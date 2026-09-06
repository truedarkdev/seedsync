"""Fail-closed, read-only SFTP source manifest capture for Incoming recovery.

The application transfer path uses LFTP and cannot rely on an SSH shell.  This
module therefore treats a bounded protocol runner as the transport boundary.
The runner returns newline-delimited JSON records and an explicit terminal
record; tests can inject that boundary while the default runner uses LFTP's
native SFTP directory listing commands only.

No remote path, entry name, credential, process stderr, or exception message
is retained in a manifest artifact.  A manifest contains counts and one-way
digests only.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import configparser
import re
import secrets
import shlex
import signal
import subprocess
import threading
import time
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


class SourceManifestError(ValueError):
    """A source manifest cannot be trusted or safely persisted."""

    def __init__(self, reason: str, stage: str | None = None):
        self.reason = reason if reason in _FAILURE_REASONS else "protocol_failure"
        self.stage = stage if stage in _FAILURE_STAGES else None
        super().__init__(self.reason)


@dataclass(frozen=True)
class SourceManifestEntry:
    """One normalized remote entry from the read-only protocol boundary."""

    path: str
    kind: str = "file"
    size: int = 0
    mtime_ns: int = 0
    mode: int = 0


@dataclass(frozen=True)
class SftpProcessResult:
    """Bounded process output returned by an injected protocol runner."""

    stdout: bytes | str
    returncode: int = 0
    timed_out: bool = False
    truncated: bool = False
    sentinel_seen: bool | None = None
    failure_stage: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class SourceManifestSnapshot:
    """Normalized, privacy-safe snapshot of a configured source root."""

    schema: str
    file_count: int
    total_bytes: int
    path_metadata_digest: str
    root_digest: str

    @property
    def files(self) -> int:
        return self.file_count

    @property
    def bytes(self) -> int:
        return self.total_bytes

    def as_artifact(self) -> Mapping[str, object]:
        return {
            "schema": self.schema,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "path_metadata_digest": self.path_metadata_digest,
            "root_digest": self.root_digest,
        }


@dataclass(frozen=True)
class SourceManifestStability:
    """Comparison result for two complete source snapshots."""

    stable: bool
    reason: str
    first: SourceManifestSnapshot
    second: SourceManifestSnapshot

    def as_artifact(self) -> Mapping[str, object]:
        return {
            "schema": "incoming-recovery-source-manifest-stability.v1",
            "stable": self.stable,
            "reason": self.reason,
            "first": self.first.as_artifact(),
            "second": self.second.as_artifact(),
        }


_SCHEMA = "incoming-recovery-source-manifest.v1"
_STABILITY_REASONS = frozenset({"stable", "snapshot_changed"})
_FAILURE_REASONS = frozenset({
    "invalid_configuration", "protocol_failure", "process_failed", "process_timeout",
    "output_truncated", "sentinel_missing", "sentinel_malformed", "record_malformed",
    "root_listing_missing",
    "root_escape", "duplicate_entry", "cycle_detected", "symlink_entry",
    "special_entry", "special_name", "invalid_size", "invalid_metadata",
    "too_many_entries", "output_too_large", "unstable_snapshot", "artifact_failure",
    "root_candidate_missing", "root_candidate_ambiguous", "pwd_malformed",
    "pwd_url_missing", "pwd_url_multiple", "pwd_url_invalid", "pwd_url_unsafe", "pwd_path_invalid",
})
_FAILURE_STAGES = frozenset({"launch", "connection", "open", "pwd", "root", "root_preflight", "listing", "enumeration", "completion", "sentinel", "parse"})
_ENTRY_KINDS = frozenset({"file", "directory"})
_MAX_INTEGER = 2_147_483_647
_MAX_BYTES = 2**63 - 1
_SENTINEL = "source_manifest_complete"
_STAGE_MARKERS = (
    "source_manifest_phase_open",
    "source_manifest_phase_root",
    "source_manifest_phase_enumeration",
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")
_SAFE_MODE_RE = re.compile(r"^[bcdlps-][rwxstST-]{9}$")
_LFTP_LONG_RE = re.compile(
    r"^(?P<mode>[bcdlps-][rwxstST-]{9})\s+(?:\S+\s+){1,3}"
    r"(?P<size>\d+)\s+(?P<mtime>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)\s+(?P<name>.*)$"
)
_LFTP_FIND_ROOT_RE = re.compile(r"^d[rwxstST-]{9}\s+-\s+-\s+\./$")
_LFTP_PROGRESS_PREFIXES = ("cd ok, cwd=",)
_ROOT_PREFLIGHT_SCHEMA = "incoming-recovery-source-root-preflight.v1"
_ROOT_PREFLIGHT_MARKERS = ("source_root_preflight_open", "source_root_preflight_pwd", "source_root_preflight_candidate")


def _fail(reason: str, stage: str | None = None) -> SourceManifestError:
    return SourceManifestError(reason, stage)


def _root_path(value: object) -> str:
    if not isinstance(value, str) or not value or _CONTROL_RE.search(value):
        raise _fail("invalid_configuration")
    if "\\" in value or "//" in value:
        raise _fail("invalid_configuration")
    root = PurePosixPath(value)
    parts = tuple(part for part in root.parts if part != "/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise _fail("invalid_configuration")
    for component in parts:
        _safe_name(component)
    normalized = root.as_posix()
    if normalized == "." or normalized.endswith("/"):
        raise _fail("invalid_configuration")
    return normalized


def _safe_name(name: object) -> str:
    if not isinstance(name, str) or not name or _CONTROL_RE.search(name):
        raise _fail("special_name")
    if name in (".", "..") or "/" in name or "\\" in name:
        raise _fail("special_name")
    return name


def _relative_entry_path(value: object, root: str) -> str:
    if not isinstance(value, str) or not value or _CONTROL_RE.search(value):
        raise _fail("special_name")
    if "\\" in value:
        raise _fail("root_escape")
    path = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in path.parts):
        raise _fail("root_escape")
    normalized = path.as_posix()
    prefix = root + "/"
    if path.is_absolute():
        # An absolute root may make LFTP emit its full selected-root prefix.
        # Only that exact trusted prefix is accepted, once.
        if not root.startswith("/") or normalized == root or not normalized.startswith(prefix):
            raise _fail("root_escape")
        relative = normalized[len(prefix):]
    elif normalized == root:
        raise _fail("record_malformed")
    elif normalized.startswith(prefix):
        relative = normalized[len(prefix):]
    else:
        relative = normalized
    root_relative = root.lstrip("/")
    if relative == root or relative.startswith(prefix) or relative == root_relative or relative.startswith(root_relative + "/"):
        raise _fail("root_escape")
    components = relative.split("/")
    if not components or any(not component for component in components):
        raise _fail("record_malformed")
    for component in components:
        _safe_name(component)
    return relative


def _metadata_value(value: object, *, allow_string: bool = False) -> int | str:
    if allow_string and isinstance(value, str) and value and not _CONTROL_RE.search(value):
        return value
    if type(value) is int and 0 <= value <= _MAX_INTEGER:
        return value
    raise _fail("invalid_metadata")


def _entry_from_record(record: object, root: str) -> SourceManifestEntry:
    if not isinstance(record, Mapping):
        raise _fail("record_malformed")
    if record.get("record", "entry") != "entry":
        raise _fail("record_malformed")
    relative = _relative_entry_path(record.get("path"), root)
    kind = record.get("kind")
    if kind not in _ENTRY_KINDS:
        if kind == "symlink":
            raise _fail("symlink_entry")
        if kind in {"block", "char", "fifo", "socket", "special"}:
            raise _fail("special_entry")
        raise _fail("record_malformed")
    size_value = record.get("size")
    if type(size_value) is not int or size_value < 0 or size_value > _MAX_BYTES:
        raise _fail("invalid_size")
    size = size_value
    mtime_raw = record.get("mtime_ns")
    if isinstance(mtime_raw, str) and mtime_raw and not _CONTROL_RE.search(mtime_raw):
        mtime: int | str = mtime_raw
    elif type(mtime_raw) is int and 0 <= mtime_raw <= _MAX_BYTES:
        mtime = mtime_raw
    else:
        raise _fail("invalid_metadata")
    mode = _metadata_value(record.get("mode"))
    if kind == "directory" and size != 0:
        raise _fail("invalid_size")
    if kind == "file" and size < 0:
        raise _fail("invalid_size")
    if isinstance(mtime, str):
        # LFTP's long listing exposes a stable timestamp string.  Normalize it
        # to the same non-negative signed range accepted by the protocol.
        mtime_ns = int.from_bytes(
            hashlib.sha256(mtime.encode("utf-8")).digest()[:8], "big",
        ) & _MAX_BYTES
    else:
        mtime_ns = mtime
    return SourceManifestEntry(relative, kind, size, mtime_ns, mode)


def _encode_entry(entry: SourceManifestEntry) -> bytes:
    return json.dumps({
        "record": "entry", "path": entry.path, "kind": entry.kind,
        "size": entry.size, "mtime_ns": entry.mtime_ns, "mode": entry.mode,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_protocol_output(output: bytes | str, root: str, *, max_entries: int) -> tuple[list[SourceManifestEntry], bool]:
    if isinstance(output, str):
        try:
            raw = output.encode("utf-8")
        except UnicodeEncodeError:
            raise _fail("output_truncated")
    elif isinstance(output, bytes):
        raw = output
    else:
        raise _fail("protocol_failure")
    if not raw or not raw.endswith(b"\n"):
        raise _fail("output_truncated")
    entries: list[SourceManifestEntry] = []
    seen: set[str] = set()
    seen_directories: set[str] = set()
    sentinel = False
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.endswith(b"\n") or len(line) > 16 * 1024:
            raise _fail("output_truncated")
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _fail("record_malformed")
        if not isinstance(record, Mapping):
            raise _fail("record_malformed")
        record_type = record.get("record")
        if record_type == "entry":
            if sentinel:
                raise _fail("record_malformed")
            entry = _entry_from_record(record, root)
            if entry.path in seen:
                raise _fail("duplicate_entry")
            seen.add(entry.path)
            if entry.kind == "directory":
                if entry.path in seen_directories:
                    raise _fail("cycle_detected")
                seen_directories.add(entry.path)
            entries.append(entry)
            if len(entries) > max_entries:
                raise _fail("too_many_entries")
            continue
        if record_type == "end":
            if sentinel or index != len(lines) - 1:
                raise _fail("sentinel_malformed")
            if record.get("sentinel") != _SENTINEL:
                raise _fail("sentinel_malformed")
            if type(record.get("entry_count")) is not int or record["entry_count"] != len(entries):
                raise _fail("sentinel_malformed")
            if type(record.get("file_count")) is not int or record["file_count"] != sum(e.kind == "file" for e in entries):
                raise _fail("sentinel_malformed")
            if type(record.get("total_bytes")) is not int or record["total_bytes"] != sum(e.size for e in entries if e.kind == "file"):
                raise _fail("sentinel_malformed")
            sentinel = True
            continue
        raise _fail("record_malformed")
    if not sentinel:
        raise _fail("sentinel_missing")
    return entries, True


def _manifest_digest(entries: Sequence[SourceManifestEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted((item for item in entries if item.kind == "file"), key=lambda item: item.path):
        digest.update(_encode_entry(entry))
        digest.update(b"\n")
    return digest.hexdigest()


def _redacted_artifact_error(exc: BaseException) -> Mapping[str, object]:
    reason = exc.reason if isinstance(exc, SourceManifestError) else "protocol_failure"
    artifact: dict[str, object] = {
        "schema": "incoming-recovery-source-manifest-error.v1", "reason": reason,
    }
    if isinstance(exc, SourceManifestError) and exc.stage is not None:
        artifact["stage"] = exc.stage
    return artifact


@dataclass(frozen=True)
class RootPreflightCandidate:
    """One privacy-safe root candidate result.  Raw paths never leave memory."""

    form: str
    valid: bool
    directory_digest: str | None = None

    def as_artifact(self) -> Mapping[str, object]:
        payload: dict[str, object] = {"form": self.form, "valid": self.valid}
        if self.directory_digest is not None:
            payload["directory_digest"] = self.directory_digest
        return payload


@dataclass(frozen=True)
class RootPreflightResult:
    """Sanitized root-only SFTP preflight result."""

    landing_directory_digest: str | None
    candidates: tuple[RootPreflightCandidate, ...]
    ambiguity: str
    stage: str
    reason: str
    selected_form: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.reason == "ok" and self.selected_form is not None

    def as_artifact(self) -> Mapping[str, object]:
        payload: dict[str, object] = {
            "schema": _ROOT_PREFLIGHT_SCHEMA,
            "stage": self.stage,
            "reason": self.reason,
            "ambiguity": self.ambiguity,
            "candidates": [candidate.as_artifact() for candidate in self.candidates],
        }
        if self.landing_directory_digest is not None:
            payload["landing_directory_digest"] = self.landing_directory_digest
        if self.selected_form is not None:
            payload["selected_form"] = self.selected_form
        return payload


class RootOnlySftpPreflight:
    """Strict-host SFTP PWD/cd discriminator which never enumerates content."""

    def __init__(self, protocol: "ReadOnlySftpProtocolRunner", *, timeout_seconds: float = 30.0, max_output_bytes: int = 64 * 1024):
        if not callable(getattr(protocol, "root_preflight_process", None)) and not callable(getattr(protocol, "canonicalize", None)):
            raise _fail("invalid_configuration")
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 300:
            raise _fail("invalid_configuration")
        if type(max_output_bytes) is not int or not 0 < max_output_bytes <= 1024 * 1024:
            raise _fail("invalid_configuration")
        self.protocol = protocol
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes

    @staticmethod
    def _path_digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _absolute_path(value: str) -> str:
        path = _root_path(value)
        if not path.startswith("/") or path == "/":
            raise _fail("invalid_configuration")
        return path

    @staticmethod
    def _relative_path(value: str) -> str:
        path = _root_path(value)
        if path.startswith("/"):
            raise _fail("invalid_configuration")
        return path

    @staticmethod
    def _join_landing(landing: str, relative: str) -> str:
        if landing == "/":
            return "/" + relative
        return landing.rstrip("/") + "/" + relative

    def _result_from_failure(self, reason: str, stage: str) -> RootPreflightResult:
        return RootPreflightResult(None, (), "missing", stage, reason)

    def _extract_pwd(self, output: bytes, marker: str) -> str:
        try:
            lines = output.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            raise _fail("pwd_url_invalid", "pwd")
        if marker not in lines:
            raise _fail("process_failed", "open")
        tokens = [
            match.group(0) for line in lines[lines.index(marker) + 1:]
            if line and line not in _ROOT_PREFLIGHT_MARKERS and not line.startswith(_LFTP_PROGRESS_PREFIXES)
            for match in re.finditer(r"sftp://[^\s]+", line)
        ]
        if not tokens:
            raise _fail("pwd_url_missing", "pwd")
        if len(tokens) != 1:
            raise _fail("pwd_url_multiple", "pwd")
        try:
            parsed = urlsplit(tokens[0])
            hostname = parsed.hostname
        except ValueError:
            raise _fail("pwd_url_invalid", "pwd")
        if parsed.scheme != "sftp" or not hostname:
            raise _fail("pwd_url_invalid", "pwd")
        if parsed.password not in (None, "") or parsed.query or parsed.fragment or _CONTROL_RE.search(parsed.path):
            raise _fail("pwd_url_unsafe", "pwd")
        path = parsed.path.rstrip("/") or "/"
        try:
            return self._absolute_path(path) if path != "/" else "/"
        except SourceManifestError:
            raise _fail("pwd_path_invalid", "pwd")

    def _run_pwd(self, candidate: str | None = None) -> tuple[str | None, SourceManifestError | None]:
        if callable(getattr(self.protocol, "canonicalize", None)):
            try:
                return self.protocol.canonicalize(
                    "." if candidate is None else candidate,
                    timeout_seconds=self.timeout_seconds, max_output_bytes=self.max_output_bytes,
                ), None
            except SourceManifestError as exc:
                return None, exc
            except Exception:
                return None, _fail("protocol_failure", "root" if candidate is not None else "open")
        try:
            result = self.protocol.root_preflight_process(
                candidate, timeout_seconds=self.timeout_seconds, max_output_bytes=self.max_output_bytes,
            )
        except SourceManifestError as exc:
            return None, exc
        if result.timed_out:
            return None, _fail("process_timeout", "root" if candidate is not None else "pwd")
        if result.truncated:
            return None, _fail("output_truncated", "root" if candidate is not None else "pwd")
        if result.returncode != 0:
            return None, _fail("process_failed", result.failure_stage or ("root" if candidate is not None else "open"))
        try:
            return self._extract_pwd(result.stdout if isinstance(result.stdout, bytes) else b"", _ROOT_PREFLIGHT_MARKERS[-1] if candidate is not None else _ROOT_PREFLIGHT_MARKERS[1]), None
        except SourceManifestError as exc:
            return None, exc

    def run(self, *, relative_root: str | None, trusted_absolute_root: str | None) -> RootPreflightResult:
        if relative_root is not None:
            relative_root = self._relative_path(relative_root)
        if trusted_absolute_root is not None:
            trusted_absolute_root = self._absolute_path(trusted_absolute_root)
        if relative_root is None and trusted_absolute_root is None:
            raise _fail("invalid_configuration")
        landing, failure = self._run_pwd()
        if failure is not None or landing is None:
            return self._result_from_failure((failure.reason if failure else "protocol_failure"), (failure.stage if failure and failure.stage else "pwd"))
        candidates: list[tuple[str, str]] = []
        if relative_root is not None:
            candidates.append(("account_relative", relative_root))
            candidates.append(("landing_absolute", self._join_landing(landing, relative_root)))
        if trusted_absolute_root is not None:
            candidates.append(("trusted_absolute", trusted_absolute_root))
            prefix = landing.rstrip("/") + "/"
            if landing == "/":
                derived = trusted_absolute_root.lstrip("/")
            elif trusted_absolute_root.startswith(prefix):
                derived = trusted_absolute_root[len(prefix):]
            else:
                derived = None
            if derived:
                candidates.append(("account_relative", self._relative_path(derived)))
        # Retain each configured form label but avoid duplicate transport work.
        probed: dict[str, tuple[bool, str | None]] = {}
        outputs: list[RootPreflightCandidate] = []
        for form, candidate in candidates:
            if candidate not in probed:
                resolved, candidate_failure = self._run_pwd(candidate)
                probed[candidate] = (candidate_failure is None and resolved is not None, self._path_digest(resolved) if resolved is not None else None)
            valid, digest = probed[candidate]
            outputs.append(RootPreflightCandidate(form, valid, digest))
        valid_outputs = [item for item in outputs if item.valid and item.directory_digest is not None]
        digests = {item.directory_digest for item in valid_outputs}
        if len(valid_outputs) == 1:
            return RootPreflightResult(self._path_digest(landing), tuple(outputs), "unique", "root_preflight", "ok", valid_outputs[0].form)
        if len(valid_outputs) > 1 and len(digests) == 1:
            return RootPreflightResult(self._path_digest(landing), tuple(outputs), "equivalent", "root_preflight", "ok", "equivalent")
        if len(valid_outputs) > 1:
            return RootPreflightResult(self._path_digest(landing), tuple(outputs), "ambiguous", "root_preflight", "root_candidate_ambiguous")
        return RootPreflightResult(self._path_digest(landing), tuple(outputs), "missing", "root_preflight", "root_candidate_missing")

    def capture_to(self, output_path: str | os.PathLike[str], *, relative_root: str | None, trusted_absolute_root: str | None) -> RootPreflightResult:
        result = self.run(relative_root=relative_root, trusted_absolute_root=trusted_absolute_root)
        _write_private_atomic(result.as_artifact(), output_path)
        if not result.succeeded:
            raise _fail(result.reason, result.stage)
        return result


class ReadOnlySftpProtocolRunner:
    """Run a native LFTP SFTP listing using only read-only commands.

    ``remote`` must be a credential-free host target such as ``sftp://host``.
    Credentials are supplied by the caller's existing LFTP environment or
    configuration, never interpolated into a command string or artifact.
    """

    _COMMANDS = ("set", "open", "cd", "cls", "echo", "bye")

    def __init__(
        self,
        remote: str | None = None,
        *,
        host: str | None = None,
        port: int = 22,
        executable: str = "lftp",
        environment: Mapping[str, str] | None = None,
        username: str | None = None,
        password: str | None = None,
        known_hosts_file: str | os.PathLike[str] | None = None,
    ):
        if remote is not None:
            if not isinstance(remote, str) or not remote.startswith("sftp://"):
                raise _fail("invalid_configuration")
            try:
                parsed = urlsplit(remote)
                uri_port = parsed.port
            except ValueError:
                raise _fail("invalid_configuration")
            if (
                parsed.scheme != "sftp" or not parsed.hostname
                or parsed.username is not None or parsed.password not in (None, "")
                or uri_port is not None or parsed.path or parsed.query or parsed.fragment
            ):
                raise _fail("invalid_configuration")
            host = "[" + parsed.hostname + "]" if ":" in parsed.hostname else parsed.hostname
        if not isinstance(host, str) or not host or _CONTROL_RE.search(host):
            raise _fail("invalid_configuration")
        if any(character in host for character in "/\\@?#%"):
            raise _fail("invalid_configuration")
        if type(port) is not int or not 1 <= port <= 65535:
            raise _fail("invalid_configuration")
        if ":" in host and not (host.startswith("[") and host.endswith("]")):
            # Raw IPv6 is accepted only when bracketed in the host field so
            # the generated SFTP URI remains unambiguous.
            raise _fail("invalid_configuration")
        self.executable = executable
        self.environment = dict(environment or {})
        if username is not None and (not isinstance(username, str) or not username or _CONTROL_RE.search(username)):
            raise _fail("invalid_configuration")
        if password is not None and (not isinstance(password, str) or _CONTROL_RE.search(password)):
            raise _fail("invalid_configuration")
        if password is not None and username is None:
            raise _fail("invalid_configuration")
        if known_hosts_file is not None:
            if not isinstance(known_hosts_file, (str, os.PathLike)):
                raise _fail("invalid_configuration")
            known_hosts_file = os.fspath(known_hosts_file)
            if not known_hosts_file or _CONTROL_RE.search(known_hosts_file):
                raise _fail("invalid_configuration")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.known_hosts_file = known_hosts_file
        self.remote = "sftp://" + host

    def root_preflight_process(self, candidate: str | None, *, timeout_seconds: float, max_output_bytes: int) -> SftpProcessResult:
        """Run strict-host PWD, optionally after one `cd`; never list content."""
        if candidate is not None:
            candidate = _root_path(candidate)
        connection = "open"
        if self.username is not None:
            credentials = self.username + "," + (self.password or "")
            connection += " -u " + json.dumps(credentials)
        if self.port != 22:
            connection += f" -p {self.port}"
        connection += " " + json.dumps(self.remote)
        connect_program = ""
        if self.known_hosts_file is not None:
            connect_program = (
                "set sftp:connect-program "
                + json.dumps("ssh -o StrictHostKeyChecking=yes -o UserKnownHostsFile=" + shlex.quote(self.known_hosts_file))
                + "\n"
            )
        marker = _ROOT_PREFLIGHT_MARKERS[-1] if candidate is not None else _ROOT_PREFLIGHT_MARKERS[1]
        script = (
            "set cmd:interactive false\nset cmd:fail-exit yes\nset net:max-retries 0\n"
            + f"set net:timeout {int(max(1, timeout_seconds))}\n" + connect_program + connection + "\n"
            + f"echo {_ROOT_PREFLIGHT_MARKERS[0]}\n"
            + (f"cd {json.dumps(candidate)}\n" if candidate is not None else "")
            + f"echo {marker}\npwd\nbye\n"
        )
        try:
            completed = _run_bounded_process([self.executable, "--norc"], script.encode("utf-8"), timeout_seconds, max_output_bytes, environment=self.environment)
        except (OSError, ValueError):
            return SftpProcessResult(b"", returncode=-1, failure_stage="launch", failure_reason="process_failed")
        if completed.timed_out or completed.truncated:
            return completed
        stdout = completed.stdout if isinstance(completed.stdout, bytes) else b""
        if completed.returncode != 0:
            return SftpProcessResult(b"", completed.returncode, failure_stage=("root" if candidate is not None else _failure_stage_from_root_preflight(stdout)), failure_reason="process_failed")
        return completed

    def __call__(self, root: str, *, timeout_seconds: float, max_output_bytes: int) -> SftpProcessResult:
        # The root is quoted as an LFTP argument; it is never a shell command.
        root_json = json.dumps(root)
        connection = f"open"
        if self.username is not None:
            credentials = self.username + "," + (self.password or "")
            connection += " -u " + json.dumps(credentials)
        if self.port != 22:
            connection += f" -p {self.port}"
        connection += " " + json.dumps(self.remote)
        connect_program = ""
        if self.known_hosts_file is not None:
            connect_program = (
                "set sftp:connect-program "
                + json.dumps(
                    "ssh -o StrictHostKeyChecking=yes -o UserKnownHostsFile="
                    + shlex.quote(self.known_hosts_file)
                )
                + "\n"
            )
        script = (
            "set cmd:interactive false\n"
            "set cmd:fail-exit yes\n"
            "set net:max-retries 0\n"
            f"set net:timeout {int(max(1, timeout_seconds))}\n"
            + connect_program
            + connection + "\n"
            + f"echo {_STAGE_MARKERS[0]}\n"
            f"cd {root_json}\n"
            + f"echo {_STAGE_MARKERS[1]}\n"
            + f"echo {_STAGE_MARKERS[2]}\n"
            "find -l .\n"
            "bye\n"
        )
        try:
            completed = _run_bounded_process(
                [self.executable, "--norc"], script.encode("utf-8"),
                timeout_seconds, max_output_bytes,
                environment=self.environment,
            )
        except (OSError, ValueError):
            return SftpProcessResult(b"", returncode=-1)
        if completed.timed_out or completed.truncated:
            return completed
        stdout = completed.stdout if isinstance(completed.stdout, bytes) else b""
        if completed.returncode != 0:
            return SftpProcessResult(
                b"", completed.returncode,
                failure_stage=_failure_stage_from_output(stdout),
                failure_reason="process_failed",
            )
        try:
            # LFTP may stream a recursive `find` after it has accepted later
            # stdin commands.  The bounded process boundary owns the terminal
            # marker: it adds it only after LFTP exits and both output drains
            # have joined, so it cannot split or interleave a listing record.
            entries = _parse_lftp_listing(stdout.rstrip(b"\n") + b"\n" + _SENTINEL.encode("ascii") + b"\n", root)
            protocol = b"".join(_encode_entry(entry) + b"\n" for entry in entries)
            protocol += json.dumps({
                "record": "end", "sentinel": _SENTINEL,
                "entry_count": len(entries), "file_count": sum(e.kind == "file" for e in entries),
                "total_bytes": sum(e.size for e in entries if e.kind == "file"),
            }, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            return SftpProcessResult(protocol, 0, sentinel_seen=True)
        except SourceManifestError as exc:
            return SftpProcessResult(
                b"", completed.returncode,
                truncated=exc.reason in {"output_truncated", "record_malformed", "sentinel_malformed"},
                sentinel_seen=False if exc.reason.startswith("sentinel_") else None,
                failure_stage="parse",
                failure_reason=exc.reason,
            )


class ReadOnlySftpRealpathRunner:
    """Strict-host SFTP canonicalization without LFTP URL rendering."""

    def __init__(self, *, host: str, port: int, username: str, known_hosts_file: str | os.PathLike[str], askpass_program: str | os.PathLike[str], connection_config_path: str | os.PathLike[str], executable: str = "sftp", environment: Mapping[str, str] | None = None):
        if not isinstance(host, str) or not host or _CONTROL_RE.search(host) or any(char in host for char in "/\\@?#%"):
            raise _fail("invalid_configuration")
        if type(port) is not int or not 1 <= port <= 65535 or not isinstance(username, str) or not username or _CONTROL_RE.search(username):
            raise _fail("invalid_configuration")
        self.host, self.port, self.username = host, port, username
        self.known_hosts_file = os.fspath(known_hosts_file)
        self.askpass_program = os.fspath(askpass_program)
        self.connection_config_path = os.fspath(connection_config_path)
        if not self.known_hosts_file or not self.askpass_program or not self.connection_config_path:
            raise _fail("invalid_configuration")
        self.executable, self.environment = executable, dict(environment or {})
        self.ssh_program = "ssh"

    def canonicalize(self, candidate: str, *, timeout_seconds: float, max_output_bytes: int) -> str:
        if candidate != ".":
            candidate = _root_path(candidate)
        environment = dict(self.environment)
        environment.update({
            "SSH_ASKPASS": self.askpass_program,
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": environment.get("DISPLAY", "incoming-recovery"),
            "INCOMING_RECOVERY_SFTP_ASKPASS": "1",
            "INCOMING_RECOVERY_SFTP_ASKPASS_CONFIG": self.connection_config_path,
        })
        remote_host = "[" + self.host + "]" if ":" in self.host and not self.host.startswith("[") else self.host
        argv = [
            self.executable, "-q", "-b", "-", "-S", self.ssh_program, "-P", str(self.port),
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + self.known_hosts_file,
            "-o", "NumberOfPasswordPrompts=1", self.username + "@" + remote_host,
        ]
        try:
            command = "pwd\n" if candidate == "." else "cd " + json.dumps(candidate) + "\npwd\n"
            result = _run_bounded_process(argv, (command + "bye\n").encode("utf-8"), timeout_seconds, max_output_bytes, environment=environment)
        except (OSError, ValueError):
            raise _fail("process_failed", "launch")
        if result.timed_out:
            raise _fail("process_timeout", "root")
        if result.truncated:
            raise _fail("output_truncated", "root")
        if result.returncode != 0:
            raise _fail("process_failed", "root")
        try:
            lines = result.stdout.decode("utf-8").splitlines() if isinstance(result.stdout, bytes) else []
        except UnicodeDecodeError:
            raise _fail("record_malformed", "root")
        paths: list[str] = []
        prefix = "Remote working directory: "
        for line in lines:
            if not line.startswith(prefix):
                continue
            path = line[len(prefix):]
            if not path or _CONTROL_RE.search(path):
                raise _fail("record_malformed", "root")
            try:
                normalized_path = path.rstrip("/") or "/"
                # A chrooted account may legitimately report its landing
                # directory as the remote root.  It is a canonical response,
                # never a configured manifest root chosen by untrusted input.
                if normalized_path != "/" and not normalized_path.startswith("/"):
                    raise _fail("root_escape", "root")
                paths.append("/" if normalized_path == "/" else _root_path(normalized_path))
            except SourceManifestError:
                raise _fail("root_escape", "root")
        if len(paths) != 1:
            raise _fail("record_malformed", "root")
        return paths[0]


def _run_bounded_process(
    argv: Sequence[str], input_bytes: bytes, timeout_seconds: float, max_output_bytes: int,
    *, environment: Mapping[str, str] | None = None,
) -> SftpProcessResult:
    """Run the protocol process while bounding retained stdout and stderr."""
    process_environment = dict(os.environ)
    process_environment.update(environment or {})
    process = subprocess.Popen(
        list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=process_environment, start_new_session=os.name != "nt",
    )
    stdout_chunks: list[bytes] = []
    stdout_size = 0
    stderr_size = 0
    overflow = threading.Event()

    def drain(stream: object, retain: bool) -> None:
        nonlocal stdout_size, stderr_size
        while True:
            chunk = stream.read(65536)  # type: ignore[attr-defined]
            if not chunk:
                return
            if retain:
                stdout_size += len(chunk)
                if stdout_size > max_output_bytes:
                    overflow.set()
                    return
                stdout_chunks.append(chunk)
            else:
                stderr_size += len(chunk)
                if stderr_size > max_output_bytes:
                    overflow.set()
                    return

    stdout_thread = threading.Thread(target=drain, args=(process.stdout, True), daemon=True)
    stderr_thread = threading.Thread(target=drain, args=(process.stderr, False), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    def kill_process() -> None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return
            except (OSError, ProcessLookupError):
                pass
        process.kill()

    try:
        if process.stdin is not None:
            process.stdin.write(input_bytes)
            process.stdin.close()
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        while process.poll() is None:
            if overflow.is_set():
                kill_process()
                break
            if time.monotonic() >= deadline:
                timed_out = True
                kill_process()
                break
            time.sleep(0.01)
        returncode = process.wait()
    finally:
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
    # A descendant can retain a pipe after the LFTP parent exits.  Do not
    # synthesize completion from a partially drained stream in that case.
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        return SftpProcessResult(b"", returncode, truncated=True)
    if overflow.is_set():
        return SftpProcessResult(b"".join(stdout_chunks), returncode, truncated=True)
    return SftpProcessResult(b"".join(stdout_chunks), returncode, timed_out=timed_out)


def _parse_lftp_listing(output: bytes, root: str) -> list[SourceManifestEntry]:
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        raise _fail("record_malformed")
    entries: list[SourceManifestEntry] = []
    current = root
    sentinel_seen = False
    root_header_seen = False
    find_root_seen = False
    stage_index = 0
    stage_markers_seen = False
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    for line_index, raw_line in enumerate(nonempty_lines):
        if raw_line == _SENTINEL:
            if (
                sentinel_seen or line_index != len(nonempty_lines) - 1
                or stage_markers_seen and stage_index != len(_STAGE_MARKERS)
            ):
                raise _fail("sentinel_malformed")
            sentinel_seen = True
            continue
        if raw_line in _STAGE_MARKERS:
            if stage_index >= len(_STAGE_MARKERS) or raw_line != _STAGE_MARKERS[stage_index]:
                raise _fail("sentinel_malformed")
            stage_index += 1
            stage_markers_seen = True
            continue
        if not raw_line.strip() or raw_line.startswith("total "):
            continue
        # LFTP acknowledges a successful `cd` on stdout, including the remote
        # absolute path.  It is transport progress, never a manifest record;
        # discard it before parsing so it cannot enter persisted artifacts.
        if raw_line.startswith(_LFTP_PROGRESS_PREFIXES):
            continue
        if not root_header_seen and _LFTP_FIND_ROOT_RE.match(raw_line):
            find_root_seen = True
            continue
        match = _LFTP_LONG_RE.match(raw_line)
        if match:
            raw_name = match.group("name")
            if root_header_seen:
                path = current + "/" + _safe_name(raw_name)
            else:
                # `find -l .` is the supported recursive LFTP form.  Its
                # long records are rooted explicitly at `./`, so retain that
                # boundary instead of accepting an unqualified listing.
                if not raw_name.startswith("./"):
                    raise _fail("root_listing_missing")
                relative = raw_name[2:]
                if relative in ("", "."):
                    find_root_seen = True
                    continue
                path = relative
                find_root_seen = True
            mode = match.group("mode")
            if not _SAFE_MODE_RE.match(mode):
                raise _fail("special_entry")
            marker = mode[0]
            if marker == "l":
                raise _fail("symlink_entry")
            if marker not in "-d":
                raise _fail("special_entry")
            kind = "directory" if marker == "d" else "file"
            size = int(match.group("size"))
            if size > _MAX_BYTES:
                raise _fail("invalid_size")
            metadata = match.group("mtime")
            mode_value = 0
            for offset, triplet in enumerate((mode[1:4], mode[4:7], mode[7:10])):
                bits = 0
                if triplet[0] in "rR":
                    bits |= 4
                if triplet[1] in "wW":
                    bits |= 2
                if triplet[2] in "xXsStT":
                    bits |= 1
                mode_value |= bits << (6 - offset * 3)
            entries.append(SourceManifestEntry(
                _relative_entry_path(path, root), kind, 0 if kind == "directory" else size,
                int.from_bytes(
                    hashlib.sha256(metadata.encode("utf-8")).digest()[:8], "big",
                ) & _MAX_BYTES,
                mode_value,
            ))
            continue
        if raw_line.endswith(":") and not raw_line.startswith(" "):
            candidate = raw_line[:-1]
            if candidate in (".", root):
                root_header_seen = True
                current = root
                continue
            relative_header = _relative_entry_path(candidate, root)
            current = root + "/" + relative_header
            continue
        raise _fail("record_malformed")
    if not sentinel_seen:
        raise _fail("sentinel_missing")
    if not root_header_seen and not find_root_seen:
        raise _fail("root_listing_missing")
    return entries


def _failure_stage_from_root_preflight(output: bytes) -> str:
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return "launch"
    return "pwd" if _ROOT_PREFLIGHT_MARKERS[0] in lines else "open"


def _failure_stage_from_output(output: bytes) -> str:
    """Classify an LFTP failure using fixed markers, never retained process text."""
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return "launch"
    marker_index = -1
    for index, marker in enumerate(_STAGE_MARKERS):
        if marker in lines:
            marker_index = index
    if marker_index < 0:
        return "launch"
    # Markers are written immediately after open/cd and immediately before the
    # recursive find.  They identify the operation that could have failed next.
    return ("root", "enumeration", "enumeration")[marker_index]


class SourceManifestHarness:
    """Capture and persist stable, read-only SFTP source manifests."""

    def __init__(
        self,
        configured_root: str,
        protocol_runner: Callable[..., object] | None = None,
        *,
        timeout_seconds: float = 30.0,
        max_entries: int = 100_000,
        max_output_bytes: int = 64 * 1024 * 1024,
        remote: str | None = None,
        host: str | None = None,
        port: int = 22,
        username: str | None = None,
        password: str | None = None,
        known_hosts_file: str | os.PathLike[str] | None = None,
    ):
        self.root = _root_path(configured_root)
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or timeout_seconds > 300:
            raise _fail("invalid_configuration")
        if type(max_entries) is not int or max_entries <= 0 or max_entries > 1_000_000:
            raise _fail("invalid_configuration")
        if type(max_output_bytes) is not int or max_output_bytes <= 0 or max_output_bytes > 512 * 1024 * 1024:
            raise _fail("invalid_configuration")
        self.timeout_seconds = float(timeout_seconds)
        self.max_entries = max_entries
        self.max_output_bytes = max_output_bytes
        if protocol_runner is None and remote is None and host is None:
            remote = os.environ.get("INCOMING_RECOVERY_SFTP_REMOTE")
        if protocol_runner is None and remote is None and host is not None:
            remote = "sftp://" + host
        if protocol_runner is None and remote is not None:
            protocol_runner = ReadOnlySftpProtocolRunner(
                remote, host=host, port=port, username=username, password=password,
                known_hosts_file=known_hosts_file,
            )
        self.protocol_runner = protocol_runner

    def _run(self) -> SourceManifestSnapshot:
        if self.protocol_runner is None:
            raise _fail("invalid_configuration")
        try:
            result = self.protocol_runner(
                self.root, timeout_seconds=self.timeout_seconds,
                max_output_bytes=self.max_output_bytes,
            )
        except TypeError:
            # Small injected runners are convenient in tests; the bounded
            # keyword form remains the maintained contract.
            try:
                result = self.protocol_runner(self.root)
            except Exception:
                raise _fail("protocol_failure")
        except Exception:
            raise _fail("protocol_failure")
        if isinstance(result, SftpProcessResult):
            if result.failure_reason is not None:
                raise _fail(result.failure_reason, result.failure_stage)
            if result.timed_out:
                raise _fail("process_timeout")
            if result.truncated:
                raise _fail("output_truncated")
            if result.sentinel_seen is False:
                raise _fail("sentinel_missing")
            if result.returncode != 0:
                raise _fail("process_failed")
            output = result.stdout
        elif isinstance(result, Mapping):
            output = result.get("stdout")
            if result.get("failure_reason") is not None:
                raise _fail(str(result.get("failure_reason")), result.get("failure_stage") if isinstance(result.get("failure_stage"), str) else None)
            if result.get("timed_out"):
                raise _fail("process_timeout")
            if result.get("truncated"):
                raise _fail("output_truncated")
            if result.get("sentinel_seen") is False:
                raise _fail("sentinel_missing")
            if type(result.get("returncode", 0)) is not int or result.get("returncode", 0) != 0:
                raise _fail("process_failed")
        elif isinstance(result, (bytes, str)):
            output = result
        elif isinstance(result, Iterable):
            try:
                records = list(result)
            except Exception:
                raise _fail("protocol_failure")
            protocol_records = []
            for item in records:
                if isinstance(item, SourceManifestEntry):
                    protocol_records.append(_encode_entry(item))
                elif isinstance(item, Mapping):
                    record = dict(item)
                    record.setdefault("record", "entry")
                    protocol_records.append(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                else:
                    raise _fail("record_malformed")
                if len(protocol_records) > self.max_entries:
                    raise _fail("too_many_entries")
            output = b"\n".join(protocol_records)
            if protocol_records:
                output += b"\n"
            output += json.dumps({
                "record": "end", "sentinel": _SENTINEL,
                "entry_count": len(records),
                "file_count": sum(
                    isinstance(item, SourceManifestEntry) and item.kind == "file"
                    or isinstance(item, Mapping) and item.get("kind") == "file"
                    for item in records
                ),
                "total_bytes": sum(
                    item.size if isinstance(item, SourceManifestEntry) and item.kind == "file"
                    else item.get("size", 0) if isinstance(item, Mapping) and item.get("kind") == "file"
                    else 0
                    for item in records
                ),
            }, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        else:
            raise _fail("protocol_failure")
        if isinstance(output, bytes) and len(output) > self.max_output_bytes:
            raise _fail("output_too_large")
        if isinstance(output, str) and len(output.encode("utf-8")) > self.max_output_bytes:
            raise _fail("output_too_large")
        try:
            entries, _ = _parse_protocol_output(output, self.root, max_entries=self.max_entries)
        except SourceManifestError:
            raise
        file_entries = [entry for entry in entries if entry.kind == "file"]
        return SourceManifestSnapshot(
            _SCHEMA, len(file_entries), sum(entry.size for entry in file_entries),
            _manifest_digest(entries),
            hashlib.sha256(self.root.encode("utf-8")).hexdigest(),
        )

    def snapshot(self) -> SourceManifestSnapshot:
        return self._run()

    capture = snapshot

    def capture_stable(self, *, delay_seconds: float = 0.0) -> SourceManifestSnapshot:
        first = self.snapshot()
        if delay_seconds:
            if delay_seconds < 0 or delay_seconds > 300:
                raise _fail("invalid_configuration")
            time.sleep(delay_seconds)
        second = self.snapshot()
        comparison = compare_source_snapshots(first, second)
        if not comparison.stable:
            raise _fail("unstable_snapshot")
        return second

    def compare_stability(
        self, first: SourceManifestSnapshot, second: SourceManifestSnapshot,
    ) -> SourceManifestStability:
        return compare_source_snapshots(first, second)

    def write_snapshot(self, snapshot: SourceManifestSnapshot, output_path: str | os.PathLike[str]) -> None:
        if not isinstance(snapshot, SourceManifestSnapshot) or snapshot.schema != _SCHEMA:
            raise _fail("artifact_failure")
        _write_private_atomic(snapshot.as_artifact(), output_path)

    def write_failure(self, exc: SourceManifestError, output_path: str | os.PathLike[str]) -> None:
        """Persist only the allowlisted failure classification beside a manifest."""
        destination = Path(output_path)
        payload = _redacted_artifact_error(exc)
        # Replace any prior success at the canonical path first: consumers
        # cannot mistake an earlier manifest for current evidence after a
        # failed two-snapshot attempt.  Keep a sibling failure record for
        # operators that collect only explicit failure artifacts.
        _write_private_atomic(payload, destination)
        _write_private_atomic(payload, destination.with_name(destination.name + ".failure"))

    def capture_stable_to(self, output_path: str | os.PathLike[str], *, delay_seconds: float = 0.0) -> SourceManifestSnapshot:
        snapshot = self.capture_stable(delay_seconds=delay_seconds)
        self.write_snapshot(snapshot, output_path)
        return snapshot


def compare_source_snapshots(first: SourceManifestSnapshot, second: SourceManifestSnapshot) -> SourceManifestStability:
    if not isinstance(first, SourceManifestSnapshot) or not isinstance(second, SourceManifestSnapshot):
        raise _fail("invalid_metadata")
    stable = (
        first.schema == second.schema == _SCHEMA
        and first.file_count == second.file_count
        and first.total_bytes == second.total_bytes
        and first.path_metadata_digest == second.path_metadata_digest
    )
    return SourceManifestStability(stable, "stable" if stable else "snapshot_changed", first, second)


def _write_private_atomic(payload: Mapping[str, object], output_path: str | os.PathLike[str]) -> None:
    try:
        destination = Path(output_path)
        if not destination.name or destination.name in (".", ".."):
            raise OSError
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        temporary = destination.with_name("." + destination.name + "." + secrets.token_hex(8) + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            try:
                directory_descriptor = os.open(destination.parent, os.O_RDONLY)
            except OSError:
                directory_descriptor = -1
            if directory_descriptor >= 0:
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except (OSError, TypeError, ValueError):
        try:
            if "temporary" in locals() and temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise _fail("artifact_failure")


# Names used by callers that prefer the longer, explicit role description.
SftpSourceManifestHarness = SourceManifestHarness
SftpSourceManifest = SourceManifestHarness
SourceManifestCollector = SourceManifestHarness
redacted_manifest_error = _redacted_artifact_error
