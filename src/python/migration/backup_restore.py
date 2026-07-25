"""Retained full-tree migration backups and offline restore support.

Only the anchored SeedSync configuration root is in scope. Runtime homes,
downloads, mounts, and transfer staging are deliberately outside this contract
and are never followed or copied.
"""

from __future__ import annotations

import hashlib
import errno
import json
import os
import re
import shutil
import stat
import uuid
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence, cast


BACKUP_ROOT_NAME = "migration-backups"
MANIFEST_NAME = "manifest.json"
# An exact 128-bit-suffixed directory in this namespace is a reserved,
# migration-owned transaction envelope. Owner-private provenance is the trust
# boundary; other same-account processes are trusted not to forge it.
PUBLICATION_TXN_PREFIX = ".publication-txn-"
PUBLICATION_INTENT_NAME = "intent.json"
PUBLICATION_STAGING_NAME = "staging"
MANIFEST_VERSION = 2
RESTORE_JOURNAL_NAME = ".migration-restore.json"
RESTORE_STAGE_PREFIX = ".migration-restore-"
RESTORE_STAGE_SUFFIX = ".staging"

# These deliberately bound untrusted source trees and manifests. They are
# substantially above normal SeedSync configuration sizes without allowing an
# accidental mount or hostile tree to consume unbounded resources.
MAX_ENTRIES = 10_000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_DEPTH = 64
MAX_COMPONENT_LENGTH = 255
MAX_RELATIVE_PATH_LENGTH = 4096
MAX_MANIFEST_BYTES = 4 * 1024 * 1024

_EXCLUDED_TOP_LEVEL_NAMES = frozenset({
    BACKUP_ROOT_NAME,
    ".migration.lock",
    ".seedsync.runtime.lock",
    "migration-state.json",
    RESTORE_JOURNAL_NAME,
})
_COORDINATOR_TEMP_TARGETS = frozenset({
    "migration-state.json", "settings.cfg", "path_pairs.json",
    "controller.persist", "autoqueue.persist",
})


class BackupRestoreError(RuntimeError):
    """A backup or restore invariant could not be satisfied safely."""


def _bounded_restore_relative(path: Path | PurePosixPath | str, config_root: Path) -> str:
    if isinstance(path, Path):
        candidate = Path(os.path.abspath(path))
        try:
            relative = candidate.relative_to(Path(os.path.abspath(config_root)))
        except ValueError as exc:
            raise BackupRestoreError("Restore diagnostic path escaped the configuration root") from exc
        value = PurePosixPath(*relative.parts)
    else:
        value = PurePosixPath(str(path))
    if value.is_absolute() or "\\" in value.as_posix() or any(part in ("", ".", "..") for part in value.parts):
        raise BackupRestoreError("Restore diagnostic path is invalid")
    return value.as_posix()


@contextmanager
def _restore_oserror_context(
    operation: str, path: Path | PurePosixPath | str, config_root: Path,
):
    relative = _bounded_restore_relative(path, config_root)
    try:
        yield
    except BackupRestoreError:
        raise
    except OSError as exc:
        error_name = errno.errorcode.get(exc.errno, "UNKNOWN")
        raise BackupRestoreError(
            "Restore {} failed for {!r} ({})".format(operation, relative, error_name)
        ) from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _validate_root(root: Path) -> tuple[int, ...]:
    absolute = Path(os.path.abspath(root))
    if absolute.parent == absolute:
        raise BackupRestoreError("Refusing a filesystem root as the SeedSync configuration root")
    info = absolute.lstat()
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise BackupRestoreError("SeedSync configuration root must be a real directory")
    return (info.st_dev, info.st_ino)


def canonical_config_root(root: Path | str) -> Path:
    """Canonicalize lexically while leaving links visible to validation."""
    return Path(os.path.abspath(os.fspath(root)))


def _assert_root_identity(root: Path, identity: tuple[int, ...]) -> None:
    if _validate_root(root) != identity:
        raise BackupRestoreError("SeedSync configuration root identity changed during the operation")


def _list_root_names(root: Path) -> list[str]:
    from .coordinator import _mutation_parent

    if os.name == "posix":
        expected_identity = _validate_root(root)
        descriptor = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != expected_identity:
                raise BackupRestoreError("Configuration root changed during anchored listing")
            with os.scandir(descriptor) as iterator:
                return sorted(entry.name for entry in iterator)
        finally:
            os.close(descriptor)
    with _mutation_parent(root / ".root-list-anchor", root):
        with os.scandir(root) as iterator:
            return sorted(entry.name for entry in iterator)


