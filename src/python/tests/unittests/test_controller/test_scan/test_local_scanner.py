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

    def test_scan_prefers_final_local_entry_over_staging_duplicate(self):
        staging_dir = os.path.join(self.temp_dir, "incomplete")
        os.mkdir(staging_dir)
        with open(os.path.join(self.temp_dir, "movie.mkv"), "w") as handle:
            handle.write("final")
        with open(os.path.join(staging_dir, "movie.mkv"), "w") as handle:
            handle.write("partial")

        scanner = LocalScanner(
            local_path=self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir
        )

        files = scanner.scan()

        self.assertEqual(["movie.mkv"], [system_file.name for system_file in files])
        self.assertEqual(5, files[0].size)
