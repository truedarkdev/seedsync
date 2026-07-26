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
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence, cast

from common import Config, PathPair, PathPairManager
from controller import AutoQueuePersist, ControllerPersist
from .backup_restore import (
    BackupRestoreError,
    create_retained_backup,
    resolve_backup,
    restore_backup,
    validate_backup,
)
from .runtime_exclusion import RuntimeExclusion, RuntimeExclusionError


CURRENT_SCHEMA_ID = "seedsync-current-v1"
METADATA_FILE = "migration-state.json"
LOCK_FILE = ".migration.lock"
BACKUP_ROOT = "migration-backups"
_MAX_JSON_BYTES = 256 * 1024
_MAX_RELEVANT_FILE_BYTES = 16 * 1024 * 1024
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
Validate = Callable[[Path], None]


class ValidatedBackupReader:
    """An immutable, manifest-bound view of a migration's declared inputs."""

    def __init__(self, payloads: Mapping[str, bytes]):
        self.__payloads = dict(payloads)

    @classmethod
    def freeze(
        cls,
        backup_dir: Path,
        config_root: Path,
        manifest: Mapping[str, object],
        declared_inputs: Sequence[str],
    ) -> "ValidatedBackupReader":
        manifest_entries = manifest.get("entries")
        if not isinstance(manifest_entries, list):
            raise ValueError("Migration backup manifest entries are invalid")
        entries = {
            cast(str, entry.get("path")): entry
            for entry in manifest_entries
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        }
        payloads: dict[str, bytes] = {}
        total_size = 0
        for declared in declared_inputs:
            relative = PurePosixPath(declared)
            if (
                not declared
                or declared != relative.as_posix()
                or relative.is_absolute()
                or "\\" in declared
                or any(part in ("", ".", "..") for part in relative.parts)
                or declared in payloads
            ):
                raise ValueError("Migration input declaration is invalid")
            entry = entries.get(declared)
            if not isinstance(entry, dict) or entry.get("type") != "file":
                raise ValueError("Declared migration input is absent from the retained backup")
            size, digest = entry.get("size"), entry.get("sha256")
            if type(size) is not int or cast(int, size) > _MAX_RELEVANT_FILE_BYTES:
                raise ValueError("Declared migration input exceeds the size limit")
            total_size += cast(int, size)
            if total_size > _MAX_RELEVANT_FILE_BYTES * max(1, len(declared_inputs)):
                raise ValueError("Declared migration inputs exceed the aggregate size limit")
            payload = _read_bytes(
                backup_dir / "data" / Path(*relative.parts),
                config_root,
                _MAX_RELEVANT_FILE_BYTES,
                owner_only=True,
            )
            if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError("Declared migration input changed after backup validation")
            payloads[declared] = payload
        return cls(payloads)

    def read_bytes(self, relative_path: str) -> bytes:
        try:
            return self.__payloads[relative_path]
        except KeyError as exc:
            raise ValueError("Migration spec requested an undeclared backup input") from exc

    def read_text(self, relative_path: str) -> str:
        return self.read_bytes(relative_path).decode("utf-8")


Apply = Callable[[Path, ValidatedBackupReader], None]


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
    input_files: tuple[str, ...] = ()


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

# Docker/local runtime wrappers add these transport-policy values before the
# coordinator can inspect an otherwise untouched v0.8.6 settings file. They do
# not describe the persisted schema and must not erase truthful source identity.
_PRE_MIGRATION_RUNTIME_GENERAL_KEYS = frozenset((
    "trusted_browser_bootstrap_remote_addrs",
    "config_api_redact_remote_details",
))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_receipt_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


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
    allow_final_reparse: bool = False,
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

        desired_access = 0
        if flags & (os.O_WRONLY | os.O_RDWR):
            desired_access |= 0x40000000
        if not flags & os.O_WRONLY:
            desired_access |= 0x80000000
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
                None, 0, 0x1,
                2 if flags & os.O_EXCL else (3 if flags & os.O_CREAT else 1),
                0x00200060, None, 0,
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
        if tag_info.FileAttributes & file_attribute_reparse_point and not allow_final_reparse:
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

    descriptor = _open_anchored(
        path, root, os.O_RDONLY, delete_control=True, allow_final_reparse=True,
    )
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


