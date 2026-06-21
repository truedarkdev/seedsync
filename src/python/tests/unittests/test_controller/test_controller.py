from datetime import datetime, timedelta
import os
import json
import shutil
import threading
import time
import tempfile
import unittest
from queue import Queue
from threading import Lock
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from controller import Controller, ControllerPersist, ModelBuilder
from controller.extract import ExtractStatus
from controller.scan import MultiPathActiveScanner
from controller.controller import ControllerError
from common import AppError, PathPairManager
from common.path_pair import PathPair
from lftp import LftpError, LftpJobStatus, LftpJobStatusParserError
from model import Model, ModelDiff, ModelError, ModelFile
from system import SystemFile


class TestController(unittest.TestCase):
    def setUp(self):
        self.controller = Controller.__new__(Controller)
        self.controller.logger = MagicMock()
        self.controller._Controller__command_queue = Queue()
        self.controller._Controller__command_flow_lock = Lock()
        self.controller._Controller__active_command_processes = []
        self.controller._Controller__active_downloading_file_names = []
        self.controller._Controller__active_extracting_file_names = []
        self.controller._Controller__prev_downloading_file_names = set()
        self.controller._Controller__pending_completion_file_names = set()
        self.controller._Controller__pending_auto_purge_file_ids = set()
        self.controller._Controller__context = MagicMock()
        self.controller._Controller__context.status.controller = MagicMock()
        self.controller._Controller__context.status.server = SimpleNamespace(up=True, error_msg=None)
        self.controller._Controller__context.breadcrumb_trace = MagicMock()
        self.controller._Controller__context.config.lftp.local_path = "/local"
        self.controller._Controller__context.config.lftp.net_socket_buffer = ""
        self.controller._Controller__password = None
        self.controller._Controller__legacy_local_path = "/local"
        self.controller._Controller__legacy_remote_path = "/remote"
        self.controller._Controller__persist = MagicMock()
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__persist.extracted_file_names = set()
        self.controller._Controller__persist.stopped_file_names = set()
        self.controller._Controller__model = MagicMock()
        self.controller._Controller__model_builder = MagicMock()
        self.controller._Controller__model_builder.has_changes.return_value = False
        self.controller._Controller__model_lock = MagicMock()
        self.controller._Controller__path_pair_refresh_lock = Lock()
        self.controller._Controller__path_pair_refresh_requested = False
        self.controller._Controller__path_pair_refresh_generation = 0
        self.controller._Controller__path_pair_refresh_completed_generation = 0
        self.controller._Controller__path_pair_runtime_error = None
        self.controller._Controller__lftp = MagicMock()
        self.controller._Controller__lftp.net_socket_buffer = ""
        self.controller._Controller__active_scan_process = MagicMock()
        self.controller._Controller__local_scan_process = MagicMock()
        self.controller._Controller__remote_scan_process = MagicMock()
        self.controller._Controller__active_scanner = MagicMock()
        self.controller._Controller__local_scanner = MagicMock()
        self.controller._Controller__remote_scanner = MagicMock()
        self.controller._Controller__extract_process = MagicMock()
        self.controller._Controller__validate_process = MagicMock()
        self.controller._Controller__mp_logger = MagicMock()
        self.controller._Controller__stop_resume_trace_logger = MagicMock()
        self.controller._Controller__stop_resume_trace_file_id = None
        self.controller._Controller__target_archive_trace_logger = MagicMock()
        self.controller._Controller__target_archive_trace_file_id = None
        self.controller._Controller__target_archive_trace_last_signature = None
        self.controller._Controller__temp_diag_file_id = None
        self.controller._Controller__temp_diag_last_signature = None
        self.controller._Controller__staging_path = "/local/incomplete"
        self.controller._Controller__path_pairs_by_id = {}
        self.controller._Controller__path_pair_staging_paths = {}
        self.controller._Controller__last_lftp_statuses = []
        self.controller._Controller__next_lftp_status_poll_at = None
        self.controller._Controller__lftp_status_poll_retry_seconds = 1
        self.controller._Controller__lftp_status_cache_expires_at = None
        self.controller._Controller__lftp_status_cache_max_age_seconds = 3
        self.controller._Controller__startup_recovery_done = False
        self.controller._Controller__memory_monitor = MagicMock()

        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = None
        self.controller._Controller__extract_process.pop_completed.return_value = []
        self.controller._Controller__extract_process.pop_failed.return_value = []

    def _make_startup_context(
        self,
        *,
        local_path,
        remote_path="/remote",
        path_pair_manager=None,
        local_path_to_scanfs="/scanfs",
        use_local_path_as_extract_path=False,
        remote_username="user",
        remote_password="password",
        use_ssh_key=False,
        verbose=False,
        auto_delete_remote=False,
    ):
        return SimpleNamespace(
            logger=MagicMock(),
            web_access_logger=MagicMock(),
            config=SimpleNamespace(
                lftp=SimpleNamespace(
                    remote_address="remote.server.com",
                    remote_username=remote_username,
                    remote_password=remote_password,
                    remote_port=22,
                    remote_path=remote_path,
                    local_path=local_path,
                    remote_path_to_scan_script="/scanfs",
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
                ),
                controller=SimpleNamespace(
                    interval_ms_remote_scan=1,
                    interval_ms_local_scan=1,
                    interval_ms_downloading_scan=1,
                    extract_path="/extract",
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

            with patch("controller.controller.Lftp") as mock_lftp:
                controller = Controller(context, ControllerPersist())

            self.assertIsNone(controller._Controller__startup_validation_error)
            self.assertEqual("/downloads/movies", controller._Controller__legacy_local_path)
            self.assertEqual("/remote/movies", controller._Controller__legacy_remote_path)
            self.assertEqual(
                os.path.join("/downloads/movies", "incomplete"),
                controller._Controller__staging_path
            )
            self.assertTrue(mock_lftp.called)
        finally:
            shutil.rmtree(manager._config_dir)

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

            with patch("controller.controller.Lftp") as mock_lftp:
                controller = Controller(context, ControllerPersist())

            self.assertIsNotNone(controller._Controller__startup_validation_error)
            self.assertIn("Lftp.remote_path", controller._Controller__startup_validation_error)
            self.assertIn("Lftp.local_path", controller._Controller__startup_validation_error)
            mock_lftp.assert_not_called()
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

    def test_update_model_ignores_lftp_status_parser_errors(self):
        self.controller._Controller__lftp.status.side_effect = LftpJobStatusParserError("bad status")

        self.controller._Controller__update_model()

        self.controller.logger.warning.assert_called_once_with("Caught lftp error: bad status")
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
        self.controller._Controller__model_builder.evict_recent_live_transfer_snapshots_missing_roots.assert_called_once_with(set())
        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([])

    def test_update_model_skips_status_poll_during_healthy_cooldown_with_cache(self):
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.controller._Controller__lftp.status.return_value = [status]

        self.controller._Controller__update_model()
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

    @patch("controller.controller.datetime")
    def test_update_model_schedules_healthy_status_poll_about_200ms_out(self, datetime_mock):
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        now = datetime(2026, 4, 4, 12, 0, 0)
        datetime_mock.now.return_value = now
        self.controller._Controller__lftp.status.return_value = [status]

        self.controller._Controller__update_model()

        self.assertEqual(
            now + timedelta(milliseconds=200),
            self.controller._Controller__next_lftp_status_poll_at
        )
        self.assertFalse(self.controller._Controller__lftp_status_poll_retry_active)

    def test_exit_ignores_lftp_teardown_failure_and_continues_shutdown(self):
        self.controller._Controller__started = True
        self.controller._Controller__lftp.exit.side_effect = LftpError("teardown failed")

        self.controller.exit()

        self.controller.logger.warning.assert_called_once()
        self.controller._Controller__active_scan_process.terminate.assert_called_once_with()
        self.controller._Controller__local_scan_process.terminate.assert_called_once_with()
        self.controller._Controller__remote_scan_process.terminate.assert_called_once_with()
        self.controller._Controller__extract_process.terminate.assert_called_once_with()
        self.controller._Controller__validate_process.terminate.assert_called_once_with()
        self.controller._Controller__active_scan_process.join.assert_called_once_with()
        self.controller._Controller__local_scan_process.join.assert_called_once_with()
        self.controller._Controller__remote_scan_process.join.assert_called_once_with()
        self.controller._Controller__extract_process.join.assert_called_once_with()
        self.controller._Controller__validate_process.join.assert_called_once_with()
        self.controller._Controller__active_scan_process.close_queues.assert_called_once_with()
        self.controller._Controller__local_scan_process.close_queues.assert_called_once_with()
        self.controller._Controller__remote_scan_process.close_queues.assert_called_once_with()
        self.controller._Controller__extract_process.close_queues.assert_called_once_with()
        self.controller._Controller__validate_process.close_queues.assert_called_once_with()
        self.controller._Controller__mp_logger.stop.assert_called_once_with()
        self.assertFalse(self.controller._Controller__started)

    @patch("controller.controller.os.makedirs")
    def test_start_records_breadcrumb_when_enabled(self, _mock_makedirs):
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
        self.controller._Controller__extract_process.start.assert_called_once_with()
        self.controller._Controller__validate_process.start.assert_called_once_with()
        self.controller._Controller__mp_logger.start.assert_called_once_with()
        self.assertTrue(self.controller._Controller__started)

    def test_configure_lftp_applies_net_socket_buffer_when_configured(self):
        self.controller._Controller__context.config.lftp.net_socket_buffer = "512K"

        self.controller._Controller__configure_lftp()

        self.assertEqual("512K", self.controller._Controller__lftp.net_socket_buffer)

    def test_configure_lftp_skips_empty_net_socket_buffer(self):
        self.controller._Controller__context.config.lftp.net_socket_buffer = ""

        self.controller._Controller__configure_lftp()

        self.assertEqual("", self.controller._Controller__lftp.net_socket_buffer)

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
        self.controller._Controller__context.breadcrumb_trace.record.reset_mock()

        self.controller._Controller__update_model()

        message_to_corr_ids = {
            call.args[1]: call.kwargs.get("corr_id")
            for call in self.controller._Controller__context.breadcrumb_trace.record.call_args_list
        }
        self.assertEqual("pair-1", message_to_corr_ids["remote_scan_result"])
        self.assertEqual("pair-1", message_to_corr_ids["local_scan_result"])
        self.assertEqual("pair-1", message_to_corr_ids["extract_completed"])
        self.assertEqual("extract:aggregate", message_to_corr_ids["extract_status_result"])

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
        )
        self.assertEqual(set(), self.controller._Controller__persist.extracted_file_names)
        self.controller._Controller__model_builder.set_extracted_files.assert_not_called()
        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([])

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

    @patch("controller.controller.ScannerProcess")
    def test_refresh_path_pairs_rebuilds_runtime_state_and_forces_rescan(self, scanner_process_cls):
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
        validate_process = self.controller._Controller__validate_process
        model_builder = self.controller._Controller__model_builder

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
        self.controller._Controller__active_downloading_file_names = [("dup", "movies", "Movies")]
        self.controller._Controller__active_extracting_file_names = []
        self.controller._Controller__set_active_scanner_files = MagicMock()
        new_active_process = MagicMock()
        new_local_process = MagicMock()
        new_remote_process = MagicMock()
        scanner_process_cls.side_effect = [new_active_process, new_local_process, new_remote_process]

        with patch("controller.controller.os.makedirs") as makedirs_mock:
            self.controller._Controller__apply_path_pair_refresh()

        makedirs_mock.assert_called_once_with(os.path.join("/local/movies", "incomplete"), exist_ok=True)
        old_active_process.terminate.assert_called_once_with()
        old_local_process.terminate.assert_called_once_with()
        old_remote_process.terminate.assert_called_once_with()
        old_active_process.join.assert_called_once_with()
        old_local_process.join.assert_called_once_with()
        old_remote_process.join.assert_called_once_with()

        new_active_process.start.assert_called_once_with()
        new_local_process.start.assert_called_once_with()
        new_remote_process.start.assert_called_once_with()
        new_active_process.force_scan.assert_called_once_with()
        new_local_process.force_scan.assert_called_once_with()
        new_remote_process.force_scan.assert_called_once_with()
        self.controller._Controller__set_active_scanner_files.assert_called_once_with(
            [("dup", "movies", "Movies")]
        )

        validate_process.set_path_pairs_by_id.assert_called_once()
        refreshed_pairs = validate_process.set_path_pairs_by_id.call_args.args[0]
        self.assertEqual(["movies"], list(refreshed_pairs.keys()))
        self.assertIs(movies_pair, refreshed_pairs["movies"])

        self.controller._Controller__lftp.set_path_pairs.assert_called_once()
        lftp_pairs = self.controller._Controller__lftp.set_path_pairs.call_args.args[0]
        self.assertEqual(1, len(lftp_pairs))
        self.assertEqual(os.path.join("/local/movies", "incomplete"), lftp_pairs[0].local_path)

        model_builder.set_local_root_paths.assert_called_once_with(
            {None: "/local", "movies": "/local/movies"},
            {
                None: "/local/incomplete",
                "movies": os.path.join("/local/movies", "incomplete")
            }
        )
        self.assertEqual({"movies"}, set(self.controller._Controller__path_pairs_by_id.keys()))
        self.assertEqual(
            os.path.join("/local/movies", "incomplete"),
            self.controller._Controller__path_pair_staging_paths["movies"]
        )

    def test_refresh_path_pairs_marks_pending_refresh_when_started(self):
        self.controller._Controller__started = True

        self.controller.refresh_path_pairs()

        self.assertTrue(self.controller._Controller__path_pair_refresh_requested)

    def test_process_keeps_running_after_path_pair_refresh_failure(self):
        self.controller._Controller__started = True
        self.controller.refresh_path_pairs()
        self.controller._Controller__refresh_path_pair_runtime_state = MagicMock(side_effect=RuntimeError("activation failed"))
        self.controller._Controller__update_model = MagicMock()
        self.controller._Controller__log_memory_usage = MagicMock()

        self.controller.process()

        self.assertFalse(self.controller._Controller__context.status.server.up)
        self.assertIn("activation failed", self.controller._Controller__context.status.server.error_msg)
        self.assertEqual(1, self.controller._Controller__path_pair_refresh_completed_generation)
        self.controller._Controller__update_model.assert_called_once()
        self.controller._Controller__log_memory_usage.assert_called_once()

    def test_process_marks_refresh_completed_for_consumed_generation_only(self):
        self.controller._Controller__started = True
        self.controller.refresh_path_pairs()

        def bump_generation():
            self.controller.refresh_path_pairs()

        self.controller._Controller__apply_path_pair_refresh = MagicMock(side_effect=bump_generation)
        self.controller._Controller__update_model = MagicMock()
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
        self.controller._Controller__update_model = MagicMock()
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
        self.controller._Controller__update_model = MagicMock(side_effect=lambda: call_order.append("update"))
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
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["b"])

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

        self.controller._Controller__model_builder.set_remote_files.assert_called_once_with([partial_file])
        self.assertIs(latest_remote_scan.timestamp, self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual(
            "Failed to scan remote path for pair 'TV': temporary remote failure",
            self.controller._Controller__context.status.controller.latest_remote_scan_error
        )

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
        self.controller._Controller__active_scanner.set_active_files.assert_any_call(["a"])
        self.assertEqual(
            [[status_b], [status_b], [status_a]],
            [call.args[0] for call in self.controller._Controller__model_builder.set_lftp_statuses.call_args_list]
        )

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

        model_files = self.controller.get_model_files()

        self.assertEqual({file_movies.file_id, file_tv.file_id}, {file.file_id for file in model_files})

    def test_process_commands_stop_reports_lftp_status_parser_errors(self):
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

        callback.on_failure.assert_called_once_with("Lftp error: bad status", 500)
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

    def test_propagate_exceptions_ignores_pending_lftp_errors(self):
        self.controller._Controller__lftp.raise_pending_error.side_effect = LftpError("pending failure")

        self.controller._Controller__propagate_exceptions()

        self.controller.logger.warning.assert_called_once_with("Caught lftp error: pending failure")
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__remote_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_called_once_with()
        self.controller._Controller__extract_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__validate_process.propagate_exception.assert_called_once_with()

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

    @patch("controller.controller.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_prunes_stale_downloaded_file_names(self, _):
        self.controller._Controller__persist.downloaded_file_names = {"keep-id", "stale-id"}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(),
            files=[],
            failed=False,
            error_message=None
        )
        self.controller._Controller__model.get_file_ids.return_value = {"keep-id"}
        self.controller._Controller__model.get_file_names.return_value = {"keep"}

        self.controller._Controller__update_model()

        self.assertEqual({"keep-id"}, self.controller._Controller__persist.downloaded_file_names)
        self.controller._Controller__model_builder.set_downloaded_files.assert_called_once_with({"keep-id"})

    def test_update_model_forwards_stopped_file_names(self):
        self.controller._Controller__persist.stopped_file_names = {"stopped-id"}

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_stopped_files.assert_called_once_with({"stopped-id"})

    @patch("controller.controller.ModelDiffUtil.diff_models")
    def test_update_model_keeps_downloaded_file_ids_when_new_download_completes(self, diff_models):
        added_file = ModelFile("keep", False)
        added_file.path_pair_id = "movies"
        added_file.state = ModelFile.State.DOWNLOADED

        stale_a = "[\"movies\",\"a\"]"
        stale_b = "[\"movies\",\"b\"]"
        self.controller._Controller__persist.downloaded_file_names = {stale_a, stale_b}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
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

    @patch("controller.controller.ModelDiffUtil.diff_models")
    def test_update_model_removes_stale_extracted_file_names_when_new_download_completes(self, diff_models):
        added_file = ModelFile("archive.zip", False)
        added_file.path_pair_id = "movies"
        added_file.state = ModelFile.State.DOWNLOADED

        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__persist.extracted_file_names = {"archive.zip"}
        self.controller._Controller__model = Model()
        self.controller._Controller__model.set_base_logger(self.controller.logger)
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        diff_models.return_value = [MagicMock(change=ModelDiff.Change.ADDED, new_file=added_file)]

        self.controller._Controller__update_model()

        self.assertEqual({added_file.file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__persist.extracted_file_names)
        self.controller._Controller__model_builder.set_extracted_files.assert_called_with(set())

    @patch("controller.controller.ModelDiffUtil.diff_models")
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

    @patch("controller.controller.ModelDiffUtil.diff_models")
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

        self.controller._Controller__update_model()
        self.assertEqual({completion_file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)

    @patch("controller.controller.ModelDiffUtil.diff_models")
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
        self.controller.clear_extracted_marker = MagicMock()
        self.controller._Controller__move_from_staging = MagicMock()
        diff_models.return_value = [
            SimpleNamespace(
                change=ModelDiff.Change.UPDATED,
                old_file=active_file,
                new_file=terminal_file
            )
        ]

        self.controller._Controller__update_model()

        self.assertEqual({completion_file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)
        self.controller.clear_extracted_marker.assert_called_once_with(terminal_file)
        self.controller._Controller__move_from_staging.assert_called_once_with("movie.mkv", "movies")
        self.controller._Controller__model_builder.set_downloaded_files.assert_called_once_with({completion_file_id})

    @patch("controller.controller.ModelDiffUtil.diff_models")
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

    @patch("controller.controller.ModelDiffUtil.diff_models")
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

    @patch("controller.controller.ModelDiffUtil.diff_models")
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

    @patch("controller.controller.ModelDiffUtil.diff_models")
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

    @patch("controller.controller.ModelDiffUtil.diff_models")
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

    def test_update_model_skips_auto_purge_for_tracked_zero_byte_local_only_file(self):
        file = ModelFile("stale", False)
        file.path_pair_id = "movies"
        file.local_size = 0
        file.remote_size = None
        file.state = ModelFile.State.DEFAULT

        self.controller._Controller__persist.downloaded_file_names = {file.file_id}
        self.assertFalse(self.controller._Controller__should_auto_purge_local_file(file))

    @patch("controller.controller.ModelDiffUtil.diff_models")
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
            local_base_dir_path="/local/movies/incomplete"
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

    def test_process_commands_queue_clears_legacy_stopped_name_identity(self):
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

        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)

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
        self.assertEqual(1, self.controller._Controller__command_queue.qsize())
        deferred_command = self.controller._Controller__command_queue.get_nowait()
        self.assertIs(command, deferred_command)
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, deferred_command.action)
        self.assertEqual(
            Controller._MAX_CONCURRENT_COMMAND_PROCESSES,
            len(self.controller._Controller__active_command_processes)
        )

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
        self.assertEqual(0, self.controller._Controller__command_queue.qsize())

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

    def test_cleanup_commands_delete_local_reports_success_after_process_completion(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__persist.stopped_file_names = {file.file_id}

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
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        process.join.assert_called_once_with()
        process.close_queues.assert_called_once_with()
        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)

    def test_cleanup_commands_delete_local_surfaces_missing_file_failure(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__persist.stopped_file_names = {file.file_id}

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
        process.join.assert_called_once_with()
        process.close_queues.assert_called_once_with()
        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)

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
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command,
                file_id=file.file_id,
                file_name=file.name,
                process=process,
                post_callback=post_callback,
                await_completion=False
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
        process.join.assert_called_once_with()
        process.close_queues.assert_called_once_with()
        self.assertEqual([], self.controller._Controller__active_command_processes)

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
        self.controller._Controller__active_command_processes = [
            Controller.CommandProcessWrapper(
                command=command,
                file_id=file.file_id,
                file_name=file.name,
                process=process,
                post_callback=post_callback,
                await_completion=False
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
        process.join.assert_called_once_with()
        process.close_queues.assert_called_once_with()
        self.assertEqual([], self.controller._Controller__active_command_processes)

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
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

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

    def test_process_commands_extract_passes_flow_id_to_extract_process(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 20
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.EXTRACT, "dup", flow_id="flow-123")
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__extract_process.extract.assert_called_once_with(file, flow_id="flow-123")

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
        self.controller._Controller__model.get_file_ids.return_value = {"a", "b", "c"}
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

    def test_build_staging_path_prefers_explicit_single_path_override(self):
        self.assertEqual(
            "/custom/staging",
            self.controller._Controller__build_staging_path("/local", "/custom/staging")
        )

    @patch("controller.controller.shutil.move")
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_uses_single_path_roots(self, _, move):
        self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with("/local/incomplete/movie.mkv", "/local/movie.mkv")

    @patch("controller.controller.shutil.move")
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_uses_path_pair_roots(self, _, move):
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        self.controller._Controller__move_from_staging("movie.mkv", "movies")

        move.assert_called_once_with(
            "/local/movies/incomplete/movie.mkv",
            "/local/movies/movie.mkv"
        )

    @patch("controller.controller.shutil.move")
    def test_move_from_staging_logs_target_archive_trace(self, move):
        self.controller._Controller__target_archive_trace_file_id = "movie.mkv"
        trace_logger = self.controller._Controller__target_archive_trace_logger

        with patch("controller.controller.os.path.exists", return_value=True), \
                patch.object(trace_logger, "info") as trace_info:
            self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with(
            os.path.join("/local/incomplete", "movie.mkv"),
            os.path.join("/local", "movie.mkv")
        )
        self.assertEqual(2, trace_info.call_count)
        attempt_payload = json.loads(trace_info.call_args_list[0][0][1])
        result_payload = json.loads(trace_info.call_args_list[1][0][1])
        self.assertEqual("move_from_staging_attempt", attempt_payload["event"])
        self.assertEqual("moved", result_payload["result"])

    def test_recover_interrupted_downloads_requeues_single_path_temp_file(self):
        self.controller._Controller__persist.downloaded_file_names = set()

        remote_file = SimpleNamespace(name="movie.mkv", path_pair_id=None)
        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch("controller.controller.os.path.isdir", return_value=False):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.assertTrue(self.controller._Controller__startup_recovery_done)
        self.controller._Controller__lftp.queue.assert_called_once_with(
            "movie.mkv",
            False,
            remote_base_dir_path=None,
            local_base_dir_path="/local/incomplete"
        )

    def test_recover_interrupted_downloads_skips_previously_downloaded_path_pair_file(self):
        file_id = ModelFile.build_file_id("dup.mkv", "movies")
        self.controller._Controller__persist.downloaded_file_names = {file_id}
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        remote_file = SimpleNamespace(name="dup.mkv", path_pair_id="movies")
        with patch("controller.controller.os.listdir", return_value=["dup.mkv.lftp"]), \
                patch("controller.controller.os.path.isdir", return_value=False):
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
            SimpleNamespace(name="dup.mkv", path_pair_id="movies"),
            SimpleNamespace(name="dup.mkv", path_pair_id="tv")
        ]

        def listdir_side_effect(path):
            if path == "/local/movies/incomplete":
                return ["dup.mkv.lftp"]
            if path == "/local/tv/incomplete":
                return ["dup.mkv.lftp"]
            raise AssertionError(path)

        with patch("controller.controller.os.listdir", side_effect=listdir_side_effect), \
                patch("controller.controller.os.path.isdir", return_value=False):
            self.controller._Controller__recover_interrupted_downloads(remote_files)

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "dup.mkv",
            False,
            remote_base_dir_path="/remote/tv",
            local_base_dir_path="/local/tv/incomplete"
        )

    def test_recover_interrupted_downloads_queues_path_pair_directory(self):
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        remote_file = SimpleNamespace(name="season1", path_pair_id="movies")

        def listdir_side_effect(path):
            if path == "/local/movies/incomplete":
                return ["season1"]
            if path == os.path.join("/local/movies/incomplete", "season1"):
                return ["episode1.mkv.lftp"]
            raise AssertionError(path)

        with patch("controller.controller.os.listdir", side_effect=listdir_side_effect), \
                patch("controller.controller.os.path.isdir", side_effect=lambda path: path.endswith("season1")):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "season1",
            True,
            remote_base_dir_path="/remote/movies",
            local_base_dir_path="/local/movies/incomplete"
        )

    def test_process_commands_queue_logs_fresh_and_resume_like_trace_details(self):
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
        self.controller._Controller__stop_resume_trace_file_id = file.file_id
        trace_logger = self.controller._Controller__stop_resume_trace_logger
        temp_path = os.path.join("/local/movies/incomplete", "dup.lftp")
        sidecar_path = temp_path + ".lftp-pget-status"

        def stat_side_effect(path):
            if path == temp_path:
                return SimpleNamespace(st_size=250, st_mtime=111, st_blocks=8)
            if path == sidecar_path:
                return SimpleNamespace(st_size=64, st_mtime=222)
            raise OSError(path)

        with patch("controller.controller.os.stat", side_effect=stat_side_effect), \
                patch.object(trace_logger, "info") as trace_info:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
            self.controller._Controller__process_commands()

        self.assertEqual(1, trace_info.call_count)
        payload = json.loads(trace_info.call_args[0][1])
        self.assertEqual("queue_fresh", payload["reason"])
        self.assertEqual(file.file_id, payload["file_id"])
        self.assertEqual("dup", payload["filename"])
        self.assertEqual("DEFAULT", payload["current_state"])
        self.assertEqual("/local/movies/incomplete", payload["local_base_dir_path"])
        self.assertEqual(temp_path, payload["temp_path"])
        self.assertTrue(payload["temp_exists"])
        self.assertEqual(250, payload["temp_apparent_size"])
        self.assertEqual(4096, payload["temp_allocated_size"])
        self.assertEqual(sidecar_path, payload["sidecar_path"])
        self.assertTrue(payload["sidecar_exists"])
        self.assertEqual(64, payload["sidecar_size"])
        self.assertEqual(222, payload["sidecar_mtime"])
        self.assertFalse(payload["stopped_marked"])

        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        trace_logger.reset_mock()
        with patch("controller.controller.os.stat", side_effect=stat_side_effect), \
                patch.object(trace_logger, "info") as trace_info:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
            self.controller._Controller__process_commands()

        self.assertEqual(1, trace_info.call_count)
        payload = json.loads(trace_info.call_args[0][1])
        self.assertEqual("queue_after_stop", payload["reason"])
        self.assertTrue(payload["stopped_marked"])
        self.assertEqual(temp_path, payload["temp_path"])

    def test_process_commands_queue_logs_trace_for_bare_filename_selector(self):
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
        self.controller._Controller__stop_resume_trace_file_id = file.name
        trace_logger = self.controller._Controller__stop_resume_trace_logger

        with patch.object(trace_logger, "info") as trace_info:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
            self.controller._Controller__process_commands()

        self.assertEqual(1, trace_info.call_count)
        payload = json.loads(trace_info.call_args[0][1])
        self.assertEqual(file.file_id, payload["file_id"])
        self.assertEqual("dup", payload["filename"])

    def test_process_commands_queue_does_not_match_unrelated_trace_selector(self):
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
        self.controller._Controller__stop_resume_trace_file_id = "other-name"
        trace_logger = self.controller._Controller__stop_resume_trace_logger

        with patch.object(trace_logger, "info") as trace_info:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
            self.controller._Controller__process_commands()

        trace_info.assert_not_called()

    def test_process_commands_stop_logs_trace_details(self):
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
        self.controller._Controller__stop_resume_trace_file_id = file.file_id
        trace_logger = self.controller._Controller__stop_resume_trace_logger
        temp_path = os.path.join("/local/movies/incomplete", "dup.lftp")
        sidecar_path = temp_path + ".lftp-pget-status"

        def stat_side_effect(path):
            if path == temp_path:
                return SimpleNamespace(st_size=250, st_mtime=111, st_blocks=8)
            if path == sidecar_path:
                return SimpleNamespace(st_size=64, st_mtime=222)
            raise OSError(path)

        self.controller._Controller__lftp.kill.return_value = True
        with patch("controller.controller.os.stat", side_effect=stat_side_effect), \
                patch.object(trace_logger, "info") as trace_info:
            self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, file.file_id))
            self.controller._Controller__process_commands()

        self.assertEqual(1, trace_info.call_count)
        payload = json.loads(trace_info.call_args[0][1])
        self.assertEqual("stop", payload["reason"])
        self.assertEqual(file.file_id, payload["file_id"])
        self.assertEqual("dup", payload["filename"])
        self.assertEqual("DOWNLOADING", payload["current_state"])
        self.assertEqual("/local/movies/incomplete", payload["local_base_dir_path"])
        self.assertEqual(temp_path, payload["temp_path"])
        self.assertTrue(payload["temp_exists"])
        self.assertEqual(250, payload["temp_apparent_size"])
        self.assertEqual(4096, payload["temp_allocated_size"])
        self.assertEqual(sidecar_path, payload["sidecar_path"])
        self.assertTrue(payload["sidecar_exists"])
        self.assertEqual(64, payload["sidecar_size"])

    def test_recover_interrupted_downloads_logs_trace_details(self):
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__stop_resume_trace_file_id = "movie.mkv"
        trace_logger = self.controller._Controller__stop_resume_trace_logger
        temp_path = os.path.join("/local/incomplete", "movie.mkv.lftp")
        sidecar_path = temp_path + ".lftp-pget-status"

        def stat_side_effect(path):
            if path == temp_path:
                return SimpleNamespace(st_size=250, st_mtime=111, st_blocks=8)
            if path == sidecar_path:
                return SimpleNamespace(st_size=64, st_mtime=222)
            raise OSError(path)

        remote_file = SimpleNamespace(name="movie.mkv", path_pair_id=None)
        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch("controller.controller.os.path.isdir", return_value=False), \
                patch("controller.controller.os.stat", side_effect=stat_side_effect), \
                patch.object(trace_logger, "info") as trace_info:
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.assertEqual(1, trace_info.call_count)
        payload = json.loads(trace_info.call_args[0][1])
        self.assertEqual("recover_interrupted_download", payload["reason"])
        self.assertEqual("movie.mkv", payload["file_id"])
        self.assertEqual("movie.mkv", payload["filename"])
        self.assertEqual("/local/incomplete", payload["local_base_dir_path"])
        self.assertEqual(temp_path, payload["temp_path"])
        self.assertTrue(payload["temp_exists"])
        self.assertEqual(250, payload["temp_apparent_size"])
        self.assertEqual(4096, payload["temp_allocated_size"])
        self.assertEqual(sidecar_path, payload["sidecar_path"])
        self.assertTrue(payload["sidecar_exists"])
        self.assertEqual(64, payload["sidecar_size"])
