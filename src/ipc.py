from __future__ import annotations

import errno, json, os, socket, time

from multiprocessing.connection import Client
from typing import Any


ENCODING = "utf-8"
MAX_JSON_BYTES = 16 * 1024 * 1024


class ProtocolError(RuntimeError):
    pass


def connection_family() -> str:
    return "AF_PIPE" if os.name == "nt" else "AF_UNIX"


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def send_connection_json(conn: Any, payload: dict[str, Any]) -> None:
    data = json_dumps(payload).encode(ENCODING)
    if len(data) > MAX_JSON_BYTES:
        raise ProtocolError(f"JSON frame too large: {len(data)} bytes")
    conn.send_bytes(data)


def recv_connection_json(conn: Any) -> dict[str, Any]:
    data = conn.recv_bytes(MAX_JSON_BYTES)
    payload = json.loads(data.decode(ENCODING))
    if not isinstance(payload, dict):
        raise ProtocolError("Expected a JSON object")
    return payload


def connect_connection(
    address: str, timeout: float = 5.0, interval: float = 0.1
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            return Client(address, family=connection_family(), authkey=None)
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            last_error = exc
            time.sleep(interval)

    if last_error:
        raise last_error
    raise TimeoutError(f"Timed out connecting to {address}")


def send_socket_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json_dumps(payload).encode(ENCODING)
    if len(data) > MAX_JSON_BYTES:
        raise ProtocolError(f"JSON frame too large: {len(data)} bytes")
    sock.sendall(data + b"\n")


def recv_socket_json(file_obj: Any) -> dict[str, Any]:
    line = file_obj.readline(MAX_JSON_BYTES + 2)
    if not line:
        raise EOFError("Socket closed")
    if len(line) > MAX_JSON_BYTES:
        raise ProtocolError("JSON frame too large")
    payload = json.loads(line.decode(ENCODING))
    if not isinstance(payload, dict):
        raise ProtocolError("Expected a JSON object")
    return payload


def connect_client_socket(
    address: str, timeout: float = 5.0, interval: float = 0.1
) -> socket.socket:
    if os.name == "nt":
        raise RuntimeError("Raw client sockets are only used on POSIX")

    if not os.path.exists(address):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), address)

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(min(interval, max(0.0, deadline - time.monotonic())))
        try:
            sock.connect(address)
            sock.settimeout(None)
            return sock
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            last_error = exc
            sock.close()
            time.sleep(interval)

    if last_error:
        raise last_error
    raise TimeoutError(f"Timed out connecting to {address}")
