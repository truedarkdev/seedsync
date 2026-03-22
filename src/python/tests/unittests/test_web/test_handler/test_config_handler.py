import json
from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import quote
from wsgiref.util import setup_testing_defaults

from common import Config, ConfigError
from web.handler.config import ConfigHandler
from web.web_app import WebApp


def _invoke_get_route(web_app, path):
    environ = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = "GET"
    environ["PATH_INFO"] = path
    environ["wsgi.input"] = BytesIO(b"")

    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(web_app(environ, start_response)).decode("utf-8")
    return int(captured["status"].split()[0]), body


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
            "lftp", "remote_path", quote("/path/with spaces")
        )

        self.assertEqual(200, response.status_code)
        inner.set_property.assert_called_once_with("remote_path", "/path/with spaces")

    def test_set_missing_section_returns_404(self):
        self.config.has_section.return_value = False

        response = self.handler._ConfigHandler__handle_set_config(
            "missing", "key", quote("value")
        )

        self.assertEqual(404, response.status_code)

    def test_set_missing_key_returns_404(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = False
        self.config.general = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "general", "badkey", quote("value")
        )

        self.assertEqual(404, response.status_code)

    def test_set_bad_value_returns_400(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        inner.set_property.side_effect = ConfigError("Invalid")
        self.config.lftp = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "lftp", "remote_address", quote("bad")
        )

        self.assertEqual(400, response.status_code)

    def test_set_api_token_via_url_is_forbidden(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.general = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "general", "api_token", quote("super-secret-token")
        )

        self.assertEqual(403, response.status_code)
        self.assertEqual(
            "Section 'general' option 'api_token' cannot be set via URL",
            response.body
        )
        inner.set_property.assert_not_called()

    def test_set_config_api_redaction_via_url_is_forbidden(self):
        self.config.has_section.return_value = True
        inner = MagicMock()
        inner.has_property.return_value = True
        self.config.general = inner

        response = self.handler._ConfigHandler__handle_set_config(
            "general", "config_api_redact_remote_details", quote("False")
        )

        self.assertEqual(403, response.status_code)
        self.assertEqual(
            "Section 'general' option 'config_api_redact_remote_details' cannot be set via URL",
            response.body
        )
        inner.set_property.assert_not_called()


class TestConfigHandlerRoutes(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = "C:\\Git\\seedsync\\src\\python\\tests"
        self.context.status = MagicMock()
        self.web_app = WebApp(self.context, MagicMock())

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

        status_code, body = _invoke_get_route(self.web_app, "/server/config/get")
        out_dict = json.loads(body)

        self.assertEqual(200, status_code)
        self.assertEqual("server.remote.com", out_dict["lftp"]["remote_address"])
        self.assertEqual("user-on-remote-server", out_dict["lftp"]["remote_username"])
        self.assertEqual("/remote/server/path", out_dict["lftp"]["remote_path"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_password"])
        self.assertEqual("**REDACTED**", out_dict["general"]["api_token"])

    def test_set_route_blocks_redaction_toggle(self):
        config = Config()
        ConfigHandler(config).add_routes(self.web_app)

        status_code, body = _invoke_get_route(
            self.web_app,
            "/server/config/set/general/config_api_redact_remote_details/False"
        )

        self.assertEqual(403, status_code)
        self.assertEqual(
            "Section 'general' option 'config_api_redact_remote_details' cannot be set via URL",
            body
        )
