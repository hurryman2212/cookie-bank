from __future__ import annotations

import time, uuid

from typing import Any


def request_id(prefix: str = "req") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def utc_timestamp() -> float:
    return time.time()


def ok_response(**values: Any) -> dict[str, Any]:
    return {"ok": True, **values}


def error_response(code: str, message: str, **values: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}, **values}
