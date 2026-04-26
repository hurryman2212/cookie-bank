from __future__ import annotations

import json, struct, sys

from typing import Any, BinaryIO


MAX_NATIVE_MESSAGE_BYTES = 64 * 1024 * 1024


class NativeMessagingError(RuntimeError):
    pass


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            if not data:
                raise EOFError
            raise NativeMessagingError("Unexpected EOF while reading native message")
        data.extend(chunk)
    return bytes(data)


def read_message(stream: BinaryIO | None = None) -> dict[str, Any]:
    stream = stream or sys.stdin.buffer
    header = _read_exact(stream, 4)
    length = struct.unpack("<I", header)[0]

    if length > MAX_NATIVE_MESSAGE_BYTES:
        raise NativeMessagingError(f"Native message too large: {length} bytes")

    body = _read_exact(stream, length)
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise NativeMessagingError("Expected a JSON object")
    return payload


def write_message(payload: dict[str, Any], stream: BinaryIO | None = None) -> None:
    stream = stream or sys.stdout.buffer
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_NATIVE_MESSAGE_BYTES:
        raise NativeMessagingError(f"Native message too large: {len(encoded)} bytes")
    stream.write(struct.pack("<I", len(encoded)))
    stream.write(encoded)
    stream.flush()
