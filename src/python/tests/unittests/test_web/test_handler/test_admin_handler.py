import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from common import Config
from web.auth_store import ApiKeyStore
from web.handler.admin import AdminHandler
from web.web_app import WebApp
from webtest import TestApp


LEGACY_TEST_API_TOKEN = "legacy-test-token"


class TestAdminHandler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_admin_handler")
        self.store_path = os.path.join(self.temp_dir, "api-keys.json")

        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = self.temp_dir
        self.context.status = MagicMock()
        self.context.config = Config()
        self.context.config.general.api_token = LEGACY_TEST_API_TOKEN

        self.auth_store = ApiKeyStore(file_path=self.store_path)
        created = self.auth_store.create_api_key("admin", ["admin"])
        self.admin_secret = created["secret"]
        self.admin_key_id = created["record"].id

        self.web_app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        AdminHandler(self.context.config, self.auth_store).add_routes(self.web_app)
        self.test_app = TestApp(self.web_app)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @staticmethod
    def _auth_headers(secret: str):
        return {"HTTP_AUTHORIZATION": "Bearer {}".format(secret)}

    @staticmethod
    def _same_origin_headers(origin: str = "http://localhost:8800"):
        return {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_ORIGIN": origin,
            "HTTP_REFERER": "{}/dashboard".format(origin),
        }

    def test_legacy_token_is_rejected_for_admin_routes(self):
        resp = self.test_app.get(
            "/server/admin/migration/v1",
            extra_environ=self._auth_headers(LEGACY_TEST_API_TOKEN),
            expect_errors=True
        )

        self.assertEqual(403, resp.status_int)
        self.assertIn("Legacy general.api_token cannot access admin endpoints", str(resp.html))

    def test_first_admin_bootstrap_requires_loopback_and_same_origin_browser_signal(self):
        empty_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "bootstrap-api-keys.json"))
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)
        test_app = TestApp(web_app)

        rejected = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "remote-admin"},
            extra_environ={"HTTP_HOST": "seed.example:8800", "REMOTE_ADDR": "203.0.113.10"},
            expect_errors=True
        )
        self.assertEqual(401, rejected.status_int)

        rejected_host_only = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "spoofed-loopback-host"},
            extra_environ={"HTTP_HOST": "localhost:8800", "REMOTE_ADDR": "203.0.113.10"},
            expect_errors=True
        )
        self.assertEqual(401, rejected_host_only.status_int)

        rejected_proxied = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "proxied-loopback-admin"},
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_ORIGIN": "https://seed.example:8800",
                "HTTP_REFERER": "https://seed.example:8800/bootstrap",
                "HTTP_X_FORWARDED_HOST": "seed.example:8800",
                "HTTP_X_FORWARDED_PROTO": "https",
            },
            expect_errors=True
        )
        self.assertEqual(401, rejected_proxied.status_int)

        rejected_cross_origin = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "cross-site-admin"},
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_ORIGIN": "http://evil.example:8800",
                "HTTP_REFERER": "http://evil.example:8800/bootstrap",
            },
            expect_errors=True
        )
        self.assertEqual(401, rejected_cross_origin.status_int)
        self.assertEqual(0, empty_store.active_admin_key_count)

        allowed = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "first-admin"},
            extra_environ=self._same_origin_headers()
        )
        allowed_payload = json.loads(allowed.text)
        self.assertEqual(201, allowed.status_int)
        self.assertEqual(["admin"], allowed_payload["key"]["scopes"])
        self.assertIn("secret", allowed_payload)

    def test_first_admin_bootstrap_rejects_loopback_transport_with_non_loopback_host(self):
        empty_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "bootstrap-api-keys-mismatch.json"))
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)
        test_app = TestApp(web_app)

        rejected = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "host-mismatch-admin"},
            extra_environ={"HTTP_HOST": "seed.example:8800", "REMOTE_ADDR": "127.0.0.1"},
            expect_errors=True
        )

        self.assertEqual(401, rejected.status_int)

    def test_first_admin_bootstrap_is_not_available_after_admin_exists(self):
        resp = self.test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "another-admin"},
            extra_environ={"HTTP_HOST": "localhost:8800", "REMOTE_ADDR": "127.0.0.1"},
            expect_errors=True
        )

        self.assertEqual(401, resp.status_int)

    def test_migration_state_reports_legacy_compatibility(self):
        resp = self.test_app.get(
            "/server/admin/migration/v1",
            extra_environ=self._auth_headers(self.admin_secret)
        )

        payload = json.loads(resp.text)
        self.assertEqual(200, resp.status_int)
        self.assertTrue(payload["legacy_api_token"]["configured"])
        self.assertTrue(payload["legacy_api_token"]["compatibility_enabled"])
        self.assertEqual("enabled", payload["legacy_api_token"]["state"])
        self.assertEqual(1, payload["api_keys"]["active"])

    def test_create_update_rotate_revoke_key_routes_work(self):
        create_resp = self.test_app.post_json(
            "/server/admin/api-keys/v1",
            {"name": "reader", "scopes": ["read"]},
            extra_environ=self._auth_headers(self.admin_secret)
        )
        created = json.loads(create_resp.text)
        key_id = created["key"]["id"]
        secret = created["secret"]

        self.assertEqual(201, create_resp.status_int)
        self.assertIn(secret, create_resp.text)

        update_resp = self.test_app.put_json(
            "/server/admin/api-keys/v1/{}".format(key_id),
            {"name": "reader-updated", "scopes": ["read", "write"]},
            extra_environ=self._auth_headers(self.admin_secret)
        )
        updated = json.loads(update_resp.text)
        self.assertEqual("reader-updated", updated["key"]["name"])
        self.assertEqual(["read", "write"], updated["key"]["scopes"])

        rotate_resp = self.test_app.post(
            "/server/admin/api-keys/v1/{}/rotate".format(key_id),
            extra_environ=self._auth_headers(self.admin_secret)
        )
        rotated = json.loads(rotate_resp.text)
        self.assertEqual(200, rotate_resp.status_int)
        self.assertNotEqual(secret, rotated["secret"])
        self.assertTrue(rotated["key"]["active"])

        revoke_resp = self.test_app.post(
            "/server/admin/api-keys/v1/{}/revoke".format(key_id),
            extra_environ=self._auth_headers(self.admin_secret)
        )
        revoked = json.loads(revoke_resp.text)
        self.assertEqual(200, revoke_resp.status_int)
        self.assertFalse(revoked["key"]["active"])

        repeat_revoke_resp = self.test_app.post(
            "/server/admin/api-keys/v1/{}/revoke".format(key_id),
            extra_environ=self._auth_headers(self.admin_secret),
            expect_errors=True
        )
        self.assertEqual(400, repeat_revoke_resp.status_int)
        self.assertIn("Cannot revoke a revoked API key", repeat_revoke_resp.text)

        update_revoked_resp = self.test_app.put_json(
            "/server/admin/api-keys/v1/{}".format(key_id),
            {"name": "reader-should-not-update"},
            extra_environ=self._auth_headers(self.admin_secret),
            expect_errors=True
        )
        self.assertEqual(400, update_revoked_resp.status_int)
        self.assertIn("Cannot update a revoked API key", update_revoked_resp.text)

        visible_keys_resp = self.test_app.get(
            "/server/admin/api-keys/v1",
            extra_environ=self._auth_headers(self.admin_secret)
        )
        visible_keys = json.loads(visible_keys_resp.text)
        self.assertEqual(200, visible_keys_resp.status_int)
        self.assertEqual(1, len(visible_keys["keys"]))
        self.assertEqual("admin", visible_keys["keys"][0]["name"])

        all_keys_resp = self.test_app.get(
            "/server/admin/api-keys/v1?include_revoked=1",
            extra_environ=self._auth_headers(self.admin_secret)
        )
        all_keys = json.loads(all_keys_resp.text)
        self.assertEqual(200, all_keys_resp.status_int)
        self.assertEqual(2, len(all_keys["keys"]))
        self.assertTrue(any(key["name"] == "admin" and key["active"] for key in all_keys["keys"]))
        self.assertTrue(any(key["name"] == "reader-updated" and not key["active"] for key in all_keys["keys"]))

        delete_resp = self.test_app.delete(
            "/server/admin/api-keys/v1/{}".format(key_id),
            extra_environ=self._auth_headers(self.admin_secret)
        )
        self.assertEqual(204, delete_resp.status_int)

        remaining_keys_resp = self.test_app.get(
            "/server/admin/api-keys/v1?include_revoked=1",
            extra_environ=self._auth_headers(self.admin_secret)
        )
        remaining_keys = json.loads(remaining_keys_resp.text)
        self.assertEqual(1, len(remaining_keys["keys"]))
        self.assertEqual("admin", remaining_keys["keys"][0]["name"])

        second_key_resp = self.test_app.post_json(
            "/server/admin/api-keys/v1",
            {"name": "writer", "scopes": ["write"]},
            extra_environ=self._auth_headers(self.admin_secret)
        )
        second_key = json.loads(second_key_resp.text)["key"]["id"]

        delete_active_resp = self.test_app.delete(
            "/server/admin/api-keys/v1/{}".format(second_key),
            extra_environ=self._auth_headers(self.admin_secret),
            expect_errors=True
        )
        self.assertEqual(400, delete_active_resp.status_int)
        self.assertIn("Cannot delete an active API key", delete_active_resp.text)

    def test_disable_and_clear_legacy_token_routes_update_state(self):
        disable_resp = self.test_app.post(
            "/server/admin/migration/v1/legacy-api-token/disable",
            extra_environ=self._auth_headers(self.admin_secret)
        )
        disabled = json.loads(disable_resp.text)
        self.assertFalse(disabled["legacy_api_token"]["compatibility_enabled"])

        clear_resp = self.test_app.post(
            "/server/admin/migration/v1/legacy-api-token/clear",
            extra_environ=self._auth_headers(self.admin_secret)
        )
        cleared = json.loads(clear_resp.text)
        self.assertFalse(cleared["legacy_api_token"]["configured"])
        self.assertFalse(cleared["legacy_api_token"]["compatibility_enabled"])
        self.assertEqual("", self.context.config.general.api_token)
