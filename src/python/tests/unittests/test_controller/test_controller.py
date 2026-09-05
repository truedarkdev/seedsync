from datetime import datetime, timedelta
import copy
import errno
import os
import json
import shutil
import stat
import threading
import time
import tempfile
import unittest
from concurrent.futures import CancelledError, Future
from pathlib import Path
from queue import Queue
from threading import Lock
from unittest.mock import ANY, MagicMock, mock_open, patch
from types import SimpleNamespace

import pexpect

from controller import AutoQueue, AutoQueuePersist, Controller, ControllerPersist, ModelBuilder
from controller.model_updater import (
    ModelUpdater, _PendingCompletionPublication, _ProgressiveScanAccumulator, _pop_scan_updates,
)
from controller.extract import ExtractRequest, ExtractStatus
from controller.validate import ValidateProcess
from controller.scan import MultiPathActiveScanner, ScannerProcess, ScannerResult
from controller.controller import (
    ControllerError, DeferredQueueIntent, DownloadStartLifecycleEntry, PendingQueueDispatch,
    _LftpOperation, _LftpQueueResult, _MoveMutationOutcome, _MoveMutationTracker,
    _lftp_executor_exception_family,
)
from controller.persist_keys import KEY_SEP, persist_key
from common import AppError, Config, PathPairError, PathPairManager
from common.performance_diagnostics import (
    DURATION_CONTROLLER_AUXILIARY_REAP,
    DURATION_CONTROLLER_CLEANUP_COMMANDS,
    DURATION_CONTROLLER_CONFIGURATION,
    DURATION_CONTROLLER_DIAGNOSTICS,
    DURATION_CONTROLLER_PROCESS,
    DURATION_CONTROLLER_PROCESS_COMMANDS,
    DURATION_CONTROLLER_PROPAGATE_EXCEPTIONS,
    DURATION_MODEL_UPDATE,
    PerformanceDiagnosticsCollector,
)
from common.exclude_patterns import ExactPathExclusion
from common.breadcrumb_trace import BreadcrumbTraceCollector, opaque_trace_correlation
from common.path_pair import PathPair
from lftp import Lftp, LftpError, LftpJobStatus, LftpJobStatusParserError
from model import ActiveProgressOverlay, IModelListener, Model, ModelDiff, ModelError, ModelFile
from system import SystemFile
from transfer import RcloneTransferError


class TestController(unittest.TestCase):
    def setUp(self):
        self.controller = Controller.__new__(Controller)
        self.controller.logger = MagicMock()
        self.controller._Controller__command_queue = Queue()
        self.controller._Controller__command_flow_lock = Lock()
        self.controller._Controller__process_wake_condition = threading.Condition()
        self.controller._Controller__process_wake_generation = 0
        self.controller._Controller__active_command_processes = []
        self.controller._Controller__active_downloading_file_names = []
        self.controller._Controller__active_extracting_file_names = []
        self.controller._Controller__prev_downloading_file_names = set()
        self.controller._Controller__pending_completion_file_names = set()
        self.controller._Controller__move_retry_due = {}
        self.controller._Controller__move_attempt_reservations = set()
        self.controller._Controller__move_attempt_lock = Lock()
        self.controller._Controller__deferred_move_file_ids = set()
        self.controller._Controller__pending_auto_purge_file_ids = set()
        self.controller._Controller__context = MagicMock()
        self.controller._Controller__context.status.controller = MagicMock()
        self.controller._Controller__context.status.server = SimpleNamespace(up=True, error_msg=None)
        self.controller._Controller__context.breadcrumb_trace = MagicMock()
        self.controller._Controller__context.path_pair_manager = None
        self.controller._Controller__context.config.lftp.local_path = "/local"
        self.controller._Controller__context.config.lftp.net_socket_buffer = ""
        self.controller._Controller__password = None
        self.controller._Controller__ssh_password = None
        self.controller._Controller__transfer_password = None
        self.controller._Controller__legacy_local_path = "/local"
        self.controller._Controller__legacy_remote_path = "/remote"
        self.controller._Controller__persist = MagicMock()
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__persist.downloaded_timestamps = {}
        self.controller._Controller__persist.extracted_file_names = set()
        self.controller._Controller__persist.stopped_file_names = set()
        self.controller._Controller__persist.move_failure_counts = {}
        self.controller._Controller__persist.final_move_succeeded_file_names = set()
        self.controller._Controller__model = MagicMock()
        self.controller._Controller__model_builder = MagicMock()
        self.controller._Controller__model_builder.has_changes.return_value = False
        self.controller._Controller__model_builder.has_complete_local_coverage.return_value = True
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        self.controller._Controller__model_builder.get_unresolved_staging_collision_file_ids.return_value = set()
        self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()
        self.controller._Controller__model_lock = MagicMock()
        self.controller._Controller__remote_delete_success_listeners = []
        self.controller._Controller__remote_delete_success_listeners_lock = Lock()
        self.controller._Controller__download_start_listeners = []
        self.controller._Controller__download_start_state = {}
        self.controller._Controller__download_start_lock = Lock()
        self.controller._Controller__path_pair_refresh_lock = Lock()
        self.controller._Controller__path_pair_refresh_requested = False
        self.controller._Controller__path_pair_refresh_generation = 0
        self.controller._Controller__path_pair_refresh_completed_generation = 0
        self.controller._Controller__transfer_lifecycle_epochs = {}
        self.controller._Controller__pending_extract_file_ids = set()
        self.controller._Controller__pending_validation_file_ids = set()
        self.controller._Controller__pending_command_dispatch_file_ids = set()
        self.controller._Controller__work_state_lock = threading.RLock()
        self.controller._Controller__path_pair_relocation_reservations = set()
        self.controller._Controller__path_pair_relocation_identities = {}
        self.controller._Controller__current_process_final_publication_file_ids = set()
        self.controller._Controller__reconciled_local_path_pair_ids = set()
        self.controller._Controller__reconciled_remote_path_pair_ids = set()
        self.controller._Controller__path_pair_runtime_error = None
        self.controller._Controller__lftp_reconfigure_lock = Lock()
        self.controller._Controller__lftp_reconfigure_requested = False
        self.controller._Controller__lftp = MagicMock()
        self.controller._Controller__lftp.net_socket_buffer = ""
        self.controller._Controller__active_scan_process = MagicMock()
        self.controller._Controller__local_scan_process = MagicMock()
        self.controller._Controller__remote_scan_process = MagicMock()
        self.controller._Controller__local_scan_process.generation = 0
        self.controller._Controller__remote_scan_process.generation = 0
        self.controller._Controller__local_scan_process.session_token = "local-test-session"
        self.controller._Controller__remote_scan_process.session_token = "remote-test-session"
        self.controller._Controller__active_scanner = MagicMock()
        self.controller._Controller__local_scanner = MagicMock()
        self.controller._Controller__remote_scanner = MagicMock()
        self.controller._Controller__extract_process = MagicMock()
        self.controller._Controller__validate_process = MagicMock()
        self.controller._Controller__extract_process.pid = None
        self.controller._Controller__validate_process.pid = None
        self.controller._Controller__extract_idle_deadline_monotonic = None
        self.controller._Controller__validate_idle_deadline_monotonic = None
        self.controller._Controller__mp_logger = MagicMock()
        self.controller._Controller__updater = MagicMock()
        self.controller._Controller__target_archive_trace_logger = MagicMock()
        self.controller._Controller__target_archive_trace_file_id = None
        self.controller._Controller__target_archive_trace_last_signature = None
        self.controller._Controller__temp_diag_file_id = None
        self.controller._Controller__temp_diag_last_signature = None
        self.controller._Controller__staging_path = "/local/incomplete"
        self.controller._Controller__explicit_staging_path_configured = False
        self.controller._Controller__reported_dead_workers = set()
        self.controller._Controller__path_pairs_by_id = {}
        self.controller._Controller__path_pair_staging_paths = {}
        self.controller._Controller__last_lftp_statuses = []
        self.controller._Controller__next_lftp_status_poll_at = None
        self.controller._Controller__lftp_idle_status_authoritative = False
        self.controller._Controller__lftp_status_poll_retry_seconds = 1
        self.controller._Controller__lftp_status_cache_expires_at = None
        self.controller._Controller__lftp_status_cache_max_age_seconds = 3
        self.controller._Controller__lftp_status_future_correlation = None
        self.controller._Controller__lftp_status_future_publication_epoch = None
        self.controller._Controller__startup_recovery_done = False
        self.controller._Controller__memory_monitor = MagicMock()
        self.controller._Controller__started = False
        self.controller._Controller__startup_failed = False

        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = None
        self.controller._Controller__extract_process.pop_completed.return_value = []
        self.controller._Controller__extract_process.pop_failed.return_value = []

    def _authorize_pending_move(self, path_pair_id=None):
        """Seed the current scan/status authority required by quiet moves."""
        self.controller._Controller__reconciled_local_path_pair_ids.add(path_pair_id)
        self.controller._Controller__reconciled_remote_path_pair_ids.add(path_pair_id)
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__lftp_status_poll_retry_active = False
        self.controller._Controller__lftp_idle_status_authoritative = True
        self.controller._Controller__last_lftp_statuses = []
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
        verified_identity = getattr(
            self.controller._Controller__model_builder,
            "has_verified_complete_staging_remote_identity",
            None,
        )
        if isinstance(verified_identity, MagicMock):
            verified_identity.return_value = True

    def test_owned_incomplete_transfer_requires_pending_directory_lineage(self):
        partial_root = ModelFile("sample", True)
        partial_root.remote_size = 200
        partial_root.local_size = 100
        partial_root.state = ModelFile.State.DEFAULT

        self.controller._Controller__pending_completion_file_names = {
            ("sample", None, None),
        }
        self.assertTrue(self.controller.is_owned_incomplete_transfer(partial_root))

        self.controller._Controller__pending_queue_dispatches = {
            partial_root.file_id: object(),
        }
        self.assertFalse(self.controller.is_owned_incomplete_transfer(partial_root))
        self.controller._Controller__pending_queue_dispatches.clear()

        # Completion (or an accepted replacement lifecycle) removes the
        # controller-owned retry identity; local presence alone is not enough.
        self.controller._Controller__pending_completion_file_names.clear()
        self.assertFalse(self.controller.is_owned_incomplete_transfer(partial_root))

    def test_owned_incomplete_transfer_rejects_files_stopped_subjects_and_active_roots(self):
        partial_file = ModelFile("sample.bin", False)
        partial_file.remote_size = 200
        partial_file.local_size = 100
        partial_file.state = ModelFile.State.DEFAULT
        self.controller._Controller__pending_completion_file_names = {
            ("sample.bin", None, None),
        }
        self.assertFalse(self.controller.is_owned_incomplete_transfer(partial_file))

        partial_root = ModelFile("sample", True)
        partial_root.remote_size = 200
        partial_root.local_size = 100
        partial_root.state = ModelFile.State.DEFAULT
        self.controller._Controller__pending_completion_file_names = {
            ("sample", None, None),
        }
        self.controller._Controller__persist.stopped_file_names = {partial_root.file_id}
        self.assertFalse(self.controller.is_owned_incomplete_transfer(partial_root))

        self.controller._Controller__persist.stopped_file_names.clear()
        partial_root.state = ModelFile.State.DOWNLOADING
        self.assertFalse(self.controller.is_owned_incomplete_transfer(partial_root))

    def _settle_collision_compare(self):
        future = getattr(self.controller, "_Controller__collision_compare_future", None)
        self.assertIsNotNone(future)
        future.result(timeout=5)

    def test_process_wait_notification_is_generation_safe(self):
        generation = self.controller.process_wake_generation()
        self.controller.wake_process()

        self.assertTrue(self.controller.wait_for_process_wake(generation, 0))
        self.assertEqual(generation + 1, self.controller.process_wake_generation())

    def test_idle_process_delay_uses_health_deadline_without_polling(self):
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__collision_compare_future = None
        self.controller._Controller__lftp_idle_status_authoritative = True

        self.assertFalse(self.controller.has_active_runtime_work())
        self.assertEqual(10.0, Controller._IDLE_HEALTH_INTERVAL_SECONDS)
        self.assertEqual(Controller._IDLE_HEALTH_INTERVAL_SECONDS, self.controller.next_process_delay_seconds())

    def test_active_transfer_process_delay_remains_100ms(self):
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__collision_compare_future = None
        self.controller._Controller__active_downloading_file_names = [("active.bin", None, None)]

        self.assertTrue(self.controller.has_active_runtime_work())
        self.assertEqual(Controller._ACTIVE_PROCESS_INTERVAL_SECONDS, self.controller.next_process_delay_seconds())

    def test_pending_scan_results_keep_active_cadence_until_bounded_drain_finishes(self):
        process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
        self.addCleanup(process.close_queues)
        process._ScannerProcess__queue.maxsize = 256
        for index in range(130):
            process._ScannerProcess__publish_result(ScannerResult(
                datetime.now(), [SystemFile("root-{}".format(index), index)],
                scanned_path_pair_ids={"pair"}, is_progress=True, is_scan_final=False,
            ))
        final = ScannerResult(
            datetime.now(), [SystemFile("final", 1)], scanned_path_pair_ids={"pair"},
            is_progress=True, is_full_snapshot=True, full_snapshot_path_pair_ids={"pair"},
        )
        process._ScannerProcess__publish_result(final)
        self.controller._Controller__active_scan_process = process
        self.controller._Controller__lftp_idle_status_authoritative = True
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__collision_compare_future = None

        self.assertTrue(self.controller.has_active_runtime_work())
        self.assertEqual(Controller._ACTIVE_PROCESS_INTERVAL_SECONDS, self.controller.next_process_delay_seconds())
        self.assertTrue(process.has_pending_results())

        first_batch = process.pop_results()
        self.assertEqual(128, len(first_batch))
        self.assertTrue(self.controller.has_active_runtime_work())
        self.assertEqual(Controller._ACTIVE_PROCESS_INTERVAL_SECONDS, self.controller.next_process_delay_seconds())
        second_batch = process.pop_results()
        self.assertIn(final, second_batch)
        self.assertFalse(process.has_pending_results())
        self.assertFalse(self.controller.has_active_runtime_work())
        self.assertEqual(Controller._IDLE_HEALTH_INTERVAL_SECONDS, self.controller.next_process_delay_seconds())

    def test_pending_results_from_each_scan_process_keep_scheduler_active(self):
        processes = []
        for attribute in (
                "_Controller__active_scan_process",
                "_Controller__local_scan_process",
                "_Controller__remote_scan_process"):
            process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
            self.addCleanup(process.close_queues)
            process._ScannerProcess__publish_result(ScannerResult(
                datetime.now(), [SystemFile(attribute, 1)], scanned_path_pair_ids={"pair"},
            ))
            setattr(self.controller, attribute, process)
            processes.append(process)
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__collision_compare_future = None
        self.controller._Controller__lftp_idle_status_authoritative = True

        self.assertTrue(self.controller.has_active_runtime_work())
        for index, process in enumerate(processes):
            self.assertIsNotNone(process.pop_latest_result())
            self.assertEqual(index < len(processes) - 1, self.controller.has_active_runtime_work())

    def test_pending_async_lftp_status_future_keeps_bounded_scheduler_cadence(self):
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__collision_compare_future = None
        self.controller._Controller__active_downloading_file_names = []
        self.controller._Controller__active_extracting_file_names = []
        pending_status = Future()
        self.controller._Controller__lftp_status_future = pending_status

        self.assertTrue(self.controller.has_active_runtime_work())
        self.assertEqual(
            Controller._ACTIVE_PROCESS_INTERVAL_SECONDS,
            self.controller.next_process_delay_seconds(),
        )
        self.assertEqual(
            Controller._ACTIVE_PROCESS_INTERVAL_SECONDS,
            self.controller.next_process_delay_seconds(),
        )

        pending_status.set_result(([], True))
        self.controller._Controller__lftp_status_future = None

    def test_pending_async_lftp_operations_keep_bounded_scheduler_cadence(self):
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__collision_compare_future = None
        self.controller._Controller__active_downloading_file_names = []
        self.controller._Controller__active_extracting_file_names = []
        self.controller._Controller__lftp_status_future = None
        self.controller._Controller__lftp_idle_status_authoritative = False
        self.controller._Controller__next_lftp_status_poll_at = None

        for action in ("queue", "stop", "reconfigure"):
            with self.subTest(action=action):
                pending_operation = Future()
                self.controller._Controller__lftp_operations = [
                    SimpleNamespace(action=action, future=pending_operation)
                ]
                self.assertTrue(self.controller.has_active_runtime_work())
                self.assertEqual(
                    Controller._ACTIVE_PROCESS_INTERVAL_SECONDS,
                    self.controller.next_process_delay_seconds(),
                )
                self.assertEqual(
                    Controller._ACTIVE_PROCESS_INTERVAL_SECONDS,
                    self.controller.next_process_delay_seconds(),
                )

                pending_operation.set_result(None)
                self.controller._Controller__drain_lftp_operations()
                self.assertFalse(self.controller.has_active_runtime_work())

    def test_async_lftp_queue_accepts_without_waiting_and_publishes_synthetic_status(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 100
        file.state = ModelFile.State.DEFAULT
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__lftp.backend_name = "lftp"
        queued = threading.Event()
        release = threading.Event()

        def blocking_queue(*_args, **_kwargs):
            queued.set()
            release.wait(2)

        self.controller._Controller__lftp.queue.side_effect = blocking_queue
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        started_at = time.monotonic()
        self.controller._Controller__process_commands()
        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertTrue(queued.wait(1))
        callback.on_success.assert_called_once_with()
        pending = self.controller._Controller__pending_queue_dispatches[file.file_id]
        self.assertEqual(file.name, pending.name)
        synthetic = self.controller._lftp_statuses_with_pending_dispatches([])
        self.assertEqual([file.file_id], [status.file_id for status in synthetic])
        release.set()
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def test_manual_queue_resumes_valid_sidecar_binding_once_after_idle_retirement(self):
        """A resumable DEFAULT row keeps the confirmed remote binding on Queue."""
        file = ModelFile("sample.bin", False)
        file.remote_size = 10
        file.state = ModelFile.State.DEFAULT
        file.path_pair_id = "pair-a"
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": PathPair("/remote/pair-a", "/local/pair-a", id="pair-a"),
        }
        self.controller._Controller__path_pair_staging_paths = {
            "pair-a": "/local/pair-a/incomplete",
        }
        self.controller._Controller__persist.resume_source_identities = {
            file.file_id: (10, 1),
        }
        self.controller._Controller__model_builder.get_remote_resume_source_identity.return_value = (10, 1)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            False,
            remote_base_dir_path="/remote/pair-a",
            local_base_dir_path="/local/pair-a/incomplete",
            allow_resume=True,
            expected_size=10,
        )

    def test_async_lftp_status_uses_one_inflight_future_then_completed_snapshot(self):
        self.controller._Controller__lftp.backend_name = "lftp"
        started = threading.Event()
        release = threading.Event()
        status = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "movie.mkv", "")

        def blocking_status():
            started.set()
            release.wait(2)
            return [status]

        self.controller._Controller__lftp.status.side_effect = blocking_status
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        poll_correlation = self.controller._Controller__lftp_status_poll_correlation
        self.assertIsNotNone(poll_correlation)
        self.assertRegex(poll_correlation, r"^lftp-poll:[0-9a-f]{16}$")
        self.assertTrue(started.wait(1))
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        self.assertEqual(poll_correlation, self.controller._Controller__lftp_status_poll_correlation)
        self.assertEqual(1, self.controller._Controller__lftp.status.call_count)
        observed_generation = self.controller.process_wake_generation()
        release.set()
        deadline = time.monotonic() + 1
        snapshot = None
        while snapshot is None and time.monotonic() < deadline:
            snapshot = self.controller._get_lftp_status_snapshot()
            if snapshot is None:
                time.sleep(0.01)
        self.assertEqual(([status], True), snapshot)
        self.assertEqual(poll_correlation, self.controller._Controller__lftp_status_poll_correlation)
        self.assertEqual(poll_correlation, self.controller._take_lftp_status_poll_correlation())
        self.assertIsNone(self.controller._take_lftp_status_poll_correlation())
        self.assertTrue(self.controller.wait_for_process_wake(observed_generation, 1))
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def test_async_lftp_status_forwards_opt_in_poll_correlation_to_real_lftp(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"transfer.lftp.status": "debug"}},
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        backend = Lftp.__new__(Lftp)
        backend.status = MagicMock(return_value=[])
        backend._Lftp__last_status_poll_healthy = True
        self.controller._Controller__lftp = backend

        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        correlation = self.controller._Controller__lftp_status_poll_correlation
        self.controller._Controller__lftp_executor.shutdown(wait=True)
        self.assertEqual(([], True), self.controller._get_lftp_status_snapshot())

        backend.status.assert_called_once_with(trace_poll_correlation=correlation)
        self.assertRegex(correlation, r"^lftp-poll:[0-9a-f]{16}$")

    def test_async_lftp_poll_trace_keeps_submitted_token_after_lifecycle_clear(self):
        self.controller._Controller__lftp.backend_name = "lftp"
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        started = threading.Event()
        release = threading.Event()

        def blocking_status():
            started.set()
            release.wait(2)
            return []

        self.controller._Controller__lftp.status.side_effect = blocking_status
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        submitted = self.controller._Controller__lftp_status_poll_correlation
        self.assertTrue(started.wait(1))
        # Queue/reconfigure invalidation may clear or replace the controller's
        # mutable correlation while the submitted PTY work remains running.
        self.controller._Controller__lftp_status_poll_correlation = "lftp-poll:fedcba9876543210"
        release.set()
        self.controller._Controller__lftp_executor.shutdown(wait=True)
        self.assertEqual(([], True), self.controller._get_lftp_status_snapshot())
        self.assertIsNone(self.controller._take_lftp_status_poll_correlation())

        spans = trace.snapshot()["progress_lineage"]["spans"]
        old = next(span for span in spans if span["correlation"] == submitted)
        self.assertEqual(["status_submit", "status_start", "status_finish"],
                         [step["phase"] for step in old["steps"]])
        self.assertFalse(any(span["correlation"] == "lftp-poll:fedcba9876543210" for span in spans))

    def test_async_lftp_poll_lineage_health_counts_two_completed_polls(self):
        self.controller._Controller__lftp.backend_name = "lftp"
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.status.return_value = []

        observed = []
        for _ in range(2):
            self.assertIsNone(self.controller._get_lftp_status_snapshot())
            correlation = self.controller._Controller__lftp_status_poll_correlation
            deadline = time.monotonic() + 1
            snapshot = None
            while snapshot is None and time.monotonic() < deadline:
                snapshot = self.controller._get_lftp_status_snapshot()
                if snapshot is None:
                    time.sleep(0.01)
            self.assertEqual(([], True), snapshot)
            observed.append(correlation)
            self.assertEqual(correlation, self.controller._take_lftp_status_poll_correlation())

        health = trace.snapshot()["progress_lineage_health"]
        self.assertEqual(2, len(trace.snapshot()["progress_lineage"]["spans"]))
        for phase in ("status_submit", "status_start", "status_finish"):
            self.assertEqual(2, health["phase_calls"][phase])
            self.assertEqual(2, health["phase_accepted"][phase])
        self.assertEqual(2, health["spans_created"])
        self.assertEqual(0, health["spans_evicted"])
        self.assertEqual(0, health["lineage_resets"])
        self.assertNotEqual(*observed)
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def test_async_queue_invalidates_completed_pre_queue_status_before_post_queue_poll(self):
        class TrackingFuture(Future):
            def __init__(self):
                super().__init__()
                self.result_calls = 0

            def result(self, timeout=None):
                self.result_calls += 1
                return super().result(timeout)

        file = ModelFile("fast-get.bin", False)
        file.remote_size = 100
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__lftp.backend_name = "lftp"
        stale_status = TrackingFuture()
        stale_status.set_result(([], True))
        self.controller._Controller__lftp_status_future = stale_status
        self.controller._Controller__lftp.status.return_value = []

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertIsNone(self.controller._Controller__lftp_status_future)
        self.assertEqual(0, stale_status.result_calls)
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        deadline = time.monotonic() + 1
        snapshot = None
        while snapshot is None and time.monotonic() < deadline:
            snapshot = self.controller._get_lftp_status_snapshot()
            if snapshot is None:
                time.sleep(0.01)
        self.assertEqual(([], True), snapshot)
        self.assertEqual(1, self.controller._Controller__lftp.status.call_count)
        self.assertEqual(0, stale_status.result_calls)
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def _assert_async_lftp_status_fence(self, transition):
        """A fenced status result cannot cross a controller lifecycle boundary."""
        class TrackingFuture(Future):
            def __init__(self):
                super().__init__()
                self.result_calls = 0

            def result(self, timeout=None):
                self.result_calls += 1
                return super().result(timeout)

        class DeferredExecutor:
            def __init__(self):
                self.submissions = []

            def submit(self, operation):
                future = TrackingFuture()
                self.submissions.append((operation, future))
                return future

            def run_next(self):
                operation, future = self.submissions.pop(0)
                try:
                    future.set_result(operation())
                except BaseException as error:
                    future.set_exception(error)
                return future

            def shutdown(self, **_kwargs):
                pass

        stale = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "stale.bin", "",
        )
        fresh = LftpJobStatus(
            2, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "fresh.bin", "",
        )
        executor = DeferredExecutor()
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__lftp.status.side_effect = [[stale], [fresh]]
        self.controller._Controller__lftp_executor = executor
        self.addCleanup(executor.shutdown)

        # Submit the old poll, then make it look like a running worker so a
        # lifecycle fence cannot cancel the future itself before it completes.
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        self.assertEqual(1, len(executor.submissions))
        stale_future = executor.submissions[0][1]
        self.assertTrue(stale_future.set_running_or_notify_cancel())

        transition()
        # Complete the old worker after the transition.  Its result is now
        # done, but the controller no longer retains the future reference.
        executor.run_next()

        # The completed pre-transition result is no longer visible to the
        # accessor used by ModelUpdater; it must submit a post-transition poll.
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        self.assertEqual(1, len(executor.submissions))
        self.assertEqual(0, stale_future.result_calls)

        fresh_future = executor.submissions[0][1]
        self.assertTrue(fresh_future.set_running_or_notify_cancel())
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        executor.run_next()
        self.assertEqual(([fresh], True), self.controller._get_lftp_status_snapshot())

    def test_async_lftp_status_reconfigure_fence_drops_completed_pre_transition_result(self):
        self._assert_async_lftp_status_fence(self.controller.request_lftp_reconfigure)

    def test_async_lftp_status_path_pair_refresh_fence_drops_completed_pre_transition_result(self):
        self.controller._Controller__started = False
        self.controller._Controller__cancel_and_settle_collision_claim_for_refresh = MagicMock(
            return_value=True,
        )
        self.controller._Controller__refresh_path_pair_runtime_state = MagicMock()
        self.controller._Controller__clear_path_pair_runtime_error = MagicMock()

        self._assert_async_lftp_status_fence(
            lambda: self.controller._Controller__apply_path_pair_refresh()
        )

    def test_async_lftp_status_shutdown_fence_drops_completed_pre_transition_result(self):
        self.controller._Controller__started = False
        self._assert_async_lftp_status_fence(self.controller.exit)

    def test_async_stop_discards_pre_stop_status_before_fresh_post_stop_poll(self):
        class TrackingFuture(Future):
            def __init__(self):
                super().__init__()
                self.result_calls = 0

            def result(self, timeout=None):
                self.result_calls += 1
                return super().result(timeout)

        class DeferredExecutor:
            def __init__(self):
                self.submissions = []

            def submit(self, operation):
                future = TrackingFuture()
                self.submissions.append((operation, future))
                return future

            def run_next(self):
                operation, future = self.submissions.pop(0)
                try:
                    future.set_result(operation())
                except BaseException as error:
                    future.set_exception(error)
                return future

            def shutdown(self, **_kwargs):
                pass

        file = ModelFile("stop-me.mkv", False)
        file.remote_size = 100
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__lftp.kill.return_value = True
        stale = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file.name, "",
        )
        executor = DeferredExecutor()
        self.controller._Controller__lftp.status.side_effect = [[stale], []]
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__lftp_executor = executor
        self.addCleanup(executor.shutdown)

        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        stale_future = executor.submissions[0][1]
        self.assertTrue(stale_future.set_running_or_notify_cancel())

        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        command.add_callback(callback)
        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
        self.assertIsNone(self.controller._Controller__lftp_status_future)

        # Let the old worker finish after Stop acceptance; its result must not
        # be consumed by the next model-updater status read.
        executor.run_next()
        self.assertEqual(0, stale_future.result_calls)
        self.assertIsNone(self.controller._get_lftp_status_snapshot())

        # The accepted Stop operation is queued ahead of the replacement poll.
        executor.run_next()
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        executor.run_next()
        self.assertEqual(([], True), self.controller._get_lftp_status_snapshot())

    def test_async_lftp_status_future_failure_becomes_bounded_unhealthy_snapshot(self):
        self.controller._Controller__lftp.backend_name = "lftp"
        failed_future = Future()
        failed_future.set_exception(RuntimeError("status worker failed"))
        self.controller._Controller__lftp_status_future = failed_future

        snapshot = self.controller._get_lftp_status_snapshot()

        self.assertEqual(([], False), snapshot)
        self.assertIsNone(self.controller._Controller__lftp_status_future)

    def test_lftp_executor_teardown_closes_pool_when_exit_submit_races(self):
        executor = MagicMock()
        executor.submit.side_effect = RuntimeError("executor already closed")
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__lftp_executor = executor
        self.controller._Controller__lftp_executor_closing = False
        self.controller._Controller__started = True

        self.controller.exit()

        executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        self.assertIsNone(self.controller._Controller__lftp_executor)

    def test_lftp_executor_forced_close_releases_blocked_exit(self):
        self.controller._Controller__started = True
        self.controller._Controller__lftp.backend_name = "lftp"
        self._set_exit_worker_processes_not_alive()
        release = threading.Event()
        started = threading.Event()

        def blocked_exit():
            started.set()
            release.wait(5)

        self.controller._Controller__lftp.exit.side_effect = blocked_exit
        self.controller._Controller__lftp.force_close.side_effect = release.set
        self.controller._Controller__ensure_lftp_executor()

        started_at = time.monotonic()
        self.controller.exit()

        self.assertLess(time.monotonic() - started_at, 3.5)
        self.assertTrue(started.is_set())
        self.controller._Controller__lftp.force_close.assert_called_once_with()
        self.assertIsNone(self.controller._Controller__lftp_executor)

    def test_lftp_executor_trace_is_explicit_opt_in_without_disabled_timing(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        executor = MagicMock()
        executor.submit.return_value = Future()
        self.controller._Controller__lftp_executor = executor
        operation = lambda: True

        with patch("controller.controller.secrets.token_hex") as token_hex:
            self.assertTrue(self.controller._Controller__submit_lftp_operation(
                "queue", operation, "sample.bin", 1,
            ))

        token_hex.assert_not_called()
        # Queue already wraps its backend callable for legacy outcome handling;
        # disabled executor tracing must not add the diagnostic wrapper.
        self.assertEqual("queue_operation", executor.submit.call_args.args[0].__name__)
        self.assertEqual([], [
            entry for entry in trace.snapshot()["entries"]
            if entry.get("category") == "transfer.lftp.executor"
        ])

    def test_lftp_executor_trace_records_local_lifecycle(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"transfer.lftp.executor": "info"}},
            max_entries=16,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        executor = MagicMock()
        future = Future()
        submitted = []
        executor.submit.side_effect = lambda operation: submitted.append(operation) or future
        self.controller._Controller__lftp_executor = executor

        with patch("controller.controller.secrets.token_hex", return_value="0123456789abcdef"):
            self.assertTrue(self.controller._Controller__submit_lftp_operation(
                "queue", lambda: True, "sample.bin", 1,
            ))
            submitted[0]()
            future.set_result(_LftpQueueResult(True, False))
            self.controller._Controller__drain_lftp_operations()

        entries = [
            entry for entry in trace.snapshot()["entries"]
            if entry.get("category") == "transfer.lftp.executor"
        ]
        self.assertEqual(
            ["enqueue_attempt", "enqueue_returned", "worker_enter", "worker_return", "harvest"],
            [entry["details"]["phase"] for entry in entries],
        )
        self.assertEqual({"lftp-executor:0123456789abcdef"}, {entry["corr_id"] for entry in entries})
        self.assertTrue(all("sample.bin" not in str(entry) for entry in entries))

    def test_lftp_executor_trace_records_submit_failure_and_worker_exception(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"transfer.lftp.executor": "info"}},
            max_entries=32,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        executor = MagicMock()
        executor.submit.side_effect = RuntimeError("executor unavailable")
        self.controller._Controller__lftp_executor = executor
        with patch("controller.controller.secrets.token_hex", return_value="1111111111111111"):
            self.assertFalse(self.controller._Controller__submit_lftp_operation("stop", lambda: True))

        failed = next(
            entry for entry in trace.snapshot()["entries"]
            if entry["details"]["phase"] == "enqueue_failed"
        )
        self.assertEqual("runtime", failed["details"]["exception_family"])

        future = Future()
        submitted = []
        executor.submit.side_effect = lambda operation: submitted.append(operation) or future
        self.controller._Controller__lftp_operations = []
        def fail_worker():
            raise LftpError("backend failure")
        with patch("controller.controller.secrets.token_hex", return_value="2222222222222222"):
            self.assertTrue(self.controller._Controller__submit_lftp_operation("stop", fail_worker))
            with self.assertRaises(LftpError):
                submitted[0]()
            future.set_exception(LftpError("backend failure"))
            self.controller._Controller__drain_lftp_operations()

        exception = next(
            entry for entry in trace.snapshot()["entries"]
            if entry["details"]["phase"] == "worker_exception" and entry["corr_id"].endswith("2222222222222222")
        )
        self.assertEqual("runtime", exception["details"]["exception_family"])

    def test_lftp_executor_trace_warning_policy_retains_only_failure_observation(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"transfer.lftp.executor": "warning"}},
            max_entries=8,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        executor = MagicMock()
        executor.submit.side_effect = RuntimeError("unavailable")
        self.controller._Controller__lftp_executor = executor

        with patch("controller.controller.secrets.token_hex", return_value="abcdef0123456789"):
            self.assertFalse(self.controller._Controller__submit_lftp_operation("stop", lambda: True))

        entries = trace.snapshot()["entries"]
        self.assertEqual(["enqueue_failed"], [entry["details"]["phase"] for entry in entries])
        self.assertEqual("runtime", entries[0]["details"]["exception_family"])

    def test_lftp_executor_worker_exception_families_use_only_the_approved_enum(self):
        errors = (
            (TimeoutError(), "timeout"),
            (pexpect.exceptions.TIMEOUT("timeout"), "timeout"),
            (OSError("os error"), "os_error"),
            (pexpect.exceptions.EOF("eof"), "eof"),
            (LftpJobStatusParserError("parser"), "parser"),
            (RuntimeError("runtime"), "runtime"),
            (CancelledError(), "cancelled"),
            (ValueError("unclassified"), "unknown"),
        )
        self.assertTrue(all(
            _lftp_executor_exception_family(error) == expected
            for error, expected in errors
        ))

    def test_lftp_executor_diagnostic_failure_preserves_worker_exception(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"transfer.lftp.executor": "info"}},
            max_entries=8,
        )
        trace.record = MagicMock(side_effect=RuntimeError("diagnostic sink failure"))
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        future = Future()
        submitted = []
        executor = MagicMock()
        executor.submit.side_effect = lambda operation: submitted.append(operation) or future
        self.controller._Controller__lftp_executor = executor
        expected = RuntimeError("worker failure")

        def fail_worker():
            raise expected

        with patch("controller.controller.secrets.token_hex", return_value="7777777777777777"):
            self.assertTrue(self.controller._Controller__submit_lftp_operation("status", fail_worker))

        with self.assertRaisesRegex(RuntimeError, "worker failure"):
            submitted[0]()

    def test_lftp_executor_trace_allows_fast_worker_observation_without_claiming_order(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"transfer.lftp.executor": "info"}},
            max_entries=16,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        completed = Future()

        def submit_and_run(operation):
            self.assertTrue(operation())
            completed.set_result(True)
            return completed

        executor = MagicMock()
        executor.submit.side_effect = submit_and_run
        self.controller._Controller__lftp_executor = executor
        with patch("controller.controller.secrets.token_hex", return_value="3333333333333333"), \
                patch("controller.controller.time.monotonic_ns", side_effect=(1, 2, 3, 4)):
            self.assertTrue(self.controller._Controller__submit_lftp_operation("stop", lambda: True))
            self.controller._Controller__drain_lftp_operations()

        phases = [
            entry["details"]["phase"] for entry in trace.snapshot()["entries"]
            if entry.get("category") == "transfer.lftp.executor"
        ]
        self.assertLess(phases.index("worker_enter"), phases.index("enqueue_returned"))
        self.assertIn("harvest", phases)

    def test_lftp_executor_trace_keeps_operations_and_retention_isolated(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"transfer.lftp.executor": "info"}},
            max_entries=4,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        futures = [Future(), Future()]
        submitted = []
        executor = MagicMock()
        executor.submit.side_effect = lambda operation: submitted.append(operation) or futures[len(submitted) - 1]
        self.controller._Controller__lftp_executor = executor

        with patch("controller.controller.secrets.token_hex", side_effect=("4444444444444444", "5555555555555555")):
            self.assertTrue(self.controller._Controller__submit_lftp_operation("queue", lambda: True, "first.bin", 1))
            self.assertTrue(self.controller._Controller__submit_lftp_operation("queue", lambda: True, "second.bin", 2))
            for operation, future in zip(submitted, futures):
                self.assertTrue(operation())
                future.set_result(_LftpQueueResult(True, False))
            self.controller._Controller__drain_lftp_operations()

        entries = [
            entry for entry in trace.snapshot()["entries"]
            if entry.get("category") == "transfer.lftp.executor"
        ]
        self.assertLessEqual(len(entries), 4)
        self.assertTrue(all("first.bin" not in str(entry) and "second.bin" not in str(entry) for entry in entries))
        correlations = {entry["corr_id"] for entry in entries}
        self.assertTrue(correlations <= {
            "lftp-executor:4444444444444444", "lftp-executor:5555555555555555",
        })

    def test_lftp_executor_trace_records_cancelled_status_harvest(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"transfer.lftp.executor": "info"}},
            max_entries=8,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        future = Future()
        executor = MagicMock()
        executor.submit.return_value = future
        self.controller._Controller__lftp_executor = executor

        with patch("controller.controller.secrets.token_hex", return_value="6666666666666666"):
            self.assertTrue(self.controller._Controller__submit_lftp_operation("status", lambda: []))
            self.assertTrue(future.cancel())

        harvest = next(
            entry for entry in trace.snapshot()["entries"]
            if entry["details"]["phase"] == "harvest"
        )
        self.assertEqual("cancelled", harvest["details"]["outcome"])
        self.assertEqual("cancelled", harvest["details"]["exception_family"])

    def test_failed_queue_completion_removes_current_pending_dispatch(self):
        file_id = ModelFile.build_file_id("movie.mkv", None)
        operation_future = Future()
        operation_future.set_exception(RuntimeError("queue failed"))
        self.controller._Controller__pending_queue_dispatches = {
            file_id: PendingQueueDispatch(0.0, "movie.mkv", None, False, 1),
        }
        self.controller._Controller__lftp_operation_sequences = {file_id: 1}
        self.controller._Controller__lftp_operations = [
            SimpleNamespace(
                action="queue",
                future=operation_future,
                file_id=file_id,
                operation_sequence=1,
            )
        ]

        self.controller._Controller__drain_lftp_operations()

        self.assertNotIn(file_id, self.controller._Controller__pending_queue_dispatches)

    def test_stale_stop_failure_cannot_clear_newer_stopped_intent(self):
        file_id = ModelFile.build_file_id("movie.mkv", None)
        operation_future = Future()
        operation_future.set_exception(RuntimeError("stale stop failed"))
        self.controller._Controller__persist.stopped_file_names.add(file_id)
        self.controller._Controller__lftp_operation_sequences = {file_id: 2}
        self.controller._Controller__lftp_operations = [
            SimpleNamespace(
                action="stop",
                future=operation_future,
                file_id=file_id,
                operation_sequence=1,
            )
        ]

        self.controller._Controller__drain_lftp_operations()

        self.assertIn(file_id, self.controller._Controller__persist.stopped_file_names)

    def test_failed_stop_completion_restores_prior_pending_queue_intent(self):
        file_id = ModelFile.build_file_id("movie.mkv", None)
        prior_dispatch = PendingQueueDispatch(0.0, "movie.mkv", None, False, 1)
        operation_future = Future()
        operation_future.set_exception(RuntimeError("stop failed"))
        self.controller._Controller__persist.stopped_file_names.add(file_id)
        self.controller._Controller__lftp_operation_sequences = {file_id: 2}
        self.controller._Controller__lftp_operations = [
            SimpleNamespace(
                action="stop",
                future=operation_future,
                file_id=file_id,
                operation_sequence=2,
                pending_dispatch=prior_dispatch,
            )
        ]

        self.controller._Controller__drain_lftp_operations()

        self.assertNotIn(file_id, self.controller._Controller__persist.stopped_file_names)
        self.assertIs(prior_dispatch, self.controller._Controller__pending_queue_dispatches[file_id])

    def test_stale_queue_and_current_stop_failures_do_not_restore_synthetic_queue(self):
        file_id = ModelFile.build_file_id("movie.mkv", None)
        prior_dispatch = PendingQueueDispatch(0.0, "movie.mkv", None, False, 1)
        queue_future = Future()
        queue_future.set_exception(RuntimeError("queue failed"))
        stop_future = Future()
        stop_future.set_exception(RuntimeError("stop failed"))
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__persist.stopped_file_names.add(file_id)
        self.controller._Controller__lftp_operation_sequences = {file_id: 2}
        self.controller._Controller__lftp_operations = [
            SimpleNamespace(
                action="queue",
                future=queue_future,
                file_id=file_id,
                operation_sequence=1,
            ),
            SimpleNamespace(
                action="stop",
                future=stop_future,
                file_id=file_id,
                operation_sequence=2,
                pending_dispatch=prior_dispatch,
                download_start_lifecycle_before=None,
            ),
        ]

        self.controller._Controller__drain_lftp_operations()

        self.assertNotIn(file_id, self.controller._Controller__pending_queue_dispatches)

    def test_repeated_queue_failures_keep_one_failed_sequence_per_file(self):
        file_id = ModelFile.build_file_id("movie.mkv", None)
        self.controller._Controller__lftp_failed_operation_sequences = set()
        for sequence in range(1, 21):
            operation_future = Future()
            operation_future.set_exception(RuntimeError("queue failed"))
            self.controller._Controller__lftp_operation_sequences = {file_id: sequence}
            self.controller._Controller__lftp_operations = [
                SimpleNamespace(
                    action="queue",
                    future=operation_future,
                    file_id=file_id,
                    operation_sequence=sequence,
                )
            ]

            self.controller._Controller__drain_lftp_operations()

        self.assertEqual(
            {(file_id, 20)},
            self.controller._Controller__lftp_failed_operation_sequences,
        )

    def test_async_stop_failure_restores_eligible_start_lifecycle_for_running_status(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 100
        file.state = ModelFile.State.DOWNLOADING
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__lftp.kill.return_value = False
        self.controller._Controller__pending_queue_dispatches = {}
        listener = MagicMock()
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._Controller__pending_queue_dispatches[file.file_id] = PendingQueueDispatch(
            0.0, file.name, file.path_pair_id, file.is_dir, 1
        )

        self.controller.queue_command(
            Controller.Command(Controller.Command.Action.STOP, file.file_id)
        )
        self.controller._Controller__process_commands()
        operation = self.controller._Controller__lftp_operations[0]
        operation.future.result(timeout=1)
        self.controller._Controller__drain_lftp_operations()

        running = LftpJobStatus(
            1,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            file.name,
            "",
        )
        self.controller._confirm_fresh_healthy_download_starts([running])
        self.controller._confirm_fresh_healthy_download_starts([running])

        listener.assert_called_once_with(file)
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def test_async_queue_failure_clears_lifecycle_so_retry_can_notify_once(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 100
        file.state = ModelFile.State.DEFAULT
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__lftp.queue.side_effect = [
            RuntimeError("queue failed"),
            None,
        ]
        listener = MagicMock()
        self.controller.add_download_start_listener(listener)

        self.controller.queue_command(
            Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        )
        self.controller._Controller__process_commands()
        first_operation = self.controller._Controller__lftp_operations[0]
        with self.assertRaises(RuntimeError):
            first_operation.future.result(timeout=1)
        self.controller._Controller__drain_lftp_operations()
        self.assertNotIn(file.file_id, self.controller._Controller__download_start_state)

        self.controller.queue_command(
            Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        )
        self.controller._Controller__process_commands()
        second_operation = self.controller._Controller__lftp_operations[0]
        second_operation.future.result(timeout=1)
        self.controller._Controller__drain_lftp_operations()

        running = LftpJobStatus(
            2,
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            file.name,
            "",
        )
        self.controller._confirm_fresh_healthy_download_starts([running])
        self.controller._confirm_fresh_healthy_download_starts([running])

        listener.assert_called_once_with(file)
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def test_stale_queue_failure_cannot_remove_newer_pending_dispatch(self):
        file_id = ModelFile.build_file_id("movie.mkv", None)
        operation_future = Future()
        operation_future.set_exception(RuntimeError("stale queue failed"))
        newer_lifecycle = DownloadStartLifecycleEntry("eligible", None, datetime.now())
        self.controller._Controller__download_start_state[file_id] = newer_lifecycle
        self.controller._Controller__pending_queue_dispatches = {
            file_id: PendingQueueDispatch(0.0, "movie.mkv", None, False, 2),
        }
        self.controller._Controller__lftp_operation_sequences = {file_id: 2}
        self.controller._Controller__lftp_operations = [
            SimpleNamespace(
                action="queue",
                future=operation_future,
                file_id=file_id,
                operation_sequence=1,
            )
        ]

        self.controller._Controller__drain_lftp_operations()

        self.assertEqual(
            2,
            self.controller._Controller__pending_queue_dispatches[file_id].operation_sequence,
        )
        self.assertIs(newer_lifecycle, self.controller._Controller__download_start_state[file_id])

    def _configure_real_model_autoqueue_pipeline(self, pair: PathPair, auto_delete_remote: bool = False):
        """Use production ModelUpdater/AutoQueue wiring, not listener mocks."""
        config = Config()
        config.autoqueue.enabled = True
        config.autoqueue.patterns_only = False
        config.autoqueue.auto_extract = False
        config.autoqueue.auto_delete_remote = auto_delete_remote
        self.controller._Controller__context.config = config
        self.controller._Controller__context.path_pair_manager = MagicMock()
        self.controller._Controller__context.path_pair_manager.get_enabled_pairs.return_value = [pair]
        self.controller._Controller__path_pairs_by_id = {pair.id: pair}
        self.controller._Controller__model = Model()
        self.controller._Controller__model.set_base_logger(self.controller.logger)
        self.controller._Controller__model_builder = ModelBuilder()
        self.controller._Controller__model_builder.set_base_logger(self.controller.logger)
        self.controller._Controller__model_lock = threading.RLock()
        self.controller._Controller__updater = ModelUpdater(self.controller)
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__validate_process.pop_latest_statuses.return_value = None
        auto_queue = AutoQueue(self.controller._Controller__context, AutoQueuePersist(), self.controller)
        return self.controller._Controller__updater, auto_queue

    @staticmethod
    def _scan_result(files: list[SystemFile], pair_id: str, failed: bool = False) -> ScannerResult:
        return ScannerResult(datetime.now(), files, scanned_path_pair_ids={pair_id}, failed=failed)

    @staticmethod
    def _pair_system_file(name: str, size: int, pair_id: str) -> SystemFile:
        system_file = SystemFile(name, size, False)
        system_file.path_pair_id = pair_id
        return system_file

    def test_record_download_completion_backfills_without_replacing_start_timestamp(self):
        self.controller._Controller__persist.downloaded_timestamps = {}
        file = ModelFile("same.mkv", False)
        file.path_pair_id = "movies"
        first = datetime(2026, 1, 1, 0, 0, 1)

        with patch.object(Controller, "_download_timestamp_clock", return_value=first) as clock:
            self.controller._record_download_completion(file)
            first_timestamp = self.controller._Controller__persist.downloaded_timestamps[file.file_id]
            self.controller._record_download_completion(file)

        self.assertEqual(first.timestamp(), first_timestamp)
        self.assertEqual(first.timestamp(), self.controller._Controller__persist.downloaded_timestamps[file.file_id])
        self.controller._Controller__model_builder.set_downloaded_timestamps.assert_called_with(
            self.controller._Controller__persist.downloaded_timestamps
        )
        clock.assert_called_once_with()

    def test_record_download_start_replaces_prior_timestamp_by_canonical_id(self):
        file = ModelFile("same.mkv", False)
        file.path_pair_id = "movies"
        self.controller._Controller__persist.downloaded_timestamps = {file.file_id: 100.0}
        started_at = datetime(2026, 1, 1, 0, 0, 2)

        with patch.object(Controller, "_download_timestamp_clock", return_value=started_at):
            self.controller._record_download_start(file)

        self.assertEqual(
            {file.file_id: started_at.timestamp()},
            self.controller._Controller__persist.downloaded_timestamps,
        )

    def test_download_start_lifecycle_confirms_running_once(self):
        file = ModelFile("release", True)
        listener = MagicMock()
        self.controller._Controller__model.get_file.return_value = file
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        queued = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file.name, "")
        running = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file.name, "")

        started_at = datetime(2026, 1, 1, 0, 0, 2)
        with patch.object(Controller, "_download_timestamp_clock", return_value=started_at):
            self.controller._confirm_fresh_healthy_download_starts([queued])
            self.controller._confirm_fresh_healthy_download_starts([running])
            self.controller._confirm_fresh_healthy_download_starts([running])

        listener.assert_called_once()
        self.assertEqual("notified", self.controller._Controller__download_start_state[file.file_id].state)
        self.assertEqual(
            {file.file_id: started_at.timestamp()},
            self.controller._Controller__persist.downloaded_timestamps,
        )

    def test_download_start_lifecycle_stop_suppresses_resume(self):
        file = ModelFile("release", False)
        listener = MagicMock()
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__persist.downloaded_timestamps = {file.file_id: 100.0}
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._Controller__suppress_download_start_lifecycle(file.file_id)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        running = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file.name, "")

        self.controller._confirm_fresh_healthy_download_starts([running])

        listener.assert_not_called()
        self.assertEqual("suppressed", self.controller._Controller__download_start_state[file.file_id].state)
        self.assertEqual(
            {file.file_id: 100.0}, self.controller._Controller__persist.downloaded_timestamps
        )

    def test_download_start_lifecycle_clear_allows_new_lifecycle(self):
        file = ModelFile("release", False)
        listener = MagicMock()
        self.controller._Controller__model.get_file.return_value = file
        self.controller.add_download_start_listener(listener)
        running = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file.name, "")

        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._confirm_fresh_healthy_download_starts([running])
        self.controller._clear_download_start_lifecycle(file.file_id)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._confirm_fresh_healthy_download_starts([running])

        self.assertEqual(2, listener.call_count)

    def test_download_start_lifecycle_keeps_path_pairs_independent(self):
        movies = ModelFile("release", True)
        movies.path_pair_id = "movies"
        tv = ModelFile("release", True)
        tv.path_pair_id = "tv"
        listener = MagicMock()
        self.controller._Controller__model.get_file.side_effect = lambda file_id: {
            movies.file_id: movies,
            tv.file_id: tv,
        }[file_id]
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(movies.file_id)
        self.controller._Controller__arm_download_start_lifecycle(tv.file_id)
        movies_status = LftpJobStatus(
            0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, movies.name, ""
        )
        movies_status.path_pair_id = movies.path_pair_id

        self.controller._confirm_fresh_healthy_download_starts([movies_status])

        listener.assert_called_once()
        self.assertEqual("notified", self.controller._Controller__download_start_state[movies.file_id].state)
        self.assertEqual("eligible", self.controller._Controller__download_start_state[tv.file_id].state)

    def test_download_start_completion_clears_terminal_states_without_retaining_completed_entries(self):
        for index in range(250):
            file_id = "completed-{}".format(index)
            self.controller._Controller__download_start_state[file_id] = DownloadStartLifecycleEntry(
                "notified" if index % 2 == 0 else "suppressed", None, datetime.now()
            )
            self.controller._complete_download_start_lifecycle(file_id)

        self.assertEqual({}, self.controller._Controller__download_start_state)

    def test_authoritative_scan_prunes_absent_legacy_entries_but_preserves_remote_present(self):
        transitioned_at = datetime.now()
        self.controller._Controller__download_start_state = {
            "absent": DownloadStartLifecycleEntry("eligible", None, transitioned_at),
            "stopped-absent": DownloadStartLifecycleEntry("suppressed", None, transitioned_at),
            "present": DownloadStartLifecycleEntry("eligible", None, transitioned_at),
        }

        self.controller._prune_download_start_lifecycles(
            transitioned_at + timedelta(seconds=1),
            {None},
            {"present"},
            set(),
        )

        self.assertEqual({"present"}, set(self.controller._Controller__download_start_state))

    def test_authoritative_scan_respects_timestamp_scope_and_protected_ids(self):
        transitioned_at = datetime.now()
        self.controller._Controller__download_start_state = {
            "legacy-old-scan": DownloadStartLifecycleEntry("eligible", None, transitioned_at),
            ModelFile.build_file_id("live", "movies"): DownloadStartLifecycleEntry(
                "eligible", "movies", transitioned_at
            ),
            ModelFile.build_file_id("disabled", "disabled"): DownloadStartLifecycleEntry(
                "eligible", "disabled", transitioned_at
            ),
            ModelFile.build_file_id("protected", "movies"): DownloadStartLifecycleEntry(
                "suppressed", "movies", transitioned_at
            ),
        }

        self.controller._prune_download_start_lifecycles(
            transitioned_at - timedelta(seconds=1),
            {None, "movies"},
            set(),
            set(),
        )
        self.controller._prune_download_start_lifecycles(
            transitioned_at + timedelta(seconds=1),
            {"movies"},
            set(),
            {ModelFile.build_file_id("protected", "movies")},
        )

        self.assertEqual(
            {
                "legacy-old-scan",
                ModelFile.build_file_id("disabled", "disabled"),
                ModelFile.build_file_id("protected", "movies"),
            },
            set(self.controller._Controller__download_start_state),
        )

    def test_authoritative_scan_bulk_prunes_absent_and_keeps_live_or_protected(self):
        transitioned_at = datetime.now()
        absent = {"absent-{}".format(index) for index in range(200)}
        live = {"live-{}".format(index) for index in range(25)}
        protected = {"protected-{}".format(index) for index in range(25)}
        self.controller._Controller__download_start_state = {
            file_id: DownloadStartLifecycleEntry("eligible", None, transitioned_at)
            for file_id in absent | live | protected
        }

        self.controller._prune_download_start_lifecycles(
            transitioned_at + timedelta(seconds=1), {None}, live, protected
        )

        self.assertEqual(live | protected, set(self.controller._Controller__download_start_state))

    def test_delete_command_snapshot_protects_active_queued_and_deferred_identities(self):
        active_command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "active")
        queued_command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, "queued")
        deferred_command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "deferred")
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                active_command, "active", "active", MagicMock(), MagicMock(), True
            )
        ]
        self.controller._Controller__command_queue.put(queued_command)
        self.controller._Controller__deferred_delete_command_refs = [deferred_command]

        protected = self.controller._snapshot_delete_command_file_ids()

        self.assertEqual({"active", "queued", "deferred"}, protected)

    def _set_exit_worker_processes_not_alive(self):
        for process in (
            self.controller._Controller__active_scan_process,
            self.controller._Controller__local_scan_process,
            self.controller._Controller__remote_scan_process,
            self.controller._Controller__extract_process,
            self.controller._Controller__validate_process,
        ):
            process.is_alive.return_value = False

    def _assert_exit_teardown(self, *, still_alive_processes=None, active_scanner_closed=True):
        join_timeout = Controller._Controller__JOIN_TIMEOUT_IN_SECS
        still_alive_processes = set() if still_alive_processes is None else set(still_alive_processes)
        for process in (
            self.controller._Controller__active_scan_process,
            self.controller._Controller__local_scan_process,
            self.controller._Controller__remote_scan_process,
            self.controller._Controller__extract_process,
            self.controller._Controller__validate_process,
        ):
            if process.pid is None:
                process.terminate.assert_not_called()
                process.join.assert_not_called()
                process.close_queues.assert_called_once_with()
                continue
            process.terminate.assert_called_once_with()
            process.join.assert_called_once_with(join_timeout)
            if process in still_alive_processes:
                process.close_queues.assert_not_called()
            else:
                process.close_queues.assert_called_once_with()
        if active_scanner_closed:
            self.controller._Controller__active_scanner.close.assert_called_once_with()
        else:
            self.controller._Controller__active_scanner.close.assert_not_called()
        self.controller._Controller__mp_logger.stop.assert_called_once_with()
        self.assertFalse(self.controller._Controller__started)
        self.assertFalse(self.controller._Controller__startup_failed)

    def _make_startup_context(
        self,
        *,
        local_path,
        remote_path="/remote",
        path_pair_manager=None,
        local_path_to_scanfs="/scanfs",
        extract_path="/extract",
        use_local_path_as_extract_path=False,
        remote_username="user",
        remote_password="password",
        remote_python_path="python3",
        use_ssh_key=False,
        verbose=False,
        auto_delete_remote=False,
        transfer_backend="lftp",
        protocol="sftp",
        remote_ftp_port=21,
        ftp_ssl_verify_certificate=True,
    ):
        return SimpleNamespace(
            logger=MagicMock(),
            web_access_logger=MagicMock(),
            config=SimpleNamespace(
                lftp=SimpleNamespace(
                    transfer_backend=transfer_backend,
                    remote_address="remote.server.com",
                    remote_username=remote_username,
                    remote_password=remote_password,
                    remote_port=22,
                    remote_path=remote_path,
                    local_path=local_path,
                    remote_path_to_scan_script="/scanfs",
                    remote_python_path=remote_python_path,
                    use_ssh_key=use_ssh_key,
                    num_max_parallel_downloads=1,
                    num_max_parallel_files_per_download=1,
                    num_max_connections_per_root_file=1,
                    num_max_connections_per_dir_file=1,
                    num_max_total_connections=1,
                    use_temp_file=False,
                    rate_limit=None,
                    net_socket_buffer="8M",
                    staging_path=None,
                    protocol=protocol,
                    remote_ftp_port=remote_ftp_port,
                    ftp_ssl_verify_certificate=ftp_ssl_verify_certificate,
                ),
                controller=SimpleNamespace(
                    interval_ms_remote_scan=1,
                    interval_ms_local_scan=1,
                    interval_ms_downloading_scan=1,
                    extract_path=extract_path,
                    use_local_path_as_extract_path=use_local_path_as_extract_path,
                    managed_extract_folders_enabled=True,
                ),
                general=SimpleNamespace(verbose=verbose),
                autoqueue=SimpleNamespace(
                    auto_delete_remote=auto_delete_remote,
                    enabled=False,
                    patterns_only=False,
                    auto_extract=False,
                ),
            ),
            args=SimpleNamespace(local_path_to_scanfs=local_path_to_scanfs),
            status=SimpleNamespace(
                server=SimpleNamespace(up=True, error_msg=None),
                controller=SimpleNamespace(),
            ),
            path_pair_manager=path_pair_manager,
            breadcrumb_trace=MagicMock(
                create_emitter=MagicMock(return_value=MagicMock())
            ),
        )

    def test_constructor_reports_missing_startup_fields_in_aggregate(self):
        context = self._make_startup_context(local_path=None)

        controller = Controller(context, ControllerPersist())

        self.assertFalse(controller._Controller__started)
        self.assertIsNotNone(controller._Controller__startup_validation_error)
        self.assertIn("Lftp.local_path", controller._Controller__startup_validation_error)
        self.assertEqual(controller._Controller__startup_validation_error, context.status.server.error_msg)
        self.assertEqual([], controller.get_model_files())

        with self.assertRaises(ControllerError) as error:
            controller.start()

        self.assertIn("Lftp.local_path", str(error.exception))

    def test_constructor_reports_missing_use_local_path_as_extract_path_in_aggregate(self):
        context = self._make_startup_context(
            local_path="/local",
            use_local_path_as_extract_path=None,
        )

        controller = Controller(context, ControllerPersist())

        self.assertFalse(controller._Controller__started)
        self.assertIsNotNone(controller._Controller__startup_validation_error)
        self.assertIn(
            "Controller.use_local_path_as_extract_path",
            controller._Controller__startup_validation_error,
        )
        self.assertEqual(controller._Controller__startup_validation_error, context.status.server.error_msg)

        with self.assertRaises(ControllerError) as error:
            controller.start()

        self.assertIn("Controller.use_local_path_as_extract_path", str(error.exception))

    def test_constructor_reports_missing_extract_path_when_local_path_is_not_extract_path(self):
        context = self._make_startup_context(
            local_path="/local",
            extract_path="",
            use_local_path_as_extract_path=False,
        )

        controller = Controller(context, ControllerPersist())

        self.assertFalse(controller._Controller__started)
        self.assertIsNotNone(controller._Controller__startup_validation_error)
        self.assertIn("Controller.extract_path", controller._Controller__startup_validation_error)
        self.assertEqual(controller._Controller__startup_validation_error, context.status.server.error_msg)

        with self.assertRaises(ControllerError) as error:
            controller.start()

        self.assertIn("Controller.extract_path", str(error.exception))

    def test_constructor_allows_empty_extract_path_when_local_path_is_extract_path(self):
        context = self._make_startup_context(
            local_path="/local",
            extract_path="",
            use_local_path_as_extract_path=True,
        )

        with patch("controller.controller.create_transfer_backend") as mock_create_transfer_backend:
            mock_backend = MagicMock()
            mock_create_transfer_backend.return_value = mock_backend
            controller = Controller(context, ControllerPersist())

        self.assertIsNone(controller._Controller__startup_validation_error)
        self.assertIs(controller._Controller__lftp, mock_backend)

    def test_constructor_wires_shared_performance_diagnostics_to_model_builder(self):
        context = self._make_startup_context(local_path="/local")
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        context.performance_diagnostics = diagnostics

        with patch("controller.controller.create_transfer_backend") as mock_create_transfer_backend:
            mock_create_transfer_backend.return_value = MagicMock()
            controller = Controller(context, ControllerPersist())

        self.assertIs(
            diagnostics,
            controller._Controller__model_builder._ModelBuilder__performance_diagnostics,
        )

    def test_constructor_passes_sftp_defaults_to_lftp(self):
        context = self._make_startup_context(local_path="/local")

        with patch("controller.controller.create_transfer_backend") as mock_create_transfer_backend:
            mock_backend = MagicMock()
            mock_create_transfer_backend.return_value = mock_backend
            controller = Controller(context, ControllerPersist())

        mock_create_transfer_backend.assert_called_once_with(
            context.config.lftp,
            "password",
            "password",
        )
        self.assertEqual("password", controller._Controller__ssh_password)
        self.assertEqual("password", controller._Controller__transfer_password)

    def test_constructor_wires_lftp_trace_through_queue_sidecar_validation(self):
        context = self._make_startup_context(local_path="/local")
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        context.breadcrumb_trace.create_emitter.return_value = trace

        backend = Lftp.__new__(Lftp)
        backend.logger = MagicMock()
        backend._Lftp__job_status_parser = MagicMock()
        backend._Lftp__path_pairs_by_id = {}
        backend._Lftp__base_remote_dir_path = "/remote"
        backend._Lftp__base_local_dir_path = "/local"
        backend._Lftp__run_command = MagicMock(return_value="")
        backend._Lftp__pending_error = None
        backend._Lftp__last_command_timed_out = False
        backend._Lftp__last_status_poll_healthy = True
        backend._Lftp__breadcrumb_trace = None

        with patch("controller.controller.create_transfer_backend", return_value=backend):
            Controller(context, ControllerPersist())

        with tempfile.TemporaryDirectory() as local_dir:
            backend.queue("sample.bin", False, local_base_dir_path=local_dir)

        trace.record.assert_called_once()
        self.assertEqual(
            "missing",
            trace.record.call_args.args[2]["classification"],
        )
        trace.is_effectively_enabled.assert_called_once_with("lftp.sidecar", "info")

    @patch("controller.controller.create_transfer_backend")
    def test_constructor_uses_rclone_backend_factory_when_selected(self, mock_create_transfer_backend):
        mock_backend = MagicMock()
        mock_backend.backend_name = "rclone"
        mock_create_transfer_backend.return_value = mock_backend
        context = self._make_startup_context(local_path="/local", transfer_backend="rclone", protocol="ftps")

        controller = Controller(context, ControllerPersist())

        mock_create_transfer_backend.assert_called_once_with(
            context.config.lftp,
            controller._Controller__transfer_password,
            controller._Controller__ssh_password,
        )
        self.assertIs(controller._Controller__lftp, mock_backend)

    @patch("controller.controller.create_transfer_backend", side_effect=RcloneTransferError("rclone missing from PATH"))
    def test_constructor_reports_rclone_backend_startup_failure(self, _mock_create_transfer_backend):
        context = self._make_startup_context(local_path="/local", transfer_backend="rclone")

        controller = Controller(context, ControllerPersist())

        self.assertFalse(controller._Controller__started)
        self.assertEqual("rclone missing from PATH", controller._Controller__startup_validation_error)
        self.assertFalse(context.status.server.up)
        self.assertEqual("rclone missing from PATH", context.status.server.error_msg)

    def test_constructor_passes_ftps_transfer_password_without_forcing_ssh_password(self):
        context = self._make_startup_context(
            local_path="/local",
            use_ssh_key=True,
            protocol="ftps",
            remote_ftp_port=2121,
            ftp_ssl_verify_certificate=True,
        )

        with patch("controller.controller.create_transfer_backend") as mock_create_transfer_backend:
            mock_backend = MagicMock()
            mock_create_transfer_backend.return_value = mock_backend
            controller = Controller(context, ControllerPersist())

        mock_create_transfer_backend.assert_called_once_with(
            context.config.lftp,
            "password",
            None,
        )
        self.assertIsNone(controller._Controller__ssh_password)
        self.assertEqual("password", controller._Controller__transfer_password)

    @patch("controller.controller.RemoteScanner")
    def test_build_remote_scanner_passes_remote_python_path(self, mock_remote_scanner):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        self.controller._Controller__context.config = SimpleNamespace(
            lftp=SimpleNamespace(
                remote_address="remote.server.com",
                remote_username="user",
                remote_password="password",
                remote_port=22,
                remote_path_to_scan_script="/scanfs",
                remote_python_path="/opt/python/bin/python3",
            )
        )
        self.controller._Controller__context.args = SimpleNamespace(local_path_to_scanfs="/local-scanfs")
        self.controller._Controller__context.performance_diagnostics = diagnostics
        path_pair = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/local/movies",
            enabled=True,
            auto_queue=False,
        )

        self.controller._Controller__build_remote_scanner([path_pair])

        mock_remote_scanner.assert_called_once_with(
            remote_address="remote.server.com",
            remote_username="user",
            remote_password=None,
            remote_port=22,
            remote_path_to_scan="/remote/movies",
            local_path_to_scan_script="/local-scanfs",
            remote_path_to_scan_script="/scanfs",
            remote_python_path="/opt/python/bin/python3",
            path_pair_id="movies",
            path_pair_name="Movies",
            performance_diagnostics=diagnostics,
        )

    def test_build_remote_scanner_wires_shared_diagnostics_for_enabled_pairs(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        scanner_a = MagicMock(name="scanner_a")
        scanner_b = MagicMock(name="scanner_b")
        pair_a = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/local/movies",
            enabled=True,
            auto_queue=False,
        )
        pair_b = PathPair(
            id="tv",
            name="TV",
            remote_path="/remote/tv",
            local_path="/local/tv",
            enabled=True,
            auto_queue=False,
        )
        self.controller._Controller__context.config = SimpleNamespace(
            lftp=SimpleNamespace(
                remote_address="remote.server.com",
                remote_username="user",
                remote_port=22,
                remote_path_to_scan_script="/scanfs",
                remote_python_path="python3",
            )
        )
        self.controller._Controller__context.args = SimpleNamespace(local_path_to_scanfs="/local-scanfs")
        self.controller._Controller__context.performance_diagnostics = diagnostics

        with patch("controller.controller.RemoteScanner", side_effect=[scanner_a, scanner_b]) as mock_remote, \
                patch("controller.controller.MultiPathRemoteScanner") as mock_multi:
            scanner = self.controller._Controller__build_remote_scanner([pair_a, pair_b])

        self.assertIs(scanner, mock_multi.return_value)
        self.assertEqual(2, mock_remote.call_count)
        self.assertTrue(all(
            call.kwargs["performance_diagnostics"] is diagnostics
            for call in mock_remote.call_args_list
        ))
        mock_multi.assert_called_once_with(
            [scanner_a, scanner_b],
            performance_diagnostics=diagnostics,
        )

    def test_build_remote_scanner_reuses_controller_lease_across_rebuilt_multi_path_scanners(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        shared_lease = object()
        scanners = [MagicMock(name="scanner_{}".format(index)) for index in range(4)]
        pair_a = PathPair(
            id="pair-a",
            name="Pair A",
            remote_path="/remote/a",
            local_path="/local/a",
            enabled=True,
            auto_queue=False,
        )
        pair_b = PathPair(
            id="pair-b",
            name="Pair B",
            remote_path="/remote/b",
            local_path="/local/b",
            enabled=True,
            auto_queue=False,
        )
        self.controller._Controller__remote_scan_lease = shared_lease
        self.controller._Controller__context.config = SimpleNamespace(
            lftp=SimpleNamespace(
                remote_address="remote.server.com",
                remote_username="user",
                remote_port=22,
                remote_path_to_scan_script="/scanfs",
                remote_python_path="python3",
            )
        )
        self.controller._Controller__context.args = SimpleNamespace(local_path_to_scanfs="/local-scanfs")
        self.controller._Controller__context.performance_diagnostics = diagnostics

        with patch("controller.controller.RemoteScanner", side_effect=scanners) as mock_remote, \
                patch("controller.controller.MultiPathRemoteScanner") as mock_multi:
            self.controller._Controller__build_remote_scanner([pair_a, pair_b])
            self.controller._Controller__build_remote_scanner([pair_a, pair_b])

        self.assertEqual(4, mock_remote.call_count)
        self.assertTrue(all(
            call.kwargs["remote_scan_lease"] is shared_lease
            for call in mock_remote.call_args_list
        ))
        self.assertEqual(2, mock_multi.call_count)
        self.assertEqual([scanners[0], scanners[1]], mock_multi.call_args_list[0].args[0])
        self.assertEqual([scanners[2], scanners[3]], mock_multi.call_args_list[1].args[0])
        self.assertTrue(all(
            call.kwargs["performance_diagnostics"] is diagnostics
            for call in mock_multi.call_args_list
        ))

    def test_constructor_requires_password_for_ftps_even_when_ssh_key_is_enabled(self):
        context = self._make_startup_context(
            local_path="/local",
            remote_password="",
            use_ssh_key=True,
            protocol="ftps",
        )

        with patch("controller.controller.create_transfer_backend") as mock_create_transfer_backend:
            mock_create_transfer_backend.side_effect = AssertionError("should not construct transfer backend")
            controller = Controller(context, ControllerPersist())

        self.assertIsNotNone(controller._Controller__startup_validation_error)
        self.assertIn("Lftp.remote_password", controller._Controller__startup_validation_error)
        mock_create_transfer_backend.assert_not_called()

    def test_constructor_uses_path_pair_fallback_when_legacy_paths_missing(self):
        manager = PathPairManager(tempfile.mkdtemp(prefix="controller_path_pairs"))
        try:
            manager.load()
            manager.add_pair(
                PathPair(
                    name="Movies",
                    remote_path="/remote/movies",
                    local_path="/downloads/movies",
                    enabled=True,
                )
            )
            context = self._make_startup_context(
                local_path=None,
                remote_path=None,
                path_pair_manager=manager,
            )

            with patch("controller.controller.create_transfer_backend") as mock_create_transfer_backend:
                mock_backend = MagicMock()
                mock_create_transfer_backend.return_value = mock_backend
                controller = Controller(context, ControllerPersist())

            self.assertIsNone(controller._Controller__startup_validation_error)
            self.assertEqual("/downloads/movies", controller._Controller__legacy_local_path)
            self.assertEqual("/remote/movies", controller._Controller__legacy_remote_path)
            self.assertEqual(
                os.path.join("/downloads/movies", "incomplete"),
                controller._Controller__staging_path
            )
            self.assertIs(controller._Controller__lftp, mock_backend)
        finally:
            shutil.rmtree(manager._config_dir)

    def test_constructor_ignores_stale_legacy_paths_when_enabled_path_pairs_exist(self):
        manager = PathPairManager(tempfile.mkdtemp(prefix="controller_path_pairs"))
        try:
            manager.load()
            manager.add_pair(
                PathPair(
                    name="Pair Alpha",
                    remote_path="/remote/source-a",
                    local_path="/data/root-a",
                    enabled=True,
                )
            )
            context = self._make_startup_context(
                local_path="/data/stale-legacy-root",
                remote_path="/remote/legacy-source",
                path_pair_manager=manager,
            )

            with patch("controller.controller.create_transfer_backend") as mock_create_transfer_backend:
                mock_create_transfer_backend.return_value = MagicMock()
                controller = Controller(context, ControllerPersist())

            self.assertIsNone(controller._Controller__startup_validation_error)
            self.assertEqual("/data/root-a", controller._Controller__legacy_local_path)
            self.assertEqual("/remote/source-a", controller._Controller__legacy_remote_path)
            self.assertEqual("/data/root-a/incomplete", controller._Controller__staging_path)
        finally:
            shutil.rmtree(manager._config_dir)

    def _refresh_runtime_fallback(self, path_pairs):
        self.controller._Controller__context.config = SimpleNamespace(
            lftp=SimpleNamespace(staging_path=None),
            controller=SimpleNamespace(
                interval_ms_downloading_scan=100,
                interval_ms_local_scan=100,
                interval_ms_remote_scan=100,
                use_local_path_as_extract_path=True,
            ),
        )
        with patch("controller.controller.ScannerProcess"), \
                patch.object(self.controller, "_Controller__build_active_scanner", return_value=MagicMock()), \
                patch.object(self.controller, "_Controller__build_local_scanner", return_value=MagicMock()), \
                patch.object(self.controller, "_Controller__build_remote_scanner", return_value=MagicMock()), \
                patch.object(self.controller, "_Controller__validate_reserved_relocation_identities"):
            self.controller._Controller__refresh_path_pair_runtime_state(path_pairs)

    def _set_configured_runtime_fallback(self):
        self.controller._Controller__configured_legacy_local_path = "/data/legacy-root"
        self.controller._Controller__configured_legacy_remote_path = "/remote/legacy-source"
        self.controller._Controller__legacy_local_path = "/data/legacy-root"
        self.controller._Controller__legacy_remote_path = "/remote/legacy-source"
        self.controller._Controller__staging_path = "/data/legacy-root/incomplete"

    def test_refresh_runtime_fallback_returns_to_configured_legacy_roots_when_no_pairs_remain(self):
        pair_a = PathPair("/remote/source-a", "/data/root-a", "Pair Alpha", "pair-a")
        self._set_configured_runtime_fallback()

        self._refresh_runtime_fallback([pair_a])
        self._refresh_runtime_fallback([])

        self.assertEqual("/data/legacy-root", self.controller._Controller__legacy_local_path)
        self.assertEqual("/remote/legacy-source", self.controller._Controller__legacy_remote_path)
        self.assertEqual("/data/legacy-root/incomplete", self.controller._Controller__staging_path)
        self.controller._Controller__lftp.set_base_remote_dir_path.assert_called_with("/remote/legacy-source")
        self.controller._Controller__lftp.set_base_local_dir_path.assert_called_with("/data/legacy-root/incomplete")
        self.controller._Controller__extract_process.set_base_paths.assert_called_with(
            out_dir_path="/data/legacy-root",
            local_path="/data/legacy-root",
            local_path_fallback="/data/legacy-root/incomplete",
        )
        self.controller._Controller__validate_process.set_base_paths.assert_called_with(
            "/data/legacy-root", "/remote/legacy-source"
        )

    def test_refresh_runtime_fallback_moves_from_first_enabled_pair_to_next_pair(self):
        pair_a = PathPair("/remote/source-a", "/data/root-a", "Pair Alpha", "pair-a")
        pair_b = PathPair("/remote/source-b", "/data/root-b", "Pair Beta", "pair-b")
        self._set_configured_runtime_fallback()

        self._refresh_runtime_fallback([pair_a])
        self._refresh_runtime_fallback([pair_b])

        self.assertEqual("/data/root-b", self.controller._Controller__legacy_local_path)
        self.assertEqual("/remote/source-b", self.controller._Controller__legacy_remote_path)
        self.assertEqual("/data/root-b/incomplete", self.controller._Controller__staging_path)

    def test_refresh_runtime_fallback_uses_next_pair_after_first_pair_is_disabled(self):
        pair_a = PathPair("/remote/source-a", "/data/root-a", "Pair Alpha", "pair-a")
        pair_b = PathPair("/remote/source-b", "/data/root-b", "Pair Beta", "pair-b")
        self._set_configured_runtime_fallback()

        self._refresh_runtime_fallback([pair_a, pair_b])
        self._refresh_runtime_fallback([pair_b])

        self.assertEqual("/data/root-b", self.controller._Controller__legacy_local_path)
        self.assertEqual("/remote/source-b", self.controller._Controller__legacy_remote_path)

    def test_refresh_runtime_fallback_restores_prior_roots_when_activation_fails(self):
        pair_a = PathPair("/remote/source-a", "/data/root-a", "Pair Alpha", "pair-a")
        pair_b = PathPair("/remote/source-b", "/data/root-b", "Pair Beta", "pair-b")
        self._set_configured_runtime_fallback()
        self._refresh_runtime_fallback([pair_a])

        with patch("controller.controller.ScannerProcess"), \
                patch.object(self.controller, "_Controller__build_active_scanner", return_value=MagicMock()), \
                patch.object(self.controller, "_Controller__build_local_scanner", return_value=MagicMock()), \
                patch.object(self.controller, "_Controller__build_remote_scanner", return_value=MagicMock()), \
                patch.object(self.controller, "_Controller__validate_reserved_relocation_identities"), \
                patch.object(
                    self.controller,
                    "_Controller__set_transfer_path_pairs",
                    side_effect=[RuntimeError("activation failed"), None],
                ):
            with self.assertRaisesRegex(RuntimeError, "activation failed"):
                self.controller._Controller__refresh_path_pair_runtime_state([pair_b])

        self.assertEqual("/data/root-a", self.controller._Controller__legacy_local_path)
        self.assertEqual("/remote/source-a", self.controller._Controller__legacy_remote_path)
        self.assertEqual("/data/root-a/incomplete", self.controller._Controller__staging_path)

    def test_constructor_reports_missing_legacy_paths_when_only_disabled_path_pairs_exist(self):
        manager = PathPairManager(tempfile.mkdtemp(prefix="controller_path_pairs"))
        try:
            manager.load()
            manager.add_pair(
                PathPair(
                    name="Movies",
                    remote_path="/remote/movies",
                    local_path="/downloads/movies",
                    enabled=False,
                )
            )
            context = self._make_startup_context(
                local_path=None,
                remote_path=None,
                path_pair_manager=manager,
            )

            with patch("controller.controller.create_transfer_backend") as mock_create_transfer_backend:
                mock_create_transfer_backend.side_effect = AssertionError("should not construct transfer backend")
                controller = Controller(context, ControllerPersist())

            self.assertIsNotNone(controller._Controller__startup_validation_error)
            self.assertIn("Lftp.remote_path", controller._Controller__startup_validation_error)
            self.assertIn("Lftp.local_path", controller._Controller__startup_validation_error)
            mock_create_transfer_backend.assert_not_called()
        finally:
            shutil.rmtree(manager._config_dir)

    def test_queue_command_assigns_unique_flow_ids_under_concurrent_enqueues(self):
        class SlowSequence(int):
            def __new__(cls, value):
                instance = int.__new__(cls, value)
                return instance

            def __add__(self, other):
                time.sleep(0.01)
                return int(self) + other

        thread_count = 16
        self.controller._Controller__command_flow_sequence = SlowSequence(0)

        commands = [Controller.Command(Controller.Command.Action.QUEUE, "dup") for _ in range(thread_count)]
        errors = []

        def _queue(command):
            try:
                self.controller.queue_command(command)
            except Exception as exc:  # pragma: no cover - defensive test capture
                errors.append(exc)

        threads = [threading.Thread(target=_queue, args=(command,)) for command in commands]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual([], errors)
        self.assertEqual(thread_count, self.controller._Controller__command_queue.qsize())
        self.assertEqual(
            thread_count,
            len({command.flow_id for command in commands})
        )
        self.assertEqual(
            ["cmd:queue:dup:{}".format(index) for index in range(1, thread_count + 1)],
            sorted(
                (command.flow_id for command in commands),
                key=lambda flow_id: int(flow_id.rsplit(":", 1)[1])
            )
        )

    def test_queue_command_coalesces_duplicate_delete_local_requests_without_success(self):
        first_command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "dup")
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "dup")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller._Controller__command_queue.put(
            first_command
        )

        self.controller.queue_command(command)

        callback.on_success.assert_not_called()
        callback.on_failure.assert_not_called()
        self.assertEqual([callback], first_command.callbacks)
        self.assertEqual(1, first_command.duplicate_waiter_count)
        self.assertEqual(1, self.controller._Controller__command_queue.qsize())

    def test_queue_command_manual_remote_delete_upgrades_pending_auto_authority(self):
        file = ModelFile("dup", False)
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__staging_path = self.controller._Controller__legacy_local_path
        self.controller._Controller__model.get_file.return_value = file
        auto_command = Controller.Command(
            Controller.Command.Action.DELETE_REMOTE, file.file_id, origin="auto_queue"
        )
        auto_command.lifecycle_token = (3, 7)
        manual_command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id)
        manual_callback = MagicMock()
        manual_command.add_callback(manual_callback)
        self.controller.queue_command(auto_command)

        self.controller.queue_command(manual_command)

        self.assertEqual("manual", auto_command.origin)
        self.assertIsNone(auto_command.lifecycle_token)
        self.assertEqual([manual_callback], auto_command.callbacks)
        self.assertEqual(1, auto_command.duplicate_waiter_count)
        self.assertEqual(1, self.controller._Controller__command_queue.qsize())
        with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            process = MagicMock()
            delete_remote_process.return_value = process
            self.controller._Controller__process_commands()
        process.start.assert_called_once_with()

    def test_queue_command_auto_remote_delete_keeps_pending_manual_authority(self):
        manual_command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, "dup")
        auto_command = Controller.Command(
            Controller.Command.Action.DELETE_REMOTE, "dup", origin="auto_queue"
        )
        auto_command.lifecycle_token = (3, 7)
        auto_callback = MagicMock()
        auto_command.add_callback(auto_callback)
        self.controller.queue_command(manual_command)

        self.controller.queue_command(auto_command)

        self.assertEqual("manual", manual_command.origin)
        self.assertIsNone(manual_command.lifecycle_token)
        self.assertEqual([auto_callback], manual_command.callbacks)
        self.assertEqual(1, manual_command.duplicate_waiter_count)
        self.assertEqual(1, self.controller._Controller__command_queue.qsize())

    def test_queue_command_rejects_duplicate_delete_when_waiter_cap_is_full(self):
        first_command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "dup")
        attached_callbacks = [
            MagicMock()
            for _ in range(Controller._MAX_DUPLICATE_DELETE_WAITERS)
        ]
        first_command.callbacks.extend(attached_callbacks)
        first_command.duplicate_waiter_count = Controller._MAX_DUPLICATE_DELETE_WAITERS
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "dup")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller._Controller__command_queue.put(first_command)

        self.controller.queue_command(command)

        callback.on_failure.assert_called_once_with(
            "Controller is busy with too many duplicate delete waiters",
            429
        )
        callback.on_success.assert_not_called()
        self.assertEqual(attached_callbacks, first_command.callbacks)
        self.assertEqual(
            Controller._MAX_DUPLICATE_DELETE_WAITERS,
            first_command.duplicate_waiter_count
        )
        self.assertEqual(1, self.controller._Controller__command_queue.qsize())

    def test_duplicate_delete_local_callback_receives_original_dispatch_failure(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.state = ModelFile.State.DOWNLOADING
        self.controller._Controller__model.get_file.return_value = file
        first_callback = MagicMock()
        duplicate_callback = MagicMock()
        first_command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        first_command.add_callback(first_callback)
        duplicate_command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        duplicate_command.add_callback(duplicate_callback)

        self.controller.queue_command(first_command)
        self.controller.queue_command(duplicate_command)
        self.controller._Controller__process_commands()

        first_callback.on_success.assert_not_called()
        duplicate_callback.on_success.assert_not_called()
        first_callback.on_failure.assert_called_once_with(
            "Local file '{}' cannot be deleted in state State.DOWNLOADING".format(file.file_id),
            409
        )
        duplicate_callback.on_failure.assert_called_once_with(
            "Local file '{}' cannot be deleted in state State.DOWNLOADING".format(file.file_id),
            409
        )
        self.assertEqual(0, self.controller._Controller__command_queue.qsize())

    def test_queue_command_rejects_delete_requests_when_delete_backlog_is_full(self):
        for index in range(Controller._MAX_PENDING_DELETE_COMMANDS):
            self.controller._Controller__command_queue.put(
                Controller.Command(Controller.Command.Action.DELETE_LOCAL, "dup{}".format(index))
            )
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "dup-next")
        callback = MagicMock()
        command.add_callback(callback)

        self.controller.queue_command(command)

        callback.on_failure.assert_called_once_with(
            "Controller is busy with too many pending delete commands",
            429
        )
        self.assertEqual(Controller._MAX_PENDING_DELETE_COMMANDS, self.controller._Controller__command_queue.qsize())

    def test_update_model_ignores_transfer_backend_status_parser_errors(self):
        self.controller._Controller__lftp.status.side_effect = LftpJobStatusParserError("bad status")

        self.controller._Controller__update_model()

        self.controller.logger.warning.assert_called_once_with("Caught transfer backend error: bad status")
        self.controller._Controller__model_builder.set_lftp_statuses.assert_called_once_with([])
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_called_once_with(set())
        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([])

    def test_update_model_evicts_recent_live_snapshots_after_unhealthy_empty_status_poll(self):
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = False

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_lftp_statuses.assert_called_once_with([])
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_called_once_with(set())
        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([])

    def test_update_model_preserves_recent_live_snapshots_for_roots_returned_by_unhealthy_poll(self):
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__lftp.status.return_value = [status]
        self.controller._Controller__lftp.last_status_poll_healthy = False

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_lftp_statuses.assert_called_once_with([status])
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_not_called()

    def test_update_model_confirms_download_start_only_from_fresh_healthy_running_status(self):
        file = ModelFile("a", False)
        listener = MagicMock()
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__model.get_file.return_value = file
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._Controller__lftp.status.return_value = [status]
        self.controller._Controller__lftp.last_status_poll_healthy = True

        self.controller._Controller__update_model()

        listener.assert_called_once()

    def test_update_model_does_not_confirm_download_start_from_unhealthy_status(self):
        file = ModelFile("a", False)
        listener = MagicMock()
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__model.get_file.return_value = file
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._Controller__lftp.status.return_value = [status]
        self.controller._Controller__lftp.last_status_poll_healthy = False

        self.controller._Controller__update_model()

        listener.assert_not_called()

    def test_update_model_does_not_confirm_download_start_from_cached_status(self):
        file = ModelFile("a", False)
        listener = MagicMock()
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__model.get_file.return_value = file
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._Controller__last_lftp_statuses = [status]
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)

        self.controller._Controller__update_model()

        listener.assert_not_called()

    def test_update_model_prunes_only_from_successful_authoritative_remote_scan(self):
        transitioned_at = datetime.now()
        file_id = "absent"
        self.controller._Controller__download_start_state[file_id] = DownloadStartLifecycleEntry(
            "eligible", None, transitioned_at
        )
        self.controller._Controller__startup_recovery_done = True
        failed_scan = SimpleNamespace(
            timestamp=transitioned_at + timedelta(seconds=1),
            files=[],
            failed=True,
            error_message="scan failed",
            is_scan_final=False,
            is_progress=False,
            scanned_path_pair_ids={None},
            unknown_path_pair_ids={None},
        )
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = failed_scan

        self.controller._Controller__update_model()

        self.assertIn(file_id, self.controller._Controller__download_start_state)
        healthy_scan = SimpleNamespace(
            timestamp=transitioned_at + timedelta(seconds=2),
            files=[],
            failed=False,
            error_message=None,
            is_scan_final=True,
            is_progress=False,
            scanned_path_pair_ids={None},
            unknown_path_pair_ids=set(),
        )
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = healthy_scan

        self.controller._Controller__update_model()

        self.assertNotIn(file_id, self.controller._Controller__download_start_state)

    def test_update_model_authoritative_prune_protects_runtime_and_persisted_identities(self):
        transitioned_at = datetime.now()
        protected_ids = {"queued", "pending", "stopped", "deleting"}
        self.controller._Controller__download_start_state = {
            file_id: DownloadStartLifecycleEntry("eligible", None, transitioned_at)
            for file_id in protected_ids
        }
        status = LftpJobStatus(
            0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "queued", ""
        )
        self.controller._Controller__lftp.status.return_value = [status]
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__pending_completion_file_names = {("pending", None, None)}
        self.controller._Controller__persist.stopped_file_names = {"stopped"}
        self.controller._Controller__command_queue.put(
            Controller.Command(Controller.Command.Action.DELETE_REMOTE, "deleting")
        )
        self.controller._Controller__startup_recovery_done = True
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=transitioned_at + timedelta(seconds=1),
            files=[],
            failed=False,
            error_message=None,
        )

        self.controller._Controller__update_model()

        self.assertEqual(protected_ids, set(self.controller._Controller__download_start_state))

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_transient_removal_preserves_notified_download_start_lifecycle(self, diff_models):
        file = ModelFile("a", False)
        self.controller._Controller__download_start_state[file.file_id] = DownloadStartLifecycleEntry(
            "notified", file.path_pair_id, datetime.now()
        )
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()
        diff_models.return_value = [SimpleNamespace(
            change=ModelDiff.Change.REMOVED,
            old_file=file,
            new_file=None,
        )]

        self.controller._Controller__update_model()

        self.assertEqual("notified", self.controller._Controller__download_start_state[file.file_id].state)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._Controller__model.get_file.return_value = file
        listener = MagicMock()
        self.controller.add_download_start_listener(listener)
        running = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file.name, "")

        self.controller._confirm_fresh_healthy_download_starts([running])

        listener.assert_not_called()

    def test_update_model_uses_unhealthy_returned_statuses_during_cooldown_without_prior_healthy_cache(self):
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__lftp.status.return_value = [status]
        self.controller._Controller__lftp.last_status_poll_healthy = False

        self.controller._Controller__update_model()

        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
        self.controller._Controller__lftp.status.side_effect = AssertionError("should not poll during cooldown without cache")
        self.controller._Controller__update_model()

        self.assertEqual(1, self.controller._Controller__lftp.status.call_count)
        self.assertEqual(
            [[status], [status]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_not_called()
        self.assertEqual(
            [["a"], ["a"]],
            [call.args[0] for call in self.controller._Controller__active_scanner.set_active_files.call_args_list]
        )

    def test_update_model_skips_status_poll_during_retry_window_without_cache(self):
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
        self.controller._Controller__lftp.status.side_effect = AssertionError("should not poll during retry window without cache")

        self.controller._Controller__update_model()

        self.assertEqual(0, self.controller._Controller__lftp.status.call_count)
        self.controller._Controller__model_builder.set_lftp_statuses.assert_called_once_with([])
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_not_called()
        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([])

    def test_update_model_skips_status_poll_during_healthy_cooldown_with_cache(self):
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__lftp.status.return_value = [status]

        self.controller._Controller__update_model()
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
        self.controller._Controller__lftp.status.side_effect = AssertionError("should not poll during healthy cooldown")

        self.controller._Controller__update_model()

        self.assertEqual(1, self.controller._Controller__lftp.status.call_count)
        self.assertEqual(
            [[status], [status]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_not_called()
        self.assertEqual(
            [["a"], ["a"]],
            [call.args[0] for call in self.controller._Controller__active_scanner.set_active_files.call_args_list]
        )

    @patch("controller.model_updater.datetime")
    def test_update_model_schedules_healthy_active_status_poll_one_second_out(self, datetime_mock):
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        now = datetime(2026, 4, 4, 12, 0, 0)
        datetime_mock.now.return_value = now
        self.controller._Controller__lftp.status.return_value = [status]

        self.controller._Controller__update_model()

        self.assertEqual(
            now + timedelta(seconds=1),
            self.controller._Controller__next_lftp_status_poll_at
        )
        self.assertFalse(self.controller._Controller__lftp_status_poll_retry_active)

    @patch("controller.model_updater.datetime")
    def test_update_model_disarms_poll_after_authoritative_idle_status_without_building(self, datetime_mock):
        now = datetime(2026, 4, 4, 12, 0, 0)
        datetime_mock.now.return_value = now
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__model_builder.has_changes.return_value = False
        self.controller._Controller__temp_diag = MagicMock()

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.assertIsNone(self.controller._Controller__next_lftp_status_poll_at)
        self.assertTrue(self.controller._Controller__lftp_idle_status_authoritative)
        self.assertEqual(1, self.controller._Controller__lftp.status.call_count)
        self.assertEqual(
            "cached_idle",
            self.controller._Controller__temp_diag.call_args_list[-1].kwargs["lftp_status_source"],
        )
        self.assertTrue(self.controller._Controller__temp_diag.call_args_list[-1].kwargs["lftp_status_poll_healthy"])
        self.assertFalse(self.controller._Controller__temp_diag.call_args_list[-1].kwargs["lftp_status_snapshot_fresh"])
        self.controller._Controller__model_builder.build_model.assert_not_called()

    def test_queue_keeps_status_poll_active_when_idle_completion_is_unproven(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 100
        file.state = ModelFile.State.DEFAULT
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=1)
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        wake_generation = self.controller.process_wake_generation()

        self.controller.queue_command(command)
        self.assertGreater(self.controller.process_wake_generation(), wake_generation)
        self.controller._Controller__process_commands()

        self.assertIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        self.assertIsNone(self.controller._Controller__next_lftp_status_poll_at)
        self.assertFalse(self.controller._Controller__lftp_idle_status_authoritative)

        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__model_builder.has_changes.return_value = False
        self.controller._Controller__update_model()

        self.controller._Controller__lftp.status.assert_called_once_with()
        self.assertFalse(self.controller._Controller__lftp_idle_status_authoritative)
        self.assertIsNotNone(self.controller._Controller__next_lftp_status_poll_at)

    def test_exit_ignores_lftp_teardown_failure_and_continues_shutdown(self):
        self.controller._Controller__started = True
        self.controller._Controller__lftp.exit.side_effect = LftpError("teardown failed")
        self._set_exit_worker_processes_not_alive()

        self.controller.exit()

        self.controller.logger.warning.assert_called_once()
        self._assert_exit_teardown()

    def test_exit_continues_shutdown_when_process_terminate_fails(self):
        self.controller._Controller__started = True
        self.controller._Controller__active_scan_process.terminate.side_effect = RuntimeError("terminate failed")
        self._set_exit_worker_processes_not_alive()

        self.controller.exit()

        self.controller.logger.exception.assert_any_call(
            "Ignoring controller teardown failure during %s; continuing shutdown",
            "active scan process terminate"
        )
        self._assert_exit_teardown()

    def test_exit_continues_shutdown_when_process_join_fails(self):
        self.controller._Controller__started = True
        self.controller._Controller__extract_process.pid = 123
        self.controller._Controller__extract_process.join.side_effect = RuntimeError("join failed")
        self._set_exit_worker_processes_not_alive()

        self.controller.exit()

        self.controller.logger.exception.assert_any_call(
            "Ignoring controller teardown failure during %s; continuing shutdown",
            "extract process join"
        )
        self._assert_exit_teardown()

    def test_exit_continues_when_worker_join_times_out(self):
        self.controller._Controller__started = True
        self.controller._Controller__extract_process.pid = 123
        self._set_exit_worker_processes_not_alive()
        stuck_process = self.controller._Controller__extract_process
        stuck_process.is_alive.return_value = True
        stuck_process.name = "extract process"

        self.controller.exit()

        self.controller.logger.warning.assert_called_once_with(
            "Worker %s did not exit within %ss; continuing teardown",
            "extract process",
            Controller._Controller__JOIN_TIMEOUT_IN_SECS
        )
        self._assert_exit_teardown(still_alive_processes={stuck_process})

    def test_exit_skips_active_scanner_close_when_active_scan_join_times_out(self):
        self.controller._Controller__started = True
        self._set_exit_worker_processes_not_alive()
        stuck_process = self.controller._Controller__active_scan_process
        stuck_process.is_alive.return_value = True
        stuck_process.name = "active scan process"

        self.controller.exit()

        self.controller.logger.warning.assert_called_once_with(
            "Worker %s did not exit within %ss; continuing teardown",
            "active scan process",
            Controller._Controller__JOIN_TIMEOUT_IN_SECS
        )
        self._assert_exit_teardown(
            still_alive_processes={stuck_process},
            active_scanner_closed=False,
        )

    def test_exit_continues_shutdown_when_lftp_raises_unexpected_error(self):
        self.controller._Controller__started = True
        self.controller._Controller__lftp.exit.side_effect = RuntimeError("lftp died")
        self._set_exit_worker_processes_not_alive()

        self.controller.exit()

        self.controller.logger.exception.assert_any_call("Ignoring lftp teardown failure; continuing shutdown")
        self._assert_exit_teardown()

    def test_exit_terminates_and_clears_active_command_processes(self):
        self.controller._Controller__started = True
        self._set_exit_worker_processes_not_alive()
        command_process = MagicMock()
        command_process.name = "DeleteRemoteProcess"
        command_process.is_alive.return_value = False
        self.controller._Controller__active_command_processes = [
            SimpleNamespace(process=command_process)
        ]

        self.controller.exit()

        command_process.terminate.assert_called_once_with()
        command_process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        command_process.close_queues.assert_called_once_with()
        self.assertEqual([], self.controller._Controller__active_command_processes)

    def test_teardown_process_skips_close_queues_when_join_times_out(self):
        process = MagicMock()
        process.name = "stuck worker"
        process.is_alive.return_value = True

        self.controller._Controller__teardown_process("stuck worker", process)

        process.terminate.assert_called_once_with()
        process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        process.close_queues.assert_not_called()
        self.controller.logger.warning.assert_called_once_with(
            "Worker %s did not exit within %ss; continuing teardown",
            "stuck worker",
            Controller._Controller__JOIN_TIMEOUT_IN_SECS
        )

    def test_teardown_process_only_closes_queues_when_start_never_succeeded(self):
        process = MagicMock()
        process.pid = None

        self.assertTrue(self.controller._Controller__teardown_process("unstarted worker", process))

        process.terminate.assert_not_called()
        process.join.assert_not_called()
        process.close_queues.assert_called_once_with()

    @patch.object(Controller, "_Controller__preflight_runtime_storage_roots")
    def test_start_records_breadcrumb_when_enabled(self, preflight):
        self.controller._Controller__context.breadcrumb_trace = MagicMock()

        self.controller.start()

        self.controller._Controller__context.breadcrumb_trace.record.assert_called_once_with(
            "controller",
            "start",
            {
                "path_pair_count": 0,
                "staging_path_count": 0,
            },
            stage="controller",
            event_type="state_transition",
            corr_id="controller",
            flow_id=None,
            file_id=None,
            path_pair_id=None,
            path_pair_name=None,
            trace_scope="flow",
        )
        self.controller._Controller__active_scan_process.start.assert_called_once_with()
        self.controller._Controller__local_scan_process.start.assert_called_once_with()
        self.controller._Controller__remote_scan_process.start.assert_called_once_with()
        self.controller._Controller__extract_process.start.assert_not_called()
        self.controller._Controller__validate_process.start.assert_not_called()
        self.controller._Controller__mp_logger.start.assert_called_once_with()
        preflight.assert_called_once_with([], {})
        self.assertTrue(self.controller._Controller__started)

    @patch.object(Controller, "_Controller__preflight_runtime_storage_roots")
    def test_start_leaves_started_false_if_child_start_fails(self, _preflight):
        self.controller._Controller__mp_logger.start.side_effect = RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self.controller.start()

        self.assertFalse(self.controller._Controller__started)
        self.assertTrue(self.controller._Controller__startup_failed)
        self.controller._Controller__active_scan_process.start.assert_called_once_with()
        self.controller._Controller__local_scan_process.start.assert_called_once_with()
        self.controller._Controller__remote_scan_process.start.assert_called_once_with()
        self.controller._Controller__extract_process.start.assert_not_called()
        self.controller._Controller__validate_process.start.assert_not_called()
        self.controller._Controller__mp_logger.start.assert_called_once_with()
        self.controller._Controller__propagate_exceptions = MagicMock()
        self.controller._Controller__cleanup_commands = MagicMock()
        self.controller._Controller__process_commands = MagicMock()
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()

        with self.assertRaises(ControllerError) as error:
            self.controller.process()

        self.assertIn("startup failed", str(error.exception))
        self.controller._Controller__propagate_exceptions.assert_not_called()
        self.controller._Controller__cleanup_commands.assert_not_called()
        self.controller._Controller__process_commands.assert_not_called()
        self.controller._Controller__updater.update.assert_not_called()
        self.controller._Controller__log_memory_usage.assert_not_called()
        self._set_exit_worker_processes_not_alive()
        self.controller.exit()
        self._assert_exit_teardown()

    def test_preflight_enabled_pair_uses_pair_roots_not_stale_legacy_root(self):
        with tempfile.TemporaryDirectory() as root:
            pair_local = os.path.join(root, "root-a")
            pair_staging = os.path.join(pair_local, "incomplete")
            disabled_root = os.path.join(root, "disabled")
            pair = PathPair(id="pair-a", name="Pair Alpha", remote_path="/remote/source-a", local_path=pair_local)
            self.controller._Controller__legacy_local_path = "/data/stale-legacy-root"
            self.controller._Controller__staging_path = "/data/stale-legacy-root/incomplete"
            self.controller._Controller__context.config = SimpleNamespace(
                controller=SimpleNamespace(use_local_path_as_extract_path=True, extract_path=None)
            )

            self.controller._Controller__preflight_runtime_storage_roots([pair], {pair.id: pair_staging})

            self.assertTrue(os.path.isdir(pair_local))
            self.assertTrue(os.path.isdir(pair_staging))
            self.assertFalse(os.path.exists(disabled_root))

    def test_preflight_legacy_root_preserves_writable_legacy_root_behavior(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = os.path.join(root, "legacy-root")
            staging_root = os.path.join(local_root, "incomplete")
            self.controller._Controller__legacy_local_path = local_root
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__context.config = SimpleNamespace(
                controller=SimpleNamespace(use_local_path_as_extract_path=True, extract_path=None)
            )

            self.controller._Controller__preflight_runtime_storage_roots([], {})

            self.assertTrue(os.path.isdir(local_root))
            self.assertTrue(os.path.isdir(staging_root))

    def test_preflight_creates_and_probes_explicit_extraction_root(self):
        with tempfile.TemporaryDirectory() as root:
            local_root = os.path.join(root, "local")
            staging_root = os.path.join(local_root, "incomplete")
            extract_root = os.path.join(root, "extract")
            self.controller._Controller__legacy_local_path = local_root
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__context.config = SimpleNamespace(
                controller=SimpleNamespace(use_local_path_as_extract_path=False, extract_path=extract_root)
            )

            self.controller._Controller__preflight_runtime_storage_roots([], {})

            self.assertTrue(os.path.isdir(extract_root))

    def test_preflight_fails_closed_for_unwritable_active_root(self):
        with tempfile.TemporaryDirectory() as root:
            pair_local = os.path.join(root, "root-a")
            pair_staging = os.path.join(pair_local, "incomplete")
            pair = PathPair(id="pair-a", name="Pair Alpha", remote_path="/remote/source-a", local_path=pair_local)
            self.controller._Controller__context.config = SimpleNamespace(
                controller=SimpleNamespace(use_local_path_as_extract_path=True, extract_path=None)
            )

            with patch("controller.controller.tempfile.mkstemp", side_effect=PermissionError("read-only")):
                with self.assertRaises(ControllerError) as error:
                    self.controller._Controller__preflight_runtime_storage_roots([pair], {pair.id: pair_staging})

            self.assertIn(pair_local, str(error.exception))
            self.assertIn("not writable", str(error.exception))

    @patch("controller.controller.ScannerProcess")
    def test_refresh_records_storage_preflight_failure_without_replacing_active_roots(self, scanner_process):
        old_pair = PathPair(id="pair-old", name="Pair Old", remote_path="/remote/source-old", local_path="/data/root-old")
        new_pair = PathPair(id="pair-new", name="Pair New", remote_path="/remote/source-new", local_path="/data/root-new")
        self.controller._Controller__started = True
        self.controller._Controller__path_pairs_by_id = {old_pair.id: old_pair}
        self.controller._Controller__path_pair_staging_paths = {old_pair.id: "/data/root-old/incomplete"}
        self.controller._Controller__context.path_pair_manager = MagicMock()
        self.controller._Controller__context.path_pair_manager.get_enabled_pairs.return_value = [new_pair]
        self.controller._Controller__context.config = SimpleNamespace(
            lftp=SimpleNamespace(
                staging_path=None, use_temp_file=False, remote_address="host", remote_port=22,
                remote_username="user", remote_path_to_scan_script="/scanfs", remote_python_path=None,
            ),
            controller=SimpleNamespace(
                managed_extract_folders_enabled=False, interval_ms_downloading_scan=100,
                interval_ms_local_scan=100, interval_ms_remote_scan=100,
                use_local_path_as_extract_path=True, extract_path=None,
            ),
        )
        self.controller._Controller__context.args = SimpleNamespace(local_path_to_scanfs="/scanfs")

        with patch.object(self.controller, "_Controller__preflight_runtime_storage_roots", side_effect=ControllerError("root is not writable")):
            self.controller._Controller__apply_path_pair_refresh()

        self.assertEqual({old_pair.id: old_pair}, self.controller._Controller__path_pairs_by_id)
        self.assertEqual({old_pair.id: "/data/root-old/incomplete"}, self.controller._Controller__path_pair_staging_paths)
        self.assertIn("root is not writable", self.controller._Controller__path_pair_runtime_error)
        self.assertFalse(self.controller._Controller__context.status.server.up)

    @patch.object(Controller, "_Controller__preflight_runtime_storage_roots")
    def test_process_rejects_partial_start_failure_before_exit(self, _preflight):
        self.controller._Controller__mp_logger.start.side_effect = RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self.controller.start()

        self.assertFalse(self.controller._Controller__started)
        self.assertTrue(self.controller._Controller__startup_failed)
        self.controller._Controller__propagate_exceptions = MagicMock()
        self.controller._Controller__cleanup_commands = MagicMock()
        self.controller._Controller__process_commands = MagicMock()
        self.controller._Controller__apply_path_pair_refresh = MagicMock()
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()

        with self.assertRaises(ControllerError) as refresh_error:
            self.controller.refresh_path_pairs()

        self.assertIn("startup failed", str(refresh_error.exception))
        self.controller._Controller__apply_path_pair_refresh.assert_not_called()

        with self.assertRaises(ControllerError) as error:
            self.controller.process()

        self.assertIn("startup failed", str(error.exception))
        self.controller._Controller__propagate_exceptions.assert_not_called()
        self.controller._Controller__cleanup_commands.assert_not_called()
        self.controller._Controller__process_commands.assert_not_called()
        self.controller._Controller__updater.update.assert_not_called()
        self.controller._Controller__log_memory_usage.assert_not_called()

    def test_process_holds_persist_transaction_across_controller_tick(self):
        persist = ControllerPersist()
        self.controller._Controller__persist = persist
        process_started = threading.Event()
        continue_process = threading.Event()
        serialization_started = threading.Event()
        serialization_finished = threading.Event()

        def process_tick():
            process_started.set()
            self.assertTrue(continue_process.wait(2))

        def serialize():
            serialization_started.set()
            persist.to_str()
            serialization_finished.set()

        self.controller._Controller__process_persist_transaction = process_tick
        process_thread = threading.Thread(target=self.controller.process)
        process_thread.start()
        self.assertTrue(process_started.wait(2))

        serialization_thread = threading.Thread(target=serialize)
        serialization_thread.start()
        self.assertTrue(serialization_started.wait(2))
        self.assertFalse(serialization_finished.wait(0.05))
        continue_process.set()

        process_thread.join(2)
        serialization_thread.join(2)
        self.assertFalse(process_thread.is_alive())
        self.assertFalse(serialization_thread.is_alive())

    def test_configure_lftp_applies_net_socket_buffer_when_configured(self):
        self.controller._Controller__context.config.lftp.net_socket_buffer = "512K"

        self.controller._Controller__configure_lftp()

        self.assertEqual("512K", self.controller._Controller__lftp.net_socket_buffer)

    def test_configure_lftp_applies_use_temp_file_at_startup(self):
        self.controller._Controller__context.config.lftp.use_temp_file = True

        self.controller._Controller__configure_lftp()

        self.assertTrue(self.controller._Controller__lftp.use_temp_file)

    def test_runtime_reconfigure_lftp_does_not_apply_use_temp_file(self):
        self.controller._Controller__context.config.general = SimpleNamespace(verbose=False)
        self.controller._Controller__context.config.validate = SimpleNamespace(xfer_verify=False)
        self.controller._Controller__context.config.lftp.use_temp_file = True
        self.controller._Controller__lftp.use_temp_file = False

        self.controller._Controller__configure_lftp(runtime_reconfigure=True)

        self.assertFalse(self.controller._Controller__lftp.use_temp_file)

    def test_configure_lftp_applies_rate_limit_when_configured(self):
        self.controller._Controller__context.config.lftp.rate_limit = "512K"

        self.controller._Controller__configure_lftp()

        self.assertEqual("512K", self.controller._Controller__lftp.rate_limit)

    def test_configure_lftp_clears_blank_rate_limit_and_net_socket_buffer(self):
        self.controller._Controller__context.config.general = SimpleNamespace(verbose=True)
        self.controller._Controller__context.config.lftp.rate_limit = ""
        self.controller._Controller__context.config.lftp.net_socket_buffer = ""
        self.controller._Controller__lftp.rate_limit = "2048"
        self.controller._Controller__lftp.net_socket_buffer = "16M"

        self.controller._Controller__configure_lftp()

        self.assertEqual(0, self.controller._Controller__lftp.rate_limit)
        self.assertEqual(0, self.controller._Controller__lftp.net_socket_buffer)
        self.controller._Controller__lftp.set_verbose_logging.assert_called_once_with(True)

    def test_configure_lftp_enables_xfer_verify_with_validate_hash_command(self):
        self.controller._Controller__context.config.general = SimpleNamespace(verbose=False)
        self.controller._Controller__context.config.validate = SimpleNamespace(xfer_verify=True)

        self.controller._Controller__configure_lftp()

        self.assertTrue(self.controller._Controller__lftp.xfer_verify)
        self.assertEqual(
            ValidateProcess.HASH_COMMAND,
            self.controller._Controller__lftp.xfer_verify_command
        )

    def test_configure_lftp_disables_xfer_verify_without_setting_verify_command(self):
        self.controller._Controller__context.config.general = SimpleNamespace(verbose=False)
        self.controller._Controller__context.config.validate = SimpleNamespace(xfer_verify=False)

        self.controller._Controller__configure_lftp()

        self.assertFalse(self.controller._Controller__lftp.xfer_verify)
        self.assertNotIn("xfer_verify_command", self.controller._Controller__lftp.__dict__)

    def test_request_lftp_reconfigure_marks_pending_request(self):
        self.controller.request_lftp_reconfigure()

        self.assertTrue(self.controller._Controller__lftp_reconfigure_requested)

    def test_process_reapplies_lftp_settings_and_clears_pending_request(self):
        self.controller._Controller__started = True
        self.controller._Controller__propagate_exceptions = MagicMock()
        self.controller._Controller__cleanup_commands = MagicMock()
        self.controller._Controller__process_commands = MagicMock()
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()
        self.controller._Controller__context.config.general = SimpleNamespace(
            verbose=True,
            exclude_patterns="*.nfo,Season */*.nfo",
        )
        self.controller._Controller__configure_lftp = MagicMock()
        self.controller._Controller__exclude_patterns = "stale"
        self.controller.request_lftp_reconfigure()

        self.controller.process()

        self.controller._Controller__configure_lftp.assert_called_once_with(runtime_reconfigure=True)
        self.assertEqual("*.nfo,Season */*.nfo", self.controller._Controller__exclude_patterns)
        self.assertFalse(self.controller._Controller__lftp_reconfigure_requested)
        self.controller._Controller__propagate_exceptions.assert_called_once_with()
        self.controller._Controller__cleanup_commands.assert_called_once_with()
        self.controller._Controller__process_commands.assert_called_once_with()
        self.controller._Controller__updater.update.assert_called_once_with()
        self.controller._Controller__log_memory_usage.assert_called_once_with()

    def test_process_attributes_each_fixed_child_stage(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        self.controller._Controller__context.performance_diagnostics = diagnostics
        self.controller._Controller__started = True
        self.controller._Controller__propagate_exceptions = MagicMock()
        self.controller._Controller__cleanup_commands = MagicMock()
        self.controller._Controller__process_commands = MagicMock()
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__reap_idle_auxiliary_workers = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()

        self.controller.process()

        durations = diagnostics.snapshot()["durations"]
        for metric in (
            DURATION_CONTROLLER_PROCESS,
            DURATION_CONTROLLER_PROPAGATE_EXCEPTIONS,
            DURATION_CONTROLLER_CLEANUP_COMMANDS,
            DURATION_CONTROLLER_PROCESS_COMMANDS,
            DURATION_CONTROLLER_CONFIGURATION,
            DURATION_MODEL_UPDATE,
            DURATION_CONTROLLER_AUXILIARY_REAP,
            DURATION_CONTROLLER_DIAGNOSTICS,
        ):
            self.assertEqual(1, durations[metric]["count"], metric)

    def test_process_clears_blank_lftp_settings_during_reconfigure(self):
        self.controller._Controller__started = True
        self.controller._Controller__propagate_exceptions = MagicMock()
        self.controller._Controller__cleanup_commands = MagicMock()
        self.controller._Controller__process_commands = MagicMock()
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()
        self.controller._Controller__context.config.general = SimpleNamespace(verbose=True)
        self.controller._Controller__context.config.lftp.rate_limit = ""
        self.controller._Controller__context.config.lftp.net_socket_buffer = ""
        self.controller._Controller__lftp.rate_limit = "2048"
        self.controller._Controller__lftp.net_socket_buffer = "16M"
        self.controller.request_lftp_reconfigure()

        self.controller.process()

        self.assertEqual(0, self.controller._Controller__lftp.rate_limit)
        self.assertEqual(0, self.controller._Controller__lftp.net_socket_buffer)
        self.controller._Controller__lftp.set_verbose_logging.assert_called_once_with(True)
        self.assertFalse(self.controller._Controller__lftp_reconfigure_requested)

    def test_process_keeps_lftp_reconfigure_pending_after_failure(self):
        self.controller._Controller__started = True
        self.controller._Controller__propagate_exceptions = MagicMock()
        self.controller._Controller__cleanup_commands = MagicMock()
        self.controller._Controller__process_commands = MagicMock()
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()
        self.controller._Controller__configure_lftp = MagicMock(side_effect=RuntimeError("boom"))
        self.controller.request_lftp_reconfigure()

        self.controller.process()

        self.controller._Controller__configure_lftp.assert_called_once_with(runtime_reconfigure=True)
        self.assertTrue(self.controller._Controller__lftp_reconfigure_requested)

    def test_async_lftp_reconfigure_publishes_exclusions_only_after_success(self):
        self.controller._Controller__started = True
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__propagate_exceptions = MagicMock()
        self.controller._Controller__cleanup_commands = MagicMock()
        self.controller._Controller__process_commands = MagicMock()
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()
        self.controller._Controller__context.config.general = SimpleNamespace(
            verbose=True,
            exclude_patterns="new-pattern",
        )
        self.controller._Controller__configure_lftp = MagicMock(
            side_effect=RuntimeError("reconfigure failed")
        )
        self.controller._Controller__exclude_patterns = "stale"
        self.controller.request_lftp_reconfigure()

        self.controller.process()
        future = self.controller._Controller__lftp_operations[0].future
        with self.assertRaises(RuntimeError):
            future.result(timeout=1)
        self.controller._Controller__drain_lftp_operations()

        self.assertEqual("stale", self.controller._Controller__exclude_patterns)
        self.assertTrue(self.controller._Controller__lftp_reconfigure_requested)
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def test_update_model_records_scan_and_extract_breadcrumbs(self):
        remote_scan = SimpleNamespace(
            files=[SimpleNamespace(name="remote-one", file_id="remote-1", path_pair_id="pair-1")],
            failed=False,
            error_message=None,
            timestamp=datetime.now(),
        )
        local_scan = SimpleNamespace(
            files=[SimpleNamespace(name="local-one", file_id="local-1", path_pair_id="pair-1")],
            managed_extract_file_ids=["managed-one"],
            timestamp=datetime.now(),
        )
        extract_status = ExtractStatus("archive.zip", False, ExtractStatus.State.EXTRACTING)
        extract_statuses = SimpleNamespace(statuses=[extract_status])
        extracted_results = [
            SimpleNamespace(name="archive.zip", file_id="file-123", is_dir=False, path_pair_id="pair-1")
        ]
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = SimpleNamespace(
            files=[],
            malformed_status_only_file_ids=[],
            managed_extract_file_ids=[],
            timestamp=datetime.now(),
        )

        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = remote_scan
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = local_scan
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = extract_statuses
        self.controller._Controller__validate_process.pop_latest_statuses.return_value = None
        self.controller._Controller__extract_process.pop_completed.return_value = extracted_results
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__path_pairs_by_id = {
            "pair-1": SimpleNamespace(local_path="/local/pair-1", remote_path="/remote/pair-1")
        }
        self.controller._Controller__context.breadcrumb_trace.is_effectively_enabled.return_value = True
        self.controller._Controller__context.breadcrumb_trace.record.reset_mock()

        self.controller._Controller__update_model()

        message_to_corr_ids = {
            call.args[1]: call.kwargs.get("corr_id")
            for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
        }
        categories_and_levels = {
            call.args[1]: (call.kwargs.get("category"), call.kwargs.get("level"))
            for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
        }
        self.assertEqual("pair-1", message_to_corr_ids["remote_scan_result"])
        self.assertEqual("pair-1", message_to_corr_ids["local_scan_result"])
        self.assertEqual("pair-1", message_to_corr_ids["extract_completed"])
        self.assertEqual("extract:aggregate", message_to_corr_ids["extract_status_result"])
        self.assertEqual(("scan.result", "info"), categories_and_levels["remote_scan_result"])
        self.assertEqual(("scan.result", "info"), categories_and_levels["local_scan_result"])
        self.assertEqual(("extract.result", "info"), categories_and_levels["extract_completed"])
        self.assertEqual(("extract.result", "info"), categories_and_levels["extract_status_result"])
        self.assertIn(
            ModelFile.build_file_id("archive.zip", "pair-1"),
            self.controller._Controller__persist.extracted_file_names,
        )
        self.assertNotIn("archive.zip", self.controller._Controller__persist.extracted_file_names)
        self.assertNotIn("managed-one", self.controller._Controller__persist.extracted_file_names)

    def test_update_model_records_extract_failed_breadcrumb_without_marking_extracted(self):
        failed_results = [
            SimpleNamespace(name="archive.zip", file_id="file-123", is_dir=False, path_pair_id="pair-1")
        ]
        extract_statuses = SimpleNamespace(statuses=[
            ExtractStatus("archive.zip", False, ExtractStatus.State.EXTRACTING)
        ])
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = extract_statuses
        self.controller._Controller__extract_process.pop_failed.return_value = failed_results
        self.controller._Controller__validate_process.pop_latest_statuses.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__path_pairs_by_id = {
            "pair-1": SimpleNamespace(local_path="/local/pair-1", remote_path="/remote/pair-1")
        }
        self.controller._Controller__context.breadcrumb_trace.is_effectively_enabled.return_value = True
        self.controller._Controller__context.breadcrumb_trace.record.reset_mock()

        self.controller._Controller__update_model()

        self.controller._Controller__context.breadcrumb_trace.record.assert_any_call(
            "controller",
            "extract_failed",
            {
                "result_count": 1,
                "results": [{
                    "name": "archive.zip",
                    "file_id": "file-123",
                    "is_dir": False,
                    "path_pair_id": "pair-1",
                }],
            },
            stage="extract",
            event_type="failure",
            corr_id="pair-1",
            flow_id=None,
            file_id=None,
            path_pair_id=None,
            path_pair_name=None,
            trace_scope="flow",
            category="extract.result",
            level="info",
        )
        self.assertEqual(set(), self.controller._Controller__persist.extracted_file_names)
        self.controller._Controller__model_builder.set_extracted_files.assert_not_called()
        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with(["archive.zip"])

    def test_update_model_ignores_extract_results_for_removed_path_pair(self):
        extracted_results = [
            SimpleNamespace(name="archive.zip", file_id="file-123", is_dir=False, path_pair_id="missing")
        ]
        failed_results = [
            SimpleNamespace(name="archive.zip", file_id="file-123", is_dir=False, path_pair_id="missing")
        ]
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = None
        self.controller._Controller__extract_process.pop_completed.return_value = extracted_results
        self.controller._Controller__extract_process.pop_failed.return_value = failed_results
        self.controller._Controller__validate_process.pop_latest_statuses.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__path_pairs_by_id = {}
        self.controller._Controller__context.breadcrumb_trace.record.reset_mock()
        self.controller.logger.warning.reset_mock()

        self.controller._Controller__update_model()

        self.controller.logger.warning.assert_any_call(
            "Ignoring extract %s for '%s': pair '%s' no longer exists",
            "completion",
            "archive.zip",
            "missing",
        )
        self.controller.logger.warning.assert_any_call(
            "Ignoring extract %s for '%s': pair '%s' no longer exists",
            "failure",
            "archive.zip",
            "missing",
        )
        self.assertEqual(set(), self.controller._Controller__persist.extracted_file_names)
        self.controller._Controller__model_builder.set_extracted_files.assert_called_once_with(set())

    def test_update_model_keeps_duplicate_name_extracting_status_for_other_path_pair_after_failure(self):
        failed_results = [
            SimpleNamespace(name="archive.zip", file_id="file-a", is_dir=False, path_pair_id="pair-a")
        ]
        extract_statuses = SimpleNamespace(statuses=[
            ExtractStatus(
                "archive.zip",
                False,
                ExtractStatus.State.EXTRACTING,
                file_id="file-a",
                path_pair_id="pair-a"
            ),
            ExtractStatus(
                "archive.zip",
                False,
                ExtractStatus.State.EXTRACTING,
                file_id="file-b",
                path_pair_id="pair-b"
            ),
        ])
        self.controller._Controller__active_scanner = MultiPathActiveScanner({})
        self.controller._Controller__active_scanner.set_active_files = MagicMock()
        self.controller._Controller__path_pairs_by_id = {
            "pair-b": SimpleNamespace(name="Pair B")
        }
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = extract_statuses
        self.controller._Controller__extract_process.pop_failed.return_value = failed_results
        self.controller._Controller__validate_process.pop_latest_statuses.return_value = None
        self.controller._Controller__lftp.status.return_value = []

        self.controller._Controller__update_model()

        self.assertEqual([
            ("archive.zip", "pair-b", "Pair B")
        ], self.controller._Controller__active_extracting_file_names)
        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([
            ("archive.zip", "pair-b", "Pair B")
        ])

    def test_propagate_exceptions_records_remote_scan_failure_breadcrumb(self):
        self.controller._Controller__remote_scan_process.propagate_exception.side_effect = Exception("boom")
        self.controller._Controller__local_scan_process.propagate_exception.return_value = None
        self.controller._Controller__active_scan_process.propagate_exception.return_value = None
        self.controller._Controller__validate_process.propagate_exception.return_value = None
        self.controller._Controller__extract_process.propagate_exception.return_value = None
        self.controller._Controller__mp_logger.propagate_exception.return_value = None
        self.controller._Controller__context.breadcrumb_trace.record.reset_mock()

        with self.assertRaises(Exception):
            self.controller._Controller__propagate_exceptions()

        self.controller._Controller__context.breadcrumb_trace.record.assert_any_call(
            "controller",
            "remote_scan_failure",
            {"error_message": "boom"},
            stage="scan",
            event_type="failure",
            corr_id="remote_scan:aggregate",
            flow_id=None,
            file_id=None,
            path_pair_id=None,
            path_pair_name=None,
            trace_scope="aggregate",
        )

    def test_update_model_preserves_stale_lftp_statuses_after_unhealthy_poll_returns_data_and_cache_expires(self):
        status_a = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status_b = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "b", "")

        self.controller._Controller__lftp.status.return_value = [status_a]
        self.controller._Controller__update_model()

        self.controller._Controller__lftp.status.return_value = [status_b]
        self.controller._Controller__lftp.last_status_poll_healthy = False
        self.controller._Controller__update_model()

        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
        self.controller._Controller__lftp_status_cache_expires_at = datetime.now() - timedelta(seconds=1)
        self.controller._Controller__lftp.status.side_effect = AssertionError("should not poll once cache expires during cooldown")
        self.controller._Controller__update_model()

        self.assertEqual(2, self.controller._Controller__lftp.status.call_count)
        self.assertEqual(
            [[status_a], [status_a], [status_a]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_not_called()
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["a"])
        self.assertEqual(3, self.controller._Controller__active_scanner.set_active_files.call_count)

    def test_update_model_uses_cached_lftp_statuses_during_unhealthy_retry_window(self):
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__lftp.status.side_effect = [
            [status],
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        self.controller._Controller__lftp.status.side_effect = LftpError("bad status")
        self.controller._Controller__update_model()
        self.controller._Controller__lftp.status.side_effect = AssertionError("should not poll during retry window")
        self.controller._Controller__update_model()

        self.assertEqual(2, self.controller._Controller__lftp.status.call_count)
        self.assertIsNotNone(self.controller._Controller__next_lftp_status_poll_at)
        self.assertEqual(
            [[status], [status], [status]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )
        self.assertEqual(3, self.controller._Controller__active_scanner.set_active_files.call_count)
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["a"])

    def test_path_pair_reconciliation_authority_invalidates_failed_pair_only(self):
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(local_path="/local/a"),
            "pair-b": SimpleNamespace(local_path="/local/b"),
        }
        self.controller._Controller__reconciled_local_path_pair_ids = {"pair-a"}
        self.controller._Controller__reconciled_remote_path_pair_ids = {"pair-a"}
        self.controller._Controller__last_local_reconciliation_healthy = True
        self.controller._Controller__last_remote_reconciliation_healthy = True
        self.controller._Controller__remote_scan_process.pop_latest_result.side_effect = [
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-a"}, failed=True,
                is_targeted_scan=True, unknown_path_pair_ids={"pair-a"},
            ),
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-b"},
                is_targeted_scan=True,
            ),
        ]
        self.controller._Controller__local_scan_process.pop_latest_result.side_effect = [
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-b"},
                is_targeted_scan=True,
            ),
            None,
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.assertTrue(self.controller._Controller__last_local_reconciliation_healthy)
        self.assertTrue(self.controller._Controller__last_remote_reconciliation_healthy)
        self.assertFalse(self.controller.is_path_pair_reconciled("pair-a"))
        self.assertTrue(self.controller.is_path_pair_reconciled("pair-b"))

    def test_path_pair_reconciliation_authority_includes_unknown_builder_overlay_and_summary(self):
        file = ModelFile("release", True)
        file.path_pair_id = "pair-a"
        self.controller._Controller__model.iter_files.return_value = [file]
        self.controller._Controller__model.version = 1
        self.controller._Controller__model_builder.local_library_inventory_snapshot.return_value = (0, {})
        self.controller._Controller__reconciled_local_path_pair_ids = {"pair-a"}
        self.controller._Controller__reconciled_remote_path_pair_ids = {"pair-a"}
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset({"pair-a"})

        self.assertFalse(self.controller.is_path_pair_reconciled("pair-a"))
        summary = self.controller.get_model_summary()["path_pairs"][0]
        self.assertFalse(summary["reconciled_local"])
        self.assertTrue(summary["reconciled_remote"])

        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()
        self.assertTrue(self.controller.is_path_pair_reconciled("pair-a"))
        summary = self.controller.get_model_summary()["path_pairs"][0]
        self.assertTrue(summary["reconciled_local"])
        self.assertTrue(summary["reconciled_remote"])

    def test_model_summary_cache_invalidates_when_unknown_local_overlay_changes(self):
        file = ModelFile("release", True)
        file.path_pair_id = "pair-a"
        self.controller._Controller__model.iter_files.return_value = [file]
        self.controller._Controller__model.version = 1
        self.controller._Controller__model_builder.local_library_inventory_snapshot.return_value = (0, {})
        self.controller._Controller__reconciled_local_path_pair_ids = {"pair-a"}
        self.controller._Controller__reconciled_remote_path_pair_ids = {"pair-a"}
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()

        initial = self.controller.get_model_summary()
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset({"pair-a"})
        changed = self.controller.get_model_summary(max_age_seconds=60)
        self.assertIsNot(initial, changed)
        self.assertFalse(changed["path_pairs"][0]["reconciled_local"])
        self.assertTrue(changed["path_pairs"][0]["reconciled_remote"])

        unchanged = self.controller.get_model_summary(max_age_seconds=60)
        self.assertIs(changed, unchanged)

    def test_model_summary_exposes_current_scan_authority_and_invalidates_cache(self):
        self.controller._Controller__model.version = 1
        self.controller._Controller__model.iter_files.return_value = []
        self.controller._Controller__model_builder.local_library_inventory_snapshot.return_value = (0, {})
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()
        self.controller._Controller__scan_authority_snapshot = {
            "final": False,
            "full": False,
            "outcome": "no_op",
            "reason": "joint_not_final",
            "effective_local_reconciliation_after_count": 0,
        }

        initial = self.controller.get_model_summary(max_age_seconds=60)
        self.assertEqual(
            "joint_not_final",
            initial["scan_authority"]["reason"],
        )

        self.controller._Controller__scan_authority_snapshot = {
            **self.controller._Controller__scan_authority_snapshot,
            "final": True,
            "full": True,
            "outcome": "adopt",
            "reason": "source_buckets_adopted",
            "effective_local_reconciliation_after_count": 1,
        }
        changed = self.controller.get_model_summary(max_age_seconds=60)
        self.assertIsNot(initial, changed)
        self.assertEqual("adopt", changed["scan_authority"]["outcome"])
        self.assertEqual(1, changed["scan_authority"]["effective_local_reconciliation_after_count"])
        self.assertIs(changed, self.controller.get_model_summary(max_age_seconds=60))

    def test_scan_authority_publication_id_is_monotonic_and_summary_aligned(self):
        self.controller._Controller__model.version = 4
        self.controller._Controller__model.iter_files.return_value = []
        self.controller._Controller__model_builder.local_library_inventory_snapshot.return_value = (0, {})
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()

        first = self.controller._publish_scan_authority_snapshot({
            "outcome": "no_op",
            "reason": "joint_not_final",
            "unknown_overlay_after_count": 1,
        })
        first_summary = self.controller.get_model_summary()
        second = self.controller._publish_scan_authority_snapshot({
            "outcome": "adopt",
            "reason": "source_buckets_adopted",
            "unknown_overlay_after_count": 0,
        })
        second_summary = self.controller.get_model_summary()

        self.assertEqual(1, first["publication_id"])
        self.assertEqual(2, second["publication_id"])
        self.assertEqual(
            first["publication_id"], first_summary["scan_authority"]["publication_id"],
        )
        self.assertEqual(
            second["publication_id"], second_summary["scan_authority"]["publication_id"],
        )
        self.assertEqual(4, second_summary["scan_authority"]["model_version"])
        self.assertEqual(0, second_summary["scan_authority"]["unknown_overlay_after_count"])

    def test_model_summary_cache_refreshes_identity_without_semantic_invalidation(self):
        self.controller._Controller__model.version = 4
        self.controller._Controller__model.iter_files.return_value = []
        self.controller._Controller__model_builder.local_library_inventory_snapshot.return_value = (0, {})
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()

        self.controller._publish_scan_authority_snapshot({
            "outcome": "no_op",
            "reason": "joint_not_final",
            "unknown_overlay_after_count": 0,
        })
        initial = self.controller.get_model_summary(max_age_seconds=60)
        first_publication_id = initial["scan_authority"]["publication_id"]

        self.controller._publish_scan_authority_snapshot({
            "outcome": "no_op",
            "reason": "joint_not_final",
            "unknown_overlay_after_count": 0,
        })
        refreshed = self.controller.get_model_summary(max_age_seconds=60)

        self.assertIs(initial, refreshed)
        self.assertGreater(refreshed["scan_authority"]["publication_id"], first_publication_id)
        self.assertEqual("joint_not_final", refreshed["scan_authority"]["reason"])

    @patch("controller.controller.ScannerProcess")
    def test_refresh_path_pairs_rebuilds_runtime_state_and_forces_rescan(self, scanner_process_cls):
        pair_a = PathPair(
            id="pair-a",
            name="Pair Alpha",
            remote_path="/remote/source-a",
            local_path="/data/root-a",
            enabled=True,
            auto_queue=False,
        )
        old_active_process = self.controller._Controller__active_scan_process
        old_local_process = self.controller._Controller__local_scan_process
        old_remote_process = self.controller._Controller__remote_scan_process
        old_active_scanner = self.controller._Controller__active_scanner
        validate_process = self.controller._Controller__validate_process
        model_builder = self.controller._Controller__model_builder

        self.controller._Controller__context.path_pair_manager = MagicMock()
        self.controller._Controller__context.path_pair_manager.get_enabled_pairs.return_value = [pair_a]
        self.controller._Controller__context.config.lftp.staging_path = None
        self.controller._Controller__context.config.lftp.use_temp_file = False
        self.controller._Controller__context.config.controller.managed_extract_folders_enabled = False
        self.controller._Controller__context.config.controller.interval_ms_downloading_scan = 100
        self.controller._Controller__context.config.controller.interval_ms_local_scan = 200
        self.controller._Controller__context.config.controller.interval_ms_remote_scan = 300
        self.controller._Controller__context.config.lftp.remote_address = "host"
        self.controller._Controller__context.config.lftp.remote_port = 22
        self.controller._Controller__context.config.lftp.remote_username = "user"
        self.controller._Controller__context.config.lftp.remote_path_to_scan_script = "/scanfs"
        self.controller._Controller__context.args.local_path_to_scanfs = "/local-scanfs"
        self.controller._Controller__started = True
        self.controller._Controller__last_remote_reconciliation_healthy = True
        self.controller._Controller__last_local_reconciliation_healthy = True
        self.controller._Controller__reconciled_local_path_pair_ids = {"pair-a"}
        self.controller._Controller__reconciled_remote_path_pair_ids = {"pair-a"}
        self.controller._Controller__active_downloading_file_names = [("sample-a.bin", "pair-a", "Pair Alpha")]
        self.controller._Controller__active_extracting_file_names = []
        self.controller._Controller__set_active_scanner_files = MagicMock()
        old_active_process.is_alive.return_value = False
        old_local_process.is_alive.return_value = False
        old_remote_process.is_alive.return_value = False
        new_active_process = MagicMock()
        new_local_process = MagicMock()
        new_remote_process = MagicMock()
        scanner_process_cls.side_effect = [new_active_process, new_local_process, new_remote_process]

        with patch.object(self.controller, "_Controller__preflight_runtime_storage_roots") as preflight:
            self.controller._Controller__apply_path_pair_refresh()

        preflight.assert_called_once_with(
            [pair_a],
            {"pair-a": os.path.join("/data/root-a", "incomplete")},
            "/data/root-a",
            os.path.join("/data/root-a", "incomplete"),
        )
        old_active_process.terminate.assert_called_once_with()
        old_local_process.terminate.assert_called_once_with()
        old_remote_process.terminate.assert_called_once_with()
        old_active_process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        old_local_process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        old_remote_process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        old_active_process.close_queues.assert_called_once_with()
        old_local_process.close_queues.assert_called_once_with()
        old_remote_process.close_queues.assert_called_once_with()
        old_active_scanner.close.assert_called_once_with()

        new_active_process.start.assert_called_once_with()
        new_local_process.start.assert_called_once_with()
        new_remote_process.start.assert_called_once_with()
        new_active_process.force_scan.assert_called_once_with()
        new_local_process.force_scan.assert_called_once_with()
        new_remote_process.force_scan.assert_called_once_with()
        self.controller._Controller__set_active_scanner_files.assert_called_once_with(
            [("sample-a.bin", "pair-a", "Pair Alpha")]
        )

        validate_process.set_path_pairs_by_id.assert_called_once()
        refreshed_pairs = validate_process.set_path_pairs_by_id.call_args.args[0]
        self.assertEqual(["pair-a"], list(refreshed_pairs.keys()))
        self.assertIs(pair_a, refreshed_pairs["pair-a"])

        self.controller._Controller__lftp.set_path_pairs.assert_called_once()
        lftp_pairs = self.controller._Controller__lftp.set_path_pairs.call_args.args[0]
        self.assertEqual(1, len(lftp_pairs))
        self.assertEqual(os.path.join("/data/root-a", "incomplete"), lftp_pairs[0].local_path)

        model_builder.set_local_root_paths.assert_called_once_with(
            {None: "/data/root-a", "pair-a": "/data/root-a"},
            {
                None: "/data/root-a/incomplete",
                "pair-a": os.path.join("/data/root-a", "incomplete")
            }
        )
        self.assertEqual({"pair-a"}, set(self.controller._Controller__path_pairs_by_id.keys()))
        self.assertEqual(
            os.path.join("/data/root-a", "incomplete"),
            self.controller._Controller__path_pair_staging_paths["pair-a"]
        )
        self.assertFalse(self.controller._Controller__last_remote_reconciliation_healthy)
        self.assertFalse(self.controller._Controller__last_local_reconciliation_healthy)
        self.assertFalse(self.controller.is_path_pair_reconciled("pair-a"))

    def test_local_inventory_runtime_generation_invalidates_changed_scopes_and_removes_disabled_scopes(self):
        pair_a = PathPair(id="pair-a", name="Pair A", remote_path="/remote/a", local_path="/local/a")
        pair_b = PathPair(id="pair-b", name="Pair B", remote_path="/remote/b", local_path="/local/b")
        local_a = SystemFile("file-a", 7); local_a.path_pair_id = "pair-a"
        local_b = SystemFile("file-b", 11); local_b.path_pair_id = "pair-b"
        builder = ModelBuilder()
        builder.set_local_files([local_a, local_b])
        builder.record_local_inventory_completion({"pair-a", "pair-b"})
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = Model()
        self.controller._Controller__model_lock = threading.RLock()

        # Restarting the same runtime keeps last-good values but makes them
        # non-authoritative until the replacement generation completes.
        self.controller._Controller__begin_local_inventory_runtime_generation(
            {"pair-a": pair_a, "pair-b": pair_b},
            {"pair-a": "/local/a/incomplete", "pair-b": "/local/b/incomplete"},
            {"pair-a": pair_a, "pair-b": pair_b},
            {"pair-a": "/local/a/incomplete", "pair-b": "/local/b/incomplete"},
        )
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("scanning", inventory["pair-a"].state)
        self.assertEqual(7, inventory["pair-a"].size)

        # A relocated/configured identity retains its values only while the
        # replacement scan is in progress; changed roots are not failures.
        relocated_a = PathPair(id="pair-a", name="Pair A", remote_path="/remote/a", local_path="/local/a-new")
        self.controller._Controller__begin_local_inventory_runtime_generation(
            {"pair-a": pair_a, "pair-b": pair_b},
            {"pair-a": "/local/a/incomplete", "pair-b": "/local/b/incomplete"},
            {"pair-a": relocated_a},
            {"pair-a": "/local/a-new/incomplete"},
        )
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("scanning", inventory["pair-a"].state)
        self.assertNotIn("pair-b", inventory)

        # A real failed result is stale and a same-identity retry cannot turn
        # that failure evidence into scanning.
        builder.observe_local_scan_result({"pair-a"}, set(), {"pair-a"}, True, {"pair-a"})
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("stale", inventory["pair-a"].state)
        self.controller._Controller__begin_local_inventory_runtime_generation(
            {"pair-a": relocated_a}, {"pair-a": "/local/a-new/incomplete"},
            {"pair-a": relocated_a}, {"pair-a": "/local/a-new/incomplete"},
        )
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("stale", inventory["pair-a"].state)

        # Re-enabling a removed scope has no retained runtime authority.
        self.controller._Controller__begin_local_inventory_runtime_generation(
            {"pair-a": relocated_a}, {"pair-a": "/local/a-new/incomplete"},
            {"pair-a": relocated_a, "pair-b": pair_b},
            {"pair-a": "/local/a-new/incomplete", "pair-b": "/local/b/incomplete"},
        )
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertNotIn("pair-b", inventory)

    @patch("controller.controller.ScannerProcess")
    def test_refresh_path_pairs_resyncs_pair_scoped_download_timestamps(self, scanner_process_cls):
        movies_pair = PathPair(
            id="movies", name="Movies", remote_path="/remote/movies",
            local_path="/local/movies", enabled=True, auto_queue=False,
        )
        tv_pair = PathPair(
            id="tv", name="TV", remote_path="/remote/tv",
            local_path="/local/tv", enabled=True, auto_queue=False,
        )
        config = self.controller._Controller__context.config
        config.lftp.staging_path = None
        config.lftp.use_temp_file = False
        config.controller.managed_extract_folders_enabled = False
        config.controller.interval_ms_downloading_scan = 100
        config.controller.interval_ms_local_scan = 200
        config.controller.interval_ms_remote_scan = 300
        config.lftp.remote_address = "host"
        config.lftp.remote_port = 22
        config.lftp.remote_username = "user"
        config.lftp.remote_path_to_scan_script = "/scanfs"
        self.controller._Controller__context.args.local_path_to_scanfs = "/local-scanfs"
        self.controller._Controller__updater = ModelUpdater(self.controller)
        movies_id = ModelFile.build_file_id("same.mkv", "movies")
        tv_id = ModelFile.build_file_id("same.mkv", "tv")
        self.controller._Controller__persist.downloaded_file_names = {movies_id, tv_id}
        self.controller._Controller__persist.downloaded_timestamps = {
            movies_id: 1760000100.0,
            tv_id: 1760000200.0,
        }
        scanner_process_cls.side_effect = [MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()]

        self.controller._Controller__refresh_path_pair_runtime_state([movies_pair])
        self.controller._Controller__model_builder.set_downloaded_timestamps.assert_called_with({
            movies_id: 1760000100.0,
        })
        self.controller._Controller__refresh_path_pair_runtime_state([tv_pair])
        self.controller._Controller__model_builder.set_downloaded_timestamps.assert_called_with({
            tv_id: 1760000200.0,
        })

    @patch("controller.controller.ScannerProcess")
    def test_refresh_path_pairs_skips_old_active_scanner_close_when_old_active_process_stays_alive(self, scanner_process_cls):
        movies_pair = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/local/movies",
            enabled=True,
            auto_queue=False,
        )
        old_active_process = self.controller._Controller__active_scan_process
        old_local_process = self.controller._Controller__local_scan_process
        old_remote_process = self.controller._Controller__remote_scan_process
        old_active_scanner = self.controller._Controller__active_scanner

        self.controller._Controller__context.path_pair_manager = MagicMock()
        self.controller._Controller__context.path_pair_manager.get_enabled_pairs.return_value = [movies_pair]
        self.controller._Controller__context.config.lftp.staging_path = None
        self.controller._Controller__context.config.lftp.use_temp_file = False
        self.controller._Controller__context.config.controller.managed_extract_folders_enabled = False
        self.controller._Controller__context.config.controller.interval_ms_downloading_scan = 100
        self.controller._Controller__context.config.controller.interval_ms_local_scan = 200
        self.controller._Controller__context.config.controller.interval_ms_remote_scan = 300
        self.controller._Controller__context.config.lftp.remote_address = "host"
        self.controller._Controller__context.config.lftp.remote_port = 22
        self.controller._Controller__context.config.lftp.remote_username = "user"
        self.controller._Controller__context.config.lftp.remote_path_to_scan_script = "/scanfs"
        self.controller._Controller__context.args.local_path_to_scanfs = "/local-scanfs"
        self.controller._Controller__started = True
        self.controller._Controller__set_active_scanner_files = MagicMock()
        old_active_process.is_alive.return_value = True
        old_active_process.name = "old active scan process"
        old_local_process.is_alive.return_value = False
        old_remote_process.is_alive.return_value = False
        new_active_process = MagicMock()
        new_local_process = MagicMock()
        new_remote_process = MagicMock()
        scanner_process_cls.side_effect = [new_active_process, new_local_process, new_remote_process]

        with patch.object(self.controller, "_Controller__preflight_runtime_storage_roots"):
            self.controller._Controller__apply_path_pair_refresh()

        old_active_process.terminate.assert_called_once_with()
        old_active_process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        old_active_process.close_queues.assert_not_called()
        old_active_scanner.close.assert_not_called()
        old_local_process.close_queues.assert_called_once_with()
        old_remote_process.close_queues.assert_called_once_with()

    def test_refresh_path_pairs_marks_pending_refresh_when_started(self):
        self.controller._Controller__started = True

        self.controller.refresh_path_pairs()

        self.assertTrue(self.controller._Controller__path_pair_refresh_requested)

    def test_process_keeps_running_after_path_pair_refresh_failure(self):
        self.controller._Controller__started = True
        self.controller.refresh_path_pairs()
        self.controller._Controller__refresh_path_pair_runtime_state = MagicMock(side_effect=RuntimeError("activation failed"))
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()

        self.controller.process()

        self.assertFalse(self.controller._Controller__context.status.server.up)
        self.assertIn("activation failed", self.controller._Controller__context.status.server.error_msg)
        self.assertEqual(1, self.controller._Controller__path_pair_refresh_completed_generation)
        self.controller._Controller__updater.update.assert_called_once()
        self.controller._Controller__log_memory_usage.assert_called_once()

    def test_process_marks_refresh_completed_for_consumed_generation_only(self):
        self.controller._Controller__started = True
        self.controller.refresh_path_pairs()

        def bump_generation():
            self.controller.refresh_path_pairs()

        self.controller._Controller__apply_path_pair_refresh = MagicMock(side_effect=bump_generation)
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()

        self.controller.process()

        self.assertEqual(2, self.controller._Controller__path_pair_refresh_generation)
        self.assertEqual(1, self.controller._Controller__path_pair_refresh_completed_generation)
        self.assertTrue(self.controller._Controller__path_pair_refresh_requested)

    def test_refresh_path_pairs_clears_runtime_error_after_recovery(self):
        self.controller._Controller__started = True
        self.controller.refresh_path_pairs()
        self.controller._Controller__refresh_path_pair_runtime_state = MagicMock(side_effect=[
            RuntimeError("activation failed"),
            None,
        ])
        self.controller._Controller__updater.update = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()

        self.controller.process()

        self.assertFalse(self.controller._Controller__context.status.server.up)
        self.assertIn("activation failed", self.controller._Controller__context.status.server.error_msg)

        self.controller.refresh_path_pairs()
        self.controller.process()

        self.assertTrue(self.controller._Controller__context.status.server.up)
        self.assertIsNone(self.controller._Controller__context.status.server.error_msg)
        self.assertIsNone(self.controller._Controller__path_pair_runtime_error)

    def test_process_applies_pending_path_pair_refresh_before_model_update(self):
        call_order = []
        self.controller._Controller__started = True
        self.controller._Controller__path_pair_refresh_requested = True
        self.controller._Controller__propagate_exceptions = MagicMock(side_effect=lambda: call_order.append("propagate"))
        self.controller._Controller__cleanup_commands = MagicMock(side_effect=lambda: call_order.append("cleanup"))
        self.controller._Controller__process_commands = MagicMock(side_effect=lambda: call_order.append("commands"))
        self.controller._Controller__apply_path_pair_refresh = MagicMock(side_effect=lambda: call_order.append("refresh"))
        self.controller._Controller__updater.update = MagicMock(side_effect=lambda: call_order.append("update"))
        self.controller._Controller__log_memory_usage = MagicMock(side_effect=lambda: call_order.append("memory"))

        self.controller.process()

        self.assertEqual(["propagate", "cleanup", "commands", "refresh", "update", "memory"], call_order)
        self.assertFalse(self.controller._Controller__path_pair_refresh_requested)

    def test_update_model_preserves_stale_lftp_statuses_after_cache_age_expires_during_unhealthy_poll(self):
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__lftp.status.side_effect = [
            [status],
            LftpError("bad status"),
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        self.controller._Controller__update_model()
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
        self.controller._Controller__lftp_status_cache_expires_at = datetime.now() - timedelta(seconds=1)
        self.controller._Controller__lftp.status.side_effect = AssertionError("should not poll once cache expires during cooldown")
        self.controller._Controller__update_model()

        self.assertEqual(2, self.controller._Controller__lftp.status.call_count)
        self.assertEqual(
            [[status], [status], [status]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_not_called()
        self.assertEqual(3, self.controller._Controller__active_scanner.set_active_files.call_count)
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["a"])

    def test_update_model_resumes_lftp_status_polling_after_retry_window_expires(self):
        status_a = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status_b = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "b", "")
        self.controller._Controller__lftp.status.side_effect = [
            [status_a],
            LftpError("bad status"),
            [status_b],
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        self.controller._Controller__update_model()
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        self.controller._Controller__update_model()

        self.assertEqual(3, self.controller._Controller__lftp.status.call_count)
        self.assertEqual(
            [[status_a], [status_a], [status_b]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["a"])
        self.assertCountEqual(
            ["a", "b"],
            self.controller._Controller__active_scanner.set_active_files.call_args_list[-1].args[0]
        )

    def test_lftp_status_refresh_timing_tracks_downloading_scan_interval(self):
        self.assertEqual((1, 3), Controller._Controller__lftp_status_refresh_timing(100))
        self.assertEqual((1, 3), Controller._Controller__lftp_status_refresh_timing(1000))

    def test_temp_diag_dedupes_repeated_payloads(self):
        self.controller._Controller__temp_diag_file_id = "rf"
        self.controller._Controller__temp_diag_last_signature = None

        with patch("builtins.print") as print_mock:
            self.controller._Controller__temp_diag("update_model", lftp_status_source="cached_error")
            self.controller._Controller__temp_diag("update_model", lftp_status_source="cached_error")
            self.controller._Controller__temp_diag("update_model", lftp_status_source="fresh_healthy")

        self.assertEqual(2, print_mock.call_count)
        self.assertTrue(print_mock.call_args_list[0].args[0].startswith("TEMP_DIAG "))

    def test_update_model_sets_remote_scan_failure_status_from_partial_result(self):
        partial_file = ModelFile("partial", False)
        latest_remote_scan = SimpleNamespace(
            timestamp=object(),
            files=[partial_file],
            failed=True,
            error_message="Failed to scan remote path for pair 'TV': temporary remote failure"
        )
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = latest_remote_scan
        self.controller._Controller__context.status.controller = SimpleNamespace(
            latest_remote_scan_time=None,
            latest_remote_scan_failed=None,
            latest_remote_scan_error=None
        )

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_remote_files.assert_not_called()
        self.assertIs(latest_remote_scan.timestamp, self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual(
            "Failed to scan remote path for pair 'TV': temporary remote failure",
            self.controller._Controller__context.status.controller.latest_remote_scan_error
        )

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_failed_remote_scan_preserves_snapshot_and_next_healthy_scan_reconciles(self, _):
        self.controller._Controller__persist.downloaded_file_names = {"existing"}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None, managed_extract_file_ids=[]
        )
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[ModelFile("partial", False)], failed=True, error_message="remote failed"
        )

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_remote_files.assert_not_called()
        self.assertEqual({"existing"}, self.controller._Controller__persist.downloaded_file_names)

        healthy_file = ModelFile("healthy", False)
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[healthy_file], failed=False, error_message=None
        )
        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_remote_files.assert_called_once_with([healthy_file])

    def test_update_model_filters_only_malformed_status_only_active_entries(self):
        status_a = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status_b = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "b", "")
        self.controller._Controller__lftp.status.return_value = [status_a, status_b]
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(),
            files=[],
            malformed_status_only_file_ids={"a"}
        )

        self.controller._Controller__update_model()

        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with(["b"])
        self.controller._Controller__model_builder.set_lftp_statuses.assert_called_once_with([status_b])

    def test_successful_final_move_handoff_quarantines_stale_active_root_until_absent(self):
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        completion_file_id = active_file.file_id
        self.controller._Controller__persist.final_move_succeeded_file_names = {completion_file_id}
        self.controller._Controller__successful_final_move_handoff_file_ids = {completion_file_id}
        self.controller._Controller__active_scan_process.pop_latest_result.side_effect = [
            SimpleNamespace(
                files=[active_file],
                malformed_status_only_file_ids=set(),
                failed=False,
            ),
            SimpleNamespace(
                files=[],
                malformed_status_only_file_ids=set(),
                failed=False,
            ),
        ]

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_active_files.assert_called_once_with([])
        self.assertEqual(
            {completion_file_id},
            self.controller._Controller__successful_final_move_handoff_file_ids,
        )

        self.controller._Controller__model_builder.set_active_files.reset_mock()
        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_active_files.assert_called_once_with([])
        self.assertEqual(set(), self.controller._Controller__successful_final_move_handoff_file_ids)

    def test_update_model_keeps_malformed_status_only_suppression_across_missing_active_scan_cycle(self):
        status_a = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status_b = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "b", "")
        self.controller._Controller__lftp.status.return_value = [status_a, status_b]
        self.controller._Controller__active_scan_process.pop_latest_result.side_effect = [
            SimpleNamespace(
                timestamp=object(),
                files=[],
                malformed_status_only_file_ids={"a"}
            ),
            None
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.assertEqual(2, self.controller._Controller__active_scanner.set_active_files.call_count)
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["b"])
        self.assertEqual(
            [[status_b], [status_b]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )

    def test_update_model_keeps_malformed_suppression_when_next_active_scan_is_empty(self):
        status_a = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status_b = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "b", "")
        self.controller._Controller__lftp.status.return_value = [status_a, status_b]
        self.controller._Controller__active_scan_process.pop_latest_result.side_effect = [
            SimpleNamespace(
                timestamp=object(),
                files=[],
                malformed_status_only_file_ids={"a"}
            ),
            SimpleNamespace(
                timestamp=object(),
                files=[],
                malformed_status_only_file_ids=[]
            )
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.assertEqual(2, self.controller._Controller__active_scanner.set_active_files.call_count)
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["b"])
        self.assertEqual(
            [[status_b], [status_b]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )

    def test_update_model_clears_malformed_suppression_when_lftp_activity_drops_file_id(self):
        status_a = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status_b = LftpJobStatus(1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "b", "")
        self.controller._Controller__active_scan_process.pop_latest_result.side_effect = [
            SimpleNamespace(
                timestamp=object(),
                files=[],
                malformed_status_only_file_ids={"a"}
            ),
            None,
            None,
        ]
        self.controller._Controller__lftp.status.side_effect = [
            [status_a, status_b],
            [status_b],
            [status_a],
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.assertEqual(3, self.controller._Controller__active_scanner.set_active_files.call_count)
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["b"])
        self.assertCountEqual(
            ["a", "b"],
            self.controller._Controller__active_scanner.set_active_files.call_args_list[-1].args[0]
        )
        self.assertEqual(
            [[status_b], [status_b], [status_a]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )

    def test_update_model_allows_listener_reentry_into_get_model_files(self):
        new_model = Model()
        new_model.set_base_logger(self.controller.logger)
        new_file = ModelFile("fresh", False)
        new_model.add_file(new_file)

        callback_started = threading.Event()
        callback_finished = threading.Event()
        callback_result = {}
        worker_errors = []

        class ReentrantListener(IModelListener):
            def __init__(self, controller):
                self._controller = controller

            def file_added(self, file: ModelFile):
                callback_started.set()
                callback_result["model_files"] = self._controller.get_model_files()
                callback_finished.set()

            def file_removed(self, file: ModelFile):
                raise AssertionError("Unexpected file_removed callback")

            def file_updated(self, old_file: ModelFile, new_file: ModelFile):
                raise AssertionError("Unexpected file_updated callback")

        context = self._make_startup_context(local_path="/local")
        with patch("controller.controller.create_transfer_backend") as create_transfer_backend_mock, \
                patch("controller.controller.ScannerProcess") as scanner_process_cls, \
                patch("controller.controller.ExtractProcess"), \
                patch("controller.controller.ValidateProcess"), \
                patch("controller.controller.MultiprocessingLogger"):
            create_transfer_backend_mock.return_value = MagicMock()
            scanner_process_cls.side_effect = [MagicMock(), MagicMock(), MagicMock()]
            controller = Controller(context, ControllerPersist())

        self.assertEqual(
            [False, False, True],
            [call.kwargs["recycle_scan_worker"] for call in scanner_process_cls.call_args_list],
        )

        controller._Controller__lftp.status.return_value = []
        controller._Controller__lftp.last_status_poll_healthy = True
        controller._Controller__active_scan_process.pop_latest_result.return_value = None
        controller._Controller__local_scan_process.pop_latest_result.return_value = None
        controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        controller._Controller__extract_process.pop_latest_statuses.return_value = None
        controller._Controller__validate_process.pop_latest_statuses.return_value = None
        controller._Controller__extract_process.pop_completed.return_value = []
        controller._Controller__extract_process.pop_failed.return_value = []
        controller._Controller__model_builder = MagicMock()
        controller._Controller__model_builder.has_changes.return_value = True
        controller._Controller__model_builder.build_model.return_value = new_model
        controller._Controller__model.add_listener(ReentrantListener(controller))

        def run_update_model():
            try:
                controller._Controller__update_model()
            except Exception as exc:  # pragma: no cover - defensive capture
                worker_errors.append(exc)

        update_thread = threading.Thread(target=run_update_model, daemon=True)
        update_thread.start()

        self.assertTrue(callback_started.wait(2), "listener did not start")
        self.assertTrue(callback_finished.wait(2), "listener did not finish re-entering get_model_files()")
        update_thread.join(2)

        self.assertFalse(update_thread.is_alive(), "controller update deadlocked")
        self.assertEqual([], worker_errors)
        self.assertEqual([new_file.file_id], [file.file_id for file in callback_result["model_files"]])
        self.assertEqual("fresh", callback_result["model_files"][0].name)

    def test_get_model_files_uses_file_ids_when_available(self):
        file_movies = ModelFile("dup", False)
        file_movies.path_pair_id = "movies"
        file_tv = ModelFile("dup", False)
        file_tv.path_pair_id = "tv"
        self.controller._Controller__model.get_file_ids = MagicMock(return_value={
            file_movies.file_id,
            file_tv.file_id
        })
        self.controller._Controller__model.get_file.side_effect = lambda identifier: {
            file_movies.file_id: file_movies,
            file_tv.file_id: file_tv
        }[identifier]
        self.controller._Controller__model.published_file.side_effect = lambda identifier: SimpleNamespace(
            download_progress=None,
            transferred_size=None,
            downloading_speed=None,
            eta=None,
        )

        model_files = self.controller.get_model_files()

        self.assertEqual({file_movies.file_id, file_tv.file_id}, {file.file_id for file in model_files})

    def test_process_commands_stop_reports_transfer_backend_status_parser_errors(self):
        file = ModelFile("example", False)
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.kill.side_effect = LftpJobStatusParserError("bad status")

        command = Controller.Command(Controller.Command.Action.STOP, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("Transfer backend error: bad status", 500)
        callback.on_success.assert_not_called()

    def test_process_commands_stop_reports_missing_lftp_job_as_failure(self):
        file = ModelFile("example", False)
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.kill.return_value = False

        command = Controller.Command(Controller.Command.Action.STOP, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("File 'example' could not be stopped", 409)
        callback.on_success.assert_not_called()
        self.assertNotIn(file.file_id, self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_stop_rejects_downloads_without_status_sidecar(self):
        model_builder = ModelBuilder()
        model_builder.set_remote_files([SystemFile("example", 100, False)])
        local_file = SystemFile("example", 10, False, is_staging=True)
        local_file.status_sidecar_ready = False
        model_builder.set_local_files([local_file])
        downloading_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "example", "")
        downloading_status.total_transfer_state = LftpJobStatus.TransferState(10, 100, 10, 100, 10)
        model_builder.set_lftp_statuses([downloading_status])
        self.controller._Controller__model = model_builder.build_model()
        file = self.controller._Controller__model.get_file("example")

        self.assertEqual(ModelFile.State.DOWNLOADING, file.state)
        self.assertFalse(file.is_stoppable)

        command = Controller.Command(Controller.Command.Action.STOP, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("File 'example' could not be stopped", 409)
        callback.on_success.assert_not_called()
        self.assertEqual(ModelFile.State.DOWNLOADING, file.state)
        self.assertNotIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
        self.controller._Controller__lftp.kill.assert_not_called()

    def test_process_commands_stop_allows_running_get_without_pget_sidecar(self):
        model_builder = ModelBuilder()
        model_builder.set_remote_files([SystemFile("example", 100, False)])
        local_file = SystemFile("example", 10, False, is_staging=True)
        local_file.status_sidecar_ready = False
        model_builder.set_local_files([local_file])
        get_status = LftpJobStatus(
            0, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING, "example", "",
        )
        get_status.total_transfer_state = LftpJobStatus.TransferState(10, 100, 10, 100, 10)
        model_builder.set_lftp_statuses([get_status])
        self.controller._Controller__model = model_builder.build_model()
        self.controller._Controller__lftp.kill.return_value = True

        command = Controller.Command(Controller.Command.Action.STOP, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        self.controller._Controller__lftp.kill.assert_called_once()

    def test_process_commands_stop_uses_current_active_scan_readiness_when_model_flag_lags(self):
        file = ModelFile("example", False)
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = False
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__active_scan_ready_file_ids = {file.file_id}
        self.controller._Controller__lftp.kill.return_value = True

        command = Controller.Command(Controller.Command.Action.STOP, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        self.controller._Controller__lftp.kill.assert_called_once_with(
            "example", path_pair_id=None, remote_path=None, local_path=None,
        )
        self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_reports_not_found_as_404(self):
        self.controller._Controller__model.get_file.side_effect = ModelError("missing")

        command = Controller.Command(Controller.Command.Action.QUEUE, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("File 'example' not found", 404)
        callback.on_success.assert_not_called()

    def test_process_commands_reports_wrong_state_as_409(self):
        file = ModelFile("example", False)
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.STOP, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("File 'example' is not Queued or Downloading", 409)
        callback.on_success.assert_not_called()

    def test_propagate_exceptions_ignores_pending_transfer_backend_errors(self):
        self.controller._Controller__extract_process.pid = 123
        self.controller._Controller__validate_process.pid = 456
        self.controller._Controller__lftp.raise_pending_error.side_effect = LftpError("pending failure")

        self.controller._Controller__propagate_exceptions()

        self.controller.logger.warning.assert_called_once_with("Caught transfer backend error: pending failure")
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__remote_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_called_once_with()
        self.controller._Controller__extract_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__validate_process.propagate_exception.assert_called_once_with()

    def test_propagate_exceptions_ignores_extract_and_validate_worker_failures(self):
        self.controller._Controller__extract_process.pid = 123
        self.controller._Controller__validate_process.pid = 456
        self.controller._Controller__remote_scan_process.propagate_exception.return_value = None
        self.controller._Controller__local_scan_process.propagate_exception.return_value = None
        self.controller._Controller__active_scan_process.propagate_exception.return_value = None
        self.controller._Controller__mp_logger.propagate_exception.return_value = None
        self.controller._Controller__extract_process.propagate_exception.side_effect = Exception("extract failed")
        self.controller._Controller__validate_process.propagate_exception.side_effect = Exception("validate failed")

        self.controller._Controller__propagate_exceptions()

        self.controller.logger.warning.assert_any_call(
            "Ignoring extract worker failure during controller loop: extract failed",
            exc_info=True
        )
        self.controller.logger.warning.assert_any_call(
            "Ignoring validate worker failure during controller loop: validate failed",
            exc_info=True
        )
        self.controller._Controller__extract_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__validate_process.propagate_exception.assert_called_once_with()

    def test_propagate_exceptions_reports_dead_extract_and_validate_workers_once(self):
        self.controller._Controller__extract_process.pid = 123
        self.controller._Controller__validate_process.pid = 456
        self.controller._Controller__remote_scan_process.propagate_exception.return_value = None
        self.controller._Controller__local_scan_process.propagate_exception.return_value = None
        self.controller._Controller__active_scan_process.propagate_exception.return_value = None
        self.controller._Controller__mp_logger.propagate_exception.return_value = None
        self.controller._Controller__extract_process.propagate_exception.side_effect = [
            Exception("extract failed"),
            None,
        ]
        self.controller._Controller__validate_process.propagate_exception.side_effect = [
            Exception("validate failed"),
            None,
        ]
        self.controller._Controller__extract_process.is_alive.return_value = False
        self.controller._Controller__validate_process.is_alive.return_value = False

        self.controller._Controller__propagate_exceptions()
        self.controller._Controller__propagate_exceptions()

        self.controller.logger.error.assert_any_call(
            "%s worker has died; %s is disabled until restart.",
            "extract",
            "extract"
        )
        self.controller.logger.error.assert_any_call(
            "%s worker has died; %s is disabled until restart.",
            "validate",
            "validate"
        )
        self.assertEqual(2, self.controller.logger.error.call_count)
        self.assertEqual(2, self.controller._Controller__extract_process.propagate_exception.call_count)
        self.assertEqual(2, self.controller._Controller__validate_process.propagate_exception.call_count)

    def test_propagate_exceptions_does_not_report_alive_extract_and_validate_workers_dead(self):
        self.controller._Controller__remote_scan_process.propagate_exception.return_value = None
        self.controller._Controller__local_scan_process.propagate_exception.return_value = None
        self.controller._Controller__active_scan_process.propagate_exception.return_value = None
        self.controller._Controller__mp_logger.propagate_exception.return_value = None
        self.controller._Controller__extract_process.propagate_exception.return_value = None
        self.controller._Controller__validate_process.propagate_exception.return_value = None
        self.controller._Controller__extract_process.pid = 1
        self.controller._Controller__validate_process.pid = 1
        self.controller._Controller__extract_process.is_alive.return_value = True
        self.controller._Controller__validate_process.is_alive.return_value = True

        self.controller._Controller__propagate_exceptions()

        self.controller.logger.error.assert_not_called()
        self.controller._Controller__extract_process.is_alive.assert_called_once_with()
        self.controller._Controller__validate_process.is_alive.assert_called_once_with()

    def test_propagate_exceptions_skips_dormant_auxiliary_workers(self):
        self.controller._Controller__remote_scan_process.propagate_exception.return_value = None
        self.controller._Controller__local_scan_process.propagate_exception.return_value = None
        self.controller._Controller__active_scan_process.propagate_exception.return_value = None
        self.controller._Controller__mp_logger.propagate_exception.return_value = None

        self.controller._Controller__propagate_exceptions()

        self.controller._Controller__extract_process.propagate_exception.assert_not_called()
        self.controller._Controller__validate_process.propagate_exception.assert_not_called()
        self.controller._Controller__extract_process.is_alive.assert_not_called()
        self.controller._Controller__validate_process.is_alive.assert_not_called()
        self.controller.logger.error.assert_not_called()

    def test_propagate_exceptions_reports_workers_dead_once_when_is_alive_raises(self):
        self.controller._Controller__remote_scan_process.propagate_exception.return_value = None
        self.controller._Controller__local_scan_process.propagate_exception.return_value = None
        self.controller._Controller__active_scan_process.propagate_exception.return_value = None
        self.controller._Controller__mp_logger.propagate_exception.return_value = None
        self.controller._Controller__extract_process.propagate_exception.return_value = None
        self.controller._Controller__validate_process.propagate_exception.return_value = None
        self.controller._Controller__extract_process.pid = 1
        self.controller._Controller__validate_process.pid = 1
        self.controller._Controller__extract_process.is_alive.side_effect = AssertionError("not started")
        self.controller._Controller__validate_process.is_alive.side_effect = ValueError("already closed")

        self.controller._Controller__propagate_exceptions()
        self.controller._Controller__propagate_exceptions()

        self.controller.logger.error.assert_any_call(
            "%s worker has died; %s is disabled until restart.",
            "extract",
            "extract"
        )
        self.controller.logger.error.assert_any_call(
            "%s worker has died; %s is disabled until restart.",
            "validate",
            "validate"
        )
        self.assertEqual(2, self.controller.logger.error.call_count)
        self.controller._Controller__extract_process.is_alive.assert_called_once_with()
        self.controller._Controller__validate_process.is_alive.assert_called_once_with()

    def test_propagate_exceptions_records_first_remote_scan_failure(self):
        self.controller._Controller__context.status.controller = SimpleNamespace(
            latest_remote_scan_time=None,
            latest_remote_scan_failed=None,
            latest_remote_scan_error=None
        )
        self.controller._Controller__remote_scan_process.propagate_exception.side_effect = AppError("remote failed")

        with self.assertRaises(AppError) as ctx:
            self.controller._Controller__propagate_exceptions()

        self.assertEqual("remote failed", str(ctx.exception))
        self.assertIsNotNone(self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual("remote failed", self.controller._Controller__context.status.controller.latest_remote_scan_error)
        self.controller.logger.warning.assert_called_once_with(
            "Fatal remote scan failure recorded: remote failed"
        )
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_not_called()
        self.controller._Controller__extract_process.propagate_exception.assert_not_called()
        self.controller._Controller__validate_process.propagate_exception.assert_not_called()

    def test_propagate_exceptions_records_first_remote_scan_runtime_failure(self):
        self.controller._Controller__context.status.controller = SimpleNamespace(
            latest_remote_scan_time=None,
            latest_remote_scan_failed=None,
            latest_remote_scan_error=None
        )
        self.controller._Controller__remote_scan_process.propagate_exception.side_effect = FileNotFoundError("missing scanfs")

        with self.assertRaises(FileNotFoundError) as ctx:
            self.controller._Controller__propagate_exceptions()

        self.assertEqual("missing scanfs", str(ctx.exception))
        self.assertIsNotNone(self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual("missing scanfs", self.controller._Controller__context.status.controller.latest_remote_scan_error)
        self.controller.logger.warning.assert_called_once_with(
            "Fatal remote scan failure recorded: missing scanfs"
        )
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_not_called()
        self.controller._Controller__extract_process.propagate_exception.assert_not_called()
        self.controller._Controller__validate_process.propagate_exception.assert_not_called()

    def test_propagate_exceptions_records_fatal_remote_failure_after_prior_recoverable_status(self):
        existing_time = object()
        self.controller._Controller__context.status.controller = SimpleNamespace(
            latest_remote_scan_time=existing_time,
            latest_remote_scan_failed=True,
            latest_remote_scan_error="fatal remote error"
        )
        self.controller._Controller__remote_scan_process.propagate_exception.side_effect = FileNotFoundError("fatal remote error")

        with self.assertRaises(FileNotFoundError) as ctx:
            self.controller._Controller__propagate_exceptions()

        self.assertEqual("fatal remote error", str(ctx.exception))
        self.assertIsNot(existing_time, self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual("fatal remote error", self.controller._Controller__context.status.controller.latest_remote_scan_error)
        self.controller.logger.warning.assert_called_once_with(
            "Fatal remote scan failure recorded: fatal remote error"
        )
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_not_called()
        self.controller._Controller__extract_process.propagate_exception.assert_not_called()
        self.controller._Controller__validate_process.propagate_exception.assert_not_called()

    def test_update_model_sets_multi_path_active_scan_entries(self):
        self.controller._Controller__active_scanner = MultiPathActiveScanner({})
        self.controller._Controller__active_scanner.set_active_files = MagicMock()
        status = MagicMock()
        status.state = LftpJobStatus.State.RUNNING
        status.name = "dup"
        status.path_pair_id = "movies"
        status.path_pair_name = "Movies"
        self.controller._Controller__lftp.status.return_value = [status]

        self.controller._Controller__update_model()

        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([
            ("dup", "movies", "Movies")
        ])

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_prunes_stale_downloaded_file_names(self, _):
        keep_id = ModelFile.build_file_id("keep", None)
        stale_id = ModelFile.build_file_id("stale", None)
        self.controller._Controller__persist.downloaded_file_names = {keep_id, stale_id}
        self.controller._Controller__persist.downloaded_timestamps = {
            keep_id: 10.0,
            stale_id: 20.0,
        }
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = self.controller._Controller__model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(),
            files=[],
            failed=False,
            error_message=None
        )
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(),
            files=[],
            failed=False,
            error_message=None,
            managed_extract_file_ids=[],
        )
        self.controller._Controller__model.get_file_ids.return_value = {keep_id}
        self.controller._Controller__model.get_file_names.return_value = {"keep"}

        self.controller._Controller__update_model()

        self.assertEqual({keep_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({keep_id: 10.0}, self.controller._Controller__persist.downloaded_timestamps)
        self.controller._Controller__model_builder.set_downloaded_files.assert_called_once_with({keep_id})

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_prunes_stale_extracted_and_final_move_markers(self, _):
        keep_id = ModelFile.build_file_id("keep", None)
        stale_id = ModelFile.build_file_id("stale", None)
        self.controller._Controller__persist.extracted_file_names = {keep_id, stale_id}
        self.controller._Controller__persist.final_move_succeeded_file_names = {keep_id, stale_id}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = self.controller._Controller__model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(),
            files=[],
            failed=False,
            error_message=None
        )
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(),
            files=[],
            failed=False,
            error_message=None,
            managed_extract_file_ids=[],
        )
        self.controller._Controller__model.get_file_ids.return_value = {keep_id}
        self.controller._Controller__model.get_file_names.return_value = {"keep"}

        self.controller._Controller__update_model()

        self.assertEqual({keep_id}, self.controller._Controller__persist.extracted_file_names)
        self.assertEqual({keep_id}, self.controller._Controller__persist.final_move_succeeded_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_failed_local_scan_preserves_snapshot_and_history(self, _):
        self.controller._Controller__persist.downloaded_file_names = {"existing"}
        self.controller._Controller__persist.extracted_file_names = {"existing"}
        self.controller._Controller__persist.final_move_succeeded_file_names = {"existing"}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None
        )
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=True, error_message="local scan failed"
        )
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_local_files.assert_not_called()
        self.assertEqual({"existing"}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({"existing"}, self.controller._Controller__persist.extracted_file_names)
        self.assertEqual({"existing"}, self.controller._Controller__persist.final_move_succeeded_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_prunes_ambiguous_and_stale_markers_with_multiple_pairs(self, _):
        self.controller._Controller__path_pairs_by_id = {"movies": MagicMock(), "tv": MagicMock()}
        self.controller._Controller__persist.downloaded_file_names = {
            "same.mkv",
            ModelFile.build_file_id("same.mkv", "tv"),
        }
        self.controller._Controller__persist.extracted_file_names = {
            "same.mkv",
            ModelFile.build_file_id("same.mkv", "tv"),
        }
        self.controller._Controller__persist.final_move_succeeded_file_names = {
            "same.mkv",
            ModelFile.build_file_id("same.mkv", "tv"),
        }
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        healthy_scan = SimpleNamespace(timestamp=object(), files=[], failed=False, error_message=None)
        healthy_local = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None, managed_extract_file_ids=[]
        )
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = healthy_scan
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = healthy_local
        self.controller._Controller__model.get_file_ids.return_value = {
            ModelFile.build_file_id("same.mkv", "movies")
        }
        self.controller._Controller__model.get_file_names.return_value = {"same.mkv"}

        self.controller._Controller__update_model()

        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__persist.extracted_file_names)
        self.assertEqual(set(), self.controller._Controller__persist.final_move_succeeded_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_prunes_preboundary_scoped_persist_keys(self, _):
        uuid_pair = "12345678-1234-1234-1234-123456789abc"
        self.controller._Controller__path_pairs_by_id = {"movies": MagicMock(), uuid_pair: MagicMock()}
        active_movie_id = ModelFile.build_file_id("active.mkv", "movies")
        active_uuid_id = ModelFile.build_file_id("active.mkv", uuid_pair)
        pending_name = "pending.mkv"
        pending_id = ModelFile.build_file_id(pending_name, "movies")
        stale_movie_key = persist_key("movies", "stale.mkv")
        stale_uuid_colon_key = f"{uuid_pair}:stale.mkv"
        pending_key = persist_key("movies", pending_name)
        for attr in ("downloaded_file_names", "extracted_file_names", "final_move_succeeded_file_names"):
            setattr(self.controller._Controller__persist, attr, {
                persist_key("movies", "active.mkv"),
                f"{uuid_pair}:active.mkv",
                stale_movie_key,
                stale_uuid_colon_key,
                pending_key,
            })
        self.controller._Controller__pending_completion_file_names = {(pending_name, "movies", "Movies")}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None
        )
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None, managed_extract_file_ids=[]
        )
        self.controller._Controller__model.get_file_ids.return_value = {active_movie_id, active_uuid_id}
        self.controller._Controller__model.get_file_names.return_value = {"active.mkv"}

        self.controller._Controller__update_model()

        for attr in ("downloaded_file_names", "extracted_file_names", "final_move_succeeded_file_names"):
            self.assertEqual(set(), getattr(self.controller._Controller__persist, attr))

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_preserves_disabled_canonical_markers_and_prunes_preboundary_forms(self, _):
        self.controller._Controller__path_pairs_by_id = {"movies": MagicMock()}
        stale_movie_key = persist_key("movies", "stale.mkv")
        unknown_pair = "87654321-4321-4321-4321-abcdefabcdef"
        unknown_canonical = ModelFile.build_file_id("orphan-json.mkv", unknown_pair)
        unknown_markers = {
            persist_key("disabled", "orphan.mkv"),
            unknown_canonical,
            f"{unknown_pair}:orphan-legacy.mkv",
        }
        for attr in ("downloaded_file_names", "extracted_file_names", "final_move_succeeded_file_names"):
            setattr(self.controller._Controller__persist, attr, {stale_movie_key, *unknown_markers})
        self.controller._Controller__model_builder.has_changes.return_value = True
        authoritative_model = Model()
        authoritative_model.set_base_logger(self.controller.logger)
        authoritative_file = ModelFile("orphan-json.mkv", False)
        authoritative_file.path_pair_id = unknown_pair
        authoritative_model.add_file(authoritative_file)
        self.controller._Controller__model_builder.build_model.return_value = authoritative_model
        healthy_remote = SimpleNamespace(timestamp=object(), files=[], failed=False, error_message=None)
        healthy_local = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None, managed_extract_file_ids=[]
        )
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = healthy_remote
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = healthy_local
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()

        self.controller._Controller__update_model()
        for attr in ("downloaded_file_names", "extracted_file_names", "final_move_succeeded_file_names"):
            self.assertEqual({unknown_canonical}, getattr(self.controller._Controller__persist, attr))

        # Re-enabling the pair restores only its own retained canonical state.
        self.controller._Controller__path_pairs_by_id[unknown_pair] = MagicMock()
        self.controller._Controller__update_model()
        for attr in ("downloaded_file_names", "extracted_file_names", "final_move_succeeded_file_names"):
            self.assertEqual({unknown_canonical}, getattr(self.controller._Controller__persist, attr))

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_path_pair_refresh_invalidates_reconciliation_authority_until_healthy_scans(self, _):
        stale_key = persist_key("movies", "stale.mkv")
        self.controller._Controller__path_pairs_by_id = {"movies": MagicMock()}
        for attr in ("downloaded_file_names", "extracted_file_names", "final_move_succeeded_file_names"):
            setattr(self.controller._Controller__persist, attr, {stale_key})
        self.controller._Controller__last_remote_reconciliation_healthy = True
        self.controller._Controller__last_local_reconciliation_healthy = True
        self.controller._Controller__active_scan_lftp_roots_awaiting = {"stale-root"}
        self.controller._Controller__active_scan_lftp_roots_seen = {"stale-root"}
        self.controller._Controller__refresh_path_pair_runtime_state = MagicMock()
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()
        self.controller._Controller__lftp.status.return_value = []

        self.controller._Controller__apply_path_pair_refresh()
        self.assertFalse(self.controller._Controller__last_remote_reconciliation_healthy)
        self.assertFalse(self.controller._Controller__last_local_reconciliation_healthy)
        self.assertEqual(set(), self.controller._Controller__active_scan_lftp_roots_awaiting)
        self.assertEqual(set(), self.controller._Controller__active_scan_lftp_roots_seen)

        # The new scan runtime must not let a root retained by the old runtime
        # wake an otherwise authoritative idle poller.
        self.controller._Controller__lftp.status.reset_mock()
        self.controller._Controller__lftp_idle_status_authoritative = True
        self.controller._Controller__next_lftp_status_poll_at = None
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = ScannerResult(
            datetime.now(), [SystemFile("stale-root", 1, False)],
        )
        self.controller._Controller__update_model()
        self.controller._Controller__lftp.status.assert_not_called()

        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__update_model()
        self.assertEqual({stale_key}, self.controller._Controller__persist.downloaded_file_names)

        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None
        )
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None, managed_extract_file_ids=[]
        )
        self.controller._Controller__update_model()
        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_reconciles_v086_fixture_markers_after_healthy_empty_scans(self, _):
        fixture_path = Path(__file__).parents[2] / "fixtures" / "upgrade_v086_ff2a" / "controller.persist"
        persisted = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.controller._Controller__persist.downloaded_file_names = set(persisted["downloaded"])
        self.controller._Controller__persist.extracted_file_names = set(persisted["extracted"])
        self.controller._Controller__persist.final_move_succeeded_file_names = set(persisted["downloaded"])
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None
        )
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None, managed_extract_file_ids=[]
        )
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()

        self.controller._Controller__update_model()

        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__persist.extracted_file_names)
        self.assertEqual(set(), self.controller._Controller__persist.final_move_succeeded_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_prunes_stale_terminal_move_metadata_after_remote_reconciliation(self, _):
        keep_id = ModelFile.build_file_id("keep", None)
        stale_id = ModelFile.build_file_id("stale", None)
        self.controller._Controller__persist.move_failure_counts = {keep_id: 4, stale_id: 4}
        self.controller._Controller__move_retry_due = {stale_id: datetime.now()}
        self.controller._Controller__deferred_move_file_ids = {stale_id}
        self.controller._Controller__move_attempt_reservations = {stale_id}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = self.controller._Controller__model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None
        )
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None, managed_extract_file_ids=[]
        )
        self.controller._Controller__model.get_file_ids.return_value = {keep_id}
        self.controller._Controller__model.get_file_names.return_value = {"keep"}

        self.controller._Controller__update_model()

        self.assertEqual({keep_id: 4}, self.controller._Controller__persist.move_failure_counts)
        self.assertNotIn(stale_id, self.controller._Controller__move_retry_due)
        self.assertNotIn(stale_id, self.controller._Controller__deferred_move_file_ids)
        self.assertNotIn(stale_id, self.controller._Controller__move_attempt_reservations)

    def test_update_model_forwards_stopped_file_names(self):
        self.controller._Controller__persist.stopped_file_names = {"stopped-id"}

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_stopped_files.assert_called_once_with({"stopped-id"})

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_does_not_fabricate_startup_timestamp_for_persisted_download(self, diff_models):
        persisted_file = ModelFile("restart.mkv", False)
        persisted_file.path_pair_id = "movies"
        persisted_file.state = ModelFile.State.DOWNLOADED

        self.controller._Controller__persist.downloaded_file_names = {persisted_file.file_id}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__model.get_file_ids.return_value = {persisted_file.file_id}
        self.controller._Controller__model.get_file_names.return_value = {persisted_file.name}
        diff_models.return_value = [MagicMock(change=ModelDiff.Change.ADDED, new_file=persisted_file)]

        for existing_timestamp in (None, 123.0):
            with self.subTest(existing_timestamp=existing_timestamp):
                self.controller._Controller__persist.downloaded_timestamps = (
                    {} if existing_timestamp is None else {persisted_file.file_id: existing_timestamp}
                )

                self.controller._Controller__update_model()

                expected_timestamps = (
                    {} if existing_timestamp is None else {persisted_file.file_id: existing_timestamp}
                )
                self.assertEqual(
                    expected_timestamps,
                    self.controller._Controller__persist.downloaded_timestamps,
                )

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_keeps_observed_start_timestamp_when_redownload_completes(self, diff_models):
        old_file = ModelFile("redownload.mkv", False)
        old_file.path_pair_id = "movies"
        old_file.state = ModelFile.State.DOWNLOADING
        new_file = ModelFile("redownload.mkv", False)
        new_file.path_pair_id = "movies"
        new_file.state = ModelFile.State.DOWNLOADED

        self.controller._Controller__persist.downloaded_file_names = {new_file.file_id}
        self.controller._Controller__persist.downloaded_timestamps = {new_file.file_id: 100.0}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__model.get_file_ids.return_value = {new_file.file_id}
        self.controller._Controller__model.get_file_names.return_value = {new_file.name}
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )
        diff_models.return_value = [
            MagicMock(change=ModelDiff.Change.UPDATED, old_file=old_file, new_file=new_file)
        ]

        with patch.object(Controller, "_download_timestamp_clock", return_value=datetime(2026, 1, 1, 0, 0, 2)):
            self.controller._Controller__update_model()

        self.assertEqual(
            {new_file.file_id: 100.0},
            self.controller._Controller__persist.downloaded_timestamps,
        )

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_keeps_downloaded_file_ids_when_new_download_completes(self, diff_models):
        added_file = ModelFile("keep", False)
        added_file.path_pair_id = "movies"
        added_file.state = ModelFile.State.DOWNLOADED

        stale_a = "[\"movies\",\"a\"]"
        stale_b = "[\"movies\",\"b\"]"
        self.controller._Controller__persist.downloaded_file_names = {stale_a, stale_b}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )
        self.controller._Controller__model.get_file_ids.return_value = {
            stale_a,
            stale_b,
            added_file.file_id
        }
        self.controller._Controller__model.get_file_names.return_value = {"a", "b", "keep"}
        diff_models.return_value = [MagicMock(change=ModelDiff.Change.ADDED, new_file=added_file)]

        self.controller._Controller__update_model()

        self.assertEqual(
            {stale_a, stale_b, added_file.file_id},
            self.controller._Controller__persist.downloaded_file_names
        )
        self.assertEqual(
            {stale_a, stale_b, added_file.file_id},
            self.controller._Controller__model_builder.set_downloaded_files.call_args_list[-1][0][0]
        )

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_removes_stale_extracted_file_names_when_new_download_completes(self, diff_models):
        added_file = ModelFile("archive.zip", False)
        added_file.path_pair_id = "movies"
        added_file.state = ModelFile.State.DOWNLOADED

        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__persist.extracted_file_names = {added_file.file_id}
        self.controller._Controller__model = Model()
        self.controller._Controller__model.set_base_logger(self.controller.logger)
        self.controller._Controller__model_builder.has_changes.return_value = True
        new_model = Model()
        new_model.set_tree_file_count(2)
        self.controller._Controller__model_builder.build_model.return_value = new_model
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )
        self.controller._Controller__download_start_state[added_file.file_id] = DownloadStartLifecycleEntry(
            "notified", added_file.path_pair_id, datetime.now()
        )
        complete_lifecycle = self.controller._complete_download_start_lifecycle
        self.controller._complete_download_start_lifecycle = MagicMock(
            side_effect=lambda file_id: (
                self.assertIn(file_id, self.controller._Controller__persist.downloaded_file_names),
                complete_lifecycle(file_id),
            )
        )
        diff_models.return_value = [MagicMock(change=ModelDiff.Change.ADDED, new_file=added_file)]

        self.controller._Controller__update_model()

        self.assertEqual(2, self.controller._Controller__model.tree_file_count)

        self.assertEqual({added_file.file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__persist.extracted_file_names)
        self.assertNotIn(added_file.file_id, self.controller._Controller__download_start_state)
        self.controller._Controller__model_builder.set_extracted_files.assert_called_with(set())

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_handles_removed_diff_without_new_file(self, diff_models):
        old_file = ModelFile("removed.bin", False)
        old_file.path_pair_id = "movies"
        old_file.state = ModelFile.State.DOWNLOADED

        pending_entry = ("removed.bin", "movies", "Movies")
        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        current_model.add_file(old_file)
        new_model = Model()
        new_model.set_base_logger(self.controller.logger)

        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = new_model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = SimpleNamespace(
            files=[],
            timestamp=datetime.now(),
        )
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = {pending_entry}
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.REMOVED,
                old_file=old_file,
                new_file=None
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual(set(), self.controller._Controller__model.get_file_ids())
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_keeps_pending_completion_until_local_completion_proof(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 900
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        partial_file = ModelFile("movie.mkv", False)
        partial_file.path_pair_id = "movies"
        partial_file.remote_size = 1000
        partial_file.local_size = 900
        partial_file.state = ModelFile.State.DOWNLOADING
        partial_model = Model()
        partial_model.set_base_logger(self.controller.logger)
        partial_model.add_file(partial_file)

        terminal_file = ModelFile("movie.mkv", False)
        terminal_file.path_pair_id = "movies"
        terminal_file.remote_size = 1000
        terminal_file.local_size = 1000
        terminal_file.state = ModelFile.State.DOWNLOADED
        terminal_model = Model()
        terminal_model.set_base_logger(self.controller.logger)
        terminal_model.add_file(terminal_file)

        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.side_effect = [True, True]
        self.controller._Controller__model_builder.build_model.side_effect = [
            partial_model,
            terminal_model
        ]
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__active_scanner = MultiPathActiveScanner({})
        self.controller._Controller__active_scanner.set_active_files = MagicMock()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )
        self.controller._Controller__prev_downloading_file_names = {completion_entry}
        diff_models.side_effect = [
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=active_file,
                    new_file=partial_file
                )
            ],
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=partial_file,
                    new_file=terminal_file
                )
            ]
        ]

        self.controller._Controller__update_model()
        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({completion_entry}, self.controller._Controller__pending_completion_file_names)
        self.controller._Controller__active_scanner.set_active_files.assert_called_with([completion_entry])

        self._authorize_pending_move("movies")
        self.controller._Controller__update_model()
        self.assertEqual({completion_file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_pending_completion_listener_keeps_progress_floor_through_terminal_move_state(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 990
        active_file.transferred_size = 990
        active_file.download_progress = 99
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        partial_file = ModelFile("movie.mkv", False)
        partial_file.path_pair_id = "movies"
        partial_file.remote_size = 1000
        partial_file.local_size = 990
        partial_file.transferred_size = 0
        partial_file.download_progress = 0
        partial_file.state = ModelFile.State.DOWNLOADING
        partial_model = Model()
        partial_model.set_base_logger(self.controller.logger)
        partial_model.add_file(partial_file)

        move_failed_file = ModelFile("movie.mkv", False)
        move_failed_file.path_pair_id = "movies"
        move_failed_file.remote_size = 1000
        move_failed_file.local_size = 1000
        move_failed_file.transferred_size = 0
        move_failed_file.download_progress = 0
        move_failed_file.state = ModelFile.State.MOVE_FAILED
        move_failed_model = Model()
        move_failed_model.set_base_logger(self.controller.logger)
        move_failed_model.add_file(move_failed_file)

        listener = MagicMock()
        current_model.add_listener(listener)
        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.side_effect = [True, True]
        self.controller._Controller__model_builder.build_model.side_effect = [
            partial_model,
            move_failed_model,
        ]
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = {completion_entry}
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.FAILED
        )
        diff_models.side_effect = [
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=active_file,
                    new_file=partial_file,
                )
            ],
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=partial_file,
                    new_file=move_failed_file,
                )
            ],
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.assertEqual(2, listener.file_updated.call_count)
        first_update = listener.file_updated.call_args_list[0].args[1]
        second_update = listener.file_updated.call_args_list[1].args[1]
        self.assertEqual(completion_file_id, first_update.file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, first_update.state)
        self.assertEqual(99, first_update.download_progress)
        self.assertEqual(990, first_update.transferred_size)
        self.assertEqual(ModelFile.State.MOVE_FAILED, second_update.state)
        self.assertEqual(99, second_update.download_progress)
        self.assertEqual(990, second_update.transferred_size)
        self.assertEqual(
            {completion_file_id},
            {
                ModelFile.build_file_id(file_name, path_pair_id)
                for file_name, path_pair_id, _ in self.controller._Controller__pending_completion_file_names
            },
        )
        other_pair_file = ModelFile("movie.mkv", False)
        other_pair_file.path_pair_id = "tv"
        other_pair_file.download_progress = 0
        ModelUpdater._preserve_pending_completion_progress_floor(
            active_file,
            other_pair_file,
            {completion_file_id},
        )
        self.assertEqual(0, other_pair_file.download_progress)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_pending_completion_listener_never_publishes_incomplete_candidate_after_local_only(self, diff_models):
        """Local Only 100% reaches its terminal handoff without a lower listener row."""
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        local_only = ModelFile("movie.mkv", False)
        local_only.path_pair_id = "movies"
        local_only.remote_size = 1000
        local_only.local_size = 1000
        local_only.transferred_size = 1000
        local_only.download_progress = 100
        local_only.local_present = True
        local_only.remote_has_transferable_content = False
        local_only.state = ModelFile.State.DOWNLOADING
        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        current_model.add_file(local_only)
        self.assertEqual("local_only", Controller._model_record_visible_state(local_only))

        incomplete = ModelFile("movie.mkv", False)
        incomplete.path_pair_id = "movies"
        incomplete.remote_size = 1000
        incomplete.local_size = 950
        incomplete.transferred_size = 950
        incomplete.download_progress = 95
        incomplete.local_present = True
        incomplete.remote_has_transferable_content = True
        incomplete.state = ModelFile.State.DEFAULT
        candidate = Model()
        candidate.set_base_logger(self.controller.logger)
        candidate.add_file(incomplete)

        listener = MagicMock()
        current_model.add_listener(listener)
        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = candidate
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = {completion_entry}
        self.controller._Controller__pending_completion_progress_floors = {
            completion_file_id: (100, 1000),
        }
        self.controller._Controller__pending_completion_publications = {
            completion_file_id: _PendingCompletionPublication(
                ActiveProgressOverlay(100, 1000, None, None), (7, "pget"),
            ),
        }
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.UPDATED,
                old_file=local_only,
                new_file=incomplete,
            )
        ]

        self.controller._Controller__update_model()

        self.assertGreaterEqual(listener.file_updated.call_count, 1)
        for published in (call.args[1] for call in listener.file_updated.call_args_list):
            self.assertEqual(ModelFile.State.DOWNLOADING, published.state)
            self.assertEqual(100, published.download_progress)
            self.assertEqual(1000, published.transferred_size)
            self.assertEqual("downloading", Controller._model_record_visible_state(published))
            self.assertNotEqual("stopped", Controller._model_record_visible_state(published))

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_pending_completion_reset_does_not_retain_stale_progress(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 990
        active_file.transferred_size = 990
        active_file.download_progress = 99
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        reset_file = ModelFile("movie.mkv", False)
        reset_file.path_pair_id = "movies"
        reset_file.remote_size = 1000
        reset_file.local_size = None
        reset_file.transferred_size = None
        reset_file.download_progress = None
        reset_file.state = ModelFile.State.DEFAULT
        reset_model = Model()
        reset_model.set_base_logger(self.controller.logger)
        reset_model.add_file(reset_file)

        listener = MagicMock()
        current_model.add_listener(listener)
        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = reset_model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = {completion_entry}
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.UPDATED,
                old_file=active_file,
                new_file=reset_file,
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual(1, listener.file_updated.call_count)
        reset_update = listener.file_updated.call_args.args[1]
        self.assertEqual(completion_file_id, reset_update.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, reset_update.state)
        self.assertIsNone(reset_update.download_progress)
        self.assertIsNone(reset_update.transferred_size)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)

    def test_pending_completion_floor_caps_transferred_size_after_remote_shrink(self):
        old_file = ModelFile("movie.mkv", False)
        old_file.path_pair_id = "movies"
        old_file.remote_size = 1000
        old_file.transferred_size = 990
        old_file.download_progress = 99

        new_file = ModelFile("movie.mkv", False)
        new_file.path_pair_id = "movies"
        new_file.remote_size = 500
        new_file.local_size = 100
        new_file.transferred_size = 0
        new_file.download_progress = 0
        new_file.state = ModelFile.State.DOWNLOADING

        ModelUpdater._preserve_pending_completion_progress_floor(
            old_file,
            new_file,
            {old_file.file_id},
        )

        self.assertEqual(500, new_file.transferred_size)
        self.assertLessEqual(new_file.transferred_size, new_file.remote_size)
        self.assertGreaterEqual(new_file.download_progress, 0)
        self.assertLessEqual(new_file.download_progress, 100)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_pending_completion_reappearance_keeps_floor_after_transient_model_removal(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 990
        active_file.transferred_size = 990
        active_file.download_progress = 99
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        removed_model = Model()
        removed_model.set_base_logger(self.controller.logger)

        reappeared_file = ModelFile("movie.mkv", False)
        reappeared_file.path_pair_id = "movies"
        reappeared_file.remote_size = 1000
        reappeared_file.local_size = 990
        reappeared_file.transferred_size = 0
        reappeared_file.download_progress = 0
        reappeared_file.state = ModelFile.State.DOWNLOADING
        reappeared_model = Model()
        reappeared_model.set_base_logger(self.controller.logger)
        reappeared_model.add_file(reappeared_file)

        listener = MagicMock()
        current_model.add_listener(listener)
        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.side_effect = [True, True]
        self.controller._Controller__model_builder.build_model.side_effect = [
            removed_model,
            reappeared_model,
        ]
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = {completion_entry}
        diff_models.side_effect = [
            [
                SimpleNamespace(
                    change=ModelDiff.Change.REMOVED,
                    old_file=active_file,
                    new_file=None,
                )
            ],
            [
                SimpleNamespace(
                    change=ModelDiff.Change.ADDED,
                    old_file=None,
                    new_file=reappeared_file,
                )
            ],
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.assertEqual(1, listener.file_removed.call_count)
        self.assertEqual(1, listener.file_added.call_count)
        added_file = listener.file_added.call_args.args[0]
        self.assertEqual(completion_file_id, added_file.file_id)
        self.assertEqual(99, added_file.download_progress)
        self.assertEqual(990, added_file.transferred_size)

    def test_update_model_prunes_floor_cache_after_pending_identity_is_removed(self):
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")
        self.controller._Controller__pending_completion_file_names = set()
        self.controller._Controller__pending_completion_progress_floors = {
            completion_file_id: (99, 990),
        }
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__model_builder.has_changes.return_value = False

        self.controller._Controller__update_model()

        self.assertEqual({}, self.controller._Controller__pending_completion_progress_floors)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_direct_download_move_failure_seeds_floor_for_next_move_failed_update(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 990
        active_file.transferred_size = 990
        active_file.download_progress = 99
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        terminal_file = ModelFile("movie.mkv", False)
        terminal_file.path_pair_id = "movies"
        terminal_file.path_pair_name = "Movies"
        terminal_file.remote_size = 1000
        terminal_file.local_size = 1000
        terminal_file.transferred_size = 990
        terminal_file.download_progress = 99
        terminal_file.state = ModelFile.State.DOWNLOADED
        terminal_model = Model()
        terminal_model.set_base_logger(self.controller.logger)
        terminal_model.add_file(terminal_file)

        move_failed_file = ModelFile("movie.mkv", False)
        move_failed_file.path_pair_id = "movies"
        move_failed_file.path_pair_name = "Movies"
        move_failed_file.remote_size = 1000
        move_failed_file.local_size = 1000
        move_failed_file.transferred_size = 0
        move_failed_file.download_progress = 0
        move_failed_file.state = ModelFile.State.MOVE_FAILED
        move_failed_model = Model()
        move_failed_model.set_base_logger(self.controller.logger)
        move_failed_model.add_file(move_failed_file)

        listener = MagicMock()
        current_model.add_listener(listener)
        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.side_effect = [True, True]
        self.controller._Controller__model_builder.build_model.side_effect = [
            terminal_model,
            move_failed_model,
        ]
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = set()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.FAILED
        )
        diff_models.side_effect = [
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=active_file,
                    new_file=terminal_file,
                )
            ],
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=terminal_file,
                    new_file=move_failed_file,
                )
            ],
        ]

        self.controller._Controller__update_model()
        self.assertEqual(
            (99, 990),
            self.controller._Controller__pending_completion_progress_floors[completion_file_id],
        )
        self.controller._Controller__update_model()

        self.assertEqual(2, listener.file_updated.call_count)
        first_update = listener.file_updated.call_args_list[0].args[1]
        second_update = listener.file_updated.call_args_list[1].args[1]
        self.assertEqual(completion_file_id, first_update.file_id)
        self.assertEqual(ModelFile.State.DOWNLOADED, first_update.state)
        self.assertEqual(99, first_update.download_progress)
        self.assertEqual(ModelFile.State.MOVE_FAILED, second_update.state)
        self.assertEqual(99, second_update.download_progress)
        self.assertEqual(990, second_update.transferred_size)
        self.assertEqual(
            {completion_file_id},
            {
                ModelFile.build_file_id(file_name, path_pair_id)
                for file_name, path_pair_id, _ in self.controller._Controller__pending_completion_file_names
            },
        )

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_successful_final_move_does_not_mask_stale_destination_truncation(self, diff_models):
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 990
        active_file.transferred_size = 990
        active_file.download_progress = 99
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        terminal_file = ModelFile("movie.mkv", False)
        terminal_file.path_pair_id = "movies"
        terminal_file.remote_size = 1000
        terminal_file.local_size = 1000
        terminal_file.transferred_size = 990
        terminal_file.download_progress = 99
        terminal_file.state = ModelFile.State.DOWNLOADED
        terminal_model = Model()
        terminal_model.set_base_logger(self.controller.logger)
        terminal_model.add_file(terminal_file)

        stale_file = ModelFile("movie.mkv", False)
        stale_file.path_pair_id = "movies"
        stale_file.remote_size = 1000
        stale_file.local_size = 960
        stale_file.transferred_size = 960
        stale_file.download_progress = None
        stale_file.final_move_succeeded = True
        stale_file.state = ModelFile.State.DEFAULT
        stale_model = Model()
        stale_model.set_base_logger(self.controller.logger)
        stale_model.add_file(stale_file)

        listener = MagicMock()
        current_model.add_listener(listener)
        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        # These fixtures have no active LFTP status.  State that fact
        # explicitly so an unconfigured mock cannot masquerade as a pending
        # progressive delta and defer the ordinary full build.
        self.controller._Controller__model_builder.has_pending_active_transfer_delta.return_value = False
        self.controller._Controller__model_builder.build_model.side_effect = [
            terminal_model,
            stale_model,
        ]
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = set()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )
        diff_models.side_effect = [
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=active_file,
                    new_file=terminal_file,
                )
            ],
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=terminal_file,
                    new_file=stale_file,
                )
            ],
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.evict_active_file_ids.assert_called_once_with(
            {completion_file_id}
        )
        self.controller._Controller__active_scan_process.force_scan.assert_called_once_with()
        self.assertEqual(2, listener.file_updated.call_count)
        stale_update = listener.file_updated.call_args_list[1].args[1]
        self.assertEqual(completion_file_id, stale_update.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, stale_update.state)
        self.assertIsNone(stale_update.download_progress)
        self.assertEqual(960, stale_update.local_size)
        self.assertEqual(960, stale_update.transferred_size)
        self.assertTrue(stale_update.final_move_succeeded)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_successful_final_move_does_not_mask_remote_size_change(self, diff_models):
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 990
        active_file.transferred_size = 990
        active_file.download_progress = 99
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        terminal_file = ModelFile("movie.mkv", False)
        terminal_file.path_pair_id = "movies"
        terminal_file.remote_size = 1000
        terminal_file.local_size = 1000
        terminal_file.transferred_size = 990
        terminal_file.download_progress = 99
        terminal_file.state = ModelFile.State.DOWNLOADED
        terminal_model = Model()
        terminal_model.set_base_logger(self.controller.logger)
        terminal_model.add_file(terminal_file)

        changed_file = ModelFile("movie.mkv", False)
        changed_file.path_pair_id = "movies"
        changed_file.remote_size = 1200
        changed_file.local_size = 1000
        changed_file.transferred_size = 1000
        changed_file.download_progress = None
        changed_file.final_move_succeeded = True
        changed_file.state = ModelFile.State.DEFAULT
        changed_model = Model()
        changed_model.set_base_logger(self.controller.logger)
        changed_model.add_file(changed_file)

        listener = MagicMock()
        current_model.add_listener(listener)
        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        # There is no active transfer delta in this terminal reconciliation.
        self.controller._Controller__model_builder.has_pending_active_transfer_delta.return_value = False
        self.controller._Controller__model_builder.build_model.side_effect = [
            terminal_model,
            changed_model,
        ]
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = set()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )
        diff_models.side_effect = [
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=active_file,
                    new_file=terminal_file,
                )
            ],
            [
                SimpleNamespace(
                    change=ModelDiff.Change.UPDATED,
                    old_file=terminal_file,
                    new_file=changed_file,
                )
            ],
        ]

        self.controller._Controller__update_model()
        self.controller._Controller__update_model()

        self.assertEqual(2, listener.file_updated.call_count)
        changed_update = listener.file_updated.call_args_list[1].args[1]
        self.assertEqual(completion_file_id, changed_update.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, changed_update.state)
        self.assertIsNone(changed_update.download_progress)
        self.assertEqual(1200, changed_update.remote_size)
        self.assertEqual(1000, changed_update.local_size)
        self.assertEqual(1000, changed_update.transferred_size)
        self.assertTrue(changed_update.final_move_succeeded)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_applies_pending_completion_side_effects_once_for_terminal_update(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 900
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        terminal_file = ModelFile("movie.mkv", False)
        terminal_file.path_pair_id = "movies"
        terminal_file.remote_size = 1000
        terminal_file.local_size = 1000
        terminal_file.state = ModelFile.State.DOWNLOADED
        terminal_model = Model()
        terminal_model.set_base_logger(self.controller.logger)
        terminal_model.add_file(terminal_file)

        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = terminal_model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__prev_downloading_file_names = {completion_entry}
        self._authorize_pending_move("movies")
        self.controller.clear_extracted_marker = MagicMock()
        move_snapshots = []

        def move_from_staging(name, path_pair_id):
            move_snapshots.append(
                (
                    name,
                    path_pair_id,
                    set(self.controller._Controller__persist.downloaded_file_names),
                    self.controller.clear_extracted_marker.call_count,
                )
            )
            return True

        self.controller._Controller__move_from_staging = MagicMock(side_effect=move_from_staging)
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.UPDATED,
                old_file=active_file,
                new_file=terminal_file
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual([("movie.mkv", "movies", set(), 0)], move_snapshots)
        self.assertEqual({completion_file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)
        self.controller.clear_extracted_marker.assert_called_once_with(terminal_file)
        self.controller._Controller__move_from_staging.assert_called_once_with("movie.mkv", "movies")
        self.controller._Controller__model_builder.set_downloaded_files.assert_called_once_with({completion_file_id})

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_leaves_downloaded_aliases_untouched_when_staging_move_fails(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")
        plain_alias = "movie.mkv"
        scoped_alias = f"movies{KEY_SEP}movie.mkv"
        legacy_alias = "movies:movie.mkv"
        unrelated_marker = "other.mkv"

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 900
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        terminal_file = ModelFile("movie.mkv", False)
        terminal_file.path_pair_id = "movies"
        terminal_file.path_pair_name = "Movies"
        terminal_file.remote_size = 1000
        terminal_file.local_size = 1000
        terminal_file.state = ModelFile.State.DOWNLOADED
        terminal_model = Model()
        terminal_model.set_base_logger(self.controller.logger)
        terminal_model.add_file(terminal_file)

        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = terminal_model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__pending_completion_file_names = {completion_entry}
        self._authorize_pending_move("movies")
        self.controller._Controller__persist.downloaded_file_names = {
            plain_alias,
            scoped_alias,
            legacy_alias,
            unrelated_marker,
        }
        self.controller.clear_extracted_marker = MagicMock()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.FAILED
        )
        downloaded_file_snapshots = []
        self.controller._Controller__model_builder.set_downloaded_files.side_effect = (
            lambda files: downloaded_file_snapshots.append(set(files))
        )
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.UPDATED,
                old_file=active_file,
                new_file=terminal_file
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual(
            {plain_alias, scoped_alias, legacy_alias, unrelated_marker},
            self.controller._Controller__persist.downloaded_file_names,
        )
        self.assertEqual({completion_entry}, self.controller._Controller__pending_completion_file_names)
        self.controller._Controller__move_from_staging.assert_called_once_with("movie.mkv", "movies")
        self.assertEqual([], downloaded_file_snapshots)
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with("movies")
        self.controller.clear_extracted_marker.assert_not_called()
        self.controller.logger.warning.assert_any_call(
            "Keeping download completion pending after failed staging move: %s",
            completion_file_id,
        )

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_keeps_direct_download_transition_pending_when_staging_move_fails(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        completion_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)

        downloaded_file = ModelFile("movie.mkv", False)
        downloaded_file.path_pair_id = "movies"
        downloaded_file.path_pair_name = "Movies"
        downloaded_file.remote_size = 1000
        downloaded_file.local_size = 1000
        downloaded_file.state = ModelFile.State.DOWNLOADED
        downloaded_model = Model()
        downloaded_model.set_base_logger(self.controller.logger)
        downloaded_model.add_file(downloaded_file)

        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = downloaded_model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller.clear_extracted_marker = MagicMock()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.FAILED
        )
        downloaded_file_snapshots = []
        self.controller._Controller__model_builder.set_downloaded_files.side_effect = (
            lambda files: downloaded_file_snapshots.append(set(files))
        )
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.ADDED,
                old_file=None,
                new_file=downloaded_file
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({completion_entry}, self.controller._Controller__pending_completion_file_names)
        self.controller._Controller__move_from_staging.assert_called_once_with("movie.mkv", "movies")
        self.assertEqual([], downloaded_file_snapshots)
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with("movies")
        self.controller.clear_extracted_marker.assert_not_called()

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_does_not_mark_stopped_disappearing_download_as_downloaded(self, diff_models):
        stopped_entry = ("movie.mkv", "movies", "Movies")
        stopped_file_id = ModelFile.build_file_id("movie.mkv", "movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 900
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        removed_model = Model()
        removed_model.set_base_logger(self.controller.logger)

        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = removed_model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__prev_downloading_file_names = {stopped_entry}
        self.controller._Controller__persist.stopped_file_names = {stopped_file_id}
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.REMOVED,
                old_file=active_file,
                new_file=None
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)
        self.controller._Controller__active_scanner.set_active_files.assert_called_with([])

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_does_not_mark_partial_disappearing_download_as_downloaded(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 900
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        partial_file = ModelFile("movie.mkv", False)
        partial_file.path_pair_id = "movies"
        partial_file.remote_size = 1000
        partial_file.local_size = 900
        partial_file.state = ModelFile.State.DOWNLOADING
        partial_model = Model()
        partial_model.set_base_logger(self.controller.logger)
        partial_model.add_file(partial_file)

        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = partial_model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__prev_downloading_file_names = {completion_entry}
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.UPDATED,
                old_file=active_file,
                new_file=partial_file
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({completion_entry}, self.controller._Controller__pending_completion_file_names)
        self.controller._Controller__active_scanner.set_active_files.assert_called_with(["movie.mkv"])

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_clears_pending_completion_when_default_file_has_no_local_size(self, diff_models):
        completion_entry = ("movie.mkv", "movies", "Movies")
        other_pending_entry = ("movie.mkv", "tv", "TV")

        current_model = Model()
        current_model.set_base_logger(self.controller.logger)
        active_file = ModelFile("movie.mkv", False)
        active_file.path_pair_id = "movies"
        active_file.remote_size = 1000
        active_file.local_size = 900
        active_file.state = ModelFile.State.DOWNLOADING
        current_model.add_file(active_file)

        reset_file = ModelFile("movie.mkv", False)
        reset_file.path_pair_id = "movies"
        reset_file.remote_size = 1000
        reset_file.local_size = None
        reset_file.state = ModelFile.State.DEFAULT
        reset_model = Model()
        reset_model.set_base_logger(self.controller.logger)
        reset_model.add_file(reset_file)

        self.controller._Controller__model = current_model
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = reset_model
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__prev_downloading_file_names = {completion_entry}
        self.controller._Controller__pending_completion_file_names = {
            completion_entry,
            other_pending_entry,
        }
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.UPDATED,
                old_file=active_file,
                new_file=reset_file
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({other_pending_entry}, self.controller._Controller__pending_completion_file_names)
        self.controller._Controller__active_scanner.set_active_files.assert_called_with(["movie.mkv", "movie.mkv"])

    def test_clear_extracted_marker_does_not_clear_duplicate_names_across_path_pairs(self):
        file_a = ModelFile("archive.zip", False)
        file_a.path_pair_id = "movies"
        file_b = ModelFile("archive.zip", False)
        file_b.path_pair_id = "tv"

        self.controller._Controller__model = Model()
        self.controller._Controller__model.set_base_logger(self.controller.logger)
        self.controller._Controller__model.add_file(file_a)
        self.controller._Controller__model.add_file(file_b)
        self.controller._Controller__persist.extracted_file_names = {"archive.zip"}

        self.controller.clear_extracted_marker(file_a)

        self.assertEqual({"archive.zip"}, self.controller._Controller__persist.extracted_file_names)
        self.controller._Controller__model_builder.set_extracted_files.assert_not_called()

    def test_clear_extracted_marker_waits_for_persist_snapshot(self):
        iterating = threading.Event()
        continue_iteration = threading.Event()
        mutation_started = threading.Event()
        mutation_finished = threading.Event()

        class PausingSet(set):
            def __iter__(self):
                iterator = super().__iter__()
                yield next(iterator)
                iterating.set()
                if not continue_iteration.wait(2):
                    raise AssertionError("clear_extracted_marker did not run")
                yield from iterator

        file = ModelFile("archive.zip", False)
        persist = ControllerPersist()
        persist.extracted_file_names = PausingSet({file.file_id, "other"})
        self.controller._Controller__persist = persist
        self.controller._Controller__model.get_file_ids.return_value = {file.file_id}
        self.controller._Controller__model.get_file.return_value = file
        serialization_result = {}

        def serialize():
            serialization_result["content"] = persist.to_str()

        def clear_marker():
            mutation_started.set()
            self.controller.clear_extracted_marker(file)
            mutation_finished.set()

        serialization_thread = threading.Thread(target=serialize)
        serialization_thread.start()
        self.assertTrue(iterating.wait(2))

        mutation_thread = threading.Thread(target=clear_marker)
        mutation_thread.start()
        self.assertTrue(mutation_started.wait(2))
        self.assertFalse(mutation_finished.wait(0.05))
        continue_iteration.set()

        serialization_thread.join(2)
        mutation_thread.join(2)
        self.assertFalse(serialization_thread.is_alive())
        self.assertFalse(mutation_thread.is_alive())
        self.assertIn(
            file.file_id,
            json.loads(serialization_result["content"])["extracted"],
        )
        self.assertNotIn(file.file_id, persist.extracted_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_keeps_staging_only_completed_markers_from_repromoting_snapshot(self, diff_models):
        self.controller._Controller__persist.downloaded_file_names = {"archive.zip"}
        self.controller._Controller__persist.extracted_file_names = {"archive.zip"}
        self.controller._Controller__model = Model()
        self.controller._Controller__model.set_base_logger(self.controller.logger)
        self.controller._Controller__model_builder.has_changes.return_value = True

        staging_only_file = ModelFile("archive.zip", False)
        staging_only_file.state = ModelFile.State.DEFAULT
        staging_only_file.local_size = 100

        new_model = Model()
        new_model.set_base_logger(self.controller.logger)
        new_model.add_file(staging_only_file)
        self.controller._Controller__model_builder.build_model.return_value = new_model
        diff_models.return_value = [MagicMock(change=ModelDiff.Change.ADDED, new_file=staging_only_file)]

        self.controller._Controller__update_model()

        self.assertEqual(ModelFile.State.DEFAULT, self.controller._Controller__model.get_file("archive.zip").state)
        self.assertEqual({"archive.zip"}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({"archive.zip"}, self.controller._Controller__persist.extracted_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_reconsiders_pending_zero_byte_local_only_file_after_remote_reconciliation(self, diff_models):
        file = ModelFile("stale", False)
        file.path_pair_id = "movies"
        file.local_size = 0
        file.remote_size = None
        file.state = ModelFile.State.DEFAULT

        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__remote_scan_process.pop_latest_result.side_effect = [
            SimpleNamespace(
                timestamp=object(),
                files=[],
                failed=True,
                error_message="remote failed"
            ),
            SimpleNamespace(
                timestamp=object(),
                files=[],
                failed=False,
                error_message=None
            ),
        ]
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()
        diff_models.return_value = [SimpleNamespace(change=ModelDiff.Change.ADDED, new_file=file)]
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            self.controller._Controller__update_model()
            delete_local_process.assert_not_called()
            self.controller._Controller__model_builder.has_changes.return_value = False
            self.controller._Controller__update_model()

        delete_local_process.assert_called_once_with(
            local_path="/local/movies",
            file_name="stale"
        )
        delete_local_process.return_value.start.assert_called_once_with()
        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)
        self.assertEqual(1, len(self.controller._Controller__active_command_processes))
        self.assertEqual(file.file_id, self.controller._Controller__active_command_processes[0].file_id)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_releases_pending_zero_byte_auto_purge_only_for_covered_remote_pair(self, diff_models):
        file = ModelFile("stale", False)
        file.path_pair_id = "pair-b"
        file.local_size = 0
        file.remote_size = None
        file.state = ModelFile.State.DEFAULT

        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__remote_scan_process.pop_latest_result.side_effect = [
            SimpleNamespace(
                timestamp=object(), files=[], failed=True, error_message="remote failed",
                is_scan_final=False, is_targeted_scan=True,
                scanned_path_pair_ids={"pair-b"}, unknown_path_pair_ids={"pair-b"},
            ),
            SimpleNamespace(
                timestamp=object(), files=[], failed=False, error_message=None,
                is_scan_final=True, is_targeted_scan=True,
                scanned_path_pair_ids={"pair-a"}, unknown_path_pair_ids=set(),
            ),
            SimpleNamespace(
                timestamp=object(), files=[], failed=False, error_message=None,
                is_scan_final=True, is_targeted_scan=True,
                scanned_path_pair_ids={"pair-b"}, unknown_path_pair_ids=set(),
            ),
        ]
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()
        diff_models.return_value = [SimpleNamespace(change=ModelDiff.Change.ADDED, new_file=file)]
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(local_path="/local/a"),
            "pair-b": SimpleNamespace(local_path="/local/b"),
        }
        self.controller._Controller__path_pair_staging_paths = {
            "pair-a": "/local/a/incomplete",
            "pair-b": "/local/b/incomplete",
        }

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            self.controller._Controller__update_model()
            self.assertEqual({file.file_id}, self.controller._Controller__pending_auto_purge_file_ids)

            self.controller._Controller__model_builder.has_changes.return_value = False
            self.controller._Controller__update_model()
            delete_local_process.assert_not_called()
            self.assertEqual({file.file_id}, self.controller._Controller__pending_auto_purge_file_ids)

            self.controller._Controller__update_model()

        delete_local_process.assert_called_once_with(local_path="/local/b", file_name="stale")
        delete_local_process.return_value.start.assert_called_once_with()
        self.assertEqual(set(), self.controller._Controller__pending_auto_purge_file_ids)

    def test_update_model_skips_auto_purge_for_tracked_zero_byte_local_only_file(self):
        file = ModelFile("stale", False)
        file.path_pair_id = "movies"
        file.local_size = 0
        file.remote_size = None
        file.state = ModelFile.State.DEFAULT

        self.controller._Controller__persist.downloaded_file_names = {file.file_id}
        self.assertFalse(self.controller._Controller__should_auto_purge_local_file(file))

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_update_model_skips_auto_purge_for_queued_delete_command(self, diff_models):
        file = ModelFile("stale", False)
        file.path_pair_id = "movies"
        file.local_size = 0
        file.remote_size = None
        file.state = ModelFile.State.DEFAULT

        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(),
            files=[],
            failed=False,
            error_message=None
        )
        self.controller._Controller__model.get_file_ids.return_value = set()
        self.controller._Controller__model.get_file_names.return_value = set()
        diff_models.return_value = [SimpleNamespace(change=ModelDiff.Change.ADDED, new_file=file)]
        self.controller._Controller__command_queue.put(
            Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        )

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            self.controller._Controller__update_model()

        delete_local_process.assert_not_called()
        self.assertEqual([], self.controller._Controller__active_command_processes)

    def test_process_commands_queue_uses_path_pair_paths(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__exclude_patterns = "*.nfo,Sample/"
        self.controller._Controller__context.config.general.exclude_patterns = ""
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "dup",
            False,
            remote_base_dir_path="/remote/movies",
            local_base_dir_path="/local/movies/incomplete",
            exclude_patterns="*.nfo,Sample/"
        )
        self.assertEqual("eligible", self.controller._Controller__download_start_state[file.file_id].state)

    def test_process_commands_queue_uses_nested_pair_relative_path(self):
        root = ModelFile("release", True)
        file = ModelFile("movie.mkv", False)
        root.add_child(file)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {"movies": "/local/movies/incomplete"}

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "release/movie.mkv", False,
            remote_base_dir_path="/remote/movies", local_base_dir_path="/local/movies/incomplete",
        )

    def test_process_commands_resolve_serialized_nested_child_ids_for_all_manual_actions(self):
        def install_nested_child(state, *, local_size=None, remote_size=10, stoppable=False):
            self.setUp()
            root = ModelFile("release", True)
            root.path_pair_id = "movies"
            nested = ModelFile("nested", True)
            nested.path_pair_id = "movies"
            child = ModelFile("episode.bin", False)
            child.path_pair_id = "movies"
            child.state = state
            child.local_size = local_size
            child.remote_size = remote_size
            child.is_stoppable = stoppable
            root.add_child(nested)
            nested.add_child(child)
            model = Model()
            model.set_base_logger(self.controller.logger)
            model.add_file(root)
            self.controller._Controller__model = model
            self.controller._Controller__model_lock = threading.RLock()
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
            }
            self.controller._Controller__path_pair_staging_paths = {
                "movies": "/local/movies/incomplete"
            }
            return child

        with self.subTest(action="queue"):
            child = install_nested_child(ModelFile.State.DEFAULT)
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, child.file_id))
            self.controller._Controller__process_commands()
            self.controller._Controller__lftp.queue.assert_called_once_with(
                "release/nested/episode.bin", False,
                remote_base_dir_path="/remote/movies", local_base_dir_path="/local/movies/incomplete",
            )

        with self.subTest(action="stop"):
            child = install_nested_child(ModelFile.State.DOWNLOADING, stoppable=True)
            self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, child.file_id))
            self.controller._Controller__process_commands()
            self.controller._Controller__lftp.kill.assert_called_once_with(
                "release/nested/episode.bin", path_pair_id="movies",
                remote_path="/remote/movies/release/nested/episode.bin",
                local_path=os.path.join("/local/movies/incomplete", "release", "nested"),
            )

        with self.subTest(action="delete_local"):
            child = install_nested_child(ModelFile.State.DEFAULT, local_size=10)
            self.controller._Controller__queue_delete_local_process = MagicMock()
            self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_LOCAL, child.file_id))
            self.controller._Controller__process_commands()
            self.controller._Controller__queue_delete_local_process.assert_called_once()
            self.assertEqual(child.file_id, self.controller._Controller__queue_delete_local_process.call_args.args[0].file_id)

        with self.subTest(action="delete_remote"):
            child = install_nested_child(ModelFile.State.DEFAULT)
            with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
                self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_REMOTE, child.file_id))
                self.controller._Controller__process_commands()
            self.assertEqual("release/nested/episode.bin", delete_remote_process.call_args.kwargs["file_name"])

    def test_process_commands_queue_rejects_empty_remote_directory_but_allows_zero_byte_file(self):
        empty_dir = ModelFile("empty-dir", True)
        empty_dir.remote_size = 0
        empty_dir.local_size = 0
        self.controller._Controller__model.get_file.return_value = empty_dir
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, empty_dir.file_id)
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_called_once()
        self.assertEqual(409, callback.on_failure.call_args.args[1])

        self.controller._Controller__model.get_file.reset_mock()
        zero_file = ModelFile("zero-byte", False)
        zero_file.remote_size = 0
        self.controller._Controller__model.get_file.return_value = zero_file
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, zero_file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "zero-byte",
            False,
            remote_base_dir_path=None,
            local_base_dir_path="/local/incomplete",
        )

    def test_process_commands_queue_is_idempotent_for_queued_file(self):
        file = ModelFile("queued", False)
        file.remote_size = 10
        file.state = ModelFile.State.QUEUED
        self.controller._Controller__model.get_file.return_value = file
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_success.assert_called_once_with()

    def test_process_commands_queue_is_idempotent_for_downloading_file(self):
        file = ModelFile("downloading", False)
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADING
        self.controller._Controller__model.get_file.return_value = file
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_success.assert_called_once_with()

    def test_queue_idempotent_pending_claim_settlement_retains_stop_and_fails_once(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        file.state = ModelFile.State.DOWNLOADING
        self.controller._Controller__persist.stopped_file_names.add(file.file_id)
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__pending_queue_dispatches[file.file_id] = PendingQueueDispatch(
            time.monotonic(), file.full_path, file.path_pair_id, file.is_dir, 1,
        )
        callback = MagicMock()
        command = self.controller._Controller__deferred_queue_intents[file.file_id].command
        command.add_callback(callback)
        self.controller._Controller__cancel_and_settle_collision_claim_for_refresh = MagicMock(
            return_value=False,
        )

        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            callback.on_success.assert_not_called()
            callback.on_failure.assert_called_once()
            self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
            retained = self.controller._Controller__deferred_queue_intents.get(file.file_id)
            self.assertIsNotNone(retained)
            self.assertTrue(retained.stop_requested)
            self.assertIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
            self.assertIsNotNone(self.controller._Controller__collision_compare_claim)
            self.assertTrue(os.path.exists(claimed))
            self.assertTrue(os.path.exists(sidecar))
            self.assertFalse(os.path.exists(original))
        finally:
            temp_dir.cleanup()

    def test_queue_idempotent_pending_matching_claim_without_intent_retains_stop(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        file.state = ModelFile.State.QUEUED
        self.controller._Controller__persist.stopped_file_names.add(file.file_id)
        self.controller._Controller__deferred_queue_intents.clear()
        self.controller._Controller__pending_queue_dispatches = {
            file.file_id: PendingQueueDispatch(
                time.monotonic(), file.full_path, file.path_pair_id, file.is_dir, 1,
            ),
        }
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)
        self.controller._Controller__cancel_and_settle_collision_claim_for_refresh = MagicMock(
            return_value=False,
        )

        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            callback.on_success.assert_not_called()
            callback.on_failure.assert_called_once()
            self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
            retained = self.controller._Controller__deferred_queue_intents.get(file.file_id)
            self.assertIsNotNone(retained)
            self.assertTrue(retained.stop_requested)
            self.assertTrue(os.path.exists(claimed))
            self.assertTrue(os.path.exists(sidecar))
            self.assertFalse(os.path.exists(original))
        finally:
            temp_dir.cleanup()

    def test_queue_late_duplicate_after_deferred_failure_gets_own_failure(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        first_callback = MagicMock()
        intent_command = self.controller._Controller__deferred_queue_intents[file.file_id].command
        intent_command.add_callback(first_callback)
        self.controller._Controller__move_from_staging = MagicMock(
            side_effect=RuntimeError("preflight failure"),
        )
        self.controller._Controller__cancel_and_settle_collision_claim_for_refresh = MagicMock(
            return_value=False,
        )

        try:
            self.controller.queue_command(intent_command)
            self.controller._Controller__process_commands()
            first_callback.on_failure.assert_called_once()
            first_callback.on_success.assert_not_called()

            late_callback = MagicMock()
            late_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
            late_command.add_callback(late_callback)
            self.controller.queue_command(late_command)
            self.controller._Controller__process_commands()

            late_callback.on_failure.assert_called_once()
            late_callback.on_success.assert_not_called()
            self.controller._Controller__lftp.queue.assert_not_called()
            self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(os.path.exists(claimed))
            self.assertTrue(os.path.exists(sidecar))
            self.assertFalse(os.path.exists(original))
        finally:
            temp_dir.cleanup()

    def test_queue_collision_claim_owner_identity_blocks_overlapping_intent(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        root_intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        nested_file_id = ModelFile.build_file_id("sample-directory/same.bin", file.path_pair_id)
        nested_command = Controller.Command(Controller.Command.Action.QUEUE, nested_file_id)
        nested_intent = DeferredQueueIntent(nested_command, nested_file_id, file.path_pair_id)

        try:
            self.assertTrue(self.controller._Controller__deferred_collision_claim_match(root_intent))
            self.assertFalse(self.controller._Controller__deferred_collision_claim_match(nested_intent))

            self.controller._Controller__collision_compare_claim = (
                original, claimed, os.path.join(temp_dir.name, "final", "sample-directory", "same.bin"),
                "claim-key", sidecar, file.path_pair_id,
            )
            self.assertIsNone(self.controller._Controller__deferred_collision_claim_match(root_intent))
            self.assertFalse(self.controller._Controller__cancel_matching_deferred_collision_claim(root_intent))
        finally:
            temp_dir.cleanup()

    def test_process_commands_queue_rechecks_authoritative_state_before_transport(self):
        stale_file = ModelFile("stale", False)
        stale_file.remote_size = 10
        current_file = ModelFile("stale", False)
        current_file.remote_size = 10
        current_file.state = ModelFile.State.QUEUED
        self.controller._Controller__model.get_file.side_effect = [stale_file, current_file]

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, stale_file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()

    def test_process_commands_queue_deduplicates_pending_commands_by_file_id(self):
        file = ModelFile("duplicate", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        first_callback = MagicMock()
        second_callback = MagicMock()
        first_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        second_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        first_command.add_callback(first_callback)
        second_command.add_callback(second_callback)

        self.controller.queue_command(first_command)
        self.controller.queue_command(second_command)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "duplicate",
            False,
            remote_base_dir_path=None,
            local_base_dir_path="/local/incomplete",
        )
        first_callback.on_success.assert_called_once_with()
        second_callback.on_success.assert_called_once_with()

    def test_process_commands_queue_pending_guard_persists_across_stale_model_ticks(self):
        file = ModelFile("cross-tick", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once()

    def test_process_commands_queue_failed_dispatch_does_not_fence_retry(self):
        file = ModelFile("retry", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.queue.side_effect = [LftpError("queue failed"), None]
        failed_callback = MagicMock()
        retried_callback = MagicMock()
        failed = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        retried = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        failed.add_callback(failed_callback)
        retried.add_callback(retried_callback)

        self.controller.queue_command(failed)
        self.controller._Controller__process_commands()
        self.controller.queue_command(retried)
        self.controller._Controller__process_commands()

        self.assertEqual(2, self.controller._Controller__lftp.queue.call_count)
        failed_callback.on_failure.assert_called_once_with("Transfer backend error: queue failed", 500)
        failed_callback.on_success.assert_not_called()
        retried_callback.on_success.assert_called_once_with()

    def test_process_commands_queue_sync_backend_false_is_a_rejection_trace(self):
        file = ModelFile("sync-rejected", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.queue.return_value = False
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        callback = MagicMock()

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)
        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("Transfer backend error: Transfer backend rejected queue request", 500)
        time.sleep(0.05)
        entry = next(
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "queue_dispatch"
        )
        self.assertEqual("queue_dispatch", entry["message"])
        self.assertEqual("backend_rejection", entry["details"]["future_outcome"])
        self.assertEqual("backend_rejected", entry["details"]["reason"])
        self.assertEqual("backend_rejection", entry["details"]["outcome"])

    def test_queue_callback_trace_records_success_at_callback_entry(self):
        file = ModelFile("callback-success", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"queue.readiness": "info"}},
            max_entries=32,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        entries = [
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "queue_callback"
        ]
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual(
            {
                "schema": "queue_readiness.v1",
                "phase": "entry",
                "outcome": "success",
                "callback_index": 0,
                "callback_count": 1,
                "error_code": 0,
            },
            entry["details"],
        )
        self.assertIsNone(entry["file_id"])
        self.assertIsNone(entry["path_pair_id"])
        self.assertNotIn("callback-success", str(entry))

    def test_queue_callback_trace_records_failure_at_callback_entry(self):
        file = ModelFile("callback-failure", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.queue.side_effect = LftpError("queue failed")
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"queue.readiness": "info"}},
            max_entries=32,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("Transfer backend error: queue failed", 500)
        entries = [
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "queue_callback"
        ]
        self.assertEqual(1, len(entries))
        self.assertEqual(
            {
                "schema": "queue_readiness.v1",
                "phase": "entry",
                "outcome": "failure",
                "callback_index": 0,
                "callback_count": 1,
                "error_code": 500,
            },
            entries[0]["details"],
        )
        self.assertNotIn("callback-failure", str(entries[0]))

    def test_queue_callback_trace_is_default_off(self):
        file = ModelFile("callback-disabled", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off"},
            max_entries=32,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        self.assertEqual([], trace.snapshot()["entries"])

    def test_queue_http_wait_trace_records_only_fixed_outcomes(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"queue.readiness": "info"}},
            max_entries=32,
        )
        self.controller._Controller__context.breadcrumb_trace = trace

        self.controller.record_queue_http_wait_trace("wait-success", True, True)
        self.controller.record_queue_http_wait_trace("wait-failure", True, False)
        self.controller.record_queue_http_wait_trace("wait-timeout", False, None)

        entries = trace.snapshot()["entries"]
        self.assertEqual(3, len(entries))
        self.assertEqual(
            ["success", "failure", "timeout"],
            [entry["details"]["outcome"] for entry in entries],
        )
        self.assertTrue(all(entry["message"] == "queue_http_wait" for entry in entries))
        self.assertTrue(all(entry["file_id"] is None for entry in entries))
        self.assertTrue(all(entry["path_pair_id"] is None for entry in entries))
        self.assertNotIn("wait-success", str(entries))
        self.assertNotIn("wait-failure", str(entries))
        self.assertNotIn("wait-timeout", str(entries))

    def test_queue_http_wait_trace_is_default_off(self):
        trace = BreadcrumbTraceCollector(lambda: True, policy={"default": "off"}, max_entries=8)
        self.controller._Controller__context.breadcrumb_trace = trace

        self.controller.record_queue_http_wait_trace("wait-disabled", False, None)

        self.assertEqual([], trace.snapshot()["entries"])

    def test_async_queue_rejection_stays_rejected_during_idle_reconciliation(self):
        file = ModelFile("async-rejected", False)
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__pending_queue_dispatches = {
            file.file_id: PendingQueueDispatch(0.0, file.name, None, False, 1),
        }

        self.assertTrue(self.controller._Controller__submit_lftp_operation(
            "queue", lambda: False, file.file_id, 1,
        ))
        operation = self.controller._Controller__lftp_operations[0]
        operation.future.result(timeout=1)

        self.assertEqual(
            set(), self.controller._reconcile_pending_queue_dispatches_from_fresh_status([]),
        )
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        time.sleep(0.05)
        entry = next(
            entry for entry in trace.snapshot()["entries"] if entry["message"] == "queue_status_ack"
        )
        self.assertEqual("backend_rejection", entry["details"]["future_outcome"])
        self.assertEqual("rejected", entry["details"]["result"])

    def test_process_commands_queue_pending_guard_clears_after_authoritative_lifecycle_exit(self):
        file = ModelFile("lifecycle", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        file.state = ModelFile.State.QUEUED
        self.controller._reconcile_pending_queue_dispatches_from_fresh_status({file.file_id})
        self.controller._Controller__process_commands()
        file.state = ModelFile.State.DEFAULT
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertEqual(2, self.controller._Controller__lftp.queue.call_count)

    def test_resume_source_binding_waits_for_running_get_or_pget_status(self):
        file = ModelFile("resume.bin", False)
        self.controller._Controller__persist.resume_source_identities = {}
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__pending_queue_dispatches[file.file_id] = PendingQueueDispatch(
            0.0, file.name, None, False, 1, (100, 1700000000),
        )

        # A fresh idle handoff never authorizes a pre-existing map; it only
        # transfers completion ownership to the updater.
        self.assertEqual(
            {(file.name, None, None)},
            self.controller._reconcile_pending_queue_dispatches_from_fresh_status(set()),
        )
        self.assertEqual({}, self.controller._Controller__persist.resume_source_identities)
        self.controller._Controller__pending_queue_dispatches[file.file_id] = PendingQueueDispatch(
            0.0, file.name, None, False, 1, (100, 1700000000),
        )

        queued_status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file.name, "/remote/resume.bin",
        )
        self.controller._reconcile_pending_queue_dispatches_from_fresh_status([queued_status])
        self.assertIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        self.assertEqual({}, self.controller._Controller__persist.resume_source_identities)

        status = LftpJobStatus(
            1, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file.name, "/remote/resume.bin",
        )
        self.controller._reconcile_pending_queue_dispatches_from_fresh_status([status])

        self.assertEqual({file.file_id: (100, 1700000000)},
                         self.controller._Controller__persist.resume_source_identities)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)

    def test_raw_queued_status_keeps_identityless_dispatch_until_idle_or_running(self):
        file_dispatches = {
            ModelFile.build_file_id("file", None): PendingQueueDispatch(
                0.0, "file", None, False, 1,
            ),
            ModelFile.build_file_id("directory", "pair-a"): PendingQueueDispatch(
                0.0, "directory", "pair-a", True, 2,
            ),
        }
        self.controller._Controller__pending_queue_dispatches = file_dispatches
        queued_statuses = [
            LftpJobStatus(
                1, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED,
                "file", "/remote/file",
            ),
            LftpJobStatus(
                2, LftpJobStatus.Type.GET, LftpJobStatus.State.QUEUED,
                "directory", "/remote/directory",
            ),
        ]

        self.assertEqual(set(), self.controller._reconcile_pending_queue_dispatches_from_fresh_status(queued_statuses))
        self.assertEqual(file_dispatches, self.controller._Controller__pending_queue_dispatches)

        retired = self.controller._reconcile_pending_queue_dispatches_from_fresh_status([])

        self.assertEqual({("file", None, None), ("directory", "pair-a", None)}, retired)
        self.assertEqual({}, self.controller._Controller__pending_queue_dispatches)

    def test_fresh_idle_queue_handoff_preserves_scoped_directory_identity_without_binding(self):
        file_dispatches = {
            ModelFile.build_file_id("file", None): PendingQueueDispatch(0.0, "file", None, False, 1),
            ModelFile.build_file_id("directory", "pair-a"): PendingQueueDispatch(
                0.0, "directory", "pair-a", True, 2,
            ),
        }
        self.controller._Controller__pending_queue_dispatches = file_dispatches
        self.controller._Controller__persist.resume_source_identities = {}

        retired = self.controller._reconcile_pending_queue_dispatches_from_fresh_status([])

        self.assertEqual({("file", None, None), ("directory", "pair-a", None)}, retired)
        self.assertEqual({}, self.controller._Controller__pending_queue_dispatches)
        self.assertEqual({}, self.controller._Controller__persist.resume_source_identities)

    def test_fresh_idle_accepted_queue_future_handoffs_completion_identity(self):
        file = ModelFile("accepted-queue", False)
        future = Future()
        future.set_result(None)
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__pending_queue_dispatches = {
            file.file_id: PendingQueueDispatch(0.0, file.name, None, False, 1),
        }
        self.controller._Controller__lftp_operations = [
            _LftpOperation("queue", future, file.file_id, 1),
        ]

        self.controller._Controller__drain_lftp_operations()
        retired = self.controller._reconcile_pending_queue_dispatches_from_fresh_status([])

        self.assertEqual({(file.name, None, None)}, retired)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        time.sleep(0.05)
        entries = trace.snapshot()["entries"]
        self.assertEqual(
            {"queue_future_outcome", "queue_status_ack"},
            {entry["message"] for entry in entries},
        )
        self.assertEqual(1, len({entry["flow_id"] for entry in entries}))
        self.assertEqual("success", next(
            entry for entry in entries if entry["message"] == "queue_future_outcome"
        )["details"]["future_outcome"])
        status_ack = next(
            entry for entry in entries if entry["message"] == "queue_status_ack"
        )
        self.assertEqual("fresh_idle_without_active_status", status_ack["details"]["retirement_reason"])

    def test_queue_worker_start_future_outcome_and_retirement_share_opaque_flow(self):
        file = ModelFile("private-queue-target.mkv", False)
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__pending_queue_dispatches = {
            file.file_id: PendingQueueDispatch(0.0, file.name, None, False, 1),
        }

        self.assertTrue(self.controller._Controller__submit_lftp_operation(
            "queue", lambda: True, file.file_id, 1,
        ))
        operation = self.controller._Controller__lftp_operations[0]
        operation.future.result(timeout=1)
        self.controller._Controller__drain_lftp_operations()
        retired = self.controller._reconcile_pending_queue_dispatches_from_fresh_status([])
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        self.assertEqual({(file.name, None, None)}, retired)
        expected_correlation = "fractional-mtime:{}".format(
            opaque_trace_correlation(file.file_id),
        )
        retrieved = trace.query_events(
            correlation_id=expected_correlation, category_prefix="queue.",
        )["events"]
        messages = [entry["message"] for entry in retrieved]
        self.assertEqual(
            ["queue_worker_start", "queue_future_outcome", "queue_status_ack"],
            messages,
        )
        self.assertEqual({expected_correlation}, {entry["corr_id"] for entry in retrieved})
        self.assertEqual(1, len({entry["flow_id"] for entry in retrieved}))
        self.assertEqual("accepted", next(
            entry for entry in retrieved if entry["message"] == "queue_future_outcome"
        )["details"]["outcome"])
        for entry in retrieved:
            self.assertIsNone(entry["file_id"])
            self.assertNotIn("private-queue-target.mkv", str(entry))

    def test_queue_future_trace_captures_prompt_timeout_before_later_command(self):
        file = ModelFile("ambiguous-queue", False)
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"

        def timed_out_queue():
            self.controller._Controller__lftp.last_command_timed_out = True

        self.assertTrue(self.controller._Controller__submit_lftp_operation(
            "queue", timed_out_queue, file.file_id, 1,
        ))
        operation = self.controller._Controller__lftp_operations[0]
        operation.future.result(timeout=1)
        # Model the next serialized PTY command completing before the
        # controller consumes the Queue future. The breadcrumb must retain
        # the Queue command's own outcome rather than this newer value.
        self.controller._Controller__lftp.last_command_timed_out = False

        self.controller._Controller__drain_lftp_operations()
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        time.sleep(0.05)
        outcome = next(
            entry for entry in trace.snapshot()["entries"] if entry["message"] == "queue_future_outcome"
        )
        self.assertEqual("success", outcome["details"]["future_outcome"])
        self.assertTrue(outcome["details"]["command_prompt_timed_out"])
        self.assertEqual("prompt_timeout", outcome["details"]["outcome"])

    def test_queue_future_trace_keeps_prompt_success_before_later_timeout(self):
        file = ModelFile("successful-queue", False)
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__lftp.last_command_timed_out = True

        def successful_queue():
            self.controller._Controller__lftp.last_command_timed_out = False

        self.assertTrue(self.controller._Controller__submit_lftp_operation(
            "queue", successful_queue, file.file_id, 1,
        ))
        operation = self.controller._Controller__lftp_operations[0]
        operation.future.result(timeout=1)
        self.controller._Controller__lftp.last_command_timed_out = True

        self.controller._Controller__drain_lftp_operations()
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        time.sleep(0.05)
        outcome = next(
            entry for entry in trace.snapshot()["entries"] if entry["message"] == "queue_future_outcome"
        )
        self.assertEqual("success", outcome["details"]["future_outcome"])
        self.assertFalse(outcome["details"]["command_prompt_timed_out"])

    def test_queue_future_trace_classifies_exception_as_distinct_outcome(self):
        file = ModelFile("private-exception-target.mkv", False)
        future = Future()
        future.set_exception(LftpError("private backend failure"))
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__lftp.backend_name = "lftp"
        self.controller._Controller__lftp_operation_sequences = {file.file_id: 1}
        self.controller._Controller__lftp_operations = [
            _LftpOperation("queue", future, file.file_id, 1),
        ]

        self.controller._Controller__drain_lftp_operations()

        outcome = next(
            entry for entry in trace.query_events(
                correlation_id="fractional-mtime:{}".format(
                    opaque_trace_correlation(file.file_id),
                ),
                category_prefix="queue.",
            )["events"] if entry["message"] == "queue_future_outcome"
        )
        self.assertEqual("exception", outcome["details"]["outcome"])
        self.assertEqual("error", outcome["details"]["future_outcome"])
        self.assertNotIn("private-exception-target.mkv", str(outcome))

    def test_pending_autoqueue_dispatch_with_other_active_status_stays_ambiguous(self):
        file = ModelFile("fallback-autoqueue", False)
        self.controller._Controller__pending_queue_dispatches = {
            file.file_id: PendingQueueDispatch(0.0, file.name, None, False, 1),
        }
        unrelated = LftpJobStatus(
            2, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "other-active", "",
        )

        retired = self.controller._reconcile_pending_queue_dispatches_from_fresh_status([unrelated])

        self.assertEqual(set(), retired)
        self.assertIn(file.file_id, self.controller._Controller__pending_queue_dispatches)

    def test_fresh_idle_failed_or_cancelled_queue_dispatch_does_not_handoff_completion(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                file = ModelFile("failed-queue", False)
                future = Future()
                if cancelled:
                    future.cancel()
                else:
                    future.set_exception(LftpError("queue failed"))
                self.controller._Controller__pending_queue_dispatches = {
                    file.file_id: PendingQueueDispatch(0.0, file.name, None, False, 1),
                }
                self.controller._Controller__lftp_operations = [
                    _LftpOperation("queue", future, file.file_id, 1),
                ]

                retired = self.controller._reconcile_pending_queue_dispatches_from_fresh_status([])

                self.assertEqual(set(), retired)
                self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)

    def test_resume_source_identity_requires_exact_size_and_portable_mtime(self):
        file_id = ModelFile.build_file_id("resume.bin", "pair-a")
        self.controller._Controller__persist.resume_source_identities = {
            file_id: (100, 1700000000),
        }

        self.assertTrue(self.controller._Controller__allow_file_resume(file_id, False, (100, 1700000000)))
        self.assertFalse(self.controller._Controller__allow_file_resume(file_id, False, (101, 1700000000)))
        self.assertFalse(self.controller._Controller__allow_file_resume(file_id, False, (100, 1700000001)))
        self.assertFalse(self.controller._Controller__allow_file_resume(file_id, False, None))

    def test_resume_source_cleanup_retains_staging_artifacts_and_clears_after_removal(self):
        file_id = ModelFile.build_file_id("resume.bin", None)
        self.controller._Controller__persist.resume_source_identities = {file_id: (100, 1700000000)}
        with tempfile.TemporaryDirectory() as staging_path:
            self.controller._Controller__staging_path = staging_path
            target = os.path.join(staging_path, "resume.bin.lftp")
            Path(target).touch()

            self.controller._Controller__clear_resume_source_if_staging_absent(file_id, "resume.bin", None)
            self.assertIn(file_id, self.controller._Controller__persist.resume_source_identities)

            os.unlink(target)
            self.controller._Controller__clear_resume_source_if_staging_absent(file_id, "resume.bin", None)
            self.assertNotIn(file_id, self.controller._Controller__persist.resume_source_identities)

    def test_queue_passes_matching_resume_identity_to_lftp(self):
        file = ModelFile("resume.bin", False)
        file.remote_size = 100
        identity = (100, 1700000000)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__model_builder.get_remote_resume_source_identity.return_value = identity
        self.controller._Controller__persist.resume_source_identities = {file.file_id: identity}
        self.controller._Controller__lftp.backend_name = "lftp"

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        self.assertTrue(self.controller._Controller__lftp.queue.call_args.kwargs["allow_resume"])

    def test_queue_disables_resume_for_same_size_mtime_drift(self):
        file = ModelFile("resume.bin", False)
        file.remote_size = 100
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__model_builder.get_remote_resume_source_identity.return_value = (100, 1700000001)
        self.controller._Controller__persist.resume_source_identities = {file.file_id: (100, 1700000000)}
        self.controller._Controller__lftp.backend_name = "lftp"

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        self.assertFalse(self.controller._Controller__lftp.queue.call_args.kwargs["allow_resume"])

    def test_queue_bootstraps_legacy_sidecarless_resume_from_confirmed_start(self):
        file = ModelFile("resume.bin", False)
        file.remote_size = 100
        identity = (100, 1700000000)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__model_builder.get_remote_resume_source_identity.return_value = identity
        self.controller._Controller__persist.resume_source_identities = {}
        self.controller._Controller__persist.downloaded_timestamps = {file.file_id: 1700000001.0}
        self.controller._Controller__lftp.backend_name = "lftp"

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        queue_kwargs = self.controller._Controller__lftp.queue.call_args.kwargs
        self.assertFalse(queue_kwargs["allow_resume"])
        self.assertTrue(queue_kwargs["allow_legacy_get_resume"])
        self.assertEqual(100, queue_kwargs["expected_size"])

    def test_legacy_resume_bootstrap_rejects_newer_source_or_invalid_start_timestamp(self):
        file_id = ModelFile.build_file_id("resume.bin", "pair-a")
        self.controller._Controller__persist.resume_source_identities = {}
        self.controller._Controller__persist.downloaded_timestamps = {file_id: 1700000000.0}

        self.assertFalse(self.controller._Controller__allow_legacy_file_resume(
            file_id, False, (100, 1700000001),
        ))
        self.controller._Controller__persist.downloaded_timestamps[file_id] = float("nan")
        self.assertFalse(self.controller._Controller__allow_legacy_file_resume(
            file_id, False, (100, 1700000000),
        ))

    def test_failed_fresh_queue_never_commits_resume_source_binding(self):
        file = ModelFile("resume.bin", False)
        self.controller._Controller__persist.resume_source_identities = {}
        self.controller._Controller__pending_queue_dispatches = {
            file.file_id: PendingQueueDispatch(0.0, file.name, None, False, 1, (100, 1700000001)),
        }
        failed = Future()
        failed.set_exception(LftpError("Cannot safely restart a changed remote file while a local partial exists"))
        self.controller._Controller__lftp_operations = [
            _LftpOperation("queue", failed, file.file_id, 1),
        ]
        self.controller._Controller__lftp_operation_sequences = {file.file_id: 1}
        self.controller._Controller__lftp_failed_operation_sequences = set()

        self.controller._Controller__drain_lftp_operations()

        self.assertEqual({}, self.controller._Controller__persist.resume_source_identities)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)

    def test_process_commands_queue_pending_guard_clears_when_file_is_missing(self):
        file = ModelFile("removed", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__model.get_file.side_effect = ModelError("removed")
        self.controller._Controller__process_commands()
        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)

    def test_process_commands_queue_unobserved_acceptance_expires_for_explicit_retry(self):
        file = ModelFile("ambiguous", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        pending = self.controller._Controller__pending_queue_dispatches[file.file_id]
        pending.accepted_at_monotonic -= (
            self.controller._Controller__lftp_status_cache_max_age_seconds + 1
        )
        # Even a stale active model cannot deny retry forever when no fresh
        # transport status ever reconciled the accepted command.
        file.state = ModelFile.State.DOWNLOADING
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertEqual(2, self.controller._Controller__lftp.queue.call_count)

    def test_process_commands_queue_fresh_idle_handoff_releases_pending_guard(self):
        file = ModelFile("fresh-empty", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._reconcile_pending_queue_dispatches_from_fresh_status(set())
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertEqual(2, self.controller._Controller__lftp.queue.call_count)

    def test_process_commands_queue_fresh_empty_then_active_clears_retry_eligibility(self):
        file = ModelFile("eventually-active", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._reconcile_pending_queue_dispatches_from_fresh_status(set())
        self.controller._reconcile_pending_queue_dispatches_from_fresh_status({file.file_id})
        file.state = ModelFile.State.DOWNLOADING
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once()
        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)

    def test_process_commands_queue_successful_ambiguous_retry_resets_deadline(self):
        file = ModelFile("retry-once", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._Controller__pending_queue_dispatches[file.file_id].accepted_at_monotonic -= 4
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertEqual(2, self.controller._Controller__lftp.queue.call_count)

    def test_process_commands_queue_failed_ambiguous_retry_remains_retryable(self):
        file = ModelFile("retry-failure", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.queue.side_effect = [None, LftpError("late failure"), None]
        failed_callback = MagicMock()

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._Controller__pending_queue_dispatches[file.file_id].accepted_at_monotonic -= 4
        failed = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        failed.add_callback(failed_callback)
        self.controller.queue_command(failed)
        self.controller._Controller__process_commands()
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertEqual(3, self.controller._Controller__lftp.queue.call_count)
        failed_callback.on_failure.assert_called_once_with("Transfer backend error: late failure", 500)

    def test_process_commands_queue_removed_identity_cannot_reuse_expired_permission(self):
        file = ModelFile("reappears", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._Controller__pending_queue_dispatches[file.file_id].accepted_at_monotonic -= 4

        self.controller._Controller__model.get_file.side_effect = ModelError("removed")
        self.controller._Controller__process_commands()
        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        self.controller._Controller__model.get_file.side_effect = None
        file.state = ModelFile.State.DOWNLOADING
        self.controller._Controller__model.get_file.return_value = file
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once()

    def test_process_commands_queue_many_removed_identities_leave_no_pending_growth(self):
        files = [ModelFile("removed-{}".format(index), False) for index in range(100)]
        for file in files:
            file.remote_size = 10
        files_by_id = {file.file_id: file for file in files}
        self.controller._Controller__model.get_file.side_effect = files_by_id.__getitem__
        for file in files:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__model.get_file.side_effect = ModelError("removed")
        self.controller._Controller__process_commands()

        self.assertEqual({}, self.controller._Controller__pending_queue_dispatches)

    def test_process_commands_stop_then_queue_redispatches_despite_stale_active_model(self):
        file = ModelFile("resume", False)
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.kill.return_value = True
        stop_callback = MagicMock()
        queue_callback = MagicMock()
        duplicate_callback = MagicMock()
        stop = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        queue = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        duplicate = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        stop.add_callback(stop_callback)
        queue.add_callback(queue_callback)
        duplicate.add_callback(duplicate_callback)

        self.controller.queue_command(stop)
        self.controller.queue_command(queue)
        self.controller.queue_command(duplicate)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.kill.assert_called_once()
        self.controller._Controller__lftp.queue.assert_called_once()
        stop_callback.on_success.assert_called_once_with()
        queue_callback.on_success.assert_called_once_with()
        duplicate_callback.on_success.assert_called_once_with()
        self.assertNotIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
        self.assertIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        # Resume preserves the existing download-start listener contract: it
        # must not create a second fresh-download notification lifecycle.
        self.assertNotIn(file.file_id, self.controller._Controller__download_start_state)

    def test_process_commands_queue_then_stop_same_drain_uses_pending_identity(self):
        file = ModelFile("cancel-pending", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.kill.return_value = True
        queue_callback = MagicMock()
        stop_callback = MagicMock()
        queue = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        stop = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        queue.add_callback(queue_callback)
        stop.add_callback(stop_callback)

        self.controller.queue_command(queue)
        self.controller.queue_command(stop)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once()
        self.controller._Controller__lftp.kill.assert_called_once()
        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
        queue_callback.on_success.assert_called_once_with()
        stop_callback.on_success.assert_called_once_with()

    def test_process_commands_queue_then_stop_separate_drain_before_poll_uses_pending_identity(self):
        file = ModelFile("cancel-cross-tick", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.kill.return_value = True

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once()
        self.controller._Controller__lftp.kill.assert_called_once()
        self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_queue_then_failed_stop_preserves_pending_identity(self):
        file = ModelFile("failed-cancel", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.kill.return_value = False
        stop_callback = MagicMock()

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        stop = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        stop.add_callback(stop_callback)
        self.controller.queue_command(stop)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.kill.assert_called_once()
        self.assertIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
        self.assertNotIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
        stop_callback.on_failure.assert_called_once_with(
            "File '{}' could not be stopped".format(file.file_id), 409
        )
        stop_callback.on_success.assert_not_called()

    def test_process_commands_stop_cannot_borrow_same_name_pending_from_other_path_pair(self):
        movies = ModelFile("shared", False)
        movies.path_pair_id = "movies"
        movies.remote_size = 10
        tv = ModelFile("shared", False)
        tv.path_pair_id = "tv"
        tv.remote_size = 10
        files = {movies.file_id: movies, tv.file_id: tv}
        self.controller._Controller__model.get_file.side_effect = files.__getitem__
        callback = MagicMock()

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, movies.file_id))
        self.controller._Controller__process_commands()
        stop = Controller.Command(Controller.Command.Action.STOP, tv.file_id)
        stop.add_callback(callback)
        self.controller.queue_command(stop)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.kill.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "File '{}' is not Queued or Downloading".format(tv.file_id), 409
        )
        self.assertIn(movies.file_id, self.controller._Controller__pending_queue_dispatches)

    def test_process_commands_queue_pending_guard_has_no_lossy_capacity_eviction(self):
        files = []
        for index in range(1025):
            file = ModelFile("capacity-{}".format(index), False)
            file.remote_size = 10
            files.append(file)
        files_by_id = {file.file_id: file for file in files}
        self.controller._Controller__model.get_file.side_effect = files_by_id.__getitem__

        for file in files:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertEqual(1025, self.controller._Controller__lftp.queue.call_count)
        self.assertEqual(set(files_by_id), set(self.controller._Controller__pending_queue_dispatches))

    def test_process_commands_queue_keeps_same_name_path_pairs_independent(self):
        movies_file = ModelFile("duplicate", False)
        movies_file.path_pair_id = "movies"
        movies_file.remote_size = 10
        tv_file = ModelFile("duplicate", False)
        tv_file.path_pair_id = "tv"
        tv_file.remote_size = 10
        self.controller._Controller__model.get_file.side_effect = {
            movies_file.file_id: movies_file,
            tv_file.file_id: tv_file,
        }.__getitem__
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies"),
            "tv": SimpleNamespace(remote_path="/remote/tv", local_path="/local/tv"),
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete",
            "tv": "/local/tv/incomplete",
        }

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, movies_file.file_id))
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, tv_file.file_id))
        self.controller._Controller__process_commands()

        self.assertEqual(2, self.controller._Controller__lftp.queue.call_count)
        self.assertEqual(
            ["duplicate", "duplicate"],
            [call.args[0] for call in self.controller._Controller__lftp.queue.call_args_list],
        )
        self.assertEqual(
            {"/remote/movies", "/remote/tv"},
            {call.kwargs["remote_base_dir_path"] for call in self.controller._Controller__lftp.queue.call_args_list},
        )

    def test_process_commands_stop_uses_path_pair_identity(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        command = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.kill.assert_called_once_with(
            "dup",
            path_pair_id="movies",
            remote_path="/remote/movies/dup",
            local_path="/local/movies/incomplete"
        )
        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_stop_uses_nested_pget_paths(self):
        root = ModelFile("release", True)
        file = ModelFile("movie.mkv", False)
        root.add_child(file)
        file.path_pair_id = "movies"
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {"movies": "/local/movies/incomplete"}

        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.kill.assert_called_once_with(
            file.full_path, path_pair_id="movies",
            remote_path="/remote/movies/release/movie.mkv",
            local_path=os.path.join("/local/movies/incomplete", "release"),
        )

    def test_process_commands_failed_queue_does_not_arm_download_start(self):
        file = ModelFile("dup", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.queue.side_effect = LftpError("queue failed")

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__download_start_state)

    def test_process_commands_failed_queue_preserves_local_delete_fresh_token(self):
        file = ModelFile("dup", False)
        file.remote_size = 10
        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        self.controller._Controller__download_start_state[file.file_id] = DownloadStartLifecycleEntry(
            "fresh_after_delete", file.path_pair_id, datetime.now()
        )
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.queue.side_effect = LftpError("queue failed")

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertEqual("fresh_after_delete", self.controller._Controller__download_start_state[file.file_id].state)

    def test_process_commands_existing_queued_file_does_not_backfill_download_start(self):
        file = ModelFile("dup", False)
        file.remote_size = 10
        file.state = ModelFile.State.QUEUED
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__download_start_state)

    def _seed_pending_completion_command_recovery(self, file):
        identity = (7, LftpJobStatus.Type.PGET.value)
        self.controller._Controller__model = Model()
        self.controller._Controller__model.add_file(file)
        self.controller._Controller__pending_completion_file_names = {
            (file.name, file.path_pair_id, file.path_pair_name),
        }
        self.controller._Controller__pending_completion_progress_floors = {
            file.file_id: (100, 1000),
        }
        self.controller._Controller__pending_completion_progress_floor_identities = {
            file.file_id: identity,
        }
        self.controller._Controller__pending_completion_progress_floor_overlay_ids = {file.file_id}
        self.controller._Controller__pending_completion_publications = {
            file.file_id: _PendingCompletionPublication(
                ActiveProgressOverlay(100, 1000, None, None), identity,
            ),
        }

    def test_pending_completion_row_queue_starts_new_lifecycle(self):
        file = ModelFile("pending-recovery", False)
        file.remote_size = 1000
        file.state = ModelFile.State.DOWNLOADING
        self._seed_pending_completion_command_recovery(file)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once()
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_publications)
        self.assertNotIn((file.name, None, None), self.controller._Controller__pending_completion_file_names)

    def test_pending_completion_row_stop_consumes_without_lftp_kill(self):
        file = ModelFile("pending-recovery", False)
        file.remote_size = 1000
        file.state = ModelFile.State.DOWNLOADING
        self._seed_pending_completion_command_recovery(file)
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.kill.assert_not_called()
        self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_publications)
        callback.on_success.assert_called_once_with()

    def test_pending_completion_row_delete_consumes_after_confirmed_delete(self):
        file = ModelFile("pending-recovery", False)
        file.remote_size = 1000
        file.local_size = 1000
        file.state = ModelFile.State.DOWNLOADING
        self._seed_pending_completion_command_recovery(file)

        with patch.object(self.controller, "_Controller__queue_delete_local_process") as queue_delete:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id))
            self.controller._Controller__process_commands()

        queue_delete.assert_called_once()
        self.assertIn(file.file_id, self.controller._Controller__pending_completion_publications)
        self.controller._Controller__complete_delete_local_lifecycle(file.file_id, None, file.name)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_publications)
        self.assertNotIn((file.name, None, None), self.controller._Controller__pending_completion_file_names)

    def test_confirmed_delete_clears_pending_completion_lifecycle_without_publication(self):
        file = ModelFile("pending-no-publication", False)
        file.remote_size = 1000
        file.local_size = 1000
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__model = Model()
        self.controller._Controller__model.add_file(file)
        self.controller._Controller__pending_completion_file_names = {
            (file.name, file.path_pair_id, file.path_pair_name),
        }
        self.controller._Controller__pending_completion_authority_rebuild_ids = {file.file_id}
        self.controller._Controller__pending_completion_progress_floors = {file.file_id: (100, 1000)}
        self.controller._Controller__pending_completion_progress_floor_overlay_ids = {file.file_id}
        self.controller._Controller__pending_completion_publications = {}

        with patch.object(self.controller, "_Controller__queue_delete_local_process") as queue_delete:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id))
            self.controller._Controller__process_commands()

        queue_delete.assert_called_once()
        self.controller._Controller__complete_delete_local_lifecycle(file.file_id, None, file.name)
        self.assertNotIn((file.name, None, None), self.controller._Controller__pending_completion_file_names)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_authority_rebuild_ids)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_progress_floors)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_progress_floor_overlay_ids)

    def test_pending_completion_delete_local_blocks_queue_until_success_settles(self):
        file = ModelFile("pending-recovery", False)
        file.remote_size = 1000
        file.local_size = 1000
        file.state = ModelFile.State.DOWNLOADING
        self._seed_pending_completion_command_recovery(file)
        delete = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(delete, file.file_id, file.name, MagicMock(), MagicMock(), True),
        ]
        callback = MagicMock()
        queue = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        queue.add_callback(callback)

        self.controller.queue_command(queue)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Local deletion is still pending; Queue was rejected", 409,
        )
        self.assertIn(file.file_id, self.controller._Controller__pending_completion_publications)

        self.controller._Controller__active_command_processes = []
        self.controller._Controller__complete_delete_local_lifecycle(file.file_id, None, file.name)
        file.local_size = None
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.update_file(file)
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once()

    def test_pending_completion_delete_local_failure_leaves_queue_recoverable(self):
        file = ModelFile("pending-recovery", False)
        file.remote_size = 1000
        file.local_size = 1000
        file.state = ModelFile.State.DOWNLOADING
        self._seed_pending_completion_command_recovery(file)
        delete = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(delete, file.file_id, file.name, MagicMock(), MagicMock(), True),
        ]
        blocked = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        blocked_callback = MagicMock()
        blocked.add_callback(blocked_callback)
        self.controller.queue_command(blocked)
        self.controller._Controller__process_commands()

        self.controller._Controller__active_command_processes = []
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        blocked_callback.on_failure.assert_called_once_with(
            "Local deletion is still pending; Queue was rejected", 409,
        )
        self.controller._Controller__lftp.queue.assert_called_once()
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_publications)

    def test_pending_completion_delete_local_blocks_stop_until_failure_cleanup(self):
        file = ModelFile("pending-recovery", False)
        file.remote_size = 1000
        file.local_size = 1000
        file.state = ModelFile.State.DOWNLOADING
        self._seed_pending_completion_command_recovery(file)
        delete_callback = MagicMock()
        delete = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        delete.add_callback(delete_callback)
        failed_process = MagicMock()
        failed_process.is_alive.return_value = False
        failed_process.propagate_exception.side_effect = RuntimeError("delete failed")
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(delete, file.file_id, file.name, failed_process, MagicMock(), True),
        ]
        blocked_callback = MagicMock()
        stop = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        stop.add_callback(blocked_callback)

        self.controller.queue_command(stop)
        self.controller._Controller__process_commands()

        blocked_callback.on_failure.assert_called_once_with(
            "Local deletion is still pending; Stop was rejected", 409,
        )
        self.assertNotIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
        self.assertIn(file.file_id, self.controller._Controller__pending_completion_publications)

        self.controller._Controller__cleanup_commands()

        failed_process.propagate_exception.assert_called_once_with()
        delete_callback.on_failure.assert_called_once_with(
            "Failed to delete local file 'pending-recovery'", 500,
        )
        self.assertEqual([], self.controller._Controller__active_command_processes)
        self.assertIn(file.file_id, self.controller._Controller__pending_completion_publications)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, file.file_id))
        self.controller._Controller__process_commands()

        self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_publications)
        self.controller._Controller__lftp.kill.assert_not_called()

    def test_queue_then_delete_local_same_drain_rejects_delete(self):
        file = ModelFile("queue-delete", False)
        file.remote_size = 1000
        file.local_size = 1000
        self.controller._Controller__model.get_file.return_value = file
        callback = MagicMock()
        delete = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        delete.add_callback(callback)

        # Queue admission is accepted before its LFTP operation can publish a
        # status.  Keep that operation pending to exercise the exact same
        # command-drain race against Delete Local.
        with (
                patch.object(self.controller, "_Controller__uses_async_lftp_owner", return_value=True),
                patch.object(
                    self.controller,
                    "_Controller__submit_lftp_operation",
                    side_effect=lambda *_args, **_kwargs: (
                        self.controller.queue_command(delete) or True
                    ),
                ) as submit,
        ):
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
            self.controller._Controller__process_commands()

        submit.assert_called_once()
        callback.on_failure.assert_called_once_with(
            "Transfer is still pending or active; Delete Local was rejected", 409,
        )
        self.assertEqual([], self.controller._Controller__active_command_processes)

    def test_delete_local_waits_for_queue_settlement_then_admits(self):
        file = ModelFile("queue-delete", False)
        file.remote_size = 1000
        file.local_size = 1000
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.assertIn(file.file_id, self.controller._Controller__pending_queue_dispatches)

        blocked = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        blocked_callback = MagicMock()
        blocked.add_callback(blocked_callback)
        self.controller.queue_command(blocked)
        self.controller._Controller__process_commands()

        blocked_callback.on_failure.assert_called_once_with(
            "Transfer is still pending or active; Delete Local was rejected", 409,
        )
        self.controller._Controller__pending_queue_dispatches.pop(file.file_id)
        with patch.object(self.controller, "_Controller__queue_delete_local_process") as queue_delete:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id))
            self.controller._Controller__process_commands()

        queue_delete.assert_called_once()

    def test_delete_local_preserves_deferred_queue_until_retry_dispatches(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        queue_callback = MagicMock()
        queue = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        queue.add_callback(queue_callback)

        self.controller.queue_command(queue)
        self.controller._Controller__process_commands()
        intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        self.assertEqual("initial_rescan", intent.phase)

        delete_callback = MagicMock()
        delete = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        delete.add_callback(delete_callback)
        with patch.object(self.controller, "_Controller__queue_delete_local_process") as queue_delete:
            self.controller.queue_command(delete)
            self.controller._Controller__process_commands()

        delete_callback.on_failure.assert_called_once_with(
            "Transfer is still pending or active; Delete Local was rejected", 409,
        )
        queue_delete.assert_not_called()
        self.assertIs(intent, self.controller._Controller__deferred_queue_intents[file.file_id])

        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 1)},
            "remote": {"pair-a": ("remote-test-session", 1)},
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        queue_callback.on_success.assert_called_once_with()

    def test_delete_local_cannot_overtake_ready_deferred_queue_handoff(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        queue = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)

        self.controller.queue_command(queue)
        self.controller._Controller__process_commands()
        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)

        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 1)},
            "remote": {"pair-a": ("remote-test-session", 1)},
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        delete_callback = MagicMock()
        delete = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        delete.add_callback(delete_callback)

        with patch.object(self.controller, "_Controller__queue_delete_local_process") as queue_delete:
            # Delete is already queued when retry preparation consumes the
            # intent and appends its Queue command behind it.
            self.controller.queue_command(delete)
            self.controller._Controller__process_commands()

        delete_callback.on_failure.assert_called_once_with(
            "Transfer is still pending or active; Delete Local was rejected", 409,
        )
        queue_delete.assert_not_called()
        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)

    def test_process_commands_completed_file_does_not_arm_without_delete_reset(self):
        file = ModelFile("dup", False)
        file.remote_size = 10
        self.controller._Controller__persist.downloaded_file_names = {file.file_id}
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__download_start_state)

    def test_process_commands_queue_clears_stopped_file_identity(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)
        self.assertNotIn(file.file_id, self.controller._Controller__download_start_state)

    def test_process_commands_queue_does_not_match_or_clear_legacy_stopped_name_identity(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__persist.stopped_file_names = {file.name}
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.assertEqual({file.name}, self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_queue_clears_canonical_path_pair_stopped_key(self):
        pair_id = "12345678-1234-1234-1234-123456789abc"
        file = ModelFile("dup", False)
        file.path_pair_id = pair_id
        file.remote_size = 10
        self.controller._Controller__persist.stopped_file_names = {
            file.file_id,
        }
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            pair_id: SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_queue_clears_startup_migrated_stopped_key(self):
        pair_id = "12345678-1234-1234-1234-123456789abc"
        file = ModelFile("dup", False)
        file.path_pair_id = pair_id
        file.remote_size = 10
        self.controller._Controller__persist = ControllerPersist.from_str(json.dumps({
            "downloaded": [],
            "extracted": [],
            "stopped": ["{}:{}".format(pair_id, file.name)],
        }))
        self.controller._Controller__persist.canonicalize_file_identities()
        self.controller._Controller__persist.canonicalize_file_identities()
        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            pair_id: SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)

    def test_persist_key_helpers_require_canonical_keys(self):
        pair_id = "12345678-1234-1234-1234-123456789abc"
        file_name = "dup"

        self.controller._Controller__persist.downloaded_file_names = {f"{pair_id}:{file_name}"}
        self.assertFalse(self.controller._Controller__is_previously_downloaded(file_name, pair_id))
        self.controller._Controller__persist.downloaded_file_names = {f"{pair_id}{KEY_SEP}{file_name}"}
        self.assertFalse(self.controller._Controller__is_previously_downloaded(file_name, pair_id))
        self.controller._Controller__persist.downloaded_file_names = {ModelFile.build_file_id(file_name, pair_id)}
        self.assertTrue(self.controller._Controller__is_previously_downloaded(file_name, pair_id))

        self.controller._Controller__persist.stopped_file_names = {f"{pair_id}:{file_name}"}
        self.assertFalse(self.controller._Controller__is_explicitly_stopped(file_name, pair_id))
        self.controller._Controller__persist.stopped_file_names = {f"{pair_id}{KEY_SEP}{file_name}"}
        self.assertFalse(self.controller._Controller__is_explicitly_stopped(file_name, pair_id))
        self.controller._Controller__persist.stopped_file_names = {ModelFile.build_file_id(file_name, pair_id)}
        self.assertTrue(self.controller._Controller__is_explicitly_stopped(file_name, pair_id))

    def test_process_commands_delete_local_tracks_stopped_file_identity(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)
        self.assertNotIn(file.file_id, self.controller._Controller__download_start_state)

    def test_process_commands_delete_local_defers_when_delete_cap_reached(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__active_command_processes = [
            MagicMock()
            for _ in range(Controller._MAX_CONCURRENT_COMMAND_PROCESSES)
        ]

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_local_process.assert_not_called()
        self.controller.logger.debug.assert_any_call(
            "Deferring %s for '%s': %d active processes at cap",
            Controller.Command.Action.DELETE_LOCAL,
            command.filename,
            Controller._MAX_CONCURRENT_COMMAND_PROCESSES
        )
        self.assertEqual(1, self.controller._Controller__command_queue.qsize())
        deferred_command = self.controller._Controller__command_queue.get_nowait()
        self.assertIs(command, deferred_command)
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, deferred_command.action)
        self.assertEqual(
            Controller._MAX_CONCURRENT_COMMAND_PROCESSES,
            len(self.controller._Controller__active_command_processes)
        )

    def test_process_commands_delete_local_deferred_command_coalesces_duplicate_during_requeue_window(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__active_command_processes = [
            MagicMock()
            for _ in range(Controller._MAX_CONCURRENT_COMMAND_PROCESSES)
        ]
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        callback = MagicMock()
        command.add_callback(callback)
        duplicate_command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        duplicate_callback = MagicMock()
        duplicate_command.add_callback(duplicate_callback)
        self.controller.queue_command(command)

        def queue_duplicate_while_deferred(*_args):
            self.controller.queue_command(duplicate_command)

        self.controller.logger.debug.side_effect = queue_duplicate_while_deferred

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            self.controller._Controller__process_commands()

        delete_local_process.assert_not_called()
        callback.on_success.assert_not_called()
        duplicate_callback.on_success.assert_not_called()
        self.assertEqual([callback, duplicate_callback], command.callbacks)
        self.assertEqual(1, self.controller._Controller__command_queue.qsize())
        self.assertIs(command, self.controller._Controller__command_queue.get_nowait())

    def test_process_commands_delete_local_invalid_state_fails_even_when_delete_cap_reached(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DOWNLOADING
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__active_command_processes = [
            MagicMock()
            for _ in range(Controller._MAX_CONCURRENT_COMMAND_PROCESSES)
        ]
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            callback = MagicMock()
            command.add_callback(callback)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_local_process.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Local file '{}' cannot be deleted in state State.DOWNLOADING".format(command.filename),
            409
        )
        callback.on_success.assert_not_called()
        self.assertEqual(0, self.controller._Controller__command_queue.qsize())
        self.assertEqual(
            Controller._MAX_CONCURRENT_COMMAND_PROCESSES,
            len(self.controller._Controller__active_command_processes)
        )

    def test_process_commands_delete_remote_starts_when_below_delete_cap(self):
        file = ModelFile("dup", False)
        file.remote_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__active_command_processes = [
            MagicMock()
            for _ in range(Controller._MAX_CONCURRENT_COMMAND_PROCESSES - 1)
        ]

        with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            process = MagicMock()
            delete_remote_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_remote_process.assert_called_once_with(
            remote_address=unittest.mock.ANY,
            remote_username=unittest.mock.ANY,
            remote_password=None,
            remote_port=unittest.mock.ANY,
            remote_path="/remote",
            file_name=file.name
        )
        process.start.assert_called_once_with()
        self.assertEqual(
            Controller._MAX_CONCURRENT_COMMAND_PROCESSES,
            len(self.controller._Controller__active_command_processes)
        )
        event_file = self.controller._Controller__active_command_processes[-1].event_file
        self.assertEqual(file.file_id, event_file.file_id)
        self.assertIsNot(file, event_file)
        self.assertEqual(0, self.controller._Controller__command_queue.qsize())

    def test_process_commands_delete_remote_uses_nested_relative_path(self):
        root = ModelFile("release", True)
        file = ModelFile("movie.mkv", False)
        root.add_child(file)
        file.path_pair_id = "movies"
        file.remote_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id))
            self.controller._Controller__process_commands()

        self.assertEqual("release/movie.mkv", delete_remote_process.call_args.kwargs["file_name"])

    def test_process_commands_delete_remote_defers_when_delete_cap_reached(self):
        file = ModelFile("dup", False)
        file.remote_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__active_command_processes = [
            MagicMock()
            for _ in range(Controller._MAX_CONCURRENT_COMMAND_PROCESSES)
        ]

        with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            process = MagicMock()
            delete_remote_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_remote_process.assert_not_called()
        self.controller.logger.debug.assert_any_call(
            "Deferring %s for '%s': %d active processes at cap",
            Controller.Command.Action.DELETE_REMOTE,
            command.filename,
            Controller._MAX_CONCURRENT_COMMAND_PROCESSES
        )
        self.assertEqual(1, self.controller._Controller__command_queue.qsize())
        deferred_command = self.controller._Controller__command_queue.get_nowait()
        self.assertIs(command, deferred_command)
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, deferred_command.action)
        self.assertEqual(
            Controller._MAX_CONCURRENT_COMMAND_PROCESSES,
            len(self.controller._Controller__active_command_processes)
        )

    def test_process_commands_queue_is_not_throttled_by_delete_cap(self):
        file = ModelFile("dup", False)
        file.remote_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__active_command_processes = [
            MagicMock()
            for _ in range(Controller._MAX_CONCURRENT_COMMAND_PROCESSES)
        ]

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            False,
            remote_base_dir_path=None,
            local_base_dir_path="/local/incomplete"
        )
        self.assertEqual(
            Controller._MAX_CONCURRENT_COMMAND_PROCESSES,
            len(self.controller._Controller__active_command_processes)
        )
        self.assertEqual(0, self.controller._Controller__command_queue.qsize())

    @patch("controller.controller.os.path.exists")
    def test_process_commands_delete_local_prefers_staging_path_until_move(self, exists):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        exists.side_effect = lambda path: os.path.normpath(path) == os.path.normpath("/local/movies/incomplete/dup")

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_local_process.assert_called_once_with(
            local_path="/local/movies/incomplete",
            file_name="dup"
        )

    @patch("controller.controller.os.path.exists")
    def test_process_commands_delete_local_prefers_staging_temp_suffix_for_partial_file(self, exists):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        exists.side_effect = lambda path: os.path.normpath(path) == os.path.normpath("/local/movies/incomplete/dup.lftp")

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_local_process.assert_called_once_with(
            local_path="/local/movies/incomplete",
            file_name="dup.lftp"
        )

    @patch("controller.controller.os.path.exists")
    def test_process_commands_delete_local_keeps_final_path_once_moved(self, exists):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        exists.side_effect = lambda path: path == "/local/movies/dup"

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_local_process.assert_called_once_with(
            local_path="/local/movies",
            file_name="dup"
        )

    def test_process_commands_delete_local_staged_directory_skips_file_artifact_planner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            staged_directory = os.path.join(staging_root, "sample-directory")
            os.makedirs(os.path.join(staged_directory, "nested"))
            Path(os.path.join(staged_directory, "nested", "payload.bin")).write_bytes(b"payload")
            Path(os.path.join(staged_directory, "nested", "payload.bin.lftp")).write_bytes(b"partial")
            Path(os.path.join(staged_directory, "nested", "payload.bin.lftp-pget-status")).write_text(
                "status", encoding="utf-8"
            )

            file = ModelFile("sample-directory", True)
            file.path_pair_id = "movies"
            file.local_size = 10
            file.state = ModelFile.State.DEFAULT
            self.controller._Controller__model.get_file.return_value = file
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(local_path=final_root)
            }
            self.controller._Controller__path_pair_staging_paths = {
                "movies": staging_root
            }
            callback = MagicMock()
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            command.add_callback(callback)

            with patch.object(Controller, "_Controller__delete_local_artifact_plan") as artifact_plan, \
                    patch("controller.controller.DeleteLocalProcess") as delete_local_process:
                process = MagicMock()
                process.is_alive.return_value = False
                process.propagate_exception.return_value = None
                delete_local_process.return_value = process
                self.controller.queue_command(command)
                self.controller._Controller__process_commands()
                self.controller._Controller__cleanup_commands()

            artifact_plan.assert_not_called()
            delete_local_process.assert_called_once_with(
                local_path=staging_root,
                file_name="sample-directory",
            )
            process.start.assert_called_once_with()
            callback.on_success.assert_called_once_with()
            callback.on_failure.assert_not_called()

    def test_process_commands_delete_local_malformed_directory_fails_and_continues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            os.makedirs(final_root)
            os.makedirs(staging_root)

            malformed = ModelFile("../malformed", True)
            malformed.path_pair_id = "movies"
            malformed.local_size = 10
            normal = ModelFile("normal.bin", False)
            normal.path_pair_id = "movies"
            normal.local_size = 10
            model_files = {malformed.file_id: malformed, normal.file_id: normal}
            self.controller._Controller__model.get_file.side_effect = model_files.__getitem__
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(local_path=final_root)
            }
            self.controller._Controller__path_pair_staging_paths = {
                "movies": staging_root
            }
            malformed_callback = MagicMock()
            normal_callback = MagicMock()
            malformed_command = Controller.Command(
                Controller.Command.Action.DELETE_LOCAL, malformed.file_id
            )
            malformed_command.add_callback(malformed_callback)
            normal_command = Controller.Command(
                Controller.Command.Action.DELETE_LOCAL, normal.file_id
            )
            normal_command.add_callback(normal_callback)

            with patch.object(
                Controller,
                "_Controller__delete_local_artifact_plan",
                return_value=((), None),
            ) as artifact_plan, patch("controller.controller.DeleteLocalProcess") as delete_local_process:
                process = MagicMock()
                process.is_alive.return_value = False
                process.propagate_exception.return_value = None
                delete_local_process.return_value = process
                self.controller.queue_command(malformed_command)
                self.controller.queue_command(normal_command)
                self.controller._Controller__process_commands()
                self.controller._Controller__cleanup_commands()

            malformed_callback.on_success.assert_not_called()
            malformed_callback.on_failure.assert_called_once()
            self.assertEqual(409, malformed_callback.on_failure.call_args.args[1])
            artifact_plan.assert_called_once_with(normal)
            delete_local_process.assert_called_once_with(
                local_path=final_root,
                file_name="normal.bin",
            )
            process.start.assert_called_once_with()
            normal_callback.on_success.assert_called_once_with()
            normal_callback.on_failure.assert_not_called()

    def test_process_commands_delete_local_directory_keeps_split_root_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            os.makedirs(os.path.join(final_root, "sample-directory"))
            os.makedirs(os.path.join(staging_root, "sample-directory"))

            file = ModelFile("sample-directory", True)
            file.path_pair_id = "movies"
            file.local_size = 10
            file.state = ModelFile.State.DEFAULT
            self.controller._Controller__model.get_file.return_value = file
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(local_path=final_root)
            }
            self.controller._Controller__path_pair_staging_paths = {
                "movies": staging_root
            }
            callback = MagicMock()
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            command.add_callback(callback)

            with patch.object(Controller, "_Controller__delete_local_artifact_plan") as artifact_plan, \
                    patch("controller.controller.DeleteLocalProcess") as delete_local_process:
                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

            artifact_plan.assert_not_called()
            delete_local_process.assert_not_called()
            callback.on_success.assert_not_called()
            callback.on_failure.assert_called_once()
            self.assertEqual(409, callback.on_failure.call_args.args[1])

    def test_process_commands_delete_local_directory_keeps_active_root_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            os.makedirs(os.path.join(staging_root, "sample-directory"))

            file = ModelFile("sample-directory", True)
            file.path_pair_id = "movies"
            file.local_size = 10
            file.state = ModelFile.State.DEFAULT
            self.controller._Controller__model.get_file.return_value = file
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(local_path=final_root)
            }
            self.controller._Controller__path_pair_staging_paths = {
                "movies": staging_root
            }
            self.controller._Controller__last_lftp_statuses = [
                SimpleNamespace(file_id=file.file_id, state=LftpJobStatus.State.RUNNING)
            ]
            callback = MagicMock()
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            command.add_callback(callback)

            with patch.object(Controller, "_Controller__delete_local_artifact_plan") as artifact_plan, \
                    patch("controller.controller.DeleteLocalProcess") as delete_local_process:
                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

            artifact_plan.assert_not_called()
            delete_local_process.assert_not_called()
            callback.on_success.assert_not_called()
            callback.on_failure.assert_called_once()
            self.assertEqual(409, callback.on_failure.call_args.args[1])

    def test_process_commands_delete_local_regular_file_still_uses_artifact_planner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            file = ModelFile("sample.bin", False)
            file.path_pair_id = "movies"
            file.local_size = 10
            file.state = ModelFile.State.DEFAULT
            self.controller._Controller__model.get_file.return_value = file
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(local_path=final_root)
            }
            self.controller._Controller__path_pair_staging_paths = {
                "movies": staging_root
            }

            with patch.object(
                Controller,
                "_Controller__delete_local_artifact_plan",
                return_value=(("artifact",), staging_root),
            ) as artifact_plan, patch("controller.controller.DeleteLocalProcess") as delete_local_process:
                process = MagicMock()
                delete_local_process.return_value = process
                command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

            artifact_plan.assert_called_once_with(file)
            delete_local_process.assert_called_once_with(
                local_path=final_root,
                file_name="sample.bin",
                artifact_paths=("artifact",),
                artifact_root=staging_root,
            )
            process.start.assert_called_once_with()

    def test_cleanup_commands_delete_local_reports_success_after_process_completion(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        self.controller._Controller__persist.move_failure_counts = {file.file_id: 4}
        self.controller._Controller__persist.downloaded_file_names = {file.file_id}
        self.controller._Controller__persist.downloaded_timestamps = {file.file_id: 123.0}
        self.controller._Controller__persist.resume_source_identities = {file.file_id: (137, 123)}
        self.controller._Controller__pending_completion_file_names = {
            (file.name, file.path_pair_id, "lftp")
        }
        self.controller._Controller__pending_completion_progress_floors = {
            file.file_id: (10, 10)
        }
        self.controller._Controller__successful_final_move_handoff_file_ids = {file.file_id}
        self.controller._Controller__download_start_state[file.file_id] = DownloadStartLifecycleEntry(
            "notified", file.path_pair_id, datetime.now()
        )

        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        callback = MagicMock()
        command.add_callback(callback)
        process = MagicMock()
        process.is_alive.return_value = False
        process.propagate_exception.return_value = None
        post_callback = MagicMock()
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command,
                file_id=file.file_id,
                file_name=file.name,
                process=process,
                post_callback=post_callback,
                await_completion=True
            )
        ]

        self.controller._Controller__cleanup_commands()

        post_callback.assert_called_once_with()
        self.controller._Controller__model_builder.confirm_local_deletions.assert_called_once_with(
            {file.file_id}
        )
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        process.close_queues.assert_called_once_with()
        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)
        self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)
        self.assertEqual({file.file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({file.file_id: 123.0}, self.controller._Controller__persist.downloaded_timestamps)
        self.assertEqual({}, self.controller._Controller__persist.resume_source_identities)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)
        self.assertEqual({}, self.controller._Controller__pending_completion_progress_floors)
        self.assertEqual(set(), self.controller._Controller__successful_final_move_handoff_file_ids)
        self.assertEqual("fresh_after_delete", self.controller._Controller__download_start_state[file.file_id].state)
        self.controller._Controller__model.get_file.return_value = file
        file.remote_size = 10
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.assertEqual("eligible", self.controller._Controller__download_start_state[file.file_id].state)
        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)

    def test_cleanup_commands_delete_local_surfaces_missing_file_failure(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        self.controller._Controller__download_start_state[file.file_id] = DownloadStartLifecycleEntry(
            "notified", file.path_pair_id, datetime.now()
        )

        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        callback = MagicMock()
        command.add_callback(callback)
        process = MagicMock()
        process.is_alive.return_value = False
        process.propagate_exception.side_effect = FileNotFoundError("/local/movies/incomplete/dup.lftp")
        post_callback = MagicMock()
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command,
                file_id=file.file_id,
                file_name=file.name,
                process=process,
                post_callback=post_callback,
                await_completion=True
            )
        ]

        self.controller._Controller__cleanup_commands()

        post_callback.assert_not_called()
        callback.on_success.assert_not_called()
        callback.on_failure.assert_called_once_with("File 'dup' does not exist locally", 404)
        process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        process.close_queues.assert_called_once_with()
        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)
        self.assertEqual("notified", self.controller._Controller__download_start_state[file.file_id].state)

    def test_cleanup_commands_delete_remote_logs_failed_async_cleanup_without_crashing(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED

        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id)
        process = MagicMock()
        process.name = "DeleteRemoteProcess"
        process.is_alive.return_value = False
        process.propagate_exception.side_effect = Exception("boom")
        post_callback = self.controller._Controller__remote_scan_process.force_scan
        remote_delete_listener = MagicMock()
        self.controller.add_remote_delete_success_listener(remote_delete_listener)
        self.controller._Controller__download_start_state[file.file_id] = DownloadStartLifecycleEntry(
            "notified", file.path_pair_id, datetime.now()
        )
        self.controller._Controller__persist.downloaded_file_names = {file.file_id}
        self.controller._Controller__persist.downloaded_timestamps = {file.file_id: 123.0}
        self.controller._Controller__persist.move_failure_counts = {file.file_id: 4}
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command,
                file_id=file.file_id,
                file_name=file.name,
                process=process,
                post_callback=post_callback,
                await_completion=False,
                event_file=copy.deepcopy(file),
            )
        ]

        self.controller._Controller__cleanup_commands()

        post_callback.assert_called_once_with()
        self.controller.logger.warning.assert_called_once_with(
            "Command process failed: %s",
            "DeleteRemoteProcess",
            exc_info=True
        )
        breadcrumb_calls = [
            call
            for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
            if len(call.args) >= 2 and call.args[1] in {"command_failed", "command_finished"}
        ]
        self.assertEqual(["command_failed"], [call.args[1] for call in breadcrumb_calls])
        self.assertEqual(500, breadcrumb_calls[0].args[2]["error_code"])
        self.assertNotIn("completion", breadcrumb_calls[0].args[2])
        process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        process.close_queues.assert_called_once_with()
        self.assertEqual([], self.controller._Controller__active_command_processes)
        remote_delete_listener.assert_not_called()
        self.assertEqual("notified", self.controller._Controller__download_start_state[file.file_id].state)
        self.assertEqual({file.file_id}, self.controller._Controller__persist.downloaded_file_names)

    def test_cleanup_commands_delete_remote_records_success_breadcrumb_when_async_cleanup_completes(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED

        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id)
        process = MagicMock()
        process.name = "DeleteRemoteProcess"
        process.is_alive.return_value = False
        process.propagate_exception.return_value = None
        post_callback = self.controller._Controller__remote_scan_process.force_scan
        remote_delete_listener = MagicMock()
        self.controller.add_remote_delete_success_listener(remote_delete_listener)
        self.controller._Controller__download_start_state[file.file_id] = DownloadStartLifecycleEntry(
            "notified", file.path_pair_id, datetime.now()
        )
        self.controller._Controller__persist.downloaded_file_names = {file.file_id}
        self.controller._Controller__persist.move_failure_counts = {file.file_id: 4}
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command,
                file_id=file.file_id,
                file_name=file.name,
                process=process,
                post_callback=post_callback,
                await_completion=False,
                event_file=copy.deepcopy(file),
            )
        ]

        self.controller._Controller__cleanup_commands()

        post_callback.assert_called_once_with()
        self.controller.logger.warning.assert_not_called()
        breadcrumb_calls = [
            call
            for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
            if len(call.args) >= 2 and call.args[1] in {"command_failed", "command_finished"}
        ]
        self.assertEqual(["command_finished"], [call.args[1] for call in breadcrumb_calls])
        self.assertEqual("completed", breadcrumb_calls[0].args[2]["completion"])
        process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        process.close_queues.assert_called_once_with()
        self.assertEqual([], self.controller._Controller__active_command_processes)
        remote_delete_listener.assert_called_once()
        self.assertNotIn(file.file_id, self.controller._Controller__download_start_state)
        self.assertEqual(set(), self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({}, self.controller._Controller__persist.downloaded_timestamps)
        self.assertEqual((0, 1), self.controller.remote_delete_lifecycle_token(file))
        self.assertEqual(
            {file.file_id: 4},
            self.controller._Controller__persist.move_failure_counts,
        )
        self.controller._Controller__model.get_file.return_value = file
        file.state = ModelFile.State.DEFAULT
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.assertEqual("eligible", self.controller._Controller__download_start_state[file.file_id].state)
        completed_file = remote_delete_listener.call_args.args[0]
        self.assertEqual(file.file_id, completed_file.file_id)

    def test_cleanup_commands_delete_remote_clears_nested_downloaded_marker(self):
        root = ModelFile("release", True)
        file = ModelFile("movie.mkv", False)
        root.add_child(file)
        file.path_pair_id = "movies"
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id)
        process = MagicMock()
        process.name = "DeleteRemoteProcess"
        process.is_alive.return_value = False
        process.propagate_exception.return_value = None
        self.controller._Controller__persist.downloaded_file_names = {file.file_id}
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command, file_id=file.file_id, file_name=file.full_path,
                process=process, post_callback=MagicMock(), await_completion=False,
                event_file=copy.deepcopy(file),
            )
        ]

        self.controller._Controller__cleanup_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__persist.downloaded_file_names)

    def test_cleanup_commands_delete_local_times_out_stale_processes(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        callback = MagicMock()
        command.add_callback(callback)
        duplicate_command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.name)
        duplicate_callback = MagicMock()
        duplicate_command.add_callback(duplicate_callback)
        process = MagicMock()
        process.name = "DeleteLocalProcess"
        process.is_alive.return_value = True
        process.propagate_exception.return_value = None
        process.terminate.return_value = None
        post_callback = self.controller._Controller__local_scan_process.force_scan
        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command,
                file_id=file.file_id,
                file_name=file.name,
                process=process,
                post_callback=post_callback,
                await_completion=True,
                started_at_monotonic=(
                    time.monotonic() - Controller._DELETE_COMMAND_STALE_TIMEOUT_IN_SECS - 1
                )
            )
        ]

        self.controller.queue_command(duplicate_command)
        self.controller._Controller__cleanup_commands()

        post_callback.assert_not_called()
        duplicate_callback.on_success.assert_not_called()
        process.terminate.assert_called_once_with()
        process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        process.close_queues.assert_not_called()
        callback.on_failure.assert_called_once_with("Delete command for file 'dup' timed out", 504)
        duplicate_callback.on_failure.assert_called_once_with("Delete command for file 'dup' timed out", 504)
        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)
        self.assertEqual([], self.controller._Controller__active_command_processes)
        breadcrumb_calls = [
            call
            for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
            if len(call.args) >= 2 and call.args[1] == "command_failed"
        ]
        self.assertEqual(1, len(breadcrumb_calls))
        self.assertEqual(504, breadcrumb_calls[0].args[2]["error_code"])
        self.assertEqual("timed_out", breadcrumb_calls[0].args[2]["completion"])

    def test_cleanup_commands_delete_remote_times_out_stale_processes(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED

        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id)
        process = MagicMock()
        process.name = "DeleteRemoteProcess"
        process.is_alive.return_value = True
        process.propagate_exception.return_value = None
        process.terminate.return_value = None
        post_callback = self.controller._Controller__remote_scan_process.force_scan
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command,
                file_id=file.file_id,
                file_name=file.name,
                process=process,
                post_callback=post_callback,
                await_completion=False,
                started_at_monotonic=(
                    time.monotonic() - Controller._DELETE_COMMAND_STALE_TIMEOUT_IN_SECS - 1
                )
            )
        ]

        self.controller._Controller__cleanup_commands()

        post_callback.assert_not_called()
        process.terminate.assert_called_once_with()
        process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        process.close_queues.assert_not_called()
        self.assertEqual([], self.controller._Controller__active_command_processes)
        breadcrumb_calls = [
            call
            for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
            if len(call.args) >= 2 and call.args[1] == "command_failed"
        ]
        self.assertEqual(1, len(breadcrumb_calls))
        self.assertEqual(504, breadcrumb_calls[0].args[2]["error_code"])
        self.assertEqual("timed_out", breadcrumb_calls[0].args[2]["completion"])

    def test_queue_delete_local_process_without_command_uses_synthetic_no_callback_command(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            post_callback = MagicMock()

            self.controller._Controller__queue_delete_local_process(file, post_callback)

        self.assertEqual(1, len(self.controller._Controller__active_command_processes))
        command_wrapper = self.controller._Controller__active_command_processes[0]
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, command_wrapper.command.action)
        self.assertEqual(file.file_id, command_wrapper.command.filename)
        self.assertEqual([], command_wrapper.command.callbacks)
        self.assertTrue(command_wrapper.await_completion)
        self.assertIs(post_callback, command_wrapper.post_callback)

    def test_process_commands_delete_local_preserves_callbacks_for_successful_cleanup(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            process.is_alive.return_value = False
            process.propagate_exception.return_value = None
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            callback = MagicMock()
            command.add_callback(callback)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()
            self.controller._Controller__cleanup_commands()

        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with("movies")

    def _prepare_absent_terminal_move_delete(self):
        pair = PathPair(
            id="movies",
            name="Movies",
            remote_path="downloads/public/Movies",
            local_path="/local/movies",
            enabled=True,
        )
        file = ModelFile("Example Series", True)
        file.path_pair_id = pair.id
        file.path_pair_name = pair.name
        file.remote_size = 137
        file.local_size = None
        file.state = ModelFile.State.MOVE_FAILED
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {pair.id: pair}
        self.controller._Controller__persist.move_failure_counts = {file.file_id: 4}
        self.controller._Controller__persist.downloaded_file_names = {file.file_id}
        self.controller._Controller__persist.downloaded_timestamps = {file.file_id: 123.0}
        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        self.controller._Controller__reconciled_local_path_pair_ids = {pair.id}
        self.controller._Controller__reconciled_remote_path_pair_ids = {pair.id}
        self.controller._Controller__last_local_reconciliation_healthy = True
        self.controller._Controller__last_remote_reconciliation_healthy = True
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__lftp_status_poll_retry_active = False
        self.controller._Controller__lftp_idle_status_authoritative = True
        self.controller._Controller__lftp_status_cache_expires_at = datetime.now() - timedelta(seconds=30)
        self.controller._Controller__last_lftp_statuses = []
        self.controller._Controller__pending_queue_dispatches = {}
        self.controller._Controller__model_builder.get_unresolved_staging_collision_file_ids.return_value = set()
        return file

    @patch("controller.controller.DeleteLocalProcess")
    def test_delete_local_explicitly_repairs_absent_terminal_move_failure_metadata(self, delete_local_process):
        file = self._prepare_absent_terminal_move_delete()
        self.controller._Controller__download_start_state[file.file_id] = DownloadStartLifecycleEntry(
            "notified", file.path_pair_id, datetime.now()
        )
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        delete_local_process.assert_not_called()
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        self.controller._Controller__validate_process.clear.assert_called_once_with(file.file_id)
        self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)
        self.assertEqual({file.file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual({file.file_id: 123.0}, self.controller._Controller__persist.downloaded_timestamps)
        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)
        self.assertEqual("fresh_after_delete", self.controller._Controller__download_start_state[file.file_id].state)
        self.controller._Controller__model_builder.set_move_failed_files.assert_called_with(set())
        self.controller._Controller__model_builder.has_unresolved_staging_collision.assert_not_called()

    @patch("controller.controller.DeleteLocalProcess")
    def test_delete_local_absent_terminal_move_failure_repair_fails_closed(self, delete_local_process):
        cases = (
            ("unreconciled", lambda file: self.controller._Controller__reconciled_local_path_pair_ids.clear()),
            ("remote absent", lambda file: setattr(file, "remote_present", False)),
            ("collision", lambda file: self.controller._Controller__model_builder.get_unresolved_staging_collision_file_ids.configure_mock(
                return_value={file.file_id}
            )),
            ("active transfer", lambda file: self.controller._Controller__last_lftp_statuses.append(
                SimpleNamespace(
                    file_id=file.file_id,
                    path_pair_id=file.path_pair_id,
                    state=LftpJobStatus.State.RUNNING,
                )
            )),
            ("pending completion", lambda file: self.controller._Controller__pending_completion_file_names.add(
                (file.name, file.path_pair_id, file.path_pair_name)
            )),
            ("disabled pair", lambda file: setattr(
                self.controller._Controller__path_pairs_by_id[file.path_pair_id], "enabled", False
            )),
            ("unscoped file", lambda file: setattr(file, "path_pair_id", None)),
        )
        for label, make_unsafe in cases:
            with self.subTest(label=label):
                file = self._prepare_absent_terminal_move_delete()
                persisted_file_id = file.file_id
                make_unsafe(file)
                callback = MagicMock()
                command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
                command.add_callback(callback)

                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

                callback.on_success.assert_not_called()
                callback.on_failure.assert_called_once()
                self.assertEqual(409, callback.on_failure.call_args.args[1])
                self.assertEqual(4, self.controller._Controller__persist.move_failure_counts[persisted_file_id])
        delete_local_process.assert_not_called()

    @patch("controller.controller.DeleteLocalProcess")
    def test_delete_local_absent_non_move_failure_remains_missing(self, delete_local_process):
        file = ModelFile("missing", False)
        file.remote_size = 10
        file.local_size = None
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_success.assert_not_called()
        callback.on_failure.assert_called_once_with("File 'missing' does not exist locally", 404)
        delete_local_process.assert_not_called()

    def test_delete_local_command_lifecycle_breadcrumbs_keep_same_flow_id(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.path_pair_name = "Movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            process.is_alive.return_value = False
            process.propagate_exception.return_value = None
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
            self.controller._Controller__cleanup_commands()

        lifecycle_entries = [
            call.kwargs
            for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
            if len(call.args) >= 2 and call.args[1] in {
                "command_queued",
                "command_dequeued",
                "command_dispatched",
                "command_finished",
            }
        ]
        self.assertEqual(4, len(lifecycle_entries))
        flow_ids = {entry.get("flow_id") for entry in lifecycle_entries}
        self.assertEqual(1, len(flow_ids))
        self.assertEqual({"cmd:delete_local:{}:1".format(file.file_id)}, flow_ids)
        self.assertEqual(
            ["command_queued", "command_dequeued", "command_dispatched", "command_finished"],
            [
                call.args[1]
                for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
                if len(call.args) >= 2 and call.args[1] in {
                    "command_queued", "command_dequeued", "command_dispatched", "command_finished"
                }
            ]
        )

    def test_process_commands_delete_local_preserves_callbacks_for_failed_cleanup(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            process.is_alive.return_value = False
            process.propagate_exception.side_effect = FileNotFoundError("/local/movies/dup")
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            callback = MagicMock()
            command.add_callback(callback)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()
            self.controller._Controller__cleanup_commands()

        callback.on_success.assert_not_called()
        callback.on_failure.assert_called_once_with("File 'dup' does not exist locally", 404)
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    def test_process_commands_validate_queues_validation(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__validate_process.validate.assert_called_once_with(file)

    def test_first_auxiliary_dispatch_starts_worker_once(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        validate_process = self.controller._Controller__validate_process
        validate_process.start.side_effect = lambda: setattr(validate_process, "pid", 123)
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.VALIDATE, "dup"))
        self.controller.queue_command(Controller.Command(Controller.Command.Action.VALIDATE, "dup"))
        self.controller._Controller__process_commands()

        validate_process.start.assert_called_once_with()
        self.assertEqual(2, validate_process.validate.call_count)
        self.assertEqual({file.file_id}, self.controller._Controller__pending_validation_file_ids)

    def test_auxiliary_worker_reap_waits_for_all_inflight_dispatches(self):
        extract_process = self.controller._Controller__extract_process
        extract_process.pid = 123
        self.controller._Controller__pending_extract_file_ids = {"first", "second"}
        self.controller._Controller__extract_idle_deadline_monotonic = 0

        self.controller._Controller__reap_idle_auxiliary_workers()

        extract_process.terminate.assert_not_called()
        self.assertIsNone(self.controller._Controller__extract_idle_deadline_monotonic)

    def test_terminal_observation_reaps_and_replaces_idle_auxiliary_worker(self):
        extract_process = self.controller._Controller__extract_process
        extract_process.pid = 123
        self.controller._Controller__pending_extract_file_ids = {"done"}
        self.controller._record_worker_terminal_ids({"done"}, set())
        self.controller._Controller__extract_idle_deadline_monotonic = 0

        with patch.object(self.controller, "_Controller__teardown_process", return_value=True) as teardown, \
                patch.object(self.controller, "_Controller__replace_extract_process") as replace:
            self.controller._Controller__reap_idle_auxiliary_workers()

        teardown.assert_called_once_with("idle extract process", extract_process)
        replace.assert_called_once_with()

    def test_next_extract_dispatch_starts_replacement_worker(self):
        replacement = MagicMock()
        replacement.pid = None
        replacement.start.side_effect = lambda: setattr(replacement, "pid", 456)
        self.controller._Controller__extract_process = replacement

        self.controller._Controller__ensure_extract_worker_started()

        replacement.start.assert_called_once_with()

    def test_dormant_auxiliary_workers_accept_clear_and_path_refresh_without_start(self):
        self.controller._Controller__context.config.controller = SimpleNamespace(
            use_local_path_as_extract_path=True,
            extract_path=None,
        )

        self.controller._Controller__validate_process.clear("stale")
        self.controller._Controller__apply_runtime_fallback_paths("/new-local", "/new-remote", "/new-local/incomplete")

        self.controller._Controller__extract_process.start.assert_not_called()
        self.controller._Controller__validate_process.start.assert_not_called()
        self.controller._Controller__validate_process.clear.assert_called_once_with("stale")
        self.controller._Controller__extract_process.set_base_paths.assert_called_once_with(
            out_dir_path="/new-local",
            local_path="/new-local",
            local_path_fallback="/new-local/incomplete",
        )
        self.controller._Controller__validate_process.set_base_paths.assert_called_once_with("/new-local", "/new-remote")

    def test_exit_before_first_auxiliary_dispatch_closes_dormant_workers(self):
        self.controller._Controller__started = True
        self._set_exit_worker_processes_not_alive()

        self.controller.exit()

        self.controller._Controller__extract_process.start.assert_not_called()
        self.controller._Controller__validate_process.start.assert_not_called()
        self.controller._Controller__extract_process.terminate.assert_not_called()
        self.controller._Controller__validate_process.terminate.assert_not_called()
        self.controller._Controller__extract_process.close_queues.assert_called_once_with()
        self.controller._Controller__validate_process.close_queues.assert_called_once_with()

    def test_process_commands_extract_passes_flow_id_to_extract_process(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 20
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__context.config.controller = SimpleNamespace(
            use_local_path_as_extract_path=False,
            extract_path="/extract",
            managed_extract_folders_enabled=True,
        )
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.EXTRACT, "dup", flow_id="flow-123")
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        extract_request = self.controller._Controller__extract_process.extract.call_args.args[0]
        self.assertIsInstance(extract_request, ExtractRequest)
        self.assertIs(extract_request.model_file, file)
        self.assertIsNone(extract_request.pair_id)
        self.assertEqual("/local/incomplete", extract_request.local_path)
        self.assertEqual("/local/incomplete", extract_request.out_dir_path)
        self.assertEqual("/local", extract_request.local_path_fallback)
        self.assertEqual("/extract", extract_request.out_dir_path_fallback)
        self.controller._Controller__extract_process.extract.assert_called_once_with(
            extract_request,
            flow_id="flow-123",
        )

    def test_process_commands_extract_rejects_unknown_path_pair(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "missing"
        file.local_size = 10
        file.remote_size = 20
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__context.config.controller = SimpleNamespace(
            use_local_path_as_extract_path=False,
            extract_path="/extract",
            managed_extract_folders_enabled=True,
        )
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.EXTRACT, "dup")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with(
            "Path pair 'missing' is unavailable for extraction",
            404,
        )
        self.controller._Controller__extract_process.extract.assert_not_called()

    def test_process_commands_extract_uses_pair_specific_staging_request(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.remote_size = 20
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__context.config.controller = SimpleNamespace(
            use_local_path_as_extract_path=False,
            extract_path="/extract",
            managed_extract_folders_enabled=True,
        )
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies", remote_path="/remote/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete",
        }
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.EXTRACT, "dup")
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        extract_request = self.controller._Controller__extract_process.extract.call_args.args[0]
        self.assertIsInstance(extract_request, ExtractRequest)
        self.assertEqual("movies", extract_request.pair_id)
        self.assertEqual("/local/movies/incomplete", extract_request.local_path)
        self.assertEqual("/local/movies/incomplete", extract_request.out_dir_path)
        self.assertEqual("/local/movies", extract_request.local_path_fallback)
        self.assertEqual("/extract", extract_request.out_dir_path_fallback)
        self.controller._Controller__extract_process.extract.assert_called_once_with(
            extract_request,
            flow_id=command.flow_id,
        )

    def test_process_commands_extract_failure_still_processes_later_commands(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 20
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__extract_process.extract.side_effect = RuntimeError("extract worker dead")
        self.controller._Controller__validate_process.validate.return_value = None

        extract_command = Controller.Command(Controller.Command.Action.EXTRACT, "dup")
        extract_callback = MagicMock()
        extract_command.add_callback(extract_callback)
        validate_command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        validate_callback = MagicMock()
        validate_command.add_callback(validate_callback)
        self.controller.queue_command(extract_command)
        self.controller.queue_command(validate_command)

        self.controller._Controller__process_commands()

        extract_callback.on_failure.assert_called_once_with("Extract worker unavailable", 500)
        validate_callback.on_success.assert_called_once_with()
        self.controller.logger.warning.assert_any_call(
            "Extract worker dispatch failed for %s",
            file.file_id,
            exc_info=True
        )
        self.controller._Controller__validate_process.validate.assert_called_once_with(file)

    def test_process_commands_validate_failure_still_processes_later_commands(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 20
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__validate_process.validate.side_effect = [
            RuntimeError("validate worker dead"),
            None
        ]

        first_command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        first_callback = MagicMock()
        first_command.add_callback(first_callback)
        second_command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        second_callback = MagicMock()
        second_command.add_callback(second_callback)
        self.controller.queue_command(first_command)
        self.controller.queue_command(second_command)

        self.controller._Controller__process_commands()

        first_callback.on_failure.assert_called_once_with("Validate worker unavailable", 500)
        second_callback.on_success.assert_called_once_with()
        self.controller.logger.warning.assert_any_call(
            "Validate worker dispatch failed for %s",
            file.file_id,
            exc_info=True
        )
        self.assertEqual(2, self.controller._Controller__validate_process.validate.call_count)

    def test_process_commands_validate_rejects_missing_remote_file(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = None
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("File 'dup' does not exist remotely", 404)
        self.controller._Controller__validate_process.validate.assert_not_called()

    def test_process_commands_validate_rejects_stopped_partial_file(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 20
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with(
            "File 'dup' in state State.DEFAULT cannot be validated",
            409
        )
        self.controller._Controller__validate_process.validate.assert_not_called()

    def test_log_memory_usage_reports_current_controller_collection_sizes(self):
        self.controller._Controller__model.file_count = 3
        self.controller._Controller__persist.downloaded_file_names = {"d1", "d2"}
        self.controller._Controller__persist.extracted_file_names = {"e1"}
        self.controller._Controller__persist.stopped_file_names = {"s1", "s2", "s3"}
        self.controller._Controller__active_downloading_file_names = [("down", None, None)]
        self.controller._Controller__active_extracting_file_names = [("extract", None, None), ("extract2", None, None)]
        self.controller._Controller__active_command_processes = [MagicMock(), MagicMock()]

        self.controller._Controller__log_memory_usage()

        self.controller._Controller__memory_monitor.log_if_due.assert_called_once_with(
            model_file_count=3,
            downloaded_file_count=2,
            extracted_file_count=1,
            stopped_file_count=3,
            active_download_count=1,
            active_extract_count=2,
            active_command_count=2
        )

    def test_snapshot_counter_only_advances_for_actual_full_snapshot_registration(self):
        diagnostics = PerformanceDiagnosticsCollector(lambda: True)
        self.controller._Controller__context.performance_diagnostics = diagnostics
        self.controller._Controller__model.file_count = 0
        self.controller._Controller__model.get_file_ids.return_value = []

        self.controller._Controller__log_memory_usage()
        self.assertEqual(0, diagnostics.snapshot()["counters"]["model_full_snapshot_requests"])

        self.controller.get_model_files_and_add_listener(MagicMock())
        self.assertEqual(1, diagnostics.snapshot()["counters"]["model_full_snapshot_requests"])
        self.assertEqual(1, diagnostics.snapshot()["counters"]["model_full_snapshot_listener_registrations"])

    def test_build_staging_path_prefers_explicit_single_path_override(self):
        self.assertEqual(
            "/custom/staging",
            self.controller._Controller__build_staging_path("/local", "/custom/staging")
        )

    def test_build_path_pair_staging_path_uses_explicit_root_and_stable_safe_subdir(self):
        pair = PathPair(
            id="../movies",
            name="Renamable movies",
            remote_path="/remote/movies",
            local_path="/local/movies",
        )
        self.controller._Controller__explicit_staging_path_configured = True
        self.controller._Controller__staging_path = "/custom/staging"

        staging_path = self.controller._Controller__build_path_pair_staging_path(pair)
        pair.name = "A different display name"

        self.assertEqual("/custom/staging", os.path.dirname(staging_path))
        self.assertRegex(os.path.basename(staging_path), r"^[0-9a-f]{16}$")
        self.assertEqual(
            staging_path,
            self.controller._Controller__build_path_pair_staging_path(pair),
        )

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_uses_single_path_roots(self, _, move):
        result = self.controller._Controller__move_from_staging("movie.mkv")

        self.assertEqual(
            (os.path.normpath("/local/incomplete/movie.mkv"), os.path.normpath("/local/movie.mkv")),
            tuple(os.path.normpath(path) for path in move.call_args.args[:2])
        )
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_uses_path_pair_roots(self, _, move):
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        self.controller._Controller__move_from_staging("movie.mkv", "movies")

        self.assertEqual(
            (
                os.path.normpath("/local/movies/incomplete/movie.mkv"),
                os.path.normpath("/local/movies/movie.mkv")
            ),
            tuple(os.path.normpath(path) for path in move.call_args.args[:2])
        )
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with("movies")

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_invalidates_current_generation_before_rescan(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            os.makedirs(os.path.join(staging_root, "nested"))
            os.makedirs(final_root)
            Path(os.path.join(staging_root, "nested", "movie.mkv")).write_bytes(b"payload")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            self.controller._Controller__local_scan_process.generation = 58
            self.controller._Controller__updater.begin_final_move_local_root_invalidation.return_value = 17

            move.side_effect = lambda *_args: _args[-1].mutated()
            result = self.controller._Controller__move_from_staging("nested/movie.mkv")

            self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
            self.assertTrue(os.path.isdir(os.path.join(final_root, "nested")))
            self.controller._Controller__updater.begin_final_move_local_root_invalidation.assert_called_once_with(
                "nested", None, 58,
            )
            move.assert_called_once()
            self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()
            self.controller._Controller__updater.finish_final_move_local_root_invalidation.assert_called_once_with(
                "nested", None, 17, True,
            )

    @patch.object(Controller, "_Controller__publish_staging_no_replace", side_effect=OSError("denied"))
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_cancels_local_invalidation_after_failure(self, _, move):
        self.controller._Controller__local_scan_process.generation = 58
        self.controller._Controller__updater.begin_final_move_local_root_invalidation.return_value = 17

        result = self.controller._Controller__move_from_staging("movie.mkv")

        self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
        self.controller._Controller__updater.begin_final_move_local_root_invalidation.assert_called_once_with(
            "movie.mkv", None, 58,
        )
        self.controller._Controller__updater.finish_final_move_local_root_invalidation.assert_called_once_with(
            "movie.mkv", None, 17, False,
        )
        move.assert_called_once()

    def test_native_publish_then_validation_error_retains_mutation_outcome(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.bin")
            destination = os.path.join(temp_dir, "destination.bin")
            Path(source).write_bytes(b"payload")
            tracker = _MoveMutationTracker()

            with patch.object(Controller, "_Controller__sync_directory_if_supported",
                              side_effect=OSError(errno.EIO, "sync failed")):
                with self.assertRaises(OSError):
                    Controller._Controller__publish_staging_no_replace(source, destination, tracker)

            self.assertEqual(_MoveMutationOutcome.MUTATED, tracker.outcome)
            self.assertTrue(os.path.exists(destination))

    def test_fallback_publish_failure_retains_uncertain_mutation_outcome(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.bin")
            destination = os.path.join(temp_dir, "destination.bin")
            Path(source).write_bytes(b"payload")
            tracker = _MoveMutationTracker()

            with patch.object(Controller, "_Controller__rename_no_replace",
                              side_effect=OSError(errno.EINVAL, "unsupported")), \
                    patch.object(Controller, "_Controller__copy_to_publish_temporary",
                                 side_effect=OSError(errno.ENOSPC, "full")):
                with self.assertRaises(OSError):
                    Controller._Controller__publish_staging_no_replace(source, destination, tracker)

            self.assertEqual(_MoveMutationOutcome.UNCERTAIN, tracker.outcome)

    def test_regular_fallback_missing_destination_parent_after_temporary_creation_is_private_and_source_safe(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=32)
        self.controller._Controller__context.breadcrumb_trace = trace
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "staging")
            final_root = os.path.join(temp_dir, "final")
            residue_root = os.path.join(temp_dir, "residue")
            os.mkdir(staging_root)
            os.mkdir(final_root)
            os.mkdir(residue_root)
            source = os.path.join(staging_root, "sample-file.bin")
            destination = os.path.join(final_root, "sample-file.bin")
            temporary_path = os.path.join(residue_root, "temporary.bin")
            Path(source).write_bytes(b"payload")
            Path(temporary_path).write_bytes(b"payload")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            file_id = ModelFile.build_file_id("sample-file.bin", None)
            rename_calls = 0

            def disappear_destination_parent(_source: str, _destination: str) -> None:
                nonlocal rename_calls
                rename_calls += 1
                if rename_calls == 1:
                    raise OSError(errno.EINVAL, "capability fallback")
                os.rmdir(final_root)
                raise OSError(errno.ENOENT, "destination parent disappeared")

            def preserve_temporary_residue(path: str) -> None:
                if path == temporary_path:
                    raise OSError(errno.EACCES, "temporary cleanup denied")
                os.unlink(path)

            with patch.object(
                    Controller, "_Controller__rename_no_replace",
                    side_effect=disappear_destination_parent), \
                    patch.object(
                        Controller, "_Controller__copy_to_publish_temporary",
                        return_value=(temporary_path, None),
                    ), \
                    patch("controller.controller.os.unlink", side_effect=preserve_temporary_residue):
                result = self.controller._Controller__move_from_staging("sample-file.bin")

            os.unlink(temporary_path)

            self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
            self.assertTrue(os.path.exists(source))
            self.assertFalse(os.path.exists(destination))
            entries = trace.snapshot()["entries"]
            self.assertEqual(
                ["fallback", "reserve_start", "create_temporary", "copy", "publish", "cleanup"],
                [entry["details"]["phase"] for entry in entries],
            )
            publish_details = entries[-2]["details"]
            self.assertEqual("enoent", publish_details["errno_class"])
            self.assertTrue(publish_details["temporary_created"])
            self.assertFalse(publish_details["destination_parent_exists"])
            self.assertTrue(publish_details["source_exists"])
            self.assertFalse(publish_details["destination_exists"])
            self.assertTrue(publish_details["residue_present"])
            cleanup_details = entries[-1]["details"]
            self.assertEqual("permission", cleanup_details["errno_class"])
            self.assertTrue(cleanup_details["residue_present"])
            self.assertTrue(cleanup_details["temporary_created"])
            self.assertEqual("final_move.publication", entries[-1]["category"])
            self.assertEqual("final_move_publication", entries[-1]["stage"])
            self.assertEqual("info", entries[-1]["level"])
            self.assertEqual(opaque_trace_correlation(file_id), entries[-1]["corr_id"])
            for entry in entries:
                self.assertNotIn("sample-file.bin", str(entry))
                self.assertNotIn(file_id, str(entry))
                self.assertTrue({
                    "phase", "operation", "side", "errno_class", "mutation_state",
                }.issubset(entry["details"]))
                self.assertTrue(set(entry["details"]).issubset({
                    "phase", "operation", "side", "errno_class", "mutation_state",
                    "temporary_created", "residue_present", "source_exists",
                    "destination_parent_exists", "destination_exists",
                }))

    def test_collision_sidecar_partial_write_retains_uncertain_mutation_outcome(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tracker = _MoveMutationTracker()
            sidecar = os.path.join(temp_dir, ".seedsync-retire-" + "a" * 48 + ".json")

            with patch("controller.controller.os.write", side_effect=OSError(errno.EIO, "write failed")):
                with self.assertRaises(OSError):
                    Controller._Controller__write_collision_claim_sidecar(
                        sidecar, "movie.mkv", None, tracker,
                    )

            self.assertEqual(_MoveMutationOutcome.UNCERTAIN, tracker.outcome)
            self.assertTrue(os.path.exists(sidecar))

    def test_collision_sidecar_cleanup_sync_failure_retains_uncertain_mutation_outcome(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tracker = _MoveMutationTracker()
            sidecar = os.path.join(temp_dir, ".seedsync-retire-" + "b" * 48 + ".json")
            Controller._Controller__write_collision_claim_sidecar(sidecar, "movie.mkv", None)

            with patch.object(Controller, "_Controller__sync_directory_if_supported",
                              side_effect=OSError(errno.EIO, "sync failed")):
                with self.assertRaises(OSError):
                    Controller._Controller__remove_collision_claim_sidecar(sidecar, tracker)

            self.assertEqual(_MoveMutationOutcome.UNCERTAIN, tracker.outcome)
            self.assertFalse(os.path.exists(sidecar))

    def test_directory_fallback_post_mkdir_validation_failure_retains_mutation_outcome(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source")
            destination = os.path.join(temp_dir, "destination")
            os.mkdir(source)
            Path(os.path.join(source, "child.bin")).write_bytes(b"payload")
            tracker = _MoveMutationTracker()

            with patch.object(Controller, "_Controller__same_path_identity", return_value=False):
                with self.assertRaises(OSError):
                    Controller._Controller__publish_temporary_directory(source, destination, tracker)

            self.assertEqual(_MoveMutationOutcome.MUTATED, tracker.outcome)
            self.assertTrue(os.path.isdir(destination))

    @patch("controller.controller.os.path.exists", return_value=True)
    def test_directory_merge_partial_mutation_commits_local_invalidation(self, _):
        self.controller._Controller__local_scan_process.generation = 58
        self.controller._Controller__updater.begin_final_move_local_root_invalidation.return_value = 17
        self.controller._Controller__safe_existing_directory = MagicMock(return_value=True)

        def partial_merge(*args):
            args[-1].mutated()
            return False

        self.controller._Controller__merge_staging_directory_no_replace = MagicMock(side_effect=partial_merge)

        self.assertEqual(Controller.MoveFromStagingResult.CONFLICT,
                         self.controller._Controller__move_from_staging("directory"))
        self.controller._Controller__updater.finish_final_move_local_root_invalidation.assert_called_once_with(
            "directory", None, 17, True,
        )

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_moves_single_file_named_lftp(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_file = os.path.join(staging_root, "notes.lftp")
            os.makedirs(staging_root)
            os.makedirs(final_root)
            with open(source_file, "w", encoding="utf-8") as temp_file:
                temp_file.write("complete")

            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("notes.lftp")

        move.assert_called_once_with(source_file, os.path.join(final_root, "notes.lftp"), ANY)
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_ignores_unrelated_lftp_sibling_for_single_file_source(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_file = os.path.join(staging_root, "movie.mkv")
            os.makedirs(staging_root)
            os.makedirs(final_root)
            with open(source_file, "w", encoding="utf-8") as source_handle:
                source_handle.write("complete")
            with open(os.path.join(staging_root, "unrelated.mkv.lftp"), "w", encoding="utf-8") as temp_handle:
                temp_handle.write("partial")

            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with(source_file, os.path.join(final_root, "movie.mkv"), ANY)
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_same_path_wins_over_lftp_temp_deferral(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_tree = os.path.join(temp_dir, "movie.mkv")
            os.makedirs(source_tree)
            with open(os.path.join(source_tree, "movie.mkv"), "w", encoding="utf-8") as final_file:
                final_file.write("complete")
            with open(os.path.join(source_tree, "movie.mkv.lftp"), "w", encoding="utf-8") as temp_file:
                temp_file.write("partial")

            self.controller._Controller__staging_path = temp_dir
            self.controller._Controller__legacy_local_path = temp_dir

            result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.NO_MOVE_APPLICABLE, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_defers_directory_with_nested_lftp_payload(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "movie.mkv")
            os.makedirs(source_tree)
            os.makedirs(final_root)
            with open(os.path.join(source_tree, "notes.lftp"), "w", encoding="utf-8") as child_file:
                child_file.write("complete payload")

            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            self.controller._Controller__model_builder.is_remote_leaf_path.side_effect = \
                lambda _file_id, relative_path: relative_path == "notes"

            result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    @unittest.skipUnless(os.name == "posix" and os.path.isdir("/proc/self/fd"),
                         "child finalization requires descriptor-anchored POSIX directories")
    def test_directory_move_publishes_verified_complete_child_while_sibling_remains_incomplete(self):
        """A directory transfer must not make one completed child wait for another.

        The fixture uses the ordinary LFTP sidecar shape: ``complete.bin`` is
        a completed remote leaf and ``incomplete.bin.lftp`` is an unresolved
        sibling.  The incomplete artifact must remain in staging, while the
        completed child becomes visible at its final relative path.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            completed_source = os.path.join(source_tree, "complete.bin")
            incomplete_source = os.path.join(source_tree, "incomplete.bin.lftp")
            completed_destination = os.path.join(final_root, "release", "complete.bin")
            os.makedirs(source_tree)
            os.makedirs(final_root)
            Path(completed_source).write_bytes(b"complete")
            Path(incomplete_source).write_bytes(b"partial")

            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            self.controller._Controller__model_builder.is_remote_leaf_path.side_effect = \
                lambda _file_id, relative_path: relative_path in {"complete.bin", "incomplete.bin"}

            result = self.controller._finalize_staging_child("release", "complete.bin")

            self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
            self.assertEqual(b"complete", Path(completed_destination).read_bytes())
            self.assertTrue(os.path.exists(incomplete_source))
            self.assertFalse(os.path.exists(os.path.join(final_root, "release", "incomplete.bin")))
            self.assertNotIn("release", self.controller._Controller__persist.final_move_succeeded_file_names)
            self.assertNotIn("release", self.controller._Controller__persist.downloaded_file_names)
            child_id = ModelFile.build_file_id("release/complete.bin", None)
            self.assertIn(child_id, self.controller._Controller__persist.downloaded_file_names)
            self.assertIn(child_id, self.controller._Controller__persist.downloaded_timestamps)
            self.assertIn(child_id, self.controller._Controller__persist.final_move_succeeded_file_names)
            self.controller._Controller__model_builder.set_downloaded_files.assert_called_with(
                self.controller._Controller__persist.downloaded_file_names,
            )

    def test_directory_child_finalization_rechecks_parent_stop_before_move(self):
        self.controller._Controller__is_explicitly_stopped = MagicMock(return_value=True)
        self.controller._Controller__move_from_staging = MagicMock()

        result = self.controller._finalize_staging_child("release", "complete.bin")

        self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
        self.controller._Controller__move_from_staging.assert_not_called()

    def test_directory_child_finalization_honors_exact_child_stop_before_move(self):
        self.controller._Controller__is_explicitly_stopped = MagicMock(
            side_effect=lambda name, _pair: name == "release/complete.bin",
        )
        self.controller._Controller__move_from_staging = MagicMock()

        result = self.controller._finalize_staging_child("release", "complete.bin")

        self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
        self.controller._Controller__move_from_staging.assert_not_called()

    @unittest.skipUnless(os.name == "posix" and os.path.isdir("/proc/self/fd"),
                         "child finalization requires descriptor-anchored POSIX directories")
    def test_directory_child_finalization_retries_without_root_markers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            os.makedirs(os.path.join(staging_root, "release"))
            os.mkdir(final_root)
            Path(os.path.join(staging_root, "release", "complete.bin")).write_bytes(b"staged")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            self.controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
            self.controller._Controller__move_from_staging = MagicMock(
                return_value=Controller.MoveFromStagingResult.FAILED,
            )
            child_id = ModelFile.build_file_id("release/complete.bin", None)

            self.assertEqual(
                Controller.MoveFromStagingResult.FAILED,
                self.controller._finalize_staging_child("release", "complete.bin"),
            )
            self.assertEqual(1, self.controller._Controller__child_final_move_failure_counts[child_id])
            self.assertEqual(
                Controller.MoveFromStagingResult.DEFERRED,
                self.controller._finalize_staging_child("release", "complete.bin"),
            )
            self.controller._Controller__move_from_staging.assert_called_once()
            self.assertNotIn("release", self.controller._Controller__persist.final_move_succeeded_file_names)

    @unittest.skipUnless(os.name == "posix" and os.path.isdir("/proc/self/fd"),
                         "child finalization requires descriptor-anchored POSIX directories")
    def test_directory_child_finalization_refuses_existing_destination_without_root_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            os.makedirs(os.path.join(staging_root, "release"))
            os.makedirs(os.path.join(final_root, "release"))
            Path(os.path.join(staging_root, "release", "complete.bin")).write_bytes(b"staged")
            Path(os.path.join(final_root, "release", "complete.bin")).write_bytes(b"existing")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._finalize_staging_child("release", "complete.bin")

            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, result)
            self.assertEqual(b"existing", Path(os.path.join(final_root, "release", "complete.bin")).read_bytes())
            self.assertTrue(os.path.exists(os.path.join(staging_root, "release", "complete.bin")))
            self.assertNotIn("release", self.controller._Controller__persist.final_move_succeeded_file_names)

    @unittest.skipUnless(os.name == "posix" and os.path.isdir("/proc/self/fd"),
                         "descriptor race coverage requires POSIX procfs")
    def test_directory_child_finalization_held_parent_does_not_follow_substituted_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            outside_root = os.path.join(temp_dir, "outside")
            os.makedirs(os.path.join(staging_root, "release"))
            os.makedirs(os.path.join(final_root, "release"))
            os.mkdir(outside_root)
            Path(os.path.join(staging_root, "release", "complete.bin")).write_bytes(b"staged")
            source_fd = self.controller._Controller__open_contained_finalization_parent(
                staging_root, ["release"], False,
            )
            destination_fd = self.controller._Controller__open_contained_finalization_parent(
                final_root, ["release"], True,
            )
            self.assertIsNotNone(source_fd)
            self.assertIsNotNone(destination_fd)
            held_destination = os.path.join(final_root, "held-release")
            os.rename(os.path.join(final_root, "release"), held_destination)
            os.symlink(outside_root, os.path.join(final_root, "release"))
            try:
                result = self.controller._Controller__move_from_staging(
                    "release/complete.bin",
                    anchored_paths=(
                        staging_root,
                        final_root,
                        os.path.join("/proc/self/fd", str(source_fd), "complete.bin"),
                        os.path.join("/proc/self/fd", str(destination_fd), "complete.bin"),
                    ),
                )
            finally:
                os.close(source_fd)
                os.close(destination_fd)

            self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
            self.assertFalse(os.path.exists(os.path.join(outside_root, "complete.bin")))
            self.assertEqual(b"staged", Path(os.path.join(held_destination, "complete.bin")).read_bytes())

    def test_child_finalization_parent_fails_closed_without_no_follow_capability(self):
        with patch("controller.controller.os.name", "posix"), \
                patch("controller.controller.os.path.isdir", return_value=True), \
                patch("controller.controller.os.O_NOFOLLOW", None, create=True), \
                patch("controller.controller.os.open") as open_file:
            descriptor = self.controller._Controller__open_contained_finalization_parent(
                "/generic-root", ["child"], True,
            )

        self.assertIsNone(descriptor)
        open_file.assert_not_called()

    def test_child_finalization_parent_closes_held_descriptor_after_traversal_failure(self):
        with patch("controller.controller.os.name", "posix"), \
                patch("controller.controller.os.path.isdir", return_value=True), \
                patch("controller.controller.os.O_DIRECTORY", 0x10000, create=True), \
                patch("controller.controller.os.O_NOFOLLOW", 0x20000, create=True), \
                patch("controller.controller.os.open", side_effect=[41, OSError("traversal failed")]) as open_file, \
                patch("controller.controller.os.mkdir") as make_directory, \
                patch("controller.controller.os.close") as close_file, \
                patch("controller.controller.os.supports_dir_fd") as supports_dir_fd:
            supports_dir_fd.__contains__.return_value = True
            descriptor = self.controller._Controller__open_contained_finalization_parent(
                "/generic-root", ["child"], True,
            )

        self.assertIsNone(descriptor)
        close_file.assert_called_once_with(41)

    def test_child_finalization_terminal_failure_count_prevents_reservation(self):
        child_id = ModelFile.build_file_id("release/complete.bin", None)
        self.controller._Controller__child_final_move_failure_counts = {child_id: 4}
        self.controller._reserve_move_attempt = MagicMock()

        result = self.controller._finalize_staging_child("release", "complete.bin")

        self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
        self.controller._reserve_move_attempt.assert_not_called()

    def test_child_finalization_containment_failure_is_accounted(self):
        self.controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        self.controller._Controller__resolve_safe_final_move_paths = MagicMock(return_value=None)
        child_id = ModelFile.build_file_id("release/complete.bin", None)

        result = self.controller._finalize_staging_child("release", "complete.bin")

        self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
        self.assertEqual(1, self.controller._Controller__child_final_move_failure_counts[child_id])

    def test_child_finalization_new_root_lifecycle_clears_descendant_retry_state(self):
        child_id = ModelFile.build_file_id("release/complete.bin", None)
        self.controller._Controller__child_final_move_failure_counts = {child_id: 2}
        self.controller._Controller__child_final_move_retry_due = {child_id: datetime.now()}

        self.controller._Controller__advance_transfer_lifecycle("release")

        self.assertEqual({}, self.controller._Controller__child_final_move_failure_counts)
        self.assertEqual({}, self.controller._Controller__child_final_move_retry_due)

    def test_child_finalization_prunes_state_when_candidate_disappears(self):
        child_id = ModelFile.build_file_id("release/complete.bin", None)
        self.controller._Controller__child_final_move_failure_counts = {child_id: 1}
        self.controller._Controller__child_final_move_retry_due = {child_id: datetime.now()}

        self.controller._prune_child_finalization_retry_state(set())

        self.assertEqual({}, self.controller._Controller__child_final_move_failure_counts)
        self.assertEqual({}, self.controller._Controller__child_final_move_retry_due)

    @unittest.skipUnless(os.name == "posix" and os.path.isdir("/proc/self/fd"),
                         "descriptor-anchored child source check requires POSIX procfs")
    def test_child_finalization_rejects_directory_source_leaf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            os.makedirs(os.path.join(staging_root, "release", "complete.bin"))
            os.mkdir(final_root)
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._finalize_staging_child("release", "complete.bin")

            self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)

    @unittest.skipUnless(os.name == "posix" and os.path.isdir("/proc/self/fd"),
                         "descriptor-anchored child source check requires POSIX procfs")
    def test_child_finalization_accounts_runtime_move_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            os.makedirs(os.path.join(staging_root, "release"))
            os.mkdir(final_root)
            Path(os.path.join(staging_root, "release", "complete.bin")).write_bytes(b"staged")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            self.controller._Controller__move_from_staging = MagicMock(side_effect=RuntimeError("generic failure"))

            result = self.controller._finalize_staging_child("release", "complete.bin")

            self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
            self.assertEqual(1, self.controller._Controller__child_final_move_failure_counts[
                ModelFile.build_file_id("release/complete.bin", None)
            ])

    @unittest.skipUnless(os.name == "posix" and os.path.isdir("/proc/self/fd"),
                         "descriptor-anchored child source check requires POSIX procfs")
    def test_child_finalization_missing_source_with_regular_destination_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            os.makedirs(os.path.join(staging_root, "release"))
            os.makedirs(os.path.join(final_root, "release"))
            Path(os.path.join(final_root, "release", "complete.bin")).write_bytes(b"published")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._finalize_staging_child("release", "complete.bin")

            self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED, result)

    def test_child_finalization_lifecycle_reset_removes_orphan_due_entry(self):
        child_id = ModelFile.build_file_id("release/complete.bin", None)
        self.controller._Controller__child_final_move_failure_counts = {}
        self.controller._Controller__child_final_move_retry_due = {child_id: datetime.now()}

        self.controller._Controller__advance_transfer_lifecycle("release")

        self.assertEqual({}, self.controller._Controller__child_final_move_retry_due)

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_allows_directory_with_remote_lftp_payload_name(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "movie")
            os.makedirs(source_tree)
            os.makedirs(final_root)
            with open(os.path.join(source_tree, "notes.lftp"), "w", encoding="utf-8") as child_file:
                child_file.write("legitimate payload")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            self.controller._Controller__model_builder.is_remote_leaf_path.side_effect = \
                lambda _file_id, relative_path: relative_path == "notes.lftp"

            result = self.controller._Controller__move_from_staging("movie")

        move.assert_called_once_with(source_tree, os.path.join(final_root, "movie"), ANY)
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_defers_directory_with_nested_lftp_status_sidecar(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "movie")
            os.makedirs(source_tree)
            os.makedirs(final_root)
            with open(os.path.join(source_tree, "foo.lftp-pget-status"), "w", encoding="utf-8") as child_file:
                child_file.write("status sidecar")

            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("movie")

        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    def test_move_from_staging_merges_missing_descendants_and_retains_collision_residue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            destination_tree = os.path.join(final_root, "release")
            os.makedirs(os.path.join(source_tree, "nested"))
            os.makedirs(destination_tree)
            Path(os.path.join(destination_tree, "E06.mkv")).write_bytes(b"final")
            Path(os.path.join(source_tree, "E06.mkv")).write_bytes(b"stale")
            os.utime(os.path.join(destination_tree, "E06.mkv"), ns=(1_786_400_000_000_000_000,) * 2)
            os.utime(os.path.join(source_tree, "E06.mkv"), ns=(1_786_400_001_000_000_000,) * 2)
            Path(os.path.join(source_tree, "nested", "E07 [special].mkv")).write_bytes(b"staged")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("release")
            repeated_result = self.controller._Controller__move_from_staging("release")

            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, result)
            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, repeated_result)
            self.assertEqual(b"final", Path(os.path.join(destination_tree, "E06.mkv")).read_bytes())
            self.assertEqual(
                b"staged", Path(os.path.join(destination_tree, "nested", "E07 [special].mkv")).read_bytes()
            )
            self.assertEqual(b"stale", Path(os.path.join(source_tree, "E06.mkv")).read_bytes())

    def test_move_from_staging_reconciles_verified_equivalent_nested_collision_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release", "nested")
            destination_tree = os.path.join(final_root, "release", "nested")
            os.makedirs(source_tree)
            os.makedirs(destination_tree)
            source_leaf = os.path.join(source_tree, "E06.mkv")
            destination_leaf = os.path.join(destination_tree, "E06.mkv")
            Path(source_leaf).write_bytes(b"equivalent")
            Path(destination_leaf).write_bytes(b"equivalent")
            timestamp = 1_786_400_000_000_000_000
            os.utime(source_leaf, ns=(timestamp, timestamp))
            os.utime(destination_leaf, ns=(timestamp, timestamp))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("release")

            self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
            self._settle_collision_compare()
            result = self.controller._Controller__move_from_staging("release")

            self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
            self.assertFalse(os.path.exists(os.path.join(staging_root, "release")))
            self.assertEqual(b"equivalent", Path(destination_leaf).read_bytes())

    def test_move_from_staging_retains_same_size_same_mtime_different_content_collision_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            destination_tree = os.path.join(final_root, "release")
            os.makedirs(source_tree)
            os.makedirs(destination_tree)
            source_leaf = os.path.join(source_tree, "movie.mkv")
            destination_leaf = os.path.join(destination_tree, "movie.mkv")
            Path(source_leaf).write_bytes(b"source")
            Path(destination_leaf).write_bytes(b"target")
            timestamp = 1_786_400_000_000_000_000
            os.utime(source_leaf, ns=(timestamp, timestamp))
            os.utime(destination_leaf, ns=(timestamp, timestamp))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("release")

            self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
            self._settle_collision_compare()
            result = self.controller._Controller__move_from_staging("release")

            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, result)
            self.assertEqual(b"target", Path(destination_leaf).read_bytes())
            self.assertEqual(b"source", Path(source_leaf).read_bytes())

    def test_collision_compare_uses_one_worker_caches_mismatch_and_reschedules_changed_leaf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            destination_tree = os.path.join(final_root, "release")
            os.makedirs(source_tree)
            os.makedirs(destination_tree)
            source_leaf = os.path.join(source_tree, "movie.mkv")
            destination_leaf = os.path.join(destination_tree, "movie.mkv")
            Path(source_leaf).write_bytes(b"source")
            Path(destination_leaf).write_bytes(b"target")
            timestamp = 1_786_400_000_000_000_000
            os.utime(source_leaf, ns=(timestamp, timestamp))
            os.utime(destination_leaf, ns=(timestamp, timestamp))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            compare_started = threading.Event()
            release_compare = threading.Event()
            calls = 0

            def compare_in_worker(*_args):
                nonlocal calls
                calls += 1
                compare_started.set()
                self.assertTrue(release_compare.wait(2))
                return False

            with patch.object(
                    Controller,
                    "_Controller__claimed_regular_file_matches_destination",
                    side_effect=compare_in_worker,
            ):
                self.assertEqual(
                    Controller.MoveFromStagingResult.DEFERRED,
                    self.controller._Controller__move_from_staging("release"),
                )
                self.assertTrue(compare_started.wait(1))
                self.assertEqual(
                    Controller.MoveFromStagingResult.DEFERRED,
                    self.controller._Controller__move_from_staging("release"),
                )
                self.assertEqual(1, calls)
                release_compare.set()
                self._settle_collision_compare()
                self.assertEqual(
                    Controller.MoveFromStagingResult.CONFLICT,
                    self.controller._Controller__move_from_staging("release"),
                )
                self.assertEqual(
                    Controller.MoveFromStagingResult.CONFLICT,
                    self.controller._Controller__move_from_staging("release"),
                )
                self.assertEqual(1, calls)

                source_signature = Controller._Controller__collision_signature(source_leaf)
                changed_ctime_signature = source_signature[:4] + (source_signature[4] + 1,) + source_signature[5:]
                destination_signature = Controller._Controller__collision_signature(destination_leaf)
                self.assertNotEqual(
                    self.controller._Controller__collision_cache_key(
                        source_leaf, destination_leaf, source_signature, destination_signature,
                    ),
                    self.controller._Controller__collision_cache_key(
                        source_leaf, destination_leaf, changed_ctime_signature, destination_signature,
                    ),
                )

                replacement_leaf = os.path.join(source_tree, "movie-replacement.mkv")
                Path(replacement_leaf).write_bytes(b"change")
                os.utime(replacement_leaf, ns=(timestamp, timestamp))
                os.replace(replacement_leaf, source_leaf)
                self.assertEqual(
                    Controller.MoveFromStagingResult.DEFERRED,
                    self.controller._Controller__move_from_staging("release"),
                )
                self._settle_collision_compare()
                self.assertEqual(2, calls)
                self.assertEqual(
                    Controller.MoveFromStagingResult.CONFLICT,
                    self.controller._Controller__move_from_staging("release"),
                )

                replacement_leaf = os.path.join(destination_tree, "movie-replacement.mkv")
                Path(replacement_leaf).write_bytes(b"target")
                os.utime(replacement_leaf, ns=(timestamp, timestamp))
                os.replace(replacement_leaf, destination_leaf)
                self.assertEqual(
                    Controller.MoveFromStagingResult.DEFERRED,
                    self.controller._Controller__move_from_staging("release"),
                )
                self._settle_collision_compare()
                self.assertEqual(3, calls)
            self.controller._Controller__shutdown_collision_compare_worker()

    def test_collision_compare_rejects_over_budget_before_opening_a_leaf(self):
        source_signature = (1, 2, 16 * 1024 * 1024 * 1024 + 1, 4, 5, 6)
        destination_signature = (7, 8, source_signature[2], 10, 11, 12)

        with patch.object(Controller, "_Controller__claimed_regular_file_matches_destination") as compare:
            outcome, _, _ = Controller._Controller__compare_collision_leaf_job(
                "missing-source",
                "missing-destination",
                source_signature,
                destination_signature,
                threading.Event(),
            )

        self.assertEqual("over_budget", outcome)
        compare.assert_not_called()

    def test_collision_compare_caches_stable_error_without_relaunching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source = os.path.join(staging_root, "release", "movie.mkv")
            destination = os.path.join(final_root, "release", "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.makedirs(os.path.dirname(destination))
            Path(source).write_bytes(b"equivalent")
            Path(destination).write_bytes(b"equivalent")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            with patch.object(
                    Controller,
                    "_Controller__claimed_regular_file_matches_destination",
                    side_effect=OSError(errno.EIO, "read failed"),
            ) as compare:
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("release"))
                self._settle_collision_compare()
                self.assertEqual(Controller.MoveFromStagingResult.CONFLICT,
                                 self.controller._Controller__move_from_staging("release"))
                self.assertEqual(Controller.MoveFromStagingResult.CONFLICT,
                                 self.controller._Controller__move_from_staging("release"))

            compare.assert_called_once()
            self.controller._Controller__shutdown_collision_compare_worker()

    def test_collision_compare_exit_cancels_active_worker_without_retaining_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source = os.path.join(staging_root, "release", "movie.mkv")
            destination = os.path.join(final_root, "release", "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.makedirs(os.path.dirname(destination))
            Path(source).write_bytes(b"equivalent")
            Path(destination).write_bytes(b"equivalent")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            compare_started = threading.Event()
            compare_cancelled = threading.Event()

            def wait_for_cancellation(*args):
                cancel_event = args[-2]
                compare_started.set()
                self.assertTrue(cancel_event.wait(2))
                compare_cancelled.set()
                raise OSError(errno.ECANCELED, "cancelled")

            with patch.object(
                    Controller,
                    "_Controller__claimed_regular_file_matches_destination",
                    side_effect=wait_for_cancellation,
            ):
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("release"))
                self.assertTrue(compare_started.wait(1))
                future = self.controller._Controller__collision_compare_future
                claim_restored = threading.Event()
                future.add_done_callback(lambda _completed_future: claim_restored.set())
                exit_started = time.monotonic()
                self.controller.exit()
                self.assertLess(time.monotonic() - exit_started, 1)
                self.assertTrue(compare_cancelled.wait(1))
                future.result(timeout=1)
                self.assertTrue(claim_restored.wait(1))

            self.assertIsNone(self.controller._Controller__collision_compare_executor)
            self.assertIsNone(self.controller._Controller__collision_compare_future)
            self.assertIsNone(self.controller._Controller__collision_compare_key)
            self.assertIsNone(self.controller._Controller__collision_compare_result)
            self.assertIsNone(self.controller._Controller__collision_compare_claim)
            self.assertTrue(os.path.exists(source))
            self.assertEqual([], [entry for entry in os.listdir(os.path.dirname(source))
                                  if entry.startswith(".seedsync-retire-")])

    def test_collision_compare_cancellation_closes_both_descriptors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source")
            destination = os.path.join(temp_dir, "destination")
            Path(source).write_bytes(b"x" * (128 * 1024 + 1))
            Path(destination).write_bytes(b"x" * (128 * 1024 + 1))
            cancel_event = threading.Event()
            original_read = Controller._Controller__read_descriptor_chunk
            original_close = os.close
            closed_descriptors = []
            reads = 0

            def read_then_cancel(file_descriptor, size):
                nonlocal reads
                result = original_read(file_descriptor, size)
                reads += 1
                if reads == 1:
                    cancel_event.set()
                return result

            def record_close(file_descriptor):
                closed_descriptors.append(file_descriptor)
                original_close(file_descriptor)

            with patch.object(
                    Controller,
                    "_Controller__read_descriptor_chunk",
                    side_effect=read_then_cancel,
            ), patch("controller.controller.os.close", side_effect=record_close):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__claimed_regular_file_matches_destination(
                        source,
                        os.lstat(source),
                        destination,
                        os.lstat(destination),
                        cancel_event,
                    )

            self.assertEqual(errno.ECANCELED, error.exception.errno)
            self.assertEqual(2, len(closed_descriptors))
            self.assertEqual(2, len(set(closed_descriptors)))

    def test_move_from_staging_publishes_unrelated_leaf_while_equivalent_collision_is_deferred(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            destination_tree = os.path.join(final_root, "release")
            os.makedirs(source_tree)
            os.makedirs(destination_tree)
            source_leaf = os.path.join(source_tree, "E06.mkv")
            destination_leaf = os.path.join(destination_tree, "E06.mkv")
            Path(source_leaf).write_bytes(b"equivalent")
            Path(destination_leaf).write_bytes(b"equivalent")
            Path(os.path.join(source_tree, "E07.mkv")).write_bytes(b"unrelated")
            timestamp = 1_786_400_000_000_000_000
            os.utime(source_leaf, ns=(timestamp, timestamp))
            os.utime(destination_leaf, ns=(timestamp, timestamp))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            self.assertEqual(
                Controller.MoveFromStagingResult.DEFERRED,
                self.controller._Controller__move_from_staging("release"),
            )
            self.assertEqual(b"unrelated", Path(os.path.join(destination_tree, "E07.mkv")).read_bytes())
            self.assertFalse(os.path.exists(source_leaf))
            private_artifacts = [entry for entry in os.listdir(source_tree)
                                 if entry.startswith(".seedsync-retire-")]
            self.assertEqual(2, len(private_artifacts))
            self.assertEqual(1, len([entry for entry in private_artifacts if entry.endswith(".json")]))
            self._settle_collision_compare()
            self.assertEqual(
                Controller.MoveFromStagingResult.COMPLETED,
                self.controller._Controller__move_from_staging("release"),
            )

    def test_collision_claim_compares_source_mutated_between_metadata_precheck_and_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release", "nested")
            destination_tree = os.path.join(final_root, "release", "nested")
            os.makedirs(source_tree)
            os.makedirs(destination_tree)
            source_leaf = os.path.join(source_tree, "E06.mkv")
            destination_leaf = os.path.join(destination_tree, "E06.mkv")
            replacement_leaf = os.path.join(source_tree, "replacement.mkv")
            Path(source_leaf).write_bytes(b"equivalent")
            Path(destination_leaf).write_bytes(b"equivalent")
            Path(replacement_leaf).write_bytes(b"replacement")
            timestamp = 1_786_400_000_000_000_000
            os.utime(source_leaf, ns=(timestamp, timestamp))
            os.utime(destination_leaf, ns=(timestamp, timestamp))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            original_claim = Controller._Controller__claim_collision_source
            claim_raced = False

            def replace_then_claim(path, mutation_tracker=None):
                nonlocal claim_raced
                if path == source_leaf and not claim_raced:
                    claim_raced = True
                    os.replace(replacement_leaf, source_leaf)
                return original_claim(self.controller, path, mutation_tracker)

            with patch.object(
                    Controller,
                    "_Controller__claim_collision_source",
                    side_effect=replace_then_claim,
            ):
                result = self.controller._Controller__move_from_staging("release")
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
                self._settle_collision_compare()
                result = self.controller._Controller__move_from_staging("release")

            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, result)
            self.assertTrue(claim_raced)
            self.assertEqual(b"equivalent", Path(destination_leaf).read_bytes())
            self.assertEqual(b"replacement", Path(source_leaf).read_bytes())
            claims = [
                entry for entry in os.listdir(source_tree)
                if entry.startswith(".seedsync-retire-")
            ]
            self.assertEqual([], claims)

    def test_collision_claim_restores_when_destination_changes_after_descriptor_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release", "nested")
            destination_tree = os.path.join(final_root, "release", "nested")
            os.makedirs(source_tree)
            os.makedirs(destination_tree)
            source_leaf = os.path.join(source_tree, "E06.mkv")
            destination_leaf = os.path.join(destination_tree, "E06.mkv")
            replacement_leaf = os.path.join(destination_tree, "replacement.mkv")
            Path(source_leaf).write_bytes(b"equivalent")
            Path(destination_leaf).write_bytes(b"equivalent")
            Path(replacement_leaf).write_bytes(b"changed-final")
            timestamp = 1_786_400_000_000_000_000
            os.utime(source_leaf, ns=(timestamp, timestamp))
            os.utime(destination_leaf, ns=(timestamp, timestamp))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            original_compare = Controller._Controller__claimed_regular_file_matches_destination
            destination_replaced = False

            def compare_then_replace(*args, **kwargs):
                nonlocal destination_replaced
                result = original_compare(*args, **kwargs)
                if not destination_replaced:
                    destination_replaced = True
                    os.replace(replacement_leaf, destination_leaf)
                return result

            with patch.object(
                    Controller,
                    "_Controller__claimed_regular_file_matches_destination",
                    side_effect=compare_then_replace,
            ):
                result = self.controller._Controller__move_from_staging("release")
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
                self._settle_collision_compare()
                result = self.controller._Controller__move_from_staging("release")

            self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
            self.assertTrue(destination_replaced)
            self.assertEqual(b"changed-final", Path(destination_leaf).read_bytes())
            self.assertEqual(b"equivalent", Path(source_leaf).read_bytes())
            claims = [
                entry for entry in os.listdir(source_tree)
                if entry.startswith(".seedsync-retire-")
            ]
            self.assertEqual([], claims)

    def test_collision_claim_restores_when_held_claim_inode_changes_after_comparison(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            destination_tree = os.path.join(final_root, "release")
            os.makedirs(source_tree); os.makedirs(destination_tree)
            source_leaf = os.path.join(source_tree, "movie.mkv")
            destination_leaf = os.path.join(destination_tree, "movie.mkv")
            Path(source_leaf).write_bytes(b"equal-data")
            Path(destination_leaf).write_bytes(b"equal-data")
            timestamp = 1_786_400_000_000_000_000
            os.utime(source_leaf, ns=(timestamp, timestamp))
            os.utime(destination_leaf, ns=(timestamp, timestamp))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            original_compare = Controller._Controller__claimed_regular_file_matches_destination

            def compare_then_mutate_claim(*args, **kwargs):
                result = original_compare(*args, **kwargs)
                claimed_source = args[0]
                file_descriptor = os.open(claimed_source, os.O_WRONLY | getattr(os, "O_BINARY", 0))
                try:
                    os.write(file_descriptor, b"changedata")
                finally:
                    os.close(file_descriptor)
                return result

            with patch.object(
                    Controller,
                    "_Controller__claimed_regular_file_matches_destination",
                    side_effect=compare_then_mutate_claim,
            ):
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("release"))
                self._settle_collision_compare()
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("release"))

            self.assertEqual(b"changedata", Path(source_leaf).read_bytes())
            self.assertEqual(b"equal-data", Path(destination_leaf).read_bytes())
            self.assertEqual([], [entry for entry in os.listdir(source_tree)
                                  if entry.startswith(".seedsync-retire-")])

    def test_collision_compare_reads_windows_text_sensitive_bytes_in_binary_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source")
            destination = os.path.join(temp_dir, "destination")
            Path(source).write_bytes(b"a\r\nb\x1a")
            Path(destination).write_bytes(b"a\nb\x1a!")

            self.assertFalse(Controller._Controller__claimed_regular_file_matches_destination(
                source, os.lstat(source), destination, os.lstat(destination), threading.Event(),
            ))

    def test_fresh_merge_recovers_owned_crash_claim_before_comparing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            destination_tree = os.path.join(final_root, "release")
            os.makedirs(source_tree); os.makedirs(destination_tree)
            original = os.path.join(source_tree, "movie.mkv")
            claim = os.path.join(source_tree, ".seedsync-retire-" + "a" * 48)
            destination = os.path.join(destination_tree, "movie.mkv")
            Path(claim).write_bytes(b"source")
            Path(destination).write_bytes(b"target")
            timestamp = 1_786_400_000_000_000_000
            os.utime(claim, ns=(timestamp, timestamp)); os.utime(destination, ns=(timestamp, timestamp))
            Controller._Controller__write_collision_claim_sidecar(claim + ".json", "movie.mkv", None)
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                             self.controller._Controller__move_from_staging("release"))
            self._settle_collision_compare()
            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT,
                             self.controller._Controller__move_from_staging("release"))
            self.assertEqual(b"source", Path(original).read_bytes())
            self.assertFalse(os.path.exists(claim))
            self.assertFalse(os.path.exists(claim + ".json"))

    def test_recovery_retains_invalid_or_foreign_private_claim_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scenarios = (
                ("occupied", {"version": 1, "original_basename": "movie.mkv", "path_pair_id": "movies"}, True, "movies"),
                ("foreign", {"version": 1, "original_basename": "movie.mkv", "path_pair_id": "tv"}, False, "movies"),
                ("malformed", b"not-json", False, "movies"),
                ("oversize", b"x" * 4097, False, "movies"),
                ("traversal", {"version": 1, "original_basename": "../movie.mkv", "path_pair_id": "movies"}, False, "movies"),
                ("missing-sidecar", None, False, "movies"),
            )
            for label, payload, occupy_original, owner in scenarios:
                with self.subTest(label=label):
                    directory = os.path.join(temp_dir, label)
                    os.makedirs(directory)
                    claim = os.path.join(directory, ".seedsync-retire-" + "b" * 47 + str(len(label) % 10))
                    Path(claim).write_bytes(b"private")
                    sidecar = claim + ".json"
                    if isinstance(payload, dict):
                        Path(sidecar).write_text(json.dumps(payload), encoding="utf-8")
                    elif isinstance(payload, bytes):
                        Path(sidecar).write_bytes(payload)
                    if occupy_original:
                        Path(os.path.join(directory, "movie.mkv")).write_bytes(b"newer")
                    retained, overflow = self.controller._Controller__recover_collision_claims(directory, owner)
                    self.assertFalse(overflow)
                    self.assertIn(claim, retained)
                    self.assertTrue(os.path.exists(claim))
                    if payload is not None:
                        self.assertIn(sidecar, retained)
                        self.assertTrue(os.path.exists(sidecar))

    def test_recovery_retains_unpaired_exact_token_sidecar_without_publishing_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sidecar = os.path.join(temp_dir, ".seedsync-retire-" + "c" * 48 + ".json")
            Controller._Controller__write_collision_claim_sidecar(sidecar, "movie.mkv", "movies")

            retained, overflow = self.controller._Controller__recover_collision_claims(temp_dir, "movies")
            self.assertFalse(overflow)
            self.assertEqual({sidecar}, retained)
            self.assertTrue(os.path.exists(sidecar))

    def test_collision_claim_sidecar_read_rejects_descriptor_identity_race(self):
        payload = b'{"original_basename":"movie.mkv","path_pair_id":null,"version":1}'
        before = SimpleNamespace(st_mode=stat.S_IFREG, st_size=len(payload), st_dev=1, st_ino=11)
        replaced = SimpleNamespace(st_mode=stat.S_IFREG, st_size=len(payload), st_dev=1, st_ino=12)
        with patch("controller.controller.os.lstat", return_value=before), \
                patch("controller.controller.os.open", return_value=17), \
                patch("controller.controller.os.fstat", return_value=replaced), \
                patch("controller.controller.os.read") as read_mock, \
                patch("controller.controller.os.close") as close_mock:
            self.assertIsNone(Controller._Controller__read_collision_claim_sidecar("claim.json"))

        read_mock.assert_not_called()
        close_mock.assert_called_once_with(17)

    def test_collision_claim_sidecar_read_allows_identity_fallback_when_inode_unavailable(self):
        payload = b'{"original_basename":"movie.mkv","path_pair_id":null,"version":1}'
        unsupported_identity = SimpleNamespace(
            st_mode=stat.S_IFREG, st_size=len(payload), st_dev=0, st_ino=0,
        )
        with patch("controller.controller.os.lstat", return_value=unsupported_identity), \
                patch("controller.controller.os.open", return_value=17), \
                patch("controller.controller.os.fstat", return_value=unsupported_identity), \
                patch("controller.controller.os.read", side_effect=(payload, b"")), \
                patch("controller.controller.os.close"):
            self.assertEqual(
                ("movie.mkv", None),
                Controller._Controller__read_collision_claim_sidecar("claim.json"),
            )

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW requires POSIX")
    def test_collision_claim_sidecar_read_uses_no_follow_descriptor_flag(self):
        payload = b'{"original_basename":"movie.mkv","path_pair_id":null,"version":1}'
        identity = SimpleNamespace(st_mode=stat.S_IFREG, st_size=len(payload), st_dev=1, st_ino=11)
        with patch("controller.controller.os.lstat", return_value=identity), \
                patch("controller.controller.os.open", return_value=17) as open_mock, \
                patch("controller.controller.os.fstat", return_value=identity), \
                patch("controller.controller.os.read", side_effect=(payload, b"")), \
                patch("controller.controller.os.close"):
            self.assertEqual(
                ("movie.mkv", None),
                Controller._Controller__read_collision_claim_sidecar("claim.json"),
            )

        self.assertTrue(open_mock.call_args.args[1] & os.O_NOFOLLOW)

    @unittest.skipUnless(os.name == "posix", "symlink semantics require POSIX")
    def test_collision_claim_sidecar_read_rejects_symlink_without_following_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "target.json")
            sidecar = os.path.join(temp_dir, "claim.json")
            Path(target).write_text(
                '{"original_basename":"movie.mkv","path_pair_id":null,"version":1}', encoding="utf-8",
            )
            os.symlink(target, sidecar)

            self.assertIsNone(Controller._Controller__read_collision_claim_sidecar(sidecar))

    def test_collision_claim_recovery_stops_at_exact_artifact_cap_before_publication(self):
        class StreamingEntries:
            def __init__(self, entries):
                self.entries = iter(entries)
                self.next_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def __iter__(self):
                return self

            def __next__(self):
                self.next_calls += 1
                return next(self.entries)

        entries = [
            SimpleNamespace(
                name=".seedsync-retire-" + "a" * 46 + "{:02x}".format(index),
                path="claim-{}".format(index),
            )
            for index in range(129)
        ]
        entries.append(SimpleNamespace(name=".seedsync-retire-user.mkv", path="user-file"))
        stream = StreamingEntries(entries)
        self.controller._Controller__rename_no_replace = MagicMock()

        with patch("controller.controller.os.scandir", return_value=stream):
            retained, overflow = self.controller._Controller__recover_collision_claims("source", None)

        self.assertEqual(set(), retained)
        self.assertTrue(overflow)
        self.assertEqual(129, stream.next_calls)
        self.controller._Controller__rename_no_replace.assert_not_called()

    def test_recovery_overflow_retains_private_artifacts_beyond_scan_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            destination_tree = os.path.join(final_root, "release")
            os.makedirs(source_tree); os.makedirs(destination_tree)
            for index in range(129):
                token = "a" * 46 + "{:02x}".format(index)
                claim = os.path.join(source_tree, ".seedsync-retire-" + token)
                Path(claim).write_bytes(b"private")
                Controller._Controller__write_collision_claim_sidecar(claim + ".json", "movie-{}.mkv".format(index), None)
            Path(os.path.join(source_tree, "ordinary.mkv")).write_bytes(b"ordinary")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT,
                             self.controller._Controller__move_from_staging("release"))
            self.assertTrue(os.path.exists(os.path.join(source_tree, "ordinary.mkv")))
            self.assertFalse(os.path.exists(os.path.join(destination_tree, "ordinary.mkv")))
            self.assertEqual([], os.listdir(destination_tree))

    def test_same_generation_callback_restores_owned_claim_without_mutating_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = os.path.join(temp_dir, "movie.mkv")
            claimed = os.path.join(temp_dir, ".seedsync-retire-" + "d" * 48)
            destination = os.path.join(temp_dir, "final.mkv")
            Path(claimed).write_bytes(b"old-generation")
            Path(destination).write_bytes(b"final")
            Controller._Controller__write_collision_claim_sidecar(claimed + ".json", "movie.mkv", None)
            self.controller._Controller__collision_compare_epoch = 1
            future_sentinel = MagicMock()
            claim_sentinel = ("new", "claim", "destination", None)
            self.controller._Controller__collision_compare_future = future_sentinel
            self.controller._Controller__collision_compare_claim = claim_sentinel
            cancelled = threading.Event(); cancelled.set()
            completed = MagicMock(); completed.result.return_value = None

            self.controller._Controller__restore_cancelled_collision_claim(
                completed, original, claimed, destination, cancelled, 1, claimed + ".json",
            )

            self.assertEqual(b"old-generation", Path(original).read_bytes())
            self.assertFalse(os.path.exists(claimed))
            self.assertFalse(os.path.exists(claimed + ".json"))
            self.assertIs(future_sentinel, self.controller._Controller__collision_compare_future)
            self.assertIs(claim_sentinel, self.controller._Controller__collision_compare_claim)

    def test_stale_callback_retains_durable_pair_for_owner_aware_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = os.path.join(temp_dir, "movie.mkv")
            claimed = os.path.join(temp_dir, ".seedsync-retire-" + "e" * 48)
            destination = os.path.join(temp_dir, "final.mkv")
            Path(claimed).write_bytes(b"old-generation")
            Path(destination).write_bytes(b"final")
            Controller._Controller__write_collision_claim_sidecar(claimed + ".json", "movie.mkv", "movies")
            self.controller._Controller__collision_compare_epoch = 2
            future_sentinel = MagicMock()
            claim_sentinel = ("new", "claim", "destination", None)
            self.controller._Controller__collision_compare_future = future_sentinel
            self.controller._Controller__collision_compare_claim = claim_sentinel
            cancelled = threading.Event(); cancelled.set()
            completed = MagicMock(); completed.result.return_value = None

            self.controller._Controller__restore_cancelled_collision_claim(
                completed, original, claimed, destination, cancelled, 1, claimed + ".json",
            )

            self.assertFalse(os.path.exists(original))
            self.assertTrue(os.path.exists(claimed))
            self.assertTrue(os.path.exists(claimed + ".json"))
            self.assertIs(future_sentinel, self.controller._Controller__collision_compare_future)
            self.assertIs(claim_sentinel, self.controller._Controller__collision_compare_claim)
            retained, overflow = self.controller._Controller__recover_collision_claims(temp_dir, "movies")
            self.assertFalse(overflow)
            self.assertEqual(set(), retained)
            self.assertEqual(b"old-generation", Path(original).read_bytes())
            self.assertFalse(os.path.exists(claimed))
            self.assertFalse(os.path.exists(claimed + ".json"))

    def test_merge_publishes_non_active_user_file_with_collision_claim_prefix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source = os.path.join(staging_root, "release", ".seedsync-retire-user.mkv")
            destination = os.path.join(final_root, "release", ".seedsync-retire-user.mkv")
            os.makedirs(os.path.dirname(source)); os.makedirs(os.path.dirname(destination))
            Path(source).write_bytes(b"user-file")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            self.assertEqual(Controller.MoveFromStagingResult.COMPLETED,
                             self.controller._Controller__move_from_staging("release"))
            self.assertEqual(b"user-file", Path(destination).read_bytes())

    def test_completed_move_cleans_only_empty_legacy_retired_directory_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            destination = os.path.join(final_root, "Example Directory")
            claim = os.path.join(staging_root, ".seedsync-retire-" + "f" * 48)
            os.makedirs(os.path.join(claim, "S01", "Example Directory")); os.makedirs(destination)
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED,
                             self.controller._Controller__move_from_staging("Example Directory"))
            self.assertFalse(os.path.lexists(claim))

    def test_retirement_cleanup_breadcrumb_is_opaque_bounded_and_retrievable(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        with tempfile.TemporaryDirectory(prefix="password=hunter2-") as temp_dir:
            source_parent = os.path.join(temp_dir, "private-authentication=secret")
            claim = os.path.join(source_parent, ".seedsync-retire-" + "f" * 48)
            os.makedirs(claim)

            self.controller._Controller__cleanup_empty_retired_source_claims(source_parent)

            snapshot = trace.snapshot()
            self.assertEqual(1, snapshot["entry_count"])
            entry = snapshot["entries"][0]
            self.assertEqual("retirement_cleanup", entry["message"])
            self.assertEqual("retirement_cleanup", entry["stage"])
            self.assertEqual("retirement.cleanup", entry["category"])
            self.assertEqual("info", entry["level"])
            self.assertEqual(opaque_trace_correlation(claim), entry["corr_id"])
            self.assertEqual(
                entry["corr_id"], trace.snapshot(corr_id=entry["corr_id"])["entries"][0]["corr_id"],
            )
            self.assertEqual({
                "schema", "outcome", "reason", "directory_count", "candidate_count", "sidecar_present",
            }, set(entry["details"]))
            self.assertEqual("retirement_cleanup.v1", entry["details"]["schema"])
            self.assertEqual("removed", entry["details"]["outcome"])
            self.assertEqual("empty_tree", entry["details"]["reason"])
            self.assertEqual(1, entry["details"]["directory_count"])
            self.assertEqual(1, entry["details"]["candidate_count"])
            self.assertFalse(entry["details"]["sidecar_present"])
            self.assertNotIn(source_parent, str(snapshot))
            self.assertNotIn("hunter2", str(snapshot))
            self.assertNotIn("private-authentication", str(snapshot))

    def test_retirement_cleanup_breadcrumb_distinguishes_retained_and_skipped_claims(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        with tempfile.TemporaryDirectory() as temp_dir:
            retained = os.path.join(temp_dir, ".seedsync-retire-" + "1" * 48)
            sidecar = os.path.join(temp_dir, ".seedsync-retire-" + "2" * 48)
            removed = os.path.join(temp_dir, ".seedsync-retire-" + "f" * 48)
            os.makedirs(retained); Path(os.path.join(retained, "payload")).write_bytes(b"keep")
            os.makedirs(sidecar); Path(sidecar + ".json").write_bytes(b"reserved")
            os.makedirs(removed)

            self.controller._Controller__cleanup_empty_retired_source_claims(temp_dir)

            entries = {
                entry["details"]["reason"]: entry
                for entry in trace.snapshot()["entries"]
            }
            self.assertEqual(("retained", "nonempty"), (
                entries["nonempty"]["details"]["outcome"], entries["nonempty"]["details"]["reason"],
            ))
            self.assertEqual("warning", entries["nonempty"]["level"])
            self.assertEqual(("skipped", "sidecar"), (
                entries["sidecar"]["details"]["outcome"], entries["sidecar"]["details"]["reason"],
            ))
            self.assertTrue(entries["sidecar"]["details"]["sidecar_present"])
            self.assertEqual(("removed", "empty_tree"), (
                entries["empty_tree"]["details"]["outcome"], entries["empty_tree"]["details"]["reason"],
            ))
            self.assertTrue(os.path.isdir(retained))
            self.assertTrue(os.path.exists(sidecar + ".json"))
            self.assertFalse(os.path.lexists(removed))

    def test_completed_move_retains_nonempty_or_sidecar_retired_directory_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            destination = os.path.join(final_root, "Example Directory")
            nonempty_claim = os.path.join(staging_root, ".seedsync-retire-" + "1" * 48)
            sidecar_claim = os.path.join(staging_root, ".seedsync-retire-" + "2" * 48)
            os.makedirs(nonempty_claim); os.makedirs(sidecar_claim); os.makedirs(destination)
            Path(os.path.join(nonempty_claim, "payload")).write_bytes(b"do-not-delete")
            Controller._Controller__write_collision_claim_sidecar(sidecar_claim + ".json", "Example Directory", None)
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED,
                             self.controller._Controller__move_from_staging("Example Directory"))
            self.assertTrue(os.path.isdir(nonempty_claim))
            self.assertTrue(os.path.exists(os.path.join(nonempty_claim, "payload")))
            self.assertTrue(os.path.isdir(sidecar_claim))
            self.assertTrue(os.path.exists(sidecar_claim + ".json"))

    def test_completed_move_retains_retired_claim_with_linux_bind_mountpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            destination = os.path.join(final_root, "Example Directory")
            claim = os.path.join(staging_root, ".seedsync-retire-" + "3" * 48)
            nested = os.path.join(claim, "S01")
            os.makedirs(nested); os.makedirs(destination)
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            for mount_point in (claim, nested):
                with self.subTest(mount_point=mount_point), \
                        patch("controller.controller.sys.platform", "linux"), \
                        patch.object(Controller, "_Controller__linux_mountinfo_mountpoints", return_value=[mount_point]), \
                        patch("controller.controller.os.path.ismount", return_value=False):
                    self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED,
                                     self.controller._Controller__move_from_staging("Example Directory"))

            self.assertTrue(os.path.isdir(claim))
            self.assertTrue(os.path.isdir(nested))

    def test_completed_move_retains_retired_directory_tree_at_bound_without_recursion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            destination = os.path.join(final_root, "Example Directory")
            claim = os.path.join(staging_root, ".seedsync-retire-" + "4" * 48)
            nested = os.path.join(claim, "one", "two")
            os.makedirs(nested); os.makedirs(destination)
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            with patch("controller.controller._EMPTY_RETIRED_DIRECTORY_TREE_LIMIT", 2):
                self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED,
                                 self.controller._Controller__move_from_staging("Example Directory"))

            self.assertTrue(os.path.isdir(claim))
            self.assertTrue(os.path.isdir(nested))

    def test_completed_move_retains_wide_retired_directory_tree_at_pending_bound(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            destination = os.path.join(final_root, "Example Directory")
            claim = os.path.join(staging_root, ".seedsync-retire-" + "5" * 48)
            os.makedirs(os.path.join(claim, "one")); os.makedirs(os.path.join(claim, "two")); os.makedirs(destination)
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            with patch("controller.controller._EMPTY_RETIRED_DIRECTORY_TREE_LIMIT", 2):
                self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED,
                                 self.controller._Controller__move_from_staging("Example Directory"))

            self.assertTrue(os.path.isdir(claim))
            self.assertTrue(os.path.isdir(os.path.join(claim, "one")))
            self.assertTrue(os.path.isdir(os.path.join(claim, "two")))

    def test_refresh_cancellation_restores_active_claim_before_runtime_remap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source = os.path.join(staging_root, "release", "movie.mkv")
            destination = os.path.join(final_root, "release", "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.makedirs(os.path.dirname(destination))
            Path(source).write_bytes(b"equal"); Path(destination).write_bytes(b"equal")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            started = threading.Event()

            def wait_for_cancel(*args):
                cancel_event = args[-2]
                started.set()
                cancel_event.wait(2)
                raise OSError(errno.ECANCELED, "cancelled")

            with patch.object(Controller, "_Controller__claimed_regular_file_matches_destination", side_effect=wait_for_cancel):
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("release"))
                self.assertTrue(started.wait(1))
                self.assertTrue(self.controller._Controller__cancel_and_settle_collision_claim_for_refresh())

            self.assertTrue(os.path.exists(source))
            self.assertEqual([], [entry for entry in os.listdir(os.path.dirname(source))
                                  if entry.startswith(".seedsync-retire-")])

    def test_async_collision_restore_keeps_committed_move_token_until_healthy_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source = os.path.join(staging_root, "release", "movie.mkv")
            destination = os.path.join(final_root, "release", "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.makedirs(os.path.dirname(destination))
            Path(source).write_bytes(b"equal"); Path(destination).write_bytes(b"equal")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            self.controller._Controller__local_scan_process.generation = 58
            self.controller._Controller__updater = ModelUpdater(self.controller)
            accumulator = self.controller._Controller__progressive_local_scan_state
            root = SystemFile("release", 1)
            accumulator.apply([ScannerResult(
                datetime.now(), [root], scanned_path_pair_ids={None}, generation=56,
                is_progress=True, is_scan_final=True, is_full_snapshot=True,
                full_snapshot_path_pair_ids={None}, completed_path_pair_ids={None},
            )])

            self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                             self.controller._Controller__move_from_staging("release"))
            claim = self.controller._Controller__collision_compare_claim
            future = self.controller._Controller__collision_compare_future
            cancel_event = self.controller._Controller__collision_compare_cancel_event
            self.assertIsNotNone(claim)
            self.assertIsNotNone(future)
            future.result(timeout=5)
            cancel_event.set()
            self.controller._Controller__restore_cancelled_collision_claim(
                future, claim[0], claim[1], claim[2], cancel_event,
                getattr(self.controller, "_Controller__collision_compare_epoch", 0), claim[4],
            )

            tokens = accumulator._ProgressiveScanAccumulator__move_invalidations_by_root
            self.assertEqual({58}, {generation for generation, status in tokens[(None, "release")].values()
                                   if status == "committed"})
            self.assertTrue(os.path.exists(source))

            accumulator.apply([ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={None}, generation=59,
                is_progress=True, is_scan_final=True, is_full_snapshot=True,
                full_snapshot_path_pair_ids={None}, completed_path_pair_ids={None},
            )])
            self.assertNotIn((None, "release"), accumulator.snapshot())
            self.assertNotIn((None, "release"), accumulator._ProgressiveScanAccumulator__move_invalidations_by_root)

    def test_exit_restores_completed_claim_before_cancellation_callback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source = os.path.join(staging_root, "release", "movie.mkv")
            destination = os.path.join(final_root, "release", "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.makedirs(os.path.dirname(destination))
            Path(source).write_bytes(b"different"); Path(destination).write_bytes(b"targeting")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                             self.controller._Controller__move_from_staging("release"))
            self._settle_collision_compare()
            self.controller.exit()

            self.assertTrue(os.path.exists(source))
            self.assertEqual([], [entry for entry in os.listdir(os.path.dirname(source))
                                  if entry.startswith(".seedsync-retire-")])

    def test_exit_restores_when_worker_finishes_after_shutdown_done_observation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source = os.path.join(staging_root, "release", "movie.mkv")
            destination = os.path.join(final_root, "release", "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.makedirs(os.path.dirname(destination))
            Path(source).write_bytes(b"equal"); Path(destination).write_bytes(b"equal")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root
            compare_started = threading.Event()
            allow_finish = threading.Event()
            restore_finished = threading.Event()

            def wait_for_shutdown(*args):
                compare_started.set()
                self.assertTrue(allow_finish.wait(2))
                raise OSError(errno.ECANCELED, "cancelled")

            with patch.object(Controller, "_Controller__claimed_regular_file_matches_destination", side_effect=wait_for_shutdown):
                self.assertEqual(Controller.MoveFromStagingResult.DEFERRED,
                                 self.controller._Controller__move_from_staging("release"))
                self.assertTrue(compare_started.wait(1))
                future = self.controller._Controller__collision_compare_future
                original_done = future.done
                first_done_observation = True

                def finish_after_done_observation():
                    nonlocal first_done_observation
                    if first_done_observation:
                        first_done_observation = False
                        allow_finish.set()
                        return False
                    return original_done()

                original_rename = Controller._Controller__rename_no_replace

                def record_restore(rename_source, rename_destination):
                    return original_rename(rename_source, rename_destination)

                original_remove_sidecar = Controller._Controller__remove_collision_claim_sidecar

                def record_sidecar_removal(sidecar_path):
                    result = original_remove_sidecar(sidecar_path)
                    restore_finished.set()
                    return result

                with patch.object(future, "done", side_effect=finish_after_done_observation), \
                        patch.object(Controller, "_Controller__rename_no_replace", side_effect=record_restore), \
                        patch.object(Controller, "_Controller__remove_collision_claim_sidecar", side_effect=record_sidecar_removal):
                    self.controller.exit()
                    self.assertTrue(restore_finished.wait(1))

            self.assertTrue(os.path.exists(source))
            self.assertEqual([], [entry for entry in os.listdir(os.path.dirname(source))
                                  if entry.startswith(".seedsync-retire-")])

    def test_active_collision_compare_predicate_tracks_claimed_root(self):
        self.controller._Controller__collision_compare_claim = (
            os.path.join("C:\\temporary", "incomplete", "release", "movie.mkv"),
            os.path.join("C:\\temporary", "incomplete", "release", ".seedsync-retire-token"),
            os.path.join("C:\\temporary", "final", "release", "movie.mkv"),
            None,
        )
        with patch.object(
                self.controller,
                "_Controller__resolve_safe_final_move_paths",
                return_value=("", "", os.path.join("C:\\temporary", "incomplete", "release"), ""),
        ):
            self.assertTrue(self.controller.has_active_collision_compare("release"))

    def test_stale_collision_callback_cannot_restore_into_new_generation_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = os.path.join(temp_dir, "movie.mkv")
            claimed = os.path.join(temp_dir, ".seedsync-retire-old")
            destination = os.path.join(temp_dir, "final.mkv")
            Path(original).write_bytes(b"new-generation")
            Path(claimed).write_bytes(b"old-generation")
            Path(destination).write_bytes(b"final")
            self.controller._Controller__collision_compare_epoch = 2
            cancelled = threading.Event(); cancelled.set()
            completed = MagicMock(); completed.result.return_value = None

            self.controller._Controller__restore_cancelled_collision_claim(
                completed, original, claimed, destination, cancelled, 1,
            )

            self.assertEqual(b"new-generation", Path(original).read_bytes())
            self.assertEqual(b"old-generation", Path(claimed).read_bytes())

    def test_move_from_staging_retains_same_size_nested_collision_with_different_mtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release", "nested")
            destination_tree = os.path.join(final_root, "release", "nested")
            os.makedirs(source_tree)
            os.makedirs(destination_tree)
            source_leaf = os.path.join(source_tree, "E06.mkv")
            destination_leaf = os.path.join(destination_tree, "E06.mkv")
            Path(source_leaf).write_bytes(b"same-size")
            Path(destination_leaf).write_bytes(b"same-size")
            os.utime(source_leaf, ns=(1_786_400_000_000_000_000,) * 2)
            os.utime(destination_leaf, ns=(1_786_400_001_000_000_000,) * 2)
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("release")

            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, result)
            self.assertEqual(b"same-size", Path(destination_leaf).read_bytes())
            self.assertTrue(os.path.exists(source_leaf))

    def test_transfer_exclusions_preserve_user_patterns_and_escape_final_leaf_globs(self):
        self.controller._Controller__exclude_patterns = "*.nfo, Sample/"
        self.controller._Controller__model_builder.get_trusted_final_leaf_paths.return_value = (
            "nested/[E06]*?.mkv",
            "name,with,commas.mkv",
        )

        exclusions = self.controller._Controller__transfer_exclude_patterns("release", True)

        self.assertEqual(
            [
                "*.nfo",
                "Sample/",
                ExactPathExclusion("nested/[E06]*?.mkv"),
                ExactPathExclusion("name,with,commas.mkv"),
            ],
            exclusions,
        )

    def test_transfer_exclusions_construct_fixture_shaped_exact_leaf_set(self):
        """Queue keeps all 240 valid Incoming Final leaves typed and distinct."""
        trusted_paths = ["trusted/leaf-{:03d}.bin".format(index) for index in range(1, 239)]
        trusted_paths.extend((
            "trusted/odd ' space [brackets].bin",
            "trusted/nested/semicolon;name.bin",
        ))
        self.controller._Controller__exclude_patterns = ""
        self.controller._Controller__model_builder.get_trusted_final_leaf_paths.return_value = tuple(trusted_paths)

        exclusions = self.controller._Controller__transfer_exclude_patterns("Incoming", True)

        self.assertEqual(240, len(exclusions))
        self.assertEqual([ExactPathExclusion(path) for path in trusted_paths], exclusions)

    def test_fractional_queue_exclusion_serialization_trace_is_target_correlated(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__exclude_patterns = "*.nfo"
        self.controller._Controller__model_builder.get_trusted_final_leaf_paths.return_value = (
            "existing.bin", "nested/other.bin",
        )

        exclusions = self.controller._Controller__transfer_exclude_patterns("sample-directory", True)

        self.assertEqual(
            ["*.nfo", ExactPathExclusion("existing.bin"), ExactPathExclusion("nested/other.bin")],
            exclusions,
        )
        time.sleep(0.05)
        entries = trace.snapshot()["entries"]
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual("queue_exclusion_serialization", entry["message"])
        details = entry["details"]
        self.assertEqual("fractional_mtime_redownload.queue_exclusion_serialization.v2", details["schema"])
        self.assertTrue(details["configured_patterns_present"])
        self.assertTrue(details["exact_leaf_candidates_present"])
        self.assertTrue(details["exact_exclusions_serialized"])
        self.assertEqual(2, details["exact_exclusion_count"])
        self.assertFalse(details["serialization_skipped"])
        self.assertTrue(details["serialized_exclusions_present"])
        self.assertNotIn("configured_pattern_count", details)
        self.assertNotIn("exact_leaf_candidate_count", details)
        self.assertNotIn("exact_leaf_serialized_count", details)
        self.assertNotIn("exact_leaf_serialization_skipped_count", details)
        self.assertNotIn("serialized_exclusion_count", details)
        self.assertNotIn("exact_leaf_candidates", details)
        self.assertEqual(1, len(trace.snapshot(corr_id=entry["corr_id"])["entries"]))
        self.assertIsNone(entry["file_id"])
        self.assertNotIn("sample-directory", str(entry))
        self.assertNotIn("existing.bin", str(entry))

    def test_queue_readiness_trace_retrieves_stopped_collision_wrong_dispatch_signature(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        root = ModelFile("sample-directory", True)
        root.path_pair_id = "path-pair-a"
        root.remote_size = 10
        leaf = ModelFile("remote-only.bin", False)
        leaf.path_pair_id = "path-pair-a"
        leaf.remote_size = 10
        root.add_child(leaf)
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(root)
        self.controller._Controller__model = model
        self.controller._Controller__path_pairs_by_id = {
            "path-pair-a": SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "path-pair-a": "/local/incomplete"
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add("path-pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("path-pair-a")
        self.controller._Controller__persist.stopped_file_names.add(root.file_id)
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
        self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()
        self.controller._Controller__model_builder.get_trusted_final_leaf_paths.return_value = ()
        self.controller._Controller__lftp.queue.return_value = True

        # Generic stopped-root fixture: the old path would submit the root
        # while its collision remains unresolved and no exact exclusion was
        # serialized. No production backend or private filesystem is used.
        self.controller.queue_command(
            Controller.Command(Controller.Command.Action.QUEUE, root.file_id, origin="manual")
        )
        self.controller._Controller__process_commands()

        correlation = "fractional-mtime:{}".format(opaque_trace_correlation(root.file_id))
        result = trace.query_events(correlation_id=correlation, category_prefix="queue.")
        entries = result["events"]
        messages = [entry["message"] for entry in entries]
        self.assertIn("queue_admission", messages)
        self.assertIn("queue_model_boundary", messages)
        self.assertIn("queue_rescan_readiness", messages)
        self.assertIn("queue_final_decision", messages)
        self.assertNotIn("queue_exclusion_serialization", messages)
        self.assertNotIn("queue_dispatch_boundary", messages)
        self.assertEqual({correlation}, {entry["corr_id"] for entry in entries})
        model_boundary = next(entry for entry in entries if entry["message"] == "queue_model_boundary")
        self.assertTrue(model_boundary["details"]["explicit_stop"])
        self.assertTrue(model_boundary["details"]["collision_unresolved"])
        self.assertFalse(model_boundary["details"]["collision_terminalizable"])
        self.controller._Controller__lftp.queue.assert_not_called()
        for entry in entries:
            self.assertIsNone(entry["file_id"])
            self.assertIsNone(entry["path_pair_id"])
            self.assertNotIn("sample-directory", str(entry))
            self.assertNotIn("path-pair-a", str(entry))

    def test_manual_directory_scan_deferral_records_bounded_readiness_cause(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"queue.readiness": "info"}},
            max_entries=16,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        root = ModelFile("private-directory", True)
        root.path_pair_id = "private-pair"
        root.remote_size = 10
        root.remote_has_transferable_content = True
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(root)
        self.controller._Controller__model = model
        self.controller._Controller__path_pairs_by_id = {
            "private-pair": SimpleNamespace(remote_path="/private/remote", local_path="/private/local"),
        }
        self.controller._Controller__reconciled_remote_path_pair_ids.add("private-pair")
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset(
            {"private-pair"},
        )
        self.controller._Controller__scan_authority_snapshot = {
            "publication_id": 7,
            "model_version": 11,
        }
        callback = MagicMock()

        command = Controller.Command(Controller.Command.Action.QUEUE, root.file_id, origin="manual")
        command.add_callback(callback)
        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        correlation = "fractional-mtime:{}".format(opaque_trace_correlation(root.file_id))
        result = trace.query_events(correlation_id=correlation, category_prefix="queue.")
        entries = result["events"]
        self.assertEqual(
            [
                "queue_admission", "queue_model_boundary",
                "queue_final_decision", "queue_rescan_readiness",
            ],
            [entry["message"] for entry in entries],
        )
        decision = next(entry for entry in entries if entry["message"] == "queue_final_decision")
        details = decision["details"]
        self.assertEqual("queue_readiness.v2", details["schema"])
        self.assertEqual("final_decision", details["phase"])
        self.assertEqual("scan_authority", details["decision_boundary"])
        self.assertEqual("manual", details["origin"])
        self.assertEqual("deferred", details["outcome"])
        self.assertEqual("defer", details["decision"])
        self.assertEqual("initial_scan_authority_pending", details["reason"])
        self.assertFalse(details["dispatch_attempted"])
        self.assertFalse(details["local_reconciled"])
        self.assertTrue(details["remote_reconciled"])
        self.assertTrue(details["unknown_local_overlay"])
        self.assertTrue(details["path_pair_scope_configured"])
        self.assertEqual(7, details["publication_id"])
        self.assertEqual(11, details["model_version"])
        self.assertEqual({correlation}, {entry["corr_id"] for entry in entries})
        self.assertIsNone(decision["file_id"])
        self.assertIsNone(decision["path_pair_id"])
        serialized = str(entries)
        self.assertNotIn("private-directory", serialized)
        self.assertNotIn("private-pair", serialized)
        self.assertNotIn("/private/remote", serialized)
        self.assertNotIn("/private/local", serialized)
        self.assertEqual([], [entry for entry in entries if entry["message"] == "queue_dispatch_boundary"])
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_not_called()
        self.assertEqual("initial_rescan", self.controller._Controller__deferred_queue_intents[root.file_id].phase)
        self.assertTrue(self.controller._Controller__local_scan_process.force_scan.called)
        self.assertTrue(self.controller._Controller__remote_scan_process.force_scan.called)

    def test_manual_directory_scan_deferral_does_not_build_disabled_readiness_details(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"queue.readiness": "off"}},
            max_entries=16,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        root = ModelFile("private-directory", True)
        root.path_pair_id = "private-pair"
        root.remote_size = 10
        root.remote_has_transferable_content = True
        model = Model()
        model.set_base_logger(self.controller.logger)
        model.add_file(root)
        self.controller._Controller__model = model
        self.controller._Controller__reconciled_remote_path_pair_ids.add("private-pair")
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.side_effect = AssertionError(
            "disabled readiness trace evaluated details",
        )
        callback = MagicMock()

        command = Controller.Command(Controller.Command.Action.QUEUE, root.file_id, origin="manual")
        command.add_callback(callback)
        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.assertEqual([], trace.snapshot()["entries"])
        callback.on_failure.assert_not_called()
        self.assertIn(root.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()

    def _seed_manual_directory_scan_readiness_fixture(self, path_pair_id="pair-a"):
        file = ModelFile("sample-directory", True)
        file.path_pair_id = path_pair_id
        file.remote_size = 10
        file.remote_has_transferable_content = True
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {} if path_pair_id is None else {
            path_pair_id: SimpleNamespace(remote_path="/remote", local_path="/local"),
        }
        self.controller._Controller__path_pair_staging_paths = {} if path_pair_id is None else {
            path_pair_id: "/local/incomplete",
        }
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        return file

    def test_manual_directory_queue_retains_intent_until_initial_scan_authority_is_ready(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        self.assertEqual("initial_rescan", intent.phase)
        self.assertTrue(intent.rescan_requested)
        self.assertEqual(0, intent.scoped_rescan_attempts)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_success.assert_not_called()
        callback.on_failure.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with("pair-a")
        self.controller._Controller__remote_scan_process.force_scan.assert_called_once_with("pair-a")

    def test_manual_directory_queue_initial_fence_requires_later_tokens_on_both_sides(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        initial_fence = intent.rescan_generations
        self.assertEqual(
            (("local-test-session", 0), ("remote-test-session", 0)),
            initial_fence,
        )

        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": initial_fence[0]},
            "remote": {"pair-a": initial_fence[1]},
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        callback.on_success.assert_not_called()

        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 1)},
            "remote": {"pair-a": ("remote-test-session", 1)},
        }
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

    def test_manual_directory_duplicate_queue_coalesces_to_one_initial_scan_and_dispatch(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        first_callback = MagicMock()
        first_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        first_command.add_callback(first_callback)
        self.controller.queue_command(first_command)
        self.controller._Controller__process_commands()

        duplicate_callback = MagicMock()
        duplicate_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        duplicate_command.add_callback(duplicate_callback)
        self.controller.queue_command(duplicate_command)
        self.controller._Controller__process_commands()

        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 1)},
            "remote": {"pair-a": ("remote-test-session", 1)},
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__process_commands()

        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with("pair-a")
        self.controller._Controller__remote_scan_process.force_scan.assert_called_once_with("pair-a")
        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        first_callback.on_success.assert_called_once_with()
        duplicate_callback.on_success.assert_called_once_with()
        first_callback.on_failure.assert_not_called()
        duplicate_callback.on_failure.assert_not_called()

    def test_manual_directory_queue_stop_before_initial_scan_ready_cancels_intent(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        queue_callback = MagicMock()
        queue_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        queue_command.add_callback(queue_callback)
        self.controller.queue_command(queue_command)
        self.controller._Controller__process_commands()

        stop_callback = MagicMock()
        stop_command = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        stop_command.add_callback(stop_callback)
        self.controller.queue_command(stop_command)
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        queue_callback.on_failure.assert_called_once()
        stop_callback.on_success.assert_called_once_with()
        stop_callback.on_failure.assert_not_called()

    def test_manual_directory_queue_initial_scan_request_failure_rejects_once(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        self.controller._Controller__local_scan_process.force_scan.side_effect = RuntimeError(
            "local scan wake failed",
        )
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_called_once()
        callback.on_success.assert_not_called()

    def test_manual_directory_queue_initial_scan_deadline_retires_callback_once(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        trace = BreadcrumbTraceCollector(
            lambda: True,
            policy={"default": "off", "rules": {"queue.readiness": "info"}},
            max_entries=16,
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)
        duplicate_callback = MagicMock()
        duplicate_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        duplicate_command.add_callback(duplicate_callback)

        with patch.object(Controller, "_DEFERRED_INITIAL_RESCAN_TIMEOUT_IN_SECS", 0.0):
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
            self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            intent = self.controller._Controller__deferred_queue_intents[file.file_id]
            intent.rescan_deadline_monotonic = 0.0
            intent.rescan_deadline_grace_consumed = True

            # No scan authority arrives.  The next controller turn reaches
            # the deadline and owns the retained callback failures, including
            # a duplicate already waiting in the command queue.
            self.controller.queue_command(duplicate_command)
            self.controller._Controller__process_commands()
            self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_success.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Queue preflight cancelled: initial_scan_authority_deadline", 409,
        )
        duplicate_callback.on_success.assert_not_called()
        duplicate_callback.on_failure.assert_called_once_with(
            "Queue preflight cancelled: initial_scan_authority_deadline", 409,
        )
        readiness = [
            entry for entry in trace.snapshot()["entries"]
            if entry["message"] == "queue_rescan_readiness"
        ]
        self.assertEqual(
            ["rescan_request", "rescan_deadline"],
            [entry["details"]["phase"] for entry in readiness],
        )
        self.assertEqual("timeout", readiness[-1]["details"]["outcome"])

    def test_manual_directory_queue_ready_authority_wins_deadline_boundary(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        with patch.object(Controller, "_DEFERRED_INITIAL_RESCAN_TIMEOUT_IN_SECS", 0.0):
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
            self.controller._Controller__scan_authority_tokens = {
                "local": {"pair-a": ("local-test-session", 1)},
                "remote": {"pair-a": ("remote-test-session", 1)},
            }
            self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
            self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
            self.controller._Controller__process_commands()
            self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

    def test_manual_directory_queue_deadline_notifies_when_claim_retirement_waits(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        with patch.object(Controller, "_DEFERRED_INITIAL_RESCAN_TIMEOUT_IN_SECS", 0.0), \
                patch.object(
                    self.controller, "_Controller__retire_deferred_queue_intent", return_value=False,
                ):
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
            intent = self.controller._Controller__deferred_queue_intents[file.file_id]
            intent.rescan_deadline_monotonic = 0.0
            intent.rescan_deadline_grace_consumed = True
            self.controller._Controller__process_commands()

        callback.on_success.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Queue preflight cancelled: initial_scan_authority_deadline", 409,
        )

    def test_manual_directory_queue_deadline_preserves_callback_retry(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        retry = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        callback = MagicMock()
        callback.on_failure.side_effect = lambda *_: self.controller.queue_command(retry)
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        self.controller._Controller__deferred_queue_intents[
            file.file_id
        ].rescan_deadline_monotonic = 0.0
        self.controller._Controller__deferred_queue_intents[
            file.file_id
        ].rescan_deadline_grace_consumed = True
        self.controller._Controller__process_commands()

        # The retry is consumed in the same drain, but it becomes a distinct
        # fresh intent rather than being removed with the old waiters.
        retry_intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        self.assertIs(retry, retry_intent.command)
        callback.on_failure.assert_called_once()

    def test_manual_directory_queue_deadline_grace_allows_scan_publication(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        intent.rescan_deadline_monotonic = 0.0
        self.controller._Controller__process_commands()
        self.assertTrue(intent.rescan_deadline_grace_consumed)
        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)

        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 1)},
            "remote": {"pair-a": ("remote-test-session", 1)},
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__process_commands()
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        callback.on_success.assert_called_once_with()

    def test_manual_directory_queue_stale_initial_scan_unknown_waits_for_deadline(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        self.controller._record_path_pair_scan_tokens(
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-a"},
                unknown_path_pair_ids={"pair-a"}, is_scan_final=True,
                generation=0, session_token="local-test-session",
            ),
            None,
        )
        self.controller._Controller__process_commands()

        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        callback.on_failure.assert_not_called()

    def test_manual_directory_queue_initial_scan_final_unknown_retires_once(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        self.controller._record_path_pair_scan_tokens(
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-a"},
                unknown_path_pair_ids={"pair-a"}, is_scan_final=True,
                generation=1, session_token="local-test-session",
            ),
            None,
        )
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_success.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Queue preflight cancelled: initial_local_scan_unknown", 409,
        )

    def test_manual_directory_queue_initial_scan_missing_pair_token_retires_once(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        missing_token_result = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"pair-a"},
            completed_path_pair_ids={"pair-a"}, is_scan_final=True,
            generation=1, session_token="local-test-session",
        )
        missing_token_result._scan_authority_tokens_by_pair = {}
        self.controller._record_path_pair_scan_tokens(missing_token_result, None)
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_success.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Queue preflight cancelled: initial_local_scan_token_missing", 409,
        )

    def test_manual_directory_queue_later_local_scan_failure_retires_without_dispatch(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)

        self.controller._record_path_pair_scan_tokens(
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-a"},
                failed=True, is_scan_final=False,
                error_message="local scan failed",
                generation=1,
                session_token="local-test-session",
            ),
            None,
        )
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_called_once()
        callback.on_success.assert_not_called()

        self.controller._Controller__process_commands()
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_called_once()

    def test_legacy_directory_queue_unscoped_full_scan_failure_retires_once(self):
        file = self._seed_manual_directory_scan_readiness_fixture(None)
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        self.assertIsNone(intent.path_pair_id)
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with(None)
        self.controller._Controller__remote_scan_process.force_scan.assert_called_once_with(None)

        self.controller._record_path_pair_scan_tokens(
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={None},
                failed=True, is_scan_final=True, is_targeted_scan=False,
                unknown_path_pair_ids={None}, generation=1,
                session_token="local-test-session",
            ),
            None,
        )
        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_called_once()
        callback.on_success.assert_not_called()

        self.controller._Controller__process_commands()
        callback.on_failure.assert_called_once()

    def test_manual_directory_queue_stale_scan_failure_does_not_retire_intent(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        self.controller._record_path_pair_scan_tokens(
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-a"},
                failed=True, is_scan_final=False, generation=0,
                session_token="local-test-session",
            ),
            None,
        )
        self.controller._Controller__process_commands()

        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_not_called()

    def test_manual_directory_queue_unscoped_or_unrelated_scan_failure_is_ignored(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        for scanned_pair_ids in ({None}, {"pair-b"}):
            self.controller._record_path_pair_scan_tokens(
                ScannerResult(
                    datetime.now(), [], scanned_path_pair_ids=scanned_pair_ids,
                    failed=True, is_scan_final=False, generation=1,
                    session_token="local-test-session",
                ),
                None,
            )
            self.controller._Controller__process_commands()

        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_not_called()

    def test_manual_directory_queue_scanner_session_change_retires_without_dispatch(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        self.controller._Controller__local_scan_process.session_token = "replacement-local-session"

        self.controller._Controller__process_commands()

        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_called_once()
        callback.on_success.assert_not_called()

    def test_initial_ready_then_collision_uses_a_fresh_second_scan_fence(self):
        file = self._seed_manual_directory_scan_readiness_fixture()
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 1)},
            "remote": {"pair-a": ("remote-test-session", 1)},
        }
        self.controller._Controller__model_builder.has_unresolved_staging_collision.side_effect = [
            True, True, False, False,
        ]
        self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = {
            file.file_id,
        }
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED,
        )
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)
        initial_intent = DeferredQueueIntent(
            command,
            file.file_id,
            file.path_pair_id,
            phase="initial_rescan",
            rescan_requested=True,
            rescan_generations=(
                ("local-test-session", 0),
                ("remote-test-session", 0),
            ),
        )
        self.controller._Controller__deferred_queue_intents_map()[file.file_id] = initial_intent

        self.controller._Controller__process_commands()

        second_intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        self.assertEqual("rescan", second_intent.phase)
        self.assertEqual(1, second_intent.scoped_rescan_attempts)
        self.assertEqual(
            (
                ("local-test-session", 1),
                ("remote-test-session", 1),
            ),
            second_intent.rescan_generations,
        )
        self.controller._Controller__lftp.queue.assert_not_called()

        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 2)},
            "remote": {"pair-a": ("remote-test-session", 2)},
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

    def test_queue_collision_preflight_defers_without_lftp_and_stop_cancels_intent(self):
        file = ModelFile("sample-directory", True)
        file.path_pair_id = "pair-a"
        file.remote_size = 10
        remote_leaf = ModelFile("remote-only.bin", False)
        remote_leaf.path_pair_id = "pair-a"
        remote_leaf.remote_size = 10
        file.add_child(remote_leaf)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__path_pair_staging_paths = {"pair-a": "/local/incomplete"}
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
        self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()
        callback = MagicMock()
        queue_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        queue_command.add_callback(callback)

        self.controller.queue_command(queue_command)
        self.controller._Controller__process_commands()

        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
        stop_command = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        stop_callback = MagicMock()
        stop_command.add_callback(stop_callback)
        self.controller.queue_command(stop_command)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        self.controller._Controller__lftp.kill.assert_not_called()
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        callback.on_failure.assert_called_once()
        stop_callback.on_success.assert_called_once_with()
        self.assertIn(file.file_id, self.controller._Controller__persist.stopped_file_names)

    def test_queue_stopped_root_file_collision_defers_without_lftp(self):
        file = ModelFile("sample-file.bin", False)
        file.path_pair_id = "pair-a"
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__path_pair_staging_paths = {"pair-a": "/local/incomplete"}
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__persist.stopped_file_names.add(file.file_id)
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
        self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()

        self.controller.queue_command(
            Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        )
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)

    def test_real_scanner_aggregate_tokens_release_deferred_queue_once(self):
        """Real scanner intake must carry both session fences into Queue readiness."""
        pair_id = "pair-a"
        self.controller._Controller__path_pairs_by_id = {
            pair_id: SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__path_pair_staging_paths = {
            pair_id: "/local/incomplete"
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add(pair_id)
        self.controller._Controller__reconciled_remote_path_pair_ids.add(pair_id)
        # Keep this integration fixture focused on production intake and Queue
        # readiness; diagnostic breadcrumbs are covered by their own tests.
        self.controller._Controller__context.breadcrumb_trace = SimpleNamespace(
            is_effectively_enabled=lambda category, level="info": False,
        )

        local_process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
        remote_process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
        self.addCleanup(local_process.close_queues)
        self.addCleanup(remote_process.close_queues)
        self.controller._Controller__local_scan_process = local_process
        self.controller._Controller__remote_scan_process = remote_process

        def publish_final(process):
            root = SystemFile("sample-directory", 10, True)
            root.path_pair_id = pair_id
            process._ScannerProcess__publish_result(ScannerResult(
                datetime.now(), [root], scanned_path_pair_ids={pair_id},
                completed_path_pair_ids={pair_id}, generation=1, is_progress=True,
                is_scan_final=True, is_full_snapshot=True,
                full_snapshot_path_pair_ids={pair_id}, session_token=process.session_token,
            ))
            return _pop_scan_updates(
                self.controller,
                "local" if process is local_process else "remote",
                process,
            )

        local_result = publish_final(local_process)
        remote_result = publish_final(remote_process)
        self.assertIsNotNone(local_result)
        self.assertIsNotNone(remote_result)
        self.assertEqual(local_process.session_token, local_result.session_token)
        self.assertEqual(remote_process.session_token, remote_result.session_token)
        self.controller._record_path_pair_scan_tokens(local_result, remote_result)
        self.assertEqual(
            (local_process.session_token, 1),
            self.controller._Controller__scan_authority_tokens["local"][pair_id],
        )
        self.assertEqual(
            (remote_process.session_token, 1),
            self.controller._Controller__scan_authority_tokens["remote"][pair_id],
        )

        file = ModelFile("sample-directory", True)
        file.path_pair_id = pair_id
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)
        self.controller._Controller__deferred_queue_intents_map()[file.file_id] = DeferredQueueIntent(
            command, file.file_id, pair_id, phase="rescan", rescan_requested=True,
            rescan_generations=(
                (local_process.session_token, 0),
                (remote_process.session_token, 0),
            ),
        )

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

    def test_real_scanner_replacement_rejects_old_session_result(self):
        pair_id = "pair-a"
        self.controller._Controller__path_pairs_by_id = {pair_id: SimpleNamespace()}
        self.controller._Controller__context.breadcrumb_trace = SimpleNamespace(
            is_effectively_enabled=lambda category, level="info": False,
        )
        old_process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
        replacement_process = ScannerProcess(scanner=SimpleNamespace(), interval_in_ms=0, verbose=False)
        self.addCleanup(old_process.close_queues)
        self.addCleanup(replacement_process.close_queues)
        self.controller._Controller__local_scan_process = old_process

        root = SystemFile("sample-directory", 10, True)
        root.path_pair_id = pair_id
        old_process._ScannerProcess__publish_result(ScannerResult(
            datetime.now(), [root], scanned_path_pair_ids={pair_id},
            completed_path_pair_ids={pair_id}, generation=1, is_progress=True,
            is_scan_final=True, is_full_snapshot=True,
            full_snapshot_path_pair_ids={pair_id}, session_token=old_process.session_token,
        ))
        old_result = _pop_scan_updates(self.controller, "local", old_process)
        self.assertIsNotNone(old_result)
        self.controller._record_path_pair_scan_tokens(old_result, None)
        old_authority = self.controller._Controller__scan_authority_tokens["local"][pair_id]

        replacement_process._ScannerProcess__publish_result(ScannerResult(
            datetime.now(), [root], scanned_path_pair_ids={pair_id},
            completed_path_pair_ids={pair_id}, generation=99, is_progress=True,
            is_scan_final=True, is_full_snapshot=True,
            full_snapshot_path_pair_ids={pair_id}, session_token=old_process.session_token,
        ))
        self.controller._Controller__local_scan_process = replacement_process

        self.assertIsNone(_pop_scan_updates(self.controller, "local", replacement_process))
        accumulator = self.controller._Controller__progressive_local_scan_state
        self.assertEqual(replacement_process.session_token, accumulator.session_token)
        self.assertEqual(old_authority, self.controller._Controller__scan_authority_tokens["local"][pair_id])

    def test_mixed_progressive_generations_do_not_advance_untouched_pair(self):
        session_token = "local-mixed-generation-session"
        pair_b = "pair-b"
        pair_a = "pair-a"
        accumulator = _ProgressiveScanAccumulator()
        accumulator.set_session_token(session_token)

        def final_result(pair_id, generation, *, targeted=False):
            root = SystemFile("{}-root".format(pair_id), 10, True)
            root.path_pair_id = pair_id
            return ScannerResult(
                datetime.now(), [root], scanned_path_pair_ids={pair_id},
                completed_path_pair_ids={pair_id}, generation=generation,
                is_progress=True, is_scan_final=True, is_full_snapshot=True,
                full_snapshot_path_pair_ids={pair_id}, is_targeted_scan=targeted,
                session_token=session_token,
            )

        # B is the authority captured immediately before the cleanup fence.
        first_b = accumulator.apply([final_result(pair_b, 1)])
        self.assertIsNotNone(first_b)
        self.controller._record_path_pair_scan_tokens(first_b, None)

        # A targeted generation-2 event is drained alongside B's still-current
        # generation-1 completion. The aggregate generation is 2, but B must
        # retain its own generation-1 authority token.
        mixed = accumulator.apply([
            final_result(pair_b, 1),
            final_result(pair_a, 2, targeted=True),
        ])
        self.assertIsNotNone(mixed)
        self.assertEqual(2, mixed.generation)
        self.assertEqual(
            (session_token, 1), mixed._scan_authority_tokens_by_pair[pair_b],
        )
        self.assertEqual(
            (session_token, 2), mixed._scan_authority_tokens_by_pair[pair_a],
        )
        self.controller._record_path_pair_scan_tokens(mixed, None)
        self.controller._Controller__scan_authority_tokens["remote"] = {
            pair_b: ("remote-mixed-generation-session", 2),
        }
        self.assertEqual(
            (session_token, 1),
            self.controller._Controller__scan_authority_tokens["local"][pair_b],
        )

        file = ModelFile("sample-directory", True)
        file.path_pair_id = pair_b
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            pair_b: SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__reconciled_local_path_pair_ids.add(pair_b)
        self.controller._Controller__reconciled_remote_path_pair_ids.add(pair_b)
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        intent = DeferredQueueIntent(
            command, file.file_id, pair_b, phase="rescan", rescan_requested=True,
            rescan_generations=(
                (session_token, 1),
                ("remote-mixed-generation-session", 1),
            ),
        )
        self.controller._Controller__deferred_queue_intents_map()[file.file_id] = intent

        self.assertFalse(self.controller._Controller__queue_scoped_rescan_ready(intent))
        self.controller._Controller__process_commands()
        self.controller._Controller__lftp.queue.assert_not_called()
        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)

    def test_queue_collision_preflight_rescan_ready_dispatches_once(self):
        file = ModelFile("sample-directory", True)
        file.path_pair_id = "pair-a"
        file.remote_size = 10
        remote_leaf = ModelFile("remote-only.bin", False)
        remote_leaf.path_pair_id = "pair-a"
        remote_leaf.remote_size = 10
        file.add_child(remote_leaf)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__path_pair_staging_paths = {"pair-a": "/local/incomplete"}
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 1)},
            "remote": {"pair-a": ("remote-test-session", 1)},
        }
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)
        self.controller._Controller__deferred_queue_intents_map()[file.file_id] = DeferredQueueIntent(
            command, file.file_id, file.path_pair_id, phase="rescan", rescan_requested=True,
            rescan_generations=(
                ("local-test-session", 0), ("remote-test-session", 0),
            ),
        )

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

    def test_queue_scoped_rescan_requires_post_cleanup_authority_tokens(self):
        file = ModelFile("sample-directory", True)
        file.path_pair_id = "pair-a"
        file.remote_size = 10
        remote_leaf = ModelFile("remote-only.bin", False)
        remote_leaf.path_pair_id = "pair-a"
        remote_leaf.remote_size = 10
        file.add_child(remote_leaf)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__path_pair_staging_paths = {"pair-a": "/local/incomplete"}
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller._Controller__deferred_queue_intents_map()[file.file_id] = DeferredQueueIntent(
            command, file.file_id, file.path_pair_id, phase="rescan", rescan_requested=True,
            rescan_generations=(
                ("local-test-session", 4), ("remote-test-session", 7),
            ),
        )
        # These are authoritative sets but only acknowledge a scan that began
        # before the cleanup request.  They must not satisfy this intent.
        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 4)},
            "remote": {"pair-a": ("remote-test-session", 7)},
        }

        self.controller._Controller__process_commands()
        self.controller._Controller__lftp.queue.assert_not_called()
        self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)

        self.controller._record_path_pair_scan_tokens(
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=5,
                session_token="local-test-session",
            ),
            ScannerResult(
                datetime.now(), [], scanned_path_pair_ids={"pair-a"}, generation=8,
                session_token="remote-test-session",
            ),
        )
        self.controller._Controller__process_commands()
        self.controller._Controller__lftp.queue.assert_called_once_with(
            file.name,
            True,
            remote_base_dir_path="/remote",
            local_base_dir_path="/local/incomplete",
        )

    def test_queue_scoped_rescan_force_failure_rejects_without_dispatch(self):
        file = ModelFile("sample-directory", True)
        file.path_pair_id = "pair-a"
        file.remote_size = 10
        remote_leaf = ModelFile("remote-only.bin", False)
        remote_leaf.path_pair_id = "pair-a"
        remote_leaf.remote_size = 10
        file.add_child(remote_leaf)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__path_pair_staging_paths = {"pair-a": "/local/incomplete"}
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
        self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()
        self.controller._Controller__local_scan_process.force_scan.side_effect = RuntimeError("scan wake failed")
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
        callback.on_failure.assert_called_once()

    def test_queue_persistent_deferred_collision_rejects_manual_and_auto_queue(self):
        file = ModelFile("sample-directory", True)
        file.path_pair_id = "pair-a"
        file.remote_size = 10
        remote_leaf = ModelFile("remote-only.bin", False)
        remote_leaf.path_pair_id = "pair-a"
        remote_leaf.remote_size = 10
        file.add_child(remote_leaf)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote", local_path="/local")
        }
        self.controller._Controller__path_pair_staging_paths = {"pair-a": "/local/incomplete"}
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
        self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = {
            file.file_id,
        }
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.DEFERRED,
        )

        callback = MagicMock()
        manual = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        manual.add_callback(callback)
        self.controller.queue_command(manual)
        self.controller._Controller__process_commands()
        callback.on_failure.assert_called_once()
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)

        auto_queue = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        auto_queue.origin = "auto_queue"
        self.controller.queue_command(auto_queue)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)

    def test_queue_collision_equal_claim_waits_then_rescans_before_one_dispatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = os.path.join(temp_dir, "incomplete")
            final = os.path.join(temp_dir, "final")
            os.mkdir(staging)
            os.mkdir(final)
            os.mkdir(os.path.join(staging, "sample-directory"))
            os.mkdir(os.path.join(final, "sample-directory"))
            staging_leaf = os.path.join(staging, "sample-directory", "same.bin")
            final_leaf = os.path.join(final, "sample-directory", "same.bin")
            Path(staging_leaf).write_bytes(b"same bytes")
            Path(final_leaf).write_bytes(b"same bytes")
            os.utime(staging_leaf, ns=(1_700_000_000_000_000_000,) * 2)
            os.utime(final_leaf, ns=(1_700_000_000_000_000_000,) * 2)

            pair = PathPair(
                id="pair-a", name="Pair A", remote_path="/remote", local_path=final, enabled=True,
            )
            file = ModelFile("sample-directory", True)
            file.path_pair_id = pair.id
            file.remote_size = 10
            remote_leaf = ModelFile("same.bin", False)
            remote_leaf.path_pair_id = pair.id
            remote_leaf.remote_size = 10
            file.add_child(remote_leaf)
            self.controller._Controller__model.get_file.return_value = file
            self.controller._Controller__path_pairs_by_id = {pair.id: pair}
            self.controller._Controller__path_pair_staging_paths = {pair.id: staging}
            self.controller._Controller__reconciled_local_path_pair_ids.add(pair.id)
            self.controller._Controller__reconciled_remote_path_pair_ids.add(pair.id)
            self.controller._Controller__persist.stopped_file_names.add(file.file_id)
            self.controller._Controller__model_builder.has_unresolved_staging_collision.side_effect = [
                True, True, True, False, False,
            ]
            self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = {
                file.file_id,
            }
            self.controller._Controller__model_builder.get_staging_collision_relative_paths.return_value = (
                "same.bin",
            )
            callback = MagicMock()
            command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
            command.add_callback(callback)

            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
            self.assertNotIn(file.file_id, self.controller._Controller__pending_queue_dispatches)
            self.assertEqual(0, self.controller._Controller__lftp.queue.call_count)
            self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)

            self.controller._Controller__process_commands()
            self.assertEqual(0, self.controller._Controller__lftp.queue.call_count)
            future = self.controller._Controller__collision_compare_future
            if future is not None:
                future.result(timeout=5)
            self.controller._Controller__process_commands()
            deferred_intent = self.controller._Controller__deferred_queue_intents.get(file.file_id)
            self.assertIsNotNone(
                deferred_intent,
                "deferred intent lost; queue calls={} collision calls={} stopped={}".format(
                    self.controller._Controller__lftp.queue.call_count,
                    self.controller._Controller__model_builder.has_unresolved_staging_collision.call_count,
                    self.controller._Controller__persist.stopped_file_names,
                ),
            )
            self.assertEqual("rescan", deferred_intent.phase)

            self.controller._Controller__reconciled_local_path_pair_ids.add(pair.id)
            self.controller._Controller__reconciled_remote_path_pair_ids.add(pair.id)
            self.controller._Controller__scan_authority_tokens = {
                "local": {pair.id: ("local-test-session", 1)},
                "remote": {pair.id: ("remote-test-session", 1)},
            }
            self.controller._Controller__process_commands()
            self.assertEqual(1, self.controller._Controller__lftp.queue.call_count)
            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            callback.on_success.assert_called_once_with()
            callback.on_failure.assert_not_called()

    def _seed_active_deferred_collision_claim(self, *, future_done=True):
        temp_dir = tempfile.TemporaryDirectory()
        staging = os.path.join(temp_dir.name, "incomplete")
        final = os.path.join(temp_dir.name, "final")
        os.mkdir(staging)
        os.mkdir(final)
        os.mkdir(os.path.join(staging, "sample-directory"))
        os.mkdir(os.path.join(final, "sample-directory"))
        original = os.path.join(staging, "sample-directory", "same.bin")
        claimed = os.path.join(staging, "sample-directory", ".seedsync-retire-claim")
        destination = os.path.join(final, "sample-directory", "same.bin")
        Path(claimed).write_bytes(b"same bytes")
        Path(destination).write_bytes(b"same bytes")
        sidecar = claimed + ".json"
        Path(sidecar).write_text("{}", encoding="utf-8")
        pair = PathPair(
            id="pair-a", name="Pair A", remote_path="/remote", local_path=final, enabled=True,
        )
        file = ModelFile("sample-directory", True)
        file.path_pair_id = pair.id
        file.remote_size = 10
        remote_leaf = ModelFile("same.bin", False)
        remote_leaf.path_pair_id = pair.id
        remote_leaf.remote_size = 10
        file.add_child(remote_leaf)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__is_explicitly_stopped = MagicMock(return_value=False)
        self.controller._Controller__path_pairs_by_id = {pair.id: pair}
        self.controller._Controller__path_pair_staging_paths = {pair.id: staging}
        self.controller._Controller__reconciled_local_path_pair_ids.add(pair.id)
        self.controller._Controller__reconciled_remote_path_pair_ids.add(pair.id)
        self.controller._Controller__collision_compare_lock = Lock()
        self.controller._Controller__collision_compare_cancel_event = threading.Event()
        future = Future()
        if future_done:
            future.set_result(("equal", None, None))
        self.controller._Controller__collision_compare_future = future
        self.controller._Controller__collision_compare_claim = (
            original, claimed, destination, "claim-key", sidecar, pair.id, file.file_id,
        )
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        intent = DeferredQueueIntent(command, file.file_id, pair.id)
        self.controller._Controller__deferred_queue_intents_map()[file.file_id] = intent
        return temp_dir, file, staging, original, claimed, sidecar

    def test_queue_autoqueue_clear_builder_defers_for_active_matching_claim(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim(
            future_done=False,
        )
        try:
            self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
            self.controller._Controller__deferred_queue_intents.clear()
            command = Controller.Command(
                Controller.Command.Action.QUEUE, file.file_id, origin="auto_queue",
            )
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            intent = self.controller._Controller__deferred_queue_intents.get(file.file_id)
            self.assertIsNotNone(intent)
            self.assertEqual("auto_queue", intent.command.origin)
        finally:
            self.controller._Controller__collision_compare_future.set_result(("equal", None, None))
            self.controller._Controller__cancel_and_settle_collision_claim_for_refresh()
            temp_dir.cleanup()

    def test_queue_stop_state_unknown_settles_matching_active_claim(self):
        temp_dir, file, staging, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        callback = MagicMock()
        self.controller._Controller__deferred_queue_intents[file.file_id].command.add_callback(callback)
        try:
            self.controller._Controller__is_explicitly_stopped = MagicMock(
                side_effect=RuntimeError("stop state unavailable"),
            )
            self.controller._Controller__process_commands()

            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(os.path.exists(original))
            self.assertFalse(os.path.exists(claimed))
            self.assertFalse(os.path.exists(sidecar))
            self.assertIsNone(self.controller._Controller__collision_compare_claim)
            callback.on_failure.assert_called_once()
        finally:
            temp_dir.cleanup()

    def test_queue_fresh_stop_state_unknown_settles_active_claim_without_intent(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        callback = MagicMock()
        self.controller._Controller__deferred_queue_intents.clear()
        self.controller._Controller__is_explicitly_stopped = MagicMock(
            side_effect=RuntimeError("stop state unavailable"),
        )
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(os.path.exists(original))
            self.assertFalse(os.path.exists(claimed))
            self.assertFalse(os.path.exists(sidecar))
            self.assertIsNone(self.controller._Controller__collision_compare_claim)
            callback.on_failure.assert_called_once()
            callback.on_success.assert_not_called()
        finally:
            temp_dir.cleanup()

    def test_queue_final_stop_state_unknown_settles_ready_claim_before_lftp(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        callback = MagicMock()
        intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        intent.phase = "rescan"
        intent.rescan_requested = True
        intent.rescan_generations = (
            ("local-test-session", 0), ("remote-test-session", 0),
        )
        self.controller._Controller__scan_authority_tokens = {
            "local": {file.path_pair_id: ("local-test-session", 1)},
            "remote": {file.path_pair_id: ("remote-test-session", 1)},
        }
        intent.command.add_callback(callback)
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        self.controller._Controller__is_explicitly_stopped = MagicMock(
            side_effect=[False, RuntimeError("final stop state unavailable")],
        )

        try:
            self.controller.queue_command(intent.command)
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(os.path.exists(original))
            self.assertFalse(os.path.exists(claimed))
            self.assertFalse(os.path.exists(sidecar))
            self.assertIsNone(self.controller._Controller__collision_compare_claim)
            callback.on_failure.assert_called_once()
            callback.on_success.assert_not_called()
        finally:
            temp_dir.cleanup()

    def test_queue_rescan_ready_matching_claim_stays_deferred_before_dispatch(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim(
            future_done=False,
        )
        intent = self.controller._Controller__deferred_queue_intents[file.file_id]
        intent.phase = "rescan"
        intent.rescan_requested = True
        intent.rescan_generations = (
            ("local-test-session", 0), ("remote-test-session", 0),
        )
        self.controller._Controller__scan_authority_tokens = {
            "local": {file.path_pair_id: ("local-test-session", 1)},
            "remote": {file.path_pair_id: ("remote-test-session", 1)},
        }
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        self.controller._Controller__is_explicitly_stopped = MagicMock(
            side_effect=[False, False],
        )
        callback = MagicMock()
        intent.command.add_callback(callback)

        try:
            self.controller.queue_command(intent.command)
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            callback.on_success.assert_not_called()
            callback.on_failure.assert_not_called()
            retained = self.controller._Controller__deferred_queue_intents.get(file.file_id)
            self.assertIsNotNone(retained)
            self.assertEqual("collision", retained.phase)
            self.assertIsNotNone(self.controller._Controller__collision_compare_claim)
            self.assertTrue(os.path.exists(claimed))
            self.assertTrue(os.path.exists(sidecar))
        finally:
            self.controller._Controller__collision_compare_future.set_result(("equal", None, None))
            self.controller._Controller__cancel_and_settle_collision_claim_for_refresh()
            temp_dir.cleanup()

    def test_queue_preflight_exception_settles_matching_active_claim_before_retire(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        callback = MagicMock()
        self.controller._Controller__deferred_queue_intents[file.file_id].command.add_callback(callback)
        try:
            self.controller._Controller__move_from_staging = MagicMock(
                side_effect=RuntimeError("preflight failure"),
            )
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(os.path.exists(original))
            self.assertFalse(os.path.exists(claimed))
            self.assertFalse(os.path.exists(sidecar))
            self.assertIsNone(self.controller._Controller__collision_compare_claim)
            callback.on_failure.assert_called_once()
        finally:
            temp_dir.cleanup()

    def test_queue_delayed_claim_settlement_notifies_failure_once(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        callback = MagicMock()
        self.controller._Controller__deferred_queue_intents[file.file_id].command.add_callback(callback)
        original_settle = self.controller._Controller__cancel_and_settle_collision_claim_for_refresh
        self.controller._Controller__move_from_staging = MagicMock(
            side_effect=RuntimeError("preflight failure"),
        )
        settle_attempts = 0

        def settle_after_retry():
            nonlocal settle_attempts
            settle_attempts += 1
            if settle_attempts == 1:
                return False
            return original_settle()

        self.controller._Controller__cancel_and_settle_collision_claim_for_refresh = MagicMock(
            side_effect=settle_after_retry,
        )

        try:
            self.controller._Controller__process_commands()
            callback.on_failure.assert_called_once()
            callback.on_success.assert_not_called()
            self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)

            self.controller._Controller__process_commands()
            callback.on_failure.assert_called_once()
            callback.on_success.assert_not_called()
            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(
                os.path.exists(original),
            )
            self.assertFalse(os.path.exists(claimed))
            self.assertFalse(os.path.exists(sidecar))
        finally:
            temp_dir.cleanup()

    def test_queue_preflight_active_claim_stop_state_unknown_rejects_once(self):
        temp_dir, file, _, original, claimed, sidecar = self._seed_active_deferred_collision_claim()
        callback = MagicMock()
        self.controller._Controller__deferred_queue_intents.clear()
        self.controller._Controller__is_explicitly_stopped = MagicMock(
            side_effect=[False, RuntimeError("preflight stop state unavailable")],
        )
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            callback.on_failure.assert_called_once()
            callback.on_success.assert_not_called()
            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(os.path.exists(original))
            self.assertFalse(os.path.exists(claimed))
            self.assertFalse(os.path.exists(sidecar))
            self.assertIsNone(self.controller._Controller__collision_compare_claim)
        finally:
            temp_dir.cleanup()

    def test_queue_preflight_collision_candidate_stop_state_unknown_rejects_once(self):
        file = ModelFile("sample-directory", True)
        file.path_pair_id = "pair-a"
        file.remote_size = 10
        remote_leaf = ModelFile("remote-only.bin", False)
        remote_leaf.path_pair_id = "pair-a"
        remote_leaf.remote_size = 10
        file.add_child(remote_leaf)
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote", local_path="/local"),
        }
        self.controller._Controller__path_pair_staging_paths = {"pair-a": "/local/incomplete"}
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
        self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()
        self.controller._Controller__is_explicitly_stopped = MagicMock(
            side_effect=[False, RuntimeError("candidate stop state unavailable")],
        )
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        callback.on_failure.assert_called_once()
        callback.on_success.assert_not_called()
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)

    def test_queue_missing_file_does_not_settle_unrelated_legacy_claim(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            legacy_original = os.path.join(temp_dir.name, "legacy-source")
            legacy_claimed = os.path.join(temp_dir.name, ".legacy-claim")
            legacy_destination = os.path.join(temp_dir.name, "legacy-destination")
            Path(legacy_claimed).write_bytes(b"legacy")
            pair_file = ModelFile("sample-directory", True)
            pair_file.path_pair_id = "pair-a"
            command = Controller.Command(
                Controller.Command.Action.QUEUE, pair_file.file_id,
            )
            self.controller._Controller__deferred_queue_intents_map()[pair_file.file_id] = DeferredQueueIntent(
                command, pair_file.file_id, pair_file.path_pair_id,
            )
            self.controller._Controller__collision_compare_claim = (
                legacy_original, legacy_claimed, legacy_destination,
                "legacy-key", None, None,
            )
            self.controller._Controller__model.get_file.side_effect = ModelError("missing")

            self.controller._Controller__process_commands()

            self.assertIn(pair_file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(self.controller._Controller__deferred_queue_intents[pair_file.file_id].stop_requested)
            self.assertTrue(os.path.exists(legacy_claimed))
            self.assertIsNotNone(self.controller._Controller__collision_compare_claim)
        finally:
            temp_dir.cleanup()

    def test_queue_stop_restores_matching_active_collision_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = os.path.join(temp_dir, "incomplete")
            final = os.path.join(temp_dir, "final")
            os.mkdir(staging)
            os.mkdir(final)
            os.mkdir(os.path.join(staging, "sample-directory"))
            os.mkdir(os.path.join(final, "sample-directory"))
            staging_leaf = os.path.join(staging, "sample-directory", "same.bin")
            final_leaf = os.path.join(final, "sample-directory", "same.bin")
            Path(staging_leaf).write_bytes(b"same bytes")
            Path(final_leaf).write_bytes(b"same bytes")
            os.utime(staging_leaf, ns=(1_700_000_000_000_000_000,) * 2)
            os.utime(final_leaf, ns=(1_700_000_000_000_000_000,) * 2)

            pair = PathPair(
                id="pair-a", name="Pair A", remote_path="/remote", local_path=final, enabled=True,
            )
            file = ModelFile("sample-directory", True)
            file.path_pair_id = pair.id
            file.remote_size = 10
            remote_leaf = ModelFile("same.bin", False)
            remote_leaf.path_pair_id = pair.id
            remote_leaf.remote_size = 10
            file.add_child(remote_leaf)
            self.controller._Controller__model.get_file.return_value = file
            self.controller._Controller__path_pairs_by_id = {pair.id: pair}
            self.controller._Controller__path_pair_staging_paths = {pair.id: staging}
            self.controller._Controller__reconciled_local_path_pair_ids.add(pair.id)
            self.controller._Controller__reconciled_remote_path_pair_ids.add(pair.id)
            self.controller._Controller__persist.stopped_file_names.add(file.file_id)
            self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
            self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = {
                file.file_id,
            }
            self.controller._Controller__model_builder.get_staging_collision_relative_paths.return_value = (
                "same.bin",
            )
            queue_callback = MagicMock()
            queue_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
            queue_command.add_callback(queue_callback)
            self.controller.queue_command(queue_command)
            self.controller._Controller__process_commands()

            self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertIsNotNone(self.controller._Controller__collision_compare_claim)
            self.controller.queue_command(
                Controller.Command(Controller.Command.Action.STOP, file.file_id)
            )
            self.controller._Controller__process_commands()

            self.controller._Controller__lftp.queue.assert_not_called()
            self.assertTrue(os.path.exists(staging_leaf))
            self.assertIsNone(self.controller._Controller__collision_compare_claim)
            self.assertIsNone(self.controller._Controller__collision_compare_future)
            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            queue_callback.on_failure.assert_called_once()
            self.controller._Controller__shutdown_collision_compare_worker()

    def test_queue_stop_failed_restore_stays_terminal_when_already_stopped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = os.path.join(temp_dir, "incomplete")
            final = os.path.join(temp_dir, "final")
            os.mkdir(staging)
            os.mkdir(final)
            os.mkdir(os.path.join(staging, "sample-directory"))
            os.mkdir(os.path.join(final, "sample-directory"))
            staging_leaf = os.path.join(staging, "sample-directory", "same.bin")
            final_leaf = os.path.join(final, "sample-directory", "same.bin")
            Path(staging_leaf).write_bytes(b"same bytes")
            Path(final_leaf).write_bytes(b"same bytes")
            os.utime(staging_leaf, ns=(1_700_000_000_000_000_000,) * 2)
            os.utime(final_leaf, ns=(1_700_000_000_000_000_000,) * 2)

            pair = PathPair(
                id="pair-a", name="Pair A", remote_path="/remote", local_path=final, enabled=True,
            )
            file = ModelFile("sample-directory", True)
            file.path_pair_id = pair.id
            file.remote_size = 10
            remote_leaf = ModelFile("same.bin", False)
            remote_leaf.path_pair_id = pair.id
            remote_leaf.remote_size = 10
            file.add_child(remote_leaf)
            self.controller._Controller__model.get_file.return_value = file
            self.controller._Controller__path_pairs_by_id = {pair.id: pair}
            self.controller._Controller__path_pair_staging_paths = {pair.id: staging}
            self.controller._Controller__reconciled_local_path_pair_ids.add(pair.id)
            self.controller._Controller__reconciled_remote_path_pair_ids.add(pair.id)
            # Queue began with an existing stopped marker; this is the path
            # that must not be allowed to resume after a failed Stop restore.
            self.controller._Controller__persist.stopped_file_names.add(file.file_id)
            self.controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
            self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = {
                file.file_id,
            }
            self.controller._Controller__model_builder.get_staging_collision_relative_paths.return_value = (
                "same.bin",
            )
            queue_callback = MagicMock()
            queue_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
            queue_command.add_callback(queue_callback)
            self.controller.queue_command(queue_command)
            self.controller._Controller__process_commands()
            self.assertIsNotNone(self.controller._Controller__collision_compare_claim)

            stop_callback = MagicMock()
            stop_command = Controller.Command(Controller.Command.Action.STOP, file.file_id)
            stop_command.add_callback(stop_callback)
            self.controller.queue_command(stop_command)
            with patch.object(
                    self.controller,
                    "_Controller__cancel_and_settle_collision_claim_for_refresh",
                    return_value=False,
            ):
                self.controller._Controller__process_commands()

            stop_callback.on_failure.assert_called_once()
            self.assertIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(self.controller._Controller__deferred_queue_intents[file.file_id].stop_requested)
            self.controller._Controller__lftp.queue.assert_not_called()

            # Restoration is now allowed to complete, but the intent remains
            # terminal and only its Queue callback is failed; it is never requeued.
            self.controller._Controller__process_commands()
            self.assertNotIn(file.file_id, self.controller._Controller__deferred_queue_intents)
            self.assertTrue(os.path.exists(staging_leaf))
            self.controller._Controller__lftp.queue.assert_not_called()
            queue_callback.on_failure.assert_called_once()
            self.controller._Controller__shutdown_collision_compare_worker()


    def test_fractional_queue_file_exclusion_trace_reports_configured_only(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__exclude_patterns = "*.nfo"

        self.assertEqual(
            "*.nfo",
            self.controller._Controller__transfer_exclude_patterns("sample-file", False),
        )

        time.sleep(0.05)
        entries = trace.snapshot()["entries"]
        self.assertEqual(1, len(entries))
        details = entries[0]["details"]
        self.assertFalse(details["exact_leaf_candidates_present"])
        self.assertTrue(details["configured_patterns_present"])
        self.assertFalse(details["exact_exclusions_serialized"])
        self.assertEqual(0, details["exact_exclusion_count"])
        self.assertTrue(details["serialized_exclusions_present"])
        self.assertEqual("configured_only", details["result"])

    def test_fractional_queue_trace_carries_opaque_operation_flow_id(self):
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        flow_id = self.controller._Controller__fractional_queue_flow_id("sample-file", 7)

        self.assertIsNotNone(flow_id)
        self.controller._Controller__record_fractional_queue_trace(
            "sample-file",
            "queue_dispatch",
            {
                "schema": "fractional_mtime_redownload.queue_dispatch.v2",
                "dispatch_mode": "sync_backend",
                "future_outcome": "not_applicable",
                "status_acknowledgement": "pending",
                "exclusions_present": False,
                "result": "submitted",
            },
            flow_id=flow_id,
        )

        time.sleep(0.05)
        entry = trace.snapshot()["entries"][0]
        self.assertEqual(flow_id, entry["flow_id"])
        self.assertNotIn("sample-file", entry["flow_id"])

    def test_fractional_queue_trace_is_silent_when_breadcrumbs_are_disabled(self):
        trace = BreadcrumbTraceCollector(lambda: False, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        self.controller._Controller__exclude_patterns = "*.nfo"
        self.controller._Controller__model_builder.get_trusted_final_leaf_paths.return_value = ("existing.bin",)

        self.controller._Controller__transfer_exclude_patterns("sample-directory", True)

        time.sleep(0.05)
        self.assertEqual([], trace.snapshot()["entries"])

    def test_fractional_queue_trace_does_not_evaluate_lazy_details_when_disabled(self):
        trace = BreadcrumbTraceCollector(lambda: False, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        factories = {
            event: MagicMock(side_effect=AssertionError("disabled trace built details"))
            for event in (
                "queue_dispatch",
                "startup_recovery_queue_dispatch",
                "queue_future_outcome",
                "queue_status_ack",
            )
        }

        for event, details_factory in factories.items():
            self.controller._Controller__record_fractional_queue_trace(
                "sample-directory",
                event,
                details_factory,
            )

        for details_factory in factories.values():
            details_factory.assert_not_called()

    def test_generic_breadcrumb_gate_skips_lazy_details_and_preserves_enabled_emission(self):
        disabled = BreadcrumbTraceCollector(
            lambda: True, max_entries=16, policy={"default": "off"},
        )
        self.controller._Controller__context.breadcrumb_trace = disabled
        details = MagicMock(side_effect=AssertionError("disabled breadcrumb built details"))
        self.controller._Controller__record_breadcrumb(
            stage="controller_test",
            message="controller_test",
            details=details,
        )
        details.assert_not_called()
        self.assertEqual([], disabled.snapshot()["entries"])

        enabled = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = enabled
        self.controller._Controller__record_breadcrumb(
            stage="controller_test",
            message="controller_test",
            details=lambda: {"sentinel": "emitted"},
        )
        entry = enabled.snapshot()["entries"][0]
        self.assertEqual("controller", entry["category"])
        self.assertEqual("emitted", entry["details"]["sentinel"])

    def test_publication_breadcrumb_gate_skips_filesystem_probes(self):
        disabled = BreadcrumbTraceCollector(
            lambda: True, max_entries=16,
            policy={"default": "info", "rules": {"final_move.publication": "off"}},
        )
        probe = MagicMock(side_effect=AssertionError("disabled publication probe"))
        tracker = _MoveMutationTracker("sample-file", disabled)
        with patch("controller.controller.os.path.lexists", probe):
            for phase in ("reserve_start", "temporary_reserved", "copy_start", "copy_complete"):
                tracker.record_publication(
                    phase, "copy", "temporary",
                    source_path="source", temporary_path="temporary",
                    destination_path="destination",
                )
        probe.assert_not_called()
        self.assertEqual([], disabled.snapshot()["entries"])

        enabled = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        tracker = _MoveMutationTracker("sample-file", enabled)
        tracker.record_publication("destination", "replace", "destination")
        self.assertEqual(1, len(enabled.snapshot()["entries"]))

    def test_fallback_publication_lifecycle_is_ordered_and_path_free_for_file_and_directory(self):
        variants = (
            ("sample-file.bin", False),
            ("sample-directory", True),
            ("sample-link", None),
        )
        for name, is_directory in variants:
            with self.subTest(name=name):
                trace = BreadcrumbTraceCollector(lambda: True, max_entries=64)
                with tempfile.TemporaryDirectory() as temp_dir:
                    staging = os.path.join(temp_dir, "staging")
                    final = os.path.join(temp_dir, "final")
                    os.mkdir(staging)
                    os.mkdir(final)
                    source = os.path.join(staging, name)
                    destination = os.path.join(final, name)
                    if is_directory:
                        os.mkdir(source)
                        Path(os.path.join(source, "example.bin")).write_bytes(b"payload")
                    elif is_directory is False:
                        Path(source).write_bytes(b"payload")
                    else:
                        try:
                            os.symlink("target.bin", source)
                        except (OSError, NotImplementedError) as error:
                            self.skipTest("symlink fixture unavailable: {}".format(error))

                    tracker = _MoveMutationTracker(name, trace)
                    with patch.object(
                            Controller, "_Controller__rename_no_replace",
                            side_effect=OSError(errno.EINVAL, "unsupported")):
                        Controller._Controller__publish_staging_no_replace(source, destination, tracker)

                entries = trace.query_events(
                    corr_id=opaque_trace_correlation(name),
                    category="final_move.publication",
                    order="asc",
                )["events"]
                phases = [entry["details"]["phase"] for entry in entries]
                lifecycle = [
                    "reserve_start", "temporary_reserved", "copy_start", "copy_complete",
                ]
                positions = [phases.index(phase) for phase in lifecycle]
                self.assertEqual(sorted(positions), positions)
                self.assertEqual("final_move.publication", entries[0]["category"])
                self.assertEqual("final_move_publication", entries[0]["stage"])
                self.assertEqual("info", entries[0]["level"])
                self.assertEqual(opaque_trace_correlation(name), entries[0]["corr_id"])
                for entry in entries:
                    self.assertNotIn(name, str(entry))
                    self.assertNotIn(source, str(entry))
                    self.assertNotIn(destination, str(entry))

    def test_transfer_exclusions_fail_closed_for_unknown_pair_with_retained_scan_snapshot(self):
        base_mtime_ns = 1_786_400_003_000_000_000
        builder = ModelBuilder()
        remote_root = SystemFile("release", 100, True)
        remote_root.path_pair_id = "pair-a"
        remote_root.add_child(SystemFile(
            "existing.mkv", 100, False, mtime_ns=base_mtime_ns + 100,
        ))
        local_root = SystemFile("release", 100, True)
        local_root.path_pair_id = "pair-a"
        local_root.add_child(SystemFile(
            "existing.mkv", 100, False, mtime_ns=base_mtime_ns,
        ))
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_unknown_local_path_pair_ids({"pair-a"})
        self.controller._Controller__model_builder = builder
        self.controller._Controller__exclude_patterns = "*.nfo"
        file_id = ModelFile.build_file_id("release", "pair-a")

        # The retained roots are real builder inputs, but an incomplete local
        # or joint scan must not become a typed exact exclusion.
        self.assertEqual("*.nfo", self.controller._Controller__transfer_exclude_patterns(file_id, True))

        builder.set_unknown_local_path_pair_ids(set())
        self.assertEqual(
            ["*.nfo", ExactPathExclusion("existing.mkv")],
            self.controller._Controller__transfer_exclude_patterns(file_id, True),
        )

    def test_transfer_exclusions_skip_unrepresentable_exact_path_without_losing_user_globs(self):
        self.controller._Controller__exclude_patterns = "*.nfo"
        self.controller._Controller__model_builder.get_trusted_final_leaf_paths.return_value = (
            "E06.mkv",
            "nested/bad\nname.mkv",
            "nested/tab\tname.mkv",
        )

        exclusions = self.controller._Controller__transfer_exclude_patterns("release", True)

        self.assertEqual(["*.nfo", ExactPathExclusion("E06.mkv")], exclusions)

    def test_process_commands_queue_passes_typed_exact_exclusions_without_string_joining(self):
        file = ModelFile("release", True)
        file.remote_size = 100
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__reconciled_local_path_pair_ids = {None}
        self.controller._Controller__reconciled_remote_path_pair_ids = {None}
        self.controller._Controller__exclude_patterns = "*.nfo"
        self.controller._Controller__model_builder.get_trusted_final_leaf_paths.return_value = (
            "E06.mkv",
        )
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "release",
            True,
            remote_base_dir_path=None,
            local_base_dir_path="/local/incomplete",
            exclude_patterns=["*.nfo", ExactPathExclusion("E06.mkv")],
        )

    def test_process_commands_queue_defers_manual_directory_until_path_pair_reconciles(self):
        file = ModelFile("release", True)
        file.path_pair_id = "pair-a"
        file.remote_size = 100
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote/a", local_path="/local/a")
        }
        self.controller._Controller__path_pair_staging_paths = {"pair-a": "/local/a/incomplete"}
        self.controller._Controller__reconciled_local_path_pair_ids = {"pair-a"}
        self.controller._Controller__reconciled_remote_path_pair_ids = {"pair-a"}
        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset({"pair-a"})

        blocked_callback = MagicMock()
        blocked = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        blocked.add_callback(blocked_callback)
        self.controller.queue_command(blocked)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_not_called()
        blocked_callback.on_failure.assert_not_called()
        blocked_callback.on_success.assert_not_called()

        self.controller._Controller__model_builder.unknown_local_path_pair_ids_snapshot.return_value = frozenset()
        self.controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
        self.controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
        self.controller._Controller__scan_authority_tokens = {
            "local": {"pair-a": ("local-test-session", 1)},
            "remote": {"pair-a": ("remote-test-session", 1)},
        }
        self.controller._Controller__model_builder.get_trusted_final_leaf_paths.return_value = ()
        admitted_callback = MagicMock()
        admitted = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        admitted.add_callback(admitted_callback)
        self.controller.queue_command(admitted)
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "release",
            True,
            remote_base_dir_path="/remote/a",
            local_base_dir_path="/local/a/incomplete",
        )
        blocked_callback.on_success.assert_called_once_with()
        admitted_callback.on_success.assert_called_once_with()
        blocked_callback.on_failure.assert_not_called()
        admitted_callback.on_failure.assert_not_called()

    def test_ambiguous_split_local_target_refuses_final_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "final")
            staging_root = os.path.join(temp_dir, "staging")
            os.makedirs(final_root)
            os.makedirs(staging_root)
            Path(os.path.join(final_root, "release")).write_bytes(b"final")
            Path(os.path.join(staging_root, "release")).write_bytes(b"staged")
            self.controller._Controller__legacy_local_path = final_root
            self.controller._Controller__staging_path = staging_root
            file = ModelFile("release", False)

            self.assertTrue(self.controller._Controller__has_ambiguous_split_local_target(file))

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_defers_when_lftp_temp_artifact_matches_path_pair_source(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_root = os.path.join(temp_dir, "movies")
            staging_root = os.path.join(final_root, "incomplete")
            source_file = os.path.join(staging_root, "movie.mkv")
            os.makedirs(staging_root)
            with open(source_file, "w", encoding="utf-8") as final_file:
                final_file.write("complete")
            with open(source_file + ".lftp", "w", encoding="utf-8") as temp_file:
                temp_file.write("partial")

            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(local_path=final_root)
            }
            self.controller._Controller__path_pair_staging_paths = {
                "movies": staging_root
            }

            result = self.controller._Controller__move_from_staging("movie.mkv", "movies")

        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
        self.controller.logger.warning.assert_called_once_with(
            "Deferring move of '%s' from staging '%s' to '%s': staging source still has an lftp temp artifact",
            "movie.mkv",
            staging_root,
            final_root,
        )
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_does_not_walk_symlink_source_tree(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            target_root = os.path.join(temp_dir, "target")
            final_root = os.path.join(temp_dir, "final")
            src_link = os.path.join(staging_root, "movie.mkv")
            target_source_tree = os.path.join(target_root, "movie.mkv")
            os.makedirs(staging_root)
            os.makedirs(final_root)
            os.makedirs(target_source_tree)
            with open(os.path.join(target_source_tree, "movie.mkv.lftp"), "w", encoding="utf-8") as temp_file:
                temp_file.write("partial")

            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            with patch("controller.controller.os.path.exists", side_effect=lambda path: path == src_link), \
                    patch("controller.controller.os.path.islink", side_effect=lambda path: path == src_link), \
                    patch(
                        "controller.controller.os.path.realpath",
                        side_effect=lambda path: target_source_tree if path == src_link else path,
                    ), \
                    patch(
                        "controller.controller.os.walk",
                        side_effect=AssertionError("os.walk should not be called for symlink roots"),
                    ) as walk:
                result = self.controller._Controller__move_from_staging("movie.mkv")

        walk.assert_not_called()
        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_rejects_absolute_and_parent_traversal(self, move):
        for unsafe_name in ("/etc/passwd", "C:\\Windows\\system.ini", "\\\\server\\share\\file", "../escape", "nested/../../escape"):
            with self.subTest(name=unsafe_name):
                result = self.controller._Controller__move_from_staging(unsafe_name)
                self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
        move.assert_not_called()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_accepts_contained_nested_relative_path(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source = os.path.join(staging_root, "nested", "movie.mkv")
            destination = os.path.join(final_root, "nested", "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.makedirs(os.path.dirname(destination))
            with open(source, "w", encoding="utf-8") as handle: handle.write("complete")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("nested/movie.mkv")

        move.assert_called_once_with(source, destination, ANY)
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_rejects_source_destination_and_parent_symlinks(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            outside = os.path.join(temp_dir, "outside")
            os.makedirs(staging_root); os.makedirs(final_root); os.makedirs(outside)
            outside_file = os.path.join(outside, "movie.mkv")
            with open(outside_file, "w", encoding="utf-8") as handle: handle.write("outside")
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            source_link = os.path.join(staging_root, "source-link.mkv")
            destination_link = os.path.join(final_root, "destination-link.mkv")
            parent_link = os.path.join(staging_root, "linked-parent")
            real_lstat = os.lstat
            cases = (
                ("source-link.mkv", source_link),
                ("destination-link.mkv", destination_link),
                ("linked-parent/movie.mkv", parent_link),
            )
            for unsafe_name, symlink_path in cases:
                with self.subTest(name=unsafe_name):
                    def lstat(path, *, dir_fd=None):
                        if os.path.normcase(path) == os.path.normcase(symlink_path):
                            return SimpleNamespace(st_mode=stat.S_IFLNK)
                        return real_lstat(path, dir_fd=dir_fd)
                    with patch("controller.controller.os.lstat", side_effect=lstat):
                        result = self.controller._Controller__move_from_staging(unsafe_name)
                    self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)

        move.assert_not_called()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_logs_target_archive_trace(self, move):
        self.controller._Controller__target_archive_trace_file_id = "movie.mkv"
        trace_logger = self.controller._Controller__target_archive_trace_logger

        with patch("controller.controller.os.path.exists", return_value=True), \
                patch.object(trace_logger, "info") as trace_info:
            self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with(
            os.path.normpath(os.path.join("/local/incomplete", "movie.mkv")),
            os.path.normpath(os.path.join("/local", "movie.mkv")), ANY,
        )
        self.assertEqual(2, trace_info.call_count)
        attempt_payload = json.loads(trace_info.call_args_list[0][0][1])
        result_payload = json.loads(trace_info.call_args_list[1][0][1])
        self.assertEqual("move_from_staging_attempt", attempt_payload["event"])
        self.assertEqual("moved", result_payload["result"])

    @patch.object(Controller, "_Controller__publish_staging_no_replace", side_effect=OSError("permission denied"))
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_reports_move_failure_without_forcing_scan(self, _, move):
        result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with(
            os.path.normpath(os.path.join("/local/incomplete", "movie.mkv")),
            os.path.normpath(os.path.join("/local", "movie.mkv")), ANY,
        )
        self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
        self.controller.logger.warning.assert_called_once_with(
            "Failed to move '%s' from staging '%s' to '%s': %s",
            "movie.mkv",
            "/local/incomplete",
            "/local",
            move.side_effect
        )
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    @patch("controller.controller.os.path.exists", side_effect=[False, False])
    def test_move_from_staging_reports_missing_source_without_destination_as_failure(self, _, move):
        result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
        self.controller.logger.warning.assert_called_once_with(
            "Failed to move '%s' from staging '%s' to '%s': source does not exist",
            "movie.mkv",
            "/local/incomplete",
            "/local",
        )
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    @patch("controller.controller.os.path.exists", side_effect=[False, True])
    def test_move_from_staging_treats_missing_source_with_destination_as_settled(self, _, move):
        result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    @patch.object(Controller, "_Controller__publish_staging_no_replace")
    def test_move_from_staging_reports_missing_move_root_as_failure(self, move):
        self.controller._Controller__staging_path = ""

        result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
        self.controller.logger.warning.assert_called_once_with(
            "Failed to move '%s' from staging to final path: missing move root "
            "(path_pair_id=%s, staging_path=%s, final_path=%s)",
            "movie.mkv",
            None,
            "",
            "/local",
        )
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    def _prepare_terminal_move_command(self, file_id=None, path_pair_id=None):
        model = Model()
        model.set_base_logger(self.controller.logger)
        file = ModelFile("movie.mkv", False)
        file.path_pair_id = path_pair_id
        file.local_size = 100
        file.remote_size = 100
        file.state = ModelFile.State.MOVE_FAILED
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__persist.move_failure_counts = {file.file_id: 4}
        callback = MagicMock()
        command = Controller.Command(
            Controller.Command.Action.RETRY_MOVE,
            file.file_id if file_id is None else file_id,
        )
        command.add_callback(callback)
        return file, command, callback

    def test_move_attempt_reservation_is_canonical_and_pair_scoped(self):
        movies_id = ModelFile.build_file_id("same.mkv", "movies")
        tv_id = ModelFile.build_file_id("same.mkv", "tv")

        self.assertTrue(self.controller._reserve_move_attempt(movies_id))
        self.assertFalse(self.controller._reserve_move_attempt(movies_id))
        self.assertTrue(self.controller._reserve_move_attempt(tv_id))

        self.controller._release_move_attempt(movies_id)
        self.controller._release_move_attempt(tv_id)
        self.assertTrue(self.controller._reserve_move_attempt(movies_id))
        self.controller._release_move_attempt(movies_id)

    def test_manual_retry_move_success_clears_terminal_marker(self):
        file, command, callback = self._prepare_terminal_move_command(path_pair_id="movies")
        self.controller._Controller__pending_completion_progress_floors = {
            file.file_id: (99, 99),
        }
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        self.assertNotIn(file.file_id, self.controller._Controller__persist.move_failure_counts)
        self.assertIn(file.file_id, self.controller._Controller__persist.downloaded_file_names)
        self.assertIn(file.file_id, self.controller._Controller__persist.final_move_succeeded_file_names)
        self.assertIn(file.file_id, self.controller._Controller__persist.downloaded_timestamps)
        self.assertIn(file.file_id, self.controller._Controller__successful_final_move_handoff_file_ids)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_progress_floors)
        self.controller._Controller__model_builder.evict_active_file_ids.assert_called_once_with({file.file_id})
        self.controller._Controller__active_scan_process.force_scan.assert_called_once_with()

    def test_implicit_staging_hides_successful_move_from_model(self):
        file_id = ModelFile.build_file_id("movie.mkv", None)
        self.controller._Controller__persist.final_move_succeeded_file_names = {file_id}

        self.controller._sync_final_move_succeeded_files_to_model()

        self.controller._Controller__model_builder.set_final_move_succeeded_files.assert_called_once_with(set())

    def test_explicit_single_path_staging_exposes_successful_move_to_model(self):
        file_id = ModelFile.build_file_id("movie.mkv", None)
        self.controller._Controller__explicit_staging_path_configured = True
        self.controller._Controller__persist.final_move_succeeded_file_names = {file_id}

        self.controller._sync_final_move_succeeded_files_to_model()

        self.controller._Controller__model_builder.set_final_move_succeeded_files.assert_called_once_with({file_id})

    def test_explicit_path_pair_staging_exposes_successful_move_to_model(self):
        file_id = ModelFile.build_file_id("movie.mkv", "movies")
        self.controller._Controller__explicit_staging_path_configured = True
        self.controller._Controller__path_pairs_by_id = {"movies": MagicMock()}
        self.controller._Controller__persist.final_move_succeeded_file_names = {file_id}

        self.controller._sync_final_move_succeeded_files_to_model()

        self.controller._Controller__model_builder.set_final_move_succeeded_files.assert_called_once_with({file_id})

    def test_manual_retry_already_completed_does_not_earn_success_marker(self):
        file, command, callback = self._prepare_terminal_move_command()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.ALREADY_COMPLETED
        )

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        self.assertIn(file.file_id, self.controller._Controller__persist.downloaded_file_names)
        self.assertNotIn(file.file_id, self.controller._Controller__persist.final_move_succeeded_file_names)
        self.assertIn(file.file_id, self.controller._Controller__persist.downloaded_timestamps)
        self.controller._Controller__active_scan_process.force_scan.assert_not_called()

    def test_new_queue_clears_terminal_move_lifecycle(self):
        file, _, _ = self._prepare_terminal_move_command()
        file.state = ModelFile.State.MOVE_FAILED
        self.controller._Controller__pending_completion_file_names = {
            (file.name, file.path_pair_id, file.path_pair_name)
        }
        self.controller._Controller__pending_completion_authority_rebuild_ids = {file.file_id}
        self.controller._Controller__pending_completion_progress_floors = {file.file_id: (100, 1000)}
        self.controller._Controller__pending_completion_progress_floor_overlay_ids = {file.file_id}
        self.controller._Controller__pending_completion_publications = {}
        self.controller._Controller__move_retry_due[file.file_id] = datetime.now() + timedelta(seconds=30)
        self.controller._Controller__deferred_move_file_ids.add(file.file_id)
        self.controller._Controller__move_attempt_reservations.add(file.file_id)
        self.controller._Controller__persist.final_move_succeeded_file_names.add(file.file_id)
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        command.add_callback(callback)

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        self.assertNotIn(file.file_id, self.controller._Controller__persist.move_failure_counts)
        self.assertNotIn(file.file_id, self.controller._Controller__move_retry_due)
        self.assertNotIn(file.file_id, self.controller._Controller__deferred_move_file_ids)
        self.assertNotIn(file.file_id, self.controller._Controller__move_attempt_reservations)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_authority_rebuild_ids)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_progress_floors)
        self.assertNotIn(file.file_id, self.controller._Controller__pending_completion_progress_floor_overlay_ids)
        self.assertNotIn(file.file_id, self.controller._Controller__persist.final_move_succeeded_file_names)
        self.controller._Controller__model_builder.set_move_failed_files.assert_called()

    def test_manual_retry_move_failure_and_deferred_remain_terminal(self):
        for result, expected_code in (
            (Controller.MoveFromStagingResult.FAILED, 500),
            (Controller.MoveFromStagingResult.DEFERRED, 409),
        ):
            with self.subTest(result=result):
                file, command, callback = self._prepare_terminal_move_command()
                self.controller._Controller__move_from_staging = MagicMock(return_value=result)

                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

                self.assertEqual(4, self.controller._Controller__persist.move_failure_counts[file.file_id])
                callback.on_failure.assert_called_once()
                self.assertEqual(expected_code, callback.on_failure.call_args.args[1])
                callback.on_success.assert_not_called()
                self.assertEqual({}, self.controller._Controller__persist.downloaded_timestamps)

    def test_manual_retry_move_rejects_nonterminal_and_reserved_identity(self):
        file, command, callback = self._prepare_terminal_move_command()
        file.state = ModelFile.State.DOWNLOADED
        self.controller.queue_command(command)
        self.controller._Controller__process_commands()
        self.assertEqual(409, callback.on_failure.call_args.args[1])

        file, command, callback = self._prepare_terminal_move_command()
        self.assertTrue(self.controller._reserve_move_attempt(file.file_id))
        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
        finally:
            self.controller._release_move_attempt(file.file_id)
        self.assertEqual(409, callback.on_failure.call_args.args[1])

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_automatic_move_uses_initial_plus_three_retries_and_backoff(self, diff_models):
        completion_entry = ("movie.mkv", None, None)
        active = ModelFile("movie.mkv", False)
        active.remote_size = 100
        active.local_size = 90
        active.state = ModelFile.State.DOWNLOADING
        current = Model()
        current.set_base_logger(self.controller.logger)
        current.add_file(active)

        terminal = ModelFile("movie.mkv", False)
        terminal.remote_size = 100
        terminal.local_size = 100
        terminal.state = ModelFile.State.DOWNLOADED
        rebuilt = Model()
        rebuilt.set_base_logger(self.controller.logger)
        rebuilt.add_file(terminal)

        self.controller._Controller__model = current
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = rebuilt
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__prev_downloading_file_names = {completion_entry}
        self._authorize_pending_move()
        self.controller._Controller__move_from_staging = MagicMock(
            side_effect=[Controller.MoveFromStagingResult.DEFERRED] +
                        [Controller.MoveFromStagingResult.FAILED] * 4
        )
        diff_models.side_effect = [
            [SimpleNamespace(change=ModelDiff.Change.UPDATED, old_file=active, new_file=terminal)],
            *([[]] * 12),
        ]

        self.controller._Controller__update_model()
        self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)

        self.controller._Controller__update_model()
        self.assertEqual(1, self.controller._Controller__persist.move_failure_counts[terminal.file_id])
        self.assertEqual(2, self.controller._Controller__move_from_staging.call_count)

        observed_delays = []
        for expected_count in range(1, 5):
            if expected_count > 1:
                self.controller._Controller__move_retry_due[terminal.file_id] = datetime.now() - timedelta(seconds=1)
            self.controller._Controller__update_model()
            self.assertEqual(
                expected_count,
                self.controller._Controller__persist.move_failure_counts[terminal.file_id],
            )
            if expected_count < 4:
                due = self.controller._Controller__move_retry_due[terminal.file_id]
                observed_delays.append(round((due - datetime.now()).total_seconds()))
                # The unchanged future timestamp is stable: it must not
                # consume another retry before the scheduled authority edge.
                self.controller._Controller__update_model()
                self.assertEqual(
                    expected_count,
                    self.controller._Controller__persist.move_failure_counts[terminal.file_id],
                )

        self.assertEqual([2, 10, 30], observed_delays)
        self.assertEqual(5, self.controller._Controller__move_from_staging.call_count)
        self.assertNotIn(terminal.file_id, self.controller._Controller__move_retry_due)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_automatic_move_conflict_stays_pending_and_consumes_failure_budget(self, diff_models):
        completion_entry = ("movie.mkv", None, None)
        active = ModelFile("movie.mkv", False)
        active.remote_size = 100; active.local_size = 90; active.state = ModelFile.State.DOWNLOADING
        terminal = ModelFile("movie.mkv", False)
        terminal.remote_size = 100; terminal.local_size = 100; terminal.state = ModelFile.State.DOWNLOADED
        current = Model(); current.set_base_logger(self.controller.logger); current.add_file(active)
        rebuilt = Model(); rebuilt.set_base_logger(self.controller.logger); rebuilt.add_file(terminal)
        self.controller._Controller__model = current
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = rebuilt
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__prev_downloading_file_names = {completion_entry}
        self._authorize_pending_move()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.CONFLICT
        )
        diff_models.side_effect = [
            [SimpleNamespace(change=ModelDiff.Change.UPDATED, old_file=active, new_file=terminal)],
            [],
        ]

        self.controller._Controller__update_model()
        self.assertEqual(1, self.controller._Controller__persist.move_failure_counts[terminal.file_id])
        self.assertNotIn(terminal.file_id, self.controller._Controller__persist.downloaded_file_names)
        self.assertIn(completion_entry, self.controller._Controller__pending_completion_file_names)

        self.controller._Controller__move_retry_due[terminal.file_id] = datetime.now() - timedelta(seconds=1)
        self.controller._Controller__update_model()
        self.assertEqual(2, self.controller._Controller__persist.move_failure_counts[terminal.file_id])
        self.assertNotIn(terminal.file_id, self.controller._Controller__persist.downloaded_file_names)

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_automatic_move_deferred_then_completed_does_not_consume_failure_budget(self, diff_models):
        completion_entry = ("movie.mkv", None, None)
        active = ModelFile("movie.mkv", False)
        active.remote_size = 100; active.local_size = 90; active.state = ModelFile.State.DOWNLOADING
        terminal = ModelFile("movie.mkv", False)
        terminal.remote_size = 100; terminal.local_size = 100; terminal.state = ModelFile.State.DOWNLOADED
        current = Model(); current.set_base_logger(self.controller.logger); current.add_file(active)
        rebuilt = Model(); rebuilt.set_base_logger(self.controller.logger); rebuilt.add_file(terminal)
        self.controller._Controller__model = current
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = rebuilt
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__prev_downloading_file_names = {completion_entry}
        self._authorize_pending_move()
        self.controller._Controller__move_from_staging = MagicMock(side_effect=[
            Controller.MoveFromStagingResult.DEFERRED,
            Controller.MoveFromStagingResult.COMPLETED,
        ])
        diff_models.side_effect = [
            [SimpleNamespace(change=ModelDiff.Change.UPDATED, old_file=active, new_file=terminal)],
            [],
        ]

        self.controller._Controller__update_model()

        self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)
        self.assertIn(completion_entry, self.controller._Controller__pending_completion_file_names)
        self.controller._Controller__update_model()
        self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)
        self.assertNotIn(completion_entry, self.controller._Controller__pending_completion_file_names)
        self.assertIn(terminal.file_id, self.controller._Controller__persist.downloaded_file_names)

    def test_pending_move_retry_waits_for_clean_split_root_coverage(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 20, True)
        remote_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("E07.mkv", 10, False, mtime_ns=mtime_ns))
        local_root = SystemFile("release", 20, True)
        local_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        local_root.add_child(SystemFile("E07.mkv", 10, False, is_staging=True, mtime_ns=mtime_ns))
        active_with_extra = SystemFile("release", 15, True)
        active_with_extra.add_child(SystemFile("E07.mkv", 10, False, mtime_ns=mtime_ns))
        active_with_extra.add_child(SystemFile("obsolete.tmp", 5, False))

        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_active_files([active_with_extra])
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = builder.build_model()
        self.controller._Controller__model_lock = threading.RLock()
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__reconciled_local_path_pair_ids = {None}
        self.controller._Controller__reconciled_remote_path_pair_ids = {None}
        self.controller._Controller__lftp_idle_status_authoritative = True
        self.controller._Controller__lftp_status_poll_retry_active = False
        pending_entry = ("release", None, None)
        release_id = ModelFile.build_file_id(*pending_entry[:2])
        self.controller._Controller__pending_completion_file_names = {pending_entry}
        self.controller._Controller__persist.move_failure_counts = {release_id: 1}
        self.controller._Controller__deferred_move_file_ids.add(release_id)
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )

        self.controller._Controller__update_model()

        self.controller._Controller__move_from_staging.assert_not_called()
        self.assertEqual({pending_entry}, self.controller._Controller__pending_completion_file_names)
        self.assertEqual({release_id: 1}, self.controller._Controller__persist.move_failure_counts)
        self.assertIn(release_id, self.controller._Controller__deferred_move_file_ids)
        self.assertNotIn(release_id, self.controller._Controller__persist.downloaded_file_names)

        active_clean = SystemFile("release", 10, True)
        active_clean.add_child(SystemFile("E07.mkv", 10, False, mtime_ns=mtime_ns))
        builder.set_active_files([active_clean])
        builder.request_rebuild()
        self.controller._Controller__update_model()
        self.controller._Controller__move_from_staging.assert_called_once_with("release", None)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)
        self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)
        self.assertNotIn(release_id, self.controller._Controller__deferred_move_file_ids)
        self.assertIn(release_id, self.controller._Controller__persist.downloaded_file_names)

    def test_model_updater_waits_for_fresh_healthy_empty_status_before_terminalizing_collision(self):
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

        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_active_files([active_root])
        previous = Model()
        previous.set_base_logger(self.controller.logger)
        prior_release = ModelFile("release", True)
        prior_release.remote_size = 10
        prior_release.local_size = 10
        prior_release.transferred_size = 10
        prior_release.download_progress = 99
        prior_release.state = ModelFile.State.DOWNLOADING
        previous.add_file(prior_release)
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = previous
        self.controller._Controller__model_lock = threading.RLock()
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = False
        self.controller._Controller__successful_final_move_handoff_file_ids = set()
        self.controller._Controller__pending_completion_progress_floors = {}
        pending_entry = ("release", None, None)
        self.controller._Controller__pending_completion_file_names = {pending_entry}

        ModelUpdater(self.controller).update()

        release = self.controller._Controller__model.get_file("release")
        self.assertNotEqual(ModelFile.State.MOVE_FAILED, release.state)
        self.assertGreaterEqual(release.download_progress, 99)
        self.assertNotIn(release.file_id, self.controller._Controller__persist.move_failure_counts)
        self.assertIn(pending_entry, self.controller._Controller__pending_completion_file_names)
        self.assertNotIn(release.file_id, self.controller._Controller__persist.downloaded_file_names)
        self.assertNotIn(release.file_id, self.controller._Controller__move_retry_due)

        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        self.controller._has_active_collision_comparison = MagicMock(return_value=True)
        ModelUpdater(self.controller).update()

        self.assertNotIn(release.file_id, self.controller._Controller__persist.move_failure_counts)
        self.assertNotEqual(ModelFile.State.MOVE_FAILED, self.controller._Controller__model.get_file("release").state)
        self.assertIn(pending_entry, self.controller._Controller__pending_completion_file_names)

        self.controller._has_active_collision_comparison.return_value = False
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        ModelUpdater(self.controller).update()

        self.assertEqual(Controller._Controller__MAX_MOVE_FAILURES,
                         self.controller._Controller__persist.move_failure_counts[release.file_id])
        terminal_release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.MOVE_FAILED, terminal_release.state)
        self.assertEqual(0, terminal_release.transferred_size)
        self.assertIsNone(terminal_release.download_progress)
        self.assertIsNone(terminal_release.downloading_speed)
        self.assertIsNone(terminal_release.eta)
        self.assertNotIn(release.file_id, self.controller._Controller__pending_completion_progress_floors)

        ModelUpdater(self.controller).update()

        terminal_release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.MOVE_FAILED, terminal_release.state)
        self.assertEqual(0, terminal_release.transferred_size)
        self.assertIsNone(terminal_release.download_progress)
        self.assertIsNone(terminal_release.downloading_speed)
        self.assertIsNone(terminal_release.eta)

    def test_model_updater_terminalizes_pending_collision_after_healthy_poll_without_other_model_diff(self):
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

        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_active_files([active_root])
        previous = builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, previous.get_file("release").state)
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = previous
        self.controller._Controller__model_lock = threading.RLock()
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = False
        self.controller._Controller__successful_final_move_handoff_file_ids = set()
        self.controller._Controller__pending_completion_progress_floors = {}
        pending_entry = ("release", None, None)
        self.controller._Controller__pending_completion_file_names = {pending_entry}

        ModelUpdater(self.controller).update()

        release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.DEFAULT, release.state)
        self.assertNotIn(release.file_id, self.controller._Controller__persist.move_failure_counts)
        self.assertIn(pending_entry, self.controller._Controller__pending_completion_file_names)

        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        ModelUpdater(self.controller).update()

        self.assertEqual(Controller._Controller__MAX_MOVE_FAILURES,
                         self.controller._Controller__persist.move_failure_counts[release.file_id])
        terminal_release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.MOVE_FAILED, terminal_release.state)
        self.assertEqual(0, terminal_release.transferred_size)
        self.assertIsNone(terminal_release.download_progress)
        self.assertIsNone(terminal_release.downloading_speed)
        self.assertIsNone(terminal_release.eta)
        self.assertNotIn(release.file_id, self.controller._Controller__pending_completion_progress_floors)

        ModelUpdater(self.controller).update()

        terminal_release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.MOVE_FAILED, terminal_release.state)
        self.assertEqual(0, terminal_release.transferred_size)
        self.assertIsNone(terminal_release.download_progress)
        self.assertIsNone(terminal_release.downloading_speed)
        self.assertIsNone(terminal_release.eta)

    def test_model_updater_terminalizes_nonpending_cached_collision_after_fresh_healthy_poll(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
        local_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
        local_root.has_staging_collision = True
        active_root = SystemFile("release", 10, False, mtime_ns=mtime_ns + 1_000_000_000)
        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_active_files([active_root])
        previous = builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, previous.get_file("release").state)
        self.assertEqual({"release"}, builder.get_unresolved_staging_collision_file_ids())
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = previous
        self.controller._Controller__model_lock = threading.RLock()
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = False
        self.controller._Controller__successful_final_move_handoff_file_ids = set()
        release_id = ModelFile.build_file_id("release", None)
        self.controller._Controller__pending_completion_progress_floors = {release_id: (99, 10)}

        ModelUpdater(self.controller).update()

        release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.DEFAULT, release.state)
        self.assertNotIn(release_id, self.controller._Controller__persist.move_failure_counts)

        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
        ModelUpdater(self.controller).update()

        terminal_release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.MOVE_FAILED, terminal_release.state)
        self.assertIsNone(terminal_release.transferred_size)
        self.assertIsNone(terminal_release.download_progress)
        self.assertIsNone(terminal_release.downloading_speed)
        self.assertIsNone(terminal_release.eta)
        self.assertNotIn(release_id, self.controller._Controller__pending_completion_progress_floors)
        self.assertIn(("release", None, None), self.controller._Controller__pending_completion_file_names)

        ModelUpdater(self.controller).update()

        terminal_release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.MOVE_FAILED, terminal_release.state)
        self.assertIsNone(terminal_release.download_progress)
        self.assertIsNone(terminal_release.downloading_speed)
        self.assertIsNone(terminal_release.eta)

    def test_model_updater_fresh_idle_poll_rebuilds_cached_complete_collision_once(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
        clean_local_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
        collided_local_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
        collided_local_root.has_staging_collision = True
        active_root = SystemFile("release", 10, False, mtime_ns=mtime_ns + 1_000_000_000)
        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([clean_local_root])
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = builder.build_model()
        self.controller._Controller__model_lock = threading.RLock()
        builder.set_local_files([collided_local_root])
        builder.set_active_files([active_root])
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__successful_final_move_handoff_file_ids = set()
        self.controller._Controller__pending_completion_progress_floors = {}
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)

        with patch.object(builder, "build_model", wraps=builder.build_model) as build_model:
            ModelUpdater(self.controller).update()

            self.assertEqual(1, build_model.call_count)
            self.assertEqual({"release"}, builder.get_terminalizable_staging_collision_file_ids())
            self.assertEqual(ModelFile.State.MOVE_FAILED, self.controller._Controller__model.get_file("release").state)

            self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
            ModelUpdater(self.controller).update()

            terminal_release = self.controller._Controller__model.get_file("release")
            self.assertEqual(1, build_model.call_count)
            self.assertEqual(ModelFile.State.MOVE_FAILED, terminal_release.state)
            self.assertIsNone(terminal_release.download_progress)
            self.assertIsNone(terminal_release.downloading_speed)
            self.assertIsNone(terminal_release.eta)

            ModelUpdater(self.controller).update()
            self.assertEqual(1, build_model.call_count)
            ModelUpdater(self.controller).update()
            self.assertEqual(1, build_model.call_count)

    def test_model_updater_unhealthy_then_fresh_empty_poll_without_collision_stays_cached(self):
        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_local_files([])
        builder.set_remote_files([])
        builder.set_active_files([])
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = builder.build_model()
        self.controller._Controller__model_lock = threading.RLock()
        for process in (
            self.controller._Controller__remote_scan_process,
            self.controller._Controller__local_scan_process,
            self.controller._Controller__active_scan_process,
        ):
            process.pop_latest_result.return_value = None
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = None
        self.controller._Controller__extract_process.pop_completed.return_value = []
        self.controller._Controller__extract_process.pop_failed.return_value = []
        self.controller._Controller__validate_process.pop_latest_statuses.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = False
        self.controller._Controller__next_lftp_status_poll_at = None

        with patch.object(builder, "build_model", wraps=builder.build_model) as build_model:
            ModelUpdater(self.controller).update()
            self.controller._Controller__lftp.last_status_poll_healthy = True
            self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
            ModelUpdater(self.controller).update()
            self.assertEqual(set(), builder.get_terminalizable_staging_collision_file_ids())
            self.assertEqual(0, build_model.call_count)

    def test_model_updater_due_move_failure_rebuilds_once_until_due_edge_rearms(self):
        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        # The durable marker is actionable only while current builder sources
        # still prove the canonical retry root exists and is complete.
        retry_root = SystemFile("retry", 1, False)
        builder.set_local_files([retry_root])
        builder.set_remote_files([retry_root])
        builder.set_active_files([])
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = builder.build_model()
        self.controller._Controller__model_lock = threading.RLock()
        for process in (
            self.controller._Controller__remote_scan_process,
            self.controller._Controller__local_scan_process,
            self.controller._Controller__active_scan_process,
        ):
            process.pop_latest_result.return_value = None
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = None
        self.controller._Controller__extract_process.pop_completed.return_value = []
        self.controller._Controller__extract_process.pop_failed.return_value = []
        self.controller._Controller__validate_process.pop_latest_statuses.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
        self.controller._Controller__persist.move_failure_counts = {"retry": 1}

        with patch.object(builder, "build_model", wraps=builder.build_model) as build_model:
            ModelUpdater(self.controller).update()
            ModelUpdater(self.controller).update()
            self.assertEqual(1, build_model.call_count)
            self.controller._Controller__move_retry_due["retry"] = datetime.now() + timedelta(seconds=10)
            ModelUpdater(self.controller).update()
            self.controller._Controller__move_retry_due["retry"] = datetime.now() - timedelta(seconds=1)
            ModelUpdater(self.controller).update()
            self.assertEqual(2, build_model.call_count)

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_model_updater_retries_deferred_pending_after_fresh_healthy_poll_without_model_diff(
            self, diff_models):
        for initial_count in (0, 1):
            with self.subTest(initial_count=initial_count):
                # Keep each marker variant independent; this exercises the
                # count-zero deferred path as well as a durable nonzero retry.
                self.setUp()
                mtime_ns = 1786400003000000000
                remote_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
                local_root = SystemFile("release", 10, False, is_staging=True, mtime_ns=mtime_ns)
                active_root = SystemFile("release", 10, False, mtime_ns=mtime_ns)
                builder = ModelBuilder()
                builder.set_base_logger(self.controller.logger)
                builder.set_remote_files([remote_root])
                builder.set_local_files([local_root])
                builder.set_active_files([active_root])
                self.controller._Controller__model_builder = builder
                self.controller._Controller__model = builder.build_model()
                self.controller._Controller__model_lock = threading.RLock()
                self.controller._Controller__lftp.status.return_value = []
                self.controller._Controller__lftp.last_status_poll_healthy = False
                self.controller._Controller__reconciled_local_path_pair_ids = {None}
                self.controller._Controller__reconciled_remote_path_pair_ids = {None}
                self.controller._Controller__lftp_idle_status_authoritative = True
                self.controller._Controller__lftp_status_poll_retry_active = False
                self.controller._Controller__next_lftp_status_poll_at = None
                pending_entry = ("release", None, None)
                release_id = ModelFile.build_file_id(*pending_entry[:2])
                self.controller._Controller__pending_completion_file_names = {pending_entry}
                self.controller._Controller__persist.move_failure_counts = {
                    release_id: initial_count,
                }
                self.controller._Controller__deferred_move_file_ids = {release_id}
                self.controller._Controller__move_from_staging = MagicMock(
                    side_effect=[
                        Controller.MoveFromStagingResult.DEFERRED,
                        Controller.MoveFromStagingResult.DEFERRED,
                        Controller.MoveFromStagingResult.COMPLETED,
                    ]
                )

                # The first attempt is deliberately driven by a rebuild with
                # no model diff.  The second rebuild must come only from the
                # unhealthy -> fresh-healthy status edge.
                builder.request_rebuild()
                ModelUpdater(self.controller).update()
                self.controller._Controller__move_from_staging.assert_called_once_with(
                    "release", None,
                )
                self.assertIn(pending_entry, self.controller._Controller__pending_completion_file_names)

                self.controller._Controller__lftp.last_status_poll_healthy = True
                self.controller._Controller__lftp_status_poll_retry_active = True
                self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
                ModelUpdater(self.controller).update()

                self.assertEqual(2, self.controller._Controller__move_from_staging.call_count)
                self.assertIn(pending_entry, self.controller._Controller__pending_completion_file_names)

                # A tick without a new retry or authority edge keeps the
                # same deferred transaction pending.
                self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(seconds=10)
                ModelUpdater(self.controller).update()
                self.assertEqual(2, self.controller._Controller__move_from_staging.call_count)

                # A meaningful explicit rebuild still permits the pending
                # move to succeed, which clears the lifecycle marker and
                # resets both edge gates.
                builder.request_rebuild()
                ModelUpdater(self.controller).update()

                self.assertEqual(3, self.controller._Controller__move_from_staging.call_count)
                self.assertNotIn(pending_entry, self.controller._Controller__pending_completion_file_names)
                self.assertNotIn(release_id, self.controller._Controller__persist.move_failure_counts)
                self.assertNotIn(release_id, self.controller._Controller__deferred_move_file_ids)

    def test_model_updater_does_not_trigger_for_collision_with_partial_remote_leaf_and_active_extra(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 20, True)
        remote_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(SystemFile("E07.mkv", 10, False, mtime_ns=mtime_ns))
        clean_local_root = SystemFile("release", 20, True)
        clean_local_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns))
        clean_local_root.add_child(SystemFile("E07.mkv", 10, False, mtime_ns=mtime_ns))
        collided_local_root = SystemFile("release", 20, True)
        collided_e06 = SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns)
        collided_e06.has_staging_collision = True
        collided_local_root.add_child(collided_e06)
        collided_local_root.add_child(SystemFile("E07.mkv", 5, False, mtime_ns=mtime_ns))
        active_root = SystemFile("release", 20, True)
        active_root.add_child(SystemFile("E06.mkv", 10, False, mtime_ns=mtime_ns + 1_000_000_000))
        active_root.add_child(SystemFile("E07.mkv", 5, False, mtime_ns=mtime_ns))
        active_root.add_child(SystemFile("obsolete.tmp", 5, False, mtime_ns=mtime_ns))
        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([clean_local_root])
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = builder.build_model()
        self.controller._Controller__model_lock = threading.RLock()
        builder.set_local_files([collided_local_root])
        builder.set_active_files([active_root])
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)

        with patch.object(builder, "build_model", wraps=builder.build_model) as build_model:
            ModelUpdater(self.controller).update()

            self.assertEqual(1, build_model.call_count)
            self.assertEqual({"release"}, builder.get_unresolved_staging_collision_file_ids())
            self.assertEqual(set(), builder.get_terminalizable_staging_collision_file_ids())

            self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
            ModelUpdater(self.controller).update()
            self.controller._Controller__next_lftp_status_poll_at = datetime.now() - timedelta(seconds=1)
            ModelUpdater(self.controller).update()

            self.assertEqual(1, build_model.call_count)
            self.assertEqual(ModelFile.State.DEFAULT, self.controller._Controller__model.get_file("release").state)
            self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)

    def test_model_updater_does_not_terminalize_explicitly_stopped_collision(self):
        mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 10, True)
        remote_root.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns))
        local_root = SystemFile("release", 10, True)
        local_leaf = SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns)
        local_leaf.has_staging_collision = True
        local_root.add_child(local_leaf)
        active_root = SystemFile("release", 10, True)
        active_root.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns + 1_000_000_000))

        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_active_files([active_root])
        self.controller._Controller__model_builder = builder
        self.controller._Controller__model = builder.build_model()
        self.controller._Controller__model_lock = threading.RLock()
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__persist.stopped_file_names = {"release"}
        pending_entry = ("release", None, None)
        self.controller._Controller__pending_completion_file_names = {pending_entry}

        ModelUpdater(self.controller).update()

        release = self.controller._Controller__model.get_file("release")
        self.assertEqual(ModelFile.State.DEFAULT, release.state)
        self.assertIn(release.file_id, self.controller._Controller__persist.stopped_file_names)
        self.assertNotIn(release.file_id, self.controller._Controller__persist.move_failure_counts)
        self.assertIn(pending_entry, self.controller._Controller__pending_completion_file_names)

    def test_manual_retry_move_preserves_stale_equally_timestamped_collision_without_remote_proof(self):
        remote_mtime_ns = 1786400003000000000
        stale_mtime_ns = remote_mtime_ns - 1_000_000_000
        remote_root = SystemFile("release", 10, True)
        remote_leaf = SystemFile("episode.mkv", 10, False, mtime_ns=remote_mtime_ns)
        remote_root.add_child(remote_leaf)
        local_root = SystemFile("release", 10, True)
        local_leaf = SystemFile("episode.mkv", 10, False, mtime_ns=stale_mtime_ns)
        local_leaf.has_staging_collision = True
        local_root.add_child(local_leaf)
        active_root = SystemFile("release", 10, True)
        active_root.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=stale_mtime_ns))
        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.set_active_files([active_root])
        self.assertFalse(builder.has_complete_local_coverage("release"))
        self.assertTrue(builder.has_unresolved_staging_collision("release"))

        model = Model()
        model.set_base_logger(self.controller.logger)
        release = ModelFile("release", True)
        release.remote_size = 10
        release.local_size = 10
        release.state = ModelFile.State.MOVE_FAILED
        model.add_file(release)
        self.controller._Controller__model = model
        self.controller._Controller__model_builder = builder
        self.controller._Controller__persist.move_failure_counts = {
            release.file_id: Controller._Controller__MAX_MOVE_FAILURES,
        }
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
        command.add_callback(callback)

        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            staging_leaf = os.path.join(staging_root, "release", "episode.mkv")
            final_leaf = os.path.join(final_root, "release", "episode.mkv")
            os.makedirs(os.path.dirname(staging_leaf))
            os.makedirs(os.path.dirname(final_leaf))
            Path(staging_leaf).write_bytes(b"same-bytes")
            Path(final_leaf).write_bytes(b"same-bytes")
            os.utime(staging_leaf, ns=(stale_mtime_ns, stale_mtime_ns))
            os.utime(final_leaf, ns=(stale_mtime_ns, stale_mtime_ns))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            self.controller.queue_command(command)
            self.controller._Controller__process_commands()

            self.assertTrue(os.path.exists(staging_leaf))
            self.assertEqual(b"same-bytes", Path(final_leaf).read_bytes())
        self.assertNotIn(release.file_id, self.controller._Controller__persist.downloaded_file_names)
        callback.on_failure.assert_called_once()
        self.assertEqual(409, callback.on_failure.call_args.args[1])

    def _prepare_authoritative_collision_retry(self, *, partial=False, unmatched=False):
        remote_mtime_ns = 1786400003000000000
        remote_root = SystemFile("release", 10, True)
        remote_root.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=remote_mtime_ns))
        local_root = SystemFile("release", 15 if unmatched else 10, True)
        local_leaf = SystemFile(
            "episode.mkv",
            5 if partial else 10,
            False,
            mtime_ns=remote_mtime_ns,
        )
        local_leaf.has_staging_collision = True
        local_root.add_child(local_leaf)
        if unmatched:
            local_root.add_child(SystemFile("obsolete.tmp", 5, False, is_staging=True))

        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.build_model()
        self.controller._Controller__model_builder = builder

        model = Model()
        model.set_base_logger(self.controller.logger)
        release = ModelFile("release", True)
        release.remote_size = 10
        release.local_size = 10
        release.state = ModelFile.State.MOVE_FAILED
        model.add_file(release)
        self.controller._Controller__model = model
        self.controller._Controller__persist.move_failure_counts = {
            release.file_id: Controller._Controller__MAX_MOVE_FAILURES,
        }
        self.controller._Controller__last_local_reconciliation_healthy = True
        self.controller._Controller__last_remote_reconciliation_healthy = True
        self.controller._Controller__reconciled_local_path_pair_ids = {None}
        self.controller._Controller__reconciled_remote_path_pair_ids = {None}
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__lftp_status_poll_retry_active = False
        self.controller._Controller__lftp_status_cache_expires_at = datetime.now() + timedelta(seconds=30)
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
        command.add_callback(callback)
        return release, command, callback

    def test_manual_retry_move_authoritative_terminal_collision_reaches_move(self):
        release, command, callback = self._prepare_authoritative_collision_retry()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )

        self.assertEqual({release.file_id}, self.controller._Controller__model_builder.get_unresolved_staging_collision_file_ids())
        self.assertEqual({release.file_id}, self.controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids())
        self.assertTrue(
            self.controller._Controller__model_builder.has_verified_staging_collision_remote_identity(
                release.file_id
            )
        )

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.controller._Controller__move_from_staging.assert_called_once_with(
            "release", None, require_collision_proof=True
        )
        callback.on_success.assert_called_once_with()
        self.assertNotIn(release.file_id, self.controller._Controller__persist.move_failure_counts)

    def test_manual_retry_move_collision_safety_matrix_remains_rejected(self):
        cases = (
            "partial", "unmatched", "unreconciled", "stopped", "live",
            "queued", "running", "stale_status", "retry_status", "active_command", "active_compare",
        )
        for case in cases:
            with self.subTest(case=case):
                release, command, callback = self._prepare_authoritative_collision_retry(
                    partial=case == "partial",
                    unmatched=case == "unmatched",
                )
                if case == "unreconciled":
                    self.controller._Controller__last_local_reconciliation_healthy = False
                elif case == "stopped":
                    self.controller._Controller__persist.stopped_file_names = {release.file_id}
                elif case == "live":
                    self.controller._Controller__active_downloading_file_names = [
                        (release.name, release.path_pair_id, release.path_pair_name)
                    ]
                elif case in ("queued", "running"):
                    self.controller._Controller__last_lftp_statuses = [LftpJobStatus(
                        0,
                        LftpJobStatus.Type.MIRROR,
                        LftpJobStatus.State.QUEUED if case == "queued" else LftpJobStatus.State.RUNNING,
                        release.name,
                        "",
                    )]
                elif case == "stale_status":
                    self.controller._Controller__lftp.last_status_poll_healthy = False
                    self.controller._Controller__lftp_status_cache_expires_at = datetime.now() - timedelta(seconds=1)
                elif case == "retry_status":
                    self.controller._Controller__lftp_status_poll_retry_active = True
                elif case == "active_command":
                    self.controller._Controller__active_command_processes = [
                        SimpleNamespace(file_id=release.file_id)
                    ]
                elif case == "active_compare":
                    self.controller._Controller__collision_compare_claim = (
                        os.path.join("/local/incomplete", "release", "episode.mkv"),
                        os.path.join("/local/incomplete", "release", ".seedsync-retire-token"),
                        os.path.join("/local", "release", "episode.mkv"),
                        "collision-key",
                        os.path.join("/local/incomplete", "release", ".seedsync-retire-token.json"),
                    )
                    self.controller._Controller__collision_compare_future = SimpleNamespace(
                        done=lambda: False
                    )
                self.controller._Controller__move_from_staging = MagicMock(
                    return_value=Controller.MoveFromStagingResult.COMPLETED
                )

                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

                self.controller._Controller__move_from_staging.assert_not_called()
                callback.on_failure.assert_called_once()
                self.assertEqual(409, callback.on_failure.call_args.args[1])
                self.assertEqual(
                    Controller._Controller__MAX_MOVE_FAILURES,
                    self.controller._Controller__persist.move_failure_counts[release.file_id],
                )

    def test_manual_retry_move_authoritative_collision_deferred_remains_terminal(self):
        release, command, callback = self._prepare_authoritative_collision_retry()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.DEFERRED
        )

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        self.controller._Controller__move_from_staging.assert_called_once_with(
            "release", None, require_collision_proof=True
        )
        callback.on_failure.assert_called_once()
        self.assertEqual(409, callback.on_failure.call_args.args[1])
        self.assertEqual(
            Controller._Controller__MAX_MOVE_FAILURES,
            self.controller._Controller__persist.move_failure_counts[release.file_id],
        )

    def _prepare_collision_retry_disk(self, source_bytes=b"same", destination_bytes=b"same"):
        release, command, callback = self._prepare_authoritative_collision_retry()
        temp_dir = tempfile.TemporaryDirectory()
        staging_root = os.path.join(temp_dir.name, "incomplete")
        final_root = os.path.join(temp_dir.name, "final")
        source_tree = os.path.join(staging_root, "release")
        destination_tree = os.path.join(final_root, "release")
        os.makedirs(source_tree)
        os.makedirs(destination_tree)
        source_leaf = os.path.join(source_tree, "episode.mkv")
        destination_leaf = os.path.join(destination_tree, "episode.mkv")
        Path(source_leaf).write_bytes(source_bytes)
        Path(destination_leaf).write_bytes(destination_bytes)
        timestamp = 1_786_400_003_000_000_000
        os.utime(source_leaf, ns=(timestamp, timestamp))
        os.utime(destination_leaf, ns=(timestamp, timestamp))
        self.controller._Controller__staging_path = staging_root
        self.controller._Controller__legacy_local_path = final_root
        return temp_dir, release, command, callback, source_tree, source_leaf, destination_leaf

    def test_manual_retry_move_authoritative_collision_reaches_real_comparator_and_clears_max(self):
        temp_dir, release, command, callback, source_tree, source_leaf, destination_leaf = \
            self._prepare_collision_retry_disk()
        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()

            callback.on_failure.assert_called_once()
            self.assertEqual(409, callback.on_failure.call_args.args[1])
            self.assertEqual(
                Controller._Controller__MAX_MOVE_FAILURES,
                self.controller._Controller__persist.move_failure_counts[release.file_id],
            )
            self._settle_collision_compare()

            retry_callback = MagicMock()
            retry_command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
            retry_command.add_callback(retry_callback)
            self.controller.queue_command(retry_command)
            self.controller._Controller__process_commands()

            retry_callback.on_success.assert_called_once_with()
            self.assertNotIn(release.file_id, self.controller._Controller__persist.move_failure_counts)
            self.assertFalse(os.path.exists(source_tree))
            self.assertEqual(b"same", Path(destination_leaf).read_bytes())
        finally:
            self.controller._Controller__shutdown_collision_compare_worker()
            temp_dir.cleanup()

    def test_manual_retry_move_authoritative_collision_mismatch_retains_max(self):
        temp_dir, release, command, callback, source_tree, source_leaf, destination_leaf = \
            self._prepare_collision_retry_disk(source_bytes=b"source", destination_bytes=b"target")
        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
            callback.on_failure.assert_called_once()
            self.assertEqual(409, callback.on_failure.call_args.args[1])
            self._settle_collision_compare()

            retry_callback = MagicMock()
            retry_command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
            retry_command.add_callback(retry_callback)
            self.controller.queue_command(retry_command)
            self.controller._Controller__process_commands()

            retry_callback.on_failure.assert_called_once()
            self.assertEqual(500, retry_callback.on_failure.call_args.args[1])
            self.assertEqual(
                Controller._Controller__MAX_MOVE_FAILURES,
                self.controller._Controller__persist.move_failure_counts[release.file_id],
            )
            self.assertTrue(os.path.exists(source_leaf))
            self.assertEqual(b"target", Path(destination_leaf).read_bytes())
        finally:
            self.controller._Controller__shutdown_collision_compare_worker()
            temp_dir.cleanup()

    def test_manual_retry_move_authoritative_collision_missing_or_empty_source_retains_max(self):
        for empty_root in (False, True):
            with self.subTest(empty_root=empty_root):
                temp_dir, release, command, callback, source_tree, source_leaf, _ = \
                    self._prepare_collision_retry_disk()
                try:
                    os.unlink(source_leaf)
                    if empty_root:
                        os.rmdir(source_tree)
                    self.controller.queue_command(command)
                    self.controller._Controller__process_commands()

                    callback.on_failure.assert_called_once()
                    self.assertEqual(409, callback.on_failure.call_args.args[1])
                    self.assertEqual(
                        Controller._Controller__MAX_MOVE_FAILURES,
                        self.controller._Controller__persist.move_failure_counts[release.file_id],
                    )
                finally:
                    self.controller._Controller__shutdown_collision_compare_worker()
                    temp_dir.cleanup()

    def test_manual_retry_move_collision_nested_clean_sibling_schedules_and_settles(self):
        self.addCleanup(self.controller._Controller__shutdown_collision_compare_worker)
        mtime_ns = 1_786_400_003_000_000_000
        remote_root = SystemFile("release", 20, True)
        remote_clean = SystemFile("a-clean", 10, True)
        remote_clean.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns))
        remote_collision = SystemFile("z-collision", 10, True)
        remote_collision.add_child(SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns))
        remote_root.add_child(remote_clean)
        remote_root.add_child(remote_collision)

        local_root = SystemFile("release", 20, True)
        local_clean = SystemFile("a-clean", 10, True, is_staging=True)
        local_clean.add_child(SystemFile("episode.mkv", 10, False, is_staging=True, mtime_ns=mtime_ns))
        local_collision = SystemFile("z-collision", 10, True)
        collision_leaf = SystemFile("episode.mkv", 10, False, mtime_ns=mtime_ns)
        collision_leaf.has_staging_collision = True
        local_collision.add_child(collision_leaf)
        local_root.add_child(local_clean)
        local_root.add_child(local_collision)

        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_root])
        builder.set_local_files([local_root])
        builder.build_model()
        self.controller._Controller__model_builder = builder

        model = Model()
        model.set_base_logger(self.controller.logger)
        release = ModelFile("release", True)
        release.remote_size = 20
        release.local_size = 20
        release.state = ModelFile.State.MOVE_FAILED
        model.add_file(release)
        self.controller._Controller__model = model
        self.controller._Controller__persist.move_failure_counts = {
            release.file_id: Controller._Controller__MAX_MOVE_FAILURES,
        }
        self.controller._Controller__last_local_reconciliation_healthy = True
        self.controller._Controller__last_remote_reconciliation_healthy = True
        self.controller._Controller__reconciled_local_path_pair_ids = {None}
        self.controller._Controller__reconciled_remote_path_pair_ids = {None}
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__lftp_status_poll_retry_active = False
        self.controller._Controller__lftp_status_cache_expires_at = datetime.now() + timedelta(seconds=30)

        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
        command.add_callback(callback)

        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "release")
            destination_tree = os.path.join(final_root, "release")
            source_clean = os.path.join(source_tree, "a-clean", "episode.mkv")
            source_collision = os.path.join(source_tree, "z-collision", "episode.mkv")
            destination_clean = os.path.join(destination_tree, "a-clean", "episode.mkv")
            destination_collision = os.path.join(destination_tree, "z-collision", "episode.mkv")
            os.makedirs(os.path.dirname(source_clean))
            os.makedirs(os.path.dirname(source_collision))
            os.makedirs(os.path.dirname(destination_clean))
            os.makedirs(os.path.dirname(destination_collision))
            Path(source_clean).write_bytes(b"clean")
            Path(source_collision).write_bytes(b"same")
            Path(destination_collision).write_bytes(b"same")
            os.utime(source_clean, ns=(mtime_ns, mtime_ns))
            os.utime(source_collision, ns=(mtime_ns, mtime_ns))
            os.utime(destination_collision, ns=(mtime_ns, mtime_ns))
            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            merge_paths = []
            original_merge = self.controller._Controller__merge_staging_directory_no_replace

            def record_merge(*args, **kwargs):
                merge_paths.append(os.path.normpath(args[0]))
                return original_merge(*args, **kwargs)

            with patch.object(
                    self.controller,
                    "_Controller__merge_staging_directory_no_replace",
                    side_effect=record_merge,
            ):
                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

            callback.on_failure.assert_called_once()
            self.assertEqual(409, callback.on_failure.call_args.args[1])
            self.assertTrue(os.path.exists(destination_clean))
            self.assertFalse(os.path.exists(source_clean))
            self.assertTrue(os.path.exists(source_tree))
            self.assertIsNotNone(self.controller._Controller__collision_compare_claim)
            self.assertEqual(
                Controller._Controller__MAX_MOVE_FAILURES,
                self.controller._Controller__persist.move_failure_counts[release.file_id],
            )
            self._settle_collision_compare()

            retry_callback = MagicMock()
            retry_command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
            retry_command.add_callback(retry_callback)
            with patch.object(
                    self.controller,
                    "_Controller__merge_staging_directory_no_replace",
                    side_effect=record_merge,
            ):
                self.controller.queue_command(retry_command)
                self.controller._Controller__process_commands()

            retry_callback.on_success.assert_called_once_with()
            self.assertIn(os.path.normpath(os.path.join(source_tree, "a-clean")), merge_paths)
            self.assertNotIn(release.file_id, self.controller._Controller__persist.move_failure_counts)
            self.assertFalse(os.path.exists(source_tree))
            self.assertEqual(b"clean", Path(destination_clean).read_bytes())
            self.assertEqual(b"same", Path(destination_collision).read_bytes())

    def _prepare_root_file_collision_retry_disk(self, source_bytes=b"same", destination_bytes=b"same"):
        remote_mtime_ns = 1_786_400_003_000_000_000
        remote_file = SystemFile("release", len(destination_bytes), False, mtime_ns=remote_mtime_ns)
        local_file = SystemFile("release", len(source_bytes), False, mtime_ns=remote_mtime_ns)
        local_file.has_staging_collision = True
        builder = ModelBuilder()
        builder.set_base_logger(self.controller.logger)
        builder.set_remote_files([remote_file])
        builder.set_local_files([local_file])
        builder.build_model()
        self.controller._Controller__model_builder = builder
        model = Model()
        model.set_base_logger(self.controller.logger)
        release = ModelFile("release", False)
        release.remote_size = len(destination_bytes)
        release.local_size = len(source_bytes)
        release.state = ModelFile.State.MOVE_FAILED
        model.add_file(release)
        self.controller._Controller__model = model
        self.controller._Controller__persist.move_failure_counts = {
            release.file_id: Controller._Controller__MAX_MOVE_FAILURES,
        }
        self.controller._Controller__last_local_reconciliation_healthy = True
        self.controller._Controller__last_remote_reconciliation_healthy = True
        self.controller._Controller__reconciled_local_path_pair_ids = {None}
        self.controller._Controller__reconciled_remote_path_pair_ids = {None}
        self.controller._Controller__lftp.last_status_poll_healthy = True
        self.controller._Controller__lftp_status_poll_retry_active = False
        self.controller._Controller__lftp_status_cache_expires_at = datetime.now() + timedelta(seconds=30)
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
        command.add_callback(callback)
        temp_dir = tempfile.TemporaryDirectory()
        staging_root = os.path.join(temp_dir.name, "incomplete")
        final_root = os.path.join(temp_dir.name, "final")
        os.makedirs(staging_root)
        os.makedirs(final_root)
        source = os.path.join(staging_root, "release")
        destination = os.path.join(final_root, "release")
        Path(source).write_bytes(source_bytes)
        Path(destination).write_bytes(destination_bytes)
        os.utime(source, ns=(remote_mtime_ns, remote_mtime_ns))
        os.utime(destination, ns=(remote_mtime_ns, remote_mtime_ns))
        self.controller._Controller__staging_path = staging_root
        self.controller._Controller__legacy_local_path = final_root
        return temp_dir, release, command, callback, source, destination

    def test_manual_retry_move_root_file_collision_equal_settles_and_clears_max(self):
        temp_dir, release, command, callback, source, destination = \
            self._prepare_root_file_collision_retry_disk()
        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
            callback.on_failure.assert_called_once()
            self._settle_collision_compare()

            retry_callback = MagicMock()
            retry_command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
            retry_command.add_callback(retry_callback)
            self.controller.queue_command(retry_command)
            self.controller._Controller__process_commands()

            retry_callback.on_success.assert_called_once_with()
            self.assertNotIn(release.file_id, self.controller._Controller__persist.move_failure_counts)
            self.assertFalse(os.path.exists(source))
            self.assertEqual(b"same", Path(destination).read_bytes())
        finally:
            self.controller._Controller__shutdown_collision_compare_worker()
            temp_dir.cleanup()

    def test_manual_retry_move_root_file_collision_mismatch_restores_and_retains_max(self):
        temp_dir, release, command, callback, source, destination = \
            self._prepare_root_file_collision_retry_disk(b"source", b"target")
        try:
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()
            callback.on_failure.assert_called_once()
            self._settle_collision_compare()

            retry_callback = MagicMock()
            retry_command = Controller.Command(Controller.Command.Action.RETRY_MOVE, release.file_id)
            retry_command.add_callback(retry_callback)
            self.controller.queue_command(retry_command)
            self.controller._Controller__process_commands()

            retry_callback.on_failure.assert_called_once()
            self.assertEqual(500, retry_callback.on_failure.call_args.args[1])
            self.assertEqual(
                Controller._Controller__MAX_MOVE_FAILURES,
                self.controller._Controller__persist.move_failure_counts[release.file_id],
            )
            self.assertTrue(os.path.exists(source))
        finally:
            self.controller._Controller__shutdown_collision_compare_worker()
            temp_dir.cleanup()

    def test_manual_retry_move_root_file_missing_source_without_claim_retains_max(self):
        temp_dir, release, command, callback, source, _ = self._prepare_root_file_collision_retry_disk()
        try:
            os.unlink(source)
            self.controller.queue_command(command)
            self.controller._Controller__process_commands()

            callback.on_failure.assert_called_once()
            self.assertEqual(409, callback.on_failure.call_args.args[1])
            self.assertEqual(
                Controller._Controller__MAX_MOVE_FAILURES,
                self.controller._Controller__persist.move_failure_counts[release.file_id],
            )
        finally:
            self.controller._Controller__shutdown_collision_compare_worker()
            temp_dir.cleanup()

    def test_manual_retry_move_collision_source_disappearing_after_proof_retains_max(self):
        temp_dir, release, command, callback, source_tree, source_leaf, _ = \
            self._prepare_collision_retry_disk()
        try:
            def remove_after_proof(*_args):
                os.unlink(source_leaf)
                return True

            with patch.object(
                    self.controller,
                    "_Controller__collision_move_source_has_physical_proof",
                    side_effect=remove_after_proof,
            ):
                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

            callback.on_failure.assert_called_once()
            self.assertEqual(500, callback.on_failure.call_args.args[1])
            self.assertEqual(
                Controller._Controller__MAX_MOVE_FAILURES,
                self.controller._Controller__persist.move_failure_counts[release.file_id],
            )
            self.assertTrue(os.path.exists(source_tree))
        finally:
            self.controller._Controller__shutdown_collision_compare_worker()
            temp_dir.cleanup()

    def test_manual_retry_move_collision_source_disappearing_after_merge_entry_retains_max(self):
        temp_dir, release, command, callback, source_tree, source_leaf, _ = \
            self._prepare_collision_retry_disk()
        try:
            original_recover = self.controller._Controller__recover_collision_claims
            removed = False

            def recover_then_remove(*args, **kwargs):
                nonlocal removed
                result = original_recover(*args, **kwargs)
                if not removed:
                    os.unlink(source_leaf)
                    removed = True
                return result

            with patch.object(
                    self.controller,
                    "_Controller__recover_collision_claims",
                    side_effect=recover_then_remove,
            ):
                self.controller.queue_command(command)
                self.controller._Controller__process_commands()

            callback.on_failure.assert_called_once()
            self.assertEqual(500, callback.on_failure.call_args.args[1])
            self.assertEqual(
                Controller._Controller__MAX_MOVE_FAILURES,
                self.controller._Controller__persist.move_failure_counts[release.file_id],
            )
            self.assertTrue(os.path.exists(source_tree))
            self.assertFalse(os.path.exists(source_leaf))
        finally:
            self.controller._Controller__shutdown_collision_compare_worker()
            temp_dir.cleanup()

    @patch("controller.model_updater.ModelDiffUtil.diff_models")
    def test_automatic_already_completed_does_not_earn_success_marker(self, diff_models):
        old_file = ModelFile("movie.mkv", False)
        old_file.remote_size = 100; old_file.local_size = 90
        new_file = ModelFile("movie.mkv", False)
        new_file.remote_size = 100; new_file.local_size = 100
        new_file.state = ModelFile.State.DOWNLOADED
        current = Model(); current.set_base_logger(self.controller.logger); current.add_file(old_file)
        rebuilt = Model(); rebuilt.set_base_logger(self.controller.logger); rebuilt.add_file(new_file)
        self.controller._Controller__model = current
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = rebuilt
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__lftp.status.return_value = []
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.ALREADY_COMPLETED
        )
        diff_models.return_value = [SimpleNamespace(
            change=ModelDiff.Change.UPDATED, old_file=old_file, new_file=new_file
        )]

        self.controller._Controller__update_model()

        self.assertIn(new_file.file_id, self.controller._Controller__persist.downloaded_file_names)
        self.assertNotIn(new_file.file_id, self.controller._Controller__persist.final_move_succeeded_file_names)

    def test_recover_interrupted_downloads_requeues_single_path_temp_file(self):
        self.controller._Controller__persist.downloaded_file_names = set()

        remote_file = SimpleNamespace(name="movie.mkv", path_pair_id=None, is_dir=False)
        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)), \
                patch.object(
                    self.controller, "_Controller__transfer_exclude_patterns",
                    wraps=self.controller._Controller__transfer_exclude_patterns,
                ) as exclude_patterns:
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.assertTrue(self.controller._Controller__startup_recovery_done)
        self.controller._Controller__lftp.queue.assert_called_once_with(
            "movie.mkv",
            False,
            remote_base_dir_path=None,
            local_base_dir_path="/local/incomplete"
        )
        self.assertIsNotNone(exclude_patterns.call_args.args[2])
        self.assertEqual({}, self.controller._Controller__download_start_state)

    def test_recover_interrupted_downloads_uses_async_owner_for_real_lftp(self):
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__lftp.backend_name = "lftp"
        queued = threading.Event()
        release = threading.Event()

        def blocking_queue(*_args, **_kwargs):
            queued.set()
            release.wait(2)

        self.controller._Controller__lftp.queue.side_effect = blocking_queue
        remote_file = SimpleNamespace(name="movie.mkv", path_pair_id=None, is_dir=False)
        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)):
            started_at = time.monotonic()
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertTrue(queued.wait(1))
        file_id = ModelFile.build_file_id("movie.mkv", None)
        self.assertIn(file_id, self.controller._Controller__pending_queue_dispatches)
        release.set()
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def test_recovery_queue_trace_correlates_serialization_dispatch_future_and_status(self):
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__lftp.backend_name = "lftp"
        trace = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.controller._Controller__context.breadcrumb_trace = trace
        remote_file = SimpleNamespace(name="movie.mkv", path_pair_id=None, is_dir=False)
        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch.object(
                    Controller, "_Controller__safe_recovery_staging_entry",
                    side_effect=lambda root, name, _is_dir: os.path.join(root, name),
                ):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        deadline = time.monotonic() + 1
        while self.controller._Controller__lftp_operations and time.monotonic() < deadline:
            self.controller._Controller__drain_lftp_operations()
            time.sleep(0.01)
        self.controller._reconcile_pending_queue_dispatches_from_fresh_status([])

        time.sleep(0.05)
        entries = trace.snapshot()["entries"]
        selected = [
            entry for entry in entries if entry["message"] in {
                "queue_exclusion_serialization",
                "startup_recovery_queue_dispatch",
                "queue_future_outcome",
                "queue_status_ack",
            }
        ]
        self.assertEqual(
            {
                "queue_exclusion_serialization",
                "startup_recovery_queue_dispatch",
                "queue_future_outcome",
                "queue_status_ack",
            },
            {entry["message"] for entry in selected},
        )
        self.assertEqual(1, len({entry["flow_id"] for entry in selected}))

    def test_recovery_async_queue_abandons_pre_queue_idle_status_for_post_queue_poll(self):
        class TrackingFuture(Future):
            def __init__(self):
                super().__init__()
                self.result_calls = 0

            def result(self, timeout=None):
                self.result_calls += 1
                return super().result(timeout)

        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__lftp.backend_name = "lftp"
        stale_status = TrackingFuture()
        stale_status.set_result(([], True))
        self.controller._Controller__lftp_status_future = stale_status
        self.controller._Controller__lftp_idle_status_authoritative = True
        self.controller._Controller__next_lftp_status_poll_at = datetime.now() + timedelta(minutes=1)
        queued = threading.Event()
        release = threading.Event()

        def blocking_queue(*_args, **_kwargs):
            queued.set()
            release.wait(2)

        self.controller._Controller__lftp.queue.side_effect = blocking_queue
        self.controller._Controller__lftp.status.return_value = []
        remote_file = SimpleNamespace(name="movie.mkv", path_pair_id=None, is_dir=False)
        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.assertTrue(queued.wait(1))
        self.assertIsNone(self.controller._Controller__lftp_status_future)
        self.assertIsNone(self.controller._Controller__next_lftp_status_poll_at)
        self.assertFalse(self.controller._Controller__lftp_idle_status_authoritative)
        self.assertEqual(0, stale_status.result_calls)
        self.assertIsNone(self.controller._get_lftp_status_snapshot())
        release.set()
        deadline = time.monotonic() + 1
        snapshot = None
        while snapshot is None and time.monotonic() < deadline:
            snapshot = self.controller._get_lftp_status_snapshot()
            if snapshot is None:
                time.sleep(0.01)
        self.assertEqual(([], True), snapshot)
        self.assertEqual(1, self.controller._Controller__lftp.status.call_count)
        self.assertEqual(0, stale_status.result_calls)
        self.controller._Controller__lftp_executor.shutdown(wait=True)

    def test_recover_interrupted_downloads_bootstraps_legacy_sidecarless_partial(self):
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__lftp.backend_name = "lftp"
        remote_file = SystemFile("movie.mkv", 100, False, mtime_ns=1700000000000000000)
        self.controller._Controller__persist.downloaded_timestamps = {
            ModelFile.build_file_id(remote_file.name, remote_file.path_pair_id): 1700000001.0,
        }

        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)):
            self.controller._Controller__recover_interrupted_downloads([remote_file])
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        queue_kwargs = self.controller._Controller__lftp.queue.call_args.kwargs
        self.assertFalse(queue_kwargs["allow_resume"])
        self.assertTrue(queue_kwargs["allow_legacy_get_resume"])
        self.assertEqual(100, queue_kwargs["expected_size"])

    def test_recover_interrupted_downloads_discovers_qualifying_legacy_direct_partial(self):
        self.controller._Controller__lftp.backend_name = "lftp"
        remote_file = SystemFile("movie.mkv", 100, False, mtime_ns=1700000000000000000)
        self.controller._Controller__persist.downloaded_timestamps = {
            ModelFile.build_file_id(remote_file.name, None): 1700000001.0,
        }
        with tempfile.TemporaryDirectory() as staging_path, tempfile.TemporaryDirectory() as final_path:
            Path(os.path.join(staging_path, remote_file.name)).write_bytes(b"partial")
            self.controller._Controller__staging_path = staging_path
            self.controller._Controller__legacy_local_path = final_path

            self.controller._Controller__recover_interrupted_downloads([remote_file])
            self.controller._Controller__lftp_executor.shutdown(wait=True)

        queue_kwargs = self.controller._Controller__lftp.queue.call_args.kwargs
        self.assertTrue(queue_kwargs["allow_legacy_get_resume"])
        self.assertEqual(100, queue_kwargs["expected_size"])

    def test_recover_interrupted_downloads_rejects_ambiguous_legacy_direct_and_temp_partials(self):
        self.controller._Controller__lftp.backend_name = "lftp"
        remote_file = SystemFile("movie.mkv", 100, False, mtime_ns=1700000000000000000)
        self.controller._Controller__persist.downloaded_timestamps = {
            ModelFile.build_file_id(remote_file.name, None): 1700000001.0,
        }
        with tempfile.TemporaryDirectory() as staging_path, tempfile.TemporaryDirectory() as final_path:
            Path(os.path.join(staging_path, remote_file.name)).write_bytes(b"partial")
            Path(os.path.join(staging_path, remote_file.name + ".lftp")).write_bytes(b"partial")
            self.controller._Controller__staging_path = staging_path
            self.controller._Controller__legacy_local_path = final_path

            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_not_called()

    def test_recover_interrupted_downloads_does_not_queue_direct_partial_with_status_map(self):
        self.controller._Controller__lftp.backend_name = "lftp"
        remote_file = SystemFile("movie.mkv", 100, False, mtime_ns=1700000000000000000)
        self.controller._Controller__persist.downloaded_timestamps = {
            ModelFile.build_file_id(remote_file.name, None): 1700000001.0,
        }
        with tempfile.TemporaryDirectory() as staging_path, tempfile.TemporaryDirectory() as final_path:
            target = os.path.join(staging_path, remote_file.name)
            Path(target).write_bytes(b"unknown-partial")
            Path(target + ".lftp-pget-status").write_text("size=100\n0.pos=bad\n")
            self.controller._Controller__staging_path = staging_path
            self.controller._Controller__legacy_local_path = final_path

            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_not_called()
        self.assertEqual({}, getattr(self.controller, "_Controller__pending_queue_dispatches", {}))

    def test_recover_interrupted_downloads_reloads_unscoped_resume_source_binding(self):
        remote_file = SystemFile("movie.mkv", 100, False, mtime_ns=1700000000000000000)
        file_id = ModelFile.build_file_id(remote_file.name, None)
        persisted = ControllerPersist()
        persisted.resume_source_identities = {file_id: (100, 1700000000)}
        self.controller._Controller__persist = ControllerPersist.from_str(persisted.to_str())
        self.controller._Controller__lftp.backend_name = "lftp"

        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)):
            self.controller._Controller__recover_interrupted_downloads([remote_file])
        self.controller._Controller__lftp_executor.shutdown(wait=True)

        queue_kwargs = self.controller._Controller__lftp.queue.call_args.kwargs
        self.assertTrue(queue_kwargs["allow_resume"])
        self.assertEqual(100, queue_kwargs["expected_size"])

    def test_recover_interrupted_downloads_skips_previously_downloaded_path_pair_file(self):
        file_id = ModelFile.build_file_id("dup.mkv", "movies")
        self.controller._Controller__persist.downloaded_file_names = {file_id}
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        remote_file = SimpleNamespace(name="dup.mkv", path_pair_id="movies", is_dir=False)
        with patch("controller.controller.os.listdir", return_value=["dup.mkv.lftp"]), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_not_called()

    def test_recover_interrupted_downloads_skips_stopped_file_for_matching_path_pair_only(self):
        stopped_file_id = ModelFile.build_file_id("dup.mkv", "movies")
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__persist.stopped_file_names = {stopped_file_id}
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies"),
            "tv": SimpleNamespace(remote_path="/remote/tv", local_path="/local/tv")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete",
            "tv": "/local/tv/incomplete"
        }

        remote_files = [
            SimpleNamespace(name="dup.mkv", path_pair_id="movies", is_dir=False),
            SimpleNamespace(name="dup.mkv", path_pair_id="tv", is_dir=False)
        ]

        def listdir_side_effect(path):
            if path == "/local/movies/incomplete":
                return ["dup.mkv.lftp"]
            if path == "/local/tv/incomplete":
                return ["dup.mkv.lftp"]
            raise AssertionError(path)

        with patch("controller.controller.os.listdir", side_effect=listdir_side_effect), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)):
            self.controller._Controller__recover_interrupted_downloads(remote_files)

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "dup.mkv",
            False,
            remote_base_dir_path="/remote/tv",
            local_base_dir_path="/local/tv/incomplete"
        )

    def test_recover_interrupted_downloads_requeues_nested_pget_partial(self):
        with tempfile.TemporaryDirectory() as staging_root:
            nested = os.path.join(staging_root, "release", "season")
            os.makedirs(nested)
            Path(os.path.join(nested, "movie.mkv.lftp")).write_bytes(b"partial")
            Path(os.path.join(nested, "movie.mkv.lftp.lftp-pget-status")).write_text(
                "size=10\n0.pos=0\n0.limit=10\n", encoding="utf-8",
            )
            remote_root = SystemFile("release", 10, True)
            remote_season = SystemFile("season", 10, True)
            remote_child = SystemFile("movie.mkv", 10, False)
            remote_season.add_child(remote_child)
            remote_root.add_child(remote_season)
            remote_root.path_pair_id = "movies"
            self.controller._Controller__persist.downloaded_file_names = set()
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
            }
            self.controller._Controller__path_pair_staging_paths = {"movies": staging_root}

            self.controller._Controller__recover_interrupted_downloads([remote_root])

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "release/season/movie.mkv", False,
            remote_base_dir_path="/remote/movies", local_base_dir_path=ANY,
        )

    def test_recover_interrupted_downloads_requeues_nested_direct_pget_target(self):
        with tempfile.TemporaryDirectory() as staging_root:
            nested = os.path.join(staging_root, "release", "season")
            os.makedirs(nested)
            Path(os.path.join(nested, "movie.mkv")).write_bytes(b"partial")
            Path(os.path.join(nested, "movie.mkv.lftp-pget-status")).write_text(
                "size=10\n0.pos=0\n0.limit=10\n", encoding="utf-8",
            )
            remote_root = SystemFile("release", 10, True)
            remote_season = SystemFile("season", 10, True)
            remote_child = SystemFile("movie.mkv", 10, False)
            remote_season.add_child(remote_child); remote_root.add_child(remote_season)
            remote_root.path_pair_id = "movies"
            self.controller._Controller__persist.downloaded_file_names = set()
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
            }
            self.controller._Controller__path_pair_staging_paths = {"movies": staging_root}

            self.controller._Controller__recover_interrupted_downloads([remote_root])

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "release/season/movie.mkv", False,
            remote_base_dir_path="/remote/movies", local_base_dir_path=ANY,
        )

    def test_recover_interrupted_downloads_does_not_mirror_nested_no_sidecar_partial(self):
        with tempfile.TemporaryDirectory() as staging_root:
            release_path = os.path.join(staging_root, "release", "season")
            os.makedirs(release_path)
            temp_path = os.path.join(release_path, "movie.mkv.lftp")
            logical_size = 4 * 1024 ** 3
            Path(temp_path).write_bytes(b"partial")
            # Keep Windows CI physically small; POSIX filesystems provide the
            # sparse logical-size discriminator used by the escaped run.
            if os.name == "posix":
                os.truncate(temp_path, logical_size)
            remote_root = SystemFile("release", logical_size, True)
            remote_season = SystemFile("season", logical_size, True)
            remote_child = SystemFile("movie.mkv", logical_size, False)
            remote_season.add_child(remote_child)
            remote_root.add_child(remote_season)
            remote_root.path_pair_id = "movies"
            self.controller._Controller__persist.downloaded_file_names = set()
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
            }
            self.controller._Controller__path_pair_staging_paths = {"movies": staging_root}

            self.controller._Controller__recover_interrupted_downloads([remote_root])
            self.assertTrue(os.path.exists(temp_path))
            if os.name == "posix":
                self.assertEqual(logical_size, os.path.getsize(temp_path))
            self.assertEqual(logical_size, remote_child.size)

        self.controller._Controller__lftp.queue.assert_not_called()

    def test_recover_interrupted_downloads_mirrors_nested_lftp_payload(self):
        with tempfile.TemporaryDirectory() as staging_root:
            release_path = os.path.join(staging_root, "release", "season")
            os.makedirs(release_path)
            payload_path = os.path.join(release_path, "notes.lftp")
            Path(payload_path).write_bytes(b"payload")
            remote_root = SystemFile("release", 7, True)
            remote_season = SystemFile("season", 7, True)
            remote_payload = SystemFile("notes.lftp", 7, False)
            remote_season.add_child(remote_payload)
            remote_root.add_child(remote_season)
            remote_root.path_pair_id = "movies"
            self.controller._Controller__persist.downloaded_file_names = set()
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
            }
            self.controller._Controller__path_pair_staging_paths = {"movies": staging_root}

            self.controller._Controller__recover_interrupted_downloads([remote_root])
            self.assertTrue(os.path.exists(payload_path))

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "release", True, remote_base_dir_path="/remote/movies", local_base_dir_path=ANY,
        )

    def test_recover_interrupted_downloads_does_not_mirror_nested_invalid_pget_sidecar(self):
        with tempfile.TemporaryDirectory() as staging_root:
            release_path = os.path.join(staging_root, "release")
            os.makedirs(release_path)
            Path(os.path.join(release_path, "movie.mkv.lftp")).write_bytes(b"partial")
            Path(os.path.join(release_path, "movie.mkv.lftp.lftp-pget-status")).write_text(
                "invalid", encoding="utf-8",
            )
            remote_root = SystemFile("release", 10, True)
            remote_root.path_pair_id = "movies"
            self.controller._Controller__persist.downloaded_file_names = set()
            self.controller._Controller__path_pairs_by_id = {
                "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
            }
            self.controller._Controller__path_pair_staging_paths = {"movies": staging_root}

            self.controller._Controller__recover_interrupted_downloads([remote_root])

        self.controller._Controller__lftp.queue.assert_not_called()

    def test_recover_interrupted_downloads_queues_path_pair_directory(self):
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__exclude_patterns = "*.nfo,Sample/"
        self.controller._Controller__context.config.general.exclude_patterns = ""
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        remote_file = SimpleNamespace(name="season1", path_pair_id="movies", is_dir=True)

        def listdir_side_effect(path):
            if path == "/local/movies/incomplete":
                return ["season1"]
            if path == os.path.join("/local/movies/incomplete", "season1"):
                return ["episode1.mkv.lftp"]
            raise AssertionError(path)

        with patch("controller.controller.os.listdir", side_effect=listdir_side_effect), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "season1",
            True,
            remote_base_dir_path="/remote/movies",
            local_base_dir_path="/local/movies/incomplete",
            exclude_patterns="*.nfo,Sample/"
        )

    def test_recover_interrupted_downloads_rejects_staging_directory_symlink(self):
        """Recovery must never traverse a directory artifact outside staging."""
        remote_file = SystemFile("sample-directory", 0, True)
        with tempfile.TemporaryDirectory() as staging_path, \
                tempfile.TemporaryDirectory() as outside_path, \
                tempfile.TemporaryDirectory() as final_path:
            outside_directory = os.path.join(outside_path, remote_file.name)
            os.mkdir(outside_directory)
            Path(os.path.join(outside_directory, "part.lftp")).write_bytes(b"partial")
            os.symlink(outside_directory, os.path.join(staging_path, remote_file.name))
            self.controller._Controller__staging_path = staging_path
            self.controller._Controller__legacy_local_path = final_path

            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_not_called()
        self.assertEqual({}, getattr(self.controller, "_Controller__pending_queue_dispatches", {}))

    def test_recovery_staging_entry_rejects_windows_reparse_directory(self):
        root_stat = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)
        reparse_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
        with patch.object(
                Controller, "_Controller__safe_final_move_candidate", return_value="C:\\staging\\sample-directory",
        ), patch("controller.controller.os.lstat", side_effect=[root_stat, reparse_stat]):
            self.assertIsNone(Controller._Controller__safe_recovery_staging_entry(
                "C:\\staging", "sample-directory", True,
            ))

    def test_recover_interrupted_downloads_rejects_directory_remote_type_mismatch(self):
        remote_file = SystemFile("sample-directory", 10, False)
        with tempfile.TemporaryDirectory() as staging_path, tempfile.TemporaryDirectory() as final_path:
            directory_path = os.path.join(staging_path, remote_file.name)
            os.mkdir(directory_path)
            Path(os.path.join(directory_path, "part.lftp")).write_bytes(b"partial")
            self.controller._Controller__staging_path = staging_path
            self.controller._Controller__legacy_local_path = final_path

            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_not_called()

    def test_recover_interrupted_downloads_rechecks_artifact_before_queue(self):
        remote_file = SystemFile("sample-file.bin", 10, False)
        with tempfile.TemporaryDirectory() as staging_path, tempfile.TemporaryDirectory() as final_path:
            entry_path = os.path.join(staging_path, remote_file.name + ".lftp")
            Path(entry_path).write_bytes(b"partial")
            self.controller._Controller__staging_path = staging_path
            self.controller._Controller__legacy_local_path = final_path
            with patch.object(
                    Controller,
                    "_Controller__safe_recovery_staging_entry",
                    side_effect=[entry_path, None],
            ):
                self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_not_called()

    def test_process_commands_queue_records_sanitized_boundary_breadcrumb(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        breadcrumb_trace = self.controller._Controller__context.breadcrumb_trace
        breadcrumb_trace.is_enabled.return_value = True
        temp_path = os.path.join("/local/movies/incomplete", "dup.lftp")
        sidecar_path = temp_path + ".lftp-pget-status"

        def stat_side_effect(path):
            if path == temp_path:
                return SimpleNamespace(st_size=250, st_mtime=111, st_blocks=8)
            if path == sidecar_path:
                return SimpleNamespace(st_size=64, st_mtime=222)
            raise OSError(path)

        with patch("controller.controller.os.stat", side_effect=stat_side_effect):
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
            self.controller._Controller__process_commands()

        boundary_calls = [call for call in breadcrumb_trace.record.call_args_list if call.args[1] == "stop_resume_boundary"]
        payload = boundary_calls[-1].args[2]
        self.assertEqual("queue_fresh", payload["reason"])
        self.assertEqual(file.file_id, payload["file_id"])
        self.assertEqual("DEFAULT", payload["current_state"])
        self.assertEqual(250, payload["temp"]["size"])
        self.assertEqual(4096, payload["temp"]["allocated_size"])
        self.assertEqual(64, payload["sidecar"]["size"])
        self.assertEqual(222, payload["sidecar"]["mtime"])
        self.assertNotIn("local_base_dir_path", str(payload))
        self.assertNotIn("temp_path", str(payload))
        self.assertFalse(payload["stopped_marked"])

        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        breadcrumb_trace.record.reset_mock()
        with patch("controller.controller.os.stat", side_effect=stat_side_effect):
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
            self.controller._Controller__process_commands()

        boundary_calls = [call for call in breadcrumb_trace.record.call_args_list if call.args[1] == "stop_resume_boundary"]
        payload = boundary_calls[-1].args[2]
        self.assertEqual("queue_after_stop", payload["reason"])
        self.assertTrue(payload["stopped_marked"])

    def test_process_commands_queue_captures_without_per_file_selector(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        breadcrumb_trace = self.controller._Controller__context.breadcrumb_trace
        breadcrumb_trace.is_enabled.return_value = True

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        boundary_calls = [call for call in breadcrumb_trace.record.call_args_list if call.args[1] == "stop_resume_boundary"]
        payload = boundary_calls[-1].args[2]
        self.assertEqual(file.file_id, payload["file_id"])

    def test_process_commands_queue_trace_is_globally_gated(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        breadcrumb_trace = self.controller._Controller__context.breadcrumb_trace
        breadcrumb_trace.is_enabled.return_value = False

        with patch("controller.controller.os.stat") as stat_mock:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
            self.controller._Controller__process_commands()
        stat_mock.assert_not_called()

        self.assertFalse(any(
            call.args[1] == "stop_resume_boundary"
            for call in breadcrumb_trace.record.call_args_list
        ))

    def test_process_commands_stop_records_boundary_breadcrumb(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        breadcrumb_trace = self.controller._Controller__context.breadcrumb_trace
        breadcrumb_trace.is_enabled.return_value = True
        temp_path = os.path.join("/local/movies/incomplete", "dup.lftp")
        sidecar_path = temp_path + ".lftp-pget-status"

        def stat_side_effect(path):
            if path == temp_path:
                return SimpleNamespace(st_size=250, st_mtime=111, st_blocks=8)
            if path == sidecar_path:
                return SimpleNamespace(st_size=64, st_mtime=222)
            raise OSError(path)

        self.controller._Controller__lftp.kill.return_value = True
        with patch("controller.controller.os.stat", side_effect=stat_side_effect):
            self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, file.file_id))
            self.controller._Controller__process_commands()

        boundary_calls = [call for call in breadcrumb_trace.record.call_args_list if call.args[1] == "stop_resume_boundary"]
        payload = boundary_calls[-1].args[2]
        self.assertEqual("stop", payload["reason"])
        self.assertEqual(file.file_id, payload["file_id"])
        self.assertEqual("DOWNLOADING", payload["current_state"])
        self.assertEqual(250, payload["temp"]["size"])
        self.assertEqual(4096, payload["temp"]["allocated_size"])
        self.assertEqual(64, payload["sidecar"]["size"])
        self.assertEqual(222, payload["sidecar"]["mtime"])
        self.assertNotIn("local_base_dir_path", str(payload))
        self.assertNotIn("remote_base_dir_path", str(payload))

    def test_transfer_stop_breadcrumb_success_and_queue_clear_are_opaque(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"transfer.stop": "info"}},
        )
        self.controller._Controller__context.breadcrumb_trace = trace

        self.controller._Controller__record_transfer_stop_breadcrumb(
            "private-file-id",
            source="stop",
            marker_before=False,
            marker_after=True,
            backend_outcome="success",
            marker_observed=True,
            operation_sequence=7,
            rejection_reason="none",
        )
        self.controller._Controller__record_transfer_stop_breadcrumb(
            "private-file-id",
            source="queue",
            marker_before=True,
            marker_after=False,
            backend_outcome="pending",
            marker_observed=False,
            operation_sequence=8,
            rejection_reason="none",
            message="transfer_stop_queue_marker_clear",
        )

        entries = trace.snapshot()["entries"]
        self.assertEqual(2, len(entries))
        self.assertEqual(
            ["stop", "queue"],
            [entry["details"]["transition_source"] for entry in entries],
        )
        self.assertEqual("transfer_stop.v1", entries[0]["details"]["schema"])
        self.assertEqual("info", entries[0]["level"])
        self.assertNotIn("private-file-id", str(entries))
        self.assertNotIn("transfer-stop:private-file-id", str(entries))

    def test_transfer_stop_breadcrumb_failed_rollback_is_warning(self):
        trace = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"transfer.stop": "warning"}},
        )
        self.controller._Controller__context.breadcrumb_trace = trace

        self.controller._Controller__record_transfer_stop_breadcrumb(
            "private-file-id",
            source="rollback",
            marker_before=True,
            marker_after=False,
            backend_outcome="rejected",
            marker_observed=False,
            operation_sequence=9,
            rejection_reason="backend_rejected",
            message="transfer_stop_marker_rollback",
        )

        entry = trace.snapshot()["entries"][0]
        self.assertEqual("warning", entry["level"])
        self.assertEqual("rollback", entry["details"]["transition_source"])
        self.assertEqual("rejected", entry["details"]["backend_outcome"])
        self.assertEqual("backend_rejected", entry["details"]["rejection_reason"])
        self.assertNotIn("private-file-id", str(entry))

    def test_transfer_stop_breadcrumb_gate_precedes_correlation(self):
        class DisabledTrace:
            def is_effectively_enabled(self, category, level="info"):
                return False

        self.controller._Controller__context.breadcrumb_trace = DisabledTrace()
        with patch(
            "controller.controller.opaque_trace_correlation",
            side_effect=AssertionError("disabled Stop trace built correlation"),
        ):
            self.controller._Controller__record_transfer_stop_breadcrumb(
                "private-file-id",
                source="stop",
                marker_before=False,
                marker_after=True,
                backend_outcome="pending",
                operation_sequence=1,
            )

    def test_recover_interrupted_downloads_records_boundary_breadcrumb(self):
        self.controller._Controller__persist.downloaded_file_names = set()
        breadcrumb_trace = self.controller._Controller__context.breadcrumb_trace
        breadcrumb_trace.is_enabled.return_value = True
        temp_path = os.path.join("/local/incomplete", "movie.mkv.lftp")
        sidecar_path = temp_path + ".lftp-pget-status"

        def stat_side_effect(path):
            if path == temp_path:
                return SimpleNamespace(st_size=250, st_mtime=111, st_blocks=8)
            if path == sidecar_path:
                return SimpleNamespace(st_size=64, st_mtime=222)
            raise OSError(path)

        remote_file = SimpleNamespace(name="movie.mkv", path_pair_id=None, is_dir=False)
        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch.object(Controller, "_Controller__safe_recovery_staging_entry", side_effect=lambda root, name, _is_dir: os.path.join(root, name)), \
                patch("controller.controller.os.stat", side_effect=stat_side_effect):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        boundary_calls = [call for call in breadcrumb_trace.record.call_args_list if call.args[1] == "stop_resume_boundary"]
        payload = boundary_calls[-1].args[2]
        self.assertEqual("recover_interrupted_download", payload["reason"])
        self.assertEqual("movie.mkv", payload["file_id"])
        self.assertEqual(250, payload["temp"]["size"])
        self.assertEqual(4096, payload["temp"]["allocated_size"])
        self.assertEqual(64, payload["sidecar"]["size"])
        self.assertNotIn("local_base_dir_path", str(payload))
        self.assertNotIn("remote_base_dir_path", str(payload))

    def test_enabled_path_pair_relocation_accepts_same_directory_alias_when_idle(self):
        with tempfile.TemporaryDirectory() as local_path:
            existing = PathPair(
                id="movies", name="Movies", remote_path="/remote/movies", local_path=local_path,
            )
            updated = PathPair(
                id="movies", name="Movies", remote_path="/remote/movies", local_path=local_path + os.sep + ".",
            )
            self.controller.validate_path_pair_relocation(existing, updated)

    def test_reserved_relocation_rejects_alias_repoint_before_activation(self):
        with tempfile.TemporaryDirectory() as root:
            original = os.path.join(root, "original")
            replacement = os.path.join(root, "replacement")
            alias = os.path.join(root, "alias")
            os.mkdir(original); os.mkdir(replacement)
            try:
                os.symlink(original, alias, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest("symlink test unavailable: {}".format(exc))
            existing = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=original)
            updated = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=alias)
            self.controller.reserve_path_pair_relocation(existing, updated)
            # Compensation persists the old pair while retaining the fence.
            self.controller._Controller__validate_reserved_relocation_identities({"movies": existing})
            # A broken new alias must not prevent rollback to the unchanged
            # old root while the reservation remains held.
            os.unlink(alias)
            os.symlink(replacement, alias, target_is_directory=True)
            self.controller._Controller__validate_reserved_relocation_identities({"movies": existing})
            # Swap the old configured path after reservation; a compensating
            # old-path activation must reject it rather than restoring onto a
            # replacement directory.
            os.rename(original, os.path.join(root, "moved-original"))
            os.mkdir(original)
            with self.assertRaises(PathPairError):
                self.controller._Controller__validate_reserved_relocation_identities({"movies": existing})
            self.controller.release_path_pair_relocation("movies")

    def test_enabled_path_pair_relocation_rejects_active_work(self):
        with tempfile.TemporaryDirectory() as local_path:
            existing = PathPair(
                id="movies", name="Movies", remote_path="/remote/movies", local_path=local_path,
            )
            updated = PathPair(
                id="movies", name="Movies", remote_path="/remote/movies", local_path=local_path + os.sep + ".",
            )
            self.controller._Controller__active_downloading_file_names = [("Release", "movies", "Movies")]
            with self.assertRaises(PathPairError):
                self.controller.validate_path_pair_relocation(existing, updated)

    def test_enabled_path_pair_relocation_rejects_parent_owned_worker_dispatches(self):
        with tempfile.TemporaryDirectory() as local_path:
            existing = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=local_path)
            updated = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=local_path + os.sep + ".")
            file_id = ModelFile.build_file_id("Release", "movies")
            self.controller._Controller__pending_extract_file_ids = {file_id}
            with self.assertRaises(PathPairError):
                self.controller.validate_path_pair_relocation(existing, updated)

    def test_relocation_and_dequeued_queue_dispatch_have_an_atomic_busy_boundary(self):
        with tempfile.TemporaryDirectory() as local_path:
            existing = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=local_path)
            updated = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=local_path + os.sep + ".")
            file_id = ModelFile.build_file_id("Release", "movies")

            self.assertTrue(self.controller._Controller__begin_command_dispatch(file_id, "movies"))
            with self.assertRaises(PathPairError):
                self.controller.reserve_path_pair_relocation(existing, updated)
            self.controller._Controller__end_command_dispatch(file_id)

            self.controller.reserve_path_pair_relocation(existing, updated)
            self.assertFalse(self.controller._Controller__begin_command_dispatch(file_id, "movies"))
            self.controller.release_path_pair_relocation("movies")

    def test_dequeued_delete_local_and_queue_are_fenced_against_relocation_in_both_orders(self):
        with tempfile.TemporaryDirectory() as local_path:
            existing = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=local_path)
            updated = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=local_path + os.sep + ".")
            file = ModelFile("Release", False)
            file.path_pair_id = "movies"
            file.local_size = 10
            file.remote_size = 10
            self.controller._Controller__model.get_file.return_value = file
            self.controller._Controller__path_pairs_by_id = {"movies": existing}

            def delete_side_effect(*_args, **_kwargs):
                with self.assertRaises(PathPairError):
                    self.controller.reserve_path_pair_relocation(existing, updated)

            with patch.object(
                self.controller, "_Controller__queue_delete_local_process", side_effect=delete_side_effect
            ):
                self.controller._Controller__command_queue.put(
                    Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
                )
                self.controller._Controller__process_commands()
            self.controller.reserve_path_pair_relocation(existing, updated)
            self.controller.release_path_pair_relocation("movies")

            callback = MagicMock()
            queue_command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
            queue_command.add_callback(callback)
            self.controller.reserve_path_pair_relocation(existing, updated)
            self.controller._Controller__command_queue.put(queue_command)
            self.controller._Controller__process_commands()
            callback.on_failure.assert_called_once_with("Path pair relocation is in progress", 409)
            self.controller._Controller__lftp.queue.assert_not_called()
            self.controller.release_path_pair_relocation("movies")

    def test_model_updater_and_autoqueue_wait_for_pair_reconciliation_before_queueing(self):
        """Exercise scan -> real model -> real listener sequencing for one pair."""
        pair = PathPair(
            id="movies", name="Movies", remote_path="/remote/movies", local_path="/local/movies",
            enabled=True, auto_queue=True,
        )
        updater, auto_queue = self._configure_real_model_autoqueue_pipeline(pair, auto_delete_remote=True)
        remote = self._pair_system_file("Release", 10, pair.id)
        complete_local = self._pair_system_file("Release", 10, pair.id)

        # Remote-first must not queue until a healthy local root scan arrives;
        # when it does, the same-size local file is already complete.
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = self._scan_result([remote], pair.id)
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        updater.update()
        auto_queue.process()
        self.assertTrue(self.controller._Controller__command_queue.empty())

        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = self._scan_result([complete_local], pair.id)
        updater.update()
        auto_queue.process()
        self.assertTrue(self.controller._Controller__command_queue.empty())

        # Local-first likewise remains quiet until remote reconciliation, and
        # the matching complete remote result must not queue or delete it.
        self.setUp()
        updater, auto_queue = self._configure_real_model_autoqueue_pipeline(pair, auto_delete_remote=True)
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = self._scan_result([complete_local], pair.id)
        updater.update()
        auto_queue.process()
        self.assertTrue(self.controller._Controller__command_queue.empty())

        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = self._scan_result([remote], pair.id)
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        updater.update()
        auto_queue.process()
        self.assertTrue(self.controller._Controller__command_queue.empty())

        # Presence, not a non-zero byte count, blocks auto-queue when a
        # conflicting local file is discovered before its remote counterpart.
        self.setUp()
        updater, auto_queue = self._configure_real_model_autoqueue_pipeline(pair, auto_delete_remote=True)
        zero_local = self._pair_system_file("ZeroConflict", 0, pair.id)
        remote_conflict = self._pair_system_file("ZeroConflict", 10, pair.id)
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = self._scan_result([zero_local], pair.id)
        updater.update()
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = self._scan_result([remote_conflict], pair.id)
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        updater.update()
        auto_queue.process()
        self.assertTrue(self.controller._Controller__command_queue.empty())

        # Remote-only content queues only after the authoritative empty local
        # scan for the same pair; a failed local scan remains non-authoritative.
        self.setUp()
        updater, auto_queue = self._configure_real_model_autoqueue_pipeline(pair, auto_delete_remote=True)
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = self._scan_result([remote], pair.id)
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = self._scan_result([], pair.id, failed=True)
        updater.update()
        auto_queue.process()
        self.assertTrue(self.controller._Controller__command_queue.empty())

        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = self._scan_result([], pair.id)
        updater.update()
        auto_queue.process()
        queued = self.controller._Controller__command_queue.get_nowait()
        self.assertEqual(Controller.Command.Action.QUEUE, queued.action)
        self.assertEqual(ModelFile.build_file_id("Release", pair.id), queued.filename)

        # A persisted marker alone is historical startup state, not proof that
        # this process published a fast DEFAULT -> DOWNLOADED transfer.
        completed = ModelFile("Release", False)
        completed.path_pair_id = pair.id
        completed.remote_size = 10
        completed.local_size = 10
        completed.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__persist.final_move_succeeded_file_names.add(completed.file_id)
        self.controller._Controller__model.update_file(completed)
        auto_queue.process()
        self.assertTrue(self.controller._Controller__command_queue.empty())

        fast_default = ModelFile("Release", False)
        fast_default.path_pair_id = pair.id
        fast_default.remote_size = 10
        self.controller._Controller__model.update_file(fast_default)
        auto_queue.process()
        # This marker is set only after a real COMPLETED final publication in
        # the current controller process, so it authorizes the skipped-status
        # completion without weakening initial reconciliation safety.
        self.controller._Controller__current_process_final_publication_file_ids.add(completed.file_id)
        self.controller._Controller__model.update_file(completed)
        auto_queue.process()
        delete_command = self.controller._Controller__command_queue.get_nowait()
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, delete_command.action)
        self.assertEqual(completed.file_id, delete_command.filename)

    def test_path_pair_refresh_nested_restore_failure_records_consistency_error_and_restores_parent_references(self):
        self.controller._Controller__started = True
        old_pairs = {"old": PathPair(id="old", name="Old", remote_path="/remote", local_path="/local")}
        old_staging = {"old": "/local/incomplete"}
        self.controller._Controller__path_pairs_by_id = old_pairs
        self.controller._Controller__path_pair_staging_paths = old_staging
        old_runtime = (
            self.controller._Controller__active_scanner, self.controller._Controller__local_scanner,
            self.controller._Controller__remote_scanner, self.controller._Controller__active_scan_process,
            self.controller._Controller__local_scan_process, self.controller._Controller__remote_scan_process,
        )
        new_process = MagicMock()
        new_process.set_mp_log_queue.side_effect = RuntimeError("late activation failure")

        def apply_new_runtime():
            self.controller._Controller__path_pairs_by_id = {"new": MagicMock()}
            self.controller._Controller__path_pair_staging_paths = {"new": "/new/incomplete"}
            self.controller._Controller__active_scanner = MagicMock()
            self.controller._Controller__local_scanner = MagicMock()
            self.controller._Controller__remote_scanner = MagicMock()
            self.controller._Controller__active_scan_process = new_process
            self.controller._Controller__local_scan_process = MagicMock()
            self.controller._Controller__remote_scan_process = MagicMock()

        with patch.object(self.controller, "_Controller__refresh_path_pair_runtime_state", side_effect=apply_new_runtime), \
                patch.object(self.controller, "_Controller__restore_path_pair_runtime_state", side_effect=RuntimeError("restore failure")):
            self.controller._Controller__apply_path_pair_refresh()

        self.assertIn("consistency restore failed", self.controller._Controller__path_pair_runtime_error)
        self.assertFalse(self.controller._Controller__context.status.server.up)
        self.assertEqual(old_pairs, self.controller._Controller__path_pairs_by_id)
        self.assertEqual(old_staging, self.controller._Controller__path_pair_staging_paths)
        self.assertEqual(old_runtime, (
            self.controller._Controller__active_scanner, self.controller._Controller__local_scanner,
            self.controller._Controller__remote_scanner, self.controller._Controller__active_scan_process,
            self.controller._Controller__local_scan_process, self.controller._Controller__remote_scan_process,
        ))
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, ModelFile.build_file_id("Release", "old"))
        command.add_callback(callback)
        self.controller.queue_command(command)
        callback.on_failure.assert_called_once_with(
            self.controller._Controller__path_pair_runtime_error,
            503,
        )
        self.assertTrue(self.controller._Controller__command_queue.empty())

    def test_path_pair_refresh_rolls_back_new_runtime_despite_preexisting_error(self):
        self.controller._Controller__started = True
        self.controller._Controller__path_pair_runtime_error = "old runtime error"
        old_pairs = {"old": PathPair(id="old", name="Old", remote_path="/remote", local_path="/local")}
        old_staging = {"old": "/local/incomplete"}
        self.controller._Controller__path_pairs_by_id = old_pairs
        self.controller._Controller__path_pair_staging_paths = old_staging
        old_runtime = (
            self.controller._Controller__active_scanner, self.controller._Controller__local_scanner,
            self.controller._Controller__remote_scanner, self.controller._Controller__active_scan_process,
            self.controller._Controller__local_scan_process, self.controller._Controller__remote_scan_process,
        )
        new_active_process = MagicMock()
        new_active_process.set_mp_log_queue.side_effect = RuntimeError("post-activation failure")
        new_local_process = MagicMock()
        new_remote_process = MagicMock()

        def apply_new_runtime():
            self.controller._Controller__path_pairs_by_id = {"new": MagicMock()}
            self.controller._Controller__path_pair_staging_paths = {"new": "/new/incomplete"}
            self.controller._Controller__active_scanner = MagicMock()
            self.controller._Controller__local_scanner = MagicMock()
            self.controller._Controller__remote_scanner = MagicMock()
            self.controller._Controller__active_scan_process = new_active_process
            self.controller._Controller__local_scan_process = new_local_process
            self.controller._Controller__remote_scan_process = new_remote_process

        with patch.object(self.controller, "_Controller__refresh_path_pair_runtime_state", side_effect=apply_new_runtime):
            self.controller._Controller__apply_path_pair_refresh()

        new_active_process.terminate.assert_called_once()
        new_local_process.terminate.assert_called_once()
        new_remote_process.terminate.assert_called_once()
        self.assertEqual(old_pairs, self.controller._Controller__path_pairs_by_id)
        self.assertEqual(old_staging, self.controller._Controller__path_pair_staging_paths)
        self.assertEqual(old_runtime, (
            self.controller._Controller__active_scanner, self.controller._Controller__local_scanner,
            self.controller._Controller__remote_scanner, self.controller._Controller__active_scan_process,
            self.controller._Controller__local_scan_process, self.controller._Controller__remote_scan_process,
        ))
        self.assertIn("activation failed", self.controller._Controller__path_pair_runtime_error)

    def test_path_pair_runtime_inner_restore_failure_fails_closed_without_overwrite(self):
        self.controller._Controller__started = False
        old_pairs = {"old": PathPair(id="old", name="Old", remote_path="/remote", local_path="/local")}
        old_staging = {"old": "/local/incomplete"}
        self.controller._Controller__path_pairs_by_id = old_pairs
        self.controller._Controller__path_pair_staging_paths = old_staging
        old_runtime = (
            self.controller._Controller__active_scanner, self.controller._Controller__local_scanner,
            self.controller._Controller__remote_scanner, self.controller._Controller__active_scan_process,
            self.controller._Controller__local_scan_process, self.controller._Controller__remote_scan_process,
        )
        pair = PathPair(id="new", name="New", remote_path="/remote/new", local_path="/local/new")
        self.controller._Controller__context.path_pair_manager = MagicMock()
        self.controller._Controller__context.path_pair_manager.get_enabled_pairs.return_value = [pair]
        self.controller._Controller__context.config.controller = SimpleNamespace(
            interval_ms_downloading_scan=100, interval_ms_local_scan=100, interval_ms_remote_scan=100,
        )
        self.controller._Controller__set_transfer_path_pairs = MagicMock(side_effect=[
            RuntimeError("activation mutation failed"), RuntimeError("old transfer restore failed"),
        ])
        with patch.object(self.controller, "_Controller__build_active_scanner", return_value=MagicMock()), \
                patch.object(self.controller, "_Controller__build_local_scanner", return_value=MagicMock()), \
                patch.object(self.controller, "_Controller__build_remote_scanner", return_value=MagicMock()):
            self.controller._Controller__apply_path_pair_refresh()

        error = self.controller._Controller__path_pair_runtime_error
        self.assertIn("consistency restore failed", error)
        self.assertIn("old transfer restore failed", error)
        self.assertFalse(self.controller._Controller__context.status.server.up)
        self.assertEqual(old_pairs, self.controller._Controller__path_pairs_by_id)
        self.assertEqual(old_staging, self.controller._Controller__path_pair_staging_paths)
        self.assertEqual(old_runtime, (
            self.controller._Controller__active_scanner, self.controller._Controller__local_scanner,
            self.controller._Controller__remote_scanner, self.controller._Controller__active_scan_process,
            self.controller._Controller__local_scan_process, self.controller._Controller__remote_scan_process,
        ))
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.QUEUE, ModelFile.build_file_id("Release", "old"))
        command.add_callback(callback)
        self.controller.queue_command(command)
        callback.on_failure.assert_called_once_with(error, 503)
    def test_interrupted_recovery_skips_stale_staging_when_final_target_is_complete(self):
        with tempfile.TemporaryDirectory() as root:
            final_root = os.path.join(root, "final")
            staging_root = os.path.join(root, "staging")
            os.mkdir(final_root)
            os.mkdir(staging_root)
            Path(os.path.join(final_root, "movie.mkv")).write_bytes(b"complete")
            Path(os.path.join(staging_root, "movie.mkv.lftp")).write_bytes(b"stale")
            pair = PathPair(id="movies", name="Movies", remote_path="/remote", local_path=final_root)
            self.controller._Controller__path_pairs_by_id = {pair.id: pair}
            self.controller._Controller__path_pair_staging_paths = {pair.id: staging_root}
            remote = SystemFile("movie.mkv", len(b"complete"), False)
            remote.path_pair_id = pair.id

            self.controller._Controller__recover_interrupted_downloads([remote])

            self.controller._Controller__lftp.queue.assert_not_called()

    def test_final_move_never_clobbers_existing_file_even_at_same_size(self):
        with tempfile.TemporaryDirectory() as root:
            staging = os.path.join(root, "staging")
            final = os.path.join(root, "final")
            os.mkdir(staging); os.mkdir(final)
            Path(os.path.join(staging, "movie.mkv")).write_bytes(b"source")
            Path(os.path.join(final, "movie.mkv")).write_bytes(b"target")
            self.controller._Controller__staging_path = staging
            self.controller._Controller__legacy_local_path = final
            result = self.controller._Controller__move_from_staging("movie.mkv")
            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, result)
            self.assertEqual(b"source", Path(os.path.join(staging, "movie.mkv")).read_bytes())
            self.assertEqual(b"target", Path(os.path.join(final, "movie.mkv")).read_bytes())

    def test_legacy_staging_conflict_is_not_remote_delete_eligible_until_published(self):
        file = ModelFile("movie.mkv", False)
        self.controller._Controller__legacy_local_path = "/local"
        self.controller._Controller__staging_path = "/local/incomplete"
        self.controller._Controller__persist.final_move_succeeded_file_names = set()
        self.assertFalse(self.controller.is_remote_delete_eligible(file))
        self.controller._Controller__persist.final_move_succeeded_file_names.add(file.file_id)
        self.assertTrue(self.controller.is_remote_delete_eligible(file))

    def test_current_publication_proof_clears_on_path_pair_refresh(self):
        self.controller._Controller__current_process_final_publication_file_ids = {"old-proof"}
        with patch.object(self.controller, "_Controller__refresh_path_pair_runtime_state"):
            self.controller._Controller__apply_path_pair_refresh()
        self.assertEqual(set(), self.controller._Controller__current_process_final_publication_file_ids)

    def test_current_publication_proof_clears_on_new_queue_lifecycle(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__current_process_final_publication_file_ids = {file.file_id}
        self.controller._Controller__command_queue.put(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.assertNotIn(file.file_id, self.controller._Controller__current_process_final_publication_file_ids)

    def test_new_queue_rejects_stale_auto_queue_remote_delete_in_same_drain(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__persist.final_move_succeeded_file_names = {file.file_id}
        self.controller._Controller__current_process_final_publication_file_ids = {file.file_id}
        callback = MagicMock()
        stale_delete = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id, origin="auto_queue")
        stale_delete.lifecycle_token = self.controller.remote_delete_lifecycle_token(file)
        stale_delete.add_callback(callback)
        self.controller._Controller__command_queue.put(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__command_queue.put(stale_delete)
        self.controller._Controller__process_commands()
        callback.on_failure.assert_called_once()
        self.assertEqual(409, callback.on_failure.call_args.args[1])
        self.assertEqual([], self.controller._Controller__active_command_processes)

    def test_delete_local_admission_rejects_stale_same_path_auto_remote_delete_in_same_drain(self):
        file = ModelFile("movie.mkv", False)
        file.local_size = 10
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__staging_path = self.controller._Controller__legacy_local_path
        self.controller._Controller__model.get_file.return_value = file
        stale_delete = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id, origin="auto_queue")
        stale_delete.lifecycle_token = self.controller.remote_delete_lifecycle_token(file)
        callback = MagicMock()
        stale_delete.add_callback(callback)
        self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id))
        self.controller.queue_command(stale_delete)

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process, \
                patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            delete_local_process.return_value = MagicMock()
            self.controller._Controller__process_commands()

        delete_local_process.return_value.start.assert_called_once_with()
        delete_remote_process.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Auto-queue remote delete no longer has current lifecycle authority", 409
        )

    def test_auto_queue_remote_delete_without_lifecycle_token_is_rejected(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__staging_path = self.controller._Controller__legacy_local_path
        self.controller._Controller__model.get_file.return_value = file
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id, origin="auto_queue")
        command.add_callback(callback)
        self.controller.queue_command(command)

        with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            self.controller._Controller__process_commands()

        delete_remote_process.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Auto-queue remote delete no longer has current lifecycle authority", 409
        )

    def test_auto_queue_remote_delete_with_mismatched_lifecycle_token_is_rejected(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__staging_path = self.controller._Controller__legacy_local_path
        self.controller._Controller__model.get_file.return_value = file
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id, origin="auto_queue")
        command.lifecycle_token = (1, 1)
        command.add_callback(callback)
        self.controller.queue_command(command)

        with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            self.controller._Controller__process_commands()

        delete_remote_process.assert_not_called()
        callback.on_failure.assert_called_once_with(
            "Auto-queue remote delete no longer has current lifecycle authority", 409
        )

    def test_auto_queue_remote_delete_with_current_token_allows_same_path_without_publication(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__staging_path = self.controller._Controller__legacy_local_path
        self.controller._Controller__model.get_file.return_value = file
        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id, origin="auto_queue")
        command.lifecycle_token = self.controller.remote_delete_lifecycle_token(file)
        self.controller.queue_command(command)

        with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            process = MagicMock()
            delete_remote_process.return_value = process
            self.controller._Controller__process_commands()

        process.start.assert_called_once_with()
        self.assertEqual(1, len(self.controller._Controller__active_command_processes))

    def test_manual_remote_delete_allows_same_path_without_lifecycle_token(self):
        file = ModelFile("movie.mkv", False)
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__staging_path = self.controller._Controller__legacy_local_path
        self.controller._Controller__model.get_file.return_value = file
        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, file.file_id)
        self.controller.queue_command(command)

        with patch("controller.controller.DeleteRemoteProcess") as delete_remote_process:
            process = MagicMock()
            delete_remote_process.return_value = process
            self.controller._Controller__process_commands()

        process.start.assert_called_once_with()
        self.assertEqual(1, len(self.controller._Controller__active_command_processes))

    def test_cross_device_file_publish_copies_then_removes_source_after_atomic_publish(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"payload")
            original = Controller._Controller__rename_no_replace
            calls = []

            def rename_side_effect(src, dst):
                calls.append((src, dst))
                if len(calls) == 1:
                    raise OSError(errno.EXDEV, "cross-device")
                return original(src, dst)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=rename_side_effect):
                Controller._Controller__publish_staging_no_replace(source, destination)
            self.assertFalse(os.path.exists(source))
            self.assertEqual(b"payload", Path(destination).read_bytes())
            self.assertEqual(2, len(calls))

    def test_cross_device_publish_collision_preserves_source_and_destination(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"source")
            Path(destination).write_bytes(b"existing")
            calls = 0

            def rename_side_effect(_src, _dst):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError(errno.EXDEV, "cross-device")
                raise FileExistsError(errno.EEXIST, "exists", destination)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=rename_side_effect):
                with self.assertRaises(FileExistsError):
                    Controller._Controller__publish_staging_no_replace(source, destination)
            self.assertEqual(b"source", Path(source).read_bytes())
            self.assertEqual(b"existing", Path(destination).read_bytes())
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_unsupported_initial_and_final_no_replace_publish_file_without_clobbering(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"payload")
            calls = []

            def unsupported_no_replace(src, dst):
                calls.append((src, dst))
                raise OSError(errno.EINVAL, "rename flags unsupported", dst)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=unsupported_no_replace):
                Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(2, len(calls))
            self.assertFalse(os.path.exists(source))
            self.assertEqual(b"payload", Path(destination).read_bytes())
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_unsupported_final_no_replace_uses_exclusive_copy_when_hardlinks_are_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"payload")
            calls = 0

            def rename_side_effect(_src, dst):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError(errno.EXDEV, "cross-device", dst)
                raise OSError(errno.EINVAL, "rename flags unsupported", dst)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=rename_side_effect), \
                    patch("controller.controller.os.link", side_effect=OSError(errno.ENOTSUP, "no hardlinks")):
                Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(2, calls)
            self.assertFalse(os.path.exists(source))
            self.assertEqual(b"payload", Path(destination).read_bytes())

    def test_exclusive_copy_failure_retains_partial_target_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"payload")
            calls = 0

            def rename_side_effect(_src, dst):
                nonlocal calls
                calls += 1
                raise OSError(errno.EXDEV if calls == 1 else errno.EINVAL, "unsupported", dst)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=rename_side_effect), \
                    patch("controller.controller.os.link", side_effect=OSError(errno.ENOTSUP, "no hardlinks")), \
                    patch("controller.controller.shutil.copyfileobj", side_effect=OSError(errno.ENOSPC, "full")):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.ENOSPC, error.exception.errno)
            self.assertEqual(b"payload", Path(source).read_bytes())
            self.assertTrue(os.path.lexists(destination))
            self.assertEqual(0, os.path.getsize(destination))
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_portable_publish_syncs_read_only_source_before_copying_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"payload")
            os.chmod(source, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EINVAL, "rename flags unsupported", destination),
            ):
                Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertFalse(os.path.exists(source))
            self.assertEqual(b"payload", Path(destination).read_bytes())
            self.assertEqual(stat.S_IRUSR, stat.S_IMODE(os.lstat(destination).st_mode) & stat.S_IRUSR)

    def test_portable_publish_preserves_nested_symlink_without_leaving_private_temporary(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "release")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "release")
            os.makedirs(source); os.mkdir(destination_parent)
            os.symlink("movie.mkv", os.path.join(source, "movie.link"))

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EINVAL, "rename flags unsupported", destination),
            ):
                Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertFalse(os.path.exists(source))
            self.assertTrue(os.path.islink(os.path.join(destination, "movie.link")))
            self.assertEqual("movie.mkv", os.readlink(os.path.join(destination, "movie.link")))
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_unsupported_directory_publish_uses_exclusive_directory_reservation(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "release")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "release")
            os.makedirs(os.path.join(source, "nested")); os.mkdir(destination_parent)
            Path(os.path.join(source, "nested", "movie.mkv")).write_bytes(b"payload")
            original = Controller._Controller__rename_no_replace
            calls = 0

            def rename_side_effect(src, dst):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError(errno.EXDEV, "cross-device", dst)
                if calls == 2:
                    raise OSError(errno.EINVAL, "rename flags unsupported", dst)
                return original(src, dst)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=rename_side_effect):
                Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertFalse(os.path.exists(source))
            self.assertEqual(b"payload", Path(os.path.join(destination, "nested", "movie.mkv")).read_bytes())
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_capability_fallback_does_not_mask_permission_failures(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"payload")

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EACCES, "permission denied", destination),
            ):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EACCES, error.exception.errno)
            self.assertEqual(b"payload", Path(source).read_bytes())
            self.assertFalse(os.path.exists(destination))
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_target_created_during_portable_final_publish_preserves_source(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"source")
            calls = 0

            def rename_side_effect(_src, dst):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError(errno.EXDEV, "cross-device", dst)
                Path(destination).write_bytes(b"racer")
                raise FileExistsError(errno.EEXIST, "exists", destination)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=rename_side_effect):
                with self.assertRaises(FileExistsError):
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(b"source", Path(source).read_bytes())
            self.assertEqual(b"racer", Path(destination).read_bytes())
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_temporary_directory_copy_failure_cleans_private_root_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "release")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "release")
            os.makedirs(source); os.mkdir(destination_parent)
            Path(os.path.join(source, "movie.mkv")).write_bytes(b"payload")

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EXDEV, "cross-device", destination),
            ), patch("controller.controller.shutil.copytree", side_effect=OSError(errno.ENOSPC, "full")):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.ENOSPC, error.exception.errno)
            self.assertTrue(os.path.isdir(source))
            self.assertFalse(os.path.lexists(destination))
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_source_change_during_portable_copy_keeps_source_and_does_not_delete_it(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"source")
            original_copyfile = shutil.copyfile

            def copy_then_change(*args, **kwargs):
                result = original_copyfile(*args, **kwargs)
                Path(source).write_bytes(b"changed")
                return result

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EINVAL, "rename flags unsupported", destination),
            ), patch("controller.controller.shutil.copyfile", side_effect=copy_then_change):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertEqual(b"changed", Path(source).read_bytes())
            self.assertEqual(b"source", Path(destination).read_bytes())
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_source_replaced_after_stability_check_survives_portable_publication(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            replacement = os.path.join(root, "staging", "replacement.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"source")
            Path(replacement).write_bytes(b"replacement")
            original_snapshot = Controller._Controller__source_tree_snapshot
            snapshot_calls = 0

            def snapshot_then_replace(path, *args, **kwargs):
                nonlocal snapshot_calls
                result = original_snapshot(path)
                snapshot_calls += 1
                if snapshot_calls == 3:
                    os.replace(replacement, source)
                return result

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EINVAL, "rename flags unsupported", destination),
            ), patch.object(Controller, "_Controller__source_tree_snapshot", side_effect=snapshot_then_replace):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertEqual(b"replacement", Path(source).read_bytes())
            self.assertEqual(b"source", Path(destination).read_bytes())

    def test_source_replaced_after_cleanup_check_is_retained_under_private_claim(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            replacement = os.path.join(root, "staging", "replacement.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"source")
            Path(replacement).write_bytes(b"replacement")
            original_same_path_identity = Controller._Controller__same_path_identity
            source_identity_checks = 0

            def swap_after_cleanup_check(path, expected):
                nonlocal source_identity_checks
                result = original_same_path_identity(path, expected)
                if path == source:
                    source_identity_checks += 1
                    if source_identity_checks == 1:
                        os.replace(replacement, source)
                return result

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EINVAL, "rename flags unsupported", destination),
            ), patch.object(Controller, "_Controller__same_path_identity", side_effect=swap_after_cleanup_check):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            claims = [entry for entry in os.listdir(os.path.dirname(source)) if entry.startswith(".seedsync-retire-")]
            self.assertEqual(1, len(claims))
            self.assertEqual(b"replacement", Path(os.path.join(os.path.dirname(source), claims[0])).read_bytes())
            self.assertEqual(b"source", Path(destination).read_bytes())

    def test_cleanup_claim_syncs_parent_before_mismatch_validation_and_retention(self):
        with tempfile.TemporaryDirectory() as root:
            source_parent = os.path.join(root, "staging")
            source = os.path.join(source_parent, "movie.mkv")
            os.mkdir(source_parent)
            Path(source).write_bytes(b"source")
            source_stat = os.lstat(source)
            source_snapshot = Controller._Controller__source_tree_snapshot(source, ignore_root_ctime=True)
            events = []

            def sync_parent(path):
                self.assertEqual(source_parent, path)
                events.append("sync")

            def mismatched_snapshot(path, *args, **kwargs):
                self.assertTrue(events)
                self.assertEqual("sync", events[0])
                self.assertTrue(os.path.basename(path).startswith(".seedsync-retire-"))
                events.append("validate")
                return b"replacement"

            with patch.object(Controller, "_Controller__sync_directory_if_supported", side_effect=sync_parent), \
                    patch.object(Controller, "_Controller__source_tree_snapshot", side_effect=mismatched_snapshot):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__remove_published_source(source, source_stat, source_snapshot)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertEqual(["sync", "validate", "sync"], events)
            claims = [entry for entry in os.listdir(source_parent) if entry.startswith(".seedsync-retire-")]
            self.assertEqual(1, len(claims))
            self.assertEqual(b"source", Path(os.path.join(source_parent, claims[0])).read_bytes())

    def test_same_target_symlink_replacement_fails_identity_proof_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "release")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "release")
            destination_link = os.path.join(destination, "movie.link")
            displaced_link = os.path.join(destination_parent, "displaced.link")
            os.makedirs(source); os.mkdir(destination_parent)
            os.symlink("movie.mkv", os.path.join(source, "movie.link"))

            def replace_destination_after_symlink_publish(path):
                if path == destination:
                    os.rename(destination_link, displaced_link)
                    os.symlink("movie.mkv", destination_link)

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EINVAL, "rename flags unsupported", destination),
            ), patch.object(
                Controller,
                "_Controller__sync_directory_if_supported",
                side_effect=replace_destination_after_symlink_publish,
            ):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertTrue(os.path.islink(os.path.join(source, "movie.link")))
            self.assertTrue(os.path.islink(destination_link))
            self.assertEqual("movie.mkv", os.readlink(destination_link))

    def test_temporary_file_sync_uses_writable_descriptor_for_windows_compatibility(self):
        with tempfile.TemporaryDirectory() as root:
            temporary_path = os.path.join(root, "payload")
            Path(temporary_path).write_bytes(b"payload")
            with patch("controller.controller.os.open", return_value=42) as open_file, \
                    patch("controller.controller.os.fsync"), \
                    patch("controller.controller.os.close"), \
                    patch("controller.controller.os.name", "nt"):
                Controller._Controller__sync_publish_temporary(temporary_path)

            self.assertTrue(open_file.call_args[0][1] & os.O_RDWR)

    def test_nested_directory_portable_publish_does_not_recursively_recopy_temporary_children(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "release")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "release")
            os.makedirs(os.path.join(source, "nested")); os.mkdir(destination_parent)
            Path(os.path.join(source, "nested", "movie.mkv")).write_bytes(b"payload")
            original_copytree = shutil.copytree

            with patch.object(
                Controller,
                "_Controller__rename_no_replace",
                side_effect=OSError(errno.EINVAL, "rename flags unsupported", destination),
            ), patch("controller.controller.shutil.copytree", wraps=original_copytree) as copytree:
                Controller._Controller__publish_staging_no_replace(source, destination)

            # copytree recursively calls itself once for the source's nested
            # directory.  No call may use a destination-side private child as
            # a fresh source copy.
            self.assertEqual(2, copytree.call_count)
            self.assertTrue(all(os.fspath(call.args[0]).startswith(source) for call in copytree.call_args_list))
            self.assertFalse(os.path.exists(source))
            self.assertEqual(b"payload", Path(os.path.join(destination, "nested", "movie.mkv")).read_bytes())
            self.assertEqual([], [p for p in os.listdir(destination_parent) if p.startswith(".seedsync-publish-")])

    def test_post_sync_replacement_fails_hardlink_and_exclusive_publication(self):
        for hardlinks_available in (True, False):
            with self.subTest(hardlinks_available=hardlinks_available), tempfile.TemporaryDirectory() as root:
                temporary_path = os.path.join(root, "temporary")
                destination = os.path.join(root, "destination")
                racer = os.path.join(root, "racer")
                Path(temporary_path).write_bytes(b"payload")
                Path(racer).write_bytes(b"racer")

                def replace_after_sync(_path):
                    os.replace(racer, destination)

                patches = [patch.object(Controller, "_Controller__sync_directory_if_supported", side_effect=replace_after_sync)]
                if not hardlinks_available:
                    patches.append(patch("controller.controller.os.link", side_effect=OSError(errno.ENOTSUP, "no hardlinks")))
                with patches[0]:
                    if len(patches) == 2:
                        with patches[1]:
                            with self.assertRaises(OSError) as error:
                                Controller._Controller__publish_temporary_no_replace(temporary_path, destination)
                    else:
                        with self.assertRaises(OSError) as error:
                            Controller._Controller__publish_temporary_no_replace(temporary_path, destination)

                self.assertEqual(errno.EAGAIN, error.exception.errno)
                self.assertEqual(b"racer", Path(destination).read_bytes())

    def test_post_sync_same_inode_mutation_fails_file_publication_manifests(self):
        for hardlinks_available in (True, False):
            with self.subTest(hardlinks_available=hardlinks_available), tempfile.TemporaryDirectory() as root:
                temporary_path = os.path.join(root, "temporary")
                destination = os.path.join(root, "destination")
                Path(temporary_path).write_bytes(b"payload")

                def mutate_after_sync(_path):
                    Path(destination).write_bytes(b"racer!!")
                    changed_time = os.stat(destination).st_mtime_ns + 1_000_000_000
                    os.utime(destination, ns=(changed_time, changed_time))

                patches = [patch.object(Controller, "_Controller__sync_directory_if_supported", side_effect=mutate_after_sync)]
                if not hardlinks_available:
                    patches.append(patch("controller.controller.os.link", side_effect=OSError(errno.ENOTSUP, "no hardlinks")))
                with patches[0]:
                    if len(patches) == 2:
                        with patches[1]:
                            with self.assertRaises(OSError) as error:
                                Controller._Controller__publish_temporary_no_replace(temporary_path, destination)
                    else:
                        with self.assertRaises(OSError) as error:
                            Controller._Controller__publish_temporary_no_replace(temporary_path, destination)

                self.assertEqual(errno.EAGAIN, error.exception.errno)
                self.assertEqual(b"racer!!", Path(destination).read_bytes())

        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            destination = os.path.join(root, "destination")
            Path(source).write_bytes(b"payload")
            mutated = False

            def mutate_native_after_sync(_path):
                nonlocal mutated
                if os.path.exists(destination) and not mutated:
                    mutated = True
                    Path(destination).write_bytes(b"racer!!")
                    changed_time = os.stat(destination).st_mtime_ns + 1_000_000_000
                    os.utime(destination, ns=(changed_time, changed_time))

            with patch.object(Controller, "_Controller__sync_directory_if_supported", side_effect=mutate_native_after_sync):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertEqual(b"racer!!", Path(destination).read_bytes())

    def test_post_sync_replacement_fails_directory_and_native_publication(self):
        with tempfile.TemporaryDirectory() as root:
            temporary_path = os.path.join(root, "temporary")
            destination = os.path.join(root, "destination")
            moved = os.path.join(root, "moved")
            os.mkdir(temporary_path)
            Path(os.path.join(temporary_path, "movie.mkv")).write_bytes(b"payload")

            def replace_directory_after_sync(path):
                if path == destination:
                    os.rename(destination, moved)
                    os.mkdir(destination)

            with patch.object(Controller, "_Controller__sync_directory_if_supported", side_effect=replace_directory_after_sync):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_temporary_no_replace(temporary_path, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertTrue(os.path.isdir(moved))

        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            destination = os.path.join(root, "destination")
            moved = os.path.join(root, "moved")
            Path(source).write_bytes(b"source")

            def replace_native_after_sync(path):
                if path == root and os.path.exists(destination) and not os.path.exists(moved):
                    os.rename(destination, moved)
                    Path(destination).write_bytes(b"racer")

            with patch.object(Controller, "_Controller__sync_directory_if_supported", side_effect=replace_native_after_sync):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertEqual(b"source", Path(moved).read_bytes())
            self.assertEqual(b"racer", Path(destination).read_bytes())

    def test_exclusive_publication_never_copystats_the_public_destination(self):
        with tempfile.TemporaryDirectory() as root:
            temporary_path = os.path.join(root, "temporary")
            destination = os.path.join(root, "destination")
            Path(temporary_path).write_bytes(b"payload")
            with patch("controller.controller.shutil.copystat") as copy_stat, \
                    patch("controller.controller.os.link", side_effect=OSError(errno.ENOTSUP, "no hardlinks")):
                Controller._Controller__publish_temporary_no_replace(temporary_path, destination)

            copy_stat.assert_not_called()

    def test_directory_publication_rejects_nested_mount_before_copy(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "release")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "release")
            nested = os.path.join(source, "nested")
            os.makedirs(nested); os.mkdir(destination_parent)
            Path(os.path.join(nested, "movie.mkv")).write_bytes(b"payload")

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=OSError(errno.EINVAL, "unsupported", destination)), \
                    patch("controller.controller.os.path.ismount", side_effect=lambda path: path == nested):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EXDEV, error.exception.errno)
            self.assertTrue(os.path.isdir(source))
            self.assertFalse(os.path.exists(destination))

    def test_linux_mountinfo_rejects_same_device_bind_with_escaped_nested_path(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "safe root")
            nested = os.path.join(source, "nested\tpath")
            os.makedirs(nested)
            escaped_nested = nested.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")
            mountinfo = "42 35 0:1 / " + escaped_nested + " rw,relatime - ext4 /dev/loop0 rw\n"
            with patch("controller.controller.sys.platform", "linux"), \
                    patch("builtins.open", mock_open(read_data=mountinfo)), \
                    patch("controller.controller.os.path.ismount", return_value=False):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__reject_nested_mounts_or_reparse_points(source)

            self.assertEqual(errno.EXDEV, error.exception.errno)

    def test_linux_mountinfo_allows_source_root_and_non_nested_mounts(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            outside = os.path.join(root, "outside")
            os.mkdir(source); os.mkdir(outside)
            mountinfo = "42 35 0:1 / " + source + " rw - ext4 /dev/loop0 rw\n" + \
                        "43 35 0:2 / " + outside + " rw - ext4 /dev/loop1 rw\n"
            with patch("controller.controller.sys.platform", "linux"), \
                    patch("builtins.open", mock_open(read_data=mountinfo)), \
                    patch("controller.controller.os.path.ismount", return_value=False):
                Controller._Controller__reject_nested_mounts_or_reparse_points(source)

    def test_reparse_point_source_root_is_rejected(self):
        root_stat = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=1)
        with patch("controller.controller.stat.FILE_ATTRIBUTE_REPARSE_POINT", 1, create=True), \
                patch("controller.controller.os.lstat", return_value=root_stat):
            with self.assertRaises(OSError) as error:
                Controller._Controller__reject_nested_mounts_or_reparse_points("C:\\staging")

        self.assertEqual(errno.EXDEV, error.exception.errno)

    @unittest.skipIf(os.name == "nt", "fd metadata expectations are POSIX-specific")
    def test_exclusive_publish_preserves_mode_and_mtime_through_owned_descriptor(self):
        with tempfile.TemporaryDirectory() as root:
            temporary_path = os.path.join(root, "temporary")
            destination = os.path.join(root, "destination")
            Path(temporary_path).write_bytes(b"payload")
            os.chmod(temporary_path, 0o640)
            timestamp = 1_700_000_000_123_456_789
            os.utime(temporary_path, ns=(timestamp, timestamp))
            with patch("controller.controller.os.link", side_effect=OSError(errno.ENOTSUP, "no hardlinks")):
                Controller._Controller__publish_temporary_no_replace(temporary_path, destination)

            destination_stat = os.stat(destination)
            self.assertEqual(0o640, stat.S_IMODE(destination_stat.st_mode))
            self.assertEqual(timestamp, destination_stat.st_mtime_ns)

    def test_windows_exclusive_publish_ignores_unavailable_mode_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            temporary_path = os.path.join(root, "temporary")
            destination = os.path.join(root, "destination")
            Path(temporary_path).write_bytes(b"payload")
            os.chmod(temporary_path, 0o444)
            os.utime(temporary_path, ns=(1_700_000_000_123_456_789,) * 2)

            with patch("controller.controller.os.name", "nt"), \
                    patch("controller.controller.os.link", side_effect=OSError(errno.ENOTSUP, "no hardlinks")):
                Controller._Controller__publish_temporary_no_replace(temporary_path, destination)

            self.assertEqual(b"payload", Path(destination).read_bytes())
            self.assertFalse(os.path.exists(temporary_path))

    def test_directory_manifest_rejects_child_replacement_after_sync(self):
        with tempfile.TemporaryDirectory() as root:
            temporary_path = os.path.join(root, "temporary")
            destination = os.path.join(root, "destination")
            child = os.path.join(temporary_path, "movie.mkv")
            os.mkdir(temporary_path)
            Path(child).write_bytes(b"payload")

            def replace_child_after_sync(path):
                if path == destination:
                    destination_child = os.path.join(destination, "movie.mkv")
                    Path(destination_child).write_bytes(b"racer!!")
                    changed_time = os.stat(destination_child).st_mtime_ns + 1_000_000_000
                    os.utime(destination_child, ns=(changed_time, changed_time))

            with patch.object(Controller, "_Controller__sync_directory_if_supported", side_effect=replace_child_after_sync):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_temporary_no_replace(temporary_path, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertTrue(os.path.isdir(destination))
            self.assertEqual(b"racer!!", Path(os.path.join(destination, "movie.mkv")).read_bytes())

    def test_same_size_restored_mtime_source_mutation_blocks_retirement(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"source")
            original_mtime = os.stat(source).st_mtime_ns
            original_copyfile = shutil.copyfile

            def copy_then_restore_mtime(*args, **kwargs):
                result = original_copyfile(*args, **kwargs)
                Path(source).write_bytes(b"change")
                os.utime(source, ns=(original_mtime, original_mtime))
                os.chmod(source, 0o600)
                return result

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=OSError(errno.EINVAL, "unsupported", destination)), \
                    patch("controller.controller.shutil.copyfile", side_effect=copy_then_restore_mtime):
                with self.assertRaises(OSError) as error:
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(errno.EAGAIN, error.exception.errno)
            self.assertEqual(b"change", Path(source).read_bytes())

    def test_cleanup_error_retains_private_temporary_without_masking_publication_error(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "staging", "movie.mkv")
            destination_parent = os.path.join(root, "final")
            destination = os.path.join(destination_parent, "movie.mkv")
            os.makedirs(os.path.dirname(source)); os.mkdir(destination_parent)
            Path(source).write_bytes(b"source")
            original_unlink = os.unlink
            calls = 0

            def rename_then_conflict(_src, dst):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError(errno.EXDEV, "cross-device", dst)
                raise FileExistsError(errno.EEXIST, "exists", dst)

            def fail_private_cleanup(path, *args, **kwargs):
                if os.path.basename(path).startswith(".seedsync-publish-"):
                    raise OSError(errno.EACCES, "cleanup denied", path)
                return original_unlink(path, *args, **kwargs)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=rename_then_conflict), \
                    patch("controller.controller.os.unlink", side_effect=fail_private_cleanup):
                with self.assertRaises(FileExistsError):
                    Controller._Controller__publish_staging_no_replace(source, destination)

            self.assertEqual(b"source", Path(source).read_bytes())
            self.assertTrue(any(entry.startswith(".seedsync-publish-") for entry in os.listdir(destination_parent)))

    def test_final_move_never_clobbers_existing_directory(self):
        with tempfile.TemporaryDirectory() as root:
            staging = os.path.join(root, "staging")
            final = os.path.join(root, "final")
            os.mkdir(staging); os.mkdir(final)
            os.mkdir(os.path.join(staging, "release"))
            os.mkdir(os.path.join(final, "release"))
            Path(os.path.join(staging, "release", "movie.mkv")).write_bytes(b"source")
            Path(os.path.join(final, "release", "movie.mkv")).write_bytes(b"target")
            self.controller._Controller__staging_path = staging
            self.controller._Controller__legacy_local_path = final
            result = self.controller._Controller__move_from_staging("release")
            self.assertEqual(Controller.MoveFromStagingResult.DEFERRED, result)
            self._settle_collision_compare()
            result = self.controller._Controller__move_from_staging("release")
            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, result)
            self.assertTrue(os.path.isdir(os.path.join(staging, "release")))
            self.assertEqual(b"target", Path(os.path.join(final, "release", "movie.mkv")).read_bytes())

    def test_final_move_race_created_target_preserves_staging_source(self):
        with tempfile.TemporaryDirectory() as root:
            staging = os.path.join(root, "staging")
            final = os.path.join(root, "final")
            os.mkdir(staging); os.mkdir(final)
            src = os.path.join(staging, "movie.mkv")
            dst = os.path.join(final, "movie.mkv")
            Path(src).write_bytes(b"source")
            self.controller._Controller__staging_path = staging
            self.controller._Controller__legacy_local_path = final
            original = Controller._Controller__rename_no_replace

            def create_destination_then_publish(source, destination):
                Path(destination).write_bytes(b"racer")
                return original(source, destination)

            with patch.object(Controller, "_Controller__rename_no_replace", side_effect=create_destination_then_publish):
                result = self.controller._Controller__move_from_staging("movie.mkv")
            self.assertEqual(Controller.MoveFromStagingResult.CONFLICT, result)
            self.assertEqual(b"source", Path(src).read_bytes())
            self.assertEqual(b"racer", Path(dst).read_bytes())
