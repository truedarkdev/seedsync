import hashlib
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from common import Config, PathPairManager, ServiceExit
from controller import AutoQueuePersist, ControllerPersist
from migration import (
    BackupRestoreError,
    MigrationBlockedError,
    MigrationCoordinator,
    MigrationDecision,
    MigrationFeature,
    MigrationSpec,
    MigrationState,
    default_migration_registry,
)
from migration.coordinator import _process_is_alive
from migration.runtime_exclusion import RuntimeExclusion
import migration.coordinator as migration_coordinator
from seedsync import Seedsync
from web.auth_store import ApiKeyStore


LEGACY_SETTINGS = """\
[General]
debug = True
verbose = False

[Lftp]
remote_address = seedbox.example
remote_username = seed
remote_password = legacy-secret
remote_port = 22
remote_path = /remote/downloads
local_path = /local/downloads
remote_path_to_scan_script = /tmp
use_ssh_key = False
num_max_parallel_downloads = 2
num_max_parallel_files_per_download = 4
num_max_connections_per_root_file = 4
num_max_connections_per_dir_file = 4
num_max_total_connections = 16
use_temp_file = False

[Controller]
interval_ms_remote_scan = 30000
interval_ms_local_scan = 10000
interval_ms_downloading_scan = 1000
extract_path = /tmp
use_local_path_as_extract_path = True

[Web]
port = 8800

[AutoQueue]
enabled = True
patterns_only = True
auto_extract = True
"""

PINNED_FF2A_NORMALIZED_SHA256 = {
    "settings.cfg": "11498de84dc93042b5416fe4caefee23101204dce22e61cc0e0d270f745a5c81",
    "controller.persist": "98d5d26ecf8d8d92167abb6121fd9767e497b80ce72e13937eeaec7b4e66872f",
    "autoqueue.persist": "96c30b115870dd1df2a0cbb23155c4b0b69e414f83844b78fbe3ba52dad25f1c",
}


class MigrationFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.downloaded = ["done.bin", "archive.zip"]
        self.extracted = ["archive.zip"]
        self.pattern_strings = [json.dumps({"pattern": value}, separators=(",", ":")) for value in ("*.mkv", "archive")]

    def write(self, settings: str = LEGACY_SETTINGS) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "settings.cfg").write_text(settings, encoding="utf-8")
        (self.root / "controller.persist").write_text(json.dumps({
            "downloaded": self.downloaded,
            "extracted": self.extracted,
        }), encoding="utf-8")
        (self.root / "autoqueue.persist").write_text(json.dumps({
            "patterns": self.pattern_strings,
        }), encoding="utf-8")


