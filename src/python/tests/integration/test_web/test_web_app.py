# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock
import logging
import sys
import shutil
import tempfile

from webtest import TestApp

from common import overrides, Status, Config, PathPairManager
from controller import AutoQueuePersist
from web import WebAppBuilder


class BaseTestWebApp(unittest.TestCase):
    """
    Base class for testing web app
    Sets up the web app with mocks
    """
    @overrides(unittest.TestCase)
    def setUp(self):
        self.context = MagicMock()
        self.controller = MagicMock()
        self.temp_dir = tempfile.mkdtemp(prefix="test_web_app")
        with open("{}/index.html".format(self.temp_dir), "w") as html_file:
            html_file.write("<html></html>")
        self.context.args.html_path = self.temp_dir

        # Mock the base logger
        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)
        self.context.logger = logger

        # Model files
        self.model_files = []

        # Real status
        self.context.status = Status()

        # Real config
        self.context.config = Config()
        self.context.path_pair_manager = PathPairManager(self.temp_dir)
        self.context.path_pair_manager.load()

        # Real auto-queue persist
        self.auto_queue_persist = AutoQueuePersist()

        # Capture the model listener
        def capture_listener(listener):
            self.model_listener = listener
            return self.model_files
        self.model_listener = None
        self.controller.get_model_files_and_add_listener = MagicMock()
        self.controller.get_model_files_and_add_listener.side_effect = capture_listener
        self.controller.remove_model_listener = MagicMock()

        # noinspection PyTypeChecker
        self.web_app_builder = WebAppBuilder(self.context,
                                             self.controller,
                                             self.auto_queue_persist)
        self.web_app = self.web_app_builder.build()
        self.test_app = TestApp(self.web_app)

    @overrides(unittest.TestCase)
    def tearDown(self):
        shutil.rmtree(self.temp_dir)


class TestWebApp(BaseTestWebApp):
    def test_process(self):
        self.web_app.process()

    def test_index_sets_connect_src_csp_header(self):
        response = self.test_app.get("/")

        self.assertEqual(
            "connect-src 'self' https://api.github.com",
            response.headers["Content-Security-Policy"]
        )
