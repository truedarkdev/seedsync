# Copyright 2017, Inderpreet Singh, All rights reserved.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Set, Tuple, cast
from threading import Lock, RLock
from queue import Queue
from enum import Enum
from datetime import datetime
import copy
import json
import os
import ntpath
import stat
import time
import shutil
from dataclasses import dataclass

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
from .extract import ExtractFailedResult, ExtractProcess, ExtractRequest, ExtractStatus
from .validate import ValidateProcess
from .model_updater import ModelUpdater
from .model_builder import ModelBuilder
from .memory_monitor import ControllerMemoryMonitor
from common import (
    AppError, AppOneShotProcess, AppProcess, Args, Config, Constants, Context,
    Localization, MultiprocessingLogger, PathPair, PathPairManager,
)
from model import ModelError, ModelFile, Model, IModelListener
from lftp import Lftp, LftpError, LftpJobStatus, LftpJobStatusParserError
from transfer import RcloneTransferBackend, create_transfer_backend, RcloneTransferError
from .controller_persist import ControllerPersist
from .persist_keys import persist_key, strip_persist_key
from .delete import DeleteLocalProcess, DeleteRemoteProcess
from system import SystemFile

ActiveScannerRuntime = ActiveScanner | MultiPathActiveScanner
LocalScannerRuntime = LocalScanner | MultiPathLocalScanner
RemoteScannerRuntime = RemoteScanner | MultiPathRemoteScanner


class _PathPairTransferBackend(Protocol):
    def set_path_pairs(self, path_pairs: list[PathPair]) -> None: ...


class ControllerError(AppError):
    """
    Exception indicating a controller error
    """
    pass


@dataclass(frozen=True)
class DownloadStartLifecycleEntry:
    state: str
    path_pair_id: Optional[str]
    transitioned_at: datetime


@dataclass
class PendingQueueDispatch:
    accepted_at_monotonic: float


