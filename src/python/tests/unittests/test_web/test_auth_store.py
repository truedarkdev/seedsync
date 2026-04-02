import os
import tempfile
import unittest
import json

from web.auth_store import ApiKeyStore
from common import PersistError


class TestApiKeyStore(unittest.TestCase):
    def test_create_update_revoke_and_rotate_persist_without_raw_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "api-keys.json")
            store = ApiKeyStore(file_path=store_path)

            created = store.create_api_key("admin", ["admin", "read"])
            secret = created["secret"]
            record = created["record"]

            self.assertTrue(os.path.isfile(store_path))
            with open(store_path, "r", encoding="utf-8") as handle:
                persisted = handle.read()
            self.assertNotIn(secret, persisted)

            self.assertIsNotNone(store.find_api_key_by_secret(secret))
            self.assertEqual(record.id, store.find_api_key_by_secret(secret).id)

            updated = store.update_api_key(record.id, name="admin-updated", scopes=["admin"])
            self.assertEqual("admin-updated", updated.name)
            self.assertEqual(["admin"], updated.scopes)

            rotated = store.rotate_api_key(record.id)
            new_secret = rotated["secret"]
            self.assertIsNone(store.find_api_key_by_secret(secret))
            self.assertIsNotNone(store.find_api_key_by_secret(new_secret))

            revoked = store.revoke_api_key(record.id)
            self.assertIsNotNone(revoked.revoked_at)
            with self.assertRaises(ValueError):
                store.revoke_api_key(record.id)
            with self.assertRaises(ValueError):
                store.rotate_api_key(record.id)
            with self.assertRaises(ValueError):
                store.update_api_key(record.id, name="revoked-admin")

            self.assertEqual(0, len(store.list_api_keys()))
            self.assertEqual(1, len(store.list_api_keys(include_revoked=True)))
            self.assertFalse(store.list_api_keys(include_revoked=True)[0]["active"])

            deleted = store.delete_api_key(record.id)
            self.assertEqual(record.id, deleted.id)

            reloaded = ApiKeyStore.from_file(store_path)
            self.assertEqual(0, len(reloaded.list_api_keys()))
            self.assertEqual(0, len(reloaded.list_api_keys(include_revoked=True)))
            self.assertTrue(reloaded.legacy_api_token_compatibility_enabled)

    def test_delete_api_key_rejects_active_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApiKeyStore(file_path=os.path.join(temp_dir, "api-keys.json"))

            created = store.create_api_key("reader", ["read"])
            with self.assertRaises(ValueError):
                store.delete_api_key(created["record"].id)

            store.revoke_api_key(created["record"].id)
            deleted = store.delete_api_key(created["record"].id)
            self.assertEqual(created["record"].id, deleted.id)
            self.assertIsNone(store.get_api_key(created["record"].id))

    def test_legacy_compatibility_flag_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "api-keys.json")
            store = ApiKeyStore(file_path=store_path)
            store.set_legacy_api_token_compatibility_enabled(False)

            reloaded = ApiKeyStore.from_file(store_path)
            self.assertFalse(reloaded.legacy_api_token_compatibility_enabled)

    def test_legacy_compatibility_flag_loader_requires_boolean(self):
        with self.assertRaises(PersistError):
            ApiKeyStore.from_str("""
            {
              "legacy_api_token_compatibility_enabled": "false",
              "api_keys": []
            }
            """)

    def test_bootstrap_proof_is_created_published_consumed_and_not_replayed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proof_path = os.path.join(temp_dir, "bootstrap", "browser-bootstrap.json")
            store = ApiKeyStore(file_path=os.path.join(temp_dir, "api-keys.json"))
            store.bind_bootstrap_proof_path(proof_path)

            proof = store.ensure_bootstrap_proof()

            self.assertIsNotNone(proof)
            self.assertTrue(os.path.isfile(proof_path))
            self.assertTrue(store.peek_bootstrap_proof(proof.secret))

            with open(proof_path, "r", encoding="utf-8") as handle:
                artifact = json.load(handle)
            self.assertEqual(proof.secret, artifact["proof"])

            self.assertTrue(store.consume_bootstrap_proof(proof.secret))
            self.assertFalse(store.peek_bootstrap_proof(proof.secret))
            self.assertFalse(store.consume_bootstrap_proof(proof.secret))
            self.assertFalse(os.path.exists(proof_path))

    def test_bootstrap_exchange_is_created_consumed_and_cleared_by_admin_creation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApiKeyStore(file_path=os.path.join(temp_dir, "api-keys.json"))

            exchange = store.ensure_bootstrap_exchange()

            self.assertIsNotNone(exchange)
            self.assertTrue(store.peek_bootstrap_exchange(exchange.secret))
            self.assertTrue(store.consume_bootstrap_exchange(exchange.secret))
            self.assertFalse(store.peek_bootstrap_exchange(exchange.secret))

            recreated = store.ensure_bootstrap_exchange()
            self.assertIsNotNone(recreated)
            self.assertNotEqual(exchange.secret, recreated.secret)

            store.create_api_key("admin", ["admin"])

            self.assertIsNone(store.ensure_bootstrap_exchange())
            self.assertFalse(store.peek_bootstrap_exchange(recreated.secret))

    def test_admin_key_creation_clears_bootstrap_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proof_path = os.path.join(temp_dir, "bootstrap", "browser-bootstrap.json")
            store = ApiKeyStore(file_path=os.path.join(temp_dir, "api-keys.json"))
            store.bind_bootstrap_proof_path(proof_path)

            proof = store.ensure_bootstrap_proof()
            self.assertIsNotNone(proof)
            self.assertTrue(os.path.isfile(proof_path))

            store.create_api_key("admin", ["admin"])

            self.assertFalse(store.peek_bootstrap_proof(proof.secret))
            self.assertFalse(os.path.exists(proof_path))
