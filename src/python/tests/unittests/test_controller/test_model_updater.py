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
    _filter_actionable_move_retry_ids,
    _request_model_rebuild,
)
from common.performance_diagnostics import (
    CANDIDATE_LIFECYCLE_FALLBACK_REASON_EXCEPTION,
    CANDIDATE_PAIR_FALLBACK_REASON_AUTHORIZATION_REJECTED,
    CANDIDATE_UNRELATED_LIFECYCLE_REASON_RETRY,
    COUNTER_CANDIDATE_LIFECYCLE_FALLBACK,
    COUNTER_CANDIDATE_PAIR_FALLBACK,
    COUNTER_UNRELATED_CANDIDATE_LIFECYCLE_DEFERRED,
    DURATION_MODEL_UPDATE_BUILD_FINALIZATION,
    DURATION_MODEL_UPDATE_SCAN_INTAKE,
    DURATION_MODEL_UPDATE_STATE_PREPARATION,
    DURATION_MODEL_UPDATE_LOCK_HOLD,
    DURATION_MODEL_UPDATE_LOCK_WAIT,
    DURATION_MODEL_UPDATE_TRACE_FINALIZATION,
    DURATION_MODEL_UPDATE_TRACE_SETUP,
    MODEL_REBUILD_REASON_MOVE_RETRY_DUE,
    PerformanceDiagnosticsCollector,
)
from common.breadcrumb_trace import BreadcrumbTraceCollector
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
            call(DURATION_MODEL_UPDATE_LOCK_HOLD),
            call(DURATION_MODEL_UPDATE_TRACE_FINALIZATION),
        ], diagnostics.begin_duration.call_args_list)
        self.assertEqual([
            call(DURATION_MODEL_UPDATE_TRACE_SETUP, (DURATION_MODEL_UPDATE_TRACE_SETUP,)),
            call(DURATION_MODEL_UPDATE_LOCK_WAIT, (DURATION_MODEL_UPDATE_LOCK_WAIT,)),
            call(DURATION_MODEL_UPDATE_LOCK_HOLD, (DURATION_MODEL_UPDATE_LOCK_HOLD,)),
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

    def test_stale_move_retry_marker_is_not_actionable(self):
        builder = MagicMock()
        builder.has_unresolved_staging_collision.return_value = False
        builder.has_complete_local_coverage.return_value = False

        actionable, collisions = _filter_actionable_move_retry_ids(
            builder, ["stale-id", "child-id"],
        )

        self.assertEqual([], actionable)
        self.assertEqual(set(), collisions)
        builder.has_unresolved_staging_collision.assert_has_calls([
            call("stale-id"), call("child-id"),
        ])
        builder.has_complete_local_coverage.assert_has_calls([
            call("stale-id"), call("child-id"),
        ])

    def test_complete_move_retry_marker_is_actionable_once_per_gate_token(self):
        builder = MagicMock()
        builder.has_unresolved_staging_collision.return_value = False
        builder.has_complete_local_coverage.return_value = True
        gate = _MoveRetryRebuildGate()
        now = datetime.now()
        failure_counts = {"retry": 1}

        first_due = gate.due_ids(failure_counts, {}, 4, now)
        actionable, collisions = _filter_actionable_move_retry_ids(builder, first_due)
        self.assertEqual(["retry"], actionable)
        self.assertEqual(set(), collisions)

        second_due = gate.due_ids(failure_counts, {}, 4, now)
        actionable, collisions = _filter_actionable_move_retry_ids(builder, second_due)
        self.assertEqual([], actionable)
        self.assertEqual(set(), collisions)

    def test_unresolved_collision_move_retry_marker_is_actionable(self):
        builder = MagicMock()
        builder.has_unresolved_staging_collision.side_effect = lambda file_id: file_id == "collision"
        builder.has_complete_local_coverage.return_value = False

        actionable, collisions = _filter_actionable_move_retry_ids(
            builder, ["collision"],
        )

        self.assertEqual(["collision"], actionable)
        self.assertEqual({"collision"}, collisions)

    def test_stale_due_marker_does_not_invalidate_or_build_global_model(self):
        controller, model_builder = self._make_progressive_update_controller(None)
        controller._Controller__persist.move_failure_counts = {"stale-id": 1}

        ModelUpdater(controller).update()

        model_builder.request_rebuild.assert_not_called()
        model_builder.build_model.assert_not_called()

    def test_complete_due_marker_requests_one_rebuild_until_token_changes(self):
        controller, model_builder = self._make_progressive_update_controller(None)
        controller._Controller__persist.move_failure_counts = {"retry": 1}
        model_builder.has_complete_local_coverage.return_value = True

        updater = ModelUpdater(controller)
        updater.update()
        updater.update()

        model_builder.request_rebuild.assert_called_once_with()

    def test_real_builder_stale_child_then_canonical_root_rearms_retry_rebuild(self):
        builder = ModelBuilder()
        builder.set_remote_files([])
        builder.set_local_files([])
        builder.set_active_files([])
        model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, model_builder=builder, model=model,
        )
        controller._Controller__context.performance_diagnostics = PerformanceDiagnosticsCollector(
            lambda: True,
        )
        controller._reserve_move_attempt = MagicMock(return_value=False)
        controller._Controller__persist.move_failure_counts = {"stale-child": 1}
        updater = ModelUpdater(controller)

        with patch.object(builder, "request_rebuild", wraps=builder.request_rebuild) as request_rebuild, \
                patch.object(builder, "build_model", wraps=builder.build_model) as build_model:
            updater.update()
            self.assertEqual(0, request_rebuild.call_count)
            self.assertEqual(0, build_model.call_count)
            self.assertEqual(
                0,
                controller._Controller__context.performance_diagnostics.snapshot()["counters"].get(
                    "model_rebuild_move_retry_due", 0,
                ),
            )

            retry_root = SystemFile("retry", 1, False)
            builder.set_remote_files([retry_root])
            builder.set_local_files([retry_root])
            controller._Controller__persist.move_failure_counts = {"retry": 1}

            updater.update()
            updater.update()

        self.assertEqual(1, request_rebuild.call_count)
        self.assertEqual(1, build_model.call_count)
        self.assertEqual(
            1,
            controller._Controller__context.performance_diagnostics.snapshot()["counters"].get(
                "model_rebuild_move_retry_due", 0,
            ),
        )

    def test_real_builder_retry_reason_counters_are_edge_triggered_for_complete_and_collision(self):
        cases = []
        complete_root = SystemFile("complete", 1, False)
        cases.append((
            "complete",
            [complete_root],
            [complete_root],
            "model_rebuild_move_retry_due",
        ))
        collision_remote = SystemFile("collision", 20, True)
        collision_remote.add_child(SystemFile("covered.mkv", 10, False))
        collision_remote.add_child(SystemFile("missing.mkv", 10, False))
        collision_local = SystemFile("collision", 10, True)
        collision_leaf = SystemFile("covered.mkv", 10, False, is_staging=True)
        collision_leaf.has_staging_collision = True
        collision_local.add_child(collision_leaf)
        cases.append((
            "collision",
            [collision_remote],
            [collision_local],
            "model_rebuild_collision_retry",
        ))

        for root_name, remote_files, local_files, reason_counter in cases:
            with self.subTest(root_name=root_name):
                builder = ModelBuilder()
                builder.set_remote_files(remote_files)
                builder.set_local_files(local_files)
                builder.set_active_files([])
                model = builder.build_model()
                controller, _ = self._make_progressive_update_controller(
                    None, model_builder=builder, model=model,
                )
                controller._Controller__context.performance_diagnostics = PerformanceDiagnosticsCollector(
                    lambda: True,
                )
                controller._reserve_move_attempt = MagicMock(return_value=False)
                controller._Controller__persist.move_failure_counts = {
                    ModelFile.build_file_id(root_name, None): 1,
                }
                updater = ModelUpdater(controller)

                with patch.object(builder, "request_rebuild", wraps=builder.request_rebuild) as request_rebuild, \
                        patch.object(builder, "build_model", wraps=builder.build_model) as build_model:
                    updater.update()
                    updater.update()

                snapshot = controller._Controller__context.performance_diagnostics.snapshot()
                self.assertEqual(1, request_rebuild.call_count)
                self.assertEqual(1, build_model.call_count)
                self.assertEqual(1, snapshot["counters"].get(reason_counter, 0))
                other_counter = (
                    "model_rebuild_collision_retry"
                    if reason_counter == "model_rebuild_move_retry_due"
                    else "model_rebuild_move_retry_due"
                )
                self.assertEqual(0, snapshot["counters"].get(other_counter, 0))

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

    def test_update_records_fixed_choice_and_scan_cardinalities_without_scan_identity_labels(self):
        remote = ScannerResult(
            datetime.now(), [SystemFile("sample-remote-root", 1)],
            scanned_path_pair_ids={"path-pair-a"}, completed_path_pair_ids={"path-pair-a"},
        )
        local = ScannerResult(
            datetime.now(), [SystemFile("sample-local-root", 1), SystemFile("second", 1)],
            scanned_path_pair_ids={"path-pair-a"}, unknown_path_pair_ids={"path-pair-a"},
        )
        controller, builder = self._make_progressive_update_controller(remote, local)
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        controller._Controller__context.performance_diagnostics = diagnostics
        controller._Controller__work_state_lock = RLock()
        controller._Controller__stop_resume_trace_cycle_id = 0
        controller._Controller__model.file_count = 3
        controller._Controller__model.tree_file_count = 5
        builder.has_pending_active_transfer_delta.return_value = False
        builder.has_changes.return_value = False

        ModelUpdater(controller).update()

        snapshot = diagnostics.snapshot()
        self.assertEqual(1, snapshot["counters"]["model_update_choice_noop"])
        self.assertEqual(1, snapshot["counters"]["remote_scan_result_observations"])
        self.assertEqual(1, snapshot["counters"]["local_scan_result_observations"])
        self.assertEqual(1, snapshot["gauges"]["remote_scan_result_root_count"])
        self.assertEqual(2, snapshot["gauges"]["local_scan_result_root_count"])
        self.assertEqual(1, snapshot["gauges"]["remote_scan_result_completed_pair_count"])
        self.assertEqual(1, snapshot["gauges"]["local_scan_result_unknown_pair_count"])
        self.assertEqual(1, snapshot["durations"][DURATION_MODEL_UPDATE_LOCK_HOLD]["count"])
        self.assertNotIn("path-pair-a", str(snapshot))
        self.assertNotIn("sample-remote-root", str(snapshot))

    def test_full_build_and_sse_publication_share_authoritative_version_correlation(self):
        builder = ModelBuilder()
        first_root = SystemFile("root-a", 1)
        first_root.path_pair_id = "pair-a"
        second_root = SystemFile("root-b", 1)
        second_root.path_pair_id = "pair-b"
        builder.set_remote_files([first_root, second_root])
        controller, _ = self._make_progressive_update_controller(
            None, model_builder=builder, model=Model(),
        )
        controller._Controller__work_state_lock = RLock()
        controller._Controller__stop_resume_trace_cycle_id = 7
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)

        def record_breadcrumb(**kwargs):
            trace.record(
                "controller", kwargs["message"], kwargs["details"],
                stage=kwargs["stage"], event_type=kwargs["event_type"],
                corr_id=kwargs["corr_id"], flow_id=kwargs["flow_id"],
                trace_scope=kwargs["trace_scope"],
            )

        controller._Controller__record_breadcrumb = record_breadcrumb

        ModelUpdater(controller).update()
        controller.get_model_global_version = lambda: controller._Controller__model.version

        from web.handler.model_api import ModelApiHandler
        ModelApiHandler(controller, breadcrumb_trace=trace)._ModelApiHandler__sse(
            "scoped", "model-page", {}, controller._Controller__model.scope_version("pair-a"),
            controller._Controller__model.version,
        )
        entries = trace.snapshot()["entries"]
        completion = next(entry for entry in entries if entry["message"] == "model_build_completed")
        publication = next(entry for entry in entries if entry["message"] == "model_scoped_sse_published")
        self.assertEqual("model_update", completion["stage"])
        self.assertEqual({
            "model_version": 2, "model_root_count": 2, "model_tree_file_count": 2,
        }, completion["details"])
        self.assertEqual("model_version:2", completion["corr_id"])
        self.assertEqual(completion["corr_id"], completion["flow_id"])
        self.assertEqual(completion["corr_id"], publication["corr_id"])
        self.assertEqual(completion["flow_id"], publication["flow_id"])
        self.assertEqual({"model_version": 2, "scope_version": 1}, publication["details"])

    def test_disabled_choice_attribution_does_not_read_model_output_cardinality(self):
        class CountingModel(Model):
            def __init__(self):
                super().__init__()
                self.file_count_reads = 0
                self.tree_file_count_reads = 0

            @property
            def file_count(self):
                self.file_count_reads += 1
                return super().file_count

            @property
            def tree_file_count(self):
                self.tree_file_count_reads += 1
                return super().tree_file_count

        model = CountingModel()
        controller, builder = self._make_progressive_update_controller(None, model=model)
        controller._Controller__context.performance_diagnostics = PerformanceDiagnosticsCollector(lambda: False)
        controller._Controller__work_state_lock = RLock()
        controller._Controller__stop_resume_trace_cycle_id = 0
        builder.has_pending_active_transfer_delta.return_value = False
        builder.has_changes.return_value = False

        ModelUpdater(controller).update()

        self.assertEqual(0, model.file_count_reads)
        self.assertEqual(0, model.tree_file_count_reads)

    def test_disabled_scan_attribution_skips_collector_cardinality_publication(self):
        scan = ScannerResult(
            datetime.now(), [SystemFile("sample-root", 1)],
            scanned_path_pair_ids={"path-pair-a"}, completed_path_pair_ids={"path-pair-a"},
        )
        controller, builder = self._make_progressive_update_controller(scan, scan)
        diagnostics = MagicMock()
        diagnostics.is_enabled.return_value = False
        controller._Controller__context.performance_diagnostics = diagnostics
        controller._Controller__work_state_lock = RLock()
        controller._Controller__stop_resume_trace_cycle_id = 0
        builder.has_pending_active_transfer_delta.return_value = False
        builder.has_changes.return_value = False

        ModelUpdater(controller).update()

        diagnostics.set_gauges.assert_not_called()
        diagnostics.increment.assert_not_called()

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

    def test_eager_local_accumulator_preserves_move_token_until_first_session_binding(self):
        controller = SimpleNamespace()
        updater = ModelUpdater(controller)
        accumulator = controller._Controller__progressive_local_scan_state
        accumulator.apply([self._local_full_snapshot([SystemFile("moved", 1)], 56)])
        token = updater.begin_final_move_local_root_invalidation("moved", "pair", 58)
        accumulator.set_session_token("first-session")

        first_session_scan = self._local_full_snapshot([SystemFile("other", 1)], 59)
        first_session_scan.session_token = "first-session"
        accumulator.apply([first_session_scan])
        self.assertIn(("pair", "moved"), accumulator.snapshot())

        accumulator.set_session_token("replacement-session")
        replacement_scan = self._local_full_snapshot([SystemFile("other", 1)], 1)
        replacement_scan.session_token = "replacement-session"
        accumulator.apply([replacement_scan])
        self.assertNotIn(("pair", "moved"), accumulator.snapshot())
        self.assertIsNotNone(token)

    def test_eager_move_token_rebinds_to_replaced_process_before_first_result(self):
        old_process = object.__new__(ScannerProcess)
        old_process._ScannerProcess__session_token = "old-session"
        controller = SimpleNamespace(_Controller__local_scan_process=old_process)
        updater = ModelUpdater(controller)
        accumulator = controller._Controller__progressive_local_scan_state
        self.assertEqual("old-session", accumulator.session_token)

        old_token = updater.begin_final_move_local_root_invalidation("old-root", "pair", 58)
        replacement_process = object.__new__(ScannerProcess)
        replacement_process._ScannerProcess__session_token = "replacement-session"
        controller._Controller__local_scan_process = replacement_process
        replacement_token = updater.begin_final_move_local_root_invalidation("new-root", "pair", 1)

        self.assertEqual("replacement-session", accumulator.session_token)
        invalidations = accumulator._ProgressiveScanAccumulator__move_invalidations_by_root
        self.assertNotIn(("pair", "old-root"), invalidations)
        self.assertIn(replacement_token, invalidations[("pair", "new-root")])
        updater.finish_final_move_local_root_invalidation("old-root", "pair", old_token, True)
        self.assertIn(replacement_token, invalidations[("pair", "new-root")])

        fresh = SystemFile("fresh-root", 1)
        fresh.path_pair_id = "pair"
        replacement_process.pop_results = MagicMock(return_value=[ScannerResult(
            datetime.now(), [fresh], scanned_path_pair_ids={"pair"}, generation=2,
            is_progress=True, is_scan_final=True, is_full_snapshot=True,
            full_snapshot_path_pair_ids={"pair"}, completed_path_pair_ids={"pair"},
            session_token="replacement-session",
        )])
        _pop_scan_updates(controller, "local", replacement_process)
        self.assertEqual({("pair", "fresh-root")}, set(accumulator.snapshot()))
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

    @staticmethod
    def _local_full_snapshot(files, generation, pair_id="pair"):
        for file in files:
            file.path_pair_id = pair_id
        return ScannerResult(
            datetime.now(), files, scanned_path_pair_ids={pair_id}, generation=generation,
            is_progress=True, root_names={file.name for file in files},
            completed_path_pair_ids={pair_id}, is_full_snapshot=True,
            full_snapshot_path_pair_ids={pair_id},
        )

    def test_progressive_accumulator_invalidates_mixed_pre_move_generations_per_root(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([self._local_full_snapshot(
            [SystemFile("moved", 1), SystemFile("unrelated", 1), SystemFile("removed", 1)], 56,
        )])
        token = accumulator.begin_root_invalidation("pair", "moved", 58)

        result = accumulator.apply([
            self._local_full_snapshot([SystemFile("unrelated", 2)], 57),
            self._local_full_snapshot([SystemFile("unrelated", 3)], 58),
        ])

        files = {file.name: file for file in result.files}
        self.assertEqual(1, files["moved"].size)
        self.assertEqual(3, files["unrelated"].size)
        self.assertNotIn("removed", files)
        self.assertEqual(1, accumulator.authority()[("pair", "moved")].size)

        accumulator.finish_root_invalidation("pair", "moved", token, True)
        post_move = accumulator.apply([self._local_full_snapshot(
            [SystemFile("moved", 4), SystemFile("unrelated", 3)], 59,
        )])
        self.assertEqual(4, {file.name: file for file in post_move.files}["moved"].size)

        deletion = accumulator.apply([self._local_full_snapshot([SystemFile("unrelated", 3)], 60)])
        self.assertNotIn("moved", {file.name: file for file in deletion.files})

    def test_progressive_accumulator_post_move_lossless_absence_clears_invalidation(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([self._local_full_snapshot([SystemFile("moved", 1)], 56)])
        token = accumulator.begin_root_invalidation("pair", "moved", 58)
        accumulator.finish_root_invalidation("pair", "moved", token, True)

        deletion = accumulator.apply([self._local_full_snapshot([], 59)])

        self.assertNotIn("moved", {file.name: file for file in deletion.files})
        self.assertNotIn(("pair", "moved"), accumulator.authority())

    def test_progressive_accumulator_failed_overlapping_move_keeps_prior_token(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([self._local_full_snapshot([SystemFile("moved", 1)], 56)])
        first = accumulator.begin_root_invalidation("pair", "moved", 58)
        second = accumulator.begin_root_invalidation("pair", "moved", 59)
        accumulator.finish_root_invalidation("pair", "moved", second, False)

        stale = accumulator.apply([self._local_full_snapshot([], 58)])
        self.assertIn("moved", {file.name: file for file in stale.files})
        accumulator.finish_root_invalidation("pair", "moved", first, True)
        accumulator.apply([self._local_full_snapshot([SystemFile("moved", 2)], 59)])
        self.assertEqual(2, accumulator.snapshot()[("pair", "moved")].size)
        self.assertIsNotNone(first)

    def test_progressive_accumulator_pending_move_token_survives_healthy_post_generation(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([self._local_full_snapshot([SystemFile("moved", 1)], 56)])
        token = accumulator.begin_root_invalidation("pair", "moved", 58)

        before_finish = accumulator.apply([self._local_full_snapshot([], 59)])
        self.assertIn("moved", {file.name: file for file in before_finish.files})

        accumulator.finish_root_invalidation("pair", "moved", token, True)
        after_finish = accumulator.apply([self._local_full_snapshot([], 60)])
        self.assertNotIn("moved", {file.name: file for file in after_finish.files})

    def test_progressive_accumulator_committed_token_survives_failed_and_incomplete_generations(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([self._local_full_snapshot([SystemFile("moved", 1)], 56)])
        token = accumulator.begin_root_invalidation("pair", "moved", 58)
        accumulator.finish_root_invalidation("pair", "moved", token, True)

        accumulator.apply([ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=59,
            is_progress=True, failed=True, unknown_path_pair_ids={"pair"},
        )])
        accumulator.apply([ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=60,
            is_progress=True, root_names={"moved"}, unknown_path_pair_ids={"pair"},
        )])

        self.assertIn(("pair", "moved"), accumulator.snapshot())
        tokens = accumulator._ProgressiveScanAccumulator__move_invalidations_by_root
        self.assertEqual((58, "committed"), tokens[("pair", "moved")][token])

        accumulator.apply([self._local_full_snapshot([], 61)])
        self.assertNotIn(("pair", "moved"), accumulator.snapshot())
        self.assertNotIn(("pair", "moved"), accumulator._ProgressiveScanAccumulator__move_invalidations_by_root)

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

    def test_progressive_accumulator_terminal_failure_wins_over_recoverable_pair_evidence(self):
        accumulator = _ProgressiveScanAccumulator()

        result = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"},
                recoverable_failure_path_pair_ids={"pair-b"},
            ),
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"},
            ),
        ])

        self.assertTrue(result.failed)
        self.assertEqual({"pair-b"}, result.unknown_path_pair_ids)
        self.assertEqual(set(), result.recoverable_failure_path_pair_ids)
        self.assertEqual({"pair-b"}, result.terminal_failure_path_pair_ids)

    def test_progressive_accumulator_terminal_failure_wins_when_it_precedes_recoverable_evidence(self):
        accumulator = _ProgressiveScanAccumulator()

        result = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"},
            ),
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"},
                recoverable_failure_path_pair_ids={"pair-b"},
            ),
        ])

        self.assertTrue(result.failed)
        self.assertEqual({"pair-b"}, result.unknown_path_pair_ids)
        self.assertEqual(set(), result.recoverable_failure_path_pair_ids)
        self.assertEqual({"pair-b"}, result.terminal_failure_path_pair_ids)

    def test_progressive_accumulator_identityless_terminal_clears_cross_tick_recoverable_pair(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"},
                recoverable_failure_path_pair_ids={"pair-b"},
            ),
        ])

        terminal = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={None}, generation=2,
                is_progress=True, failed=True, unknown_path_pair_ids={None},
            ),
        ])

        self.assertIn("pair-b", terminal.unknown_path_pair_ids)
        self.assertEqual(set(), terminal.recoverable_failure_path_pair_ids)
        builder = ModelBuilder()
        last_good = SystemFile("pair-b-file", 11)
        last_good.path_pair_id = "pair-b"
        builder.set_local_files([last_good])
        builder.record_local_inventory_completion({"pair-b"})
        builder.observe_local_scan_result(
            terminal.scanned_path_pair_ids,
            terminal.completed_path_pair_ids,
            terminal.unknown_path_pair_ids,
            terminal.failed,
            {"pair-b"},
            terminal.recoverable_failure_path_pair_ids,
            terminal.terminal_failure_path_pair_ids,
        )
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 11, "stale"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

    def test_progressive_accumulator_normalizes_anonymous_recoverable_and_terminal_evidence(self):
        configured = {"pair-a", "pair-b"}
        for events in (
            [
                ScannerResult(datetime.now(), [], scanned_path_pair_ids={None}, generation=1,
                              is_progress=True, failed=True, unknown_path_pair_ids={None},
                              recoverable_failure_path_pair_ids={None}),
                ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                              is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"}),
            ],
            [
                ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                              is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"}),
                ScannerResult(datetime.now(), [], scanned_path_pair_ids={None}, generation=1,
                              is_progress=True, failed=True, unknown_path_pair_ids={None},
                              recoverable_failure_path_pair_ids={None}),
            ],
        ):
            result = _ProgressiveScanAccumulator().apply(events, configured)
            self.assertEqual({"pair-a", "pair-b"}, result.unknown_path_pair_ids)
            self.assertEqual({"pair-a"}, result.recoverable_failure_path_pair_ids)
            self.assertNotIn(None, result.unknown_path_pair_ids)

    def test_progressive_accumulator_normalizes_cross_tick_anonymous_failure_to_incomplete_pair(self):
        configured = {"pair-a", "pair-b"}
        accumulator = _ProgressiveScanAccumulator()
        initial_a = SystemFile("pair-a-file", 7)
        initial_a.path_pair_id = "pair-a"
        initial_b = SystemFile("pair-b-file", 11)
        initial_b.path_pair_id = "pair-b"
        accumulator.apply([
            ScannerResult(datetime.now(), [initial_a, initial_b], scanned_path_pair_ids=configured,
                          generation=1, is_progress=True, completed_path_pair_ids=configured,
                          is_full_snapshot=True, full_snapshot_path_pair_ids=configured),
        ], configured)
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=2,
                          is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"},
                          recoverable_failure_path_pair_ids={"pair-b"}),
        ], configured)
        retry = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={None}, generation=3,
                          is_progress=True, failed=True, unknown_path_pair_ids={None},
                          recoverable_failure_path_pair_ids={None}),
        ], configured)
        self.assertEqual({"pair-b"}, retry.unknown_path_pair_ids)
        self.assertEqual({"pair-b"}, retry.recoverable_failure_path_pair_ids)
        self.assertNotIn(None, retry.unknown_path_pair_ids)
        terminal = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={None}, generation=4,
                          is_progress=True, failed=True, unknown_path_pair_ids={None}),
        ], configured)
        self.assertEqual({"pair-b"}, terminal.unknown_path_pair_ids)
        self.assertEqual(set(), terminal.recoverable_failure_path_pair_ids)
        self.assertIn(("pair-a", "pair-a-file"), accumulator.snapshot())

    def test_progressive_accumulator_normalizes_same_drain_completion_and_anonymous_retry(self):
        configured = {"pair-a", "pair-b"}
        accumulator = _ProgressiveScanAccumulator()
        healthy_a = SystemFile("pair-a-file", 8)
        healthy_a.path_pair_id = "pair-a"
        result = accumulator.apply([
            ScannerResult(datetime.now(), [healthy_a], scanned_path_pair_ids={"pair-a"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair-a"},
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"}),
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={None}, generation=1,
                          is_progress=True, failed=True, unknown_path_pair_ids={None},
                          recoverable_failure_path_pair_ids={None}),
        ], configured)
        self.assertEqual({"pair-a"}, result.completed_path_pair_ids)
        self.assertEqual({"pair-b"}, result.unknown_path_pair_ids)
        self.assertEqual({"pair-b"}, result.recoverable_failure_path_pair_ids)

    def test_progressive_accumulator_normalizes_nonprogress_terminal_over_recoverable_evidence(self):
        configured = {"pair-a", "pair-b"}
        result = _ProgressiveScanAccumulator().apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                          failed=True, unknown_path_pair_ids={"pair-b"},
                          recoverable_failure_path_pair_ids={"pair-b"}),
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={None}, generation=1,
                          failed=True, unknown_path_pair_ids={None}),
        ], configured)
        self.assertEqual({"pair-a", "pair-b"}, result.unknown_path_pair_ids)
        self.assertEqual(set(), result.recoverable_failure_path_pair_ids)
        self.assertNotIn(None, result.unknown_path_pair_ids)

    def test_progressive_accumulator_session_replacement_clears_recoverable_incomplete_pair(self):
        accumulator = _ProgressiveScanAccumulator()
        recoverable = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"},
                recoverable_failure_path_pair_ids={"pair-b"}, session_token="first-session",
            ),
        ])
        self.assertEqual({"pair-b"}, recoverable.recoverable_failure_path_pair_ids)

        accumulator.set_session_token("replacement-session")
        progress = accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("pair-b-file", 12)], scanned_path_pair_ids={"pair-b"},
                generation=1, is_progress=True, session_token="replacement-session",
            ),
        ])

        self.assertEqual({"pair-b"}, progress.unknown_path_pair_ids)
        self.assertEqual(set(), progress.recoverable_failure_path_pair_ids)

    def test_progressive_accumulator_authoritative_completion_clears_normalized_retry(self):
        configured = {"pair-a", "pair-b"}
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=1,
                          is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"},
                          recoverable_failure_path_pair_ids={"pair-b"}),
        ], configured)
        recovered = SystemFile("pair-b-recovered", 13)
        recovered.path_pair_id = "pair-b"
        result = accumulator.apply([
            ScannerResult(datetime.now(), [recovered], scanned_path_pair_ids={"pair-b"}, generation=2,
                          is_progress=True, completed_path_pair_ids={"pair-b"},
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-b"}),
        ], configured)
        self.assertEqual({"pair-b"}, result.completed_path_pair_ids)
        self.assertEqual(set(), result.unknown_path_pair_ids)
        self.assertEqual(set(), result.recoverable_failure_path_pair_ids)

    def test_legacy_healthy_full_result_stays_non_progress_for_joint_mode_selection(self):
        result = _ProgressiveScanAccumulator().apply([
            ScannerResult(datetime.now(), [SystemFile("legacy-root", 5)],
                          scanned_path_pair_ids={"pair-a"}, generation=1,
                          completed_path_pair_ids={"pair-a"}, is_full_snapshot=True,
                          full_snapshot_path_pair_ids={"pair-a"}),
        ], {"pair-a"})

        self.assertFalse(result.is_progress)
        self.assertTrue(result.is_scan_final)

    def test_targeted_identityless_legacy_recoverable_failure_is_retained_and_normalized(self):
        result = _ProgressiveScanAccumulator().apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids=set(), generation=1,
                          failed=True, is_targeted_scan=True, unknown_path_pair_ids={None},
                          recoverable_failure_path_pair_ids={None}),
        ], {"pair-a", "pair-b"})

        self.assertTrue(result.failed)
        self.assertEqual({"pair-a", "pair-b"}, result.unknown_path_pair_ids)
        self.assertEqual({"pair-a", "pair-b"}, result.recoverable_failure_path_pair_ids)

    def test_stale_completion_does_not_exclude_incomplete_pair_from_anonymous_terminal(self):
        accumulator = _ProgressiveScanAccumulator()
        configured = {"pair-a", "pair-b"}
        accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=2,
                          is_progress=True, failed=True, unknown_path_pair_ids={"pair-a"},
                          recoverable_failure_path_pair_ids={"pair-a"}),
        ], configured)

        result = accumulator.apply([
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=1,
                          is_progress=True, completed_path_pair_ids={"pair-a"},
                          is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"}),
            ScannerResult(datetime.now(), [], scanned_path_pair_ids={None}, generation=3,
                          is_progress=True, failed=True, unknown_path_pair_ids={None}),
        ], configured)

        self.assertEqual({"pair-a"}, result.unknown_path_pair_ids)
        self.assertEqual(set(), result.recoverable_failure_path_pair_ids)

    def test_legacy_explicit_unknown_pair_is_not_broadened_to_configured_scopes(self):
        configured = {"pair-a", "pair-b"}
        for recoverable in (set(), {"pair-b"}):
            result = _ProgressiveScanAccumulator().apply([
                ScannerResult(datetime.now(), [], scanned_path_pair_ids=set(), generation=1,
                              failed=True, unknown_path_pair_ids={"pair-b"},
                              recoverable_failure_path_pair_ids=recoverable),
            ], configured)
            self.assertEqual({"pair-b"}, result.unknown_path_pair_ids)
            self.assertEqual(recoverable, result.recoverable_failure_path_pair_ids)

    def test_non_authoritative_completion_marker_does_not_exclude_anonymous_failure_targets(self):
        configured = {"pair-a", "pair-b"}
        marker = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=1,
            is_progress=True, completed_path_pair_ids={"pair-a"},
        )
        for recoverable in (False, True):
            anonymous = ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={None}, generation=1,
                is_progress=True, failed=True, unknown_path_pair_ids={None},
                recoverable_failure_path_pair_ids={None} if recoverable else set(),
            )
            for events in ((marker, anonymous), (anonymous, marker)):
                result = _ProgressiveScanAccumulator().apply(events, configured)
                self.assertEqual({"pair-a", "pair-b"}, result.unknown_path_pair_ids)
                self.assertEqual(
                    {"pair-a", "pair-b"} if recoverable else set(),
                    result.recoverable_failure_path_pair_ids,
                )

    def test_newer_explicit_failure_revokes_prior_authoritative_completion_for_anonymous_evidence(self):
        configured = {"pair-a", "pair-b"}
        completed = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=1,
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_full_snapshot=True,
            full_snapshot_path_pair_ids={"pair-a"},
        )
        for explicit_recoverable, anonymous_recoverable in ((False, True), (True, False)):
            explicit_failure = ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=2,
                is_progress=True, failed=True, unknown_path_pair_ids={"pair-a"},
                recoverable_failure_path_pair_ids={"pair-a"} if explicit_recoverable else set(),
            )
            anonymous = ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={None}, generation=2,
                is_progress=True, failed=True, unknown_path_pair_ids={None},
                recoverable_failure_path_pair_ids={None} if anonymous_recoverable else set(),
            )
            result = _ProgressiveScanAccumulator().apply(
                (completed, explicit_failure, anonymous), configured,
            )
            self.assertEqual({"pair-a", "pair-b"}, result.unknown_path_pair_ids)
            self.assertNotIn("pair-a", result.completed_path_pair_ids)
            self.assertEqual(
                {"pair-b"} if anonymous_recoverable and not explicit_recoverable else set(),
                result.recoverable_failure_path_pair_ids,
            )

    def test_authoritative_completion_excludes_only_its_pair_from_anonymous_failure_both_orders(self):
        configured = {"pair-a", "pair-b"}
        completed = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=1,
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_full_snapshot=True,
            full_snapshot_path_pair_ids={"pair-a"},
        )
        for recoverable in (False, True):
            anonymous = ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={None}, generation=1,
                is_progress=True, failed=True, unknown_path_pair_ids={None},
                recoverable_failure_path_pair_ids={None} if recoverable else set(),
            )
            for events in ((completed, anonymous), (anonymous, completed)):
                result = _ProgressiveScanAccumulator().apply(events, configured)
                self.assertEqual({"pair-a"}, result.completed_path_pair_ids)
                self.assertEqual({"pair-b"}, result.unknown_path_pair_ids)
                self.assertEqual(
                    {"pair-b"} if recoverable else set(),
                    result.recoverable_failure_path_pair_ids,
                )

    def test_invalid_unchanged_marker_revokes_nominal_completion_before_anonymous_failure_truth(self):
        configured = {"pair-a", "pair-b"}
        for recoverable in (False, True):
            for reverse_order in (False, True):
                accumulator = _ProgressiveScanAccumulator()
                original = SystemFile("root", 3)
                original.path_pair_id = "pair-a"
                accumulator.apply([
                    ScannerResult(
                        datetime.now(), [original], scanned_path_pair_ids={"pair-a"}, generation=1,
                        is_progress=True, completed_path_pair_ids={"pair-a"}, is_full_snapshot=True,
                        full_snapshot_path_pair_ids={"pair-a"},
                    ),
                ], configured)
                invalid_full_completion = ScannerResult(
                    datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=2,
                    is_progress=True, completed_path_pair_ids={"pair-a"}, is_full_snapshot=True,
                    full_snapshot_path_pair_ids={"pair-a"},
                    unchanged_root_fingerprints_by_pair={"pair-a": {"root": "invalid"}},
                )
                anonymous = ScannerResult(
                    datetime.now(), [], scanned_path_pair_ids={None}, generation=2,
                    is_progress=True, failed=True, unknown_path_pair_ids={None},
                    recoverable_failure_path_pair_ids={None} if recoverable else set(),
                )
                events = (anonymous, invalid_full_completion) if reverse_order else \
                    (invalid_full_completion, anonymous)
                result = accumulator.apply(events, configured)
                self.assertNotIn("pair-a", result.completed_path_pair_ids)
                self.assertEqual({"pair-a", "pair-b"}, result.unknown_path_pair_ids)
                self.assertEqual(
                    {"pair-b"} if recoverable else set(),
                    result.recoverable_failure_path_pair_ids,
                )

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

    def test_progressive_accumulator_unchanged_new_generation_has_no_delta_keys(self):
        accumulator = _ProgressiveScanAccumulator()
        initial = SystemFile("unchanged.bin", 3)
        accumulator.apply([
            ScannerResult(
                datetime.now(), [initial], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])

        unchanged = SystemFile("unchanged.bin", 3)
        accumulator.apply([
            ScannerResult(
                datetime.now(), [unchanged], scanned_path_pair_ids={"pair"}, generation=2,
                is_progress=True, session_token="session",
            ),
        ])

        self.assertEqual(set(), accumulator.touched_keys())
        self.assertEqual({("pair", "unchanged.bin")}, set(accumulator.snapshot()))

        changed = SystemFile("unchanged.bin", 4)
        accumulator.apply([
            ScannerResult(
                datetime.now(), [changed], scanned_path_pair_ids={"pair"}, generation=2,
                is_progress=True, session_token="session",
            ),
        ])
        self.assertEqual({("pair", "unchanged.bin")}, accumulator.touched_keys())

    def test_progressive_accumulator_retains_only_matching_unchanged_root_marker(self):
        accumulator = _ProgressiveScanAccumulator()
        original = SystemFile("root", 3)
        accumulator.apply([
            ScannerResult(
                datetime.now(), [original], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        accepted = accumulator.accepted_root_fingerprints()["pair"]["root"]

        with patch("controller.model_updater.stream_root_fingerprint") as fingerprint:
            retained = accumulator.apply([
                ScannerResult(
                    datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                    is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                    is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
                    unchanged_root_fingerprints_by_pair={"pair": {"root": accepted}},
                ),
            ])
            fingerprint.assert_not_called()
        self.assertFalse(retained.failed)
        self.assertEqual({("pair", "root")}, set(accumulator.snapshot()))
        self.assertEqual(set(), accumulator.touched_keys())

        rejected = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=3,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
                unchanged_root_fingerprints_by_pair={"pair": {"root": "0" * 64}},
            ),
        ])
        self.assertTrue(rejected.failed)
        self.assertEqual({("pair", "root")}, set(accumulator.snapshot()))
        self.assertEqual({"pair"}, accumulator.incomplete_pairs())
        self.assertNotIn("pair", accumulator.accepted_root_fingerprints())

        recovered = accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root", 3)], scanned_path_pair_ids={"pair"}, generation=4,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        self.assertFalse(recovered.failed)
        self.assertNotIn("pair", accumulator.incomplete_pairs())
        self.assertIn("pair", accumulator.accepted_root_fingerprints())

    def test_progressive_accumulator_clears_root_fingerprints_on_new_session(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root", 3)], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="first",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        self.assertTrue(accumulator.accepted_root_fingerprints())
        accumulator.set_session_token("restarted")
        self.assertEqual({}, accumulator.accepted_root_fingerprints())

    def test_progressive_accumulator_failed_hinted_generation_forces_full_retry(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root", 3)], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        self.assertIn("pair", accumulator.accepted_root_fingerprints())

        failed = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                is_progress=True, failed=True, error_message="invalid hinted stream",
                unknown_path_pair_ids={"pair"}, session_token="session",
            ),
        ])
        self.assertTrue(failed.failed)
        self.assertNotIn("pair", accumulator.accepted_root_fingerprints())
        self.assertEqual({("pair", "root")}, set(accumulator.snapshot()))

        recovered = accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root", 3)], scanned_path_pair_ids={"pair"}, generation=3,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        self.assertFalse(recovered.failed)
        self.assertIn("pair", accumulator.accepted_root_fingerprints())

    def test_progressive_accumulator_pre_manifest_failure_clears_all_hints(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root-a", 1)], scanned_path_pair_ids={"pair-a"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair-a"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
            ),
            ScannerResult(
                datetime.now(), [SystemFile("root-b", 1)], scanned_path_pair_ids={"pair-b"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair-b"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-b"},
            ),
        ])
        self.assertEqual({"pair-a", "pair-b"}, set(accumulator.accepted_root_fingerprints()))

        failed = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids=set(), generation=2,
            failed=True, error_message="failure before manifest", session_token="session",
        )
        result = accumulator.apply([failed])

        self.assertIsNot(failed, result)
        self.assertTrue(result.failed)
        self.assertEqual({}, accumulator.accepted_root_fingerprints())
        self.assertEqual({("pair-a", "root-a"), ("pair-b", "root-b")}, set(accumulator.snapshot()))

    def test_progressive_accumulator_ignores_stale_unchanged_root_marker(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root", 3)], scanned_path_pair_ids={"pair"}, generation=2,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        accepted = accumulator.accepted_root_fingerprints()["pair"]["root"]

        result = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
                unchanged_root_fingerprints_by_pair={"pair": {"root": accepted}},
            ),
        ])

        self.assertFalse(result.failed)
        self.assertEqual(set(), result.scanned_path_pair_ids)
        self.assertEqual({("pair", "root")}, set(accumulator.snapshot()))

    def test_progressive_full_snapshot_touches_only_actual_pair_changes(self):
        accumulator = _ProgressiveScanAccumulator()
        original = [
            SystemFile("removed.bin", 1),
            SystemFile("changed.bin", 2),
            SystemFile("same.bin", 3),
        ]
        accumulator.apply([
            ScannerResult(
                datetime.now(), original, scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        unchanged = [SystemFile("removed.bin", 1), SystemFile("changed.bin", 2), SystemFile("same.bin", 3)]
        accumulator.apply([
            ScannerResult(
                datetime.now(), unchanged, scanned_path_pair_ids={"pair"}, generation=2,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        self.assertEqual(set(), accumulator.touched_keys())

        updated = [
            SystemFile("changed.bin", 4),
            SystemFile("same.bin", 3),
            SystemFile("added.bin", 5),
        ]
        accumulator.apply([
            ScannerResult(
                datetime.now(), updated, scanned_path_pair_ids={"pair"}, generation=3,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        self.assertEqual(
            {("pair", "removed.bin"), ("pair", "changed.bin"), ("pair", "added.bin")},
            accumulator.touched_keys(),
        )
        self.assertEqual(
            {("pair", "changed.bin"), ("pair", "same.bin"), ("pair", "added.bin")},
            set(accumulator.snapshot()),
        )
        self.assertEqual(
            {("pair", "changed.bin"), ("pair", "same.bin"), ("pair", "added.bin")},
            set(accumulator.authority()),
        )

    def test_progressive_accumulator_proves_repeated_empty_full_snapshot(self):
        accumulator = _ProgressiveScanAccumulator()
        for generation in (1, 2):
            accumulator.apply([
                ScannerResult(
                    datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=generation,
                    is_progress=True, completed_path_pair_ids={"pair"}, session_token="session",
                    is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
                ),
            ])
            self.assertEqual(set(), accumulator.touched_keys())
            self.assertEqual(set(), set(accumulator.snapshot()))
            self.assertEqual(
                set() if generation == 1 else {"pair"},
                accumulator.final_comparison_proven_pairs(),
            )

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

    def test_identityless_local_scan_failure_passes_all_enabled_scopes_to_inventory(self):
        """An unattributed failure is not evidence that other configured roots are healthy."""
        failed_local_scan = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={None}, failed=True,
        )
        failed_local_scan.recoverable_failure_path_pair_ids = {None}
        controller, builder = self._make_progressive_update_controller(None, failed_local_scan)
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}

        ModelUpdater(controller).update()

        builder.observe_local_scan_result.assert_called_once_with(
            {None}, set(), set(), True, {"pair-a", "pair-b"}, {None}, None,
        )

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
            model_builder.has_complete_local_coverage.return_value = False
            model_builder.has_unresolved_staging_collision.return_value = False
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

    def test_unchanged_remote_progressive_chunk_skips_delta_builder(self):
        remote_token = "remote-unchanged-chunk"
        local_token = "local-standing-authority"
        initial_remote = self._progressive_final_result(remote_token)
        initial_local = self._progressive_final_result(local_token)
        unchanged_remote = ScannerResult(
            datetime.now(), [SystemFile("root", 1)], scanned_path_pair_ids={None}, generation=2,
            is_progress=True, session_token=remote_token,
        )
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], [unchanged_remote]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], []],
        )
        updater = ModelUpdater(controller)

        updater.update()
        model_builder.reset_mock()
        updater.update()

        model_builder.build_progressive_roots.assert_not_called()
        model_builder.build_model.assert_not_called()
        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()

    def test_unchanged_remote_full_snapshot_skips_renderer_and_full_build(self):
        remote_token = "remote-unchanged-final"
        local_token = "local-standing-final"
        initial_remote = self._progressive_final_result(remote_token)
        initial_local = self._progressive_final_result(local_token)
        unchanged_remote = self._progressive_final_result(remote_token, generation=2)
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], [unchanged_remote]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], []],
        )
        updater = ModelUpdater(controller)

        updater.update()
        model_builder.reset_mock()
        updater.update()

        model_builder.build_progressive_roots.assert_not_called()
        model_builder.build_model.assert_not_called()

    def test_unchanged_completed_generation_skips_declared_pair_publication(self):
        remote_token = "remote-unchanged-declared-final"
        local_token = "local-standing-declared-final"
        initial_remote = self._progressive_final_result(remote_token)
        initial_local = self._progressive_final_result(local_token)
        unchanged_remote = self._progressive_final_result(remote_token, generation=2)
        builder = ModelBuilder()
        model = Model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False, model_builder=builder, model=model,
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], [unchanged_remote]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], []],
        )
        updater = ModelUpdater(controller)

        updater.update()
        builder.build_authoritative_pair_roots = MagicMock(
            wraps=builder.build_authoritative_pair_roots,
        )
        builder.build_model = MagicMock(wraps=builder.build_model)
        builder.set_local_files = MagicMock(wraps=builder.set_local_files)
        builder.set_remote_files = MagicMock(wraps=builder.set_remote_files)
        updater.update()

        builder.build_authoritative_pair_roots.assert_not_called()
        builder.build_model.assert_not_called()
        builder.set_local_files.assert_not_called()
        builder.set_remote_files.assert_not_called()
        self.assertTrue(controller._Controller__progressive_joint_authoritative)
        self.assertTrue(controller._Controller__last_local_reconciliation_healthy)
        self.assertTrue(controller._Controller__last_remote_reconciliation_healthy)

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

    def test_completed_pair_final_replaces_only_that_pair_without_global_build(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        idle = SystemFile("idle.bin", 20, False)
        idle.path_pair_id = "pair-b"
        builder = ModelBuilder()
        builder.set_local_files([old, idle])
        builder.set_remote_files([old, idle])
        # Pair-b remains unavailable throughout this selected pair-a final;
        # retaining the same overlay makes candidate reuse provable.
        builder.set_unknown_local_path_pair_ids({"pair-b"})
        live_model = builder.build_model()
        idle_before = live_model.get_file(ModelFile.build_file_id("idle.bin", "pair-b"))
        builder.build_model = MagicMock(wraps=builder.build_model)
        builder.build_authoritative_pair_roots = MagicMock(wraps=builder.build_authoritative_pair_roots)
        next_local = SystemFile("new.bin", 30, False)
        next_local.path_pair_id = "pair-a"
        next_remote = SystemFile("new.bin", 30, False)
        next_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [next_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [next_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._refresh_model_file_command_identities_locked = MagicMock()
        controller._Controller__context.performance_diagnostics = PerformanceDiagnosticsCollector(lambda: True)

        ModelUpdater(controller).update()

        builder.build_authoritative_pair_roots.assert_called_once()

        self.assertEqual(
            {ModelFile.build_file_id("new.bin", "pair-a"), ModelFile.build_file_id("idle.bin", "pair-b")},
            live_model.get_file_ids(),
        )
        self.assertIs(idle_before, live_model.get_file(ModelFile.build_file_id("idle.bin", "pair-b")))
        builder.build_model.assert_not_called()
        self.assertFalse(builder.has_changes())
        counters = controller._Controller__context.performance_diagnostics.snapshot()["counters"]
        self.assertEqual(1, counters["model_update_choice_progressive"])
        self.assertEqual(0, counters["model_update_choice_active"])
        self.assertEqual(0, counters["model_update_choice_full"])

    def test_targeted_progressive_final_clears_stale_inventory_when_roots_are_unchanged(self):
        """A selected pair completion publishes even when its roots match standing authority."""
        builder = ModelBuilder()

        def scan_result(pair_ids, generation, session_token, *, targeted=False):
            files = []
            for path_pair_id, name, size in pair_ids:
                file = SystemFile(name, size)
                file.path_pair_id = path_pair_id
                files.append(file)
            ids = {file.path_pair_id for file in files}
            return ScannerResult(
                datetime.now(), files, scanned_path_pair_ids=ids, generation=generation,
                is_progress=True, completed_path_pair_ids=ids, is_scan_final=True,
                is_full_snapshot=True, full_snapshot_path_pair_ids=ids,
                is_targeted_scan=targeted,
                session_token=session_token,
            )

        baseline_pairs = [
            ("pair-a", "same-a.bin", 7),
            ("pair-b", "same-b.bin", 11),
        ]
        baseline_local = scan_result(baseline_pairs, 1, "local-targeted")
        baseline_remote = scan_result(baseline_pairs, 1, "remote-targeted")
        targeted_local = scan_result(
            [("pair-a", "same-a.bin", 7)], 2, "local-targeted", targeted=True,
        )
        targeted_remote = scan_result(
            [("pair-a", "same-a.bin", 7)], 2, "remote-targeted", targeted=True,
        )
        targeted_remote_partial = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=2,
            is_progress=True, is_targeted_scan=True, session_token="remote-targeted",
        )
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False, model_builder=builder, model=Model(),
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        # The remote selected generation starts first, local completes while
        # that remote view is still incomplete, and the remote-only final is
        # the joint publication boundary.
        controller._Controller__remote_scan_process = self._progressive_process(
            "remote-targeted", [[baseline_remote], [targeted_remote_partial], [], [targeted_remote]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            "local-targeted", [[baseline_local], [], [targeted_local], []],
        )
        updater = ModelUpdater(controller)
        updater.update()
        builder.build_authoritative_pair_roots = MagicMock(
            wraps=builder.build_authoritative_pair_roots,
        )
        updater.update()
        updater.update()
        _, before_final = builder.local_library_inventory_snapshot()
        self.assertIn(before_final["pair-a"].state, {"scanning", "stale"})

        updater.update()

        builder.build_authoritative_pair_roots.assert_called_once()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 7, "up_to_date"), (
            inventory["pair-a"].file_count, inventory["pair-a"].size, inventory["pair-a"].state,
        ))
        self.assertEqual((1, 11, "up_to_date"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

    def test_multi_pair_progressive_final_completes_local_inventory_after_full_source_publication(self):
        """A healthy multi-pair final uses whole-source publication, not the single-pair delta."""
        builder = ModelBuilder()
        local_a = SystemFile("local-a.bin", 7)
        local_a.path_pair_id = "pair-a"
        local_b = SystemFile("local-b.bin", 11)
        local_b.path_pair_id = "pair-b"
        remote_a = SystemFile("local-a.bin", 7)
        remote_a.path_pair_id = "pair-a"
        remote_b = SystemFile("local-b.bin", 11)
        remote_b.path_pair_id = "pair-b"
        local_final = ScannerResult(
            datetime.now(), [local_a, local_b], scanned_path_pair_ids={"pair-a", "pair-b"},
            is_progress=True, completed_path_pair_ids={"pair-a", "pair-b"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a", "pair-b"},
            session_token="local-multi",
        )
        remote_final = ScannerResult(
            datetime.now(), [remote_a, remote_b], scanned_path_pair_ids={"pair-a", "pair-b"},
            is_progress=True, completed_path_pair_ids={"pair-a", "pair-b"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a", "pair-b"},
            session_token="remote-multi",
        )
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False, model_builder=builder, model=Model(),
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        # The local scanner completes first.  The later remote-only tick is
        # the joint publication boundary, so no current local event is
        # available to supply completion scope.
        controller._Controller__remote_scan_process = self._progressive_process(
            "remote-multi", [[], [remote_final]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            "local-multi", [[local_final], []],
        )

        ModelUpdater(controller).update()
        ModelUpdater(controller).update()

        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 7, "up_to_date"), (
            inventory["pair-a"].file_count, inventory["pair-a"].size, inventory["pair-a"].state,
        ))
        self.assertEqual((1, 11, "up_to_date"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

    def test_two_pair_progressive_scanner_chronology_rehabilitates_stale_matching_inventory(self):
        """Per-pair waves and final aggregates must clear stale inventory on both pairs."""
        configured = {"pair-a", "pair-b"}

        def file_for(pair_id):
            file = SystemFile("{}-root".format(pair_id), 7 if pair_id == "pair-a" else 11)
            file.path_pair_id = pair_id
            return file

        def scanner_events(session_token):
            files = {pair_id: file_for(pair_id) for pair_id in configured}
            per_pair = []
            for pair_id in ("pair-a", "pair-b"):
                per_pair.append(ScannerResult(
                    datetime.now(), [files[pair_id]], scanned_path_pair_ids={pair_id}, generation=1,
                    is_progress=True, is_scan_final=False, root_names={files[pair_id].name},
                    session_token=session_token,
                ))
                per_pair.append(ScannerResult(
                    datetime.now(), [files[pair_id]], scanned_path_pair_ids={pair_id}, generation=1,
                    is_progress=True, completed_path_pair_ids={pair_id}, is_full_snapshot=True,
                    full_snapshot_path_pair_ids={pair_id}, session_token=session_token,
                ))
            aggregate = ScannerResult(
                datetime.now(), list(files.values()), scanned_path_pair_ids=set(configured), generation=1,
                is_progress=True, completed_path_pair_ids=set(configured), is_full_snapshot=True,
                full_snapshot_path_pair_ids=set(configured), session_token=session_token,
            )
            return per_pair, aggregate

        # Cover fully coalesced delivery and staggered local/remote aggregate
        # arrival, the two paths observed from spawned ScannerProcess queues.
        for scenario, (local_batches, remote_batches) in enumerate((
            (lambda local, aggregate: [[*local, aggregate]],
             lambda remote, aggregate: [[*remote, aggregate]]),
            (lambda local, aggregate: [[*local[:2]], [*local[2:], aggregate], []],
             lambda remote, aggregate: [[], [*remote[:2]], [*remote[2:]], [aggregate]]),
            (lambda local, aggregate: [[], [*local[:2]], [*local[2:]], [aggregate]],
             lambda remote, aggregate: [[*remote[:2]], [*remote[2:], aggregate], []]),
        ), start=1):
          with self.subTest(scenario=scenario):
            builder = ModelBuilder()
            stale_local = [file_for(pair_id) for pair_id in configured]
            stale_remote = [file_for(pair_id) for pair_id in configured]
            builder.set_local_files(stale_local)
            builder.set_remote_files(stale_remote)
            builder.record_local_inventory_completion(configured)
            builder.observe_local_scan_result(set(), set(), configured, True, configured)
            local_events, local_aggregate = scanner_events("local-healthy")
            remote_events, remote_aggregate = scanner_events("remote-healthy")
            controller, _ = self._make_progressive_update_controller(
                None, local_scan=None, authoritative=False, model_builder=builder, model=Model(),
            )
            controller._Controller__path_pairs_by_id = {
                "pair-a": MagicMock(), "pair-b": MagicMock(),
            }
            local_delivery = list(local_batches(local_events, local_aggregate))
            remote_delivery = list(remote_batches(remote_events, remote_aggregate))
            local_delivery.extend([[]] * (4 - len(local_delivery)))
            remote_delivery.extend([[]] * (4 - len(remote_delivery)))
            controller._Controller__local_scan_process = self._progressive_process(
                "local-healthy", local_delivery,
            )
            controller._Controller__remote_scan_process = self._progressive_process(
                "remote-healthy", remote_delivery,
            )
            updater = ModelUpdater(controller)
            states = []
            for _ in range(4):
                updater.update()
                _, current_inventory = builder.local_library_inventory_snapshot()
                states.append({
                    pair_id: current_inventory[pair_id].state for pair_id in sorted(configured)
                })
            _, inventory = builder.local_library_inventory_snapshot()
            self.assertEqual(
                "up_to_date", inventory["pair-a"].state,
                "scenario {}; states={}".format(scenario, states),
            )
            self.assertEqual(
                "up_to_date", inventory["pair-b"].state,
                "scenario {}; states={}".format(scenario, states),
            )

    def test_progressive_recoverable_local_failure_keeps_mixed_pair_inventory_retrying(self):
        """A healthy pair in the same drain must not erase another pair's retry truth."""
        def final_result(files, generation, session_token):
            path_pair_ids = {file.path_pair_id for file in files}
            return ScannerResult(
                datetime.now(), files, scanned_path_pair_ids=path_pair_ids, generation=generation,
                is_progress=True, completed_path_pair_ids=path_pair_ids, is_scan_final=True,
                is_full_snapshot=True, full_snapshot_path_pair_ids=path_pair_ids,
                session_token=session_token,
            )

        def file_for(pair_id, size):
            file = SystemFile("{}-file".format(pair_id), size)
            file.path_pair_id = pair_id
            return file

        local_initial = final_result([file_for("pair-a", 7), file_for("pair-b", 11)], 1, "local-retry")
        remote_initial = final_result([file_for("pair-a", 7), file_for("pair-b", 11)], 1, "remote-retry")
        healthy_a = final_result([file_for("pair-a", 8)], 2, "local-retry")
        recoverable_b = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=2,
            # Legacy failure evidence can share this drain with a progressive
            # healthy pair; normalization must retain its pair-scoped retry.
            failed=True, recoverable_failure_path_pair_ids={"pair-b"},
            unknown_path_pair_ids={"pair-b"}, session_token="local-retry",
        )
        recovery_progress_b = ScannerResult(
            datetime.now(), [file_for("pair-b", 12)], scanned_path_pair_ids={"pair-b"}, generation=3,
            is_progress=True, session_token="local-retry",
        )
        recovered_b = final_result([file_for("pair-b", 13)], 3, "local-retry")
        remote_recovered_b = final_result([file_for("pair-b", 13)], 2, "remote-retry")
        terminal_b = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-b"}, generation=4,
            is_progress=True, failed=True, unknown_path_pair_ids={"pair-b"}, session_token="local-retry",
        )

        builder = ModelBuilder()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False, model_builder=builder, model=Model(),
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._Controller__remote_scan_process = self._progressive_process(
            "remote-retry", [[], [remote_initial], [], [], [remote_recovered_b], [], []],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            "local-retry", [[local_initial], [], [healthy_a, recoverable_b], [recovery_progress_b],
                             [recovered_b], [], [terminal_b]],
        )
        updater = ModelUpdater(controller)

        updater.update()
        updater.update()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 7, "up_to_date"), (
            inventory["pair-a"].file_count, inventory["pair-a"].size, inventory["pair-a"].state,
        ))
        self.assertEqual((1, 11, "up_to_date"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

        updater.update()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 11, "scanning"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

        updater.update()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 11, "scanning"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

        updater.update()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 13, "up_to_date"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

        updater.update()
        updater.update()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 13, "stale"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

    def test_recoverable_pair_does_not_make_ordinary_incomplete_sibling_stale(self):
        """Only a terminal pair may be stale while another pair is simply scanning."""
        def file_for(pair_id, size):
            file = SystemFile("{}-file".format(pair_id), size)
            file.path_pair_id = pair_id
            return file

        def final(files, generation, session_token):
            pair_ids = {file.path_pair_id for file in files}
            return ScannerResult(
                datetime.now(), files, scanned_path_pair_ids=pair_ids, generation=generation,
                is_progress=True, completed_path_pair_ids=pair_ids, is_scan_final=True,
                is_full_snapshot=True, full_snapshot_path_pair_ids=pair_ids,
                session_token=session_token,
            )

        initial_local = final([file_for("pair-a", 7), file_for("pair-b", 11)], 1, "local-mixed")
        initial_remote = final([file_for("pair-a", 7), file_for("pair-b", 11)], 1, "remote-mixed")
        recoverable_a = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=2,
            is_progress=True, failed=True, unknown_path_pair_ids={"pair-a"},
            recoverable_failure_path_pair_ids={"pair-a"}, session_token="local-mixed",
        )
        progress_b = ScannerResult(
            datetime.now(), [file_for("pair-b", 12)], scanned_path_pair_ids={"pair-b"}, generation=2,
            is_progress=True, is_scan_final=False, session_token="local-mixed",
        )
        recovered_local = final([file_for("pair-a", 8), file_for("pair-b", 12)], 3, "local-mixed")
        recovered_remote = final([file_for("pair-a", 8), file_for("pair-b", 12)], 2, "remote-mixed")
        builder = ModelBuilder()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False, model_builder=builder, model=Model(),
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._Controller__local_scan_process = self._progressive_process(
            "local-mixed", [[initial_local], [recoverable_a, progress_b], [recovered_local]],
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            "remote-mixed", [[initial_remote], [], [recovered_remote]],
        )
        updater = ModelUpdater(controller)

        updater.update()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("up_to_date", inventory["pair-a"].state)
        self.assertEqual("up_to_date", inventory["pair-b"].state)

        updater.update()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 7, "scanning"), (
            inventory["pair-a"].file_count, inventory["pair-a"].size, inventory["pair-a"].state,
        ))
        self.assertEqual((1, 11, "scanning"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))

        updater.update()
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("up_to_date", inventory["pair-a"].state)
        self.assertEqual("up_to_date", inventory["pair-b"].state)

    def test_completed_pair_with_unrelated_unknown_delta_uses_global_build(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        untouched = SystemFile("idle.bin", 20, False)
        untouched.path_pair_id = "pair-b"
        builder = ModelBuilder()
        builder.set_local_files([old, untouched])
        builder.set_remote_files([old, untouched])
        builder.set_unknown_local_path_pair_ids({"pair-a"})
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        replacement_local = SystemFile("new.bin", 30, False)
        replacement_local.path_pair_id = "pair-a"
        replacement_remote = SystemFile("new.bin", 30, False)
        replacement_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [replacement_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [replacement_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._refresh_model_file_command_identities_locked = MagicMock()

        ModelUpdater(controller).update()

        self.assertEqual(
            {ModelFile.build_file_id("new.bin", "pair-a"), ModelFile.build_file_id("idle.bin", "pair-b")},
            live_model.get_file_ids(),
        )
        self.assertEqual({"pair-b"}, builder._ModelBuilder__unknown_local_path_pair_ids)
        builder.build_model.assert_called_once()

    def test_candidate_lifecycle_exception_commits_pair_sources_for_global_recovery(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        builder = ModelBuilder()
        builder.set_local_files([old])
        builder.set_remote_files([old])
        live_model = builder.build_model()
        next_local = SystemFile("new.bin", 30, False)
        next_local.path_pair_id = "pair-a"
        next_remote = SystemFile("new.bin", 30, False)
        next_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [next_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [next_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock()}
        controller._Controller__context.performance_diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        live_model.add_file = MagicMock(side_effect=RuntimeError("candidate lifecycle failure"))

        with self.assertRaisesRegex(RuntimeError, "candidate lifecycle failure"):
            ModelUpdater(controller).update()

        new_id = ModelFile.build_file_id("new.bin", "pair-a")
        self.assertIn(new_id, builder._ModelBuilder__local_files_by_pair["pair-a"])
        self.assertIn(new_id, builder._ModelBuilder__remote_files_by_pair["pair-a"])
        self.assertTrue(builder.has_changes())
        counters = controller._Controller__context.performance_diagnostics.snapshot()["counters"]
        self.assertEqual(1, counters[COUNTER_CANDIDATE_LIFECYCLE_FALLBACK])
        lifecycle_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "candidate_lifecycle_fallback"
        ]
        self.assertEqual(1, len(lifecycle_calls))
        self.assertEqual(
            CANDIDATE_LIFECYCLE_FALLBACK_REASON_EXCEPTION,
            lifecycle_calls[0].kwargs["details"]["reason"],
        )

    def test_pair_candidate_waits_for_startup_recovery(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        builder = ModelBuilder()
        builder.set_local_files([old])
        builder.set_remote_files([old])
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        replacement = SystemFile("new.bin", 30, False)
        replacement.path_pair_id = "pair-a"
        result = ScannerResult(
            datetime.now(), [replacement], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            result, local_scan=result, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock()}
        controller._Controller__startup_recovery_done = False
        controller._reserve_move_attempt = MagicMock(return_value=False)
        controller._Controller__recover_interrupted_downloads = MagicMock()

        ModelUpdater(controller).update()

        self.assertIn(ModelFile.build_file_id("new.bin", "pair-a"), live_model.get_file_ids())
        builder.build_model.assert_called_once()

    def test_pair_final_with_pending_completion_uses_candidate_lifecycle(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        builder = ModelBuilder()
        builder.set_local_files([old])
        builder.set_remote_files([old])
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        replacement_local = SystemFile("new.bin", 30, False)
        replacement_local.path_pair_id = "pair-a"
        replacement_remote = SystemFile("new.bin", 30, False)
        replacement_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [replacement_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [replacement_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock()}
        controller._Controller__pending_completion_file_names = {("old.bin", "pair-a", None)}
        controller._reserve_move_attempt = MagicMock(return_value=False)

        ModelUpdater(controller).update()

        self.assertIn(ModelFile.build_file_id("new.bin", "pair-a"), live_model.get_file_ids())
        builder.build_model.assert_not_called()

    def test_pair_final_with_stale_markers_uses_candidate_and_prunes_them(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        builder = ModelBuilder()
        builder.set_local_files([old])
        builder.set_remote_files([old])
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        replacement_local = SystemFile("new.bin", 30, False)
        replacement_local.path_pair_id = "pair-a"
        replacement_remote = SystemFile("new.bin", 30, False)
        replacement_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [replacement_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [replacement_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock()}
        controller._reserve_move_attempt = MagicMock(return_value=False)
        stale_id = ModelFile.build_file_id("stale.bin", "pair-a")
        persist = controller._Controller__persist
        persist.downloaded_file_names = {stale_id}
        persist.downloaded_timestamps = {stale_id: 1.0}
        persist.extracted_file_names = {stale_id}
        persist.final_move_succeeded_file_names = {stale_id}

        ModelUpdater(controller).update()

        self.assertEqual(set(), persist.downloaded_file_names)
        self.assertEqual({}, persist.downloaded_timestamps)
        self.assertEqual(set(), persist.extracted_file_names)
        self.assertEqual(set(), persist.final_move_succeeded_file_names)
        builder.build_model.assert_not_called()

    def test_pair_candidate_removal_prunes_its_canonical_persisted_markers(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        builder = ModelBuilder()
        builder.set_local_files([old])
        builder.set_remote_files([old])
        old_id = ModelFile.build_file_id("old.bin", "pair-a")
        # These persisted values are already reflected in the cached live
        # model. The final scan alone must therefore be enough to remove them.
        builder.set_downloaded_files({old_id})
        builder.set_downloaded_timestamps({old_id: 1.0})
        builder.set_extracted_files({old_id})
        builder.set_final_move_succeeded_files({old_id})
        builder.set_move_failed_files({old_id})
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        empty_final = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            empty_final, local_scan=empty_final, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock()}
        persist = controller._Controller__persist
        persist.downloaded_file_names = {old_id}
        persist.downloaded_timestamps = {old_id: 1.0}
        persist.extracted_file_names = {old_id}
        persist.final_move_succeeded_file_names = {old_id}
        persist.move_failure_counts = {old_id: controller._Controller__MAX_MOVE_FAILURES}

        ModelUpdater(controller).update()

        self.assertEqual(set(), live_model.get_file_ids())
        self.assertEqual(set(), persist.downloaded_file_names)
        self.assertEqual({}, persist.downloaded_timestamps)
        self.assertEqual(set(), persist.extracted_file_names)
        self.assertEqual(set(), persist.final_move_succeeded_file_names)
        self.assertEqual({}, persist.move_failure_counts)
        builder.build_model.assert_not_called()

    def test_pair_candidate_runs_existing_auto_purge_lifecycle_for_new_local_only_root(self):
        builder = ModelBuilder()
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        local_only = SystemFile("orphan.bin", 0, False)
        local_only.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [local_only], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock()}
        controller._Controller__should_auto_purge_local_file = MagicMock(return_value=True)
        controller._Controller__queue_delete_local_process = MagicMock()

        ModelUpdater(controller).update()

        local_id = ModelFile.build_file_id("orphan.bin", "pair-a")
        self.assertIn(local_id, live_model.get_file_ids())
        controller._Controller__queue_delete_local_process.assert_called_once()
        self.assertIs(
            live_model.get_file(local_id),
            controller._Controller__queue_delete_local_process.call_args.args[0],
        )
        controller._Controller__queue_delete_local_process.call_args.args[1]()
        controller._Controller__local_scan_process.force_scan.assert_called_once_with("pair-a")
        builder.build_model.assert_not_called()

    def test_unrelated_terminalizable_collision_uses_global_lifecycle_before_candidate(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        idle_remote = SystemFile("idle", 20, True)
        idle_remote.path_pair_id = "pair-b"
        idle_remote.add_child(SystemFile("entry.bin", 20, False))
        idle_local = SystemFile("idle", 20, True)
        idle_local.path_pair_id = "pair-b"
        idle_collision = SystemFile("entry.bin", 20, False, is_staging=True)
        idle_collision.has_staging_collision = True
        idle_local.add_child(idle_collision)
        builder = ModelBuilder()
        builder.set_local_files([old, idle_local])
        builder.set_remote_files([old, idle_remote])
        builder.set_downloaded_files(set())
        builder.set_downloaded_timestamps({})
        builder.set_extracted_files(set())
        builder.set_stopped_files(set())
        builder.set_move_failed_files(set())
        builder.set_final_move_succeeded_files(set())
        builder.set_unknown_local_path_pair_ids({"pair-b"})
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        builder.build_authoritative_pair_roots = MagicMock(
            wraps=builder.build_authoritative_pair_roots,
        )
        idle_id = ModelFile.build_file_id("idle", "pair-b")
        idle_before = live_model.get_file(idle_id)
        idle_state = idle_before.state
        idle_scope_version = live_model.scope_version("pair-b")
        self.assertIn(idle_id, builder.get_terminalizable_staging_collision_file_ids())
        builder.request_rebuild = MagicMock(wraps=builder.request_rebuild)
        replacement_local = SystemFile("new.bin", 10, False)
        replacement_local.path_pair_id = "pair-a"
        replacement_remote = SystemFile("new.bin", 30, False)
        replacement_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [replacement_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [replacement_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        controller._has_active_collision_comparison = MagicMock(return_value=False)
        controller._reserve_move_attempt = MagicMock(return_value=True)
        controller._release_move_attempt = MagicMock()
        controller._Controller__move_from_staging = MagicMock()
        controller._Controller__context.performance_diagnostics = PerformanceDiagnosticsCollector(lambda: True)

        ModelUpdater(controller).update()

        self.assertIn(ModelFile.build_file_id("new.bin", "pair-a"), live_model.get_file_ids())
        builder.build_authoritative_pair_roots.assert_called_once()
        # Lifecycle maintenance owns the cached pair-b collision before
        # candidate selection, so its normal global build is authoritative.
        builder.build_model.assert_called_once()
        controller._reserve_move_attempt.assert_not_called()
        controller._Controller__move_from_staging.assert_not_called()
        self.assertGreaterEqual(builder.request_rebuild.call_count, 1)
        self.assertFalse(builder.has_changes())
        counters = controller._Controller__context.performance_diagnostics.snapshot()["counters"]
        self.assertEqual(1, counters["model_rebuild_terminalizable_collision"])
        self.assertEqual(1, counters[COUNTER_CANDIDATE_PAIR_FALLBACK])
        fallback_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "candidate_pair_fallback"
        ]
        self.assertEqual(1, len(fallback_calls))
        self.assertEqual(
            CANDIDATE_PAIR_FALLBACK_REASON_AUTHORIZATION_REJECTED,
            fallback_calls[0].kwargs["details"]["reason"],
        )

    def test_pair_candidate_ignores_unrelated_stale_move_failure_marker(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        idle = SystemFile("idle.bin", 20, False)
        idle.path_pair_id = "pair-b"
        builder = ModelBuilder()
        builder.set_local_files([old, idle])
        builder.set_remote_files([old, idle])
        builder.set_downloaded_files(set())
        builder.set_downloaded_timestamps({})
        builder.set_extracted_files(set())
        builder.set_stopped_files(set())
        builder.set_move_failed_files(set())
        builder.set_final_move_succeeded_files(set())
        builder.set_unknown_local_path_pair_ids({"pair-b"})
        live_model = builder.build_model()
        idle_id = ModelFile.build_file_id("idle.bin", "pair-b")
        idle_before = live_model.get_file(idle_id)
        builder.build_model = MagicMock(wraps=builder.build_model)
        builder.request_rebuild = MagicMock(wraps=builder.request_rebuild)
        replacement_local = SystemFile("new.bin", 10, False)
        replacement_local.path_pair_id = "pair-a"
        replacement_remote = SystemFile("new.bin", 30, False)
        replacement_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [replacement_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [replacement_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._Controller__persist.move_failure_counts = {
            ModelFile.build_file_id("missing.bin", "pair-b"): 1,
        }
        controller._reserve_move_attempt = MagicMock(return_value=False)

        ModelUpdater(controller).update()

        self.assertIn(ModelFile.build_file_id("new.bin", "pair-a"), live_model.get_file_ids())
        self.assertIs(idle_before, live_model.get_file(idle_id))
        builder.build_model.assert_not_called()
        builder.request_rebuild.assert_not_called()
        self.assertFalse(builder.has_changes())

    def test_pair_candidate_attributes_actionable_unrelated_retry_deferral(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        retry_remote = SystemFile("retry.bin", 10, False)
        retry_remote.path_pair_id = "pair-b"
        retry_local = SystemFile("retry.bin", 10, False)
        retry_local.path_pair_id = "pair-b"
        builder = ModelBuilder()
        builder.set_local_files([old, retry_local])
        builder.set_remote_files([old, retry_remote])
        builder.set_downloaded_files(set())
        builder.set_downloaded_timestamps({})
        builder.set_extracted_files(set())
        builder.set_stopped_files(set())
        builder.set_move_failed_files(set())
        builder.set_final_move_succeeded_files(set())
        builder.set_unknown_local_path_pair_ids({"pair-b"})
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        builder.request_rebuild = MagicMock(wraps=builder.request_rebuild)
        replacement_local = SystemFile("new.bin", 10, False)
        replacement_local.path_pair_id = "pair-a"
        replacement_remote = SystemFile("new.bin", 30, False)
        replacement_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [replacement_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [replacement_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        retry_id = ModelFile.build_file_id("retry.bin", "pair-b")
        controller._Controller__persist.move_failure_counts = {retry_id: 1}
        controller._Controller__move_retry_due = {retry_id: datetime.now() + timedelta(minutes=1)}
        controller._Controller__context.performance_diagnostics = PerformanceDiagnosticsCollector(lambda: True)

        ModelUpdater(controller).update()

        builder.build_model.assert_not_called()
        builder.request_rebuild.assert_called_once()
        counters = controller._Controller__context.performance_diagnostics.snapshot()["counters"]
        self.assertEqual(1, counters[COUNTER_UNRELATED_CANDIDATE_LIFECYCLE_DEFERRED])
        deferred_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "unrelated_candidate_lifecycle_deferred"
        ]
        self.assertEqual(1, len(deferred_calls))
        self.assertEqual(
            CANDIDATE_UNRELATED_LIFECYCLE_REASON_RETRY,
            deferred_calls[0].kwargs["details"]["reason"],
        )

    def test_multi_pair_final_with_new_duplicate_basenames_uses_global_build(self):
        old_a = SystemFile("old-a.bin", 10, False)
        old_a.path_pair_id = "pair-a"
        old_b = SystemFile("old-b.bin", 20, False)
        old_b.path_pair_id = "pair-b"
        builder = ModelBuilder()
        builder.set_local_files([old_a, old_b])
        builder.set_remote_files([old_a, old_b])
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        duplicate_a = SystemFile("shared.bin", 30, False)
        duplicate_a.path_pair_id = "pair-a"
        duplicate_b = SystemFile("shared.bin", 40, False)
        duplicate_b.path_pair_id = "pair-b"
        final_local = ScannerResult(
            datetime.now(), [duplicate_a, duplicate_b], scanned_path_pair_ids={"pair-a", "pair-b"},
            is_progress=True, completed_path_pair_ids={"pair-a", "pair-b"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a", "pair-b"},
        )
        final_remote = ScannerResult(
            datetime.now(), [duplicate_a, duplicate_b], scanned_path_pair_ids={"pair-a", "pair-b"},
            is_progress=True, completed_path_pair_ids={"pair-a", "pair-b"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a", "pair-b"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._reserve_move_attempt = MagicMock(return_value=False)

        ModelUpdater(controller).update()

        self.assertEqual(
            {ModelFile.build_file_id("shared.bin", "pair-a"), ModelFile.build_file_id("shared.bin", "pair-b")},
            live_model.get_file_ids(),
        )
        builder.build_model.assert_called_once()

    def test_pair_final_with_staged_collision_uses_candidate_lifecycle(self):
        old = SystemFile("old.bin", 1, False)
        old.path_pair_id = "pair-a"
        builder = ModelBuilder()
        builder.set_local_files([old])
        builder.set_remote_files([old])
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        remote = SystemFile("release", 10, True)
        remote.path_pair_id = "pair-a"
        remote.add_child(SystemFile("entry.bin", 10, False))
        local = SystemFile("release", 10, True)
        local.path_pair_id = "pair-a"
        collision = SystemFile("entry.bin", 10, False, is_staging=True)
        collision.has_staging_collision = True
        local.add_child(collision)
        final_local = ScannerResult(
            datetime.now(), [local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock()}
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        controller._has_active_collision_comparison = MagicMock(return_value=False)

        ModelUpdater(controller).update()

        builder.build_model.assert_not_called()
        release_id = ModelFile.build_file_id("release", "pair-a")
        self.assertEqual(ModelFile.State.MOVE_FAILED, live_model.get_file(release_id).state)
        self.assertEqual(
            controller._Controller__MAX_MOVE_FAILURES,
            controller._Controller__persist.move_failure_counts[release_id],
        )

    def test_unsafe_completed_pair_fallback_preserves_unrelated_source_bucket(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        untouched = SystemFile("shared.bin", 20, False)
        untouched.path_pair_id = "pair-b"
        builder = ModelBuilder()
        builder.set_local_files([old, untouched])
        builder.set_remote_files([old, untouched])
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        replacement_local = SystemFile("shared.bin", 30, False)
        replacement_local.path_pair_id = "pair-a"
        replacement_remote = SystemFile("shared.bin", 30, False)
        replacement_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [replacement_local], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [replacement_remote], scanned_path_pair_ids={"pair-a"},
            is_progress=True, completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._reserve_move_attempt = MagicMock(return_value=False)

        ModelUpdater(controller).update()

        self.assertEqual(
            {ModelFile.build_file_id("shared.bin", "pair-a"), ModelFile.build_file_id("shared.bin", "pair-b")},
            live_model.get_file_ids(),
        )
        self.assertEqual(
            {ModelFile.build_file_id("shared.bin", "pair-b")},
            set(builder._ModelBuilder__remote_files_by_pair["pair-b"]),
        )
        self.assertEqual(
            {ModelFile.build_file_id("shared.bin", "pair-a")},
            set(builder._ModelBuilder__remote_files_by_pair["pair-a"]),
        )
        builder.build_model.assert_called_once()

    def test_legacy_builder_final_refresh_receives_full_reconciled_authority(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        untouched = SystemFile("idle.bin", 20, False)
        untouched.path_pair_id = "pair-b"
        reconciler = _JointProgressiveReconciler()
        snapshot = {
            ("pair-a", "old.bin"): old,
            ("pair-b", "idle.bin"): untouched,
        }
        reconciler.reconcile(
            snapshot, dict(snapshot), set(), {"pair-a", "pair-b"},
            snapshot, dict(snapshot), set(), {"pair-a", "pair-b"},
            {"pair-a", "pair-b"},
        )
        replacement_local = SystemFile("new.bin", 30, False)
        replacement_local.path_pair_id = "pair-a"
        replacement_remote = SystemFile("new.bin", 30, False)
        replacement_remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [replacement_local, untouched], scanned_path_pair_ids={"pair-a", "pair-b"},
            is_progress=True, completed_path_pair_ids={"pair-a", "pair-b"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        final_remote = ScannerResult(
            datetime.now(), [replacement_remote, untouched], scanned_path_pair_ids={"pair-a", "pair-b"},
            is_progress=True, completed_path_pair_ids={"pair-a", "pair-b"}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={"pair-a"},
        )
        controller, builder = self._make_progressive_update_controller(
            final_remote, local_scan=final_local,
        )
        # MagicMock has no declared ModelBuilder pair API. It must retain the
        # compatibility whole-source path and receive both pair buckets.
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._Controller__progressive_joint_reconciler = reconciler

        ModelUpdater(controller).update()

        self.assertEqual(
            {("pair-a", "new.bin"), ("pair-b", "idle.bin")},
            {(file.path_pair_id, file.name) for file in builder.set_local_files.call_args.args[0]},
        )
        self.assertEqual(
            {("pair-a", "new.bin"), ("pair-b", "idle.bin")},
            {(file.path_pair_id, file.name) for file in builder.set_remote_files.call_args.args[0]},
        )
        builder.build_authoritative_pair_roots.assert_not_called()

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

        def deep_first_root(size: int) -> SystemFile:
            root = SystemFile("first.bin", size, True)
            parent = root
            for depth in range(24):
                child = SystemFile(f"nested-{depth}", size, True)
                parent.add_child(child)
                parent = child
            parent.add_child(SystemFile("payload.bin", size, False))
            return root

        # The first progressive wave is deliberately deep and retained.  The
        # second wave must not pass it into the partial renderer again.
        first_remote = deep_first_root(10)
        first_local = deep_first_root(5)
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
        retained_first_root = model.get_file("first.bin")
        controller._refresh_model_file_command_identities_locked.assert_called_once_with()
        updater.update()
        self.assertEqual({"first.bin", "second.bin"}, model.get_file_names())
        self.assertIs(retained_first_root, model.get_file("first.bin"))
        self.assertEqual(2, controller._refresh_model_file_command_identities_locked.call_count)
        self.assertEqual(2, builder.build_progressive_roots.call_count)
        first_delta_call, second_delta_call = builder.build_progressive_roots.call_args_list
        self.assertEqual({"first.bin"}, {file.name for file in first_delta_call.args[0]})
        self.assertEqual({"first.bin"}, {file.name for file in first_delta_call.args[1]})
        self.assertEqual({"second.bin"}, {file.name for file in second_delta_call.args[0]})
        self.assertEqual({"second.bin"}, {file.name for file in second_delta_call.args[1]})
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

    def test_lifecycle_scan_breadcrumb_is_hot_gated_and_omits_scan_identity(self):
        enabled = [False]
        trace = BreadcrumbTraceCollector(lambda: enabled[0], max_entries=8)
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace), logger=MagicMock(),
        )
        process = object.__new__(ScannerProcess)
        process._ScannerProcess__session_token = "scanner-session-private"
        result = ScannerResult(
            datetime.now(), [SystemFile("private-root", 1)], scanned_path_pair_ids={"private-pair"},
            generation=3, is_progress=True, is_full_snapshot=True,
            full_snapshot_path_pair_ids={"private-pair"}, completed_path_pair_ids={"private-pair"},
            session_token="scanner-session-private",
        )
        with patch.object(ScannerProcess, "pop_results", return_value=[result]):
            _pop_scan_updates(controller, "local", process)
        self.assertEqual([], trace.snapshot()["entries"])

        enabled[0] = True
        trace.sync_enabled_state()
        with patch.object(ScannerProcess, "pop_results", return_value=[result]):
            _pop_scan_updates(controller, "local", process)

        entries = trace.snapshot()["entries"]
        self.assertTrue(entries)
        details = entries[-1]["details"]
        self.assertEqual("local", details["scanner_side"])
        self.assertIn("accumulator_authoritative_root_count", details)
        self.assertEqual(1, details["event_count"])
        self.assertEqual(1, details["full_event_count"])
        self.assertEqual(1, details["distinct_generation_count"])
        self.assertEqual(3, details["min_generation"])
        self.assertEqual(3, details["max_generation"])
        self.assertEqual(1, details["input_top_level_file_count"])
        self.assertEqual(1, details["distinct_pair_name_count"])
        self.assertEqual(0, details["duplicate_pair_name_count"])
        # The fixture's SystemFile deliberately omits its pair ID while the
        # event declares one; only the aggregate mismatch count is exported.
        self.assertEqual(1, details["pair_id_mismatch_count"])
        self.assertNotIn("private-pair", str(entries))
        self.assertNotIn("private-root", str(entries))
        self.assertNotIn("scanner-session-private", str(entries))

        with patch.object(ScannerProcess, "pop_results", return_value=[]):
            _pop_scan_updates(controller, "local", process)
        self.assertEqual(len(entries), len(trace.snapshot()["entries"]))

    def test_lifecycle_scan_trace_does_not_build_details_or_tokens_while_disabled(self):
        trace = BreadcrumbTraceCollector(lambda: False, max_entries=8)
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace), logger=MagicMock(),
        )
        process = object.__new__(ScannerProcess)
        process._ScannerProcess__session_token = "private-session"
        result = ScannerResult(
            datetime.now(), [SystemFile("private-root", 1)], generation=1,
            scanned_path_pair_ids={None}, is_progress=True,
        )

        with patch.object(ScannerProcess, "pop_results", return_value=[result]), \
                patch("controller.model_updater._lifecycle_scan_details",
                      side_effect=AssertionError("disabled trace must not build details")), \
                patch.object(_ProgressiveScanAccumulator, "lifecycle_trace_transition_token",
                             side_effect=AssertionError("disabled trace must not create token")):
            _pop_scan_updates(controller, "local", process)

        self.assertEqual([], trace.snapshot()["entries"])

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
        controller, model_builder = self._make_progressive_update_controller(
            partial, local_scan=partial, authoritative=False,
        )
        status = LftpJobStatus(
            1,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            "root",
            "",
        )
        controller._Controller__lftp.status.return_value = [status]
        model_builder.has_changes.return_value = True
        model_builder.has_only_pending_active_transfer_delta.return_value = True
        updater = ModelUpdater(controller)

        updater.update()

        model_builder.set_lftp_statuses.assert_called_once_with([status])
        model_builder.build_progressive_roots.assert_called_once()
        model_builder.build_model.assert_not_called()
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

    def test_progressive_active_timestamp_overlay_rejection_forces_one_full_build(self):
        builder = ModelBuilder()
        first = SystemFile("first.bin", 100, False)
        first.path_pair_id = "pair-a"
        second = SystemFile("second.bin", 200, False)
        second.path_pair_id = "pair-b"
        first_id = ModelFile.build_file_id("first.bin", "pair-a")
        second_id = ModelFile.build_file_id("second.bin", "pair-b")
        builder.set_remote_files([first, second])
        builder.set_downloaded_files({first_id, second_id})
        live_model = builder.build_model()

        # Simulate startup timestamp synchronization after the progressive
        # baseline is visible, while active LFTP status keeps the progressive
        # active-only eligibility branch open.
        builder.set_downloaded_timestamps({first_id: 1760000010.0, second_id: 1760000020.0})
        progressive_first = SystemFile("first.bin", 100, False)
        progressive_first.path_pair_id = "pair-a"
        progressive = ScannerResult(
            datetime.now(), [progressive_first], scanned_path_pair_ids={"pair-a"},
            is_progress=True, is_scan_final=False,
        )
        controller, _ = self._make_progressive_update_controller(
            progressive, local_scan=progressive, authoritative=True,
            model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {
            "pair-a": MagicMock(), "pair-b": MagicMock(),
        }
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "first.bin", "",
        )
        status.path_pair_id = "pair-a"
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [status]
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        updater = ModelUpdater(controller)
        updater.update()

        builder.build_model.assert_called_once_with()
        self.assertEqual(1760000010.0, live_model.get_file(first_id).downloaded_timestamp.timestamp())
        self.assertEqual(1760000020.0, live_model.get_file(second_id).downloaded_timestamp.timestamp())

        updater.update()
        builder.build_model.assert_called_once_with()

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

    def test_scoped_active_scan_delta_preserves_exact_model_identity(self):
        builder = ModelBuilder()
        remote_root = SystemFile("root", 100, False)
        remote_root.path_pair_id = "pair-a"
        builder.set_remote_files([remote_root])
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        status.path_pair_id = "pair-a"
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [status]
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("root", 25, False)],
        )
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        scoped_id = ModelFile.build_file_id("root", "pair-a")
        self.assertEqual({scoped_id}, live_model.get_file_ids())
        self.assertEqual(25, live_model.get_file(scoped_id).transferred_size)
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
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        controller._Controller__context.breadcrumb_trace = trace
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
        entries = trace.snapshot()["entries"]
        messages = [entry["message"] for entry in entries]
        self.assertIn("completion_pending_registered", messages)
        self.assertIn("completion_gate_candidate", messages)
        candidate = next(entry for entry in entries if entry["message"] == "completion_gate_candidate")
        self.assertEqual("full_build", candidate["details"]["build_kind"])
        self.assertTrue(candidate["details"]["candidate_present"])
        self.assertTrue(candidate["details"]["live_present"])
        self.assertIn("complete_local_coverage", candidate["details"])
        self.assertNotIn("root", str(entries))

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

    def test_pending_completion_trace_reports_when_no_model_build_runs(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        controller, _ = self._make_progressive_update_controller(None, local_scan=None)
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__pending_completion_file_names = {("private-root", None, None)}

        ModelUpdater(controller).update()

        entries = trace.snapshot()["entries"]
        self.assertEqual(["completion_gate_build_deferred"], [entry["message"] for entry in entries])
        self.assertEqual(
            {"build_ran": False, "reason": "no_model_build"}, entries[0]["details"],
        )
        self.assertNotIn("private-root", str(entries))

    def _make_lftp_completion_controller(self, prev_downloading_file_names=None):
        controller = SimpleNamespace(
            _Controller__prev_downloading_file_names=set(prev_downloading_file_names or []),
            _Controller__pending_completion_file_names=set(),
            _Controller__model_builder=MagicMock(),
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

    def test_sync_persist_to_all_builders_keeps_two_pair_timestamp_overlay_after_startup(self):
        first_id = ModelFile.build_file_id("first.bin", "pair-a")
        second_id = ModelFile.build_file_id("second.bin", "pair-b")
        controller, model_builder = self._make_controller(
            downloaded_file_names={first_id, second_id},
            extracted_file_names=set(),
            stopped_file_names=set(),
            path_pairs_by_id={"pair-a": SimpleNamespace(), "pair-b": SimpleNamespace()},
        )
        controller._Controller__persist.downloaded_timestamps = {
            first_id: 1760000010.0,
            second_id: 1760000020.0,
        }

        ModelUpdater(controller).sync_persist_to_all_builders()

        model_builder.set_downloaded_timestamps.assert_called_once_with(
            controller._Controller__persist.downloaded_timestamps,
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
        controller._Controller__model_builder.evict_recent_live_transfer_snapshots_for_completed_file_ids \
            .assert_called_once_with({ModelFile.build_file_id(*completion_entry[:2])})
        controller._Controller__local_scan_process.force_scan.assert_called_once_with("movies")
        controller.logger.info.assert_called_once_with(
            "Download completion pending (LFTP job finished): {}".format(
                ModelFile.build_file_id(*completion_entry[:2])
            )
        )

    def test_handle_lftp_completion_detection_scopes_rescans_per_completed_pair(self):
        controller = self._make_lftp_completion_controller(
            prev_downloading_file_names={
                ("movie.mkv", "movies", "Movies"),
                ("episode.mkv", "tv", "TV"),
                ("second-movie.mkv", "movies", "Movies"),
            }
        )

        ModelUpdater(controller)._handle_lftp_completion_detection([], True)

        self.assertEqual(
            [call("movies"), call("tv")],
            controller._Controller__local_scan_process.force_scan.call_args_list,
        )

    def test_handle_lftp_completion_detection_uses_one_global_rescan_for_legacy_identity(self):
        controller = self._make_lftp_completion_controller(
            prev_downloading_file_names={
                ("legacy.mkv", None, None),
                ("movie.mkv", "movies", "Movies"),
            }
        )

        ModelUpdater(controller)._handle_lftp_completion_detection([], True)

        controller._Controller__local_scan_process.force_scan.assert_called_once_with()

    def test_completion_gate_breadcrumb_is_hot_gated_deduplicated_and_identity_free(self):
        enabled = [False]
        trace = BreadcrumbTraceCollector(lambda: enabled[0], max_entries=8)
        controller = self._make_lftp_completion_controller()
        controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)
        updater = ModelUpdater(controller)

        class DetailSentinel(dict):
            def items(self):
                raise AssertionError("disabled trace must not enumerate details")

        updater._record_completion_gate_breadcrumb(
            "private-file-id", "completion_gate_candidate", DetailSentinel(),
        )
        self.assertEqual([], trace.snapshot()["entries"])

        enabled[0] = True
        trace.sync_enabled_state()
        details = {"candidate_present": True, "complete_local_coverage": False}
        updater._record_completion_gate_breadcrumb(
            "private-file-id", "completion_gate_candidate", details,
        )
        updater._record_completion_gate_breadcrumb(
            "private-file-id", "completion_gate_candidate", details,
        )

        entries = trace.snapshot()["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual("completion_gate", entries[0]["stage"])
        self.assertEqual("completion_gate_candidate", entries[0]["message"])
        self.assertEqual(details, entries[0]["details"])
        self.assertNotIn("private-file-id", str(entries))

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
        controller._Controller__model_builder.evict_recent_live_transfer_snapshots_for_completed_file_ids \
            .assert_not_called()
        controller._Controller__is_explicitly_stopped.assert_not_called()
        controller.logger.info.assert_not_called()

    def test_pending_completion_floor_derives_percent_from_retained_bytes(self):
        file = ModelFile("release", False)
        file.remote_size = 1000
        file.local_size = 740
        file.transferred_size = 740
        file.download_progress = 99
        file.state = ModelFile.State.DEFAULT

        ModelUpdater._apply_pending_completion_progress_floor(
            file,
            {file.file_id},
            99,
            750,
        )

        self.assertEqual(750, file.transferred_size)
        self.assertEqual(75, file.download_progress)

    def test_pending_completion_floor_clamps_oversized_current_bytes_without_floor(self):
        file = ModelFile("release", False)
        file.remote_size = 500
        file.local_size = 500
        file.transferred_size = 750
        file.download_progress = 99
        file.state = ModelFile.State.DEFAULT

        ModelUpdater._apply_pending_completion_progress_floor(
            file,
            {file.file_id},
            None,
            None,
        )

        self.assertEqual(500, file.transferred_size)
        self.assertEqual(100, file.download_progress)

    def test_pending_completion_floor_clears_percent_for_zero_remote_total(self):
        file = ModelFile("release", False)
        file.remote_size = 0
        file.local_size = 0
        file.transferred_size = 1
        file.download_progress = 99
        file.state = ModelFile.State.DEFAULT

        ModelUpdater._apply_pending_completion_progress_floor(
            file,
            {file.file_id},
            None,
            None,
        )

        self.assertEqual(0, file.transferred_size)
        self.assertIsNone(file.download_progress)

    def test_completion_registration_publishes_real_builder_scan_state_with_consistent_floor(self):
        file_name = "release"
        path_pair_id = "movies"
        file_id = ModelFile.build_file_id(file_name, path_pair_id)
        remote_root = SystemFile(file_name, 1000, True)
        remote_root.path_pair_id = path_pair_id
        remote_root.path_pair_name = "Movies"
        remote_root.add_child(SystemFile("child", 1000, False))
        local_root = SystemFile(file_name, 740, True)
        local_root.path_pair_id = path_pair_id
        local_root.path_pair_name = "Movies"
        local_root.add_child(SystemFile("child", 740, False))
        builder = ModelBuilder()
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        running_status = LftpJobStatus(
            0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, file_name, "",
        )
        running_status.path_pair_id = path_pair_id
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 99, 14, 123)
        builder.set_lftp_statuses([running_status])
        live_model = builder.build_model()

        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        controller._Controller__prev_downloading_file_names = {
            (file_name, path_pair_id, "Movies"),
        }
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        controller._Controller__lftp.status.return_value = []
        controller._Controller__lftp.last_status_poll_healthy = True

        ModelUpdater(controller).update()

        published_file = controller._Controller__model.get_file(file_id)
        self.assertEqual(ModelFile.State.DEFAULT, published_file.state)
        self.assertEqual(750, published_file.transferred_size)
        self.assertEqual(75, published_file.download_progress)
        self.assertIsNone(published_file.downloading_speed)
        self.assertIsNone(published_file.eta)
        self.assertIn(
            (file_name, path_pair_id, "Movies"),
            controller._Controller__pending_completion_file_names,
        )
        controller._Controller__local_scan_process.force_scan.assert_called_once_with(path_pair_id)

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
