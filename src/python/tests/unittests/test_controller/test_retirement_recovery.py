"""Recovery tests use the production publication and move entrypoints."""
import errno
import json
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from controller import Controller, ControllerPersist
from tests.unittests.test_controller import test_controller as controller_tests


class TestRetirementRecovery(unittest.TestCase):
    def setUp(self):
        controller_tests.TestController.setUp(self)
        self.controller._Controller__persist = ControllerPersist()

    def configure(self, root):
        staging = Path(root, "staging")
        final = Path(root, "final")
        staging.mkdir()
        final.mkdir()
        self.controller._Controller__staging_path = str(staging)
        self.controller._Controller__legacy_local_path = str(final)
        self.controller._Controller__context.config.file_path = str(Path(root, "settings.json"))
        return staging / "item", final / "item"

    def interrupt(self, source, destination, mode="residue", name="item"):
        source.mkdir()
        (source / "payload").write_bytes(b"verified payload")
        original = Controller._Controller__rename_no_replace
        first = True
        def rename(src, dst):
            nonlocal first
            if first:
                first = False
                raise OSError(errno.EXDEV, "different devices")
            return original(src, dst)
        def partial(claim):
            # Simulate an open-file filesystem rename-on-unlink residue.
            os.rename(os.path.join(claim, "payload"), os.path.join(claim, ".busy"))
            if mode == "crash":
                raise KeyboardInterrupt()
            raise OSError(errno.ENOTEMPTY, "retirement pending")
        with patch.object(Controller, "_Controller__rename_no_replace", side_effect=rename), \
                patch.object(Controller, "_Controller__rename_owned_directory", side_effect=OSError(errno.EXDEV, "forced copy")), \
                patch("controller.controller.shutil.rmtree", side_effect=partial):
            if mode == "crash":
                with self.assertRaises(KeyboardInterrupt):
                    self.controller._Controller__move_from_staging(name)
            else:
                self.assertEqual(Controller.MoveFromStagingResult.FAILED,
                                 self.controller._Controller__move_from_staging(name))
        journal = next(Path(self.controller._Controller__context.config.file_path).parent.glob(".seedsync-retirement-*.json"))
        record = json.loads(journal.read_text(encoding="utf-8"))
        return journal, record, Path(record["claim"])

    def test_partial_retirement_restart_waits_for_residue_without_recopy(self):
        for mode in ("residue", "crash"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                journal, record, claim = self.interrupt(source, destination, mode)
                self.assertEqual("deleting", record["phase"])
                self.assertFalse(source.exists())
                self.assertEqual(b"verified payload", (destination / "payload").read_bytes())
                # Restart loses all in-memory retry state and uses only the record.
                self.setUp()
                self.controller._Controller__staging_path = str(source.parent)
                self.controller._Controller__legacy_local_path = str(destination.parent)
                self.controller._Controller__context.config.file_path = str(Path(root, "settings.json"))
                with patch.object(Controller, "_Controller__copy_to_publish_temporary") as copy, \
                        patch("controller.controller.shutil.rmtree") as recursive:
                    self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                     self.controller._Controller__move_from_staging("item"))
                    self.assertTrue((claim / ".busy").exists())
                    (claim / ".busy").unlink()  # external holder releases its own residue
                    self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                                     self.controller._Controller__move_from_staging("item"))
                    self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                                     self.controller._Controller__move_from_staging("item"))
                    copy.assert_not_called()
                    recursive.assert_not_called()
                self.assertFalse(claim.exists())
                self.assertEqual("settled", json.loads(journal.read_text())["phase"])

    def test_foreign_directory_and_replaced_final_never_settle(self):
        for replacement in ("foreign_directory", "final", "staging_root", "source", "claim", "malformed"):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                journal, record, claim = self.interrupt(source, destination)
                (claim / ".busy").unlink()
                if replacement == "foreign_directory":
                    (claim / "unknown").mkdir()
                elif replacement == "final":
                    destination.rename(destination.with_name("preserved"))
                    destination.mkdir()
                    (destination / "payload").write_bytes(b"verified payload")
                elif replacement == "staging_root":
                    source.parent.rename(source.parent.with_name("preserved"))
                    source.parent.mkdir()
                elif replacement == "source":
                    source.mkdir()
                    (source / "new").write_bytes(b"new operation")
                elif replacement == "claim":
                    claim.rename(claim.with_name("preserved"))
                    claim.mkdir()
                else:
                    journal.write_text("{}", encoding="utf-8")
                with patch("controller.controller.shutil.rmtree") as recursive:
                    self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                     self.controller._Controller__move_from_staging("item"))
                    recursive.assert_not_called()
                self.assertTrue(journal.exists())

    def test_settled_record_preserves_new_source_until_explicit_lifecycle_reset(self):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            journal, record, claim = self.interrupt(source, destination)
            (claim / ".busy").unlink()
            self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                             self.controller._Controller__move_from_staging("item"))
            source.mkdir()
            (source / "new").write_bytes(b"new operation")
            self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                             self.controller._Controller__move_from_staging("item"))
            self.assertEqual(b"new operation", (source / "new").read_bytes())
            self.controller._Controller__reset_settled_retirement("item", None)
            self.assertFalse(journal.exists())
            self.assertEqual(b"new operation", (source / "new").read_bytes())

    def test_recovery_sync_failure_cannot_complete(self):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            journal, record, claim = self.interrupt(source, destination)
            (claim / ".busy").unlink()
            with patch("controller.controller.os.fsync", side_effect=OSError(errno.EIO, "sync")):
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("item"))
            self.assertEqual("deleting", json.loads(journal.read_text())["phase"])
            self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                             self.controller._Controller__move_from_staging("item"))

    def test_production_completion_recovers_journal_with_missing_staging_and_exhausted_retry(self):
        from tests.unittests.test_controller import test_model_updater as updater_tests
        from controller.model_updater import ModelUpdater
        from model import ModelFile
        import threading
        for cancellation in (False, True):
            with self.subTest(cancellation=cancellation), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                journal, record, claim = self.interrupt(source, destination)
                # Bind the existing production ModelBuilder completion fixture to
                # the real Controller methods, with physical source now absent.
                fixture = updater_tests.TestModelUpdater()._make_v092_pending_completion_controller(
                    complete=False, directory_root=True,
                )
                self.controller.__dict__.update(fixture.__dict__)
                del self.controller.__dict__["_Controller__move_from_staging"]
                self.controller._Controller__staging_path = str(source.parent)
                self.controller._Controller__legacy_local_path = str(destination.parent)
                self.controller._Controller__legacy_remote_path = "/remote"
                self.controller._Controller__context.config.file_path = str(Path(root, "settings.json"))
                # Rename the fixture model's canonical item to the physical test
                # name through its supported builder inputs.
                from system import SystemFile
                builder = self.controller._Controller__model_builder
                remote = SystemFile("item", 16, True, mtime_ns=1)
                remote.children.append(SystemFile("payload", 16, False, mtime_ns=1))
                builder.set_remote_files([remote])
                builder.set_local_files([])
                builder.set_lftp_statuses([])
                builder.request_rebuild()
                self.controller._Controller__model = builder.build_model()
                self.controller._Controller__prev_downloading_file_names = set()
                self.controller._Controller__pending_completion_file_names = set()
                self.controller._Controller__persist.move_failure_counts["item"] = 4
                self.controller._Controller__path_pair_refresh_lock = threading.RLock()
                self.controller._Controller__path_pair_refresh_requested = False
                self.controller._Controller__path_pair_refresh_generation = 0
                self.controller._Controller__is_explicitly_stopped.return_value = cancellation
                self.assertEqual({"item"}, self.controller._retirement_recovery_file_ids(self.controller._Controller__model), (self.controller._Controller__model.get_file_ids(), list(Path(root).iterdir())))
                if not cancellation:
                    original_scandir = os.scandir
                    for error_number in (errno.EIO, errno.EACCES):
                        def fail_discovery(path):
                            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(root)):
                                raise OSError(error_number, "journal directory unavailable")
                            return original_scandir(path)
                        builder.request_rebuild()
                        with patch("controller.controller.os.scandir", side_effect=fail_discovery), \
                                patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]), \
                                patch.object(Controller, "_Controller__settle_retirement_journal") as settle:
                            self.assertIsNone(self.controller._retirement_recovery_file_ids(self.controller._Controller__model))
                            ModelUpdater(self.controller).update()
                            settle.assert_not_called()
                        self.assertNotIn("item", self.controller._Controller__persist.downloaded_file_names)
                        self.assertNotIn("item", self.controller._Controller__persist.final_move_succeeded_file_names)
                        self.assertTrue((claim / ".busy").exists())
                    self.controller._reserve_move_attempt.reset_mock()
                    self.controller._release_move_attempt.reset_mock()
                builder.request_rebuild()
                with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
                    ModelUpdater(self.controller).update()
                self.assertNotIn("item", self.controller._Controller__persist.final_move_succeeded_file_names)
                self.assertNotIn("item", self.controller._Controller__persist.downloaded_file_names)
                self.assertTrue((claim / ".busy").exists())
                if cancellation:
                    self.controller._reserve_move_attempt.assert_not_called()
                    continue
                self.assertIn(("item", None, None), self.controller._Controller__pending_completion_file_names)
                (claim / ".busy").unlink()
                original_fsync = os.fsync
                journal_parent_stat = os.stat(root)
                def unsupported_journal_sync(descriptor):
                    opened = os.fstat(descriptor)
                    if stat.S_ISDIR(opened.st_mode) and (opened.st_dev, opened.st_ino) == (
                            journal_parent_stat.st_dev, journal_parent_stat.st_ino):
                        raise OSError(errno.EINVAL, "directory sync unsupported")
                    return original_fsync(descriptor)
                self.controller._Controller__next_lftp_status_poll_at = None
                builder.request_rebuild()
                with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]), \
                        patch("controller.controller.os.fsync", side_effect=unsupported_journal_sync):
                    ModelUpdater(self.controller).update()
                self.assertNotIn("item", self.controller._Controller__persist.downloaded_file_names)
                self.assertNotIn("item", self.controller._Controller__persist.final_move_succeeded_file_names)
                self.assertTrue(claim.exists())
                self.controller._Controller__next_lftp_status_poll_at = None
                builder.request_rebuild()
                with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
                    ModelUpdater(self.controller).update()
                self.assertIn("item", self.controller._Controller__persist.downloaded_file_names)
                self.assertIn("item", self.controller._Controller__persist.final_move_succeeded_file_names)
                self.assertNotIn(("item", None, None), self.controller._Controller__pending_completion_file_names)
                self.assertEqual(3, self.controller._reserve_move_attempt.call_count)
                self.assertEqual(3, self.controller._release_move_attempt.call_count)

    def test_recovery_diagnostics_off_on_and_failing_are_equivalent(self):
        from common.breadcrumb_trace import BreadcrumbTraceCollector
        for mode in ("off", "on", "throwing"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                trace = BreadcrumbTraceCollector(lambda: mode != "off", max_entries=32,
                                                 policy={"default": "info"})
                self.controller._Controller__context.breadcrumb_trace = trace
                with patch.object(trace, "record", side_effect=RuntimeError("sink")) if mode == "throwing" else \
                        patch.object(trace, "record", wraps=trace.record) as recorder:
                    journal, record, claim = self.interrupt(source, destination)
                    publications = [call for call in recorder.call_args_list
                                    if call.kwargs.get("category") == "final_move.publication"]
                    if mode == "off":
                        self.assertEqual([], publications)
                    else:
                        self.assertTrue(publications, "production publication recorder was not invoked")
                        self.assertTrue(any(call.args[2].get("phase") == "remove_source"
                                            for call in publications))
                    self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                     self.controller._Controller__move_from_staging("item"))
                    (claim / ".busy").unlink()
                    self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                                     self.controller._Controller__move_from_staging("item"))

    def test_mount_and_link_residue_are_never_removed(self):
        for unsafe in ("mount", "symlink"):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                journal, record, claim = self.interrupt(source, destination)
                (claim / ".busy").unlink()
                if unsafe == "mount":
                    guard = patch.object(Controller, "_Controller__reject_nested_mounts_or_reparse_points",
                                         side_effect=OSError(errno.EXDEV, "mount"))
                else:
                    try:
                        os.symlink(str(destination), str(claim / "link"), target_is_directory=True)
                    except OSError:
                        continue  # native Linux validation exercises this case
                    from contextlib import nullcontext
                    guard = nullcontext()
                with guard:
                    self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                     self.controller._Controller__move_from_staging("item"))
                self.assertTrue(claim.exists())

    @patch.object(Controller, "_Controller__rename_owned_directory", side_effect=OSError(errno.EXDEV, "forced copy"))
    def test_planned_claim_crash_cannot_admit_recursive_deletion_on_restart(self, _directory_alternate):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            source.mkdir()
            (source / "payload").write_bytes(b"verified payload")
            original_rename = os.rename
            original_publish = Controller._Controller__rename_no_replace
            def rename(src, dst, *args, **kwargs):
                result = original_rename(src, dst, *args, **kwargs)
                if os.path.basename(dst).startswith(".seedsync-retire-"):
                    raise KeyboardInterrupt()
                return result
            first = True
            def publish(src, dst):
                nonlocal first
                if first:
                    first = False
                    raise OSError(errno.EXDEV, "different devices")
                return original_publish(src, dst)
            with patch("controller.controller.os.rename", side_effect=rename), \
                    patch.object(Controller, "_Controller__rename_no_replace", side_effect=publish):
                with self.assertRaises(KeyboardInterrupt):
                    self.controller._Controller__move_from_staging("item")
            journal = next(Path(root).glob(".seedsync-retirement-*.json"))
            record = json.loads(journal.read_text())
            self.assertEqual("planned", record["phase"])
            claim = Path(record["claim"])
            with patch("controller.controller.shutil.rmtree") as recursive:
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("item"))
                recursive.assert_not_called()
            self.assertEqual(b"verified payload", (claim / "payload").read_bytes())

    def test_journal_lookup_fault_never_takes_missing_source_completion_shortcut(self):
        for error_number in (errno.EIO, errno.EACCES):
            with self.subTest(error_number=error_number), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                journal, record, claim = self.interrupt(source, destination)
                original_lstat = os.lstat
                def fail_lookup(path, *args, **kwargs):
                    if os.path.normcase(os.fspath(path)) == os.path.normcase(str(journal)):
                        raise OSError(error_number, "journal unavailable")
                    return original_lstat(path, *args, **kwargs)
                with patch("controller.controller.os.lstat", side_effect=fail_lookup):
                    self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                     self.controller._Controller__move_from_staging("item"))
                    with self.assertRaises(OSError):
                        self.controller._has_retirement_journal("item")
                self.assertTrue((claim / ".busy").exists())
                self.assertNotIn("item", self.controller._Controller__persist.downloaded_file_names)

    def test_legacy_cleanup_excludes_journal_owned_claim_and_skips_unknown_ownership(self):
        for uncertainty in (False, True):
            with self.subTest(uncertainty=uncertainty), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                journal, record, claim = self.interrupt(source, destination)
                (claim / ".busy").unlink()
                legacy = source.parent / (".seedsync-retire-" + "f" * 48)
                legacy.mkdir()
                if uncertainty:
                    journal.write_text("{}", encoding="utf-8")
                self.controller._Controller__cleanup_empty_retired_source_claims(str(source.parent))
                self.assertTrue(claim.exists())
                self.assertEqual(uncertainty, legacy.exists())

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored progressive child publication")
    def test_progressive_child_publication_preserves_a1_paths_without_any_journal_access(self):
        import hashlib
        for state in ("copy", "missing", "failure_limit", "stopped"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                source.mkdir()
                destination.mkdir()
                payload = source / "payload"
                published = destination / "payload"
                if state == "missing":
                    published.write_bytes(b"already published")
                else:
                    payload.write_bytes(b"child payload")
                self.controller._Controller__child_final_move_retry_due = {}
                self.controller._Controller__child_final_move_failure_counts = {
                    "item/payload": 4,
                } if state == "failure_limit" else {}
                self.controller._Controller__persist.stopped_file_names = {
                    "item/payload",
                } if state == "stopped" else set()
                records = []
                for file_id in ("item", "item/payload"):
                    record = Path(root, ".seedsync-retirement-" + hashlib.sha256(file_id.encode()).hexdigest() + ".json")
                    record.write_bytes(b"preexisting record is outside child authority")
                    records.append(record)
                with patch.object(Controller, "_Controller__rename_no_replace",
                                  side_effect=OSError(errno.EXDEV, "forced child copy")), \
                        patch.object(Controller, "_has_retirement_journal",
                                     side_effect=AssertionError("progressive child journal lookup")), \
                        patch.object(Controller, "_Controller__read_retirement_journal",
                                     side_effect=AssertionError("progressive child journal read")), \
                        patch.object(Controller, "_Controller__settle_retirement_journal",
                                     side_effect=AssertionError("progressive child journal settlement")), \
                        patch.object(Controller, "_Controller__write_retirement_journal",
                                     side_effect=AssertionError("progressive child journal creation")):
                    result = self.controller._finalize_staging_child("item", "payload")
                if state == "copy":
                    self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
                    self.assertEqual(b"child payload", published.read_bytes())
                    self.assertFalse(payload.exists())
                elif state == "missing":
                    self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED, result)
                    self.assertEqual(b"already published", published.read_bytes())
                else:
                    self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
                    self.assertEqual(b"child payload", payload.read_bytes())
                    self.assertFalse(published.exists())
                for record in records:
                    self.assertEqual(b"preexisting record is outside child authority", record.read_bytes())

    @unittest.skipUnless(os.name == "posix", "native no-follow symlink publication")
    def test_symlink_root_copy_fallback_preserves_referent_and_never_looks_up_journal(self):
        with tempfile.TemporaryDirectory() as root:
            referent = Path(root, "referent")
            referent.mkdir()
            (referent / "payload").write_bytes(b"protected payload")
            source = Path(root, "alias")
            source.symlink_to(referent, target_is_directory=True)
            destination = Path(root, "published")
            target = os.readlink(source)
            with patch.object(Controller, "_Controller__rename_no_replace",
                              side_effect=OSError(errno.EINVAL, "unsupported")), \
                    patch.object(Controller, "_Controller__retirement_journal_path",
                                 side_effect=AssertionError("symlink journal lookup")), \
                    patch.object(Controller, "_Controller__read_retirement_journal",
                                 side_effect=AssertionError("symlink journal read")):
                Controller._Controller__publish_staging_no_replace(
                    str(source), str(destination), retirement_journal_enabled=True,
                    retirement_roots=(str(source.parent), str(destination.parent)),
                )
            self.assertTrue(destination.is_symlink())
            self.assertEqual(target, os.readlink(destination))
            self.assertFalse(os.path.lexists(source))
            self.assertEqual(b"protected payload", (referent / "payload").read_bytes())
            self.assertEqual([], list(Path(root).rglob(".seedsync-retirement-*.json")))
            self.assertEqual([], list(Path(root).rglob(".seedsync-retire-*")))

    @unittest.skipUnless(os.name == "posix", "native journal directory syscall faults")
    @patch.object(Controller, "_Controller__rename_owned_directory", side_effect=OSError(errno.EXDEV, "forced copy"))
    def test_journal_syscall_failure_before_claim_mutation_preserves_source_and_verified_final(self, _directory_alternate):
        failures = [("open", value) for value in (errno.EIO, errno.EACCES, errno.ENOTSUP)]
        failures += [("directory_fsync", value) for value in (errno.EIO, errno.EINVAL, errno.ENOTSUP)]
        failures += [("file_fsync", errno.EIO)]
        for directory in (False, True):
            for operation, error_number in failures:
                with self.subTest(directory=directory, operation=operation, error_number=error_number), \
                        tempfile.TemporaryDirectory() as root:
                    source, destination = self.configure(root)
                    if directory:
                        source.mkdir()
                        payload = source / "payload"
                        published = destination / "payload"
                    else:
                        payload, published = source, destination
                    payload.write_bytes(b"verified payload")
                    root_stat = os.stat(root)
                    original_open, original_fsync = os.open, os.fsync
                    def native_open(path, flags, *args, **kwargs):
                        if operation == "open" and os.fspath(path) == root and flags & os.O_DIRECTORY:
                            raise OSError(error_number, "journal directory open failed")
                        return original_open(path, flags, *args, **kwargs)
                    def native_fsync(descriptor):
                        opened = os.fstat(descriptor)
                        if operation == "directory_fsync" and stat.S_ISDIR(opened.st_mode) and \
                                (opened.st_dev, opened.st_ino) == (root_stat.st_dev, root_stat.st_ino):
                            raise OSError(error_number, "journal directory fsync failed")
                        if operation == "file_fsync" and stat.S_ISREG(opened.st_mode) and \
                                ".seedsync-retirement-write-" in os.readlink("/proc/self/fd/" + str(descriptor)):
                            raise OSError(error_number, "journal file fsync failed")
                        return original_fsync(descriptor)
                    original_rename = os.rename
                    with patch.object(Controller, "_Controller__rename_no_replace",
                                      side_effect=OSError(errno.EINVAL, "unsupported")), \
                            patch("controller.controller.os.open", side_effect=native_open), \
                            patch("controller.controller.os.fsync", side_effect=native_fsync), \
                            patch("controller.controller.os.rename", wraps=original_rename) as rename:
                        self.assertEqual(Controller.MoveFromStagingResult.FAILED,
                                         self.controller._Controller__move_from_staging("item"))
                        claims = [call for call in rename.call_args_list
                                  if os.path.basename(call.args[1]).startswith(".seedsync-retire-")]
                        self.assertEqual([], claims)
                    self.assertEqual(b"verified payload", payload.read_bytes())
                    self.assertEqual(b"verified payload", published.read_bytes())
                    self.assertEqual([], list(source.parent.glob(".seedsync-retire-*")))
                    self.assertNotIn("item", self.controller._Controller__persist.downloaded_file_names)

    @unittest.skipUnless(os.name == "posix", "native settled journal directory sync retry")
    def test_visible_settled_record_after_failed_directory_sync_cannot_complete_until_sync_succeeds(self):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            journal, record, claim = self.interrupt(source, destination)
            (claim / ".busy").unlink()
            root_stat = os.stat(root)
            original_fsync = os.fsync
            journal_syncs = 0
            def native_fsync(descriptor):
                nonlocal journal_syncs
                opened = os.fstat(descriptor)
                if stat.S_ISDIR(opened.st_mode) and (opened.st_dev, opened.st_ino) == (
                        root_stat.st_dev, root_stat.st_ino):
                    journal_syncs += 1
                    # Admit recovery of the durable deleting record, then fail
                    # the directory entry sync after replacing it with settled.
                    if journal_syncs >= 2:
                        raise OSError(errno.EINVAL, "directory sync unsupported")
                return original_fsync(descriptor)
            with patch("controller.controller.os.fsync", side_effect=native_fsync):
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("item"))
                self.assertEqual("settled", json.loads(journal.read_text())["phase"])
                self.assertFalse(claim.exists())
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("item"))
            with patch.object(Controller, "_Controller__copy_to_publish_temporary") as copy:
                self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                                 self.controller._Controller__move_from_staging("item"))
                copy.assert_not_called()

    @patch.object(Controller, "_Controller__rename_owned_directory", side_effect=OSError(errno.EXDEV, "forced copy"))
    def test_existing_directory_merge_copies_multiple_children_without_child_journals(self, _directory_alternate):
        from controller.controller import _MoveMutationTracker
        for entrypoint in ("controller_root", "merge_no_tracker", "merge_root_tracker"):
            with self.subTest(entrypoint=entrypoint), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                source.mkdir()
                destination.mkdir()
                (destination / "existing").write_bytes(b"protected Final")
                for name in ("first", "second"):
                    (source / name).mkdir()
                    (source / name / "payload").write_bytes(name.encode("ascii"))
                record = Path(root, ".seedsync-retirement-" + "f" * 64 + ".json")
                record.write_bytes(b"unrelated record must remain unchanged")
                tracker = _MoveMutationTracker("item") if entrypoint == "merge_root_tracker" else None
                if tracker is not None:
                    tracker.retirement_journal_path = str(record)
                    tracker.retirement_context = ["root ownership"]
                original_settle = Controller._Controller__settle_retirement_journal
                def root_only_settle(src, dst, *args, **kwargs):
                    self.assertEqual(str(source), src, "merge child attempted journal settlement")
                    return original_settle(src, dst, *args, **kwargs)
                original_copy = Controller._Controller__copy_to_publish_temporary
                with patch.object(Controller, "_Controller__rename_no_replace",
                                  side_effect=OSError(errno.EXDEV, "forced copy")), \
                        patch.object(Controller, "_Controller__copy_to_publish_temporary", wraps=original_copy) as copy, \
                        patch.object(Controller, "_Controller__settle_retirement_journal", side_effect=root_only_settle) as settle, \
                        patch.object(Controller, "_Controller__write_retirement_journal",
                                     side_effect=AssertionError("merge child journal creation")):
                    if entrypoint == "controller_root":
                        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                                         self.controller._Controller__move_from_staging("item"))
                        self.assertEqual(1, settle.call_count)
                    else:
                        self.assertTrue(self.controller._Controller__merge_staging_directory_no_replace(
                            str(source), str(destination), mutation_tracker=tracker,
                        ))
                        settle.assert_not_called()
                    self.assertEqual(2, copy.call_count)
                self.assertFalse(source.exists())
                for name in ("first", "second"):
                    self.assertEqual(name.encode("ascii"), (destination / name / "payload").read_bytes())
                self.assertEqual(b"protected Final", (destination / "existing").read_bytes())
                self.assertEqual(b"unrelated record must remain unchanged", record.read_bytes())
                self.assertEqual([record], list(Path(root).glob(".seedsync-retirement-*.json")))

    @patch.object(Controller, "_Controller__rename_owned_directory", side_effect=OSError(errno.EXDEV, "forced copy"))
    def test_merge_child_retirement_failure_preserves_pending_root_without_automatic_child_recovery(self, _directory_alternate):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            source.mkdir()
            destination.mkdir()
            for name in ("first", "second"):
                (source / name).mkdir()
                (source / name / "payload").write_bytes(name.encode("ascii"))
            original_rmtree = __import__("shutil").rmtree
            def retain_claim(path, *args, **kwargs):
                if os.path.basename(path).startswith(".seedsync-retire-"):
                    raise OSError(errno.ENOTEMPTY, "child retirement remains pending")
                return original_rmtree(path, *args, **kwargs)
            with patch.object(Controller, "_Controller__rename_no_replace",
                              side_effect=OSError(errno.EXDEV, "forced copy")), \
                    patch("controller.controller.shutil.rmtree", side_effect=retain_claim), \
                    patch.object(Controller, "_Controller__write_retirement_journal",
                                 side_effect=AssertionError("merge child journal creation")):
                self.assertEqual(Controller.MoveFromStagingResult.FAILED,
                                 self.controller._Controller__move_from_staging("item"))
            claims = list(source.glob(".seedsync-retire-*"))
            self.assertEqual(1, len(claims))
            self.assertEqual(b"first", (claims[0] / "payload").read_bytes())
            self.assertEqual(b"first", (destination / "first" / "payload").read_bytes())
            self.assertTrue((source / "second").exists())
            self.assertNotIn("item", self.controller._Controller__persist.final_move_succeeded_file_names)
            # The retained private claim remains protected under established a1
            # merge behavior. C3 deliberately adds no child recovery protocol.
            with patch.object(Controller, "_Controller__rename_no_replace",
                              side_effect=OSError(errno.EXDEV, "forced copy")):
                self.assertEqual(Controller.MoveFromStagingResult.CONFLICT,
                                 self.controller._Controller__move_from_staging("item"))
            self.assertTrue(source.exists())
            self.assertEqual(b"first", (claims[0] / "payload").read_bytes())
            self.assertEqual([], list(Path(root).rglob(".seedsync-retirement-*.json")))

    def test_merge_cancelled_before_publication_preserves_source_and_final(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            source.mkdir()
            destination.mkdir()
            (destination / "existing").write_bytes(b"protected Final")
            for name in ("first", "second"):
                (source / name).write_bytes(name.encode("ascii"))
            boundary = SimpleNamespace(publish_no_replace=MagicMock(return_value=False))
            with patch.object(Controller, "_Controller__write_retirement_journal",
                              side_effect=AssertionError("cancelled merge journal creation")):
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging(
                                     "item", automatic_move_boundary=boundary,
                                 ))
            boundary.publish_no_replace.assert_called_once()
            for name in ("first", "second"):
                self.assertEqual(name.encode("ascii"), (source / name).read_bytes())
                self.assertFalse((destination / name).exists())
            self.assertEqual(b"protected Final", (destination / "existing").read_bytes())

    def test_lower_publication_defaults_to_no_journal_even_with_root_tracker_metadata(self):
        from controller.controller import _MoveMutationTracker
        for with_tracker in (False, True):
            with self.subTest(with_tracker=with_tracker), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                source.write_bytes(b"payload")
                record = Path(root, "existing-root-record")
                record.write_bytes(b"preserve")
                tracker = _MoveMutationTracker("item") if with_tracker else None
                if tracker is not None:
                    tracker.retirement_journal_path = str(record)
                    tracker.retirement_context = ["root metadata"]
                with patch.object(Controller, "_Controller__rename_no_replace",
                                  side_effect=OSError(errno.EXDEV, "forced copy")), \
                        patch.object(Controller, "_Controller__settle_retirement_journal",
                                     side_effect=AssertionError("implicit journal lookup")), \
                        patch.object(Controller, "_Controller__write_retirement_journal",
                                     side_effect=AssertionError("implicit journal creation")):
                    Controller._Controller__publish_staging_no_replace(str(source), str(destination), tracker)
                self.assertEqual(b"payload", destination.read_bytes())
                self.assertFalse(source.exists())
                self.assertEqual(b"preserve", record.read_bytes())

    def test_restart_root_proof_never_compares_namespace_ancestors_above_configured_roots(self):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            journal, record, claim = self.interrupt(source, destination)
            self.assertEqual(2, record["version"])
            self.assertEqual({str(source.parent)}, set(record["root_authority"]["staging"]["parents"]))
            self.assertEqual({str(destination.parent)}, set(record["root_authority"]["final"]["parents"]))
            (claim / ".busy").unlink()
            self.setUp()
            self.controller._Controller__staging_path = str(source.parent)
            self.controller._Controller__legacy_local_path = str(destination.parent)
            self.controller._Controller__context.config.file_path = str(Path(root, "settings.json"))
            original_identity = Controller._Controller__retirement_identity
            def bounded_identity(path):
                absolute = os.path.abspath(path)
                if not any(os.path.commonpath([absolute, str(bound)]) == str(bound)
                           for bound in (source.parent, destination.parent)):
                    raise OSError(errno.EACCES, "container ancestor identity is unavailable")
                return original_identity(path)
            with patch.object(Controller, "_Controller__retirement_identity", side_effect=bounded_identity), \
                    patch.object(Controller, "_Controller__copy_to_publish_temporary") as copy:
                self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                                 self.controller._Controller__move_from_staging("item"))
                copy.assert_not_called()

    def test_nested_staging_under_final_has_separate_bounded_root_authority(self):
        with tempfile.TemporaryDirectory() as root:
            final = Path(root, "final")
            final.mkdir()
            staging = final / "incoming"
            staging.mkdir()
            self.controller._Controller__staging_path = str(staging)
            self.controller._Controller__legacy_local_path = str(final)
            self.controller._Controller__context.config.file_path = str(Path(root, "settings.json"))
            source, destination = staging / "item", final / "item"
            journal, record, claim = self.interrupt(source, destination)
            self.assertEqual({str(staging)}, set(record["root_authority"]["staging"]["parents"]))
            self.assertEqual({str(final)}, set(record["root_authority"]["final"]["parents"]))
            (claim / ".busy").unlink()
            self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                             self.controller._Controller__move_from_staging("item"))

    def test_descendant_parent_replacement_refuses_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            source.parent.joinpath("nested").mkdir()
            destination.parent.joinpath("nested").mkdir()
            source, destination = source.parent / "nested" / "item", destination.parent / "nested" / "item"
            journal, record, claim = self.interrupt(source, destination, name="nested/item")
            self.assertEqual({str(source.parent.parent), str(source.parent)},
                             set(record["root_authority"]["staging"]["parents"]))
            source.parent.rename(source.parent.with_name("preserved"))
            source.parent.mkdir()
            with patch.object(Controller, "_Controller__write_retirement_journal") as write:
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("nested/item"))
                write.assert_not_called()
            self.assertEqual(record, json.loads(journal.read_text()))

    @unittest.skipUnless(os.name == "posix", "configured symlink root acceptance")
    def test_configured_symlink_root_is_preserved_but_retarget_and_text_change_refuse_recovery(self):
        for change in ("none", "retarget", "configured_path"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                original_source, destination = self.configure(root)
                configured = Path(root, "staging-alias")
                configured.symlink_to(original_source.parent, target_is_directory=True)
                self.controller._Controller__staging_path = str(configured)
                source = configured / "item"
                journal, record, claim = self.interrupt(source, destination)
                self.assertEqual(str(configured), record["root_authority"]["staging"]["configured_path"])
                self.assertEqual(str(original_source.parent), record["root_authority"]["staging"]["canonical_path"])
                (claim / ".busy").unlink()
                if change == "retarget":
                    configured.unlink()
                    replacement = Path(root, "replacement")
                    replacement.mkdir()
                    configured.symlink_to(replacement, target_is_directory=True)
                elif change == "configured_path":
                    self.controller._Controller__staging_path = str(original_source.parent)
                result = self.controller._Controller__move_from_staging("item")
                self.assertEqual(Controller.MoveFromStagingResult.COMPLETED if change == "none"
                                 else Controller.MoveFromStagingResult.DEFERRED, result)

    def test_explicit_root_publication_requires_supplied_confined_root_authority(self):
        for roots in (None, ("outside", "final")):
            with self.subTest(roots=roots), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                source.write_bytes(b"source")
                supplied = None if roots is None else (str(Path(root, roots[0])), str(destination.parent))
                with patch.object(Controller, "_Controller__rename_no_replace") as rename:
                    with self.assertRaises(OSError):
                        Controller._Controller__publish_staging_no_replace(
                            str(source), str(destination), retirement_journal_enabled=True,
                            retirement_roots=supplied,
                        )
                    rename.assert_not_called()
                self.assertEqual(b"source", source.read_bytes())
                self.assertFalse(destination.exists())

    def test_old_record_without_explicit_root_authority_remains_pending_and_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            journal, record, claim = self.interrupt(source, destination)
            record["version"] = 1
            record["ancestors"] = record.pop("root_authority")
            journal.write_text(json.dumps(record), encoding="utf-8")
            with patch.object(Controller, "_Controller__write_retirement_journal") as write:
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("item"))
                write.assert_not_called()
            self.assertEqual(record, json.loads(journal.read_text()))
            self.assertEqual(b"verified payload", (claim / ".busy").read_bytes())

    @patch.object(Controller, "_Controller__rename_owned_directory", side_effect=OSError(errno.EXDEV, "forced copy"))
    def test_root_replacement_after_source_recheck_is_not_adopted_at_claim_admission(self, _directory_alternate):
        with tempfile.TemporaryDirectory() as root:
            source, destination = self.configure(root)
            source.mkdir()
            (source / "payload").write_bytes(b"verified payload")
            original_remove = Controller._Controller__remove_published_source
            def replace_root_before_claim(src, *args, **kwargs):
                staging = source.parent
                preserved = staging.with_name("preserved")
                staging.rename(preserved)
                staging.mkdir()
                # Keep the source inode/content while replacing its root. The
                # pre-copy root identity must still prevent claim admission.
                (preserved / "item").rename(source)
                return original_remove(src, *args, **kwargs)
            with patch.object(Controller, "_Controller__rename_no_replace",
                              side_effect=OSError(errno.EXDEV, "forced copy")), \
                    patch.object(Controller, "_Controller__remove_published_source", side_effect=replace_root_before_claim):
                self.assertEqual(Controller.MoveFromStagingResult.FAILED,
                                 self.controller._Controller__move_from_staging("item"))
            self.assertEqual(b"verified payload", (source / "payload").read_bytes())
            self.assertEqual(b"verified payload", (destination / "payload").read_bytes())
            self.assertEqual([], list(source.parent.glob(".seedsync-retire-*")))
            self.assertEqual([], list(Path(root).glob(".seedsync-retirement-*.json")))

    def interrupt_nested_retirement(self, source, destination, depth):
        source.mkdir()
        nested = source.joinpath(*(["nested"] * depth))
        nested.mkdir(parents=True)
        (nested / "payload").write_bytes(b"verified nested payload")
        def partial(claim):
            claimed_nested = Path(claim).joinpath(*(["nested"] * depth))
            (claimed_nested / "payload").rename(claimed_nested / ".busy")
            raise OSError(errno.ENOTEMPTY, "nested retirement pending")
        with patch.object(Controller, "_Controller__rename_no_replace",
                          side_effect=OSError(errno.EXDEV, "forced strict copy")), \
                patch.object(Controller, "_Controller__rename_owned_directory",
                             side_effect=OSError(errno.EXDEV, "forced owned-directory copy")), \
                patch("controller.controller.shutil.rmtree", side_effect=partial):
            self.assertEqual(Controller.MoveFromStagingResult.FAILED,
                             self.controller._Controller__move_from_staging("item"))
        journal = next(Path(self.controller._Controller__context.config.file_path).parent.glob(
            ".seedsync-retirement-*.json"))
        record = json.loads(journal.read_text())
        return journal, record, Path(record["claim"])

    def test_nested_and_multidepth_retirement_restart_settles_after_delayed_residue_without_recopy(self):
        for depth in (1, 3):
            with self.subTest(depth=depth), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                journal, record, claim = self.interrupt_nested_retirement(source, destination, depth)
                leaf = claim.joinpath(*(["nested"] * depth))
                self.assertEqual("deleting", record["phase"])
                self.assertEqual({".", *(os.path.join(*(["nested"] * level))
                                         for level in range(1, depth + 1))}, set(record["directories"]))
                self.assertEqual(b"verified nested payload",
                                 destination.joinpath(*(["nested"] * depth), "payload").read_bytes())
                self.setUp()
                self.controller._Controller__staging_path = str(source.parent)
                self.controller._Controller__legacy_local_path = str(destination.parent)
                self.controller._Controller__context.config.file_path = str(Path(root, "settings.json"))
                with patch.object(Controller, "_Controller__copy_to_publish_temporary",
                                  side_effect=AssertionError("recovery recopied Final")), \
                        patch("controller.controller.shutil.rmtree",
                              side_effect=AssertionError("recovery recursively deleted residue")):
                    self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                     self.controller._Controller__move_from_staging("item"))
                    self.assertEqual(b"verified nested payload", (leaf / ".busy").read_bytes())
                    (leaf / ".busy").unlink()  # external holder releases its residue
                    self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                                     self.controller._Controller__move_from_staging("item"))
                    self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                                     self.controller._Controller__move_from_staging("item"))
                self.assertFalse(claim.exists())
                self.assertFalse(source.exists())
                self.assertEqual("settled", json.loads(journal.read_text())["phase"])

    def test_nested_recovery_retains_foreign_and_replaced_empty_directories(self):
        for change in ("foreign", "replaced"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                source, destination = self.configure(root)
                journal, record, claim = self.interrupt_nested_retirement(source, destination, 2)
                leaf = claim / "nested" / "nested"
                (leaf / ".busy").unlink()
                if change == "foreign":
                    protected = leaf / "foreign"
                    protected.mkdir()
                else:
                    protected = source.parent / "preserved-empty-directory"
                    leaf.rename(protected)
                    leaf.mkdir()
                    expected_identity = record["directories"][os.path.join("nested", "nested")]
                    self.assertNotEqual(expected_identity, Controller._Controller__retirement_identity(str(leaf)))
                with patch.object(Controller, "_Controller__copy_to_publish_temporary",
                                  side_effect=AssertionError("recovery recopied Final")), \
                        patch("controller.controller.shutil.rmtree",
                              side_effect=AssertionError("recovery recursively deleted residue")):
                    self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                     self.controller._Controller__move_from_staging("item"))
                self.assertTrue(claim.is_dir())
                self.assertTrue(leaf.is_dir())
                self.assertTrue(protected.is_dir())
                self.assertEqual("deleting", json.loads(journal.read_text())["phase"])
                self.assertEqual(b"verified nested payload",
                                 (destination / "nested" / "nested" / "payload").read_bytes())
