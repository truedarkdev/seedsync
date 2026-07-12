from datetime import datetime, timedelta
import copy
import os
import json
import shutil
import stat
import threading
import time
import tempfile
import unittest
from queue import Queue
from threading import Lock
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from controller import Controller, ControllerPersist, ModelBuilder
from controller.extract import ExtractRequest, ExtractStatus
from controller.validate import ValidateProcess
from controller.scan import MultiPathActiveScanner
from controller.controller import ControllerError, DownloadStartLifecycleEntry
from controller.persist_keys import KEY_SEP
from common import AppError, PathPairManager
from common.path_pair import PathPair
from lftp import LftpError, LftpJobStatus, LftpJobStatusParserError
from model import IModelListener, Model, ModelDiff, ModelError, ModelFile
from system import SystemFile
from transfer import RcloneTransferError


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
        self.controller._Controller__persist.extracted_file_names = set()
        self.controller._Controller__persist.stopped_file_names = set()
        self.controller._Controller__persist.move_failure_counts = {}
        self.controller._Controller__persist.final_move_succeeded_file_names = set()
        self.controller._Controller__model = MagicMock()
        self.controller._Controller__model_builder = MagicMock()
        self.controller._Controller__model_builder.has_changes.return_value = False
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
        self.controller._Controller__path_pair_runtime_error = None
        self.controller._Controller__lftp_reconfigure_lock = Lock()
        self.controller._Controller__lftp_reconfigure_requested = False
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
        self.controller._Controller__updater = MagicMock()
        self.controller._Controller__stop_resume_trace_logger = MagicMock()
        self.controller._Controller__stop_resume_trace_file_id = None
        self.controller._Controller__target_archive_trace_logger = MagicMock()
        self.controller._Controller__target_archive_trace_file_id = None
        self.controller._Controller__target_archive_trace_last_signature = None
        self.controller._Controller__temp_diag_file_id = None
        self.controller._Controller__temp_diag_last_signature = None
        self.controller._Controller__staging_path = "/local/incomplete"
        self.controller._Controller__reported_dead_workers = set()
        self.controller._Controller__path_pairs_by_id = {}
        self.controller._Controller__path_pair_staging_paths = {}
        self.controller._Controller__last_lftp_statuses = []
        self.controller._Controller__next_lftp_status_poll_at = None
        self.controller._Controller__lftp_status_poll_retry_seconds = 1
        self.controller._Controller__lftp_status_cache_expires_at = None
        self.controller._Controller__lftp_status_cache_max_age_seconds = 3
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

    def test_download_start_lifecycle_confirms_running_once(self):
        file = ModelFile("release", True)
        listener = MagicMock()
        self.controller._Controller__model.get_file.return_value = file
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        queued = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file.name, "")
        running = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file.name, "")

        self.controller._confirm_fresh_healthy_download_starts([queued])
        self.controller._confirm_fresh_healthy_download_starts([running])
        self.controller._confirm_fresh_healthy_download_starts([running])

        listener.assert_called_once()
        self.assertEqual("notified", self.controller._Controller__download_start_state[file.file_id].state)

    def test_download_start_lifecycle_stop_suppresses_resume(self):
        file = ModelFile("release", False)
        listener = MagicMock()
        self.controller._Controller__model.get_file.return_value = file
        self.controller.add_download_start_listener(listener)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        self.controller._Controller__suppress_download_start_lifecycle(file.file_id)
        self.controller._Controller__arm_download_start_lifecycle(file.file_id)
        running = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file.name, "")

        self.controller._confirm_fresh_healthy_download_starts([running])

        listener.assert_not_called()
        self.assertEqual("suppressed", self.controller._Controller__download_start_state[file.file_id].state)

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
        )

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
        )
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = failed_scan

        self.controller._Controller__update_model()

        self.assertIn(file_id, self.controller._Controller__download_start_state)
        healthy_scan = SimpleNamespace(
            timestamp=transitioned_at + timedelta(seconds=2),
            files=[],
            failed=False,
            error_message=None,
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

    @patch("controller.model_updater.datetime")
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

    @patch("controller.controller.os.makedirs")
    def test_start_leaves_started_false_if_child_start_fails(self, _mock_makedirs):
        self.controller._Controller__extract_process.start.side_effect = RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self.controller.start()

        self.assertFalse(self.controller._Controller__started)
        self.assertTrue(self.controller._Controller__startup_failed)
        self.controller._Controller__active_scan_process.start.assert_called_once_with()
        self.controller._Controller__local_scan_process.start.assert_called_once_with()
        self.controller._Controller__remote_scan_process.start.assert_called_once_with()
        self.controller._Controller__extract_process.start.assert_called_once_with()
        self.controller._Controller__validate_process.start.assert_not_called()
        self.controller._Controller__mp_logger.start.assert_not_called()
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

    @patch("controller.controller.os.makedirs")
    def test_process_rejects_partial_start_failure_before_exit(self, _mock_makedirs):
        self.controller._Controller__validate_process.start.side_effect = RuntimeError("boom")

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

    def test_configure_lftp_applies_net_socket_buffer_when_configured(self):
        self.controller._Controller__context.config.lftp.net_socket_buffer = "512K"

        self.controller._Controller__configure_lftp()

        self.assertEqual("512K", self.controller._Controller__lftp.net_socket_buffer)

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

        self.controller._Controller__configure_lftp.assert_called_once_with()
        self.assertEqual("*.nfo,Season */*.nfo", self.controller._Controller__exclude_patterns)
        self.assertFalse(self.controller._Controller__lftp_reconfigure_requested)
        self.controller._Controller__propagate_exceptions.assert_called_once_with()
        self.controller._Controller__cleanup_commands.assert_called_once_with()
        self.controller._Controller__process_commands.assert_called_once_with()
        self.controller._Controller__updater.update.assert_called_once_with()
        self.controller._Controller__log_memory_usage.assert_called_once_with()

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

        self.controller._Controller__configure_lftp.assert_called_once_with()
        self.assertTrue(self.controller._Controller__lftp_reconfigure_requested)

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
        self.controller._Controller__path_pairs_by_id = {
            "pair-1": SimpleNamespace(local_path="/local/pair-1", remote_path="/remote/pair-1")
        }
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
        old_active_scanner = self.controller._Controller__active_scanner
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
        old_active_process.is_alive.return_value = False
        old_local_process.is_alive.return_value = False
        old_remote_process.is_alive.return_value = False
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

        with patch("controller.controller.os.makedirs"):
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
        self.controller._Controller__extract_process.is_alive.return_value = True
        self.controller._Controller__validate_process.is_alive.return_value = True

        self.controller._Controller__propagate_exceptions()

        self.controller.logger.error.assert_not_called()
        self.controller._Controller__extract_process.is_alive.assert_called_once_with()
        self.controller._Controller__validate_process.is_alive.assert_called_once_with()

    def test_propagate_exceptions_reports_workers_dead_once_when_is_alive_raises(self):
        self.controller._Controller__remote_scan_process.propagate_exception.return_value = None
        self.controller._Controller__local_scan_process.propagate_exception.return_value = None
        self.controller._Controller__active_scan_process.propagate_exception.return_value = None
        self.controller._Controller__mp_logger.propagate_exception.return_value = None
        self.controller._Controller__extract_process.propagate_exception.return_value = None
        self.controller._Controller__validate_process.propagate_exception.return_value = None
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

    @patch("controller.model_updater.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_prunes_stale_terminal_move_metadata_after_remote_reconciliation(self, _):
        self.controller._Controller__persist.move_failure_counts = {"keep-id": 4, "stale-id": 4}
        self.controller._Controller__move_retry_due = {"stale-id": datetime.now()}
        self.controller._Controller__deferred_move_file_ids = {"stale-id"}
        self.controller._Controller__move_attempt_reservations = {"stale-id"}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = SimpleNamespace(
            timestamp=object(), files=[], failed=False, error_message=None
        )
        self.controller._Controller__model.get_file_ids.return_value = {"keep-id"}
        self.controller._Controller__model.get_file_names.return_value = {"keep"}

        self.controller._Controller__update_model()

        self.assertEqual({"keep-id": 4}, self.controller._Controller__persist.move_failure_counts)
        self.assertNotIn("stale-id", self.controller._Controller__move_retry_due)
        self.assertNotIn("stale-id", self.controller._Controller__deferred_move_file_ids)
        self.assertNotIn("stale-id", self.controller._Controller__move_attempt_reservations)

    def test_update_model_forwards_stopped_file_names(self):
        self.controller._Controller__persist.stopped_file_names = {"stopped-id"}

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_stopped_files.assert_called_once_with({"stopped-id"})

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
        self.controller._Controller__persist.extracted_file_names = {"archive.zip"}
        self.controller._Controller__model = Model()
        self.controller._Controller__model.set_base_logger(self.controller.logger)
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
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

        self.controller._Controller__update_model()
        self.assertEqual({completion_file_id}, self.controller._Controller__persist.downloaded_file_names)
        self.assertEqual(set(), self.controller._Controller__pending_completion_file_names)

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

    def test_process_commands_queue_fresh_empty_before_deadline_keeps_pending_guard(self):
        file = ModelFile("fresh-empty", False)
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.controller._reconcile_pending_queue_dispatches_from_fresh_status(set())
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once()

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

    def test_process_commands_queue_clears_path_pair_stopped_key_encodings(self):
        pair_id = "12345678-1234-1234-1234-123456789abc"
        file = ModelFile("dup", False)
        file.path_pair_id = pair_id
        file.remote_size = 10
        self.controller._Controller__persist.stopped_file_names = {
            file.file_id,
            file.name,
            "{}:{}".format(pair_id, file.name),
            "{}{}{}".format(pair_id, KEY_SEP, file.name),
        }
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            pair_id: SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_queue_clears_migrated_legacy_stopped_key(self):
        pair_id = "12345678-1234-1234-1234-123456789abc"
        file = ModelFile("dup", False)
        file.path_pair_id = pair_id
        file.remote_size = 10
        self.controller._Controller__persist = ControllerPersist.from_str(json.dumps({
            "downloaded": [],
            "extracted": [],
            "stopped": ["{}:{}".format(pair_id, file.name)],
        }))
        self.assertEqual(
            {"{}{}{}".format(pair_id, KEY_SEP, file.name)},
            self.controller._Controller__persist.stopped_file_names
        )
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            pair_id: SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)

    def test_persist_key_helpers_accept_legacy_and_unit_separator_keys(self):
        pair_id = "12345678-1234-1234-1234-123456789abc"
        file_name = "dup"

        self.controller._Controller__persist.downloaded_file_names = {f"{pair_id}:{file_name}"}
        self.assertTrue(self.controller._Controller__is_previously_downloaded(file_name, pair_id))
        self.controller._Controller__persist.downloaded_file_names = {f"{pair_id}{KEY_SEP}{file_name}"}
        self.assertTrue(self.controller._Controller__is_previously_downloaded(file_name, pair_id))

        self.controller._Controller__persist.stopped_file_names = {f"{pair_id}:{file_name}"}
        self.assertTrue(self.controller._Controller__is_explicitly_stopped(file_name, pair_id))
        self.controller._Controller__persist.stopped_file_names = {f"{pair_id}{KEY_SEP}{file_name}"}
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

    def test_cleanup_commands_delete_local_reports_success_after_process_completion(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        self.controller._Controller__persist.move_failure_counts = {file.file_id: 4}
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
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        process.join.assert_called_once_with(Controller._Controller__JOIN_TIMEOUT_IN_SECS)
        process.close_queues.assert_called_once_with()
        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)
        self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)
        self.assertEqual("fresh_after_delete", self.controller._Controller__download_start_state[file.file_id].state)
        self.controller._Controller__model.get_file.return_value = file
        file.remote_size = 10
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, file.file_id))
        self.controller._Controller__process_commands()
        self.assertEqual("eligible", self.controller._Controller__download_start_state[file.file_id].state)

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
                started_at_monotonic=0.0
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
                started_at_monotonic=0.0
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
        result = self.controller._Controller__move_from_staging("movie.mkv")

        self.assertEqual(
            (os.path.normpath("/local/incomplete/movie.mkv"), os.path.normpath("/local/movie.mkv")),
            tuple(os.path.normpath(path) for path in move.call_args.args)
        )
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

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

        self.assertEqual(
            (
                os.path.normpath("/local/movies/incomplete/movie.mkv"),
                os.path.normpath("/local/movies/movie.mkv")
            ),
            tuple(os.path.normpath(path) for path in move.call_args.args)
        )
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with("movies")

    @patch("controller.controller.shutil.move")
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

        move.assert_called_once_with(source_file, os.path.join(final_root, "notes.lftp"))
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

    @patch("controller.controller.shutil.move")
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

        move.assert_called_once_with(source_file, os.path.join(final_root, "movie.mkv"))
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

    @patch("controller.controller.shutil.move")
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

    @patch("controller.controller.shutil.move")
    def test_move_from_staging_moves_directory_with_legitimate_lftp_child_name(self, move):
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

            result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with(source_tree, os.path.join(final_root, "movie.mkv"))
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

    @patch("controller.controller.shutil.move")
    def test_move_from_staging_moves_directory_with_legitimate_lftp_child_pair(self, move):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = os.path.join(temp_dir, "incomplete")
            final_root = os.path.join(temp_dir, "final")
            source_tree = os.path.join(staging_root, "movie")
            os.makedirs(source_tree)
            os.makedirs(final_root)
            with open(os.path.join(source_tree, "foo"), "w", encoding="utf-8") as child_file:
                child_file.write("complete payload")
            with open(os.path.join(source_tree, "foo.lftp"), "w", encoding="utf-8") as child_file:
                child_file.write("also complete payload")

            self.controller._Controller__staging_path = staging_root
            self.controller._Controller__legacy_local_path = final_root

            result = self.controller._Controller__move_from_staging("movie")

        move.assert_called_once_with(source_tree, os.path.join(final_root, "movie"))
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_called_once_with()

    @patch("controller.controller.shutil.move")
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

    @patch("controller.controller.shutil.move")
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

    @patch("controller.controller.shutil.move")
    def test_move_from_staging_rejects_absolute_and_parent_traversal(self, move):
        for unsafe_name in ("/etc/passwd", "C:\\Windows\\system.ini", "\\\\server\\share\\file", "../escape", "nested/../../escape"):
            with self.subTest(name=unsafe_name):
                result = self.controller._Controller__move_from_staging(unsafe_name)
                self.assertEqual(Controller.MoveFromStagingResult.FAILED, result)
        move.assert_not_called()

    @patch("controller.controller.shutil.move")
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

        move.assert_called_once_with(source, destination)
        self.assertEqual(Controller.MoveFromStagingResult.COMPLETED, result)

    @patch("controller.controller.shutil.move")
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

    @patch("controller.controller.shutil.move")
    def test_move_from_staging_logs_target_archive_trace(self, move):
        self.controller._Controller__target_archive_trace_file_id = "movie.mkv"
        trace_logger = self.controller._Controller__target_archive_trace_logger

        with patch("controller.controller.os.path.exists", return_value=True), \
                patch.object(trace_logger, "info") as trace_info:
            self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with(
            os.path.normpath(os.path.join("/local/incomplete", "movie.mkv")),
            os.path.normpath(os.path.join("/local", "movie.mkv"))
        )
        self.assertEqual(2, trace_info.call_count)
        attempt_payload = json.loads(trace_info.call_args_list[0][0][1])
        result_payload = json.loads(trace_info.call_args_list[1][0][1])
        self.assertEqual("move_from_staging_attempt", attempt_payload["event"])
        self.assertEqual("moved", result_payload["result"])

    @patch("controller.controller.shutil.move", side_effect=OSError("permission denied"))
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_reports_move_failure_without_forcing_scan(self, _, move):
        result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with(
            os.path.normpath(os.path.join("/local/incomplete", "movie.mkv")),
            os.path.normpath(os.path.join("/local", "movie.mkv"))
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

    @patch("controller.controller.shutil.move")
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

    @patch("controller.controller.shutil.move")
    @patch("controller.controller.os.path.exists", side_effect=[False, True])
    def test_move_from_staging_treats_missing_source_with_destination_as_settled(self, _, move):
        result = self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_not_called()
        self.assertEqual(Controller.MoveFromStagingResult.ALREADY_COMPLETED, result)
        self.controller.logger.warning.assert_not_called()
        self.controller._Controller__local_scan_process.force_scan.assert_not_called()

    @patch("controller.controller.shutil.move")
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

    def _prepare_terminal_move_command(self, file_id="movie.mkv"):
        model = Model()
        model.set_base_logger(self.controller.logger)
        file = ModelFile("movie.mkv", False)
        file.local_size = 100
        file.remote_size = 100
        file.state = ModelFile.State.MOVE_FAILED
        model.add_file(file)
        self.controller._Controller__model = model
        self.controller._Controller__persist.move_failure_counts = {file.file_id: 4}
        callback = MagicMock()
        command = Controller.Command(Controller.Command.Action.RETRY_MOVE, file_id)
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
        file, command, callback = self._prepare_terminal_move_command()
        self.controller._Controller__move_from_staging = MagicMock(
            return_value=Controller.MoveFromStagingResult.COMPLETED
        )

        self.controller.queue_command(command)
        self.controller._Controller__process_commands()

        callback.on_success.assert_called_once_with()
        self.assertNotIn(file.file_id, self.controller._Controller__persist.move_failure_counts)
        self.assertIn(file.file_id, self.controller._Controller__persist.downloaded_file_names)
        self.assertIn(file.file_id, self.controller._Controller__persist.final_move_succeeded_file_names)

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

    def test_new_queue_clears_terminal_move_lifecycle(self):
        file, _, _ = self._prepare_terminal_move_command()
        file.state = ModelFile.State.MOVE_FAILED
        self.controller._Controller__pending_completion_file_names = {
            (file.name, file.path_pair_id, file.path_pair_name)
        }
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
        self.controller._Controller__move_from_staging = MagicMock(
            side_effect=[Controller.MoveFromStagingResult.DEFERRED] +
                        [Controller.MoveFromStagingResult.FAILED] * 4
        )
        diff_models.side_effect = [
            [SimpleNamespace(change=ModelDiff.Change.UPDATED, old_file=active, new_file=terminal)],
            [], [], [], [], [],
        ]

        self.controller._Controller__update_model()
        self.assertEqual({}, self.controller._Controller__persist.move_failure_counts)

        observed_delays = []
        for expected_count in range(1, 5):
            self.controller._Controller__update_model()
            self.assertEqual(
                expected_count,
                self.controller._Controller__persist.move_failure_counts[terminal.file_id],
            )
            if expected_count < 4:
                due = self.controller._Controller__move_retry_due[terminal.file_id]
                observed_delays.append(round((due - datetime.now()).total_seconds()))
                if expected_count == 1:
                    # Restart keeps the durable count but loses pending/due memory.
                    self.controller._Controller__pending_completion_file_names = set()
                    self.controller._Controller__move_retry_due = {}
                else:
                    self.controller._Controller__move_retry_due[terminal.file_id] = datetime.now() - timedelta(seconds=1)

        self.assertEqual([2, 10, 30], observed_delays)
        self.assertEqual(5, self.controller._Controller__move_from_staging.call_count)
        self.assertNotIn(terminal.file_id, self.controller._Controller__move_retry_due)
        self.controller._Controller__update_model()
        self.assertEqual(5, self.controller._Controller__move_from_staging.call_count)

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
        self.assertEqual({}, self.controller._Controller__download_start_state)

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
        self.controller._Controller__exclude_patterns = "*.nfo,Sample/"
        self.controller._Controller__context.config.general.exclude_patterns = ""
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
            local_base_dir_path="/local/movies/incomplete",
            exclude_patterns="*.nfo,Sample/"
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
