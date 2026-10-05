import os
import posixpath
import shutil
import tempfile
import unittest
import logging
import multiprocessing
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
    def test_dual_root_directory_delete_prevalidates_then_removes_only_selected_trees(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            final_target = os.path.join(final_root, "selected")
            staging_target = os.path.join(staging_root, "selected")
            os.makedirs(final_target)
            os.makedirs(staging_target)
            with open(os.path.join(final_target, "final.bin"), "wb") as handle:
                handle.write(b"final")
            with open(os.path.join(staging_target, "partial.bin"), "wb") as handle:
                handle.write(b"partial")
            os.makedirs(os.path.join(final_root, "sibling"))
            os.makedirs(os.path.join(staging_root, "sibling"))
            result_queue = multiprocessing.Queue(maxsize=1)
            process = DeleteLocalProcess(
                final_root, "selected", allow_recursive=True,
                additional_targets=((staging_root, "selected", True),),
                result_queue=result_queue,
            )
            process.logger = MagicMock()

            try:
                process.run_once()
                self.assertFalse(os.path.lexists(final_target))
                self.assertFalse(os.path.lexists(staging_target))
                self.assertTrue(os.path.isdir(os.path.join(final_root, "sibling")))
                self.assertTrue(os.path.isdir(os.path.join(staging_root, "sibling")))
                self.assertEqual(
                    {"planned": 2, "removed": 2, "known": True, "partial": False},
                    result_queue.get(timeout=1),
                )
            finally:
                result_queue.close()
                result_queue.join_thread()

    def test_dual_root_preflight_rejects_second_symlink_before_first_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            os.makedirs(final_root)
            os.makedirs(staging_root)
            final_target = os.path.join(final_root, "selected.bin")
            staging_target = os.path.join(staging_root, "selected.bin")
            with open(final_target, "wb") as handle:
                handle.write(b"must remain")
            staging_sentinel = os.path.join(staging_root, "sentinel.bin")
            with open(staging_sentinel, "wb") as handle:
                handle.write(b"outside")
            try:
                os.symlink(staging_sentinel, staging_target)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")
            result_queue = multiprocessing.Queue(maxsize=1)
            process = DeleteLocalProcess(
                final_root, "selected.bin",
                additional_targets=((staging_root, "selected.bin", False),),
                result_queue=result_queue,
            )
            process.logger = MagicMock()

            try:
                with self.assertRaises(OSError):
                    process.run_once()
                self.assertTrue(os.path.isfile(final_target))
                self.assertTrue(os.path.islink(staging_target))
                self.assertEqual(
                    {"planned": 2, "removed": 0, "known": True, "partial": False},
                    result_queue.get(timeout=1),
                )
            finally:
                result_queue.close()
                result_queue.join_thread()

    def test_dual_root_second_unlink_failure_reports_partial_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            os.makedirs(final_root)
            os.makedirs(staging_root)
            final_target = os.path.join(final_root, "selected.bin")
            staging_target = os.path.join(staging_root, "selected.bin")
            with open(final_target, "wb") as handle:
                handle.write(b"final")
            with open(staging_target, "wb") as handle:
                handle.write(b"staging")
            result_queue = multiprocessing.Queue(maxsize=1)
            process = DeleteLocalProcess(
                final_root, "selected.bin",
                additional_targets=((staging_root, "selected.bin", False),),
                result_queue=result_queue,
            )
            process.logger = MagicMock()
            real_unlink = os.unlink

            def fail_second(path):
                if path == final_target:
                    real_unlink(path)
                    return
                raise PermissionError("blocked second removal")

            try:
                with patch("controller.delete.delete_process.os.unlink", side_effect=fail_second):
                    with self.assertRaises(PermissionError):
                        process.run_once()
                self.assertFalse(os.path.exists(final_target))
                self.assertTrue(os.path.exists(staging_target))
                self.assertEqual(
                    {"planned": 2, "removed": 1, "known": True, "partial": True},
                    result_queue.get(timeout=1),
                )
            finally:
                result_queue.close()
                result_queue.join_thread()

    def test_dual_root_rechecks_second_target_after_first_directory_removal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            final_target = os.path.join(final_root, "selected")
            staging_target = os.path.join(staging_root, "selected")
            outside = os.path.join(temp_dir, "outside")
            os.makedirs(final_target)
            os.makedirs(staging_target)
            os.makedirs(outside)
            probe_link = os.path.join(temp_dir, "symlink-probe")
            try:
                os.symlink(outside, probe_link, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")
            os.unlink(probe_link)
            result_queue = multiprocessing.Queue(maxsize=1)
            process = DeleteLocalProcess(
                final_root, "selected", allow_recursive=True,
                additional_targets=((staging_root, "selected", True),),
                result_queue=result_queue,
            )
            process.logger = MagicMock()
            real_rmtree = shutil.rmtree

            def mutate_second_target(path):
                real_rmtree(path)
                if path == final_target:
                    os.rmdir(staging_target)
                    os.symlink(outside, staging_target, target_is_directory=True)

            try:
                with patch("controller.delete.delete_process.shutil.rmtree", side_effect=mutate_second_target):
                    with self.assertRaises((OSError, ValueError)):
                        process.run_once()
                self.assertFalse(os.path.exists(final_target))
                self.assertTrue(os.path.islink(staging_target))
                self.assertEqual(
                    {"planned": 2, "removed": 1, "known": True, "partial": True},
                    result_queue.get(timeout=1),
                )
            finally:
                result_queue.close()
                result_queue.join_thread()

    def test_dual_root_rejects_in_root_parent_symlink_swap_before_second_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            final_parent = os.path.join(final_root, "nested")
            staging_parent = os.path.join(staging_root, "nested")
            sibling_parent = os.path.join(staging_root, "sibling")
            os.makedirs(final_parent)
            os.makedirs(staging_parent)
            os.makedirs(sibling_parent)
            final_target = os.path.join(final_parent, "selected.bin")
            staging_target = os.path.join(staging_parent, "selected.bin")
            sibling_target = os.path.join(sibling_parent, "selected.bin")
            with open(final_target, "wb") as handle:
                handle.write(b"first selected location")
            with open(staging_target, "wb") as handle:
                handle.write(b"second selected location")
            with open(sibling_target, "wb") as handle:
                handle.write(b"unrelated in-root sibling")
            probe_link = os.path.join(temp_dir, "symlink-probe")
            try:
                os.symlink(sibling_parent, probe_link, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")
            os.unlink(probe_link)
            result_queue = multiprocessing.Queue(maxsize=1)
            process = DeleteLocalProcess(
                final_root, os.path.join("nested", "selected.bin"),
                additional_targets=((staging_root, os.path.join("nested", "selected.bin"), False),),
                result_queue=result_queue,
            )
            process.logger = MagicMock()
            real_unlink = os.unlink

            def swap_second_parent(path):
                real_unlink(path)
                if path == final_target:
                    real_unlink(staging_target)
                    os.rmdir(staging_parent)
                    os.symlink(sibling_parent, staging_parent, target_is_directory=True)

            try:
                with patch("controller.delete.delete_process.os.unlink", side_effect=swap_second_parent):
                    with self.assertRaises(OSError):
                        process.run_once()
                self.assertFalse(os.path.exists(final_target))
                self.assertTrue(os.path.islink(staging_parent))
                self.assertTrue(os.path.isfile(sibling_target))
                self.assertEqual(
                    {"planned": 2, "removed": 1, "known": True, "partial": True},
                    result_queue.get(timeout=1),
                )
            finally:
                result_queue.close()
                result_queue.join_thread()

    def test_dual_root_first_directory_failure_reports_unknown_partial_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            final_target = os.path.join(final_root, "selected")
            staging_target = os.path.join(staging_root, "selected")
            os.makedirs(final_target)
            os.makedirs(staging_target)
            partial_path = os.path.join(final_target, "partially-removed.bin")
            with open(partial_path, "wb") as handle:
                handle.write(b"may be removed before failure")
            result_queue = multiprocessing.Queue(maxsize=1)
            process = DeleteLocalProcess(
                final_root, "selected", allow_recursive=True,
                additional_targets=((staging_root, "selected", True),),
                result_queue=result_queue,
            )
            process.logger = MagicMock()

            def partially_remove_then_fail(path):
                if path == final_target:
                    os.unlink(partial_path)
                    raise PermissionError("failed after an uncounted partial mutation")
                return shutil.rmtree(path)

            try:
                with patch("controller.delete.delete_process.shutil.rmtree", side_effect=partially_remove_then_fail):
                    with self.assertRaises(PermissionError):
                        process.run_once()
                self.assertFalse(os.path.exists(partial_path))
                self.assertTrue(os.path.isdir(staging_target))
                self.assertEqual(
                    {"planned": 2, "removed": 0, "known": True, "partial": None},
                    result_queue.get(timeout=1),
                )
            finally:
                result_queue.close()
                result_queue.join_thread()

    def test_dual_root_revalidates_artifact_before_unlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            outside_root = os.path.join(temp_dir, "outside")
            os.makedirs(final_root)
            os.makedirs(staging_root)
            os.makedirs(outside_root)
            final_target = os.path.join(final_root, "selected.bin")
            staging_target = os.path.join(staging_root, "selected.bin")
            artifact_path = os.path.join(staging_root, "selected.bin.lftp-pget-status")
            outside_target = os.path.join(outside_root, "sentinel.bin")
            for path, content in (
                    (final_target, b"final"), (staging_target, b"staging"),
                    (artifact_path, b"metadata"), (outside_target, b"must remain")):
                with open(path, "wb") as handle:
                    handle.write(content)
            probe_link = os.path.join(temp_dir, "symlink-probe")
            try:
                os.symlink(outside_target, probe_link)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")
            os.unlink(probe_link)
            result_queue = multiprocessing.Queue(maxsize=1)
            process = DeleteLocalProcess(
                final_root, "selected.bin",
                artifact_paths=(artifact_path,), artifact_root=staging_root,
                additional_targets=((staging_root, "selected.bin", False),),
                result_queue=result_queue,
            )
            process.logger = MagicMock()
            real_unlink = os.unlink

            def swap_artifact_after_primary(path):
                real_unlink(path)
                if path == final_target:
                    real_unlink(artifact_path)
                    os.symlink(outside_target, artifact_path)

            try:
                with patch("controller.delete.delete_process.os.unlink", side_effect=swap_artifact_after_primary):
                    with self.assertRaises(OSError):
                        process.run_once()
                self.assertFalse(os.path.exists(final_target))
                self.assertFalse(os.path.exists(staging_target))
                self.assertTrue(os.path.islink(artifact_path))
                self.assertTrue(os.path.isfile(outside_target))
                self.assertEqual(
                    {"planned": 2, "removed": 2, "known": True, "partial": True},
                    result_queue.get(timeout=1),
                )
            finally:
                result_queue.close()
                result_queue.join_thread()

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
