# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import patch

import pytest

from ssh import Sshcp


pytestmark = pytest.mark.timeout(2)


class FakeSftpSpawn:
    def __init__(self):
        self.before = b""
        self.after = b""
        self.sent_lines = []
        self.expect_calls = 0

    def expect(self, *_args, **_kwargs):
        self.expect_calls += 1
        return 0

    def sendline(self, line):
        self.sent_lines.append(line)

    def close(self):
        pass


class TestSshcpRemoteAddress(unittest.TestCase):
    def test_remote_address_includes_configured_user(self):
        sshcp = Sshcp(host="testhost", port=22, user="testuser")
        self.assertEqual("testuser@testhost", sshcp._Sshcp__remote_address())

    def test_remote_address_omits_missing_user(self):
        sshcp = Sshcp(host="testhost", port=22)
        self.assertEqual("testhost", sshcp._Sshcp__remote_address())

    def test_remote_address_rejects_leading_dash_host_without_user(self):
        sshcp = Sshcp(host="-testhost", port=22)
        with self.assertRaises(ValueError):
            sshcp._Sshcp__remote_address()

    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_detect_shell_uses_host_only_without_user(self, mock_run_command):
        sshcp = Sshcp(host="testhost", port=22)
        mock_run_command.return_value = b"__shell_path__/bin/sh__end__"

        self.assertEqual("/bin/sh", sshcp.detect_shell())

        mock_run_command.assert_called_once_with(
            command="ssh",
            flags=["-p", "22"],
            args=[
                "testhost",
                "echo __shell_path__$(which bash 2>/dev/null || which sh 2>/dev/null || echo unknown)__end__"
            ]
        )

    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_copy_uses_host_only_without_user(self, mock_run_command):
        sshcp = Sshcp(host="testhost", port=22)
        mock_run_command.return_value = b""

        sshcp.copy("/tmp/local.txt", "/tmp/remote.txt")

        mock_run_command.assert_called_once_with(
            command="scp",
            flags=["-q", "-P", "22"],
            args=[
                "/tmp/local.txt",
                "testhost:/tmp/remote.txt"
            ]
        )

    @patch.object(Sshcp, "_Sshcp__run_command")
    def test_copy_rejects_leading_dash_host_without_user(self, mock_run_command):
        sshcp = Sshcp(host="-testhost", port=22)

        with self.assertRaises(ValueError):
            sshcp.copy("/tmp/local.txt", "/tmp/remote.txt")

        mock_run_command.assert_not_called()

    @patch.object(Sshcp, "_Sshcp__spawn_process")
    def test_sftp_stat_uses_host_only_without_user(self, mock_spawn_process):
        sshcp = Sshcp(host="testhost", port=22)
        fake_spawn = FakeSftpSpawn()
        mock_spawn_process.return_value = (fake_spawn, False)

        sshcp._Sshcp__sftp_stat("/bin/sh")

        mock_spawn_process.assert_called_once()
        self.assertEqual("sftp", mock_spawn_process.call_args.args[0])
        self.assertEqual("testhost", mock_spawn_process.call_args.args[1][-1])
        self.assertEqual(["ls /bin/sh", "bye"], fake_spawn.sent_lines)
        self.assertEqual(3, fake_spawn.expect_calls)
