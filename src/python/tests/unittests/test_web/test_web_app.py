import unittest
import os
import tempfile
from threading import Timer
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from common import Config
from web.auth_store import ApiKeyStore
from webtest import TestApp
from web.web_app import IStreamHandler, WebApp
from web.web_app_builder import WebAppBuilder


LEGACY_TEST_API_TOKEN = "legacy-test-token"


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
            response = client.get(
                "/dashboard/123e4567-e89b-12d3-a456-426614174000",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

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
        context.config.general.api_token = LEGACY_TEST_API_TOKEN
        return WebApp(context, MagicMock())

    def test_normalizes_hostname_values_for_compare(self):
        self.assertEqual("myapp.local", WebApp._WebApp__normalize_hostname("  MyApp.Local.  "))

    def test_allows_localhost_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("  MyApp.Local.  ")

        @web_app.route("/server/ping", required_scope="read", allow_legacy_api_token=True)
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "localhost:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_exact_server_path_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("myapp.local")

        @web_app.route("/server", required_scope="read", allow_legacy_api_token=True)
        def _server_root():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)})
        response = client.get("/server", extra_environ={"HTTP_HOST": "myapp.local:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_configured_hostname_with_normalization(self):
        web_app = self._make_web_app("  MyApp.Local.  ")

        @web_app.route("/server/ping", required_scope="read", allow_legacy_api_token=True)
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "MYAPP.LOCAL.:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_ipv6_literal_when_configured_without_brackets(self):
        web_app = self._make_web_app("::1")

        @web_app.route("/server/ping", required_scope="read", allow_legacy_api_token=True)
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "[::1]:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_ipv6_literal_when_configured_with_brackets(self):
        web_app = self._make_web_app("[::1]")

        @web_app.route("/server/ping", required_scope="read", allow_legacy_api_token=True)
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "[::1]:8800"})
        self.assertEqual(200, response.status_int)

    def test_rejects_unlisted_hostname_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("myapp.local")

        @web_app.route("/server/ping", required_scope="read", allow_legacy_api_token=True)
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)})
        response = client.get(
            "/server/ping",
            extra_environ={"HTTP_HOST": "evil.example:8800"},
            expect_errors=True
        )
        self.assertEqual(400, response.status_int)

    def test_allows_any_hostname_when_not_configured(self):
        web_app = self._make_web_app("")

        @web_app.route("/server/ping", required_scope="read", allow_legacy_api_token=True)
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "evil.example:8800"})
        self.assertEqual(200, response.status_int)

    def test_unregistered_server_path_fails_closed(self):
        web_app = self._make_web_app("")
        web_app.add_default_routes()

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)})
        response = client.get("/server/not-registered", expect_errors=True)

        self.assertEqual(404, response.status_int)


