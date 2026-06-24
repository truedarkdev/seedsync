# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import logging
import sys
import time
from multiprocessing import Value
import threading
from unittest.mock import MagicMock, patch

import pytest

from common import AppProcess, AppOneShotProcess


pytestmark = pytest.mark.timeout(2)

class DummyException(Exception):
    pass


class DummyProcess(AppProcess):
    def __init__(self, fail: bool):
        super().__init__(name=self.__class__.__name__)
        self.fail = fail
        self.time = Value('i', 0)
        self.last_loop_time = Value('i', -1)
        self.last_init_time = Value('i', -1)
        self.last_cleanup_time = Value('i', -1)

    def run_loop(self):
        self.last_loop_time.value = self.time.value
        self.time.value += 1
        if self.fail:
            raise DummyException()

    def run_init(self):
        self.last_init_time.value = self.time.value
        self.time.value += 1

    def run_cleanup(self):
        self.last_cleanup_time.value = self.time.value
        self.time.value += 1


class LongRunningProcess(AppProcess):
    def __init__(self):
        super().__init__(name=self.__class__.__name__)

    def run_init(self):
        pass

    def run_loop(self):
        while True:
            pass

    def run_cleanup(self):
        pass


class LongRunningThreadProcess(AppProcess):
    def __init__(self):
        super().__init__(name=self.__class__.__name__)
        self.thread = threading.Thread(target=self.long_task)

    def run_init(self):
        self.thread.start()

    def run_loop(self):
        pass

    def run_cleanup(self):
        pass

    # noinspection PyMethodMayBeStatic
    def long_task(self):
        print("Thread task started")
        while True:
            pass


class DummyOneShotProcess(AppOneShotProcess):
    def __init__(self):
        super().__init__(name=self.__class__.__name__)
        self.time = Value('i', 0)

    def run_once(self):
        self.time.value += 1


class OneShotLongRunningProcess(AppOneShotProcess):
    def __init__(self):
        super().__init__(name=self.__class__.__name__)

    def run_once(self):
        while True:
            pass


class TestAppProcess(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        # Assign process to this variable so that it can be cleaned up
        # even after an error
        self.process = None

    def tearDown(self):
        if self.process:
            self.process.terminate()

    def test_exception_propagates(self):
        self.process = DummyProcess(fail=True)
        self.process.start()
        time.sleep(0.2)
        with self.assertRaises(DummyException):
            self.process.propagate_exception()

    def test_process_terminates(self):
        self.process = DummyProcess(fail=False)
        self.process.start()
        self.process.terminate()
        self.process.join()
        self.process = None

    def test_init_called_before_loop(self):
        self.process = DummyProcess(fail=False)
        self.process.start()
        time.sleep(0.2)
        self.assertGreater(self.process.last_init_time.value, -1)
        self.assertGreater(self.process.last_loop_time.value, -1)
        self.assertGreater(self.process.last_loop_time.value, self.process.last_init_time.value)

    def test_cleanup_called_after_loop(self):
        self.process = DummyProcess(fail=False)
        self.process.start()
        time.sleep(0.2)
        self.process.terminate()
        self.process.join()
        self.assertGreater(self.process.last_cleanup_time.value, -1)
        self.assertGreater(self.process.last_loop_time.value, -1)
        self.assertLess(self.process.last_loop_time.value, self.process.last_cleanup_time.value)
        self.process = None

    @pytest.mark.timeout(5)
    def test_long_running_process_is_force_terminated(self):
        self.process = LongRunningProcess()
        self.process.start()
        time.sleep(0.2)
        self.process.terminate()
        self.process.join()
        self.process = None

    @pytest.mark.timeout(5)
    def test_process_with_long_running_thread_terminates_properly(self):
        self.process = LongRunningThreadProcess()
        self.process.start()
        time.sleep(0.2)
        self.process.terminate()
        self.process.join()
        self.process = None

    def test_terminate_wait_loop_sleeps_between_alive_polls(self):
        process = DummyProcess(fail=False)
        process.is_alive = unittest.mock.MagicMock(side_effect=[True, True, False])

        with patch("common.app_process.time.sleep") as mock_sleep, \
                patch("common.app_process.Process.terminate") as mock_terminate:
            process.terminate()

        self.assertEqual(2, mock_sleep.call_count)
        mock_terminate.assert_called_once_with()

    def test_close_queues_releases_resources_and_makes_terminate_a_noop(self):
        self.process = DummyProcess(fail=False)
        self.process.mp_logger = MagicMock()
        exception_queue = MagicMock()
        self.process._AppProcess__exception_queue = exception_queue

        self.process.close_queues()
        self.process.close_queues()

        exception_queue.close.assert_called_once_with()
        exception_queue.join_thread.assert_called_once_with()
        self.assertIsNone(self.process._AppProcess__exception_queue)
        self.assertIsNone(self.process._terminate)
        self.assertIsNone(self.process.mp_logger)

        with patch("common.app_process.Process.terminate") as mock_terminate:
            self.process.terminate()

        mock_terminate.assert_not_called()


class TestAppOneShotProcess(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        # Assign process to this variable so that it can be cleaned up
        # even after an error
        self.process = None

    def tearDown(self):
        if self.process:
            self.process.terminate()

    def test_run_once_called_once(self):
        self.process = DummyOneShotProcess()
        self.process.start()
        time.sleep(0.2)
        self.assertEqual(self.process.time.value, 1)

    @pytest.mark.timeout(5)
    def test_long_running_process_is_force_terminated(self):
        self.process = OneShotLongRunningProcess()
        self.process.start()
        time.sleep(0.2)
        self.process.terminate()
        self.process.join()
        self.process = None