def _secure_rmdir(path: Path, root: Path) -> None:
    if os.name == "posix":
        with _mutation_parent(path, root) as (directory_fd, name):
            os.rmdir(name, dir_fd=directory_fd)
        return

    import ctypes
    from ctypes import wintypes

    with _mutation_parent(path, root) as (parent_handle, target_path):
        handle = _windows_open_directory(
            parent_handle, target_path.name, delete_control=True,
        )
        try:
            class FileDispositionInfo(ctypes.Structure):
                _fields_ = (("DeleteFile", wintypes.BOOL),)

            info = FileDispositionInfo(True)
            kernel32, _, _ = _windows_api()
            if not kernel32.SetFileInformationByHandle(
                wintypes.HANDLE(handle), 4, ctypes.byref(info), ctypes.sizeof(info),
            ):
                raise OSError(ctypes.get_last_error(), "Unable to remove anchored migration directory")
        finally:
            _windows_api()[0].CloseHandle(wintypes.HANDLE(handle))


def _windows_rename_handle(
    handle: int, directory_handle: int, target_name: str, *, replace: bool = True,
) -> None:
    import ctypes
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
    info.ReplaceIfExists = replace
    info.RootDirectory = directory_handle
    info.FileNameLength = len(target_name.encode("utf-16-le"))
    info.FileName = target_name
    io_status = IoStatusBlock()
    _, _, ntdll = _windows_api()
    status = ntdll.NtSetInformationFile(
        wintypes.HANDLE(handle), ctypes.byref(io_status),
        ctypes.byref(info), ctypes.sizeof(info), 10,
    )
    if status < 0:
        error = ntdll.RtlNtStatusToDosError(status)
        raise OSError(error, "Unable to commit anchored migration file")


def _windows_rename_fd(descriptor: int, directory_handle: int, target_name: str) -> None:
    import msvcrt

    _windows_rename_handle(msvcrt.get_osfhandle(descriptor), directory_handle, target_name)


