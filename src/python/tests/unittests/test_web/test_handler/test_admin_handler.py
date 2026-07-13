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


def _assert_security_headers(testcase, response):
    testcase.assertEqual("connect-src 'self' https://api.github.com", response.headers["Content-Security-Policy"])
    testcase.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
    testcase.assertEqual("DENY", response.headers["X-Frame-Options"])
    testcase.assertEqual("strict-origin-when-cross-origin", response.headers["Referrer-Policy"])


class TestAdminHandler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_admin_handler")
        self.store_path = os.path.join(self.temp_dir, "api-keys.json")

        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = self.temp_dir
        self.context.status = MagicMock()
        self.context.config = Config()

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

    @staticmethod
    def _trusted_remote_same_origin_headers(origin: str = "http://localhost:8800"):
        return {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "172.25.0.1",
            "HTTP_ORIGIN": origin,
            "HTTP_REFERER": "{}/dashboard".format(origin),
        }

    def test_legacy_token_is_rejected_for_admin_routes(self):
        resp = self.test_app.get(
            "/server/admin/api-keys/v1",
            extra_environ=self._auth_headers(LEGACY_TEST_API_TOKEN),
            expect_errors=True
        )

        self.assertEqual(401, resp.status_int)
        self.assertIn("Invalid API token", str(resp.html))

    def test_first_admin_bootstrap_requires_trusted_local_browser_and_same_origin_signal(self):
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
                "HTTP_HOST": "seed.example:8800",
                "REMOTE_ADDR": "172.25.0.1",
                "HTTP_ORIGIN": "https://seed.example:8800",
                "HTTP_REFERER": "https://seed.example:8800/bootstrap",
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
        _assert_security_headers(self, rejected_cross_origin)
        self.assertEqual(0, empty_store.active_admin_key_count)

        allowed = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "first-admin"},
            extra_environ=self._same_origin_headers()
        )
        allowed_payload = json.loads(allowed.text)
        self.assertEqual(201, allowed.status_int)
        _assert_security_headers(self, allowed)
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

    def test_prebootstrap_browser_can_bootstrap_first_admin_and_unlock_admin_routes(self):
        empty_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "bootstrap-api-keys-limited.json"))
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)

        with open(os.path.join(self.temp_dir, "index.html"), "w") as html_file:
            html_file.write("<html></html>")

        web_app.add_default_routes()
        test_app = TestApp(web_app)

        issued = test_app.get("/", extra_environ=self._same_origin_headers())
        limited_cookie = issued.headers.get("Set-Cookie", "")
        self.assertEqual("", limited_cookie)

        stale_bootstrap_session = empty_store.create_ui_session(["write"])
        stale_bootstrap_cookie = "seedsync_ui_session={}".format(stale_bootstrap_session.secret)

        rejected_remote = test_app.get(
            "/server/admin/api-keys/v1",
            extra_environ={"HTTP_HOST": "localhost:8800", "REMOTE_ADDR": "203.0.113.10"},
            expect_errors=True
        )
        self.assertEqual(401, rejected_remote.status_int)

        bootstrap_page = test_app.get(
            "/bootstrap",
            extra_environ=self._same_origin_headers(),
        )
        self.assertEqual(200, bootstrap_page.status_int)
        self.assertEqual("", bootstrap_page.headers.get("Set-Cookie", ""))
        self.assertIn("/server/admin/bootstrap/v1/first-api-key", bootstrap_page.text)
        self.assertEqual(0, empty_store.active_admin_key_count)
        self.assertTrue(empty_store.get_browser_handover_state(self.context.config)["open"])

        allowed = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "first-admin"},
            extra_environ={**self._same_origin_headers(), "HTTP_COOKIE": stale_bootstrap_cookie}
        )
        allowed_payload = json.loads(allowed.text)
        allowed_cookie = allowed.headers.get("Set-Cookie", "")
        upgraded_cookie = allowed.headers.get("Set-Cookie", "").split(";", 1)[0]
        self.assertEqual(201, allowed.status_int)
        self.assertEqual(["admin"], allowed_payload["key"]["scopes"])
        self.assertIn("secret", allowed_payload)
        self.assertIn("seedsync_ui_session=", upgraded_cookie)
        self.assertIn("Max-Age=315360000", allowed_cookie)
        self.assertNotEqual("", upgraded_cookie)
        self.assertNotEqual(limited_cookie, upgraded_cookie)
        self.assertIn("browser_handover", allowed_payload)
        self.assertFalse(allowed_payload["browser_handover"]["open"])

        authorized_admin_list = test_app.get(
            "/server/admin/api-keys/v1",
            extra_environ={**self._same_origin_headers(), "HTTP_COOKIE": upgraded_cookie}
        )
        authorized_payload = json.loads(authorized_admin_list.text)
        self.assertEqual(200, authorized_admin_list.status_int)
        self.assertEqual(1, len(authorized_payload["keys"]))

    def test_trusted_bootstrap_remote_can_read_migration_state_and_bootstrap_first_admin(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        empty_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "bootstrap-api-keys-trusted-remote.json"))
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)

        with open(os.path.join(self.temp_dir, "index.html"), "w") as html_file:
            html_file.write("<html></html>")

        web_app.add_default_routes()
        test_app = TestApp(web_app)

        trusted_browser_headers = self._trusted_remote_same_origin_headers()
        bootstrap_page = test_app.get(
            "/bootstrap",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "172.25.0.1",
            },
        )
        self.assertEqual(200, bootstrap_page.status_int)
        self.assertEqual("", bootstrap_page.headers.get("Set-Cookie", ""))
        self.assertIn("/server/admin/bootstrap/v1/first-api-key", bootstrap_page.text)
        self.assertEqual(0, empty_store.active_admin_key_count)
        self.assertTrue(empty_store.get_browser_handover_state(self.context.config)["open"])

        bootstrap_response = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "trusted-remote-admin"},
            extra_environ=trusted_browser_headers,
        )
        bootstrap_payload = json.loads(bootstrap_response.text)
        admin_cookie = bootstrap_response.headers.get("Set-Cookie", "").split(";", 1)[0]
        cookie_secret = admin_cookie.split("=", 1)[1]
        bootstrap_session = empty_store.find_ui_session_by_secret(cookie_secret)
        self.assertEqual(201, bootstrap_response.status_int)
        self.assertEqual(["admin"], bootstrap_payload["key"]["scopes"])
        self.assertIn("secret", bootstrap_payload)
        self.assertIn("browser_handover", bootstrap_payload)
        self.assertFalse(bootstrap_payload["browser_handover"]["open"])
        self.assertIn("seedsync_ui_session=", admin_cookie)
        self.assertIn("Max-Age=315360000", bootstrap_response.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", bootstrap_response.headers.get("Set-Cookie", ""))
        self.assertIsNotNone(bootstrap_session)
        self.assertTrue(bootstrap_session.remembered)
        self.assertGreater(bootstrap_session.cookie_max_age_seconds(), 12 * 60 * 60)
        self.assertEqual(1, empty_store.active_admin_key_count)

    def test_remember_browser_session_route_issues_cookie_for_existing_api_key(self):
        trusted_headers = self._same_origin_headers()

        response = self.test_app.post_json(
            "/server/browser/v1/remember",
            {"secret": self.admin_secret},
            extra_environ=trusted_headers,
        )
        payload = json.loads(response.text)

        self.assertEqual(201, response.status_int)
        self.assertEqual(self.admin_key_id, payload["key"]["id"])
        self.assertEqual(["admin"], payload["key"]["scopes"])
        self.assertIn("browser_handover", payload)
        self.assertFalse(payload["browser_handover"]["open"])
        self.assertTrue(payload["remembered"])
        self.assertGreater(payload["cookie_max_age_seconds"], 12 * 60 * 60)
        self.assertIn("seedsync_ui_session=", response.headers.get("Set-Cookie", ""))
        self.assertIn("Max-Age=315360000", response.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", response.headers.get("Set-Cookie", ""))

        cookie_secret = response.headers.get("Set-Cookie", "").split(";", 1)[0].split("=", 1)[1]
        remembered_session = self.auth_store.find_ui_session_by_secret(cookie_secret)
        self.assertIsNotNone(remembered_session)
        self.assertTrue(remembered_session.remembered)

    def test_revoke_api_key_clears_remembered_browser_sessions(self):
        trusted_headers = self._same_origin_headers()

        remember_response = self.test_app.post_json(
            "/server/browser/v1/remember",
            {"secret": self.admin_secret},
            extra_environ=trusted_headers,
        )
        session_cookie = remember_response.headers.get("Set-Cookie", "").split(";", 1)[0]
        cookie_secret = session_cookie.split("=", 1)[1]
        admin_cookie_headers = {
            **self._same_origin_headers(),
            "HTTP_COOKIE": session_cookie,
        }

        revoke_response = self.test_app.post(
            "/server/admin/api-keys/v1/{}/revoke".format(self.admin_key_id),
            extra_environ=admin_cookie_headers,
        )

        self.assertEqual(200, revoke_response.status_int)
        self.assertNotIn(cookie_secret, self.auth_store._ApiKeyStore__ui_sessions)
        self.assertIsNone(self.auth_store.find_ui_session_by_secret(cookie_secret))

    def test_bootstrap_proof_exchange_rejects_non_same_origin_requests(self):
        empty_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "bootstrap-api-keys-reject-non-origin.json"))
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)
        test_app = TestApp(web_app)

        proof = empty_store.ensure_bootstrap_proof()
        untrusted_headers = {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "203.0.113.10",
        }

        rejected = test_app.post_json(
            "/server/admin/bootstrap/v1/exchange",
            {"proof": proof.secret},
            extra_environ=untrusted_headers,
            expect_errors=True,
        )

        self.assertEqual(401, rejected.status_int)
        self.assertIn("Missing API token", rejected.text)

    def test_bootstrap_proof_exchange_is_one_time_and_sets_http_only_cookie(self):
        empty_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "bootstrap-api-keys-trusted-remote-once.json"))
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)
        with open(os.path.join(self.temp_dir, "index.html"), "w") as html_file:
            html_file.write("<html></html>")
        web_app.add_default_routes()
        test_app = TestApp(web_app)

        proof = empty_store.ensure_bootstrap_proof()
        exchange = empty_store.ensure_bootstrap_exchange()
        same_origin_headers = {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_ORIGIN": "http://localhost:8800",
            "HTTP_REFERER": "http://localhost:8800/bootstrap",
            "HTTP_COOKIE": "seedsync_bootstrap_exchange={}".format(exchange.secret),
        }
        bootstrap_page = test_app.get(
            "/bootstrap",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
            },
        )
        self.assertEqual("", bootstrap_page.headers.get("Set-Cookie", ""))
        self.assertIn("/server/admin/bootstrap/v1/first-api-key", bootstrap_page.text)

        first_exchange = test_app.post_json(
            "/server/admin/bootstrap/v1/exchange",
            {"proof": proof.secret},
            extra_environ=same_origin_headers,
        )
        replay_exchange = test_app.post_json(
            "/server/admin/bootstrap/v1/exchange",
            {"proof": proof.secret},
            extra_environ=same_origin_headers,
            expect_errors=True,
        )

        self.assertEqual(200, first_exchange.status_int)
        self.assertEqual(401, replay_exchange.status_int)
        self.assertIn("Missing API token", replay_exchange.text)
        self.assertIn("seedsync_ui_session=", first_exchange.headers.get("Set-Cookie", ""))
        self.assertIn("Max-Age=43200", first_exchange.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", first_exchange.headers.get("Set-Cookie", ""))
        self.assertNotIn("session_secret", json.loads(first_exchange.text))

    def test_bootstrap_session_can_unlock_first_admin_without_trusted_remote_headers(self):
        empty_store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "bootstrap-api-keys-untrusted-session.json"))
        web_app = WebApp(self.context, MagicMock(), auth_store=empty_store)
        AdminHandler(self.context.config, empty_store).add_routes(web_app)
        with open(os.path.join(self.temp_dir, "index.html"), "w") as html_file:
            html_file.write("<html></html>")
        web_app.add_default_routes()
        test_app = TestApp(web_app)

        bootstrap_headers = {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_ORIGIN": "http://localhost:8800",
            "HTTP_REFERER": "http://localhost:8800/bootstrap",
        }
        bootstrap_page = test_app.get(
            "/bootstrap",
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "REMOTE_ADDR": "127.0.0.1",
            },
        )
        self.assertEqual("", bootstrap_page.headers.get("Set-Cookie", ""))
        self.assertIn("/server/admin/bootstrap/v1/first-api-key", bootstrap_page.text)

        bootstrap_response = test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "untrusted-bootstrap-admin"},
            extra_environ=bootstrap_headers,
        )
        bootstrap_payload = json.loads(bootstrap_response.text)
        bootstrap_cookie = bootstrap_response.headers.get("Set-Cookie", "").split(";", 1)[0]
        bootstrap_cookie_secret = bootstrap_cookie.split("=", 1)[1]
        bootstrap_session = empty_store.find_ui_session_by_secret(bootstrap_cookie_secret)

        self.assertEqual(201, bootstrap_response.status_int)
        self.assertEqual(["admin"], bootstrap_payload["key"]["scopes"])
        self.assertIn("secret", bootstrap_payload)
        self.assertIn("seedsync_ui_session=", bootstrap_cookie)
        self.assertIn("Max-Age=315360000", bootstrap_response.headers.get("Set-Cookie", ""))
        self.assertIsNotNone(bootstrap_session)
        self.assertTrue(bootstrap_session.remembered)
        self.assertGreater(bootstrap_session.cookie_max_age_seconds(), 12 * 60 * 60)
        self.assertEqual(1, empty_store.active_admin_key_count)

    def test_migration_state_reports_non_admin_keys_without_exiting_bootstrap_mode(self):
        self.context.config.general.trusted_browser_bootstrap_remote_addrs = "172.25.0.1/32"
        store = ApiKeyStore(file_path=os.path.join(self.temp_dir, "bootstrap-api-keys-non-admin.json"))
        store.create_api_key("reader-writer", ["read", "write", "stream"])
        web_app = WebApp(self.context, MagicMock(), auth_store=store)
        AdminHandler(self.context.config, store).add_routes(web_app)
        web_app.add_default_routes()
        test_app = TestApp(web_app)

        trusted_browser_headers = {
            "HTTP_HOST": "localhost:8800",
            "REMOTE_ADDR": "172.25.0.1",
            "HTTP_ORIGIN": "http://localhost:8800",
            "HTTP_REFERER": "http://localhost:8800/settings",
        }

        bootstrap_page = test_app.get(
            "/bootstrap",
            extra_environ=trusted_browser_headers,
        )
        self.assertEqual(200, bootstrap_page.status_int)
        self.assertEqual(0, store.active_admin_key_count)
        self.assertTrue(store.get_browser_handover_state(self.context.config)["open"])

    def test_first_admin_bootstrap_is_not_available_after_admin_exists(self):
        resp = self.test_app.post_json(
            "/server/admin/bootstrap/v1/first-api-key",
            {"name": "another-admin"},
            extra_environ={"HTTP_HOST": "localhost:8800", "REMOTE_ADDR": "127.0.0.1"},
            expect_errors=True
        )

        self.assertEqual(401, resp.status_int)

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

    def test_create_key_rejects_non_string_scope_without_creating_key(self):
        create_api_key = self.auth_store.create_api_key
        self.auth_store.create_api_key = MagicMock(wraps=create_api_key)

        response = self.test_app.post_json(
            "/server/admin/api-keys/v1",
            {"name": "invalid", "scopes": ["read", 7]},
            extra_environ=self._auth_headers(self.admin_secret),
            expect_errors=True,
        )

        self.assertEqual(400, response.status_int)
        self.assertIn("API key scopes must be a list of strings", response.text)
        self.auth_store.create_api_key.assert_not_called()

    def test_legacy_migration_routes_are_removed(self):
        get_resp = self.test_app.get("/server/admin/migration/v1", expect_errors=True)
        self.assertEqual(404, get_resp.status_int)

        disable_resp = self.test_app.post("/server/admin/migration/v1/legacy-api-token/disable", expect_errors=True)
        self.assertEqual(404, disable_resp.status_int)

        clear_resp = self.test_app.post("/server/admin/migration/v1/legacy-api-token/clear", expect_errors=True)
        self.assertEqual(404, clear_resp.status_int)
