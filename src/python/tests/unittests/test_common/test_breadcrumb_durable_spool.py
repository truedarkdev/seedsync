import json
import multiprocessing
import os
import queue
import stat
import tempfile
import threading
import time
import unittest
from collections.abc import Mapping
from unittest.mock import patch

from common import ServiceExit
from common.breadcrumb_trace import (
    BREADCRUMB_DURABLE_BATCH_SIZE_MAX,
    BREADCRUMB_DURABLE_FLUSH_INTERVAL_MAX_SECONDS,
    BREADCRUMB_INGRESS_CRITICAL_BURST,
    BREADCRUMB_INGRESS_NORMAL_CAPACITY,
    BreadcrumbDurableSpool,
    BreadcrumbTraceCollector,
    BreadcrumbTraceNoopEmitter,
    _BREADCRUMB_INGRESS_INFLIGHT_CAPACITY,
    _BREADCRUMB_LIFECYCLE_CLOSED,
    _BREADCRUMB_LIFECYCLE_CLOSING,
    _DURABLE_RETENTION_SCAN_LIMIT,
    _bounded_ingress_record,
    is_critical_breadcrumb,
    trace_session_digest,
)
from seedsync import Seedsync


def _emit_worker_boundary(emitter):
    emitter.record("controller", "worker_boundary", category="queue.lifecycle")


