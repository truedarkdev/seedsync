# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import inspect
import multiprocessing
import logging
import queue
from datetime import datetime
import sys
import time
from unittest.mock import MagicMock, patch, call

import pytest

from common import MultiprocessingLogger
from common.breadcrumb_trace import BreadcrumbTraceCollector
from controller import IScanner, ScannerProcess, ScannerError
from controller.scan.scanner_process import (
    ScannerResult, _ScannerQueueReleaseMarker, _create_scanner_worker, _run_scanner_once,
)
from controller.extract import ExtractProcess
from system import SystemFile


pytestmark = pytest.mark.timeout(10)

class DummyScanner(IScanner):
    def scan(self):
        return []

    def set_base_logger(self, base_logger: logging.Logger):
        pass


class RecoverablePartialScanner(DummyScanner):
    def scan(self):
        raise ScannerError("recoverable child error", recoverable=True, files=[SystemFile("partial", 1)])


class FatalScanner(DummyScanner):
    def scan(self):
        raise ScannerError("fatal child error", recoverable=False)


class StatefulRecycledScanner(DummyScanner):
    def __init__(self):
        self.first_run = True

    def scan(self):
        self.first_run = False
        return []

    def export_recycled_state(self):
        return self.first_run

    def apply_recycled_state(self, state):
        if type(state) is not bool:
            raise TypeError
        self.first_run = state


class RecoverableStatefulRecycledScanner(StatefulRecycledScanner):
    def scan(self):
        self.first_run = False
        raise ScannerError("recoverable stateful child error", recoverable=True)


class FatalStatefulRecycledScanner(StatefulRecycledScanner):
    def scan(self):
        self.first_run = False
        raise ScannerError("fatal stateful child error", recoverable=False)


class _InspectingWakeEvent:
    def __init__(self):
        self.run_loop_locals: set[str] = set()

    def wait(self, timeout: float) -> None:
        caller = inspect.currentframe().f_back
        assert caller is not None
        self.run_loop_locals = set(caller.f_locals)

    def clear(self) -> None:
        pass


class _InlineScannerRunProcess:
    """Exercise coordinator behavior without requiring mock scanners to pickle."""
    def __init__(self, *args):
        self._args = args
        self.pid = 1

    def start(self) -> None:
        _run_scanner_once(*self._args)

    def join(self, timeout=None) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:
        pass

    def close(self) -> None:
        pass


