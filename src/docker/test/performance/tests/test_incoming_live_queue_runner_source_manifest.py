import importlib.util
import hashlib
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest


RUNNER_SPEC = importlib.util.spec_from_file_location(
    "incoming_live_queue_runner",
    Path(__file__).parents[1] / "incoming_live_queue_runner.py",
)
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
assert RUNNER_SPEC.loader is not None
RUNNER_SPEC.loader.exec_module(runner)


def _protocol(path="file.bin", size=3):
    return (
        json.dumps({
            "record": "entry", "path": path, "kind": "file", "size": size,
            "mtime_ns": 1, "mode": 0o644,
        }) + "\n"
        + json.dumps({
            "record": "end", "sentinel": "source_manifest_complete",
            "entry_count": 1, "file_count": 1, "total_bytes": size,
        }) + "\n"
    )


def test_source_manifest_mode_uses_external_config_and_does_not_construct_queue(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    connection_path = tmp_path / "connection.json"
    known_hosts_path = tmp_path / "known_hosts"
    output_path = tmp_path / "source.json"
    known_hosts_path.write_text("seedbox.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    connection_path.write_text(json.dumps({
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser", "remote_path": "fixture",
        "password": "private-password", "known_hosts_file": str(known_hosts_path),
    }), encoding="utf-8")
    if sys.platform != "win32":
        connection_path.chmod(0o600)
    config_path.write_text(json.dumps({
        "source_manifest": {
            "root": "incoming",
            "connection_config_path": str(connection_path),
            "artifact_path": str(output_path),
        },
        "key_path": str(tmp_path / "unused-key"),
    }), encoding="utf-8")

    class FakeHarness:
        def __init__(self, root, **kwargs):
            assert root == "fixture/incoming"
            assert kwargs["host"] == "seedbox.invalid"
            assert kwargs["port"] == 2222
            assert kwargs["username"] == "remoteuser"
            assert kwargs["password"] == "private-password"
            assert kwargs["known_hosts_file"] == str(known_hosts_path)
            self.kwargs = kwargs

        def capture_stable_to(self, path, **kwargs):
            assert path == str(output_path)
            assert kwargs["delay_seconds"] == 0.0

    monkeypatch.setattr(runner, "SourceManifestHarness", FakeHarness)
    monkeypatch.setattr(runner, "QueueGate", lambda *args, **kwargs: pytest.fail("Queue must not be constructed"))
    assert runner.main(str(config_path), source_manifest=True) == 0


