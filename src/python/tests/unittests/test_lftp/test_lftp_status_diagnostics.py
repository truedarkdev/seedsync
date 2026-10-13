import json
import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pexpect

import lftp.lftp as lftp_mod
from common.breadcrumb_trace import BreadcrumbTraceCollector
from lftp import Lftp, LftpJobStatus, LftpJobStatusParser, LftpJobStatusParserError


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
    def test_mirror_terminal_breadcrumb_requires_complete_mirror_and_final_counts(self):
        trace = self._trace()
        poll = "lftp-poll:0123456789abcdef"
        complete = (
            "[7] Done (mirror -c /private/remote /private/local)\n"
            "Total: 1 directory, 2 files, 0 symlinks\n"
        )
        lftp_mod._record_lftp_mirror_terminal_evidence(
            trace, poll, complete, source="post_command", complete=True,
        )
        detail = trace.snapshot()["entries"][-1]["details"]
        mirror_status = LftpJobStatus(
            job_id=7, job_type=LftpJobStatus.Type.MIRROR,
            state=LftpJobStatus.State.RUNNING, name="private-name", flags="-c",
        )
        self.assertEqual((7, "mirror", "success", 0),
                         (detail["jobno"], detail["job_type"], detail["result"], detail["error_count"]))
        self.assertEqual(mirror_status.job_correlation, detail["job_correlation"])
        self.assertNotIn("/private", repr(detail))

        lftp_mod._record_lftp_mirror_terminal_evidence(
            trace, poll, "[0] Done (queue (private))\n", source="post_command", complete=True,
        )
        lftp_mod._record_lftp_mirror_terminal_evidence(
            trace, poll, "[6] Done (get /private/remote /private/local)\n",
            source="post_command", complete=True,
        )
        self.assertEqual(1, len(trace.snapshot()["entries"]))
        lftp_mod._record_lftp_mirror_terminal_evidence(
            trace, poll, "[8] Done (mirror -c private", source="pre_send_drain", complete=False,
        )
        partial = trace.snapshot()["entries"][-1]["details"]
        self.assertEqual("unknown", partial["result"])
        self.assertFalse(partial["final_status_complete"])

        lftp_mod._record_lftp_mirror_terminal_evidence(
            trace, poll,
            "[10] Done (mirror -c private)\nTotal: 1 directory, 2 files, 0 symlinks\n" + "x" * 17000,
            source="post_command", complete=True,
        )
        truncated = trace.snapshot()["entries"][-1]["details"]
        self.assertEqual("unknown", truncated["result"])
        self.assertFalse(truncated["final_status_complete"])

        lftp_mod._record_lftp_mirror_terminal_evidence(
            trace, poll, "x" * lftp_mod._LFTP_MIRROR_TERMINAL_MAX_BYTES +
            "\n[11] Done (mirror -c private)\nTotal: 1 directory, 2 files, 0 symlinks\n",
            source="post_command", complete=True,
        )
        lost = trace.snapshot()["entries"][-1]["details"]
        self.assertTrue(lost["evidence_lost"])
        self.assertEqual("unknown", lost["result"])
        self.assertFalse(lost["job_id_known"])

        lftp_mod._record_lftp_mirror_terminal_evidence(
            trace, poll,
            "[9] Done (mirror -c private)\nTotal: 1 directory, 2 files, 0 symlinks\n"
            "1 error detected\n",
            source="post_command", complete=True,
        )
        error = trace.snapshot()["entries"][-1]["details"]
        self.assertEqual(("error", 1), (error["result"], error["error_count"]))

    def test_pre_send_drain_callsite_emits_mirror_only_terminal_event(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__process._buffer = io.StringIO(
            "[0] Done (queue (private))\n"
            "[4] Done (mirror -c /private/remote /private/local)\n"
            "Total: 1 directory, 2 files, 0 symlinks\n"
        )
        poll = "lftp-poll:0123456789abcdef"

        _, lines = self._capture_docker_lines(
            lambda: lftp._Lftp__drain_status_poll_terminal(True, poll),
        )
        self.assertEqual(1, len(lines))
        self.assertIn("source=pre_send_drain", lines[0])
        self.assertIn("event=mirror_terminal", lines[0])
        self.assertIn("result=unknown", lines[0])
        self.assertLessEqual(len(lines[0].encode("ascii")), 320)
        self.assertNotIn("private", lines[0])

        events = trace.snapshot()["entries"]
        self.assertEqual(1, len(events))
        detail = events[0]["details"]
        self.assertIn("job_corr={}".format(detail["job_correlation"]), lines[0])
        self.assertEqual(("pre_send_drain", 4, "unknown"),
                         (detail["source"], detail["jobno"], detail["result"]))
        expected = LftpJobStatus(
            job_id=4, job_type=LftpJobStatus.Type.MIRROR,
            state=LftpJobStatus.State.RUNNING, name="private-name", flags="-c",
        )
        self.assertEqual(expected.job_correlation, detail["job_correlation"])
        self.assertFalse(detail["final_status_complete"])
        self.assertNotIn("private", repr(events[0]))

    def test_pre_send_drain_without_debug_skips_new_evidence_buffer_copy(self):
        class TrackedText(str):
            prefix_slices = 0

            def __getitem__(self, key):
                if isinstance(key, slice) and key.start in (None, 0) and key.stop is not None:
                    type(self).prefix_slices += 1
                return super().__getitem__(key)

        lftp = self._build_lftp()
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8, policy={"default": "off"})
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__process._buffer = io.StringIO(
            "[4] Done (mirror -c private)\nTotal: 1 directory, 2 files, 0 symlinks\n"
        )
        lftp._Lftp__decode_spawn_output = lambda value: TrackedText(value)
        TrackedText.prefix_slices = 0

        with patch("lftp.lftp._record_lftp_mirror_terminal_evidence") as record:
            lftp._Lftp__drain_status_poll_terminal(True, "lftp-poll:0123456789abcdef")

        record.assert_not_called()
        self.assertEqual(0, TrackedText.prefix_slices)
        self.assertGreater(lftp._Lftp__last_incoming_recovery_status_observation[
            "_pre_send_drain_bytes"
        ], 0)

    def test_post_command_callsite_emits_mirror_only_terminal_event(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__run_command = MagicMock(return_value=(
            "[0] Done (queue (private))\n"
            "[5] Done (mirror -c /private/remote /private/local)\n"
            "Total: 1 directory, 2 files, 0 symlinks\n"
        ))

        _, lines = self._capture_docker_lines(
            lambda: lftp.status("lftp-poll:0123456789abcdef"),
        )
        self.assertEqual(2, len(lines))
        mirror_line = next(line for line in lines if "event=mirror_terminal" in line)
        self.assertIn("source=post_command", mirror_line)
        self.assertIn("result=success", mirror_line)
        self.assertNotIn("private", repr(lines))

        events = [event for event in trace.snapshot()["entries"]
                  if event["stage"] == "lftp_mirror_terminal"]
        self.assertEqual(1, len(events))
        detail = events[0]["details"]
        self.assertIn("job_corr={}".format(detail["job_correlation"]), mirror_line)
        self.assertEqual(("post_command", 5, "success"),
                         (detail["source"], detail["jobno"], detail["result"]))
        expected = LftpJobStatus(
            job_id=5, job_type=LftpJobStatus.Type.MIRROR,
            state=LftpJobStatus.State.RUNNING, name="private-name", flags="-c",
        )
        self.assertEqual(expected.job_correlation, detail["job_correlation"])
        self.assertNotIn("private", repr(events[0]))

    def test_partial_post_command_terminal_line_is_unknown_in_docker_log(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__job_status_parser.parse.return_value = []
        lftp._Lftp__run_command = MagicMock(return_value=(
            "[13] Done (mirror -c /private/remote"
        ))

        _, lines = self._capture_docker_lines(
            lambda: lftp.status("lftp-poll:0123456789abcdef"),
        )
        partial = next(line for line in lines if "event=mirror_terminal" in line)
        self.assertIn("result=unknown", partial)
        self.assertIn("complete=0", partial)
        self.assertNotIn("private", repr(lines))

    def test_empty_status_observation_joins_prior_job_and_reports_bounded_no_match(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        running = LftpJobStatus(
            job_id=7, job_type=LftpJobStatus.Type.MIRROR,
            state=LftpJobStatus.State.RUNNING, name="private-name", flags="-c",
        )
        lftp._Lftp__job_status_parser.parse.side_effect = [[running], [], []]
        lftp._Lftp__run_command = MagicMock(side_effect=[
            "[7] mirror -c /private/remote /private/local\n",
            "[0] Done (queue (private))\n",
            "x" * (lftp_mod._LFTP_MIRROR_TERMINAL_MAX_BYTES + 1)
            + "[9] Done (mirror -c /private/remote /private/local)\n",
        ])

        self.assertEqual([running], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))
        _, no_match_lines = self._capture_docker_lines(
            lambda: lftp.status(trace_poll_correlation="lftp-poll:1123456789abcdef"),
        )
        _, truncated_lines = self._capture_docker_lines(
            lambda: lftp.status(trace_poll_correlation="lftp-poll:2123456789abcdef"),
        )
        self.assertEqual(1, len(no_match_lines))
        self.assertIn("event=empty_observation", no_match_lines[0])
        self.assertIn("terminal_seen=no", no_match_lines[0])
        self.assertEqual(2, len(truncated_lines))
        self.assertTrue(any("event=evidence_lost" in line and "truncated=1" in line
                            for line in truncated_lines))
        self.assertTrue(all(len(line.encode("ascii")) <= 320 for line in truncated_lines))
        self.assertNotIn("private", repr(truncated_lines))

        observations = [event for event in trace.snapshot()["entries"]
                        if event["stage"] == "lftp_empty_status_terminal_observation"]
        self.assertEqual(2, len(observations))
        ordinary = observations[0]["details"]
        self.assertEqual("complete_success", ordinary["prior_parse_outcome"])
        self.assertEqual("nonempty", ordinary["prior_parsed_job_set"])
        self.assertEqual("no", ordinary["terminal_mirror_seen"])
        self.assertTrue(ordinary["scan_complete"])
        self.assertTrue(ordinary["status_healthy"])
        truncated = observations[1]["details"]
        self.assertEqual("unknown", truncated["terminal_mirror_seen"])
        self.assertTrue(truncated["scan_truncated"])
        self.assertFalse(truncated["scan_complete"])
        self.assertNotIn("private", repr(observations))

    def test_idle_empty_status_without_prior_job_or_candidate_is_silent(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__job_status_parser.parse.return_value = []
        lftp._Lftp__run_command = MagicMock(return_value="")
        recorder = lambda *_args, **_kwargs: None

        self.assertEqual([], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))

        self.assertFalse(any(
            event["stage"] == "lftp_empty_status_terminal_observation"
            for event in trace.snapshot()["entries"]
        ))

    def test_post_command_error_terminal_and_default_off_gate(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__job_status_parser.parse.return_value = []
        lftp._Lftp__run_command = MagicMock(return_value=(
            "[12] Done (mirror -c /private/remote /private/local)\n"
            "Total: 1 directory, 0 files, 0 symlinks\n1 error detected\n"
        ))
        recorder = lambda *_args, **_kwargs: None

        _, lines = self._capture_docker_lines(
            lambda: lftp.status("lftp-poll:0123456789abcdef", recorder),
        )
        self.assertEqual(2, len(lines))
        self.assertTrue(any("result=error" in line for line in lines))

        events = trace.snapshot()["entries"]
        terminal = next(event for event in events
                        if event["stage"] == "lftp_mirror_terminal")
        observation = next(event for event in events
                           if event["stage"] == "lftp_empty_status_terminal_observation")
        self.assertEqual("error", terminal["details"]["result"])
        self.assertEqual("yes", observation["details"]["terminal_mirror_seen"])
        self.assertNotIn("private", repr((terminal, observation)))

        disabled = self._build_lftp()
        disabled_trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8, policy={"default": "off"},
        )
        disabled.set_breadcrumb_trace(disabled_trace)
        disabled._Lftp__last_incoming_recovery_status_observation = {
            "status_result_shape": "job_present",
        }
        disabled._Lftp__job_status_parser.parse.return_value = []
        disabled._Lftp__run_command = MagicMock(return_value="")
        _, disabled_lines = self._capture_docker_lines(
            lambda: disabled.status("lftp-poll:1123456789abcdef", recorder),
        )
        self.assertEqual([], disabled_lines)
        self.assertEqual([], disabled.status("lftp-poll:2123456789abcdef", recorder))
        self.assertEqual([], disabled_trace.snapshot()["entries"])

    def test_empty_status_no_match_has_docker_log_line_on_active_to_empty_callsite(self):
        lftp = self._build_lftp()
        trace = self._trace()
        lftp.set_breadcrumb_trace(trace)
        running = LftpJobStatus(
            job_id=7, job_type=LftpJobStatus.Type.MIRROR,
            state=LftpJobStatus.State.RUNNING, name="private-name", flags="-c",
        )
        lftp._Lftp__job_status_parser.parse.side_effect = [[running], []]
        lftp._Lftp__run_command = MagicMock(side_effect=[
            "[7] mirror -c /private/remote /private/local\n",
            "[0] Done (queue (private))\n",
        ])
        lftp.status("lftp-poll:0123456789abcdef")
        trace.record = MagicMock(side_effect=OSError("diagnostic recorder unavailable"))

        _, lines = self._capture_docker_lines(
            lambda: lftp.status("lftp-poll:1123456789abcdef"),
        )
        self.assertEqual(1, len(lines))
        self.assertIn("event=empty_observation", lines[0])
        self.assertIn("terminal_seen=no", lines[0])
        self.assertIn("prior_jobs=nonempty", lines[0])
        self.assertIn("complete=1", lines[0])
        self.assertNotIn("private", lines[0])

    def test_terminal_docker_log_write_failures_are_isolated_and_trace_failure_is_independent(self):
        payload = (
            "[12] Done (mirror -c /private/remote /private/local)\n"
            "Total: 1 directory, 0 files, 0 symlinks\n1 error detected\n"
        )
        for failure in ("select", "write", "recorder"):
            with self.subTest(failure=failure):
                lftp = self._build_lftp()
                trace = self._trace()
                if failure == "recorder":
                    trace.record = MagicMock(side_effect=OSError("unavailable"))
                lftp.set_breadcrumb_trace(trace)
                lftp._Lftp__job_status_parser.parse.return_value = []
                lftp._Lftp__run_command = MagicMock(return_value=payload)
                writes = []
                with patch("lftp.lftp.sys.stderr", MagicMock(fileno=MagicMock(return_value=2))), \
                        patch("lftp.lftp.select.select",
                              side_effect=OSError("unavailable") if failure == "select" else
                              lambda _r, writable, _x, _timeout: ([], writable, [])), \
                        patch("lftp.lftp.os.write",
                              side_effect=OSError("unavailable") if failure == "write" else
                              lambda fd, data: writes.append((fd, data)) or len(data)):
                    self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))
                self.assertTrue(lftp._Lftp__job_status_parser.parse.called)
                if failure == "recorder":
                    self.assertEqual(1, len(writes))
                    self.assertIn(b"result=error", writes[0][1])
                else:
                    self.assertEqual([], writes)

    @staticmethod
    def _capture_docker_lines(call):
        writes = []
        with patch("lftp.lftp.sys.stderr", MagicMock(fileno=MagicMock(return_value=2))), \
                patch("lftp.lftp.select.select", return_value=([], [2], [])), \
                patch("lftp.lftp.os.write",
                      side_effect=lambda fd, data: writes.append((fd, data)) or len(data)):
            result = call()
        return result, [data.decode("ascii") for _fd, data in writes]

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

    @staticmethod
    def _info_trace(max_entries=32):
        return BreadcrumbTraceCollector(
            lambda: True,
            max_entries=max_entries,
            policy={"default": "info"},
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
            lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = 3
            try:
                with patch.dict(
                        os.environ,
                        {"SEEDSYNC_LFTP_PRIVATE_STATUS_FRAME_CAPTURE_DIR": capture_dir},
                ), patch.object(lftp_mod, "_PRIVATE_STATUS_FRAME_CAPTURE_ROLES", set()), \
                        patch.object(lftp_mod, "_PRIVATE_STATUS_FRAME_CAPTURE_PATHS", {}):
                    self.assertTrue(lftp_mod._lftp_private_status_frame_capture(
                        "lftp-poll:0123456789abcdef", "synthetic timeout tail", True, trace,
                        {"command_timed_out": True}, poll_healthy=False,
                    ))
                    anomaly_path = lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_PATHS["anomaly"]
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
                self.assertEqual(1, len(os.listdir(capture_dir)))
                self.assertEqual(2, lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING)
                with open(os.path.join(anomaly_path, "relation.json"), encoding="utf-8") as handle:
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
        self.assertEqual("reused_anomaly_slot", manifest["capture_quota_state"])
        self.assertEqual(2, manifest["capture_quota_remaining_before"])
        self.assertEqual(2, manifest["capture_quota_remaining_after"])
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

    def test_healthy_fresh_and_repeated_polls_emit_two_correlated_info_boundaries(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.before = (
            "sftp://private-user@private-host/private/path\nprivate-file status"
        )
        trace = self._info_trace()
        lftp.set_breadcrumb_trace(trace)

        self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))
        self.assertEqual([], lftp.status("lftp-poll:fedcba9876543210"))

        entries = trace.snapshot()["entries"]
        self.assertEqual(
            ["jobs_read", "parse_complete", "jobs_read", "parse_complete"],
            [event["details"]["phase"] for event in entries],
        )
        for poll_id, pair in zip(
                ("lftp-poll:0123456789abcdef", "lftp-poll:fedcba9876543210"),
                (entries[:2], entries[2:]),
        ):
            self.assertEqual(["info", "info"], [event["level"] for event in pair])
            self.assertEqual([poll_id, poll_id], [event["corr_id"] for event in pair])
            self.assertEqual([poll_id, poll_id], [event["flow_id"] for event in pair])
        rendered = repr(entries)
        self.assertNotIn("private-user", rendered)
        self.assertNotIn("private-host", rendered)
        self.assertNotIn("private/path", rendered)
        self.assertNotIn("private-file", rendered)

    def test_connection_grace_emits_at_most_four_info_rows_for_one_poll(self):
        lftp = self._build_lftp()
        lftp._Lftp__status_poll_needs_connection_grace = True
        trace = self._info_trace()
        lftp.set_breadcrumb_trace(trace)
        process = lftp._Lftp__process
        expect_calls = []

        def set_output_for_each_read(*_args, **_kwargs):
            expect_calls.append(None)
            process.before = "" if len(expect_calls) == 1 else "status payload"

        process.expect.side_effect = set_output_for_each_read

        self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        entries = trace.snapshot()["entries"]
        self.assertEqual(
            ["jobs_read", "parse_complete", "jobs_read", "parse_complete"],
            [event["details"]["phase"] for event in entries],
        )
        self.assertEqual(4, len(entries))
        self.assertTrue(all(event["level"] == "info" for event in entries))
        self.assertEqual(
            ["lftp-poll:0123456789abcdef"] * 4,
            [event["corr_id"] for event in entries],
        )
        self.assertEqual(2, lftp._Lftp__job_status_parser.parse.call_count)

    def test_info_policy_keeps_failures_as_warnings_without_error_info(self):
        lftp = self._build_lftp()
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        trace = self._info_trace()
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp.time.monotonic", side_effect=[0.0, 1.01]), \
                patch("lftp.lftp.time.sleep"):
            self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        entries = trace.snapshot()["entries"]
        self.assertTrue(entries)
        self.assertTrue(all(event["level"] == "warning" for event in entries))
        self.assertIn("prompt_timeout", [event["details"]["phase"] for event in entries])
        self.assertNotIn("jobs_read", [event["details"]["phase"] for event in entries])
        self.assertNotIn("parse_complete", [event["details"]["phase"] for event in entries])

    def test_info_policy_disabled_capture_emits_no_status_boundaries(self):
        lftp = self._build_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: False,
            max_entries=32,
            policy={"default": "info"},
        )
        lftp.set_breadcrumb_trace(trace)

        self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))
        self.assertEqual([], trace.snapshot()["entries"])

    def test_info_boundary_eviction_remains_visible_through_collector_snapshot(self):
        lftp = self._build_lftp()
        trace = self._info_trace(max_entries=1)
        lftp.set_breadcrumb_trace(trace)

        self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))

        snapshot = trace.snapshot()
        self.assertEqual(1, len(snapshot["entries"]))
        self.assertEqual("parse_complete", snapshot["entries"][0]["details"]["phase"])
        self.assertGreaterEqual(snapshot["accounting"]["evicted_count"], 1)
        self.assertTrue(snapshot["gap_detected"])

    def test_info_poll_rows_and_loss_health_are_retrievable_after_spool_close(self):
        lftp = self._build_lftp()
        with tempfile.TemporaryDirectory() as spool_dir:
            trace = BreadcrumbTraceCollector(
                lambda: True,
                max_entries=1,
                policy={"default": "info"},
                durable_enabled=True,
                durable_path=spool_dir,
            )
            lftp.set_breadcrumb_trace(trace)
            try:
                self.assertEqual([], lftp.status("lftp-poll:0123456789abcdef"))
                flushed = trace.flush_durable(2.0)
                health = trace.close(timeout=2.0)
                trace = None

                with open(os.path.join(spool_dir, "breadcrumbs.jsonl"), encoding="utf-8") as handle:
                    records = [json.loads(line) for line in handle if line.strip()]
                events = [record for record in records if record.get("message") == "lftp_status_poll"]
                self.assertEqual(["jobs_read", "parse_complete"], [
                    event["details"]["phase"] for event in events
                ])
                self.assertTrue(all(event["level"] == "info" for event in events))
                self.assertTrue(all(
                    event["corr_id"] == "lftp-poll:0123456789abcdef" for event in events
                ))
                jobs_read, parse_complete = events
                self.assertTrue(jobs_read["details"]["healthy"])
                self.assertTrue(parse_complete["details"]["healthy"])
                self.assertTrue({
                    "prior_prompt", "send_admitted", "prompt_reached", "retained_before",
                }.issubset(jobs_read["details"]))
                self.assertLessEqual(len(jobs_read["details"]), 24)
                self.assertEqual(2, health["written"])
                self.assertEqual(0, health["lost"])
                self.assertEqual(0, health["unknown"])
                if os.name == "nt":
                    self.assertEqual("buffered_best_effort", health["durability"])
                    self.assertEqual("INCOMPLETE", health["state"])
                    self.assertFalse(health["closed"])
                else:
                    self.assertTrue(flushed)
                    self.assertTrue(health["health_publish_ok"])
                    with open(
                            os.path.join(spool_dir, "breadcrumbs.health.json"),
                            encoding="utf-8",
                    ) as handle:
                        persisted_health = json.load(handle)
                    self.assertTrue(persisted_health["closed"])
                    self.assertEqual(2, persisted_health["written"])
                    self.assertEqual(0, persisted_health["lost"])
            finally:
                if trace is not None:
                    trace.close(timeout=2.0)

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
