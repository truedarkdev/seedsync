import unittest
from unittest.mock import patch

from web.handler.stream_heartbeat import HeartbeatStreamHandler


class TestHeartbeatStreamHandler(unittest.TestCase):
    def test_get_value_returns_heartbeat_only_after_interval(self):
        handler = HeartbeatStreamHandler()

        with patch("web.handler.stream_heartbeat.time.monotonic", side_effect=[0.0, 4.9, 5.0, 9.9, 10.0]):
            handler.setup()
            self.assertIsNone(handler.get_value())
            self.assertEqual(": heartbeat\n\n", handler.get_value())
            self.assertIsNone(handler.get_value())
            self.assertEqual(": heartbeat\n\n", handler.get_value())
