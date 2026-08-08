# Copyright 2026, SeedSync Contributors, All rights reserved.

import binascii
import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import uuid
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, TypeGuard, TypedDict

from common import Persist, PersistError


_ALLOWED_SCOPES = {"read", "write", "stream", "admin", "bootstrap"}
_HASH_ALGORITHM = "pbkdf2_sha256"
_HASH_ITERATIONS = 200000
_HASH_SALT_BYTES = 16
_UI_SESSION_TTL = timedelta(hours=12)
_REMEMBERED_UI_SESSION_COOKIE_MAX_AGE = timedelta(days=3650)
_BOOTSTRAP_PROOF_TTL = timedelta(minutes=10)
_BOOTSTRAP_EXCHANGE_TTL = timedelta(minutes=5)
_INITIAL_BROWSER_HANDOVER_TTL = timedelta(hours=8)
_RECOVERY_BROWSER_HANDOVER_TTL = timedelta(hours=2)
_BROWSER_HANDOVER_STATE_MAX_BYTES = 16 * 1024
_BROWSER_HANDOVER_SEEN_RECOVERY_VERSION_LIMIT = 128
_COMPLETED_MIGRATION_AUTH_MAX_BYTES = 64 * 1024
_COMPLETED_MIGRATION_HISTORY_MAX_BYTES = 1024 * 1024
_COMPLETED_MIGRATION_STORE_NAME = "api-keys.json"
_COMPLETED_MIGRATION_HISTORY_NAME = "api-keys.history.jsonl"
_COMPLETED_MIGRATION_CLAIM_MARKER_NAME = "migration-claimed-auth.json"
_COMPLETED_MIGRATION_CLAIM_MARKER_MAX_BYTES = 4096
_COMPLETED_MIGRATION_CLAIM_JOURNAL_NAME = ".migration-claim-auth.journal.json"
# The journal contains one base64 copy of each auth artifact.  Twice their
# maximum raw size covers base64's 4/3 expansion plus the bounded JSON fields.
_COMPLETED_MIGRATION_CLAIM_JOURNAL_MAX_BYTES = (
    2 * (_COMPLETED_MIGRATION_AUTH_MAX_BYTES * 2 + _COMPLETED_MIGRATION_HISTORY_MAX_BYTES) + 4096
)
_COMPLETED_MIGRATION_BACKUP_NAME = re.compile(
    r"api-keys-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}\.json"
)


class CreatedApiKey(TypedDict):
    record: "ApiKeyRecord"
    secret: str


def _is_string_object_dict(value: object) -> TypeGuard[Dict[str, object]]:
    return isinstance(value, dict)


def _is_object_list(value: object) -> TypeGuard[List[object]]:
    return isinstance(value, list)


def _is_scope_collection(
    value: object,
) -> TypeGuard[List[object] | tuple[object, ...] | set[object]]:
    return isinstance(value, (list, tuple, set))


def _required_string_field(record: Dict[str, object], field_name: str) -> str:
    value = record[field_name]
    if not isinstance(value, str):
        raise ValueError("{} must be a string".format(field_name))
    return value


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _current_time() -> datetime:
    """Indirection keeps browser-handover boundary tests deterministic."""
    return datetime.now(timezone.utc)


def _history_file_path(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    history_root, _ = os.path.splitext(file_path)
    return "{}.history.jsonl".format(history_root)


def append_api_key_store_history(
    file_path: Optional[str], event: str, reason: str, *, required: bool = False, **details: object
) -> None:
    history_path = _history_file_path(file_path)
    if history_path is None or file_path is None:
        return

    payload: Dict[str, object] = {
        "timestamp": _utc_now_iso(),
        "event": event,
        "reason": reason,
        "store_file": os.path.basename(file_path),
    }
    if details:
        payload["details"] = details

    try:
        history_dir = os.path.dirname(history_path)
        if history_dir:
            os.makedirs(history_dir, exist_ok=True)
        with open(history_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")
            if required:
                handle.flush()
                os.fsync(handle.fileno())
    except (OSError, TypeError, ValueError) as exc:
        if required:
            raise ValueError("Required completed migration claim history could not be persisted") from exc
        return


def _completed_migration_auth_error() -> ValueError:
    # Deliberately do not include parsed content in this error: auth metadata can
    # contain identifiers and must never turn a migration failure into disclosure.
    return ValueError("Completed migration auth state is not an allowed pre-claim bootstrap state")


def _strict_json_object(pairs: list[tuple[object, object]]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise _completed_migration_auth_error()
        result[key] = value
    return result


def _valid_completed_migration_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() == timedelta(0)
        and value == parsed.isoformat(timespec="seconds")
    )


def _read_completed_migration_auth_file(
    root: Path, name: str, *, private: bool, max_bytes: int = _COMPLETED_MIGRATION_AUTH_MAX_BYTES,
) -> bytes | None:
    path = root / name
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("Completed migration auth file limit is invalid")
    if not os.path.lexists(path):
        return None
    try:
        path_info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(path_info.st_mode) or path_info.st_nlink != 1:
            raise _completed_migration_auth_error()
        if os.name == "posix":
            mode = stat.S_IMODE(path_info.st_mode)
            if private:
                if mode != 0o600:
                    raise _completed_migration_auth_error()
            elif mode & 0o022:
                raise _completed_migration_auth_error()
            if path_info.st_uid != os.geteuid() or path_info.st_gid != os.getegid():
                raise _completed_migration_auth_error()

        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        try:
            opened_info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_info.st_mode)
                or opened_info.st_nlink != 1
                or (opened_info.st_dev, opened_info.st_ino) != (path_info.st_dev, path_info.st_ino)
                or opened_info.st_size > max_bytes
            ):
                raise _completed_migration_auth_error()
            payload = os.read(descriptor, max_bytes + 1)
            if len(payload) > max_bytes:
                raise _completed_migration_auth_error()
            return payload
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise _completed_migration_auth_error() from exc


def _completed_migration_empty_store(payload: bytes) -> bool:
    try:
        decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_json_object)
    except (UnicodeDecodeError, ValueError, TypeError):
        return False
    return decoded == {
        "version": 3,
        "api_keys": [],
        "ui_sessions": [],
        "browser_handover_claimed_version": "",
    }


def _completed_migration_history_entry(payload: object, kind: str) -> bool:
    if not _is_string_object_dict(payload):
        return False
    expected_details: Dict[str, object]
    if kind == "proof":
        expected_details = {"expires_at": None}
        expected_event, expected_reason = "bootstrap_proof_created", "first_run_bootstrap_window_opened"
    elif kind == "expired_proof":
        expected_details = {}
        expected_event, expected_reason = "bootstrap_proof_cleared", "expired"
    elif kind == "loaded":
        expected_details = {
            "api_key_count": 0,
            "active_api_key_count": 0,
            "ui_session_count": 0,
            "remembered_ui_session_count": 0,
            "browser_handover_claimed_version": "",
            "bootstrap_proof_present": False,
            "bootstrap_exchange_present": False,
        }
        expected_event, expected_reason = "store_loaded", "loaded_existing_store"
    elif kind in ("saved_with_proof", "saved_without_proof"):
        expected_details = {
            "api_key_count": 0,
            "active_api_key_count": 0,
            "ui_session_count": 0,
            "remembered_ui_session_count": 0,
            "browser_handover_claimed_version": "",
            "bootstrap_proof_present": kind == "saved_with_proof",
            "bootstrap_exchange_present": False,
        }
        expected_event, expected_reason = "store_saved", "persisted"
    else:
        return False

    expected_keys = {"timestamp", "event", "reason", "store_file"}
    if expected_details:
        expected_keys.add("details")
    if set(payload) != expected_keys:
        return False
    if (
        payload.get("event") != expected_event
        or payload.get("reason") != expected_reason
        or payload.get("store_file") != _COMPLETED_MIGRATION_STORE_NAME
        or not _valid_completed_migration_timestamp(payload.get("timestamp"))
    ):
        return False
    if not expected_details:
        return True
    details = payload.get("details")
    if not _is_string_object_dict(details) or set(details) != set(expected_details):
        return False
    if kind == "proof":
        return _valid_completed_migration_timestamp(details.get("expires_at"))
    return details == expected_details


