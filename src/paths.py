from __future__ import annotations

import getpass, os, re, stat, tempfile

from pathlib import Path

from constants import APP_ID, NATIVE_HOST_NAME


def _safe_user() -> str:
    user = getpass.getuser() or "user"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", user)


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(stat.S_IRWXU)
    return path


def runtime_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        return _private_dir(base / APP_ID / "runtime")

    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return _private_dir(Path(xdg_runtime) / APP_ID)

    return _private_dir(Path(tempfile.gettempdir()) / f"{APP_ID}-{os.getuid()}")


def state_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        return _private_dir(base / APP_ID)

    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return _private_dir(base / APP_ID)


def log_dir() -> Path:
    return _private_dir(state_dir() / "logs")


def lock_file() -> Path:
    return state_dir() / "broker.lock"


def broker_log_file() -> Path:
    return log_dir() / "broker.log"


def adapter_log_file() -> Path:
    return log_dir() / "adapter.log"


def adapter_address() -> str:
    if os.name == "nt":
        return rf"\\.\pipe\{NATIVE_HOST_NAME}.{_safe_user()}.adapter"
    return str(runtime_dir() / "adapter.sock")


def client_address() -> str:
    if os.name == "nt":
        return rf"\\.\pipe\{NATIVE_HOST_NAME}.{_safe_user()}.client"
    return str(runtime_dir() / "client.sock")


def remove_stale_socket(path: str) -> bool:
    if os.name == "nt":
        return False
    socket_path = Path(path)
    try:
        if socket_path.exists() or socket_path.is_socket():
            socket_path.unlink()
            return True
    except FileNotFoundError:
        return False
    return False


def restrict_socket(path: str) -> None:
    if os.name != "nt":
        Path(path).chmod(stat.S_IRUSR | stat.S_IWUSR)
