import json
import unittest
import os
import tempfile
from threading import Timer
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from common import Config
from web.auth_store import ApiKeyStore
from web.handler.admin import AdminHandler
from webtest import TestApp
from web.web_app import IStreamHandler, WebApp
from web.web_app_builder import WebAppBuilder


LEGACY_TEST_API_TOKEN = "legacy-test-token"


def _assert_security_headers(testcase, response):
    testcase.assertEqual("connect-src 'self' https://api.github.com", response.headers["Content-Security-Policy"])
    testcase.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
    testcase.assertEqual("DENY", response.headers["X-Frame-Options"])
    testcase.assertEqual("strict-origin-when-cross-origin", response.headers["Referrer-Policy"])


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
        _assert_security_headers(self, response)

    def test_bootstrap_page_serves_browser_remember_form(self):
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = client.get("/bootstrap")

        self.assertEqual(200, response.status_int)
        self.assertIn("/server/browser/v1/remember", response.text)
        self.assertIn("Save this browser for next time", response.text)
        _assert_security_headers(self, response)

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

    def test_builder_wires_lftp_reconfigure_callback_into_config_handler(self):
        controller = MagicMock()
        controller.request_lftp_reconfigure = MagicMock()
        self.context.config = Config()
        self.context.breadcrumb_trace = MagicMock(
            sync_enabled_state=MagicMock(),
        )

        builder = WebAppBuilder(self.context, controller, MagicMock())

        self.assertIs(
            builder.config_handler._ConfigHandler__lftp_reconfigure_request,
            controller.request_lftp_reconfigure,
        )

class TestWebAppHostValidation(unittest.TestCase):
    def setUp(self):
        self.auth_store = ApiKeyStore()
        created_admin = self.auth_store.create_api_key("unit-admin", ["admin"])
        self.admin_secret = created_admin["secret"]

    def _make_web_app(self, allowed_hostname: str):
        context = MagicMock()
        context.logger.getChild.return_value = MagicMock()
        context.args.html_path = "/tmp"
        context.status = MagicMock()
        context.config = Config()
        context.config.general.allowed_hostname = allowed_hostname
        return WebApp(context, MagicMock(), auth_store=self.auth_store)

    def test_normalizes_hostname_values_for_compare(self):
        self.assertEqual("myapp.local", WebApp._WebApp__normalize_hostname("  MyApp.Local.  "))

    def test_allows_localhost_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("  MyApp.Local.  ")

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "localhost:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_exact_server_path_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("myapp.local")

        @web_app.route("/server", required_scope="read")
        def _server_root():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret)})
        response = client.get("/server", extra_environ={"HTTP_HOST": "myapp.local:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_configured_hostname_with_normalization(self):
        web_app = self._make_web_app("  MyApp.Local.  ")

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "MYAPP.LOCAL.:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_ipv6_literal_when_configured_without_brackets(self):
        web_app = self._make_web_app("::1")

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "[::1]:8800"})
        self.assertEqual(200, response.status_int)

    def test_allows_ipv6_literal_when_configured_with_brackets(self):
        web_app = self._make_web_app("[::1]")

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "[::1]:8800"})
        self.assertEqual(200, response.status_int)

    def test_rejects_unlisted_hostname_when_allowed_hostname_is_configured(self):
        web_app = self._make_web_app("myapp.local")

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret)})
        response = client.get(
            "/server/ping",
            extra_environ={"HTTP_HOST": "evil.example:8800"},
            expect_errors=True
        )
        self.assertEqual(400, response.status_int)

    def test_allows_any_hostname_when_not_configured(self):
        web_app = self._make_web_app("")

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret)})
        response = client.get("/server/ping", extra_environ={"HTTP_HOST": "evil.example:8800"})
        self.assertEqual(200, response.status_int)

    def test_unregistered_server_path_fails_closed(self):
        web_app = self._make_web_app("")
        web_app.add_default_routes()

        client = TestApp(web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.admin_secret)})
        response = client.get("/server/not-registered", expect_errors=True)

        self.assertEqual(404, response.status_int)


