# Copyright 2026, SeedSync Contributors, All rights reserved.

import queue
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