def _list_directory_names(directory: Path, config_root: Path) -> list[str]:
    from .coordinator import _mutation_parent, _open_anchored

    if os.name == "posix":
        descriptor = _open_anchored(
            directory, config_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            with os.scandir(descriptor) as iterator:
                return sorted(entry.name for entry in iterator)
        finally:
            os.close(descriptor)
    with _mutation_parent(directory / ".directory-list-anchor", config_root):
        with os.scandir(directory) as iterator:
            return sorted(entry.name for entry in iterator)


def _decode_mountinfo_path(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def detect_nested_mounts(root: Path, mountinfo_text: str | None = None) -> tuple[Path, ...]:
    """Detect Linux mount/bind boundaries, including same-device bind mounts."""
    root = canonical_config_root(root)
    mountinfo = Path("/proc/self/mountinfo")
    if mountinfo_text is None and (os.name != "posix" or not mountinfo.exists()):
        return ()
    text = mountinfo_text if mountinfo_text is not None else mountinfo.read_text(encoding="utf-8")
    nested: list[Path] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 5:
            raise BackupRestoreError("Linux mount information is malformed")
        mount_point = Path(_decode_mountinfo_path(fields[4]))
        try:
            relative = mount_point.relative_to(root)
        except ValueError:
            continue
        if relative.parts:
            nested.append(mount_point)
    return tuple(sorted(set(nested)))


def _reject_nested_mounts(root: Path, detector=None) -> None:
    if (detector or detect_nested_mounts)(root):
        raise BackupRestoreError("Nested mounts or bind mounts below the configuration root are unsupported")


def is_migration_infrastructure(relative: PurePosixPath) -> bool:
    """Return whether a normalized relative path is migration-owned infrastructure."""
    if not relative.parts:
        return False
    top = relative.parts[0]
    if top in _EXCLUDED_TOP_LEVEL_NAMES:
        return True
    if top.startswith(RESTORE_STAGE_PREFIX) and top.endswith(RESTORE_STAGE_SUFFIX):
        return True
    if len(relative.parts) == 1 and top.startswith(".") and top.endswith(".tmp"):
        body = top[1:-4]
        return any(body.startswith(target + "-") for target in _COORDINATOR_TEMP_TARGETS)
    return False


def infrastructure_exclusions() -> tuple[str, ...]:
    """Stable public description used by documentation and focused tests."""
    return tuple(sorted(_EXCLUDED_TOP_LEVEL_NAMES)) + (
        RESTORE_STAGE_PREFIX + "*" + RESTORE_STAGE_SUFFIX,
        ".<coordinator-output>-<uuid>.tmp",
    )


def _normalized_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise BackupRestoreError("Backup manifest contains an invalid relative path")
    normalized_value = unicodedata.normalize("NFC", value)
    if normalized_value != value:
        raise BackupRestoreError("Backup path is not in canonical Unicode NFC form")
    path = PurePosixPath(normalized_value)
    if path.is_absolute() or str(path) != value or any(part in ("", ".", "..") for part in path.parts):
        raise BackupRestoreError("Backup manifest path escapes the configuration root")
    if len(value) > MAX_RELATIVE_PATH_LENGTH or len(path.parts) > MAX_DEPTH:
        raise BackupRestoreError("Backup manifest path exceeds the configured limit")
    if any(len(part) > MAX_COMPONENT_LENGTH for part in path.parts):
        raise BackupRestoreError("Backup manifest name exceeds the configured limit")
    if is_migration_infrastructure(path):
        raise BackupRestoreError("Backup manifest attempts to restore migration infrastructure")
    return path


def _safe_mode(value: object) -> int:
    if type(value) is not int or cast(int, value) < 0 or cast(int, value) > 0o7777:
        raise BackupRestoreError("Backup manifest contains an invalid POSIX mode")
    return cast(int, value)


def _hash_file(
    path: Path,
    expected_size: int | None = None,
    *,
    root: Path | None = None,
    owner_only: bool = False,
) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if root is not None:
        from .coordinator import _open_anchored

        descriptor = _open_anchored(path, root, os.O_RDONLY, owner_control=owner_only)
    else:
        descriptor = os.open(path, flags)
    try:
        if owner_only:
            from .coordinator import _restrict_fd_to_owner

            _restrict_fd_to_owner(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
            raise BackupRestoreError("Migration backup input must be a regular file")
        if info.st_nlink != 1:
            raise BackupRestoreError("Migration backup files must not be hard-linked")
        if info.st_size > MAX_FILE_BYTES:
            raise BackupRestoreError("Migration backup file exceeds the configured limit")
        if expected_size is not None and info.st_size != expected_size:
            raise BackupRestoreError("Migration backup file size does not match its manifest")
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise BackupRestoreError("Migration backup file exceeds the configured limit")
                digest.update(chunk)
        return total, digest.hexdigest()
    finally:
        os.close(descriptor)


def _hash_descriptor(descriptor: int, expected_size: int | None = None) -> tuple[int, str, os.stat_result]:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BackupRestoreError("Migration backup input must be one non-hard-linked regular file")
    if info.st_size > MAX_FILE_BYTES or (expected_size is not None and info.st_size != expected_size):
        raise BackupRestoreError("Migration backup file size is invalid")
    digest = hashlib.sha256()
    total = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_BYTES:
            raise BackupRestoreError("Migration backup file exceeds the configured limit")
        digest.update(chunk)
    return total, digest.hexdigest(), info


def _fd_mount_id(descriptor: int) -> str | None:
    fdinfo = Path("/proc/self/fdinfo") / str(descriptor)
    if not fdinfo.exists():
        return None
    for line in fdinfo.read_text(encoding="ascii").splitlines():
        if line.startswith("mnt_id:"):
            return line.split(":", 1)[1].strip()
    raise BackupRestoreError("Unable to establish Linux mount identity")


def _walk_inventory_posix(
    root: Path,
    *,
    anchored_root: Path | None = None,
    exclude_infrastructure: bool = True,
) -> list[dict[str, object]]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    if anchored_root is None or root == anchored_root:
        expected_identity = _validate_root(root)
        root_fd = os.open(root, directory_flags)
        root_info = os.fstat(root_fd)
        if (root_info.st_dev, root_info.st_ino) != expected_identity:
            os.close(root_fd)
            raise BackupRestoreError("Configuration root changed during anchored traversal")
    else:
        from .coordinator import _open_anchored

        root_fd = _open_anchored(root, anchored_root, directory_flags)
    root_mount = _fd_mount_id(root_fd)
    entries: list[dict[str, object]] = []
    collision_keys: set[str] = set()
    total = 0

    def visit(directory_fd: int, relative_directory: PurePosixPath) -> None:
        nonlocal total
        with os.scandir(directory_fd) as iterator:
            names = sorted(entry.name for entry in iterator)
        for name in names:
            relative = relative_directory / name
            if exclude_infrastructure and not relative_directory.parts and is_migration_infrastructure(relative):
                continue
            normalized = _normalized_relative(relative.as_posix())
            collision_key = unicodedata.normalize("NFC", normalized.as_posix()).casefold()
            if collision_key in collision_keys:
                raise BackupRestoreError("Configuration contains normalized or case-colliding paths")
            collision_keys.add(collision_key)
            if len(collision_keys) > MAX_ENTRIES:
                raise BackupRestoreError("Migration backup exceeds the entry-count limit")
            initial = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(initial.st_mode):
                raise BackupRestoreError("Migration backup tree contains a link")
            if stat.S_ISDIR(initial.st_mode):
                child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                try:
                    current = os.fstat(child_fd)
                    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
                        initial.st_dev, initial.st_ino,
                    ):
                        raise BackupRestoreError("Configuration changed during anchored traversal")
                    if root_mount is not None and _fd_mount_id(child_fd) != root_mount:
                        raise BackupRestoreError("Nested mounts or bind mounts are unsupported")
                    entries.append({
                        "path": normalized.as_posix(), "type": "dir", "mode": stat.S_IMODE(current.st_mode),
                    })
                    visit(child_fd, normalized)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(initial.st_mode):
                child_fd = os.open(name, os.O_RDONLY | nofollow, dir_fd=directory_fd)
                try:
                    if root_mount is not None and _fd_mount_id(child_fd) != root_mount:
                        raise BackupRestoreError("Nested mounts or bind mounts are unsupported")
                    size, digest, current = _hash_descriptor(child_fd)
                    if (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino):
                        raise BackupRestoreError("Configuration changed during anchored traversal")
                finally:
                    os.close(child_fd)
                total += size
                if total > MAX_TOTAL_BYTES:
                    raise BackupRestoreError("Migration backup exceeds the total-size limit")
                entries.append({
                    "path": normalized.as_posix(), "type": "file", "mode": stat.S_IMODE(current.st_mode),
                    "size": size, "sha256": digest,
                })
            else:
                raise BackupRestoreError("Migration backup tree contains a non-regular special file")

    try:
        visit(root_fd, PurePosixPath())
        return entries
    finally:
        os.close(root_fd)


def _walk_inventory(root: Path) -> list[dict[str, object]]:
    expected_root_identity = _validate_root(root)
    if os.name == "posix":
        return _walk_inventory_posix(root)
    from .coordinator import _capture_root_identity

    expected_root_identity = _capture_root_identity(root)
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    total = 0

    def visit(directory: Path, relative_directory: PurePosixPath) -> None:
        from .coordinator import _handle_identity, _mutation_parent

        with _mutation_parent(directory / ".inventory-anchor", root) as (directory_anchor, _):
            if directory == root and _handle_identity(directory_anchor, windows_handle=True) != expected_root_identity:
                raise BackupRestoreError("Configuration root changed during anchored traversal")
            visit_held(directory, relative_directory)

    def visit_held(directory: Path, relative_directory: PurePosixPath) -> None:
        nonlocal total
        directory_info = directory.lstat()
        if (
            stat.S_ISLNK(directory_info.st_mode)
            or _is_reparse(directory_info)
            or not stat.S_ISDIR(directory_info.st_mode)
        ):
            raise BackupRestoreError("Migration backup tree contains a link, reparse point, or special file")
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            relative = relative_directory / child.name
            if relative_directory == PurePosixPath() and is_migration_infrastructure(relative):
                continue
            normalized = _normalized_relative(relative.as_posix())
            collision_key = unicodedata.normalize("NFC", normalized.as_posix()).casefold()
            if collision_key in seen:
                raise BackupRestoreError("Configuration contains duplicate or case-colliding normalized paths")
            seen.add(collision_key)
            if len(seen) > MAX_ENTRIES:
                raise BackupRestoreError("Migration backup exceeds the entry-count limit")
            info = child.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise BackupRestoreError("Migration backup tree contains a link or reparse point")
            if stat.S_ISDIR(info.st_mode):
                entries.append({"path": normalized.as_posix(), "type": "dir", "mode": stat.S_IMODE(info.st_mode)})
                visit(Path(child.path), normalized)
            elif stat.S_ISREG(info.st_mode):
                size, digest = _hash_file(Path(child.path), root=root)
                total += size
                if total > MAX_TOTAL_BYTES:
                    raise BackupRestoreError("Migration backup exceeds the total-size limit")
                entries.append({
                    "path": normalized.as_posix(), "type": "file", "mode": stat.S_IMODE(info.st_mode),
                    "size": size, "sha256": digest,
                })
            else:
                raise BackupRestoreError("Migration backup tree contains a non-regular special file")

    visit(root, PurePosixPath())
    return entries


def _aggregate(entries: Sequence[Mapping[str, object]]) -> dict[str, int]:
    files = sum(1 for entry in entries if entry["type"] == "file")
    directories = sum(1 for entry in entries if entry["type"] == "dir")
    total = sum(cast(int, entry.get("size", 0)) for entry in entries)
    return {"entries": len(entries), "files": files, "directories": directories, "total_size": total}


def _fsync_directory(path: Path, root: Path | None = None) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if root is not None and path != root:
        from .coordinator import _open_anchored

        descriptor = _open_anchored(path, root, flags)
    else:
        expected_identity = _validate_root(path)
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != expected_identity:
            os.close(descriptor)
            raise BackupRestoreError("Directory changed before durability synchronization")
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_mkdir(path: Path, root: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    # Imported lazily to avoid a module-import cycle; production calls occur
    # only after migration.coordinator has completed initialization.
    from .coordinator import _make_private_directory, _safe_directory

    existed = path.exists() or path.is_symlink()
    _safe_directory(path, root, create=True, private=True)
    if os.name == "posix" and existed:
        info = path.lstat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise BackupRestoreError("Existing migration private directory has unsafe provenance or permissions")
    else:
        _make_private_directory(path, root)


def _mkdir_restore_directory(path: Path, root: Path) -> None:
    from .coordinator import _safe_directory

    _safe_directory(path, root, create=True, private=False)


def _write_private_file(
    path: Path,
    source: Path | None = None,
    payload: bytes | None = None,
    *,
    root: Path | None = None,
    source_root: Path | None = None,
) -> None:
    if os.name == "nt":
        from .coordinator import _open_anchored, _restrict_fd_to_owner

        # Every private file is beneath the config root. Locate it without
        # resolving links so coordinator's anchored create enforces the root.
        cursor = path.parent
        while (
            cursor.parent != cursor
            and cursor.name != BACKUP_ROOT_NAME
            and not cursor.name.startswith(RESTORE_STAGE_PREFIX)
        ):
            cursor = cursor.parent
        inferred_root = cursor.parent if cursor.name == BACKUP_ROOT_NAME else cursor.parent
        root = root or inferred_root
        descriptor = _open_anchored(path, root, os.O_CREAT | os.O_EXCL | os.O_WRONLY, owner_control=True)
        _restrict_fd_to_owner(descriptor)
    else:
        if root is not None:
            from .coordinator import _open_anchored

            descriptor = _open_anchored(path, root, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            descriptor = os.open(
                path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600,
            )
    try:
        os.fchmod(descriptor, 0o600) if os.name == "posix" else None
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            if source is not None:
                if source_root is not None:
                    from .coordinator import _open_anchored

                    source_descriptor = _open_anchored(source, source_root, os.O_RDONLY)
                    if os.name == "posix":
                        root_descriptor = os.open(
                            source_root,
                            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                        )
                        try:
                            root_mount = _fd_mount_id(root_descriptor)
                        finally:
                            os.close(root_descriptor)
                        if root_mount is not None and _fd_mount_id(source_descriptor) != root_mount:
                            os.close(source_descriptor)
                            raise BackupRestoreError("Refusing to copy through a nested mount or bind mount")
                else:
                    source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    info = os.fstat(source_descriptor)
                    if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
                        raise BackupRestoreError("Migration backup source changed during copy")
                    with os.fdopen(source_descriptor, "rb", closefd=False) as input_file:
                        shutil.copyfileobj(input_file, output, 1024 * 1024)
                finally:
                    os.close(source_descriptor)
            else:
                output.write(payload or b"")
            output.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _manifest_payload(
    *, backup_id: str, migration_id: str, source_schema: str, target_schema: str,
    root_identity: tuple[int, ...], entries: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "backup_id": backup_id,
        "migration_id": migration_id,
        "source_schema": source_schema,
        "target_schema": target_schema,
        "created_at": _utc_now(),
        "root_identity": list(root_identity),
        "aggregate": _aggregate(entries),
        "entries": list(entries),
    }


def _inventory_signature(manifest: Mapping[str, object]) -> tuple[object, ...]:
    return (
        manifest.get("migration_id"), manifest.get("source_schema"), manifest.get("target_schema"),
        manifest.get("root_identity"), manifest.get("aggregate"), manifest.get("entries"),
    )


def _publish_directory(staging: Path, destination: Path, config_root: Path) -> None:
    try:
        _publish_directory_anchored(staging, destination, config_root)
    except BackupRestoreError:
        raise
    except (OSError, ValueError) as exc:
        raise BackupRestoreError("Anchored migration backup publication failed safely") from exc


def _publish_directory_anchored(staging: Path, destination: Path, config_root: Path) -> None:
    """Rename one private directory relative to a retained parent anchor."""
    from .coordinator import _mutation_parent

    with _mutation_parent(destination, config_root) as (parent_anchor, target):
        if os.name == "posix":
            try:
                _rename_directory_noreplace(parent_anchor, staging.name, cast(str, target))
            except OSError as exc:
                unsupported = {
                    errno.EINVAL, errno.ENOSYS,
                    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
                    getattr(errno, "ENOTSUP", errno.EINVAL),
                }
                if exc.errno not in unsupported:
                    raise
                raise BackupRestoreError(
                    "POSIX fallback publication requires a pre-reserved transaction envelope"
                ) from exc
            os.fsync(parent_anchor)
            return
        from ctypes import wintypes
        from .coordinator import _windows_api, _windows_open_directory, _windows_rename_handle

        staging_handle = _windows_open_directory(
            parent_anchor, staging.name, delete_control=True,
        )
        try:
            _windows_rename_handle(staging_handle, parent_anchor, destination.name, replace=False)
        finally:
            _windows_api()[0].CloseHandle(wintypes.HANDLE(staging_handle))


def _publish_posix_transaction(
    transaction: Path, staging: Path, destination: Path, config_root: Path,
) -> None:
    """Publish a completed staging tree from its pre-reserved private envelope."""
    from .coordinator import _mutation_parent, _open_anchored

    unsupported = {
        errno.EINVAL, errno.ENOSYS,
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        getattr(errno, "ENOTSUP", errno.EINVAL),
    }
    with _mutation_parent(destination, config_root) as (parent_anchor, target):
        transaction_anchor = _open_anchored(
            transaction, config_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            try:
                _rename_directory_noreplace(
                    parent_anchor, PUBLICATION_STAGING_NAME, cast(str, target),
                    source_anchor=transaction_anchor,
                )
            except OSError as exc:
                if exc.errno not in unsupported:
                    raise
                _publish_directory_reserved_fallback(
                    parent_anchor, transaction_anchor, transaction,
                    staging, destination, config_root,
                )
                return
            os.fsync(parent_anchor)
            _publication_transition("atomic_publish_durable")
            os.rmdir(transaction.name, dir_fd=parent_anchor)
            os.fsync(parent_anchor)
            _publication_transition("txn_removed")
        finally:
            os.close(transaction_anchor)


def _rename_directory_noreplace(
    parent_anchor: int, staging_name: str, target_name: str, *, source_anchor: int | None = None,
) -> None:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(
        source_anchor if source_anchor is not None else parent_anchor, os.fsencode(staging_name),
        parent_anchor, os.fsencode(target_name),
        1,  # RENAME_NOREPLACE
    ) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise BackupRestoreError("Migration backup destination already exists")
        raise OSError(error, "Unable to atomically publish migration backup")


def _before_fallback_publication_reservation(_destination: Path) -> None:
    """Deterministic test seam immediately before exclusive reservation."""


def _before_fallback_manifest_publish(_destination: Path) -> None:
    """Deterministic test seam after data move and before logical publication."""


def _read_private_publication_file(path: Path, config_root: Path, maximum: int) -> bytes:
    from .coordinator import _read_bytes

    return _read_bytes(path, config_root, maximum, owner_only=True)


def _publish_directory_reserved_fallback(
    parent_anchor: int, transaction_anchor: int, transaction: Path,
    staging: Path, destination: Path, config_root: Path,
) -> None:
    """Publish from a reserved transaction after its intent is durable."""
    manifest_payload = _read_private_publication_file(
        staging / MANIFEST_NAME, config_root, MAX_MANIFEST_BYTES,
    )
    generation = transaction.name[len(PUBLICATION_TXN_PREFIX):]
    intent = {
        "publication_version": 3,
        "backup_id": destination.name,
        "destination_name": destination.name,
        "staging_name": PUBLICATION_STAGING_NAME,
        "transaction_name": transaction.name,
        "root_identity": list(_validate_root(config_root)),
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "generation": generation,
    }
    payload = (json.dumps(intent, sort_keys=True) + "\n").encode("utf-8")
    temporary_name = ".intent-{}.tmp".format(generation)
    intent_fd = os.open(
        temporary_name,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=transaction_anchor,
    )
    try:
        midpoint = max(1, len(payload) // 2)
        first_half = memoryview(payload[:midpoint])
        while first_half:
            written = os.write(intent_fd, first_half)
            if written <= 0:
                raise OSError(errno.EIO, "Unable to write backup publication intent")
            first_half = first_half[written:]
        os.fsync(intent_fd)
        _publication_transition("intent_temp_partial")
        view = memoryview(payload[midpoint:])
        while view:
            written = os.write(intent_fd, view)
            if written <= 0:
                raise OSError(errno.EIO, "Unable to write backup publication intent")
            view = view[written:]
        os.fsync(intent_fd)
        _publication_transition("intent_temp_durable")
    finally:
        os.close(intent_fd)
    os.rename(
        temporary_name, PUBLICATION_INTENT_NAME,
        src_dir_fd=transaction_anchor, dst_dir_fd=transaction_anchor,
    )
    os.fsync(transaction_anchor)
    _publication_transition("intent_durable")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    staging_fd: int | None = None
    destination_fd: int | None = None
    moved: list[str] = []
    reserved = False
    manifest_committed = False
    try:
        _before_fallback_publication_reservation(destination)
        try:
            os.mkdir(destination.name, 0o700, dir_fd=parent_anchor)
        except FileExistsError as exc:
            raise BackupRestoreError("Migration backup destination already exists") from exc
        reserved = True
        os.fsync(parent_anchor)
        _publication_transition("reservation_durable")
        staging_fd = os.open(PUBLICATION_STAGING_NAME, directory_flags, dir_fd=transaction_anchor)
        destination_fd = os.open(destination.name, directory_flags, dir_fd=parent_anchor)
        with os.scandir(staging_fd) as iterator:
            names = sorted(entry.name for entry in iterator)
        if names != ["data", MANIFEST_NAME]:
            raise BackupRestoreError("Validated backup staging layout changed before publication")

        os.rename("data", "data", src_dir_fd=staging_fd, dst_dir_fd=destination_fd)
        moved.append("data")
        os.fsync(destination_fd)
        _publication_transition("data_durable")
        _before_fallback_manifest_publish(destination)
        os.rename(
            MANIFEST_NAME, MANIFEST_NAME,
            src_dir_fd=staging_fd, dst_dir_fd=destination_fd,
        )
        moved.append(MANIFEST_NAME)
        os.fsync(destination_fd)
        os.fsync(parent_anchor)
        manifest_committed = True
        _publication_transition("manifest_durable")
        os.rmdir(PUBLICATION_STAGING_NAME, dir_fd=transaction_anchor)
        os.fsync(transaction_anchor)
        _publication_transition("staging_removed")
        os.unlink(PUBLICATION_INTENT_NAME, dir_fd=transaction_anchor)
        os.fsync(transaction_anchor)
        _publication_transition("intent_removed")
        os.rmdir(transaction.name, dir_fd=parent_anchor)
        os.fsync(parent_anchor)
        _publication_transition("txn_removed")
    except Exception:
        if not manifest_committed and staging_fd is not None and destination_fd is not None:
            for name in reversed(moved):
                try:
                    os.rename(name, name, src_dir_fd=destination_fd, dst_dir_fd=staging_fd)
                except OSError:
                    pass
            try:
                os.fsync(staging_fd)
            except OSError:
                pass
        if not manifest_committed and reserved:
            try:
                os.rmdir(destination.name, dir_fd=parent_anchor)
            except OSError:
                pass
        if not manifest_committed:
            try:
                os.fsync(parent_anchor)
            except OSError:
                pass
        raise
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        if staging_fd is not None:
            os.close(staging_fd)


def _publication_transition(_name: str) -> None:
    """Deterministic hard-crash seam after a durable protocol transition."""


def _install_staged_file(staged: Path, target: Path, config_root: Path) -> None:
    try:
        _install_staged_file_anchored(staged, target, config_root)
    except BackupRestoreError:
        raise
    except (OSError, ValueError) as exc:
        raise BackupRestoreError("Anchored restore installation failed safely") from exc


def _install_staged_file_anchored(staged: Path, target: Path, config_root: Path) -> None:
    from .coordinator import _mutation_parent, _open_anchored, _windows_rename_fd

    if os.name == "posix":
        with _mutation_parent(staged, config_root) as (source_parent, source_name):
            with _mutation_parent(target, config_root) as (target_parent, target_name):
                os.rename(
                    source_name, target_name,
                    src_dir_fd=source_parent, dst_dir_fd=target_parent,
                )
                os.fsync(target_parent)
        return
    descriptor = _open_anchored(staged, config_root, os.O_RDONLY, delete_control=True)
    try:
        with _mutation_parent(target, config_root) as (target_parent, _target_path):
            _windows_rename_fd(descriptor, target_parent, target.name)
    finally:
        os.close(descriptor)


def _freeze_validated_staged_file(
    staged: Path, config_root: Path, expected_size: int, expected_digest: str,
) -> bytes:
    """Read and validate staging through one retained descriptor."""
    from .coordinator import _open_anchored

    with _restore_oserror_context("open staged restore file", staged, config_root):
        descriptor = _open_anchored(staged, config_root, os.O_RDONLY, owner_control=True)
    try:
        with _restore_oserror_context("validate staged restore file", staged, config_root):
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or _is_reparse(info) or info.st_nlink != 1:
                raise BackupRestoreError("Restore staging file provenance changed before installation")
            if info.st_size != expected_size or info.st_size > MAX_FILE_BYTES:
                raise BackupRestoreError("Restore staging file size changed before installation")
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise BackupRestoreError("Restore staging file exceeds the configured limit")
                chunks.append(chunk)
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (
                total != expected_size
                or digest.hexdigest() != expected_digest
                or (after.st_dev, after.st_ino, after.st_size) != (info.st_dev, info.st_ino, info.st_size)
            ):
                raise BackupRestoreError("Restore staging changed immediately before installation")
            return b"".join(chunks)
    finally:
        os.close(descriptor)


def _install_frozen_payload(
    payload: bytes, expected_digest: str, target: Path, config_root: Path,
) -> None:
    """Materialize frozen validated bytes and retain the file descriptor through install."""
    from .coordinator import (
        _mutation_parent,
        _open_anchored,
        _restrict_fd_to_owner,
        _windows_rename_fd,
    )

    temporary = target.with_name(".{}-restore-{}.tmp".format(target.name, uuid.uuid4().hex))
    descriptor: int | None = None
    installed = False
    try:
        with _restore_oserror_context("create private restore install object", target, config_root):
            descriptor = _open_anchored(
                temporary, config_root, os.O_CREAT | os.O_EXCL | os.O_RDWR,
                owner_control=os.name == "nt", delete_control=os.name == "nt",
            )
        with _restore_oserror_context("write and validate restore install object", target, config_root):
            _restrict_fd_to_owner(descriptor)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("Unable to write frozen restore payload")
                view = view[written:]
            os.fsync(descriptor)
            size, digest, descriptor_info = _hash_descriptor(descriptor, len(payload))
            if size != len(payload) or digest != expected_digest:
                raise BackupRestoreError("Frozen restore payload changed before installation")

        if os.name == "posix":
            with _mutation_parent(temporary, config_root) as (source_parent, source_name):
                named_info = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
                if (named_info.st_dev, named_info.st_ino) != (
                    descriptor_info.st_dev, descriptor_info.st_ino,
                ):
                    raise BackupRestoreError("Frozen restore install pathname changed")
                with _mutation_parent(target, config_root) as (target_parent, target_name):
                    rollback_name: str | None = None
                    target_info: os.stat_result | None = None
                    try:
                        target_info = os.stat(
                            target_name, dir_fd=target_parent, follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        pass
                    if target_info is not None:
                        rollback_name = ".{}-restore-{}.rollback".format(
                            target.name, uuid.uuid4().hex,
                        )
                        with _restore_oserror_context("retain live file for rollback", target, config_root):
                            os.rename(
                                target_name, rollback_name,
                                src_dir_fd=target_parent, dst_dir_fd=target_parent,
                            )
                    try:
                        _before_posix_install_rename(temporary, descriptor)
                        try:
                            _rename_posix_install(
                                cast(str, source_name), cast(str, target_name),
                                source_parent, target_parent,
                            )
                        except FileNotFoundError as exc:
                            published_matches = False
                            try:
                                published_info = _stat_posix_published_install(
                                    cast(str, target_name), target_parent,
                                )
                            except OSError:
                                published_info = None
                            if published_info is not None:
                                published_matches = (
                                    published_info.st_dev, published_info.st_ino,
                                ) == (descriptor_info.st_dev, descriptor_info.st_ino)
                            if not published_matches:
                                published_matches = _descriptor_reports_installed_target(
                                    descriptor, target,
                                )
                            if not published_matches:
                                raise _bounded_restore_publish_error(
                                    target, config_root, exc,
                                ) from exc
                        except OSError as exc:
                            raise _bounded_restore_publish_error(
                                target, config_root, exc,
                            ) from exc
                        with _restore_oserror_context("synchronize published restore file", target, config_root):
                            os.fsync(target_parent)
                        with _restore_oserror_context("verify published restore file", target, config_root):
                            _verify_published_restore_descriptor(
                                descriptor, target, descriptor_info,
                                len(payload), expected_digest,
                            )
                    except Exception:
                        with _restore_oserror_context("roll back failed restore file", target, config_root):
                            try:
                                os.unlink(target_name, dir_fd=target_parent)
                            except FileNotFoundError:
                                pass
                            if rollback_name is not None:
                                os.rename(
                                    rollback_name, target_name,
                                    src_dir_fd=target_parent, dst_dir_fd=target_parent,
                                )
                            os.fsync(target_parent)
                        raise
                    if rollback_name is not None:
                        with _restore_oserror_context("remove restore rollback", target, config_root):
                            os.unlink(rollback_name, dir_fd=target_parent)
                    with _restore_oserror_context("synchronize completed restore file", target, config_root):
                        os.fsync(target_parent)
        else:
            with _mutation_parent(target, config_root) as (target_parent, _target_path):
                _windows_rename_fd(descriptor, target_parent, target.name)
        installed = True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not installed and (temporary.exists() or temporary.is_symlink()):
            from .coordinator import _secure_unlink

            with _restore_oserror_context("remove failed restore install object", target, config_root):
                _secure_unlink(temporary, config_root)


def _before_posix_install_rename(_temporary: Path, _descriptor: int) -> None:
    """Deterministic test seam at the documented same-account trust boundary."""


def _rename_posix_install(
    source_name: str, target_name: str, source_parent: int, target_parent: int,
) -> None:
    os.rename(
        source_name, target_name,
        src_dir_fd=source_parent, dst_dir_fd=target_parent,
    )


def _stat_posix_published_install(target_name: str, target_parent: int) -> os.stat_result:
    return os.stat(target_name, dir_fd=target_parent, follow_symlinks=False)


def _descriptor_reports_installed_target(descriptor: int, target: Path) -> bool:
    """Reconcile bind filesystems that hide an open renamed file from lookup."""
    try:
        descriptor_target = os.readlink("/proc/self/fd/{}".format(descriptor))
    except OSError:
        return False
    return descriptor_target == os.path.abspath(target)


def _published_restore_descriptor_stat(descriptor: int) -> os.stat_result:
    return os.fstat(descriptor)


def _hash_retained_descriptor_bytes(descriptor: int, maximum_size: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > maximum_size:
            raise BackupRestoreError("Published restore file exceeds its validated size")
        digest.update(chunk)
    return total, digest.hexdigest()


def _verify_published_restore_descriptor(
    descriptor: int,
    target: Path,
    descriptor_info: os.stat_result,
    expected_size: int,
    expected_digest: str,
) -> None:
    try:
        published_info = _published_restore_descriptor_stat(descriptor)
    except FileNotFoundError:
        # Windows-backed Linux bind filesystems can keep the renamed descriptor
        # readable while transiently rejecting both fstat and pathname lookup.
        # Continue only when the kernel descriptor link proves the intended
        # publication target; content is still re-read below from that handle.
        if not _descriptor_reports_installed_target(descriptor, target):
            raise BackupRestoreError(
                "Published restore descriptor identity could not be proven"
            )
    else:
        if (
            not stat.S_ISREG(published_info.st_mode)
            or published_info.st_nlink != 1
            or (published_info.st_dev, published_info.st_ino) != (
                descriptor_info.st_dev, descriptor_info.st_ino,
            )
        ):
            raise BackupRestoreError("Published restore descriptor identity changed")

    installed_size, installed_digest = _hash_retained_descriptor_bytes(
        descriptor, expected_size,
    )
    if installed_size != expected_size or installed_digest != expected_digest:
        raise BackupRestoreError("Installed restore payload changed during final publication")


def _apply_restored_directory_mode(descriptor: int, desired_mode: int) -> None:
    unsupported = {
        errno.EPERM,
        errno.EROFS,
        getattr(errno, "EOPNOTSUPP", errno.EPERM),
    }
    try:
        os.fchmod(descriptor, desired_mode)
    except OSError as exc:
        if exc.errno not in unsupported:
            raise
        effective_mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
        if effective_mode == desired_mode:
            return
        raise BackupRestoreError(
            "Restored directory permissions do not match the retained backup"
        ) from exc
    effective_mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
    if effective_mode != desired_mode:
        raise BackupRestoreError(
            "Restored directory permissions do not match the retained backup"
        )


def _bounded_restore_publish_error(target: Path, config_root: Path, error: OSError) -> BackupRestoreError:
    relative = target.relative_to(config_root).as_posix()
    error_name = errno.errorcode.get(error.errno, "UNKNOWN")
    return BackupRestoreError(
        "Restore file publication failed for {!r} ({})".format(relative, error_name)
    )


def _install_validated_staged_file(
    staged: Path, target: Path, config_root: Path, expected_size: int, expected_digest: str,
) -> None:
    payload = _freeze_validated_staged_file(
        staged, config_root, expected_size, expected_digest,
    )
    _install_frozen_payload(payload, expected_digest, target, config_root)


def validate_backup(
    backup_dir: Path,
    config_root: Path,
    *,
    _expected_backup_id: str | None = None,
    _allow_staging: bool = False,
) -> dict[str, object]:
    """Treat a retained manifest and its data tree as untrusted input."""
    config_root = canonical_config_root(config_root)
    _validate_root(config_root)
    backup_root = config_root / BACKUP_ROOT_NAME
    try:
        backup_dir.absolute().relative_to(backup_root.absolute())
    except ValueError as exc:
        raise BackupRestoreError("Migration backup must be inside the anchored backup root") from exc
    if _allow_staging:
        transaction_staging = (
            backup_dir.name == PUBLICATION_STAGING_NAME
            and backup_dir.parent.parent == backup_root
            and re.fullmatch(
                r"{}[0-9a-f]{{32}}".format(re.escape(PUBLICATION_TXN_PREFIX)),
                backup_dir.parent.name,
            ) is not None
        )
        legacy_staging = (
            backup_dir.parent == backup_root
            and _expected_backup_id is not None
            and backup_dir.name == "." + _expected_backup_id + ".staging"
        )
        if _expected_backup_id is None or not (transaction_staging or legacy_staging):
            raise BackupRestoreError("Migration backup staging identity is invalid")
    elif backup_dir.parent != backup_root or backup_dir.name.startswith("."):
        raise BackupRestoreError("Migration backup path is ambiguous or not a published backup")
    backup_info = backup_dir.lstat()
    if stat.S_ISLNK(backup_info.st_mode) or _is_reparse(backup_info) or not stat.S_ISDIR(backup_info.st_mode):
        raise BackupRestoreError("Migration backup must be a real directory")
    allowed_top_level = ["data", MANIFEST_NAME]
    if _list_directory_names(backup_dir, config_root) != sorted(allowed_top_level):
        raise BackupRestoreError("Migration backup contains unexpected publication artifacts")
    if os.name == "posix":
        expected_owner = os.geteuid()
        for private_path in (backup_root, backup_dir, backup_dir / "data"):
            private_info = private_path.lstat()
            if private_info.st_uid != expected_owner or stat.S_IMODE(private_info.st_mode) & 0o077:
                raise BackupRestoreError("Migration backup provenance or private permissions are invalid")
    else:
        from .coordinator import _make_private_directory

        for private_path in (backup_root, backup_dir, backup_dir / "data"):
            _make_private_directory(private_path, config_root)
    manifest_path = backup_dir / MANIFEST_NAME
    manifest_info = manifest_path.lstat()
    if stat.S_ISLNK(manifest_info.st_mode) or _is_reparse(manifest_info) or not stat.S_ISREG(manifest_info.st_mode):
        raise BackupRestoreError("Migration backup manifest must be a regular file")
    if manifest_info.st_nlink != 1:
        raise BackupRestoreError("Migration backup manifest must not be hard-linked")
    if os.name == "posix" and (
        manifest_info.st_uid != os.geteuid() or stat.S_IMODE(manifest_info.st_mode) & 0o077
    ):
        raise BackupRestoreError("Migration backup manifest provenance or permissions are invalid")
    if manifest_info.st_size > MAX_MANIFEST_BYTES:
        raise BackupRestoreError("Migration backup manifest exceeds the size limit")
    from .coordinator import _read_bytes

    manifest = json.loads(
        _read_bytes(manifest_path, config_root, MAX_MANIFEST_BYTES, owner_only=True).decode("utf-8")
    )
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != MANIFEST_VERSION:
        raise BackupRestoreError("Migration backup manifest version is unsupported")
    expected_backup_id = _expected_backup_id or backup_dir.name
    if manifest.get("backup_id") != expected_backup_id:
        raise BackupRestoreError("Migration backup identity does not match its directory")
    for field in ("migration_id", "source_schema", "target_schema", "created_at"):
        if not isinstance(manifest.get(field), str) or not manifest[field] or len(cast(str, manifest[field])) > 256:
            raise BackupRestoreError("Migration backup manifest identity is invalid")
    try:
        created_at = datetime.fromisoformat(cast(str, manifest["created_at"]))
    except ValueError as exc:
        raise BackupRestoreError("Migration backup creation time is invalid") from exc
    if created_at.tzinfo is None:
        raise BackupRestoreError("Migration backup creation time must include a timezone")
    root_identity = manifest.get("root_identity")
    if (
        not isinstance(root_identity, list)
        or not root_identity
        or len(root_identity) > 4
        or not all(type(value) is int and cast(int, value) >= 0 for value in root_identity)
    ):
        raise BackupRestoreError("Migration backup root identity is invalid")
    if root_identity != list(_validate_root(config_root)):
        raise BackupRestoreError("Migration backup belongs to a different configuration root")
    entries_value = manifest.get("entries")
    if not isinstance(entries_value, list) or len(entries_value) > MAX_ENTRIES:
        raise BackupRestoreError("Migration backup manifest entry count is invalid")
    entries = cast(list[object], entries_value)
    seen: set[str] = set()
    normalized_entries: list[dict[str, object]] = []
    total = 0
    expected_data_paths: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise BackupRestoreError("Migration backup manifest entry is invalid")
        entry = cast(dict[str, object], raw)
        if set(entry) not in ({"path", "type", "mode"}, {"path", "type", "mode", "size", "sha256"}):
            raise BackupRestoreError("Migration backup manifest entry fields are invalid")
        relative = _normalized_relative(cast(str, entry.get("path")))
        key = unicodedata.normalize("NFC", relative.as_posix()).casefold()
        if key in seen:
            raise BackupRestoreError("Migration backup contains duplicate or case-colliding paths")
        seen.add(key)
        entry_type = entry.get("type")
        mode = _safe_mode(entry.get("mode"))
        target = backup_dir / "data" / Path(*relative.parts)
        info = target.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise BackupRestoreError("Migration backup data contains a link or reparse point")
        if os.name == "posix" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise BackupRestoreError("Migration backup data provenance or permissions are invalid")
        if entry_type == "dir":
            if set(entry) != {"path", "type", "mode"} or not stat.S_ISDIR(info.st_mode):
                raise BackupRestoreError("Migration backup directory type does not match its manifest")
            if os.name == "nt":
                from .coordinator import _make_private_directory

                _make_private_directory(target, config_root)
        elif entry_type == "file":
            size, digest = entry.get("size"), entry.get("sha256")
            if type(size) is not int or cast(int, size) < 0 or cast(int, size) > MAX_FILE_BYTES:
                raise BackupRestoreError("Migration backup file size is invalid")
            if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise BackupRestoreError("Migration backup file digest is invalid")
            actual_size, actual_digest = _hash_file(
                target, cast(int, size), root=config_root, owner_only=True,
            )
            if actual_digest != digest:
                raise BackupRestoreError("Migration backup file digest validation failed")
            total += actual_size
            if total > MAX_TOTAL_BYTES:
                raise BackupRestoreError("Migration backup exceeds the total-size limit")
        else:
            raise BackupRestoreError("Migration backup entry type is invalid")
        expected_data_paths.add(relative.as_posix())
        normalized_entries.append({**entry, "path": relative.as_posix(), "mode": mode})
    actual_entries = _walk_data_tree(backup_dir / "data", config_root, expected_data_paths)
    if actual_entries != expected_data_paths:
        raise BackupRestoreError("Migration backup data tree contains missing or unexpected paths")
    if manifest.get("aggregate") != _aggregate(normalized_entries):
        raise BackupRestoreError("Migration backup aggregate counts do not match its entries")
    return cast(dict[str, object], manifest)


def _walk_data_tree(
    data_root: Path,
    config_root: Path,
    expected: set[str] | None = None,
) -> set[str]:
    info = data_root.lstat()
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise BackupRestoreError("Migration backup data root is invalid")
    if os.name == "posix":
        raw_entries = _walk_inventory_posix(
            data_root, anchored_root=config_root, exclude_infrastructure=False,
        )
        found = {cast(str, entry["path"]) for entry in raw_entries}
        if expected is not None and not found.issubset(expected):
            raise BackupRestoreError("Migration backup data contains an unexpected path")
        return found

    found: set[str] = set()
    collision_keys: set[str] = set()
    total_size = 0
    def visit(base: Path) -> None:
        nonlocal total_size
        from .coordinator import _mutation_parent

        with _mutation_parent(base / ".data-validation-anchor", config_root):
            with os.scandir(base) as iterator:
                children = sorted((Path(entry.path) for entry in iterator), key=lambda path: path.name)
            for path in children:
                relative = path.relative_to(data_root).as_posix()
                normalized = _normalized_relative(relative)
                normalized_text = normalized.as_posix()
                collision_key = unicodedata.normalize("NFC", normalized_text).casefold()
                if collision_key in collision_keys:
                    raise BackupRestoreError("Migration backup data has normalized or case-colliding paths")
                collision_keys.add(collision_key)
                if len(collision_keys) > MAX_ENTRIES:
                    raise BackupRestoreError("Migration backup data exceeds the entry-count limit")
                if expected is not None and normalized_text not in expected:
                    raise BackupRestoreError("Migration backup data contains an unexpected path")
                item_info = path.lstat()
                if stat.S_ISLNK(item_info.st_mode) or _is_reparse(item_info):
                    raise BackupRestoreError("Migration backup data tree contains a link or reparse point")
                if not (stat.S_ISDIR(item_info.st_mode) or stat.S_ISREG(item_info.st_mode)):
                    raise BackupRestoreError("Migration backup data tree contains a special file")
                if stat.S_ISREG(item_info.st_mode):
                    if item_info.st_size > MAX_FILE_BYTES:
                        raise BackupRestoreError("Migration backup data file exceeds the size limit")
                    total_size += item_info.st_size
                    if total_size > MAX_TOTAL_BYTES:
                        raise BackupRestoreError("Migration backup data exceeds the total-size limit")
                found.add(normalized_text)
                if stat.S_ISDIR(item_info.st_mode):
                    visit(path)

    visit(data_root)
    return found


def _recover_reserved_publications(
    backup_root: Path, config_root: Path, migration_id: str,
) -> None:
    """Recover private transaction envelopes without claiming foreign visible paths."""
    from .coordinator import _open_anchored, _secure_rmdir, _secure_unlink

    transaction_pattern = re.compile(
        r"^{}([0-9a-f]{{32}})$".format(re.escape(PUBLICATION_TXN_PREFIX))
    )
    backup_pattern = re.compile(r"^{}-[0-9a-f]{{32}}$".format(re.escape(migration_id)))
    root_identity = list(_validate_root(config_root))
    for transaction_name in _list_directory_names(backup_root, config_root):
        match = transaction_pattern.fullmatch(transaction_name)
        if match is None:
            if transaction_name.startswith(PUBLICATION_TXN_PREFIX):
                raise BackupRestoreError("Backup publication transaction name is invalid")
            continue
        generation = match.group(1)
        transaction = backup_root / transaction_name
        transaction_info = transaction.lstat()
        if (
            stat.S_ISLNK(transaction_info.st_mode) or _is_reparse(transaction_info)
            or not stat.S_ISDIR(transaction_info.st_mode)
            or transaction_info.st_uid != os.geteuid()
            or stat.S_IMODE(transaction_info.st_mode) & 0o077
        ):
            raise BackupRestoreError("Backup publication transaction is not owner-private")
        transaction_names = _list_directory_names(transaction, config_root)
        temporary_name = ".intent-{}.tmp".format(generation)
        intent_path = transaction / PUBLICATION_INTENT_NAME
        if PUBLICATION_INTENT_NAME not in transaction_names:
            if not set(transaction_names).issubset({PUBLICATION_STAGING_NAME, temporary_name}):
                raise BackupRestoreError("Pre-intent publication transaction is malformed")
            if temporary_name in transaction_names:
                temporary_info = (transaction / temporary_name).lstat()
                if (
                    not stat.S_ISREG(temporary_info.st_mode) or temporary_info.st_nlink != 1
                    or temporary_info.st_uid != os.geteuid()
                    or stat.S_IMODE(temporary_info.st_mode) & 0o077
                    or temporary_info.st_size > 4096
                ):
                    raise BackupRestoreError("Publication intent temporary file is unsafe")
            if PUBLICATION_STAGING_NAME in transaction_names:
                staging = transaction / PUBLICATION_STAGING_NAME
                staging_info = staging.lstat()
                if (
                    stat.S_ISLNK(staging_info.st_mode) or _is_reparse(staging_info)
                    or not stat.S_ISDIR(staging_info.st_mode)
                    or staging_info.st_uid != os.geteuid()
                    or stat.S_IMODE(staging_info.st_mode) & 0o077
                ):
                    raise BackupRestoreError("Pre-intent publication staging is unsafe")
                manifest_path = staging / MANIFEST_NAME
                if manifest_path.exists() or manifest_path.is_symlink():
                    try:
                        manifest = json.loads(_read_private_publication_file(
                            manifest_path, config_root, MAX_MANIFEST_BYTES,
                        ).decode("utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError, BackupRestoreError):
                        manifest = None
                    if isinstance(manifest, dict):
                        candidate = manifest.get("backup_id")
                        if isinstance(candidate, str) and backup_pattern.fullmatch(candidate):
                            destination = backup_root / candidate
                            if destination.exists() or destination.is_symlink():
                                raise BackupRestoreError(
                                    "Pre-intent transaction conflicts with a visible backup path"
                                )
            _remove_path(transaction, config_root)
            _fsync_directory(backup_root, config_root)
            _publication_transition("recovery_txn_removed")
            continue

        intent_payload = _read_private_publication_file(intent_path, config_root, 4096)
        try:
            record = json.loads(intent_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupRestoreError("Backup publication intent is invalid") from exc
        if not isinstance(record, dict) or set(record) != {
            "publication_version", "backup_id", "destination_name", "staging_name",
            "transaction_name", "root_identity", "manifest_sha256", "generation",
        }:
            raise BackupRestoreError("Backup publication intent fields are invalid")
        candidate_name = record.get("backup_id")
        if (
            record.get("publication_version") != 3
            or not isinstance(candidate_name, str)
            or backup_pattern.fullmatch(cast(str, candidate_name)) is None
            or record.get("destination_name") != candidate_name
            or record.get("staging_name") != PUBLICATION_STAGING_NAME
            or record.get("transaction_name") != transaction_name
            or record.get("root_identity") != root_identity
            or not isinstance(record.get("manifest_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", cast(str, record.get("manifest_sha256")))
            or record.get("generation") != generation
        ):
            raise BackupRestoreError("Backup publication intent identity is invalid")

        destination = backup_root / cast(str, candidate_name)
        staging = transaction / PUBLICATION_STAGING_NAME
        destination_exists = destination.exists() or destination.is_symlink()
        staging_exists = staging.exists() or staging.is_symlink()
        for directory, exists in ((destination, destination_exists), (staging, staging_exists)):
            if not exists:
                continue
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise BackupRestoreError("Backup publication intent points to an unsafe directory")
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise BackupRestoreError("Backup publication intent directory is not owner-private")

        staging_names = _list_directory_names(staging, config_root) if staging_exists else []
        destination_names = _list_directory_names(destination, config_root) if destination_exists else []
        expected_transaction_names = {PUBLICATION_INTENT_NAME}
        if staging_exists:
            expected_transaction_names.add(PUBLICATION_STAGING_NAME)
        if set(transaction_names) != expected_transaction_names:
            raise BackupRestoreError("Backup publication transaction contains unexpected artifacts")
        if not staging_exists and not destination_exists:
            phase = "cleanup-complete"
            manifest_path = None
        elif staging_names == ["data", MANIFEST_NAME] and destination_names == []:
            phase = "reserved" if destination_exists else "intent-only"
            manifest_path = staging / MANIFEST_NAME
        elif staging_names == [MANIFEST_NAME] and destination_names == ["data"]:
            phase = "data-moved"
            manifest_path = staging / MANIFEST_NAME
        elif staging_names == [] and destination_names == ["data", MANIFEST_NAME]:
            phase = "committed"
            manifest_path = destination / MANIFEST_NAME
        else:
            raise BackupRestoreError("Backup publication intent state is structurally ambiguous")

        if manifest_path is not None:
            manifest_payload = _read_private_publication_file(
                manifest_path, config_root, MAX_MANIFEST_BYTES,
            )
            if hashlib.sha256(manifest_payload).hexdigest() != record["manifest_sha256"]:
                raise BackupRestoreError("Backup publication intent manifest digest does not match")
            try:
                manifest_identity = json.loads(manifest_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupRestoreError("Backup publication intent manifest is invalid") from exc
            if (
                not isinstance(manifest_identity, dict)
                or manifest_identity.get("backup_id") != candidate_name
                or manifest_identity.get("root_identity") != root_identity
            ):
                raise BackupRestoreError("Backup publication intent manifest identity does not match")

        if phase == "data-moved":
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            destination_fd = _open_anchored(destination, config_root, directory_flags)
            staging_fd = _open_anchored(staging, config_root, directory_flags)
            try:
                os.rename(
                    MANIFEST_NAME, MANIFEST_NAME,
                    src_dir_fd=staging_fd, dst_dir_fd=destination_fd,
                )
                os.fsync(destination_fd)
            finally:
                os.close(staging_fd)
                os.close(destination_fd)
            _fsync_directory(backup_root, config_root)
            _publication_transition("recovery_manifest_durable")
            phase = "committed"

        if phase == "committed":
            validate_backup(destination, config_root)
            _fsync_directory(destination, config_root)
            _fsync_directory(backup_root, config_root)
            if staging_exists:
                _secure_rmdir(staging, config_root)
                _fsync_directory(transaction, config_root)
                _publication_transition("recovery_staging_removed")
            _secure_unlink(intent_path, config_root)
            _fsync_directory(transaction, config_root)
            _publication_transition("recovery_intent_removed")
            _secure_rmdir(transaction, config_root)
            _fsync_directory(backup_root, config_root)
            _publication_transition("recovery_txn_removed")
            validate_backup(destination, config_root)
            continue

        if phase != "cleanup-complete":
            validate_backup(
                staging, config_root,
                _expected_backup_id=candidate_name, _allow_staging=True,
            )
            if destination_exists:
                _secure_rmdir(destination, config_root)
                _fsync_directory(backup_root, config_root)
                _publication_transition("recovery_reservation_removed")
            _remove_path(staging, config_root)
            _fsync_directory(transaction, config_root)
            _publication_transition("recovery_staging_removed")
        _secure_unlink(intent_path, config_root)
        _fsync_directory(transaction, config_root)
        _publication_transition("recovery_intent_removed")
        _secure_rmdir(transaction, config_root)
        _fsync_directory(backup_root, config_root)
        _publication_transition("recovery_txn_removed")


def create_retained_backup(
    config_root: Path, *, migration_id: str, source_schema: str, target_schema: str,
    mount_detector=None,
) -> Path:
    """Publish a private, unique full-tree backup after complete validation."""
    config_root = canonical_config_root(config_root)
    root_identity = _validate_root(config_root)
    _reject_nested_mounts(config_root, mount_detector)
    entries = _walk_inventory(config_root)
    backup_root = config_root / BACKUP_ROOT_NAME
    _private_mkdir(backup_root, config_root, exist_ok=True)
    if backup_root.is_symlink() or _is_reparse(backup_root.lstat()) or not backup_root.is_dir():
        raise BackupRestoreError("Migration backup root must be a real directory")
    if os.name == "posix":
        _recover_reserved_publications(backup_root, config_root, migration_id)

    proposed = _manifest_payload(
        backup_id="pending", migration_id=migration_id, source_schema=source_schema,
        target_schema=target_schema, root_identity=root_identity, entries=entries,
    )
    owned_staging_pattern = re.compile(
        r"^\.{}-[0-9a-f]{{32}}\.staging$".format(re.escape(migration_id))
    )
    for candidate_name in _list_directory_names(backup_root, config_root):
        candidate = backup_root / candidate_name
        if candidate.name.startswith("."):
            if owned_staging_pattern.fullmatch(candidate.name):
                if os.name == "posix":
                    raise BackupRestoreError(
                        "Root-level migration backup staging is ambiguous"
                    )
                info = candidate.lstat()
                if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                    raise BackupRestoreError("Owned migration backup staging path is unsafe")
                _remove_path(candidate, config_root)
            continue
        try:
            existing = validate_backup(candidate, config_root)
        except (OSError, ValueError, BackupRestoreError, json.JSONDecodeError) as exc:
            raise BackupRestoreError(
                "Conflicting published migration backup data requires operator inspection"
            ) from exc
        if _inventory_signature(existing) == _inventory_signature(proposed):
            return candidate

    usage = shutil.disk_usage(backup_root)
    required = cast(int, proposed["aggregate"]["total_size"]) + 4 * 1024 * 1024  # type: ignore[index]
    if usage.free < required:
        raise BackupRestoreError("Insufficient free space for the retained migration backup")

    backup_id = "{}-{}".format(migration_id, uuid.uuid4().hex)
    transaction: Path | None = None
    if os.name == "posix":
        transaction = backup_root / (PUBLICATION_TXN_PREFIX + uuid.uuid4().hex)
        _private_mkdir(transaction, config_root)
        _fsync_directory(backup_root, config_root)
        _publication_transition("txn_durable")
        staging = transaction / PUBLICATION_STAGING_NAME
    else:
        staging = backup_root / ("." + backup_id + ".staging")
    destination = backup_root / backup_id
    _private_mkdir(staging, config_root)
    try:
        data_root = staging / "data"
        _private_mkdir(data_root, config_root)
        for entry in entries:
            relative = _normalized_relative(cast(str, entry["path"]))
            source = config_root / Path(*relative.parts)
            target = data_root / Path(*relative.parts)
            if entry["type"] == "dir":
                _private_mkdir(target, config_root)
            else:
                _private_mkdir(target.parent, config_root, parents=True, exist_ok=True)
                _write_private_file(target, source=source, root=config_root, source_root=config_root)
                size, digest = _hash_file(target, cast(int, entry["size"]), root=config_root)
                if digest != entry["sha256"]:
                    raise BackupRestoreError("Migration source changed while the backup was copied")
            if target.parent != data_root:
                _fsync_directory(target.parent, config_root)
        staged_directories = [
            data_root / Path(*PurePosixPath(cast(str, entry["path"])).parts)
            for entry in entries if entry["type"] == "dir"
        ]
        for directory in sorted(staged_directories, key=lambda path: len(path.parts), reverse=True):
            _fsync_directory(directory, config_root)
        _fsync_directory(data_root, config_root)
        manifest = _manifest_payload(
            backup_id=backup_id, migration_id=migration_id, source_schema=source_schema,
            target_schema=target_schema, root_identity=root_identity, entries=entries,
        )
        serialized_manifest = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(serialized_manifest) > MAX_MANIFEST_BYTES:
            raise BackupRestoreError("Migration backup manifest exceeds the serialized size limit")
        _write_private_file(
            staging / MANIFEST_NAME,
            payload=serialized_manifest,
            root=config_root,
        )
        staged_manifest = validate_backup(
            staging, config_root,
            _expected_backup_id=backup_id,
            _allow_staging=True,
        )
        if staged_manifest != manifest:
            raise BackupRestoreError("Migration backup manifest failed exact staging validation")
        if _walk_inventory(config_root) != entries:
            raise BackupRestoreError("Configuration changed while its migration backup was being created")
        _reject_nested_mounts(config_root, mount_detector)
        _assert_root_identity(config_root, root_identity)
        _fsync_directory(staging, config_root)
        _assert_root_identity(config_root, root_identity)
        if transaction is not None:
            try:
                _publish_posix_transaction(transaction, staging, destination, config_root)
            except BackupRestoreError:
                raise
            except (OSError, ValueError) as exc:
                raise BackupRestoreError("Anchored migration backup publication failed safely") from exc
        else:
            _publish_directory(staging, destination, config_root)
        _fsync_directory(backup_root, config_root)
        validate_backup(destination, config_root)
        return destination
    except Exception:
        # Before publication intent exists, this invocation still owns its
        # unique staging path and can remove it safely. Once intent exists,
        # recovery alone owns every visible protocol state.
        if os.name == "posix" and transaction is not None:
            intent = transaction / PUBLICATION_INTENT_NAME
            if not (intent.exists() or intent.is_symlink()) and (
                transaction.exists() or transaction.is_symlink()
            ):
                _remove_path(transaction, config_root)
                _fsync_directory(backup_root, config_root)
        raise


def resolve_backup(config_root: Path, backup_reference: str) -> Path:
    config_root = canonical_config_root(config_root)
    _validate_root(config_root)
    if not backup_reference or backup_reference.strip() != backup_reference:
        raise BackupRestoreError("A single unambiguous migration backup id or path is required")
    backup_root = config_root / BACKUP_ROOT_NAME
    if not backup_root.exists():
        raise BackupRestoreError("Migration backup root does not exist")
    supplied = Path(backup_reference)
    candidate = supplied if supplied.is_absolute() else backup_root / supplied
    absolute = Path(os.path.abspath(candidate))
    try:
        absolute.relative_to(Path(os.path.abspath(backup_root)))
    except ValueError as exc:
        raise BackupRestoreError("Migration backup reference is outside the anchored backup root") from exc
    if absolute.parent != Path(os.path.abspath(backup_root)) or absolute.name.startswith("."):
        raise BackupRestoreError("Migration backup reference must name one published backup")
    validate_backup(absolute, config_root)
    return absolute


def _remove_path(path: Path, root: Path) -> None:
    """Remove a tree through retained parent anchors without following links."""
    from .coordinator import _mutation_parent, _secure_rmdir, _secure_unlink

    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow

        def remove_at(parent_fd: int, name: str, root_mount: str | None) -> None:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                os.unlink(name, dir_fd=parent_fd)
                return
            directory_fd = os.open(name, directory_flags, dir_fd=parent_fd)
            try:
                if root_mount is not None and _fd_mount_id(directory_fd) != root_mount:
                    raise BackupRestoreError("Refusing to delete a nested mount or bind mount")
                with os.scandir(directory_fd) as iterator:
                    names = sorted(entry.name for entry in iterator)
                for child_name in names:
                    remove_at(directory_fd, child_name, root_mount)
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            os.rmdir(name, dir_fd=parent_fd)

        with _mutation_parent(path, root) as (parent_fd, name):
            remove_at(parent_fd, cast(str, name), _fd_mount_id(parent_fd))
        return

    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        _secure_unlink(path, root)
        return
    if not stat.S_ISDIR(info.st_mode):
        _secure_unlink(path, root)
        return

    # Retain the directory itself without delete sharing on Windows, and a
    # dir_fd on POSIX, while enumerating and deleting descendants.
    with _mutation_parent(path / ".remove-anchor", root):
        with os.scandir(path) as iterator:
            children = sorted((Path(entry.path) for entry in iterator), key=lambda item: item.name)
        for child in children:
            _remove_path(child, root)
    _secure_rmdir(path, root)


def _walk_current_paths_anchored(root: Path) -> dict[str, Path]:
    """List destination entries without following links or reading file content."""
    from .coordinator import _mutation_parent

    found: dict[str, Path] = {}
    collision_keys: set[str] = set()

    def record(relative: PurePosixPath, path: Path) -> None:
        normalized = _normalized_relative(relative.as_posix())
        key = unicodedata.normalize("NFC", normalized.as_posix()).casefold()
        if key in collision_keys:
            raise BackupRestoreError("Destination contains normalized or case-colliding paths")
        collision_keys.add(key)
        if len(collision_keys) > MAX_ENTRIES:
            raise BackupRestoreError("Destination exceeds the restore entry-count limit")
        found[normalized.as_posix()] = path

    if os.name == "posix":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        expected_identity = _validate_root(root)
        root_fd = os.open(root, flags)
        root_info = os.fstat(root_fd)
        if (root_info.st_dev, root_info.st_ino) != expected_identity:
            os.close(root_fd)
            raise BackupRestoreError("Destination root changed during anchored traversal")
        root_mount = _fd_mount_id(root_fd)

        def visit_fd(directory_fd: int, relative_directory: PurePosixPath) -> None:
            with os.scandir(directory_fd) as iterator:
                names = sorted(entry.name for entry in iterator)
            for name in names:
                relative = relative_directory / name
                if not relative_directory.parts and is_migration_infrastructure(relative):
                    continue
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                path = root / Path(*relative.parts)
                record(relative, path)
                if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                    child_fd = os.open(name, flags, dir_fd=directory_fd)
                    try:
                        if root_mount is not None and _fd_mount_id(child_fd) != root_mount:
                            raise BackupRestoreError("Nested mounts or bind mounts are unsupported")
                        visit_fd(child_fd, relative)
                    finally:
                        os.close(child_fd)

        try:
            visit_fd(root_fd, PurePosixPath())
        finally:
            os.close(root_fd)
        return found

    def visit_path(directory: Path, relative_directory: PurePosixPath) -> None:
        with _mutation_parent(directory / ".destination-list-anchor", root):
            with os.scandir(directory) as iterator:
                children = sorted((Path(entry.path) for entry in iterator), key=lambda path: path.name)
            for path in children:
                relative = relative_directory / path.name
                if not relative_directory.parts and is_migration_infrastructure(relative):
                    continue
                info = path.lstat()
                record(relative, path)
                if stat.S_ISDIR(info.st_mode) and not _is_reparse(info):
                    visit_path(path, relative)

    visit_path(root, PurePosixPath())
    return found


def _recover_restore_artifacts(config_root: Path, selected_backup_id: str) -> None:
    from .coordinator import _read_json_object

    journal = config_root / RESTORE_JOURNAL_NAME
    journal_backup: str | None = None
    if journal.exists() or journal.is_symlink():
        try:
            value = _read_json_object(journal, config_root, 4096, owner_only=True)
        except Exception as exc:
            raise BackupRestoreError("Restore recovery journal is invalid or unauthenticated") from exc
        if (
            value.get("journal_version") != 1
            or value.get("phase") != "converging"
            or not isinstance(value.get("backup_id"), str)
        ):
            raise BackupRestoreError("Restore recovery journal is invalid or unauthenticated")
        journal_backup = cast(str, value["backup_id"])
        if journal_backup != selected_backup_id:
            raise BackupRestoreError(
                "A different interrupted restore must be resumed before selecting another backup"
            )

    stage_pattern = re.compile(r"^\.migration-restore-(.+-[0-9a-f]{32})\.staging$")
    for child_name in _list_root_names(config_root):
        child = config_root / child_name
        match = stage_pattern.fullmatch(child.name)
        if match is None:
            continue
        info = child.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise BackupRestoreError("Restore recovery staging ownership is invalid")
        if os.name == "posix" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise BackupRestoreError("Restore recovery staging provenance is invalid")
        staged_backup_id = match.group(1)
        if journal_backup is not None and staged_backup_id != journal_backup:
            raise BackupRestoreError("Ambiguous restore staging exists for multiple backups")
        published = config_root / BACKUP_ROOT_NAME / staged_backup_id
        validate_backup(published, config_root)
        _remove_path(child, config_root)

    if journal.exists():
        # The selected transaction will recreate the journal only after its new
        # staging tree is durable and fully validated.
        from .coordinator import _secure_unlink

        _secure_unlink(journal, config_root)


def restore_backup(config_root: Path, backup_dir: Path, *, mount_detector=None) -> dict[str, int]:
    """Restartably converge the exact config root to a validated old inventory."""
    canonical_root = canonical_config_root(config_root)
    with _restore_oserror_context("execute restore convergence", ".", canonical_root):
        return _restore_backup_convergence(
            canonical_root, backup_dir, mount_detector=mount_detector,
        )


def _restore_backup_convergence(
    config_root: Path, backup_dir: Path, *, mount_detector=None,
) -> dict[str, int]:
    config_root = canonical_config_root(config_root)
    root_identity = _validate_root(config_root)
    _reject_nested_mounts(config_root, mount_detector)
    with _restore_oserror_context("validate retained backup", backup_dir, config_root):
        manifest = validate_backup(backup_dir, config_root)
    entries = cast(list[dict[str, object]], manifest["entries"])
    backup_id = cast(str, manifest["backup_id"])
    aggregate = cast(dict[str, int], manifest["aggregate"])
    if shutil.disk_usage(config_root).free < aggregate["total_size"] + 4 * 1024 * 1024:
        raise BackupRestoreError("Insufficient free space for restore staging")
    stage = config_root / (RESTORE_STAGE_PREFIX + backup_id + RESTORE_STAGE_SUFFIX)
    journal = config_root / RESTORE_JOURNAL_NAME

    _assert_root_identity(config_root, root_identity)
    with _restore_oserror_context("recover interrupted staging", RESTORE_JOURNAL_NAME, config_root):
        _recover_restore_artifacts(config_root, backup_id)
    with _restore_oserror_context("create restore staging", stage, config_root):
        _private_mkdir(stage, config_root)
    data_root = stage / "data"
    with _restore_oserror_context("create restore data staging", data_root, config_root):
        _private_mkdir(data_root, config_root)
    for entry in entries:
        relative = _normalized_relative(cast(str, entry["path"]))
        source = backup_dir / "data" / Path(*relative.parts)
        target = data_root / Path(*relative.parts)
        with _restore_oserror_context("stage retained backup entry", relative, config_root):
            if entry["type"] == "dir":
                _private_mkdir(target, config_root, parents=True, exist_ok=True)
            else:
                _private_mkdir(target.parent, config_root, parents=True, exist_ok=True)
                _write_private_file(target, source=source, root=config_root, source_root=config_root)
                size, digest = _hash_file(target, cast(int, entry["size"]), root=config_root)
                if digest != entry["sha256"]:
                    raise BackupRestoreError("Restore staging failed validation")
    staged_directories = [
        data_root / Path(*PurePosixPath(cast(str, entry["path"])).parts)
        for entry in entries if entry["type"] == "dir"
    ]
    for directory in sorted(staged_directories, key=lambda path: len(path.parts), reverse=True):
        with _restore_oserror_context("synchronize staged directory", directory, config_root):
            _fsync_directory(directory, config_root)
    with _restore_oserror_context("synchronize staged data", data_root, config_root):
        _fsync_directory(data_root, config_root)
    with _restore_oserror_context("synchronize restore staging", stage, config_root):
        _fsync_directory(stage, config_root)
    # The published backup remains untrusted throughout staging. Detect a
    # same-owner mutation before creating the durable convergence journal or
    # changing any live configuration entry.
    with _restore_oserror_context("revalidate retained backup", backup_dir, config_root):
        validate_backup(backup_dir, config_root)
    with _restore_oserror_context("write convergence journal", journal, config_root):
        _write_json_atomic(
            journal,
            {"journal_version": 1, "backup_id": backup_id, "phase": "converging"},
            config_root,
        )

    _reject_nested_mounts(config_root, mount_detector)
    _assert_root_identity(config_root, root_identity)
    desired = {cast(str, entry["path"]): entry for entry in entries}
    with _restore_oserror_context("inventory live configuration", ".", config_root):
        current = _walk_current_paths_anchored(config_root)
    extras = [path for relative, path in current.items() if relative not in desired]
    for path in sorted(extras, key=lambda item: len(item.parts), reverse=True):
        if path.exists() or path.is_symlink():
            _assert_root_identity(config_root, root_identity)
            with _restore_oserror_context("remove post-backup entry", path, config_root):
                _remove_path(path, config_root)

    directories = [entry for entry in entries if entry["type"] == "dir"]
    for entry in sorted(directories, key=lambda item: len(PurePosixPath(cast(str, item["path"])).parts)):
        relative = _normalized_relative(cast(str, entry["path"]))
        target = config_root / Path(*relative.parts)
        with _restore_oserror_context("prepare restored directory", relative, config_root):
            if target.exists() or target.is_symlink():
                target_info = target.lstat()
                if not stat.S_ISDIR(target_info.st_mode) or _is_reparse(target_info):
                    _assert_root_identity(config_root, root_identity)
                    _remove_path(target, config_root)
            _mkdir_restore_directory(target, config_root)

    for entry in (item for item in entries if item["type"] == "file"):
        relative = _normalized_relative(cast(str, entry["path"]))
        target = config_root / Path(*relative.parts)
        with _restore_oserror_context("install restored file", relative, config_root):
            _mkdir_restore_directory(target.parent, config_root)
            if target.exists() or target.is_symlink():
                target_info = target.lstat()
                if stat.S_ISDIR(target_info.st_mode) or _is_reparse(target_info):
                    _assert_root_identity(config_root, root_identity)
                    _remove_path(target, config_root)
            staged = data_root / Path(*relative.parts)
            _assert_root_identity(config_root, root_identity)
            _install_validated_staged_file(
                staged, target, config_root,
                cast(int, entry["size"]), cast(str, entry["sha256"]),
            )
            if os.name == "posix":
                from .coordinator import _open_anchored

                with _restore_oserror_context("apply restored file mode", relative, config_root):
                    target_fd = _open_anchored(target, config_root, os.O_RDONLY)
                    try:
                        os.fchmod(target_fd, cast(int, entry["mode"]))
                        os.fsync(target_fd)
                    finally:
                        os.close(target_fd)
            with _restore_oserror_context("synchronize restored file parent", relative, config_root):
                _fsync_directory(target.parent, config_root)

    # Apply restrictive directory modes only after all descendants have been
    # installed, deepest first, so a faithful read-only directory cannot block
    # restoration of its own children.
    if os.name == "posix":
        for entry in sorted(
            directories,
            key=lambda item: len(PurePosixPath(cast(str, item["path"])).parts),
            reverse=True,
        ):
            relative = _normalized_relative(cast(str, entry["path"]))
            from .coordinator import _open_anchored

            with _restore_oserror_context("apply restored directory mode", relative, config_root):
                directory_fd = _open_anchored(
                    config_root / Path(*relative.parts), config_root,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    _apply_restored_directory_mode(
                        directory_fd, cast(int, entry["mode"]),
                    )
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)

    # New migration receipts and coordinator temporaries are infrastructure,
    # not old-state inventory. Remove them only after restored data is durable.
    for child_name in _list_root_names(config_root):
        child = config_root / child_name
        relative = PurePosixPath(child.name)
        if child.name in {"migration-state.json", RESTORE_JOURNAL_NAME} or (
            is_migration_infrastructure(relative)
            and child.name not in {BACKUP_ROOT_NAME, ".migration.lock", ".seedsync.runtime.lock"}
        ):
            if child.exists() or child.is_symlink():
                _assert_root_identity(config_root, root_identity)
                with _restore_oserror_context("remove migration infrastructure", child, config_root):
                    _remove_path(child, config_root)

    _assert_root_identity(config_root, root_identity)
    with _restore_oserror_context("verify final restored inventory", ".", config_root):
        actual = _walk_inventory(config_root)
    if actual != entries:
        raise BackupRestoreError("Restored configuration tree failed final inventory validation")
    if stage.exists():
        _assert_root_identity(config_root, root_identity)
        with _restore_oserror_context("remove completed restore staging", stage, config_root):
            _remove_path(stage, config_root)
    if journal.exists():
        from .coordinator import _secure_unlink

        with _restore_oserror_context("remove convergence journal", journal, config_root):
            _secure_unlink(journal, config_root)
    with _restore_oserror_context("synchronize restored configuration", ".", config_root):
        _fsync_directory(config_root, config_root)
    return dict(aggregate)


def _write_json_atomic(path: Path, payload: Mapping[str, object], config_root: Path) -> None:
    temporary = path.with_name(".{}-{}.tmp".format(path.name, uuid.uuid4().hex))
    _write_private_file(
        temporary,
        payload=(json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
        root=config_root,
    )
    _install_staged_file(temporary, path, config_root)
    _fsync_directory(path.parent, config_root)
