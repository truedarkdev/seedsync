#!/usr/bin/env python3
"""Externally configured, one-shot Incoming Queue caller for the maintained observer."""
from __future__ import annotations

import json
import importlib.util
import hashlib
import os
import configparser
from pathlib import Path
import secrets
import stat
import sys
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote

_helper_path = Path(__file__).with_name("incoming-recovery-observer.py")
if not _helper_path.is_file():
    _helper_path = Path(__file__).with_name("incoming_recovery_observer.py")
_spec = importlib.util.spec_from_file_location("incoming_recovery_observer", _helper_path)
if _spec is None or _spec.loader is None:
    raise RuntimeError("maintained observer helper is unavailable")
_observer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _observer
_spec.loader.exec_module(_observer)
PassiveQueueCaller = _observer.PassiveQueueCaller
QueueGate = _observer.QueueGate
QueueTransportResponse = _observer.QueueTransportResponse

_manifest_path = Path(__file__).with_name("incoming_sftp_source_manifest.py")
_manifest_spec = importlib.util.spec_from_file_location("incoming_sftp_source_manifest", _manifest_path)
if _manifest_spec is None or _manifest_spec.loader is None:
    raise RuntimeError("source manifest helper is unavailable")
_manifest = importlib.util.module_from_spec(_manifest_spec)
sys.modules[_manifest_spec.name] = _manifest
_manifest_spec.loader.exec_module(_manifest)
SourceManifestHarness = _manifest.SourceManifestHarness
SftpSourceManifestHarness = _manifest.SftpSourceManifestHarness
SourceManifestCollector = _manifest.SourceManifestCollector
SourceManifestError = _manifest.SourceManifestError
SftpProcessResult = _manifest.SftpProcessResult
SourceManifestEntry = _manifest.SourceManifestEntry
SourceManifestSnapshot = _manifest.SourceManifestSnapshot
SourceManifestStability = _manifest.SourceManifestStability
ReadOnlySftpProtocolRunner = _manifest.ReadOnlySftpProtocolRunner
RootOnlySftpPreflight = _manifest.RootOnlySftpPreflight
ReadOnlySftpRealpathRunner = _manifest.ReadOnlySftpRealpathRunner
redacted_manifest_error = _manifest.redacted_manifest_error
compare_source_snapshots = _manifest.compare_source_snapshots


