import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from controller.scan import MultiPathActiveScanner


class TestMultiPathActiveScanner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_multi_path_active_scanner_")
        self.movies_dir = os.path.join(self.temp_dir, "movies")
        self.tv_dir = os.path.join(self.temp_dir, "tv")
        os.mkdir(self.movies_dir)
        os.mkdir(self.tv_dir)
        with open(os.path.join(self.movies_dir, "dup"), "wb") as handle:
            handle.write(b"movies")
        with open(os.path.join(self.tv_dir, "dup"), "wb") as handle:
            handle.write(b"tv")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_scan_routes_duplicate_names_to_matching_path_pair(self):
        scanner = MultiPathActiveScanner({
            "movies": self.movies_dir,
            "tv": self.tv_dir,
        })
        scanner._MultiPathActiveScanner__active_files = [
            ("dup", "movies", "Movies"),
            ("dup", "tv", "TV"),
        ]

        files = scanner.scan()

        self.assertEqual(2, len(files))
        self.assertEqual(
            {("dup", "movies", "Movies", 6), ("dup", "tv", "TV", 2)},
            {(file.name, file.path_pair_id, file.path_pair_name, file.size) for file in files}
        )

    def test_scan_uses_single_scanner_fallback_for_missing_path_pair(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir})
        scanner._MultiPathActiveScanner__active_files = [("dup", None, None)]

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual("dup", files[0].name)

    def test_scan_uses_temp_file_when_final_active_path_is_missing(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir}, use_temp_file=True)
        scanner._MultiPathActiveScanner__active_files = [("download.zip", "movies", "Movies")]

        temp_path = os.path.join(self.movies_dir, "download.zip.lftp")
        with open(temp_path, "wb") as handle:
            handle.write(b"temp")

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual("download.zip", files[0].name)
        self.assertEqual("movies", files[0].path_pair_id)
        self.assertEqual("Movies", files[0].path_pair_name)

    def test_scan_ignores_status_only_partial_when_temp_file_missing(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir}, use_temp_file=True)
        scanner._MultiPathActiveScanner__active_files = [("download.zip", "movies", "Movies")]
        scanner.logger = MagicMock()

        with open(os.path.join(self.movies_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=0\n0.limit=4\n")

        files = scanner.scan()

        self.assertEqual([], files)
        scanner.logger.warning.assert_not_called()
