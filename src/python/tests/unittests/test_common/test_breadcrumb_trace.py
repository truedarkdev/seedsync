# Copyright 2026, SeedSync Contributors, All rights reserved.

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

    def test_snapshot_clears_retained_entries_after_disable(self):
        enabled = {"value": True}
        collector = BreadcrumbTraceCollector(lambda: enabled["value"], max_entries=2)

        collector.record("controller", "start", {"phase": "init"})
        enabled["value"] = False

        snapshot = collector.snapshot()
        self.assertEqual(False, snapshot["enabled"])
        self.assertEqual([], snapshot["entries"])
        self.assertEqual(True, snapshot["window_reset"])
        self.assertEqual("disabled", snapshot["window_reset_reason"])
        enabled["value"] = True
        self.assertEqual([], collector.snapshot()["entries"])

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
