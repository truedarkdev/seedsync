# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from threading import Timer

from tests.integration.test_web.test_web_app import BaseTestWebApp


class TestModelStreamHandler(BaseTestWebApp):
    def test_global_stream_does_not_fetch_model_or_add_listener(self):
        # Schedule server stop
        Timer(0.5, self.web_app.stop).start()

        self.test_app.get("/server/stream")
        self.controller.get_model_files_and_add_listener.assert_not_called()

    def test_global_stream_does_not_remove_a_model_listener(self):
        # Schedule server stop
        Timer(0.5, self.web_app.stop).start()

        self.test_app.get("/server/stream")
        self.controller.remove_model_listener.assert_not_called()
