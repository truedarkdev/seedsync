import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import pytest


SPEC = importlib.util.spec_from_file_location(
    "incoming_sftp_source_manifest",
    Path(__file__).parents[1] / "incoming_sftp_source_manifest.py",
)
manifest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manifest
assert SPEC.loader is not None
SPEC.loader.exec_module(manifest)


ROOT = "fixture/incoming"


def protocol(records):
    payload = [dict(record) for record in records]
    files = [record for record in payload if record.get("kind") == "file"]
    payload.append({
        "record": "end",
        "sentinel": "source_manifest_complete",
        "entry_count": len(records),
        "file_count": len(files),
        "total_bytes": sum(record.get("size", 0) for record in files),
    })
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in payload)


def nested_records(count=400):
    records = []
    for index in range(count):
        directory = f"branch-{index % 20:02d}/leaf-{index % 10:02d}"
        records.append({
            "record": "entry", "path": f"{directory}/file-{index:04d}.bin",
            "kind": "file", "size": index + 1, "mtime_ns": index + 100,
            "mode": 0o644,
        })
    return records


def test_nested_fixture_is_deterministic_and_has_normalized_digest():
    records = nested_records()
    first = manifest.SourceManifestHarness(ROOT, lambda _root: protocol(records)).snapshot()
    shuffled = list(reversed(records))
    second = manifest.SourceManifestHarness(ROOT, lambda _root: protocol(shuffled)).snapshot()
    assert first.file_count == 400
    assert first.total_bytes == sum(range(1, 401))
    assert first.path_metadata_digest == second.path_metadata_digest
    assert first.root_digest == second.root_digest


def test_injected_protocol_is_read_only_and_has_no_side_effect_commands():
    calls = []

    def runner(root, **limits):
        calls.append((root, limits))
        return protocol(nested_records(2))

    harness = manifest.SourceManifestHarness(ROOT, runner)
    snapshot = harness.snapshot()
    assert snapshot.file_count == 2
    assert calls[0][0] == ROOT
    assert calls[0][1]["timeout_seconds"] == 30.0
    assert calls[0][1]["max_output_bytes"] > 0


@pytest.mark.parametrize(
    "result",
    [
        protocol(nested_records(1)).rsplit("{", 1)[0],
        manifest.SftpProcessResult(protocol(nested_records(1)), returncode=1),
        manifest.SftpProcessResult(protocol(nested_records(1)), truncated=True),
        manifest.SftpProcessResult(protocol(nested_records(1)), timed_out=True),
    ],
)
def test_incomplete_or_failed_protocol_output_is_rejected(result):
    harness = manifest.SourceManifestHarness(ROOT, lambda _root: result)
    with pytest.raises(manifest.SourceManifestError):
        harness.snapshot()


def test_missing_terminal_sentinel_is_rejected():
    records = nested_records(1)
    output = "".join(json.dumps(record) + "\n" for record in records)
    with pytest.raises(manifest.SourceManifestError, match="sentinel_missing"):
        manifest.SourceManifestHarness(ROOT, lambda _root: output).snapshot()


def test_unstable_two_snapshot_capture_fails_closed_without_writing_output(tmp_path):
    outputs = iter((protocol(nested_records(1)), protocol(nested_records(2))))
    output_path = tmp_path / "source-manifest.json"
    harness = manifest.SourceManifestHarness(ROOT, lambda _root: next(outputs))
    with pytest.raises(manifest.SourceManifestError, match="unstable_snapshot"):
        harness.capture_stable_to(output_path)
    assert not output_path.exists()


def test_duplicate_cycle_symlink_and_special_name_fail_closed():
    records = nested_records(1)
    cases = [
        records + records,
        [{"record": "entry", "path": "loop", "kind": "directory", "size": 0, "mtime_ns": 1, "mode": 0o755}] * 2,
        [{"record": "entry", "path": "link", "kind": "symlink", "size": 0, "mtime_ns": 1, "mode": 0}],
        [{"record": "entry", "path": "bad\nname", "kind": "file", "size": 1, "mtime_ns": 1, "mode": 0o644}],
    ]
    for case in cases:
        with pytest.raises(manifest.SourceManifestError):
            manifest.SourceManifestHarness(ROOT, lambda _root, case=case: protocol(case)).snapshot()


