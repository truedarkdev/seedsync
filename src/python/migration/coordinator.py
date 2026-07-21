"""Consent-gated, selected-major configuration migration coordination."""

from __future__ import annotations

import configparser
import errno
import hashlib
import json
import os
import socket
import stat
import threading
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Sequence, cast

from common import Config, PathPair, PathPairManager
from controller import AutoQueuePersist, ControllerPersist


CURRENT_SCHEMA_ID = "seedsync-current-v1"
METADATA_FILE = "migration-state.json"
LOCK_FILE = ".migration.lock"
BACKUP_ROOT = "migration-backups"
_MAX_JSON_BYTES = 256 * 1024
_MAX_RELEVANT_FILE_BYTES = 16 * 1024 * 1024
_MAX_BACKUP_BYTES = 64 * 1024 * 1024
_MAX_BACKUP_FILES = 16
_root_transaction_state = threading.local()
_windows_api_cache = None


def _windows_api():
    global _windows_api_cache
    if _windows_api_cache is not None:
        return _windows_api_cache
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
    )
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.LPVOID,)
    kernel32.LocalFree.restype = wintypes.LPVOID
    kernel32.GetCurrentProcess.argtypes = ()
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = (wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR))
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = (
        wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    )
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorControl.argtypes = (
        wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = (
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.c_int,
    )
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = (wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID))
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.SetSecurityInfo.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID,
        wintypes.LPVOID, wintypes.LPVOID,
    )
    advapi32.SetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetSecurityInfo.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
    )
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    ntdll.NtCreateFile.argtypes = (
        ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, wintypes.LPVOID, wintypes.LPVOID,
        wintypes.LPVOID, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
        wintypes.LPVOID, wintypes.ULONG,
    )
    ntdll.NtCreateFile.restype = wintypes.LONG
    ntdll.NtSetInformationFile.argtypes = (
        wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID, wintypes.ULONG, ctypes.c_int,
    )
    ntdll.NtSetInformationFile.restype = wintypes.LONG
    ntdll.RtlNtStatusToDosError.argtypes = (wintypes.LONG,)
    ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
    _windows_api_cache = (kernel32, advapi32, ntdll)
    return _windows_api_cache


def _handle_identity(descriptor_or_handle: int, *, windows_handle: bool = False) -> tuple[int, ...]:
    if os.name == "posix":
        info = os.fstat(descriptor_or_handle)
        return (info.st_dev, info.st_ino)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("FileAttributes", wintypes.DWORD), ("CreationTimeLow", wintypes.DWORD),
            ("CreationTimeHigh", wintypes.DWORD), ("LastAccessTimeLow", wintypes.DWORD),
            ("LastAccessTimeHigh", wintypes.DWORD), ("LastWriteTimeLow", wintypes.DWORD),
            ("LastWriteTimeHigh", wintypes.DWORD), ("VolumeSerialNumber", wintypes.DWORD),
            ("FileSizeHigh", wintypes.DWORD), ("FileSizeLow", wintypes.DWORD),
            ("NumberOfLinks", wintypes.DWORD), ("FileIndexHigh", wintypes.DWORD),
            ("FileIndexLow", wintypes.DWORD),
        )

    handle = descriptor_or_handle if windows_handle else msvcrt.get_osfhandle(descriptor_or_handle)
    information = ByHandleFileInformation()
    kernel32, _, _ = _windows_api()
    if not kernel32.GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(information)):
        raise OSError(ctypes.get_last_error(), "Unable to identify migration root handle")
    return (information.VolumeSerialNumber, information.FileIndexHigh, information.FileIndexLow)


