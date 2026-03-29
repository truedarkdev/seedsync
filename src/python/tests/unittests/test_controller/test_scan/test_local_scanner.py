import json
import os
import shutil
import tempfile
import unittest

from model import ModelFile
from controller.scan import LocalScanner, ScannerError
from common import Localization


class TestLocalScanner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_local_scanner")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_scan_merges_staging_results_without_exposing_staging_dir(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        with open(os.path.join(self.temp_dir, "complete.mkv"), "w") as handle:
            handle.write("complete")
        with open(os.path.join(staging_dir, "partial.mkv"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = scanner.scan()

        self.assertEqual({"complete.mkv", "partial.mkv"}, {system_file.name for system_file in files})

    def test_scan_prefers_authoritative_final_entry_over_same_name_staging_duplicate(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        with open(os.path.join(self.temp_dir, "movie.mkv"), "w") as handle:
            handle.write("complete")
        with open(os.path.join(staging_dir, "movie.mkv"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = scanner.scan()

        self.assertEqual(["movie.mkv"], [system_file.name for system_file in files])
        self.assertEqual(8, files[0].size)
        self.assertFalse(files[0].is_staging)

    def test_scan_prefers_staging_entry_when_same_name_final_duplicate_is_less_complete(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        with open(os.path.join(self.temp_dir, "movie.mkv"), "w") as handle:
            handle.write("part")
        with open(os.path.join(staging_dir, "movie.mkv"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = scanner.scan()

        self.assertEqual(["movie.mkv"], [system_file.name for system_file in files])
        self.assertEqual(7, files[0].size)
        self.assertTrue(files[0].is_staging)

    def test_scan_prefers_final_entry_when_same_name_duplicate_sizes_are_equal(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        with open(os.path.join(self.temp_dir, "movie.mkv"), "w") as handle:
            handle.write("partial")
        with open(os.path.join(staging_dir, "movie.mkv"), "w") as handle:
            handle.write("staging")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = scanner.scan()

        self.assertEqual(["movie.mkv"], [system_file.name for system_file in files])
        self.assertEqual(7, files[0].size)
        self.assertFalse(files[0].is_staging)

    def test_scan_marks_staging_entries(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        with open(os.path.join(self.temp_dir, "complete.mkv"), "w") as handle:
            handle.write("complete")
        with open(os.path.join(staging_dir, "partial.zip"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertFalse(files["complete.mkv"].is_staging)
        self.assertTrue(files["partial.zip"].is_staging)

    def test_scan_merges_same_name_directory_collision_without_masking_final_tree(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        os.mkdir(os.path.join(self.temp_dir, "series"))
        with open(os.path.join(self.temp_dir, "series", "complete.txt"), "w") as handle:
            handle.write("complete")
        os.mkdir(os.path.join(staging_dir, "series"))
        with open(os.path.join(staging_dir, "series", "partial.txt"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertEqual(["series"], list(files.keys()))
        self.assertTrue(files["series"].is_dir)
        self.assertFalse(files["series"].is_staging)
        self.assertEqual(
            ["complete.txt", "partial.txt"],
            [child.name for child in files["series"].children]
        )
        self.assertFalse(files["series"].children[0].is_staging)
        self.assertTrue(files["series"].children[1].is_staging)

    def test_scan_prefers_final_directory_when_staging_has_same_name_file(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        os.mkdir(os.path.join(self.temp_dir, "series"))
        with open(os.path.join(self.temp_dir, "series", "complete.txt"), "w") as handle:
            handle.write("complete")
        with open(os.path.join(staging_dir, "series"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertTrue(files["series"].is_dir)
        self.assertFalse(files["series"].is_staging)
        self.assertEqual(["complete.txt"], [child.name for child in files["series"].children])

    def test_scan_prefers_final_file_when_staging_has_same_name_directory(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        with open(os.path.join(self.temp_dir, "movie.mkv"), "w") as handle:
            handle.write("complete")
        os.mkdir(os.path.join(staging_dir, "movie.mkv"))
        with open(os.path.join(staging_dir, "movie.mkv", "partial.txt"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertFalse(files["movie.mkv"].is_dir)
        self.assertFalse(files["movie.mkv"].is_staging)
        self.assertEqual(8, files["movie.mkv"].size)

    def test_scan_prefers_final_nested_directory_when_staging_has_same_name_file(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        os.mkdir(os.path.join(self.temp_dir, "series"))
        os.mkdir(os.path.join(self.temp_dir, "series", "extras"))
        with open(os.path.join(self.temp_dir, "series", "extras", "complete.txt"), "w") as handle:
            handle.write("complete")
        os.mkdir(os.path.join(staging_dir, "series"))
        with open(os.path.join(staging_dir, "series", "extras"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertTrue(files["series"].is_dir)
        self.assertFalse(files["series"].is_staging)
        self.assertEqual(["extras"], [child.name for child in files["series"].children])
        self.assertTrue(files["series"].children[0].is_dir)
        self.assertFalse(files["series"].children[0].is_staging)
        self.assertEqual(
            ["complete.txt"],
            [child.name for child in files["series"].children[0].children]
        )

    def test_scan_prefers_final_nested_file_when_staging_has_same_name_directory(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        os.mkdir(os.path.join(self.temp_dir, "series"))
        with open(os.path.join(self.temp_dir, "series", "extras"), "w") as handle:
            handle.write("complete")
        os.mkdir(os.path.join(staging_dir, "series"))
        os.mkdir(os.path.join(staging_dir, "series", "extras"))
        with open(os.path.join(staging_dir, "series", "extras", "partial.txt"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertTrue(files["series"].is_dir)
        self.assertFalse(files["series"].is_staging)
        self.assertEqual(["extras"], [child.name for child in files["series"].children])
        self.assertFalse(files["series"].children[0].is_dir)
        self.assertFalse(files["series"].children[0].is_staging)
        self.assertEqual(8, files["series"].children[0].size)

    def test_scan_rejects_non_absolute_local_path(self):
        scanner = LocalScanner(
            local_path="relative/path",
            use_temp_file=False
        )

        with self.assertRaises(ScannerError) as error:
            scanner.scan()

        self.assertEqual(Localization.Error.LOCAL_SERVER_SCAN, str(error.exception))

    def test_scan_rejects_non_string_local_path(self):
        scanner = LocalScanner(
            local_path=None,
            use_temp_file=False
        )

        with self.assertRaises(ScannerError) as error:
            scanner.scan()

        self.assertEqual(Localization.Error.LOCAL_SERVER_SCAN, str(error.exception))

    def test_scan_suppresses_managed_extract_folder_and_recovers_marker_identity(self):
        managed_dir = os.path.join(self.temp_dir, "movie")
        os.mkdir(managed_dir)
        with open(os.path.join(managed_dir, ".seedsync-extract.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "archive_name": "movie.zip",
                    "archive_file_id": ModelFile.build_file_id("movie.zip", "pair-1"),
                    "path_pair_id": "pair-1",
                    "extracted_at": "2026-01-01T00:00:00",
                },
                handle,
            )
        with open(os.path.join(managed_dir, "episode.mkv"), "w", encoding="utf-8") as handle:
            handle.write("hidden")
        with open(os.path.join(self.temp_dir, "visible.txt"), "w", encoding="utf-8") as handle:
            handle.write("visible")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=None,
            managed_extract_folders_enabled=True,
        )

        files = scanner.scan()
        recovered_ids = scanner.pop_managed_extract_file_ids()

        self.assertEqual(["visible.txt"], [system_file.name for system_file in files])
        self.assertEqual([ModelFile.build_file_id("movie.zip", "pair-1")], recovered_ids)

    def test_scan_does_not_suppress_managed_extract_folder_when_marker_is_corrupt(self):
        managed_dir = os.path.join(self.temp_dir, "movie")
        os.mkdir(managed_dir)
        with open(os.path.join(managed_dir, ".seedsync-extract.json"), "w", encoding="utf-8") as handle:
            handle.write("{not valid json")
        with open(os.path.join(managed_dir, "episode.mkv"), "w", encoding="utf-8") as handle:
            handle.write("visible")
        with open(os.path.join(self.temp_dir, "visible.txt"), "w", encoding="utf-8") as handle:
            handle.write("visible")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=None,
            managed_extract_folders_enabled=True,
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertEqual({"movie", "visible.txt"}, set(files.keys()))
        self.assertEqual(["episode.mkv"], [child.name for child in files["movie"].children])
        self.assertEqual([], scanner.pop_managed_extract_file_ids())

    def test_scan_does_not_trust_inconsistent_managed_extract_marker_identity(self):
        managed_dir = os.path.join(self.temp_dir, "movie")
        os.mkdir(managed_dir)
        with open(os.path.join(managed_dir, ".seedsync-extract.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "archive_name": "movie.zip",
                    "archive_file_id": "wrong-id",
                    "path_pair_id": "pair-1",
                    "extracted_at": "2026-01-01T00:00:00",
                },
                handle,
            )
        with open(os.path.join(managed_dir, "episode.mkv"), "w", encoding="utf-8") as handle:
            handle.write("visible")
        with open(os.path.join(self.temp_dir, "visible.txt"), "w", encoding="utf-8") as handle:
            handle.write("visible")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=None,
            managed_extract_folders_enabled=True,
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertEqual({"movie", "visible.txt"}, set(files.keys()))
        self.assertEqual(["episode.mkv"], [child.name for child in files["movie"].children])
        self.assertEqual([], scanner.pop_managed_extract_file_ids())

    def test_scan_recovers_managed_extract_identity_without_archive_file_id(self):
        managed_dir = os.path.join(self.temp_dir, "movie")
        os.mkdir(managed_dir)
        with open(os.path.join(managed_dir, ".seedsync-extract.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "archive_name": "movie.zip",
                    "path_pair_id": "pair-1",
                    "extracted_at": "2026-01-01T00:00:00",
                },
                handle,
            )
        with open(os.path.join(managed_dir, "episode.mkv"), "w", encoding="utf-8") as handle:
            handle.write("hidden")
        with open(os.path.join(self.temp_dir, "visible.txt"), "w", encoding="utf-8") as handle:
            handle.write("visible")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=None,
            managed_extract_folders_enabled=True,
        )

        files = scanner.scan()
        recovered_ids = scanner.pop_managed_extract_file_ids()

        self.assertEqual(["visible.txt"], [system_file.name for system_file in files])
        self.assertEqual([ModelFile.build_file_id("movie.zip", "pair-1")], recovered_ids)

    def test_scan_ignores_managed_extract_folders_when_disabled(self):
        managed_dir = os.path.join(self.temp_dir, "movie")
        os.mkdir(managed_dir)
        with open(os.path.join(managed_dir, ".seedsync-extract.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "archive_name": "movie.zip",
                    "archive_file_id": ModelFile.build_file_id("movie.zip", "pair-1"),
                    "path_pair_id": "pair-1",
                    "extracted_at": "2026-01-01T00:00:00",
                },
                handle,
            )
        with open(os.path.join(managed_dir, "episode.mkv"), "w", encoding="utf-8") as handle:
            handle.write("visible")
        with open(os.path.join(self.temp_dir, "visible.txt"), "w", encoding="utf-8") as handle:
            handle.write("visible")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=None,
            managed_extract_folders_enabled=False,
        )

        files = {system_file.name: system_file for system_file in scanner.scan()}

        self.assertEqual({"movie", "visible.txt"}, set(files.keys()))
        self.assertEqual({".seedsync-extract.json", "episode.mkv"}, {child.name for child in files["movie"].children})
        self.assertEqual([], scanner.pop_managed_extract_file_ids())
