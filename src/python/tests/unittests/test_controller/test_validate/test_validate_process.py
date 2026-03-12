import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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
