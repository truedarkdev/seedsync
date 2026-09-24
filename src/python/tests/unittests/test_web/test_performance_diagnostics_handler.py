import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta
from queue import Queue
from threading import Condition, Event, Lock, RLock, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common import BreadcrumbTraceCollector, Config, PerformanceDiagnosticsCollector
from common.breadcrumb_trace import BreadcrumbDurableSpool
from controller import Controller
from lftp import LftpJobStatus
from web.auth_store import ApiKeyStore
from web.handler.performance_diagnostics import PerformanceDiagnosticsHandler
from web.web_app import WebApp
from webtest import TestApp


def _controller() -> Controller:
    controller = Controller.__new__(Controller)
    controller.logger = MagicMock()
    controller._Controller__command_queue = Queue()
    controller._Controller__context = SimpleNamespace(breadcrumb_trace=None)
    controller._Controller__path_pair_runtime_error = None
    controller._Controller__path_pair_relocation_reservations = set()
    controller._Controller__process_wake_condition = Condition()
    controller._Controller__process_wake_generation = 0
    controller._Controller__work_state_lock = RLock()
    controller._Controller__pending_queue_dispatches = {}
    controller._Controller__deferred_queue_intents = {}
    controller._Controller__pending_command_dispatch_file_ids = set()
    controller._Controller__command_flow_lock = Lock()
    controller._Controller__active_command_processes = []
    controller._Controller__last_lftp_statuses = []
    controller._Controller__lftp_status_cache_expires_at = None
    controller._Controller__lftp_status_poll_retry_active = False
    controller._Controller__lftp_status_future = None
    controller._Controller__lftp_operations = []
    return controller


def _hold_lock(lock: object) -> tuple[Thread, Event]:
    acquired = Event()
    release = Event()

    def hold() -> None:
        lock.acquire()
        acquired.set()
        release.wait(2)
        lock.release()

    thread = Thread(target=hold)
    thread.start()
    if not acquired.wait(1):
        release.set()
        thread.join(1)
        raise AssertionError("test lock holder did not acquire the lock")
    return thread, release


class TestPerformanceDiagnosticsRuntimeCounts(unittest.TestCase):
    def test_counts_real_admission_drain_and_dispatch_handoff_separately(self):
        controller = _controller()
        command = Controller.Command(Controller.Command.Action.QUEUE, "private-file-id")
        controller.queue_command(command)
        controller._Controller__pending_queue_dispatches = {"pending": object()}
        controller._Controller__deferred_queue_intents = {"deferred": object()}

        with patch.object(controller._Controller__command_queue, "qsize", side_effect=AssertionError("qsize used")):
            counts = controller.get_performance_diagnostic_runtime_counts()

        self.assertTrue(counts["command_queue"]["known"])
        self.assertEqual(1, counts["command_queue"]["queued"])
        self.assertEqual(1, counts["dispatch"]["pending_queue_dispatches"])
        self.assertEqual(1, counts["dispatch"]["deferred_queue_intents"])
        self.assertEqual(0, counts["dispatch"]["pending_command_dispatches"])
        self.assertFalse(counts["lftp_operations"]["known"])
        self.assertEqual("unsynchronized_registry", counts["lftp_operations"]["reason"])
        self.assertNotIn("private-file-id", json.dumps(counts))

        dequeued = controller._Controller__command_queue.get_nowait()
        self.assertIs(command, dequeued)
        self.assertTrue(controller._Controller__begin_command_dispatch(command.filename, None))
        drained = controller.get_performance_diagnostic_runtime_counts()
        self.assertEqual(0, drained["command_queue"]["queued"])
        self.assertEqual(1, drained["dispatch"]["pending_queue_dispatches"])
        self.assertEqual(1, drained["dispatch"]["pending_command_dispatches"])
        controller._Controller__end_command_dispatch(command.filename)
        self.assertEqual(0, controller.get_performance_diagnostic_runtime_counts()["dispatch"]["pending_command_dispatches"])

    def test_busy_component_locks_return_unknown_without_waiting(self):
        controller = _controller()
        locks = [
            controller._Controller__command_queue.mutex,
            controller._Controller__work_state_lock,
            controller._Controller__command_flow_lock,
        ]
        holders = [_hold_lock(lock) for lock in locks]
        try:
            counts = controller.get_performance_diagnostic_runtime_counts()
        finally:
            for _, release in holders:
                release.set()
            for thread, _ in holders:
                thread.join(1)

        self.assertFalse(counts["command_queue"]["known"])
        self.assertFalse(counts["dispatch"]["known"])
        self.assertFalse(counts["active_commands"]["known"])

    def test_lftp_status_requires_fresh_healthy_cache_without_pending_status_future(self):
        controller = _controller()
        controller._Controller__last_lftp_statuses = [
            LftpJobStatus(-1, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "private-name", ""),
            LftpJobStatus(-2, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "another-private-name", ""),
        ]
        controller._Controller__lftp_status_cache_expires_at = datetime.now() + timedelta(seconds=30)
        controller._Controller__lftp_status_poll_retry_active = False
        controller._Controller__lftp = SimpleNamespace(last_status_poll_healthy=False)

        unhealthy = controller.get_performance_diagnostic_runtime_counts()["lftp_status"]
        self.assertFalse(unhealthy["known"])
        self.assertEqual("status_unhealthy", unhealthy["reason"])

        controller._Controller__lftp.last_status_poll_healthy = True

        fresh = controller.get_performance_diagnostic_runtime_counts()["lftp_status"]
        self.assertTrue(fresh["known"])
        self.assertEqual(1, fresh["queued"])
        self.assertEqual(1, fresh["running"])
        self.assertNotIn("private-name", json.dumps(fresh))

        controller._Controller__lftp_status_cache_expires_at = datetime.now() - timedelta(seconds=1)
        stale = controller.get_performance_diagnostic_runtime_counts()["lftp_status"]
        self.assertFalse(stale["known"])

        controller._Controller__lftp_status_cache_expires_at = datetime.now() + timedelta(seconds=30)
        controller._Controller__lftp_status_future = Future()
        inflight = controller.get_performance_diagnostic_runtime_counts()["lftp_status"]
        self.assertFalse(inflight["known"])
        self.assertEqual("status_poll_inflight_or_unharvested", inflight["reason"])


