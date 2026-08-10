# Copyright 2026, SeedSync Contributors, All rights reserved.

import threading
import unittest
from unittest.mock import MagicMock

from controller.scan import MultiPathLocalScanner, MultiPathRemoteScanner, ScannerError, ScannerProcess
from system import SystemFile


class _BlockingPathPairScanner:
    def __init__(self, path_pair_id, started, release, block=False, fail=False, fatal=False):
        self.path_pair_id = path_pair_id
        self.path_pair_name = path_pair_id
        self.started = started
        self.release = release
        self.block = block
        self.fail = fail
        self.fatal = fatal
        self.__progress_callback = None

    def set_base_logger(self, logger):
        return None

    def set_progress_callback(self, callback):
        self.__progress_callback = callback

    def scan(self):
        self.started.set()
        if self.block:
            self.release.wait(timeout=5)
        if self.fatal:
            raise RuntimeError("unexpected scanner failure")
        if self.fail:
            raise ScannerError("temporary failure", recoverable=True)
        system_file = SystemFile(self.path_pair_id + ".bin", 1, False)
        if self.__progress_callback is not None:
            self.__progress_callback([system_file], self.path_pair_id, self.path_pair_name, None, False)
            self.__progress_callback([], self.path_pair_id, self.path_pair_name, None, True)
        return [system_file]


