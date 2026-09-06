import importlib.util
from pathlib import Path
import json
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
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser",
        "password": "private-password", "known_hosts_file": str(known_hosts_path),
    }), encoding="utf-8")
    if sys.platform != "win32":
        connection_path.chmod(0o600)
    config_path.write_text(json.dumps({
        "source_manifest": {
            "root": "/fixture/incoming",
            "connection_config_path": str(connection_path),
            "artifact_path": str(output_path),
        },
        "key_path": str(tmp_path / "unused-key"),
    }), encoding="utf-8")

    class FakeHarness:
        def __init__(self, root, **kwargs):
            assert root == "/fixture/incoming"
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
        "remote_username = remoteuser\nremote_password = private-password\n",
        encoding="utf-8",
    )
    if sys.platform != "win32":
        settings_path.chmod(0o660)
    connection = runner._source_manifest_connection(
        {}, {"connection_config_path": str(settings_path), "known_hosts_file": str(known_hosts_path)},
    )
    assert connection == {
        "host": "seedbox.invalid", "port": 2222, "username": "remoteuser",
        "password": "private-password", "known_hosts_file": str(known_hosts_path),
    }


def test_real_lftp_runner_requires_exact_terminal_sentinel(monkeypatch):
    listing = (
        "/fixture/incoming:\n"
        "-rw-r--r-- 1 remoteuser remoteuser 3 2026-01-01 00:00 file.bin\n"
        "source_manifest_complete\n"
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
        "/fixture/incoming", timeout_seconds=5, max_output_bytes=10_000,
    )
    assert result.returncode == 0
    assert result.sentinel_seen is True
    assert runner.SourceManifestHarness(
        "/fixture/incoming", lambda _root: result,
    ).snapshot().total_bytes == 3
    argv, input_bytes, *_ = calls[0]
    script = input_bytes.decode("utf-8")
    assert "private-password" in script
    assert "-p 2222" in script
    assert "known_hosts" in script
    assert "set cmd:fail-exit yes" in script
    assert all("private-password" not in str(argument) for argument in argv)
    assert all("sftp://" not in str(argument) for argument in argv)
    assert "put" not in script and "rm " not in script

    bare = runner.ReadOnlySftpProtocolRunner("sftp://seedbox.invalid")
    monkeypatch.setattr(runner._manifest, "_run_bounded_process", lambda *args, **kwargs: runner.SftpProcessResult(
        listing.replace(b"source_manifest_complete\n", b""), returncode=0,
    ))
    incomplete = bare("/fixture/incoming", timeout_seconds=5, max_output_bytes=10_000)
    assert incomplete.sentinel_seen is False
    with pytest.raises(runner._manifest.SourceManifestError, match="sentinel_missing"):
        runner.SourceManifestHarness("/fixture/incoming", lambda _root: incomplete).snapshot()
