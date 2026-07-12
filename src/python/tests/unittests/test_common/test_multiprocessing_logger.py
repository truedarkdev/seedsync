# Copyright 2017, Inderpreet Singh, All rights reserved.

import io
import unittest
import logging
import sys
import time
import multiprocessing
from logging.handlers import QueueHandler

from testfixtures import LogCapture
import pytest
from unittest.mock import MagicMock

from common import MultiprocessingLogger


pytestmark = pytest.mark.timeout(5)


def _child_logger(log_queue, log_level, name):
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(QueueHandler(log_queue))
    root_logger.setLevel(log_level)
    return root_logger.getChild(name)


def _process_1(log_queue, log_level):
    logger = _child_logger(log_queue, log_level, "process_1")
    logger.debug("Debug line")
    time.sleep(0.1)
    logger.info("Info line")
    time.sleep(0.1)
    logger.warning("Warning line")
    time.sleep(0.1)
    logger.error("Error line")


def _process_1_children(log_queue, log_level):
    logger = _child_logger(log_queue, log_level, "process_1")
    logger.debug("Debug line")
    logger.getChild("child_1").debug("Debug line")
    logger.getChild("child_1_1").debug("Debug line")


def _continuous_producer(log_queue, log_level, stop_event):
    logger = _child_logger(log_queue, log_level, "continuous")
    while not stop_event.is_set():
        logger.info("record")
        time.sleep(0.001)


