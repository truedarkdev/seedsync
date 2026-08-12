# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import logging
from datetime import datetime, timedelta
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from controller import ModelBuilder
from controller.extract import ExtractCompletedResult
from controller.persist_keys import KEY_SEP
from controller.model_updater import (
    ModelUpdater,
    _ProgressiveScanAccumulator,
    _JointProgressiveReconciler,
    _filter_progressive_remote_state,
    _lifecycle_scanned_path_pair_ids,
    _merge_targeted_legacy_scan_files,
    _remote_reconciliation_established,
    _pop_scan_updates,
    _ModelUpdateStageTimer,
    _MoveRetryRebuildGate,
    _request_model_rebuild,
)
from common.performance_diagnostics import (
    DURATION_MODEL_UPDATE_BUILD_FINALIZATION,
    DURATION_MODEL_UPDATE_SCAN_INTAKE,
    DURATION_MODEL_UPDATE_STATE_PREPARATION,
    DURATION_MODEL_UPDATE_LOCK_WAIT,
    DURATION_MODEL_UPDATE_TRACE_FINALIZATION,
    DURATION_MODEL_UPDATE_TRACE_SETUP,
    MODEL_REBUILD_REASON_MOVE_RETRY_DUE,
)
from controller.scan.scanner_process import ScannerProcess, ScannerResult
from lftp import LftpJobStatus
from model import Model, ModelFile
from system import SystemFile