class TestMultiPathRemoteScanner(unittest.TestCase):
    def test_scanner_process_publishes_all_six_selected_pairs(self):
        release = threading.Event()
        release.set()
        pair_ids = {"pair-{}".format(index + 1) for index in range(6)}
        scanners = [
            _BlockingPathPairScanner(
                path_pair_id,
                threading.Event(),
                release,
            )
            for path_pair_id in sorted(pair_ids)
        ]
        scanner = MultiPathRemoteScanner(scanners)
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.run_loop()
        results = process.pop_results()

        progress_ids = {
            path_pair_id
            for result in results
            if result.is_progress
            for path_pair_id in result.scanned_path_pair_ids
        }
        full_snapshots = [result for result in results if result.is_full_snapshot]
        self.assertEqual(pair_ids, progress_ids)
        self.assertEqual(1, len(full_snapshots))
        self.assertEqual(pair_ids, full_snapshots[0].full_snapshot_path_pair_ids)
        self.assertEqual(pair_ids, {file.path_pair_id for file in full_snapshots[0].files})

    def test_fatal_worker_error_stops_new_submissions_and_propagates(self):
        release = threading.Event()
        started = [threading.Event() for _ in range(6)]
        scanners = [
            _BlockingPathPairScanner(
                "pair-{}".format(index + 1),
                started[index],
                release,
                block=index in {0, 1, 2, 3},
                fatal=index == 0,
            )
            for index in range(6)
        ]
        scanner = MultiPathRemoteScanner(scanners)
        published = []
        scanner.set_progress_callback(
            lambda files, path_pair_id, path_pair_name, root_names, complete:
            published.append(path_pair_id)
        )
        result = []

        def run_scan():
            try:
                scanner.scan()
            except Exception as error:
                result.append(error)

        thread = threading.Thread(target=run_scan)
        thread.start()
        self.assertTrue(all(started[index].wait(timeout=2) for index in range(4)))
        self.assertFalse(started[4].is_set())
        self.assertFalse(started[5].is_set())
        release.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(result))
        self.assertIsInstance(result[0], RuntimeError)
        self.assertEqual(set(), scanner.failed_path_pair_ids())
        self.assertNotIn("pair-5", published)
        self.assertNotIn("pair-6", published)

    def test_bounded_scheduler_advances_after_early_error_and_first_wave(self):
        release = threading.Event()
        started = [threading.Event() for _ in range(6)]
        scanners = [
            _BlockingPathPairScanner(
                "pair-{}".format(index + 1),
                started[index],
                release,
                block=index in {1, 2, 3, 4},
                fail=index == 0,
            )
            for index in range(6)
        ]
        scanner = MultiPathRemoteScanner(scanners)
        published = []
        scanner.set_progress_callback(
            lambda files, path_pair_id, path_pair_name, root_names, complete:
            published.append((path_pair_id, complete))
        )
        result = []

        def run_scan():
            try:
                scanner.scan()
            except ScannerError as error:
                result.append(error)

        thread = threading.Thread(target=run_scan)
        thread.start()
        self.assertTrue(started[4].wait(timeout=2))
        self.assertFalse(started[5].is_set())
        release.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(result))
        self.assertTrue(result[0].recoverable)
        self.assertEqual({"pair-1"}, scanner.failed_path_pair_ids())
        self.assertTrue(started[5].is_set())
        published_ids = {path_pair_id for path_pair_id, _ in published}
        self.assertIn("pair-5", published_ids)
        self.assertIn("pair-6", published_ids)

    def test_recycled_state_round_trips_each_remote_scanner_in_order(self):
        movie_scanner = MagicMock()
        movie_scanner.export_recycled_state.return_value = (False, "~/movie-scanfs")
        tv_scanner = MagicMock()
        tv_scanner.export_recycled_state.return_value = (True, "/tmp/tv-scanfs")
        scanner = MultiPathRemoteScanner([movie_scanner, tv_scanner])

        state = scanner.export_recycled_state()
        scanner.apply_recycled_state(state)

        self.assertEqual(((False, "~/movie-scanfs"), (True, "/tmp/tv-scanfs")), state)
        movie_scanner.apply_recycled_state.assert_called_once_with((False, "~/movie-scanfs"))
        tv_scanner.apply_recycled_state.assert_called_once_with((True, "/tmp/tv-scanfs"))

    def test_empty_remote_pair_roots_are_reported_for_reconciliation(self):
        movie_scanner = MagicMock()
        movie_scanner.path_pair_id = "movies"
        tv_scanner = MagicMock()
        tv_scanner.path_pair_id = "tv"
        scanner = MultiPathRemoteScanner([movie_scanner, tv_scanner])

        self.assertEqual({"movies", "tv"}, scanner.scanned_path_pair_ids())

    def test_reports_partial_files_and_aggregate_recoverable_error(self):
        partial_success_file = SystemFile("movie.mkv", 1234, False)
        partial_failure_file = SystemFile("episode.mkv", 2222, False)

        successful_scanner = MagicMock()
        successful_scanner.path_pair_id = "movies"
        successful_scanner.path_pair_name = "Movies"
        successful_scanner.scan.return_value = [partial_success_file]

        failing_scanner = MagicMock()
        failing_scanner.path_pair_id = "tv"
        failing_scanner.path_pair_name = "TV"
        failing_scanner.scan.side_effect = ScannerError(
            "temporary remote failure",
            recoverable=True,
            files=[partial_failure_file]
        )

        scanner = MultiPathRemoteScanner([successful_scanner, failing_scanner])

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertTrue(ctx.exception.recoverable)
        self.assertEqual([partial_success_file, partial_failure_file], ctx.exception.files)
        self.assertEqual("movies", ctx.exception.files[0].path_pair_id)
        self.assertEqual("Movies", ctx.exception.files[0].path_pair_name)
        self.assertEqual("tv", ctx.exception.files[1].path_pair_id)
        self.assertEqual("TV", ctx.exception.files[1].path_pair_name)
        self.assertIn("TV", str(ctx.exception))
        self.assertIn("temporary remote failure", str(ctx.exception))