def test_root_confinement_rejects_absolute_and_escape_paths():
    record = {"record": "entry", "path": "/other/file", "kind": "file", "size": 1, "mtime_ns": 1, "mode": 0o644}
    with pytest.raises(manifest.SourceManifestError, match="root_escape"):
        manifest.SourceManifestHarness(ROOT, lambda _root: protocol([record])).snapshot()

    record["path"] = "/fixture/incoming/file"
    with pytest.raises(manifest.SourceManifestError, match="root_escape"):
        manifest.SourceManifestHarness(ROOT, lambda _root: protocol([record])).snapshot()

    absolute_root = "/fixture/incoming"
    assert manifest.SourceManifestHarness(absolute_root, lambda _root: protocol([])).root == absolute_root
    entry = manifest._entry_from_record({
        "record": "entry", "path": "/fixture/incoming/file", "kind": "file", "size": 1,
        "mtime_ns": 1, "mode": 0o644,
    }, absolute_root)
    assert entry.path == "file"
    with pytest.raises(manifest.SourceManifestError, match="root_escape"):
        manifest._entry_from_record({
            "record": "entry", "path": "/fixture/incoming/fixture/incoming/file", "kind": "file", "size": 1,
            "mtime_ns": 1, "mode": 0o644,
        }, absolute_root)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("child.bin", "child.bin"),
        ("fixture/incoming/child.bin", "child.bin"),
        ("fixture/incoming/nested/child.bin", "nested/child.bin"),
        ("selected folder/child [final] (1)+$!.bin", "selected folder/child [final] (1)+$!.bin"),
    ],
)
def test_account_home_relative_entry_paths_normalize_once(path, expected):
    entry = manifest._entry_from_record({
        "record": "entry", "path": path, "kind": "file", "size": 1,
        "mtime_ns": 1, "mode": 0o644,
    }, ROOT)
    assert entry.path == expected


@pytest.mark.parametrize(
    "path",
    [
        "/fixture/incoming/child.bin",
        "../child.bin",
        "fixture/incoming/../child.bin",
        "fixture/incoming/fixture/incoming/child.bin",
    ],
)
def test_account_home_relative_entry_paths_reject_absolute_traversal_and_double_prefix(path):
    with pytest.raises(manifest.SourceManifestError, match="root_escape"):
        manifest._entry_from_record({
            "record": "entry", "path": path, "kind": "file", "size": 1,
            "mtime_ns": 1, "mode": 0o644,
        }, ROOT)


def test_account_home_relative_duplicate_paths_fail_after_normalization():
    records = [
        {"record": "entry", "path": "child.bin", "kind": "file", "size": 1, "mtime_ns": 1, "mode": 0o644},
        {"record": "entry", "path": "fixture/incoming/child.bin", "kind": "file", "size": 1, "mtime_ns": 1, "mode": 0o644},
    ]
    with pytest.raises(manifest.SourceManifestError, match="duplicate_entry"):
        manifest.SourceManifestHarness(ROOT, lambda _root: protocol(records)).snapshot()


def test_snapshot_output_is_private_atomic_and_redacts_root_and_names(tmp_path):
    output = tmp_path / "manifest.json"
    harness = manifest.SourceManifestHarness(
        "private/credential-root", lambda _root: protocol(nested_records(1)),
    )
    snapshot = harness.capture_stable()
    harness.write_snapshot(snapshot, output)
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    text = output.read_text(encoding="utf-8")
    assert "private/credential-root" not in text
    assert "file-0000.bin" not in text
    assert "path_metadata_digest" in json.loads(text)

    error = manifest.redacted_manifest_error(manifest.SourceManifestError("special_name"))
    assert "credential-root" not in str(error)
    assert "file-0000.bin" not in str(error)


def test_explicit_host_cannot_be_overridden_by_inherited_remote(monkeypatch):
    monkeypatch.setenv("INCOMING_RECOVERY_SFTP_REMOTE", "sftp://wrong.invalid")
    harness = manifest.SourceManifestHarness(ROOT, host="right.invalid", username="user")
    assert harness.protocol_runner.host == "right.invalid"


def test_bracketed_ipv6_uri_is_normalized_without_uri_credentials():
    protocol = manifest.ReadOnlySftpProtocolRunner("sftp://[2001:db8::1]")
    assert protocol.host == "[2001:db8::1]"


