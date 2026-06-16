import json
import logging
import sys
import unittest
from logging.handlers import QueueHandler

from common.json_formatter import JsonFormatter
from seedsync import Seedsync


class TestJsonFormatter(unittest.TestCase):
    def test_format_emits_structured_fields(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="seedsync.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=123,
            msg="hello %s",
            args=("world",),
            exc_info=None
        )

        payload = json.loads(formatter.format(record))

        self.assertEqual("INFO", payload["level"])
        self.assertEqual("seedsync.test", payload["logger"])
        self.assertEqual("hello world", payload["message"])
        self.assertIn("timestamp", payload)
        self.assertIn("process", payload)
        self.assertIn("thread", payload)
        self.assertNotIn("traceback", payload)

    def test_format_includes_traceback_when_exception_present(self):
        formatter = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                name="seedsync.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=456,
                msg="failed",
                args=(),
                exc_info=sys.exc_info()
            )

        payload = json.loads(formatter.format(record))

        self.assertIn("traceback", payload)
        self.assertIn("ValueError: boom", payload["traceback"])

    def test_format_recovers_traceback_from_queue_prepared_record(self):
        formatter = JsonFormatter()
        try:
            raise ValueError("queue boom")
        except ValueError:
            record = logging.LogRecord(
                name="seedsync.worker",
                level=logging.ERROR,
                pathname=__file__,
                lineno=789,
                msg="subprocess failed",
                args=(),
                exc_info=sys.exc_info()
            )

        prepared = QueueHandler(None).prepare(record)
        self.assertIsNone(prepared.exc_info)
        self.assertIsNone(prepared.exc_text)

        payload = json.loads(formatter.format(prepared))

        self.assertEqual("subprocess failed", payload["message"])
        self.assertIn("traceback", payload)
        self.assertIn("Traceback (most recent call last):", payload["traceback"])
        self.assertIn("ValueError: queue boom", payload["traceback"])

    def test_create_logger_uses_json_formatter_when_configured(self):
        logger_name = "TestJsonFormatterLogger"
        logger = Seedsync._create_logger(logger_name, debug=False, logdir=None, log_format="json")
        try:
            self.assertEqual(JsonFormatter, type(logger.handlers[0].formatter))
        finally:
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
                handler.close()

    def test_create_logger_uses_standard_formatter_by_default(self):
        logger_name = "TestStandardFormatterLogger"
        logger = Seedsync._create_logger(logger_name, debug=False, logdir=None)
        try:
            self.assertEqual(logging.Formatter, type(logger.handlers[0].formatter))
        finally:
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
                handler.close()
