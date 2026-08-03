# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import patch

from web.handler.stream_heartbeat import HeartbeatStreamHandler


class TestHeartbeatStreamHandler(unittest.TestCase):
    @patch("web.handler.stream_heartbeat.time.monotonic")
    def test_stream_heartbeat_emits_after_interval(self, mock_monotonic):
        handler = HeartbeatStreamHandler()

        mock_monotonic.return_value = 0.0
        handler.setup()
        self.assertIsNone(handler.get_value())

        mock_monotonic.return_value = 5.0
        self.assertEqual(": heartbeat\n\n", handler.get_value())
