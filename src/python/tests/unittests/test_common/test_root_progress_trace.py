import unittest
import gc
import weakref
from types import SimpleNamespace
from unittest.mock import patch

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
        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(1, health["outcome_counts"]["trace_policy_disabled"])
        self.assertEqual("trace_policy_disabled", health["latest"]["outcome"])

    def test_health_distinguishes_deduplication_and_collector_admission(self):
        trace = self._trace()
        tracer = root_progress_tracer(trace)
        details = {"representation": "root_target", "status_state": "running"}
        self.assertTrue(tracer.record("opaque-root", "status", details))
        self.assertFalse(tracer.record("opaque-root", "status", details))

        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(1, health["outcome_counts"]["tracer_deduplicated"])
        self.assertEqual(1, health["outcome_counts"]["collector_accepted"])
        self.assertEqual("root_target", health["latest_by_representation"]["root_target"]["details"]["representation"])

    def test_health_reports_when_root_event_is_evicted(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=1,
            policy={"default": "info", "rules": {"model.progress": "debug"}},
        )
        trace.record("controller", "failure", event_type="failure", category="other", level="error")
        tracer = root_progress_tracer(trace)
        self.assertFalse(tracer.record("opaque-root", "status", {"representation": "root_target"}))
        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(1, health["outcome_counts"]["collector_evicted"])

    def test_health_reports_collector_rejection_for_oversized_retention_budget(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, memory_budget_bytes=1,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        tracer = root_progress_tracer(trace)
        self.assertFalse(tracer.record("opaque-root", "status", {"representation": "root_target"}))
        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(1, health["outcome_counts"]["collector_rejected"])

    def test_emitter_success_counts_one_tracer_attempt(self):
        trace = self._trace()
        emitter = trace.create_emitter()
        tracer = root_progress_tracer(emitter)
        self.assertTrue(tracer.record("opaque-root", "status", {"representation": "root_target"}))

        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(1, health["attempt_count"])
        self.assertEqual(1, health["outcome_counts"]["emitter_enqueued"])
        self.assertEqual(1, health["outcome_counts"]["collector_accepted"])

    def test_disabled_emitter_counts_one_policy_attempt_without_queueing(self):
        trace = self._trace(enabled=False)
        emitter = trace.create_emitter()
        tracer = root_progress_tracer(emitter)
        self.assertFalse(tracer.record("opaque-root", "status", {"representation": "root_target"}))

        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(1, health["attempt_count"])
        self.assertEqual(1, health["outcome_counts"]["trace_policy_disabled"])
        self.assertEqual(0, health["outcome_counts"]["emitter_enqueued"])

    def test_emitter_dedupe_and_summary_are_retrievable(self):
        trace = self._trace()
        emitter = trace.create_emitter()
        tracer = root_progress_tracer(emitter)
        details = {"representation": "root_target", "status_state": "running"}
        self.assertTrue(tracer.record("opaque-root", "status", details))
        self.assertFalse(tracer.record("opaque-root", "status", details))
        self.assertTrue(tracer.record_summary(
            "status-list", "status_list",
            {"representation": "status_list", "sampled_count_bucket": "1-1"},
        ))

        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(2, health["attempt_count"])
        self.assertEqual(1, health["outcome_counts"]["tracer_deduplicated"])
        self.assertEqual(1, health["outcome_counts"]["summary_observed"])
        self.assertEqual("summary_observed", health["latest_summary"]["outcome"])

    def test_direct_collector_summary_does_not_count_as_attempt(self):
        trace = self._trace()
        tracer = root_progress_tracer(trace)
        self.assertTrue(tracer.record_summary(
            "status-list", "status_list",
            {"representation": "status_list", "sampled_count_bucket": "1-1"},
        ))

        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(0, health["attempt_count"])
        self.assertEqual(1, health["outcome_counts"]["summary_observed"])
        self.assertEqual("summary_observed", health["latest_summary"]["outcome"])

    def test_emitter_rejects_invalid_health_outcomes_before_mapping(self):
        trace = self._trace()
        emitter = trace.create_emitter()
        self.assertFalse(emitter.record_root_progress_health([], "status"))
        self.assertFalse(emitter.record_root_progress_health({"outcome": "bad"}, "status"))

        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(0, health["attempt_count"])
        self.assertTrue(all(value == 0 for value in health["outcome_counts"].values()))

    def test_non_root_model_progress_emitter_record_does_not_change_health(self):
        trace = self._trace()
        emitter = trace.create_emitter()
        self.assertEqual(
            "enqueued",
            emitter.record(
                "worker", "ordinary", {"representation": "root_target"},
                category="model.progress", level="debug", stage="ordinary",
            ),
        )
        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(0, health["attempt_count"])
        self.assertTrue(all(value == 0 for value in health["outcome_counts"].values()))

    def test_emitter_rejection_is_counted_only_for_marked_root_records(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=1,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        emitter = trace.create_emitter()
        self.assertEqual(
            "enqueued",
            emitter.record(
                "root_progress", "queued", {"representation": "root_target"},
                category="model.progress", level="debug", stage="root_progress_status",
                _root_progress=True,
            ),
        )
        rejected = False
        for index in range(100):
            if emitter.record(
                    "root_progress", "full", {"index": index},
                    category="model.progress", level="debug", stage="root_progress_status",
                    _root_progress=True,
            ) == "dropped":
                rejected = True
                break
        self.assertTrue(rejected)
        health = trace.snapshot()["root_progress_health"]
        self.assertGreaterEqual(health["outcome_counts"]["emitter_rejected"], 1)
        self.assertGreaterEqual(health["attempt_count"], 2)

    def test_category_clear_resets_root_progress_health_with_evidence(self):
        trace = self._trace()
        tracer = root_progress_tracer(trace)
        self.assertTrue(tracer.record("opaque-root", "status", {"representation": "root_target"}))
        self.assertGreater(trace.snapshot()["root_progress_health"]["attempt_count"], 0)

        trace.clear(category="model.progress")
        health = trace.snapshot()["root_progress_health"]
        self.assertEqual(0, health["attempt_count"])
        self.assertIsNone(health["latest"])
        self.assertIsNone(health["latest_summary"])
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
        health = trace.snapshot()["root_progress_health"]
        self.assertEqual("status_list", health["latest_by_representation"]["status_list"]["details"]["representation"])
        self.assertEqual("128+", health["latest_by_representation"]["status_list"]["details"]["sampled_count_bucket"])

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

    def test_progress_lineage_is_disabled_lazy_bounded_and_identity_free(self):
        disabled = BreadcrumbTraceCollector(
            lambda: True, max_entries=1,
            policy={"default": "off", "rules": {"model.progress": "off"}},
        )
        with patch("common.breadcrumb_trace.time.monotonic_ns", side_effect=AssertionError("disabled")):
            self.assertFalse(disabled.record_progress_lineage(
                "lftp-poll:0123456789abcdef", "status_submit", {"outcome": "private-name"},
            ))

        trace = self._trace()
        correlation = "lftp-poll:0123456789abcdef"
        for phase in (
                "status_submit", "status_start", "status_finish", "status_consume",
                "pre_active_delta", "active_delta_selector", "active_delta_builder",
                "active_delta_authorization", "active_delta_adoption", "updater_decision",
                "model_mutation",
        ):
            self.assertTrue(trace.record_progress_lineage(
                correlation, phase, {"outcome": "mutated" if phase == "model_mutation" else "ok", "model_version": 9,
                "path": "/private/path", "name": "private-name"},
            ))
        self.assertTrue(trace.record_progress_lineage_for_model_version(
            9, "scoped_stream_emit", {"scope_version": 3},
        ))
        for index in range(1, 32):
            trace.record_progress_lineage(
                "lftp-poll:{:016x}".format(index), "status_submit", {"outcome": "ok"},
            )
        snapshot = trace.snapshot()["progress_lineage"]
        self.assertEqual(32, len(snapshot["spans"]))
        retained = next(span for span in snapshot["spans"] if span["correlation"] == correlation)
        self.assertEqual(
            [
                "status_submit", "status_start", "status_finish", "status_consume", "pre_active_delta",
                "active_delta_selector", "active_delta_builder", "active_delta_authorization",
                "active_delta_adoption", "updater_decision", "model_mutation", "scoped_stream_emit",
            ],
            [step["phase"] for step in retained["steps"]],
        )
        serialized = str(snapshot)
        self.assertNotIn("private-name", serialized)
        self.assertNotIn("/private/path", serialized)

        class BrokenDetails(dict):
            def items(self):
                raise RuntimeError("diagnostic-only")

        self.assertFalse(trace.record_progress_lineage(
            "lftp-poll:fedcba9876543210", "status_submit", BrokenDetails(),
        ))

    def test_active_delta_lineage_phase_order_and_unreached_tails_are_bounded(self):
        trace = self._trace()
        correlation = "lftp-poll:0123456789abcdef"
        reached_failure = (
            "status_consume", "pre_active_delta", "active_delta_selector",
            "updater_decision",
        )
        for phase in reached_failure:
            self.assertTrue(trace.record_progress_lineage(
                correlation, phase,
                {"outcome": "ok", "decision": "full_build"} if phase == "updater_decision"
                else {"outcome": "ok"},
            ))
        span = trace.snapshot()["progress_lineage"]["spans"][0]
        self.assertEqual(list(reached_failure), [step["phase"] for step in span["steps"]])
        self.assertNotIn("active_delta_builder", [step["phase"] for step in span["steps"]])
        self.assertNotIn("active_delta_authorization", [step["phase"] for step in span["steps"]])
        self.assertNotIn("active_delta_adoption", [step["phase"] for step in span["steps"]])

        noop_correlation = "lftp-poll:fedcba9876543210"
        trace.record_progress_lineage(
            noop_correlation, "status_consume", {"outcome": "ok"},
        )
        trace.record_progress_lineage(
            noop_correlation, "pre_active_delta", {"outcome": "ok"},
        )
        trace.record_progress_lineage(
            noop_correlation, "updater_decision", {"outcome": "ok", "decision": "cached"},
        )
        span = next(
            span for span in trace.snapshot()["progress_lineage"]["spans"]
            if span["correlation"] == noop_correlation
        )
        self.assertEqual(
            ["status_consume", "pre_active_delta", "updater_decision"],
            [step["phase"] for step in span["steps"]],
        )
        self.assertEqual(3, len(span["steps"]))

    def test_progress_lineage_uses_exact_mutation_mapping_and_clear_is_safe(self):
        trace = self._trace()
        first = "lftp-poll:0123456789abcdef"
        second = "lftp-poll:fedcba9876543210"
        self.assertTrue(trace.record_progress_lineage(
            first, "model_mutation", {"outcome": "mutated", "model_version": 10},
        ))
        # A no-op/intervening poll can mention a version but must not replace
        # the published mutation's stream lineage.
        self.assertTrue(trace.record_progress_lineage(
            second, "updater_decision", {"decision": "cached", "model_version": 10},
        ))
        self.assertTrue(trace.record_progress_lineage_for_model_version(
            10, "scoped_stream_emit", {"scope_version": 4},
        ))
        spans = trace.snapshot()["progress_lineage"]["spans"]
        first_span = next(span for span in spans if span["correlation"] == first)
        second_span = next(span for span in spans if span["correlation"] == second)
        self.assertEqual("scoped_stream_emit", first_span["steps"][-1]["phase"])
        self.assertEqual("updater_decision", second_span["steps"][-1]["phase"])

        trace.clear(category="model.progress")
        self.assertFalse(trace.record_progress_lineage_for_model_version(
            10, "scoped_stream_emit", {"scope_version": 4},
        ))

    def test_progress_lineage_rejects_hostile_enabled_strings(self):
        trace = self._trace()
        self.assertTrue(trace.record_progress_lineage(
            "lftp-poll:0123456789abcdef", "status_consume",
            {"source": "/private/path", "outcome": "private-name", "fresh": True},
        ))
        details = trace.snapshot()["progress_lineage"]["spans"][0]["steps"][0]["details"]
        self.assertEqual({"fresh": True}, details)

    def test_large_mutation_update_keeps_lifecycle_and_early_late_version_links(self):
        trace = self._trace()
        correlation = "lftp-poll:0123456789abcdef"
        for phase in ("status_submit", "status_start", "status_finish", "status_consume", "updater_decision"):
            self.assertTrue(trace.record_progress_lineage(
                correlation, phase, {"outcome": "ok", "source": "fresh_healthy"},
            ))
        for version in range(1, 41):
            self.assertTrue(trace.record_progress_lineage(
                correlation, "model_mutation",
                {"outcome": "mutated", "model_version": version, "scope_version": version},
            ))
        self.assertTrue(trace.record_progress_lineage_for_model_version(
            1, "scoped_stream_emit", {"scope_version": 1},
        ))
        self.assertTrue(trace.record_progress_lineage_for_model_version(
            40, "scoped_stream_emit", {"scope_version": 40},
        ))
        span = trace.snapshot()["progress_lineage"]["spans"][0]
        self.assertEqual(
            ["status_submit", "status_start", "status_finish", "status_consume", "updater_decision", "model_mutation", "scoped_stream_emit"],
            [step["phase"] for step in span["steps"]],
        )
        mutation = next(step for step in span["steps"] if step["phase"] == "model_mutation")
        self.assertEqual(1, mutation["details"]["model_version_first"])
        self.assertEqual(40, mutation["details"]["model_version_last"])
        self.assertEqual("33+", mutation["details"]["mutation_count_bucket"])
        self.assertEqual("2-4", span["steps"][-1]["details"]["scoped_stream_count_bucket"])

    def test_mutation_range_overflow_is_explicit_and_never_links_omitted_version(self):
        trace = self._trace()
        correlation = "lftp-poll:0123456789abcdef"
        for version in range(1, 18, 2):
            trace.record_progress_lineage(
                correlation, "model_mutation",
                {"outcome": "mutated", "model_version": version, "scope_version": version},
            )
        span = trace.snapshot()["progress_lineage"]["spans"][0]
        self.assertTrue(span["mutation_ranges_truncated"])
        self.assertEqual(1, span["mutation_ranges_omitted_count"])
        self.assertTrue(trace.record_progress_lineage_for_model_version(
            15, "scoped_stream_emit", {"scope_version": 15},
        ))
        self.assertFalse(trace.record_progress_lineage_for_model_version(
            17, "scoped_stream_emit", {"scope_version": 17},
        ))

    def test_many_scoped_emissions_coalesce_without_evicting_lifecycle(self):
        trace = self._trace()
        correlation = "lftp-poll:0123456789abcdef"
        for phase in ("status_submit", "status_start", "status_finish", "status_consume", "updater_decision"):
            trace.record_progress_lineage(correlation, phase, {"outcome": "ok", "source": "fresh_healthy"})
        trace.record_progress_lineage(
            correlation, "model_mutation", {"outcome": "mutated", "model_version": 1, "scope_version": 1},
        )
        for scope_version in range(1, 41):
            self.assertTrue(trace.record_progress_lineage_for_model_version(
                1, "scoped_stream_emit", {"scope_version": scope_version},
            ))
        span = trace.snapshot()["progress_lineage"]["spans"][0]
        self.assertEqual(
            ["status_submit", "status_start", "status_finish", "status_consume", "updater_decision", "model_mutation", "scoped_stream_emit"],
            [step["phase"] for step in span["steps"]],
        )
        emission = span["steps"][-1]
        self.assertEqual("33+", emission["details"]["scoped_stream_count_bucket"])
