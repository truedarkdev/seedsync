import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from common import Config
from controller.notifier import NotificationError
from web.auth_store import ApiKeyStore
from web.handler.notifications import NotificationsAdminHandler
from web.web_app import WebApp
from webtest import TestApp


class TestNotificationsAdminHandler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = Config()
        self.config.to_file(os.path.join(self.temp_dir.name, "config.ini"))
        self.notifier = Mock()
        self.notifier.public_config.return_value = {
            "enabled": True, "provider": "webhook", "webhook_url_configured": True,
            "hmac_secret_configured": True, "allow_private_networks": False,
            "apprise_url_configured": False, "apprise_tag": "",
            "download_complete": True, "extraction_complete": True, "delete_complete": True,
        }
        self.context = Mock()
        self.context.logger.getChild.return_value = Mock()
        self.context.args.html_path = self.temp_dir.name
        self.context.status = Mock()
        self.context.config = self.config
        self.store = ApiKeyStore(file_path=os.path.join(self.temp_dir.name, "keys.json"))
        admin_result = self.store.create_api_key("admin", ["admin"])
        self.admin = admin_result["secret"]
        self.admin_session = self.store.create_remembered_browser_session_for_api_key(admin_result["record"].id).secret
        self.writer = self.store.create_api_key("writer", ["write"])["secret"]
        app = WebApp(self.context, Mock(), auth_store=self.store)
        NotificationsAdminHandler(self.config, self.notifier).add_routes(app)
        self.app = TestApp(app)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def headers(secret, origin="http://localhost:8800", fetch_site="same-origin"):
        return {
            "HTTP_AUTHORIZATION": "Bearer " + secret,
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_ORIGIN": origin,
            "HTTP_REFERER": origin + "/settings",
            "HTTP_SEC_FETCH_SITE": fetch_site,
        }

    @patch("web.handler.notifications.validate_webhook_url")
    def test_admin_same_origin_can_atomically_update_write_only_settings(self, validate):
        response = self.app.post_json(
            "/server/admin/notifications/v1/config",
            {"enabled": True, "webhook_url": "https://hooks.example.test/x", "hmac_secret": "secret",
             "allow_private_networks": False, "download_complete": True,
             "extraction_complete": True, "delete_complete": True},
            extra_environ=self.headers(self.admin),
        )
        self.assertEqual(200, response.status_int)
        self.assertNotIn("hooks.example", response.text)
        self.assertNotIn('"hmac_secret":', response.text)
        self.assertEqual("secret", self.config.notifications.hmac_secret)
        self.notifier.reconfigure.assert_called_once_with(self.config)

    def test_write_scope_and_cross_origin_are_rejected(self):
        body = {"enabled": False}
        writer = self.app.post_json(
            "/server/admin/notifications/v1/config", body,
            extra_environ=self.headers(self.writer), expect_errors=True,
        )
        cross_origin_headers = self.headers(self.admin, "https://evil.example", "cross-site")
        cross_origin_headers.pop("HTTP_AUTHORIZATION")
        cross_origin_headers["HTTP_COOKIE"] = "{}={}".format(
            WebApp._UI_SESSION_COOKIE_NAME, self.admin_session
        )
        cross_origin = self.app.post_json(
            "/server/admin/notifications/v1/config", body,
            extra_environ=cross_origin_headers, expect_errors=True,
        )
        self.assertEqual(403, writer.status_int)
        self.assertEqual(403, cross_origin.status_int)

    def test_test_delivery_returns_only_generic_error(self):
        self.notifier.test_delivery.side_effect = NotificationError("contains sensitive upstream detail")
        response = self.app.post_json(
            "/server/admin/notifications/v1/test", {},
            extra_environ=self.headers(self.admin), expect_errors=True,
        )
        self.assertEqual(502, response.status_int)
        self.assertEqual({"error": "Notification test delivery failed"}, response.json)

    @patch("web.handler.notifications.validate_apprise_url")
    def test_admin_can_select_apprise_without_exposing_endpoint_key(self, validate):
        self.notifier.public_config.return_value.update({
            "provider": "apprise", "apprise_url_configured": True, "apprise_tag": "seedbox",
        })
        response = self.app.post_json(
            "/server/admin/notifications/v1/config",
            {"enabled": True, "provider": "apprise",
             "apprise_url": "https://apprise.example.test/notify/private-key",
             "apprise_tag": "seedbox", "allow_private_networks": False,
             "download_complete": True, "extraction_complete": True, "delete_complete": True},
            extra_environ=self.headers(self.admin),
        )
        self.assertEqual(200, response.status_int)
        self.assertEqual("apprise", self.config.notifications.provider)
        self.assertNotIn("private-key", response.text)
        validate.assert_called_once()