class TestMultiprocessingLogger(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger(TestMultiprocessingLogger.__name__)
        handler = logging.StreamHandler(sys.stdout)
        self.logger.addHandler(handler)
        self.addCleanup(self.logger.removeHandler, handler)
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

    def test_main_logger_receives_records(self):
        mp_logger = MultiprocessingLogger(self.logger)
        p_1 = multiprocessing.Process(target=_process_1,
                                      args=(mp_logger.queue, mp_logger.log_level))

        with LogCapture("TestMultiprocessingLogger.MPLogger.process_1") as log_capture:
            mp_logger.start()
            p_1.start()
            time.sleep(1)
            p_1.join()
            mp_logger.stop()

            log_capture.check(
                ("process_1", "DEBUG", "Debug line"),
                ("process_1", "INFO", "Info line"),
                ("process_1", "WARNING", "Warning line"),
                ("process_1", "ERROR", "Error line")
            )

    def test_stop_before_start_and_repeated_stop_are_safe(self):
        mp_logger = MultiprocessingLogger(self.logger)
        mp_logger.stop()
        mp_logger.stop()

    def test_children_names(self):
        mp_logger = MultiprocessingLogger(self.logger)
        p_1 = multiprocessing.Process(target=_process_1_children,
                                      args=(mp_logger.queue, mp_logger.log_level))

        with LogCapture("TestMultiprocessingLogger.MPLogger.process_1") as log_capture:
            mp_logger.start()
            p_1.start()
            time.sleep(1)
            p_1.join()
            mp_logger.stop()

            log_capture.check(
                ("process_1", "DEBUG", "Debug line"),
                ("process_1.child_1", "DEBUG", "Debug line"),
                ("process_1.child_1_1", "DEBUG", "Debug line"),
            )

    def test_stop_is_bounded_while_producer_is_still_live(self):
        mp_logger = MultiprocessingLogger(self.logger)
        stop_event = multiprocessing.Event()
        producer = multiprocessing.Process(
            target=_continuous_producer,
            args=(mp_logger.queue, mp_logger.log_level, stop_event),
        )
        mp_logger.start()
        producer.start()
        time.sleep(0.2)

        started_at = time.monotonic()
        mp_logger.stop()
        elapsed = time.monotonic() - started_at
        stop_event.set()
        producer.join(timeout=5)
        if producer.is_alive():
            producer.terminate()
            producer.join()

        self.assertLess(elapsed, 2.0)

    def test_closed_stream_handlers_are_pruned_without_breaking_logging(self):
        closed_stream = io.StringIO()
        closed_handler = logging.StreamHandler(closed_stream)
        closed_handler.handleError = MagicMock()
        self.logger.addHandler(closed_handler)
        closed_stream.close()

        mp_logger = MultiprocessingLogger(self.logger)
        p_1 = multiprocessing.Process(target=_process_1,
                                      args=(mp_logger.queue, mp_logger.log_level))

        with LogCapture("TestMultiprocessingLogger.MPLogger.process_1") as log_capture:
            mp_logger.start()
            p_1.start()
            time.sleep(1)
            p_1.join()
            mp_logger.stop()

            log_capture.check(
                ("process_1", "DEBUG", "Debug line"),
                ("process_1", "INFO", "Info line"),
                ("process_1", "WARNING", "Warning line"),
                ("process_1", "ERROR", "Error line")
            )

        self.assertNotIn(closed_handler, self.logger.handlers)
        closed_handler.handleError.assert_not_called()

    def test_logger_levels(self):
        def _wait_for_records(log_capture, expected_count):
            deadline = time.time() + 5
            while len(log_capture.actual()) < expected_count and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(expected_count, len(log_capture.actual()))

        # Debug level
        self.logger.setLevel(logging.DEBUG)
        with LogCapture("TestMultiprocessingLogger.MPLogger.process_1") as log_capture:
            mp_logger = MultiprocessingLogger(self.logger)
            p_1 = multiprocessing.Process(target=_process_1,
                                          args=(mp_logger.queue, mp_logger.log_level))
            mp_logger.start()
            p_1.start()
            time.sleep(0.2)
            p_1.join()
            _wait_for_records(log_capture, 4)
            mp_logger.stop()

            log_capture.check(
                ("process_1", "DEBUG", "Debug line"),
                ("process_1", "INFO", "Info line"),
                ("process_1", "WARNING", "Warning line"),
                ("process_1", "ERROR", "Error line")
            )

        # Info level
        self.logger.setLevel(logging.INFO)
        with LogCapture("TestMultiprocessingLogger.MPLogger.process_1") as log_capture:
            mp_logger = MultiprocessingLogger(self.logger)
            p_1 = multiprocessing.Process(target=_process_1,
                                          args=(mp_logger.queue, mp_logger.log_level))
            mp_logger.start()
            p_1.start()
            time.sleep(0.2)
            p_1.join()
            _wait_for_records(log_capture, 3)
            mp_logger.stop()

            log_capture.check(
                ("process_1", "INFO", "Info line"),
                ("process_1", "WARNING", "Warning line"),
                ("process_1", "ERROR", "Error line")
            )

        # Warning level
        self.logger.setLevel(logging.WARNING)
        with LogCapture("TestMultiprocessingLogger.MPLogger.process_1") as log_capture:
            mp_logger = MultiprocessingLogger(self.logger)
            p_1 = multiprocessing.Process(target=_process_1,
                                          args=(mp_logger.queue, mp_logger.log_level))
            mp_logger.start()
            p_1.start()
            time.sleep(0.2)
            p_1.join()
            _wait_for_records(log_capture, 2)
            mp_logger.stop()

            log_capture.check(
                ("process_1", "WARNING", "Warning line"),
                ("process_1", "ERROR", "Error line")
            )

        # Error level
        self.logger.setLevel(logging.ERROR)
        with LogCapture("TestMultiprocessingLogger.MPLogger.process_1") as log_capture:
            mp_logger = MultiprocessingLogger(self.logger)
            p_1 = multiprocessing.Process(target=_process_1,
                                          args=(mp_logger.queue, mp_logger.log_level))
            mp_logger.start()
            p_1.start()
            time.sleep(0.2)
            p_1.join()
            _wait_for_records(log_capture, 1)
            mp_logger.stop()

            log_capture.check(
                ("process_1", "ERROR", "Error line")
            )
