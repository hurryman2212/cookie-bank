from __future__ import annotations

import argparse, json, os, sys, uuid

from pathlib import Path
from typing import Any

from ipc import (
    connect_client_socket,
    connect_connection,
    recv_connection_json,
    recv_socket_json,
    send_connection_json,
    send_socket_json,
)
from paths import adapter_address, client_address, remove_stale_socket


def _load_json(path: str | None) -> Any:
    if not path or path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _normalize_identifier(identifier: str | None) -> str | None:
    if identifier is None:
        return None
    try:
        return str(uuid.UUID(identifier))
    except ValueError as exc:
        raise SystemExit("--identifier must be a UUID string.") from exc


def _target_from_args(args: argparse.Namespace) -> str | None:
    values = [
        value
        for value in (args.target, args.target_browser, args.target_path)
        if value is not None
    ]
    if len(values) > 1:
        raise SystemExit(
            "Use only one of --target, --target-browser, or --target-path."
        )
    return values[0] if values else None


def _request_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.list:
        return {"type": "list_targets"}

    payload = _load_json(args.json_file)
    if isinstance(payload, list):
        payload = {"cookies": payload}
    if not isinstance(payload, dict):
        raise SystemExit("Input JSON must be an object or a cookie array.")

    request = dict(payload)
    target = _target_from_args(args)
    if target is not None:
        request["target"] = target
    if args.identifier:
        request["identifier"] = _normalize_identifier(args.identifier)
    if args.timeout is not None:
        request["timeout"] = args.timeout
    return request


def send_request(request: dict[str, Any], timeout: float) -> dict[str, Any]:
    address = client_address()
    if os.name == "nt":
        conn = connect_connection(address, timeout=timeout)
        try:
            send_connection_json(conn, request)
            return recv_connection_json(conn)
        finally:
            conn.close()

    sock = connect_client_socket(address, timeout=timeout)
    with sock:
        file_obj = sock.makefile("rb")
        send_socket_json(sock, request)
        return recv_socket_json(file_obj)


def broker_unavailable_response(exc: Exception) -> dict[str, Any]:
    address = client_address()
    removed_stale_sockets: list[str] = []
    if os.name != "nt" and isinstance(exc, (FileNotFoundError, ConnectionRefusedError)):
        for stale_address in (address, adapter_address()):
            if remove_stale_socket(stale_address):
                removed_stale_sockets.append(stale_address)

    message = (
        f"Unable to connect to broker at {address}: {exc}. "
        "No broker process is listening. Open or reload the browser extension "
        "with the power button on so the native adapter can start the broker."
    )
    if removed_stale_sockets:
        message += " Removed stale broker socket files."

    return {
        "ok": False,
        "error": {
            "code": "broker_unavailable",
            "message": message,
            "address": address,
            "stale_sockets_removed": removed_stale_sockets,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send Cookie Bank cookie updates to a connected browser."
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        help="JSON file containing a cookie array or cookie update object. Use - for stdin.",
    )
    parser.add_argument("--list", action="store_true", help="List connected targets.")
    parser.add_argument("--identifier", help="Target extension identifier UUID.")
    parser.add_argument(
        "--target",
        help="Target browser executable name or full executable path.",
    )
    parser.add_argument(
        "--target-browser",
        help="Alias for --target when passing a browser executable name.",
    )
    parser.add_argument(
        "--target-path",
        help="Alias for --target when passing a full executable path.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Browser apply timeout in seconds for cookie updates.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=1.0,
        help="Broker connection timeout in seconds.",
    )
    args = parser.parse_args(argv)

    if not args.list and not args.json_file:
        parser.error("json_file is required unless --list is used.")

    request = _request_from_args(args)
    try:
        response = send_request(request, timeout=args.connect_timeout)
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        response = broker_unavailable_response(exc)

    json.dump(response, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if response.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