def test_protected_content_compare_mode_is_explicit_and_bypasses_queue(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    config_path.write_text(json.dumps({"protected_content_compare": {"placeholder": True}}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(runner, "_private_protected_content_compare", lambda path, config, section: calls.append((path, config, section)) or 0)
    monkeypatch.setattr(runner, "QueueGate", lambda *_args, **_kwargs: pytest.fail("Queue must not be constructed"))
    assert runner.main(str(config_path), protected_content_compare=True) == 0
    assert calls[0][0] == config_path
    with pytest.raises(SystemExit, match="exclusive"):
        runner.main(str(config_path), protected_content_compare=True, source_manifest=True)


def test_protected_content_compare_hashes_exact_inode_only_set_and_persists_opaque_results(monkeypatch, tmp_path):
    local = tmp_path / "local"
    entries = []
    hashes = {}
    for index in range(11):
        relative = f"group-{index}/leaf.bin"
        target = local / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = f"payload-{index}".encode()
        target.write_bytes(payload)
        hashes[relative] = hashlib.sha256(payload).hexdigest()
        entries.append({"path": relative, "inode": index, "size": len(payload), "mtime_ns": 1})
    current = [{**entry, "inode": entry["inode"] + 100} for entry in entries]
    baseline_path, provenance_path, artifact_path = (tmp_path / "baseline.json", tmp_path / "provenance.json", tmp_path / "artifact.json")
    baseline_path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    provenance_path.write_text(json.dumps({"entries": current, "root": "/mounts/protected"}), encoding="utf-8")
    config_path, connection_path, known_hosts = tmp_path / "runner.json", tmp_path / "connection.json", tmp_path / "known_hosts"
    known_hosts.write_text("test ssh-ed25519 AAAA\n", encoding="utf-8")
    connection_path.write_text(json.dumps({"host":"test","port":22,"username":"user","password":"secret","remote_path":"base"}), encoding="utf-8")
    config = {"source_manifest":{"root":"Incoming","trusted_absolute_root":"/base/Incoming","connection_config_path":str(connection_path),"known_hosts_file":str(known_hosts)},"protected_content_compare":{"baseline_path":str(baseline_path),"provenance_path":str(provenance_path),"artifact_path":str(artifact_path),"expected_entries":11,"max_total_bytes":sum(e["size"] for e in entries)}}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    real_path = runner.Path
    monkeypatch.setattr(runner, "Path", lambda *parts: local / parts[1] if len(parts) == 2 and parts[0] == "/mounts/protected" else real_path(*parts))
    class FakeProtocol:
        def __init__(self, **_kwargs): pass
        def hash_file(self, root, path, size, *, timeout_seconds):
            assert root == "/base/Incoming" and size > 0 and timeout_seconds == 900.0
            return hashes[path]
    monkeypatch.setattr(runner, "ReadOnlySftpProtocolRunner", FakeProtocol)
    assert runner._private_protected_content_compare(config_path, config, config["protected_content_compare"]) == 0
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["all_match"] and len(artifact["entries"]) == 11
    rendered = artifact_path.read_text(encoding="utf-8")
    assert "group-0" not in rendered and hashes["group-0/leaf.bin"] not in rendered


def test_source_manifest_main_resolves_trusted_absolute_base_once(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    settings_path = tmp_path / "settings.cfg"
    known_hosts_path = tmp_path / "known_hosts"
    output_path = tmp_path / "source.json"
    known_hosts_path.write_text("seedbox.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    settings_path.write_text(
        "[Lftp]\nremote_address = seedbox.invalid\nremote_port = 2222\n"
        "remote_username = remoteuser\nremote_password = private-password\n"
        "remote_path = /home/remoteuser/files\n",
        encoding="utf-8",
    )
    if sys.platform != "win32":
        settings_path.chmod(0o660)
    config_path.write_text(json.dumps({"source_manifest": {
        "root": "nested/Incoming", "connection_config_path": str(settings_path),
        "known_hosts_file": str(known_hosts_path), "artifact_path": str(output_path),
    }}), encoding="utf-8")

    calls = []
    class FakeHarness:
        def __init__(self, root, **kwargs):
            calls.append((root, kwargs))
        def capture_stable_to(self, path, **kwargs):
            assert path == str(output_path)
            assert kwargs["delay_seconds"] == 0.0

    monkeypatch.setattr(runner, "SourceManifestHarness", FakeHarness)
    monkeypatch.setattr(runner, "QueueGate", lambda *_args, **_kwargs: pytest.fail("Queue must not be constructed"))
    assert runner.main(str(config_path), source_manifest=True) == 0
    assert calls == [("/home/remoteuser/files/nested/Incoming", {
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser",
        "password": "private-password", "known_hosts_file": str(known_hosts_path),
        "timeout_seconds": 30.0, "max_entries": 100_000,
        "max_output_bytes": 64 * 1024 * 1024,
    })]


def test_source_manifest_config_alone_does_not_switch_queue_mode(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    config_path.write_text(json.dumps({
        "source_manifest": {}, "key_path": str(tmp_path / "key"),
        "pair_id": "pair", "pair_name": "pair", "root_id": "root",
        "root_name": "root", "artifact_path": str(tmp_path / "artifact"),
        "passive_paths": ["/server/status"],
    }), encoding="utf-8")
    calls = []

    class FakeGate:
        def __init__(self, *args, **kwargs):
            calls.append("queue")

        def preflight(self, _get):
            calls.append("preflight")

    class FakeSampler:
        def __init__(self, *args, **kwargs):
            calls.append("sampler")

        def sample(self, _get):
            calls.append("sample")

    monkeypatch.setattr(runner, "QueueGate", FakeGate)
    monkeypatch.setattr(runner._observer, "FixedGetSampler", FakeSampler)
    monkeypatch.setattr(runner, "SourceManifestHarness", lambda *args, **kwargs: pytest.fail("manifest mode must be explicit"))
    assert runner.main(str(config_path), dry_run=True) == 0
    assert calls == ["queue", "preflight", "sampler", "sample"]


def test_source_manifest_flag_requires_config_and_config_is_not_argv_secret(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    config_path.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(SystemExit, match="source_manifest config is required"):
        runner.main(str(config_path), source_manifest=True)


def test_source_manifest_reads_existing_lftp_settings_through_protected_reference(tmp_path):
    settings_path = tmp_path / "settings.cfg"
    known_hosts_path = tmp_path / "known_hosts"
    known_hosts_path.write_text("seedbox.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    settings_path.write_text(
        "[Lftp]\nremote_address = seedbox.invalid\nremote_port = 2222\n"
        "remote_username = remoteuser\nremote_password = private-password\nremote_path = fixture\n",
        encoding="utf-8",
    )
    if sys.platform != "win32":
        settings_path.chmod(0o660)
    connection = runner._source_manifest_connection(
        {}, {"connection_config_path": str(settings_path), "known_hosts_file": str(known_hosts_path)},
    )
    assert connection == {
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser",
        "password": "private-password", "known_hosts_file": str(known_hosts_path), "remote_path": "fixture",
        "connection_config_path": str(settings_path),
    }


@pytest.mark.parametrize(("root", "base", "expected"), [
    ("Incoming", "downloads/public/collection", "downloads/public/collection/Incoming"),
    ("downloads/public/collection/Incoming", "downloads/public/collection", "downloads/public/collection/Incoming"),
    ("nested/Incoming", "downloads/public/collection", "downloads/public/collection/nested/Incoming"),
    ("Incoming", "/home/remoteuser/files", "/home/remoteuser/files/Incoming"),
    ("nested/Incoming", "/home/remoteuser/files", "/home/remoteuser/files/nested/Incoming"),
])
def test_account_home_manifest_root_resolves_selected_or_full_relative_path_once(root, base, expected):
    assert runner._account_home_manifest_root(root, base) == expected


@pytest.mark.parametrize("root,base", [
    ("/absolute", "base"), ("../escape", "base"), ("base/base/base/base/Incoming", "base/base"),
    ("base/base/Incoming", "base"), ("Incoming", "../base"), ("Incoming", "sftp://host/base"),
])
def test_account_home_manifest_root_rejects_escape_and_double_prefix(root, base):
    with pytest.raises(SystemExit, match="root configuration"):
        runner._account_home_manifest_root(root, base)


def test_trusted_absolute_root_overrides_stale_relative_base_for_manifest_and_preflight():
    trusted = "/home/remoteuser/canonical/Incoming"
    assert runner._trusted_manifest_root("Incoming", "stale/base", trusted) == trusted
    assert runner._source_root_preflight_candidates_with_trusted_root("Incoming", "stale/base", trusted) == (
        "stale/base/Incoming", trusted,
    )


@pytest.mark.parametrize("trusted", (
    "relative/Incoming", "/home/remoteuser/canonical/not-incoming", "/home/../canonical/Incoming",
    "/home//canonical/Incoming", "/home/canonical/Incoming?query", "/home/canonical/Incoming#fragment",
    "/home/canonical/Incoming\x01",
))
def test_trusted_absolute_root_rejects_unbound_value(trusted):
    with pytest.raises(SystemExit, match="trusted absolute root"):
        runner._trusted_manifest_root("Incoming", "stale/base", trusted)


def test_trusted_absolute_root_requires_owner_private_runner_config(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    config_path.write_text(json.dumps({"source_manifest": {
        "root": "Incoming", "trusted_absolute_root": "/home/remoteuser/canonical/Incoming",
        "connection_config_path": str(tmp_path / "connection.json"),
        "root_preflight_artifact_path": str(tmp_path / "artifact.json"),
    }}), encoding="utf-8")
    monkeypatch.setattr(runner.os, "name", "posix")
    config_path.chmod(0o644)
    with pytest.raises(SystemExit, match="requires private config"):
        runner.main(str(config_path), source_root_preflight=True)


def test_real_lftp_runner_adds_completion_only_after_the_process_finishes(monkeypatch):
    listing = (
        "source_manifest_phase_open\n"
        "source_manifest_phase_root\n"
        "source_manifest_phase_enumeration\n"
        "drwxr-xr-x                      - - ./\n"
        "-rw-r--r-- 1 remoteuser remoteuser 3 2026-01-01 00:00 ./file.bin\n"
        ""
    ).encode("utf-8")

    calls = []

    def fake_process(argv, input_bytes, timeout_seconds, max_output_bytes, *, environment):
        calls.append((argv, input_bytes, timeout_seconds, max_output_bytes, environment))
        return runner.SftpProcessResult(listing, returncode=0)

    monkeypatch.setattr(runner._manifest, "_run_bounded_process", fake_process)
    result = runner.ReadOnlySftpProtocolRunner(
        host="seedbox.invalid", port=2222, username="remoteuser",
        password="private-password", known_hosts_file="known_hosts",
    )(
        "fixture/incoming", timeout_seconds=5, max_output_bytes=10_000,
    )
    assert result.returncode == 0
    assert result.sentinel_seen is True
    assert runner.SourceManifestHarness(
        "fixture/incoming", lambda _root: result,
    ).snapshot().total_bytes == 3
    argv, input_bytes, *_ = calls[0]
    script = input_bytes.decode("utf-8")
    assert "private-password" in script
    assert "-p 2222" in script
    assert "known_hosts" in script
    assert "set cmd:fail-exit yes" in script
    assert argv == ["lftp", "--norc"]
    assert "--quiet" not in script
    assert "find -l ." in script
    assert "source_manifest_phase_open" in script
    assert "source_manifest_phase_root" in script
    assert "source_manifest_phase_enumeration" in script
    assert "source_manifest_complete" not in script
    assert all("private-password" not in str(argument) for argument in argv)
    assert all("sftp://" not in str(argument) for argument in argv)
    assert "put" not in script and "rm " not in script

    bare = runner.ReadOnlySftpProtocolRunner("sftp://seedbox.invalid")
    monkeypatch.setattr(runner._manifest, "_run_bounded_process", lambda *args, **kwargs: runner.SftpProcessResult(
        b"source_manifest_phase_open\n", returncode=1,
    ))
    incomplete = bare("fixture/incoming", timeout_seconds=5, max_output_bytes=10_000)
    assert incomplete.failure_stage == "root"
    assert incomplete.failure_reason == "process_failed"
    with pytest.raises(runner._manifest.SourceManifestError, match="process_failed") as raised:
        runner.SourceManifestHarness("fixture/incoming", lambda _root: incomplete).snapshot()
    assert runner.redacted_manifest_error(raised.value)["stage"] == "root"


def test_source_manifest_failure_writes_only_private_allowlisted_classification(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    output_path = tmp_path / "source.json"
    connection_path = tmp_path / "connection.json"
    known_hosts_path = tmp_path / "known_hosts"
    connection_path.write_text(json.dumps({
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser", "remote_path": "fixture",
    }), encoding="utf-8")
    known_hosts_path.write_text("seedbox.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    if sys.platform != "win32":
        connection_path.chmod(0o600)
    config_path.write_text(json.dumps({"source_manifest": {
        "root": "incoming", "connection_config_path": str(connection_path),
        "known_hosts_file": str(known_hosts_path), "artifact_path": str(output_path),
    }}), encoding="utf-8")

    class FailingHarness:
        def __init__(self, *_args, **_kwargs):
            pass

        def capture_stable_to(self, *_args, **_kwargs):
            raise runner.SourceManifestError("process_failed", "listing")

        def write_failure(self, exc, path):
            runner._manifest._write_private_atomic(runner.redacted_manifest_error(exc), Path(path).with_name(Path(path).name + ".failure"))

    monkeypatch.setattr(runner, "SourceManifestHarness", FailingHarness)
    with pytest.raises(runner.SourceManifestError, match="process_failed"):
        runner.main(str(config_path), source_manifest=True)
    payload = json.loads((tmp_path / "source.json.failure").read_text(encoding="utf-8"))
    assert payload == {
        "schema": "incoming-recovery-source-manifest-error.v1",
        "reason": "process_failed", "stage": "listing",
    }
    assert "fixture" not in str(payload)


def test_root_preflight_mode_uses_external_config_and_never_constructs_queue_or_manifest(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    connection_path = tmp_path / "connection.json"
    known_hosts_path = tmp_path / "known_hosts"
    artifact_path = tmp_path / "root-preflight.json"
    known_hosts_path.write_text("seedbox.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    connection_path.write_text(json.dumps({
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser",
        "password": "private-password", "remote_path": "fixture",
    }), encoding="utf-8")
    if sys.platform != "win32":
        connection_path.chmod(0o600)
    config_path.write_text(json.dumps({"source_manifest": {
        "root": "Incoming", "connection_config_path": str(connection_path),
        "known_hosts_file": str(known_hosts_path), "root_preflight_artifact_path": str(artifact_path),
    }}), encoding="utf-8")
    calls = []

    class FakeProtocol:
        def __init__(self, **kwargs):
            calls.append(("protocol", kwargs))

    class FakePreflight:
        def __init__(self, protocol, **kwargs):
            calls.append(("preflight", protocol, kwargs))
        def capture_to(self, path, **kwargs):
            calls.append(("capture", path, kwargs))

    monkeypatch.setattr(runner, "ReadOnlySftpRealpathRunner", FakeProtocol)
    monkeypatch.setattr(runner, "RootOnlySftpPreflight", FakePreflight)
    monkeypatch.setattr(runner, "SourceManifestHarness", lambda *args, **kwargs: pytest.fail("manifest must not run"))
    monkeypatch.setattr(runner, "QueueGate", lambda *args, **kwargs: pytest.fail("Queue must not be constructed"))
    assert runner.main(str(config_path), source_root_preflight=True) == 0
    assert calls[-1] == ("capture", str(artifact_path), {"relative_root": "fixture/Incoming", "trusted_absolute_root": None})
    assert calls[0][1]["askpass_program"].endswith("incoming_sftp_askpass.py")


def test_root_preflight_absolute_config_passes_only_trusted_absolute(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    connection_path = tmp_path / "connection.json"
    known_hosts_path = tmp_path / "known_hosts"
    known_hosts_path.write_text("seedbox.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    connection_path.write_text(json.dumps({
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser",
        "remote_path": "/home/remoteuser/files",
    }), encoding="utf-8")
    if sys.platform != "win32":
        connection_path.chmod(0o600)
    config_path.write_text(json.dumps({"source_manifest": {
        "root": "nested/Incoming", "connection_config_path": str(connection_path),
        "known_hosts_file": str(known_hosts_path), "root_preflight_artifact_path": str(tmp_path / "out.json"),
    }}), encoding="utf-8")
    captured = {}
    monkeypatch.setattr(runner, "ReadOnlySftpRealpathRunner", lambda **_kwargs: object())
    class FakePreflight:
        def __init__(self, *_args, **_kwargs): pass
        def capture_to(self, _path, **kwargs): captured.update(kwargs)
    monkeypatch.setattr(runner, "RootOnlySftpPreflight", FakePreflight)
    monkeypatch.setattr(runner, "QueueGate", lambda *args, **kwargs: pytest.fail("Queue must not be constructed"))
    assert runner.main(str(config_path), source_root_preflight=True) == 0
    assert captured == {"relative_root": None, "trusted_absolute_root": "/home/remoteuser/files/nested/Incoming"}


def test_root_preflight_realpath_transport_uses_private_askpass_reference(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    connection_path = tmp_path / "connection.json"
    known_hosts_path = tmp_path / "known_hosts"
    askpass_path = tmp_path / "askpass"
    known_hosts_path.write_text("seedbox.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    askpass_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    connection_path.write_text(json.dumps({
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser",
        "password": "private-password", "remote_path": "fixture",
    }), encoding="utf-8")
    if sys.platform != "win32":
        connection_path.chmod(0o600)
    config_path.write_text(json.dumps({"source_manifest": {
        "root": "Incoming", "connection_config_path": str(connection_path),
        "known_hosts_file": str(known_hosts_path), "root_preflight_artifact_path": str(tmp_path / "out.json"),
        "root_preflight_transport": "sftp_realpath", "root_preflight_askpass_program": str(askpass_path),
    }}), encoding="utf-8")
    captured = {}
    class FakeRealpath:
        def __init__(self, **kwargs): captured.update(kwargs)
    class FakePreflight:
        def __init__(self, *_args, **_kwargs): pass
        def capture_to(self, *_args, **_kwargs): pass
    monkeypatch.setenv("INCOMING_RECOVERY_LOCAL_LFTP", "1")
    monkeypatch.setattr(runner, "ReadOnlySftpRealpathRunner", FakeRealpath)
    monkeypatch.setattr(runner, "RootOnlySftpPreflight", FakePreflight)
    monkeypatch.setattr(runner, "QueueGate", lambda *_args, **_kwargs: pytest.fail("Queue must not be constructed"))
    assert runner.main(str(config_path), source_root_preflight=True) == 0
    assert captured["askpass_program"] == str(askpass_path)
    assert captured["connection_config_path"] == str(connection_path)
    assert captured["host"] == "seedbox.invalid"
    assert "private-password" not in str(captured)


def test_root_preflight_rejects_credentialed_lftp_transport(monkeypatch, tmp_path):
    config_path = tmp_path / "runner.json"
    connection_path = tmp_path / "connection.json"
    known_hosts_path = tmp_path / "known_hosts"
    known_hosts_path.write_text("seedbox.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    connection_path.write_text(json.dumps({
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser",
        "password": "private-password", "remote_path": "fixture",
    }), encoding="utf-8")
    if sys.platform != "win32":
        connection_path.chmod(0o600)
    config_path.write_text(json.dumps({"source_manifest": {
        "root": "Incoming", "connection_config_path": str(connection_path),
        "known_hosts_file": str(known_hosts_path), "root_preflight_artifact_path": str(tmp_path / "out.json"),
        "root_preflight_transport": "lftp_pwd",
    }}), encoding="utf-8")
    monkeypatch.setattr(runner, "QueueGate", lambda *_args, **_kwargs: pytest.fail("Queue must not be constructed"))
    with pytest.raises(SystemExit, match="credential-safe"):
        runner.main(str(config_path), source_root_preflight=True)


def test_sftp_askpass_reads_protected_connection_only_to_its_stdout(tmp_path):
    connection_path = tmp_path / "connection.json"
    password = "fixture-password-only-in-child-pipe"
    connection_path.write_text(json.dumps({
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser", "password": password,
        "remote_path": "fixture",
    }), encoding="utf-8")
    if sys.platform != "win32":
        connection_path.chmod(0o600)
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "incoming_sftp_askpass.py")],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "INCOMING_RECOVERY_SFTP_ASKPASS": "1", "INCOMING_RECOVERY_SFTP_ASKPASS_CONFIG": str(connection_path)},
    )
    assert completed.returncode == 0
    assert completed.stdout == password
    assert completed.stderr == ""
