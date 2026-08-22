# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import inspect
import multiprocessing
import logging
import pickle
import queue
import threading
from collections import deque
from datetime import datetime
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from common import MultiprocessingLogger
from common.breadcrumb_trace import BreadcrumbTraceCollector
from common.performance_diagnostics import (
    DURATION_REMOTE_SCAN_AGGREGATION,
    DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION,
    DURATION_REMOTE_SCAN_STREAM_PARSING,
    DURATION_REMOTE_SCAN_TRANSPORT_READ,
    PerformanceDiagnosticsCollector,
)
from controller import IScanner, ScannerProcess, ScannerError
from controller.scan import MultiPathRemoteScanner, RemoteScanner
from controller.scan.scanner_process import (
    ScannerResult, _ScannerQueueReleaseMarker, _create_scanner_worker, _publish_bounded_result,
    _record_scan_breadcrumb, _run_scanner_once,
)
from controller.extract import ExtractProcess
from system import SystemFile


pytestmark = pytest.mark.timeout(10)

class DummyScanner(IScanner):
    def scan(self):
        return []

    def set_base_logger(self, base_logger: logging.Logger):
        pass


class ProgressiveScanner(DummyScanner):
    path_pair_id = "pair"
    path_pair_name = "Pair"

    def __init__(self):
        self.callback = None

    def set_progress_callback(self, callback):
        self.callback = callback

    def scanned_path_pair_ids(self):
        return {self.path_pair_id}

    def scan(self):
        assert self.callback is not None
        self.callback([], self.path_pair_id, self.path_pair_name, {"a", "b"}, False)
        self.callback([SystemFile("a", 1)], self.path_pair_id, self.path_pair_name, None, False)
        self.callback([], self.path_pair_id, self.path_pair_name, None, True)
        return [SystemFile("a", 1), SystemFile("b", 2)]


class FingerprintProgressiveScanner(ProgressiveScanner):
    def __init__(self):
        super().__init__()
        self.accepted = {}

    def set_accepted_root_fingerprints(self, fingerprints):
        self.accepted = fingerprints

    def scan(self):
        assert self.callback is not None
        fingerprints = self.accepted.get(self.path_pair_id, {})
        self.callback([], self.path_pair_id, self.path_pair_name, {"a"}, False)
        self.callback([], self.path_pair_id, self.path_pair_name, None, False, fingerprints)
        self.callback([], self.path_pair_id, self.path_pair_name, None, True)
        return []


class BlockingFingerprintScanner(DummyScanner):
    """Expose whether a staged hint can mutate an already admitted scan."""
    def __init__(self):
        self.accepted = {}
        self.started = threading.Event()
        self.release = threading.Event()
        self.observed = []

    def set_accepted_root_fingerprints(self, fingerprints):
        self.accepted = fingerprints

    def scan(self):
        self.started.set()
        self.release.wait(timeout=2)
        self.observed.append(self.accepted)
        return []


class SpawnedFingerprintScanner(DummyScanner):
    """Report each spawned child's admitted hint while coordinating its scan."""
    def __init__(self):
        spawn_context = multiprocessing.get_context("spawn")
        self.accepted = {}
        self.started = spawn_context.Event()
        self.release = spawn_context.Event()
        self.observed = spawn_context.Queue()

    def set_accepted_root_fingerprints(self, fingerprints):
        self.accepted = fingerprints

    def scan(self):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("spawned scan was not released")
        self.observed.put(self.accepted)
        return []


class BurstProgressiveScanner(ProgressiveScanner):
    def scan(self):
        assert self.callback is not None
        aggregate = []
        for index in range(512):
            root = SystemFile("root-{}".format(index), index)
            aggregate.append(root)
            self.callback(
                [root],
                self.path_pair_id,
                self.path_pair_name,
                None,
                False,
            )
        self.callback([], self.path_pair_id, self.path_pair_name, None, True)
        return aggregate


class LargeBurstProgressiveScanner(BurstProgressiveScanner):
    def scan(self):
        assert self.callback is not None
        aggregate = []
        for index in range(2048):
            root = SystemFile("large-root-{}".format(index), index)
            aggregate.append(root)
            self.callback([root], self.path_pair_id, self.path_pair_name, None, False)
        self.callback([], self.path_pair_id, self.path_pair_name, None, True)
        return aggregate


class PairBurstScanner(DummyScanner):
    def __init__(self, pair_id: str):
        self.path_pair_id = pair_id
        self.path_pair_name = pair_id
        self.callback = None

    def set_progress_callback(self, callback):
        self.callback = callback

    def scanned_path_pair_ids(self):
        return {self.path_pair_id}

    def scan(self):
        assert self.callback is not None
        aggregate = []
        for index in range(256):
            root = SystemFile("{}-root-{}".format(self.path_pair_id, index), index)
            root.path_pair_id = self.path_pair_id
            root.path_pair_name = self.path_pair_name
            aggregate.append(root)
            self.callback([root], self.path_pair_id, self.path_pair_name, None, False)
        self.callback([], self.path_pair_id, self.path_pair_name, set(), True)
        return aggregate

    def set_base_logger(self, base_logger: logging.Logger):
        pass

    def export_recycled_state(self):
        return None

    def apply_recycled_state(self, state):
        if state is not None:
            raise TypeError("unexpected burst scanner state")


