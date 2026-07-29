# Copyright 2026, SeedSync Contributors, All rights reserved.

import binascii
import base64
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
from typing import Dict, List, Optional, Sequence, TypeGuard, TypedDict

from common import Persist, PersistError


_ALLOWED_SCOPES = {"read", "write", "stream", "admin", "bootstrap"}
_HASH_ALGORITHM = "pbkdf2_sha256"
_HASH_ITERATIONS = 200000
_HASH_SALT_BYTES = 16
_UI_SESSION_TTL = timedelta(hours=12)
_REMEMBERED_UI_SESSION_COOKIE_MAX_AGE = timedelta(days=3650)
_BOOTSTRAP_PROOF_TTL = timedelta(minutes=10)
_BOOTSTRAP_EXCHANGE_TTL = timedelta(minutes=5)
_COMPLETED_MIGRATION_AUTH_MAX_BYTES = 64 * 1024
_COMPLETED_MIGRATION_STORE_NAME = "api-keys.json"
_COMPLETED_MIGRATION_HISTORY_NAME = "api-keys.history.jsonl"
_COMPLETED_MIGRATION_CLAIM_MARKER_NAME = "migration-claimed-auth.json"
_COMPLETED_MIGRATION_CLAIM_MARKER_MAX_BYTES = 4096
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


def _history_file_path(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    history_root, _ = os.path.splitext(file_path)
    return "{}.history.jsonl".format(history_root)


def append_api_key_store_history(
    file_path: Optional[str], event: str, reason: str, **details: object
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
    except (OSError, TypeError, ValueError):
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
    root: Path, name: str, *, private: bool
) -> bytes | None:
    path = root / name
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

        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened_info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_info.st_mode)
                or opened_info.st_nlink != 1
                or (opened_info.st_dev, opened_info.st_ino) != (path_info.st_dev, path_info.st_ino)
                or opened_info.st_size > _COMPLETED_MIGRATION_AUTH_MAX_BYTES
            ):
                raise _completed_migration_auth_error()
            payload = os.read(descriptor, _COMPLETED_MIGRATION_AUTH_MAX_BYTES + 1)
            if len(payload) > _COMPLETED_MIGRATION_AUTH_MAX_BYTES:
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
    else:
        expected_details = {
            "api_key_count": 0,
            "active_api_key_count": 0,
            "ui_session_count": 0,
            "remembered_ui_session_count": 0,
            "browser_handover_claimed_version": "",
            "bootstrap_proof_present": True,
            "bootstrap_exchange_present": False,
        }
        expected_event, expected_reason = "store_saved", "persisted"

    if set(payload) != {"timestamp", "event", "reason", "store_file", "details"}:
        return False
    if (
        payload.get("event") != expected_event
        or payload.get("reason") != expected_reason
        or payload.get("store_file") != _COMPLETED_MIGRATION_STORE_NAME
        or not _valid_completed_migration_timestamp(payload.get("timestamp"))
    ):
        return False
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

    index = 0
    while index < len(entries):
        proof_count = 0
        while index < len(entries) and _completed_migration_history_entry(entries[index], "proof"):
            proof_count += 1
            index += 1
        if proof_count == 0:
            return False
        if index >= len(entries) or not _completed_migration_history_entry(entries[index], "saved"):
            return False
        index += 1
        while index < len(entries) and _completed_migration_history_entry(entries[index], "saved"):
            index += 1
        if index == len(entries):
            return True
        if not _completed_migration_history_entry(entries[index], "loaded"):
            return False
        index += 1
        # A normal restart records its exact empty-store load before opening
        # the ephemeral browser proof.  With no subsequent persisted auth
        # mutation, that terminal loader record is a safe pre-claim state.
        if index == len(entries):
            return True
    return False


def validate_completed_migration_preclaim_auth_state(config_dir: str | Path) -> None:
    """Accept only the empty, pre-claim auth residue normal startup may create.

    Migration application remains stricter: this is solely for validating a
    receipt that was already completed before normal startup opened first-run
    browser handover.  The accepted history is a closed grammar of events
    emitted by ``ensure_bootstrap_proof`` and empty-store persistence.
    """
    root = Path(config_dir)
    store = _read_completed_migration_auth_file(root, _COMPLETED_MIGRATION_STORE_NAME, private=True)
    history = _read_completed_migration_auth_file(root, _COMPLETED_MIGRATION_HISTORY_NAME, private=False)
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
    history_bytes = _read_completed_migration_auth_file(root, _COMPLETED_MIGRATION_HISTORY_NAME, private=False)
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


class ApiKeyStore(Persist):
    __KEY_VERSION = "version"
    __KEY_API_KEYS = "api_keys"
    __KEY_UI_SESSIONS = "ui_sessions"
    __KEY_BROWSER_HANDOVER_CLAIMED_VERSION = "browser_handover_claimed_version"

    def __init__(self, file_path: Optional[str] = None):
        self.__file_path = file_path
        self.__api_keys: List[ApiKeyRecord] = []
        self.__ui_sessions: Dict[str, UiSessionRecord] = {}
        self.__browser_handover_claimed_version = ""
        self.__state_lock = threading.RLock()
        self.__bootstrap_proof_path: Optional[str] = None
        self.__bootstrap_proof: Optional[BootstrapProofRecord] = None
        self.__bootstrap_exchange: Optional[BootstrapExchangeRecord] = None
        self.__completed_migration_transition_binding: Optional[Dict[str, str]] = None

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

    def complete_completed_migration_claim_transition(
        self, initial_admin_key_id: object, browser_handover_version: object,
    ) -> None:
        binding = self.__completed_migration_transition_binding
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        if binding is None:
            return
        if not isinstance(initial_admin_key_id, str) or not version:
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
        self.__completed_migration_transition_binding = None

    def __record_history_event(self, event: str, reason: str, **details: object) -> None:
        append_api_key_store_history(self.__file_path, event, reason, **details)

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

    def can_claim_initial_admin(self, browser_handover_version: object) -> bool:
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        if self.active_admin_key_count == 0:
            return True
        return self.__browser_handover_claimed_version != version

    def claim_initial_admin_if_available(self, browser_handover_version: object) -> bool:
        version = browser_handover_version.strip() if isinstance(browser_handover_version, str) else ""
        with self.__state_lock:
            if not self.can_claim_initial_admin(version):
                return False
            self.__browser_handover_claimed_version = version
            self.save()
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
        version = self.__get_browser_handover_version(config)
        return {
            "configured_version": version,
            "claimed_version": self.__browser_handover_claimed_version,
            "open": self.can_claim_initial_admin(version),
        }

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
        self.to_file(self.__file_path)
        self.__record_history_event("store_saved", "persisted", **self.__history_snapshot())

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
    def __get_browser_handover_version(config: object) -> str:
        general_config = getattr(config, "general", None)
        if general_config is None:
            return ""
        browser_handover_version = getattr(general_config, "browser_handover_recovery_version", "")
        return browser_handover_version if isinstance(browser_handover_version, str) else ""