class TestScannerProcess(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        # Assign process to this variable so that it can be cleaned up
        # even after an error
        self.process = None
        self._scan_run_patcher = patch(
            "controller.scan.scanner_process._create_scanner_worker",
            _InlineScannerRunProcess,
        )
        self._scan_run_patcher.start()

    def tearDown(self):
        self._scan_run_patcher.stop()
        if self.process:
            self.process.terminate()

    def test_real_spawn_with_production_breadcrumb_and_log_transport(self):
        self._scan_run_patcher.stop()
        collector = BreadcrumbTraceCollector(lambda: True)
        mp_logger = MultiprocessingLogger(logging.getLogger("scanner-spawn-boundary"))
        self.process = ScannerProcess(
            scanner=DummyScanner(),
            interval_in_ms=1000,
            verbose=False,
            breadcrumb_trace=collector.create_emitter(),
            recycle_scan_worker=True,
        )
        self.process.set_mp_log_queue(mp_logger.queue, mp_logger.log_level)

        mp_logger.start()
        try:
            self.process.start()
            deadline = time.time() + 5
            result = None
            while result is None and time.time() < deadline:
                result = self.process.pop_latest_result()
                time.sleep(0.02)
            self.assertIsNotNone(result)
            self.process.terminate()
            self.process.join(timeout=5)
            self.assertFalse(self.process.is_alive())
            entries = collector.snapshot()["entries"]
            self.assertTrue(any(entry["message"] == "scan_started" for entry in entries))
        finally:
            if self.process.is_alive():
                self.process.terminate()
                self.process.join()
            self.process.close_queues()
            self.process = None
            mp_logger.stop()

    def test_one_shot_scan_worker_is_gone_after_direct_result_delivery(self):
        self._scan_run_patcher.stop()
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=0, verbose=False, recycle_scan_worker=True)
        self.addCleanup(process.close_queues)

        process.run_loop()
        result = None
        deadline = time.monotonic() + 5
        while result is None and time.monotonic() < deadline:
            process.run_loop()
            result = process.pop_latest_result()

        self.assertIsNotNone(result)
        while process._ScannerProcess__scan_worker is not None and time.monotonic() < deadline:
            process.run_loop()
        self.assertIsNone(process._ScannerProcess__scan_worker)
        self.assertFalse(any(child.name.endswith("ScanRun") for child in multiprocessing.active_children()))

    def test_spawned_worker_returns_recoverable_partial_result_directly(self):
        self._scan_run_patcher.stop()
        process = ScannerProcess(scanner=RecoverablePartialScanner(), interval_in_ms=0, verbose=False,
                                 recycle_scan_worker=True)
        self.addCleanup(process.close_queues)

        process.run_loop()
        result = None
        deadline = time.monotonic() + 5
        while result is None and time.monotonic() < deadline:
            process.run_loop()
            result = process.pop_latest_result()

        self.assertIsNotNone(result)
        self.assertTrue(result.failed)
        self.assertEqual("recoverable child error", result.error_message)
        self.assertEqual(["partial"], [system_file.name for system_file in result.files])

    def test_spawned_worker_propagates_fatal_scanner_error(self):
        self._scan_run_patcher.stop()
        process = ScannerProcess(scanner=FatalScanner(), interval_in_ms=0, verbose=False, recycle_scan_worker=True)
        self.addCleanup(process.close_queues)

        process.run_loop()
        deadline = time.monotonic() + 5
        with self.assertRaisesRegex(ScannerError, "fatal child error"):
            while time.monotonic() < deadline:
                process.run_loop()

    def test_spawned_worker_returns_mutable_scanner_state_to_coordinator(self):
        self._scan_run_patcher.stop()
        scanner = StatefulRecycledScanner()
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False, recycle_scan_worker=True)
        self.addCleanup(process.close_queues)

        process.run_loop()
        deadline = time.monotonic() + 5
        while scanner.first_run and time.monotonic() < deadline:
            process.run_loop()

        self.assertFalse(scanner.first_run)
        self.assertIsNone(process._ScannerProcess__scan_worker)

    def test_spawned_worker_returns_mutable_scanner_state_after_recoverable_error(self):
        self._scan_run_patcher.stop()
        scanner = RecoverableStatefulRecycledScanner()
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False, recycle_scan_worker=True)
        self.addCleanup(process.close_queues)

        process.run_loop()
        deadline = time.monotonic() + 5
        while scanner.first_run and time.monotonic() < deadline:
            process.run_loop()

        self.assertFalse(scanner.first_run)
        self.assertIsNone(process._ScannerProcess__scan_worker)

    def test_spawned_worker_returns_mutable_scanner_state_before_fatal_error(self):
        self._scan_run_patcher.stop()
        scanner = FatalStatefulRecycledScanner()
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False, recycle_scan_worker=True)
        self.addCleanup(process.close_queues)

        process.run_loop()
        deadline = time.monotonic() + 5
        with self.assertRaisesRegex(ScannerError, "fatal stateful child error"):
            while time.monotonic() < deadline:
                process.run_loop()

        self.assertFalse(scanner.first_run)

    def test_force_scan_wakes_completed_worker_without_waiting_full_cadence(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=1000, verbose=False)
        self.addCleanup(process.close_queues)

        process.force_scan()
        started_at = time.monotonic()
        process.run_loop()

        self.assertLess(time.monotonic() - started_at, 0.2)

    def test_create_scanner_worker_uses_explicit_spawn_context(self):
        spawn_context = MagicMock()
        worker = MagicMock()
        spawn_context.Process.return_value = worker
        with patch("controller.scan.scanner_process.multiprocessing.get_context", return_value=spawn_context) as get_context:
            created = _create_scanner_worker(
                DummyScanner(), MagicMock(), None, MagicMock(), None, "flow", None, None,
            )

        self.assertIs(worker, created)
        get_context.assert_called_once_with("spawn")
        spawn_context.Process.assert_called_once()
        self.assertIs(_run_scanner_once, spawn_context.Process.call_args.kwargs["target"])

    def test_default_inline_scan_does_not_create_recycled_worker(self):
        scanner = DummyScanner()
        scanner.scan = MagicMock(return_value=[])
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        with patch("controller.scan.scanner_process._create_scanner_worker") as create_worker:
            process.run_init()
            process.run_loop()

        create_worker.assert_not_called()
        scanner.scan.assert_called_once_with()
        self.assertIsNone(process._ScannerProcess__scan_worker)

    def test_thread_coordinator_starts_stops_and_surfaces_inline_fatal_error(self):
        process = ScannerProcess(scanner=FatalScanner(), interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.start()
        deadline = time.monotonic() + 2
        while process.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        process.join(1)

        self.assertFalse(process.is_alive())
        with self.assertRaisesRegex(ScannerError, "fatal child error"):
            process.propagate_exception()

    def test_thread_coordinator_terminates_promptly_during_interval_wait(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=10_000, verbose=False)
        self.addCleanup(process.close_queues)

        process.start()
        deadline = time.monotonic() + 2
        while process.pop_latest_result() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        process.terminate()
        process.join(1)

        self.assertFalse(process.is_alive())

    def test_retrieves_scan_results(self):
        # Use this as a signal to mock to control which result to send
        self.scan_signal = multiprocessing.Value('i', 0)
        self.scan_counter = multiprocessing.Value('i', 0)

        a = SystemFile("a", 100, True)
        aa = SystemFile("aa", 60, False)
        a.add_child(aa)
        ab = SystemFile("ab", 40, False)
        a.add_child(ab)

        b = SystemFile("b", 10, True)
        ba = SystemFile("ba", 10, True)
        b.add_child(ba)
        baa = SystemFile("baa", 10, False)
        ba.add_child(baa)

        c = SystemFile("c", 1234, False)

        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()

        def _scan():
            ret = None
            if self.scan_signal.value == 0:
                ret = [a]
            elif self.scan_signal.value == 1:
                ret = [a, b]
            elif self.scan_signal.value == 2:
                ret = [c]
            elif self.scan_signal.value == 3:
                ret = []
            self.scan_counter.value += 1
            return ret
        mock_scanner.scan.side_effect = _scan

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100)
        process.run_init()
        process.run_loop()

        # wait for the first queued scan result
        while self.scan_counter.value < 1:
            pass
        result = process.pop_latest_result()
        self.assertEqual(1, len(result.files))
        self.assertEqual("a", result.files[0].name)
        self.assertEqual(True, result.files[0].is_dir)
        self.assertEqual(100, result.files[0].size)
        self.assertEqual(2, len(result.files[0].children))
        self.assertEqual("aa", result.files[0].children[0].name)
        self.assertEqual(False, result.files[0].children[0].is_dir)
        self.assertEqual(60, result.files[0].children[0].size)
        self.assertEqual("ab", result.files[0].children[1].name)
        self.assertEqual(False, result.files[0].children[1].is_dir)
        self.assertEqual(40, result.files[0].children[1].size)

        # signal for scan #1 and wait scan fetch
        self.scan_signal.value = 1
        process.run_loop()
        while self.scan_counter.value < 2:
            pass
        result = process.pop_latest_result()
        self.assertEqual(2, len(result.files))
        self.assertEqual("a", result.files[0].name)
        self.assertEqual(True, result.files[0].is_dir)
        self.assertEqual(100, result.files[0].size)
        self.assertEqual(2, len(result.files[0].children))
        self.assertEqual("aa", result.files[0].children[0].name)
        self.assertEqual(False, result.files[0].children[0].is_dir)
        self.assertEqual(60, result.files[0].children[0].size)
        self.assertEqual("ab", result.files[0].children[1].name)
        self.assertEqual(False, result.files[0].children[1].is_dir)
        self.assertEqual(40, result.files[0].children[1].size)
        self.assertEqual("b", result.files[1].name)
        self.assertEqual(True, result.files[1].is_dir)
        self.assertEqual(10, result.files[1].size)
        self.assertEqual(1, len(result.files[1].children))
        self.assertEqual("ba", result.files[1].children[0].name)
        self.assertEqual(True, result.files[1].children[0].is_dir)
        self.assertEqual(10, result.files[1].children[0].size)
        self.assertEqual(1, len(result.files[1].children[0].children))
        self.assertEqual("baa", result.files[1].children[0].children[0].name)
        self.assertEqual(False, result.files[1].children[0].children[0].is_dir)
        self.assertEqual(10, result.files[1].children[0].children[0].size)

        # signal for scan #2 and wait scan fetch
        self.scan_signal.value = 2
        process.run_loop()
        while self.scan_counter.value < 3:
            pass
        result = process.pop_latest_result()
        self.assertEqual(1, len(result.files))
        self.assertEqual("c", result.files[0].name)
        self.assertEqual(False, result.files[0].is_dir)
        self.assertEqual(1234, result.files[0].size)

        # signal for scan #3 and wait scan fetch
        self.scan_signal.value = 3
        process.run_loop()
        while self.scan_counter.value < 4:
            pass
        result = process.pop_latest_result()
        self.assertEqual(0, len(result.files))

    def test_run_loop_releases_scan_graph_before_interval_wait(self):
        root = SystemFile("root", 1, is_dir=True)
        root.add_child(SystemFile("child", 1))
        scanner = DummyScanner()
        scanner.scan = MagicMock(return_value=[root])
        scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=[])
        scanner.pop_managed_extract_file_ids = MagicMock(return_value=[])
        process = ScannerProcess(scanner=scanner, interval_in_ms=100, verbose=False)
        self.addCleanup(process.close_queues)
        wake_event = _InspectingWakeEvent()
        process._ScannerProcess__wake_event = wake_event

        process.run_loop()

        self.assertNotIn("result", wake_event.run_loop_locals)
        self.assertNotIn("files", wake_event.run_loop_locals)
        self.assertNotIn("malformed_status_only_file_ids", wake_event.run_loop_locals)
        self.assertNotIn("managed_extract_file_ids", wake_event.run_loop_locals)
        queued = None
        deadline = time.monotonic() + 1.0
        while queued is None and time.monotonic() < deadline:
            queued = process.pop_latest_result()
            if queued is None:
                time.sleep(0.01)
        self.assertIsNotNone(queued)
        self.assertEqual("root", queued.files[0].name)

    def test_sends_error_result_on_recoverable_error(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()
        mock_scanner.scan.side_effect = ScannerError("recoverable error", recoverable=True)

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100,
                                 verbose=False)
        process.logger = MagicMock()

        process.run_loop()
        result = process.pop_latest_result()
        self.assertEqual(0, len(result.files))
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        process.logger.warning.assert_called_once()
        warning_message = process.logger.warning.call_args[0][0]
        self.assertIn("recoverable error", warning_message)
        self.assertIn("failed result", warning_message.lower())

    def test_sends_partial_files_on_recoverable_error(self):
        partial_file = SystemFile("partial", 42, False)
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()
        mock_scanner.scan.side_effect = ScannerError(
            "recoverable error",
            recoverable=True,
            files=[partial_file]
        )

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100,
                                 verbose=False)
        process.logger = MagicMock()

        process.run_loop()
        result = process.pop_latest_result()
        self.assertEqual([partial_file], result.files)
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)

    def test_propagates_malformed_status_only_file_ids_with_scan_result(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(return_value=[])
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=["a"])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=["managed-1"])

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100,
                                 verbose=False)

        process.run_loop()
        result = process.pop_latest_result()

        self.assertEqual([], result.files)
        self.assertEqual(["a"], result.malformed_status_only_file_ids)
        self.assertEqual(["managed-1"], result.managed_extract_file_ids)
        mock_scanner.pop_malformed_status_only_file_ids.assert_called_once()
        mock_scanner.pop_managed_extract_file_ids.assert_called_once()

    def test_propagates_malformed_status_only_file_ids_on_recoverable_error(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(side_effect=ScannerError("recoverable error", recoverable=True))
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=["a"])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=["managed-1"])

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100,
                                 verbose=False)

        process.run_loop()
        result = process.pop_latest_result()

        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(["a"], result.malformed_status_only_file_ids)
        self.assertEqual(["managed-1"], result.managed_extract_file_ids)
        mock_scanner.pop_malformed_status_only_file_ids.assert_called_once()
        mock_scanner.pop_managed_extract_file_ids.assert_called_once()

    def test_pop_latest_result_returns_last_drained_result_when_queue_get_fails(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=100, verbose=False)
        process.logger = MagicMock()
        first_result = object()
        process._ScannerProcess__queue = MagicMock()
        process._ScannerProcess__queue.get.side_effect = [first_result, OSError("queue broken")]

        latest_result = process.pop_latest_result()

        self.assertIs(latest_result, first_result)
        process.logger.warning.assert_called_once()
        self.assertIn("Scanner queue read failed", process.logger.warning.call_args[0][0])

    def test_pop_latest_result_ignores_release_marker_after_result(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=100, verbose=False)
        result = ScannerResult(datetime.now(), [])
        process._ScannerProcess__queue = MagicMock()
        process._ScannerProcess__queue.get.side_effect = [
            result,
            _ScannerQueueReleaseMarker(),
            queue.Empty(),
        ]

        self.assertIs(result, process.pop_latest_result())

    def test_pop_latest_result_returns_none_for_release_marker_only(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=100, verbose=False)
        process._ScannerProcess__queue = MagicMock()
        process._ScannerProcess__queue.get.side_effect = [
            _ScannerQueueReleaseMarker(),
            queue.Empty(),
        ]

        self.assertIsNone(process.pop_latest_result())

    def test_pop_latest_result_keeps_latest_real_result_across_release_markers(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=100, verbose=False)
        first = ScannerResult(datetime.now(), [SystemFile("first", 1)])
        latest = ScannerResult(datetime.now(), [SystemFile("latest", 2)])
        process._ScannerProcess__queue = MagicMock()
        process._ScannerProcess__queue.get.side_effect = [
            first,
            _ScannerQueueReleaseMarker(),
            latest,
            _ScannerQueueReleaseMarker(),
            queue.Empty(),
        ]

        self.assertIs(latest, process.pop_latest_result())

    def test_pop_latest_result_returns_latest_real_result_when_marker_precedes_queue_error(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=100, verbose=False)
        process.logger = MagicMock()
        result = ScannerResult(datetime.now(), [])
        process._ScannerProcess__queue = MagicMock()
        process._ScannerProcess__queue.get.side_effect = [
            result,
            _ScannerQueueReleaseMarker(),
            OSError("queue broken"),
        ]

        self.assertIs(result, process.pop_latest_result())
        process.logger.warning.assert_called_once()

    def test_run_loop_applies_targeted_scan_request_to_scanner(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(return_value=[])
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=[])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=[])
        mock_scanner.set_scan_target_path_pair_ids = MagicMock()

        process = ScannerProcess(scanner=mock_scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.force_scan("movies")
        time.sleep(0.05)
        process.run_loop()

        self.assertEqual(
            [call({"movies"}), call(None)],
            mock_scanner.set_scan_target_path_pair_ids.mock_calls,
        )
        mock_scanner.scan.assert_called_once_with()

    def test_run_loop_coalesces_multiple_targeted_scan_requests(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(return_value=[])
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=[])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=[])
        mock_scanner.set_scan_target_path_pair_ids = MagicMock()

        process = ScannerProcess(scanner=mock_scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.force_scan("movies")
        process.force_scan("tv")
        time.sleep(0.05)
        process.run_loop()

        self.assertEqual(
            [call({"movies", "tv"}), call(None)],
            mock_scanner.set_scan_target_path_pair_ids.mock_calls,
        )
        mock_scanner.scan.assert_called_once_with()

    def test_run_loop_prefers_full_scan_over_targeted_requests_when_full_is_queued_first(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(return_value=[])
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=[])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=[])
        mock_scanner.set_scan_target_path_pair_ids = MagicMock()

        process = ScannerProcess(scanner=mock_scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.force_scan()
        process.force_scan("movies")
        time.sleep(0.05)
        process.run_loop()

        self.assertEqual(
            [call(None), call(None)],
            mock_scanner.set_scan_target_path_pair_ids.mock_calls,
        )
        mock_scanner.scan.assert_called_once_with()

    def test_run_loop_prefers_full_scan_over_targeted_requests_when_full_is_queued_last(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(return_value=[])
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=[])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=[])
        mock_scanner.set_scan_target_path_pair_ids = MagicMock()

        process = ScannerProcess(scanner=mock_scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.force_scan("movies")
        process.force_scan()
        time.sleep(0.05)
        process.run_loop()

        self.assertEqual(
            [call(None), call(None)],
            mock_scanner.set_scan_target_path_pair_ids.mock_calls,
        )
        mock_scanner.scan.assert_called_once_with()

    def test_run_loop_clears_targeted_scan_state_after_recoverable_exception(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(side_effect=ScannerError("recoverable error", recoverable=True))
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=[])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=[])
        mock_scanner.set_scan_target_path_pair_ids = MagicMock()

        process = ScannerProcess(scanner=mock_scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.force_scan("movies")
        time.sleep(0.05)
        process.run_loop()

        time.sleep(0.05)
        result = process.pop_latest_result()
        self.assertIsNotNone(result)
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(
            [call({"movies"}), call(None)],
            mock_scanner.set_scan_target_path_pair_ids.mock_calls,
        )
        mock_scanner.scan.assert_called_once_with()

    def test_run_loop_clears_targeted_scan_state_after_nonrecoverable_exception(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(side_effect=ScannerError("fatal error", recoverable=False))
        mock_scanner.set_scan_target_path_pair_ids = MagicMock()

        process = ScannerProcess(scanner=mock_scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.force_scan("movies")
        time.sleep(0.05)
        with self.assertRaises(ScannerError):
            process.run_loop()

        self.assertEqual(
            [call({"movies"}), call(None)],
            mock_scanner.set_scan_target_path_pair_ids.mock_calls,
        )
        mock_scanner.scan.assert_called_once_with()

    def test_close_queues_releases_owned_queue_and_is_idempotent(self):
        result_queue = MagicMock()
        spawn_context = MagicMock()
        spawn_context.Queue.return_value = result_queue

        with patch("controller.scan.scanner_process.multiprocessing.get_context", return_value=spawn_context):
            process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=100, verbose=False, recycle_scan_worker=True)

        self.assertIs(process._ScannerProcess__queue, result_queue)

        process.close_queues()
        process.close_queues()

        result_queue.close.assert_called_once_with()
        result_queue.join_thread.assert_called_once_with()
        self.assertIsNone(process._ScannerProcess__queue)
        self.assertIsNone(process._ScannerProcess__scan_target_queue)
        self.assertIsNone(process._ScannerProcess__wake_event)
        self.assertIsNone(process._mp_log_queue)
        self.assertIsNone(process._mp_log_level)

    def test_recoverable_error_warning_resets_after_success(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()
        mock_scanner.scan.side_effect = [
            ScannerError("recoverable error", recoverable=True),
            ScannerError("recoverable error", recoverable=True),
            [],
            ScannerError("recoverable error", recoverable=True),
        ]

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=0,
                                 verbose=False)
        process.logger = MagicMock()

        def _pop_result():
            result = None
            for _ in range(100):
                result = process.pop_latest_result()
                if result is not None:
                    return result
                time.sleep(0.01)
            return result

        process.run_loop()
        result = _pop_result()
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(1, process.logger.warning.call_count)

        process.run_loop()
        result = _pop_result()
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(1, process.logger.warning.call_count)

        process.run_loop()
        result = _pop_result()
        self.assertFalse(result.failed)
        self.assertEqual([], result.files)
        self.assertEqual(1, process.logger.warning.call_count)

        process.run_loop()
        result = _pop_result()
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(2, process.logger.warning.call_count)
        self.assertIn("recoverable error", process.logger.warning.call_args_list[0][0][0])
        self.assertIn("recoverable error", process.logger.warning.call_args_list[1][0][0])

    def test_scan_and_extract_breadcrumbs_share_correlated_versions(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        emitter = collector.create_emitter()

        mock_scanner = DummyScanner()
        mock_scanner.path_pair_id = "movies"
        mock_scanner.path_pair_name = "Movies"
        mock_scanner.scan = MagicMock(return_value=[])

        process = ScannerProcess(
            scanner=mock_scanner,
            interval_in_ms=0,
            verbose=False,
            breadcrumb_trace=emitter
        )
        process.run_init()
        process.run_loop()

        extract_process = ExtractProcess(
            out_dir_path="/out",
            local_path="/local",
            breadcrumb_trace=emitter
        )
        listener = ExtractProcess._ExtractProcess__ExtractListener(
            logger=MagicMock(),
            completed_queue=MagicMock(),
            trace_owner=extract_process
        )
        listener.extract_completed(
            name="movie.mkv",
            is_dir=False,
            file_id="movie-id",
            path_pair_id="movies"
        )

        deadline = time.monotonic() + 1
        snapshot = collector.snapshot()
        while len(snapshot["entries"]) < 3 and time.monotonic() < deadline:
            time.sleep(0.001)
            snapshot = collector.snapshot()
        self.assertEqual(3, len(snapshot["entries"]))
        self.assertEqual(["movies", "movies", "movies"], [entry["corr_id"] for entry in snapshot["entries"]])
        self.assertEqual([1, 2, 3], [entry["version"] for entry in snapshot["entries"]])
        self.assertEqual(["scan", "scan", "extract"], [entry["stage"] for entry in snapshot["entries"]])
        self.assertEqual(["scan_started", "scan_completed", "extract_completed"],
                         [entry["message"] for entry in snapshot["entries"]])
        self.assertEqual(
            snapshot["entries"][0]["flow_id"],
            snapshot["entries"][1]["flow_id"]
        )
        self.assertIsNotNone(snapshot["entries"][0]["flow_id"])

    def test_sends_fatal_exception_on_nonrecoverable_error(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()
        mock_scanner.scan.side_effect = ScannerError("non-recoverable error", recoverable=False)

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100)
        process.run_init()
        with self.assertRaises(ScannerError) as ctx:
            process.run_loop()
        self.assertEqual("non-recoverable error", str(ctx.exception))