class Controller:
    """
    Top-level class that controls the behaviour of the app
    """
    class MoveFromStagingResult(Enum):
        COMPLETED = 0
        ALREADY_COMPLETED = 1
        DEFERRED = 2
        FAILED = 3
        NO_MOVE_APPLICABLE = 4

    __MAX_MOVE_FAILURES = 4
    __MOVE_RETRY_DELAYS = (2, 10, 30)

    __context: Context
    __persist: ControllerPersist
    __command_queue: Queue[Controller.Command]
    __model: Model
    __model_builder: ModelBuilder
    __updater: ModelUpdater
    __path_pairs_by_id: dict[str, PathPair]
    __path_pair_staging_paths: dict[str, str]
    __active_scanner: ActiveScannerRuntime
    __local_scanner: LocalScannerRuntime
    __remote_scanner: RemoteScannerRuntime
    __active_scan_process: ScannerProcess
    __local_scan_process: ScannerProcess
    __remote_scan_process: ScannerProcess
    __extract_process: ExtractProcess
    __validate_process: ValidateProcess
    __lftp: Lftp | RcloneTransferBackend
    __mp_logger: MultiprocessingLogger
    __active_downloading_file_names: list[tuple[str, Optional[str], Optional[str]]]
    __active_extracting_file_names: list[tuple[str, Optional[str], Optional[str]]]
    __prev_downloading_file_names: set[tuple[str, Optional[str], Optional[str]]]
    __pending_completion_file_names: set[tuple[str, Optional[str], Optional[str]]]
    __move_retry_due: dict[str, datetime]
    __move_attempt_reservations: set[str]
    __deferred_move_file_ids: set[str]
    __malformed_status_only_file_ids: set[str]
    __pending_auto_purge_file_ids: set[str]
    __last_lftp_statuses: Optional[list[LftpJobStatus]]
    __next_lftp_status_poll_at: Optional[datetime]
    __lftp_status_cache_expires_at: Optional[datetime]
    __active_command_processes: list[Controller.CommandProcessWrapper]
    __reported_dead_workers: set[int]
    __remote_delete_success_listeners: list[Callable[[ModelFile], None]]
    __download_start_listeners: list[Callable[[ModelFile], None]]
    __download_start_state: dict[str, DownloadStartLifecycleEntry]
    __deferred_delete_command_refs: list[Controller.Command]
    __startup_validation_error: Optional[str]
    __path_pair_runtime_error: Optional[str]
    __stop_resume_trace_file_id: Optional[str]
    __target_archive_trace_file_id: Optional[str]
    __target_archive_trace_last_signature: Optional[str]
    __temp_diag_file_id: Optional[str]
    __temp_diag_last_signature: Optional[str]

    # ModelUpdater intentionally shares this controller-owned runtime state.
    # The explicit mangled aliases describe that combined strict boundary
    # without adding a public façade or changing the runtime representation.
    _Controller__context: Context
    _Controller__persist: ControllerPersist
    _Controller__model: Model
    _Controller__model_builder: ModelBuilder
    _Controller__path_pairs_by_id: dict[str, PathPair]
    _Controller__active_scan_process: ScannerProcess
    _Controller__local_scan_process: ScannerProcess
    _Controller__remote_scan_process: ScannerProcess
    _Controller__extract_process: ExtractProcess
    _Controller__validate_process: ValidateProcess
    _Controller__lftp: Lftp | RcloneTransferBackend
    _Controller__active_downloading_file_names: list[tuple[str, Optional[str], Optional[str]]]
    _Controller__active_extracting_file_names: list[tuple[str, Optional[str], Optional[str]]]
    _Controller__prev_downloading_file_names: set[tuple[str, Optional[str], Optional[str]]]
    _Controller__pending_completion_file_names: set[tuple[str, Optional[str], Optional[str]]]
    _Controller__move_retry_due: dict[str, datetime]
    _Controller__move_attempt_lock: Lock
    _Controller__move_attempt_reservations: set[str]
    _Controller__deferred_move_file_ids: set[str]
    _Controller__malformed_status_only_file_ids: set[str]
    _Controller__pending_auto_purge_file_ids: set[str]
    _Controller__last_lftp_statuses: Optional[list[LftpJobStatus]]
    _Controller__next_lftp_status_poll_at: Optional[datetime]
    _Controller__lftp_status_poll_retry_seconds: int
    _Controller__lftp_status_cache_expires_at: Optional[datetime]
    _Controller__lftp_status_cache_max_age_seconds: int
    _Controller__lftp_status_poll_retry_active: bool
    _Controller__startup_recovery_done: bool
    _Controller__exclude_patterns: str
    _Controller__model_lock: RLock
    _Controller__MAX_MOVE_FAILURES: int
    _Controller__MOVE_RETRY_DELAYS: tuple[int, ...]

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
            RETRY_MOVE = 6

        class ICallback(ABC):
            """Command callback interface"""
            @abstractmethod
            def on_success(self) -> None:
                """Called on successful completion of action"""
                pass

            @abstractmethod
            def on_failure(self, error: str, error_code: int = 400) -> None:
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
            self.callbacks: List[Controller.Command.ICallback] = []
            self.duplicate_waiter_count = 0
            self.delete_identity = filename

        def add_callback(self, callback: ICallback) -> None:
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
            post_callback: Callable[[], None],
            await_completion: bool,
            started_at_monotonic: float | None = None,
            event_file: Optional[ModelFile] = None,
        ):
            self.command = command
            self.file_id = file_id
            self.file_name = file_name
            self.process = process
            self.post_callback = post_callback
            self.await_completion = await_completion
            self.started_at_monotonic = time.monotonic() if started_at_monotonic is None else started_at_monotonic
            self.event_file = event_file

    _MAX_CONCURRENT_COMMAND_PROCESSES = 8
    _MAX_PENDING_DELETE_COMMANDS = _MAX_CONCURRENT_COMMAND_PROCESSES * 2
    _MAX_DUPLICATE_DELETE_WAITERS = _MAX_CONCURRENT_COMMAND_PROCESSES
    _DELETE_COMMAND_STALE_TIMEOUT_IN_SECS = 10 * 60

    @staticmethod
    def __lftp_status_refresh_timing(interval_ms_downloading_scan: int) -> tuple[int, int]:
        # Keep the unhealthy retry window close to the downloading scan
        # cadence so a brief lftp hiccup does not pin a finished transfer in
        # a stale state.
        lftp_status_poll_retry_seconds = max(1, int(interval_ms_downloading_scan / 1000))
        lftp_status_cache_max_age_seconds = max(3, lftp_status_poll_retry_seconds * 3)
        return lftp_status_poll_retry_seconds, lftp_status_cache_max_age_seconds

    @staticmethod
    def _is_missing_startup_value(value: object) -> bool:
        return value is None or (
            isinstance(value, str) and (value.strip() == "" or value == "<replace me>")
        )

    @staticmethod
    def __require_runtime_path(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value:
            raise ControllerError("Missing validated runtime path: {}".format(field_name))
        return value

    @staticmethod
    def __require_runtime_int(value: object, field_name: str) -> int:
        if type(value) is not int:
            raise ControllerError("Missing validated runtime integer: {}".format(field_name))
        return value

    @staticmethod
    def __require_runtime_bool(value: object, field_name: str) -> bool:
        if type(value) is not bool:
            raise ControllerError("Missing validated runtime boolean: {}".format(field_name))
        return value

    @staticmethod
    def __runtime_int_or_default(value: object, default: int) -> int:
        return value if type(value) is int else default

    @staticmethod
    def __runtime_bool_or_default(value: object, default: bool) -> bool:
        return value if type(value) is bool else default

    @staticmethod
    def __runtime_str_or_default(value: object, default: str) -> str:
        return value if isinstance(value, str) else default

    def __set_transfer_path_pairs(self, path_pairs: list[PathPair]) -> None:
        backend = cast(_PathPairTransferBackend, self.__lftp)
        backend.set_path_pairs(path_pairs)

    @staticmethod
    def __get_exclude_patterns(config: object) -> str:
        controller_exclude_patterns = getattr(config, "_Controller__exclude_patterns", None)
        if isinstance(controller_exclude_patterns, str):
            return controller_exclude_patterns
        config_obj = getattr(config, "config", config)
        general_cfg = getattr(config_obj, "general", None)
        exclude_patterns = getattr(general_cfg, "exclude_patterns", "")
        return exclude_patterns if isinstance(exclude_patterns, str) else ""

    @staticmethod
    def collect_missing_startup_fields(
        config: Config,
        args: Optional[Args] = None,
        path_pair_manager: Optional[PathPairManager] = None,
    ) -> List[str]:
        def _append_missing(section_name: str, field_name: str, value: object) -> None:
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

        transfer_backend = getattr(lftp_cfg, "transfer_backend", "lftp")
        # The controller always starts Lftp, so the username remains required
        # even when key-based auth is enabled. Password auth is conditional.
        _append_missing("Lftp", "remote_address", getattr(lftp_cfg, "remote_address", None))
        _append_missing("Lftp", "remote_username", getattr(lftp_cfg, "remote_username", None))
        transfer_protocol = getattr(lftp_cfg, "protocol", "sftp")
        _append_missing("Lftp", "protocol", transfer_protocol)
        if getattr(lftp_cfg, "use_ssh_key", None) is False or (
            transfer_backend == "lftp" and transfer_protocol == "ftps"
        ):
            _append_missing("Lftp", "remote_password", getattr(lftp_cfg, "remote_password", None))
        if transfer_backend == "lftp" and transfer_protocol == "ftps":
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
        self.__initialize_startup_failure(error_message)

    def __initialize_startup_failure(self, error_message: str) -> None:
        self.__startup_validation_error = error_message
        self.__context.status.server.up = False
        self.__context.status.server.error_msg = error_message
        self.logger.error(error_message)
        self.__password = None
        self.__ssh_password = None
        self.__transfer_password = None
        self.__staging_path = ""
        # These attributes are unavailable only in the explicit failed-startup
        # state. Keep that exceptional runtime representation out of their
        # normal operational types; public lifecycle methods gate this state.
        failed_startup_attributes = (
            "__lftp",
            "__legacy_local_path",
            "__legacy_remote_path",
            "__active_scanner",
            "__local_scanner",
            "__remote_scanner",
            "__active_scan_process",
            "__local_scan_process",
            "__remote_scan_process",
            "__extract_process",
            "__validate_process",
            "__mp_logger",
        )
        for attribute_name in failed_startup_attributes:
            object.__setattr__(self, "_Controller{}".format(attribute_name), None)
        self.__active_downloading_file_names = []
        self.__active_extracting_file_names = []
        self.__prev_downloading_file_names = set()
        self.__pending_completion_file_names = set()
        self.__move_retry_due = {}
        self.__move_attempt_reservations = set()
        self.__move_attempt_lock = Lock()
        self.__deferred_move_file_ids = set()
        self.__malformed_status_only_file_ids = set()
        self.__pending_auto_purge_file_ids = set()
        self.__exclude_patterns = ""
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
        self.__pending_queue_dispatches: Dict[str, PendingQueueDispatch] = {}

        # The model
        self.__model = Model()
        self.__model.set_base_logger(self.logger)
        # Lock for the model. Listeners may re-enter controller model access
        # while the model updater is mutating the model, so this must be reentrant.
        self.__model_lock = RLock()
        self.__remote_delete_success_listeners = []
        self.__remote_delete_success_listeners_lock = Lock()
        self.__download_start_listeners = []
        self.__download_start_state = {}
        self.__download_start_lock = Lock()
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

        config = self.__context.config
        self.__exclude_patterns = Controller.__get_exclude_patterns(config)
        startup_args = getattr(self.__context, "args", None)
        if startup_args is None:
            startup_args = Args()
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
        self.__ssh_password = lftp_cfg.remote_password if not lftp_cfg.use_ssh_key else None
        self.__transfer_password = (
            lftp_cfg.remote_password if lftp_cfg.transfer_backend == "lftp" and lftp_cfg.protocol == "ftps"
            else self.__ssh_password
        )
        self.__password = self.__ssh_password

        enabled_path_pairs = self.__get_enabled_path_pairs()
        first_path_pair = enabled_path_pairs[0] if enabled_path_pairs else None
        legacy_local_path = lftp_cfg.local_path
        if Controller._is_missing_startup_value(legacy_local_path) and first_path_pair is not None:
            legacy_local_path = first_path_pair.local_path
        legacy_remote_path = lftp_cfg.remote_path
        if Controller._is_missing_startup_value(legacy_remote_path) and first_path_pair is not None:
            legacy_remote_path = first_path_pair.remote_path
        self.__legacy_local_path = Controller.__require_runtime_path(legacy_local_path, "Lftp.local_path")
        self.__legacy_remote_path = Controller.__require_runtime_path(legacy_remote_path, "Lftp.remote_path")

        self.__staging_path = self.__build_staging_path(
            self.__legacy_local_path,
            lftp_cfg.staging_path
        )

        # Lftp
        try:
            self.__lftp = create_transfer_backend(lftp_cfg, self.__transfer_password, self.__ssh_password)
            self.__lftp.set_base_logger(self.logger)
            self.__lftp.set_base_remote_dir_path(self.__legacy_remote_path)
            self.__lftp.set_base_local_dir_path(self.__staging_path)
            self.__configure_lftp()
        except (LftpError, RcloneTransferError) as exc:
            self.__initialize_startup_failure(str(exc))
            return

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
            out_dir_path = Controller.__require_runtime_path(
                controller_cfg.extract_path, "Controller.extract_path"
            )
        # Keep the final local root primary, but allow archive lookup to fall
        # back to staging so extraction can survive the move boundary.
        self.__extract_process = ExtractProcess(
            out_dir_path=out_dir_path,
            local_path=self.__legacy_local_path,
            local_path_fallback=self.__staging_path,
            managed_extract_folders_enabled=controller_cfg.managed_extract_folders_enabled,
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter()
        )
        path_pairs_for_validation: dict[str, object] = dict(self.__path_pairs_by_id)
        self.__validate_process = ValidateProcess(
            remote_address=Controller.__require_runtime_path(lftp_cfg.remote_address, "Lftp.remote_address"),
            remote_username=Controller.__require_runtime_path(lftp_cfg.remote_username, "Lftp.remote_username"),
            remote_password=self.__ssh_password,
            remote_port=Controller.__require_runtime_int(lftp_cfg.remote_port, "Lftp.remote_port"),
            local_path=self.__legacy_local_path,
            remote_path=self.__legacy_remote_path,
            path_pairs_by_id=path_pairs_for_validation
        )

        # Setup multiprocess logging
        self.__mp_logger = MultiprocessingLogger(self.logger)
        self.__active_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
        self.__local_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
        self.__remote_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
        self.__extract_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
        self.__validate_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)

        # Keep track of active files
        self.__active_downloading_file_names = []
        self.__active_extracting_file_names = []
        # Path-pair aware completion tracking so a finished download stays
        # visible until the model reaches a terminal state.
        self.__prev_downloading_file_names = set()
        self.__pending_completion_file_names = set()
        self.__move_retry_due = {}
        self.__move_attempt_reservations = set()
        self.__move_attempt_lock = Lock()
        self.__deferred_move_file_ids = set()
        self.__malformed_status_only_file_ids = set()
        self.__pending_auto_purge_file_ids = set()
        self.__last_lftp_statuses = []
        self.__next_lftp_status_poll_at = None
        (
            self.__lftp_status_poll_retry_seconds,
            self.__lftp_status_cache_max_age_seconds
        ) = Controller.__lftp_status_refresh_timing(Controller.__require_runtime_int(
            controller_cfg.interval_ms_downloading_scan, "Controller.interval_ms_downloading_scan"
        ))
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
        # Configure the active transfer backend while preserving the legacy lftp
        # runtime path unchanged when lftp remains selected.
        config = self.__context.config
        lftp_cfg = config.lftp
        validate_cfg = getattr(config, "validate", None)
        general_cfg = config.general
        self.__lftp.num_parallel_jobs = Controller.__runtime_int_or_default(
            lftp_cfg.num_max_parallel_downloads, 1
        )
        self.__lftp.num_parallel_files = Controller.__runtime_int_or_default(
            lftp_cfg.num_max_parallel_files_per_download, 1
        )
        self.__lftp.num_connections_per_root_file = Controller.__runtime_int_or_default(
            lftp_cfg.num_max_connections_per_root_file, 1
        )
        self.__lftp.num_connections_per_dir_file = Controller.__runtime_int_or_default(
            lftp_cfg.num_max_connections_per_dir_file, 1
        )
        self.__lftp.num_max_total_connections = Controller.__runtime_int_or_default(
            lftp_cfg.num_max_total_connections, 0
        )
        self.__lftp.use_temp_file = Controller.__runtime_bool_or_default(lftp_cfg.use_temp_file, False)
        rate_limit = lftp_cfg.rate_limit
        self.__lftp.rate_limit = 0 if rate_limit in (None, "") else rate_limit
        net_socket_buffer = lftp_cfg.net_socket_buffer
        self.__lftp.net_socket_buffer = 0 if net_socket_buffer in (None, "") else net_socket_buffer
        if getattr(self.__lftp, "backend_name", "lftp") != "rclone":
            self.__lftp.temp_file_name = "*" + Constants.LFTP_TEMP_FILE_SUFFIX
            if getattr(validate_cfg, "xfer_verify", True):
                self.__lftp.xfer_verify = True
                self.__lftp.xfer_verify_command = ValidateProcess.HASH_COMMAND
            else:
                self.__lftp.xfer_verify = False
        self.__lftp.set_verbose_logging(Controller.__runtime_bool_or_default(general_cfg.verbose, False))

    def __get_enabled_path_pairs(self) -> List[PathPair]:
        if self.__context.path_pair_manager is None:
            return []
        return self.__context.path_pair_manager.get_enabled_pairs()

    def __refresh_path_pair_runtime_state(self, enabled_path_pairs: Optional[List[PathPair]] = None):
        if enabled_path_pairs is None:
            enabled_path_pairs = self.__get_enabled_path_pairs()

        config = self.__context.config
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
            interval_in_ms=Controller.__require_runtime_int(
                controller_cfg.interval_ms_downloading_scan, "Controller.interval_ms_downloading_scan"
            ),
            verbose=False,
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter()
        )
        local_scan_process = ScannerProcess(
            scanner=local_scanner,
            interval_in_ms=Controller.__require_runtime_int(
                controller_cfg.interval_ms_local_scan, "Controller.interval_ms_local_scan"
            ),
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter()
        )
        remote_scan_process = ScannerProcess(
            scanner=remote_scanner,
            interval_in_ms=Controller.__require_runtime_int(
                controller_cfg.interval_ms_remote_scan, "Controller.interval_ms_remote_scan"
            ),
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter()
        )

        self.__set_transfer_path_pairs(lftp_path_pairs)
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

    def __build_active_scanner(
        self, enabled_path_pairs: List[PathPair], path_pair_staging_paths: dict[str, str]
    ) -> ActiveScannerRuntime:
        config = self.__context.config
        if enabled_path_pairs:
            return MultiPathActiveScanner({
                pair.id: path_pair_staging_paths[pair.id] for pair in enabled_path_pairs
            }, use_temp_file=Controller.__require_runtime_bool(config.lftp.use_temp_file, "Lftp.use_temp_file"))
        return ActiveScanner(
            self.__staging_path,
            use_temp_file=Controller.__require_runtime_bool(config.lftp.use_temp_file, "Lftp.use_temp_file")
        )

    def __build_local_scanner(
        self, enabled_path_pairs: List[PathPair], path_pair_staging_paths: dict[str, str]
    ) -> LocalScannerRuntime:
        config = self.__context.config
        if enabled_path_pairs:
            return MultiPathLocalScanner([
                LocalScanner(
                    local_path=pair.local_path,
                    use_temp_file=Controller.__require_runtime_bool(config.lftp.use_temp_file, "Lftp.use_temp_file"),
                    staging_path=path_pair_staging_paths[pair.id],
                    managed_extract_folders_enabled=Controller.__require_runtime_bool(
                        config.controller.managed_extract_folders_enabled,
                        "Controller.managed_extract_folders_enabled",
                    ),
                    path_pair_id=pair.id,
                    path_pair_name=pair.name
                ) for pair in enabled_path_pairs
            ])
        return LocalScanner(
            local_path=self.__legacy_local_path,
            use_temp_file=Controller.__require_runtime_bool(config.lftp.use_temp_file, "Lftp.use_temp_file"),
            staging_path=self.__staging_path,
            managed_extract_folders_enabled=Controller.__require_runtime_bool(
                config.controller.managed_extract_folders_enabled,
                "Controller.managed_extract_folders_enabled",
            )
        )

    def __build_remote_scanner(self, enabled_path_pairs: List[PathPair]) -> RemoteScannerRuntime:
        config = self.__context.config
        remote_python_path = getattr(config.lftp, "remote_python_path", None)
        if not isinstance(remote_python_path, str):
            remote_python_path = None
        if enabled_path_pairs:
            return MultiPathRemoteScanner([
                RemoteScanner(
                    remote_address=Controller.__require_runtime_path(config.lftp.remote_address, "Lftp.remote_address"),
                    remote_username=Controller.__require_runtime_path(config.lftp.remote_username, "Lftp.remote_username"),
                    remote_password=self.__ssh_password,
                    remote_port=Controller.__require_runtime_int(config.lftp.remote_port, "Lftp.remote_port"),
                    remote_path_to_scan=pair.remote_path,
                    local_path_to_scan_script=Controller.__require_runtime_path(
                        self.__context.args.local_path_to_scanfs, "Args.local_path_to_scanfs"
                    ),
                    remote_path_to_scan_script=Controller.__require_runtime_path(
                        config.lftp.remote_path_to_scan_script, "Lftp.remote_path_to_scan_script"
                    ),
                    remote_python_path=remote_python_path,
                    path_pair_id=pair.id,
                    path_pair_name=pair.name
                ) for pair in enabled_path_pairs
            ])
        return RemoteScanner(
            remote_address=Controller.__require_runtime_path(config.lftp.remote_address, "Lftp.remote_address"),
            remote_username=Controller.__require_runtime_path(config.lftp.remote_username, "Lftp.remote_username"),
            remote_password=self.__ssh_password,
            remote_port=Controller.__require_runtime_int(config.lftp.remote_port, "Lftp.remote_port"),
            remote_path_to_scan=self.__legacy_remote_path,
            local_path_to_scan_script=Controller.__require_runtime_path(
                self.__context.args.local_path_to_scanfs, "Args.local_path_to_scanfs"
            ),
            remote_path_to_scan_script=Controller.__require_runtime_path(
                config.lftp.remote_path_to_scan_script, "Lftp.remote_path_to_scan_script"
            ),
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
            active_scanner: ActiveScannerRuntime,
            local_scanner: LocalScannerRuntime,
            remote_scanner: RemoteScannerRuntime,
            active_scan_process: ScannerProcess,
            local_scan_process: ScannerProcess,
            remote_scan_process: ScannerProcess) -> None:
        self.__set_transfer_path_pairs(self.__build_lftp_path_pairs(path_pairs_by_id, path_pair_staging_paths))
        self.__refresh_model_builder_local_paths(path_pairs_by_id, path_pair_staging_paths)
        validation_path_pairs: dict[str, object] = dict(path_pairs_by_id)
        self.__validate_process.set_path_pairs_by_id(validation_path_pairs)
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

        def stop_process(process: AppProcess) -> bool:
            return self.__teardown_process(
                "refresh process {}".format(getattr(process, "name", "?")),
                process,
            )

        def close_active_scanner(label: str, scanner: ActiveScannerRuntime) -> None:
            close = getattr(scanner, "close", None)
            if callable(close):
                self.__best_effort_teardown(label, close)

        try:
            self.__refresh_path_pair_runtime_state()
            new_state_applied = True
            if was_started:
                self.__active_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
                self.__local_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
                self.__remote_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
                refreshed_validation_pairs: dict[str, object] = dict(self.__path_pairs_by_id)
                self.__validate_process.set_path_pairs_by_id(refreshed_validation_pairs)

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
                old_active_scan_process_stopped = stop_process(old_active_scan_process)
                stop_process(old_local_scan_process)
                stop_process(old_remote_scan_process)
                if old_active_scan_process_stopped:
                    close_active_scanner("old active scanner close", old_active_scanner)
            self.__clear_path_pair_runtime_error()
        except Exception as exc:
            if new_state_applied:
                new_active_scan_process_stopped = stop_process(self.__active_scan_process)
                stop_process(self.__local_scan_process)
                stop_process(self.__remote_scan_process)
                if new_active_scan_process_stopped:
                    close_active_scanner("new active scanner close", self.__active_scanner)
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
                self.__exclude_patterns = Controller.__get_exclude_patterns(self.__context)
            except Exception:
                self.__restore_lftp_reconfigure_request()
                self.logger.exception("Ignoring lftp reconfigure failure")
        self.__updater.update()
        self.__log_memory_usage()

    def __best_effort_teardown(self, label: str, teardown: Callable[[], object]):
        try:
            teardown()
        except Exception:
            self.logger.exception(
                "Ignoring controller teardown failure during %s; continuing shutdown",
                label
            )

    __JOIN_TIMEOUT_IN_SECS = 2

    def __bounded_join(self, label: str, process: AppProcess) -> bool:
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
        return not still_alive

    def __teardown_process(self,
                           label: str,
                           process: AppProcess | None,
                           *,
                           terminate: bool = True) -> bool:
        if process is None:
            return True
        if process.pid is None:
            # start() never succeeded; there is no child to terminate or join,
            # but parent-owned queues still need deterministic cleanup.
            self.__best_effort_teardown("{} close_queues".format(label), process.close_queues)
            return True
        if terminate:
            self.__best_effort_teardown("{} terminate".format(label), process.terminate)
        process_stopped = self.__bounded_join("{} join".format(label), process)
        if process_stopped:
            self.__best_effort_teardown("{} close_queues".format(label), process.close_queues)
        return process_stopped

    def __cleanup_active_command_processes_for_exit(self) -> None:
        active_command_processes = list(getattr(self, "_Controller__active_command_processes", []))
        if not active_command_processes:
            return

        for command_process in active_command_processes:
            self.__teardown_process(
                "active command process {}".format(getattr(command_process.process, "name", "?")),
                command_process.process,
            )

        with self.__command_state_lock():
            self.__active_command_processes = []

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
                self.__cleanup_active_command_processes_for_exit()
                active_scan_process_stopped = self.__teardown_process("active scan process", self.__active_scan_process)
                self.__teardown_process("local scan process", self.__local_scan_process)
                self.__teardown_process("remote scan process", self.__remote_scan_process)
                self.__teardown_process("extract process", self.__extract_process)
                self.__teardown_process("validate process", self.__validate_process)
                if active_scan_process_stopped:
                    self.__best_effort_teardown("active scanner close", self.__active_scanner.close)
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

    def add_remote_delete_success_listener(self, listener: Callable[[ModelFile], None]):
        with self.__remote_delete_success_listeners_lock:
            if listener not in self.__remote_delete_success_listeners:
                self.__remote_delete_success_listeners.append(listener)

    def add_download_start_listener(self, listener: Callable[[ModelFile], None]):
        with self.__download_start_lock:
            if listener not in self.__download_start_listeners:
                self.__download_start_listeners.append(listener)

    def remove_download_start_listener(self, listener: Callable[[ModelFile], None]):
        with self.__download_start_lock:
            if listener in self.__download_start_listeners:
                self.__download_start_listeners.remove(listener)

    def __arm_download_start_lifecycle(
        self,
        file_id: str,
        file_name: Optional[str] = None,
        path_pair_id: Optional[str] = None,
        is_resume: bool = False,
    ) -> None:
        with self.__download_start_lock:
            entry = self.__download_start_state.get(file_id)
            if entry is not None and entry.state == "fresh_after_delete":
                self.__download_start_state[file_id] = DownloadStartLifecycleEntry(
                    "eligible", path_pair_id, datetime.now()
                )
                return
            if entry is not None or is_resume:
                return
            if file_name is not None and self.__is_previously_downloaded(file_name, path_pair_id):
                return
            self.__download_start_state[file_id] = DownloadStartLifecycleEntry(
                "eligible", path_pair_id, datetime.now()
            )

    def __suppress_download_start_lifecycle(self, file_id: str) -> None:
        with self.__download_start_lock:
            entry = self.__download_start_state.get(file_id)
            if entry is not None and entry.state == "eligible":
                self.__download_start_state[file_id] = DownloadStartLifecycleEntry(
                    "suppressed", entry.path_pair_id, datetime.now()
                )

    def _clear_download_start_lifecycle(self, file_id: str) -> None:
        with self.__download_start_lock:
            self.__download_start_state.pop(file_id, None)

    def _complete_download_start_lifecycle(self, file_id: str) -> None:
        with self.__download_start_lock:
            entry = self.__download_start_state.get(file_id)
            if entry is not None and entry.state in ("notified", "suppressed"):
                self.__download_start_state.pop(file_id, None)

    def __reset_download_start_after_local_delete(
        self, file_id: str, path_pair_id: Optional[str]
    ) -> None:
        with self.__download_start_lock:
            self.__download_start_state[file_id] = DownloadStartLifecycleEntry(
                "fresh_after_delete", path_pair_id, datetime.now()
            )

    def _snapshot_delete_command_file_ids(self) -> Set[str]:
        with self.__command_state_lock():
            protected = {
                command_process.file_id
                for command_process in self.__active_command_processes
                if self.__is_delete_command_action(command_process.command.action)
            }
            with self.__command_queue.mutex:
                protected.update(
                    self.__delete_command_identity(command)
                    for command in self.__command_queue.queue
                    if self.__is_delete_command_action(command.action)
                )
            protected.update(
                self.__delete_command_identity(command)
                for command in self.__deferred_delete_commands()
                if self.__is_delete_command_action(command.action)
            )
        return protected

    def _prune_download_start_lifecycles(
        self,
        scan_timestamp: datetime,
        scanned_path_pair_ids: Set[Optional[str]],
        remote_file_ids: Set[str],
        protected_file_ids: Set[str],
    ) -> None:
        with self.__download_start_lock:
            stale_ids = [
                file_id
                for file_id, entry in self.__download_start_state.items()
                if entry.path_pair_id in scanned_path_pair_ids
                and file_id not in remote_file_ids
                and file_id not in protected_file_ids
                and scan_timestamp >= entry.transitioned_at
            ]
            for file_id in stale_ids:
                self.__download_start_state.pop(file_id, None)

    def _confirm_fresh_healthy_download_starts(self, statuses: List[LftpJobStatus]) -> None:
        notifications: list[ModelFile] = []
        with self.__download_start_lock:
            listeners = list(self.__download_start_listeners)
            for status in statuses:
                if status.state != LftpJobStatus.State.RUNNING:
                    continue
                entry = self.__download_start_state.get(status.file_id)
                if entry is None or entry.state != "eligible":
                    continue
                try:
                    file = copy.deepcopy(self.__model.get_file(status.file_id))
                except ModelError:
                    continue
                # Commit the once-per-lifecycle decision before any listener
                # can re-enter controller or notification code.
                self.__download_start_state[status.file_id] = DownloadStartLifecycleEntry(
                    "notified", entry.path_pair_id, datetime.now()
                )
                notifications.append(file)
        for file in notifications:
            for listener in listeners:
                try:
                    listener(file)
                except Exception:
                    self.logger.warning("Download start listener failed", exc_info=True)

    def remove_remote_delete_success_listener(self, listener: Callable[[ModelFile], None]):
        with self.__remote_delete_success_listeners_lock:
            if listener in self.__remote_delete_success_listeners:
                self.__remote_delete_success_listeners.remove(listener)

    def __notify_remote_delete_success(self, file: Optional[ModelFile]):
        if file is None:
            return
        with self.__remote_delete_success_listeners_lock:
            listeners = list(self.__remote_delete_success_listeners)
        for listener in listeners:
            try:
                listener(file)
            except Exception:
                self.logger.warning("Remote delete success listener failed", exc_info=True)

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
        model_files: list[ModelFile] = []
        identifiers = self.__model.get_file_ids()
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

    def __build_extract_request(self, file: ModelFile) -> Optional[ExtractRequest]:
        path_pair = self.__get_path_pair(file.path_pair_id)
        if file.path_pair_id is not None and path_pair is None:
            return None

        controller_cfg = self.__context.config.controller
        staging_path = self.__get_staging_path(file.path_pair_id if path_pair is not None else None)
        if staging_path is None:
            return None

        final_local_path = path_pair.local_path if path_pair is not None else self.__legacy_local_path
        extract_out_dir = final_local_path if controller_cfg.use_local_path_as_extract_path else getattr(
            controller_cfg,
            "extract_path",
            final_local_path,
        )
        extract_out_dir = Controller.__require_runtime_path(extract_out_dir, "Controller.extract_path")
        local_path_fallback = final_local_path if os.path.normcase(os.path.abspath(final_local_path)) != os.path.normcase(os.path.abspath(staging_path)) else None
        out_dir_path_fallback = extract_out_dir if os.path.normcase(os.path.abspath(extract_out_dir)) != os.path.normcase(os.path.abspath(staging_path)) else None
        return ExtractRequest(
            model_file=file,
            local_path=staging_path,
            out_dir_path=staging_path,
            pair_id=file.path_pair_id,
            local_path_fallback=local_path_fallback,
            out_dir_path_fallback=out_dir_path_fallback,
        )

    def __get_stop_resume_trace_file_details(
        self, path: Optional[str], include_allocated_size: bool = False
    ) -> dict[str, object]:
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

        details: dict[str, object] = {
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
                                current_state: Optional[object] = None,
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
        if isinstance(parsed_identifier, list):
            parsed_items = cast(list[object], parsed_identifier)
            if len(parsed_items) == 2 and isinstance(parsed_items[1], str):
                return parsed_items[1]
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
    def __summarize_target_archive_file(file: ModelFile) -> dict[str, object]:
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

    def __find_target_archive_model_file(
        self, file_name: str, file_id: Optional[str] = None
    ) -> Optional[ModelFile]:
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

    def __trace_target_archive_event(self, event: str, payload: dict[str, object]) -> None:
        if not self.__is_target_archive_trace_enabled():
            return
        trace_payload: dict[str, object] = {
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
                            details: Optional[dict[str, object]] = None,
                            event_type: str = "diagnostic",
                            file_id: Optional[str] = None,
                            path_pair_id: Optional[str] = None,
                            path_pair_name: Optional[str] = None,
                            corr_id: Optional[str] = None,
                            flow_id: Optional[str] = None,
                            trace_scope: str = "flow") -> None:
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
                                    details: dict[str, object],
                                    event_type: str = "state_transition",
                                    file: Optional[ModelFile] = None) -> None:
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

    def __trace_corr_id_from_files(self, files: Optional[Sequence[object]], fallback: str) -> str:
        if files is not None:
            for file in files:
                path_pair_id = getattr(file, "path_pair_id", None)
                if path_pair_id is not None:
                    return path_pair_id
                file_id = getattr(file, "file_id", None)
                if file_id is not None:
                    return file_id
        return fallback

    def __extract_status_matches_failed_result(
        self, status: ExtractStatus, failed_results: list[ExtractFailedResult]
    ) -> bool:
        status_file_id = getattr(status, "file_id", None)
        status_path_pair_id = getattr(status, "path_pair_id", None)
        for result in failed_results or []:
            result_file_id = getattr(result, "file_id", None)
            if status_file_id is not None and result_file_id is not None:
                if status_file_id == result_file_id:
                    return True
                continue

            result_path_pair_id = getattr(result, "path_pair_id", None)
            if status_path_pair_id is not None and result_path_pair_id is not None:
                if status_path_pair_id == result_path_pair_id and status.name == result.name:
                    return True
                continue

            if status_path_pair_id is None and result_path_pair_id is None and status.name == result.name:
                return True
        return False

    def __active_extracting_file_tuple(
        self, status: ExtractStatus
    ) -> tuple[str, Optional[str], Optional[str]]:
        path_pair_id = getattr(status, "path_pair_id", None)
        path_pair_name = getattr(status, "path_pair_name", None)
        if path_pair_name is None:
            path_pair = self.__get_path_pair(path_pair_id)
            path_pair_name = getattr(path_pair, "name", None)
        return status.name, path_pair_id, path_pair_name

    def __temp_diag(self, stage: str, file_id: Optional[str] = None, **payload: object) -> None:
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

    def clear_extracted_marker(self, file: ModelFile) -> None:
        stale_extracted_file_names: set[str] = set()
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

    @staticmethod
    def __safe_final_move_candidate(root: str, name: str) -> Optional[str]:
        normalized_name = name.replace("\\", "/")
        if not normalized_name or os.path.isabs(name) or ntpath.isabs(name):
            return None
        parts = normalized_name.split("/")
        if any(part in ("", ".", "..") for part in parts):
            return None
        try:
            canonical_root = os.path.realpath(root)
            candidate = os.path.normpath(os.path.join(root, *parts))
            resolved_candidate = os.path.realpath(candidate)
            if os.path.normcase(os.path.commonpath([canonical_root, resolved_candidate])) != os.path.normcase(canonical_root):
                return None
            current = os.path.normpath(root)
            for part in parts:
                current = os.path.join(current, part)
                try:
                    if stat.S_ISLNK(os.lstat(current).st_mode):
                        return None
                except FileNotFoundError:
                    continue
            return candidate
        except (OSError, ValueError):
            return None

    def __resolve_safe_final_move_paths(
            self, name: str, path_pair_id: Optional[str] = None) -> Optional[Tuple[str, str, str, str]]:
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
            return None

        src = Controller.__safe_final_move_candidate(staging_path, name)
        dst = Controller.__safe_final_move_candidate(final_path, name)
        if src is None or dst is None:
            self.logger.warning("Rejected unsafe final move path")
            return None
        return staging_path, final_path, src, dst

    def __move_from_staging(self, name: str, path_pair_id: Optional[str] = None) -> MoveFromStagingResult:
        resolved = self.__resolve_safe_final_move_paths(name, path_pair_id)
        if resolved is None:
            return Controller.MoveFromStagingResult.FAILED
        staging_path, final_path, src, dst = resolved

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
            return Controller.MoveFromStagingResult.ALREADY_COMPLETED if destination_exists \
                else Controller.MoveFromStagingResult.FAILED
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "same_path",
                })
            return Controller.MoveFromStagingResult.NO_MOVE_APPLICABLE
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
            return Controller.MoveFromStagingResult.DEFERRED

        try:
            # Re-resolve immediately before the mutation to narrow the window
            # for a path component to be replaced with a symlink.
            current = self.__resolve_safe_final_move_paths(name, path_pair_id)
            if current is None or current[2:] != (src, dst):
                return Controller.MoveFromStagingResult.FAILED
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
            return Controller.MoveFromStagingResult.COMPLETED
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
            return Controller.MoveFromStagingResult.FAILED

    def _reserve_move_attempt(self, file_id: str) -> bool:
        # Lock order is model_lock -> move_attempt_lock. This helper never
        # acquires model_lock, so callers must not invert that ordering.
        with self.__move_attempt_lock:
            if file_id in self.__move_attempt_reservations:
                return False
            self.__move_attempt_reservations.add(file_id)
            return True

    def _release_move_attempt(self, file_id: str) -> None:
        with self.__move_attempt_lock:
            self.__move_attempt_reservations.discard(file_id)

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
        if not isinstance(commands, list):
            commands = []
            self.__deferred_delete_command_refs = commands
        command_items = cast(list[object], commands)
        if not all(isinstance(command, Controller.Command) for command in command_items):
            self.__deferred_delete_command_refs = []
            return self.__deferred_delete_command_refs
        return cast(list[Controller.Command], command_items)

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

    def _has_active_command_for_file(self, file_id: str, action: Optional["Controller.Command.Action"] = None) -> bool:
        with self.__command_state_lock():
            return self.__has_active_command_for_file_unlocked(file_id, action)

    __has_active_command_for_file = _has_active_command_for_file

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
        post_callback: Callable[[], None],
        command: Optional["Controller.Command"] = None
    ) -> None:
        delete_local_path, delete_local_name = self.__get_delete_local_target(file)
        process = DeleteLocalProcess(
            local_path=delete_local_path,
            file_name=delete_local_name
        )
        process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
        command_wrapper = Controller.CommandProcessWrapper(
            command=command or Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id),
            file_id=file.file_id,
            file_name=file.name,
            process=process,
            post_callback=post_callback,
            await_completion=True,
            event_file=copy.deepcopy(file),
        )
        with self.__command_state_lock():
            self.__active_command_processes.append(command_wrapper)
        command_wrapper.process.start()

    def __recover_interrupted_downloads(self, remote_files: list[SystemFile]) -> None:
        self.__startup_recovery_done = True
        suffix = Constants.LFTP_TEMP_FILE_SUFFIX
        remote_names_by_pair: dict[Optional[str], set[str]] = {}
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
                    queue_kwargs: dict[str, str] = {}
                    exclude_patterns = Controller.__get_exclude_patterns(self)
                    if exclude_patterns.strip():
                        queue_kwargs["exclude_patterns"] = exclude_patterns
                    self.__lftp.queue(
                        file_name,
                        is_dir,
                        remote_base_dir_path=path_pair.remote_path if path_pair is not None else None,
                        local_base_dir_path=staging_path,
                        **queue_kwargs
                    )
                    self.logger.info("Recovered interrupted download '%s' from '%s'", file_name, staging_path)
                except (LftpError, RcloneTransferError) as error:
                    self.logger.warning(
                        "Failed to recover interrupted download '%s' from '%s': %s",
                        file_name,
                        staging_path,
                        error
                    )

    def __queue_dispatch_pending(self) -> Dict[str, PendingQueueDispatch]:
        pending = getattr(self, "_Controller__pending_queue_dispatches", None)
        if not isinstance(pending, dict):
            pending = {}
            self.__pending_queue_dispatches = pending
        pending_items = cast(dict[object, object], pending)
        if not all(
            isinstance(file_id, str) and isinstance(dispatch, PendingQueueDispatch)
            for file_id, dispatch in pending_items.items()
        ):
            self.__pending_queue_dispatches = {}
            return self.__pending_queue_dispatches
        return cast(dict[str, PendingQueueDispatch], pending_items)

    def __reconcile_queue_dispatch_pending(self) -> None:
        pending = self.__queue_dispatch_pending()
        for file_id in list(pending):
            try:
                self.__model.get_file(file_id)
            except ModelError:
                del pending[file_id]
                continue

    def _reconcile_pending_queue_dispatches_from_fresh_status(self, active_file_ids: Set[str]) -> None:
        pending = self.__queue_dispatch_pending()
        for file_id in list(pending):
            if file_id in active_file_ids:
                # A fresh healthy transport snapshot has made the accepted
                # lifecycle authoritative. The model's active-state guard now
                # owns duplicate suppression.
                del pending[file_id]

    def __set_active_scanner_files(
        self, active_files: list[tuple[str, Optional[str], Optional[str]]]
    ) -> None:
        if isinstance(self.__active_scanner, MultiPathActiveScanner):
            self.__active_scanner.set_active_files(active_files)
        else:
            self.__active_scanner.set_active_files([name for name, _, _ in active_files])

    def _update_model_compat(self) -> None:
        updater = getattr(self, "_Controller__updater", None)
        if not isinstance(updater, ModelUpdater):
            updater = ModelUpdater(self)
            self.__updater = updater
        updater.update()

    __update_model = _update_model_compat

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

        deferred_commands: list[Controller.Command] = []
        # Queue commands are drained before the next model refresh. Retain
        # accepted identities until their authoritative lifecycle is observed
        # (or bounded reconciliation expires an unobserved acknowledgement).
        self.__reconcile_queue_dispatch_pending()
        pending_queue_dispatches = self.__queue_dispatch_pending()
        stopped_queue_lifecycle_ids: set[str] = set()
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
                # Re-resolve immediately before any transport side effect. A
                # model refresh may have advanced this exact file since the
                # command was dequeued (for example, to QUEUED or
                # DOWNLOADING), and identity must remain file-id/path-pair
                # aware rather than falling back to a name match.
                try:
                    file = self.__model.get_file(command.filename)
                except ModelError:
                    _notify_failure(command, "File '{}' not found".format(command.filename), 404)
                    continue

                already_active = file.state in (
                    ModelFile.State.QUEUED,
                    ModelFile.State.DOWNLOADING,
                )
                stopped_marked = self.__is_explicitly_stopped(file.name, file.path_pair_id)
                stop_boundary = file.file_id in stopped_queue_lifecycle_ids or stopped_marked
                if stop_boundary:
                    pending_queue_dispatches.pop(file.file_id, None)
                pending_dispatch = pending_queue_dispatches.get(file.file_id)
                pending_timeout = max(
                    1, getattr(self, "_Controller__lftp_status_cache_max_age_seconds", 3)
                )
                retry_after_ambiguity = pending_dispatch is not None and (
                    time.monotonic() - pending_dispatch.accepted_at_monotonic >= pending_timeout
                )
                already_dispatched = pending_dispatch is not None and not retry_after_ambiguity
                if (already_active and not stop_boundary and not retry_after_ambiguity) or already_dispatched:
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
                            "mode": "idempotent_noop",
                            "already_active": already_active,
                            "already_dispatched": already_dispatched,
                            "stopped_marked": stopped_marked,
                        },
                        file=file,
                    )
                elif file.remote_size is None:
                    _notify_failure(command, "File '{}' does not exist remotely".format(command.filename), 404, file)
                    continue
                else:
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
                        queue_kwargs: dict[str, str] = {}
                        exclude_patterns = Controller.__get_exclude_patterns(self)
                        if exclude_patterns.strip():
                            queue_kwargs["exclude_patterns"] = exclude_patterns
                        self.__lftp.queue(
                            file.name,
                            file.is_dir,
                            remote_base_dir_path=path_pair.remote_path if path_pair else None,
                            local_base_dir_path=local_base_dir_path,
                            **queue_kwargs
                        )
                        pending_queue_dispatches[file.file_id] = PendingQueueDispatch(time.monotonic())
                        # If the prior acknowledgement was never observable,
                        # this successful explicit retry resets the bounded
                        # ambiguity window. Beyond that window Queue is
                        # intentionally at-least-once: LFTP acknowledgement is
                        # not transactional with controller model observation.
                        # Ensure this acceptance is reconciled against a fresh
                        # transport snapshot in the updater that follows this
                        # command drain.
                        self.__next_lftp_status_poll_at = None
                        is_new_transfer_lifecycle = stop_boundary or file.state not in (
                            ModelFile.State.QUEUED,
                            ModelFile.State.DOWNLOADING,
                        )
                        stopped_queue_lifecycle_ids.discard(file.file_id)
                        if is_new_transfer_lifecycle:
                            self.__persist.move_failure_counts.pop(file.file_id, None)
                            self.__move_retry_due.pop(file.file_id, None)
                            self.__deferred_move_file_ids.discard(file.file_id)
                            self.__pending_completion_file_names = {
                                entry for entry in self.__pending_completion_file_names
                                if ModelFile.build_file_id(entry[0], entry[1]) != file.file_id
                            }
                            with self.__move_attempt_lock:
                                self.__move_attempt_reservations.discard(file.file_id)
                            self.__model_builder.set_move_failed_files({
                                file_id for file_id, count in self.__persist.move_failure_counts.items()
                                if count >= Controller.__MAX_MOVE_FAILURES
                            })
                            self.__arm_download_start_lifecycle(
                                file.file_id,
                                file.name,
                                file.path_pair_id,
                                is_resume=stopped_marked,
                            )
                        Controller.__clear_persist_key(
                            self.__persist.stopped_file_names,
                            file.name,
                            file.path_pair_id
                        )
                        if is_new_transfer_lifecycle:
                            # A genuinely new queue invalidates all final-move
                            # identity from the prior transfer lifecycle.
                            self.__persist.final_move_succeeded_file_names.discard(file.file_id)
                            self.__model_builder.set_final_move_succeeded_files(
                                self.__persist.final_move_succeeded_file_names
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
                    except (LftpError, RcloneTransferError) as e:
                        _notify_failure(command, "Transfer backend error: {}".format(str(e)), 500, file)
                        continue

            elif command.action == Controller.Command.Action.STOP:
                pending_stop = file.file_id in pending_queue_dispatches
                if not pending_stop and file.state not in (
                    ModelFile.State.DOWNLOADING,
                    ModelFile.State.QUEUED,
                ):
                    _notify_failure(
                        command,
                        "File '{}' is not Queued or Downloading".format(command.filename),
                        409,
                        file
                    )
                    continue
                if not pending_stop and not file.is_stoppable:
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
                    pending_queue_dispatches.pop(file.file_id, None)
                    stopped_queue_lifecycle_ids.add(file.file_id)
                    self.__suppress_download_start_lifecycle(file.file_id)
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
                except (LftpError, LftpJobStatusParserError, RcloneTransferError) as e:
                    _notify_failure(command, "Transfer backend error: {}".format(str(e)), 500, file)
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
                        extract_request = self.__build_extract_request(file)
                        if extract_request is None:
                            _notify_failure(
                                command,
                                "Path pair '{}' is unavailable for extraction".format(file.path_pair_id),
                                404,
                                file
                            )
                            continue
                        self.__extract_process.extract(extract_request, flow_id=command.flow_id)
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
                    ModelFile.State.EXTRACTED,
                    ModelFile.State.MOVE_FAILED,
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

            elif command.action == Controller.Command.Action.RETRY_MOVE:
                if file.state != ModelFile.State.MOVE_FAILED or \
                        self.__persist.move_failure_counts.get(file.file_id, 0) < Controller.__MAX_MOVE_FAILURES:
                    _notify_failure(command, "Final move is not failed for this file", 409, file)
                    continue
                if not self._reserve_move_attempt(file.file_id):
                    _notify_failure(command, "Move retry is already active", 409, file)
                    continue
                try:
                    result = self.__move_from_staging(file.name, file.path_pair_id)
                    if result in (
                        Controller.MoveFromStagingResult.COMPLETED,
                        Controller.MoveFromStagingResult.ALREADY_COMPLETED,
                    ):
                        self.__persist.move_failure_counts.pop(file.file_id, None)
                        self.__deferred_move_file_ids.discard(file.file_id)
                        self.__move_retry_due.pop(file.file_id, None)
                        self.__persist.downloaded_file_names.add(file.file_id)
                        if result == Controller.MoveFromStagingResult.COMPLETED:
                            self.__persist.final_move_succeeded_file_names.add(file.file_id)
                        self._complete_download_start_lifecycle(file.file_id)
                        self.clear_extracted_marker(file)
                        self.__pending_completion_file_names = {
                            entry for entry in self.__pending_completion_file_names
                            if ModelFile.build_file_id(entry[0], entry[1]) != file.file_id
                        }
                        self.__model_builder.set_downloaded_files(self.__persist.downloaded_file_names)
                        self.__model_builder.set_final_move_succeeded_files(
                            self.__persist.final_move_succeeded_file_names
                        )
                        self.__model_builder.set_move_failed_files({
                            file_id for file_id, count in self.__persist.move_failure_counts.items()
                            if count >= Controller.__MAX_MOVE_FAILURES
                        })
                        if file.path_pair_id is None:
                            self.__local_scan_process.force_scan()
                        else:
                            self.__local_scan_process.force_scan(file.path_pair_id)
                    elif result == Controller.MoveFromStagingResult.DEFERRED:
                        _notify_failure(command, "Move retry is temporarily unavailable", 409, file)
                        continue
                    else:
                        _notify_failure(command, "Final move failed", 500, file)
                        continue
                finally:
                    self._release_move_attempt(file.file_id)

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
                    config = self.__context.config
                    process = DeleteRemoteProcess(
                        remote_address=Controller.__runtime_str_or_default(
                            config.lftp.remote_address, ""
                        ),
                        remote_username=Controller.__runtime_str_or_default(
                            config.lftp.remote_username, ""
                        ),
                        remote_password=self.__ssh_password,
                        remote_port=Controller.__runtime_int_or_default(config.lftp.remote_port, 22),
                        remote_path=path_pair.remote_path if (path_pair := self.__get_path_pair(file.path_pair_id))
                        else self.__legacy_remote_path,
                        file_name=file.name
                    )
                    process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
                    post_callback = self.__remote_scan_process.force_scan
                    command_wrapper = Controller.CommandProcessWrapper(
                        command=command,
                        file_id=file.file_id,
                        file_name=file.name,
                        process=process,
                        post_callback=post_callback,
                        await_completion=False,
                        event_file=copy.deepcopy(file),
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
            model_file_count = len(self.__model.get_file_ids())

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
        except (LftpError, RcloneTransferError) as e:
            self.logger.warning("Caught transfer backend error: {}".format(str(e)))
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
        still_active_processes: list[Controller.CommandProcessWrapper] = []
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
                        self.__teardown_process("stale delete command process", command_process.process, terminate=False)
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
                            if command_process.command.action == Controller.Command.Action.DELETE_LOCAL:
                                self.__persist.move_failure_counts.pop(command_process.file_id, None)
                                self.__deferred_move_file_ids.discard(command_process.file_id)
                                self.__move_retry_due.pop(command_process.file_id, None)
                                self.__persist.final_move_succeeded_file_names.discard(command_process.file_id)
                                self.__model_builder.set_final_move_succeeded_files(
                                    self.__persist.final_move_succeeded_file_names
                                )
                                self.__model_builder.set_move_failed_files({
                                    file_id for file_id, count in self.__persist.move_failure_counts.items()
                                    if count >= Controller.__MAX_MOVE_FAILURES
                                })
                                self.__reset_download_start_after_local_delete(
                                    command_process.file_id,
                                    getattr(command_process.event_file, "path_pair_id", None),
                                )
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
                            if command_process.command.action == Controller.Command.Action.DELETE_REMOTE:
                                self._clear_download_start_lifecycle(command_process.file_id)
                                event_file = command_process.event_file
                                if event_file is not None:
                                    Controller.__clear_persist_key(
                                        self.__persist.downloaded_file_names,
                                        event_file.name,
                                        event_file.path_pair_id,
                                    )
                                    self.__model_builder.set_downloaded_files(
                                        self.__persist.downloaded_file_names
                                    )
                                    # Remote deletion clears the existing
                                    # completion identity, matching downloaded.
                                    self.__persist.final_move_succeeded_file_names.discard(
                                        command_process.file_id
                                    )
                                    self.__model_builder.set_final_move_succeeded_files(
                                        self.__persist.final_move_succeeded_file_names
                                    )
                                self.__notify_remote_delete_success(command_process.event_file)
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
                    self.__teardown_process("completed command process", command_process.process, terminate=False)
        with self.__command_state_lock():
            self.__active_command_processes = still_active_processes

    _Controller__active_extracting_file_tuple = __active_extracting_file_tuple
    _Controller__extract_status_matches_failed_result = __extract_status_matches_failed_result
    _Controller__find_target_archive_model_file = __find_target_archive_model_file
    _Controller__get_path_pair = __get_path_pair
    _Controller__is_explicitly_stopped = __is_explicitly_stopped
    _Controller__is_target_archive_trace_enabled = __is_target_archive_trace_enabled
    _Controller__move_from_staging = __move_from_staging
    _Controller__queue_delete_local_process = __queue_delete_local_process
    _Controller__record_breadcrumb = __record_breadcrumb
    _Controller__recover_interrupted_downloads = __recover_interrupted_downloads
    _Controller__set_active_scanner_files = __set_active_scanner_files
    _Controller__should_auto_purge_local_file = __should_auto_purge_local_file
    _Controller__summarize_target_archive_file = __summarize_target_archive_file
    _Controller__target_archive_trace_selector_matches_file = __target_archive_trace_selector_matches_file
    _Controller__temp_diag = __temp_diag
    _Controller__trace_corr_id_from_files = __trace_corr_id_from_files
    _Controller__trace_target_archive_event = __trace_target_archive_event