class TestPerformanceDiagnosticsHandler(unittest.TestCase):
    def test_authenticated_endpoint_composes_fixed_counts_and_trace_health(self):
        controller = _controller()
        controller._Controller__command_queue.put(
            Controller.Command(Controller.Command.Action.QUEUE, "private-file-id")
        )
        controller.get_memory_ownership_census = MagicMock(side_effect=AssertionError("graph census invoked"))
        controller._Controller__lftp = MagicMock()

        context = MagicMock()
        context.logger.getChild.return_value = MagicMock()
        context.args.html_path = "/tmp"
        context.config = Config()
        context.status = MagicMock()
        context.performance_diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        context.breadcrumb_trace = BreadcrumbTraceCollector(lambda: True)
        context.breadcrumb_trace.ingress_snapshot = MagicMock(side_effect=AssertionError("blocking ingress snapshot invoked"))
        context.breadcrumb_trace.durable_snapshot = MagicMock(side_effect=AssertionError("blocking durable snapshot invoked"))

        auth_store = ApiKeyStore()
        admin_secret = auth_store.create_api_key("unit-admin", ["admin"])["secret"]
        read_secret = auth_store.create_api_key("unit-reader", ["read"])["secret"]
        baseline_size = len(json.dumps(context.performance_diagnostics.snapshot()))
        web_app = WebApp(context, controller, auth_store=auth_store)
        PerformanceDiagnosticsHandler(context, controller).add_routes(web_app)
        client = TestApp(web_app)

        response = client.get(
            "/server/admin/performance-diagnostics/v1",
            extra_environ={"HTTP_AUTHORIZATION": "Bearer " + admin_secret},
        )
        payload = response.json

        self.assertEqual(200, response.status_int)
        self.assertEqual(1, payload["runtime_counts"]["command_queue"]["queued"])
        self.assertFalse(payload["runtime_counts"]["lftp_operations"]["known"])
        self.assertEqual(0, payload["trace_health"]["ingress"]["critical_rejected"])
        self.assertIn("critical_lost", payload["trace_health"]["durable"])
        self.assertLess(len(response.body) - baseline_size, 4096)
        self.assertNotIn("private-file-id", response.text)
        controller.get_memory_ownership_census.assert_not_called()
        controller._Controller__lftp.assert_not_called()
        denied = client.get(
            "/server/admin/performance-diagnostics/v1",
            extra_environ={"HTTP_AUTHORIZATION": "Bearer " + read_secret},
            status=403,
        )
        self.assertEqual(403, denied.status_int)


