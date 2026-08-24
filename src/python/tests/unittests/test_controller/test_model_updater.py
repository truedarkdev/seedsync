# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import logging
import os
import tempfile
from copy import copy
from concurrent.futures import Future
from datetime import datetime, timedelta
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from controller import Controller, ModelBuilder
from controller.controller import _LftpOperation, PendingQueueDispatch
from controller.extract import ExtractCompletedResult
from controller.persist_keys import KEY_SEP
from controller.model_updater import (
    ModelUpdater,
    _breadcrumb_effectively_enabled,
    _active_delta_rejection_correlation_identity,
    _active_delta_poll_decision_diagnostics,
    _active_delta_status_match_evidence,
    _active_delta_status_missing_provenance,
    _record_active_delta_rejection_summary,
    _record_lftp_status_breadcrumb,
    _ProgressiveScanAccumulator,
    _JointProgressiveReconciler,
    _filter_progressive_remote_state,
    _lifecycle_scanned_path_pair_ids,
    _merge_targeted_legacy_scan_files,
    _remote_reconciliation_established,
    _pop_scan_updates,
    _sync_remote_scan_root_fingerprint_hints,
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
from common.breadcrumb_trace import BreadcrumbTraceCollector, trace_session_digest
from common.exclude_patterns import ExactPathExclusion
from controller.scan.scanner_process import ScannerProcess, ScannerResult
from lftp import LftpJobStatus
from model.diff import ModelDiff
from model import Model, ModelFile
from system import SystemFile
from system.scanner import SystemScanner


class TestModelUpdater(unittest.TestCase):
    def test_update_exception_clears_lineage_without_false_mutation(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        builder = MagicMock()
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace, performance_diagnostics=None),
            _Controller__model_builder=builder,
            _Controller__model=SimpleNamespace(version=3),
            _Controller__work_state_lock=None,
            _Controller__stop_resume_trace_cycle_id=0,
            logger=MagicMock(),
        )
        updater = ModelUpdater(controller)
        def fail_after_status_consume():
            updater._ModelUpdater__progress_lineage_correlation = "lftp-poll:0123456789abcdef"
            raise RuntimeError("update failed")
        updater._update_once = MagicMock(side_effect=fail_after_status_consume)

        with self.assertRaisesRegex(RuntimeError, "update failed"):
            updater.update()

        self.assertIsNone(updater._ModelUpdater__progress_lineage_correlation)
        self.assertEqual([], trace.snapshot()["progress_lineage"]["spans"])

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

    def test_scan_authority_breadcrumb_is_aggregate_queryable_and_sanitized(self):
        remote = ScannerResult(
            datetime.now(), [SystemFile("private-remote-root", 1)],
            scanned_path_pair_ids={"private-pair"},
            completed_path_pair_ids={"private-pair"},
            is_progress=True, is_scan_final=True, is_full_snapshot=True,
            session_token="private-remote-session",
        )
        local = ScannerResult(
            datetime.now(), [SystemFile("private-local-root", 1)],
            scanned_path_pair_ids={"private-pair"},
            completed_path_pair_ids={"private-pair"},
            is_progress=True, is_scan_final=True, is_full_snapshot=True,
            session_token="private-local-session",
        )
        remote.files[0].path_pair_id = "private-pair"
        local.files[0].path_pair_id = "private-pair"
        controller, _ = self._make_progressive_update_controller(
            remote, local, authoritative=False,
        )
        controller._Controller__path_pairs_by_id = {"private-pair": MagicMock()}
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)

        def record_breadcrumb(**kwargs):
            metadata = {
                "stage": kwargs["stage"], "event_type": kwargs["event_type"],
                "corr_id": kwargs.get("corr_id"), "flow_id": kwargs.get("flow_id"),
                "trace_scope": kwargs.get("trace_scope", "flow"),
            }
            if "category" in kwargs:
                metadata["category"] = kwargs["category"]
            if "level" in kwargs:
                metadata["level"] = kwargs["level"]
            trace.record(
                "controller", kwargs["message"], kwargs["details"],
                **metadata,
            )

        controller._Controller__record_breadcrumb = record_breadcrumb
        ModelUpdater(controller).update()

        payload = trace.query_events(
            category="scan.authority", stage="scan_authority", limit=1,
        )
        self.assertEqual(1, len(payload["events"]))
        entry = payload["events"][0]
        details = entry["details"]
        self.assertEqual("scan.authority", entry["category"])
        self.assertEqual("info", entry["level"])
        self.assertEqual("scan_authority", entry["stage"])
        self.assertEqual("diagnostic", entry["event_type"])
        self.assertEqual("aggregate", entry["trace_scope"])
        self.assertTrue(details["final"])
        self.assertTrue(details["full"])
        self.assertTrue(details["joint_final"])
        self.assertFalse(details["joint_authoritative_before"])
        self.assertTrue(details["joint_authoritative_after"])
        self.assertEqual(2, details["scanned_pair_count"])
        self.assertEqual(2, details["completed_pair_count"])
        self.assertEqual(0, details["unknown_pair_count"])
        self.assertEqual(2, details["staged_bucket_count"])
        self.assertEqual(2, details["adopted_bucket_count"])
        self.assertFalse(details["pair_delta_allowed"])
        self.assertFalse(details["pair_delta_fallback"])
        self.assertEqual(0, details["unknown_overlay_before_count"])
        self.assertEqual(0, details["unknown_overlay_current_count"])
        self.assertEqual(0, details["unknown_overlay_after_count"])
        self.assertEqual(0, details["raw_local_reconciliation_before_count"])
        self.assertEqual(1, details["raw_local_reconciliation_after_count"])
        self.assertEqual(1, details["effective_local_reconciliation_after_count"])
        self.assertNotIn("private-remote-session", str(entry))
        self.assertNotIn("private-local-session", str(entry))
        self.assertNotIn("private-pair", str(entry))
        self.assertNotIn("private-remote-root", str(entry))
        self.assertNotIn("private-local-root", str(entry))
        self.assertNotEqual("private-remote-session", entry["corr_id"])
        self.assertEqual(
            details,
            controller._Controller__scan_authority_snapshot | {
                "local_final": details["local_final"],
                "remote_final": details["remote_final"],
                "local_full": details["local_full"],
                "remote_full": details["remote_full"],
                "local_scanned_pair_count": details["local_scanned_pair_count"],
                "remote_scanned_pair_count": details["remote_scanned_pair_count"],
                "local_completed_pair_count": details["local_completed_pair_count"],
                "remote_completed_pair_count": details["remote_completed_pair_count"],
                "local_unknown_pair_count": details["local_unknown_pair_count"],
                "remote_unknown_pair_count": details["remote_unknown_pair_count"],
                "joint_authoritative_before": details["joint_authoritative_before"],
                "joint_authoritative": details["joint_authoritative"],
                "staged_pair_count": details["staged_pair_count"],
                "adopted_pair_count": details["adopted_pair_count"],
                "pair_delta_allowed": details["pair_delta_allowed"],
                "pair_delta_fallback": details["pair_delta_fallback"],
                "unknown_overlay_before_count": details["unknown_overlay_before_count"],
                "unknown_overlay_current_count": details["unknown_overlay_current_count"],
                "raw_local_reconciliation_before_count": details["raw_local_reconciliation_before_count"],
            },
        )

    def test_scan_authority_event_matches_standing_summary_identity_and_versions(self):
        remote = ScannerResult(
            datetime.now(), [SystemFile("private-remote-root", 1)],
            scanned_path_pair_ids={"private-pair"},
            completed_path_pair_ids={"private-pair"},
            is_progress=True, is_scan_final=True, is_full_snapshot=True,
            session_token="private-remote-session",
            generation=7,
        )
        local = ScannerResult(
            datetime.now(), [SystemFile("private-local-root", 1)],
            scanned_path_pair_ids={"private-pair"},
            completed_path_pair_ids={"private-pair"},
            is_progress=True, is_scan_final=True, is_full_snapshot=True,
            session_token="private-local-session",
            generation=8,
        )
        remote.files[0].path_pair_id = "private-pair"
        local.files[0].path_pair_id = "private-pair"
        controller, model_builder = self._make_progressive_update_controller(
            remote, local, authoritative=False,
        )
        controller._Controller__path_pairs_by_id = {"private-pair": MagicMock()}
        controller._Controller__model.version = 11
        controller._Controller__model.iter_files.return_value = []
        model_builder.local_library_inventory_snapshot.return_value = (0, {})
        model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()
        controller._model_scope_id = Controller._model_scope_id
        controller.get_model_summary = Controller.get_model_summary.__get__(controller, Controller)
        controller._Controller__record_breadcrumb = MagicMock()

        ModelUpdater(controller).update()

        authority_call = next(
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "scan_authority"
        )
        details = authority_call.kwargs["details"]
        summary = controller.get_model_summary()
        summary_snapshot = summary["scan_authority"]
        self.assertEqual("scan.authority", authority_call.kwargs["category"])
        self.assertEqual(summary_snapshot["publication_id"], details["publication_id"])
        self.assertEqual(summary_snapshot["model_version"], details["model_version"])
        self.assertEqual(
            summary_snapshot["local_scan_generation"], details["local_scan_generation"],
        )
        self.assertEqual(
            summary_snapshot["remote_scan_generation"], details["remote_scan_generation"],
        )
        self.assertEqual(11, summary["model_version"])
        for private_value in (
            "private-remote-root", "private-local-root", "private-pair",
            "private-remote-session", "private-local-session",
        ):
            self.assertNotIn(private_value, str({"details": details, "summary": summary}))

    def test_scan_authority_event_is_category_gated_before_payload_emission(self):
        remote = ScannerResult(
            datetime.now(), [SystemFile("private-root", 1)],
            scanned_path_pair_ids={"private-pair"},
        )
        controller, _ = self._make_progressive_update_controller(remote)

        class CategoryGate:
            def is_effectively_enabled(self, category, level="info"):
                return category != "scan.authority"

        controller._Controller__context.breadcrumb_trace = CategoryGate()
        controller._Controller__record_breadcrumb = MagicMock()

        ModelUpdater(controller).update()

        authority_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "scan_authority"
        ]
        self.assertEqual([], authority_calls)
        self.assertNotIn(
            "publication_id",
            str([
                call.kwargs.get("details")
                for call in controller._Controller__record_breadcrumb.call_args_list
            ]),
        )

    def test_scan_authority_identity_only_repeat_does_not_notify_summary_but_transition_does(self):
        first_remote = ScannerResult(
            datetime.now(), [SystemFile("root", 1)],
            scanned_path_pair_ids={"pair"},
        )
        controller, _ = self._make_progressive_update_controller(
            first_remote, local_scan=None, authoritative=True,
        )
        controller._Controller__record_breadcrumb = MagicMock()
        controller.notify_model_summary_changed = MagicMock()
        updater = ModelUpdater(controller)

        updater.update()
        self.assertEqual(1, controller.notify_model_summary_changed.call_count)
        first_publication_id = controller._Controller__scan_authority_snapshot["publication_id"]

        controller.notify_model_summary_changed.reset_mock()
        updater.update()
        self.assertEqual(0, controller.notify_model_summary_changed.call_count)
        second_publication_id = controller._Controller__scan_authority_snapshot["publication_id"]
        self.assertGreater(second_publication_id, first_publication_id)

        transitioned_remote = ScannerResult(
            datetime.now(), [SystemFile("root", 1)],
            scanned_path_pair_ids={"pair"},
            unknown_path_pair_ids={"pair"},
        )
        controller._Controller__remote_scan_process.pop_latest_result.return_value = transitioned_remote
        updater.update()
        self.assertEqual(1, controller.notify_model_summary_changed.call_count)

    def test_scan_authority_retains_absent_side_generation_from_prior_publication(self):
        pair_id = "private-pair"

        def scan_file(name):
            file = SystemFile(name, 1)
            file.path_pair_id = pair_id
            return file

        first_remote = ScannerResult(
            datetime.now(), [scan_file("private-remote-root")],
            scanned_path_pair_ids={pair_id}, completed_path_pair_ids={pair_id},
            generation=7, is_progress=True, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={pair_id},
            session_token="private-remote-session",
        )
        first_local = ScannerResult(
            datetime.now(), [scan_file("private-local-root")],
            scanned_path_pair_ids={pair_id}, completed_path_pair_ids={pair_id},
            generation=8, is_progress=True, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={pair_id},
            session_token="private-local-session",
        )
        second_remote = ScannerResult(
            datetime.now(), [scan_file("private-remote-root-new")],
            scanned_path_pair_ids={pair_id}, completed_path_pair_ids={pair_id},
            generation=9, is_progress=True, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={pair_id},
            session_token="private-remote-session",
        )
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
        )
        controller._Controller__path_pairs_by_id = {pair_id: MagicMock()}
        controller._Controller__remote_scan_process = self._progressive_process(
            "private-remote-session", [[first_remote], [second_remote]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            "private-local-session", [[first_local], []],
        )
        controller._Controller__model.version = 11
        controller._Controller__model.iter_files.return_value = []
        model_builder.local_library_inventory_snapshot.return_value = (0, {})
        model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()
        controller._model_scope_id = Controller._model_scope_id
        controller.get_model_summary = Controller.get_model_summary.__get__(controller, Controller)
        controller._Controller__record_breadcrumb = MagicMock()
        updater = ModelUpdater(controller)

        updater.update()
        updater.update()

        authority_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "scan_authority"
        ]
        self.assertEqual(2, len(authority_calls))
        second_details = authority_calls[-1].kwargs["details"]
        second_summary = controller.get_model_summary()["scan_authority"]
        self.assertEqual(8, second_summary["local_scan_generation"])
        self.assertEqual(9, second_summary["remote_scan_generation"])
        self.assertEqual(8, second_details["local_scan_generation"])
        self.assertEqual(9, second_details["remote_scan_generation"])
        self.assertEqual(
            second_summary["publication_id"], second_details["publication_id"],
        )
        for private_value in (
            "private-remote-root", "private-local-root", "private-pair",
            "private-remote-session", "private-local-session",
        ):
            self.assertNotIn(private_value, str({"details": second_details, "summary": second_summary}))

    def test_scan_authority_clears_absent_side_generation_after_session_reset(self):
        pair_id = "private-pair"

        def scan_file(name):
            file = SystemFile(name, 1)
            file.path_pair_id = pair_id
            return file

        first_remote = ScannerResult(
            datetime.now(), [scan_file("private-remote-root")],
            scanned_path_pair_ids={pair_id}, completed_path_pair_ids={pair_id},
            generation=7, is_progress=True, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={pair_id},
            session_token="private-remote-session",
        )
        first_local = ScannerResult(
            datetime.now(), [scan_file("private-local-root")],
            scanned_path_pair_ids={pair_id}, completed_path_pair_ids={pair_id},
            generation=8, is_progress=True, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={pair_id},
            session_token="private-local-session",
        )
        second_remote = ScannerResult(
            datetime.now(), [scan_file("private-remote-root-new")],
            scanned_path_pair_ids={pair_id}, completed_path_pair_ids={pair_id},
            generation=9, is_progress=True, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={pair_id},
            session_token="private-remote-session",
        )
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
        )
        controller._Controller__path_pairs_by_id = {pair_id: MagicMock()}
        controller._Controller__remote_scan_process = self._progressive_process(
            "private-remote-session", [[first_remote], [second_remote]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            "private-local-session", [[first_local], []],
        )
        controller._Controller__model.version = 11
        controller._Controller__model.iter_files.return_value = []
        model_builder.local_library_inventory_snapshot.return_value = (0, {})
        model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()
        controller._model_scope_id = Controller._model_scope_id
        controller.get_model_summary = Controller.get_model_summary.__get__(controller, Controller)
        controller._Controller__record_breadcrumb = MagicMock()
        updater = ModelUpdater(controller)

        updater.update()
        controller._Controller__local_scan_process._ScannerProcess__session_token = (
            "private-new-local-session"
        )
        updater.update()

        authority_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "scan_authority"
        ]
        self.assertEqual(2, len(authority_calls))
        second_details = authority_calls[-1].kwargs["details"]
        second_summary = controller.get_model_summary()["scan_authority"]
        self.assertTrue(controller._Controller__progressive_local_scan_session_changed)
        self.assertEqual(0, second_summary["local_scan_generation"])
        self.assertEqual(9, second_summary["remote_scan_generation"])
        self.assertEqual(0, second_details["local_scan_generation"])
        self.assertEqual(9, second_details["remote_scan_generation"])
        self.assertEqual(
            second_summary["publication_id"], second_details["publication_id"],
        )
        for private_value in (
            "private-remote-root", "private-local-root", "private-pair",
            "private-remote-session", "private-local-session", "private-new-local-session",
        ):
            self.assertNotIn(private_value, str({"details": second_details, "summary": second_summary}))

    def test_scan_result_category_gate_skips_payload_and_correlation_work(self):
        remote = ScannerResult(
            datetime.now(), [SystemFile("sample-remote-root", 1)],
            scanned_path_pair_ids={"path-pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(remote)

        class CategoryGate:
            def is_effectively_enabled(self, category, level="info"):
                return category != "scan.result"

        controller._Controller__context.breadcrumb_trace = CategoryGate()
        controller._Controller__trace_corr_id_from_files = MagicMock(
            side_effect=AssertionError("disabled scan correlation was built"),
        )

        ModelUpdater(controller).update()

        self.assertFalse(any(
            call.kwargs.get("message") in {
                "remote_scan_result", "local_scan_result", "active_scan_result",
            }
            for call in controller._Controller__record_breadcrumb.call_args_list
        ))

    def test_scan_result_verbosity_gate_skips_payload_and_correlation_work(self):
        remote = ScannerResult(
            datetime.now(), [SystemFile("sample-remote-root", 1)],
            scanned_path_pair_ids={"path-pair-a"},
        )
        controller, _ = self._make_progressive_update_controller(remote)

        class VerbosityGate:
            def is_effectively_enabled(self, category, level="info"):
                return level == "debug"

        controller._Controller__context.breadcrumb_trace = VerbosityGate()
        controller._Controller__trace_corr_id_from_files = MagicMock(
            side_effect=AssertionError("disabled scan correlation was built"),
        )

        ModelUpdater(controller).update()

        self.assertFalse(any(
            call.kwargs.get("message") == "remote_scan_result"
            for call in controller._Controller__record_breadcrumb.call_args_list
        ))

    def test_effective_breadcrumb_gate_fails_closed_without_legacy_fallback(self):
        class RaisingGate:
            def is_effectively_enabled(self, category, level="info"):
                raise RuntimeError("gate unavailable")

            def is_enabled(self):
                return True

        class NonBooleanGate:
            def is_effectively_enabled(self, category, level="info"):
                return MagicMock()

            def is_enabled(self):
                return True

        class LegacyGate:
            def is_enabled(self):
                return True

        self.assertFalse(_breadcrumb_effectively_enabled(RaisingGate(), "finalization.child"))
        self.assertFalse(_breadcrumb_effectively_enabled(NonBooleanGate(), "finalization.child"))
        self.assertTrue(_breadcrumb_effectively_enabled(LegacyGate(), "finalization.child"))

    def test_remote_only_final_noop_notifies_summary_after_authority_snapshot(self):
        remote = self._progressive_result(final=True)
        controller, model_builder = self._make_progressive_update_controller(
            remote, local_scan=None, authoritative=True, model=Model(),
        )
        model_builder.local_library_inventory_revision.return_value = 0
        model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()
        observed_snapshots = []

        class SummaryReader:
            def model_summary_changed(self):
                observed_snapshots.append(
                    controller.get_model_summary()["scan_authority"]
                )

        controller.get_model_summary = lambda: {
            "scan_authority": dict(controller._Controller__scan_authority_snapshot),
        }
        controller._Controller__model.add_listener(SummaryReader())
        controller.notify_model_summary_changed = lambda: controller._Controller__model.notify_summary_changed()

        updater = ModelUpdater(controller)
        updater.update()

        self.assertEqual(1, len(observed_snapshots))
        self.assertEqual(
            controller._Controller__scan_authority_snapshot,
            observed_snapshots[0],
        )
        self.assertEqual("no_op", observed_snapshots[0]["outcome"])

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
            metadata = {
                "stage": kwargs["stage"], "event_type": kwargs["event_type"],
                "corr_id": kwargs["corr_id"], "flow_id": kwargs["flow_id"],
                "trace_scope": kwargs["trace_scope"],
            }
            if "category" in kwargs:
                metadata["category"] = kwargs["category"]
            if "level" in kwargs:
                metadata["level"] = kwargs["level"]
            trace.record(
                "controller", kwargs["message"], kwargs["details"],
                **metadata,
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

    def test_progressive_accumulator_root_shape_trace_is_empty_when_disabled(self):
        accumulator = _ProgressiveScanAccumulator()
        self.assertEqual({}, accumulator.root_shape_trace())

        accumulator.apply([
            self._local_full_snapshot([SystemFile("sample-root", 1)], 1),
        ], root_shape_trace_enabled=False)

        self.assertEqual({}, accumulator.root_shape_trace())

    def test_progressive_accumulator_root_shape_trace_counts_admission_and_protection(self):
        accumulator = _ProgressiveScanAccumulator()
        initial = self._local_full_snapshot([SystemFile("protected-root", 1)], 1)
        initial.session_token = "session-a"
        accumulator.apply([initial])
        accumulator.begin_root_invalidation("pair", "protected-root", 2)

        current = self._local_full_snapshot([SystemFile("fresh-root", 2)], 2)
        stale = self._local_full_snapshot([SystemFile("stale-root", 3)], 1)
        current.session_token = "session-a"
        stale.session_token = "session-a"
        rejected_session = self._local_full_snapshot([SystemFile("rejected-root", 4)], 3)
        rejected_session.session_token = "session-b"
        accumulator.apply(
            [current, stale, rejected_session],
            root_shape_trace_enabled=True,
        )

        trace = accumulator.root_shape_trace()
        self.assertEqual(3, trace["incoming_event_count"])
        self.assertEqual(2, trace["admitted_event_count"])
        self.assertEqual(2, trace["full_snapshot_event_count"])
        self.assertEqual(1, trace["stale_rejected_event_count"])
        self.assertGreaterEqual(trace["protected_root_count"], 1)
        self.assertGreaterEqual(trace["working_root_count_pre_full_snapshot_clear"], 1)
        self.assertGreaterEqual(trace["working_root_count_post_full_snapshot_clear"], 1)
        self.assertEqual(2, trace["committed_root_count"])
        self.assertEqual(2, trace["visible_root_count"])

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

    def test_remote_hint_sync_uses_authority_drained_from_the_queued_snapshot(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root", 3)], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="stream",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        older_fingerprint = accumulator.accepted_root_fingerprints()["pair"]["root"]

        # This is the queued full snapshot that must be drained before the
        # next remote scan receives any hint.
        accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root", 4)], scanned_path_pair_ids={"pair"}, generation=2,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="stream",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])
        current_fingerprint = accumulator.accepted_root_fingerprints()["pair"]["root"]
        self.assertNotEqual(older_fingerprint, current_fingerprint)
        process = SimpleNamespace(session_token="stream", set_accepted_root_fingerprints=MagicMock())
        controller = SimpleNamespace(
            _Controller__progressive_remote_scan_state=accumulator,
            _Controller__remote_scan_process=process,
        )

        _sync_remote_scan_root_fingerprint_hints(controller)

        process.set_accepted_root_fingerprints.assert_called_once_with({
            "pair": {"root": current_fingerprint},
        })
        retained = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=3,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="stream",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
                unchanged_root_fingerprints_by_pair={"pair": {"root": current_fingerprint}},
            ),
        ])
        self.assertFalse(retained.failed)

    def test_progressive_accumulator_marker_trace_uses_public_safe_stream_fields(self):
        accumulator = _ProgressiveScanAccumulator()
        accumulator.apply([
            ScannerResult(
                datetime.now(), [SystemFile("root", 3)], scanned_path_pair_ids={"pair"}, generation=1,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="stream-a",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
            ),
        ])

        result = accumulator.apply([
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair"}, generation=2,
                is_progress=True, completed_path_pair_ids={"pair"}, session_token="stream-a",
                is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
                unchanged_root_fingerprints_by_pair={"pair": {"root": "0" * 64}},
            ),
        ], scan_marker_trace_enabled=True)

        self.assertTrue(result.failed)
        self.assertEqual([{
            "schema": "scan_unchanged_root_marker.v1",
            "stream_binding": "same",
            "accumulator_stream_digest": trace_session_digest("stream-a"),
            "incoming_stream_digest": trace_session_digest("stream-a"),
            "generation_relation": "newer",
            "generation_class": "future",
            "accepted_hint_present": True,
            "accepted_hint_count": 1,
            "marker_count": 1,
            "mismatch_count": 1,
            "marker_after_rebind": False,
            "outcome": "digest_mismatch",
        }], list(accumulator.scan_marker_trace()))

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
            resume_source_identities={},
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
            resume_source_identities={},
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
                breadcrumb_trace=SimpleNamespace(
                    is_effectively_enabled=lambda category, level="info": True,
                ),
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
            _Controller__is_explicitly_stopped=MagicMock(return_value=False),
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

    def _make_v092_pending_completion_controller(
            self, *, complete=True, path_pair_id=None, sidecar_ready=False,
    ):
        """Create a retired-LFTP pending root without relying on a model diff."""
        remote = SystemFile("pending.bin", 10, False, mtime_ns=1)
        local = SystemFile(
            "pending.bin", 10 if complete else 9, False, is_staging=True, mtime_ns=1,
        )
        local.status_sidecar_ready = sidecar_ready
        remote.path_pair_id = path_pair_id
        local.path_pair_id = path_pair_id
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "pending.bin", "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(
            local.size, 10, local.size * 10, 100, 0,
        )
        running.path_pair_id = path_pair_id
        builder = ModelBuilder()
        builder.set_remote_files([remote])
        builder.set_local_files([local])
        builder.set_lftp_statuses([running])
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        controller.MoveFromStagingResult = Controller.MoveFromStagingResult
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        # ModelUpdater only treats the legacy scope as reconciled when both
        # scan sides explicitly covered it.  The focused retry fixtures start
        # from that already-authoritative steady state; the scan-authority
        # regressions below exercise missing and failed coverage directly.
        controller._Controller__reconciled_local_path_pair_ids = {path_pair_id}
        controller._Controller__reconciled_remote_path_pair_ids = {path_pair_id}
        controller._Controller__prev_downloading_file_names = {("pending.bin", path_pair_id, None)}
        controller._Controller__lftp.status.return_value = []
        controller._reserve_move_attempt = MagicMock(return_value=True)
        controller._release_move_attempt = MagicMock()
        controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )
        controller._record_download_completion = MagicMock()
        controller._complete_download_start_lifecycle = MagicMock()
        controller.clear_extracted_marker = MagicMock()
        controller._mark_successful_final_move_handoff = MagicMock()
        controller._mark_current_process_final_publication = MagicMock()
        controller._Controller__target_archive_trace_selector_matches_file = MagicMock(return_value=False)
        controller._Controller__target_archive_trace_selector_matches_file = MagicMock(return_value=False)
        return controller

    def _make_active_delta_lineage_fixture(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        live_model = builder.build_model()
        remote_scan = ScannerResult(
            datetime.now(), [SystemFile("root", 100, False)],
            scanned_path_pair_ids={None}, completed_path_pair_ids={None}, is_scan_final=True,
        )
        controller, _ = self._make_progressive_update_controller(
            remote_scan, local_scan=None, model_builder=builder, model=live_model,
        )
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        controller._Controller__context.breadcrumb_trace = trace
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [status]
        controller._take_lftp_status_poll_correlation = lambda: "lftp-poll:0123456789abcdef"
        return controller, builder, trace

    @staticmethod
    def _lineage_phases(trace):
        return [
            step["phase"] for step in trace.snapshot()["progress_lineage"]["spans"][0]["steps"]
        ]

    def _make_v092_fast_get_controller(
            self, *, local_size=100, local_mtime_ns=1700000000000000000,
            local_scan=True, remote_scan=True, stopped=False, defer_scans=False,
            pending_dispatch=True):
        """Build the queue-ack/idle-poll boundary for the fast-GET race."""
        remote = SystemFile("fast-get.bin", 100, False, mtime_ns=local_mtime_ns)
        staged = SystemFile(
            "fast-get.bin", local_size, False, is_staging=True,
            mtime_ns=local_mtime_ns,
        )
        builder = ModelBuilder()
        builder.set_remote_files([remote])
        live_model = builder.build_model()
        remote_result = ScannerResult(
            datetime.now(), [remote], scanned_path_pair_ids={None},
            is_scan_final=True,
        ) if remote_scan else None
        local_result = ScannerResult(
            datetime.now(), [staged], scanned_path_pair_ids={None},
            is_scan_final=True,
        ) if local_scan else None
        controller, _ = self._make_progressive_update_controller(
            remote_result,
            local_scan=local_result,
            model_builder=builder,
            model=live_model,
        )
        if defer_scans:
            controller._Controller__remote_scan_process.pop_latest_result.side_effect = [
                None, remote_result,
            ]
            controller._Controller__local_scan_process.pop_latest_result.side_effect = [
                None, local_result,
            ]
        file_id = ModelFile.build_file_id("fast-get.bin", None)
        controller._Controller__work_state_lock = RLock()
        controller._Controller__pending_queue_dispatches = {
            file_id: PendingQueueDispatch(
                0.0, "fast-get.bin", None, False, 7, (100, 1700000000),
            ),
        } if pending_dispatch else {}
        controller._Controller__queue_dispatch_pending = (
            Controller._Controller__queue_dispatch_pending.__get__(controller, Controller)
        )
        controller._lftp_statuses_with_pending_dispatches = (
            Controller._lftp_statuses_with_pending_dispatches.__get__(controller, Controller)
        )
        controller._reconcile_pending_queue_dispatches_from_fresh_status = (
            Controller._reconcile_pending_queue_dispatches_from_fresh_status.__get__(
                controller, Controller,
            )
        )
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=stopped)
        controller._Controller__prev_downloading_file_names = set()
        controller._Controller__lftp.status.return_value = []
        controller._Controller__lftp.last_status_poll_healthy = True
        controller.MoveFromStagingResult = Controller.MoveFromStagingResult
        controller._reserve_move_attempt = MagicMock(return_value=True)
        controller._release_move_attempt = MagicMock()
        controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )
        controller._record_download_completion = MagicMock()
        controller._complete_download_start_lifecycle = MagicMock()
        controller.clear_extracted_marker = MagicMock()
        controller._mark_successful_final_move_handoff = MagicMock()
        controller._mark_current_process_final_publication = MagicMock()
        controller._Controller__target_archive_trace_selector_matches_file = MagicMock(return_value=False)
        if stopped:
            controller._Controller__persist.stopped_file_names.add(file_id)
        return controller, live_model, file_id

    def _prepare_v092_stale_pre_queue_poll(self, controller, file_id):
        """Install a completed pre-Queue idle poll and an unfinished queue op."""
        class DeferredStatusExecutor:
            def __init__(self):
                self.submissions = []

            def submit(self, operation):
                future = Future()
                self.submissions.append((operation, future))
                return future

            def run_next(self):
                operation, future = self.submissions.pop(0)
                try:
                    future.set_result(operation())
                except Exception as error:
                    future.set_exception(error)

        status_future = Future()
        controller._Controller__lftp_status_future = status_future
        controller._Controller__uses_async_lftp_owner = MagicMock(return_value=True)
        controller._Controller__lftp_executor = DeferredStatusExecutor()
        controller._Controller__ensure_lftp_executor = (
            Controller._Controller__ensure_lftp_executor.__get__(controller, Controller)
        )
        controller._Controller__submit_lftp_operation = (
            Controller._Controller__submit_lftp_operation.__get__(controller, Controller)
        )
        controller.wake_process = MagicMock()
        controller._get_lftp_status_snapshot = (
            Controller._get_lftp_status_snapshot.__get__(controller, Controller)
        )
        queue_future = Future()
        controller._Controller__lftp_operation_sequences = {file_id: 7}
        controller._Controller__lftp_failed_operation_sequences = set()
        controller._Controller__lftp_operations = [
            _LftpOperation("queue", queue_future, file_id, 7),
        ]
        controller._Controller__lftp_operation_is_current = (
            Controller._Controller__lftp_operation_is_current.__get__(controller, Controller)
        )
        controller._Controller__download_start_lock = RLock()
        controller._Controller__download_start_state = {}
        controller._Controller__restore_failed_queue_lifecycle = (
            Controller._Controller__restore_failed_queue_lifecycle.__get__(controller, Controller)
        )
        controller._Controller__drain_lftp_operations = (
            Controller._Controller__drain_lftp_operations.__get__(controller, Controller)
        )

        # The status future began before Queue installed this dispatch. Queue
        # acceptance is visible when the pre-Queue empty status
        # completes.  The real status-snapshot seam consumes that old result;
        # this mirrors the controller's post-poll scheduler bookkeeping.
        controller._Controller__pending_queue_dispatches[file_id] = PendingQueueDispatch(
            0.0, "fast-get.bin", None, False, 7, (100, 1700000000),
        )
        status_future.set_result(([], True))
        self.assertEqual(([], True), controller._get_lftp_status_snapshot())
        controller._Controller__last_lftp_statuses = []
        # The direct Controller test owns the Queue submission boundary. This
        # updater fixture starts immediately after that boundary, where the
        # pre-Queue future reference and idle authority were invalidated.
        controller._Controller__lftp_idle_status_authoritative = False
        controller._Controller__next_lftp_status_poll_at = None
        controller._Controller__lftp_status_cache_expires_at = (
            datetime.now() + timedelta(seconds=3)
        )
        return queue_future, controller._Controller__lftp_executor

    def test_v092_fast_get_idle_exact_staging_finalizes_optimistic_queue(self):
        """A fast GET can finish between Queue acknowledgement and first status."""
        controller, live_model, file_id = self._make_v092_fast_get_controller(
            defer_scans=True,
        )

        ModelUpdater(controller).update()
        self.assertEqual(ModelFile.State.QUEUED, live_model.get_file(file_id).state)
        self.assertEqual([], controller._Controller__last_lftp_statuses)
        self.assertEqual([], controller._Controller__active_downloading_file_names)
        self.assertNotIn(file_id, controller._Controller__pending_queue_dispatches)
        self.assertIn(("fast-get.bin", None, None), controller._Controller__pending_completion_file_names)
        controller._Controller__move_from_staging.assert_not_called()
        ModelUpdater(controller).update()
        self.assertEqual({None}, controller._Controller__reconciled_local_path_pair_ids)
        self.assertEqual({None}, controller._Controller__reconciled_remote_path_pair_ids)
        self.assertTrue(controller._Controller__model_builder.has_complete_local_coverage(file_id))
        self.assertTrue(controller._Controller__model_builder.has_verified_complete_staging_remote_identity(file_id))

        file = live_model.get_file(file_id)
        self.assertEqual(
            (
                True,
                ModelFile.State.DOWNLOADED,
                100,
                100,
                [call("fast-get.bin", None)],
                {file_id},
                {},
            ),
            (
                file_id not in controller._Controller__pending_queue_dispatches,
                file.state,
                file.remote_size,
                file.transferred_size,
                controller._Controller__move_from_staging.call_args_list,
                controller._Controller__persist.downloaded_file_names,
                controller._Controller__persist.resume_source_identities,
            ),
        )
        controller._Controller__remote_scan_process.pop_latest_result.side_effect = None
        controller._Controller__local_scan_process.pop_latest_result.side_effect = None
        ModelUpdater(controller).update()
        controller._Controller__move_from_staging.assert_called_once_with("fast-get.bin", None)

    def test_v092_fast_get_idle_without_exact_reconciliation_does_not_move_or_bind(self):
        controller, live_model, file_id = self._make_v092_fast_get_controller(
            local_size=99, remote_scan=False,
        )

        ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_not_called()
        self.assertEqual({}, controller._Controller__persist.resume_source_identities)
        self.assertNotEqual(ModelFile.State.DOWNLOADED, live_model.get_file(file_id).state)

    def test_v092_fast_get_stop_before_idle_remains_stopped(self):
        controller, live_model, file_id = self._make_v092_fast_get_controller(stopped=True)

        ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_not_called()
        self.assertEqual({}, controller._Controller__persist.resume_source_identities)
        self.assertIn(file_id, controller._Controller__persist.stopped_file_names)
        self.assertNotEqual(ModelFile.State.DOWNLOADED, live_model.get_file(file_id).state)

    def test_v092_fast_get_stale_pre_queue_idle_does_not_force_post_dispatch_poll(self):
        """A status result started before Queue must not retire the next lifecycle."""
        controller, live_model, file_id = self._make_v092_fast_get_controller(
            defer_scans=True, pending_dispatch=False,
        )
        queue_future, status_executor = self._prepare_v092_stale_pre_queue_poll(controller, file_id)

        ModelUpdater(controller).update()
        self.assertEqual(ModelFile.State.QUEUED, live_model.get_file(file_id).state)

        queue_future.set_result(None)
        controller._Controller__drain_lftp_operations()
        self.assertEqual([], controller._Controller__lftp_operations)
        self.assertEqual(0, controller._Controller__lftp.status.call_count)
        status_executor.run_next()
        ModelUpdater(controller).update()

        self.assertEqual({None}, controller._Controller__reconciled_local_path_pair_ids)
        self.assertEqual({None}, controller._Controller__reconciled_remote_path_pair_ids)
        self.assertTrue(controller._Controller__model_builder.has_complete_local_coverage(file_id))
        self.assertTrue(controller._Controller__model_builder.has_verified_complete_staging_remote_identity(file_id))
        file = live_model.get_file(file_id)
        self.assertEqual(
            (
                True,
                ModelFile.State.DOWNLOADED,
                100,
                100,
                [call("fast-get.bin", None)],
                {file_id},
                {},
                set(),
                1,
            ),
            (
                file_id not in controller._Controller__pending_queue_dispatches,
                file.state,
                file.remote_size,
                file.transferred_size,
                controller._Controller__move_from_staging.call_args_list,
                controller._Controller__persist.downloaded_file_names,
                controller._Controller__persist.resume_source_identities,
                controller._Controller__pending_completion_file_names,
                controller._Controller__lftp.status.call_count,
            ),
        )

    def test_v092_fast_get_stale_idle_alone_does_not_handoff_completion(self):
        controller, live_model, file_id = self._make_v092_fast_get_controller(defer_scans=True)
        queue_future, _ = self._prepare_v092_stale_pre_queue_poll(controller, file_id)

        ModelUpdater(controller).update()
        self.assertFalse(queue_future.done())
        ModelUpdater(controller).update()

        self.assertIn(file_id, controller._Controller__pending_queue_dispatches)
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        controller._Controller__move_from_staging.assert_not_called()
        self.assertEqual({}, controller._Controller__persist.resume_source_identities)

    def test_v092_fast_get_post_queue_running_status_binds_without_completion_handoff(self):
        controller, _, file_id = self._make_v092_fast_get_controller(
            defer_scans=True, pending_dispatch=False,
        )
        queue_future, status_executor = self._prepare_v092_stale_pre_queue_poll(controller, file_id)
        running = LftpJobStatus(
            7, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "fast-get.bin", "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(1, 100, 1, 100, 1)

        ModelUpdater(controller).update()
        queue_future.set_result(None)
        controller._Controller__drain_lftp_operations()
        controller._Controller__lftp.status.return_value = [running]
        status_executor.run_next()
        ModelUpdater(controller).update()

        self.assertEqual({file_id: (100, 1700000000)}, controller._Controller__persist.resume_source_identities)
        self.assertNotIn(file_id, controller._Controller__pending_queue_dispatches)
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        controller._Controller__move_from_staging.assert_not_called()

    def test_v092_fast_get_failed_or_cancelled_queue_future_never_registers_completion(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                controller, live_model, file_id = self._make_v092_fast_get_controller(
                    defer_scans=True, pending_dispatch=False,
                )
                queue_future, _ = self._prepare_v092_stale_pre_queue_poll(controller, file_id)
                ModelUpdater(controller).update()

                if cancelled:
                    queue_future.cancel()
                else:
                    queue_future.set_exception(RuntimeError("queue failed"))
                controller._Controller__drain_lftp_operations()

                self.assertNotIn(file_id, controller._Controller__pending_queue_dispatches)
                self.assertEqual(set(), controller._Controller__pending_completion_file_names)
                controller._Controller__move_from_staging.assert_not_called()
                self.assertEqual({}, controller._Controller__persist.resume_source_identities)
                self.assertNotEqual(ModelFile.State.DOWNLOADED, live_model.get_file(file_id).state)

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

    def test_progressive_unknown_authority_blocks_queue_exclusions_until_healthy_final(self):
        base_mtime_ns = 1_786_400_003_000_000_000

        def scan_root():
            remote_root = SystemFile("release", 100, True)
            remote_root.add_child(SystemFile(
                "existing.mkv", 100, False, mtime_ns=base_mtime_ns + 100,
            ))
            local_root = SystemFile("release", 100, True)
            local_root.add_child(SystemFile(
                "existing.mkv", 100, False, mtime_ns=base_mtime_ns,
            ))
            return remote_root, local_root

        for failed_side in ("local", "remote", "incomplete"):
            with self.subTest(failed_side=failed_side):
                remote_root, local_root = scan_root()
                remote_token = "remote-queue-authority-{}".format(failed_side)
                local_token = "local-queue-authority-{}".format(failed_side)
                initial_remote = ScannerResult(
                    datetime.now(), [remote_root], scanned_path_pair_ids={None}, generation=1,
                    is_progress=True, completed_path_pair_ids={None}, is_scan_final=True,
                    session_token=remote_token, is_full_snapshot=True,
                    full_snapshot_path_pair_ids={None},
                )
                initial_local = ScannerResult(
                    datetime.now(), [local_root], scanned_path_pair_ids={None}, generation=1,
                    is_progress=True, completed_path_pair_ids={None}, is_scan_final=True,
                    session_token=local_token, is_full_snapshot=True,
                    full_snapshot_path_pair_ids={None},
                )
                failed = ScannerResult(
                    datetime.now(), [], scanned_path_pair_ids={None}, generation=2,
                    is_progress=True, is_scan_final=failed_side != "incomplete", session_token=(
                        remote_token if failed_side == "remote" else local_token
                    ), unknown_path_pair_ids={None},
                    failed=failed_side != "incomplete",
                )
                recovered = ScannerResult(
                    datetime.now(), [remote_root if failed_side == "remote" else local_root],
                    scanned_path_pair_ids={None}, generation=3, is_progress=True,
                    completed_path_pair_ids={None}, is_scan_final=True,
                    session_token=(remote_token if failed_side == "remote" else local_token),
                    is_full_snapshot=True, full_snapshot_path_pair_ids={None},
                )
                builder = ModelBuilder()
                live_model = builder.build_model()
                controller, _ = self._make_progressive_update_controller(
                    None, local_scan=None, authoritative=False,
                    model_builder=builder, model=live_model,
                )
                controller._Controller__remote_scan_process = self._progressive_process(
                    remote_token,
                    [[initial_remote], [failed] if failed_side == "remote" else [],
                     [recovered] if failed_side == "remote" else []],
                )
                controller._Controller__local_scan_process = self._progressive_process(
                    local_token,
                    [[initial_local], [failed] if failed_side != "remote" else [],
                     [recovered] if failed_side != "remote" else []],
                )
                file_id = ModelFile.build_file_id("release", None)
                updater = ModelUpdater(controller)

                updater.update()
                self.assertEqual(("existing.mkv",), builder.get_trusted_final_leaf_paths(file_id))

                updater.update()
                self.assertEqual((), builder.get_trusted_final_leaf_paths(file_id))

                updater.update()
                # An unchanged authoritative local final revalidates the
                # retained local authority even when source buckets need no
                # rebuild. A remote-only recovery still retains the overlay
                # until a local authority event confirms it.
                expected_paths = () if failed_side == "remote" else ("existing.mkv",)
                self.assertEqual(expected_paths, builder.get_trusted_final_leaf_paths(file_id))

    def test_two_pair_partial_recovery_retains_a_unknown_until_source_adoption(self):
        """A's retained leaf stays Queue-unsafe while B holds the joint scan open."""
        base_mtime_ns = 1_786_400_003_000_000_000

        def root(pair_id: str, leaves: list[str], mtime_offset: int = 0) -> SystemFile:
            value = SystemFile("release", len(leaves) * 100, True)
            value.path_pair_id = pair_id
            for leaf in leaves:
                value.add_child(SystemFile(leaf, 100, False, mtime_ns=base_mtime_ns + mtime_offset))
            return value

        def remote_root(pair_id: str, leaves: list[str]) -> SystemFile:
            return root(pair_id, leaves, 100)

        def result(
                files: list[SystemFile], generation: int, token: str, *,
                completed: set[str], unknown: set[str] = set(), final: bool = True,
                failed: bool = False,
        ) -> ScannerResult:
            return ScannerResult(
                datetime.now(), files, scanned_path_pair_ids={"pair-a", "pair-b"},
                generation=generation, is_progress=True, completed_path_pair_ids=completed,
                unknown_path_pair_ids=unknown, is_scan_final=final, failed=failed,
                is_full_snapshot=final, full_snapshot_path_pair_ids=completed if final else set(),
                session_token=token,
            )

        initial_local = result(
            [root("pair-a", ["stale.mkv", "keep.mkv"]), root("pair-b", ["b.mkv"])],
            1, "local-two-pair", completed={"pair-a", "pair-b"},
        )
        initial_remote = result(
            [remote_root("pair-a", ["stale.mkv", "keep.mkv"]), remote_root("pair-b", ["b.mkv"])],
            1, "remote-two-pair", completed={"pair-a", "pair-b"},
        )
        failed_a = result([], 2, "local-two-pair", completed=set(), unknown={"pair-a"}, failed=True)
        # A's current source no longer contains stale.mkv. B is explicitly
        # incomplete, so this partial transition cannot adopt either bucket.
        partial_local = result(
            [root("pair-a", ["keep.mkv"])], 3, "local-two-pair",
            completed={"pair-a"}, unknown={"pair-b"}, final=False,
        )
        final_local = result(
            [root("pair-a", ["keep.mkv"]), root("pair-b", ["b.mkv"])],
            4, "local-two-pair", completed={"pair-a", "pair-b"},
        )
        final_remote = result(
            [remote_root("pair-a", ["keep.mkv"]), remote_root("pair-b", ["b.mkv"])],
            2, "remote-two-pair", completed={"pair-a", "pair-b"},
        )
        builder = ModelBuilder()
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._Controller__local_scan_process = self._progressive_process(
            "local-two-pair", [[initial_local], [failed_a], [partial_local], [final_local]],
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            "remote-two-pair", [[initial_remote], [], [], [final_remote]],
        )
        updater = ModelUpdater(controller)
        a_id = ModelFile.build_file_id("release", "pair-a")

        updater.update()
        self.assertEqual(("keep.mkv", "stale.mkv"), builder.get_trusted_final_leaf_paths(a_id))

        updater.update()
        self.assertIn("pair-a", builder.unknown_local_path_pair_ids_snapshot())
        self.assertEqual((), builder.get_trusted_final_leaf_paths(a_id))

        updater.update()
        self.assertTrue({"pair-a", "pair-b"}.issubset(builder.unknown_local_path_pair_ids_snapshot()))
        self.assertEqual((), builder.get_trusted_final_leaf_paths(a_id))

        updater.update()
        self.assertEqual(frozenset(), builder.unknown_local_path_pair_ids_snapshot())
        self.assertEqual(("keep.mkv",), builder.get_trusted_final_leaf_paths(a_id))

    def test_healthy_progressive_noop_refresh_does_not_dirty_real_builder(self):
        remote_root = SystemFile("release", 1, True)
        remote_root.add_child(SystemFile("existing.mkv", 1, False, mtime_ns=1_786_400_003_000_000_100))
        local_root = SystemFile("release", 1, True)
        local_root.add_child(SystemFile("existing.mkv", 1, False, mtime_ns=1_786_400_003_000_000_000))
        remote_token = "remote-queue-noop"
        local_token = "local-queue-noop"
        initial_remote = self._progressive_final_result(remote_token)
        initial_local = self._progressive_final_result(local_token)
        initial_remote.files = [remote_root]
        initial_local.files = [local_root]
        builder = ModelBuilder()
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
            model_builder=builder, model=live_model,
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], []],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], []],
        )
        updater = ModelUpdater(controller)

        updater.update()
        self.assertFalse(builder.has_changes())
        updater.update()
        self.assertFalse(builder.has_changes())

    def test_remote_unknown_progress_keeps_real_builder_cache_through_unchanged_final(self):
        def root(path_pair_id: str, mtime_ns: int) -> SystemFile:
            value = SystemFile("release", 1, True)
            value.path_pair_id = path_pair_id
            value.add_child(SystemFile("existing.mkv", 1, False, mtime_ns=mtime_ns))
            return value

        def result(files, generation, token, *, completed, unknown=None, final=True, scanned=None):
            return ScannerResult(
                datetime.now(), files, scanned_path_pair_ids=scanned or {"pair-a", "pair-b"},
                generation=generation, is_progress=True, completed_path_pair_ids=completed,
                unknown_path_pair_ids=set(unknown or set()), is_scan_final=final,
                is_full_snapshot=final, full_snapshot_path_pair_ids=completed if final else set(),
                session_token=token,
            )

        remote_token = "remote-unknown-cache"
        local_token = "local-standing-cache"
        initial_remote = result([root("pair-a", 2), root("pair-b", 2)], 1, remote_token,
                                completed={"pair-a", "pair-b"})
        initial_local = result([root("pair-a", 1), root("pair-b", 1)], 1, local_token,
                               completed={"pair-a", "pair-b"})
        remote_unknown_a = result([], 2, remote_token, completed=set(), unknown={"pair-a"}, final=False,
                                  scanned={"pair-a"})
        remote_unknown_b = result([], 2, remote_token, completed=set(), unknown={"pair-b"}, final=False,
                                  scanned={"pair-b"})
        unchanged_remote_final = result([root("pair-a", 2), root("pair-b", 2)], 2, remote_token,
                                        completed={"pair-a", "pair-b"})
        builder = ModelBuilder()
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], [remote_unknown_a], [remote_unknown_b], [unchanged_remote_final]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], [], [], []],
        )
        updater = ModelUpdater(controller)
        a_id = ModelFile.build_file_id("release", "pair-a")

        updater.update()
        self.assertFalse(builder.has_changes())
        builder.build_model = MagicMock(wraps=builder.build_model)

        updater.update()
        self.assertEqual(frozenset({"pair-a"}), builder.unknown_local_path_pair_ids_snapshot())
        self.assertEqual((), builder.get_trusted_final_leaf_paths(a_id))
        updater.update()
        self.assertEqual(frozenset({"pair-a", "pair-b"}), builder.unknown_local_path_pair_ids_snapshot())
        updater.update()

        self.assertEqual(frozenset({"pair-a", "pair-b"}), builder.unknown_local_path_pair_ids_snapshot())
        self.assertEqual((), builder.get_trusted_final_leaf_paths(a_id))
        builder.build_model.assert_not_called()

    def test_running_mirror_progress_adopts_active_delta_during_unknown_overlay_churn(self):
        def root(path_pair_id: str, size: int) -> SystemFile:
            value = SystemFile("release", size, True)
            value.path_pair_id = path_pair_id
            value.add_child(SystemFile("existing.mkv", size, False, mtime_ns=1))
            return value

        def result(files, generation, token, *, completed, unknown=None, final=True, scanned=None):
            return ScannerResult(
                datetime.now(), files, scanned_path_pair_ids=scanned or {"pair-a", "pair-b"},
                generation=generation, is_progress=True, completed_path_pair_ids=completed,
                unknown_path_pair_ids=set(unknown or set()), is_scan_final=final,
                is_full_snapshot=final, full_snapshot_path_pair_ids=completed if final else set(),
                session_token=token,
            )

        remote_token = "remote-mirror-active"
        local_token = "local-mirror-standing"
        initial_remote = result(
            [root("pair-a", 100), root("pair-b", 200)], 1, remote_token,
            completed={"pair-a", "pair-b"},
        )
        initial_local = result(
            [root("pair-a", 25), root("pair-b", 200)], 1, local_token,
            completed={"pair-a", "pair-b"},
        )
        unknown_a = result(
            [], 2, remote_token, completed=set(), unknown={"pair-a"},
            final=False, scanned={"pair-a"},
        )
        unchanged_final = result(
            [root("pair-a", 100), root("pair-b", 200)], 2, remote_token,
            completed={"pair-a", "pair-b"},
        )
        builder = ModelBuilder()
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {"pair-a": MagicMock(), "pair-b": MagicMock()}
        controller._Controller__remote_scan_process = self._progressive_process(
            remote_token, [[initial_remote], [unknown_a], [unchanged_final]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            local_token, [[initial_local], [], []],
        )
        status = LftpJobStatus(
            21, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "",
        )
        status.path_pair_id = "pair-a"
        status.total_transfer_state = LftpJobStatus.TransferState(10, 80, 12, 100, 8)
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        controller._Controller__context.breadcrumb_trace = trace
        poll_correlations = iter((
            "lftp-poll:0123456789abcdef", "lftp-poll:fedcba9876543210",
            "lftp-poll:0011223344556677",
        ))
        controller._take_lftp_status_poll_correlation = lambda: next(poll_correlations)
        updater = ModelUpdater(controller)
        updater.update()
        builder.build_model = MagicMock(wraps=builder.build_model)

        controller._Controller__lftp.status.return_value = [status]
        controller._Controller__next_lftp_status_poll_at = None
        controller._Controller__lftp_idle_status_authoritative = False
        updater.update()

        active_id = ModelFile.build_file_id("release", "pair-a")
        unrelated_id = ModelFile.build_file_id("release", "pair-b")
        self.assertEqual(10, live_model.get_file(active_id).transferred_size)
        self.assertEqual(200, live_model.get_file(unrelated_id).transferred_size)
        self.assertEqual(frozenset({"pair-a"}), builder.unknown_local_path_pair_ids_snapshot())
        self.assertFalse(builder.has_changes())
        builder.build_model.assert_not_called()
        active_lineage = next(
            span for span in trace.snapshot()["progress_lineage"]["spans"]
            if span["correlation"] == "lftp-poll:fedcba9876543210"
        )
        self.assertEqual(
            [
                "status_consume", "pre_active_delta", "active_progress_overlay_admission",
                "direct_root_counter_publish", "active_delta_selector",
                "active_delta_builder", "active_delta_authorization", "active_delta_adoption",
                "model_mutation", "updater_decision",
            ],
            [step["phase"] for step in active_lineage["steps"]],
        )

        controller._Controller__next_lftp_status_poll_at = None
        updater.update()

        self.assertEqual(frozenset({"pair-a"}), builder.unknown_local_path_pair_ids_snapshot())
        builder.build_model.assert_not_called()

    def test_active_delta_lineage_selector_failure_stops_before_builder(self):
        controller, builder, trace = self._make_active_delta_lineage_fixture()
        builder.active_transfer_delta_file_ids = MagicMock(return_value=None)

        ModelUpdater(controller).update()

        phases = self._lineage_phases(trace)
        self.assertEqual(
            [
                "status_consume", "pre_active_delta", "active_progress_overlay_admission",
                "direct_root_counter_publish", "active_delta_selector",
                "updater_decision", "model_mutation",
            ],
            phases,
        )
        self.assertNotIn("active_delta_builder", phases)
        self.assertNotIn("active_delta_authorization", phases)
        self.assertNotIn("active_delta_adoption", phases)

    def test_nonfresh_status_keeps_pending_active_delta_dirty_without_selection_or_adoption(self):
        controller, builder, _ = self._make_active_delta_lineage_fixture()
        status = controller._Controller__lftp.status.return_value[0]
        builder.set_lftp_statuses([status])
        self.assertTrue(builder.has_pending_active_transfer_delta())
        self.assertTrue(builder.has_changes())

        # A completed but nonfresh cache is not evidence for active-delta
        # selection.  It must remain pending until a fresh healthy poll arrives.
        controller._Controller__last_lftp_statuses = [status]
        controller._get_lftp_status_snapshot = MagicMock(return_value=None)
        controller._Controller__lftp_idle_status_authoritative = False
        controller._Controller__active_scan_lftp_roots_awaiting = set()
        controller._Controller__active_scan_lftp_roots_seen = set()

        selector = MagicMock(wraps=builder.active_transfer_delta_file_ids)
        builder.active_transfer_delta_file_ids = selector
        delta_builder = MagicMock(wraps=builder.build_active_transfer_roots)
        builder.build_active_transfer_roots = delta_builder
        authorizer = MagicMock(wraps=builder.authorize_active_transfer_delta)
        builder.authorize_active_transfer_delta = authorizer
        adopter = MagicMock(wraps=builder.adopt_active_transfer_delta)
        builder.adopt_active_transfer_delta = adopter
        base_builder = MagicMock(wraps=builder.build_model)
        builder.build_model = base_builder
        model_update = MagicMock(wraps=controller._Controller__model.update_file)
        controller._Controller__model.update_file = model_update

        ModelUpdater(controller).update()

        selector.assert_not_called()
        delta_builder.assert_not_called()
        authorizer.assert_not_called()
        adopter.assert_not_called()
        base_builder.assert_not_called()
        model_update.assert_not_called()
        self.assertTrue(builder.has_pending_active_transfer_delta())
        self.assertTrue(builder.has_changes())

    def test_active_delta_lineage_builder_exception_stops_before_authorization(self):
        controller, builder, trace = self._make_active_delta_lineage_fixture()
        builder.build_active_transfer_roots = MagicMock(side_effect=RuntimeError("fixture"))

        ModelUpdater(controller).update()

        phases = self._lineage_phases(trace)
        self.assertEqual(
            [
                "status_consume", "pre_active_delta", "active_progress_overlay_admission",
                "direct_root_counter_publish", "active_delta_selector",
                "active_delta_builder", "updater_decision",
            ],
            phases,
        )
        self.assertNotIn("active_delta_authorization", phases)
        self.assertNotIn("active_delta_adoption", phases)

    def test_active_delta_lineage_authorizer_exception_records_exception_and_stops(self):
        controller, builder, trace = self._make_active_delta_lineage_fixture()
        builder.authorize_active_transfer_delta = MagicMock(side_effect=RuntimeError("fixture"))

        ModelUpdater(controller).update()

        phases = self._lineage_phases(trace)
        self.assertEqual(
            [
                "status_consume", "pre_active_delta", "active_progress_overlay_admission",
                "direct_root_counter_publish", "active_delta_selector",
                "active_delta_builder", "active_delta_authorization", "updater_decision",
            ],
            phases,
        )
        authorization = next(
            step for step in trace.snapshot()["progress_lineage"]["spans"][0]["steps"]
            if step["phase"] == "active_delta_authorization"
        )
        self.assertEqual("active_delta_authorization", authorization["phase"])
        self.assertEqual("exception", authorization["details"]["outcome"])
        self.assertNotIn("active_delta_adoption", phases)

    def test_active_delta_lineage_missing_root_stops_before_adoption(self):
        controller, builder, trace = self._make_active_delta_lineage_fixture()
        builder.build_active_transfer_roots = MagicMock(
            return_value=SimpleNamespace(model=Model()),
        )
        builder.authorize_active_transfer_delta = MagicMock(return_value=True)

        ModelUpdater(controller).update()

        phases = self._lineage_phases(trace)
        self.assertEqual(
            [
                "status_consume", "pre_active_delta", "active_progress_overlay_admission",
                "direct_root_counter_publish", "active_delta_selector",
                "active_delta_builder", "active_delta_authorization", "updater_decision",
                "model_mutation",
            ],
            phases,
        )
        authorization = next(
            step for step in trace.snapshot()["progress_lineage"]["spans"][0]["steps"]
            if step["phase"] == "active_delta_authorization"
        )
        self.assertEqual("ok", authorization["details"]["outcome"])
        self.assertNotIn("active_delta_adoption", phases)

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
        old_id = ModelFile.build_file_id("old.bin", "pair-a")
        builder.set_downloaded_timestamps({old_id: 1760000010.0})
        live_model = builder.build_model()
        idle_before = live_model.get_file(ModelFile.build_file_id("idle.bin", "pair-b"))
        before_version = live_model.version
        before_pair_a_scope_version = live_model.scope_version("pair-a")
        before_pair_b_scope_version = live_model.scope_version("pair-b")
        before_overlay_generation = live_model.downloaded_timestamp_overlay_generation
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
        self.assertEqual(before_overlay_generation, live_model.downloaded_timestamp_overlay_generation)
        # The selected pair replaces one root (remove + add), while the
        # metadata synchronization itself must not create extra versions or
        # touch an unaffected pair.
        self.assertEqual(before_version + 2, live_model.version)
        self.assertEqual(before_pair_a_scope_version + 2, live_model.scope_version("pair-a"))
        self.assertEqual(before_pair_b_scope_version, live_model.scope_version("pair-b"))
        # Canonical pair adoption replaces only A's buckets, and is therefore
        # the transition allowed to publish the reconciler's exact overlay.
        self.assertEqual(frozenset({"pair-b"}), builder.unknown_local_path_pair_ids_snapshot())
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

    def test_two_tick_progressive_final_keeps_summary_authority_aligned_without_local_event(self):
        """A remote-only final tick must consume the local final already held by the accumulator."""
        pair_id = "path-pair-a"

        def scanned_file(name, size):
            file = SystemFile(name, size)
            file.path_pair_id = pair_id
            return file

        local_final = ScannerResult(
            datetime.now(), [scanned_file("local-root", 7)],
            scanned_path_pair_ids={pair_id}, completed_path_pair_ids={pair_id},
            is_progress=True, is_scan_final=True, is_full_snapshot=True,
            full_snapshot_path_pair_ids={pair_id}, session_token="local-session",
        )
        remote_final = ScannerResult(
            datetime.now(), [scanned_file("remote-root", 7)],
            scanned_path_pair_ids={pair_id}, completed_path_pair_ids={pair_id},
            is_progress=True, is_scan_final=True, is_full_snapshot=True,
            full_snapshot_path_pair_ids={pair_id}, session_token="remote-session",
        )
        builder = ModelBuilder()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
            model_builder=builder, model=Model(),
        )
        controller._Controller__path_pairs_by_id = {pair_id: MagicMock()}
        controller._Controller__local_scan_process = self._progressive_process(
            "local-session", [[local_final], []],
        )
        controller._Controller__remote_scan_process = self._progressive_process(
            "remote-session", [[], [remote_final]],
        )

        summary_snapshots = []

        def read_summary():
            _, inventory = builder.local_library_inventory_snapshot()
            return {
                "scan_authority": dict(controller._Controller__scan_authority_snapshot),
                "inventory_state": inventory[pair_id].state,
                "reconciled": (
                    pair_id in controller._Controller__reconciled_local_path_pair_ids,
                    pair_id in controller._Controller__reconciled_remote_path_pair_ids,
                ),
            }

        controller.get_model_summary = read_summary
        controller.notify_model_summary_changed = lambda: summary_snapshots.append(
            controller.get_model_summary()
        )
        updater = ModelUpdater(controller)

        updater.update()
        _, first_inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("scanning", first_inventory[pair_id].state)
        self.assertEqual(frozenset({pair_id}), builder.unknown_local_path_pair_ids_snapshot())
        self.assertEqual({pair_id}, controller._Controller__reconciled_local_path_pair_ids)
        self.assertEqual(set(), controller._Controller__reconciled_remote_path_pair_ids)

        updater.update()
        _, final_inventory = builder.local_library_inventory_snapshot()
        self.assertEqual((1, 7, "up_to_date"), (
            final_inventory[pair_id].file_count,
            final_inventory[pair_id].size,
            final_inventory[pair_id].state,
        ))
        self.assertEqual(frozenset(), builder.unknown_local_path_pair_ids_snapshot())
        self.assertEqual({pair_id}, controller._Controller__reconciled_local_path_pair_ids)
        self.assertEqual({pair_id}, controller._Controller__reconciled_remote_path_pair_ids)

        authority_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "scan_authority"
        ]
        self.assertEqual(2, len(authority_calls))
        final_authority = authority_calls[-1].kwargs["details"]
        self.assertTrue(final_authority["final"])
        self.assertTrue(final_authority["joint_final"])
        self.assertTrue(final_authority["local_final"])
        self.assertTrue(final_authority["remote_final"])
        self.assertEqual(0, final_authority["local_scanned_pair_count"])
        self.assertEqual(1, final_authority["remote_scanned_pair_count"])
        self.assertEqual("adopt", final_authority["outcome"])
        self.assertEqual("source_buckets_adopted", final_authority["reason"])
        for key, value in controller._Controller__scan_authority_snapshot.items():
            self.assertEqual(value, final_authority[key])

        self.assertEqual(2, len(summary_snapshots))
        self.assertEqual("scanning", summary_snapshots[0]["inventory_state"])
        self.assertEqual((True, False), summary_snapshots[0]["reconciled"])
        self.assertEqual("up_to_date", summary_snapshots[-1]["inventory_state"])
        self.assertEqual((True, True), summary_snapshots[-1]["reconciled"])
        self.assertEqual(
            controller._Controller__scan_authority_snapshot,
            summary_snapshots[-1]["scan_authority"],
        )

    def test_combined_progressive_authority_scenario_keeps_overlay_and_snapshot_truthful(self):
        """Exercise replacement, no-op, pair delta, and failed-pair recovery together."""
        pair_a = "fixture-pair-a"
        pair_b = "fixture-pair-b"
        old_remote_session = "fixture-old-remote-session"
        old_local_session = "fixture-old-local-session"
        new_remote_session = "fixture-new-remote-session"
        new_local_session = "fixture-new-local-session"
        root_names = {
            pair_a: "fixture-root-a",
            pair_b: "fixture-root-b",
        }
        stale_root_name = "fixture-stale-root"
        configured_pairs = {pair_a, pair_b}

        def scan_file(pair_id):
            file = SystemFile(root_names[pair_id], 10 if pair_id == pair_a else 20)
            file.path_pair_id = pair_id
            return file

        def final_result(session_token, generation, pair_ids, *, targeted=False, files=None):
            selected = set(pair_ids)
            result_files = (
                list(files)
                if files is not None
                else [scan_file(pair_id) for pair_id in sorted(selected)]
            )
            return ScannerResult(
                datetime.now(), result_files,
                scanned_path_pair_ids=selected,
                generation=generation,
                is_progress=True,
                completed_path_pair_ids=selected,
                is_scan_final=True,
                unknown_path_pair_ids=set(),
                session_token=session_token,
                is_full_snapshot=True,
                full_snapshot_path_pair_ids=selected,
                is_targeted_scan=targeted,
            )

        def failed_result(session_token, generation, pair_id):
            return ScannerResult(
                datetime.now(), [],
                scanned_path_pair_ids={pair_id},
                generation=generation,
                is_progress=True,
                failed=True,
                error_message="fixture scan failure",
                unknown_path_pair_ids={pair_id},
                session_token=session_token,
                recoverable_failure_path_pair_ids={pair_id},
            )

        def stale_result(session_token):
            stale_file = SystemFile(stale_root_name, 999)
            stale_file.path_pair_id = pair_a
            return ScannerResult(
                datetime.now(), [stale_file],
                scanned_path_pair_ids={pair_a},
                generation=99,
                is_progress=True,
                completed_path_pair_ids={pair_a},
                is_scan_final=True,
                session_token=session_token,
                is_full_snapshot=True,
                full_snapshot_path_pair_ids={pair_a},
            )

        builder = ModelBuilder()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, authoritative=False,
            model_builder=builder, model=Model(),
        )
        controller._Controller__path_pairs_by_id = {
            pair_a: MagicMock(), pair_b: MagicMock(),
        }
        controller._Controller__remote_scan_process = self._progressive_process(
            old_remote_session,
            [[final_result(old_remote_session, 1, configured_pairs)]],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            old_local_session,
            [[final_result(old_local_session, 1, configured_pairs)]],
        )

        trace = BreadcrumbTraceCollector(lambda: True, max_entries=128)
        controller._Controller__context.breadcrumb_trace = trace

        def record_breadcrumb(**kwargs):
            metadata = {
                "stage": kwargs["stage"],
                "event_type": kwargs["event_type"],
                "trace_scope": kwargs.get("trace_scope", "flow"),
            }
            for key in ("category", "level", "corr_id", "flow_id"):
                if key in kwargs:
                    metadata[key] = kwargs[key]
            trace.record(
                "controller", kwargs["message"], kwargs.get("details"), **metadata,
            )

        controller._Controller__record_breadcrumb = record_breadcrumb
        updater = ModelUpdater(controller)
        authority_seen = 0

        def authority_events():
            # Query immediately after each update so this uses the real
            # collector's synchronous in-memory retrieval path.
            return trace.query_events(
                category="scan.authority", stage="scan_authority", limit=128,
            )["events"]

        def run_update(expected_new_events):
            nonlocal authority_seen
            updater.update()
            events = authority_events()
            new_events = events[authority_seen:]
            self.assertEqual(expected_new_events, len(new_events))
            standing = controller._Controller__scan_authority_snapshot
            for event in new_events:
                details = event["details"]
                for key, value in standing.items():
                    self.assertIn(key, details)
                    self.assertEqual(value, details[key], key)
            authority_seen = len(events)
            return new_events

        baseline = run_update(1)[0]["details"]
        self.assertEqual("adopt", baseline["outcome"])
        self.assertTrue(baseline["joint_authoritative_after"])
        baseline_snapshot = dict(controller._Controller__scan_authority_snapshot)

        recovered_remote = final_result(new_remote_session, 3, configured_pairs)
        recovered_local = final_result(new_local_session, 3, configured_pairs)
        no_op_remote = final_result(
            new_remote_session, 3, configured_pairs, files=recovered_remote.files,
        )
        no_op_local = final_result(
            new_local_session, 3, configured_pairs, files=recovered_local.files,
        )

        # Replacing the scanner sessions must discard queued rows from the old
        # sessions before any new authority event is emitted.
        controller._Controller__remote_scan_process = self._progressive_process(
            new_remote_session,
            [
                [stale_result(old_remote_session)],
                [failed_result(new_remote_session, 2, pair_b)],
                [recovered_remote],
                [],
                [no_op_remote],
                [final_result(new_remote_session, 4, {pair_a}, targeted=True)],
                [failed_result(new_remote_session, 5, pair_b)],
                [final_result(new_remote_session, 6, {pair_b}, targeted=True)],
            ],
        )
        controller._Controller__local_scan_process = self._progressive_process(
            new_local_session,
            [
                [stale_result(old_local_session)],
                [failed_result(new_local_session, 2, pair_b)],
                [recovered_local],
                [],
                [no_op_local],
                [final_result(new_local_session, 4, {pair_a}, targeted=True)],
                [failed_result(new_local_session, 5, pair_b)],
                [final_result(new_local_session, 6, {pair_b}, targeted=True)],
            ],
        )

        self.assertEqual([], run_update(0))
        self.assertEqual(baseline_snapshot, controller._Controller__scan_authority_snapshot)
        self.assertNotIn(stale_root_name, controller._Controller__model.get_file_names())

        failed = run_update(1)[0]["details"]
        self.assertEqual("no_op", failed["outcome"])
        self.assertEqual("joint_not_final", failed["reason"])
        self.assertEqual(2, failed["unknown_overlay_after_count"])

        recovered = run_update(1)[0]["details"]
        self.assertEqual("adopt", recovered["outcome"])
        self.assertEqual("source_buckets_adopted", recovered["reason"])
        self.assertEqual(0, recovered["unknown_overlay_after_count"])
        self.assertTrue(recovered["joint_authoritative_after"])

        self.assertEqual([], run_update(0))
        no_op = run_update(1)[0]["details"]
        self.assertEqual("no_op", no_op["outcome"])
        self.assertEqual("comparison_proven_noop", no_op["reason"])
        self.assertEqual(2, no_op["local_noop_count"])

        pair_a_final = run_update(1)[0]["details"]
        self.assertEqual("adopt", pair_a_final["outcome"])
        self.assertEqual("pair_delta_adopted", pair_a_final["reason"])
        self.assertTrue(pair_a_final["pair_delta_allowed"])
        self.assertFalse(pair_a_final["pair_delta_fallback"])
        self.assertEqual(0, pair_a_final["unknown_overlay_after_count"])

        failed_again = run_update(1)[0]["details"]
        self.assertEqual("no_op", failed_again["outcome"])
        self.assertEqual(1, failed_again["unknown_overlay_after_count"])

        pair_b_final = run_update(1)[0]["details"]
        self.assertEqual("adopt", pair_b_final["outcome"])
        self.assertEqual("pair_delta_adopted", pair_b_final["reason"])
        self.assertTrue(pair_b_final["pair_delta_allowed"])
        self.assertFalse(pair_b_final["pair_delta_fallback"])
        self.assertEqual(0, pair_b_final["unknown_overlay_after_count"])

        fixture_values = (
            pair_a, pair_b, old_remote_session, old_local_session,
            new_remote_session, new_local_session, *root_names.values(),
            stale_root_name,
        )
        for event in authority_events():
            for value in fixture_values:
                self.assertNotIn(value, str(event))

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

    def test_pair_candidate_exact_staging_pending_completion_moves_without_model_diff(self):
        """A selected final pair supplies the physical proof before adoption."""
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        builder = ModelBuilder()
        builder.set_local_files([old])
        builder.set_remote_files([old])
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)

        staged = SystemFile("release.bin", 30, False, is_staging=True, mtime_ns=1)
        staged.path_pair_id = "pair-a"
        remote = SystemFile("release.bin", 30, False, mtime_ns=1)
        remote.path_pair_id = "pair-a"
        final_local = ScannerResult(
            datetime.now(), [staged], scanned_path_pair_ids={"pair-a"},
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
        controller._Controller__pending_completion_file_names = {("release.bin", "pair-a", None)}
        controller.MoveFromStagingResult = Controller.MoveFromStagingResult
        controller._reserve_move_attempt = MagicMock(return_value=True)
        controller._release_move_attempt = MagicMock()
        controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )
        controller._record_download_completion = MagicMock()
        controller._complete_download_start_lifecycle = MagicMock()
        controller.clear_extracted_marker = MagicMock()
        controller._mark_successful_final_move_handoff = MagicMock()
        controller._mark_current_process_final_publication = MagicMock()
        controller._Controller__target_archive_trace_selector_matches_file = MagicMock(return_value=False)

        # The candidate itself changes the selected root, but the pending
        # completion owner must not require a ModelDiff to attempt its move.
        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_called_once_with("release.bin", "pair-a")
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        self.assertEqual(
            {ModelFile.build_file_id("release.bin", "pair-a")},
            controller._Controller__persist.downloaded_file_names,
        )
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

    def test_pair_final_keeps_rendered_nested_lifecycle_markers_after_settled_rebuild(self):
        path_pair_id = "pair-a"
        remote_root = SystemFile("release", 10, True)
        remote_nested = SystemFile("nested", 10, True)
        remote_nested.add_child(SystemFile("completed.bin", 10, False))
        remote_root.add_child(remote_nested)
        remote_root.path_pair_id = path_pair_id
        local_root = SystemFile("release", 10, True)
        local_nested = SystemFile("nested", 10, True)
        local_nested.add_child(SystemFile("completed.bin", 10, False))
        local_root.add_child(local_nested)
        local_root.path_pair_id = path_pair_id
        child_id = ModelFile.build_file_id("release/nested/completed.bin", path_pair_id)

        builder = ModelBuilder()
        builder.set_local_files([local_root])
        builder.set_remote_files([remote_root])
        live_model = builder.build_model()
        final_local = ScannerResult(
            datetime.now(), [local_root], scanned_path_pair_ids={path_pair_id},
            is_progress=True, completed_path_pair_ids={path_pair_id}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={path_pair_id},
        )
        final_remote = ScannerResult(
            datetime.now(), [remote_root], scanned_path_pair_ids={path_pair_id},
            is_progress=True, completed_path_pair_ids={path_pair_id}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={path_pair_id},
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {path_pair_id: MagicMock()}
        controller._reserve_move_attempt = MagicMock(return_value=False)
        persist = controller._Controller__persist
        persist.downloaded_file_names = {child_id}
        persist.downloaded_timestamps = {child_id: 1.0}
        persist.final_move_succeeded_file_names = {child_id}

        ModelUpdater(controller).update()

        self.assertEqual({child_id}, persist.downloaded_file_names)
        self.assertEqual({child_id: 1.0}, persist.downloaded_timestamps)
        self.assertEqual({child_id}, persist.final_move_succeeded_file_names)

        # A fresh ModelBuilder simulates the post-restart persisted rebuild:
        # the descendant is not a model root, but remains rendered beneath it.
        fresh_remote_root = SystemFile("release", 10, True)
        fresh_remote_nested = SystemFile("nested", 10, True)
        fresh_remote_nested.add_child(SystemFile("completed.bin", 10, False))
        fresh_remote_root.add_child(fresh_remote_nested)
        fresh_remote_root.path_pair_id = path_pair_id
        fresh_local_root = SystemFile("release", 10, True)
        fresh_local_nested = SystemFile("nested", 10, True)
        fresh_local_nested.add_child(SystemFile("completed.bin", 10, False))
        fresh_local_root.add_child(fresh_local_nested)
        fresh_local_root.path_pair_id = path_pair_id
        fresh_builder = ModelBuilder()
        fresh_builder.set_local_files([fresh_local_root])
        fresh_builder.set_remote_files([fresh_remote_root])
        fresh_model = fresh_builder.build_model()
        fresh_final_local = ScannerResult(
            datetime.now(), [fresh_local_root], scanned_path_pair_ids={path_pair_id},
            is_progress=True, completed_path_pair_ids={path_pair_id}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={path_pair_id},
        )
        fresh_final_remote = ScannerResult(
            datetime.now(), [fresh_remote_root], scanned_path_pair_ids={path_pair_id},
            is_progress=True, completed_path_pair_ids={path_pair_id}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={path_pair_id},
        )
        fresh_controller, _ = self._make_progressive_update_controller(
            fresh_final_remote, local_scan=fresh_final_local,
            model_builder=fresh_builder, model=fresh_model,
        )
        fresh_controller._Controller__persist = persist
        fresh_controller._Controller__path_pairs_by_id = {path_pair_id: MagicMock()}
        fresh_controller._reserve_move_attempt = MagicMock(return_value=False)

        ModelUpdater(fresh_controller).update()

        self.assertEqual({child_id}, persist.downloaded_file_names)
        self.assertEqual({child_id: 1.0}, persist.downloaded_timestamps)
        self.assertEqual({child_id}, persist.final_move_succeeded_file_names)

    def test_pair_final_removes_absent_nested_lifecycle_markers(self):
        path_pair_id = "pair-a"
        root = SystemFile("release", 10, True)
        root.add_child(SystemFile("present.bin", 10, False))
        root.path_pair_id = path_pair_id
        absent_child_id = ModelFile.build_file_id("release/nested/missing.bin", path_pair_id)
        builder = ModelBuilder()
        builder.set_local_files([root])
        builder.set_remote_files([root])
        live_model = builder.build_model()
        final = ScannerResult(
            datetime.now(), [root], scanned_path_pair_ids={path_pair_id},
            is_progress=True, completed_path_pair_ids={path_pair_id}, is_scan_final=True,
            is_full_snapshot=True, full_snapshot_path_pair_ids={path_pair_id},
        )
        controller, _ = self._make_progressive_update_controller(
            final, local_scan=final, model_builder=builder, model=live_model,
        )
        controller._Controller__path_pairs_by_id = {path_pair_id: MagicMock()}
        controller._reserve_move_attempt = MagicMock(return_value=False)
        persist = controller._Controller__persist
        persist.downloaded_file_names = {absent_child_id}
        persist.downloaded_timestamps = {absent_child_id: 1.0}
        persist.final_move_succeeded_file_names = {absent_child_id}

        ModelUpdater(controller).update()

        self.assertEqual(set(), persist.downloaded_file_names)
        self.assertEqual({}, persist.downloaded_timestamps)
        self.assertEqual(set(), persist.final_move_succeeded_file_names)

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
        authority_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "scan_authority"
        ]
        self.assertEqual(1, len(authority_calls))
        self.assertEqual("adopt", authority_calls[0].kwargs["details"]["outcome"])
        self.assertEqual(
            "source_buckets_adopted_after_pair_fallback",
            authority_calls[0].kwargs["details"]["reason"],
        )
        self.assertTrue(authority_calls[0].kwargs["details"]["pair_delta_fallback"])

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

    def test_pair_candidate_does_not_move_unreconciled_unselected_pending_completion(self):
        old = SystemFile("old.bin", 10, False)
        old.path_pair_id = "pair-a"
        pending_remote = SystemFile("pending.bin", 10, False)
        pending_remote.path_pair_id = "pair-b"
        pending_local = SystemFile("pending.bin", 10, False, is_staging=True)
        pending_local.path_pair_id = "pair-b"
        builder = ModelBuilder()
        builder.set_local_files([old, pending_local])
        builder.set_remote_files([old, pending_remote])
        builder.set_downloaded_files(set())
        builder.set_downloaded_timestamps({})
        builder.set_extracted_files(set())
        builder.set_stopped_files(set())
        builder.set_move_failed_files(set())
        builder.set_final_move_succeeded_files(set())
        builder.set_unknown_local_path_pair_ids({"pair-b"})
        live_model = builder.build_model()
        pending_id = ModelFile.build_file_id("pending.bin", "pair-b")
        self.assertTrue(builder.has_complete_local_coverage(pending_id))
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
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        controller._Controller__pending_completion_file_names = {("pending.bin", "pair-b", None)}
        controller._Controller__context.performance_diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        controller.MoveFromStagingResult = Controller.MoveFromStagingResult
        controller._reserve_move_attempt = MagicMock(return_value=True)
        controller._release_move_attempt = MagicMock()
        controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )
        controller._record_download_completion = MagicMock()
        controller._complete_download_start_lifecycle = MagicMock()
        controller.clear_extracted_marker = MagicMock()
        controller._mark_successful_final_move_handoff = MagicMock()
        controller._mark_current_process_final_publication = MagicMock()
        controller._Controller__target_archive_trace_selector_matches_file = MagicMock(return_value=False)

        ModelUpdater(controller).update()

        self.assertIn(ModelFile.build_file_id("new.bin", "pair-a"), live_model.get_file_ids())
        self.assertIn(("pending.bin", "pair-b", None), controller._Controller__pending_completion_file_names)
        builder.build_model.assert_not_called()
        # ``has_complete_local_coverage`` deliberately ignores the unknown
        # bucket; a candidate for pair-a must therefore not turn pair-b's
        # retained staging tree into a global final move.
        builder.request_rebuild.assert_not_called()
        counters = controller._Controller__context.performance_diagnostics.snapshot()["counters"]
        self.assertEqual(0, counters[COUNTER_UNRELATED_CANDIDATE_LIFECYCLE_DEFERRED])

        # A later idle/candidate update still cannot use stale pair-b staging
        # evidence until both scan sides reconcile that exact Path Pair.
        ModelUpdater(controller).update()
        controller._Controller__move_from_staging.assert_not_called()
        self.assertIn(("pending.bin", "pair-b", None), controller._Controller__pending_completion_file_names)
        self.assertNotIn(pending_id, controller._Controller__persist.downloaded_file_names)

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
        base_mtime_ns = 1_786_400_003_000_000_000

        def release(pair_id: str, leaf: str, mtime_offset: int = 0) -> SystemFile:
            value = SystemFile("shared", 100, True)
            value.path_pair_id = pair_id
            value.add_child(SystemFile(leaf, 100, False, mtime_ns=base_mtime_ns + mtime_offset))
            return value

        old = release("pair-a", "stale.mkv")
        old_remote = release("pair-a", "stale.mkv", 100)
        untouched = release("pair-b", "other.mkv")
        untouched_remote = release("pair-b", "other.mkv", 100)
        builder = ModelBuilder()
        builder.set_local_files([old, untouched])
        builder.set_remote_files([old_remote, untouched_remote])
        builder.set_unknown_local_path_pair_ids({"pair-a"})
        live_model = builder.build_model()
        builder.build_model = MagicMock(wraps=builder.build_model)
        replacement_local = release("pair-a", "keep.mkv")
        replacement_remote = release("pair-a", "keep.mkv", 100)
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
            {ModelFile.build_file_id("shared", "pair-a"), ModelFile.build_file_id("shared", "pair-b")},
            live_model.get_file_ids(),
        )
        self.assertEqual(
            {ModelFile.build_file_id("shared", "pair-b")},
            set(builder._ModelBuilder__remote_files_by_pair["pair-b"]),
        )
        self.assertEqual(
            {ModelFile.build_file_id("shared", "pair-a")},
            set(builder._ModelBuilder__remote_files_by_pair["pair-a"]),
        )
        a_id = ModelFile.build_file_id("shared", "pair-a")
        # The fallback adopts A only; the reconciler's exact result clears A
        # while retaining unrelated B, which this targeted scan did not cover.
        self.assertEqual(frozenset({"pair-b"}), builder.unknown_local_path_pair_ids_snapshot())
        self.assertEqual(("keep.mkv",), builder.get_trusted_final_leaf_paths(a_id))
        self.assertEqual(
            [ExactPathExclusion("keep.mkv")],
            Controller._Controller__transfer_exclude_patterns(controller, a_id, True),
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
        # The declared-capability fallback adopts whole source buckets, so it
        # also takes the exact-overlay publication path rather than retaining
        # an obsolete union from an earlier partial wave.
        builder.set_unknown_local_path_pair_ids.assert_called_with(set())
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
        model_builder.local_library_inventory_revision.return_value = 0
        model_builder.unknown_local_path_pair_ids_snapshot.side_effect = [
            frozenset(), frozenset({None}),
        ]
        controller.notify_model_summary_changed = MagicMock()
        updater = ModelUpdater(controller)

        updater.update()
        updater.update()

        model_builder.build_model.assert_not_called()
        model_builder.set_local_files.assert_not_called()
        model_builder.set_remote_files.assert_not_called()
        # The end-of-update publication keeps the unchanged incomplete
        # overlay current on each event; ModelBuilder's public setter is
        # idempotent and does not invalidate the real builder when unchanged.
        self.assertEqual([
            call({None}), call({None}),
        ], model_builder.set_unknown_local_path_pair_ids.call_args_list)
        controller.notify_model_summary_changed.assert_called_once_with()
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
        model_builder.set_unknown_local_path_pair_ids.assert_called_once_with({None})
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

    def test_lftp_status_breadcrumb_category_gate_precedes_status_inspection(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp": "off"}},
        )
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace),
            _Controller__record_breadcrumb=MagicMock(),
            _lftp_status_authority_context=MagicMock(
                side_effect=AssertionError("disabled trace must not inspect authority state")
            ),
            logger=MagicMock(),
        )

        class ExplodingStatuses:
            def __len__(self):
                raise AssertionError("disabled trace must not inspect status objects")

        _record_lftp_status_breadcrumb(
            controller,
            ExplodingStatuses(),
            source="fresh_healthy",
            fresh=True,
            healthy=True,
            poll_due=True,
        )

        controller._Controller__record_breadcrumb.assert_not_called()
        controller._lftp_status_authority_context.assert_not_called()
        self.assertEqual([], trace.snapshot()["entries"])

    def test_lftp_status_authority_context_distinguishes_pending_done_and_idle(self):
        controller = Controller.__new__(Controller)
        controller._Controller__work_state_lock = RLock()
        pending_status_future = Future()
        pending_queue_future = Future()
        done_queue_future = Future()
        done_queue_future.set_result(True)
        controller._Controller__lftp_status_future = pending_status_future
        controller._Controller__pending_queue_dispatches = {
            "private-file-id": PendingQueueDispatch(0.0, "private-name"),
        }
        controller._Controller__lftp_operations = [
            _LftpOperation("queue", pending_queue_future),
            _LftpOperation("queue", done_queue_future),
            _LftpOperation("stop", pending_queue_future),
        ]

        pending_context = controller._lftp_status_authority_context(True)
        self.assertEqual(
            {
                "poll_due": True,
                "status_future_state": "pending",
                "queue_dispatch_pending_count": 1,
                "lftp_queue_operation_pending_count": 1,
                "lftp_queue_operation_done_count": 1,
            },
            pending_context,
        )

        pending_status_future.set_result(([], True))
        done_context = controller._lftp_status_authority_context(True)
        self.assertEqual("done", done_context["status_future_state"])

        controller._Controller__lftp_status_future = None
        controller._Controller__pending_queue_dispatches = {}
        controller._Controller__lftp_operations = []
        idle_context = controller._lftp_status_authority_context(False)
        self.assertEqual(
            {
                "poll_due": False,
                "status_future_state": "none",
                "queue_dispatch_pending_count": 0,
                "lftp_queue_operation_pending_count": 0,
                "lftp_queue_operation_done_count": 0,
            },
            idle_context,
        )

    def test_lftp_status_authority_context_bounds_counts_and_omits_identity(self):
        controller = Controller.__new__(Controller)
        controller._Controller__work_state_lock = RLock()
        controller._Controller__lftp_status_future = None
        controller._Controller__pending_queue_dispatches = {
            "private-file-{}".format(index): PendingQueueDispatch(0.0, "private-name-{}".format(index))
            for index in range(40)
        }
        pending_futures = [Future() for _ in range(40)]
        done_futures = [Future() for _ in range(40)]
        for future in done_futures:
            future.set_result(True)
        controller._Controller__lftp_operations = [
            *[_LftpOperation("queue", future) for future in pending_futures],
            *[_LftpOperation("queue", future) for future in done_futures],
        ]

        context = controller._lftp_status_authority_context(True)
        self.assertEqual(32, context["queue_dispatch_pending_count"])
        self.assertEqual(32, context["lftp_queue_operation_pending_count"])
        self.assertEqual(32, context["lftp_queue_operation_done_count"])
        self.assertNotIn("private-file", str(context))
        self.assertNotIn("private-name", str(context))

    def test_lftp_status_breadcrumb_includes_controller_authority_context(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp": "debug"}},
        )
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace),
            _Controller__record_breadcrumb=lambda **kwargs: trace.record(
                "model_updater", kwargs["message"], kwargs["details"],
                **{key: value for key, value in kwargs.items() if key not in {"message", "details"}},
            ),
            _lftp_status_authority_context=lambda poll_due: {
                "poll_due": poll_due,
                "status_future_state": "done",
                "queue_dispatch_pending_count": 1,
                "lftp_queue_operation_pending_count": 2,
                "lftp_queue_operation_done_count": 3,
            },
            _Controller__lftp=SimpleNamespace(backend_name="lftp"),
            _Controller__last_lftp_statuses=[],
            _Controller__lftp_status_cache_expires_at=None,
            _Controller__lftp_status_poll_retry_active=False,
            _Controller__lftp_idle_status_authoritative=True,
            logger=MagicMock(),
        )

        _record_lftp_status_breadcrumb(
            controller,
            [],
            source="cached_idle",
            fresh=False,
            healthy=True,
            poll_due=False,
        )

        details = trace.snapshot()["entries"][0]["details"]
        self.assertEqual(False, details["poll_due"])
        self.assertEqual("done", details["status_future_state"])
        self.assertEqual(1, details["queue_dispatch_pending_count"])
        self.assertEqual(2, details["lftp_queue_operation_pending_count"])
        self.assertEqual(3, details["lftp_queue_operation_done_count"])

    def test_lftp_status_breadcrumb_cached_retry_uses_warning_policy_level(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"transfer.lftp": "warning"}},
        )

        controller, _ = self._make_progressive_update_controller(None, local_scan=None)
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__record_breadcrumb = lambda **kwargs: trace.record(
            "model_updater", kwargs["message"], kwargs["details"],
            **{key: value for key, value in kwargs.items() if key not in {"message", "details"}},
        )
        controller._Controller__lftp.backend_name = "lftp"
        controller._Controller__lftp.last_status_poll_healthy = False
        controller._Controller__last_lftp_statuses = [LftpJobStatus(
            1,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            "private-fixture-name",
            "",
        )]
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
        controller._Controller__lftp_status_poll_retry_active = True

        ModelUpdater(controller).update()

        entries = trace.snapshot()["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual("cached_retry", entries[0]["details"]["outcome"])
        self.assertEqual("warning", entries[0]["level"])
        details = entries[0]["details"]
        self.assertEqual("cached_retry", details["source"])
        self.assertFalse(details["fresh"])
        self.assertFalse(details["healthy"])
        self.assertTrue(details["retry_active"])

    def test_lftp_status_breadcrumb_omits_rclone_backend(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "trace"},
        )
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace),
            _Controller__record_breadcrumb=MagicMock(),
            _Controller__lftp=SimpleNamespace(backend_name="rclone"),
            logger=MagicMock(),
        )

        _record_lftp_status_breadcrumb(
            controller,
            [],
            source="fresh_healthy",
            fresh=True,
            healthy=True,
        )

        controller._Controller__record_breadcrumb.assert_not_called()
        self.assertEqual([], trace.snapshot()["entries"])

    def test_root_progress_status_trace_covers_rclone_without_legacy_lftp_breadcrumb(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"model.progress": "debug", "transfer.lftp": "off"}},
        )
        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace),
            _Controller__record_breadcrumb=MagicMock(),
            _Controller__lftp=SimpleNamespace(backend_name="rclone"),
            logger=MagicMock(),
        )
        status = LftpJobStatus(
            1,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            "private-fixture-name",
            "/private/fixture/path",
        )

        _record_lftp_status_breadcrumb(
            controller,
            [status],
            source="fresh_healthy",
            fresh=True,
            healthy=True,
        )

        controller._Controller__record_breadcrumb.assert_not_called()
        entries = trace.snapshot(category="model.progress")["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual("status", entries[0]["stage"].replace("root_progress_", ""))
        self.assertNotIn("private-fixture-name", str(entries))
        self.assertNotIn("/private/fixture/path", str(entries))

    def test_lftp_status_breadcrumb_records_gated_outcomes_without_identity(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=16,
            policy={"default": "off", "rules": {"transfer.lftp": "debug"}},
        )

        def record_breadcrumb(**kwargs):
            payload = dict(kwargs)
            message = payload.pop("message")
            details = payload.pop("details")
            trace.record("model_updater", message, details, **payload)

        controller = SimpleNamespace(
            _Controller__context=SimpleNamespace(breadcrumb_trace=trace),
            _Controller__record_breadcrumb=record_breadcrumb,
            _Controller__last_lftp_statuses=[],
            _Controller__lftp_status_cache_expires_at=None,
            _Controller__lftp_status_poll_retry_active=False,
            _Controller__lftp_idle_status_authoritative=False,
            logger=MagicMock(),
        )
        private_status = LftpJobStatus(
            1,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            "private-fixture-name",
            "/private/fixture/path",
        )

        for source, fresh, healthy, statuses, reason in (
            ("fresh_healthy", True, True, [private_status], None),
            ("fresh_healthy", True, True, [], None),
            ("inflight_empty", False, False, [], None),
            ("cached_unhealthy", False, False, [private_status], "timeout"),
            ("error_empty", True, False, [], "parser_error"),
        ):
            _record_lftp_status_breadcrumb(
                controller,
                statuses,
                source=source,
                fresh=fresh,
                healthy=healthy,
                failure_reason=reason,
            )

        entries = trace.snapshot()["entries"]
        self.assertEqual(5, len(entries))
        self.assertEqual(
            ["fresh_healthy", "fresh_healthy_empty", "inflight", "cached_unhealthy", "error_empty"],
            [entry["details"]["outcome"] for entry in entries],
        )
        self.assertEqual(
            ["debug", "debug", "debug", "warning", "warning"],
            [entry["level"] for entry in entries],
        )
        self.assertEqual([1, 0, 0, 1, 0], [entry["details"]["status_count"] for entry in entries])
        self.assertEqual([1, 0, 0, 1, 0], [entry["details"]["active_count"] for entry in entries])
        self.assertEqual("timeout", entries[3]["details"]["failure_reason"])
        self.assertEqual("parser_error", entries[4]["details"]["failure_reason"])
        self.assertTrue(all(entry["category"] == "transfer.lftp" for entry in entries))
        self.assertTrue(all(entry["corr_id"].startswith("lftp:") for entry in entries))
        serialized = str(entries)
        self.assertNotIn("private-fixture-name", serialized)
        self.assertNotIn("/private/fixture/path", serialized)

    def test_lftp_poll_lineage_links_status_inlet_to_completion_decision(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=16,
            policy={
                "default": "off",
                "rules": {"transfer.lftp": "debug", "completion.gate": "info"},
            },
        )
        active_entry = ("sample-transfer.bin", None, None)
        controller = self._make_lftp_completion_controller({active_entry})
        controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)
        controller._Controller__record_breadcrumb = lambda **kwargs: trace.record(
            "model_updater", kwargs["message"], kwargs["details"],
            **{key: value for key, value in kwargs.items() if key not in {"message", "details"}},
        )
        poll_correlation = "lftp-poll:0123456789abcdef"
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING,
            "private-transfer-name", "/private/transfer/output",
        )

        _record_lftp_status_breadcrumb(
            controller,
            [status],
            source="fresh_healthy",
            fresh=True,
            healthy=True,
            poll_correlation=poll_correlation,
            raw_status_count=1,
        )
        ModelUpdater(controller)._handle_lftp_completion_detection(
            [],
            True,
            lftp_status_poll_authoritative=True,
            lftp_status_snapshot_fresh=True,
            lftp_status_poll_healthy=True,
            lftp_status_source="fresh_healthy",
            lftp_status_poll_correlation=poll_correlation,
        )

        linked = trace.snapshot(flow_id=poll_correlation)["entries"]
        self.assertEqual(
            {"lftp_status_poll", "completion_pending_registered"},
            {entry["message"] for entry in linked},
        )
        self.assertTrue(all(entry["flow_id"] == poll_correlation for entry in linked))
        status_entry = next(entry for entry in linked if entry["message"] == "lftp_status_poll")
        self.assertEqual("status_inlet", status_entry["details"]["phase"])
        self.assertEqual(1, status_entry["details"]["raw_status_count"])
        self.assertEqual(1, status_entry["details"]["filtered_status_count"])
        self.assertEqual(poll_correlation, status_entry["details"]["poll_correlation"])
        completion_entry = next(
            entry for entry in linked if entry["message"] == "completion_pending_registered"
        )
        self.assertEqual(poll_correlation, completion_entry["details"]["poll_correlation"])
        serialized = str(linked)
        self.assertNotIn("private-transfer-name", serialized)
        self.assertNotIn("/private/transfer/output", serialized)

    def test_update_links_running_100_percent_to_fresh_empty_completion(self):
        """The real updater intake keeps one poll lineage through retirement."""
        controller = self._make_v092_pending_completion_controller(
            complete=False, sidecar_ready=True,
        )
        running = LftpJobStatus(
            7, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING,
            "private-transfer-name", "/private/transfer/output",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(10, 10, 100, 100, 0)
        controller._Controller__lftp.backend_name = "lftp"
        controller._Controller__lftp.status.side_effect = [[running], []]
        # Use the production snapshot/accessor seam with the fixture's
        # synchronous backend shape; only the PTY executor is bypassed.
        controller._Controller__uses_async_lftp_owner = MagicMock(return_value=False)
        controller._Controller__lftp_status_lineage_enabled = MagicMock(return_value=True)
        controller._Controller__begin_lftp_status_poll_lineage = (
            Controller._Controller__begin_lftp_status_poll_lineage.__get__(controller, Controller)
        )
        controller._get_lftp_status_snapshot = (
            Controller._get_lftp_status_snapshot.__get__(controller, Controller)
        )
        controller._take_lftp_status_poll_correlation = (
            Controller._take_lftp_status_poll_correlation.__get__(controller, Controller)
        )

        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=32,
            policy={
                "default": "off",
                "rules": {"transfer.lftp": "debug", "completion.gate": "info"},
            },
        )
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__record_breadcrumb = lambda **kwargs: trace.record(
            "model_updater", kwargs["message"], kwargs["details"],
            **{key: value for key, value in kwargs.items() if key not in {"message", "details"}},
        )

        updater = ModelUpdater(controller)
        updater.update()
        first_status = next(
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "lftp_status_poll"
        )
        first_correlation = first_status["details"]["poll_correlation"]
        self.assertEqual("fresh_healthy", first_status["details"]["source"])
        self.assertEqual(1, first_status["details"]["active_count"])

        # The active cadence is deliberately forced due to make the second
        # update a fresh PTY poll rather than a cached tick.
        controller._Controller__next_lftp_status_poll_at = None
        controller._Controller__lftp_idle_status_authoritative = False
        updater.update()

        status_events = [
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "lftp_status_poll"
        ]
        self.assertEqual(2, len(status_events))
        second_correlation = status_events[-1]["details"]["poll_correlation"]
        self.assertNotEqual(first_correlation, second_correlation)
        self.assertEqual("fresh_healthy_empty", status_events[-1]["details"]["outcome"])
        self.assertEqual(0, status_events[-1]["details"]["filtered_status_count"])

        linked = trace.snapshot(flow_id=second_correlation)["entries"]
        linked_messages = {entry["message"] for entry in linked}
        self.assertIn("lftp_status_poll", linked_messages)
        self.assertIn("completion_pending_registered", linked_messages)
        self.assertTrue(all(
            entry["details"].get("poll_correlation") == second_correlation
            for entry in linked
            if entry["message"] != "lftp_status_poll"
        ))
        # The token is consumed by the fresh-snapshot intake and cannot leak
        # into a later cached/no-poll updater tick.
        self.assertIsNone(controller._take_lftp_status_poll_correlation())
        before_cached = len(trace.snapshot()["entries"])
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
        controller._Controller__lftp_idle_status_authoritative = True
        updater.update()
        cached_entries = trace.snapshot()["entries"][before_cached:]
        cached_status = next(
            entry for entry in cached_entries if entry["message"] == "lftp_status_poll"
        )
        self.assertEqual("cached_idle", cached_status["details"]["source"])
        self.assertNotIn("poll_correlation", cached_status["details"])
        self.assertIsNone(cached_status["flow_id"])
        self.assertTrue(all(entry["flow_id"] is None for entry in cached_entries))
        serialized = str(linked)
        self.assertNotIn("private-transfer-name", serialized)
        self.assertNotIn("/private/transfer/output", serialized)

    def test_lftp_poll_lineage_reader_is_skipped_when_diagnostics_are_disabled(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={
                "default": "off",
                "rules": {
                    "transfer.lftp": "off",
                    "completion.gate": "off",
                },
            },
        )
        controller, _ = self._make_progressive_update_controller(None, local_scan=None)
        controller._Controller__context.breadcrumb_trace = trace
        controller._take_lftp_status_poll_correlation = MagicMock(
            side_effect=AssertionError("disabled diagnostics must not read poll lineage")
        )

        ModelUpdater(controller).update()

        controller._take_lftp_status_poll_correlation.assert_not_called()
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

    def test_direct_root_counter_publish_lineage_records_accepted_and_rejected_outcomes(self):
        def run_publish(*, change_lifecycle_epoch):
            builder = ModelBuilder()
            builder.set_remote_files([SystemFile("root", 100, False)])
            initial = LftpJobStatus(
                1, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "root", "",
            )
            initial.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
            builder.set_lftp_statuses([initial])
            builder.set_active_files([SystemFile("root", 25, False)])
            live_model = builder.build_model()
            controller, _ = self._make_progressive_update_controller(
                None, local_scan=None, model_builder=builder, model=live_model,
            )
            trace = BreadcrumbTraceCollector(
                lambda: True,
                policy={"default": "off", "rules": {"model.progress": "debug"}},
            )
            controller._Controller__context.breadcrumb_trace = trace
            controller._Controller__progress_publication_epoch = 1
            controller._take_lftp_status_poll_correlation = MagicMock(
                return_value="lftp-poll:0123456789abcdef",
            )
            status = LftpJobStatus(
                1, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "root", "",
            )
            status.total_transfer_state = LftpJobStatus.TransferState(26, 100, 26, 11, 7)
            controller._Controller__lftp.status.return_value = [status]
            controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
                datetime.now(), [SystemFile("root", 26, False, time_modified=datetime.now())],
            )
            if change_lifecycle_epoch:
                original_publish = live_model.publish_active_lftp_root_counters

                def publish_and_change_epoch(*args, **kwargs):
                    controller._Controller__progress_publication_epoch = 2
                    return original_publish(*args, **kwargs)

                live_model.publish_active_lftp_root_counters = publish_and_change_epoch

            ModelUpdater(controller).update()
            span = trace.snapshot()["progress_lineage"]["spans"][0]
            direct = next(
                step for step in span["steps"]
                if step["phase"] == "direct_root_counter_publish"
            )
            return direct

        accepted = run_publish(change_lifecycle_epoch=False)
        rejected = run_publish(change_lifecycle_epoch=True)
        self.assertEqual("accepted", accepted["details"]["outcome"])
        self.assertEqual("lifecycle_epoch", rejected["details"]["outcome"])
        for step in (accepted, rejected):
            self.assertIn(step["details"]["lock_wait_duration_bucket"], {
                "0-4", "5-19", "20-99", "100-499", "500-1999", "2000+",
            })
            self.assertIn(step["details"]["publish_duration_bucket"], {
                "0-4", "5-19", "20-99", "100-499", "500-1999", "2000+",
            })
            self.assertNotIn("root", str(step["details"]))

    def test_direct_root_counter_rejected_status_snapshots_leave_builder_dirty(self):
        for snapshot in ([], [
                LftpJobStatus(1, LftpJobStatus.Type.GET, LftpJobStatus.State.QUEUED, "root", ""),
        ], [
                LftpJobStatus(1, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "root", ""),
                LftpJobStatus(2, LftpJobStatus.Type.GET, LftpJobStatus.State.QUEUED, "other", ""),
        ]):
            with self.subTest(status_count=len(snapshot)):
                builder = ModelBuilder()
                builder.set_remote_files([SystemFile("root", 100, False)])
                initial = LftpJobStatus(1, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "root", "")
                initial.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
                builder.set_lftp_statuses([initial])
                builder.set_active_files([SystemFile("root", 25, False)])
                model = builder.build_model()
                controller, _ = self._make_progressive_update_controller(
                    None, local_scan=None, model_builder=builder, model=model,
                )
                original_adopt = builder.adopt_active_progress_overlays
                builder.adopt_active_progress_overlays = MagicMock(wraps=original_adopt)
                for status in snapshot:
                    if status.state == LftpJobStatus.State.RUNNING:
                        status.total_transfer_state = LftpJobStatus.TransferState(26, 100, 26, 11, 7)
                controller._Controller__lftp.status.return_value = snapshot

                ModelUpdater(controller).update()

                builder.adopt_active_progress_overlays.assert_not_called()

    def test_same_tick_active_scan_progress_uses_projection_without_tree_build(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        initial = LftpJobStatus(1, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "root", "")
        initial.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        builder.set_lftp_statuses([initial])
        builder.set_active_files([SystemFile("root", 25, False)])
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        active_scan = ScannerResult(
            datetime.now(), [SystemFile("root", 26, False, time_modified=datetime.now())],
        )
        controller._Controller__active_scan_process.pop_latest_result.return_value = active_scan
        progressed = LftpJobStatus(1, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "root", "")
        progressed.total_transfer_state = LftpJobStatus.TransferState(26, 100, 26, 11, 7)
        controller._Controller__lftp.status.return_value = [progressed]
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        self.assertEqual(26, live_model.active_progress_overlay("root").transferred_size)
        self.assertEqual(25, live_model.get_file("root").transferred_size)
        self.assertTrue(builder.has_changes())
        builder.build_model.assert_not_called()

    def test_deep_active_scan_counter_mtime_churn_keeps_direct_publish_and_defers_tree_build(self):
        def active_tree(
                leaf_size: int, leaf_mtime_ns: int, *, extra_child: bool = False,
        ) -> SystemFile:
            root = SystemFile("root", 20 + leaf_size, True, mtime_ns=100)
            nested = SystemFile("nested", 20 + leaf_size, True, mtime_ns=200)
            nested.add_child(SystemFile("payload.bin", leaf_size, False, mtime_ns=leaf_mtime_ns))
            if extra_child:
                nested.add_child(SystemFile("new.bin", 1, False, mtime_ns=300))
            root.add_child(nested)
            return root

        builder = ModelBuilder()
        builder.set_remote_files([active_tree(100, 1)])
        initial = LftpJobStatus(
            1, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "root", "",
        )
        initial.total_transfer_state = LftpJobStatus.TransferState(20, 100, 20, 10, 8)
        builder.set_lftp_statuses([initial])
        builder.set_active_files([active_tree(10, 1)])
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        progressed = LftpJobStatus(
            1, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "root", "",
        )
        progressed.total_transfer_state = LftpJobStatus.TransferState(21, 100, 21, 11, 7)
        next_progressed = LftpJobStatus(
            1, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "root", "",
        )
        next_progressed.total_transfer_state = LftpJobStatus.TransferState(22, 100, 22, 12, 6)
        terminal_progressed = LftpJobStatus(
            1, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "root", "",
        )
        terminal_progressed.total_transfer_state = LftpJobStatus.TransferState(23, 100, 23, 13, 5)
        controller._Controller__active_scan_process.pop_latest_result.side_effect = [
            ScannerResult(datetime.now(), [active_tree(11, 2)]),
            ScannerResult(datetime.now(), [active_tree(12, 3)]),
            ScannerResult(datetime.now(), [active_tree(13, 4, extra_child=True)]),
        ]
        controller._Controller__lftp.status.side_effect = [
            [progressed], [next_progressed], [terminal_progressed],
        ]
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        self.assertEqual(21, live_model.active_progress_overlay("root").transferred_size)
        self.assertEqual(20, live_model.get_file("root").transferred_size)
        self.assertTrue(builder.has_changes())
        builder.build_model.assert_not_called()

        controller._Controller__next_lftp_status_poll_at = None
        ModelUpdater(controller).update()

        self.assertEqual(22, live_model.active_progress_overlay("root").transferred_size)
        self.assertTrue(builder.has_changes())
        builder.build_model.assert_not_called()

        controller._Controller__next_lftp_status_poll_at = None
        ModelUpdater(controller).update()

        self.assertIsNone(live_model.active_progress_overlay("root"))
        self.assertEqual(23, live_model.get_file("root").transferred_size)
        self.assertFalse(builder.has_changes())
        builder.build_model.assert_not_called()
        self.assertEqual(
            {"payload.bin", "new.bin"},
            {child.name for child in live_model.get_file("root").get_children()[0].get_children()},
        )

    def test_timestamp_overlay_reconciliation_recovers_active_lftp_delta(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        live_model = builder.build_model()
        builder.set_downloaded_timestamps({"root": 1760000010.0})
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        initial_status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        initial_status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [initial_status]
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)
        updater = ModelUpdater(controller)

        updater.update()

        builder.build_model.assert_called_once_with()
        self.assertEqual(1, live_model.downloaded_timestamp_overlay_generation)
        self.assertEqual(25, live_model.get_file("root").transferred_size)

        next_status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        next_status.total_transfer_state = LftpJobStatus.TransferState(50, 100, 50, 10, 4)
        controller._Controller__lftp.status.return_value = [next_status]
        controller._Controller__next_lftp_status_poll_at = None

        updater.update()

        builder.build_model.assert_called_once_with()
        self.assertEqual(50, live_model.get_file("root").transferred_size)

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

    def test_v092_lftp_finish_exact_staged_directory_without_model_diff_finalizes(self):
        """Reproduce the v0.9.2 no-diff completion handoff.

        A restarted controller has only the remote inventory and an incomplete
        staged directory.  Manual Queue then resumes that directory.  By the
        time the LFTP job disappears, the staged tree can already be byte exact
        and the model is therefore still Downloaded before/after status loss.
        The completion owner must move/publish that row even though no model
        diff is emitted by the status disappearance.
        """
        remote_root = SystemFile("generic", 30, True)
        remote_root.add_child(SystemFile("complete.bin", 10, False, mtime_ns=1))
        remote_root.add_child(SystemFile("partial.bin", 10, False, mtime_ns=2))
        remote_root.add_child(SystemFile("missing.bin", 10, False, mtime_ns=3))

        restarted_staging = SystemFile("generic", 15, True)
        restarted_staging.add_child(SystemFile("complete.bin", 10, False, mtime_ns=1))
        restarted_staging.add_child(SystemFile("partial.bin", 5, False, is_staging=True))

        builder = ModelBuilder()
        builder.set_remote_files([remote_root])
        builder.set_local_files([restarted_staging])
        restarted_model = builder.build_model()
        restarted_file = restarted_model.get_file("generic")
        self.assertEqual(ModelFile.State.DEFAULT, restarted_file.state)
        self.assertEqual(15, restarted_file.local_size)

        # Manual Queue resumes the valid partial and missing leaves.  The
        # final LFTP snapshot has no sidecar files and the staged tree is exact
        # before the status disappears, so both model builds are Downloaded.
        exact_staging = SystemFile("generic", 30, True)
        exact_staging.add_child(SystemFile("complete.bin", 10, False, mtime_ns=1))
        exact_staging.add_child(SystemFile("partial.bin", 10, False, is_staging=True, mtime_ns=2))
        exact_staging.add_child(SystemFile("missing.bin", 10, False, is_staging=True, mtime_ns=3))
        running = LftpJobStatus(
            1, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "generic", "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(30, 30, 100, 100, 0)
        builder.set_local_files([exact_staging])
        builder.set_lftp_statuses([running])
        live_model = builder.build_model()
        live_file = live_model.get_file("generic")
        self.assertEqual(ModelFile.State.DOWNLOADED, live_file.state)
        self.assertEqual(30, live_file.transferred_size)
        self.assertTrue(builder.has_complete_local_coverage("generic"))

        # These final scans establish both sides of the legacy scope.  This
        # is deliberately not inferred from exact staging bytes alone.
        final_local = ScannerResult(
            datetime.now(), [exact_staging], scanned_path_pair_ids={None},
            is_scan_final=True,
        )
        final_remote = ScannerResult(
            datetime.now(), [remote_root], scanned_path_pair_ids={None},
            is_scan_final=True,
        )
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        controller.MoveFromStagingResult = Controller.MoveFromStagingResult
        controller._Controller__prev_downloading_file_names = {("generic", None, None)}
        controller._Controller__lftp.status.return_value = []
        controller._reserve_move_attempt = MagicMock(return_value=True)
        controller._release_move_attempt = MagicMock()
        controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )
        controller._record_download_completion = MagicMock()
        controller._complete_download_start_lifecycle = MagicMock()
        controller.clear_extracted_marker = MagicMock()
        controller._mark_successful_final_move_handoff = MagicMock()
        controller._mark_current_process_final_publication = MagicMock()
        controller._Controller__target_archive_trace_selector_matches_file = MagicMock(return_value=False)

        # Exact local coverage can keep the rendered state Downloaded before
        # and after status retirement. The production failure is precisely
        # the quiet ModelDiff path, so exercise it explicitly here.
        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]) as diff_models:
            ModelUpdater(controller).update()

        diff_models.assert_called_once()

        controller._Controller__move_from_staging.assert_called_once_with("generic", None)
        # These are the fields ViewFile consumes: DOWNLOADED plus equal
        # transferred/remote bytes maps to 100%, even though completed model
        # rows deliberately clear the live-only percentage.
        published_file = live_model.get_file("generic")
        self.assertEqual(ModelFile.State.DOWNLOADED, published_file.state)
        self.assertEqual(30, published_file.remote_size)
        self.assertEqual(30, published_file.transferred_size)
        self.assertIsNone(published_file.download_progress)
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        self.assertEqual({"generic"}, controller._Controller__persist.downloaded_file_names)
        self.assertEqual({"generic"}, controller._Controller__persist.final_move_succeeded_file_names)

        # A quiet idle pass after publication must not re-run either the move
        # or completion publication for an identity that is no longer pending.
        ModelUpdater(controller).update()
        controller._Controller__move_from_staging.assert_called_once()

    def test_active_directory_transfer_dispatches_only_verified_staging_child_finalization(self):
        remote_root = SystemFile("release", 20, True)
        remote_root.add_child(SystemFile("complete.bin", 8, False, mtime_ns=1))
        remote_root.add_child(SystemFile("incomplete.bin", 12, False, mtime_ns=2))
        local_root = SystemFile("release", 20, True, is_staging=True)
        local_root.add_child(SystemFile("complete.bin", 8, False, is_staging=True, mtime_ns=1))
        incomplete = SystemFile("incomplete.bin", 12, False, is_staging=True, mtime_ns=2)
        incomplete.status_sidecar_ready = True
        local_root.add_child(incomplete)
        running = LftpJobStatus(1, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "release", "")
        running.total_transfer_state = LftpJobStatus.TransferState(8, 20, 40, 100, 1)
        builder = ModelBuilder()
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_lftp_statuses([running])
        live_model = builder.build_model()
        final_remote = ScannerResult(datetime.now(), [remote_root], scanned_path_pair_ids={None}, is_scan_final=True)
        final_local = ScannerResult(datetime.now(), [local_root], scanned_path_pair_ids={None}, is_scan_final=True)
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__lftp.status.return_value = [running]
        controller._finalize_staging_child = MagicMock(return_value=Controller.MoveFromStagingResult.COMPLETED)

        ModelUpdater(controller).update()

        controller._finalize_staging_child.assert_called_once_with("release", "complete.bin", None)
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)

    def test_queued_directory_mirror_dispatches_verified_completed_child_while_sibling_downloads(self):
        remote_root = SystemFile("release", 20, True)
        remote_root.add_child(SystemFile("complete.bin", 8, False, mtime_ns=1))
        remote_root.add_child(SystemFile("downloading.bin", 12, False, mtime_ns=2))
        local_root = SystemFile("release", 20, True, is_staging=True)
        local_root.add_child(SystemFile("complete.bin", 8, False, is_staging=True, mtime_ns=1))
        downloading = SystemFile("downloading.bin", 12, False, is_staging=True, mtime_ns=2)
        downloading.status_sidecar_ready = True
        local_root.add_child(downloading)
        queued = LftpJobStatus(1, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "release", "")
        builder = ModelBuilder()
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_lftp_statuses([queued])
        live_model = builder.build_model()
        final_remote = ScannerResult(datetime.now(), [remote_root], scanned_path_pair_ids={None}, is_scan_final=True)
        final_local = ScannerResult(datetime.now(), [local_root], scanned_path_pair_ids={None}, is_scan_final=True)
        controller, _ = self._make_progressive_update_controller(
            final_remote, local_scan=final_local, model_builder=builder, model=live_model,
        )
        controller._Controller__lftp.status.return_value = [queued]
        controller._finalize_staging_child = MagicMock(return_value=Controller.MoveFromStagingResult.COMPLETED)

        ModelUpdater(controller).update()

        controller._finalize_staging_child.assert_called_once_with("release", "complete.bin", None)
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)

    def test_child_finalization_breadcrumbs_are_queryable_and_identity_free(self):
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None,
        )
        model_builder.get_finalizable_staging_leaf_candidates.return_value = (
            ('["accepted-private-pair", "private-root"]', "private-complete.bin"),
            ('["rejected-private-pair", "private-root"]', "private-skipped.bin"),
        )
        controller._Controller__reconciled_local_path_pair_ids = {"accepted-private-pair"}
        controller._Controller__reconciled_remote_path_pair_ids = {"accepted-private-pair"}
        controller._finalize_staging_child = MagicMock(
            return_value=Controller.MoveFromStagingResult.FAILED,
        )
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=32)
        controller._Controller__context.breadcrumb_trace = trace

        def record_breadcrumb(**kwargs):
            metadata = {
                "stage": kwargs["stage"],
                "event_type": kwargs["event_type"],
                "category": kwargs["category"],
                "level": kwargs["level"],
                "corr_id": kwargs["corr_id"],
                "flow_id": kwargs.get("flow_id"),
                "trace_scope": kwargs.get("trace_scope", "flow"),
            }
            trace.record(
                "controller", kwargs["message"], kwargs["details"], **metadata,
            )

        controller._Controller__record_breadcrumb = record_breadcrumb
        ModelUpdater(controller).update()

        events = trace.query_events(
            category="finalization.child", stage="finalization_child", limit=16,
        )["events"]
        self.assertEqual(4, len(events))
        self.assertEqual("child_finalization_candidates", events[0]["message"])
        self.assertEqual(
            2,
            sum(event["message"] == "child_finalization_authority" for event in events),
        )
        self.assertEqual(
            ["child_finalization_result"],
            [event["message"] for event in events if event["message"] == "child_finalization_result"],
        )
        candidate = events[0]
        self.assertEqual("aggregate", candidate["trace_scope"])
        self.assertEqual("state_transition", candidate["event_type"])
        self.assertEqual("finalization_child.v1", candidate["details"]["schema"])
        self.assertEqual("candidate_discovery", candidate["details"]["phase"])
        self.assertEqual("available", candidate["details"]["outcome"])
        self.assertEqual(2, candidate["details"]["candidate_count"])
        self.assertEqual(2, candidate["details"]["valid_candidate_count"])
        authority_events = [
            event for event in events if event["message"] == "child_finalization_authority"
        ]
        accepted = next(
            event for event in authority_events
            if event["details"]["decision"] == "accepted"
        )
        rejected = next(
            event for event in authority_events
            if event["details"]["decision"] == "rejected"
        )
        self.assertEqual("accepted", accepted["details"]["decision"])
        self.assertEqual("reconciled_both_sides", accepted["details"]["reason"])
        self.assertEqual("rejected", rejected["details"]["decision"])
        self.assertEqual("local_and_remote_unreconciled", rejected["details"]["reason"])
        result = next(
            event for event in events if event["message"] == "child_finalization_result"
        )
        self.assertEqual("failed", result["details"]["outcome"])
        self.assertEqual("move_failed", result["details"]["reason"])
        self.assertTrue(result["details"]["attempt_failure"])
        self.assertNotIn("terminal_failure", result["details"])
        self.assertEqual("warning", result["level"])
        self.assertEqual("state_transition", accepted["event_type"])
        self.assertEqual("state_transition", result["event_type"])
        self.assertNotEqual(accepted["corr_id"], rejected["corr_id"])
        self.assertEqual("child:aggregate", candidate["corr_id"])
        for event in events:
            self.assertNotIn("private-", str(event))
            self.assertNotIn("accepted-private-pair", str(event))
            self.assertNotIn("rejected-private-pair", str(event))
        controller._finalize_staging_child.assert_called_once_with(
            "private-root", "private-complete.bin", "accepted-private-pair",
        )

    def test_child_finalization_state_transitions_survive_idle_retention(self):
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None,
        )
        model_builder.get_finalizable_staging_leaf_candidates.side_effect = (
            [(('["private-pair", "private-root"]', "private-child.bin"),)]
            + [()] * 80
        )
        controller._Controller__reconciled_local_path_pair_ids = {"private-pair"}
        controller._Controller__reconciled_remote_path_pair_ids = {"private-pair"}
        controller._finalize_staging_child = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=32)
        controller._Controller__context.breadcrumb_trace = trace

        def record_breadcrumb(**kwargs):
            trace.record(
                "controller",
                kwargs["message"],
                kwargs["details"],
                stage=kwargs["stage"],
                event_type=kwargs["event_type"],
                category=kwargs["category"],
                level=kwargs["level"],
                corr_id=kwargs["corr_id"],
                flow_id=kwargs.get("flow_id"),
                trace_scope=kwargs.get("trace_scope", "flow"),
            )

        controller._Controller__record_breadcrumb = record_breadcrumb
        updater = ModelUpdater(controller)
        updater.update()
        for _ in range(64):
            updater.update()

        payload = trace.query_events(
            category="finalization.child", stage="finalization_child", limit=16,
        )
        events = payload["events"]
        messages = [event["message"] for event in events]
        self.assertIn("child_finalization_authority", messages)
        self.assertIn("child_finalization_result", messages)
        candidate_events = [
            event for event in events
            if event["message"] == "child_finalization_candidates"
        ]
        self.assertEqual(2, len(candidate_events))
        self.assertEqual(
            [1, 0],
            [event["details"]["candidate_count"] for event in candidate_events],
        )
        category_accounting = trace.query_events()["accounting"]["categories"][
            "finalization.child"
        ]
        self.assertEqual(4, category_accounting["admitted"])
        self.assertEqual(0, category_accounting["evicted"])
        for event in events:
            self.assertEqual("state_transition", event["event_type"])
            self.assertNotIn("private-", str(event))
        self.assertEqual(1, controller._finalize_staging_child.call_count)

    def test_child_finalization_plain_empty_candidates_emit_one_transition(self):
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None,
        )
        model_builder.get_finalizable_staging_leaf_candidates.return_value = ()
        controller._finalize_staging_child = MagicMock()
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        controller._Controller__context.breadcrumb_trace = trace

        def record_breadcrumb(**kwargs):
            trace.record(
                "controller",
                kwargs["message"],
                kwargs["details"],
                stage=kwargs["stage"],
                event_type=kwargs["event_type"],
                category=kwargs["category"],
                level=kwargs["level"],
                corr_id=kwargs["corr_id"],
                flow_id=kwargs.get("flow_id"),
                trace_scope=kwargs.get("trace_scope", "flow"),
            )

        controller._Controller__record_breadcrumb = record_breadcrumb
        updater = ModelUpdater(controller)
        updater.update()
        updater.update()

        events = trace.query_events(
            category="finalization.child", stage="finalization_child", limit=8,
        )["events"]
        candidate_events = [
            event for event in events
            if event["message"] == "child_finalization_candidates"
        ]
        self.assertEqual(1, len(candidate_events))
        self.assertEqual(0, candidate_events[0]["details"]["candidate_count"])
        self.assertEqual("state_transition", candidate_events[0]["event_type"])
        self.assertEqual(
            1,
            trace.query_events()["accounting"]["categories"][
                "finalization.child"
            ]["admitted"],
        )
        controller._finalize_staging_child.assert_not_called()

    def test_child_finalization_disabled_category_skips_trace_only_work(self):
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None,
        )
        model_builder.get_finalizable_staging_leaf_candidates.return_value = (
            ('["private-pair", "private-root"]', "private-child.bin"),
        )
        controller._Controller__reconciled_local_path_pair_ids = {"private-pair"}
        controller._Controller__reconciled_remote_path_pair_ids = {"private-pair"}
        controller._finalize_staging_child = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )

        class CategoryGate:
            def is_effectively_enabled(self, category, level="info"):
                return category != "finalization.child"

        controller._Controller__context.breadcrumb_trace = CategoryGate()
        with patch(
            "controller.model_updater.opaque_trace_correlation",
            side_effect=AssertionError("disabled child trace built a correlation"),
        ):
            ModelUpdater(controller).update()

        controller._finalize_staging_child.assert_called_once_with(
            "private-root", "private-child.bin", "private-pair",
        )
        self.assertFalse(any(
            call.kwargs.get("category") == "finalization.child"
            for call in controller._Controller__record_breadcrumb.call_args_list
        ))

    def test_child_finalization_disabled_verbosity_skips_trace_only_work(self):
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None,
        )
        model_builder.get_finalizable_staging_leaf_candidates.return_value = (
            ('["private-pair", "private-root"]', "private-child.bin"),
        )
        controller._Controller__reconciled_local_path_pair_ids = {"private-pair"}
        controller._Controller__reconciled_remote_path_pair_ids = {"private-pair"}
        controller._finalize_staging_child = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )

        class VerbosityGate:
            def is_effectively_enabled(self, category, level="info"):
                return level == "debug"

        controller._Controller__context.breadcrumb_trace = VerbosityGate()
        with patch(
            "controller.model_updater.opaque_trace_correlation",
            side_effect=AssertionError("disabled child trace built a correlation"),
        ):
            ModelUpdater(controller).update()

        controller._finalize_staging_child.assert_called_once_with(
            "private-root", "private-child.bin", "private-pair",
        )
        self.assertFalse(controller._Controller__record_breadcrumb.called)

    def test_child_finalization_warning_only_policy_skips_info_correlations(self):
        controller, model_builder = self._make_progressive_update_controller(
            None, local_scan=None,
        )
        model_builder.get_finalizable_staging_leaf_candidates.return_value = (
            ('["rejected-pair", "private-root"]', "private-rejected.bin"),
            ('["complete-pair", "private-root"]', "private-complete.bin"),
            ('["deferred-pair", "private-root"]', "private-deferred.bin"),
        )
        controller._Controller__reconciled_local_path_pair_ids = {
            "complete-pair", "deferred-pair",
        }
        controller._Controller__reconciled_remote_path_pair_ids = {
            "complete-pair", "deferred-pair",
        }
        controller._finalize_staging_child = MagicMock(
            side_effect=[
                Controller.MoveFromStagingResult.COMPLETED,
                Controller.MoveFromStagingResult.DEFERRED,
            ],
        )

        class WarningOnlyGate:
            def is_effectively_enabled(self, category, level="info"):
                return category == "finalization.child" and level == "warning"

        controller._Controller__context.breadcrumb_trace = WarningOnlyGate()
        with patch(
            "controller.model_updater._child_finalization_trace_identity",
            side_effect=AssertionError("info-only child path built a correlation"),
        ):
            ModelUpdater(controller).update()

        controller._finalize_staging_child.assert_has_calls([
            call("private-root", "private-complete.bin", "complete-pair"),
            call("private-root", "private-deferred.bin", "deferred-pair"),
        ])
        self.assertEqual(2, controller._finalize_staging_child.call_count)
        self.assertFalse(any(
            call.kwargs.get("category") == "finalization.child"
            for call in controller._Controller__record_breadcrumb.call_args_list
        ))

    def test_child_finalization_failed_result_is_attempt_failure_at_retry_boundaries(self):
        for failure_count in (1, 4):
            with self.subTest(failure_count=failure_count):
                controller, model_builder = self._make_progressive_update_controller(
                    None, local_scan=None,
                )
                model_builder.get_finalizable_staging_leaf_candidates.return_value = (
                    ('["private-pair", "private-root"]', "private-child.bin"),
                )
                controller._Controller__reconciled_local_path_pair_ids = {"private-pair"}
                controller._Controller__reconciled_remote_path_pair_ids = {"private-pair"}
                controller._Controller__child_final_move_failure_counts = {
                    ModelFile.build_file_id(
                        "private-root/private-child.bin", "private-pair",
                    ): failure_count,
                }
                controller._finalize_staging_child = MagicMock(
                    return_value=Controller.MoveFromStagingResult.FAILED,
                )
                trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
                controller._Controller__context.breadcrumb_trace = trace

                def record_breadcrumb(**kwargs):
                    trace.record(
                        "controller",
                        kwargs["message"],
                        kwargs["details"],
                        stage=kwargs["stage"],
                        event_type=kwargs["event_type"],
                        category=kwargs["category"],
                        level=kwargs["level"],
                        corr_id=kwargs["corr_id"],
                        flow_id=kwargs.get("flow_id"),
                        trace_scope=kwargs.get("trace_scope", "flow"),
                    )

                controller._Controller__record_breadcrumb = record_breadcrumb
                ModelUpdater(controller).update()

                result = next(event for event in trace.query_events(
                    category="finalization.child", stage="finalization_child", limit=16,
                )["events"] if event["message"] == "child_finalization_result")
                self.assertEqual("failed", result["details"]["outcome"])
                self.assertTrue(result["details"]["attempt_failure"])
                self.assertNotIn("terminal_failure", result["details"])

    def test_v092_pending_completion_waits_for_authoritative_local_coverage(self):
        controller = self._make_v092_pending_completion_controller(complete=False)

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

        file_id = ModelFile.build_file_id("pending.bin", None)
        controller._Controller__move_from_staging.assert_not_called()
        self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)
        self.assertNotIn(file_id, controller._Controller__persist.downloaded_file_names)

    def test_v092_pending_completion_defers_when_local_scan_is_unknown(self):
        controller = self._make_v092_pending_completion_controller()
        controller._Controller__local_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("pending.bin", 10, False, is_staging=True)],
            scanned_path_pair_ids={None}, unknown_path_pair_ids={None},
        )
        controller._Controller__remote_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("pending.bin", 10, False)], scanned_path_pair_ids={None},
        )

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

        self.assertNotIn(None, controller._Controller__reconciled_local_path_pair_ids)
        self.assertIn(None, controller._Controller__reconciled_remote_path_pair_ids)
        controller._Controller__move_from_staging.assert_not_called()
        self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)

    def test_v092_pending_completion_defers_when_remote_scan_is_unknown_or_failed(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                controller = self._make_v092_pending_completion_controller()
                controller._Controller__local_scan_process.pop_latest_result.return_value = ScannerResult(
                    datetime.now(), [SystemFile("pending.bin", 10, False, is_staging=True)],
                    scanned_path_pair_ids={None},
                )
                controller._Controller__remote_scan_process.pop_latest_result.return_value = ScannerResult(
                    datetime.now(), [] if failed else [SystemFile("pending.bin", 10, False)],
                    scanned_path_pair_ids={None}, failed=failed, unknown_path_pair_ids={None},
                )

                with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
                    ModelUpdater(controller).update()

                self.assertIn(None, controller._Controller__reconciled_local_path_pair_ids)
                self.assertNotIn(None, controller._Controller__reconciled_remote_path_pair_ids)
                controller._Controller__move_from_staging.assert_not_called()
                self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)

    def test_v092_pending_path_pair_moves_only_after_both_final_scans_reconcile_it(self):
        path_pair_id = "pair-b"
        controller = self._make_v092_pending_completion_controller(path_pair_id=path_pair_id)
        local = SystemFile("pending.bin", 10, False, is_staging=True, mtime_ns=1)
        local.path_pair_id = path_pair_id
        remote = SystemFile("pending.bin", 10, False, mtime_ns=1)
        remote.path_pair_id = path_pair_id
        controller._Controller__local_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [local], scanned_path_pair_ids={path_pair_id}, is_scan_final=True,
        )
        controller._Controller__remote_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [remote], scanned_path_pair_ids={path_pair_id}, is_scan_final=True,
        )

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

        self.assertEqual({path_pair_id}, controller._Controller__reconciled_local_path_pair_ids)
        self.assertEqual({path_pair_id}, controller._Controller__reconciled_remote_path_pair_ids)
        controller._Controller__move_from_staging.assert_called_once_with("pending.bin", path_pair_id)
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)

    def test_v092_pending_completion_requires_exact_staging_source_identity(self):
        for mtime_ns in (1_000_000_001, None):
            with self.subTest(mtime_ns=mtime_ns):
                controller = self._make_v092_pending_completion_controller()
                controller._Controller__model_builder.set_local_files([
                    SystemFile("pending.bin", 10, False, is_staging=True, mtime_ns=mtime_ns),
                ])

                with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
                    ModelUpdater(controller).update()

                self.assertTrue(
                    controller._Controller__model_builder.has_complete_local_coverage(
                        ModelFile.build_file_id("pending.bin", None),
                    )
                )
                self.assertFalse(
                    controller._Controller__model_builder.has_verified_complete_staging_remote_identity(
                        ModelFile.build_file_id("pending.bin", None),
                    )
                )
                controller._Controller__move_from_staging.assert_not_called()
                self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)

    def test_v092_pending_completion_rejects_full_logical_size_from_pget_sidecar(self):
        controller = self._make_v092_pending_completion_controller()
        sidecar_backed_target = SystemFile(
            "pending.bin", 10, False, is_staging=True, mtime_ns=1,
        )
        # This models a syntactically valid pget map whose declared segments
        # make Scanner publish the remote-sized logical value while the actual
        # target is truncated. Resume metadata is never physical completion.
        sidecar_backed_target.status_sidecar_ready = True
        controller._Controller__model_builder.set_local_files([sidecar_backed_target])

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

        file_id = ModelFile.build_file_id("pending.bin", None)
        self.assertTrue(controller._Controller__model_builder.has_complete_local_coverage(file_id))
        self.assertFalse(
            controller._Controller__model_builder.has_verified_complete_staging_remote_identity(file_id)
        )
        controller._Controller__move_from_staging.assert_not_called()
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)
        self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)

    def test_v092_real_pget_sidecar_parser_keeps_running_completion_pending(self):
        """A parsed resumable map must not turn a 100% PGET row into a move."""
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=32,
            policy={
                "default": "off",
                "rules": {"lftp.sidecar": "info", "transfer.lftp": "debug"},
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "pending.bin")
            with open(target, "wb") as handle:
                handle.write(b"x" * 9)
            with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                handle.write("size=10\n0.pos=9\n0.limit=10\n")

            transient_target = os.path.join(temp_dir, "transient.bin.lftp")
            with open(transient_target, "wb") as handle:
                handle.write(b"x" * 9)
            with open(transient_target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                handle.write("size=10\n0.pos=9\n0.limit=bad\n")

            scanner = SystemScanner(temp_dir)
            scanner.set_lftp_temp_suffix(".lftp")
            scanner.set_scan_role("local")
            scanner.set_breadcrumb_trace(trace)
            parsed_local = scanner.scan_single("pending.bin")
            parsed_transient = scanner.scan_single("transient.bin")

        self.assertIsNotNone(parsed_local)
        self.assertIsNotNone(parsed_transient)
        assert parsed_local is not None
        assert parsed_transient is not None
        self.assertEqual(9, parsed_local.size)
        self.assertTrue(parsed_local.status_sidecar_ready)
        self.assertEqual(0, parsed_transient.size)
        self.assertFalse(parsed_transient.status_sidecar_ready)
        parsed_local.is_staging = True

        scanner_events = [
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "lftp_sidecar_classified"
        ]
        self.assertEqual(
            {"valid", "malformed"},
            {entry["details"]["classification"] for entry in scanner_events},
        )
        self.assertEqual(
            {"known"},
            {entry["details"]["parser_coverage"] for entry in scanner_events},
        )
        self.assertTrue(all(entry["details"]["scan_role"] == "local" for entry in scanner_events))
        self.assertTrue(all(entry["details"]["status_only"] is False for entry in scanner_events))

        controller = self._make_v092_pending_completion_controller(
            complete=False, sidecar_ready=True,
        )
        controller._Controller__pending_completion_file_names = {("pending.bin", None, None)}
        controller._Controller__model_builder.set_local_files([parsed_local])
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "pending.bin", "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(10, 10, 100, 100, 0)
        controller._Controller__model_builder.set_lftp_statuses([running])
        controller._Controller__last_lftp_statuses = [running]
        controller._Controller__lftp.last_status_poll_healthy = False
        controller._Controller__lftp.status.return_value = ([], False)
        controller._Controller__lftp_status_poll_retry_active = False
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__record_breadcrumb = lambda **kwargs: trace.record(
            "model_updater", kwargs["message"], kwargs["details"],
            **{key: value for key, value in kwargs.items() if key not in {"message", "details"}},
        )
        controller._Controller__lftp.backend_name = "lftp"

        ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_not_called()
        controller._record_download_completion.assert_not_called()
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)
        self.assertNotIn(
            ModelFile.build_file_id("pending.bin", None),
            controller._Controller__persist.stopped_file_names,
        )
        self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)
        entries = trace.snapshot()["entries"]
        status_events = [entry for entry in entries if entry["message"] == "lftp_status_poll"]
        self.assertEqual(1, len(status_events))
        self.assertEqual("cached_unhealthy", status_events[0]["details"]["outcome"])
        self.assertEqual(1, status_events[0]["details"]["active_count"])
        serialized = str(entries)
        self.assertNotIn("pending.bin", serialized)
        self.assertNotIn(temp_dir, serialized)

    def test_v092_upstream_base_only_pget_sidecar_is_pending_checkpoint(self):
        """Base-only late checkpoints should report conservative covered bytes."""
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=32,
            policy={
                "default": "off",
                "rules": {"lftp.sidecar": "info", "transfer.lftp": "debug"},
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "pending.bin")
            with open(target, "wb") as handle:
                handle.write(b"x" * 9)
            with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                handle.write("size=10\n0.pos=9\n")

            scanner = SystemScanner(temp_dir)
            scanner.set_scan_role("local")
            scanner.set_breadcrumb_trace(trace)
            parsed_local = scanner.scan_single("pending.bin")

        self.assertIsNotNone(parsed_local)
        assert parsed_local is not None
        sidecar_events = [
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "lftp_sidecar_classified"
        ]
        self.assertEqual(1, len(sidecar_events))
        # Upstream lftp emits this late-stage base-only form without a limit.
        # Keep this expectation strict so a parser regression is visible at
        # the classification boundary instead of being treated as transient.
        self.assertEqual("valid", sidecar_events[0]["details"]["classification"])
        self.assertEqual("known", sidecar_events[0]["details"]["parser_coverage"])
        self.assertEqual(9, parsed_local.size)
        self.assertTrue(parsed_local.status_sidecar_ready)
        parsed_local.is_staging = True

        controller = self._make_v092_pending_completion_controller(
            complete=False, sidecar_ready=True,
        )
        controller._Controller__pending_completion_file_names = {("pending.bin", None, None)}
        controller._Controller__model_builder.set_local_files([parsed_local])
        pending_file_id = ModelFile.build_file_id("pending.bin", None)
        model_local = controller._Controller__model_builder._ModelBuilder__local_file(pending_file_id)
        self.assertIs(model_local, parsed_local)
        self.assertEqual("pending.bin", model_local.name)
        self.assertEqual(9, model_local.size)
        self.assertTrue(model_local.status_sidecar_ready)
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "pending.bin", "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(10, 10, 100, 100, 0)
        controller._Controller__model_builder.set_lftp_statuses([running])
        controller._Controller__last_lftp_statuses = [running]
        controller._Controller__lftp.last_status_poll_healthy = False
        controller._Controller__lftp.status.return_value = ([], False)
        controller._Controller__lftp_status_poll_retry_active = False
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__record_breadcrumb = lambda **kwargs: trace.record(
            "model_updater", kwargs["message"], kwargs["details"],
            **{key: value for key, value in kwargs.items() if key not in {"message", "details"}},
        )
        controller._Controller__lftp.backend_name = "lftp"

        ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_not_called()
        controller._record_download_completion.assert_not_called()
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)
        self.assertNotIn(
            pending_file_id,
            controller._Controller__persist.stopped_file_names,
        )
        self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)
        serialized = str(trace.snapshot()["entries"])
        self.assertNotIn("pending.bin", serialized)
        self.assertNotIn(temp_dir, serialized)

    def test_v092_base_only_checkpoint_at_declared_size_remains_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "pending.bin")
            with open(target, "wb") as handle:
                handle.write(b"x" * 10)
            with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                handle.write("size=10\n0.pos=10\n")

            scanner = SystemScanner(temp_dir)
            parsed_local = scanner.scan_single("pending.bin")

        self.assertIsNotNone(parsed_local)
        assert parsed_local is not None
        self.assertEqual(10, parsed_local.size)
        self.assertTrue(parsed_local.status_sidecar_ready)
        parsed_local.is_staging = True

        controller = self._make_v092_pending_completion_controller(
            complete=True, sidecar_ready=True,
        )
        controller._Controller__pending_completion_file_names = {("pending.bin", None, None)}
        controller._Controller__model_builder.set_local_files([parsed_local])
        pending_file_id = ModelFile.build_file_id("pending.bin", None)
        model_local = controller._Controller__model_builder._ModelBuilder__local_file(pending_file_id)
        self.assertIs(model_local, parsed_local)
        self.assertEqual(10, model_local.size)
        self.assertTrue(model_local.status_sidecar_ready)
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "pending.bin", "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(10, 10, 100, 100, 0)
        controller._Controller__model_builder.set_lftp_statuses([running])
        controller._Controller__last_lftp_statuses = [running]
        controller._Controller__lftp.last_status_poll_healthy = False
        controller._Controller__lftp.status.return_value = ([], False)
        controller._Controller__lftp_status_poll_retry_active = False
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)

        ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_not_called()
        controller._record_download_completion.assert_not_called()
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)
        self.assertNotIn(pending_file_id, controller._Controller__persist.stopped_file_names)
        self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)

    def test_v092_late_terminal_cached_retry_keeps_sidecar_completion_pending(self):
        """Keep a late, ambiguous PGET terminal from finalizing staged data."""
        path_pair_id = "pair-b"
        controller = self._make_v092_pending_completion_controller(
            path_pair_id=path_pair_id, sidecar_ready=True,
        )
        pending_entry = ("pending.bin", path_pair_id, None)
        controller._Controller__pending_completion_file_names = {pending_entry}

        # A status row without a Path Pair is intentionally ambiguous with the
        # scoped pending source.  Its complete transfer counters do not prove
        # physical completion while the valid PGET sidecar remains present.
        cached_running = LftpJobStatus(
            9, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING,
            "pending.bin", "",
        )
        cached_running.total_transfer_state = LftpJobStatus.TransferState(
            10, 10, 100, 100, 0,
        )
        controller._Controller__last_lftp_statuses = [cached_running]
        controller._Controller__lftp.last_status_poll_healthy = False
        controller._Controller__lftp.status.return_value = ([], False)
        controller._Controller__lftp_status_poll_retry_active = False
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)

        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=16,
            policy={"default": "off", "rules": {"transfer.lftp": "debug"}},
        )
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__record_breadcrumb = lambda **kwargs: trace.record(
            "model_updater", kwargs["message"], kwargs["details"],
            **{key: value for key, value in kwargs.items() if key not in {"message", "details"}},
        )
        controller._Controller__lftp.backend_name = "lftp"

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

            # The retry interval serves the same cached row without polling;
            # preserve the ambiguous active authority for this second tick.
            controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
            ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_not_called()
        controller._record_download_completion.assert_not_called()
        controller._complete_download_start_lifecycle.assert_not_called()
        controller._mark_successful_final_move_handoff.assert_not_called()
        controller._mark_current_process_final_publication.assert_not_called()
        controller.clear_extracted_marker.assert_not_called()
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)
        self.assertNotIn(
            ModelFile.build_file_id("pending.bin", path_pair_id),
            controller._Controller__persist.stopped_file_names,
        )
        self.assertIn(pending_entry, controller._Controller__pending_completion_file_names)

        entries = trace.snapshot()["entries"]
        self.assertEqual(
            ["cached_unhealthy", "cached_retry"],
            [entry["details"]["outcome"] for entry in entries],
        )
        for entry in entries:
            details = entry["details"]
            self.assertFalse(details["fresh"])
            self.assertFalse(details["healthy"])
            self.assertTrue(details["retry_active"])
            self.assertEqual(1, details["active_count"])
        serialized = str(entries)
        self.assertNotIn("pending.bin", serialized)
        self.assertNotIn(path_pair_id, serialized)

    def test_v092_scoped_pending_completion_defers_for_unscoped_same_name_active_status(self):
        for job_type in (LftpJobStatus.Type.GET, LftpJobStatus.Type.PGET):
            for state in (LftpJobStatus.State.QUEUED, LftpJobStatus.State.RUNNING):
                with self.subTest(job_type=job_type, state=state):
                    controller = self._make_v092_pending_completion_controller(path_pair_id="pair-b")
                    unscoped_status = LftpJobStatus(
                        9, job_type, state, "pending.bin", "",
                    )
                    if state == LftpJobStatus.State.RUNNING:
                        unscoped_status.total_transfer_state = LftpJobStatus.TransferState(
                            1, 10, 10, 100, 1,
                        )
                    # Exercise the ambiguity that matters here: an older
                    # authoritative-idle marker with a cached one-sided
                    # status. Its legacy file id differs from pair-b's id.
                    controller._Controller__last_lftp_statuses = [unscoped_status]
                    controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
                    controller._Controller__lftp_idle_status_authoritative = True

                    with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
                        ModelUpdater(controller).update()

                    controller._Controller__move_from_staging.assert_not_called()
                    self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
                    self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)
                    self.assertIn(
                        ("pending.bin", "pair-b", None),
                        controller._Controller__pending_completion_file_names,
                    )

    def test_v092_scoped_pending_completion_ignores_unscoped_active_other_name(self):
        controller = self._make_v092_pending_completion_controller(path_pair_id="pair-b")
        other_status = LftpJobStatus(
            9, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "other.bin", "",
        )
        other_status.total_transfer_state = LftpJobStatus.TransferState(1, 10, 10, 100, 1)
        controller._Controller__last_lftp_statuses = [other_status]
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
        controller._Controller__lftp_idle_status_authoritative = True

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_called_once_with("pending.bin", "pair-b")

    def test_v092_pending_completion_failure_retries_after_existing_delay(self):
        controller = self._make_v092_pending_completion_controller()
        controller._Controller__move_from_staging.side_effect = [
            Controller.MoveFromStagingResult.FAILED,
            Controller.MoveFromStagingResult.COMPLETED,
        ]
        file_id = ModelFile.build_file_id("pending.bin", None)

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()
        self.assertEqual(1, controller._Controller__move_from_staging.call_count)
        self.assertEqual(1, controller._Controller__persist.move_failure_counts[file_id])
        self.assertIn(("pending.bin", None, None), controller._Controller__pending_completion_file_names)

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()
        self.assertEqual(1, controller._Controller__move_from_staging.call_count)

        controller._Controller__move_retry_due[file_id] = datetime.now() - timedelta(seconds=1)
        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()
        self.assertEqual(2, controller._Controller__move_from_staging.call_count)
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        self.assertEqual({file_id}, controller._Controller__persist.downloaded_file_names)

    def test_v092_pending_completion_no_move_clears_resume_source_binding(self):
        controller = self._make_v092_pending_completion_controller()
        file_id = ModelFile.build_file_id("pending.bin", None)
        controller._Controller__persist.resume_source_identities = {file_id: (10, 1)}
        controller._Controller__move_from_staging.return_value = Controller.MoveFromStagingResult.NO_MOVE_APPLICABLE

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

        self.assertEqual({}, controller._Controller__persist.resume_source_identities)

    def test_v092_pending_completion_respects_explicit_stop(self):
        controller = self._make_v092_pending_completion_controller()
        controller._Controller__is_explicitly_stopped.return_value = True
        # Cover an already-registered completion whose stop arrives before
        # the quiet finalization pass, rather than the earlier LFTP detector
        # (which correctly avoids registering stopped work in the first place).
        controller._Controller__pending_completion_file_names = {("pending.bin", None, None)}

        with patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[]):
            ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_not_called()
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)

    def test_v092_nested_pending_completion_stop_uses_canonical_full_path(self):
        path_pair_id = "pair-a"
        full_path = "release/nested/pending.bin"
        child_id = ModelFile.build_file_id(full_path, path_pair_id)
        controller = self._make_v092_pending_completion_controller(path_pair_id=path_pair_id)
        old_root = ModelFile("release", True)
        old_root.path_pair_id = path_pair_id
        old_nested = ModelFile("nested", True)
        old_child = ModelFile("pending.bin", False)
        old_child.path_pair_id = path_pair_id
        old_child.remote_size = 10
        old_child.local_size = 9
        old_nested.add_child(old_child)
        old_root.add_child(old_nested)
        new_root = ModelFile("release", True)
        new_root.path_pair_id = path_pair_id
        new_nested = ModelFile("nested", True)
        new_child = ModelFile("pending.bin", False)
        new_child.path_pair_id = path_pair_id
        new_child.remote_size = 10
        new_child.local_size = 10
        new_child.state = ModelFile.State.DOWNLOADED
        new_nested.add_child(new_child)
        new_root.add_child(new_nested)
        self.assertEqual(child_id, new_child.file_id)
        controller._Controller__pending_completion_file_names = {
            (full_path, path_pair_id, None),
        }
        controller._Controller__prev_downloading_file_names = set()
        controller._Controller__is_explicitly_stopped = MagicMock(
            side_effect=lambda name, pair_id: (name, pair_id) == (full_path, path_pair_id),
        )
        controller._reserve_move_attempt = MagicMock(return_value=True)
        controller._release_move_attempt = MagicMock()
        controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )
        # Model roots own publication, while the update decision is made from
        # the exact nested ModelFile reported by the recursive diff.
        controller._Controller__model.update_file = MagicMock()

        with patch(
                "controller.model_updater.ModelDiffUtil.diff_models",
                return_value=[ModelDiff(ModelDiff.Change.UPDATED, old_child, new_child)],
        ):
            ModelUpdater(controller).update()

        controller._Controller__is_explicitly_stopped.assert_any_call(full_path, path_pair_id)
        controller._Controller__move_from_staging.assert_not_called()
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        self.assertNotIn(child_id, controller._Controller__persist.downloaded_file_names)
        self.assertNotIn(child_id, controller._Controller__persist.final_move_succeeded_file_names)

    def test_v092_stop_cancels_pending_completion_before_downloaded_diff_can_finalize(self):
        controller = self._make_v092_pending_completion_controller()
        controller._Controller__is_explicitly_stopped.return_value = True
        controller._Controller__pending_completion_file_names = {("pending.bin", None, None)}
        old_file = controller._Controller__model.get_file("pending.bin")
        downloaded_file = ModelFile("pending.bin", False)
        downloaded_file.remote_size = 10
        downloaded_file.local_size = 10
        downloaded_file.state = ModelFile.State.DOWNLOADED

        with patch(
                "controller.model_updater.ModelDiffUtil.diff_models",
                return_value=[ModelDiff(ModelDiff.Change.UPDATED, old_file, downloaded_file)],
        ):
            ModelUpdater(controller).update()

        controller._Controller__move_from_staging.assert_not_called()
        self.assertEqual(set(), controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), controller._Controller__persist.final_move_succeeded_file_names)

    def test_active_delta_authorization_rejection_publishes_only_full_reconciliation(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        live_model = builder.build_model()
        listener = MagicMock()
        live_model.add_listener(listener)
        remote_scan = ScannerResult(
            datetime.now(), [SystemFile("root", 100, False)],
            scanned_path_pair_ids={None}, is_scan_final=True,
        )
        controller, _ = self._make_progressive_update_controller(
            remote_scan, local_scan=None, model_builder=builder, model=live_model,
        )
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=2,
            policy={"default": "off", "rules": {"model.progress": "debug", "scan.authority": "info"}},
        )
        controller._Controller__context.breadcrumb_trace = trace
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [status]
        builder.authorize_active_transfer_delta = MagicMock(return_value=False)
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)
        rejected_model_version = live_model.version

        ModelUpdater(controller).update()

        builder.build_model.assert_called_once()
        listener.file_updated.assert_called_once()
        self.assertEqual(25, live_model.get_file("root").transferred_size)
        authority_calls = [
            call for call in controller._Controller__record_breadcrumb.call_args_list
            if call.kwargs.get("message") == "scan_authority"
        ]
        self.assertEqual(1, len(authority_calls))
        self.assertEqual("publish", authority_calls[0].kwargs["details"]["outcome"])
        self.assertEqual(
            "published_after_active_delta_rejection",
            authority_calls[0].kwargs["details"]["reason"],
        )
        summary = trace.snapshot()["active_delta_rejection_summary"]
        self.assertEqual("active_delta_authorization_rejected", summary["reason"])
        self.assertEqual(rejected_model_version, summary["model_version"])
        self.assertTrue(summary["corr_id"].startswith("root-progress:"))
        self.assertEqual(16, len(summary["corr_id"].removeprefix("root-progress:")))

    def test_active_delta_selector_rejection_replaces_older_authorization_summary(self):
        first = SystemFile("private-release.bin", 100, False)
        first.path_pair_id = "first"
        second = SystemFile("private-release.bin", 100, False)
        second.path_pair_id = "second"
        builder = ModelBuilder()
        builder.set_remote_files([first, second])
        builder.set_extracted_files({"private-release.bin"})
        live_model = builder.build_model()
        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "private-release.bin", "",
        )
        status.path_pair_id = "first"
        builder.set_lftp_statuses([status])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        trace = BreadcrumbTraceCollector(
            lambda: True, max_entries=2,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        trace.record_active_delta_authorization_rejection(
            "root-progress:0123456789abcdef", 1, {},
        )
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__lftp.status.return_value = [status]
        original_diagnostics = builder.active_transfer_delta_diagnostics
        def diagnostic_snapshot(*args):
            self.assertFalse(getattr(builder.build_model, "called", False))
            return original_diagnostics(*args)
        builder.active_transfer_delta_diagnostics = MagicMock(side_effect=diagnostic_snapshot)
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        builder.build_model.assert_called_once()
        summary = trace.snapshot()["active_delta_rejection_summary"]
        self.assertEqual("active_delta_selector_rejected", summary["reason"])
        self.assertEqual(["status", "ambiguity"], summary["diagnostics"]["rejection_categories"])
        self.assertEqual("ambiguous_global_visibility", summary["diagnostics"]["selector_failure"])
        self.assertNotIn("private-release.bin", str(summary))

    def test_active_delta_status_missing_selector_summary_has_bounded_poll_provenance(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("active.bin", 100, False)])
        live_model = builder.build_model()
        prior_status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "",
        )
        builder.set_lftp_statuses([prior_status])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__lftp.status.return_value = []

        ModelUpdater(controller).update()

        summary = trace.snapshot()["active_delta_rejection_summary"]
        self.assertEqual("active_delta_selector_rejected", summary["reason"])
        diagnostics = summary["diagnostics"]
        self.assertEqual("status_missing", diagnostics["selector_failure"])
        self.assertEqual({
            "poll_source": "fresh_healthy", "fresh": True, "healthy": True,
            "raw_count_bucket": "0", "filtered_count_bucket": "0",
            "active_scan_root_present": False, "raw_status_match": False,
            "filtered_status_match": False, "canonical_match": False,
            "file_id_match": False, "pair_match": False, "retry_active": False,
            "future_state": "none",
            "poll_decision": {
                "idle_authoritative": False, "next_poll_present": False,
                "last_status_count_bucket": "0", "active_scan_result_root_count_bucket": "0",
                "builder_pending_active_delta": True,
                "builder_active_touched_count_bucket": "0",
                "builder_lftp_touched_count_bucket": "1",
                "poll_due_reason": "no_idle_authority",
            },
        }, diagnostics["status_missing_provenance"])
        self.assertEqual({
            "idle_authoritative": False, "next_poll_present": False,
            "last_status_count_bucket": "0", "active_scan_result_root_count_bucket": "0",
            "builder_pending_active_delta": True,
            "builder_active_touched_count_bucket": "0",
            "builder_lftp_touched_count_bucket": "1",
            "poll_due_reason": "no_idle_authority",
        }, diagnostics["status_missing_provenance"]["poll_decision"])
        self.assertNotIn("active.bin", str(summary))

    def _establish_delayed_active_scan_handoff(self, controller, updater, statuses):
        """Poll active, then empty, without restoring controller download state."""
        controller._Controller__lftp.status.side_effect = [statuses, []]
        updater.update()
        controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        updater.update()
        controller._Controller__lftp.status.side_effect = None
        self.assertTrue(controller._Controller__lftp_idle_status_authoritative)

    def test_active_scan_root_wakes_cached_idle_status_poll_and_admits_active_delta(self):
        builder = ModelBuilder()
        root = SystemFile("active.bin", 100, False)
        builder.set_remote_files([root])
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        controller._Controller__context.breadcrumb_trace = trace

        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        updater = ModelUpdater(controller)
        self._establish_delayed_active_scan_handoff(controller, updater, [status])
        controller._Controller__lftp.status.return_value = [status]
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("active.bin", 100, False)],
        )

        updater.update()

        self.assertEqual(3, controller._Controller__lftp.status.call_count)
        self.assertEqual(25, live_model.get_file("active.bin").transferred_size)
        self.assertFalse(controller._Controller__lftp_idle_status_authoritative)
        self.assertIsNotNone(controller._Controller__next_lftp_status_poll_at)
        # A later status-missing rejection must retain the handoff reason
        # without exposing the scanned root identity.
        status_missing = _active_delta_status_missing_provenance(
            controller,
            source="fresh_healthy",
            fresh=True,
            healthy=True,
            poll_error=None,
            raw_count=0,
            filtered_count=0,
            active_scan_root_present=True,
            poll_decision=_active_delta_poll_decision_diagnostics(
                controller, builder,
                controller._Controller__active_scan_process.pop_latest_result.return_value,
                poll_due=True,
                poll_due_reason="active_scan_lftp_transition",
            ),
        )
        self.assertEqual(
            "active_scan_lftp_transition",
            status_missing["poll_decision"]["poll_due_reason"],
        )

    def test_cached_idle_tick_without_active_scan_does_not_wake_status_poll(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("active.bin", 100, False)])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=builder.build_model(),
        )

        updater = ModelUpdater(controller)
        updater.update()
        updater.update()

        self.assertEqual(1, controller._Controller__lftp.status.call_count)
        self.assertTrue(controller._Controller__lftp_idle_status_authoritative)

    def test_active_scan_wakeup_respects_status_poll_retry_backoff(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("active.bin", 100, False)])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=builder.build_model(),
        )
        updater = ModelUpdater(controller)
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "",
        )
        self._establish_delayed_active_scan_handoff(controller, updater, [running])
        controller._Controller__lftp_status_poll_retry_active = True
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("active.bin", 100, False)],
        )

        updater.update()

        self.assertEqual(2, controller._Controller__lftp.status.call_count)
        self.assertTrue(controller._Controller__lftp_status_poll_retry_active)

    def test_active_scan_wakeup_keeps_inflight_status_poll_nonblocking(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("active.bin", 100, False)])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=builder.build_model(),
        )
        updater = ModelUpdater(controller)
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "",
        )
        self._establish_delayed_active_scan_handoff(controller, updater, [running])
        controller._get_lftp_status_snapshot = MagicMock(return_value=None)
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("active.bin", 100, False)],
        )

        updater.update()

        controller._get_lftp_status_snapshot.assert_called_once_with()
        self.assertEqual(2, controller._Controller__lftp.status.call_count)
        self.assertFalse(controller._Controller__lftp_idle_status_authoritative)
        self.assertIsNone(controller._Controller__next_lftp_status_poll_at)

    def test_identical_active_scan_does_not_repeat_idle_wakeup_after_empty_poll(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("active.bin", 100, False)])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=builder.build_model(),
        )
        updater = ModelUpdater(controller)
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "",
        )
        self._establish_delayed_active_scan_handoff(controller, updater, [running])
        active_scan = ScannerResult(datetime.now(), [SystemFile("active.bin", 100, False)])
        controller._Controller__active_scan_process.pop_latest_result.return_value = active_scan

        updater.update()

        self.assertEqual(3, controller._Controller__lftp.status.call_count)
        self.assertTrue(controller._Controller__lftp_idle_status_authoritative)
        updater.update()

        self.assertEqual(3, controller._Controller__lftp.status.call_count)

    def test_extract_or_pending_completion_active_scan_does_not_wake_idle_poll(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("active.bin", 100, False)])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=builder.build_model(),
        )
        updater = ModelUpdater(controller)
        updater.update()
        controller._Controller__active_extracting_file_names = [("active.bin", None, None)]
        controller._Controller__pending_completion_file_names = {("active.bin", None, None)}
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("active.bin", 100, False)],
        )

        updater.update()

        self.assertEqual(1, controller._Controller__lftp.status.call_count)

    def test_active_scan_latch_prunes_removed_roots_before_reappearance(self):
        builder = ModelBuilder()
        builder.set_remote_files([
            SystemFile("first.bin", 100, False), SystemFile("second.bin", 100, False),
        ])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=builder.build_model(),
        )
        updater = ModelUpdater(controller)
        first = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "first.bin", "",
        )
        second = LftpJobStatus(
            2, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "second.bin", "",
        )
        self._establish_delayed_active_scan_handoff(controller, updater, [first, second])
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("first.bin", 100, False), SystemFile("second.bin", 100, False)],
        )
        updater.update()
        self.assertEqual(3, controller._Controller__lftp.status.call_count)
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("second.bin", 100, False)],
        )
        updater.update()
        self.assertEqual(3, controller._Controller__lftp.status.call_count)
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("first.bin", 100, False), SystemFile("second.bin", 100, False)],
        )

        updater.update()

        self.assertEqual(4, controller._Controller__lftp.status.call_count)

    def test_active_delta_status_missing_summary_marks_healthy_cached_retry(self):
        builder = ModelBuilder()
        builder.set_remote_files([
            SystemFile("active.bin", 100, False), SystemFile("cached.bin", 100, False),
        ])
        live_model = builder.build_model()
        prior_status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "",
        )
        cached_status = LftpJobStatus(
            2, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "cached.bin", "",
        )
        builder.set_lftp_statuses([prior_status])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__last_lftp_statuses = [cached_status]
        controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
        controller._Controller__lftp.last_status_poll_healthy = True

        ModelUpdater(controller).update()

        summary = trace.snapshot()["active_delta_rejection_summary"]
        self.assertEqual("status_missing", summary["diagnostics"]["selector_failure"])
        provenance = summary["diagnostics"]["status_missing_provenance"]
        self.assertEqual("cached_retry", provenance["poll_source"])
        self.assertTrue(provenance["healthy"])
        self.assertEqual("retry_pending", provenance["failure_reason"])

    def test_disabled_progress_trace_skips_selector_diagnostics_snapshot(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("active.bin", 100, False)])
        live_model = builder.build_model()
        builder.set_extracted_files({"unrelated-private.bin"})
        status = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "active.bin", "")
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        controller._Controller__context.breadcrumb_trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off"},
        )
        controller._Controller__lftp.status.return_value = [status]
        builder.active_transfer_delta_diagnostics = MagicMock(
            side_effect=AssertionError("disabled trace must not read selector diagnostics"),
        )
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        builder.active_transfer_delta_diagnostics.assert_not_called()
        builder.build_model.assert_called_once()

    def test_active_delta_authorization_summary_uses_enabled_legacy_recorder(self):
        legacy_recorder = MagicMock()
        trace = SimpleNamespace(
            is_effectively_enabled=lambda category, level: category == "model.progress" and level == "debug",
            record_active_delta_authorization_rejection=legacy_recorder,
        )
        controller = SimpleNamespace(_Controller__context=SimpleNamespace(breadcrumb_trace=trace))

        _record_active_delta_rejection_summary(
            controller, SimpleNamespace(version=3), "active_delta_authorization_rejected",
            {"rejection_categories": ["authority"]}, {"private-file-id"},
        )

        legacy_recorder.assert_called_once()
        self.assertEqual("root-progress:", legacy_recorder.call_args.args[0][:14])
        self.assertEqual(3, legacy_recorder.call_args.args[1])

    def test_active_delta_status_missing_provenance_distinguishes_poll_outcomes(self):
        controller = SimpleNamespace(
            _Controller__lftp=SimpleNamespace(last_status_poll_failure_reason="command_error"),
            _Controller__lftp_status_poll_retry_active=True,
        )
        healthy_empty = _active_delta_status_missing_provenance(
            controller, source="fresh_healthy", fresh=True, healthy=True, poll_error=None,
            raw_count=0, filtered_count=0, active_scan_root_present=False,
        )
        cached_retry = _active_delta_status_missing_provenance(
            controller, source="cached_retry", fresh=False, healthy=False, poll_error=None,
            raw_count=2, filtered_count=1, active_scan_root_present=True,
        )

        self.assertEqual("fresh_healthy", healthy_empty["poll_source"])
        self.assertEqual("0", healthy_empty["raw_count_bucket"])
        self.assertNotIn("failure_reason", healthy_empty)
        self.assertEqual("retry_pending", cached_retry["failure_reason"])
        self.assertEqual("2-4", cached_retry["raw_count_bucket"])
        self.assertEqual("1", cached_retry["filtered_count_bucket"])
        self.assertTrue(cached_retry["retry_active"])

    def test_active_delta_status_missing_provenance_accepts_timeout_and_eof_only_when_unhealthy(self):
        controller = SimpleNamespace(
            _Controller__lftp=SimpleNamespace(last_status_poll_failure_reason="timeout"),
            _Controller__lftp_status_poll_retry_active=False,
        )
        healthy = _active_delta_status_missing_provenance(
            controller, source="fresh_healthy", fresh=True, healthy=True, poll_error=None,
            raw_count=0, filtered_count=0, active_scan_root_present=False,
        )
        unhealthy_timeout = _active_delta_status_missing_provenance(
            controller, source="unhealthy_empty", fresh=True, healthy=False, poll_error=None,
            raw_count=0, filtered_count=0, active_scan_root_present=False,
        )
        controller._Controller__lftp.last_status_poll_failure_reason = "eof"
        unhealthy_eof = _active_delta_status_missing_provenance(
            controller, source="error_empty", fresh=True, healthy=False, poll_error=None,
            raw_count=0, filtered_count=0, active_scan_root_present=False,
        )

        self.assertNotIn("failure_reason", healthy)
        self.assertEqual("timeout", unhealthy_timeout["failure_reason"])
        self.assertEqual("eof", unhealthy_eof["failure_reason"])

    def test_active_delta_status_missing_provenance_marks_healthy_cached_retry_not_idle(self):
        controller = SimpleNamespace(
            _Controller__lftp=SimpleNamespace(last_status_poll_failure_reason="command_error"),
            _Controller__lftp_status_poll_retry_active=True,
        )
        cached_retry = _active_delta_status_missing_provenance(
            controller, source="cached_retry", fresh=False, healthy=True, poll_error=None,
            raw_count=1, filtered_count=1, active_scan_root_present=False,
        )
        cached_idle = _active_delta_status_missing_provenance(
            controller, source="cached_idle", fresh=False, healthy=True, poll_error=None,
            raw_count=0, filtered_count=0, active_scan_root_present=False,
        )

        self.assertEqual("retry_pending", cached_retry["failure_reason"])
        self.assertNotIn("failure_reason", cached_idle)

    def test_active_delta_status_match_evidence_is_target_specific_and_identity_free(self):
        root_file_id = ModelFile.build_file_id("active.bin", "pair-a")
        exact = SimpleNamespace(name="active.bin", path_pair_id="pair-a", file_id=root_file_id)
        malformed = SimpleNamespace(name="active.bin", path_pair_id="pair-a", file_id=root_file_id)
        pair_only = SimpleNamespace(
            name="other.bin", path_pair_id="pair-a",
            file_id=ModelFile.build_file_id("other.bin", "pair-a"),
        )
        canonical_only = SimpleNamespace(
            name="active.bin", path_pair_id="pair-a", file_id="noncanonical-id",
        )
        unrelated = SimpleNamespace(
            name="other.bin", path_pair_id="pair-b",
            file_id=ModelFile.build_file_id("other.bin", "pair-b"),
        )

        self.assertEqual({
            "raw_status_match": True, "filtered_status_match": False,
            "canonical_match": True, "file_id_match": True, "pair_match": True,
        }, _active_delta_status_match_evidence([malformed], [])(root_file_id, "pair-a"))
        self.assertEqual({
            "raw_status_match": False, "filtered_status_match": False,
            "canonical_match": False, "file_id_match": False, "pair_match": True,
        }, _active_delta_status_match_evidence([pair_only], [pair_only])(root_file_id, "pair-a"))
        self.assertEqual({
            "raw_status_match": True, "filtered_status_match": True,
            "canonical_match": True, "file_id_match": False, "pair_match": True,
        }, _active_delta_status_match_evidence([canonical_only], [canonical_only])(root_file_id, "pair-a"))
        self.assertEqual({
            "raw_status_match": False, "filtered_status_match": False,
            "canonical_match": False, "file_id_match": False, "pair_match": False,
        }, _active_delta_status_match_evidence([unrelated], [unrelated])(root_file_id, "pair-a"))
        self.assertNotIn("active.bin", str(_active_delta_status_match_evidence([exact], [exact])(root_file_id, "pair-a")))

    def test_disabled_progress_trace_skips_authorization_diagnostics_snapshot(self):
        builder = ModelBuilder()
        builder.set_remote_files([SystemFile("root", 100, False)])
        live_model = builder.build_model()
        remote_scan = ScannerResult(
            datetime.now(), [SystemFile("root", 100, False)],
            scanned_path_pair_ids={None}, is_scan_final=True,
        )
        controller, _ = self._make_progressive_update_controller(
            remote_scan, local_scan=None, model_builder=builder, model=live_model,
        )
        controller._Controller__context.breadcrumb_trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off"},
        )
        status = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "root", "")
        status.total_transfer_state = LftpJobStatus.TransferState(25, 100, 25, 10, 8)
        controller._Controller__lftp.status.return_value = [status]
        builder.authorize_active_transfer_delta = MagicMock(return_value=False)
        builder.active_transfer_delta_diagnostics = MagicMock(
            side_effect=AssertionError("disabled trace must not read authorization diagnostics"),
        )
        original_build = builder.build_model
        builder.build_model = MagicMock(wraps=original_build)

        ModelUpdater(controller).update()

        builder.build_model.assert_called_once()
        builder.active_transfer_delta_diagnostics.assert_not_called()
        self.assertEqual(25, live_model.get_file("root").transferred_size)

    def test_active_delta_rejection_correlation_identity_is_bounded_and_opaque(self):
        file_ids = {"private-target-{:04d}".format(index) for index in range(1_000)}

        with patch("controller.model_updater.opaque_trace_correlation", wraps=trace_session_digest) as digest:
            identity = _active_delta_rejection_correlation_identity(file_ids)

        self.assertLessEqual(digest.call_count, 16)
        self.assertTrue(identity.startswith("active-delta-targets:128:"))
        self.assertLessEqual(len(identity), len("active-delta-targets:128:") + (16 * 16) + 15)
        self.assertNotIn("private-target", identity)

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
                resume_source_identities={},
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
                breadcrumb_trace=SimpleNamespace(
                    is_effectively_enabled=lambda category, level="info": True,
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
            category="scan.result",
            level="info",
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
                resume_source_identities={},
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

    def test_lftp_completion_retirement_breadcrumb_records_pending_and_no_retirement(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        active_entry = ("sample-movie.mkv", "path-pair-a", "Path Pair A")
        controller = self._make_lftp_completion_controller({active_entry})
        controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)
        updater = ModelUpdater(controller)

        updater._handle_lftp_completion_detection(
            [],
            True,
            lftp_status_poll_authoritative=True,
            lftp_status_snapshot_fresh=True,
            lftp_status_poll_healthy=True,
            lftp_status_source="fresh_healthy",
        )
        controller._Controller__prev_downloading_file_names = {active_entry}
        updater._handle_lftp_completion_detection(
            [active_entry],
            True,
            lftp_status_poll_authoritative=True,
            lftp_status_snapshot_fresh=True,
            lftp_status_poll_healthy=True,
            lftp_status_source="fresh_healthy",
        )

        entries = trace.snapshot()["entries"]
        self.assertEqual(
            ["completion_pending_registered", "completion_retirement_not_detected"],
            [entry["message"] for entry in entries],
        )
        pending, active = entries
        self.assertEqual(
            {
                "poll_eligible": True,
                "poll_fresh": True,
                "poll_healthy": True,
                "poll_source": "fresh_healthy",
                "previous_active": True,
                "current_active": False,
                "decision": "pending",
                "reason": "lftp_job_finished",
                "marker_observed": False,
                "local_scan_forced": True,
                "registration_source": "lftp_job_finished",
            },
            pending["details"],
        )
        self.assertEqual("no_retirement", active["details"]["decision"])
        self.assertEqual("still_active", active["details"]["reason"])
        self.assertTrue(active["details"]["current_active"])
        self.assertFalse(active["details"]["local_scan_forced"])
        self.assertNotIn("sample-movie.mkv", str(entries))
        self.assertNotIn("path-pair-a", str(entries))
        self.assertNotIn("Path Pair A", str(entries))
        self.assertTrue(all(entry["corr_id"].startswith("completion:") for entry in entries))

    def test_lftp_completion_retirement_breadcrumb_records_unhealthy_and_inflight_blocks(self):
        for source, fresh in (("unhealthy_empty", True), ("inflight_empty", False)):
            with self.subTest(source=source):
                trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
                entry = ("sample-blocked.mkv", "path-pair-a", "Path Pair A")
                controller = self._make_lftp_completion_controller({entry})
                controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)

                ModelUpdater(controller)._handle_lftp_completion_detection(
                    [],
                    False,
                    lftp_status_poll_authoritative=False,
                    lftp_status_snapshot_fresh=fresh,
                    lftp_status_poll_healthy=False,
                    lftp_status_source=source,
                )

                entries = trace.snapshot()["entries"]
                self.assertEqual(1, len(entries))
                self.assertEqual("completion_retirement_blocked", entries[0]["message"])
                self.assertEqual(
                    {
                        "poll_eligible": False,
                        "poll_fresh": fresh,
                        "poll_healthy": False,
                        "poll_source": source,
                        "previous_active": True,
                        "current_active": False,
                        "decision": "blocked",
                        "reason": "completion_detection_not_authoritative",
                        "marker_observed": False,
                        "local_scan_forced": False,
                    },
                    entries[0]["details"],
                )
                self.assertNotIn("sample-blocked.mkv", str(entries))
                self.assertNotIn("path-pair-a", str(entries))
                self.assertEqual(set(), controller._Controller__pending_completion_file_names)
                controller._Controller__local_scan_process.force_scan.assert_not_called()

    def test_lftp_completion_retirement_breadcrumb_records_explicit_stop_exclusion(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        entry = ("sample-stopped.mkv", "path-pair-a", "Path Pair A")
        controller = self._make_lftp_completion_controller({entry})
        controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)
        controller._Controller__is_explicitly_stopped.return_value = True

        ModelUpdater(controller)._handle_lftp_completion_detection(
            [],
            True,
            lftp_status_poll_authoritative=True,
            lftp_status_snapshot_fresh=True,
            lftp_status_poll_healthy=True,
            lftp_status_source="fresh_healthy",
        )

        entries = trace.snapshot()["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual("completion_retirement_excluded", entries[0]["message"])
        self.assertEqual("excluded", entries[0]["details"]["decision"])
        self.assertEqual("explicit_stop", entries[0]["details"]["reason"])
        self.assertFalse(entries[0]["details"]["local_scan_forced"])
        self.assertNotIn("sample-stopped.mkv", str(entries))
        self.assertNotIn("path-pair-a", str(entries))
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        controller._Controller__local_scan_process.force_scan.assert_not_called()

    def test_lftp_completion_retirement_breadcrumb_respects_category_gate_and_privacy(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"completion.gate": "off"}},
        )
        entry = ("sample-gated.mkv", "path-pair-a", "Path Pair A")
        controller = self._make_lftp_completion_controller({entry})
        controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)

        ModelUpdater(controller)._handle_lftp_completion_detection(
            [],
            True,
            lftp_status_poll_authoritative=True,
            lftp_status_snapshot_fresh=True,
            lftp_status_poll_healthy=True,
            lftp_status_source="fresh_healthy",
        )

        self.assertEqual([], trace.snapshot()["entries"])
        self.assertEqual(
            {entry}, controller._Controller__pending_completion_file_names,
        )
        controller._Controller__local_scan_process.force_scan.assert_called_once_with("path-pair-a")

    def test_lftp_completion_detection_mixed_legacy_and_pair_entries_are_total_ordered(self):
        entries = {
            ("same-name.mkv", None, None),
            ("same-name.mkv", "path-pair-a", "Path Pair A"),
        }

        with self.subTest(decision="no_retirement_and_dedupe"):
            trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
            controller = self._make_lftp_completion_controller(entries)
            controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)
            updater = ModelUpdater(controller)

            updater._handle_lftp_completion_detection(
                list(entries), True,
                lftp_status_poll_authoritative=True,
                lftp_status_snapshot_fresh=True,
                lftp_status_poll_healthy=True,
                lftp_status_source="fresh_healthy",
            )
            # The second identical tick is a diagnostic duplicate only.
            updater._handle_lftp_completion_detection(
                list(entries), True,
                lftp_status_poll_authoritative=True,
                lftp_status_snapshot_fresh=True,
                lftp_status_poll_healthy=True,
                lftp_status_source="fresh_healthy",
            )

            trace_entries = trace.snapshot()["entries"]
            self.assertEqual(2, len(trace_entries))
            self.assertTrue(all(
                entry["message"] == "completion_retirement_not_detected"
                for entry in trace_entries
            ))
            self.assertEqual(set(), controller._Controller__pending_completion_file_names)
            controller._Controller__local_scan_process.force_scan.assert_not_called()

        with self.subTest(decision="blocked"):
            trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
            controller = self._make_lftp_completion_controller(entries)
            controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)

            ModelUpdater(controller)._handle_lftp_completion_detection(
                [], False,
                lftp_status_poll_authoritative=False,
                lftp_status_snapshot_fresh=False,
                lftp_status_poll_healthy=False,
                lftp_status_source="inflight_empty",
            )

            trace_entries = trace.snapshot()["entries"]
            self.assertEqual(2, len(trace_entries))
            self.assertTrue(all(
                entry["message"] == "completion_retirement_blocked"
                for entry in trace_entries
            ))
            self.assertEqual(entries, controller._Controller__prev_downloading_file_names)
            self.assertEqual(set(), controller._Controller__pending_completion_file_names)
            controller._Controller__local_scan_process.force_scan.assert_not_called()

        with self.subTest(decision="explicit_stop_exclusion"):
            trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
            controller = self._make_lftp_completion_controller(entries)
            controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)
            controller._Controller__is_explicitly_stopped.side_effect = (
                lambda name, path_pair_id: path_pair_id is None
            )

            ModelUpdater(controller)._handle_lftp_completion_detection(
                [], True,
                lftp_status_poll_authoritative=True,
                lftp_status_snapshot_fresh=True,
                lftp_status_poll_healthy=True,
                lftp_status_source="fresh_healthy",
            )

            trace_entries = trace.snapshot()["entries"]
            self.assertEqual(
                {"completion_retirement_excluded", "completion_pending_registered"},
                {entry["message"] for entry in trace_entries},
            )
            self.assertEqual(
                {("same-name.mkv", "path-pair-a", "Path Pair A")},
                controller._Controller__pending_completion_file_names,
            )
            controller._Controller__local_scan_process.force_scan.assert_called_once_with(
                "path-pair-a"
            )

        with self.subTest(decision="pending_registration"):
            trace = BreadcrumbTraceCollector(lambda: True, max_entries=8)
            controller = self._make_lftp_completion_controller(entries)
            controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)

            ModelUpdater(controller)._handle_lftp_completion_detection(
                [], True,
                lftp_status_poll_authoritative=True,
                lftp_status_snapshot_fresh=True,
                lftp_status_poll_healthy=True,
                lftp_status_source="fresh_healthy",
            )

            trace_entries = trace.snapshot()["entries"]
            self.assertEqual(2, len(trace_entries))
            self.assertTrue(all(
                entry["message"] == "completion_pending_registered"
                for entry in trace_entries
            ))
            self.assertEqual(entries, controller._Controller__pending_completion_file_names)
            self.assertEqual(
                [call()],
                controller._Controller__local_scan_process.force_scan.call_args_list,
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

    def test_fresh_empty_lftp_poll_releases_valid_partial_pget_to_resumable_default(self):
        """A sidecar-backed partial is resumable, never an inferred Stop."""
        file_name = "pending.bin"
        file_id = ModelFile.build_file_id(file_name, None)
        remote = SystemFile(file_name, 10, False)
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, file_name)
            with open(target, "wb") as handle:
                handle.write(b"x" * 9)
            with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                # This is the upstream-valid late PGET form: one base offset,
                # no limit, and a physical staging file still one byte short.
                handle.write("size=10\n0.pos=9\n")
            active_scan_file = SystemScanner(temp_dir).scan_single(file_name)
        self.assertIsNotNone(active_scan_file)
        assert active_scan_file is not None
        self.assertEqual(9, active_scan_file.size)
        self.assertTrue(active_scan_file.status_sidecar_ready)
        self.assertFalse(active_scan_file.is_staging)
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(10, 10, 100, 1, 0)
        builder = ModelBuilder()
        builder.set_remote_files([remote])
        # ActiveScanner leaves this flag false; ModelBuilder owns the staging
        # interpretation for its own active-file copy. Preserve the raw scan
        # object so ModelUpdater receives the real scanner provenance too.
        builder.set_active_files([copy(active_scan_file)])
        builder.set_lftp_statuses([running])
        live_model = builder.build_model()
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=live_model,
        )
        controller._Controller__prev_downloading_file_names = {(file_name, None, None)}
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"completion.gate": "info"}},
        )
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__record_breadcrumb = lambda **kwargs: trace.record(
            "model_updater", kwargs["message"], kwargs["details"],
            **{key: value for key, value in kwargs.items() if key not in {"message", "details"}},
        )
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [active_scan_file], is_scan_final=True,
        )
        controller._Controller__lftp.status.return_value = []
        self.assertFalse(active_scan_file.is_staging)

        ModelUpdater(controller).update()

        published = controller._Controller__model.get_file(file_id)
        self.assertEqual(ModelFile.State.DEFAULT, published.state)
        self.assertIsNone(published.download_progress)
        self.assertIn((file_name, None, None), controller._Controller__pending_completion_file_names)
        self.assertNotIn(file_id, controller._Controller__persist.stopped_file_names)
        controller._Controller__lftp.queue.assert_not_called()
        pending = [
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "completion_pending_registered"
        ]
        self.assertEqual(1, len(pending))
        self.assertEqual("lftp_job_finished", pending[0]["details"]["reason"])
        self.assertNotIn(file_name, str(pending))
        self.assertEqual("default", Controller._model_record_visible_state(published))

        # Repeated empty polls leave the physical-proof owner pending, but do
        # not reintroduce fake retained progress or dispatch another Queue.
        ModelUpdater(controller).update()

        retired = controller._Controller__model.get_file(file_id)
        self.assertEqual(ModelFile.State.DEFAULT, retired.state)
        self.assertIsNone(retired.transferred_size)
        self.assertIsNone(retired.download_progress)
        self.assertEqual([], controller._Controller__active_downloading_file_names)
        self.assertIn((file_name, None, None), controller._Controller__pending_completion_file_names)
        self.assertNotIn(file_id, controller._Controller__persist.stopped_file_names)
        controller._Controller__lftp.queue.assert_not_called()
        self.assertEqual("default", Controller._model_record_visible_state(retired))

    def test_fresh_empty_lftp_poll_honors_explicit_stop_despite_valid_sidecar(self):
        file_name = "pending.bin"
        file_id = ModelFile.build_file_id(file_name, None)
        remote = SystemFile(file_name, 10, False)
        staged = SystemFile(file_name, 9, False, is_staging=True)
        staged.status_sidecar_ready = True
        running = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "",
        )
        running.total_transfer_state = LftpJobStatus.TransferState(9, 10, 90, 1, 0)
        builder = ModelBuilder()
        builder.set_remote_files([remote])
        builder.set_local_files([staged])
        builder.set_lftp_statuses([running])
        controller, _ = self._make_progressive_update_controller(
            None, local_scan=None, model_builder=builder, model=builder.build_model(),
        )
        controller._Controller__prev_downloading_file_names = {(file_name, None, None)}
        controller._Controller__is_explicitly_stopped = MagicMock(return_value=True)
        controller._Controller__persist.stopped_file_names.add(file_id)
        active_staged = SystemFile(file_name, 9, False, is_staging=True)
        active_staged.status_sidecar_ready = True
        controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [active_staged], is_scan_final=True,
        )
        controller._Controller__lftp.status.return_value = []

        ModelUpdater(controller).update()

        published = controller._Controller__model.get_file(file_id)
        self.assertNotEqual(ModelFile.State.DOWNLOADING, published.state)
        self.assertEqual([], controller._Controller__active_downloading_file_names)
        self.assertNotIn((file_name, None, None), controller._Controller__pending_completion_file_names)

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

    def test_pending_completion_floor_uses_raw_progress_not_display_union(self):
        file = ModelFile("sample", True)
        file.remote_size = 40
        file.transferred_size = 10
        file.download_progress = 25
        file.display_size_total = 100
        file.display_transferred_size = 70

        ModelUpdater._apply_pending_completion_progress_floor(
            file, {file.file_id}, previous_download_progress=25, previous_transferred_size=10,
        )

        self.assertEqual((40, 10, 25), (file.remote_size, file.transferred_size, file.download_progress))
        self.assertEqual((100, 70), (file.display_size_total, file.display_transferred_size))

    def test_pending_completion_floor_resynchronizes_display_union_from_raw_floor(self):
        file = ModelFile("sample", True)
        file.remote_size = 40
        file.local_size = 1
        file.transferred_size = 0
        file.download_progress = 0
        file.display_size_total = 100
        file.display_transferred_size = 60

        ModelUpdater._apply_pending_completion_progress_floor(
            file, {file.file_id}, previous_download_progress=98, previous_transferred_size=39,
        )

        self.assertEqual((40, 39, 98), (file.remote_size, file.transferred_size, file.download_progress))
        self.assertEqual((100, 99), (file.display_size_total, file.display_transferred_size))

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