def _completed_migration_history_is_safe(payload: bytes, store_present: bool) -> bool:
    try:
        text = payload.decode("utf-8")
        if not text.endswith("\n"):
            return False
        entries = [json.loads(line, object_pairs_hook=_strict_json_object) for line in text.splitlines()]
    except (UnicodeDecodeError, ValueError, TypeError):
        return False
    if not entries:
        return False
    if not store_present:
        return len(entries) == 1 and _completed_migration_history_entry(entries[0], "proof")

    proof_present = False
    proof_issued = False
    store_saved = False
    for entry in entries:
        if _completed_migration_history_entry(entry, "proof"):
            # More than one runtime can open the same still-unclaimed browser
            # handover before the first empty store is persisted.  Both proofs
            # remain ephemeral, so repeated creation is still pre-claim state.
            proof_present = True
            proof_issued = True
        elif _completed_migration_history_entry(entry, "expired_proof"):
            if not proof_present:
                return False
            proof_present = False
        elif _completed_migration_history_entry(entry, "loaded"):
            if not store_saved:
                return False
            # Browser proofs are intentionally absent from the durable store.
            proof_present = False
        elif _completed_migration_history_entry(entry, "saved_with_proof"):
            if not proof_present:
                return False
            store_saved = True
        elif _completed_migration_history_entry(entry, "saved_without_proof"):
            if proof_present or not proof_issued:
                return False
            store_saved = True
        else:
            return False
    return store_saved


def validate_completed_migration_preclaim_auth_state(config_dir: str | Path) -> None:
    """Accept only the empty, pre-claim auth residue normal startup may create.

    Migration application remains stricter: this is solely for validating a
    receipt that was already completed before normal startup opened first-run
    browser handover.  The accepted history is a closed grammar of events
    emitted by ``ensure_bootstrap_proof`` and empty-store persistence.
    """
    root = Path(config_dir)
    store = _read_completed_migration_auth_file(root, _COMPLETED_MIGRATION_STORE_NAME, private=True)
    history = _read_completed_migration_auth_file(
        root,
        _COMPLETED_MIGRATION_HISTORY_NAME,
        private=False,
        max_bytes=_COMPLETED_MIGRATION_HISTORY_MAX_BYTES,
    )
    if store is None and history is None:
        return
    if history is None or not _completed_migration_history_is_safe(history, store is not None):
        raise _completed_migration_auth_error()
    if store is not None and not _completed_migration_empty_store(store):
        raise _completed_migration_auth_error()

    allowed = {_COMPLETED_MIGRATION_STORE_NAME, _COMPLETED_MIGRATION_HISTORY_NAME}
    for candidate in root.iterdir():
        if candidate.name in allowed or not candidate.name.startswith("api-keys"):
            continue
        if not _COMPLETED_MIGRATION_BACKUP_NAME.fullmatch(candidate.name):
            raise _completed_migration_auth_error()
        backup = _read_completed_migration_auth_file(root, candidate.name, private=True)
        if backup is None or not _completed_migration_empty_store(backup):
            raise _completed_migration_auth_error()


def _completed_migration_claim_error() -> ValueError:
    return ValueError("Completed migration claimed auth state is invalid")


def _completed_migration_transition_binding(binding: object) -> Dict[str, str]:
    if not isinstance(binding, dict) or set(binding) != {
        "migration_id", "backup", "receipt_sha256", "backup_manifest_sha256",
    }:
        raise _completed_migration_claim_error()
    normalized: Dict[str, str] = {}
    for key in ("migration_id", "backup", "receipt_sha256", "backup_manifest_sha256"):
        value = binding.get(key)
        if not isinstance(value, str) or not value:
            raise _completed_migration_claim_error()
        normalized[key] = value
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,159}", normalized["migration_id"])
        or not re.fullmatch(r"migration-backups/[A-Za-z0-9._-]{1,160}", normalized["backup"])
        or any(not re.fullmatch(r"[0-9a-f]{64}", normalized[key]) for key in ("receipt_sha256", "backup_manifest_sha256"))
    ):
        raise _completed_migration_claim_error()
    return normalized


def _migration_claim_browser_handover_version(binding: Mapping[str, str]) -> str:
    """Return a bounded, non-secret, receipt-bound browser claim identifier."""
    canonical = "\n".join(
        "{}={}".format(key, binding[key])
        for key in ("migration_id", "backup", "receipt_sha256", "backup_manifest_sha256")
    )
    return "migration-claim-{}".format(hashlib.sha256(canonical.encode("utf-8")).hexdigest())


def _strict_completed_migration_json(payload: bytes) -> Dict[str, object]:
    try:
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_json_object)
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise _completed_migration_claim_error() from error
    if not isinstance(parsed, dict):
        raise _completed_migration_claim_error()
    return parsed


def _write_completed_migration_claim_marker(root: Path, payload: Dict[str, object]) -> None:
    path = root / _COMPLETED_MIGRATION_CLAIM_MARKER_NAME
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > _COMPLETED_MIGRATION_CLAIM_MARKER_MAX_BYTES:
        raise _completed_migration_claim_error()
    temporary = root / ".migration-claimed-auth.tmp-{}".format(uuid.uuid4().hex)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.write(descriptor, encoded)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
            directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as error:
        raise _completed_migration_claim_error() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def completed_migration_claim_marker_exists(config_dir: str | Path) -> bool:
    return os.path.lexists(Path(config_dir) / _COMPLETED_MIGRATION_CLAIM_MARKER_NAME)


def _write_completed_migration_private_file(root: Path, name: str, content: bytes, mode: int = 0o600) -> None:
    temporary = root / ".{}.tmp-{}".format(name, uuid.uuid4().hex)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            mode,
        )
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, root / name)
        if os.name == "posix":
            os.chmod(root / name, mode)
        if os.name == "posix":
            directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _claim_journal_artifact(root: Path, name: str) -> Dict[str, object]:
    path = root / name
    if not os.path.lexists(path):
        return {"present": False}
    before = path.lstat()
    private = name != _COMPLETED_MIGRATION_HISTORY_NAME
    max_bytes = (
        _COMPLETED_MIGRATION_HISTORY_MAX_BYTES
        if name == _COMPLETED_MIGRATION_HISTORY_NAME
        else _COMPLETED_MIGRATION_AUTH_MAX_BYTES
    )
    content = _read_completed_migration_auth_file(
        root, name, private=private, max_bytes=max_bytes,
    )
    after = path.lstat()
    if (
        content is None
        or path.is_symlink()
        or not stat.S_ISREG(after.st_mode)
        or (before.st_dev, before.st_ino, before.st_nlink) != (after.st_dev, after.st_ino, after.st_nlink)
    ):
        raise ValueError("Completed migration claim transaction file is unsafe")
    return {
        "present": True,
        "mode": stat.S_IMODE(after.st_mode),
        "content": base64.b64encode(content).decode("ascii"),
    }


