# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock, patch, call
import logging
import sys
import os
import shutil
import tempfile

from webtest import TestApp

from common import overrides, Status, Config, PathPairManager
from controller import AutoQueuePersist
from web.auth_store import ApiKeyStore
from web import WebAppBuilder
from web.web_app import IStreamHandler, WebApp


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

        self.auth_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "api-keys.json"))
        created = self.auth_store.create_api_key("integration-admin", ["admin"])
        self.integration_admin_secret = created["secret"]

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
                                             self.auto_queue_persist,
                                             self.auth_store)
        self.web_app = self.web_app_builder.build()
        self.test_app = TestApp(
            self.web_app,
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.integration_admin_secret)}
        )

    def build_browser_test_app(self, auth_secret=None):
        extra_environ = {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "127.0.0.1",
        }
        if auth_secret is not None:
            extra_environ["HTTP_AUTHORIZATION"] = "Bearer {}".format(auth_secret)

        browser_app = TestApp(
            self.web_app,
            extra_environ=extra_environ
        )
        browser_app.get("/")
        return browser_app

    @overrides(unittest.TestCase)
    def tearDown(self):
        shutil.rmtree(self.temp_dir)


class TestWebApp(BaseTestWebApp):
    def test_process(self):
        self.web_app.process()

    def test_index_sets_connect_src_csp_header(self):
        response = self.build_browser_test_app().get("/index.html")

        self.assertEqual(
            "connect-src 'self' https://api.github.com",
            response.headers["Content-Security-Policy"]
        )

    def test_index_html_is_served_directly(self):
        response = self.build_browser_test_app().get("/index.html")

        self.assertEqual(200, response.status_int)
        self.assertIn("<html></html>", response.text)

    def test_dashboard_path_pair_deep_link_serves_index_html(self):
        response = self.build_browser_test_app().get(
            "/dashboard/123e4567-e89b-12d3-a456-426614174000"
        ).follow()

        self.assertEqual(200, response.status_int)
        self.assertIn("SeedSync browser access", response.text)
        self.assertIn("Save this browser for next time", response.text)

    def test_dashboard_path_pair_deep_link_issues_ui_session_cookie(self):
        browser_app = TestApp(
            self.web_app,
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
            }
        )

        response = browser_app.get("/dashboard/123e4567-e89b-12d3-a456-426614174000")

        self.assertEqual("http://localhost:8800/bootstrap", response.headers["Location"])
        self.assertEqual("", response.headers.get("Set-Cookie", ""))

        response = response.follow()

        self.assertEqual(200, response.status_int)
        self.assertIn("SeedSync browser access", response.text)
        self.assertIn("Save this browser for next time", response.text)
        self.assertEqual({}, browser_app.cookies)

    def test_trusted_docker_gateway_requires_bootstrap_limited_session_before_first_admin_exists(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.auth_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "empty-api-keys.json"))
        self.web_app = WebAppBuilder(
            self.context,
            self.controller,
            self.auto_queue_persist,
            self.auth_store
        ).build()
        browser_app = TestApp(
            self.web_app,
            extra_environ={
                "HTTP_HOST": "seedsync.local:8800",
                "REMOTE_ADDR": "172.25.0.1",
            }
        )

        response = browser_app.get("/", expect_errors=True)

        self.assertEqual(403, response.status_int)
        self.assertIn(
            "First-admin browser bootstrap requires direct loopback access or an approved local browser request.",
            response.text
        )

    def test_stream_interleaves_one_event_per_handler(self):
        class SequenceHandler(IStreamHandler):
            def __init__(self, values):
                self._values = list(values)

            def setup(self):
                pass

            def get_value(self):
                if not self._values:
                    return None
                return self._values.pop(0)

            def cleanup(self):
                pass

        web_app = WebApp(self.context, self.controller)
        web_app.add_streaming_handler(SequenceHandler, values=["a1\n", "a2\n"])
        web_app.add_streaming_handler(SequenceHandler, values=["b1\n"])

        stream = web_app._WebApp__web_stream()

        self.assertEqual("a1\n", next(stream))
        self.assertEqual("b1\n", next(stream))
        self.assertEqual("a2\n", next(stream))

        web_app.stop()
        next(stream, None)

    @patch("web.web_app.time.sleep")
    def test_stream_yield_sleep_is_shorter_than_idle_poll_sleep(self, mock_sleep):
        class SingleEventHandler(IStreamHandler):
            def __init__(self):
                self._values = ["event1\n", "event2\n"]

            def setup(self):
                pass

            def get_value(self):
                if not self._values:
                    return None

                return self._values.pop(0)

            def cleanup(self):
                pass

        web_app = WebApp(self.context, self.controller)
        web_app.add_streaming_handler(SingleEventHandler)

        stream = web_app._WebApp__web_stream()
        expected_sleep = WebApp._STREAM_EVENT_YIELD_INTERVAL_IN_MS / 1000

        self.assertEqual("event1\n", next(stream))
        self.assertEqual("event2\n", next(stream))

        web_app.stop()
        next(stream, None)
        self.assertEqual([call(expected_sleep), call(expected_sleep)], mock_sleep.call_args_list)
