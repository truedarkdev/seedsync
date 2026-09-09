# Copyright 2017, Inderpreet Singh, All rights reserved.

import hashlib
import json
import logging
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import stat
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch
from io import BytesIO, StringIO

import pexpect
import pytest
from pexpect.expect import Expecter, searcher_re

from tests.utils import TestUtils, requires_live_ssh
from common import ConfigError
from common.breadcrumb_trace import BreadcrumbTraceCollector
from common.exclude_patterns import ExactPathExclusion
from common.lftp_status import MAX_LFTP_PGET_STATUS_BYTES, parse_lftp_pget_status_bytes
from lftp import Lftp, LftpJobStatus, LftpError, LftpJobStatusParser, LftpJobStatusParserError
from lftp.lftp import _lftp_diagnostic_exception_family
import lftp.lftp as lftp_mod


# noinspection PyPep8Naming,SpellCheckingInspection
pytestmark = pytest.mark.timeout(5)

class TestLftp(unittest.TestCase):
    temp_dir = None

    @classmethod
    def setUpClass(cls):
        if os.name == "nt":
            return
        # Create a temp directory
        TestLftp.temp_dir = tempfile.mkdtemp(prefix="test_lftp_")
        print(f"Temp dir: {TestLftp.temp_dir}")

        # Allow group access for the seedsynctest account
        TestUtils.chmod_from_to(TestLftp.temp_dir, tempfile.gettempdir(), 0o775)

        # Create some test directories
        # remote [dir] for remote path
        #   a [dir]
        #     aa [file,       24*1024 bytes]
        #     ab [file,  2*1024*1024 bytes]
        #   b [dir]
        #     ba [dir]
        #       baa [file, 128*1024 bytes]
        #       bab [file, 128*1024 bytes]
        #     bb [file, 128*1024 bytes]
        #   c [file, 1234 bytes]
        #   "d d" [file, 128*1024 bytes]
        #   "e e" [dir]
        #     "e e a" [file, 128*1024 bytes]
        #   áßç [dir]
        #     dőÀ [file, 128*1024 bytes]
        #   üæÒ [file, 256*1024 bytes]
        # local [dir] for local path, cleared before every test

        def my_mkdir(*args):
            os.mkdir(os.path.join(TestLftp.temp_dir, *args))

        def my_touch(size, *args):
            path = os.path.join(TestLftp.temp_dir, *args)
            with open(path, 'wb') as f:
                f.write(bytearray([0xff]*size))

        def my_mkdir_latin(*args):
            if os.name == "nt":
                path = os.path.join(
                    TestLftp.temp_dir,
                    *(arg.decode("latin-1") if isinstance(arg, (bytes, bytearray)) else arg for arg in args)
                )
            else:
                path = os.path.join(os.fsencode(TestLftp.temp_dir), *args)
            os.mkdir(path)

        def my_touch_latin(size, *args):
            if os.name == "nt":
                path = os.path.join(
                    TestLftp.temp_dir,
                    *(arg.decode("latin-1") if isinstance(arg, (bytes, bytearray)) else arg for arg in args)
                )
            else:
                path = os.path.join(os.fsencode(TestLftp.temp_dir), *args)
            with open(path, 'wb') as f:
                f.write(bytearray([0xff]*size))

        my_mkdir("remote")
        my_mkdir("remote", "a")
        my_touch(24*1024, "remote", "a", "aa")
        my_touch(24*1024*1024, "remote", "a", "ab")
        my_mkdir("remote", "b")
        my_mkdir("remote", "b", "ba")
        my_touch(128*1024, "remote", "b", "ba", "baa")
        my_touch(128*1024, "remote", "b", "ba", "bab")
        my_touch(128*1024, "remote", "b", "bb")
        my_touch(1234, "remote", "c")
        my_touch(128*1024, "remote", "d d")
        my_mkdir("remote", "e e")
        my_touch(128*1024, "remote", "e e", "e e a")
        my_mkdir("remote", "áßç")
        my_touch(128*1024, "remote", "áßç", "dőÀ")
        my_touch(256*1024, "remote", "üæÒ")
        my_mkdir_latin(b"remote", b"f\xe9g")
        my_touch_latin(128*1024, b"remote", b"f\xe9g", b"d\xe9f")
        my_touch_latin(256*1024, b"remote", b"g\xe9h")
        my_mkdir_latin(b"remote", b"latin")
        my_touch_latin(128*1024, b"remote", b"latin", b"d\xe9f")
        my_mkdir("local")

    @classmethod
    def tearDownClass(cls):
        if TestLftp.temp_dir is None:
            return
        # Cleanup
        shutil.rmtree(TestLftp.temp_dir)

    @staticmethod
    def _build_test_lftp():
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__path_pairs_by_id = {}
        lftp._Lftp__base_remote_dir_path = "/remote/default"
        lftp._Lftp__base_local_dir_path = "/local/default"
        lftp._Lftp__run_command = MagicMock(return_value="")
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        lftp._Lftp__last_status_poll_healthy = True
        return lftp

    @staticmethod
    def _build_status_poll_test_lftp(send_side_effect=None):
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
        process.buffer_type = StringIO
        process._buffer = StringIO()
        process._before = StringIO()
        process.searchwindowsize = None
        process.delaybeforesend = 7
        process.delayafterread = 11
        process.expect.return_value = None
        process.read_nonblocking.side_effect = pexpect.exceptions.TIMEOUT("empty terminal")
        if send_side_effect is not None:
            process.send.side_effect = send_side_effect
        lftp._Lftp__process = process
        return lftp

    def test_status_poll_discards_stale_terminal_prompt_before_sending(self):
        lftp = self._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process._buffer.write("stale prompt>")
        process._before.write("stale prompt>")
        process.before = ""
        process.read_nonblocking.side_effect = ["background progress", pexpect.exceptions.TIMEOUT("empty")]

        def assert_fresh_boundary(command):
            self.assertEqual("", process._buffer.getvalue())
            self.assertEqual("", process._before.getvalue())
            self.assertIsNone(process.after)
            self.assertEqual("jobs -v\n", command)

        process.send.side_effect = assert_fresh_boundary

        self.assertEqual([], lftp.status())

        process.send.assert_called_once_with("jobs -v\n")
        self.assertTrue(lftp.last_status_poll_healthy)
        self.assertIsNone(lftp.last_status_poll_failure_reason)

        # Pexpect 4.9 repopulates _buffer from _before before searching.  The
        # drain must leave both empty, or the stale prompt reappears here.
        expecter = Expecter(process, searcher_re([re.compile("stale prompt>")]))
        self.assertIsNone(expecter.existing_data())

    def test_status_poll_terminal_backlog_is_unhealthy_and_does_not_send(self):
        lftp = self._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process._buffer.write("x" * (lftp_mod.STATUS_POLL_TERMINAL_DRAIN_MAX_BYTES + 1))
        process._before.write("x" * (lftp_mod.STATUS_POLL_TERMINAL_DRAIN_MAX_BYTES + 1))

        self.assertEqual([], lftp.status())

        process.send.assert_not_called()
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("terminal_backlog", lftp.last_status_poll_failure_reason)
        self.assertEqual(1, len(process._buffer.getvalue()))
        self.assertEqual(1, len(process._before.getvalue()))

        self.assertEqual([], lftp.status())
        process.send.assert_called_once_with("jobs -v\n")
        self.assertTrue(lftp.last_status_poll_healthy)

    def test_status_poll_terminal_backend_error_is_visible_without_sending(self):
        lftp = self._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process._buffer.write("Login failed:" + "x" * (lftp_mod.STATUS_POLL_TERMINAL_DRAIN_READ_BYTES + 1))
        process._before.write("Login failed:" + "x" * (lftp_mod.STATUS_POLL_TERMINAL_DRAIN_READ_BYTES + 1))

        self.assertEqual([], lftp.status())

        process.send.assert_not_called()
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("command_error", lftp.last_status_poll_failure_reason)
        self.assertEqual("Lftp status terminal reported a backend error", lftp._Lftp__pending_error)
        lftp.logger.error.assert_called_once_with("Lftp status terminal reported a backend error")
        with self.assertRaisesRegex(LftpError, "Lftp status terminal reported a backend error"):
            lftp.raise_pending_error()

    def test_status_poll_terminal_host_key_error_spanning_chunks_does_not_send(self):
        lftp = self._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process.read_nonblocking.side_effect = [
            "The authenticity of ",
            "host example cannot be established" + "x" * (lftp_mod.STATUS_POLL_TERMINAL_DRAIN_READ_BYTES + 1),
        ]

        self.assertEqual([], lftp.status())

        process.send.assert_not_called()
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("command_error", lftp.last_status_poll_failure_reason)
        self.assertEqual("Lftp status terminal reported a backend error", lftp._Lftp__pending_error)

    def test_status_poll_terminal_eof_is_unhealthy_without_sending(self):
        lftp = self._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process.read_nonblocking.side_effect = pexpect.exceptions.EOF("closed")

        self.assertEqual([], lftp.status())

        process.send.assert_not_called()
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("eof", lftp.last_status_poll_failure_reason)

    def test_queue_uses_override_paths(self):
        lftp = self._build_test_lftp()

        lftp.queue("dup", False, remote_base_dir_path="/remote/movies", local_base_dir_path="/local/movies")

        lftp._Lftp__run_command.assert_called_once_with(
            "queue pget -c \"/remote/movies/dup\" -o \"/local/movies/\"",
            require_prompt_ready=False,
            low_latency=True,
        )
        lftp.logger.debug.assert_called_once_with(
            "queue command: %s",
            "queue pget -c \"/remote/movies/dup\" -o \"/local/movies/\""
        )

    def test_queue_command_breadcrumb_is_opt_in_and_opaque(self):
        lftp = self._build_status_poll_test_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.command": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        lftp._Lftp__run_command(
            "queue pget -c \"/private/source\" -o \"/private/destination\"",
            require_prompt_ready=False,
            trace_command_kind="pget",
            trace_flow_id="fractional-queue:0123456789abcdef",
            trace_pty_correlation="lftp-pty:0123456789abcdef",
        )

        events = trace.snapshot()["entries"]
        self.assertEqual(["submitted", "prompt_ready"], [event["details"]["phase"] for event in events])
        self.assertEqual(["pget", "pget"], [event["details"]["transfer_kind"] for event in events])
        self.assertTrue(all(event["details"]["process_alive"] for event in events))
        self.assertTrue(all(event["details"]["output_class"] == "empty" for event in events))
        self.assertTrue(all(event["flow_id"] == "fractional-queue:0123456789abcdef" for event in events))
        self.assertNotIn("/private", repr(events))

    def test_pty_queue_breadcrumb_requires_explicit_rule_and_records_no_payload(self):
        lftp = self._build_status_poll_test_lftp()
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8, policy={"default": "info"})
        lftp.set_breadcrumb_trace(trace)

        lftp._Lftp__run_command(
            "queue pget -c \"/private/source\" -o \"/private/destination\"",
            require_prompt_ready=False,
            trace_command_kind="pget",
            trace_flow_id="fractional-queue:0123456789abcdef",
            trace_pty_correlation="lftp-pty:0123456789abcdef",
        )

        self.assertEqual([], trace.snapshot()["entries"])

    def test_pty_queue_breadcrumb_records_write_and_prompt_boundaries_without_payload(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.sendline.return_value = 3
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.pty": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        lftp._Lftp__run_command(
            "queue pget -c \"/private/source\" -o \"/private/destination\"",
            require_prompt_ready=False,
            trace_command_kind="pget",
            trace_flow_id="fractional-queue:0123456789abcdef",
            trace_pty_correlation="lftp-pty:0123456789abcdef",
        )

        events = trace.snapshot()["entries"]
        self.assertEqual(["pre_write", "write", "prompt"], [event["details"]["phase"] for event in events])
        self.assertEqual("sendline", events[1]["details"]["send_mode"])
        self.assertTrue(all(event["details"]["process_alive"] for event in events))
        self.assertNotIn("/private", repr(events))

    def test_pty_queue_breadcrumb_warning_mode_retains_send_failures_without_payload_reads(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.sendline.side_effect = pexpect.exceptions.TIMEOUT("synthetic")
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.pty": "warning"}},
        )
        lftp.set_breadcrumb_trace(trace)

        with self.assertRaises(pexpect.exceptions.TIMEOUT):
            lftp._Lftp__run_command(
                "queue pget -c \"/private/source\" -o \"/private/destination\"",
                require_prompt_ready=False, trace_command_kind="pget",
                trace_flow_id="fractional-queue:0123456789abcdef",
                trace_pty_correlation="lftp-pty:0123456789abcdef",
            )

        events = trace.snapshot()["entries"]
        self.assertEqual(["write"], [event["details"]["phase"] for event in events])
        self.assertEqual("send_error", events[0]["details"]["prompt_outcome"])
        self.assertEqual("timeout", events[0]["details"]["exception_family"])
        self.assertNotIn("/private", repr(events))

    def test_pty_queue_breadcrumb_records_os_error_before_reraising(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.sendline.side_effect = OSError("synthetic")
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.pty": "warning"}},
        )
        lftp.set_breadcrumb_trace(trace)

        with self.assertRaises(OSError):
            lftp._Lftp__run_command(
                "queue pget -c \"/private/source\" -o \"/private/destination\"",
                require_prompt_ready=False, trace_command_kind="pget",
                trace_pty_correlation="lftp-pty:0123456789abcdef",
            )

        event = trace.snapshot()["entries"][0]
        self.assertEqual("send_error", event["details"]["prompt_outcome"])
        self.assertEqual("os_error", event["details"]["exception_family"])

    def test_pty_setup_failures_preserve_queue_dispatch_without_a_trace_token(self):
        lftp = self._build_test_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.pty": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp.secrets.token_hex", side_effect=RuntimeError("rng unavailable")):
            lftp.queue("sample.bin", False)

        _, kwargs = lftp._Lftp__run_command.call_args
        self.assertNotIn("trace_pty_correlation", kwargs)

    def test_queue_command_breadcrumb_includes_only_bounded_scale_metrics(self):
        lftp = self._build_status_poll_test_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.command": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        lftp.queue(
            "private-directory", True,
            exclude_patterns=["private-glob", "another-private-glob"],
            trace_flow_id="fractional-queue:0123456789abcdef",
        )

        events = trace.snapshot()["entries"]
        self.assertEqual(["2-4", "2-4"], [event["details"]["exclude_count_bucket"] for event in events])
        self.assertEqual([2, 2], [event["details"]["exact_exclusion_count"] for event in events])
        self.assertTrue(all(event["details"]["argument_count_bucket"] == "5-16" for event in events))
        self.assertTrue(all(event["details"]["submission_byte_length_bucket"] != "unknown" for event in events))
        self.assertNotIn("private-directory", repr(events))
        self.assertNotIn("private-glob", repr(events))

    def test_queue_command_breadcrumb_bounds_large_exact_exclusion_scale(self):
        lftp = self._build_status_poll_test_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.command": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        lftp.queue(
            "private-directory", True,
            exclude_patterns=[ExactPathExclusion("private-{:04d}.bin".format(index)) for index in range(300)],
            trace_flow_id="fractional-queue:0123456789abcdef",
        )

        events = trace.snapshot()["entries"]
        self.assertTrue(all(event["details"]["exclude_count_bucket"] == "257+" for event in events))
        self.assertTrue(all(event["details"]["exact_exclusion_count"] == 300 for event in events))
        self.assertEqual({"8192-32767"}, {event["details"]["submission_byte_length_bucket"] for event in events})
        self.assertNotIn("private-directory", repr(events))
        self.assertNotIn("private-0000.bin", repr(events))

    def test_queue_command_breadcrumb_handles_unencodable_metric_input(self):
        lftp = self._build_status_poll_test_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.command": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        lftp.queue("surrogate-\ud800", True, trace_flow_id="fractional-queue:0123456789abcdef")

        events = trace.snapshot()["entries"]
        self.assertTrue(all(event["details"]["submission_byte_length_bucket"] == "unknown" for event in events))

    def test_status_poll_breadcrumb_is_opt_in_and_joins_the_opaque_poll_token(self):
        lftp = self._build_status_poll_test_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        statuses = lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef")

        self.assertEqual([], statuses)
        events = trace.snapshot()["entries"]
        self.assertEqual(["submitted", "jobs_read", "prompt_ready", "parse_started", "parse_complete", "health"],
                         [event["details"]["phase"] for event in events])
        self.assertTrue(all(event["corr_id"] == "lftp-poll:0123456789abcdef" for event in events))
        self.assertTrue(all(event["flow_id"] == "lftp-poll:0123456789abcdef" for event in events))
        self.assertTrue(all(event["details"]["read_buffer_byte_length_bucket"] == "0" for event in events))
        self.assertEqual("health", events[-1]["details"]["boundary"])
        self.assertTrue(events[-1]["details"]["healthy"])
        self.assertEqual("not_required", events[0]["details"]["prior_prompt"])
        self.assertTrue(events[0]["details"]["send_admitted"])
        self.assertEqual("unknown", events[0]["details"]["prompt_reached"])
        self.assertTrue(events[1]["details"]["prompt_reached"])
        self.assertEqual("unknown", events[1]["details"]["retained_before"])
        self.assertNotIn("jobs -v", repr(events))

    def test_status_poll_breadcrumb_exposes_independent_boundary_outcomes(self):
        lftp = self._build_status_poll_test_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef")

        events = trace.snapshot()["entries"]
        self.assertEqual(
            ["send", "read", "prompt", "parse", "parse", "health"],
            [event["details"]["boundary"] for event in events],
        )
        self.assertEqual("healthy", events[-1]["details"]["outcome"])
        self.assertTrue(all(event["corr_id"] == "lftp-poll:0123456789abcdef" for event in events))
        self.assertTrue(all("exception_family" not in event["details"] for event in events))

    def test_status_poll_parser_failure_records_parser_family_and_preserves_result(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__job_status_parser.parse.side_effect = LftpJobStatusParserError("private parser detail")
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        self.assertIsNone(lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))

        events = trace.snapshot()["entries"]
        parse_error = next(event for event in events if event["details"]["phase"] == "parse_error")
        health = next(event for event in events if event["details"]["phase"] == "health")
        self.assertEqual("parser", parse_error["details"]["exception_family"])
        self.assertFalse(health["details"]["healthy"])
        self.assertNotIn("private parser detail", repr(events))

    def test_status_poll_marks_malformed_known_option_queue_membership_unhealthy(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__job_status_parser = LftpJobStatusParser()
        lftp._Lftp__run_command = MagicMock(return_value=(
            "jobs -v\n"
            "[0] queue (sftp://someone:@localhost)\n"
            "sftp://someone:@localhost/remote\n"
            "Queue is stopped.\n"
            "Commands queued:\n"
            "1. mirror --exclude\n"
            "[2] mirror -c --exclude child /remote/sample /local/staging/ -- 10/20 (50%)\n"
        ))

        self.assertIsNone(lftp.status())

        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("parser_error", lftp.last_status_poll_failure_reason)

    def test_status_poll_pre_parse_record_is_retrievable_without_an_exit_record(self):
        lftp = self._build_status_poll_test_lftp()
        entered = threading.Event()
        release = threading.Event()

        def wait_for_release(_output):
            entered.set()
            self.assertTrue(release.wait(timeout=1))
            return []

        lftp._Lftp__job_status_parser.parse.side_effect = wait_for_release
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)
        stderr = MagicMock()
        stderr.fileno.return_value = 19
        original_remaining = lftp_mod._PREPARSE_STDERR_REMAINING
        try:
            with patch.dict(os.environ, {"SEEDSYNC_LFTP_PREPARSE_STDERR": "1"}, clear=True), \
                    patch("lftp.lftp.sys.stderr", stderr), \
                    patch("lftp.lftp.select.select", return_value=([], [19], [])), \
                    patch("lftp.lftp.os.write") as write:
                worker = threading.Thread(
                    target=lftp.status,
                    kwargs={"trace_poll_correlation": "lftp-poll:0123456789abcdef"},
                )
                worker.start()
                self.assertTrue(entered.wait(timeout=1))
                phases = [entry["details"]["phase"] for entry in trace.snapshot()["entries"]]
                self.assertIn("parse_started", phases)
                self.assertNotIn("parse_complete", phases)
                self.assertNotIn("parse_error", phases)
                self.assertNotIn("health", phases)
                self.assertNotIn("jobs -v", repr(trace.snapshot()))
                self.assertIn(
                    b"seedsync_lftp_preparse corr=lftp-poll:0123456789abcdef",
                    write.call_args.args[1],
                )

                release.set()
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
        finally:
            lftp_mod._PREPARSE_STDERR_REMAINING = original_remaining

    def test_status_preparse_stderr_is_explicitly_gated_and_joins_poll_correlation(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__run_command = MagicMock(return_value="one\ntwo\nthree")
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)
        stderr = MagicMock()
        stderr.fileno.return_value = 19
        original_remaining = lftp_mod._PREPARSE_STDERR_REMAINING
        try:
            with patch.dict(os.environ, {}, clear=True), patch("lftp.lftp.sys.stderr", stderr), \
                    patch("lftp.lftp.os.write") as write:
                self.assertEqual([], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))
                write.assert_not_called()

            with patch.dict(os.environ, {"SEEDSYNC_LFTP_PREPARSE_STDERR": "1"}, clear=True), \
                    patch("lftp.lftp.sys.stderr", stderr), \
                    patch("lftp.lftp.select.select", return_value=([], [19], [])), \
                    patch("lftp.lftp.os.write") as write:
                self.assertEqual([], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))
        finally:
            lftp_mod._PREPARSE_STDERR_REMAINING = original_remaining

        self.assertEqual(1, write.call_count)
        line = write.call_args.args[1].decode("ascii").strip()
        self.assertRegex(
            line,
            r"^seedsync_lftp_preparse corr=lftp-poll:0123456789abcdef "
            r"phase=parse_started bytes=1-127 lines=2-4 process_alive=1$",
        )
        self.assertNotIn("one", line)

    def test_status_preparse_stderr_requires_status_trace_policy(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__run_command = MagicMock(return_value="private-output")
        lftp.set_breadcrumb_trace(BreadcrumbTraceCollector(lambda: True, max_entries=8, policy={"default": "off"}))
        stderr = MagicMock()
        original_remaining = lftp_mod._PREPARSE_STDERR_REMAINING
        try:
            with patch.dict(os.environ, {"SEEDSYNC_LFTP_PREPARSE_STDERR": "1"}, clear=True), \
                    patch("lftp.lftp.sys.stderr", stderr), \
                    patch("lftp.lftp.os.write") as write:
                self.assertEqual([], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))
                write.assert_not_called()
        finally:
            lftp_mod._PREPARSE_STDERR_REMAINING = original_remaining

    def test_private_status_frame_capture_is_disabled_before_any_file_io(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        with patch.dict(os.environ, {}, clear=True), patch("lftp.lftp.os.open") as open_file:
            lftp_mod._lftp_private_status_frame_capture(
                "lftp-poll:0123456789abcdef", "private status output", True, trace, {},
            )
        open_file.assert_not_called()

    def test_private_status_frame_capture_writes_capped_atomic_private_pair(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        original_remaining = lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING
        output = "private-status-" + ("x" * lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_MAX_BYTES)
        try:
            with tempfile.TemporaryDirectory() as capture_dir, \
                    patch.dict(os.environ, {
                        "SEEDSYNC_LFTP_PRIVATE_STATUS_FRAME_CAPTURE_DIR": capture_dir,
                    }, clear=True):
                lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = 1
                lftp_mod._lftp_private_status_frame_capture(
                    "lftp-poll:0123456789abcdef", output, True, trace,
                    {"pexpect_version": "test", "before_byte_count": 1,
                     "after_byte_count": 2, "buffer_byte_count": 3},
                )
                captures = list(Path(capture_dir).iterdir())
                self.assertEqual(1, len(captures))
                self.assertTrue(captures[0].is_dir())
                raw_path = captures[0] / "frame.bin"
                manifest_path = captures[0] / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_MAX_BYTES, raw_path.stat().st_size)
                self.assertEqual(len(output.encode("utf-8")), manifest["original_byte_count"])
                self.assertTrue(manifest["truncated"])
                self.assertEqual(hashlib.sha256(output.encode("utf-8")).hexdigest(), manifest["sha256"])
                self.assertEqual("test", manifest["boundary"]["pexpect_version"])
                self.assertEqual(0o600, stat.S_IMODE(raw_path.stat().st_mode))
                self.assertEqual(0o600, stat.S_IMODE(manifest_path.stat().st_mode))
        finally:
            lftp_mod._PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = original_remaining

    def test_status_captures_private_frame_on_parser_failure_before_error_reporting(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__run_command = MagicMock(return_value="private status output")
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture") as capture:
            def fail_after_capture(output):
                self.assertEqual("private status output", output)
                capture.assert_not_called()
                raise LftpJobStatusParserError("test failure")

            lftp._Lftp__job_status_parser.parse.side_effect = fail_after_capture
            self.assertIsNone(lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))

        self.assertEqual("lftp-poll:0123456789abcdef", capture.call_args.args[0])
        self.assertEqual("private status output", capture.call_args.args[1])

    def test_status_success_does_not_consume_private_frame_capture(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__run_command = MagicMock(return_value="ordinary status output")
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp._lftp_private_status_frame_capture") as capture:
            self.assertEqual([], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))
        capture.assert_not_called()

    def test_status_poll_trace_failure_does_not_change_unexpected_exception_propagation(self):
        lftp = self._build_status_poll_test_lftp()
        expected = RuntimeError("status worker failure")
        lftp._Lftp__run_command = MagicMock(side_effect=expected)
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        trace.record = MagicMock(side_effect=RuntimeError("diagnostic sink failure"))
        lftp.set_breadcrumb_trace(trace)

        with self.assertRaisesRegex(RuntimeError, "status worker failure"):
            lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef")

    def test_status_exception_families_use_only_the_approved_enum(self):
        errors = (
            (pexpect.exceptions.TIMEOUT("timeout"), "timeout"),
            (OSError("os error"), "os_error"),
            (pexpect.exceptions.EOF("eof"), "eof"),
            (LftpJobStatusParserError("parser"), "parser"),
            (RuntimeError("runtime"), "runtime"),
            (ValueError("unclassified"), "unknown"),
        )
        self.assertTrue(all(
            _lftp_diagnostic_exception_family(error) == expected
            for error, expected in errors
        ))

    def test_status_poll_breadcrumb_disabled_adds_no_trace_reads(self):
        lftp = self._build_status_poll_test_lftp()
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8, policy={"default": "off", "rules": {}})
        lftp.set_breadcrumb_trace(trace)

        self.assertEqual([], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))

        self.assertEqual([], trace.snapshot()["entries"])

    def test_status_poll_breadcrumb_warning_policy_keeps_only_failure_phase(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("synthetic")
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "warning"}},
        )
        lftp.set_breadcrumb_trace(trace)

        self.assertEqual([], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))

        events = trace.snapshot()["entries"]
        self.assertEqual(["prompt_timeout", "health"], [event["details"]["phase"] for event in events])
        self.assertFalse(events[-1]["details"]["healthy"])
        self.assertEqual(["warning", "warning"], [event["level"] for event in events])
        self.assertNotIn("jobs -v", repr(events))

    def test_status_poll_connection_grace_retry_keeps_correlation(self):
        lftp = self._build_test_lftp()
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.return_value = []
        lftp._Lftp__status_poll_needs_connection_grace = True

        self.assertEqual([], lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef"))

        self.assertEqual(
            [
                call("jobs -v", timeout_seconds=0, require_prompt_ready=False, status_poll=True,
                     trace_status_poll_correlation="lftp-poll:0123456789abcdef"),
                call("jobs -v", timeout_seconds=5.0, require_prompt_ready=False, status_poll=True,
                     trace_status_poll_correlation="lftp-poll:0123456789abcdef"),
            ],
            lftp._Lftp__run_command.call_args_list,
        )

    def test_status_poll_connecting_recovery_records_prompt_ready(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.before = b"Connecting..."
        lftp._Lftp__process.after = pexpect.TIMEOUT
        lftp._Lftp__process.expect.side_effect = [
            pexpect.exceptions.TIMEOUT("timeout"),
            pexpect.exceptions.TIMEOUT("timeout"),
            None,
        ]
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        lftp.set_breadcrumb_trace(trace)

        with patch("lftp.lftp.time.monotonic", side_effect=[0.0, 0.01, 1.01]), patch("lftp.lftp.time.sleep"):
            lftp._Lftp__run_command(
                "jobs -v", timeout_seconds=0, require_prompt_ready=False, status_poll=True,
                trace_status_poll_correlation="lftp-poll:0123456789abcdef",
            )

        self.assertEqual(
            ["submitted", "jobs_read", "prompt_timeout", "prompt_ready"],
            [event["details"]["phase"] for event in trace.snapshot()["entries"]],
        )

    def test_queue_command_breadcrumb_warns_on_send_timeout_when_debug_is_disabled(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.sendline.side_effect = pexpect.exceptions.TIMEOUT("synthetic")
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.command": "warning"}},
        )
        lftp.set_breadcrumb_trace(trace)

        with self.assertRaises(pexpect.exceptions.TIMEOUT):
            lftp._Lftp__run_command(
                "queue pget -c \"/private/source\" -o \"/private/destination\"",
                require_prompt_ready=False,
                trace_command_kind="pget",
                trace_flow_id="fractional-queue:0123456789abcdef",
            )

        events = trace.snapshot()["entries"]
        self.assertEqual(["prompt_timeout"], [event["details"]["phase"] for event in events])
        self.assertEqual(["pget"], [event["details"]["transfer_kind"] for event in events])
        self.assertNotIn("/private", repr(events))

    def test_queue_forwards_only_a_valid_opaque_command_trace_flow(self):
        lftp = self._build_test_lftp()

        lftp.queue(
            "sample.bin", False,
            trace_flow_id="fractional-queue:0123456789abcdef",
        )

        lftp._Lftp__run_command.assert_called_once_with(
            "queue pget -c \"/remote/default/sample.bin\" -o \"/local/default/\"",
            require_prompt_ready=False,
            low_latency=True,
            trace_command_kind="pget",
            trace_flow_id="fractional-queue:0123456789abcdef",
        )

    def test_queue_sidecar_breadcrumb_classifies_missing_and_is_gated(self):
        lftp = self._build_test_lftp()
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        lftp.set_breadcrumb_trace(trace)
        with tempfile.TemporaryDirectory() as local_dir:
            lftp.queue("private-name.bin", False, local_base_dir_path=local_dir)

        trace.is_effectively_enabled.assert_called_once_with("lftp.sidecar", "info")
        trace.record.assert_called_once()
        details = trace.record.call_args.args[2]
        self.assertEqual("missing", details["classification"])
        self.assertNotIn("private-name.bin", repr(trace.record.call_args))
        self.assertNotEqual("private-name.bin", trace.record.call_args.kwargs["corr_id"])

    def test_queue_sidecar_flow_is_opaque_and_exact_debug_gated(self):
        lftp = self._build_test_lftp()
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"lftp.sidecar": "info"}},
            max_entries=8,
        )
        lftp.set_breadcrumb_trace(trace)
        flow_id = "fractional-queue:0123456789abcdef"
        with tempfile.TemporaryDirectory() as local_dir, patch.dict(
                os.environ, {"INCOMING_RECOVERY_EXPERIMENTAL_AUTHORITY_TIMEOUT_SECS": "600"},
        ):
            lftp.queue(
                "private-name.bin", False,
                local_base_dir_path=local_dir, trace_flow_id=flow_id,
            )

        events = trace.snapshot()["entries"]
        self.assertEqual(1, len(events))
        self.assertEqual(flow_id, events[0]["flow_id"])
        self.assertNotIn("private-name.bin", repr(events))
        self.assertEqual(1, lftp._Lftp__run_command.call_count)

        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"lftp.sidecar": "info"}},
            max_entries=8,
        )
        lftp.set_breadcrumb_trace(trace)
        with tempfile.TemporaryDirectory() as local_dir, patch.dict(
                os.environ, {"INCOMING_RECOVERY_EXPERIMENTAL_AUTHORITY_TIMEOUT_SECS": "599"},
        ):
            lftp.queue(
                "private-name.bin", False,
                local_base_dir_path=local_dir, trace_flow_id=flow_id,
            )

        self.assertIsNone(trace.snapshot()["entries"][0]["flow_id"])
        self.assertNotIn("private-name.bin", repr(trace.snapshot()["entries"]))
        self.assertEqual(2, lftp._Lftp__run_command.call_count)

    def test_queue_sidecar_breadcrumb_classifies_rejection_and_valid_artifact(self):
        lftp = self._build_test_lftp()
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        lftp.set_breadcrumb_trace(trace)
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "sample.bin.lftp")
            with open(target, "wb") as handle:
                handle.write(b"partial")
            with open(target + ".lftp-pget-status", "w") as handle:
                handle.write("size=4\n0.pos=0\n0.limit=4\n")
            lftp.queue("sample.bin", False, local_base_dir_path=local_dir)
            self.assertEqual("valid", trace.record.call_args.args[2]["classification"])

            trace.record.reset_mock()
            with open(target + ".lftp-pget-status", "w") as handle:
                handle.write("size=-2\n0.pos=0\n")
            with self.assertRaises(LftpError):
                lftp.queue("sample.bin", False, local_base_dir_path=local_dir)
            self.assertEqual("malformed", trace.record.call_args.args[2]["classification"])

    def test_queue_sidecar_breadcrumb_disabled_preserves_dispatch(self):
        lftp = self._build_test_lftp()
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = False
        lftp.set_breadcrumb_trace(trace)
        with tempfile.TemporaryDirectory() as local_dir:
            lftp.queue("sample.bin", False, local_base_dir_path=local_dir)

        trace.record.assert_not_called()
        lftp._Lftp__run_command.assert_called_once()

    def test_queue_sidecar_breadcrumb_coalesces_across_interleaved_poll_event(self):
        lftp = self._build_test_lftp()
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        lftp.set_breadcrumb_trace(trace)

        with tempfile.TemporaryDirectory() as local_dir:
            lftp.queue("sample.bin", False, local_base_dir_path=local_dir)
            trace.record(
                "lftp",
                "lftp_poll_snapshot",
                {"coverage": "unknown"},
                category="transfer.lftp",
                level="info",
            )
            lftp.queue("sample.bin", False, local_base_dir_path=local_dir)

        sidecar_events = [
            event for event in trace.snapshot()["entries"]
            if event["category"] == "lftp.sidecar"
        ]
        self.assertEqual(1, len(sidecar_events))
        self.assertEqual("missing", sidecar_events[0]["details"]["classification"])
        self.assertEqual(2, sidecar_events[0]["repeat_count"])

    def test_queue_sidecar_breadcrumb_keeps_valid_coverage_transition(self):
        lftp = self._build_test_lftp()
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        lftp.set_breadcrumb_trace(trace)

        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "sample.bin")
            with open(target, "wb") as handle:
                handle.write(b"partial")
            lftp.queue("sample.bin", False, local_base_dir_path=local_dir)

            with open(target + ".lftp-pget-status", "w") as handle:
                handle.write("size=4\n0.pos=0\n0.limit=4\n")
            lftp.queue("sample.bin", False, local_base_dir_path=local_dir)

        sidecar_events = [
            event for event in trace.snapshot()["entries"]
            if event["category"] == "lftp.sidecar"
        ]
        self.assertEqual(2, len(sidecar_events))
        self.assertEqual(
            ["unknown", "known"],
            [event["details"]["coverage"]["sidecar_size"] for event in sidecar_events],
        )
        self.assertEqual([1, 1], [event["repeat_count"] for event in sidecar_events])

    def test_queue_dir_uses_override_paths(self):
        lftp = self._build_test_lftp()

        lftp.queue("dup", True, remote_base_dir_path="/remote/movies", local_base_dir_path="/local/movies")

        lftp._Lftp__run_command.assert_called_once_with(
            "queue mirror -c \"/remote/movies/dup\" \"/local/movies/\"",
            require_prompt_ready=False,
            low_latency=True,
        )

    def test_queue_control_command_uses_zero_pexpect_delays_and_normal_prompt_wait(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.before = b"queue accepted"

        def assert_zero_delays(*_args, **_kwargs):
            self.assertEqual(0, lftp._Lftp__process.delaybeforesend)
            self.assertEqual(0, lftp._Lftp__process.delayafterread)

        lftp._Lftp__process.sendline.side_effect = assert_zero_delays
        lftp._Lftp__process.expect.side_effect = assert_zero_delays

        lftp.queue("queued.bin", False)

        self.assertEqual(7, lftp._Lftp__process.delaybeforesend)
        self.assertEqual(11, lftp._Lftp__process.delayafterread)
        lftp._Lftp__process.sendline.assert_called_once_with(
            'queue pget -c "/remote/default/queued.bin" -o "/local/default/"'
        )
        lftp._Lftp__process.expect.assert_called_once_with("prompt>", timeout=30)

    def test_kill_matches_duplicate_names_by_remote_path(self):
        lftp = self._build_test_lftp()
        status_movies = LftpJobStatus(
            job_id=7,
            job_type=LftpJobStatus.Type.MIRROR,
            state=LftpJobStatus.State.RUNNING,
            name="dup",
            flags="-c",
            remote_path="/remote/movies/dup",
            local_path="/local/movies/"
        )
        status_movies.path_pair_id = "movies"
        status_tv = LftpJobStatus(
            job_id=9,
            job_type=LftpJobStatus.Type.MIRROR,
            state=LftpJobStatus.State.RUNNING,
            name="dup",
            flags="-c",
            remote_path="/remote/tv/dup",
            local_path="/local/tv/"
        )
        status_tv.path_pair_id = "tv"
        lftp.status = MagicMock(return_value=[status_movies, status_tv])

        killed = lftp.kill("dup", path_pair_id="tv", remote_path="/remote/tv/dup", local_path="/local/tv")

        self.assertTrue(killed)
        lftp._Lftp__run_command.assert_called_once_with(
            "kill 9",
            require_prompt_ready=False,
            low_latency=True,
        )

    def test_status_annotates_path_pairs_from_job_paths(self):
        lftp = self._build_test_lftp()
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__consecutive_status_errors = 0
        lftp._Lftp__last_command_timed_out = False
        lftp._Lftp__last_status_poll_healthy = True
        lftp._Lftp__path_pairs_by_id = {
            "movies": {
                "name": "Movies",
                "remote_path": "/remote/movies",
                "local_path": "/local/movies"
            },
            "tv": {
                "name": "TV",
                "remote_path": "/remote/tv",
                "local_path": "/local/tv"
            }
        }
        status = LftpJobStatus(
            job_id=5,
            job_type=LftpJobStatus.Type.MIRROR,
            state=LftpJobStatus.State.RUNNING,
            name="dup",
            flags="-c",
            remote_path="/remote/tv/dup",
            local_path="/local/tv/"
        )
        lftp._Lftp__job_status_parser.parse.return_value = [status]

        statuses = lftp.status()

        self.assertEqual("tv", statuses[0].path_pair_id)
        self.assertEqual("TV", statuses[0].path_pair_name)
        self.assertTrue(lftp.last_status_poll_healthy)

    def test_status_annotates_parsed_mirror_with_jobs_rendered_exclusion(self):
        output = (
            "jobs -v\n"
            "[0] queue (sftp://someone:@localhost)\n"
            "sftp://someone:@localhost/remote\n"
            "Queue is running.\n"
            "[1] mirror -c --exclude ^child\\\\-a\\\\.bin$ "
            "/remote/pair-a/sample-directory /local/pair-a/staging/ -- 10/20 (50%)\n"
        )
        status = LftpJobStatusParser().parse(output)[0]
        lftp = self._build_test_lftp()
        lftp._Lftp__path_pairs_by_id = {
            "pair-a": {
                "name": "Pair A",
                "remote_path": "/remote/pair-a",
                "local_path": "/local/pair-a",
            },
        }

        lftp._Lftp__annotate_status_path_pairs([status])

        self.assertEqual("pair-a", status.path_pair_id)
        self.assertEqual("Pair A", status.path_pair_name)
        self.assertEqual("sample-directory", status.name)

    def test_status_leaves_cross_pair_remote_and_local_matches_unscoped(self):
        lftp = self._build_test_lftp()
        lftp._Lftp__path_pairs_by_id = {
            "movies": {
                "name": "Movies",
                "remote_path": "/remote/movies",
                "local_path": "/local/movies",
            },
            "tv": {
                "name": "TV",
                "remote_path": "/remote/tv",
                "local_path": "/local/tv",
            },
        }
        status = LftpJobStatus(
            job_id=5,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="cross-pair",
            flags="-c",
            remote_path="/remote/movies/cross-pair",
            local_path="/local/tv/",
        )

        lftp._Lftp__annotate_status_path_pairs([status])

        self.assertIsNone(status.path_pair_id)
        self.assertIsNone(status.path_pair_name)

    def test_status_leaves_overlapping_or_reconfigured_roots_unscoped(self):
        cases = [
            {
                "broad": {
                    "name": "Broad",
                    "remote_path": "/remote",
                    "local_path": "/local",
                },
                "specific": {
                    "name": "Specific",
                    "remote_path": "/remote/movies",
                    "local_path": "/other",
                },
                "remote_path": "/remote/movies/file.mkv",
                "local_path": "/local/file.mkv",
            },
            {
                "first": {
                    "name": "First",
                    "remote_path": "/remote/movies",
                    "local_path": "/local/movies",
                },
                "reconfigured": {
                    "name": "Reconfigured",
                    "remote_path": "/remote/movies",
                    "local_path": "/local/other",
                },
                "remote_path": "/remote/movies/file.mkv",
                "local_path": "/local/movies/file.mkv",
            },
        ]
        for case in cases:
            with self.subTest(case=case):
                lftp = self._build_test_lftp()
                pair_data = {
                    key: value for key, value in case.items()
                    if key not in {"remote_path", "local_path"}
                }
                lftp._Lftp__path_pairs_by_id = pair_data
                status = LftpJobStatus(
                    job_id=5,
                    job_type=LftpJobStatus.Type.PGET,
                    state=LftpJobStatus.State.RUNNING,
                    name="ambiguous",
                    flags="-c",
                    remote_path=case["remote_path"],
                    local_path=case["local_path"],
                )

                lftp._Lftp__annotate_status_path_pairs([status])

                self.assertIsNone(status.path_pair_id)
                self.assertIsNone(status.path_pair_name)

    def test_status_path_pair_breadcrumb_classifies_zero_one_and_multiple_remote_matches(self):
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        lftp = self._build_test_lftp()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__path_pairs_by_id = {
            "first": {
                "name": "First",
                "remote_path": "/remote/first",
                "local_path": "/local/first",
            },
            "second": {
                "name": "Second",
                "remote_path": "/remote/second",
                "local_path": "/local/second",
            },
            "broad": {
                "name": "Broad",
                "remote_path": "/remote",
                "local_path": "/local/broad",
            },
        }
        statuses = [
            LftpJobStatus(
                job_id=1, job_type=LftpJobStatus.Type.PGET,
                state=LftpJobStatus.State.RUNNING, name="zero", flags="-c",
                remote_path="/other/zero", local_path="/other/zero",
            ),
            LftpJobStatus(
                job_id=2, job_type=LftpJobStatus.Type.PGET,
                state=LftpJobStatus.State.RUNNING, name="one", flags="-c",
                remote_path="/remote/second/one", local_path="/local/second/one",
            ),
            LftpJobStatus(
                job_id=3, job_type=LftpJobStatus.Type.PGET,
                state=LftpJobStatus.State.RUNNING, name="many", flags="-c",
                remote_path="/remote/first/many", local_path="/other/many",
            ),
        ]

        lftp._Lftp__annotate_status_path_pairs(statuses)

        events = [
            event for event in trace.record.call_args_list
            if event.args[1] == "lftp_path_pair_annotation"
        ]
        self.assertEqual(3, len(events))
        details = [event.args[2] for event in events]
        self.assertEqual(
            [
                ("zero", "zero", "unscoped", "remote_no_match"),
                ("multiple", "one", "unscoped", "remote_multiple_matches"),
                ("multiple", "zero", "unscoped", "remote_multiple_matches"),
            ],
            [
                (
                    item["remote_match_cardinality"], item["local_match_cardinality"],
                    item["result"], item["reason"],
                ) for item in details
            ],
        )
        for event in events:
            self.assertEqual("transfer.lftp", event.kwargs["category"])
            self.assertEqual("info", event.kwargs["level"])
            self.assertNotIn("/remote", repr(event))
            self.assertNotIn("/local", repr(event))

    def test_status_path_pair_breadcrumb_distinguishes_local_only_and_conflict(self):
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        lftp = self._build_test_lftp()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__path_pairs_by_id = {
            "movies": {
                "name": "Movies",
                "remote_path": "/remote/movies",
                "local_path": "/local/movies",
            },
            "tv": {
                "name": "TV",
                "remote_path": "/remote/tv",
                "local_path": "/local/tv",
            },
        }
        remote_only = LftpJobStatus(
            job_id=4, job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING, name="staging", flags="-c",
            remote_path="/remote/movies/staging", local_path="/staging/staging.lftp",
        )
        local_conflict = LftpJobStatus(
            job_id=5, job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING, name="conflict", flags="-c",
            remote_path="/remote/movies/conflict", local_path="/local/tv/conflict.lftp",
        )

        lftp._Lftp__annotate_status_path_pairs([remote_only, local_conflict])

        events = [
            event for event in trace.record.call_args_list
            if event.args[1] == "lftp_path_pair_annotation"
        ]
        self.assertEqual(
            [("selected", "remote_only"), ("unscoped", "local_conflict")],
            [(event.args[2]["result"], event.args[2]["reason"]) for event in events],
        )
        self.assertEqual("movies", remote_only.path_pair_id)
        self.assertIsNone(local_conflict.path_pair_id)

    def test_status_path_pair_breadcrumb_disabled_does_not_build_diagnostic_identity(self):
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = False
        lftp = self._build_test_lftp()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__path_pairs_by_id = {
            "movies": {
                "name": "Movies",
                "remote_path": "/remote/movies",
                "local_path": "/local/movies",
            },
        }
        status = LftpJobStatus(
            job_id=6, job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING, name="sample", flags="-c",
            remote_path="/remote/movies/sample", local_path="/local/movies/sample.lftp",
        )

        with patch("lftp.lftp.opaque_trace_correlation", side_effect=AssertionError("gate missed")):
            lftp._Lftp__annotate_status_path_pairs([status])

        trace.record.assert_not_called()
        self.assertEqual("movies", status.path_pair_id)

    def test_status_path_pair_breadcrumb_collector_retrieves_opaque_job_correlation(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp": "info"}},
        )
        lftp = self._build_test_lftp()
        lftp.set_breadcrumb_trace(trace)
        lftp._Lftp__path_pairs_by_id = {
            "movies": {
                "name": "Movies",
                "remote_path": "/remote/movies",
                "local_path": "/local/movies",
            },
        }
        status = LftpJobStatus(
            job_id=7, job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING, name="sample", flags="-c",
            remote_path="/remote/movies/sample", local_path="/local/movies/sample.lftp",
        )

        lftp._Lftp__annotate_status_path_pairs([status])

        events = [
            event for event in trace.snapshot()["entries"]
            if event["message"] == "lftp_path_pair_annotation"
        ]
        self.assertEqual(1, len(events))
        self.assertEqual("selected", events[0]["details"]["result"])
        self.assertEqual(status.job_correlation, events[0]["corr_id"])
        self.assertNotIn("sample", repr(events[0]))

    def test_status_marks_poll_unhealthy_when_jobs_command_times_out(self):
        lftp = self._build_status_poll_test_lftp(send_side_effect=pexpect.exceptions.TIMEOUT("timeout"))
        lftp.set_breadcrumb_trace(BreadcrumbTraceCollector(
            lambda: True, max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        ))

        statuses = lftp.status(trace_poll_correlation="lftp-poll:0123456789abcdef")

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("timeout", lftp.last_status_poll_failure_reason)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        self.assertTrue(lftp.last_command_timed_out)
        self.assertEqual(7, lftp._Lftp__process.delaybeforesend)
        lftp._Lftp__process.send.assert_called_once_with("jobs -v\n")
        lftp._Lftp__process.sendline.assert_not_called()
        lftp._Lftp__process.expect.assert_not_called()
        event = next(event for event in lftp._Lftp__breadcrumb_trace.snapshot()["entries"]
                     if event["details"]["phase"] == "prompt_timeout")
        self.assertEqual("prompt_timeout", event["details"]["phase"])
        self.assertEqual("timeout", event["details"]["exception_family"])

    def test_status_marks_poll_unhealthy_when_jobs_command_eof(self):
        lftp = self._build_status_poll_test_lftp(send_side_effect=pexpect.exceptions.EOF("eof"))

        statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("eof", lftp.last_status_poll_failure_reason)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        self.assertEqual(7, lftp._Lftp__process.delaybeforesend)
        lftp._Lftp__process.send.assert_called_once_with("jobs -v\n")
        lftp._Lftp__process.sendline.assert_not_called()
        lftp._Lftp__process.expect.assert_not_called()

    def test_status_marks_poll_unhealthy_when_jobs_command_raises_lftp_error(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__run_command = MagicMock(side_effect=LftpError("Lftp process terminated: eof"))

        statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("command_error", lftp.last_status_poll_failure_reason)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        lftp.logger.warning.assert_called_once()
        lftp._Lftp__run_command.assert_called_once_with(
            "jobs -v",
            timeout_seconds=0,
            require_prompt_ready=False,
            status_poll=True
        )

    def test_status_poll_failure_reason_defaults_to_safe_unhealthy_snapshot(self):
        lftp = self._build_status_poll_test_lftp()

        def unhealthy_snapshot(*args, **kwargs):
            lftp._Lftp__last_command_timed_out = True
            return ""

        lftp._Lftp__run_command = MagicMock(side_effect=unhealthy_snapshot)

        self.assertEqual([], lftp.status())
        self.assertEqual("unhealthy_snapshot", lftp.last_status_poll_failure_reason)

    def test_status_error_recovery_eof_keeps_eof_category_and_success_resets_it(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.before = b"get: Access failed"
        lftp._Lftp__process.expect.side_effect = [None, pexpect.exceptions.EOF("eof")]

        self.assertEqual([], lftp.status())
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("eof", lftp.last_status_poll_failure_reason)

        lftp._Lftp__last_command_timed_out = False
        lftp._Lftp__run_command = MagicMock(return_value="")
        self.assertEqual([], lftp.status())
        self.assertTrue(lftp.last_status_poll_healthy)
        self.assertIsNone(lftp.last_status_poll_failure_reason)

    def test_status_marks_poll_unhealthy_when_queue_command_echo_leaks_into_snapshot(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__job_status_parser = LftpJobStatusParser()
        lftp._Lftp__run_command = MagicMock(
            return_value='queue mirror -c "/remote/sample-directory" "/local/staging/"'
        )

        statuses = lftp.status()

        self.assertIsNone(statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("parser_error", lftp.last_status_poll_failure_reason)
        self.assertEqual(1, lftp._Lftp__consecutive_status_errors)

    def test_status_marks_poll_unhealthy_when_jobs_command_echo_interleaves_with_progress(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__job_status_parser = LftpJobStatusParser()
        lftp._Lftp__run_command = MagicMock(return_value=(
            "jobs -v\n"
            "[0] queue (sftp://example:@localhost) -- 4.5 MiB/s\n"
            "sftp://example:@localhost/remote\n"
            "Now executing: [1] mirror -c /remote/sample-directory /local/staging/ "
            "-- 4G/10G (40%) 4.5 MiB/s\n"
            "[1] mirror -c /remote/sample-directory /local/staging/ "
            "-- 4G/10G (40%) 4.5 MiB/s\n"
            "\\chunk 0-999 jobs -v\n"
        ))

        statuses = lftp.status()

        self.assertIsNone(statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("parser_error", lftp.last_status_poll_failure_reason)
        self.assertEqual(1, lftp._Lftp__consecutive_status_errors)

    def test_status_marks_poll_unhealthy_when_valid_queue_precedes_damaged_membership(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__job_status_parser = LftpJobStatusParser()
        lftp._Lftp__run_command = MagicMock(return_value=(
            "jobs -v\n"
            "[0] queue (sftp://example:@localhost)\n"
            "sftp://example:@localhost/remote\n"
            "Queue is running.\n"
            "[1] mirror --exclude " + ("\\" * 240)
        ))

        statuses = lftp.status()

        self.assertIsNone(statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("parser_error", lftp.last_status_poll_failure_reason)

    def test_status_marks_poll_unhealthy_when_jobs_command_raises_exception_pexpect(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.ExceptionPexpect("boom")

        statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("command_error", lftp.last_status_poll_failure_reason)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        self.assertEqual(7, lftp._Lftp__process.delaybeforesend)
        self.assertEqual(11, lftp._Lftp__process.delayafterread)
        lftp._Lftp__process.send.assert_called_once_with("jobs -v\n")
        self.assertGreaterEqual(lftp._Lftp__process.expect.call_count, 1)
        self.assertTrue(all(call.kwargs.get("timeout") == 0 for call in lftp._Lftp__process.expect.call_args_list))
        lftp.logger.warning.assert_called_once()

    def test_status_marks_poll_unhealthy_when_jobs_command_raises_oserror(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.expect.side_effect = OSError("io failure")

        statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual("command_error", lftp.last_status_poll_failure_reason)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        self.assertEqual(7, lftp._Lftp__process.delaybeforesend)
        self.assertEqual(11, lftp._Lftp__process.delayafterread)
        lftp._Lftp__process.send.assert_called_once_with("jobs -v\n")
        self.assertGreaterEqual(lftp._Lftp__process.expect.call_count, 1)
        self.assertTrue(all(call.kwargs.get("timeout") == 0 for call in lftp._Lftp__process.expect.call_args_list))
        lftp.logger.warning.assert_called_once()

    def test_status_poll_loop_retries_until_deadline_on_timeouts(self):
        lftp = self._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        call_count = {"value": 0}

        def expect_side_effect(pattern, timeout):
            call_count["value"] += 1
            raise pexpect.exceptions.TIMEOUT("timeout")

        process.expect.side_effect = expect_side_effect
        monotonic_values = iter([0.0, 0.01, 1.01])
        sleep_calls = []
        with patch("lftp.lftp.time.monotonic", side_effect=lambda: next(monotonic_values)), \
             patch("lftp.lftp.time.sleep", side_effect=lambda value: sleep_calls.append(value)):
            statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        self.assertEqual(2, process.expect.call_count)
        self.assertTrue(all(call.kwargs.get("timeout") == 0 for call in process.expect.call_args_list))
        self.assertEqual([0.01], sleep_calls)

    def test_run_command_status_poll_exhausts_to_empty_snapshot(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b"stale buffered output"
        process.after = pexpect.TIMEOUT
        process.delaybeforesend = 7
        process.delayafterread = 11
        process.send.return_value = None
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        lftp._Lftp__process = process

        monotonic_values = iter([0.0, 0.01, 1.01])
        with patch("lftp.lftp.time.monotonic", side_effect=lambda: next(monotonic_values)), \
             patch("lftp.lftp.time.sleep"):
            out = lftp._Lftp__run_command(
                "jobs -v",
                timeout_seconds=0,
                require_prompt_ready=False,
                status_poll=True
            )

        self.assertEqual("", out)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        self.assertEqual(2, process.expect.call_count)
        self.assertTrue(all(call.kwargs.get("timeout") == 0 for call in process.expect.call_args_list))
        self.assertEqual(7, process.delaybeforesend)
        self.assertEqual(11, process.delayafterread)

    def test_status_poll_preserves_pending_error_before_prompt_timeout(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__consecutive_status_errors = 0
        lftp._Lftp__last_command_timed_out = False
        lftp._Lftp__last_status_poll_healthy = True
        lftp._Lftp__path_pairs_by_id = {}
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.return_value = []
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b"mirror: Access failed"
        process.after = pexpect.TIMEOUT
        process.delaybeforesend = 7
        process.delayafterread = 11
        process.send.return_value = None

        call_count = {"value": 0}

        def expect_side_effect(pattern, timeout):
            call_count["value"] += 1
            raise pexpect.exceptions.TIMEOUT("timeout")

        process.expect.side_effect = expect_side_effect
        lftp._Lftp__process = process

        monotonic_values = iter([0.0, 0.01, 1.01])
        with patch("lftp.lftp.time.monotonic", side_effect=lambda: next(monotonic_values)), \
             patch("lftp.lftp.time.sleep"):
            statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertEqual("mirror: Access failed", lftp._Lftp__pending_error)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        self.assertGreaterEqual(call_count["value"], 2)

    def test_run_command_status_poll_preserves_recovered_connecting_output(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.before = (
            b"jobs -v\n"
            b"[0] queue (sftp://someone:@localhost)\n"
            b"sftp://someone:@localhost/home/someone\n"
            b"Queue is running.\n"
            b"[1] pget -c /remote/a -o /local/\n"
            b"sftp://someone:@localhost/home/someone\n"
            b"/remote/a at 0 [Connecting...]\n"
        )
        process.after = pexpect.TIMEOUT
        process.delaybeforesend = 7
        process.delayafterread = 11
        process.send.return_value = None

        call_count = {"value": 0}

        def expect_side_effect(pattern, timeout):
            call_count["value"] += 1
            raise pexpect.exceptions.TIMEOUT("timeout")

        process.expect.side_effect = expect_side_effect
        lftp._Lftp__process = process

        monotonic_values = iter([0.0, 0.01, 1.01])
        with patch("lftp.lftp.time.monotonic", side_effect=lambda: next(monotonic_values)), \
             patch("lftp.lftp.time.sleep"):
            out = lftp._Lftp__run_command(
                "jobs -v",
                timeout_seconds=0,
                require_prompt_ready=False,
                status_poll=True
            )

        self.assertIn("[Connecting...]", out)
        self.assertGreaterEqual(call_count["value"], 2)

    def test_run_command_records_pending_error_for_common_failure_outputs(self):
        cases = [
            ("pget: Access failed: No such file (/remote/missing)", "No such file"),
            ("get: Access failed: No such file (/remote/missing)", "No such file"),
            ("mirror: Access failed: No such file (/remote/missing)", "No such file"),
            ("pget: Access failed: Wrong type", "Access failed"),
            ("mirror: Access failed: Wrong type", "Access failed"),
            ("mirror: Login failed: Login incorrect", "Login failed: Login incorrect"),
        ]
        for output, expected in cases:
            with self.subTest(output=output):
                lftp = Lftp.__new__(Lftp)
                lftp.logger = MagicMock()
                lftp._Lftp__expect_pattern = "prompt>"
                lftp._Lftp__timeout = 30
                lftp._Lftp__log_command_output = False
                lftp._Lftp__pending_error = None
                lftp._Lftp__last_command_timed_out = False
                process = MagicMock()
                process.isalive.return_value = True
                process.before = output.encode("utf8")
                process.after = pexpect.TIMEOUT
                process.delaybeforesend = 7
                process.delayafterread = 11
                process.sendline.return_value = None
                process.expect.side_effect = [pexpect.exceptions.TIMEOUT("timeout"), None]
                lftp._Lftp__process = process

                lftp._Lftp__run_command("mirror", require_prompt_ready=False)

                with self.assertRaises(LftpError) as ctx:
                    lftp.raise_pending_error()
                self.assertIn(expected, str(ctx.exception))

    def test_status_restores_process_read_delays_after_poll(self):
        lftp = self._build_status_poll_test_lftp()

        statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertEqual(7, lftp._Lftp__process.delaybeforesend)
        self.assertEqual(11, lftp._Lftp__process.delayafterread)

    def test_run_command_preserves_process_read_delays_when_not_status_poll(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b"command output"
        process.after = b"prompt>"
        process.delaybeforesend = 7
        process.delayafterread = 11
        process.expect.side_effect = [None, None]
        lftp._Lftp__process = process

        out = lftp._Lftp__run_command("ls")

        self.assertEqual("command output", out)
        self.assertEqual(7, process.delaybeforesend)
        self.assertEqual(11, process.delayafterread)

    def test_status_logs_bounded_summary_when_verbose(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__log_command_output = True
        lftp._Lftp__process.before = b"jobs -v\nvery long raw payload"

        statuses = lftp.status()

        self.assertEqual([], statuses)
        lftp.logger.debug.assert_not_called()

    def test_run_command_logs_verbose_output_when_not_status_poll(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = True
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b"raw payload"
        process.after = b"prompt>"
        process.expect.side_effect = [None, None]
        lftp._Lftp__process = process

        out = lftp._Lftp__run_command("ls")

        self.assertEqual("raw payload", out)
        self.assertTrue(any("command: b'ls'" in str(call.args[0]) for call in lftp.logger.debug.call_args_list if call.args))
        self.assertTrue(any("out (11 bytes):" in str(call.args[0]) for call in lftp.logger.debug.call_args_list if call.args))
        self.assertTrue(any("after: prompt>" in str(call.args[0]) for call in lftp.logger.debug.call_args_list if call.args))

    def test_status_uses_short_timeout_budget_for_jobs_command(self):
        lftp = self._build_test_lftp()
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.return_value = []

        statuses = lftp.status()

        self.assertEqual([], statuses)
        lftp._Lftp__run_command.assert_called_once_with(
            "jobs -v",
            timeout_seconds=0,
            require_prompt_ready=False,
            status_poll=True
        )

    def test_status_marks_poll_unhealthy_when_parser_error_is_suppressed(self):
        lftp = self._build_test_lftp()
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.side_effect = LftpJobStatusParserError("bad status")
        lftp._Lftp__consecutive_status_errors = 0
        lftp._Lftp__last_command_timed_out = False
        lftp._Lftp__last_status_poll_healthy = True

        statuses = lftp.status()

        self.assertIsNone(statuses)
        self.assertFalse(lftp.last_status_poll_healthy)

    def test_status_returns_none_when_parser_error_is_tolerated_during_connection_grace_retry(self):
        lftp = self._build_test_lftp()
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.side_effect = LftpJobStatusParserError("bad status")
        lftp._Lftp__consecutive_status_errors = 0
        lftp._Lftp__last_command_timed_out = False
        lftp._Lftp__last_status_poll_healthy = True
        lftp._Lftp__status_poll_needs_connection_grace = True

        statuses = lftp.status()

        self.assertIsNone(statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertEqual(2, lftp._Lftp__job_status_parser.parse.call_count)
        self.assertEqual(2, lftp._Lftp__run_command.call_count)

    def test_status_drops_stale_queue_snapshot_after_command_failure(self):
        lftp = self._build_test_lftp()
        lftp._Lftp__job_status_parser = LftpJobStatusParser()
        lftp._Lftp__run_command = MagicMock(return_value=(
            "[0] queue (sftp://someone:@localhost)\n"
            "sftp://someone:@localhost/home/someone\n"
            "Queue is running.\n"
            "Commands queued:\n"
            " 1. mirror -c /remote/c /local/\n"
            "mirror: Access failed: Wrong type\n"
        ))

        statuses = lftp.status()

        self.assertEqual([], statuses)
        lftp._Lftp__run_command.assert_called_once_with(
            "jobs -v",
            timeout_seconds=0,
            require_prompt_ready=False,
            status_poll=True
        )

    def test_status_ignores_command_failure_before_jobs_slice(self):
        lftp = self._build_test_lftp()
        lftp._Lftp__job_status_parser = LftpJobStatusParser()
        lftp._Lftp__run_command = MagicMock(return_value=(
            "mirror: Access failed: Wrong type\n"
            "jobs -v\n"
            "[0] queue (sftp://someone:@localhost)  -- 90 B/s\n"
            "sftp://someone:@localhost/home/someone\n"
            "Now executing: [1] mirror -c /tmp/test_lftp_rm_s6oau/remote/a /tmp/test_lftp_rm_s6oau/local/ -- 345/26M (0%) 90 B/s\n"
            "[1] mirror -c /tmp/test_lftp_rm_s6oau/remote/a /tmp/test_lftp_rm_s6oau/local/  -- 345/26M (0%) 90 B/s\n"
        ))

        statuses = lftp.status()

        self.assertEqual(1, len(statuses))
        self.assertEqual("a", statuses[0].name)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    def test_run_command_logs_warning_on_timeout(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b"harmless output"
        process.after = pexpect.TIMEOUT
        process.expect.side_effect = [None, pexpect.exceptions.TIMEOUT("timeout")]
        lftp._Lftp__process = process

        out = lftp._Lftp__run_command("ls")

        self.assertEqual("harmless output", out)
        lftp.logger.warning.assert_called_once_with("Lftp timeout exception")
        self.assertTrue(lftp._Lftp__last_command_timed_out)

    def test_ensure_prompt_ready_returns_when_prompt_is_ready(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        process = MagicMock()
        process.expect.return_value = None
        lftp._Lftp__process = process

        lftp._Lftp__ensure_prompt_ready("running command")

        process.expect.assert_called_once_with("prompt>", timeout=1)
        process.sendline.assert_not_called()

    def test_ensure_prompt_ready_recovers_once_after_initial_timeout(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        process = MagicMock()
        process.before = b"stale output"
        process.expect.side_effect = [pexpect.exceptions.TIMEOUT("timeout"), None]
        lftp._Lftp__process = process

        lftp._Lftp__ensure_prompt_ready("running command")

        self.assertEqual(
            [
                call("prompt>", timeout=1),
                call("prompt>", timeout=3),
            ],
            process.expect.call_args_list
        )
        process.sendline.assert_called_once_with()
        lftp.logger.warning.assert_not_called()

    def test_ensure_prompt_ready_raises_lftp_error_after_retry_timeout(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        process = MagicMock()
        process.before = b"stale output"
        process.expect.side_effect = [pexpect.exceptions.TIMEOUT("timeout"), pexpect.exceptions.TIMEOUT("timeout")]
        lftp._Lftp__process = process

        with self.assertRaises(LftpError) as ctx:
            lftp._Lftp__ensure_prompt_ready("running command")

        self.assertIn("not ready", str(ctx.exception))
        self.assertEqual(
            [
                call("prompt>", timeout=1),
                call("prompt>", timeout=3),
            ],
            process.expect.call_args_list
        )
        process.sendline.assert_called_once_with()
        lftp.logger.warning.assert_called_once_with("Lftp timeout exception")

    def test_run_command_recovers_prompt_readiness_after_retry_before_send(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.after = pexpect.TIMEOUT
        call_count = {"value": 0}

        def expect_side_effect(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                process.before = b"stale output"
                raise pexpect.exceptions.TIMEOUT("timeout")
            if call_count["value"] == 2:
                process.before = b""
                return None
            if call_count["value"] == 3:
                process.before = b"command output"
                return None
            raise AssertionError("Unexpected expect call")

        process.expect.side_effect = expect_side_effect
        lftp._Lftp__process = process

        out = lftp._Lftp__run_command("ls")

        self.assertEqual("command output", out)
        self.assertEqual([call(), call("ls")], process.sendline.call_args_list)
        lftp.logger.warning.assert_not_called()

    def test_kill_all_skips_prompt_readiness_probe(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__run_command = MagicMock()
        process = MagicMock()
        process.isalive.return_value = True
        lftp._Lftp__process = process

        lftp.kill_all()

        self.assertEqual(
            [
                call("queue -d *", require_prompt_ready=False),
                call("kill all", require_prompt_ready=False, timeout_seconds=0),
            ],
            lftp._Lftp__run_command.call_args_list
        )

    def test_run_command_logs_warning_on_error_recovery_timeout(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b"mirror: Access failed"
        process.after = pexpect.TIMEOUT
        process.expect.side_effect = [None, None, pexpect.exceptions.TIMEOUT("timeout")]
        lftp._Lftp__process = process

        out = lftp._Lftp__run_command("mirror")

        self.assertEqual("mirror: Access failed", out)
        self.assertEqual("mirror: Access failed", lftp._Lftp__pending_error)
        lftp.logger.warning.assert_called_once_with("Lftp timeout exception")


    def setUp(self):
        unit_only_methods = {
            "test_queue_uses_override_paths",
            "test_queue_command_breadcrumb_is_opt_in_and_opaque",
            "test_pty_queue_breadcrumb_requires_explicit_rule_and_records_no_payload",
            "test_pty_queue_breadcrumb_records_write_and_prompt_boundaries_without_payload",
            "test_pty_queue_breadcrumb_warning_mode_retains_send_failures_without_payload_reads",
            "test_pty_queue_breadcrumb_records_os_error_before_reraising",
            "test_queue_command_breadcrumb_includes_only_bounded_scale_metrics",
            "test_queue_command_breadcrumb_bounds_large_exact_exclusion_scale",
            "test_queue_command_breadcrumb_handles_unencodable_metric_input",
            "test_status_poll_breadcrumb_is_opt_in_and_joins_the_opaque_poll_token",
            "test_status_poll_breadcrumb_exposes_independent_boundary_outcomes",
            "test_private_status_frame_capture_is_disabled_before_any_file_io",
            "test_private_status_frame_capture_writes_capped_atomic_private_pair",
            "test_status_captures_private_frame_on_parser_failure_before_error_reporting",
            "test_status_success_does_not_consume_private_frame_capture",
            "test_status_poll_parser_failure_records_parser_family_and_preserves_result",
            "test_status_poll_trace_failure_does_not_change_unexpected_exception_propagation",
            "test_status_exception_families_use_only_the_approved_enum",
            "test_status_poll_breadcrumb_disabled_adds_no_trace_reads",
            "test_status_poll_breadcrumb_warning_policy_keeps_only_failure_phase",
            "test_status_poll_connection_grace_retry_keeps_correlation",
            "test_status_poll_connecting_recovery_records_prompt_ready",
            "test_queue_command_breadcrumb_warns_on_send_timeout_when_debug_is_disabled",
            "test_queue_forwards_only_a_valid_opaque_command_trace_flow",
            "test_queue_sidecar_breadcrumb_classifies_missing_and_is_gated",
            "test_queue_sidecar_breadcrumb_classifies_rejection_and_valid_artifact",
            "test_queue_sidecar_breadcrumb_disabled_preserves_dispatch",
            "test_queue_sidecar_breadcrumb_coalesces_across_interleaved_poll_event",
            "test_queue_sidecar_breadcrumb_keeps_valid_coverage_transition",
            "test_kill_matches_duplicate_names_by_remote_path",
            "test_set_skips_prompt_readiness_probe",
            "test_status_annotates_path_pairs_from_job_paths",
            "test_status_leaves_cross_pair_remote_and_local_matches_unscoped",
            "test_status_leaves_overlapping_or_reconfigured_roots_unscoped",
            "test_status_marks_poll_unhealthy_when_jobs_command_times_out",
            "test_status_marks_poll_unhealthy_when_jobs_command_eof",
            "test_status_marks_poll_unhealthy_when_jobs_command_raises_lftp_error",
            "test_status_marks_poll_unhealthy_when_queue_command_echo_leaks_into_snapshot",
            "test_status_marks_poll_unhealthy_when_jobs_command_echo_interleaves_with_progress",
            "test_status_marks_poll_unhealthy_when_jobs_command_raises_exception_pexpect",
            "test_status_marks_poll_unhealthy_when_jobs_command_raises_oserror",
            "test_status_poll_discards_stale_terminal_prompt_before_sending",
            "test_status_poll_terminal_backlog_is_unhealthy_and_does_not_send",
            "test_status_poll_terminal_backend_error_is_visible_without_sending",
            "test_status_poll_terminal_host_key_error_spanning_chunks_does_not_send",
            "test_status_poll_terminal_eof_is_unhealthy_without_sending",
            "test_status_logs_bounded_summary_when_verbose",
            "test_status_uses_short_timeout_budget_for_jobs_command",
            "test_run_command_logs_verbose_output_when_not_status_poll",
            "test_run_command_status_poll_exhausts_to_empty_snapshot",
            "test_run_command_preserves_process_read_delays_when_not_status_poll",
            "test_status_poll_preserves_pending_error_before_prompt_timeout",
            "test_run_command_status_poll_preserves_recovered_connecting_output",
            "test_run_command_records_pending_error_for_common_failure_outputs",
            "test_status_marks_poll_unhealthy_when_parser_error_is_suppressed",
            "test_status_drops_stale_queue_snapshot_after_command_failure",
            "test_status_ignores_command_failure_before_jobs_slice",
            "test_run_command_logs_warning_on_timeout",
            "test_ensure_prompt_ready_returns_when_prompt_is_ready",
            "test_ensure_prompt_ready_recovers_once_after_initial_timeout",
            "test_ensure_prompt_ready_raises_lftp_error_after_retry_timeout",
            "test_run_command_recovers_prompt_readiness_after_retry_before_send",
            "test_kill_all_skips_prompt_readiness_probe",
            "test_run_command_logs_warning_on_error_recovery_timeout",
            "test_queue_dir_uses_override_paths",
            "test_status_poll_loop_retries_until_deadline_on_timeouts",
            "test_status_restores_process_read_delays_after_poll",
            "test_status_returns_none_when_parser_error_is_tolerated_during_connection_grace_retry",
            "test_docker_runtime_user_ssh_config_guardrail",
            "test_net_socket_buffer",
        }
        if self._testMethodName in unit_only_methods:
            return
        if os.name == "nt":
            self.skipTest("Requires POSIX pexpect.spawn and /usr/bin/lftp")

        # Delete and recreate the local dir
        shutil.rmtree(os.path.join(TestLftp.temp_dir, "local"))
        os.mkdir(os.path.join(TestLftp.temp_dir, "local"))
        self.local_dir = os.path.join(TestLftp.temp_dir, "local")
        self.remote_dir = os.path.join(TestLftp.temp_dir, "remote")

        # Note: seedsynctest account must be set up. See DeveloperReadme.md for details
        self.host = "localhost"
        self.port = 22
        self.user = "seedsynctest"
        self.password = "seedsyncpass"

        # Default lftp instance - use key-based login
        self.lftp = Lftp(address=self.host, port=self.port, user=self.user, password=None)
        self.lftp.set_base_remote_dir_path(self.remote_dir)
        self.lftp.set_base_local_dir_path(self.local_dir)
        self.lftp.set_verbose_logging(True)

        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

    def tearDown(self):
        if not hasattr(self, "lftp"):
            return
        self.lftp.raise_pending_error()
        self.lftp.exit()

    @requires_live_ssh
    def test_pty_command_echo_is_disabled(self):
        self.assertFalse(self.lftp._Lftp__process.getecho())

    @requires_live_ssh
    def test_num_connections_per_dir_file(self):
        self.lftp.num_connections_per_dir_file = 5
        self.assertEqual(5, self.lftp.num_connections_per_dir_file)
        with self.assertRaises(ValueError):
            self.lftp.num_connections_per_dir_file = -1

    @requires_live_ssh
    def test_num_connections_per_root_file(self):
        self.lftp.num_connections_per_root_file = 5
        self.assertEqual(5, self.lftp.num_connections_per_root_file)
        with self.assertRaises(ValueError):
            self.lftp.num_connections_per_root_file = -1

    @requires_live_ssh
    def test_num_parallel_files(self):
        self.lftp.num_parallel_files = 5
        self.assertEqual(5, self.lftp.num_parallel_files)
        with self.assertRaises(ValueError):
            self.lftp.num_parallel_files = -1

    @requires_live_ssh
    def test_num_max_total_connections(self):
        self.lftp.num_max_total_connections = 5
        self.assertEqual(5, self.lftp.num_max_total_connections)
        self.lftp.num_max_total_connections = 0
        self.assertEqual(0, self.lftp.num_max_total_connections)
        with self.assertRaises(ValueError):
            self.lftp.num_max_total_connections = -1

    @requires_live_ssh
    def test_rate_limit(self):
        self.lftp.rate_limit = 500
        self.assertEqual("500", self.lftp.rate_limit)
        self.lftp.rate_limit = "2k"
        self.assertEqual("2k", self.lftp.rate_limit)
        self.lftp.rate_limit = "1M"
        self.assertEqual("1M", self.lftp.rate_limit)

    def test_net_socket_buffer(self):
        lftp = Lftp.__new__(Lftp)
        lftp._Lftp__run_command = MagicMock(return_value="")

        lftp.net_socket_buffer = 8388608

        lftp._Lftp__run_command.assert_called_once_with(
            "set net:socket-buffer 8388608",
            require_prompt_ready=False
        )
        lftp._Lftp__run_command.reset_mock()

        lftp.net_socket_buffer = "512K"

        lftp._Lftp__run_command.assert_called_once_with(
            "set net:socket-buffer 512K",
            require_prompt_ready=False
        )
        lftp._Lftp__run_command.reset_mock()

        lftp.net_socket_buffer = "2k"

        lftp._Lftp__run_command.assert_called_once_with(
            "set net:socket-buffer 2K",
            require_prompt_ready=False
        )
        lftp._Lftp__run_command.reset_mock()

        lftp.net_socket_buffer = ""

        lftp._Lftp__run_command.assert_not_called()

        with self.assertRaises(ConfigError):
            lftp.net_socket_buffer = "512KB"

    def test_xfer_verify_and_command(self):
        lftp = Lftp.__new__(Lftp)
        state = {
            "xfer_verify": False,
            "xfer_verify_command": "sha256sum",
        }

        def run_command(command, *args, **kwargs):
            if command == "set xfer:verify 1":
                state["xfer_verify"] = True
                return ""
            if command == "set xfer:verify 0":
                state["xfer_verify"] = False
                return ""
            if command == "set xfer:verify-command md5sum":
                state["xfer_verify_command"] = "md5sum"
                return ""
            if command == "set xfer:verify-command sha256sum":
                state["xfer_verify_command"] = "sha256sum"
                return ""
            if command == "set -a | grep xfer:verify-command":
                return "set xfer:verify-command {}".format(state["xfer_verify_command"])
            if command == "set -a | grep xfer:verify":
                return "set xfer:verify {}".format("true" if state["xfer_verify"] else "false")
            return ""

        lftp._Lftp__run_command = MagicMock(side_effect=run_command)

        lftp.xfer_verify = True
        self.assertTrue(lftp.xfer_verify)
        lftp.xfer_verify_command = "md5sum"
        self.assertEqual("md5sum", lftp.xfer_verify_command)
        lftp.xfer_verify = False
        self.assertFalse(lftp.xfer_verify)

        self.assertEqual(
            [
                ("set xfer:verify 1", {"require_prompt_ready": False}),
                ("set -a | grep xfer:verify", {}),
                ("set xfer:verify-command md5sum", {"require_prompt_ready": False}),
                ("set -a | grep xfer:verify-command", {}),
                ("set xfer:verify 0", {"require_prompt_ready": False}),
                ("set -a | grep xfer:verify", {}),
            ],
            [
                (call.args[0], call.kwargs)
                for call in lftp._Lftp__run_command.call_args_list
            ]
        )

    @requires_live_ssh
    def test_min_chunk_size(self):
        self.lftp.min_chunk_size = 500
        self.assertEqual("500", self.lftp.min_chunk_size)
        self.lftp.min_chunk_size = "2k"
        self.assertEqual("2k", self.lftp.min_chunk_size)
        self.lftp.min_chunk_size = "1M"
        self.assertEqual("1M", self.lftp.min_chunk_size)

    @requires_live_ssh
    def test_num_parallel_jobs(self):
        self.lftp.num_parallel_jobs = 5
        self.assertEqual(5, self.lftp.num_parallel_jobs)
        with self.assertRaises(ValueError):
            self.lftp.num_parallel_jobs = -1

    @requires_live_ssh
    def test_move_background_on_exit(self):
        self.lftp.move_background_on_exit = True
        self.assertEqual(True, self.lftp.move_background_on_exit)
        self.lftp.move_background_on_exit = False
        self.assertEqual(False, self.lftp.move_background_on_exit)

    @requires_live_ssh
    def test_use_temp_file(self):
        self.lftp.use_temp_file = True
        self.assertEqual(True, self.lftp.use_temp_file)
        self.lftp.use_temp_file = False
        self.assertEqual(False, self.lftp.use_temp_file)

    @requires_live_ssh
    def test_temp_file_name(self):
        self.lftp.temp_file_name = "*.lftp"
        self.assertEqual("*.lftp", self.lftp.temp_file_name)
        self.lftp.temp_file_name = "*.temp"
        self.assertEqual("*.temp", self.lftp.temp_file_name)

    @requires_live_ssh
    def test_sftp_auto_confirm(self):
        self.lftp.sftp_auto_confirm = True
        self.assertEqual(True, self.lftp.sftp_auto_confirm)
        self.lftp.sftp_auto_confirm = False
        self.assertEqual(False, self.lftp.sftp_auto_confirm)

    @requires_live_ssh
    def test_sftp_connect_program(self):
        self.lftp.sftp_connect_program = "program -a -f"
        self.assertEqual("\"program -a -f\"", self.lftp.sftp_connect_program)
        self.lftp.sftp_connect_program = "\"abc -d\""
        self.assertEqual("\"abc -d\"", self.lftp.sftp_connect_program)

    @requires_live_ssh
    def test_status_empty(self):
        statuses = self.lftp.status()
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_queue_file(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("c", False)
        while True:
            statuses = self.lftp.status()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("c", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.PGET, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    @requires_live_ssh
    def test_queue_dir(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("a", True)
        while True:
            statuses = self.lftp.status()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("a", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    @requires_live_ssh
    @pytest.mark.timeout(30)
    def test_status_poll_terminal_drain_keeps_419_exclusion_sftp_mirror_progressing(self):
        """Exercise the status-owner drain against the real local SFTP fixture.

        This deliberately has no reader outside ``Lftp``.  It samples queued
        PTY and SFTP response pipes without consuming them, then proves that
        authoritative ``jobs -v`` parsing and canonical local bytes continue
        advancing across the scheduled polls.
        """
        if not os.path.isdir("/proc"):
            self.skipTest("requires Linux /proc pipe inspection")
        try:
            import fcntl
            import termios
        except ImportError:
            self.skipTest("requires POSIX FIONREAD support")

        target_name = "terminal-drain-419"
        remote_target = os.path.join(self.remote_dir, target_name)
        os.mkdir(remote_target)
        payload = b"terminal-drain-fixture" * 4096
        for index in range(4):
            with open(os.path.join(remote_target, "payload-{:02d}.bin".format(index)), "wb") as handle:
                # Keep physical coverage active after the first long jobs-v
                # header, rather than allowing admission to finish the whole
                # fixture before the scheduled status window begins.
                for _ in range(384):
                    handle.write(payload)

        # Match the production-shaped 4/4/16 connection contract.  Exact
        # neutral leaf exclusions make ``jobs -v`` render the long header
        # without exposing any real names in the test evidence.
        self.lftp.num_parallel_files = 4
        self.lftp.num_connections_per_root_file = 4
        self.lftp.num_connections_per_dir_file = 4
        self.lftp.num_max_total_connections = 16
        self.lftp.rate_limit = "512K"
        self.lftp.set_verbose_logging(False)
        exclusions = [
            ExactPathExclusion("trusted-final-{:03d}-{}".format(index, "x" * 64))
            for index in range(419)
        ]

        original_run_command = self.lftp._Lftp__run_command
        commands = []

        def record_command(command, *args, **kwargs):
            commands.append((command, bool(kwargs.get("status_poll"))))
            return original_run_command(command, *args, **kwargs)

        self.lftp._Lftp__run_command = record_command
        self.addCleanup(setattr, self.lftp, "_Lftp__run_command", original_run_command)

        def fionread_fd(fd):
            value = bytearray(struct.calcsize("I"))
            fcntl.ioctl(fd, termios.FIONREAD, value, True)
            return struct.unpack("I", value)[0]

        def descendant_ssh_pids(parent_pid):
            pending = [parent_pid]
            descendants = []
            while pending:
                current = pending.pop()
                for entry in os.scandir("/proc"):
                    if not entry.name.isdecimal():
                        continue
                    try:
                        with open(os.path.join(entry.path, "status"), encoding="utf-8") as status_file:
                            fields = dict(
                                line.split(":", 1) for line in status_file if ":" in line
                            )
                        if int(fields.get("PPid", "-1").strip()) != current:
                            continue
                        pending.append(int(entry.name))
                        with open(os.path.join(entry.path, "comm"), encoding="utf-8") as comm_file:
                            if comm_file.read().strip() == "ssh":
                                descendants.append(int(entry.name))
                    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                        continue
            return descendants

        def linked_pipe_bytes(lftp_pid):
            try:
                ssh_pids = descendant_ssh_pids(lftp_pid)
                ssh_pipes = {
                    os.readlink(os.path.join("/proc", str(pid), "fd", fd))
                    for pid in ssh_pids for fd in os.listdir(os.path.join("/proc", str(pid), "fd"))
                    if os.path.islink(os.path.join("/proc", str(pid), "fd", fd))
                    and os.readlink(os.path.join("/proc", str(pid), "fd", fd)).startswith("pipe:[")
                }
                values = []
                for fd_name in os.listdir(os.path.join("/proc", str(lftp_pid), "fd")):
                    fd_path = os.path.join("/proc", str(lftp_pid), "fd", fd_name)
                    if os.readlink(fd_path) not in ssh_pipes:
                        continue
                    descriptor = None
                    try:
                        descriptor = os.open(fd_path, os.O_RDONLY | os.O_NONBLOCK)
                        values.append(fionread_fd(descriptor))
                    except OSError:
                        continue
                    finally:
                        if descriptor is not None:
                            os.close(descriptor)
                return tuple(values), len(ssh_pids)
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                return (), 0

        def canonical_local_bytes():
            """Return pget coverage plus allocated payload/sidecar bytes.

            LFTP may preallocate a sparse MIRROR target before its contents
            arrive.  A valid pget sidecar is the authoritative coverage
            signal; file allocation remains a structural secondary value.
            """
            root = os.path.join(self.local_dir, target_name)
            allocated_payload = 0
            allocated_sidecars = 0
            sidecar_coverage = 0
            for directory, _subdirs, names in os.walk(root):
                for name in names:
                    path = os.path.join(directory, name)
                    allocated_bytes = os.stat(path).st_blocks * 512
                    if name.endswith(".lftp-pget-status"):
                        allocated_sidecars += allocated_bytes
                        with open(path, "rb") as handle:
                            parsed = parse_lftp_pget_status_bytes(
                                handle.read(MAX_LFTP_PGET_STATUS_BYTES + 1)
                            )
                        self.assertIsNotNone(parsed)
                        sidecar_coverage += parsed.covered_size
                    else:
                        allocated_payload += allocated_bytes
            return sidecar_coverage, allocated_payload, allocated_sidecars

        self.lftp.queue(target_name, True, exclude_patterns=exclusions)
        mirror_commands = [command for command, status_poll in commands if not status_poll]
        self.assertEqual(1, len(mirror_commands))
        self.assertEqual(419, mirror_commands[0].count("--exclude "))

        process = self.lftp._Lftp__process
        samples = []
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline and len(samples) < 6:
            pty_before = fionread_fd(process.child_fd)
            pipe_before, ssh_count = linked_pipe_bytes(process.pid)
            statuses = self.lftp.status()
            covered_bytes, allocated_payload, sidecar_bytes = canonical_local_bytes()
            pty_after = fionread_fd(process.child_fd)
            pipe_after, _ = linked_pipe_bytes(process.pid)
            samples.append({
                "covered": covered_bytes,
                "allocated_payload": allocated_payload,
                "sidecars": sidecar_bytes,
                "pty_before": pty_before,
                "pty_after": pty_after,
                "pipe_before": pipe_before,
                "pipe_after": pipe_after,
                "ssh": ssh_count,
                "healthy": self.lftp._Lftp__last_status_poll_healthy,
                "membership": len(statuses or []),
            })
            time.sleep(0.9)

        self.assertTrue(samples, "no status samples")
        self.assertTrue(all(sample["healthy"] for sample in samples), samples)
        self.assertGreaterEqual(len(samples), 6, samples)
        self.assertTrue(any(sample["membership"] for sample in samples), samples)
        self.assertTrue(any(sample["ssh"] for sample in samples), samples)
        self.assertTrue(any(sample["pipe_before"] or sample["pipe_after"] for sample in samples), samples)
        progress_samples = [
            index for index in range(1, len(samples))
            if samples[index]["covered"] > samples[index - 1]["covered"]
        ]
        self.assertGreaterEqual(len(progress_samples), 2, samples)
        self.assertTrue(
            any(
                pipe_values and sum(pipe_values) < len(pipe_values) * 65536
                for index in progress_samples for pipe_values in (
                    samples[index]["pipe_before"], samples[index]["pipe_after"],
                )
            ),
            samples,
        )
        self.assertGreater(samples[-1]["covered"], samples[0]["covered"], samples)
        if os.environ.get("SEEDSYNC_LFTP_DRAIN_REPORT") == "1":
            print("TERMINAL_DRAIN_REPORT=" + json.dumps([
                {
                    "covered": sample["covered"],
                    "payload_allocated": sample["allocated_payload"],
                    "sidecar_allocated": sample["sidecars"],
                    "pipe_before_total": sum(sample["pipe_before"]),
                    "pipe_after_total": sum(sample["pipe_after"]),
                    "ssh": sample["ssh"],
                    "membership": sample["membership"],
                    "healthy": sample["healthy"],
                }
                for sample in samples
            ], sort_keys=True))
        # A final owner-issued jobs-v remains authoritative after the bounded
        # drain; no separate PTY reader is used by this test.
        final_statuses = self.lftp.status()
        self.assertTrue(final_statuses)
        status_commands = [command for command, status_poll in commands if status_poll]
        self.assertGreaterEqual(len(status_commands), len(samples) + 1)
        self.assertEqual({"jobs -v"}, set(status_commands))
        self.assertTrue(self.lftp._Lftp__last_status_poll_healthy)

    @requires_live_ssh
    def test_queue_file_with_spaces(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("d d", False)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("d d", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.PGET, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    @requires_live_ssh
    def test_queue_dir_with_spaces(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("e e", True)
        while True:
            statuses = self.lftp.status()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("e e", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    @requires_live_ssh
    def test_queue_file_with_unicode(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("üæÒ", False)
        while True:
            statuses = self.lftp.status()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("üæÒ", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.PGET, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    @requires_live_ssh
    def test_queue_dir_with_latin(self):
        self.lftp.rate_limit = 100  # so jobs don't finish right away
        self.lftp.queue("latin", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("latin", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)
        # Download over 100 bytes without errors
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            size_local = statuses[0].total_transfer_state.size_local
            if size_local and size_local > 100:
                break

    @requires_live_ssh
    def test_queue_dir_with_unicode(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("áßç", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("áßç", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    @requires_live_ssh
    def test_queue_num_parallel_jobs(self):
        self.lftp.num_parallel_jobs = 2
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("a", True)
        self.lftp.queue("c", False)
        self.lftp.queue("b", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 2:
                break
        self.assertEqual(3, len(statuses))
        # queued jobs
        self.assertEqual("b", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.QUEUED, statuses[0].state)
        # running jobs
        self.assertEqual("a", statuses[1].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[1].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[1].state)
        self.assertEqual("c", statuses[2].name)
        self.assertEqual(LftpJobStatus.Type.PGET, statuses[2].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[2].state)

    @requires_live_ssh
    def test_kill_all(self):
        self.lftp.num_parallel_jobs = 2
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("a", True)
        self.lftp.queue("c", False)
        self.lftp.queue("b", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 2:
                break
        self.assertEqual(3, len(statuses))
        self.lftp.kill_all()
        statuses = self.lftp.status()
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 0:
                break
        statuses = self.lftp.status()
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_kill_all_and_queue_again(self):
        self.lftp.num_parallel_jobs = 2
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("a", True)
        self.lftp.queue("c", False)
        self.lftp.queue("b", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 2:
                break
        self.assertEqual(3, len(statuses))
        self.lftp.kill_all()
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 0:
                break
        self.assertEqual(0, len(statuses))
        self.lftp.queue("b", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("b", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    @requires_live_ssh
    def test_kill_queued_job(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.num_parallel_jobs = 1
        self.lftp.queue("a", True)  # this job will run
        self.lftp.queue("b", True)  # this job will queue
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 1:
                break
        self.assertEqual(2, len(statuses))
        self.assertEqual("b", statuses[0].name)
        self.assertEqual(LftpJobStatus.State.QUEUED, statuses[0].state)
        self.assertEqual("a", statuses[1].name)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[1].state)
        self.assertEqual(True, self.lftp.kill("b"))
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("a", statuses[0].name)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

    @requires_live_ssh
    def test_kill_running_job(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("a", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("a", statuses[0].name)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)
        self.assertEqual(True, self.lftp.kill("a"))
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 0:
                break
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_kill_missing_job(self):
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.queue("a", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("a", statuses[0].name)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)
        self.assertEqual(False, self.lftp.kill("b"))
        self.assertEqual(True, self.lftp.kill("a"))
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 0:
                break
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_kill_job_1(self):
        """Queued and running jobs killed one at a time"""
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.num_parallel_jobs = 2
        # 2 jobs running, 3 jobs queued
        self.lftp.queue("a", True)  # running
        self.lftp.queue("d d", False)  # running
        self.lftp.queue("b", True)  # queued
        self.lftp.queue("c", False)  # queued
        self.lftp.queue("e e", True)  # queued

        Q = LftpJobStatus.State.QUEUED
        R = LftpJobStatus.State.RUNNING

        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 4:
                break
        self.assertEqual(5, len(statuses))
        self.assertEqual(["b", "c", "e e", "a", "d d"], [s.name for s in statuses])
        self.assertEqual([Q, Q, Q, R, R], [s.state for s in statuses])

        # kill the queued jobs one-by-one
        self.lftp.kill("c")
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 4:
                break
        self.assertEqual(4, len(statuses))
        self.assertEqual(["b", "e e", "a", "d d"], [s.name for s in statuses])
        self.assertEqual([Q, Q, R, R], [s.state for s in statuses])
        self.lftp.kill("b")
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 3:
                break
        self.assertEqual(3, len(statuses))
        self.assertEqual(["e e", "a", "d d"], [s.name for s in statuses])
        self.assertEqual([Q, R, R], [s.state for s in statuses])
        self.lftp.kill("e e")
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 2:
                break
        self.assertEqual(2, len(statuses))
        self.assertEqual(["a", "d d"], [s.name for s in statuses])
        self.assertEqual([R, R], [s.state for s in statuses])
        # kill the running jobs one-by-one
        self.lftp.kill("d d")
        statuses = self.lftp.status()
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 1:
                break
        self.assertEqual("a", statuses[0].name)
        self.assertEqual(R, statuses[0].state)
        self.lftp.kill("a")
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 0:
                break
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_queued_and_kill_jobs_1(self):
        """Queued and running jobs killed one at a time"""
        self.lftp.rate_limit = 10  # so jobs don't finish right away
        self.lftp.num_parallel_jobs = 2

        Q = LftpJobStatus.State.QUEUED
        R = LftpJobStatus.State.RUNNING

        # add 3 jobs - a, dd, b
        self.lftp.queue("a", True)
        self.lftp.queue("d d", False)
        self.lftp.queue("b", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 2:
                break
        self.assertEqual(3, len(statuses))
        self.assertEqual(["b", "a", "d d"], [s.name for s in statuses])
        self.assertEqual([Q, R, R], [s.state for s in statuses])

        # remove dd (running)
        self.lftp.kill("d d")
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 2:
                break
        self.assertEqual(2, len(statuses))
        self.assertEqual(["a", "b"], [s.name for s in statuses])
        self.assertEqual([R, R], [s.state for s in statuses])

        # remove a (running)
        self.lftp.kill("a")
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 1:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual(["b"], [s.name for s in statuses])
        self.assertEqual([R], [s.state for s in statuses])

        # add 3 jobs - c, ee, a
        self.lftp.queue("c", False)
        self.lftp.queue("e e", True)
        self.lftp.queue("a", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 4:
                break
        self.assertEqual(4, len(statuses))
        self.assertEqual(["e e", "a", "b", "c"], [s.name for s in statuses])
        self.assertEqual([Q, Q, R, R], [s.state for s in statuses])

        # remove ee (queued) and b (running)
        self.lftp.kill("e e")
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 3:
                break
        self.assertEqual(3, len(statuses))
        self.assertEqual(["a", "b", "c"], [s.name for s in statuses])
        self.assertEqual([Q, R, R], [s.state for s in statuses])
        self.lftp.kill("b")
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 2:
                break
        self.assertEqual(2, len(statuses))
        self.assertEqual(["c", "a"], [s.name for s in statuses])
        self.assertEqual([R, R], [s.state for s in statuses])

        # remove all
        self.lftp.kill_all()
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 0:
                break
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_queue_dir_wrong_file_type(self):
        """check that queueing a dir with PGET fails gracefully"""
        # passing dir as a file
        print("Queuing dir as a file")
        self.lftp.queue("a", False)
        # wait for command to fail
        while True:
            statuses = self.lftp.status()
            if len(statuses) == 0:
                break
        # next status should be empty
        print("Getting empty status")
        statuses = self.lftp.status()
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_queue_file_wrong_file_type(self):
        """check that queueing a file with MIRROR fails gracefully"""
        # passing file as a dir
        print("Queuing file as a dir")
        self.lftp.queue("c", True)
        # wait for command to fail
        while True:
            statuses = self.lftp.status()
            if len(statuses) == 0:
                break
        # next status should be empty
        print("Getting empty status")
        statuses = self.lftp.status()
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_queue_missing_file(self):
        """check that queueing non-existing file fails gracefully"""
        self.lftp.queue("non-existing-file", False)
        # wait for command to fail
        while True:
            statuses = self.lftp.status()
            if len(statuses) == 0:
                break
        # next status should be empty
        print("Getting empty status")
        statuses = self.lftp.status()
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_queue_missing_dir(self):
        """check that queueing non-existing directory fails gracefully"""

        self.lftp.queue("non-existing-folder", True)
        # wait for command to fail
        while True:
            statuses = self.lftp.status()
            if len(statuses) == 0:
                break
        # next status should be empty
        print("Getting empty status")
        statuses = self.lftp.status()
        self.assertEqual(0, len(statuses))

    @requires_live_ssh
    def test_password_auth(self):
        # exit the default instance
        self.lftp.exit()

        self.lftp = Lftp(address=self.host, port=self.port, user=self.user, password=self.password)
        self.lftp.set_base_remote_dir_path(self.remote_dir)
        self.lftp.set_base_local_dir_path(self.local_dir)
        self.lftp.set_verbose_logging(True)

        # Disable key-based auth
        program = self.lftp.sftp_connect_program
        program = program[:-1]  # remove the end double-quote
        program += " -oPubkeyAuthentication=no\""
        self.lftp.sftp_connect_program = program

        self.lftp.queue("a", True)
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("a", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

        # Wait for empty status
        while True:
            statuses = self.lftp.status()
            self.lftp.raise_pending_error()
            if len(statuses) == 0:
                break
        self.lftp.raise_pending_error()

    @pytest.mark.timeout(15)
    @requires_live_ssh
    def test_error_bad_password(self):
        # exit the default instance
        self.lftp.exit()

        self.lftp = Lftp(address=self.host, port=self.port, user=self.user, password="wrong password")
        self.lftp.set_base_remote_dir_path(self.remote_dir)
        self.lftp.set_base_local_dir_path(self.local_dir)
        self.lftp.set_verbose_logging(True)
        self.lftp.rate_limit = 10  # so jobs don't finish right away

        # Disable key-based auth
        program = self.lftp.sftp_connect_program
        program = program[:-1]  # remove the end double-quote
        program += " -oPubkeyAuthentication=no\""
        self.lftp.sftp_connect_program = program

        self.lftp.queue("a", True)
        while True:
            statuses = self.lftp.status()
            if len(statuses) > 0:
                break
        self.assertEqual(1, len(statuses))
        self.assertEqual("a", statuses[0].name)
        self.assertEqual(LftpJobStatus.Type.MIRROR, statuses[0].type)
        self.assertEqual(LftpJobStatus.State.RUNNING, statuses[0].state)

        # Wait for empty status
        while True:
            statuses = self.lftp.status()
            if len(statuses) == 0:
                break

    def test_docker_runtime_user_ssh_config_guardrail(self):
        repo_root = None
        dockerfile_relpath = Path("src/docker/build/docker-image/Dockerfile")
        for base_path in Path(__file__).resolve().parents:
            candidate = base_path / dockerfile_relpath
            if candidate.is_file() and (base_path / "Makefile").is_file():
                repo_root = base_path
                break

        if repo_root is None:
            self.skipTest("Runtime Dockerfile is unavailable in this test layout; skipping SSH guardrail assertions.")

        dockerfile = repo_root / dockerfile_relpath
        contents = dockerfile.read_text(encoding="utf-8")
        entrypoint = repo_root / Path("src/docker/build/docker-image/entrypoint.sh")
        entrypoint_contents = entrypoint.read_text(encoding="utf-8")
        compose = repo_root / Path("compose.local.yml")
        compose_contents = compose.read_text(encoding="utf-8")

        self.assertIn("mkdir -p /home/seedsync/.ssh", contents)
        self.assertIn("StrictHostKeyChecking accept-new", contents)
        self.assertIn("chmod 600 /home/seedsync/.ssh/config", contents)
        self.assertIn("mkdir /staging", contents)
        self.assertIn("chown seedsync:seedsync /staging", contents)
        self.assertIn(
            'FROM seedsync_run_python AS seedsync_run\n'
            'ARG SEEDSYNC_DEBUG_AUTHORITY_TIMEOUT_SECS=""\n'
            'ENV INCOMING_RECOVERY_EXPERIMENTAL_AUTHORITY_TIMEOUT_SECS="${SEEDSYNC_DEBUG_AUTHORITY_TIMEOUT_SECS}"',
            contents,
        )
        self.assertIn('VOLUME [ "/config", "/downloads" ]', contents)
        self.assertIn("RUN /scripts/entrypoint.sh --bootstrap-default-config", contents)
        self.assertIn("        tini \\", contents)
        self.assertIn(
            'exec setpriv --reuid="$USER_ID" --regid="$GROUP_ID" --clear-groups -- tini -g -- bash -lc',
            entrypoint_contents,
        )
        self.assertNotIn("setup_default_config.sh", contents)
        self.assertNotIn("run_as_user", contents)
        self.assertNotIn("/usr/local/sbin/ssh", contents)
        self.assertNotIn("/usr/local/sbin/scp", contents)
        self.assertIn('SETTINGS_FILE="${CONFIG_DIR}/settings.cfg"', entrypoint_contents)
        self.assertIn('SCRIPT_PATH="/app/python/seedsync.py"', entrypoint_contents)
        self.assertIn('DEFAULT_LOCAL_PATH="/downloads/"', entrypoint_contents)
        self.assertIn(
            'DEFAULT_BROWSER_HANDOVER_RECOVERY_VERSION="${SEEDSYNC_BROWSER_HANDOVER_RECOVERY_VERSION:-}"',
            entrypoint_contents,
        )
        self.assertIn("ensure_ssh_host_key_config()", entrypoint_contents)
        self.assertIn('safe_chown "home SSH config" "${ssh_config}"', entrypoint_contents)
        self.assertIn("unset BASH_ENV ENV", entrypoint_contents)
        self.assertLess(
            entrypoint_contents.index('SETTINGS_FILE="${CONFIG_DIR}/settings.cfg"'),
            entrypoint_contents.index("bootstrap_default_config()"),
        )
        self.assertLess(
            entrypoint_contents.index('SCRIPT_PATH="/app/python/seedsync.py"'),
            entrypoint_contents.index("bootstrap_default_config()"),
        )
        self.assertLess(
            entrypoint_contents.index(
                'DEFAULT_BROWSER_HANDOVER_RECOVERY_VERSION="${SEEDSYNC_BROWSER_HANDOVER_RECOVERY_VERSION:-}"'
            ),
            entrypoint_contents.index("bootstrap_default_config()"),
        )
        self.assertLess(
            entrypoint_contents.index("ensure_ssh_host_key_config()"),
            entrypoint_contents.index("unset BASH_ENV ENV"),
        )
        self.assertIn("bootstrap_default_config()", entrypoint_contents)
        self.assertIn('local ssh_config="${USER_HOME}/.ssh/config"', entrypoint_contents)
        self.assertIn("if [ ! -f \"${ssh_config}\" ]; then", entrypoint_contents)
        self.assertIn("elif ! grep -Eq '^[[:space:]]*StrictHostKeyChecking[[:space:]]+' \"${ssh_config}\"; then", entrypoint_contents)
        self.assertIn('printf \'\\n%s\\n\' "StrictHostKeyChecking accept-new" >> "${ssh_config}"', entrypoint_contents)
        self.assertIn('safe_chown "home SSH config" "${ssh_config}"', entrypoint_contents)
        self.assertNotIn('printf \'%s\\n\' "StrictHostKeyChecking accept-new" > "${USER_HOME}/.ssh/config"', entrypoint_contents)
        self.assertNotIn("required_runtime_roots()", entrypoint_contents)
        self.assertNotIn("PathPairManager", entrypoint_contents)
        self.assertNotIn("prepare_required_runtime_root()", entrypoint_contents)
        self.assertNotIn("check_writable_path \"$DOWNLOADS_DIR\"", entrypoint_contents)
        self.assertIn('safe_chown "downloads directory" "$DOWNLOADS_DIR"', entrypoint_contents)
        self.assertIn('safe_chown "staging directory" /staging', entrypoint_contents)
        self.assertIn("prepare_config_root()", entrypoint_contents)
        self.assertIn("os.O_DIRECTORY | os.O_NOFOLLOW", entrypoint_contents)
        self.assertIn("os.fchown(root_fd, uid, gid)", entrypoint_contents)
        self.assertIn("os.fchmod(root_fd, 0o700)", entrypoint_contents)
        self.assertIn("revoked_mode = 0o500 if legacy_mode else 0", entrypoint_contents)
        self.assertIn("os.fchmod(root_fd, revoked_mode)", entrypoint_contents)
        self.assertIn("require_revoked_root_state", entrypoint_contents)
        self.assertIn("require_admitted_root_owner", entrypoint_contents)
        self.assertIn("stat.S_IMODE(root_info.st_mode) & 0o022", entrypoint_contents)
        self.assertIn("SEEDSYNC_CONFIG_ROOT_TEST_DELAY_SECONDS", entrypoint_contents)
        self.assertIn("require_same_directory_identity", entrypoint_contents)
        self.assertIn("info.st_nlink != 1", entrypoint_contents)
        self.assertIn("os.fchown(file_fd, uid, gid)", entrypoint_contents)
        self.assertIn("require_same_regular_identity", entrypoint_contents)
        self.assertIn('re.sub(r"\\\\([0-7]{3})"', entrypoint_contents)
        self.assertIn("validate_tree(root_fd, root_info.st_dev)", entrypoint_contents)
        self.assertIn("repair_tree(root_fd, root_info.st_dev)", entrypoint_contents)
        self.assertLess(
            entrypoint_contents.index("require_admitted_root_owner(root_info)"),
            entrypoint_contents.index("os.fchmod(root_fd, revoked_mode)"),
        )
        self.assertLess(
            entrypoint_contents.index("require_revoked_root_state(root_fd, root_info, root_info.st_uid, root_info.st_gid, revoked_mode, \"after access revocation\")"),
            entrypoint_contents.index("validate_tree(root_fd, root_info.st_dev)"),
        )
        self.assertIn('LEGACY_NONROOT_MODE=1', entrypoint_contents)
        self.assertIn("configure_legacy_nss_identity()", entrypoint_contents)
        self.assertIn('export NSS_WRAPPER_PASSWD="$passwd_file"', entrypoint_contents)
        self.assertIn('export NSS_WRAPPER_GROUP="$group_file"', entrypoint_contents)
        self.assertIn('libnss_wrapper.so', entrypoint_contents)
        self.assertIn('legacy_unraid_filesystem = "fuse.shfs"', entrypoint_contents)
        self.assertIn('PUID/PGID must match Docker --user', entrypoint_contents)
        self.assertIn('mode_label = "legacy-nonroot" if legacy_mode else "strict"', entrypoint_contents)
        self.assertIn('export TMPDIR="$RUNTIME_TMP_DIR"', entrypoint_contents)
        self.assertIn('safe_chown_recursive "runtime temporary directory" "$RUNTIME_TMP_DIR"', entrypoint_contents)
        self.assertIn('if [ "${1:-}" = "--prepare-config-root" ]; then', entrypoint_contents)
        self.assertLess(
            entrypoint_contents.index('if [ "${1:-}" = "--prepare-config-root" ]; then'),
            entrypoint_contents.index('mkdir -p "$CONFIG_DIR"'),
        )
        self.assertNotIn('safe_chown_recursive "config directory"', entrypoint_contents)
        entrypoint_contract = repo_root / "src/docker/test/entrypoint_config_contract.sh"
        self.assertTrue(entrypoint_contract.is_file())
        path_pair_roots_contract = repo_root / "src/docker/test/entrypoint_path_pair_roots_contract.sh"
        self.assertTrue(path_pair_roots_contract.is_file())
        path_pair_roots_contract_contents = path_pair_roots_contract.read_text(encoding="utf-8")
        self.assertIn("no-downloads", path_pair_roots_contract_contents)
        self.assertIn("legacy-downloads", path_pair_roots_contract_contents)
        self.assertIn("timeout 30s docker run", path_pair_roots_contract_contents)
        contract_contents = entrypoint_contract.read_text(encoding="utf-8")
        self.assertIn("chmod 0777", contract_contents)
        self.assertIn("windows-drvfs-negative", contract_contents)
        self.assertIn("root-link-negative", contract_contents)
        self.assertIn("root-file-negative", contract_contents)
        self.assertIn("nested-link-negative", contract_contents)
        self.assertIn("nested-mount-negative", contract_contents)
        self.assertIn("space-mountinfo", contract_contents)
        self.assertIn("hard-link-negative", contract_contents)
        self.assertIn("runtime-owner-idempotence", contract_contents)
        self.assertIn("wrong-owner-negative", contract_contents)
        self.assertIn("unsafe-root-config", contract_contents)
        self.assertIn("unsafe-runtime-config", contract_contents)
        self.assertIn("legacy-nonroot-first", contract_contents)
        self.assertIn("pwd.getpwuid(os.getuid())", contract_contents)
        self.assertIn("['ssh', '-G', 'example.invalid']", contract_contents)
        self.assertIn("legacy-nonroot-id-mismatch", contract_contents)
        self.assertIn("legacy-nonroot-wrong-owner-negative", contract_contents)
        self.assertIn("barrier-attacker", contract_contents)
        self.assertIn("POSIX_VOLUME", contract_contents)
        self.assertIn('mktemp "$path/.seedsync_write_test.XXXXXX"', entrypoint_contents)
        self.assertIn('rm -f -- "$test_file"', entrypoint_contents)
        self.assertNotIn('local test_file="${path}/.seedsync_write_test"', entrypoint_contents)
        self.assertNotIn("touch '$test_file' && rm '$test_file'", entrypoint_contents)
        self.assertNotIn("if mountpoint -q /staging 2>/dev/null; then", entrypoint_contents)
        self.assertIn("ERROR: invalid UMASK value", entrypoint_contents)
        self.assertNotIn("setup_default_config.sh", compose_contents)
        self.assertIn("set_general_option config_api_redact_remote_details False", compose_contents)
        self.assertNotIn("trusted_browser_bootstrap_remote_addrs", compose_contents)
        self.assertIn("remote_python_path = python3", compose_contents)



class TestIncomingRecoveryLftpDiagnostics(unittest.TestCase):
    def test_status_observation_keeps_multiline_terminal_drain_shape(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process._buffer.write(
            "jobs -v\n[0] queue (sftp://private@host)\nsftp://private@host/root\n"
            "[0] Done (queue (sftp://private@host))\n"
        )
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            self.assertEqual([], lftp.status())

        observation = lftp.incoming_recovery_last_status_observation
        self.assertEqual("marked", observation["pre_send_drain_shape"])
        self.assertTrue(observation["pre_send_drain_queue_done"])
        self.assertTrue(observation["pre_send_drain_prompt_or_echo"])
        self.assertFalse(observation["pre_send_drain_job_or_progress"])
        self.assertEqual("empty_or_prompt", observation["status_result_shape"])
        self.assertEqual("reached", observation["post_send_prompt"])
        self.assertNotIn("private", repr(observation))

    def test_status_observation_classifies_post_send_frames(self):
        cases = (
            ("", "empty_or_prompt"),
            (
                "jobs -v\n[0] queue (sftp://private@host)\nsftp://private@host/root\n"
                "[0] Done (queue (sftp://private@host))\n", "queue_done",
            ),
            ("jobs -v\nprivate unexpected frame\n", "ambiguous"),
        )
        for output, expected in cases:
            with self.subTest(expected=expected):
                lftp = TestLftp._build_status_poll_test_lftp()
                process = lftp._Lftp__process

                def prompt(*_args, **_kwargs):
                    process.before = output
                    return 0

                process.expect.side_effect = prompt
                with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
                    self.assertEqual([], lftp.status())
                observation = lftp.incoming_recovery_last_status_observation
                self.assertEqual(expected, observation["status_result_shape"])
                self.assertEqual("empty", observation["pre_send_drain_shape"])
                self.assertEqual("reached", observation["post_send_prompt"])
                self.assertNotIn("private", repr(observation))

    def test_status_observation_accumulates_real_grace_drain_and_prompt(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process._buffer.write("[0] Done (queue (sftp://private@host))\n")
        process.before = "Connecting..."
        process.expect.side_effect = [
            pexpect.exceptions.TIMEOUT("initial prompt"), 0, 0,
        ]
        with patch.object(lftp_mod.time, "monotonic", side_effect=[0, 2, 3]):
            with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
                self.assertEqual([], lftp.status())

        observation = lftp.incoming_recovery_last_status_observation
        self.assertEqual("connection_grace", observation["status_recovery"])
        self.assertTrue(observation["pre_send_drain_queue_done"])
        self.assertEqual("reached", observation["post_send_prompt"])
        self.assertEqual(3, process.expect.call_count)
        self.assertGreaterEqual(process.expect.call_args_list[1].kwargs["timeout"], 5)
        self.assertNotIn("private", repr(observation))

    def test_status_drain_does_not_classify_when_gate_is_off(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process._buffer.write("private discarded terminal frame")
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "599"}), \
                patch.object(lftp_mod.re, "search", side_effect=AssertionError("gate-off marker scan")):
            self.assertEqual([], lftp.status())
        self.assertEqual({}, lftp.incoming_recovery_last_status_observation)

    def test_status_drain_keeps_existing_error_detection_when_gate_is_off(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process._buffer.write("Login failed: private")
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "599"}):
            self.assertEqual([], lftp.status())
        process.send.assert_not_called()
        self.assertEqual("command_error", lftp.last_status_poll_failure_reason)
        self.assertEqual("Lftp status terminal reported a backend error", lftp._Lftp__pending_error)
        self.assertEqual({}, lftp.incoming_recovery_last_status_observation)

    def test_status_drain_keeps_existing_host_key_detection_when_gate_is_off(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        process = lftp._Lftp__process
        process.read_nonblocking.side_effect = [
            "The authenticity of ", "host private cannot be established",
        ]
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "599"}):
            self.assertEqual([], lftp.status())
        process.send.assert_not_called()
        self.assertEqual("command_error", lftp.last_status_poll_failure_reason)
        self.assertEqual("Lftp status terminal reported a backend error", lftp._Lftp__pending_error)
        self.assertEqual({}, lftp.incoming_recovery_last_status_observation)

    def test_status_observation_binds_prior_timeout_to_empty_grace_result(self):
        lftp = TestLftp._build_test_lftp()
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.return_value = []
        lftp._Lftp__status_poll_needs_connection_grace = True

        def command(*_args, **_kwargs):
            if lftp._Lftp__run_command.call_count == 1:
                lftp._Lftp__last_command_timed_out = True
                return "Connecting..."
            lftp._Lftp__last_command_timed_out = False
            return ""

        lftp._Lftp__run_command.side_effect = command
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            self.assertEqual([], lftp.status())

        observation = lftp.incoming_recovery_last_status_observation
        self.assertEqual("empty_or_prompt", observation["status_result_shape"])
        self.assertEqual("connection_grace", observation["status_recovery"])
        self.assertTrue(observation["status_preceded_timeout"])
        self.assertTrue(lftp.last_status_poll_healthy)

    def test_status_result_shapes_are_fixed_and_content_free(self):
        cases = (
            ("", [], "empty_or_prompt"),
            ("[7] Done (queue (sftp://private))", [], "queue_done"),
            ("[7] Done (queue (sftp://private))\n[8] mirror -c -- 1/2 (50%)", [], "job_present"),
            ("private transfer progress", [], "ambiguous"),
            ("private transfer progress", [object()], "job_present"),
            ("private parser frame", None, "parse_error"),
        )
        for output, statuses, expected in cases:
            with self.subTest(expected=expected):
                result = lftp_mod._incoming_recovery_status_result_shape(output, statuses)
                self.assertEqual(expected, result)
                self.assertNotIn("private", result)

    def test_status_observation_preserves_parser_failure_through_grace(self):
        lftp = TestLftp._build_test_lftp()
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.side_effect = [
            LftpJobStatusParserError("private parser detail"), [],
        ]
        lftp._Lftp__consecutive_status_errors = 0
        lftp._Lftp__status_poll_needs_connection_grace = True
        lftp._Lftp__run_command.side_effect = ["private frame", ""]
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            self.assertEqual([], lftp.status())

        observation = lftp.incoming_recovery_last_status_observation
        self.assertEqual("empty_or_prompt", observation["status_result_shape"])
        self.assertEqual("connection_grace", observation["status_recovery"])
        self.assertEqual("parser_error", lftp.last_status_poll_failure_reason)

    def test_status_observation_is_absent_when_exact_gate_is_off(self):
        lftp = TestLftp._build_test_lftp()
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.return_value = []
        lftp._Lftp__last_incoming_recovery_status_observation = {
            "status_result_shape": "empty_or_prompt",
        }
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "599"}):
            self.assertEqual([], lftp.status())
        self.assertEqual({}, lftp.incoming_recovery_last_status_observation)
        self.assertTrue(lftp.last_status_poll_healthy)

    def test_child_record_does_not_read_pexpect_buffer_property(self):
        class Process:
            pid = 1234
            exitstatus = None
            signalstatus = None

            def __init__(self):
                self._buffer = BytesIO(b"unread")

            @property
            def buffer(self):
                raise AssertionError("compatibility property must not be read")

            @staticmethod
            def isalive():
                return True

        records = []
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            lftp_mod._incoming_recovery_child_record(
                lambda event, fields: records.append((event, fields)), Process(), phase="prompt",
            )
        self.assertEqual("private_buffer", records[0][1]["read_buffer_source"])
        self.assertEqual(1234, records[0][1]["process_pid"])

    def test_child_record_timeout_sentinel_still_counts_private_buffer(self):
        process = MagicMock()
        process.isalive.return_value = True
        process.pid = 1234
        process.exitstatus = None
        process.signalstatus = None
        process.before = pexpect.exceptions.TIMEOUT("public before unavailable")
        process.after = pexpect.exceptions.TIMEOUT("public after unavailable")
        process.buffer = pexpect.exceptions.TIMEOUT("public buffer unavailable")
        process._buffer = BytesIO(b"unread PTY bytes")
        records = []
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            lftp_mod._incoming_recovery_child_record(
                lambda event, fields: records.append((event, fields)), process,
                error=pexpect.exceptions.TIMEOUT("prompt timeout"),
                phase="prompt", started_at=time.monotonic() - 0.01, alive_before=False,
            )
        fields = records[0][1]
        self.assertEqual("timeout", fields["classification"])
        self.assertEqual("prompt", fields["phase"])
        self.assertEqual("private_buffer", fields["read_buffer_source"])
        self.assertEqual("1-127", fields["read_buffer_byte_length_bucket"])
        self.assertEqual((False, True), (fields["process_alive_before"], fields["process_alive_after"]))
        self.assertEqual("alive", fields["reaped"])
        self.assertEqual(1234, fields["process_pid"])
        self.assertNotIn("process_start_identity", fields)

    def test_status_binds_one_successful_empty_poll_to_existing_recorder(self):
        status_lftp = TestLftp._build_status_poll_test_lftp()
        records = []
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            self.assertEqual([], status_lftp.status(
                diagnostic_recorder=lambda event, fields: records.append((event, fields)),
            ))
        self.assertEqual(["lftp_child"], [event for event, _ in records])
        fields = records[0][1]
        self.assertEqual("status", fields["phase"])
        self.assertEqual("status", fields["command_kind"])
        self.assertEqual("none", fields["classification"])
        self.assertEqual("healthy", fields["status_health"])
        self.assertEqual("0", fields["status_count_bucket"])
        self.assertNotIn("output", fields)

    def test_queue_recorder_captures_one_command_outcome_before_same_flow_status(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        records = []
        recorder = lambda event, fields: records.append((event, fields))
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            lftp.queue("private.bin", False, diagnostic_recorder=recorder)
            self.assertEqual([], lftp.status(diagnostic_recorder=recorder))

        queue_records = [fields for event, fields in records if fields.get("command_kind") == "queue"]
        status_records = [fields for event, fields in records if fields.get("command_kind") == "status"]
        self.assertEqual(1, len(queue_records))
        self.assertEqual("success", queue_records[0]["command_outcome"])
        self.assertEqual("prompt", queue_records[0]["phase"])
        self.assertEqual(1, len(status_records))
        self.assertEqual("status", status_records[0]["command_kind"])
        self.assertEqual("healthy", status_records[0]["status_health"])

    def test_queue_recorder_captures_one_prompt_timeout_outcome(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.TIMEOUT("prompt timeout")
        records = []
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            lftp.queue(
                "private.bin", False,
                diagnostic_recorder=lambda event, fields: records.append((event, fields)),
            )

        queue_records = [fields for event, fields in records if fields.get("command_kind") == "queue"]
        self.assertEqual(1, len(queue_records))
        self.assertEqual("prompt_timeout", queue_records[0]["command_outcome"])
        self.assertEqual("timeout", queue_records[0]["classification"])

    def test_queue_recorder_captures_fixed_eof_and_backend_error_outcomes(self):
        cases = (
            ("eof", pexpect.exceptions.EOF("terminal eof"), "eof"),
            ("error", None, "command_error"),
        )
        for expected_outcome, expect_side_effect, expected_classification in cases:
            with self.subTest(expected_outcome=expected_outcome):
                lftp = TestLftp._build_status_poll_test_lftp()
                if expect_side_effect is not None:
                    lftp._Lftp__process.expect.side_effect = expect_side_effect
                else:
                    lftp._Lftp__process.before = "Login failed: private backend"
                records = []
                with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
                    if expected_outcome == "eof":
                        with self.assertRaises(LftpError):
                            lftp.queue(
                                "private.bin", False,
                                diagnostic_recorder=lambda event, fields: records.append((event, fields)),
                            )
                    else:
                        lftp.queue(
                            "private.bin", False,
                            diagnostic_recorder=lambda event, fields: records.append((event, fields)),
                        )
                queue_records = [
                    fields for event, fields in records if fields.get("command_kind") == "queue"
                ]
                self.assertEqual(1, len(queue_records))
                self.assertEqual(expected_outcome, queue_records[0]["command_outcome"])
                self.assertEqual(expected_classification, queue_records[0]["classification"])

    def test_status_completion_collapses_large_status_count_to_registry_bucket(self):
        status_lftp = TestLftp._build_status_poll_test_lftp()
        status_lftp._Lftp__last_status_poll_healthy = True
        status_lftp._Lftp__last_status_poll_failure_reason = None
        status_lftp._Lftp__last_incoming_recovery_status_observation = {}
        records = []
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            status_lftp._Lftp__record_incoming_recovery_status_completion(
                lambda event, fields: records.append((event, fields)), [object()] * 17,
            )
        self.assertEqual("17+", records[0][1]["status_count_bucket"])

    def test_status_forwards_later_child_terminal_observations(self):
        for exitstatus, signalstatus, expected in ((7, None, "exit"), (None, 9, "signal")):
            with self.subTest(expected=expected), \
                    patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
                status_lftp = TestLftp._build_status_poll_test_lftp()
                status_lftp._Lftp__process.isalive.return_value = False
                status_lftp._Lftp__process.exitstatus = exitstatus
                status_lftp._Lftp__process.signalstatus = signalstatus
                status_records = []
                self.assertEqual([], status_lftp.status(
                    diagnostic_recorder=lambda event, fields: status_records.append((event, fields)),
                ))
                self.assertEqual(["lftp_child"], [event for event, _ in status_records])
                self.assertEqual(expected, status_records[0][1]["classification"])
                self.assertEqual("status", status_records[0][1]["command_kind"])

        eof_lftp = TestLftp._build_status_poll_test_lftp()
        eof_lftp._Lftp__process.expect.side_effect = pexpect.exceptions.EOF("private status eof")
        eof_records = []
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            self.assertEqual([], eof_lftp.status(
                diagnostic_recorder=lambda event, fields: eof_records.append((event, fields)),
            ))
        self.assertEqual(["eof"], [fields["classification"] for _, fields in eof_records])

        for boundary in ("send", "drain"):
            with self.subTest(boundary=boundary), \
                    patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
                terminal_lftp = TestLftp._build_status_poll_test_lftp()
                terminal_process = terminal_lftp._Lftp__process
                if boundary == "send":
                    terminal_process.send.side_effect = pexpect.exceptions.EOF("private send eof")
                else:
                    terminal_process.read_nonblocking.side_effect = pexpect.exceptions.EOF("private drain eof")
                terminal_records = []
                self.assertEqual([], terminal_lftp.status(
                    diagnostic_recorder=lambda event, fields: terminal_records.append((event, fields)),
                ))
                self.assertEqual(["eof"], [fields["classification"] for _, fields in terminal_records])

        recovery_lftp = TestLftp._build_status_poll_test_lftp()
        recovery_lftp._Lftp__process.expect.side_effect = [None, pexpect.exceptions.EOF("private recovery eof")]
        recovery_records = []
        with patch.object(Lftp, "_Lftp__normalize_output", return_value="backend"), \
                patch.object(Lftp, "_Lftp__detect_errors_from_output", side_effect=lambda value: value == "backend"), \
                patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            self.assertEqual([], recovery_lftp.status(
                diagnostic_recorder=lambda event, fields: recovery_records.append((event, fields)),
            ))
        self.assertEqual(["command_error", "eof"], [fields["classification"] for _, fields in recovery_records])

        timeout_lftp = TestLftp._build_status_poll_test_lftp()
        timeout_lftp._Lftp__process.send.side_effect = pexpect.exceptions.TIMEOUT("private timeout")
        timeout_records = []
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            self.assertEqual([], timeout_lftp.status(
                diagnostic_recorder=lambda event, fields: timeout_records.append((event, fields)),
            ))
        self.assertEqual(["timeout"], [fields["classification"] for _, fields in timeout_records])

        for error in (pexpect.exceptions.ExceptionPexpect("private command"), OSError("private command")):
            with self.subTest(status_error=type(error).__name__):
                command_lftp = TestLftp._build_status_poll_test_lftp()
                command_lftp._Lftp__process.expect.side_effect = error
                command_records = []
                with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
                    self.assertEqual([], command_lftp.status(
                        diagnostic_recorder=lambda event, fields: command_records.append((event, fields)),
                    ))
                self.assertEqual(["command_error"], [fields["classification"] for _, fields in command_records])

        terminal_lftp = TestLftp._build_status_poll_test_lftp()
        terminal_lftp._Lftp__process._buffer.write("Login failed: private backend")
        terminal_records = []
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "600"}):
            self.assertEqual([], terminal_lftp.status(
                diagnostic_recorder=lambda event, fields: terminal_records.append((event, fields)),
            ))
        self.assertEqual(["command_error"], [fields["classification"] for _, fields in terminal_records])

        suppressed_lftp = TestLftp._build_status_poll_test_lftp()
        suppressed_lftp._Lftp__process.send.side_effect = pexpect.exceptions.TIMEOUT("private timeout")
        with patch.dict(os.environ, {lftp_mod._INCOMING_RECOVERY_DIAGNOSTIC_ENV: "599"}):
            self.assertEqual([], suppressed_lftp.status(
                diagnostic_recorder=lambda *_args: self.fail("disabled diagnostic emitted"),
            ))

class TestLftpPromptClassification(unittest.TestCase):
    @patch("lftp.lftp.pexpect.spawn", create=True)
    def test_init_raises_lftp_error_on_ssh_host_key_prompt_timeout(self, spawn):
        process = MagicMock()
        process.before = (
            b"The authenticity of host 'localhost (127.0.0.1)' can't be established.\n"
            b"Are you sure you want to continue connecting (yes/no/[fingerprint])? "
        )
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        spawn.return_value = process

        with self.assertRaises(LftpError) as ctx:
            Lftp(address="localhost", port=22, user="seedsynctest", password=None)

        self.assertIn("SSH host-key prompt", str(ctx.exception))

    @patch("lftp.lftp.pexpect.spawn", create=True)
    def test_init_propagates_generic_startup_timeout(self, spawn):
        process = MagicMock()
        process.before = b"some harmless startup output"
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        spawn.return_value = process

        with self.assertRaises(pexpect.exceptions.TIMEOUT):
            Lftp(address="localhost", port=22, user="seedsynctest", password=None)

    @patch("lftp.lftp.pexpect.spawn", create=True)
    def test_init_sets_short_pget_save_status_interval(self, spawn):
        process = MagicMock()
        process.isalive.return_value = True
        process.expect.return_value = None
        spawn.return_value = process

        with patch.dict(os.environ, {}, clear=True):
            Lftp(address="localhost", port=22, user="seedsynctest", password=None)

        self.assertEqual(
            [
                call('set cmd:at-exit "kill all"'),
                call("set cmd:save-rl-history false"),
                call("set cmd:save-cwd-history false"),
                call("set sftp:auto-confirm 1"),
                call("set pget:save-status 2"),
            ],
            process.sendline.call_args_list
        )
        self.assertEqual(6, process.expect.call_count)

    @patch("lftp.lftp.pexpect.spawn", create=True)
    def test_init_disables_pty_command_echo_at_spawn(self, spawn):
        process = MagicMock()
        process.isalive.return_value = True
        process.expect.return_value = None
        spawn.return_value = process

        Lftp(address="localhost", port=22, user="seedsynctest", password=None)

        self.assertFalse(process.setecho.called)
        self.assertFalse(spawn.call_args.kwargs["echo"])

    @patch("lftp.lftp.pexpect.spawn", create=True)
    def test_init_preserves_env_while_forcing_wide_columns(self, spawn):
        process = MagicMock()
        process.isalive.return_value = True
        process.expect.return_value = None
        spawn.return_value = process

        with patch.dict(os.environ, {"EXISTING": "keep"}, clear=True):
            Lftp(address="localhost", port=22, user="seedsynctest", password=None)

        self.assertEqual(
            {
                "EXISTING": "keep",
                "COLUMNS": "10000",
            },
            spawn.call_args.kwargs["env"],
        )

    @patch("lftp.lftp.pexpect.spawn", create=True)
    def test_init_sets_permissions_override_when_umask_is_valid(self, spawn):
        process = MagicMock()
        process.isalive.return_value = True
        process.expect.return_value = None
        spawn.return_value = process

        with patch.dict(os.environ, {"UMASK": "022"}, clear=True):
            Lftp(address="localhost", port=22, user="seedsynctest", password=None)

        self.assertEqual(
            [
                call('set cmd:at-exit "kill all"'),
                call("set cmd:save-rl-history false"),
                call("set cmd:save-cwd-history false"),
                call("set sftp:auto-confirm 1"),
                call("set sftp:set-permissions false"),
                call("set pget:save-status 2"),
            ],
            process.sendline.call_args_list
        )
        self.assertEqual(7, process.expect.call_count)

    @patch("lftp.lftp.pexpect.spawn", create=True)
    def test_init_skips_permissions_override_when_umask_is_invalid_or_whitespace(self, spawn):
        for umask_value in ("+022", "-022", "0o22", " 022", "022 ", "022\n", " "):
            with self.subTest(umask_value=umask_value):
                process = MagicMock()
                process.isalive.return_value = True
                process.expect.return_value = None
                spawn.return_value = process

                with patch.dict(os.environ, {"UMASK": umask_value}, clear=True):
                    Lftp(address="localhost", port=22, user="seedsynctest", password=None)

                self.assertEqual(
                    [
                        call('set cmd:at-exit "kill all"'),
                        call("set cmd:save-rl-history false"),
                        call("set cmd:save-cwd-history false"),
                        call("set sftp:auto-confirm 1"),
                        call("set pget:save-status 2"),
                    ],
                    process.sendline.call_args_list
                )
                self.assertNotIn(call("set sftp:set-permissions false"), process.sendline.call_args_list)
                self.assertEqual(6, process.expect.call_count)

    def test_set_skips_prompt_readiness_probe(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b""
        process.after = b"prompt>"
        process.expect.return_value = None
        lftp._Lftp__process = process

        lftp._Lftp__set("cmd:at-exit", "\"kill all\"")

        process.sendline.assert_called_once_with('set cmd:at-exit "kill all"')
        process.expect.assert_called_once_with("prompt>", timeout=30)

    def test_run_command_raises_lftp_error_on_ssh_host_key_prompt_timeout(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        process = MagicMock()
        process.isalive.return_value = True
        process.before = (
            b"The authenticity of host 'localhost (127.0.0.1)' can't be established.\n"
            b"Are you sure you want to continue connecting (yes/no/[fingerprint])? "
        )
        process.after = pexpect.TIMEOUT
        process.expect.side_effect = pexpect.exceptions.TIMEOUT("timeout")
        lftp._Lftp__process = process

        with self.assertRaises(LftpError) as ctx:
            lftp._Lftp__run_command("ls")

        self.assertIn("SSH host-key prompt", str(ctx.exception))
        lftp.logger.warning.assert_not_called()

    def test_run_command_raises_lftp_error_on_ssh_host_key_prompt_timeout_during_error_recovery(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        process = MagicMock()
        process.isalive.return_value = True
        process.after = pexpect.TIMEOUT

        call_count = {"value": 0}

        def expect_side_effect(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return None
            if call_count["value"] == 2:
                process.before = b"mirror: Access failed"
                return None
            process.before = (
                b"The authenticity of host 'localhost (127.0.0.1)' can't be established.\n"
                b"Are you sure you want to continue connecting (yes/no/[fingerprint])? "
            )
            raise pexpect.exceptions.TIMEOUT("timeout")

        process.expect.side_effect = expect_side_effect
        lftp._Lftp__process = process

        with self.assertRaises(LftpError) as ctx:
            lftp._Lftp__run_command("mirror")

        self.assertIn("SSH host-key prompt", str(ctx.exception))
        self.assertEqual("mirror: Access failed", lftp._Lftp__pending_error)
        lftp.logger.warning.assert_not_called()

    def test_run_command_strips_bracketed_paste_toggle_lines(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        process = MagicMock()
        process.isalive.return_value = True
        process.before = (
            b"\x1b[?2004h\n"
            b"jobs -v\n"
            b"\x1b[?2004l\n"
            b"[0] queue (sftp://someone:@localhost)\n"
            b"sftp://someone:@localhost/home/someone\n"
            b"Queue is stopped.\n"
        )
        process.after = b"prompt>"
        process.expect.return_value = None
        lftp._Lftp__process = process

        output = lftp._Lftp__run_command("jobs -v")

        self.assertNotIn("\x1b[?2004h", output)
        self.assertNotIn("\x1b[?2004l", output)
        self.assertIn("[0] queue (sftp://someone:@localhost)", output)

    def test_status_ignores_nine_parser_errors_then_raises_on_tenth(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        lftp._Lftp__job_status_parser.parse.side_effect = LftpJobStatusParserError("boom")

        for _ in range(9):
            statuses = lftp.status()
            self.assertIsNone(statuses)

        self.assertEqual(9, lftp._Lftp__consecutive_status_errors)

        with self.assertRaises(LftpJobStatusParserError):
            lftp.status()

        self.assertEqual(10, lftp._Lftp__consecutive_status_errors)

    def test_status_connection_grace_retry_raises_on_tenth_parser_error(self):
        lftp = TestLftp._build_status_poll_test_lftp()
        lftp._Lftp__job_status_parser.parse.side_effect = LftpJobStatusParserError("boom")
        lftp._Lftp__consecutive_status_errors = 8
        lftp._Lftp__status_poll_needs_connection_grace = True

        with self.assertRaises(LftpJobStatusParserError):
            lftp.status()

        self.assertEqual(10, lftp._Lftp__consecutive_status_errors)
        self.assertEqual(2, lftp._Lftp__job_status_parser.parse.call_count)
        self.assertEqual(2, lftp._Lftp__process.send.call_count)

class TestLftpKillPathMatching(unittest.TestCase):
    def test_kill_matches_nested_pget_temp_target_by_containing_staging_directory(self):
        lftp = TestLftp._build_test_lftp()
        status = LftpJobStatus(
            job_id=12,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="release/nested/movie.mkv",
            flags="-c",
            remote_path="/remote/downloads/release/nested/movie.mkv",
            local_path="/downloads/incomplete/release/nested/movie.mkv.lftp"
        )
        lftp.status = MagicMock(return_value=[status])

        killed = lftp.kill(
            "release/nested/movie.mkv",
            remote_path="/remote/downloads/release/nested/movie.mkv",
            local_path="/downloads/incomplete/release/nested",
        )

        self.assertTrue(killed)
        lftp._Lftp__run_command.assert_called_once_with(
            "kill 12",
            require_prompt_ready=False,
            low_latency=True,
        )

    def test_kill_matches_running_pget_job_by_staging_root(self):
        lftp = TestLftp._build_test_lftp()
        status = LftpJobStatus(
            job_id=11,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="stop-repro-7g.bin",
            flags="-c",
            remote_path="/remote/downloads/stop-repro-7g.bin",
            local_path="/downloads/incomplete/stop-repro-7g.bin.lftp"
        )
        lftp.status = MagicMock(return_value=[status])

        killed = lftp.kill(
            "stop-repro-7g.bin",
            remote_path="/remote/downloads",
            local_path="/downloads/incomplete"
        )

        self.assertTrue(killed)
        lftp._Lftp__run_command.assert_called_once_with(
            "kill 11",
            require_prompt_ready=False,
            low_latency=True,
        )

    def test_kill_removes_all_matching_running_jobs(self):
        lftp = TestLftp._build_test_lftp()
        lftp._Lftp__path_is_within = MagicMock(return_value=True)
        status_1 = LftpJobStatus(
            job_id=3,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="dup.bin",
            flags="-c",
            remote_path="/remote/downloads/dup.bin",
            local_path="/local/incomplete/dup.bin.lftp"
        )
        status_2 = LftpJobStatus(
            job_id=4,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="dup.bin",
            flags="-c",
            remote_path="/remote/downloads/dup.bin",
            local_path="/local/incomplete/dup.bin.lftp"
        )
        lftp.status = MagicMock(return_value=[status_1, status_2])

        killed = lftp.kill(
            "dup.bin",
            remote_path="/remote/downloads",
            local_path="/local/incomplete"
        )

        self.assertTrue(killed)
        self.assertEqual(
            [("kill 3",), ("kill 4",)],
            [call.args for call in lftp._Lftp__run_command.call_args_list]
        )
        self.assertEqual(1, lftp.status.call_count)
        self.assertTrue(all(call.kwargs == {
            "require_prompt_ready": False,
            "low_latency": True,
        } for call in lftp._Lftp__run_command.call_args_list))

    def test_kill_does_not_retry_nonmatching_nonempty_status(self):
        lftp = TestLftp._build_test_lftp()
        other_status = LftpJobStatus(
            job_id=99,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="other.bin",
            flags="-c",
            remote_path="/remote/downloads/other.bin",
            local_path="/local/incomplete/other.bin.lftp"
        )
        lftp.status = MagicMock(return_value=[other_status])

        with patch("lftp.lftp.time.sleep") as sleep:
            killed = lftp.kill("rc")

        self.assertFalse(killed)
        self.assertEqual(1, lftp.status.call_count)
        sleep.assert_not_called()
        lftp._Lftp__run_command.assert_not_called()

    def test_kill_stops_when_matching_jobs_do_not_converge(self):
        lftp = TestLftp._build_test_lftp()
        lftp._Lftp__path_is_within = MagicMock(return_value=True)
        stuck_status = LftpJobStatus(
            job_id=3,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="dup.bin",
            flags="-c",
            remote_path="/remote/downloads/dup.bin",
            local_path="/local/incomplete/dup.bin.lftp"
        )
        lftp.status = MagicMock(side_effect=[
            [stuck_status],
            [stuck_status]
        ])

        killed = lftp.kill(
            "dup.bin",
            remote_path="/remote/downloads",
            local_path="/local/incomplete"
        )

        self.assertTrue(killed)
        lftp._Lftp__run_command.assert_called_once_with(
            "kill 3",
            require_prompt_ready=False,
            low_latency=True,
        )
        self.assertEqual(1, lftp.status.call_count)

    def test_kill_retries_empty_status_before_giving_up(self):
        lftp = TestLftp._build_test_lftp()
        status = LftpJobStatus(
            job_id=11,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="rc",
            flags="-c",
            remote_path="/remote/rc",
            local_path="/local/incomplete/rc.lftp"
        )
        polls = iter([
            ([], False),
            ([status], True),
        ])

        def status_side_effect():
            statuses, healthy = next(polls)
            lftp._Lftp__last_status_poll_healthy = healthy
            return statuses

        lftp.status = MagicMock(side_effect=status_side_effect)

        with patch("lftp.lftp.time.sleep") as sleep:
            killed = lftp.kill("rc")

        self.assertTrue(killed)
        self.assertEqual(2, lftp.status.call_count)
        sleep.assert_called_once_with(0.05)
        lftp._Lftp__run_command.assert_called_once_with(
            "kill 11", require_prompt_ready=False, low_latency=True,
        )

    def test_kill_retries_parser_failure_before_giving_up(self):
        lftp = TestLftp._build_test_lftp()
        status = LftpJobStatus(
            job_id=11,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="rc",
            flags="-c",
            remote_path="/remote/rc",
            local_path="/local/incomplete/rc.lftp"
        )
        polls = iter([
            (None, False),
            ([status], True),
        ])

        def status_side_effect():
            statuses, healthy = next(polls)
            lftp._Lftp__last_status_poll_healthy = healthy
            return statuses

        lftp.status = MagicMock(side_effect=status_side_effect)

        with patch("lftp.lftp.time.sleep") as sleep:
            killed = lftp.kill("rc")

        self.assertTrue(killed)
        self.assertEqual(2, lftp.status.call_count)
        sleep.assert_called_once_with(0.05)
        lftp._Lftp__run_command.assert_called_once_with(
            "kill 11", require_prompt_ready=False, low_latency=True,
        )

    def test_kill_retries_transient_empty_between_multiple_matches(self):
        lftp = TestLftp._build_test_lftp()
        first_status = LftpJobStatus(
            job_id=11,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="rc",
            flags="-c",
            remote_path="/remote/rc",
            local_path="/local/incomplete/rc.lftp"
        )
        second_status = LftpJobStatus(
            job_id=12,
            job_type=LftpJobStatus.Type.PGET,
            state=LftpJobStatus.State.RUNNING,
            name="rc",
            flags="-c",
            remote_path="/remote/rc",
            local_path="/local/incomplete/rc.lftp"
        )
        polls = iter([([first_status, second_status], True)])

        def status_side_effect():
            statuses, healthy = next(polls)
            lftp._Lftp__last_status_poll_healthy = healthy
            return statuses

        lftp.status = MagicMock(side_effect=status_side_effect)

        with patch("lftp.lftp.time.sleep") as sleep:
            killed = lftp.kill("rc")

        self.assertTrue(killed)
        self.assertEqual(1, lftp.status.call_count)
        self.assertEqual(
            [("kill 11",), ("kill 12",)],
            [call.args for call in lftp._Lftp__run_command.call_args_list]
        )
        sleep.assert_not_called()