class TestWebAppAuthCompatibility(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = "/tmp"
        self.context.status = MagicMock()
        self.context.config = Config()
        self.auth_store = ApiKeyStore()
        created_admin = self.auth_store.create_api_key("unit-admin", ["admin"])
        self.admin_key_id = created_admin["record"].id
        self.admin_secret = created_admin["secret"]
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)

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
        response = client.post_json(
            "/server/browser/v1/remember",
            {"secret": self.admin_secret},
            extra_environ={
                "HTTP_HOST": host,
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_ORIGIN": "http://{}".format(host),
                "HTTP_REFERER": "http://{}/dashboard".format(host),
            },
        )
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

    def _remember_trusted_browser_session(
        self,
        client: TestApp,
        remote_addr: str,
        host: str = "localhost:8800",
    ):
        return client.post_json(
            "/server/browser/v1/remember",
            {"secret": self.admin_secret},
            extra_environ={
                "HTTP_HOST": host,
                "REMOTE_ADDR": remote_addr,
                "HTTP_ORIGIN": "http://{}".format(host),
                "HTTP_REFERER": "http://{}/dashboard".format(host),
            },
        )

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

    def test_stream_rejects_legacy_token(self):
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = client.get(
            "/server/stream",
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)},
            expect_errors=True
        )

        self.assertEqual(401, response.status_int)
        self.assertIn("Invalid API token", response.text)

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

        self.assertEqual(401, response.status_int)
        self.assertIn("Invalid API token", response.text)

    def test_legacy_token_is_rejected_for_read_route(self):
        @self.web_app.route("/server/status", required_scope="read")
        def _status():
            return "ok"

        client = TestApp(self.web_app)
        response = client.get(
            "/server/status",
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)},
            expect_errors=True
        )

        self.assertEqual(401, response.status_int)
        self.assertIn("Invalid API token", response.text)

    def test_server_routes_still_require_auth_when_allowed_hostname_is_blank(self):
        @self.web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        client = TestApp(self.web_app)
        response = client.get("/server/ping", expect_errors=True)

        self.assertEqual(401, response.status_int)

    def test_loopback_index_issues_shell_session_cookie(self):
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        response = self._issue_browser_session(client)

        self.assertEqual(201, response.status_int)
        self.assertIn("seedsync_ui_session=", response.headers.get("Set-Cookie", ""))
        _assert_security_headers(self, response)

    def test_loopback_index_auto_grants_first_admin_access_before_first_admin_exists(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        web_app.add_streaming_handler(QueueStreamHandler, values=["event\n"], cleanup_log=[])
        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)
            response = client.get(
                "/",
                extra_environ=self._same_origin_headers(),
                expect_errors=True,
            )
            bootstrap_response = client.get(
                "/bootstrap",
                extra_environ=self._same_origin_headers(),
            )
            bootstrap_claim_response = client.post_json(
                "/server/admin/bootstrap/v1/first-api-key",
                {},
                extra_environ=self._same_origin_headers(),
            )
            shell_response = client.get(
                "/",
                extra_environ=self._same_origin_headers(),
            )
            admin_response = client.get("/server/admin/api-keys/v1", extra_environ=self._same_origin_headers())

        self.assertEqual(302, response.status_int)
        self.assertTrue(response.headers.get("Location", "").endswith("/bootstrap"))
        _assert_security_headers(self, response)
        self.assertEqual("", response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, bootstrap_response.status_int)
        self.assertIn("Claim the first local session", bootstrap_response.text)
        self.assertIn("/server/admin/bootstrap/v1/first-api-key", bootstrap_response.text)
        self.assertIn(
            "This trusted browser can take SeedSync's initial admin handoff and keep the setup inside the local runtime. After that, any other browser will need an API key once to become remembered.",
            bootstrap_response.text,
        )
        self.assertNotIn("submitBootstrapRequest();", bootstrap_response.text)
        self.assertEqual("", bootstrap_response.headers.get("Set-Cookie", ""))
        self.assertEqual(201, bootstrap_claim_response.status_int)
        self.assertIn("seedsync_ui_session=", bootstrap_claim_response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, shell_response.status_int)
        self.assertIn("<html></html>", shell_response.text)
        self.assertEqual(200, admin_response.status_int)
        self.assertEqual(1, len(json.loads(admin_response.text)["keys"]))
        self.assertFalse(empty_store.get_browser_handover_state(self.context.config)["open"])

    def test_loopback_index_redirects_to_bootstrap_after_first_admin_exists_until_browser_is_remembered(self):
        store = ApiKeyStore()
        created = store.create_api_key("admin", ["admin"])
        web_app = WebApp(self.context, MagicMock(), auth_store=store)
        web_app.add_streaming_handler(QueueStreamHandler, values=["event\n"], cleanup_log=[])
        AdminHandler(self.context.config, store).add_routes(web_app)

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)
            response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
                expect_errors=True,
            )
            bootstrap_response = client.get(
                "/bootstrap",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
                expect_errors=True,
            )

            rejected_read = client.get(
                "/server/ping",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
                expect_errors=True,
            )

            remember_response = client.post_json(
                "/server/browser/v1/remember",
                {"secret": created["secret"]},
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_ORIGIN": "http://localhost:8800",
                    "HTTP_REFERER": "http://localhost:8800/bootstrap",
                },
            )

            shell_response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

            authorized_read = client.get(
                "/server/ping",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

            authorized_admin = client.get(
                "/server/admin/api-keys/v1",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_ORIGIN": "http://localhost:8800",
                    "HTTP_REFERER": "http://localhost:8800/dashboard",
                },
            )

            @web_app.route("/server/status", required_scope="read", allow_sessionless_ui=True)
            def _status():
                return "ok"

            status_response = client.get(
                "/server/status",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

            Timer(0.1, web_app.stop).start()
            stream_response = client.get(
                "/server/stream",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

        self.assertEqual(302, response.status_int)
        self.assertTrue(response.headers.get("Location", "").endswith("/bootstrap"))
        self.assertEqual("", response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, bootstrap_response.status_int)
        self.assertIn("Save this browser for next time", bootstrap_response.text)
        self.assertEqual("", bootstrap_response.headers.get("Set-Cookie", ""))
        self.assertEqual(401, rejected_read.status_int)
        self.assertIn("Missing API token", rejected_read.text)
        self.assertEqual(201, remember_response.status_int)
        self.assertIn("seedsync_ui_session=", remember_response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, shell_response.status_int)
        self.assertIn("<html></html>", shell_response.text)
        self.assertEqual(200, authorized_read.status_int)
        self.assertEqual("pong", authorized_read.text)
        self.assertEqual(200, authorized_admin.status_int)
        self.assertEqual(1, len(json.loads(authorized_admin.text)["keys"]))
        self.assertEqual(200, status_response.status_int)
        self.assertEqual("ok", status_response.text)
        self.assertEqual(200, stream_response.status_int)
        self.assertIn("text/event-stream", stream_response.headers["Content-Type"])

    def test_loopback_remembered_browser_session_redirects_to_bootstrap_while_handover_is_open(self):
        store = ApiKeyStore()
        self.context.config.general.browser_handover_recovery_version = "0"
        created = store.create_initial_admin_api_key_if_available("0", "admin")
        self.assertIsNotNone(created)

        web_app = WebApp(self.context, MagicMock(), auth_store=store)
        AdminHandler(self.context.config, store).add_routes(web_app)

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)

            remember_response = client.post_json(
                "/server/browser/v1/remember",
                {"secret": created["secret"]},
                extra_environ=self._same_origin_headers(),
            )

            self.context.config.general.browser_handover_recovery_version = "1"
            self.assertTrue(store.get_browser_handover_state(self.context.config)["open"])

            response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
                expect_errors=True,
            )
            read_response = client.get(
                "/server/ping",
                extra_environ=self._same_origin_headers(),
                expect_errors=True,
            )
            bootstrap_response = client.get(
                "/bootstrap",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )
            bootstrap_claim_response = client.post_json(
                "/server/admin/bootstrap/v1/first-api-key",
                {},
                extra_environ=self._same_origin_headers(),
            )
            shell_after_claim_response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

        self.assertEqual(201, remember_response.status_int)
        self.assertIn("seedsync_ui_session=", remember_response.headers.get("Set-Cookie", ""))
        self.assertEqual(302, response.status_int)
        self.assertTrue(response.headers.get("Location", "").endswith("/bootstrap"))
        self.assertEqual(302, read_response.status_int)
        self.assertTrue(read_response.headers.get("Location", "").endswith("/bootstrap"))
        self.assertEqual(200, bootstrap_response.status_int)
        self.assertIn("Claim the first local session", bootstrap_response.text)
        self.assertNotIn("Save this browser for next time", bootstrap_response.text)
        self.assertEqual(201, bootstrap_claim_response.status_int)
        self.assertIn("seedsync_ui_session=", bootstrap_claim_response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, shell_after_claim_response.status_int)
        self.assertIn("<html></html>", shell_after_claim_response.text)

    def test_loopback_remembered_browser_session_allows_bootstrap_static_assets_while_handover_is_open(self):
        store = ApiKeyStore()
        self.context.config.general.browser_handover_recovery_version = "0"
        created = store.create_initial_admin_api_key_if_available("0", "admin")
        self.assertIsNotNone(created)

        web_app = WebApp(self.context, MagicMock(), auth_store=store)
        AdminHandler(self.context.config, store).add_routes(web_app)
        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            os.makedirs(os.path.join(html_path, "assets"))
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            with open(os.path.join(html_path, "assets", "logo.png"), "w") as asset_file:
                asset_file.write("logo")
            with open(os.path.join(html_path, "assets", "favicon.png"), "w") as asset_file:
                asset_file.write("icon")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)

            remember_response = client.post_json(
                "/server/browser/v1/remember",
                {"secret": created["secret"]},
                extra_environ=self._same_origin_headers(),
            )

            self.context.config.general.browser_handover_recovery_version = "1"
            logo_response = client.get(
                "/assets/logo.png",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "seedsync.raw_path": "/assets/logo.png",
                },
            )
            favicon_response = client.get(
                "/assets/favicon.png",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "seedsync.raw_path": "/assets/favicon.png",
                },
            )
            hostile_asset_response = client.get(
                "/assets/logo.png",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "seedsync.raw_path": "/assets/../assets/logo.png",
                },
                expect_errors=True,
            )
            shell_response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "seedsync.raw_path": "/",
                },
                expect_errors=True,
            )

        self.assertEqual(201, remember_response.status_int)
        self.assertIn("seedsync_ui_session=", remember_response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, logo_response.status_int)
        self.assertEqual(200, favicon_response.status_int)
        self.assertEqual(302, hostile_asset_response.status_int)
        self.assertTrue(hostile_asset_response.headers.get("Location", "").endswith("/bootstrap"))
        self.assertEqual(302, shell_response.status_int)
        self.assertTrue(shell_response.headers.get("Location", "").endswith("/bootstrap"))

    def test_loopback_legacy_shell_session_redirects_to_bootstrap_after_first_admin_exists(self):
        store = ApiKeyStore()
        store.create_api_key("admin", ["admin"])
        legacy_shell_session = store.create_ui_session(["admin"])
        web_app = WebApp(self.context, MagicMock(), auth_store=store)
        AdminHandler(self.context.config, store).add_routes(web_app)
        web_app.add_default_routes()

        @web_app.route("/server/ping", required_scope="read")
        def _ping():
            return "pong"

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)
            response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_COOKIE": "seedsync_ui_session={}".format(legacy_shell_session.secret),
                },
                expect_errors=True,
            )
            bootstrap_response = client.get(
                "/bootstrap",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_COOKIE": "seedsync_ui_session={}".format(legacy_shell_session.secret),
                },
            )

            rejected_admin = client.get(
                "/server/admin/api-keys/v1",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_COOKIE": "seedsync_ui_session={}".format(legacy_shell_session.secret),
                },
                expect_errors=True,
            )

        self.assertEqual(302, response.status_int)
        self.assertTrue(response.headers.get("Location", "").endswith("/bootstrap"))
        self.assertEqual("", response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, bootstrap_response.status_int)
        self.assertIn("Save this browser for next time", bootstrap_response.text)
        self.assertEqual("", bootstrap_response.headers.get("Set-Cookie", ""))
        self.assertEqual(401, rejected_admin.status_int)
        self.assertIn("Missing API token", rejected_admin.text)

    def test_bootstrap_page_serves_browser_remember_form_after_first_admin_exists(self):
        store = ApiKeyStore()
        store.create_api_key("admin", ["admin"])
        web_app = WebApp(self.context, MagicMock(), auth_store=store)
        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)
            response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
                expect_errors=True,
            )
            bootstrap_response = client.get(
                "/bootstrap",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

        self.assertEqual(302, response.status_int)
        self.assertTrue(response.headers.get("Location", "").endswith("/bootstrap"))
        self.assertEqual("", response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, bootstrap_response.status_int)
        self.assertIn("/server/browser/v1/remember", bootstrap_response.text)
        self.assertIn("Save this browser for next time", bootstrap_response.text)

    def test_bootstrap_page_requires_explicit_first_admin_claim_for_loopback_before_first_admin_exists(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)
        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)

            response = client.get(
                "/bootstrap",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )
            handover_state_after_get = empty_store.get_browser_handover_state(self.context.config)
            bootstrap_claim_response = client.post_json(
                "/server/admin/bootstrap/v1/first-api-key",
                {},
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_ORIGIN": "http://localhost:8800",
                    "HTTP_REFERER": "http://localhost:8800/bootstrap",
                },
            )
            shell_response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )
            admin_response = client.get(
                "/server/admin/api-keys/v1",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_ORIGIN": "http://localhost:8800",
                    "HTTP_REFERER": "http://localhost:8800/dashboard",
                },
            )

        self.assertEqual(200, response.status_int)
        self.assertIn("Claim the first local session", response.text)
        self.assertIn("/server/admin/bootstrap/v1/first-api-key", response.text)
        self.assertIn(
            "This trusted browser can take SeedSync's initial admin handoff and keep the setup inside the local runtime. After that, any other browser will need an API key once to become remembered.",
            response.text,
        )
        self.assertNotIn("submitBootstrapRequest();", response.text)
        self.assertEqual("", response.headers.get("Set-Cookie", ""))
        self.assertTrue(handover_state_after_get["open"])
        self.assertEqual(201, bootstrap_claim_response.status_int)
        self.assertIn("seedsync_ui_session=", bootstrap_claim_response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, shell_response.status_int)
        self.assertIn("<html></html>", shell_response.text)
        self.assertEqual(200, admin_response.status_int)
        self.assertEqual(1, len(json.loads(admin_response.text)["keys"]))
        self.assertFalse(empty_store.get_browser_handover_state(self.context.config)["open"])

    def test_bootstrap_page_reopens_first_admin_access_after_handover_version_changes(self):
        store = ApiKeyStore()
        self.assertIsNotNone(store.create_initial_admin_api_key_if_available("0", "admin"))
        self.context.config.general.browser_handover_recovery_version = "1"
        web_app = WebApp(self.context, MagicMock(), auth_store=store)
        AdminHandler(self.context.config, store).add_routes(web_app)
        web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            client = TestApp(web_app)

            response = client.get(
                "/bootstrap",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )
            handover_state_after_get = store.get_browser_handover_state(self.context.config)
            bootstrap_claim_response = client.post_json(
                "/server/admin/bootstrap/v1/first-api-key",
                {},
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_ORIGIN": "http://localhost:8800",
                    "HTTP_REFERER": "http://localhost:8800/bootstrap",
                },
            )
            shell_response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )
            admin_response = client.get(
                "/server/admin/api-keys/v1",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_ORIGIN": "http://localhost:8800",
                    "HTTP_REFERER": "http://localhost:8800/dashboard",
                },
            )

        self.assertEqual(200, response.status_int)
        self.assertIn("Claim the first local session", response.text)
        self.assertIn("/server/admin/bootstrap/v1/first-api-key", response.text)
        self.assertIn(
            "This trusted browser can take SeedSync's initial admin handoff and keep the setup inside the local runtime. After that, any other browser will need an API key once to become remembered.",
            response.text,
        )
        self.assertNotIn("submitBootstrapRequest();", response.text)
        self.assertEqual("", response.headers.get("Set-Cookie", ""))
        self.assertTrue(handover_state_after_get["open"])
        self.assertEqual(201, bootstrap_claim_response.status_int)
        self.assertIn("seedsync_ui_session=", bootstrap_claim_response.headers.get("Set-Cookie", ""))
        self.assertEqual(200, shell_response.status_int)
        self.assertIn("<html></html>", shell_response.text)
        self.assertEqual(200, admin_response.status_int)
        payload = json.loads(admin_response.text)
        self.assertEqual(2, len(payload["keys"]))
        browser_handover = store.get_browser_handover_state(self.context.config)
        self.assertEqual("1", browser_handover["configured_version"])
        self.assertEqual("1", browser_handover["claimed_version"])
        self.assertFalse(browser_handover["open"])

    def test_bootstrap_page_rejects_untrusted_remote_before_first_admin_exists(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        web_app.add_default_routes()
        client = TestApp(web_app)

        response = client.get(
            "/bootstrap?proof=test-proof",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "203.0.113.10",
            },
            expect_errors=True,
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("Bootstrap access is limited", response.text)

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

        status_response = client.get("/server/status", extra_environ=trusted_headers, expect_errors=True)
        self.assertEqual(401, status_response.status_int)

        Timer(0.1, web_app.stop).start()
        stream_response = client.get("/server/stream", extra_environ=trusted_headers, expect_errors=True)
        self.assertEqual(401, stream_response.status_int)

    def test_bootstrap_limited_ui_session_allows_only_trusted_remote_shell_and_bootstrap_routes(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)

        @web_app.route("/server/status", required_scope="read")
        def _status():
            return "ok"

        @web_app.route("/server/admin/bootstrap-check", required_scope="admin", allow_first_admin_bootstrap=True)
        def _bootstrap_check():
            return "ok"

        web_app.add_default_routes()
        client = TestApp(web_app)
        bootstrap_session = empty_store.create_ui_session(["bootstrap"], bootstrap=True)
        bootstrap_cookie = "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, bootstrap_session.secret)

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(web_app, "_WebApp__html_path", html_path)
            shell_response = client.get(
                "/",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "172.25.0.1",
                    "HTTP_COOKIE": bootstrap_cookie,
                }
            )
        self.assertEqual(200, shell_response.status_int)

        bootstrap_check = client.get(
            "/server/admin/bootstrap-check",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "172.25.0.1",
                "HTTP_REFERER": "http://localhost:8800/dashboard",
                "HTTP_COOKIE": bootstrap_cookie,
            }
        )
        self.assertEqual(200, bootstrap_check.status_int)
        self.assertEqual("ok", bootstrap_check.text)

        denied_status = client.get(
            "/server/status",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "172.25.0.1",
                "HTTP_COOKIE": bootstrap_cookie,
                "HTTP_REFERER": "http://localhost:8800/dashboard",
            },
            expect_errors=True,
        )
        self.assertEqual(403, denied_status.status_int)
        self.assertIn("Session lacks scope", denied_status.text)
        self.assertIn("read", denied_status.text)

    def test_bootstrap_limited_ui_session_is_denied_on_ordinary_stream_routes(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        web_app.add_streaming_handler(QueueStreamHandler, values=["event\n"], cleanup_log=[])
        web_app.add_default_routes()
        client = TestApp(web_app)
        bootstrap_session = empty_store.create_ui_session(["bootstrap"], bootstrap=True)
        bootstrap_cookie = "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, bootstrap_session.secret)

        Timer(0.1, web_app.stop).start()
        denied_stream = client.get(
            "/server/stream",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "172.25.0.1",
                "HTTP_COOKIE": bootstrap_cookie,
                "HTTP_REFERER": "http://localhost:8800/dashboard",
            },
            expect_errors=True,
        )

        self.assertEqual(403, denied_stream.status_int)
        self.assertIn("Session lacks scope", denied_stream.text)
        self.assertIn("stream", denied_stream.text)

    def test_bootstrap_limited_ui_session_is_denied_on_ordinary_admin_routes(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)

        @web_app.route("/server/admin/ping", required_scope="admin")
        def _admin_ping():
            return "pong"

        client = TestApp(web_app)
        bootstrap_session = empty_store.create_ui_session(["bootstrap"], bootstrap=True)
        bootstrap_cookie = "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, bootstrap_session.secret)

        denied_admin = client.get(
            "/server/admin/ping",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "172.25.0.1",
                "HTTP_COOKIE": bootstrap_cookie,
                "HTTP_REFERER": "http://localhost:8800/dashboard",
            },
            expect_errors=True,
        )

        self.assertEqual(403, denied_admin.status_int)
        self.assertIn("Session lacks scope", denied_admin.text)
        self.assertIn("admin", denied_admin.text)

    def test_loopback_dashboard_deep_link_issues_ui_session_cookie(self):
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            self._issue_browser_session(client)
            response = client.get(
                "/dashboard/123e4567-e89b-12d3-a456-426614174000",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "127.0.0.1",
                },
            )

        self.assertEqual(200, response.status_int)
        self.assertIn("<html></html>", response.text)

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

    def test_trusted_bootstrap_remote_addr_allows_remember_browser_route_for_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = self._remember_trusted_browser_session(client, remote_addr="172.25.0.1")

        self.assertEqual(201, response.status_int)
        self.assertIn("seedsync_ui_session=", response.headers.get("Set-Cookie", ""))

    def test_trusted_bootstrap_remote_addr_allows_dashboard_deep_link_for_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            self._remember_trusted_browser_session(client, remote_addr="172.25.0.1")
            response = client.get(
                "/dashboard/123e4567-e89b-12d3-a456-426614174000",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "172.25.0.1",
                },
            )

        self.assertEqual(200, response.status_int)

    def test_trusted_bootstrap_remote_addr_allows_static_asset_for_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)
        self.web_app.add_default_routes()

        with tempfile.TemporaryDirectory() as html_path:
            with open(os.path.join(html_path, "index.html"), "w") as html_file:
                html_file.write("<html></html>")
            with open(os.path.join(html_path, "main.js"), "w") as asset_file:
                asset_file.write("console.log('hello');")
            object.__setattr__(self.web_app, "_WebApp__html_path", html_path)
            client = TestApp(self.web_app)
            self._remember_trusted_browser_session(client, remote_addr="172.25.0.1")
            response = client.get(
                "/main.js",
                extra_environ={
                    "HTTP_HOST": "localhost:8800",
                    "REMOTE_ADDR": "172.25.0.1",
                },
            )

        self.assertEqual(200, response.status_int)
        self.assertIn("console.log('hello');", response.text)

    def test_trusted_bootstrap_remote_addr_allows_root_route_despite_forwarded_non_loopback_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.26.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        response = client.get(
            "/",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "172.26.0.1",
                "HTTP_X_FORWARDED_HOST": "seed.example:8800",
                "HTTP_X_FORWARDED_PROTO": "https",
            },
            expect_errors=True,
        )

        self.assertEqual(302, response.status_int)
        self.assertTrue(response.headers.get("Location", "").endswith("/bootstrap"))

    def test_cross_origin_bootstrap_get_does_not_consume_first_admin_handover(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)
        web_app.add_default_routes()
        client = TestApp(web_app)

        response = client.get(
            "/bootstrap",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_ORIGIN": "http://evil.example",
                "HTTP_REFERER": "http://evil.example/attack",
            },
        )

        self.assertEqual(200, response.status_int)
        self.assertIn("Claim the first local session", response.text)
        self.assertEqual(0, empty_store.active_admin_key_count)
        self.assertTrue(empty_store.get_browser_handover_state(self.context.config)["open"])

    def test_cross_origin_first_admin_bootstrap_post_is_rejected_and_preserves_handover(self):
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)
        web_app.add_default_routes()
        client = TestApp(web_app)

        response = client.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {},
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_ORIGIN": "http://evil.example",
                "HTTP_REFERER": "http://evil.example/attack",
            },
            expect_errors=True,
        )

        self.assertEqual(401, response.status_int)
        self.assertIn("Missing API token", response.text)
        self.assertEqual(0, empty_store.active_admin_key_count)
        self.assertTrue(empty_store.get_browser_handover_state(self.context.config)["open"])
        self.assertEqual("", response.headers.get("Set-Cookie", ""))

    def test_trusted_bootstrap_remote_addr_allows_bootstrap_page_for_service_host(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.26.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = client.get(
            "/bootstrap",
            extra_environ={
                "HTTP_HOST": "myapp:8800",
                "REMOTE_ADDR": "172.26.0.1",
            },
        )

        self.assertEqual(200, response.status_int)
        self.assertIn("SeedSync browser access", response.text)

    def test_trusted_bootstrap_remote_addr_rejects_sibling_bridge_peer_without_exact_match(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.26.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)
        self.web_app.add_default_routes()
        client = TestApp(self.web_app)

        response = client.get(
            "/bootstrap",
            extra_environ={
                "HTTP_HOST": "myapp:8800",
                "REMOTE_ADDR": "172.26.0.2",
            },
            expect_errors=True,
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("trusted local runtime", response.text)

    def test_malformed_trusted_bootstrap_remote_addrs_fail_closed(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "definitely-not-a-network"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)
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
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.26.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)

        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        self._remember_trusted_browser_session(client, remote_addr="172.26.0.1", host="myapp:8800")

        response = client.post(
            "/server/ping",
            extra_environ={
                "HTTP_HOST": "myapp:8800",
                "REMOTE_ADDR": "172.26.0.1",
                "HTTP_REFERER": "http://myapp:8800/dashboard",
            }
        )

        self.assertEqual(200, response.status_int)

    def test_trusted_bootstrap_remote_cannot_access_admin_bootstrap_routes_before_first_admin_exists(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        empty_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)

        @web_app.route("/server/admin/bootstrap-check", required_scope="admin", allow_first_admin_bootstrap=True)
        def _bootstrap_check():
            return "ok"

        web_app.add_default_routes()
        client = TestApp(web_app)

        trusted_headers = {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "172.25.0.1",
            "HTTP_REFERER": "http://localhost:8800/dashboard",
        }

        allowed = client.get("/server/admin/bootstrap-check", extra_environ=trusted_headers)
        self.assertEqual(200, allowed.status_int)
        self.assertEqual("ok", allowed.text)

    def test_non_bearer_authorization_header_does_not_block_ui_session_cookie_auth(self):
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
                "HTTP_AUTHORIZATION": "Basic dXNlcjpwYXNz",
                "HTTP_REFERER": "http://localhost:8800/dashboard",
            }
        )

        self.assertEqual(200, response.status_int)

    def test_write_route_accepts_cookie_backed_ui_session_through_trusted_forwarded_origin(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)

        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        self._remember_trusted_browser_session(client, remote_addr="172.25.0.1")

        response = client.post(
            "/server/ping",
            extra_environ={
                **self._proxied_same_origin_headers(),
            }
        )

        self.assertEqual(200, response.status_int)

    def test_loopback_proxy_headers_do_not_supply_forwarded_same_origin_for_cookie_writes(self):
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
                "HTTP_ORIGIN": "https://localhost:8800",
                "HTTP_X_FORWARDED_HOST": "localhost:8800",
                "HTTP_X_FORWARDED_PROTO": "https",
            },
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("Browser-origin signal required", response.text)

    def test_configured_loopback_proxy_can_supply_forwarded_same_origin_for_cookie_writes(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "127.0.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)

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
                "HTTP_ORIGIN": "https://localhost:8800",
                "HTTP_X_FORWARDED_HOST": "localhost:8800",
                "HTTP_X_FORWARDED_PROTO": "https",
            }
        )

        self.assertEqual(200, response.status_int)
        self.assertEqual("pong", response.text)

    def test_write_route_rejects_unsafe_forwarded_proto_for_cookie_backed_ui_session(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)

        @self.web_app.route("/server/ping", method="POST", required_scope="write")
        def _ping():
            return "pong"

        self.web_app.add_default_routes()
        client = TestApp(self.web_app)
        self._remember_trusted_browser_session(client, remote_addr="172.25.0.1")

        response = client.post(
            "/server/ping",
            extra_environ={
                **self._proxied_same_origin_headers(forwarded_proto="ftp"),
            },
            expect_errors=True
        )

        self.assertEqual(403, response.status_int)
        self.assertIn("Browser-origin signal required", response.text)

    def test_sec_fetch_site_same_origin_is_not_standalone_same_origin_proof(self):
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
                "HTTP_SEC_FETCH_SITE": "same-origin",
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

    def test_non_server_prefix_route_is_not_treated_as_api_route(self):
        @self.web_app.route("/server-assets/ping")
        def _asset_ping():
            return "asset-pong"

        client = TestApp(self.web_app)
        response = client.get("/server-assets/ping")

        self.assertEqual(200, response.status_int)
        self.assertEqual("asset-pong", response.text)
