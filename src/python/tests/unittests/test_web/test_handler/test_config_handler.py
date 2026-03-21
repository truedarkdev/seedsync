import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from common import ConfigError
from web.handler.config import ConfigHandler


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
