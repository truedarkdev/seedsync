#!/usr/bin/env python3
"""Evidence primitives for the retained v0.8.6 upgrade ship-readiness lab.

This module deliberately has no SeedSync imports.  It can therefore inventory a
legacy config before the current image is allowed to parse or normalize it.
All output is JSON and secrets are replaced before an artifact is written.
"""
from __future__ import annotations

import argparse
import configparser
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
import tarfile
import tempfile
import time
from urllib.parse import unquote
from typing import Any
import uuid
import zipfile
import zlib

_SECRET_NAME = r"[A-Za-z0-9_.-]*(?:password|secret|token|api[_-]?key|credential)[A-Za-z0-9_.-]*"
_SECRET_HEADER = r"(?:authorization|proxy-authorization|cookie2?|set-cookie2?|x-(?:api-key|auth-token|access-token))"
_SECRET_FIELD = rf"(?:{_SECRET_NAME}|{_SECRET_HEADER})"
SECRET = re.compile(
    rf"(?im)(?P<key>[\"']?{_SECRET_FIELD}[\"']?)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\r\n,;}\]\"']+)"
)
SECRET_KEY = re.compile(rf"(?i){_SECRET_FIELD}")
SECRET_QUERY = re.compile(rf"(?i)(?P<prefix>[?&]{_SECRET_NAME}=)[^&#\s\"']*")
SECRET_HEADER_LINE = re.compile(rf"(?im)^(?P<prefix>\s*[\"']?{_SECRET_HEADER}[\"']?\s*[:=]\s*).*$")
SECRET_URI = re.compile(r"(?i)(?P<prefix>[a-z][a-z0-9+.-]*://[^/@:\s]+:)[^@/\s]*@")
KNOWN_LAB_SECRET = re.compile(r"remotepass", re.IGNORECASE)
MATRIX = (
    ("before-legacy-ui-api-model", "pinned v0.8.6 UI/API/model and parsed settings are captured", "before"),
    ("before-filesystem-inventory", "config and fixture roots have exact hashes, sizes and modes", "before"),
    ("migration-guards", "host/origin/body/CSRF and single-flight guards reject hostile requests", "migration"),
    ("migration-retained-backup", "complete retained backup manifest, digests and modes are valid", "migration"),
    ("migration-transform", "one deterministic Default pair, current defaults, no API keys and receipt", "migration"),
    ("migration-restart-retry", "running/restart/interruption/retry contracts have focused evidence", "migration"),
    ("after-first-claim", "automatic handoff and explicit first browser claim succeed", "after"),
    ("after-authenticated-api", "settings, path pairs, status, logs and model are readable after claim", "after"),
    ("after-legacy-values", "legacy values persist and current defaults are explicitly injected", "after"),
    ("after-remembered-browser", "remembered browser survives a normal-runtime restart", "after"),
    ("after-scan-model", "real remote scan and path-aware model settle", "after"),
    ("after-transfer-resume", "queue, transfer/progress, completion and partial resume are observed", "after"),
    ("after-extract-autoqueue", "archive/nonarchive extraction and AutoQueue persistence are observed", "after"),
    ("after-safe-operations", "validate, cleanup/delete policy, exclusions and backend defaults are usable", "after"),
    ("after-notification-redaction", "notification configuration reads back without secret disclosure or delivery", "after"),
    ("after-files-pagination", "all-files and pagination smoke is captured", "after"),
    ("restore-offline", "supported offline restore is invoked only while runtime is stopped", "restore"),
    ("restore-exact-inventory", "old config-root hashes and modes exactly match the before inventory", "restore"),
    ("restore-infrastructure", "current receipt/state/generated files are removed as restore contract requires", "restore"),
    ("restore-pinned-reboot", "pinned v0.8.6 boots and representative UI/API/model/AutoQueue behavior returns", "restore"),
    ("focused-security-probes", "tamper, partial backup, active runtime and malformed request probes are recorded", "security"),
)


def redact(value: str) -> str:
    def redact_assignment(match: re.Match[str]) -> str:
        replacement = '"<redacted>"' if match.group("value").startswith('"') else "'<redacted>'" if match.group("value").startswith("'") else "<redacted>"
        return "{}{}{}".format(match.group("key"), match.group("separator").strip(), replacement)
    value = SECRET.sub(redact_assignment, value)
    value = SECRET_QUERY.sub(lambda match: "{}<redacted>".format(match.group("prefix")), value)
    value = SECRET_URI.sub(lambda match: "{}<redacted>@".format(match.group("prefix")), value)
    value = SECRET_HEADER_LINE.sub(lambda match: "{}<redacted>".format(match.group("prefix")), value)
    return KNOWN_LAB_SECRET.sub("<redacted>", value)


def json_dump(path: Path, payload: Any) -> None:
    if str(path) == "-":
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def validate_archive(path: Path, output: Path) -> None:
    """Post-validate the exact protected tar without extracting any member."""
    seen: set[str] = set()
    members: list[dict[str, Any]] = []
    with tarfile.open(path, mode="r:") as archive:
        for member in archive.getmembers():
            raw_name = member.name.replace("\\", "/")
            parts = [part for part in raw_name.split("/") if part not in ("", ".")]
            if raw_name.startswith("/") or any(part == ".." for part in parts):
                raise SystemExit("protected archive contains an unsafe path")
            normalized = "/".join(parts) or "."
            if normalized in seen:
                raise SystemExit("protected archive contains a duplicate member")
            seen.add(normalized)
            if not (member.isdir() or member.isreg()):
                raise SystemExit("protected archive contains a link, device, or special member")
            members.append({
                "path": normalized,
                "kind": "directory" if member.isdir() else "file",
                "mode": format(member.mode & 0o777, "04o"),
                "size": member.size,
            })
    json_dump(output, {
        "schema": 1,
        "archive": path.name,
        "member_count": len(members),
        "members": members,
        "postvalidated_without_extraction": True,
    })


def _archive_rows(path: Path) -> dict[str, dict[str, Any]]:
    """Read a tar without extraction and normalize it to inventory-like rows."""
    rows: dict[str, dict[str, Any]] = {}
    with tarfile.open(path, mode="r:") as archive:
        for member in archive.getmembers():
            raw_name = member.name.replace("\\", "/")
            parts = [part for part in raw_name.split("/") if part not in ("", ".")]
            if raw_name.startswith("/") or any(part == ".." for part in parts):
                raise ValueError("protected archive contains an unsafe path")
            normalized = "/".join(parts)
            if not normalized:
                if not member.isdir():
                    raise ValueError("protected archive root is not a directory")
                continue
            if normalized in rows:
                raise ValueError("protected archive contains a duplicate member")
            if member.isdir():
                rows[normalized] = {"path": normalized, "type": "directory", "mode": format(member.mode & 0o777, "04o")}
                continue
            if not member.isreg():
                raise ValueError("protected archive contains a link, device, or special member")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("protected archive file member is unreadable")
            digest = hashlib.sha256()
            size = 0
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            if size != member.size:
                raise ValueError("protected archive file size changed while reading")
            rows[normalized] = {"path": normalized, "type": "file", "mode": format(member.mode & 0o777, "04o"), "size": size, "sha256": digest.hexdigest()}
    return rows


def _archive_inventory_binding(archive: Path, inventory_payload: dict[str, Any]) -> dict[str, Any]:
    """Return safe binding metadata or fail closed on any inventory mismatch."""
    entries = inventory_payload.get("entries")
    if inventory_payload.get("schema") != 1 or not isinstance(entries, list):
        raise ValueError("inventory payload is malformed")
    expected: dict[str, dict[str, Any]] = {}
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or row.get("type") not in {"directory", "file"}:
            raise ValueError("inventory row is malformed")
        path = row["path"]
        if not path or path in expected:
            raise ValueError("inventory has an invalid or duplicate path")
        expected[path] = row
    actual = _archive_rows(archive)
    differences = sorted(path for path in expected.keys() | actual.keys() if expected.get(path) != actual.get(path))
    if differences:
        raise ValueError("protected archive does not exactly match inventory")
    inventory_digest = hashlib.sha256(json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "schema": 1,
        "archive": archive.name,
        "exact_inventory_match": True,
        "entry_count": len(expected),
        "file_count": sum(row["type"] == "file" for row in expected.values()),
        "directory_count": sum(row["type"] == "directory" for row in expected.values()),
        "inventory_sha256": inventory_digest,
        "validated_without_extraction": True,
    }


def bind_archive_inventory(archive: Path, inventory_payload: dict[str, Any], output: Path) -> None:
    """Fail closed unless the tar is an exact, no-extraction copy of inventory."""
    json_dump(output, _archive_inventory_binding(archive, inventory_payload))


def verify_protected_archive(archive: Path, inventory_payload: dict[str, Any], manifest_payload: dict[str, Any], output: Path) -> None:
    """Recheck the retained archive immediately before a read-only consumer uses it."""
    expected_digest = manifest_payload.get("sha256")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ValueError("protected archive manifest digest is invalid")
    expected_inventory_digest = manifest_payload.get("inventory_sha256")
    if not isinstance(expected_inventory_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_inventory_digest):
        raise ValueError("protected archive manifest inventory binding is invalid")
    actual_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual_digest != expected_digest:
        raise ValueError("protected archive digest differs from its safe manifest")
    binding = _archive_inventory_binding(archive, inventory_payload)
    if binding["inventory_sha256"] != expected_inventory_digest:
        raise ValueError("protected archive inventory differs from its safe manifest")
    json_dump(output, {"schema": 1, "archive": archive.name, "sha256": actual_digest,
                       "digest_matches_manifest": True, "exact_inventory_match": True,
                       "entry_count": binding["entry_count"], "validated_without_extraction": True})


_DOWNLOAD_ARCHIVE_MAX_BYTES = 32 * 1024 * 1024
_DOWNLOAD_ARCHIVE_MAX_MEMBERS = 64
_DOWNLOAD_ARCHIVE_MAX_MEMBER_BYTES = 16 * 1024 * 1024
_DOWNLOAD_ARCHIVE_GENERATED_PATTERN = b"seedsync-v086-transient-"


class DownloadArchiveError(ValueError):
    """A downloaded fixture archive does not meet its bounded contract."""


def _safe_zip_member_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise DownloadArchiveError("fixture member path is unsafe")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or parts[0].endswith(":"):
        raise DownloadArchiveError("fixture member path is unsafe")
    normalized = str(PurePosixPath(*parts))
    if normalized != value:
        raise DownloadArchiveError("fixture member path is not canonical")
    return normalized


def _fixture_payload_contract(value: Any) -> dict[str, Any]:
    """Hash one manifest payload without materializing it or exposing content."""
    digest = hashlib.sha256()
    crc = 0
    size = 0

    def consume(chunk: bytes) -> None:
        nonlocal crc, size
        digest.update(chunk)
        crc = zlib.crc32(chunk, crc)
        size += len(chunk)

    if isinstance(value, str):
        consume(value.encode("utf-8"))
    elif isinstance(value, dict) and set(value) == {"generated_bytes"}:
        requested = value["generated_bytes"]
        if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= _DOWNLOAD_ARCHIVE_MAX_MEMBER_BYTES:
            raise DownloadArchiveError("fixture generated payload is out of bounds")
        remaining = requested
        while remaining:
            chunk = _DOWNLOAD_ARCHIVE_GENERATED_PATTERN[:min(remaining, len(_DOWNLOAD_ARCHIVE_GENERATED_PATTERN))]
            consume(chunk)
            remaining -= len(chunk)
    else:
        raise DownloadArchiveError("fixture archive payload is malformed")
    return {"size": size, "sha256": digest.hexdigest(), "crc32": format(crc & 0xFFFFFFFF, "08x")}


def _fixture_download_archive_contract(manifest_path: Path, case_id: str) -> dict[str, dict[str, Any]]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DownloadArchiveError("fixture manifest is unreadable") from error
    cases = manifest.get("cases") if isinstance(manifest, dict) else None
    if not isinstance(cases, list):
        raise DownloadArchiveError("fixture manifest cases are malformed")
    matches = [case for case in cases if isinstance(case, dict) and case.get("id") == case_id]
    if len(matches) != 1:
        raise DownloadArchiveError("fixture archive case is ambiguous")
    remote = matches[0].get("remote")
    files = remote.get("archive") if isinstance(remote, dict) else None
    if not isinstance(files, dict) or not files or len(files) > _DOWNLOAD_ARCHIVE_MAX_MEMBERS:
        raise DownloadArchiveError("fixture archive members are malformed")
    expected: dict[str, dict[str, Any]] = {}
    for raw_path, value in files.items():
        path = _safe_zip_member_path(raw_path)
        if path in expected:
            raise DownloadArchiveError("fixture archive has duplicate members")
        contract = _fixture_payload_contract(value)
        expected[path] = contract
    if sum(contract["size"] for contract in expected.values()) > _DOWNLOAD_ARCHIVE_MAX_MEMBER_BYTES:
        raise DownloadArchiveError("fixture archive total is out of bounds")
    return expected


def _safe_download_archive_failure(error: BaseException) -> str:
    if isinstance(error, OSError):
        return "archive-unreadable"
    if isinstance(error, zipfile.BadZipFile):
        return "archive-invalid"
    return "archive-contract-mismatch"


