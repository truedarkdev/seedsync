# Copyright 2017, Inderpreet Singh, All rights reserved.

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, cast
from threading import Lock, RLock
from queue import Queue
from enum import Enum
from datetime import datetime, timedelta
import copy
import json
import os
import time
import shutil
from types import SimpleNamespace

# my libs
from .scan import (
    ScannerProcess,
    ActiveScanner,
    LocalScanner,
    RemoteScanner,
    MultiPathActiveScanner,
    MultiPathLocalScanner,
    MultiPathRemoteScanner,
)
from .extract import ExtractProcess, ExtractStatus
from .validate import ValidateProcess
from .model_updater import ModelUpdater
from .model_builder import ModelBuilder
from .memory_monitor import ControllerMemoryMonitor
from common import Context, AppError, MultiprocessingLogger, AppOneShotProcess, AppProcess, Constants, PathPair, Localization
from model import ModelError, ModelFile, Model, ModelDiff, ModelDiffUtil, IModelListener
from lftp import Lftp, LftpError, LftpJobStatus, LftpJobStatusParserError
from .controller_persist import ControllerPersist
from .persist_keys import persist_key, strip_persist_key
from .delete import DeleteLocalProcess, DeleteRemoteProcess


class ControllerError(AppError):
    """
    Exception indicating a controller error
    """
    pass


