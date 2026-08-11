# Copyright 2026, SeedSync Contributors, All rights reserved.

import threading
import unittest
import os
from unittest.mock import MagicMock

from controller.scan import (
    MultiPathLocalScanner,
    MultiPathRemoteScanner,
    RemoteScanLease,
    RemoteScanner,
    ScannerError,
    ScannerProcess,
)
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


class _SerialPathPairScanner:
    """Remote-like scanner that exposes overlap and invocation ordering."""

    def __init__(self, path_pair_id, release, state, fail=False):
        self.path_pair_id = path_pair_id
        self.path_pair_name = path_pair_id
        self.started = threading.Event()
        self.release = release
        self.state = state
        self.fail = fail
        self.__progress_callback = None

    def set_base_logger(self, logger):
        return None

    def set_progress_callback(self, callback):
        self.__progress_callback = callback

    def scan(self):
        self.started.set()
        with self.state["lock"]:
            self.state["active"] += 1
            self.state["max_active"] = max(self.state["max_active"], self.state["active"])
            self.state["order"].append(self.path_pair_id)
        try:
            self.release.wait(timeout=5)
            if self.fail:
                raise ScannerError("temporary failure", recoverable=True)
            system_file = SystemFile(self.path_pair_id + ".bin", 1, False)
            if self.__progress_callback is not None:
                self.__progress_callback([system_file], self.path_pair_id, self.path_pair_name, None, False)
                self.__progress_callback([], self.path_pair_id, self.path_pair_name, None, True)
            return [system_file]
        finally:
            with self.state["lock"]:
                self.state["active"] -= 1


class TestMultiPathRemoteScanner(unittest.TestCase):
    def test_remote_scan_lease_serializes_refresh_generations(self):
        lease = RemoteScanLease.create()
        self.addCleanup(lambda: os.path.exists(lease.path) and os.unlink(lease.path))
        old_scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/old",
            local_path_to_scan_script="/scanfs",
            remote_path_to_scan_script="/scanfs",
            remote_scan_lease=lease,
        )
        new_scanner = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password="password",
            remote_port=22,
            remote_path_to_scan="/new",
            local_path_to_scan_script="/scanfs",
            remote_path_to_scan_script="/scanfs",
            remote_scan_lease=lease,
        )
        old_entered = threading.Event()
        release_old = threading.Event()
        new_entered = threading.Event()

        def old_scan():
            old_entered.set()
            self.assertTrue(release_old.wait(timeout=5))
            return []

        def new_scan():
            new_entered.set()
            return []

        old_scanner._RemoteScanner__scan = old_scan
        new_scanner._RemoteScanner__scan = new_scan
        old_process = ScannerProcess(scanner=old_scanner, interval_in_ms=0, verbose=False)
        new_process = ScannerProcess(scanner=new_scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(old_process.close_queues)
        self.addCleanup(new_process.close_queues)

        old_thread = threading.Thread(target=old_process.run_loop)
        new_thread = threading.Thread(target=new_process.run_loop)
        old_thread.start()
        self.assertTrue(old_entered.wait(timeout=2))
        new_thread.start()
        self.assertFalse(new_entered.wait(timeout=0.1))

        release_old.set()
        old_thread.join(timeout=5)
        new_thread.join(timeout=5)
        self.assertFalse(old_thread.is_alive())
        self.assertFalse(new_thread.is_alive())
        self.assertTrue(new_entered.is_set())

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
        self.assertEqual(7, len(full_snapshots))
        pair_snapshots = [result for result in full_snapshots
                          if len(result.full_snapshot_path_pair_ids) == 1]
        self.assertEqual(pair_ids, {
            next(iter(result.full_snapshot_path_pair_ids)) for result in pair_snapshots
        })
        aggregate_snapshots = [result for result in full_snapshots
                               if result.full_snapshot_path_pair_ids == pair_ids]
        self.assertEqual(1, len(aggregate_snapshots))
        self.assertEqual(pair_ids, {file.path_pair_id for file in aggregate_snapshots[0].files})

    def test_remote_scans_are_serialized_and_keep_input_order(self):
        first_release = threading.Event()
        second_release = threading.Event()
        second_release.set()
        state = {"lock": threading.Lock(), "active": 0, "max_active": 0, "order": []}
        first = _SerialPathPairScanner("pair-1", first_release, state)
        second = _SerialPathPairScanner("pair-2", second_release, state)
        scanner = MultiPathRemoteScanner([first, second])
        result = []

        thread = threading.Thread(target=lambda: result.append(scanner.scan()))
        thread.start()
        self.assertTrue(first.started.wait(timeout=2))
        self.assertFalse(second.started.wait(timeout=0.1))

        first_release.set()
        self.assertTrue(second.started.wait(timeout=2))
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(result))
        self.assertEqual(["pair-1", "pair-2"], state["order"])
        self.assertEqual(1, state["max_active"])
        self.assertEqual(["pair-1.bin", "pair-2.bin"], [file.name for file in result[0]])

    def test_fatal_remote_error_stops_following_scans(self):
        release = threading.Event()
        release.set()
        first = _BlockingPathPairScanner("pair-1", threading.Event(), release, fatal=True)
        second = _BlockingPathPairScanner("pair-2", threading.Event(), release)
        scanner = MultiPathRemoteScanner([first, second])

        with self.assertRaises(RuntimeError):
            scanner.scan()
        self.assertTrue(first.started.is_set())
        self.assertFalse(second.started.is_set())
        self.assertEqual(set(), scanner.failed_path_pair_ids())

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
