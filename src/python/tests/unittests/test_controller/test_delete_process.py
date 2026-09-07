import os
import posixpath
import tempfile
import unittest
import logging
from unittest.mock import MagicMock, patch

from controller.delete.delete_process import DeleteLocalProcess, DeleteRemoteProcess
from common import escape_remote_path_for_shell, MultiprocessingLogger
from ssh import SshcpError


class TestDeleteProcessSpawn(unittest.TestCase):
    def test_real_local_delete_spawn_with_log_transport(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "delete-me")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("test")
            mp_logger = MultiprocessingLogger(logging.getLogger("delete-spawn-boundary"))
            process = DeleteLocalProcess(temp_dir, "delete-me")
            process.set_mp_log_queue(mp_logger.queue, mp_logger.log_level)
            mp_logger.start()
            try:
                process.start()
                process.join(timeout=5)
                self.assertFalse(process.is_alive())
                self.assertEqual(0, process.exitcode)
                self.assertFalse(os.path.exists(target))
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join()
                process.close_queues()
                mp_logger.stop()

    def test_explicit_artifact_cleanup_removes_map_only_without_primary_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = os.path.join(temp_dir, "sample-file.lftp-pget-status")
            with open(status_path, "w", encoding="utf-8") as handle:
                handle.write("malformed map")
            process = DeleteLocalProcess(
                temp_dir,
                "sample-file",
                artifact_paths=(status_path,),
                artifact_root=temp_dir,
                delete_primary=False,
            )
            process.logger = MagicMock()

            process.run_once()

            self.assertFalse(os.path.lexists(status_path))


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
    @patch("controller.delete.delete_process.os.unlink", side_effect=FileNotFoundError)
    @patch("controller.delete.delete_process.os.path.isfile", return_value=True)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_propagates_target_disappearing_during_delete(self, exists, isfile, unlink):
        process = DeleteLocalProcess(local_path="/local", file_name="gone")
        process.logger = MagicMock()

        with self.assertRaises(FileNotFoundError):
            process.run_once()

        unlink.assert_called_once_with(os.path.join("/local", "gone"))
        process.logger.exception.assert_called_once()

    @patch("controller.delete.delete_process.shutil.rmtree", side_effect=FileNotFoundError("child vanished"))
    @patch("controller.delete.delete_process.os.path.isdir", return_value=True)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_propagates_descendant_race_when_directory_remains(self, exists, isdir, rmtree):
        process = DeleteLocalProcess(local_path="/local", file_name="directory", allow_recursive=True)
        process.logger = MagicMock()

        with self.assertRaises(FileNotFoundError):
            process.run_once()

        rmtree.assert_called_once_with(os.path.join("/local", "directory"))
        process.logger.exception.assert_called_once()

    @patch("controller.delete.delete_process.shutil.rmtree")
    @patch("controller.delete.delete_process.os.path.isdir", return_value=True)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_deletes_directory_target(self, exists, isdir, rmtree):
        process = DeleteLocalProcess(local_path="/local", file_name="dir", allow_recursive=True)
        process.logger = MagicMock()

        process.run_once()

        rmtree.assert_called_once_with(os.path.join("/local", "dir"))

    @patch("controller.delete.delete_process.shutil.rmtree")
    @patch("controller.delete.delete_process.os.path.isfile", return_value=False)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_rejects_directory_substitution_without_recursive_fallback(self, exists, isfile, rmtree):
        process = DeleteLocalProcess(local_path="/local", file_name="changed")
        process.logger = MagicMock()

        with self.assertRaises(OSError):
            process.run_once()

        rmtree.assert_not_called()

    def test_run_once_preserves_substituted_directory_contents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "changed")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("regular target")
            process = DeleteLocalProcess(local_path=temp_dir, file_name="changed")
            process.logger = MagicMock()

            os.unlink(target)
            os.mkdir(target)
            sentinel = os.path.join(target, "sentinel.bin")
            with open(sentinel, "w", encoding="utf-8") as handle:
                handle.write("must survive")

            with self.assertRaises(OSError):
                process.run_once()

            self.assertTrue(os.path.isdir(target))
            self.assertTrue(os.path.isfile(sentinel))
            with open(sentinel, encoding="utf-8") as handle:
                self.assertEqual("must survive", handle.read())

    def test_run_once_rejects_regular_file_symlink_substitution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "target")
            substitute = os.path.join(temp_dir, "changed")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("must survive")
            try:
                os.symlink(target, substitute)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")

            process = DeleteLocalProcess(local_path=temp_dir, file_name="changed")
            process.logger = MagicMock()

            with self.assertRaises(OSError):
                process.run_once()

            self.assertTrue(os.path.islink(substitute))
            self.assertTrue(os.path.isfile(target))
            with open(target, encoding="utf-8") as handle:
                self.assertEqual("must survive", handle.read())

    @patch("controller.delete.delete_process.shutil.rmtree")
    @patch("controller.delete.delete_process.os.unlink")
    @patch("controller.delete.delete_process.os.path.isfile", return_value=True)
    @patch("controller.delete.delete_process.os.path.islink", return_value=True)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_rejects_symlink_before_any_delete_primitive(
            self, exists, islink, isfile, unlink, rmtree):
        process = DeleteLocalProcess(local_path="/local", file_name="changed")
        process.logger = MagicMock()

        with self.assertRaises(OSError):
            process.run_once()

        unlink.assert_not_called()
        rmtree.assert_not_called()

    @patch("controller.delete.delete_process.shutil.rmtree")
    @patch("controller.delete.delete_process.os.unlink", side_effect=PermissionError("denied"))
    @patch("controller.delete.delete_process.os.path.isfile", return_value=True)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_propagates_unlink_failure_without_recursive_fallback(
            self, exists, isfile, unlink, rmtree):
        process = DeleteLocalProcess(local_path="/local", file_name="protected")
        process.logger = MagicMock()

        with self.assertRaises(PermissionError):
            process.run_once()

        unlink.assert_called_once_with(os.path.join("/local", "protected"))
        rmtree.assert_not_called()

    @patch("controller.delete.delete_process.shutil.rmtree")
    @patch("controller.delete.delete_process.os.path.isdir", return_value=False)
    @patch("controller.delete.delete_process.os.path.exists", return_value=True)
    def test_run_once_rejects_file_substitution_for_recursive_target(self, exists, isdir, rmtree):
        process = DeleteLocalProcess(local_path="/local", file_name="changed", allow_recursive=True)
        process.logger = MagicMock()

        with self.assertRaises(OSError):
            process.run_once()

        rmtree.assert_not_called()

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

            with self.assertRaises(ValueError):
                process.run_once()

            self.assertTrue(os.path.isfile(outside_file))
            self.assertTrue(os.path.isdir(local_root))

    def test_run_once_blocks_local_base_directory_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            process = DeleteLocalProcess(local_path=temp_dir, file_name=".")
            process.logger = MagicMock()

            with self.assertRaises(ValueError):
                process.run_once()

            self.assertTrue(os.path.isdir(temp_dir))
