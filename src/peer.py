from __future__ import annotations

import os, platform, subprocess

from pathlib import Path
from typing import Any


def _linux_process_info(pid: int) -> dict[str, Any]:
    info: dict[str, Any] = {"pid": pid}
    proc = Path("/proc") / str(pid)

    try:
        info["path"] = os.readlink(proc / "exe")
    except OSError:
        pass

    try:
        info["name"] = (proc / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        if "path" in info:
            info["name"] = Path(info["path"]).name

    return info


def _ps_process_info(pid: int) -> dict[str, Any]:
    info: dict[str, Any] = {"pid": pid}
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        command = result.stdout.strip()
        if command:
            info["path"] = command
            info["name"] = Path(command).name
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def _windows_process_info(pid: int) -> dict[str, Any]:
    info: dict[str, Any] = {"pid": pid}
    ps_script = (
        f'$p = Get-CimInstance Win32_Process -Filter "ProcessId = {pid}"; '
        "$p | Select-Object -First 1 -ExpandProperty ExecutablePath"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        path = result.stdout.strip()
        if path:
            info["path"] = path
            info["name"] = Path(path).name
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def parent_process_info() -> dict[str, Any]:
    pid = os.getppid()
    system = platform.system().lower()
    if system == "linux":
        return _linux_process_info(pid)
    if system == "windows":
        return _windows_process_info(pid)
    return _ps_process_info(pid)