def begin_completed_migration_claim_journal(config_dir: str | Path) -> None:
    root = Path(config_dir)
    journal_path = root / _COMPLETED_MIGRATION_CLAIM_JOURNAL_NAME
    if os.path.lexists(journal_path):
        raise ValueError("Completed migration claim transaction recovery is required")
    artifacts = {
        name: _claim_journal_artifact(root, name)
        for name in (
            _COMPLETED_MIGRATION_STORE_NAME,
            _COMPLETED_MIGRATION_HISTORY_NAME,
            _COMPLETED_MIGRATION_CLAIM_MARKER_NAME,
        )
    }
    payload = json.dumps({"schema": 1, "phase": "prepared", "artifacts": artifacts}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > _COMPLETED_MIGRATION_CLAIM_JOURNAL_MAX_BYTES:
        raise ValueError("Completed migration claim transaction is too large")
    _write_completed_migration_private_file(root, _COMPLETED_MIGRATION_CLAIM_JOURNAL_NAME, payload)


def commit_completed_migration_claim_journal(config_dir: str | Path) -> None:
    root = Path(config_dir)
    payload = _read_completed_migration_auth_file(
        root, _COMPLETED_MIGRATION_CLAIM_JOURNAL_NAME, private=True,
        max_bytes=_COMPLETED_MIGRATION_CLAIM_JOURNAL_MAX_BYTES,
    )
    if payload is None:
        raise ValueError("Completed migration claim transaction is unavailable")
    parsed = _strict_completed_migration_json(payload)
    if parsed.get("schema") != 1 or parsed.get("phase") != "prepared" or not isinstance(parsed.get("artifacts"), dict):
        raise ValueError("Completed migration claim transaction journal is invalid")
    parsed["phase"] = "committed"
    _write_completed_migration_private_file(
        root,
        _COMPLETED_MIGRATION_CLAIM_JOURNAL_NAME,
        json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )


def recover_completed_migration_claim_journal(config_dir: str | Path) -> bool:
    """Restore preclaim auth files after an interrupted completed-migration claim."""
    root = Path(config_dir)
    journal_path = root / _COMPLETED_MIGRATION_CLAIM_JOURNAL_NAME
    payload = _read_completed_migration_auth_file(
        root,
        _COMPLETED_MIGRATION_CLAIM_JOURNAL_NAME,
        private=True,
        max_bytes=_COMPLETED_MIGRATION_CLAIM_JOURNAL_MAX_BYTES,
    )
    if payload is None:
        return False
    if len(payload) > _COMPLETED_MIGRATION_CLAIM_JOURNAL_MAX_BYTES:
        raise ValueError("Completed migration claim transaction journal is invalid")
    try:
        parsed = _strict_completed_migration_json(payload)
        artifacts = parsed.get("artifacts")
        names = {
            _COMPLETED_MIGRATION_STORE_NAME,
            _COMPLETED_MIGRATION_HISTORY_NAME,
            _COMPLETED_MIGRATION_CLAIM_MARKER_NAME,
        }
        phase = parsed.get("phase")
        if parsed.get("schema") != 1 or phase not in ("prepared", "committed") or not isinstance(artifacts, dict) or set(artifacts) != names:
            raise ValueError
        if phase == "committed":
            clear_completed_migration_claim_journal(root)
            return False
        for name in names:
            artifact = artifacts[name]
            if not isinstance(artifact, dict) or type(artifact.get("present")) is not bool:
                raise ValueError
            path = root / name
            if artifact["present"]:
                content, mode = artifact.get("content"), artifact.get("mode")
                if not isinstance(content, str) or type(mode) is not int:
                    raise ValueError
                decoded = base64.b64decode(content.encode("ascii"), validate=True)
                max_bytes = (
                    _COMPLETED_MIGRATION_HISTORY_MAX_BYTES
                    if name == _COMPLETED_MIGRATION_HISTORY_NAME
                    else _COMPLETED_MIGRATION_AUTH_MAX_BYTES
                )
                if len(decoded) > max_bytes:
                    raise ValueError
                _write_completed_migration_private_file(root, name, decoded, mode)
            elif os.path.lexists(path):
                info = path.lstat()
                if path.is_symlink() or not stat.S_ISREG(info.st_mode):
                    raise ValueError
                path.unlink()
        journal_path.unlink()
        if os.name == "posix":
            directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return True
    except (OSError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError("Completed migration claim transaction journal is invalid") from exc


def _fsync_completed_migration_directory(root: Path) -> None:
    if os.name != "posix":
        return
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def clear_completed_migration_claim_journal(config_dir: str | Path) -> None:
    root = Path(config_dir)
    journal_path = root / _COMPLETED_MIGRATION_CLAIM_JOURNAL_NAME
    if not os.path.lexists(journal_path):
        raise ValueError("Completed migration claim transaction is unavailable")
    info = journal_path.lstat()
    if journal_path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("Completed migration claim transaction file is unsafe")
    journal_path.unlink()
    _fsync_completed_migration_directory(root)


def validate_completed_migration_claimed_auth_state(config_dir: str | Path, binding: object) -> None:
    """Validate the product-created claimed phase bound to one receipt/backup."""
    root = Path(config_dir)
    expected = _completed_migration_transition_binding(binding)
    marker_bytes = _read_completed_migration_auth_file(
        root, _COMPLETED_MIGRATION_CLAIM_MARKER_NAME, private=True,
    )
    if marker_bytes is None:
        raise _completed_migration_claim_error()
    marker = _strict_completed_migration_json(marker_bytes)
    required = {
        "schema", "migration_id", "backup", "receipt_sha256", "backup_manifest_sha256",
        "browser_handover_version", "initial_admin_key_id",
    }
    if set(marker) != required or marker.get("schema") != 1:
        raise _completed_migration_claim_error()
    for key, value in expected.items():
        if marker.get(key) != value:
            raise _completed_migration_claim_error()
    version, key_id = marker.get("browser_handover_version"), marker.get("initial_admin_key_id")
    if (
        not isinstance(version, str) or not version.strip() or len(version) > 160
        or not isinstance(key_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", key_id)
    ):
        raise _completed_migration_claim_error()

    store_bytes = _read_completed_migration_auth_file(root, _COMPLETED_MIGRATION_STORE_NAME, private=True)
    history_bytes = _read_completed_migration_auth_file(
        root,
        _COMPLETED_MIGRATION_HISTORY_NAME,
        private=False,
        max_bytes=_COMPLETED_MIGRATION_HISTORY_MAX_BYTES,
    )
    if store_bytes is None or history_bytes is None:
        raise _completed_migration_claim_error()
    raw_store = _strict_completed_migration_json(store_bytes)
    try:
        store = ApiKeyStore.from_str(store_bytes.decode("utf-8"))
    except (PersistError, UnicodeDecodeError, ValueError) as error:
        raise _completed_migration_claim_error() from error
    if raw_store.get("version") != 3 or raw_store.get("browser_handover_claimed_version") != version:
        raise _completed_migration_claim_error()
    keys = raw_store.get("api_keys")
    sessions = raw_store.get("ui_sessions")
    if not isinstance(keys, list) or not isinstance(sessions, list):
        raise _completed_migration_claim_error()
    key_ids = [record.get("id") for record in keys if isinstance(record, dict)]
    if len(key_ids) != len(keys) or len(set(key_ids)) != len(key_ids):
        raise _completed_migration_claim_error()
    initial = store.get_api_key(key_id)
    if initial is None or initial.is_revoked or "admin" not in initial.scopes:
        raise _completed_migration_claim_error()
    active_hashes = {
        record.id: record.secret_hash for record in store.api_keys if not record.is_revoked
    }
    for session in sessions:
        if not isinstance(session, dict) or session.get("bootstrap") is True:
            raise _completed_migration_claim_error()
        session_key, session_hash = session.get("api_key_id"), session.get("api_key_secret_hash")
        if not isinstance(session_key, str) or not isinstance(session_hash, str) or active_hashes.get(session_key) != session_hash:
            raise _completed_migration_claim_error()

    try:
        lines = history_bytes.decode("utf-8").splitlines()
        entries = [json.loads(line, object_pairs_hook=_strict_json_object) for line in lines]
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise _completed_migration_claim_error() from error
    if not lines or not history_bytes.endswith(b"\n"):
        raise _completed_migration_claim_error()
    initial_claim = False
    remembered_claim = False
    allowed_events = {
        "store_loaded", "store_saved", "bootstrap_proof_created", "bootstrap_proof_cleared",
        "bootstrap_exchange_created", "bootstrap_exchange_cleared", "api_key_created",
        "ui_session_created", "api_key_updated", "api_key_rotated", "api_key_revoked", "api_key_deleted",
    }
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) not in (
            {"timestamp", "event", "reason", "store_file"},
            {"timestamp", "event", "reason", "store_file", "details"},
        ):
            raise _completed_migration_claim_error()
        details = entry.get("details", {})
        if (
            not _valid_completed_migration_timestamp(entry.get("timestamp"))
            or entry.get("store_file") != _COMPLETED_MIGRATION_STORE_NAME
            or entry.get("event") not in allowed_events
            or not isinstance(entry.get("reason"), str)
            or not isinstance(details, dict)
        ):
            raise _completed_migration_claim_error()
        if entry["event"] == "api_key_created" and entry["reason"] == "initial_admin_created":
            initial_claim = details.get("api_key_id") == key_id and details.get("browser_handover_version") == version
        if entry["event"] == "ui_session_created" and entry["reason"] == "remembered_browser_session_created":
            remembered_claim = remembered_claim or details.get("api_key_id") == key_id
    if not initial_claim or not remembered_claim:
        raise _completed_migration_claim_error()


def completed_migration_claimed_browser_handover_version(config_dir: str | Path) -> str:
    """Read the already-validated completed-migration marker version."""
    root = Path(config_dir)
    marker_bytes = _read_completed_migration_auth_file(
        root, _COMPLETED_MIGRATION_CLAIM_MARKER_NAME, private=True,
    )
    if marker_bytes is None:
        raise _completed_migration_claim_error()
    version = _strict_completed_migration_json(marker_bytes).get("browser_handover_version")
    if not isinstance(version, str) or not version.strip():
        raise _completed_migration_claim_error()
    return version.strip()


def _normalize_scopes(scopes: object) -> List[str]:
    if not _is_scope_collection(scopes):
        raise ValueError("API key scopes must be a list of scope names")

    normalized: List[str] = []
    for scope in scopes:
        if not isinstance(scope, str):
            raise ValueError("API key scopes must be strings")
        scope_name = scope.strip().lower()
        if not scope_name:
            raise ValueError("API key scopes cannot be blank")
        if scope_name not in _ALLOWED_SCOPES:
            raise ValueError("Unknown API key scope '{}'".format(scope))
        if scope_name not in normalized:
            normalized.append(scope_name)

    if not normalized:
        raise ValueError("API key scopes cannot be empty")

    return normalized


def _hash_secret(secret: object, salt: object = None) -> str:
    if not isinstance(secret, str) or not secret:
        raise ValueError("API key secret cannot be blank")

    salt_bytes = salt if salt is not None else os.urandom(_HASH_SALT_BYTES)
    if not isinstance(salt_bytes, bytes):
        raise ValueError("API key salt must be bytes")

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt_bytes,
        _HASH_ITERATIONS
    )
    return "{}${}${}${}".format(
        _HASH_ALGORITHM,
        _HASH_ITERATIONS,
        base64.b64encode(salt_bytes).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_secret(secret: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algorithm != _HASH_ALGORITHM:
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected_digest = base64.b64decode(digest_b64.encode("ascii"))
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            secret.encode("utf-8"),
            salt,
            int(iterations)
        )
        return hmac.compare_digest(expected_digest, actual_digest)
    except (ValueError, TypeError, binascii.Error):
        return False


@dataclass
class ApiKeyRecord:
    id: str
    name: str
    scopes: List[str] = field(default_factory=lambda: list[str]())
    secret_hash: str = ""
    created_at: str = ""
    updated_at: str = ""
    revoked_at: Optional[str] = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def to_public_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload.pop("secret_hash", None)
        payload["active"] = not self.is_revoked
        return payload


@dataclass
class UiSessionRecord:
    secret: str
    scopes: List[str] = field(default_factory=lambda: list[str]())
    created_at: str = ""
    expires_at: str = ""
    bootstrap: bool = False
    remembered: bool = False
    api_key_id: Optional[str] = None
    api_key_secret_hash: Optional[str] = None

    def cookie_max_age_seconds(self) -> int:
        if self.remembered:
            return int(_REMEMBERED_UI_SESSION_COOKIE_MAX_AGE.total_seconds())
        created_at = datetime.fromisoformat(self.created_at)
        expires_at = datetime.fromisoformat(self.expires_at)
        return max(0, int((expires_at - created_at).total_seconds()))


@dataclass
class BootstrapProofRecord:
    secret: str
    created_at: str = ""
    expires_at: str = ""


@dataclass
class BootstrapExchangeRecord:
    secret: str
    created_at: str = ""
    expires_at: str = ""


@dataclass
class _CompletedMigrationClaimRuntimeState:
    api_keys: List[ApiKeyRecord]
    ui_sessions: Dict[str, UiSessionRecord]
    browser_handover_claimed_version: str
    bootstrap_proof: Optional[BootstrapProofRecord]
    bootstrap_exchange: Optional[BootstrapExchangeRecord]
    transition_binding: Optional[Dict[str, str]]


class ApiKeyStore(Persist):
    __KEY_VERSION = "version"
    __KEY_API_KEYS = "api_keys"
    __KEY_UI_SESSIONS = "ui_sessions"
    __KEY_BROWSER_HANDOVER_CLAIMED_VERSION = "browser_handover_claimed_version"
    __BROWSER_HANDOVER_DEADLINE_NAME = "browser-handover-deadline.json"

    def __init__(self, file_path: Optional[str] = None):
        self.__file_path = file_path
        self.__api_keys: List[ApiKeyRecord] = []
        self.__ui_sessions: Dict[str, UiSessionRecord] = {}
        self.__browser_handover_claimed_version = ""
        self.__browser_handover_deadline: Optional[Dict[str, object]] = None
        self.__browser_handover_deadline_loaded = False
        self.__browser_handover_deadline_invalid = False
        self.__browser_handover_activated = False
        self.__state_lock = threading.RLock()
        self.__bootstrap_proof_path: Optional[str] = None
        self.__bootstrap_proof: Optional[BootstrapProofRecord] = None
        self.__bootstrap_exchange: Optional[BootstrapExchangeRecord] = None
        self.__completed_migration_transition_binding: Optional[Dict[str, str]] = None
        self.__completed_migration_claimed_handover_version = ""
        self.__completed_migration_claim_transaction: Optional[_CompletedMigrationClaimRuntimeState] = None
        self.__last_saved_history_snapshot: Optional[Dict[str, object]] = None

    @property
    def file_path(self) -> Optional[str]:
        return self.__file_path

    def bind_file_path(self, file_path: str) -> None:
        self.__file_path = file_path

    def bind_bootstrap_proof_path(self, file_path: str) -> None:
        self.__bootstrap_proof_path = file_path
        self.__sync_bootstrap_proof_artifact()

    def bind_completed_migration_claim_transition(self, binding: object) -> None:
        if self.__file_path is None:
            raise ValueError("Completed migration claim transition requires a bound auth store")
        self.__completed_migration_transition_binding = _completed_migration_transition_binding(binding)

    def bind_completed_migration_claimed_handover_version(self, version: object) -> None:
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Completed migration claimed handover version is invalid")
        self.__completed_migration_claimed_handover_version = version.strip()

    def effective_browser_handover_version(self, config: object) -> str:
        """Return the configured replay version or a stable completed-migration claim id."""
        configured = self.__configured_browser_handover_version(config)
        if configured:
            return configured
        binding = self.__completed_migration_transition_binding
        if binding is not None:
            return _migration_claim_browser_handover_version(binding)
        return self.__completed_migration_claimed_handover_version

    def validate_completed_migration_claim_transition(self, browser_handover_version: object) -> None:
        """Validate transition prerequisites before first-admin state mutates."""
        binding = self.__completed_migration_transition_binding
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        if binding is None:
            return
        if not version or self.__file_path is None:
            raise ValueError("Completed migration claim transition is unavailable")
        if completed_migration_claim_marker_exists(Path(self.__file_path).parent):
            raise ValueError("Completed migration claim transition is unavailable")

    def begin_completed_migration_claim_transaction(self) -> bool:
        """Journal the preclaim state before any completed-migration auth mutation."""
        with self.__state_lock:
            if self.__completed_migration_transition_binding is None:
                return False
            if self.__file_path is None or self.__completed_migration_claim_transaction is not None:
                raise ValueError("Completed migration claim transition is unavailable")
            begin_completed_migration_claim_journal(Path(self.__file_path).parent)
            self.__completed_migration_claim_transaction = _CompletedMigrationClaimRuntimeState(
                api_keys=copy.deepcopy(self.__api_keys),
                ui_sessions=copy.deepcopy(self.__ui_sessions),
                browser_handover_claimed_version=self.__browser_handover_claimed_version,
                bootstrap_proof=copy.deepcopy(self.__bootstrap_proof),
                bootstrap_exchange=copy.deepcopy(self.__bootstrap_exchange),
                transition_binding=copy.deepcopy(self.__completed_migration_transition_binding),
            )
            return True

    def abort_completed_migration_claim_transaction(self) -> None:
        """Use the durable journal to abandon a failed claim in this process."""
        with self.__state_lock:
            state = self.__completed_migration_claim_transaction
            if state is None or self.__file_path is None:
                return
            recover_completed_migration_claim_journal(Path(self.__file_path).parent)
            self.__api_keys = copy.deepcopy(state.api_keys)
            self.__ui_sessions = copy.deepcopy(state.ui_sessions)
            self.__browser_handover_claimed_version = state.browser_handover_claimed_version
            self.__bootstrap_proof = copy.deepcopy(state.bootstrap_proof)
            self.__bootstrap_exchange = copy.deepcopy(state.bootstrap_exchange)
            self.__completed_migration_transition_binding = copy.deepcopy(state.transition_binding)
            self.__completed_migration_claim_transaction = None
            self.__sync_bootstrap_proof_artifact()

    def finish_completed_migration_claim_transaction(self) -> None:
        with self.__state_lock:
            if self.__completed_migration_claim_transaction is None or self.__file_path is None:
                raise ValueError("Completed migration claim transition is unavailable")
            root = Path(self.__file_path).parent
            commit_completed_migration_claim_journal(root)
            self.__completed_migration_claim_transaction = None
            # Cleanup is advisory after the durable committed phase.  A later
            # startup consumes it before claimed-lineage validation.
            try:
                clear_completed_migration_claim_journal(root)
            except OSError:
                pass

    def complete_completed_migration_claim_transition(
        self, initial_admin_key_id: object, browser_handover_version: object,
    ) -> None:
        binding = self.__completed_migration_transition_binding
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        if binding is None:
            return
        self.validate_completed_migration_claim_transition(version)
        if not isinstance(initial_admin_key_id, str):
            raise ValueError("Completed migration claim transition is unavailable")
        record = self.get_api_key(initial_admin_key_id)
        if (
            record is None or record.is_revoked or "admin" not in record.scopes
            or self.__browser_handover_claimed_version != version or self.__file_path is None
        ):
            raise ValueError("Completed migration claim transition is invalid")
        payload: Dict[str, object] = {
            "schema": 1,
            **binding,
            "browser_handover_version": version,
            "initial_admin_key_id": record.id,
        }
        _write_completed_migration_claim_marker(Path(self.__file_path).parent, payload)
        self.__completed_migration_claimed_handover_version = version
        self.__completed_migration_transition_binding = None

    def __record_history_event(self, event: str, reason: str, **details: object) -> None:
        append_api_key_store_history(
            self.__file_path, event, reason,
            required=self.__completed_migration_claim_transaction is not None,
            **details,
        )

    def __history_snapshot(self) -> Dict[str, object]:
        return {
            "api_key_count": len(self.__api_keys),
            "active_api_key_count": len([record for record in self.__api_keys if not record.is_revoked]),
            "ui_session_count": len(self.__ui_sessions),
            "remembered_ui_session_count": len([
                session for session in self.__ui_sessions.values() if getattr(session, "remembered", False)
            ]),
            "browser_handover_claimed_version": self.__browser_handover_claimed_version,
            "bootstrap_proof_present": self.__bootstrap_proof is not None,
            "bootstrap_exchange_present": self.__bootstrap_exchange is not None,
        }

    @property
    def api_keys(self) -> List[ApiKeyRecord]:
        return list(self.__api_keys)

    @property
    def active_admin_key_count(self) -> int:
        return len([
            record for record in self.__api_keys
            if not record.is_revoked and "admin" in record.scopes
        ])

    def list_api_keys(self, include_revoked: bool = False) -> List[Dict[str, object]]:
        if include_revoked:
            records = self.__api_keys
        else:
            records = [record for record in self.__api_keys if not record.is_revoked]
        return [record.to_public_dict() for record in records]

    def get_api_key(self, key_id: str) -> Optional[ApiKeyRecord]:
        for record in self.__api_keys:
            if record.id == key_id:
                return record
        return None

    def find_api_key_by_secret(self, secret: str) -> Optional[ApiKeyRecord]:
        for record in self.__api_keys:
            if _verify_secret(secret, record.secret_hash):
                return record
        return None

    def create_ui_session(
        self,
        scopes: object,
        bootstrap: object = False,
        api_key_id: object = None,
        api_key_secret_hash: object = None,
        remembered: object = False
    ) -> UiSessionRecord:
        normalized_scopes = _normalize_scopes(scopes)
        if type(bootstrap) is not bool:
            raise ValueError("Bootstrap session flag must be a boolean")
        if api_key_id is not None and (not isinstance(api_key_id, str) or not api_key_id.strip()):
            raise ValueError("API key id cannot be blank")
        if api_key_secret_hash is not None and (not isinstance(api_key_secret_hash, str) or not api_key_secret_hash.strip()):
            raise ValueError("API key secret hash cannot be blank")
        if type(remembered) is not bool:
            raise ValueError("Remembered session flag must be a boolean")
        if bootstrap and remembered:
            raise ValueError("Bootstrap sessions cannot also be remembered")
        now = datetime.now(timezone.utc)
        record = UiSessionRecord(
            secret=secrets.token_urlsafe(32),
            scopes=normalized_scopes,
            created_at=now.isoformat(timespec="seconds"),
            expires_at="" if remembered else (now + _UI_SESSION_TTL).isoformat(timespec="seconds"),
            bootstrap=bootstrap,
            remembered=remembered,
            api_key_id=api_key_id.strip() if isinstance(api_key_id, str) else None,
            api_key_secret_hash=api_key_secret_hash.strip() if isinstance(api_key_secret_hash, str) else None,
        )
        self.__ui_sessions[record.secret] = record
        self.__prune_expired_ui_sessions(now)
        self.save()
        if remembered:
            reason = "remembered_browser_session_created"
        elif bootstrap:
            reason = "bootstrap_session_created"
        else:
            reason = "browser_session_created"
        self.__record_history_event(
            "ui_session_created",
            reason,
            bootstrap=bootstrap,
            remembered=remembered,
            api_key_id=record.api_key_id,
            scopes=record.scopes,
        )
        return record

    def __create_api_key_record(self, name: object, scopes: object) -> CreatedApiKey:
        scopes = _normalize_scopes(scopes)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("API key name cannot be blank")

        secret = secrets.token_urlsafe(32)
        now = _utc_now_iso()
        record = ApiKeyRecord(
            id=str(uuid.uuid4()),
            name=name.strip(),
            scopes=scopes,
            secret_hash=_hash_secret(secret),
            created_at=now,
            updated_at=now,
        )
        self.__api_keys.append(record)
        return {"record": record, "secret": secret}

    def create_browser_session_for_api_key(self, key_id: object) -> UiSessionRecord:
        if not isinstance(key_id, str) or not key_id.strip():
            raise ValueError("API key id cannot be blank")

        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))
        if record.is_revoked:
            raise ValueError("Cannot create a browser session for a revoked API key")

        return self.create_ui_session(
            record.scopes,
            api_key_id=record.id,
            api_key_secret_hash=record.secret_hash
        )

    def create_remembered_browser_session_for_api_key(self, key_id: object) -> UiSessionRecord:
        if not isinstance(key_id, str) or not key_id.strip():
            raise ValueError("API key id cannot be blank")

        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))
        if record.is_revoked:
            raise ValueError("Cannot create a browser session for a revoked API key")

        return self.create_ui_session(
            record.scopes,
            remembered=True,
            api_key_id=record.id,
            api_key_secret_hash=record.secret_hash
        )

    def __discard_browser_sessions_for_api_key(self, key_id: str, reason: str) -> int:
        matching_secrets = [
            secret for secret, session in self.__ui_sessions.items()
            if getattr(session, "api_key_id", None) == key_id
        ]
        remembered_count = len([
            secret for secret in matching_secrets
            if getattr(self.__ui_sessions.get(secret), "remembered", False)
        ])
        for secret in matching_secrets:
            self.__ui_sessions.pop(secret, None)
        self.__record_history_event(
            "ui_sessions_discarded",
            reason,
            api_key_id=key_id,
            discarded_count=len(matching_secrets),
            remembered_count=remembered_count,
        )
        return len(matching_secrets)

    def find_ui_session_by_secret(self, secret: str) -> Optional[UiSessionRecord]:
        now = datetime.now(timezone.utc)
        self.__prune_expired_ui_sessions(now)
        record = self.__ui_sessions.get(secret)
        if record is None:
            return None
        if getattr(record, "remembered", False):
            return record
        try:
            if datetime.fromisoformat(record.expires_at) <= now:
                self.__ui_sessions.pop(secret, None)
                return None
        except ValueError:
            self.__ui_sessions.pop(secret, None)
            return None
        return record

    def invalidate_ui_session(self, secret: object) -> None:
        if not isinstance(secret, str) or not secret.strip():
            return
        if self.__ui_sessions.pop(secret, None) is not None:
            self.save()

    def resolve_ui_session_api_key(self, session: UiSessionRecord) -> Optional[ApiKeyRecord]:
        api_key_id = getattr(session, "api_key_id", None)
        api_key_secret_hash = getattr(session, "api_key_secret_hash", None)
        if not isinstance(api_key_id, str) or not api_key_id.strip():
            return None
        if not isinstance(api_key_secret_hash, str) or not api_key_secret_hash.strip():
            return None

        record = self.get_api_key(api_key_id)
        if record is None or record.is_revoked:
            return None

        if not hmac.compare_digest(record.secret_hash, api_key_secret_hash):
            return None

        return record

    def can_claim_initial_admin(self, browser_handover_version: object, config: object = None) -> bool:
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        state = self.__browser_handover_state(version, config, activate=False)
        if self.__browser_handover_activated or state["state_invalid"] or state["expires_at"] is not None:
            return bool(state["open"])
        # Legacy/direct callers that have not brought up the normal WebApp
        # retain their old atomic claim primitive.  They deliberately do not
        # create a deadline; normal startup is the sole deadline origin.
        return self.active_admin_key_count == 0 or self.__browser_handover_claimed_version != version

    def claim_initial_admin_if_available(self, browser_handover_version: object) -> bool:
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        with self.__state_lock:
            if not self.can_claim_initial_admin(version):
                return False
            self.__browser_handover_claimed_version = version
            self.save()
            if self.__completed_migration_claim_transaction is None:
                self.finalize_browser_handover_claim(version)
            self.__record_history_event(
                "browser_handover_claimed",
                "initial_admin_claimed",
                browser_handover_version=version,
            )
            return True

    def create_initial_admin_api_key_if_available(
        self,
        browser_handover_version: object,
        name: str,
    ) -> Optional[CreatedApiKey]:
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        with self.__state_lock:
            if not self.can_claim_initial_admin(version):
                return None

            result = self.__create_api_key_record(name, ["admin"])
            self.__browser_handover_claimed_version = version
            self.clear_bootstrap_proof(reason="admin_api_key_created")
            self.clear_bootstrap_exchange(reason="admin_api_key_created")
            self.save()
            if self.__completed_migration_claim_transaction is None:
                self.finalize_browser_handover_claim(version)
            self.__record_history_event(
                "api_key_created",
                "initial_admin_created",
                api_key_id=result["record"].id,
                name=result["record"].name,
                scopes=result["record"].scopes,
                browser_handover_version=version,
            )
            return result

    def claim_initial_admin(self, browser_handover_version: str) -> None:
        self.claim_initial_admin_if_available(browser_handover_version)

    def get_browser_handover_state(self, config: object) -> Dict[str, object]:
        version = self.effective_browser_handover_version(config)
        return self.__browser_handover_state(version, config, activate=False)

    def activate_browser_handover(self, config: object) -> Dict[str, object]:
        """Open/preserve the claim window once the normal WebApp is available.

        The deadline is deliberately a sidecar, rather than another auth-store
        field: completed v0.8.6 migration validation has a strict auth-store
        grammar which must remain readable by older completed installations.
        """
        version = self.effective_browser_handover_version(config)
        fresh_activation = not self.__browser_handover_activated
        self.__browser_handover_activated = True
        return self.__browser_handover_state(
            version, config, activate=True, reopen_initial=fresh_activation,
        )

    def __browser_handover_state(
        self, version: str, config: object, *, activate: bool, reopen_initial: bool = False,
    ) -> Dict[str, object]:
        with self.__state_lock:
            now = _current_time()
            configured = self.__configured_browser_handover_version(config)
            active_admin_exists = self.active_admin_key_count > 0
            recovery = bool(
                active_admin_exists and version != self.__browser_handover_claimed_version
                and (configured or config is None)
            )
            desired_kind = "recovery" if recovery else "initial"
            desired_version = self.__browser_handover_version_digest(version) if recovery else ""
            state = self.__load_browser_handover_deadline()
            invalid = self.__browser_handover_deadline_invalid

            if not recovery and active_admin_exists:
                if activate:
                    self.finalize_browser_handover_claim(version)
                return self.__handover_payload(version, None, now, False, False, invalid)

            deadline = state.get("active") if state is not None else None
            matching = (
                deadline is not None
                and deadline["kind"] == desired_kind
                and deadline["version_sha256"] == desired_version
            )
            expired = matching and self.__deadline_expired(deadline, now)
            seen_recovery_version = bool(
                recovery and state is not None and desired_version in state["seen_recovery_versions"]
            )
            recovery_history_full = bool(
                recovery and state is not None and not matching and not seen_recovery_version
                and len(state["seen_recovery_versions"]) >= _BROWSER_HANDOVER_SEEN_RECOVERY_VERSION_LIMIT
            )
            # A normal app restart may deliberately offer a fresh first-admin
            # window, but never extends a live one.  Recovery windows are
            # version-bound; an expired version stays closed until it changes.
            should_open = False
            if not invalid and (
                (not matching and not seen_recovery_version and not recovery_history_full)
                or (desired_kind == "initial" and expired and reopen_initial)
            ):
                # get_browser_handover_state is observational; only normal
                # WebApp activation (or a newly configured recovery while that
                # app is live) starts it.
                should_open = activate or (
                    desired_kind == "recovery" and self.__browser_handover_activated
                )
            if should_open:
                ttl = _RECOVERY_BROWSER_HANDOVER_TTL if desired_kind == "recovery" else _INITIAL_BROWSER_HANDOVER_TTL
                deadline = {
                    "kind": desired_kind,
                    "version_sha256": desired_version,
                    "opened_at": now.isoformat(timespec="seconds"),
                    "expires_at": (now + ttl).isoformat(timespec="seconds"),
                }
                state = state or {"active": None, "seen_recovery_versions": []}
                state["active"] = deadline
                if desired_kind == "recovery":
                    state["seen_recovery_versions"].append(desired_version)
                self.__browser_handover_deadline_invalid = False
                self.__browser_handover_deadline = state
                self.__write_browser_handover_deadline(state)
                matching, expired = True, False

            open_window = bool(matching and not expired and not invalid)
            return self.__handover_payload(version, deadline if matching else None, now, open_window, expired, invalid,
                                           recovery_seen=seen_recovery_version, recovery_history_full=recovery_history_full)

    def __handover_payload(
        self, version: str, deadline: Optional[Dict[str, str]], now: datetime,
        open_window: bool, expired: bool, invalid: bool, *, recovery_seen: bool = False,
        recovery_history_full: bool = False,
    ) -> Dict[str, object]:
        expires_at = deadline.get("expires_at") if deadline is not None else None
        remaining_seconds = 0
        if open_window and deadline is not None:
            try:
                remaining_seconds = max(0, int((datetime.fromisoformat(deadline["expires_at"]) - now).total_seconds()))
            except ValueError:
                open_window = False
        return {
            "configured_version": version,
            "claimed_version": self.__browser_handover_claimed_version,
            "open": open_window,
            "window_type": deadline.get("kind") if deadline is not None else None,
            "opened_at": deadline.get("opened_at") if deadline is not None else None,
            "expires_at": expires_at,
            "remaining_seconds": remaining_seconds,
            "expired": bool(expired),
            "state_invalid": invalid,
            "recovery_version_closed": recovery_seen,
            "recovery_history_full": recovery_history_full,
        }

    @staticmethod
    def __deadline_expired(deadline: Dict[str, str], now: datetime) -> bool:
        try:
            return datetime.fromisoformat(deadline["expires_at"]) <= now
        except ValueError:
            return True

    def __browser_handover_deadline_path(self) -> Optional[Path]:
        if not self.__file_path:
            return None
        path = Path(self.__file_path)
        return path.with_name(self.__BROWSER_HANDOVER_DEADLINE_NAME)

    @staticmethod
    def __browser_handover_version_digest(version: str) -> str:
        return hashlib.sha256(version.encode("utf-8")).hexdigest()

    def __load_browser_handover_deadline(self) -> Optional[Dict[str, object]]:
        if self.__browser_handover_deadline_loaded:
            return self.__browser_handover_deadline
        self.__browser_handover_deadline_loaded = True
        path = self.__browser_handover_deadline_path()
        if path is None or not os.path.lexists(path):
            return None
        try:
            raw = json.loads(self.__read_browser_handover_deadline_bytes(path).decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("deadline state is invalid")
            if raw.get("schema") == 1:
                # Preserve in-progress first implementation windows while
                # migrating them in memory; a recovery entry counts as seen.
                if set(raw) != {"schema", "kind", "version", "opened_at", "expires_at"}:
                    raise ValueError("legacy deadline fields are invalid")
                kind, legacy_version = raw.get("kind"), raw.get("version")
                if kind not in {"initial", "recovery"} or not isinstance(legacy_version, str):
                    raise ValueError("legacy deadline state is invalid")
                digest = self.__browser_handover_version_digest(legacy_version) if kind == "recovery" else ""
                active = {"kind": kind, "version_sha256": digest, "opened_at": raw.get("opened_at"), "expires_at": raw.get("expires_at")}
                self.__validate_browser_handover_deadline(active)
                self.__browser_handover_deadline = {
                    "active": active,
                    "seen_recovery_versions": [digest] if kind == "recovery" else [],
                }
                return self.__browser_handover_deadline
            if set(raw) != {"schema", "active", "seen_recovery_versions"} or raw.get("schema") != 2:
                raise ValueError("deadline state fields are invalid")
            active = raw.get("active")
            if active is not None:
                if not isinstance(active, dict):
                    raise ValueError("active deadline is invalid")
                self.__validate_browser_handover_deadline(active)
            seen = raw.get("seen_recovery_versions")
            if (
                not isinstance(seen, list) or len(seen) > _BROWSER_HANDOVER_SEEN_RECOVERY_VERSION_LIMIT
                or len(set(seen)) != len(seen)
                or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in seen)
            ):
                raise ValueError("recovery version history is invalid")
            self.__browser_handover_deadline = {"active": active, "seen_recovery_versions": seen}
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self.__browser_handover_deadline_invalid = True
        return self.__browser_handover_deadline

    @staticmethod
    def __validate_browser_handover_deadline(deadline: object) -> None:
        if not isinstance(deadline, dict) or set(deadline) != {"kind", "version_sha256", "opened_at", "expires_at"}:
            raise ValueError("deadline fields are invalid")
        if deadline.get("kind") not in {"initial", "recovery"}:
            raise ValueError("deadline kind is invalid")
        version_digest = deadline.get("version_sha256")
        if not isinstance(version_digest, str) or (deadline["kind"] == "initial" and version_digest != ""):
            raise ValueError("deadline version is invalid")
        if deadline["kind"] == "recovery" and re.fullmatch(r"[0-9a-f]{64}", version_digest) is None:
            raise ValueError("deadline version is invalid")
        opened_at, expires_at = datetime.fromisoformat(deadline.get("opened_at")), datetime.fromisoformat(deadline.get("expires_at"))
        if opened_at.tzinfo is None or expires_at.tzinfo is None or expires_at <= opened_at:
            raise ValueError("deadline timestamps are invalid")

    @staticmethod
    def __read_browser_handover_deadline_bytes(path: Path) -> bytes:
        before = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("deadline file is unsafe")
        if os.name == "posix":
            if stat.S_IMODE(before.st_mode) != 0o600 or before.st_uid != os.geteuid() or before.st_gid != os.getegid():
                raise ValueError("deadline file is unsafe")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > _BROWSER_HANDOVER_STATE_MAX_BYTES
            ):
                raise ValueError("deadline file is unsafe")
            content = os.read(descriptor, _BROWSER_HANDOVER_STATE_MAX_BYTES + 1)
            if len(content) > _BROWSER_HANDOVER_STATE_MAX_BYTES:
                raise ValueError("deadline file is too large")
            return content
        finally:
            os.close(descriptor)

    def __write_browser_handover_deadline(self, state: Dict[str, object]) -> None:
        path = self.__browser_handover_deadline_path()
        if path is None:
            return
        payload = (json.dumps({"schema": 2, **state}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > _BROWSER_HANDOVER_STATE_MAX_BYTES:
            raise ValueError("browser handover state is too large")
        _write_completed_migration_private_file(path.parent, path.name, payload)

    def __mark_browser_handover_claimed(self, version: str) -> None:
        if not self.__browser_handover_deadline_loaded or self.__browser_handover_deadline_invalid:
            return
        state = self.__browser_handover_deadline
        if state is None:
            return
        active = state.get("active")
        if not isinstance(active, dict):
            return
        expected = self.__browser_handover_version_digest(version) if active.get("kind") == "recovery" else ""
        if active.get("version_sha256") != expected:
            return
        state["active"] = None
        self.__write_browser_handover_deadline(state)

    def finalize_browser_handover_claim(self, browser_handover_version: object) -> bool:
        """Best-effort post-commit cleanup of an already-closed claim window."""
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        with self.__state_lock:
            try:
                self.__mark_browser_handover_claimed(version)
            except (OSError, ValueError):
                # The authoritative API-key claim is already durable.  Keep
                # the in-memory state closed and retry sidecar cleanup on a
                # later normal-app activation rather than losing the secret.
                return False
            return True

    def ensure_bootstrap_proof(self) -> Optional[BootstrapProofRecord]:
        now = datetime.now(timezone.utc)
        self.__prune_bootstrap_proof(now)
        self.__prune_bootstrap_exchange(now)
        if self.active_admin_key_count > 0:
            self.clear_bootstrap_proof(reason="active_admin_key_exists")
            self.clear_bootstrap_exchange(reason="active_admin_key_exists")
            return None

        if self.__bootstrap_proof is None:
            self.__bootstrap_proof = BootstrapProofRecord(
                secret=secrets.token_urlsafe(32),
                created_at=now.isoformat(timespec="seconds"),
                expires_at=(now + _BOOTSTRAP_PROOF_TTL).isoformat(timespec="seconds"),
            )
            self.__sync_bootstrap_proof_artifact()
            self.__record_history_event(
                "bootstrap_proof_created",
                "first_run_bootstrap_window_opened",
                expires_at=self.__bootstrap_proof.expires_at,
            )

        return self.__bootstrap_proof

    def peek_bootstrap_proof(self, secret: object) -> bool:
        if not isinstance(secret, str) or not secret.strip():
            return False

        now = datetime.now(timezone.utc)
        self.__prune_bootstrap_proof(now)
        if self.__bootstrap_proof is None:
            return False

        return hmac.compare_digest(self.__bootstrap_proof.secret, secret.strip())

    def consume_bootstrap_proof(self, secret: str) -> bool:
        if not self.peek_bootstrap_proof(secret):
            return False

        self.__bootstrap_proof = None
        self.__sync_bootstrap_proof_artifact()
        self.__record_history_event("bootstrap_proof_cleared", "consumed")
        return True

    def clear_bootstrap_proof(self, reason: str = "manual_clear") -> None:
        if self.__bootstrap_proof is None:
            return
        self.__bootstrap_proof = None
        self.__sync_bootstrap_proof_artifact()
        self.__record_history_event("bootstrap_proof_cleared", reason)

    def ensure_bootstrap_exchange(self) -> Optional[BootstrapExchangeRecord]:
        now = datetime.now(timezone.utc)
        self.__prune_bootstrap_exchange(now)
        if self.active_admin_key_count > 0:
            self.clear_bootstrap_exchange(reason="active_admin_key_exists")
            return None

        if self.__bootstrap_exchange is None:
            self.__bootstrap_exchange = BootstrapExchangeRecord(
                secret=secrets.token_urlsafe(32),
                created_at=now.isoformat(timespec="seconds"),
                expires_at=(now + _BOOTSTRAP_EXCHANGE_TTL).isoformat(timespec="seconds"),
            )
            self.__record_history_event(
                "bootstrap_exchange_created",
                "first_run_bootstrap_window_opened",
                expires_at=self.__bootstrap_exchange.expires_at,
            )

        return self.__bootstrap_exchange

    def peek_bootstrap_exchange(self, secret: object) -> bool:
        if not isinstance(secret, str) or not secret.strip():
            return False

        now = datetime.now(timezone.utc)
        self.__prune_bootstrap_exchange(now)
        if self.__bootstrap_exchange is None:
            return False

        return hmac.compare_digest(self.__bootstrap_exchange.secret, secret.strip())

    def consume_bootstrap_exchange(self, secret: str) -> bool:
        if not self.peek_bootstrap_exchange(secret):
            return False

        self.__bootstrap_exchange = None
        self.__record_history_event("bootstrap_exchange_cleared", "consumed")
        return True

    def clear_bootstrap_exchange(self, reason: str = "manual_clear") -> None:
        if self.__bootstrap_exchange is None:
            return
        self.__bootstrap_exchange = None
        self.__record_history_event("bootstrap_exchange_cleared", reason)

    def create_api_key(self, name: str, scopes: Sequence[str]) -> CreatedApiKey:
        with self.__state_lock:
            result = self.__create_api_key_record(name, scopes)
            if "admin" in result["record"].scopes:
                self.clear_bootstrap_proof(reason="admin_api_key_created")
                self.clear_bootstrap_exchange(reason="admin_api_key_created")
            self.save()
            self.__record_history_event(
                "api_key_created",
                "create_api_key",
                api_key_id=result["record"].id,
                name=result["record"].name,
                scopes=result["record"].scopes,
            )
            return result

    def update_api_key(
        self,
        key_id: str,
        name: object = None,
        scopes: Optional[Sequence[str]] = None
    ) -> ApiKeyRecord:
        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))
        if record.is_revoked:
            raise ValueError("Cannot update a revoked API key")

        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("API key name cannot be blank")
            name_changed = name.strip() != record.name
            record.name = name.strip()
        else:
            name_changed = False

        if scopes is not None:
            normalized_scopes = _normalize_scopes(scopes)
            scopes_changed = normalized_scopes != record.scopes
            record.scopes = normalized_scopes
        else:
            scopes_changed = False

        record.updated_at = _utc_now_iso()
        self.save()
        if name_changed and scopes_changed:
            reason = "name_and_scopes_updated"
        elif name_changed:
            reason = "name_updated"
        elif scopes_changed:
            reason = "scopes_updated"
        else:
            reason = "metadata_refreshed"
        self.__record_history_event(
            "api_key_updated",
            reason,
            api_key_id=record.id,
            name=record.name,
            scopes=record.scopes,
        )
        return record

    def delete_api_key(self, key_id: str) -> ApiKeyRecord:
        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))
        if not record.is_revoked:
            raise ValueError("Cannot delete an active API key")

        discarded_count = self.__discard_browser_sessions_for_api_key(record.id, "api_key_deleted")
        self.__api_keys.remove(record)
        self.save()
        self.__record_history_event(
            "api_key_deleted",
            "api_key_deleted",
            api_key_id=record.id,
            name=record.name,
            discarded_session_count=discarded_count,
        )
        return record

    def revoke_api_key(self, key_id: str) -> ApiKeyRecord:
        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))
        if record.is_revoked:
            raise ValueError("Cannot revoke a revoked API key")

        now = _utc_now_iso()
        record.revoked_at = now
        record.updated_at = now
        discarded_count = self.__discard_browser_sessions_for_api_key(record.id, "api_key_revoked")
        self.save()
        self.__record_history_event(
            "api_key_revoked",
            "api_key_revoked",
            api_key_id=record.id,
            name=record.name,
            discarded_session_count=discarded_count,
        )
        return record

    def rotate_api_key(self, key_id: str) -> CreatedApiKey:
        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))
        if record.is_revoked:
            raise ValueError("Cannot rotate a revoked API key")

        secret = secrets.token_urlsafe(32)
        record.secret_hash = _hash_secret(secret)
        record.updated_at = _utc_now_iso()
        self.save()
        self.__record_history_event(
            "api_key_rotated",
            "api_key_rotated",
            api_key_id=record.id,
            name=record.name,
            scopes=record.scopes,
        )
        return {"record": record, "secret": secret}

    def save(self):
        if self.__file_path is None:
            return
        directory = os.path.dirname(self.__file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if self.__completed_migration_claim_transaction is not None:
            _write_completed_migration_private_file(
                Path(self.__file_path).parent,
                Path(self.__file_path).name,
                self.to_str().encode("utf-8"),
            )
        else:
            self.to_file(self.__file_path)
        snapshot = self.__history_snapshot()
        if snapshot != self.__last_saved_history_snapshot:
            self.__record_history_event("store_saved", "persisted", **snapshot)
            self.__last_saved_history_snapshot = copy.deepcopy(snapshot)

    @classmethod
    def from_file(cls, file_path: str) -> "ApiKeyStore":
        store = super().from_file(file_path)
        store.__file_path = file_path
        store.__record_history_event("store_loaded", "loaded_existing_store", **store.__history_snapshot())
        return store

    @classmethod
    def from_str(cls, content: str) -> "ApiKeyStore":
        store = ApiKeyStore()
        try:
            payload: object = json.loads(content)
        except ValueError as exc:
            raise PersistError("Invalid API key store JSON: {}".format(exc)) from exc

        if not _is_string_object_dict(payload):
            raise PersistError("Invalid API key store JSON: expected an object")

        version = payload.get(cls.__KEY_VERSION, 1)
        if type(version) is not int:
            raise PersistError("Invalid API key store JSON: version must be an integer")
        if version < 1:
            raise PersistError("Invalid API key store JSON: version must be at least 1")

        claimed_version = payload.get(cls.__KEY_BROWSER_HANDOVER_CLAIMED_VERSION, "")
        if claimed_version is None:
            claimed_version = ""
        if not isinstance(claimed_version, str):
            raise PersistError(
                "Invalid API key store JSON: browser_handover_claimed_version must be a string"
            )
        store.__browser_handover_claimed_version = claimed_version

        records = payload.get(cls.__KEY_API_KEYS, [])
        if not _is_object_list(records):
            raise PersistError("Invalid API key store JSON: api_keys must be a list")

        for raw_record in records:
            if not _is_string_object_dict(raw_record):
                raise PersistError("Invalid API key store JSON: api key record must be an object")
            try:
                store.__api_keys.append(
                    ApiKeyRecord(
                        id=_required_string_field(raw_record, "id"),
                        name=_required_string_field(raw_record, "name"),
                        scopes=_normalize_scopes(raw_record.get("scopes", [])),
                        secret_hash=_required_string_field(raw_record, "secret_hash"),
                        created_at=_required_string_field(raw_record, "created_at"),
                        updated_at=_required_string_field(raw_record, "updated_at"),
                        revoked_at=(
                            _required_string_field(raw_record, "revoked_at")
                            if raw_record.get("revoked_at") is not None
                            else None
                        ),
                    )
                )
            except KeyError as exc:
                raise PersistError("Invalid API key store JSON: missing field {}".format(exc)) from exc
            except ValueError as exc:
                raise PersistError("Invalid API key store JSON: {}".format(exc)) from exc

        ui_sessions = payload.get(cls.__KEY_UI_SESSIONS, [])
        if not _is_object_list(ui_sessions):
            raise PersistError("Invalid API key store JSON: ui_sessions must be a list")

        for raw_session in ui_sessions:
            if not _is_string_object_dict(raw_session):
                raise PersistError("Invalid API key store JSON: ui session record must be an object")
            try:
                api_key_id = raw_session.get("api_key_id")
                api_key_secret_hash = raw_session.get("api_key_secret_hash")
                remembered = raw_session.get("remembered", False)
                if api_key_id is not None and not isinstance(api_key_id, str):
                    raise ValueError("API key session api_key_id must be a string")
                if api_key_secret_hash is not None and not isinstance(api_key_secret_hash, str):
                    raise ValueError("API key session api_key_secret_hash must be a string")
                if type(remembered) is not bool:
                    raise ValueError("API key session remembered flag must be a boolean")
                secret = _required_string_field(raw_session, "secret")
                bootstrap = raw_session.get("bootstrap", False)
                if type(bootstrap) is not bool:
                    raise ValueError("API key session bootstrap flag must be a boolean")
                store.__ui_sessions[secret] = UiSessionRecord(
                    secret=secret,
                    scopes=_normalize_scopes(raw_session.get("scopes", [])),
                    created_at=_required_string_field(raw_session, "created_at"),
                    expires_at=_required_string_field(raw_session, "expires_at"),
                    bootstrap=bootstrap,
                    remembered=remembered,
                    api_key_id=api_key_id,
                    api_key_secret_hash=api_key_secret_hash,
                )
            except KeyError as exc:
                raise PersistError("Invalid API key store JSON: missing field {}".format(exc)) from exc
            except ValueError as exc:
                raise PersistError("Invalid API key store JSON: {}".format(exc)) from exc

        return store

    def to_str(self) -> str:
        now = datetime.now(timezone.utc)
        self.__prune_expired_ui_sessions(now)
        self.__prune_bootstrap_proof(now)
        self.__prune_bootstrap_exchange(now)
        payload = {
            self.__KEY_VERSION: 3,
            self.__KEY_API_KEYS: [asdict(record) for record in self.__api_keys],
            self.__KEY_UI_SESSIONS: [
                asdict(record)
                for record in sorted(
                    self.__ui_sessions.values(),
                    key=lambda record: (record.created_at, record.secret)
                )
            ],
            self.__KEY_BROWSER_HANDOVER_CLAIMED_VERSION: self.__browser_handover_claimed_version,
        }
        return json.dumps(payload, indent=2)

    def __prune_expired_ui_sessions(self, now: datetime) -> None:
        expired_session_ids: List[str] = []
        for secret, record in self.__ui_sessions.items():
            if getattr(record, "remembered", False):
                continue
            try:
                if datetime.fromisoformat(record.expires_at) <= now:
                    expired_session_ids.append(secret)
            except ValueError:
                expired_session_ids.append(secret)
        for secret in expired_session_ids:
            self.__ui_sessions.pop(secret, None)

    def __prune_bootstrap_proof(self, now: datetime) -> None:
        if self.__bootstrap_proof is None:
            return

        try:
            if datetime.fromisoformat(self.__bootstrap_proof.expires_at) > now:
                return
        except ValueError:
            pass

        self.clear_bootstrap_proof(reason="expired")

    def __prune_bootstrap_exchange(self, now: datetime) -> None:
        if self.__bootstrap_exchange is None:
            return

        try:
            if datetime.fromisoformat(self.__bootstrap_exchange.expires_at) > now:
                return
        except ValueError:
            pass

        self.clear_bootstrap_exchange(reason="expired")

    def __sync_bootstrap_proof_artifact(self) -> None:
        if not self.__bootstrap_proof_path:
            return

        artifact_dir = os.path.dirname(self.__bootstrap_proof_path)
        if artifact_dir:
            os.makedirs(artifact_dir, exist_ok=True)

        if self.__bootstrap_proof is None:
            if os.path.exists(self.__bootstrap_proof_path):
                os.remove(self.__bootstrap_proof_path)
            return

        payload = {
            "proof": self.__bootstrap_proof.secret,
            "created_at": self.__bootstrap_proof.created_at,
            "expires_at": self.__bootstrap_proof.expires_at,
        }
        with open(self.__bootstrap_proof_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    @staticmethod
    def __configured_browser_handover_version(config: object) -> str:
        general_config = getattr(config, "general", None)
        if general_config is None:
            return ""
        browser_handover_version = getattr(general_config, "browser_handover_recovery_version", "")
        return browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
