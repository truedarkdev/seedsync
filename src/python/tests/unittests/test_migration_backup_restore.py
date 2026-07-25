import json
import errno
import os
import socket
import subprocess
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import ServiceExit
from migration.backup_restore import (
    BackupRestoreError,
    create_retained_backup,
    infrastructure_exclusions,
    restore_backup,
    validate_backup,
    detect_nested_mounts,
)
from migration.runtime_exclusion import RuntimeExclusion
from migration.coordinator import (
    MigrationBlockedError,
    MigrationCoordinator,
    ValidatedBackupReader,
)
from seedsync import Seedsync
from tests.unittests.test_migration_coordinator import MigrationFixture


class TestMigrationBackupRestore(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_backup(self) -> Path:
        return create_retained_backup(
            self.root,
            migration_id="original-v0.8.6-to-current-v1",
            source_schema="original-v0.8.6",
            target_schema="seedsync-current-v1",
        )

    def test_recursive_backup_includes_hidden_unknown_and_modes_with_central_exclusions(self) -> None:
        (self.root / "nested" / ".hidden").mkdir(parents=True)
        (self.root / "nested" / ".hidden" / "history.jsonl").write_bytes(b"history\n")
        (self.root / ".auth-secret").write_bytes(b"secret")
        (self.root / "empty").mkdir()
        if os.name == "posix":
            (self.root / ".auth-secret").chmod(0o640)
            (self.root / "nested").chmod(0o750)
        (self.root / "migration-state.json").write_text("new receipt", encoding="utf-8")
        (self.root / ".migration.lock").write_text("lock", encoding="utf-8")
        (self.root / ".settings.cfg-deadbeef.tmp").write_text("temp", encoding="utf-8")

        backup = self._create_backup()
        manifest = validate_backup(backup, self.root)
        entries = {entry["path"]: entry for entry in manifest["entries"]}
        self.assertEqual(
            {".auth-secret", "empty", "nested", "nested/.hidden", "nested/.hidden/history.jsonl"},
            set(entries),
        )
        self.assertEqual(b"secret", (backup / "data" / ".auth-secret").read_bytes())
        self.assertEqual(sum(entry.get("size", 0) for entry in entries.values()), manifest["aggregate"]["total_size"])
        self.assertIn("migration-backups", infrastructure_exclusions())
        if os.name == "posix":
            self.assertEqual(0o640, entries[".auth-secret"]["mode"])
            self.assertEqual(0o750, entries["nested"]["mode"])
            self.assertEqual(0, stat.S_IMODE((backup / "data" / ".auth-secret").stat().st_mode) & 0o077)

    def test_symlink_and_case_collision_block_before_noninfrastructure_mutation(self) -> None:
        (self.root / "source").write_text("data", encoding="utf-8")
        try:
            (self.root / "link").symlink_to(self.root / "source")
        except OSError as exc:
            self.skipTest("symlink creation unavailable: {}".format(exc))
        with self.assertRaises(BackupRestoreError):
            self._create_backup()
        self.assertEqual(b"data", (self.root / "source").read_bytes())
        self.assertFalse((self.root / "migration-state.json").exists())

        (self.root / "link").unlink()
        (self.root / "Name").write_text("one", encoding="utf-8")
        try:
            (self.root / "name").write_text("two", encoding="utf-8")
        except OSError:
            self.skipTest("case-distinct files unavailable")
        if len([path for path in self.root.iterdir() if path.name.casefold() == "name"]) == 2:
            with self.assertRaises(BackupRestoreError):
                self._create_backup()

    def test_coordinator_backup_rejection_does_not_write_checkpoint_metadata(self) -> None:
        MigrationFixture(self.root).write()
        original = {
            path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()
        }
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_text("outside", encoding="utf-8")
        try:
            try:
                (self.root / "unknown-link").symlink_to(outside)
            except OSError as exc:
                self.skipTest("symlink creation unavailable: {}".format(exc))
            coordinator = MigrationCoordinator(self.root)
            self.assertEqual("required", coordinator.preflight().state.value)
            with self.assertRaises(MigrationBlockedError):
                coordinator.apply_confirmed()
            self.assertFalse((self.root / "migration-state.json").exists())
            self.assertFalse((self.root / "path_pairs.json").exists())
            self.assertEqual(original, {
                name: (self.root / name).read_bytes() for name in original
            })
            self.assertTrue((self.root / "unknown-link").is_symlink())
        finally:
            outside.unlink(missing_ok=True)

    def test_unique_backup_reuse_and_failed_publication_is_not_restorable(self) -> None:
        (self.root / "a").write_text("one", encoding="utf-8")
        first = self._create_backup()
        self.assertEqual(first, self._create_backup())
        (self.root / "a").write_text("two", encoding="utf-8")
        second = self._create_backup()
        self.assertNotEqual(first, second)
        self.assertEqual(b"one", (first / "data" / "a").read_bytes())

        another = self.root / "another"
        another.mkdir()
        (another / "file").write_text("value", encoding="utf-8")
        publication_target = (
            "migration.backup_restore._publish_posix_transaction"
            if os.name == "posix" else "migration.backup_restore._publish_directory"
        )
        with patch(
            publication_target,
            side_effect=OSError("injected publish failure"),
        ):
            with self.assertRaises(BackupRestoreError if os.name == "posix" else OSError):
                create_retained_backup(
                    another, migration_id="m", source_schema="old", target_schema="new",
                )
        candidates = list((another / "migration-backups").iterdir())
        if os.name == "posix":
            self.assertEqual([], candidates)
            self.assertTrue(create_retained_backup(
                another, migration_id="m", source_schema="old", target_schema="new",
            ).is_dir())
        else:
            self.assertTrue(candidates)
            self.assertTrue(all(
                path.name.startswith(".") and path.name.endswith(".staging")
                for path in candidates
            ))

    def test_backup_capacity_write_fsync_and_cross_device_failures_are_retryable(self) -> None:
        from migration.backup_restore import _fsync_directory

        failure_kinds = ("capacity", "write", "fsync", "cross-device")
        for failure_kind in failure_kinds:
            with self.subTest(failure_kind=failure_kind):
                case_root = self.root / failure_kind
                case_root.mkdir()
                (case_root / "a").write_text("old", encoding="utf-8")
                kwargs = {
                    "migration_id": "m", "source_schema": "old", "target_schema": "new",
                }
                if failure_kind == "capacity":
                    context = patch(
                        "migration.backup_restore.shutil.disk_usage",
                        return_value=type("Usage", (), {"free": 0})(),
                    )
                elif failure_kind == "write":
                    context = patch(
                        "migration.backup_restore._write_private_file",
                        side_effect=OSError(errno.ENOSPC, "injected write exhaustion"),
                    )
                elif failure_kind == "cross-device":
                    context = patch(
                        "migration.backup_restore._publish_posix_transaction"
                        if os.name == "posix"
                        else "migration.backup_restore._publish_directory_anchored",
                        side_effect=OSError(errno.EXDEV, "injected cross-device publication"),
                    )
                else:
                    failed = False

                    def fail_staging_fsync_once(path, config_root):
                        nonlocal failed
                        result = _fsync_directory(path, config_root)
                        if not failed and (
                            path.name == "staging"
                            or any(part.endswith(".staging") for part in path.parts)
                        ):
                            failed = True
                            raise OSError(errno.ENOSPC, "injected fsync exhaustion")
                        return result

                    context = patch(
                        "migration.backup_restore._fsync_directory",
                        side_effect=fail_staging_fsync_once,
                    )
                with context:
                    with self.assertRaises((BackupRestoreError, OSError)):
                        create_retained_backup(case_root, **kwargs)
                self.assertEqual("old", (case_root / "a").read_text(encoding="utf-8"))
                self.assertFalse(any(
                    not path.name.startswith(".")
                    for path in (case_root / "migration-backups").iterdir()
                ))
                self.assertTrue(create_retained_backup(case_root, **kwargs).is_dir())

    def test_backup_publication_never_replaces_an_existing_destination(self) -> None:
        from migration.backup_restore import _publish_directory

        backup_root = self.root / "migration-backups"
        backup_root.mkdir()
        staging = backup_root / ".candidate.staging"
        destination = backup_root / "candidate"
        staging.mkdir()
        destination.mkdir()
        (staging / "sentinel").write_text("staging", encoding="utf-8")
        (destination / "sentinel").write_text("published", encoding="utf-8")
        with self.assertRaises(BackupRestoreError):
            _publish_directory(staging, destination, self.root)
        self.assertEqual("staging", (staging / "sentinel").read_text(encoding="utf-8"))
        self.assertEqual("published", (destination / "sentinel").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "POSIX publication fallback regression")
    def test_renameat2_einval_uses_reserved_manifest_last_publication(self) -> None:
        (self.root / "a").write_text("retained", encoding="utf-8")
        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "bind filesystem rejects RENAME_NOREPLACE"),
        ):
            backup = self._create_backup()

        self.assertEqual("retained", (backup / "data" / "a").read_text(encoding="utf-8"))
        self.assertEqual(backup.name, validate_backup(backup, self.root)["backup_id"])
        self.assertFalse(any(
            path.name.startswith(".") and path.name.endswith(".staging")
            for path in backup.parent.iterdir()
        ))

    @unittest.skipUnless(os.name == "posix", "POSIX publication fallback regression")
    def test_reserved_fallback_loses_destination_race_without_overwrite(self) -> None:
        (self.root / "a").write_text("retained", encoding="utf-8")
        competing_destination: Path | None = None

        def create_competing_destination(destination):
            nonlocal competing_destination
            competing_destination = destination
            destination.mkdir(mode=0o700)
            (destination / "sentinel").write_text("competitor", encoding="utf-8")

        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        ), patch(
            "migration.backup_restore._before_fallback_publication_reservation",
            side_effect=create_competing_destination,
        ):
            with self.assertRaises(BackupRestoreError):
                self._create_backup()

        self.assertIsNotNone(competing_destination)
        assert competing_destination is not None
        self.assertEqual(
            "competitor", (competing_destination / "sentinel").read_text(encoding="utf-8"),
        )
        self.assertFalse((competing_destination / "manifest.json").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX publication fallback regression")
    def test_reserved_fallback_failure_restores_staging_and_removes_reservation(self) -> None:
        (self.root / "a").write_text("retained", encoding="utf-8")
        failed_destination: Path | None = None

        def fail_before_manifest(destination):
            nonlocal failed_destination
            failed_destination = destination
            raise OSError(errno.ENOSPC, "injected manifest publication failure")

        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        ), patch(
            "migration.backup_restore._before_fallback_manifest_publish",
            side_effect=fail_before_manifest,
        ):
            with self.assertRaises(BackupRestoreError):
                self._create_backup()

        self.assertIsNotNone(failed_destination)
        assert failed_destination is not None
        self.assertFalse(failed_destination.exists())
        transactions = list((self.root / "migration-backups").glob(".publication-txn-*"))
        self.assertEqual(1, len(transactions))
        staging = transactions[0] / "staging"
        self.assertTrue((staging / "data" / "a").is_file())
        self.assertTrue((staging / "manifest.json").is_file())

        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        ):
            self.assertTrue(self._create_backup().is_dir())

    @unittest.skipUnless(os.name == "posix", "POSIX publication crash recovery regression")
    def test_reserved_fallback_hard_crash_pair_recovers_and_publishes(self) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        (self.root / "a").write_text("retained", encoding="utf-8")
        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        ), patch(
            "migration.backup_restore._before_fallback_manifest_publish",
            side_effect=SimulatedHardCrash(),
        ):
            with self.assertRaises(SimulatedHardCrash):
                self._create_backup()

        backup_root = self.root / "migration-backups"
        visible = [path for path in backup_root.iterdir() if not path.name.startswith(".")]
        transactions = list(backup_root.glob(".publication-txn-*"))
        self.assertEqual(1, len(visible))
        self.assertEqual(1, len(transactions))
        self.assertTrue((transactions[0] / "staging").is_dir())
        intent = transactions[0] / "intent.json"
        self.assertTrue(intent.is_file())
        self.assertEqual(0, stat.S_IMODE(intent.stat().st_mode) & 0o077)

        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        ):
            published = self._create_backup()
        self.assertEqual("retained", (published / "data" / "a").read_text(encoding="utf-8"))
        self.assertEqual([published], [
            path for path in backup_root.iterdir() if not path.name.startswith(".")
        ])
        self.assertEqual([], list(backup_root.glob(".publication-txn-*")))

    @unittest.skipUnless(os.name == "posix", "POSIX publication crash matrix")
    def test_reserved_fallback_crash_matrix_converges_after_every_transition(self) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        transitions = (
            "txn_durable",
            "intent_temp_partial",
            "intent_temp_durable",
            "intent_durable",
            "reservation_durable",
            "data_durable",
            "manifest_durable",
            "staging_removed",
            "intent_removed",
            "txn_removed",
        )
        for transition in transitions:
            with self.subTest(transition=transition), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "a").write_text("retained", encoding="utf-8")
                crashed = False

                def crash_once(name):
                    nonlocal crashed
                    if name == transition and not crashed:
                        crashed = True
                        raise SimulatedHardCrash()

                with patch(
                    "migration.backup_restore._rename_directory_noreplace",
                    side_effect=OSError(errno.EINVAL, "unsupported"),
                ), patch(
                    "migration.backup_restore._publication_transition",
                    side_effect=crash_once,
                ):
                    with self.assertRaises(SimulatedHardCrash):
                        create_retained_backup(
                            root,
                            migration_id="original-v0.8.6-to-current-v1",
                            source_schema="original-v0.8.6",
                            target_schema="seedsync-current-v1",
                        )
                self.assertTrue(crashed)

                with patch(
                    "migration.backup_restore._rename_directory_noreplace",
                    side_effect=OSError(errno.EINVAL, "unsupported"),
                ):
                    published = create_retained_backup(
                        root,
                        migration_id="original-v0.8.6-to-current-v1",
                        source_schema="original-v0.8.6",
                        target_schema="seedsync-current-v1",
                    )
                backup_root = root / "migration-backups"
                self.assertEqual("retained", (published / "data" / "a").read_text(encoding="utf-8"))
                self.assertEqual(published.name, validate_backup(published, root)["backup_id"])
                self.assertEqual([published], [
                    path for path in backup_root.iterdir() if not path.name.startswith(".")
                ])
                self.assertEqual([], list(backup_root.glob(".publication-txn-*")))

    @unittest.skipUnless(os.name == "posix", "POSIX recovery crash matrix")
    def test_reserved_fallback_recovery_crash_matrix_converges(self) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        cases = (
            ("txn_durable", "recovery_txn_removed", "empty-envelope"),
            ("intent_temp_partial", "recovery_txn_removed", "partial-intent"),
            ("intent_temp_durable", "recovery_txn_removed", "temporary-intent"),
            ("intent_durable", "recovery_staging_removed", "intent-only"),
            ("intent_durable", "recovery_intent_removed", "intent-only"),
            ("intent_durable", "recovery_txn_removed", "intent-only"),
            ("reservation_durable", "recovery_reservation_removed", "reserved"),
            ("reservation_durable", "recovery_staging_removed", "reserved"),
            ("reservation_durable", "recovery_intent_removed", "reserved"),
            ("reservation_durable", "recovery_txn_removed", "reserved"),
            ("data_durable", "recovery_manifest_durable", "data-moved"),
            ("manifest_durable", "recovery_staging_removed", "committed"),
            ("manifest_durable", "recovery_intent_removed", "committed"),
            ("manifest_durable", "recovery_txn_removed", "committed"),
        )
        for initial_transition, recovery_transition, phase in cases:
            with self.subTest(phase=phase, transition=recovery_transition), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "a").write_text("retained", encoding="utf-8")

                def crash_initial(name):
                    if name == initial_transition:
                        raise SimulatedHardCrash()

                with patch(
                    "migration.backup_restore._rename_directory_noreplace",
                    side_effect=OSError(errno.EINVAL, "unsupported"),
                ), patch(
                    "migration.backup_restore._publication_transition",
                    side_effect=crash_initial,
                ):
                    with self.assertRaises(SimulatedHardCrash):
                        create_retained_backup(
                            root,
                            migration_id="original-v0.8.6-to-current-v1",
                            source_schema="original-v0.8.6",
                            target_schema="seedsync-current-v1",
                        )

                recovery_crashed = False

                def crash_recovery(name):
                    nonlocal recovery_crashed
                    if name == recovery_transition and not recovery_crashed:
                        recovery_crashed = True
                        raise SimulatedHardCrash()

                with patch(
                    "migration.backup_restore._rename_directory_noreplace",
                    side_effect=OSError(errno.EINVAL, "unsupported"),
                ), patch(
                    "migration.backup_restore._publication_transition",
                    side_effect=crash_recovery,
                ):
                    with self.assertRaises(SimulatedHardCrash):
                        create_retained_backup(
                            root,
                            migration_id="original-v0.8.6-to-current-v1",
                            source_schema="original-v0.8.6",
                            target_schema="seedsync-current-v1",
                        )
                self.assertTrue(recovery_crashed)

                with patch(
                    "migration.backup_restore._rename_directory_noreplace",
                    side_effect=OSError(errno.EINVAL, "unsupported"),
                ):
                    published = create_retained_backup(
                        root,
                        migration_id="original-v0.8.6-to-current-v1",
                        source_schema="original-v0.8.6",
                        target_schema="seedsync-current-v1",
                    )
                backup_root = root / "migration-backups"
                self.assertEqual("retained", (published / "data" / "a").read_text(encoding="utf-8"))
                self.assertEqual(published.name, validate_backup(published, root)["backup_id"])
                self.assertEqual([published], [
                    path for path in backup_root.iterdir() if not path.name.startswith(".")
                ])
                self.assertEqual([], list(backup_root.glob(".publication-txn-*")))

    @unittest.skipUnless(os.name == "posix", "POSIX atomic publication crash regression")
    def test_atomic_publication_crash_leaves_only_recoverable_empty_envelope(self) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        (self.root / "a").write_text("retained", encoding="utf-8")

        def crash_after_atomic_publish(name):
            if name == "atomic_publish_durable":
                raise SimulatedHardCrash()

        with patch(
            "migration.backup_restore._publication_transition",
            side_effect=crash_after_atomic_publish,
        ):
            with self.assertRaises(SimulatedHardCrash):
                self._create_backup()

        backup_root = self.root / "migration-backups"
        transaction = next(backup_root.glob(".publication-txn-*"))
        self.assertEqual([], list(transaction.iterdir()))
        published = self._create_backup()
        self.assertEqual("retained", (published / "data" / "a").read_text(encoding="utf-8"))
        self.assertEqual([], list(backup_root.glob(".publication-txn-*")))

    @unittest.skipUnless(os.name == "posix", "POSIX pre-intent ownership regression")
    def test_preintent_envelope_cleanup_never_touches_visible_foreign_directory(self) -> None:
        from migration.backup_restore import _recover_reserved_publications

        backup_root = self.root / "migration-backups"
        backup_root.mkdir(mode=0o700)
        foreign = backup_root / "foreign-visible-directory"
        foreign.mkdir(mode=0o700)
        (foreign / "sentinel").write_text("foreign", encoding="utf-8")
        generation = "a" * 32
        transaction = backup_root / (".publication-txn-" + generation)
        transaction.mkdir(mode=0o700)
        (transaction / "staging").mkdir(mode=0o700)
        temporary_intent = transaction / (".intent-" + generation + ".tmp")
        temporary_intent.write_bytes(b'{"partial":')
        temporary_intent.chmod(0o600)

        _recover_reserved_publications(
            backup_root, self.root, "original-v0.8.6-to-current-v1",
        )

        self.assertFalse(transaction.exists())
        self.assertEqual("foreign", (foreign / "sentinel").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "POSIX publication proof regression")
    def test_tampered_publication_recovery_proof_blocks_without_cleanup(self) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        (self.root / "a").write_text("retained", encoding="utf-8")
        fallback = patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        )
        with fallback, patch(
            "migration.backup_restore._before_fallback_manifest_publish",
            side_effect=SimulatedHardCrash(),
        ):
            with self.assertRaises(SimulatedHardCrash):
                self._create_backup()

        backup_root = self.root / "migration-backups"
        destination = next(path for path in backup_root.iterdir() if not path.name.startswith("."))
        transaction = next(backup_root.glob(".publication-txn-*"))
        staging = transaction / "staging"
        intent = transaction / "intent.json"
        proof = json.loads(intent.read_text(encoding="utf-8"))
        proof["manifest_sha256"] = "0" * 64
        intent.write_text(json.dumps(proof), encoding="utf-8")
        intent.chmod(0o600)

        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        ):
            with self.assertRaises(BackupRestoreError):
                self._create_backup()
        self.assertTrue(destination.is_dir())
        self.assertTrue(staging.is_dir())
        self.assertTrue((destination / "data" / "a").is_file())

    @unittest.skipUnless(os.name == "posix", "POSIX publication generation regression")
    def test_mismatched_transaction_generation_blocks_with_visible_destination(self) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        (self.root / "a").write_text("retained", encoding="utf-8")
        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        ), patch(
            "migration.backup_restore._before_fallback_manifest_publish",
            side_effect=SimulatedHardCrash(),
        ):
            with self.assertRaises(SimulatedHardCrash):
                self._create_backup()

        backup_root = self.root / "migration-backups"
        destination = next(path for path in backup_root.iterdir() if not path.name.startswith("."))
        transaction = next(backup_root.glob(".publication-txn-*"))
        intent = transaction / "intent.json"
        record = json.loads(intent.read_text(encoding="utf-8"))
        record["generation"] = "0" * 32
        intent.write_text(json.dumps(record), encoding="utf-8")
        intent.chmod(0o600)

        with self.assertRaises(BackupRestoreError):
            self._create_backup()
        self.assertTrue(destination.is_dir())
        self.assertTrue(transaction.is_dir())

    @unittest.skipUnless(os.name == "posix", "POSIX publication proof regression")
    def test_missing_publication_recovery_proof_blocks_without_cleanup(self) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        (self.root / "a").write_text("retained", encoding="utf-8")
        with patch(
            "migration.backup_restore._rename_directory_noreplace",
            side_effect=OSError(errno.EINVAL, "unsupported"),
        ), patch(
            "migration.backup_restore._before_fallback_manifest_publish",
            side_effect=SimulatedHardCrash(),
        ):
            with self.assertRaises(SimulatedHardCrash):
                self._create_backup()

        backup_root = self.root / "migration-backups"
        destination = next(path for path in backup_root.iterdir() if not path.name.startswith("."))
        transaction = next(backup_root.glob(".publication-txn-*"))
        staging = transaction / "staging"
        (transaction / "intent.json").unlink()

        with self.assertRaises(BackupRestoreError):
            self._create_backup()
        self.assertTrue(destination.is_dir())
        self.assertTrue(staging.is_dir())

    @unittest.skipUnless(os.name == "posix", "POSIX foreign publication regression")
    def test_foreign_manifestless_destination_is_never_auto_removed(self) -> None:
        (self.root / "a").write_text("retained", encoding="utf-8")
        backup_root = self.root / "migration-backups"
        backup_root.mkdir(mode=0o700)
        foreign = backup_root / (
            "original-v0.8.6-to-current-v1-" + "f" * 32
        )
        foreign.mkdir(mode=0o700)
        (foreign / "sentinel").write_text("foreign", encoding="utf-8")

        with self.assertRaises(BackupRestoreError):
            self._create_backup()
        self.assertEqual("foreign", (foreign / "sentinel").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "POSIX foreign publication regression")
    def test_orphan_staging_and_malformed_intent_are_never_auto_removed(self) -> None:
        for artifact_kind in ("staging", "intent"):
            with self.subTest(artifact_kind=artifact_kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "a").write_text("retained", encoding="utf-8")
                backup_root = root / "migration-backups"
                backup_root.mkdir(mode=0o700)
                if artifact_kind == "staging":
                    artifact = backup_root / (
                        ".original-v0.8.6-to-current-v1-" + "f" * 32 + ".staging"
                    )
                    artifact.mkdir(mode=0o700)
                    (artifact / "sentinel").write_text("foreign", encoding="utf-8")
                else:
                    artifact = backup_root / ".publication-txn-not-an-owned-generation"
                    artifact.mkdir(mode=0o700)
                    (artifact / "sentinel").write_text("foreign", encoding="utf-8")

                with self.assertRaises(BackupRestoreError):
                    create_retained_backup(
                        root,
                        migration_id="original-v0.8.6-to-current-v1",
                        source_schema="original-v0.8.6",
                        target_schema="seedsync-current-v1",
                    )
                self.assertTrue(artifact.exists())
                if artifact_kind == "staging":
                    self.assertEqual("foreign", (artifact / "sentinel").read_text(encoding="utf-8"))

    def test_restore_capacity_write_fsync_and_cross_device_failures_can_retry(self) -> None:
        from migration.backup_restore import _fsync_directory

        for failure_kind in ("capacity", "write", "fsync", "cross-device"):
            with self.subTest(failure_kind=failure_kind):
                case_root = self.root / ("restore-" + failure_kind)
                case_root.mkdir()
                (case_root / "a").write_text("old-a", encoding="utf-8")
                (case_root / "b").write_text("old-b", encoding="utf-8")
                backup = create_retained_backup(
                    case_root, migration_id="m", source_schema="old", target_schema="new",
                )
                (case_root / "a").write_text("current-a", encoding="utf-8")
                (case_root / "extra").write_text("current-extra", encoding="utf-8")
                if failure_kind == "capacity":
                    context = patch(
                        "migration.backup_restore.shutil.disk_usage",
                        return_value=type("Usage", (), {"free": 0})(),
                    )
                elif failure_kind == "write":
                    context = patch(
                        "migration.backup_restore._write_private_file",
                        side_effect=OSError(errno.ENOSPC, "injected restore write exhaustion"),
                    )
                elif failure_kind == "cross-device":
                    context = patch(
                        "migration.backup_restore._install_staged_file_anchored",
                        side_effect=OSError(errno.EXDEV, "injected restore cross-device install"),
                    )
                else:
                    failed = False

                    def fail_restore_stage_fsync_once(path, config_root):
                        nonlocal failed
                        result = _fsync_directory(path, config_root)
                        if not failed and any(
                            part.startswith(".migration-restore-") and part.endswith(".staging")
                            for part in path.parts
                        ):
                            failed = True
                            raise OSError(errno.ENOSPC, "injected restore fsync exhaustion")
                        return result

                    context = patch(
                        "migration.backup_restore._fsync_directory",
                        side_effect=fail_restore_stage_fsync_once,
                    )
                with context:
                    with self.assertRaises((BackupRestoreError, OSError)):
                        restore_backup(case_root, backup)
                if failure_kind != "cross-device":
                    self.assertEqual("current-a", (case_root / "a").read_text(encoding="utf-8"))
                    self.assertEqual("current-extra", (case_root / "extra").read_text(encoding="utf-8"))
                    self.assertFalse((case_root / ".migration-restore.json").exists())
                restore_backup(case_root, backup)
                self.assertEqual("old-a", (case_root / "a").read_text(encoding="utf-8"))
                self.assertEqual("old-b", (case_root / "b").read_text(encoding="utf-8"))
                self.assertFalse((case_root / "extra").exists())

    def test_round_trip_removes_extras_retains_backup_and_is_idempotent(self) -> None:
        MigrationFixture(self.root).write()
        (self.root / "logs").mkdir()
        (self.root / "logs" / "history.jsonl").write_text("old-history\n", encoding="utf-8")
        (self.root / ".auth").write_text("old-auth", encoding="utf-8")
        original = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*") if path.is_file()
        }
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        coordinator.apply_confirmed()
        receipt = json.loads((self.root / "migration-state.json").read_text(encoding="utf-8"))
        backup = self.root / receipt["backup"]

        (self.root / "settings.cfg").write_text("mutated", encoding="utf-8")
        (self.root / "post-migration.tmp").write_text("extra", encoding="utf-8")
        (self.root / "logs" / "history.jsonl").unlink()
        outside = self.root.parent / (self.root.name + "-restore-outside")
        outside.write_text("outside", encoding="utf-8")
        extra_link = self.root / "post-migration-link"
        try:
            extra_link.symlink_to(outside)
        except OSError:
            extra_link = None
        result = coordinator.restore_offline(backup.name)
        self.assertEqual(len(original), result["files"])
        restored = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
            and "migration-backups" not in path.parts
            and path.name != ".seedsync.runtime.lock"
        }
        self.assertEqual(original, restored)
        self.assertTrue(backup.is_dir())
        if extra_link is not None:
            self.assertFalse(extra_link.exists())
            self.assertEqual("outside", outside.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "migration-state.json").exists())
        self.assertFalse((self.root / ".migration.lock").exists())
        self.assertFalse((self.root / ".migration-restore.json").exists())
        self.assertEqual("required", MigrationCoordinator(self.root).preflight().state.value)
        coordinator.restore_offline(str(backup))
        self.assertEqual(original, {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
            and "migration-backups" not in path.parts
            and path.name != ".seedsync.runtime.lock"
        })
        outside.unlink()

    def test_tamper_blocks_before_destination_mutation_and_interrupted_restore_reruns(self) -> None:
        (self.root / "a").write_text("old-a", encoding="utf-8")
        (self.root / "b").write_text("old-b", encoding="utf-8")
        backup = self._create_backup()
        (self.root / "a").write_text("current-a", encoding="utf-8")
        before = (self.root / "a").read_bytes()
        (backup / "data" / "b").write_text("tampered", encoding="utf-8")
        with self.assertRaises(BackupRestoreError):
            restore_backup(self.root, backup)
        self.assertEqual(before, (self.root / "a").read_bytes())

        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["entries"][0]["path"] = "../outside"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(BackupRestoreError):
            restore_backup(self.root, backup)
        self.assertEqual(before, (self.root / "a").read_bytes())

        # Make a fresh valid backup in another root and interrupt after the
        # journal is durable but during convergence, then safely rerun.
        restart_root = self.root / "restart"
        restart_root.mkdir()
        (restart_root / "a").write_text("old-a", encoding="utf-8")
        (restart_root / "b").write_text("old-b", encoding="utf-8")
        restart_backup = create_retained_backup(
            restart_root, migration_id="m", source_schema="old", target_schema="new",
        )
        (restart_root / "a").write_text("new-a", encoding="utf-8")
        from migration.backup_restore import _install_validated_staged_file
        failed = False

        def interrupt_once(source, destination, config_root, expected_size, expected_digest):
            nonlocal failed
            if not failed and Path(destination).name == "a":
                failed = True
                raise OSError("injected interruption")
            return _install_validated_staged_file(
                source, destination, config_root, expected_size, expected_digest,
            )

        with patch(
            "migration.backup_restore._install_validated_staged_file",
            side_effect=interrupt_once,
        ):
            with self.assertRaises(BackupRestoreError):
                restore_backup(restart_root, restart_backup)
        restore_backup(restart_root, restart_backup)
        self.assertEqual("old-a", (restart_root / "a").read_text(encoding="utf-8"))
        self.assertEqual("old-b", (restart_root / "b").read_text(encoding="utf-8"))

    def test_entry_count_limit_blocks_publication(self) -> None:
        (self.root / "a").write_text("a", encoding="utf-8")
        (self.root / "b").write_text("b", encoding="utf-8")
        with patch("migration.backup_restore.MAX_ENTRIES", 1):
            with self.assertRaises(BackupRestoreError):
                self._create_backup()
        self.assertFalse(any(
            path.name == "manifest.json" for path in self.root.rglob("manifest.json")
        ))

    def test_concurrent_source_change_blocks_publication_before_checkpoint(self) -> None:
        MigrationFixture(self.root).write()
        from migration.backup_restore import _write_private_file

        changed = False

        def mutate_during_copy(path, source=None, payload=None, **kwargs):
            nonlocal changed
            result = _write_private_file(path, source=source, payload=payload, **kwargs)
            if source is not None and not changed:
                changed = True
                (self.root / "concurrent-new-file").write_text("raced", encoding="utf-8")
            return result

        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        with patch("migration.backup_restore._write_private_file", side_effect=mutate_during_copy):
            with self.assertRaises(MigrationBlockedError):
                coordinator.apply_confirmed()
        self.assertFalse((self.root / "migration-state.json").exists())
        self.assertFalse((self.root / "path_pairs.json").exists())
        self.assertEqual([], [
            path for path in (self.root / "migration-backups").iterdir()
            if not path.name.startswith(".")
        ])

    def test_apply_freezes_declared_inputs_before_checkpoint_or_live_writes(self) -> None:
        MigrationFixture(self.root).write()
        original = {
            name: (self.root / name).read_bytes()
            for name in ("settings.cfg", "controller.persist", "autoqueue.persist")
        }
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        original_freeze = ValidatedBackupReader.freeze
        mutated = False

        def mutate_after_manifest_validation(backup_dir, config_root, manifest, declared_inputs):
            nonlocal mutated
            mutated = True
            (backup_dir / "data" / "settings.cfg").write_bytes(b"same-owner race")
            return original_freeze(backup_dir, config_root, manifest, declared_inputs)

        with patch.object(
            ValidatedBackupReader, "freeze", side_effect=mutate_after_manifest_validation,
        ):
            with self.assertRaises(MigrationBlockedError):
                coordinator.apply_confirmed()
        self.assertTrue(mutated)
        self.assertFalse((self.root / "migration-state.json").exists())
        self.assertFalse((self.root / "path_pairs.json").exists())
        self.assertEqual(original, {name: (self.root / name).read_bytes() for name in original})

    def test_validated_reader_exposes_only_declared_manifest_inputs(self) -> None:
        MigrationFixture(self.root).write()
        backup = self._create_backup()
        manifest = validate_backup(backup, self.root)
        reader = ValidatedBackupReader.freeze(
            backup, self.root, manifest, ("settings.cfg",),
        )
        self.assertEqual((backup / "data" / "settings.cfg").read_bytes(), reader.read_bytes("settings.cfg"))
        for undeclared in ("controller.persist", "../settings.cfg", "/settings.cfg"):
            with self.subTest(undeclared=undeclared):
                with self.assertRaises(ValueError):
                    reader.read_bytes(undeclared)
        self.assertFalse(hasattr(reader, "backup_dir"))

    def test_restore_backup_mutation_during_staging_blocks_before_live_mutation(self) -> None:
        (self.root / "a").write_text("old-a", encoding="utf-8")
        (self.root / "b").write_text("old-b", encoding="utf-8")
        backup = self._create_backup()
        (self.root / "a").write_text("current-a", encoding="utf-8")
        (self.root / "live-only").write_text("untouched", encoding="utf-8")
        before = {
            "a": (self.root / "a").read_bytes(),
            "live-only": (self.root / "live-only").read_bytes(),
        }
        from migration.backup_restore import _write_private_file
        mutated = False

        def mutate_backup_after_copy(path, source=None, payload=None, **kwargs):
            nonlocal mutated
            result = _write_private_file(path, source=source, payload=payload, **kwargs)
            if source is not None and not mutated:
                mutated = True
                (backup / "data" / "b").write_bytes(b"same-owner race")
            return result

        with patch(
            "migration.backup_restore._write_private_file",
            side_effect=mutate_backup_after_copy,
        ):
            with self.assertRaises(BackupRestoreError):
                restore_backup(self.root, backup)
        self.assertTrue(mutated)
        self.assertEqual(before["a"], (self.root / "a").read_bytes())
        self.assertEqual(before["live-only"], (self.root / "live-only").read_bytes())
        self.assertFalse((self.root / ".migration-restore.json").exists())

    def test_live_normal_runtime_exclusion_blocks_apply_and_restore(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        with RuntimeExclusion(self.root, "normal-runtime"):
            with self.assertRaises(MigrationBlockedError):
                coordinator.apply_confirmed()
        self.assertFalse((self.root / "migration-state.json").exists())

        backup = self._create_backup()
        with RuntimeExclusion(self.root, "normal-runtime"):
            with self.assertRaises(BackupRestoreError):
                coordinator.restore_offline(backup.name)

    def test_cross_process_normal_runtime_lease_blocks_migration_apply(self) -> None:
        MigrationFixture(self.root).write()
        coordinator = MigrationCoordinator(self.root)
        coordinator.preflight()
        script = (
            "import sys; from pathlib import Path; "
            "from migration.runtime_exclusion import RuntimeExclusion; "
            "lease=RuntimeExclusion(Path(sys.argv[1]), 'normal-runtime'); "
            "print('ready', flush=True); sys.stdin.read(1); lease.release()"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(self.root)],
            cwd=str(Path(__file__).parents[2]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        child_stderr = ""
        try:
            self.assertEqual("ready", process.stdout.readline().strip())
            with self.assertRaises(MigrationBlockedError):
                coordinator.apply_confirmed()
            self.assertFalse((self.root / "migration-state.json").exists())
        finally:
            _, child_stderr = process.communicate("x", timeout=10)
        self.assertEqual(0, process.returncode, child_stderr)

    def test_concurrent_stale_runtime_lease_contenders_have_one_winner(self) -> None:
        lock_path = self.root / ".seedsync.runtime.lock"
        with RuntimeExclusion(self.root, "departed-runtime"):
            pass
        script = """import sys
from pathlib import Path
from migration.runtime_exclusion import RuntimeExclusion, RuntimeExclusionError
sys.stdin.read(1)
try:
    lease = RuntimeExclusion(Path(sys.argv[1]), "contender")
    print("acquired", flush=True)
    sys.stdin.read(1)
    lease.release()
except RuntimeExclusionError:
    print("blocked", flush=True)
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.root)],
                cwd=str(Path(__file__).parents[2]),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        try:
            for process in processes:
                process.stdin.write("g")
                process.stdin.flush()
            outcomes = [process.stdout.readline().strip() for process in processes]
            self.assertEqual(["acquired", "blocked"], sorted(outcomes))
        finally:
            for process in processes:
                if process.poll() is None:
                    try:
                        process.stdin.write("x")
                        process.stdin.flush()
                    except OSError:
                        pass
            errors = [process.communicate(timeout=10)[1] for process in processes]
        self.assertEqual([0, 0], [process.returncode for process in processes], errors)
        self.assertTrue(lock_path.is_file())

    def test_restore_uses_frozen_bytes_when_staging_changes_after_freeze(self) -> None:
        (self.root / "settings.cfg").write_bytes(b"retained")
        backup = self._create_backup()
        (self.root / "settings.cfg").write_bytes(b"current")
        from migration.backup_restore import _install_frozen_payload

        mutated = False

        def mutate_staging_after_freeze(payload, expected_digest, target, config_root):
            nonlocal mutated
            if not mutated and target.name == "settings.cfg":
                mutated = True
                stages = list(config_root.glob(".migration-restore-*.staging"))
                self.assertEqual(1, len(stages))
                (stages[0] / "data" / "settings.cfg").write_bytes(b"late staging mutation")
            return _install_frozen_payload(payload, expected_digest, target, config_root)

        with patch(
            "migration.backup_restore._install_frozen_payload",
            side_effect=mutate_staging_after_freeze,
        ):
            restore_backup(self.root, backup)
        self.assertTrue(mutated)
        self.assertEqual(b"retained", (self.root / "settings.cfg").read_bytes())

    @unittest.skipUnless(os.name == "posix", "POSIX named-install interference regression")
    def test_final_install_tamper_fails_and_rolls_back_live_file(self) -> None:
        (self.root / "settings.cfg").write_bytes(b"retained")
        backup = self._create_backup()
        (self.root / "settings.cfg").write_bytes(b"current-live")
        tampered = False

        def overwrite_after_descriptor_validation(temporary, _descriptor):
            nonlocal tampered
            if not tampered:
                tampered = True
                temporary.write_bytes(b"same-account final-window interference")

        with patch(
            "migration.backup_restore._before_posix_install_rename",
            side_effect=overwrite_after_descriptor_validation,
        ):
            with self.assertRaises(BackupRestoreError):
                restore_backup(self.root, backup)

        self.assertTrue(tampered)
        self.assertEqual(b"current-live", (self.root / "settings.cfg").read_bytes())
        self.assertEqual([], list(self.root.glob(".*-restore-*.rollback")))

        restore_backup(self.root, backup)
        self.assertEqual(b"retained", (self.root / "settings.cfg").read_bytes())

    @unittest.skipUnless(os.name == "posix", "POSIX bind-rename regression")
    def test_enoent_after_effective_install_rename_converges(self) -> None:
        (self.root / "settings.cfg").write_bytes(b"retained")
        backup = self._create_backup()
        (self.root / "settings.cfg").write_bytes(b"current-live")
        from migration.backup_restore import _rename_posix_install

        def rename_then_report_enoent(source_name, target_name, source_parent, target_parent):
            _rename_posix_install(source_name, target_name, source_parent, target_parent)
            raise FileNotFoundError(errno.ENOENT, "bind mount reported ENOENT after rename")

        with patch(
            "migration.backup_restore._rename_posix_install",
            side_effect=rename_then_report_enoent,
        ), patch(
            "migration.backup_restore._stat_posix_published_install",
            side_effect=FileNotFoundError(errno.ENOENT, "bind lookup remains hidden while open"),
        ):
            restore_backup(self.root, backup)

        self.assertEqual(b"retained", (self.root / "settings.cfg").read_bytes())
        self.assertFalse((self.root / ".migration-restore.json").exists())
        self.assertEqual([], list(self.root.glob(".migration-restore-*.staging")))

    @unittest.skipUnless(os.name == "posix", "POSIX bind-descriptor regression")
    def test_post_rename_fstat_enoent_uses_descriptor_target_and_exact_bytes(self) -> None:
        (self.root / "settings.cfg").write_bytes(b"retained")
        backup = self._create_backup()
        (self.root / "settings.cfg").write_bytes(b"current-live")

        with patch(
            "migration.backup_restore._published_restore_descriptor_stat",
            side_effect=FileNotFoundError(errno.ENOENT, "bind fstat transient"),
        ):
            restore_backup(self.root, backup)

        self.assertEqual(b"retained", (self.root / "settings.cfg").read_bytes())
        self.assertFalse((self.root / ".migration-restore.json").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX bind-descriptor regression")
    def test_post_rename_fstat_enoent_without_target_proof_rolls_back(self) -> None:
        (self.root / "settings.cfg").write_bytes(b"retained")
        backup = self._create_backup()
        (self.root / "settings.cfg").write_bytes(b"current-live")

        with patch(
            "migration.backup_restore._published_restore_descriptor_stat",
            side_effect=FileNotFoundError(errno.ENOENT, "bind fstat transient"),
        ), patch(
            "migration.backup_restore._descriptor_reports_installed_target",
            return_value=False,
        ):
            with self.assertRaises(BackupRestoreError):
                restore_backup(self.root, backup)

        self.assertEqual(b"current-live", (self.root / "settings.cfg").read_bytes())
        restore_backup(self.root, backup)
        self.assertEqual(b"retained", (self.root / "settings.cfg").read_bytes())

    @unittest.skipUnless(os.name == "posix", "POSIX bind-descriptor regression")
    def test_post_rename_fstat_enoent_with_wrong_bytes_rolls_back(self) -> None:
        (self.root / "settings.cfg").write_bytes(b"retained")
        backup = self._create_backup()
        (self.root / "settings.cfg").write_bytes(b"current-live")
        mutated = False

        def mutate_install_object(temporary, _descriptor):
            nonlocal mutated
            if not mutated:
                mutated = True
                temporary.write_bytes(b"wrong-published-bytes")

        with patch(
            "migration.backup_restore._published_restore_descriptor_stat",
            side_effect=FileNotFoundError(errno.ENOENT, "bind fstat transient"),
        ), patch(
            "migration.backup_restore._before_posix_install_rename",
            side_effect=mutate_install_object,
        ):
            with self.assertRaises(BackupRestoreError):
                restore_backup(self.root, backup)

        self.assertTrue(mutated)
        self.assertEqual(b"current-live", (self.root / "settings.cfg").read_bytes())

    @unittest.skipUnless(os.name == "posix", "POSIX bind-rename regression")
    def test_true_install_enoent_is_bounded_and_rolls_back_for_retry(self) -> None:
        (self.root / "settings.cfg").write_bytes(b"retained")
        backup = self._create_backup()
        (self.root / "settings.cfg").write_bytes(b"current-live")

        with patch(
            "migration.backup_restore._rename_posix_install",
            side_effect=FileNotFoundError(errno.ENOENT, "injected missing install source"),
        ):
            with self.assertRaises(BackupRestoreError) as context:
                restore_backup(self.root, backup)

        message = str(context.exception)
        self.assertIn("'settings.cfg' (ENOENT)", message)
        self.assertNotIn(str(self.root), message)
        self.assertEqual(b"current-live", (self.root / "settings.cfg").read_bytes())

        restore_backup(self.root, backup)
        self.assertEqual(b"retained", (self.root / "settings.cfg").read_bytes())

    def test_restore_oserror_context_is_bounded_for_every_convergence_boundary(self) -> None:
        from migration.backup_restore import _restore_oserror_context

        boundaries = (
            ("execute restore convergence", "."),
            ("recover interrupted staging", ".migration-restore.json"),
            ("create restore staging", ".migration-restore-backup.staging"),
            ("stage retained backup entry", ".hidden/input.conf"),
            ("synchronize restore staging", ".migration-restore-backup.staging"),
            ("write convergence journal", ".migration-restore.json"),
            ("inventory live configuration", "."),
            ("remove post-backup entry", "extra.txt"),
            ("prepare restored directory", ".hidden/nested"),
            ("install restored file", "settings.cfg"),
            ("apply restored file mode", "settings.cfg"),
            ("synchronize restored file parent", "settings.cfg"),
            ("open staged restore file", ".migration-restore-backup.staging/data/settings.cfg"),
            ("validate staged restore file", ".migration-restore-backup.staging/data/settings.cfg"),
            ("create private restore install object", "settings.cfg"),
            ("write and validate restore install object", "settings.cfg"),
            ("retain live file for rollback", "settings.cfg"),
            ("synchronize published restore file", "settings.cfg"),
            ("verify published restore file", "settings.cfg"),
            ("roll back failed restore file", "settings.cfg"),
            ("remove restore rollback", "settings.cfg"),
            ("synchronize completed restore file", "settings.cfg"),
            ("remove failed restore install object", "settings.cfg"),
            ("apply restored directory mode", ".hidden"),
            ("remove migration infrastructure", "migration-state.json"),
            ("verify final restored inventory", "."),
            ("remove completed restore staging", ".migration-restore-backup.staging"),
            ("remove convergence journal", ".migration-restore.json"),
            ("synchronize restored configuration", "."),
        )
        for operation, relative in boundaries:
            with self.subTest(operation=operation):
                error = FileNotFoundError(errno.ENOENT, "secret-bearing raw message")
                with self.assertRaises(BackupRestoreError) as context:
                    with _restore_oserror_context(operation, relative, self.root):
                        raise error
                self.assertEqual(
                    "Restore {} failed for {!r} (ENOENT)".format(operation, relative),
                    str(context.exception),
                )
                self.assertIs(error, context.exception.__cause__)
                self.assertNotIn("secret-bearing", str(context.exception))

    def test_late_restore_eperm_context_is_bounded(self) -> None:
        from migration.backup_restore import _restore_oserror_context

        late_boundaries = (
            ("apply restored file mode", ".hidden/nested/layer/probe.conf"),
            ("apply restored directory mode", ".hidden/nested/layer"),
            ("remove migration infrastructure", "migration-state.json"),
            ("verify final restored inventory", "."),
            ("remove completed restore staging", ".migration-restore-backup.staging"),
            ("remove convergence journal", ".migration-restore.json"),
            ("synchronize restored configuration", "."),
        )
        for operation, relative in late_boundaries:
            with self.subTest(operation=operation):
                error = PermissionError(errno.EPERM, "secret-bearing raw message")
                with self.assertRaises(BackupRestoreError) as context:
                    with _restore_oserror_context(operation, relative, self.root):
                        raise error
                self.assertEqual(
                    "Restore {} failed for {!r} (EPERM)".format(operation, relative),
                    str(context.exception),
                )
                self.assertIs(error, context.exception.__cause__)
                self.assertNotIn("secret-bearing", str(context.exception))

    @unittest.skipUnless(os.name == "posix", "POSIX directory mode capability regression")
    def test_directory_mode_unsupported_error_requires_exact_effective_mode(self) -> None:
        from migration.backup_restore import _apply_restored_directory_mode

        directory = self.root / "directory-mode"
        directory.mkdir(mode=0o700)
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.chmod(directory, 0o777)
            with patch(
                "migration.backup_restore.os.fchmod",
                side_effect=PermissionError(errno.EPERM, "unsupported bind chmod"),
            ):
                _apply_restored_directory_mode(descriptor, 0o777)

            os.chmod(directory, 0o700)
            with patch(
                "migration.backup_restore.os.fchmod",
                side_effect=PermissionError(errno.EPERM, "unsupported bind chmod"),
            ):
                with self.assertRaises(BackupRestoreError):
                    _apply_restored_directory_mode(descriptor, 0o777)

            os.chmod(directory, 0o777)
            unexpected = OSError(errno.EINVAL, "unexpected chmod failure")
            with patch("migration.backup_restore.os.fchmod", side_effect=unexpected):
                with self.assertRaises(OSError) as context:
                    _apply_restored_directory_mode(descriptor, 0o777)
            self.assertIs(unexpected, context.exception)
        finally:
            os.close(descriptor)

    @unittest.skipUnless(os.name == "posix", "POSIX directory mode verification regression")
    def test_successful_directory_chmod_must_take_effect(self) -> None:
        from migration.backup_restore import _apply_restored_directory_mode

        directory = self.root / "directory-mode-noop"
        directory.mkdir(mode=0o700)
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            with patch("migration.backup_restore.os.fchmod", return_value=None):
                with self.assertRaises(BackupRestoreError):
                    _apply_restored_directory_mode(descriptor, 0o777)
        finally:
            os.close(descriptor)

    def test_restore_recovery_error_is_contextual_and_retryable(self) -> None:
        (self.root / "settings.cfg").write_bytes(b"retained")
        backup = self._create_backup()
        with patch(
            "migration.backup_restore._recover_restore_artifacts",
            side_effect=FileNotFoundError(errno.ENOENT, "injected recovery lookup"),
        ):
            with self.assertRaises(BackupRestoreError) as context:
                restore_backup(self.root, backup)
        self.assertEqual(
            "Restore recover interrupted staging failed for '.migration-restore.json' (ENOENT)",
            str(context.exception),
        )
        self.assertNotIn(str(self.root), str(context.exception))

        restore_backup(self.root, backup)
        self.assertEqual(b"retained", (self.root / "settings.cfg").read_bytes())

    def test_injected_nested_mount_blocks_backup_and_restore(self) -> None:
        (self.root / "a").write_text("old", encoding="utf-8")
        detector = lambda root: (root / "nested-bind",)
        with self.assertRaises(BackupRestoreError):
            create_retained_backup(
                self.root, migration_id="m", source_schema="old", target_schema="new",
                mount_detector=detector,
            )
        backup = self._create_backup()
        with self.assertRaises(BackupRestoreError):
            restore_backup(self.root, backup, mount_detector=detector)

        synthetic = "36 25 0:32 / {0}/nested rw - none none rw\n".format(self.root.as_posix())
        self.assertEqual((self.root / "nested",), detect_nested_mounts(self.root, synthetic))

    def test_hardlink_and_manifest_boundary_fail_closed(self) -> None:
        (self.root / "a").write_text("old", encoding="utf-8")
        os.link(self.root / "a", self.root / "hardlink")
        with self.assertRaises(BackupRestoreError):
            self._create_backup()
        (self.root / "hardlink").unlink()
        with patch("migration.backup_restore.MAX_MANIFEST_BYTES", 100):
            with self.assertRaises(BackupRestoreError):
                self._create_backup()
        self.assertEqual([], [
            path for path in (self.root / "migration-backups").iterdir()
            if not path.name.startswith(".")
        ])

    def test_actual_cli_restore_accepts_relative_config_root_and_exits_before_services(self) -> None:
        config = self.root / "relative-config"
        config.mkdir()
        (config / "old").write_text("old", encoding="utf-8")
        backup = create_retained_backup(
            config, migration_id="m", source_schema="old", target_schema="new",
        )
        (config / "old").write_text("new", encoding="utf-8")
        (config / "extra").write_text("extra", encoding="utf-8")
        argv = [
            "seedsync.py", "-c", config.name,
            "--restore-migration-backup", str(backup),
            "--confirm-restore", "--confirm-stopped",
        ]
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.object(sys, "argv", argv):
                application = Seedsync()
                with self.assertRaises(ServiceExit):
                    application.run()
        finally:
            os.chdir(previous)
        self.assertEqual("old", (config / "old").read_text(encoding="utf-8"))
        self.assertFalse((config / "extra").exists())

    def test_cross_backup_interruption_is_not_silently_reassigned(self) -> None:
        (self.root / "a").write_text("one", encoding="utf-8")
        first = self._create_backup()
        (self.root / "a").write_text("two", encoding="utf-8")
        second = self._create_backup()
        journal = self.root / ".migration-restore.json"
        journal.write_text(json.dumps({
            "journal_version": 1,
            "backup_id": first.name,
            "phase": "converging",
        }), encoding="utf-8")
        if os.name == "posix":
            journal.chmod(0o600)
        before = (self.root / "a").read_bytes()
        with self.assertRaises(BackupRestoreError):
            restore_backup(self.root, second)
        self.assertEqual(before, (self.root / "a").read_bytes())
        self.assertTrue(journal.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor-race regression")
    def test_source_ancestor_swap_fails_before_publication_without_external_read(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "secret").write_text("inside", encoding="utf-8")
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir()
        (outside / "secret").write_text("outside", encoding="utf-8")
        moved = self.root / "nested-moved"
        from migration.backup_restore import _write_private_file
        swapped = False

        def swap_after_copy(path, source=None, payload=None, **kwargs):
            nonlocal swapped
            result = _write_private_file(path, source=source, payload=payload, **kwargs)
            if source is not None and not swapped:
                swapped = True
                nested.rename(moved)
                nested.symlink_to(outside, target_is_directory=True)
            return result

        try:
            with patch("migration.backup_restore._write_private_file", side_effect=swap_after_copy):
                with self.assertRaises(BackupRestoreError):
                    self._create_backup()
            self.assertEqual("outside", (outside / "secret").read_text(encoding="utf-8"))
            self.assertEqual([], [
                path for path in (self.root / "migration-backups").iterdir()
                if not path.name.startswith(".")
            ])
        finally:
            if nested.is_symlink():
                nested.unlink()
            if moved.exists():
                moved.rename(nested)
            for child in outside.iterdir():
                child.unlink()
            outside.rmdir()

    @unittest.skipUnless(os.name == "posix", "POSIX provenance and Unicode regression")
    def test_backup_permissions_and_unicode_normalization_fail_closed(self) -> None:
        (self.root / "a").write_text("old", encoding="utf-8")
        backup = self._create_backup()
        (backup / "data" / "a").chmod(0o644)
        (self.root / "a").write_text("current", encoding="utf-8")
        with self.assertRaises(BackupRestoreError):
            restore_backup(self.root, backup)
        self.assertEqual("current", (self.root / "a").read_text(encoding="utf-8"))

        unicode_root = self.root / "unicode"
        unicode_root.mkdir()
        (unicode_root / "Ã©").write_text("nfc", encoding="utf-8")
        (unicode_root / "e\u0301").write_text("nfd", encoding="utf-8")
        with self.assertRaises(BackupRestoreError):
            create_retained_backup(
                unicode_root, migration_id="m", source_schema="old", target_schema="new",
            )

    @unittest.skipUnless(os.name == "posix", "POSIX root-replacement regression")
    def test_root_replacement_during_publication_fails_cleanly_without_escape(self) -> None:
        (self.root / "a").write_text("inside", encoding="utf-8")
        moved = self.root.parent / (self.root.name + "-moved")
        outside = self.root.parent / (self.root.name + "-replacement")
        outside.mkdir()
        (outside / "sentinel").write_text("outside", encoding="utf-8")
        from migration.backup_restore import _publish_posix_transaction

        def replace_root_then_publish(transaction, staging, destination, config_root):
            self.root.rename(moved)
            self.root.symlink_to(outside, target_is_directory=True)
            try:
                return _publish_posix_transaction(
                    transaction, staging, destination, config_root,
                )
            finally:
                self.root.unlink()
                moved.rename(self.root)

        try:
            with patch(
                "migration.backup_restore._publish_posix_transaction",
                side_effect=replace_root_then_publish,
            ):
                with self.assertRaises(BackupRestoreError):
                    self._create_backup()
            self.assertEqual("outside", (outside / "sentinel").read_text(encoding="utf-8"))
            self.assertFalse((outside / "migration-backups").exists())
        finally:
            (outside / "sentinel").unlink(missing_ok=True)
            outside.rmdir()

    @unittest.skipUnless(
        (3, 11) <= sys.version_info[:2] < (3, 13),
        "Executable CLI requires supported Python 3.11/3.12",
    )
    def test_offline_restore_runs_as_real_process_and_exits_without_runtime(self) -> None:
        config = self.root / "subprocess-config"
        config.mkdir()
        (config / "old").write_text("old", encoding="utf-8")
        backup = create_retained_backup(
            config, migration_id="m", source_schema="old", target_schema="new",
        )
        (config / "old").write_text("new", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable, str(Path(__file__).parents[2] / "seedsync.py"),
                "-c", str(config),
                "--restore-migration-backup", backup.name,
                "--confirm-restore", "--confirm-stopped",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Restored migration backup", result.stdout)
        self.assertNotIn("Starting SeedSync", result.stdout + result.stderr)
        self.assertEqual("old", (config / "old").read_text(encoding="utf-8"))

    def test_cli_requires_both_confirmations_and_exits_before_normal_startup(self) -> None:
        argv = ["seedsync.py", "-c", str(self.root), "--restore-migration-backup", "backup-id"]
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as context:
                Seedsync()
        self.assertIn("both --confirm-restore and --confirm-stopped", str(context.exception))

        result = {"files": 2, "directories": 1, "entries": 3, "total_size": 7}
        argv += ["--confirm-restore", "--confirm-stopped"]
        with patch.object(sys, "argv", argv), patch.object(
            MigrationCoordinator, "restore_offline", return_value=result,
        ) as restore, patch.object(MigrationCoordinator, "preflight") as preflight:
            application = Seedsync()
            with self.assertRaises(ServiceExit):
                application.run()
        restore.assert_called_once_with("backup-id")
        preflight.assert_not_called()

    def test_outside_backup_and_existing_lock_are_refused(self) -> None:
        (self.root / "a").write_text("old", encoding="utf-8")
        backup = self._create_backup()
        outside = self.root.parent / "outside-backup"
        with self.assertRaises(BackupRestoreError):
            MigrationCoordinator(self.root).restore_offline(str(outside))
        (self.root / ".migration.lock").write_text("active-or-unknown", encoding="utf-8")
        with self.assertRaises(BackupRestoreError):
            MigrationCoordinator(self.root).restore_offline(backup.name)

    def test_interrupted_offline_restore_lock_for_same_backup_is_reclaimed(self) -> None:
        (self.root / "a").write_text("old", encoding="utf-8")
        backup = self._create_backup()
        (self.root / "a").write_text("new", encoding="utf-8")
        (self.root / ".migration.lock").write_text(json.dumps({
            "lock_version": 1,
            "pid": 99999999,
            "hostname": socket.gethostname(),
            "migration_id": "restore:" + backup.name,
        }), encoding="utf-8")
        MigrationCoordinator(self.root).restore_offline(backup.name)
        self.assertEqual("old", (self.root / "a").read_text(encoding="utf-8"))
        self.assertFalse((self.root / ".migration.lock").exists())


if __name__ == "__main__":
    unittest.main()