def verify_download_archive(archive_path: Path, fixture_manifest: Path, case_id: str, output: Path) -> None:
    """Verify a downloaded ZIP against one fixture case without extracting it."""
    try:
        info = archive_path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > _DOWNLOAD_ARCHIVE_MAX_BYTES:
            raise DownloadArchiveError("download archive is not a bounded regular file")
        expected = _fixture_download_archive_contract(fixture_manifest, case_id)
        observed: dict[str, dict[str, Any]] = {}
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            members = archive.infolist()
            if not members or len(members) > _DOWNLOAD_ARCHIVE_MAX_MEMBERS:
                raise DownloadArchiveError("download archive member count is out of bounds")
            for member in members:
                if member.is_dir() or member.flag_bits & 0x1:
                    raise DownloadArchiveError("download archive has an encrypted or directory member")
                path = _safe_zip_member_path(member.filename)
                mode = (member.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if file_type not in (0, stat.S_IFREG) or path in observed:
                    raise DownloadArchiveError("download archive has a duplicate or special member")
                contract = expected.get(path)
                if contract is None or member.file_size != contract["size"] or member.file_size > _DOWNLOAD_ARCHIVE_MAX_MEMBER_BYTES:
                    raise DownloadArchiveError("download archive members differ from fixture")
                if format(member.CRC & 0xFFFFFFFF, "08x") != contract["crc32"]:
                    raise DownloadArchiveError("download archive CRC differs from fixture")
                digest = hashlib.sha256()
                crc = 0
                size = 0
                with archive.open(member, mode="r") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        crc = zlib.crc32(chunk, crc)
                        size += len(chunk)
                if size != contract["size"] or digest.hexdigest() != contract["sha256"] or format(crc & 0xFFFFFFFF, "08x") != contract["crc32"]:
                    raise DownloadArchiveError("download archive content differs from fixture")
                observed[path] = {"path": path, **contract}
        if set(observed) != set(expected):
            raise DownloadArchiveError("download archive members differ from fixture")
    except (DownloadArchiveError, OSError, zipfile.BadZipFile) as error:
        json_dump(output, {"schema": 1, "archive": archive_path.name, "case_id": case_id, "status": "failed",
                           "failure": _safe_download_archive_failure(error), "validated_without_extraction": True})
        raise ValueError("download archive verification failed") from None
    json_dump(output, {"schema": 1, "archive": archive_path.name, "case_id": case_id, "status": "passed",
                       "member_count": len(observed), "members": [observed[path] for path in sorted(observed)],
                       "exact_fixture_match": True, "validated_without_extraction": True})


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "<redacted>" if SECRET_KEY.search(str(key)) else redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def response_shape(value: Any) -> dict[str, Any]:
    """Describe an HTTP payload without retaining any of its values."""
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(map(str, value))}
    if isinstance(value, list):
        return {"type": "array", "length": len(value)}
    return {"type": type(value).__name__}


def failure_summary(error: BaseException) -> dict[str, Any]:
    """Keep failure artifacts useful without serializing exception text."""
    return {"type": type(error).__name__, "message_present": bool(getattr(error, "args", ()))}


_SAFE_MIGRATION_TEXT = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")


def _safe_migration_text(value: Any, *, allowed: set[str] | None = None) -> str | None:
    if not isinstance(value, str) or not _SAFE_MIGRATION_TEXT.fullmatch(value):
        return None
    if allowed is not None and value not in allowed:
        return None
    return value


def migration_status_evidence(payload: Any) -> dict[str, Any]:
    """Select the non-secret migration facts permitted in retained evidence."""
    if not isinstance(payload, dict):
        raise ValueError("migration status payload must be an object")
    features = payload.get("features")
    feature_keys = []
    if isinstance(features, list):
        feature_keys = [key for item in features if isinstance(item, dict)
                        if (key := _safe_migration_text(item.get("key"))) is not None]
    capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
    backup = payload.get("backup") if isinstance(payload.get("backup"), dict) else {}
    operation = payload.get("operation") if isinstance(payload.get("operation"), dict) else {}
    return {
        "schema": 1,
        "mode": _safe_migration_text(payload.get("mode"), allowed={"migration_required"}),
        "state": _safe_migration_text(payload.get("state"), allowed={"required", "running", "failed", "complete"}),
        "migration_id": _safe_migration_text(payload.get("migration_id")),
        "source_schema": _safe_migration_text(payload.get("source_schema")),
        "target_schema": _safe_migration_text(payload.get("target_schema")),
        "features": feature_keys,
        "retryable": bool(payload.get("retryable")),
        "capabilities": {name: bool(capabilities.get(name)) for name in ("apply", "retry", "restore")},
        "backup": {
            "status": _safe_migration_text(backup.get("status"), allowed={"created_before_apply", "ready"}),
            "complete_restore_ready": bool(backup.get("complete_restore_ready")),
        },
        "operation": {
            "status": _safe_migration_text(operation.get("status"), allowed={"idle", "running", "succeeded", "failed"}),
        },
    }


def migration_terminal_transition_evidence(payload: Any) -> dict[str, Any]:
    """Prove the completed migration runtime must be replaced before normal routes."""
    if isinstance(payload, dict):
        final = payload.get("final")
        nested = final.get("migration_status") if isinstance(final, dict) else None
        if isinstance(nested, dict):
            payload = nested
    status = migration_status_evidence(payload)
    if (
        status.get("state") != "complete"
        or status.get("operation", {}).get("status") != "succeeded"
        or status.get("backup", {}).get("status") != "ready"
        or status.get("backup", {}).get("complete_restore_ready") is not True
    ):
        raise ValueError("migration has not reached the terminal complete state")
    return {
        "schema": 1,
        "migration_state": "complete",
        "migration_operation": "succeeded",
        "normal_runtime_transition": "container-restart-required",
    }


def normal_runtime_transition_evidence(
    migration_status: Any, status: Any, bootstrap: Any,
) -> dict[str, Any]:
    """Accept only route facts that distinguish normal WebApp from MigrationWebApp."""
    if any(type(value) is not int or value < 100 or value > 599 for value in (migration_status, status, bootstrap)):
        raise ValueError("normal runtime transition HTTP statuses are invalid")
    if migration_status != 404:
        raise ValueError("migration runtime remains active after the restart boundary")
    if status not in {200, 401}:
        raise ValueError("normal current status route is not ready after the restart boundary")
    if bootstrap != 200:
        raise ValueError("trusted browser bootstrap route is not ready after the restart boundary")
    return {
        "schema": 1,
        "migration_runtime": "inactive",
        "status_route": "ready" if status == 200 else "api_key_required",
        "bootstrap_route": "ready",
    }


def preclaim_auth_challenge_evidence(http_status: Any, headers: Any, body: Any) -> dict[str, Any]:
    """Validate the unauthenticated current-runtime contract without retaining its body."""
    if type(http_status) is not int:
        raise ValueError("pre-claim status response must include an integer HTTP status")
    if not isinstance(headers, dict):
        raise ValueError("pre-claim status response headers must be an object")
    if not isinstance(body, str):
        raise ValueError("pre-claim status response body must be text")
    content_type = headers.get("content-type")
    if http_status != 401:
        raise ValueError("pre-claim status response must be HTTP 401")
    if not isinstance(content_type, str) or not re.fullmatch(
        r"text/html(?:\s*;\s*[^;\r\n]+)*\s*", content_type, flags=re.IGNORECASE
    ):
        raise ValueError("pre-claim status response must have text/html content type")
    required_markers = ("<h1>Error: 401 Unauthorized</h1>", "Missing API token")
    if not all(marker in body for marker in required_markers):
        raise ValueError("pre-claim status response was not the expected API-token challenge")
    return {
        "schema": 1,
        "http_status": 401,
        "auth_state": "api_key_required",
        "content_type": "text/html",
    }


def read_http_headers(path: Path) -> dict[str, str]:
    """Parse the final response header block emitted by curl -D."""
    headers: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return headers


_DENIED_RETAINED_ARTIFACT_PARTS = {"browser-profile", "cookies", "cookies-journal"}
_AUDIT_FINDING_KINDS = {
    "obsolete-protected-host-path", "obsolete-protected-host-artifact", "denied-artifact",
    "canary-quarantined", "unreadable", "secret-pattern-remediated",
    "secret-pattern-quarantined", "invalid-status-artifact", "raw-status-artifact",
    "prior-audit-invalid",
    "screenshot-attestation-invalid", "log-publication-invalid",
}
_SCREENSHOT_SAFETY_POLICY_VERSION = 1
_SCREENSHOT_MAX_BYTES = 8 * 1024 * 1024
_SCREENSHOT_MAX_DIMENSION = 16384
_SCREENSHOT_MAX_PIXELS = 64 * 1024 * 1024
_LOG_PUBLICATION_POLICY_VERSION = 1
_LOG_PUBLICATION_MAX_BYTES = 512 * 1024
_LOG_PUBLICATION_RELATIVE_PATH = "logs/seedsync.log"
_LOG_REDACTION_CLASSES = {
    "assignment": SECRET,
    "query": SECRET_QUERY,
    "uri_credentials": SECRET_URI,
    "header": SECRET_HEADER_LINE,
    "lab_literal": KNOWN_LAB_SECRET,
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SAFE_SCREENSHOT_PATH = re.compile(
    r"evidence/ship-readiness/(?:after-(?:bootstrap|first-claim|files|restart|restart-files|restore-bootstrap|restore-legacy-files)|before-legacy-files|browser(?:-(?:claim|reuse|legacy(?:-restore)?))?-failure-[a-z0-9-]{1,100})\.png\Z"
)
_SAFE_SCREENSHOT_RUN_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")
_SAFE_SCREENSHOT_ROUTE = re.compile(r"/[A-Za-z0-9/_-]{0,160}\Z")
_SAFE_SCREENSHOT_STATE = re.compile(r"[a-z][a-z0-9-]{1,80}\Z")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _private_regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink() and stat.S_IMODE(info.st_mode) & 0o077 == 0


def _strict_png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) > _SCREENSHOT_MAX_BYTES or not payload.startswith(_PNG_SIGNATURE):
        raise ValueError("invalid PNG")
    offset, seen_ihdr, seen_idat, seen_iend = len(_PNG_SIGNATURE), False, False, False
    width = height = channels = 0
    idat = bytearray()
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise ValueError("truncated PNG chunk")
        length = int.from_bytes(payload[offset:offset + 4], "big")
        if length > _SCREENSHOT_MAX_BYTES or offset + 12 + length > len(payload):
            raise ValueError("invalid PNG chunk length")
        chunk_type = payload[offset + 4:offset + 8]
        chunk_data = payload[offset + 8:offset + 8 + length]
        crc = int.from_bytes(payload[offset + 8 + length:offset + 12 + length], "big")
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != crc:
            raise ValueError("invalid PNG CRC")
        offset += 12 + length
        if chunk_type == b"IHDR":
            if seen_ihdr or seen_idat or seen_iend or length != 13:
                raise ValueError("invalid PNG IHDR")
            width, height, bit_depth, color_type, compression, filter_method, interlace = (
                int.from_bytes(chunk_data[:4], "big"), int.from_bytes(chunk_data[4:8], "big"),
                chunk_data[8], chunk_data[9], chunk_data[10], chunk_data[11], chunk_data[12],
            )
            if (width < 1 or height < 1 or width > _SCREENSHOT_MAX_DIMENSION or height > _SCREENSHOT_MAX_DIMENSION
                    or width * height > _SCREENSHOT_MAX_PIXELS or bit_depth != 8 or color_type not in {2, 6}
                    or (compression, filter_method, interlace) != (0, 0, 0)):
                raise ValueError("unsafe PNG IHDR")
            channels = 3 if color_type == 2 else 4
            seen_ihdr = True
        elif chunk_type == b"IDAT":
            if not seen_ihdr or seen_iend:
                raise ValueError("invalid PNG IDAT")
            seen_idat = True
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            if not seen_ihdr or not seen_idat or seen_iend or length != 0 or offset != len(payload):
                raise ValueError("invalid PNG IEND")
            seen_iend = True
        else:
            # Retained screenshots deliberately allow no ancillary chunks:
            # text-bearing chunks, unknown metadata, and polyglot carriers fail closed.
            raise ValueError("unexpected PNG chunk")
    if not seen_iend:
        raise ValueError("missing PNG IEND")
    try:
        decoded = zlib.decompress(bytes(idat))
    except zlib.error as error:
        raise ValueError("invalid PNG image data") from error
    if len(decoded) != height * (1 + width * channels):
        raise ValueError("invalid PNG pixel data")
    return width, height


def _regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink()