class Controller:
    """
    Top-level class that controls the behaviour of the app
    """
    class Command:
        """
        Class by which clients of Controller can request Actions to be executed
        Supports callbacks by which clients can be notified of action success/failure
        Note: callbacks will be executed in Controller thread, so any heavy computation
              should be moved out of the callback
        """
        class Action(Enum):
            QUEUE = 0
            STOP = 1
            EXTRACT = 2
            DELETE_LOCAL = 3
            DELETE_REMOTE = 4
            VALIDATE = 5

        class ICallback(ABC):
            """Command callback interface"""
            @abstractmethod
            def on_success(self):
                """Called on successful completion of action"""
                pass

            @abstractmethod
            def on_failure(self, error: str, error_code: int = 400):
                """Called on action failure"""
                pass

        def __init__(self,
                     action: Action,
                     filename: str,
                     flow_id: Optional[str] = None,
                     origin: str = "manual"):
            self.action = action
            self.filename = filename
            self.flow_id = flow_id
            self.origin = origin
            self.callbacks = []

        def add_callback(self, callback: ICallback):
            self.callbacks.append(callback)

    class CommandProcessWrapper:
        """
        Wraps any one-shot command processes launched by the controller
        """
        def __init__(
            self,
            command: "Controller.Command",
            file_id: str,
            file_name: str,
            process: AppOneShotProcess,
            post_callback: Callable,
            await_completion: bool,
            started_at_monotonic: float | None = None
        ):
            self.command = command
            self.file_id = file_id
            self.file_name = file_name
            self.process = process
            self.post_callback = post_callback
            self.await_completion = await_completion
            self.started_at_monotonic = time.monotonic() if started_at_monotonic is None else started_at_monotonic

    _MAX_CONCURRENT_COMMAND_PROCESSES = 8
    _MAX_PENDING_DELETE_COMMANDS = _MAX_CONCURRENT_COMMAND_PROCESSES * 2
    _MAX_DUPLICATE_DELETE_WAITERS = _MAX_CONCURRENT_COMMAND_PROCESSES
    _DELETE_COMMAND_STALE_TIMEOUT_IN_SECS = 10 * 60

    @staticmethod
    def __lftp_status_refresh_timing(interval_ms_downloading_scan: int):
        # Keep the unhealthy retry window close to the downloading scan
        # cadence so a brief lftp hiccup does not pin a finished transfer in
        # a stale state.
        lftp_status_poll_retry_seconds = max(1, int(interval_ms_downloading_scan / 1000))
        lftp_status_cache_max_age_seconds = max(3, lftp_status_poll_retry_seconds * 3)
        return lftp_status_poll_retry_seconds, lftp_status_cache_max_age_seconds

    @staticmethod
    def _is_missing_startup_value(value: Any) -> bool:
        return value is None or (
            isinstance(value, str) and (value.strip() == "" or value == "<replace me>")
        )

    @staticmethod
    def collect_missing_startup_fields(
        config: Any,
        args: Any | None = None,
        path_pair_manager: Any | None = None,
    ) -> List[str]:
        def _append_missing(section_name: str, field_name: str, value: Any) -> None:
            if Controller._is_missing_startup_value(value):
                missing_fields.append("{}.{}".format(section_name, field_name))

        missing_fields: List[str] = []
        enabled_path_pairs = []
        if path_pair_manager is not None:
            try:
                enabled_path_pairs = list(path_pair_manager.get_enabled_pairs() or [])
            except Exception:
                enabled_path_pairs = []
        require_legacy_paths = len(enabled_path_pairs) == 0

        lftp_cfg = getattr(config, "lftp", None)
        controller_cfg = getattr(config, "controller", None)
        general_cfg = getattr(config, "general", None)
        autoqueue_cfg = getattr(config, "autoqueue", None)

        # The controller always starts Lftp, so the username remains required
        # even when key-based auth is enabled. Password auth is conditional.
        _append_missing("Lftp", "remote_address", getattr(lftp_cfg, "remote_address", None))
        _append_missing("Lftp", "remote_username", getattr(lftp_cfg, "remote_username", None))
        transfer_protocol = getattr(lftp_cfg, "protocol", "sftp")
        _append_missing("Lftp", "protocol", transfer_protocol)
        if getattr(lftp_cfg, "use_ssh_key", None) is False or transfer_protocol == "ftps":
            _append_missing("Lftp", "remote_password", getattr(lftp_cfg, "remote_password", None))
        if transfer_protocol == "ftps":
            _append_missing("Lftp", "remote_ftp_port", getattr(lftp_cfg, "remote_ftp_port", None))
        for field_name in (
            "remote_port",
            "remote_path_to_scan_script",
            "use_ssh_key",
            "use_temp_file",
            "num_max_parallel_downloads",
            "num_max_parallel_files_per_download",
            "num_max_connections_per_root_file",
            "num_max_connections_per_dir_file",
            "num_max_total_connections",
        ):
            _append_missing("Lftp", field_name, getattr(lftp_cfg, field_name, None))

        if require_legacy_paths:
            for field_name in ("remote_path", "local_path"):
                _append_missing("Lftp", field_name, getattr(lftp_cfg, field_name, None))

        for field_name in (
            "interval_ms_remote_scan",
            "interval_ms_local_scan",
            "interval_ms_downloading_scan",
        ):
            _append_missing("Controller", field_name, getattr(controller_cfg, field_name, None))

        controller_use_local_path_as_extract_path = getattr(
            controller_cfg,
            "use_local_path_as_extract_path",
            None,
        )
        _append_missing(
            "Controller",
            "use_local_path_as_extract_path",
            controller_use_local_path_as_extract_path,
        )
        if controller_use_local_path_as_extract_path is False:
            _append_missing("Controller", "extract_path", getattr(controller_cfg, "extract_path", None))

        _append_missing("General", "verbose", getattr(general_cfg, "verbose", None))
        _append_missing("AutoQueue", "auto_delete_remote", getattr(autoqueue_cfg, "auto_delete_remote", None))

        if args is not None:
            _append_missing("Args", "local_path_to_scanfs", getattr(args, "local_path_to_scanfs", None))

        return missing_fields

    def __initialize_startup_validation_failure(self, missing_fields: List[str]) -> None:
        error_message = Localization.Error.SETTINGS_INCOMPLETE_FIELDS.format(", ".join(missing_fields))
        self.__startup_validation_error = error_message
        self.__context.status.server.up = False
        self.__context.status.server.error_msg = error_message
        self.logger.error(error_message)
        self.__password = None
        self.__ssh_password = None
        self.__transfer_password = None
        self.__legacy_local_path = None
        self.__legacy_remote_path = None
        self.__staging_path = ""
        self.__lftp = None
        self.__active_scanner = None
        self.__local_scanner = None
        self.__remote_scanner = None
        self.__active_scan_process = None
        self.__local_scan_process = None
        self.__remote_scan_process = None
        self.__extract_process = None
        self.__validate_process = None
        self.__mp_logger = None
        self.__active_downloading_file_names = []
        self.__active_extracting_file_names = []
        self.__prev_downloading_file_names = set()
        self.__pending_completion_file_names = set()
        self.__malformed_status_only_file_ids = set()
        self.__pending_auto_purge_file_ids = set()
        self.__last_lftp_statuses = []
        self.__next_lftp_status_poll_at = None
        self.__lftp_status_poll_retry_seconds = 1
        self.__lftp_status_cache_expires_at = None
        self.__lftp_status_poll_retry_active = False
        self.__active_command_processes = []
        self.__startup_recovery_done = False
        self.__reported_dead_workers = set()
        self.__memory_monitor = ControllerMemoryMonitor(self.logger.getChild("MemoryMonitor"))
        self.__started = False
        self.__startup_failed = False
        self.__lftp_reconfigure_lock = Lock()
        self.__lftp_reconfigure_requested = False

    def __init__(self,
                 context: Context,
                 persist: ControllerPersist):
        self.__context = context
        self.__persist = persist
        self.logger = context.logger.getChild("Controller")
        self.__stop_resume_trace_logger = self.logger.getChild("StopResumeTrace")
        self.__stop_resume_trace_file_id = os.environ.get("SEEDSYNC_STOP_RESUME_TRACE_FILE_ID")
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        self.__target_archive_trace_file_id = os.environ.get("SEEDSYNC_TARGET_ARCHIVE_TRACE_FILE_ID")
        if self.__target_archive_trace_file_id is not None and not self.__target_archive_trace_file_id.strip():
            self.__target_archive_trace_file_id = None
        self.__target_archive_trace_last_signature = None
        self.__temp_diag_file_id = os.environ.get("SEEDSYNC_TEMP_DIAG_FILE_ID")
        if self.__temp_diag_file_id is not None and not self.__temp_diag_file_id.strip():
            self.__temp_diag_file_id = None
        self.__temp_diag_last_signature = None

        # The command queue
        self.__command_queue = Queue()
        self.__command_flow_sequence = 0
        self.__command_flow_lock = Lock()

        # The model
        self.__model = Model()
        self.__model.set_base_logger(self.logger)
        # Lock for the model. Listeners may re-enter controller model access
        # while the model updater is mutating the model, so this must be reentrant.
        self.__model_lock = RLock()
        self.__path_pair_refresh_lock = Lock()
        self.__path_pair_refresh_requested = False
        self.__path_pair_refresh_generation = 0
        self.__path_pair_refresh_completed_generation = 0
        self.__path_pair_runtime_error = None
        self.__lftp_reconfigure_lock = Lock()
        self.__lftp_reconfigure_requested = False

        # Model builder
        self.__model_builder = ModelBuilder()
        self.__model_builder.set_base_logger(self.logger)

        self.__path_pairs_by_id: Dict[str, PathPair] = {}
        self.__path_pair_staging_paths: Dict[str, str] = {}

        config = cast(Any, self.__context.config)
        startup_args = getattr(self.__context, "args", None)
        if startup_args is None:
            startup_args = SimpleNamespace(local_path_to_scanfs=None)
        missing_startup_fields = Controller.collect_missing_startup_fields(
            config,
            startup_args,
            getattr(self.__context, "path_pair_manager", None),
        )
        self.__startup_validation_error = None
        if missing_startup_fields:
            self.__initialize_startup_validation_failure(missing_startup_fields)
            return

        # Decide the password here
        lftp_cfg = config.lftp
        controller_cfg = config.controller
        general_cfg = config.general
        self.__ssh_password = lftp_cfg.remote_password if not lftp_cfg.use_ssh_key else None
        self.__transfer_password = (
            lftp_cfg.remote_password if lftp_cfg.protocol == "ftps" else self.__ssh_password
        )
        self.__password = self.__ssh_password

        enabled_path_pairs = self.__get_enabled_path_pairs()
        first_path_pair = enabled_path_pairs[0] if enabled_path_pairs else None
        self.__legacy_local_path = lftp_cfg.local_path
        if Controller._is_missing_startup_value(self.__legacy_local_path) and first_path_pair is not None:
            self.__legacy_local_path = first_path_pair.local_path
        self.__legacy_remote_path = lftp_cfg.remote_path
        if Controller._is_missing_startup_value(self.__legacy_remote_path) and first_path_pair is not None:
            self.__legacy_remote_path = first_path_pair.remote_path

        self.__staging_path = self.__build_staging_path(
            self.__legacy_local_path,
            lftp_cfg.staging_path
        )

        # Lftp
        self.__lftp = Lftp(address=lftp_cfg.remote_address,
                           port=lftp_cfg.remote_port,
                           user=lftp_cfg.remote_username,
                           password=self.__transfer_password,
                           protocol=lftp_cfg.protocol,
                           remote_ftp_port=lftp_cfg.remote_ftp_port,
                           ssl_verify_certificate=lftp_cfg.ftp_ssl_verify_certificate)
        self.__lftp.set_base_logger(self.logger)
        self.__lftp.set_base_remote_dir_path(self.__legacy_remote_path)
        self.__lftp.set_base_local_dir_path(self.__staging_path)
        self.__configure_lftp()

        try:
            self.__refresh_path_pair_runtime_state()
        except Exception as exc:
            self.__record_path_pair_runtime_error(
                "Path pair runtime activation failed: {}".format(exc)
            )
            self.logger.exception(
                "Path pair runtime activation failed during controller initialization; "
                "continuing without enabled path pairs"
            )
            self.__refresh_path_pair_runtime_state([])

        # Setup extract process
        if controller_cfg.use_local_path_as_extract_path:
            out_dir_path = self.__legacy_local_path
        else:
            out_dir_path = controller_cfg.extract_path
        # Keep the final local root primary, but allow archive lookup to fall
        # back to staging so extraction can survive the move boundary.
        self.__extract_process = ExtractProcess(
            out_dir_path=out_dir_path,
            local_path=self.__legacy_local_path,
            local_path_fallback=self.__staging_path,
            managed_extract_folders_enabled=controller_cfg.managed_extract_folders_enabled,
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter()
        )
        self.__validate_process = ValidateProcess(
            remote_address=lftp_cfg.remote_address,
            remote_username=lftp_cfg.remote_username,
            remote_password=self.__ssh_password,
            remote_port=lftp_cfg.remote_port,
            local_path=self.__legacy_local_path,
            remote_path=self.__legacy_remote_path,
            path_pairs_by_id=cast(Dict[str, object], self.__path_pairs_by_id)
        )

        # Setup multiprocess logging
        self.__mp_logger = MultiprocessingLogger(self.logger)
        self.__active_scan_process.set_multiprocessing_logger(self.__mp_logger)
        self.__local_scan_process.set_multiprocessing_logger(self.__mp_logger)
        self.__remote_scan_process.set_multiprocessing_logger(self.__mp_logger)
        self.__extract_process.set_multiprocessing_logger(self.__mp_logger)
        self.__validate_process.set_multiprocessing_logger(self.__mp_logger)

        # Keep track of active files
        self.__active_downloading_file_names = []
        self.__active_extracting_file_names = []
        # Path-pair aware completion tracking so a finished download stays
        # visible until the model reaches a terminal state.
        self.__prev_downloading_file_names = set()
        self.__pending_completion_file_names = set()
        self.__malformed_status_only_file_ids = set()
        self.__pending_auto_purge_file_ids = set()
        self.__last_lftp_statuses = []
        self.__next_lftp_status_poll_at = None
        (
            self.__lftp_status_poll_retry_seconds,
            self.__lftp_status_cache_max_age_seconds
        ) = Controller.__lftp_status_refresh_timing(controller_cfg.interval_ms_downloading_scan)
        self.__lftp_status_cache_expires_at = None
        self.__lftp_status_poll_retry_active = False

        # Keep track of active command processes
        self.__active_command_processes = []
        self.__startup_recovery_done = False
        self.__reported_dead_workers = set()
        self.__memory_monitor = ControllerMemoryMonitor(self.logger.getChild("MemoryMonitor"))
        self.__updater = ModelUpdater(self)
        self.__updater.sync_persist_to_all_builders()

        self.__started = False
        self.__startup_failed = False

    def __configure_lftp(self):
        # Configure Lftp
        config = cast(Any, self.__context.config)
        lftp_cfg = config.lftp
        general_cfg = config.general
        self.__lftp.num_parallel_jobs = lftp_cfg.num_max_parallel_downloads
        self.__lftp.num_parallel_files = lftp_cfg.num_max_parallel_files_per_download
        self.__lftp.num_connections_per_root_file = lftp_cfg.num_max_connections_per_root_file
        self.__lftp.num_connections_per_dir_file = lftp_cfg.num_max_connections_per_dir_file
        self.__lftp.num_max_total_connections = lftp_cfg.num_max_total_connections
        self.__lftp.use_temp_file = lftp_cfg.use_temp_file
        rate_limit = lftp_cfg.rate_limit
        self.__lftp.rate_limit = 0 if rate_limit in (None, "") else rate_limit
        net_socket_buffer = lftp_cfg.net_socket_buffer
        self.__lftp.net_socket_buffer = 0 if net_socket_buffer in (None, "") else net_socket_buffer
        self.__lftp.temp_file_name = "*" + Constants.LFTP_TEMP_FILE_SUFFIX
        self.__lftp.set_verbose_logging(general_cfg.verbose)

    def __get_enabled_path_pairs(self) -> List[PathPair]:
        if self.__context.path_pair_manager is None:
            return []
        return self.__context.path_pair_manager.get_enabled_pairs()

    def __refresh_path_pair_runtime_state(self, enabled_path_pairs: Optional[List[PathPair]] = None):
        if enabled_path_pairs is None:
            enabled_path_pairs = self.__get_enabled_path_pairs()

        config = cast(Any, self.__context.config)
        controller_cfg = config.controller
        path_pairs_by_id: Dict[str, PathPair] = {pair.id: pair for pair in enabled_path_pairs}
        path_pair_staging_paths: Dict[str, str] = {
            pair.id: self.__build_staging_path(pair.local_path)
            for pair in enabled_path_pairs
        }
        lftp_path_pairs: List[PathPair] = [
            PathPair(
                remote_path=pair.remote_path,
                local_path=path_pair_staging_paths[pair.id],
                name=pair.name,
                id=pair.id,
                enabled=pair.enabled,
                auto_queue=pair.auto_queue
            )
            for pair in enabled_path_pairs
        ]
        active_scanner = self.__build_active_scanner(enabled_path_pairs, path_pair_staging_paths)
        local_scanner = self.__build_local_scanner(enabled_path_pairs, path_pair_staging_paths)
        remote_scanner = self.__build_remote_scanner(enabled_path_pairs)
        active_scan_process = ScannerProcess(
            scanner=active_scanner,
            interval_in_ms=controller_cfg.interval_ms_downloading_scan,
            verbose=False,
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter()
        )
        local_scan_process = ScannerProcess(
            scanner=local_scanner,
            interval_in_ms=controller_cfg.interval_ms_local_scan,
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter()
        )
        remote_scan_process = ScannerProcess(
            scanner=remote_scanner,
            interval_in_ms=controller_cfg.interval_ms_remote_scan,
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter()
        )

        self.__lftp.set_path_pairs(lftp_path_pairs)
        self.__refresh_model_builder_local_paths(path_pairs_by_id, path_pair_staging_paths)
        self.__path_pairs_by_id = path_pairs_by_id
        self.__path_pair_staging_paths = path_pair_staging_paths
        self.__active_scanner = active_scanner
        self.__local_scanner = local_scanner
        self.__remote_scanner = remote_scanner
        self.__active_scan_process = active_scan_process
        self.__local_scan_process = local_scan_process
        self.__remote_scan_process = remote_scan_process

    def __build_lftp_path_pairs(self,
                                path_pairs_by_id: Dict[str, PathPair],
                                path_pair_staging_paths: Dict[str, str]) -> List[PathPair]:
        return [
            PathPair(
                remote_path=pair.remote_path,
                local_path=path_pair_staging_paths[pair.id],
                name=pair.name,
                id=pair.id,
                enabled=pair.enabled,
                auto_queue=pair.auto_queue
            )
            for pair in path_pairs_by_id.values()
        ]

    def __refresh_model_builder_local_paths(
            self,
            path_pairs_by_id: Optional[Dict[str, PathPair]] = None,
            path_pair_staging_paths: Optional[Dict[str, str]] = None):
        if path_pairs_by_id is None:
            path_pairs_by_id = self.__path_pairs_by_id
        if path_pair_staging_paths is None:
            path_pair_staging_paths = self.__path_pair_staging_paths

        local_root_paths: Dict[Optional[str], str] = {None: self.__legacy_local_path}
        local_staging_paths: Dict[Optional[str], str] = {None: self.__staging_path}
        for pair_id, pair in path_pairs_by_id.items():
            local_root_paths[pair_id] = pair.local_path
            local_staging_paths[pair_id] = path_pair_staging_paths.get(pair_id, self.__staging_path)
        self.__model_builder.set_local_root_paths(local_root_paths, local_staging_paths)

    def __record_path_pair_runtime_error(self, error_msg: str):
        self.__path_pair_runtime_error = error_msg
        self.__context.status.server.up = False
        self.__context.status.server.error_msg = error_msg
        self.__record_breadcrumb(
            stage="path_pair_runtime",
            message="path_pair_runtime_error",
            details={
                "error_message": error_msg,
            },
            event_type="failure",
            corr_id="path_pair_runtime",
        )

    def __clear_path_pair_runtime_error(self):
        if self.__path_pair_runtime_error is None:
            return
        if self.__context.status.server.error_msg == self.__path_pair_runtime_error:
            self.__context.status.server.up = True
            self.__context.status.server.error_msg = None
        self.__path_pair_runtime_error = None

    def __build_active_scanner(self, enabled_path_pairs: List[PathPair], path_pair_staging_paths):
        config = cast(Any, self.__context.config)
        if enabled_path_pairs:
            return MultiPathActiveScanner({
                pair.id: path_pair_staging_paths[pair.id] for pair in enabled_path_pairs
            }, use_temp_file=config.lftp.use_temp_file)
        return ActiveScanner(
            self.__staging_path,
            use_temp_file=config.lftp.use_temp_file
        )

    def __build_local_scanner(self, enabled_path_pairs: List[PathPair], path_pair_staging_paths):
        config = cast(Any, self.__context.config)
        if enabled_path_pairs:
            return MultiPathLocalScanner([
                LocalScanner(
                    local_path=pair.local_path,
                    use_temp_file=config.lftp.use_temp_file,
                    staging_path=path_pair_staging_paths[pair.id],
                    managed_extract_folders_enabled=config.controller.managed_extract_folders_enabled,
                    path_pair_id=pair.id,
                    path_pair_name=pair.name
                ) for pair in enabled_path_pairs
            ])
        return LocalScanner(
            local_path=self.__legacy_local_path,
            use_temp_file=config.lftp.use_temp_file,
            staging_path=self.__staging_path,
            managed_extract_folders_enabled=config.controller.managed_extract_folders_enabled
        )

    def __build_remote_scanner(self, enabled_path_pairs: List[PathPair]):
        config = cast(Any, self.__context.config)
        remote_python_path = getattr(config.lftp, "remote_python_path", None)
        if not isinstance(remote_python_path, str):
            remote_python_path = None
        if enabled_path_pairs:
            return MultiPathRemoteScanner([
                RemoteScanner(
                    remote_address=config.lftp.remote_address,
                    remote_username=config.lftp.remote_username,
                    remote_password=self.__ssh_password,
                    remote_port=config.lftp.remote_port,
                    remote_path_to_scan=pair.remote_path,
                    local_path_to_scan_script=cast(Any, self.__context.args).local_path_to_scanfs,
                    remote_path_to_scan_script=config.lftp.remote_path_to_scan_script,
                    remote_python_path=remote_python_path,
                    path_pair_id=pair.id,
                    path_pair_name=pair.name
                ) for pair in enabled_path_pairs
            ])
        return RemoteScanner(
            remote_address=config.lftp.remote_address,
            remote_username=config.lftp.remote_username,
            remote_password=self.__ssh_password,
            remote_port=config.lftp.remote_port,
            remote_path_to_scan=self.__legacy_remote_path,
            local_path_to_scan_script=cast(Any, self.__context.args).local_path_to_scanfs,
            remote_path_to_scan_script=config.lftp.remote_path_to_scan_script,
            remote_python_path=remote_python_path
        )

    def __mark_path_pair_refresh_completed(self, generation: Optional[int] = None):
        if generation is None:
            generation = self.__path_pair_refresh_generation
        with self.__path_pair_refresh_lock:
            self.__path_pair_refresh_completed_generation = max(
                self.__path_pair_refresh_completed_generation,
                generation
            )

    def refresh_path_pairs(self, wait: bool = False, timeout_secs: Optional[float] = None):
        startup_validation_error = getattr(self, "_Controller__startup_validation_error", None)
        if startup_validation_error is not None:
            raise ControllerError(startup_validation_error)
        if getattr(self, "_Controller__startup_failed", False):
            raise ControllerError("Cannot refresh path pairs, controller startup failed")
        if not self.__started:
            self.__apply_path_pair_refresh()
            self.__mark_path_pair_refresh_completed(self.__path_pair_refresh_generation)
            if self.__path_pair_runtime_error is not None:
                raise ControllerError(self.__path_pair_runtime_error)
            return
        with self.__path_pair_refresh_lock:
            self.__path_pair_refresh_requested = True
            self.__path_pair_refresh_generation += 1
            requested_generation = self.__path_pair_refresh_generation

        if not wait:
            return

        if timeout_secs is None:
            timeout_secs = Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
        deadline = time.monotonic() + timeout_secs
        while time.monotonic() < deadline:
            with self.__path_pair_refresh_lock:
                if self.__path_pair_refresh_completed_generation >= requested_generation:
                    break
            time.sleep(Constants.MAIN_THREAD_SLEEP_INTERVAL_IN_SECS)
        else:
            raise ControllerError("Timed out waiting for path pair refresh")

        if self.__path_pair_runtime_error is not None:
            raise ControllerError(self.__path_pair_runtime_error)

    def request_lftp_reconfigure(self):
        with self.__lftp_reconfigure_lock:
            self.__lftp_reconfigure_requested = True

    def __consume_lftp_reconfigure_request(self) -> bool:
        with self.__lftp_reconfigure_lock:
            if not self.__lftp_reconfigure_requested:
                return False
            self.__lftp_reconfigure_requested = False
            return True

    def __restore_lftp_reconfigure_request(self):
        with self.__lftp_reconfigure_lock:
            self.__lftp_reconfigure_requested = True

    def __consume_path_pair_refresh_request(self):
        with self.__path_pair_refresh_lock:
            if not self.__path_pair_refresh_requested:
                return None
            self.__path_pair_refresh_requested = False
            return self.__path_pair_refresh_generation

    def __restore_path_pair_runtime_state(
            self,
            path_pairs_by_id: Dict[str, PathPair],
            path_pair_staging_paths: Dict[str, str],
            active_scanner,
            local_scanner,
            remote_scanner,
            active_scan_process,
            local_scan_process,
            remote_scan_process):
        self.__lftp.set_path_pairs(self.__build_lftp_path_pairs(path_pairs_by_id, path_pair_staging_paths))
        self.__refresh_model_builder_local_paths(path_pairs_by_id, path_pair_staging_paths)
        self.__validate_process.set_path_pairs_by_id(cast(Dict[str, object], path_pairs_by_id))
        self.__path_pairs_by_id = path_pairs_by_id
        self.__path_pair_staging_paths = path_pair_staging_paths
        self.__active_scanner = active_scanner
        self.__local_scanner = local_scanner
        self.__remote_scanner = remote_scanner
        self.__active_scan_process = active_scan_process
        self.__local_scan_process = local_scan_process
        self.__remote_scan_process = remote_scan_process
        self.__record_breadcrumb(
            stage="path_pair_runtime",
            message="path_pair_runtime_refreshed",
            details={
                "path_pair_count": len(path_pairs_by_id),
                "staging_path_count": len(path_pair_staging_paths),
            },
            event_type="state_transition",
            corr_id="path_pair_runtime",
        )

    def __apply_path_pair_refresh(self):
        active_files = list(
            getattr(self, "_Controller__active_downloading_file_names", []) +
            getattr(self, "_Controller__active_extracting_file_names", []) +
            list(getattr(self, "_Controller__pending_completion_file_names", []))
        )
        was_started = self.__started
        old_active_scan_process = self.__active_scan_process
        old_local_scan_process = self.__local_scan_process
        old_remote_scan_process = self.__remote_scan_process
        old_path_pairs_by_id = self.__path_pairs_by_id
        old_path_pair_staging_paths = self.__path_pair_staging_paths
        old_active_scanner = self.__active_scanner
        old_local_scanner = self.__local_scanner
        old_remote_scanner = self.__remote_scanner
        new_state_applied = False

        def stop_process_if_alive(process):
            if process is not None and process.is_alive():
                process.terminate()
                process.join()

        try:
            self.__refresh_path_pair_runtime_state()
            new_state_applied = True
            if was_started:
                self.__active_scan_process.set_multiprocessing_logger(self.__mp_logger)
                self.__local_scan_process.set_multiprocessing_logger(self.__mp_logger)
                self.__remote_scan_process.set_multiprocessing_logger(self.__mp_logger)
                self.__validate_process.set_path_pairs_by_id(cast(Dict[str, object], self.__path_pairs_by_id))

            if was_started:
                for staging_path in self.__path_pair_staging_paths.values():
                    os.makedirs(staging_path, exist_ok=True)
                self.__active_scan_process.start()
                self.__local_scan_process.start()
                self.__remote_scan_process.start()
                self.__set_active_scanner_files(active_files)
                self.__active_scan_process.force_scan()
                self.__local_scan_process.force_scan()
                self.__remote_scan_process.force_scan()
                self.__next_lftp_status_poll_at = None
                stop_process_if_alive(old_active_scan_process)
                stop_process_if_alive(old_local_scan_process)
                stop_process_if_alive(old_remote_scan_process)
            self.__clear_path_pair_runtime_error()
        except Exception as exc:
            if new_state_applied:
                stop_process_if_alive(self.__active_scan_process)
                stop_process_if_alive(self.__local_scan_process)
                stop_process_if_alive(self.__remote_scan_process)
                self.__restore_path_pair_runtime_state(
                    old_path_pairs_by_id,
                    old_path_pair_staging_paths,
                    old_active_scanner,
                    old_local_scanner,
                    old_remote_scanner,
                    old_active_scan_process,
                    old_local_scan_process,
                    old_remote_scan_process
                )
            self.logger.exception("Path pair runtime activation failed")
            self.__record_path_pair_runtime_error("Path pair runtime activation failed: {}".format(exc))

    def start(self):
        """
        Start the controller
        Must be called after ctor and before process()
        :return:
        """
        startup_validation_error = getattr(self, "_Controller__startup_validation_error", None)
        if startup_validation_error is not None:
            raise ControllerError(startup_validation_error)
        self.logger.debug("Starting controller")
        os.makedirs(self.__staging_path, exist_ok=True)
        for staging_path in self.__path_pair_staging_paths.values():
            os.makedirs(staging_path, exist_ok=True)
        # Keep partial startup failure separate so exit() can clean up already
        # started workers without making process() look fully live.
        self.__startup_failed = False
        try:
            self.__active_scan_process.start()
            self.__local_scan_process.start()
            self.__remote_scan_process.start()
            self.__extract_process.start()
            self.__validate_process.start()
            self.__mp_logger.start()
        except Exception:
            self.__startup_failed = True
            raise
        self.__started = True
        self.__record_breadcrumb(
            stage="controller",
            message="start",
            details={
                "path_pair_count": len(self.__path_pairs_by_id),
                "staging_path_count": len(self.__path_pair_staging_paths),
            },
            event_type="state_transition",
            corr_id="controller",
        )

    def process(self):
        """
        Advance the controller state
        This method should return relatively quickly as the heavy lifting is done by concurrent tasks
        :return:
        """
        startup_validation_error = getattr(self, "_Controller__startup_validation_error", None)
        if startup_validation_error is not None:
            raise ControllerError(startup_validation_error)
        if getattr(self, "_Controller__startup_failed", False):
            raise ControllerError("Cannot process, controller startup failed")
        if not self.__started:
            raise ControllerError("Cannot process, controller is not started")
        self.__propagate_exceptions()
        self.__cleanup_commands()
        self.__process_commands()
        refresh_generation = self.__consume_path_pair_refresh_request()
        if refresh_generation is not None:
            try:
                self.__apply_path_pair_refresh()
            except Exception:
                self.logger.exception("Ignoring path pair refresh failure")
            finally:
                self.__mark_path_pair_refresh_completed(refresh_generation)
        lftp_reconfigure_requested = self.__consume_lftp_reconfigure_request()
        if lftp_reconfigure_requested:
            try:
                self.__configure_lftp()
            except Exception:
                self.__restore_lftp_reconfigure_request()
                self.logger.exception("Ignoring lftp reconfigure failure")
        updater = getattr(self, "_Controller__updater", None)
        if updater is None:
            updater = ModelUpdater(self)
            self.__updater = updater
        updater.update()
        self.__log_memory_usage()

    def __best_effort_teardown(self, label: str, teardown: Callable[[], None]):
        try:
            teardown()
        except Exception:
            self.logger.exception(
                "Ignoring controller teardown failure during %s; continuing shutdown",
                label
            )

    __JOIN_TIMEOUT_IN_SECS = 2

    def __bounded_join(self, label: str, process: AppProcess) -> None:
        self.__best_effort_teardown(label, lambda: process.join(self.__JOIN_TIMEOUT_IN_SECS))
        try:
            still_alive = process.is_alive()
        except (AssertionError, ValueError):
            still_alive = False
        if still_alive:
            self.logger.warning(
                "Worker %s did not exit within %ss; continuing teardown",
                getattr(process, "name", "?"),
                self.__JOIN_TIMEOUT_IN_SECS,
            )

    def __report_dead_worker_once(self, worker: AppProcess | None, worker_name: str) -> None:
        if worker is None:
            return
        worker_id = id(worker)
        if worker_id in self.__reported_dead_workers:
            return
        try:
            alive = worker.is_alive()
        except (AssertionError, ValueError):
            alive = False
        if alive:
            return

        self.__reported_dead_workers.add(worker_id)
        self.logger.error(
            "%s worker has died; %s is disabled until restart.",
            worker_name,
            worker_name,
        )

    def exit(self):
        self.logger.debug("Exiting controller")
        if self.__started or getattr(self, "_Controller__startup_failed", False):
            try:
                self.__lftp.exit()
            except LftpError as exc:
                self.logger.warning("Ignoring lftp teardown failure: {}".format(exc))
            except Exception:
                self.logger.exception("Ignoring lftp teardown failure; continuing shutdown")
            finally:
                self.__best_effort_teardown("active scan process terminate", self.__active_scan_process.terminate)
                self.__best_effort_teardown("local scan process terminate", self.__local_scan_process.terminate)
                self.__best_effort_teardown("remote scan process terminate", self.__remote_scan_process.terminate)
                self.__best_effort_teardown("extract process terminate", self.__extract_process.terminate)
                self.__best_effort_teardown("validate process terminate", self.__validate_process.terminate)
                self.__bounded_join("active scan process join", self.__active_scan_process)
                self.__best_effort_teardown("active scan process close_queues", self.__active_scan_process.close_queues)
                self.__bounded_join("local scan process join", self.__local_scan_process)
                self.__best_effort_teardown("local scan process close_queues", self.__local_scan_process.close_queues)
                self.__bounded_join("remote scan process join", self.__remote_scan_process)
                self.__best_effort_teardown("remote scan process close_queues", self.__remote_scan_process.close_queues)
                self.__bounded_join("extract process join", self.__extract_process)
                self.__best_effort_teardown("extract process close_queues", self.__extract_process.close_queues)
                self.__bounded_join("validate process join", self.__validate_process)
                self.__best_effort_teardown("validate process close_queues", self.__validate_process.close_queues)
                self.__best_effort_teardown("mp logger stop", self.__mp_logger.stop)
                self.__started = False
                self.__startup_failed = False
                self.logger.info("Exited controller")

    def get_model_files(self) -> List[ModelFile]:
        """
        Returns a copy of all the model files
        :return:
        """
        with self.__model_lock:
            model_files = self.__get_model_files()
        return model_files

    def is_file_stopped(self, filename: str) -> bool:
        return filename in self.__persist.stopped_file_names

    def add_model_listener(self, listener: IModelListener):
        """
        Adds a listener to the controller's model
        :param listener:
        :return:
        """
        with self.__model_lock:
            self.__model.add_listener(listener)

    def remove_model_listener(self, listener: IModelListener):
        """
        Removes a listener from the controller's model
        :param listener:
        :return:
        """
        with self.__model_lock:
            self.__model.remove_listener(listener)

    def get_model_files_and_add_listener(self, listener: IModelListener):
        """
        Adds a listener and returns the current state of model files in one atomic operation
        This guarantees that model update events are not missed or duplicated for the clients
        Without an atomic operation, the following scenarios can happen:
            1. get_model() -> model updated -> add_listener()
               The model update never propagates to client
            2. add_listener() -> model updated -> get_model()
               The model update is duplicated on client side (once through listener, and once
               through the model).
        :param listener:
        :return:
        """
        with self.__model_lock:
            self.__model.add_listener(listener)
            model_files = self.__get_model_files()
        return model_files

    def queue_command(self, command: Command):
        startup_validation_error = getattr(self, "_Controller__startup_validation_error", None)
        if startup_validation_error is not None:
            self.logger.warning("Rejecting command because controller startup config is incomplete: %s",
                                startup_validation_error)
            for callback in command.callbacks:
                callback.on_failure(startup_validation_error, 400)
            return
        if getattr(command, "flow_id", None) is None:
            command.flow_id = self.__next_command_flow_id(command)
        is_delete_command = self.__is_delete_command_action(command.action)
        duplicate_delete_command: Optional[Controller.Command] = None
        delete_backpressure = False
        duplicate_waiter_backpressure = False
        queued_delete_count = 0
        duplicate_waiter_count = 0
        queue_size = None

        if is_delete_command:
            delete_identity = self.__canonical_delete_command_identity(command)
            with self.__command_state_lock():
                duplicate_delete_command = self.__find_pending_delete_command_unlocked(
                    delete_identity,
                    command.action
                )
                if not duplicate_delete_command:
                    queued_delete_count = self.__pending_delete_command_count_unlocked()
                    delete_backpressure = queued_delete_count >= Controller._MAX_PENDING_DELETE_COMMANDS
                if not duplicate_delete_command and not delete_backpressure:
                    self.__command_queue.put(command)
                    queue_size = self.__safe_command_queue_size()
                if duplicate_delete_command:
                    duplicate_waiter_count = getattr(duplicate_delete_command, "duplicate_waiter_count", 0)
                    requested_waiters = len(command.callbacks)
                    duplicate_waiter_backpressure = (
                        duplicate_waiter_count + requested_waiters >
                        Controller._MAX_DUPLICATE_DELETE_WAITERS
                    )
                    if not duplicate_waiter_backpressure:
                        duplicate_delete_command.callbacks.extend(command.callbacks)
                        duplicate_delete_command.duplicate_waiter_count = \
                            duplicate_waiter_count + requested_waiters
        else:
            self.__command_queue.put(command)
            queue_size = self.__safe_command_queue_size()

        if duplicate_waiter_backpressure:
            self.logger.warning(
                "Rejecting duplicate %s for '%s': %d duplicate delete waiters at limit %d",
                command.action,
                command.filename,
                duplicate_waiter_count,
                Controller._MAX_DUPLICATE_DELETE_WAITERS
            )
            self.__record_command_breadcrumb(
                command=command,
                message="command_failed",
                details={
                    "command": getattr(command.action, "name", str(command.action)),
                    "origin": getattr(command, "origin", "manual"),
                    "file_name": command.filename,
                    "queue_size": self.__safe_command_queue_size(),
                    "error_code": 429,
                    "reason": "duplicate_delete_waiters_full",
                    "duplicate_waiter_count": duplicate_waiter_count,
                    "limit": Controller._MAX_DUPLICATE_DELETE_WAITERS,
                },
                event_type="failure",
            )
            for callback in command.callbacks:
                callback.on_failure(
                    "Controller is busy with too many duplicate delete waiters",
                    429
                )
            return

        if duplicate_delete_command:
            self.logger.info(
                "Coalescing duplicate %s command for '%s'",
                command.action,
                command.filename
            )
            self.__record_command_breadcrumb(
                command=command,
                message="command_coalesced",
                details={
                    "command": getattr(command.action, "name", str(command.action)),
                    "origin": getattr(command, "origin", "manual"),
                    "file_name": command.filename,
                    "queue_size": self.__safe_command_queue_size(),
                    "reason": "duplicate_pending",
                },
                event_type="state_transition",
            )
            return

        if delete_backpressure:
            self.logger.warning(
                "Rejecting %s for '%s': %d queued delete commands at limit %d",
                command.action,
                command.filename,
                queued_delete_count,
                Controller._MAX_PENDING_DELETE_COMMANDS
            )
            self.__record_command_breadcrumb(
                command=command,
                message="command_failed",
                details={
                    "command": getattr(command.action, "name", str(command.action)),
                    "origin": getattr(command, "origin", "manual"),
                    "file_name": command.filename,
                    "queue_size": self.__safe_command_queue_size(),
                    "error_code": 429,
                    "reason": "delete_backlog_full",
                    "queued_delete_count": queued_delete_count,
                    "limit": Controller._MAX_PENDING_DELETE_COMMANDS,
                },
                event_type="failure",
            )
            for callback in command.callbacks:
                callback.on_failure(
                    "Controller is busy with too many pending delete commands",
                    429
                )
            return

        self.__record_command_breadcrumb(
            command=command,
            message="command_queued",
            details={
                "command": getattr(command.action, "name", str(command.action)),
                "origin": getattr(command, "origin", "manual"),
                "file_name": command.filename,
                "queue_size": queue_size,
            },
            event_type="state_transition",
        )

    def __get_model_files(self) -> List[ModelFile]:
        model_files = []
        get_ids = getattr(self.__model, "get_file_ids", None)
        identifiers = cast(List[str], get_ids()) if callable(get_ids) else self.__model.get_file_names()
        for identifier in identifiers:
            model_files.append(copy.deepcopy(self.__model.get_file(identifier)))
        return model_files

    def __get_path_pair(self, path_pair_id: Optional[str]) -> Optional[PathPair]:
        if not path_pair_id:
            return None
        return getattr(self, "_Controller__path_pairs_by_id", {}).get(path_pair_id)

    @staticmethod
    def __build_staging_path(local_path: str, staging_path: Optional[str] = None) -> str:
        return staging_path or os.path.join(local_path, "incomplete")

    @staticmethod
    def __persist_key_candidates(name: str, path_pair_id: Optional[str] = None) -> set[str]:
        candidates = {
            name,
            ModelFile.build_file_id(name, path_pair_id),
            persist_key(path_pair_id, name),
        }
        if path_pair_id:
            candidates.add("{}:{}".format(path_pair_id, name))
        return candidates

    @staticmethod
    def __has_persist_key(keys: set[str], name: str, path_pair_id: Optional[str] = None) -> bool:
        if not keys:
            return False
        if keys.intersection(Controller.__persist_key_candidates(name, path_pair_id)):
            return True
        return any(strip_persist_key(key, path_pair_id) == name for key in keys)

    @staticmethod
    def __clear_persist_key(keys: set[str], name: str, path_pair_id: Optional[str] = None) -> None:
        keys.difference_update(Controller.__persist_key_candidates(name, path_pair_id))
        for key in list(keys):
            if strip_persist_key(key, path_pair_id) == name:
                keys.discard(key)

    def __is_previously_downloaded(self, name: str, path_pair_id: Optional[str] = None) -> bool:
        return Controller.__has_persist_key(self.__persist.downloaded_file_names, name, path_pair_id)

    def __is_explicitly_stopped(self, name: str, path_pair_id: Optional[str] = None) -> bool:
        return Controller.__has_persist_key(self.__persist.stopped_file_names, name, path_pair_id)

    def __get_staging_path(self, path_pair_id: Optional[str] = None) -> Optional[str]:
        if path_pair_id:
            path_pair = self.__get_path_pair(path_pair_id)
            if path_pair is not None:
                return self.__path_pair_staging_paths.get(path_pair_id, self.__build_staging_path(path_pair.local_path))
            return self.__path_pair_staging_paths.get(path_pair_id)
        return self.__staging_path

    def __get_stop_resume_trace_file_details(self, path: Optional[str], include_allocated_size: bool = False) -> dict:
        if path is None:
            return {
                "exists": False,
                "size": None,
                "mtime": None,
                "allocated_size": None
            }

        try:
            stat_result = os.stat(path)
        except OSError:
            return {
                "exists": False,
                "size": None,
                "mtime": None,
                "allocated_size": None
            }

        details = {
            "exists": True,
            "size": stat_result.st_size,
            "mtime": stat_result.st_mtime,
            "allocated_size": None
        }
        if include_allocated_size:
            blocks = getattr(stat_result, "st_blocks", None)
            if blocks is not None:
                try:
                    details["allocated_size"] = int(blocks) * 512
                except (TypeError, ValueError, OverflowError):
                    details["allocated_size"] = None
        return details

    def __log_stop_resume_trace(self,
                                reason: str,
                                file_id: str,
                                file_name: str,
                                path_pair_id: Optional[str] = None,
                                is_dir: bool = False,
                                current_state: Optional[Any] = None,
                                remote_base_dir_path: Optional[str] = None,
                                local_base_dir_path: Optional[str] = None,
                                stopped_marked: bool = False):
        trace_file_id = getattr(self, "_Controller__stop_resume_trace_file_id", None)
        if trace_file_id is None or (trace_file_id != file_id and trace_file_id != file_name):
            return

        temp_path = None
        sidecar_path = None
        if local_base_dir_path is not None and not is_dir:
            temp_path = os.path.join(local_base_dir_path, file_name + Constants.LFTP_TEMP_FILE_SUFFIX)
            sidecar_path = temp_path + ".lftp-pget-status"

        temp_details = self.__get_stop_resume_trace_file_details(temp_path, include_allocated_size=True)
        sidecar_details = self.__get_stop_resume_trace_file_details(sidecar_path)
        logger = getattr(self, "_Controller__stop_resume_trace_logger", self.logger.getChild("StopResumeTrace"))
        logger.info(
            "stop_resume_trace %s",
            json.dumps({
                "reason": reason,
                "file_id": file_id,
                "filename": file_name,
                "path_pair_id": path_pair_id,
                "current_state": current_state,
                "remote_base_dir_path": remote_base_dir_path,
                "local_base_dir_path": local_base_dir_path,
                "stopped_marked": stopped_marked,
                "temp_path": temp_path,
                "temp_exists": temp_details["exists"],
                "temp_apparent_size": temp_details["size"],
                "temp_allocated_size": temp_details["allocated_size"],
                "temp_mtime": temp_details["mtime"],
                "sidecar_path": sidecar_path,
                "sidecar_exists": sidecar_details["exists"],
                "sidecar_size": sidecar_details["size"],
                "sidecar_mtime": sidecar_details["mtime"]
            }, sort_keys=True)
        )

    @staticmethod
    def __extract_target_archive_trace_selector_name(identifier: Optional[str]) -> Optional[str]:
        if identifier is None:
            return None
        try:
            parsed_identifier = json.loads(identifier)
        except (TypeError, ValueError, json.JSONDecodeError):
            return identifier
        if isinstance(parsed_identifier, list) and len(parsed_identifier) == 2 and isinstance(parsed_identifier[1], str):
            return parsed_identifier[1]
        return identifier

    def __is_target_archive_trace_enabled(self) -> bool:
        return self.__target_archive_trace_file_id is not None

    def __target_archive_trace_selector_matches_file(self, file_id: str, file_name: str) -> bool:
        if not self.__is_target_archive_trace_enabled():
            return False
        if self.__target_archive_trace_file_id == file_id or self.__target_archive_trace_file_id == file_name:
            return True
        selector_name = self.__extract_target_archive_trace_selector_name(self.__target_archive_trace_file_id)
        return selector_name == file_name

    @staticmethod
    def __summarize_target_archive_file(file: ModelFile) -> dict:
        return {
            "file_id": file.file_id,
            "name": file.name,
            "path_pair_id": file.path_pair_id,
            "path_pair_name": file.path_pair_name,
            "state": getattr(file.state, "name", file.state),
            "is_dir": file.is_dir,
            "local_size": file.local_size,
            "remote_size": file.remote_size,
            "is_extractable": file.is_extractable,
        }

    def __find_target_archive_model_file(self, file_name: str, file_id: Optional[str] = None):
        try:
            file_ids = self.__model.get_file_ids()
        except AttributeError:
            return None
        for candidate_file_id in file_ids:
            try:
                file = self.__model.get_file(candidate_file_id)
            except ModelError:
                continue
            if file_id is not None and file.file_id == file_id:
                return file
            if file.name == file_name and self.__target_archive_trace_selector_matches_file(file.file_id, file.name):
                return file
        return None

    def __trace_target_archive_event(self, event: str, payload: dict):
        if not self.__is_target_archive_trace_enabled():
            return
        trace_payload = {
            "event": event,
            "target_selector": self.__target_archive_trace_file_id,
        }
        trace_payload.update(payload)
        signature = json.dumps(trace_payload, sort_keys=True)
        if signature == self.__target_archive_trace_last_signature:
            return
        self.__target_archive_trace_last_signature = signature
        self.__target_archive_trace_logger.info("target_archive_trace %s", signature)

    def __record_breadcrumb(self,
                            stage: str,
                            message: str,
                            details: Optional[dict] = None,
                            event_type: str = "diagnostic",
                            file_id: Optional[str] = None,
                            path_pair_id: Optional[str] = None,
                            path_pair_name: Optional[str] = None,
                            corr_id: Optional[str] = None,
                            flow_id: Optional[str] = None,
                            trace_scope: str = "flow"):
        breadcrumb_trace = getattr(self.__context, "breadcrumb_trace", None)
        if breadcrumb_trace is None:
            return
        breadcrumb_trace.record(
            "controller",
            message,
            {} if details is None else details,
            stage=stage,
            event_type=event_type,
            corr_id=corr_id if corr_id is not None else (file_id if file_id is not None else stage),
            flow_id=flow_id,
            file_id=file_id,
            path_pair_id=path_pair_id,
            path_pair_name=path_pair_name,
            trace_scope=trace_scope,
        )

    def __command_state_lock(self):
        lock = getattr(self, "_Controller__command_flow_lock", None)
        if lock is None:
            lock = Lock()
            self.__command_flow_lock = lock
        return lock

    def __next_command_flow_id(self, command: "Controller.Command") -> str:
        lock = self.__command_state_lock()
        with lock:
            sequence = getattr(self, "_Controller__command_flow_sequence", 0) + 1
            self.__command_flow_sequence = sequence
        action_name = getattr(command.action, "name", str(command.action)).lower()
        return "cmd:{}:{}:{}".format(action_name, command.filename, sequence)

    @staticmethod
    def __command_corr_id(command: "Controller.Command", file: Optional[ModelFile] = None):
        if file is not None:
            return file.path_pair_id or file.file_id or command.filename
        return command.filename

    def __record_command_breadcrumb(self,
                                    command: "Controller.Command",
                                    message: str,
                                    details: dict,
                                    event_type: str = "state_transition",
                                    file: Optional[ModelFile] = None):
        self.__record_breadcrumb(
            stage="command",
            message=message,
            details=details,
            event_type=event_type,
            file_id=file.file_id if file is not None else None,
            path_pair_id=file.path_pair_id if file is not None else None,
            path_pair_name=file.path_pair_name if file is not None else None,
            corr_id=self.__command_corr_id(command, file),
            flow_id=getattr(command, "flow_id", None),
        )

    def __safe_command_queue_size(self):
        try:
            return self.__command_queue.qsize()
        except (NotImplementedError, AttributeError):
            return None

    def __trace_corr_id_from_files(self, files, fallback: str):
        if files is not None:
            for file in files:
                path_pair_id = getattr(file, "path_pair_id", None)
                if path_pair_id is not None:
                    return path_pair_id
                file_id = getattr(file, "file_id", None)
                if file_id is not None:
                    return file_id
        return fallback

    def __extract_status_matches_failed_result(self, status: ExtractStatus, failed_results) -> bool:
        status_file_id = getattr(status, "file_id", None)
        status_path_pair_id = getattr(status, "path_pair_id", None)
        for result in failed_results or []:
            result_file_id = getattr(result, "file_id", None)
            if status_file_id and result_file_id:
                if status_file_id == result_file_id:
                    return True
                continue

            result_path_pair_id = getattr(result, "path_pair_id", None)
            if status_path_pair_id and result_path_pair_id:
                if status_path_pair_id == result_path_pair_id and status.name == result.name:
                    return True
                continue

            if status.name == result.name:
                return True
        return False

    def __active_extracting_file_tuple(self, status: ExtractStatus):
        path_pair_id = getattr(status, "path_pair_id", None)
        path_pair_name = getattr(status, "path_pair_name", None)
        if path_pair_name is None:
            path_pair = self.__get_path_pair(path_pair_id)
            path_pair_name = getattr(path_pair, "name", None)
        return status.name, path_pair_id, path_pair_name

    def __temp_diag(self, stage: str, file_id: Optional[str] = None, **payload):
        self.__record_breadcrumb(
            stage=stage,
            message=stage,
            details=payload,
            event_type="diagnostic",
            file_id=file_id,
            corr_id=file_id if file_id is not None else stage
        )
        if self.__temp_diag_file_id is None:
            return
        if file_id is not None and file_id != self.__temp_diag_file_id:
            return
        payload["stage"] = stage
        if file_id is not None:
            payload["file_id"] = file_id
        signature = json.dumps(payload, sort_keys=True, default=str)
        if signature == self.__temp_diag_last_signature:
            return
        self.__temp_diag_last_signature = signature
        print("TEMP_DIAG {}".format(signature), flush=True)

    def __is_model_file_name_unambiguous(self, file_name: str) -> bool:
        try:
            file_ids = self.__model.get_file_ids()
        except AttributeError:
            return True

        matching_file_ids = 0
        try:
            for file_id in file_ids:
                try:
                    file = self.__model.get_file(file_id)
                except ModelError:
                    continue
                if file.name == file_name:
                    matching_file_ids += 1
                    if matching_file_ids > 1:
                        return False
        except TypeError:
            return True
        return matching_file_ids <= 1

    def clear_extracted_marker(self, file: ModelFile):
        stale_extracted_file_names = set()
        if file.file_id in self.__persist.extracted_file_names:
            stale_extracted_file_names.add(file.file_id)
        if file.name in self.__persist.extracted_file_names and self.__is_model_file_name_unambiguous(file.name):
            stale_extracted_file_names.add(file.name)
        if not stale_extracted_file_names:
            return

        self.logger.info(
            "Removing stale extracted list entries for blocked auto-extract: {}".format(
                stale_extracted_file_names
            )
        )
        self.__persist.extracted_file_names.difference_update(stale_extracted_file_names)
        self.__model_builder.set_extracted_files(self.__persist.extracted_file_names)

    def __move_from_staging(self, name: str, path_pair_id: Optional[str] = None) -> bool:
        if path_pair_id:
            staging_path = self.__get_staging_path(path_pair_id)
            path_pair = self.__get_path_pair(path_pair_id)
            final_path = path_pair.local_path if path_pair is not None else None
        else:
            staging_path = self.__staging_path
            final_path = self.__legacy_local_path

        if not staging_path or not final_path:
            self.logger.warning(
                "Failed to move '%s' from staging to final path: missing move root "
                "(path_pair_id=%s, staging_path=%s, final_path=%s)",
                name,
                path_pair_id,
                staging_path,
                final_path,
            )
            return False

        src = os.path.join(staging_path, name)
        dst = os.path.join(final_path, name)
        trace_file_id = ModelFile.build_file_id(name, path_pair_id)
        should_trace = self.__target_archive_trace_selector_matches_file(trace_file_id, name)
        if should_trace:
            self.__trace_target_archive_event("move_from_staging_attempt", {
                "file_id": trace_file_id,
                "file_name": name,
                "path_pair_id": path_pair_id,
                "staging_path": staging_path,
                "final_path": final_path,
                "source_path": src,
                "destination_path": dst,
                "source_exists": os.path.exists(src),
                "same_path": os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)),
            })
        if not os.path.exists(src):
            destination_exists = os.path.exists(dst)
            if not destination_exists:
                self.logger.warning(
                    "Failed to move '%s' from staging '%s' to '%s': source does not exist",
                    name,
                    staging_path,
                    final_path,
                )
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "missing_source",
                    "destination_exists": destination_exists,
                })
            return destination_exists
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "same_path",
                })
            return True
        if self.__source_has_lftp_temp_artifact(staging_path, src):
            self.logger.warning(
                "Deferring move of '%s' from staging '%s' to '%s': staging source still has an lftp temp artifact",
                name,
                staging_path,
                final_path,
            )
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "deferred_temp_files",
                })
            return False

        try:
            shutil.move(src, dst)
            self.logger.info("Moved '%s' from staging '%s' to '%s'", name, staging_path, final_path)
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "moved",
                })
            if path_pair_id is None:
                self.__local_scan_process.force_scan()
            else:
                self.__local_scan_process.force_scan(path_pair_id)
            return True
        except OSError as error:
            self.logger.warning(
                "Failed to move '%s' from staging '%s' to '%s': %s",
                name,
                staging_path,
                final_path,
                error
            )
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "failed",
                    "error": str(error),
                })
            return False

    @staticmethod
    def __source_has_lftp_temp_artifact(staging_path: str, src: str) -> bool:
        suffix = Constants.LFTP_TEMP_FILE_SUFFIX
        try:
            resolved_staging_root = os.path.realpath(staging_path)
            resolved_src = os.path.realpath(src)
        except OSError:
            return False

        if os.path.islink(src):
            return False

        try:
            if os.path.normcase(os.path.commonpath([resolved_staging_root, resolved_src])) != os.path.normcase(resolved_staging_root):
                return False
        except ValueError:
            return False

        temp_candidate = src + suffix
        try:
            resolved_temp_candidate = os.path.realpath(temp_candidate)
        except OSError:
            return False

        try:
            if os.path.normcase(os.path.commonpath([resolved_staging_root, resolved_temp_candidate])) != os.path.normcase(resolved_staging_root):
                return False
        except ValueError:
            return False

        try:
            return os.path.isfile(resolved_temp_candidate)
        except OSError:
            return False

    def __get_delete_local_target(self, file: ModelFile) -> Tuple[str, str]:
        path_pair = self.__get_path_pair(file.path_pair_id)
        final_path = path_pair.local_path if path_pair is not None else self.__legacy_local_path
        staging_path = self.__get_staging_path(file.path_pair_id if path_pair is not None else None)
        final_target = os.path.join(final_path, file.name)

        if os.path.exists(final_target) or not staging_path:
            return final_path, file.name

        staging_target = os.path.join(staging_path, file.name)
        if os.path.exists(staging_target):
            return staging_path, file.name

        staging_target = os.path.join(staging_path, file.name + Constants.LFTP_TEMP_FILE_SUFFIX)
        if os.path.exists(staging_target):
            return staging_path, file.name + Constants.LFTP_TEMP_FILE_SUFFIX

        return final_path, file.name

    @staticmethod
    def __is_delete_command_action(action: "Controller.Command.Action") -> bool:
        return action in (
            Controller.Command.Action.DELETE_LOCAL,
            Controller.Command.Action.DELETE_REMOTE,
        )

    def __canonical_delete_command_identity(self, command: "Controller.Command") -> str:
        identity = command.filename
        try:
            file = self.__model.get_file(command.filename)
        except Exception:
            file = None
        file_id = getattr(file, "file_id", None)
        if isinstance(file_id, str) and file_id:
            identity = file_id
        command.delete_identity = identity
        return identity

    @staticmethod
    def __delete_command_identity(command: "Controller.Command") -> str:
        return getattr(command, "delete_identity", command.filename)

    def __deferred_delete_commands(self) -> List["Controller.Command"]:
        commands = getattr(self, "_Controller__deferred_delete_command_refs", None)
        if commands is None:
            commands = []
            self.__deferred_delete_command_refs = commands
        return commands

    def __active_command_matches_delete(
        self,
        command_process: "Controller.CommandProcessWrapper",
        file_id: str,
        action: Optional["Controller.Command.Action"] = None
    ) -> bool:
        if action is not None and command_process.command.action != action:
            return False
        return command_process.file_id == file_id or \
            self.__delete_command_identity(command_process.command) == file_id

    def __queued_command_matches_delete(
        self,
        command: "Controller.Command",
        file_id: str,
        action: Optional["Controller.Command.Action"] = None
    ) -> bool:
        return self.__is_delete_command_action(command.action) and \
            (action is None or command.action == action) and \
            self.__delete_command_identity(command) == file_id

    def __find_pending_delete_command_unlocked(
        self,
        file_id: str,
        action: Optional["Controller.Command.Action"] = None
    ) -> Optional["Controller.Command"]:
        for command_process in self.__active_command_processes:
            if self.__active_command_matches_delete(command_process, file_id, action):
                return command_process.command

        with self.__command_queue.mutex:
            for command in self.__command_queue.queue:
                if self.__queued_command_matches_delete(command, file_id, action):
                    return command

        for command in self.__deferred_delete_commands():
            if self.__queued_command_matches_delete(command, file_id, action):
                return command
        return None

    def __has_active_command_for_file_unlocked(self, file_id: str, action: Optional["Controller.Command.Action"] = None) -> bool:
        return any(
            self.__active_command_matches_delete(command_process, file_id, action)
            for command_process in self.__active_command_processes
        )

    def __has_active_command_for_file(self, file_id: str, action: Optional["Controller.Command.Action"] = None) -> bool:
        with self.__command_state_lock():
            return self.__has_active_command_for_file_unlocked(file_id, action)

    def __has_pending_delete_command_unlocked(
        self,
        file_id: str,
        action: Optional["Controller.Command.Action"] = None
    ) -> bool:
        return self.__find_pending_delete_command_unlocked(file_id, action) is not None

    def __has_pending_delete_command(
        self,
        file_id: str,
        action: Optional["Controller.Command.Action"] = None
    ) -> bool:
        with self.__command_state_lock():
            return self.__has_pending_delete_command_unlocked(file_id, action)

    def __has_pending_delete_local_command(self, file_id: str) -> bool:
        return self.__has_pending_delete_command(file_id, Controller.Command.Action.DELETE_LOCAL)

    def __pending_delete_command_count_unlocked(self) -> int:
        with self.__command_queue.mutex:
            queued_count = sum(
                1
                for command in self.__command_queue.queue
                if self.__is_delete_command_action(command.action)
            )
        return queued_count + len(self.__deferred_delete_commands())

    def __defer_delete_command(
        self,
        command: "Controller.Command",
        deferred_commands: List["Controller.Command"]
    ) -> None:
        with self.__command_state_lock():
            deferred_commands.append(command)
            self.__deferred_delete_commands().append(command)

    def __requeue_deferred_delete_commands(self, deferred_commands: List["Controller.Command"]) -> None:
        with self.__command_state_lock():
            deferred_refs = self.__deferred_delete_commands()
            for deferred_command in deferred_commands:
                if deferred_command in deferred_refs:
                    deferred_refs.remove(deferred_command)
                self.__command_queue.put(deferred_command)

    def __delete_command_is_stale(self, command_process: "Controller.CommandProcessWrapper", now_monotonic: float) -> bool:
        started_at_monotonic = getattr(command_process, "started_at_monotonic", None)
        if started_at_monotonic is None:
            return False
        if not self.__is_delete_command_action(command_process.command.action):
            return False
        return (now_monotonic - started_at_monotonic) >= Controller._DELETE_COMMAND_STALE_TIMEOUT_IN_SECS

    def __should_auto_purge_local_file(self, file: ModelFile) -> bool:
        if file.is_dir or file.remote_size is not None or file.local_size != 0:
            return False
        if file.state != ModelFile.State.DEFAULT:
            return False
        if self.__is_previously_downloaded(file.name, file.path_pair_id) or \
                self.__is_explicitly_stopped(file.name, file.path_pair_id):
            return False
        if file.file_id in self.__persist.extracted_file_names or file.name in self.__persist.extracted_file_names:
            return False
        return not self.__has_pending_delete_local_command(file.file_id)

    def __queue_delete_local_process(
        self,
        file: ModelFile,
        post_callback: Callable,
        command: Optional["Controller.Command"] = None
    ):
        delete_local_path, delete_local_name = self.__get_delete_local_target(file)
        process = DeleteLocalProcess(
            local_path=delete_local_path,
            file_name=delete_local_name
        )
        process.set_multiprocessing_logger(self.__mp_logger)
        command_wrapper = Controller.CommandProcessWrapper(
            command=command or Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id),
            file_id=file.file_id,
            file_name=file.name,
            process=process,
            post_callback=post_callback,
            await_completion=True
        )
        with self.__command_state_lock():
            self.__active_command_processes.append(command_wrapper)
        command_wrapper.process.start()

    def __recover_interrupted_downloads(self, remote_files):
        self.__startup_recovery_done = True
        suffix = Constants.LFTP_TEMP_FILE_SUFFIX
        remote_names_by_pair = {}
        for remote_file in remote_files:
            remote_names_by_pair.setdefault(getattr(remote_file, "path_pair_id", None), set()).add(remote_file.name)

        staging_roots = self.__path_pair_staging_paths or {None: self.__staging_path}
        for path_pair_id, staging_path in staging_roots.items():
            try:
                staging_entries = os.listdir(staging_path)
            except OSError as error:
                self.logger.warning("Failed to inspect staging path '%s': %s", staging_path, error)
                continue

            remote_names = remote_names_by_pair.get(path_pair_id, set())
            for entry in staging_entries:
                entry_path = os.path.join(staging_path, entry)
                if entry.endswith(suffix):
                    file_name = entry[:-len(suffix)]
                    is_dir = False
                elif os.path.isdir(entry_path):
                    try:
                        has_temp_children = any(
                            child.endswith(suffix)
                            for child in os.listdir(entry_path)
                        )
                    except OSError:
                        continue
                    if not has_temp_children:
                        continue
                    file_name = entry
                    is_dir = True
                else:
                    continue

                if file_name not in remote_names or \
                        self.__is_previously_downloaded(file_name, path_pair_id) or \
                        self.__is_explicitly_stopped(file_name, path_pair_id):
                    continue

                path_pair = self.__get_path_pair(path_pair_id)
                try:
                    file_id = ModelFile.build_file_id(file_name, path_pair_id)
                    self.__log_stop_resume_trace(
                        "recover_interrupted_download",
                        file_id,
                        file_name,
                        path_pair_id,
                        is_dir,
                        None,
                        path_pair.remote_path if path_pair is not None else None,
                        staging_path,
                        False
                    )
                    self.__lftp.queue(
                        file_name,
                        is_dir,
                        remote_base_dir_path=path_pair.remote_path if path_pair is not None else None,
                        local_base_dir_path=staging_path
                    )
                    self.logger.info("Recovered interrupted download '%s' from '%s'", file_name, staging_path)
                except LftpError as error:
                    self.logger.warning(
                        "Failed to recover interrupted download '%s' from '%s': %s",
                        file_name,
                        staging_path,
                        error
                    )

    def __set_active_scanner_files(self, active_files):
        if isinstance(self.__active_scanner, MultiPathActiveScanner):
            self.__active_scanner.set_active_files(active_files)
        else:
            self.__active_scanner.set_active_files([name for name, _, _ in active_files])

    def __update_model(self):
        updater = getattr(self, "_Controller__updater", None)
        if not isinstance(updater, ModelUpdater):
            updater = ModelUpdater(self)
            self.__updater = updater
        updater.update()

    def __process_commands(self):
        def _notify_failure(_command: Controller.Command,
                            _msg: str,
                            _error_code: int = 400,
                            _file: Optional[ModelFile] = None):
            self.logger.warning("Command failed. {}".format(_msg))
            self.__record_command_breadcrumb(
                command=_command,
                message="command_failed",
                details={
                    "command": getattr(_command.action, "name", str(_command.action)),
                    "message": _msg,
                    "error_code": _error_code,
                    "file_name": _file.name if _file is not None else _command.filename,
                    "lifecycle_phase": "dispatch",
                },
                event_type="failure",
                file=_file,
            )
            for _callback in _command.callbacks:
                _callback.on_failure(_msg, _error_code)

        deferred_commands = []
        while not self.__command_queue.empty():
            command = self.__command_queue.get()
            self.logger.info("Received command {} for file {}".format(str(command.action), command.filename))
            try:
                file = self.__model.get_file(command.filename)
            except ModelError:
                self.__record_command_breadcrumb(
                    command=command,
                    message="command_dequeued",
                    details={
                        "command": getattr(command.action, "name", str(command.action)),
                        "file_name": command.filename,
                        "queue_size": self.__safe_command_queue_size(),
                    },
                )
                _notify_failure(command, "File '{}' not found".format(command.filename), 404)
                continue
            self.__record_command_breadcrumb(
                command=command,
                message="command_dequeued",
                details={
                    "command": getattr(command.action, "name", str(command.action)),
                    "file_name": file.name,
                    "queue_size": self.__safe_command_queue_size(),
                    "origin": getattr(command, "origin", "manual"),
                },
                file=file,
            )
            self.__temp_diag(
                "command_received",
                file_id=file.file_id,
                file_name=file.name,
                command=getattr(command.action, "name", str(command.action)),
                state=getattr(file.state, "name", file.state),
                local_size=file.local_size,
                remote_size=file.remote_size,
                is_dir=file.is_dir,
            )

            if command.action == Controller.Command.Action.QUEUE:
                if file.remote_size is None:
                    _notify_failure(command, "File '{}' does not exist remotely".format(command.filename), 404, file)
                    continue
                try:
                    path_pair = self.__get_path_pair(file.path_pair_id)
                    local_base_dir_path = self.__get_staging_path(file.path_pair_id if path_pair else None)
                    stopped_marked = self.__is_explicitly_stopped(file.name, file.path_pair_id)
                    self.__log_stop_resume_trace(
                        "queue_after_stop" if stopped_marked else "queue_fresh",
                        file.file_id,
                        file.name,
                        file.path_pair_id,
                        file.is_dir,
                        getattr(file.state, "name", file.state),
                        path_pair.remote_path if path_pair else None,
                        local_base_dir_path,
                        stopped_marked
                    )
                    self.__lftp.queue(
                        file.name,
                        file.is_dir,
                        remote_base_dir_path=path_pair.remote_path if path_pair else None,
                        local_base_dir_path=local_base_dir_path
                    )
                    Controller.__clear_persist_key(
                        self.__persist.stopped_file_names,
                        file.name,
                        file.path_pair_id
                    )
                    self.__record_command_breadcrumb(
                        command=command,
                        message="command_dispatched",
                        details={
                            "command": "QUEUE",
                            "mode": "lftp_queue",
                            "stopped_marked": stopped_marked,
                        },
                        file=file,
                    )
                except LftpError as e:
                    _notify_failure(command, "Lftp error: {}".format(str(e)), 500, file)
                    continue

            elif command.action == Controller.Command.Action.STOP:
                if file.state not in (ModelFile.State.DOWNLOADING, ModelFile.State.QUEUED):
                    _notify_failure(
                        command,
                        "File '{}' is not Queued or Downloading".format(command.filename),
                        409,
                        file
                    )
                    continue
                if not file.is_stoppable:
                    _notify_failure(
                        command,
                        "File '{}' could not be stopped".format(command.filename),
                        409,
                        file
                    )
                    continue
                try:
                    path_pair = self.__get_path_pair(file.path_pair_id)
                    remote_path = None
                    local_path = None
                    local_base_dir_path = self.__get_staging_path(file.path_pair_id if path_pair else None)
                    if path_pair is not None:
                        assert file.path_pair_id is not None
                        remote_path = "/".join([path_pair.remote_path.rstrip("/"), file.name])
                        local_path = self.__path_pair_staging_paths.get(file.path_pair_id, path_pair.local_path)
                    stopped_marked = file.file_id in self.__persist.stopped_file_names or \
                        file.name in self.__persist.stopped_file_names
                    self.__log_stop_resume_trace(
                        "stop",
                        file.file_id,
                        file.name,
                        file.path_pair_id,
                        file.is_dir,
                        getattr(file.state, "name", file.state),
                        path_pair.remote_path if path_pair else None,
                        local_base_dir_path,
                        stopped_marked
                    )
                    killed = self.__lftp.kill(
                        file.name,
                        path_pair_id=file.path_pair_id,
                        remote_path=remote_path,
                        local_path=local_path
                    )
                    if not killed:
                        _notify_failure(
                            command,
                            "File '{}' could not be stopped".format(command.filename),
                            409,
                            file
                        )
                        continue
                    self.__persist.stopped_file_names.add(file.file_id)
                    # Force the next model refresh to observe the post-stop lftp state
                    # instead of reusing the pre-stop running snapshot for one more cycle.
                    self.__next_lftp_status_poll_at = None
                    self.__record_command_breadcrumb(
                        command=command,
                        message="command_dispatched",
                        details={
                            "command": "STOP",
                            "mode": "lftp_kill",
                        },
                        file=file,
                    )
                except (LftpError, LftpJobStatusParserError) as e:
                    _notify_failure(command, "Lftp error: {}".format(str(e)), 500, file)
                    continue

            elif command.action == Controller.Command.Action.EXTRACT:
                self.__temp_diag(
                    "extract_command_evaluating",
                    file_id=file.file_id,
                    file_name=file.name,
                    state=getattr(file.state, "name", file.state),
                    local_size=file.local_size,
                    remote_size=file.remote_size,
                )
                # Note: We don't check the is_extractable flag because it's just a guess
                should_trace_target = self.__target_archive_trace_selector_matches_file(file.file_id, file.name)
                if file.state not in (
                        ModelFile.State.DEFAULT,
                        ModelFile.State.DOWNLOADED,
                        ModelFile.State.EXTRACTED
                ):
                    self.__temp_diag(
                        "extract_command_blocked",
                        file_id=file.file_id,
                        file_name=file.name,
                        state=getattr(file.state, "name", file.state),
                        reason="state_not_allowed",
                    )
                    if should_trace_target:
                        self.__trace_target_archive_event("extract_command_blocked", {
                            "file": self.__summarize_target_archive_file(file),
                            "reason": "state_not_allowed",
                        })
                    _notify_failure(
                        command,
                        "File '{}' in state {} cannot be extracted".format(
                            command.filename, str(file.state)
                        ),
                        409,
                        file
                    )
                    continue
                elif file.local_size is None:
                    self.__temp_diag(
                        "extract_command_blocked",
                        file_id=file.file_id,
                        file_name=file.name,
                        state=getattr(file.state, "name", file.state),
                        reason="missing_local_file",
                    )
                    if should_trace_target:
                        self.__trace_target_archive_event("extract_command_blocked", {
                            "file": self.__summarize_target_archive_file(file),
                            "reason": "missing_local_file",
                        })
                    _notify_failure(command, "File '{}' does not exist locally".format(command.filename), 404, file)
                    continue
                else:
                    self.__temp_diag(
                        "extract_command_queued",
                        file_id=file.file_id,
                        file_name=file.name,
                        state=getattr(file.state, "name", file.state),
                    )
                    if should_trace_target:
                        self.__trace_target_archive_event("extract_command_queued", {
                            "file": self.__summarize_target_archive_file(file),
                        })
                    try:
                        self.__extract_process.extract(file, flow_id=command.flow_id)
                    except Exception:
                        self.logger.warning(
                            "Extract worker dispatch failed for %s",
                            file.file_id,
                            exc_info=True
                        )
                        _notify_failure(
                            command,
                            "Extract worker unavailable",
                            500,
                            file
                        )
                        continue
                    self.__temp_diag(
                        "extract_command_dispatched",
                        file_id=file.file_id,
                        file_name=file.name,
                        state=getattr(file.state, "name", file.state),
                    )
                    self.__record_command_breadcrumb(
                        command=command,
                        message="command_dispatched",
                        details={
                            "command": "EXTRACT",
                            "mode": "extract_process_queue",
                        },
                        file=file,
                    )

            elif command.action == Controller.Command.Action.VALIDATE:
                if file.state not in (
                    ModelFile.State.DOWNLOADED,
                    ModelFile.State.EXTRACTED,
                    ModelFile.State.VALIDATED,
                    ModelFile.State.CORRUPT
                ):
                    _notify_failure(
                        command,
                        "File '{}' in state {} cannot be validated".format(
                            command.filename, str(file.state)
                        ),
                        409,
                        file
                    )
                    continue
                elif file.local_size is None:
                    _notify_failure(command, "File '{}' does not exist locally".format(command.filename), 404, file)
                    continue
                elif file.remote_size is None:
                    _notify_failure(command, "File '{}' does not exist remotely".format(command.filename), 404, file)
                    continue
                else:
                    try:
                        self.__validate_process.validate(file)
                    except Exception:
                        self.logger.warning(
                            "Validate worker dispatch failed for %s",
                            file.file_id,
                            exc_info=True
                        )
                        _notify_failure(
                            command,
                            "Validate worker unavailable",
                            500,
                            file
                        )
                        continue
                    self.__record_command_breadcrumb(
                        command=command,
                        message="command_dispatched",
                        details={
                            "command": "VALIDATE",
                            "mode": "validate_process_queue",
                        },
                        file=file,
                    )

            elif command.action == Controller.Command.Action.DELETE_LOCAL:
                if file.state not in (
                    ModelFile.State.DEFAULT,
                    ModelFile.State.DOWNLOADED,
                    ModelFile.State.EXTRACTED
                ):
                    _notify_failure(
                        command,
                        "Local file '{}' cannot be deleted in state {}".format(
                            command.filename, str(file.state)
                        ),
                        409,
                        file
                    )
                    continue
                elif file.local_size is None:
                    _notify_failure(command, "File '{}' does not exist locally".format(command.filename), 404, file)
                    continue
                else:
                    if len(self.__active_command_processes) >= Controller._MAX_CONCURRENT_COMMAND_PROCESSES:
                        self.__defer_delete_command(command, deferred_commands)
                        self.logger.debug(
                            "Deferring %s for '%s': %d active processes at cap",
                            command.action,
                            command.filename,
                            len(self.__active_command_processes)
                        )
                        continue
                    self.__queue_delete_local_process(
                        file,
                        self.__local_scan_process.force_scan,
                        command=command
                    )
                    self.__persist.stopped_file_names.add(file.file_id)
                    self.__validate_process.clear(file.file_id)
                    self.__record_command_breadcrumb(
                        command=command,
                        message="command_dispatched",
                        details={
                            "command": "DELETE_LOCAL",
                            "mode": "delete_local_process",
                            "await_completion": True,
                        },
                        file=file,
                    )

            elif command.action == Controller.Command.Action.DELETE_REMOTE:
                if file.state not in (
                    ModelFile.State.DEFAULT,
                    ModelFile.State.DOWNLOADED,
                    ModelFile.State.EXTRACTED,
                    ModelFile.State.DELETED
                ):
                    _notify_failure(
                        command,
                        "Remote file '{}' cannot be deleted in state {}".format(
                            command.filename, str(file.state)
                        ),
                        409,
                        file
                    )
                    continue
                elif file.remote_size is None:
                    _notify_failure(command, "File '{}' does not exist remotely".format(command.filename), 404, file)
                    continue
                else:
                    if len(self.__active_command_processes) >= Controller._MAX_CONCURRENT_COMMAND_PROCESSES:
                        self.__defer_delete_command(command, deferred_commands)
                        self.logger.debug(
                            "Deferring %s for '%s': %d active processes at cap",
                            command.action,
                            command.filename,
                            len(self.__active_command_processes)
                        )
                        continue
                    config = cast(Any, self.__context.config)
                    process = DeleteRemoteProcess(
                        remote_address=config.lftp.remote_address,
                        remote_username=config.lftp.remote_username,
                        remote_password=self.__ssh_password,
                        remote_port=config.lftp.remote_port,
                        remote_path=path_pair.remote_path if (path_pair := self.__get_path_pair(file.path_pair_id))
                        else self.__legacy_remote_path,
                        file_name=file.name
                    )
                    process.set_multiprocessing_logger(self.__mp_logger)
                    post_callback = self.__remote_scan_process.force_scan
                    command_wrapper = Controller.CommandProcessWrapper(
                        command=command,
                        file_id=file.file_id,
                        file_name=file.name,
                        process=process,
                        post_callback=post_callback,
                        await_completion=False
                    )
                    with self.__command_state_lock():
                        self.__active_command_processes.append(command_wrapper)
                    command_wrapper.process.start()
                    self.__validate_process.clear(file.file_id)
                    self.__record_command_breadcrumb(
                        command=command,
                        message="command_dispatched",
                        details={
                            "command": "DELETE_REMOTE",
                            "mode": "delete_remote_process",
                            "await_completion": False,
                        },
                        file=file,
                    )

            # If we get here, it was a success
            if command.action in (
                Controller.Command.Action.QUEUE,
                Controller.Command.Action.STOP
            ):
                self.__validate_process.clear(file.file_id)
            if not self.__is_delete_command_action(command.action):
                for callback in command.callbacks:
                    callback.on_success()
                self.__record_command_breadcrumb(
                    command=command,
                    message="command_finished",
                    details={
                        "command": getattr(command.action, "name", str(command.action)),
                        "lifecycle_phase": "dispatch",
                        "completion": "accepted",
                        },
                        file=file,
                    )

        self.__requeue_deferred_delete_commands(deferred_commands)

    def __log_memory_usage(self):
        with self.__model_lock:
            get_ids = getattr(self.__model, "get_file_ids", None)
            if callable(get_ids):
                model_file_count = len(cast(List[str], get_ids()))
            else:
                model_file_count = len(self.__model.get_file_names())

        self.__memory_monitor.log_if_due(
            model_file_count=model_file_count,
            downloaded_file_count=len(self.__persist.downloaded_file_names),
            extracted_file_count=len(self.__persist.extracted_file_names),
            stopped_file_count=len(self.__persist.stopped_file_names),
            active_download_count=len(self.__active_downloading_file_names),
            active_extract_count=len(self.__active_extracting_file_names),
            active_command_count=len(self.__active_command_processes)
        )

    def __propagate_exceptions(self):
        """
        Propagate any exceptions from child processes/threads to this thread
        :return:
        """
        try:
            self.__lftp.raise_pending_error()
        except LftpError as e:
            self.logger.warning("Caught lftp error: {}".format(str(e)))
        self.__active_scan_process.propagate_exception()
        self.__local_scan_process.propagate_exception()
        try:
            self.__remote_scan_process.propagate_exception()
        except Exception as error:
            self.__record_first_remote_scan_failure(str(error))
            raise
        self.__mp_logger.propagate_exception()
        try:
            self.__extract_process.propagate_exception()
        except Exception as exc:
            self.logger.warning(
                "Ignoring extract worker failure during controller loop: {}".format(str(exc)),
                exc_info=True
            )
        self.__report_dead_worker_once(self.__extract_process, "extract")
        try:
            self.__validate_process.propagate_exception()
        except Exception as exc:
            self.logger.warning(
                "Ignoring validate worker failure during controller loop: {}".format(str(exc)),
                exc_info=True
            )
        self.__report_dead_worker_once(self.__validate_process, "validate")

    def __record_first_remote_scan_failure(self, error_message: str):
        self.logger.warning("Fatal remote scan failure recorded: {}".format(error_message))
        self.__context.status.controller.latest_remote_scan_time = datetime.now()
        self.__context.status.controller.latest_remote_scan_failed = True
        self.__context.status.controller.latest_remote_scan_error = error_message
        self.__record_breadcrumb(
            stage="scan",
            message="remote_scan_failure",
            details={
                "error_message": error_message,
            },
            event_type="failure",
            corr_id="remote_scan:aggregate",
            trace_scope="aggregate",
        )

    def __cleanup_commands(self):
        """
        Cleanup the list of active commands and do any callbacks
        :return:
        """
        self.__temp_diag("cleanup_commands", active_command_count=len(self.__active_command_processes))
        now_monotonic = time.monotonic()
        still_active_processes = []
        for command_process in self.__active_command_processes:
            if command_process.process.is_alive():
                if self.__delete_command_is_stale(command_process, now_monotonic):
                    started_at_monotonic = getattr(command_process, "started_at_monotonic", now_monotonic)
                    elapsed_seconds = now_monotonic - started_at_monotonic
                    self.logger.warning(
                        "Stale delete command %s for file %s timed out after %.1fs; terminating",
                        command_process.command.action,
                        command_process.file_name,
                        elapsed_seconds,
                    )
                    try:
                        command_process.process.terminate()
                    except Exception as error:
                        self.logger.warning(
                            "Failed to terminate stale delete command %s for file %s: %s",
                            command_process.command.action,
                            command_process.file_name,
                            error,
                            exc_info=True
                        )
                    try:
                        self.__record_command_breadcrumb(
                            command=command_process.command,
                            message="command_failed",
                            details={
                                "command": getattr(command_process.command.action, "name", str(command_process.command.action)),
                                "message": "Delete command timed out",
                                "error_code": 504,
                                "file_name": command_process.file_name,
                                "lifecycle_phase": "cleanup",
                                "completion": "timed_out",
                                "timeout_seconds": Controller._DELETE_COMMAND_STALE_TIMEOUT_IN_SECS,
                                "elapsed_seconds": elapsed_seconds,
                            },
                            event_type="failure",
                        )
                        if command_process.await_completion:
                            self.__persist.stopped_file_names.discard(command_process.file_id)
                        for callback in command_process.command.callbacks:
                            callback.on_failure(
                                "Delete command for file '{}' timed out".format(command_process.file_name),
                                504
                            )
                    finally:
                        self.__bounded_join("stale delete command process join", command_process.process)
                        command_process.process.close_queues()
                    continue
                still_active_processes.append(command_process)
            else:
                try:
                    if command_process.await_completion:
                        try:
                            command_process.process.propagate_exception()
                        except FileNotFoundError as error:
                            self.__record_command_breadcrumb(
                                command=command_process.command,
                                message="command_failed",
                                details={
                                    "command": getattr(command_process.command.action, "name", str(command_process.command.action)),
                                    "message": str(error),
                                    "error_code": 404,
                                    "file_name": command_process.file_name,
                                    "lifecycle_phase": "cleanup",
                                },
                                event_type="failure",
                            )
                            self.logger.warning(
                                "Command {} for file {} failed: {}".format(
                                    command_process.command.action,
                                    command_process.file_name,
                                    error
                                )
                            )
                            self.__persist.stopped_file_names.discard(command_process.file_id)
                            for callback in command_process.command.callbacks:
                                callback.on_failure(
                                    "File '{}' does not exist locally".format(command_process.file_name),
                                    404
                                )
                        except Exception as error:
                            self.__record_command_breadcrumb(
                                command=command_process.command,
                                message="command_failed",
                                details={
                                    "command": getattr(command_process.command.action, "name", str(command_process.command.action)),
                                    "message": str(error),
                                    "error_code": 500,
                                    "file_name": command_process.file_name,
                                    "lifecycle_phase": "cleanup",
                                },
                                event_type="failure",
                            )
                            self.logger.warning(
                                "Command {} for file {} failed: {}".format(
                                    command_process.command.action,
                                    command_process.file_name,
                                    error
                                )
                            )
                            self.__persist.stopped_file_names.discard(command_process.file_id)
                            for callback in command_process.command.callbacks:
                                callback.on_failure(
                                    "Failed to delete local file '{}'".format(command_process.file_name),
                                    500
                                )
                        else:
                            command_process.post_callback()
                            for callback in command_process.command.callbacks:
                                callback.on_success()
                            self.__record_command_breadcrumb(
                                command=command_process.command,
                                message="command_finished",
                                details={
                                    "command": getattr(command_process.command.action, "name", str(command_process.command.action)),
                                    "lifecycle_phase": "cleanup",
                                    "completion": "completed",
                                },
                            )
                    else:
                        # Do the post callback
                        command_process.post_callback()
                        # Propagate the exception without crashing the controller loop
                        try:
                            command_process.process.propagate_exception()
                        except Exception as error:
                            self.logger.warning(
                                "Command process failed: %s",
                                command_process.process.name,
                                exc_info=True
                            )
                            self.__record_command_breadcrumb(
                                command=command_process.command,
                                message="command_failed",
                                details={
                                    "command": getattr(command_process.command.action, "name", str(command_process.command.action)),
                                    "message": str(error),
                                    "error_code": 500,
                                    "file_name": command_process.file_name,
                                    "lifecycle_phase": "cleanup",
                                },
                                event_type="failure",
                            )
                            for callback in command_process.command.callbacks:
                                callback.on_failure(
                                    "Failed to delete remote file '{}'".format(command_process.file_name),
                                    500
                                )
                        else:
                            for callback in command_process.command.callbacks:
                                callback.on_success()
                            self.__record_command_breadcrumb(
                                command=command_process.command,
                                message="command_finished",
                                details={
                                    "command": getattr(command_process.command.action, "name", str(command_process.command.action)),
                                    "lifecycle_phase": "cleanup",
                                    "completion": "completed",
                                },
                            )
                finally:
                    command_process.process.join()
                    command_process.process.close_queues()
        with self.__command_state_lock():
            self.__active_command_processes = still_active_processes
