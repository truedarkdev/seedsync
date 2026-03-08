import unittest
from unittest.mock import MagicMock, patch

from web.handler.status import StatusHandler


class TestStatusHandler(unittest.TestCase):
    def setUp(self):
        self.status = MagicMock()
        self.handler = StatusHandler(self.status)

    @patch("web.handler.status.SerializeStatusJson")
    def test_get_status_returns_serialized_body(self, mock_serialize):
        mock_serialize.status.return_value = '{"server":{"up":true}}'

        response = self.handler._StatusHandler__handle_get_status()

        self.assertEqual(200, response.status_code)
        self.assertEqual('{"server":{"up":true}}', response.body)
        mock_serialize.status.assert_called_once_with(self.status)
