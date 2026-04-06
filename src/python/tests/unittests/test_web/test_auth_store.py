import os
import tempfile
import unittest
import json
import threading

from web.auth_store import ApiKeyStore


def _read_history_entries(file_path):
    history_path = os.path.splitext(file_path)[0] + ".history.jsonl"
    with open(history_path, "r", encoding="utf-8") as handle:
        return [
            json.loads(line)
            for line in handle
            if line.strip()
        ]


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

            history_entries = _read_history_entries(store_path)
            events = [entry["event"] for entry in history_entries]
            self.assertIn("store_loaded", events)
            self.assertIn("api_key_created", events)
            self.assertIn("api_key_updated", events)
            self.assertIn("api_key_rotated", events)
            self.assertIn("api_key_revoked", events)
            self.assertIn("api_key_deleted", events)

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

    def test_obsolete_token_marker_is_ignored_on_load(self):
        store = ApiKeyStore.from_str("""
        {
          "obsolete_token_marker": "false",
          "api_keys": []
        }
        """)

        self.assertEqual(0, len(store.list_api_keys()))
        self.assertEqual(0, store.active_admin_key_count)

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

    def test_browser_session_is_persisted_and_tracks_current_api_key_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "api-keys.json")
            store = ApiKeyStore(file_path=store_path)

            created = store.create_api_key("admin", ["admin", "read"])
            record = created["record"]
            session = store.create_browser_session_for_api_key(record.id)

            self.assertTrue(os.path.isfile(store_path))
            self.assertIsNotNone(store.find_ui_session_by_secret(session.secret))
            self.assertEqual(record.id, store.resolve_ui_session_api_key(session).id)
            self.assertEqual(12 * 60 * 60, session.cookie_max_age_seconds())

            updated = store.update_api_key(record.id, scopes=["admin", "read", "stream"])
            self.assertEqual(["admin", "read", "stream"], updated.scopes)
            self.assertEqual(["admin", "read", "stream"], store.resolve_ui_session_api_key(session).scopes)

            rotated = store.rotate_api_key(record.id)
            self.assertIsNone(store.resolve_ui_session_api_key(session))

            replacement_session = store.create_browser_session_for_api_key(record.id)
            self.assertEqual(record.id, store.resolve_ui_session_api_key(replacement_session).id)

            store.revoke_api_key(record.id)
            self.assertIsNone(store.resolve_ui_session_api_key(replacement_session))

    def test_create_ui_session_preserves_legacy_positional_argument_compatibility(self):
        store = ApiKeyStore()

        session = store.create_ui_session(["admin"], False, "api-key-id", "api-key-secret")

        self.assertFalse(session.bootstrap)
        self.assertFalse(session.remembered)
        self.assertEqual("api-key-id", session.api_key_id)
        self.assertEqual("api-key-secret", session.api_key_secret_hash)

    def test_remembered_ui_session_is_not_time_pruned_and_cleared_with_api_key_revoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "api-keys.json")
            store = ApiKeyStore(file_path=store_path)
            created = store.create_api_key("admin", ["admin", "read"])
            record = created["record"]

            bootstrap_session = store.create_ui_session(["bootstrap"], bootstrap=True)
            remembered_session = store.create_remembered_browser_session_for_api_key(record.id)
            self.assertEqual("", remembered_session.expires_at)
            self.assertGreater(
                remembered_session.cookie_max_age_seconds(),
                12 * 60 * 60,
            )

            fresh_reload = ApiKeyStore.from_file(store_path)
            durable_remembered = fresh_reload.find_ui_session_by_secret(remembered_session.secret)

            self.assertIsNotNone(durable_remembered)
            self.assertTrue(durable_remembered.remembered)
            self.assertEqual(record.id, fresh_reload.resolve_ui_session_api_key(durable_remembered).id)
            self.assertGreater(
                durable_remembered.cookie_max_age_seconds(),
                12 * 60 * 60,
            )

            store._ApiKeyStore__ui_sessions[bootstrap_session.secret].expires_at = "2000-01-01T00:00:00+00:00"
            store._ApiKeyStore__ui_sessions[remembered_session.secret].expires_at = "2000-01-01T00:00:00+00:00"
            store.save()

            expired_reload = ApiKeyStore.from_file(store_path)
            self.assertIsNone(expired_reload.find_ui_session_by_secret(bootstrap_session.secret))
            durable_after_timestamp_change = expired_reload.find_ui_session_by_secret(remembered_session.secret)
            self.assertIsNotNone(durable_after_timestamp_change)
            self.assertTrue(durable_after_timestamp_change.remembered)

            expired_reload.revoke_api_key(record.id)
            self.assertIsNone(expired_reload.find_ui_session_by_secret(remembered_session.secret))
            self.assertNotIn(remembered_session.secret, expired_reload._ApiKeyStore__ui_sessions)

            history_entries = _read_history_entries(store_path)
            remembered_creation = [
                entry for entry in history_entries
                if entry["event"] == "ui_session_created" and entry["reason"] == "remembered_browser_session_created"
            ]
            self.assertEqual(1, len(remembered_creation))
            discarded_entries = [
                entry for entry in history_entries
                if entry["event"] == "ui_sessions_discarded" and entry["reason"] == "api_key_revoked"
            ]
            self.assertEqual(1, len(discarded_entries))
            self.assertEqual(1, discarded_entries[0]["details"]["discarded_count"])
            self.assertEqual(1, discarded_entries[0]["details"]["remembered_count"])

    def test_delete_api_key_removes_remembered_browser_sessions_for_revoked_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApiKeyStore(file_path=os.path.join(temp_dir, "api-keys.json"))
            created = store.create_api_key("admin", ["admin", "read"])
            record = created["record"]
            remembered_session = store.create_remembered_browser_session_for_api_key(record.id)

            record.revoked_at = "2026-04-05T00:00:00+00:00"
            record.updated_at = "2026-04-05T00:00:00+00:00"

            deleted = store.delete_api_key(record.id)
            self.assertEqual(record.id, deleted.id)
            self.assertIsNone(store.find_ui_session_by_secret(remembered_session.secret))
            self.assertNotIn(remembered_session.secret, store._ApiKeyStore__ui_sessions)

    def test_initial_admin_claim_reopens_when_handover_version_changes(self):
        store = ApiKeyStore()

        self.assertTrue(store.can_claim_initial_admin(""))

        store.create_api_key("admin", ["admin"])

        self.assertFalse(store.can_claim_initial_admin(""))
        self.assertTrue(store.can_claim_initial_admin("2026.04.03"))

        store.claim_initial_admin("2026.04.03")

        self.assertFalse(store.can_claim_initial_admin("2026.04.03"))
        self.assertTrue(store.can_claim_initial_admin("2026.04.04"))

    def test_initial_admin_claim_is_atomic_across_concurrent_requests(self):
        store = ApiKeyStore()
        store.create_api_key("admin", ["admin"])
        store.claim_initial_admin("2026.04.03")

        gate = threading.Barrier(2)
        results = []

        def _claim():
            gate.wait()
            results.append(store.claim_initial_admin_if_available("2026.04.04"))

        first = threading.Thread(target=_claim)
        second = threading.Thread(target=_claim)
        first.start()
        second.start()
        first.join()
        second.join()

        self.assertEqual(1, results.count(True))
        self.assertEqual(1, results.count(False))
        self.assertFalse(store.can_claim_initial_admin("2026.04.04"))

    def test_version_1_payload_without_browser_handover_state_still_loads(self):
        payload = {
            "version": 1,
            "obsolete_token_marker": True,
            "api_keys": [],
        }

        store = ApiKeyStore.from_str(json.dumps(payload))

        self.assertEqual(0, store.active_admin_key_count)
        self.assertTrue(store.can_claim_initial_admin(""))
