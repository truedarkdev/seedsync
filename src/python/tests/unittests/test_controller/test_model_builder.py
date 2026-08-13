# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime
from types import SimpleNamespace

from system import SystemFile
from lftp import LftpJobStatus
from model import ModelError, ModelFile, Model
from controller import ModelBuilder
from controller.scan import LocalScanner
from controller.model_builder import _RecentLiveTransferSnapshot
from controller.model_updater import ModelUpdater
from controller.extract import ExtractStatus
from controller.validate import ValidateStatus
from common.breadcrumb_trace import BreadcrumbTraceCollector
from common.performance_diagnostics import (
    COUNTER_PAIR_SAFETY_REJECT_STATUS_ONLY_NAME,
    COUNTER_PAIR_SAFETY_REJECT_ACTIVE_ONLY_NAME,
    COUNTER_PAIR_SAFETY_REJECT_EXTRACTED_BARE_MARKER,
    DURATION_MODEL_BUILDER_SET_ACTIVE_FILES,
    DURATION_MODEL_BUILDER_SET_LFTP_STATUSES,
    DURATION_MODEL_BUILDER_SET_LOCAL_FILES,
    DURATION_MODEL_BUILDER_SET_REMOTE_FILES,
    DURATION_MODEL_BUILDER_SET_STOPPED_FILES,
    MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
    MODEL_BUILDER_INVALIDATION_CLEAR,
    MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
    MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
    MODEL_BUILDER_INVALIDATION_EXPLICIT,
    MODEL_BUILDER_INVALIDATION_EXTRACTED_FILES,
    MODEL_BUILDER_INVALIDATION_EXTRACT_STATUSES,
    MODEL_BUILDER_INVALIDATION_FINAL_MOVE_SUCCEEDED_FILES,
    MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
    MODEL_BUILDER_INVALIDATION_LOCAL_FILES,
    MODEL_BUILDER_INVALIDATION_LOCAL_ROOT_PATHS,
    MODEL_BUILDER_INVALIDATION_MOVE_FAILED_FILES,
    MODEL_BUILDER_INVALIDATION_REMOTE_FILES,
    MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
    MODEL_BUILDER_INVALIDATION_UNKNOWN_LOCAL_PAIRS,
    MODEL_BUILDER_INVALIDATION_VALIDATION_STATUSES,
    PerformanceDiagnosticsCollector,
)