def test_lftp_listing_requires_root_header_and_parses_colon_filename_before_header_logic():
    listing = (
        b"fixture/incoming:\n"
        b"-rw-r--r-- 1 user group 4 2026-01-01 00:00 legal:name:\n"
        b"source_manifest_complete\n"
    )
    records = manifest._parse_lftp_listing(listing, ROOT)
    assert records[0].path == "legal:name:"

    with pytest.raises(manifest.SourceManifestError, match="root_listing_missing"):
        manifest._parse_lftp_listing(b"source_manifest_complete\n", ROOT)
    with pytest.raises(manifest.SourceManifestError, match="root_listing_missing"):
        manifest._parse_lftp_listing(
            b"-rw-r--r-- 1 user group 4 2026-01-01 00:00 file\nsource_manifest_complete\n",
            ROOT,
        )


def test_lftp_relative_listing_keeps_selected_nested_root_and_special_names():
    listing = (
        b"source_manifest_phase_open\n"
        b"source_manifest_phase_root\n"
        b"source_manifest_phase_enumeration\n"
        b"drwxr-xr-x                      - - ./\n"
        b"-rw-r--r-- user/group 4 2026-01-01 00:00 ./fixture/incoming/selected/child [1].bin\n"
        b"source_manifest_complete\n"
    )
    records = manifest._parse_lftp_listing(listing, ROOT)
    assert records[0].path == "selected/child [1].bin"


def test_lftp_find_listing_requires_ordered_fixed_markers_and_discards_progress_path():
    listing = (
        b"source_manifest_phase_open\n"
        b"source_manifest_phase_root\n"
        b"cd ok, cwd=/private/source/root\n"
        b"source_manifest_phase_enumeration\n"
        b"drwxr-xr-x                      - - ./\n"
        b"-rw-r--r-- user/group 4 2026-01-01 00:00 ./nested/file.bin\n"
        b"source_manifest_complete\n"
    )
    records = manifest._parse_lftp_listing(listing, ROOT)
    assert records[0].path == "nested/file.bin"

    with pytest.raises(manifest.SourceManifestError, match="sentinel_malformed"):
        manifest._parse_lftp_listing(listing.replace(
            b"source_manifest_phase_root\n", b"",
        ), ROOT)


def test_lftp_listing_strips_trusted_absolute_root_prefix_once():
    absolute_root = "/home/remoteuser/files/nested/Incoming"
    listing = (
        b"source_manifest_phase_open\nsource_manifest_phase_root\n"
        b"/home/remoteuser/files/nested/Incoming:\n"
        b"-rw-r--r-- user/group 1 2026-01-01 00:00 child.bin\n"
        b"source_manifest_phase_enumeration\n"
        b"source_manifest_complete\n"
    )
    assert manifest._parse_lftp_listing(listing, absolute_root)[0].path == "child.bin"


def test_lftp_high_bit_timestamp_hash_is_normalized_before_protocol_encoding():
    timestamp = "2026-09-06 11:34:00"
    raw = int.from_bytes(hashlib.sha256(timestamp.encode("utf-8")).digest()[:8], "big")
    assert raw > manifest._MAX_BYTES
    records = manifest._parse_lftp_listing(
        (
            "source_manifest_phase_open\nsource_manifest_phase_root\n"
            "source_manifest_phase_enumeration\ndrwxr-xr-x                      - - ./\n"
            f"-rw-r--r-- user/group 1 {timestamp} ./file.bin\nsource_manifest_complete\n"
        ).encode("utf-8"),
        ROOT,
    )
    assert 0 <= records[0].mtime_ns <= manifest._MAX_BYTES
    # The LFTP record is valid under the same JSON protocol range as normal entries.
    assert manifest._entry_from_record({
        "record": "entry", "path": "file.bin", "kind": "file", "size": 1,
        "mtime_ns": records[0].mtime_ns, "mode": 0o644,
    }, ROOT).mtime_ns == records[0].mtime_ns


def test_lftp_failure_stage_is_allowlisted_and_persisted_without_output():
    assert manifest._failure_stage_from_output(b"") == "launch"
    assert manifest._failure_stage_from_output(b"source_manifest_phase_open\n") == "root"
    assert manifest._failure_stage_from_output(
        b"source_manifest_phase_open\nsource_manifest_phase_root\n",
    ) == "enumeration"
    assert manifest._failure_stage_from_output(
        b"source_manifest_phase_open\nsource_manifest_phase_root\nsource_manifest_phase_enumeration\n",
    ) == "enumeration"

    harness = manifest.SourceManifestHarness(
        ROOT, lambda _root: manifest.SftpProcessResult(
            b"sensitive stdout", returncode=1,
            failure_stage="listing", failure_reason="process_failed",
        ),
    )
    with pytest.raises(manifest.SourceManifestError) as raised:
        harness.snapshot()
    artifact = manifest.redacted_manifest_error(raised.value)
    assert artifact == {
        "schema": "incoming-recovery-source-manifest-error.v1",
        "reason": "process_failed", "stage": "listing",
    }
    assert "sensitive" not in str(artifact)