class TestMultiPathLocalScanner(unittest.TestCase):
    def test_bounded_scheduler_runs_pairs_after_first_four_complete(self):
        release = threading.Event()
        started = [threading.Event() for _ in range(6)]
        scanners = [
            _BlockingPathPairScanner(
                "pair-{}".format(index + 1),
                started[index],
                release,
                block=index < 4,
            )
            for index in range(6)
        ]
        scanner = MultiPathLocalScanner(scanners)
        published = []
        scanner.set_progress_callback(
            lambda files, path_pair_id, path_pair_name, root_names, complete:
            published.append((path_pair_id, complete))
        )
        result = []

        thread = threading.Thread(target=lambda: result.append(scanner.scan()))
        thread.start()
        self.assertTrue(all(started[index].wait(timeout=2) for index in range(4)))
        self.assertFalse(started[4].is_set())
        self.assertFalse(started[5].is_set())
        release.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(result))
        self.assertEqual(6, len(result[0]))
        self.assertTrue(started[4].is_set())
        self.assertTrue(started[5].is_set())
        published_ids = {path_pair_id for path_pair_id, _ in published}
        self.assertIn("pair-5", published_ids)
        self.assertIn("pair-6", published_ids)

    def test_reports_recoverable_error_so_partial_local_scan_cannot_be_authoritative(self):
        successful_file = SystemFile("movie.mkv", 1234, False)
        successful_scanner = MagicMock()
        successful_scanner.path_pair_id = "movies"
        successful_scanner.path_pair_name = "Movies"
        successful_scanner.scan.return_value = [successful_file]

        failing_scanner = MagicMock()
        failing_scanner.path_pair_id = "tv"
        failing_scanner.path_pair_name = "TV"
        failing_scanner.scan.side_effect = ScannerError("temporary local failure", recoverable=True)

        scanner = MultiPathLocalScanner([successful_scanner, failing_scanner])
        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertTrue(ctx.exception.recoverable)
        self.assertEqual([successful_file], ctx.exception.files)

    def test_aggregates_recovered_managed_extract_file_ids(self):
        scanner_one = MagicMock()
        scanner_one.pop_managed_extract_file_ids.return_value = ["movie.zip", "series.zip"]
        scanner_two = MagicMock()
        scanner_two.pop_managed_extract_file_ids.return_value = ["series.zip", "episode.zip"]

        scanner = MultiPathLocalScanner([scanner_one, scanner_two])

        self.assertEqual(
            ["episode.zip", "movie.zip", "series.zip"],
            scanner.pop_managed_extract_file_ids()
        )

    def test_scans_only_targeted_path_pair_when_targeted(self):
        movie_file = SystemFile("movie.mkv", 1234, False)
        movie_scanner = MagicMock()
        movie_scanner.path_pair_id = "movies"
        movie_scanner.path_pair_name = "Movies"
        movie_scanner.scan.return_value = [movie_file]

        episode_file = SystemFile("episode.mkv", 4321, False)
        episode_scanner = MagicMock()
        episode_scanner.path_pair_id = "tv"
        episode_scanner.path_pair_name = "TV"
        episode_scanner.scan.return_value = [episode_file]

        scanner = MultiPathLocalScanner([movie_scanner, episode_scanner])
        scanner.set_scan_target_path_pair_ids({"tv"})

        results = scanner.scan()

        self.assertEqual([episode_file], results)
        movie_scanner.scan.assert_not_called()
        episode_scanner.scan.assert_called_once_with()
        self.assertEqual("tv", results[0].path_pair_id)
        self.assertEqual("TV", results[0].path_pair_name)

    def test_empty_pair_roots_are_reported_for_reconciliation(self):
        movie_scanner = MagicMock()
        movie_scanner.path_pair_id = "movies"
        tv_scanner = MagicMock()
        tv_scanner.path_pair_id = "tv"
        scanner = MultiPathLocalScanner([movie_scanner, tv_scanner])

        self.assertEqual({"movies", "tv"}, scanner.scanned_path_pair_ids())
        scanner.set_scan_target_path_pair_ids({"tv"})
        self.assertEqual({"tv"}, scanner.scanned_path_pair_ids())


if __name__ == "__main__":
    unittest.main()
