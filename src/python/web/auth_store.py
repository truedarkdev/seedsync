# Copyright 2026, SeedSync Contributors, All rights reserved.

import binascii
import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from common import Persist, PersistError


_ALLOWED_SCOPES = {"read", "write", "stream", "admin"}
_HASH_ALGORITHM = "pbkdf2_sha256"
_HASH_ITERATIONS = 200000
_HASH_SALT_BYTES = 16
_UI_SESSION_TTL = timedelta(hours=12)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_scopes(scopes: Sequence[str]) -> List[str]:
    if not isinstance(scopes, (list, tuple, set)):
        raise ValueError("API key scopes must be a list of scope names")

    normalized = []
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


def _hash_secret(secret: str, salt: Optional[bytes] = None) -> str:
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
    scopes: List[str] = field(default_factory=list)
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
    scopes: List[str] = field(default_factory=list)
    created_at: str = ""
    expires_at: str = ""


class ApiKeyStore(Persist):
    __KEY_VERSION = "version"
    __KEY_API_KEYS = "api_keys"
    __KEY_LEGACY_TOKEN_COMPATIBILITY_ENABLED = "legacy_api_token_compatibility_enabled"

    def __init__(self, file_path: Optional[str] = None):
        self.__file_path = file_path
        self.__api_keys: List[ApiKeyRecord] = []
        self.__legacy_api_token_compatibility_enabled = True
        self.__ui_sessions: Dict[str, UiSessionRecord] = {}

    @property
    def file_path(self) -> Optional[str]:
        return self.__file_path

    def bind_file_path(self, file_path: str) -> None:
        self.__file_path = file_path

    @property
    def legacy_api_token_compatibility_enabled(self) -> bool:
        return self.__legacy_api_token_compatibility_enabled

    def set_legacy_api_token_compatibility_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("Legacy API token compatibility must be a boolean value")
        self.__legacy_api_token_compatibility_enabled = enabled
        self.save()

    @property
    def api_keys(self) -> List[ApiKeyRecord]:
        return list(self.__api_keys)

    @property
    def active_admin_key_count(self) -> int:
        return len([
            record for record in self.__api_keys
            if not record.is_revoked and "admin" in record.scopes
        ])

    def list_api_keys(self) -> List[Dict[str, object]]:
        return [record.to_public_dict() for record in self.__api_keys]

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

    def create_ui_session(self, scopes: Sequence[str]) -> UiSessionRecord:
        normalized_scopes = _normalize_scopes(scopes)
        now = datetime.now(timezone.utc)
        record = UiSessionRecord(
            secret=secrets.token_urlsafe(32),
            scopes=normalized_scopes,
            created_at=now.isoformat(timespec="seconds"),
            expires_at=(now + _UI_SESSION_TTL).isoformat(timespec="seconds"),
        )
        self.__ui_sessions[record.secret] = record
        self.__prune_expired_ui_sessions(now)
        return record

    def find_ui_session_by_secret(self, secret: str) -> Optional[UiSessionRecord]:
        now = datetime.now(timezone.utc)
        self.__prune_expired_ui_sessions(now)
        record = self.__ui_sessions.get(secret)
        if record is None:
            return None
        try:
            if datetime.fromisoformat(record.expires_at) <= now:
                self.__ui_sessions.pop(secret, None)
                return None
        except ValueError:
            self.__ui_sessions.pop(secret, None)
            return None
        return record

    def create_api_key(self, name: str, scopes: Sequence[str]) -> Dict[str, object]:
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
        self.save()
        return {"record": record, "secret": secret}

    def update_api_key(
        self,
        key_id: str,
        name: Optional[str] = None,
        scopes: Optional[Sequence[str]] = None
    ) -> ApiKeyRecord:
        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))

        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("API key name cannot be blank")
            record.name = name.strip()

        if scopes is not None:
            record.scopes = _normalize_scopes(scopes)

        record.updated_at = _utc_now_iso()
        self.save()
        return record

    def revoke_api_key(self, key_id: str) -> ApiKeyRecord:
        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))

        if record.revoked_at is None:
            now = _utc_now_iso()
            record.revoked_at = now
            record.updated_at = now
            self.save()
        return record

    def rotate_api_key(self, key_id: str) -> Dict[str, object]:
        record = self.get_api_key(key_id)
        if record is None:
            raise KeyError("API key '{}' not found".format(key_id))
        if record.is_revoked:
            raise ValueError("Cannot rotate a revoked API key")

        secret = secrets.token_urlsafe(32)
        record.secret_hash = _hash_secret(secret)
        record.updated_at = _utc_now_iso()
        self.save()
        return {"record": record, "secret": secret}

    def get_migration_state(self, config) -> Dict[str, object]:
        general_config = getattr(config, "general", None)
        legacy_api_token = getattr(general_config, "api_token", None) if general_config is not None else None
        legacy_configured = isinstance(legacy_api_token, str) and legacy_api_token.strip() != ""
        if not legacy_configured:
            legacy_state = "cleared"
        elif self.__legacy_api_token_compatibility_enabled:
            legacy_state = "enabled"
        else:
            legacy_state = "disabled"

        active_keys = [record for record in self.__api_keys if not record.is_revoked]
        return {
            "legacy_api_token": {
                "configured": legacy_configured,
                "compatibility_enabled": self.__legacy_api_token_compatibility_enabled,
                "state": legacy_state,
                "accepted_for_external_non_admin": legacy_configured and self.__legacy_api_token_compatibility_enabled,
            },
            "api_keys": {
                "total": len(self.__api_keys),
                "active": len(active_keys),
                "revoked": len(self.__api_keys) - len(active_keys),
            },
        }

    def save(self):
        if self.__file_path is None:
            return
        directory = os.path.dirname(self.__file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.to_file(self.__file_path)

    @classmethod
    def from_file(cls, file_path: str) -> "ApiKeyStore":
        store = super().from_file(file_path)
        store.__file_path = file_path
        return store

    @classmethod
    def from_str(cls, content: str) -> "ApiKeyStore":
        store = ApiKeyStore()
        try:
            payload = json.loads(content)
        except ValueError as exc:
            raise PersistError("Invalid API key store JSON: {}".format(exc)) from exc

        if not isinstance(payload, dict):
            raise PersistError("Invalid API key store JSON: expected an object")

        legacy_compatibility = payload.get(cls.__KEY_LEGACY_TOKEN_COMPATIBILITY_ENABLED, True)
        if type(legacy_compatibility) is not bool:
            raise PersistError(
                "Invalid API key store JSON: legacy_api_token_compatibility_enabled must be a boolean"
            )
        store.__legacy_api_token_compatibility_enabled = legacy_compatibility

        records = payload.get(cls.__KEY_API_KEYS, [])
        if not isinstance(records, list):
            raise PersistError("Invalid API key store JSON: api_keys must be a list")

        for raw_record in records:
            if not isinstance(raw_record, dict):
                raise PersistError("Invalid API key store JSON: api key record must be an object")
            try:
                store.__api_keys.append(
                    ApiKeyRecord(
                        id=raw_record["id"],
                        name=raw_record["name"],
                        scopes=_normalize_scopes(raw_record.get("scopes", [])),
                        secret_hash=raw_record["secret_hash"],
                        created_at=raw_record["created_at"],
                        updated_at=raw_record["updated_at"],
                        revoked_at=raw_record.get("revoked_at"),
                    )
                )
            except KeyError as exc:
                raise PersistError("Invalid API key store JSON: missing field {}".format(exc)) from exc
            except ValueError as exc:
                raise PersistError("Invalid API key store JSON: {}".format(exc)) from exc

        return store

    def to_str(self) -> str:
        payload = {
            self.__KEY_VERSION: 1,
            self.__KEY_LEGACY_TOKEN_COMPATIBILITY_ENABLED: self.__legacy_api_token_compatibility_enabled,
            self.__KEY_API_KEYS: [asdict(record) for record in self.__api_keys],
        }
        return json.dumps(payload, indent=2)

    def __prune_expired_ui_sessions(self, now: datetime) -> None:
        expired_session_ids = []
        for secret, record in self.__ui_sessions.items():
            try:
                if datetime.fromisoformat(record.expires_at) <= now:
                    expired_session_ids.append(secret)
            except ValueError:
                expired_session_ids.append(secret)
        for secret in expired_session_ids:
            self.__ui_sessions.pop(secret, None)
