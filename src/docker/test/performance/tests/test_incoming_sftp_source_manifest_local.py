"""Opt-in local execution reproducer for the source-manifest LFTP boundary.

This test is intentionally separate from the protocol/unit tests.  It starts a
short-lived user-owned OpenSSH process on loopback, creates a synthetic nested
source tree, and runs the committed LFTP runner twice through the normal
source-manifest config path.  No SeedSync process, Queue endpoint, or external
host is involved.

Run from WSL/Linux with ``INCOMING_RECOVERY_LOCAL_LFTP=1``.  The opt-in keeps
the normal test lane independent from host LFTP/sshd availability.
"""
from __future__ import annotations

from contextlib import contextmanager
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import socket
import subprocess
import tempfile
import time

import pytest


PERFORMANCE_ROOT = Path(__file__).parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_module(
    "incoming_live_queue_runner_local_test",
    PERFORMANCE_ROOT / "incoming_live_queue_runner.py",
)
manifest = runner._manifest


pytestmark = pytest.mark.skipif(
    os.environ.get("INCOMING_RECOVERY_LOCAL_LFTP") != "1"
    or os.name != "posix"
    or not Path("/proc/version").is_file(),
    reason="opt-in WSL/Linux-only local LFTP integration",
)


def _command_available(name: str) -> bool:
    return shutil.which(name) is not None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[bytes], log_path: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"local sshd exited before readiness; log={log_path.name}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"local sshd did not become ready; log={log_path.name}")


