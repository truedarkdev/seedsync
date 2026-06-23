import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common import escape_remote_path_for_shell
from controller.validate import ValidateProcess
from model import ModelFile


class TestValidateProcess(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.process = ValidateProcess(
            remote_address="example.com",
            remote_username="user",
            remote_password=None,
            remote_port=22,
            local_path=self.temp_dir.name,
            remote_path="/remote",
            path_pairs_by_id={
                "movies": SimpleNamespace(local_path="/local/movies", remote_path="/remote/movies")
            }
        )

    def test_validate_file_marks_validated_for_matching_checksum(self):
        file = ModelFile("movie.mkv", False)
        file.local_size = 3
        file.remote_size = 3
        local_path = os.path.join(self.temp_dir.name, "movie.mkv")
        with open(local_path, "wb") as handle:
            handle.write(b"abc")

        with patch("controller.validate.validate_process.Sshcp.shell", return_value=(
            b"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  movie.mkv\n"
        )):
            is_valid, error = self.process._ValidateProcess__validate(file)

        self.assertTrue(is_valid)
        self.assertIsNone(error)

    def test_validate_file_marks_corrupt_for_checksum_mismatch(self):
        file = ModelFile("movie.mkv", False)
        file.local_size = 3
        file.remote_size = 3
        local_path = os.path.join(self.temp_dir.name, "movie.mkv")
        with open(local_path, "wb") as handle:
            handle.write(b"abc")

        with patch("controller.validate.validate_process.Sshcp.shell", return_value=(
            b"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff  movie.mkv\n"
        )):
            is_valid, error = self.process._ValidateProcess__validate(file)

        self.assertFalse(is_valid)
        self.assertEqual("Checksum mismatch", error)

    def test_remote_directory_manifest_parses_paths(self):
        with patch.object(
            self.process,
            "_ValidateProcess__run_remote_command",
            side_effect=[
                "folder\nfolder/sub\n",
                "aaaa folder/file one.mkv\nbbbb folder/sub/file2.mkv\n"
            ]
        ):
            dirs, hashes = self.process._ValidateProcess__build_remote_directory_manifest(None, "folder")

        self.assertEqual({"folder", "folder/sub"}, dirs)
        self.assertEqual({
            "folder/file one.mkv": "aaaa",
            "folder/sub/file2.mkv": "bbbb"
        }, hashes)

    def test_remote_file_hash_expands_tilde_and_quotes_spaces(self):
        process = ValidateProcess(
            remote_address="example.com",
            remote_username="user",
            remote_password=None,
            remote_port=22,
            local_path=self.temp_dir.name,
            remote_path="~/downloads",
            path_pairs_by_id={}
        )

        file = ModelFile("movie with spaces.mkv", False)
        file.local_size = 3
        file.remote_size = 3
        local_path = os.path.join(self.temp_dir.name, file.name)
        with open(local_path, "wb") as handle:
            handle.write(b"abc")

        with patch.object(
            process._ValidateProcess__ssh,
            "shell",
            return_value=b"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  movie with spaces.mkv\n"
        ) as mock_shell:
            is_valid, error = process._ValidateProcess__validate(file)

        self.assertTrue(is_valid)
        self.assertIsNone(error)
        mock_shell.assert_called_once_with(
            "cd {} && sha256sum {}".format(
                escape_remote_path_for_shell("~/downloads", allow_tilde_expansion=True),
                escape_remote_path_for_shell(file.name)
            )
        )

    def test_remote_directory_manifest_expands_tilde_and_quotes_root_name(self):
        process = ValidateProcess(
            remote_address="example.com",
            remote_username="user",
            remote_password=None,
            remote_port=22,
            local_path=self.temp_dir.name,
            remote_path="~/downloads",
            path_pairs_by_id={}
        )

        with patch.object(
            process._ValidateProcess__ssh,
            "shell",
            side_effect=[
                b"folder with spaces\nfolder with spaces/sub\n",
                b"aaaa folder with spaces/file one.mkv\n"
            ]
        ) as mock_shell:
            dirs, hashes = process._ValidateProcess__build_remote_directory_manifest(None, "folder with spaces")

        self.assertEqual({"folder with spaces", "folder with spaces/sub"}, dirs)
        self.assertEqual({"folder with spaces/file one.mkv": "aaaa"}, hashes)
        expected_remote_base = escape_remote_path_for_shell("~/downloads", allow_tilde_expansion=True)
        expected_root_name = escape_remote_path_for_shell("folder with spaces")
        self.assertEqual(
            "cd {} && find {} -type d | sort".format(expected_remote_base, expected_root_name),
            mock_shell.call_args_list[0].args[0]
        )
        self.assertEqual(
            "cd {} && find {} -type f -exec sha256sum {{}} \\; | sort".format(expected_remote_base, expected_root_name),
            mock_shell.call_args_list[1].args[0]
        )

    def test_close_queues_releases_owned_queues_and_is_idempotent(self):
        exception_queue = MagicMock()
        command_queue = MagicMock()
        status_queue = MagicMock()

        with patch(
            "controller.validate.validate_process.multiprocessing.Queue",
            side_effect=[exception_queue, command_queue, status_queue],
        ), patch("controller.validate.validate_process.Sshcp") as mock_sshcp:
            process = ValidateProcess(
                remote_address="example.com",
                remote_username="user",
                remote_password=None,
                remote_port=22,
                local_path=self.temp_dir.name,
                remote_path="/remote",
                path_pairs_by_id={}
            )

        mock_sshcp.assert_called_once_with(
            host="example.com",
            port=22,
            user="user",
            password=None
        )
        process.mp_logger = MagicMock()
        process._AppProcess__exception_queue = MagicMock()

        process.close_queues()
        process.close_queues()

        command_queue.close.assert_called_once_with()
        command_queue.join_thread.assert_called_once_with()
        status_queue.close.assert_called_once_with()
        status_queue.join_thread.assert_called_once_with()
        self.assertIsNone(process._ValidateProcess__command_queue)
        self.assertIsNone(process._ValidateProcess__status_result_queue)
        self.assertIsNone(process._AppProcess__exception_queue)
        self.assertIsNone(process._terminate)
        self.assertIsNone(process.mp_logger)
