import json
import os
import shutil
import tempfile
from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch
from wsgiref.util import setup_testing_defaults

from common import Config, ConfigError, BreadcrumbTraceCollector, Localization
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

    def test_set_protocol_to_ftps_rejects_blank_transfer_password(self):
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        inner.protocol = "sftp"
        inner.remote_password = ""
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp",
            "protocol",
            "ftps"
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("sftp", inner.protocol)
        self.assertEqual("", inner.remote_password)
        self.assertEqual("FTPS requires a transfer password.", response.body)

    def test_set_blank_remote_password_remains_valid_for_sftp_key_auth(self):
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        inner.protocol = "sftp"
        inner.use_ssh_key = True
        inner.remote_password = ""
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp",
            "remote_password",
            ""
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("", inner.remote_password)

    def test_set_transfer_backend_to_rclone_forces_sftp_protocol(self):
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        inner.transfer_backend = "lftp"
        inner.protocol = "ftps"
        inner.remote_password = "password"
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp",
            "transfer_backend",
            "rclone"
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("rclone", inner.transfer_backend)
        self.assertEqual("sftp", inner.protocol)

    def test_set_remote_username_rejects_control_characters(self):
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp",
            "remote_username",
            "user\r\nname"
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("contains control characters", response.body)

    def test_set_protocol_to_ftps_is_ignored_for_rclone_backend(self):
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        inner.transfer_backend = "rclone"
        inner.protocol = "sftp"
        inner.remote_password = ""
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp",
            "protocol",
            "ftps"
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("rclone", inner.transfer_backend)
        self.assertEqual("sftp", inner.protocol)

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

    def test_set_lftp_legacy_password_argv_via_body_is_forbidden(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp", "use_legacy_lftp_password_argv", True
        )

        self.assertEqual(403, response.status_code)
        self.assertIn("cannot be set via request body", response.body)
        inner.set_property.assert_not_called()

    def test_generic_config_route_cannot_update_notification_settings(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.notifications = inner

        for field, value in (
            ("provider", "apprise"),
            ("apprise_url", "https://apprise.example.test/notify/key"),
            ("apprise_tag", "seedbox"),
            ("allow_private_networks", True),
        ):
            with self.subTest(field=field):
                response = self.handler._ConfigHandler__handle_set_config(
                    "notifications", field, value
                )
                self.assertEqual(403, response.status_code)
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
        with tempfile.TemporaryDirectory(prefix="test_config_handler_set_") as temp_dir:
            config_path = os.path.join(temp_dir, "settings.cfg")
            config = Config()
            config.file_path = config_path
            config.to_file()
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
            with open(config_path, "r", encoding="utf-8") as config_file:
                self.assertIn("breadcrumb_trace_enabled = False", config_file.read())

    def test_set_general_verbose_requests_reconfigure_after_persist(self):
        reconfigure_hook = MagicMock()
        handler = ConfigHandler(self.config, lftp_reconfigure_request=reconfigure_hook)
        self.config.has_section.return_value = True
        inner = Config.General()
        inner.verbose = False
        self.config.general = inner

        response = handler._ConfigHandler__handle_set_config("general", "verbose", True)

        self.assertEqual(200, response.status_code)
        self.assertTrue(inner.verbose)
        reconfigure_hook.assert_called_once_with()

    def test_set_general_exclude_patterns_requests_reconfigure_after_persist(self):
        reconfigure_hook = MagicMock()
        handler = ConfigHandler(self.config, lftp_reconfigure_request=reconfigure_hook)
        self.config.has_section.return_value = True
        inner = Config.General()
        inner.exclude_patterns = ""
        self.config.general = inner

        response = handler._ConfigHandler__handle_set_config("general", "exclude_patterns", "*.nfo,Season */*.nfo")

        self.assertEqual(200, response.status_code)
        self.assertEqual("*.nfo,Season */*.nfo", inner.exclude_patterns)
        reconfigure_hook.assert_called_once_with()

    def test_set_lftp_tuning_key_requests_reconfigure_after_persist(self):
        reconfigure_hook = MagicMock()
        handler = ConfigHandler(self.config, lftp_reconfigure_request=reconfigure_hook)
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        inner.net_socket_buffer = "512K"
        self.config.lftp = inner

        response = handler._ConfigHandler__handle_set_config("lftp", "net_socket_buffer", "8M")

        self.assertEqual(200, response.status_code)
        self.assertEqual("8M", inner.net_socket_buffer)
        reconfigure_hook.assert_called_once_with()

    def test_set_validate_xfer_verify_requests_reconfigure_after_persist(self):
        reconfigure_hook = MagicMock()
        handler = ConfigHandler(self.config, lftp_reconfigure_request=reconfigure_hook)
        self.config.has_section.return_value = True
        inner = Config.Validate()
        inner.xfer_verify = True
        self.config.validate = inner

        response = handler._ConfigHandler__handle_set_config("validate", "xfer_verify", False)

        self.assertEqual(200, response.status_code)
        self.assertFalse(inner.xfer_verify)
        reconfigure_hook.assert_called_once_with()

    def test_set_lftp_non_tuning_key_does_not_request_reconfigure(self):
        reconfigure_hook = MagicMock()
        handler = ConfigHandler(self.config, lftp_reconfigure_request=reconfigure_hook)
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        inner.remote_path = "/old/path"
        self.config.lftp = inner

        response = handler._ConfigHandler__handle_set_config("lftp", "remote_path", "/new/path")

        self.assertEqual(200, response.status_code)
        self.assertEqual("/new/path", inner.remote_path)
        reconfigure_hook.assert_not_called()

    def test_set_lftp_scanner_dependent_key_does_not_request_reconfigure(self):
        reconfigure_hook = MagicMock()
        handler = ConfigHandler(self.config, lftp_reconfigure_request=reconfigure_hook)
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        inner.use_temp_file = False
        self.config.lftp = inner

        response = handler._ConfigHandler__handle_set_config("lftp", "use_temp_file", True)

        self.assertEqual(200, response.status_code)
        self.assertTrue(inner.use_temp_file)
        reconfigure_hook.assert_not_called()

    def test_set_lftp_tuning_key_does_not_request_reconfigure_on_validation_failure(self):
        reconfigure_hook = MagicMock()
        handler = ConfigHandler(self.config, lftp_reconfigure_request=reconfigure_hook)
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        self.config.lftp = inner

        response = handler._ConfigHandler__handle_set_config("lftp", "net_socket_buffer", "bad")

        self.assertEqual(400, response.status_code)
        self.assertEqual(None, inner.net_socket_buffer)
        reconfigure_hook.assert_not_called()

    def test_set_lftp_tuning_key_skips_reconfigure_on_persist_failure(self):
        reconfigure_hook = MagicMock()
        handler = ConfigHandler(self.config, lftp_reconfigure_request=reconfigure_hook)
        self.config.has_section.return_value = True
        inner = Config.Lftp()
        self.config.lftp = inner

        with patch.object(self.config, "to_file", side_effect=OSError("disk full")) as mock_to_file:
            response = handler._ConfigHandler__handle_set_config("lftp", "rate_limit", "512K")

        self.assertEqual(500, response.status_code)
        self.assertEqual("Failed to persist config lftp.rate_limit", response.body)
        self.assertIsNone(inner.rate_limit)
        mock_to_file.assert_called_once_with()
        reconfigure_hook.assert_not_called()

    def test_obsolete_bootstrap_allowlist_is_not_a_config_option(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = False
        self.config.general = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "general",
            "trusted_browser_bootstrap_remote_addrs",
            "172.25.0.1/32"
        )

        self.assertEqual(404, response.status_code)
        self.assertIn("has no option 'trusted_browser_bootstrap_remote_addrs'", response.body)
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
        self.config_dir = tempfile.mkdtemp(prefix="test_config_handler_routes_")
        self.addCleanup(shutil.rmtree, self.config_dir, ignore_errors=True)
        self.config_path = os.path.join(self.config_dir, "settings.cfg")

    def _new_config(self):
        config = Config()
        config.file_path = self.config_path
        config.to_file()
        return config

    def _read_config_contents(self):
        with open(self.config_path, "r", encoding="utf-8") as config_file:
            return config_file.read()

    def test_get_route_honors_remote_detail_redaction_opt_out(self):
        config = self._new_config()
        config.general.api_token = "super-secret-token"
        config.general.config_api_redact_remote_details = False
        config.lftp.remote_address = "server.remote.com"
        config.lftp.remote_username = "user-on-remote-server"
        config.lftp.remote_password = "secret123"
        config.lftp.remote_path = "/remote/server/path"
        config.lftp.remote_path_to_scan_script = "/remote/server/path/to/script"
        config.lftp.remote_python_path = "/home/user/.pyenv/shims/python3"
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
        self.assertEqual("/home/user/.pyenv/shims/python3", out_dict["lftp"]["remote_python_path"])
        self.assertEqual(True, out_dict["validate"]["xfer_verify"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_password"])
        self.assertEqual("**REDACTED**", out_dict["general"]["api_token"])

    def test_legacy_lftp_password_argv_option_is_file_only_in_config_api(self):
        config = self._new_config()
        config.lftp.use_legacy_lftp_password_argv = True
        config.to_file()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_get_route(
            self.web_app, "/server/config/get", api_token=self.admin_api_token,
        )
        out_dict = json.loads(body)
        self.assertEqual(200, status_code)
        self.assertNotIn("use_legacy_lftp_password_argv", out_dict["lftp"])
        self.assertNotIn("use_legacy_lftp_password_argv", out_dict["restart_required"]["lftp"])

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/lftp/use_legacy_lftp_password_argv",
            {"value": False},
            api_token=self.admin_api_token,
        )
        self.assertEqual(403, status_code)
        self.assertTrue(config.lftp.use_legacy_lftp_password_argv)
        self.assertIn("cannot be set via request body", body)

    def test_set_route_blocks_redaction_toggle_from_body(self):
        config = self._new_config()
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

    def test_set_route_blocks_browser_auth_compatibility_toggle_from_body(self):
        config = self._new_config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/general/disable_browser_auth",
            {"value": True},
            api_token=self.admin_api_token,
        )

        self.assertEqual(403, status_code)
        self.assertFalse(config.general.disable_browser_auth)
        self.assertIn(
            "Section 'general' option 'disable_browser_auth' cannot be set via request body",
            body
        )

    def test_open_browser_auth_does_not_allow_remote_compatibility_toggle(self):
        self.context.config.general.disable_browser_auth = True
        config = self._new_config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/general/disable_browser_auth",
            {"value": False},
        )

        self.assertEqual(403, status_code)
        self.assertFalse(config.general.disable_browser_auth)
        self.assertIn(
            "Section 'general' option 'disable_browser_auth' cannot be set via request body",
            body,
        )

    def test_set_route_rejects_obsolete_bootstrap_allowlist_option(self):
        config = self._new_config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/general/trusted_browser_bootstrap_remote_addrs",
            {"value": "172.25.0.1/32"},
            api_token=self.admin_api_token,
        )

        self.assertEqual(404, status_code)
        self.assertFalse(config.general.has_property("trusted_browser_bootstrap_remote_addrs"))
        self.assertIn("has no option 'trusted_browser_bootstrap_remote_addrs'", body)

    def test_set_route_allows_empty_remote_password_from_body(self):
        config = self._new_config()
        config.lftp.remote_password = "existing-password"
        config.lftp.use_ssh_key = True
        config.lftp.protocol = "sftp"
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

    def test_set_route_rejects_ftps_with_blank_remote_password_from_body(self):
        config = self._new_config()
        config.lftp.remote_password = ""
        config.lftp.use_ssh_key = True
        config.lftp.protocol = "sftp"
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/lftp/protocol",
            {"value": "ftps"},
            api_token=self.admin_api_token,
        )

        self.assertEqual(400, status_code)
        self.assertEqual("sftp", config.lftp.protocol)
        self.assertIn(Localization.Error.FTPS_TRANSFER_PASSWORD_REQUIRED, body)

    def test_set_route_rejects_noncanonical_ftps_with_blank_remote_password_from_body(self):
        config = self._new_config()
        config.lftp.remote_password = ""
        config.lftp.use_ssh_key = True
        config.lftp.protocol = "sftp"
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/lftp/protocol",
            {"value": " FTPS "},
            api_token=self.admin_api_token,
        )

        self.assertEqual(400, status_code)
        self.assertEqual("sftp", config.lftp.protocol)
        self.assertIn(Localization.Error.FTPS_TRANSFER_PASSWORD_REQUIRED, body)

    def test_set_route_rejects_whitespace_remote_password_from_body(self):
        config = self._new_config()
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

    def test_set_route_rejects_control_character_remote_password_from_body(self):
        for bad_password in ("line\nbreak", "carriage\rreturn", "tab\tvalue", "null\x00value", "delete\x7fvalue", "escape\x1bvalue"):
            with self.subTest(bad_password=repr(bad_password)):
                config = self._new_config()
                config.lftp.remote_password = "existing-password"
                ConfigHandler(config).add_routes(self.web_app)

                status_code, body = _invoke_post_json_route(
                    self.web_app,
                    "/server/config/set/lftp/remote_password",
                    {"value": bad_password},
                    api_token=self.admin_api_token,
                )

                self.assertEqual(400, status_code)
                self.assertEqual("existing-password", config.lftp.remote_password)
                self.assertIn("contains control characters", body)

    def test_set_route_treats_literal_empty_sentinel_as_value(self):
        config = self._new_config()
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
        config = self._new_config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/logging/log_format",
            {"value": "JSON"},
            api_token=self.admin_api_token,
        )

        self.assertEqual(200, status_code)
        self.assertEqual("json", config.logging.log_format)
        self.assertIn("logging.log_format set to json", body)

    def test_set_route_rejects_invalid_logging_format_from_body(self):
        config = self._new_config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_post_json_route(
            self.web_app,
            "/server/config/set/logging/log_format",
            {"value": "text"},
            api_token=self.admin_api_token,
        )

        self.assertEqual(400, status_code)
        self.assertEqual("standard", config.logging.log_format)
        self.assertIn("Bad config: Logging.log_format (text) must be either standard or json", body)

    def test_set_route_rejects_old_url_value_shape(self):
        config = self._new_config()
        ConfigHandler(config).add_routes(self.web_app)
        ui_session = self.auth_store.create_ui_session(["admin"])

        status_code, body = _invoke_post_route(
            self.web_app,
            "/server/config/set/general/debug/True",
            ui_session_secret=ui_session.secret,
        )

        self.assertEqual(404, status_code)
        self.assertEqual("INFO", config.general.log_level)

    def test_set_route_accepts_legacy_debug_body(self):
        config = self._new_config()
        ConfigHandler(config).add_routes(self.web_app)

        for value, expected_level in ((True, "DEBUG"), (False, "INFO")):
            with self.subTest(value=value):
                status_code, body = _invoke_post_json_route(
                    self.web_app,
                    "/server/config/set/general/debug",
                    {"value": value},
                    api_token=self.admin_api_token,
                )

                self.assertEqual(200, status_code)
                self.assertEqual(expected_level, config.general.log_level)
                self.assertIn("log_level = {}".format(expected_level), self._read_config_contents())
                self.assertIn("general.debug set to {}".format(value), body)

    def test_get_route_rejects_legacy_token(self):
        auth_store = ApiKeyStore()
        web_app = WebApp(self.context, MagicMock(), auth_store=auth_store)
        ConfigHandler(Config()).add_routes(web_app)

        status_code, body = _invoke_get_route(web_app, "/server/config/get", api_token=LEGACY_TEST_API_TOKEN)

        self.assertEqual(401, status_code)
        self.assertIn("Invalid API token", body)