def _windows_open_directory(
    directory_handle: int, name: str, *, create: bool = False, owner_control: bool = False,
    delete_control: bool = False,
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
            ctypes.byref(created_handle), 0x00100080 | (0x00060000 if owner_control else 0)
            | (0x00010000 if delete_control else 0),
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
                actual.difference_update(_PRE_MIGRATION_RUNTIME_GENERAL_KEYS)
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


def _validate_manifest(backup_dir: Path, config_dir: Path, spec: MigrationSpec) -> dict[str, object]:
    manifest = validate_backup(backup_dir, config_dir)
    if (
        manifest.get("migration_id") != spec.migration_id
        or manifest.get("source_schema") != spec.source_schema
        or manifest.get("target_schema") != spec.target_schema
    ):
        raise ValueError("Existing migration backup manifest identity is invalid")
    return manifest


def _backup_metadata(config_dir: Path, spec: MigrationSpec) -> Path:
    return create_retained_backup(
        config_dir,
        migration_id=spec.migration_id,
        source_schema=spec.source_schema,
        target_schema=spec.target_schema,
    )


def _apply_v086(config_dir: Path, source: ValidatedBackupReader) -> None:
    source_settings = source.read_text("settings.cfg")
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
    controller = ControllerPersist.from_str(source.read_text("controller.persist"))
    autoqueue = AutoQueuePersist.from_str(source.read_text("autoqueue.persist"))

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
        input_files=("settings.cfg", "controller.persist", "autoqueue.persist"),
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

    _process_lock = threading.RLock()

    def __init__(self, config_dir: str | os.PathLike[str], registry: Sequence[MigrationSpec] | None = None) -> None:
        self.config_dir = Path(os.path.abspath(config_dir))
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

    def retained_backup_ready(self, decision: MigrationDecision | None = None) -> bool:
        """Report whether the durable receipt names a fully valid retained backup."""
        decision = decision or self.preflight()
        spec = self._spec(decision.migration_id)
        if spec is None:
            return False
        identity = _capture_root_identity(self.config_dir)
        if self._root_identity is None:
            self._root_identity = identity
        elif identity != self._root_identity:
            raise ValueError("Migration configuration root identity changed")
        with _root_transaction(self.config_dir, self._root_identity):
            metadata = self._read_metadata() or {}
            reference = metadata.get("backup")
            if not isinstance(reference, str):
                return False
            try:
                backup_dir = self.config_dir / reference
                validate_backup(backup_dir, self.config_dir)
                _validate_manifest(backup_dir, self.config_dir, spec)
            except (BackupRestoreError, OSError, ValueError, json.JSONDecodeError):
                return False
            return True

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
                if lock_state in ("orphaned", "missing"):
                    return self._failure_decision(
                        decision.migration_id, "Previous migration attempt was interrupted", True,
                    )
                return decision
            if decision.state in (MigrationState.REQUIRED, MigrationState.FAILED):
                return decision
            if decision.state == MigrationState.COMPLETE:
                return self._validate_completed_lineage(metadata)
            return self._failure_decision(None, "Migration lineage metadata is invalid", False)

        relevant = [
            path for path in self.config_dir.iterdir()
            if path.name not in (LOCK_FILE, METADATA_FILE, ".seedsync.runtime.lock")
        ]
        if not relevant:
            return MigrationDecision(MigrationState.NOT_REQUIRED)
        matches = [spec for spec in self.registry if spec.source_schema == "original-v0.8.6" and spec.fingerprint(self.config_dir)]
        if len(matches) == 1:
            spec = matches[0]
            return self._decision_for_spec(MigrationState.REQUIRED, spec)
        if len(matches) > 1:
            return self._failure_decision(None, "Configuration matches more than one selected migration", False)
        if _looks_current(self.config_dir):
            return MigrationDecision(MigrationState.NOT_REQUIRED)
        return self._failure_decision(None, "Nonempty configuration has an unsupported or ambiguous schema", False)

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
            try:
                with RuntimeExclusion(self.config_dir, "migration-apply"):
                    return self._apply_confirmed_exclusive(retry=retry)
            except RuntimeExclusionError as exc:
                decision = self._failure_decision(
                    None, "Migration refused because the SeedSync runtime is active", True,
                )
                raise MigrationBlockedError(decision) from exc

    def _apply_confirmed_exclusive(self, *, retry: bool = False) -> MigrationDecision:
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
            backup_ready = False
            backup_anchor = None
            try:
                lock_state = self._lock_state(spec.migration_id)
                if lock_state == "orphaned":
                    self._reclaim_orphan_lock(spec.migration_id)
                elif lock_state != "missing":
                    raise MigrationBlockedError(decision)
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
                recorded_backup = metadata.get("backup")
                if isinstance(recorded_backup, str):
                    candidate_backup = self.config_dir / recorded_backup
                    candidate_manifest = validate_backup(candidate_backup, self.config_dir)
                    if candidate_manifest.get("migration_id") == spec.migration_id:
                        _validate_manifest(candidate_backup, self.config_dir, spec)
                        backup_dir = candidate_backup
                    else:
                        backup_dir = _backup_metadata(self.config_dir, spec)
                else:
                    backup_dir = _backup_metadata(self.config_dir, spec)
                backup_anchor = _mutation_parent(backup_dir / "data" / ".apply-anchor", self.config_dir)
                backup_anchor.__enter__()
                manifest = _validate_manifest(backup_dir, self.config_dir, spec)
                source = ValidatedBackupReader.freeze(
                    backup_dir, self.config_dir, manifest, spec.input_files,
                )
                backup_ready = True
                # The retained backup is fully copied, fsynced, published, and
                # validated before checkpoint metadata or migration outputs may
                # mutate the old configuration inventory.
                running = self._decision_for_spec(MigrationState.RUNNING, spec)
                self._write_metadata(
                    running, current_schema=metadata.get("current_schema", spec.source_schema),
                    applied_migrations=self._applied(metadata), attempt=attempt,
                    backup=backup_dir.relative_to(self.config_dir).as_posix(),
                )
                spec.apply(self.config_dir, source)
                spec.validate(self.config_dir)
                # A same-owner writer cannot influence the already-frozen
                # migration inputs. Revalidate before issuing the completion
                # receipt so later corruption is never silently accepted.
                _validate_manifest(backup_dir, self.config_dir, spec)
                applied = self._applied(metadata)
                if spec.migration_id not in applied:
                    applied.append(spec.migration_id)
                complete = self._decision_for_spec(MigrationState.COMPLETE, spec)
                self._write_metadata(
                    complete, current_schema=spec.target_schema, applied_migrations=applied,
                    attempt=attempt, backup=backup_dir.relative_to(self.config_dir).as_posix(),
                    receipt_version=1, completed_at=_utc_now(),
                )
                return complete
            except FileExistsError:
                raise MigrationBlockedError(self.preflight())
            except MigrationBlockedError:
                raise
            except Exception as exc:
                error = "Migration step failed ({})".format(type(exc).__name__)[:500]
                if backup_ready:
                    failed = self._record_failure(
                        spec.migration_id, error + "; the full configuration backup was retained", True,
                    )
                else:
                    failed = self._failure_decision(
                        spec.migration_id, error + "; configuration was not mutated", True,
                    )
                raise MigrationBlockedError(failed) from exc
            finally:
                if backup_anchor is not None:
                    backup_anchor.__exit__(None, None, None)
                if descriptor is not None:
                    os.close(descriptor)
                    self._remove_owned_lock(lock_bytes)

    def restore_offline(self, backup_reference: str) -> dict[str, int]:
        """Restore a retained backup while normal/web runtime remains unconstructed."""
        identity = _capture_root_identity(self.config_dir)
        if self._root_identity is None:
            self._root_identity = identity
        elif identity != self._root_identity:
            raise ValueError("Migration configuration root identity changed")
        with _root_transaction(self.config_dir, self._root_identity):
            try:
                exclusion = RuntimeExclusion(self.config_dir, "migration-restore")
            except RuntimeExclusionError as exc:
                raise BackupRestoreError("Offline restore refused because SeedSync is active") from exc
            with exclusion, self._process_lock:
                backup_dir = resolve_backup(self.config_dir, backup_reference)
                if self.lock_path.exists() or self.lock_path.is_symlink():
                    reclaimable = False
                    try:
                        existing_lock = _read_json_object(self.lock_path, self.config_dir, 4096)
                        reclaimable = (
                            existing_lock.get("lock_version") == 1
                            and existing_lock.get("migration_id") == "restore:" + backup_dir.name
                            and existing_lock.get("hostname") == socket.gethostname()
                            and type(existing_lock.get("pid")) is int
                            and not _process_is_alive(cast(int, existing_lock["pid"]))
                        )
                    except Exception:
                        reclaimable = False
                    if not reclaimable:
                        raise BackupRestoreError(
                            "Offline restore refused because a migration transaction lock already exists"
                        )
                    _secure_unlink(self.lock_path, self.config_dir)
                lock_bytes = json.dumps({
                    "lock_version": 1,
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "migration_id": "restore:" + backup_dir.name,
                    "created_at": _utc_now(),
                }, sort_keys=True).encode("utf-8")
                descriptor: int | None = None
                try:
                    descriptor = _open_anchored(
                        self.lock_path, self.config_dir, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        owner_control=os.name == "nt",
                    )
                    _restrict_fd_to_owner(descriptor)
                    os.write(descriptor, lock_bytes)
                    os.fsync(descriptor)
                    with _mutation_parent(backup_dir / "data" / ".restore-anchor", self.config_dir):
                        return restore_backup(self.config_dir, backup_dir)
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
                    self._remove_owned_lock(lock_bytes)

    def require_normal_startup(self) -> MigrationDecision:
        decision = self.preflight()
        if not decision.allows_normal_startup:
            raise MigrationBlockedError(decision)
        return decision

    def legacy_web_port(self, default: int = 8800) -> int:
        """Read only the bounded legacy Web port needed by migration mode."""
        try:
            settings = _parse_settings(self.config_dir / "settings.cfg", self.config_dir)
            value = settings.get("Web", "port")
            if not value.isascii() or not value.isdecimal():
                return default
            port = int(value)
            return port if 1 <= port <= 65535 else default
        except (OSError, ValueError, configparser.Error):
            return default

    def _expected_completed_specs(self) -> tuple[MigrationSpec, ...]:
        lineage: list[MigrationSpec] = []
        schema = "original-v0.8.6"
        while True:
            candidates = [spec for spec in self.registry if spec.source_schema == schema]
            if not candidates:
                return tuple(lineage)
            if len(candidates) != 1:
                return ()
            spec = candidates[0]
            lineage.append(spec)
            schema = spec.target_schema
            if len(lineage) > len(self.registry):
                return ()
        return tuple(lineage)

    def _validate_completed_lineage(self, metadata: Mapping[str, object]) -> MigrationDecision:
        expected_specs = self._expected_completed_specs()
        try:
            applied = self._applied(metadata)
        except ValueError:
            applied = []
        known_ids = [spec.migration_id for spec in expected_specs]
        applied_specs = expected_specs[:len(applied)]
        last_spec = applied_specs[-1] if applied_specs else None
        expected_backup = metadata.get("backup") if last_spec else None
        valid_backup_path = False
        if isinstance(expected_backup, str):
            backup_path = Path(expected_backup)
            valid_backup_path = (
                len(backup_path.parts) == 2
                and backup_path.parts[0] == BACKUP_ROOT
                and not backup_path.parts[1].startswith(".")
            )
        valid_receipt = (
            last_spec is not None
            and len(applied) <= len(expected_specs)
            and applied == known_ids[:len(applied)]
            and metadata.get("metadata_version") == 2
            and metadata.get("receipt_version") == 1
            and metadata.get("state") == MigrationState.COMPLETE.value
            and metadata.get("migration_id") == last_spec.migration_id
            and metadata.get("source_schema") == last_spec.source_schema
            and metadata.get("target_schema") == last_spec.target_schema
            and metadata.get("current_schema") == last_spec.target_schema
            and type(metadata.get("attempt")) is int
            and cast(int, metadata.get("attempt")) >= 1
            and _valid_receipt_timestamp(metadata.get("completed_at"))
            and _valid_receipt_timestamp(metadata.get("updated_at"))
            and valid_backup_path
            and metadata.get("error") is None
            and metadata.get("retryable") is False
        )
        if not valid_receipt:
            return self._failure_decision(None, "Completed migration lineage is invalid", False)
        try:
            last_spec.validate(self.config_dir)
            _validate_manifest(self.config_dir / cast(str, expected_backup), self.config_dir, last_spec)
        except Exception as exc:
            return self._failure_decision(
                last_spec.migration_id,
                "Completed migration validation failed: {}".format(type(exc).__name__),
                True,
            )
        if len(applied) < len(expected_specs):
            next_spec = expected_specs[len(applied)]
            if next_spec.source_schema != last_spec.target_schema or not next_spec.fingerprint(self.config_dir):
                return self._failure_decision(None, "Completed migration lineage does not match configuration", False)
            return self._decision_for_spec(MigrationState.REQUIRED, next_spec)
        return self._decision_for_spec(MigrationState.COMPLETE, last_spec)

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
        decision = self._failure_decision(migration_id, error, retryable)
        metadata = self._read_metadata() or {}
        try:
            applied = self._applied(metadata)
        except ValueError:
            applied = []
        extra: dict[str, object] = {}
        if isinstance(metadata.get("backup"), str):
            extra["backup"] = metadata["backup"]
        self._write_metadata(
            decision, current_schema=metadata.get("current_schema"), applied_migrations=applied,
            attempt=int(metadata.get("attempt", 0)), **extra,
        )
        return decision

    def _failure_decision(self, migration_id: str | None, error: str, retryable: bool) -> MigrationDecision:
        spec = self._spec(migration_id)
        decision = self._decision_for_spec(MigrationState.FAILED, spec) if spec is not None else MigrationDecision(
            MigrationState.FAILED,
        )
        decision = MigrationDecision(
            state=decision.state, migration_id=decision.migration_id, source_schema=decision.source_schema,
            target_schema=decision.target_schema, features=decision.features, error=error, retryable=retryable,
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