def _private_protected_content_compare(
    config_file: Path, config: dict[str, object], section: dict[str, object],
) -> int:
    """Compare exactly the retained inode-only protected entries to source.

    Both retained manifests and the job artifact are owner-private.  Paths and
    digest values stay in memory; the artifact contains only opaque IDs, sizes,
    match booleans, and allowlisted statuses.
    """
    if config_file.stat().st_mode & 0o777 != 0o600 and os.name != "nt":
        raise SystemExit("protected_content_compare requires private config")
    fields = ("baseline_path", "provenance_path", "artifact_path", "expected_entries", "max_total_bytes")
    if any(name not in section for name in fields):
        raise SystemExit("protected_content_compare config is incomplete")
    if not all(isinstance(section[name], str) and section[name] for name in fields[:3]):
        raise SystemExit("protected_content_compare config is invalid")
    if type(section["expected_entries"]) is not int or type(section["max_total_bytes"]) is not int:
        raise SystemExit("protected_content_compare config is invalid")
    try:
        baseline = json.loads(Path(section["baseline_path"]).read_text(encoding="utf-8"))
        provenance = json.loads(Path(section["provenance_path"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SystemExit("protected_content_compare manifests are unavailable")
    if not isinstance(baseline, dict) or not isinstance(provenance, dict):
        raise SystemExit("protected_content_compare manifests are invalid")
    baseline_entries, current_entries, local_root = baseline.get("entries"), provenance.get("entries"), provenance.get("root")
    if not isinstance(baseline_entries, list) or not isinstance(current_entries, list) or not isinstance(local_root, str):
        raise SystemExit("protected_content_compare manifests are invalid")
    if not local_root.startswith("/mounts/") or ".." in local_root.split("/") or "//" in local_root:
        raise SystemExit("protected_content_compare root is invalid")
    try:
        before = {item["path"]: item for item in baseline_entries if isinstance(item, dict)}
        after = {item["path"]: item for item in current_entries if isinstance(item, dict)}
    except (KeyError, TypeError):
        raise SystemExit("protected_content_compare manifests are invalid")
    selected: list[tuple[str, dict[str, object]]] = []
    for path, old in before.items():
        new = after.get(path)
        if not isinstance(path, str) or not isinstance(old, dict) or not isinstance(new, dict):
            raise SystemExit("protected_content_compare manifests are invalid")
        if old.get("inode") != new.get("inode") and old.get("size") == new.get("size") and old.get("mtime_ns") == new.get("mtime_ns"):
            if type(old.get("size")) is not int or old["size"] < 0:
                raise SystemExit("protected_content_compare manifests are invalid")
            try:
                _manifest._relative_entry_path(path, "protected")
            except SourceManifestError:
                raise SystemExit("protected_content_compare manifests are invalid")
            selected.append((path, old))
    selected.sort(key=lambda item: item[0])
    if len(selected) != section["expected_entries"] or sum(int(item[1]["size"]) for item in selected) != section["max_total_bytes"]:
        raise SystemExit("protected_content_compare target set is invalid")
    # Validate every local target before opening a source connection.
    local_hashes: dict[str, str] = {}
    for path, entry in selected:
        target = Path(local_root, path)
        try:
            metadata = target.lstat()
        except OSError:
            _manifest._write_private_atomic({"schema": "incoming-recovery-protected-content.v1", "status": "local_missing"}, section["artifact_path"])
            raise SystemExit("protected_content_compare local target is unavailable")
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != entry["size"]:
            _manifest._write_private_atomic({"schema": "incoming-recovery-protected-content.v1", "status": "local_size_mismatch"}, section["artifact_path"])
            raise SystemExit("protected_content_compare local target is unavailable")
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        local_hashes[path] = digest.hexdigest()
    source_section = config.get("source_manifest")
    if not isinstance(source_section, dict):
        raise SystemExit("protected_content_compare source config is unavailable")
    connection = _source_manifest_connection(config, source_section, base_dir=config_file.resolve().parent)
    root = _trusted_manifest_root(
        source_section.get("configured_root", source_section.get("root")), connection["remote_path"],
        source_section.get("trusted_absolute_root"),
    )
    protocol = ReadOnlySftpProtocolRunner(
        host=connection["host"], port=connection["port"], username=connection["username"],
        password=connection["password"], known_hosts_file=connection["known_hosts_file"],
    )
    timeout_seconds = section.get("timeout_seconds", 900.0)
    results = []
    for path, entry in selected:
        opaque_id = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
        status = "match"
        try:
            remote_hash = protocol.hash_file(root, path, int(entry["size"]), timeout_seconds=timeout_seconds)
            if not secrets.compare_digest(local_hashes[path], remote_hash):
                status = "hash_mismatch"
        except SourceManifestError as exc:
            status = exc.reason
        results.append({"id": opaque_id, "size": entry["size"], "hash_match": status == "match", "status": status})
        if status != "match":
            break
    payload = {"schema": "incoming-recovery-protected-content.v1", "entries": results,
               "expected_entries": len(selected), "all_match": len(results) == len(selected) and all(item["hash_match"] for item in results)}
    _manifest._write_private_atomic(payload, section["artifact_path"])
    if not payload["all_match"]:
        raise SystemExit("protected_content_compare failed")
    return 0


def _read_protected_source_config(config_path: Path) -> dict[str, object]:
    """Read the existing app's SFTP connection material from a protected file."""
    try:
        if not config_path.is_file():
            raise OSError
        if os.name != "nt":
            mode = config_path.stat().st_mode & 0o777
            # SeedSync's generated settings are owner/group protected (0660).
            if mode & 0o007 or not mode & 0o400:
                raise OSError
        text = config_path.read_text(encoding="utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            parser = configparser.RawConfigParser(interpolation=None)
            parser.read_string(text)
            if not parser.has_section("Lftp"):
                raise ValueError
            lftp = parser["Lftp"]
            return {
                "host": lftp.get("remote_address"),
                "port": lftp.getint("remote_port", fallback=22),
                "username": lftp.get("remote_username"),
                "password": lftp.get("remote_password"),
                "remote_path": lftp.get("remote_path"),
            }
        if not isinstance(payload, dict):
            raise ValueError
        nested = payload.get("sftp", payload.get("lftp", payload))
        if not isinstance(nested, dict):
            raise ValueError
        return nested
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, configparser.Error):
        raise SystemExit("source_manifest connection config is unavailable")


def _source_manifest_connection(
    config: dict[str, object], section: dict[str, object], *, base_dir: Path | None = None,
) -> dict[str, object]:
    reference = (
        section.get("connection_config_path")
        or section.get("credentials_path")
        or section.get("connection_path")
        or section.get("config_path")
    )
    if reference is None:
        reference = config.get("sftp_config_path")
    if not isinstance(reference, str) or not reference:
        raise SystemExit("source_manifest requires a protected connection_config_path")
    reference_path = Path(reference)
    if base_dir is not None and not reference_path.is_absolute():
        reference_path = base_dir / reference_path
    connection = _read_protected_source_config(reference_path)
    host = connection.get("host", connection.get("hostname", connection.get("remote_host")))
    username = connection.get("username", connection.get("user", connection.get("remote_user")))
    password = connection.get("password")
    port = connection.get("port", connection.get("remote_port", 22))
    known_hosts = (
        section.get("known_hosts_file")
        or config.get("known_hosts_file")
        or connection.get(
            "known_hosts_file",
            connection.get(
                "host_key_file",
                connection.get("known_hosts", connection.get("known_hosts_path", connection.get("host_key_path"))),
            ),
        )
    )
    if (
        not isinstance(host, str) or not host or not isinstance(username, str) or not username
        or (password is not None and not isinstance(password, str))
        or type(port) is not int or not 1 <= port <= 65535
        or not isinstance(known_hosts, str) or not known_hosts
    ):
        raise SystemExit("source_manifest connection config is incomplete")
    known_hosts_path = Path(known_hosts)
    if base_dir is not None and not known_hosts_path.is_absolute():
        known_hosts_path = base_dir / known_hosts_path
    if not known_hosts_path.is_file():
        raise SystemExit("source_manifest host key reference is unavailable")
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "known_hosts_file": str(known_hosts_path),
        "remote_path": connection.get("remote_path"),
        "connection_config_path": str(reference_path),
    }


def _account_home_manifest_root(root: object, remote_path: object) -> str:
    """Join a trusted configured base to one contained selected SFTP root."""
    if not isinstance(root, str) or not isinstance(remote_path, str):
        raise SystemExit("source_manifest root configuration is incomplete")
    def parts(value: str, *, selected: bool) -> tuple[bool, tuple[str, ...]]:
        if (
            not value
            or "\\" in value
            or "//" in value
            or "://" in value
            or any(ch in value for ch in "?#")
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        ):
            raise SystemExit("source_manifest root configuration is invalid")
        absolute = value.startswith("/")
        if selected and absolute:
            raise SystemExit("source_manifest root configuration is invalid")
        result = tuple(part for part in value.split("/") if part)
        if not result or any(not part or part in (".", "..") for part in result):
            raise SystemExit("source_manifest root configuration is invalid")
        return absolute, result
    base_absolute, base = parts(remote_path, selected=False)
    _, selected = parts(root, selected=True)
    if not base:
        raise SystemExit("source_manifest root configuration is invalid")
    if not base_absolute and selected[:len(base)] == base:
        if len(selected) == len(base):
            raise SystemExit("source_manifest root configuration is invalid")
        resolved = selected
    else:
        resolved = base + selected
    if len(resolved) >= len(base) * 2 and resolved[:len(base) * 2] == base + base:
        raise SystemExit("source_manifest root configuration is invalid")
    return ("/" if base_absolute else "") + "/".join(resolved)


def _source_root_preflight_candidates(root: object, remote_path: object) -> tuple[str | None, str | None]:
    """Return only trusted configured root forms; PWD-derived forms stay in memory."""
    resolved = _account_home_manifest_root(root, remote_path)
    if resolved.startswith("/"):
        return None, resolved
    return resolved, None


def _trusted_manifest_root(root: object, remote_path: object, trusted_absolute_root: object = None) -> str:
    """Select a protected canonical absolute root when one is explicitly bound."""
    resolved = _account_home_manifest_root(root, remote_path)
    if trusted_absolute_root is None:
        return resolved
    if (
        not isinstance(trusted_absolute_root, str)
        or not trusted_absolute_root.startswith("/")
        or trusted_absolute_root == "/"
        or "\\" in trusted_absolute_root
        or "//" in trusted_absolute_root
        or "://" in trusted_absolute_root
        or any(ch in trusted_absolute_root for ch in "?#")
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in trusted_absolute_root)
    ):
        raise SystemExit("source_manifest trusted absolute root is invalid")
    trusted_parts = tuple(part for part in trusted_absolute_root.split("/") if part)
    selected_parts = tuple(part for part in root.split("/") if part) if isinstance(root, str) else ()
    if (
        not trusted_parts
        or not selected_parts
        or any(part in (".", "..") for part in trusted_parts)
        or len(trusted_parts) < len(selected_parts)
        or trusted_parts[-len(selected_parts):] != selected_parts
    ):
        raise SystemExit("source_manifest trusted absolute root is invalid")
    return "/" + "/".join(trusted_parts)


def _source_root_preflight_candidates_with_trusted_root(root: object, remote_path: object, trusted_absolute_root: object = None) -> tuple[str | None, str | None]:
    resolved = _account_home_manifest_root(root, remote_path)
    explicit = _trusted_manifest_root(root, remote_path, trusted_absolute_root)
    if trusted_absolute_root is not None:
        return (None if resolved.startswith("/") else resolved), explicit
    return _source_root_preflight_candidates(root, remote_path)


def _require_private_trusted_root_config(config_path: Path) -> None:
    if os.name == "nt":
        return
    try:
        mode = config_path.stat().st_mode & 0o777
    except OSError:
        raise SystemExit("source_manifest trusted absolute root config is unavailable")
    if mode != 0o600:
        raise SystemExit("source_manifest trusted absolute root requires private config")


class HttpAdapter:
    """Keep secrets in the caller and return an explicit Queue status result."""
    def __init__(self, key_path: Path, opener=urllib.request.urlopen):
        self.key_path, self.opener = key_path, opener

    def request(self, path: str, method: str = "GET"):
        key = self.key_path.read_text(encoding="utf-8").strip()
        request = urllib.request.Request("http://127.0.0.1:8800" + path, method=method,
            headers={"Authorization": "Bearer " + key})
        try:
            with self.opener(request, timeout=35 if method == "POST" else 5) as response:
                body = response.read()
                if method == "POST":
                    return QueueTransportResponse(response.status, body)
                return json.loads(body.decode("utf-8"))
        except HTTPError as error:
            if method != "POST":
                raise
            return QueueTransportResponse(error.code, error.read())


def main(
    config_path: str, dry_run: bool = False, source_manifest: bool = False, source_root_preflight: bool = False,
    protected_content_compare: bool = False,
) -> int:
    config_file = Path(config_path)
    config = json.loads(config_file.read_text(encoding="utf-8"))
    source_manifest_config = config.get("source_manifest")
    if source_manifest_config is not None and not isinstance(source_manifest_config, dict):
        raise SystemExit("source_manifest config must be an object")
    source_manifest_requested = source_manifest is True or "--source-manifest" in sys.argv[1:-1]
    root_preflight_requested = source_root_preflight is True or "--source-root-preflight" in sys.argv[1:-1]
    content_compare_requested = protected_content_compare is True or "--protected-content-compare" in sys.argv[1:-1]
    if sum((source_manifest_requested, root_preflight_requested, content_compare_requested)) > 1:
        raise SystemExit("recovery diagnostic modes are exclusive")
    if content_compare_requested:
        section = config.get("protected_content_compare")
        if not isinstance(section, dict):
            raise SystemExit("protected_content_compare config is required")
        return _private_protected_content_compare(config_file, config, section)
    if root_preflight_requested:
        if source_manifest_config is None:
            raise SystemExit("source_manifest config is required")
        root = source_manifest_config.get("configured_root", source_manifest_config.get("root"))
        if source_manifest_config.get("trusted_absolute_root") is not None:
            _require_private_trusted_root_config(config_file)
        output_path = source_manifest_config.get("root_preflight_artifact_path")
        if not isinstance(root, str) or not isinstance(output_path, str):
            raise SystemExit("root preflight requires root and root_preflight_artifact_path")
        connection = _source_manifest_connection(
            config, source_manifest_config, base_dir=Path(config_path).resolve().parent,
        )
        relative_root, trusted_absolute_root = _source_root_preflight_candidates_with_trusted_root(
            root, connection["remote_path"], source_manifest_config.get("trusted_absolute_root"),
        )
        transport = source_manifest_config.get("root_preflight_transport", "sftp_realpath")
        if transport == "sftp_realpath":
            askpass_program = str(Path(__file__).with_name("incoming_sftp_askpass.py").resolve())
            if "root_preflight_askpass_program" in source_manifest_config:
                if os.environ.get("INCOMING_RECOVERY_LOCAL_LFTP") != "1":
                    raise SystemExit("root preflight askpass override is local-only")
                askpass_program = source_manifest_config["root_preflight_askpass_program"]
            if not isinstance(askpass_program, str) or not askpass_program:
                raise SystemExit("root preflight askpass program is invalid")
            askpass_path = Path(askpass_program)
            if not askpass_path.is_absolute():
                askpass_path = Path(config_path).resolve().parent / askpass_path
            protocol = ReadOnlySftpRealpathRunner(
                host=connection["host"], port=connection["port"], username=connection["username"],
                known_hosts_file=connection["known_hosts_file"], askpass_program=str(askpass_path),
                connection_config_path=str(connection["connection_config_path"]),
            )
        else:
            raise SystemExit("root preflight requires the credential-safe SFTP canonical transport")
        preflight = RootOnlySftpPreflight(
            protocol, timeout_seconds=source_manifest_config.get("timeout_seconds", 30.0),
            max_output_bytes=source_manifest_config.get("root_preflight_max_output_bytes", 64 * 1024),
        )
        preflight.capture_to(output_path, relative_root=relative_root, trusted_absolute_root=trusted_absolute_root)
        return 0
    if source_manifest_requested:
        if source_manifest_config is None:
            raise SystemExit("source_manifest config is required")
        root = source_manifest_config.get("configured_root", source_manifest_config.get("root"))
        if source_manifest_config.get("trusted_absolute_root") is not None:
            _require_private_trusted_root_config(config_file)
        output_path = source_manifest_config.get("artifact_path", source_manifest_config.get("output_path"))
        if not isinstance(root, str) or not isinstance(output_path, str):
            raise SystemExit("source_manifest requires root and artifact_path")
        connection = _source_manifest_connection(
            config, source_manifest_config, base_dir=Path(config_path).resolve().parent,
        )
        root = _trusted_manifest_root(root, connection["remote_path"], source_manifest_config.get("trusted_absolute_root"))
        harness = SourceManifestHarness(
            root,
            host=connection["host"], port=connection["port"],
            username=connection["username"], password=connection["password"],
            known_hosts_file=connection["known_hosts_file"],
            timeout_seconds=source_manifest_config.get("timeout_seconds", 30.0),
            max_entries=source_manifest_config.get("max_entries", 100_000),
            max_output_bytes=source_manifest_config.get("max_output_bytes", 64 * 1024 * 1024),
        )
        try:
            harness.capture_stable_to(output_path, delay_seconds=source_manifest_config.get("delay_seconds", 0.0))
        except SourceManifestError as exc:
            harness.write_failure(exc, output_path)
            raise
        return 0
    adapter = HttpAdapter(Path(config["key_path"]))
    gate = QueueGate(config["pair_id"], config["pair_name"], config["root_id"],
        config["root_name"], require_pending_transfer=True, artifact_path=config["artifact_path"],
        attempt_state_path=config.get("attempt_state_path"))
    if dry_run:
        gate.preflight(adapter.request)
        _observer.FixedGetSampler(tuple(config["passive_paths"]), artifact_path=config["artifact_path"],
            continue_on_error=False).sample(adapter.request)
        return 0
    if os.environ.get("INCOMING_RECOVERY_ALLOW_QUEUE") != "1":
        raise SystemExit("Queue gate must be explicitly enabled")
    caller = PassiveQueueCaller(gate, adapter.request,
        lambda path: adapter.request(path, "POST"), tuple(config["passive_paths"]),
        config["artifact_path"], os.environ, 35)
    caller.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(
        sys.argv[-1], "--dry-run" in sys.argv[1:-1],
        source_manifest="--source-manifest" in sys.argv[1:-1],
        source_root_preflight="--source-root-preflight" in sys.argv[1:-1],
        protected_content_compare="--protected-content-compare" in sys.argv[1:-1],
    ))
