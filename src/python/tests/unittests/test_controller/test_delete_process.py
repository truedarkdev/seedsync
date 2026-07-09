import os
import posixpath
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from controller.delete.delete_process import DeleteLocalProcess, DeleteRemoteProcess
from common import escape_remote_path_for_shell
from ssh import SshcpError


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

        ssh.shell.assert_called_once_with(
            "rm -rf " + escape_remote_path_for_shell(posixpath.join("/remote", "what's.mkv"))
        )

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

        ssh.shell.assert_called_once_with(
            "rm -rf " + escape_remote_path_for_shell(posixpath.join("/remote", "bad;rm -rf /"))
        )

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_shell_leaves_normal_filename_unquoted(self, sshcp_cls):
        ssh = MagicMock()
        ssh.shell.return_value = b"deleted"
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

        ssh.shell.assert_called_once_with(
            "rm -rf " + escape_remote_path_for_shell(posixpath.join("/remote", "normal.mkv"))
        )
        process.logger.debug.assert_any_call("Deleting remote file: normal.mkv")
        process.logger.debug.assert_any_call("Remote delete output: deleted")
        process.logger.debug.assert_any_call("Successfully deleted remote file: normal.mkv")
        process.logger.info.assert_not_called()

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_shell_expands_tilde_remote_path(self, sshcp_cls):
        ssh = MagicMock()
        sshcp_cls.return_value = ssh
        process = DeleteRemoteProcess(
            remote_address="remote",
            remote_username="user",
            remote_password="pass",
            remote_port=22,
            remote_path="~/remote",
            file_name="normal.mkv"
        )
        process.logger = MagicMock()

        process.run_once()

        ssh.shell.assert_called_once_with(
            "rm -rf " + escape_remote_path_for_shell(
                posixpath.join("~/remote", "normal.mkv"),
                allow_tilde_expansion=True
            )
        )

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_propagates_sshcp_error(self, sshcp_cls):
        ssh = MagicMock()
        ssh.shell.side_effect = SshcpError("boom")
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

        with self.assertRaises(SshcpError):
            process.run_once()

        ssh.shell.assert_called_once_with(
            "rm -rf " + escape_remote_path_for_shell(posixpath.join("/remote", "normal.mkv"))
        )

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_blocks_remote_traversal_filename(self, sshcp_cls):
        ssh = MagicMock()
        sshcp_cls.return_value = ssh
        process = DeleteRemoteProcess(
            remote_address="remote",
            remote_username="user",
            remote_password="pass",
            remote_port=22,
            remote_path="/remote",
            file_name="../escape"
        )
        process.logger = MagicMock()

        process.run_once()

        ssh.shell.assert_not_called()

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_blocks_remote_base_directory_filename(self, sshcp_cls):
        ssh = MagicMock()
        sshcp_cls.return_value = ssh
        process = DeleteRemoteProcess(
            remote_address="remote",
            remote_username="user",
            remote_password="pass",
            remote_port=22,
            remote_path="/remote",
            file_name="."
        )
        process.logger = MagicMock()

        process.run_once()

        ssh.shell.assert_not_called()

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_blocks_remote_absolute_filename(self, sshcp_cls):
        ssh = MagicMock()
        sshcp_cls.return_value = ssh
        process = DeleteRemoteProcess(
            remote_address="remote",
            remote_username="user",
            remote_password="pass",
            remote_port=22,
            remote_path="/remote",
            file_name="/etc/passwd"
        )
        process.logger = MagicMock()

        process.run_once()

        ssh.shell.assert_not_called()

    @patch("controller.delete.delete_process.Sshcp")
    def test_run_once_blocks_remote_null_byte_filename(self, sshcp_cls):
        ssh = MagicMock()
        sshcp_cls.return_value = ssh
        process = DeleteRemoteProcess(
            remote_address="remote",
            remote_username="user",
            remote_password="pass",
            remote_port=22,
            remote_path="/remote",
            file_name="bad\x00name"
        )
        process.logger = MagicMock()

        process.run_once()

        ssh.shell.assert_not_called()


class TestDeleteLocalProcess(unittest.TestCase):
    @patch("controller.delete.delete_process.os.path.lexists", return_value=False)
    @patch("controller.delete.delete_process.os.remove", side_effect=FileNotFoundError)
    @patch("controller.delete.delete_process.os.path.isfile", return_value=True)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_tolerates_target_disappearing_during_delete(self, _, __, remove, lexists):
        process = DeleteLocalProcess(local_path="/local", file_name="gone")
        process.logger = MagicMock()

        process.run_once()

        remove.assert_called_once_with(os.path.join("/local", "gone"))
        lexists.assert_called_once_with(os.path.join("/local", "gone"))
        process.logger.warning.assert_called_once()

    @patch("controller.delete.delete_process.os.path.lexists", return_value=True)
    @patch("controller.delete.delete_process.shutil.rmtree", side_effect=FileNotFoundError("child vanished"))
    @patch("controller.delete.delete_process.os.path.isfile", return_value=False)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_propagates_descendant_race_when_directory_remains(self, _, __, rmtree, lexists):
        process = DeleteLocalProcess(local_path="/local", file_name="directory")
        process.logger = MagicMock()

        with self.assertRaises(FileNotFoundError):
            process.run_once()

        rmtree.assert_called_once_with(os.path.join("/local", "directory"))
        lexists.assert_called_once_with(os.path.join("/local", "directory"))
        process.logger.exception.assert_called_once()

    @patch("controller.delete.delete_process.shutil.rmtree")
    @patch("controller.delete.delete_process.os.path.isfile", return_value=False)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_deletes_directory_target(self, _, __, rmtree):
        process = DeleteLocalProcess(local_path="/local", file_name="dir")
        process.logger = MagicMock()

        process.run_once()

        rmtree.assert_called_once_with(os.path.join("/local", "dir"))

    @patch("controller.delete.delete_process.os.path.exists", return_value=False)
    def test_run_once_raises_for_missing_target(self, exists):
        process = DeleteLocalProcess(local_path="/local", file_name="missing.lftp")
        process.logger = MagicMock()

        with self.assertRaises(FileNotFoundError):
            process.run_once()

        exists.assert_called_once_with(os.path.join("/local", "missing.lftp"))

    def test_run_once_blocks_local_traversal_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = os.path.join(temp_dir, "local")
            os.makedirs(local_root)
            outside_file = os.path.join(temp_dir, "escape.txt")
            with open(outside_file, "w") as f:
                f.write("do not delete")

            process = DeleteLocalProcess(local_path=local_root, file_name="../escape.txt")
            process.logger = MagicMock()

            process.run_once()

            self.assertTrue(os.path.isfile(outside_file))
            self.assertTrue(os.path.isdir(local_root))

    def test_run_once_blocks_local_base_directory_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            process = DeleteLocalProcess(local_path=temp_dir, file_name=".")
            process.logger = MagicMock()

            process.run_once()

            self.assertTrue(os.path.isdir(temp_dir))
