import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from controller.scan import ActiveScanner


class TestActiveScanner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_active_scanner_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_scan_uses_temp_file_when_final_active_path_is_missing(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        scanner.set_active_files(["download.zip"])

        with open(os.path.join(self.temp_dir, "download.zip.lftp"), "wb") as handle:
            handle.write(b"temp")

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual("download.zip", files[0].name)

    def test_scan_ignores_status_only_partial_when_temp_file_missing(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        scanner.set_active_files(["download.zip"])
        scanner.logger = MagicMock()

        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=0\n0.limit=4\n")

        files = scanner.scan()

        self.assertEqual([], files)
        scanner.logger.warning.assert_not_called()
