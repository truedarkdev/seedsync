"""Externally configured, one-shot Incoming Queue caller for the maintained observer."""
from __future__ import annotations

import json
import importlib.util
import os
import configparser
from pathlib import Path
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
redacted_manifest_error = _manifest.redacted_manifest_error
compare_source_snapshots = _manifest.compare_source_snapshots


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
    }


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


def main(config_path: str, dry_run: bool = False, source_manifest: bool = False) -> int:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    source_manifest_config = config.get("source_manifest")
    if source_manifest_config is not None and not isinstance(source_manifest_config, dict):
        raise SystemExit("source_manifest config must be an object")
    source_manifest_requested = source_manifest is True or "--source-manifest" in sys.argv[1:-1]
    if source_manifest_requested:
        if source_manifest_config is None:
            raise SystemExit("source_manifest config is required")
        root = source_manifest_config.get("configured_root", source_manifest_config.get("root"))
        output_path = source_manifest_config.get("artifact_path", source_manifest_config.get("output_path"))
        if not isinstance(root, str) or not isinstance(output_path, str):
            raise SystemExit("source_manifest requires root and artifact_path")
        connection = _source_manifest_connection(
            config, source_manifest_config, base_dir=Path(config_path).resolve().parent,
        )
        harness = SourceManifestHarness(
            root,
            host=connection["host"], port=connection["port"],
            username=connection["username"], password=connection["password"],
            known_hosts_file=connection["known_hosts_file"],
            timeout_seconds=source_manifest_config.get("timeout_seconds", 30.0),
            max_entries=source_manifest_config.get("max_entries", 100_000),
            max_output_bytes=source_manifest_config.get("max_output_bytes", 64 * 1024 * 1024),
        )
        harness.capture_stable_to(output_path, delay_seconds=source_manifest_config.get("delay_seconds", 0.0))
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
    raise SystemExit(main(sys.argv[-1], "--dry-run" in sys.argv[1:-1]))