class TestModelUpdater(unittest.TestCase):
    def test_update_attributes_trace_setup_lock_wait_and_finalization(self):
        diagnostics = MagicMock()
        diagnostics.begin_duration.side_effect = lambda metric: (metric,)
        builder = MagicMock()
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(performance_diagnostics=diagnostics),
            _Controller__model_builder=builder,
            _Controller__model=MagicMock(),
            _Controller__work_state_lock=RLock(),
            _Controller__stop_resume_trace_cycle_id=4,
            logger=MagicMock(),
        )
        updater = ModelUpdater(controller)
        updater._update_once = MagicMock(return_value=False)

        updater.update()

        self.assertEqual(5, controller._Controller__stop_resume_trace_cycle_id)
        builder.begin_stop_resume_trace_cycle.assert_called_once_with(5)
        builder.finish_stop_resume_trace_cycle.assert_called_once_with(controller._Controller__model, False)
        self.assertEqual([
            call(DURATION_MODEL_UPDATE_TRACE_SETUP),
            call(DURATION_MODEL_UPDATE_LOCK_WAIT),
            call(DURATION_MODEL_UPDATE_TRACE_FINALIZATION),
        ], diagnostics.begin_duration.call_args_list)
        self.assertEqual([
            call(DURATION_MODEL_UPDATE_TRACE_SETUP, (DURATION_MODEL_UPDATE_TRACE_SETUP,)),
            call(DURATION_MODEL_UPDATE_LOCK_WAIT, (DURATION_MODEL_UPDATE_LOCK_WAIT,)),
            call(DURATION_MODEL_UPDATE_TRACE_FINALIZATION, (DURATION_MODEL_UPDATE_TRACE_FINALIZATION,)),
        ], diagnostics.finish_duration.call_args_list)

    def test_move_retry_rebuild_gate_is_edge_triggered_and_rearms_after_future_due(self):
        gate = _MoveRetryRebuildGate()
        now = datetime.now()
        failure_counts = {"retry": 1}
        retry_due = {}

        self.assertEqual(["retry"], gate.due_ids(failure_counts, retry_due, 4, now))
        self.assertEqual([], gate.due_ids(failure_counts, retry_due, 4, now))

        retry_due["retry"] = now + timedelta(seconds=10)
        self.assertEqual([], gate.due_ids(failure_counts, retry_due, 4, now))
        retry_due["retry"] = now - timedelta(seconds=1)
        self.assertEqual(["retry"], gate.due_ids(failure_counts, retry_due, 4, now))

        failure_counts.clear()
        self.assertEqual([], gate.due_ids(failure_counts, retry_due, 4, now))

    def test_move_retry_gate_detects_due_token_change_without_intermediate_tick(self):
        gate = _MoveRetryRebuildGate()
        now = datetime.now()
        failure_counts = {"retry": 1}
        self.assertEqual(["retry"], gate.due_ids(failure_counts, {}, 4, now))

        # A failed attempt writes a new future due time while the controller
        # is busy.  The next observation is already past that due time.
        retry_due = {"retry": now + timedelta(seconds=10)}
        later = now + timedelta(seconds=20)
        self.assertEqual(["retry"], gate.due_ids(failure_counts, retry_due, 4, later))

    def test_deferred_attempt_stays_retryable_without_repeated_idle_rebuilds(self):
        gate = _MoveRetryRebuildGate()
        now = datetime.now()
        failure_counts = {"retry": 1}
        self.assertEqual(["retry"], gate.due_ids(failure_counts, {}, 4, now))
        gate.record_attempt("retry", consume_budget=False)
        self.assertEqual([], gate.due_ids(failure_counts, {}, 4, now))
        self.assertEqual([], gate.due_ids(failure_counts, {}, 4, now))
        self.assertEqual(["retry"], gate.due_ids(
            failure_counts, {"retry": now - timedelta(seconds=1)}, 4, now
        ))

    def test_move_retry_gate_resets_when_an_identical_marker_is_recreated(self):
        gate = _MoveRetryRebuildGate()
        now = datetime.now()
        failure_counts = {"retry": 1}

        self.assertEqual(["retry"], gate.due_ids(failure_counts, {}, 4, now))
        self.assertEqual([], gate.due_ids(failure_counts, {}, 4, now))

        # The marker can be removed and recreated before the next updater
        # observation.  Explicit lifecycle reset must not suppress the new
        # marker merely because its persisted token is identical.
        gate.reset("retry")
        failure_counts.clear()
        failure_counts["retry"] = 1
        self.assertEqual(["retry"], gate.due_ids(failure_counts, {}, 4, now))

        deferred_ids = {"retry"}
        pending_ids = {"retry"}
        self.assertEqual(["retry"], gate.deferred_recovery_ids(
            failure_counts, {}, deferred_ids, pending_ids, 4, now
        ))
        self.assertEqual([], gate.deferred_recovery_ids(
            failure_counts, {}, deferred_ids, pending_ids, 4, now
        ))
        gate.reset("retry")
        self.assertEqual(["retry"], gate.deferred_recovery_ids(
            failure_counts, {}, deferred_ids, pending_ids, 4, now
        ))

    def test_deferred_recovery_gate_is_edge_triggered_and_rearms_on_token_change(self):
        gate = _MoveRetryRebuildGate()
        now = datetime.now()
        failure_counts = {"retry": 0}
        retry_due = {}
        deferred_ids = {"retry"}
        pending_ids = {"retry"}

        self.assertEqual(["retry"], gate.deferred_recovery_ids(
            failure_counts, retry_due, deferred_ids, pending_ids, 4, now
        ))
        self.assertEqual([], gate.deferred_recovery_ids(
            failure_counts, retry_due, deferred_ids, pending_ids, 4, now
        ))
        failure_counts["retry"] = 1
        self.assertEqual(["retry"], gate.deferred_recovery_ids(
            failure_counts, retry_due, deferred_ids, pending_ids, 4, now
        ))

    def test_rebuild_reason_attribution_is_fixed_and_fail_closed(self):
        builder = MagicMock()
        diagnostics = MagicMock()
        _request_model_rebuild(builder, diagnostics, MODEL_REBUILD_REASON_MOVE_RETRY_DUE)
        diagnostics.increment.assert_called_once_with("model_rebuild_move_retry_due")
        diagnostics.reset_mock()

        _request_model_rebuild(builder, diagnostics, "file:/private/path")
        diagnostics.increment.assert_not_called()

    def test_model_update_stage_timer_switches_fixed_stages_and_closes_on_finish_error(self):
        class Diagnostics:
            def __init__(self):
                self.events = []

            def begin_duration(self, metric):
                self.events.append(("begin", metric))
                return (1.0, 1.0, len(self.events), 0)

            def finish_duration(self, metric, token):
                self.events.append(("finish", metric))
                if metric == DURATION_MODEL_UPDATE_SCAN_INTAKE:
                    raise RuntimeError("diagnostic failure")

        diagnostics = Diagnostics()
        timer = _ModelUpdateStageTimer(diagnostics)
        timer.switch(DURATION_MODEL_UPDATE_STATE_PREPARATION)
        timer.switch(DURATION_MODEL_UPDATE_SCAN_INTAKE)
        timer.switch(DURATION_MODEL_UPDATE_BUILD_FINALIZATION)
        timer.finish()
        self.assertEqual([
            ("begin", DURATION_MODEL_UPDATE_STATE_PREPARATION),
            ("finish", DURATION_MODEL_UPDATE_STATE_PREPARATION),
            ("begin", DURATION_MODEL_UPDATE_SCAN_INTAKE),
            ("finish", DURATION_MODEL_UPDATE_SCAN_INTAKE),
            ("begin", DURATION_MODEL_UPDATE_BUILD_FINALIZATION),
            ("finish", DURATION_MODEL_UPDATE_BUILD_FINALIZATION),
        ], diagnostics.events)

    def test_remote_lifecycle_requires_result_on_staggered_or_no_event_tick(self):
        final_scan = SimpleNamespace(is_scan_final=True, failed=False, unknown_path_pair_ids=set())
        self.assertTrue(_remote_reconciliation_established(final_scan, True))
        self.assertFalse(_remote_reconciliation_established(None, True))
        self.assertFalse(_remote_reconciliation_established(final_scan, False))

    def test_refresh_resets_accumulator_before_first_new_event(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(datetime.now(), [SystemFile("old", 1)], scanned_path_pair_ids={"pair"},
                          generation=1, is_progress=True, session_token="old-session"),
        ])
        controller = SimpleNamespace(_Controller__progressive_remote_scan_state=accumulator)
        replacement = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0)
        self.addCleanup(replacement.close_queues)

        self.assertIsNone(_pop_scan_updates(controller, "remote", replacement))
        self.assertEqual({}, accumulator.snapshot())
    def test_progressive_remote_exclusions_match_legacy_root_and_nested_filtering(self):
        root_skip = SystemFile("skip.nfo", 5)
        root = SystemFile("Series", 100, True)
        sample = SystemFile("Sample", 20, True)
        sample.add_child(SystemFile("sample.mkv", 20))
        season = SystemFile("Season 1", 70, True)
        season.add_child(SystemFile("episode.nfo", 5))
        season.add_child(SystemFile("episode.mkv", 65))
        root.add_child(sample)
        root.add_child(season)
        root.add_child(SystemFile("keep.mkv", 10))
        for file in (root, root_skip):
            file.path_pair_id = "pair"

        snapshot = {
            ("pair", root.name): root,
            ("pair", root_skip.name): root_skip,
        }
        filtered_snapshot, filtered_authority, excluded = _filter_progressive_remote_state(
            snapshot,
            dict(snapshot),
            "*.nfo, Sample/",
        )

        self.assertNotIn(("pair", "skip.nfo"), filtered_snapshot)
        self.assertIn(("pair", "skip.nfo"), excluded)
        filtered_root = filtered_snapshot[("pair", "Series")]
        self.assertEqual(["Season 1", "keep.mkv"], [child.name for child in filtered_root.children])
        self.assertEqual(["episode.mkv"], [child.name for child in filtered_root.children[0].children])
        self.assertEqual(set(), filtered_authority.keys() & excluded)
        reconciler = _JointProgressiveReconciler()
        _, remote_files, unknown = reconciler.reconcile(
            filtered_snapshot, filtered_authority, set(), {"pair"},
            filtered_snapshot, filtered_authority, set(), {"pair"},
            {"pair"}, excluded,
        )
        self.assertEqual(["Series"], [file.name for file in remote_files])
        self.assertNotIn("pair", unknown)

    def test_joint_reconciler_marks_enabled_pairs_unknown_before_first_events(self):
        reconciler = _JointProgressiveReconciler()
        local_files, remote_files, unknown = reconciler.reconcile(
            {}, {}, set(), set(),
            {}, {}, set(), set(),
            {"pair-a", "pair-b"},
        )
        self.assertEqual([], local_files)
        self.assertEqual([], remote_files)
        self.assertEqual({"pair-a", "pair-b"}, unknown)

    def test_joint_reconciler_gates_new_root_until_both_sides_are_authoritative(self):
        reconciler = _JointProgressiveReconciler()
        remote = SystemFile("new.bin", 4)
        key = ("pair", "new.bin")
        local_files, remote_files, unknown = reconciler.reconcile(
            {}, {}, {"pair"}, set(),
            {key: remote}, {key: remote}, set(), {"pair"}, {"pair"},
        )
        self.assertEqual([], local_files)
        self.assertEqual([], remote_files)
        self.assertIn("pair", unknown)

        local_files, remote_files, unknown = reconciler.reconcile(
            {}, {}, set(), {"pair"},
            {key: remote}, {key: remote}, set(), {"pair"}, {"pair"},
        )
        self.assertEqual([], local_files)
        self.assertEqual(["new.bin"], [file.name for file in remote_files])
        self.assertNotIn("pair", unknown)

    def test_joint_reconciler_publishes_each_side_only_after_other_side_absence_or_presence(self):
        reconciler = _JointProgressiveReconciler()
        key = ("pair", "new.bin")
        local = SystemFile("new.bin", 2)
        remote = SystemFile("new.bin", 3)

        _, remote_files, unknown = reconciler.reconcile(
            {key: local}, {key: local}, set(), {"pair"},
            {}, {}, {"pair"}, set(), {"pair"},
        )
        self.assertEqual([], remote_files)
        self.assertIn("pair", unknown)

        local_files, remote_files, unknown = reconciler.reconcile(
            {key: local}, {key: local}, set(), {"pair"},
            {}, {key: None}, set(), {"pair"}, {"pair"},
        )
        self.assertEqual(["new.bin"], [file.name for file in local_files])
        self.assertEqual([], remote_files)
        self.assertNotIn("pair", unknown)

    def test_progressive_accumulator_keeps_unselected_committed_roots(self):
        accumulator = _ProgressiveScanAccumulator()
        first_a = SystemFile("a", 1)
        first_a.path_pair_id = "pair-a"
        first_b = SystemFile("b", 1)
        first_b.path_pair_id = "pair-b"
        second_a = SystemFile("a", 2)
        second_a.path_pair_id = "pair-a"
        accumulator.apply([
            ScannerResult(datetime.now(), [first_a, first_b],
                          scanned_path_pair_ids={"pair-a", "pair-b"}),
        ])
        accumulator.apply([
            ScannerResult(datetime.now(), [second_a], scanned_path_pair_ids={"pair-a"}),
        ])
        snapshot = accumulator.snapshot()
        self.assertEqual(2, snapshot[("pair-a", "a")].size)
        self.assertEqual(1, snapshot[("pair-b", "b")].size)

        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-a"}),
        ])
        snapshot = accumulator.snapshot()
        self.assertNotIn(("pair-a", "a"), snapshot)
        self.assertIn(("pair-b", "b"), snapshot)

    def test_progressive_accumulator_keeps_last_good_roots_when_remote_setup_fails(self):
        accumulator = _ProgressiveScanAccumulator()
        good = SystemFile("last-good.bin", 1)
        good.path_pair_id = "pair"
        accumulator.apply([
            ScannerResult(datetime.now(), [good], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"},
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])

        failed = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                          failed=True, error_message="Connection refused by server",
                          unknown_path_pair_ids={"pair"}, is_progress=True),
        ])

        self.assertTrue(failed.failed)
        self.assertEqual({("pair", "last-good.bin")}, set(accumulator.snapshot()))
        self.assertEqual({"pair"}, accumulator.incomplete_pairs())

        recovered = SystemFile("recovered.bin", 2)
        recovered.path_pair_id = "pair"
        result = accumulator.apply([
            ScannerResult(datetime.now(), [recovered], scanned_path_pair_ids={"pair"}, generation=3,
                          is_progress=True, completed_path_pair_ids={"pair"},
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])

        self.assertFalse(result.failed)
        self.assertEqual({("pair", "recovered.bin")}, set(accumulator.snapshot()))
        self.assertEqual(set(), accumulator.incomplete_pairs())

    def test_progressive_accumulator_commits_healthy_pair_when_another_pair_recovers_with_failure(self):
        accumulator = _ProgressiveScanAccumulator()
        healthy = SystemFile("healthy", 1)
        healthy.path_pair_id = "pair-a"

        result = accumulator.apply([
            ScannerResult(datetime.now(), [healthy], scanned_path_pair_ids={"pair-a"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair-a"},
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"}),
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                          is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"}),
        ])

        self.assertIsNotNone(result)
        self.assertFalse(result.failed)
        self.assertEqual({"pair-a"}, accumulator.completed_pairs())
        self.assertEqual({"pair-b"}, accumulator.incomplete_pairs())
        self.assertEqual({("pair-a", "healthy")}, set(accumulator.snapshot()))

    def test_progressive_accumulator_keeps_lower_generation_full_snapshot_for_untouched_pair(self):
        accumulator = _ProgressiveScanAccumulator()
        pair_b = SystemFile("pair-b-root", 1)
        pair_b.path_pair_id = "pair-b"
        pair_a = SystemFile("pair-a-root", 2)
        pair_a.path_pair_id = "pair-a"

        result = accumulator.apply([
            ScannerResult(datetime.now(), [pair_b], scanned_path_pair_ids={"pair-b"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair-b"},
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-b"}),
            ScannerResult(datetime.now(), [pair_a], scanned_path_pair_ids={"pair-a"}, generation=2,
                          is_progress=True),
        ])

        self.assertIsNotNone(result)
        self.assertEqual({"pair-b"}, accumulator.completed_pairs())
        self.assertEqual({"pair-a"}, accumulator.incomplete_pairs())
        self.assertEqual({("pair-a", "pair-a-root"), ("pair-b", "pair-b-root")},
                         set(accumulator.snapshot()))

    def test_progressive_accumulator_normalizes_legacy_snapshot_in_mixed_targeted_drain(self):
        accumulator = _ProgressiveScanAccumulator()
        legacy_pair_b = SystemFile("legacy-pair-b", 1)
        legacy_pair_b.path_pair_id = "pair-b"
        targeted_pair_a = SystemFile("targeted-pair-a", 2)
        targeted_pair_a.path_pair_id = "pair-a"

        result = accumulator.apply([
            ScannerResult(datetime.now(), [legacy_pair_b], scanned_path_pair_ids={"pair-b"}, generation=1),
            ScannerResult(datetime.now(), [targeted_pair_a], scanned_path_pair_ids={"pair-a"}, generation=2,
                          is_progress=True, is_targeted_scan=True),
        ])

        self.assertIsNotNone(result)
        self.assertEqual({"pair-b"}, accumulator.completed_pairs())
        self.assertEqual({"pair-a"}, accumulator.incomplete_pairs())
        self.assertEqual(
            {("pair-a", "targeted-pair-a"), ("pair-b", "legacy-pair-b")},
            set(accumulator.snapshot()),
        )

    def test_joint_reconciler_keeps_last_good_remote_root_unknown_during_setup_outage(self):
        remote = _ProgressiveScanAccumulator()
        local = _ProgressiveScanAccumulator()
        remote_root = SystemFile("last-good.bin", 1)
        remote_root.path_pair_id = "pair"
        local_root = SystemFile("last-good.bin", 1)
        local_root.path_pair_id = "pair"
        remote.apply([ScannerResult(datetime.now(), [remote_root], scanned_path_pair_ids={"pair"},
                                    generation=1, is_progress=True, completed_path_pair_ids={"pair"},
                                    is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"})])
        local.apply([ScannerResult(datetime.now(), [local_root], scanned_path_pair_ids={"pair"},
                                   generation=1, is_progress=True, completed_path_pair_ids={"pair"},
                                   is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"})])
        reconciler = _JointProgressiveReconciler()
        first = reconciler.reconcile(
            local.snapshot(), local.authority(), local.incomplete_pairs(), local.completed_pairs(),
            remote.snapshot(), remote.authority(), remote.incomplete_pairs(), remote.completed_pairs(),
            {"pair"},
        )
        self.assertEqual(["last-good.bin"], [file.name for file in first[1]])

        remote.apply([ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                                    failed=True, is_progress=True,
                                    unknown_path_pair_ids={"pair"})])
        outage = reconciler.reconcile(
            local.snapshot(), local.authority(), local.incomplete_pairs(), local.completed_pairs(),
            remote.snapshot(), remote.authority(), remote.incomplete_pairs(), remote.completed_pairs(),
            {"pair"},
        )
        self.assertEqual(["last-good.bin"], [file.name for file in outage[1]])
        self.assertIn("pair", outage[2])

    def test_joint_reconciler_excludes_disabled_pairs_from_live_output(self):
        reconciler = _JointProgressiveReconciler()
        root = SystemFile("old.bin", 1)
        key = ("pair", root.name)
        first = reconciler.reconcile(
            {key: root}, {key: root}, set(), {"pair"},
            {key: root}, {key: root}, set(), {"pair"}, {"pair"},
        )
        self.assertEqual(["old.bin"], [file.name for file in first[1]])
        disabled = reconciler.reconcile(
            {key: root}, {key: root}, set(), {"pair"},
            {key: root}, {key: root}, set(), {"pair"}, set(),
        )
        self.assertEqual([], disabled[0])
        self.assertEqual([], disabled[1])
        reenabled = reconciler.reconcile(
            {key: root}, {key: root}, set(), {"pair"},
            {key: root}, {key: root}, set(), {"pair"}, {"pair"},
        )
        self.assertEqual(["old.bin"], [file.name for file in reenabled[1]])

    def test_progressive_completion_waits_for_late_matching_root(self):
        local_accumulator = _ProgressiveScanAccumulator()
        remote_accumulator = _ProgressiveScanAccumulator()
        local_root = SystemFile("late.bin", 2)
        remote_root = SystemFile("late.bin", 3)
        local_partial = local_accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, root_names={"late.bin"}, session_token="local"),
        ])
        remote_final = remote_accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, root_names={"late.bin"}, session_token="remote"),
            ScannerResult(datetime.now(), [remote_root], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, session_token="remote"),
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="remote"),
            ScannerResult(datetime.now(), [remote_root], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="remote",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])
        self.assertFalse(local_partial.is_scan_final)
        self.assertTrue(remote_final.is_scan_final)

        reconciler = _JointProgressiveReconciler()
        local_files, remote_files, unknown = reconciler.reconcile(
            local_accumulator.snapshot(), local_accumulator.authority(),
            local_accumulator.incomplete_pairs(), local_accumulator.completed_pairs(),
            remote_accumulator.snapshot(), remote_accumulator.authority(),
            remote_accumulator.incomplete_pairs(), remote_accumulator.completed_pairs(),
            {"pair"},
        )
        self.assertEqual([], remote_files)
        self.assertIn("pair", unknown)

        local_final = local_accumulator.apply([
            ScannerResult(datetime.now(), [local_root], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, session_token="local"),
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="local"),
            ScannerResult(datetime.now(), [local_root], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="local",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])
        self.assertTrue(local_final.is_scan_final)
        local_files, remote_files, unknown = reconciler.reconcile(
            local_accumulator.snapshot(), local_accumulator.authority(),
            local_accumulator.incomplete_pairs(), local_accumulator.completed_pairs(),
            remote_accumulator.snapshot(), remote_accumulator.authority(),
            remote_accumulator.incomplete_pairs(), remote_accumulator.completed_pairs(),
            {"pair"},
        )
        self.assertEqual(["late.bin"], [file.name for file in local_files])
        self.assertEqual(["late.bin"], [file.name for file in remote_files])
        self.assertNotIn("pair", unknown)

    def test_matching_partial_roots_stream_before_completion_with_unknown_pair(self):
        local_accumulator = _ProgressiveScanAccumulator()
        remote_accumulator = _ProgressiveScanAccumulator()
        local_root = SystemFile("stream.bin", 2)
        local_root.path_pair_id = "pair"
        remote_root = SystemFile("stream.bin", 3)
        remote_root.path_pair_id = "pair"
        local_accumulator.apply([
            ScannerResult(
                datetime.now(), [local_root], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, session_token="local",
            ),
        ])
        remote_accumulator.apply([
            ScannerResult(
                datetime.now(), [remote_root], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, session_token="remote",
            ),
        ])

        local_files, remote_files, unknown = _JointProgressiveReconciler().reconcile(
            local_accumulator.snapshot(), local_accumulator.authority(),
            local_accumulator.incomplete_pairs(), local_accumulator.completed_pairs(),
            remote_accumulator.snapshot(), remote_accumulator.authority(),
            remote_accumulator.incomplete_pairs(), remote_accumulator.completed_pairs(),
            {"pair"},
        )

        self.assertEqual(["stream.bin"], [file.name for file in local_files])
        self.assertEqual(["stream.bin"], [file.name for file in remote_files])
        self.assertEqual({"pair"}, unknown)

    def test_remote_refresh_reuses_standing_local_authority(self):
        """Remote refreshes must not wait for the intentionally slower local scan."""
        local_accumulator = _ProgressiveScanAccumulator()
        remote_accumulator = _ProgressiveScanAccumulator()
        local_root = SystemFile("standing.bin", 2)
        remote_root = SystemFile("standing.bin", 3)

        local_accumulator.apply([
            ScannerResult(
                datetime.now(), [local_root], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="local",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        remote_accumulator.apply([
            ScannerResult(
                datetime.now(), [remote_root], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="remote",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        reconciler = _JointProgressiveReconciler()
        reconciler.reconcile(
            local_accumulator.snapshot(), local_accumulator.authority(),
            local_accumulator.incomplete_pairs(), local_accumulator.completed_pairs(),
            remote_accumulator.snapshot(), remote_accumulator.authority(),
            remote_accumulator.incomplete_pairs(), remote_accumulator.completed_pairs(),
            {"pair"},
        )

        refreshed_remote = SystemFile("standing.bin", 4)
        remote_accumulator.apply([
            ScannerResult(
                datetime.now(), [refreshed_remote], scanned_path_pair_ids={"pair"}, generation=2,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="remote",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        local_files, remote_files, unknown = reconciler.reconcile(
            local_accumulator.snapshot(), local_accumulator.authority(),
            local_accumulator.incomplete_pairs(), local_accumulator.completed_pairs(),
            remote_accumulator.snapshot(), remote_accumulator.authority(),
            remote_accumulator.incomplete_pairs(), remote_accumulator.completed_pairs(),
            {"pair"},
        )

        self.assertEqual(["standing.bin"], [file.name for file in local_files])
        self.assertEqual([4], [file.size for file in remote_files])
        self.assertEqual(set(), unknown)

    def test_joint_session_refresh_keeps_last_good_output_unknown_until_both_sides_refresh(self):
        local_accumulator = _ProgressiveScanAccumulator()
        remote_accumulator = _ProgressiveScanAccumulator()
        old_local = SystemFile("old.bin", 2)
        old_local.path_pair_id = "pair"
        old_remote = SystemFile("old.bin", 3)
        old_remote.path_pair_id = "pair"
        local_accumulator.apply([
            ScannerResult(
                datetime.now(), [old_local], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="local-a",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        remote_accumulator.apply([
            ScannerResult(
                datetime.now(), [old_remote], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="remote-a",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        reconciler = _JointProgressiveReconciler()
        reconciler.reconcile(
            local_accumulator.snapshot(), local_accumulator.authority(),
            local_accumulator.incomplete_pairs(), local_accumulator.completed_pairs(),
            remote_accumulator.snapshot(), remote_accumulator.authority(),
            remote_accumulator.incomplete_pairs(), remote_accumulator.completed_pairs(),
            {"pair"},
        )

        local_accumulator.set_session_token("local-b")
        remote_accumulator.set_session_token("remote-b")
        new_remote = SystemFile("new.bin", 4)
        new_remote.path_pair_id = "pair"
        remote_accumulator.apply([
            ScannerResult(
                datetime.now(), [new_remote], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="remote-b",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        local_files, remote_files, unknown = reconciler.reconcile(
            local_accumulator.snapshot(), local_accumulator.authority(),
            local_accumulator.incomplete_pairs(), local_accumulator.completed_pairs(),
            remote_accumulator.snapshot(), remote_accumulator.authority(),
            remote_accumulator.incomplete_pairs(), remote_accumulator.completed_pairs(),
            {"pair"},
        )
        self.assertEqual(["old.bin"], [file.name for file in local_files])
        self.assertEqual(["old.bin"], [file.name for file in remote_files])
        self.assertEqual({"pair"}, unknown)

        new_local = SystemFile("new.bin", 5)
        new_local.path_pair_id = "pair"
        local_accumulator.apply([
            ScannerResult(
                datetime.now(), [new_local], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="local-b",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        local_files, remote_files, unknown = reconciler.reconcile(
            local_accumulator.snapshot(), local_accumulator.authority(),
            local_accumulator.incomplete_pairs(), local_accumulator.completed_pairs(),
            remote_accumulator.snapshot(), remote_accumulator.authority(),
            remote_accumulator.incomplete_pairs(), remote_accumulator.completed_pairs(),
            {"pair"},
        )
        self.assertEqual(["new.bin"], [file.name for file in local_files])
        self.assertEqual(["new.bin"], [file.name for file in remote_files])
        self.assertEqual(set(), unknown)

    def test_lossy_progress_completion_never_proves_marker_backed_local_absence_before_full_snapshot(self):
        """A bounded queue may retain completion before its authoritative aggregate."""
        local = _ProgressiveScanAccumulator()
        remote = _ProgressiveScanAccumulator()
        reconciler = _JointProgressiveReconciler()
        marker_id = ModelFile.build_file_id("movie.mkv", "pair")

        def root(name, size):
            system_file = SystemFile(name, size)
            system_file.path_pair_id = "pair"
            return system_file

        local_movie = root("movie.mkv", 100)
        remote_movie = root("movie.mkv", 100)
        local.apply([
            ScannerResult(datetime.now(), [local_movie], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="local",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])
        remote.apply([
            ScannerResult(datetime.now(), [remote_movie], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="remote",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])

        def current_movie_state():
            local_files, remote_files, unknown_pairs = reconciler.reconcile(
                local.snapshot(), local.authority(), local.incomplete_pairs(), local.completed_pairs(),
                remote.snapshot(), remote.authority(), remote.incomplete_pairs(), remote.completed_pairs(),
                {"pair"},
            )
            builder = ModelBuilder()
            builder.set_local_files(local_files)
            builder.set_remote_files(remote_files)
            builder.set_downloaded_files({marker_id})
            builder.set_unknown_local_path_pair_ids(unknown_pairs)
            return builder.build_model().get_file(marker_id).state, unknown_pairs

        state, unknown_pairs = current_movie_state()
        self.assertNotEqual(ModelFile.State.DELETED, state)
        self.assertEqual(set(), unknown_pairs)

        # This mimics a slow parent drain: 129 root batches mean the bounded
        # 128-event progress queue can evict arbitrary earlier batches, while
        # the completion marker still precedes the lossless aggregate.
        manifest = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
            is_progress=True, root_names={"root-{}".format(index) for index in range(129)},
            session_token="local",
        )
        local.apply([manifest])
        state, unknown_pairs = current_movie_state()
        self.assertNotEqual(ModelFile.State.DELETED, state)
        self.assertIn("pair", unknown_pairs)

        for index in range(129):
            local.apply([
                ScannerResult(datetime.now(), [root("root-{}".format(index), index)],
                              scanned_path_pair_ids={"pair"}, generation=2,
                              is_progress=True, session_token="local"),
            ])
            state, unknown_pairs = current_movie_state()
            self.assertNotEqual(ModelFile.State.DELETED, state)
            self.assertIn("pair", unknown_pairs)

        completion = local.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="local"),
        ])
        self.assertIsNotNone(completion)
        self.assertFalse(completion.is_scan_final)
        self.assertNotIn("pair", local.completed_pairs())
        state, unknown_pairs = current_movie_state()
        self.assertNotEqual(ModelFile.State.DELETED, state)
        self.assertIn("pair", unknown_pairs)

        # Only this lossless aggregate is allowed to establish the local
        # absence, after which the canonical downloaded marker may be Deleted.
        final = local.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="local",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])
        self.assertIsNotNone(final)
        self.assertTrue(final.is_scan_final)
        self.assertEqual({"pair"}, local.completed_pairs())
        state, unknown_pairs = current_movie_state()
        self.assertEqual(ModelFile.State.DELETED, state)
        self.assertEqual(set(), unknown_pairs)

    def test_progressive_accumulator_keeps_unknown_pair_across_empty_drain(self):
        accumulator = _ProgressiveScanAccumulator()
        root = SystemFile("a", 1)
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, root_names={"a"}),
            ScannerResult(datetime.now(), [root], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True),
        ])
        self.assertEqual({"pair"}, accumulator.incomplete_pairs())
        self.assertIsNone(accumulator.apply([]))
        self.assertEqual({"pair"}, accumulator.incomplete_pairs())

    def test_full_snapshot_completion_rebuilds_roots_after_dropped_progress(self):
        accumulator = _ProgressiveScanAccumulator()
        old = SystemFile("old", 1)
        keep = SystemFile("keep", 2)
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, root_names={"old", "keep"}, session_token="session"),
            ScannerResult(datetime.now(), [old, keep], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, session_token="session"),
            ScannerResult(datetime.now(), [old, keep], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])
        final_keep = SystemFile("keep", 3)
        result = accumulator.apply([
            ScannerResult(datetime.now(), [final_keep], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])

        self.assertIsNotNone(result)
        self.assertEqual({("pair", "keep")}, set(accumulator.snapshot()))
        self.assertEqual(3, accumulator.snapshot()[("pair", "keep")].size)
        self.assertEqual({"pair"}, accumulator.completed_pairs())
        self.assertEqual(set(), accumulator.incomplete_pairs())

    def test_managed_extract_pruned_root_survives_incomplete_then_is_removed_on_full_completion(self):
        accumulator = _ProgressiveScanAccumulator()
        old_root = SystemFile("movie", 10, True)
        accumulator.apply([
            ScannerResult(datetime.now(), [old_root], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True, root_names={"movie"}, session_token="session"),
        ])
        self.assertIn(("pair", "movie"), accumulator.snapshot())

        managed_result = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
                          managed_extract_file_ids=[ModelFile.build_file_id("movie", "pair")]),
        ])
        self.assertEqual([ModelFile.build_file_id("movie", "pair")], managed_result.managed_extract_file_ids)
        self.assertNotIn(("pair", "movie"), accumulator.snapshot())

    def test_progressive_accumulator_resets_active_state_on_session_replacement(self):
        accumulator = _ProgressiveScanAccumulator()
        old = SystemFile("old", 1)
        fresh = SystemFile("fresh", 2)
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, root_names={"old"}, session_token="session-a"),
            ScannerResult(datetime.now(), [old], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, session_token="session-a"),
        ])
        accumulator.set_session_token("session-b")
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, root_names={"fresh"}, session_token="session-b"),
            ScannerResult(datetime.now(), [fresh], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, session_token="session-b"),
        ])
        self.assertEqual({"fresh"}, {key[1] for key in accumulator.snapshot()})

    def test_progressive_accumulator_ignores_mixed_session_events(self):
        accumulator = _ProgressiveScanAccumulator()
        old = SystemFile("old", 1)
        current = SystemFile("current", 2)
        accumulator.set_session_token("current-session")
        result = accumulator.apply([
            ScannerResult(datetime.now(), [old], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True, session_token="old-session"),
            ScannerResult(datetime.now(), [current], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, session_token="current-session"),
        ])
        self.assertIsNotNone(result)
        self.assertEqual({"current"}, {file.name for file in result.files})

    def test_progressive_accumulator_uses_first_session_event_order(self):
        accumulator = _ProgressiveScanAccumulator()
        first = SystemFile("first", 1)
        second = SystemFile("second", 2)
        result = accumulator.apply([
            ScannerResult(datetime.now(), [first], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, session_token="first-session"),
            ScannerResult(datetime.now(), [second], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, session_token="second-session"),
        ])
        self.assertIsNotNone(result)
        self.assertEqual({"first"}, {file.name for file in result.files})

    def test_progressive_accumulator_rejects_stale_generation(self):
        accumulator = _ProgressiveScanAccumulator()
        first = SystemFile("a", 1)
        newer = SystemFile("a", 2)
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, root_names={"a"}),
            ScannerResult(datetime.now(), [first], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True),
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair"}),
        ])
        latest = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True, root_names={"a"}),
            ScannerResult(datetime.now(), [newer], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True),
        ])
        self.assertIsNotNone(latest)
        self.assertEqual(2, latest.files[0].size)

        stale = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          is_progress=True, root_names=set(), completed_path_pair_ids={"pair"}),
        ])
        self.assertIsNotNone(stale)
        self.assertEqual(2, stale.files[0].size)

    def test_progressive_accumulator_rejects_stale_full_snapshot(self):
        accumulator = _ProgressiveScanAccumulator()
        current = SystemFile("current", 2)
        accumulator.apply([
            ScannerResult(datetime.now(), [current], scanned_path_pair_ids={"pair"}, generation=2,
                          is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"}),
        ])
        stale = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                          session_token="session"),
        ])
        self.assertIsNone(stale)
        self.assertEqual({("pair", "current")}, set(accumulator.snapshot()))

    def test_progressive_accumulator_treats_empty_targeted_scan_as_noop(self):
        accumulator = _ProgressiveScanAccumulator()
        current = SystemFile("current", 2)
        accumulator.apply([
            ScannerResult(datetime.now(), [current], scanned_path_pair_ids={None}, generation=1,
                          session_token="session"),
        ])
        result = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids=set(), generation=2,
                          is_targeted_scan=True, session_token="session"),
        ])
        self.assertIsNone(result)
        self.assertEqual({(None, "current")}, set(accumulator.snapshot()))

    def test_targeted_legacy_scan_keeps_unselected_model_roots_and_scopes_lifecycle(self):
        controller = SimpleNamespace()
        root_a = SystemFile("root-a", 1)
        root_a.path_pair_id = "pair-a"
        root_b = SystemFile("root-b", 2)
        root_b.path_pair_id = "pair-b"
        replacement_a = SystemFile("root-a-new", 3)
        replacement_a.path_pair_id = "pair-a"
        full_result = ScannerResult(
            datetime.now(), [root_a, root_b], scanned_path_pair_ids={"pair-a", "pair-b"},
        )
        targeted_result = ScannerResult(
            datetime.now(), [replacement_a], scanned_path_pair_ids={"pair-a"},
            is_targeted_scan=True,
        )

        initial = _merge_targeted_legacy_scan_files(controller, "remote", full_result, full_result.files)
        merged = _merge_targeted_legacy_scan_files(
            controller, "remote", targeted_result, targeted_result.files,
        )
        builder = ModelBuilder()
        builder.set_remote_files(initial)
        builder.set_remote_files(merged)

        self.assertEqual({"root-a-new", "root-b"}, builder.build_model().get_file_names())
        self.assertEqual(
            {"pair-a"},
            _lifecycle_scanned_path_pair_ids(targeted_result, {"pair-a", "pair-b"}),
        )

    def test_unknown_local_scan_does_not_create_false_deleted_state(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("a", 100)])
        builder.set_downloaded_files({"a"})
        builder.set_unknown_local_path_pair_ids({None})

        model = builder.build_model()

        self.assertNotEqual(ModelFile.State.DELETED, model.get_file("a").state)

    def _make_controller(self, downloaded_file_names, extracted_file_names, stopped_file_names, path_pairs_by_id=None):
        persist = SimpleNamespace(
            downloaded_file_names=downloaded_file_names,
            extracted_file_names=extracted_file_names,
            stopped_file_names=stopped_file_names,
        )
        model_builder = MagicMock()
        controller = SimpleNamespace(
            _Controller__persist=persist,
            _Controller__model_builder=model_builder,
        )
        if path_pairs_by_id is not None:
            controller._Controller__path_pairs_by_id = path_pairs_by_id
        return controller, model_builder

    def _make_progressive_update_controller(
            self, remote_scan, local_scan=None, *, authoritative=True,
            downloaded_file_names=None, downloaded_timestamps=None,
            model_builder=None, model=None):
        """Build the narrow controller boundary needed by progressive update tests."""
        persist = SimpleNamespace(
            downloaded_file_names=set(downloaded_file_names or set()),
            downloaded_timestamps=dict(downloaded_timestamps or {}),
            extracted_file_names=set(),
            stopped_file_names=set(),
            move_failure_counts={},
            final_move_succeeded_file_names=set(),
        )
        if model_builder is None:
            model_builder = MagicMock()
            model_builder.has_changes.return_value = False
            model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()
            model_builder.get_unresolved_staging_collision_file_ids.return_value = set()
        if model is None:
            model = MagicMock()
            model.get_file_ids.return_value = {"root"}
            model.get_file_names.return_value = {"root"}
        status = SimpleNamespace(
            latest_remote_scan_time=None,
            latest_remote_scan_failed=False,
            latest_remote_scan_error=None,
            latest_local_scan_time=None,
        )
        controller = SimpleNamespace(
            _Controller__persist=persist,
            _Controller__model_builder=model_builder,
            _Controller__model=model,
            _Controller__model_lock=RLock(),
            _Controller__remote_scan_process=MagicMock(),
            _Controller__local_scan_process=MagicMock(),
            _Controller__active_scan_process=MagicMock(),
            _Controller__extract_process=MagicMock(),
            _Controller__validate_process=MagicMock(),
            _Controller__lftp=MagicMock(),
            _Controller__context=SimpleNamespace(
                config=SimpleNamespace(general=SimpleNamespace(exclude_patterns="")),
                status=SimpleNamespace(controller=status, server=SimpleNamespace()),
            ),
            logger=MagicMock(),
            _Controller__temp_diag=MagicMock(),
            _Controller__set_active_scanner_files=MagicMock(),
            _Controller__record_breadcrumb=MagicMock(),
            _Controller__trace_corr_id_from_files=MagicMock(return_value="progressive-test"),
            _Controller__startup_recovery_done=True,
            _Controller__pending_completion_file_names=set(),
            _Controller__prev_downloading_file_names=set(),
            _Controller__malformed_status_only_file_ids=set(),
            _Controller__pending_auto_purge_file_ids=set(),
            _Controller__last_lftp_statuses=[],
            _Controller__active_downloading_file_names=[],
            _Controller__active_extracting_file_names=[],
            _Controller__next_lftp_status_poll_at=None,
            _Controller__lftp_status_poll_retry_seconds=1,
            _Controller__lftp_status_cache_expires_at=None,
            _Controller__lftp_status_cache_max_age_seconds=3,
            _Controller__lftp_status_poll_retry_active=False,
            _Controller__exclude_patterns="",
            _Controller__last_remote_reconciliation_healthy=False,
            _Controller__last_local_reconciliation_healthy=False,
            _Controller__reconciled_local_path_pair_ids=set(),
            _Controller__reconciled_remote_path_pair_ids=set(),
            _Controller__path_pairs_by_id={},
            _Controller__progressive_joint_authoritative=authoritative,
            _Controller__MAX_MOVE_FAILURES=4,
            _Controller__MOVE_RETRY_DELAYS=(1, 2, 3, 4),
            _Controller__get_path_pair=MagicMock(return_value=None),
            _Controller__is_target_archive_trace_enabled=MagicMock(return_value=False),
            _Controller__find_target_archive_model_file=MagicMock(return_value=None),
            _Controller__should_auto_purge_local_file=MagicMock(return_value=False),
            _sync_final_move_succeeded_files_to_model=MagicMock(),
        )
        def record_reconciliation(local_ids, remote_ids):
            if local_ids is not None:
                controller._Controller__reconciled_local_path_pair_ids = set(local_ids)
            if remote_ids is not None:
                controller._Controller__reconciled_remote_path_pair_ids = set(remote_ids)
        controller._record_path_pair_reconciliation = record_reconciliation
        controller._Controller__remote_scan_process.pop_latest_result.return_value = remote_scan
        controller._Controller__local_scan_process.pop_latest_result.return_value = local_scan
        controller._Controller__active_scan_process.pop_latest_result.return_value = None
        controller._Controller__extract_process.pop_latest_statuses.return_value = None
        controller._Controller__extract_process.pop_completed.return_value = []
        controller._Controller__extract_process.pop_failed.return_value = []
        controller._Controller__validate_process.pop_latest_statuses.return_value = None
        controller._Controller__lftp.status.return_value = []
        controller._Controller__lftp.last_status_poll_healthy = True
        return controller, model_builder

    @staticmethod
    def _progressive_result(name="root", size=1, *, final=False, unknown=None):
        completed = {None} if final else set()
        return ScannerResult(
            datetime.now(),
            [SystemFile(name, size)],
            scanned_path_pair_ids={None},
            is_progress=True,
            completed_path_pair_ids=completed,
            is_scan_final=final,
            unknown_path_pair_ids=set(unknown or set()),
        )

    @staticmethod
    def _progressive_process(session_token, result_batches):
        process = ScannerProcess(
            scanner=SimpleNamespace(), interval_in_ms=0, verbose=False,
        )
        process._ScannerProcess__session_token = session_token
        process.pop_results = MagicMock(side_effect=result_batches)
        return process

    @staticmethod
    def _progressive_final_result(session_token, size=1, generation=1):
        return ScannerResult(
            datetime.now(),
            [SystemFile("root", size)],
            scanned_path_pair_ids={None},
            generation=generation,
            is_progress=True,
            completed_path_pair_ids={None},
            is_scan_final=True,
            session_token=session_token,
            is_full_snapshot=True,
            full_snapshot_path_pair_ids={None},
        )

    def test_authoritative_progressive_no_event_ticks_skip_joint_builder_inputs(self):
        remote_token = "remote-no-event"
        local_token = "local-no-event"
        initial_remote = self._progressive_final_result(remote_token)
        initial_local = self._progressive_final_result(local_token)
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], [], []],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], [], []],
        )
        updater = ModelUpdater(controller)

        updater.update()
        self.assertTrue(controller._Controller__progressive_joint_authoritative)
        self.assertEqual({None}, controller._Controller__reconciled_local_path_pair_ids)
        self.assertEqual({None}, controller._Controller__reconciled_remote_path_pair_ids)
        model_builder.reset_mock()

        updater.update()
        updater.update()

        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()
        model_builder.build_model.assert_not_called()

    def test_later_progressive_final_event_republishes_against_standing_authority(self):
        remote_token = "remote-later-final"
        local_token = "local-later-final"
        initial_remote = self._progressive_final_result(remote_token, size=1)
        initial_local = self._progressive_final_result(local_token, size=1)
        later_remote = self._progressive_final_result(remote_token, size=2, generation=2)
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], [], [later_remote]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], [], []],
        )
        updater = ModelUpdater(controller)

        updater.update()
        model_builder.reset_mock()
        updater.update()
        model_builder.reset_mock()

        updater.update()

        model_builder.set_local_files.assert_called_once()
        model_builder.set_remote_files.assert_called_once()
        self.assertEqual(1, model_builder.set_local_files.call_args.args[0][0].size)
        self.assertEqual(2, model_builder.set_remote_files.call_args.args[0][0].size)

    def test_authoritative_progressive_no_event_tick_keeps_active_lftp_status_updates(self):
        remote_token = "remote-lftp-cadence"
        local_token = "local-lftp-cadence"
        initial_remote = self._progressive_final_result(remote_token)
        initial_local = self._progressive_final_result(local_token)
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], []],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], []],
        )
        status = LftpJobStatus(
            1,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            "root",
            "",
        )
        controller._Controller__lftp.status.return_value = [status]
        updater = ModelUpdater(controller)

        updater.update()
        model_builder.reset_mock()
        controller._Controller__next_lftp_status_poll_at = None
        updater.update()

        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()
        model_builder.set_lftp_statuses.assert_called_once_with([status])

    def test_partial_progressive_refresh_after_baseline_does_not_rebuild_or_churn_markers(self):
        partial = self._progressive_result(final=False, unknown={None})
        controller, model_builder = self._make_progressive_update_controller(
            partial,
            downloaded_file_names={"root"},
            downloaded_timestamps={"root": 1.0},
        )
        updater = ModelUpdater(controller)

        updater.update()
        updater.update()

        model_builder.build_model.assert_not_called()
        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()
        model_builder.set_unknown_local_path_pair_ids.assert_not_called()
        model_builder.set_downloaded_files.assert_not_called()
        model_builder.set_downloaded_timestamps.assert_not_called()

    def test_first_progressive_baseline_streams_through_root_delta_builder(self):
        partial = self._progressive_result(final=False, unknown={None})
        controller, model_builder = self._make_progressive_update_controller(
            partial,
            local_scan=partial,
            authoritative=False,
        )

        ModelUpdater(controller).update()

        model_builder.build_progressive_roots.assert_called_once_with(
            partial.files,
            partial.files,
            {None},
        )
        model_builder.set_remote_files.assert_not_called()
        model_builder.set_local_files.assert_not_called()
        model_builder.set_unknown_local_path_pair_ids.assert_not_called()
        model_builder.build_model.assert_not_called()

    def test_partial_progressive_delta_does_not_mask_pending_full_builder_change(self):
        partial = self._progressive_result(final=False, unknown={None})
        controller, model_builder = self._make_progressive_update_controller(
            partial,
            local_scan=partial,
            authoritative=False,
        )
        model_builder.has_changes.return_value = True

        ModelUpdater(controller).update()

        model_builder.build_progressive_roots.assert_not_called()
        model_builder.build_model.assert_called_once_with()

    def test_progressive_startup_publishes_each_root_wave_before_one_final_build(self):
        def result(files, *, final=False):
            return ScannerResult(
                datetime.now(), files,
                scanned_path_pair_ids={None},
                generation=1,
                is_progress=True,
                completed_path_pair_ids={None} if final else set(),
                is_scan_final=final,
                unknown_path_pair_ids=set() if final else {None},
                is_full_snapshot=final,
                full_snapshot_path_pair_ids={None} if final else set(),
            )

        first_remote = SystemFile("first.bin", 10)
        first_local = SystemFile("first.bin", 5)
        second_remote = SystemFile("second.bin", 20)
        second_local = SystemFile("second.bin", 7)
        final_remote = [first_remote, second_remote]
        final_local = [first_local, second_local]
        builder = ModelBuilder()
        model = Model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
            model_builder=builder, model=model,
        )
        controller._Controller__remote_scan_process.pop_latest_result.side_effect = [
            result([first_remote]), result([second_remote]), result(final_remote, final=True),
        ]
        controller._Controller__local_scan_process.pop_latest_result.side_effect = [
            result([first_local]), result([second_local]), result(final_local, final=True),
        ]
        initial_model = builder.build_model()
        builder.adopt_applied_model(initial_model, model)
        original_delta_builder = builder.build_progressive_roots
        original_full_builder = builder.build_model
        builder.build_progressive_roots = MagicMock(wraps=original_delta_builder)
        builder.build_model = MagicMock(wraps=original_full_builder)
        controller._refresh_model_file_command_identities_locked = MagicMock()
        updater = ModelUpdater(controller)

        updater.update()
        self.assertEqual({"first.bin"}, model.get_file_names())
        controller._refresh_model_file_command_identities_locked.assert_called_once_with()
        updater.update()
        self.assertEqual({"first.bin", "second.bin"}, model.get_file_names())
        self.assertEqual(2, controller._refresh_model_file_command_identities_locked.call_count)
        self.assertEqual(2, builder.build_progressive_roots.call_count)
        builder.build_model.assert_not_called()

        updater.update()
        self.assertEqual({"first.bin", "second.bin"}, model.get_file_names())
        self.assertEqual(3, controller._refresh_model_file_command_identities_locked.call_count)
        builder.build_model.assert_called_once_with()

    def test_progressive_scanner_session_change_reopens_initial_publication_window(self):
        process = object.__new__(ScannerProcess)
        process._ScannerProcess__session_token = "session-a"
        controller = SimpleNamespace()

        with patch.object(ScannerProcess, "pop_results", return_value=[]):
            _pop_scan_updates(controller, "remote", process)
            process._ScannerProcess__session_token = "session-b"
            _pop_scan_updates(controller, "remote", process)

        self.assertTrue(controller._Controller__progressive_scan_session_changed)

    def test_replaced_progressive_sessions_ignore_stale_rows_until_new_partial_evidence(self):
        def process(session_token, results):
            scanner_process = ScannerProcess(
                scanner=SimpleNamespace(), interval_in_ms=0, verbose=False,
            )
            scanner_process._ScannerProcess__session_token = session_token
            scanner_process.pop_results = MagicMock(side_effect=results)
            return scanner_process

        def scan_result(session_token, size, *, final=False, failed=False, manifest_only=False):
            return ScannerResult(
                datetime.now(), [] if failed or manifest_only else [SystemFile("root", size)],
                scanned_path_pair_ids={None},
                generation=1,
                is_progress=True,
                failed=failed,
                root_names={"root"} if manifest_only else None,
                completed_path_pair_ids={None} if final else set(),
                is_scan_final=final,
                unknown_path_pair_ids=set() if final and not failed else {None},
                session_token=session_token,
                is_full_snapshot=final,
                full_snapshot_path_pair_ids={None} if final else set(),
            )

        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
        )
        old_remote = process("old-remote", [[scan_result("old-remote", 1, final=True)]])
        old_local = process("old-local", [[scan_result("old-local", 1, final=True)]])
        controller._Controller__remote_scan_process = old_remote
        controller._Controller__local_scan_process = old_local
        updater = ModelUpdater(controller)

        updater.update()
        self.assertTrue(controller._Controller__progressive_joint_authoritative)
        self.assertEqual({None}, controller._Controller__reconciled_local_path_pair_ids)
        self.assertEqual({None}, controller._Controller__reconciled_remote_path_pair_ids)
        model_builder.reset_mock()

        new_remote = process(
            "new-remote",
            [
                [],
                [scan_result("new-remote", 0, failed=True)],
                [scan_result("new-remote", 0, manifest_only=True)],
                [scan_result("new-remote", 2)],
                [scan_result("new-remote", 3)],
                [scan_result("new-remote", 4, final=True)],
            ],
        )
        new_local = process(
            "new-local",
            [
                [],
                [scan_result("new-local", 0, failed=True)],
                [scan_result("new-local", 0, manifest_only=True)],
                [scan_result("new-local", 2)],
                [scan_result("new-local", 3)],
                [scan_result("new-local", 4, final=True)],
            ],
        )
        controller._Controller__remote_scan_process = new_remote
        controller._Controller__local_scan_process = new_local

        # Session replacement alone must not consume the reopened publication
        # budget from the reconciler's retained last-good rows.
        updater.update()
        self.assertFalse(controller._Controller__progressive_joint_first_publication)
        self.assertEqual(set(), controller._Controller__reconciled_local_path_pair_ids)
        self.assertEqual(set(), controller._Controller__reconciled_remote_path_pair_ids)
        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()

        updater.update()
        self.assertFalse(controller._Controller__progressive_joint_first_publication)
        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()

        updater.update()
        self.assertFalse(controller._Controller__progressive_joint_first_publication)
        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()

        updater.update()
        self.assertTrue(controller._Controller__progressive_joint_first_publication)
        model_builder.build_progressive_roots.assert_called_once()
        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()
        model_builder.reset_mock()

        updater.update()
        self.assertTrue(controller._Controller__progressive_joint_first_publication)
        model_builder.build_progressive_roots.assert_called_once()
        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()
        model_builder.reset_mock()

        updater.update()
        self.assertTrue(controller._Controller__progressive_joint_authoritative)
        model_builder.build_progressive_roots.assert_not_called()
        model_builder.set_local_files.assert_called_once()
        model_builder.set_remote_files.assert_called_once()

    def test_progressive_final_reconciliation_publishes_and_prunes_markers(self):
        partial = self._progressive_result(final=False, unknown={None})
        controller, model_builder = self._make_progressive_update_controller(
            partial,
            downloaded_file_names={"root", "stale"},
            downloaded_timestamps={"root": 1.0, "stale": 2.0},
        )
        updater = ModelUpdater(controller)
        updater.update()
        model_builder.reset_mock()
        model_builder.has_changes.return_value = True
        model_builder.build_model.return_value = controller._Controller__model

        final_remote = self._progressive_result(size=2, final=True)
        final_local = self._progressive_result(size=2, final=True)
        controller._Controller__remote_scan_process.pop_latest_result.return_value = final_remote
        controller._Controller__local_scan_process.pop_latest_result.return_value = final_local
        updater.update()

        model_builder.set_remote_files.assert_called_once_with(final_remote.files)
        model_builder.set_local_files.assert_called_once_with(final_local.files)
        model_builder.set_unknown_local_path_pair_ids.assert_called_once_with(set())
        model_builder.set_downloaded_files.assert_called_once_with({"root"})
        model_builder.set_downloaded_timestamps.assert_called_once_with({"root": 1.0})
        self.assertEqual({"root"}, controller._Controller__persist.downloaded_file_names)
        self.assertEqual({"root": 1.0}, controller._Controller__persist.downloaded_timestamps)

    def test_identical_authoritative_progressive_refresh_keeps_real_builder_clean(self):
        final = self._progressive_result(final=True)
        model_builder = ModelBuilder()
        model_builder.set_local_files(final.files)
        model_builder.set_remote_files(final.files)
        model_builder.set_unknown_local_path_pair_ids(set())
        model_builder.set_downloaded_files({"root"})
        model_builder.set_downloaded_timestamps({"root": 1.0})
        baseline_model = model_builder.build_model()
        model_builder.adopt_applied_model(baseline_model, baseline_model)
        self.assertFalse(model_builder.has_changes())

        controller, _ = self._make_progressive_update_controller(
            final,
            local_scan=final,
            downloaded_file_names={"root"},
            downloaded_timestamps={"root": 1.0},
            model_builder=model_builder,
            model=baseline_model,
        )
        updater = ModelUpdater(controller)

        updater.update()
        updater.update()

        self.assertFalse(model_builder.has_changes())
        self.assertIs(baseline_model, model_builder.build_model())

    def test_partial_progressive_refresh_keeps_active_lftp_updates_immediate(self):
        partial = self._progressive_result(final=False, unknown={None})
        controller, model_builder = self._make_progressive_update_controller(partial)
        status = LftpJobStatus(
            1,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            "root",
            "",
        )
        controller._Controller__lftp.status.return_value = [status]
        updater = ModelUpdater(controller)

        updater.update()

        model_builder.set_lftp_statuses.assert_called_once_with([status])
        next_poll = controller._Controller__next_lftp_status_poll_at
        self.assertIsNotNone(next_poll)
        self.assertGreater(next_poll, datetime.now())
        self.assertLessEqual(next_poll - datetime.now(), timedelta(milliseconds=100))

    def test_active_lftp_status_updates_existing_root_without_full_build(self):
        builder = ModelBuilder()
        remote_root = SystemFile("root", 100, False)
        builder.set_remote_files([remote_root])
        live_model = builder.build_model()
        before_version = live_model.version
        listener = MagicMock()
        live_model.add_listener(listener)
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [status]
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        self.assertEqual(25, live_model.get_file("root").transferred_size)
        self.assertEqual(before_version + 1, live_model.version)
        self.assertEqual(1, live_model.file_count)
        self.assertEqual(1, live_model.tree_file_count)
        listener.file_updated.assert_called_once()
        self.assertFalse(builder.has_changes())
        builder.build_model.assert_not_called()

    def test_active_scan_and_fresh_lftp_progress_share_bounded_root_delta(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [status]
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("root", 25, False)],
        )
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        self.assertEqual(25, live_model.get_file("root").transferred_size)
        self.assertFalse(builder.has_changes())
        builder.build_model.assert_not_called()

    def test_running_to_queued_pending_completion_uses_full_reconciliation(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        builder.set_local_files([SystemFile("root", 20, False, is_staging=True)])
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(65, 100, 65, 10, 4)
        builder.set_lftp_statuses([running])
        live_model = builder.build_model()
        builder.adopt_applied_model(live_model, live_model)
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        controller._Controller__prev_downloading_file_names = {("root", None, None)}
        queued = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "root", "",
        )
        controller._Controller__lftp.status.return_value = [queued]
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        builder.build_model.assert_called_once()
        self.assertTrue(controller._Controller__pending_completion_file_names)

    def test_active_delta_authorization_rejection_publishes_only_full_reconciliation(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        live_model = builder.build_model()
        listener = MagicMock()
        live_model.add_listener(listener)
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [status]
        builder.authorize_active_transfer_delta = MagicMock(return_value=False)
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        builder.build_model.assert_called_once()
        listener.file_updated.assert_called_once()
        self.assertEqual(25, live_model.get_file("root").transferred_size)

    def test_clean_idle_tick_skips_active_delta_model_root_lookup(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        live_model.get_file_ids = MagicMock(side_effect=AssertionError("idle delta must not inspect roots"))

        ModelUpdater(controller).update()

        live_model.get_file_ids.assert_not_called()

    def _make_lftp_completion_controller(self, prev_downloading_file_names=None):
        controller = SimpleNamespace(
            _Controller__prev_downloading_file_names=set(prev_downloading_file_names or []),
            _Controller__pending_completion_file_names=set(),
            _Controller__is_explicitly_stopped=MagicMock(return_value=False),
            _Controller__local_scan_process=MagicMock(),
            logger=MagicMock(),
        )
        return controller

    def test_sync_persist_to_all_builders_forwards_persisted_categories(self):
        downloaded_file_names = {"downloaded-a", "downloaded-b"}
        extracted_file_names = {"extracted-a"}
        stopped_file_names = {"stopped-a", "stopped-b"}
        controller, model_builder = self._make_controller(
            downloaded_file_names,
            extracted_file_names,
            stopped_file_names,
        )

        updater = ModelUpdater(controller)
        updater.sync_persist_to_all_builders()

        self.assertEqual(
            [
                call.set_downloaded_files(downloaded_file_names),
                call.set_downloaded_timestamps({}),
                call.set_extracted_files(extracted_file_names),
                call.set_stopped_files(stopped_file_names),
            ],
            model_builder.mock_calls,
        )

    def test_sync_persist_to_all_builders_drops_preboundary_separator_keys(self):
        pair_id = "movies"
        normalized_file_id = ModelFile.build_file_id("legacy.mkv", pair_id)
        controller, model_builder = self._make_controller(
            downloaded_file_names={
                "plain.txt",
                f"{pair_id}{KEY_SEP}legacy.mkv",
            },
            extracted_file_names={
                f"{pair_id}{KEY_SEP}legacy.mkv",
            },
            stopped_file_names={
                f"{pair_id}{KEY_SEP}legacy.mkv",
            },
            path_pairs_by_id={pair_id: SimpleNamespace()},
        )

        updater = ModelUpdater(controller)
        updater.sync_persist_to_all_builders()

        self.assertEqual(
            [
                call.set_downloaded_files(set()),
                call.set_downloaded_timestamps({}),
                call.set_extracted_files(set()),
                call.set_stopped_files(set()),
            ],
            model_builder.mock_calls,
        )

    def test_sync_persist_to_all_builders_preserves_default_name_with_colon(self):
        pair_id = "movies"
        default_file_name = f"{pair_id}:legacy.mkv"
        controller, model_builder = self._make_controller(
            downloaded_file_names={default_file_name},
            extracted_file_names={default_file_name},
            stopped_file_names={default_file_name},
            path_pairs_by_id={pair_id: SimpleNamespace()},
        )

        updater = ModelUpdater(controller)
        updater.sync_persist_to_all_builders()

        self.assertEqual(
            [
                call.set_downloaded_files(set()),
                call.set_downloaded_timestamps({}),
                call.set_extracted_files(set()),
                call.set_stopped_files(set()),
            ],
            model_builder.mock_calls,
        )

    def test_sync_persist_to_all_builders_drops_preboundary_uuid_colon_keys(self):
        pair_id = "12345678-1234-1234-1234-123456789abc"
        legacy_key = f"{pair_id}:legacy.mkv"
        normalized_file_id = ModelFile.build_file_id("legacy.mkv", pair_id)
        controller, model_builder = self._make_controller(
            downloaded_file_names={legacy_key},
            extracted_file_names={legacy_key},
            stopped_file_names={legacy_key},
            path_pairs_by_id={pair_id: SimpleNamespace()},
        )

        updater = ModelUpdater(controller)
        updater.sync_persist_to_all_builders()

        self.assertEqual(
            [
                call.set_downloaded_files(set()),
                call.set_downloaded_timestamps({}),
                call.set_extracted_files(set()),
                call.set_stopped_files(set()),
            ],
            model_builder.mock_calls,
        )

    def test_safe_stale_markers_keeps_active_unscoped_id_without_path_pairs(self):
        active = "active.mkv"
        stale = "stale.mkv"

        self.assertEqual(
            {stale},
            ModelUpdater._safe_stale_marker_ids(
                {active, stale}, {active}, set(), set(), set(),
            ),
        )

    def test_update_filters_remote_scan_files_before_publishing(self):
        remote_root = SystemFile("Series", 350, True)
        season = SystemFile("Season 1", 300, True)
        season.add_child(SystemFile("episode1.mkv", 100, False))
        season.add_child(SystemFile("episode1.nfo", 5, False))
        season.add_child(SystemFile("notes.txt", 3, False))
        remote_root.add_child(season)
        remote_root.add_child(SystemFile("keep.mkv", 50, False))
        remote_root.add_child(SystemFile("skip.nfo", 5, False))
        latest_remote_scan = SimpleNamespace(
            timestamp=0,
            failed=False,
            error_message=None,
            files=[remote_root],
        )

        controller = SimpleNamespace(
            _Controller__persist=SimpleNamespace(
                downloaded_file_names=set(),
                extracted_file_names=set(),
                stopped_file_names=set(),
            ),
            _Controller__model_builder=MagicMock(),
            _Controller__model=MagicMock(),
            _Controller__remote_scan_process=MagicMock(),
            _Controller__local_scan_process=MagicMock(),
            _Controller__active_scan_process=MagicMock(),
            _Controller__extract_process=MagicMock(),
            _Controller__validate_process=MagicMock(),
            _Controller__lftp=MagicMock(),
            _Controller__context=SimpleNamespace(
                config=SimpleNamespace(
                    general=SimpleNamespace(exclude_patterns="*.nfo, Sample/"),
                ),
                status=SimpleNamespace(
                    controller=SimpleNamespace(),
                    server=SimpleNamespace(),
                ),
            ),
            logger=MagicMock(),
            _Controller__temp_diag=MagicMock(),
            _Controller__set_active_scanner_files=MagicMock(),
            _Controller__record_breadcrumb=MagicMock(),
            _Controller__trace_corr_id_from_files=MagicMock(return_value="remote-scan-corr"),
            _Controller__startup_recovery_done=True,
            _Controller__pending_completion_file_names=set(),
            _Controller__prev_downloading_file_names=set(),
            _Controller__malformed_status_only_file_ids=set(),
            _Controller__pending_auto_purge_file_ids=set(),
            _Controller__last_lftp_statuses=[],
            _Controller__active_downloading_file_names=[],
            _Controller__active_extracting_file_names=[],
            _Controller__next_lftp_status_poll_at=None,
            _Controller__lftp_status_poll_retry_seconds=1,
            _Controller__lftp_status_cache_expires_at=None,
            _Controller__lftp_status_cache_max_age_seconds=3,
            _Controller__lftp_status_poll_retry_active=False,
            _Controller__exclude_patterns="*.nfo, Sample/",
        )
        controller._Controller__context.config.general.exclude_patterns = ""
        controller._Controller__remote_scan_process.pop_latest_result.return_value = latest_remote_scan
        controller._Controller__local_scan_process.pop_latest_result.return_value = None
        controller._Controller__active_scan_process.pop_latest_result.return_value = None
        controller._Controller__extract_process.pop_latest_statuses.return_value = None
        controller._Controller__extract_process.pop_completed.return_value = []
        controller._Controller__extract_process.pop_failed.return_value = []
        controller._Controller__validate_process.pop_latest_statuses.return_value = None
        controller._Controller__lftp.status.return_value = []
        controller._Controller__lftp.last_status_poll_healthy = True
        controller._Controller__model_builder.has_changes.return_value = False

        updater = ModelUpdater(controller)
        updater.update()

        controller._Controller__model_builder.set_remote_files.assert_called_once()
        filtered_files = controller._Controller__model_builder.set_remote_files.call_args[0][0]
        self.assertEqual(["Series"], [file.name for file in filtered_files])
        self.assertEqual(["Season 1", "keep.mkv"], [file.name for file in filtered_files[0].children])
        self.assertEqual(103, filtered_files[0].children[0].size)
        self.assertEqual(["episode1.mkv", "notes.txt"], [file.name for file in filtered_files[0].children[0].children])
        controller._Controller__record_breadcrumb.assert_any_call(
            stage="scan",
            message="remote_scan_result",
            details={
                "file_count": 1,
                "failed": False,
                "error_message": None,
                "is_progress": False,
                "is_scan_final": True,
                "generation": 0,
                "scanned_pair_count": 0,
                "completed_pair_count": 0,
                "unknown_pair_count": 0,
                "joint_publication_allowed": True,
                "joint_authoritative": False,
                "joint_local_root_count": 0,
                "joint_remote_root_count": 0,
            },
            event_type="state_transition",
            corr_id="remote-scan-corr",
        )

    def test_update_adopts_live_model_cache_without_masking_later_invalidation(self):
        builder = ModelBuilder()
        builder.set_base_logger(logging.getLogger("model-updater-cache-test"))
        live_model = Model()
        live_model.set_base_logger(logging.getLogger("model-updater-cache-test"))
        remote_root = SystemFile("root", 10, False)
        remote_scan = SimpleNamespace(
            timestamp=datetime.now(),
            failed=False,
            error_message=None,
            files=[remote_root],
            scanned_path_pair_ids={None},
        )
        pending_extract_ids = {"root"}

        def record_terminal_ids(extract_ids, validation_ids):
            pending_extract_ids.difference_update(extract_ids)
            self.assertEqual(set(), validation_ids)

        controller = SimpleNamespace(
            _Controller__persist=SimpleNamespace(
                downloaded_file_names=set(),
                extracted_file_names=set(),
                stopped_file_names=set(),
            ),
            _Controller__model_builder=builder,
            _Controller__model=live_model,
            _Controller__model_lock=RLock(),
            _Controller__remote_scan_process=MagicMock(),
            _Controller__local_scan_process=MagicMock(),
            _Controller__active_scan_process=MagicMock(),
            _Controller__extract_process=MagicMock(),
            _Controller__validate_process=MagicMock(),
            _Controller__lftp=MagicMock(),
            _Controller__context=SimpleNamespace(
                config=SimpleNamespace(general=SimpleNamespace(exclude_patterns="")),
                status=SimpleNamespace(controller=SimpleNamespace(), server=SimpleNamespace()),
            ),
            logger=logging.getLogger("model-updater-cache-test"),
            _Controller__temp_diag=MagicMock(),
            _Controller__set_active_scanner_files=MagicMock(),
            _Controller__record_breadcrumb=MagicMock(),
            _Controller__trace_corr_id_from_files=MagicMock(return_value="cache-test"),
            _Controller__should_auto_purge_local_file=MagicMock(return_value=False),
            _Controller__find_target_archive_model_file=MagicMock(return_value=None),
            _Controller__startup_recovery_done=True,
            _Controller__pending_completion_file_names=set(),
            _Controller__prev_downloading_file_names=set(),
            _Controller__malformed_status_only_file_ids=set(),
            _Controller__pending_auto_purge_file_ids=set(),
            _Controller__last_lftp_statuses=[],
            _Controller__active_downloading_file_names=[],
            _Controller__active_extracting_file_names=[],
            _Controller__next_lftp_status_poll_at=None,
            _Controller__lftp_status_poll_retry_seconds=1,
            _Controller__lftp_status_cache_expires_at=None,
            _Controller__lftp_status_cache_max_age_seconds=3,
            _Controller__lftp_status_poll_retry_active=False,
            _Controller__exclude_patterns="",
            _Controller__last_remote_reconciliation_healthy=True,
            _Controller__last_local_reconciliation_healthy=True,
            _Controller__path_pairs_by_id={},
            _record_worker_terminal_ids=record_terminal_ids,
        )
        controller._Controller__remote_scan_process.pop_latest_result.return_value = remote_scan
        controller._Controller__local_scan_process.pop_latest_result.return_value = None
        controller._Controller__active_scan_process.pop_latest_result.return_value = None
        controller._Controller__extract_process.pop_latest_statuses.return_value = None
        controller._Controller__extract_process.pop_completed.return_value = [
            ExtractCompletedResult(datetime.now(), "root", False, file_id="root"),
        ]
        controller._Controller__extract_process.pop_failed.return_value = []
        controller._Controller__validate_process.pop_latest_statuses.return_value = None
        controller._Controller__lftp.status.return_value = []
        controller._Controller__lftp.last_status_poll_healthy = True

        updater = ModelUpdater(controller)
        updater.update()

        self.assertEqual(10, live_model.get_file("root").remote_size)
        self.assertIs(live_model, builder._ModelBuilder__cached_model)
        self.assertEqual(set(), pending_extract_ids)
        builder.set_remote_files([SystemFile("root", 11, False)])
        self.assertTrue(builder.has_changes())
        self.assertIsNot(live_model, builder.build_model())

    def test_handle_lftp_completion_detection_records_completed_downloads_and_forces_rescan(self):
        completion_entry = ("movie.mkv", "movies", "Movies")
        still_downloading_entry = ("episode.mkv", "tv", "TV")
        explicitly_stopped_entry = ("stopped.mkv", "movies", "Movies")

        controller = self._make_lftp_completion_controller(
            prev_downloading_file_names={
                completion_entry,
                still_downloading_entry,
                explicitly_stopped_entry,
            }
        )
        controller._Controller__is_explicitly_stopped.side_effect = (
            lambda name, path_pair_id: (name, path_pair_id) == explicitly_stopped_entry[:2]
        )

        updater = ModelUpdater(controller)
        updater._handle_lftp_completion_detection(
            [still_downloading_entry],
            True,
        )

        self.assertEqual(
            {still_downloading_entry},
            controller._Controller__prev_downloading_file_names,
        )
        self.assertEqual(
            {completion_entry},
            controller._Controller__pending_completion_file_names,
        )
        controller._Controller__local_scan_process.force_scan.assert_called_once_with()
        controller.logger.info.assert_called_once_with(
            "Download completion pending (LFTP job finished): {}".format(
                ModelFile.build_file_id(*completion_entry[:2])
            )
        )

    def test_handle_lftp_completion_detection_skips_when_detection_is_not_ready(self):
        previous_entry = ("movie.mkv", "movies", "Movies")
        controller = self._make_lftp_completion_controller({previous_entry})

        updater = ModelUpdater(controller)
        updater._handle_lftp_completion_detection([], False)

        self.assertEqual(
            {previous_entry},
            controller._Controller__prev_downloading_file_names,
        )
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        controller._Controller__local_scan_process.force_scan.assert_not_called()
        controller._Controller__is_explicitly_stopped.assert_not_called()
        controller.logger.info.assert_not_called()

    def test_active_scan_force_retries_at_checkpoint_cadence_with_long_scan_interval(self):
        controller = SimpleNamespace(
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids=set(),
            _Controller__active_scan_ready_file_ids=set(),
            _Controller__next_active_scan_force_at=None,
            _Controller__context=SimpleNamespace(
                config=SimpleNamespace(
                    controller=SimpleNamespace(interval_ms_downloading_scan=60000),
                ),
            ),
        )
        updater = ModelUpdater(controller)
        start = datetime(2026, 1, 1)
        active_file = [("movie.mkv", None, None)]

        updater._force_active_scan_for_stoppability(active_file, now=start)
        updater._force_active_scan_for_stoppability(
            active_file,
            now=start + timedelta(seconds=1.9),
        )
        updater._force_active_scan_for_stoppability(
            active_file,
            now=start + timedelta(seconds=2),
        )

        self.assertEqual(2, controller._Controller__active_scan_process.force_scan.call_count)

    def test_active_scan_force_stops_after_sidecar_readiness_without_new_scan_result(self):
        controller = SimpleNamespace(
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids={"movie.mkv"},
            _Controller__active_scan_ready_file_ids=set(),
            _Controller__next_active_scan_force_at=None,
        )
        updater = ModelUpdater(controller)
        start = datetime(2026, 1, 1)
        ready_file = SystemFile("movie.mkv", 100)
        ready_file.status_sidecar_ready = True
        latest_scan = SimpleNamespace(files=[ready_file])
        active_file = [("movie.mkv", None, None)]

        updater._force_active_scan_for_stoppability(active_file, latest_scan, now=start)
        updater._force_active_scan_for_stoppability(
            active_file,
            latest_active_scan=None,
            now=start + timedelta(seconds=10),
        )

        controller._Controller__active_scan_process.force_scan.assert_not_called()
        self.assertEqual(set(), controller._Controller__active_scan_force_file_ids)
        self.assertIsNone(controller._Controller__next_active_scan_force_at)

    def test_failed_active_scan_invalidates_cached_readiness_for_still_active_job(self):
        controller = SimpleNamespace(
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids=set(),
            _Controller__active_scan_ready_file_ids=set(),
            _Controller__next_active_scan_force_at=None,
        )
        updater = ModelUpdater(controller)
        start = datetime(2026, 1, 1)
        active_file = [("movie.mkv", None, None)]
        ready_file = SystemFile("movie.mkv", 100)
        ready_file.status_sidecar_ready = True

        updater._force_active_scan_for_stoppability(
            active_file,
            SimpleNamespace(files=[ready_file], failed=False),
            now=start,
        )
        updater._force_active_scan_for_stoppability(
            active_file,
            SimpleNamespace(files=[], failed=True),
            now=start + timedelta(seconds=1),
        )

        controller._Controller__active_scan_process.force_scan.assert_called_once_with()
        self.assertEqual({"movie.mkv"}, controller._Controller__active_scan_force_file_ids)
        self.assertEqual(set(), controller._Controller__active_scan_ready_file_ids)

    def test_failed_directory_scan_does_not_force_checkpoint_retry(self):
        directory = ModelFile("movie-folder", True)
        model = MagicMock()
        model.get_file.return_value = directory
        controller = SimpleNamespace(
            _Controller__model=model,
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids=set(),
            _Controller__active_scan_ready_file_ids=set(),
            _Controller__next_active_scan_force_at=None,
        )
        updater = ModelUpdater(controller)
        active_file = [("movie-folder", None, None)]

        updater._force_active_scan_for_stoppability(active_file, now=datetime(2026, 1, 1))
        updater._force_active_scan_for_stoppability(
            active_file,
            SimpleNamespace(files=[], failed=True),
            now=datetime(2026, 1, 1, 0, 0, 1),
        )

        controller._Controller__active_scan_process.force_scan.assert_not_called()
        self.assertEqual(set(), controller._Controller__active_scan_force_file_ids)

    def test_fresh_regular_scan_overrides_stale_model_directory_fallback(self):
        stale_directory = ModelFile("movie.mkv", True)
        model = MagicMock()
        model.get_file.return_value = stale_directory
        controller = SimpleNamespace(
            _Controller__model=model,
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids=set(),
            _Controller__active_scan_ready_file_ids=set(),
            _Controller__next_active_scan_force_at=None,
        )
        updater = ModelUpdater(controller)
        regular_file = SystemFile("movie.mkv", 100)
        active_file = [("movie.mkv", None, None)]

        updater._force_active_scan_for_stoppability(
            active_file,
            SimpleNamespace(files=[regular_file], failed=False),
            now=datetime(2026, 1, 1),
        )

        controller._Controller__active_scan_process.force_scan.assert_called_once_with()
        self.assertEqual({"movie.mkv"}, controller._Controller__active_scan_force_file_ids)

    def test_rclone_active_download_does_not_force_lftp_checkpoint_scan(self):
        controller = SimpleNamespace(
            _Controller__lftp=SimpleNamespace(backend_name="rclone"),
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids={"movie.mkv"},
            _Controller__active_scan_ready_file_ids={"movie.mkv"},
            _Controller__next_active_scan_force_at=datetime(2026, 1, 1),
        )
        updater = ModelUpdater(controller)

        updater._force_active_scan_for_stoppability(
            [("movie.mkv", None, None)],
            now=datetime(2026, 1, 1),
        )

        controller._Controller__active_scan_process.force_scan.assert_not_called()
        self.assertEqual(set(), controller._Controller__active_scan_force_file_ids)
        self.assertEqual(set(), controller._Controller__active_scan_ready_file_ids)
        self.assertIsNone(controller._Controller__next_active_scan_force_at)

    def test_delayed_ready_scan_does_not_suppress_same_identity_restart_wake(self):
        controller = SimpleNamespace(
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids=set(),
            _Controller__active_scan_ready_file_ids=set(),
            _Controller__next_active_scan_force_at=None,
        )
        updater = ModelUpdater(controller)
        start = datetime(2026, 1, 1)
        ready_file = SystemFile("movie.mkv", 100)
        ready_file.status_sidecar_ready = True
        delayed_scan = SimpleNamespace(files=[ready_file])

        # The result arrived after the prior job ended, so it must not cache
        # readiness for a future transfer with the same identity.
        updater._force_active_scan_for_stoppability([], delayed_scan, now=start)
        updater._force_active_scan_for_stoppability(
            [("movie.mkv", None, None)],
            latest_active_scan=None,
            now=start + timedelta(seconds=10),
        )

        controller._Controller__active_scan_process.force_scan.assert_called_once_with()
        self.assertEqual({"movie.mkv"}, controller._Controller__active_scan_force_file_ids)

    def test_failed_active_scan_does_not_certify_checkpoint_across_path_pairs(self):
        controller = SimpleNamespace(
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids=set(),
            _Controller__active_scan_ready_file_ids=set(),
            _Controller__next_active_scan_force_at=None,
        )
        updater = ModelUpdater(controller)
        start = datetime(2026, 1, 1)
        active_files = [
            ("movie.mkv", "movies", "Movies"),
            ("movie.mkv", "tv", "TV"),
        ]
        movies_file = SystemFile("movie.mkv", 100)
        movies_file.path_pair_id = "movies"
        movies_file.status_sidecar_ready = True
        tv_file = SystemFile("movie.mkv", 100)
        tv_file.path_pair_id = "tv"
        tv_file.status_sidecar_ready = True

        updater._force_active_scan_for_stoppability(
            active_files,
            SimpleNamespace(files=[movies_file, tv_file], failed=True),
            now=start,
        )

        expected_pending = {
            ModelFile.build_file_id("movie.mkv", "movies"),
            ModelFile.build_file_id("movie.mkv", "tv"),
        }
        self.assertEqual(expected_pending, controller._Controller__active_scan_force_file_ids)

        updater._force_active_scan_for_stoppability(
            active_files,
            SimpleNamespace(files=[movies_file], failed=False),
            now=start + timedelta(seconds=2),
        )

        self.assertEqual(
            {ModelFile.build_file_id("movie.mkv", "tv")},
            controller._Controller__active_scan_force_file_ids,
        )

    def test_active_scan_force_state_clears_when_no_jobs_remain(self):
        controller = SimpleNamespace(
            _Controller__active_scan_process=MagicMock(),
            _Controller__active_scan_force_file_ids={"movie.mkv"},
            _Controller__active_scan_ready_file_ids=set(),
            _Controller__next_active_scan_force_at=None,
        )
        updater = ModelUpdater(controller)

        updater._force_active_scan_for_stoppability([], now=datetime(2026, 1, 1))

        controller._Controller__active_scan_process.force_scan.assert_not_called()
        self.assertEqual(set(), controller._Controller__active_scan_force_file_ids)
        self.assertEqual(set(), controller._Controller__active_scan_ready_file_ids)
