# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import logging
import sys
from unittest.mock import patch, call, ANY, MagicMock
import tempfile
import os
import json
import shutil
import shlex

from controller.scan import RemoteScanner, ScannerError
from ssh import SshcpError
from common import Localization, escape_remote_path_for_shell


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

    def test_progressive_stream_publishes_before_remote_command_eof(self):
        scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/remote/path/to/scan",
            local_path_to_scan_script=TestRemoteScanner.temp_scan_script,
            remote_path_to_scan_script="/remote/path/to/scan/script",
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
