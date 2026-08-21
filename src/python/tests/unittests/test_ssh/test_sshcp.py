# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import os
import pickle
import tempfile
import shutil
import shlex
import filecmp
import logging
import subprocess
import signal
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pexpect
import pytest
from parameterized import parameterized

from tests.utils import TestUtils, requires_live_ssh
from common import overrides
from ssh import Sshcp, SshcpError
from ssh.sshcp import _linux_master_parent_death_preexec
from common.performance_diagnostics import DURATION_REMOTE_SCAN_TRANSPORT_READ


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

    def _control_master(self, password=_PASSWORD, port=None):
        sshcp = Sshcp(host=self.host, port=self.port if port is None else port,
                      user=self.user, password=password)
        # Windows pexpect builds may omit spawn. These tests exercise the
        # Linux-only owner while mocking its child process, so make allocation
        # independent of the host that runs the unit suite.
        with patch("ssh.sshcp.pexpect.spawn", MagicMock(), create=True):
            control_master = sshcp.create_generation_control_master()
        self.assertIsNotNone(control_master)
        assert control_master is not None
        self.addCleanup(shutil.rmtree, control_master.directory, ignore_errors=True)
        sshcp.set_generation_control_master(control_master)
        return sshcp, control_master

    @patch("ssh.sshcp.sys.platform", "linux")
    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_reused_control_master_options_cover_all_transports(self, mock_spawn_process):
        sshcp, control_master = self._control_master()
        control_master.started = True
        scenarios = [
            ("ssh-shell", lambda: sshcp.shell("true"), "command"),
            ("scp-copy", lambda: sshcp.copy(self.local_file, self.remote_file), "command"),
            ("streamed-ssh", lambda: sshcp.shell_stream("true", lambda _chunk: None), "stream"),
            ("sftp-shell-probe", lambda: sshcp._Sshcp__sftp_stat("/bin/sh"), "sftp"),
        ]

        for name, operation, kind in scenarios:
            with self.subTest(name=name):
                spawn = MagicMock()
                spawn.before = spawn.after = b""
                spawn.exitstatus = 0
                spawn.expect.return_value = 0
                if kind == "stream":
                    spawn.read_nonblocking.side_effect = [pexpect.EOF("done")]
                elif kind == "sftp":
                    spawn.expect.side_effect = [0, 0, 0]
                mock_spawn_process.reset_mock()
                mock_spawn_process.return_value = (spawn, False)

                operation()

                self.assertEqual(1, mock_spawn_process.call_count)
                arguments = mock_spawn_process.call_args.args[1]
                self.assertIn("ControlPath={}".format(control_master.path), arguments)
                self.assertIn("ControlMaster=no", arguments)
                self.assertIn("BatchMode=yes", arguments)
                if kind == "sftp":
                    spawn.sendline.assert_any_call("ls /bin/sh")
                    self.assertNotIn(_PASSWORD, [call.args[0] for call in spawn.sendline.call_args_list])
                else:
                    spawn.sendline.assert_not_called()

    @parameterized.expand((
        ("password", _PASSWORD, [0, pexpect.exceptions.TIMEOUT("pending")], "PubkeyAuthentication=no"),
        ("key", None, [pexpect.exceptions.TIMEOUT("pending")], "PasswordAuthentication=no"),
    ))
    @patch("ssh.sshcp.sys.platform", "linux")
    @patch("ssh.sshcp.os.path.exists", side_effect=[False, True])
    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_control_master_starts_with_password_or_key_authentication(
        self, _, password, expect_results, auth_option, mock_spawn_process, _exists
    ):
        sshcp, control_master = self._control_master(password=password)
        spawn = MagicMock()
        spawn.expect.side_effect = expect_results
        mock_spawn_process.return_value = (spawn, False)

        sshcp.start_generation_control_master()

        self.assertTrue(control_master.started)
        self.assertIn(auth_option, mock_spawn_process.call_args.args[1])
        if password is None:
            spawn.sendline.assert_not_called()
        else:
            spawn.sendline.assert_called_once_with(password)

    @patch("ssh.sshcp.sys.platform", "linux")
    @patch("ssh.sshcp.os.path.exists", return_value=False)
    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_control_master_startup_classifies_terminal_events_and_resets_state(
        self, mock_spawn_process, _exists
    ):
        scenarios = [
            ("eof-before-password", [1], b"", b"", "Unknown error"),
            ("prompt-then-eof", [0, 1], b"", b"", "Incorrect password"),
            ("repeated-password-prompt", [0, 0], b"", b"", "Incorrect password"),
            ("host-key-changed", [8], b"REMOTE HOST IDENTIFICATION HAS CHANGED", b"",
             "Remote host key has changed"),
            ("connection-refused", [4], b"ssh: connect to host port 22: Connection refused", b"",
             "Connection refused by server"),
        ]

        for name, expect_results, before, after, error_message in scenarios:
            with self.subTest(name=name):
                sshcp, control_master = self._control_master()
                spawn = MagicMock()
                spawn.expect.side_effect = expect_results
                spawn.before = before
                spawn.after = after
                mock_spawn_process.return_value = (spawn, False)

                with self.assertRaisesRegex(SshcpError, error_message):
                    sshcp.start_generation_control_master()

                spawn.close.assert_called_once_with(force=True)
                self.assertFalse(control_master.started)
                self.assertIsNone(control_master.process)
                mock_spawn_process.reset_mock()

    def test_generation_control_master_is_linux_only_and_uses_parent_death_preexec(self):
        for platform, expected_control_master in (("linux", True), ("win32", False), ("darwin", False)):
            with self.subTest(platform=platform), \
                    patch("ssh.sshcp.sys.platform", platform), \
                    patch("ssh.sshcp.pexpect.spawn", create=True) as spawn_factory, \
                    patch("ssh.sshcp.os.path.exists", return_value=True):
                sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
                control_master = sshcp.create_generation_control_master()
                if not expected_control_master:
                    self.assertIsNone(control_master)
                    spawn_factory.assert_not_called()
                    continue

                self.assertIsNotNone(control_master)
                assert control_master is not None
                self.addCleanup(shutil.rmtree, control_master.directory, ignore_errors=True)
                sshcp.set_generation_control_master(control_master)
                sshcp.start_generation_control_master()
                sshcp._Sshcp__spawn_process("ssh", [])

                self.assertEqual(2, spawn_factory.call_count)
                master_call = spawn_factory.call_args_list[0]
                ordinary_call = spawn_factory.call_args_list[1]
                self.assertIsNotNone(master_call.kwargs.get("preexec_fn"))
                self.assertNotIn("preexec_fn", ordinary_call.kwargs)

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PDEATHSIG is Linux-only")
    def test_linux_master_parent_death_preexec_outcomes(self):
        parent_pid = 1234
        scenarios = [
            ("success", [parent_pid, parent_pid], 0, None, False),
            ("prctl-failure", [parent_pid], 1, OSError, False),
            ("parent-mismatch-before-prctl", [9999, parent_pid], 0, None, True),
            ("parent-mismatch-after-prctl", [parent_pid, 9999], 0, None, True),
        ]
        for name, parent_ids, prctl_result, expected_error, should_kill in scenarios:
            with self.subTest(name=name), \
                    patch("ctypes.CDLL") as cdll, \
                    patch("ssh.sshcp.os.getpid", return_value=5678), \
                    patch("ssh.sshcp.os.getppid", side_effect=parent_ids), \
                    patch("ssh.sshcp.os.kill") as kill:
                cdll.return_value.prctl.return_value = prctl_result
                preexec = _linux_master_parent_death_preexec(parent_pid)
                if expected_error is None:
                    preexec()
                else:
                    with self.assertRaises(expected_error):
                        preexec()

                cdll.return_value.prctl.assert_called_once_with(1, signal.SIGTERM, 0, 0, 0)
                if should_kill:
                    kill.assert_called_once_with(5678, signal.SIGTERM)
                else:
                    kill.assert_not_called()

    @patch("ssh.sshcp.sys.platform", "linux")
    @patch("ssh.sshcp.subprocess.run")
    def test_control_master_close_exits_exact_endpoint_reaps_and_removes_temp_state(self, mock_run):
        sshcp, control_master = self._control_master(port=2222)
        control_master.started = True
        process = MagicMock()
        control_master.process = process

        sshcp.close_generation_control_master()

        mock_run.assert_called_once_with(
            ["ssh", "-S", control_master.path, "-O", "exit", "-p", "2222", self.user + "@" + self.host],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        process.close.assert_called_once_with(force=True)
        process.wait.assert_called_once_with()
        self.assertIsNone(control_master.process)
        self.assertTrue(control_master.closed)
        self.assertFalse(os.path.exists(control_master.directory))

    @patch("ssh.sshcp.sys.platform", "linux")
    def test_control_master_is_removed_by_pickle_round_trip(self):
        sshcp, control_master = self._control_master()

        restored = pickle.loads(pickle.dumps(sshcp))

        self.assertIsNone(restored._Sshcp__control_master)

    @patch("ssh.sshcp.sys.platform", "linux")
    @patch("ssh.sshcp.pexpect.spawn", MagicMock(), create=True)
    @patch("ssh.sshcp._SshcpControlMaster", side_effect=OSError("cannot allocate runtime state"))
    def test_control_master_allocator_oserror_falls_back(self, mock_control_master):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)

        self.assertIsNone(sshcp.create_generation_control_master())
        mock_control_master.assert_called_once_with()

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
        spawn.close.assert_called_once_with()

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_run_command_preserves_timeout_when_cleanup_fails(self, mock_spawn_process):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=_PASSWORD)
        sshcp.logger = MagicMock()
        spawn = MagicMock()
        spawn.expect.side_effect = pexpect.exceptions.TIMEOUT("timed out")
        spawn.before = b"waiting for password"
        spawn.close.side_effect = OSError("cleanup failed")
        mock_spawn_process.return_value = (spawn, False)

        with self.assertRaises(SshcpError) as ctx:
            sshcp._Sshcp__run_command(
                command="ssh",
                flags=["-p", str(self.port)],
                args=[sshcp._Sshcp__remote_address(), "echo hi"]
            )

        self.assertEqual("Timed out", str(ctx.exception))
        spawn.close.assert_called_once_with()
        sshcp.logger.warning.assert_called_once_with(
            "Failed to clean up SSH child process",
            exc_info=True
        )

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_run_command_waits_when_spawn_has_no_close(self, mock_spawn_process):
        diagnostics = MagicMock()
        diagnostics.begin_duration.return_value = "transport-start"
        wait = MagicMock(return_value=0)
        spawn = SimpleNamespace(
            expect=MagicMock(return_value=0),
            before=b"",
            after=b"",
            wait=wait,
        )
        mock_spawn_process.return_value = (spawn, True)
        sshcp = Sshcp(
            host=self.host,
            port=self.port,
            user=self.user,
            password=None,
            performance_diagnostics=diagnostics,
        )

        result = sshcp._Sshcp__run_command(
            command="ssh",
            flags=["-p", str(self.port)],
            args=[sshcp._Sshcp__remote_address(), "echo hi"]
        )

        self.assertEqual(b"", result)
        wait.assert_called_once_with()
        diagnostics.begin_duration.assert_called_once_with(DURATION_REMOTE_SCAN_TRANSPORT_READ)
        diagnostics.finish_duration.assert_called_once_with(
            DURATION_REMOTE_SCAN_TRANSPORT_READ,
            "transport-start",
        )

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_shell_stream_requires_explicit_success_exit_status(self, mock_spawn_process):
        for exitstatus in (None, 7):
            with self.subTest(exitstatus=exitstatus):
                spawn = MagicMock()
                spawn.read_nonblocking.side_effect = [b"partial", pexpect.EOF("eof")]
                spawn.exitstatus = exitstatus
                spawn.before = b"partial"
                spawn.after = b""
                spawn.wait.return_value = None
                mock_spawn_process.return_value = (spawn, True)
                sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
                with self.assertRaises(SshcpError):
                    sshcp.shell_stream("echo stream", lambda chunk: None)

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_shell_stream_can_avoid_retaining_raw_output(self, mock_spawn_process):
        spawn = MagicMock()
        spawn.read_nonblocking.side_effect = [b"large-chunk", pexpect.EOF("eof")]
        spawn.exitstatus = 0
        spawn.before = b""
        spawn.after = b""
        mock_spawn_process.return_value = (spawn, True)
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        chunks = []

        output = sshcp.shell_stream("echo stream", chunks.append, retain_output=False)

        self.assertEqual(b"", output)
        self.assertEqual([b"large-chunk"], chunks)

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_shell_stream_returns_empty_for_chunked_legacy_json_without_retention(self, mock_spawn_process):
        chunks = [b" \n[{\"name\":\"legacy.bin\",", b"\"size\":1,\"is_dir\":false}]\n"]
        spawn = MagicMock()
        spawn.read_nonblocking.side_effect = [*chunks, pexpect.EOF("eof")]
        spawn.exitstatus = 0
        spawn.before = b""
        spawn.after = b""
        mock_spawn_process.return_value = (spawn, True)
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        received = []

        output = sshcp.shell_stream("echo stream", received.append, retain_output=False)

        self.assertEqual(b"", output)
        self.assertEqual(chunks, received)

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_shell_stream_retains_bounded_error_text_without_aggregate_output(self, mock_spawn_process):
        spawn = MagicMock()
        spawn.read_nonblocking.side_effect = [
            b"usage: scanfs: unknown option --stream",
            pexpect.EOF("eof"),
        ]
        spawn.exitstatus = 2
        spawn.before = b""
        spawn.after = b""
        mock_spawn_process.return_value = (spawn, True)
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)

        with self.assertRaises(SshcpError) as context:
            sshcp.shell_stream("scanfs --stream", lambda chunk: None, retain_output=False)

        self.assertIn("unknown option --stream", str(context.exception))

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
        spawn.close.assert_called_once_with()
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

    def test_posix_spawn_uses_bounded_bulk_output_reads(self):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        spawn = MagicMock()

        with patch("ssh.sshcp.pexpect.spawn", return_value=spawn, create=True) as pexpect_spawn:
            created, using_fallback = sshcp._Sshcp__spawn_process("ssh", ["host", "echo hi"])

        self.assertIs(spawn, created)
        self.assertFalse(using_fallback)
        pexpect_spawn.assert_called_once_with(
            "ssh",
            ["host", "echo hi"],
            maxread=64 * 1024,
            searchwindowsize=1024,
        )

    def test_short_hostname_resolver_options_are_child_local(self):
        sshcp = Sshcp(host="shortname", port=self.port, user=self.user, password=None)
        spawn = MagicMock()
        original = os.environ.get("RES_OPTIONS")
        with patch("ssh.sshcp.pexpect.spawn", return_value=spawn, create=True) as pexpect_spawn:
            sshcp._Sshcp__spawn_process("ssh", ["shortname"])
        self.assertEqual(original, os.environ.get("RES_OPTIONS"))
        self.assertEqual("attempts:1 timeout:1", pexpect_spawn.call_args.kwargs["env"]["RES_OPTIONS"])

    def test_bounded_popen_stream_reap_terminates_then_kills_without_sp_wait(self):
        process = MagicMock()
        process.wait.side_effect = [
            subprocess.TimeoutExpired("ssh", 0.25),
            subprocess.TimeoutExpired("ssh", 0.25),
            0,
        ]
        stream = MagicMock()
        stream.proc = process

        self.assertEqual(0, Sshcp._Sshcp__bounded_reap_popen_stream(stream))
        self.assertEqual(3, process.wait.call_count)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        stream.wait.assert_not_called()

    def test_bounded_popen_stream_reap_returns_normal_exit_status(self):
        process = MagicMock()
        process.wait.return_value = 7
        stream = MagicMock()
        stream.proc = process

        self.assertEqual(7, Sshcp._Sshcp__bounded_reap_popen_stream(stream))
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    @patch("ssh.sshcp.time.sleep")
    @patch.object(Sshcp, "_Sshcp__log_timeout")
    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_fallback_empty_stream_read_honors_total_deadline(self, mock_spawn_process, log_timeout, sleep):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        sshcp._Sshcp__TIMEOUT_SECS = 0
        stream = MagicMock()
        stream.read_nonblocking.return_value = b""
        stream.before = b""
        stream.after = b""
        stream.exitstatus = None
        stream.proc.wait.return_value = None
        mock_spawn_process.return_value = (stream, True)

        with self.assertRaisesRegex(SshcpError, "Timed out"):
            sshcp.shell_stream("true", lambda _chunk: None)

        sleep.assert_not_called()
        stream.wait.assert_not_called()
        log_timeout.assert_called_once()

    @patch("ssh.sshcp.time.sleep")
    @patch("ssh.sshcp.time.time", return_value=0.0)
    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_fallback_empty_stream_read_waits_briefly_before_retry(self, mock_spawn_process, _time, sleep):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        stream = MagicMock()
        stream.read_nonblocking.side_effect = [b"", pexpect.EOF("done")]
        stream.before = b""
        stream.after = b""
        stream.exitstatus = 0
        stream.proc.wait.return_value = 0
        mock_spawn_process.return_value = (stream, True)

        self.assertEqual(b"", sshcp.shell_stream("true", lambda _chunk: None))
        sleep.assert_called_once_with(0.01)

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_reused_stream_forces_popen_spawn_only_for_that_stream(self, mock_spawn_process):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        sshcp._Sshcp__control_master = SimpleNamespace(started=True, closed=False, path="/tmp/control")
        stream = MagicMock()
        stream.read_nonblocking.side_effect = [pexpect.EOF("done")]
        stream.before = b""
        stream.after = b""
        stream.exitstatus = 0
        stream.proc.wait.return_value = 0
        mock_spawn_process.return_value = (stream, True)

        original_command = "printf before-error; exit 7"
        self.assertEqual(b"", sshcp.shell_stream(original_command, lambda _chunk: None))
        self.assertTrue(mock_spawn_process.call_args.kwargs["force_popen_spawn"])
        remote_command = mock_spawn_process.call_args.args[1][-1]
        self.assertIn("exec 3<&0", remote_command)
        self.assertIn("setsid sh -c {}".format(shlex.quote(original_command)), remote_command)
        self.assertNotIn("exec " + original_command, remote_command)
        self.assertIn("read -r _ <&3", remote_command)
        self.assertIn("kill -TERM -$child", remote_command)
        self.assertNotIn("kill -TERM --", remote_command)
        stream.proc.wait.assert_called_once_with(timeout=0.25)

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_nonreused_stream_keeps_default_spawn_selection(self, mock_spawn_process):
        sshcp = Sshcp(host=self.host, port=self.port, user=self.user, password=None)
        stream = MagicMock()
        stream.read_nonblocking.side_effect = [pexpect.EOF("done")]
        stream.before = b""
        stream.after = b""
        stream.exitstatus = 0
        mock_spawn_process.return_value = (stream, False)

        self.assertEqual(b"", sshcp.shell_stream("true", lambda _chunk: None))
        self.assertFalse(mock_spawn_process.call_args.kwargs["force_popen_spawn"])

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
