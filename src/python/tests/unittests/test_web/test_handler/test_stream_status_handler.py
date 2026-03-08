import unittest
from unittest.mock import MagicMock

from web.handler.stream_status import StatusListener, StatusStreamHandler


class TestStatusListener(unittest.TestCase):
    def test_notify_copies_status_before_queueing(self):
        status = MagicMock()
        copied_status = MagicMock()
        status.copy.return_value = copied_status
        listener = StatusListener(status)

        listener.notify()

        status.copy.assert_called_once()
        self.assertIs(copied_status, listener.get_next_event())

    def test_empty_queue_returns_none(self):
        listener = StatusListener(MagicMock())

        self.assertIsNone(listener.get_next_event())


class TestStatusStreamHandler(unittest.TestCase):
    def setUp(self):
        self.status = MagicMock()
        self.handler = StatusStreamHandler(self.status)
        self.handler.serialize = MagicMock()
        self.handler.serialize.status.return_value = "event: status\ndata: {}\n\n"

    def test_setup_registers_listener(self):
        self.handler.setup()

        self.status.add_listener.assert_called_once_with(self.handler.status_listener)

    def test_first_get_value_serializes_status_copy(self):
        self.status.copy.return_value = MagicMock()
        self.handler.setup()

        result = self.handler.get_value()

        self.assertIn("event: status", result)
        self.assertFalse(self.handler.first_run)

    def test_second_get_value_reads_queued_status(self):
        self.status.copy.return_value = MagicMock()
        self.handler.setup()
        self.handler.get_value()
        queued_status = MagicMock()
        self.status.copy.return_value = queued_status
        self.handler.status_listener.notify()

        result = self.handler.get_value()

        self.assertIn("event: status", result)

    def test_cleanup_removes_listener(self):
        self.handler.cleanup()

        self.status.remove_listener.assert_called_once_with(self.handler.status_listener)
