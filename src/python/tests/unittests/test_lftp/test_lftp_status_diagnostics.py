import json
import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pexpect

import lftp.lftp as lftp_mod
from common.breadcrumb_trace import BreadcrumbTraceCollector
from lftp import Lftp, LftpJobStatusParser, LftpJobStatusParserError


class _RealisticStatusPty:
    """Small byte-oriented PTY seam for status-loop terminal outcomes."""

    def __init__(self, mode, payload_size=0):
        self.mode = mode
        self.payload_size = payload_size
        self.before = b""
        self.after = b""
        self.buffer_type = io.BytesIO
        self._buffer = io.BytesIO()
        self._before = io.BytesIO()
        self.delaybeforesend = 0
        self.delayafterread = 0
        self.expect_calls = 0
        self.send_calls = []
        self.closed = False

    def isalive(self):
        return not self.closed

    def send(self, command):
        self.send_calls.append(command)

    def read_nonblocking(self, size=-1, timeout=-1):
        raise pexpect.exceptions.TIMEOUT("quiet terminal")

    def expect(self, _pattern, timeout=None):
        self.expect_calls += 1
        if timeout != 0:
            raise AssertionError("status fixture must use zero-timeout expect")
        if self.mode == "eof":
            self.closed = True
            raise pexpect.exceptions.EOF("fixture eof")
        if self.mode == "partial" and self.expect_calls == 1:
            payload = b"x" * self.payload_size
            self.before = payload
            self._buffer = io.BytesIO(payload)
        raise pexpect.exceptions.TIMEOUT("fixture timeout")

    def close(self, force=False):
        self.closed = True


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

    def test_parser_reports_only_fixed_snapshot_outcomes(self):
        cases = (
            (
                "complete_success",
                "jobs -v\n"
                "[0] queue (sftp://someone:@localhost)\n"
                "sftp://someone:@localhost/remote\n"
                "Queue is running.\n"
                "[1] mirror -c /remote/sample-directory /local/staging/ -- 10/20 (50%)\n",
            ),
            ("blank_empty", ""),
            ("partial_queue", "jobs -v\n[0] queue (sftp://someone:@localhost)\n"),
            ("hard_error", "jobs -v\n[2] jobs -v &\n"),
            (
                "hard_error",
                "jobs -v\n"
                "[0] queue (sftp://someone:@localhost)\n"
                "sftp://someone:@localhost/home/someone\n"
                "Queue is running.\n"
                "Commands queued:\n"
                " 1. mirror -c /tmp/test_lftp/remote/a /tmp/test_lftp/local/\n"
                "mirror: Access failed: Wrong type\n",
            ),
        )
        for expected, output in cases:
            with self.subTest(expected=expected):
                parser = LftpJobStatusParser()
                try:
                    parser.parse(output)
                except LftpJobStatusParserError:
                    pass
                self.assertEqual(expected, parser.last_parse_outcome)
                self.assertNotIn("someone", parser.last_parse_outcome)

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

    def test_post_send_loop_metrics_report_productive_timeout(self):
        lftp = self._build_lftp()
        process = lftp._Lftp__process
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        def first_timeout(*_args, **_kwargs):
            process.before = b"x"
            process._buffer = io.StringIO("y")
            raise pexpect.exceptions.TIMEOUT("productive")

        def second_timeout(*_args, **_kwargs):
            process.before = b"xx"
            process._buffer = io.StringIO("yy")
            raise pexpect.exceptions.TIMEOUT("productive")

        expect_calls = [0]

        def expect_side_effect(*args, **kwargs):
            callback = first_timeout if expect_calls[0] == 0 else second_timeout
            expect_calls[0] += 1
            return callback(*args, **kwargs)

        process.expect.side_effect = expect_side_effect
        with patch("lftp.lftp.time.monotonic", side_effect=[0.0, 0.1, 1.1]), \
                patch("lftp.lftp.time.sleep") as sleep:
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        sleep.assert_not_called()

        jobs_read = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "jobs_read"
        )
        details = jobs_read["details"]
        metrics = details["post_send_metrics"]
        self.assertLessEqual(len(details), 24)
        self.assertEqual("not_required", details["prior_prompt"])
        self.assertTrue(details["send_admitted"])
        self.assertFalse(details["prompt_reached"])
        self.assertEqual("unknown", details["retained_before"])
        self.assertEqual(2, metrics["post_send_zero_timeout_expect_iterations"])
        self.assertEqual(2, metrics["post_send_productive_count"])
        self.assertEqual(0, metrics["post_send_no_progress_count"])
        self.assertEqual(0, metrics["post_send_unknown_count"])
        self.assertEqual(0, metrics["post_send_sleep_requested_ns"])
        self.assertEqual(0, metrics["post_send_sleep_actual_ns"])
        self.assertEqual(1_100_000_000, metrics["post_send_loop_elapsed_ns"])
        self.assertEqual("progress", metrics["post_send_buffer_progress"])

    def test_post_send_loop_metrics_distinguish_mixed_from_all_productive(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        process = lftp._Lftp__process
        expect_calls = [0]

        def mixed_expect(*_args, **_kwargs):
            if expect_calls[0] == 0:
                process.before = b"x"
                process._buffer = io.StringIO("y")
            expect_calls[0] += 1
            raise pexpect.exceptions.TIMEOUT("mixed")

        process.expect.side_effect = mixed_expect
        with patch("lftp.lftp.time.monotonic", side_effect=[0.0, 0.1, 0.2, 1.1]), \
                patch("lftp.lftp.time.sleep"):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        jobs_read = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "jobs_read"
        )
        details = jobs_read["details"]
        metrics = details["post_send_metrics"]
        self.assertEqual(3, metrics["post_send_zero_timeout_expect_iterations"])
        self.assertEqual(1, metrics["post_send_productive_count"])
        self.assertEqual(2, metrics["post_send_no_progress_count"])
        self.assertEqual(0, metrics["post_send_unknown_count"])
        self.assertEqual(1_100_000_000, metrics["post_send_loop_elapsed_ns"])
        self.assertEqual("progress", metrics["post_send_buffer_progress"])

    def test_post_send_loop_metrics_report_no_progress_timeout(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        process = lftp._Lftp__process
        process.before = b"same"
        process._buffer = io.StringIO("same")
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("quiet")

        with patch("lftp.lftp.time.monotonic", side_effect=[0.0, 0.1, 1.1]), \
                patch("lftp.lftp.time.sleep") as sleep:
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        sleep.assert_called_once_with(0.01)

        jobs_read = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "jobs_read"
        )
        details = jobs_read["details"]
        metrics = details["post_send_metrics"]
        self.assertEqual(2, metrics["post_send_zero_timeout_expect_iterations"])
        self.assertEqual(0, metrics["post_send_productive_count"])
        self.assertEqual(2, metrics["post_send_no_progress_count"])
        self.assertEqual(0, metrics["post_send_unknown_count"])
        self.assertEqual(10_000_000, metrics["post_send_sleep_requested_ns"])
        self.assertEqual(1_100_000_000, metrics["post_send_loop_elapsed_ns"])
        self.assertEqual("no_progress", metrics["post_send_buffer_progress"])

    def test_post_send_prompt_completion_reset_is_unknown_progress(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        process = lftp._Lftp__process

        def timeout_then_progress(*_args, **_kwargs):
            process.before = b"x"
            process._buffer = io.StringIO("y")
            raise pexpect.exceptions.TIMEOUT("waiting")

        def prompt_match(*_args, **_kwargs):
            process.before = b""
            process._buffer = io.StringIO()
            return None

        expect_calls = [0]

        def expect_side_effect(*args, **kwargs):
            callback = timeout_then_progress if expect_calls[0] == 0 else prompt_match
            expect_calls[0] += 1
            return callback(*args, **kwargs)

        process.expect.side_effect = expect_side_effect
        with patch("lftp.lftp.time.monotonic", side_effect=[0.0, 0.1, 0.2]), \
                patch("lftp.lftp.time.sleep"):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        jobs_read = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "jobs_read"
        )
        details = jobs_read["details"]
        metrics = details["post_send_metrics"]
        self.assertEqual(2, metrics["post_send_zero_timeout_expect_iterations"])
        self.assertEqual(1, metrics["post_send_productive_count"])
        self.assertEqual(0, metrics["post_send_no_progress_count"])
        self.assertEqual(1, metrics["post_send_unknown_count"])
        self.assertEqual(200_000_000, metrics["post_send_loop_elapsed_ns"])
        self.assertEqual("unknown", metrics["post_send_buffer_progress"])

    def test_post_send_invalid_or_decreasing_structure_is_unknown(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        process = lftp._Lftp__process
        process.before = b"abc"
        process._buffer = io.StringIO("xy")

        def decreasing_timeout(*_args, **_kwargs):
            process.before = b"a"
            process._buffer = io.StringIO()
            raise pexpect.exceptions.TIMEOUT("decrease")

        process.expect.side_effect = decreasing_timeout
        with patch("lftp.lftp.time.monotonic", side_effect=[0.0, 0.1, 1.1]), \
                patch("lftp.lftp.time.sleep") as sleep:
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        sleep.assert_called_once_with(0.01)

        jobs_read = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "jobs_read"
        )
        details = jobs_read["details"]
        metrics = details["post_send_metrics"]
        self.assertEqual(2, metrics["post_send_zero_timeout_expect_iterations"])
        self.assertEqual(0, metrics["post_send_productive_count"])
        self.assertEqual(1, metrics["post_send_no_progress_count"])
        self.assertEqual(1, metrics["post_send_unknown_count"])
        self.assertEqual(1_100_000_000, metrics["post_send_loop_elapsed_ns"])
        self.assertEqual("unknown", metrics["post_send_buffer_progress"])

    def test_post_send_metrics_are_disabled_without_debug_status_trace(self):
        lftp = self._build_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=32,
            policy={"default": "off"},
        )
        lftp.set_breadcrumb_trace(trace)
        process = lftp._Lftp__process
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("quiet")

        with patch(
                "lftp.lftp._lftp_status_structural_lengths",
                side_effect=[(0, 0), (1, 1), (1, 1), (2, 2)],
        ) as lengths, \
                patch("lftp.lftp.time.monotonic_ns") as monotonic_ns, \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 0.1, 1.1]), \
                patch("lftp.lftp.time.sleep") as sleep:
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        self.assertEqual(4, lengths.call_count)
        monotonic_ns.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual([], trace.snapshot()["entries"])

    def test_status_realistic_pty_quiet_partial_and_eof_are_bounded_without_debug(self):
        cases = (
            ("quiet", 0, [0.0, 0.1, 1.1], 4, 1, "timeout"),
            ("partial", 47_000, [0.0, 0.1, 0.2, 1.1], 6, 1, "timeout"),
            ("eof", 47_000, [0.0], 1, 0, "eof"),
        )
        for mode, payload_size, clock, structural_checks, sleep_count, failure_reason in cases:
            with self.subTest(mode=mode):
                lftp = self._build_lftp()
                trace = BreadcrumbTraceCollector(
                    lambda: True,
                    max_entries=32,
                    policy={"default": "off"},
                )
                lftp.set_breadcrumb_trace(trace)
                process = _RealisticStatusPty(mode, payload_size)
                lftp._Lftp__process = process

                with patch(
                        "lftp.lftp._lftp_status_structural_lengths",
                        wraps=lftp_mod._lftp_status_structural_lengths,
                ) as structural, \
                        patch("lftp.lftp.time.monotonic", side_effect=clock), \
                        patch("lftp.lftp.time.sleep") as sleep:
                    self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

                self.assertEqual(structural_checks, structural.call_count)
                self.assertEqual(sleep_count, sleep.call_count)
                self.assertEqual([], trace.snapshot()["entries"])
                self.assertEqual(failure_reason, lftp.last_status_poll_failure_reason)
                if mode == "partial":
                    self.assertEqual(payload_size, len(process.before))
                self.assertEqual(["jobs -v\n"], process.send_calls)

    def test_post_send_metrics_serialize_to_trace_and_timeout_frame_manifest(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        process = lftp._Lftp__process

        def timeout_with_frame(*_args, **_kwargs):
            process.before = b"raw timeout frame"
            process._buffer = io.StringIO()
            raise pexpect.exceptions.TIMEOUT("timeout")

        process.expect.side_effect = timeout_with_frame
        with tempfile.TemporaryDirectory() as capture_dir:
            prior_remaining = lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING
            lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = 1
            try:
                with patch.dict(
                        os.environ,
                        {"SEEDSYNC_LFTP_PRIVATE_STATUS_FRAME_CAPTURE_DIR": capture_dir},
                ), patch("lftp.lftp.time.monotonic", side_effect=[0.0, 1.1]), \
                        patch("lftp.lftp.time.sleep"):
                    self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))
                capture_dirs = [
                    name for name in os.listdir(capture_dir)
                    if name.startswith("lftp-status-")
                ]
                self.assertEqual(1, len(capture_dirs))
                with open(
                        os.path.join(capture_dir, capture_dirs[0], "manifest.json"),
                        encoding="utf-8",
                ) as handle:
                    manifest = json.load(handle)
            finally:
                lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = prior_remaining

        jobs_read = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "jobs_read"
        )
        details = jobs_read["details"]
        metrics = details["post_send_metrics"]
        self.assertEqual(1, metrics["post_send_zero_timeout_expect_iterations"])
        self.assertEqual(1, metrics["post_send_productive_count"])
        self.assertEqual(0, metrics["post_send_no_progress_count"])
        self.assertEqual(0, metrics["post_send_unknown_count"])
        self.assertEqual(0, metrics["post_send_sleep_requested_ns"])
        self.assertEqual(1_100_000_000, metrics["post_send_loop_elapsed_ns"])
        self.assertEqual("progress", metrics["post_send_buffer_progress"])
        self.assertEqual(1, manifest["boundary"]["post_send_zero_timeout_expect_iterations"])
        self.assertEqual(1, manifest["boundary"]["post_send_productive_count"])
        self.assertEqual(0, manifest["boundary"]["post_send_no_progress_count"])
        self.assertEqual(0, manifest["boundary"]["post_send_unknown_count"])
        self.assertEqual(0, manifest["boundary"]["post_send_sleep_requested_ns"])
        self.assertEqual(1_100_000_000, manifest["boundary"]["post_send_loop_elapsed_ns"])
        self.assertEqual("progress", manifest["boundary"]["post_send_buffer_progress"])

    def test_status_structural_lengths_use_bytesio_buffer_without_copy(self):
        class NoCopyBytesIO(io.BytesIO):
            def getvalue(self):
                raise AssertionError("structural helper must not copy payload")

        class Process:
            before = b"before"

            def __init__(self):
                self._buffer = NoCopyBytesIO(b"retained")

        self.assertEqual((6, 8), lftp_mod._lftp_status_structural_lengths(Process()))

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

    def test_timeout_status_frame_is_captured_before_nonempty_output_is_cleared(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.before = "opaque-timeout-frame"
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture") as capture, \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        capture.assert_called_once()
        self.assertEqual("lftp-poll:0123456789abcdef", capture.call_args.args[0])
        self.assertEqual("opaque-timeout-frame", capture.call_args.args[1])
        self.assertTrue(capture.call_args.args[2])
        self.assertIs(capture.call_args.args[3], trace)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("timeout", lftp.last_status_poll_failure_reason)

    def test_timeout_status_frame_capture_failure_preserves_empty_unhealthy_result(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.before = "opaque-timeout-frame"
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch(
                "lftp.lftp._lftp_private_status_frame_capture",
                side_effect=RuntimeError("capture failure"),
        ), patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("timeout", lftp.last_status_poll_failure_reason)
        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_correlation)

    def test_captured_timeout_predecessor_is_consumed_once_before_following_status_send(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.before = "synthetic-late-tail"
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture", return_value=True), \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        self.assertEqual("lftp-poll:0123456789abcdef", lftp._Lftp__status_timeout_predecessor_correlation)
        self.assertIsInstance(lftp._Lftp__status_timeout_predecessor_monotonic_time_ns, int)

        lftp._Lftp__process.expect.side_effect = None
        lftp._Lftp__process.expect.return_value = None
        with patch("lftp.lftp._lftp_private_status_relation_capture", return_value="captured") as relation:
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543211"))

        relation.assert_called_once()
        self.assertEqual("lftp-poll:0123456789abcdef", relation.call_args.args[0])
        self.assertEqual("lftp-poll:fedcba9876543210", relation.call_args.args[1])
        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_correlation)
        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_monotonic_time_ns)

    def test_retained_only_drain_reports_no_new_pty_bytes(self):
        lftp = self._build_lftp()
        process = lftp._Lftp__process
        process.before = "synthetic-timeout-frame"
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture", return_value=True), \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        process.expect.side_effect = None
        process.expect.return_value = None
        process.before = ""
        process._buffer = io.StringIO("synthetic-late-tail\n[0] Done (queue (\n")
        process._before = io.StringIO("synthetic-late-tail\n[0] Done (queue (\n")
        with patch("lftp.lftp._lftp_private_status_relation_capture", return_value="captured") as relation:
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543211"))

        relation.assert_called_once()
        observation = relation.call_args.args[3]
        self.assertEqual("queue_done", observation["_pre_send_drain_class"])
        self.assertGreater(observation["_pre_send_drain_bytes"], 0)
        self.assertGreater(observation["_pre_send_drain_lines"], 0)
        self.assertEqual(0, observation["_pre_send_drain_fresh_bytes"])
        self.assertEqual(0, observation["_pre_send_drain_fresh_lines"])
        self.assertFalse(observation["_pre_send_drain_fresh_queue_done"])
        self.assertFalse(observation["_pre_send_drain_fresh_job_or_progress"])
        self.assertFalse(observation["_pre_send_drain_fresh_prompt_or_echo"])
        self.assertFalse(observation["_pre_send_drain_fresh_error"])
        self.assertEqual("lftp-poll:0123456789abcdef", relation.call_args.args[0])
        self.assertEqual("lftp-poll:fedcba9876543210", relation.call_args.args[1])
        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_correlation)

    def test_retained_and_late_tail_distinguish_fresh_pty_marker(self):
        lftp = self._build_lftp()
        process = lftp._Lftp__process
        process.before = "synthetic-timeout-frame"
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture", return_value=True), \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        process.expect.side_effect = None
        process.expect.return_value = None
        process.before = ""
        process._buffer = io.StringIO("[0] Done (queue (\n")
        process._before = io.StringIO("[0] Done (queue (\n")
        process.read_nonblocking.side_effect = [
            "[1] mirror -- 1/2 (50%)\n",
            pexpect.exceptions.TIMEOUT("empty terminal"),
        ]
        with patch("lftp.lftp._lftp_private_status_relation_capture", return_value="captured") as relation:
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))

        relation.assert_called_once()
        observation = relation.call_args.args[3]
        self.assertGreater(observation["_pre_send_drain_fresh_bytes"], 0)
        self.assertGreater(observation["_pre_send_drain_fresh_lines"], 0)
        self.assertFalse(observation["_pre_send_drain_fresh_queue_done"])
        self.assertTrue(observation["_pre_send_drain_fresh_job_or_progress"])
        self.assertFalse(observation["_pre_send_drain_fresh_prompt_or_echo"])
        self.assertFalse(observation["_pre_send_drain_fresh_error"])
        self.assertEqual("lftp-poll:0123456789abcdef", relation.call_args.args[0])
        self.assertEqual("lftp-poll:fedcba9876543210", relation.call_args.args[1])
        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_correlation)

    def test_fresh_command_echo_is_separate_from_prompt_match(self):
        lftp = self._build_lftp()
        process = lftp._Lftp__process
        process.before = "synthetic-timeout-frame"
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture", return_value=True), \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        process.expect.side_effect = None
        process.expect.return_value = None
        process.before = ""
        process._buffer = io.StringIO()
        process._before = io.StringIO()
        process.read_nonblocking.side_effect = [
            "jobs -v\n", pexpect.exceptions.TIMEOUT("empty terminal"),
        ]
        with patch("lftp.lftp._lftp_private_status_relation_capture", return_value="captured") as relation:
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))

        observation = relation.call_args.args[3]
        self.assertEqual("quiet_timeout", observation["_pre_send_drain_termination"])
        self.assertTrue(observation["_pre_send_drain_fresh_command_echo"])
        self.assertFalse(observation["_pre_send_drain_fresh_prompt_match"])
        self.assertTrue(observation["_pre_send_drain_fresh_prompt_or_echo"])

    def test_empty_drain_read_has_distinct_termination(self):
        lftp = self._build_lftp()
        process = lftp._Lftp__process
        process.before = "synthetic-timeout-frame"
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture", return_value=True), \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        process.expect.side_effect = None
        process.expect.return_value = None
        process.before = ""
        process._buffer = io.StringIO()
        process._before = io.StringIO()
        process.read_nonblocking.side_effect = ["", pexpect.exceptions.TIMEOUT("empty terminal")]
        with patch("lftp.lftp._lftp_private_status_relation_capture", return_value="captured") as relation:
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))

        observation = relation.call_args.args[3]
        self.assertEqual("empty_read", observation["_pre_send_drain_termination"])
        self.assertFalse(observation["_pre_send_drain_fresh_command_echo"])
        self.assertFalse(observation["_pre_send_drain_fresh_prompt_match"])
        self.assertFalse(observation["_pre_send_drain_fresh_prompt_or_echo"])

    def test_fresh_prompt_match_is_separate_from_command_echo(self):
        lftp = self._build_lftp()
        process = lftp._Lftp__process
        process.before = "synthetic-timeout-frame"
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture", return_value=True), \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        process.expect.side_effect = None
        process.expect.return_value = None
        process.before = ""
        process._buffer = io.StringIO()
        process._before = io.StringIO()
        process.read_nonblocking.side_effect = [
            "prompt>", pexpect.exceptions.TIMEOUT("empty terminal"),
        ]
        with patch("lftp.lftp._lftp_private_status_relation_capture", return_value="captured") as relation:
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))

        observation = relation.call_args.args[3]
        self.assertEqual("quiet_timeout", observation["_pre_send_drain_termination"])
        self.assertFalse(observation["_pre_send_drain_fresh_command_echo"])
        self.assertTrue(observation["_pre_send_drain_fresh_prompt_match"])
        self.assertTrue(observation["_pre_send_drain_fresh_prompt_or_echo"])

    def test_disabled_capture_path_does_not_create_predecessor_or_relation(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.before = "synthetic-late-tail"
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=32,
            policy={"default": "info"},
        )
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture") as capture, \
                patch("lftp.lftp._lftp_private_status_relation_capture") as relation, \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))
        lftp._Lftp__process.expect.side_effect = None
        lftp._Lftp__process.expect.return_value = None
        with patch("lftp.lftp._lftp_private_status_relation_capture") as relation:
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))

        capture.assert_not_called()
        relation.assert_not_called()
        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_correlation)

    def test_unrelated_command_clears_timeout_predecessor_before_status(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.before = "synthetic-late-tail"
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture", return_value=True), \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        lftp._Lftp__process.expect.side_effect = None
        lftp._Lftp__process.expect.return_value = None
        lftp._Lftp__run_command("set cmd:queue-parallel 1", require_prompt_ready=False)
        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_correlation)
        with patch("lftp.lftp._lftp_private_status_relation_capture") as relation:
            self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))
        relation.assert_not_called()

    def test_force_close_clears_timeout_predecessor(self):
        lftp = self._build_lftp()
        lftp._Lftp__status_timeout_predecessor_correlation = "lftp-poll:0123456789abcdef"
        lftp._Lftp__status_timeout_predecessor_monotonic_time_ns = 123

        lftp.force_close()

        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_correlation)
        self.assertIsNone(lftp._Lftp__status_timeout_predecessor_monotonic_time_ns)

    def test_late_tail_relation_manifest_is_structural_and_quota_bounded(self):
        trace = self._trace()
        with tempfile.TemporaryDirectory() as capture_dir:
            prior_remaining = lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING
            lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = 2
            try:
                with patch.dict(
                        os.environ,
                        {"SEEDSYNC_LFTP_PRIVATE_STATUS_FRAME_CAPTURE_DIR": capture_dir},
                ):
                    result = lftp_mod._lftp_private_status_relation_capture(
                        "lftp-poll:0123456789abcdef",
                        "lftp-poll:fedcba9876543210",
                        1,
                        {
                            "_pre_send_drain_class": "mixed",
                            "_pre_send_drain_termination": "quiet_timeout",
                            "_pre_send_drain_bytes": 17,
                            "_pre_send_drain_lines": 2,
                            "_pre_send_drain_fresh_command_echo": True,
                            "_pre_send_drain_fresh_prompt_match": False,
                            "_pre_send_drain_fresh_prompt_or_echo": True,
                        },
                        True,
                        trace,
                    )
                self.assertEqual("captured", result)
                relation_dirs = [
                    name for name in os.listdir(capture_dir)
                    if name.startswith("lftp-status-relation-")
                ]
                self.assertEqual(1, len(relation_dirs))
                with open(os.path.join(capture_dir, relation_dirs[0], "manifest.json"), encoding="utf-8") as handle:
                    manifest = json.load(handle)
            finally:
                lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = prior_remaining

        self.assertEqual("seedsync.lftp.private-status-relation.v1", manifest["schema"])
        self.assertEqual("lftp-poll:0123456789abcdef", manifest["predecessor_correlation"])
        self.assertEqual("lftp-poll:fedcba9876543210", manifest["current_correlation"])
        self.assertEqual("mixed", manifest["drain_class"])
        self.assertEqual("quiet_timeout", manifest["drain_termination"])
        self.assertEqual("1-127", manifest["drain_byte_length_bucket"])
        self.assertTrue(manifest["fresh_read_command_echo"])
        self.assertFalse(manifest["fresh_read_prompt_match"])
        self.assertTrue(manifest["fresh_read_prompt_or_echo"])
        self.assertEqual("available", manifest["capture_quota_state"])
        self.assertEqual(2, manifest["capture_quota_remaining_before"])
        self.assertEqual(1, manifest["capture_quota_remaining_after"])
        self.assertNotIn("synthetic", repr(manifest))

    def test_relation_capture_reports_unavailable_or_exhausted_without_claiming_persistence(self):
        trace = self._trace()
        with tempfile.TemporaryDirectory() as capture_dir:
            prior_remaining = lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING
            lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = 0
            try:
                with patch.dict(
                        os.environ,
                        {"SEEDSYNC_LFTP_PRIVATE_STATUS_FRAME_CAPTURE_DIR": capture_dir},
                ):
                    self.assertEqual(
                        "exhausted",
                        lftp_mod._lftp_private_status_relation_capture(
                            "lftp-poll:0123456789abcdef",
                            "lftp-poll:fedcba9876543210",
                            1,
                            {},
                            True,
                            trace,
                        ),
                    )
                self.assertEqual([], os.listdir(capture_dir))
            finally:
                lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = prior_remaining

            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    "unavailable",
                    lftp_mod._lftp_private_status_relation_capture(
                        "lftp-poll:0123456789abcdef",
                        "lftp-poll:fedcba9876543210",
                        1,
                        {},
                        True,
                        trace,
                    ),
                )
            self.assertEqual([], os.listdir(capture_dir))

    def test_private_status_frame_is_not_captured_when_timeout_clear_branch_is_ineligible(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.before = "opaque-prompt-result"
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture") as capture:
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        capture.assert_not_called()
        self.assertTrue(lftp.last_status_poll_healthy)

    def test_timeout_status_frame_is_default_off_without_debug_status_policy(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.before = "opaque-timeout-frame"
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=32,
            policy={"default": "info"},
        )
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture") as capture, \
                patch("lftp.lftp.time.monotonic", side_effect=[0.0, 2.0]):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        capture.assert_not_called()
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("timeout", lftp.last_status_poll_failure_reason)

    def test_command_timeout_before_parser_is_not_attempted(self):
        lftp = self._build_lftp()
        lftp._Lftp__run_command = MagicMock(
            side_effect=pexpect.exceptions.TIMEOUT("timeout"),
        )
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))
        self.assertEqual("not_attempted", lftp.last_status_parse_outcome)

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
        lftp._Lftp__job_status_parser.last_parse_outcome = "hard_error"
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)

        self.assertIsNone(lftp.status("lftp-poll:0123456789abcdef"))

        parse_error = next(
            event for event in trace.snapshot()["entries"]
            if event["details"]["phase"] == "parse_error"
        )
        self.assertEqual("error", parse_error["details"]["prompt_outcome"])
        self.assertEqual("parser_error", parse_error["details"]["error_class"])
        self.assertEqual("hard_error", lftp.last_status_parse_outcome)
        self.assertNotIn("private detail", repr(trace.snapshot()))


if __name__ == "__main__":
    unittest.main()
