import json
from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch
from wsgiref.util import setup_testing_defaults

from common import Config, ConfigError, BreadcrumbTraceCollector
from web.auth_store import ApiKeyStore
from web.handler.config import ConfigHandler
from web.web_app import WebApp


LEGACY_TEST_API_TOKEN = "legacy-test-token"


def _invoke_route(
    web_app,
    path,
    method: str = "GET",
    api_token: str = None,
    ui_session_secret: str = None,
    body: str = None,
    content_type: str = None
):
    environ = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = method
    environ["PATH_INFO"] = path
    body_bytes = body.encode("utf-8") if isinstance(body, str) else (body or b"")
    environ["wsgi.input"] = BytesIO(body_bytes)
    environ["CONTENT_LENGTH"] = str(len(body_bytes))
    environ["HTTP_HOST"] = "localhost:8800"
    environ["REMOTE_ADDR"] = "127.0.0.1"
    environ["HTTP_REFERER"] = "http://localhost:8800/settings"
    if content_type is not None:
        environ["CONTENT_TYPE"] = content_type
    if api_token is not None:
        environ["HTTP_AUTHORIZATION"] = "Bearer {}".format(api_token)
    if ui_session_secret is not None:
        environ["HTTP_COOKIE"] = "{}={}".format(WebApp._UI_SESSION_COOKIE_NAME, ui_session_secret)

    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(web_app(environ, start_response)).decode("utf-8")
    return int(captured["status"].split()[0]), body


def _invoke_get_route(web_app, path, api_token: str = None, ui_session_secret: str = None):
    return _invoke_route(web_app, path, method="GET", api_token=api_token, ui_session_secret=ui_session_secret)


def _invoke_post_route(web_app, path, api_token: str = None, ui_session_secret: str = None):
    return _invoke_route(web_app, path, method="POST", api_token=api_token, ui_session_secret=ui_session_secret)


def _invoke_post_json_route(
    web_app,
    path,
    json_body,
    api_token: str = None,
    ui_session_secret: str = None
):
    return _invoke_route(
        web_app,
        path,
        method="POST",
        api_token=api_token,
        ui_session_secret=ui_session_secret,
        body=json.dumps(json_body),
        content_type="application/json"
    )


