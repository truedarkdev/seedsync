import os
import tempfile
import unittest

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
                store.rotate_api_key(record.id)

            reloaded = ApiKeyStore.from_file(store_path)
            self.assertEqual(1, len(reloaded.list_api_keys()))
            self.assertFalse(reloaded.list_api_keys()[0]["active"])
            self.assertTrue(reloaded.legacy_api_token_compatibility_enabled)

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