def _private_source_summary(path: Path) -> dict[str, object] | None:
    """Return the strict private-source contract without exposing its path."""
    try:
        info = path.lstat()
    except OSError:
        return None
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        return None
    return {"mode": "0600", "owner_uid": os.geteuid(), "hardlinks": 1, "regular": True}


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Publish only fully prepared non-secret output into the retained tree."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(".{}.publish-{}".format(path.name, uuid.uuid4().hex))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _stable_private_log_snapshot(source: Path) -> tuple[bytes, dict[str, object]]:
    """Read a bounded snapshot only if the private source stayed unchanged."""
    for _ in range(3):
        privacy = _private_source_summary(source)
        if privacy is None:
            raise ValueError("private log source is missing or unsafe")
        before = source.stat()
        if before.st_size > _LOG_PUBLICATION_MAX_BYTES:
            raise ValueError("private log source exceeds publication bound")
        payload = source.read_bytes()
        after = source.stat()
        if (len(payload) == before.st_size == after.st_size
                and (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
                and _private_source_summary(source) == privacy):
            return payload, privacy
    raise ValueError("private log source changed during bounded snapshot")


def _log_redaction_class_counts(text: str) -> dict[str, int]:
    return {name: len(pattern.findall(text)) for name, pattern in _LOG_REDACTION_CLASSES.items()}


def publish_private_log_snapshot(source: Path, root: Path, run_id: str) -> dict[str, object]:
    """Publish a stable, centrally redacted private app-log snapshot and record."""
    if not _SAFE_SCREENSHOT_RUN_ID.fullmatch(run_id) or root.name != run_id:
        raise ValueError("log publication run identity is invalid")
    payload, privacy = _stable_private_log_snapshot(source)
    text = payload.decode("utf-8", errors="replace")
    class_counts = _log_redaction_class_counts(text)
    redacted = redact(text)
    if _retained_secret_hint(redacted):
        raise ValueError("redacted log still contains a secret pattern")
    destination = root / _LOG_PUBLICATION_RELATIVE_PATH
    source_hash = hashlib.sha256(payload).hexdigest()
    published = redacted.encode("utf-8")
    published_hash = hashlib.sha256(published).hexdigest()
    _atomic_write_bytes(destination, published)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != published_hash:
        raise ValueError("published log hash differs from prepared snapshot")
    record = {
        "schema": 1,
        "policy_version": _LOG_PUBLICATION_POLICY_VERSION,
        "run_id": run_id,
        "relative_path": _LOG_PUBLICATION_RELATIVE_PATH,
        "source_backend": "wsl-private-posix",
        "source_privacy": privacy,
        "source_snapshot_sha256": source_hash,
        "published_sha256": published_hash,
        "redaction_pattern_classes": class_counts,
        "published_safe": True,
    }
    _atomic_write_bytes(destination.with_name(destination.name + ".publication.json"),
                        (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return record


def _valid_published_log(root: Path, log: Path, private_source: Path | None = None) -> bool:
    try:
        if log.relative_to(root).as_posix() != _LOG_PUBLICATION_RELATIVE_PATH or not _regular_file(log):
            return False
        record_path = log.with_name(log.name + ".publication.json")
        if not _regular_file(record_path):
            return False
        record = json.loads(record_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_object)
        expected_keys = {
            "schema", "policy_version", "run_id", "relative_path", "source_backend", "source_privacy",
            "source_snapshot_sha256", "published_sha256", "redaction_pattern_classes", "published_safe",
        }
        expected_privacy = {"mode": "0600", "owner_uid": os.geteuid(), "hardlinks": 1, "regular": True}
        classes = record.get("redaction_pattern_classes") if isinstance(record, dict) else None
        if (not isinstance(record, dict) or set(record) != expected_keys or record.get("schema") != 1
                or record.get("policy_version") != _LOG_PUBLICATION_POLICY_VERSION
                or record.get("run_id") != root.name or not _SAFE_SCREENSHOT_RUN_ID.fullmatch(root.name)
                or record.get("relative_path") != _LOG_PUBLICATION_RELATIVE_PATH
                or record.get("source_backend") != "wsl-private-posix"
                or record.get("source_privacy") != expected_privacy or record.get("published_safe") is not True
                or not isinstance(classes, dict) or set(classes) != set(_LOG_REDACTION_CLASSES)
                or any(type(count) is not int or count < 0 for count in classes.values())):
            return False
        expected_hashes = (record.get("source_snapshot_sha256"), record.get("published_sha256"))
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in expected_hashes):
            return False
        published = log.read_bytes()
        if hashlib.sha256(published).hexdigest() != record["published_sha256"]:
            return False
        if private_source is None:
            return False
        source_payload, privacy = _stable_private_log_snapshot(private_source)
        redacted = redact(source_payload.decode("utf-8", errors="replace")).encode("utf-8")
        return (privacy == expected_privacy
                and hashlib.sha256(source_payload).hexdigest() == record["source_snapshot_sha256"]
                and _log_redaction_class_counts(source_payload.decode("utf-8", errors="replace")) == classes
                and redacted == published and not _retained_secret_detected(log))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _valid_screenshot_attestation(root: Path, image: Path, *, require_private: bool = True) -> bool:
    try:
        relative = image.relative_to(root).as_posix()
        valid_file = _private_regular_file if require_private else _regular_file
        if not _SAFE_SCREENSHOT_PATH.fullmatch(relative) or not valid_file(image):
            return False
        sidecar = image.with_name(image.name + ".safety.json")
        if not valid_file(sidecar):
            return False
        payload = image.read_bytes()
        width, height = _strict_png_dimensions(payload)
        attestation = json.loads(sidecar.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_object)
        expected_keys = {
            "schema", "policy_version", "run_id", "relative_path", "sha256", "width", "height",
            "route", "state", "captured_at", "secret_exposure",
        }
        if not isinstance(attestation, dict) or set(attestation) != expected_keys:
            return False
        timestamp = attestation.get("captured_at")
        if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", timestamp):
            return False
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return (
            attestation.get("schema") == 1
            and attestation.get("policy_version") == _SCREENSHOT_SAFETY_POLICY_VERSION
            and attestation.get("run_id") == root.name
            and isinstance(attestation.get("run_id"), str) and _SAFE_SCREENSHOT_RUN_ID.fullmatch(attestation["run_id"]) is not None
            and attestation.get("relative_path") == relative
            and attestation.get("sha256") == hashlib.sha256(payload).hexdigest()
            and attestation.get("width") == width and attestation.get("height") == height
            and isinstance(attestation.get("route"), str) and _SAFE_SCREENSHOT_ROUTE.fullmatch(attestation["route"]) is not None
            and isinstance(attestation.get("state"), str) and _SAFE_SCREENSHOT_STATE.fullmatch(attestation["state"]) is not None
            and attestation.get("secret_exposure") is False
        )
    except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _is_drvfs_evidence_root(root: Path) -> bool:
    """Detect the WSL-mounted host path where POSIX mode bits are not authoritative."""
    resolved = root.resolve()
    return sys.platform.startswith("linux") and str(resolved).startswith("/mnt/")


def _valid_published_screenshot(root: Path, image: Path) -> bool:
    try:
        if not _valid_screenshot_attestation(root, image, require_private=False):
            return False
        record_path = image.with_name(image.name + ".publication.json")
        if not _regular_file(record_path):
            return False
        record = json.loads(record_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_object)
        relative = image.relative_to(root).as_posix()
        sidecar = image.with_name(image.name + ".safety.json")
        expected_keys = {
            "schema", "policy_version", "run_id", "relative_path", "source_backend", "source_privacy",
            "source_sha256", "destination_sha256", "attestation_sha256", "published_safe",
        }
        privacy = record.get("source_privacy") if isinstance(record, dict) else None
        if not isinstance(record, dict) or set(record) != expected_keys or privacy != {
            "mode": "0600", "owner_uid": os.geteuid(), "hardlinks": 1, "regular": True,
        }:
            return False
        image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
        sidecar_hash = hashlib.sha256(sidecar.read_bytes()).hexdigest()
        return (
            record.get("schema") == 1 and record.get("policy_version") == _SCREENSHOT_SAFETY_POLICY_VERSION
            and record.get("run_id") == root.name and _SAFE_SCREENSHOT_RUN_ID.fullmatch(str(record.get("run_id"))) is not None
            and record.get("relative_path") == relative and record.get("source_backend") == "wsl-private-posix"
            and record.get("source_sha256") == image_hash and record.get("destination_sha256") == image_hash
            and record.get("attestation_sha256") == sidecar_hash and record.get("published_safe") is True
        )
    except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _safe_audit_path(path: Path) -> str:
    parts = []
    for part in path.parts:
        if part in ("", ".", "..") or SECRET_KEY.search(part) or re.search(_SECRET_HEADER, part, re.IGNORECASE):
            parts.append("<redacted-path>")
            continue
        parts.append("".join(character if character.isascii() and (character.isalnum() or character in "._-") else "_" for character in part)[:160])
    return redact("/".join(parts))


def _historical_audit_findings(output: Path) -> list[dict[str, str]]:
    """Accept only the prior report shape produced by this helper."""
    if not output.exists():
        return []
    try:
        prior = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [{"kind": "prior-audit-invalid", "path": "retained-run-audit.json"}]
    expected_keys = {
        "schema", "passed", "current_passed", "scanned_files", "protected_roots",
        "findings", "current_findings", "cumulative_findings",
    }
    if not isinstance(prior, dict) or set(prior) != expected_keys or prior.get("schema") != 3:
        return [{"kind": "prior-audit-invalid", "path": "retained-run-audit.json"}]
    if (not isinstance(prior["passed"], bool) or not isinstance(prior["current_passed"], bool)
            or not isinstance(prior["scanned_files"], int) or isinstance(prior["scanned_files"], bool)
            or prior["scanned_files"] < 0 or prior["protected_roots"] != []):
        return [{"kind": "prior-audit-invalid", "path": "retained-run-audit.json"}]

    def normalize(items: object) -> list[dict[str, str]] | None:
        if not isinstance(items, list):
            return None
        normalized: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"kind", "path"} or item["kind"] not in _AUDIT_FINDING_KINDS or not isinstance(item["path"], str):
                return None
            raw_path = item["path"]
            safe_path = _safe_audit_path(Path(raw_path))
            if safe_path != raw_path or raw_path.startswith("/") or any(part in ("", ".", "..") for part in PurePosixPath(raw_path).parts):
                return None
            normalized.append({"kind": item["kind"], "path": safe_path})
        return normalized

    recorded = normalize(prior["cumulative_findings"])
    findings = normalize(prior["findings"])
    current = normalize(prior["current_findings"])
    if (recorded is None or findings is None or current is None or findings != recorded
            or prior["passed"] != (not recorded) or prior["current_passed"] != (not current)):
        return [{"kind": "prior-audit-invalid", "path": "retained-run-audit.json"}]
    return recorded


def _protected_artifact_roots(root: Path, manifest_path: Path | None) -> set[str]:
    """New runs may not exclude any host path from retained-artifact audit."""
    if manifest_path is not None and manifest_path.exists():
        raise ValueError("host protected artifact exemptions are no longer permitted")
    return set()


def _remediate_retained_secret(path: Path, text: str) -> str:
    """Replace a detected secret artifact atomically, or quarantine the raw file."""
    sanitized = redact(text)
    path.chmod(0)
    temporary = path.with_name(".{}.redacted-{}".format(path.name, uuid.uuid4().hex))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(sanitized)
        os.replace(temporary, path)
        path.chmod(0o600)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return "secret-pattern-quarantined"
    return "secret-pattern-remediated"


def _quarantine_retained_artifact(path: Path) -> None:
    try:
        path.chmod(0)
    except OSError:
        pass


def _retained_secret_hint(text: str) -> bool:
    """Cheap, bounded prefilter before redaction of untrusted retained bytes."""
    lowered = text.casefold()
    if "remotepass" in lowered:
        return True
    for name in ("password", "secret", "token", "api_key", "api-key", "credential", "authorization", "cookie", "x-api-key", "x-auth-token", "x-access-token"):
        start = 0
        while (found := lowered.find(name, start)) >= 0:
            after = lowered[found + len(name):].lstrip(" \t\"'")
            if after.startswith(("=", ":")):
                value = after[1:].lstrip(" \t\"'")
                if value and not value.startswith(("<redacted>", "{", "[")):
                    return True
            start = found + len(name)
    scheme_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-")
    terminator_pattern = re.compile(r"[/\s?#]")
    occurrences: list[tuple[int, int]] = []
    at_positions = [index for index, character in enumerate(text) if character == "@"]
    marker = 0
    while (marker := text.find("://", marker)) >= 0:
        start = marker
        while start and text[start - 1] in scheme_chars:
            start -= 1
        candidate = next((index for index in range(start, marker) if text[index].isalpha()), None)
        if candidate is not None:
            occurrences.append((candidate, marker + 3))
        marker += 3
    at_cursor = 0
    for index, (_start, scheme_end) in enumerate(occurrences):
        terminator = terminator_pattern.search(text, scheme_end)
        authority_end = terminator.start() if terminator else len(text)
        authority = text[scheme_end:authority_end]
        if "@" in authority:
            userinfo, _host_port = authority.rsplit("@", 1)
        else:
            while at_cursor < len(at_positions) and at_positions[at_cursor] <= scheme_end:
                at_cursor += 1
            if at_cursor == len(at_positions):
                continue
            eventual_at = at_positions[at_cursor]
            later_scheme = occurrences[index + 1][0] if index + 1 < len(occurrences) else -1
            authority_is_host_only = bool(re.fullmatch(r"(?:\[[0-9a-f:.]+\]|[a-z0-9.-]+)(?::\d+)?", authority, re.IGNORECASE))
            prefix_end = later_scheme if 0 <= later_scheme < eventual_at else -1
            if prefix_end >= 0 and authority_is_host_only and ":" not in unquote(text[authority_end:prefix_end]):
                # The following scheme is a separate occurrence only after an
                # unambiguous no-userinfo URI; finditer will inspect it too.
                continue
            # This includes ambiguous host:port/path@ forms; fail closed
            # instead of guessing that a malformed credential is harmless.
            userinfo = text[scheme_end:eventual_at]
        decoded = unquote(userinfo).casefold()
        if ":" in decoded:
            _user, credential = decoded.split(":", 1)
            if credential and credential != "<redacted>":
                return True
    return False


def _retained_secret_detected(path: Path) -> bool:
    """Bound regex work while still inspecting every retained file."""
    try:
        size = path.stat().st_size
    except OSError:
        raise
    if size <= 64 * 1024:
        return _retained_secret_hint(path.read_text(encoding="utf-8", errors="replace"))
    overlap = ""
    with path.open("rb") as stream:
        while chunk := stream.read(4096):
            text = overlap + chunk.decode("utf-8", errors="replace")
            if _retained_secret_hint(text):
                return True
            overlap = text[-512:]
    return False


def audit_failure_status(output: Path, reason: str, exit_code: int) -> None:
    if reason not in {"timeout", "failed"} or exit_code < 0:
        raise ValueError("invalid audit failure status")
    json_dump(output, {"schema": 1, "status": "failed", "reason": reason, "exit_code": exit_code,
                       "security_acceptance": "not-satisfied"})


def browser_command_failure(output: Path, phase_name: str, mode: str, exit_code: int, timed_out: bool) -> None:
    if not phase_name or not mode or exit_code < 0:
        raise ValueError("invalid browser command failure")
    json_dump(output, {"schema": 1, "status": "failed", "phase": phase_name, "mode": mode,
                       "exit_code": exit_code, "timed_out": timed_out, "diagnostics": "redacted bounded excerpt"})


def audit_retained_run(root: Path, canaries: list[str], output: Path, *, classification_manifest: Path | None = None,
                       private_log_source: Path | None = None) -> None:
    """Audit every publishable retained artifact, with narrow explicit exclusions."""
    findings: list[dict[str, str]] = []
    historical_findings = _historical_audit_findings(output)
    scanned = 0
    encoded_canaries = [value.encode("utf-8") for value in canaries if value]
    protected_roots = _protected_artifact_roots(root, classification_manifest)
    obsolete_protected = root / "protected"
    if obsolete_protected.exists() or obsolete_protected.is_symlink():
        if obsolete_protected.is_symlink() or not obsolete_protected.is_dir():
            findings.append({"kind": "obsolete-protected-host-path", "path": "protected"})
        elif any(obsolete_protected.rglob("*")):
            findings.append({"kind": "obsolete-protected-host-artifact", "path": "protected"})
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        safe_path = _safe_audit_path(relative)
        if relative.parts and relative.parts[0] in protected_roots:
            continue
        if any(part.casefold() in _DENIED_RETAINED_ARTIFACT_PARTS for part in relative.parts):
            findings.append({"kind": "denied-artifact", "path": safe_path})
            continue
        if not path.is_file() or path.is_symlink():
            continue
        scanned += 1
        if relative.as_posix() == _LOG_PUBLICATION_RELATIVE_PATH:
            valid_log = (_valid_published_log(root, path, private_log_source)
                         if _is_drvfs_evidence_root(root) else _regular_file(path))
            if not valid_log:
                _quarantine_retained_artifact(path)
                record = path.with_name(path.name + ".publication.json")
                if record.exists() and not record.is_symlink():
                    _quarantine_retained_artifact(record)
                findings.append({"kind": "log-publication-invalid", "path": safe_path})
                continue
        if relative.as_posix() == _LOG_PUBLICATION_RELATIVE_PATH + ".publication.json":
            log = path.with_name("seedsync.log")
            if not (_is_drvfs_evidence_root(root) and log.exists()
                    and _valid_published_log(root, log, private_log_source)):
                _quarantine_retained_artifact(path)
                findings.append({"kind": "log-publication-invalid", "path": safe_path})
                continue
        if path.suffix.lower() == ".png":
            valid_screenshot = (
                _valid_published_screenshot(root, path)
                if _is_drvfs_evidence_root(root)
                else _valid_screenshot_attestation(root, path)
            )
            if valid_screenshot:
                # The pre-capture DOM detector plus this bound attestation are
                # the image evidence contract. Never apply text regexes to
                # compressed IDAT bytes, which can create entropy false positives.
                continue
            _quarantine_retained_artifact(path)
            sidecar = path.with_name(path.name + ".safety.json")
            if sidecar.exists() and not sidecar.is_symlink():
                _quarantine_retained_artifact(sidecar)
            findings.append({"kind": "screenshot-attestation-invalid", "path": safe_path})
            continue
        if path.name.endswith(".png.safety.json"):
            image = path.with_name(path.name[:-12])
            valid_screenshot = (
                _valid_published_screenshot(root, image)
                if image.exists() and _is_drvfs_evidence_root(root)
                else _valid_screenshot_attestation(root, image) if image.exists() else False
            )
            if not valid_screenshot:
                _quarantine_retained_artifact(path)
                findings.append({"kind": "screenshot-attestation-invalid", "path": safe_path})
                continue
        if path.name.endswith(".png.publication.json"):
            image = path.with_name(path.name[:-17])
            valid_screenshot = (
                image.exists()
                and _is_drvfs_evidence_root(root)
                and _valid_published_screenshot(root, image)
            )
            if not valid_screenshot:
                _quarantine_retained_artifact(path)
                findings.append({"kind": "screenshot-attestation-invalid", "path": safe_path})
                continue
        canary_detected = False
        try:
            with path.open("rb") as stream:
                previous = b""
                while chunk := stream.read(1024 * 1024):
                    payload = previous + chunk
                    if any(canary in payload for canary in encoded_canaries):
                        _quarantine_retained_artifact(path)
                        findings.append({"kind": "canary-quarantined", "path": safe_path})
                        canary_detected = True
                        break
                    previous = payload[-max((len(value) for value in encoded_canaries), default=1):]
        except OSError:
            findings.append({"kind": "unreadable", "path": safe_path})
            continue
        if canary_detected:
            continue
        try:
            secret_detected = _retained_secret_detected(path)
        except OSError:
            findings.append({"kind": "unreadable", "path": safe_path})
            continue
        if secret_detected:
            if path.stat().st_size <= 64 * 1024:
                text = path.read_text(encoding="utf-8", errors="replace")
                findings.append({"kind": _remediate_retained_secret(path, text), "path": safe_path})
            else:
                _quarantine_retained_artifact(path)
                findings.append({"kind": "secret-pattern-quarantined", "path": safe_path})
        if path.name in {"migration-required.json", "migration-status.json", "migration-failure-http.json"}:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                findings.append({"kind": "invalid-status-artifact", "path": safe_path})
            else:
                if isinstance(payload, dict) and "action" in payload:
                    findings.append({"kind": "raw-status-artifact", "path": safe_path})
    cumulative_findings = list(dict.fromkeys((item["kind"], item["path"]) for item in historical_findings + findings))
    cumulative_findings = [{"kind": kind, "path": path} for kind, path in cumulative_findings]
    json_dump(output, {"schema": 3, "passed": not cumulative_findings, "current_passed": not findings,
                       "scanned_files": scanned, "protected_roots": sorted(protected_roots),
                       "findings": cumulative_findings, "current_findings": findings,
                       "cumulative_findings": cumulative_findings})
    if cumulative_findings:
        raise ValueError("retained run audit failed")


def migration_failure_files(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    names = ("migration-state.json", ".migration.lock", ".seedsync.runtime.lock", ".migration-restore.json", "migration-receipt.json", "migration-backups")
    files: dict[str, Any] = {}
    for name in names:
        path = root / name
        item: dict[str, Any] = {"present": path.exists() or path.is_symlink()}
        if path.is_symlink():
            item["type"] = "symlink"
        elif path.is_dir():
            item["type"] = "directory"
            item["entries"] = sorted(child.name for child in path.iterdir())
        elif path.is_file():
            item["type"] = "file"
            item["size"] = path.stat().st_size
            content = path.read_text(encoding="utf-8", errors="replace")[:8192]
            try:
                item["content"] = redact_value(json.loads(content))
            except json.JSONDecodeError:
                item["content"] = redact(content)
        files[name] = item
    return {"schema": 1, "root_name": root.name, "files": files}


MIGRATION_INFRASTRUCTURE = {"migration-backups", ".migration.lock", ".seedsync.runtime.lock", "migration-state.json", ".migration-restore.json"}


def inventory(root: Path, *, legacy_config: bool = False) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("inventory root must be a real directory")
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if legacy_config and len(path.relative_to(root).parts) == 1 and path.name in MIGRATION_INFRASTRUCTURE:
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("refusing symlink in inventory: {}".format(relative))
        mode = stat.S_IMODE(info.st_mode)
        if path.is_dir():
            entries.append({"path": relative, "type": "directory", "mode": format(mode, "04o")})
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append({"path": relative, "type": "file", "mode": format(mode, "04o"), "size": info.st_size, "sha256": digest})
        else:
            raise ValueError("refusing non-regular inventory entry: {}".format(relative))
    return {"schema": 1, "root_name": root.name, "entries": entries}


def compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    if expected.get("entries") == actual.get("entries"):
        return []
    expected_rows = {row["path"]: row for row in expected.get("entries", [])}
    actual_rows = {row["path"]: row for row in actual.get("entries", [])}
    result = []
    for path in sorted(expected_rows.keys() | actual_rows.keys()):
        if expected_rows.get(path) != actual_rows.get(path):
            result.append(path)
    return result


def matrix_payload(run_id: str) -> dict[str, Any]:
    return {"schema": 1, "run_id": run_id, "rows": [
        {"id": row_id, "phase": phase, "expected": expected, "status": "pending", "artifacts": []}
        for row_id, expected, phase in MATRIX
    ]}


def update_matrix(path: Path, row_id: str, status: str, artifact: str, detail: str = "") -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if status not in {"passed", "failed", "not-exercised"}:
        raise ValueError("invalid matrix status")
    for row in payload["rows"]:
        if row["id"] == row_id:
            row["status"] = status
            row["artifacts"].append(artifact)
            if detail:
                row["detail"] = redact(detail)
            json_dump(path, payload)
            return
    raise ValueError("unknown matrix row: {}".format(row_id))


def verify_matrix(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    unacceptable = [row["id"] for row in payload.get("rows", []) if row.get("status") != "passed" or not row.get("artifacts")]
    if unacceptable:
        raise SystemExit("ship-readiness matrix is not all passed: {}".format(",".join(unacceptable)))


def progress(path: Path, phase: str, state: str, detail: str = "") -> None:
    payload = {"schema": 1, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "phase": phase, "state": state, "detail": redact(detail)}
    json_dump(path, payload)
    with path.with_suffix(".tsv").open("a", encoding="utf-8") as stream:
        stream.write("{at}\t{phase}\t{state}\t{detail}\n".format(**payload))


def summarize(matrix_path: Path, output: Path, failures: Path, outcome: str, detail: str) -> None:
    payload = json.loads(matrix_path.read_text(encoding="utf-8")) if matrix_path.exists() else {"rows": []}
    rows = payload.get("rows", [])
    summary = {"outcome": outcome, "detail": redact(detail), "counts": {status: sum(row.get("status") == status for row in rows) for status in ("passed", "failed", "pending", "not-exercised")}, "rows": rows}
    json_dump(output, summary)
    json_dump(failures, {"outcome": outcome, "detail": redact(detail), "failed_or_unproven": [row["id"] for row in rows if row.get("status") != "passed"]})


def _model_contract(value: object) -> object:
    if isinstance(value, list):
        return sorted((_model_contract(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if not isinstance(value, dict):
        return value
    keep = {key: value.get(key) for key in ("name", "is_dir", "state", "remote_size", "local_size") if key in value}
    if "children" in value:
        keep["children"] = _model_contract(value["children"])
    return keep


def behavior_contract(model_path: Path, settings_path: Path, controller_path: Path, autoqueue_path: Path, fixture_path: Path, output: Path) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(settings_path, encoding="utf-8")
    settings = {section: {key: redact(value) for key, value in sorted(parser.items(section)) if not SECRET.search(key)} for section in sorted(parser.sections())}
    model = json.loads(model_path.read_text(encoding="utf-8"))
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    contract = {
        "schema": 1,
        "settings": settings,
        "controller_persist": json.loads(controller_path.read_text(encoding="utf-8")),
        "autoqueue_persist": json.loads(autoqueue_path.read_text(encoding="utf-8")),
        "fixture_cases": fixture.get("case_index", []),
        "model": _model_contract(model),
    }
    json_dump(output, contract)


def compare_contract(expected_path: Path, actual_path: Path, output: Path) -> None:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    equal = expected == actual
    json_dump(output, {"equal": equal, "expected": expected if not equal else None, "actual": actual if not equal else None})
    if not equal:
        raise ValueError("normalized legacy behavior contract changed after pinned reboot")


def assert_browser_evidence(source: Path, output: Path, *, reuse: bool = False) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    errors = payload.get("errors")
    runtime_errors = payload.get("runtimeErrors")
    diagnostic_failures = payload.get("diagnosticFailures")
    stream_connections = payload.get("streamConnections")
    api = payload.get("api")
    visible = payload.get("visibleFixtureRows")
    if (
        errors
        or not isinstance(runtime_errors, list)
        or runtime_errors
        or not isinstance(diagnostic_failures, list)
        or diagnostic_failures
        or not isinstance(api, dict)
        or not api
        or any(not isinstance(value, dict) or value.get("status") != 200 for value in api.values())
    ):
        raise ValueError("browser/API evidence has errors")
    if not isinstance(visible, dict) or not visible or not all(visible.values()):
        raise ValueError("browser files UI did not expose every representative fixture row")
    if not reuse:
        if payload.get("claimButtonCount") != 1:
            raise ValueError("first claim was not presented exactly once")
        actions = payload.get("actions")
        if not isinstance(actions, dict) or not actions or any(value != 200 for value in actions.values()):
            raise ValueError("browser queue/extract action was not accepted")
    else:
        transitions = payload.get("expectedTransitions")
        stream_evidence = payload.get("streamTransitionEvidence")
        if not isinstance(transitions, list) or not isinstance(stream_evidence, list) or not isinstance(stream_connections, list) or not {"restart-status", "restart-settings"}.issubset(api):
            raise ValueError("browser restart transport evidence is missing")
        restart = [item for item in transitions if isinstance(item, dict) and item.get("kind") == "restart-armed-sse-transport-cluster"]
        if len(restart) != 1:
            raise ValueError("browser restart transport evidence is ambiguous")
        transition = restart[0]
        proof = transition.get("sameOriginProof")
        stop_proof = transition.get("stopDispatchProof")
        quiet = payload.get("postReuseQuiet")
        required = ("classification", "totalCount", "sseEventCount", "badGateway502Count", "firstGeneration", "lastGeneration", "firstObservedAfterMs", "lastObservedAfterMs", "clusterMaximumEvents", "clusterMaximumMs", "recoveryStatus", "modelRows", "sameOriginProof", "stopDispatchProof")
        if (
            any(key not in transition for key in required)
            or transition.get("classification") != "expected-bounded-restart-transport-cluster"
            or any(type(transition.get(key)) is not int for key in required if key not in {"classification", "sameOriginProof", "stopDispatchProof"})
            or transition["clusterMaximumEvents"] != 8 or not 1 <= transition["sseEventCount"] <= transition["totalCount"] <= 8
            or not 0 <= transition["badGateway502Count"] <= transition["totalCount"]
            or transition["sseEventCount"] + transition["badGateway502Count"] != transition["totalCount"]
            or transition["firstGeneration"] < 0 or transition["lastGeneration"] < transition["firstGeneration"]
            or transition["lastGeneration"] - transition["firstGeneration"] + 1 != transition["totalCount"]
            or transition["clusterMaximumMs"] != 15000 or transition["firstObservedAfterMs"] < 0
            or transition["lastObservedAfterMs"] < transition["firstObservedAfterMs"]
            or transition["lastObservedAfterMs"] - transition["firstObservedAfterMs"] > transition["clusterMaximumMs"]
            or transition["recoveryStatus"] != 200 or transition["modelRows"] <= 0
            or not isinstance(proof, dict) or proof.get("pathname") != "/server/stream"
            or proof.get("recoveryPathname") != "/server/stream" or proof.get("recoveryStatus") != 200
            or not isinstance(proof.get("origin"), str) or proof["origin"] != proof.get("recoveryOrigin")
            or not isinstance(proof.get("orderedTemporal502Associations"), list) or len(proof["orderedTemporal502Associations"]) != transition["badGateway502Count"]
            or not isinstance(stop_proof, dict) or set(stop_proof) != {"schema", "run_id", "stability_generation", "arm_generation", "acknowledged_error_generation", "restart_stop_dispatched", "stop_dispatch_epoch_ms", "acknowledgedEpochMs"}
            or stop_proof.get("schema") != 1 or stop_proof.get("restart_stop_dispatched") is not True
            or any(type(stop_proof.get(key)) is not int for key in ("stability_generation", "arm_generation", "acknowledged_error_generation", "stop_dispatch_epoch_ms", "acknowledgedEpochMs"))
            or stop_proof["arm_generation"] != stop_proof["stability_generation"] + 1 or stop_proof["acknowledged_error_generation"] != stop_proof["stability_generation"]
            or stop_proof["stop_dispatch_epoch_ms"] < stop_proof["acknowledgedEpochMs"]
            or not isinstance(quiet, dict) or quiet.get("quietWindowMs") != 1500 or type(quiet.get("errorGeneration")) is not int
        ):
            raise ValueError("browser restart transport evidence is invalid")
        associations = proof["orderedTemporal502Associations"]
        if (len({item.get("errorGeneration") for item in associations if isinstance(item, dict)}) != len(associations)
                or len({item.get("responseConnectionId") for item in associations if isinstance(item, dict)}) != len(associations)
                or any(not isinstance(location, dict) or set(location) != {"origin", "pathname", "errorGeneration", "responseObservedAfterMs", "errorObservedAfterMs", "responseConnectionId", "temporalAssociation"}
               or location.get("origin") != proof["origin"] or location.get("pathname") != "/server/stream"
               or type(location.get("errorGeneration")) is not int or not transition["firstGeneration"] <= location["errorGeneration"] <= transition["lastGeneration"]
               or type(location.get("responseObservedAfterMs")) is not int or not transition["firstObservedAfterMs"] <= location["responseObservedAfterMs"] <= transition["lastObservedAfterMs"]
               or type(location.get("errorObservedAfterMs")) is not int or not transition["firstObservedAfterMs"] <= location["errorObservedAfterMs"] <= transition["lastObservedAfterMs"]
               or location["responseObservedAfterMs"] > location["errorObservedAfterMs"]
               or location["errorObservedAfterMs"] - location["responseObservedAfterMs"] > 1000
               or type(location.get("responseConnectionId")) is not int or location["responseConnectionId"] < 0 or location["responseConnectionId"] >= len(stream_connections)
               or not isinstance(stream_connections[location["responseConnectionId"]], dict)
               or stream_connections[location["responseConnectionId"]].get("connectionId") != location["responseConnectionId"]
               or stream_connections[location["responseConnectionId"]].get("origin") != proof["origin"] or stream_connections[location["responseConnectionId"]].get("pathname") != "/server/stream"
               or stream_connections[location["responseConnectionId"]].get("status") != 502 or stream_connections[location["responseConnectionId"]].get("contentType") != "other"
               or stream_connections[location["responseConnectionId"]].get("observedAfterMs") != location["responseObservedAfterMs"]
               or location.get("temporalAssociation") != "ordered-response-before-console"
               for location in associations)):
            raise ValueError("browser restart transport evidence has invalid 502 correlation")
        retained = [item for item in stream_evidence if isinstance(item, dict) and item.get("kind") == transition["kind"]]
        if len(retained) != 1 or retained[0] != transition:
            raise ValueError("browser restart transport evidence was not retained consistently")
        convergence = [item for item in transitions if isinstance(item, dict) and item.get("kind") == "post-reuse-sse-convergence"]
        if len(convergence) != 1:
            raise ValueError("browser post-reuse convergence evidence is ambiguous")
        post = convergence[0]
        clean = post.get("classification") == "clean-post-reuse-convergence"
        post_required = ("classification", "totalCount", "firstGeneration", "lastGeneration", "clusterMaximumEvents", "clusterMaximumMs", "phaseBoundary")
        boundary = post.get("phaseBoundary")
        if (
            any(key not in post for key in post_required)
            or any(type(post.get(key)) is not int for key in post_required if key not in {"classification", "phaseBoundary"})
            or post["clusterMaximumEvents"] != 4 or post["clusterMaximumMs"] != 5000
            or post["firstGeneration"] < transition["lastGeneration"] or post["lastGeneration"] < post["firstGeneration"]
            or not isinstance(boundary, dict) or set(boundary) != {"kind", "errorGeneration", "observedAfterMs", "observedAtEpochMs"}
            or boundary.get("kind") != "pre-reuse-action-start"
            or any(type(boundary.get(key)) is not int for key in ("errorGeneration", "observedAfterMs", "observedAtEpochMs")) or boundary["errorGeneration"] != transition["lastGeneration"]
            or not isinstance(quiet, dict) or type(quiet.get("observedAfterMs")) is not int or type(quiet.get("observedAtEpochMs")) is not int
            or any(type(post.get(key)) is not int for key in ("recoveryStatus", "recoveryObservedAfterMs", "modelRows", "modelObservedAfterMs"))
            or post.get("recoveryOrigin") != proof["origin"] or post.get("recoveryPathname") != "/server/stream" or post["recoveryStatus"] != 200 or post["modelRows"] <= 0
            or post["recoveryObservedAfterMs"] <= boundary["observedAfterMs"] or post["modelObservedAfterMs"] < post["recoveryObservedAfterMs"]
            or not {"post-reuse-convergence-status", "post-reuse-convergence-settings"}.issubset(api)
            or any(type(api[label].get("observedAfterMs")) is not int or type(api[label].get("observedAtEpochMs")) is not int or api[label]["observedAfterMs"] < post["modelObservedAfterMs"] or api[label]["observedAfterMs"] > quiet.get("observedAfterMs", -1) or api[label]["observedAtEpochMs"] < boundary["observedAtEpochMs"] or api[label]["observedAtEpochMs"] > quiet.get("observedAtEpochMs", -1) for label in ("post-reuse-convergence-status", "post-reuse-convergence-settings"))
            or (clean and (post["totalCount"] != 0 or post["firstGeneration"] != transition["lastGeneration"] or post["lastGeneration"] != transition["lastGeneration"]))
            or (not clean and (
                post.get("classification") != "expected-bounded-post-reuse-sse-convergence"
                or not 1 <= post["totalCount"] <= 4
                or post["firstGeneration"] != transition["lastGeneration"] + 1
                or post["lastGeneration"] - post["firstGeneration"] + 1 != post["totalCount"]
                or any(type(post.get(key)) is not int for key in ("firstObservedAfterMs", "lastObservedAfterMs"))
                or post["firstObservedAfterMs"] <= boundary["observedAfterMs"] or post["recoveryObservedAfterMs"] <= boundary["observedAfterMs"] or post["modelObservedAfterMs"] <= boundary["observedAfterMs"]
                or post["lastObservedAfterMs"] >= post["recoveryObservedAfterMs"]
                or post["lastObservedAfterMs"] < post["firstObservedAfterMs"]
                or post["lastObservedAfterMs"] - post["firstObservedAfterMs"] > post["clusterMaximumMs"]
            ))
            or quiet["errorGeneration"] != post["lastGeneration"] or type(quiet.get("observedAfterMs")) is not int or type(quiet.get("observedAtEpochMs")) is not int or quiet["observedAfterMs"] <= boundary["observedAfterMs"] or quiet["observedAtEpochMs"] <= boundary["observedAtEpochMs"]
        ):
            raise ValueError("browser post-reuse convergence evidence is invalid")
        post_retained = [item for item in stream_evidence if isinstance(item, dict) and item.get("kind") == post["kind"]]
        if len(post_retained) != 1 or post_retained[0] != post:
            raise ValueError("browser post-reuse convergence evidence was not retained consistently")
    json_dump(output, {"reuse": reuse, "api_endpoints": sorted(api), "visible_fixture_rows": sorted(visible), "actions": sorted((payload.get("actions") or {}).keys())})


def assert_legacy_browser_evidence(source: Path, output: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    visible = payload.get("visibleFixtureRows")
    if payload.get("errors") or not isinstance(visible, dict) or not visible or not all(visible.values()):
        raise ValueError("legacy browser evidence did not show every representative fixture row")
    json_dump(output, {"visible_fixture_rows": sorted(visible)})


def assert_migrated_settings(before_path: Path, after_path: Path, output: Path) -> None:
    def read(path: Path) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(path, encoding="utf-8")
        return parser
    before, after = read(before_path), read(after_path)
    checked: list[str] = []
    for section in ("General", "Lftp", "Controller", "Web", "AutoQueue"):
        if not before.has_section(section) or not after.has_section(section):
            raise ValueError("migration omitted legacy settings section {}".format(section))
        for key, value in before.items(section):
            if SECRET.search(key):
                continue
            if section == "General" and key == "debug":
                expected_level = "DEBUG" if value.strip().lower() in {"true", "1", "yes"} else "INFO"
                if after.get("General", "log_level", fallback="") != expected_level:
                    raise ValueError("migration did not normalize General.debug to log_level")
                checked.append("General.debug->General.log_level")
                continue
            if after.get(section, key, fallback=None) != value:
                raise ValueError("migration changed legacy setting {}.{}".format(section, key))
            checked.append("{}.{}".format(section, key))
    if after.get("Lftp", "transfer_backend", fallback="") != "lftp" or after.get("General", "exclude_patterns", fallback=None) is None:
        raise ValueError("current defaults were not injected")
    json_dump(output, {"preserved_legacy_keys": checked, "injected_defaults": {"Lftp.transfer_backend": "lftp", "General.exclude_patterns": after.get("General", "exclude_patterns")}})


def assert_current_model(before_settings: Path, path_pairs_path: Path, browser_path: Path, fixture_path: Path, output: Path) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(before_settings, encoding="utf-8")
    remote, local = parser.get("Lftp", "remote_path"), parser.get("Lftp", "local_path")
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "seedsync:v086:{}\n{}".format(remote, local)))
    pairs = json.loads(path_pairs_path.read_text(encoding="utf-8")).get("path_pairs")
    if not isinstance(pairs, list) or len(pairs) != 1:
        raise ValueError("current model requires exactly one path pair")
    pair = pairs[0]
    if pair != {"id": expected_id, "name": "Default", "remote_path": remote, "local_path": local, "enabled": True, "auto_queue": True}:
        raise ValueError("current Default path pair identity/content differs from deterministic migration contract")
    browser = json.loads(browser_path.read_text(encoding="utf-8"))
    model = browser.get("model")
    if not isinstance(model, list):
        raise ValueError("browser model stream did not provide a model-init payload")
    expected = {case["name"]: case["backend_state"] for case in json.loads(fixture_path.read_text(encoding="utf-8")).get("case_index", [])}
    actual = {item.get("name"): item for item in model if isinstance(item, dict)}
    missing = sorted(set(expected) - set(actual))
    wrong = {name: {"expected": state, "actual": actual[name].get("state")} for name, state in expected.items() if name in actual and actual[name].get("state") != state}
    identity_errors = sorted(name for name, item in actual.items() if name in expected and (item.get("path_pair_id") != expected_id or item.get("path_pair_name") != "Default"))
    if missing or wrong or identity_errors:
        raise ValueError("current model contract mismatch missing={} wrong={} identity={}".format(missing, sorted(wrong), identity_errors))
    json_dump(output, {"path_pair": pair, "model_rows": [{"name": name, "state": actual[name]["state"], "path_pair_id": actual[name]["path_pair_id"]} for name in sorted(expected)], "scan_content": sorted(actual)})


def assert_autoqueue(before_settings: Path, autoqueue_persist: Path, browser_path: Path, fixture_path: Path, controller_persist: Path, output: Path) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(before_settings, encoding="utf-8")
    expected_options = {key: parser.getboolean("AutoQueue", key) for key in ("enabled", "patterns_only", "auto_extract")}
    if expected_options != {"enabled": True, "patterns_only": True, "auto_extract": True}:
        raise ValueError("fixture AutoQueue options are not enabled/patterns-only/auto-extract")
    patterns = sorted(json.loads(item)["pattern"] for item in json.loads(autoqueue_persist.read_text(encoding="utf-8")).get("patterns", []))
    browser = json.loads(browser_path.read_text(encoding="utf-8"))
    if browser.get("autoqueuePatterns") != patterns:
        raise ValueError("AutoQueue API patterns differ from persisted patterns")
    cases = json.loads(fixture_path.read_text(encoding="utf-8")).get("case_index", [])
    outcomes = {case["name"]: {"expected_match": case["autoqueue"], "state": case["backend_state"]} for case in cases if case["autoqueue"] != "unmatched"}
    current = {item.get("name"): item.get("state") for item in browser.get("model", []) if isinstance(item, dict)}
    wrong = {name: item for name, item in outcomes.items() if current.get(name) != item["state"]}
    if wrong:
        raise ValueError("AutoQueue pattern outcomes do not match current model: {}".format(sorted(wrong)))
    persisted = json.loads(controller_persist.read_text(encoding="utf-8"))
    if not isinstance(persisted.get("downloaded"), list) or not isinstance(persisted.get("extracted"), list):
        raise ValueError("controller persistence is malformed after AutoQueue actions")
    json_dump(output, {"options": expected_options, "patterns": patterns, "pattern_outcomes": {name: {**item, "actual_state": current[name]} for name, item in outcomes.items()}, "persisted_markers": {key: persisted[key] for key in ("downloaded", "extracted")}})


def _product_preclaim_auth_validator():
    product_python = Path("/app/python")
    repository_python = Path(__file__).parents[3] / "python" if "__file__" in globals() else Path("/nonexistent")
    for candidate in (product_python, repository_python):
        candidate_text = str(candidate)
        if candidate.is_dir() and candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)
    try:
        from web.auth_store import validate_completed_migration_preclaim_auth_state
    except ImportError as error:
        raise RuntimeError("product auth-store validator is unavailable to the retained-lab helper") from error
    return validate_completed_migration_preclaim_auth_state


def _current_product_auth_contract_summary(contract_path: Path) -> dict[str, Any]:
    """Trust only the safe, successful result of the current-image validator."""
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("current product auth contract is unavailable") from error
    required = {
        "schema", "validator", "status", "image_ref", "image_id", "image_digest",
        "image_provenance", "container", "containment",
    }
    if not isinstance(contract, dict) or set(contract) != required:
        raise ValueError("current product auth contract schema is invalid")
    containment = contract.get("containment")
    if (
        contract.get("schema") != 1
        or contract.get("validator") != "current-product-preclaim-auth"
        or contract.get("status") != "passed"
        or contract.get("image_provenance") != "immutable-current-image-id"
        or not isinstance(contract.get("image_ref"), str)
        or not re.fullmatch(r"seedsync/upgrade-v086:current-[a-z0-9_-]{1,32}", contract["image_ref"])
        or not isinstance(contract.get("image_id"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", contract["image_id"])
        or not isinstance(contract.get("image_digest"), str)
        or not re.fullmatch(r"(?:unpublished:sha256:|[^\s@]+@sha256:)[0-9a-f]{64}", contract["image_digest"])
        or not isinstance(contract.get("container"), str)
        or not re.fullmatch(r"seedsync-upgrade-v086-current-auth-validator-[a-z0-9_-]{1,32}", contract["container"])
        or not isinstance(containment, dict)
        or containment != {
            "network": "none",
            "read_only_rootfs": True,
            "config_mount_read_only": True,
            "evidence_mount_read_only": True,
            "user": "1000:1000",
            "no_new_privileges": True,
            "cap_drop_all": True,
        }
    ):
        raise ValueError("current product auth contract is invalid")
    return {
        "path": contract_path.name,
        "schema": "current-product-preclaim-auth-contract-v1",
        "event_counts": {},
        "order_class": "validated-by-current-product-image",
        "validator": contract["validator"],
        "image_id": contract["image_id"],
        "image_digest": contract["image_digest"],
    }


def post_start_auth_history_summary(config_root: Path, product_auth_contract: Path | None = None) -> dict[str, Any]:
    """Report only redacted shape after the product validator accepts the state."""
    if product_auth_contract is not None:
        return _current_product_auth_contract_summary(product_auth_contract)
    _product_preclaim_auth_validator()(config_root)
    history_path = config_root / "api-keys.history.jsonl"
    if not history_path.exists():
        return {"path": history_path.name, "schema": "product-preclaim-v1", "event_counts": {}, "order_class": "absent"}
    try:
        rows = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("product-validated auth history could not be summarized") from error
    counts: dict[str, int] = {}
    for row in rows:
        event = row.get("event") if isinstance(row, dict) else None
        if not isinstance(event, str):
            raise ValueError("product-validated auth history could not be summarized")
        counts[event] = counts.get(event, 0) + 1
    return {
        "path": history_path.name,
        "schema": "product-preclaim-v1",
        "event_counts": counts,
        "order_class": "product-validated-preclaim",
    }


def assert_migration(config_root: Path, output: Path, *, auth_store_phase: str = "post-start", product_auth_contract: Path | None = None) -> None:
    """Check only stable, customer-visible v0.8.6 migration invariants."""
    pairs = json.loads((config_root / "path_pairs.json").read_text(encoding="utf-8"))
    rows = pairs.get("path_pairs")
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("migration must create exactly one path pair")
    pair = rows[0]
    if pair.get("name") != "Default" or pair.get("enabled") is not True or pair.get("auto_queue") is not True:
        raise ValueError("migration Default path pair is incomplete")
    api_keys_path = config_root / "api-keys.json"
    auth_history_path = config_root / "api-keys.history.jsonl"
    auth_state_paths = (api_keys_path, auth_history_path)
    if auth_store_phase not in {"migration-apply", "post-start"}:
        raise ValueError("migration auth-store phase is invalid")
    if auth_store_phase == "migration-apply":
        if any(path.exists() or path.is_symlink() for path in auth_state_paths):
            raise ValueError("migration transaction end state must not retain auth state")
        auth_store_state = "absent-at-migration-transaction-end"
        auth_history = {"path": auth_history_path.name, "schema": "none", "event_counts": {}}
    else:
        try:
            auth_history = post_start_auth_history_summary(config_root, product_auth_contract)
        except (RuntimeError, ValueError) as error:
            json_dump(output, {"schema": 1, "status": "failed", "auth_store_phase": auth_store_phase,
                               "auth_history": {"path": auth_history_path.name, "reason": str(error)}})
            raise
        auth_store_state = (
            "validated-by-current-product-image"
            if product_auth_contract is not None
            else "empty-v3-post-start" if api_keys_path.exists()
            else "bootstrap-proof-history-only-post-start" if auth_history_path.exists()
            else "absent-lazy-post-start"
        )
    receipt = json.loads((config_root / "migration-state.json").read_text(encoding="utf-8"))
    if receipt.get("state") != "complete" or receipt.get("migration_id") != "original-v0.8.6-to-current-v1":
        raise ValueError("migration receipt is not complete for the v0.8.6 migration")
    backup_ref = receipt.get("backup")
    if not isinstance(backup_ref, str) or not backup_ref.startswith("migration-backups/"):
        raise ValueError("migration receipt does not name a retained backup")
    backup_name = backup_ref[len("migration-backups/"):]
    if not backup_name or "/" in backup_name or "\\" in backup_name or backup_name in {".", ".."}:
        raise ValueError("migration receipt backup escapes retained backup root")
    backup_root, backup = config_root / "migration-backups", config_root / "migration-backups" / backup_name
    if backup_root.is_symlink() or not stat.S_ISDIR(backup_root.lstat().st_mode) or backup.is_symlink() or not stat.S_ISDIR(backup.lstat().st_mode):
        raise ValueError("migration retained backup root is unsafe")
    manifest_path, data_root = backup / "manifest.json", backup / "data"
    if manifest_path.is_symlink() or not stat.S_ISREG(manifest_path.lstat().st_mode) or data_root.is_symlink() or not stat.S_ISDIR(data_root.lstat().st_mode):
        raise ValueError("migration retained backup layout is unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_manifest = {"manifest_version", "backup_id", "migration_id", "source_schema", "target_schema", "created_at", "root_identity", "aggregate", "entries"}
    if not isinstance(manifest, dict) or set(manifest) != required_manifest or manifest.get("manifest_version") != 2 or manifest.get("backup_id") != backup_name:
        raise ValueError("retained backup manifest schema is invalid")
    if any(not isinstance(manifest.get(key), str) or not manifest[key] for key in ("migration_id", "source_schema", "target_schema", "created_at")):
        raise ValueError("retained backup manifest identity is invalid")
    if manifest["migration_id"] != receipt["migration_id"] or manifest["source_schema"] != "original-v0.8.6" or manifest["target_schema"] != "seedsync-current-v1":
        raise ValueError("retained backup manifest identity does not match the v0.8.6 migration lane")
    if not isinstance(manifest.get("root_identity"), list) or not manifest["root_identity"] or not all(type(item) is int and item >= 0 for item in manifest["root_identity"]):
        raise ValueError("retained backup manifest root identity is invalid")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("retained backup manifest has no entries")
    expected: dict[str, dict[str, Any]] = {}
    hardened_modes: dict[str, dict[str, str]] = {}
    aggregate_size = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") not in {"file", "dir"}:
            raise ValueError("retained backup manifest entry type is invalid")
        entry_type, relative, mode = entry["type"], entry.get("path"), entry.get("mode")
        if not isinstance(relative, str) or not relative or "\\" in relative or type(mode) is not int or not 0 <= mode <= 0o7777:
            raise ValueError("retained backup manifest entry is incomplete")
        parts = PurePosixPath(relative).parts
        if PurePosixPath(relative).is_absolute() or any(part in {"", ".", ".."} for part in parts) or PurePosixPath(*parts).as_posix() != relative or relative in expected:
            raise ValueError("retained backup manifest path is unsafe or duplicate")
        if entry_type == "file":
            if set(entry) != {"path", "type", "mode", "size", "sha256"} or type(entry.get("size")) is not int or entry["size"] < 0 or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ValueError("retained backup file manifest entry is invalid")
            aggregate_size += entry["size"]
        elif set(entry) != {"path", "type", "mode"}:
            raise ValueError("retained backup directory manifest entry is invalid")
        expected[relative] = entry
    actual: dict[str, tuple[str, Path, os.stat_result]] = {}
    def walk(directory: Path, prefix: PurePosixPath = PurePosixPath()) -> None:
        for child in directory.iterdir():
            relative = (prefix / child.name).as_posix()
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError("retained backup data tree contains a link or special file")
            actual[relative] = ("dir" if stat.S_ISDIR(info.st_mode) else "file", child, info)
            if stat.S_ISDIR(info.st_mode): walk(child, prefix / child.name)
    walk(data_root)
    if set(actual) != set(expected): raise ValueError("retained backup data tree has missing or unexpected members")
    files = 0
    for relative, entry in expected.items():
        actual_type, payload, info = actual[relative]
        if os.path.commonpath((str(payload.resolve()), str(data_root.resolve()))) != str(data_root.resolve()):
            raise ValueError("retained backup payload escapes data root")
        if actual_type != entry["type"]: raise ValueError("retained backup member type differs from manifest: {}".format(relative))
        actual_mode = stat.S_IMODE(info.st_mode)
        if actual_mode & ~entry["mode"]: raise ValueError("retained backup payload mode broadened from manifest: {}".format(relative))
        if actual_mode != entry["mode"]: hardened_modes[relative] = {"manifest_mode": format(entry["mode"], "04o"), "actual_mode": format(actual_mode, "04o")}
        if actual_type == "file":
            if info.st_size != entry["size"] or hashlib.sha256(payload.read_bytes()).hexdigest() != entry["sha256"]: raise ValueError("retained backup payload does not match manifest: {}".format(relative))
            files += 1
    aggregate = manifest.get("aggregate")
    expected_aggregate = {"entries": len(expected), "files": files, "directories": len(expected) - files, "total_size": aggregate_size}
    if not isinstance(aggregate, dict) or aggregate != expected_aggregate: raise ValueError("retained backup manifest aggregate is invalid")
    json_dump(output, {"pair": pair, "receipt": {key: receipt.get(key) for key in ("state", "migration_id", "backup", "current_schema")}, "auth_store_phase": auth_store_phase, "auth_store_state": auth_store_state, "auth_history": auth_history, "auth_state_paths_verified": [path.name for path in auth_state_paths], "backup_file_entries_verified": files, "backup_directory_entries_verified": len(expected) - files, "backup_modes_hardened": hardened_modes, "intentional_ephemeral_state": {"api_keys": "lazy absent or exact empty current store before first claim", "bootstrap_proof": "post-start proof history only; no claim or exchange", "migration_lock": "transaction-only", "normal_runtime_lock": "runtime-only"}})


def assert_legacy_auth_absence(inventory_path: Path, output: Path) -> None:
    inventory_payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    entries = inventory_payload.get("entries") if isinstance(inventory_payload, dict) else None
    if inventory_payload.get("schema") != 1 or not isinstance(entries, list):
        raise ValueError("legacy inventory is malformed")
    forbidden = {"api-keys.json", "api-keys.history.jsonl"}
    observed = sorted(row.get("path") for row in entries if isinstance(row, dict) and row.get("path") in forbidden)
    if observed:
        raise ValueError("legacy source contains auth state: {}".format(", ".join(observed)))
    json_dump(output, {"schema": 1, "legacy_auth_state": "absent", "forbidden_paths": sorted(forbidden)})


def assert_migration_apply_auth_boundary(status_path: Path, legacy_auth_path: Path, output: Path) -> None:
    status, legacy = json.loads(status_path.read_text(encoding="utf-8")), json.loads(legacy_auth_path.read_text(encoding="utf-8"))
    final = status.get("final") if isinstance(status, dict) else None
    migration = final.get("migration_status") if isinstance(final, dict) else None
    backup = migration.get("backup") if isinstance(migration, dict) else None
    if (
        not isinstance(final, dict)
        or final.get("http_status") != 200
        or not isinstance(migration, dict)
        or migration.get("migration_id") != "original-v0.8.6-to-current-v1"
        or migration.get("state") != "complete"
        or migration.get("operation", {}).get("status") != "succeeded"
        or not isinstance(backup, dict)
        or backup.get("status") != "ready"
        or backup.get("complete_restore_ready") is not True
    ):
        raise ValueError("migration apply did not reach the transactional complete boundary")
    if (
        not isinstance(legacy, dict)
        or legacy.get("schema") != 1
        or legacy.get("legacy_auth_state") != "absent"
        or legacy.get("forbidden_paths") != ["api-keys.history.jsonl", "api-keys.json"]
    ):
        raise ValueError("migration apply boundary lacks a legacy auth-absence baseline")
    json_dump(output, {"schema": 1, "auth_store_phase": "migration-apply", "auth_store_state": "absent-at-validated-transaction-end-state", "auth_state_paths_verified": ["api-keys.json", "api-keys.history.jsonl"], "operation": "succeeded", "legacy_auth_state": "absent"})


def assert_restore(config_root: Path, expected: dict[str, Any], output: Path) -> None:
    differences = compare(expected, inventory(config_root, legacy_config=True))
    generated = [name for name in ("migration-state.json", "path_pairs.json", "api-keys.json") if (config_root / name).exists()]
    json_dump(output, {"legacy_inventory_equal": not differences, "different_paths": differences, "unexpected_current_files": generated})
    if differences or generated:
        raise ValueError("offline restore did not return the legacy configuration contract")


def seed_v086_settings(path: Path, output: Path) -> None:
    """Make the fixture exercise non-default values in every legacy section."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    with path.open(encoding="utf-8") as stream:
        parser.read_file(stream)
    values = {
        "General": {"debug": "false", "verbose": "true"},
        "Lftp": {
            "num_max_parallel_downloads": "2", "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4", "num_max_connections_per_dir_file": "5",
            "num_max_total_connections": "6", "use_temp_file": "true",
        },
        "Controller": {
            "interval_ms_remote_scan": "2000", "interval_ms_local_scan": "3000",
            "interval_ms_downloading_scan": "4000", "extract_path": "/downloads", "use_local_path_as_extract_path": "true",
        },
        "Web": {"port": "8800"},
        "AutoQueue": {"enabled": "true", "patterns_only": "true", "auto_extract": "true"},
    }
    for section, section_values in values.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in section_values.items():
            parser.set(section, key, value)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        parser.write(stream)
    json_dump(output, {"seeded_sections": {section: sorted(items) for section, items in values.items()}})


def self_test() -> None:
    if redact("remote_password = value\napi_key: ABC") != "remote_password=<redacted>\napi_key:<redacted>":
        raise SystemExit("redaction self-test failed")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "root"
        root.mkdir()
        target = root / "nested"
        target.mkdir()
        (target / "sample.txt").write_text("ok", encoding="utf-8")
        os.chmod(target / "sample.txt", 0o600)
        first = inventory(root)
        second = inventory(root)
        if compare(first, second) != []:
            raise SystemExit("stable inventory self-test failed")
        (target / "sample.txt").write_text("changed", encoding="utf-8")
        if compare(first, inventory(root)) != ["nested/sample.txt"]:
            raise SystemExit("inventory difference self-test failed")
        matrix = Path(temporary) / "matrix.json"
        json_dump(matrix, matrix_payload("self-test"))
        update_matrix(matrix, "before-legacy-ui-api-model", "passed", "artifact.json")
        if json.loads(matrix.read_text(encoding="utf-8"))["rows"][0]["status"] != "passed":
            raise SystemExit("matrix update self-test failed")
        manifest = Path(temporary) / "fixture.json"
        archive = Path(temporary) / "fixture.zip"
        archive_output = Path(temporary) / "fixture-archive.json"
        manifest.write_text(json.dumps({"cases": [{
            "id": "archive-self-test", "remote": {"archive": {"payload.bin": {"generated_bytes": 32}}},
        }]}), encoding="utf-8")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as stream:
            stream.writestr("payload.bin", (_DOWNLOAD_ARCHIVE_GENERATED_PATTERN * 2)[:32])
        verify_download_archive(archive, manifest, "archive-self-test", archive_output)
        if json.loads(archive_output.read_text(encoding="utf-8")).get("status") != "passed":
            raise SystemExit("download archive self-test failed")
    print("ship-readiness helper self-test: passed")


class ProcInconclusive(RuntimeError):
    """Procfs state could not be safely interpreted for a live process."""


def proc_stat_identity(pid: int) -> tuple[str, int, int, int]:
    """Read the fields needed to bind a pidfd to one exact Linux process."""
    raw = Path("/proc") / str(pid) / "stat"
    try:
        text = raw.read_bytes()
    except FileNotFoundError:
        raise
    except OSError as error:
        raise ProcInconclusive("unable to read proc stat") from error
    closing = text.rfind(b")")
    if closing < 0 or closing + 2 >= len(text):
        raise ValueError("malformed proc stat")
    fields = text[closing + 2:].split()
    if len(fields) <= 19:
        raise ValueError("short proc stat")
    state = fields[0]
    numeric = (fields[2], fields[3], fields[19])
    if len(state) != 1 or any(not field or any(byte < 48 or byte > 57 for byte in field) for field in numeric):
        raise ValueError("invalid proc stat fields")
    try:
        decoded_state = state.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid process state encoding") from exc
    return decoded_state, *(int(field) for field in numeric)


def session_descendants(leader: int) -> list[tuple[int, int]]:
    if leader <= 0:
        raise ValueError("leader must be positive")
    members: list[tuple[int, int]] = []
    helper_pid = os.getpid()
    for path in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(path.name)
            state, group, session, start_time = proc_stat_identity(pid)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, ProcInconclusive, ValueError) as error:
            raise ProcInconclusive("unable to enumerate session members") from error
        if pid not in (leader, helper_pid) and state != "Z" and session == leader:
            members.append((pid, start_time))
    return sorted(members)


def pidfd_kill_session_descendant(leader: int, pid: int, start_time: int) -> bool:
    """Kill only a still-matching session member through a stable pidfd."""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("pidfd signaling is unavailable")
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return False
    except OSError as error:
        raise ProcInconclusive("unable to open session member pidfd") from error
    try:
        state, group, session, observed_start = proc_stat_identity(pid)
        if state == "Z" or session != leader or observed_start != start_time:
            return False
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        return True
    except (ProcessLookupError, FileNotFoundError):
        return False
    except (PermissionError, OSError, ProcInconclusive, ValueError) as error:
        raise ProcInconclusive("unable to signal session member through pidfd") from error
    finally:
        os.close(descriptor)


def pidfd_kill_session_descendants(leader: int) -> bool:
    for pid, start_time in session_descendants(leader):
        if pidfd_kill_session_descendant(leader, pid, start_time):
            continue
        try:
            state, group, session, _ = proc_stat_identity(pid)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, ProcInconclusive, ValueError) as error:
            raise ProcInconclusive("unable to revalidate session member") from error
        if state != "Z" and session == leader:
            return False
    return True


def pidfd_signal_session_leader(pid: int, start_time: int, signum: int) -> bool:
    """Deliver an allowed control signal only to the exact session leader."""
    if signum not in (signal.SIGTERM, signal.SIGUSR1):
        raise ValueError("unsupported leader control signal")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("pidfd signaling is unavailable")
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return False
    except OSError as error:
        raise ProcInconclusive("unable to open leader pidfd") from error
    try:
        state, group, session, observed_start = proc_stat_identity(pid)
        if state == "Z" or group != pid or session != pid or observed_start != start_time:
            return False
        signal.pidfd_send_signal(descriptor, signum)
        return True
    except (ProcessLookupError, FileNotFoundError):
        return False
    except (PermissionError, OSError, ProcInconclusive, ValueError) as error:
        raise ProcInconclusive("unable to signal session leader through pidfd") from error
    finally:
        os.close(descriptor)


def pidfd_kill_session_leader(pid: int, start_time: int) -> bool:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("pidfd signaling is unavailable")
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return False
    except OSError as error:
        raise ProcInconclusive("unable to open leader pidfd") from error
    try:
        state, group, session, observed_start = proc_stat_identity(pid)
        if state == "Z" or group != pid or session != pid or observed_start != start_time:
            return False
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        return True
    except (ProcessLookupError, FileNotFoundError):
        return False
    except (PermissionError, OSError, ProcInconclusive, ValueError) as error:
        raise ProcInconclusive("unable to kill session leader through pidfd") from error
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--root", required=True)
    inventory_parser.add_argument("--output", required=True)
    inventory_parser.add_argument("--legacy-config", action="store_true")
    compare_parser = subparsers.add_parser("compare-inventory")
    compare_parser.add_argument("--expected", required=True)
    compare_parser.add_argument("--root", required=True)
    compare_parser.add_argument("--output", required=True)
    compare_parser.add_argument("--legacy-config", action="store_true")
    matrix_parser = subparsers.add_parser("matrix-init")
    matrix_parser.add_argument("--run-id", required=True)
    matrix_parser.add_argument("--output", required=True)
    update_parser = subparsers.add_parser("matrix-update")
    update_parser.add_argument("--matrix", required=True)
    update_parser.add_argument("--row", required=True)
    update_parser.add_argument("--status", required=True)
    update_parser.add_argument("--artifact", required=True)
    update_parser.add_argument("--detail", default="")
    verify_parser = subparsers.add_parser("matrix-verify")
    verify_parser.add_argument("--matrix", required=True)
    progress_parser = subparsers.add_parser("progress")
    progress_parser.add_argument("--output", required=True)
    progress_parser.add_argument("--phase", required=True)
    progress_parser.add_argument("--state", required=True)
    progress_parser.add_argument("--detail", default="")
    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--matrix", required=True)
    summary_parser.add_argument("--output", required=True)
    summary_parser.add_argument("--failures", required=True)
    summary_parser.add_argument("--outcome", required=True)
    summary_parser.add_argument("--detail", default="")
    contract_parser = subparsers.add_parser("behavior-contract")
    contract_parser.add_argument("--model", required=True)
    contract_parser.add_argument("--settings", required=True)
    contract_parser.add_argument("--controller", required=True)
    contract_parser.add_argument("--autoqueue", required=True)
    contract_parser.add_argument("--fixture", required=True)
    contract_parser.add_argument("--output", required=True)
    contract_compare_parser = subparsers.add_parser("compare-contract")
    contract_compare_parser.add_argument("--expected", required=True)
    contract_compare_parser.add_argument("--actual", required=True)
    contract_compare_parser.add_argument("--output", required=True)
    browser_parser = subparsers.add_parser("assert-browser")
    browser_parser.add_argument("--input", required=True)
    browser_parser.add_argument("--output", required=True)
    browser_parser.add_argument("--reuse", action="store_true")
    legacy_browser_parser = subparsers.add_parser("assert-legacy-browser")
    legacy_browser_parser.add_argument("--input", required=True)
    legacy_browser_parser.add_argument("--output", required=True)
    settings_assert_parser = subparsers.add_parser("assert-migrated-settings")
    settings_assert_parser.add_argument("--before", required=True)
    settings_assert_parser.add_argument("--after", required=True)
    settings_assert_parser.add_argument("--output", required=True)
    model_assert_parser = subparsers.add_parser("assert-current-model")
    model_assert_parser.add_argument("--before-settings", required=True)
    model_assert_parser.add_argument("--path-pairs", required=True)
    model_assert_parser.add_argument("--browser", required=True)
    model_assert_parser.add_argument("--fixture", required=True)
    model_assert_parser.add_argument("--output", required=True)
    autoqueue_assert_parser = subparsers.add_parser("assert-autoqueue")
    autoqueue_assert_parser.add_argument("--before-settings", required=True)
    autoqueue_assert_parser.add_argument("--persist", required=True)
    autoqueue_assert_parser.add_argument("--browser", required=True)
    autoqueue_assert_parser.add_argument("--fixture", required=True)
    autoqueue_assert_parser.add_argument("--controller", required=True)
    autoqueue_assert_parser.add_argument("--output", required=True)
    migration_parser = subparsers.add_parser("assert-migration")
    migration_parser.add_argument("--config-root", required=True)
    migration_parser.add_argument("--auth-store-phase", choices=("migration-apply", "post-start"), default="post-start")
    migration_parser.add_argument("--product-auth-contract")
    migration_parser.add_argument("--output", required=True)
    legacy_auth_parser = subparsers.add_parser("assert-legacy-auth-absence")
    legacy_auth_parser.add_argument("--inventory", required=True)
    legacy_auth_parser.add_argument("--output", required=True)
    apply_auth_parser = subparsers.add_parser("assert-migration-apply-auth-boundary")
    apply_auth_parser.add_argument("--status", required=True)
    apply_auth_parser.add_argument("--legacy-auth", required=True)
    apply_auth_parser.add_argument("--output", required=True)
    restore_parser = subparsers.add_parser("assert-restore")
    restore_parser.add_argument("--config-root", required=True)
    restore_parser.add_argument("--expected", required=True)
    restore_parser.add_argument("--output", required=True)
    seed_parser = subparsers.add_parser("seed-v086-settings")
    seed_parser.add_argument("--settings", required=True)
    seed_parser.add_argument("--output", required=True)
    failure_files_parser = subparsers.add_parser("migration-failure-files")
    failure_files_parser.add_argument("--root", required=True)
    failure_files_parser.add_argument("--output", required=True)
    status_evidence_parser = subparsers.add_parser("migration-status-evidence")
    status_evidence_parser.add_argument("--output", required=True)
    transition_terminal_parser = subparsers.add_parser("migration-terminal-transition-evidence")
    transition_terminal_parser.add_argument("--output", required=True)
    normal_runtime_parser = subparsers.add_parser("normal-runtime-transition-evidence")
    normal_runtime_parser.add_argument("--migration-status", required=True, type=int)
    normal_runtime_parser.add_argument("--status", required=True, type=int)
    normal_runtime_parser.add_argument("--bootstrap", required=True, type=int)
    normal_runtime_parser.add_argument("--output", required=True)
    preclaim_auth_parser = subparsers.add_parser("preclaim-auth-challenge-evidence")
    preclaim_auth_parser.add_argument("--status", required=True, type=int)
    preclaim_auth_parser.add_argument("--headers", required=True)
    preclaim_auth_parser.add_argument("--output", required=True)
    publish_log_parser = subparsers.add_parser("publish-private-log")
    publish_log_parser.add_argument("--source", required=True)
    publish_log_parser.add_argument("--root", required=True)
    publish_log_parser.add_argument("--run-id", required=True)
    publish_log_parser.add_argument("--output", required=True)
    subparsers.add_parser("redact-stdin")
    audit_parser = subparsers.add_parser("audit-retained-run")
    audit_parser.add_argument("--root", required=True)
    audit_parser.add_argument("--output", required=True)
    audit_parser.add_argument("--classification-manifest")
    audit_parser.add_argument("--private-log-source")
    audit_parser.add_argument("--canaries-stdin", action="store_true")
    archive_parser = subparsers.add_parser("validate-archive")
    archive_parser.add_argument("--archive", required=True)
    archive_parser.add_argument("--output", required=True)
    archive_binding_parser = subparsers.add_parser("bind-archive-inventory")
    archive_binding_parser.add_argument("--archive", required=True)
    archive_binding_parser.add_argument("--inventory", required=True)
    archive_binding_parser.add_argument("--output", required=True)
    protected_verify_parser = subparsers.add_parser("verify-protected-archive")
    protected_verify_parser.add_argument("--archive", required=True)
    protected_verify_parser.add_argument("--inventory", required=True)
    protected_verify_parser.add_argument("--manifest", required=True)
    protected_verify_parser.add_argument("--output", required=True)
    download_archive_parser = subparsers.add_parser("verify-download-archive")
    download_archive_parser.add_argument("--archive", required=True)
    download_archive_parser.add_argument("--fixture-manifest", required=True)
    download_archive_parser.add_argument("--case-id", required=True)
    download_archive_parser.add_argument("--output", required=True)
    pidfd_kill_parser = subparsers.add_parser("pidfd-kill-session-descendant")
    pidfd_kill_parser.add_argument("--leader", required=True, type=int)
    pidfd_kill_parser.add_argument("--pid", required=True, type=int)
    pidfd_kill_parser.add_argument("--start-time", required=True, type=int)
    proc_start_parser = subparsers.add_parser("proc-start-time")
    proc_start_parser.add_argument("--pid", required=True, type=int)
    leader_status_parser = subparsers.add_parser("session-leader-status")
    leader_status_parser.add_argument("--pid", required=True, type=int)
    leader_status_parser.add_argument("--start-time", required=True, type=int)
    leader_status_parser.add_argument("--allow-zombie", action="store_true")
    descendants_present_parser = subparsers.add_parser("session-descendants-status")
    descendants_present_parser.add_argument("--leader", required=True, type=int)
    member_status_parser = subparsers.add_parser("session-member-status")
    member_status_parser.add_argument("--leader", required=True, type=int)
    member_status_parser.add_argument("--pid", required=True, type=int)
    member_status_parser.add_argument("--same-process-group", action="store_true")
    pidfd_kill_many_parser = subparsers.add_parser("pidfd-kill-session-descendants")
    pidfd_kill_many_parser.add_argument("--leader", required=True, type=int)
    pidfd_kill_leader_parser = subparsers.add_parser("pidfd-kill-session-leader")
    pidfd_kill_leader_parser.add_argument("--pid", required=True, type=int)
    pidfd_kill_leader_parser.add_argument("--start-time", required=True, type=int)
    pidfd_signal_leader_parser = subparsers.add_parser("pidfd-signal-session-leader")
    pidfd_signal_leader_parser.add_argument("--pid", required=True, type=int)
    pidfd_signal_leader_parser.add_argument("--start-time", required=True, type=int)
    pidfd_signal_leader_parser.add_argument("--signal", required=True, choices=("TERM", "USR1"))
    audit_failure_parser = subparsers.add_parser("audit-failure-status")
    audit_failure_parser.add_argument("--output", required=True)
    audit_failure_parser.add_argument("--reason", required=True, choices=("timeout", "failed"))
    audit_failure_parser.add_argument("--exit-code", required=True, type=int)
    browser_failure_parser = subparsers.add_parser("browser-command-failure")
    browser_failure_parser.add_argument("--output", required=True)
    browser_failure_parser.add_argument("--phase", required=True)
    browser_failure_parser.add_argument("--mode", required=True)
    browser_failure_parser.add_argument("--exit-code", required=True, type=int)
    browser_failure_parser.add_argument("--timed-out", action="store_true")
    subparsers.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "inventory":
        json_dump(Path(args.output), inventory(Path(args.root), legacy_config=args.legacy_config))
    elif args.command == "compare-inventory":
        result = compare(json.loads(Path(args.expected).read_text(encoding="utf-8")), inventory(Path(args.root), legacy_config=args.legacy_config))
        json_dump(Path(args.output), {"equal": not result, "different_paths": result})
        if result:
            raise SystemExit(1)
    elif args.command == "matrix-init":
        json_dump(Path(args.output), matrix_payload(args.run_id))
    elif args.command == "matrix-update":
        update_matrix(Path(args.matrix), args.row, args.status, args.artifact, args.detail)
    elif args.command == "matrix-verify":
        verify_matrix(Path(args.matrix))
    elif args.command == "progress":
        progress(Path(args.output), args.phase, args.state, args.detail)
    elif args.command == "summary":
        summarize(Path(args.matrix), Path(args.output), Path(args.failures), args.outcome, args.detail)
    elif args.command == "behavior-contract":
        behavior_contract(Path(args.model), Path(args.settings), Path(args.controller), Path(args.autoqueue), Path(args.fixture), Path(args.output))
    elif args.command == "compare-contract":
        compare_contract(Path(args.expected), Path(args.actual), Path(args.output))
    elif args.command == "assert-browser":
        assert_browser_evidence(Path(args.input), Path(args.output), reuse=args.reuse)
    elif args.command == "assert-legacy-browser":
        assert_legacy_browser_evidence(Path(args.input), Path(args.output))
    elif args.command == "assert-migrated-settings":
        assert_migrated_settings(Path(args.before), Path(args.after), Path(args.output))
    elif args.command == "assert-current-model":
        assert_current_model(Path(args.before_settings), Path(args.path_pairs), Path(args.browser), Path(args.fixture), Path(args.output))
    elif args.command == "assert-autoqueue":
        assert_autoqueue(Path(args.before_settings), Path(args.persist), Path(args.browser), Path(args.fixture), Path(args.controller), Path(args.output))
    elif args.command == "assert-migration":
        assert_migration(Path(args.config_root), Path(args.output), auth_store_phase=args.auth_store_phase,
                         product_auth_contract=Path(args.product_auth_contract) if args.product_auth_contract else None)
    elif args.command == "assert-legacy-auth-absence":
        assert_legacy_auth_absence(Path(args.inventory), Path(args.output))
    elif args.command == "assert-migration-apply-auth-boundary":
        assert_migration_apply_auth_boundary(Path(args.status), Path(args.legacy_auth), Path(args.output))
    elif args.command == "assert-restore":
        assert_restore(Path(args.config_root), json.loads(Path(args.expected).read_text(encoding="utf-8")), Path(args.output))
    elif args.command == "seed-v086-settings":
        seed_v086_settings(Path(args.settings), Path(args.output))
    elif args.command == "migration-failure-files":
        json_dump(Path(args.output), migration_failure_files(Path(args.root)))
    elif args.command == "migration-status-evidence":
        json_dump(Path(args.output), migration_status_evidence(json.load(sys.stdin)))
    elif args.command == "migration-terminal-transition-evidence":
        json_dump(Path(args.output), migration_terminal_transition_evidence(json.load(sys.stdin)))
    elif args.command == "normal-runtime-transition-evidence":
        json_dump(
            Path(args.output),
            normal_runtime_transition_evidence(args.migration_status, args.status, args.bootstrap),
        )
    elif args.command == "preclaim-auth-challenge-evidence":
        json_dump(
            Path(args.output),
            preclaim_auth_challenge_evidence(args.status, read_http_headers(Path(args.headers)), sys.stdin.read()),
        )
    elif args.command == "publish-private-log":
        json_dump(Path(args.output), publish_private_log_snapshot(Path(args.source), Path(args.root), args.run_id))
    elif args.command == "redact-stdin":
        sys.stdout.write(redact(sys.stdin.read()))
    elif args.command == "audit-retained-run":
        raw = sys.stdin.buffer.read().split(b"\0") if args.canaries_stdin else []
        audit_retained_run(
            Path(args.root), [value.decode("utf-8") for value in raw if value], Path(args.output),
            classification_manifest=Path(args.classification_manifest) if args.classification_manifest else None,
            private_log_source=Path(args.private_log_source) if args.private_log_source else None,
        )
    elif args.command == "validate-archive":
        validate_archive(Path(args.archive), Path(args.output))
    elif args.command == "bind-archive-inventory":
        bind_archive_inventory(Path(args.archive), json.loads(Path(args.inventory).read_text(encoding="utf-8")), Path(args.output))
    elif args.command == "verify-protected-archive":
        verify_protected_archive(Path(args.archive), json.loads(Path(args.inventory).read_text(encoding="utf-8")), json.loads(Path(args.manifest).read_text(encoding="utf-8")), Path(args.output))
    elif args.command == "verify-download-archive":
        verify_download_archive(Path(args.archive), Path(args.fixture_manifest), args.case_id, Path(args.output))
    elif args.command == "pidfd-kill-session-descendant":
        try:
            killed = pidfd_kill_session_descendant(args.leader, args.pid, args.start_time)
        except ProcInconclusive:
            raise SystemExit(2)
        except RuntimeError:
            raise SystemExit(3)
        if not killed:
            raise SystemExit(1)
    elif args.command == "proc-start-time":
        try:
            state, _, _, start_time = proc_stat_identity(args.pid)
        except (FileNotFoundError, PermissionError, ProcInconclusive, ValueError):
            raise SystemExit(2)
        if state == "Z":
            raise SystemExit(1)
        print(start_time)
    elif args.command == "session-leader-status":
        try:
            state, group, session, start_time = proc_stat_identity(args.pid)
        except (FileNotFoundError, PermissionError, ProcInconclusive, ValueError):
            raise SystemExit(2)
        if start_time != args.start_time or group != args.pid or session != args.pid or (state == "Z" and not args.allow_zombie):
            raise SystemExit(1)
    elif args.command == "session-descendants-status":
        try:
            present = bool(session_descendants(args.leader))
        except (PermissionError, ProcInconclusive, ValueError):
            raise SystemExit(2)
        if not present:
            raise SystemExit(1)
    elif args.command == "session-member-status":
        try:
            state, group, session, _ = proc_stat_identity(args.pid)
        except (FileNotFoundError, PermissionError, ProcInconclusive, ValueError):
            raise SystemExit(2)
        if state == "Z" or session != args.leader or (args.same_process_group and group != args.leader):
            raise SystemExit(1)
    elif args.command == "pidfd-kill-session-descendants":
        try:
            killed = pidfd_kill_session_descendants(args.leader)
        except ProcInconclusive:
            raise SystemExit(2)
        except RuntimeError:
            raise SystemExit(3)
        if not killed:
            raise SystemExit(1)
    elif args.command == "pidfd-kill-session-leader":
        try:
            killed = pidfd_kill_session_leader(args.pid, args.start_time)
        except ProcInconclusive:
            raise SystemExit(2)
        except RuntimeError:
            raise SystemExit(3)
        if not killed:
            raise SystemExit(1)
    elif args.command == "pidfd-signal-session-leader":
        try:
            signaled = pidfd_signal_session_leader(args.pid, args.start_time, getattr(signal, f"SIG{args.signal}"))
        except ProcInconclusive:
            raise SystemExit(2)
        except RuntimeError:
            raise SystemExit(3)
        if not signaled:
            raise SystemExit(1)
    elif args.command == "audit-failure-status":
        audit_failure_status(Path(args.output), args.reason, args.exit_code)
    elif args.command == "browser-command-failure":
        browser_command_failure(Path(args.output), args.phase, args.mode, args.exit_code, args.timed_out)
    else:
        self_test()


if __name__ == "__main__":
    main()
