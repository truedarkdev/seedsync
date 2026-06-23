# Copyright 2017, Inderpreet Singh, All rights reserved.

import shlex
import unittest
from unittest.mock import call, patch

from ssh import Sshcp, SshcpError


class FakeFailedProcess:
    def __init__(self, before: bytes, exitstatus: int = 1):
        self.before = before
        self.after = b""
        self.exitstatus = exitstatus

    def expect(self, *_args, **_kwargs):
        return 0

    def sendline(self, *_args):
        pass

    def close(self):
        pass


class TestShellDetection(unittest.TestCase):
    def setUp(self):
        self.sshcp = Sshcp(host="testhost", port=22, user="testuser", password="testpass")

    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_detect_shell_returns_detected_shell_and_caches_it(self, mock_run_command):
        mock_run_command.return_value = b"__shell_path__/usr/bin/bash__end__"

        self.assertEqual("/usr/bin/bash", self.sshcp.detect_shell())
        self.assertEqual("/usr/bin/bash", self.sshcp.detect_shell())
        self.assertEqual(1, mock_run_command.call_count)
        mock_run_command.assert_called_once()
        self.assertEqual(
            {
                "command": "ssh",
                "flags": ["-p", "22"],
                "args": [
                    "testuser@testhost",
                    "echo __shell_path__$(which bash 2>/dev/null || which sh 2>/dev/null || echo unknown)__end__"
                ]
            },
            mock_run_command.call_args.kwargs
        )

    @patch.object(Sshcp, "_Sshcp__check_remote_shells_via_sftp")
    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_detect_shell_uses_sftp_candidate_for_ambiguous_probe_output(self, mock_run_command, mock_shells):
        mock_run_command.return_value = b"__shell_path__unknown__end__"
        mock_shells.return_value = ["/usr/bin/sh"]

        self.assertEqual("/usr/bin/sh", self.sshcp.detect_shell())
        self.assertEqual("/usr/bin/sh", self.sshcp.detect_shell())
        self.assertEqual(1, mock_run_command.call_count)
        mock_shells.assert_called_once()

    @patch.object(Sshcp, "_Sshcp__check_remote_shells_via_sftp")
    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_detect_shell_does_not_cache_unverified_default_shell(self, mock_run_command, mock_shells):
        mock_run_command.side_effect = [
            b"__shell_path__unknown__end__",
            b"__shell_path__/usr/bin/bash__end__",
        ]
        mock_shells.return_value = []

        with self.assertRaises(SshcpError) as ctx:
            self.sshcp.detect_shell()

        self.assertIn("Unable to detect remote shell", str(ctx.exception))
        self.assertEqual("/usr/bin/bash", self.sshcp.detect_shell())
        self.assertEqual(2, mock_run_command.call_count)

    @patch.object(Sshcp, "_Sshcp__check_remote_shells_via_sftp")
    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_detect_shell_reports_available_shells_on_missing_login_shell(self, mock_run_command, mock_shells):
        mock_run_command.side_effect = SshcpError("bash: /bin/bash: No such file or directory")
        mock_shells.return_value = ["/usr/bin/bash", "/bin/sh"]

        with self.assertRaises(SshcpError) as ctx:
            self.sshcp.detect_shell()

        error_message = str(ctx.exception)
        self.assertIn("Remote user's shell not found", error_message)
        self.assertIn("bash: /bin/bash: No such file or directory", error_message)
        self.assertIn("/usr/bin/bash", error_message)
        self.assertIn("sudo chsh -s /usr/bin/bash testuser", error_message)

    @patch.object(Sshcp, "_Sshcp__check_remote_shells_via_sftp")
    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_detect_shell_reports_missing_shell_without_alternatives(self, mock_run_command, mock_shells):
        mock_run_command.side_effect = SshcpError("bash: /bin/bash: No such file or directory")
        mock_shells.return_value = []

        with self.assertRaises(SshcpError) as ctx:
            self.sshcp.detect_shell()

        error_message = str(ctx.exception)
        self.assertIn("Remote user's shell not found", error_message)
        self.assertIn("bash: /bin/bash: No such file or directory", error_message)
        self.assertIn("no common shells", error_message)
        self.assertIn("sudo chsh -s /bin/sh testuser", error_message)

    @patch.object(Sshcp, "_Sshcp__sftp_stat")
    def test_check_remote_shells_via_sftp_probes_candidates_in_order(self, mock_sftp_stat):
        def side_effect(shell_path):
            if shell_path in ["/usr/bin/bash", "/bin/sh"]:
                return None
            raise SshcpError("File not found: {}".format(shell_path))

        mock_sftp_stat.side_effect = side_effect

        self.assertEqual(
            ["/usr/bin/bash", "/bin/sh"],
            self.sshcp._Sshcp__check_remote_shells_via_sftp()
        )
        self.assertEqual(
            [
                call("/bin/bash"),
                call("/usr/bin/bash"),
                call("/bin/sh"),
                call("/usr/bin/sh"),
            ],
            mock_sftp_stat.call_args_list
        )

    @patch.object(Sshcp, "_Sshcp__sftp_stat")
    def test_check_remote_shells_via_sftp_propagates_transport_failures(self, mock_sftp_stat):
        mock_sftp_stat.side_effect = SshcpError("Permission denied")

        with self.assertRaises(SshcpError) as ctx:
            self.sshcp._Sshcp__check_remote_shells_via_sftp()

        error_message = str(ctx.exception)
        self.assertIn("SFTP shell probe failed", error_message)
        self.assertIn("Permission denied", error_message)
        self.assertNotIn("no common shells", error_message)


