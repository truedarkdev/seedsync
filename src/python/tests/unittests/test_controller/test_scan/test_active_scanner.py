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
        self.addCleanup(scanner.close)
        scanner.set_active_files(["download.zip"])

        with open(os.path.join(self.temp_dir, "download.zip.lftp"), "wb") as handle:
            handle.write(b"temp")

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual("download.zip", files[0].name)
        self.assertEqual(0, files[0].size)

    def test_scan_uses_status_sidecar_for_temp_file_size_when_final_active_path_is_missing(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner.set_active_files(["download.zip"])

        with open(os.path.join(self.temp_dir, "download.zip.lftp"), "wb") as handle:
            handle.write(b"temp")
        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=100\n0.pos=30\n0.limit=100\n")

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual("download.zip", files[0].name)
        self.assertEqual(30, files[0].size)

    def test_scan_ignores_status_only_partial_when_temp_file_missing(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner.set_active_files(["download.zip"])
        scanner.logger = MagicMock()

        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=0\n0.limit=4\n")

        files = scanner.scan()

        self.assertEqual([], files)
        scanner.logger.warning.assert_not_called()

    def test_scan_ignores_malformed_status_only_partial_when_temp_file_missing(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner.set_active_files(["download.zip"])

        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=-2\n0.pos=0\n")

        with self.assertLogs("ActiveScanner", level="DEBUG") as captured:
            files = scanner.scan()

        self.assertEqual([], files)
        self.assertEqual(1, len(captured.output))
        self.assertTrue(captured.output[0].startswith("WARNING:ActiveScanner:"))

    def test_scan_returns_malformed_status_only_file_ids(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner.set_active_files(["download.zip"])
        scanner.logger = MagicMock()

        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=-2\n0.pos=0\n")

        files = scanner.scan()

        self.assertEqual([], files)
        self.assertEqual(["download.zip"], scanner.pop_malformed_status_only_file_ids())
        self.assertEqual([], scanner.pop_malformed_status_only_file_ids())