class TestModelBuilder(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger(TestModelBuilder.__name__)
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)
        self.model_builder = ModelBuilder()
        self.model_builder.set_base_logger(logger)

    def __enable_trace(self, enabled: bool = True, max_entries: int = 128) -> BreadcrumbTraceCollector:
        self.__trace_enabled = [enabled]
        collector = BreadcrumbTraceCollector(lambda: self.__trace_enabled[0], max_entries=max_entries)
        self.model_builder.set_stop_resume_trace_breadcrumb(collector.create_emitter())
        return collector

    @staticmethod
    def __trace_entries(collector: BreadcrumbTraceCollector) -> list[dict[str, object]]:
        # Breadcrumb emitters use a multiprocessing.Queue so worker processes
        # never block model work. Give its feeder a bounded moment to publish a
        # same-cycle burst before asserting the complete retained window.
        time.sleep(0.05)
        return collector.snapshot()["entries"]

    def __assert_generic_lifecycle_trace_is_private(
            self, entries: list[dict[str, object]], subject_file_id: str,
    ) -> None:
        generic_entries = [
            entry for entry in entries
            if entry["message"] in {
                "persist_authority_before", "persist_authority_after",
                "model_candidate", "model_live_publication",
            }
        ]
        for entry in generic_entries:
            self.assertIsNone(entry["file_id"])
            self.assertIsNone(entry["path_pair_id"])
            self.assertIsNone(entry["path_pair_name"])
            self.assertTrue(entry["corr_id"])
            self.assertNotIn(subject_file_id, str(entry))

    def __set_transfer_sources(self, file_name: str, path_pair_id: str, local_size: int = 650) -> str:
        remote_file = SystemFile(file_name, 1000, False)
        remote_file.path_pair_id = path_pair_id
        local_file = SystemFile(file_name, local_size, False)
        local_file.path_pair_id = path_pair_id
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        return ModelFile.build_file_id(file_name, path_pair_id)

    def test_progressive_root_build_does_not_walk_or_replace_retained_roots(self):
        pair_a = SystemFile("a.bin", 10, False)
        pair_a.path_pair_id = "pair-a"
        pair_b = SystemFile("b.bin", 20, False)
        pair_b.path_pair_id = "pair-b"
        self.model_builder.set_remote_files([pair_a, pair_b])
        self.model_builder.set_downloaded_files(set())
        retained_model = self.model_builder.build_model()

        changed_a = SystemFile("a.bin", 11, False)
        changed_a.path_pair_id = "pair-a"
        delta_model = self.model_builder.build_progressive_roots(
            [], [changed_a], {"pair-a"},
        )

        self.assertEqual({ModelFile.build_file_id("a.bin", "pair-a")}, delta_model.get_file_ids())
        self.assertEqual(11, delta_model.get_file(ModelFile.build_file_id("a.bin", "pair-a")).remote_size)
        self.assertIs(retained_model, self.model_builder.build_model())
        self.assertEqual(
            {
                ModelFile.build_file_id("a.bin", "pair-a"),
                ModelFile.build_file_id("b.bin", "pair-b"),
            },
            retained_model.get_file_ids(),
        )

    def test_authoritative_pair_build_replaces_only_completed_pair_and_adopts_after_publication(self):
        first = SystemFile("old.bin", 10, False)
        first.path_pair_id = "pair-a"
        untouched = SystemFile("idle.bin", 20, False)
        untouched.path_pair_id = "pair-b"
        self.model_builder.set_remote_files([first, untouched])
        live_model = self.model_builder.build_model()
        untouched_before = live_model.get_file(ModelFile.build_file_id("idle.bin", "pair-b"))

        replacement = SystemFile("new.bin", 30, False)
        replacement.path_pair_id = "pair-a"
        pair_build = self.model_builder.build_authoritative_pair_roots(
            "pair-a", [], [replacement], set(),
        )

        self.assertIsNotNone(pair_build)
        assert pair_build is not None
        self.assertTrue(self.model_builder.authorize_authoritative_pair_delta(
            lambda file_id: file_id in live_model.get_file_ids(), pair_build,
        ))
        live_model.remove_file(ModelFile.build_file_id("old.bin", "pair-a"))
        live_model.add_file(pair_build.model.get_file(ModelFile.build_file_id("new.bin", "pair-a")))
        self.model_builder.adopt_authoritative_pair_delta(live_model, pair_build)

        self.assertEqual(
            {ModelFile.build_file_id("new.bin", "pair-a"), ModelFile.build_file_id("idle.bin", "pair-b")},
            live_model.get_file_ids(),
        )
        self.assertIs(untouched_before, live_model.get_file(ModelFile.build_file_id("idle.bin", "pair-b")))
        self.assertEqual(
            {ModelFile.build_file_id("new.bin", "pair-a")},
            set(self.model_builder._ModelBuilder__remote_files_by_pair["pair-a"]),
        )
        self.assertEqual(
            {ModelFile.build_file_id("idle.bin", "pair-b")},
            set(self.model_builder._ModelBuilder__remote_files_by_pair["pair-b"]),
        )
        self.assertFalse(self.model_builder.has_changes())

    def test_authoritative_pair_build_adopts_cross_pair_duplicate_basename(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        self.model_builder.set_performance_diagnostics(diagnostics)
        first = SystemFile("shared.bin", 10, False)
        first.path_pair_id = "pair-a"
        second = SystemFile("shared.bin", 20, False)
        second.path_pair_id = "pair-b"
        self.model_builder.set_remote_files([first, second])
        live_model = self.model_builder.build_model()
        second_id = ModelFile.build_file_id("shared.bin", "pair-b")
        untouched_before = live_model.get_file(second_id)

        replacement = SystemFile("shared.bin", 30, False)
        replacement.path_pair_id = "pair-a"

        pair_build = self.model_builder.build_authoritative_pair_roots(
            "pair-a", [], [replacement], set(),
        )
        self.assertIsNotNone(pair_build)
        assert pair_build is not None
        self.assertTrue(self.model_builder.authorize_authoritative_pair_delta(
            lambda file_id: file_id in live_model.get_file_ids(), pair_build,
        ))
        first_id = ModelFile.build_file_id("shared.bin", "pair-a")
        live_model.update_file(pair_build.model.get_file(first_id))
        self.model_builder.adopt_authoritative_pair_delta(live_model, pair_build)

        self.assertEqual(30, live_model.get_file(first_id).remote_size)
        self.assertIs(untouched_before, live_model.get_file(second_id))
        self.assertFalse(self.model_builder.has_changes())

    def test_authoritative_pair_build_falls_back_for_cross_pair_local_root_arbitration(self):
        for other_is_managed in (True, False):
            with self.subTest(other_is_managed=other_is_managed):
                builder = ModelBuilder()
                builder.set_local_root_paths({
                    "pair-a": "/shared-local-root",
                    "pair-b": "/shared-local-root/.",
                })
                selected = SystemFile("shared.bin", 10, False)
                selected.path_pair_id = "pair-a"
                other_local = SystemFile("shared.bin", 20, False)
                other_local.path_pair_id = "pair-b"
                builder.set_local_files([selected, other_local])
                other_remote = SystemFile("shared.bin", 20, False)
                other_remote.path_pair_id = "pair-b"
                builder.set_remote_files([other_remote] if other_is_managed else [])

                before = builder.build_model()
                other_id = ModelFile.build_file_id("shared.bin", "pair-b")
                self.assertEqual({other_id}, before.get_file_ids())
                replacement = SystemFile("shared.bin", 11, False)
                replacement.path_pair_id = "pair-a"

                # A pair candidate would reuse pair-b's live root and skip
                # the global local-root winner/visibility arbitration.
                self.assertIsNone(builder.build_authoritative_pair_roots(
                    "pair-a", [replacement], [], set(),
                ))

                # The normal global rebuild preserves the one visible winner.
                builder.set_local_files([replacement, other_local])
                self.assertEqual({other_id}, builder.build_model().get_file_ids())

    def test_authoritative_pair_build_attributes_bare_extracted_marker_without_rejecting_safe_pair(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        self.model_builder.set_performance_diagnostics(diagnostics)
        selected = SystemFile("shared.bin", 10, False)
        selected.path_pair_id = "pair-a"
        unrelated = SystemFile("shared.bin", 20, False)
        unrelated.path_pair_id = "pair-b"
        self.model_builder.set_remote_files([selected, unrelated])
        self.model_builder.build_model()

        self.assertIsNotNone(self.model_builder.build_authoritative_pair_roots(
            "pair-a", [], [selected], set(),
        ))
        self.assertEqual(
            0,
            diagnostics.snapshot()["counters"][COUNTER_PAIR_SAFETY_REJECT_EXTRACTED_BARE_MARKER],
        )

        self.model_builder.set_extracted_files({"shared.bin"})
        self.assertIsNone(self.model_builder.build_authoritative_pair_roots(
            "pair-a", [], [selected], set(),
        ))
        self.assertEqual(
            1,
            diagnostics.snapshot()["counters"][COUNTER_PAIR_SAFETY_REJECT_EXTRACTED_BARE_MARKER],
        )

    def test_authoritative_pair_build_rejects_cross_pair_status_only_duplicate_basename(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        self.model_builder.set_performance_diagnostics(diagnostics)
        selected = SystemFile("release.bin", 10, False)
        selected.path_pair_id = "pair-a"
        self.model_builder.set_remote_files([selected])
        self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "release.bin", "",
        )
        status.path_pair_id = "pair-b"
        self.model_builder.set_lftp_statuses([status])

        self.assertIsNone(self.model_builder.build_authoritative_pair_roots(
            "pair-a", [], [selected], set(),
        ))
        self.assertEqual(
            1,
            diagnostics.snapshot()["counters"][COUNTER_PAIR_SAFETY_REJECT_STATUS_ONLY_NAME],
        )

    def test_authoritative_pair_build_rejects_cross_pair_active_only_duplicate_basename(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        self.model_builder.set_performance_diagnostics(diagnostics)
        selected = SystemFile("release.bin", 10, False)
        selected.path_pair_id = "pair-a"
        self.model_builder.set_remote_files([selected])
        self.model_builder.build_model()
        active = SystemFile("release.bin", 5, False)
        active.path_pair_id = "pair-b"
        self.model_builder.set_active_files([active])

        self.assertIsNone(self.model_builder.build_authoritative_pair_roots(
            "pair-a", [], [selected], set(),
        ))
        self.assertEqual(
            1,
            diagnostics.snapshot()["counters"][COUNTER_PAIR_SAFETY_REJECT_ACTIVE_ONLY_NAME],
        )

    def test_authoritative_pair_build_retains_selected_recent_transfer_snapshot(self):
        remote = SystemFile("active.bin", 1000, False)
        remote.path_pair_id = "pair-a"
        local = SystemFile("active.bin", 950, False)
        local.path_pair_id = "pair-a"
        self.model_builder.set_remote_files([remote])
        self.model_builder.set_local_files([local])
        self.model_builder.build_model()
        file_id = ModelFile.build_file_id("active.bin", "pair-a")
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots[file_id] = _RecentLiveTransferSnapshot(
            root_file_id=file_id, size_local=975, percent_local=97, speed=1000, eta=5,
        )

        pair_build = self.model_builder.build_authoritative_pair_roots(
            "pair-a", [local], [remote], set(),
        )

        self.assertIsNotNone(pair_build)
        assert pair_build is not None
        rendered = pair_build.model.get_file(file_id)
        self.assertEqual(975, rendered.transferred_size)
        self.assertEqual(97, rendered.download_progress)

    def test_authoritative_pair_adoption_preserves_later_invalidation(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        self.model_builder.set_remote_files([old])
        live_model = self.model_builder.build_model()
        replacement = SystemFile("new.bin", 20, False)
        replacement.path_pair_id = "pair-a"
        pair_build = self.model_builder.build_authoritative_pair_roots(
            "pair-a", [], [replacement], set(),
        )
        assert pair_build is not None
        live_model.remove_file(ModelFile.build_file_id("old.bin", "pair-a"))
        live_model.add_file(pair_build.model.get_file(ModelFile.build_file_id("new.bin", "pair-a")))
        self.model_builder.set_downloaded_files({ModelFile.build_file_id("later.bin", "pair-a")})

        self.model_builder.adopt_authoritative_pair_delta(live_model, pair_build)

        self.assertTrue(self.model_builder.has_changes())
        self.assertTrue(self.model_builder._ModelBuilder__pending_invalidation_tokens)

    def test_authoritative_pair_build_reports_staged_terminalizable_collision(self):
        old = SystemFile("old.bin", 1, False)
        old.path_pair_id = "pair-a"
        self.model_builder.set_local_files([old])
        self.model_builder.set_remote_files([old])
        self.model_builder.build_model()
        remote = SystemFile("release", 10, True)
        remote.path_pair_id = "pair-a"
        remote.add_child(SystemFile("entry.bin", 10, False))
        local = SystemFile("release", 10, True)
        local.path_pair_id = "pair-a"
        collision = SystemFile("entry.bin", 10, False, is_staging=True)
        collision.has_staging_collision = True
        local.add_child(collision)

        pair_build = self.model_builder.build_authoritative_pair_roots(
            "pair-a", [local], [remote], set(),
        )

        self.assertIsNotNone(pair_build)
        assert pair_build is not None
        release_id = ModelFile.build_file_id("release", "pair-a")
        self.assertEqual({release_id}, pair_build.unresolved_staging_collision_file_ids)
        self.assertEqual({release_id}, pair_build.terminalizable_staging_collision_file_ids)

    def test_authoritative_pair_auth_rejection_commits_sources_and_requires_full_rebuild(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        self.model_builder.set_remote_files([old])
        live_model = self.model_builder.build_model()
        replacement = SystemFile("new.bin", 20, False)
        replacement.path_pair_id = "pair-a"
        pair_build = self.model_builder.build_authoritative_pair_roots(
            "pair-a", [], [replacement], set(),
        )

        self.assertIsNotNone(pair_build)
        assert pair_build is not None
        self.model_builder.set_downloaded_files(set())
        self.assertFalse(self.model_builder.authorize_authoritative_pair_delta(
            lambda file_id: file_id in live_model.get_file_ids(), pair_build,
        ))
        self.model_builder.commit_authoritative_pair_sources_for_full_rebuild(pair_build)

        self.assertEqual(
            {ModelFile.build_file_id("new.bin", "pair-a")},
            set(self.model_builder._ModelBuilder__remote_files_by_pair["pair-a"]),
        )
        self.assertTrue(self.model_builder.has_changes())

    def test_active_transfer_delta_builds_only_known_changed_root_and_adopts_live_model(self):
        active = SystemFile("active.bin", 100, False)
        retained = SystemFile("retained.bin", 200, False)
        self.model_builder.set_remote_files([active, retained])
        live_model = self.model_builder.build_model()

        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        self.model_builder.set_lftp_statuses([status])

        self.assertTrue(self.model_builder.has_only_pending_active_transfer_delta())
        self.assertEqual({"active.bin"}, self.model_builder.active_transfer_delta_file_ids(
            live_model.get_file_ids(),
        ))
        partial_build = self.model_builder.build_active_transfer_roots({"active.bin"})
        partial_model = partial_build.model
        self.assertEqual({"active.bin"}, partial_model.get_file_ids())
        old_active = live_model.get_file("active.bin")
        new_active = partial_model.get_file("active.bin")
        live_model.update_file(new_active)
        live_model.set_tree_file_count(
            live_model.tree_file_count + 1 - 1
        )

        self.assertTrue(self.model_builder.authorize_active_transfer_delta(
            live_model.get_file_ids(), {"active.bin"}, partial_build,
        ))
        self.model_builder.adopt_active_transfer_delta(live_model, {"active.bin"}, partial_build)
        self.assertFalse(self.model_builder.has_changes())
        self.assertEqual(25, live_model.get_file("active.bin").transferred_size)
        self.assertEqual(200, live_model.get_file("retained.bin").remote_size)
        self.assertNotEqual(old_active, new_active)

    def test_active_transfer_delta_falls_back_for_unknown_or_terminal_status_root(self):
        self.model_builder.set_remote_files([SystemFile("known.bin", 100, False)])
        live_model = self.model_builder.build_model()

        unknown = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "unknown.bin", "",
        )
        self.model_builder.set_lftp_statuses([unknown])
        self.assertIsNone(self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()))

    def test_active_transfer_delta_publishes_single_stopped_root_without_full_rebuild(self):
        self.model_builder.set_remote_files([SystemFile("active.bin", 100, False)])
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        self.model_builder.set_lftp_statuses([status])
        live_model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, live_model.get_file("active.bin").state)

        self.model_builder.set_stopped_files({"active.bin"})

        self.assertEqual(
            {"active.bin"},
            self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()),
        )
        partial = self.model_builder.build_active_transfer_roots({"active.bin"})
        self.assertEqual(ModelFile.State.DEFAULT, partial.model.get_file("active.bin").state)

        self.model_builder.build_model()
        self.model_builder.set_lftp_statuses([])
        self.assertEqual(
            {"active.bin"},
            self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()),
        )

    def test_active_transfer_delta_accepts_known_downloaded_timestamp_change(self):
        self.model_builder.set_remote_files([SystemFile("active.bin", 100, False)])
        live_model = self.model_builder.build_model()

        self.model_builder.set_downloaded_timestamps({"active.bin": 10.0})

        self.assertEqual(
            {"active.bin"},
            self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()),
        )
        partial = self.model_builder.build_active_transfer_roots({"active.bin"})
        self.assertEqual({"active.bin"}, partial.model.get_file_ids())

    def test_confirmed_local_delete_evicts_transfer_snapshots_as_exact_root_delta(self):
        remote = SystemFile("active.bin", 100, False)
        remote.path_pair_id = "pair-a"
        local = SystemFile("active.bin", 25, False, is_staging=True)
        local.path_pair_id = "pair-a"
        active = SystemFile("active.bin", 25, False)
        active.path_pair_id = "pair-a"
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING,
            "active.bin", "",
        )
        status.path_pair_id = "pair-a"
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        self.model_builder.set_remote_files([remote])
        self.model_builder.set_local_files([local])
        self.model_builder.set_active_files([active])
        self.model_builder.set_lftp_statuses([status])
        live_model = self.model_builder.build_model()
        file_id = ModelFile.build_file_id("active.bin", "pair-a")

        self.model_builder.set_stopped_files({file_id})
        self.model_builder.build_model()
        self.model_builder.set_lftp_statuses([])
        self.model_builder.confirm_local_deletions({file_id})

        self.assertEqual(
            {file_id},
            self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()),
        )
        partial = self.model_builder.build_active_transfer_roots({file_id})
        deleted = partial.model.get_file(file_id)
        self.assertFalse(deleted.local_present)
        self.assertIsNone(deleted.local_size)
        self.assertEqual(ModelFile.State.DEFAULT, deleted.state)

    def test_active_transfer_delta_accepts_active_scan_only_change_for_known_root(self):
        self.model_builder.set_remote_files([SystemFile("active.bin", 100, False)])
        live_model = self.model_builder.build_model()
        active = SystemFile("active.bin", 25, False)
        self.model_builder.set_active_files([active])

        self.assertEqual(
            {"active.bin"},
            self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()),
        )

    def test_active_scan_inherits_unique_scoped_status_identity(self):
        remote = SystemFile("active.bin", 100, False)
        remote.path_pair_id = "pair-a"
        self.model_builder.set_remote_files([remote])
        live_model = self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING,
            "active.bin", "",
        )
        status.path_pair_id = "pair-a"
        self.model_builder.set_lftp_statuses([status])
        active = SystemFile("active.bin", 25, False)
        self.model_builder.set_active_files([active])

        scoped_id = ModelFile.build_file_id("active.bin", "pair-a")
        self.assertEqual({scoped_id}, self.model_builder.active_transfer_delta_file_ids(
            live_model.get_file_ids(),
        ))

        # Re-establish a cached baseline, then prove that status disappearance
        # is never treated as partial-root removal authority.
        self.model_builder.build_model()
        self.model_builder.set_lftp_statuses([])
        self.assertIsNone(self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()))

    def test_active_transfer_delta_uses_scoped_identity_not_same_basename_pair(self):
        first = SystemFile("release.bin", 100, False)
        first.path_pair_id = "first"
        second = SystemFile("release.bin", 200, False)
        second.path_pair_id = "second"
        self.model_builder.set_remote_files([first, second])
        live_model = self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "release.bin", "",
        )
        status.path_pair_id = "second"
        status.total_transfer_state = LftpJobStatus.TransferState(50, 200, 25, 10, 15)
        self.model_builder.set_lftp_statuses([status])

        second_id = ModelFile.build_file_id("release.bin", "second")
        self.assertEqual({second_id}, self.model_builder.active_transfer_delta_file_ids(
            live_model.get_file_ids(),
        ))
        self.assertEqual({second_id}, self.model_builder.build_active_transfer_roots(
            {second_id},
        ).model.get_file_ids())

    def test_active_transfer_delta_accepts_stopped_input_but_falls_back_for_scan_change(self):
        self.model_builder.set_remote_files([SystemFile("root.bin", 100, False)])
        live_model = self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "root.bin", "",
        )
        self.model_builder.set_lftp_statuses([status])
        self.model_builder.set_stopped_files({"root.bin"})
        self.assertEqual(
            {"root.bin"},
            self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()),
        )

        self.model_builder.build_model()
        running = LftpJobStatus(
            2, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root.bin", "",
        )
        self.model_builder.set_lftp_statuses([running])
        self.model_builder.set_remote_files([SystemFile("root.bin", 101, False)])
        self.assertIsNone(self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()))

    def test_active_transfer_delta_adoption_does_not_mask_later_full_reconciliation(self):
        self.model_builder.set_remote_files([SystemFile("root.bin", 100, False)])
        live_model = self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root.bin", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(20, 100, 20, 10, 8)
        self.model_builder.set_lftp_statuses([status])
        partial = self.model_builder.build_active_transfer_roots({"root.bin"})
        live_model.update_file(partial.model.get_file("root.bin"))
        self.assertTrue(self.model_builder.authorize_active_transfer_delta(
            live_model.get_file_ids(), {"root.bin"}, partial,
        ))
        self.model_builder.adopt_active_transfer_delta(live_model, {"root.bin"}, partial)

        self.model_builder.set_remote_files([SystemFile("root.bin", 101, False)])
        self.assertTrue(self.model_builder.has_changes())
        self.assertEqual(101, self.model_builder.build_model().get_file("root.bin").remote_size)

    def test_active_transfer_delta_combines_changed_status_and_active_roots(self):
        self.model_builder.set_remote_files([
            SystemFile("first.bin", 100, False),
            SystemFile("second.bin", 100, False),
        ])
        live_model = self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "first.bin", "",
        )
        self.model_builder.set_lftp_statuses([status])
        self.model_builder.set_active_files([SystemFile("second.bin", 10, False)])

        self.assertEqual(
            {"first.bin", "second.bin"},
            self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()),
        )

    def test_active_transfer_delta_rejects_legacy_extraction_name_ambiguity(self):
        first = SystemFile("release.bin", 100, False)
        first.path_pair_id = "first"
        second = SystemFile("release.bin", 100, False)
        second.path_pair_id = "second"
        self.model_builder.set_remote_files([first, second])
        self.model_builder.set_extracted_files({"release.bin"})
        live_model = self.model_builder.build_model()
        self.assertNotEqual(ModelFile.State.EXTRACTED, live_model.get_file(
            ModelFile.build_file_id("release.bin", "first"),
        ).state)
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "release.bin", "",
        )
        status.path_pair_id = "first"
        self.model_builder.set_lftp_statuses([status])

        self.assertIsNone(self.model_builder.active_transfer_delta_file_ids(live_model.get_file_ids()))

    def test_active_transfer_delta_selector_does_not_walk_effective_source_maps(self):
        self.model_builder.set_remote_files([SystemFile("root.bin", 100, False)])
        live_model = self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root.bin", "",
        )
        self.model_builder.set_lftp_statuses([status])
        with patch.object(
                self.model_builder,
                "_ModelBuilder__build_effective_local_files",
                side_effect=AssertionError("selector must not walk source maps"),
        ):
            self.assertEqual({"root.bin"}, self.model_builder.active_transfer_delta_file_ids(
                lambda file_id: file_id == "root.bin",
            ))

    def test_active_transfer_delta_rejects_legacy_extraction_ambiguity_with_status_only_root(self):
        self.model_builder.set_remote_files([SystemFile("release.bin", 100, False)])
        self.model_builder.set_extracted_files({"release.bin"})
        first = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "release.bin", "",
        )
        second = LftpJobStatus(
            2, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "release.bin", "",
        )
        second.path_pair_id = "other"
        self.model_builder.set_lftp_statuses([first, second])
        live_model = self.model_builder.build_model()
        changed_first = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "release.bin", "",
        )
        changed_first.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        self.model_builder.set_lftp_statuses([changed_first, second])

        self.assertIsNone(self.model_builder.active_transfer_delta_file_ids(
            lambda file_id: file_id in live_model.get_file_ids(),
        ))

    def test_active_transfer_delta_rejects_legacy_extraction_ambiguity_with_active_only_root(self):
        selected = SystemFile("release.bin", 100, False)
        selected.path_pair_id = "selected"
        self.model_builder.set_remote_files([selected])
        self.model_builder.set_extracted_files({"release.bin"})
        active_only = SystemFile("release.bin", 10, False)
        active_only.path_pair_id = "active-only"
        self.model_builder.set_active_files([active_only])
        live_model = self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "release.bin", "",
        )
        status.path_pair_id = "selected"
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        self.model_builder.set_lftp_statuses([status])

        self.assertIsNone(self.model_builder.active_transfer_delta_file_ids(
            lambda file_id: file_id == ModelFile.build_file_id("release.bin", "selected"),
        ))

    def test_active_transfer_delta_commits_recent_snapshot_for_later_status_absence(self):
        self.model_builder.set_remote_files([SystemFile("root.bin", 100, False)])
        self.model_builder.set_local_files([SystemFile("root.bin", 20, False, is_staging=True)])
        live_model = self.model_builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root.bin", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(65, 100, 65, 10, 4)
        self.model_builder.set_lftp_statuses([status])
        partial = self.model_builder.build_active_transfer_roots({"root.bin"})
        self.assertTrue(self.model_builder.authorize_active_transfer_delta(
            live_model.get_file_ids(), {"root.bin"}, partial,
        ))
        live_model.update_file(partial.model.get_file("root.bin"))
        self.model_builder.adopt_active_transfer_delta(live_model, {"root.bin"}, partial)

        self.assertEqual(65, self.model_builder._ModelBuilder__recent_live_transfer_snapshots[
            "root.bin"
        ].size_local)
        self.model_builder.set_lftp_statuses([])
        reconciled = self.model_builder.build_model()
        self.assertEqual(65, reconciled.get_file("root.bin").transferred_size)

    def test_active_transfer_delta_authorization_rejection_leaves_live_model_untouched(self):
        self.model_builder.set_remote_files([SystemFile("root.bin", 100, False)])
        live_model = self.model_builder.build_model()
        before_version = live_model.version
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root.bin", "",
        )
        self.model_builder.set_lftp_statuses([status])
        partial = self.model_builder.build_active_transfer_roots({"root.bin"})
        self.model_builder.set_remote_files([SystemFile("root.bin", 101, False)])

        self.assertFalse(self.model_builder.authorize_active_transfer_delta(
            live_model.get_file_ids(), {"root.bin"}, partial,
        ))
        self.assertEqual(before_version, live_model.version)
        self.assertTrue(self.model_builder.has_changes())

    def test_build_model_suppresses_temp_model_logs_without_mutating_shared_dummy_logger(self):
        root_logger = logging.getLogger()
        root_level = root_logger.level
        dummy_logger = logging.getLogger("dummy")
        dummy_model_logger = logging.getLogger("dummy.Model")
        real_model_builder_logger = self.model_builder.logger
        original_propagate = dummy_logger.propagate
        original_model_propagate = dummy_model_logger.propagate
        original_real_propagate = real_model_builder_logger.propagate
        captured_records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                captured_records.append(record)

        handler = CaptureHandler()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        dummy_logger.propagate = True
        dummy_model_logger.propagate = True
        try:
            self.model_builder.clear()
            self.model_builder.set_remote_files([SystemFile("a", 0, False)])
            self.model_builder.build_model()
            dummy_propagate = dummy_logger.propagate
            dummy_model_propagate = dummy_model_logger.propagate
            real_model_builder_propagate = real_model_builder_logger.propagate
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(root_level)
            dummy_logger.propagate = original_propagate
            dummy_model_logger.propagate = original_model_propagate

        self.assertTrue(dummy_propagate)
        self.assertTrue(dummy_model_propagate)
        self.assertEqual(original_real_propagate, real_model_builder_propagate)
        self.assertFalse(any(record.name.startswith("dummy.Model") for record in captured_records))

    def test_presence_signals_distinguish_zero_byte_files_and_empty_remote_trees(self):
        remote_zero = SystemFile("zero.bin", 0, False)
        self.model_builder.set_remote_files([remote_zero])
        model = self.model_builder.build_model()
        zero_file = model.get_file("zero.bin")
        self.assertTrue(zero_file.remote_present)
        self.assertFalse(zero_file.local_present)
        self.assertTrue(zero_file.remote_has_transferable_content)

        self.model_builder.clear()
        empty_remote_dir = SystemFile("empty", 0, True)
        self.model_builder.set_remote_files([empty_remote_dir])
        empty_model = self.model_builder.build_model()
        self.assertEqual(set(), empty_model.get_file_names())

        self.model_builder.clear()
        remote_tree = SystemFile("tree", 0, True)
        remote_tree.add_child(SystemFile("zero.bin", 0, False))
        self.model_builder.set_remote_files([remote_tree])
        tree_model = self.model_builder.build_model()
        tree = tree_model.get_file("tree")
        self.assertTrue(tree.remote_present)
        self.assertTrue(tree.remote_has_transferable_content)
        self.assertTrue(tree.get_children()[0].remote_has_transferable_content)
        self.assertEqual(2, tree_model.tree_file_count)

    def test_adopt_applied_model_reuses_live_model_for_unchanged_partial_roots(self):
        self.model_builder.set_remote_files([
            SystemFile("updated-root", 10, False),
            SystemFile("unchanged-root", 20, False),
        ])
        built_model = self.model_builder.build_model()
        live_model = Model()

        self.model_builder.adopt_applied_model(built_model, live_model)

        self.assertIs(live_model, self.model_builder.build_model())
        self.assertFalse(self.model_builder.has_changes())

    def test_adopt_applied_model_does_not_mask_post_build_invalidation(self):
        self.model_builder.set_remote_files([SystemFile("root", 10, False)])
        built_model = self.model_builder.build_model()
        self.model_builder.set_remote_files([SystemFile("root", 11, False)])
        live_model = Model()

        self.model_builder.adopt_applied_model(built_model, live_model)

        rebuilt_model = self.model_builder.build_model()
        self.assertIsNot(live_model, rebuilt_model)
        self.assertEqual(11, rebuilt_model.get_file("root").remote_size)

    def test_adopt_applied_model_accepts_explicitly_applied_side_effect(self):
        self.model_builder.set_remote_files([SystemFile("root", 10, False)])
        built_model = self.model_builder.build_model()
        downloaded_token = self.model_builder.set_downloaded_files({"root"})
        self.assertTrue(self.model_builder.invalidation_token_matches_file(
            downloaded_token, "root",
        ))
        live_model = Model()
        applied_file = built_model.get_file("root")
        applied_file.state = ModelFile.State.DOWNLOADED
        live_model.add_file(applied_file)

        self.model_builder.adopt_applied_model(
            built_model,
            live_model,
            {downloaded_token},
        )

        self.assertIs(live_model, self.model_builder.build_model())
        self.assertFalse(self.model_builder.has_changes())

    def test_adopt_applied_model_preserves_unacknowledged_invalidation(self):
        self.model_builder.set_remote_files([SystemFile("root", 10, False)])
        built_model = self.model_builder.build_model()
        self.model_builder.set_downloaded_files({"root"})
        self.model_builder.set_remote_files([SystemFile("root", 11, False)])
        live_model = Model()

        self.model_builder.adopt_applied_model(
            built_model,
            live_model,
            set(),
        )

        rebuilt_model = self.model_builder.build_model()
        self.assertIsNot(live_model, rebuilt_model)
        self.assertEqual(11, rebuilt_model.get_file("root").remote_size)

    def test_adopt_applied_model_does_not_merge_same_category_downloaded_events(self):
        self.model_builder.set_remote_files([
            SystemFile("first", 10, False),
            SystemFile("second", 10, False),
        ])
        built_model = self.model_builder.build_model()
        self.model_builder.set_downloaded_files({"first"})
        second_token = self.model_builder.set_downloaded_files({"first", "second"})
        live_model = Model()
        applied_file = built_model.get_file("second")
        applied_file.state = ModelFile.State.DOWNLOADED
        live_model.add_file(applied_file)

        self.model_builder.adopt_applied_model(
            built_model,
            live_model,
            {second_token},
        )

        rebuilt_model = self.model_builder.build_model()
        self.assertIsNot(live_model, rebuilt_model)
        self.assertEqual(ModelFile.State.DELETED, rebuilt_model.get_file("first").state)
        self.assertEqual(ModelFile.State.DELETED, rebuilt_model.get_file("second").state)

    def test_adopt_applied_model_does_not_merge_same_category_move_failure_events(self):
        self.model_builder.set_remote_files([
            SystemFile("first", 10, False),
            SystemFile("second", 10, False),
        ])
        built_model = self.model_builder.build_model()
        self.model_builder.set_move_failed_files({"first"})
        second_token = self.model_builder.set_move_failed_files({"first", "second"})
        live_model = Model()
        applied_file = built_model.get_file("second")
        applied_file.state = ModelFile.State.MOVE_FAILED
        live_model.add_file(applied_file)

        self.model_builder.adopt_applied_model(
            built_model,
            live_model,
            {second_token},
        )

        rebuilt_model = self.model_builder.build_model()
        self.assertIsNot(live_model, rebuilt_model)
        self.assertEqual(ModelFile.State.MOVE_FAILED, rebuilt_model.get_file("first").state)
        self.assertEqual(ModelFile.State.MOVE_FAILED, rebuilt_model.get_file("second").state)

    def test_invalidation_token_requires_exact_single_file_event(self):
        self.model_builder.set_remote_files([
            SystemFile("first", 10, False),
            SystemFile("second", 10, False),
        ])
        self.model_builder.build_model()
        downloaded_token = self.model_builder.set_downloaded_files({"first", "second"})
        move_failed_token = self.model_builder.set_move_failed_files({"first", "second"})

        self.assertFalse(self.model_builder.invalidation_token_matches_file(
            downloaded_token, "second",
        ))
        self.assertFalse(self.model_builder.invalidation_token_matches_file(
            move_failed_token, "second",
        ))

    def test_equal_remote_scan_preserves_shared_system_tree_and_cached_model(self):
        retained_root = SystemFile("root", 10, True)
        retained_root.add_child(SystemFile("child.bin", 10, False))
        self.model_builder.set_remote_files([retained_root])
        built_model = self.model_builder.build_model()
        live_model = Model()
        self.model_builder.adopt_applied_model(built_model, live_model)

        equal_root = SystemFile("root", 10, True)
        equal_root.add_child(SystemFile("child.bin", 10, False))
        self.model_builder.set_remote_files([equal_root])

        self.assertIs(retained_root, self.model_builder._ModelBuilder__remote_files_by_pair[None]["root"])
        self.assertIs(live_model, self.model_builder.build_model())

    def test_equal_local_scan_preserves_shared_system_tree_and_cached_model(self):
        retained_file = SystemFile("local.bin", 10, False)
        self.model_builder.set_local_files([retained_file])
        built_model = self.model_builder.build_model()
        live_model = Model()
        self.model_builder.adopt_applied_model(built_model, live_model)

        self.model_builder.set_local_files([SystemFile("local.bin", 10, False)])

        self.assertIs(retained_file, self.model_builder._ModelBuilder__local_files_by_pair[None]["local.bin"])
        self.assertIs(live_model, self.model_builder.build_model())

    def test_local_only_presence_includes_empty_local_files(self):
        local_empty = SystemFile("empty.bin", 0, False)
        self.model_builder.set_local_files([local_empty])
        model = self.model_builder.build_model()
        local_file = model.get_file("empty.bin")
        self.assertFalse(local_file.remote_present)
        self.assertTrue(local_file.local_present)
        self.assertFalse(local_file.remote_has_transferable_content)

    def test_local_only_presence_preserves_downloaded_and_extracted_history(self):
        local_file = SystemFile("archive.zip", 24, False)
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_downloaded_files({"archive.zip"})
        self.model_builder.set_downloaded_timestamps({"archive.zip": 1760000123.0})
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()
        file = model.get_file("archive.zip")
        self.assertEqual(ModelFile.State.EXTRACTED, file.state)
        self.assertEqual(1760000123.0, file.downloaded_timestamp.timestamp())
        self.assertFalse(file.remote_present)
        self.assertTrue(file.local_present)
        self.assertFalse(file.remote_has_transferable_content)

    def test_v086_fixture_markers_reconcile_remote_empty_local_matrix(self):
        fixture_path = Path(__file__).parents[2] / "fixtures" / "upgrade_v086_ff2a" / "controller.persist"
        persisted = json.loads(fixture_path.read_text(encoding="utf-8"))
        downloaded = set(persisted["downloaded"])
        extracted = set(persisted["extracted"])

        # A migrated local file remains visible and retains its historical
        # extracted state when the remote scan is empty.
        local = SystemFile("nonarchive.txt", 0, False)
        self.model_builder.set_remote_files([])
        self.model_builder.set_local_files([local])
        self.model_builder.set_downloaded_files(downloaded)
        self.model_builder.set_extracted_files(extracted)
        model = self.model_builder.build_model()
        local_file = model.get_file("nonarchive.txt")
        self.assertTrue(local_file.local_present)
        self.assertFalse(local_file.remote_present)
        self.assertFalse(local_file.remote_has_transferable_content)
        self.assertEqual(ModelFile.State.DOWNLOADED, local_file.state)

        # With no local counterpart, an empty remote snapshot contributes no
        # rows; stale marker pruning is performed by the healthy updater lane.
        self.model_builder.clear()
        self.model_builder.set_remote_files([])
        self.model_builder.set_local_files([])
        self.model_builder.set_downloaded_files(downloaded)
        self.model_builder.set_extracted_files(extracted)
        self.assertEqual(set(), self.model_builder.build_model().get_file_names())

        # A zero-byte remote file is still transferable content and remains
        # visible after migration.
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("zero-byte.bin", 0, False)])
        zero_file = self.model_builder.build_model().get_file("zero-byte.bin")
        self.assertTrue(zero_file.remote_present)
        self.assertTrue(zero_file.remote_has_transferable_content)

    def test_move_failed_overlay_is_canonical_pair_scoped_and_last(self):
        movies = SystemFile("same.mkv", 100, False)
        movies.path_pair_id = "movies"
        tv = SystemFile("same.mkv", 100, False)
        tv.path_pair_id = "tv"
        movies_id = ModelFile.build_file_id("same.mkv", "movies")
        tv_id = ModelFile.build_file_id("same.mkv", "tv")
        self.model_builder.set_local_files([movies, tv])
        self.model_builder.set_downloaded_files({movies_id, tv_id})
        self.model_builder.set_extracted_files({movies_id, tv_id})
        self.model_builder.set_move_failed_files({movies_id})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.MOVE_FAILED, model.get_file(movies_id).state)
        self.assertEqual(ModelFile.State.EXTRACTED, model.get_file(tv_id).state)

    def test_downloaded_timestamps_are_exactly_path_pair_scoped(self):
        files = []
        timestamps = {}
        for index, pair_id in enumerate(("movies", "tv", "anime"), start=1):
            file = SystemFile("same.mkv", 100, False)
            file.path_pair_id = pair_id
            files.append(file)
            timestamps[ModelFile.build_file_id("same.mkv", pair_id)] = 1760000000.0 + index * 100

        self.model_builder.set_local_files(files)
        self.model_builder.set_downloaded_files(set(timestamps))
        self.model_builder.set_downloaded_timestamps(timestamps)
        model = self.model_builder.build_model()

        for index, pair_id in enumerate(("movies", "tv", "anime"), start=1):
            file = model.get_file(ModelFile.build_file_id("same.mkv", pair_id))
            self.assertEqual(1760000000.0 + index * 100, file.downloaded_timestamp.timestamp())

    def test_unrepresentable_downloaded_timestamp_is_treated_as_unknown(self):
        local = SystemFile("huge.mkv", 100, False)
        self.model_builder.set_local_files([local])
        self.model_builder.set_downloaded_files({"huge.mkv"})
        self.model_builder.set_downloaded_timestamps({"huge.mkv": 1e20})

        file = self.model_builder.build_model().get_file("huge.mkv")

        self.assertIsNone(file.downloaded_timestamp)

    def test_bare_markers_are_deferred_for_duplicate_path_pair_names(self):
        movies = SystemFile("same.mkv", 100, False)
        movies.path_pair_id = "movies"
        tv = SystemFile("same.mkv", 100, False)
        tv.path_pair_id = "tv"
        persisted_marker = {"same.mkv"}

        # The updater must not pass an ambiguous bare name into ModelBuilder,
        # where name-based matching would otherwise apply it to both pairs.
        filtered_marker = ModelUpdater._filter_keys_for_model_builder(
            persisted_marker,
            {"movies", "tv"},
        )
        self.assertEqual(set(), filtered_marker)
        self.assertEqual({"same.mkv"}, persisted_marker)

        self.model_builder.set_remote_files([movies, tv])
        self.model_builder.set_downloaded_files(filtered_marker)
        self.model_builder.set_extracted_files(filtered_marker)
        self.model_builder.set_final_move_succeeded_files(filtered_marker)
        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DEFAULT, model.get_file(ModelFile.build_file_id("same.mkv", "movies")).state)
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file(ModelFile.build_file_id("same.mkv", "tv")).state)
        self.assertFalse(model.get_file(ModelFile.build_file_id("same.mkv", "movies")).final_move_succeeded)
        self.assertFalse(model.get_file(ModelFile.build_file_id("same.mkv", "tv")).final_move_succeeded)

    def test_raw_runtime_markers_cannot_overlay_scoped_pairs_after_rebuild(self):
        movies = SystemFile("same.mkv", 100, False)
        movies.path_pair_id = "movies"
        tv = SystemFile("same.mkv", 100, False)
        tv.path_pair_id = "tv"
        movies_id = ModelFile.build_file_id("same.mkv", "movies")
        tv_id = ModelFile.build_file_id("same.mkv", "tv")

        self.model_builder.set_remote_files([movies, tv])
        self.model_builder.set_downloaded_files(set())
        self.model_builder.set_extracted_files(set())
        self.model_builder.set_final_move_succeeded_files(set())
        self.model_builder.build_model()

        # Simulate a runtime raw setter bypassing updater-side normalization.
        raw_marker = {"same.mkv"}
        self.model_builder.set_downloaded_files(raw_marker)
        self.model_builder.set_extracted_files(raw_marker)
        self.model_builder.set_final_move_succeeded_files(raw_marker)
        self.model_builder.request_rebuild()
        rebuilt = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DEFAULT, rebuilt.get_file(movies_id).state)
        self.assertEqual(ModelFile.State.DEFAULT, rebuilt.get_file(tv_id).state)
        self.assertFalse(rebuilt.get_file(movies_id).final_move_succeeded)
        self.assertFalse(rebuilt.get_file(tv_id).final_move_succeeded)
        self.assertEqual(raw_marker, self.model_builder._ModelBuilder__downloaded_files)
        self.assertEqual(raw_marker, self.model_builder._ModelBuilder__extracted_files)
        self.assertEqual(raw_marker, self.model_builder._ModelBuilder__final_move_succeeded_files)

    def test_move_failed_overlay_survives_remote_absence_and_rebuild(self):
        local = SystemFile("movie.mkv", 100, False)
        self.model_builder.set_local_files([local])
        self.model_builder.set_move_failed_files({"movie.mkv"})

        first = self.model_builder.build_model()
        self.model_builder.request_rebuild()
        second = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.MOVE_FAILED, first.get_file("movie.mkv").state)
        self.assertEqual(ModelFile.State.MOVE_FAILED, second.get_file("movie.mkv").state)

    def __build_test_model_children_tree_1(self) -> Model:
        """Build a test model for children testing"""
        self.model_builder.clear()

        r_a = SystemFile("a", 1024, True)
        r_aa = SystemFile("aa", 512, False)
        r_a.add_child(r_aa)
        r_ab = SystemFile("ab", 512, False)
        r_a.add_child(r_ab)
        r_b = SystemFile("b", 3090, True)
        r_ba = SystemFile("ba", 2048, True)
        r_b.add_child(r_ba)
        r_baa = SystemFile("baa", 2048, False)
        r_ba.add_child(r_baa)
        r_bb = SystemFile("bb", 42, True)  # only in remote
        r_b.add_child(r_bb)
        r_bba = SystemFile("bba", 42, False)  # only in remote
        r_bb.add_child(r_bba)
        r_bd = SystemFile("bd", 1000, False)
        r_b.add_child(r_bd)
        r_c = SystemFile("c", 1234, False)  # only in remote
        r_d = SystemFile("d", 5678, True)  # only in remote
        r_da = SystemFile("da", 5678, False)  # only in remote
        r_d.add_child(r_da)

        l_a = SystemFile("a", 1024, True)
        l_aa = SystemFile("aa", 512, False)
        l_a.add_child(l_aa)
        l_ab = SystemFile("ab", 512, False)
        l_a.add_child(l_ab)
        l_b = SystemFile("b", 1611, True)
        l_ba = SystemFile("ba", 512, True)
        l_b.add_child(l_ba)
        l_baa = SystemFile("baa", 512, False)
        l_ba.add_child(l_baa)
        l_bc = SystemFile("bc", 99, True)  # only in local
        l_b.add_child(l_bc)
        l_bca = SystemFile("bca", 99, False)  # only in local
        l_bc.add_child(l_bca)
        l_bd = SystemFile("bd", 1000, False)
        l_b.add_child(l_bd)

        s_b = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "b", "")
        s_b.total_transfer_state = LftpJobStatus.TransferState(1611, 3090, 52, 10, 1000)
        s_b.add_active_file_transfer_state("ba/baa", LftpJobStatus.TransferState(512, 2048, 25, 5, 500))
        s_c = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "c", "")
        s_d = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "d", "")

        self.model_builder.set_remote_files([r_a, r_b, r_c, r_d])
        self.model_builder.set_local_files([l_a, l_b])
        self.model_builder.set_lftp_statuses([s_b, s_c, s_d])
        return self.model_builder.build_model()

    def test_build_file_names(self):
        remote_files = [SystemFile("a", 0, False), SystemFile("b", 0, False)]
        local_files = [SystemFile("b", 0, False), SystemFile("c", 0, False)]
        statuses = [LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", ""),
                    LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "d", "")]
        self.model_builder.set_remote_files(remote_files)
        self.model_builder.set_local_files(local_files)
        self.model_builder.set_lftp_statuses(statuses)
        model = self.model_builder.build_model()
        self.assertEqual({"a", "b", "c", "d"}, model.get_file_names())

    def test_build_is_dir(self):
        # remote
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        model = self.model_builder.build_model()
        self.assertEqual(False, model.get_file("a").is_dir)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, True)])
        model = self.model_builder.build_model()
        self.assertEqual(set(), model.get_file_names())

        # local
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        model = self.model_builder.build_model()
        self.assertEqual(False, model.get_file("a").is_dir)
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 0, True)])
        model = self.model_builder.build_model()
        self.assertEqual(True, model.get_file("a").is_dir)

        # statuses
        self.model_builder.clear()
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(False, model.get_file("a").is_dir)
        self.model_builder.clear()
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(True, model.get_file("a").is_dir)

        # all three
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(False, model.get_file("a").is_dir)
        self.model_builder.set_remote_files([SystemFile("a", 0, True)])
        self.model_builder.set_local_files([SystemFile("a", 0, True)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(True, model.get_file("a").is_dir)

    def test_build_mismatch_is_dir(self):
        """Mismatching is_dir raises error"""
        # remote mismatches
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, True)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir"))

        # local mismatches
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, True)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir"))

        # status mismatches
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")
        ])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir"))

        # extracting mismatches
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_extract_statuses([ExtractStatus("a", True, ExtractStatus.State.EXTRACTING)])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir between file and extract status"))

    def test_build_state(self):
        # Queued
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.QUEUED, model.get_file("a").state)

        # Downloading
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("a").state)

        # Downloading - remote only
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Default
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Default - local only
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Downloaded - stays downloaded after the remote file disappears
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 50, False)])
        self.model_builder.set_downloaded_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        self.model_builder.set_remote_files([])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("a").state)
        self.assertIsNone(model.get_file("a").remote_size)

        # Staging-only local file should not be promoted by stale completion markers
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("archive.zip", 100, False, is_staging=True)])
        self.model_builder.set_downloaded_files({"archive.zip"})
        self.model_builder.set_extracted_files({"archive.zip"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("archive.zip").state)

        # Downloaded
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("a").state)

        # Deleted
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DELETED, model.get_file("a").state)

        # Deleted but Queued
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.QUEUED, model.get_file("a").state)

        # Deleted but Downloading
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Deleted, then partially Downloaded
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 50, False)])
        self.model_builder.set_downloaded_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Downloaded, and Extracting
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.EXTRACTING, model.get_file("a").state)

        # Local-only, and Extracting
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.EXTRACTING, model.get_file("a").state)

        # Remote-only, and Extracting (unexpected: should fall-back to Default)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Extracting and Downloading/Queued (unexpected: should ignore Extracting)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 50, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Extracting and Deleted (unexpected: should ignore Extracting)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DELETED, model.get_file("a").state)

        # Downloaded+Extracted, but extracting again
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_extracted_files({"a"})
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.EXTRACTING, model.get_file("a").state)

        # Downloaded, and Extracted
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.EXTRACTED, model.get_file("a").state)

        # Local-only, and Extracted
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Remote-only, and Extracted
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Extracted, but Downloading/Queued (possible after deletion)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 50, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Extracted and Deleted
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DELETED, model.get_file("a").state)

    def test_build_state_keeps_final_root_local_only_file_completed_from_persisted_markers(self):
        self.model_builder.set_local_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_downloaded_files({"archive.zip"})
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.EXTRACTED, model.get_file("archive.zip").state)

    def test_build_state_uses_exact_path_pair_extracted_marker(self):
        local_archive = SystemFile("archive.zip", 100, False)
        local_archive.path_pair_id = "movies"

        self.model_builder.set_local_files([local_archive])
        self.model_builder.set_downloaded_files({
            ModelFile.build_file_id("archive.zip", "movies")
        })
        self.model_builder.set_extracted_files({
            ModelFile.build_file_id("archive.zip", "movies")
        })

        model = self.model_builder.build_model()

        self.assertEqual(
            ModelFile.State.EXTRACTED,
            model.get_file(ModelFile.build_file_id("archive.zip", "movies")).state
        )

    def test_build_state_does_not_promote_duplicate_root_names_from_persisted_extracted_marker(self):
        local_movies = SystemFile("archive.zip", 100, False)
        local_movies.path_pair_id = "movies"
        local_tv = SystemFile("archive.zip", 100, False)
        local_tv.path_pair_id = "tv"

        self.model_builder.set_local_files([local_movies, local_tv])
        self.model_builder.set_downloaded_files({
            ModelFile.build_file_id("archive.zip", "movies"),
            ModelFile.build_file_id("archive.zip", "tv"),
        })
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()

        self.assertEqual(
            ModelFile.State.DOWNLOADED,
            model.get_file(ModelFile.build_file_id("archive.zip", "movies")).state
        )
        self.assertEqual(
            ModelFile.State.DOWNLOADED,
            model.get_file(ModelFile.build_file_id("archive.zip", "tv")).state
        )

    def test_build_state_keeps_legacy_extracted_marker_deferred_after_root_name_becomes_unique(self):
        local_movies = SystemFile("archive.zip", 100, False)
        local_movies.path_pair_id = "movies"
        local_tv = SystemFile("archive.zip", 100, False)
        local_tv.path_pair_id = "tv"
        movies_id = ModelFile.build_file_id("archive.zip", "movies")
        tv_id = ModelFile.build_file_id("archive.zip", "tv")

        self.model_builder.set_local_files([local_movies, local_tv])
        self.model_builder.set_downloaded_files({movies_id, tv_id})
        self.model_builder.set_extracted_files({"archive.zip"})

        ambiguous_model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADED, ambiguous_model.get_file(movies_id).state)
        self.assertEqual(ModelFile.State.DOWNLOADED, ambiguous_model.get_file(tv_id).state)

        self.model_builder.set_local_files([local_movies])
        unique_model = self.model_builder.build_model()

        # Scoped files never fall back to a bare name, even after the other
        # same-named pair disappears; canonicalization must establish identity.
        self.assertEqual(ModelFile.State.DOWNLOADED, unique_model.get_file(movies_id).state)

    def test_build_state_does_not_promote_after_ambiguous_extracted_marker_is_removed(self):
        local_movies = SystemFile("archive.zip", 100, False)
        local_movies.path_pair_id = "movies"
        local_tv = SystemFile("archive.zip", 100, False)
        local_tv.path_pair_id = "tv"

        self.model_builder.set_local_files([local_movies, local_tv])
        self.model_builder.set_downloaded_files({
            ModelFile.build_file_id("archive.zip", "movies"),
            ModelFile.build_file_id("archive.zip", "tv"),
        })
        self.model_builder.set_extracted_files({"archive.zip"})

        ambiguous_model = self.model_builder.build_model()

        self.assertEqual(
            ModelFile.State.DOWNLOADED,
            ambiguous_model.get_file(ModelFile.build_file_id("archive.zip", "movies")).state
        )
        self.assertEqual(
            ModelFile.State.DOWNLOADED,
            ambiguous_model.get_file(ModelFile.build_file_id("archive.zip", "tv")).state
        )

        self.model_builder.set_local_files([local_movies])
        self.model_builder.set_downloaded_files({ModelFile.build_file_id("archive.zip", "movies")})
        self.model_builder.set_extracted_files(set())

        collapsed_model = self.model_builder.build_model()

        self.assertEqual(
            ModelFile.State.DOWNLOADED,
            collapsed_model.get_file(ModelFile.build_file_id("archive.zip", "movies")).state
        )

    def test_build_scan_only_staging_root_file_promotes_to_downloaded_when_local_size_matches_remote_size(self):
        self.model_builder.set_remote_files([SystemFile("movie.mkv", 100, False)])
        self.model_builder.set_local_files([SystemFile("movie.mkv", 100, False, is_staging=True)])

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("movie.mkv").state)

    def test_build_scan_only_staging_root_archive_file_promotes_to_downloaded_when_local_size_matches_remote_size(self):
        self.model_builder.set_remote_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_local_files([SystemFile("archive.zip", 100, False, is_staging=True)])
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("archive.zip").state)

    def test_build_scan_only_staging_root_archive_file_stays_default_when_local_size_is_short_even_with_persisted_markers(self):
        self.model_builder.set_remote_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_local_files([SystemFile("archive.zip", 99, False, is_staging=True)])
        self.model_builder.set_downloaded_files({"archive.zip"})
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("archive.zip").state)

    def test_build_state_does_not_promote_staging_only_child_file_from_remote_size_match(self):
        remote_root = SystemFile("folder", 100, True)
        remote_child = SystemFile("archive.zip", 100, False)
        remote_root.add_child(remote_child)
        local_root = SystemFile("folder", 100, True)
        local_child = SystemFile("archive.zip", 100, False, is_staging=True)
        local_root.add_child(local_child)

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        model = self.model_builder.build_model()

        built_root = model.get_file("folder")
        self.assertEqual(ModelFile.State.DEFAULT, built_root.state)
        self.assertEqual(ModelFile.State.DEFAULT, built_root.get_children()[0].state)

    def test_split_root_combines_final_and_staged_bytes_and_returns_exact_final_leaves(self):
        stamp = datetime(2026, 8, 11, 12, 0, 0)
        remote_root = SystemFile("release", 300, True)
        remote_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=1))
        remote_root.add_child(SystemFile("E07.mkv", 100, False))
        remote_nested = SystemFile("nested", 100, True)
        remote_nested.add_child(SystemFile("[E08]*.mkv", 100, False))
        remote_root.add_child(remote_nested)

        local_root = SystemFile("release", 180, True)
        local_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=1))
        local_root.add_child(SystemFile("E07.mkv", 80, False, is_staging=True))
        local_nested = SystemFile("nested", 0, True)
        local_nested.add_child(SystemFile("[E08]*.mkv", 0, False, is_staging=True))
        local_root.add_child(local_nested)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(80, 300, 27, 100, 3)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        release = model.get_file("release")

        self.assertEqual(180, release.transferred_size)
        self.assertEqual(60, release.download_progress)
        self.assertEqual(("E06.mkv",), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_split_root_rejects_same_second_raw_mtime_difference_despite_display_timezone_difference(self):
        remote_root = SystemFile("release", 200, True)
        remote_root.add_child(SystemFile(
            "E06.mkv", 100, False,
            time_modified=datetime(2026, 8, 11, 12, 0, 0), mtime_ns=1786400003444444444
        ))
        remote_root.add_child(SystemFile("E07.mkv", 100, False))
        local_root = SystemFile("release", 120, True)
        # Display timestamps can differ by timezone, but the raw scanner
        # nanoseconds remain the identity proof for a final leaf.
        local_root.add_child(SystemFile(
            "E06.mkv", 100, False,
            time_modified=datetime(2026, 8, 11, 14, 0, 0), mtime_ns=1786400003000000000
        ))
        local_root.add_child(SystemFile("E07.mkv", 20, False, is_staging=True))
        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(20, 200, 10, 100, 3)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        release = self.model_builder.build_model().get_file("release")

        self.assertEqual(20, release.transferred_size)
        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_split_root_rejects_unequal_epoch_mtime_even_with_equal_naive_display_time(self):
        displayed_time = datetime(2026, 8, 11, 12, 0, 0)
        remote_root = SystemFile("release", 200, True)
        remote_root.add_child(SystemFile(
            "E06.mkv", 100, False, time_modified=displayed_time, mtime_ns=1786400004000000000
        ))
        remote_root.add_child(SystemFile("E07.mkv", 100, False))
        local_root = SystemFile("release", 120, True)
        local_root.add_child(SystemFile(
            "E06.mkv", 100, False, time_modified=displayed_time, mtime_ns=1786400003000000000
        ))
        local_root.add_child(SystemFile("E07.mkv", 20, False, is_staging=True))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_split_root_does_not_trust_legacy_leaf_without_epoch_mtime(self):
        displayed_time = datetime(2026, 8, 11, 12, 0, 0)
        remote_root = SystemFile("release", 200, True)
        remote_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=displayed_time))
        remote_root.add_child(SystemFile("E07.mkv", 100, False))
        local_root = SystemFile("release", 120, True)
        local_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=displayed_time))
        local_root.add_child(SystemFile("E07.mkv", 20, False, is_staging=True))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_active_staging_does_not_supersede_final_for_equal_lftp_epoch_across_display_timezones(self):
        final_file = SystemFile(
            "movie.mkv", 100, False,
            time_modified=datetime(2026, 8, 11, 12, 0, 0), mtime_ns=1786400003000000000
        )
        remote_file = SystemFile(
            "movie.mkv", 100, False,
            time_modified=datetime(2026, 8, 11, 14, 0, 0), mtime_ns=1786400003444444444
        )
        active_staging = SystemFile("movie.mkv", 20, False, is_staging=True)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([final_file])
        self.model_builder.set_active_files([active_staging])

        effective_file = self.model_builder._ModelBuilder__build_effective_local_files()["movie.mkv"]

        self.assertIs(final_file, effective_file)

    def test_active_staging_supersedes_final_for_next_lftp_epoch_second(self):
        final_file = SystemFile(
            "movie.mkv", 100, False, time_modified=datetime(2026, 8, 11, 14, 0, 0), mtime_ns=1786400003000000000
        )
        remote_file = SystemFile(
            "movie.mkv", 100, False, time_modified=datetime(2026, 8, 11, 12, 0, 0), mtime_ns=1786400004000000000
        )
        active_staging = SystemFile("movie.mkv", 20, False, is_staging=True)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([final_file])
        self.model_builder.set_active_files([active_staging])

        effective_file = self.model_builder._ModelBuilder__build_effective_local_files()["movie.mkv"]

        self.assertIs(active_staging, effective_file)

    def test_active_staging_does_not_supersede_final_for_older_epoch_despite_newer_display_time(self):
        final_file = SystemFile(
            "movie.mkv", 100, False, time_modified=datetime(2026, 8, 11, 12, 0, 0), mtime_ns=1786400004000000000
        )
        remote_file = SystemFile(
            "movie.mkv", 100, False, time_modified=datetime(2026, 8, 11, 14, 0, 0), mtime_ns=1786400003000000000
        )
        active_staging = SystemFile("movie.mkv", 20, False, is_staging=True)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([final_file])
        self.model_builder.set_active_files([active_staging])

        effective_file = self.model_builder._ModelBuilder__build_effective_local_files()["movie.mkv"]

        self.assertIs(final_file, effective_file)

    def test_active_staging_does_not_supersede_final_without_epoch_provenance(self):
        final_file = SystemFile(
            "movie.mkv", 100, False, time_modified=datetime(2026, 8, 11, 12, 0, 0)
        )
        remote_file = SystemFile(
            "movie.mkv", 100, False, time_modified=datetime(2026, 8, 11, 14, 0, 0)
        )
        active_staging = SystemFile("movie.mkv", 20, False, is_staging=True)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([final_file])
        self.model_builder.set_active_files([active_staging])

        effective_file = self.model_builder._ModelBuilder__build_effective_local_files()["movie.mkv"]

        self.assertIs(final_file, effective_file)

    def test_active_staging_overlay_preserves_verified_split_root_final_leaves_and_completion(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 28, True)
        remote_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("E07.mkv", 7, False))
        remote_nested = SystemFile("nested", 11, True)
        remote_nested.add_child(SystemFile("E06.mkv", 11, False))
        remote_root.add_child(remote_nested)

        final_e06 = SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns)
        staged_e07 = SystemFile("E07.mkv", 7, False, is_staging=True)
        staged_nested = SystemFile("nested", 11, True, is_staging=True)
        staged_nested.add_child(SystemFile("E06.mkv", 11, False, is_staging=True))
        stale_staged = SystemFile("stale.mkv", 5, False, is_staging=True)
        merged_local_root = SystemFile("release", 33, True)
        merged_local_root.add_child(final_e06)
        merged_local_root.add_child(staged_e07)
        merged_local_root.add_child(staged_nested)
        merged_local_root.add_child(stale_staged)

        active_e07 = SystemFile("E07.mkv", 7, False)
        active_e07.status_sidecar_ready = True
        active_nested = SystemFile("nested", 11, True)
        active_nested.add_child(SystemFile("E06.mkv", 11, False))
        # ActiveScanner historically returns this staging-root tree without
        # location flags; ModelBuilder owns the semantic normalization.
        active_root = SystemFile("release", 18, True)
        active_root.add_child(active_e07)
        active_root.add_child(active_nested)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(18, 28, 64, 100, 1)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([merged_local_root])
        self.model_builder.set_active_files([active_root])
        self.model_builder.set_lftp_statuses([running_status])

        effective_root = self.model_builder._ModelBuilder__build_effective_local_files()["release"]
        effective_children = {child.name: child for child in effective_root.iter_children()}

        self.assertTrue(active_root.is_staging)
        self.assertTrue(active_nested.is_staging)
        self.assertEqual(28, effective_root.size)
        self.assertIs(final_e06, effective_children["E06.mkv"])
        self.assertNotIn("stale.mkv", effective_children)
        self.assertTrue(effective_children["E07.mkv"].status_sidecar_ready)

        release = self.model_builder.build_model().get_file("release")
        release_children = {child.name: child for child in release.get_children()}

        self.assertEqual(ModelFile.State.DOWNLOADED, release.state)
        self.assertEqual(28, release.local_size)
        self.assertEqual(28, release.transferred_size)
        self.assertTrue(release_children["E06.mkv"].local_present)
        self.assertEqual(10, release_children["E06.mkv"].transferred_size)
        self.assertTrue(self.model_builder.has_complete_local_coverage("release"))

        # The active tree remains complete after the LFTP status vanishes;
        # ModelUpdater's pending-completion move gate uses this exact query.
        self.model_builder.set_lftp_statuses([])
        self.model_builder.build_model()
        self.assertTrue(self.model_builder.has_complete_local_coverage("release"))

    def test_active_duplicate_of_verified_split_leaf_retains_collision_without_double_counting(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 38, True)
        remote_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("E07.mkv", 7, False))
        remote_nested = SystemFile("nested", 11, True)
        remote_nested.add_child(SystemFile("E06.mkv", 11, False))
        remote_root.add_child(remote_nested)
        remote_root.add_child(SystemFile("missing.mkv", 10, False))

        existing_root = SystemFile("release", 28, True)
        existing_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        existing_root.add_child(SystemFile("E07.mkv", 7, False, is_staging=True))
        existing_nested = SystemFile("nested", 11, True, is_staging=True)
        existing_nested.add_child(SystemFile("E06.mkv", 11, False, is_staging=True))
        existing_root.add_child(existing_nested)

        active_root = SystemFile("release", 28, True)
        active_root.add_child(SystemFile("E06.mkv", 10, False))
        active_root.add_child(SystemFile("E07.mkv", 7, False))
        active_nested = SystemFile("nested", 11, True)
        active_nested.add_child(SystemFile("E06.mkv", 11, False))
        active_root.add_child(active_nested)
        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(18, 38, 47, 100, 1)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([existing_root])
        self.model_builder.set_active_files([active_root])
        self.model_builder.set_lftp_statuses([running_status])

        effective_root = self.model_builder._ModelBuilder__build_effective_local_files()["release"]
        effective_e06 = next(child for child in effective_root.iter_children() if child.name == "E06.mkv")
        release = self.model_builder.build_model().get_file("release")

        self.assertTrue(effective_e06.has_staging_collision)
        self.assertEqual(18, release.transferred_size)
        self.assertNotEqual(ModelFile.State.DOWNLOADED, release.state)
        self.assertFalse(self.model_builder.has_complete_local_coverage("release"))

    def test_nested_verified_equivalent_final_and_staging_leaf_reconciles_without_collision(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 10, True)
        remote_nested = SystemFile("nested", 10, True)
        remote_nested.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(remote_nested)
        local_root = SystemFile("release", 10, True)
        local_nested = SystemFile("nested", 10, True)
        local_episode = SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns)
        local_episode.has_staging_collision = True
        local_nested.add_child(local_episode)
        local_root.add_child(local_nested)
        active_root = SystemFile("release", 10, True)
        active_nested = SystemFile("nested", 10, True)
        active_nested.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns))
        active_root.add_child(active_nested)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])

        effective_root = self.model_builder._ModelBuilder__build_effective_local_files()["release"]
        effective_leaf = next(child for child in next(effective_root.iter_children()).iter_children())

        self.assertFalse(effective_leaf.has_staging_collision)
        self.assertFalse(self.model_builder.has_unresolved_staging_collision("release"))
        self.assertTrue(self.model_builder.has_complete_local_coverage("release"))

    def test_nested_same_size_different_mtime_staging_leaf_remains_unresolved_collision(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 10, True)
        remote_nested = SystemFile("nested", 10, True)
        remote_nested.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(remote_nested)
        local_root = SystemFile("release", 10, True)
        local_nested = SystemFile("nested", 10, True)
        local_episode = SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns)
        local_episode.has_staging_collision = True
        local_nested.add_child(local_episode)
        local_root.add_child(local_nested)
        active_root = SystemFile("release", 10, True)
        active_nested = SystemFile("nested", 10, True)
        active_nested.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns + 1_000_000_000))
        active_root.add_child(active_nested)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])

        effective_root = self.model_builder._ModelBuilder__build_effective_local_files()["release"]
        effective_leaf = next(child for child in next(effective_root.iter_children()).iter_children())

        self.assertTrue(effective_leaf.has_staging_collision)
        self.assertTrue(self.model_builder.has_unresolved_staging_collision("release"))
        self.assertFalse(self.model_builder.has_complete_local_coverage("release"))
        built_model = self.model_builder.build_model()

        self.assertEqual({"release"}, self.model_builder.get_unresolved_staging_collision_file_ids())
        self.assertEqual({"release"}, self.model_builder.get_terminalizable_staging_collision_file_ids())
        self.assertFalse(self.model_builder.has_verified_staging_collision_remote_identity("release"))
        self.assertFalse(self.model_builder.has_changes())
        self.assertIs(built_model, self.model_builder.build_model())

    def test_terminal_collision_identity_accepts_fractional_remote_mtime_at_portable_second(self):
        remote_mtime_ns = 1786400003000000100
        remote_root = SystemFile("release", 10, True)
        remote_root.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=remote_mtime_ns))
        local_root = SystemFile("release", 10, True)
        local_leaf = SystemFile(
            "episode.mkv",
            10,
            False,
            mtime_ns=remote_mtime_ns - 100,
        )
        local_leaf.has_staging_collision = True
        local_root.add_child(local_leaf)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        self.model_builder.build_model()

        self.assertEqual({"release"}, self.model_builder.get_terminalizable_staging_collision_file_ids())
        self.assertTrue(self.model_builder.has_verified_staging_collision_remote_identity("release"))

    def test_terminal_collision_identity_checks_active_leaf_at_portable_second(self):
        remote_mtime_ns = 1786400003000000100
        remote_root = SystemFile("release", 10, True)
        remote_root.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=remote_mtime_ns))
        local_root = SystemFile("release", 10, True)
        local_leaf = SystemFile("episode.mkv", 10, False, mtime_ns=remote_mtime_ns)
        local_leaf.has_staging_collision = True
        local_root.add_child(local_leaf)

        for active_mtime_ns, expected in (
            (remote_mtime_ns - 100, True),
            (remote_mtime_ns + 1_000_000_000, False),
        ):
            with self.subTest(expected=expected):
                active_root = SystemFile("release", 10, True)
                active_root.add_child(SystemFile(
                    "episode.mkv", 10, False, mtime_ns=active_mtime_ns
                ))
                self.model_builder.set_remote_files([remote_root])
                self.model_builder.set_local_files([local_root])
                self.model_builder.set_active_files([active_root])
                self.model_builder.build_model()

                self.assertTrue(
                    self.model_builder.has_verified_staging_collision_remote_identity("release")
                    is expected
                )

    def test_same_second_different_raw_mtime_is_not_trusted_as_remote_final_leaf(self):
        remote = SystemFile("movie.mkv", 10, False, mtime_ns=1786400003000000100)
        local = SystemFile("movie.mkv", 10, False, mtime_ns=1786400003000000900)
        self.model_builder.set_remote_files([remote])
        self.model_builder.set_local_files([local])

        self.model_builder.build_model()

        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("movie.mkv"))

    def test_active_only_extra_bytes_cannot_complete_partial_split_root_directory(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 20, True)
        remote_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("E07.mkv", 10, False))
        local_root = SystemFile("release", 15, True)
        local_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        local_root.add_child(SystemFile("E07.mkv", 5, False, is_staging=True))
        active_root = SystemFile("release", 10, True)
        active_root.add_child(SystemFile("E07.mkv", 5, False))
        active_root.add_child(SystemFile("x", 5, False))
        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(5, 20, 25, 100, 1)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])
        self.model_builder.set_lftp_statuses([running_status])

        release = self.model_builder.build_model().get_file("release")

        self.assertEqual(20, release.local_size)
        self.assertEqual(15, release.transferred_size)
        self.assertEqual(ModelFile.State.DOWNLOADING, release.state)
        self.assertFalse(self.model_builder.has_complete_local_coverage("release"))

    def test_active_only_staging_extra_rejects_complete_split_root_coverage(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 20, True)
        remote_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("E07.mkv", 10, False))
        local_root = SystemFile("release", 20, True)
        local_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        local_root.add_child(SystemFile("E07.mkv", 10, False, is_staging=True))
        active_root = SystemFile("release", 15, True)
        active_root.add_child(SystemFile("E07.mkv", 10, False))
        active_root.add_child(SystemFile("x", 5, False))
        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(10, 20, 50, 100, 1)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])
        self.model_builder.set_lftp_statuses([running_status])

        release = self.model_builder.build_model().get_file("release")

        self.assertEqual(ModelFile.State.DOWNLOADING, release.state)
        self.assertFalse(self.model_builder.has_complete_local_coverage("release"))

    def test_final_only_extra_does_not_reject_complete_split_root_coverage(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 20, True)
        remote_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("E07.mkv", 10, False))
        local_root = SystemFile("release", 25, True)
        local_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        local_root.add_child(SystemFile("E07.mkv", 10, False, is_staging=True))
        local_root.add_child(SystemFile("notes.txt", 5, False))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        self.assertTrue(self.model_builder.has_complete_local_coverage("release"))

    def test_active_file_type_mismatch_preserves_remote_matching_final_directory_shape(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 15, True)
        remote_payload = SystemFile("payload", 10, True)
        remote_payload.add_child(SystemFile("complete.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(remote_payload)
        remote_root.add_child(SystemFile("progress", 5, False))
        local_root = SystemFile("release", 15, True)
        local_payload = SystemFile("payload", 10, True)
        local_payload.add_child(SystemFile("complete.mkv", 10, False, mtime_ns=mtime_ns))
        local_root.add_child(local_payload)
        local_root.add_child(SystemFile("progress", 5, False, is_staging=True))
        active_root = SystemFile("release", 15, True)
        active_root.add_child(SystemFile("payload", 10, False))
        active_root.add_child(SystemFile("progress", 5, False))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])

        effective_root = self.model_builder._ModelBuilder__build_effective_local_files()["release"]
        effective_payload = next(child for child in effective_root.iter_children() if child.name == "payload")
        release = self.model_builder.build_model().get_file("release")

        self.assertTrue(effective_payload.is_dir)
        self.assertTrue(effective_payload.has_staging_collision)
        self.assertTrue(next(child for child in release.get_children() if child.name == "payload").is_dir)

    def test_active_directory_type_mismatch_preserves_remote_matching_final_file_shape(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 15, True)
        remote_root.add_child(SystemFile("payload", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("progress", 5, False))
        local_root = SystemFile("release", 15, True)
        local_root.add_child(SystemFile("payload", 10, False, mtime_ns=mtime_ns))
        local_root.add_child(SystemFile("progress", 5, False, is_staging=True))
        active_root = SystemFile("release", 15, True)
        active_payload = SystemFile("payload", 10, True)
        active_payload.add_child(SystemFile("staged.mkv", 10, False))
        active_root.add_child(active_payload)
        active_root.add_child(SystemFile("progress", 5, False))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])

        effective_root = self.model_builder._ModelBuilder__build_effective_local_files()["release"]
        effective_payload = next(child for child in effective_root.iter_children() if child.name == "payload")
        release = self.model_builder.build_model().get_file("release")

        self.assertFalse(effective_payload.is_dir)
        self.assertTrue(effective_payload.has_staging_collision)
        self.assertFalse(next(child for child in release.get_children() if child.name == "payload").is_dir)

    def test_active_root_file_type_mismatch_preserves_split_final_directory_shape(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 15, True)
        remote_root.add_child(SystemFile("complete.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("progress", 5, False))
        local_root = SystemFile("release", 15, True)
        local_root.add_child(SystemFile("complete.mkv", 10, False, mtime_ns=mtime_ns))
        local_root.add_child(SystemFile("progress", 5, False, is_staging=True))
        active_root = SystemFile("release", 15, False)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])

        effective_root = self.model_builder._ModelBuilder__build_effective_local_files()["release"]
        release = self.model_builder.build_model().get_file("release")

        self.assertTrue(effective_root.is_dir)
        self.assertTrue(effective_root.has_staging_collision)
        self.assertTrue(release.is_dir)
        self.assertNotEqual(ModelFile.State.DOWNLOADED, release.state)
        self.assertFalse(self.model_builder.has_complete_local_coverage("release"))

    def test_active_root_directory_type_mismatch_preserves_collided_final_file_shape(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
        local_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
        local_root.has_staging_collision = True
        active_root = SystemFile("release", 10, True)
        active_root.add_child(SystemFile("staged.mkv", 10, False))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])

        effective_root = self.model_builder._ModelBuilder__build_effective_local_files()["release"]
        release = self.model_builder.build_model().get_file("release")

        self.assertFalse(effective_root.is_dir)
        self.assertTrue(effective_root.has_staging_collision)
        self.assertFalse(release.is_dir)
        self.assertNotEqual(ModelFile.State.DOWNLOADED, release.state)
        self.assertFalse(self.model_builder.has_complete_local_coverage("release"))

    def test_root_collision_blocks_all_children_downloaded_promotion(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 20, True)
        remote_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("E07.mkv", 10, False))
        local_root = SystemFile("release", 20, True)
        local_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        local_root.add_child(SystemFile("E07.mkv", 10, False))
        local_root.has_staging_collision = True
        active_root = SystemFile("release", 20, False)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_active_files([active_root])

        release = self.model_builder.build_model().get_file("release")
        children = {child.name: child for child in release.get_children()}

        self.assertEqual(ModelFile.State.DOWNLOADED, children["E06.mkv"].state)
        self.assertEqual(ModelFile.State.DOWNLOADED, children["E07.mkv"].state)
        self.assertNotEqual(ModelFile.State.DOWNLOADED, release.state)
        self.assertFalse(self.model_builder.has_complete_local_coverage("release"))

    def test_split_root_does_not_trust_stale_final_leaf_by_name_or_size_alone(self):
        remote_stamp = datetime(2026, 8, 11, 12, 0, 0)
        stale_stamp = datetime(2026, 8, 10, 12, 0, 0)
        remote_root = SystemFile("release", 200, True)
        remote_root.add_child(SystemFile(
            "E06.mkv", 100, False, time_modified=remote_stamp, mtime_ns=2000000000
        ))
        remote_root.add_child(SystemFile("E07.mkv", 100, False, time_modified=remote_stamp))
        local_root = SystemFile("release", 120, True)
        local_root.add_child(SystemFile(
            "E06.mkv", 100, False, time_modified=stale_stamp, mtime_ns=3000000000
        ))
        local_root.add_child(SystemFile("E07.mkv", 20, False, is_staging=True))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_ordinary_final_directory_does_not_generate_split_root_exclusions(self):
        stamp = datetime(2026, 8, 11, 12, 0, 0)
        remote_root = SystemFile("release", 100, True)
        remote_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=4))
        local_root = SystemFile("release", 100, True)
        local_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=4))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_split_root_does_not_trust_same_name_final_leaf_with_changed_size(self):
        stamp = datetime(2026, 8, 11, 12, 0, 0)
        remote_root = SystemFile("release", 220, True)
        remote_root.add_child(SystemFile("E06.mkv", 120, False, time_modified=stamp, mtime_ns=5))
        remote_root.add_child(SystemFile("E07.mkv", 100, False, time_modified=stamp))
        local_root = SystemFile("release", 120, True)
        local_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=5))
        local_root.add_child(SystemFile("E07.mkv", 20, False, is_staging=True))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_split_root_collision_leaf_is_not_added_to_staged_progress(self):
        stamp = datetime(2026, 8, 11, 12, 0, 0)
        remote_root = SystemFile("release", 200, True)
        remote_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=6))
        remote_root.add_child(SystemFile("E07.mkv", 100, False, time_modified=stamp))
        local_root = SystemFile("release", 170, True)
        local_e06 = SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=6)
        local_e06.has_staging_collision = True
        local_root.add_child(local_e06)
        local_root.add_child(SystemFile("E07.mkv", 70, False, is_staging=True))
        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(70, 200, 35, 100, 3)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        release = self.model_builder.build_model().get_file("release")

        self.assertEqual(70, release.transferred_size)
        self.assertEqual(35, release.download_progress)
        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_split_root_type_collision_leaf_is_not_trusted_or_added_to_progress(self):
        stamp = datetime(2026, 8, 11, 12, 0, 0)
        remote_root = SystemFile("release", 200, True)
        remote_root.add_child(SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=7))
        remote_root.add_child(SystemFile("E07.mkv", 100, False, time_modified=stamp))
        local_root = SystemFile("release", 170, True)
        final_e06 = SystemFile("E06.mkv", 100, False, time_modified=stamp, mtime_ns=7)
        # LocalScanner sets this when an incompatible staging directory named
        # E06.mkv is retained beside the final file.
        final_e06.has_staging_collision = True
        local_root.add_child(final_e06)
        local_root.add_child(SystemFile("E07.mkv", 70, False, is_staging=True))
        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(70, 200, 35, 100, 3)
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        release = self.model_builder.build_model().get_file("release")

        self.assertEqual(70, release.transferred_size)
        self.assertEqual(35, release.download_progress)
        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_split_root_final_directory_staging_file_collision_does_not_trust_descendants(self):
        local_dir = tempfile.mkdtemp(prefix="test_split_root_final_directory_collision")
        self.addCleanup(shutil.rmtree, local_dir)
        staging_dir = os.path.join(local_dir, "incomplete")
        os.mkdir(staging_dir)
        os.mkdir(os.path.join(local_dir, "release"))
        with open(os.path.join(local_dir, "release", "complete.mkv"), "w") as handle:
            handle.write("complete")
        with open(os.path.join(staging_dir, "release"), "w") as handle:
            handle.write("incomplete")

        local_root = LocalScanner(local_dir, use_temp_file=False, staging_path=staging_dir).scan()[0]
        local_complete = list(local_root.iter_children())[0]
        remote_root = SystemFile("release", local_complete.size, True)
        remote_root.add_child(SystemFile(
            "complete.mkv", local_complete.size, False,
            time_modified=local_complete.timestamp_modified, mtime_ns=local_complete.mtime_ns
        ))

        self.assertTrue(local_root.has_staging_collision)
        self.assertEqual(
            0,
            ModelBuilder._ModelBuilder__trusted_final_leaf_bytes(remote_root, local_root),
        )

    def test_split_root_nested_type_collision_does_not_trust_descendant_from_scanner_tree(self):
        local_dir = tempfile.mkdtemp(prefix="test_split_root_nested_collision")
        self.addCleanup(shutil.rmtree, local_dir)
        staging_dir = os.path.join(local_dir, "incomplete")
        os.mkdir(staging_dir)
        os.makedirs(os.path.join(local_dir, "release", "nested"))
        with open(os.path.join(local_dir, "release", "nested", "complete.mkv"), "w") as handle:
            handle.write("complete")
        os.mkdir(os.path.join(staging_dir, "release"))
        with open(os.path.join(staging_dir, "release", "nested"), "w") as handle:
            handle.write("incomplete")
        with open(os.path.join(staging_dir, "release", "partial.mkv"), "w") as handle:
            handle.write("part")

        local_root = LocalScanner(local_dir, use_temp_file=False, staging_path=staging_dir).scan()[0]
        local_children = {child.name: child for child in local_root.iter_children()}
        local_complete = list(local_children["nested"].iter_children())[0]
        local_partial = local_children["partial.mkv"]
        remote_root = SystemFile("release", local_complete.size + 100, True)
        remote_nested = SystemFile("nested", local_complete.size, True)
        remote_nested.add_child(SystemFile(
            "complete.mkv", local_complete.size, False,
            time_modified=local_complete.timestamp_modified, mtime_ns=local_complete.mtime_ns
        ))
        remote_root.add_child(remote_nested)
        remote_root.add_child(SystemFile("partial.mkv", 100, False))
        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(
            local_partial.size, remote_root.size, 50, 100, 3
        )
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        release = self.model_builder.build_model().get_file("release")

        self.assertTrue(local_children["nested"].has_staging_collision)
        self.assertEqual(local_partial.size, release.transferred_size)
        self.assertEqual((), self.model_builder.get_trusted_final_leaf_paths("release"))

    def test_build_state_dir_persist_staging_root_stays_default_with_incomplete_remote_children(self):
        remote_root = SystemFile("release", 300, True)
        remote_root.add_child(SystemFile("part1.rar", 100, False))
        remote_root.add_child(SystemFile("part2.rar", 100, False))
        remote_root.add_child(SystemFile("part3.rar", 100, False))

        local_root = SystemFile("release", 500, True, is_staging=True)
        local_root.add_child(SystemFile("part1.rar", 100, False, is_staging=True))
        local_root.add_child(SystemFile("part2.rar", 100, False, is_staging=True))
        local_root.add_child(SystemFile("movie.mkv", 300, False, is_staging=True))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_downloaded_files({"release"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("release").state)

    def test_build_state_dir_persist_staging_root_downloaded_when_remote_children_are_complete(self):
        remote_root = SystemFile("release", 200, True)
        remote_root.add_child(SystemFile("part1.rar", 100, False))
        remote_root.add_child(SystemFile("part2.rar", 100, False))

        local_root = SystemFile("release", 200, True, is_staging=True)
        local_root.add_child(SystemFile("part1.rar", 100, False, is_staging=True))
        local_root.add_child(SystemFile("part2.rar", 100, False, is_staging=True))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_downloaded_files({"release"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("release").state)

    def test_build_state_dir_recent_snapshot_staging_root_stays_downloading_with_incomplete_remote_children(self):
        remote_root = SystemFile("release", 300, True)
        remote_root.add_child(SystemFile("part1.rar", 100, False))
        remote_root.add_child(SystemFile("part2.rar", 100, False))
        remote_root.add_child(SystemFile("part3.rar", 100, False))

        local_root = SystemFile("release", 500, True, is_staging=True)
        local_root.add_child(SystemFile("part1.rar", 100, False, is_staging=True))
        local_root.add_child(SystemFile("part2.rar", 100, False, is_staging=True))
        local_root.add_child(SystemFile("movie.mkv", 300, False, is_staging=True))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["release"] = \
            _RecentLiveTransferSnapshot(
                root_file_id="release",
                size_local=290,
                percent_local=97,
                speed=1000,
                eta=1
            )

        model = self.model_builder.build_model()
        built_root = model.get_file("release")

        self.assertEqual(ModelFile.State.DOWNLOADING, built_root.state)
        self.assertEqual(290, built_root.transferred_size)
        self.assertEqual(97, built_root.download_progress)

    def test_build_state_dir_recent_snapshot_staging_root_downloaded_when_remote_children_are_complete(self):
        remote_root = SystemFile("release", 200, True)
        remote_root.add_child(SystemFile("part1.rar", 100, False))
        remote_root.add_child(SystemFile("part2.rar", 100, False))

        local_root = SystemFile("release", 200, True, is_staging=True)
        local_root.add_child(SystemFile("part1.rar", 100, False, is_staging=True))
        local_root.add_child(SystemFile("part2.rar", 100, False, is_staging=True))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["release"] = \
            _RecentLiveTransferSnapshot(
                root_file_id="release",
                size_local=190,
                percent_local=95,
                speed=1000,
                eta=1
            )

        model = self.model_builder.build_model()
        built_root = model.get_file("release")

        self.assertEqual(ModelFile.State.DOWNLOADED, built_root.state)
        self.assertEqual(200, built_root.transferred_size)
        self.assertIsNone(built_root.download_progress)
        self.assertNotIn("release", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_build_state_dir_staging_root_downloaded_when_remote_child_size_is_unknown(self):
        remote_root = SystemFile("release", 100, True)
        remote_child = SystemFile("part1.rar", 0, False)
        remote_child._SystemFile__size = None
        remote_root.add_child(remote_child)

        local_root = SystemFile("release", 100, True, is_staging=True)

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("release").state)

    @staticmethod
    def __build_deep_wide_tree(name: str, depth: int, breadth: int, leaf_size):
        """Build a balanced tree for traversal-shaped model-builder coverage."""
        if depth == 0:
            size = leaf_size(name) if callable(leaf_size) else leaf_size
            return SystemFile(name, size, False)
        children = [
            TestModelBuilder.__build_deep_wide_tree(f"{name}.{i}", depth - 1, breadth, leaf_size)
            for i in range(breadth)
        ]
        sized = SystemFile(name, sum(c.size for c in children), True)
        for child in children:
            sized.add_child(child)
        return sized

    def test_build_state_deep_wide_tree_downloaded(self):
        remote = TestModelBuilder.__build_deep_wide_tree("a", depth=4, breadth=4, leaf_size=10)
        local = TestModelBuilder.__build_deep_wide_tree("a", depth=4, breadth=4, leaf_size=10)

        self.model_builder.set_remote_files([remote])
        self.model_builder.set_local_files([local])

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("a").state)

    def test_build_state_deep_wide_tree_incomplete_leaf(self):
        remote = TestModelBuilder.__build_deep_wide_tree("a", depth=4, breadth=4, leaf_size=10)
        incomplete_leaf = "a.3.3.3.3"

        def local_leaf_size(leaf_name: str) -> int:
            return 1 if leaf_name == incomplete_leaf else 10

        local = TestModelBuilder.__build_deep_wide_tree(
            "a",
            depth=4,
            breadth=4,
            leaf_size=local_leaf_size,
        )

        self.model_builder.set_remote_files([remote])
        self.model_builder.set_local_files([local])

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

    def test_build_state_keeps_final_root_remote_size_match_completed(self):
        self.model_builder.set_remote_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_local_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.EXTRACTED, model.get_file("archive.zip").state)

    def test_build_remote_size(self):
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").remote_size)

        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").remote_size)

        self.model_builder.clear()
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").remote_size)

    def test_build_remote_size_from_status_is_ignored(self):
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, 12345, None, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").remote_size)

    def test_build_local_size(self):
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").local_size)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").local_size)

        self.model_builder.clear()
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").local_size)

    def test_build_local_size_from_status_is_ignored(self):
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(12345, 1000, 0.25, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").local_size)

    def test_build_local_size_downloading(self):
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        self.model_builder.set_active_files([SystemFile("a", 99, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(12345, 1000, 0.25, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(99, model.get_file("a").local_size)

    def test_build_download_progress(self):
        remote_root = SystemFile("a", 100, True)
        remote_child = SystemFile("aa", 100, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("a", 24, True)
        local_child = SystemFile("aa", 24, False)
        local_root.add_child(local_child)

        s = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(24, 100, 60, 1000, 5)
        s.add_active_file_transfer_state("aa", LftpJobStatus.TransferState(24, 100, 60, 500, 3))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        m_a_ch = {m.name: m for m in m_a.get_children()}

        self.assertEqual(24, m_a.local_size)
        self.assertEqual(100, m_a.remote_size)
        self.assertEqual(60, m_a.download_progress)
        self.assertEqual(60, m_a_ch["aa"].download_progress)

    def test_build_download_progress_fractional_percent_local(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 25, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(25, 100, 0.25, 1000, 5)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(25, model.get_file("a").download_progress)

    def test_build_download_progress_percent_local_one_is_literal_percent(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 1, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(1, 100, 1.0, 1000, 5)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(1, model.get_file("a").download_progress)

    def test_build_recent_live_transfer_snapshot_handoff(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 950, False)])
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(975, 1000, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(975, file_a.transferred_size)
        self.assertEqual(97, file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(975, file_a.transferred_size)
        self.assertEqual(97, file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_local_files([SystemFile("a", 975, False)])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(975, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, file_a.state)
        self.assertEqual(1000, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_rekeys_legacy_alias_to_canonical_file_id(self):
        self.model_builder.clear()
        qualified_file_id = ModelFile.build_file_id("dup", "movies")
        remote_file = SystemFile("dup", 1000, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile("dup", 900, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["dup"] = _RecentLiveTransferSnapshot(
            root_file_id="dup",
            size_local=950,
            percent_local=95,
            speed=1000,
            eta=5
        )

        model = self.model_builder.build_model()
        built_file = model.get_file(qualified_file_id)

        self.assertEqual(ModelFile.State.DOWNLOADING, built_file.state)
        self.assertEqual(950, built_file.transferred_size)
        self.assertEqual(95, built_file.download_progress)
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)
        self.assertNotIn("dup", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_build_recent_live_transfer_child_snapshot_handoff_uses_real_file_id(self):
        remote_root = SystemFile("a", 1000, True)
        remote_child = SystemFile("aa", 1000, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("a", 650, True)
        local_child = SystemFile("aa", 650, False)
        local_root.add_child(local_child)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        built_child = model.get_file("a").get_children()[0]
        self.assertEqual(ModelFile.build_file_id(os.path.join("a", "aa"), None), built_child.file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, built_child.state)
        self.assertEqual(750, built_child.transferred_size)
        self.assertEqual(75, built_child.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        built_child = model.get_file("a").get_children()[0]
        self.assertEqual(ModelFile.build_file_id(os.path.join("a", "aa"), None), built_child.file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, built_child.state)
        self.assertEqual(750, built_child.transferred_size)
        self.assertEqual(75, built_child.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        caught_up_local_root = SystemFile("a", 750, True)
        caught_up_local_child = SystemFile("aa", 750, False)
        caught_up_local_root.add_child(caught_up_local_child)
        self.model_builder.set_local_files([caught_up_local_root])
        model = self.model_builder.build_model()
        built_child = model.get_file("a").get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, built_child.state)
        self.assertEqual(750, built_child.transferred_size)
        self.assertIsNone(built_child.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_without_size_local_is_not_retained(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])

        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(None, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_is_evicted_when_file_disappears(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 700, False)])

        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        self.model_builder.set_lftp_statuses([])
        self.model_builder.set_remote_files([])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_is_not_applied_to_queued_state(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])

        running_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        self.assertEqual(750, model.get_file("a").transferred_size)

        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_name_entry_preserves_retained_transfer_metrics_without_active_state(self):
        self.model_builder.clear()
        remote_file = SystemFile("dup", 1000, False)
        remote_file.path_pair_id = "movies"
        remote_file.path_pair_name = "Movies"
        local_file = SystemFile("dup", 650, False)
        local_file.path_pair_id = "movies"
        local_file.path_pair_name = "Movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_stopped_files({"dup"})

        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "dup", "")
        status.path_pair_id = "movies"
        status.path_pair_name = "Movies"
        status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(ModelFile.build_file_id("dup", "movies"))
        self.assertEqual(ModelFile.State.DEFAULT, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)
        self.assertIsNone(file_dup.downloading_speed)
        self.assertIsNone(file_dup.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_status_only_stopped_file_name_entry_preserves_retained_transfer_metrics(self):
        self.model_builder.clear()
        self.model_builder.set_stopped_files({"dup"})

        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "dup", "")
        status.path_pair_id = "movies"
        status.path_pair_name = "Movies"
        status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(ModelFile.build_file_id("dup", "movies"))
        self.assertEqual(ModelFile.State.DEFAULT, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)
        self.assertIsNone(file_dup.downloading_speed)
        self.assertIsNone(file_dup.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_id_entry_preserves_retained_snapshot_with_legacy_status_root_id(self):
        self.model_builder.clear()
        remote_file = SystemFile("dup", 1000, False)
        remote_file.path_pair_id = "movies"
        remote_file.path_pair_name = "Movies"
        local_file = SystemFile("dup", 650, False)
        local_file.path_pair_id = "movies"
        local_file.path_pair_name = "Movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        file_id = ModelFile.build_file_id("dup", "movies")
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots[file_id] = _RecentLiveTransferSnapshot(
            root_file_id="dup",
            size_local=750,
            percent_local=75,
            speed=1000,
            eta=5
        )
        self.model_builder.set_stopped_files({ModelFile.build_file_id("dup", "movies")})

        model = self.model_builder.build_model()
        file_dup = model.get_file(file_id)
        self.assertEqual(ModelFile.State.DEFAULT, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)
        self.assertIsNone(file_dup.downloading_speed)
        self.assertIsNone(file_dup.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_id_entry_does_not_suppress_other_path_pair_snapshot(self):
        self.model_builder.clear()
        remote_file = SystemFile("dup", 1000, False)
        remote_file.path_pair_id = "tv"
        remote_file.path_pair_name = "TV"
        local_file = SystemFile("dup", 650, False)
        local_file.path_pair_id = "tv"
        local_file.path_pair_name = "TV"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        file_id = ModelFile.build_file_id("dup", "tv")
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots[file_id] = _RecentLiveTransferSnapshot(
            root_file_id="dup",
            size_local=750,
            percent_local=75,
            speed=1000,
            eta=5
        )
        self.model_builder.set_stopped_files({ModelFile.build_file_id("dup", "movies")})

        model = self.model_builder.build_model()
        file_dup = model.get_file(file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)
        self.assertEqual(1000, file_dup.downloading_speed)
        self.assertEqual(5, file_dup.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_staging_file_preserves_retained_snapshot_when_local_size_looks_complete(self):
        self.model_builder.clear()
        remote_file = SystemFile("a", 1000, False)
        local_file = SystemFile("a", 1000, False, is_staging=True)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=1000,
            eta=5
        )
        self.model_builder.set_stopped_files({"a"})

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertEqual(65, file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_staging_file_does_not_use_local_size_as_transferred_size_fallback(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False, is_staging=True)])

        model = self.model_builder.build_model()

        self.assertIsNone(model.get_file("a").transferred_size)

    def test_build_recent_live_snapshot_promotes_full_size_staging_copy_after_live_status_disappears(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("archive.zip", 1000, False)])
        self.model_builder.set_local_files([SystemFile("archive.zip", 1000, False, is_staging=True)])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["archive.zip"] = \
            _RecentLiveTransferSnapshot(
                root_file_id="archive.zip",
                size_local=990,
                percent_local=99,
                speed=1000,
                eta=1
            )

        model = self.model_builder.build_model()
        file_archive = model.get_file("archive.zip")
        self.assertEqual(ModelFile.State.DOWNLOADED, file_archive.state)
        self.assertEqual(1000, file_archive.transferred_size)
        self.assertIsNone(file_archive.download_progress)
        self.assertIsNone(file_archive.downloading_speed)
        self.assertIsNone(file_archive.eta)
        self.assertNotIn("archive.zip", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_build_running_staging_file_does_not_promote_to_downloaded_while_live_progress_is_active(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("archive.zip", 1000, False)])
        self.model_builder.set_local_files([SystemFile("archive.zip", 1000, False, is_staging=True)])
        self.model_builder.set_active_files([SystemFile("archive.zip", 975, False)])
        self.model_builder.set_extracted_files({"archive.zip"})

        running_status = LftpJobStatus(
            0,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            "archive.zip",
            ""
        )
        running_status.total_transfer_state = LftpJobStatus.TransferState(975, 1000, 97, 1000, 0)
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        file_archive = model.get_file("archive.zip")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_archive.state)
        self.assertEqual(975, file_archive.transferred_size)
        self.assertEqual(97, file_archive.download_progress)
        self.assertEqual(1000, file_archive.local_size)

    def test_build_staging_child_without_transfer_bytes_does_not_break_parent_rollup(self):
        self.model_builder.clear()
        remote_root = SystemFile("root", 1000, True)
        remote_child = SystemFile("child", 1000, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("root", 1000, True)
        local_child = SystemFile("child", 1000, False, is_staging=True)
        local_root.add_child(local_child)

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        model = self.model_builder.build_model()
        built_root = model.get_file("root")
        built_child = built_root.get_children()[0]

        self.assertEqual(0, built_root.transferred_size)
        self.assertIsNone(built_child.transferred_size)

    def test_build_stopped_final_root_file_promotes_to_downloaded_when_authoritative_local_is_complete(self):
        self.model_builder.clear()
        remote_file = SystemFile("a", 1000, False)
        local_file = SystemFile("a", 1000, False)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=1000,
            eta=5
        )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )
        self.model_builder.set_stopped_files({"a"})

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, file_a.state)
        self.assertEqual(1000, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertNotIn("a", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)
        self.assertNotIn("a", self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_running_file_promotes_to_downloaded_when_active_scan_only_has_staging_copy(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        self.model_builder.set_active_files([SystemFile("a", 975, False, is_staging=True)])

        running_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(975, 1000, 99, 1000, 0)
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, file_a.state)
        self.assertEqual(1000, file_a.local_size)
        self.assertEqual(1000, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_running_file_prefers_staging_copy_when_remote_timestamp_indicates_newer_content(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([
            SystemFile(
                "a", 1000, False, time_modified=datetime(2026, 3, 26, 12, 0, 0), mtime_ns=2000000000
            )
        ])
        self.model_builder.set_local_files([
            SystemFile(
                "a", 1000, False, time_modified=datetime(2026, 3, 25, 12, 0, 0), mtime_ns=1000000000
            )
        ])
        self.model_builder.set_active_files([SystemFile("a", 100, False, is_staging=True)])

        running_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(100, 1000, 10, 1000, 1)
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(100, file_a.local_size)
        self.assertEqual(100, file_a.transferred_size)
        self.assertEqual(10, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(1, file_a.eta)

    def test_build_stopped_file_prefers_retained_snapshot_over_larger_local_scan_size(self):
        self.model_builder.clear()
        remote_file = SystemFile("verifier-stop-regression-1g.bin", 1073741824, False)
        local_file = SystemFile("verifier-stop-regression-1g.bin", 1067800592, False)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["verifier-stop-regression-1g.bin"] = \
            _RecentLiveTransferSnapshot(
                root_file_id="verifier-stop-regression-1g.bin",
                size_local=1044601281,
                percent_local=97,
                speed=None,
                eta=None
            )
        self.model_builder.set_stopped_files({"verifier-stop-regression-1g.bin"})

        model = self.model_builder.build_model()
        file_bin = model.get_file("verifier-stop-regression-1g.bin")
        self.assertEqual(ModelFile.State.DEFAULT, file_bin.state)
        self.assertEqual(1067800592, file_bin.local_size)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_keeps_retained_snapshot_when_local_scan_grows_without_live_status(self):
        self.model_builder.clear()
        file_name = "verifier-stop-regression-1g.bin"
        self.model_builder.set_remote_files([SystemFile(file_name, 1073741824, False)])
        self.model_builder.set_local_files([SystemFile(file_name, 1044601281, False)])
        self.model_builder.set_stopped_files({file_name})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(1044601281, 1073741824, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])

        stopped_model = self.model_builder.build_model()
        stopped_file = stopped_model.get_file(file_name)
        self.assertEqual(ModelFile.State.DEFAULT, stopped_file.state)
        self.assertEqual(1044601281, stopped_file.transferred_size)
        self.assertEqual(97, stopped_file.download_progress)

        self.model_builder.set_lftp_statuses([])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots.pop(file_name, None)
        self.model_builder.set_local_files([SystemFile(file_name, 1067800592, False)])

        rebuilt_model = self.model_builder.build_model()
        rebuilt_file = rebuilt_model.get_file(file_name)
        self.assertEqual(ModelFile.State.DEFAULT, rebuilt_file.state)
        self.assertEqual(1067800592, rebuilt_file.local_size)
        self.assertEqual(1044601281, rebuilt_file.transferred_size)
        self.assertEqual(97, rebuilt_file.download_progress)
        self.assertIsNone(rebuilt_file.downloading_speed)
        self.assertIsNone(rebuilt_file.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_keeps_retained_snapshot_when_only_queued_status_remains(self):
        self.model_builder.clear()
        file_name = "verifier-stop-regression-1g.bin"
        self.model_builder.set_remote_files([SystemFile(file_name, 1073741824, False)])
        self.model_builder.set_local_files([SystemFile(file_name, 1067800592, False)])
        self.model_builder.set_stopped_files({file_name})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(1044601281, 1073741824, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder._ModelBuilder__recent_live_transfer_snapshots.pop(file_name, None)
        self.model_builder.set_local_files([SystemFile(file_name, 1067800592, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, "")
        ])

        rebuilt_model = self.model_builder.build_model()
        rebuilt_file = rebuilt_model.get_file(file_name)
        self.assertEqual(ModelFile.State.DEFAULT, rebuilt_file.state)
        self.assertEqual(1067800592, rebuilt_file.local_size)
        self.assertEqual(1044601281, rebuilt_file.transferred_size)
        self.assertEqual(97, rebuilt_file.download_progress)
        self.assertIsNone(rebuilt_file.downloading_speed)
        self.assertIsNone(rebuilt_file.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_promotes_recent_live_floor_after_stop_status_disappears(self):
        self.model_builder.clear()
        file_name = "same.mkv"
        path_pair_id = "movies"
        qualified_file_id = ModelFile.build_file_id(file_name, path_pair_id)
        remote_file = SystemFile(file_name, 1000, False)
        remote_file.path_pair_id = path_pair_id
        local_file = SystemFile(file_name, 650, False)
        local_file.path_pair_id = path_pair_id
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])

        running_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        running_status.path_pair_id = path_pair_id
        running_status.total_transfer_state = LftpJobStatus.TransferState(770, 1000, 77, 2000, 4)
        self.model_builder.set_lftp_statuses([running_status])

        running_model = self.model_builder.build_model()
        running_file = running_model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, running_file.state)
        self.assertEqual(770, running_file.transferred_size)
        self.assertEqual(77, running_file.download_progress)

        # A legacy status/root alias must be canonicalized when the stopped
        # row promotes its recent-live snapshot.
        recent_snapshot = self.model_builder._ModelBuilder__recent_live_transfer_snapshots.pop(
            qualified_file_id
        )
        recent_snapshot.root_file_id = file_name
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots[file_name] = recent_snapshot

        # The real stop path clears LFTP status after setting the stop marker.
        self.model_builder.set_stopped_files({qualified_file_id})
        self.model_builder.set_lftp_statuses([])

        stopped_model = self.model_builder.build_model()
        stopped_file = stopped_model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DEFAULT, stopped_file.state)
        self.assertEqual(770, stopped_file.transferred_size)
        self.assertEqual(77, stopped_file.download_progress)
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertNotIn(file_name, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)
        self.assertNotIn(file_name, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, "")
        queued_status.path_pair_id = path_pair_id
        self.model_builder.set_lftp_statuses([queued_status])

        queued_model = self.model_builder.build_model()
        queued_file = queued_model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.QUEUED, queued_file.state)
        self.assertEqual(770, queued_file.transferred_size)
        self.assertEqual(77, queued_file.download_progress)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

        lower_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        lower_resume_status.path_pair_id = path_pair_id
        lower_resume_status.total_transfer_state = LftpJobStatus.TransferState(730, 1000, 73, 1800, 5)
        self.model_builder.set_lftp_statuses([lower_resume_status])

        resumed_model = self.model_builder.build_model()
        resumed_file = resumed_model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, resumed_file.state)
        self.assertEqual(770, resumed_file.transferred_size)
        self.assertEqual(77, resumed_file.download_progress)
        self.assertEqual(1800, resumed_file.downloading_speed)
        self.assertEqual(5, resumed_file.eta)

        caught_up_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_status.path_pair_id = path_pair_id
        caught_up_status.total_transfer_state = LftpJobStatus.TransferState(780, 1000, 78, 1800, 5)
        self.model_builder.set_lftp_statuses([caught_up_status])

        caught_up_model = self.model_builder.build_model()
        caught_up_file = caught_up_model.get_file(qualified_file_id)
        self.assertEqual(780, caught_up_file.transferred_size)
        self.assertEqual(78, caught_up_file.download_progress)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_stopped_promotion_isolated_for_duplicate_path_pairs(self):
        self.model_builder.clear()
        file_name = "same.mkv"
        movies_file_id = ModelFile.build_file_id(file_name, "movies")
        tv_file_id = ModelFile.build_file_id(file_name, "tv")
        remote_files = []
        local_files = []
        running_statuses = []
        for path_pair_id, local_size, transferred_size, percent in (
            ("movies", 650, 770, 77),
            ("tv", 750, 880, 88),
        ):
            remote_file = SystemFile(file_name, 1000, False)
            remote_file.path_pair_id = path_pair_id
            local_file = SystemFile(file_name, local_size, False)
            local_file.path_pair_id = path_pair_id
            remote_files.append(remote_file)
            local_files.append(local_file)
            running_status = LftpJobStatus(
                0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, ""
            )
            running_status.path_pair_id = path_pair_id
            running_status.total_transfer_state = LftpJobStatus.TransferState(
                transferred_size, 1000, percent, 1800, 5
            )
            running_statuses.append(running_status)
        self.model_builder.set_remote_files(remote_files)
        self.model_builder.set_local_files(local_files)
        self.model_builder.set_lftp_statuses(running_statuses)
        self.model_builder.build_model()

        self.model_builder.set_stopped_files({movies_file_id})
        self.model_builder.set_lftp_statuses([])

        stopped_model = self.model_builder.build_model()
        stopped_movies = stopped_model.get_file(movies_file_id)
        stopped_tv = stopped_model.get_file(tv_file_id)
        self.assertEqual(ModelFile.State.DEFAULT, stopped_movies.state)
        self.assertEqual(770, stopped_movies.transferred_size)
        self.assertEqual(77, stopped_movies.download_progress)
        self.assertEqual(ModelFile.State.DOWNLOADING, stopped_tv.state)
        self.assertEqual(880, stopped_tv.transferred_size)
        self.assertEqual(88, stopped_tv.download_progress)
        self.assertEqual(
            {movies_file_id},
            set(self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        )
        self.assertNotIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

        self.model_builder.set_stopped_files(set())
        queued_movies = LftpJobStatus(
            0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, ""
        )
        queued_movies.path_pair_id = "movies"
        self.model_builder.set_lftp_statuses([queued_movies])
        queued_model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.QUEUED, queued_model.get_file(movies_file_id).state)
        self.assertEqual(770, queued_model.get_file(movies_file_id).transferred_size)
        self.assertEqual(77, queued_model.get_file(movies_file_id).download_progress)
        self.assertIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertNotIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

        lower_movies = LftpJobStatus(
            0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, ""
        )
        lower_movies.path_pair_id = "movies"
        lower_movies.total_transfer_state = LftpJobStatus.TransferState(730, 1000, 73, 1700, 6)
        self.model_builder.set_lftp_statuses([lower_movies])
        resumed_model = self.model_builder.build_model()
        resumed_movies = resumed_model.get_file(movies_file_id)
        self.assertEqual(770, resumed_movies.transferred_size)
        self.assertEqual(77, resumed_movies.download_progress)
        self.assertIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertNotIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

        caught_up_movies = LftpJobStatus(
            0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, ""
        )
        caught_up_movies.path_pair_id = "movies"
        caught_up_movies.total_transfer_state = LftpJobStatus.TransferState(780, 1000, 78, 1700, 6)
        self.model_builder.set_lftp_statuses([caught_up_movies])
        caught_up_model = self.model_builder.build_model()
        self.assertEqual(780, caught_up_model.get_file(movies_file_id).transferred_size)
        self.assertEqual(78, caught_up_model.get_file(movies_file_id).download_progress)
        self.assertNotIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertNotIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_allows_zero_reset_after_promoted_stop_floor(self):
        self.model_builder.clear()
        file_name = "reset-after-stop.bin"
        path_pair_id = "movies"
        qualified_file_id = self.__set_transfer_sources(file_name, path_pair_id)

        running_status = LftpJobStatus(
            0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, ""
        )
        running_status.path_pair_id = path_pair_id
        running_status.total_transfer_state = LftpJobStatus.TransferState(770, 1000, 77, 1800, 5)
        self.model_builder.set_lftp_statuses([running_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files({qualified_file_id})
        self.model_builder.set_lftp_statuses([])
        stopped_model = self.model_builder.build_model()
        self.assertEqual(770, stopped_model.get_file(qualified_file_id).transferred_size)
        self.assertEqual(77, stopped_model.get_file(qualified_file_id).download_progress)
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(
            0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, ""
        )
        queued_status.path_pair_id = path_pair_id
        self.model_builder.set_lftp_statuses([queued_status])
        queued_model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.QUEUED, queued_model.get_file(qualified_file_id).state)
        self.assertEqual(770, queued_model.get_file(qualified_file_id).transferred_size)
        self.assertEqual(77, queued_model.get_file(qualified_file_id).download_progress)

        zero_local_file = SystemFile(file_name, 0, False)
        zero_local_file.path_pair_id = path_pair_id
        self.model_builder.set_local_files([zero_local_file])
        reset_status = LftpJobStatus(
            0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, ""
        )
        reset_status.path_pair_id = path_pair_id
        reset_status.total_transfer_state = LftpJobStatus.TransferState(0, 1000, 0, 1800, 5)
        self.model_builder.set_lftp_statuses([reset_status])

        reset_model = self.model_builder.build_model()
        reset_file = reset_model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, reset_file.state)
        self.assertEqual(0, reset_file.transferred_size)
        self.assertEqual(0, reset_file.download_progress)
        self.assertEqual(1800, reset_file.downloading_speed)
        self.assertEqual(5, reset_file.eta)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_keeps_retained_stopped_snapshot_until_progress_catches_up(self):
        self.model_builder.clear()
        file_name = "verifier-stop-regression-1g.bin"
        self.model_builder.set_remote_files([SystemFile(file_name, 1073741824, False)])
        self.model_builder.set_local_files([SystemFile(file_name, 1067800592, False)])
        self.model_builder.set_stopped_files({file_name})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(1044601281, 1073741824, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DEFAULT, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.QUEUED, file_bin.state)
        self.assertEqual(1067800592, file_bin.transferred_size)
        self.assertIsNone(file_bin.download_progress)

        regressing_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        regressing_resume_status.total_transfer_state = LftpJobStatus.TransferState(1033895936, 1073741824, 96, 1000, 5)
        self.model_builder.set_lftp_statuses([regressing_resume_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)
        self.assertEqual(1000, file_bin.downloading_speed)
        self.assertEqual(5, file_bin.eta)

        caught_up_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_resume_status.total_transfer_state = LftpJobStatus.TransferState(1058013184, 1073741824, 99, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_resume_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_bin.state)
        self.assertEqual(1058013184, file_bin.transferred_size)
        self.assertEqual(99, file_bin.download_progress)

    def test_build_resumed_running_state_keeps_retained_stopped_snapshot_through_missing_remote_window(self):
        self.model_builder.clear()
        file_name = "verifier-stop-regression-1g.bin"
        self.model_builder.set_remote_files([SystemFile(file_name, 1073741824, False)])
        self.model_builder.set_local_files([SystemFile(file_name, 1044601281, False)])
        self.model_builder.set_stopped_files({file_name})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(1044601281, 1073741824, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DEFAULT, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)

        self.model_builder.set_remote_files([])
        self.model_builder.set_lftp_statuses([])

        missing_remote_model = self.model_builder.build_model()
        missing_remote_file = missing_remote_model.get_file(file_name)
        self.assertEqual(ModelFile.State.DEFAULT, missing_remote_file.state)
        self.assertEqual(1044601281, missing_remote_file.local_size)
        self.assertEqual(1044601281, missing_remote_file.transferred_size)
        self.assertEqual(97, missing_remote_file.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_remote_files([SystemFile(file_name, 1073741824, False)])
        self.model_builder.set_stopped_files(set())

        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, "")
        self.model_builder.set_lftp_statuses([queued_status])

        queued_model = self.model_builder.build_model()
        queued_file = queued_model.get_file(file_name)
        self.assertEqual(ModelFile.State.QUEUED, queued_file.state)
        self.assertEqual(1044601281, queued_file.local_size)
        self.assertEqual(1044601281, queued_file.transferred_size)
        self.assertEqual(97, queued_file.download_progress)

        regressing_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        regressing_resume_status.total_transfer_state = LftpJobStatus.TransferState(1033895936, 1073741824, 96, 1000, 5)
        self.model_builder.set_lftp_statuses([regressing_resume_status])

        resumed_model = self.model_builder.build_model()
        resumed_file = resumed_model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, resumed_file.state)
        self.assertEqual(1044601281, resumed_file.transferred_size)
        self.assertEqual(97, resumed_file.download_progress)
        self.assertEqual(1000, resumed_file.downloading_speed)
        self.assertEqual(5, resumed_file.eta)

        caught_up_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_resume_status.total_transfer_state = LftpJobStatus.TransferState(1058013184, 1073741824, 99, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_resume_status])

        caught_up_model = self.model_builder.build_model()
        caught_up_file = caught_up_model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, caught_up_file.state)
        self.assertEqual(1058013184, caught_up_file.transferred_size)
        self.assertEqual(99, caught_up_file.download_progress)
        self.assertNotIn(file_name, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_keeps_retained_floor_when_resume_uses_equivalent_root_alias(self):
        self.model_builder.clear()
        file_name = "verifier-stop-alias-regression.bin"
        remote_file = SystemFile(file_name, 1073741824, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile(file_name, 1067800592, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        qualified_file_id = ModelFile.build_file_id(file_name, "movies")
        self.model_builder.set_stopped_files({qualified_file_id})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        stopped_status.path_pair_id = "movies"
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(1044601281, 1073741824, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DEFAULT, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[qualified_file_id].root_file_id = file_name

        self.model_builder.set_stopped_files(set())
        self.model_builder.set_remote_files([])
        self.model_builder.set_local_files([])

        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.QUEUED, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)

        resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        resume_status.total_transfer_state = LftpJobStatus.TransferState(1033895936, 1073741824, 96, 1000, 5)
        self.model_builder.set_lftp_statuses([resume_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)
        self.assertEqual(1000, file_bin.downloading_speed)
        self.assertEqual(5, file_bin.eta)

        caught_up_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_status.total_transfer_state = LftpJobStatus.TransferState(1058013184, 1073741824, 99, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(1058013184, file_bin.transferred_size)
        self.assertEqual(99, file_bin.download_progress)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_alias_reuse_skips_ambiguous_duplicate_path_pair_candidates(self):
        self.model_builder.clear()
        file_name = "dup"
        movies_file_id = ModelFile.build_file_id(file_name, "movies")
        tv_file_id = ModelFile.build_file_id(file_name, "tv")
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[movies_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=750,
                percent_local=75,
                speed=None,
                eta=None
            )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[tv_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=850,
                percent_local=85,
                speed=None,
                eta=None
            )

        resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        resume_status.total_transfer_state = LftpJobStatus.TransferState(600, 1000, 60, 1000, 5)
        self.model_builder.set_lftp_statuses([resume_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_dup.state)
        self.assertEqual(600, file_dup.transferred_size)
        self.assertEqual(60, file_dup.download_progress)
        self.assertIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_prefers_current_file_id_over_alias_when_both_exist(self):
        self.model_builder.clear()
        file_name = "dup"
        qualified_file_id = ModelFile.build_file_id(file_name, "movies")
        remote_file = SystemFile(file_name, 1000, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile(file_name, 650, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[file_name] = _RecentLiveTransferSnapshot(
            root_file_id=file_name,
            size_local=900,
            percent_local=90,
            speed=None,
            eta=None
        )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[qualified_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=750,
                percent_local=75,
                speed=None,
                eta=None
            )

        resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        resume_status.path_pair_id = "movies"
        resume_status.total_transfer_state = LftpJobStatus.TransferState(700, 1000, 70, 1000, 5)
        self.model_builder.set_lftp_statuses([resume_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)

        caught_up_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_status.path_pair_id = "movies"
        caught_up_status.total_transfer_state = LftpJobStatus.TransferState(760, 1000, 76, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(qualified_file_id)
        self.assertEqual(760, file_dup.transferred_size)
        self.assertEqual(76, file_dup.download_progress)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(file_name, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_catch_up_does_not_evict_other_duplicate_path_pair_retained_snapshot(self):
        self.model_builder.clear()
        file_name = "dup"
        movies_file_id = ModelFile.build_file_id(file_name, "movies")
        tv_file_id = ModelFile.build_file_id(file_name, "tv")
        remote_file = SystemFile(file_name, 1000, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile(file_name, 650, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[movies_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=750,
                percent_local=75,
                speed=None,
                eta=None
            )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[tv_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=850,
                percent_local=85,
                speed=None,
                eta=None
            )

        caught_up_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_status.path_pair_id = "movies"
        caught_up_status.total_transfer_state = LftpJobStatus.TransferState(760, 1000, 76, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(movies_file_id)
        self.assertEqual(760, file_dup.transferred_size)
        self.assertEqual(76, file_dup.download_progress)
        self.assertNotIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_reset_does_not_evict_other_duplicate_path_pair_retained_snapshot(self):
        self.model_builder.clear()
        file_name = "dup"
        movies_file_id = ModelFile.build_file_id(file_name, "movies")
        tv_file_id = ModelFile.build_file_id(file_name, "tv")
        remote_file = SystemFile(file_name, 1000, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile(file_name, 0, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[movies_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=750,
                percent_local=75,
                speed=None,
                eta=None
            )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[tv_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=850,
                percent_local=85,
                speed=None,
                eta=None
            )

        reset_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        reset_status.path_pair_id = "movies"
        reset_status.total_transfer_state = LftpJobStatus.TransferState(0, 1000, 0, 1000, 5)
        self.model_builder.set_lftp_statuses([reset_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(movies_file_id)
        self.assertEqual(0, file_dup.transferred_size)
        self.assertEqual(0, file_dup.download_progress)
        self.assertNotIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_keeps_percent_floor_when_size_has_caught_up(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 750, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])
        self.model_builder.build_model()

        resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        resume_status.total_transfer_state = LftpJobStatus.TransferState(800, 1000, 74, 1000, 5)
        self.model_builder.set_lftp_statuses([resume_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(800, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(5, file_a.eta)

        caught_up_percent_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        caught_up_percent_status.total_transfer_state = LftpJobStatus.TransferState(810, 1000, 76, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_percent_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(810, file_a.transferred_size)
        self.assertEqual(76, file_a.download_progress)

    def test_build_resumed_queued_state_keeps_retained_stopped_floor_for_non_authoritative_local_progress(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False, is_staging=True)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_resumed_queued_state_keeps_retained_stopped_floor_for_authoritative_local_progress(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 750, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 78, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertEqual(78, file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_resumed_queued_state_allows_explicit_zero_reset_after_stop(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, file_a.state)
        self.assertEqual(0, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_resumed_running_state_allows_clear_reset_signal_after_stop(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])
        self.model_builder.build_model()

        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        reset_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        reset_resume_status.total_transfer_state = LftpJobStatus.TransferState(0, 1000, 0, 1000, 5)
        self.model_builder.set_lftp_statuses([reset_resume_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(0, file_a.transferred_size)
        self.assertEqual(0, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(5, file_a.eta)

    def test_build_resumed_running_state_keeps_retained_floor_for_near_zero_non_zero_percent(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])
        self.model_builder.build_model()

        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        near_zero_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        near_zero_resume_status.total_transfer_state = LftpJobStatus.TransferState(100, 1000, 0.004, 1000, 5)
        self.model_builder.set_lftp_statuses([near_zero_resume_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(5, file_a.eta)

    def test_build_resumed_running_state_keeps_retained_floor_for_transient_smaller_authoritative_local_sample(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])
        self.model_builder.build_model()

        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        regressing_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        regressing_resume_status.total_transfer_state = LftpJobStatus.TransferState(100, 1000, 10, 1000, 5)
        self.model_builder.set_lftp_statuses([regressing_resume_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(5, file_a.eta)

    def test_build_running_state_promotes_to_downloaded_when_authoritative_local_file_is_complete(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])

        running_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(990, 1000, 99, 1000, 0)
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, file_a.state)
        self.assertEqual(1000, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_stopped_file_retains_snapshot_across_remote_missing_then_reappears(self):
        self.model_builder.clear()
        local_file = SystemFile("a", 650, False)
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )
        self.model_builder.set_stopped_files({"a"})

        first_model = self.model_builder.build_model()
        first_file = first_model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, first_file.state)
        self.assertEqual(650, first_file.local_size)
        self.assertEqual(650, first_file.transferred_size)
        self.assertEqual(65, first_file.download_progress)

        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])

        second_model = self.model_builder.build_model()
        second_file = second_model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, second_file.state)
        self.assertEqual(1000, second_file.remote_size)
        self.assertEqual(650, second_file.transferred_size)
        self.assertEqual(65, second_file.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_evict_recent_live_transfer_snapshots_missing_roots_preserves_stopped_snapshot(self):
        self.model_builder.clear()
        local_file = SystemFile("a", 650, False)
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_stopped_files({"a"})
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )

        self.model_builder.evict_recent_live_transfer_snapshots_missing_roots(set())

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertEqual(65, file_a.download_progress)
        self.assertIn("a", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_evict_recent_live_transfer_snapshots_missing_roots_preserves_stopped_and_evicts_non_stopped(self):
        self.model_builder.clear()
        stopped_local_file = SystemFile("a", 650, False)
        running_local_file = SystemFile("b", 400, False)
        self.model_builder.set_local_files([stopped_local_file, running_local_file])
        self.model_builder.set_stopped_files({"a"})
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["b"] = _RecentLiveTransferSnapshot(
            root_file_id="b",
            size_local=400,
            percent_local=40,
            speed=None,
            eta=None
        )

        self.model_builder.evict_recent_live_transfer_snapshots_missing_roots(set())

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        file_b = model.get_file("b")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertEqual(65, file_a.download_progress)
        self.assertEqual(ModelFile.State.DEFAULT, file_b.state)
        self.assertIsNone(file_b.transferred_size)
        self.assertIsNone(file_b.download_progress)
        self.assertIn("a", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)
        self.assertNotIn("b", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_evict_recent_live_transfer_snapshots_missing_roots_alias_stopped_snapshot_is_removed_after_protection_clears(self):
        self.model_builder.clear()
        qualified_file_id = ModelFile.build_file_id("dup", "movies")
        local_file = SystemFile("dup", 650, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_stopped_files({qualified_file_id})
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots[qualified_file_id] = _RecentLiveTransferSnapshot(
            root_file_id="dup",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )

        self.model_builder.evict_recent_live_transfer_snapshots_missing_roots(set())
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

        self.model_builder.set_stopped_files(set())
        self.model_builder.evict_recent_live_transfer_snapshots_missing_roots(set())

        model = self.model_builder.build_model()
        file_dup = model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DEFAULT, file_dup.state)
        self.assertIsNone(file_dup.transferred_size)
        self.assertIsNone(file_dup.download_progress)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_build_stopped_directory_name_entry_suppresses_child_live_transfer_state(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, False)
        local_root.add_child(local_child)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_stopped_files({"dup"})
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]

        self.assertEqual(ModelFile.State.DEFAULT, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(650, child.transferred_size)
        self.assertIsNone(child.download_progress)
        self.assertIsNone(child.downloading_speed)
        self.assertIsNone(child.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_directory_name_entry_suppresses_nested_descendant_live_transfer_state(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, True)
        remote_root.add_child(remote_child)
        remote_grandchild = SystemFile("bb", 1000, False)
        remote_child.add_child(remote_grandchild)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, True)
        local_root.add_child(local_child)
        local_grandchild = SystemFile("bb", 650, False)
        local_child.add_child(local_grandchild)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa/bb", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_stopped_files({"dup"})
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]
        grandchild = child.get_children()[0]

        self.assertEqual(ModelFile.State.DEFAULT, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(ModelFile.State.DEFAULT, grandchild.state)
        self.assertEqual(650, grandchild.transferred_size)
        self.assertIsNone(grandchild.download_progress)
        self.assertIsNone(grandchild.downloading_speed)
        self.assertIsNone(grandchild.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_descendant_file_id_suppresses_nested_descendant_live_transfer_state(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, True)
        remote_root.add_child(remote_child)
        remote_grandchild = SystemFile("bb", 1000, False)
        remote_child.add_child(remote_grandchild)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, True)
        local_root.add_child(local_child)
        local_grandchild = SystemFile("bb", 650, False)
        local_child.add_child(local_grandchild)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa/bb", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_stopped_files({ModelFile.build_file_id(os.path.join("dup", "aa", "bb"), "movies")})
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]
        grandchild = child.get_children()[0]

        self.assertEqual(ModelFile.State.DOWNLOADING, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(ModelFile.State.DEFAULT, grandchild.state)
        self.assertEqual(650, grandchild.transferred_size)
        self.assertIsNone(grandchild.download_progress)
        self.assertIsNone(grandchild.downloading_speed)
        self.assertIsNone(grandchild.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_unrelated_legacy_stopped_name_does_not_suppress_matching_grandchild(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, True)
        remote_root.add_child(remote_child)
        remote_grandchild = SystemFile("bb", 1000, False)
        remote_child.add_child(remote_grandchild)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, True)
        local_root.add_child(local_child)
        local_grandchild = SystemFile("bb", 650, False)
        local_child.add_child(local_grandchild)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa/bb", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_stopped_files({"bb"})
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]
        grandchild = child.get_children()[0]

        self.assertEqual(ModelFile.State.DOWNLOADING, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(ModelFile.State.DOWNLOADING, grandchild.state)
        self.assertEqual(750, grandchild.transferred_size)
        self.assertEqual(75, grandchild.download_progress)
        self.assertEqual(1000, grandchild.downloading_speed)
        self.assertEqual(5, grandchild.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_descendant_snapshot_ignores_unrelated_legacy_stopped_name(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, True)
        remote_root.add_child(remote_child)
        remote_grandchild = SystemFile("bb", 1000, False)
        remote_child.add_child(remote_grandchild)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, True)
        local_root.add_child(local_child)
        local_grandchild = SystemFile("bb", 650, False)
        local_child.add_child(local_grandchild)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa/bb", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        grandchild = root.get_children()[0].get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADING, grandchild.state)
        self.assertEqual(750, grandchild.transferred_size)
        self.assertEqual(75, grandchild.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        self.model_builder.set_stopped_files({"bb"})

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]
        grandchild = child.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADING, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(ModelFile.State.DOWNLOADING, grandchild.state)
        self.assertEqual(750, grandchild.transferred_size)
        self.assertEqual(75, grandchild.download_progress)
        self.assertEqual(1000, grandchild.downloading_speed)
        self.assertEqual(5, grandchild.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_survives_until_local_catches_up(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        self.assertEqual(750, model.get_file("a").transferred_size)

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)
        self.assertEqual(750, model.get_file("a").transferred_size)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_local_files([SystemFile("a", 750, False)])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_retained_snapshot_keeps_production_shaped_idle_model_cached_until_local_catches_up(self):
        remote_files = [SystemFile("release-{:03}.mkv".format(index), 1000, False) for index in range(96)]
        local_files = [SystemFile("release-{:03}.mkv".format(index), 100, False) for index in range(96)]
        self.model_builder.set_remote_files(remote_files)
        self.model_builder.set_local_files(local_files)
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["release-042.mkv"] = \
            _RecentLiveTransferSnapshot(
                root_file_id="release-042.mkv",
                size_local=750,
                percent_local=75,
                speed=1000,
                eta=5,
            )

        first_model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DOWNLOADING, first_model.get_file("release-042.mkv").state)
        self.assertFalse(self.model_builder.has_changes())
        self.assertIs(first_model, self.model_builder.build_model())

        caught_up_local_files = [
            SystemFile(file.name, 750 if file.name == "release-042.mkv" else file.size, False)
            for file in local_files
        ]
        self.model_builder.set_local_files(caught_up_local_files)
        caught_up_model = self.model_builder.build_model()

        self.assertIsNot(first_model, caught_up_model)
        self.assertEqual(ModelFile.State.DEFAULT, caught_up_model.get_file("release-042.mkv").state)
        self.assertNotIn("release-042.mkv", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_child_snapshot_is_suppressed_by_queued_root_state(self):
        remote_root = SystemFile("a", 1000, True)
        remote_child = SystemFile("aa", 1000, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("a", 650, True)
        local_child = SystemFile("aa", 650, False)
        local_root.add_child(local_child)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])
        self.model_builder.build_model()

        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        root = model.get_file("a")
        child = root.get_children()[0]
        self.assertEqual(ModelFile.State.QUEUED, child.state)
        self.assertEqual(650, child.transferred_size)
        self.assertIsNone(child.download_progress)
        self.assertIsNone(child.downloading_speed)
        self.assertIsNone(child.eta)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        root = model.get_file("a")
        child = root.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, root.state)
        self.assertEqual(650, root.transferred_size)
        self.assertIsNone(root.download_progress)
        self.assertIsNone(root.downloading_speed)
        self.assertIsNone(root.eta)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(650, child.transferred_size)
        self.assertIsNone(child.download_progress)
        self.assertIsNone(child.downloading_speed)
        self.assertIsNone(child.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_active_overlay_falls_back_to_persistent_local_state_when_cleared(self):
        # The active overlay is transient; clearing it should expose the
        # persistent local snapshot again instead of keeping stale active data.
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        self.model_builder.set_active_files([SystemFile("a", 99, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(12345, 1000, 0.25, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(99, model.get_file("a").local_size)

        # Clear the overlay and rebuild from persistent local state.
        self.model_builder.set_active_files([])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(12345, 1000, 0.25, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").local_size)

    def test_evict_active_file_ids_falls_back_to_persistent_local_state(self):
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        self.model_builder.set_active_files([SystemFile("a", 99, False)])
        model = self.model_builder.build_model()
        self.assertEqual(99, model.get_file("a").local_size)

        self.model_builder.evict_active_file_ids({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").local_size)

    def test_build_running_state_merges_active_files_without_mutating_local_state(self):
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        self.model_builder.set_active_files([SystemFile("a", 99, False)])

        model = self.model_builder.build_model()

        self.assertEqual(42, self.model_builder._ModelBuilder__local_files_by_pair[None]["a"].size)
        self.assertEqual(99, self.model_builder._ModelBuilder__active_files["a"].size)
        self.assertEqual(99, model.get_file("a").local_size)
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        self.model_builder.set_active_files([])
        self.assertTrue(self.model_builder.has_changes())

        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").local_size)
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

    def test_rebuild_on_cleared_active_files_rebuilds_cached_overlay(self):
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        self.model_builder.set_active_files([SystemFile("a", 99, False)])

        model = self.model_builder.build_model()
        self.assertEqual(99, model.get_file("a").local_size)

        self.model_builder.set_active_files([])
        self.assertTrue(self.model_builder.has_changes())

        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").local_size)
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

    def test_build_downloading_speed(self):
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 1234, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(1234, model.get_file("a").downloading_speed)

        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").downloading_speed)

        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").downloading_speed)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").downloading_speed)

    def test_build_sets_is_stoppable_for_queued_and_resumable_downloads(self):
        self.model_builder.set_remote_files([SystemFile("queued", 100, False),
                                             SystemFile("downloading", 100, False)])
        queued_local = SystemFile("queued", 0, False, is_staging=True)
        downloading_local = SystemFile("downloading", 10, False, is_staging=True)
        downloading_local.status_sidecar_ready = True
        self.model_builder.set_local_files([
            queued_local,
            downloading_local,
        ])

        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "queued", "")
        downloading_status = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "downloading", "")
        downloading_status.total_transfer_state = LftpJobStatus.TransferState(10, 100, 10, 100, 10)
        self.model_builder.set_lftp_statuses([queued_status, downloading_status])

        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("queued").is_stoppable)
        self.assertTrue(model.get_file("downloading").is_stoppable)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("downloading", 100, False)])
        downloading_local = SystemFile("downloading", 10, False, is_staging=True)
        downloading_local.status_sidecar_ready = False
        self.model_builder.set_local_files([downloading_local])
        downloading_status = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "downloading", "")
        downloading_status.total_transfer_state = LftpJobStatus.TransferState(10, 100, 10, 100, 10)
        self.model_builder.set_lftp_statuses([downloading_status])

        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("downloading").is_stoppable)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("downloading", 100, False)])
        downloading_local = SystemFile("downloading", 10, False)
        self.model_builder.set_local_files([downloading_local])

        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("downloading").is_stoppable)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("downloading", 100, False)])
        downloading_local = SystemFile("downloading", 10, False)
        downloading_local.status_sidecar_ready = False
        self.model_builder.set_local_files([downloading_local])

        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("downloading").is_stoppable)

    def test_build_eta(self):
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, None, 4567)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(4567, model.get_file("a").eta)

        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

    def test_build_estimated_eta(self):
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 100, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(10, model.get_file("a").eta)

        # round up
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 133, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(8, model.get_file("a").eta)

        # round up
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 133, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1999, False)])
        model = self.model_builder.build_model()
        self.assertEqual(1, model.get_file("a").eta)

        # zero downloading speed
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 0, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        # remote size unavailable
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 100, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        # finished
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 200, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 2000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        # local size larger than remote
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 200, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 3000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

    def test_build_children_names(self):
        model = self.__build_test_model_children_tree_1()
        self.assertEqual({"a", "b", "c", "d"}, model.get_file_names())
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        self.assertEqual({"aa", "ab"}, m_a_ch.keys())
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        self.assertEqual({"ba", "bb", "bc", "bd"}, m_b_ch.keys())
        m_ba_ch = {m.name: m for m in m_b_ch["ba"].get_children()}
        self.assertEqual({"baa"}, m_ba_ch.keys())
        m_baa_ch = {m.name: m for m in m_ba_ch["baa"].get_children()}
        self.assertEqual(0, len(m_baa_ch.keys()))
        m_bb_ch = {m.name: m for m in m_b_ch["bb"].get_children()}
        self.assertEqual({"bba"}, m_bb_ch.keys())
        m_bba_ch = {m.name: m for m in m_bb_ch["bba"].get_children()}
        self.assertEqual(0, len(m_bba_ch.keys()))
        m_bc_ch = {m.name: m for m in m_b_ch["bc"].get_children()}
        self.assertEqual({"bca"}, m_bc_ch.keys())
        m_bca_ch = {m.name: m for m in m_bc_ch["bca"].get_children()}
        self.assertEqual(0, len(m_bca_ch.keys()))
        m_c_ch = {m.name: m for m in model.get_file("c").get_children()}
        self.assertEqual(0, len(m_c_ch.keys()))
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        self.assertEqual({"da"}, m_d_ch.keys())
        m_da_ch = {m.name: m for m in m_d_ch["da"].get_children()}
        self.assertEqual(0, len(m_da_ch.keys()))

    def test_build_children_is_dir(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual(True, m_a.is_dir)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(False, m_aa.is_dir)
        m_ab = m_a_ch["ab"]
        self.assertEqual(False, m_ab.is_dir)
        m_b = model.get_file("b")
        self.assertEqual(True, m_b.is_dir)
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual(True, m_ba.is_dir)
        m_baa = m_ba.get_children()[0]
        self.assertEqual(False, m_baa.is_dir)
        m_bb = m_b_ch["bb"]
        self.assertEqual(True, m_bb.is_dir)
        m_bba = m_bb.get_children()[0]
        self.assertEqual(False, m_bba.is_dir)
        m_bc = m_b_ch["bc"]
        self.assertEqual(True, m_bc.is_dir)
        m_bca = m_bc.get_children()[0]
        self.assertEqual(False, m_bca.is_dir)
        m_bd = m_b_ch["bd"]
        self.assertEqual(False, m_bd.is_dir)
        m_c = model.get_file("c")
        self.assertEqual(False, m_c.is_dir)
        m_d = model.get_file("d")
        self.assertEqual(True, m_d.is_dir)
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual(False, m_da.is_dir)

    def test_build_children_mismatch_is_dir(self):
        """Mismatching is_dir in a child raises error"""
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        l_a = SystemFile("a", 0, True)
        l_aa = SystemFile("aa", 0, False)
        l_a.add_child(l_aa)
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir between child"))

    def test_build_children_sizes(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual((1024, 1024, 1024), (m_a.remote_size, m_a.local_size, m_a.transferred_size))
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual((512, 512, 512), (m_aa.remote_size, m_aa.local_size, m_aa.transferred_size))
        m_ab = m_a_ch["ab"]
        self.assertEqual((512, 512, 512), (m_ab.remote_size, m_ab.local_size, m_ab.transferred_size))
        m_b = model.get_file("b")
        self.assertEqual((3090, 1611, 1611), (m_b.remote_size, m_b.local_size, m_b.transferred_size))
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual((2048, 512, 512), (m_ba.remote_size, m_ba.local_size, m_ba.transferred_size))
        m_baa = m_ba.get_children()[0]
        self.assertEqual((2048, 512, 512), (m_baa.remote_size, m_baa.local_size, m_baa.transferred_size))
        m_bb = m_b_ch["bb"]
        self.assertEqual((42, None, None), (m_bb.remote_size, m_bb.local_size, m_bb.transferred_size))
        m_bba = m_bb.get_children()[0]
        self.assertEqual((42, None, None), (m_bba.remote_size, m_bba.local_size, m_bba.transferred_size))
        m_bc = m_b_ch["bc"]
        self.assertEqual((None, 99, None), (m_bc.remote_size, m_bc.local_size, m_bc.transferred_size))
        m_bca = m_bc.get_children()[0]
        self.assertEqual((None, 99, None), (m_bca.remote_size, m_bca.local_size, m_bca.transferred_size))
        m_bd = m_b_ch["bd"]
        self.assertEqual((1000, 1000, 1000), (m_bd.remote_size, m_bd.local_size, m_bd.transferred_size))
        m_c = model.get_file("c")
        self.assertEqual((1234, None, None), (m_c.remote_size, m_c.local_size, m_c.transferred_size))
        m_d = model.get_file("d")
        self.assertEqual((5678, None, None), (m_d.remote_size, m_d.local_size, m_d.transferred_size))
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual((5678, None, None), (m_da.remote_size, m_da.local_size, m_da.transferred_size))

    def test_build_children_state_default(self):
        """File only exists remotely"""
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        self.model_builder.set_remote_files([r_a])
        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ab.state)

    def test_build_children_state_default_partial(self):
        """File is partially downloaded"""
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 150, True)
        l_aa = SystemFile("aa", 50, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 50, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 100, False)
        l_a.add_child(l_ab)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])
        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ab.state)

    def test_build_children_state_default_extra(self):
        """File only exists locally"""
        l_a = SystemFile("a", 150, True)
        l_aa = SystemFile("aa", 50, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 50, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 100, False)
        l_a.add_child(l_ab)

        self.model_builder.set_local_files([l_a])
        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ab.state)

    def test_build_children_state_downloaded_full(self):
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 300, True)
        l_aa = SystemFile("aa", 100, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 100, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 200, False)
        l_a.add_child(l_ab)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)

    def test_build_children_state_downloaded_full_extra(self):
        """Fully downloaded but with an extra local-only file"""
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 400, True)
        l_aa = SystemFile("aa", 100, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 100, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 200, False)
        l_a.add_child(l_ab)
        l_ac = SystemFile("ac", 100, True)  # local only
        l_a.add_child(l_ac)
        l_aca = SystemFile("aca", 100, False)  # local only
        l_ac.add_child(l_aca)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)
        l_ac = SystemFile("ac", 100, True)  # local only
        l_a.add_child(l_ac)
        l_aca = SystemFile("aca", 100, False)  # local only
        l_ac.add_child(l_aca)

    def test_build_children_state_downloaded_partial(self):
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 250, True)
        l_aa = SystemFile("aa", 50, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 50, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 200, False)
        l_a.add_child(l_ab)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)

    def test_build_children_state_downloaded_partial_extra(self):
        """Partially downloaded but with an extra local-only file"""
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 350, True)
        l_aa = SystemFile("aa", 50, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 50, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 200, False)
        l_a.add_child(l_ab)
        l_ac = SystemFile("ac", 100, True)  # local only
        l_a.add_child(l_ac)
        l_aca = SystemFile("aca", 100, False)  # local only
        l_ac.add_child(l_aca)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)
        m_ac = m_a_ch["ac"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ac.state)
        m_aca = m_ac.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aca.state)

    def test_build_children_state_default_remote_dir_without_remote_leaf_files(self):
        """Remote directories without remote files should not be marked downloaded"""
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)

        l_a = SystemFile("a", 100, True)
        l_aa = SystemFile("aa", 100, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 100, False)  # local only leaf
        l_aa.add_child(l_aaa)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_aa = m_a.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)

    def test_build_children_state_queued(self):
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 0, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 0, False)
        r_a.add_child(r_ab)
        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.QUEUED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.QUEUED, m_ab.state)

    def test_build_children_state_downloading_1(self):
        # Child files are active
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 0, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 0, False)
        r_a.add_child(r_ab)
        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        s_a.add_active_file_transfer_state("aa/aaa", LftpJobStatus.TransferState(None, None, None, None, None))
        s_a.add_active_file_transfer_state("ab", LftpJobStatus.TransferState(None, None, None, None, None))
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADING, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADING, m_ab.state)

    def test_build_children_state_downloading_2(self):
        # Child files are finished
        r_a = SystemFile("a", 150, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 50, False)
        r_a.add_child(r_ab)

        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([r_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)

    def test_build_children_state_downloading_3(self):
        # Child files are queued
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 0, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 0, False)
        r_a.add_child(r_ab)
        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.QUEUED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.QUEUED, m_ab.state)

    def test_build_children_state_downloading_4(self):
        # Child files are only present in local
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        l_a = SystemFile("a", 0, True)
        l_aa = SystemFile("aa", 0, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 0, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 0, False)
        l_a.add_child(l_ab)
        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ab.state)

    def test_build_children_state_all(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_aa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)
        m_b = model.get_file("b")
        self.assertEqual(ModelFile.State.DOWNLOADING, m_b.state)
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ba.state)
        m_baa = m_ba.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADING, m_baa.state)
        m_bb = m_b_ch["bb"]
        self.assertEqual(ModelFile.State.DEFAULT, m_bb.state)
        m_bba = m_bb.get_children()[0]
        self.assertEqual(ModelFile.State.QUEUED, m_bba.state)
        m_bc = m_b_ch["bc"]
        self.assertEqual(ModelFile.State.DEFAULT, m_bc.state)
        m_bca = m_bc.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_bca.state)
        m_bd = m_b_ch["bd"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_bd.state)
        m_c = model.get_file("c")
        self.assertEqual(ModelFile.State.QUEUED, m_c.state)
        m_d = model.get_file("d")
        self.assertEqual(ModelFile.State.QUEUED, m_d.state)
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual(ModelFile.State.QUEUED, m_da.state)

    def test_build_children_downloading_speed(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual(None, m_a.downloading_speed)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(None, m_aa.downloading_speed)
        m_ab = m_a_ch["ab"]
        self.assertEqual(None, m_ab.downloading_speed)
        m_b = model.get_file("b")
        self.assertEqual(10, m_b.downloading_speed)
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual(None, m_ba.downloading_speed)
        m_baa = m_ba.get_children()[0]
        self.assertEqual(5, m_baa.downloading_speed)
        m_bb = m_b_ch["bb"]
        self.assertEqual(None, m_bb.downloading_speed)
        m_bba = m_bb.get_children()[0]
        self.assertEqual(None, m_bba.downloading_speed)
        m_bc = m_b_ch["bc"]
        self.assertEqual(None, m_bc.downloading_speed)
        m_bca = m_bc.get_children()[0]
        self.assertEqual(None, m_bca.downloading_speed)
        m_bd = m_b_ch["bd"]
        self.assertEqual(None, m_bd.downloading_speed)
        m_c = model.get_file("c")
        self.assertEqual(None, m_c.downloading_speed)
        m_d = model.get_file("d")
        self.assertEqual(None, m_d.downloading_speed)
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual(None, m_da.downloading_speed)

    def test_build_children_eta(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual(None, m_a.eta)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(None, m_aa.eta)
        m_ab = m_a_ch["ab"]
        self.assertEqual(None, m_ab.eta)
        m_b = model.get_file("b")
        self.assertEqual(1000, m_b.eta)
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual(None, m_ba.eta)
        m_baa = m_ba.get_children()[0]
        self.assertEqual(500, m_baa.eta)
        m_bb = m_b_ch["bb"]
        self.assertEqual(None, m_bb.eta)
        m_bba = m_bb.get_children()[0]
        self.assertEqual(None, m_bba.eta)
        m_bc = m_b_ch["bc"]
        self.assertEqual(None, m_bc.eta)
        m_bca = m_bc.get_children()[0]
        self.assertEqual(None, m_bca.eta)
        m_bd = m_b_ch["bd"]
        self.assertEqual(None, m_bd.eta)
        m_c = model.get_file("c")
        self.assertEqual(None, m_c.eta)
        m_d = model.get_file("d")
        self.assertEqual(None, m_d.eta)
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual(None, m_da.eta)

    @patch("controller.model_builder.Extract")
    def test_build_sets_is_extractable(self, mock_extract_module):
        mock_is_archive_fast = mock_extract_module.is_archive_fast
        is_archive_list = []

        def _is_archive_fast(name: str):
            return name in is_archive_list
        mock_is_archive_fast.side_effect = _is_archive_fast

        # Root local file
        self.model_builder.clear()
        is_archive_list = ["a"]
        self.model_builder.set_local_files([SystemFile("a", 10, False), SystemFile("b", 10, False)])
        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("a").is_extractable)
        self.assertFalse(model.get_file("b").is_extractable)

        # Root remote file
        self.model_builder.clear()
        is_archive_list = ["b"]
        self.model_builder.set_remote_files([SystemFile("a", 10, False), SystemFile("b", 10, False)])
        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("a").is_extractable)
        self.assertTrue(model.get_file("b").is_extractable)

        # Directory with archive
        self.model_builder.clear()
        is_archive_list = ["aa"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("aa", 10, False)
        a.add_child(aa)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("a").is_extractable)
        self.assertEqual("aa", model.get_file("a").get_children()[0].name)
        self.assertTrue(model.get_file("a").get_children()[0].is_extractable)

        # Directory with non-archive
        self.model_builder.clear()
        is_archive_list = ["aa"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("ab", 10, False)
        a.add_child(aa)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("a").is_extractable)
        self.assertEqual("ab", model.get_file("a").get_children()[0].name)
        self.assertFalse(model.get_file("a").get_children()[0].is_extractable)

        # Directory with archive and non-archive
        self.model_builder.clear()
        is_archive_list = ["ab"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("aa", 10, False)
        ab = SystemFile("ab", 10, False)
        a.add_child(aa)
        a.add_child(ab)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("a").is_extractable)
        a_children = {f.name: f for f in model.get_file("a").get_children()}
        self.assertFalse(a_children["aa"].is_extractable)
        self.assertTrue(a_children["ab"].is_extractable)

        # Directory with archive and non-archive sub-directories
        self.model_builder.clear()
        is_archive_list = ["aba"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("aa", 10, True)
        aaa = SystemFile("aaa", 10, False)
        ab = SystemFile("ab", 10, True)
        aba = SystemFile("aba", 10, False)
        a.add_child(aa)
        a.add_child(ab)
        aa.add_child(aaa)
        ab.add_child(aba)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("a").is_extractable)
        a_children = {f.name: f for f in model.get_file("a").get_children()}
        self.assertFalse(a_children["aa"].is_extractable)
        self.assertEqual("aaa", a_children["aa"].get_children()[0].name)
        self.assertFalse(a_children["aa"].get_children()[0].is_extractable)
        self.assertTrue(a_children["ab"].is_extractable)
        self.assertEqual("aba", a_children["ab"].get_children()[0].name)
        self.assertTrue(a_children["ab"].get_children()[0].is_extractable)

        # Directory name passes is_archive, but not file
        self.model_builder.clear()
        is_archive_list = ["a"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("aa", 10, False)
        a.add_child(aa)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("a").is_extractable)
        self.assertEqual("aa", model.get_file("a").get_children()[0].name)
        self.assertFalse(model.get_file("a").get_children()[0].is_extractable)

    def test_build_transferred_size(self):
        # both remote and local
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        self.model_builder.set_local_files([SystemFile("a", 22, False)])
        model = self.model_builder.build_model()
        self.assertEqual(22, model.get_file("a").transferred_size)

        # remote but no local
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").transferred_size)

        # local but no remote
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 22, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").transferred_size)

        # local size larger than remote
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        self.model_builder.set_local_files([SystemFile("a", 55, False)])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").transferred_size)

        # active download prefers live transfer bytes
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        self.model_builder.set_local_files([SystemFile("a", 22, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(33, 42, 0.75, 1000, 5)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(33, model.get_file("a").transferred_size)

        # downloading directories without an explicit live root value still
        # aggregate child live bytes
        self.model_builder.clear()
        remote_root = SystemFile("root", 100, True)
        remote_child = SystemFile("child", 100, False)
        remote_root.add_child(remote_child)
        local_root = SystemFile("root", 0, True)
        local_child = SystemFile("child", 0, False)
        local_root.add_child(local_child)
        status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "root", "")
        status.total_transfer_state = LftpJobStatus.TransferState(None, 100, None, 1000, 5)
        status.add_active_file_transfer_state("child", LftpJobStatus.TransferState(18, 100, 0.18, 500, 3))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([status])
        model = self.model_builder.build_model()
        self.assertEqual(18, model.get_file("root").transferred_size)

        # both remote and local directory (but no children specified)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, True)])
        self.model_builder.set_local_files([SystemFile("a", 22, True)])
        model = self.model_builder.build_model()
        self.assertEqual(0, model.get_file("a").transferred_size)

        # remote only directory
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, True)])
        model = self.model_builder.build_model()
        self.assertEqual(set(), model.get_file_names())

        # local only directory
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 22, True)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").transferred_size)

    def test_build_staging_directory_without_live_status_is_marked_downloaded(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, True)])
        self.model_builder.set_local_files([SystemFile("a", 42, True, is_staging=True)])

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("a").state)

    def test_build_local_created_timestamp(self):
        self.model_builder.set_local_files([
            SystemFile("a", 42, False, time_created=datetime(2018, 11, 9, 21, 40, 18)),
            SystemFile("b", 42, False)
        ])
        model = self.model_builder.build_model()
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18),
                         model.get_file("a").local_created_timestamp)
        self.assertIsNone(model.get_file("b").local_created_timestamp)

    def test_build_local_modified_timestamp(self):
        self.model_builder.set_local_files([
            SystemFile("a", 42, False, time_modified=datetime(2018, 11, 9, 21, 40, 18)),
            SystemFile("b", 42, False)
        ])
        model = self.model_builder.build_model()
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18),
                         model.get_file("a").local_modified_timestamp)
        self.assertIsNone(model.get_file("b").local_modified_timestamp)

    def test_build_remote_created_timestamp(self):
        self.model_builder.set_remote_files([
            SystemFile("a", 42, False, time_created=datetime(2018, 11, 9, 21, 40, 18)),
            SystemFile("b", 42, False)
        ])
        model = self.model_builder.build_model()
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18),
                         model.get_file("a").remote_created_timestamp)
        self.assertIsNone(model.get_file("b").remote_created_timestamp)

    def test_build_remote_modified_timestamp(self):
        self.model_builder.set_remote_files([
            SystemFile("a", 42, False, time_modified=datetime(2018, 11, 9, 21, 40, 18)),
            SystemFile("b", 42, False)
        ])
        model = self.model_builder.build_model()
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18),
                         model.get_file("a").remote_modified_timestamp)
        self.assertIsNone(model.get_file("b").remote_modified_timestamp)

    def test_rebuild(self):
        remote_files = [SystemFile("a", 0, False), SystemFile("b", 0, False)]
        local_files = [SystemFile("b", 0, False), SystemFile("c", 0, False)]
        statuses = [LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", ""),
                    LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "d", "")]
        self.model_builder.set_remote_files(remote_files)
        self.model_builder.set_local_files(local_files)
        self.model_builder.set_lftp_statuses(statuses)
        model = self.model_builder.build_model()
        self.assertEqual({"a", "b", "c", "d"}, model.get_file_names())

        self.assertFalse(self.model_builder.has_changes())

        # Set without any changes
        remote_files = [SystemFile("a", 0, False), SystemFile("b", 0, False)]
        self.model_builder.set_remote_files(remote_files)
        self.assertFalse(self.model_builder.has_changes())
        model = self.model_builder.build_model()
        self.assertEqual({"a", "b", "c", "d"}, model.get_file_names())

        # Set with changes
        remote_files = [SystemFile("b", 0, False), SystemFile("e", 0, False)]
        self.model_builder.set_remote_files(remote_files)
        self.assertTrue(self.model_builder.has_changes())
        model = self.model_builder.build_model()
        self.assertEqual({"b", "c", "d", "e"}, model.get_file_names())

    def test_rebuild_on_active_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_active_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Equal active snapshots are no-ops; rebuilding them would starve
        # status-only root deltas behind a full model walk.
        self.model_builder.set_active_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.assertFalse(self.model_builder.has_changes())
        self.model_builder.build_model()

        # Invalidates when the active overlay is cleared after previously being populated
        self.model_builder.set_active_files([])
        self.assertTrue(self.model_builder.has_changes())

    def test_cache_invalidation_counters_are_fixed_and_count_changed_inputs(self):
        enabled = [True]
        diagnostics = PerformanceDiagnosticsCollector(lambda: enabled[0])
        self.model_builder.set_performance_diagnostics(diagnostics)

        self.model_builder.set_local_files([SystemFile("local", 10)])
        self.model_builder.set_local_files([SystemFile("local", 10)])
        self.model_builder.set_local_files([SystemFile("local", 11)])

        self.model_builder.set_remote_files([SystemFile("remote", 10)])
        self.model_builder.set_remote_files([SystemFile("remote", 10)])
        self.model_builder.set_remote_files([SystemFile("remote", 11)])

        self.model_builder.set_active_files([SystemFile("active", 10)])
        self.model_builder.set_active_files([SystemFile("active", 10)])

        status = LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "status", "flags")
        self.model_builder.set_lftp_statuses([status])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "status", "flags")
        ])
        changed_status = LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "status", "flags")
        self.model_builder.set_lftp_statuses([changed_status])

        self.model_builder.set_unknown_local_path_pair_ids({None})
        self.model_builder.set_unknown_local_path_pair_ids({None})
        self.model_builder.set_unknown_local_path_pair_ids({"movies"})

        self.model_builder.set_local_root_paths({None: "/local"})
        self.model_builder.set_downloaded_files({"downloaded"})
        self.model_builder.set_downloaded_files({"downloaded"})
        self.model_builder.set_downloaded_timestamps({"downloaded": 1.0})
        self.model_builder.set_extract_statuses([
            ExtractStatus("archive", False, ExtractStatus.State.EXTRACTING)
        ])
        self.model_builder.set_extracted_files({"archive"})
        self.model_builder.set_stopped_files({"stopped"})
        self.model_builder.set_move_failed_files({"failed"})
        self.model_builder.set_final_move_succeeded_files({"succeeded"})
        self.model_builder.set_validation_statuses([
            ValidateStatus("validated", ModelFile.State.VALIDATING)
        ])
        self.model_builder.request_rebuild()

        enabled[0] = False
        self.model_builder.set_remote_files([SystemFile("remote", 12)])
        enabled[0] = True
        counters = diagnostics.snapshot()["counters"]

        self.assertEqual(2, counters[MODEL_BUILDER_INVALIDATION_LOCAL_FILES])
        self.assertEqual(2, counters[MODEL_BUILDER_INVALIDATION_REMOTE_FILES])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_ACTIVE_FILES])
        self.assertEqual(2, counters[MODEL_BUILDER_INVALIDATION_LFTP_STATUSES])
        self.assertEqual(2, counters[MODEL_BUILDER_INVALIDATION_UNKNOWN_LOCAL_PAIRS])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_LOCAL_ROOT_PATHS])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_EXTRACT_STATUSES])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_EXTRACTED_FILES])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_STOPPED_FILES])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_MOVE_FAILED_FILES])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_FINAL_MOVE_SUCCEEDED_FILES])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_VALIDATION_STATUSES])
        self.model_builder.clear()
        counters = diagnostics.snapshot()["counters"]
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_CLEAR])
        self.assertEqual(1, counters[MODEL_BUILDER_INVALIDATION_EXPLICIT])
        diagnostics.increment("model_builder_cache_invalidation:/private/path")
        self.assertNotIn("model_builder_cache_invalidation:/private/path", diagnostics.snapshot()["counters"])

    def test_setter_durations_are_recorded_and_fail_closed(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        self.model_builder.set_performance_diagnostics(diagnostics)

        self.model_builder.set_local_files([])
        self.model_builder.set_remote_files([])
        self.model_builder.set_active_files([])
        self.model_builder.set_lftp_statuses([])
        self.model_builder.set_stopped_files(set())

        durations = diagnostics.snapshot()["durations"]
        metrics = (
            DURATION_MODEL_BUILDER_SET_LOCAL_FILES,
            DURATION_MODEL_BUILDER_SET_REMOTE_FILES,
            DURATION_MODEL_BUILDER_SET_ACTIVE_FILES,
            DURATION_MODEL_BUILDER_SET_LFTP_STATUSES,
            DURATION_MODEL_BUILDER_SET_STOPPED_FILES,
        )
        for metric in metrics:
            self.assertEqual(1, durations[metric]["count"])
            self.assertGreaterEqual(durations[metric]["total_wall_seconds"], 0.0)

        disabled = PerformanceDiagnosticsCollector(lambda: False)
        disabled_builder = ModelBuilder()
        disabled_builder.set_performance_diagnostics(disabled)
        disabled_builder.set_local_files([])
        disabled_builder.set_remote_files([])
        disabled_builder.set_active_files([])
        disabled_builder.set_lftp_statuses([])
        disabled_builder.set_stopped_files(set())
        self.assertEqual({}, disabled.snapshot()["durations"])

        class FailingDiagnostics:
            @staticmethod
            def begin_duration(_metric):
                raise RuntimeError("diagnostic begin failure")

            @staticmethod
            def finish_duration(_metric, _started_at):
                raise RuntimeError("diagnostic finish failure")

        self.model_builder.set_performance_diagnostics(FailingDiagnostics())
        self.model_builder.set_local_files([])
        self.model_builder.set_remote_files([])
        self.model_builder.set_active_files([])
        self.model_builder.set_lftp_statuses([])
        self.model_builder.set_stopped_files(set())

    def test_rebuild_on_local_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_local_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_local_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_local_files([
            SystemFile("a", 10),
            SystemFile("b", 21)
        ])
        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_remote_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_remote_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_remote_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_remote_files([
            SystemFile("a", 10),
            SystemFile("b", 21)
        ])
        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_lftp_statuses(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        s1 = LftpJobStatus(3, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "flags")
        s1.total_transfer_state = LftpJobStatus.TransferState(100, 200, 50, 10, 50)
        s2 = LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", "flags")
        self.model_builder.set_lftp_statuses([s1, s2])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        s1a = LftpJobStatus(3, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "flags")
        s1a.total_transfer_state = LftpJobStatus.TransferState(100, 200, 50, 10, 50)
        s2a = LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", "flags")
        self.model_builder.set_lftp_statuses([s1a, s2a])
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        s1b = LftpJobStatus(3, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "flags")
        s1b.total_transfer_state = LftpJobStatus.TransferState(150, 200, 50, 10, 50)
        s2b = LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", "flags")
        self.model_builder.set_lftp_statuses([s1b, s2b])
        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_downloaded_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_downloaded_files({"a", "b"})
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_downloaded_files({"a", "b"})
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_downloaded_files({"a", "c"})
        self.assertTrue(self.model_builder.has_changes())

    def test_clear_does_not_mutate_downloaded_files(self):
        downloaded_files = {"a", "b"}

        self.model_builder.set_downloaded_files(downloaded_files)
        self.model_builder.clear()

        self.assertEqual({"a", "b"}, downloaded_files)

    def test_rebuild_on_in_place_downloaded_file_mutation_after_reset(self):
        downloaded_files = {"a", "b"}

        self.model_builder.set_downloaded_files(downloaded_files)
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        downloaded_files.add("c")
        self.model_builder.set_downloaded_files(downloaded_files)

        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_extract_statuses(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_extract_statuses([
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING),
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING)
        ])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_extract_statuses([
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING),
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING)
        ])
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_extract_statuses([
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING),
            ExtractStatus("c", True, ExtractStatus.State.EXTRACTING)
        ])
        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_extracted_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_extracted_files({"a", "b"})
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_extracted_files({"a", "b"})
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_extracted_files({"a", "c"})
        self.assertTrue(self.model_builder.has_changes())

    def test_build_model_applies_validation_status(self):
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_validation_statuses([
            ValidateStatus(
                file_id="a",
                state=ModelFile.State.VALIDATING,
                progress=35,
                error=None,
                corrupt_chunks=None
            )
        ])

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.VALIDATING, model.get_file("a").state)
        self.assertEqual(35, model.get_file("a").validation_progress)

    def test_trace_disabled_is_noop_and_sse_metadata_absent(self):
        collector = self.__enable_trace(False)
        remote_file = SystemFile("a", 1000, False)
        local_file = SystemFile("a", 250, False)
        status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(250, 1000, 25, 50, 15)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_lftp_statuses([status])
        self.model_builder.begin_stop_resume_trace_cycle(1)
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        self.assertEqual([], self.__trace_entries(collector))
        self.assertIsNone(self.model_builder.stop_resume_trace_metadata_for_file(model.get_file("a")))

    def test_trace_captures_all_active_files_with_canonical_ids(self):
        file_name = "verifier-stop-regression-1g.bin"
        remote_file = SystemFile(file_name, 1073741824, False)
        local_file = SystemFile(file_name, 1067800592, False)
        active_file = SystemFile(file_name, 1067800592, False)
        running_status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(1044601281, 1073741824, 97, 50, 15)

        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_active_files([active_file])
        self.model_builder.set_lftp_statuses([running_status])
        collector = self.__enable_trace()
        self.model_builder.begin_stop_resume_trace_cycle(3)
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        entries = self.__trace_entries(collector)
        self.assertEqual({file_name}, {entry["file_id"] for entry in entries})
        self.assertTrue(entries[0]["corr_id"].startswith("stop-resume:" + file_name + ":3"))
        details = entries[0]["details"]
        for key in (
            "model_source",
            "update_context",
            "recent_snapshot_present",
            "retained_snapshot_present",
            "raw_lftp_status",
            "matched_local",
            "local_data_role",
            "local_size_apparent",
            "local_size_allocated",
            "presence",
            "arbitration_source",
            "final_model",
            "context",
        ):
            self.assertIn(key, details)
        self.assertEqual({"present": True, "is_staging": True}, details["matched_local"])
        self.assertNotIn("C:\\seedsync", str(details))
        self.assertNotIn("command", str(details).lower())

    def test_trace_boundary_details_omit_local_paths(self):
        file_name = "verifier-stop-regression-allocated.bin"
        remote_file = SystemFile(file_name, 1000, False)
        local_file = SystemFile(file_name, 250, False, is_staging=True)
        running_status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(250, 1000, 25, 50, 15)

        local_root_path = os.path.join("C:\\seedsync", "local")
        staging_root_path = os.path.join("C:\\seedsync", "local", "incomplete")
        selected_local_path = os.path.join(staging_root_path, file_name)
        self.model_builder.set_local_root_paths(
            {None: local_root_path},
            {None: staging_root_path}
        )
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_lftp_statuses([running_status])
        collector = self.__enable_trace()
        self.model_builder.begin_stop_resume_trace_cycle(4)
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        entries = self.__trace_entries(collector)
        self.assertEqual(1, len(entries))
        details = entries[0]["details"]
        self.assertNotIn("C:\\seedsync", str(details))
        self.assertNotIn("local_base_dir_path", str(details))
        self.assertNotIn("remote_base_dir_path", str(details))

    def test_set_local_root_paths_invalidates_cached_model_when_paths_change(self):
        remote_file = SystemFile("a", 1000, False)
        self.model_builder.set_remote_files([remote_file])

        self.assertTrue(self.model_builder.has_changes())
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_local_root_paths(
            {None: os.path.join("C:\\seedsync", "local")},
            {None: os.path.join("C:\\seedsync", "local", "incomplete")}
        )

        self.assertTrue(self.model_builder.has_changes())

    def test_trace_duplicate_basenames_remain_path_pair_isolated(self):
        remote_file = SystemFile("backup.zip", 1000, False)
        remote_file.path_pair_id = "homeserver"
        remote_file.path_pair_name = "Home Server"
        local_file = SystemFile("backup.zip", 250, False)
        local_file.path_pair_id = "homeserver"
        local_file.path_pair_name = "Home Server"
        running_status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "backup.zip", "")
        running_status.path_pair_id = "homeserver"
        running_status.path_pair_name = "Home Server"
        running_status.total_transfer_state = LftpJobStatus.TransferState(250, 1000, 25, 50, 15)

        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_lftp_statuses([running_status])
        second_remote = SystemFile("backup.zip", 900, False)
        second_remote.path_pair_id = "laptop"
        second_status = LftpJobStatus(8, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "backup.zip", "")
        second_status.path_pair_id = "laptop"
        second_status.total_transfer_state = LftpJobStatus.TransferState(100, 900, 11, 20, 40)
        collector = self.__enable_trace()
        self.model_builder.set_remote_files([remote_file, second_remote])
        self.model_builder.set_lftp_statuses([running_status, second_status])
        self.model_builder.begin_stop_resume_trace_cycle(4)
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        entries = self.__trace_entries(collector)
        expected_ids = {
            ModelFile.build_file_id("backup.zip", "homeserver"),
            ModelFile.build_file_id("backup.zip", "laptop"),
        }
        self.assertEqual(expected_ids, {entry["file_id"] for entry in entries})
        self.assertEqual(expected_ids, set(model.get_file_ids()))

    def test_trace_changed_context_is_retained_and_unchanged_cycles_coalesce(self):
        remote_file = SystemFile("a", 1000, False)
        local_file = SystemFile("a", 100, False, is_staging=True)
        collector = self.__enable_trace()

        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=250,
            percent_local=25,
            speed=50,
            eta=15
        )
        self.model_builder.begin_stop_resume_trace_cycle(6)

        recent_model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(recent_model, True)
        first_count = len(self.__trace_entries(collector))
        self.assertEqual(1, first_count)
        self.model_builder.begin_stop_resume_trace_cycle(7)
        self.model_builder.finish_stop_resume_trace_cycle(recent_model, False)
        self.assertEqual(first_count + 1, len(self.__trace_entries(collector)))
        self.model_builder.begin_stop_resume_trace_cycle(8)
        self.model_builder.finish_stop_resume_trace_cycle(recent_model, False)
        self.assertEqual(first_count + 1, len(self.__trace_entries(collector)))
        self.model_builder.begin_stop_resume_trace_cycle(9)
        self.model_builder.set_stop_resume_trace_cycle_context({"lftp_status_source": "fresh_healthy"})
        self.model_builder.finish_stop_resume_trace_cycle(recent_model, False)
        self.assertEqual(first_count + 2, len(self.__trace_entries(collector)))

    def test_no_rebuild_trace_queries_only_runtime_candidates(self):
        remote_files = [SystemFile("idle-{}.bin".format(index), 1000, False) for index in range(500)]
        active_file = SystemFile("active.bin", 1000, False)
        remote_files.append(active_file)
        status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "")
        status.total_transfer_state = LftpJobStatus.TransferState(250, 1000, 25, 50, 15)
        collector = self.__enable_trace()
        self.model_builder.set_remote_files(remote_files)
        self.model_builder.set_lftp_statuses([status])
        self.model_builder.begin_stop_resume_trace_cycle(40)
        model = self.model_builder.build_model()
        initial_count = len(self.__trace_entries(collector))
        model_proxy = MagicMock(wraps=model)
        model_proxy.get_file_ids.side_effect = AssertionError("full model walk is forbidden")

        self.model_builder.begin_stop_resume_trace_cycle(41)
        self.model_builder.finish_stop_resume_trace_cycle(model_proxy, False)

        model_proxy.get_file_ids.assert_not_called()
        model_proxy.get_file.assert_called_once_with("active.bin")
        self.assertEqual(initial_count + 1, len(self.__trace_entries(collector)))

    def test_trace_alternating_events_keep_unchanged_arbitration_coalesced(self):
        remote_file = SystemFile("interleave.bin", 1000, False)
        local_file = SystemFile("interleave.bin", 100, False)
        status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "interleave.bin", "")
        status.total_transfer_state = LftpJobStatus.TransferState(100, 1000, 10, 25, 30)
        collector = self.__enable_trace()
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_lftp_statuses([status])

        self.model_builder.begin_stop_resume_trace_cycle(20)
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        self.assertEqual(1, len(self.__trace_entries(collector)))

        self.model_builder.begin_stop_resume_trace_cycle(21)
        self.model_builder.finish_stop_resume_trace_cycle(model, False)
        self.assertEqual(2, len(self.__trace_entries(collector)))

        self.model_builder.begin_stop_resume_trace_cycle(22)
        self.model_builder.request_rebuild()
        rebuilt_model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(rebuilt_model, True)
        self.assertEqual(2, len(self.__trace_entries(collector)))

    def test_trace_signature_cache_is_bounded_and_clearable(self):
        collector = self.__enable_trace()
        self.model_builder.begin_stop_resume_trace_cycle(30)
        cache_limit = self.model_builder._ModelBuilder__STOP_RESUME_TRACE_SIGNATURE_CACHE_SIZE
        for index in range(cache_limit + 32):
            self.model_builder._ModelBuilder__trace_cycle_event(
                "arbitration",
                {
                    "file_id": "churn-{}".format(index),
                    "model_source": "rebuilt",
                },
            )

        cache = self.model_builder._ModelBuilder__stop_resume_trace_last_signatures
        self.assertEqual(cache_limit, len(cache))
        self.assertNotIn(("churn-0", "arbitration"), cache)
        self.assertIn(("churn-{}".format(cache_limit + 31), "arbitration"), cache)
        self.assertTrue(self.__trace_entries(collector))
        self.model_builder.clear()
        self.assertEqual(0, len(self.model_builder._ModelBuilder__stop_resume_trace_last_signatures))

    def test_trace_metadata_uses_canonical_path_pair_identity(self):
        remote_file = SystemFile("backup.zip", 1000, False)
        remote_file.path_pair_id = "homeserver"
        running_status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "backup.zip", "")
        running_status.path_pair_id = "homeserver"
        running_status.total_transfer_state = LftpJobStatus.TransferState(250, 1000, 25, 50, 15)
        qualified_file_id = ModelFile.build_file_id("backup.zip", "homeserver")

        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_lftp_statuses([running_status])
        collector = self.__enable_trace()
        self.model_builder.begin_stop_resume_trace_cycle(5)
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        metadata = self.model_builder.stop_resume_trace_metadata_for_file(model.get_file(qualified_file_id))
        self.assertEqual(qualified_file_id, metadata["file_id"])
        self.assertIn(qualified_file_id, metadata["corr_id"])
        self.assertTrue(self.__trace_entries(collector))

    def test_target_archive_trace_logs_selected_file_arbitration(self):
        file_name = "archive.zip"
        remote_file = SystemFile(file_name, 1000, False)
        local_file = SystemFile(file_name, 250, False, is_staging=True)
        running_status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(250, 1000, 25, 50, 15)

        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_lftp_statuses([running_status])
        self.model_builder._ModelBuilder__target_archive_trace_file_id = file_name

        trace_logger = self.model_builder._ModelBuilder__target_archive_trace_logger
        with patch.object(trace_logger, "info") as trace_info:
            self.model_builder.build_model()

        self.assertEqual(1, trace_info.call_count)
        payload = json.loads(trace_info.call_args[0][1])
        self.assertEqual("arbitration", payload["event"])
        self.assertEqual(file_name, payload["resolved_identity"]["file_id"])
        self.assertEqual("live_transfer", payload["source_kind"])
        self.assertFalse(payload["markers"]["downloaded"])

    def test_inactive_unrelated_files_are_not_traced(self):
        collector = self.__enable_trace()
        self.model_builder.set_remote_files([
            SystemFile("active.bin", 1000, False),
            SystemFile("idle.bin", 1000, False),
        ])
        status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "")
        status.total_transfer_state = LftpJobStatus.TransferState(250, 1000, 25, 50, 15)
        self.model_builder.set_lftp_statuses([status])
        self.model_builder.begin_stop_resume_trace_cycle(10)
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        entries = self.__trace_entries(collector)
        self.assertEqual({"active.bin"}, {
            entry["file_id"] for entry in entries if entry["file_id"] is not None
        })
        self.__assert_generic_lifecycle_trace_is_private(entries, "active.bin")

    def test_live_status_remains_relevant_after_terminal_reconciliation(self):
        remote_file = SystemFile("done.bin", 100, False)
        local_file = SystemFile("done.bin", 100, False)
        idle_active_scan_file = SystemFile("idle.bin", 100, False)
        status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "done.bin", "")
        status.total_transfer_state = LftpJobStatus.TransferState(100, 100, 100, 0, 0)
        collector = self.__enable_trace()
        self.model_builder.set_remote_files([remote_file, idle_active_scan_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_active_files([idle_active_scan_file])
        self.model_builder.set_lftp_statuses([status])
        self.model_builder.begin_stop_resume_trace_cycle(13)
        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("done.bin").state)
        self.assertIsNotNone(self.model_builder.stop_resume_trace_metadata_for_file(model.get_file("done.bin")))
        self.assertIsNone(self.model_builder.stop_resume_trace_metadata_for_file(model.get_file("idle.bin")))
        entries = self.__trace_entries(collector)
        self.assertEqual({"done.bin"}, {
            entry["file_id"] for entry in entries if entry["file_id"] is not None
        })
        self.__assert_generic_lifecycle_trace_is_private(entries, "done.bin")

    def test_disabled_to_enabled_hot_toggle_captures_without_restart(self):
        collector = self.__enable_trace(False)
        remote_file = SystemFile("hot.bin", 1000, False)
        status = LftpJobStatus(7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "hot.bin", "")
        status.total_transfer_state = LftpJobStatus.TransferState(250, 1000, 25, 50, 15)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_lftp_statuses([status])
        self.model_builder.begin_stop_resume_trace_cycle(11)
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        self.assertEqual([], self.__trace_entries(collector))
        self.__trace_enabled[0] = True
        collector.sync_enabled_state()
        self.model_builder.begin_stop_resume_trace_cycle(12)
        self.model_builder.request_rebuild()
        model = self.model_builder.build_model()
        self.model_builder.finish_stop_resume_trace_cycle(model, True)
        self.assertTrue(self.__trace_entries(collector))

    def test_global_lifecycle_persist_trace_is_hot_gated_private_and_bounded(self):
        """Downloaded/final-move lifecycle evidence needs no scenario selector."""
        subject_name = "private-lifecycle-subject.bin"
        collector = self.__enable_trace(False)
        remote_file = SystemFile(subject_name, 100, False)
        self.model_builder.set_remote_files([remote_file])
        subject_id = ModelFile.build_file_id(subject_name, None)
        self.model_builder.set_downloaded_files({subject_id})

        self.model_builder.build_model()
        self.assertEqual([], self.__trace_entries(collector))

        self.__trace_enabled[0] = True
        collector.sync_enabled_state()
        self.model_builder.request_rebuild()
        candidate = self.model_builder.build_model()
        subject_ids = self.model_builder.record_lifecycle_candidate_publication(candidate, "full_build")
        self.model_builder.record_lifecycle_live_publication(
            candidate, candidate, subject_ids, "full_build", "full_model_adopted",
        )
        entries = self.__trace_entries(collector)
        messages = [entry["message"] for entry in entries]
        self.assertEqual(
            [
                "persist_authority_before",
                "persist_authority_after",
                "model_candidate",
                "model_live_publication",
            ],
            messages,
        )
        before = next(entry for entry in entries if entry["message"] == "persist_authority_before")
        after = next(entry for entry in entries if entry["message"] == "persist_authority_after")
        self.assertEqual("default", before["details"]["pre_state"])
        self.assertEqual("deleted", after["details"]["state_category"])
        self.assertEqual("full_model_adopted", next(
            entry for entry in entries if entry["message"] == "model_live_publication"
        )["details"]["adoption_kind"])
        self.assertTrue(all(entry["corr_id"] and entry["file_id"] is None for entry in entries))
        self.assertNotIn(subject_name, str(entries))
        self.assertNotIn(subject_id, str(entries))

        self.model_builder.request_rebuild()
        self.model_builder.build_model()
        self.assertEqual(len(entries), len(self.__trace_entries(collector)))

        # A fresh global enable clears the process-local dedupe window while
        # retaining the collector's independent bounded retention contract.
        self.model_builder.set_stop_resume_trace_breadcrumb(None)
        self.model_builder.request_rebuild()
        self.model_builder.build_model()
        bounded = BreadcrumbTraceCollector(lambda: True, max_entries=2)
        self.model_builder.set_stop_resume_trace_breadcrumb(bounded)
        self.model_builder.request_rebuild()
        candidate = self.model_builder.build_model()
        subject_ids = self.model_builder.record_lifecycle_candidate_publication(candidate, "full_build")
        self.model_builder.record_lifecycle_live_publication(
            candidate, candidate, subject_ids, "full_build", "full_model_adopted",
        )
        bounded_entries = bounded.snapshot()["entries"]
        self.assertEqual(2, len(bounded_entries))
        self.assertEqual(["model_candidate", "model_live_publication"], [
            entry["message"] for entry in bounded_entries
        ])

        self.model_builder.set_downloaded_files({subject_id})
        empty_candidate = Model()
        missing_subject_ids = self.model_builder.record_lifecycle_candidate_publication(
            empty_candidate, "full_build",
        )
        self.assertEqual({subject_id}, missing_subject_ids)
        missing_entry = self.__trace_entries(bounded)[-1]
        self.assertEqual("model_candidate", missing_entry["message"])
        self.assertEqual("absent", missing_entry["details"]["candidate_state"])
        self.assertNotIn(subject_name, str(missing_entry))
        self.assertNotIn(subject_id, str(missing_entry))

    def test_stable_downloaded_model_requires_explicit_lifecycle_marker_for_persist_trace(self):
        collector = self.__enable_trace()
        remote_files = [
            SystemFile("stable-{}.bin".format(index), 100, False)
            for index in range(256)
        ]
        local_files = [
            SystemFile("stable-{}.bin".format(index), 100, False)
            for index in range(256)
        ]
        self.model_builder.set_remote_files(remote_files)
        self.model_builder.set_local_files(local_files)

        recorder = self.model_builder._ModelBuilder__record_lifecycle_persist_breadcrumb
        with patch.object(
                self.model_builder,
                "_ModelBuilder__record_lifecycle_persist_breadcrumb",
                wraps=recorder,
        ) as record:
            model = self.model_builder.build_model()

        self.assertEqual(256, len(model.get_file_ids()))
        record.assert_not_called()
        self.assertEqual([], [
            entry for entry in self.__trace_entries(collector)
            if entry["message"] in {"persist_authority_before", "persist_authority_after"}
        ])

        subject_id = ModelFile.build_file_id("stable-0.bin", None)
        self.model_builder.set_downloaded_files({subject_id})
        recorder = self.model_builder._ModelBuilder__record_lifecycle_persist_breadcrumb
        with patch.object(
                self.model_builder,
                "_ModelBuilder__record_lifecycle_persist_breadcrumb",
                wraps=recorder,
        ) as record:
            self.model_builder.build_model()

        self.assertEqual(
            ["persist_authority_before", "persist_authority_after"],
            [call.args[0] for call in record.call_args_list],
        )
        entries = self.__trace_entries(collector)
        self.assertEqual(
            {"persist_authority_before", "persist_authority_after"},
            {
                entry["message"] for entry in entries
                if entry["message"] in {"persist_authority_before", "persist_authority_after"}
            },
        )

        final_move_subject_id = ModelFile.build_file_id("stable-1.bin", None)
        self.model_builder.set_downloaded_files(set())
        self.model_builder.set_final_move_succeeded_files({final_move_subject_id})
        recorder = self.model_builder._ModelBuilder__record_lifecycle_persist_breadcrumb
        with patch.object(
                self.model_builder,
                "_ModelBuilder__record_lifecycle_persist_breadcrumb",
                wraps=recorder,
        ) as record:
            self.model_builder.build_model()

        self.assertEqual(
            ["persist_authority_before", "persist_authority_after"],
            [call.args[0] for call in record.call_args_list],
        )
        before_details = record.call_args_list[0].args[2]
        self.assertFalse(before_details["downloaded_marker_present"])
        self.assertTrue(before_details["final_move_marker_present"])

    def test_lifecycle_recorders_do_not_enumerate_marker_sets_while_trace_disabled(self):
        class IterationSentinel:
            def __iter__(self):
                raise AssertionError("disabled lifecycle recorder enumerated markers")

        self.__enable_trace(False)
        self.model_builder._ModelBuilder__downloaded_files = IterationSentinel()
        self.model_builder._ModelBuilder__final_move_succeeded_files = IterationSentinel()

        self.assertEqual(set(), self.model_builder.record_lifecycle_candidate_publication(Model(), "full_build"))
        self.model_builder.record_lifecycle_live_publication(Model(), Model(), {"ignored"}, "full_build", "pending")

    def test_build_model_preserves_validation_status_across_rebuilds(self):
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_validation_statuses([
            ValidateStatus(
                file_id="a",
                state=ModelFile.State.VALIDATED,
                progress=100,
                error=None,
                corrupt_chunks=None
            )
        ])

        first_model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.VALIDATED, first_model.get_file("a").state)

        updated_local = SystemFile("a", 100, False)
        self.model_builder.set_local_files([updated_local])
        rebuilt_model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.VALIDATED, rebuilt_model.get_file("a").state)
        self.assertEqual(100, rebuilt_model.get_file("a").validation_progress)

    def test_build_duplicate_root_names_by_path_pair(self):
        remote_movies = SystemFile("dup", 10, False)
        remote_movies.path_pair_id = "movies"
        remote_movies.path_pair_name = "Movies"
        remote_tv = SystemFile("dup", 20, False)
        remote_tv.path_pair_id = "tv"
        remote_tv.path_pair_name = "TV"

        self.model_builder.set_remote_files([remote_movies, remote_tv])

        model = self.model_builder.build_model()

        self.assertEqual({"dup"}, model.get_file_names())
        self.assertEqual(
            {
                ModelFile.build_file_id("dup", "movies"),
                ModelFile.build_file_id("dup", "tv"),
            },
            model.get_file_ids()
        )
        with self.assertRaises(ModelError):
            model.get_file("dup")
        self.assertEqual(
            "movies",
            model.get_file(ModelFile.build_file_id("dup", "movies")).path_pair_id
        )
        self.assertEqual(
            "tv",
            model.get_file(ModelFile.build_file_id("dup", "tv")).path_pair_id
        )

    def test_build_deduplicates_local_only_roots_in_shared_local_directory(self):
        local_movies = SystemFile("dup", 10, False)
        local_movies.path_pair_id = "movies"
        local_movies.path_pair_name = "Movies"
        local_tv = SystemFile("dup", 20, False)
        local_tv.path_pair_id = "tv"
        local_tv.path_pair_name = "TV"

        self.model_builder.set_local_root_paths(
            {
                "movies": r"C:\seedsync\downloads\shared",
                "tv": r"C:\seedsync\downloads\..\downloads\shared",
            }
        )
        self.model_builder.set_local_files([local_movies, local_tv])

        model = self.model_builder.build_model()

        self.assertEqual({"dup"}, model.get_file_names())
        self.assertEqual(1, len(model.get_file_ids()))
        self.assertIn(model.get_file("dup").path_pair_id, {"movies", "tv"})

    def test_build_deduplicates_local_only_downloaded_roots_in_shared_local_directory(self):
        local_movies = SystemFile("dup", 10, False)
        local_movies.path_pair_id = "movies"
        local_movies.path_pair_name = "Movies"
        local_tv = SystemFile("dup", 20, False)
        local_tv.path_pair_id = "tv"
        local_tv.path_pair_name = "TV"

        self.model_builder.set_local_root_paths(
            {
                "movies": r"C:\seedsync\downloads\shared",
                "tv": r"C:\seedsync\downloads\..\downloads\shared",
            }
        )
        self.model_builder.set_local_files([local_movies, local_tv])
        self.model_builder.set_downloaded_files({ModelFile.build_file_id("dup", "movies")})

        model = self.model_builder.build_model()

        self.assertEqual({"dup"}, model.get_file_names())
        self.assertEqual(1, len(model.get_file_ids()))
        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("dup").state)
        self.assertEqual("movies", model.get_file("dup").path_pair_id)

    def test_build_deduplicates_local_only_extracted_roots_in_shared_local_directory(self):
        local_movies = SystemFile("dup", 10, False)
        local_movies.path_pair_id = "movies"
        local_movies.path_pair_name = "Movies"
        local_tv = SystemFile("dup", 20, False)
        local_tv.path_pair_id = "tv"
        local_tv.path_pair_name = "TV"

        self.model_builder.set_local_root_paths(
            {
                "movies": r"C:\seedsync\downloads\shared",
                "tv": r"C:\seedsync\downloads\..\downloads\shared",
            }
        )
        self.model_builder.set_local_files([local_movies, local_tv])
        self.model_builder.set_downloaded_files({ModelFile.build_file_id("dup", "movies")})
        self.model_builder.set_extracted_files({ModelFile.build_file_id("dup", "movies")})

        model = self.model_builder.build_model()

        self.assertEqual({"dup"}, model.get_file_names())
        self.assertEqual(1, len(model.get_file_ids()))
        self.assertEqual(ModelFile.State.EXTRACTED, model.get_file("dup").state)
        self.assertEqual("movies", model.get_file("dup").path_pair_id)

    def test_build_keeps_local_only_roots_from_distinct_local_directories(self):
        local_movies = SystemFile("dup", 10, False)
        local_movies.path_pair_id = "movies"
        local_movies.path_pair_name = "Movies"
        local_tv = SystemFile("dup", 20, False)
        local_tv.path_pair_id = "tv"
        local_tv.path_pair_name = "TV"

        self.model_builder.set_local_root_paths(
            {
                "movies": r"C:\seedsync\downloads\movies",
                "tv": r"C:\seedsync\downloads\tv",
            }
        )
        self.model_builder.set_local_files([local_movies, local_tv])

        model = self.model_builder.build_model()

        self.assertEqual({"dup"}, model.get_file_names())
        self.assertEqual(
            {
                ModelFile.build_file_id("dup", "movies"),
                ModelFile.build_file_id("dup", "tv"),
            },
            model.get_file_ids()
        )
        with self.assertRaises(ModelError):
            model.get_file("dup")

    def test_build_keeps_managed_entry_over_local_only_twin_in_shared_directory(self):
        remote_movies = SystemFile("dup", 10, False)
        remote_movies.path_pair_id = "movies"
        remote_movies.path_pair_name = "Movies"
        local_movies = SystemFile("dup", 10, False)
        local_movies.path_pair_id = "movies"
        local_movies.path_pair_name = "Movies"
        local_tv = SystemFile("dup", 20, False)
        local_tv.path_pair_id = "tv"
        local_tv.path_pair_name = "TV"

        self.model_builder.set_local_root_paths(
            {
                "movies": r"C:\seedsync\downloads\shared",
                "tv": r"C:\seedsync\downloads\..\downloads\shared",
            }
        )
        self.model_builder.set_remote_files([remote_movies])
        self.model_builder.set_local_files([local_movies, local_tv])

        model = self.model_builder.build_model()

        self.assertEqual({"dup"}, model.get_file_names())
        self.assertEqual({ModelFile.build_file_id("dup", "movies")}, model.get_file_ids())
        self.assertEqual("movies", model.get_file("dup").path_pair_id)

    def test_build_keeps_status_backed_entry_over_local_only_twin_in_shared_directory(self):
        remote_movies = SystemFile("dup", 10, False)
        remote_movies.path_pair_id = "movies"
        remote_movies.path_pair_name = "Movies"
        local_tv = SystemFile("dup", 20, False)
        local_tv.path_pair_id = "tv"
        local_tv.path_pair_name = "TV"

        self.model_builder.set_local_root_paths(
            {
                "movies": r"C:\seedsync\downloads\shared",
                "tv": r"C:\seedsync\downloads\..\downloads\shared",
            }
        )
        self.model_builder.set_remote_files([remote_movies])
        self.model_builder.set_local_files([local_tv])

        for status_state, expected_state in (
            (LftpJobStatus.State.QUEUED, ModelFile.State.QUEUED),
            (LftpJobStatus.State.RUNNING, ModelFile.State.DOWNLOADING),
        ):
            with self.subTest(status_state=status_state):
                self.model_builder.set_lftp_statuses([])
                status_movies = LftpJobStatus(0, LftpJobStatus.Type.PGET, status_state, "dup", "")
                status_movies.path_pair_id = "movies"
                status_movies.path_pair_name = "Movies"
                self.model_builder.set_lftp_statuses([status_movies])

                model = self.model_builder.build_model()

                self.assertEqual({"dup"}, model.get_file_names())
                self.assertEqual({ModelFile.build_file_id("dup", "movies")}, model.get_file_ids())
                self.assertEqual("movies", model.get_file("dup").path_pair_id)
                self.assertEqual(expected_state, model.get_file("dup").state)

    def test_build_children_inherit_path_pair_metadata(self):
        remote_root = SystemFile("dup", 10, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("child", 10, False)
        remote_root.add_child(remote_child)

        self.model_builder.set_remote_files([remote_root])

        model = self.model_builder.build_model()
        built_root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        built_child = built_root.get_children()[0]

        self.assertEqual("movies", built_root.path_pair_id)
        self.assertEqual("Movies", built_root.path_pair_name)
        self.assertEqual("movies", built_child.path_pair_id)
        self.assertEqual("Movies", built_child.path_pair_name)
        self.assertEqual(
            ModelFile.build_file_id(os.path.join("dup", "child"), "movies"),
            built_child.file_id
        )
