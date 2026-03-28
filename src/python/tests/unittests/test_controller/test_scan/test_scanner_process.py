# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import multiprocessing
import logging
import sys
import time
from unittest.mock import MagicMock

import pytest

from controller import IScanner, ScannerProcess, ScannerError
from system import SystemFile


pytestmark = pytest.mark.timeout(10)

class DummyScanner(IScanner):
    def scan(self):
        return []

    def set_base_logger(self, base_logger: logging.Logger):
        pass


class TestScannerProcess(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        # Assign process to this variable so that it can be cleaned up
        # even after an error
        self.process = None

    def tearDown(self):
        if self.process:
            self.process.terminate()

    def test_retrieves_scan_results(self):
        # Use this as a signal to mock to control which result to send
        self.scan_signal = multiprocessing.Value('i', 0)
        self.scan_counter = multiprocessing.Value('i', 0)

        a = SystemFile("a", 100, True)
        aa = SystemFile("aa", 60, False)
        a.add_child(aa)
        ab = SystemFile("ab", 40, False)
        a.add_child(ab)

        b = SystemFile("b", 10, True)
        ba = SystemFile("ba", 10, True)
        b.add_child(ba)
        baa = SystemFile("baa", 10, False)
        ba.add_child(baa)

        c = SystemFile("c", 1234, False)

        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()

        def _scan():
            ret = None
            if self.scan_signal.value == 0:
                ret = [a]
            elif self.scan_signal.value == 1:
                ret = [a, b]
            elif self.scan_signal.value == 2:
                ret = [c]
            elif self.scan_signal.value == 3:
                ret = []
            self.scan_counter.value += 1
            return ret
        mock_scanner.scan.side_effect = _scan

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100)
        process.run_init()
        process.run_loop()

        # wait for the first queued scan result
        while self.scan_counter.value < 1:
            pass
        result = process.pop_latest_result()
        self.assertEqual(1, len(result.files))
        self.assertEqual("a", result.files[0].name)
        self.assertEqual(True, result.files[0].is_dir)
        self.assertEqual(100, result.files[0].size)
        self.assertEqual(2, len(result.files[0].children))
        self.assertEqual("aa", result.files[0].children[0].name)
        self.assertEqual(False, result.files[0].children[0].is_dir)
        self.assertEqual(60, result.files[0].children[0].size)
        self.assertEqual("ab", result.files[0].children[1].name)
        self.assertEqual(False, result.files[0].children[1].is_dir)
        self.assertEqual(40, result.files[0].children[1].size)

        # signal for scan #1 and wait scan fetch
        self.scan_signal.value = 1
        process.run_loop()
        while self.scan_counter.value < 2:
            pass
        result = process.pop_latest_result()
        self.assertEqual(2, len(result.files))
        self.assertEqual("a", result.files[0].name)
        self.assertEqual(True, result.files[0].is_dir)
        self.assertEqual(100, result.files[0].size)
        self.assertEqual(2, len(result.files[0].children))
        self.assertEqual("aa", result.files[0].children[0].name)
        self.assertEqual(False, result.files[0].children[0].is_dir)
        self.assertEqual(60, result.files[0].children[0].size)
        self.assertEqual("ab", result.files[0].children[1].name)
        self.assertEqual(False, result.files[0].children[1].is_dir)
        self.assertEqual(40, result.files[0].children[1].size)
        self.assertEqual("b", result.files[1].name)
        self.assertEqual(True, result.files[1].is_dir)
        self.assertEqual(10, result.files[1].size)
        self.assertEqual(1, len(result.files[1].children))
        self.assertEqual("ba", result.files[1].children[0].name)
        self.assertEqual(True, result.files[1].children[0].is_dir)
        self.assertEqual(10, result.files[1].children[0].size)
        self.assertEqual(1, len(result.files[1].children[0].children))
        self.assertEqual("baa", result.files[1].children[0].children[0].name)
        self.assertEqual(False, result.files[1].children[0].children[0].is_dir)
        self.assertEqual(10, result.files[1].children[0].children[0].size)

        # signal for scan #2 and wait scan fetch
        self.scan_signal.value = 2
        process.run_loop()
        while self.scan_counter.value < 3:
            pass
        result = process.pop_latest_result()
        self.assertEqual(1, len(result.files))
        self.assertEqual("c", result.files[0].name)
        self.assertEqual(False, result.files[0].is_dir)
        self.assertEqual(1234, result.files[0].size)

        # signal for scan #3 and wait scan fetch
        self.scan_signal.value = 3
        process.run_loop()
        while self.scan_counter.value < 4:
            pass
        result = process.pop_latest_result()
        self.assertEqual(0, len(result.files))

    def test_sends_error_result_on_recoverable_error(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()
        mock_scanner.scan.side_effect = ScannerError("recoverable error", recoverable=True)

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100,
                                 verbose=False)
        process.logger = MagicMock()

        process.run_loop()
        result = process.pop_latest_result()
        self.assertEqual(0, len(result.files))
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        process.logger.warning.assert_called_once()
        warning_message = process.logger.warning.call_args[0][0]
        self.assertIn("recoverable error", warning_message)
        self.assertIn("failed result", warning_message.lower())

    def test_sends_partial_files_on_recoverable_error(self):
        partial_file = SystemFile("partial", 42, False)
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()
        mock_scanner.scan.side_effect = ScannerError(
            "recoverable error",
            recoverable=True,
            files=[partial_file]
        )

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100,
                                 verbose=False)
        process.logger = MagicMock()

        process.run_loop()
        result = process.pop_latest_result()
        self.assertEqual([partial_file], result.files)
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)

    def test_propagates_malformed_status_only_file_ids_with_scan_result(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(return_value=[])
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=["a"])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=["managed-1"])

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100,
                                 verbose=False)

        process.run_loop()
        result = process.pop_latest_result()

        self.assertEqual([], result.files)
        self.assertEqual(["a"], result.malformed_status_only_file_ids)
        self.assertEqual(["managed-1"], result.managed_extract_file_ids)
        mock_scanner.pop_malformed_status_only_file_ids.assert_called_once()
        mock_scanner.pop_managed_extract_file_ids.assert_called_once()

    def test_propagates_malformed_status_only_file_ids_on_recoverable_error(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock(side_effect=ScannerError("recoverable error", recoverable=True))
        mock_scanner.pop_malformed_status_only_file_ids = MagicMock(return_value=["a"])
        mock_scanner.pop_managed_extract_file_ids = MagicMock(return_value=["managed-1"])

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100,
                                 verbose=False)

        process.run_loop()
        result = process.pop_latest_result()

        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(["a"], result.malformed_status_only_file_ids)
        self.assertEqual(["managed-1"], result.managed_extract_file_ids)
        mock_scanner.pop_malformed_status_only_file_ids.assert_called_once()
        mock_scanner.pop_managed_extract_file_ids.assert_called_once()

    def test_pop_latest_result_returns_last_drained_result_when_queue_get_fails(self):
        process = ScannerProcess(scanner=DummyScanner(), interval_in_ms=100, verbose=False)
        process.logger = MagicMock()
        first_result = object()
        process._ScannerProcess__queue = MagicMock()
        process._ScannerProcess__queue.get.side_effect = [first_result, OSError("queue broken")]

        latest_result = process.pop_latest_result()

        self.assertIs(latest_result, first_result)
        process.logger.warning.assert_called_once()
        self.assertIn("Scanner queue read failed", process.logger.warning.call_args[0][0])

    def test_recoverable_error_warning_resets_after_success(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()
        mock_scanner.scan.side_effect = [
            ScannerError("recoverable error", recoverable=True),
            ScannerError("recoverable error", recoverable=True),
            [],
            ScannerError("recoverable error", recoverable=True),
        ]

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=0,
                                 verbose=False)
        process.logger = MagicMock()

        def _pop_result():
            result = None
            for _ in range(100):
                result = process.pop_latest_result()
                if result is not None:
                    return result
                time.sleep(0.01)
            return result

        process.run_loop()
        result = _pop_result()
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(1, process.logger.warning.call_count)

        process.run_loop()
        result = _pop_result()
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(1, process.logger.warning.call_count)

        process.run_loop()
        result = _pop_result()
        self.assertFalse(result.failed)
        self.assertEqual([], result.files)
        self.assertEqual(1, process.logger.warning.call_count)

        process.run_loop()
        result = _pop_result()
        self.assertTrue(result.failed)
        self.assertEqual("recoverable error", result.error_message)
        self.assertEqual(2, process.logger.warning.call_count)
        self.assertIn("recoverable error", process.logger.warning.call_args_list[0][0][0])
        self.assertIn("recoverable error", process.logger.warning.call_args_list[1][0][0])

    def test_sends_fatal_exception_on_nonrecoverable_error(self):
        mock_scanner = DummyScanner()
        mock_scanner.scan = MagicMock()
        mock_scanner.scan.side_effect = ScannerError("non-recoverable error", recoverable=False)

        process = ScannerProcess(scanner=mock_scanner,
                                 interval_in_ms=100)
        process.run_init()
        with self.assertRaises(ScannerError) as ctx:
            process.run_loop()
        self.assertEqual("non-recoverable error", str(ctx.exception))
