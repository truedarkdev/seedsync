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


def test_real_lftp_runner_adds_completion_only_after_the_process_finishes(monkeypatch):
    listing = (
        "source_manifest_stage_started\n"
        "source_manifest_stage_connected\n"
        "source_manifest_stage_root\n"
        "drwxr-xr-x                      - - ./\n"
        "-rw-r--r-- 1 remoteuser remoteuser 3 2026-01-01 00:00 ./file.bin\n"
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
    assert "source_manifest_complete" not in script
    assert all("private-password" not in str(argument) for argument in argv)
    assert all("sftp://" not in str(argument) for argument in argv)
    assert "put" not in script and "rm " not in script

    bare = runner.ReadOnlySftpProtocolRunner("sftp://seedbox.invalid")
    monkeypatch.setattr(runner._manifest, "_run_bounded_process", lambda *args, **kwargs: runner.SftpProcessResult(
        b"source_manifest_stage_started\n", returncode=1,
    ))
    incomplete = bare("fixture/incoming", timeout_seconds=5, max_output_bytes=10_000)
    assert incomplete.failure_stage == "connection"
    assert incomplete.failure_reason == "process_failed"
    with pytest.raises(runner._manifest.SourceManifestError, match="process_failed") as raised:
        runner.SourceManifestHarness("fixture/incoming", lambda _root: incomplete).snapshot()
    assert runner.redacted_manifest_error(raised.value)["stage"] == "connection"


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