class SixPairBurstScanner(MultiPathRemoteScanner):
    def __init__(self):
        super().__init__([PairBurstScanner("pair-{}".format(index)) for index in range(1, 7)])


class RecoverablePartialScanner(DummyScanner):
    def scan(self):
        raise ScannerError("recoverable child error", recoverable=True, files=[SystemFile("partial", 1)])


class RecoverableSelectedPairScanner(DummyScanner):
    def __init__(self):
        self.callback = None

    def set_progress_callback(self, callback):
        self.callback = callback

    def scanned_path_pair_ids(self):
        return {"pair-a", "pair-b"}

    def failed_path_pair_ids(self):
        return {"pair-b"}

    def scan(self):
        assert self.callback is not None
        self.callback([SystemFile("healthy", 1)], "pair-a", "Pair A", None, False)
        self.callback([], "pair-a", "Pair A", None, True)
        raise ScannerError("one pair failed", recoverable=True)


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


class DiagnosticRecycledScanner(DummyScanner):
    path_pair_id = "pair"
    path_pair_name = "Pair"

    def __init__(self):
        self.__diagnostics = None

    def set_performance_diagnostics(self, diagnostics):
        self.__diagnostics = diagnostics

    def scan(self):
        for metric in (
            DURATION_REMOTE_SCAN_TRANSPORT_READ,
            DURATION_REMOTE_SCAN_STREAM_PARSING,
            DURATION_REMOTE_SCAN_AGGREGATION,
            DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION,
        ):
            started = self.__diagnostics.begin_duration(metric)
            self.__diagnostics.finish_duration(metric, started)
        return [SystemFile("diagnostic", 1)]


class DelayedDiagnosticRecycledScanner(DiagnosticRecycledScanner):
    def scan(self):
        time.sleep(0.4)
        return super().scan()


class _InspectingWakeEvent:
    def __init__(self):
        self.run_loop_locals: set[str] = set()

    def wait(self, timeout: float) -> None:
        caller = inspect.currentframe().f_back
        assert caller is not None
        self.run_loop_locals = set(caller.f_locals)

    def clear(self) -> None:
        pass


class _DelayedExitWorker:
    def __init__(self):
        self.pid = 1
        self.alive = True

    def join(self, timeout=None) -> None:
        pass

    def is_alive(self) -> bool:
        return self.alive

    def close(self) -> None:
        pass


