import io
import unittest
from unittest.mock import MagicMock, patch

import pexpect

import lftp.lftp as lftp_mod
from common.breadcrumb_trace import BreadcrumbTraceCollector
from lftp import Lftp, LftpJobStatusParserError


class TestLftpStatusDiagnostics(unittest.TestCase):
    @staticmethod
    def _build_lftp():
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__consecutive_status_errors = 0
        lftp._Lftp__last_command_timed_out = False
        lftp._Lftp__last_status_poll_healthy = True
        lftp._Lftp__base_remote_dir_path = "/remote/default"
        lftp._Lftp__base_local_dir_path = "/local/default"
        lftp._Lftp__path_pairs_by_id = {}
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.return_value = []
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b""
        process.after = b"prompt>"
        process.buffer_type = io.StringIO
        process._buffer = io.StringIO()
        process._before = io.StringIO()
        process.searchwindowsize = None
        process.delaybeforesend = 7
        process.delayafterread = 11
        process.expect.return_value = None
        process.read_nonblocking.side_effect = pexpect.exceptions.TIMEOUT("empty terminal")
        lftp._Lftp__process = process
        return lftp

    @staticmethod
    def _trace():
        return BreadcrumbTraceCollector(
            lambda: True,
            max_entries=32,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )

    def test_status_boundary_emits_only_fixed_structural_fields(self):
        trace = self._trace()
        lftp_mod._record_lftp_status_poll_breadcrumb(
            trace,
            "lftp-poll:0123456789abcdef",
            "jobs_read",
            process_alive=True,
            output="private/path\nstatus",
            raw_before=b"raw/private\nline",
            retained=io.BytesIO(b"retained/private\n"),
            normalized="status",
            prompt_outcome="empty",
            error_class="none",
            pre_send_drain_class="queue_done",
            pre_send_drain_bytes=17,
            status_count=3,
        )

        event = trace.snapshot()["entries"][0]
        details = event["details"]
        self.assertEqual("lftp-poll:0123456789abcdef", event["corr_id"])
        self.assertEqual("lftp-poll:0123456789abcdef", event["flow_id"])
        self.assertEqual("queue_done", details["pre_send_drain_class"])
        self.assertEqual("1-127", details["raw_before_byte_length_bucket"])
        self.assertEqual("1-127", details["retained_byte_length_bucket"])
        self.assertEqual("1-127", details["normalized_byte_length_bucket"])
        self.assertEqual("2-4", details["raw_before_line_count_bucket"])
        self.assertEqual("1", details["retained_line_count_bucket"])
        self.assertEqual("1", details["normalized_line_count_bucket"])
        self.assertEqual("empty", details["prompt_outcome"])
        self.assertEqual("none", details["error_class"])
        self.assertEqual("2-4", details["status_count_bucket"])
        self.assertIsInstance(event["created_ns"], int)
        self.assertIsInstance(details["monotonic_time_ns"], int)
        rendered = repr(event)
        self.assertNotIn("private/path", rendered)
        self.assertNotIn("raw/private", rendered)
        self.assertNotIn("retained/private", rendered)

    def test_invalid_poll_correlation_does_not_emit_status_diagnostics(self):
        trace = self._trace()
        lftp_mod._record_lftp_status_poll_breadcrumb(
            trace,
            "lftp-poll:not-an-opaque-token",
            "jobs_read",
            process_alive=True,
            output="private output",
        )
        self.assertEqual([], trace.snapshot()["entries"])

    def test_history_reader_uses_private_retained_buffer_without_materializing_public_history(self):
        class Process:
            def __init__(self):
                self.before = b"raw-before"
                self._buffer = io.BytesIO(b"retained-history")

            @property
            def buffer(self):
                raise AssertionError("public pexpect history must not be read")

        process = Process()
        before, retained = lftp_mod._lftp_status_boundary_values(process)
        self.assertEqual(b"raw-before", before)
        self.assertIs(process._buffer, retained)
        self.assertEqual(
            ("1-127", "1"),
            (
                lftp_mod._lftp_trace_bytes_bucket(lftp_mod._lftp_trace_value_metrics(retained)[0]),
                lftp_mod._lftp_trace_count_bucket(lftp_mod._lftp_trace_value_metrics(retained)[1]),
            ),
        )

    def test_large_single_line_history_uses_bounded_unknown_line_bucket(self):
        trace = self._trace()
        lftp_mod._record_lftp_status_poll_breadcrumb(
            trace,
            "lftp-poll:0123456789abcdef",
            "jobs_read",
            process_alive=True,
            output="",
            raw_before=b"x" * (lftp_mod._LFTP_TRACE_LINE_SCAN_MAX_BYTES + 1),
            retained=io.BytesIO(),
            normalized="",
            prompt_outcome="empty",
            error_class="none",
            pre_send_drain_class="empty",
            pre_send_drain_bytes=0,
        )
        details = trace.snapshot()["entries"][0]["details"]
        self.assertEqual("32768+", details["raw_before_byte_length_bucket"])
        self.assertEqual("unknown", details["raw_before_line_count_bucket"])
        self.assertNotIn("xxx", repr(details))

    def test_unencodable_status_frame_keeps_fixed_diagnostic_record(self):
        lftp = self._build_lftp()
        lftp._Lftp__run_command = MagicMock(return_value="status-\ud800")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        parse_started = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "parse_started"
        )
        self.assertEqual("unknown", parse_started["details"]["normalized_byte_length_bucket"])
        self.assertNotIn("status-", repr(parse_started))

    def test_prompt_timeout_is_not_prompt_empty(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp.time.monotonic", side_effect=[0.0, 1.01]), \
                patch("lftp.lftp.time.sleep"):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        events = trace.snapshot()["entries"]
        prompt_timeout = next(event for event in events if event["details"]["phase"] == "prompt_timeout")
        health = next(event for event in events if event["details"]["phase"] == "health")
        self.assertEqual("timeout", prompt_timeout["details"]["prompt_outcome"])
        self.assertEqual("timeout", prompt_timeout["details"]["error_class"])
        self.assertNotEqual("empty", prompt_timeout["details"]["prompt_outcome"])
        self.assertEqual("timeout", health["details"]["error_class"])

    def test_prompt_empty_is_not_prompt_timeout_or_parser_error(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        events = trace.snapshot()["entries"]
        jobs_read = next(event for event in events if event["details"]["phase"] == "jobs_read")
        parse_complete = next(event for event in events if event["details"]["phase"] == "parse_complete")
        self.assertEqual("empty", jobs_read["details"]["prompt_outcome"])
        self.assertEqual("empty", parse_complete["details"]["prompt_outcome"])
        self.assertEqual("none", parse_complete["details"]["error_class"])
        self.assertNotIn("prompt_timeout", [event["details"]["phase"] for event in events])
        self.assertNotIn("parse_error", [event["details"]["phase"] for event in events])

    def test_parser_error_has_distinct_error_class(self):
        lftp = self._build_lftp()
        lftp._Lftp__job_status_parser.parse.side_effect = LftpJobStatusParserError("private detail")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        self.assertIsNone(lftp.status("lftp-poll:0123456789abcdef"))

        parse_error = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "parse_error"
        )
        self.assertEqual("error", parse_error["details"]["prompt_outcome"])
        self.assertEqual("parser_error", parse_error["details"]["error_class"])
        self.assertNotIn("private detail", repr(trace.snapshot()))


if __name__ == "__main__":
    unittest.main()