class TestBreadcrumbDurableSpool(unittest.TestCase):
    def test_classifier_keeps_progress_and_root_shape_normal(self):
        self.assertFalse(is_critical_breadcrumb(
            "model_updater", "progress", category="model.progress",
        ))
        self.assertFalse(is_critical_breadcrumb(
            "scanner", "root_shape", category="scan.root_shape",
        ))
        self.assertTrue(is_critical_breadcrumb(
            "scanner", "scan_failed", category="scanner_process",
            event_type="failure",
        ))
        self.assertTrue(is_critical_breadcrumb(
            "controller", "queue_started", category="queue.lifecycle",
        ))
        self.assertTrue(is_critical_breadcrumb(
            "controller", "warning", category="model.progress", level="warning",
        ))
        self.assertFalse(is_critical_breadcrumb(
            "model_builder", "excluded", category="queue.exclusion",
        ))
        self.assertFalse(is_critical_breadcrumb(
            "controller", "ready", category="queue.readiness",
        ))
        self.assertFalse(is_critical_breadcrumb(
            "lftp", "poll", category="transfer.lftp.status",
        ))
        self.assertFalse(is_critical_breadcrumb(
            "scanner", "scan_result_published", category="scanner_process",
            details={"final": False, "progress": True},
        ))
        self.assertTrue(is_critical_breadcrumb(
            "scanner", "scan_result_published", category="scanner_process",
            details={"final": True},
        ))
        self.assertTrue(is_critical_breadcrumb(
            "lftp", "membership", category="transfer.lftp.membership",
        ))
        self.assertTrue(is_critical_breadcrumb(
            "lftp", "lifecycle", category="transfer.lftp",
        ))
        self.assertFalse(is_critical_breadcrumb(
            "lftp", "lftp_status_poll", category="transfer.lftp",
        ))
        self.assertFalse(is_critical_breadcrumb(
            "lftp", "arbitrary", category="transfer.lftp",
        ))

    def test_default_off_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(lambda: True, durable_path=path)
            emitter = collector.create_emitter()
            emitter.record("controller", "normal")
            time.sleep(0.05)
            self.assertEqual([], os.listdir(path))
            collector.close()

    def test_accepted_record_is_durable_after_memory_eviction(self):
        if os.name == "nt":
            self.skipTest("Windows durable file acknowledgement is buffered/unverified")
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                max_entries=1,
                durable_enabled=True,
                durable_path=path,
            )
            collector.record("controller", "first", category="queue.lifecycle")
            collector.record("controller", "second", category="queue.lifecycle")
            self.assertTrue(collector.flush_durable(2.0))
            with open(os.path.join(path, "breadcrumbs.jsonl"), encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]
            ordinary = [record for record in records if "message" in record]
            self.assertEqual(["first", "second"], [record["message"] for record in ordinary])
            self.assertEqual(1, collector.snapshot()["entry_count"])
            health = collector.durable_snapshot()
            self.assertEqual(2, health["written"])
            self.assertEqual(0, health["lost"])
            self.assertIn("upstream_critical_ingress_rejected", health)
            self.assertIn("upstream_normal_ingress_overflow", health)
            self.assertIn("upstream_collector_accepted", health)
            collector.close()

    def test_lane_capacity_and_critical_reservation_are_explicit(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(
                path,
                total_capacity=4,
                critical_capacity=2,
                normal_capacity=2,
                batch_size=1,
                flush_interval_seconds=10,
            )
            self.assertEqual(4, spool.total_capacity)
            self.assertEqual(2, spool.critical_capacity)
            self.assertEqual(2, spool.normal_capacity)
            self.assertEqual(BREADCRUMB_INGRESS_CRITICAL_BURST, 8)
            self.assertLessEqual(spool.critical_capacity + spool.normal_capacity, spool.total_capacity)
            spool.close(timeout=0.1)

    def test_direct_constructor_clamps_batch_and_flush_configuration(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(
                path,
                total_capacity=4,
                critical_capacity=1,
                normal_capacity=3,
                batch_size=10_000,
                flush_interval_seconds=10_000.0,
            )
            self.assertEqual(BREADCRUMB_DURABLE_BATCH_SIZE_MAX, spool.batch_size)
            self.assertEqual(
                BREADCRUMB_DURABLE_FLUSH_INTERVAL_MAX_SECONDS,
                spool.flush_interval_seconds,
            )
            spool.close(timeout=1.0)

        with tempfile.TemporaryDirectory() as path:
            for invalid_interval in (0, -1, float("inf"), float("nan"), "0.5"):
                with self.subTest(invalid_interval=invalid_interval):
                    with self.assertRaises(ValueError):
                        BreadcrumbDurableSpool(
                            path,
                            batch_size=1,
                            flush_interval_seconds=invalid_interval,
                        )
            with self.assertRaises(ValueError):
                BreadcrumbDurableSpool(path, batch_size=0)

    def test_normal_ingress_flood_retains_critical_capacity(self):
        collector = BreadcrumbTraceCollector(lambda: True)
        emitter = collector.create_emitter()
        for index in range(BREADCRUMB_INGRESS_NORMAL_CAPACITY + 400):
            emitter.record("scanner", "progress-{}".format(index), category="model.progress")
        self.assertEqual(
            BREADCRUMB_INGRESS_CRITICAL_BURST,
            collector.ingress_snapshot()["critical_burst"],
        )
        self.assertEqual(
            "enqueued",
            emitter.record("controller", "queue_boundary", category="queue.lifecycle"),
        )
        self.assertGreater(collector.ingress_snapshot()["critical_pending"], 0)
        collector.close()

    def test_health_sidecar_is_atomic_and_permissions_are_private(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
            spool.enqueue({"source": "scanner", "message": "failure", "event_type": "failure"}, True)
            spool.close(timeout=2.0)
            health_path = os.path.join(path, "breadcrumbs.health.json")
            health = None
            if os.path.exists(health_path):
                with open(health_path, encoding="utf-8") as handle:
                    health = json.load(handle)
                self.assertEqual("breadcrumb_durable_health.v1", health["schema"])
                self.assertNotIn("path", health)
                self.assertNotIn("last_error", health)
            elif os.name != "nt":
                self.fail("POSIX durable close must publish a health sidecar")
            else:
                self.assertEqual("buffered_best_effort", spool.snapshot()["durability"])
                self.assertEqual("windows_acl_unverified", spool.snapshot()["path_safety"])
            self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(path)))
            if health is not None:
                self.assertTrue(health["closed"] or health["incomplete"])
            if hasattr(os, "stat") and os.name != "nt":
                self.assertEqual(0o600, os.stat(health_path).st_mode & 0o777)
                self.assertEqual(0o700, os.stat(path).st_mode & 0o777)

    def test_rotation_retention_is_bounded_by_count_and_bytes(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(
                path,
                total_capacity=24,
                critical_capacity=8,
                normal_capacity=16,
                batch_size=1,
                flush_interval_seconds=0.01,
                rotate_bytes=80,
                max_files=3,
                max_bytes=200,
            )
            for index in range(20):
                spool.enqueue({"source": "worker", "message": "event", "index": index}, False)
            spool.close(timeout=2.0)
            rotated = [
                name for name in os.listdir(path)
                if name.startswith("breadcrumbs-") and name.endswith(".jsonl")
            ]
            self.assertLessEqual(len(rotated), 2)
            data_files = rotated + ["breadcrumbs.jsonl"]
            self.assertLessEqual(
                sum(os.path.getsize(os.path.join(path, name)) for name in data_files),
                200,
            )

    def test_writer_failure_uses_fixed_error_class_only(self):
        with tempfile.TemporaryDirectory() as parent:
            bad_path = os.path.join(parent, "not-a-directory")
            with open(bad_path, "w", encoding="utf-8") as handle:
                handle.write("x")
            spool = BreadcrumbDurableSpool(bad_path, batch_size=1, flush_interval_seconds=0.01)
            spool.enqueue({"source": "worker", "message": "event"}, False)
            health = spool.close(timeout=2.0)
            self.assertIn(health["last_error_class"], {
                "storage_unavailable", "permission_denied", "writer_failure",
                "health_publish_failure",
            })
            self.assertNotIn("path", health)
            self.assertNotIn("last_error", health)

    def test_pre_gate_regular_and_health_tokens_settle_before_terminal_publish(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            emitter = collector.create_emitter()
            release = threading.Event()
            regular_entered = threading.Event()
            health_entered = threading.Event()
            original_health = emitter._BreadcrumbTraceEmitter__record_root_progress_health_internal

            def blocked_regular(*args, **kwargs):
                emitter._BreadcrumbTraceEmitter__reject(True)
                regular_entered.set()
                release.wait(timeout=2.0)
                return "dropped"

            def blocked_health(outcome):
                health_entered.set()
                release.wait(timeout=2.0)
                return original_health(outcome)

            close_result = {}
            try:
                with patch.object(
                    emitter,
                    "_BreadcrumbTraceEmitter__record_internal",
                    side_effect=blocked_regular,
                ), patch.object(
                    emitter,
                    "_BreadcrumbTraceEmitter__record_root_progress_health_internal",
                    side_effect=blocked_health,
                ):
                    regular = threading.Thread(
                        target=lambda: emitter.record("worker", "pre_gate"),
                    )
                    health = threading.Thread(
                        target=lambda: emitter.record_root_progress_health(
                            "summary_observed", "summary",
                        ),
                    )
                    regular.start()
                    health.start()
                    self.assertTrue(regular_entered.wait(timeout=1.0))
                    self.assertTrue(health_entered.wait(timeout=1.0))
                    closed = threading.Event()
                    closer = threading.Thread(
                        target=lambda: (close_result.update(collector.close(timeout=2.0)), closed.set()),
                    )
                    closer.start()
                    self.assertFalse(closed.wait(timeout=0.05))
                    release.set()
                    regular.join(timeout=1.0)
                    health.join(timeout=1.0)
                    closer.join(timeout=2.0)
                    self.assertFalse(regular.is_alive())
                    self.assertFalse(health.is_alive())
                    self.assertFalse(closer.is_alive())
            finally:
                release.set()
                collector.close(timeout=2.0)

            if os.name == "nt":
                self.assertTrue(close_result.get("terminal") or close_result.get("incomplete"))
                self.assertEqual("buffered_best_effort", close_result.get("durability"))
                return
            self.assertTrue(close_result.get("terminal"))
            self.assertGreaterEqual(close_result.get("upstream_collector_rejected", 0), 1)
            self.assertGreaterEqual(
                collector.snapshot()["root_progress_health"]["outcome_counts"]["summary_observed"],
                1,
            )
            self.assertTrue(any(
                gap.get("reason") == "ingress_rejected"
                for gap in collector.snapshot().get("gaps", [])
            ))
            with open(os.path.join(path, "breadcrumbs.health.json"), encoding="utf-8") as handle:
                sidecar = json.load(handle)
            self.assertTrue(sidecar["terminal"])
            self.assertFalse(sidecar["incomplete"])

    def test_post_gate_emitter_calls_are_strict_noops_without_accounting_mutation(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            emitter = collector.create_emitter()
            collector._BreadcrumbTraceCollector__close_ingress_gate(
                time.monotonic() + 1.0,
            )
            before = collector.snapshot()
            before_health = collector.durable_snapshot()
            self.assertEqual("disabled", emitter.record("scanner", "late", category="scan.failure"))
            self.assertFalse(emitter.record_root_progress_health("summary_observed", "summary"))
            after = collector.snapshot()
            after_health = collector.durable_snapshot()
            for key in ("version", "dropped_count", "accepted_count", "gaps", "root_progress_health"):
                self.assertEqual(before.get(key), after.get(key))
            for key in (
                "accepted", "written", "lost", "critical_lost", "normal_lost",
                "upstream_collector_rejected", "upstream_critical_ingress_rejected",
            ):
                self.assertEqual(before_health.get(key), after_health.get(key))
            collector.close(timeout=2.0)

    def test_create_emitter_after_closing_is_inert_and_creates_no_resources(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        collector._BreadcrumbTraceCollector__close_ingress_gate(time.monotonic() + 1.0)
        before = (
            collector._BreadcrumbTraceCollector__critical_external_records,
            collector._BreadcrumbTraceCollector__normal_external_records,
            collector._BreadcrumbTraceCollector__ingress_drainer,
            collector._BreadcrumbTraceCollector__durable_spool,
        )
        emitter = collector.create_emitter()
        self.assertIsInstance(emitter, BreadcrumbTraceNoopEmitter)
        self.assertEqual("disabled", emitter.record("worker", "late"))
        after = (
            collector._BreadcrumbTraceCollector__critical_external_records,
            collector._BreadcrumbTraceCollector__normal_external_records,
            collector._BreadcrumbTraceCollector__ingress_drainer,
            collector._BreadcrumbTraceCollector__durable_spool,
        )
        self.assertEqual(before, after)
        collector.close(timeout=2.0)
        late = collector.create_emitter()
        self.assertIsInstance(late, BreadcrumbTraceNoopEmitter)
        self.assertEqual(
            (None, None, None, None),
            (
                collector._BreadcrumbTraceCollector__critical_external_records,
                collector._BreadcrumbTraceCollector__normal_external_records,
                collector._BreadcrumbTraceCollector__ingress_drainer,
                collector._BreadcrumbTraceCollector__durable_spool,
            ),
        )

    def test_timeout_publishes_nonterminal_incomplete_health(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            emitter = collector.create_emitter()
            entered = threading.Event()
            release = threading.Event()

            def blocked_regular(*args, **kwargs):
                entered.set()
                release.wait(timeout=2.0)
                return "enqueued"

            try:
                with patch.object(
                    emitter,
                    "_BreadcrumbTraceEmitter__record_internal",
                    side_effect=blocked_regular,
                ):
                    producer = threading.Thread(
                        target=lambda: emitter.record("worker", "stalled"),
                    )
                    producer.start()
                    self.assertTrue(entered.wait(timeout=1.0))
                    health = collector.close(timeout=0.05)
                    self.assertFalse(health.get("terminal", True))
                    self.assertTrue(health.get("incomplete"))
                    self.assertFalse(health.get("closed", True))
                    if os.name != "nt":
                        self.assertEqual("INCOMPLETE", health["state"])
                        self.assertFalse(health["health_publish_ok"])
                        self.assertEqual("unavailable", health["offline_retrieval"])
                    else:
                        self.assertEqual("buffered_best_effort", health["durability"])
                    release.set()
                    producer.join(timeout=1.0)
                    self.assertFalse(producer.is_alive())
                    second = collector.close(timeout=2.0)
                    self.assertFalse(second.get("terminal", True))
                    self.assertTrue(second.get("incomplete"))
            finally:
                release.set()
                collector.close(timeout=2.0)

    def test_spool_close_is_atomic_with_late_enqueue_loss(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
            spool.enqueue({"source": "worker", "message": "before"}, False)
            spool.close(timeout=2.0)
            self.assertFalse(spool.enqueue({"source": "worker", "message": "after"}, False))
            health = spool.snapshot()
            self.assertGreaterEqual(health["written"] + health["lost"], health["accepted"])
            self.assertGreaterEqual(health["lost"], 1)

    def test_runtime_disable_accounts_durable_records_before_replacing_owner(self):
        with tempfile.TemporaryDirectory() as path:
            durable_state = [True]
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled_getter=lambda: durable_state[0],
                durable_path=path,
            )
            collector.record("controller", "before_disable", category="queue.lifecycle")
            durable_state[0] = False
            collector.sync_enabled_state()
            health = collector.durable_snapshot()
            self.assertFalse(health.get("thread_alive", True))
            self.assertGreaterEqual(health["written"] + health["lost"], health["accepted"])
            self.assertIsNone(collector._BreadcrumbTraceCollector__durable_spool)
            collector.close(timeout=2.0)

    def test_active_existing_file_oversize_fails_closed(self):
        with tempfile.TemporaryDirectory() as path:
            active = os.path.join(path, "breadcrumbs.jsonl")
            with open(active, "wb") as handle:
                handle.write(b"x" * 129)
            spool = BreadcrumbDurableSpool(
                path,
                batch_size=1,
                flush_interval_seconds=0.01,
                rotate_bytes=64,
                max_files=2,
                max_bytes=128,
            )
            spool.enqueue({"source": "worker", "message": "blocked"}, False)
            health = spool.close(timeout=2.0)
            self.assertEqual("retention_failure", health["last_error_class"])
            self.assertGreaterEqual(health["lost"], 1)
            self.assertEqual(129, os.path.getsize(active))

    def test_hostile_retention_directory_is_scan_bounded_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(
                path,
                batch_size=1,
                flush_interval_seconds=0.01,
                rotate_bytes=64,
                max_files=2,
                max_bytes=1024 * 1024,
            )
            spool.close(timeout=2.0)
            for index in range(_DURABLE_RETENTION_SCAN_LIMIT + 1):
                with open(
                    os.path.join(path, "breadcrumbs-{}-1.jsonl".format(index)),
                    "wb",
                ) as handle:
                    handle.write(b"x")
            self.assertFalse(spool._BreadcrumbDurableSpool__prune_rotated_files())
            health = spool.snapshot()
            self.assertEqual("retention_failure", health["last_error_class"])

    def test_successful_flush_remains_counted_when_prune_fails(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
            spool.close(timeout=2.0)
            with patch.object(
                spool,
                "_BreadcrumbDurableSpool__prune_rotated_files",
                return_value=False,
            ):
                spool._BreadcrumbDurableSpool__write_batch([
                    ({"source": "worker", "message": "flushed"}, False, 32),
                ])
            health = spool.snapshot()
            self.assertEqual(1, health["written"])
            self.assertEqual(1, health["flush_count"])
            self.assertEqual(0, health["lost"])
            self.assertEqual("retention_failure", health["last_error_class"])

    def test_flush_timeout_is_false_while_writer_is_busy(self):
        if os.name == "nt":
            self.skipTest("Windows durable file acknowledgement is buffered/unverified")
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            spool = collector._BreadcrumbTraceCollector__durable_spool
            self.assertIsNotNone(spool)
            assert spool is not None
            original_write = spool._BreadcrumbDurableSpool__write_batch

            def slow_write(batch):
                time.sleep(0.15)
                original_write(batch)

            spool._BreadcrumbDurableSpool__write_batch = slow_write
            collector.record("worker", "event")
            self.assertFalse(collector.flush_durable(0.01))
            self.assertTrue(collector.flush_durable(2.0))
            collector.close()

    def test_short_batch_write_counts_completed_records_and_loss(self):
        class ShortWriter:
            def __init__(self):
                self.calls = 0

            def write(self, value):
                self.calls += 1
                return len(value) if self.calls == 1 else len(value) - 1

            def seek(self, *_args):
                return None

            def truncate(self):
                return None

            def flush(self):
                return None

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, batch_size=2, flush_interval_seconds=0.01)
            spool.close(timeout=2.0)
            fake = ShortWriter()
            spool._BreadcrumbDurableSpool__file_handle = fake
            spool._BreadcrumbDurableSpool__file_path = os.path.join(path, "breadcrumbs.jsonl")
            spool._BreadcrumbDurableSpool__file_size = 0
            spool._BreadcrumbDurableSpool__write_batch([
                ({"message": "one"}, True, 32),
                ({"message": "two"}, False, 32),
            ])
            health = spool.snapshot()
            self.assertEqual(1, health["written"])
            self.assertEqual(1, health["lost"])
            self.assertEqual(1, health["partial_write_failures"])

    def test_short_second_record_cannot_make_uncommitted_session_offline_valid(self):
        if os.name == "nt":
            self.skipTest("POSIX offline session-boundary contract")

        class ShortSecondWriter:
            def __init__(self):
                self.calls = 0

            def write(self, value):
                self.calls += 1
                if self.calls == 1:
                    return len(value)
                return len(value) - 1

            def seek(self, *_args):
                return None

            def truncate(self):
                return None

            def flush(self):
                return None

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as path:
            # Prevent the startup owner from committing a boundary. This
            # leaves a deterministic pre-boundary writer for the two-record
            # short-write seam below.
            boundary_name = "_BreadcrumbDurableSpool__publish_session_boundary"
            with patch.object(BreadcrumbDurableSpool, boundary_name, return_value=None):
                spool = BreadcrumbDurableSpool(
                    path, batch_size=2, flush_interval_seconds=0.01,
                )
            spool.close(timeout=2.0)
            active = os.path.join(path, "breadcrumbs.jsonl")
            fake = ShortSecondWriter()
            spool._BreadcrumbDurableSpool__file_handle = fake
            spool._BreadcrumbDurableSpool__file_path = active
            spool._BreadcrumbDurableSpool__file_size = 0
            spool._BreadcrumbDurableSpool__write_batch([
                ({"source": "worker", "message": "boundary"}, True, 32),
                ({"source": "worker", "message": "ordinary"}, False, 32),
            ])
            health = spool.snapshot()
            # The writer must reject the whole candidate batch before the
            # second-record short write can turn an uncommitted first record
            # into apparently valid offline evidence.
            self.assertEqual("retention_failure", health["last_error_class"])
            self.assertEqual(0, fake.calls)
            self.assertFalse(health["session_boundary_written"])
            self.assertNotEqual("valid", health["offline_retrieval"])
            health_path = os.path.join(path, "breadcrumbs.health.json")
            with open(health_path, encoding="utf-8") as handle:
                sidecar = json.load(handle)
            self.assertFalse(sidecar["session_boundary_written"])
            self.assertNotEqual("valid", sidecar["offline_retrieval"])

    def test_metadata_is_bounded_before_durable_ingress_copy(self):
        details = {"message": "ordinary"}
        metadata = {
            "api_key": "metadata-secret",
            "huge_string": "m" * 1_000_000,
            "huge_mapping": {
                "key-{}".format(index): "value-{}".format(index)
                for index in range(10_000)
            },
            "huge_list": ["item"] * 10_000,
        }
        revision = multiprocessing.Value("L", 3)
        epoch = multiprocessing.Value("L", 4)
        started = time.perf_counter()
        record = _bounded_ingress_record(
            "scanner", "scan_failed", details, metadata,
            time.time_ns(), revision, epoch,
        )
        elapsed = time.perf_counter() - started
        self.assertIsNotNone(record)
        self.assertLess(elapsed, 0.5)
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(encoded.encode("utf-8")), 8 * 1024)
        self.assertNotIn("metadata-secret", encoded)

    def test_ingress_sanitizer_is_iterator_bounded_without_json_serialization(self):
        class StreamingMapping(Mapping):
            def __init__(self):
                self.yielded = 0

            def __getitem__(self, key):
                return key

            def __iter__(self):
                return iter(())

            def __len__(self):
                return 10**12

            def items(self):
                for index in range(10**12):
                    self.yielded += 1
                    yield "key-{}".format(index), "value-{}".format(index)

        details = StreamingMapping()
        revision = multiprocessing.Value("L", 3)
        epoch = multiprocessing.Value("L", 4)
        with patch("common.breadcrumb_trace.json.dumps", side_effect=AssertionError("producer serialized JSON")):
            record = _bounded_ingress_record(
                "scanner", "root", details, {}, time.time_ns(), revision, epoch,
            )
        self.assertIsNotNone(record)
        self.assertLessEqual(details.yielded, 24)
        self.assertLessEqual(len(record["details"]), 24)
        self.assertLessEqual(len(json.dumps(record, separators=(",", ":")).encode("utf-8")), 8 * 1024)

    def test_ingress_byte_bound_covers_unicode_keys_and_json_escaping(self):
        revision = multiprocessing.Value("L", 3)
        epoch = multiprocessing.Value("L", 4)
        details = {
            "\x00\\\"" + "💥" * 64: "\x00\\\"" + "💥" * 256,
            "nested": [{"key": "\n\r\t" + "💥" * 256} for _ in range(16)],
        }
        record = _bounded_ingress_record(
            "scanner\x00", "failure\\\"", details, {}, time.time_ns(), revision, epoch,
        )
        self.assertIsNotNone(record)
        self.assertLessEqual(
            len(json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")),
            8 * 1024,
        )

    def test_retention_prune_failure_disables_future_durable_writes(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(
                path,
                batch_size=1,
                flush_interval_seconds=0.01,
                rotate_bytes=64,
                max_files=2,
                max_bytes=128,
            )
            spool.close(timeout=2.0)
            for index in (1, 2):
                with open(
                    os.path.join(path, "breadcrumbs-{}-0001.jsonl".format(index)),
                    "wb",
                ) as handle:
                    handle.write(b"x" * 32)
            with patch("common.breadcrumb_trace.os.unlink", side_effect=PermissionError("no prune")):
                self.assertFalse(spool._BreadcrumbDurableSpool__prune_rotated_files())
            self.assertFalse(spool.enqueue({"message": "blocked"}, False))
            health = spool.close(timeout=2.0)
            self.assertGreaterEqual(health["retention_prune_failures"], 1)
            self.assertEqual("retention_failure", health["last_error_class"])
            self.assertEqual([], [
                name for name in os.listdir(path)
                if name == "breadcrumbs.jsonl"
            ])

    def test_write_exception_rolls_back_and_quarantines_when_truncate_fails(self):
        class RaisingWriter:
            def __init__(self, handle):
                self.handle = handle

            def write(self, value):
                self.handle.write(value[:3])
                self.handle.flush()
                raise OSError("write failed")

            def seek(self, *_args):
                raise OSError("seek failed")

            def truncate(self):
                raise OSError("truncate failed")

            def close(self):
                self.handle.close()

        with tempfile.TemporaryDirectory() as path:
            active = os.path.join(path, "breadcrumbs.jsonl")
            raw = open(active, "w+b")
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
            spool.close(timeout=2.0)
            spool._BreadcrumbDurableSpool__file_handle = RaisingWriter(raw)
            spool._BreadcrumbDurableSpool__file_path = active
            spool._BreadcrumbDurableSpool__file_size = 0
            spool._BreadcrumbDurableSpool__write_batch([
                ({"message": "bad"}, True, 32),
            ])
            health = spool.snapshot()
            self.assertEqual(1, health["active_file_quarantined"])
            self.assertEqual(1, health["partial_write_failures"])
            self.assertEqual("active_file_quarantined", health["last_error_class"])
            self.assertFalse(os.path.exists(active))
            self.assertTrue(any(
                name.startswith("breadcrumbs-quarantine-")
                for name in os.listdir(path)
            ))

    def test_quarantine_failure_is_not_reported_as_success(self):
        class RaisingWriter:
            def __init__(self, handle):
                self.handle = handle

            def write(self, value):
                self.handle.write(value[:3])
                self.handle.flush()
                raise OSError("write failed")

            def seek(self, *_args):
                raise OSError("seek failed")

            def truncate(self):
                raise OSError("truncate failed")

            def close(self):
                self.handle.close()

        with tempfile.TemporaryDirectory() as path:
            active = os.path.join(path, "breadcrumbs.jsonl")
            raw = open(active, "w+b")
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
            spool.close(timeout=2.0)
            spool._BreadcrumbDurableSpool__file_handle = RaisingWriter(raw)
            spool._BreadcrumbDurableSpool__file_path = active
            spool._BreadcrumbDurableSpool__file_size = 0
            real_replace = os.replace

            def fail_quarantine(source, destination):
                if "breadcrumbs-quarantine-" in destination:
                    raise OSError("rename failed")
                return real_replace(source, destination)

            with patch("common.breadcrumb_trace.os.replace", side_effect=fail_quarantine):
                spool._BreadcrumbDurableSpool__write_batch([
                    ({"message": "bad"}, True, 32),
                ])
            health = spool.snapshot()
            self.assertEqual(0, health["active_file_quarantined"])
            self.assertEqual(1, health["active_file_quarantine_failures"])
            self.assertEqual("active_file_quarantine_failure", health["last_error_class"])
            self.assertTrue(os.path.exists(active))

    def test_close_gate_drains_multiprocess_lanes_without_snapshot_help(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            emitter = collector.create_emitter()
            self.assertEqual(
                "enqueued",
                emitter.record("controller", "before_close", category="queue.lifecycle"),
            )
            self.assertEqual(
                "enqueued",
                emitter.record("scanner", "scan_failed", category="scanner_process", event_type="failure"),
            )
            health = collector.close(timeout=2.0)
            self.assertFalse(health.get("thread_alive", True))
            with open(os.path.join(path, "breadcrumbs.jsonl"), encoding="utf-8") as handle:
                messages = [
                    record["message"] for line in handle if line.strip()
                    for record in [json.loads(line)] if "message" in record
                ]
            self.assertIn("before_close", messages)
            self.assertIn("scan_failed", messages)

    def test_spawned_worker_feeder_is_acked_before_lanes_close(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            emitter = collector.create_emitter()
            context = multiprocessing.get_context("spawn")
            worker = context.Process(target=_emit_worker_boundary, args=(emitter,))
            worker.start()
            worker.join(timeout=5.0)
            self.assertFalse(worker.is_alive())
            self.assertEqual(0, worker.exitcode)
            health = collector.close(timeout=2.0)
            self.assertFalse(health.get("thread_alive", True))
            ingress = collector.ingress_snapshot()
            self.assertEqual(ingress["critical_enqueued"], ingress["critical_ack"])

    def test_concurrent_feeder_is_rejected_at_close_gate_with_bounded_return(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            emitter = collector.create_emitter()
            running = threading.Event()
            running.set()

            def produce():
                index = 0
                while running.is_set():
                    emitter.record("scanner", "progress-{}".format(index), category="model.progress")
                    index += 1

            producer = threading.Thread(target=produce)
            producer.start()
            time.sleep(0.05)
            started = time.monotonic()
            health = collector.close(timeout=0.2)
            elapsed = time.monotonic() - started
            running.clear()
            producer.join(timeout=1.0)
            health = collector.close(timeout=2.0)
            self.assertLess(elapsed, 0.8)
            self.assertFalse(health.get("thread_alive", True))

    def test_open_admission_lock_miss_is_accounted_without_waiting(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        emitter = collector.create_emitter()
        admission_lock = collector._BreadcrumbTraceCollector__ingress_admission_lock
        self.assertTrue(admission_lock.acquire(False))
        try:
            started = time.monotonic()
            result = emitter.record("scanner", "progress", category="model.progress")
            elapsed = time.monotonic() - started
        finally:
            admission_lock.release()

        self.assertEqual("dropped", result)
        self.assertLess(elapsed, 0.25)
        ingress = collector.ingress_snapshot()
        self.assertGreaterEqual(ingress["normal_rejected_count"], 1)
        self.assertGreaterEqual(collector.snapshot()["accounting"]["dropped_count"], 1)

        self.assertTrue(admission_lock.acquire(False))
        try:
            self.assertEqual("dropped", collector.record("scanner", "direct-lock-miss"))
            self.assertFalse(
                collector.record_root_progress_health("summary_observed", "summary"),
            )
        finally:
            admission_lock.release()
        self.assertGreaterEqual(collector.snapshot()["accounting"]["dropped_count"], 3)
        collector.close(timeout=2.0)

    def test_open_inflight_cap_miss_is_accounted_without_waiting(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        emitter = collector.create_emitter()
        inflight = collector._BreadcrumbTraceCollector__ingress_inflight_count
        with inflight.get_lock():
            inflight.value = _BREADCRUMB_INGRESS_INFLIGHT_CAPACITY
        try:
            started = time.monotonic()
            result = emitter.record("scanner", "progress", category="model.progress")
            elapsed = time.monotonic() - started
        finally:
            with inflight.get_lock():
                inflight.value = 0

        self.assertEqual("dropped", result)
        self.assertLess(elapsed, 0.25)
        ingress = collector.ingress_snapshot()
        self.assertGreaterEqual(ingress["normal_rejected_count"], 1)
        self.assertGreaterEqual(collector.snapshot()["accounting"]["dropped_count"], 1)
        collector.close(timeout=2.0)

    def test_direct_and_emitter_apis_are_admitted_before_closing_and_inert_after(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        emitter = collector.create_emitter()
        self.assertEqual(
            "retained",
            collector.record("controller", "direct-open", category="queue.lifecycle"),
        )
        self.assertTrue(
            collector.record_root_progress_health("summary_observed", "summary"),
        )
        self.assertEqual("enqueued", emitter.record("controller", "emitter-open"))

        self.assertTrue(
            collector._BreadcrumbTraceCollector__close_ingress_gate(
                time.monotonic() + 1.0,
            )
        )
        self.assertEqual(
            _BREADCRUMB_LIFECYCLE_CLOSING,
            collector._BreadcrumbTraceCollector__ingress_lifecycle_state(),
        )
        before = collector.snapshot()
        before_ingress = collector.ingress_snapshot()
        self.assertEqual("disabled", emitter.record("controller", "emitter-closing"))
        self.assertEqual("disabled", collector.record("controller", "direct-closing"))
        self.assertFalse(
            collector.record_root_progress_health("summary_observed", "summary"),
        )
        after = collector.snapshot()
        after_ingress = collector.ingress_snapshot()
        self.assertEqual(before["version"], after["version"])
        self.assertEqual(before["accounting"], after["accounting"])
        self.assertEqual(before["entries"], after["entries"])
        self.assertEqual(before["root_progress_health"], after["root_progress_health"])
        self.assertEqual(before_ingress, after_ingress)
        collector.close(timeout=2.0)

    def test_direct_calls_after_terminal_close_are_noops(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        collector.record("controller", "before-close", category="queue.lifecycle")
        collector.close(timeout=2.0)
        before = collector.snapshot()
        before_ingress = collector.ingress_snapshot()

        self.assertEqual("disabled", collector.record("controller", "late"))
        self.assertFalse(
            collector.record_root_progress_health("summary_observed", "summary"),
        )
        after = collector.snapshot()
        after_ingress = collector.ingress_snapshot()
        self.assertEqual(before["version"], after["version"])
        self.assertEqual(before["accounting"], after["accounting"])
        self.assertEqual(before["entries"], after["entries"])
        self.assertEqual(before["root_progress_health"], after["root_progress_health"])
        self.assertEqual(before_ingress, after_ingress)

    def test_queue_exclusion_follows_critical_boundary_in_writer_batch(self):
        with tempfile.TemporaryDirectory() as path:
            original_take = BreadcrumbDurableSpool._BreadcrumbDurableSpool__take_batch
            parked = threading.Event()
            release = threading.Event()

            def parked_take(instance):
                parked.set()
                release.wait(timeout=2.0)
                return original_take(instance)

            with patch.object(
                BreadcrumbDurableSpool,
                "_BreadcrumbDurableSpool__take_batch",
                parked_take,
            ):
                spool = BreadcrumbDurableSpool(
                    path,
                    total_capacity=3,
                    critical_capacity=2,
                    normal_capacity=1,
                    batch_size=2,
                    flush_interval_seconds=10.0,
                )
                self.assertTrue(parked.wait(timeout=1.0))
            spool._BreadcrumbDurableSpool__stop.set()
            spool.critical_queue.put_nowait((
                {"source": "controller", "message": "failure"}, True, 1,
            ))
            spool.normal_queue.put_nowait((
                {"source": "model_builder", "message": "excluded"}, False, 1,
            ))
            batch = original_take(spool)
            release.set()
            spool.close(timeout=2.0)

        self.assertEqual(
            ["failure", "excluded"],
            [record[0]["message"] for record in batch],
        )

    def test_critical_lane_fairness_keeps_normal_records_visible_at_1_8_and_32(self):
        for critical_count in (1, 8, 32):
            with self.subTest(critical_count=critical_count), tempfile.TemporaryDirectory() as path:
                original_take = BreadcrumbDurableSpool._BreadcrumbDurableSpool__take_batch
                parked = threading.Event()
                release = threading.Event()

                def parked_take(instance):
                    parked.set()
                    release.wait(timeout=2.0)
                    return original_take(instance)

                with patch.object(
                    BreadcrumbDurableSpool,
                    "_BreadcrumbDurableSpool__take_batch",
                    parked_take,
                ):
                    spool = BreadcrumbDurableSpool(
                        path,
                        total_capacity=critical_count + 1,
                        critical_capacity=critical_count,
                        normal_capacity=1,
                        batch_size=critical_count + 1,
                        flush_interval_seconds=10.0,
                    )
                    self.assertTrue(parked.wait(timeout=1.0))
                spool._BreadcrumbDurableSpool__stop.set()
                for index in range(critical_count):
                    spool.critical_queue.put_nowait((
                        {"source": "controller", "message": "failure-{}".format(index)},
                        True,
                        1,
                    ))
                spool.normal_queue.put_nowait((
                    {"source": "model_builder", "message": "excluded"}, False, 1,
                ))
                batch = original_take(spool)
                release.set()
                spool.close(timeout=2.0)

            messages = [record[0]["message"] for record in batch]
            self.assertIn("excluded", messages)
            self.assertLessEqual(messages.index("excluded"), BREADCRUMB_INGRESS_CRITICAL_BURST)
            if critical_count == 32:
                self.assertEqual(BREADCRUMB_INGRESS_CRITICAL_BURST, messages.index("excluded"))

    def test_critical_fairness_persists_across_batch_sizes_1_8_and_32(self):
        for batch_size in (1, 8, 32):
            with self.subTest(batch_size=batch_size), tempfile.TemporaryDirectory() as path:
                original_take = BreadcrumbDurableSpool._BreadcrumbDurableSpool__take_batch
                parked = threading.Event()
                release = threading.Event()

                def parked_take(instance):
                    parked.set()
                    release.wait(timeout=2.0)
                    return original_take(instance)

                with patch.object(
                    BreadcrumbDurableSpool,
                    "_BreadcrumbDurableSpool__take_batch",
                    parked_take,
                ):
                    spool = BreadcrumbDurableSpool(
                        path,
                        total_capacity=33,
                        critical_capacity=32,
                        normal_capacity=1,
                        batch_size=batch_size,
                        flush_interval_seconds=10.0,
                    )
                    self.assertTrue(parked.wait(timeout=1.0))
                spool._BreadcrumbDurableSpool__stop.set()
                for index in range(32):
                    spool.critical_queue.put_nowait((
                        {"source": "controller", "message": "failure-{}".format(index)},
                        True,
                        1,
                    ))
                spool.normal_queue.put_nowait((
                    {"source": "model_builder", "message": "excluded"}, False, 1,
                ))
                messages = []
                for _index in range(40):
                    batch = original_take(spool)
                    messages.extend(item[0]["message"] for item in batch)
                    if "excluded" in messages or not batch:
                        break
                release.set()
                spool.close(timeout=2.0)
            self.assertIn("excluded", messages)
            excluded_index = messages.index("excluded")
            self.assertLessEqual(excluded_index, BREADCRUMB_INGRESS_CRITICAL_BURST)

    def test_health_cache_api_and_sidecar_agree_after_terminal_close(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
            spool.enqueue({"source": "controller", "message": "ready"}, True)
            returned = spool.close(timeout=2.0)
            api = spool.snapshot()
            sidecar = None
            if os.path.exists(os.path.join(path, "breadcrumbs.health.json")):
                with open(os.path.join(path, "breadcrumbs.health.json"), encoding="utf-8") as handle:
                    sidecar = json.load(handle)

        for key in (
            "schema", "session_id", "state", "enabled", "accepted", "written", "lost", "critical_lost", "normal_lost",
            "writer_failures", "flush_count", "rotation_count", "last_error_class",
            "closed", "terminal", "incomplete", "unresolved",
        ):
            self.assertEqual(returned[key], api[key])
            if sidecar is not None:
                self.assertEqual(api[key], sidecar[key], key)
        self.assertTrue(api["terminal"] or api["incomplete"])
        if os.name == "nt":
            self.assertEqual("buffered_best_effort", api["durability"])
            self.assertEqual("windows_acl_unverified", api["path_safety"])
        else:
            self.assertTrue(api["terminal"])
            self.assertFalse(api["incomplete"])

    def test_health_publish_failure_is_explicit_and_does_not_claim_sidecar_success(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=10.0)
            health_path = os.path.join(path, "breadcrumbs.health.json")
            before = None
            if os.path.exists(health_path):
                with open(health_path, encoding="utf-8") as handle:
                    before = json.load(handle)
            real_replace = os.replace

            def fail_health_replace(source, destination):
                if destination.endswith("breadcrumbs.health.json"):
                    raise OSError("sidecar unavailable")
                return real_replace(source, destination)

            with patch("common.breadcrumb_trace.os.replace", side_effect=fail_health_replace):
                spool._BreadcrumbDurableSpool__publish_health()
            api = spool.snapshot()
            self.assertEqual("health_publish_failure", api["last_error_class"])
            if os.path.exists(health_path):
                with open(health_path, encoding="utf-8") as handle:
                    sidecar = json.load(handle)
                if before is not None:
                    self.assertEqual(before["updated_ms"], sidecar["updated_ms"])
                self.assertFalse(sidecar["terminal"])
            else:
                self.assertFalse(api["health_publish_ok"])
            spool.close(timeout=2.0)

    def test_sync_false_invalid_marker_stays_sticky_until_sync_true_clear(self):
        if os.name == "nt":
            self.skipTest("POSIX invalidation marker durability contract")
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
            initial = spool.close(timeout=2.0)
            health_path = os.path.join(path, "breadcrumbs.health.json")
            marker_path = os.path.join(path, "breadcrumbs.health.invalid")
            real_replace = os.replace

            def fail_health_replace(source, destination):
                if destination.endswith("breadcrumbs.health.json"):
                    raise OSError("health sidecar unavailable")
                return real_replace(source, destination)

            with patch(
                "common.breadcrumb_trace.os.fsync",
                wraps=os.fsync,
            ) as fsync, patch(
                "common.breadcrumb_trace.os.replace",
                side_effect=fail_health_replace,
            ):
                self.assertFalse(spool._BreadcrumbDurableSpool__publish_health(sync=False))
            self.assertGreaterEqual(fsync.call_count, 2)

            failed = spool.snapshot()
            self.assertTrue(os.path.exists(marker_path))
            self.assertTrue(failed["offline_retrieval_invalidated"])
            self.assertEqual("invalidated", failed["offline_retrieval"])
            self.assertEqual("buffered_best_effort", failed["durability"])
            with open(health_path, encoding="utf-8") as handle:
                stale = json.load(handle)
            self.assertEqual(initial["session_id"], stale["session_id"])
            self.assertTrue(stale["terminal"])
            with open(marker_path, encoding="utf-8") as handle:
                marker = json.load(handle)
            self.assertEqual("breadcrumb_durable_health_invalidation.v1", marker["schema"])
            self.assertEqual(initial["session_id"], marker["session_id"])
            self.assertEqual("INVALID", marker["state"])
            self.assertEqual(0o600, stat.S_IMODE(os.stat(marker_path).st_mode))

            with patch("common.breadcrumb_trace.os.fsync", wraps=os.fsync):
                self.assertTrue(spool._BreadcrumbDurableSpool__publish_health(sync=False))
            self.assertTrue(os.path.exists(marker_path))
            recovered = spool.snapshot()
            self.assertEqual(initial["session_id"], recovered["session_id"])
            self.assertTrue(recovered["offline_retrieval_invalidated"])
            self.assertEqual("invalidated", recovered["offline_retrieval"])
            self.assertEqual("buffered_best_effort", recovered["durability"])
            with open(health_path, encoding="utf-8") as handle:
                published = json.load(handle)
            self.assertEqual(recovered["session_id"], published["session_id"])
            self.assertTrue(published["health_publish_ok"])

            self.assertTrue(spool._BreadcrumbDurableSpool__publish_health(sync=True))
            self.assertFalse(os.path.exists(marker_path))
            self.assertFalse(spool.snapshot()["offline_retrieval_invalidated"])
            self.assertEqual("valid", spool.snapshot()["offline_retrieval"])

    def test_post_spool_close_failure_keeps_spool_evidence_and_collector_incomplete(self):
        class FailingCollectorLock:
            def acquire(self, *_args, **_kwargs):
                return False

            def release(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            spool = collector._BreadcrumbTraceCollector__durable_spool
            self.assertIsNotNone(spool)
            assert spool is not None
            collector.record("controller", "ready", category="queue.lifecycle")
            real_close = spool.close

            def close_then_fail_collector_lock(timeout):
                result = real_close(timeout)
                collector._BreadcrumbTraceCollector__lock = FailingCollectorLock()
                return result

            spool.close = close_then_fail_collector_lock
            result = collector.close(timeout=2.0)
            api = collector.durable_snapshot()
            self.assertTrue(result["incomplete"])
            self.assertFalse(api["terminal"])
            self.assertTrue(api["incomplete"])
            self.assertEqual("INCOMPLETE", result["state"])
            self.assertEqual("CLOSING", result["collector_lifecycle"])
            self.assertFalse(result["collector_retired"])
            self.assertFalse(api["collector_retired"])
            # The collector lifecycle gate remains CLOSING after the late
            # lock failure, retaining exclusion until the lock is restored.
            self.assertEqual(
                _BREADCRUMB_LIFECYCLE_CLOSING,
                collector._BreadcrumbTraceCollector__ingress_closed_gate.value,
            )
            sidecar_path = os.path.join(path, "breadcrumbs.health.json")
            if os.name != "nt":
                self.assertTrue(os.path.exists(sidecar_path))
                with open(sidecar_path, encoding="utf-8") as handle:
                    sidecar = json.load(handle)
                self.assertTrue(sidecar["terminal"])
                self.assertFalse(sidecar["incomplete"])
                self.assertEqual("TERMINAL", sidecar["state"])
                self.assertEqual(sidecar["session_id"], api["session_id"])
                self.assertTrue(sidecar["health_publish_ok"])
                self.assertEqual("valid", sidecar["offline_retrieval"])
                self.assertTrue(sidecar["session_boundary_written"])
                self.assertGreaterEqual(sidecar["written"], sidecar["accepted"])
                self.assertEqual(0, sidecar["lost"])
                with open(os.path.join(path, "breadcrumbs.jsonl"), encoding="utf-8") as handle:
                    records = [json.loads(line) for line in handle if line.strip()]
                ordinary = [record for record in records if "message" in record]
                self.assertIn("ready", [record["message"] for record in ordinary])
            else:
                self.assertEqual("buffered_best_effort", api["durability"])

            # Restore the collector lock so this forced failure cannot leave a
            # live multiprocessing queue behind for the next test.
            collector._BreadcrumbTraceCollector__lock = threading.RLock()
            collector.close(timeout=2.0)

    def test_disable_timeout_then_reenable_keeps_one_durable_owner(self):
        with tempfile.TemporaryDirectory() as path:
            durable_state = [True]
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_enabled_getter=lambda: durable_state[0],
                durable_path=path,
            )
            emitter = collector.create_emitter()
            spool = collector._BreadcrumbTraceCollector__durable_spool
            self.assertIsNotNone(spool)
            assert spool is not None
            old_thread = spool._BreadcrumbDurableSpool__thread
            emitter.record("controller", "before-disable", category="queue.lifecycle")
            incomplete = dict(spool.snapshot())
            incomplete.update({"thread_alive": True, "terminal": False, "incomplete": True})

            with patch.object(spool, "close", return_value=incomplete):
                durable_state[0] = False
                collector.sync_enabled_state()
                self.assertFalse(collector._BreadcrumbTraceCollector__durable_enabled_state)
                self.assertIs(spool, collector._BreadcrumbTraceCollector__durable_spool)
                durable_state[0] = True
                collector.sync_enabled_state()

            self.assertIs(spool, collector._BreadcrumbTraceCollector__durable_spool)
            self.assertTrue(old_thread.is_alive())
            self.assertEqual(
                1,
                len({
                    id(collector._BreadcrumbTraceCollector__durable_spool),
                }),
            )
            durable_state[0] = False
            collector.sync_enabled_state()
            self.assertFalse(old_thread.is_alive())
            collector.close(timeout=2.0)

    def test_new_session_replaces_stale_terminal_sidecar_before_close(self):
        with tempfile.TemporaryDirectory() as path:
            health_path = os.path.join(path, "breadcrumbs.health.json")
            with open(health_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "schema": "breadcrumb_durable_health.v1",
                    "session_id": "stale-session",
                    "closed": True,
                    "terminal": True,
                    "incomplete": False,
                    "updated_ms": 1,
                }, handle)
            spool = BreadcrumbDurableSpool(path, flush_interval_seconds=0.01)
            current = spool.snapshot()
            if os.name == "nt":
                self.assertEqual("buffered_best_effort", current["durability"])
                self.assertEqual("windows_acl_unverified", current["path_safety"])
                self.assertNotEqual("stale-session", current["session_id"])
                self.assertIn(current["state"], {"RUNNING", "INCOMPLETE"})
                spool.close(timeout=2.0)
                return
            self.assertFalse(current["closed"])
            self.assertFalse(current["terminal"])
            self.assertFalse(current["incomplete"])
            self.assertEqual("RUNNING", current["state"])
            self.assertNotEqual("stale-session", current["session_id"])
            for _index in range(200):
                try:
                    with open(health_path, encoding="utf-8") as handle:
                        current = json.load(handle)
                except (FileNotFoundError, json.JSONDecodeError):
                    current = None
                if current and current.get("updated_ms", 0) > 1:
                    break
                time.sleep(0.005)
            self.assertIsNotNone(current)
            self.assertFalse(current["closed"])
            self.assertFalse(current["terminal"])
            self.assertFalse(current["incomplete"])
            self.assertEqual("RUNNING", current["state"])
            self.assertNotEqual("stale-session", current["session_id"])
            spool.close(timeout=2.0)

        self.assertEqual(12, len(trace_session_digest("session-secret")))
        self.assertNotIn("session-secret", trace_session_digest("session-secret"))

    def test_reservation_is_visible_before_queue_put_and_unresolved_is_bounded(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        emitter = collector.create_emitter()
        reservation = collector._BreadcrumbTraceCollector__critical_ingress_reservation_count
        enqueued = collector._BreadcrumbTraceCollector__critical_ingress_enqueued_count
        observed = []

        class ObservingQueue:
            def put_nowait(self, _value):
                with reservation.get_lock():
                    observed.append(int(reservation.value))

        emitter._BreadcrumbTraceEmitter__critical_record_queue = ObservingQueue()
        self.assertEqual(
            "enqueued",
            emitter.record("controller", "failure", category="queue.lifecycle"),
        )
        self.assertEqual([1], observed)
        ingress = collector.ingress_snapshot()
        self.assertEqual(1, ingress["critical_enqueued"])
        self.assertEqual(1, ingress["critical_pending"])
        # The observing queue deliberately does not acknowledge a record. A
        # real drainer would settle this reservation; settle it explicitly so
        # this test does not make collector shutdown wait for an unreachable
        # fake queue item.
        ack = collector._BreadcrumbTraceCollector__critical_ingress_ack_count
        with ack.get_lock():
            ack.value = enqueued.value
        self.assertEqual(0, collector.ingress_snapshot()["critical_pending"])
        collector.close(timeout=2.0)

    def test_unresolved_reservation_from_killed_producer_keeps_close_nonterminal(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            collector.create_emitter()
            reservation = collector._BreadcrumbTraceCollector__critical_ingress_reservation_count
            with reservation.get_lock():
                reservation.value = 1
            try:
                health = collector.close(timeout=0.05)
                self.assertFalse(health.get("terminal", True))
                self.assertTrue(health.get("incomplete"))
                self.assertGreaterEqual(health.get("unresolved", 0), 1)
                self.assertEqual("INCOMPLETE", health["state"])
                if os.name != "nt":
                    self.assertFalse(health["health_publish_ok"])
                    self.assertEqual("unavailable", health["offline_retrieval"])
                    if health.get("thread_alive"):
                        sidecar_path = os.path.join(path, "breadcrumbs.health.json")
                        sidecar = None
                        deadline = time.monotonic() + 1.0
                        while time.monotonic() < deadline:
                            try:
                                with open(sidecar_path, encoding="utf-8") as handle:
                                    candidate = json.load(handle)
                            except (FileNotFoundError, OSError, ValueError):
                                candidate = None
                            if (
                                candidate is not None
                                and candidate.get("state") == "INCOMPLETE"
                                and candidate.get("terminal") is False
                                and candidate.get("incomplete") is True
                            ):
                                sidecar = candidate
                                break
                            time.sleep(0.01)
                        self.assertIsNotNone(sidecar)
                else:
                    self.assertEqual("buffered_best_effort", health["durability"])
            finally:
                with reservation.get_lock():
                    reservation.value = 0
                collector.close(timeout=2.0)

    def test_long_keys_and_deep_nested_sensitive_values_are_redacted_at_ingress(self):
        revision = multiprocessing.Value("L", 3)
        epoch = multiprocessing.Value("L", 4)
        details = {
            "password" + ("x" * 10_000): {
                "outer": {
                    "api_key": "deep-api-secret",
                    "inner": {"token": "deep-token-secret"},
                },
            },
        }
        record = _bounded_ingress_record(
            "scanner", "scan_failed", details, {}, time.time_ns(), revision, epoch,
        )
        self.assertIsNotNone(record)
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        self.assertNotIn("deep-api-secret", encoded)
        self.assertNotIn("deep-token-secret", encoded)
        self.assertLessEqual(max(len(key) for key in record["details"]), 64)
        self.assertLessEqual(len(encoded.encode("utf-8")), 8 * 1024)

    def test_middle_sensitive_keyword_in_long_key_stays_redacted_child_parent_and_durable(self):
        if os.name == "nt":
            self.skipTest("Windows durable health sidecar is unsupported")
        revision = multiprocessing.Value("L", 3)
        epoch = multiprocessing.Value("L", 4)
        long_key = ("prefix-" * 360) + "password" + ("-suffix" * 360)
        details = {
            long_key: {
                "value": "middle-key-secret",
                "nested": {
                    "token": "nested-token-secret",
                    "api_key": "nested-api-secret",
                },
            },
        }
        child_record = _bounded_ingress_record(
            "scanner", "scan_failed", details, {}, time.time_ns(), revision, epoch,
        )
        self.assertIsNotNone(child_record)
        assert child_record is not None
        child_encoded = json.dumps(child_record, ensure_ascii=False, separators=(",", ":"))

        with tempfile.TemporaryDirectory() as path:
            child_spool = BreadcrumbDurableSpool(
                os.path.join(path, "child"), batch_size=1, flush_interval_seconds=0.01,
            )
            self.assertTrue(child_spool.enqueue(child_record, True))
            child_health = child_spool.close(timeout=2.0)
            with open(os.path.join(path, "child", "breadcrumbs.jsonl"), encoding="utf-8") as handle:
                child_durable = handle.read()

            parent_collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=os.path.join(path, "parent"),
            )
            self.assertEqual(
                "retained",
                parent_collector.record(
                    "scanner", "scan_failed", details,
                    category="scan.failure", event_type="failure",
                ),
            )
            self.assertTrue(parent_collector.flush_durable(2.0))
            parent_health = parent_collector.close(timeout=2.0)
            with open(
                os.path.join(path, "parent", "breadcrumbs.jsonl"),
                encoding="utf-8",
            ) as handle:
                parent_durable = handle.read()

        all_paths = {
            "child": child_encoded,
            "child_durable": child_durable,
            "parent_durable": parent_durable,
        }
        for label, serialized in all_paths.items():
            with self.subTest(path=label):
                self.assertNotIn("middle-key-secret", serialized)
                self.assertNotIn("nested-token-secret", serialized)
                self.assertNotIn("nested-api-secret", serialized)
        self.assertTrue(child_health["terminal"])
        self.assertTrue(parent_health["terminal"])

    def test_flush_failure_and_partial_tail_leave_truthful_empty_jsonl(self):
        class FailingFlushWriter:
            def __init__(self, handle):
                self.handle = handle

            def write(self, value):
                return self.handle.write(value)

            def seek(self, *args):
                return self.handle.seek(*args)

            def truncate(self):
                return self.handle.truncate()

            def flush(self):
                raise OSError("flush failed")

            def close(self):
                self.handle.close()

        class PartialWriter(FailingFlushWriter):
            def write(self, value):
                self.handle.write(value[:max(1, len(value) // 2)])
                return len(value) - 1

            def flush(self):
                return self.handle.flush()

        for writer_type, expected_error in (
            (FailingFlushWriter, "flush_failure"),
            (PartialWriter, "partial_write"),
        ):
            with self.subTest(writer=writer_type.__name__), tempfile.TemporaryDirectory() as path:
                spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
                spool.close(timeout=2.0)
                active = os.path.join(path, "breadcrumbs.jsonl")
                raw = open(active, "w+b")
                spool._BreadcrumbDurableSpool__file_handle = writer_type(raw)
                spool._BreadcrumbDurableSpool__file_path = active
                spool._BreadcrumbDurableSpool__file_size = 0
                spool._BreadcrumbDurableSpool__write_batch([
                    ({"source": "worker", "message": "event"}, True, 32),
                ])
                health = spool.snapshot()
                self.assertEqual(expected_error, health["last_error_class"])
                self.assertEqual(0, health["written"])
                self.assertEqual(1, health["lost"])
                with open(active, "rb") as handle:
                    self.assertEqual(b"", handle.read())

    def test_retrieval_is_bounded_and_returns_sanitized_durable_correlated_events(self):
        if os.name == "nt":
            self.skipTest("Windows durable file acknowledgement is buffered/unverified")
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            collector.record(
                "controller",
                "failure",
                {"password": "must-not-persist"},
                category="queue.lifecycle",
                event_type="failure",
                corr_id="flow-1",
            )
            self.assertTrue(collector.flush_durable(2.0))
            queried = collector.query_events(correlation_id="flow-1", limit=4)
            self.assertEqual(1, len(queried["events"]))
            self.assertNotIn("must-not-persist", json.dumps(queried))
            exported = collector.export_events(
                correlation_id="flow-1", max_events=1, max_bytes=8 * 1024,
            )
            self.assertLessEqual(
                len(json.dumps(exported, separators=(",", ":")).encode("utf-8")),
                8 * 1024,
            )
            self.assertEqual(1, exported["export_event_count"])
            collector.close(timeout=2.0)

    def test_producer_latency_and_loss_stay_bounded_under_writer_backlog(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(
                path,
                total_capacity=4,
                critical_capacity=2,
                normal_capacity=2,
                batch_size=1,
                flush_interval_seconds=10.0,
            )
            entered = threading.Event()
            release = threading.Event()
            original_write = spool._BreadcrumbDurableSpool__write_batch

            def blocked_write(batch):
                entered.set()
                release.wait(timeout=2.0)
                original_write(batch)

            spool._BreadcrumbDurableSpool__write_batch = blocked_write
            attempts = 1
            self.assertTrue(spool.enqueue({"source": "worker", "message": "first"}, False))
            self.assertTrue(entered.wait(timeout=1.0))
            durations = []
            for index in range(128):
                started = time.perf_counter()
                spool.enqueue({"source": "worker", "message": "backlog", "index": index}, False)
                durations.append(time.perf_counter() - started)
                attempts += 1
            release.set()
            health = spool.close(timeout=3.0)

        self.assertLess(max(durations), 0.5)
        self.assertLess(sum(durations), 1.0)
        self.assertEqual(attempts, health["accepted"] + health["lost"])
        self.assertGreater(health["normal_lost"], 0)

    def test_steady_burst_coalesces_write_and_fsync_batches_before_terminal_close(self):
        if os.name == "nt":
            self.skipTest("Windows durable file acknowledgement is buffered/unverified")
        with tempfile.TemporaryDirectory() as path:
            batch_size = 8
            record_count = 32
            original_take = BreadcrumbDurableSpool._BreadcrumbDurableSpool__take_batch
            parked = threading.Event()
            release = threading.Event()
            first_take = [True]
            batch_lengths = []

            def parked_take(instance):
                if first_take[0]:
                    first_take[0] = False
                    parked.set()
                    release.wait(timeout=2.0)
                return original_take(instance)

            with patch.object(
                BreadcrumbDurableSpool,
                "_BreadcrumbDurableSpool__take_batch",
                parked_take,
            ):
                spool = BreadcrumbDurableSpool(
                    path,
                    total_capacity=64,
                    critical_capacity=8,
                    normal_capacity=56,
                    batch_size=batch_size,
                    flush_interval_seconds=0.05,
                )
                self.assertTrue(parked.wait(timeout=1.0))

                original_write = spool._BreadcrumbDurableSpool__write_batch

                def counted_write(batch):
                    batch_lengths.append(len(batch))
                    original_write(batch)

                spool._BreadcrumbDurableSpool__write_batch = counted_write
                accepted = sum(
                    spool.enqueue(
                        {"source": "worker", "message": "steady", "index": index},
                        False,
                    )
                    for index in range(record_count)
                )
                self.assertEqual(record_count, accepted)
                release.set()
                health = spool.close(timeout=3.0)

        self.assertTrue(batch_lengths)
        self.assertGreater(max(batch_lengths), 1)
        self.assertLess(len(batch_lengths), record_count)
        self.assertEqual(record_count, sum(batch_lengths))
        self.assertEqual(len(batch_lengths), health["flush_count"])
        self.assertEqual(record_count, health["accepted"])
        self.assertEqual(record_count, health["written"])
        self.assertEqual(0, health["lost"])
        self.assertTrue(health["terminal"])
        self.assertFalse(health["incomplete"])

    def test_full_batch_coalesces_stale_wakeups_and_spaces_health_publication(self):
        if os.name == "nt":
            self.skipTest("Windows durable health sidecar is unsupported")
        flush_interval = 0.1
        with tempfile.TemporaryDirectory() as path:
            original_take = BreadcrumbDurableSpool._BreadcrumbDurableSpool__take_batch
            parked = threading.Event()
            release = threading.Event()
            first_take = [True]

            def parked_take(instance):
                if first_take[0]:
                    first_take[0] = False
                    parked.set()
                    release.wait(timeout=2.0)
                return original_take(instance)

            with patch.object(
                BreadcrumbDurableSpool,
                "_BreadcrumbDurableSpool__take_batch",
                parked_take,
            ):
                spool = BreadcrumbDurableSpool(
                    path,
                    total_capacity=16,
                    critical_capacity=4,
                    normal_capacity=12,
                    batch_size=8,
                    flush_interval_seconds=flush_interval,
                )
                self.assertTrue(parked.wait(timeout=1.0))
                published = []
                original_publish = spool._BreadcrumbDurableSpool__publish_health

                def counted_publish(*args, **kwargs):
                    current = spool.snapshot()
                    published.append((time.monotonic(), current["state"]))
                    return original_publish(*args, **kwargs)

                spool._BreadcrumbDurableSpool__publish_health = counted_publish
                write_done = threading.Event()
                batch_lengths = []
                original_write = spool._BreadcrumbDurableSpool__write_batch

                def counted_write(batch):
                    batch_lengths.append(len(batch))
                    result = original_write(batch)
                    write_done.set()
                    return result

                spool._BreadcrumbDurableSpool__write_batch = counted_write
                for index in range(8):
                    self.assertTrue(spool.enqueue({
                        "source": "worker",
                        "message": "full-batch",
                        "index": index,
                    }, False))
                # Event is level-triggered, so repeated sets model stale
                # wakeups without creating one publication per producer call.
                for _index in range(32):
                    spool._BreadcrumbDurableSpool__wake.set()
                release.set()
                self.assertTrue(write_done.wait(timeout=2.0))

                deadline = time.monotonic() + 1.0
                while len(published) < 1 and time.monotonic() < deadline:
                    time.sleep(0.005)
                running_publications = [
                    item for item in published if item[1] == "RUNNING"
                ]
                self.assertEqual(1, len(running_publications))
                for _index in range(32):
                    spool._BreadcrumbDurableSpool__wake.set()
                time.sleep(flush_interval * 0.2)
                stale_wakeup_publications = [
                    item for item in published if item[1] == "RUNNING"
                ]
                self.assertEqual(1, len(stale_wakeup_publications))

                deadline = time.monotonic() + flush_interval * 3.0
                while len([
                    item for item in published if item[1] == "RUNNING"
                ]) < 2 and time.monotonic() < deadline:
                    time.sleep(0.005)
                running_publications = [
                    item for item in published if item[1] == "RUNNING"
                ]
                self.assertGreaterEqual(len(running_publications), 2)
                self.assertGreaterEqual(
                    running_publications[1][0] - running_publications[0][0],
                    flush_interval * 0.75,
                )

                health = spool.close(timeout=3.0)

        self.assertEqual([8], batch_lengths)
        self.assertGreaterEqual(len(running_publications), 2)
        self.assertTrue(health["terminal"])
        self.assertFalse(health["incomplete"])
        self.assertEqual(8, health["written"])

    def test_partial_batch_waits_only_until_configured_deadline(self):
        if os.name == "nt":
            self.skipTest("Windows durable file acknowledgement is buffered/unverified")
        flush_interval = 0.06
        with tempfile.TemporaryDirectory() as path:
            original_take = BreadcrumbDurableSpool._BreadcrumbDurableSpool__take_batch
            parked = threading.Event()
            release = threading.Event()
            first_take = [True]

            def parked_take(instance):
                if first_take[0]:
                    first_take[0] = False
                    parked.set()
                    release.wait(timeout=2.0)
                return original_take(instance)

            with patch.object(
                BreadcrumbDurableSpool,
                "_BreadcrumbDurableSpool__take_batch",
                parked_take,
            ):
                spool = BreadcrumbDurableSpool(
                    path,
                    total_capacity=8,
                    critical_capacity=2,
                    normal_capacity=6,
                    batch_size=4,
                    flush_interval_seconds=flush_interval,
                )
                self.assertTrue(parked.wait(timeout=1.0))
                write_started = threading.Event()
                batch_lengths = []
                original_write = spool._BreadcrumbDurableSpool__write_batch

                def timed_write(batch):
                    batch_lengths.append(len(batch))
                    write_started.set()
                    return original_write(batch)

                spool._BreadcrumbDurableSpool__write_batch = timed_write
                self.assertTrue(spool.enqueue({
                    "source": "worker", "message": "partial-deadline",
                }, False))
                # The enqueue wake is stale for this deadline measurement;
                # clear it while the writer is still parked before releasing
                # the first take.
                spool._BreadcrumbDurableSpool__wake.clear()
                started = time.monotonic()
                release.set()
                self.assertTrue(write_started.wait(timeout=2.0))
                elapsed = time.monotonic() - started
                health = spool.close(timeout=3.0)

        self.assertEqual([1], batch_lengths)
        self.assertGreaterEqual(elapsed, flush_interval * 0.75)
        self.assertLess(elapsed, flush_interval * 4.0)
        self.assertTrue(health["terminal"])
        self.assertFalse(health["incomplete"])
        self.assertEqual(1, health["written"])

    def test_failed_close_keeps_closing_gate_and_allows_bounded_retry(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        emitter = collector.create_emitter()
        critical_queue = collector._BreadcrumbTraceCollector__critical_external_records
        normal_queue = collector._BreadcrumbTraceCollector__normal_external_records
        lifecycle_lock = collector._BreadcrumbTraceCollector__durable_lifecycle_lock
        self.assertTrue(lifecycle_lock.acquire(False))
        try:
            failed = collector.close(timeout=0.02)
        finally:
            lifecycle_lock.release()

        self.assertTrue(failed["incomplete"])
        self.assertEqual(
            _BREADCRUMB_LIFECYCLE_CLOSING,
            collector._BreadcrumbTraceCollector__ingress_closed_gate.value,
        )
        self.assertFalse(collector._BreadcrumbTraceCollector__collector_closed)
        self.assertIs(critical_queue, collector._BreadcrumbTraceCollector__critical_external_records)
        self.assertIs(normal_queue, collector._BreadcrumbTraceCollector__normal_external_records)
        before = collector.ingress_snapshot()
        self.assertEqual("disabled", emitter.record("controller", "after-close-failure"))
        self.assertEqual(before, collector.ingress_snapshot())

        # The failed close did not strand the lifecycle lock or queue owner.
        # A retry can take ownership and transition the gate exactly once.
        retried = collector.close(timeout=2.0)
        self.assertEqual(
            _BREADCRUMB_LIFECYCLE_CLOSED,
            collector._BreadcrumbTraceCollector__ingress_closed_gate.value,
        )
        self.assertTrue(collector._BreadcrumbTraceCollector__collector_closed)
        self.assertIsInstance(retried, dict)
        self.assertIsNone(collector._BreadcrumbTraceCollector__critical_external_records)
        self.assertIsNone(collector._BreadcrumbTraceCollector__normal_external_records)
        collector.close(timeout=0.1)

    def test_retired_incomplete_owner_keeps_threads_and_queues_excluded(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            emitter = collector.create_emitter()
            spool = collector._BreadcrumbTraceCollector__durable_spool
            critical_queue = collector._BreadcrumbTraceCollector__critical_external_records
            normal_queue = collector._BreadcrumbTraceCollector__normal_external_records
            self.assertIsNotNone(spool)
            assert spool is not None
            release = threading.Event()
            entered = threading.Event()
            original_write = spool._BreadcrumbDurableSpool__write_batch

            def blocked_write(batch):
                entered.set()
                release.wait(timeout=2.0)
                original_write(batch)

            spool._BreadcrumbDurableSpool__write_batch = blocked_write
            try:
                collector.record("controller", "blocked", category="queue.lifecycle")
                self.assertTrue(entered.wait(timeout=1.0))
                with patch("common.breadcrumb_trace.DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS", 0.05):
                    durable_state = [True]
                    # Use a fresh getter so disable/re-enable exercises the
                    # owner reconciliation path rather than the system gate.
                    collector._BreadcrumbTraceCollector__durable_enabled_getter = lambda: durable_state[0]
                    durable_state[0] = False
                    collector.sync_enabled_state()
                incomplete = collector.durable_snapshot()
                self.assertEqual("INCOMPLETE", collector._BreadcrumbTraceCollector__durable_owner_state)
                self.assertTrue(incomplete["incomplete"])
                self.assertIs(spool, collector._BreadcrumbTraceCollector__durable_spool)
                self.assertTrue(spool._BreadcrumbDurableSpool__thread.is_alive())
                self.assertIs(critical_queue, collector._BreadcrumbTraceCollector__critical_external_records)
                self.assertIs(normal_queue, collector._BreadcrumbTraceCollector__normal_external_records)
                self.assertEqual("dropped", emitter.record("controller", "excluded"))
                self.assertIs(spool, collector._BreadcrumbTraceCollector__durable_spool)
            finally:
                release.set()
                collector.close(timeout=2.0)

    def test_direct_mutators_are_excluded_by_open_admission_lock(self):
        correlation = "lftp-poll:" + ("a" * 16)
        root_correlation = "root-progress:" + ("b" * 16)
        operations = (
            ("root_health", lambda collector: collector.record_root_progress_health(
                "summary_observed", "summary",
            )),
            ("lineage", lambda collector: collector.record_progress_lineage(
                correlation, "model_mutation", {"outcome": "mutated", "model_version": 1},
            )),
            ("lineage_model_version", lambda collector: collector.record_progress_lineage_for_model_version(
                1, "scoped_stream_emit", {},
            )),
            ("active_delta", lambda collector: collector.record_active_delta_rejection(
                root_correlation, 1, "active_delta_authorization_rejected", {},
            )),
            ("policy", lambda collector: collector.apply_policy(
                {"default": "debug"},
            )),
            ("clear", lambda collector: collector.clear()),
            ("reset", lambda collector: collector.reset()),
        )
        for name, operation in operations:
            with self.subTest(operation=name):
                collector = BreadcrumbTraceCollector(
                    lambda: True,
                    policy={"default": "debug"},
                )
                before = collector.snapshot()
                before_policy = collector.policy_snapshot()
                gate = collector._BreadcrumbTraceCollector__ingress_admission_lock
                self.assertTrue(gate.acquire(False))
                try:
                    result = operation(collector)
                finally:
                    gate.release()
                self.assertFalse(result if isinstance(result, bool) else result.get("applied", result.get("cleared")))
                after = collector.snapshot()
                after_policy = collector.policy_snapshot()
                self.assertEqual(before["entries"], after["entries"])
                self.assertEqual(before["progress_lineage"], after["progress_lineage"])
                self.assertEqual(before["active_delta_rejection_summary"], after["active_delta_rejection_summary"])
                self.assertEqual(before_policy["revision"], after_policy["revision"])
                collector.close(timeout=2.0)
                closed_before = collector.snapshot()
                closed_result = operation(collector)
                self.assertFalse(
                    closed_result if isinstance(closed_result, bool)
                    else closed_result.get("applied", closed_result.get("cleared")),
                )
                closed_after = collector.snapshot()
                self.assertEqual(closed_before["entries"], closed_after["entries"])
                self.assertEqual(closed_before["progress_lineage"], closed_after["progress_lineage"])
                self.assertEqual(
                    closed_before["active_delta_rejection_summary"],
                    closed_after["active_delta_rejection_summary"],
                )

    def test_real_timeout_retirement_allows_one_new_durable_owner(self):
        with tempfile.TemporaryDirectory() as path:
            durable_state = [True]
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_enabled_getter=lambda: durable_state[0],
                durable_path=path,
            )
            old_spool = collector._BreadcrumbTraceCollector__durable_spool
            self.assertIsNotNone(old_spool)
            assert old_spool is not None
            entered = threading.Event()
            release = threading.Event()
            original_write = old_spool._BreadcrumbDurableSpool__write_batch

            def blocked_write(batch):
                entered.set()
                release.wait(timeout=2.0)
                original_write(batch)

            old_spool._BreadcrumbDurableSpool__write_batch = blocked_write
            try:
                collector.record("controller", "blocked", category="queue.lifecycle")
                self.assertTrue(entered.wait(timeout=1.0))
                with patch("common.breadcrumb_trace.DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS", 0.05):
                    durable_state[0] = False
                    collector.sync_enabled_state()
                    self.assertEqual("INCOMPLETE", collector._BreadcrumbTraceCollector__durable_owner_state)
                    self.assertIs(old_spool, collector._BreadcrumbTraceCollector__durable_spool)
                    self.assertTrue(old_spool._BreadcrumbDurableSpool__thread.is_alive())

                    # Re-enable while the old owner is still alive: no second
                    # spool or drainer may be created beside it.
                    durable_state[0] = True
                    collector.sync_enabled_state()
                    self.assertIs(old_spool, collector._BreadcrumbTraceCollector__durable_spool)
                    self.assertEqual("INCOMPLETE", collector._BreadcrumbTraceCollector__durable_owner_state)

                release.set()
                old_spool._BreadcrumbDurableSpool__thread.join(timeout=2.0)
                self.assertFalse(old_spool._BreadcrumbDurableSpool__thread.is_alive())
                collector.sync_enabled_state()
                new_spool = collector._BreadcrumbTraceCollector__durable_spool
                self.assertIsNot(old_spool, new_spool)
                self.assertEqual("RUNNING", collector._BreadcrumbTraceCollector__durable_owner_state)
            finally:
                release.set()
                durable_state[0] = False
                collector.sync_enabled_state()
                collector.close(timeout=2.0)

    def test_reservation_transitions_ack_and_queue_failure_without_leak(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        emitter = collector.create_emitter()
        reservation = collector._BreadcrumbTraceCollector__critical_ingress_reservation_count
        enqueued = collector._BreadcrumbTraceCollector__critical_ingress_enqueued_count
        ack = collector._BreadcrumbTraceCollector__critical_ingress_ack_count
        observed = []

        class AcknowledgingQueue:
            def put_nowait(self, _value):
                with reservation.get_lock():
                    observed.append(int(reservation.value))
                with ack.get_lock():
                    ack.value += 1

        emitter._BreadcrumbTraceEmitter__critical_record_queue = AcknowledgingQueue()
        self.assertEqual("enqueued", emitter.record("controller", "queued", category="queue.lifecycle"))
        self.assertEqual([1], observed)
        self.assertEqual(1, enqueued.value)
        self.assertEqual(0, reservation.value)
        self.assertEqual(0, collector.ingress_snapshot()["critical_pending"])

        class FullQueue:
            def put_nowait(self, _value):
                raise queue.Full

        emitter._BreadcrumbTraceEmitter__critical_record_queue = FullQueue()
        self.assertEqual("dropped", emitter.record("controller", "full", category="queue.lifecycle"))
        self.assertEqual(0, reservation.value)
        failed_ingress = collector.ingress_snapshot()
        self.assertEqual(0, failed_ingress["critical_reserved"])
        self.assertEqual(1, failed_ingress["critical_enqueued"])
        self.assertGreaterEqual(failed_ingress["critical_rejected_count"], 1)
        collector.close(timeout=2.0)

    def test_flush_is_false_after_writer_loss(self):
        with tempfile.TemporaryDirectory() as parent:
            bad_path = os.path.join(parent, "not-a-directory")
            with open(bad_path, "w", encoding="utf-8") as handle:
                handle.write("x")
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=bad_path,
            )
            collector.record("controller", "writer-failure", category="queue.lifecycle")
            deadline = time.monotonic() + 1.0
            while (
                (
                    collector.durable_snapshot().get("writer_failures", 0) == 0
                    or collector.durable_snapshot().get("writing", False)
                )
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            writer_health = collector.durable_snapshot()
            self.assertGreaterEqual(writer_health["lost"], 1)
            self.assertFalse(writer_health["writing"])
            writer_flush = collector.flush_durable(0.1)
            collector.close(timeout=2.0)
            self.assertFalse(writer_flush)

    def test_flush_is_false_after_retention_loss(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            spool = collector._BreadcrumbTraceCollector__durable_spool
            self.assertIsNotNone(spool)
            assert spool is not None
            spool._BreadcrumbDurableSpool__retention_failed = True
            collector.record("controller", "retention-failure", category="queue.lifecycle")
            deadline = time.monotonic() + 1.0
            while (
                (
                    collector.durable_snapshot().get("lost", 0) == 0
                    or collector.durable_snapshot().get("writing", False)
                )
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            retention_flush = collector.flush_durable(0.1)
            collector.close(timeout=2.0)
            self.assertFalse(retention_flush)

    def test_flush_is_false_after_collector_rejection(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        emitter = collector.create_emitter()

        class FullQueue:
            def put_nowait(self, _value):
                raise queue.Full

            def close(self):
                return None

            def cancel_join_thread(self):
                return None

        emitter._BreadcrumbTraceEmitter__record_queue = FullQueue()
        self.assertEqual("dropped", emitter.record("worker", "rejected"))
        rejection_flush = collector.flush_durable(0.1)
        collector.close(timeout=2.0)
        self.assertFalse(rejection_flush)

    def test_offline_health_and_jsonl_retrieval_survives_memory_api_stall(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            collector.record(
                "controller", "durable", category="queue.lifecycle", corr_id="flow-1",
            )
            if os.name == "nt":
                health = collector.durable_snapshot()
                self.assertEqual("buffered_best_effort", health["durability"])
                self.assertEqual("windows_acl_unverified", health["path_safety"])
                self.assertFalse(health["health_publish_ok"])
                self.assertFalse(os.path.exists(os.path.join(path, "breadcrumbs.health.json")))
                collector.close(timeout=2.0)
                return
            self.assertTrue(collector.flush_durable(2.0))
            collector_lock = collector._BreadcrumbTraceCollector__lock
            self.assertTrue(collector_lock.acquire(False))
            try:
                with open(os.path.join(path, "breadcrumbs.health.json"), encoding="utf-8") as handle:
                    health = json.load(handle)
                with open(os.path.join(path, "breadcrumbs.jsonl"), encoding="utf-8") as handle:
                    events = [
                        record for line in handle if line.strip()
                        for record in [json.loads(line)] if "message" in record
                    ]
                self.assertEqual("breadcrumb_durable_health.v1", health["schema"])
                self.assertIsInstance(health["session_id"], str)
                self.assertEqual("durable", events[0]["message"])
            finally:
                collector_lock.release()
            collector.close(timeout=2.0)

    def test_default_mounted_retrieval_is_available_and_unavailable_is_explicit(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            capabilities = collector.capabilities()
            self.assertTrue(capabilities["query"])
            self.assertTrue(capabilities["export"])
            collector.record("controller", "mounted", category="queue.lifecycle")
            if os.name == "nt":
                health = collector.durable_snapshot()
                self.assertEqual("buffered_best_effort", health["durability"])
                self.assertEqual("windows_acl_unverified", health["path_safety"])
                self.assertFalse(health["health_publish_ok"])
                self.assertFalse(os.path.exists(os.path.join(path, "breadcrumbs.health.json")))
                collector.close(timeout=2.0)
                return
            self.assertTrue(collector.flush_durable(2.0))
            health = collector.durable_snapshot()
            self.assertTrue(health["enabled"])
            self.assertTrue(os.path.exists(os.path.join(path, "breadcrumbs.health.json")))
            collector.close(timeout=2.0)

        with tempfile.TemporaryDirectory() as parent:
            unavailable = os.path.join(parent, "not-a-directory")
            with open(unavailable, "w", encoding="utf-8") as handle:
                handle.write("x")
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=unavailable,
            )
            collector.record("controller", "memory-only", category="queue.lifecycle")
            health = collector.durable_snapshot()
            self.assertIn(health["last_error_class"], {
                "health_publish_failure", "storage_unavailable", "permission_denied",
                "retention_failure",
            })
            self.assertEqual(1, len(collector.query_events()["events"]))
            collector.close(timeout=2.0)

    def test_ingress_bounds_large_key_string_int_mapping_and_list_work(self):
        details = {
            "secret_" + ("x" * 100_000): "do-not-copy",
            "huge_string": "s" * 1_000_000,
            "huge_int": 10 ** 200,
            "huge_mapping": {"key-{}".format(index): index for index in range(10_000)},
            "huge_list": ["item"] * 10_000,
        }
        revision = multiprocessing.Value("L", 3)
        epoch = multiprocessing.Value("L", 4)
        started = time.perf_counter()
        record = _bounded_ingress_record(
            "scanner", "scan_failed", details, {}, time.time_ns(), revision, epoch,
        )
        elapsed = time.perf_counter() - started
        self.assertIsNotNone(record)
        self.assertLess(elapsed, 0.5)
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(encoded.encode("utf-8")), 8 * 1024)
        self.assertNotIn("do-not-copy", encoded)

    def test_parent_symlink_is_fail_closed_for_durable_output(self):
        if os.name == "nt":
            self.skipTest("portable symlink creation is not guaranteed on Windows")
        with tempfile.TemporaryDirectory() as parent:
            target = os.path.join(parent, "target")
            os.mkdir(target)
            link = os.path.join(parent, "link")
            try:
                os.symlink(target, link, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest("symlink unavailable: {}".format(error))
            spool = BreadcrumbDurableSpool(link, batch_size=1, flush_interval_seconds=0.01)
            spool.enqueue({"source": "worker", "message": "must-not-follow"}, False)
            health = spool.close(timeout=2.0)
            self.assertIn(health["last_error_class"], {
                "retention_failure", "storage_unavailable", "health_publish_failure",
            })
            self.assertFalse(os.path.exists(os.path.join(target, "breadcrumbs.jsonl")))
            self.assertFalse(os.path.exists(os.path.join(target, "breadcrumbs.health.json")))

    def test_health_publication_uses_fsync_and_writer_failure_latch_is_bounded(self):
        real_fsync = os.fsync
        with tempfile.TemporaryDirectory() as parent:
            good_path = os.path.join(parent, "good")
            bad_path = os.path.join(parent, "not-a-directory")
            with open(bad_path, "w", encoding="utf-8") as handle:
                handle.write("x")
            with patch("common.breadcrumb_trace.os.fsync", wraps=real_fsync) as fsync:
                healthy = BreadcrumbDurableSpool(good_path, batch_size=1, flush_interval_seconds=0.01)
                self.assertTrue(healthy.enqueue({"source": "worker", "message": "healthy"}, False))
                healthy.close(timeout=1.0)
                spool = BreadcrumbDurableSpool(bad_path, batch_size=1, flush_interval_seconds=0.01)
                deadline = time.monotonic() + 1.0
                while spool.snapshot()["writer_failures"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                first_failure_count = spool.snapshot()["writer_failures"]
                # Boundary publication fails before admission can expose a
                # record, so the owner is explicitly latched unavailable.
                self.assertGreaterEqual(first_failure_count, 1)
                self.assertFalse(spool.enqueue({"source": "worker", "message": "first"}, False))
                # Once durable output is known unusable, admission is latched
                # off; callers must not keep feeding a failing writer.
                self.assertFalse(spool.enqueue({"source": "worker", "message": "second"}, False))
                time.sleep(0.05)
                second = spool.snapshot()
                self.assertGreaterEqual(fsync.call_count, 1)
                self.assertGreaterEqual(first_failure_count, 1)
                self.assertLessEqual(second["writer_failures"], first_failure_count + 1)
                self.assertIn("last_error_class", second)
                spool.close(timeout=1.0)

    def test_idle_writer_heartbeat_refreshes_health_with_bounded_interval(self):
        calls = []
        original_publish = BreadcrumbDurableSpool._BreadcrumbDurableSpool__publish_health

        def counted_publish(instance):
            calls.append(time.monotonic())
            original_publish(instance)

        with tempfile.TemporaryDirectory() as path:
            with patch.object(
                BreadcrumbDurableSpool,
                "_BreadcrumbDurableSpool__publish_health",
                counted_publish,
            ):
                spool = BreadcrumbDurableSpool(
                    path,
                    batch_size=1,
                    flush_interval_seconds=0.01,
                )
                time.sleep(0.05)
                spool.close(timeout=1.0)
        self.assertGreaterEqual(len(calls), 3)
        self.assertLess(max(calls) - min(calls), 1.0)

    def test_open_lock_miss_then_concurrent_close_has_one_final_disposition(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        emitter = collector.create_emitter()
        admission_lock = collector._BreadcrumbTraceCollector__ingress_admission_lock
        self.assertTrue(admission_lock.acquire(False))
        close_result = {}
        try:
            with patch("common.breadcrumb_trace.DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS", 0.05):
                closer = threading.Thread(
                    target=lambda: close_result.update(collector.close(timeout=0.05)),
                )
                closer.start()
                # Calls that began while OPEN but could not acquire the
                # admission lock are accounted as rejection, not accepted.
                self.assertEqual("dropped", emitter.record("controller", "open-lock-miss"))
                self.assertEqual("dropped", collector.record("controller", "open-lock-miss"))
                time.sleep(0.1)
                self.assertFalse(closer.is_alive())
        finally:
            admission_lock.release()
        closer.join(timeout=1.0)
        self.assertFalse(closer.is_alive())
        self.assertTrue(close_result["incomplete"])
        self.assertEqual(
            _BREADCRUMB_LIFECYCLE_CLOSING,
            collector._BreadcrumbTraceCollector__ingress_closed_gate.value,
        )
        before = collector.ingress_snapshot()
        self.assertEqual("disabled", emitter.record("controller", "post-closing"))
        self.assertEqual("disabled", collector.record("controller", "post-closing"))
        self.assertEqual(before, collector.ingress_snapshot())
        collector.close(timeout=2.0)

    def test_live_direct_token_blocks_terminal_close_and_late_emitter_is_excluded(self):
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            emitter = collector.create_emitter()
            entered = threading.Event()
            release = threading.Event()
            original_entry = collector._BreadcrumbTraceCollector__record_entry

            def blocked_entry(*args, **kwargs):
                entered.set()
                release.wait(timeout=2.0)
                return original_entry(*args, **kwargs)

            result = {}
            try:
                with patch.object(
                    collector,
                    "_BreadcrumbTraceCollector__record_entry",
                    side_effect=blocked_entry,
                ):
                    producer = threading.Thread(
                        target=lambda: result.update({
                            "result": collector.record(
                                "controller", "direct-token", category="queue.lifecycle",
                            ),
                        }),
                    )
                    producer.start()
                    self.assertTrue(entered.wait(timeout=1.0))
                    with patch("common.breadcrumb_trace.DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS", 0.05):
                        health = collector.close(timeout=0.05)
                    self.assertTrue(health["incomplete"])
                    self.assertTrue(producer.is_alive())
                    self.assertEqual(
                        _BREADCRUMB_LIFECYCLE_CLOSING,
                        collector._BreadcrumbTraceCollector__ingress_closed_gate.value,
                    )
                    self.assertEqual("disabled", emitter.record("controller", "late-emitter"))
                    release.set()
                    producer.join(timeout=1.0)
                    self.assertFalse(producer.is_alive())
                collector.close(timeout=2.0)
            finally:
                release.set()
                collector.close(timeout=2.0)
            self.assertEqual("retained", result["result"])

    def test_post_detach_final_lock_failure_preserves_cache_api_and_sidecar(self):
        class FailSecondCollectorLock:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.calls = 0
                self.entered = False

            def acquire(self, *args, **kwargs):
                self.calls += 1
                if self.calls >= 2:
                    return False
                return self.wrapped.acquire(*args, **kwargs)

            def release(self):
                return self.wrapped.release()

            def __enter__(self):
                self.entered = bool(self.acquire(False))
                return self

            def __exit__(self, *_args):
                if self.entered:
                    self.release()
                return False

        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            spool = collector._BreadcrumbTraceCollector__durable_spool
            original_lock = collector._BreadcrumbTraceCollector__lock
            self.assertIsNotNone(spool)
            assert spool is not None
            collector.record("controller", "before-final-lock", category="queue.lifecycle")
            real_close = spool.close

            def close_then_fail_final_lock(timeout, *, final_state="TERMINAL"):
                result = real_close(timeout, final_state=final_state)
                collector._BreadcrumbTraceCollector__lock = FailSecondCollectorLock(original_lock)
                return result

            spool.close = close_then_fail_final_lock
            result = collector.close(timeout=2.0)
            api = collector.durable_snapshot()
            self.assertTrue(result["terminal"] or result["incomplete"])
            sidecar_path = os.path.join(path, "breadcrumbs.health.json")
            if os.path.exists(sidecar_path):
                with open(sidecar_path, encoding="utf-8") as handle:
                    sidecar = json.load(handle)
                self.assertEqual(result["terminal"], sidecar["terminal"])
                self.assertEqual(result["incomplete"], sidecar["incomplete"])
                self.assertEqual(result["state"], sidecar["state"])
                self.assertEqual(sidecar["session_id"], api["session_id"])
                self.assertEqual(sidecar["terminal"], api["terminal"])
                self.assertEqual(sidecar["incomplete"], api["incomplete"])
            else:
                self.assertEqual("nt", os.name)
                self.assertEqual("buffered_best_effort", api["durability"])
            collector._BreadcrumbTraceCollector__lock = original_lock
            collector.close(timeout=2.0)

    def test_durable_snapshot_race_with_reenable_does_not_publish_stale_owner(self):
        with tempfile.TemporaryDirectory() as path:
            durable_state = [True]
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_enabled_getter=lambda: durable_state[0],
                durable_path=path,
            )
            old_spool = collector._BreadcrumbTraceCollector__durable_spool
            self.assertIsNotNone(old_spool)
            assert old_spool is not None
            entered = threading.Event()
            release = threading.Event()
            reader_ref = {}
            reader_result = {}
            original_snapshot = old_spool.snapshot

            def blocked_snapshot():
                if threading.current_thread() is reader_ref.get("thread"):
                    entered.set()
                    release.wait(timeout=2.0)
                return original_snapshot()

            old_spool.snapshot = blocked_snapshot
            try:
                reader = threading.Thread(
                    target=lambda: reader_result.update({"health": collector.durable_snapshot()}),
                )
                reader_ref["thread"] = reader
                reader.start()
                self.assertTrue(entered.wait(timeout=1.0))
                durable_state[0] = False
                collector.sync_enabled_state()
                durable_state[0] = True
                collector.sync_enabled_state()
                new_spool = collector._BreadcrumbTraceCollector__durable_spool
                if os.name == "nt":
                    # Windows exposes an explicit buffered/unverified health
                    # contract. Either a failed publication retains the old
                    # incomplete owner, or a settled owner is replaced; both
                    # cases must avoid pairing the reader with the new owner.
                    self.assertEqual("buffered_best_effort", collector.durable_snapshot()["durability"])
                    release.set()
                    reader.join(timeout=1.0)
                    self.assertFalse(reader.is_alive())
                    if new_spool is old_spool:
                        self.assertEqual("INCOMPLETE", collector._BreadcrumbTraceCollector__durable_owner_state)
                    else:
                        self.assertEqual("RUNNING", collector._BreadcrumbTraceCollector__durable_owner_state)
                        self.assertNotEqual(
                            reader_result["health"]["session_id"],
                            collector.durable_snapshot()["session_id"],
                        )
                    return
                self.assertIsNot(old_spool, new_spool)
                self.assertEqual("RUNNING", collector._BreadcrumbTraceCollector__durable_owner_state)
                release.set()
                reader.join(timeout=1.0)
                self.assertFalse(reader.is_alive())
                self.assertNotEqual(
                    reader_result["health"]["session_id"],
                    collector.durable_snapshot()["session_id"],
                )
            finally:
                release.set()
                old_spool.snapshot = original_snapshot
                durable_state[0] = False
                collector.sync_enabled_state()
                collector.close(timeout=2.0)

    def test_permission_enforcement_failure_writes_no_durable_files(self):
        with tempfile.TemporaryDirectory() as path:
            with patch(
                "common.breadcrumb_trace.os.chmod",
                side_effect=PermissionError("permission enforcement denied"),
            ):
                spool = BreadcrumbDurableSpool(
                    path,
                    batch_size=1,
                    flush_interval_seconds=0.01,
                )
                self.assertTrue(spool.enqueue({"source": "worker", "message": "blocked"}, False))
                health = spool.close(timeout=2.0)
            self.assertIn(health["last_error_class"], {"health_publish_failure", "permission_denied"})
            self.assertFalse(os.path.exists(os.path.join(path, "breadcrumbs.jsonl")))
            self.assertFalse(os.path.exists(os.path.join(path, "breadcrumbs.health.json")))

    def test_mark_incomplete_returns_inmemory_unavailable_when_persistence_fails(self):
        if os.name == "nt":
            self.skipTest("POSIX unavailable offline-retrieval contract")
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(
                path, batch_size=1, flush_interval_seconds=0.01,
            )
            try:
                deadline = time.monotonic() + 2.0
                health = spool.snapshot()
                sidecar = None
                health_path = os.path.join(path, "breadcrumbs.health.json")
                while (
                    not health.get("session_boundary_written")
                    or sidecar is None
                    or not sidecar.get("session_boundary_written")
                    or sidecar.get("offline_retrieval") != "valid"
                ) and time.monotonic() < deadline:
                    time.sleep(0.005)
                    health = spool.snapshot()
                    try:
                        with open(health_path, encoding="utf-8") as handle:
                            sidecar = json.load(handle)
                    except (FileNotFoundError, OSError, ValueError):
                        sidecar = None
                self.assertTrue(health["session_boundary_written"])
                self.assertIsNotNone(sidecar)
                with open(health_path, "rb") as handle:
                    before_sidecar = handle.read()

                calls = {}

                def fail_io(name):
                    def denied(*_args, **_kwargs):
                        calls[name] = calls.get(name, 0) + 1
                        raise OSError("forced durable persistence failure")

                    return denied

                with patch("common.breadcrumb_trace.os.lstat", side_effect=fail_io("lstat")), \
                    patch("common.breadcrumb_trace.os.stat", side_effect=fail_io("stat")), \
                    patch("common.breadcrumb_trace.os.makedirs", side_effect=fail_io("makedirs")), \
                    patch("common.breadcrumb_trace.os.chmod", side_effect=fail_io("chmod")), \
                    patch("common.breadcrumb_trace.os.fchmod", side_effect=fail_io("fchmod")), \
                    patch("common.breadcrumb_trace.os.open", side_effect=fail_io("open")), \
                    patch("common.breadcrumb_trace.os.close", side_effect=fail_io("close")), \
                    patch("common.breadcrumb_trace.os.fsync", side_effect=fail_io("fsync")), \
                    patch("common.breadcrumb_trace.os.replace", side_effect=fail_io("replace")), \
                    patch("common.breadcrumb_trace.os.unlink", side_effect=fail_io("unlink")), \
                    patch("common.breadcrumb_trace.tempfile.mkstemp", side_effect=fail_io("mkstemp")), \
                    patch("common.breadcrumb_trace.os.fdopen", side_effect=fail_io("fdopen")):
                    started = time.perf_counter()
                    returned = spool.mark_incomplete(unresolved=3, unknown=4)
                    elapsed = time.perf_counter() - started

                self.assertLess(elapsed, 0.5)
                self.assertEqual("INCOMPLETE", returned["state"])
                self.assertTrue(returned["incomplete"])
                self.assertFalse(returned["terminal"])
                self.assertFalse(returned["closed"])
                self.assertFalse(returned["enabled"])
                self.assertTrue(returned["shutdown_incomplete"])
                self.assertFalse(returned["health_publish_ok"])
                self.assertIsNone(returned["last_error_class"])
                self.assertEqual("unavailable", returned["offline_retrieval"])
                self.assertFalse(returned["offline_retrieval_invalidated"])
                self.assertEqual(3, returned["unresolved"])
                self.assertEqual(4, returned["unknown"])
                with open(health_path, "rb") as handle:
                    self.assertEqual(before_sidecar, handle.read())
                self.assertFalse(os.path.exists(os.path.join(path, "breadcrumbs.health.invalid")))
                for name in (
                    "lstat", "stat", "makedirs", "chmod", "fchmod", "open",
                    "close", "fsync", "replace", "unlink", "mkstemp", "fdopen",
                ):
                    self.assertEqual(0, calls.get(name, 0), name)
            finally:
                spool.close(timeout=2.0)

    def test_direct_details_bound_mapping_iteration_without_full_copy(self):
        class HugeMapping(Mapping):
            def __init__(self):
                self.touched = 0

            def __iter__(self):
                for index in range(1_000_000):
                    self.touched += 1
                    yield "key-{}".format(index)

            def __getitem__(self, key):
                self.touched += 1
                return {"password": "direct-secret", "value": key}

            def __len__(self):
                return 1_000_000

        details = HugeMapping()
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        started = time.perf_counter()
        result = collector.record("scanner", "direct-details", details, category="scan.failure")
        elapsed = time.perf_counter() - started
        snapshot = collector.snapshot()
        collector.close(timeout=2.0)
        self.assertEqual("retained", result)
        self.assertLess(elapsed, 0.5)
        self.assertLessEqual(details.touched, 512)
        self.assertNotIn("direct-secret", json.dumps(snapshot, separators=(",", ":")))

    def test_mounted_offline_manifest_and_health_use_fixed_private_files(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
            spool.enqueue({"source": "controller", "message": "offline"}, True)
            spool.close(timeout=2.0)
            files = sorted(os.listdir(path))
            if os.name == "nt":
                health = spool.snapshot()
                self.assertEqual("buffered_best_effort", health["durability"])
                self.assertEqual("windows_acl_unverified", health["path_safety"])
                self.assertIn(files, (["breadcrumbs.jsonl"], ["breadcrumbs.health.json", "breadcrumbs.jsonl"]))
                if "breadcrumbs.health.json" in files:
                    with open(os.path.join(path, "breadcrumbs.health.json"), encoding="utf-8") as handle:
                        sidecar = json.load(handle)
                    self.assertEqual(health["session_id"], sidecar["session_id"])
                else:
                    self.assertFalse(health["health_publish_ok"])
                return
            self.assertEqual(["breadcrumbs.health.json", "breadcrumbs.jsonl"], files)
            with open(os.path.join(path, "breadcrumbs.health.json"), encoding="utf-8") as handle:
                health = json.load(handle)
            with open(os.path.join(path, "breadcrumbs.jsonl"), encoding="utf-8") as handle:
                events = [
                    record for line in handle if line.strip()
                    for record in [json.loads(line)] if "message" in record
                ]
            for key in (
                "schema", "session_id", "state", "enabled", "closed", "terminal",
                "incomplete", "health_publish_ok", "durability", "path_safety",
            ):
                self.assertIn(key, health)
            self.assertEqual("breadcrumb_durable_health.v1", health["schema"])
            self.assertTrue(health["terminal"])
            self.assertFalse(health["incomplete"])
            self.assertEqual(1, len(events))
            self.assertEqual("offline", events[0]["message"])
            self.assertNotIn("path", health)

    def test_mounted_reader_matches_writer_session_boundary_before_ordinary_records(self):
        if os.name == "nt":
            self.skipTest("Windows durable health sidecar is unsupported")
        with tempfile.TemporaryDirectory() as path:
            collector = BreadcrumbTraceCollector(
                lambda: True,
                durable_enabled=True,
                durable_path=path,
            )
            retrieval = collector.capabilities()["durable_retrieval"]
            if retrieval["availability"] == "unavailable":
                self.assertFalse(retrieval["offline"])
                self.assertEqual("unavailable", retrieval["availability"])
                collector.close(timeout=2.0)
                return
            self.assertEqual("configured_path", retrieval["availability"])
            self.assertEqual("fixed_app_owned_mounted_path", retrieval["mode"])
            self.assertEqual(
                "retained",
                collector.record("controller", "ordinary", category="queue.lifecycle"),
            )
            self.assertEqual(
                "retained",
                collector.record("controller", "ordinary-2", category="queue.lifecycle"),
            )
            self.assertTrue(collector.flush_durable(2.0))
            health = collector.durable_snapshot()
            collector.close(timeout=2.0)

            with open(os.path.join(path, "breadcrumbs.health.json"), encoding="utf-8") as handle:
                sidecar = json.load(handle)
            with open(os.path.join(path, "breadcrumbs.jsonl"), encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]

        self.assertEqual(sidecar["session_id"], health["session_id"])
        self.assertGreaterEqual(len(records), 2)
        boundary_index = next(
            index for index, record in enumerate(records)
            if record.get("schema") == "breadcrumb_durable_session_boundary.v1"
            or record.get("event_type") == "session_boundary"
            or record.get("session_boundary") is True
        )
        ordinary_index = next(
            index for index, record in enumerate(records)
            if record.get("message") == "ordinary"
        )
        boundary = records[boundary_index]
        self.assertLess(boundary_index, ordinary_index)
        self.assertEqual(sidecar["session_id"], boundary.get("session_id"))
        self.assertIn(boundary.get("schema"), {
            "breadcrumb_durable_session_boundary.v1", None,
        })
        self.assertEqual("ordinary", records[ordinary_index]["message"])
        ordinary_two_index = next(
            index for index, record in enumerate(records)
            if record.get("message") == "ordinary-2"
        )
        self.assertEqual("ordinary-2", records[ordinary_two_index]["message"])
        self.assertEqual(sidecar["session_id"], records[ordinary_index].get("session_id"))
        self.assertEqual(sidecar["session_id"], records[ordinary_two_index].get("session_id"))
        newest_session = next(
            (record.get("session_id") for record in reversed(records)
             if record.get("session_id") is not None),
            None,
        )
        self.assertEqual(sidecar["session_id"], newest_session)

    def test_fsynced_session_boundary_precedes_ordinary_and_valid_health(self):
        if os.name == "nt":
            self.skipTest("POSIX offline session-boundary contract")
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(
                path, batch_size=1, flush_interval_seconds=0.01,
            )
            try:
                deadline = time.monotonic() + 2.0
                health = spool.snapshot()
                sidecar_before_ordinary = None
                while (
                    (
                        not health.get("session_boundary_written")
                        or sidecar_before_ordinary is None
                        or not sidecar_before_ordinary.get("session_boundary_written")
                        or sidecar_before_ordinary.get("offline_retrieval") != "valid"
                    )
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                    health = spool.snapshot()
                    try:
                        with open(
                            os.path.join(path, "breadcrumbs.health.json"),
                            encoding="utf-8",
                        ) as handle:
                            sidecar_before_ordinary = json.load(handle)
                    except (FileNotFoundError, OSError, ValueError):
                        sidecar_before_ordinary = None
                self.assertTrue(health["session_boundary_written"])
                self.assertEqual("fsync", health["durability"])
                self.assertEqual("valid", health["offline_retrieval"])

                with open(
                    os.path.join(path, "breadcrumbs.jsonl"), encoding="utf-8",
                ) as handle:
                    boundary_only = [json.loads(line) for line in handle if line.strip()]
                self.assertGreaterEqual(len(boundary_only), 1)
                self.assertTrue(any(
                    record.get("schema") == "breadcrumb_durable_session_boundary.v1"
                    or record.get("event_type") == "session_boundary"
                    or record.get("session_boundary") is True
                    for record in boundary_only
                ))
                self.assertFalse(any(
                    record.get("message") == "ordinary"
                    for record in boundary_only
                ))
                self.assertIsNotNone(sidecar_before_ordinary)
                assert sidecar_before_ordinary is not None
                self.assertEqual(health["session_id"], sidecar_before_ordinary["session_id"])
                self.assertEqual("valid", sidecar_before_ordinary["offline_retrieval"])

                self.assertTrue(spool.enqueue(
                    {"source": "controller", "message": "ordinary"}, True,
                ))
            finally:
                final = spool.close(timeout=2.0)

            with open(
                os.path.join(path, "breadcrumbs.jsonl"), encoding="utf-8",
            ) as handle:
                records = [json.loads(line) for line in handle if line.strip()]
            ordinary_index = next(
                index for index, record in enumerate(records)
                if record.get("message") == "ordinary"
            )
            boundary_index = next(
                index for index, record in enumerate(records)
                if record.get("schema") == "breadcrumb_durable_session_boundary.v1"
                or record.get("event_type") == "session_boundary"
                or record.get("session_boundary") is True
            )
            self.assertLess(boundary_index, ordinary_index)
            self.assertEqual(final["session_id"], records[ordinary_index]["session_id"])
            self.assertTrue(final["session_boundary_written"])
            self.assertEqual("valid", final["offline_retrieval"])

    def test_seedsync_run_keeps_exclusion_while_no_emitter_direct_token_is_live(self):
        collector = BreadcrumbTraceCollector(lambda: True, durable_enabled=False)
        entered = threading.Event()
        release = threading.Event()
        direct_result = {}
        original_entry = collector._BreadcrumbTraceCollector__record_entry

        def blocked_entry(*args, **kwargs):
            entered.set()
            release.wait(timeout=2.0)
            return original_entry(*args, **kwargs)

        collector._BreadcrumbTraceCollector__record_entry = blocked_entry

        class TrackingExclusion:
            def __init__(self):
                self.release_count = 0

            def release(self):
                self.release_count += 1

        exclusion = TrackingExclusion()
        application = Seedsync.__new__(Seedsync)
        application.context = type("ContextStub", (), {"breadcrumb_trace": collector})()
        application.runtime_exclusion = exclusion
        close_result = {}
        original_close = collector.close

        def capture_first_close(*args, **kwargs):
            result = original_close(*args, **kwargs)
            if not close_result:
                close_result.update(result)
            return result

        collector.close = capture_first_close
        producer = threading.Thread(
            target=lambda: direct_result.update({
                "result": collector.record(
                    "controller", "direct-during-run-close", category="queue.lifecycle",
                ),
            }),
        )
        producer.start()
        self.assertTrue(entered.wait(timeout=1.0))
        try:
            with patch.object(application, "_run_with_exclusion", side_effect=ServiceExit()):
                with patch(
                    "common.breadcrumb_trace.DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS",
                    0.05,
                ):
                    with self.assertRaises(ServiceExit):
                        application.run()
            # The direct token is still inside the collector's admission body;
            # Seedsync must not release the process exclusion while that owner
            # can still mutate collector state after close timed out.
            self.assertEqual(1, close_result["inflight_tokens"])
            self.assertEqual("CLOSING", close_result["collector_lifecycle"])
            self.assertFalse(close_result["collector_retired"])
            self.assertEqual(0, exclusion.release_count)
            self.assertTrue(producer.is_alive())
            self.assertEqual(
                _BREADCRUMB_LIFECYCLE_CLOSING,
                collector._BreadcrumbTraceCollector__ingress_closed_gate.value,
            )
        finally:
            release.set()
            producer.join(timeout=1.0)
            collector.close(timeout=2.0)
        self.assertFalse(producer.is_alive())
        self.assertEqual("retained", direct_result["result"])

    def test_linux_offline_reader_rejects_stale_session_when_health_publish_fails(self):
        if os.name == "nt":
            self.skipTest("Linux trusted sidecar session contract")
        with tempfile.TemporaryDirectory() as path:
            health_path = os.path.join(path, "breadcrumbs.health.json")
            invalid_marker_path = os.path.join(path, "breadcrumbs.health.invalid")
            with open(health_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "schema": "breadcrumb_durable_health.v1",
                    "session_id": "stale-session",
                    "state": "TERMINAL",
                    "enabled": False,
                    "closed": True,
                    "terminal": True,
                    "incomplete": False,
                    "updated_ms": 1,
                }, handle)
            real_replace = os.replace

            def fail_health_replace(source, destination):
                if destination.endswith("breadcrumbs.health.json"):
                    raise OSError("health sidecar publication unavailable")
                return real_replace(source, destination)

            with patch("common.breadcrumb_trace.os.replace", side_effect=fail_health_replace):
                spool = BreadcrumbDurableSpool(path, batch_size=1, flush_interval_seconds=0.01)
                deadline = time.monotonic() + 1.0
                current = spool.snapshot()
                while (
                    current.get("health_publish_ok", True)
                    or current.get("offline_retrieval") != "invalidated"
                ) and time.monotonic() < deadline:
                    time.sleep(0.005)
                    current = spool.snapshot()
                self.assertNotEqual("stale-session", current["session_id"])
                self.assertTrue(current["incomplete"])
                self.assertFalse(current["health_publish_ok"])
                self.assertGreaterEqual(current["health_publish_failures"], 1)
                spool.enqueue({"source": "controller", "message": "new-session"}, True)
                final = spool.close(timeout=2.0)

            # The failed initial and final replacements leave the old terminal
            # sidecar in place, while the fixed invalidation marker makes that
            # stale terminal projection explicitly unreadable offline.
            self.assertTrue(os.path.exists(invalid_marker_path))
            marker_stat = os.stat(invalid_marker_path, follow_symlinks=False)
            self.assertTrue(stat.S_ISREG(marker_stat.st_mode))
            self.assertEqual(0o600, stat.S_IMODE(marker_stat.st_mode))
            with open(invalid_marker_path, encoding="utf-8") as handle:
                marker = json.load(handle)
            self.assertEqual("breadcrumb_durable_health_invalidation.v1", marker["schema"])
            self.assertEqual(final["session_id"], marker["session_id"])
            self.assertEqual("INVALID", marker["state"])
            self.assertEqual("health_publish_failure", marker["reason"])
            with open(health_path, encoding="utf-8") as handle:
                stale = json.load(handle)
            self.assertEqual("stale-session", stale["session_id"])
            self.assertTrue(stale["terminal"])
            self.assertNotEqual(stale["session_id"], final["session_id"])
            self.assertTrue(final["incomplete"])
            offline_manifest_accepted = (
                stale.get("session_id") == final.get("session_id")
                and stale.get("terminal") is True
                and stale.get("incomplete") is False
                and not os.path.exists(invalid_marker_path)
            )
            self.assertFalse(offline_manifest_accepted)

            # Once a health publication succeeds, the marker is removed and
            # the newly committed sidecar carries this session identity.
            self.assertTrue(spool._BreadcrumbDurableSpool__publish_health())
            self.assertFalse(os.path.exists(invalid_marker_path))
            with open(health_path, encoding="utf-8") as handle:
                published = json.load(handle)
            self.assertEqual(final["session_id"], published["session_id"])
            self.assertTrue(published["health_publish_ok"])

        with tempfile.TemporaryDirectory() as unavailable_path:
            unavailable_health_path = os.path.join(
                unavailable_path, "breadcrumbs.health.json",
            )
            unavailable_marker_path = os.path.join(
                unavailable_path, "breadcrumbs.health.invalid",
            )
            with open(unavailable_health_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "schema": "breadcrumb_durable_health.v1",
                    "session_id": "stale-terminal-session",
                    "state": "TERMINAL",
                    "enabled": False,
                    "closed": True,
                    "terminal": True,
                    "incomplete": False,
                    "health_publish_ok": True,
                    "offline_retrieval": "valid",
                    "updated_ms": 1,
                }, handle)

            def offline_manifest_usable(expected_session):
                if os.path.exists(unavailable_marker_path):
                    return False
                try:
                    with open(unavailable_health_path, encoding="utf-8") as handle:
                        candidate = json.load(handle)
                except (OSError, ValueError, TypeError):
                    return False
                return (
                    candidate.get("schema") == "breadcrumb_durable_health.v1"
                    and candidate.get("session_id") == expected_session
                    and candidate.get("state") in {"TERMINAL", "INCOMPLETE"}
                    and candidate.get("health_publish_ok") is True
                    and candidate.get("offline_retrieval") == "valid"
                )

            real_replace = os.replace

            def fail_health_and_marker_replace(source, destination):
                if destination.endswith((
                    "breadcrumbs.health.json", "breadcrumbs.health.invalid",
                )):
                    raise OSError("health and invalidation publication unavailable")
                return real_replace(source, destination)

            with patch(
                "common.breadcrumb_trace.os.replace",
                side_effect=fail_health_and_marker_replace,
            ):
                unavailable = BreadcrumbDurableSpool(
                    unavailable_path,
                    batch_size=1,
                    flush_interval_seconds=0.01,
                )
                unavailable_health = unavailable.snapshot()
                deadline = time.monotonic() + 1.0
                while (
                    unavailable_health.get("offline_retrieval") != "unavailable"
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                    unavailable_health = unavailable.snapshot()
                with open(unavailable_health_path, encoding="utf-8") as handle:
                    stale_terminal = json.load(handle)
                self.assertEqual("stale-terminal-session", stale_terminal["session_id"])
                self.assertTrue(stale_terminal["terminal"])
                self.assertFalse(offline_manifest_usable(unavailable_health["session_id"]))
                self.assertFalse(os.path.exists(unavailable_marker_path))
                self.assertFalse(unavailable_health["offline_retrieval_invalidated"])
                self.assertEqual("unavailable", unavailable_health["offline_retrieval"])
            unavailable.close(timeout=2.0)

    def test_platform_durability_and_path_safety_are_explicit(self):
        with tempfile.TemporaryDirectory() as path:
            spool = BreadcrumbDurableSpool(path, flush_interval_seconds=10.0)
            health = spool.snapshot()
            if os.name == "nt":
                self.assertEqual("buffered_best_effort", health["durability"])
                self.assertEqual("windows_acl_unverified", health["path_safety"])
            else:
                self.assertEqual("fsync", health["durability"])
                self.assertEqual("trusted_linux_path_checks", health["path_safety"])
            spool.close(timeout=2.0)

    def test_boundary_health_and_marker_failure_is_explicitly_unavailable(self):
        if os.name == "nt":
            self.skipTest("POSIX offline session-boundary contract")

        with tempfile.TemporaryDirectory() as path:
            boundary_name = "_BreadcrumbDurableSpool__publish_session_boundary"
            with patch.object(BreadcrumbDurableSpool, boundary_name, return_value=None):
                spool = BreadcrumbDurableSpool(
                    path, batch_size=1, flush_interval_seconds=0.01,
                )
            spool.close(timeout=2.0)

            # Preserve a terminal sidecar from an older owner. The failed
            # boundary write below must not make this stale projection look
            # readable when health and invalidation persistence fail too.
            health_path = os.path.join(path, "breadcrumbs.health.json")
            stale_session = "stale-boundary-session"
            with open(health_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "schema": "breadcrumb_durable_health.v1",
                    "session_id": stale_session,
                    "state": "TERMINAL",
                    "enabled": False,
                    "closed": True,
                    "terminal": True,
                    "incomplete": False,
                    "health_publish_ok": True,
                    "offline_retrieval": "valid",
                    "updated_ms": 1,
                }, handle)

            active = os.path.join(path, "breadcrumbs.jsonl")
            raw = open(active, "w+b")
            spool._BreadcrumbDurableSpool__file_handle = raw
            spool._BreadcrumbDurableSpool__file_path = active
            spool._BreadcrumbDurableSpool__file_size = 0
            real_replace = os.replace

            def fail_health_and_marker_replace(source, destination):
                if destination.endswith((
                    "breadcrumbs.health.json", "breadcrumbs.health.invalid",
                )):
                    raise OSError("health and invalidation publication unavailable")
                return real_replace(source, destination)

            with patch(
                "common.breadcrumb_trace.os.fsync",
                side_effect=OSError("session boundary fsync unavailable"),
            ), patch(
                "common.breadcrumb_trace.os.replace",
                side_effect=fail_health_and_marker_replace,
            ):
                self.assertFalse(spool._BreadcrumbDurableSpool__publish_session_boundary())
                self.assertFalse(spool._BreadcrumbDurableSpool__publish_health())
            health = spool.snapshot()
            self.assertFalse(health["session_boundary_written"])
            self.assertEqual(1, health["session_boundary_failures"])
            self.assertFalse(health["health_publish_ok"])
            self.assertEqual("unavailable", health["offline_retrieval"])
            self.assertFalse(health["offline_retrieval_invalidated"])
            self.assertFalse(os.path.exists(os.path.join(path, "breadcrumbs.health.invalid")))
            with open(health_path, encoding="utf-8") as handle:
                stale = json.load(handle)
            self.assertEqual(stale_session, stale["session_id"])
            self.assertTrue(stale["terminal"])
            with open(active, "rb") as handle:
                self.assertEqual(b"", handle.read())


if __name__ == "__main__":
    unittest.main()
