# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch

import pexpect
import pytest

from tests.utils import TestUtils
from common import ConfigError
from lftp import Lftp, LftpJobStatus, LftpError, LftpJobStatusParser, LftpJobStatusParserError


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
        lftp._Lftp__path_pairs_by_id = {}
        lftp._Lftp__job_status_parser = MagicMock()
        lftp._Lftp__job_status_parser.parse.return_value = []
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b""
        process.after = b"prompt>"
        process.delaybeforesend = 7
        process.delayafterread = 11
        process.expect.return_value = None
        if send_side_effect is not None:
            process.send.side_effect = send_side_effect
        lftp._Lftp__process = process
        return lftp

    def test_queue_uses_override_paths(self):
        lftp = self._build_test_lftp()

        lftp.queue("dup", False, remote_base_dir_path="/remote/movies", local_base_dir_path="/local/movies")

        lftp._Lftp__run_command.assert_called_once_with(
            "queue ' pget -c \"/remote/movies/dup\" -o \"/local/movies/\" '",
            require_prompt_ready=False
        )

    def test_queue_dir_uses_override_paths(self):
        lftp = self._build_test_lftp()

        lftp.queue("dup", True, remote_base_dir_path="/remote/movies", local_base_dir_path="/local/movies")

        lftp._Lftp__run_command.assert_called_once_with(
            "queue ' mirror -c \"/remote/movies/dup\" \"/local/movies/\" '",
            require_prompt_ready=False
        )

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
        lftp._Lftp__run_command.assert_called_once_with("kill 9", require_prompt_ready=False)

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

    def test_status_marks_poll_unhealthy_when_jobs_command_times_out(self):
        lftp = self._build_status_poll_test_lftp(send_side_effect=pexpect.exceptions.TIMEOUT("timeout"))

        statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        self.assertEqual(7, lftp._Lftp__process.delaybeforesend)
        lftp._Lftp__process.send.assert_called_once_with("jobs -v\n")
        lftp._Lftp__process.sendline.assert_not_called()
        lftp._Lftp__process.expect.assert_not_called()

    def test_status_marks_poll_unhealthy_when_jobs_command_eof(self):
        lftp = self._build_status_poll_test_lftp(send_side_effect=pexpect.exceptions.EOF("eof"))

        statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
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
        self.assertTrue(lftp._Lftp__last_command_timed_out)
        lftp.logger.warning.assert_called_once()
        lftp._Lftp__run_command.assert_called_once_with(
            "jobs -v",
            timeout_seconds=0,
            require_prompt_ready=False,
            status_poll=True
        )

    def test_status_marks_poll_unhealthy_when_jobs_command_raises_exception_pexpect(self):
        lftp = self._build_status_poll_test_lftp()
        lftp._Lftp__process.expect.side_effect = pexpect.exceptions.ExceptionPexpect("boom")

        statuses = lftp.status()

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)
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

        self.assertEqual([], statuses)
        self.assertFalse(lftp.last_status_poll_healthy)

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
            "test_kill_matches_duplicate_names_by_remote_path",
            "test_set_skips_prompt_readiness_probe",
            "test_status_annotates_path_pairs_from_job_paths",
            "test_status_marks_poll_unhealthy_when_jobs_command_times_out",
            "test_status_marks_poll_unhealthy_when_jobs_command_eof",
            "test_status_marks_poll_unhealthy_when_jobs_command_raises_lftp_error",
            "test_status_marks_poll_unhealthy_when_jobs_command_raises_exception_pexpect",
            "test_status_marks_poll_unhealthy_when_jobs_command_raises_oserror",
            "test_status_logs_bounded_summary_when_verbose",
            "test_status_uses_short_timeout_budget_for_jobs_command",
            "test_run_command_logs_verbose_output_when_not_status_poll",
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

    def tearDown(self):
        if not hasattr(self, "lftp"):
            return
        self.lftp.raise_pending_error()
        self.lftp.exit()

    def test_num_connections_per_dir_file(self):
        self.lftp.num_connections_per_dir_file = 5
        self.assertEqual(5, self.lftp.num_connections_per_dir_file)
        with self.assertRaises(ValueError):
            self.lftp.num_connections_per_dir_file = -1

    def test_num_connections_per_root_file(self):
        self.lftp.num_connections_per_root_file = 5
        self.assertEqual(5, self.lftp.num_connections_per_root_file)
        with self.assertRaises(ValueError):
            self.lftp.num_connections_per_root_file = -1

    def test_num_parallel_files(self):
        self.lftp.num_parallel_files = 5
        self.assertEqual(5, self.lftp.num_parallel_files)
        with self.assertRaises(ValueError):
            self.lftp.num_parallel_files = -1

    def test_num_max_total_connections(self):
        self.lftp.num_max_total_connections = 5
        self.assertEqual(5, self.lftp.num_max_total_connections)
        self.lftp.num_max_total_connections = 0
        self.assertEqual(0, self.lftp.num_max_total_connections)
        with self.assertRaises(ValueError):
            self.lftp.num_max_total_connections = -1

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

    def test_min_chunk_size(self):
        self.lftp.min_chunk_size = 500
        self.assertEqual("500", self.lftp.min_chunk_size)
        self.lftp.min_chunk_size = "2k"
        self.assertEqual("2k", self.lftp.min_chunk_size)
        self.lftp.min_chunk_size = "1M"
        self.assertEqual("1M", self.lftp.min_chunk_size)

    def test_num_parallel_jobs(self):
        self.lftp.num_parallel_jobs = 5
        self.assertEqual(5, self.lftp.num_parallel_jobs)
        with self.assertRaises(ValueError):
            self.lftp.num_parallel_jobs = -1

    def test_move_background_on_exit(self):
        self.lftp.move_background_on_exit = True
        self.assertEqual(True, self.lftp.move_background_on_exit)
        self.lftp.move_background_on_exit = False
        self.assertEqual(False, self.lftp.move_background_on_exit)

    def test_use_temp_file(self):
        self.lftp.use_temp_file = True
        self.assertEqual(True, self.lftp.use_temp_file)
        self.lftp.use_temp_file = False
        self.assertEqual(False, self.lftp.use_temp_file)

    def test_temp_file_name(self):
        self.lftp.temp_file_name = "*.lftp"
        self.assertEqual("*.lftp", self.lftp.temp_file_name)
        self.lftp.temp_file_name = "*.temp"
        self.assertEqual("*.temp", self.lftp.temp_file_name)

    def test_sftp_auto_confirm(self):
        self.lftp.sftp_auto_confirm = True
        self.assertEqual(True, self.lftp.sftp_auto_confirm)
        self.lftp.sftp_auto_confirm = False
        self.assertEqual(False, self.lftp.sftp_auto_confirm)

    def test_sftp_connect_program(self):
        self.lftp.sftp_connect_program = "program -a -f"
        self.assertEqual("\"program -a -f\"", self.lftp.sftp_connect_program)
        self.lftp.sftp_connect_program = "\"abc -d\""
        self.assertEqual("\"abc -d\"", self.lftp.sftp_connect_program)

    def test_status_empty(self):
        statuses = self.lftp.status()
        self.assertEqual(0, len(statuses))

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

        self.assertIn("mkdir -p /home/seedsync/.ssh", contents)
        self.assertIn("StrictHostKeyChecking accept-new", contents)
        self.assertIn("chmod 600 /home/seedsync/.ssh/config", contents)
        self.assertIn("mkdir /staging", contents)
        self.assertIn("chown seedsync:seedsync /staging", contents)
        self.assertIn('VOLUME [ "/config", "/downloads" ]', contents)
        self.assertIn('safe_chown "staging directory" /staging', entrypoint_contents)
        self.assertIn("check_writable_path \"$DOWNLOADS_DIR\"", entrypoint_contents)
        self.assertIn('mktemp "$path/.seedsync_write_test.XXXXXX"', entrypoint_contents)
        self.assertIn('rm -f -- "$test_file"', entrypoint_contents)
        self.assertNotIn('local test_file="${path}/.seedsync_write_test"', entrypoint_contents)
        self.assertNotIn("touch '$test_file' && rm '$test_file'", entrypoint_contents)
        self.assertIn("if mountpoint -q /staging 2>/dev/null; then", entrypoint_contents)
        self.assertIn("ERROR: invalid UMASK value", entrypoint_contents)


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
                call("set sftp:auto-confirm 1"),
                call("set pget:save-status 2"),
            ],
            process.sendline.call_args_list
        )
        self.assertEqual(4, process.expect.call_count)

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
                call("set sftp:auto-confirm 1"),
                call("set sftp:set-permissions false"),
                call("set pget:save-status 2"),
            ],
            process.sendline.call_args_list
        )
        self.assertEqual(5, process.expect.call_count)

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
                        call("set sftp:auto-confirm 1"),
                        call("set pget:save-status 2"),
                    ],
                    process.sendline.call_args_list
                )
                self.assertNotIn(call("set sftp:set-permissions false"), process.sendline.call_args_list)
                self.assertEqual(4, process.expect.call_count)

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

