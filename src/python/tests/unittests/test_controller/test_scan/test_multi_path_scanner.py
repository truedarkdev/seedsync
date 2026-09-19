# Copyright 2026, SeedSync Contributors, All rights reserved.

import threading
import unittest
import shutil
import tempfile
import os
from unittest.mock import MagicMock, patch

from controller.scan import (
    MultiPathLocalScanner,
    MultiPathRemoteScanner,
    LocalScanner,
    RemoteScanLease,
    RemoteScanner,
    ScannerError,
    ScannerProcess,
)
from system import SystemFile
from common.performance_diagnostics import DURATION_REMOTE_SCAN_AGGREGATION


class _BlockingPathPairScanner:
    def __init__(self, path_pair_id, started, release, block=False, fail=False, fatal=False, first_run=True):
        self.path_pair_id = path_pair_id
        self.path_pair_name = path_pair_id
        self.started = started
        self.release = release
        self.block = block
        self.fail = fail
        self.fatal = fatal
        self.first_run = first_run
        self.__progress_callback = None

    def set_base_logger(self, logger):
        return None

    def set_progress_callback(self, callback):
        self.__progress_callback = callback

    def export_recycled_state(self):
        return self.first_run, "~/scanfs"

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

    def __init__(self, path_pair_id, release, state, fail=False, first_run=True):
        self.path_pair_id = path_pair_id
        self.path_pair_name = path_pair_id
        self.started = threading.Event()
        self.release = release
        self.state = state
        self.fail = fail
        self.first_run = first_run
        self.__progress_callback = None

    def set_base_logger(self, logger):
        return None

    def set_progress_callback(self, callback):
        self.__progress_callback = callback

    def export_recycled_state(self):
        return self.first_run, "~/scanfs"

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
    def _create_remote_scan_lease(self):
        runtime_dir = tempfile.mkdtemp(prefix="test-remote-scan-runtime-")
        self.addCleanup(shutil.rmtree, runtime_dir)
        with patch("controller.scan.remote_scanner.tempfile.gettempdir", return_value=runtime_dir):
            return RemoteScanLease.create()

    @staticmethod
    def _create_settled_remote_scanners(lease, roots):
        scanners = [
            RemoteScanner("host", "user", "password", 22, root, "/scanfs", "/scanfs", remote_scan_lease=lease)
            for root in roots
        ]
        for scanner in scanners:
            scanner.apply_recycled_state((False, "/scanfs"))
        return scanners

    @staticmethod
    def _run_in_thread(scanner):
        result = []
        errors = []

        def run():
            try:
                result.append(scanner.scan())
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        return thread, result, errors

    @staticmethod
    def _blocking_remote_scan_body(state, started, release, finished, name):
        def run():
            with state["lock"]:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            started.set()
            try:
                if not release.wait(timeout=5):
                    raise AssertionError("remote scan body release timed out")
                return [SystemFile(name, 1, False)]
            finally:
                with state["lock"]:
                    state["active"] -= 1
                finished.set()

        return run

    def test_remote_aggregation_duration_wraps_result_tagging(self):
        release = threading.Event()
        release.set()
        scanner_diagnostics = MagicMock()
        scanner_diagnostics.begin_duration.return_value = "aggregation-start"
        child = _BlockingPathPairScanner("pair-1", threading.Event(), release)

        scanner = MultiPathRemoteScanner(
            [child],
            performance_diagnostics=scanner_diagnostics,
        )

        self.assertEqual(["pair-1.bin"], [file.name for file in scanner.scan()])
        scanner_diagnostics.begin_duration.assert_called_once_with(
            DURATION_REMOTE_SCAN_AGGREGATION,
        )
        scanner_diagnostics.finish_duration.assert_called_once_with(
            DURATION_REMOTE_SCAN_AGGREGATION,
            "aggregation-start",
        )

    def test_remote_scan_lease_serializes_refresh_generations(self):
        runtime_dir = tempfile.mkdtemp(prefix="test-remote-scan-runtime-")
        self.addCleanup(shutil.rmtree, runtime_dir)
        with patch("controller.scan.remote_scanner.tempfile.gettempdir", return_value=runtime_dir):
            lease = RemoteScanLease.create()
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

    def test_settled_compatible_scans_share_master_and_bounded_pool(self):
        scanners = self._create_settled_remote_scanners(
            self._create_remote_scan_lease(), ("/one", "/two"))
        state = {"lock": threading.Lock(), "active": 0, "max_active": 0}
        started = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        finished = [threading.Event(), threading.Event()]

        for index, scanner in enumerate(scanners):
            scanner.create_generation_ssh_reuse = MagicMock()
            scanner.set_generation_ssh_reuse = MagicMock()
            scanner.clear_generation_ssh_reuse = MagicMock()
            scanner.close_generation_ssh_reuse = MagicMock()
            scanner.scan_with_remote_scan_lease_held = MagicMock(
                side_effect=self._blocking_remote_scan_body(
                    state, started[index], release[index], finished[index], "pair-{}.bin".format(index + 1)))
        control_master = object()
        scanners[0].create_generation_ssh_reuse.return_value = control_master

        thread, result, errors = self._run_in_thread(MultiPathRemoteScanner(scanners))
        try:
            self.assertTrue(started[0].wait(timeout=2))
            self.assertTrue(started[1].wait(timeout=2))
            with state["lock"]:
                self.assertEqual(2, state["max_active"])
            release[1].set()
            self.assertTrue(finished[1].wait(timeout=2))
            self.assertFalse(finished[0].is_set())
            release[0].set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
            self.assertEqual(["pair-1.bin", "pair-2.bin"], [file.name for file in result[0]])
            scanners[0].create_generation_ssh_reuse.assert_called_once_with()
            scanners[1].create_generation_ssh_reuse.assert_not_called()
            for scanner in scanners:
                scanner.set_generation_ssh_reuse.assert_called_once_with(control_master)
                scanner.clear_generation_ssh_reuse.assert_called_once_with()
                scanner.scan_with_remote_scan_lease_held.assert_called_once_with()
            scanners[0].close_generation_ssh_reuse.assert_called_once_with()
            scanners[1].close_generation_ssh_reuse.assert_not_called()
        finally:
            for event in release:
                event.set()
            thread.join(timeout=5)

    def test_compatible_generations_serialize_with_or_without_master(self):
        for control_master in (object(), None):
            with self.subTest(control_master=control_master is not None):
                lease = self._create_remote_scan_lease()
                first_scanners = self._create_settled_remote_scanners(
                    lease, ("/first-one", "/first-two"))
                second_scanners = self._create_settled_remote_scanners(
                    lease, ("/second-one", "/second-two"))
                state = {"lock": threading.Lock(), "active": 0, "max_active": 0}
                first_started, first_release, first_finished = threading.Event(), threading.Event(), threading.Event()
                second_started, second_release, second_finished = threading.Event(), threading.Event(), threading.Event()
                first_scanners[0].scan_with_remote_scan_lease_held = MagicMock(
                    side_effect=self._blocking_remote_scan_body(
                        state, first_started, first_release, first_finished, "first-one"))
                second_scanners[0].scan_with_remote_scan_lease_held = MagicMock(
                    side_effect=self._blocking_remote_scan_body(
                        state, second_started, second_release, second_finished, "second-one"))
                for scanners, second_name in ((first_scanners, "first-two"), (second_scanners, "second-two")):
                    scanners[1].scan_with_remote_scan_lease_held = MagicMock(
                        return_value=[SystemFile(second_name, 1, False)])
                    for scanner in scanners:
                        scanner.create_generation_ssh_reuse = MagicMock()
                        scanner.set_generation_ssh_reuse = MagicMock()
                        scanner.clear_generation_ssh_reuse = MagicMock()
                        scanner.close_generation_ssh_reuse = MagicMock()
                    scanners[0].create_generation_ssh_reuse.return_value = control_master

                first_thread, first_result, first_errors = self._run_in_thread(
                    MultiPathRemoteScanner(first_scanners))
                second_thread = None
                try:
                    self.assertTrue(first_started.wait(timeout=2))
                    second_thread, second_result, second_errors = self._run_in_thread(
                        MultiPathRemoteScanner(second_scanners))
                    self.assertFalse(second_started.wait(timeout=0.1))
                    first_release.set()
                    first_thread.join(timeout=5)
                    self.assertFalse(first_thread.is_alive())
                    self.assertTrue(second_started.wait(timeout=2))
                    second_release.set()
                    second_thread.join(timeout=5)
                    self.assertFalse(second_thread.is_alive())
                    self.assertEqual([], first_errors + second_errors)
                    self.assertEqual(1, state["max_active"])
                    self.assertEqual(["first-one", "first-two"], [file.name for file in first_result[0]])
                    self.assertEqual(["second-one", "second-two"], [file.name for file in second_result[0]])
                    for scanners in (first_scanners, second_scanners):
                        scanners[0].create_generation_ssh_reuse.assert_called_once_with()
                        scanners[1].create_generation_ssh_reuse.assert_not_called()
                        if control_master is None:
                            scanners[0].close_generation_ssh_reuse.assert_not_called()
                            for scanner in scanners:
                                scanner.set_generation_ssh_reuse.assert_not_called()
                                scanner.clear_generation_ssh_reuse.assert_not_called()
                        else:
                            scanners[0].close_generation_ssh_reuse.assert_called_once_with()
                            for scanner in scanners:
                                scanner.set_generation_ssh_reuse.assert_called_once_with(control_master)
                                scanner.clear_generation_ssh_reuse.assert_called_once_with()
                finally:
                    first_release.set()
                    second_release.set()
                    first_thread.join(timeout=5)
                    if second_thread is not None:
                        second_thread.join(timeout=5)

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

    def test_export_state_failure_keeps_remote_generation_serial(self):
        first_release = threading.Event()
        second_release = threading.Event()
        second_release.set()
        state = {"lock": threading.Lock(), "active": 0, "max_active": 0, "order": []}
        first = _SerialPathPairScanner("pair-1", first_release, state)
        first.export_recycled_state = MagicMock(side_effect=RuntimeError("state unavailable"))
        second = _SerialPathPairScanner("pair-2", second_release, state, first_run=False)
        scanner = MultiPathRemoteScanner([first, second])
        result = []

        thread = threading.Thread(target=lambda: result.append(scanner.scan()))
        thread.start()
        self.assertTrue(first.started.wait(timeout=2))
        self.assertFalse(second.started.wait(timeout=0.1))

        first_release.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(result))
        self.assertEqual(["pair-1.bin", "pair-2.bin"], [file.name for file in result[0]])
        self.assertEqual(1, state["max_active"])

    def test_malformed_export_state_keeps_remote_generation_serial(self):
        for recycled_state in ((), (None, "~/scanfs"), (0, "~/scanfs"), ("false", "~/scanfs")):
            with self.subTest(recycled_state=recycled_state):
                first_release = threading.Event()
                second_release = threading.Event()
                second_release.set()
                state = {"lock": threading.Lock(), "active": 0, "max_active": 0, "order": []}
                first = _SerialPathPairScanner("pair-1", first_release, state)
                first.export_recycled_state = MagicMock(return_value=recycled_state)
                second = _SerialPathPairScanner("pair-2", second_release, state, first_run=False)
                scanner = MultiPathRemoteScanner([first, second])
                result = []

                thread = threading.Thread(target=lambda: result.append(scanner.scan()))
                thread.start()
                self.assertTrue(first.started.wait(timeout=2))
                self.assertFalse(second.started.wait(timeout=0.1))

                first_release.set()
                thread.join(timeout=5)

                self.assertFalse(thread.is_alive())
                self.assertEqual(1, len(result))
                self.assertEqual(1, state["max_active"])

    def test_remote_refreshes_use_bounded_parallelism_and_keep_input_order(self):
        release = threading.Event()
        state = {"lock": threading.Lock(), "active": 0, "max_active": 0, "order": []}
        started = [threading.Event() for _ in range(6)]
        scanners = [
            _SerialPathPairScanner(
                "pair-{}".format(index + 1),
                release,
                state,
                first_run=False,
            )
            for index in range(6)
        ]
        for scanner, event in zip(scanners, started):
            scanner.started = event
        scanner = MultiPathRemoteScanner(scanners)
        result = []

        thread = threading.Thread(target=lambda: result.append(scanner.scan()))
        thread.start()
        self.assertTrue(all(event.wait(timeout=2) for event in started[:4]))
        self.assertFalse(started[4].is_set())
        self.assertFalse(started[5].is_set())

        release.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(result))
        self.assertEqual(4, state["max_active"])
        self.assertEqual(
            ["pair-{}.bin".format(index + 1) for index in range(6)],
            [file.name for file in result[0]],
        )

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
        successful_scanner.export_recycled_state.return_value = (False, "~/scanfs")
        successful_scanner.scan.return_value = [partial_success_file]

        failing_scanner = MagicMock()
        failing_scanner.path_pair_id = "tv"
        failing_scanner.path_pair_name = "TV"
        failing_scanner.export_recycled_state.return_value = (False, "~/scanfs")
        failing_scanner.scan.side_effect = ScannerError(
            "An error occurred while scanning the remote server: "
            "'SystemScannerError: Path does not exist: /remote/tv'.",
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
        self.assertIn("SystemScannerError: Path does not exist: /remote/tv", str(ctx.exception))
        self.assertEqual({"tv"}, scanner.failed_path_pair_ids())
        successful_scanner.scan.assert_called_once_with()
        failing_scanner.scan.assert_called_once_with()


class TestMultiPathLocalScanner(unittest.TestCase):
    def test_lftp_sidecar_forwarding_distinguishes_same_basename_across_roots(self):
        root = tempfile.mkdtemp(prefix="test-multipath-local-sidecar-")
        self.addCleanup(shutil.rmtree, root)
        movies = os.path.join(root, "movies")
        tv = os.path.join(root, "tv")
        os.mkdir(movies)
        os.mkdir(tv)
        for path in (movies, tv):
            with open(os.path.join(path, "same.zip.lftp"), "wb") as handle:
                handle.write(b"partial")
            with open(os.path.join(path, "same.zip.lftp.lftp-pget-status"), "w") as handle:
                handle.write("size=100\n0.pos=30\n0.limit=100\n")

        scanner = MultiPathLocalScanner([
            LocalScanner(movies, use_temp_file=True, path_pair_id="movies"),
            LocalScanner(tv, use_temp_file=True, path_pair_id="tv"),
        ])
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner.set_breadcrumb_trace(trace)

        self.assertEqual(2, len(scanner.scan()))
        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual(2, len(events))
        self.assertEqual({"local"}, {event.args[2]["scan_role"] for event in events})
        self.assertEqual(2, len({event.kwargs["corr_id"] for event in events}))

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

    def test_selected_pair_uses_reserved_worker_while_first_four_are_busy(self):
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
        result = []
        thread = threading.Thread(target=lambda: result.append(scanner.scan()))
        thread.start()
        self.assertTrue(all(started[index].wait(timeout=2) for index in range(4)))

        scanner.prioritize_path_pair("pair-6")

        self.assertTrue(started[5].wait(timeout=2))
        self.assertFalse(started[4].is_set())
        release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(6, len(result[0]))

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
