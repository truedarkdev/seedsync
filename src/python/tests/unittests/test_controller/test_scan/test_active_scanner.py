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

    def test_scan_ignores_base_only_status_only_partial_when_temp_file_missing(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner.set_active_files(["download.zip"])
        scanner.logger = MagicMock()

        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=2\n")

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

    def test_scan_rejects_crlf_padded_oversize_status_only_partial(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner.set_active_files(["download.zip"])
        scanner.logger = MagicMock()

        status = "size=4\r\n0.pos=0\r\n0.limit=4\r\n" + ("\r\n" * 32760)
        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "wb") as handle:
            handle.write(status.encode("utf-8"))

        self.assertEqual([], scanner.scan())
        scanner.logger.warning.assert_called_once()
        self.assertEqual(["download.zip"], scanner.pop_malformed_status_only_file_ids())

    def test_status_only_sidecar_breadcrumb_uses_fixed_class_and_role(self):
        scanner = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner.set_active_files(["download.zip"])
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner.set_breadcrumb_trace(trace)

        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=0\n0.limit=4\n")

        self.assertEqual([], scanner.scan())
        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual(1, len(events))
        details = events[0].args[2]
        self.assertEqual("valid", details["classification"])
        self.assertTrue(details["status_only"])
        self.assertEqual("known", details["parser_coverage"])
        self.assertEqual("active", details["scan_role"])
        self.assertNotIn(self.temp_dir, repr(events[0]))

    def test_temp_scan_and_status_only_scan_share_target_correlation(self):
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True

        normal = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(normal.close)
        normal.set_breadcrumb_trace(trace)
        normal.set_active_files(["download.zip"])
        with open(os.path.join(self.temp_dir, "download.zip.lftp"), "wb") as handle:
            handle.write(b"partial")
        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=0\n0.limit=4\n")
        self.assertEqual(1, len(normal.scan()))

        os.remove(os.path.join(self.temp_dir, "download.zip.lftp"))
        status_only = ActiveScanner(self.temp_dir, use_temp_file=True)
        self.addCleanup(status_only.close)
        status_only.set_breadcrumb_trace(trace)
        status_only.set_active_files(["download.zip"])
        self.assertEqual([], status_only.scan())

        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual(2, len(events))
        self.assertEqual(events[0].kwargs["corr_id"], events[1].kwargs["corr_id"])