def _capture_root_identity(root: Path) -> tuple[int, ...]:
    if os.name == "posix":
        descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            return _handle_identity(descriptor)
        finally:
            os.close(descriptor)
    import ctypes
    from ctypes import wintypes

    kernel32, _, _ = _windows_api()
    handle = kernel32.CreateFileW(str(Path(os.path.abspath(root))), 0x80, 0x7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Unable to anchor migration configuration root")
    try:
        return _handle_identity(handle, windows_handle=True)
    finally:
        kernel32.CloseHandle(handle)


def _verify_transaction_root(root: Path, identity: tuple[int, ...]) -> None:
    expected = getattr(_root_transaction_state, "value", None)
    if expected is not None and expected[0] == Path(os.path.abspath(root)) and identity != expected[1]:
        raise ValueError("Migration configuration root identity changed during the transaction")


@contextmanager
def _root_transaction(root: Path, required_identity: tuple[int, ...] | None = None):
    root_path = Path(os.path.abspath(root))
    previous = getattr(_root_transaction_state, "value", None)
    if previous is not None:
        if previous[0] != root_path:
            raise ValueError("Nested migration transaction changed configuration roots")
        yield
        return
    identity = _capture_root_identity(root_path)
    if required_identity is not None and identity != required_identity:
        raise ValueError("Migration configuration root identity changed before the transaction")
    _root_transaction_state.value = (root_path, identity)
    try:
        yield
    finally:
        del _root_transaction_state.value


class MigrationState(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETE = "complete"


class MigrationBlockedError(RuntimeError):
    """Raised when normal startup is unsafe until migration is resolved."""

    def __init__(self, decision: "MigrationDecision") -> None:
        self.decision = decision
        super().__init__(decision.error or "Configuration migration is {}".format(decision.state.value))


@dataclass(frozen=True)
class MigrationFeature:
    title: str
    summary: str


@dataclass(frozen=True)
class MigrationDecision:
    state: MigrationState
    migration_id: str | None = None
    source_schema: str | None = None
    target_schema: str = CURRENT_SCHEMA_ID
    features: tuple[MigrationFeature, ...] = ()
    error: str | None = None
    retryable: bool = False

    @property
    def allows_normal_startup(self) -> bool:
        return self.state in (MigrationState.NOT_REQUIRED, MigrationState.COMPLETE)


Fingerprint = Callable[[Path], bool]
Apply = Callable[[Path, Path], None]
Validate = Callable[[Path], None]


@dataclass(frozen=True)
class MigrationSpec:
    migration_id: str
    order: int
    source_schema: str
    target_schema: str
    features: tuple[MigrationFeature, ...]
    fingerprint: Fingerprint
    apply: Apply
    validate: Validate


_LEGACY_SETTINGS_KEYS: Mapping[str, frozenset[str]] = {
    "General": frozenset(("debug", "verbose")),
    "Lftp": frozenset((
        "remote_address", "remote_username", "remote_password", "remote_port",
        "remote_path", "local_path", "remote_path_to_scan_script", "use_ssh_key",
        "num_max_parallel_downloads", "num_max_parallel_files_per_download",
        "num_max_connections_per_root_file", "num_max_connections_per_dir_file",
        "num_max_total_connections", "use_temp_file",
    )),
    "Controller": frozenset((
        "interval_ms_remote_scan", "interval_ms_local_scan", "interval_ms_downloading_scan",
        "extract_path", "use_local_path_as_extract_path",
    )),
    "Web": frozenset(("port",)),
    "AutoQueue": frozenset(("enabled", "patterns_only", "auto_extract")),
}
_BACKUP_FILES = (
    "settings.cfg", "controller.persist", "autoqueue.persist", "path_pairs.json",
    "api-keys.json", "api-keys.history.jsonl",
)
_BACKUP_FILE_SET = frozenset(_BACKUP_FILES)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_under(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Migration path escapes its configured root") from exc
    return resolved


def _regular_file(path: Path, root: Path, max_bytes: int = _MAX_RELEVANT_FILE_BYTES) -> Path:
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("Migration input must be a regular file")
    if info.st_size > max_bytes:
        raise ValueError("Migration input exceeds the size limit")
    return _ensure_under(path, root)


def _open_anchored(
    path: Path, root: Path, flags: int, *, owner_control: bool = False, delete_control: bool = False,
) -> int:
    """Open a root-contained path without following its final link."""
    root_path = Path(os.path.abspath(root))
    path_path = Path(os.path.abspath(path))
    try:
        relative = path_path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("Migration path escapes its configured root") from exc
    if not relative.parts:
        raise ValueError("Migration input must be a file")

    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
        directory_fd = os.open(root_path, directory_flags)
        _verify_transaction_root(root_path, _handle_identity(directory_fd))
        try:
            for component in relative.parts[:-1]:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            return os.open(relative.parts[-1], flags | nofollow, 0o600, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32, _, ntdll = _windows_api()
    file_attribute_tag_info = 9
    file_attribute_reparse_point = 0x400
    file_attribute_directory = 0x10
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    invalid_handle_value = ctypes.c_void_p(-1).value

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = (("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD))

    # Keep every traversed directory open without delete sharing. This blocks
    # rename/reparse replacement until the final file handle is acquired.
    directory_handles: list[int] = []
    current_directory = root_path
    try:
        for component in (None,) + relative.parts[:-1]:
            if component is not None:
                current_directory /= component
            directory_handle = kernel32.CreateFileW(
                str(current_directory), 0x80, 0x3, None, 3,
                file_flag_open_reparse_point | file_flag_backup_semantics, None,
            ) if component is None else _windows_open_directory(directory_handles[-1], component)
            if directory_handle == invalid_handle_value:
                raise OSError(ctypes.get_last_error(), "Unable to anchor migration directory", str(current_directory))
            tag_info = FileAttributeTagInfo()
            if not kernel32.GetFileInformationByHandleEx(
                directory_handle, file_attribute_tag_info, ctypes.byref(tag_info), ctypes.sizeof(tag_info),
            ):
                kernel32.CloseHandle(directory_handle)
                raise OSError(ctypes.get_last_error(), "Unable to inspect migration directory handle")
            if tag_info.FileAttributes & file_attribute_reparse_point or not (
                tag_info.FileAttributes & file_attribute_directory
            ):
                kernel32.CloseHandle(directory_handle)
                raise ValueError("Migration directory must not traverse a reparse point")
            directory_handles.append(directory_handle)
            if component is None:
                _verify_transaction_root(root_path, _handle_identity(directory_handle, windows_handle=True))

        desired_access = 0x40000000 if flags & os.O_WRONLY else 0x80000000
        if owner_control:
            desired_access |= 0x00060000  # READ_CONTROL | WRITE_DAC
        if delete_control:
            desired_access |= 0x00010000  # DELETE
        desired_access |= 0x00100080  # SYNCHRONIZE | FILE_READ_ATTRIBUTES

        class UnicodeString(ctypes.Structure):
            _fields_ = (
                ("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR),
            )

        class ObjectAttributes(ctypes.Structure):
            _fields_ = (
                ("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
                ("SecurityDescriptor", wintypes.LPVOID), ("SecurityQualityOfService", wintypes.LPVOID),
            )

        class IoStatusBlock(ctypes.Structure):
            _fields_ = (("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t))

        leaf_name = relative.parts[-1]
        leaf_buffer = ctypes.create_unicode_buffer(leaf_name)
        leaf_unicode = UnicodeString(
            len(leaf_name.encode("utf-16-le")), len(leaf_name.encode("utf-16-le")),
            ctypes.cast(leaf_buffer, wintypes.LPWSTR),
        )
        security_context = _windows_private_security() if owner_control and flags & os.O_CREAT else nullcontext((None, ()))
        with security_context as (security_descriptor, creation_trustees):
            attributes = ObjectAttributes(
                ctypes.sizeof(ObjectAttributes), directory_handles[-1], ctypes.pointer(leaf_unicode), 0x40,
                security_descriptor, None,
            )
            handle = wintypes.HANDLE()
            io_status = IoStatusBlock()
            status = ntdll.NtCreateFile(
                ctypes.byref(handle), desired_access, ctypes.byref(attributes), ctypes.byref(io_status),
                None, 0, 0x1, 2 if flags & os.O_EXCL else 1, 0x00200060, None, 0,
            )
            if status >= 0 and creation_trustees:
                try:
                    _verify_windows_handle_owner_only(handle.value, creation_trustees)
                except Exception:
                    kernel32.CloseHandle(wintypes.HANDLE(handle.value))
                    handle = wintypes.HANDLE()
                    raise
        if status < 0:
            error = ntdll.RtlNtStatusToDosError(status)
            if error in (80, 183):
                raise FileExistsError(error, "Migration file already exists", str(path_path))
            raise OSError(error, "Unable to open anchored migration file", str(path_path))
    finally:
        for directory_handle in reversed(directory_handles):
            kernel32.CloseHandle(directory_handle)
    try:
        descriptor = msvcrt.open_osfhandle(handle.value, getattr(os, "O_BINARY", 0) | flags)
    except Exception:
        kernel32.CloseHandle(wintypes.HANDLE(handle.value))
        raise
    try:
        handle = msvcrt.get_osfhandle(descriptor)
        tag_info = FileAttributeTagInfo()
        if not kernel32.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle), file_attribute_tag_info, ctypes.byref(tag_info), ctypes.sizeof(tag_info),
        ):
            raise OSError(ctypes.get_last_error(), "Unable to inspect migration file handle")
        if tag_info.FileAttributes & file_attribute_reparse_point:
            raise ValueError("Migration input must not be a reparse point")

        required = kernel32.GetFinalPathNameByHandleW(wintypes.HANDLE(handle), None, 0, 0)
        if not required:
            raise OSError(ctypes.get_last_error(), "Unable to resolve migration file handle")
        buffer = ctypes.create_unicode_buffer(required + 1)
        if not kernel32.GetFinalPathNameByHandleW(wintypes.HANDLE(handle), buffer, len(buffer), 0):
            raise OSError(ctypes.get_last_error(), "Unable to resolve migration file handle")
        final_path = buffer.value
        if final_path.startswith("\\\\?\\"):
            final_path = final_path[4:]
        resolved_root = str(root_path.resolve(strict=True))
        if os.path.commonpath((resolved_root, final_path)) != resolved_root:
            raise ValueError("Migration path escapes its configured root")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _mutation_parent(path: Path, root: Path):
    """Retain a trusted parent anchor for the complete pathname mutation."""
    root_path = Path(os.path.abspath(root))
    path_path = Path(os.path.abspath(path))
    try:
        relative = path_path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("Migration path escapes its configured root") from exc
    if not relative.parts:
        raise ValueError("Migration cannot replace its configured root")

    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None or not getattr(os, "supports_dir_fd", set()).issuperset({os.open, os.mkdir, os.unlink}):
            raise RuntimeError("Secure migration dir_fd primitives are unavailable")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
        directory_fd = os.open(root_path, directory_flags)
        _verify_transaction_root(root_path, _handle_identity(directory_fd))
        try:
            for component in relative.parts[:-1]:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            yield directory_fd, relative.parts[-1]
        finally:
            os.close(directory_fd)
        return

    import ctypes
    from ctypes import wintypes

    kernel32, _, _ = _windows_api()
    invalid_handle_value = ctypes.c_void_p(-1).value
    reparse_attribute = 0x400
    directory_attribute = 0x10
    open_reparse = 0x00200000
    backup_semantics = 0x02000000
    file_attribute_tag_info = 9

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = (("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD))

    handles: list[int] = []
    current = root_path
    try:
        for component in (None,) + relative.parts[:-1]:
            if component is not None:
                current /= component
            handle = kernel32.CreateFileW(
                str(current), 0x80, 0x3, None, 3, open_reparse | backup_semantics, None,
            ) if component is None else _windows_open_directory(handles[-1], component)
            if handle == invalid_handle_value:
                raise OSError(ctypes.get_last_error(), "Unable to anchor migration mutation", str(current))
            info = FileAttributeTagInfo()
            if not kernel32.GetFileInformationByHandleEx(
                handle, file_attribute_tag_info, ctypes.byref(info), ctypes.sizeof(info),
            ):
                kernel32.CloseHandle(handle)
                raise OSError(ctypes.get_last_error(), "Unable to inspect migration mutation anchor")
            if info.FileAttributes & reparse_attribute or not info.FileAttributes & directory_attribute:
                kernel32.CloseHandle(handle)
                raise ValueError("Migration mutation must not traverse a reparse point")
            handles.append(handle)
            if component is None:
                _verify_transaction_root(root_path, _handle_identity(handle, windows_handle=True))
        yield handles[-1], path_path
    finally:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)


def _secure_unlink(path: Path, root: Path) -> None:
    if os.name == "posix":
        with _mutation_parent(path, root) as (directory_fd, name):
            os.unlink(name, dir_fd=directory_fd)
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    descriptor = _open_anchored(path, root, os.O_RDONLY, delete_control=True)
    try:
        class FileDispositionInfo(ctypes.Structure):
            _fields_ = (("DeleteFile", wintypes.BOOL),)

        info = FileDispositionInfo(True)
        kernel32, _, _ = _windows_api()
        if not kernel32.SetFileInformationByHandle(
            wintypes.HANDLE(msvcrt.get_osfhandle(descriptor)), 4, ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise OSError(ctypes.get_last_error(), "Unable to remove anchored migration file")
    finally:
        os.close(descriptor)


def _windows_rename_fd(descriptor: int, directory_handle: int, target_name: str) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileRenameInformation(ctypes.Structure):
        _fields_ = (
            ("ReplaceIfExists", ctypes.c_ubyte),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * len(target_name)),
        )

    class IoStatusBlock(ctypes.Structure):
        _fields_ = (("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t))

    info = FileRenameInformation()
    info.ReplaceIfExists = True
    info.RootDirectory = directory_handle
    info.FileNameLength = len(target_name.encode("utf-16-le"))
    info.FileName = target_name
    io_status = IoStatusBlock()
    _, _, ntdll = _windows_api()
    status = ntdll.NtSetInformationFile(
        wintypes.HANDLE(msvcrt.get_osfhandle(descriptor)), ctypes.byref(io_status),
        ctypes.byref(info), ctypes.sizeof(info), 10,
    )
    if status < 0:
        error = ntdll.RtlNtStatusToDosError(status)
        raise OSError(error, "Unable to commit anchored migration file")


def _windows_open_directory(
    directory_handle: int, name: str, *, create: bool = False, owner_control: bool = False,
) -> int:
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = (("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR))

    class ObjectAttributes(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID), ("SecurityQualityOfService", wintypes.LPVOID),
        )

    class IoStatusBlock(ctypes.Structure):
        _fields_ = (("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t))

    name_buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(
        len(name.encode("utf-16-le")), len(name.encode("utf-16-le")),
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    _, _, ntdll = _windows_api()
    security_context = _windows_private_security() if owner_control and create else nullcontext((None, ()))
    with security_context as (security_descriptor, creation_trustees):
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes), directory_handle, ctypes.pointer(unicode_name), 0x40,
            security_descriptor, None,
        )
        created_handle = wintypes.HANDLE()
        io_status = IoStatusBlock()
        status = ntdll.NtCreateFile(
            ctypes.byref(created_handle), 0x00100080 | (0x00060000 if owner_control else 0),
            ctypes.byref(attributes), ctypes.byref(io_status),
            None, 0, 0x3, 2 if create else 1, 0x00200021, None, 0,
        )
        if status >= 0 and creation_trustees:
            try:
                _verify_windows_handle_owner_only(created_handle.value, creation_trustees)
            except Exception:
                _windows_api()[0].CloseHandle(wintypes.HANDLE(created_handle.value))
                created_handle = wintypes.HANDLE()
                raise
    if status < 0:
        error = ntdll.RtlNtStatusToDosError(status)
        if error in (80, 183):
            raise FileExistsError(error, "Migration directory already exists", name)
        raise OSError(error, "Unable to create anchored migration directory", name)
    return created_handle.value


@contextmanager
def _windows_private_security():
    import ctypes
    from ctypes import wintypes

    security_descriptor = wintypes.LPVOID()
    kernel32, advapi32, _ = _windows_api()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise PermissionError(ctypes.get_last_error(), "Unable to read migration process identity")
    owner_sid_text = wintypes.LPWSTR()
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        token_information = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token, 1, token_information, len(token_information), ctypes.byref(required),
        ):
            raise PermissionError(ctypes.get_last_error(), "Unable to read migration process identity")
        token_sid = ctypes.cast(token_information, ctypes.POINTER(wintypes.LPVOID)).contents
        if not advapi32.ConvertSidToStringSidW(token_sid, ctypes.byref(owner_sid_text)):
            raise PermissionError(ctypes.get_last_error(), "Unable to identify migration backup owner")
        trustees = [owner_sid_text.value]
        app_required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 31, None, 0, ctypes.byref(app_required))
        if app_required.value:
            app_information = ctypes.create_string_buffer(app_required.value)
            if not advapi32.GetTokenInformation(
                token, 31, app_information, len(app_information), ctypes.byref(app_required),
            ):
                raise PermissionError(ctypes.get_last_error(), "Unable to read migration application identity")
            app_sid = ctypes.cast(app_information, ctypes.POINTER(wintypes.LPVOID)).contents
            if app_sid:
                app_sid_text = wintypes.LPWSTR()
                try:
                    if not advapi32.ConvertSidToStringSidW(app_sid, ctypes.byref(app_sid_text)):
                        raise PermissionError(ctypes.get_last_error(), "Unable to identify migration application")
                    trustees.append(app_sid_text.value)
                finally:
                    if app_sid_text:
                        kernel32.LocalFree(app_sid_text)
        owner_only_sddl = "D:P{}".format("".join("(A;;FA;;;{})".format(sid) for sid in trustees))
    finally:
        if owner_sid_text:
            kernel32.LocalFree(owner_sid_text)
        kernel32.CloseHandle(token)
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        owner_only_sddl, 1, ctypes.byref(security_descriptor), None,
    ):
        raise PermissionError(ctypes.get_last_error(), "Unable to build owner-only backup permissions")
    try:
        yield security_descriptor, tuple(trustees)
    finally:
        kernel32.LocalFree(security_descriptor)


def _verify_windows_handle_owner_only(handle: int, trustees: Sequence[str] | None = None) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32, advapi32, _ = _windows_api()
    if trustees is None:
        with _windows_private_security() as (_, expected_trustees):
            trustees = expected_trustees
    verified_descriptor = wintypes.LPVOID()
    verified_dacl = wintypes.LPVOID()
    result = advapi32.GetSecurityInfo(
        wintypes.HANDLE(handle), 1, 0x00000004, None, None,
        ctypes.byref(verified_dacl), None, ctypes.byref(verified_descriptor),
    )
    if result:
        raise PermissionError(result, "Unable to verify owner-only backup permissions")
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            verified_descriptor, ctypes.byref(control), ctypes.byref(revision),
        ):
            raise PermissionError(ctypes.get_last_error(), "Unable to verify owner-only backup permissions")
        se_dacl_present = 0x0004
        se_dacl_protected = 0x1000
        if control.value & (se_dacl_present | se_dacl_protected) != (
            se_dacl_present | se_dacl_protected
        ):
            raise PermissionError("Migration backup permissions are not owner-only")

        class AclSizeInformation(ctypes.Structure):
            _fields_ = (
                ("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                ("AclBytesFree", wintypes.DWORD),
            )

        class AceHeader(ctypes.Structure):
            _fields_ = (("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", wintypes.WORD))

        class AccessAllowedAce(ctypes.Structure):
            _fields_ = (("Header", AceHeader), ("Mask", wintypes.DWORD), ("SidStart", wintypes.DWORD))

        acl_information = AclSizeInformation()
        if not advapi32.GetAclInformation(
            verified_dacl, ctypes.byref(acl_information), ctypes.sizeof(acl_information), 2,
        ):
            raise PermissionError(ctypes.get_last_error(), "Unable to verify owner-only backup permissions")
        if acl_information.AceCount != len(trustees) or len(set(trustees)) != len(trustees):
            raise PermissionError("Migration backup permissions are not owner-only")

        parsed_trustees: list[str] = []
        file_all_access = 0x001F01FF
        for index in range(acl_information.AceCount):
            ace_pointer = wintypes.LPVOID()
            if not advapi32.GetAce(verified_dacl, index, ctypes.byref(ace_pointer)):
                raise PermissionError(ctypes.get_last_error(), "Unable to verify owner-only backup permissions")
            header = ctypes.cast(ace_pointer, ctypes.POINTER(AceHeader)).contents
            if (
                header.AceType != 0
                or header.AceFlags != 0
                or header.AceSize < ctypes.sizeof(AccessAllowedAce)
            ):
                raise PermissionError("Migration backup permissions are not owner-only")
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(AccessAllowedAce)).contents
            if ace.Mask != file_all_access:
                raise PermissionError("Migration backup permissions are not owner-only")
            sid_pointer = wintypes.LPVOID(ace_pointer.value + AccessAllowedAce.SidStart.offset)
            sid_text = wintypes.LPWSTR()
            try:
                if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_text)):
                    raise PermissionError(ctypes.get_last_error(), "Unable to verify owner-only backup permissions")
                parsed_trustees.append(sid_text.value)
            finally:
                if sid_text:
                    kernel32.LocalFree(sid_text)
        if len(set(parsed_trustees)) != len(parsed_trustees) or set(parsed_trustees) != set(trustees):
            raise PermissionError("Migration backup permissions are not owner-only")
    finally:
        kernel32.LocalFree(verified_descriptor)


def _restrict_windows_handle_to_owner(handle: int) -> None:
    """Compatibility name: verification-only; existing insecure objects fail closed."""
    _verify_windows_handle_owner_only(handle)


def _restrict_fd_to_owner(descriptor: int) -> None:
    if os.name == "posix":
        os.fchmod(descriptor, 0o600)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
            raise PermissionError("Migration backup permissions are not owner-only")
        return
    import msvcrt

    _restrict_windows_handle_to_owner(msvcrt.get_osfhandle(descriptor))


def _safe_directory(path: Path, root: Path, *, create: bool = False, private: bool = False) -> Path:
    if not path.exists():
        if path.is_symlink():
            raise ValueError("Migration directory must not be a link or non-directory")
        if not create:
            raise ValueError("Migration directory is missing")
        parent = path.parent
        if parent != root:
            _safe_directory(parent, root, create=True, private=private)
        with _mutation_parent(path, root) as (directory_fd, name):
            try:
                if os.name == "posix":
                    os.mkdir(name, mode=0o700, dir_fd=directory_fd)
                else:
                    handle = _windows_open_directory(
                        directory_fd, path.name, create=True, owner_control=private,
                    )
                    from ctypes import wintypes
                    _windows_api()[0].CloseHandle(wintypes.HANDLE(handle))
            except FileExistsError:
                pass
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise ValueError("Migration directory must not be a link or non-directory")
    absolute_path = Path(os.path.abspath(path))
    try:
        absolute_path.relative_to(Path(os.path.abspath(root)))
    except ValueError as exc:
        raise ValueError("Migration path escapes its configured root") from exc
    return absolute_path


def _make_private_directory(path: Path, root: Path) -> None:
    with _mutation_parent(path, root) as (directory_fd, name):
        if os.name == "posix":
            descriptor = os.open(
                name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            try:
                os.fchmod(descriptor, 0o700)
                if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
                    raise PermissionError("Migration backup directory permissions are not owner-only")
            finally:
                os.close(descriptor)
            return
        handle = _windows_open_directory(directory_fd, path.name, owner_control=True)
        try:
            _restrict_windows_handle_to_owner(handle)
        finally:
            from ctypes import wintypes
            _windows_api()[0].CloseHandle(wintypes.HANDLE(handle))


def _read_text(path: Path, root: Path, max_bytes: int = _MAX_RELEVANT_FILE_BYTES) -> str:
    return _read_bytes(path, root, max_bytes).decode("utf-8")


def _read_bytes(
    path: Path,
    root: Path,
    max_bytes: int = _MAX_RELEVANT_FILE_BYTES,
    *,
    owner_only: bool = False,
) -> bytes:
    descriptor = _open_anchored(path, root, os.O_RDONLY, owner_control=owner_only)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Migration input must be a regular file")
        if info.st_size > max_bytes:
            raise ValueError("Migration input exceeds the size limit")
        if owner_only:
            _restrict_fd_to_owner(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError("Migration input exceeds the size limit")
        return payload
    finally:
        os.close(descriptor)


def _write_private_backup(path: Path, payload: bytes, root: Path) -> None:
    descriptor = _open_anchored(path, root, os.O_CREAT | os.O_EXCL | os.O_WRONLY, owner_control=True)
    try:
        _restrict_fd_to_owner(descriptor)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Unable to write migration backup")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if _read_bytes(path, root, owner_only=True) != payload:
        raise ValueError("Completed migration backup failed validation")


def _atomic_write(path: Path, content: str, root: Path) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_RELEVANT_FILE_BYTES:
        raise ValueError("Migration output exceeds the size limit")
    _safe_directory(path.parent, root, create=True)
    temp_name = ".{}-{}.tmp".format(path.name, uuid.uuid4().hex)
    with _mutation_parent(path, root) as (directory_fd, target_name):
        temp_path = path.parent / temp_name
        descriptor: int | None = None
        committed = False
        try:
            if os.name == "posix":
                descriptor = os.open(
                    temp_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600, dir_fd=directory_fd,
                )
            else:
                descriptor = _open_anchored(
                    temp_path, root, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    owner_control=True, delete_control=True,
                )
            _restrict_fd_to_owner(descriptor)
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("Unable to write migration output")
                view = view[written:]
            os.fsync(descriptor)
            if os.name == "posix":
                os.replace(temp_name, target_name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                os.fsync(directory_fd)
            else:
                _windows_rename_fd(descriptor, directory_fd, path.name)
            committed = True
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not committed:
                try:
                    if os.name == "posix":
                        os.unlink(temp_name, dir_fd=directory_fd)
                    else:
                        _secure_unlink(temp_path, root)
                except FileNotFoundError:
                    pass


def _read_json_object(
    path: Path, root: Path, max_bytes: int = _MAX_JSON_BYTES, *, owner_only: bool = False,
) -> dict[str, object]:
    value = json.loads(_read_bytes(path, root, max_bytes, owner_only=owner_only).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("{} must contain a JSON object".format(path.name))
    return cast(dict[str, object], value)


def _parse_settings(path: Path, root: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(_read_text(path, root))
    return parser


def _legacy_v086_fingerprint(config_dir: Path) -> bool:
    required = tuple(config_dir / name for name in ("settings.cfg", "controller.persist", "autoqueue.persist"))
    if any(
        path.exists() or path.is_symlink()
        for path in (config_dir / name for name in ("path_pairs.json", "api-keys.json", "api-keys.history.jsonl"))
    ):
        return False
    try:
        for path in required:
            _regular_file(path, config_dir)
        settings = _parse_settings(required[0], config_dir)
        if set(settings.sections()) != set(_LEGACY_SETTINGS_KEYS):
            return False
        for section, expected in _LEGACY_SETTINGS_KEYS.items():
            actual = set(settings.options(section))
            if section == "General":
                actual.discard("webhook_secret")
            if actual != set(expected):
                return False

        controller = _read_json_object(required[1], config_dir)
        if set(controller) != {"downloaded", "extracted"}:
            return False
        if not all(
            isinstance(controller[name], list)
            and all(isinstance(item, str) for item in cast(list[object], controller[name]))
            for name in ("downloaded", "extracted")
        ):
            return False

        autoqueue = _read_json_object(required[2], config_dir)
        if set(autoqueue) != {"patterns"} or not isinstance(autoqueue["patterns"], list):
            return False
        for encoded in cast(list[object], autoqueue["patterns"]):
            if not isinstance(encoded, str):
                return False
            pattern = json.loads(encoded)
            if not isinstance(pattern, dict) or set(pattern) != {"pattern"} or not isinstance(pattern["pattern"], str):
                return False
        return True
    except (OSError, ValueError, TypeError, configparser.Error, json.JSONDecodeError):
        return False


def _looks_current(config_dir: Path) -> bool:
    settings_path = config_dir / "settings.cfg"
    try:
        parser = _parse_settings(settings_path, config_dir)
        current_sections = {"Validate", "Logging", "Notifications"}
        current_keys = (
            parser.has_option("General", "log_level"),
            parser.has_option("Lftp", "transfer_backend"),
            parser.has_option("Lftp", "remote_python_path"),
        )
        if not (current_sections.intersection(parser.sections()) or any(current_keys)):
            return False
        Config.from_str(_read_text(settings_path, config_dir))
        optional_parsers = (
            ("path_pairs.json", lambda value: PathPairManager(str(config_dir)).from_str(value)),
            ("controller.persist", ControllerPersist.from_str),
            ("autoqueue.persist", AutoQueuePersist.from_str),
        )
        for name, parser_fn in optional_parsers:
            path = config_dir / name
            if path.exists():
                parser_fn(_read_text(path, config_dir))
        return True
    except Exception:
        return False


def _validate_manifest(backup_dir: Path, config_dir: Path, spec: MigrationSpec) -> None:
    manifest = _read_json_object(backup_dir / "manifest.json", config_dir, owner_only=True)
    if (
        manifest.get("manifest_version") != 1
        or manifest.get("migration_id") != spec.migration_id
        or manifest.get("source_schema") != spec.source_schema
        or manifest.get("target_schema") != spec.target_schema
    ):
        raise ValueError("Existing migration backup manifest is invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) > _MAX_BACKUP_FILES:
        raise ValueError("Existing migration backup manifest is invalid")
    seen: set[str] = set()
    total = 0
    for entry_value in cast(list[object], files):
        if not isinstance(entry_value, dict):
            raise ValueError("Existing migration backup manifest is invalid")
        entry = cast(dict[str, object], entry_value)
        name, digest, size = entry.get("name"), entry.get("sha256"), entry.get("size")
        if name not in _BACKUP_FILE_SET or name in seen or not isinstance(digest, str) or type(size) is not int:
            raise ValueError("Existing migration backup manifest is invalid")
        seen.add(cast(str, name))
        if cast(int, size) < 0 or cast(int, size) > _MAX_RELEVANT_FILE_BYTES:
            raise ValueError("Existing migration backup manifest is invalid")
        total += cast(int, size)
        if total > _MAX_BACKUP_BYTES:
            raise ValueError("Existing migration backup exceeds the size limit")
        backup_file = backup_dir / cast(str, name)
        payload = _read_bytes(backup_file, config_dir, owner_only=True)
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Existing migration backup failed digest validation")


def _backup_metadata(config_dir: Path, spec: MigrationSpec) -> Path:
    backup_root = config_dir / BACKUP_ROOT
    _safe_directory(backup_root, config_dir, create=True, private=True)
    backup_dir = backup_root / spec.migration_id
    _safe_directory(backup_dir, config_dir, create=True, private=True)
    _make_private_directory(backup_dir, config_dir)
    _make_private_directory(backup_root, config_dir)
    manifest_path = backup_dir / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Existing migration backup manifest must not be a link")
    if manifest_path.exists():
        _validate_manifest(backup_dir, config_dir, spec)
        return backup_dir

    entries: list[dict[str, object]] = []
    total = 0
    for name in _BACKUP_FILES:
        source = config_dir / name
        if source.is_symlink():
            raise ValueError("Migration backup input must not be a link")
        if not source.exists():
            continue
        payload = _read_bytes(source, config_dir)
        total += len(payload)
        if total > _MAX_BACKUP_BYTES:
            raise ValueError("Migration backup exceeds the size limit")
        destination = backup_dir / name
        if destination.exists():
            if _read_bytes(destination, config_dir, owner_only=True) != payload:
                raise ValueError("Partial migration backup does not match its source")
        else:
            _write_private_backup(destination, payload, config_dir)
        entries.append({"name": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {
        "manifest_version": 1,
        "migration_id": spec.migration_id,
        "source_schema": spec.source_schema,
        "target_schema": spec.target_schema,
        "created_at": _utc_now(),
        "files": entries,
    }
    _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n", config_dir)
    _validate_manifest(backup_dir, config_dir, spec)
    return backup_dir


def _apply_v086(config_dir: Path, source_dir: Path) -> None:
    source_settings = _read_text(source_dir / "settings.cfg", config_dir)
    config = Config.from_str(source_settings)
    remote_path, local_path = config.lftp.remote_path, config.lftp.local_path
    if not isinstance(remote_path, str) or not remote_path or remote_path.startswith("<"):
        raise ValueError("Legacy remote path is not valid")
    if not isinstance(local_path, str) or not local_path or local_path.startswith("<"):
        raise ValueError("Legacy local path is not valid")

    pair = PathPair(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, "seedsync:v086:{}\n{}".format(remote_path, local_path))),
        name="Default", remote_path=remote_path, local_path=local_path, enabled=True, auto_queue=True,
    )
    pair.validate()
    path_pair_content = json.dumps({
        "version": 1,
        "path_pairs": [{
            "id": pair.id, "name": pair.name, "remote_path": pair.remote_path,
            "local_path": pair.local_path, "enabled": pair.enabled, "auto_queue": pair.auto_queue,
        }],
    }, indent=2)
    controller = ControllerPersist.from_str(_read_text(source_dir / "controller.persist", config_dir))
    autoqueue = AutoQueuePersist.from_str(_read_text(source_dir / "autoqueue.persist", config_dir))

    _atomic_write(config_dir / "settings.cfg", config.to_str(), config_dir)
    _atomic_write(config_dir / "path_pairs.json", path_pair_content, config_dir)
    _atomic_write(config_dir / "controller.persist", controller.to_str(), config_dir)
    _atomic_write(config_dir / "autoqueue.persist", autoqueue.to_str(), config_dir)


def _validate_v086_result(config_dir: Path) -> None:
    Config.from_str(_read_text(config_dir / "settings.cfg", config_dir))
    collection = PathPairManager(str(config_dir)).from_str(_read_text(config_dir / "path_pairs.json", config_dir))
    if len(collection.path_pairs) != 1 or collection.path_pairs[0].name != "Default":
        raise ValueError("Migration must create exactly one Default path pair")
    ControllerPersist.from_str(_read_text(config_dir / "controller.persist", config_dir))
    AutoQueuePersist.from_str(_read_text(config_dir / "autoqueue.persist", config_dir))
    if (config_dir / "api-keys.json").exists():
        raise ValueError("Migration must not initialize administrator credentials")


def default_migration_registry() -> tuple[MigrationSpec, ...]:
    return (MigrationSpec(
        migration_id="original-v0.8.6-to-current-v1", order=100,
        source_schema="original-v0.8.6", target_schema=CURRENT_SCHEMA_ID,
        features=(
            MigrationFeature("Path pairs", "Converts the legacy download roots into a Default path pair."),
            MigrationFeature("Current settings", "Adds current defaults while preserving configured behavior."),
            MigrationFeature("First claim", "Leaves administrator setup available after the upgrade."),
        ),
        fingerprint=_legacy_v086_fingerprint, apply=_apply_v086, validate=_validate_v086_result,
    ),)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # Python implements os.kill() on Windows with TerminateProcess for
        # ordinary signal values, so the POSIX signal-0 liveness probe is not
        # safe here. Querying a limited-information process handle has no
        # side effect on the owner we are trying to protect.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32, _, _ = _windows_api()
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # Protected processes can reject the query even though they exist.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError as exc:
        return exc.errno != errno.ESRCH


class MigrationCoordinator:
    """Stable preflight/status/apply boundary for selected-major migrations."""

    _process_lock = threading.Lock()

    def __init__(self, config_dir: str | os.PathLike[str], registry: Sequence[MigrationSpec] | None = None) -> None:
        self.config_dir = Path(config_dir)
        self.metadata_path = self.config_dir / METADATA_FILE
        self.lock_path = self.config_dir / LOCK_FILE
        self.registry = tuple(sorted(registry or default_migration_registry(), key=lambda item: item.order))
        self._root_identity: tuple[int, ...] | None = None
        ids = [spec.migration_id for spec in self.registry]
        if len(ids) != len(set(ids)):
            raise ValueError("Migration registry contains duplicate ids")

    def status(self) -> MigrationDecision:
        """Return the durable preflight decision without constructing normal SeedSync."""
        return self.preflight()

    def preflight(self) -> MigrationDecision:
        identity = _capture_root_identity(self.config_dir)
        if self._root_identity is None:
            self._root_identity = identity
        elif identity != self._root_identity:
            raise ValueError("Migration configuration root identity changed")
        with _root_transaction(self.config_dir, self._root_identity):
            return self._preflight_anchored()

    def _preflight_anchored(self) -> MigrationDecision:
        _safe_directory(self.config_dir, self.config_dir)
        metadata = self._read_metadata()
        if metadata is not None:
            decision = self._decision_from_metadata(metadata)
            if decision.state == MigrationState.RUNNING:
                lock_state = self._lock_state(decision.migration_id)
                if lock_state == "orphaned":
                    self._reclaim_orphan_lock(decision.migration_id)
                    return self._record_failure(decision.migration_id, "Previous migration attempt was interrupted", True)
                if lock_state == "missing":
                    return self._record_failure(decision.migration_id, "Previous migration attempt was interrupted", True)
                return decision
            if self._lock_state(decision.migration_id) == "orphaned":
                self._reclaim_orphan_lock(decision.migration_id)
            if decision.state in (MigrationState.REQUIRED, MigrationState.FAILED):
                return decision
            return self._advance_lineage(metadata, decision.state)

        relevant = [path for path in self.config_dir.iterdir() if path.name not in (LOCK_FILE, METADATA_FILE)]
        if not relevant:
            decision = MigrationDecision(MigrationState.NOT_REQUIRED)
            self._write_metadata(decision, current_schema=CURRENT_SCHEMA_ID, applied_migrations=[], attempt=0)
            return decision
        matches = [spec for spec in self.registry if spec.source_schema == "original-v0.8.6" and spec.fingerprint(self.config_dir)]
        if len(matches) == 1:
            spec = matches[0]
            decision = self._decision_for_spec(MigrationState.REQUIRED, spec)
            self._write_metadata(
                decision, current_schema=spec.source_schema, applied_migrations=[], attempt=0,
            )
            return decision
        if len(matches) > 1:
            return self._record_failure(None, "Configuration matches more than one selected migration", False)
        if _looks_current(self.config_dir):
            decision = MigrationDecision(MigrationState.NOT_REQUIRED)
            self._write_metadata(decision, current_schema=CURRENT_SCHEMA_ID, applied_migrations=[], attempt=0)
            return self._advance_lineage(self._read_metadata() or {}, MigrationState.NOT_REQUIRED)
        return self._record_failure(None, "Nonempty configuration has an unsupported or ambiguous schema", False)

    def apply_confirmed(self, *, retry: bool = False) -> MigrationDecision:
        """Apply exactly one pending migration after explicit caller confirmation."""
        identity = _capture_root_identity(self.config_dir)
        if self._root_identity is None:
            self._root_identity = identity
        elif identity != self._root_identity:
            raise ValueError("Migration configuration root identity changed")
        with _root_transaction(self.config_dir, self._root_identity):
            return self._apply_confirmed_anchored(retry=retry)

    def _apply_confirmed_anchored(self, *, retry: bool = False) -> MigrationDecision:
        with self._process_lock:
            decision = self.preflight()
            if decision.state == MigrationState.RUNNING:
                raise MigrationBlockedError(decision)
            if decision.state == MigrationState.FAILED and (not retry or not decision.retryable):
                raise MigrationBlockedError(decision)
            if decision.state not in (MigrationState.REQUIRED, MigrationState.FAILED):
                return decision
            spec = self._spec(decision.migration_id)
            if spec is None:
                raise MigrationBlockedError(decision)
            metadata = self._read_metadata() or {}
            if decision.state == MigrationState.REQUIRED and not spec.fingerprint(self.config_dir):
                failed = self._record_failure(
                    spec.migration_id, "Configuration changed after migration detection; reassessment is required", False,
                )
                raise MigrationBlockedError(failed)

            lock_bytes: bytes | None = None
            descriptor: int | None = None
            try:
                lock_bytes = json.dumps({
                    "lock_version": 1, "pid": os.getpid(), "hostname": socket.gethostname(),
                    "migration_id": spec.migration_id, "created_at": _utc_now(),
                }, sort_keys=True).encode("utf-8")
                descriptor = _open_anchored(
                    self.lock_path, self.config_dir, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    owner_control=os.name == "nt",
                )
                _restrict_fd_to_owner(descriptor)
                os.write(descriptor, lock_bytes)
                os.fsync(descriptor)
                attempt = int(metadata.get("attempt", 0)) + 1
                running = self._decision_for_spec(MigrationState.RUNNING, spec)
                self._write_metadata(
                    running, current_schema=metadata.get("current_schema", spec.source_schema),
                    applied_migrations=self._applied(metadata), attempt=attempt,
                )
                backup_dir = _backup_metadata(self.config_dir, spec)
                spec.apply(self.config_dir, backup_dir)
                spec.validate(self.config_dir)
                applied = self._applied(metadata)
                if spec.migration_id not in applied:
                    applied.append(spec.migration_id)
                complete = self._decision_for_spec(MigrationState.COMPLETE, spec)
                self._write_metadata(
                    complete, current_schema=spec.target_schema, applied_migrations=applied,
                    attempt=attempt, backup=str(backup_dir.relative_to(self.config_dir)),
                    receipt_version=1, completed_at=_utc_now(),
                )
                return complete
            except FileExistsError:
                raise MigrationBlockedError(self.preflight())
            except Exception as exc:
                failed = self._record_failure(
                    spec.migration_id,
                    "Migration step failed ({}); the metadata backup was retained".format(type(exc).__name__)[:500],
                    True,
                )
                raise MigrationBlockedError(failed) from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                    self._remove_owned_lock(lock_bytes)

    def require_normal_startup(self) -> MigrationDecision:
        decision = self.preflight()
        if not decision.allows_normal_startup:
            raise MigrationBlockedError(decision)
        return decision

    def _advance_lineage(self, metadata: Mapping[str, object], terminal_state: MigrationState) -> MigrationDecision:
        current_schema = metadata.get("current_schema")
        if not isinstance(current_schema, str):
            return self._record_failure(None, "Migration lineage metadata is invalid", False)
        applied = self._applied(metadata)
        last_id = applied[-1] if applied else None
        last_spec = self._spec(last_id)
        if last_spec is not None:
            try:
                last_spec.validate(self.config_dir)
            except Exception as exc:
                return self._record_failure(
                    last_id, "Completed migration validation failed: {}".format(type(exc).__name__), True,
                )
        candidates = [
            spec for spec in self.registry
            if spec.source_schema == current_schema and spec.migration_id not in applied and spec.fingerprint(self.config_dir)
        ]
        if len(candidates) > 1:
            return self._record_failure(None, "Schema lineage matches more than one selected migration", False)
        if candidates:
            spec = candidates[0]
            decision = self._decision_for_spec(MigrationState.REQUIRED, spec)
            self._write_metadata(
                decision, current_schema=current_schema, applied_migrations=applied,
                attempt=int(metadata.get("attempt", 0)),
            )
            return decision
        decision = self._decision_from_metadata(metadata)
        if decision.state != terminal_state:
            decision = MigrationDecision(terminal_state)
        return decision

    def _lock_state(self, migration_id: str | None) -> str:
        if not self.lock_path.exists():
            return "missing"
        try:
            lock = _read_json_object(self.lock_path, self.config_dir, 4096)
            if lock.get("lock_version") != 1 or lock.get("migration_id") != migration_id:
                return "unknown"
            pid, hostname = lock.get("pid"), lock.get("hostname")
            if type(pid) is not int or not isinstance(hostname, str):
                return "unknown"
            if hostname != socket.gethostname():
                return "unknown"
            return "active" if _process_is_alive(cast(int, pid)) else "orphaned"
        except Exception:
            return "unknown"

    def _reclaim_orphan_lock(self, migration_id: str | None) -> None:
        lock_state = self._lock_state(migration_id)
        if lock_state == "missing":
            return
        if lock_state != "orphaned":
            raise MigrationBlockedError(self._decision_from_metadata(self._read_metadata() or {}))
        try:
            _secure_unlink(self.lock_path, self.config_dir)
        except FileNotFoundError:
            pass

    def _remove_owned_lock(self, expected: bytes | None) -> None:
        if expected is None:
            return
        try:
            if _read_bytes(self.lock_path, self.config_dir, 4096) == expected:
                _secure_unlink(self.lock_path, self.config_dir)
        except (OSError, ValueError):
            pass

    def _spec(self, migration_id: str | None) -> MigrationSpec | None:
        return next((spec for spec in self.registry if spec.migration_id == migration_id), None)

    def _decision_for_spec(self, state: MigrationState, spec: MigrationSpec) -> MigrationDecision:
        return MigrationDecision(state, spec.migration_id, spec.source_schema, spec.target_schema, spec.features)

    def _read_metadata(self) -> dict[str, object] | None:
        if not self.metadata_path.exists():
            return None
        try:
            return _read_json_object(self.metadata_path, self.config_dir)
        except Exception as exc:
            return {
                "state": MigrationState.FAILED.value, "target_schema": CURRENT_SCHEMA_ID,
                "error": "Migration metadata is invalid: {}".format(type(exc).__name__), "retryable": False,
            }

    def _decision_from_metadata(self, metadata: Mapping[str, object]) -> MigrationDecision:
        try:
            state = MigrationState(metadata["state"])
        except (KeyError, ValueError):
            state = MigrationState.FAILED
        migration_id = metadata.get("migration_id")
        migration_id = migration_id if isinstance(migration_id, str) else None
        spec = self._spec(migration_id)
        source, target, error = metadata.get("source_schema"), metadata.get("target_schema"), metadata.get("error")
        return MigrationDecision(
            state=state, migration_id=migration_id,
            source_schema=source if isinstance(source, str) else None,
            target_schema=target if isinstance(target, str) else CURRENT_SCHEMA_ID,
            features=spec.features if spec is not None else (),
            error=error if isinstance(error, str) else None,
            retryable=metadata.get("retryable") is True,
        )

    @staticmethod
    def _applied(metadata: Mapping[str, object]) -> list[str]:
        value = metadata.get("applied_migrations", [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("Migration applied lineage is invalid")
        return list(cast(list[str], value))

    def _record_failure(self, migration_id: str | None, error: str, retryable: bool) -> MigrationDecision:
        spec = self._spec(migration_id)
        decision = self._decision_for_spec(MigrationState.FAILED, spec) if spec is not None else MigrationDecision(
            MigrationState.FAILED,
        )
        decision = MigrationDecision(
            state=decision.state, migration_id=decision.migration_id, source_schema=decision.source_schema,
            target_schema=decision.target_schema, features=decision.features, error=error, retryable=retryable,
        )
        metadata = self._read_metadata() or {}
        try:
            applied = self._applied(metadata)
        except ValueError:
            applied = []
        self._write_metadata(
            decision, current_schema=metadata.get("current_schema"), applied_migrations=applied,
            attempt=int(metadata.get("attempt", 0)),
        )
        return decision

    def _write_metadata(self, decision: MigrationDecision, **extra: object) -> None:
        payload: dict[str, object] = {
            "metadata_version": 2, "state": decision.state.value, "migration_id": decision.migration_id,
            "source_schema": decision.source_schema, "target_schema": decision.target_schema,
            "error": decision.error, "retryable": decision.retryable, "updated_at": _utc_now(),
        }
        payload.update(extra)
        _atomic_write(self.metadata_path, json.dumps(payload, indent=2, sort_keys=True) + "\n", self.config_dir)
