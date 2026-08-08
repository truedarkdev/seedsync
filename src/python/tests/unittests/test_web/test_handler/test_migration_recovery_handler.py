import json
import unittest
from unittest.mock import MagicMock

from common import Config
from web.auth_store import ApiKeyStore
from web.handler.migration_recovery import MigrationRecoveryHandler
from web.web_app import WebApp
from webtest import TestApp


class TestMigrationRecoveryHandler(unittest.TestCase):
    def setUp(self) -> None:
        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = "/tmp"
        self.context.status = MagicMock()
        self.context.config = Config()
        self.auth_store = ApiKeyStore()
        created = self.auth_store.create_api_key("admin", ["admin"])
        self.secret = created["secret"]
        self.coordinator = MagicMock()
        self.restart = MagicMock()
        self.app = WebApp(self.context, MagicMock(), auth_store=self.auth_store)
        MigrationRecoveryHandler(self.coordinator, self.restart).add_routes(self.app)
        self.client = TestApp(self.app)

    def _headers(self):
        return {"HTTP_AUTHORIZATION": "Bearer {}".format(self.secret)}

    def test_status_exposes_only_public_receipt_bound_eligibility(self) -> None:
        self.coordinator.recovery_eligibility.return_value = {
            "eligible": True, "migration_id": "v086", "backup_id": "v086-a1",
            "confirmation": "RESTORE", "receipt_sha256": "secret-receipt",
            "backup_manifest_sha256": "secret-manifest",
        }

        response = self.client.get(
            "/server/admin/migration-recovery/v1/status", extra_environ=self._headers(),
        )
        payload = json.loads(response.text)
        self.assertTrue(payload["eligible"])
        self.assertNotIn("receipt_sha256", payload)
        self.assertNotIn("backup_manifest_sha256", payload)

    def test_restore_requires_admin_and_fixed_confirmation_attestation_shape(self) -> None:
        denied = self.client.post_json(
            "/server/admin/migration-recovery/v1/restore", {}, expect_errors=True,
        )
        self.assertEqual(401, denied.status_int)

        response = self.client.post_json(
            "/server/admin/migration-recovery/v1/restore",
            {"confirmation": "RESTORE", "other_instances_stopped": True},
            extra_environ=self._headers(),
        )
        self.assertEqual(202, response.status_int)
        self.coordinator.request_recovery_restore.assert_called_once_with(
            confirmation="RESTORE", other_instances_stopped=True,
        )
        self.restart.assert_called_once_with()

    def test_restore_rejects_backup_or_unknown_client_fields(self) -> None:
        response = self.client.post_json(
            "/server/admin/migration-recovery/v1/restore",
            {"confirmation": "RESTORE", "other_instances_stopped": True, "backup": "/tmp/anywhere"},
            extra_environ=self._headers(), expect_errors=True,
        )
        self.assertEqual(409, response.status_int)
        self.coordinator.request_recovery_restore.assert_not_called()

    def test_disabled_browser_auth_opens_recovery_status_but_keeps_restore_attestation(self) -> None:
        self.context.config.general.disable_browser_auth = True
        self.coordinator.recovery_eligibility.return_value = {
            "eligible": True,
            "migration_id": "v086",
            "backup_id": "v086-a1",
            "confirmation": "RESTORE",
        }

        status = self.client.get(
            "/server/admin/migration-recovery/v1/status",
            extra_environ={
                "HTTP_AUTHORIZATION": "Bearer invalid",
                "HTTP_COOKIE": "seedsync_ui_session=invalid-session",
            },
        )
        self.assertEqual(200, status.status_int)
        self.assertTrue(json.loads(status.text)["eligible"])

        cross_origin_restore = self.client.post_json(
            "/server/admin/migration-recovery/v1/restore",
            {"confirmation": "RESTORE", "other_instances_stopped": True},
            extra_environ={
                "HTTP_HOST": "localhost:8800",
                "HTTP_ORIGIN": "https://evil.example",
                "HTTP_REFERER": "https://evil.example/dashboard",
                "REMOTE_ADDR": "203.0.113.10",
            },
            expect_errors=True,
        )
        self.assertEqual(403, cross_origin_restore.status_int)
        self.coordinator.request_recovery_restore.assert_not_called()

        missing_confirmation = self.client.post_json(
            "/server/admin/migration-recovery/v1/restore",
            {},
            extra_environ={"HTTP_AUTHORIZATION": "Basic invalid"},
            expect_errors=True,
        )
        self.assertEqual(409, missing_confirmation.status_int)
        self.coordinator.request_recovery_restore.assert_not_called()

        restore = self.client.post_json(
            "/server/admin/migration-recovery/v1/restore",
            {"confirmation": "RESTORE", "other_instances_stopped": True},
            extra_environ={"HTTP_COOKIE": "seedsync_ui_session=invalid-session"},
        )
        self.assertEqual(202, restore.status_int)
        self.coordinator.request_recovery_restore.assert_called_once_with(
            confirmation="RESTORE", other_instances_stopped=True,
        )
        self.restart.assert_called_once_with()
