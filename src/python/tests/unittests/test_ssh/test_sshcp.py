# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import os
import tempfile
import shutil
import filecmp
import logging
import sys
from unittest.mock import MagicMock, patch

import pexpect
import pytest
from parameterized import parameterized

from tests.utils import TestUtils, requires_live_ssh
from common import overrides
from ssh import Sshcp, SshcpError


# Test credentials for the Docker-based test container.
# noinspection SpellCheckingInspection
_PASSWORD = "seedsyncpass"
# noinspection SpellCheckingInspection
_PARAMS = [
    ("password", _PASSWORD),
    ("keyauth", None)
]


# noinspection SpellCheckingInspection
pytestmark = pytest.mark.timeout(5)

class TestSshcp(unittest.TestCase):
    __KEEP_FILES = False  # for debugging

    @overrides(unittest.TestCase)
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_sshcp")
        self.local_dir = os.path.join(self.temp_dir, "local")
        os.mkdir(self.local_dir)
        self.remote_dir = os.path.join(self.temp_dir, "remote")
        os.mkdir(self.remote_dir)

        # Allow group access for the seedsynctest account
        TestUtils.chmod_from_to(self.remote_dir, tempfile.gettempdir(), 0o775)

        # Note: seedsynctest account must be set up. See DeveloperReadme.md for details
        self.host = "127.0.0.1"
        self.port = 22
        self.user = "seedsynctest"

        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        # Create local file
        self.local_file = os.path.join(self.local_dir, "file.txt")
        self.remote_file = os.path.join(self.remote_dir, "file2.txt")
        with open(self.local_file, "w") as f:
            f.write("this is a test file")

    @overrides(unittest.TestCase)
    def tearDown(self):
        if not self.__KEEP_FILES:
            shutil.rmtree(self.temp_dir)

    def test_ctor(self):
        sshcp = Sshcp(host=self.host, port=self.port)
        self.assertIsNotNone(sshcp)

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_copy(self, _, password):
        self.assertFalse(os.path.exists(self.remote_file))
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=password)
        sshcp.copy(local_path=self.local_file, remote_path=self.remote_file)

        self.assertTrue(filecmp.cmp(self.local_file, self.remote_file))

    @requires_live_ssh
    def test_copy_error_bad_password(self):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password="wrong password")
        with self.assertRaises(SshcpError) as ctx:
            sshcp.copy(local_path=self.local_file, remote_path=self.remote_file)
        error_str = str(ctx.exception).lower()
        self.assertIn("connection closed", error_str)
        self.assertNotIn("incorrect password", error_str)

    def test_copy_preserves_scp_destination_permission_denied_error(self):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        spawn = MagicMock()
        spawn.expect.return_value = 9
        spawn.before = b"scp: /home/remoteuser/restricted/scanfs: - "
        spawn.after = b"Permission denied"

        with patch("ssh.sshcp.pexpect.spawn", return_value=spawn, create=True):
            with self.assertRaises(SshcpError) as ctx:
                sshcp.copy(local_path=self.local_file, remote_path="/home/remoteuser/restricted/scanfs")

        self.assertEqual(
            "scp: /home/remoteuser/restricted/scanfs: - Permission denied",
            str(ctx.exception)
        )

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_copy_password_auth_permission_denied_before_prompt_maps_to_incorrect_password(
        self,
        mock_spawn_process
    ):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=_PASSWORD)

        spawn = MagicMock()
        spawn.expect.return_value = 9
        spawn.before = b"Permission denied"
        spawn.after = b""
        spawn.exitstatus = 1
        mock_spawn_process.return_value = (spawn, False)

        with self.assertRaises(SshcpError) as ctx:
            sshcp.copy(local_path=self.local_file, remote_path=self.remote_file)

        self.assertEqual("Incorrect password", str(ctx.exception))
        spawn.sendline.assert_not_called()

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_copy_error_missing_local_file(self, _, password):
        local_file = os.path.join(self.local_dir, "nofile.txt")
        self.assertFalse(os.path.exists(self.remote_file))
        self.assertFalse(os.path.exists(local_file))

        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=password)
        with self.assertRaises(SshcpError) as ctx:
            sshcp.copy(local_path=local_file, remote_path=self.remote_file)
        self.assertTrue("No such file or directory" in str(ctx.exception))

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_copy_error_missing_remote_dir(self, _, password):
        remote_file = os.path.join(self.remote_dir, "nodir", "file2.txt")
        self.assertFalse(os.path.exists(remote_file))

        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=password)
        with self.assertRaises(SshcpError) as ctx:
            sshcp.copy(local_path=self.local_file, remote_path=remote_file)
        self.assertTrue("No such file or directory" in str(ctx.exception))

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_copy_error_bad_host(self, _, password):
        sshcp = Sshcp(host="badhost", port=self.port, user=self.user, password=password)
        with self.assertRaises(SshcpError) as ctx:
            sshcp.copy(local_path=self.local_file, remote_path=self.remote_file)
        error_str = str(ctx.exception).lower()
        self.assertTrue(
            "bad hostname" in error_str or
            "connection refused" in error_str or
            "connection closed" in error_str or
            "name or service not known" in error_str or
            "could not resolve" in error_str or
            "no route to host" in error_str or
            "unknown error" in error_str or
            "temporary failure" in error_str,
            f"Unexpected error: {ctx.exception}"
        )

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_copy_error_bad_port(self, _, password):
        sshcp = Sshcp(host=self.host, port=666, user=self.user, password=password)
        with self.assertRaises(SshcpError) as ctx:
            sshcp.copy(local_path=self.local_file, remote_path=self.remote_file)
        error_str = str(ctx.exception).lower()
        self.assertTrue(
            "connection refused" in error_str or
            "connection closed" in error_str or
            "connection timed out" in error_str or
            "no route to host" in error_str or
            "unknown error" in error_str or
            "port" in error_str,
            f"Unexpected error: {ctx.exception}"
        )

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_shell(self, _, password):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=password)
        out = sshcp.shell("cd {}; pwd".format(self.local_dir))
        out_str = out.decode().strip()
        self.assertEqual(self.local_dir, out_str)

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_shell_with_escape_characters(self, _, password):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=password)

        # single quotes
        _dir = os.path.join(self.remote_dir, "a a")
        out = sshcp.shell("mkdir '{}' && cd '{}' && pwd".format(_dir, _dir))
        out_str = out.decode().strip()
        self.assertEqual(_dir, out_str)

        # double quotes
        _dir = os.path.join(self.remote_dir, "a b")
        out = sshcp.shell('mkdir "{}" && cd "{}" && pwd'.format(_dir, _dir))
        out_str = out.decode().strip()
        self.assertEqual(_dir, out_str)

        # single and double quotes - error out
        _dir = os.path.join(self.remote_dir, "a b")
        with self.assertRaises(ValueError):
            sshcp.shell('mkdir "{}" && cd \'{}\' && pwd'.format(_dir, _dir))

    @requires_live_ssh
    def test_shell_error_bad_password(self):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password="wrong password")
        with self.assertRaises(SshcpError) as ctx:
            sshcp.shell("cd {}; pwd".format(self.local_dir))
        self.assertEqual("Incorrect password", str(ctx.exception))

    def test_shell_error_bad_owner_or_permissions_maps_to_incorrect_password(self):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password="wrong password")
        spawn = MagicMock()
        spawn.expect.return_value = 1
        spawn.before = b"Bad owner or permissions on C:\\Users\\johan/.ssh/config"
        spawn.after = b""

        with patch("ssh.sshcp.pexpect.spawn", return_value=spawn, create=True):
            with self.assertRaises(SshcpError) as ctx:
                sshcp.shell("cd {}; pwd".format(self.local_dir))

        self.assertEqual("Incorrect password", str(ctx.exception))
        spawn.sendline.assert_not_called()

    def test_shell_timeout_logs_password_prompt_context(self):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=_PASSWORD)
        sshcp.logger = MagicMock()

        spawn = MagicMock()
        spawn.expect.side_effect = pexpect.exceptions.TIMEOUT("timed out")
        spawn.before = b"waiting for password"

        with patch("ssh.sshcp.pexpect.spawn", return_value=spawn), \
                patch("ssh.sshcp.time.time", side_effect=[100.0, 103.25]):
            with self.assertRaises(SshcpError) as ctx:
                sshcp.shell("cd {}; pwd".format(self.local_dir))

        self.assertEqual("Timed out", str(ctx.exception))
        self.assertEqual(1, spawn.expect.call_count)
        self.assertEqual(
            sshcp._Sshcp__TIMEOUT_SECS,
            spawn.expect.call_args.kwargs["timeout"]
        )

        sshcp.logger.exception.assert_called_once()
        timeout_message = sshcp.logger.exception.call_args[0][0]
        self.assertIn("password prompt", timeout_message)
        self.assertIn("command=ssh", timeout_message)
        self.assertIn("host={}".format(self.host), timeout_message)
        self.assertIn("user={}".format(self.user), timeout_message)
        self.assertIn("port={}".format(self.port), timeout_message)
        self.assertIn("3.250", timeout_message)
        sshcp.logger.error.assert_called_once_with(
            "Command output before:\n{}".format(spawn.before)
        )

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_run_command_password_prompt_uses_full_timeout_and_omits_gssapi_option(
        self,
        mock_spawn_process
    ):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=_PASSWORD)
        sshcp.logger = MagicMock()

        spawn = MagicMock()
        spawn.expect.side_effect = pexpect.exceptions.TIMEOUT("timed out")
        spawn.before = b"waiting for password"
        spawn.after = b""
        mock_spawn_process.return_value = (spawn, False)

        with patch("ssh.sshcp.time.time", side_effect=[100.0, 103.25]):
            with self.assertRaises(SshcpError) as ctx:
                sshcp._Sshcp__run_command(
                    command="ssh",
                    flags=["-p", str(self.port)],
                    args=[sshcp._Sshcp__remote_address(), "echo hi"]
                )

        self.assertEqual("Timed out", str(ctx.exception))
        mock_spawn_process.assert_called_once_with(
            "ssh",
            [
                "-p",
                str(self.port),
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "LogLevel=error",
                "-o",
                "PubkeyAuthentication=no",
                sshcp._Sshcp__remote_address(),
                "echo hi",
            ]
        )
        self.assertNotIn("GSSAPIAuthentication=no", mock_spawn_process.call_args.args[1])
        self.assertEqual(
            sshcp._Sshcp__TIMEOUT_SECS,
            spawn.expect.call_args.kwargs["timeout"]
        )

    def test_spawn_fallback_forwards_argv_list(self):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        spawn = MagicMock()
        spawn.expect.return_value = 0
        spawn.before = b""
        spawn.after = b""
        spawn.exitstatus = 0

        with patch("ssh.sshcp.pexpect.spawn", None, create=True), \
                patch("ssh.sshcp.shutil.which", return_value="C:\\WINDOWS\\System32\\OpenSSH\\ssh.EXE"), \
                patch("ssh.sshcp.pexpect.popen_spawn.PopenSpawn", return_value=spawn) as popen_spawn:
            result = sshcp._Sshcp__run_command(
                "ssh",
                ["-p", "22"],
                ["host", "echo hi"]
            )

        self.assertEqual(b"", result)
        popen_spawn.assert_called_once_with([
            "C:\\WINDOWS\\System32\\OpenSSH\\ssh.EXE",
            "-p",
            "22",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "LogLevel=error",
            "-o",
            "PasswordAuthentication=no",
            "host",
            "echo hi",
        ])

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_shell_error_bad_host(self, _, password):
        sshcp = Sshcp(host="badhost", port=self.port, user=self.user, password=password)
        with self.assertRaises(SshcpError) as ctx:
            sshcp.shell("cd {}; pwd".format(self.local_dir))
        error_str = str(ctx.exception).lower()
        self.assertTrue(
            "bad hostname" in error_str or
            "connection closed" in error_str or
            "name or service not known" in error_str or
            "could not resolve" in error_str or
            "no route to host" in error_str or
            "unknown error" in error_str or
            "temporary failure" in error_str or
            "bad owner or permissions" in error_str,
            f"Unexpected error: {ctx.exception}"
        )

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_shell_error_bad_port(self, _, password):
        sshcp = Sshcp(host=self.host, port=6666, user=self.user, password=password)
        with self.assertRaises(SshcpError) as ctx:
            sshcp.shell("cd {}; pwd".format(self.local_dir))
        error_str = str(ctx.exception).lower()
        self.assertTrue(
            "connection refused" in error_str or
            "connection closed" in error_str or
            "connection timed out" in error_str or
            "no route to host" in error_str or
            "unknown error" in error_str or
            "port" in error_str,
            f"Unexpected error: {ctx.exception}"
        )

    @parameterized.expand(_PARAMS)
    @requires_live_ssh
    def test_shell_error_bad_command(self, _, password):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=password)
        with self.assertRaises(SshcpError) as ctx:
            sshcp.shell("./some_bad_command.sh".format(self.local_dir))
        self.assertTrue("./some_bad_command.sh" in str(ctx.exception))

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_run_command_shell_not_found_before_password_prompt_is_remapped(self, mock_spawn_process):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=_PASSWORD)

        spawn = MagicMock()
        spawn.expect.return_value = 1
        spawn.before = b"bash: /bin/bash: No such file or directory"
        spawn.after = b""
        spawn.exitstatus = 1
        mock_spawn_process.return_value = (spawn, False)

        with self.assertRaises(SshcpError) as ctx:
            sshcp._Sshcp__run_command(
                command="ssh",
                flags=["-p", str(self.port)],
                args=[sshcp._Sshcp__remote_address(), "echo hi"]
            )

        error_message = str(ctx.exception)
        self.assertIn("Remote user's shell not found", error_message)
        self.assertIn("bash: /bin/bash: No such file or directory", error_message)
        self.assertIn("sudo chsh -s /bin/sh {}".format(self.user), error_message)
        spawn.sendline.assert_not_called()

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_run_command_shell_not_found_exit_status_respects_detection_gate(self, mock_spawn_process):
        for shell_detection_in_progress, expected_message in (
            (
                False,
                "Remote user's shell not found (login shell not found and no common shells could be detected)"
            ),
            (
                True,
                "bash: /bin/bash: No such file or directory"
            )
        ):
            with self.subTest(shell_detection_in_progress=shell_detection_in_progress):
                sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
                sshcp._Sshcp__shell_detection_in_progress = shell_detection_in_progress

                spawn = MagicMock()
                spawn.expect.return_value = 0
                spawn.before = b"bash: /bin/bash: No such file or directory"
                spawn.after = b""
                spawn.exitstatus = 1
                mock_spawn_process.return_value = (spawn, False)

                with self.assertRaises(SshcpError) as ctx:
                    sshcp._Sshcp__run_command(
                        command="ssh",
                        flags=["-p", str(self.port)],
                        args=[sshcp._Sshcp__remote_address(), "echo hi"]
                    )

                error_message = str(ctx.exception)
                self.assertIn(expected_message, error_message)
                if shell_detection_in_progress:
                    self.assertEqual("bash: /bin/bash: No such file or directory", error_message)
                else:
                    self.assertIn("sudo chsh -s /bin/sh {}".format(self.user), error_message)