class TestWebAppAuthCompatibility(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = "/tmp"
        self.context.status = MagicMock()
        self.context.config = Config()
        self.context.config.general.api_token = LEGACY_TEST_API_TOKEN
        self.auth_store = ApiKeyStore()
        created_admin = self.auth_store.create_api_key("unit-admin", ["admin"])
        self.admin_secret = created_admin["secret"]
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)

    @staticmethod
    def _same_origin_headers(origin: str = "http://localhost:8800"):
        parsed = urlparse(origin)
        return {
            "HTTP_HOST": parsed.netloc,
            "HTTP_ORIGIN": origin,
            "HTTP_REFERER": "{}/dashboard".format(origin),
            "REMOTE_ADDR": "127.0.0.1",
        }

    @staticmethod
    def _proxied_same_origin_headers(origin: str = "https://localhost:8800", forwarded_proto: str = "https"):
        parsed = urlparse(origin)
        return {
            "HTTP_HOST": parsed.netloc,
            "HTTP_ORIGIN": origin,
            "HTTP_REFERER": "{}/dashboard".format(origin),
            "HTTP_X_FORWARDED_HOST": parsed.netloc,
            "HTTP_X_FORWARDED_PROTO": forwarded_proto,
            "REMOTE_ADDR": "172.25.0.1",
        }

    def _issue_browser_session(self, client: TestApp, host: str = "localhost:8800"):
        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            response = client.get("/", extra_environ={"HTTP_HOST": host, "REMOTE_ADDR": "127.0.0.1"})
        return response

    def _issue_trusted_browser_session(
        self,
        client: TestApp,
        remote_addr: str,
        host: str = "localhost:8800",
        expect_errors: bool = False
    ):
        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            response = client.get(
                "/",
                extra_environ={"HTTP_HOST": host, "REMOTE_ADDR": remote_addr},
                expect_errors=expect_errors
            )
        return response

    def test_same_origin_browser_request_cannot_use_sessionless_route_after_admin_exists(self):
        @self.web_app.route("/server/ping", required_scope="read", allow_sessionless_ui=True)
        def _ping():
            return "pong"

        client = TestApp(self.web_app)
        response = client.get("/server/ping", extra_environ=self._same_origin_headers(), expect_errors=True)

        self.assertEqual(401, response.status_int)
        self.assertIn("Missing API token", response.text)

    def test_write_route_requires_cookie_backed_ui_session(self):
        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        self._issue_browser_session(client)
        response = client.post(
            "/server/ping",
            extra_environ=self._same_origin_headers()
        )

        self.assertEqual(200, response.status_int)

    def test_write_route_rejects_cookie_backed_ui_session_with_cross_origin_signal(self):
        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        self._issue_browser_session(client)
        response = client.post(
            "/server/ping",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_ORIGIN": "http://localhost:3000",
            },
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("Browser-origin signal required", response.text)

    def test_admin_write_route_requires_cookie_backed_ui_session_same_origin_signal(self):
        @self.web_app.route("/server/admin/ping", method="POST", required_scope="admin")
        def _admin_ping():
            return "pong"

        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        self._issue_browser_session(client)
        response = client.post(
            "/server/admin/ping",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_ORIGIN": "http://localhost:3000",
            },
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("Browser-origin signal required", response.text)

    def test_sessionless_ui_flag_is_rejected_for_write_routes(self):
        with self.assertRaisesRegex(ValueError, "allow_sessionless_ui is only supported for read/stream /server routes"):
            @self.web_app.route("/server/ping", method="POST", required_scope="write", allow_sessionless_ui=True)
            def _ping():
                return "pong"

    def test_same_origin_browser_request_can_use_stream_with_ui_session_cookie(self):
        self.web_app.add_streaming_handler(QueueStreamHandler, values=["event\n"], cleanup_log=[])
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        self._issue_browser_session(client)
        Timer(0.1, self.web_app.stop).start()

        response = client.get(
            "/server/stream",
            extra_environ={"HTTP_HOST": "localhost:8800", "REMOTE_ADDR": "127.0.0.1"}
        )

        self.assertEqual(200, response.status_int)
        self.assertIn("text/event-stream", response.headers["Content-Type"])

    def test_stream_rejects_legacy_token_when_compatibility_is_disabled(self):
        self.web_app.add_default_routes()
        self.auth_store.set_legacy_api_token_compatibility_enabled(False)
        client = TestApp(self.web_app)

        response = client.get(
            "/server/stream",
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)},
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("compatibility has been disabled", response.text)

    def test_legacy_token_is_rejected_for_sensitive_config_get(self):
        @self.web_app.route("/server/config/get", required_scope="read")
        def _get_config():
            return "secret"

        client = TestApp(self.web_app)
        response = client.get(
            "/server/config/get",
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)},
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("cannot access this route", response.text)

    def test_legacy_token_is_allowed_only_when_route_opted_in(self):
        @self.web_app.route("/server/status", required_scope="read", allow_legacy_api_token=True)
        def _status():
            return "ok"

        client = TestApp(self.web_app)
        response = client.get(
            "/server/status",
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)}
        )

        self.assertEqual(200, response.status_int)

    def test_server_routes_still_require_auth_when_allowed_hostname_is_blank(self):
        @self.web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        client = TestApp(self.web_app)
        response = client.get("/server/ping", expect_errors=True)

        self.assertEqual(401, response.status_int)

    def test_loopback_index_issues_ui_session_cookie(self):
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        response = self._issue_browser_session(client)

        self.assertEqual(200, response.status_int)
        self.assertIn("seedsync_ui_session=", response.headers.get("Set-Cookie", ""))

    def test_loopback_index_does_not_issue_ui_session_cookie_before_first_admin_exists(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        @web_app.route("/server/status", required_scope="read", allow_sessionless_ui=True)
        def _status():
            return "ok"

        web_app.add_streaming_handler(QueueStreamHandler, values=["event\n"], cleanup_log=[])
        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)
            response = client.get(
                "/dashboard/123e4567-e89b-12d3-a456-426614174000",
                extra_environ=self._same_origin_headers(),
            )

            status_response = client.get(
                "/server/status",
                extra_environ=self._same_origin_headers(),
            )

            rejected_read = client.get(
                "/server/ping",
                extra_environ=self._same_origin_headers(),
                expect_errors=True,
            )

            Timer(0.1, web_app.stop).start()
            stream_response = client.get(
                "/server/stream",
                extra_environ=self._same_origin_headers(),
            )

        self.assertEqual(200, response.status_int)
        self.assertEqual("", response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, status_response.status_int)
        self.assertEqual("ok", status_response.text)
        self.assertEqual(401, rejected_read.status_int)
        self.assertIn("Missing API token", rejected_read.text)
        self.assertEqual(200, stream_response.status_int)
        self.assertIn("text/event-stream", stream_response.headers["Content-Type"])

    def test_trusted_bootstrap_remote_can_use_sessionless_status_and_stream_before_first_admin_exists(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)

        @web_app.route("/server/status", required_scope="read", allow_sessionless_ui=True)
        def _status():
            return "ok"

        web_app.add_streaming_handler(QueueStreamHandler, values=["event\n"], cleanup_log=[])
        web_app.add_default_routes()
        client = TestApp(web_app)

        trusted_headers = {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "172.25.0.1",
            "HTTP_ORIGIN": "http://localhost:8800",
            "HTTP_REFERER": "http://localhost:8800/settings",
        }

        status_response = client.get("/server/status", extra_environ=trusted_headers)

        Timer(0.1, web_app.stop).start()
        stream_response = client.get("/server/stream", extra_environ=trusted_headers)

        self.assertEqual(200, status_response.status_int)
        self.assertEqual("ok", status_response.text)
        self.assertEqual(200, stream_response.status_int)
        self.assertIn("text/event-stream", stream_response.headers["Content-Type"])

    def test_loopback_dashboard_deep_link_issues_ui_session_cookie(self):
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            response = client.get(
                "/dashboard/123e4567-e89b-12d3-a456-426614174000",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

        self.assertEqual(200, response.status_int)
        self.assertIn("seedsync_ui_session=", response.headers.get("Set-Cookie", ""))

    def test_non_loopback_index_rejects_browser_bootstrap(self):
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "203.0.113.10",
                },
                expect_errors=True,
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("loopback", response.text)
        self.assertEqual("", response.headers.get("Set-Cookie", ""))

    def test_non_loopback_dashboard_deep_link_rejects_browser_bootstrap(self):
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            response = client.get(
                "/dashboard/123e4567-e89b-12d3-a456-426614174000",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "203.0.113.10",
                },
                expect_errors=True,
            )

        self.assertEqual(403, response.status_int)
        self.assertIn("loopback", response.text)

    def test_non_loopback_static_asset_request_rejects_browser_bootstrap(self):
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            with open(os.path.join(html_path, "main.js"), "w") as asset_file:
                asset_file.write("console.log('hello');")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            response = client.get(
                "/main.js",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "203.0.113.10",
                },
                expect_errors=True,
            )

        self.assertEqual(403, response.status_int)
        self.assertIn("loopback", response.text)

    def test_loopback_transport_rejects_non_loopback_host_for_browser_bootstrap(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)
            response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "seed.example:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
                expect_errors=True,
            )

        self.assertEqual(403, response.status_int)
        self.assertIn("loopback", response.text)
        self.assertEqual("", response.headers.get("Set-Cookie", ""))

    def test_loopback_transport_ignores_forwarded_loopback_host_for_browser_bootstrap(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)
            response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "seed.example:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_X_FORWARDED_HOST": "localhost:8800",
                    "HTTP_X_FORWARDED_PROTO": "https",
                },
                expect_errors=True,
            )

        self.assertEqual(403, response.status_int)
        self.assertIn("loopback", response.text)
        self.assertEqual("", response.headers.get("Set-Cookie", ""))

    def test_trusted_bootstrap_remote_addr_allows_index_and_ui_session_cookie_for_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = self._issue_trusted_browser_session(client, remote_addr="172.25.0.1")

        self.assertEqual(200, response.status_int)
        self.assertIn("seedsync_ui_session=", response.headers.get("Set-Cookie", ""))

    def test_trusted_bootstrap_remote_addr_allows_dashboard_deep_link_for_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            response = client.get(
                "/dashboard/123e4567-e89b-12d3-a456-426614174000",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "172.25.0.1",
                },
            )

        self.assertEqual(200, response.status_int)
        self.assertIn("seedsync_ui_session=", response.headers.get("Set-Cookie", ""))

    def test_trusted_bootstrap_remote_addr_allows_static_asset_for_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            with open(os.path.join(html_path, "main.js"), "w") as asset_file:
                asset_file.write("console.log('hello');")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            response = client.get(
                "/main.js",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "172.25.0.1",
                },
            )

        self.assertEqual(200, response.status_int)
        self.assertIn("console.log('hello');", response.text)

    def test_trusted_bootstrap_remote_addr_rejects_forwarded_non_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "172.25.0.1",
                    "HTTP_X_FORWARDED_HOST": "seed.example:8800",
                    "HTTP_X_FORWARDED_PROTO": "https",
                },
                expect_errors=True,
            )

        self.assertEqual(403, response.status_int)
        self.assertIn("trusted local runtime", response.text)
        self.assertEqual("", response.headers.get("Set-Cookie", ""))

    def test_trusted_bootstrap_remote_addr_still_rejects_non_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = self._issue_trusted_browser_session(
            client,
            remote_addr="172.25.0.1",
            host="seed.example:8800",
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("trusted local runtime", response.text)

    def test_malformed_trusted_bootstrap_remote_addrs_fail_closed(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "definitely-not-a-network"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = self._issue_trusted_browser_session(
            client,
            remote_addr="172.25.0.1",
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("trusted local runtime", response.text)

    def test_non_loopback_transport_rejects_ui_session_cookie(self):
        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        ui_session = self.auth_store.create_ui_session(["write"])
        client = TestApp(self.web_app)

        response = client.post(
            "/server/ping",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "203.0.113.10",
                "HTTP_COOKIE": "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, ui_session.secret),
            },
            expect_errors=True
        )

        self.assertEqual(401, response.status_int)
        self.assertIn("Missing API token", response.text)

    def test_first_admin_bootstrap_route_ignores_stale_prebootstrap_ui_session_scopes(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)

        @web_app.route("/server/admin/bootstrap-check", required_scope="admin", allow_first_admin_bootstrap=True)
        def _bootstrap_check():
            return "ok"

        client = TestApp(web_app)
        ui_session = empty_store.create_ui_session(["write"])

        response = client.get(
            "/server/admin/bootstrap-check",
            extra_environ={
                **self._same_origin_headers(),
                "HTTP_COOKIE": "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, ui_session.secret),
            }
        )

        self.assertEqual(200, response.status_int)
        self.assertEqual("ok", response.text)

    def test_trusted_bootstrap_remote_addr_can_use_cookie_backed_ui_session(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)

        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        self._issue_trusted_browser_session(client, remote_addr="172.25.0.1")

        response = client.post(
            "/server/ping",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "172.25.0.1",
                "HTTP_REFERER": "http://localhost:8800/dashboard",
            }
        )

        self.assertEqual(200, response.status_int)

    def test_non_bearer_authorization_header_does_not_block_ui_session_cookie_auth(self):
        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        ui_session = self.auth_store.create_ui_session(["write"])
        client = TestApp(self.web_app)

        response = client.post(
            "/server/ping",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_AUTHORIZATION": "Basic dXNlcjpwYXNz",
                "HTTP_REFERER": "http://localhost:8800/dashboard",
                "HTTP_COOKIE": "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, ui_session.secret),
            }
        )

        self.assertEqual(200, response.status_int)

    def test_write_route_accepts_cookie_backed_ui_session_through_trusted_forwarded_origin(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)

        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        ui_session = self.auth_store.create_ui_session(["write"])
        client = TestApp(self.web_app)

        response = client.post(
            "/server/ping",
            extra_environ={
                **self._proxied_same_origin_headers(),
                "HTTP_COOKIE": "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, ui_session.secret),
            }
        )

        self.assertEqual(200, response.status_int)

    def test_write_route_rejects_unsafe_forwarded_proto_for_cookie_backed_ui_session(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)

        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        ui_session = self.auth_store.create_ui_session(["write"])
        client = TestApp(self.web_app)

        response = client.post(
            "/server/ping",
            extra_environ={
                **self._proxied_same_origin_headers(forwarded_proto="ftp"),
                "HTTP_COOKIE": "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, ui_session.secret),
            },
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("Browser-origin signal required", response.text)

    def test_admin_write_route_accepts_bearer_api_key_without_same_origin_signal(self):
        @self.web_app.route("/server/admin/ping", method="POST", required_scope="admin")
        def _admin_ping():
            return "pong"

        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = client.post(
            "/server/admin/ping",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret),
            }
        )

        self.assertEqual(200, response.status_int)
