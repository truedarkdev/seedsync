# Copyright 2026, SeedSync Contributors, All rights reserved.

import queue
import unittest
from unittest.mock import patch

from common.breadcrumb_trace import BreadcrumbTraceCollector


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