class TestBreadcrumbDiagnosticProjection(unittest.TestCase):
    def test_projects_loss_counters_without_records_or_private_metadata(self):
        collector = BreadcrumbTraceCollector(lambda: True)
        critical = collector._BreadcrumbTraceCollector__critical_ingress_rejected_count
        normal = collector._BreadcrumbTraceCollector__normal_ingress_rejected_count
        with critical.get_lock():
            critical.value = 3
        with normal.get_lock():
            normal.value = 2
        collector._BreadcrumbTraceCollector__ingress_unresolved = 4
        collector.record("private-source", "private-message", {"path": "C:/private/item"})

        health = collector.performance_diagnostic_health_snapshot()

        self.assertTrue(health["ingress"]["known"])
        self.assertFalse(health["ingress"]["pending_known"])
        self.assertEqual(3, health["ingress"]["critical_rejected"])
        self.assertEqual(2, health["ingress"]["normal_rejected"])
        self.assertEqual(4, health["ingress"]["unresolved"])
        self.assertNotIn("private-source", json.dumps(health))
        self.assertNotIn("private-message", json.dumps(health))
        self.assertNotIn("C:/private/item", json.dumps(health))
        collector_lock = collector._BreadcrumbTraceCollector__lock
        thread, release = _hold_lock(collector_lock)
        try:
            busy = collector.performance_diagnostic_health_snapshot()
        finally:
            release.set()
            thread.join(1)
        self.assertFalse(busy["ingress"]["known"])
        self.assertEqual("collector_busy", busy["ingress"]["reason"])

        collector._BreadcrumbTraceCollector__critical_ingress_rejected_count = object()
        failed = collector.performance_diagnostic_health_snapshot()
        self.assertFalse(failed["ingress"]["known"])
        self.assertEqual("counter_read_failed", failed["ingress"]["reason"])
        lifecycle_lock = collector._BreadcrumbTraceCollector__durable_lifecycle_lock
        lifecycle_lock.acquire()
        try:
            health = collector.performance_diagnostic_health_snapshot()
        finally:
            lifecycle_lock.release()
        self.assertFalse(health["durable"]["known"])
        self.assertEqual("durable_lifecycle_busy", health["durable"]["reason"])
        absent = collector.performance_diagnostic_health_snapshot()["durable"]
        self.assertFalse(absent["known"])
        self.assertIsNone(absent["lost"])
        self.assertEqual("durable_cache_incomplete", absent["reason"])

        collector._BreadcrumbTraceCollector__durable_last_snapshot = {"lost": 0}
        incomplete = collector.performance_diagnostic_health_snapshot()["durable"]
        self.assertFalse(incomplete["known"])
        self.assertIsNone(incomplete["accepted"])
        self.assertEqual("durable_cache_incomplete", incomplete["reason"])

    def test_durable_loss_counters_are_projected_from_the_spool_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = BreadcrumbDurableSpool(os.path.join(directory, "trace.jsonl"))
            collector = BreadcrumbTraceCollector(lambda: True)
            collector._BreadcrumbTraceCollector__durable_spool = spool
            try:
                spool._BreadcrumbDurableSpool__record_loss(True)
                spool._BreadcrumbDurableSpool__record_loss(False)

                health = collector.performance_diagnostic_health_snapshot()["durable"]

                self.assertTrue(health["known"])
                self.assertEqual(2, health["lost"])
                self.assertEqual(1, health["critical_lost"])
                self.assertEqual(1, health["normal_lost"])
                self.assertNotIn(directory, json.dumps(health))

                with spool._BreadcrumbDurableSpool__lock:
                    del spool._BreadcrumbDurableSpool__health["lost"]
                incomplete = collector.performance_diagnostic_health_snapshot()["durable"]
                self.assertFalse(incomplete["known"])
                self.assertIsNone(incomplete["lost"])
                self.assertEqual("durable_health_incomplete", incomplete["reason"])

                spool_lock = spool._BreadcrumbDurableSpool__lock
                thread, release = _hold_lock(spool_lock)
                try:
                    busy = collector.performance_diagnostic_health_snapshot()["durable"]
                finally:
                    release.set()
                    thread.join(1)
                self.assertFalse(busy["known"])
                self.assertEqual("durable_spool_busy", busy["reason"])
            finally:
                spool.close(0.5)


if __name__ == "__main__":
    unittest.main()