@contextmanager
def _local_sftp_fixture(*, chroot: bool = False):
    """Yield a synthetic tree and strict-known-host connection details."""

    username = getpass.getuser()
    if not username or any(character in username for character in "\r\n"):
        raise RuntimeError("current user is not safe for a local sshd fixture")

    with tempfile.TemporaryDirectory(prefix="seedsync-source-manifest-", dir=Path.home()) as raw_dir:
        workspace = Path(raw_dir)
        remote_base = "fixture" if chroot else workspace.name + "/fixture"
        selected_root = "nested selected/Incoming"
        base_path = workspace / "fixture"
        source_root = base_path / selected_root
        source_root.mkdir(parents=True)
        expected_bytes = 0
        for index in range(400):
            target = source_root / f"branch-{index % 20:02d}" / f"leaf-{index % 10:02d}" / f"file-{index:04d}.bin"
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = bytes((index % 251,)) * (index + 1)
            target.write_bytes(payload)
            expected_bytes += len(payload)

        ssh_home = workspace / "ssh-home"
        ssh_dir = ssh_home / ".ssh"
        ssh_dir.mkdir(parents=True)
        ssh_home.chmod(0o700)
        ssh_dir.chmod(0o700)
        client_key = ssh_dir / "id_ed25519"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(client_key)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ssh_binary = shutil.which("ssh")
        assert ssh_binary is not None
        lftp_binary = shutil.which("lftp")
        assert lftp_binary is not None
        wrapper_dir = workspace / "bin"
        wrapper_dir.mkdir()
        lftp_diagnostic = workspace / "lftp.stderr"
        lftp_wrapper = wrapper_dir / "lftp"
        lftp_wrapper.write_text(
            "#!/bin/sh\nexec " + shlex.quote(lftp_binary) + " \"$@\" 2>" + shlex.quote(str(lftp_diagnostic)) + "\n",
            encoding="utf-8",
        )
        lftp_wrapper.chmod(0o700)
        ssh_diagnostic = workspace / "ssh.stderr"
        ssh_arguments = workspace / "ssh.arguments"
        ssh_wrapper = wrapper_dir / "ssh"
        ssh_wrapper.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >" + shlex.quote(str(ssh_arguments)) + "\nexec " + shlex.quote(ssh_binary) + " -i " + shlex.quote(str(client_key))
            + " \"$@\" 2>" + shlex.quote(str(ssh_diagnostic)) + "\n",
            encoding="utf-8",
        )
        ssh_wrapper.chmod(0o700)
        authorized_keys = workspace / "authorized_keys"
        authorized_keys.write_text((client_key.with_name("id_ed25519.pub")).read_text(encoding="utf-8"), encoding="utf-8")
        authorized_keys.chmod(0o600)
        host_key = workspace / "ssh_host_ed25519_key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        port = _free_port()
        sshd_binary = shutil.which("sshd")
        assert sshd_binary is not None
        config = workspace / "sshd_config"
        config.write_text(
            "\n".join(
                (
                    f"Port {port}",
                    "ListenAddress 127.0.0.1",
                    f"HostKey {host_key}",
                    f"AuthorizedKeysFile {authorized_keys}",
                    f"PidFile {workspace / 'sshd.pid'}",
                    "UsePAM no",
                    "PasswordAuthentication no",
                    "KbdInteractiveAuthentication no",
                    "PubkeyAuthentication yes",
                    "PermitRootLogin no",
                    "StrictModes no",
                    "LogLevel ERROR",
                    f"AllowUsers {username}",
                    "Subsystem sftp /usr/lib/openssh/sftp-server",
                ),
            )
            + "\n",
            encoding="utf-8",
        )
        checked = subprocess.run(
            [sshd_binary, "-t", "-f", str(config)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if checked.returncode != 0:
            raise RuntimeError("local sshd config rejected")

        log_path = workspace / "sshd.log"
        log_handle = log_path.open("wb")
        chroot_ownership_changed = False
        if chroot:
            # ChrootDirectory requires a root-owned boundary.  Leave the
            # synthetic source tree user-owned and restore the temporary tree
            # before TemporaryDirectory removes it.
            with config.open("a", encoding="utf-8") as handle:
                handle.write("ChrootDirectory " + str(workspace) + "\nForceCommand internal-sftp\n")
            checked = subprocess.run(
                ["sudo", "-n", "chown", "root:root", str(workspace)],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if checked.returncode != 0:
                pytest.skip("passwordless sudo chown is unavailable for local chroot integration")
            chroot_ownership_changed = True
            subprocess.run(["sudo", "-n", "chmod", "755", str(workspace)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process = None
        try:
            process = subprocess.Popen(
                (["sudo", "-n"] if chroot else []) + [sshd_binary, "-D", "-e", "-f", str(config)],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            _wait_for_port(port, process, log_path)
            known_hosts = workspace / "known_hosts"
            scanned = subprocess.run(
                ["ssh-keyscan", "-p", str(port), "127.0.0.1"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            known_hosts.write_bytes(scanned.stdout)
            known_hosts.chmod(0o600)
            ssh_probe = subprocess.run(
                [
                    "ssh", "-F", "/dev/null", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", f"UserKnownHostsFile={known_hosts}",
                    "-i", str(client_key), "-p", str(port), f"{username}@127.0.0.1", "true",
                ],
                check=False,
                env={**os.environ, "HOME": str(ssh_home)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if ssh_probe.returncode != 0:
                raise RuntimeError("local strict-known-host SSH probe failed")
            yield {
                "root": str(source_root),
                "remote_base": remote_base,
                "absolute_base": "/fixture" if chroot else str(base_path),
                "selected_root": selected_root,
                "host": "127.0.0.1",
                "port": port,
                "username": username,
                "known_hosts_file": str(known_hosts),
                "home": str(ssh_home),
                "path_prefix": str(wrapper_dir),
                "lftp_diagnostic": str(lftp_diagnostic),
                "ssh_diagnostic": str(ssh_diagnostic),
                "ssh_arguments": str(ssh_arguments),
                "expected_bytes": expected_bytes,
            }
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            log_handle.close()
            if chroot_ownership_changed:
                subprocess.run(
                    ["sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(workspace)],
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )


@pytest.mark.parametrize("base_mode", ("relative", "absolute"))
def test_real_lftp_source_manifest_is_local_strict_and_stable(tmp_path, monkeypatch, base_mode):
    required = ("lftp", "ssh", "ssh-keygen", "ssh-keyscan", "sshd")
    missing = [name for name in required if not _command_available(name)]
    if missing:
        pytest.skip("missing local integration tools: " + ", ".join(missing))

    with _local_sftp_fixture() as fixture:
        monkeypatch.setenv("HOME", fixture["home"])
        monkeypatch.setenv("PATH", fixture["path_prefix"] + os.pathsep + os.environ["PATH"])
        connection_path = tmp_path / "local-connection.json"
        output_path = tmp_path / "source-manifest.json"
        config_path = tmp_path / "source-manifest-config.json"
        connection_path.write_text(
            json.dumps(
                {
                    "host": fixture["host"],
                    "port": fixture["port"],
                    "username": fixture["username"],
                    "remote_path": fixture["remote_base"] if base_mode == "relative" else fixture["absolute_base"],
                },
            ),
            encoding="utf-8",
        )
        connection_path.chmod(0o600)
        config_path.write_text(
            json.dumps(
                {
                    "source_manifest": {
                        "root": fixture["selected_root"],
                        "connection_config_path": str(connection_path),
                        "known_hosts_file": fixture["known_hosts_file"],
                        "artifact_path": str(output_path),
                        "delay_seconds": 0.0,
                    },
                },
            ),
            encoding="utf-8",
        )

        try:
            assert runner.main(str(config_path), source_manifest=True) == 0
        except manifest.SourceManifestError as exc:
            # The harness intentionally exposes only a stable failure stage.
            # Keep the failure useful without exposing local fixture paths,
            # host keys, usernames, or process output in test logs.
            diagnostic = manifest.redacted_manifest_error(exc)
            assert set(diagnostic).issubset({"schema", "reason", "stage"})
            pytest.fail("local source-manifest stage: " + json.dumps(diagnostic, sort_keys=True))

        artifact = json.loads(output_path.read_text(encoding="utf-8"))
        assert artifact["file_count"] == 400
        assert artifact["total_bytes"] == fixture["expected_bytes"]
        assert fixture["root"] not in output_path.read_text(encoding="utf-8")
        assert "file-0000.bin" not in output_path.read_text(encoding="utf-8")
        assert "source_manifest_complete" not in output_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("case", "expected_stage"), (
    ("missing_root", "root"),
    ("unreadable_root", "root"),
    ("unreadable_child", "enumeration"),
))
def test_real_lftp_source_manifest_failure_phases_are_private(tmp_path, monkeypatch, case, expected_stage):
    required = ("lftp", "ssh", "ssh-keygen", "ssh-keyscan", "sshd")
    missing = [name for name in required if not _command_available(name)]
    if missing:
        pytest.skip("missing local integration tools: " + ", ".join(missing))

    with _local_sftp_fixture() as fixture:
        monkeypatch.setenv("HOME", fixture["home"])
        monkeypatch.setenv("PATH", fixture["path_prefix"] + os.pathsep + os.environ["PATH"])
        source_root = Path(fixture["root"])
        inaccessible = source_root if case == "unreadable_root" else source_root / "blocked child"
        if case == "unreadable_child":
            inaccessible.mkdir()
        if case != "missing_root":
            inaccessible.chmod(0)
        selected_root = "missing root" if case == "missing_root" else fixture["selected_root"]
        connection_path = tmp_path / f"{case}-connection.json"
        config_path = tmp_path / f"{case}-config.json"
        artifact_path = tmp_path / f"{case}-artifact.json"
        connection_path.write_text(json.dumps({
            "host": fixture["host"], "port": fixture["port"],
            "username": fixture["username"], "remote_path": fixture["remote_base"],
        }), encoding="utf-8")
        connection_path.chmod(0o600)
        config_path.write_text(json.dumps({"source_manifest": {
            "root": selected_root, "connection_config_path": str(connection_path),
            "known_hosts_file": fixture["known_hosts_file"], "artifact_path": str(artifact_path),
            "delay_seconds": 0.0,
        }}), encoding="utf-8")
        try:
            with pytest.raises(manifest.SourceManifestError, match="process_failed") as raised:
                runner.main(str(config_path), source_manifest=True)
        finally:
            if case != "missing_root":
                inaccessible.chmod(0o700)
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert payload == manifest.redacted_manifest_error(raised.value)
        assert payload["stage"] == expected_stage
        assert fixture["root"] not in str(payload)
        assert "blocked child" not in str(payload)


@pytest.mark.parametrize("base_mode", ("relative", "absolute"))
@pytest.mark.parametrize("chroot", (False, True))
def test_real_lftp_root_preflight_is_strict_and_resolves_landing_forms(tmp_path, monkeypatch, base_mode, chroot):
    required = ("lftp", "ssh", "ssh-keygen", "ssh-keyscan", "sshd")
    missing = [name for name in required if not _command_available(name)]
    if missing:
        pytest.skip("missing local integration tools: " + ", ".join(missing))

    with _local_sftp_fixture(chroot=chroot) as fixture:
        monkeypatch.setenv("HOME", fixture["home"])
        monkeypatch.setenv("PATH", fixture["path_prefix"] + os.pathsep + os.environ["PATH"])
        connection_path = tmp_path / f"root-{base_mode}-{chroot}.json"
        output_path = tmp_path / f"root-{base_mode}-{chroot}.artifact.json"
        config_path = tmp_path / f"root-{base_mode}-{chroot}.config.json"
        dummy_password = "local-fixture-session-password"
        connection_path.write_text(json.dumps({
            "host": fixture["host"], "port": fixture["port"], "username": fixture["username"],
            "password": dummy_password,
            "remote_path": fixture["remote_base"] if base_mode == "relative" else fixture["absolute_base"],
        }), encoding="utf-8")
        connection_path.chmod(0o600)
        askpass_path = tmp_path / "local-askpass"
        askpass_path.write_text("#!/bin/sh\nexec python3 " + shlex.quote(str(PERFORMANCE_ROOT / "incoming_sftp_askpass.py")) + "\n", encoding="utf-8")
        askpass_path.chmod(0o700)
        config_path.write_text(json.dumps({"source_manifest": {
            "root": fixture["selected_root"], "connection_config_path": str(connection_path),
            "known_hosts_file": fixture["known_hosts_file"], "root_preflight_artifact_path": str(output_path),
            "root_preflight_transport": "sftp_realpath", "root_preflight_askpass_program": str(askpass_path),
        }}), encoding="utf-8")
        captured = []
        original_process = runner._manifest._run_bounded_process
        def record_process(*args, **kwargs):
            result = original_process(*args, **kwargs)
            captured.append(result.stdout)
            return result
        monkeypatch.setattr(runner._manifest, "_run_bounded_process", record_process)
        protocol = runner.ReadOnlySftpProtocolRunner(
            host=fixture["host"], port=fixture["port"], username=fixture["username"],
            password=dummy_password, known_hosts_file=fixture["known_hosts_file"],
        )
        assert protocol.root_preflight_process(None, timeout_seconds=10, max_output_bytes=65536).returncode == 0
        # Exact LFTP output remains only in process memory.  LFTP 4.9.2
        # includes this supplied credential even without `pwd -p`, proving
        # why the production preflight must use SFTP realpath instead.
        assert any(dummy_password.encode("utf-8") in output for output in captured)
        assert runner.main(str(config_path), source_root_preflight=True) == 0
        ssh_arguments = Path(fixture["ssh_arguments"]).read_text(encoding="utf-8")
        assert "BatchMode=no" in ssh_arguments
        artifact = json.loads(output_path.read_text(encoding="utf-8"))
        assert artifact["schema"] == "incoming-recovery-source-root-preflight.v1"
        assert artifact["reason"] == "ok"
        assert artifact["ambiguity"] in {"unique", "equivalent"}
        assert fixture["root"] not in str(artifact)
        assert dummy_password not in str(artifact)
        assert "file-0000.bin" not in str(artifact)
        assert "find" not in output_path.read_text(encoding="utf-8")