class TestLftpKillPathMatching(unittest.TestCase):
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
        lftp._Lftp__run_command.assert_called_once_with("kill 11", require_prompt_ready=False)

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
        lftp.status = MagicMock(side_effect=[
            [status_1, status_2],
            [status_2],
            []
        ])

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
        lftp._Lftp__run_command.assert_called_once_with("kill 3", require_prompt_ready=False)
        lftp.logger.warning.assert_called_once_with(
            "Kill did not converge for job 'dup.bin' after repeated matching polls"
        )

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
            ([], True),
        ])

        def status_side_effect():
            statuses, healthy = next(polls)
            lftp._Lftp__last_status_poll_healthy = healthy
            return statuses

        lftp.status = MagicMock(side_effect=status_side_effect)

        with patch("lftp.lftp.time.sleep") as sleep:
            killed = lftp.kill("rc")

        self.assertTrue(killed)
        self.assertEqual(3, lftp.status.call_count)
        sleep.assert_called_once_with(0.05)
        lftp._Lftp__run_command.assert_called_once_with("kill 11", require_prompt_ready=False)

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
        polls = iter([
            ([first_status, second_status], True),
            ([], False),
            ([second_status], True),
            ([], True),
        ])

        def status_side_effect():
            statuses, healthy = next(polls)
            lftp._Lftp__last_status_poll_healthy = healthy
            return statuses

        lftp.status = MagicMock(side_effect=status_side_effect)

        with patch("lftp.lftp.time.sleep") as sleep:
            killed = lftp.kill("rc")

        self.assertTrue(killed)
        self.assertEqual(4, lftp.status.call_count)
        self.assertEqual(
            [("kill 11",), ("kill 12",)],
            [call.args for call in lftp._Lftp__run_command.call_args_list]
        )
        sleep.assert_called_once_with(0.05)