def test_remote_uri_is_host_only_and_credentials_are_separate():
    for remote in (
        "sftp://user@example.invalid", "sftp://example.invalid:22",
        "sftp://example.invalid/root", "sftp://example.invalid?secret=1",
        "sftp://user:password@example.invalid", "sftp://example.invalid#fragment",
    ):
        with pytest.raises(manifest.SourceManifestError, match="invalid_configuration"):
            manifest.ReadOnlySftpProtocolRunner(remote)
    runner = manifest.ReadOnlySftpProtocolRunner(
        host="example.invalid", port=2222, username="user", password="secret",
    )
    assert runner.remote == "sftp://example.invalid"


def test_bounded_process_stops_on_stdout_or_stderr_overflow():
    result = manifest._run_bounded_process(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 100000); sys.stderr.write('e' * 100000)"],
        b"", 5, 1024,
    )
    assert result.truncated is True
    assert len(result.stdout) <= 1024


def test_bounded_process_rejects_a_pipe_held_after_parent_exit():
    result = manifest._run_bounded_process(
        [
            sys.executable, "-c",
            "import subprocess,sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)']); print('parent-exit')",
        ],
        b"", 5, 1024,
    )
    assert result.truncated is True
    assert result.stdout == b""


def test_failure_replaces_a_prior_success_with_private_allowlisted_error(tmp_path):
    output = tmp_path / "source.json"
    manifest._write_private_atomic({"schema": "incoming-recovery-source-manifest.v1", "file_count": 1}, output)
    harness = manifest.SourceManifestHarness(ROOT, lambda _root: protocol(nested_records(1)))
    harness.write_failure(manifest.SourceManifestError("process_failed", "listing"), output)
    expected = {
        "schema": "incoming-recovery-source-manifest-error.v1",
        "reason": "process_failed", "stage": "listing",
    }
    assert json.loads(output.read_text(encoding="utf-8")) == expected
    assert json.loads((tmp_path / "source.json.failure").read_text(encoding="utf-8")) == expected
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


def _root_preflight_output(marker, path):
    return (marker + "\n" + "sftp://remote.example" + path + "\n").encode("utf-8")


def test_root_only_preflight_uses_pwd_and_cd_without_listing_and_persists_only_digests(monkeypatch, tmp_path):
    protocol = manifest.ReadOnlySftpProtocolRunner(
        host="remote.example", port=2222, username="private-user", password="private-password",
        known_hosts_file="private-known-hosts",
    )
    calls = []

    def fake_process(candidate, **_kwargs):
        calls.append(candidate)
        if candidate is None:
            return manifest.SftpProcessResult(_root_preflight_output("source_root_preflight_pwd", "/home/remoteuser"))
        return manifest.SftpProcessResult(_root_preflight_output("source_root_preflight_candidate", "/home/remoteuser/files/Incoming"))

    monkeypatch.setattr(protocol, "root_preflight_process", fake_process)
    output_path = tmp_path / "root-preflight.json"
    result = manifest.RootOnlySftpPreflight(protocol).capture_to(
        output_path, relative_root="files/Incoming", trusted_absolute_root=None,
    )
    assert result.succeeded
    assert result.ambiguity == "equivalent"
    assert calls == [None, "files/Incoming", "/home/remoteuser/files/Incoming"]
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "incoming-recovery-source-root-preflight.v1"
    assert payload["ambiguity"] == "equivalent"
    assert "remote.example" not in str(payload)
    assert "private" not in str(payload)
    assert "/home/remoteuser" not in str(payload)
    if os.name != "nt":
        assert stat.S_IMODE(output_path.stat().st_mode) == 0o600