class TestMigrationCoordinator(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _tree_bytes(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    @staticmethod
    def _recorded_backup(root: Path) -> Path:
        receipt = json.loads((root / "migration-state.json").read_text(encoding="utf-8"))
        return root / receipt["backup"]

    @staticmethod
    def _write_running_metadata(root: Path, decision: MigrationDecision) -> None:
        (root / "migration-state.json").write_text(json.dumps({
            "metadata_version": 2,
            "state": "running",
            "migration_id": decision.migration_id,
            "source_schema": decision.source_schema,
            "target_schema": decision.target_schema,
            "current_schema": decision.source_schema,
            "applied_migrations": [],
            "attempt": 1,
            "error": None,
            "retryable": False,
        }), encoding="utf-8")

    @unittest.skipUnless(os.name == "nt", "Windows-specific process liveness regression")
    def test_windows_process_liveness_probe_does_not_call_os_kill(self) -> None:
        with patch("migration.coordinator.os.kill", side_effect=AssertionError("unsafe Windows liveness probe")):
            self.assertTrue(_process_is_alive(os.getpid()))

    @unittest.skipUnless(os.name == "posix", "POSIX owner-mode contract")
    def test_private_files_remain_mode_0600_on_posix_filesystems(self) -> None:
        path = self.root / "private"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
        try:
            migration_coordinator._restrict_fd_to_owner(descriptor)
            self.assertEqual(0o600, stat.S_IMODE(os.fstat(descriptor).st_mode))
        finally:
            os.close(descriptor)

    @unittest.skipUnless(os.name == "posix", "POSIX owner-mode contract")
    def test_mode_failures_block_runtime_locks_and_private_storage(self) -> None:
        unsupported = PermissionError("DrvFS chmod is unsupported")
        path = self.root / "private"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
        try:
            with patch("migration.coordinator.os.fchmod", side_effect=unsupported):
                with self.assertRaises(PermissionError):
                    migration_coordinator._restrict_fd_to_owner(descriptor)
        finally:
            os.close(descriptor)
        with patch("migration.coordinator.os.fchmod", side_effect=unsupported):
            with self.assertRaises(PermissionError):
                with RuntimeExclusion(self.root, "runtime-lease"):
                    pass

        from migration.backup_restore import _write_private_file
        with patch("migration.coordinator.os.fchmod", side_effect=unsupported):
            with self.assertRaises(PermissionError):
                _write_private_file(self.root / "private-backup", payload=b"synthetic", root=self.root)

    @unittest.skipUnless(os.name == "posix", "POSIX owner-mode contract")
    def test_unverified_mode_blocks_private_storage(self) -> None:
        path = self.root / "unverified-private"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
        try:
            with patch("migration.coordinator.os.fchmod", return_value=None):
                with self.assertRaisesRegex(PermissionError, "not owner-only"):
                    migration_coordinator._restrict_fd_to_owner(descriptor)
        finally:
            os.close(descriptor)

    @unittest.skipUnless(os.name == "posix", "POSIX root and lock integrity contract")
    def test_writable_root_and_replaced_lock_are_rejected(self) -> None:
        MigrationFixture(self.root).write()
        os.chmod(self.root, 0o777)
        try:
            with self.assertRaisesRegex(PermissionError, "not group/other writable"):
                migration_coordinator._capture_root_identity(self.root)
        finally:
            os.chmod(self.root, 0o700)
        lock = self.root / ".migration.lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            migration_coordinator._restrict_fd_to_owner(descriptor)
            os.unlink(lock)
            replacement = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(replacement)
            with self.assertRaisesRegex(PermissionError, "unsafe identity"):
                migration_coordinator._verify_lock_descriptor(lock, self.root, descriptor)
        finally:
            os.close(descriptor)

    @unittest.skipUnless(os.name == "posix", "POSIX post-acquisition lock integrity contract")
    def test_runtime_lock_replacement_while_acquiring_is_rejected(self) -> None:
        lock = self.root / ".seedsync.runtime.lock"

        def replace_after_acquisition(_descriptor: int) -> None:
            os.unlink(lock)
            replacement = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(replacement)

        with patch.object(RuntimeExclusion, "_lock_descriptor", side_effect=replace_after_acquisition):
            with self.assertRaisesRegex(PermissionError, "unsafe identity"):
                RuntimeExclusion(self.root, "runtime-lease")

    def test_exact_legacy_requires_consent_without_mutating_source_files(self) -> None:
        fixture = MigrationFixture(self.root)
        fixture.write()
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}

        decision = MigrationCoordinator(self.root).preflight()

        self.assertEqual(MigrationState.REQUIRED, decision.state)
        self.assertFalse(decision.allows_normal_startup)
        self.assertTrue(decision.features)
        for name, content in before.items():
            self.assertEqual(content, (self.root / name).read_bytes())
        self.assertEqual(set(before), {path.name for path in self.root.iterdir()})
        self.assertFalse((self.root / "path_pairs.json").exists())
        self.assertFalse((self.root / "api-keys.json").exists())

    def test_completed_migration_requires_durable_continue_before_normal_startup(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)

        completed = coordinator.apply_confirmed()

        self.assertEqual(MigrationState.COMPLETE, completed.state)
        self.assertFalse(completed.normal_startup_released)
        self.assertFalse(coordinator.preflight().allows_normal_startup)
        with self.assertRaises(MigrationBlockedError):
            coordinator.require_normal_startup()

        released = coordinator.release_normal_startup()

        self.assertTrue(released.normal_startup_released)
        self.assertTrue(released.allows_normal_startup)
        self.assertEqual(released, coordinator.release_normal_startup())
        persisted = json.loads((self.root / "migration-state.json").read_text(encoding="utf-8"))
        self.assertTrue(persisted["normal_startup_released"])
        self.assertTrue(MigrationCoordinator(self.root).require_normal_startup().normal_startup_released)

    def test_completed_receipt_without_continue_gate_remains_startable(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.apply_confirmed()
        receipt_path = self.root / "migration-state.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        del receipt["normal_startup_released"]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        restarted = MigrationCoordinator(self.root).require_normal_startup()

        self.assertTrue(restarted.normal_startup_released)

    def test_pinned_ff2a_fixture_requires_consent_without_source_mutation(self) -> None:
        fixture_root = Path(__file__).parents[1] / "fixtures" / "upgrade_v086_ff2a"
        provenance = (fixture_root / "PROVENANCE.md").read_text(encoding="utf-8")
        self.assertIn("ff2a1039935beccbbf7ec76134b41d2e91137742", provenance)
        for name, expected_digest in PINNED_FF2A_NORMALIZED_SHA256.items():
            source = fixture_root / name
            normalized = source.read_bytes().rstrip(b"\r\n")
            self.assertEqual(expected_digest, hashlib.sha256(normalized).hexdigest())
            shutil.copyfile(source, self.root / source.name)
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}

        coordinator = MigrationCoordinator(self.root)
        self.assertTrue(default_migration_registry()[0].fingerprint(self.root))
        decision = coordinator.preflight()

        self.assertEqual(MigrationState.REQUIRED, decision.state)
        self.assertEqual(set(before), {path.name for path in self.root.iterdir()})
        for name, content in before.items():
            self.assertEqual(content, (self.root / name).read_bytes())

    def test_pinned_v086_with_runtime_injected_general_keys_remains_selected(self) -> None:
        fixture = MigrationFixture(self.root)
        fixture.write(LEGACY_SETTINGS.replace(
            "[General]\n",
            "[General]\n"
            "trusted_browser_bootstrap_remote_addrs = 172.17.0.1\n"
            "config_api_redact_remote_details = False\n",
        ))
        before = self._tree_bytes(self.root)

        coordinator = MigrationCoordinator(self.root)
        decision = coordinator.status()

        self.assertEqual(MigrationState.REQUIRED, decision.state)
        self.assertEqual("original-v0.8.6-to-current-v1", decision.migration_id)
        self.assertEqual("original-v0.8.6", decision.source_schema)
        self.assertFalse(coordinator.retained_backup_ready(decision))
        self.assertEqual(decision, coordinator.status())
        self.assertEqual(before, self._tree_bytes(self.root))
        self.assertFalse((self.root / "migration-backups").exists())
        self.assertFalse((self.root / "migration-state.json").exists())

    def test_empty_current_and_completed_receipt_are_allowed(self) -> None:
        self.assertEqual(MigrationState.NOT_REQUIRED, MigrationCoordinator(self.root).preflight().state)

        current_root = self.root / "current"
        current_root.mkdir()
        current = Seedsync._create_default_config()
        (current_root / "settings.cfg").write_text(current.to_str(), encoding="utf-8")
        self.assertEqual(MigrationState.NOT_REQUIRED, MigrationCoordinator(current_root).preflight().state)

        # A real completed receipt is produced by the coordinator and remains a no-op.
        migrated_root = self.root / "migrated"
        fixture = MigrationFixture(migrated_root)
        fixture.write()
        coordinator = MigrationCoordinator(migrated_root)
        coordinator.preflight()
        first = coordinator.apply_confirmed()
        second = coordinator.apply_confirmed()
        self.assertEqual(MigrationState.COMPLETE, first.state)
        self.assertEqual(first, second)

    def test_ambiguous_nonempty_config_fails_closed(self) -> None:
        (self.root / "settings.cfg").write_text("[General]\nverbose=True\n", encoding="utf-8")
        before = self._tree_bytes(self.root)
        decision = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.FAILED, decision.state)
        self.assertFalse(decision.retryable)
        self.assertEqual(before, self._tree_bytes(self.root))
        with self.assertRaises(MigrationBlockedError):
            MigrationCoordinator(self.root).require_normal_startup()

    def test_repeated_inspection_is_byte_preserving_for_required_failed_and_current(self) -> None:
        roots = {
            "required": self.root / "required",
            "failed": self.root / "failed",
            "current": self.root / "current",
        }
        MigrationFixture(roots["required"]).write()
        roots["failed"].mkdir()
        (roots["failed"] / "settings.cfg").write_text("[General]\nverbose=True\n", encoding="utf-8")
        roots["current"].mkdir()
        (roots["current"] / "settings.cfg").write_text(
            Seedsync._create_default_config().to_str(), encoding="utf-8",
        )
        for name, root in roots.items():
            with self.subTest(name=name):
                before = self._tree_bytes(root)
                coordinator = MigrationCoordinator(root)
                coordinator.preflight()
                coordinator.status()
                coordinator.status()
                self.assertEqual(before, self._tree_bytes(root))

    def test_forged_or_unknown_completed_lineage_fails_closed_without_rewrite(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.apply_confirmed()
        receipt_path = self.root / "migration-state.json"
        valid = json.loads(receipt_path.read_text(encoding="utf-8"))
        for name, applied, migration_id in (
            ("empty", [], valid["migration_id"]),
            ("unknown", ["unknown-migration"], "unknown-migration"),
            ("mismatched", valid["applied_migrations"], "unknown-migration"),
        ):
            with self.subTest(name=name):
                forged = dict(valid, applied_migrations=applied, migration_id=migration_id)
                receipt_path.write_text(json.dumps(forged), encoding="utf-8")
                before = self._tree_bytes(self.root)
                decision = MigrationCoordinator(self.root).preflight()
                self.assertEqual(MigrationState.FAILED, decision.state)
                self.assertFalse(decision.allows_normal_startup)
                self.assertEqual(before, self._tree_bytes(self.root))
                with self.assertRaises(MigrationBlockedError):
                    MigrationCoordinator(self.root).require_normal_startup()
        receipt_path.write_text(json.dumps(valid), encoding="utf-8")

    def test_legacy_shape_with_current_auth_state_is_ambiguous(self) -> None:
        MigrationFixture(self.root).write()
        (self.root / "api-keys.json").write_text('{"version": 1, "api_keys": []}', encoding="utf-8")
        decision = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.FAILED, decision.state)
        self.assertFalse(decision.retryable)

    def test_legacy_web_port_is_bounded_and_malformed_values_fall_back(self) -> None:
        for value, expected in (("9876", 9876), ("0", 8800), ("65536", 8800), ("not-a-port", 8800)):
            with self.subTest(value=value):
                root = self.root / value.replace("-", "_")
                MigrationFixture(root).write(LEGACY_SETTINGS.replace("port = 8800", "port = {}".format(value)))
                before = self._tree_bytes(root)
                self.assertEqual(expected, MigrationCoordinator(root).legacy_web_port())
                self.assertEqual(before, self._tree_bytes(root))

    def test_apply_preserves_semantics_creates_backup_and_leaves_first_claim_open(self) -> None:
        fixture = MigrationFixture(self.root)
        fixture.write()
        source_digests = {
            name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
            for name in ("settings.cfg", "controller.persist", "autoqueue.persist")
        }
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()

        self.assertFalse(coordinator.retained_backup_ready(coordinator.status()))
        verified_lock_names = []
        original_verify = migration_coordinator._verify_lock_descriptor

        def record_verify(path: Path, root: Path, descriptor: int) -> None:
            verified_lock_names.append(path.name)
            original_verify(path, root, descriptor)

        with patch("migration.coordinator._verify_lock_descriptor", side_effect=record_verify):
            decision = coordinator.apply_confirmed()

        self.assertEqual(MigrationState.COMPLETE, decision.state)
        self.assertGreaterEqual(verified_lock_names.count(".migration.lock"), 2)
        self.assertTrue(coordinator.retained_backup_ready(decision))
        migrated = Config.from_file(str(self.root / "settings.cfg"))
        self.assertEqual("DEBUG", migrated.general.log_level)
        self.assertEqual("0", migrated.lftp.rate_limit)
        self.assertEqual("seedbox.example", migrated.lftp.remote_address)
        pairs = PathPairManager(str(self.root)).load().path_pairs
        self.assertEqual(1, len(pairs))
        self.assertEqual(("Default", "/remote/downloads", "/local/downloads"), (
            pairs[0].name, pairs[0].remote_path, pairs[0].local_path,
        ))
        controller = ControllerPersist.from_file(str(self.root / "controller.persist"))
        self.assertEqual(set(fixture.downloaded), controller.downloaded_file_names)
        self.assertEqual(set(fixture.extracted), controller.extracted_file_names)
        serialized_controller = json.loads((self.root / "controller.persist").read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "downloaded", "extracted", "stopped", "move_failure_counts",
                "final_move_succeeded",
            },
            set(serialized_controller),
        )
        self.assertEqual(set(fixture.downloaded), set(serialized_controller["downloaded"]))
        self.assertEqual(set(fixture.extracted), set(serialized_controller["extracted"]))
        self.assertEqual([], serialized_controller["stopped"])
        self.assertEqual({}, serialized_controller["move_failure_counts"])
        self.assertEqual([], serialized_controller["final_move_succeeded"])
        autoqueue = AutoQueuePersist.from_file(str(self.root / "autoqueue.persist"))
        self.assertEqual({"*.mkv", "archive"}, {pattern.pattern for pattern in autoqueue.patterns})
        self.assertFalse((self.root / "api-keys.json").exists())

        backup = self._recorded_backup(self.root)
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(source_digests, {
            entry["path"]: entry["sha256"]
            for entry in manifest["entries"] if entry["path"] in source_digests
        })
        receipt = json.loads((self.root / "migration-state.json").read_text(encoding="utf-8"))
        self.assertEqual("complete", receipt["state"])
        self.assertEqual(1, receipt["receipt_version"])
        self.assertFalse(receipt["normal_startup_released"])
        self.assertEqual("seedsync-current-v1", receipt["current_schema"])
        self.assertEqual(["original-v0.8.6-to-current-v1"], receipt["applied_migrations"])

    def test_completed_lineage_allows_only_bootstrap_proof_history_created_after_migration(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)
        with self.assertRaises(MigrationBlockedError):
            coordinator.require_normal_startup()
        released = coordinator.release_normal_startup()
        self.assertTrue(released.normal_startup_released)

        store = ApiKeyStore(file_path=str(self.root / "api-keys.json"))
        self.assertIsNotNone(store.ensure_bootstrap_proof())

        restarted = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.COMPLETE, restarted.state)
        self.assertTrue(restarted.allows_normal_startup)

    def test_completed_lineage_allows_repeated_empty_store_bootstrap_exit_state(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)
        coordinator.release_normal_startup()

        migration_runtime = ApiKeyStore(file_path=str(self.root / "api-keys.json"))
        migration_runtime.ensure_bootstrap_proof()
        first = ApiKeyStore(file_path=str(self.root / "api-keys.json"))
        first.ensure_bootstrap_proof()
        first.save()
        self.assertEqual(MigrationState.COMPLETE, MigrationCoordinator(self.root).preflight().state)

        second = ApiKeyStore.from_file(str(self.root / "api-keys.json"))
        self.assertEqual(MigrationState.COMPLETE, MigrationCoordinator(self.root).preflight().state)
        second.ensure_bootstrap_proof()
        second.save()
        second.save()
        restarted = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.COMPLETE, restarted.state)
        self.assertTrue(restarted.allows_normal_startup)

    def _assert_interrupted_completed_claim_recovers_to_preclaim(self, *, after_remembered_session: bool) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)
        before = self._tree_bytes(self.root)
        store = ApiKeyStore(file_path=str(self.root / "api-keys.json"))
        store.bind_completed_migration_claim_transition(coordinator.completed_auth_transition_binding())
        self.assertTrue(store.begin_completed_migration_claim_transaction())
        created = store.create_initial_admin_api_key_if_available("migration-claim-v1", "migration-admin")
        self.assertIsNotNone(created)
        assert created is not None
        if after_remembered_session:
            store.create_remembered_browser_session_for_api_key(created["record"].id)

        restarted = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.COMPLETE, restarted.state)
        self.assertEqual("preclaim", restarted.completed_auth_phase)
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_completed_claim_interruption_after_initial_key_recovers_before_preclaim_validation(self) -> None:
        self._assert_interrupted_completed_claim_recovers_to_preclaim(after_remembered_session=False)

    def test_completed_claim_interruption_after_remembered_session_recovers_before_preclaim_validation(self) -> None:
        self._assert_interrupted_completed_claim_recovers_to_preclaim(after_remembered_session=True)

    def test_completed_claim_required_history_failures_are_restartable_preclaim(self) -> None:
        from web import auth_store as auth_store_module

        original_append = auth_store_module.append_api_key_store_history
        for failing_write in range(1, 5):
            with self.subTest(failing_write=failing_write):
                root = self.root / "history-failure-{}".format(failing_write)
                MigrationFixture(root).write()
                coordinator = MigrationCoordinator(root)
                self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)
                before = self._tree_bytes(root)
                store = ApiKeyStore(file_path=str(root / "api-keys.json"))
                store.bind_completed_migration_claim_transition(coordinator.completed_auth_transition_binding())
                self.assertTrue(store.begin_completed_migration_claim_transaction())
                required_write_count = 0

                def append_with_failure(file_path, event, reason, *, required=False, **details):
                    nonlocal required_write_count
                    if required:
                        required_write_count += 1
                        if required_write_count == failing_write:
                            raise ValueError("forced required history failure")
                    return original_append(file_path, event, reason, required=required, **details)

                with patch("web.auth_store.append_api_key_store_history", side_effect=append_with_failure):
                    with self.assertRaises(ValueError):
                        created = store.create_initial_admin_api_key_if_available("migration-claim-v1", "migration-admin")
                        if created is not None:
                            store.create_remembered_browser_session_for_api_key(created["record"].id)

                restarted = MigrationCoordinator(root).preflight()
                self.assertEqual(MigrationState.COMPLETE, restarted.state)
                self.assertEqual("preclaim", restarted.completed_auth_phase)
                self.assertEqual(before, self._tree_bytes(root))

    def test_committed_claim_cleanup_fsync_failure_preserves_claimed_restart(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)
        store = ApiKeyStore(file_path=str(self.root / "api-keys.json"))
        store.bind_completed_migration_claim_transition(coordinator.completed_auth_transition_binding())
        self.assertTrue(store.begin_completed_migration_claim_transaction())
        created = store.create_initial_admin_api_key_if_available("migration-claim-v1", "migration-admin")
        self.assertIsNotNone(created)
        assert created is not None
        store.create_remembered_browser_session_for_api_key(created["record"].id)
        store.complete_completed_migration_claim_transition(created["record"].id, "migration-claim-v1")

        with patch("web.auth_store._fsync_completed_migration_directory", side_effect=OSError("after unlink")):
            store.finish_completed_migration_claim_transaction()

        self.assertFalse((self.root / ".migration-claim-auth.journal.json").exists())
        restarted = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.COMPLETE, restarted.state)
        self.assertEqual("claimed", restarted.completed_auth_phase)

    def test_completed_migration_exit_probe_is_repeatedly_finite_and_restartable(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)
        coordinator.release_normal_startup()
        argv = [
            "seedsync.py", "-c", str(self.root), "--html", str(self.root),
            "--scanfs", str(self.root), "--exit",
        ]

        for _ in range(3):
            with patch.object(sys, "argv", argv):
                application = Seedsync()
                with self.assertRaises(ServiceExit):
                    application.run()
            restarted = MigrationCoordinator(self.root).preflight()
            self.assertEqual(MigrationState.COMPLETE, restarted.state)
            self.assertTrue(restarted.allows_normal_startup)

    def _complete_migrated_browser_claim(self, root: Path) -> tuple[MigrationCoordinator, str]:
        MigrationFixture(root).write()
        coordinator = MigrationCoordinator(root)
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)
        coordinator.release_normal_startup()
        binding = coordinator.completed_auth_transition_binding()
        store = ApiKeyStore(file_path=str(root / "api-keys.json"))
        store.bind_completed_migration_claim_transition(binding)
        self.assertIsNotNone(store.ensure_bootstrap_proof())
        created = store.create_initial_admin_api_key_if_available("migration-claim-v1", "migration-admin")
        self.assertIsNotNone(created)
        assert created is not None
        store.create_remembered_browser_session_for_api_key(created["record"].id)
        store.complete_completed_migration_claim_transition(created["record"].id, "migration-claim-v1")
        os.chmod(root / "api-keys.json", 0o600)
        return coordinator, created["record"].id

    def test_completed_claim_marker_allows_later_normal_restarts_only_after_real_claim(self) -> None:
        _, key_id = self._complete_migrated_browser_claim(self.root)
        marker = json.loads((self.root / "migration-claimed-auth.json").read_text(encoding="utf-8"))
        self.assertEqual(1, marker["schema"])
        self.assertEqual(key_id, marker["initial_admin_key_id"])
        self.assertNotIn("secret", json.dumps(marker).lower())
        for restart in range(2):
            with self.subTest(restart=restart):
                decision = MigrationCoordinator(self.root).preflight()
                self.assertEqual(MigrationState.COMPLETE, decision.state)
                self.assertEqual("claimed", decision.completed_auth_phase)

    def test_claimed_completed_migration_allows_intentionally_empty_path_pairs_after_restart(self) -> None:
        self._complete_migrated_browser_claim(self.root)
        manager = PathPairManager(str(self.root))
        pairs = manager.get_all_pairs()
        self.assertEqual(1, len(pairs))
        manager.remove_pair(pairs[0].id)

        restarted = MigrationCoordinator(self.root).require_normal_startup()

        self.assertEqual(MigrationState.COMPLETE, restarted.state)
        self.assertEqual("claimed", restarted.completed_auth_phase)
        self.assertEqual([], PathPairManager(str(self.root)).get_all_pairs())

    def test_completed_claimed_lineage_validation_is_read_only(self) -> None:
        self._complete_migrated_browser_claim(self.root)
        with patch("migration.coordinator._restrict_fd_to_owner", side_effect=AssertionError("read mutation")):
            decision = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.COMPLETE, decision.state)
        self.assertEqual("claimed", decision.completed_auth_phase)

    def test_claimed_completed_migration_can_arm_and_consume_only_its_receipt_backup(self) -> None:
        coordinator, _ = self._complete_migrated_browser_claim(self.root)
        eligibility = coordinator.recovery_eligibility()
        self.assertTrue(eligibility["eligible"])
        self.assertEqual("original-v0.8.6-to-current-v1", eligibility["migration_id"])
        self.assertEqual("RESTORE", eligibility["confirmation"])

        coordinator.request_recovery_restore(
            confirmation=eligibility["confirmation"], other_instances_stopped=True,
        )
        intent_path = self.root / ".migration-recovery-intent.json"
        self.assertTrue(intent_path.is_file())
        if os.name == "posix":
            self.assertEqual(0, stat.S_IMODE(intent_path.stat().st_mode) & 0o077)

        with RuntimeExclusion(self.root, "normal-runtime"):
            with self.assertRaises(BackupRestoreError):
                coordinator.consume_recovery_restore_intent()
        self.assertTrue(intent_path.exists())

        self.assertTrue(coordinator.consume_recovery_restore_intent())
        self.assertFalse(intent_path.exists())
        restored = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.REQUIRED, restored.state)

    def test_recovery_intent_rejects_client_like_backup_tampering_before_restore(self) -> None:
        coordinator, _ = self._complete_migrated_browser_claim(self.root)
        eligibility = coordinator.recovery_eligibility()
        coordinator.request_recovery_restore(
            confirmation=eligibility["confirmation"], other_instances_stopped=True,
        )
        intent_path = self.root / ".migration-recovery-intent.json"
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        intent["backup"] = "migration-backups/not-a-receipt-backup"
        intent_path.write_text(json.dumps(intent), encoding="utf-8")
        if os.name == "posix":
            os.chmod(intent_path, 0o600)

        with self.assertRaises(ValueError):
            coordinator.consume_recovery_restore_intent()
        self.assertTrue(intent_path.exists())

    def test_recovery_intent_requires_exact_visible_confirmation_phrase(self) -> None:
        coordinator, _ = self._complete_migrated_browser_claim(self.root)
        eligibility = coordinator.recovery_eligibility()
        with self.assertRaises(ValueError):
            coordinator.request_recovery_restore(
                confirmation=eligibility["confirmation"] + " now", other_instances_stopped=True,
            )
        with self.assertRaises(ValueError):
            coordinator.request_recovery_restore(
                confirmation="restore", other_instances_stopped=True,
            )
        self.assertFalse((self.root / ".migration-recovery-intent.json").exists())

    def test_recovery_intent_survives_failure_after_receipt_cleanup_before_final_inventory(self) -> None:
        coordinator, _ = self._complete_migrated_browser_claim(self.root)
        eligibility = coordinator.recovery_eligibility()
        coordinator.request_recovery_restore(
            confirmation=eligibility["confirmation"], other_instances_stopped=True,
        )
        intent_path = self.root / ".migration-recovery-intent.json"

        with patch("migration.backup_restore._walk_inventory", side_effect=BackupRestoreError("injected final inventory failure")):
            with self.assertRaises(BackupRestoreError):
                coordinator.consume_recovery_restore_intent()

        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("converging", intent["phase"])
        self.assertFalse((self.root / "migration-state.json").exists())
        self.assertFalse((self.root / ".migration-restore.json").exists())

        self.assertTrue(coordinator.consume_recovery_restore_intent())
        self.assertFalse(intent_path.exists())
        self.assertEqual(MigrationState.REQUIRED, MigrationCoordinator(self.root).preflight().state)

    def test_recovery_intent_survives_failure_after_final_inventory_before_intent_cleanup(self) -> None:
        coordinator, _ = self._complete_migrated_browser_claim(self.root)
        eligibility = coordinator.recovery_eligibility()
        coordinator.request_recovery_restore(
            confirmation=eligibility["confirmation"], other_instances_stopped=True,
        )
        intent_path = self.root / ".migration-recovery-intent.json"

        with patch.object(coordinator, "_consume_recovery_intent_after_restore", side_effect=OSError("injected intent cleanup failure")):
            with self.assertRaises(OSError):
                coordinator.consume_recovery_restore_intent()

        self.assertTrue(intent_path.exists())
        self.assertFalse((self.root / "migration-state.json").exists())
        self.assertFalse((self.root / ".migration-restore.json").exists())
        self.assertTrue(coordinator.consume_recovery_restore_intent())
        self.assertFalse(intent_path.exists())

    def test_completed_claim_marker_rejects_active_auth_before_transition_and_tampering(self) -> None:
        active_root = self.root / "active-before-marker"
        MigrationFixture(active_root).write()
        coordinator = MigrationCoordinator(active_root)
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)
        active = ApiKeyStore(file_path=str(active_root / "api-keys.json"))
        active.create_api_key("forged", ["admin"])
        os.chmod(active_root / "api-keys.json", 0o600)
        self.assertEqual(MigrationState.FAILED, MigrationCoordinator(active_root).preflight().state)

        _, key_id = self._complete_migrated_browser_claim(self.root)
        marker_path = self.root / "migration-claimed-auth.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["receipt_sha256"] = "0" * 64
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        os.chmod(marker_path, 0o600)
        self.assertEqual(MigrationState.FAILED, MigrationCoordinator(self.root).preflight().state)
        marker["receipt_sha256"] = hashlib.sha256((self.root / "migration-state.json").read_bytes()).hexdigest()
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        os.chmod(marker_path, 0o600)
        store_path = self.root / "api-keys.json"
        store_payload = json.loads(store_path.read_text(encoding="utf-8"))
        store_payload["api_keys"] = [record for record in store_payload["api_keys"] if record["id"] != key_id]
        store_path.write_text(json.dumps(store_payload), encoding="utf-8")
        os.chmod(store_path, 0o600)
        self.assertEqual(MigrationState.FAILED, MigrationCoordinator(self.root).preflight().state)

    def test_completed_lineage_rejects_active_malformed_dangling_and_unsafe_auth_state(self) -> None:
        for name in ("active", "malformed", "dangling", "unsafe"):
            with self.subTest(name=name):
                root = self.root / name
                MigrationFixture(root).write()
                self.assertEqual(MigrationState.COMPLETE, MigrationCoordinator(root).apply_confirmed().state)
                store_path = root / "api-keys.json"
                history_path = root / "api-keys.history.jsonl"
                if name == "active":
                    store = ApiKeyStore(file_path=str(store_path))
                    store.create_api_key("administrator", ["admin"])
                elif name == "malformed":
                    history_path.write_text("{not json}\n", encoding="utf-8")
                elif name == "dangling":
                    store_path.write_text(
                        json.dumps({
                            "version": 3,
                            "api_keys": [],
                            "ui_sessions": [],
                            "browser_handover_claimed_version": "",
                        }),
                        encoding="utf-8",
                    )
                else:
                    store = ApiKeyStore(file_path=str(store_path))
                    store.ensure_bootstrap_proof()
                    store.save()
                    if os.name == "posix":
                        os.chmod(store_path, 0o644)
                    else:
                        os.link(store_path, root / "api-keys-copy.json")

                decision = MigrationCoordinator(root).preflight()
                self.assertEqual(MigrationState.FAILED, decision.state)
                self.assertFalse(decision.allows_normal_startup)

    def test_apply_stays_strict_when_bootstrap_proof_artifacts_exist(self) -> None:
        MigrationFixture(self.root).write()
        original = default_migration_registry()[0]

        def apply_with_bootstrap_proof(config_dir: Path, source) -> None:
            original.apply(config_dir, source)
            ApiKeyStore(file_path=str(config_dir / "api-keys.json")).ensure_bootstrap_proof()

        injected = MigrationSpec(
            original.migration_id, original.order, original.source_schema, original.target_schema,
            original.features, original.fingerprint, apply_with_bootstrap_proof,
            original.validate, original.input_files,
        )

        with self.assertRaises(MigrationBlockedError) as context:
            MigrationCoordinator(self.root, (injected,)).apply_confirmed()

        self.assertEqual(MigrationState.FAILED, context.exception.decision.state)
        self.assertTrue(context.exception.decision.retryable)

    def test_apply_rejects_dangling_auth_store_and_retains_recovery_backup(self) -> None:
        for auth_state_name in ("api-keys.json", "api-keys.history.jsonl"):
            with self.subTest(auth_state_name=auth_state_name):
                root = self.root / auth_state_name.replace(".", "_")
                fixture = MigrationFixture(root)
                fixture.write()
                before = {
                    name: (root / name).read_bytes()
                    for name in ("settings.cfg", "controller.persist", "autoqueue.persist")
                }
                original = default_migration_registry()[0]

                def apply_with_dangling_auth_store(config_dir: Path, source) -> None:
                    original.apply(config_dir, source)
                    try:
                        (config_dir / auth_state_name).symlink_to("missing-auth-state")
                    except OSError as exc:
                        self.skipTest("symlink creation is unavailable: {}".format(exc))

                injected = MigrationSpec(
                    original.migration_id, original.order, original.source_schema, original.target_schema,
                    original.features, original.fingerprint, apply_with_dangling_auth_store,
                    original.validate, original.input_files,
                )
                coordinator = MigrationCoordinator(root, (injected,))

                with self.assertRaises(MigrationBlockedError) as context:
                    coordinator.apply_confirmed()

                failed = context.exception.decision
                self.assertEqual(MigrationState.FAILED, failed.state)
                self.assertTrue(failed.retryable)
                self.assertTrue((root / auth_state_name).is_symlink())
                receipt = json.loads((root / "migration-state.json").read_text(encoding="utf-8"))
                self.assertEqual("failed", receipt["state"])
                self.assertEqual([], receipt.get("applied_migrations"))
                self.assertIn("migration-backups/", receipt["backup"])
                with self.assertRaises(MigrationBlockedError):
                    coordinator.require_normal_startup()

                coordinator.restore_offline(Path(receipt["backup"]).name)
                self.assertFalse((root / auth_state_name).exists())
                self.assertFalse((root / auth_state_name).is_symlink())
                self.assertFalse((root / "path_pairs.json").exists())
                self.assertEqual(before, {
                    name: (root / name).read_bytes()
                    for name in before
                })

    def test_retained_backup_ready_revalidates_after_prior_ready_result(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        decision = coordinator.apply_confirmed()
        self.assertTrue(coordinator.retained_backup_ready(decision))
        metadata = json.loads((self.root / "migration-state.json").read_text(encoding="utf-8"))
        backup_settings = self.root / metadata["backup"] / "data" / "settings.cfg"

        backup_settings.write_bytes(backup_settings.read_bytes() + b"\ncorrupted=True\n")

        self.assertFalse(coordinator.retained_backup_ready(decision))

    def test_retained_backup_ready_revalidates_after_prior_ready_file_removal(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        decision = coordinator.apply_confirmed()
        self.assertTrue(coordinator.retained_backup_ready(decision))
        metadata = json.loads((self.root / "migration-state.json").read_text(encoding="utf-8"))
        backup_settings = self.root / metadata["backup"] / "data" / "settings.cfg"

        backup_settings.rename(backup_settings.with_suffix(".missing"))

        self.assertFalse(coordinator.retained_backup_ready(decision))

    def test_known_legacy_webhook_secret_moves_to_notifications(self) -> None:
        settings = LEGACY_SETTINGS.replace("verbose = False", "verbose = False\nwebhook_secret = signing-secret")
        MigrationFixture(self.root).write(settings)
        coordinator = MigrationCoordinator(self.root)
        self.assertEqual(MigrationState.REQUIRED, coordinator.preflight().state)
        coordinator.apply_confirmed()
        migrated = Config.from_file(str(self.root / "settings.cfg"))
        self.assertEqual("signing-secret", migrated.notifications.hmac_secret)
        self.assertFalse(hasattr(migrated.general, "webhook_secret"))

    def test_failure_retry_and_interrupted_restart_are_explicit(self) -> None:
        fixture = MigrationFixture(self.root)
        fixture.write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        spec = coordinator.registry[0]
        failed_once = False

        def fail_after_writes(config_dir: Path, source_dir: Path) -> None:
            nonlocal failed_once
            spec.apply(config_dir, source_dir)
            if not failed_once:
                failed_once = True
                raise RuntimeError("bounded failure")

        retry_spec = type(spec)(
            spec.migration_id, spec.order, spec.source_schema, spec.target_schema,
            spec.features, spec.fingerprint, fail_after_writes, spec.validate, spec.input_files,
        )
        coordinator = MigrationCoordinator(self.root, (retry_spec,))
        with self.assertRaises(MigrationBlockedError) as context:
            coordinator.apply_confirmed()
        self.assertEqual(MigrationState.FAILED, context.exception.decision.state)
        with self.assertRaises(MigrationBlockedError):
            coordinator.apply_confirmed()
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed(retry=True).state)
        self.assertEqual(2, json.loads((self.root / "migration-state.json").read_text())["attempt"])

        # A durable RUNNING record without a live lock is treated as interrupted and retryable.
        receipt = json.loads((self.root / "migration-state.json").read_text())
        receipt.update({"state": "running", "retryable": False})
        (self.root / "migration-state.json").write_text(json.dumps(receipt), encoding="utf-8")
        restarted = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.FAILED, restarted.state)
        self.assertTrue(restarted.retryable)

    def test_concurrent_apply_runs_once(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        results = []

        def run() -> None:
            results.append(coordinator.apply_confirmed().state)

        threads = [threading.Thread(target=run), threading.Thread(target=run)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([MigrationState.COMPLETE, MigrationState.COMPLETE], results)
        self.assertEqual(1, json.loads((self.root / "migration-state.json").read_text())["attempt"])

    def test_registry_lineage_discovers_one_later_selected_major(self) -> None:
        MigrationFixture(self.root).write()
        first = MigrationCoordinator(self.root)
        first.preflight()
        first.apply_confirmed()

        marker = self.root / "current-v2.marker"

        def fingerprint(config_dir: Path) -> bool:
            return (config_dir / "settings.cfg").is_file() and not marker.exists()

        def apply(config_dir: Path, _backup_dir: Path) -> None:
            (config_dir / marker.name).write_text("v2\n", encoding="utf-8")

        def validate(config_dir: Path) -> None:
            if (config_dir / marker.name).read_text(encoding="utf-8") != "v2\n":
                raise ValueError("v2 marker missing")

        second_spec = MigrationSpec(
            migration_id="current-v1-to-current-v2", order=200,
            source_schema="seedsync-current-v1", target_schema="seedsync-current-v2",
            features=(MigrationFeature("V2", "Fake selected-major transition used to prove lineage."),),
            fingerprint=fingerprint, apply=apply, validate=validate,
        )
        registry = default_migration_registry() + (second_spec,)
        upgraded_binary = MigrationCoordinator(self.root, registry)
        pending = upgraded_binary.preflight()
        self.assertEqual(MigrationState.REQUIRED, pending.state)
        self.assertEqual(second_spec.migration_id, pending.migration_id)
        self.assertFalse(pending.allows_normal_startup)

        completed = upgraded_binary.apply_confirmed()
        self.assertEqual(MigrationState.COMPLETE, completed.state)
        receipt = json.loads((self.root / "migration-state.json").read_text(encoding="utf-8"))
        self.assertEqual("seedsync-current-v2", receipt["current_schema"])
        self.assertEqual(
            ["original-v0.8.6-to-current-v1", "current-v1-to-current-v2"],
            receipt["applied_migrations"],
        )
        self.assertEqual(MigrationState.COMPLETE, MigrationCoordinator(self.root, registry).preflight().state)

    def test_required_orphan_lock_is_reclaimed_before_apply(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        required = coordinator.preflight()
        lock_path = self.root / ".migration.lock"
        lock_path.write_text(json.dumps({
            "lock_version": 1,
            "pid": 99999999,
            "hostname": socket.gethostname(),
            "migration_id": required.migration_id,
            "created_at": "2000-01-01T00:00:00+00:00",
        }), encoding="utf-8")

        recovered = coordinator.preflight()
        self.assertEqual(MigrationState.REQUIRED, recovered.state)
        self.assertTrue(lock_path.exists())
        self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)

    def test_completed_orphan_lock_is_reclaimed_before_advancing_lineage(self) -> None:
        MigrationFixture(self.root).write()
        first = MigrationCoordinator(self.root)
        first.preflight()
        completed = first.apply_confirmed()
        lock_path = self.root / ".migration.lock"
        lock_path.write_text(json.dumps({
            "lock_version": 1,
            "pid": 99999999,
            "hostname": socket.gethostname(),
            "migration_id": completed.migration_id,
            "created_at": "2000-01-01T00:00:00+00:00",
        }), encoding="utf-8")
        marker = self.root / "current-v2.marker"
        second_spec = MigrationSpec(
            migration_id="current-v1-to-current-v2", order=200,
            source_schema="seedsync-current-v1", target_schema="seedsync-current-v2",
            features=(MigrationFeature("V2", "Future selected-major lineage regression."),),
            fingerprint=lambda _config_dir: not marker.exists(),
            apply=lambda _config_dir, _backup_dir: marker.write_text("v2\n", encoding="utf-8"),
            validate=lambda _config_dir: None,
        )

        pending = MigrationCoordinator(self.root, default_migration_registry() + (second_spec,)).preflight()
        self.assertEqual(MigrationState.REQUIRED, pending.state)
        self.assertEqual(second_spec.migration_id, pending.migration_id)
        self.assertTrue(lock_path.exists())

    def test_unvalidated_nonrunning_locks_remain_and_block_apply(self) -> None:
        cases = {
            "live": lambda migration_id: {
                "lock_version": 1, "pid": os.getpid(), "hostname": socket.gethostname(),
                "migration_id": migration_id,
            },
            "foreign": lambda migration_id: {
                "lock_version": 1, "pid": 99999999, "hostname": "another-host.example",
                "migration_id": migration_id,
            },
            "mismatched": lambda _migration_id: {
                "lock_version": 1, "pid": 99999999, "hostname": socket.gethostname(),
                "migration_id": "another-migration",
            },
            "malformed": lambda _migration_id: None,
        }
        for name, payload_factory in cases.items():
            with self.subTest(name=name):
                root = self.root / name
                MigrationFixture(root).write()
                coordinator = MigrationCoordinator(root)
                required = coordinator.preflight()
                lock_path = root / ".migration.lock"
                payload = payload_factory(required.migration_id)
                lock_path.write_text("not-json" if payload is None else json.dumps(payload), encoding="utf-8")
                excluded_locks = {".migration.lock", ".seedsync.runtime.lock"}
                before = {
                    item.name: item.read_bytes()
                    for item in root.iterdir() if item.name not in excluded_locks
                }

                self.assertEqual(MigrationState.REQUIRED, coordinator.preflight().state)
                with self.assertRaises(MigrationBlockedError):
                    coordinator.apply_confirmed()
                self.assertTrue(lock_path.exists())
                self.assertFalse((root / "path_pairs.json").exists())
                self.assertEqual(before, {
                    item.name: item.read_bytes()
                    for item in root.iterdir() if item.name not in excluded_locks
                })

    def test_orphaned_running_lock_becomes_retryable_and_can_resume(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        required = coordinator.preflight()
        self._write_running_metadata(self.root, required)
        (self.root / ".migration.lock").write_text(json.dumps({
            "lock_version": 1,
            "pid": 99999999,
            "hostname": socket.gethostname(),
            "migration_id": required.migration_id,
            "created_at": "2000-01-01T00:00:00+00:00",
        }), encoding="utf-8")

        restarted = MigrationCoordinator(self.root)
        decision = restarted.preflight()
        self.assertEqual(MigrationState.FAILED, decision.state)
        self.assertTrue(decision.retryable)
        self.assertTrue((self.root / ".migration.lock").exists())
        self.assertEqual(MigrationState.COMPLETE, restarted.apply_confirmed(retry=True).state)

    def test_active_running_lock_is_not_reclaimed(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        required = coordinator.preflight()
        self._write_running_metadata(self.root, required)
        lock_path = self.root / ".migration.lock"
        lock_path.write_text(json.dumps({
            "lock_version": 1,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "migration_id": required.migration_id,
            "created_at": "2000-01-01T00:00:00+00:00",
        }), encoding="utf-8")

        decision = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.RUNNING, decision.state)
        self.assertTrue(lock_path.exists())

    def test_malformed_and_foreign_host_running_locks_fail_safe(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        required = coordinator.preflight()
        self._write_running_metadata(self.root, required)
        lock_path = self.root / ".migration.lock"

        lock_path.write_text("not-json", encoding="utf-8")
        malformed = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.RUNNING, malformed.state)
        self.assertFalse(malformed.retryable)
        self.assertTrue(lock_path.exists())

        lock_path.write_text(json.dumps({
            "lock_version": 1,
            "pid": 99999999,
            "hostname": "another-host.example",
            "migration_id": required.migration_id,
            "created_at": "2000-01-01T00:00:00+00:00",
        }), encoding="utf-8")
        foreign = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.RUNNING, foreign.state)
        self.assertFalse(foreign.retryable)
        self.assertTrue(lock_path.exists())

    def test_symlinked_legacy_input_fails_closed(self) -> None:
        MigrationFixture(self.root).write()
        outside = self.root.parent / (self.root.name + "-outside-settings")
        outside.write_text((self.root / "settings.cfg").read_text(encoding="utf-8"), encoding="utf-8")
        try:
            (self.root / "settings.cfg").unlink()
            try:
                (self.root / "settings.cfg").symlink_to(outside)
            except OSError as exc:
                self.skipTest("symlink creation is unavailable: {}".format(exc))
            decision = MigrationCoordinator(self.root).preflight()
            self.assertEqual(MigrationState.FAILED, decision.state)
            self.assertFalse(decision.retryable)
        finally:
            outside.unlink(missing_ok=True)

    def test_hostile_unpublished_backup_manifest_is_rejected_before_mutation(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        required = coordinator.preflight()
        backup = self.root / "migration-backups" / required.migration_id
        backup.mkdir(parents=True)
        (backup / "manifest.json").write_text(json.dumps({
            "manifest_version": 1,
            "migration_id": required.migration_id,
            "files": [{"name": "../settings.cfg", "size": 1, "sha256": "0" * 64}],
        }), encoding="utf-8")
        with self.assertRaises(MigrationBlockedError) as context:
            coordinator.apply_confirmed()
        self.assertEqual(MigrationState.FAILED, context.exception.decision.state)
        self.assertFalse((self.root / "path_pairs.json").exists())

    def test_conflicting_published_backup_data_is_never_reused_or_overwritten(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        required = coordinator.preflight()
        backup = self.root / "migration-backups" / required.migration_id
        with migration_coordinator._root_transaction(self.root, coordinator._root_identity):
            migration_coordinator._safe_directory(
                backup, self.root, create=True, private=True,
            )
            migration_coordinator._write_private_backup(
                backup / "settings.cfg", (self.root / "settings.cfg").read_bytes(), self.root,
            )

        partial_content = (backup / "settings.cfg").read_bytes()
        with self.assertRaises(MigrationBlockedError):
            coordinator.apply_confirmed()
        self.assertEqual(partial_content, (backup / "settings.cfg").read_bytes())
        self.assertFalse((self.root / "path_pairs.json").exists())

        conflicting_root = self.root / "conflicting"
        MigrationFixture(conflicting_root).write()
        conflicting = MigrationCoordinator(conflicting_root)
        required = conflicting.preflight()
        backup = conflicting_root / "migration-backups" / required.migration_id
        with migration_coordinator._root_transaction(conflicting_root, conflicting._root_identity):
            migration_coordinator._safe_directory(
                backup, conflicting_root, create=True, private=True,
            )
            migration_coordinator._write_private_backup(
                backup / "settings.cfg", b"different", conflicting_root,
            )
        with self.assertRaises(MigrationBlockedError):
            conflicting.apply_confirmed()
        self.assertEqual(b"different", (backup / "settings.cfg").read_bytes())
        self.assertFalse((conflicting_root / "path_pairs.json").exists())

    def test_backup_files_are_private_and_descriptor_reads_do_not_reopen_paths(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        with patch.object(Path, "read_bytes", side_effect=AssertionError("path was reopened by name")):
            self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)

        backup = self._recorded_backup(self.root)
        for name in ("settings.cfg", "controller.persist", "autoqueue.persist", "manifest.json"):
            path = backup / name if name == "manifest.json" else backup / "data" / name
            self.assertTrue(path.is_file())
            if os.name == "posix":
                self.assertEqual(0, stat.S_IMODE(path.stat().st_mode) & 0o077)

    def test_atomic_mutations_remain_anchored_during_parent_replacement_attempt(self) -> None:
        config_root = self.root / "config"
        moved_root = self.root / "moved-config"
        MigrationFixture(config_root).write()
        original_replace = os.replace
        committed_names = []

        def replace_with_parent_attack(source, destination, *args, **kwargs):
            parent_was_moved = False
            original_replace(config_root, moved_root)
            parent_was_moved = True
            try:
                result = original_replace(source, destination, *args, **kwargs)
                committed_names.append(Path(destination).name)
                return result
            finally:
                if parent_was_moved:
                    original_replace(moved_root, config_root)

        if os.name == "nt":
            from migration.coordinator import _windows_rename_fd

            def windows_rename_with_parent_attack(descriptor, directory_handle, target_name):
                parent_was_moved = False
                try:
                    original_replace(config_root, moved_root)
                    parent_was_moved = True
                except PermissionError:
                    pass
                try:
                    _windows_rename_fd(descriptor, directory_handle, target_name)
                    committed_names.append(target_name)
                finally:
                    if parent_was_moved:
                        original_replace(moved_root, config_root)

            patcher = patch(
                "migration.coordinator._windows_rename_fd", side_effect=windows_rename_with_parent_attack,
            )
        else:
            patcher = patch("migration.coordinator.os.replace", side_effect=replace_with_parent_attack)

        with patcher:
            coordinator = MigrationCoordinator(config_root)
            self.assertEqual(MigrationState.REQUIRED, coordinator.preflight().state)
            self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)

        self.assertTrue((config_root / "migration-state.json").is_file())
        self.assertTrue((self._recorded_backup(config_root) / "manifest.json").is_file())
        self.assertTrue((config_root / "path_pairs.json").is_file())
        self.assertTrue({
            "migration-state.json", "settings.cfg", "path_pairs.json",
            "controller.persist", "autoqueue.persist",
        }.issubset(set(committed_names)))

    def test_root_identity_swap_before_next_anchor_fails_without_replacement_mutation(self) -> None:
        config_root = self.root / "config"
        original_root = self.root / "original-config"
        replacement_root = self.root / "replacement-config"
        MigrationFixture(config_root).write()
        replacement_root.mkdir()
        (replacement_root / "replacement.marker").write_text("untouched", encoding="utf-8")
        coordinator = MigrationCoordinator(config_root)
        self.assertEqual(MigrationState.REQUIRED, coordinator.preflight().state)

        os.replace(config_root, original_root)
        os.replace(replacement_root, config_root)
        try:
            with self.assertRaises(ValueError):
                coordinator.apply_confirmed()
            self.assertEqual("untouched", (config_root / "replacement.marker").read_text(encoding="utf-8"))
            self.assertFalse((config_root / "migration-state.json").exists())
        finally:
            os.replace(config_root, replacement_root)
            os.replace(original_root, config_root)

    def test_root_swap_between_identity_capture_and_transaction_fails_closed(self) -> None:
        config_root = self.root / "config"
        original_root = self.root / "original-config"
        replacement_root = self.root / "replacement-config"
        MigrationFixture(config_root).write()
        replacement_root.mkdir()
        (replacement_root / "replacement.marker").write_text("untouched", encoding="utf-8")
        original_capture = migration_coordinator._capture_root_identity
        calls = 0

        def capture_then_swap(root):
            nonlocal calls
            identity = original_capture(root)
            calls += 1
            if calls == 1:
                os.replace(config_root, original_root)
                os.replace(replacement_root, config_root)
            return identity

        try:
            with patch("migration.coordinator._capture_root_identity", side_effect=capture_then_swap):
                with self.assertRaises(ValueError):
                    MigrationCoordinator(config_root).preflight()
            self.assertEqual("untouched", (config_root / "replacement.marker").read_text(encoding="utf-8"))
            self.assertFalse((config_root / "migration-state.json").exists())
        finally:
            os.replace(config_root, replacement_root)
            os.replace(original_root, config_root)

    def test_new_and_reused_backup_directories_are_private(self) -> None:
        for state in ("new",):
            with self.subTest(state=state):
                root = self.root / state
                MigrationFixture(root).write()
                coordinator = MigrationCoordinator(root)
                coordinator.preflight()
                backup_root = root / "migration-backups"
                if os.name == "nt":
                    original_restrict = migration_coordinator._restrict_windows_handle_to_owner
                    restricted_handles = []

                    def record_restriction(handle):
                        restricted_handles.append(handle)
                        return original_restrict(handle)

                    patcher = patch(
                        "migration.coordinator._restrict_windows_handle_to_owner",
                        side_effect=record_restriction,
                    )
                else:
                    patcher = patch("migration.coordinator._utc_now", wraps=migration_coordinator._utc_now)

                with patcher:
                    self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)

                retained_backup = self._recorded_backup(root)

                if os.name == "posix":
                    self.assertEqual(0, stat.S_IMODE(backup_root.stat().st_mode) & 0o077)
                    self.assertEqual(0, stat.S_IMODE(retained_backup.stat().st_mode) & 0o077)
                else:
                    self.assertGreaterEqual(len(restricted_handles), 1)

    @unittest.skipUnless(os.name == "nt", "Windows ACL regression")
    def test_insecure_existing_backup_objects_fail_before_secret_writes(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        backup = self.root / "migration-backups"
        backup.mkdir(parents=True)

        with self.assertRaises(MigrationBlockedError):
            coordinator.apply_confirmed()

        self.assertFalse((self.root / "path_pairs.json").exists())
        self.assertEqual([], list(backup.glob("*/manifest.json")))

    @unittest.skipUnless(os.name == "nt", "Windows native ABI regression")
    def test_windows_native_prototypes_creation_descriptor_and_handle_cleanup(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32, advapi32, ntdll = migration_coordinator._windows_api()
        for function in (
            kernel32.CreateFileW, kernel32.GetFileInformationByHandle,
            kernel32.GetFileInformationByHandleEx, kernel32.CloseHandle,
            kernel32.GetFinalPathNameByHandleW, kernel32.SetFileInformationByHandle,
            kernel32.LocalFree, kernel32.GetProcessHandleCount,
            advapi32.GetSecurityInfo, advapi32.SetSecurityInfo,
            advapi32.GetTokenInformation, advapi32.ConvertSidToStringSidW,
            advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW,
            advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW,
            ntdll.NtCreateFile, ntdll.NtSetInformationFile,
        ):
            self.assertIsNotNone(function.argtypes)
            self.assertIsNotNone(function.restype)

        class ObjectAttributes(ctypes.Structure):
            _fields_ = (
                ("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", wintypes.LPVOID), ("Attributes", wintypes.ULONG),
                ("SecurityDescriptor", wintypes.LPVOID), ("SecurityQualityOfService", wintypes.LPVOID),
            )

        original_create = ntdll.NtCreateFile
        creation_descriptors = []

        def record_create(*args):
            object_attributes = ctypes.cast(args[2], ctypes.POINTER(ObjectAttributes)).contents
            if args[7] == 2:  # FILE_CREATE
                creation_descriptors.append(bool(object_attributes.SecurityDescriptor))
            return original_create(*args)

        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        process = kernel32.GetCurrentProcess()
        before = wintypes.DWORD()
        self.assertTrue(kernel32.GetProcessHandleCount(process, ctypes.byref(before)))
        with patch.object(ntdll, "NtCreateFile", side_effect=record_create):
            self.assertEqual(MigrationState.COMPLETE, coordinator.apply_confirmed().state)

        after = wintypes.DWORD()
        self.assertTrue(kernel32.GetProcessHandleCount(process, ctypes.byref(after)))
        self.assertTrue(creation_descriptors)
        self.assertTrue(all(creation_descriptors))
        self.assertEqual(before.value, after.value)

    @unittest.skipUnless(os.name == "nt", "Windows ACL structure regression")
    def test_windows_structural_acl_rejects_nonbasic_extra_and_wrong_aces_before_backup_read(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32, advapi32, _ = migration_coordinator._windows_api()
        with migration_coordinator._windows_private_security() as (_, trustees):
            user_sid = trustees[0]
        variants = {
            "other-trustee": ("(A;;FR;;;WD)", None),
            "wrong-mask": ("(A;;FR;;;{})".format(user_sid), None),
            "object-allow": ("(OA;;FR;;;WD)", None),
            "callback-allow": ("(A;;FR;;;WD)", 0x09),
        }
        for name, (extra_ace, replacement_ace_type) in variants.items():
            with self.subTest(name=name):
                root = self.root / name
                MigrationFixture(root).write()
                coordinator = MigrationCoordinator(root)
                coordinator.preflight()
                backup = root / "migration-backups"
                backup.mkdir(parents=True)
                handle = kernel32.CreateFileW(
                    str(backup), 0x00060000, 0x7, None, 3, 0x02000000, None,
                )
                self.assertNotEqual(ctypes.c_void_p(-1).value, handle)
                security_descriptor = wintypes.LPVOID()
                sddl = "D:P(A;;FA;;;{}){}".format(user_sid, extra_ace)
                try:
                    self.assertTrue(advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                        sddl, 1, ctypes.byref(security_descriptor), None,
                    ))
                    dacl = wintypes.LPVOID()
                    present = wintypes.BOOL()
                    defaulted = wintypes.BOOL()
                    self.assertTrue(advapi32.GetSecurityDescriptorDacl(
                        security_descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted),
                    ))
                    if replacement_ace_type is not None:
                        ace = wintypes.LPVOID()
                        self.assertTrue(advapi32.GetAce(dacl, 1, ctypes.byref(ace)))
                        ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))[0] = replacement_ace_type
                    self.assertEqual(0, advapi32.SetSecurityInfo(
                        wintypes.HANDLE(handle), 1, 0x80000004, None, None, dacl, None,
                    ))
                finally:
                    if security_descriptor:
                        kernel32.LocalFree(security_descriptor)
                    kernel32.CloseHandle(wintypes.HANDLE(handle))

                with self.assertRaises(MigrationBlockedError):
                    coordinator.apply_confirmed()
                self.assertEqual([], list(backup.glob("*/manifest.json")))
                self.assertFalse((root / "path_pairs.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows native handle cleanup regression")
    def test_windows_post_create_verification_failures_close_file_and_directory_handles(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32, _, _ = migration_coordinator._windows_api()
        process = kernel32.GetCurrentProcess()
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()

        def handle_count():
            count = wintypes.DWORD()
            self.assertTrue(kernel32.GetProcessHandleCount(process, ctypes.byref(count)))
            return count.value

        with migration_coordinator._root_transaction(self.root, coordinator._root_identity):
            baseline = handle_count()
            with patch(
                "migration.coordinator._verify_windows_handle_owner_only",
                side_effect=PermissionError("injected file verification failure"),
            ):
                with self.assertRaises(PermissionError):
                    migration_coordinator._open_anchored(
                        self.root / "failed-private-file", self.root,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY, owner_control=True,
                    )
            self.assertEqual(baseline, handle_count())

            with migration_coordinator._mutation_parent(
                self.root / "failed-private-directory", self.root,
            ) as (parent_handle, _):
                directory_baseline = handle_count()
                with patch(
                    "migration.coordinator._verify_windows_handle_owner_only",
                    side_effect=PermissionError("injected directory verification failure"),
                ):
                    with self.assertRaises(PermissionError):
                        migration_coordinator._windows_open_directory(
                            parent_handle, "failed-private-directory", create=True, owner_control=True,
                        )
                self.assertEqual(directory_baseline, handle_count())

    def test_dangling_symlink_backup_input_and_manifest_are_rejected(self) -> None:
        MigrationFixture(self.root).write()
        optional_input = self.root / "api-keys.history.jsonl"
        try:
            optional_input.symlink_to(self.root / "missing-history")
        except OSError as exc:
            self.skipTest("symlink creation is unavailable: {}".format(exc))
        decision = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.FAILED, decision.state)
        self.assertFalse(decision.retryable)

        manifest_root = self.root / "manifest-link"
        MigrationFixture(manifest_root).write()
        coordinator = MigrationCoordinator(manifest_root)
        required = coordinator.preflight()
        backup = manifest_root / "migration-backups" / required.migration_id
        backup.mkdir(parents=True)
        (backup / "manifest.json").symlink_to(backup / "missing-manifest")
        with self.assertRaises(MigrationBlockedError) as context:
            coordinator.apply_confirmed()
        self.assertEqual(MigrationState.FAILED, context.exception.decision.state)

    def test_oversized_relevant_file_fails_closed(self) -> None:
        MigrationFixture(self.root).write()
        (self.root / "controller.persist").write_bytes(b" " * (16 * 1024 * 1024 + 1))
        decision = MigrationCoordinator(self.root).preflight()
        self.assertEqual(MigrationState.FAILED, decision.state)
        self.assertFalse(decision.retryable)

    def test_seedsync_gates_before_normal_loaders(self) -> None:
        MigrationFixture(self.root).write()
        argv = ["seedsync.py", "-c", str(self.root), "--html", str(self.root), "--scanfs", "scanfs"]
        with patch.object(sys, "argv", argv), patch.object(Config, "from_file") as config_loader:
            seedsync = Seedsync()
        config_loader.assert_not_called()
        self.assertEqual(MigrationState.REQUIRED, seedsync.migration_decision.state)
        self.assertTrue(hasattr(seedsync, "migration_runtime"))

    def test_blocked_startup_is_byte_preserving_for_required_failed_and_stale_states(self) -> None:
        roots = {
            "required": self.root / "required-startup",
            "failed": self.root / "failed-startup",
            "stale": self.root / "stale-startup",
        }
        MigrationFixture(roots["required"]).write()
        roots["failed"].mkdir()
        (roots["failed"] / "settings.cfg").write_text("[General]\nverbose=True\n", encoding="utf-8")
        MigrationFixture(roots["stale"]).write()
        stale_decision = MigrationCoordinator(roots["stale"]).preflight()
        self._write_running_metadata(roots["stale"], stale_decision)

        for name, root in roots.items():
            with self.subTest(name=name):
                before = self._tree_bytes(root)
                argv = ["seedsync.py", "-c", str(root), "--html", str(root), "--scanfs", "scanfs"]
                with patch.object(sys, "argv", argv), patch("seedsync.signal.signal"):
                    seedsync = Seedsync()
                self.assertFalse(seedsync.migration_decision.allows_normal_startup)
                self.assertEqual(before, self._tree_bytes(root))


if __name__ == "__main__":
    unittest.main()
