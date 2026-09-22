# Copyright 2026, SeedSync Contributors, All rights reserved.

from collections import deque
import queue
import time
import unittest
from unittest.mock import patch

from common.breadcrumb_trace import (
    DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES,
    BreadcrumbTraceCollector,
)


class TestBreadcrumbTraceCollector(unittest.TestCase):
    def test_record_is_bounded_and_returns_copies(self):
        enabled = {"value": True}
        collector = BreadcrumbTraceCollector(lambda: enabled["value"], max_entries=2)

        with patch("common.breadcrumb_trace.time.time_ns", side_effect=[1_000_000_000, 2_000_000_000, 3_000_000_000]):
            collector.record("controller", "start", {"phase": "init"})
            collector.record("controller", "refresh", {"phase": "retry"})
            collector.record("controller", "exit", {"phase": "done"})

        snapshot = collector.snapshot()
        self.assertEqual(True, snapshot["enabled"])
        self.assertEqual(2, snapshot["entry_count"])
        self.assertEqual(2, len(snapshot["entries"]))
        self.assertEqual("controller", snapshot["entries"][0]["source"])
        self.assertEqual("refresh", snapshot["entries"][0]["message"])
        self.assertEqual(1, snapshot["entries"][0]["repeat_count"])
        self.assertEqual(2_000, snapshot["entries"][0]["created_ms"])
        self.assertEqual("exit", snapshot["entries"][1]["message"])
        self.assertEqual(3_000, snapshot["entries"][1]["created_ms"])
        self.assertEqual(3, snapshot["version"])
        self.assertTrue(snapshot["window_truncated"])

        snapshot["entries"][0]["details"]["phase"] = "mutated"
        self.assertEqual("retry", collector.snapshot()["entries"][0]["details"]["phase"])

    def test_record_noops_when_disabled(self):
        collector = BreadcrumbTraceCollector(lambda: False, max_entries=2)
        emitter = collector.create_emitter()

        for _ in range(25):
            emitter.record("scanner_process", "scan_started", {"phase": "init"}, stage="scan")

        snapshot = collector.snapshot()
        self.assertEqual(False, snapshot["enabled"])
        self.assertEqual(0, snapshot["entry_count"])
        self.assertEqual([], snapshot["entries"])
        self.assertFalse(snapshot["window_reset"])
        self.assertEqual(0, snapshot["version"])

    def test_progress_lineage_summary_projects_span_and_preserves_ranges(self):
        collector = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        correlation = "lftp-poll:0123456789abcdef"
        self.assertTrue(collector.record_progress_lineage(
            correlation, "status_consume",
            {"source": "fresh_healthy", "fresh": True, "healthy": True},
        ))
        self.assertTrue(collector.record_progress_lineage(
            correlation, "model_mutation",
            {"outcome": "mutated", "model_version": 4, "scope_version": 2},
        ))
        self.assertTrue(collector.record_progress_lineage(
            correlation, "model_mutation",
            {"outcome": "mutated", "model_version": 7, "scope_version": 3},
        ))
        self.assertTrue(collector.record_progress_lineage(
            correlation, "updater_decision",
            {"decision": "active_delta", "build_kind": "candidate", "status_count_bucket": "2-4",
             "updater_cycle_duration_bucket": "20-99"},
        ))

        self.assertTrue(collector.record_progress_lineage_summary(correlation))
        entries = [
            entry for entry in collector.snapshot()["entries"]
            if entry["message"] == "progress_lineage_summary"
        ]
        self.assertEqual(1, len(entries))
        self.assertEqual(1, entries[0]["repeat_count"])
        self.assertEqual("lftp-poll:0123456789abcdef", entries[0]["corr_id"])
        summary = entries[0]["details"]
        self.assertEqual("model_progress_lineage_summary.v1", summary["schema"])
        self.assertEqual("none", summary["missing_phase"])
        self.assertEqual(
            {"source": "fresh_healthy", "fresh": True, "healthy": True,
             "monotonic_ms": summary["status_consume"]["monotonic_ms"]},
            summary["status_consume"],
        )
        self.assertEqual(4, summary["model_mutation"]["model_version_first"])
        self.assertEqual(7, summary["model_mutation"]["model_version_last"])
        self.assertEqual(3, summary["model_mutation"]["scope_version"])
        self.assertEqual([[4, 4], [7, 7]], summary["model_mutation"]["mutation_version_ranges"])
        covered_versions = [
            version
            for start, end in summary["model_mutation"]["mutation_version_ranges"]
            for version in range(start, end + 1)
        ]
        self.assertNotIn(6, covered_versions)
        self.assertEqual("active_delta", summary["updater_decision"]["decision"])
        self.assertEqual("2-4", summary["updater_decision"]["status_count_bucket"])
        self.assertEqual("20-99", summary["updater_decision"]["updater_cycle_duration_bucket"])

    def test_progress_lineage_summary_reports_bounded_truncated_ranges(self):
        collector = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        correlation = "lftp-poll:0123456789abcdef"
        self.assertTrue(collector.record_progress_lineage(
            correlation, "status_consume",
            {"source": "fresh_healthy", "fresh": True, "healthy": True},
        ))
        for version in range(0, 20, 2):
            accepted = collector.record_progress_lineage(
                correlation, "model_mutation",
                {"outcome": "mutated", "model_version": version, "scope_version": version},
            )
            self.assertEqual(version < 16, accepted)
        self.assertTrue(collector.record_progress_lineage_summary(correlation))
        summary = next(
            item["details"] for item in collector.snapshot()["entries"]
            if item["message"] == "progress_lineage_summary"
        )
        mutation = summary["model_mutation"]
        self.assertEqual([[version, version] for version in range(0, 16, 2)], mutation["mutation_version_ranges"])
        self.assertTrue(mutation["mutation_ranges_truncated"])
        self.assertEqual(2, mutation["mutation_ranges_omitted_count"])

    def test_progress_lineage_summary_reports_typed_missing_phase_and_reset_is_stale_safe(self):
        collector = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        correlation = "lftp-poll:0123456789abcdef"
        collector.record_progress_lineage(
            correlation, "status_consume",
            {"source": "cached_retry", "fresh": False, "healthy": False},
        )
        collector.record_progress_lineage(
            correlation, "updater_decision", {"decision": "cached", "build_kind": "none"},
        )
        self.assertTrue(collector.record_progress_lineage_summary(correlation))
        entry = next(
            item for item in collector.snapshot()["entries"]
            if item["message"] == "progress_lineage_summary"
        )
        self.assertEqual("model_mutation", entry["details"]["missing_phase"])
        collector.clear({"category": "model.progress"})
        self.assertFalse(collector.record_progress_lineage_summary(correlation))
        self.assertFalse(any(
            item["message"] == "progress_lineage_summary"
            for item in collector.snapshot()["entries"]
        ))

    def test_progress_lineage_summary_is_inert_when_debug_policy_is_disabled(self):
        collector = BreadcrumbTraceCollector(lambda: True)
        correlation = "lftp-poll:0123456789abcdef"
        self.assertFalse(collector.record_progress_lineage(
            correlation, "status_consume",
            {"source": "fresh_healthy", "fresh": True, "healthy": True},
        ))
        self.assertFalse(collector.record_progress_lineage_summary(correlation))
        self.assertEqual([], collector.snapshot()["entries"])

    def test_record_reads_enabled_gate_once(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=2)

        with patch.object(collector, "is_enabled", wraps=collector.is_enabled) as enabled_gate:
            collector.record("controller", "start")

        self.assertEqual(1, enabled_gate.call_count)

    def test_record_bounds_external_queue_drain_cadence(self):
        class FakeRecordQueue:
            def __init__(self):
                self.records = [
                    {"source": "worker", "message": "queued-{}".format(index), "details": {}, "metadata": {}}
                    for index in range(5)
                ]

            def get_nowait(self):
                if not self.records:
                    raise queue.Empty()
                return self.records.pop(0)

            def empty(self):
                return not self.records

        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)
        collector._BreadcrumbTraceCollector__external_records = FakeRecordQueue()

        with patch("common.breadcrumb_trace.time.monotonic", side_effect=(0.0, 0.05, 0.11)):
            collector.record("controller", "local-1")
            self.assertEqual(4, collector._BreadcrumbTraceCollector__external_queue_last_drain_count)
            collector.record("controller", "local-2")
            self.assertEqual(4, collector._BreadcrumbTraceCollector__external_queue_last_drain_count)
            collector.record("controller", "local-3")
            self.assertEqual(1, collector._BreadcrumbTraceCollector__external_queue_last_drain_count)

        self.assertEqual(4, collector.snapshot()["entry_count"])

    def test_disabled_emitter_traffic_stays_bounded_without_retaining_raw_backlog(self):
        enabled = {"value": False}
        collector = BreadcrumbTraceCollector(lambda: enabled["value"], max_entries=2)
        emitter = collector.create_emitter()

        for index in range(200):
            emitter.record(
                "scanner_process",
                "scan_started",
                {"phase": "init", "iteration": index, "token": "secret-token-{}".format(index)},
                stage="scan",
                corr_id="flow-1",
            )

        snapshot = collector.snapshot()
        self.assertEqual(False, snapshot["enabled"])
        self.assertEqual(0, snapshot["entry_count"])
        self.assertEqual([], snapshot["entries"])
        self.assertEqual(0, snapshot["external_queue_drain_count"])
        self.assertFalse(snapshot["external_queue_drain_limited"])

    def test_emitter_created_while_disabled_can_record_after_enable(self):
        enabled = {"value": False}
        collector = BreadcrumbTraceCollector(lambda: enabled["value"], max_entries=2)
        emitter = collector.create_emitter()

        emitter.record("scanner_process", "scan_started", {"phase": "init"}, stage="scan")
        self.assertEqual(0, collector.snapshot()["entry_count"])

        enabled["value"] = True
        collector.sync_enabled_state()
        emitter.record("scanner_process", "scan_started", {"phase": "resume"}, stage="scan")
        snapshot = collector.snapshot()
        self.assertEqual(True, snapshot["enabled"])
        self.assertEqual(1, snapshot["entry_count"])
        self.assertEqual("scan_started", snapshot["entries"][0]["message"])
        self.assertEqual("scan", snapshot["entries"][0]["stage"])
        self.assertEqual("resume", snapshot["entries"][0]["details"]["phase"])

    def test_emitter_created_while_enabled_stops_after_disable(self):
        enabled = {"value": True}
        collector = BreadcrumbTraceCollector(lambda: enabled["value"], max_entries=2)
        emitter = collector.create_emitter()

        emitter.record("scanner_process", "scan_started", {"phase": "initial"}, stage="scan")
        self.assertEqual(1, collector.snapshot()["entry_count"])
        enabled["value"] = False
        collector.sync_enabled_state()
        emitter.record("scanner_process", "scan_started", {"phase": "stopped"}, stage="scan")

        snapshot = collector.snapshot()
        self.assertEqual(False, snapshot["enabled"])
        self.assertEqual(1, snapshot["entry_count"])
        self.assertEqual("initial", snapshot["entries"][0]["details"]["phase"])

    def test_snapshot_waits_for_delayed_first_external_record(self):
        class DelayedRecordQueue:
            def __init__(self, record):
                self.__record = record

            def get(self, timeout=None):
                if timeout is None:
                    raise queue.Empty()
                if self.__record is None:
                    raise queue.Empty()
                record = self.__record
                self.__record = None
                return record

            def get_nowait(self):
                raise queue.Empty()

            def empty(self):
                return self.__record is None

        collector = BreadcrumbTraceCollector(lambda: True, max_entries=2)
        collector._BreadcrumbTraceCollector__external_records = DelayedRecordQueue({
            "source": "controller",
            "message": "start",
            "details": {"phase": "delayed"},
            "metadata": {"stage": "controller", "corr_id": "flow-1"},
            "created_ns": 10,
            "created_ms": 0,
        })

        snapshot = collector.snapshot()
        self.assertEqual(1, snapshot["external_queue_drain_count"])
        self.assertEqual(1, snapshot["entry_count"])
        self.assertEqual("delayed", snapshot["entries"][0]["details"]["phase"])

    def test_snapshot_skips_malformed_timed_record_and_non_mapping_metadata(self):
        class MixedRecordQueue:
            def __init__(self):
                self.records = [
                    "malformed",
                    {"source": "controller", "message": "valid", "details": {}, "metadata": "bad"},
                ]

            def get(self, timeout=None):
                return self.records.pop(0)

            def get_nowait(self):
                if not self.records:
                    raise queue.Empty()
                return self.records.pop(0)

            def empty(self):
                return not self.records

        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)
        collector._BreadcrumbTraceCollector__external_records = MixedRecordQueue()

        snapshot = collector.snapshot()

        self.assertEqual(1, snapshot["entry_count"])
        self.assertEqual("valid", snapshot["entries"][0]["message"])
        self.assertEqual("valid", snapshot["entries"][0]["stage"])

    def test_snapshot_skips_malformed_nonblocking_record(self):
        class NonblockingRecordQueue:
            def __init__(self):
                self.records = [42]

            def get_nowait(self):
                if not self.records:
                    raise queue.Empty()
                return self.records.pop(0)

            def empty(self):
                return not self.records

        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)
        collector._BreadcrumbTraceCollector__external_records = NonblockingRecordQueue()

        snapshot = collector.snapshot()

        self.assertEqual(0, snapshot["entry_count"])

    def test_record_wraps_scalar_details_when_metadata_is_present(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        collector.record("controller", "scalar", "detail-value", stage="scan", attempt=2)

        entry = collector.snapshot()["entries"][0]
        self.assertEqual("detail-value", entry["details"]["value"])
        self.assertEqual(2, entry["details"]["attempt"])

    def test_record_redacts_risky_strings_inside_generic_detail_values(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        collector.record(
            "controller",
            "command_failed",
            {
                "error_message": "Lftp error: password=hunter2 user myuser@seedbox.example.com:~>",
                "reason": "ssh command failed: token=secret-token",
            },
            stage="command",
            event_type="failure",
            corr_id="flow-1"
        )

        snapshot = collector.snapshot()
        details = snapshot["entries"][0]["details"]
        self.assertNotIn("hunter2", details["error_message"])
        self.assertNotIn("myuser@seedbox.example.com", details["error_message"])
        self.assertNotIn("secret-token", details["reason"])
        self.assertIn("**REDACTED**", details["error_message"])
        self.assertIn("**REDACTED**", details["reason"])

    def test_record_allowlists_scan_authority_keys_without_widening_auth_redaction(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        collector.record(
            "controller",
            "scan_authority",
            {
                "joint_authoritative_before": False,
                "joint_authoritative_after": True,
                "joint_authoritative": True,
                "authoritative_authentication": "private-authentication",
                "authoritative_auth": "private-auth",
            },
            category="scan.authority",
            level="info",
            stage="scan_authority",
            event_type="diagnostic",
        )

        details = collector.snapshot()["entries"][0]["details"]
        self.assertFalse(details["joint_authoritative_before"])
        self.assertTrue(details["joint_authoritative_after"])
        self.assertTrue(details["joint_authoritative"])
        self.assertEqual("<redacted>", details["authoritative_authentication"])
        self.assertEqual("<redacted>", details["authoritative_auth"])

    def test_record_preserves_allowlisted_queue_command_outcome_only(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        collector.record(
            "controller",
            "queue_lifecycle_operation_retired",
            {
                "command_kind": "queue",
                "command_outcome": "prompt_timeout",
                "command_prompt_timed_out": True,
                "command_outcome_untrusted": "private-command-output",
                "command": "private-command-output",
            },
            category="queue.lifecycle",
            level="info",
        )
        collector.record(
            "controller",
            "queue_lifecycle_operation_retired",
            {"command_outcome": "private-command-output"},
            category="queue.lifecycle",
            level="info",
        )

        entries = collector.snapshot()["entries"]
        details = entries[0]["details"]
        self.assertEqual("queue", details["command_kind"])
        self.assertEqual("prompt_timeout", details["command_outcome"])
        self.assertTrue(details["command_prompt_timed_out"])
        self.assertEqual("<redacted>", details["command_outcome_untrusted"])
        self.assertEqual("<redacted>", details["command"])
        self.assertEqual("<redacted>", entries[1]["details"]["command_outcome"])

        def sanitize_command_field(field, value):
            candidate = BreadcrumbTraceCollector(lambda: True, max_entries=2)
            candidate.record(
                "controller",
                "queue_lifecycle_operation_retired",
                {field: value},
                category="queue.lifecycle",
                level="info",
            )
            return candidate.snapshot()["entries"][0]["details"][field]

        self.assertEqual("<redacted>", sanitize_command_field("command_outcome", 7))
        self.assertEqual("<redacted>", sanitize_command_field("command_outcome", True))
        self.assertEqual("<redacted>", sanitize_command_field("command_outcome", 1.5))
        self.assertEqual(
            "<redacted>",
            sanitize_command_field("command_outcome", {"nested": "private-command-output"}),
        )
        self.assertEqual("<redacted>", sanitize_command_field("command_outcome", object()))
        self.assertEqual("<redacted>", sanitize_command_field("command_kind", 7))
        self.assertEqual(
            "<redacted>",
            sanitize_command_field("command_kind", {"nested": "private-command-output"}),
        )

    def test_record_redacts_ftp_and_ftps_urls_with_reserved_characters(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        collector.record(
            "controller",
            "command_failed",
            {
                "error_message": "ftp://alice:pa:ss@seedbox.example.com/downloads",
                "reason": "ftps://bob:pa/ss@mirror.example.net:21/files",
            },
            stage="command",
            event_type="failure",
            corr_id="flow-1"
        )

        snapshot = collector.snapshot()
        details = snapshot["entries"][0]["details"]
        self.assertNotIn("pa:ss", details["error_message"])
        self.assertNotIn("pa/ss", details["reason"])
        self.assertNotIn("seedbox.example.com", details["error_message"])
        self.assertNotIn("mirror.example.net", details["reason"])
        self.assertIn("ftp://**REDACTED**@**REDACTED**/downloads", details["error_message"])
        self.assertIn("ftps://**REDACTED**@**REDACTED**:21/files", details["reason"])

    def test_record_coalesces_identical_entries_without_window_growth(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        with patch("common.breadcrumb_trace.time.time_ns", side_effect=[100, 200, 300, 400, 500]):
            for _ in range(5):
                collector.record(
                    "controller",
                    "command_received",
                    {
                        "command": "lftp -e 'open secret.example; get file'",
                        "api_token": "super-secret-token",
                    },
                )

        snapshot = collector.snapshot()
        self.assertEqual(1, len(snapshot["entries"]))
        entry = snapshot["entries"][0]
        self.assertEqual(5, entry["repeat_count"])
        self.assertEqual(5, snapshot["version"])
        self.assertEqual("<redacted>", entry["details"]["command"])
        self.assertEqual("<redacted>", entry["details"]["api_token"])

    def test_admission_skips_retention_scans_without_eviction(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        range_check = "_BreadcrumbTraceCollector__entry_range_is_retained"
        refresh_failure = "_BreadcrumbTraceCollector__refresh_failure_locked"
        with patch.object(collector, range_check, wraps=getattr(collector, range_check)) as range_method:
            with patch.object(
                    collector, refresh_failure, wraps=getattr(collector, refresh_failure),
            ) as refresh_method:
                for index in range(8):
                    self.assertEqual("retained", collector.record("worker", "event-{}".format(index)))
        self.assertEqual(0, range_method.call_count)
        self.assertEqual(0, refresh_method.call_count)
        self.assertEqual(8, collector.snapshot()["retained_count"])

    def test_ordinary_admission_does_not_iterate_retained_entries(self):
        class CountingDeque(deque):
            yielded = 0

            def __iter__(self):
                for entry in super().__iter__():
                    type(self).yielded += 1
                    yield entry

        collector = BreadcrumbTraceCollector(lambda: True, max_entries=10000)
        collector._BreadcrumbTraceCollector__entries = CountingDeque()

        for index in range(10000):
            self.assertEqual("retained", collector.record("worker", "event-{}".format(index)))

        self.assertEqual(0, CountingDeque.yielded)
        self.assertEqual(10000, len(collector._BreadcrumbTraceCollector__entries))
        self.assertEqual(10000, collector._BreadcrumbTraceCollector__version)
        self.assertEqual(0, collector._BreadcrumbTraceCollector__evicted_count)

    def test_eviction_candidate_selection_uses_entry_snapshot(self):
        class IndexedReadCountingDeque(deque):
            indexed_reads = 0

            def __getitem__(self, index):
                if isinstance(index, int):
                    type(self).indexed_reads += 1
                return super().__getitem__(index)

        entry_count = 128
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=None)
        for index in range(entry_count):
            collector.record("worker", "event-{}".format(index), category="noise")

        collector._BreadcrumbTraceCollector__entries = IndexedReadCountingDeque(
            collector._BreadcrumbTraceCollector__entries,
        )
        collector._BreadcrumbTraceCollector__max_entries = entry_count // 2
        IndexedReadCountingDeque.indexed_reads = 0

        evicted = collector._BreadcrumbTraceCollector__evict_to_budget()

        self.assertTrue(evicted)
        self.assertEqual(entry_count // 2, collector.snapshot()["entry_count"])
        self.assertEqual(entry_count // 2, collector.snapshot()["accounting"]["evicted_count"])
        # Candidate selection reads entries from one immutable tuple per
        # eviction pass; the single remaining indexed read is the final
        # signature lookup after the pass completes.
        self.assertEqual(1, IndexedReadCountingDeque.indexed_reads)

    def test_eviction_matches_frozen_baseline_oracle_for_mixed_and_fallback_streams(self):
        policy = {"retention": {"protected_categories": ["protected"]}}
        cases = (
            (
                [
                    {"message": "noise-a", "category": "noise"},
                    {"message": "noise-b", "category": "noise"},
                    {"message": "quiet", "category": "quiet"},
                    {"message": "tie-a", "category": "tie"},
                    {"message": "tie-b", "category": "tie"},
                    {"message": "decision-old", "category": "decision", "event_type": "decision"},
                    {"message": "decision-new", "category": "decision", "event_type": "decision"},
                    {"message": "protected-level", "category": "level-protected", "level": "warning"},
                    {"message": "protected-category", "category": "protected.audit"},
                    {
                        "message": "failure", "category": "failure", "event_type": "failure",
                        "level": "error", "_coalesce_key": "failure",
                    },
                    {
                        "message": "failure", "category": "failure", "event_type": "failure",
                        "level": "error", "_coalesce_key": "failure",
                    },
                    {"message": "protected-tail", "category": "tail-protected", "level": "warning"},
                ],
                3,
                [
                    ("noise-a", 1, 1), ("tie-a", 4, 4), ("noise-b", 2, 2),
                    ("quiet", 3, 3), ("tie-b", 5, 5), ("decision-old", 6, 6),
                    ("decision-new", 7, 7), ("protected-level", 8, 8),
                ],
                ["protected-category", "failure", "protected-tail"],
            ),
            (
                [
                    {"message": "protected-level-a", "category": "level-a", "level": "warning"},
                    {"message": "protected-level-b", "category": "level-b", "level": "error"},
                    {"message": "protected-category", "category": "protected.audit"},
                    {"message": "protected-decision", "category": "decision", "event_type": "decision"},
                ],
                1,
                [
                    ("protected-level-a", 1, 1),
                    ("protected-level-b", 2, 2),
                    ("protected-category", 3, 3),
                ],
                ["protected-decision"],
            ),
        )

        def populate(collector, stream):
            with patch("common.breadcrumb_trace.time.time_ns", return_value=1_000_000_000):
                for event in stream:
                    metadata = dict(event)
                    message = metadata.pop("message")
                    collector.record("worker", message, **metadata)

        # Immutable outputs captured from the current pre-change retention
        # behavior.  This oracle deliberately does not reproduce candidate
        # selection logic, so a refactor cannot make the test agree with itself.
        for stream, max_entries, expected_evictions, expected_remaining in cases:
            collector = BreadcrumbTraceCollector(lambda: True, max_entries=None, policy=policy)
            populate(collector, stream)
            collector._BreadcrumbTraceCollector__max_entries = max_entries
            version_to_message = {
                int(entry["version"]): entry["message"]
                for entry in collector._BreadcrumbTraceCollector__entries
            }
            actual_evictions = []
            record_gap = "_BreadcrumbTraceCollector__record_gap_range"
            original_record_gap = getattr(collector, record_gap)

            def capture_gap(start, end, reason):
                if reason == "evicted":
                    actual_evictions.append((version_to_message[start], start, end))
                return original_record_gap(start, end, reason)

            with patch.object(collector, record_gap, side_effect=capture_gap):
                self.assertTrue(collector._BreadcrumbTraceCollector__evict_to_budget())

            self.assertEqual(expected_evictions, actual_evictions)
            self.assertEqual(
                expected_remaining,
                [entry["message"] for entry in collector._BreadcrumbTraceCollector__entries],
            )

    def test_eviction_ties_use_oldest_and_falsey_categories_share_unknown_bucket(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=None)
        for index in range(4):
            collector.record("worker", "event-{}".format(index), category="seed")

        entries = collector._BreadcrumbTraceCollector__entries
        entries[0]["category"] = None
        entries[1]["category"] = False
        entries[2]["category"] = "other"
        entries[3]["category"] = "other"
        collector._BreadcrumbTraceCollector__max_entries = 2
        version_to_message = {
            int(entry["version"]): entry["message"] for entry in entries
        }
        evictions = []
        record_gap = "_BreadcrumbTraceCollector__record_gap_range"
        original_record_gap = getattr(collector, record_gap)

        def capture_gap(start, end, reason):
            if reason == "evicted":
                evictions.append((version_to_message[start], start, end))
            return original_record_gap(start, end, reason)

        with patch.object(collector, record_gap, side_effect=capture_gap):
            self.assertTrue(collector._BreadcrumbTraceCollector__evict_to_budget())

        self.assertEqual([("event-0", 1, 1), ("event-2", 3, 3)], evictions)
        self.assertEqual(["event-1", "event-3"], [
            entry["message"] for entry in collector._BreadcrumbTraceCollector__entries
        ])

    def test_eviction_recomputes_candidates_after_retention_policy_transition(self):
        collector = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=None,
            policy={"retention": {"protected_categories": ["keep"]}},
        )
        collector.record("worker", "keep-old", category="keep")
        collector.record("worker", "noise-a", category="noise")
        collector.record("worker", "noise-b", category="noise")
        collector.record("worker", "keep-new", category="keep")
        collector.apply_policy({"retention": {"protected_categories": ["noise"]}})
        collector._BreadcrumbTraceCollector__max_entries = 2

        evictions = []
        record_gap = "_BreadcrumbTraceCollector__record_gap_range"
        original_record_gap = getattr(collector, record_gap)

        def capture_gap(start, end, reason):
            if reason == "evicted":
                evictions.append(start)
            return original_record_gap(start, end, reason)

        with patch.object(collector, record_gap, side_effect=capture_gap):
            self.assertTrue(collector._BreadcrumbTraceCollector__evict_to_budget())

        self.assertEqual([1, 4], evictions)
        self.assertEqual(["noise-a", "noise-b"], [
            entry["message"] for entry in collector._BreadcrumbTraceCollector__entries
        ])

    def test_oversized_drop_and_multi_eviction_keep_accounting_unchanged(self):
        collector = BreadcrumbTraceCollector(
            lambda: True, max_entries=1, memory_budget_bytes=768,
        )
        self.assertEqual(
            "dropped",
            collector.record("worker", "oversized", {"payload": "x" * 10_000}),
        )
        for index in range(3):
            self.assertEqual("retained", collector.record("worker", "event-{}".format(index)))

        payload = collector.snapshot()
        self.assertEqual(["event-2"], [
            entry["message"] for entry in payload["entries"]
        ])
        self.assertEqual(1, payload["accounting"]["oversized_dropped_count"])
        self.assertEqual(2, payload["accounting"]["evicted_count"])
        self.assertTrue(any(gap["reason"] == "oversized" for gap in payload["gaps"]))
        self.assertTrue(any(gap["reason"] == "evicted" for gap in payload["gaps"]))

    def test_eviction_removes_coalescing_identity_and_preserves_ranges(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=2)
        collector.record("worker", "first", category="alpha", _coalesce_key="stable")
        collector.record("worker", "second", category="beta", _coalesce_key="other")
        collector.record("worker", "first", category="alpha", _coalesce_key="stable")
        collector.record("worker", "third", category="gamma", _coalesce_key="third")

        self.assertEqual("retained", collector.record(
            "worker", "first", category="alpha", _coalesce_key="stable",
        ))
        payload = collector.snapshot()
        self.assertEqual(["third", "first"], [entry["message"] for entry in payload["entries"]])
        self.assertEqual([1, 1], [entry["repeat_count"] for entry in payload["entries"]])
        self.assertEqual(
            {"from_version": 1, "to_version": 3, "reason": "evicted"},
            next(gap for gap in payload["gaps"] if gap["reason"] == "evicted"),
        )
        self.assertIs(
            collector._BreadcrumbTraceCollector__coalesce_entries["coalesce:stable"],
            collector._BreadcrumbTraceCollector__entries[-1],
        )

    def test_eviction_does_not_change_durable_admission_or_loss_accounting(self):
        class RecordingSpool:
            def __init__(self):
                self.calls = []

            def enqueue(self, entry, is_critical):
                self.calls.append((entry["message"], is_critical))
                return len(self.calls) == 1

        collector = BreadcrumbTraceCollector(lambda: True, max_entries=1)
        spool = RecordingSpool()
        collector._BreadcrumbTraceCollector__durable_spool = spool

        self.assertEqual("retained", collector.record("worker", "first"))
        self.assertEqual("retained", collector.record("worker", "second"))

        self.assertEqual(["first", "second"], [call[0] for call in spool.calls])
        self.assertEqual(1, collector._BreadcrumbTraceCollector__durable_enqueue_rejected_count)
        self.assertEqual(["second"], [
            entry["message"] for entry in collector.snapshot()["entries"]
        ])
        self.assertEqual(1, collector.snapshot()["accounting"]["evicted_count"])

    def test_protected_indices_snapshot_matches_oracle_and_recomputes_after_policy_replace(self):
        collector = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=None,
            policy={
                "retention": {
                    "protected_categories": ["protected.*"],
                    "latest_decision_event_types": ["decision"],
                },
            },
        )
        entries = (
            {"category": None, "event_type": "decision", "level": "info"},
            {"category": "", "event_type": "decision", "level": "info"},
            {"category": "protected.audit", "event_type": "breadcrumb", "level": "info"},
            {"category": "wild.child", "event_type": "breadcrumb", "level": "info"},
            {"category": "warning.only", "event_type": "breadcrumb", "level": "warning"},
            {"category": "error.only", "event_type": "breadcrumb", "level": "error"},
        )

        classifier = "_BreadcrumbTraceCollector__is_category_protected"
        original_classifier = getattr(collector, classifier)

        def pre_change_protected(entries):
            retention = collector._BreadcrumbTraceCollector__policy["retention"]
            latest_decision = {}
            for index, entry in enumerate(entries):
                if entry.get("event_type") in retention["latest_decision_event_types"]:
                    latest_decision[str(entry.get("category") or "unknown")] = index
            protected = set(latest_decision.values())
            for index, entry in enumerate(entries):
                if (
                    entry.get("level") in retention["protected_levels"]
                    or original_classifier(str(entry.get("category") or ""))
                ):
                    protected.add(index)
            return protected

        with patch.object(collector, classifier, wraps=getattr(collector, classifier)) as protected_match:
            first = collector._BreadcrumbTraceCollector__protected_indices(entries)
            self.assertEqual(pre_change_protected(entries), first)
            self.assertEqual({1, 2, 4, 5}, first)
            self.assertEqual(["", "protected.audit", "wild.child"], [
                call.args[0] for call in protected_match.call_args_list
            ])
            self.assertEqual(3, protected_match.call_count)

            collector.apply_policy({
                "retention": {
                    "protected_categories": ["wild.*"],
                    "latest_decision_event_types": ["decision"],
                },
            })
            protected_match.reset_mock()
            second = collector._BreadcrumbTraceCollector__protected_indices(entries)

        self.assertEqual(pre_change_protected(entries), second)
        self.assertEqual({1, 3, 4, 5}, second)
        self.assertEqual(["", "protected.audit", "wild.child"], [
            call.args[0] for call in protected_match.call_args_list
        ])
        self.assertEqual(3, protected_match.call_count)

    def test_coalesced_failure_keeps_latest_failure_in_entry_order(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=8)

        collector.record(
            "worker", "failure-a", event_type="failure", level="error",
            category="failure.a", _coalesce_key="a",
        )
        collector.record(
            "worker", "failure-b", event_type="failure", level="error",
            category="failure.b", _coalesce_key="b",
        )
        collector.record(
            "worker", "failure-a", event_type="failure", level="error",
            category="failure.a", _coalesce_key="a",
        )

        payload = collector.snapshot()
        self.assertEqual("failure-b", payload["latest_failure_entry"]["message"])
        self.assertEqual(2, payload["latest_failure_version"])
        self.assertEqual(2, len(payload["entries"]))
        self.assertEqual(3, payload["entries"][0]["last_seen_version"])
        self.assertEqual(2, payload["entries"][1]["last_seen_version"])

    def test_coalesced_non_failure_refreshes_failure_projection(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=8)

        collector.record(
            "worker", "failure", event_type="failure", level="error",
            category="failure", _coalesce_key="same-entry",
        )
        collector.record(
            "worker", "failure", event_type="diagnostic", level="error",
            category="failure", _coalesce_key="same-entry",
        )

        payload = collector.snapshot()
        self.assertEqual("failure", payload["latest_failure_entry"]["message"])
        self.assertEqual(2, payload["latest_failure_entry"]["last_seen_version"])
        self.assertEqual(2, payload["latest_failure_version"])

    def test_eviction_refreshes_latest_failure_by_entry_order(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=3)

        collector.record(
            "worker", "failure-a", event_type="failure", level="error",
            category="failure.a", _coalesce_key="a",
        )
        collector.record(
            "worker", "failure-b", event_type="failure", level="error",
            category="failure.b", _coalesce_key="b",
        )
        collector.record(
            "worker", "failure-a", event_type="failure", level="error",
            category="failure.a", _coalesce_key="a",
        )
        collector.record("worker", "noise-1", category="noise")
        collector.record("worker", "noise-2", category="noise")

        payload = collector.snapshot()
        self.assertEqual("failure-b", payload["latest_failure_entry"]["message"])
        self.assertEqual(2, payload["latest_failure_version"])
        self.assertEqual(1, payload["accounting"]["evicted_count"])
        self.assertEqual(3, payload["entries"][0]["last_seen_version"])

    def test_admission_preserves_stale_signature_without_coalesce_key(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)
        collector.record("worker", "event", _coalesce_key="semantic-key")

        entries = collector.snapshot()["entries"]
        expected = collector._BreadcrumbTraceCollector__signature(entries[-1])
        actual = collector._BreadcrumbTraceCollector__last_signature
        self.assertEqual(expected, actual)
        self.assertNotEqual("coalesce:semantic-key", actual)

    def test_snapshot_preserves_retained_entries_after_disable(self):
        enabled = {"value": True}
        collector = BreadcrumbTraceCollector(lambda: enabled["value"], max_entries=2)

        collector.record("controller", "start", {"phase": "init"})
        enabled["value"] = False

        snapshot = collector.snapshot()
        self.assertEqual(False, snapshot["enabled"])
        self.assertEqual(1, snapshot["entry_count"])
        self.assertEqual("start", snapshot["entries"][0]["message"])
        self.assertFalse(snapshot["window_reset"])
        self.assertIsNone(snapshot["window_reset_reason"])
        enabled["value"] = True
        self.assertEqual(1, collector.snapshot()["entry_count"])

    def test_snapshot_while_disabled_surfaces_pending_external_records(self):
        enabled = {"value": True}
        collector = BreadcrumbTraceCollector(lambda: enabled["value"], max_entries=2)
        emitter = collector.create_emitter()

        emitter.record("controller", "start", {"phase": "queued"}, stage="controller", corr_id="flow-1")
        enabled["value"] = False
        collector.sync_enabled_state()

        snapshot = collector.snapshot()
        self.assertEqual(False, snapshot["enabled"])
        self.assertEqual(1, snapshot["external_queue_drain_count"])
        self.assertFalse(snapshot["external_queue_drain_limited"])
        self.assertEqual(1, snapshot["entry_count"])
        self.assertEqual("start", snapshot["entries"][0]["message"])
        self.assertEqual("queued", snapshot["entries"][0]["details"]["phase"])
        self.assertEqual("controller", snapshot["entries"][0]["stage"])
        self.assertEqual("flow-1", snapshot["entries"][0]["corr_id"])

    def test_snapshot_supports_filters_limit_and_order(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        collector.record(
            "controller",
            "start",
            {"path_pair_count": 0},
            stage="controller",
            event_type="state_transition",
            corr_id="flow-1",
            flow_id="flow-a",
            path_pair_id="pair-1",
            file_id="file-1",
        )
        collector.record(
            "controller",
            "refresh",
            {"path_pair_count": 1},
            stage="refresh",
            event_type="state_transition",
            corr_id="flow-2",
            flow_id="flow-b",
            path_pair_id="pair-2",
            file_id="file-2",
        )
        collector.record(
            "controller",
            "finish",
            {"path_pair_count": 2},
            stage="finish",
            event_type="state_transition",
            corr_id="flow-1",
            flow_id="flow-a",
            path_pair_id="pair-1",
            file_id="file-1",
        )

        ascending_snapshot = collector.snapshot(
            corr_id="flow-1",
            flow_id="flow-a",
            event_type="state_transition",
            path_pair_id="pair-1",
            file_id="file-1",
            limit=1,
            order="asc",
        )
        self.assertEqual(1, ascending_snapshot["entry_count"])
        self.assertEqual("start", ascending_snapshot["entries"][0]["message"])
        self.assertEqual(1, ascending_snapshot["query"]["limit"])
        self.assertEqual("asc", ascending_snapshot["query"]["order"])
        self.assertEqual("flow-1", ascending_snapshot["query"]["corr_id"])

        descending_snapshot = collector.snapshot(
            corr_id="flow-1",
            flow_id="flow-a",
            event_type="state_transition",
            path_pair_id="pair-1",
            file_id="file-1",
            limit=1,
            order="desc",
        )
        self.assertEqual(1, descending_snapshot["entry_count"])
        self.assertEqual("finish", descending_snapshot["entries"][0]["message"])
        self.assertEqual("desc", descending_snapshot["query"]["order"])

    def test_clear_resets_retained_entries_and_marks_reset_metadata(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=2)

        collector.record("controller", "start", {"phase": "init"})
        collector.clear()

        snapshot = collector.snapshot()
        self.assertEqual(0, snapshot["entry_count"])
        self.assertEqual(True, snapshot["window_reset"])
        self.assertEqual("clear", snapshot["window_reset_reason"])
        self.assertEqual(1, snapshot["last_reset_version"])
        self.assertEqual("clear", snapshot["last_reset_reason"])
        self.assertEqual([], snapshot["entries"])

    def test_reset_resets_retained_entries_and_marks_reset_metadata(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=2)

        collector.record("controller", "start", {"phase": "init"})
        collector.reset()

        snapshot = collector.snapshot()
        self.assertEqual(0, snapshot["entry_count"])
        self.assertEqual(True, snapshot["window_reset"])
        self.assertEqual("reset", snapshot["window_reset_reason"])
        self.assertEqual(1, snapshot["last_reset_version"])
        self.assertEqual("reset", snapshot["last_reset_reason"])
        self.assertEqual([], snapshot["entries"])

    def test_snapshot_reports_non_limited_drain_when_queue_exactly_exhausted(self):
        class FakeRecordQueue:
            def __init__(self, records):
                self.__records = list(records)

            def get_nowait(self):
                if not self.__records:
                    raise queue.Empty()
                return self.__records.pop(0)

            def empty(self):
                return True

        collector = BreadcrumbTraceCollector(lambda: True, max_entries=2)
        collector._BreadcrumbTraceCollector__external_records = FakeRecordQueue([
            {
                "source": "controller",
                "message": "start",
                "details": {"path_pair_count": 0},
                "metadata": {"stage": "controller", "event_type": "state_transition", "corr_id": "flow-1"},
                "created_ns": 10,
                "created_ms": 0,
            },
            {
                "source": "controller",
                "message": "refresh",
                "details": {"path_pair_count": 1},
                "metadata": {"stage": "refresh", "event_type": "state_transition", "corr_id": "flow-1"},
                "created_ns": 20,
                "created_ms": 0,
            },
        ])

        snapshot = collector.snapshot()
        self.assertEqual(2, snapshot["external_queue_drain_count"])
        self.assertFalse(snapshot["external_queue_drain_limited"])
        self.assertEqual(["start", "refresh"], [entry["message"] for entry in snapshot["entries"]])

    def test_snapshot_reports_bounded_external_drain_telemetry(self):
        class FakeRecordQueue:
            def __init__(self, records):
                self.__records = list(records)

            def get_nowait(self):
                if not self.__records:
                    raise queue.Empty()
                return self.__records.pop(0)

        collector = BreadcrumbTraceCollector(lambda: True, max_entries=2)
        collector._BreadcrumbTraceCollector__external_records = FakeRecordQueue([
            {
                "source": "controller",
                "message": "start",
                "details": {"path_pair_count": 0},
                "metadata": {"stage": "controller", "event_type": "state_transition", "corr_id": "flow-1"},
                "created_ns": 10,
                "created_ms": 0,
            },
            {
                "source": "controller",
                "message": "refresh",
                "details": {"path_pair_count": 1},
                "metadata": {"stage": "refresh", "event_type": "state_transition", "corr_id": "flow-1"},
                "created_ns": 20,
                "created_ms": 0,
            },
            {
                "source": "controller",
                "message": "finish",
                "details": {"path_pair_count": 2},
                "metadata": {"stage": "finish", "event_type": "state_transition", "corr_id": "flow-1"},
                "created_ns": 30,
                "created_ms": 0,
            },
        ])

        snapshot = collector.snapshot()
        self.assertEqual(2, snapshot["external_queue_drain_count"])
        self.assertEqual(2, snapshot["external_queue_drain_limit"])
        self.assertTrue(snapshot["external_queue_drain_limited"])
        self.assertEqual(2, snapshot["entry_count"])
        self.assertEqual(["start", "refresh"], [entry["message"] for entry in snapshot["entries"]])

        snapshot = collector.snapshot()
        self.assertEqual(1, snapshot["external_queue_drain_count"])
        self.assertFalse(snapshot["external_queue_drain_limited"])
        self.assertEqual(["refresh", "finish"], [entry["message"] for entry in snapshot["entries"]])

    def test_record_coalesces_repeated_entries_and_redacts_command_details(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        with patch("common.breadcrumb_trace.time.time_ns", side_effect=[10_000_000, 20_000_000, 30_000_000]):
            collector.record(
                "controller",
                "command_received",
                {
                    "command": "lftp -e 'open secret.example; get file'",
                    "api_token": "super-secret-token",
                    "reason": "x" * 400,
                },
            )
            collector.record(
                "controller",
                "command_received",
                {
                    "command": "lftp -e 'open secret.example; get file'",
                    "api_token": "super-secret-token",
                    "reason": "x" * 400,
                },
            )
            collector.record(
                "controller",
                "command_received",
                {
                    "command": "lftp -e 'open secret.example; get file'",
                    "api_token": "super-secret-token",
                    "reason": "x" * 400,
                },
            )

        snapshot = collector.snapshot()
        self.assertEqual(1, len(snapshot["entries"]))
        entry = snapshot["entries"][0]
        self.assertEqual(3, entry["repeat_count"])
        self.assertEqual("<redacted>", entry["details"]["command"])
        self.assertEqual("<redacted>", entry["details"]["api_token"])
        self.assertTrue(entry["details"]["reason"].endswith("...<truncated>"))
        self.assertEqual(30, entry["last_seen_ms"])

    def test_snapshot_since_version_filters_entries(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        collector.record("controller", "start", {"phase": "init"})
        first_version = collector.snapshot()["version"]
        collector.record("controller", "refresh", {"phase": "retry"})

        snapshot = collector.snapshot(since_version=first_version)
        self.assertEqual(first_version, snapshot["since_version"])
        self.assertEqual(1, len(snapshot["entries"]))
        self.assertEqual("refresh", snapshot["entries"][0]["message"])
        self.assertEqual(False, snapshot["window_reset"])

    def test_snapshot_none_order_defaults_to_ascending(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)
        collector.record("controller", "first", {"position": 1})
        collector.record("controller", "second", {"position": 2})

        snapshot = collector.snapshot(order=None)

        self.assertEqual(["first", "second"], [entry["message"] for entry in snapshot["entries"]])

    def test_record_failure_marks_latest_failure_window(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)

        collector.record("controller", "start", {"phase": "init"}, stage="controller", corr_id="flow-1")
        collector.record("controller", "refresh", {"phase": "queue"}, stage="queue", corr_id="flow-1")
        collector.record(
            "controller",
            "remote_scan_failure",
            {"error_message": "boom", "command": "rm -rf /tmp"},
            event_type="failure",
            corr_id="flow-1",
            stage="scan",
        )

        snapshot = collector.snapshot()
        self.assertEqual(3, snapshot["latest_failure_version"])
        self.assertEqual("remote_scan_failure", snapshot["latest_failure_entry"]["message"])
        self.assertEqual("failure", snapshot["latest_failure_entry"]["event_type"])
        self.assertEqual("flow-1", snapshot["failure_summary"]["corr_id"])
        self.assertEqual("scan", snapshot["failure_summary"]["stage"])
        self.assertEqual("remote_scan_failure", snapshot["failure_summary"]["message"])
        self.assertEqual(3, snapshot["failure_summary"]["version"])
        self.assertEqual("flow", snapshot["failure_summary"]["trace_scope"])
        self.assertLessEqual(len(snapshot["failure_summary"]["recent_stage_trail"]), 5)
        self.assertEqual("controller", snapshot["failure_summary"]["recent_stage_trail"][0]["stage"])

    def test_active_delta_rejection_summary_survives_progress_eviction_and_is_queryable(self):
        collector = BreadcrumbTraceCollector(
            lambda: True, max_entries=2,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        admitted = collector.record_active_delta_authorization_rejection(
            "root-progress:0123456789abcdef", 7,
            {
                "invalidation_reasons": ["safe_reason", "/private/path"],
                "lftp_touched_count": 999,
                "active_touched_count": 2,
                "lftp_regressed_count": -1,
                "pending_token_count": 4,
                "token_reason_counts": {"private-token": 3, "other": -1},
            },
        )

        self.assertTrue(admitted)
        for index in range(1_000):
            collector.record(
                "root_progress", "noisy-{}".format(index), {"count": index},
                category="model.progress", level="debug",
            )

        snapshot = collector.snapshot()
        summary = snapshot["active_delta_rejection_summary"]
        self.assertEqual("root-progress:0123456789abcdef", summary["corr_id"])
        self.assertEqual("active_delta_authorization_rejected", summary["reason"])
        self.assertEqual(7, summary["model_version"])
        self.assertEqual(
            {
                "invalidation_reason_count": 2,
                "rejection_categories": ["authority"],
                "lftp_touched_count": 128,
                "active_touched_count": 2,
                "pending_token_count": 4,
                "token_reason_kind_count": 1,
                "token_reason_total_count": 3,
            },
            summary["diagnostics"],
        )
        self.assertEqual(2, snapshot["retained_count"])
        self.assertGreater(snapshot["evictions"], 0)
        serialized = str(summary)
        self.assertNotIn("/private/path", serialized)
        self.assertNotIn("private-token", serialized)
        self.assertEqual(summary, collector.query_events()["active_delta_rejection_summary"])

        collector.reset()
        self.assertIsNone(collector.snapshot()["active_delta_rejection_summary"])

        collector.record_active_delta_rejection(
            "root-progress:0123456789abcdef", 8, "active_delta_selector_rejected",
            {
                "rejection_categories": ["status", "/private/path", "authority", "status"],
                "selector_failure": "/private/selector-failure",
            },
        )
        summary = collector.snapshot()["active_delta_rejection_summary"]
        self.assertEqual("active_delta_selector_rejected", summary["reason"])
        self.assertEqual(["status", "authority"], summary["diagnostics"]["rejection_categories"])
        self.assertNotIn("/private/path", str(summary))
        self.assertNotIn("/private/selector-failure", str(summary))

    def test_scoped_clear_matches_active_delta_rejection_summary_metadata(self):
        collector = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        collector.record_active_delta_authorization_rejection(
            "root-progress:0123456789abcdef", 7, {},
        )

        collector.clear({"corr_id": "other"})
        self.assertIsNotNone(collector.snapshot()["active_delta_rejection_summary"])
        collector.clear({"corr_id": "root-progress:0123456789abcdef"})
        self.assertIsNone(collector.snapshot()["active_delta_rejection_summary"])

    def test_active_delta_rejection_summary_rejects_malformed_reason(self):
        collector = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        for reason in ({"active_delta_selector_rejected"}, ["active_delta_selector_rejected"],
                       "unknown", b"active_delta_selector_rejected"):
            self.assertFalse(collector.record_active_delta_rejection(
                "root-progress:0123456789abcdef", 1, reason, {},
            ))
        self.assertIsNone(collector.snapshot()["active_delta_rejection_summary"])

    def test_active_delta_rejection_summary_retains_allowlisted_selector_failure(self):
        collector = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )

        self.assertTrue(collector.record_active_delta_rejection(
            "root-progress:0123456789abcdef", 1, "active_delta_selector_rejected",
            {"selector_failure": "unknown_root"},
        ))

        self.assertEqual(
            "unknown_root",
            collector.snapshot()["active_delta_rejection_summary"]["diagnostics"]["selector_failure"],
        )

    def test_active_delta_status_missing_provenance_is_allowlisted_and_redacted(self):
        collector = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        collector.record_active_delta_rejection(
            "root-progress:0123456789abcdef", 1, "active_delta_selector_rejected",
            {
                "selector_failure": "status_missing",
                "status_missing_provenance": {
                    "poll_source": "fresh_healthy", "fresh": True, "healthy": True,
                    "raw_count_bucket": "1", "filtered_count_bucket": "0",
                    "raw_status_match": True, "filtered_status_match": False,
                    "failure_reason": "timeout", "future_state": True,
                    "private_path": "/private/path", "poll_source_bad_type": True,
                    "poll_decision": {
                        "idle_authoritative": True, "next_poll_present": False,
                        "last_status_count_bucket": "0",
                        "poll_suppressed_reason": "idle_authoritative",
                        "private_file_id": "private",
                    },
                },
            },
        )

        provenance = collector.snapshot()["active_delta_rejection_summary"]["diagnostics"][
            "status_missing_provenance"
        ]
        self.assertEqual("fresh_healthy", provenance["poll_source"])
        self.assertEqual("0", provenance["filtered_count_bucket"])
        self.assertNotIn("private_path", provenance)
        self.assertEqual("timeout", provenance["failure_reason"])
        self.assertNotIn("future_state", provenance)
        self.assertEqual({
            "idle_authoritative": True, "next_poll_present": False,
            "last_status_count_bucket": "0", "poll_suppressed_reason": "idle_authoritative",
        }, provenance["poll_decision"])

        collector.record_active_delta_authorization_rejection(
            "root-progress:0123456789abcdef", 7, {},
        )
        collector.clear({"flow": "root-progress:0123456789abcdef"})
        self.assertIsNone(collector.snapshot()["active_delta_rejection_summary"])

        collector.record_active_delta_authorization_rejection(
            "root-progress:0123456789abcdef", 7, {},
        )
        collector.clear({"category": "model.progress", "event_type": "diagnostic"})
        self.assertIsNone(collector.snapshot()["active_delta_rejection_summary"])

    def test_record_aggregate_failure_summary_stays_aggregate_scoped(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=8)

        collector.record("controller", "start", {"phase": "init"}, stage="controller", corr_id="flow-1")
        collector.record(
            "controller",
            "extract_status_result",
            {"status_count": 2, "extracting_count": 1},
            stage="extract",
            corr_id="extract:aggregate",
            trace_scope="aggregate"
        )
        collector.record(
            "controller",
            "remote_scan_failure",
            {"error_message": "boom"},
            event_type="failure",
            stage="scan",
            corr_id="remote_scan:aggregate",
            trace_scope="aggregate",
        )

        snapshot = collector.snapshot()
        summary = snapshot["failure_summary"]
        self.assertEqual("aggregate", summary["trace_scope"])
        self.assertEqual("remote_scan:aggregate", summary["corr_id"])
        self.assertTrue(all(entry["trace_scope"] == "aggregate" for entry in summary["recent_stage_trail"]))
        self.assertNotIn("flow-1", {entry["corr_id"] for entry in summary["recent_stage_trail"]})

    def test_policy_is_hierarchical_and_most_specific(self):
        collector = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=None,
            policy={
                "default": "off",
                "rules": {"*": "error", "transfer": "debug", "transfer.progress": "warning"},
            },
        )

        collector.record("transfer", "debug-event", level="debug")
        collector.record("transfer.progress", "info-event", level="info")
        collector.record("transfer.progress", "warning-event", level="warning")
        collector.record("other", "error-event", level="error")

        events = collector.query_events()["events"]
        self.assertEqual(["debug-event", "warning-event", "error-event"], [event["message"] for event in events])
        invalid = collector.validate_policy({"default": "not-a-level"})
        self.assertFalse(invalid["valid"])

    def test_policy_revision_uses_compare_and_swap_and_reset_preserves_evidence(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)
        collector.record("controller", "before")

        applied = collector.apply_policy({"default": "debug"}, expected_revision=0)
        self.assertTrue(applied["applied"])
        self.assertEqual(1, applied["revision"])
        conflict = collector.apply_policy({"default": "trace"}, expected_revision=0)
        self.assertFalse(conflict["applied"])
        self.assertTrue(conflict["conflict"])
        reset = collector.reset_policy(expected_revision=1)
        self.assertTrue(reset["applied"])
        self.assertEqual(2, reset["revision"])
        self.assertEqual(1, collector.snapshot()["entry_count"])

    def test_unlimited_count_has_no_artificial_1024_event_cap(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=None)

        for index in range(1100):
            collector.record("worker", "event-{}".format(index), {"index": index})

        snapshot = collector.snapshot()
        self.assertIsNone(snapshot["max_entries"])
        self.assertEqual(1100, snapshot["entry_count"])
        self.assertLessEqual(snapshot["retained_bytes"], DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES)

    def test_live_budget_workload_reports_bounded_multi_eviction_and_policy_drop(self):
        bulk_details = {
            "operation": "inspect_status",
            "phase": "scan",
            "state": "active",
            "samples": {
                "sample_{:02d}".format(index): "status" * 42
                for index in range(24)
            },
        }
        summary_details = {
            "operation": "inspect_status",
            "phase": "scan",
            "state": "active",
            "samples": [["status" * 40 for _ in range(16)] for _ in range(16)],
        }
        collector = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=None,
            memory_budget_bytes=DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES,
        )

        collector.record(
            "controller", "warning_observation", {"state": "degraded"},
            category="retention.warning", level="warning", event_type="diagnostic",
        )
        collector.record(
            "controller", "decision_observation", {"decision": "continue"},
            category="retention.decision", event_type="decision",
        )

        # The 24 bounded status samples are representative of the live
        # diagnostic shape and keep the fill payload below the 8 KiB ingress
        # cap.  The larger summary is admitted directly to exercise one
        # multi-eviction transition. This count leaves less than one small
        # entry of headroom while remaining deterministic and avoiding
        # eviction during the fill.
        bulk_count = 37_715
        fill_started = time.perf_counter()
        for index in range(bulk_count):
            collector.record(
                "scanner", "status_update_{:05d}".format(index), bulk_details,
                category=("scan.status", "scan.progress", "scan.metrics")[index % 3],
                level="info", event_type="diagnostic",
            )
        fill_elapsed = time.perf_counter() - fill_started

        before = collector.snapshot()
        self.assertIsNone(before["max_entries"])
        self.assertEqual(DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES, before["memory_budget_bytes"])
        self.assertEqual(0, before["accounting"]["evicted_count"])
        self.assertEqual(bulk_count + 2, before["entry_count"])
        self.assertLessEqual(before["retained_bytes"], DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES)

        evicted_ranges = []
        gap_name = "_BreadcrumbTraceCollector__record_gap_range"
        original_gap = getattr(collector, gap_name)
        protected_name = "_BreadcrumbTraceCollector__protected_indices"

        def capture_gap(start, end, reason):
            if reason == "evicted":
                evicted_ranges.append((start, end))
            return original_gap(start, end, reason)

        with patch.object(collector, gap_name, side_effect=capture_gap) as gap_method:
            with patch.object(
                collector,
                "_BreadcrumbTraceCollector__evict_to_budget",
                wraps=getattr(collector, "_BreadcrumbTraceCollector__evict_to_budget"),
            ) as eviction_method:
                with patch.object(
                    collector,
                    protected_name,
                    wraps=getattr(collector, protected_name),
                ) as protected_method:
                    final_started = time.perf_counter()
                    final_started_ns = time.perf_counter_ns()
                    result = collector.record(
                        "scanner", "status_summary", summary_details,
                        category="scan.status", level="info", event_type="diagnostic",
                    )
        final_elapsed = time.perf_counter() - final_started
        final_elapsed_ns = time.perf_counter_ns() - final_started_ns

        after = collector.snapshot()
        evicted_count = (
            after["accounting"]["evicted_count"]
            - before["accounting"]["evicted_count"]
        )
        self.assertEqual("retained", result)
        self.assertEqual(1, eviction_method.call_count)
        self.assertEqual(evicted_count, len(evicted_ranges))
        self.assertGreater(evicted_count, 1)
        self.assertEqual(evicted_count, gap_method.call_count)
        self.assertEqual(evicted_count, protected_method.call_count)
        self.assertTrue(after["gaps"])
        self.assertTrue(all(gap["reason"] == "evicted" for gap in after["gaps"]))
        self.assertLessEqual(len(after["gaps"]), evicted_count)
        self.assertEqual(before["version"] + 1, after["version"])
        self.assertEqual(
            before["entry_count"] + 1 - evicted_count,
            after["entry_count"],
        )
        self.assertLessEqual(after["retained_bytes"], DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES)
        messages = {entry["message"] for entry in after["entries"]}
        self.assertIn("warning_observation", messages)
        self.assertIn("decision_observation", messages)
        self.assertIn("status_summary", messages)
        categories = {entry["category"] for entry in after["entries"]}
        self.assertTrue({"scan.status", "scan.progress", "scan.metrics"}.issubset(categories))

        print(
            "breadcrumb_collector_benchmark "
            "workload_entry_count={} workload_retained_bytes={} "
            "final_entry_count={} final_retained_bytes={} "
            "final_admission_elapsed_ns={} evicted_count={} "
            "protected_index_pass_count={} eviction_transaction_count={} "
            "selection_pass_count={}".format(
                before["entry_count"], before["retained_bytes"],
                after["entry_count"], after["retained_bytes"],
                final_elapsed_ns, evicted_count,
                protected_method.call_count, eviction_method.call_count,
                protected_method.call_count,
            ),
        )

        policy_collector = BreadcrumbTraceCollector(lambda: True, max_entries=None)
        policy_collector.apply_policy({"default": "off"})
        # Keep the public collector.record path while making the policy
        # transition deterministic between its gate and admission body.
        with patch.object(policy_collector, "is_effectively_enabled", return_value=True):
            self.assertEqual(
                "dropped",
                policy_collector.record(
                    "scanner", "policy_filtered", {"state": "inactive"},
                    category="scan.status", level="info", event_type="diagnostic",
                ),
            )
        policy_snapshot = policy_collector.snapshot()
        self.assertEqual(1, policy_snapshot["accounting"]["policy_dropped_count"])
        self.assertEqual([], policy_snapshot["entries"])

        # This is a gross runaway guard only; it is intentionally far above
        # the normal local runtime and is not a performance threshold.
        self.assertLess(fill_elapsed, 180.0)
        self.assertLess(final_elapsed, 30.0)

    def test_memory_budget_and_count_accounting_report_evictions_and_gaps(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=2, memory_budget_bytes=10_000)
        collector.record("worker", "one", {"value": "a" * 100})
        collector.record("worker", "two", {"value": "b" * 100})
        collector.record("worker", "three", {"value": "c" * 100})

        payload = collector.query_events(since_version=0)
        self.assertEqual(1, payload["accounting"]["evicted_count"])
        self.assertEqual(2, payload["accounting"]["retained_count"])
        self.assertTrue(any(gap["reason"] == "evicted" for gap in payload["gaps"]))
        self.assertLessEqual(payload["retained_bytes"], 10_000)

    def test_record_reports_dropped_when_budget_evicts_the_new_event(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=1, memory_budget_bytes=10_000)
        self.assertEqual(
            "retained",
            collector.record(
                "controller", "failure", event_type="failure",
                category="queue.exclusion", level="error",
            ),
        )
        self.assertEqual(
            "dropped",
            collector.record("controller", "ordinary", event_type="diagnostic", category="queue.exclusion"),
        )

        payload = collector.query_events(since_version=0)
        self.assertEqual(["failure"], [event["message"] for event in payload["events"]])
        self.assertTrue(any(gap["reason"] == "evicted" for gap in payload["gaps"]))

    def test_scoped_clear_does_not_reset_policy_and_export_is_bounded(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=None)
        collector.apply_policy({"default": "debug"})
        collector.record("keep", "keep", {"data": "x" * 100})
        collector.record("remove", "remove", {"data": "y" * 100}, corr_id="flow-remove")

        cleared = collector.clear({"corr_id": "flow-remove"})
        self.assertEqual(1, cleared["cleared_count"])
        self.assertEqual(1, collector.snapshot()["entry_count"])
        self.assertEqual(1, collector.policy_snapshot()["revision"])
        exported = collector.export_events(max_bytes=1_000_000)
        self.assertTrue(exported["export_truncated"] is False)
        self.assertEqual(1, exported["export_event_count"])

    def test_child_ingress_is_bounded_and_rejections_create_a_gap(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        emitter = collector.create_emitter()
        emitter.record(None, "huge", {"value": "x" * 20_000})
        payload = collector.query_events(since_version=0)
        self.assertEqual([], payload["events"])
        self.assertTrue(any(gap["reason"] == "ingress_rejected" for gap in payload["gaps"]))
        self.assertGreater(payload["version"], 0)

    def test_child_ingress_caps_structured_mapping_at_24_items(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        emitter = collector.create_emitter()
        details = {"field_{:02d}".format(index): index for index in range(25)}

        emitter.record("model_builder", "diagnostic", details)

        payload = collector.query_events(since_version=0)
        self.assertEqual(1, len(payload["events"]))
        retained_details = payload["events"][0]["details"]
        self.assertEqual(
            {"field_{:02d}".format(index): index for index in range(24)},
            retained_details,
        )
        self.assertEqual(24, len(retained_details))
        self.assertNotIn("field_24", retained_details)

    def test_policy_persist_failure_rolls_back_live_revision(self):
        def fail_persist(policy):
            raise OSError("no write")

        collector = BreadcrumbTraceCollector(lambda: True, policy_persist=fail_persist)
        with self.assertRaises(OSError):
            collector.apply_policy({"default": "debug"}, persist=True)
        snapshot = collector.policy_snapshot()
        self.assertEqual(0, snapshot["revision"])
        self.assertEqual("info", snapshot["default"])
        self.assertEqual("failed_rolled_back", snapshot["persistence"]["state"])

    def test_deep_query_applies_filter_before_page_limit(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        collector.record("other", "one", category="other")
        collector.record("target", "two", category="target.deep")
        events = collector.query_events(category_prefix="target", limit=1)["events"]
        self.assertEqual(["two"], [event["message"] for event in events])

    def test_retention_prefers_noisy_category_and_keeps_latest_decision(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=3, memory_budget_bytes=100_000)
        collector.record("noisy", "one", category="noisy")
        collector.record("noisy", "two", category="noisy")
        collector.record("decision", "latest", category="decision", event_type="decision")
        collector.record("quiet", "three", category="quiet")

        payload = collector.query_events()
        messages = [event["message"] for event in payload["events"]]
        self.assertNotIn("one", messages)
        self.assertIn("latest", messages)
        self.assertEqual(1, payload["accounting"]["categories"]["noisy"]["evicted"])
        self.assertEqual("overrepresented_unprotected_category_then_oldest",
                         payload["retention"]["policy"]["eviction"])

    def test_child_policy_revision_is_annotated_and_acknowledged_when_drained(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)
        emitter = collector.create_emitter()
        collector.apply_policy({"default": "debug"})
        emitter.record("worker", "event")
        payload = collector.query_events()
        self.assertEqual(1, payload["events"][0]["worker_policy_revision"])
        self.assertEqual(1, payload["worker_propagation"]["ack_revision"])
        self.assertFalse(payload["worker_propagation"]["pending"])

    def test_reset_cursor_equal_to_reset_version_is_durably_detected(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4)
        collector.record("controller", "before")
        reset_version = collector.reset()["version"]
        collector.record("controller", "after")
        payload = collector.query_events(since_version=reset_version)
        self.assertTrue(payload["window_reset"])
        self.assertEqual("reset", payload["window_reset_reason"])

    def test_rollback_advances_policy_epoch_and_old_ack_stays_pending(self):
        collector = BreadcrumbTraceCollector(
            lambda: True, max_entries=4,
            policy_persist=lambda policy: (_ for _ in ()).throw(OSError("write")),
        )
        emitter = collector.create_emitter()
        collector.apply_policy({"default": "debug"})
        emitter.record("worker", "old")
        with self.assertRaises(OSError):
            collector.apply_policy({"default": "trace"}, persist=True)
        payload = collector.query_events()
        self.assertNotEqual(payload["worker_propagation"]["ack_epoch"], payload["worker_propagation"]["current_epoch"])
        self.assertTrue(payload["worker_propagation"]["pending"])

    def test_category_accounting_snapshot_aggregates_retained_overflow(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=32, memory_budget_bytes=65536)
        for index in range(32):
            collector.record("source", "event-{}".format(index), category="category-{}".format(index))
        categories = collector.snapshot()["accounting"]["categories"]
        self.assertLessEqual(len(categories), 17)
        self.assertIn("__other_categories__", categories)

    def test_effective_gate_rejects_system_category_and_level_before_serialization(self):
        def details_factory():
            raise AssertionError("disabled breadcrumb constructed details")

        disabled = BreadcrumbTraceCollector(lambda: False, policy={"default": "trace"})
        category_disabled = BreadcrumbTraceCollector(lambda: True, policy={"default": "off"})
        level_disabled = BreadcrumbTraceCollector(lambda: True, policy={"default": "warning"})

        for collector, category, level in (
            (disabled, "scan", "trace"),
            (category_disabled, "scan", "error"),
            (level_disabled, "scan", "info"),
        ):
            with patch.object(
                collector, "_BreadcrumbTraceCollector__sanitize_value",
                side_effect=AssertionError("disabled breadcrumb was serialized"),
            ):
                self.assertEqual(
                    "disabled",
                    collector.record("source", "message", details_factory, category=category, level=level),
                )
            self.assertFalse(collector.is_effectively_enabled(category, level))

    def test_effective_gate_preserves_inheritance_and_refreshes_collector_policy(self):
        collector = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"transfer": "debug", "transfer.progress": "warning"}},
        )

        self.assertTrue(collector.is_effectively_enabled("transfer.child", "debug"))
        self.assertFalse(collector.is_effectively_enabled("transfer.progress.child", "info"))
        self.assertTrue(collector.is_effectively_enabled("transfer.progress.child", "warning"))
        collector.apply_policy({"default": "error"})
        self.assertFalse(collector.is_effectively_enabled("transfer.child", "debug"))
        self.assertTrue(collector.is_effectively_enabled("transfer.child", "error"))

    def test_explicit_policy_gate_does_not_inherit_default(self):
        collector = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "info", "rules": {"model.publication": "info"}},
        )
        emitter = collector.create_emitter()

        self.assertTrue(collector.is_explicitly_configured("model.publication.child"))
        self.assertTrue(emitter.is_explicitly_configured("model.publication.child"))
        self.assertFalse(collector.is_explicitly_configured("model.lifecycle"))
        self.assertFalse(emitter.is_explicitly_configured("model.lifecycle"))

    def test_effective_gate_denies_off_events_and_off_configured_categories(self):
        default_off = BreadcrumbTraceCollector(lambda: True, policy={"default": "off"})
        rule_off = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "debug", "rules": {"scan": "off"}},
        )

        self.assertFalse(default_off.is_effectively_enabled("scan", "off"))
        self.assertFalse(rule_off.is_effectively_enabled("scan.child", "off"))
        self.assertFalse(rule_off.is_effectively_enabled("scan.child", "error"))

    def test_spawned_emitter_effective_gate_skips_ingress_serializer_and_refreshes_policy(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=4, policy={"default": "off"})
        emitter = collector.create_emitter()

        with patch(
            "common.breadcrumb_trace._bounded_ingress_record",
            side_effect=AssertionError("disabled breadcrumb entered ingress serialization"),
        ):
            self.assertFalse(emitter.is_effectively_enabled("scan", "info"))
            self.assertFalse(emitter.is_effectively_enabled("scan", "off"))
            self.assertEqual("dropped", emitter.record("worker", "disabled", object(), category="scan", level="info"))

        collector.apply_policy({"default": "debug", "rules": {"scan": "debug", "scan.deep": "warning"}})
        self.assertTrue(emitter.is_effectively_enabled("scan", "info"))
        self.assertTrue(emitter.is_effectively_enabled("scan.deep.child", "warning"))
        self.assertFalse(emitter.is_effectively_enabled("scan.deep.child", "info"))
        self.assertEqual("enqueued", emitter.record("worker", "enabled", {"phase": "run"}, category="scan", level="info"))
        self.assertEqual(["enabled"], [entry["message"] for entry in collector.query_events()["events"]])

        collector.apply_policy({"default": "debug", "rules": {"scan": "off"}})
        self.assertFalse(emitter.is_effectively_enabled("scan.child", "off"))
        self.assertFalse(emitter.is_effectively_enabled("scan.child", "error"))
        collector.apply_policy({"default": "off"})
        self.assertFalse(emitter.is_effectively_enabled("scan", "info"))
