# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import logging
import sys
from unittest.mock import patch, call, ANY, MagicMock
import tempfile
import os
import base64
import json
import shutil
import shlex

from controller.scan import RemoteScanLease, RemoteScanner, ScannerError
from ssh import Sshcp, SshcpError
from common import Localization, escape_remote_path_for_shell
from common.performance_diagnostics import (
    DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION,
    DURATION_REMOTE_SCAN_STREAM_PARSING,
)


class TestRemoteScanner(unittest.TestCase):
    temp_dir = None
    temp_scan_script = None

    def setUp(self):
        ssh_patcher = patch('controller.scan.remote_scanner.Sshcp')
        self.addCleanup(ssh_patcher.stop)
        self.mock_ssh_cls = ssh_patcher.start()
        self.mock_ssh = self.mock_ssh_cls.return_value
        # The production Sshcp exposes shell_stream, but keep legacy tests on
        # the one-shot shell path unless they explicitly exercise streaming.
        self.mock_ssh.shell_stream = None
        self.mock_ssh.detect_shell.return_value = "/bin/sh"

        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        # Ssh to return mangled binary by default
        self.mock_ssh.shell.return_value = b'error'

    @classmethod
    def setUpClass(cls):
        TestRemoteScanner.temp_dir = tempfile.mkdtemp(prefix="test_remote_scanner")
        TestRemoteScanner.temp_scan_script = os.path.join(TestRemoteScanner.temp_dir, "script")
        with open(TestRemoteScanner.temp_scan_script, "w") as f:
            f.write("")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TestRemoteScanner.temp_dir)

    @staticmethod
    def _scanfs_probe_command(remote_path):
        escaped_remote_path = escape_remote_path_for_shell(remote_path, allow_tilde_expansion=True)
        return "if [ -d {} ]; then echo IS_DIRECTORY; else md5sum {} | awk '{{print $1}}' || echo; fi".format(
            escaped_remote_path,
            escaped_remote_path
        )

    @staticmethod
    def _scan_command(remote_python_path, remote_script, remote_path):
        if remote_python_path is None:
            normalized_remote_python_path = "python3"
        elif isinstance(remote_python_path, str):
            normalized_remote_python_path = remote_python_path.strip() or "python3"
        else:
            normalized_remote_python_path = str(remote_python_path).strip() or "python3"
        return "{} {} {}".format(
            shlex.quote(normalized_remote_python_path),
            escape_remote_path_for_shell(remote_script, allow_tilde_expansion=True),
            escape_remote_path_for_shell(remote_path, allow_tilde_expansion=True)
        )

    @staticmethod
    def _direct_scan_command(remote_script, remote_path):
        return "{} {}".format(
            escape_remote_path_for_shell(remote_script, allow_tilde_expansion=True),
            escape_remote_path_for_shell(remote_path, allow_tilde_expansion=True)
        )

    def test_remote_scan_lease_uses_runtime_temp_storage(self):
        config_dir = tempfile.mkdtemp(prefix="test-remote-scan-config-", dir=TestRemoteScanner.temp_dir)
        self.addCleanup(shutil.rmtree, config_dir)
        runtime_dir = tempfile.mkdtemp(prefix="test-remote-scan-runtime-", dir=TestRemoteScanner.temp_dir)
        self.addCleanup(shutil.rmtree, runtime_dir)

        with patch("controller.scan.remote_scanner.tempfile.gettempdir", return_value=runtime_dir), \
                patch("controller.scan.remote_scanner.os.path.isdir") as isdir:
            lease = RemoteScanLease.create()
            rebuilt_lease = RemoteScanLease.create()

        isdir.assert_not_called()
        self.assertEqual(
            os.path.normcase(os.path.abspath(runtime_dir)),
            os.path.normcase(os.path.dirname(os.path.abspath(lease.path))),
        )
        self.assertEqual(lease.path, rebuilt_lease.path)
        self.assertFalse(os.path.exists(os.path.join(config_dir, ".seedsync-remote-scan.lock")))

    def test_remote_scan_lease_releases_after_scan_error(self):
        runtime_dir = tempfile.mkdtemp(prefix="test-remote-scan-runtime-", dir=TestRemoteScanner.temp_dir)
        self.addCleanup(shutil.rmtree, runtime_dir)
        with patch("controller.scan.remote_scanner.tempfile.gettempdir", return_value=runtime_dir):
            lease = RemoteScanLease.create()
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/files",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/tmp/scanfs",
            remote_scan_lease=lease,
        )

        scanner._RemoteScanner__scan = MagicMock(side_effect=RuntimeError("scan failed"))
        with self.assertRaises(RuntimeError):
            scanner.scan()

        scanner._RemoteScanner__scan = MagicMock(return_value=[])
        self.assertEqual([], scanner.scan())

    def test_generation_master_startup_errors_keep_scan_error_contract(self):
        scanner = RemoteScanner(
            "host", "user", "password", 22, "/remote", TestRemoteScanner.temp_scan_script, "/tmp/scanfs"
        )
        scanner._RemoteScanner__ssh.start_generation_control_master.side_effect = SshcpError("Timed out")
        with self.assertRaises(ScannerError) as transient:
            scanner.scan_with_remote_scan_lease_held()
        self.assertTrue(transient.exception.recoverable)

        scanner._RemoteScanner__ssh.start_generation_control_master.side_effect = SshcpError("Incorrect password")
        with self.assertRaises(ScannerError) as password:
            scanner.scan_with_remote_scan_lease_held()
        self.assertFalse(password.exception.recoverable)

        scanner._RemoteScanner__first_run = False
        scanner._RemoteScanner__ssh.start_generation_control_master.side_effect = SshcpError("Connection refused by server")
        with self.assertRaises(ScannerError) as settled:
            scanner.scan_with_remote_scan_lease_held()
        self.assertTrue(settled.exception.recoverable)

    @unittest.skipUnless(
        os.name == "posix" and getattr(os, "O_NOFOLLOW", None) is not None and hasattr(os, "symlink"),
        "POSIX symlink and O_NOFOLLOW support is required",
    )
    def test_remote_scan_lease_rejects_runtime_symlink_without_touching_target(self):
        runtime_dir = tempfile.mkdtemp(prefix="test-remote-scan-runtime-", dir=TestRemoteScanner.temp_dir)
        target_dir = tempfile.mkdtemp(prefix="test-remote-scan-target-", dir=TestRemoteScanner.temp_dir)
        self.addCleanup(shutil.rmtree, runtime_dir)
        self.addCleanup(shutil.rmtree, target_dir)
        target_path = os.path.join(target_dir, "target.lock")
        target_contents = b"target remains unchanged\n"
        with open(target_path, "wb") as target_file:
            target_file.write(target_contents)
        os.chmod(target_path, 0o640)
        target_mode = os.stat(target_path).st_mode
        lease_path = os.path.join(runtime_dir, ".seedsync-remote-scan.lock")
        try:
            os.symlink(target_path, lease_path)
        except (NotImplementedError, OSError) as error:
            self.skipTest("unable to create symlink: {}".format(error))

        with patch("controller.scan.remote_scanner.tempfile.gettempdir", return_value=runtime_dir):
            with self.assertRaises(OSError):
                RemoteScanLease.create()

            os.unlink(lease_path)
            lease = RemoteScanLease.create()
            os.unlink(lease.path)
            os.symlink(target_path, lease.path)
            with self.assertRaises(OSError):
                with lease.hold():
                    pass

        with open(target_path, "rb") as target_file:
            self.assertEqual(target_contents, target_file.read())
        self.assertEqual(target_mode, os.stat(target_path).st_mode)

    @staticmethod
    def _framed_stream(root_names):
        records = [
            {"type": "manifest_begin", "count": len(root_names)},
            {"type": "manifest_names", "names": list(root_names)},
            {"type": "manifest_end"},
        ]
        for root_id, name in enumerate(root_names):
            records.extend((
                {"type": "root_begin", "id": root_id, "name": name},
                {"type": "root_node", "root": root_id, "id": 0, "parent": None,
                 "file": {"name": name, "size": root_id, "is_dir": False}},
                {"type": "root_end", "id": root_id, "nodes": 1},
            ))
        records.append({"type": "complete"})
        return b"".join(
            "SEEDSYNC_SCAN_V2\t{}\n".format(json.dumps(record, separators=(",", ":"))).encode()
            for record in records
        )

    def test_correctly_initializes_ssh(self):
        self.ssh_args = {}

        def mock_ssh_ctor(**kwargs):
            self.ssh_args = kwargs

        self.mock_ssh_cls.side_effect = mock_ssh_ctor

        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.assertIsNotNone(scanner)
        self.assertEqual("my remote address", self.ssh_args["host"])
        self.assertEqual(1234, self.ssh_args["port"])
        self.assertEqual("my remote user", self.ssh_args["user"])
        self.assertEqual("my password", self.ssh_args["password"])

    def test_recycled_state_preserves_first_run_and_fallback_script_path(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/files",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/tmp/scanfs",
        )

        scanner.apply_recycled_state((False, "~/scanfs"))

        self.assertEqual((False, "~/scanfs"), scanner.export_recycled_state())

    def test_installs_scan_script_on_first_scan(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh returns error for md5sum check, empty JSON for later commands
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # first try
                return "".encode()
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()
        self.mock_ssh.detect_shell.assert_called_once()
        self.mock_ssh.copy.assert_called_once_with(
            local_path=TestRemoteScanner.temp_scan_script,
            remote_path="/remote/path/to/scan/script"
        )
        self.mock_ssh.copy.reset_mock()

        # should not be called the second time
        scanner.scan()
        self.assertEqual(1, self.mock_ssh.detect_shell.call_count)
        self.mock_ssh.copy.assert_not_called()

    def test_copy_appends_scanfs_name_to_remote_path(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan"
        )

        self.ssh_run_command_count = 0

        # Ssh returns error for md5sum check, empty JSON for later commands
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # first try
                return "".encode()
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()
        # check for appended path ('script')
        self.mock_ssh.copy.assert_called_once_with(
            local_path=TestRemoteScanner.temp_scan_script,
            remote_path="/remote/path/to/scan/script"
        )

    def test_calls_correct_ssh_md5sum_command(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh returns error for md5sum check, empty JSON for later commands
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # first try
                return "".encode()
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()
        self.assertEqual(2, self.mock_ssh.shell.call_count)
        self.mock_ssh.shell.assert_has_calls([
            call(self._scanfs_probe_command("/remote/path/to/scan/script")),
            call(ANY)
        ])

    def test_raises_nonrecoverable_error_when_shell_detection_fails(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.mock_ssh.detect_shell.side_effect = SshcpError(
            "bash: /bin/bash: No such file or directory"
        )

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format(
                "bash: /bin/bash: No such file or directory"
            ),
            str(ctx.exception)
        )
        self.assertFalse(ctx.exception.recoverable)
        self.mock_ssh.shell.assert_not_called()
        self.mock_ssh.copy.assert_not_called()

    def test_raises_recoverable_error_when_first_run_shell_detection_is_transient(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/tmp/scanfs",
        )
        self.mock_ssh.detect_shell.side_effect = SshcpError("Connection refused by server")

        with self.assertRaises(ScannerError) as context:
            scanner.scan()

        self.assertTrue(context.exception.recoverable)
        self.assertTrue(scanner.export_recycled_state()[0])
        self.mock_ssh.copy.assert_not_called()

    def test_first_run_setup_retries_after_transient_shell_failure(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/tmp/scanfs",
        )
        self.mock_ssh.detect_shell.side_effect = [
            SshcpError("Connection refused by server"),
            "/bin/sh",
        ]
        self.mock_ssh.shell.side_effect = [b"", b"[]", b"[]"]

        with self.assertRaises(ScannerError) as first_error:
            scanner.scan()
        self.assertTrue(first_error.exception.recoverable)

        self.assertEqual([], scanner.scan())
        self.assertFalse(scanner.export_recycled_state()[0])
        self.mock_ssh.copy.assert_called_once()

    def test_skips_install_on_md5sum_match(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh returns empty on md5sum, empty JSON for later commands
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # first try
                return "d41d8cd98f00b204e9800998ecf8427e".encode()
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()
        self.mock_ssh.copy.assert_not_called()
        self.mock_ssh.copy.reset_mock()

        # should not be called the second time either
        scanner.scan()
        self.mock_ssh.copy.assert_not_called()

    def test_installs_scan_script_on_any_md5sum_output(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh returns error for md5sum check, empty JSON for later commands
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # first try
                return "some output from md5sum".encode()
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()
        self.mock_ssh.copy.assert_called_once_with(
            local_path=TestRemoteScanner.temp_scan_script,
            remote_path="/remote/path/to/scan/script"
        )
        self.mock_ssh.copy.reset_mock()

    def test_raises_nonrecoverable_error_on_md5sum_error(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh returns error for md5sum check, empty JSON for later commands
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # md5sum check
                raise SshcpError("an ssh error")
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_INSTALL.format("an ssh error"), str(ctx.exception))
        self.assertFalse(ctx.exception.recoverable)

    def test_raises_recoverable_error_on_md5sum_timeout(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        # Ssh returns timeout error for md5sum check
        self.mock_ssh.shell.side_effect = SshcpError("Timed out")

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_INSTALL.format("Timed out"), str(ctx.exception))
        self.assertTrue(ctx.exception.recoverable)

    def test_raises_recoverable_error_on_md5sum_connection_refused(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        # Ssh returns connection refused error for md5sum check
        self.mock_ssh.shell.side_effect = SshcpError("Connection refused by server")

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format("Connection refused by server"),
            str(ctx.exception)
        )
        self.assertTrue(ctx.exception.recoverable)

    def test_calls_correct_ssh_scan_command(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh returns error for md5sum check, empty JSON for later commands
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # md5sum check
                return b''
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()
        self.assertEqual(2, self.mock_ssh.shell.call_count)
        self.mock_ssh.shell.assert_called_with(
            self._scan_command("python3", "/remote/path/to/scan/script", "/remote/path/to/scan")
        )

    def test_progressive_stream_decodes_manifest_and_batched_roots(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        stream = (
            'SEEDSYNC_SCAN_V2\t{"type":"manifest","names":["a","b"]}\n'
            'SEEDSYNC_SCAN_V2\t{"type":"roots","files":[{"name":"a","size":1,"is_dir":false}]}\n'
            'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'
        ).encode()
        self.mock_ssh.shell.side_effect = [b"", stream]
        events = []
        scanner.set_progress_callback(lambda files, pair_id, pair_name, roots, complete:
                                       events.append((files, roots, complete)))

        files = scanner.scan()

        self.assertEqual(["a"], [file.name for file in files])
        self.assertEqual([({"a", "b"}, False), (None, False), (None, True)],
                         [(roots, complete) for _, roots, complete in events])
        self.assertIn("--stream", self.mock_ssh.shell.call_args.args[0])

    def test_framed_progress_coalesces_roots_in_order_with_bounded_batches(self):
        names = ["root-{}".format(index) for index in range(17)]
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        events = []
        scanner.set_progress_callback(
            lambda files, pair_id, pair_name, roots, complete:
            events.append(([file.name for file in files], roots, complete))
        )
        stream = self._framed_stream(names)
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (on_chunk(stream), b"")[-1])

        files = scanner.scan()

        self.assertEqual(names, [file.name for file in files])
        root_events = [files for files, roots, complete in events if files and roots is None and not complete]
        self.assertEqual([names[:8], names[8:16], names[16:]], root_events)
        self.assertEqual(set(names), events[0][1])
        self.assertTrue(events[-1][2])

    def test_framed_progress_flushes_small_batch_before_completion(self):
        names = ["slow-0", "slow-1", "slow-2"]
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        events = []
        scanner.set_progress_callback(
            lambda files, pair_id, pair_name, roots, complete:
            events.append(([file.name for file in files], roots, complete))
        )
        stream = self._framed_stream(names)
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (on_chunk(stream), b"")[-1])

        scanner.scan()

        self.assertEqual([names], [files for files, roots, complete in events if files and roots is None])
        self.assertEqual([False, False, True], [complete for _, _, complete in events])

    def test_framed_progress_flushes_slow_roots_on_next_root_completion(self):
        names = ["slow-root-0", "slow-root-1"]
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        events = []
        scanner.set_progress_callback(
            lambda files, pair_id, pair_name, roots, complete:
            events.append(([file.name for file in files], roots, complete))
        )
        stream = self._framed_stream(names)
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (on_chunk(stream), b"")[-1])

        with patch("controller.scan.remote_scanner.time.monotonic", side_effect=(0.0, 0.2)):
            scanner.scan()

        root_events = [files for files, roots, complete in events if files and roots is None and not complete]
        self.assertEqual([names], root_events)
        self.assertEqual([False, False, True], [complete for _, _, complete in events])

    def test_framed_progress_does_not_flush_pending_roots_before_validated_completion(self):
        names = ["pending-root"]
        stream = self._framed_stream(names)
        malformed = b'SEEDSYNC_SCAN_V2\t{not-json}\n'
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        events = []
        scanner.set_progress_callback(
            lambda files, pair_id, pair_name, roots, complete:
            events.append(([file.name for file in files], roots, complete))
        )
        self.mock_ssh.shell_stream = None
        self.mock_ssh.shell.return_value = stream + malformed

        with self.assertRaises(ScannerError) as context:
            scanner.scan()

        self.assertTrue(context.exception.recoverable)
        self.assertEqual([([], {"pending-root"}, False)], events)
        self.assertFalse(any(complete for _, _, complete in events))

    def test_framed_progress_keeps_flushed_batch_after_late_malformed_data_without_completion(self):
        names = ["late-{}".format(index) for index in range(9)]
        stream = self._framed_stream(names) + b'SEEDSYNC_SCAN_V2\t{not-json}\n'
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        events = []
        scanner.set_progress_callback(
            lambda files, pair_id, pair_name, roots, complete:
            events.append(([file.name for file in files], roots, complete))
        )
        self.mock_ssh.shell_stream = None
        self.mock_ssh.shell.return_value = stream

        with self.assertRaises(ScannerError) as context:
            scanner.scan()

        self.assertTrue(context.exception.recoverable)
        root_events = [files for files, roots, complete in events if files and roots is None and not complete]
        self.assertEqual([names[:8]], root_events)
        self.assertFalse(any(names[8] in files for files, _, _ in events))
        self.assertFalse(any(complete for _, _, complete in events))

    def test_framed_progress_checks_exact_age_boundary_on_next_root_completion(self):
        names = ["boundary-0", "boundary-1"]
        records = self._framed_stream(names).splitlines(keepends=True)
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        events = []
        scanner.set_progress_callback(
            lambda files, pair_id, pair_name, roots, complete:
            events.append(([file.name for file in files], roots, complete))
        )

        def shell_stream(command, on_chunk):
            on_chunk(b"".join(records[:6]))  # manifest + first root; no timer flush occurs here
            self.assertEqual([], [files for files, roots, complete in events
                                  if files and roots is None and not complete])
            on_chunk(b"".join(records[6:9]))  # second root reaches the exact 100 ms boundary
            self.assertEqual([names], [files for files, roots, complete in events
                                       if files and roots is None and not complete])
            on_chunk(records[9])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        with patch("controller.scan.remote_scanner.time.monotonic", side_effect=(0.0, 0.1)):
            scanner.scan()

        self.assertTrue(events[-1][2])

    def test_framed_progress_preserves_manifest_event_order_across_fragmented_chunks(self):
        names = ["fragment-0", "fragment-1"]
        records = [
            {"type": "manifest_begin", "count": len(names)},
            {"type": "manifest_names", "names": [names[0]]},
            {"type": "manifest_names", "names": [names[1]]},
            {"type": "manifest_end"},
            {"type": "root_begin", "id": 0, "name": names[0]},
            {"type": "root_node", "root": 0, "id": 0, "parent": None,
             "file": {"name": names[0], "size": 0, "is_dir": False}},
            {"type": "root_end", "id": 0, "nodes": 1},
            {"type": "root_begin", "id": 1, "name": names[1]},
            {"type": "root_node", "root": 1, "id": 0, "parent": None,
             "file": {"name": names[1], "size": 1, "is_dir": False}},
            {"type": "root_end", "id": 1, "nodes": 1},
            {"type": "complete"},
        ]
        stream = b"".join(
            "SEEDSYNC_SCAN_V2\t{}\n".format(json.dumps(record, separators=(",", ":"))).encode()
            for record in records
        )
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        events = []
        scanner.set_progress_callback(
            lambda files, pair_id, pair_name, roots, complete:
            events.append(([file.name for file in files], roots, complete))
        )

        def shell_stream(command, on_chunk):
            for offset in range(0, len(stream), 5):
                on_chunk(stream[offset:offset + 5])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.scan()

        self.assertEqual(set(names), events[0][1])
        self.assertEqual([names], [files for files, roots, complete in events if files and roots is None])
        self.assertEqual([False, False, True], [complete for _, _, complete in events])

    def test_one_shot_v2_decode_is_attributed_to_stream_parsing(self):
        diagnostics = MagicMock()
        diagnostics.begin_duration.side_effect = lambda metric: metric
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
            performance_diagnostics=diagnostics,
        )
        stream = (
            'SEEDSYNC_SCAN_V2\t{"type":"manifest","names":["a"]}\n'
            'SEEDSYNC_SCAN_V2\t{"type":"roots","files":[{"name":"a","size":1,"is_dir":false}]}\n'
            'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'
        ).encode()
        self.mock_ssh.shell.side_effect = [b"", stream]
        scanner.set_progress_callback(lambda *_args: None)

        self.assertEqual(["a"], [file.name for file in scanner.scan()])
        started_metrics = [call.args[0] for call in diagnostics.begin_duration.call_args_list]
        finished_metrics = [call.args[0] for call in diagnostics.finish_duration.call_args_list]
        self.assertIn(DURATION_REMOTE_SCAN_STREAM_PARSING, started_metrics)
        self.assertIn(DURATION_REMOTE_SCAN_STREAM_PARSING, finished_metrics)

    def test_progressive_stream_publishes_before_remote_command_eof(self):
        diagnostics = MagicMock()
        diagnostics.begin_duration.side_effect = lambda metric: metric
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
            performance_diagnostics=diagnostics,
        )
        events = []
        scanner.set_progress_callback(lambda files, pair_id, pair_name, roots, complete:
                                       events.append((files, roots, complete)))
        command_finished = [False]

        def shell_stream(command, on_chunk):
            on_chunk(b'SEEDSYNC_SCAN_V2\t{"type":"manifest","names":["a"]}\n')
            on_chunk(b'SEEDSYNC_SCAN_V2\t{"type":"roots","files":[{"name":"a","size":1,"is_dir":false}]}\n')
            self.assertFalse(command_finished[0])
            on_chunk(b'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n')
            command_finished[0] = True
            return b""

        self.mock_ssh.shell.side_effect = [b""]
        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)

        files = scanner.scan()

        self.assertEqual(["a"], [file.name for file in files])
        self.assertEqual([({"a"}, False), (None, False), (None, True)],
                         [(roots, complete) for _, roots, complete in events])
        self.mock_ssh.shell_stream.assert_called_once()
        self.assertTrue(command_finished[0])
        self.assertIn("--stream", self.mock_ssh.shell_stream.call_args.args[0])
        started_metrics = [call.args[0] for call in diagnostics.begin_duration.call_args_list]
        self.assertIn(DURATION_REMOTE_SCAN_STREAM_PARSING, started_metrics)
        self.assertIn(DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION, started_metrics)
        self.assertEqual(
            diagnostics.begin_duration.call_count,
            diagnostics.finish_duration.call_count,
        )

    def test_progressive_transport_accepts_chunked_legacy_json_with_empty_transport_return(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        root = {"name": "legacy.bin", "size": 7, "is_dir": False}
        self.mock_ssh.shell.side_effect = [b""]
        payload = b" \n" + json.dumps([root]).encode()

        def shell_stream(command, on_chunk, *, retain_output=True):
            self.assertFalse(retain_output)
            for offset in range(0, len(payload), 3):
                on_chunk(payload[offset:offset + 3])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        events = []
        scanner.set_progress_callback(lambda files, pair_id, pair_name, roots, complete:
                                       events.append((files, roots, complete)))

        files = scanner.scan()

        self.assertEqual(["legacy.bin"], [file.name for file in files])
        self.assertEqual([({"legacy.bin"}, False), (None, False), (None, True)],
                         [(roots, complete) for _, roots, complete in events])
        self.mock_ssh.shell_stream.assert_called_once()
        self.assertFalse(self.mock_ssh.shell_stream.call_args.kwargs["retain_output"])

    def test_progressive_stream_does_not_retain_raw_v2_output(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        stream = (
            'SEEDSYNC_SCAN_V2\t{"type":"manifest","names":["v2.bin"]}\n'
            'SEEDSYNC_SCAN_V2\t{"type":"roots","files":[{"name":"v2.bin","size":1,"is_dir":false}]}\n'
            'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'
        ).encode()
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk, *, retain_output=True):
            self.assertFalse(retain_output)
            for offset in range(0, len(stream), 5):
                on_chunk(stream[offset:offset + 5])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        files = scanner.scan()

        self.assertEqual(["v2.bin"], [file.name for file in files])
        self.mock_ssh.shell_stream.assert_called_once()
        self.assertFalse(self.mock_ssh.shell_stream.call_args.kwargs["retain_output"])

    def test_progressive_stream_ignores_warning_before_split_v2_prefix(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        stream = (
            b"[scanfs warning] legacy mode disabled\n"
            b'SEEDSYNC_SCAN_V2\t{"type":"manifest","names":["warned.bin"]}\n'
            b'SEEDSYNC_SCAN_V2\t{"type":"roots","files":[{"name":"warned.bin","size":1,"is_dir":false}]}\n'
            b'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'
        )
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk, *, retain_output=True):
            self.assertFalse(retain_output)
            for offset in range(0, len(stream), 7):
                on_chunk(stream[offset:offset + 7])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        files = scanner.scan()

        self.assertEqual(["warned.bin"], [file.name for file in files])
        self.assertFalse(self.mock_ssh.shell_stream.call_args.kwargs["retain_output"])

    def test_progressive_transport_ignores_warning_before_legacy_json(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        payload = (
            b"[scanfs warning] stream disabled\n"
            b"  [{\"name\":\"warned-legacy.bin\",\"size\":2,\"is_dir\":false}]\n"
        )
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk, *, retain_output=True):
            self.assertFalse(retain_output)
            for offset in range(0, len(payload), 4):
                on_chunk(payload[offset:offset + 4])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        files = scanner.scan()

        self.assertEqual(["warned-legacy.bin"], [file.name for file in files])

    def test_progressive_transport_bounds_long_nonprotocol_probe(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        payload = b"warning without a newline " + b"x" * (128 * 1024)
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk, *, retain_output=True):
            self.assertFalse(retain_output)
            for offset in range(0, len(payload), 4096):
                on_chunk(payload[offset:offset + 4096])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        with self.assertRaises(ScannerError) as context:
            scanner.scan()

        self.assertTrue(context.exception.recoverable)
        self.assertIn("Invalid scan data", str(context.exception))

    def test_progressive_v2_stream_accepts_record_at_encoded_limit(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        prefix = b"SEEDSYNC_SCAN_V2\t"
        head = b'{"type":"manifest","names":["'
        tail = b'"]}\n'
        name = b"a" * (RemoteScanner._MAX_V2_STREAM_RECORD_BYTES - len(prefix) - len(head) - len(tail))
        manifest = prefix + head + name + tail
        self.assertEqual(RemoteScanner._MAX_V2_STREAM_RECORD_BYTES, len(manifest))
        self.mock_ssh.shell.side_effect = [b""]
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (
            on_chunk(manifest),
            on_chunk(b'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'),
            b"",
        )[-1])
        scanner.set_progress_callback(lambda *args: None)

        self.assertEqual([], scanner.scan())

    def test_progressive_v2_stream_rejects_prefixed_unterminated_oversized_record(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        oversized = b"SEEDSYNC_SCAN_V2\t{" + b"x" * RemoteScanner._MAX_V2_STREAM_RECORD_BYTES
        self.mock_ssh.shell.side_effect = [b""]
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (
            on_chunk(oversized), b"",
        )[-1])
        scanner.set_progress_callback(lambda *args: None)

        with self.assertRaises(ScannerError) as context:
            scanner.scan()

        self.assertTrue(context.exception.recoverable)
        self.assertIn("Invalid scan data", str(context.exception))

    def test_one_shot_v2_fallback_enforces_the_same_exact_and_oversize_record_limits(self):
        prefix = b"SEEDSYNC_SCAN_V2\t"
        head = b'{"type":"manifest","names":["'
        tail = b'"]}\n'
        name = b"a" * (RemoteScanner._MAX_V2_STREAM_RECORD_BYTES - len(prefix) - len(head) - len(tail))
        exact_stream = prefix + head + name + tail + b'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'

        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        scanner.set_progress_callback(lambda *args: None)
        self.mock_ssh.shell.return_value = exact_stream
        self.assertEqual([], scanner.scan())

        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        scanner.set_progress_callback(lambda *args: None)
        self.mock_ssh.shell.return_value = b"SEEDSYNC_SCAN_V2\t{" + b"x" * RemoteScanner._MAX_V2_STREAM_RECORD_BYTES

        with self.assertRaises(ScannerError) as context:
            scanner.scan()

        self.assertTrue(context.exception.recoverable)
        self.assertIn("Invalid scan data", str(context.exception))

    def test_one_shot_v2_does_not_publish_complete_before_trailing_data_is_validated(self):
        valid_stream = (
            b'SEEDSYNC_SCAN_V2\t{"type":"manifest","names":["root"]}\n'
            b'SEEDSYNC_SCAN_V2\t{"type":"roots","files":[{"name":"root","size":0,"is_dir":false}]}\n'
            b'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'
        )
        trailing_records = (
            b"SEEDSYNC_SCAN_V2\t{not-json}\n",
            b"SEEDSYNC_SCAN_V2\t{" + b"x" * RemoteScanner._MAX_V2_STREAM_RECORD_BYTES,
            b"warning: " + b"x" * RemoteScanner._MAX_V2_STREAM_RECORD_BYTES,
        )
        for trailing_record in trailing_records:
            with self.subTest(trailing_record=trailing_record[:32]):
                scanner = RemoteScanner(
                    remote_address="host", remote_username="user", remote_password="password", remote_port=22,
                    remote_path_to_scan="/remote/path/to/scan",
                    local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
                    remote_path_to_scan_script="/remote/path/to/scan/script",
                )
                scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
                events = []
                scanner.set_progress_callback(
                    lambda files, pair_id, pair_name, roots, complete: events.append((files, roots, complete))
                )
                self.mock_ssh.shell_stream = None
                self.mock_ssh.shell.return_value = valid_stream + trailing_record

                with self.assertRaises(ScannerError) as context:
                    scanner.scan()

                self.assertTrue(context.exception.recoverable)
                self.assertFalse(any(complete for _, _, complete in events))

    def test_progressive_v2_round_trips_large_recursive_root_as_bounded_nodes(self):
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        child_names = ["child-{:04d}-{}.bin".format(index, "x" * 80) for index in range(800)]
        records = [
            {"type": "manifest_begin", "count": 1},
            {"type": "manifest_names", "names": ["huge"]},
            {"type": "manifest_end"},
            {"type": "root_begin", "id": 0, "name": "huge"},
        ]
        nodes = [
            {"id": 0, "parent": None, "file": {"name": "huge", "size": 0, "is_dir": True}},
            *[
                {"id": index + 1, "parent": 0,
                 "file": {"name": name, "size": index, "is_dir": False}}
                for index, name in enumerate(child_names)
            ],
        ]
        records.extend(
            {"type": "root_nodes", "root": 0, "nodes": nodes[index:index + 64]}
            for index in range(0, len(nodes), 64)
        )
        records.extend((
            {"type": "root_end", "id": 0, "nodes": 801},
            {"type": "complete"},
        ))
        encoded = ["SEEDSYNC_SCAN_V2\t{}\n".format(json.dumps(record, separators=(",", ":"))).encode()
                   for record in records]
        self.assertTrue(all(len(record) <= RemoteScanner._MAX_V2_STREAM_RECORD_BYTES for record in encoded))
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk):
            for record in encoded:
                on_chunk(record)
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        files = scanner.scan()

        self.assertEqual(["huge"], [file.name for file in files])
        self.assertEqual(child_names, [child.name for child in files[0].children])

    def test_progressive_v2_rejects_malformed_root_node_ordering_recoverably(self):
        cases = (
            ("missing", [
                {"type": "manifest_begin", "count": 1},
                {"type": "manifest_names", "names": ["root"]},
                {"type": "manifest_end"},
                {"type": "root_begin", "id": 0, "name": "root"},
                {"type": "root_node", "root": 0, "id": 0, "parent": None,
                 "file": {"name": "root", "size": 0, "is_dir": True}},
                {"type": "complete"},
            ]),
            ("out_of_order", [
                {"type": "manifest_begin", "count": 1},
                {"type": "manifest_names", "names": ["root"]},
                {"type": "manifest_end"},
                {"type": "root_begin", "id": 0, "name": "root"},
                {"type": "root_node", "root": 0, "id": 1, "parent": None,
                 "file": {"name": "root", "size": 0, "is_dir": True}},
            ]),
            ("batched_out_of_order", [
                {"type": "manifest_begin", "count": 1},
                {"type": "manifest_names", "names": ["root"]},
                {"type": "manifest_end"},
                {"type": "root_begin", "id": 0, "name": "root"},
                {"type": "root_nodes", "root": 0, "nodes": [
                    {"id": 1, "parent": None,
                     "file": {"name": "root", "size": 0, "is_dir": True}},
                ]},
            ]),
            ("oversized_node_batch", [
                {"type": "manifest_begin", "count": 1},
                {"type": "manifest_names", "names": ["root"]},
                {"type": "manifest_end"},
                {"type": "root_begin", "id": 0, "name": "root"},
                {"type": "root_nodes", "root": 0, "nodes": [
                    {"id": index, "parent": None,
                     "file": {"name": "root", "size": 0, "is_dir": True}}
                    for index in range(65)
                ]},
            ]),
            ("batched_boolean_root", [
                {"type": "manifest_begin", "count": 1},
                {"type": "manifest_names", "names": ["root"]},
                {"type": "manifest_end"},
                {"type": "root_begin", "id": 0, "name": "root"},
                {"type": "root_nodes", "root": False, "nodes": [
                    {"id": 0, "parent": None,
                     "file": {"name": "root", "size": 0, "is_dir": True}},
                ]},
            ]),
            ("duplicate", [
                {"type": "manifest_begin", "count": 1},
                {"type": "manifest_names", "names": ["root"]},
                {"type": "manifest_end"},
                {"type": "root_begin", "id": 0, "name": "root"},
                {"type": "root_node", "root": 0, "id": 0, "parent": None,
                 "file": {"name": "root", "size": 0, "is_dir": True}},
                {"type": "root_node", "root": 0, "id": 0, "parent": 0,
                 "file": {"name": "duplicate", "size": 0, "is_dir": False}},
            ]),
            ("mixed_framed_legacy", [
                {"type": "manifest_begin", "count": 1},
                {"type": "manifest_names", "names": ["root"]},
                {"type": "manifest_end"},
                {"type": "roots", "files": [{"name": "root", "size": 0, "is_dir": False}]},
            ]),
            ("duplicate_manifest_fragment_name", [
                {"type": "manifest_begin", "count": 1},
                {"type": "manifest_names", "names": ["root", "root"]},
            ]),
            ("mixed_legacy_framed", [
                {"type": "manifest", "names": ["root"]},
                {"type": "root_begin", "id": 0, "name": "root"},
            ]),
            ("legacy_unknown_root", [
                {"type": "manifest", "names": ["root"]},
                {"type": "roots", "files": [{"name": "other", "size": 0, "is_dir": False}]},
            ]),
            ("legacy_duplicate_root", [
                {"type": "manifest", "names": ["root"]},
                {"type": "roots", "files": [{"name": "root", "size": 0, "is_dir": False}]},
                {"type": "roots", "files": [{"name": "root", "size": 0, "is_dir": False}]},
            ]),
            ("duplicate_complete", [
                {"type": "manifest", "names": []},
                {"type": "complete"},
                {"type": "complete"},
            ]),
        )
        for name, records in cases:
            with self.subTest(name=name):
                scanner = RemoteScanner(
                    remote_address="host", remote_username="user", remote_password="password", remote_port=22,
                    remote_path_to_scan="/remote/path/to/scan",
                    local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
                    remote_path_to_scan_script="/remote/path/to/scan/script",
                )
                payload = b"".join(
                    "SEEDSYNC_SCAN_V2\t{}\n".format(json.dumps(record, separators=(",", ":"))).encode()
                    for record in records
                )
                self.mock_ssh.shell.side_effect = [b""]
                self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (
                    on_chunk(payload), b""
                )[-1])
                scanner.set_progress_callback(lambda *args: None)

                with self.assertRaises(ScannerError) as context:
                    scanner.scan()

                self.assertTrue(context.exception.recoverable)
                self.assertIn("Invalid scan data", str(context.exception))

    def test_progressive_v2_stream_accepts_many_bounded_records(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        roots = [
            {"name": "root-{}".format(index), "size": index, "is_dir": False}
            for index in range(256)
        ]
        records = [
            "SEEDSYNC_SCAN_V2\t{}\n".format(json.dumps({
                "type": "manifest", "names": [root["name"] for root in roots],
            })).encode(),
        ]
        records.extend(
            "SEEDSYNC_SCAN_V2\t{}\n".format(json.dumps({"type": "roots", "files": [root]})).encode()
            for root in roots
        )
        records.append(b'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n')
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk):
            for record in records:
                self.assertLessEqual(len(record), RemoteScanner._MAX_V2_STREAM_RECORD_BYTES)
                on_chunk(record)
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        files = scanner.scan()

        self.assertEqual(256, len(files))
        self.assertEqual("root-255", files[-1].name)

    def test_progressive_transport_rejects_malformed_legacy_json_from_empty_return(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk, *, retain_output=True):
            self.assertFalse(retain_output)
            on_chunk(b" \nnot-json")
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        with self.assertRaises(ScannerError) as context:
            scanner.scan()

        self.assertTrue(context.exception.recoverable)
        self.assertIn("Invalid scan data", str(context.exception))

    def test_progressive_transport_accepts_oversize_legacy_json_from_empty_return(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        roots = [
            {"name": "legacy-{}.bin".format(index), "size": index, "is_dir": False}
            for index in range(4096)
        ]
        payload = json.dumps(roots).encode()
        self.assertGreater(len(payload), 64 * 1024)
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk, *, retain_output=True):
            self.assertFalse(retain_output)
            for offset in range(0, len(payload), 4096):
                on_chunk(payload[offset:offset + 4096])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        files = scanner.scan()

        self.assertEqual(len(roots), len(files))
        self.assertEqual("legacy-4095.bin", files[-1].name)

    def test_unsupported_stream_option_retries_once_with_legacy_command(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        self.mock_ssh.shell.side_effect = [b""]
        self.mock_ssh.shell_stream = MagicMock(side_effect=[
            SshcpError("usage: scanfs: unknown option --stream"),
            json.dumps([{"name": "legacy.bin", "size": 1, "is_dir": False}]).encode(),
        ])
        scanner.set_progress_callback(lambda *args: None)

        files = scanner.scan()

        self.assertEqual(["legacy.bin"], [file.name for file in files])
        self.assertEqual(2, self.mock_ssh.shell_stream.call_count)
        first_command = self.mock_ssh.shell_stream.call_args_list[0].args[0]
        second_command = self.mock_ssh.shell_stream.call_args_list[1].args[0]
        self.assertIn("--stream", first_command)
        self.assertNotIn("--stream", second_command)

    def test_progressive_stream_decodes_utf8_codepoint_split_across_chunks(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        stream = (
            'SEEDSYNC_SCAN_V2\t{"type":"manifest","names":["caf\u00e9.bin"]}\n'
            'SEEDSYNC_SCAN_V2\t{"type":"roots","files":[{"name":"caf\u00e9.bin","size":1,"is_dir":false}]}\n'
            'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'
        ).encode("utf-8")
        split_at = stream.index(b"\xc3\xa9") + 1
        self.mock_ssh.shell.side_effect = [b""]

        def shell_stream(command, on_chunk):
            on_chunk(stream[:split_at])
            on_chunk(stream[split_at:])
            return b""

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        scanner.set_progress_callback(lambda *args: None)

        files = scanner.scan()

        self.assertEqual(["caf\u00e9.bin"], [file.name for file in files])

    def test_progressive_v2_utf8_split_counts_pending_bytes_at_the_record_limit(self):
        prefix = b"SEEDSYNC_SCAN_V2\t"
        head = b'{"type":"manifest","names":["'
        tail = b'"]}\n'
        ascii_name_size = RemoteScanner._MAX_V2_STREAM_RECORD_BYTES - len(prefix) - len(head) - len(tail) - 2

        def scan_manifest(ascii_name: bytes):
            scanner = RemoteScanner(
                remote_address="host", remote_username="user", remote_password="password", remote_port=22,
                remote_path_to_scan="/remote/path/to/scan",
                local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
                remote_path_to_scan_script="/remote/path/to/scan/script",
            )
            first_chunk = prefix + head + ascii_name + b"\xc3"
            second_chunk = b"\xa9" + tail
            self.mock_ssh.shell.side_effect = [b""]
            self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (
                on_chunk(first_chunk),
                on_chunk(second_chunk),
                on_chunk(b'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'),
                b"",
            )[-1])
            scanner.set_progress_callback(lambda *args: None)
            return scanner

        self.assertEqual([], scan_manifest(b"a" * ascii_name_size).scan())

        with self.assertRaises(ScannerError) as context:
            scan_manifest(b"a" * (ascii_name_size + 1)).scan()

        self.assertTrue(context.exception.recoverable)
        self.assertIn("Invalid scan data", str(context.exception))

    def test_stream_complete_is_provisional_until_successful_transport_exit(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        events = []
        scanner.set_progress_callback(lambda files, pair_id, pair_name, roots, complete:
                                       events.append((files, roots, complete)))
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (
            on_chunk(b'SEEDSYNC_SCAN_V2\t{"type":"manifest","names":["late.bin"]}\n'),
            on_chunk(b'SEEDSYNC_SCAN_V2\t{"type":"roots","files":[{"name":"late.bin","size":1,"is_dir":false}]}\n'),
            on_chunk(b'SEEDSYNC_SCAN_V2\t{"type":"complete"}\n'),
            (_ for _ in ()).throw(SshcpError("remote exit status 1")),
        )[-1])

        with self.assertRaises(ScannerError) as context:
            scanner.scan()

        self.assertTrue(context.exception.recoverable)
        self.assertEqual([False, False], [complete for _, _, complete in events])

    def test_uses_direct_scan_command_for_packaged_scanfs_helpers(self):
        packaged_scanfs_path = os.path.join(TestRemoteScanner.temp_dir, "scanfs")
        with open(packaged_scanfs_path, "wb") as handle:
            handle.write(b"\x7fELF" + b"\x00" * 32)

        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=packaged_scanfs_path,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                return "".encode()
            return json.dumps([]).encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()

        expected_remote_script = "/remote/path/to/scan/script/scanfs"
        self.assertEqual(2, self.mock_ssh.shell.call_count)
        self.mock_ssh.copy.assert_called_once_with(
            local_path=packaged_scanfs_path,
            remote_path=expected_remote_script
        )
        self.mock_ssh.shell.assert_called_with(
            self._direct_scan_command(expected_remote_script, "/remote/path/to/scan")
        )

    def test_uses_configured_remote_python_path_for_shebang_python_scanfs_helpers(self):
        shebang_scanfs_path = os.path.join(TestRemoteScanner.temp_dir, "scanfs.py")
        with open(shebang_scanfs_path, "w", encoding="utf-8") as handle:
            handle.write("#!/usr/bin/env python3\nprint('hello')\n")

        remote_python_path = "/opt/custom python/bin/python3"
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=shebang_scanfs_path,
            remote_path_to_scan_script="/remote/path/to/scan/script",
            remote_python_path=remote_python_path
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                return "".encode()
            return json.dumps([]).encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()

        expected_remote_script = "/remote/path/to/scan/script/scanfs.py"
        self.assertEqual(2, self.mock_ssh.shell.call_count)
        self.mock_ssh.copy.assert_called_once_with(
            local_path=shebang_scanfs_path,
            remote_path=expected_remote_script
        )
        self.mock_ssh.shell.assert_called_with(
            self._scan_command(remote_python_path, expected_remote_script, "/remote/path/to/scan")
        )

    def test_uses_home_expansion_for_tilde_remote_paths(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="~/data/torrents",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                return "".encode()
            return json.dumps([]).encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()

        self.assertEqual(2, self.mock_ssh.shell.call_count)
        self.mock_ssh.shell.assert_called_with(
            self._scan_command("python3", "/remote/path/to/scan/script", "~/data/torrents")
        )

    def test_supports_tilde_remote_script_paths(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="~"
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                return "".encode()
            return json.dumps([]).encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()

        expected_remote_script = "~/script"
        self.mock_ssh.copy.assert_called_once_with(
            local_path=TestRemoteScanner.temp_scan_script,
            remote_path=expected_remote_script
        )
        self.assertEqual(2, self.mock_ssh.shell.call_count)
        self.mock_ssh.shell.assert_has_calls([
            call(self._scanfs_probe_command(expected_remote_script)),
            call(self._scan_command("python3", expected_remote_script, "/remote/path/to/scan"))
        ])

    def test_quotes_scan_commands_with_spaces_in_remote_paths(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path with spaces/to/scan dir",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path with spaces/to/scan script"
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                return "".encode()
            return json.dumps([]).encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()

        expected_remote_script = "/remote/path with spaces/to/scan script/script"
        self.mock_ssh.copy.assert_called_once_with(
            local_path=TestRemoteScanner.temp_scan_script,
            remote_path=expected_remote_script
        )
        self.assertEqual(2, self.mock_ssh.shell.call_count)
        self.mock_ssh.shell.assert_has_calls([
            call(self._scanfs_probe_command(expected_remote_script)),
            call(self._scan_command("python3", expected_remote_script, "/remote/path with spaces/to/scan dir"))
        ])

    def test_quotes_custom_remote_python_path_in_scan_command(self):
        for remote_python_path in (
            "/opt/custom python/bin/python3;rm -rf /",
            "/opt/custom's/bin/python3",
            '/opt/mixed "quote\'s" path/python3;$(whoami) & echo',
        ):
            with self.subTest(remote_python_path=remote_python_path):
                scanner = RemoteScanner(
                    remote_address="my remote address",
                    remote_username="my remote user",
                    remote_password="my password",
                    remote_port=1234,
                    remote_path_to_scan="/remote/path/to/scan",
                    local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
                    remote_path_to_scan_script="/remote/path/to/scan/script",
                    remote_python_path=remote_python_path
                )

                self.ssh_run_command_count = 0

                def ssh_shell(*args):
                    self.ssh_run_command_count += 1
                    if self.ssh_run_command_count == 1:
                        return "".encode()
                    return json.dumps([]).encode()

                self.mock_ssh.shell.side_effect = ssh_shell

                scanner.scan()

                self.assertEqual(2, self.mock_ssh.shell.call_count)
                self.mock_ssh.shell.assert_called_with(
                    self._scan_command(remote_python_path, "/remote/path/to/scan/script", "/remote/path/to/scan")
                )
                self.mock_ssh.shell.reset_mock()
                self.mock_ssh.copy.reset_mock()

    def test_falls_back_to_python3_for_blank_remote_python_path(self):
        for remote_python_path in (None, "", "   "):
            with self.subTest(remote_python_path=remote_python_path):
                scanner = RemoteScanner(
                    remote_address="my remote address",
                    remote_username="my remote user",
                    remote_password="my password",
                    remote_port=1234,
                    remote_path_to_scan="/remote/path/to/scan",
                    local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
                    remote_path_to_scan_script="/remote/path/to/scan/script",
                    remote_python_path=remote_python_path
                )

                self.ssh_run_command_count = 0

                def ssh_shell(*args):
                    self.ssh_run_command_count += 1
                    if self.ssh_run_command_count == 1:
                        return "".encode()
                    return json.dumps([]).encode()

                self.mock_ssh.shell.side_effect = ssh_shell

                scanner.scan()

                self.assertEqual(2, self.mock_ssh.shell.call_count)
                self.mock_ssh.shell.assert_called_with(
                    self._scan_command(remote_python_path, "/remote/path/to/scan/script", "/remote/path/to/scan")
                )
                self.mock_ssh.shell.reset_mock()
                self.mock_ssh.copy.reset_mock()

    def test_raises_nonrecoverable_error_on_first_failed_ssh(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh run command fails the first time
        # noinspection PyUnusedLocal
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # md5sum check
                return b''
            elif self.ssh_run_command_count == 2:
                # first try
                raise SshcpError("an ssh error")
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_SCAN.format("an ssh error"), str(ctx.exception))
        self.assertFalse(ctx.exception.recoverable)

    def test_raises_recoverable_error_on_first_run_timeout(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh run command times out during first run scan
        # noinspection PyUnusedLocal
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # md5sum check
                return b''
            elif self.ssh_run_command_count == 2:
                # first scan attempt
                raise SshcpError("Timed out")
            else:
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_SCAN.format("Timed out"), str(ctx.exception))
        self.assertTrue(ctx.exception.recoverable)

    def test_raises_recoverable_error_on_first_run_connection_refused(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh run command gets connection refused during first run scan
        # noinspection PyUnusedLocal
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # md5sum check
                return b''
            elif self.ssh_run_command_count == 2:
                # first scan attempt
                raise SshcpError("Connection refused by server")
            else:
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(
            Localization.Error.REMOTE_SERVER_SCAN.format("Connection refused by server"),
            str(ctx.exception)
        )
        self.assertTrue(ctx.exception.recoverable)

    def test_raises_recoverable_error_on_subsequent_failed_ssh(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh run command succeeds first time, raises error the second time
        # noinspection PyUnusedLocal
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # md5sum check
                return b''
            elif self.ssh_run_command_count == 2:
                # first try
                return json.dumps([]).encode()
            elif self.ssh_run_command_count == 3:
                # second try
                raise SshcpError("an ssh error")
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()  # no error first time
        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_SCAN.format("an ssh error"), str(ctx.exception))
        self.assertTrue(ctx.exception.recoverable)

    def test_recovers_from_failed_ssh(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh run command succeeds first time, raises error the second time, fine after that
        # noinspection PyUnusedLocal
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # md5sum check
                return b''
            elif self.ssh_run_command_count == 2:
                # first try
                return json.dumps([]).encode()
            elif self.ssh_run_command_count == 3:
                # second try
                raise SshcpError("an ssh error")
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        scanner.scan()  # no error first time
        with self.assertRaises(ScannerError):
            scanner.scan()
        scanner.scan()
        self.assertEqual(4, self.mock_ssh.shell.call_count)

    def test_raises_nonrecoverable_error_on_failed_copy(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        # noinspection PyUnusedLocal
        def ssh_copy(*args, **kwargs):
            raise SshcpError("an scp error")
        self.mock_ssh.copy.side_effect = ssh_copy

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_INSTALL.format("an scp error"), str(ctx.exception))
        self.assertFalse(ctx.exception.recoverable)

    def test_raises_recoverable_error_on_failed_copy_timeout(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        # noinspection PyUnusedLocal
        def ssh_copy(*args, **kwargs):
            raise SshcpError("Timed out")
        self.mock_ssh.copy.side_effect = ssh_copy

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_INSTALL.format("Timed out"), str(ctx.exception))
        self.assertTrue(ctx.exception.recoverable)

    def test_raises_recoverable_error_on_failed_copy_connection_refused(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        # noinspection PyUnusedLocal
        def ssh_copy(*args, **kwargs):
            raise SshcpError("Connection refused by server")
        self.mock_ssh.copy.side_effect = ssh_copy

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format("Connection refused by server"),
            str(ctx.exception)
        )
        self.assertTrue(ctx.exception.recoverable)

    def test_does_not_fallback_for_unrelated_copy_permission_denied_error(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        def ssh_copy(*args, **kwargs):
            raise SshcpError("Permission denied (publickey)")
        self.mock_ssh.copy.side_effect = ssh_copy

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format("Permission denied (publickey)"),
            str(ctx.exception)
        )
        self.assertFalse(ctx.exception.recoverable)
        self.assertEqual(1, self.mock_ssh.shell.call_count)
        self.mock_ssh.copy.assert_called_once_with(
            local_path=TestRemoteScanner.temp_scan_script,
            remote_path="/remote/path/to/scan/script"
        )

    def test_does_not_fallback_for_adjacent_denied_scanfs_path(self):
        local_scanfs_path = os.path.join(TestRemoteScanner.temp_dir, "scanfs")
        with open(local_scanfs_path, "w") as f:
            f.write("")

        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=local_scanfs_path,
            remote_path_to_scan_script="/tmp/scanfs"
        )

        def ssh_copy(*args, **kwargs):
            raise SshcpError("scp: /tmp/scanfs.old: Permission denied")
        self.mock_ssh.copy.side_effect = ssh_copy

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format("scp: /tmp/scanfs.old: Permission denied"),
            str(ctx.exception)
        )
        self.assertFalse(ctx.exception.recoverable)
        self.assertEqual(1, self.mock_ssh.shell.call_count)
        self.mock_ssh.copy.assert_called_once_with(
            local_path=local_scanfs_path,
            remote_path="/tmp/scanfs"
        )

    def test_raises_nonrecoverable_error_when_remote_scanfs_script_path_is_a_directory(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            return "IS_DIRECTORY".encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format(
                "Server Script Path '/remote/path/to/scan/script' is a directory on the remote server. "
                "Change the 'Server Script Path' setting to a writable location outside your sync tree "
                "(e.g. '~' or '~/.local') and remove the conflicting directory from the remote server."
            ),
            str(ctx.exception)
        )
        self.assertFalse(ctx.exception.recoverable)
        self.assertEqual(1, self.mock_ssh.shell.call_count)
        self.mock_ssh.shell.assert_called_once_with(self._scanfs_probe_command("/remote/path/to/scan/script"))
        self.mock_ssh.copy.assert_not_called()

    def test_falls_back_to_home_script_path_on_permission_denied(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/tmp"
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                return "".encode()
            if self.ssh_run_command_count == 2:
                return "".encode()
            return json.dumps([]).encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        self.copy_run_count = 0

        def ssh_copy(*args, **kwargs):
            self.copy_run_count += 1
            if self.copy_run_count == 1:
                raise SshcpError("scp: dest open /tmp/script: Permission denied")

        self.mock_ssh.copy.side_effect = ssh_copy

        scanner.scan()

        expected_primary_script = "/tmp/script"
        expected_fallback_script = "~/script"
        self.assertEqual(3, self.mock_ssh.shell.call_count)
        self.mock_ssh.shell.assert_has_calls([
            call(self._scanfs_probe_command(expected_primary_script)),
            call(self._scanfs_probe_command(expected_fallback_script)),
            call(self._scan_command("python3", expected_fallback_script, "/remote/path/to/scan"))
        ])
        self.assertEqual(2, self.copy_run_count)
        self.mock_ssh.copy.assert_has_calls([
            call(local_path=TestRemoteScanner.temp_scan_script, remote_path=expected_primary_script),
            call(local_path=TestRemoteScanner.temp_scan_script, remote_path=expected_fallback_script)
        ])
        self.mock_ssh.copy.assert_called_with(
            local_path=TestRemoteScanner.temp_scan_script,
            remote_path=expected_fallback_script
        )

    def test_falls_back_to_home_script_path_on_scp_hyphen_permission_denied(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/tmp"
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                return "".encode()
            if self.ssh_run_command_count == 2:
                return "".encode()
            return json.dumps([]).encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        self.copy_run_count = 0

        def ssh_copy(*args, **kwargs):
            self.copy_run_count += 1
            if self.copy_run_count == 1:
                raise SshcpError("scp: /tmp/script: - Permission denied")

        self.mock_ssh.copy.side_effect = ssh_copy

        scanner.scan()

        expected_fallback_script = "~/script"
        self.assertEqual(3, self.mock_ssh.shell.call_count)
        self.mock_ssh.copy.assert_has_calls([
            call(local_path=TestRemoteScanner.temp_scan_script, remote_path="/tmp/script"),
            call(local_path=TestRemoteScanner.temp_scan_script, remote_path=expected_fallback_script)
        ])
        self.mock_ssh.shell.assert_called_with(
            self._scan_command("python3", expected_fallback_script, "/remote/path/to/scan")
        )

    def test_preserves_original_permission_denied_context_when_fallback_copy_fails(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/tmp"
        )

        self.ssh_run_command_count = 0

        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                return "".encode()
            if self.ssh_run_command_count == 2:
                return "".encode()
            return json.dumps([]).encode()

        self.mock_ssh.shell.side_effect = ssh_shell

        self.copy_run_count = 0

        def ssh_copy(*args, **kwargs):
            self.copy_run_count += 1
            if self.copy_run_count == 1:
                raise SshcpError("scp: dest open /tmp/script: Permission denied")
            raise SshcpError("an scp error")

        self.mock_ssh.copy.side_effect = ssh_copy

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format(
                "Could not install scanner to '/tmp/script' (scp: dest open /tmp/script: Permission denied), "
                "fallback to '~/script' also failed: an scp error"
            ),
            str(ctx.exception)
        )
        self.assertFalse(ctx.exception.recoverable)
        self.assertEqual(2, self.copy_run_count)

    def test_raises_nonrecoverable_error_on_mangled_output(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        def ssh_shell(*args):
            return "mangled data".encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_SCAN.format("Invalid scan data"), str(ctx.exception))
        self.assertFalse(ctx.exception.recoverable)

    def test_raises_nonrecoverable_error_on_non_mapping_scan_entry(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        def ssh_shell(*args):
            return json.dumps([[]]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(Localization.Error.REMOTE_SERVER_SCAN.format("Invalid scan data"), str(ctx.exception))
        self.assertFalse(ctx.exception.recoverable)

    def test_scan_rejects_non_string_remote_script_path(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script=None
        )

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format(
                "Remote scan script path must be absolute or start with '~': None"
            ),
            str(ctx.exception)
        )
        self.assertFalse(ctx.exception.recoverable)

    def test_scan_rejects_non_string_local_script_path(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=None,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertEqual(
            Localization.Error.REMOTE_SERVER_SCAN.format(
                "Failed to find scanfs executable at None"
            ),
            str(ctx.exception)
        )
        self.assertFalse(ctx.exception.recoverable)

    def test_raises_nonrecoverable_error_on_failed_scan(self):
        scanner = RemoteScanner(
            remote_address="my remote address",
            remote_username="my remote user",
            remote_password="my password",
            remote_port=1234,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script"
        )

        self.ssh_run_command_count = 0

        # Ssh run command raises error the first time, succeeds the second time
        # noinspection PyUnusedLocal
        def ssh_shell(*args):
            self.ssh_run_command_count += 1
            if self.ssh_run_command_count == 1:
                # md5sum check
                return b''
            elif self.ssh_run_command_count == 2:
                # first try
                raise SshcpError("SystemScannerError: something failed")
            else:
                # later tries
                return json.dumps([]).encode()
        self.mock_ssh.shell.side_effect = ssh_shell

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()
        self.assertEqual(
            Localization.Error.REMOTE_SERVER_SCAN.format("SystemScannerError: something failed"),
            str(ctx.exception)
        )
        self.assertFalse(ctx.exception.recoverable)

    def test_progressive_v2_retains_compact_unchanged_root_marker(self):
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        fingerprint = "a" * 64
        root_name = "root ' with space"
        scanner.set_accepted_root_fingerprints({root_name: fingerprint})
        records = [
            {"type": "manifest_begin", "count": 1},
            {"type": "manifest_names", "names": [root_name]},
            {"type": "manifest_end"},
            {"type": "root_unchanged", "name": root_name, "fingerprint": fingerprint},
            {"type": "complete"},
        ]
        payload = b"".join(
            "SEEDSYNC_SCAN_V2\t{}\n".format(json.dumps(record, separators=(",", ":"))).encode()
            for record in records
        )
        commands = []
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (
            commands.append(command), on_chunk(payload), b""
        )[-1])
        events = []
        scanner.set_progress_callback(lambda *args: events.append(args))

        self.assertEqual([], scanner.scan())
        arguments = shlex.split(commands[0])
        self.assertIn("--stream-known-root-fingerprints-b64", arguments)
        self.assertNotIn("--stream-known-root-fingerprints", arguments)
        with patch.object(Sshcp, "_Sshcp__run_command_stream", return_value=b""):
            Sshcp(host="host", port=22, user="user", password="password").shell_stream(
                commands[0], lambda _chunk: None, retain_output=False
            )
        encoded_index = arguments.index("--stream-known-root-fingerprints-b64") + 1
        self.assertEqual(
            {root_name: fingerprint},
            json.loads(base64.urlsafe_b64decode(arguments[encoded_index]).decode("utf-8")),
        )
        self.assertIn({root_name: fingerprint}, [event[-1] for event in events if event[-1]])

    def test_progressive_v2_unsupported_hint_falls_back_to_full_scan(self):
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        scanner.set_accepted_root_fingerprints({"root": "a" * 64})
        commands = []

        def shell_stream(command, on_chunk, retain_output=False):
            commands.append(command)
            if "--stream-known-root-fingerprints-b64" in command:
                raise SshcpError("unrecognized option: --stream-known-root-fingerprints-b64")
            return json.dumps([{"name": "root", "size": 1, "is_dir": False}]).encode("utf-8")

        self.mock_ssh.shell_stream = MagicMock(side_effect=shell_stream)
        events = []
        scanner.set_progress_callback(lambda *args: events.append(args))

        self.assertEqual(["root"], [file.name for file in scanner.scan()])
        self.assertEqual(2, len(commands))
        self.assertIn("--stream-known-root-fingerprints-b64", shlex.split(commands[0]))
        self.assertNotIn("--stream-known-root-fingerprints", shlex.split(commands[1]))
        self.assertTrue(events)

    def test_legacy_five_argument_progress_callback_falls_back_to_full_roots(self):
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        scanner.set_accepted_root_fingerprints({"root": "a" * 64})
        commands = []
        payload = self._framed_stream(["root"])
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (
            commands.append(command), on_chunk(payload), b""
        )[-1])
        events = []

        def legacy_callback(files, path_pair_id, path_pair_name, root_names, complete):
            events.append((files, path_pair_id, path_pair_name, root_names, complete))

        scanner.set_progress_callback(legacy_callback)

        self.assertEqual(["root"], [file.name for file in scanner.scan()])
        self.assertNotIn("--stream-known-root-fingerprints", commands[0])
        self.assertTrue(events)

    def test_oversized_root_fingerprint_map_falls_back_to_full_stream(self):
        scanner = RemoteScanner(
            remote_address="host", remote_username="user", remote_password="password", remote_port=22,
            remote_path_to_scan="/remote/path/to/scan", local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
        )
        scanner.apply_recycled_state((False, "/remote/path/to/scan/script"))
        scanner.set_accepted_root_fingerprints({
            "root-{:04d}".format(index): "a" * 64 for index in range(600)
        })
        commands = []
        payload = b"".join(
            "SEEDSYNC_SCAN_V2\t{}\n".format(json.dumps(record, separators=(",", ":"))).encode()
            for record in (
                {"type": "manifest_begin", "count": 0},
                {"type": "manifest_end"},
                {"type": "complete"},
            )
        )
        self.mock_ssh.shell_stream = MagicMock(side_effect=lambda command, on_chunk: (
            commands.append(command), on_chunk(payload), b""
        )[-1])
        scanner.set_progress_callback(lambda *args: None)

        self.assertEqual([], scanner.scan())
        self.assertNotIn("--stream-known-root-fingerprints", commands[0])
