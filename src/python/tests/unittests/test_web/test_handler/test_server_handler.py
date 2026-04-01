# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock

from web.handler.server import ServerHandler


class TestServerHandler(unittest.TestCase):
    def setUp(self):
        self.mock_context = MagicMock()
        self.handler = ServerHandler(self.mock_context)

    def test_restart_route_registered_as_post(self):
        mock_web_app = MagicMock()

        self.handler.add_routes(mock_web_app)

        mock_web_app.add_post_handler.assert_called_once_with(
            "/server/command/restart",
            unittest.mock.ANY,
            required_scope="write"
        )
        mock_web_app.add_handler.assert_not_called()
