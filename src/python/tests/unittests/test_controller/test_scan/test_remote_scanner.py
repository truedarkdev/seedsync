# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import logging
import sys
from unittest.mock import patch, call, ANY
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
