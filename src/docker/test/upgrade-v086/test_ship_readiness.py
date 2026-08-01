#!/usr/bin/env python3
"""Fast dependency-free tests for ship-readiness artifact logic."""
import importlib.util
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import uuid
from unittest import mock
import zipfile
import zlib


MODULE_PATH = Path(__file__).with_name("ship_readiness.py")
BROWSER_PATH = Path(__file__).with_name("ship_readiness_browser.mjs")
LAUNCHER_PATH = Path(__file__).with_name("ship_readiness.sh")
LAB_PATH = Path(__file__).with_name("lab.sh")
REPO_ROOT = LAB_PATH.parents[4]
SPEC = importlib.util.spec_from_file_location("ship_readiness", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load ship-readiness helper")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class ShipReadinessTests(unittest.TestCase):
    def test_postvalidates_exact_archive_without_extracting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.txt"
            source.write_text("sample", encoding="utf-8")
            archive = root / "config.tar"
            with tarfile.open(archive, "w") as stream:
                stream.add(source, arcname="sample.txt")
            output = root / "archive.json"
            HARNESS.validate_archive(archive, output)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["postvalidated_without_extraction"])
            self.assertEqual(["sample.txt"], [item["path"] for item in payload["members"]])

            unsafe = root / "unsafe.tar"
            info = tarfile.TarInfo("../escape")
            info.size = 0
            with tarfile.open(unsafe, "w") as stream:
                stream.addfile(info)
            with self.assertRaisesRegex(SystemExit, "unsafe path"):
                HARNESS.validate_archive(unsafe, root / "unsafe.json")

    def test_download_archive_matches_fixture_without_extraction_and_fails_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "fixture.json"
            manifest.write_text(json.dumps({"cases": [{
                "id": "transient-manual",
                "remote": {"archive": {"extracted/payload.bin": {"generated_bytes": 32}}},
            }]}), encoding="utf-8")
            payload = (b"seedsync-v086-transient-" * 2)[:32]
            archive = root / "transient-manual.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as stream:
                stream.writestr("extracted/payload.bin", payload)
            output = root / "archive.json"
            HARNESS.verify_download_archive(archive, manifest, "transient-manual", output)
            passed = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(passed["exact_fixture_match"])
            self.assertTrue(passed["validated_without_extraction"])
            self.assertEqual("passed", passed["status"])
            self.assertFalse((root / "extracted").exists())

            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w", compression=zipfile.ZIP_STORED) as stream:
                stream.writestr("../payload.bin", payload)
            failure = root / "unsafe.json"
            with self.assertRaisesRegex(ValueError, "download archive verification failed"):
                HARNESS.verify_download_archive(unsafe, manifest, "transient-manual", failure)
            failed = json.loads(failure.read_text(encoding="utf-8"))
            self.assertEqual({"schema", "archive", "case_id", "status", "failure", "validated_without_extraction"}, set(failed))
            self.assertEqual("failed", failed["status"])
            self.assertNotIn("payload", json.dumps(failed))

    def test_download_archive_rejects_duplicate_special_and_oversized_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "fixture.json"
            manifest.write_text(json.dumps({"cases": [{
                "id": "archive-contract", "remote": {"archive": {"payload.bin": "expected"}},
            }]}), encoding="utf-8")

            def symlink_info(name):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                return info

            cases = {
                "duplicate": lambda stream: (stream.writestr("payload.bin", b"expected"), stream.writestr("payload.bin", b"expected")),
                "oversized": lambda stream: stream.writestr("payload.bin", b"expected-plus"),
                "special": lambda stream: stream.writestr(symlink_info("payload.bin"), b"target"),
            }
            for label, write in cases.items():
                with self.subTest(label=label):
                    archive = root / f"{label}.zip"
                    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as stream:
                        write(stream)
                    output = root / f"{label}.json"
                    with self.assertRaisesRegex(ValueError, "download archive verification failed"):
                        HARNESS.verify_download_archive(archive, manifest, "archive-contract", output)
                    self.assertEqual("failed", json.loads(output.read_text(encoding="utf-8"))["status"])

            self.assertIn("member.flag_bits & 0x1", MODULE_PATH.read_text(encoding="utf-8"))

    def test_redacts_secret_like_settings(self):
        self.assertEqual("api_key=<redacted>", HARNESS.redact("api_key = synthetic-secret"))

    def test_redacts_json_query_uri_and_header_credentials(self):
        cases = {
            '{"password":"json-secret"}': "json-secret",
            'https://example.invalid/status?access_token=query-secret': "query-secret",
            'sftp://seed:uri-secret@example.invalid/path': "uri-secret",
            'Cookie: session=cookie-secret': "cookie-secret",
            'X-Api-Key: header-secret': "header-secret",
            '{"Authorization":"header-json-secret"}': "header-json-secret",
            'known lab password is remotepass': "remotepass",
        }
        for source, secret in cases.items():
            with self.subTest(source=source):
                self.assertNotIn(secret, HARNESS.redact(source))
                self.assertIn("<redacted>", HARNESS.redact(source))
        self.assertEqual(
            {"url": "https://example.invalid/?token=<redacted>"},
            json.loads(HARNESS.redact('{"url":"https://example.invalid/?token=query-secret"}')),
        )

    def test_inventory_comparison_includes_content_and_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "sample"
            file.write_text("one", encoding="utf-8")
            before = HARNESS.inventory(root)
            file.write_text("two", encoding="utf-8")
            self.assertEqual(["sample"], HARNESS.compare(before, HARNESS.inventory(root)))

    def test_protected_archive_binds_exactly_to_inventory_without_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"
            config.mkdir()
            nested = config / "nested"
            nested.mkdir()
            nested.chmod(0o700)
            (nested / "sample.txt").write_text("original", encoding="utf-8")
            (nested / "sample.txt").chmod(0o600)
            inventory = HARNESS.inventory(config)
            archive = root / "exact.tar"
            with tarfile.open(archive, "w") as stream:
                stream.add(config, arcname=".")
            binding = root / "binding.json"
            HARNESS.bind_archive_inventory(archive, inventory, binding)
            self.assertTrue(json.loads(binding.read_text(encoding="utf-8"))["exact_inventory_match"])

            omitted = root / "omitted.tar"
            with tarfile.open(omitted, "w"):
                pass
            with self.assertRaisesRegex(ValueError, "exactly match inventory"):
                HARNESS.bind_archive_inventory(omitted, inventory, root / "omitted.json")

            extra = root / "extra.tar"
            extra_file = root / "extra.txt"
            extra_file.write_text("extra", encoding="utf-8")
            with tarfile.open(extra, "w") as stream:
                stream.add(config, arcname=".")
                stream.add(extra_file, arcname="extra.txt")
            with self.assertRaisesRegex(ValueError, "exactly match inventory"):
                HARNESS.bind_archive_inventory(extra, inventory, root / "extra.json")

            altered = root / "altered.tar"
            altered_file = root / "altered.txt"
            altered_file.write_text("altered", encoding="utf-8")
            with tarfile.open(altered, "w") as stream:
                stream.add(config / "nested", arcname="nested", recursive=False)
                stream.add(altered_file, arcname="nested/sample.txt")
            with self.assertRaisesRegex(ValueError, "exactly match inventory"):
                HARNESS.bind_archive_inventory(altered, inventory, root / "altered.json")

            mode_only = root / "mode-only.tar"
            mode_file = root / "mode-only.txt"
            mode_file.write_text("original", encoding="utf-8")
            mode_file.chmod(0o644)
            with tarfile.open(mode_only, "w") as stream:
                stream.add(config / "nested", arcname="nested", recursive=False)
                stream.add(mode_file, arcname="nested/sample.txt")
            with self.assertRaisesRegex(ValueError, "exactly match inventory"):
                HARNESS.bind_archive_inventory(mode_only, inventory, root / "mode-only.json")

            manifest = {"sha256": "0" * 64, "inventory_sha256": json.loads(binding.read_text(encoding="utf-8"))["inventory_sha256"]}
            with self.assertRaisesRegex(ValueError, "digest differs"):
                HARNESS.verify_protected_archive(archive, inventory, manifest, root / "verify.json")

    def test_matrix_rejects_pending_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            matrix = Path(temporary) / "matrix.json"
            HARNESS.json_dump(matrix, HARNESS.matrix_payload("fixture"))
            with self.assertRaises(SystemExit):
                HARNESS.verify_matrix(matrix)
            payload = json.loads(matrix.read_text(encoding="utf-8"))
            self.assertEqual("before", payload["rows"][0]["phase"])

    def test_matrix_rejects_not_exercised_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            matrix = Path(temporary) / "matrix.json"
            payload = HARNESS.matrix_payload("fixture")
            for row in payload["rows"]:
                row["status"] = "passed"
                row["artifacts"] = ["evidence.json"]
            payload["rows"][3]["status"] = "not-exercised"
            HARNESS.json_dump(matrix, payload)
            with self.assertRaises(SystemExit):
                HARNESS.verify_matrix(matrix)

    def test_seed_keeps_remote_secret_out_of_its_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = root / "settings.cfg"
            settings.write_text("[General]\ndebug = true\n[Lftp]\nremote_password = synthetic\n", encoding="utf-8")
            output = root / "seed.json"
            HARNESS.seed_v086_settings(settings, output)
            self.assertIn("num_max_parallel_downloads = 2", settings.read_text(encoding="utf-8"))
            self.assertNotIn("synthetic", output.read_text(encoding="utf-8"))

    def test_migration_failure_file_bundle_redacts_and_marks_presence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "migration-state.json").write_text('{"csrf_token":"synthetic-secret","state":"failed"}', encoding="utf-8")
            (root / ".seedsync.runtime.lock").write_text("token=synthetic-secret", encoding="utf-8")
            (root / "migration-backups").mkdir()
            bundle = HARNESS.migration_failure_files(root)
            self.assertTrue(bundle["files"]["migration-state.json"]["present"])
            self.assertTrue(bundle["files"]["migration-backups"]["present"])
            rendered = json.dumps(bundle)
            self.assertNotIn("synthetic-secret", rendered)
            self.assertIn("<redacted>", rendered)

    def test_failure_artifact_omits_synthetic_csrf_from_exception_text(self):
        synthetic_token = "synthetic-csrf-token-value"
        response = {"action": {"csrf_token": synthetic_token}, "state": "required"}
        error = AssertionError(response)
        payload = {
            "failure": HARNESS.failure_summary(error),
            "timeline": [{"event": "failure", "error": HARNESS.failure_summary(error)}],
            "response_shape": HARNESS.response_shape(response),
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "migration-status.json"
            HARNESS.json_dump(output, payload)
            rendered = output.read_text(encoding="utf-8")
        self.assertNotIn(synthetic_token, rendered)
        self.assertNotIn(str(response), rendered)
        self.assertEqual({"type": "AssertionError", "message_present": True}, payload["failure"])
        self.assertEqual({"type": "object", "keys": ["action", "state"]}, payload["response_shape"])

    def test_all_retained_migration_status_artifacts_exclude_raw_csrf(self):
        token = "synthetic-csrf-token-value"
        raw_status = {
            "mode": "migration_required", "state": "required",
            "migration_id": "original-v0.8.6-to-current-v1",
            "source_schema": "original-v0.8.6", "target_schema": "current-v1",
            "features": [{"key": "path-pairs", "title": "Synthetic title"}],
            "retryable": False,
            "capabilities": {"apply": True, "retry": False, "restore": False},
            "backup": {"status": "created_before_apply", "complete_restore_ready": False},
            "operation": {"status": "idle"},
            "action": {"csrf_token": token, "confirmation": "MIGRATE original-v0.8.6-to-current-v1"},
        }
        evidence = HARNESS.migration_status_evidence(raw_status)
        self.assertNotIn("action", evidence)
        self.assertEqual("idle", evidence["operation"]["status"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("migration-required.json", "migration-status.json", "migration-failure-http.json"):
                HARNESS.json_dump(root / name, evidence)
            rendered = "\n".join(path.read_text(encoding="utf-8") for path in root.iterdir())
        self.assertNotIn(token, rendered)
        self.assertNotIn("MIGRATE original-v0.8.6-to-current-v1", rendered)

    def test_preclaim_auth_challenge_requires_the_known_unauthenticated_contract(self):
        evidence = HARNESS.preclaim_auth_challenge_evidence(
            401,
            {"content-type": "text/html; charset=UTF-8"},
            "<html><h1>Error: 401 Unauthorized</h1><pre>Missing API token</pre></html>",
        )
        self.assertEqual(
            {"schema": 1, "http_status": 401, "auth_state": "api_key_required", "content_type": "text/html"},
            evidence,
        )

    def test_preclaim_auth_challenge_rejects_transport_failure_5xx_and_wrong_401_content(self):
        expected_headers = {"content-type": "text/html; charset=UTF-8"}
        cases = (
            (None, expected_headers, "<h1>Error: 401 Unauthorized</h1>Missing API token"),
            (503, expected_headers, "<h1>Error: 503 Service Unavailable</h1>"),
            (401, expected_headers, "<h1>Error: 401 Unauthorized</h1>Unknown request"),
            (401, {"content-type": "application/json"}, "<h1>Error: 401 Unauthorized</h1>Missing API token"),
            (401, {"content-type": "text/html-unexpected"}, "<h1>Error: 401 Unauthorized</h1>Missing API token"),
        )
        for status, headers, body in cases:
            with self.subTest(status=status, headers=headers):
                with self.assertRaises(ValueError):
                    HARNESS.preclaim_auth_challenge_evidence(status, headers, body)

    def test_assert_migration_accepts_empty_initialized_key_store_and_rejects_real_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pair = {"id": "pair", "name": "Default", "enabled": True, "auto_queue": True}
            (root / "path_pairs.json").write_text(json.dumps({"path_pairs": [pair]}), encoding="utf-8")
            backup = root / "migration-backups" / "backup"
            data = backup / "data"
            data.mkdir(parents=True)
            payload = data / "settings.cfg"
            payload.write_text("[General]\n", encoding="utf-8")
            os.chmod(payload, 0o644)
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            entries = [{"type": "file", "path": "settings.cfg", "sha256": digest, "size": payload.stat().st_size, "mode": 0o644}]
            manifest = {"manifest_version": 2, "backup_id": "backup", "migration_id": "original-v0.8.6-to-current-v1", "source_schema": "original-v0.8.6", "target_schema": "seedsync-current-v1", "created_at": "2026-01-01T00:00:00+00:00", "root_identity": [1, 2], "aggregate": {"entries": 1, "files": 1, "directories": 0, "total_size": payload.stat().st_size}, "entries": entries}
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "migration-state.json").write_text(json.dumps({"state": "complete", "migration_id": "original-v0.8.6-to-current-v1", "backup": "migration-backups/backup"}), encoding="utf-8")
            empty_store = {"version": 3, "api_keys": [], "ui_sessions": [], "browser_handover_claimed_version": ""}
            (root / "api-keys.json").write_text(json.dumps(empty_store), encoding="utf-8")
            os.chmod(root / "api-keys.json", 0o600)
            output = root / "migration.json"
            with self.assertRaisesRegex(ValueError, "Completed migration auth state"):
                HARNESS.assert_migration(root, output)
            history = root / "api-keys.history.jsonl"
            bootstrap = {
                "timestamp": "2026-01-01T00:00:00+00:00", "event": "bootstrap_proof_created",
                "reason": "first_run_bootstrap_window_opened", "store_file": "api-keys.json",
                "details": {"expires_at": "2026-01-01T00:10:00+00:00"},
            }
            saved = {
                "timestamp": "2026-01-01T00:00:01+00:00", "event": "store_saved", "reason": "persisted",
                "store_file": "api-keys.json", "details": {
                    "active_api_key_count": 0, "api_key_count": 0, "bootstrap_exchange_present": False,
                    "bootstrap_proof_present": True, "browser_handover_claimed_version": "",
                    "remembered_ui_session_count": 0, "ui_session_count": 0,
                },
            }
            def write_history(*rows):
                history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                os.chmod(history, 0o644)
            write_history(bootstrap, {**bootstrap, "timestamp": "2026-01-01T00:00:01+00:00"}, saved, {**saved, "timestamp": "2026-01-01T00:00:02+00:00"})
            HARNESS.assert_migration(root, output, auth_store_phase="post-start")
            self.assertEqual("Default", json.loads(output.read_text(encoding="utf-8"))["pair"]["name"])
            summary = json.loads(output.read_text(encoding="utf-8"))["auth_history"]
            self.assertEqual(
                {"bootstrap_proof_created": 2, "store_saved": 2},
                summary["event_counts"],
            )
            (root / "api-keys.json").unlink()
            write_history(bootstrap)
            HARNESS.assert_migration(root, output, auth_store_phase="post-start")
            self.assertEqual("bootstrap-proof-history-only-post-start", json.loads(output.read_text(encoding="utf-8"))["auth_store_state"])
            (root / "api-keys.json").write_text(json.dumps(empty_store), encoding="utf-8")
            for rows, expected in (
                ((bootstrap, bootstrap), "duplicate proof"),
                (({**bootstrap, "event": "api_key_created"},), "event kind"),
                ((bootstrap, {**saved, "details": {**saved["details"], "api_key_count": 1}}), "active mutation"),
                (({**bootstrap, "token": "must-not-appear"},), "event schema"),
                (({**bootstrap, "timestamp": "must-not-appear"},), "event metadata"),
                (({**bootstrap, "details": {"expires_at": "must-not-appear"}},), "proof timestamp"),
            ):
                write_history(*rows)
                with self.subTest(history_failure=expected):
                    with self.assertRaisesRegex(ValueError, "Completed migration auth state"):
                        HARNESS.assert_migration(root, output, auth_store_phase="post-start")
                    failure = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual("failed", failure["status"])
                    self.assertNotIn("must-not-appear", json.dumps(failure))
            history.write_text("{not-json}\n", encoding="utf-8")
            os.chmod(history, 0o644)
            with self.assertRaisesRegex(ValueError, "Completed migration auth state"):
                HARNESS.assert_migration(root, output, auth_store_phase="post-start")
            history.unlink()
            (root / "api-keys.json").unlink()
            HARNESS.assert_migration(root, output, auth_store_phase="post-start")
            self.assertEqual("absent-lazy-post-start", json.loads(output.read_text(encoding="utf-8"))["auth_store_state"])
            HARNESS.assert_migration(root, output, auth_store_phase="migration-apply")
            (root / "api-keys.json").write_text(json.dumps(empty_store), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "transaction end state must not retain"):
                HARNESS.assert_migration(root, output, auth_store_phase="migration-apply")
            (root / "api-keys.json").unlink()
            os.chmod(payload, 0o600)
            HARNESS.assert_migration(root, output)
            self.assertEqual({"settings.cfg": {"manifest_mode": "0644", "actual_mode": "0600"}}, json.loads(output.read_text(encoding="utf-8"))["backup_modes_hardened"])
            os.chmod(payload, 0o666)
            with self.assertRaisesRegex(ValueError, "mode broadened"):
                HARNESS.assert_migration(root, output)
            os.chmod(payload, 0o600)
            entries[0]["mode"] = 0o1644
            manifest["entries"] = entries
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            os.chmod(payload, 0o1600)
            HARNESS.assert_migration(root, output)
            entries[0]["mode"] = 0o644
            manifest["entries"] = entries
            os.chmod(payload, 0o600)
            manifest["source_schema"] = "wrong"
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                HARNESS.assert_migration(root, output)
            manifest["source_schema"] = "original-v0.8.6"
            manifest["migration_id"] = "wrong"
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                HARNESS.assert_migration(root, output)
            manifest["migration_id"] = "original-v0.8.6-to-current-v1"
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            empty_store["api_keys"] = [{"id": "must-not-exist-before-claim"}]
            (root / "api-keys.json").write_text(json.dumps(empty_store), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Completed migration auth state"):
                HARNESS.assert_migration(root, output)
            for mutation, expected in (
                ({"version": 2, "api_keys": [], "ui_sessions": [], "browser_handover_claimed_version": ""}, "schema"),
                ({"version": 3.0, "api_keys": [], "ui_sessions": [], "browser_handover_claimed_version": ""}, "schema"),
                ({"version": 3, "api_keys": [], "ui_sessions": [{}], "browser_handover_claimed_version": ""}, "sessions"),
                ({"version": 3, "api_keys": [], "ui_sessions": [], "browser_handover_claimed_version": "claimed"}, "unclaimed"),
                ({"version": 3, "api_keys": "bad", "ui_sessions": [], "browser_handover_claimed_version": ""}, "API keys"),
                ({"version": 3, "api_keys": [], "ui_sessions": [], "browser_handover_claimed_version": "", "unexpected": True}, "schema"),
            ):
                (root / "api-keys.json").write_text(json.dumps(mutation), encoding="utf-8")
                with self.subTest(auth_mutation=mutation):
                    with self.assertRaisesRegex(ValueError, "Completed migration auth state"):
                        HARNESS.assert_migration(root, output)
            (root / "api-keys.json").unlink()
            os.symlink("missing-api-keys.json", root / "api-keys.json")
            with self.assertRaisesRegex(ValueError, "Completed migration auth state"):
                HARNESS.assert_migration(root, output)
            (root / "api-keys.json").unlink()
            os.symlink("missing-api-key-history", root / "api-keys.history.jsonl")
            with self.assertRaisesRegex(ValueError, "Completed migration auth state"):
                HARNESS.assert_migration(root, output)
            (root / "api-keys.history.jsonl").unlink()
            for mutate, expected in (
                (lambda: entries.append(dict(entries[0])), "duplicate"),
                (lambda: entries.__setitem__(0, {**entries[0], "path": "../escape"}), "unsafe"),
                (lambda: entries.__setitem__(0, {**entries[0], "type": "link"}), "type"),
                (lambda: entries.__setitem__(0, {**entries[0], "mode": -1}), "incomplete"),
            ):
                baseline = json.loads(json.dumps(manifest))
                entries[:] = baseline["entries"]
                mutate()
                manifest["entries"] = entries
                (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(manifest_mutation=expected):
                    with self.assertRaisesRegex(ValueError, expected): HARNESS.assert_migration(root, output)
                manifest = baseline
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (data / "extra").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing or unexpected"):
                HARNESS.assert_migration(root, output)

    def test_current_product_auth_contract_requires_immutable_image_and_containment_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = root / "current-product-preclaim-auth-contract.json"
            payload = {
                "schema": 1,
                "validator": "current-product-preclaim-auth",
                "status": "passed",
                "image_ref": "seedsync/upgrade-v086:current-ship-20260729-r26b",
                "image_id": "sha256:" + "a" * 64,
                "image_digest": "unpublished:sha256:" + "a" * 64,
                "image_provenance": "immutable-current-image-id",
                "container": "seedsync-upgrade-v086-current-auth-validator-ship-20260729-r26b",
                "containment": {
                    "network": "none", "read_only_rootfs": True, "config_mount_read_only": True,
                    "evidence_mount_read_only": True, "user": "1000:1000",
                    "no_new_privileges": True, "cap_drop_all": True,
                },
            }
            contract.write_text(json.dumps(payload), encoding="utf-8")
            summary = HARNESS._current_product_auth_contract_summary(contract)
            self.assertEqual("validated-by-current-product-image", summary["order_class"])
            self.assertEqual(payload["image_id"], summary["image_id"])
            payload["status"] = "failed"
            contract.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "current product auth contract is invalid"):
                HARNESS._current_product_auth_contract_summary(contract)

    def test_current_product_auth_validator_launcher_is_immutable_contained_and_safe_on_missing_module(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        self.assertIn("current_product_preclaim_auth_contract()", launcher)
        self.assertIn("current_product_claimed_auth_contract()", launcher)
        self.assertIn('image="seedsync/upgrade-v086:current-${1,,}"', launcher)
        self.assertIn('docker create --name "$validator" --network none --read-only --user 1000:1000', launcher)
        self.assertIn("--security-opt no-new-privileges:true --cap-drop ALL", launcher)
        self.assertIn("--env PYTHONPATH=/app/python", launcher)
        self.assertIn('--entrypoint python "$image_id"', launcher)
        self.assertIn('from pathlib import Path\ntry:\n    from web.auth_store', launcher)
        self.assertIn("product-validator-module-unavailable", launcher)
        self.assertIn("product-validator-rejected-state", launcher)
        self.assertIn('"error_type": type(error).__name__', launcher)
        self.assertIn("immutable-current-image-id", launcher)
        self.assertIn("--product-auth-contract \"$(validator_evidence_path current-product-preclaim-auth-contract.json)\"", launcher)
        self.assertIn("migration-current-preclaim-auth-flush", launcher)
        self.assertIn("migration-current-preclaim-restart-ready", launcher)
        self.assertIn('decision.completed_auth_phase != "claimed"', launcher)
        self.assertIn('"marker_binding": "receipt-and-backup"', launcher)

    def test_browser_claim_harness_classifies_standalone_bootstrap_before_shell_readiness(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        self.assertIn("async function classifyStandaloneBootstrap()", browser)
        self.assertIn("body.bootstrap-page", browser)
        self.assertIn("form#bootstrap-form", browser)
        self.assertIn("Claim the first local session", browser)
        self.assertIn("name: 'Claim session', exact: true", browser)
        self.assertIn("await secretInput.count() === 0", browser)
        self.assertIn("claimClassification = 'remembered-api-key-bootstrap'", browser)
        self.assertIn("name: 'Remember browser', exact: true", browser)
        self.assertIn("await page.waitForFunction(() => window.location.pathname !== '/bootstrap'", browser)
        self.assertIn("post-claim-route-shell-status", browser)
        self.assertLess(browser.index("const claim = await classifyStandaloneBootstrap();"), browser.index("await completeFirstBootstrapClaim(claim);"))
        claim_complete = browser.index("await completeFirstBootstrapClaim(claim);")
        self.assertLess(claim_complete, browser.index("await safeScreenshot", claim_complete))
        self.assertIn("Verify its API contract without visiting that presentation surface", browser)
        self.assertIn("await requireApi('settings', '/server/config/get');", browser)
        self.assertNotIn("await navigateReady('settings'", browser)

    def test_browser_evidence_keeps_runtime_and_diagnostic_capture_failures_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "browser.json", root / "contract.json"
            baseline = {
                "errors": [], "runtimeErrors": [], "diagnosticFailures": [],
                "api": {"/server/status": {"status": 200}},
                "visibleFixtureRows": {"fixture": True}, "claimButtonCount": 1,
                "actions": {"queue:fixture": 200},
            }
            source.write_text(json.dumps(baseline), encoding="utf-8")
            HARNESS.assert_browser_evidence(source, output)
            for field, value in (
                ("runtimeErrors", [{"kind": "console-error", "classification": "captured-redacted", "message": "safe"}]),
                ("runtimeErrors", [{"kind": "pageerror", "classification": "captured-redacted", "message": "safe"}]),
                ("diagnosticFailures", [{"kind": "console-error", "classification": "event-capture-failed"}]),
                ("diagnosticFailures", [{"kind": "console-error", "classification": "redaction-failed"}]),
            ):
                payload = {**baseline, field: value}
                source.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(field=field, classification=value[0]["classification"]):
                    with self.assertRaisesRegex(ValueError, "browser/API evidence has errors"):
                        HARNESS.assert_browser_evidence(source, output)

    def test_browser_first_claim_evidence_requires_real_api_status_observations(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        self.assertIn("const apiEvidence = {}", browser)
        self.assertIn("apiEvidence[label] =", browser)
        self.assertIn("const mergedApiEvidence", browser)
        self.assertIn("await writeEvidence({ browserStability: ready }, 'browser-stability.json');", browser)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "browser.json", root / "contract.json"
            baseline = {
                "errors": [], "runtimeErrors": [], "diagnosticFailures": [],
                "api": {
                    "after-first-claim": {"endpoint": "/server/status", "status": 200, "readiness": "API HTTP 200"},
                    "settings": {"endpoint": "/server/config/get", "status": 200, "readiness": "API HTTP 200"},
                },
                "visibleFixtureRows": {"fixture": True}, "claimButtonCount": 1,
                "actions": {"queue:fixture": 200},
            }
            source.write_text(json.dumps(baseline), encoding="utf-8")
            HARNESS.assert_browser_evidence(source, output)
            invalid_payloads = [("absent", {key: value for key, value in baseline.items() if key != "api"})]
            invalid_payloads.extend((str(invalid_api), {**baseline, "api": invalid_api}) for invalid_api in (
                None, [], {},
                {"settings": {"endpoint": "/server/config/get", "status": 503}},
                {"fake-null": None}, {"fake-string": "status"}, {"fake-list": []},
            ))
            for label, payload in invalid_payloads:
                source.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(api=label):
                    with self.assertRaisesRegex(ValueError, "browser/API evidence has errors"):
                        HARNESS.assert_browser_evidence(source, output)

    def test_browser_reuse_evidence_requires_the_r31i_bounded_restart_cluster(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "browser-reuse.json", root / "contract.json"
            transition = {
                "kind": "restart-armed-sse-transport-cluster",
                "classification": "expected-bounded-restart-transport-cluster",
                "totalCount": 5, "sseEventCount": 3, "badGateway502Count": 2,
                "firstGeneration": 2, "lastGeneration": 6,
                "firstObservedAfterMs": 43540, "lastObservedAfterMs": 53999, "clusterMaximumEvents": 8, "clusterMaximumMs": 15000,
                "recoveryStatus": 200, "modelRows": 4,
                "stopDispatchProof": {"schema": 1, "run_id": "r31i", "stability_generation": 1, "arm_generation": 2,
                                      "acknowledged_error_generation": 1, "restart_stop_dispatched": True,
                                      "stop_dispatch_epoch_ms": 1700000000100, "acknowledgedEpochMs": 1700000000000},
                "sameOriginProof": {
                    "origin": "http://127.0.0.1:18820", "pathname": "/server/stream",
                    "orderedTemporal502Associations": [
                        {"origin": "http://127.0.0.1:18820", "pathname": "/server/stream", "errorGeneration": 3, "responseObservedAfterMs": 49911, "errorObservedAfterMs": 50000, "responseConnectionId": 0, "temporalAssociation": "ordered-response-before-console"},
                        {"origin": "http://127.0.0.1:18820", "pathname": "/server/stream", "errorGeneration": 5, "responseObservedAfterMs": 50200, "errorObservedAfterMs": 50300, "responseConnectionId": 1, "temporalAssociation": "ordered-response-before-console"},
                    ],
                    "recoveryOrigin": "http://127.0.0.1:18820", "recoveryPathname": "/server/stream",
                    "recoveryStatus": 200, "recoveryObservedAfterMs": 50300,
                },
            }
            convergence = {
                "kind": "post-reuse-sse-convergence", "classification": "clean-post-reuse-convergence",
                "totalCount": 0, "firstGeneration": 6, "lastGeneration": 6,
                "clusterMaximumEvents": 4, "clusterMaximumMs": 5000,
                "phaseBoundary": {"kind": "pre-reuse-action-start", "errorGeneration": 6, "observedAfterMs": 58000, "observedAtEpochMs": 1700000001000},
                "recoveryOrigin": "http://127.0.0.1:18820", "recoveryPathname": "/server/stream", "recoveryStatus": 200,
                "recoveryObservedAfterMs": 59000, "modelRows": 4, "modelObservedAfterMs": 59000,
            }
            baseline = {
                "errors": [], "runtimeErrors": [], "diagnosticFailures": [],
                "streamConnections": [
                    {"connectionId": 0, "origin": "http://127.0.0.1:18820", "pathname": "/server/stream", "status": 502, "contentType": "other", "observedAfterMs": 49911},
                    {"connectionId": 1, "origin": "http://127.0.0.1:18820", "pathname": "/server/stream", "status": 502, "contentType": "other", "observedAfterMs": 50200},
                ],
                "api": {"restart-status": {"status": 200}, "restart-settings": {"status": 200}, "post-reuse-convergence-status": {"status": 200, "observedAfterMs": 59001, "observedAtEpochMs": 1700000001500}, "post-reuse-convergence-settings": {"status": 200, "observedAfterMs": 59002, "observedAtEpochMs": 1700000001600}},
                "visibleFixtureRows": {"fixture": True}, "expectedTransitions": [transition, convergence],
                "streamTransitionEvidence": [dict(transition), dict(convergence)], "postReuseQuiet": {"quietWindowMs": 1500, "errorGeneration": 6, "observedAfterMs": 60000, "observedAtEpochMs": 1700000002000},
            }
            source.write_text(json.dumps(baseline), encoding="utf-8")
            HARNESS.assert_browser_evidence(source, output, reuse=True)
            active_convergence = {
                "kind": "post-reuse-sse-convergence", "classification": "expected-bounded-post-reuse-sse-convergence",
                "totalCount": 3, "firstGeneration": 7, "lastGeneration": 9,
                "firstObservedAfterMs": 56957, "lastObservedAfterMs": 57417,
                "clusterMaximumEvents": 4, "clusterMaximumMs": 5000,
                "recoveryOrigin": "http://127.0.0.1:18820", "recoveryPathname": "/server/stream",
                "recoveryStatus": 200, "recoveryObservedAfterMs": 59742, "modelRows": 4, "modelObservedAfterMs": 59800,
                "phaseBoundary": {"kind": "pre-reuse-action-start", "errorGeneration": 6, "observedAfterMs": 56000, "observedAtEpochMs": 1700000001000},
            }
            active_payload = {
                **baseline,
                "api": {**baseline["api"], "post-reuse-convergence-status": {"status": 200, "observedAfterMs": 59850, "observedAtEpochMs": 1700000001500}, "post-reuse-convergence-settings": {"status": 200, "observedAfterMs": 59900, "observedAtEpochMs": 1700000001600}},
                "expectedTransitions": [transition, active_convergence],
                "streamTransitionEvidence": [dict(transition), dict(active_convergence)],
                "postReuseQuiet": {"quietWindowMs": 1500, "errorGeneration": 9, "observedAfterMs": 60000, "observedAtEpochMs": 1700000002000},
            }
            source.write_text(json.dumps(active_payload), encoding="utf-8")
            HARNESS.assert_browser_evidence(source, output, reuse=True)
            invalid_payloads = (
                {**baseline, "expectedTransitions": []},
                {**baseline, "expectedTransitions": [{**transition, "sseEventCount": 0}]},
                {**baseline, "expectedTransitions": [{**transition, "totalCount": 9, "sseEventCount": 7}]},
                {**baseline, "expectedTransitions": [{**transition, "badGateway502Count": 3}]},
                {**baseline, "expectedTransitions": [{**transition, "lastObservedAfterMs": 58541}]},
                {**baseline, "expectedTransitions": [{key: value for key, value in transition.items() if key != "recoveryStatus"}, convergence]},
                {**baseline, "expectedTransitions": [{key: value for key, value in transition.items() if key != "sameOriginProof"}]},
                {**baseline, "expectedTransitions": [{**transition, "sameOriginProof": {**transition["sameOriginProof"], "recoveryOrigin": "https://foreign.invalid"}}]},
                {**baseline, "streamTransitionEvidence": []},
                {**baseline, "streamTransitionEvidence": [{**transition, "recoveryStatus": 503}, convergence]},
                {**baseline, "streamTransitionEvidence": [{**transition, "sameOriginProof": {**transition["sameOriginProof"], "recoveryPathname": "/other"}}]},
            )
            for payload in invalid_payloads:
                source.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload):
                    with self.assertRaisesRegex(ValueError, "browser restart transport evidence"):
                        HARNESS.assert_browser_evidence(source, output, reuse=True)
            invalid_convergence_payloads = []
            for field, value in (
                ("firstGeneration", 8), ("lastGeneration", 10), ("recoveryStatus", 502),
                ("recoveryPathname", "/other"), ("modelRows", 0),
            ):
                payload = json.loads(json.dumps(active_payload))
                payload["expectedTransitions"][1][field] = value
                invalid_convergence_payloads.append(payload)
            missing_api = json.loads(json.dumps(active_payload))
            del missing_api["api"]["post-reuse-convergence-settings"]
            invalid_convergence_payloads.append(missing_api)
            wrong_quiet = json.loads(json.dumps(active_payload))
            wrong_quiet["postReuseQuiet"]["errorGeneration"] = 6
            invalid_convergence_payloads.append(wrong_quiet)
            reverse_502 = json.loads(json.dumps(active_payload))
            reverse_502["expectedTransitions"][0]["sameOriginProof"]["orderedTemporal502Associations"][0]["responseObservedAfterMs"] = 50001
            invalid_convergence_payloads.append(reverse_502)
            duplicate_502 = json.loads(json.dumps(active_payload))
            duplicate_502["expectedTransitions"][0]["sameOriginProof"]["orderedTemporal502Associations"][1]["responseConnectionId"] = 0
            invalid_convergence_payloads.append(duplicate_502)
            mismatched_502 = json.loads(json.dumps(active_payload))
            mismatched_502["streamConnections"][0]["status"] = 200
            invalid_convergence_payloads.append(mismatched_502)
            pre_boundary = json.loads(json.dumps(active_payload))
            pre_boundary["expectedTransitions"][1]["firstObservedAfterMs"] = 56000
            invalid_convergence_payloads.append(pre_boundary)
            missing_boundary_kind = json.loads(json.dumps(active_payload))
            del missing_boundary_kind["expectedTransitions"][1]["phaseBoundary"]["kind"]
            invalid_convergence_payloads.append(missing_boundary_kind)
            error_after_recovery = json.loads(json.dumps(active_payload))
            error_after_recovery["expectedTransitions"][1]["lastObservedAfterMs"] = 59743
            invalid_convergence_payloads.append(error_after_recovery)
            model_before_recovery = json.loads(json.dumps(active_payload))
            model_before_recovery["expectedTransitions"][1]["modelObservedAfterMs"] = 59741
            invalid_convergence_payloads.append(model_before_recovery)
            api_before_model = json.loads(json.dumps(active_payload))
            api_before_model["api"]["post-reuse-convergence-status"]["observedAfterMs"] = 59799
            invalid_convergence_payloads.append(api_before_model)
            api_after_quiet = json.loads(json.dumps(active_payload))
            api_after_quiet["api"]["post-reuse-convergence-settings"]["observedAfterMs"] = 60001
            invalid_convergence_payloads.append(api_after_quiet)
            quiet_before_observations = json.loads(json.dumps(active_payload))
            quiet_before_observations["postReuseQuiet"]["observedAfterMs"] = 59741
            invalid_convergence_payloads.append(quiet_before_observations)
            for payload in invalid_convergence_payloads:
                source.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(convergence=payload):
                    with self.assertRaisesRegex(ValueError, "browser (restart transport|post-reuse convergence) evidence"):
                        HARNESS.assert_browser_evidence(source, output, reuse=True)

    def test_browser_harness_captures_event_metadata_synchronously_and_safely(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        self.assertIn("captureBrowserDiagnostic('console-error'", browser)
        self.assertIn("captureBrowserDiagnostic('pageerror'", browser)
        self.assertIn("function synchronousRedactedDiagnostic", browser)
        self.assertIn("classification: 'event-capture-failed'", browser)
        self.assertIn("envelope.classification = 'redaction-failed'", browser)
        self.assertIn("runtimeErrors.push(envelope)", browser)
        self.assertIn("diagnosticFailures.push", browser)
        self.assertNotIn("queueBrowserDiagnostic", browser)

    def test_browser_harness_narrowly_classifies_only_recovered_first_claim_sse_transition(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        self.assertIn("function isFirstClaimSseTransportError", browser)
        self.assertIn("error.message === 'Error in stream: %O Event'", browser)
        self.assertIn("claimClassification === 'first-claim-bootstrap'", browser)
        self.assertIn("const expectedStreamOrigin = new URL(baseUrl).origin", browser)
        self.assertIn("const sseReconnectMaximumMs = 25000", browser)
        self.assertIn("const sseRecoveryPollMs = 200", browser)
        self.assertNotIn("ssePostClaimObservationMs", browser)
        self.assertIn("first-claim-sse-recovery-status", browser)
        self.assertIn("recoveryModelRows", browser)
        self.assertIn("recovery-timeout", browser)
        self.assertIn("recovery-aborted-error", browser)
        self.assertIn("recovery-unstable", browser)
        self.assertIn("runtimeErrors.splice(runtimeErrors.indexOf(error), 1)", browser)
        self.assertIn("post-claim-stateful-reconnect", browser)
        self.assertIn("recoveryLatencyMs", browser)
        self.assertIn("streamAttemptCount", browser)
        self.assertIn("statusAfterRecovery", browser)
        self.assertIn("modelTimeoutMs = Math.min(15000, deadline - (Date.now() - browserStartedAt))", browser)
        self.assertIn("model.length < minimumRows", browser)
        self.assertIn("minimumRows: fixtureNames.length", browser)
        self.assertIn("currentMatching.length !== 1 || otherErrors.length || diagnosticFailures.length", browser)
        self.assertIn("claimCompletedAtMs,", browser)
        self.assertIn("connection.origin === expectedOrigin", browser)
        self.assertIn("ignoredOtherOriginCount", browser)
        self.assertIn("function firstClaimSseRecoveryState", browser)
        self.assertIn("connection.observedAfterMs > errorObservedAfterMs", browser)
        self.assertIn("--first-claim-sse-recovery-self-check", browser)
        self.assertIn("browser_first_claim_sse_recovery_self_check", launcher)
        self.assertNotIn("sseReconnectMinimumMs", browser)
        self.assertNotIn("ignoredPreMinimumCount", browser)
        self.assertIn("async function establishPreRestartStability", browser)
        self.assertIn("browser-stability-request.json", browser)
        self.assertIn("browser-stability-ready.json", browser)
        self.assertIn("browser-stability-invalid.json", browser)
        self.assertIn("stability_generation", browser)
        self.assertIn("browserErrorGeneration += 1", browser)
        self.assertIn("await classifyRecoveredFirstClaimSseTransition();", browser)
        self.assertLess(browser.index("async function establishPreRestartStability"), browser.index("async function waitForRestartRequest"))

    def test_first_claim_sse_recovery_is_cached_before_late_stability_request(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        for marker in (
            "let firstClaimSseRecoveryPromise = null",
            "let validatedFirstClaimSseRecovery = null",
            "let firstClaimSseRecoveryFailure = null",
            "void classifyRecoveredFirstClaimSseTransition();",
            "async function observeFirstClaimSseRecovery()",
            "const deadline = error.observedAfterMs + sseReconnectMaximumMs",
            "claimClassification,",
            "claimPhase,",
            "error.claimPhase === 'post-claim-complete'",
            "errorGeneration: browserErrorGeneration",
            "validatedFirstClaimSseRecovery = validated",
            "if (matching.length === 1 || firstClaimSseRecoveryPromise || validatedFirstClaimSseRecovery)",
            "validatedFirstClaimSseRecovery.errorGeneration !== browserErrorGeneration",
        ):
            self.assertIn(marker, browser)
        self.assertLess(browser.index("void classifyRecoveredFirstClaimSseTransition();"), browser.index("async function establishPreRestartStability"))
        self.assertLess(browser.index("validatedFirstClaimSseRecovery = validated"), browser.index("async function establishPreRestartStability"))
        self.assertIn("firstClaimSseRecoveryFailure || runtimeErrors.length || diagnosticFailures.length", browser)

    def test_browser_stability_handshake_is_run_bound_and_precedes_restart_request(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            "request.request_kind !== 'pre-restart-stability'",
            "async function waitForStabilityRequest()",
            "async function establishPreRestartStability(request)",
            "browser-stability-ready.json",
            "browser-stability-invalid.json",
            "stability.error_generation",
            "parsed.stability_generation !== stability.error_generation",
            "async function waitForRestartArm(stability)",
            "browser-restart-arm.json",
            "browser-restart-arm-ack.json",
            "async function waitForRestartRequest(stability, arm)",
            "parsed.arm_generation !== arm.arm_generation",
        ):
            self.assertIn(marker, browser)
        for marker in (
            "request_browser_stability()",
            "wait_browser_stability_ready()",
            "arm_browser_restart()",
            "wait_browser_restart_arm_ack()",
            "browser-stability-request.json",
            "browser-stability-ready.json",
            "browser-restart-arm.json",
            "browser-restart-arm-ack.json",
            "stability_generation",
            "arm_generation",
        ):
            self.assertIn(marker, launcher)
        self.assertLess(launcher.index('request_browser_stability "$id"'), launcher.index('stop_container "$id" migration-current-restart-stop'))
        self.assertLess(launcher.index('wait_browser_stability_ready "$id"'), launcher.index('stop_container "$id" migration-current-restart-stop'))
        self.assertLess(launcher.index('arm_browser_restart "$id"'), launcher.index('stop_container "$id" migration-current-restart-stop'))
        self.assertLess(launcher.index('wait_browser_restart_arm_ack "$id"'), launcher.index('stop_container "$id" migration-current-restart-stop'))
        stop_container = launcher[launcher.index("stop_container() {") : launcher.index("wait_for_downloads() {")]
        self.assertIn('dispatch_mode="${5:-}"', stop_container)
        self.assertIn('[[ "$dispatch_mode" == restart-dispatch ]]', stop_container)
        self.assertIn('export -f publish_browser_restart_stop_dispatch', stop_container)
        self.assertIn('exec docker stop --time 20 "$5"', stop_container)
        self.assertIn('publish_browser_restart_stop_dispatch "$1" "$2" "$3" "$4" || exit $?', stop_container)
        self.assertLess(stop_container.index('publish_browser_restart_stop_dispatch'), stop_container.index('docker stop --time 20 "$name"'))
        self.assertIn('restart-dispatch "$stability_generation" "$restart_arm_generation"', launcher)
        finish = launcher[launcher.index("finish_browser_claim_reuse()") : launcher.index("browser_dispatch_self_check()")]
        self.assertLess(finish.index('"stability_generation": int(generation)'), finish.index('wait "$BROWSER_SESSION_PID"'))
        self.assertLess(finish.index('"arm_generation": int(arm_generation)'), finish.index('wait "$BROWSER_SESSION_PID"'))

    def test_browser_restart_arm_protocol_accepts_only_the_bounded_r31i_cluster(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            "function parseRestartArm(parsed, stability)",
            "parsed.run_id !== screenshotRunId",
            "parsed.stability_generation !== stability.error_generation",
            "parsed.arm_generation !== stability.error_generation + 1",
            "function isArmedRestartSseTransportError(error, arm)",
            "error.observed_at_epoch_ms >= arm.stop_dispatch_epoch_ms",
            "function restart502StreamLocation(error, arm, usedResponses)",
            "Failed to load resource: the server responded with a status of 502 (Bad Gateway)",
            "location.origin !== arm.origin || location.pathname !== '/server/stream'",
            "restartClusterMaximumEvents = 8",
            "restartClusterMaximumMs = 15000",
            "restartTransportResponseCorrelationMs = 1000",
            "connection.observedAfterMs <= error.observedAfterMs",
            "orderedTemporal502Associations",
            "temporalAssociation: 'ordered-response-before-console'",
            "async function restartClusterEntries(stability, arm)",
            "error.error_generation !== arm.arm_generation + index",
            "restart-arm-cluster-over-cap",
            "restart-arm-cluster-unexpected-diagnostic",
            "restart-arm-cluster-wrong-generation",
            "restart-arm-cluster-window-exceeded",
            "async function finalizeArmedRestartTransport(stability, arm, deadline)",
            "restart-request-missing-expected-sse-event",
            "restart-request-missing-stream-recovery",
            "restart-arm-post-classification-error",
            "restart-request-wrong-run-or-generation",
            "browser-stability-invalid.json",
            "browser-restart-invalid.json",
            "kind: 'restart-armed-sse-transport-cluster'",
            "classification: 'expected-bounded-restart-transport-cluster'",
            "totalCount: validatedEntries.length",
            "sseEventCount: validatedEntries.length - resourceEntries.length",
            "badGateway502Count: resourceEntries.length",
            "sameOriginProof:",
            "recoveryStatus: recovery.status",
            "for (const entry of validatedEntries) runtimeErrors.splice(runtimeErrors.indexOf(entry.error), 1)",
            "async function finalizePostReuseConvergence(stability, arm, boundary)",
            "postReuseClusterMaximumEvents = 4",
            "post-reuse-convergence-invalid-diagnostic",
            "post-reuse-convergence-window-exceeded",
            "kind: 'post-reuse-sse-convergence'",
            "kind: 'pre-reuse-action-start'",
            "writeEvidenceNoFlush({ ...reuse, postReuseConvergence, postReuseQuiet })",
            "browser-reuse-quiescence-invalidated",
        ):
            self.assertIn(marker, browser)
        for marker in (
            "expected_ack = expected_arm | {\"acknowledged\", \"acknowledged_error_generation\", \"acknowledged_epoch_ms\"}",
            "browser restart arm acknowledgement is invalid",
            "report_browser_restart_invalidation",
            "browser-reuse.json",
        ):
            self.assertIn(marker, launcher)
        self.assertLess(browser.index("async function waitForRestartArm(stability)"), browser.index("async function waitForRestartRequest(stability, arm)"))
        restart_producer = browser[browser.index("async function finalizeArmedRestartTransport(stability, arm, deadline)"):browser.index("async function assertRestartArmWindow(stability, arm)")]
        self.assertIn("recoveryStatus: recovery.status,\n        stopDispatchProof:", restart_producer)
        handoff = browser[browser.index("const arm = await waitForRestartArm(stability);"):]
        self.assertLess(handoff.index("const arm = await waitForRestartArm(stability);"), handoff.index("await waitForRestartRequest(stability, arm);"))
        self.assertLess(handoff.index("const postReuseBoundary = { kind: 'pre-reuse-action-start'"), handoff.index("const reuse = await runReuse();"))
        self.assertLess(handoff.index("const postReuseConvergence = await finalizePostReuseConvergence(stability, arm, postReuseBoundary);"), handoff.index("const postReuseQuiet = await waitForPostReuseQuiet(stability, arm, postReuseBoundary);"))
        self.assertLess(handoff.index("await flushBrowserDiagnostics();"), handoff.index("await closeBrowserResources();"))
        self.assertLess(handoff.index("await closeBrowserResources();"), handoff.index("writeEvidenceNoFlush({ ...reuse, postReuseConvergence, postReuseQuiet });"))
        self.assertLess(browser.index("await finalizeArmedRestartTransport(stability, arm, deadline);"), handoff.index("await runReuse();") + browser.index("const arm = await waitForRestartArm(stability);"))
        self.assertLess(handoff.index("const reuse = await runReuse();"), handoff.index("const postReuseQuiet = await waitForPostReuseQuiet(stability, arm, postReuseBoundary);"))

    def test_browser_stability_request_timestamp_validator_matches_emitted_contract(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        match = re.search(r're\.fullmatch\(r"([^"]+)"', launcher)
        self.assertIsNotNone(match)
        timestamp = re.compile(match.group(1))
        self.assertIsNotNone(timestamp.fullmatch("2026-07-29T15:24:08Z"))
        for malformed in (
            "2026-7-29T15:24:08Z",
            "2026-07-29 15:24:08Z",
            "2026-07-29T15:24:08+00:00",
            "2026-07-29T15:24:08Z-extra",
        ):
            with self.subTest(malformed=malformed):
                self.assertIsNone(timestamp.fullmatch(malformed))

    def test_auth_phase_boundary_binds_completed_transaction_to_auth_free_legacy_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inventory = root / "before-config.json"
            legacy_auth = root / "before-legacy-auth-absence.json"
            status = root / "migration-status.json"
            output = root / "migration-apply-auth-contract.json"
            inventory.write_text(json.dumps({"schema": 1, "entries": []}), encoding="utf-8")
            HARNESS.assert_legacy_auth_absence(inventory, legacy_auth)
            legacy_source = root / "legacy-source"
            legacy_source.mkdir()
            os.symlink("missing-api-keys.json", legacy_source / "api-keys.json")
            with self.assertRaisesRegex(ValueError, "refusing symlink"):
                HARNESS.inventory(legacy_source, legacy_config=True)
            status.write_text(json.dumps({"final": {"http_status": 200, "migration_status": {
                "migration_id": "original-v0.8.6-to-current-v1", "state": "complete",
                "operation": {"status": "succeeded"},
                "backup": {"status": "ready", "complete_restore_ready": True},
            }}}), encoding="utf-8")
            HARNESS.assert_migration_apply_auth_boundary(status, legacy_auth, output)
            self.assertEqual(
                "absent-at-validated-transaction-end-state",
                json.loads(output.read_text(encoding="utf-8"))["auth_store_state"],
            )
            self.assertEqual(
                ["api-keys.json", "api-keys.history.jsonl"],
                json.loads(output.read_text(encoding="utf-8"))["auth_state_paths_verified"],
            )
            inventory.write_text(json.dumps({"schema": 1, "entries": [{"path": "api-keys.json", "type": "file"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contains auth state"):
                HARNESS.assert_legacy_auth_absence(inventory, legacy_auth)
            status.write_text(json.dumps({"final": {"http_status": 200, "migration_status": {
                "migration_id": "original-v0.8.6-to-current-v1", "state": "complete",
                "operation": {"status": "running"},
                "backup": {"status": "ready", "complete_restore_ready": True},
            }}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "complete boundary"):
                HARNESS.assert_migration_apply_auth_boundary(status, legacy_auth, output)

    def test_legacy_inventory_excludes_entire_migration_infrastructure_subtree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "settings.cfg").write_text("[General]\n", encoding="utf-8")
            backup_data = root / "migration-backups" / "backup" / "data"
            backup_data.mkdir(parents=True)
            (backup_data / "controller.persist").write_text("{}", encoding="utf-8")
            (root / ".seedsync.runtime.lock").write_text("runtime", encoding="utf-8")
            paths = [entry["path"] for entry in HARNESS.inventory(root, legacy_config=True)["entries"]]
            self.assertEqual(["settings.cfg"], paths)

    def test_restore_assertion_ignores_retained_backup_but_rejects_current_generated_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = root / "settings.cfg"
            settings.write_text("[General]\n", encoding="utf-8")
            expected = HARNESS.inventory(root, legacy_config=True)
            backup_data = root / "migration-backups" / "backup" / "data"
            backup_data.mkdir(parents=True)
            (backup_data / "settings.cfg").write_text("[General]\n", encoding="utf-8")
            output = root / "restore.json"
            HARNESS.assert_restore(root, expected, output)
            self.assertEqual([], json.loads(output.read_text(encoding="utf-8"))["unexpected_current_files"])
            (root / "path_pairs.json").write_text("{}", encoding="utf-8")
            (root / ".migration.lock").write_text("locked", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "legacy configuration contract"):
                HARNESS.assert_restore(root, expected, output)
            self.assertEqual(
                [".migration.lock", "path_pairs.json"],
                json.loads(output.read_text(encoding="utf-8"))["unexpected_current_files"],
            )

    def test_current_runtime_uses_recovery_bootstrap_before_browser_authenticated_checks(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        self.assertIn("wait_preclaim_auth_challenge()", launcher)
        self.assertIn("wait_normal_runtime_readiness()", launcher)
        self.assertNotIn('wait_http "$current/server/status"', launcher)
        self.assertIn('after-normal-runtime-transition.json', launcher)
        self.assertIn('after-restart-claimed-auth.json', launcher)
        self.assertIn('current-product-claimed-auth-contract.json', launcher)
        self.assertIn('SEEDSYNC_BROWSER_HANDOVER_RECOVERY=1', launcher)
        self.assertIn("preclaim-auth-challenge-evidence", launcher)
        self.assertIn("SEEDSYNC_SHIP_PRECLAIM_TEMP_ROOT", launcher)
        self.assertIn("const recoveryHandoverMode", browser)
        self.assertIn("new URL('/bootstrap', baseUrl).href", browser)
        preclaim_wait = launcher.split("wait_preclaim_auth_challenge()", 1)[1].split("capture_inventory()", 1)[0]
        self.assertNotIn('"${output}.headers"', preclaim_wait)
        self.assertNotIn('"${output}.stderr"', preclaim_wait)
        self.assertIn("await requireApi('after-first-claim', '/server/status');", browser)
        self.assertIn("await requireApi('restart-status', '/server/status');", browser)

    def test_migration_transition_requires_a_new_normal_runtime_before_bootstrap(self):
        complete = {
            "state": "complete", "migration_id": "original-v0.8.6-to-current-v1",
            "operation": {"status": "succeeded"},
            "backup": {"status": "ready", "complete_restore_ready": True},
        }
        self.assertEqual(
            "container-restart-required",
            HARNESS.migration_terminal_transition_evidence(complete)["normal_runtime_transition"],
        )
        with self.assertRaisesRegex(ValueError, "migration runtime remains active"):
            HARNESS.normal_runtime_transition_evidence(200, 404, 404)
        with self.assertRaisesRegex(ValueError, "status route is not ready"):
            HARNESS.normal_runtime_transition_evidence(404, 404, 200)
        with self.assertRaisesRegex(ValueError, "bootstrap route is not ready"):
            HARNESS.normal_runtime_transition_evidence(404, 401, 404)
        self.assertEqual(
            {"schema": 1, "migration_runtime": "inactive", "status_route": "api_key_required", "bootstrap_route": "ready"},
            HARNESS.normal_runtime_transition_evidence(404, 401, 200),
        )

    def test_launcher_waits_for_migration_exit_and_route_proven_normal_runtime(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            "cross_migration_runtime_boundary()", "migration-terminal-transition-evidence",
            "migration-current-normal-transition-stop", "wait_normal_runtime_readiness()",
            "normal-runtime-transition-evidence", "migration-current-normal-transition-ready",
            "migration-current-normal-transition-start", "migration_state=complete",
        ):
            self.assertIn(marker, launcher)
        self.assertNotIn('sleep 3\n  # The explicit recovery version', launcher)

    def test_volume_helper_outputs_are_atomic_and_failure_diagnostics_are_bounded(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            "capture_volume_helper_output()", "SEEDSYNC_SHIP_VOLUME_HELPER_TEMP_ROOT",
            "os.replace(temporary, output)", "capture_volume_helper_failure()",
            "volume-helper-${label%.json}", "volume_helper_output_self_check",
        ):
            self.assertIn(marker, launcher)
        self.assertNotIn('volume_helper "$id" "$@" --output - > "$output"', launcher)
        self.assertIn('redact() { python "$HELPER" redact-stdin; }', launcher)

    def test_retained_run_audit_reports_only_safe_paths_and_rejects_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "safe.json").write_text('{"status":"ok"}', encoding="utf-8")
            output = root / "audit.json"
            HARNESS.audit_retained_run(root, ["injected-canary"], output)
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["passed"])
            (root / "leaked.json").write_text("injected-canary", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, ["injected-canary"], output)
            report = json.loads(output.read_text(encoding="utf-8"))
            rendered = json.dumps(report)
            self.assertFalse(report["passed"])
            self.assertIn("leaked.json", rendered)
            self.assertNotIn("injected-canary", rendered)

    def test_retained_run_audit_scans_large_artifacts_in_bounded_chunks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            large = root / "fixture.bin"
            large.write_bytes(b"x" * (1024 * 1024 + 4096) + b"token=synthetic-secret")
            output = root / "audit.json"
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, [], output)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn({"kind": "secret-pattern-quarantined", "path": "fixture.bin"}, report["findings"])
            self.assertEqual(0, large.stat().st_mode & 0o777)

    def _write_attested_png(self, root: Path, *, sha256: str | None = None, mode: int = 0o600,
                            extra_chunk: bytes = b"", trailing: bytes = b"", width: int = 8) -> Path:
        evidence = root / "evidence" / "ship-readiness"
        evidence.mkdir(parents=True, exist_ok=True)
        image = evidence / "after-restart-files.png"

        def chunk(kind: bytes, data: bytes) -> bytes:
            return len(data).to_bytes(4, "big") + kind + data + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")

        raw = (b"\0token=synthetic-secret" + b"\0" * (1 + width * 3))[:1 + width * 3]
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", width.to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x02\0\0\0")
        png += extra_chunk + chunk(b"IDAT", zlib.compress(raw, level=0)) + chunk(b"IEND", b"") + trailing
        image.write_bytes(png)
        os.chmod(image, mode)
        payload = {
            "schema": 1, "policy_version": 1, "run_id": root.name,
            "relative_path": "evidence/ship-readiness/after-restart-files.png",
            "sha256": sha256 or hashlib.sha256(png).hexdigest(), "width": width, "height": 1,
            "route": "/dashboard", "state": "post-claim-complete",
            "captured_at": "2026-07-29T12:00:00.000Z", "secret_exposure": False,
        }
        HARNESS.json_dump(image.with_name(image.name + ".safety.json"), payload)
        return image

    def test_retained_run_audit_accepts_bound_attested_png_without_scanning_idat_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-screenshot"
            root.mkdir()
            image = self._write_attested_png(root)
            self.assertIn(b"token=synthetic-secret", image.read_bytes())
            output = root / "audit.json"
            HARNESS.audit_retained_run(root, [], output)
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["passed"])

    def test_retained_run_audit_rejects_invalid_screenshot_evidence(self):
        cases = {
            "unattested": lambda root, image: image.with_name(image.name + ".safety.json").unlink(),
            "digest-mismatch": lambda root, image: HARNESS.json_dump(image.with_name(image.name + ".safety.json"), {
                **json.loads(image.with_name(image.name + ".safety.json").read_text(encoding="utf-8")), "sha256": "0" * 64,
            }),
            "text-chunk": lambda root, image: self._write_attested_png(
                root, extra_chunk=(4).to_bytes(4, "big") + b"tEXt" + b"note" + (zlib.crc32(b"tEXtnote") & 0xFFFFFFFF).to_bytes(4, "big"),
            ),
            "polyglot": lambda root, image: self._write_attested_png(root, trailing=b"not-png"),
            "unsafe-permissions": lambda root, image: os.chmod(image, 0o644),
            "oversized": lambda root, image: self._write_attested_png(root, width=HARNESS._SCREENSHOT_MAX_DIMENSION + 1),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "ship-screenshot"
                root.mkdir()
                image = self._write_attested_png(root)
                mutate(root, image)
                output = root / "audit.json"
                with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                    HARNESS.audit_retained_run(root, [], output)
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertIn("screenshot-attestation-invalid", json.dumps(report))
                self.assertEqual(0, image.stat().st_mode & 0o777)

    def _write_screenshot_publication(self, root: Path, image: Path, *, mutate=None) -> None:
        sidecar = image.with_name(image.name + ".safety.json")
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        record = {
            "schema": 1, "policy_version": 1, "run_id": root.name,
            "relative_path": "evidence/ship-readiness/after-restart-files.png",
            "source_backend": "wsl-private-posix",
            "source_privacy": {"mode": "0600", "owner_uid": os.geteuid(), "hardlinks": 1, "regular": True},
            "source_sha256": digest, "destination_sha256": digest,
            "attestation_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(), "published_safe": True,
        }
        if mutate:
            mutate(record)
        HARNESS.json_dump(image.with_name(image.name + ".publication.json"), record)

    def test_retained_run_audit_accepts_drvfs_private_source_publication_and_posix_direct(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-screenshot"
            root.mkdir()
            image = self._write_attested_png(root)
            self._write_screenshot_publication(root, image)
            with mock.patch.object(HARNESS, "_is_drvfs_evidence_root", return_value=True):
                HARNESS.audit_retained_run(root, [], root / "audit.json")
            self.assertTrue(image.is_file())
            self.assertTrue((root / "audit.json").is_file())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-screenshot"
            root.mkdir()
            self._write_attested_png(root)
            HARNESS.audit_retained_run(root, [], root / "audit.json")

    def test_retained_run_audit_rejects_drvfs_direct_or_forged_screenshot_publication(self):
        mutations = {
            "direct": None,
            "source-mode": lambda record: record["source_privacy"].update(mode="0644"),
            "source-owner": lambda record: record["source_privacy"].update(owner_uid=os.geteuid() + 1),
            "source-link": lambda record: record["source_privacy"].update(hardlinks=2),
            "missing-source": lambda record: record.pop("source_sha256"),
            "hash": lambda record: record.update(destination_sha256="0" * 64),
            "run": lambda record: record.update(run_id="wrong-run"),
            "path": lambda record: record.update(relative_path="evidence/ship-readiness/after-files.png"),
            "forged": lambda record: record.update(source_backend="forged"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "ship-screenshot"
                root.mkdir()
                image = self._write_attested_png(root)
                if mutate:
                    self._write_screenshot_publication(root, image, mutate=mutate)
                with mock.patch.object(HARNESS, "_is_drvfs_evidence_root", return_value=True), self.assertRaisesRegex(ValueError, "retained run audit failed"):
                    HARNESS.audit_retained_run(root, [], root / "audit.json")

    def test_retained_run_audit_rejects_orphan_drvfs_publication_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-screenshot"
            root.mkdir()
            publication = root / "evidence" / "ship-readiness" / "after-restart-files.png.publication.json"
            publication.parent.mkdir(parents=True)
            HARNESS.json_dump(publication, {"forged": True})
            with mock.patch.object(HARNESS, "_is_drvfs_evidence_root", return_value=True), self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, [], root / "audit.json")

    def _private_log_source(self, root: Path, content: str, *, mode: int = 0o600) -> Path:
        private = root.parent / "private-logs"
        private.mkdir(mode=0o700)
        source = private / "seedsync.log"
        source.write_text(content, encoding="utf-8")
        os.chmod(source, mode)
        return source

    def test_private_log_publication_redacts_and_audits_drvfs_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-log"
            root.mkdir()
            source = self._private_log_source(
                root,
                "password=super-secret\nGET /?api_key=another-secret\n"
                "Authorization: Bearer header-secret\nssh://user:uri-secret@example.test/path\nremotepass\n",
            )
            record = HARNESS.publish_private_log_snapshot(source, root, root.name)
            published = (root / "logs" / "seedsync.log").read_text(encoding="utf-8")
            serialized = json.dumps(record, sort_keys=True)
            self.assertNotIn("super-secret", published + serialized)
            self.assertNotIn("another-secret", published + serialized)
            self.assertNotIn("header-secret", published + serialized)
            self.assertNotIn("uri-secret", published + serialized)
            self.assertNotIn("remotepass", published + serialized)
            self.assertEqual(set(HARNESS._LOG_REDACTION_CLASSES), set(record["redaction_pattern_classes"]))
            with mock.patch.object(HARNESS, "_is_drvfs_evidence_root", return_value=True):
                HARNESS.audit_retained_run(root, [], root / "audit.json", private_log_source=source)

    def test_retained_secret_hint_parses_uri_userinfo_without_confusing_host_ports(self):
        for value in (
            "sftp://user@host:1234/path",
            "sftp://user@[2001:db8::1]:1234/path",
            "sftp://user:<redacted>@host:1234/path",
            "sftp://user:<ReDaCtEd>@host:1234/path",
            "sftp://user:@host:1234/path",
            "sftp://user%3a%3credacted%3e@host:1234/path",
            "sftp://user@host:1234/path@artifact",
            "sftp://user@host:1234/path?note=@artifact",
            "sftp://one@host:1234/a sftp://two@[2001:db8::1]:1234/b",
        ):
            with self.subTest(value=value):
                self.assertFalse(HARNESS._retained_secret_hint(value))
        self.assertTrue(HARNESS._retained_secret_hint("sftp://user:secret@host:1234/path"))

    def test_retained_secret_hint_fails_closed_for_malformed_uri_like_userinfo(self):
        for value in (
            "sftp://user:synthetic-credential/raw@host:1234/path",
            "sftp://user:synthetic-credential space@host:1234/path",
            "sftp://user:synthetic-credential\ttab@host:1234/path",
            "sftp://user:synthetic-credential/raw\n@host:1234/path",
            "sftp://user:synthetic-credential/raw\r\n@host:1234/path",
            "sftp://user%3Asynthetic-credential@host:1234/path",
            "sftp://user:synthetic-credential@middle@host:1234/path",
            "sftp://user:synthetic-credential/sftp://safe@host:1234/path",
            "sftp://user:synthetic-credential sftp://safe@host:1234/path",
            "sftp://user:synthetic-credential/raw'@host:1234/path",
            'sftp://user:synthetic-credential/raw"@host:1234/path',
            "sftp://user:synthetic-credential/raw)@host:1234/path",
            "(sftp://user:synthetic-credential/raw@host:1234/path)",
            "endpoint=sftp://user%3Asynthetic-credential@host:1234/path",
            "'sftp://user:synthetic-credential/raw@host:1234/path'",
            "[sftp://user:synthetic-credential/raw@host:1234/path]",
        ):
            with self.subTest(value=value):
                self.assertTrue(HARNESS._retained_secret_hint(value))

    def test_retained_secret_hint_completes_for_no_userinfo_urls_and_scans_structural_boundaries(self):
        self.assertFalse(HARNESS._retained_secret_hint("https://example.invalid/path https://other.invalid/path"))
        self.assertFalse(HARNESS._retained_secret_hint("https://host:1234/path sftp://user@remote:1234/file"))
        self.assertFalse(HARNESS._retained_secret_hint("https://host/path\nsftp://user@remote:1234/file"))
        self.assertFalse(HARNESS._retained_secret_hint(
            "Warning at file:///app/node_modules/font.scss:5:8:\nSass @import is deprecated"
        ))
        self.assertFalse(HARNESS._retained_secret_hint(
            "More info: https://sass-lang.com/d/slash-div\nnode_modules/font.scss 4:11 @import"
        ))
        self.assertFalse(HARNESS._retained_secret_hint(
            "Warning at file:///app/font.scss:5:8:\n#14 31.76 5 | @import \"variables\";"
        ))
        self.assertTrue(HARNESS._retained_secret_hint(
            "More info: https://sass-lang.com/d/slash-div\nuser:review-secret@internal.example"
        ))
        self.assertTrue(HARNESS._retained_secret_hint(
            "file:///tmp/source.scss\nuser:review-secret@internal.example"
        ))
        self.assertTrue(HARNESS._retained_secret_hint(
            "https://host:443/path\nSass @import user:review-secret@internal.example"
        ))
        self.assertTrue(HARNESS._retained_secret_hint(
            "file:///tmp/source.scss\nuser:review-secret/raw@internal.example"
        ))
        self.assertTrue(HARNESS._retained_secret_hint(
            "https://host:443/path\nSass @import user:review-secret/raw@internal.example"
        ))
        for malformed in (
            "user:review-secret@internal.example:bad",
            "user:review-secret@internal_host",
            "user:review-secret@internal.example,",
        ):
            self.assertTrue(HARNESS._retained_secret_hint("file:///tmp/a\n" + malformed))
        self.assertFalse(HARNESS._retained_secret_hint("mailto:person@example.com"))
        self.assertFalse(HARNESS._retained_secret_hint(
            '{"resolved":"https://registry.npmjs.org/pkg/-/pkg-1.2.3.tgz","from":"pkg@1.2.3"}'
        ))
        self.assertFalse(HARNESS._retained_secret_hint(
            '{"source":"https://github.com/truedarkdev/seedsync","patch":"@@ -1 +1 @@"}'
        ))
        self.assertFalse(HARNESS._retained_secret_hint(
            "[TypeScript](https://typescriptlang.org/) install typescript@next"
        ))
        self.assertFalse(HARNESS._retained_secret_hint(
            "```\ngit clone https://github.com/example/project.git\n```\nnpm install project@next"
        ))
        self.assertTrue(HARNESS._retained_secret_hint("sftp://safe@host:1234/path,sftp://user:synthetic-credential@host:1234/path"))
        self.assertTrue(HARNESS._retained_secret_hint("sftp://user:synthetic-credential/sftp://safe@host:1234/path"))
        self.assertTrue(HARNESS._retained_secret_hint("https://host:non-numeric/sftp://safe@host:1234/path"))
        self.assertTrue(HARNESS._retained_secret_hint("https://host%3asynthetic/sftp://safe@host:1234/path"))
        self.assertTrue(HARNESS._retained_secret_hint("1sftp://user:synthetic-credential@host:1234/path"))
        self.assertFalse(HARNESS._retained_secret_hint("9HTTPS://user@host:1234/path"))

    def test_retained_secret_hint_scans_large_no_scheme_input_linearly(self):
        started = time.monotonic()
        self.assertFalse(HARNESS._retained_secret_hint("x" * (1024 * 1024)))
        self.assertLess(time.monotonic() - started, 3.0)

    def test_retained_secret_hint_scans_colon_dense_no_at_input_linearly(self):
        started = time.monotonic()
        self.assertFalse(HARNESS._retained_secret_hint("a:" * (512 * 1024)))
        self.assertLess(time.monotonic() - started, 3.0)

    def test_retained_secret_hint_scans_dense_sass_import_traces_linearly(self):
        for separator in ("\n", "\r"):
            with self.subTest(separator=repr(separator)):
                trace = "#14 31.76 Sass @import is deprecated" + separator
                value = "https://sass-lang.com/d/import" + separator + trace * ((1024 * 1024) // len(trace))
                started = time.monotonic()
                self.assertFalse(HARNESS._retained_secret_hint(value))
                self.assertLess(time.monotonic() - started, 3.0)

    def test_retained_secret_hint_scans_scheme_dense_no_at_input_bounded(self):
        started = time.monotonic()
        self.assertFalse(HARNESS._retained_secret_hint("x://" * (512 * 1024)))
        self.assertLess(time.monotonic() - started, 3.0)

    def test_retained_secret_hint_scans_scheme_dense_mixed_safe_input_bounded(self):
        pattern = "https://host:1234/path sftp://user@remote:1234/file "
        started = time.monotonic()
        self.assertFalse(HARNESS._retained_secret_hint(pattern * ((2 * 1024 * 1024) // len(pattern))))
        self.assertLess(time.monotonic() - started, 3.0)

    def test_retained_secret_detection_scans_chunked_large_file_and_boundary_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "large.log"
            path.write_text("x" * (4096 - 8) + "password=synthetic-value" + "x" * (2 * 1024 * 1024), encoding="utf-8")
            started = time.monotonic()
            self.assertTrue(HARNESS._retained_secret_detected(path))
            self.assertLess(time.monotonic() - started, 3.0)

    def test_retained_secret_detection_preserves_safe_uri_context_in_bounded_direct_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "npm-inventory.json"
            prefix = "x" * (70 * 1024)
            safe_metadata = (
                '{"resolved":"https://registry.npmjs.org/package/-/'
                + "x" * 650
                + '","from":"package@1.2.3"}'
            )
            path.write_text(prefix + safe_metadata, encoding="utf-8")
            self.assertGreater(path.stat().st_size, 64 * 1024)
            self.assertLessEqual(path.stat().st_size, HARNESS._RETAINED_DIRECT_SCAN_MAX_BYTES)
            self.assertFalse(HARNESS._retained_secret_detected(path))

    def test_retained_secret_detection_keeps_uri_token_splits_within_overlap(self):
        for label, token in (("scheme", "sftp://user:synthetic@host"), ("encoded", "sftp://user%3Asynthetic@host"), ("at", "sftp://user:synthetic@host")):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                split = token.index("://") + 1 if label == "scheme" else token.index("%3A") + 1 if label == "encoded" else token.index("@")
                path = Path(temporary) / "split.log"
                path.write_text("x" * (4096 - split) + token, encoding="utf-8")
                self.assertTrue(HARNESS._retained_secret_detected(path))

    def test_private_log_publication_ignores_path_and_query_at_after_safe_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-log"
            root.mkdir()
            source = self._private_log_source(root, "sftp://user@host:1234/path@artifact\nsftp://two@[2001:db8::1]:1234/b?note=@artifact\n")
            record = HARNESS.publish_private_log_snapshot(source, root, root.name)
            self.assertEqual("logs/seedsync.log", record["relative_path"])

    def test_private_log_publication_rejects_nested_scheme_before_malformed_final_at(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-log"
            root.mkdir()
            source = self._private_log_source(root, "sftp://user:synthetic-credential/sftp://safe@host:1234/path\n")
            with self.assertRaisesRegex(ValueError, "redacted log still contains a secret pattern"):
                HARNESS.publish_private_log_snapshot(source, root, root.name)
            self.assertFalse((root / "logs" / "seedsync.log").exists())

    def test_private_log_publication_handles_no_at_and_structural_uri_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-log"
            root.mkdir()
            source = self._private_log_source(root, "https://example.invalid/path https://other.invalid/path\n")
            HARNESS.publish_private_log_snapshot(source, root, root.name)
            source.write_text("(sftp://user:synthetic-credential/raw@host:1234/path)\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "redacted log still contains a secret pattern"):
                HARNESS.publish_private_log_snapshot(source, root, root.name)

    def test_private_log_publication_accepts_redacted_username_only_uri_and_rejects_credential_uri(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-log"
            root.mkdir()
            source = self._private_log_source(root, "sftp://remoteuser@host:1234/path\nsftp://remoteuser@[2001:db8::1]:1234/path\n")
            record = HARNESS.publish_private_log_snapshot(source, root, root.name)
            self.assertFalse(HARNESS._retained_secret_hint((root / "logs" / "seedsync.log").read_text(encoding="utf-8")))
            self.assertEqual("logs/seedsync.log", record["relative_path"])
            unsafe = source
            unsafe.write_text("sftp://user:uri-secret@host:1234/path\n", encoding="utf-8")
            published = HARNESS.publish_private_log_snapshot(unsafe, root, root.name)
            self.assertNotIn("uri-secret", (root / "logs" / "seedsync.log").read_text(encoding="utf-8"))
            self.assertEqual("logs/seedsync.log", published["relative_path"])
            unsafe.write_text("sftp://user:synthetic-credential/raw@host:1234/path\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "redacted log still contains a secret pattern"):
                HARNESS.publish_private_log_snapshot(unsafe, root, root.name)

    def test_private_log_publication_retries_torn_append_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-log"
            root.mkdir()
            source = self._private_log_source(root, "password=first-secret\n")
            original = Path.read_bytes
            reads = 0

            def append_during_first_read(path: Path) -> bytes:
                nonlocal reads
                payload = original(path)
                if path == source and reads == 0:
                    reads += 1
                    source.write_text("password=second-secret\n", encoding="utf-8")
                    os.chmod(source, 0o600)
                return payload

            with mock.patch.object(Path, "read_bytes", autospec=True, side_effect=append_during_first_read):
                record = HARNESS.publish_private_log_snapshot(source, root, root.name)
            self.assertEqual(1, reads)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), record["source_snapshot_sha256"])
            self.assertNotIn("second-secret", (root / "logs" / "seedsync.log").read_text(encoding="utf-8"))

    def test_private_log_publication_rejects_unsafe_source_and_drvfs_forgery(self):
        for label, mutate in {
            "mode": lambda source: os.chmod(source, 0o644),
            "owner": lambda source: mock.patch.object(HARNESS.os, "geteuid", return_value=os.geteuid() + 1),
            "link": lambda source: (source.unlink(), source.symlink_to(source.parent / "target.log")),
        }.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "ship-log"
                root.mkdir()
                source = self._private_log_source(root, "password=secret\n")
                if label == "owner":
                    with mutate(source), self.assertRaises(ValueError):
                        HARNESS.publish_private_log_snapshot(source, root, root.name)
                else:
                    mutate(source)
                    with self.assertRaises(ValueError):
                        HARNESS.publish_private_log_snapshot(source, root, root.name)

        for label, mutate in {
            "direct": None,
            "hash": lambda record: record.update(published_sha256="0" * 64),
            "source-hash": lambda record: record.update(source_snapshot_sha256="0" * 64),
            "forged": lambda record: record.update(source_backend="forged"),
            "missing-source": lambda record: record.pop("source_snapshot_sha256"),
        }.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "ship-log"
                root.mkdir()
                source = self._private_log_source(root, "password=secret\n")
                if mutate:
                    HARNESS.publish_private_log_snapshot(source, root, root.name)
                    record_path = root / "logs" / "seedsync.log.publication.json"
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    mutate(record)
                    HARNESS.json_dump(record_path, record)
                else:
                    log = root / "logs" / "seedsync.log"
                    log.parent.mkdir()
                    log.write_text("password=<redacted>\n", encoding="utf-8")
                with mock.patch.object(HARNESS, "_is_drvfs_evidence_root", return_value=True), self.assertRaisesRegex(ValueError, "retained run audit failed"):
                    HARNESS.audit_retained_run(root, [], root / "audit.json", private_log_source=source)

    def test_retained_run_audit_accepts_true_posix_redacted_log_and_rejects_orphan_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-log"
            root.mkdir()
            log = root / "logs" / "seedsync.log"
            log.parent.mkdir()
            log.write_text("password=<redacted>\n", encoding="utf-8")
            HARNESS.audit_retained_run(root, [], root / "audit.json")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ship-log"
            root.mkdir()
            record = root / "logs" / "seedsync.log.publication.json"
            record.parent.mkdir()
            HARNESS.json_dump(record, {"forged": True})
            with mock.patch.object(HARNESS, "_is_drvfs_evidence_root", return_value=True), self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, [], root / "audit.json")

    def test_failure_status_artifacts_are_minimal_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = root / "audit-failure.json"
            browser = root / "browser-failure.json"
            HARNESS.audit_failure_status(audit, "timeout", 124)
            HARNESS.browser_command_failure(browser, "before-legacy-browser-launch", "legacy", 1, False)
            self.assertEqual("not-satisfied", json.loads(audit.read_text(encoding="utf-8"))["security_acceptance"])
            payload = json.loads(browser.read_text(encoding="utf-8"))
            self.assertEqual("failed", payload["status"])
            self.assertFalse(payload["timed_out"])

    def test_retained_run_audit_keeps_detection_history_after_remediation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            leaked = root / "leaked.json"
            leaked.write_text('{"api_key":"synthetic-secret"}', encoding="utf-8")
            output = root / "audit.json"
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, [], output)
            first = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(first["passed"])
            self.assertIn({"kind": "secret-pattern-remediated", "path": "leaked.json"}, first["cumulative_findings"])
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, [], output)
            second = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(second["passed"])
            self.assertTrue(second["current_passed"])
            self.assertIn({"kind": "secret-pattern-remediated", "path": "leaked.json"}, second["cumulative_findings"])
            self.assertNotIn("synthetic-secret", json.dumps(second))

    def test_retained_run_audit_rejects_untrusted_historical_finding_without_reflecting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "retained-run-audit.json"
            secret = "historical-audit-secret"
            malicious_finding = {"kind": f"unknown-kind-{secret}", "path": f"query?token={secret}"}
            output.write_text(json.dumps({
                "schema": 3, "passed": False, "current_passed": False, "scanned_files": 0,
                "protected_roots": [], "findings": [malicious_finding], "current_findings": [malicious_finding],
                "cumulative_findings": [malicious_finding],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, [], output)
            report = json.loads(output.read_text(encoding="utf-8"))
            rendered = json.dumps(report)
            self.assertIn({"kind": "prior-audit-invalid", "path": "retained-run-audit.json"}, report["cumulative_findings"])
            self.assertNotIn(secret, rendered)

    def test_retained_run_audit_rejects_partial_or_malformed_historical_schema_without_reflection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "retained-run-audit.json"
            secret = "partial-history-secret"
            output.write_text(json.dumps({"schema": 3, "cumulative_findings": f"token={secret}"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, [], output)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn({"kind": "prior-audit-invalid", "path": "retained-run-audit.json"}, report["cumulative_findings"])
            self.assertNotIn(secret, json.dumps(report))

    def test_retained_run_audit_rejects_non_canary_secret_forms(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secrets = {
                "json.txt": '{"credential":"json-secret"}',
                "query.txt": "https://example.invalid/?api_key=query-secret",
                "headers.txt": "Set-Cookie: session=cookie-secret\nAuthorization: Bearer header-secret",
                "uri.txt": "sftp://seed:uri-secret@example.invalid/path",
            }
            for name, value in secrets.items():
                (root / name).write_text(value, encoding="utf-8")
            output = root / "audit.json"
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, [], output)
            report = json.dumps(json.loads(output.read_text(encoding="utf-8")))
            self.assertIn("secret-pattern", report)
            for value in secrets.values():
                self.assertNotIn(value, report)
            for name, value in secrets.items():
                artifact = root / name
                self.assertNotIn(value, artifact.read_text(encoding="utf-8"))
                self.assertEqual(0o600, artifact.stat().st_mode & 0o777)

    def test_retained_run_audit_quarantines_unredactable_canaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            leaked = root / "opaque.bin"
            leaked.write_text("opaque-canary", encoding="utf-8")
            output = root / "audit.json"
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, ["opaque-canary"], output)
            self.assertEqual(0, leaked.stat().st_mode & 0o777)
            self.assertNotIn("opaque-canary", output.read_text(encoding="utf-8"))

    def test_retained_run_audit_rejects_profile_and_raw_status_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "browser-profile").mkdir()
            (root / "migration-required.json").write_text('{"action":{"csrf_token":"hidden"}}', encoding="utf-8")
            output = root / "audit.json"
            with self.assertRaises(ValueError):
                HARNESS.audit_retained_run(root, [], output)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertNotIn("hidden", json.dumps(report))

    def test_retained_run_audit_rejects_obsolete_host_protected_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protected = root / "protected"
            protected.mkdir()
            (protected / "config-snapshot.tar").write_text("injected-canary", encoding="utf-8")
            output = root / "audit.json"
            with self.assertRaisesRegex(ValueError, "retained run audit failed"):
                HARNESS.audit_retained_run(root, ["injected-canary"], output)
            self.assertIn("obsolete-protected-host-artifact", json.dumps(json.loads(output.read_text(encoding="utf-8"))))
            manifest = root / "protected-artifacts.json"
            HARNESS.json_dump(manifest, {"schema": 1, "protected_artifacts": []})
            with self.assertRaisesRegex(ValueError, "exemptions are no longer permitted"):
                HARNESS.audit_retained_run(root, [], output, classification_manifest=manifest)

    def _current_contract_files(self, root, *, state="downloaded", pair_id=None, persisted=None):
        settings = root / "settings.cfg"
        settings.write_text("[Lftp]\nremote_path = /remote\nlocal_path = /downloads\n[AutoQueue]\nenabled = true\npatterns_only = true\nauto_extract = true\n", encoding="utf-8")
        pair_id = pair_id or str(uuid.uuid5(uuid.NAMESPACE_URL, "seedsync:v086:/remote\n/downloads"))
        (root / "pairs.json").write_text(json.dumps({"path_pairs": [{"id": pair_id, "name": "Default", "remote_path": "/remote", "local_path": "/downloads", "enabled": True, "auto_queue": True}]}), encoding="utf-8")
        browser = {"model": [{"name": "matched.bin", "state": state, "path_pair_id": pair_id, "path_pair_name": "Default"}], "autoqueuePatterns": ["match"]}
        (root / "browser.json").write_text(json.dumps(browser), encoding="utf-8")
        (root / "fixture.json").write_text(json.dumps({"case_index": [{"name": "matched.bin", "backend_state": "downloaded", "autoqueue": "substring"}]}), encoding="utf-8")
        (root / "autoqueue.persist").write_text(json.dumps({"patterns": [json.dumps({"pattern": "match"})]}), encoding="utf-8")
        (root / "controller.persist").write_text(json.dumps(persisted if persisted is not None else {"downloaded": ["matched.bin"], "extracted": []}), encoding="utf-8")
        return settings

    def test_current_model_rejects_wrong_state_or_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = self._current_contract_files(root, state="default")
            with self.assertRaises(ValueError):
                HARNESS.assert_current_model(settings, root / "pairs.json", root / "browser.json", root / "fixture.json", root / "out.json")
            settings = self._current_contract_files(root, pair_id="wrong")
            with self.assertRaises(ValueError):
                HARNESS.assert_current_model(settings, root / "pairs.json", root / "browser.json", root / "fixture.json", root / "out.json")

    def test_autoqueue_rejects_wrong_outcome_or_persistence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = self._current_contract_files(root, state="default")
            with self.assertRaises(ValueError):
                HARNESS.assert_autoqueue(settings, root / "autoqueue.persist", root / "browser.json", root / "fixture.json", root / "controller.persist", root / "out.json")

    def test_autoqueue_contract_uses_the_claim_browser_api_and_model_evidence(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        invocation = next(line for line in launcher.splitlines() if "assert-autoqueue" in line)
        self.assertIn("--browser /evidence/ship-readiness/browser.json", invocation)
        self.assertNotIn("--browser /evidence/ship-readiness/browser-reuse.json", invocation)

    def test_focused_angular_lane_uses_the_playwright_chromium_executable(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        self.assertIn("playwright_chromium_binary()", launcher)
        self.assertIn("chromium.executablePath()", launcher)
        invocation = next(line for line in launcher.splitlines() if "focused-angular-tests" in line and "bounded_command" in line)
        self.assertIn('env CHROME_BIN="$chrome_bin"', invocation)
        self.assertIn("--include src/app/tests/unittests/services/files/view-file.service.spec.ts", launcher)
        self.assertIn("--include src/app/tests/unittests/pages/files/file-list.component.spec.ts", launcher)

    def test_failure_summary_records_unproven_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "matrix.json"
            HARNESS.json_dump(matrix, HARNESS.matrix_payload("failed-run"))
            summary, failures = root / "summary.json", root / "failures.json"
            HARNESS.summarize(matrix, summary, failures, "failed", "browser launch failed")
            self.assertEqual("failed", json.loads(summary.read_text(encoding="utf-8"))["outcome"])
            self.assertEqual(len(HARNESS.MATRIX), len(json.loads(failures.read_text(encoding="utf-8"))["failed_or_unproven"]))
            settings = self._current_contract_files(root, persisted={"downloaded": "bad", "extracted": []})
            with self.assertRaises(ValueError):
                HARNESS.assert_autoqueue(settings, root / "autoqueue.persist", root / "browser.json", root / "fixture.json", root / "controller.persist", root / "out.json")

    def test_browser_navigation_uses_explicit_readiness_and_failure_evidence(self):
        source = BROWSER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("network" + "idle", source)
        self.assertIn("waitUntil: 'domcontentloaded'", source)
        self.assertIn("if (claimMode && recoveryHandoverMode)", source)
        self.assertIn("await classifyStandaloneBootstrap()", source)
        self.assertIn("const initialPaths = legacyMode ? ['/dashboard'] : ['/'];", source)
        self.assertIn("mode === 'legacy-restore'", source)
        self.assertIn("acceptedPaths.includes(final.pathname)", source)
        for policy_marker in ("navigateReady", "requireFixtureRows", "requireApi", "captureFailure", "bodySnippet", "consoleAndPageErrors"):
            self.assertIn(policy_marker, source)
        self.assertIn("chromium.launchPersistentContext(temporaryProfileDir, { headless: true })", source)
        self.assertIn("browser-claim-ready.json", source)
        self.assertIn("browser-restart-request.json", source)
        self.assertIn("SEEDSYNC_BROWSER_PROFILE_DIR", source)
        self.assertIn("closeWithinDeadline", source)
        self.assertIn("handleTermination", source)
        self.assertIn("process.once('SIGTERM'", source)
        self.assertIn(".secret-value", source)
        self.assertIn("node.getAttribute('aria-label')", source)
        self.assertIn("node.className", source)
        self.assertIn("node.labels", source)

    def test_launcher_bounds_migration_commands_and_retains_timeout_diagnostics(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        for policy_marker in (
            "bounded_command()", "timeout --foreground", "capture_command_diagnostics()",
            "migration-stop-legacy", "migration-current-image-build", "${2}-diagnostics.txt",
        ):
            self.assertIn(policy_marker, source)

    def test_failure_paths_audit_retained_evidence_after_redaction(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        self.assertIn('audit_retained_run "$id" || true', source)
        self.assertIn('audit-retained-run --root "$output_dir"', source)
        self.assertIn('SEEDSYNC_SHIP_AUDIT_TIMEOUT_SECONDS', source)
        self.assertIn('retained-run-audit-failure.json', source)
        self.assertIn('audit-failure-status', source)
        self.assertIn('retained-run-audit-diagnostics.txt', source)
        self.assertIn('first_recorded_failure_detail()', source)
        self.assertIn('recorded_detail="$(first_recorded_failure_detail "$id"', source)

    def test_legacy_browser_transition_is_phased_bounded_and_retains_pre_mjs_failure_evidence(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            'before-legacy-http-wait', 'before-legacy-browser-launch', 'before-legacy-browser-assert',
            'run_browser_bounded', 'SEEDSYNC_SHIP_BROWSER_TIMEOUT_SECONDS',
            'browser-${mode}-command-diagnostics.txt', 'browser-command-failure', 'SEEDSYNC_BROWSER_PROFILE_DIR="$profile_dir"',
        ):
            self.assertIn(marker, source)

    def test_restored_legacy_browser_uses_bounded_private_profile_launcher(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        self.assertIn(
            'run_browser_bounded "$id" restore-legacy-browser-launch "http://127.0.0.1:${legacy_port}" "$(evidence_dir "$id")" legacy-restore',
            source,
        )
        self.assertNotIn('run_browser "$id"', source)

    def test_launcher_exposes_current_runtime_from_browser_network_and_retains_http_failures(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        for policy_marker in (
            'browser_network="seedsync-upgrade-v086-browser-${1,,}"', "capture_http_diagnostics()",
            'docker run -d --name "$proxy" --network "$browser_network" -p "127.0.0.1:${port}:8800"',
            'docker network connect "$network" "$proxy"', '--network-alias current',
            "capture_http_diagnostics()", "migration-current-status", "seedsync-http", "seedsync-migration-status",
        ):
            self.assertIn(policy_marker, source)

    def test_launcher_hardens_ports_proxies_and_long_running_lanes(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        for policy_marker in (
            "validate_port()", "canonical decimal TCP port", "run_lab_bootstrap_bounded()",
            "run_lab_bounded()", "focused-migration-tests", "focused-angular-tests",
            "current-proxy-nginx.conf", "--network-alias current", "current_proxy",
            "assert_current_topology()", "capture_current_provenance()", "action']['csrf_token",
            "operation', {}).get('status')", "bounded_command_self_check", "port_validation_self_check",
            "migration-stop-legacy-proxy", "legacy_proxy", "persist_failure(error)",
            "topology_and_apply_contract_self_check", "current-topology-failure.json",
            "def error_summary(error)", "wait_migration_status()", "migration-status-evidence",
        ):
            self.assertIn(policy_marker, source)
        self.assertNotIn("ev" + "al \"$command\"", source)

    def test_named_volume_is_the_only_live_config_storage_and_protected_artifacts_are_classified(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        lab = MODULE_PATH.with_name("lab.sh").read_text(encoding="utf-8")
        compose = MODULE_PATH.with_name("compose.yml").read_text(encoding="utf-8")
        for marker in (
            "config_volume()", "type=volume,src=$(config_volume", "capture_volume_inventory()",
            "snapshot_volume_config()", "preflight_volume_private_storage()",
            "validator_container()", "verify_validator()", "validate-archive",
        ):
            self.assertIn(marker, launcher)
        self.assertIn("CONFIG_VOLUME=\"$(config_volume_name \"$id\")\"", lab)
        self.assertIn("RUN_ID must be lowercase", lab)
        self.assertIn("retained config volume already exists", lab)
        self.assertIn("verify_config_volume", lab)
        self.assertIn("verify_validator_container", lab)
        self.assertIn("--network none --read-only --user 1000:1000", lab)
        self.assertIn("dst=/config,readonly", lab)
        self.assertIn("type: volume", compose)
        self.assertIn("source: upgrade_config", compose)
        self.assertNotIn("${RUN_DIR}/config", compose)
        self.assertNotIn("protected-artifacts.json", lab)

    def test_protected_snapshots_stay_in_a_labeled_posix_volume(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        lab = MODULE_PATH.with_name("lab.sh").read_text(encoding="utf-8")
        readme = MODULE_PATH.with_name("README.md").read_text(encoding="utf-8")
        for marker in (
            "protected_volume()", "snapshotter_container()", "verify_protected_volume()",
            "verify_snapshotter()", "docker exec \"$snapshotter\"", "/protected/${label}.tar",
            'validate-archive --archive "/protected/${label}.tar"',
            '"storage": "docker-named-volume"', '"archive_mode": "0600"',
            '"validator_access": "read-only postvalidated without extraction"',
            'dst=/protected,readonly', 'after-current-restart.tar',
            "bind-archive-inventory", "verify-protected-archive", "verify_snapshot_for_consumer()",
            "validator_evidence_path()", "/evidence/ship-readiness/",
            "archive inventory input is missing or empty", "archive consumer inventory is missing or empty",
        ):
            self.assertIn(marker, launcher)
        for marker in (
            "protected_volume_name()", "snapshotter_container_name()",
            "seedsync.upgrade-v086.role=protected-artifacts",
            "seedsync.upgrade-v086.role=snapshotter",
            "dst=/protected,readonly", "writable protected storage",
            "protected-storage-self-check", "tar -xOf /protected/probe.tar source",
            "read-only evidence mount", "validator-evidence-path-self-check",
        ):
            self.assertIn(marker, lab)
        self.assertIn("contains only a safe storage manifest", readme)

    def test_restore_snapshot_binds_to_fresh_stopped_runtime_inventory(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        stopped = 'stop_container "$id" restore-current-stop restore-current-stop'
        inventory = 'capture_volume_inventory "$id" after-current-restart-config'
        snapshot = 'snapshot_volume_config "$id" after-current-restart after-current-restart-config'
        consumer = 'verify_snapshot_for_consumer "$id" after-current-restart after-current-restart-config'
        for marker in (stopped, inventory, snapshot, consumer):
            self.assertIn(marker, launcher)
        self.assertLess(launcher.index(stopped), launcher.index(inventory))
        self.assertLess(launcher.index(inventory), launcher.index(snapshot))
        self.assertLess(launcher.index(snapshot), launcher.index(consumer))
        self.assertNotIn('snapshot_volume_config "$id" after-current-restart after-config', launcher)

    def test_after_restore_archive_uses_full_stopped_inventory_without_weakening_legacy_compare(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        proxy_stop = 'stop_container "$id" restore-legacy-proxy-stop restore-legacy-proxy-stop'
        downloads_restore = 'restore_downloads_baseline "$id"'
        filtered = 'capture_volume_inventory "$id" restore-config --legacy-config'
        reboot = 'run_lab_bounded "$id" "$legacy_port" restore-legacy-start'
        full = 'capture_volume_inventory "$id" after-restore-config-full'
        snapshot = 'snapshot_volume_config "$id" after-restore-config after-restore-config-full'
        compare = 'capture_volume_helper_output "$id" "$(evidence_dir "$id")/restore-config-compare.json" assert-restore'
        expected = '--config-root /config --expected "$(validator_evidence_path before-config.json)"'
        for marker in (proxy_stop, downloads_restore, filtered, reboot, full, snapshot, compare, expected):
            self.assertIn(marker, launcher)
        self.assertLess(launcher.index(downloads_restore), launcher.index(filtered))
        self.assertLess(launcher.index(filtered), launcher.index(compare))
        self.assertLess(launcher.index(compare), launcher.index(reboot))
        self.assertLess(launcher.index(proxy_stop), launcher.index(full))
        self.assertLess(launcher.index(full), launcher.index(snapshot))
        self.assertNotIn('snapshot_volume_config "$id" after-restore-config restore-config', launcher)

    def test_downloads_restore_uses_private_archive_staging_and_exact_baseline_equality(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        lab = MODULE_PATH.with_name("lab.sh").read_text(encoding="utf-8")
        snapshot_stop = 'stop_container "$id" migration-stop-legacy-proxy migration-stop-legacy-proxy'
        before_inventory = 'capture_inventory "$id" before-downloads "$(run_dir "$id")/downloads"'
        snapshot = 'snapshot_downloads_baseline "$id"'
        config_restore = 'row "$id" restore-offline passed "evidence/ship-readiness/restore.log"'
        restore = 'restore_downloads_baseline "$id"'
        reboot = 'run_lab_bounded "$id" "$legacy_port" restore-legacy-start'
        for marker in (snapshot_stop, before_inventory, snapshot, config_restore, restore, reboot):
            self.assertIn(marker, launcher)
        self.assertLess(launcher.index(snapshot_stop), launcher.index(before_inventory))
        self.assertLess(launcher.index(before_inventory), launcher.index(snapshot))
        self.assertLess(launcher.index(config_restore), launcher.index(restore))
        self.assertLess(launcher.index(restore), launcher.index(reboot))
        restore_body = launcher[launcher.index("restore_downloads_baseline() {"):launcher.index("snapshot_downloads_tree() {")]
        self.assertLess(restore_body.index('assert_download_restore_runtimes_stopped "$id"'),
                        restore_body.index('create_downloads_restorer "$id"'))
        self.assertLess(restore_body.index('create_downloads_restorer "$id"'),
                        restore_body.index('verify_downloads_baseline_for_restore "$id"'))
        self.assertLess(restore_body.index('snapshot_downloads_tree "$id" after-current-downloads after-current-downloads'),
                        restore_body.index('verify_downloads_baseline_for_restore "$id"'))
        self.assertLess(restore_body.index('verify_downloads_baseline_for_restore "$id"'),
                        restore_body.index('tar -C "$stage" -xpf /protected/before-downloads.tar'))
        self.assertLess(restore_body.index('restore-downloads-staging-compare.json'),
                        restore_body.index('guarded_downloads_baseline_replacement "$id"'))
        self.assertLess(restore_body.index('verify_downloads_recovery_for_restore "$id"'),
                        restore_body.index('verify_downloads_baseline_for_restore "$id"'))
        for marker in (
            'before-downloads.tar', 'before-downloads-restore-consumer-verification.json',
            'assert_download_restore_runtimes_stopped', 'restore-downloads-staging-compare.json',
            'restore-downloads-compare.json', 'after-current-downloads', 'snapshot_downloads_tree',
            'verify_downloads_baseline_for_restore', 'find /downloads -mindepth 1 -maxdepth 1',
            'legacy reboot is blocked', 'validate-archive --archive /protected/before-downloads.tar',
            'bind-archive-inventory --archive /protected/before-downloads.tar',
            'verify_downloads_recovery_for_restore', 'after-current-downloads-replacement-consumer-verification.json',
            'restore-downloads-in-progress.json', 'guarded_downloads_baseline_replacement',
            'rollback_downloads_after_failed_replacement', 'restore_downloads_recovery_tree',
            'rollback-downloads-staging-compare.json', 'rollback-downloads-compare.json',
            'rollback-complete-verifier-blocked', 'prior interrupted restore marker',
        ):
            self.assertIn(marker, launcher)
        for marker in (
            'downloads_snapshotter_container_name()', 'downloads_restorer_container_name()',
            'seedsync.upgrade-v086.role=downloads-snapshotter',
            'seedsync.upgrade-v086.role=downloads-restorer', '--network none --read-only --user 1000:1000',
            'exactly two mounts', 'exact downloads source', 'downloads mount access',
            'verify-downloads-snapshotter', 'verify-downloads-restorer', 'check-run-tree',
            'create-downloads-restorer', 'create_downloads_restorer_container()',
        ):
            self.assertIn(marker, lab)
        create_run = lab[lab.index("create_run() {"):lab.index("preflight() {")]
        self.assertNotIn('downloads_restorer="', create_run)
        self.assertNotIn('docker start "$downloads_restorer"', create_run)
        self.assertIn('restore-current-proxy-stop', launcher)

    def test_existing_downloads_restorer_is_reused_only_after_full_isolation_verification(self):
        lab = MODULE_PATH.with_name("lab.sh").read_text(encoding="utf-8")
        body = lab[lab.index("create_downloads_restorer_container() {"):lab.index("validate_host_port() {")]
        self.assertIn('if docker container inspect "$name" >/dev/null 2>&1; then', body)
        self.assertIn('verify_downloads_restorer_container "$id" true || die "existing downloads restorer failed its exact isolation contract"', body)
        self.assertIn('docker start "$name" >/dev/null || die "unable to restart verified downloads restorer"', body)
        self.assertIn('verify_downloads_restorer_container "$id" || die "restarted downloads restorer failed its exact isolation contract"', body)
        self.assertIn('return 0', body)
        self.assertLess(body.index('docker container inspect "$name"'), body.index('docker create --name "$name"'))

    def test_downloads_restorer_accepts_only_exact_or_proven_docker_desktop_bind_source(self):
        lab = MODULE_PATH.with_name("lab.sh").read_text(encoding="utf-8")
        body = lab[lab.index("verify_downloads_helper_container() {"):lab.index("validate_host_port() {")]
        for marker in (
            'downloads_source_exact =', 'docker_desktop_wsl_proxy =',
            '/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/',
            'verify_downloads_snapshotter_container "$id"',
            "stat -c '%d:%i' /downloads", '[[ "$restorer_identity" == "$snapshot_identity" ]]',
            'downloads restorer bind does not reference the exact snapshotter source',
        ):
            self.assertIn(marker, body)

    def test_downloads_restore_guard_covers_marker_replacement_compare_and_signal_rollback(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        guard = launcher[launcher.index("guarded_downloads_baseline_replacement() ("):launcher.index("assert_download_restore_runtimes_stopped() {")]
        for marker in (
            "trap 'status=$?; rollback", "baseline replacement interrupted by HUP",
            "baseline replacement interrupted by INT", "baseline replacement interrupted by TERM",
            'write_downloads_restore_marker "$id" replacement-in-progress',
            'find /downloads -mindepth 1 -maxdepth 1', 'capture_inventory "$id" restore-downloads',
            'restore-downloads-compare.json', 'rm -f -- "$(downloads_restore_marker "$id")"',
        ):
            self.assertIn(marker, guard)
        self.assertLess(guard.index('write_downloads_restore_marker "$id" replacement-in-progress'),
                        guard.index('find /downloads -mindepth 1 -maxdepth 1'))
        self.assertLess(guard.index('find /downloads -mindepth 1 -maxdepth 1'),
                        guard.index('restore-downloads-compare.json'))
        self.assertLess(guard.index('restore-downloads-compare.json'),
                        guard.index('rm -f -- "$(downloads_restore_marker "$id")"'))

    def test_retained_downloads_restore_marker_recovers_before_legacy_boot(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        gate = launcher[launcher.index("recover_marked_downloads_before_legacy_start() {"):launcher.index("restore_downloads_baseline() {")]
        full = launcher[launcher.index("full() {"):launcher.index('  run_lab_bounded "$id" "$legacy_port" before-legacy-start')]
        for marker in (
            'assert_download_restore_runtimes_stopped "$id"', 'verify_protected_volume "$id"',
            'create_downloads_restorer "$id"', 'rollback_downloads_after_failed_replacement',
            'rollback evidence blocks this verifier invocation before legacy start',
        ):
            self.assertIn(marker, gate)
        for marker in ('downloads restore requires labelled network identity', 'downloads restore requires exact lab-only'):
            self.assertIn(marker, launcher)
        self.assertIn('recover_marked_downloads_before_legacy_start "$id"', full)
        self.assertLess(full.index('recover_marked_downloads_before_legacy_start "$id"'),
                        full.index('run_lab_bootstrap_bounded "$id"'))

    def test_fresh_shell_skips_login_profiles_and_validator_uses_evidence_child_path(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        fresh_shell = source[source.index("fresh_repo_shell() {"):source.index("run_lab() {")]
        self.assertIn("env -u LC_BYOBU bash --noprofile --norc -c", fresh_shell)
        self.assertIn('validator_evidence_path "${inventory_label}.json"', source)
        self.assertIn('validator_evidence_path "${label}-protected-storage.json"', source)

    def test_topology_includes_the_hardened_snapshotters_and_keeps_restorer_lazy(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            "'snapshotter': 'seedsync-upgrade-v086-snapshotter-' + run_id.lower()",
            "'downloads_snapshotter': 'seedsync-upgrade-v086-downloads-snapshotter-' + run_id.lower()",
            "expected_running = {names['current'], names['current_proxy'], names['remote'], names['validator'], names['snapshotter'], names['downloads_snapshotter']}",
            "snapshotter must have no network", "snapshotter root filesystem must be read-only",
            "snapshotter capabilities must be dropped", "snapshotter must prohibit new privileges",
            "snapshotter must run as the retained-volume owner",
            "snapshotter config mount must be the exact retained volume read-only",
            "snapshotter protected mount must be the exact retained volume writable",
            "downloads snapshotter must have no network", "downloads snapshotter root filesystem must be read-only",
            "downloads snapshotter capabilities must be dropped", "downloads snapshotter must prohibit new privileges",
            "downloads snapshotter must run as the retained-volume owner", "downloads snapshotter must have exactly two mounts",
            "downloads snapshotter downloads mount must be the exact run source read-only",
            "downloads snapshotter protected mount must be the exact retained volume writable",
            "downloads snapshotter labels must bind the exact run and role",
        ):
            self.assertIn(marker, source)
        topology = source[source.index("assert_current_topology() {"):source.index("capture_current_provenance() {")]
        self.assertNotIn("downloads_restorer", topology)

    def test_browser_screenshots_check_rendered_secret_controls_before_capture(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        self.assertIn("async function safeScreenshot", browser)
        self.assertIn("detectSecretExposure", browser)
        self.assertIn("suppressedSecretFailure", browser)
        self.assertIn('[class*="secret" i]', browser)
        self.assertIn('[aria-label*="secret" i]', browser)
        self.assertIn("document.body?.innerText", browser)
        self.assertIn("Array.from(node.attributes", browser)
        self.assertIn("screenshotSafetyPolicyVersion", browser)
        self.assertIn("SEEDSYNC_SHIP_RUN_ID", browser)
        self.assertIn("PNG byte", browser)

    def test_browser_screenshot_detector_covers_visible_text_controls_and_safe_attestations(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        for marker in (
            "const visibleText", "const textSecret", "const controlSecret", "node.getAttribute('placeholder')",
            "password|secret|token|api[_ -]?key|credential|cookie|authorization", "secret_exposure: false",
            "relative_path: relativePath", "createHash('sha256')", "screenshot path is not an approved retained evidence path",
        ):
            self.assertIn(marker, browser)

    def test_browser_failure_evidence_suppresses_text_images_and_error_content_after_secret_detection(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        capture = browser[ browser.index("async function captureFailure"):browser.index("async function safeScreenshot") ]
        self.assertIn("async function detectSecretExposure", browser)
        self.assertIn("suppressedSecretFailure", browser)
        self.assertIn("diagnosticsSuppressed: true", capture)
        self.assertLess(capture.index("if (await detectSecretExposure())"), capture.index("bodySnippet"))
        self.assertLess(capture.index("if (await detectSecretExposure())"), capture.index("safeScreenshot"))
        self.assertNotIn("bodySnippet,\n      screenshot,", capture[:capture.index("const screenshot")])
        self.assertIn("errors.splice(0, errors.length, suppressedSecretFailure)", browser)
        self.assertIn("captureBrowserDiagnostic('console-error'", browser)
        self.assertIn("captureBrowserDiagnostic('pageerror'", browser)
        diagnostic = browser[browser.index("function synchronousRedactedDiagnostic"):browser.index("function isFirstClaimSseTransportError")]
        self.assertIn("spawnSync('python'", diagnostic)
        self.assertIn("redaction-failed", diagnostic)

    def test_browser_and_shell_retention_use_private_central_redaction(self):
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        lab = LAB_PATH.read_text(encoding="utf-8")
        for marker in ("SEEDSYNC_SHIP_EVIDENCE_HELPER", "redact-stdin", "mode: 0o600", "fs.chmodSync(target, 0o600)"):
            self.assertIn(marker, browser)
        self.assertIn("umask 077", launcher)
        self.assertIn("seedsync-http", launcher)
        self.assertIn("seedsync-migration-status", launcher)
        self.assertIn("seedsync-browser-session", launcher)
        self.assertIn('redact() { python "$EVIDENCE_HELPER" redact-stdin; }', lab)

    def test_async_browser_session_cleans_exact_private_workspace_on_all_exits(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            "cleanup_browser_session_workspace()", "publish_browser_session_log()",
            "browser_claim_reuse_worker()", "browser_claim_reuse_supervisor()",
            "trap request_browser_worker_shutdown USR1 HUP INT TERM",
            "trap request_browser_supervisor_shutdown USR1 HUP INT TERM",
            "cleanup_browser_claim_reuse()", "browser_session_temp_cleanup_self_check()",
            "seedsync-browser-session-self-check-failed", "seedsync-browser-session-self-check-term",
        ):
            self.assertIn(marker, launcher)

    def test_parent_browser_cleanup_uses_only_in_memory_child_identity(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        cleanup = launcher[launcher.index("cleanup_browser_claim_reuse() {"):launcher.index("wait_browser_claim_reuse_ready() {")]
        for marker in (
            "BROWSER_SESSION_PID", "BROWSER_SESSION_START_TIME", "browser_session_is_known_live_child",
            "browser_session_has_known_identity", "browser_process_has_session_identity",
            "kill_browser_session_descendants", "pidfd-kill-session-descendants", "pidfd-kill-session-leader", "deadline=$((SECONDS + 6))",
            "BROWSER_SESSION_REAPED=1", "browser_parent_cleanup_self_check()",
            "tampered browser-session PID file signalled an unrelated process",
            "forged", "normal completion deadline left browser descendant running",
            "supervisor-crash probe lost its protected descendant unexpectedly",
            "tampered browser-session workspace pointer removed an unrelated directory",
            "redactor-failure cleanup did not publish fixed safe marker",
            "parent signal cleanup did not publish redacted browser diagnostics",
            "trap 'cleanup_browser_claim_reuse; exit 129' HUP",
            "trap 'cleanup_browser_claim_reuse; exit 130' INT",
            "trap 'cleanup_browser_claim_reuse; exit 143' TERM",
        ):
            self.assertIn(marker, launcher)
        self.assertNotIn('cat "$evidence/browser-session.pid"', cleanup)
        self.assertNotIn('cat "$evidence/browser-session.raw-dir"', cleanup)
        self.assertNotIn("BROWSER_SESSION_CONTROL_TOKEN", launcher)
        self.assertNotIn("browser_worker_completion_status", launcher)
        self.assertNotIn('kill -KILL -- "$pid"', launcher)
        self.assertNotIn('kill -KILL -- "-', launcher)

    def test_initial_browser_identity_failure_is_bounded_and_preserves_workspace(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        startup = launcher[launcher.index("start_browser_claim_reuse() {"):launcher.index("browser_session_is_known_live_child() {")]
        self.assertIn('if ! browser_session_is_known_live_child; then', startup)
        self.assertIn('signal_browser_session_leader "$BROWSER_SESSION_PID" "$BROWSER_SESSION_START_TIME" TERM || true', startup)
        self.assertIn('if ! kill -0 "$BROWSER_SESSION_PID" 2>/dev/null; then wait "$BROWSER_SESSION_PID" 2>/dev/null || true; fi', startup)
        self.assertNotIn('kill -TERM "$BROWSER_SESSION_PID"', startup)
        self.assertNotIn('cleanup_browser_claim_reuse || true', startup)

    def test_browser_session_shutdown_reaps_only_its_verified_process_group_and_temp_profile(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        browser = BROWSER_PATH.read_text(encoding="utf-8")
        for marker in (
            'setsid "$BASH" "$SCRIPT_DIR/ship_readiness.sh" browser-claim-supervisor',
            'session-leader-status', 'session-descendants-status', 'pidfd-kill-session-descendants',
            'pidfd-signal-session-leader', 'signal_browser_session_leader', 'pidfd-kill-session-leader',
            'BROWSER_SESSION_PROFILE_DIR',
            'rm -rf -- "$profile_dir"', 'browser_shutdown_self_check()',
            'browser-session supervisor cleanup probe', 'publish_browser_session_log()',
            '"status":"redaction-failed","artifact":"browser-session.log"',
        ):
            self.assertIn(marker, launcher)
        self.assertNotIn('kill -TERM "$BROWSER_SESSION_PID"', launcher)
        self.assertNotIn('kill -USR1 "$BROWSER_SESSION_PID"', launcher)
        for marker in (
            'launchPersistentContext(temporaryProfileDir, { headless: true })',
            'closeWithinDeadline', 'closeBrowserResources', 'handleTermination',
            "process.once('SIGHUP'", "process.once('SIGINT'", "process.once('SIGTERM'",
            "--shutdown-self-check", "browser shutdown timed out",
        ):
            self.assertIn(marker, browser)

    def test_pidfd_descendant_kill_binds_the_signal_to_one_verified_process(self):
        if os.name != "posix" or not Path("/proc").is_dir():
            self.skipTest("pidfd requires Linux procfs")
        target = subprocess.Popen(["setsid", "sleep", "30"])
        sentinel = subprocess.Popen(["setsid", "sleep", "30"])
        try:
            time.sleep(0.05)
            state, group, session, start = HARNESS.proc_stat_identity(target.pid)
            self.assertNotEqual("Z", state)
            self.assertEqual(target.pid, group)
            self.assertEqual(target.pid, session)
            if not hasattr(os, "pidfd_open") or not hasattr(__import__("signal"), "pidfd_send_signal"):
                with self.assertRaisesRegex(RuntimeError, "pidfd"):
                    HARNESS.pidfd_kill_session_descendant(group, target.pid, start)
                self.assertIsNone(target.poll())
            else:
                self.assertTrue(HARNESS.pidfd_kill_session_descendant(group, target.pid, start))
                target.wait(timeout=3)
                sentinel_state, sentinel_group, sentinel_session, sentinel_start = HARNESS.proc_stat_identity(sentinel.pid)
                self.assertFalse(HARNESS.pidfd_kill_session_descendant(sentinel_group, sentinel.pid, sentinel_start + 1))
                self.assertIsNone(sentinel.poll())
                exited = subprocess.Popen(["setsid", "sleep", "0.01"])
                exited.wait(timeout=3)
                self.assertFalse(HARNESS.pidfd_kill_session_descendant(exited.pid, exited.pid, 1))
        finally:
            for process in (target, sentinel):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)

    def test_pidfd_leader_control_signals_bind_to_verified_session_leader(self):
        if os.name != "posix" or not Path("/proc").is_dir():
            self.skipTest("pidfd requires Linux procfs")
        leader_code = (
            "import signal, sys\n"
            "signal.signal(signal.SIGUSR1, lambda *_: print('usr1', flush=True))\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "print('ready', flush=True)\n"
            "while True:\n"
            "    signal.pause()\n"
        )
        leader = subprocess.Popen(["setsid", sys.executable, "-c", leader_code], stdout=subprocess.PIPE, text=True)
        sentinel = subprocess.Popen(["setsid", "sleep", "30"])
        nonleader = subprocess.Popen(["sleep", "30"])
        try:
            self.assertEqual("ready", leader.stdout.readline().strip())
            _, _, _, start = HARNESS.proc_stat_identity(leader.pid)
            _, _, _, sentinel_start = HARNESS.proc_stat_identity(sentinel.pid)
            if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                with self.assertRaisesRegex(RuntimeError, "pidfd"):
                    HARNESS.pidfd_signal_session_leader(leader.pid, start, signal.SIGUSR1)
                self.assertIsNone(leader.poll())
            else:
                self.assertTrue(HARNESS.pidfd_signal_session_leader(leader.pid, start, signal.SIGUSR1))
                self.assertEqual("usr1", leader.stdout.readline().strip())
                self.assertFalse(HARNESS.pidfd_signal_session_leader(leader.pid, start + 1, signal.SIGTERM))
                self.assertFalse(HARNESS.pidfd_signal_session_leader(nonleader.pid, HARNESS.proc_stat_identity(nonleader.pid)[3], signal.SIGTERM))
                self.assertIsNone(leader.poll())
                self.assertIsNone(sentinel.poll())
                self.assertFalse(HARNESS.pidfd_signal_session_leader(sentinel.pid, sentinel_start + 1, signal.SIGTERM))
                self.assertIsNone(sentinel.poll())
                self.assertTrue(HARNESS.pidfd_signal_session_leader(leader.pid, start, signal.SIGTERM))
                leader.wait(timeout=3)
        finally:
            leader.stdout.close()
            for process in (leader, sentinel, nonleader):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)

    def test_proc_descendant_enumeration_handles_space_and_right_paren_comm_names(self):
        if os.name != "posix" or not Path("/proc").is_dir():
            self.skipTest("procfs requires Linux")
        child_code = (
            "import ctypes, os, time; os.setpgrp(); "
            "ctypes.CDLL(None).prctl(15, b'x ) child name', 0, 0, 0); time.sleep(30)"
        )
        invalid_child_code = (
            "import ctypes, os, time; os.setpgrp(); "
            "ctypes.CDLL(None).prctl(15, b'bad\\xff)name', 0, 0, 0); time.sleep(30)"
        )
        leader_code = (
            "import subprocess, sys, time; "
            f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            f"invalid = subprocess.Popen([sys.executable, '-c', {invalid_child_code!r}]); "
            "print(child.pid, invalid.pid, flush=True); time.sleep(30)"
        )
        leader = subprocess.Popen(["setsid", sys.executable, "-c", leader_code], stdout=subprocess.PIPE, text=True)
        sentinel = subprocess.Popen(["setsid", "sleep", "30"])
        try:
            child_pid, invalid_child_pid = map(int, leader.stdout.readline().split())
            leader.stdout.close()
            time.sleep(0.05)
            raw_stat = Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8")
            self.assertIn("x ) child name", raw_stat)
            self.assertIn(b"bad\xff)name", Path(f"/proc/{invalid_child_pid}/stat").read_bytes())
            members = dict(HARNESS.session_descendants(leader.pid))
            self.assertIn(child_pid, members)
            self.assertIn(invalid_child_pid, members)
            self.assertNotEqual(leader.pid, HARNESS.proc_stat_identity(child_pid)[1])
            if not hasattr(os, "pidfd_open") or not hasattr(__import__("signal"), "pidfd_send_signal"):
                with self.assertRaisesRegex(RuntimeError, "pidfd"):
                    HARNESS.pidfd_kill_session_descendants(leader.pid)
                self.assertIsNone(sentinel.poll())
            else:
                self.assertTrue(HARNESS.pidfd_kill_session_descendants(leader.pid))
                for _ in range(60):
                    if HARNESS.proc_stat_identity(child_pid)[0] == "Z" and HARNESS.proc_stat_identity(invalid_child_pid)[0] == "Z":
                        break
                    time.sleep(0.05)
                self.assertEqual("Z", HARNESS.proc_stat_identity(child_pid)[0])
                self.assertEqual("Z", HARNESS.proc_stat_identity(invalid_child_pid)[0])
                self.assertIsNone(sentinel.poll())
        finally:
            for process in (leader, sentinel):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)

    def test_pidfd_inconclusive_member_state_fails_without_signaling_target(self):
        if os.name != "posix" or not Path("/proc").is_dir() or not hasattr(os, "pidfd_open"):
            self.skipTest("pidfd requires Linux")
        target = subprocess.Popen(["setsid", "sleep", "30"])
        original = HARNESS.proc_stat_identity
        try:
            _, group, _, start = original(target.pid)
            HARNESS.proc_stat_identity = lambda _: (_ for _ in ()).throw(HARNESS.ProcInconclusive("simulated permission failure"))
            with self.assertRaises(HARNESS.ProcInconclusive):
                HARNESS.pidfd_kill_session_descendant(group, target.pid, start)
            self.assertIsNone(target.poll())
        finally:
            HARNESS.proc_stat_identity = original
            if target.poll() is None:
                target.kill()
            target.wait(timeout=3)

    def test_pidfd_descendant_open_permission_is_inconclusive_and_cli_reports_it(self):
        if os.name != "posix" or not Path("/proc").is_dir() or not hasattr(os, "pidfd_open"):
            self.skipTest("pidfd requires Linux")
        target = subprocess.Popen(["setsid", "sleep", "30"])
        try:
            _, group, _, start = HARNESS.proc_stat_identity(target.pid)
            with mock.patch.object(HARNESS.os, "pidfd_open", side_effect=PermissionError("simulated denial")):
                with self.assertRaises(HARNESS.ProcInconclusive):
                    HARNESS.pidfd_kill_session_descendant(group, target.pid, start)
                with mock.patch.object(sys, "argv", [
                    "ship_readiness.py", "pidfd-kill-session-descendant", "--leader", str(group),
                    "--pid", str(target.pid), "--start-time", str(start),
                ]):
                    with self.assertRaises(SystemExit) as exited:
                        HARNESS.main()
                self.assertEqual(2, exited.exception.code)
            self.assertIsNone(target.poll())
        finally:
            if target.poll() is None:
                target.kill()
            target.wait(timeout=3)

    def test_before_filesystem_inventory_fails_closed_with_redacted_diagnostics(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            "capture_before_filesystem_inventory()", "capture_before_filesystem_failure()",
            "before-filesystem-inventory.stderr.txt", "before-filesystem-snapshot.stderr.txt",
            "before-filesystem-${step}-failure.txt", "row \"$id\" before-filesystem-inventory failed",
            "phase \"$id\" before-filesystem-inventory failed", "capture_before_filesystem_inventory \"$id\"",
            "} | redact > \"$diagnostic\"", "2> >(redact > \"$inventory_stderr\")",
            "2> >(redact > \"$snapshot_stderr\")",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("capture_volume_inventory \"$id\" before-config --legacy-config\n  snapshot_volume_config", source)

    def test_volume_inventory_scoping_self_check_covers_set_u_label_expansion(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        inventory = source[source.index("capture_volume_inventory() {"):source.index("volume_helper() {")]
        self.assertIn("local id label legacy_flag output", inventory)
        self.assertIn('label="$2"', inventory)
        self.assertIn('output="$(evidence_dir \"$id\")/${label}.json"', inventory)
        self.assertNotIn('local id="$1" label="$2" legacy_flag="${3:-}" output=', inventory)
        self.assertIn("capture_volume_inventory_scoping_self_check()", source)
        self.assertIn("inventory-scope-self-check) capture_volume_inventory_scoping_self_check", source)
        self.assertIn("capture_volume_inventory inventory-scope-self-check before-config --legacy-config", source)

    def test_volume_inventory_rehomes_before_helper_and_rejects_empty_output(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        inventory = source[source.index("capture_volume_inventory() {"):source.index("volume_helper() {")]
        self.assertIn("stabilize_repo_cwd", inventory)
        self.assertIn('capture_volume_helper_output "$id" "$output" inventory --root /config', inventory)
        self.assertIn("capture_volume_inventory_cwd_self_check()", source)
        self.assertIn("inventory-cwd-self-check) capture_volume_inventory_cwd_self_check", source)
        self.assertIn('[[ "$PWD" == "$ROOT_DIR" ]] || return 1', source)

    @unittest.skipUnless(shutil.which("bash"), "requires Bash for the WSL cwd regression probe")
    def test_deleted_cwd_uses_script_anchored_lab_and_inventory_helper(self):
        """The pre-fix root lookup fails before a cwd repair can run."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deleted_cwd = root / "deleted-cwd"
            deleted_cwd.mkdir()
            evidence = root / "evidence"
            evidence.mkdir()
            legacy_lab = root / "legacy-lab.sh"
            legacy_source = LAB_PATH.read_text(encoding="utf-8").replace(
                'readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"\n'
                'readonly ROOT_DIR="$(git -C "${SCRIPT_DIR}/../../../.." rev-parse --show-toplevel)"\n'
                'cd -- "$ROOT_DIR" || { echo "upgrade-v086: unable to enter repository root" >&2; exit 1; }',
                'readonly ROOT_DIR="$(git rev-parse --show-toplevel)"',
            )
            legacy_lab.write_text(legacy_source, encoding="utf-8")
            driver = 'cd -- "$1"; rmdir -- "$1"; exec bash "$2" "$3" "$4"'

            before = subprocess.run(
                ["bash", "-c", driver, "bash", str(deleted_cwd), str(legacy_lab), "cwd-probe", str(REPO_ROOT)],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, before.returncode)
            self.assertIn("Unable to read current working directory", before.stderr)

            deleted_cwd.mkdir()
            lab = subprocess.run(
                ["bash", "-c", driver, "bash", str(deleted_cwd), str(LAB_PATH), "cwd-probe", str(REPO_ROOT)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, lab.returncode, lab.stderr)

            deleted_cwd.mkdir()
            inventory = subprocess.run(
                ["bash", "-c", driver, "bash", str(deleted_cwd), str(LAUNCHER_PATH), "invalid-cwd-inventory-helper-self-check", str(evidence)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, inventory.returncode, inventory.stderr)
            self.assertTrue((evidence / "before-config.json").stat().st_size > 0)

    def test_apply_failure_bundle_preserves_primary_status_and_captures_context(self):
        source = LAUNCHER_PATH.read_text(encoding="utf-8")
        for marker in (
            "capture_apply_failure_bundle()", "capture_volume_inventory \"$id\" migration-failure-config-inventory",
            "migration-failure-files.json", "migration-failure-current.log",
            "migration-failure-current-proxy.log", "migration-failure-container-state.txt",
            "migration-failure-docker-events.txt", "migration-failure-http.json",
            "capture_apply_failure_bundle \"$id\" \"$base\" || true", "return \"$status\"",
            "event('initial-status'", "event('apply-accepted'", "event('duplicate-apply'", "event('poll-status'",
            "test_background_apply_failure_records_safe_diagnostic_without_request_values",
            "audit-retained-run", "audit_retained_run \"$id\"", "audit_retained_run \"$id\" || true",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("'{}: {}'.format(type(error).__name__, error)", source)
        self.assertNotIn('wait_http "$current/server/migration/v1/status"', source)


if __name__ == "__main__":
    unittest.main()
