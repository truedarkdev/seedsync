import unittest
import gc
import weakref
from types import SimpleNamespace

from common.breadcrumb_trace import BreadcrumbTraceCollector
from common.root_progress_trace import (
    duration_bucket,
    eta_bucket,
    percent_bucket,
    root_progress_tracer,
    scalar_percent,
    speed_bucket,
)


class TestRootProgressTrace(unittest.TestCase):
    def _trace(self, enabled=True):
        return BreadcrumbTraceCollector(
            lambda: enabled,
            max_entries=32,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )

    def test_enabled_sequence_is_retrievable_and_deduplicated(self):
        trace = self._trace()
        tracer = root_progress_tracer(trace)

        self.assertTrue(tracer.record(
            "private-root-id",
            "status",
            {
                "status_state": "running",
                "progress_percent": 25,
                "percent_bucket": percent_bucket(25),
                "speed_bucket": speed_bucket(1024),
                "eta_bucket": eta_bucket(60),
                "scope_digest": "scope-digest",
                "filename": "private-name.mkv",
                "path": "/private/path",
                "raw_size": 987654,
            },
        ))
        self.assertFalse(tracer.record(
            "private-root-id", "status",
            {
                "status_state": "running",
                "progress_percent": 25,
                "percent_bucket": percent_bucket(25),
                "speed_bucket": speed_bucket(1024),
                "eta_bucket": eta_bucket(60),
                "scope_digest": "scope-digest",
            },
        ))
        self.assertTrue(tracer.record(
            "private-root-id", "decision",
            {"decision": "active_delta_or_full_build", "outcome": "active_delta", "reason": "eligible"},
        ))

        entries = trace.snapshot(category="model.progress")["entries"]
        self.assertEqual([1, 2], [entry["details"]["sequence"] for entry in entries])
        self.assertEqual(["status", "decision"], [entry["stage"].replace("root_progress_", "") for entry in entries])
        self.assertNotIn("private-root-id", str(entries))
        self.assertNotIn("private-name.mkv", str(entries))
        self.assertNotIn("/private/path", str(entries))
        self.assertNotIn("987654", str(entries))

    def test_disabled_gate_skips_details_traversal_and_emission(self):
        trace = self._trace(enabled=False)
        tracer = root_progress_tracer(trace)

        class ExplodingDetails:
            def items(self):
                raise AssertionError("disabled trace must not traverse details")

        self.assertFalse(tracer.record("private-root-id", "status", ExplodingDetails()))
        self.assertEqual([], trace.snapshot(category="model.progress")["entries"])

    def test_scalar_buckets_are_bounded(self):
        self.assertEqual(0, scalar_percent(-10))
        self.assertEqual(100, scalar_percent(101))
        self.assertEqual("0-9", percent_bucket(9))
        self.assertEqual("100-100", percent_bucket(100))
        self.assertEqual("1024-1048576", speed_bucket(1024 * 1024))
        self.assertEqual("60-299", eta_bucket(60))
        self.assertEqual("500-1999", duration_bucket(500))

    def test_dropped_record_is_retryable_and_reset_invalidates_dedupe(self):
        class DropOnceTrace:
            def __init__(self):
                self.outcomes = ["dropped", "retained", "retained"]
                self.generation = 0
                self.records = []

            def is_effectively_enabled(self, category, level):
                return True

            def trace_generation(self):
                return self.generation

            def record(self, source, message, details, **metadata):
                self.records.append(details)
                return self.outcomes.pop(0)

        trace = DropOnceTrace()
        tracer = root_progress_tracer(trace)
        details = {"status_state": "running"}
        self.assertFalse(tracer.record("opaque-job", "status", details))
        self.assertTrue(tracer.record("opaque-job", "status", details))
        trace.generation += 1
        self.assertTrue(tracer.record("opaque-job", "status", details))
        self.assertEqual([1, 2], [record["sequence"] for record in trace.records[1:]])

        collector = self._trace()
        collector_tracer = root_progress_tracer(collector)
        self.assertTrue(collector_tracer.record("opaque-job", "status", details))
        self.assertFalse(collector_tracer.record("opaque-job", "status", details))
        collector.reset()
        self.assertTrue(collector_tracer.record("opaque-job", "status", details))

    def test_weak_registry_does_not_extend_collector_lifetime(self):
        trace = self._trace()
        root_progress_tracer(trace)
        trace_ref = weakref.ref(trace)
        del trace
        gc.collect()
        self.assertIsNone(trace_ref())

    def test_large_status_iterable_is_bounded_before_field_traversal(self):
        from controller.model_updater import _record_root_progress_status

        trace = self._trace()
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace),
        )

        class Status:
            file_id = "opaque-file"
            path_pair_id = "opaque-pair"
            state = SimpleNamespace(name="RUNNING")
            type = SimpleNamespace(name="GET")
            total_transfer_state = SimpleNamespace(percent_local=25, speed=1024, eta=60)

        class CountingStatuses:
            def __init__(self):
                self.visited = 0

            def __iter__(self):
                for _ in range(1000):
                    self.visited += 1
                    yield Status()

        statuses = CountingStatuses()
        _record_root_progress_status(
            controller, statuses, source="lftp", model_version=1,
        )
        self.assertEqual(128, statuses.visited)
        self.assertEqual(1, len(trace.snapshot(category="model.progress")["entries"]))

    def test_decision_matrix_uses_only_settled_bounded_outcomes(self):
        from controller.model_updater import ModelUpdater

        trace = self._trace()
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace),
            _Controller__model=SimpleNamespace(version=7),
        )
        updater = ModelUpdater(controller)
        matrix = (
            ("active_delta", "adopted"),
            ("authoritative_pair_candidate", "candidate_authorized"),
            ("full_build", "pending_changes"),
            ("cached", "no_pending_changes"),
            ("rejected", "active_delta_selector_rejected"),
        )
        for outcome, reason in matrix:
            updater._record_root_progress_decision(
                "opaque-job", outcome=outcome, reason=reason,
                decision="active_delta_or_full_build",
            )
        entries = trace.snapshot(category="model.progress")["entries"]
        self.assertEqual([item[0] for item in matrix], [entry["details"]["outcome"] for entry in entries])
        self.assertEqual([item[1] for item in matrix], [entry["details"]["reason"] for entry in entries])

    def test_publication_version_change_is_not_coalesced(self):
        trace = self._trace()
        tracer = root_progress_tracer(trace)
        self.assertTrue(tracer.record(
            "opaque-scope", "sse",
            {
                "publication": "scoped", "event": "model-page",
                "model_version": 10, "scope_version": 3,
                "outcome": "emitted", "reason": "sse_write",
            },
        ))
        self.assertTrue(tracer.record(
            "opaque-scope", "sse",
            {
                "publication": "scoped", "event": "model-page",
                "model_version": 11, "scope_version": 4,
                "outcome": "emitted", "reason": "sse_write",
            },
        ))
        entries = trace.snapshot(category="model.progress")["entries"]
        self.assertEqual([10, 11], [entry["details"]["model_version"] for entry in entries])
