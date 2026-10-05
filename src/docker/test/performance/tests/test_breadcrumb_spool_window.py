from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from breadcrumb_spool_capture import select_input_arbitration_rows
import breadcrumb_spool_window as window


def _row(created_ms: int, *, schema: str = "known.v1") -> bytes:
    return (json.dumps({
        "created_ms": created_ms,
        "category": "model.progress",
        "event_type": "progress",
        "stage": "model_progress",
        "details": {"schema": schema},
    }, separators=(",", ":")) + "\n").encode()


class BreadcrumbSpoolWindowTests(unittest.TestCase):
    def test_explicit_end_selects_older_window_rotations_and_excludes_late_files(self) -> None:
        inventory = {
            "breadcrumbs-1000000000-1.jsonl": (1, 1, 10),
            "breadcrumbs-2000000000-2.jsonl": (1, 2, 10),
            "breadcrumbs-3000000000-3.jsonl": (1, 3, 10),
            "breadcrumbs-4000000000-4.jsonl": (1, 4, 10),
            "breadcrumbs-5000000000-5.jsonl": (1, 5, 10),
            "breadcrumbs-6000000000-6.jsonl": (1, 6, 10),
            "breadcrumbs.jsonl": (1, 7, 10),
        }
        selected = window.select_window_files(inventory, 2500, 4500)
        self.assertEqual(selected["selected_names"], (
            "breadcrumbs-3000000000-3.jsonl",
            "breadcrumbs-4000000000-4.jsonl",
            "breadcrumbs-5000000000-5.jsonl",
        ))
        self.assertNotIn("breadcrumbs-6000000000-6.jsonl", selected["selected_names"])
        self.assertEqual(selected["candidate_count"], 3)
        self.assertIn("retention_unknown", selected["reasons"])

    def test_raw_chunk_is_retained_before_rejected_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            spool = Path(temporary)
            valid = {
                "created_ms": 1500,
                "corr_id": "input-arbitration:0123456789abcdef",
                "flow_id": "input-arbitration:0123456789abcdef",
                "trace_scope": "flow",
                "source": "model_updater",
                "category": "scan.authority",
                "event_type": "diagnostic",
                "stage": "scan_authority",
                "message": "input_arbitration_witness",
                "details": {
                    "schema": "model_input_arbitration.v1", "phase": "root_fill",
                    "subject_kind": "root", "root_id": "fedcba9876543210",
                    "subject_id": "fedcba9876543210", "input_scope": "root", "selection": "local",
                },
            }
            rejected = {
                "created_ms": 1600, "category": "unsupported.category",
                "event_type": "diagnostic", "stage": "scan_authority",
                "details": {"schema": "unsupported.v0", "private": "must-not-project"},
            }
            rows = (json.dumps(valid, separators=(",", ":")) + "\n" +
                    json.dumps(rejected, separators=(",", ":")) + "\n").encode()
            (spool / "breadcrumbs.jsonl").write_bytes(rows)
            retained: list[tuple[str, int, bytes]] = []
            projected: list[dict] = []

            result = window.capture_window(
                spool, 1000, 2000,
                retain_raw=lambda name, offset, chunk: retained.append((name, offset, chunk)),
                consume_projected=projected.append,
            )
            self.assertEqual(result["coverage"], "unknown")
            self.assertEqual(result["raw_retention"], "complete")
            self.assertEqual(result["bytes_retained"], len(rows))
            self.assertEqual((result["rows_projected"], result["rows_rejected"]), (1, 1))
            self.assertIn("projection_rejected", result["omissions"])
            raw = b"".join(chunk for _, _, chunk in retained)
            self.assertEqual(raw, rows)
            self.assertIn(b"unsupported.category", raw)
            selected = select_input_arbitration_rows(projected, "fedcba9876543210")
            self.assertEqual(selected["state"], "selected")
            self.assertEqual(len(selected["records"]), 1)
            self.assertFalse(set(result) & {"rows", "paths", "file_names", "payload"})

    def test_missing_predecessor_and_read_failure_remain_unknown(self) -> None:
        selection = window.select_window_files(
            {"breadcrumbs-3000000000-3.jsonl": (1, 3, 10), "breadcrumbs.jsonl": (1, 4, 10)},
            2500, 3500,
        )
        self.assertIn("missing_predecessor", selection["reasons"])

        with tempfile.TemporaryDirectory() as temporary:
            spool = Path(temporary)
            (spool / "breadcrumbs.jsonl").write_bytes(_row(1500))
            with mock.patch.object(window.os, "pread", side_effect=OSError("read failed")):
                result = window.capture_window(
                    spool, 1000, 2000, retain_raw=lambda _name, _offset, _chunk: None,
                )
        self.assertEqual(result["coverage"], "unknown")
        self.assertEqual(result["raw_retention"], "unknown")
        self.assertIn("read_failed", result["omissions"])

    def test_file_cap_and_byte_cap_are_explicit_unknown_coverage(self) -> None:
        inventory = {
            f"breadcrumbs-{tick * 1_000_000_000}-{tick}.jsonl": (1, tick, 10)
            for tick in range(1, 7)
        }
        inventory["breadcrumbs.jsonl"] = (1, 7, 10)
        selected = window.select_window_files(inventory, 0, 10000)
        self.assertEqual(len(selected["selected_names"]), window.MAX_WINDOW_FILES)
        self.assertEqual(selected["omitted_files"], 3)
        self.assertIn("file_cap", selected["reasons"])

        with tempfile.TemporaryDirectory() as temporary:
            spool = Path(temporary)
            for tick in range(1, 6):
                (spool / f"breadcrumbs-{tick * 1_000_000_000}-{tick}.jsonl").write_bytes(_row(1500))
            (spool / "breadcrumbs.jsonl").write_bytes(_row(1500))
            result = window.capture_window(
                spool, 0, 10000,
                retain_raw=lambda _name, _offset, _chunk: None,
            )
        self.assertEqual(result["coverage"], "unknown")
        self.assertEqual(result["raw_retention"], "partial")
        self.assertEqual(result["omitted_files"], 2)
        self.assertIn("file_cap", result["omissions"])

        # Exercise aggregate byte truncation cheaply through the production cap branch.
        old_limit = window.MAX_WINDOW_BYTES
        try:
            window.MAX_WINDOW_BYTES = 16
            with tempfile.TemporaryDirectory() as temporary:
                spool = Path(temporary)
                payload = _row(1500) + b" " * 64
                (spool / "breadcrumbs.jsonl").write_bytes(payload)
                retained: list[bytes] = []
                result = window.capture_window(
                    spool, 1000, 2000,
                    retain_raw=lambda _name, _offset, chunk: retained.append(chunk),
                )
            self.assertEqual(result["coverage"], "unknown")
            self.assertEqual(result["raw_retention"], "partial")
            self.assertEqual(result["bytes_retained"], 16)
            self.assertEqual(sum(map(len, retained)), 16)
            self.assertIn("byte_cap", result["omissions"])
        finally:
            window.MAX_WINDOW_BYTES = old_limit

    def test_raw_retention_failure_prevents_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            spool = Path(temporary)
            (spool / "breadcrumbs.jsonl").write_bytes(_row(1500))
            projected = []

            def fail_retention(_name: str, _offset: int, _chunk: bytes) -> None:
                raise OSError("private sink unavailable")

            result = window.capture_window(
                spool, 1000, 2000, retain_raw=fail_retention,
                projector=lambda value: (projected.append(value) or ({"safe": True}, False)),
            )
            self.assertFalse(projected)
            self.assertEqual(result["raw_retention"], "failed")
            self.assertIn("raw_retention_failed", result["omissions"])
            self.assertEqual(result["rows_projected"], 0)


if __name__ == "__main__":
    unittest.main()
