import os
import shutil
import tempfile
import unittest

from controller.scan import LocalScanner


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
