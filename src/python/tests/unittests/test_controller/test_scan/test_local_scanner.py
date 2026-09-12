import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from model import ModelFile
from controller.scan import LocalScanner, ScannerError, ScannerProcess
from common import Localization
from common.performance_diagnostics import (
    DURATION_LOCAL_SCAN_AGGREGATION,
    DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL,
    DURATION_LOCAL_SCAN_MANAGED_EXTRACT,
    DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION,
    DURATION_LOCAL_SCAN_STAGING_MERGE,
)
from common import PerformanceDiagnosticsCollector


class TestLocalScanner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_local_scanner")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_lftp_sidecar_breadcrumb_forwarding_uses_local_role(self):
        scanner = LocalScanner(self.temp_dir, use_temp_file=True, path_pair_id="pair")
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner.set_breadcrumb_trace(trace)
        with open(os.path.join(self.temp_dir, "download.zip.lftp"), "wb") as handle:
            handle.write(b"partial")
        with open(os.path.join(self.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=100\n0.pos=30\n0.limit=100\n")

        files = scanner.scan()

        self.assertEqual(1, len(files))
        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual(1, len(events))
        self.assertEqual("local", events[0].args[2]["scan_role"])

    def test_progressive_scan_publishes_manifest_and_roots_without_full_snapshot(self):
        os.mkdir(os.path.join(self.temp_dir, "root-a"))
        with open(os.path.join(self.temp_dir, "root-a", "file.txt"), "w") as handle:
            handle.write("content")
        with open(os.path.join(self.temp_dir, "root-b"), "w") as handle:
            handle.write("b")
        scanner = LocalScanner(self.temp_dir, use_temp_file=False, path_pair_id="pair")
        events = []
        scanner.set_progress_callback(lambda files, pair_id, pair_name, roots, complete:
                                       events.append((files, roots, complete)))

        final_files = scanner.scan()
        self.assertEqual({"root-a", "root-b"}, {file.name for file in final_files})
        self.assertEqual({"root-a", "root-b"}, events[0][1])
        self.assertEqual({"root-a", "root-b"}, {file.name for event in events[1:-1] for file in event[0]})
        self.assertTrue(events[-1][2])

    def test_progressive_scan_fails_when_manifest_root_disappears_before_scan(self):
        target = os.path.join(self.temp_dir, "target")
        hidden = self.temp_dir + ".hidden"
        os.mkdir(target)
        with open(os.path.join(target, "file.txt"), "w") as handle:
            handle.write("content")

        scanner = LocalScanner(self.temp_dir, use_temp_file=False, path_pair_id="pair")
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner.set_breadcrumb_trace(trace)
        events = []

        def publish(files, _pair_id, _pair_name, root_names, complete):
            events.append((files, root_names, complete))
            if root_names is not None:
                os.rename(target, hidden)

        scanner.set_progress_callback(publish)
        try:
            with self.assertRaises(ScannerError) as error:
                scanner.scan()
        finally:
            if os.path.isdir(hidden):
                os.rename(hidden, target)

        self.assertEqual(Localization.Error.LOCAL_SERVER_SCAN, str(error.exception))
        self.assertTrue(error.exception.recoverable)
        self.assertEqual({"target"}, events[0][1])
        self.assertFalse(any(event[2] for event in events))
        missing = next(call for call in trace.record.call_args_list if call.args[1] == "scan_missing_root")
        self.assertEqual("final", missing.args[2]["root_role"])
        self.assertEqual("final", missing.args[2]["boundary"])
        self.assertNotIn("target", repr(missing))

    def test_progressive_scan_fails_when_manifest_staging_root_disappears_before_scan(self):
        staging_dir = os.path.join(self.temp_dir, "staging")
        target = os.path.join(staging_dir, "partial")
        hidden = staging_dir + ".hidden"
        os.mkdir(staging_dir)
        os.mkdir(target)
        with open(os.path.join(target, "file.txt"), "w") as handle:
            handle.write("content")

        scanner = LocalScanner(
            self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir,
            path_pair_id="pair",
        )
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner.set_breadcrumb_trace(trace)
        events = []

        def publish(files, _pair_id, _pair_name, root_names, complete):
            events.append((files, root_names, complete))
            if root_names is not None:
                os.rename(target, hidden)

        scanner.set_progress_callback(publish)
        try:
            with self.assertRaises(ScannerError) as error:
                scanner.scan()
        finally:
            if os.path.isdir(hidden):
                os.rename(hidden, target)

        self.assertEqual(Localization.Error.LOCAL_SERVER_SCAN, str(error.exception))
        self.assertTrue(error.exception.recoverable)
        self.assertEqual({"partial"}, events[0][1])
        self.assertFalse(any(event[2] for event in events))
        missing = next(call for call in trace.record.call_args_list if call.args[1] == "scan_missing_root")
        self.assertEqual("staging", missing.args[2]["root_role"])
        self.assertEqual("staging", missing.args[2]["boundary"])
        self.assertNotIn("partial", repr(missing))

    @unittest.skipUnless(os.name == "posix", "Dangling symlink semantics require POSIX")
    def test_progressive_scan_fails_closed_for_manifest_dangling_symlink(self):
        os.symlink(os.path.join(self.temp_dir, "missing-target"), os.path.join(self.temp_dir, "dangling"))
        scanner = LocalScanner(self.temp_dir, use_temp_file=False, path_pair_id="pair")
        events = []
        scanner.set_progress_callback(lambda files, _pair_id, _pair_name, roots, complete:
                                      events.append((files, roots, complete)))

        with self.assertRaises(ScannerError) as error:
            scanner.scan()

        self.assertTrue(error.exception.recoverable)
        self.assertEqual({"dangling"}, events[0][1])
        self.assertFalse(any(event[2] for event in events))

    def test_progressive_scan_keeps_managed_extract_pruning_successful(self):
        managed_dir = os.path.join(self.temp_dir, "managed")
        os.mkdir(managed_dir)
        with open(os.path.join(managed_dir, ".seedsync-extract.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "archive_name": "archive.zip",
                    "archive_file_id": ModelFile.build_file_id("archive.zip", "pair"),
                    "path_pair_id": "pair",
                    "extracted_at": "2026-01-01T00:00:00",
                },
                handle,
            )
        with open(os.path.join(managed_dir, "episode.mkv"), "w", encoding="utf-8") as handle:
            handle.write("hidden")
        with open(os.path.join(self.temp_dir, "visible.txt"), "w") as handle:
            handle.write("visible")

        scanner = LocalScanner(self.temp_dir, use_temp_file=False, path_pair_id="pair")
        events = []
        scanner.set_progress_callback(lambda files, _pair_id, _pair_name, roots, complete:
                                      events.append((files, roots, complete)))

        files = scanner.scan()

        self.assertEqual(["visible.txt"], [system_file.name for system_file in files])
        self.assertTrue(events[-1][2])
        self.assertEqual([ModelFile.build_file_id("archive.zip", "pair")], scanner.pop_managed_extract_file_ids())

    def test_progressive_scan_records_fixed_scanner_stage_durations(self):
        with open(os.path.join(self.temp_dir, "root.txt"), "w") as handle:
            handle.write("content")
        staging_dir = os.path.join(self.temp_dir, "staging")
        os.mkdir(staging_dir)
        with open(os.path.join(staging_dir, "partial.txt"), "w") as handle:
            handle.write("partial")
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        scanner = LocalScanner(
            self.temp_dir,
            use_temp_file=False,
            staging_path=staging_dir,
            path_pair_id="pair",
            performance_diagnostics=diagnostics,
        )
        scanner.set_progress_callback(lambda *args: None)

        scanner.scan()

        durations = diagnostics.snapshot()["durations"]
        for metric in (
            DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL,
            DURATION_LOCAL_SCAN_MANAGED_EXTRACT,
            DURATION_LOCAL_SCAN_AGGREGATION,
            DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION,
            DURATION_LOCAL_SCAN_STAGING_MERGE,
        ):
            self.assertGreaterEqual(durations[metric]["count"], 1)
        self.assertNotIn(self.temp_dir, str(diagnostics.snapshot()))

    def test_progressive_scan_returns_lossless_aggregate_for_bounded_queue_final(self):
        for index in range(140):
            with open(os.path.join(self.temp_dir, "root-{}".format(index)), "w") as handle:
                handle.write(str(index))
        scanner = ScannerProcess(
            LocalScanner(self.temp_dir, use_temp_file=False, path_pair_id="pair"),
            interval_in_ms=0,
            verbose=False,
        )
        self.addCleanup(scanner.close_queues)

        scanner.run_loop()
        final_results = [result for result in scanner.pop_results() if result.is_full_snapshot]

        # Completion publishes a per-pair lossless snapshot before the final
        # aggregate; both remain authoritative under bounded queue pressure.
        self.assertEqual(2, len(final_results))
        self.assertEqual({"root-{}".format(index) for index in range(140)},
                         {file.name for result in final_results for file in result.files})

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

    def test_scan_prefers_final_entry_when_same_name_staging_duplicate_is_larger(self):
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
        self.assertEqual(4, files[0].size)
        self.assertFalse(files[0].is_staging)
        self.assertTrue(files[0].has_staging_collision)

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
        self.assertTrue(files["series"].has_staging_collision)
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
        self.assertTrue(files["movie.mkv"].has_staging_collision)
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
        self.assertTrue(files["series"].children[0].has_staging_collision)
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
        self.assertTrue(files["series"].children[0].has_staging_collision)
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