def test_root_only_preflight_fails_closed_for_missing_or_ambiguous_candidates(monkeypatch, tmp_path):
    protocol = manifest.ReadOnlySftpProtocolRunner(host="remote.example")
    responses = {
        None: manifest.SftpProcessResult(_root_preflight_output("source_root_preflight_pwd", "/home/user")),
        "files/Incoming": manifest.SftpProcessResult(b"source_root_preflight_open\n", returncode=1),
        "/home/user/files/Incoming": manifest.SftpProcessResult(b"source_root_preflight_open\n", returncode=1),
    }
    monkeypatch.setattr(protocol, "root_preflight_process", lambda candidate, **_kwargs: responses[candidate])
    output_path = tmp_path / "missing.json"
    with pytest.raises(manifest.SourceManifestError, match="root_candidate_missing"):
        manifest.RootOnlySftpPreflight(protocol).capture_to(output_path, relative_root="files/Incoming", trusted_absolute_root=None)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["ambiguity"] == "missing"
    assert payload["reason"] == "root_candidate_missing"

    def ambiguous(candidate, **_kwargs):
        if candidate is None:
            return manifest.SftpProcessResult(_root_preflight_output("source_root_preflight_pwd", "/home/user"))
        suffix = "/a" if candidate == "files/Incoming" else "/b"
        return manifest.SftpProcessResult(_root_preflight_output("source_root_preflight_candidate", suffix))

    monkeypatch.setattr(protocol, "root_preflight_process", ambiguous)
    with pytest.raises(manifest.SourceManifestError, match="root_candidate_ambiguous"):
        manifest.RootOnlySftpPreflight(protocol).capture_to(tmp_path / "ambiguous.json", relative_root="files/Incoming", trusted_absolute_root=None)


def test_root_only_process_script_is_strict_and_never_enumerates(monkeypatch):
    calls = []

    def fake_process(argv, input_bytes, timeout_seconds, max_output_bytes, *, environment):
        calls.append((argv, input_bytes.decode("utf-8"), timeout_seconds, max_output_bytes))
        return manifest.SftpProcessResult(_root_preflight_output("source_root_preflight_candidate", "/private/root"))

    monkeypatch.setattr(manifest, "_run_bounded_process", fake_process)
    protocol = manifest.ReadOnlySftpProtocolRunner(
        host="remote.example", port=2222, username="user", password="secret", known_hosts_file="known_hosts",
    )
    result = protocol.root_preflight_process("safe root/Incoming", timeout_seconds=5, max_output_bytes=4096)
    assert result.returncode == 0
    script = calls[0][1]
    assert "StrictHostKeyChecking=yes" in script
    assert "UserKnownHostsFile=" in script
    assert "cd \"safe root/Incoming\"" in script
    assert "pwd" in script
    assert "find" not in script and "cls" not in script and "put" not in script and "rm " not in script


def test_root_only_preflight_models_chroot_landing_and_keeps_candidates_confined(monkeypatch):
    protocol = manifest.ReadOnlySftpProtocolRunner(host="remote.example")
    calls = []
    def chroot_process(candidate, **_kwargs):
        calls.append(candidate)
        if candidate is None:
            return manifest.SftpProcessResult(_root_preflight_output("source_root_preflight_pwd", "/"))
        if candidate == "files/Incoming":
            return manifest.SftpProcessResult(_root_preflight_output("source_root_preflight_candidate", "/files/Incoming"))
        return manifest.SftpProcessResult(b"source_root_preflight_open\n", returncode=1)
    monkeypatch.setattr(protocol, "root_preflight_process", chroot_process)
    result = manifest.RootOnlySftpPreflight(protocol).run(
        relative_root="files/Incoming", trusted_absolute_root="/outside/Incoming",
    )
    assert result.succeeded
    assert result.selected_form == "account_relative"
    assert calls == [None, "files/Incoming", "/files/Incoming", "/outside/Incoming", "outside/Incoming"]


@pytest.mark.parametrize("output", [
    b"source_root_preflight_pwd\nnot-a-url\n",
    b"source_root_preflight_pwd\nsftp://one.example/root\nsftp://two.example/root\n",
])
def test_root_only_preflight_rejects_malformed_or_multiple_pwd_output(monkeypatch, output):
    protocol = manifest.ReadOnlySftpProtocolRunner(host="remote.example")
    monkeypatch.setattr(protocol, "root_preflight_process", lambda *_args, **_kwargs: manifest.SftpProcessResult(output))
    result = manifest.RootOnlySftpPreflight(protocol).run(relative_root="fixture/Incoming", trusted_absolute_root=None)
    assert result.reason == "pwd_malformed"
    assert result.stage == "pwd"
    assert result.candidates == ()
