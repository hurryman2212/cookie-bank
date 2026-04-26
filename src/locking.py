from __future__ import annotations

import errno, os, time

from pathlib import Path
from typing import BinaryIO


class InterProcessLock:
    """Small cross-platform exclusive file lock for singleton processes."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.file_obj: BinaryIO | None = None
        self.acquired = False

    def acquire(self, blocking: bool = True) -> bool:
        if self.acquired:
            return True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_obj = self.path.open("a+b")
        try:
            if os.name == "nt":
                locked = self._acquire_windows(file_obj, blocking)
            else:
                locked = self._acquire_posix(file_obj, blocking)
        except Exception:
            file_obj.close()
            raise

        if not locked:
            file_obj.close()
            return False

        self.file_obj = file_obj
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.file_obj:
            return

        try:
            if self.acquired:
                if os.name == "nt":
                    self._release_windows(self.file_obj)
                else:
                    self._release_posix(self.file_obj)
        finally:
            self.file_obj.close()
            self.file_obj = None
            self.acquired = False

    def _acquire_posix(self, file_obj: BinaryIO, blocking: bool) -> bool:
        import fcntl

        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB

        try:
            fcntl.flock(file_obj.fileno(), flags)
            return True
        except OSError as exc:
            if not blocking and exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise

    def _release_posix(self, file_obj: BinaryIO) -> None:
        import fcntl

        fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)

    def _acquire_windows(self, file_obj: BinaryIO, blocking: bool) -> bool:
        import msvcrt

        self._ensure_windows_lock_byte(file_obj)
        while True:
            file_obj.seek(0)
            try:
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError as exc:
                if not self._is_windows_lock_contention(exc):
                    raise
                if not blocking:
                    return False
                time.sleep(0.1)

    def _release_windows(self, file_obj: BinaryIO) -> None:
        import msvcrt

        file_obj.seek(0)
        msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)

    def _ensure_windows_lock_byte(self, file_obj: BinaryIO) -> None:
        file_obj.seek(0, os.SEEK_END)
        if file_obj.tell() == 0:
            file_obj.write(b"\0")
            file_obj.flush()
        file_obj.seek(0)

    def _is_windows_lock_contention(self, exc: OSError) -> bool:
        winerror = getattr(exc, "winerror", None)
        if winerror in {33, 36}:
            return True
        return exc.errno in {errno.EACCES, errno.EDEADLK}

    def __enter__(self) -> InterProcessLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def interprocess_lock(path: str) -> InterProcessLock:
    return InterProcessLock(path)