class _MessageConnection:
    def __init__(self, messages):
        self.messages = deque(messages)

    def poll(self, timeout=0.0) -> bool:
        return bool(self.messages)

    def recv(self):
        return self.messages.popleft()

    def close(self) -> None:
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

    def _wait_for_recycled_worker(self):
        deadline = time.monotonic() + 8
        while self.process._ScannerProcess__scan_worker is not None and time.monotonic() < deadline:
            self.process.run_loop()
            time.sleep(0.01)
        self.assertIsNone(self.process._ScannerProcess__scan_worker)
        self.assertIsNotNone(self.process.pop_latest_result())

    def test_result_publication_notifies_controller_wake_callback(self):
        wake = MagicMock()
        self.process = ScannerProcess(
            scanner=DummyScanner(),
            interval_in_ms=1000,
            result_available_callback=wake,
        )
        result = ScannerResult(datetime.now(), [])

        self.process._ScannerProcess__publish_result(result)

        wake.assert_called_once_with()
        self.assertIs(result, self.process.pop_latest_result())

    def test_latest_result_pop_is_bounded_when_queue_is_replenished(self):
        class ReplenishedQueue:
            def __init__(self):
                self.calls = 0

            def get(self, block=False):
                self.calls += 1
                return ScannerResult(
                    datetime.now(), [SystemFile("root-{}".format(self.calls), self.calls)],
                    generation=self.calls,
                )

        process = object.__new__(ScannerProcess)
        process.logger = MagicMock()
        queue = ReplenishedQueue()
        process._ScannerProcess__queue = queue

        latest = process.pop_latest_result(max_items=7)

        self.assertIsNotNone(latest)
        self.assertEqual(7, queue.calls)
        self.assertEqual(7, latest.generation)

        # The producer remains active, but each subsequent controller tick
        # still consumes only its own bounded window and sees that window's
        # newest snapshot.
        latest = process.pop_latest_result(max_items=7)
        self.assertEqual(14, queue.calls)
        self.assertEqual(14, latest.generation)

    def test_pending_results_tracks_drained_queue_during_put_get_interleaving(self):
        process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)
        result_queue = process._ScannerProcess__queue
        self.assertIsNotNone(result_queue)
        enqueued = threading.Event()
        allow_put_return = threading.Event()
        original_put = queue.Queue.put

        def delayed_put(instance, item, block=True, timeout=None):
            result = original_put(instance, item, block=block, timeout=timeout)
            if instance is result_queue:
                enqueued.set()
                self.assertTrue(allow_put_return.wait(2))
            return result

        result = ScannerResult(datetime.now(), [])
        with patch.object(queue.Queue, "put", delayed_put):
            producer = threading.Thread(target=result_queue.put, args=(result,))
            producer.start()
            self.assertTrue(enqueued.wait(2))
            self.assertIs(result, process.pop_latest_result())
            allow_put_return.set()
            producer.join(2)

        self.assertFalse(producer.is_alive())
        self.assertFalse(process.has_pending_results())

    def test_bounded_pop_results_keeps_final_snapshot_pending_until_later_tick(self):
        process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)
        process._ScannerProcess__queue.maxsize = 256
        ordinary = [
            ScannerResult(
                datetime.now(), [SystemFile("root-{}".format(index), index)],
                scanned_path_pair_ids={"pair"}, is_progress=True, is_scan_final=False,
            )
            for index in range(130)
        ]
        final = ScannerResult(
            datetime.now(), [SystemFile("final", 1)], scanned_path_pair_ids={"pair"},
            is_progress=True, is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
        )
        for result in ordinary + [final]:
            process._ScannerProcess__publish_result(result)

        first_batch = process.pop_results()

        self.assertEqual(128, len(first_batch))
        self.assertTrue(process.has_pending_results())
        second_batch = process.pop_results()
        self.assertIn(final, second_batch)
        self.assertFalse(process.has_pending_results())

    def test_release_marker_counts_toward_bounded_pop_and_does_not_hide_final_snapshot(self):
        process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)
        process._ScannerProcess__queue.maxsize = 256
        process._ScannerProcess__queue.put_nowait(_ScannerQueueReleaseMarker())
        ordinary = [
            ScannerResult(
                datetime.now(), [SystemFile("root-{}".format(index), index)],
                scanned_path_pair_ids={"pair"}, is_progress=True, is_scan_final=False,
            )
            for index in range(128)
        ]
        final = ScannerResult(
            datetime.now(), [SystemFile("final", 1)], scanned_path_pair_ids={"pair"},
            is_progress=True, is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
        )
        for result in ordinary + [final]:
            process._ScannerProcess__publish_result(result)

        first_batch = process.pop_results()

        self.assertEqual(127, len(first_batch))
        self.assertTrue(process.has_pending_results())
        second_batch = process.pop_results()
        self.assertIn(final, second_batch)
        self.assertFalse(process.has_pending_results())

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
            started = next(entry for entry in entries if entry["message"] == "scan_started")
            completed = next(entry for entry in entries if entry["message"] == "scan_completed")
            published = next(entry for entry in entries if entry["message"] == "scan_result_published")
            self.assertEqual(started["corr_id"], completed["corr_id"])
            self.assertEqual(started["corr_id"], published["corr_id"])
        finally:
            if self.process.is_alive():
                self.process.terminate()
                self.process.join()
            self.process.close_queues()
            self.process = None
            mp_logger.stop()

    def test_scan_breadcrumb_gate_skips_lazy_details_and_preserves_enabled_emission(self):
        scanner = DummyScanner()
        disabled = BreadcrumbTraceCollector(
            lambda: True, max_entries=16, policy={"default": "off"},
        )
        details = MagicMock(side_effect=AssertionError("disabled scan breadcrumb built details"))
        _record_scan_breadcrumb(scanner, disabled.create_emitter(), "flow-id", "scan_started", details)
        details.assert_not_called()
        self.assertEqual([], disabled.snapshot()["entries"])

        enabled = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        _record_scan_breadcrumb(
            scanner, enabled.create_emitter(), "flow-id", "scan_started",
            lambda: {"generation": 1, "session_digest": "opaque"},
        )
        entry = enabled.snapshot()["entries"][0]
        self.assertEqual("scanner_process", entry["category"])
        self.assertEqual("scan_started", entry["message"])

    def test_spawned_remote_duration_aggregates_reach_parent_collector(self):
        self._scan_run_patcher.stop()
        collector = PerformanceDiagnosticsCollector(lambda: True, sample_interval_seconds=1)
        self.process = ScannerProcess(
            scanner=DiagnosticRecycledScanner(),
            interval_in_ms=0,
            verbose=False,
            recycle_scan_worker=True,
            performance_diagnostics=collector,
        )

        self.process.run_loop()
        deadline = time.monotonic() + 8
        while self.process._ScannerProcess__scan_worker is not None and time.monotonic() < deadline:
            self.process.run_loop()
            time.sleep(0.01)
        self.assertIsNone(self.process._ScannerProcess__scan_worker)
        self.assertIsNotNone(self.process.pop_latest_result())

        self.assertTrue(collector.sample_if_due())
        sample = collector.snapshot()["samples"][-1]
        metrics = sample["stage_window"]["metrics"]
        for metric in (
            DURATION_REMOTE_SCAN_TRANSPORT_READ,
            DURATION_REMOTE_SCAN_STREAM_PARSING,
            DURATION_REMOTE_SCAN_AGGREGATION,
            DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION,
        ):
            self.assertEqual(1, metrics[metric]["count"])

    def test_remote_scanner_spawn_pickle_strips_parent_collector(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        first = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password=None,
            remote_port=22,
            remote_path_to_scan="/remote",
            local_path_to_scan_script="/scanfs",
            remote_path_to_scan_script="/scanfs",
            performance_diagnostics=collector,
        )
        second = RemoteScanner(
            remote_address="host",
            remote_username="user",
            remote_password=None,
            remote_port=22,
            remote_path_to_scan="/remote-two",
            local_path_to_scan_script="/scanfs",
            remote_path_to_scan_script="/scanfs",
            performance_diagnostics=collector,
        )

        restored = pickle.loads(pickle.dumps(MultiPathRemoteScanner(
            [first, second], performance_diagnostics=collector,
        )))

        self.assertIsNone(restored._MultiPathRemoteScanner__performance_diagnostics)
        for scanner in restored._MultiPathRemoteScanner__scanners:
            self.assertIsNone(scanner._RemoteScanner__performance_diagnostics)
            self.assertIsNone(scanner._RemoteScanner__ssh._Sshcp__performance_diagnostics)

    def test_disabled_spawned_remote_diagnostics_are_isolated(self):
        self._scan_run_patcher.stop()
        collector = PerformanceDiagnosticsCollector(lambda: False, sample_interval_seconds=1)
        self.process = ScannerProcess(
            scanner=DiagnosticRecycledScanner(),
            interval_in_ms=0,
            verbose=False,
            recycle_scan_worker=True,
            performance_diagnostics=collector,
        )

        self.process.run_loop()
        deadline = time.monotonic() + 8
        while self.process._ScannerProcess__scan_worker is not None and time.monotonic() < deadline:
            self.process.run_loop()
            time.sleep(0.01)
        self.assertIsNone(self.process._ScannerProcess__scan_worker)
        self.assertIsNotNone(self.process.pop_latest_result())
        self.assertFalse(collector.sample_if_due())
        self.assertEqual([], collector.snapshot()["samples"])

    def test_spawned_duration_aggregate_before_reset_is_fenced(self):
        self._scan_run_patcher.stop()
        collector = PerformanceDiagnosticsCollector(lambda: True, sample_interval_seconds=1)
        self.process = ScannerProcess(
            scanner=DelayedDiagnosticRecycledScanner(),
            interval_in_ms=0,
            verbose=False,
            recycle_scan_worker=True,
            performance_diagnostics=collector,
        )
        self.process.run_loop()
        collector.reset()
        self._wait_for_recycled_worker()
        self.assertTrue(collector.sample_if_due())
        metrics = collector.snapshot()["samples"][-1]["stage_window"]["metrics"]
        self.assertEqual(0, metrics[DURATION_REMOTE_SCAN_STREAM_PARSING]["count"])

        self.process.run_loop()
        self._wait_for_recycled_worker()
        deadline = time.monotonic() + 2
        sampled = collector.sample_if_due()
        while not sampled and time.monotonic() < deadline:
            time.sleep(0.01)
            sampled = collector.sample_if_due()
        self.assertTrue(sampled)
        metrics = collector.snapshot()["samples"][-1]["stage_window"]["metrics"]
        self.assertEqual(1, metrics[DURATION_REMOTE_SCAN_STREAM_PARSING]["count"])

    def test_spawned_duration_aggregate_is_fenced_across_disable_reenable(self):
        self._scan_run_patcher.stop()
        enabled = [True]
        collector = PerformanceDiagnosticsCollector(lambda: enabled[0], sample_interval_seconds=1)
        self.process = ScannerProcess(
            scanner=DelayedDiagnosticRecycledScanner(),
            interval_in_ms=0,
            verbose=False,
            recycle_scan_worker=True,
            performance_diagnostics=collector,
        )
        self.process.run_loop()
        enabled[0] = False
        self.assertFalse(collector.sample_if_due())
        self.assertFalse(collector.snapshot()["enabled"])
        enabled[0] = True
        self.assertTrue(collector.snapshot()["enabled"])
        self._wait_for_recycled_worker()
        self.assertTrue(collector.sample_if_due())
        metrics = collector.snapshot()["samples"][-1]["stage_window"]["metrics"]
        self.assertEqual(0, metrics[DURATION_REMOTE_SCAN_STREAM_PARSING]["count"])

    def test_bounded_progress_queue_does_not_deadlock_on_burst_termination(self):
        self.process = ScannerProcess(
            scanner=BurstProgressiveScanner(),
            interval_in_ms=0,
            verbose=False,
        )
        self.process.start()
        time.sleep(0.05)
        self.process.terminate()
        self.process.join(timeout=3)
        self.assertFalse(self.process.is_alive())

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

    def test_spawned_worker_finishes_after_large_progressive_full_snapshot(self):
        self._scan_run_patcher.stop()
        process = ScannerProcess(scanner=LargeBurstProgressiveScanner(), interval_in_ms=0, verbose=False,
                                 recycle_scan_worker=True)
        self.addCleanup(process.close_queues)

        process.run_loop()
        deadline = time.monotonic() + 8
        while process._ScannerProcess__scan_worker is not None and time.monotonic() < deadline:
            process.run_loop()
            time.sleep(0.01)

        self.assertIsNone(process._ScannerProcess__scan_worker)
        result = process.pop_latest_result()
        self.assertIsNotNone(result)
        self.assertTrue(result.is_full_snapshot)

    def test_spawned_six_pair_progress_survives_slow_parent_drain(self):
        self._scan_run_patcher.stop()
        process = ScannerProcess(scanner=SixPairBurstScanner(), interval_in_ms=0, verbose=False,
                                 recycle_scan_worker=True)
        self.addCleanup(process.close_queues)

        process.run_loop()
        # Let the child publish while the model-facing consumer is paused.
        # The coordinator still drains the control pipe into its bounded
        # local queue, dropping only intermediate progress under pressure.
        pause_deadline = time.monotonic() + 0.5
        while time.monotonic() < pause_deadline:
            process.run_loop()
            time.sleep(0.01)

        deadline = time.monotonic() + 8
        while process._ScannerProcess__scan_worker is not None and time.monotonic() < deadline:
            process.run_loop()
            time.sleep(0.01)
        self.assertIsNone(process._ScannerProcess__scan_worker)

        results = process.pop_results()
        final = [result for result in results if result.is_full_snapshot]
        self.assertTrue(final)
        self.assertEqual(6 * 256, len(final[-1].files))
        self.assertEqual({"pair-{}".format(index) for index in range(1, 7)},
                         {file.path_pair_id for file in final[-1].files})
        progress_completed = set().union(*(
            result.completed_path_pair_ids for result in results if not result.is_full_snapshot
        ))
        self.assertTrue({"pair-5", "pair-6"}.issubset(progress_completed))
        self.assertLessEqual(len(results), 128)

    def test_terminal_status_received_while_worker_alive_is_applied_after_final(self):
        scanner = StatefulRecycledScanner()
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False, recycle_scan_worker=True)
        self.addCleanup(process.close_queues)
        worker = _DelayedExitWorker()
        final = ScannerResult(
            datetime.now(),
            [SystemFile("authoritative", 7)],
            scanned_path_pair_ids={"pair"},
            is_progress=True,
            is_full_snapshot=True,
            full_snapshot_path_pair_ids={"pair"},
            completed_path_pair_ids={"pair"},
        )
        connection = _MessageConnection([
            ("result", final),
            ("success", None, False),
        ])
        process._ScannerProcess__scan_worker = worker
        process._ScannerProcess__scan_worker_started_at = datetime.now()
        process._ScannerProcess__scan_worker_control_connection = connection
        process._ScannerProcess__wake_event = _InspectingWakeEvent()

        process._ScannerProcess__poll_scan_worker()
        self.assertTrue(worker.alive)
        self.assertIsNotNone(process._ScannerProcess__scan_worker_pending_status)

        worker.alive = False
        process._ScannerProcess__poll_scan_worker()

        self.assertIsNone(process._ScannerProcess__scan_worker)
        self.assertFalse(scanner.first_run)
        results = process.pop_results()
        self.assertEqual(["authoritative"], [file.name for file in results[-1].files])

    def test_terminal_fatal_status_received_while_worker_alive_is_reraised(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=0, verbose=False, recycle_scan_worker=True)
        self.addCleanup(process.close_queues)
        worker = _DelayedExitWorker()

        class RaisingWrapper:
            def re_raise(self):
                raise ScannerError("delayed fatal")

        connection = _MessageConnection([("fatal", RaisingWrapper(), None)])
        process._ScannerProcess__scan_worker = worker
        process._ScannerProcess__scan_worker_started_at = datetime.now()
        process._ScannerProcess__scan_worker_control_connection = connection
        process._ScannerProcess__wake_event = _InspectingWakeEvent()

        process._ScannerProcess__poll_scan_worker()
        worker.alive = False
        with self.assertRaisesRegex(ScannerError, "delayed fatal"):
            process._ScannerProcess__poll_scan_worker()

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

    def test_force_scan_coalesces_requests_during_active_scan(self):
        started = threading.Event()
        release = threading.Event()
        active_lock = threading.Lock()
        active_count = 0
        max_active_count = 0
        scan_calls = 0

        def scan():
            nonlocal active_count, max_active_count, scan_calls
            scan_calls += 1
            with active_lock:
                active_count += 1
                max_active_count = max(max_active_count, active_count)
            started.set()
            self.assertTrue(release.wait(2))
            with active_lock:
                active_count -= 1
            return []

        scanner = DummyScanner()
        scanner.scan = scan
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)
        process.force_scan()
        worker = threading.Thread(target=process.run_loop)
        worker.start()
        self.assertTrue(started.wait(2))
        for path_pair_id in (None, "pair-a", "pair-a", "pair-b", None, "pair-c"):
            process.force_scan(path_pair_id)
        with process._ScannerProcess__scan_target_queue.mutex:
            self.assertEqual([None], list(process._ScannerProcess__scan_target_queue.queue))
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, max_active_count)
        process.run_loop()
        self.assertEqual(2, scan_calls)

    def test_prioritize_scan_interrupts_full_worker_then_schedules_selected_pair_and_full_followup(self):
        process = ScannerProcess(
            scanner=DummyScanner(), interval_in_ms=1000, verbose=False,
            recycle_scan_worker=True,
        )
        self.addCleanup(process.close_queues)
        worker = MagicMock()
        worker.pid = 1
        worker.is_alive.side_effect = [True, True, False, False]
        process._ScannerProcess__scan_worker = worker
        process._ScannerProcess__scan_worker_started_at = datetime.now()
        process._ScannerProcess__scan_worker_control_connection = _MessageConnection([])
        process._ScannerProcess__scan_worker_target_path_pair_ids = None
        process._ScannerProcess__scan_generation = 1

        process.force_scan()
        process.prioritize_scan("pair-6")
        process._ScannerProcess__poll_scan_worker()

        worker.terminate.assert_called_once_with()
        self.assertEqual({"pair-6"}, process._ScannerProcess__drain_priority_target_path_pair_ids())
        self.assertTrue(process._ScannerProcess__priority_requires_full_followup)

        completed_worker = MagicMock()
        completed_worker.pid = 2
        completed_worker.is_alive.return_value = False
        process._ScannerProcess__scan_worker = completed_worker
        process._ScannerProcess__scan_worker_started_at = datetime.now()
        process._ScannerProcess__scan_worker_control_connection = _MessageConnection([])
        process._ScannerProcess__scan_worker_target_path_pair_ids = {"pair-6"}
        process._ScannerProcess__poll_scan_worker()

        self.assertFalse(process._ScannerProcess__priority_requires_full_followup)
        self.assertIsNone(process._ScannerProcess__drain_scan_target_path_pair_ids())

    def test_priority_target_is_drained_before_queued_full_scan(self):
        process = ScannerProcess(
            scanner=DummyScanner(), interval_in_ms=1000, verbose=False,
            recycle_scan_worker=True,
        )
        self.addCleanup(process.close_queues)

        process.force_scan()
        process.prioritize_scan("pair-6")

        self.assertEqual({"pair-6"}, process._ScannerProcess__drain_priority_target_path_pair_ids())
        self.assertIsNone(process._ScannerProcess__drain_scan_target_path_pair_ids())

    def test_inline_full_scan_reserves_selected_pair_inside_active_generation(self):
        scanner = DummyScanner()
        scanner.prioritize_path_pair = MagicMock()
        process = ScannerProcess(scanner=scanner, interval_in_ms=1000, verbose=False)
        self.addCleanup(process.close_queues)
        process._ScannerProcess__inline_scan_active.set()
        process._ScannerProcess__inline_scan_target_path_pair_ids = None

        process.prioritize_scan("pair-6")

        scanner.prioritize_path_pair.assert_called_once_with("pair-6")
        self.assertFalse(process._ScannerProcess__has_pending_priority_targets())

    def test_inline_initial_priority_runs_target_then_skips_interval_for_full_followup(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=1000, verbose=False)
        self.addCleanup(process.close_queues)
        process.prioritize_scan("pair-6")

        started_at = time.monotonic()
        process.run_loop()

        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertFalse(process._ScannerProcess__priority_requires_full_followup)
        self.assertIsNone(process._ScannerProcess__scan_target_queue.get_nowait())

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

    def test_progressive_scan_keeps_manifest_and_root_events_in_order(self):
        process = ScannerProcess(scanner=ProgressiveScanner(), interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.run_loop()
        results = process.pop_results()

        self.assertGreaterEqual(len(results), 4)
        self.assertEqual({"a", "b"}, results[0].root_names)
        self.assertEqual(["a"], [file.name for file in results[1].files])
        self.assertEqual({"pair"}, results[-1].completed_path_pair_ids)
        self.assertTrue(all(result.generation == 1 for result in results))
        self.assertIsNone(process._ScannerProcess__scan_worker)

    def test_progressive_scan_carries_model_owned_unchanged_root_hint_to_full_snapshot(self):
        scanner = FingerprintProgressiveScanner()
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)
        fingerprint = "a" * 64

        process.set_accepted_root_fingerprints({"pair": {"a": fingerprint}})
        process.run_loop()
        results = process.pop_results()

        self.assertEqual({"pair": {"a": fingerprint}}, scanner.accepted)
        marker_events = [result for result in results if result.unchanged_root_fingerprints]
        self.assertTrue(marker_events)
        self.assertTrue(all(result.unchanged_root_fingerprints == {"a": fingerprint}
                            for result in marker_events))
        final = next(result for result in results if result.is_full_snapshot)
        self.assertEqual({"pair": {"a": fingerprint}}, final.unchanged_root_fingerprints_by_pair)

    def test_forced_successor_scan_uses_hint_staged_after_queued_scan_admission(self):
        scanner = BlockingFingerprintScanner()
        process = ScannerProcess(scanner=scanner, interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)
        old = {"pair": {"root": "old"}}
        current = {"pair": {"root": "current"}}
        process.set_accepted_root_fingerprints(old)

        admitted_scan = threading.Thread(target=process.run_loop)
        admitted_scan.start()
        self.assertTrue(scanner.started.wait(timeout=1))

        # ModelUpdater drains the queued result then stages its newly accepted
        # fingerprints while a forced successor is waiting to begin.  This
        # must not mutate the already-admitted scan's scanner instance.
        process.set_accepted_root_fingerprints(current)
        self.assertEqual(old, scanner.accepted)
        scanner.release.set()
        admitted_scan.join(timeout=1)
        self.assertFalse(admitted_scan.is_alive())

        process.force_scan()
        process.run_loop()

        self.assertEqual([old, current], scanner.observed)

    def test_spawned_scan_handoff_keeps_admitted_hint_and_updates_successor(self):
        self._scan_run_patcher.stop()
        scanner = SpawnedFingerprintScanner()
        self.addCleanup(scanner.observed.join_thread)
        self.addCleanup(scanner.observed.close)
        process = ScannerProcess(
            scanner=scanner,
            interval_in_ms=0,
            verbose=False,
            recycle_scan_worker=True,
        )
        self.addCleanup(process.close_queues)
        old = {"pair": {"root": "old"}}
        current = {"pair": {"root": "current"}}
        process.set_accepted_root_fingerprints(old)

        process.run_loop()
        self.assertTrue(scanner.started.wait(timeout=2))

        # The first child has already crossed the admission boundary.  A
        # concurrent model update must stage for the successor, not mutate it.
        process.set_accepted_root_fingerprints(current)
        self.assertEqual(old, scanner.accepted)
        scanner.release.set()

        deadline = time.monotonic() + 5
        while process._ScannerProcess__scan_worker is not None and time.monotonic() < deadline:
            process.run_loop()
        self.assertIsNone(process._ScannerProcess__scan_worker)
        self.assertEqual(old, scanner.observed.get(timeout=2))

        process.force_scan()
        process.run_loop()
        deadline = time.monotonic() + 5
        while process._ScannerProcess__scan_worker is not None and time.monotonic() < deadline:
            process.run_loop()
        self.assertIsNone(process._ScannerProcess__scan_worker)
        self.assertEqual(current, scanner.observed.get(timeout=2))

    def test_multi_path_scanner_routes_root_fingerprints_only_to_matching_pair(self):
        first = MagicMock()
        first.path_pair_id = "pair-a"
        second = MagicMock()
        second.path_pair_id = "pair-b"
        scanner = MultiPathRemoteScanner([first, second])
        fingerprints = {
            "pair-a": {"root-a": "a" * 64},
            "pair-b": {"root-b": "b" * 64},
            "unrelated": {"root-c": "c" * 64},
        }

        scanner.set_accepted_root_fingerprints(fingerprints)

        first.set_accepted_root_fingerprints.assert_called_once_with(fingerprints["pair-a"])
        second.set_accepted_root_fingerprints.assert_called_once_with(fingerprints["pair-b"])

    def test_burst_progressive_queue_preserves_lossless_final_snapshot(self):
        process = ScannerProcess(scanner=BurstProgressiveScanner(), interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)

        process.run_loop()
        results = process.pop_results()
        final_results = [result for result in results if result.is_full_snapshot]

        self.assertEqual(2, len(final_results))
        final = final_results[0]
        self.assertTrue(final.is_scan_final)
        self.assertEqual({"pair"}, final.full_snapshot_path_pair_ids)
        self.assertEqual({"root-{}".format(index) for index in range(512)},
                         {file.name for file in final.files})
        completion_index = next(
            index for index, result in enumerate(results)
            if result.completed_path_pair_ids == {"pair"} and not result.is_full_snapshot
        )
        self.assertLess(completion_index, results.index(final))

    def test_bounded_queue_preserves_pair_completion_markers_before_full_snapshot(self):
        bounded_queue = queue.Queue(maxsize=3)

        def ordinary(name):
            return ScannerResult(
                datetime.now(), [SystemFile(name, 1)], scanned_path_pair_ids={"pair"},
                is_progress=True, is_scan_final=False,
            )

        def completion(pair_id):
            return ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={pair_id},
                is_progress=True, completed_path_pair_ids={pair_id}, is_scan_final=False,
            )

        for index in range(3):
            _publish_bounded_result(bounded_queue, ordinary("root-{}".format(index)))
        _publish_bounded_result(bounded_queue, completion("pair-a"))
        _publish_bounded_result(bounded_queue, completion("pair-b"))
        _publish_bounded_result(bounded_queue, ordinary("discarded-root"))

        retained = list(bounded_queue.queue)
        self.assertEqual(
            {"pair-a", "pair-b"},
            set().union(*(result.completed_path_pair_ids for result in retained)),
        )
        self.assertEqual(1, len([result for result in retained if result.files]))

        full_snapshot = ScannerResult(
            datetime.now(), [SystemFile("authoritative", 10)], scanned_path_pair_ids={"pair"},
            is_progress=True, completed_path_pair_ids={"pair"},
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
        )
        _publish_bounded_result(bounded_queue, full_snapshot)
        _publish_bounded_result(bounded_queue, ordinary("still-discarded"))

        retained = list(bounded_queue.queue)
        self.assertIn(full_snapshot, retained)
        self.assertEqual(
            {"pair-a", "pair-b"},
            set().union(*(
                result.completed_path_pair_ids
                for result in retained if not result.is_full_snapshot
            )),
        )

        protected_only_queue = queue.Queue(maxsize=2)
        _publish_bounded_result(protected_only_queue, completion("pair-a"))
        _publish_bounded_result(protected_only_queue, completion("pair-b"))
        _publish_bounded_result(protected_only_queue, full_snapshot)

        retained = list(protected_only_queue.queue)
        self.assertIn(full_snapshot, retained)
        self.assertEqual(2, len(retained))

    def test_bounded_queue_retries_authoritative_publish_after_concurrent_drain(self):
        class FullThenDrainedQueue:
            def __init__(self):
                self.put_attempts = 0
                self.published = []

            def put_nowait(self, item):
                self.put_attempts += 1
                if self.put_attempts == 1:
                    raise queue.Full
                self.published.append(item)

            @staticmethod
            def get_nowait():
                # Simulate another consumer emptying the queue after the
                # producer observed Full but before it could inspect entries.
                raise queue.Empty

        output_queue = FullThenDrainedQueue()
        final = ScannerResult(
            datetime.now(), [SystemFile("authoritative", 10)], scanned_path_pair_ids={"pair"},
            is_progress=True, completed_path_pair_ids={"pair"},
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
        )

        _publish_bounded_result(output_queue, final)

        self.assertEqual(2, output_queue.put_attempts)
        self.assertEqual([final], output_queue.published)

    def test_bounded_queue_keeps_unrelated_failure_through_repeated_targeted_full_snapshots(self):
        bounded_queue = queue.Queue(maxsize=128)
        failed_pair = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
            failed=True, unknown_path_pair_ids={"pair-b"}, is_progress=True,
            is_targeted_scan=True,
        )
        _publish_bounded_result(bounded_queue, failed_pair)

        for generation in range(1, 131):
            targeted_final = ScannerResult(
                datetime.now(), [SystemFile("pair-a-{}".format(generation), generation)],
                scanned_path_pair_ids={"pair-a"}, generation=generation,
                is_progress=True, completed_path_pair_ids={"pair-a"},
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
                is_targeted_scan=True,
            )
            _publish_bounded_result(bounded_queue, targeted_final)

        retained = list(bounded_queue.queue)
        self.assertIn(failed_pair, retained)
        self.assertTrue(any(
            result.full_snapshot_path_pair_ids == {"pair-a"} and result.generation == 130
            for result in retained
        ))

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
        self.assertEqual({None}, result.recoverable_failure_path_pair_ids)
        self.assertEqual("recoverable error", result.error_message)
        process.logger.warning.assert_called_once()
        warning_message = process.logger.warning.call_args[0][0]
        self.assertIn("recoverable error", warning_message)
        self.assertIn("failed result", warning_message.lower())

    def test_scanner_result_preserves_legacy_positional_error_message_binding(self):
        result = ScannerResult(
            datetime.now(), [], [], [], {"pair"}, True, "legacy error", 17,
        )

        self.assertEqual(set(), result.recoverable_failure_path_pair_ids)
        self.assertIsNone(result.terminal_failure_path_pair_ids)
        self.assertEqual("legacy error", result.error_message)
        self.assertEqual(17, result.generation)

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

    def test_recoverable_error_reports_only_failed_path_pairs(self):
        process = ScannerProcess(
            scanner=RecoverableSelectedPairScanner(),
            interval_in_ms=100,
            verbose=False,
        )
        process.run_loop()
        result = process.pop_latest_result()

        self.assertTrue(result.failed)
        self.assertEqual({"pair-b"}, result.scanned_path_pair_ids)
        self.assertEqual({"pair-b"}, result.unknown_path_pair_ids)

    def test_recoverable_multi_pair_scan_publishes_lossless_snapshot_for_completed_pair(self):
        process = ScannerProcess(
            scanner=RecoverableSelectedPairScanner(),
            interval_in_ms=100,
            verbose=False,
        )
        self.addCleanup(process.close_queues)

        process.run_loop()
        results = process.pop_results()

        healthy = [
            result for result in results
            if result.is_full_snapshot and result.full_snapshot_path_pair_ids == {"pair-a"}
        ]
        self.assertEqual(1, len(healthy))
        self.assertEqual(["healthy"], [file.name for file in healthy[0].files])
        failed = results[-1]
        self.assertTrue(failed.failed)
        self.assertEqual({"pair-b"}, failed.unknown_path_pair_ids)

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
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=100, verbose=False, recycle_scan_worker=True)

        process.close_queues()
        process.close_queues()

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
        while len(snapshot["entries"]) < 4 and time.monotonic() < deadline:
            time.sleep(0.001)
            snapshot = collector.snapshot()
        self.assertEqual(4, len(snapshot["entries"]))
        self.assertEqual(1, len({entry["corr_id"] for entry in snapshot["entries"][:3]}))
        self.assertEqual("movies", snapshot["entries"][3]["corr_id"])
        self.assertEqual([1, 2, 3, 4], [entry["version"] for entry in snapshot["entries"]])
        self.assertEqual(["scan", "scan", "scan", "extract"], [entry["stage"] for entry in snapshot["entries"]])
        self.assertEqual(["scan_started", "scan_completed", "scan_result_published", "extract_completed"],
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