class TestConfigHandlerGet(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.handler = ConfigHandler(self.config)

    @patch("web.handler.config.SerializeConfig")
    def test_get_returns_serialized_config(self, mock_serialize):
        mock_serialize.config.return_value = '{"test":"data"}'

        response = self.handler._ConfigHandler__handle_get_config()

        self.assertEqual(200, response.status_code)
        self.assertEqual('{"test":"data"}', response.body)


class TestConfigHandlerSet(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.handler = ConfigHandler(self.config)

    def test_set_valid_calls_inner_property_setter(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp", "remote_path", "/path/with spaces"
        )

        self.assertEqual(200, response.status_code)
        inner.set_property.assert_called_once_with("remote_path", "/path/with spaces")

    def test_set_missing_section_returns_404(self):
        self.config.has_section.return_value = False

        response = self.handler._ConfigHandler__handle_set_config(
            "missing", "key", "value"
        )

        self.assertEqual(404, response.status_code)

    def test_set_missing_key_returns_404(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = False
        self.config.general = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "general", "badkey", "value"
        )

        self.assertEqual(404, response.status_code)

    def test_set_bad_value_returns_400(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        inner.set_property.side_effect = ConfigError("Invalid")
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp", "remote_address", "bad"
        )

        self.assertEqual(400, response.status_code)

    def test_set_sensitive_field_success_response_is_redacted(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp",
            "remote_password",
            "super-secret-password"
        )

        self.assertEqual(200, response.status_code)
        inner.set_property.assert_called_once_with("remote_password", "super-secret-password")
        self.assertEqual("lftp.remote_password set to {}".format(Config.REDACTED_SENTINEL), response.body)
        self.assertNotIn("super-secret-password", response.body)

    def test_set_api_token_via_body_is_forbidden(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.general = inner

        response = self.handler._ConfigHandler__handle_set_config("general", "api_token", "super-secret-token")

        self.assertEqual(403, response.status_code)
        self.assertEqual(
            "Section 'general' option 'api_token' cannot be set via request body",
            response.body
        )
        inner.set_property.assert_not_called()

    def test_set_config_api_redaction_via_body_is_forbidden(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.general = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "general",
            "config_api_redact_remote_details",
            False
        )

        self.assertEqual(403, response.status_code)
        self.assertEqual(
            "Section 'general' option 'config_api_redact_remote_details' cannot be set via request body",
            response.body
        )
        inner.set_property.assert_not_called()

    def test_set_breadcrumb_trace_enabled_calls_sync_hook(self):
        sync_hook = MagicMock()
        handler = ConfigHandler(self.config, breadcrumb_trace_sync=sync_hook)
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.general = inner

        response = handler._ConfigHandler__handle_set_config("general", "breadcrumb_trace_enabled", True)

        self.assertEqual(200, response.status_code)
        inner.set_property.assert_called_once_with("breadcrumb_trace_enabled", True)
        sync_hook.assert_called_once()

    def test_set_breadcrumb_trace_enabled_updates_active_gate(self):
        config = Config()
        config.general.breadcrumb_trace_enabled = True
        collector = BreadcrumbTraceCollector(lambda: config.general.breadcrumb_trace_enabled, max_entries=2)
        emitter = collector.create_emitter()
        handler = ConfigHandler(config, breadcrumb_trace_sync=collector.sync_enabled_state)

        emitter.record("controller", "start", {"phase": "init"}, stage="scan")
        self.assertEqual(1, collector.snapshot()["entry_count"])
        response = handler._ConfigHandler__handle_set_config("general", "breadcrumb_trace_enabled", False)
        self.assertEqual(200, response.status_code)
        emitter.record("controller", "start", {"phase": "disabled"}, stage="scan")

        snapshot = collector.snapshot()
        self.assertEqual(False, snapshot["enabled"])
        self.assertEqual(1, snapshot["entry_count"])
        self.assertEqual("init", snapshot["entries"][0]["details"]["phase"])

    def test_set_trusted_browser_bootstrap_remote_addrs_via_body_is_forbidden(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.general = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "general",
            "trusted_browser_bootstrap_remote_addrs",
            "172.25.0.1/32"
        )

        self.assertEqual(403, response.status_code)
        self.assertEqual(
            "Section 'general' option 'trusted_browser_bootstrap_remote_addrs' cannot be set via request body",
            response.body
        )
        inner.set_property.assert_not_called()


class TestConfigHandlerRoutes(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = "C:\\Git\\seedsync\\src\\python\\tests"
        self.context.status = MagicMock()
        self.context.config = Config()
        self.auth_store = ApiKeyStore()
        self.admin_api_token = self.auth_store.create_api_key("unit-admin", ["admin"])["secret"]
        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)

    def test_get_route_honors_remote_detail_redaction_opt_out(self):
        config = Config()
        config.general.api_token = "super-secret-token"
        config.general.config_api_redact_remote_details = False
        config.lftp.remote_address = "server.remote.com"
        config.lftp.remote_username = "user-on-remote-server"
        config.lftp.remote_password = "secret123"
        config.lftp.remote_path = "/remote/server/path"
        config.lftp.remote_path_to_scan_script = "/remote/server/path/to/script"
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_get_route(
            self.web_app,
            "/server/config/get",
            api_token=self.admin_api_token,
        )
        out_dict = json.loads(body)

        self.assertEqual(200, status_code)
        self.assertEqual("server.remote.com", out_dict["lftp"]["remote_address"])
        self.assertEqual("user-on-remote-server", out_dict["lftp"]["remote_username"])
        self.assertEqual("/remote/server/path", out_dict["lftp"]["remote_path"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_password"])
        self.assertEqual("**REDACTED**", out_dict["general"]["api_token"])

    def test_set_route_blocks_redaction_toggle_from_body(self):
        config = Config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/general/config_api_redact_remote_details",
            {"value": False},
            api_token=self.admin_api_token,
        )

        self.assertEqual(403, status_code)
        self.assertEqual(True, config.general.config_api_redact_remote_details)
        self.assertIn(
            "Section 'general' option 'config_api_redact_remote_details' cannot be set via request body",
            body
        )

    def test_set_route_blocks_trusted_browser_bootstrap_remote_addrs_from_body(self):
        config = Config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/general/trusted_browser_bootstrap_remote_addrs",
            {"value": "172.25.0.1/32"},
            api_token=self.admin_api_token,
        )

        self.assertEqual(403, status_code)
        self.assertIsNone(config.general.trusted_browser_bootstrap_remote_addrs)
        self.assertIn(
            "Section 'general' option 'trusted_browser_bootstrap_remote_addrs' cannot be set via request body",
            body
        )

    def test_set_route_allows_empty_remote_password_from_body(self):
        config = Config()
        config.lftp.remote_password = "existing-password"
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/lftp/remote_password",
            {"value": ""},
            api_token=self.admin_api_token,
        )

        self.assertEqual(200, status_code)
        self.assertEqual("", config.lftp.remote_password)
        self.assertEqual("lftp.remote_password set to {}".format(Config.REDACTED_SENTINEL), body)

    def test_set_route_rejects_whitespace_remote_password_from_body(self):
        config = Config()
        config.lftp.remote_password = "existing-password"
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/lftp/remote_password",
            {"value": "  "},
            api_token=self.admin_api_token,
        )

        self.assertEqual(400, status_code)
        self.assertEqual("existing-password", config.lftp.remote_password)
        self.assertIn("Bad config: Lftp.remote_password is empty", body)

    def test_set_route_treats_literal_empty_sentinel_as_value(self):
        config = Config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/lftp/remote_password",
            {"value": "__empty__"},
            api_token=self.admin_api_token,
        )

        self.assertEqual(200, status_code)
        self.assertEqual("__empty__", config.lftp.remote_password)
        self.assertEqual("lftp.remote_password set to {}".format(Config.REDACTED_SENTINEL), body)

    def test_set_route_allows_logging_format_from_body(self):
        config = Config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/logging/log_format",
            {"value": "json"},
            api_token=self.admin_api_token,
        )

        self.assertEqual(200, status_code)
        self.assertEqual("json", config.logging.log_format)
        self.assertIn("logging.log_format set to json", body)

    def test_set_route_rejects_old_url_value_shape(self):
        config = Config()
        ConfigHandler(config).add_routes(self.web_app)
        ui_session = self.auth_store.create_ui_session(["admin"])

        status_code, body = _invoke_post_route(
            self.web_app,
            "/server/config/set/general/debug/True",
            ui_session_secret=ui_session.secret,
        )

        self.assertEqual(404, status_code)
        self.assertIsNone(config.general.debug)

    def test_get_route_rejects_legacy_token(self):
        auth_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=auth_store)
        ConfigHandler(Config()).add_routes(web_app)

        status_code, body = _invoke_get_route(web_app, "/server/config/get", api_token=LEGACY_TEST_API_TOKEN)

        self.assertEqual(401, status_code)
        self.assertIn("Invalid API token", body)
