import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from controller.scan import MultiPathActiveScanner
from model import ModelFile


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
        self.addCleanup(scanner.close)
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

    def test_scan_observes_immediately_set_active_files(self):
        scanner = MultiPathActiveScanner({
            "movies": self.movies_dir,
            "tv": self.tv_dir,
        })
        self.addCleanup(scanner.close)

        scanner.set_active_files([
            ("dup", "movies", "Movies"),
            ("dup", "tv", "TV"),
        ])

        files = scanner.scan()

        self.assertEqual(
            {("dup", "movies", "Movies", 6), ("dup", "tv", "TV", 2)},
            {(file.name, file.path_pair_id, file.path_pair_name, file.size) for file in files}
        )

    def test_scan_uses_newest_queued_active_files(self):
        scanner = MultiPathActiveScanner({
            "movies": self.movies_dir,
            "tv": self.tv_dir,
        })
        self.addCleanup(scanner.close)

        scanner.set_active_files([("dup", "movies", "Movies")])
        scanner.set_active_files([("dup", "tv", "TV")])

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual(("dup", "tv", "TV", 2), (
            files[0].name,
            files[0].path_pair_id,
            files[0].path_pair_name,
            files[0].size,
        ))

    def test_scan_uses_single_scanner_fallback_for_missing_path_pair(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir})
        self.addCleanup(scanner.close)
        scanner._MultiPathActiveScanner__active_files = [("dup", None, None)]

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual("dup", files[0].name)

    def test_scan_uses_temp_file_when_final_active_path_is_missing(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir}, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner._MultiPathActiveScanner__active_files = [("download.zip", "movies", "Movies")]

        temp_path = os.path.join(self.movies_dir, "download.zip.lftp")
        with open(temp_path, "wb") as handle:
            handle.write(b"temp")

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual("download.zip", files[0].name)
        self.assertEqual(0, files[0].size)
        self.assertEqual("movies", files[0].path_pair_id)
        self.assertEqual("Movies", files[0].path_pair_name)

    def test_scan_uses_status_sidecar_for_temp_file_size_when_final_active_path_is_missing(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir}, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner._MultiPathActiveScanner__active_files = [("download.zip", "movies", "Movies")]

        temp_path = os.path.join(self.movies_dir, "download.zip.lftp")
        with open(temp_path, "wb") as handle:
            handle.write(b"temp")
        with open(os.path.join(self.movies_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=100\n0.pos=30\n0.limit=100\n")

        files = scanner.scan()

        self.assertEqual(1, len(files))
        self.assertEqual("download.zip", files[0].name)
        self.assertEqual(30, files[0].size)
        self.assertEqual("movies", files[0].path_pair_id)
        self.assertEqual("Movies", files[0].path_pair_name)

    def test_scan_ignores_status_only_partial_when_temp_file_missing(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir}, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner._MultiPathActiveScanner__active_files = [("download.zip", "movies", "Movies")]
        scanner.logger = MagicMock()

        with open(os.path.join(self.movies_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=0\n0.limit=4\n")

        files = scanner.scan()

        self.assertEqual([], files)
        scanner.logger.warning.assert_not_called()

    def test_scan_ignores_base_only_status_only_partial_when_temp_file_missing(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir}, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner._MultiPathActiveScanner__active_files = [("download.zip", "movies", "Movies")]
        scanner.logger = MagicMock()

        with open(os.path.join(self.movies_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=2\n")

        files = scanner.scan()

        self.assertEqual([], files)
        scanner.logger.warning.assert_not_called()

    def test_scan_ignores_malformed_status_only_partial_when_temp_file_missing(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir}, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner._MultiPathActiveScanner__active_files = [("download.zip", "movies", "Movies")]
        scanner.logger = MagicMock()

        with open(os.path.join(self.movies_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=-2\n0.pos=0\n")

        files = scanner.scan()

        self.assertEqual([], files)
        scanner.logger.warning.assert_called_once()
        self.assertEqual(
            [ModelFile.build_file_id("download.zip", "movies")],
            scanner.pop_malformed_status_only_file_ids()
        )

    def test_scan_rejects_crlf_padded_oversize_status_only_partial(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir}, use_temp_file=True)
        self.addCleanup(scanner.close)
        scanner._MultiPathActiveScanner__active_files = [("download.zip", "movies", "Movies")]
        scanner.logger = MagicMock()

        status = "size=4\r\n0.pos=0\r\n0.limit=4\r\n" + ("\r\n" * 32760)
        with open(os.path.join(self.movies_dir, "download.zip.lftp.lftp-pget-status"), "wb") as handle:
            handle.write(status.encode("utf-8"))

        self.assertEqual([], scanner.scan())
        scanner.logger.warning.assert_called_once()
        self.assertEqual(
            [ModelFile.build_file_id("download.zip", "movies")],
            scanner.pop_malformed_status_only_file_ids(),
        )

    def test_status_only_sidecar_breadcrumb_is_forwarded_to_each_path_pair(self):
        scanner = MultiPathActiveScanner({"movies": self.movies_dir, "tv": self.tv_dir}, use_temp_file=True)
        self.addCleanup(scanner.close)
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner.set_breadcrumb_trace(trace)
        scanner._MultiPathActiveScanner__active_files = [
            ("download.zip", "movies", "Movies"),
        ]

        with open(os.path.join(self.movies_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=4\n0.pos=0\n0.limit=4\n")

        self.assertEqual([], scanner.scan())
        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual(1, len(events))
        details = events[0].args[2]
        self.assertEqual("valid", details["classification"])
        self.assertTrue(details["status_only"])
        self.assertEqual("known", details["parser_coverage"])
        self.assertEqual("multipath_active", details["scan_role"])
        self.assertNotIn(self.temp_dir, repr(events[0]))
