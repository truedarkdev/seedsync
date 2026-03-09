import unittest
from unittest.mock import MagicMock, patch

from controller.delete.delete_process import DeleteLocalProcess, DeleteRemoteProcess


class TestDeleteRemoteProcess(unittest.TestCase):
    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_shell_quotes_single_quotes(self, sshcp_cls):
        ssh = MagicMock()
        sshcp_cls.return_value = ssh
        process = DeleteRemoteProcess(
            remote_address="remote",
            remote_username="user",
            remote_password="pass",
            remote_port=22,
            remote_path="/remote",
            file_name="what's.mkv"
        )
        process.logger = MagicMock()

        process.run_once()

        ssh.shell.assert_called_once_with("rm -rf '/remote/what'\"'\"'s.mkv'")

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_shell_quotes_shell_metacharacters(self, sshcp_cls):
        ssh = MagicMock()
        sshcp_cls.return_value = ssh
        process = DeleteRemoteProcess(
            remote_address="remote",
            remote_username="user",
            remote_password="pass",
            remote_port=22,
            remote_path="/remote",
            file_name="bad;rm -rf /"
        )
        process.logger = MagicMock()

        process.run_once()

        ssh.shell.assert_called_once_with("rm -rf '/remote/bad;rm -rf /'")

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_shell_leaves_normal_filename_unquoted(self, sshcp_cls):
        ssh = MagicMock()
        sshcp_cls.return_value = ssh
        process = DeleteRemoteProcess(
            remote_address="remote",
            remote_username="user",
            remote_password="pass",
            remote_port=22,
            remote_path="/remote",
            file_name="normal.mkv"
        )
        process.logger = MagicMock()

        process.run_once()

        ssh.shell.assert_called_once_with("rm -rf /remote/normal.mkv")


class TestDeleteLocalProcess(unittest.TestCase):
    @patch("controller.delete.delete_process.shutil.rmtree")
    @patch("controller.delete.delete_process.os.path.isfile", return_value=False)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_ignores_directory_delete_errors(self, _, __, rmtree):
        process = DeleteLocalProcess(local_path="/local", file_name="dir")
        process.logger = MagicMock()

        process.run_once()

        rmtree.assert_called_once_with("/local/dir", ignore_errors=True)
