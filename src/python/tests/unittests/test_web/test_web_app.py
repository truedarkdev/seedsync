import unittest
import os
import tempfile
from unittest.mock import MagicMock, patch

from common import Config
from webtest import TestApp
from web.web_app import IStreamHandler, WebApp
from web.web_app_builder import WebAppBuilder


class QueueStreamHandler(IStreamHandler):
    def __init__(self, values, cleanup_log):
        self.values = list(values)
        self.cleanup_log = cleanup_log

    def setup(self):
        pass

    def get_value(self):
        if self.values:
            return self.values.pop(0)
        return None

    def cleanup(self):
        self.cleanup_log.append(True)


class TestWebAppStream(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = "/tmp"
        self.context.config = MagicMock()
        self.context.status = MagicMock()
        self.context.path_pair_manager = None
        self.web_app = WebApp(self.context, MagicMock())

    def test_stream_interleaves_one_event_per_handler_per_cycle(self):
        first_cleanup = []
        second_cleanup = []
        self.web_app.add_streaming_handler(QueueStreamHandler, values=["a1", "a2"], cleanup_log=first_cleanup)
        self.web_app.add_streaming_handler(QueueStreamHandler, values=["b1", "b2"], cleanup_log=second_cleanup)

        with patch("web.web_app.time.sleep"):
            stream = self.web_app._WebApp__web_stream()
            self.assertEqual("a1", next(stream))
            self.assertEqual("b1", next(stream))
            self.assertEqual("a2", next(stream))
            self.assertEqual("b2", next(stream))
            stream.close()

        self.assertEqual([True], first_cleanup)
        self.assertEqual([True], second_cleanup)

    def test_stream_paces_between_emitted_events(self):
        self.web_app.add_streaming_handler(QueueStreamHandler, values=["a1", "a2"], cleanup_log=[])

        with patch("web.web_app.time.sleep") as sleep:
            stream = self.web_app._WebApp__web_stream()
            self.assertEqual("a1", next(stream))
            self.assertEqual("a2", next(stream))
            stream.close()

        sleep.assert_called_once_with(WebApp._STREAM_EVENT_YIELD_INTERVAL_IN_MS / 1000)

    def test_dashboard_path_pair_deep_link_serves_index_html(self):
        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")

            self.context.args.html_path = html_path
            web_app = WebApp(self.context, MagicMock())
            web_app.add_default_routes()
            client = TestApp(web_app)
            response = client.get("/dashboard/123e4567-e89b-12d3-a456-426614174000")

        self.assertEqual(200, response.status_int)
        self.assertIn("<html></html>", response.text)

    def test_stop_does_not_raise_and_stops_active_stream(self):
        cleanup_log = []
        self.web_app.add_streaming_handler(
            QueueStreamHandler,
            values=["a1"],
            cleanup_log=cleanup_log,
        )

        with patch("web.web_app.time.sleep"):
            stream = self.web_app._WebApp__web_stream()
            self.assertEqual("a1", next(stream))

            self.web_app.stop()

            with self.assertRaises(StopIteration):
                next(stream)

        self.assertEqual([True], cleanup_log)

    def test_builder_registers_heartbeat_stream_handler(self):
        auto_queue_persist = MagicMock()

        web_app = WebAppBuilder(self.context, MagicMock(), auto_queue_persist).build()

        streaming_handler_classes = [
            stream_handler[0]
            for stream_handler in web_app._WebApp__streaming_handlers
        ]
        self.assertEqual("HeartbeatStreamHandler", streaming_handler_classes[-1].__name__)

    def test_builder_wires_lftp_local_path_into_controller_handler(self):
        auto_queue_persist = MagicMock()
        self.context.config.lftp = MagicMock()
        self.context.config.lftp.local_path = "/tmp/downloads"

        builder = WebAppBuilder(self.context, MagicMock(), auto_queue_persist)

        self.assertEqual(
            os.path.realpath("/tmp/downloads"),
            builder.controller_handler._ControllerHandler__local_path_root
        )

    def test_builder_leaves_controller_guard_root_unset_without_local_path(self):
        auto_queue_persist = MagicMock()
        self.context.config.lftp = MagicMock()
        self.context.config.lftp.local_path = None

        builder = WebAppBuilder(self.context, MagicMock(), auto_queue_persist)

        self.assertIsNone(builder.controller_handler._ControllerHandler__local_path_root)


class TestWebAppHostValidation(unittest.TestCase):
    def _make_web_app(self, allowed_hostname: str):
        context = MagicMock()
        context.logger.getChild.return_value = MagicMock()
        context.args.html_path = "/tmp"
        context.status = MagicMock()
        context.config = Config()
        context.config.general.allowed_hostname = allowed_hostname
        return WebApp(context, MagicMock())

    def test_normalizes_hostname_values_for_compare(self):
        self.assertEqual("myapp.local", WebApp._WebApp__normalize_hostname("  MyApp.Local.  "))

    def test_allows_localhost_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("  MyApp.Local.  ")

        @web_app.route("/server/ping")
        def _ping():
            return "pong"

        client = TestApp(web_app)
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "localhost:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_exact_server_path_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("myapp.local")

        @web_app.route("/server")
        def _server_root():
            return "pong"

        client = TestApp(web_app)
        response = client.get("/server", extra_environ={"HTTP_HOST": "myapp.local:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_configured_hostname_with_normalization(self):
        web_app = self._make_web_app("  MyApp.Local.  ")

        @web_app.route("/server/ping")
        def _ping():
            return "pong"

        client = TestApp(web_app)
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "MYAPP.LOCAL.:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_ipv6_literal_when_configured_without_brackets(self):
        web_app = self._make_web_app("::1")

        @web_app.route("/server/ping")
        def _ping():
            return "pong"

        client = TestApp(web_app)
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "[::1]:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_ipv6_literal_when_configured_with_brackets(self):
        web_app = self._make_web_app("[::1]")

        @web_app.route("/server/ping")
        def _ping():
            return "pong"

        client = TestApp(web_app)
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "[::1]:8800"})
        self.assertEqual(200, response.status_int)

    def test_rejects_unlisted_hostname_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("myapp.local")

        @web_app.route("/server/ping")
        def _ping():
            return "pong"

        client = TestApp(web_app)
        response = client.get(
            "/server/ping",
            extra_environ={"HTTP_HOST": "evil.example:8800"},
            expect_errors=True
        )
        self.assertEqual(400, response.status_int)

    def test_allows_any_hostname_when_not_configured(self):
        web_app = self._make_web_app("")

        @web_app.route("/server/ping")
        def _ping():
            return "pong"

        client = TestApp(web_app)
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "evil.example:8800"})
        self.assertEqual(200, response.status_int)
