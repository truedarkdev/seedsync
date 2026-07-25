"""Cross-process exclusion between normal runtime and destructive migration work."""

from __future__ import annotations

import json
import os
import socket
import stat
from pathlib import Path


RUNTIME_LOCK_NAME = ".seedsync.runtime.lock"


class RuntimeExclusionError(RuntimeError):
    pass


class RuntimeExclusion:
    """OS-held exclusive lease retained for object lifetime and crash release."""

    def __init__(self, config_root: Path, purpose: str) -> None:
        from .coordinator import (
            _capture_root_identity,
            _open_anchored,
            _restrict_fd_to_owner,
        )

        self.root = Path(os.path.abspath(config_root))
        self.path = self.root / RUNTIME_LOCK_NAME
        self.descriptor: int | None = None
        identity = _capture_root_identity(self.root)
        payload = json.dumps({
            "lock_version": 2,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "root_identity": list(identity),
            "purpose": purpose,
        }, sort_keys=True).encode("utf-8")
        descriptor: int | None = None
        try:
            descriptor = _open_anchored(
                self.path, self.root, os.O_CREAT | os.O_RDWR,
                owner_control=os.name == "nt",
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeExclusionError("SeedSync runtime lock has unsafe provenance")
            _restrict_fd_to_owner(descriptor)
            self._lock_descriptor(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, payload)
            os.fsync(descriptor)
        except OSError as exc:
            error = getattr(exc, "winerror", None) or exc.errno
            if os.name == "nt" and error in (32, 33, 158):
                if descriptor is not None:
                    os.close(descriptor)
                    descriptor = None
                raise RuntimeExclusionError(
                    "SeedSync configuration root is already in use"
                ) from exc
            if descriptor is not None:
                os.close(descriptor)
                descriptor = None
            raise
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            raise
        self.descriptor = descriptor

    @staticmethod
    def _lock_descriptor(descriptor: int) -> None:
        if os.name == "posix":
            import errno
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise RuntimeExclusionError(
                        "SeedSync configuration root is already in use"
                    ) from exc
                raise
            return

        import ctypes
        import msvcrt
        from ctypes import wintypes

        class Overlapped(ctypes.Structure):
            _fields_ = (
                ("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LockFileEx.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
        )
        kernel32.LockFileEx.restype = wintypes.BOOL
        overlapped = Overlapped()
        if not kernel32.LockFileEx(
            wintypes.HANDLE(msvcrt.get_osfhandle(descriptor)),
            0x00000002 | 0x00000001, 0, 1, 0, ctypes.byref(overlapped),
        ):
            error = ctypes.get_last_error()
            if error in (33, 158):
                raise RuntimeExclusionError(
                    "SeedSync configuration root is already in use"
                )
            raise OSError(error, "Unable to acquire SeedSync runtime exclusion")

    def release(self) -> None:
        if self.descriptor is None:
            return
        os.close(self.descriptor)
        self.descriptor = None

    def __enter__(self) -> "RuntimeExclusion":
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