class TestShellCommandExecution(unittest.TestCase):
    def setUp(self):
        self.sshcp = Sshcp(host="testhost", port=22, user="testuser", password="testpass")

    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_shell_quotes_detected_shell_and_command(self, mock_run_command):
        self.sshcp._Sshcp__detected_shell = "/bin/bash"

        for command in (
            "ls -la",
            'echo "hi"',
            "don't",
            'a\'b"c',
        ):
            with self.subTest(command=command):
                mock_run_command.reset_mock()
                self.sshcp.shell(command)
                self.assertEqual(
                    {
                        "command": "ssh",
                        "flags": ["-p", "22"],
                        "args": [
                            "testuser@testhost",
                            "/bin/bash -c {}".format(shlex.quote(command))
                        ]
                    },
                    mock_run_command.call_args.kwargs
                )

    def test_shell_rejects_mixed_quotes_before_shell_detection(self):
        with self.assertRaises(ValueError):
            self.sshcp.shell('echo "hi" && printf \'bye\'')

    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_shell_uses_cached_detected_shell_for_command_execution(self, mock_run_command):
        mock_run_command.side_effect = [
            b"__shell_path__/usr/bin/bash__end__",
            b"shell output",
        ]

        self.assertEqual("/usr/bin/bash", self.sshcp.detect_shell())
        self.assertEqual(b"shell output", self.sshcp.shell("cd /tmp && pwd"))

        self.assertEqual(2, mock_run_command.call_count)
        self.assertEqual(
            [
                call(
                    command="ssh",
                    flags=["-p", "22"],
                    args=[
                        "testuser@testhost",
                        "echo __shell_path__$(which bash 2>/dev/null || which sh 2>/dev/null || echo unknown)__end__"
                    ]
                ),
                call(
                    command="ssh",
                    flags=["-p", "22"],
                    args=[
                        "testuser@testhost",
                        "/usr/bin/bash -c 'cd /tmp && pwd'"
                    ]
                ),
            ],
            mock_run_command.call_args_list
        )

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_shell_command_failure_mentioning_shell_path_is_not_remapped(self, mock_spawn_process):
        mock_spawn_process.return_value = (
            FakeFailedProcess(b"ls: /bin/sh: No such file or directory"),
            False
        )

        with self.assertRaises(SshcpError) as ctx:
            self.sshcp.shell("ls /bin/sh")

        error_message = str(ctx.exception)
        self.assertEqual("ls: /bin/sh: No such file or directory", error_message)
        self.assertNotIn("Remote user's shell not found", error_message)
