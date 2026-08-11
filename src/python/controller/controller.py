# Copyright 2017, Inderpreet Singh, All rights reserved.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple, cast
from threading import Event, Lock, RLock
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from queue import Queue
from enum import Enum
from datetime import datetime
import copy
import hashlib
import json
import heapq
import os
import ntpath
import stat
import time
import shutil
import errno
import tempfile
import secrets
import re
import sys
from dataclasses import dataclass

# my libs
from .scan import (
    ScannerProcess,
    ActiveScanner,
    LocalScanner,
    RemoteScanner,
    RemoteScanLease,
    MultiPathActiveScanner,
    MultiPathLocalScanner,
    MultiPathRemoteScanner,
)
from .extract import ExtractFailedResult, ExtractProcess, ExtractRequest, ExtractStatus
from .validate import ValidateProcess
from .model_updater import ModelUpdater
from .model_builder import ModelBuilder
from .memory_monitor import ControllerMemoryMonitor
from .ownership_census import (
    OwnershipRoot, build_ownership_census, disabled_ownership_census,
    release_ownership_census_working_memory, snapshot_container,
    unavailable_ownership_census,
)
from common import (
    AppError, AppOneShotProcess, AppProcess, Args, Config, Constants, Context,
    Localization, MultiprocessingLogger, PathPair, PathPairManager, PathPairError,
)
from common.performance_diagnostics import DURATION_CONTROLLER_PROCESS, DURATION_MODEL_UPDATE
from common.exclude_patterns import ExactPathExclusion, parse_exclude_patterns
from model import ModelError, ModelFile, Model, IModelListener
from lftp import Lftp, LftpError, LftpJobStatus, LftpJobStatusParserError
from transfer import RcloneTransferBackend, create_transfer_backend, RcloneTransferError
from .controller_persist import ControllerPersist
from .delete import DeleteLocalProcess, DeleteRemoteProcess
from system import SystemFile

ActiveScannerRuntime = ActiveScanner | MultiPathActiveScanner
LocalScannerRuntime = LocalScanner | MultiPathLocalScanner
RemoteScannerRuntime = RemoteScanner | MultiPathRemoteScanner

# A single-path configuration still has no persisted path-pair id.  The web
# model API exposes it through this explicit synthetic scope without changing
# command/file identities (which remain the legacy unscoped file ids).
MODEL_LEGACY_SCOPE_ID = "__legacy__"


class _PathPairTransferBackend(Protocol):
    def set_path_pairs(self, path_pairs: list[PathPair]) -> None: ...


class ControllerError(AppError):
    """
    Exception indicating a controller error
    """
    pass


class ModelPageCursorError(ControllerError):
    """The client supplied an invalid page cursor for a scoped model page."""
    pass


class _ReverseModelSortKey:
    """heapq adapter that keeps the greatest selected key at heap[0]."""
    def __init__(self, key: tuple[object, ...]):
        self.key = key

    def __lt__(self, other: "_ReverseModelSortKey") -> bool:
        return self.key > other.key


@dataclass(frozen=True)
class DownloadStartLifecycleEntry:
    state: str
    path_pair_id: Optional[str]
    transitioned_at: datetime


@dataclass
class PendingQueueDispatch:
    accepted_at_monotonic: float


_COLLISION_COMPARE_MAX_BYTES = 16 * 1024 * 1024 * 1024
_COLLISION_COMPARE_CHUNK_BYTES = 128 * 1024
_COLLISION_CLAIM_SIDECAR_MAX_BYTES = 4096
_COLLISION_CLAIM_SCAN_LIMIT = 128


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
        CONFLICT = 5

    __MAX_MOVE_FAILURES = 4
    __MOVE_RETRY_DELAYS = (2, 10, 30)
    __AUXILIARY_WORKER_IDLE_GRACE_IN_SECS = 2.0

    __context: Context
    __persist: ControllerPersist
    __command_queue: Queue[Controller.Command]
    __model: Model
    __model_builder: ModelBuilder
    __updater: ModelUpdater
    __path_pairs_by_id: dict[str, PathPair]
    __path_pair_staging_paths: dict[str, str]
    __explicit_staging_path_configured: bool
    __active_scanner: ActiveScannerRuntime
    __local_scanner: LocalScannerRuntime
    __remote_scanner: RemoteScannerRuntime
    __active_scan_process: ScannerProcess
    __local_scan_process: ScannerProcess
    __remote_scan_process: ScannerProcess
    __remote_scan_lease: Optional[RemoteScanLease]
    __extract_process: ExtractProcess
    __validate_process: ValidateProcess
    __lftp: Lftp | RcloneTransferBackend
    __mp_logger: MultiprocessingLogger
    __active_downloading_file_names: list[tuple[str, Optional[str], Optional[str]]]
    __active_extracting_file_names: list[tuple[str, Optional[str], Optional[str]]]
    __prev_downloading_file_names: set[tuple[str, Optional[str], Optional[str]]]
    __pending_completion_file_names: set[tuple[str, Optional[str], Optional[str]]]
    __pending_completion_progress_floors: dict[str, tuple[Optional[int], Optional[int]]]
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
    __extract_idle_deadline_monotonic: Optional[float]
    __validate_idle_deadline_monotonic: Optional[float]
    __remote_delete_success_listeners: list[Callable[[ModelFile], None]]
    __download_start_listeners: list[Callable[[ModelFile], None]]
    __download_start_state: dict[str, DownloadStartLifecycleEntry]
    __deferred_delete_command_refs: list[Controller.Command]
    __startup_validation_error: Optional[str]
    __path_pair_runtime_error: Optional[str]
    __stop_resume_trace_cycle_id: int
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
    _Controller__pending_completion_progress_floors: dict[str, tuple[Optional[int], Optional[int]]]
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
            self.lifecycle_token: Optional[tuple[int, int]] = None

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

    def __transfer_exclude_patterns(
            self, file_id: str, is_dir: bool
    ) -> str | list[str | ExactPathExclusion]:
        configured_patterns = Controller.__get_exclude_patterns(self)
        patterns = parse_exclude_patterns(configured_patterns)
        if not is_dir:
            return configured_patterns
        trusted_patterns: list[ExactPathExclusion] = []
        for relative_path in self.__model_builder.get_trusted_final_leaf_paths(file_id):
            try:
                exact_path = ExactPathExclusion(relative_path)
            except (TypeError, ValueError):
                # A Linux filename can contain control characters which neither
                # transport can safely encode in its command grammar.  Keep
                # user globs and redownload that uncertain leaf instead.
                continue
            if exact_path not in trusted_patterns:
                trusted_patterns.append(exact_path)
        if not trusted_patterns:
            return configured_patterns
        return [*patterns, *trusted_patterns]

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
        self.__explicit_staging_path_configured = False
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
            "__remote_scan_lease",
            "__extract_process",
            "__validate_process",
            "__mp_logger",
        )
        for attribute_name in failed_startup_attributes:
            object.__setattr__(self, "_Controller{}".format(attribute_name), None)
        self.__active_downloading_file_names = []
        self.__active_extracting_file_names = []
        self.__active_scan_force_file_ids = set()
        self.__active_scan_ready_file_ids = set()
        self.__next_active_scan_force_at = None
        self.__prev_downloading_file_names = set()
        self.__pending_completion_file_names = set()
        self.__pending_completion_progress_floors = {}
        self.__collision_compare_lock = Lock()
        self.__collision_compare_executor = None
        self.__collision_compare_future = None
        self.__collision_compare_key = None
        self.__collision_compare_result = None
        self.__collision_compare_claim = None
        self.__collision_compare_cancel_event = None
        self.__collision_compare_epoch = 0
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
        # A dequeued QUEUE command is briefly tracked here while its transport
        # handoff is being decided.  This closes the gap between checking a
        # relocation reservation and registering the durable queue dispatch.
        self.__pending_command_dispatch_file_ids: set[str] = set()
        self.__pending_extract_file_ids: set[str] = set()
        self.__pending_validation_file_ids: set[str] = set()
        self.__work_state_lock = RLock()
        self.__path_pair_relocation_reservations: set[str] = set()
        self.__path_pair_relocation_identities: Dict[str, tuple[str, str, tuple[int, int]]] = {}

        # The model
        self.__model = Model()
        self.__model.set_base_logger(self.logger)
        # Lock for the model. Listeners may re-enter controller model access
        # while the model updater is mutating the model, so this must be reentrant.
        self.__model_lock = RLock()
        self.__model_summary_cache: Optional[dict[str, object]] = None
        self.__model_summary_cache_at = 0.0
        self.__remote_delete_success_listeners = []
        self.__remote_delete_success_listeners_lock = Lock()
        self.__download_start_listeners = []
        self.__download_start_state = {}
        self.__download_start_lock = Lock()
        self.__path_pair_refresh_lock = Lock()
        self.__path_pair_refresh_requested = False
        self.__path_pair_refresh_generation = 0
        self.__path_pair_refresh_completed_generation = 0
        self.__reconciled_local_path_pair_ids: set[str | None] = set()
        self.__reconciled_remote_path_pair_ids: set[str | None] = set()
        self.__path_pair_runtime_error = None
        self.__lftp_reconfigure_lock = Lock()
        self.__lftp_reconfigure_requested = False

        # Model builder
        self.__model_builder = ModelBuilder()
        self.__model_builder.set_base_logger(self.logger)
        # Keep one shared emitter wired at all times; its process-safe gate
        # makes disabled tracing effectively free and enables hot settings
        # changes without restarting workers or selecting a file.
        self.__model_builder.set_stop_resume_trace_breadcrumb(
            self.__context.breadcrumb_trace.create_emitter()
        )
        self.__stop_resume_trace_cycle_id = 0

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

        config_file_path = getattr(config, "file_path", None)
        lock_directory = os.path.dirname(config_file_path) if isinstance(config_file_path, str) else None
        self.__remote_scan_lease = RemoteScanLease.create(lock_directory)

        # Preserve the configured legacy roots independently from the runtime
        # fallback. The latter follows the first enabled pair and must return
        # to these configured values when no pairs remain enabled.
        self.__configured_legacy_local_path = lftp_cfg.local_path
        self.__configured_legacy_remote_path = lftp_cfg.remote_path
        enabled_path_pairs = self.__get_enabled_path_pairs()
        self.__legacy_local_path, self.__legacy_remote_path = \
            self.__resolve_runtime_fallback_paths(enabled_path_pairs)

        self.__explicit_staging_path_configured = bool(
            isinstance(lftp_cfg.staging_path, str) and lftp_cfg.staging_path.strip()
        )
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

        # Auxiliary workers remain unstarted until their first real command.
        self.__extract_process = self.__build_extract_process()
        self.__validate_process = self.__build_validate_process()

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
        self.__active_scan_force_file_ids = set()
        self.__active_scan_ready_file_ids = set()
        self.__next_active_scan_force_at = None
        # Path-pair aware completion tracking so a finished download stays
        # visible until the model reaches a terminal state.
        self.__prev_downloading_file_names = set()
        self.__pending_completion_file_names = set()
        self.__pending_completion_progress_floors = {}
        self.__shutdown_collision_compare_worker()
        self.__collision_compare_epoch = getattr(self, "_Controller__collision_compare_epoch", 0) + 1
        self.__collision_compare_lock = Lock()
        self.__collision_compare_executor = None
        self.__collision_compare_future = None
        self.__collision_compare_key = None
        self.__collision_compare_result = None
        self.__collision_compare_claim = None
        self.__collision_compare_cancel_event = None
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
        self.__extract_idle_deadline_monotonic = None
        self.__validate_idle_deadline_monotonic = None
        self.__memory_monitor = ControllerMemoryMonitor(self.logger.getChild("MemoryMonitor"))
        self.__updater = ModelUpdater(self)
        self.__updater.sync_persist_to_all_builders()

        self.__started = False
        self.__startup_failed = False

    def __build_extract_process(self) -> ExtractProcess:
        controller_cfg = self.__context.config.controller
        out_dir_path = self.__legacy_local_path if controller_cfg.use_local_path_as_extract_path else \
            Controller.__require_runtime_path(controller_cfg.extract_path, "Controller.extract_path")
        return ExtractProcess(
            out_dir_path=out_dir_path,
            local_path=self.__legacy_local_path,
            local_path_fallback=self.__staging_path,
            managed_extract_folders_enabled=controller_cfg.managed_extract_folders_enabled,
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter(),
        )

    def __build_validate_process(self) -> ValidateProcess:
        lftp_cfg = self.__context.config.lftp
        return ValidateProcess(
            remote_address=Controller.__require_runtime_path(lftp_cfg.remote_address, "Lftp.remote_address"),
            remote_username=Controller.__require_runtime_path(lftp_cfg.remote_username, "Lftp.remote_username"),
            remote_password=self.__ssh_password,
            remote_port=Controller.__require_runtime_int(lftp_cfg.remote_port, "Lftp.remote_port"),
            local_path=self.__legacy_local_path,
            remote_path=self.__legacy_remote_path,
            path_pairs_by_id=dict(self.__path_pairs_by_id),
        )

    def __configure_auxiliary_worker_logging(self, worker: AppProcess) -> None:
        worker.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)

    def __ensure_extract_worker_started(self) -> None:
        if self.__extract_process.pid is not None:
            return
        self.__configure_auxiliary_worker_logging(self.__extract_process)
        self.__extract_process.start()
        self.__extract_idle_deadline_monotonic = None

    def __ensure_validate_worker_started(self) -> None:
        if self.__validate_process.pid is not None:
            return
        self.__configure_auxiliary_worker_logging(self.__validate_process)
        self.__validate_process.start()
        self.__validate_idle_deadline_monotonic = None

    def __replace_extract_process(self) -> None:
        self.__extract_process = self.__build_extract_process()
        self.__configure_auxiliary_worker_logging(self.__extract_process)

    def __replace_validate_process(self) -> None:
        self.__validate_process = self.__build_validate_process()
        self.__configure_auxiliary_worker_logging(self.__validate_process)

    def __reap_idle_auxiliary_workers(self) -> None:
        now = time.monotonic()
        with self.__work_state_lock:
            extract_pending = bool(self.__pending_extract_file_ids)
            validate_pending = bool(self.__pending_validation_file_ids)

        if extract_pending or self.__extract_process.pid is None:
            self.__extract_idle_deadline_monotonic = None
        elif self.__extract_idle_deadline_monotonic is None:
            self.__extract_idle_deadline_monotonic = now + self.__AUXILIARY_WORKER_IDLE_GRACE_IN_SECS
        elif now >= self.__extract_idle_deadline_monotonic:
            if self.__teardown_process("idle extract process", self.__extract_process):
                self.__replace_extract_process()
            self.__extract_idle_deadline_monotonic = None

        if validate_pending or self.__validate_process.pid is None:
            self.__validate_idle_deadline_monotonic = None
        elif self.__validate_idle_deadline_monotonic is None:
            self.__validate_idle_deadline_monotonic = now + self.__AUXILIARY_WORKER_IDLE_GRACE_IN_SECS
        elif now >= self.__validate_idle_deadline_monotonic:
            if self.__teardown_process("idle validate process", self.__validate_process):
                self.__replace_validate_process()
            self.__validate_idle_deadline_monotonic = None

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

    def __resolve_runtime_fallback_paths(self, enabled_path_pairs: Sequence[PathPair]) -> tuple[str, str]:
        first_path_pair = enabled_path_pairs[0] if enabled_path_pairs else None
        local_path = first_path_pair.local_path if first_path_pair is not None else self.__configured_legacy_local_path
        remote_path = first_path_pair.remote_path if first_path_pair is not None else self.__configured_legacy_remote_path
        return (
            Controller.__require_runtime_path(local_path, "Lftp.local_path"),
            Controller.__require_runtime_path(remote_path, "Lftp.remote_path"),
        )

    def __apply_runtime_fallback_paths(self,
                                       local_path: str,
                                       remote_path: str,
                                       staging_path: str) -> None:
        """Apply the no-path-pair fallback consistently across collaborators."""
        self.__legacy_local_path = local_path
        self.__legacy_remote_path = remote_path
        self.__staging_path = staging_path
        self.__lftp.set_base_remote_dir_path(remote_path)
        self.__lftp.set_base_local_dir_path(staging_path)

        extract_process = getattr(self, "_Controller__extract_process", None)
        if extract_process is not None:
            controller_cfg = self.__context.config.controller
            extract_out_dir = local_path if getattr(controller_cfg, "use_local_path_as_extract_path", True) else \
                Controller.__require_runtime_path(controller_cfg.extract_path, "Controller.extract_path")
            extract_process.set_base_paths(
                out_dir_path=extract_out_dir,
                local_path=local_path,
                local_path_fallback=staging_path,
            )

        validate_process = getattr(self, "_Controller__validate_process", None)
        if validate_process is not None:
            validate_process.set_base_paths(local_path, remote_path)

    def __preflight_runtime_storage_roots(
            self,
            enabled_path_pairs: Sequence[PathPair],
            path_pair_staging_paths: Dict[str, str],
            fallback_local_path: Optional[str] = None,
            fallback_staging_path: Optional[str] = None) -> None:
        """Fail before workers use configured storage roots.

        Staging and explicit extraction directories retain the existing
        controller/extractor creation behavior. Final roots are probed after
        those creation steps so a new pair's staging child can establish its
        previously-created parent root.
        """
        config = self.__context.config
        controller_cfg = config.controller
        roots: list[tuple[str, str, bool]] = []
        if enabled_path_pairs:
            for pair in enabled_path_pairs:
                roots.append((pair.local_path, "path pair '{}' local root".format(pair.name), False))
                roots.append((
                    path_pair_staging_paths[pair.id],
                    "path pair '{}' staging root".format(pair.name),
                    True,
                ))
        else:
            roots.extend((
                (fallback_local_path or self.__legacy_local_path, "legacy local root", False),
                (fallback_staging_path or self.__staging_path, "legacy staging root", True),
            ))

        if controller_cfg.use_local_path_as_extract_path is False:
            extract_path = Controller.__require_runtime_path(
                controller_cfg.extract_path,
                "Controller.extract_path",
            )
            roots.append((extract_path, "explicit extraction root", True))

        deduplicated_roots: dict[str, tuple[str, str, bool]] = {}
        for root, label, create_if_staging in roots:
            root_key = os.path.normcase(os.path.abspath(root))
            if root_key in deduplicated_roots:
                previous_root, previous_label, previous_create = deduplicated_roots[root_key]
                deduplicated_roots[root_key] = (previous_root, previous_label, previous_create or create_if_staging)
            else:
                deduplicated_roots[root_key] = (root, label, create_if_staging)

        for root, _, create_if_staging in deduplicated_roots.values():
            if create_if_staging:
                try:
                    os.makedirs(root, exist_ok=True)
                except OSError as exc:
                    raise ControllerError(
                        "Required runtime storage root '{}' is not creatable: {}".format(root, exc)
                    ) from exc

        for root, label, _ in deduplicated_roots.values():
            probe_path = None
            try:
                descriptor, probe_path = tempfile.mkstemp(prefix=".seedsync_write_test.", dir=root)
                os.close(descriptor)
                os.unlink(probe_path)
                probe_path = None
            except OSError as exc:
                raise ControllerError(
                    "Required runtime storage root '{}' ({}) is not writable: {}".format(root, label, exc)
                ) from exc
            finally:
                if probe_path is not None:
                    try:
                        os.unlink(probe_path)
                    except OSError:
                        pass

    def __refresh_path_pair_runtime_state(self, enabled_path_pairs: Optional[List[PathPair]] = None):
        if enabled_path_pairs is None:
            enabled_path_pairs = self.__get_enabled_path_pairs()

        config = self.__context.config
        controller_cfg = config.controller
        fallback_local_path, fallback_remote_path = self.__resolve_runtime_fallback_paths(enabled_path_pairs)
        fallback_staging_path = self.__build_staging_path(
            fallback_local_path,
            config.lftp.staging_path,
        )
        path_pairs_by_id: Dict[str, PathPair] = {pair.id: pair for pair in enabled_path_pairs}
        path_pair_staging_paths: Dict[str, str] = {
            pair.id: self.__build_path_pair_staging_path(pair)
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
        active_scanner = self.__build_active_scanner(
            enabled_path_pairs, path_pair_staging_paths, fallback_staging_path
        )
        local_scanner = self.__build_local_scanner(
            enabled_path_pairs, path_pair_staging_paths, fallback_local_path, fallback_staging_path
        )
        remote_scanner = self.__build_remote_scanner(enabled_path_pairs, fallback_remote_path)
        active_scan_process = ScannerProcess(
            scanner=active_scanner,
            interval_in_ms=Controller.__require_runtime_int(
                controller_cfg.interval_ms_downloading_scan, "Controller.interval_ms_downloading_scan"
            ),
            verbose=False,
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter(),
            recycle_scan_worker=False,
        )
        local_scan_process = ScannerProcess(
            scanner=local_scanner,
            interval_in_ms=Controller.__require_runtime_int(
                controller_cfg.interval_ms_local_scan, "Controller.interval_ms_local_scan"
            ),
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter(),
            # Local scanning builds its retained SystemFile graph directly and
            # has no large serialized scanfs response to discard.  Keeping it
            # inline avoids spawning/importing Python every ten seconds.
            recycle_scan_worker=False,
        )
        remote_scan_process = ScannerProcess(
            scanner=remote_scanner,
            interval_in_ms=Controller.__require_runtime_int(
                controller_cfg.interval_ms_remote_scan, "Controller.interval_ms_remote_scan"
            ),
            breadcrumb_trace=self.__context.breadcrumb_trace.create_emitter(),
            recycle_scan_worker=True,
        )

        old_path_pairs_by_id = self.__path_pairs_by_id
        old_path_pair_staging_paths = self.__path_pair_staging_paths
        old_fallback_paths = (
            self.__legacy_local_path,
            self.__legacy_remote_path,
            self.__staging_path,
        )
        old_runtime = (
            getattr(self, "_Controller__active_scanner", None),
            getattr(self, "_Controller__local_scanner", None),
            getattr(self, "_Controller__remote_scanner", None),
            getattr(self, "_Controller__active_scan_process", None),
            getattr(self, "_Controller__local_scan_process", None),
            getattr(self, "_Controller__remote_scan_process", None),
        )
        try:
            if getattr(self, "_Controller__started", False):
                self.__preflight_runtime_storage_roots(
                    enabled_path_pairs,
                    path_pair_staging_paths,
                    fallback_local_path,
                    fallback_staging_path,
                )
            # A path alias can be repointed after API preflight but before
            # asynchronous activation. Re-bind both configured paths to the
            # reserved directory identity immediately before collaborators see
            # the new roots.
            self.__validate_reserved_relocation_identities(path_pairs_by_id)
            self.__apply_runtime_fallback_paths(
                fallback_local_path,
                fallback_remote_path,
                fallback_staging_path,
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
            self.__sync_persist_to_model_builder_if_ready()
        except Exception as activation_exc:
            # Some builders mutate transfer/model configuration before they
            # fail. Restore every parent-owned reference and configuration so
            # activation is all-or-old rather than a partial new runtime.
            try:
                self.__set_transfer_path_pairs(self.__build_lftp_path_pairs(
                    old_path_pairs_by_id, old_path_pair_staging_paths
                ))
                self.__apply_runtime_fallback_paths(*old_fallback_paths)
                self.__refresh_model_builder_local_paths(old_path_pairs_by_id, old_path_pair_staging_paths)
                self.__sync_persist_to_model_builder_if_ready()
            except Exception as restore_exc:
                # Even a collaborator rollback can fail.  Restore the parent
                # references unconditionally and fail closed; callers must
                # not clear this more-specific consistency error.
                self.__path_pairs_by_id = old_path_pairs_by_id
                self.__path_pair_staging_paths = old_path_pair_staging_paths
                (
                    self.__active_scanner, self.__local_scanner, self.__remote_scanner,
                    self.__active_scan_process, self.__local_scan_process, self.__remote_scan_process,
                ) = old_runtime
                message = "Path pair runtime consistency restore failed: {}".format(restore_exc)
                self.__record_path_pair_runtime_error(message)
                raise ControllerError(message) from restore_exc
            self.__path_pairs_by_id = old_path_pairs_by_id
            self.__path_pair_staging_paths = old_path_pair_staging_paths
            (
                self.__active_scanner, self.__local_scanner, self.__remote_scanner,
                self.__active_scan_process, self.__local_scan_process, self.__remote_scan_process,
            ) = old_runtime
            raise activation_exc

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
            path_pair_staging_paths: Optional[Dict[str, str]] = None,
            fallback_local_path: Optional[str] = None,
            fallback_staging_path: Optional[str] = None):
        if path_pairs_by_id is None:
            path_pairs_by_id = self.__path_pairs_by_id
        if path_pair_staging_paths is None:
            path_pair_staging_paths = self.__path_pair_staging_paths

        local_root_paths: Dict[Optional[str], str] = {None: fallback_local_path or self.__legacy_local_path}
        local_staging_paths: Dict[Optional[str], str] = {None: fallback_staging_path or self.__staging_path}
        for pair_id, pair in path_pairs_by_id.items():
            local_root_paths[pair_id] = pair.local_path
            local_staging_paths[pair_id] = path_pair_staging_paths.get(
                pair_id, fallback_staging_path or self.__staging_path
            )
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
        self,
        enabled_path_pairs: List[PathPair],
        path_pair_staging_paths: dict[str, str],
        fallback_staging_path: Optional[str] = None,
    ) -> ActiveScannerRuntime:
        config = self.__context.config
        if enabled_path_pairs:
            return MultiPathActiveScanner({
                pair.id: path_pair_staging_paths[pair.id] for pair in enabled_path_pairs
            }, use_temp_file=Controller.__require_runtime_bool(config.lftp.use_temp_file, "Lftp.use_temp_file"))
        return ActiveScanner(
            fallback_staging_path or self.__staging_path,
            use_temp_file=Controller.__require_runtime_bool(config.lftp.use_temp_file, "Lftp.use_temp_file")
        )

    def __build_local_scanner(
        self,
        enabled_path_pairs: List[PathPair],
        path_pair_staging_paths: dict[str, str],
        fallback_local_path: Optional[str] = None,
        fallback_staging_path: Optional[str] = None,
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
            local_path=fallback_local_path or self.__legacy_local_path,
            use_temp_file=Controller.__require_runtime_bool(config.lftp.use_temp_file, "Lftp.use_temp_file"),
            staging_path=fallback_staging_path or self.__staging_path,
            managed_extract_folders_enabled=Controller.__require_runtime_bool(
                config.controller.managed_extract_folders_enabled,
                "Controller.managed_extract_folders_enabled",
            )
        )

    def __build_remote_scanner(
            self,
            enabled_path_pairs: List[PathPair],
            fallback_remote_path: Optional[str] = None) -> RemoteScannerRuntime:
        config = self.__context.config
        remote_python_path = getattr(config.lftp, "remote_python_path", None)
        if not isinstance(remote_python_path, str):
            remote_python_path = None
        remote_scan_lease = getattr(self, "_Controller__remote_scan_lease", None)
        remote_scan_lease_kwargs = {} if remote_scan_lease is None else {
            "remote_scan_lease": remote_scan_lease,
        }
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
                    path_pair_name=pair.name,
                    **remote_scan_lease_kwargs,
                ) for pair in enabled_path_pairs
            ])
        return RemoteScanner(
            remote_address=Controller.__require_runtime_path(config.lftp.remote_address, "Lftp.remote_address"),
            remote_username=Controller.__require_runtime_path(config.lftp.remote_username, "Lftp.remote_username"),
            remote_password=self.__ssh_password,
            remote_port=Controller.__require_runtime_int(config.lftp.remote_port, "Lftp.remote_port"),
            remote_path_to_scan=fallback_remote_path or self.__legacy_remote_path,
            local_path_to_scan_script=Controller.__require_runtime_path(
                self.__context.args.local_path_to_scanfs, "Args.local_path_to_scanfs"
            ),
            remote_path_to_scan_script=Controller.__require_runtime_path(
                config.lftp.remote_path_to_scan_script, "Lftp.remote_path_to_scan_script"
            ),
            remote_python_path=remote_python_path,
            **remote_scan_lease_kwargs,
        )

    def __mark_path_pair_refresh_completed(self, generation: Optional[int] = None):
        if generation is None:
            generation = self.__path_pair_refresh_generation
        with self.__path_pair_refresh_lock:
            self.__path_pair_refresh_completed_generation = max(
                self.__path_pair_refresh_completed_generation,
                generation
            )

    def is_path_pair_reconciled(self, path_pair_id: Optional[str]) -> bool:
        """True only after local and remote scans covered this runtime root."""
        return path_pair_id in getattr(self, "_Controller__reconciled_local_path_pair_ids", set()) and \
            path_pair_id in getattr(self, "_Controller__reconciled_remote_path_pair_ids", set())

    def is_remote_delete_eligible(self, file: ModelFile) -> bool:
        """Require final publication proof before deleting staged remote data."""
        if file.path_pair_id is None:
            staging_root = self.__staging_path
            final_root = self.__legacy_local_path
        else:
            path_pair = self.__get_path_pair(file.path_pair_id)
            staging_root = self.__get_staging_path(file.path_pair_id)
            final_root = path_pair.local_path if path_pair is not None else None
        try:
            requires_final_move = (
                not isinstance(staging_root, str) or not isinstance(final_root, str)
                or os.path.normcase(os.path.realpath(staging_root)) != os.path.normcase(os.path.realpath(final_root))
            )
        except OSError:
            requires_final_move = True
        return not requires_final_move or file.file_id in self.__persist.final_move_succeeded_file_names

    def has_current_process_final_publication(self, file: ModelFile) -> bool:
        return file.file_id in getattr(self, "_Controller__current_process_final_publication_file_ids", set())

    def remote_delete_lifecycle_token(self, file: ModelFile) -> tuple[int, int]:
        return (
            getattr(self, "_Controller__path_pair_refresh_generation", 0),
            getattr(self, "_Controller__transfer_lifecycle_epochs", {}).get(file.file_id, 0),
        )

    def __advance_transfer_lifecycle(self, file_id: str) -> None:
        if not hasattr(self, "_Controller__transfer_lifecycle_epochs"):
            self.__transfer_lifecycle_epochs = {}
        self.__transfer_lifecycle_epochs[file_id] = self.__transfer_lifecycle_epochs.get(file_id, 0) + 1

    def _record_path_pair_reconciliation(
            self, local_path_pair_ids: set[str | None], remote_path_pair_ids: set[str | None]) -> None:
        if not hasattr(self, "_Controller__reconciled_local_path_pair_ids"):
            self.__reconciled_local_path_pair_ids = set()
            self.__reconciled_remote_path_pair_ids = set()
        self.__reconciled_local_path_pair_ids.update(local_path_pair_ids)
        self.__reconciled_remote_path_pair_ids.update(remote_path_pair_ids)

    def validate_path_pair_relocation(self, existing: PathPair, updated: PathPair) -> None:
        """Preflight an enabled-pair alias switch without moving user data."""
        if not existing.enabled or existing.local_path == updated.local_path:
            return
        try:
            same_directory = os.path.isdir(existing.local_path) and os.path.isdir(updated.local_path) and \
                os.path.samefile(existing.local_path, updated.local_path)
        except OSError:
            same_directory = False
        if not same_directory:
            raise PathPairError(
                "Enabled path pair local-path relocation requires existing old and new directories that resolve to the same directory"
            )
        if self.__path_pair_busy_file_ids(existing.id):
            raise PathPairError("Path pair '{}' is busy; wait for active work to finish before relocating".format(existing.name))

    def reserve_path_pair_relocation(self, existing: PathPair, updated: PathPair) -> None:
        """Atomically turn an idle alias-switch preflight into a work barrier."""
        if not existing.enabled or existing.local_path == updated.local_path:
            return
        self.validate_path_pair_relocation(existing, updated)
        with self.__work_state_lock:
            try:
                old_stat = os.stat(existing.local_path)
                new_stat = os.stat(updated.local_path)
                identity = (old_stat.st_dev, old_stat.st_ino)
                if identity != (new_stat.st_dev, new_stat.st_ino):
                    raise PathPairError("Enabled path pair relocation directory identity changed during preflight")
            except OSError as exc:
                raise PathPairError("Enabled path pair relocation directory identity could not be verified: {}".format(exc))
            if self.__path_pair_busy_file_ids_locked(existing.id):
                raise PathPairError("Path pair '{}' became busy during relocation preflight".format(existing.name))
            if existing.id in self.__path_pair_relocation_reservations:
                raise PathPairError("Path pair '{}' is already being relocated".format(existing.name))
            self.__path_pair_relocation_reservations.add(existing.id)
            self.__path_pair_relocation_identities[existing.id] = (
                existing.local_path, updated.local_path, identity,
            )

    def release_path_pair_relocation(self, pair_id: str) -> None:
        with self.__work_state_lock:
            self.__path_pair_relocation_reservations.discard(pair_id)
            getattr(self, "_Controller__path_pair_relocation_identities", {}).pop(pair_id, None)

    def __validate_reserved_relocation_identities(self, path_pairs_by_id: Dict[str, PathPair]) -> None:
        with self.__work_state_lock:
            reservations = dict(getattr(self, "_Controller__path_pair_relocation_identities", {}))
        for pair_id, (old_path, new_path, identity) in reservations.items():
            pair = path_pairs_by_id.get(pair_id)
            # The handler retains the reservation while it compensates a
            # failed activation by persisting the old pair again. Accept only
            # that exact old path (never an arbitrary third path), and still
            # re-bind both aliases below before any runtime collaborator sees
            # them.
            if pair is None or pair.local_path not in (new_path, old_path):
                raise PathPairError("Path pair relocation activation no longer matches its reserved path")
            try:
                old_stat = os.stat(old_path)
            except OSError as exc:
                raise PathPairError("Path pair relocation directory identity could not be re-verified: {}".format(exc))
            if (old_stat.st_dev, old_stat.st_ino) != identity:
                raise PathPairError("Path pair relocation directory identity changed before activation")
            if pair.local_path == new_path:
                try:
                    new_stat = os.stat(new_path)
                except OSError as exc:
                    raise PathPairError("Path pair relocation directory identity could not be re-verified: {}".format(exc))
                if (new_stat.st_dev, new_stat.st_ino) != identity:
                    raise PathPairError("Path pair relocation directory identity changed before activation")

    def __path_pair_relocation_reserved(self, path_pair_id: Optional[str]) -> bool:
        with self.__work_state_lock:
            return path_pair_id is not None and path_pair_id in self.__path_pair_relocation_reservations

    def __path_pair_busy_file_ids(self, pair_id: str) -> set[str]:
        """Snapshot controller-owned work before changing a pair root.

        Worker queues are intentionally not inspected: multiprocessing queue
        internals do not provide a parent-side atomic snapshot. Dispatches are
        registered here before handoff and cleared from worker result/status
        observations, making unknown/in-flight work fail closed.
        """
        with self.__work_state_lock:
            return self.__path_pair_busy_file_ids_locked(pair_id)

    def __path_pair_busy_file_ids_locked(self, pair_id: str) -> set[str]:
        file_ids: set[str] = set()
        for name, entry_pair_id, _ in (
            list(self.__active_downloading_file_names) +
            list(self.__active_extracting_file_names) +
            list(self.__pending_completion_file_names)
        ):
            if entry_pair_id == pair_id:
                file_ids.add(ModelFile.build_file_id(name, entry_pair_id))
        file_ids.update(file_id for file_id in self.__queue_dispatch_pending() if self.__file_id_targets_path_pair(file_id, pair_id))
        file_ids.update(
            file_id for file_id in getattr(self, "_Controller__pending_command_dispatch_file_ids", set())
            if self.__file_id_targets_path_pair(file_id, pair_id)
        )
        file_ids.update(file_id for file_id in getattr(self, "_Controller__pending_extract_file_ids", set()) if self.__file_id_targets_path_pair(file_id, pair_id))
        file_ids.update(file_id for file_id in getattr(self, "_Controller__pending_validation_file_ids", set()) if self.__file_id_targets_path_pair(file_id, pair_id))
        with self.__move_attempt_lock:
            pending_ids = set(self.__move_attempt_reservations) | set(self.__deferred_move_file_ids) | set(self.__pending_auto_purge_file_ids)
        file_ids.update(file_id for file_id in pending_ids if self.__file_id_targets_path_pair(file_id, pair_id))
        for status in self.__last_lftp_statuses or []:
            if getattr(status, "path_pair_id", None) == pair_id and getattr(status, "state", None) in (LftpJobStatus.State.QUEUED, LftpJobStatus.State.RUNNING):
                file_ids.add(status.file_id)
        with self.__command_state_lock():
            for wrapper in list(self.__active_command_processes):
                if getattr(wrapper.event_file, "path_pair_id", None) == pair_id or self.__file_id_targets_path_pair(wrapper.file_id, pair_id):
                    file_ids.add(wrapper.file_id)
            with self.__command_queue.mutex:
                queued_commands = list(self.__command_queue.queue)
            for command in queued_commands:
                if self.__command_targets_path_pair(command, pair_id):
                    file_ids.add(command.filename)
            for command in self.__deferred_delete_commands():
                if self.__command_targets_path_pair(command, pair_id):
                    file_ids.add(command.filename)
        return file_ids

    def _record_worker_dispatch(self, kind: str, file_id: str) -> None:
        with self.__work_state_lock:
            if kind == "extract":
                self.__pending_extract_file_ids.add(file_id)
            elif kind == "validate":
                self.__pending_validation_file_ids.add(file_id)

    def _record_worker_terminal_ids(self, extract_ids: set[str], validation_ids: set[str]) -> None:
        with self.__work_state_lock:
            self.__pending_extract_file_ids.difference_update(extract_ids)
            self.__pending_validation_file_ids.difference_update(validation_ids)

    def __begin_command_dispatch(self, file_id: str, path_pair_id: Optional[str]) -> bool:
        """Atomically admit a dequeued command against a relocation barrier."""
        with self.__work_state_lock:
            if path_pair_id is not None and path_pair_id in self.__path_pair_relocation_reservations:
                return False
            if not hasattr(self, "_Controller__pending_command_dispatch_file_ids"):
                self.__pending_command_dispatch_file_ids = set()
            self.__pending_command_dispatch_file_ids.add(file_id)
            drain_ids = getattr(self, "_Controller__command_dispatch_drain_file_ids", None)
            if isinstance(drain_ids, set):
                drain_ids.add(file_id)
            return True

    def __end_command_dispatch(self, file_id: str) -> None:
        with self.__work_state_lock:
            getattr(self, "_Controller__pending_command_dispatch_file_ids", set()).discard(file_id)

    def __command_targets_path_pair(self, command: "Controller.Command", pair_id: str) -> bool:
        if self.__file_id_targets_path_pair(command.filename, pair_id):
            return True
        try:
            file = self.__model.get_file(command.filename)
        except Exception:
            return False
        return getattr(file, "path_pair_id", None) == pair_id

    @staticmethod
    def __file_id_targets_path_pair(file_id: object, pair_id: str) -> bool:
        if not isinstance(file_id, str):
            return False
        try:
            decoded = json.loads(file_id)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(decoded, list) and len(decoded) == 2 and decoded[0] == pair_id

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
            fallback_paths: tuple[str, str, str],
            active_scanner: ActiveScannerRuntime,
            local_scanner: LocalScannerRuntime,
            remote_scanner: RemoteScannerRuntime,
            active_scan_process: ScannerProcess,
            local_scan_process: ScannerProcess,
            remote_scan_process: ScannerProcess) -> None:
        self.__apply_runtime_fallback_paths(*fallback_paths)
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
        self.__sync_persist_to_model_builder_if_ready()
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

    def __sync_persist_to_model_builder_if_ready(self) -> None:
        """Refresh marker/timestamp overlays after runtime pair identity changes."""
        updater = getattr(self, "_Controller__updater", None)
        if updater is not None:
            updater.sync_persist_to_all_builders()

    def __apply_path_pair_refresh(self):
        if not self.__cancel_and_settle_collision_claim_for_refresh():
            raise ControllerError("Path-pair refresh deferred until collision comparison claim is restored")
        self.__collision_compare_epoch = getattr(self, "_Controller__collision_compare_epoch", 0) + 1
        runtime_error_before_refresh = self.__path_pair_runtime_error
        # A path-pair refresh starts a new scan generation. Any prior scan
        # health evidence belongs to the old roots and must not authorize
        # marker pruning before both refreshed scanners report healthy.
        self._Controller__last_remote_reconciliation_healthy = False
        self._Controller__last_local_reconciliation_healthy = False
        self.__reconciled_local_path_pair_ids = set()
        self.__reconciled_remote_path_pair_ids = set()
        self.__current_process_final_publication_file_ids = set()
        with self.__work_state_lock:
            active_files = list(
                getattr(self, "_Controller__active_downloading_file_names", []) +
                getattr(self, "_Controller__active_extracting_file_names", []) +
                list(getattr(self, "_Controller__pending_completion_file_names", []))
            )
        # The replacement scanner starts with a new root set; readiness from
        # the previous process must not authorize STOP before it reports a
        # fresh checkpoint for the active transfer.
        self.__active_scan_force_file_ids = set(
            getattr(self, "_Controller__active_scan_force_file_ids", set())
        )
        self.__active_scan_ready_file_ids = set(
            getattr(self, "_Controller__active_scan_ready_file_ids", set())
        )
        self.__active_scan_force_file_ids.clear()
        self.__active_scan_ready_file_ids.clear()
        self.__next_active_scan_force_at = None
        was_started = self.__started
        old_active_scan_process = self.__active_scan_process
        old_local_scan_process = self.__local_scan_process
        old_remote_scan_process = self.__remote_scan_process
        old_path_pairs_by_id = self.__path_pairs_by_id
        old_path_pair_staging_paths = self.__path_pair_staging_paths
        old_fallback_paths = (
            self.__legacy_local_path,
            self.__legacy_remote_path,
            self.__staging_path,
        )
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
            refreshed_validation_pairs: dict[str, object] = dict(self.__path_pairs_by_id)
            self.__validate_process.set_path_pairs_by_id(refreshed_validation_pairs)
            if was_started:
                self.__active_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
                self.__local_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)
                self.__remote_scan_process.set_mp_log_queue(self.__mp_logger.queue, self.__mp_logger.log_level)

            if was_started:
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
            if (
                    self.__path_pair_runtime_error is not None
                    and self.__path_pair_runtime_error != runtime_error_before_refresh
            ):
                # The inner activation rollback recorded a more precise,
                # fail-closed consistency failure.  Do not overwrite it.
                return
            if new_state_applied:
                new_active_scan_process_stopped = stop_process(self.__active_scan_process)
                stop_process(self.__local_scan_process)
                stop_process(self.__remote_scan_process)
                if new_active_scan_process_stopped:
                    close_active_scanner("new active scanner close", self.__active_scanner)
                try:
                    self.__restore_path_pair_runtime_state(
                        old_path_pairs_by_id, old_path_pair_staging_paths,
                        old_fallback_paths,
                        old_active_scanner, old_local_scanner, old_remote_scanner,
                        old_active_scan_process, old_local_scan_process, old_remote_scan_process
                    )
                except Exception as restore_exc:
                    # Keep the old parent references even if a collaborator
                    # cannot be restored.  The server remains down rather
                    # than continuing against an uncertain root mapping.
                    self.__path_pairs_by_id = old_path_pairs_by_id
                    self.__path_pair_staging_paths = old_path_pair_staging_paths
                    self.__active_scanner = old_active_scanner
                    self.__local_scanner = old_local_scanner
                    self.__remote_scanner = old_remote_scanner
                    self.__active_scan_process = old_active_scan_process
                    self.__local_scan_process = old_local_scan_process
                    self.__remote_scan_process = old_remote_scan_process
                    self.__record_path_pair_runtime_error(
                        "Path pair runtime consistency restore failed: {}".format(restore_exc)
                    )
                    self.logger.exception("Path pair runtime restoration failed")
                    return
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
        # A restarted controller must not reuse scan authority from an earlier
        # process generation.
        self.__reconciled_local_path_pair_ids = set()
        self.__reconciled_remote_path_pair_ids = set()
        self.__current_process_final_publication_file_ids = set()
        self.__preflight_runtime_storage_roots(
            list(self.__path_pairs_by_id.values()),
            self.__path_pair_staging_paths,
        )
        # Keep partial startup failure separate so exit() can clean up already
        # started workers without making process() look fully live.
        self.__startup_failed = False
        try:
            self.__active_scan_process.start()
            self.__local_scan_process.start()
            self.__remote_scan_process.start()
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
        diagnostics = getattr(self.__context, "performance_diagnostics", None)
        try:
            started_at = diagnostics.begin_duration(DURATION_CONTROLLER_PROCESS) if diagnostics is not None else None
        except Exception:
            started_at = None
        try:
            with self.__persist.state_transaction():
                self.__process_persist_transaction()
        finally:
            if diagnostics is not None:
                try:
                    diagnostics.finish_duration(DURATION_CONTROLLER_PROCESS, started_at)
                except Exception:
                    pass

    def __process_persist_transaction(self):
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
        diagnostics = getattr(self.__context, "performance_diagnostics", None)
        try:
            started_at = diagnostics.begin_duration(DURATION_MODEL_UPDATE) if diagnostics is not None else None
        except Exception:
            started_at = None
        try:
            self.__updater.update()
        finally:
            if diagnostics is not None:
                try:
                    diagnostics.finish_duration(DURATION_MODEL_UPDATE, started_at)
                except Exception:
                    pass
        self.__reap_idle_auxiliary_workers()
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
        if worker is None or worker.pid is None:
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
        self.__shutdown_collision_compare_worker()
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

    def _get_model_root_references(self) -> List[ModelFile]:
        """Shallow, controller-internal root snapshot for same-process consumers.

        Callers must treat returned files as read-only.  Public model APIs
        continue to return deep copies for isolation across their boundary.
        """
        with self.__model_lock:
            return list(self.__model.iter_files_by_id())

    @staticmethod
    def _model_scope_id(path_pair_id: Optional[str]) -> str:
        return path_pair_id if path_pair_id is not None else MODEL_LEGACY_SCOPE_ID

    @staticmethod
    def _model_state_name(file: ModelFile) -> str:
        return file.state.name.lower()

    @classmethod
    def _model_file_page_record(cls, file: ModelFile) -> dict[str, object]:
        """Build one JSON-ready, shallow model record while the model is locked.

        Do not copy the ModelFile and, in particular, do not walk descendants
        here.  Children are fetched through the scoped child-page endpoint.
        """
        child_count = file.child_count
        return {
            "name": file.name,
            "is_dir": file.is_dir,
            "state": cls._model_state_name(file),
            "remote_size": file.remote_size,
            "local_size": file.local_size,
            "remote_present": file.remote_present,
            "local_present": file.local_present,
            "remote_has_transferable_content": file.remote_has_transferable_content,
            "transferred_size": file.transferred_size,
            "download_progress": file.download_progress,
            "downloading_speed": file.downloading_speed,
            "eta": file.eta,
            "is_extractable": file.is_extractable,
            "is_stoppable": file.is_stoppable,
            "local_created_timestamp": str(file.local_created_timestamp.timestamp()) if file.local_created_timestamp else None,
            "local_modified_timestamp": str(file.local_modified_timestamp.timestamp()) if file.local_modified_timestamp else None,
            "remote_created_timestamp": str(file.remote_created_timestamp.timestamp()) if file.remote_created_timestamp else None,
            "remote_modified_timestamp": str(file.remote_modified_timestamp.timestamp()) if file.remote_modified_timestamp else None,
            "downloaded_timestamp": str(file.downloaded_timestamp.timestamp()) if file.downloaded_timestamp else None,
            "full_path": file.full_path,
            "file_id": file.file_id,
            "path_pair_id": file.path_pair_id,
            "path_pair_name": file.path_pair_name,
            "validation_progress": file.validation_progress,
            "validation_error": file.validation_error,
            "corrupt_chunks": file.corrupt_chunks,
            "final_move_succeeded": file.final_move_succeeded,
            # Keep the established object shape without smuggling the tree
            # through a page response.
            "children": [],
            "child_count": child_count,
            "has_children": child_count > 0,
        }

    @staticmethod
    def _model_record_visible_state(file: ModelFile) -> str:
        if file.local_present and not file.remote_has_transferable_content:
            return "local_only"
        has_retained_progress = (
            file.remote_has_transferable_content
            and (file.remote_size or 0) > 0
            and ((file.transferred_size or 0) > 0 or (file.download_progress or 0) > 0)
        )
        if file.state == ModelFile.State.DEFAULT and has_retained_progress:
            return "stopped"
        if file.state == ModelFile.State.DOWNLOADED and file.final_move_succeeded:
            return "move_succeeded"
        return file.state.name.lower()

    @staticmethod
    def __scoped_file_path(scope_id: str, file_id: str) -> str:
        """Decode one canonical ModelFile identity without searching the model."""
        if scope_id == MODEL_LEGACY_SCOPE_ID:
            path = file_id
        else:
            try:
                payload = json.loads(file_id)
            except (TypeError, ValueError) as exc:
                raise ModelPageCursorError("File identity is malformed") from exc
            if (
                not isinstance(payload, list) or len(payload) != 2
                or payload[0] != scope_id or not isinstance(payload[1], str)
            ):
                raise ModelPageCursorError("File identity is outside this path-pair scope")
            path = payload[1]
        if not isinstance(path, str) or not path or os.path.isabs(path):
            raise ModelPageCursorError("File identity is malformed")
        normalized = os.path.normpath(path)
        if normalized in {".", ".."} or normalized.startswith(".." + os.sep):
            raise ModelPageCursorError("File identity is malformed")
        expected_path_pair_id = None if scope_id == MODEL_LEGACY_SCOPE_ID else scope_id
        if ModelFile.build_file_id(normalized, expected_path_pair_id) != file_id:
            raise ModelPageCursorError("File identity is not canonical")
        return normalized

    def __resolve_scoped_model_file(self, scope_id: str, file_id: str) -> ModelFile:
        path = self.__scoped_file_path(scope_id, file_id)
        components = path.split(os.sep)
        if not components or any(not component for component in components):
            raise ModelPageCursorError("File identity is malformed")
        expected_path_pair_id = None if scope_id == MODEL_LEGACY_SCOPE_ID else scope_id
        root_id = ModelFile.build_file_id(components[0], expected_path_pair_id)
        try:
            current = self.__model.get_file(root_id)
        except ModelError as exc:
            raise ModelPageCursorError("File does not exist in this path-pair scope") from exc
        if current.path_pair_id != expected_path_pair_id:
            raise ModelPageCursorError("File is outside this path-pair scope")
        for component in components[1:]:
            current = next((child for child in current.iter_children() if child.name == component), None)
            if current is None:
                raise ModelPageCursorError("File does not exist in this path-pair scope")
        if current.file_id != file_id:
            raise ModelPageCursorError("File identity is not canonical")
        return current

    def __scoped_model_file_candidates(
        self, scope_id: str, parent_file_id: Optional[str]
    ) -> Iterable[ModelFile]:
        if parent_file_id is not None:
            return self.__resolve_scoped_model_file(scope_id, parent_file_id).iter_children()
        expected_path_pair_id = None if scope_id == MODEL_LEGACY_SCOPE_ID else scope_id

        def roots() -> Iterable[ModelFile]:
            for candidate in self.__model.iter_files_by_id():
                if candidate.path_pair_id == expected_path_pair_id:
                    yield candidate
        return roots()

    def __bounded_model_page_files(
        self,
        candidates: Iterable[ModelFile],
        limit: int,
        cursor_file_id: Optional[str],
        cursor_sort_key: Optional[tuple[object, ...]],
        scope_id: str, parent_file_id: Optional[str],
    ) -> tuple[int, list[ModelFile], bool]:
        del scope_id
        after_id = cursor_file_id
        if cursor_sort_key is not None and len(cursor_sort_key) == 1 and isinstance(cursor_sort_key[0], str):
            after_id = cursor_sort_key[0]
        total = 0
        if parent_file_id is None:
            # Model.iter_files_by_id is a controller-lock-stable canonical
            # identity index. Root cursors never depend on mutable view state.
            selected: list[ModelFile] = []
            has_successor = False
            for candidate in candidates:
                total += 1
                if after_id is not None and candidate.file_id <= after_id:
                    continue
                if len(selected) < limit:
                    selected.append(candidate)
                else:
                    has_successor = True
            return total, selected, has_successor

        # Children are not part of the dashboard transport path, but retain a
        # bounded identity page for API symmetry without copying their list.
        selected_heap: list[tuple[_ReverseModelSortKey, int, ModelFile]] = []
        has_successor = False
        serial = 0
        for candidate in candidates:
            total += 1
            key = (candidate.file_id,)
            if after_id is not None and candidate.file_id <= after_id:
                continue
            entry = (_ReverseModelSortKey(key), serial, candidate)
            serial += 1
            if len(selected_heap) < limit:
                heapq.heappush(selected_heap, entry)
            elif key < selected_heap[0][0].key:
                heapq.heapreplace(selected_heap, entry)
                has_successor = True
            else:
                has_successor = True
        selected = [entry[2] for entry in selected_heap]
        selected.sort(key=lambda file: file.file_id)
        return total, selected, has_successor

    def __get_model_page_locked(
        self,
        scope_id: str,
        limit: int,
        cursor_file_id: Optional[str] = None,
        cursor_version: Optional[int] = None,
        cursor_sort_key: Optional[tuple[object, ...]] = None,
        parent_file_id: Optional[str] = None, sort_mode: int = 1, status_filter: Optional[str] = None,
        name_filter: Optional[str] = None,
    ) -> dict[str, object]:
        expected_path_pair_id = None if scope_id == MODEL_LEGACY_SCOPE_ID else scope_id
        version = self.__model.scope_version(expected_path_pair_id)
        total, page_files, has_successor = self.__bounded_model_page_files(
            self.__scoped_model_file_candidates(scope_id, parent_file_id),
            limit, cursor_file_id, cursor_sort_key, scope_id, parent_file_id,
        )
        next_cursor_file_id = None
        if has_successor and page_files:
            next_cursor_file_id = page_files[-1].file_id
        return {
            "model_version": version,
            "path_pair_id": scope_id,
            "parent_file_id": parent_file_id,
            "limit": limit,
            "total": total,
            "records": [self._model_file_page_record(file) for file in page_files],
            # The handler turns this identity/version pair into an opaque
            # cursor; keeping it primitive here lets the controller stay HTTP
            # agnostic and makes atomic setup reusable by SSE.
            "next_cursor_file_id": next_cursor_file_id,
            "next_cursor_sort_key": (page_files[-1].file_id,) if next_cursor_file_id else None,
        }

    def get_model_page(
        self,
        scope_id: str,
        limit: int,
        cursor_file_id: Optional[str] = None,
        cursor_version: Optional[int] = None,
        cursor_sort_key: Optional[tuple[object, ...]] = None,
        parent_file_id: Optional[str] = None, sort_mode: int = 1, status_filter: Optional[str] = None,
        name_filter: Optional[str] = None,
    ) -> dict[str, object]:
        with self.__model_lock:
            return self.__get_model_page_locked(
                scope_id, limit, cursor_file_id, cursor_version, cursor_sort_key, parent_file_id, sort_mode, status_filter, name_filter
            )

    def get_model_page_and_add_listener(
        self,
        listener: IModelListener,
        scope_id: str,
        limit: int,
        cursor_file_id: Optional[str] = None,
        cursor_version: Optional[int] = None,
        cursor_sort_key: Optional[tuple[object, ...]] = None,
        parent_file_id: Optional[str] = None, sort_mode: int = 1, status_filter: Optional[str] = None,
        name_filter: Optional[str] = None,
    ) -> dict[str, object]:
        """Atomically register a scoped listener and return its initial page."""
        diagnostics = getattr(self.__context, "performance_diagnostics", None)
        if diagnostics is not None:
            diagnostics.increment("model_scoped_snapshot_registrations")
        with self.__model_lock:
            page = self.__get_model_page_locked(
                scope_id, limit, cursor_file_id, cursor_version, cursor_sort_key, parent_file_id, sort_mode, status_filter, name_filter
            )
            self.__model.add_listener(listener)
            return page

    def get_model_root_updates(self, scope_id: str, file_ids: Iterable[str]) -> dict[str, object]:
        """Map primitive changed identities to bounded shallow root patches.

        Listener queues deliberately retain no ModelFile references.  A child
        identity maps to its root at emission time so dashboard rows can patch
        without rescanning the selected scope.
        """
        expected_path_pair_id = None if scope_id == MODEL_LEGACY_SCOPE_ID else scope_id
        with self.__model_lock:
            root_ids: set[str] = set()
            for file_id in file_ids:
                try:
                    path = self.__scoped_file_path(scope_id, file_id)
                except ModelPageCursorError:
                    continue
                root_name = path.split(os.sep)[0]
                root_ids.add(ModelFile.build_file_id(root_name, expected_path_pair_id))
            records: list[dict[str, object]] = []
            removed_root_ids: list[str] = []
            for root_id in sorted(root_ids):
                try:
                    root = self.__model.get_file(root_id)
                except ModelError:
                    removed_root_ids.append(root_id)
                    continue
                if root.path_pair_id == expected_path_pair_id:
                    records.append(self._model_file_page_record(root))
                else:
                    removed_root_ids.append(root_id)
            return {
                "model_version": self.__model.scope_version(expected_path_pair_id),
                "records": records,
                "removed_file_ids": removed_root_ids,
            }

    def get_model_summary(self, max_age_seconds: float = 0.0) -> dict[str, object]:
        """Return compact root-only counts; deliberately no file tree records."""
        with self.__model_lock:
            now = time.monotonic()
            cached_summary = getattr(self, "_Controller__model_summary_cache", None)
            cached_at = getattr(self, "_Controller__model_summary_cache_at", 0.0)
            if (
                max_age_seconds > 0 and isinstance(cached_summary, dict)
                and cached_summary.get("model_version") == self.__model.version
                and now - cached_at < max_age_seconds
            ):
                return cached_summary
            summaries: dict[str, dict[str, object]] = {}
            reconciled_local_scopes = {
                self._model_scope_id(value) for value in self.__reconciled_local_path_pair_ids
            }
            reconciled_remote_scopes = {
                self._model_scope_id(value) for value in self.__reconciled_remote_path_pair_ids
            }
            for file in self.__model.iter_files():
                scope_id = self._model_scope_id(file.path_pair_id)
                summary = summaries.setdefault(scope_id, {
                    "path_pair_id": scope_id,
                    "path_pair_name": file.path_pair_name,
                    "root_count": 0,
                    "remote_size": 0,
                    "local_size": 0,
                    "transferred_size": 0,
                    "downloading_speed": 0,
                    "remaining_bytes": 0,
                    "active_eta_seconds_max": None,
                    "active_count": 0,
                    "queued_count": 0,
                    "completed_count": 0,
                    "state_counts": {},
                    "visible_state_counts": {},
                    "reconciled_local": scope_id in reconciled_local_scopes,
                    "reconciled_remote": scope_id in reconciled_remote_scopes,
                })
                if summary["path_pair_name"] is None and file.path_pair_name is not None:
                    summary["path_pair_name"] = file.path_pair_name
                summary["root_count"] = int(summary["root_count"]) + 1
                # Match the legacy path-pair card: roots without a positive
                # remote size still contribute state counts, but not byte
                # totals. Completion prefers transferred bytes, then local.
                if file.remote_size is not None and file.remote_size > 0:
                    completed_bytes = file.transferred_size
                    if completed_bytes is None:
                        completed_bytes = file.local_size
                    completed_bytes = min(max(completed_bytes or 0, 0), file.remote_size)
                    summary["remote_size"] = int(summary["remote_size"]) + file.remote_size
                    summary["transferred_size"] = int(summary["transferred_size"]) + completed_bytes
                    summary["local_size"] = int(summary["local_size"]) + completed_bytes
                state_counts = cast(dict[str, int], summary["state_counts"])
                state = self._model_state_name(file)
                state_counts[state] = state_counts.get(state, 0) + 1
                visible_state_counts = cast(dict[str, int], summary["visible_state_counts"])
                visible_state = self._model_record_visible_state(file)
                visible_state_counts[visible_state] = visible_state_counts.get(visible_state, 0) + 1
                if file.state == ModelFile.State.DOWNLOADING:
                    summary["active_count"] = int(summary["active_count"]) + 1
                    summary["downloading_speed"] = int(summary["downloading_speed"]) + (file.downloading_speed or 0)
                    if file.remote_size is not None:
                        summary["remaining_bytes"] = int(summary["remaining_bytes"]) + max(
                            0, file.remote_size - (file.transferred_size or 0)
                        )
                    if file.eta is not None:
                        current_eta = summary["active_eta_seconds_max"]
                        summary["active_eta_seconds_max"] = max(
                            current_eta if isinstance(current_eta, int) else 0, file.eta
                        )
                elif file.state == ModelFile.State.QUEUED:
                    summary["queued_count"] = int(summary["queued_count"]) + 1
                elif file.state in {
                    ModelFile.State.DOWNLOADED,
                    ModelFile.State.EXTRACTED,
                }:
                    summary["completed_count"] = int(summary["completed_count"]) + 1
            summary = {
                "model_version": self.__model.version,
                "path_pairs": [summaries[key] for key in sorted(summaries)],
            }
            self.__model_summary_cache = summary
            self.__model_summary_cache_at = now
            return summary

    def get_model_summary_and_add_listener(self, listener: IModelListener) -> dict[str, object]:
        """Atomically subscribe a compact-summary stream after its snapshot."""
        diagnostics = getattr(self.__context, "performance_diagnostics", None)
        if diagnostics is not None:
            diagnostics.increment("model_summary_snapshot_registrations")
        with self.__model_lock:
            summary = self.get_model_summary()
            self.__model.add_listener(listener)
            return summary

    def is_file_stopped(self, filename: str) -> bool:
        return filename in self.__persist.stopped_file_names

    def get_stop_resume_trace_metadata(self, file: Optional[ModelFile]) -> Optional[dict[str, object]]:
        """Return active-transfer model stream metadata for this refresh cycle."""
        return self.__model_builder.stop_resume_trace_metadata_for_file(file)

    def is_stop_resume_trace_enabled(self) -> bool:
        """Return the current breadcrumb gate for late SSE emission checks."""
        try:
            return bool(self.__model_builder.is_stop_resume_trace_enabled())
        except Exception:
            return False

    def record_stop_resume_trace_breadcrumb(
        self,
        stage: str,
        file: Optional[ModelFile],
        details: Optional[dict[str, object]] = None,
    ) -> None:
        """Record stream breadcrumbs without exposing local/remote paths."""
        metadata = self.get_stop_resume_trace_metadata(file)
        breadcrumb_trace = getattr(self.__context, "breadcrumb_trace", None)
        if not isinstance(metadata, dict) or breadcrumb_trace is None:
            return
        supplied_details = details if isinstance(details, dict) else {}
        supplied_corr_id = supplied_details.get("corr_id")
        supplied_file_id = supplied_details.get("file_id")
        corr_id = supplied_corr_id if isinstance(supplied_corr_id, str) else (
            metadata.get("corr_id") if isinstance(metadata.get("corr_id"), str) else None
        )
        file_id = supplied_file_id if isinstance(supplied_file_id, str) else (
            metadata.get("file_id") if isinstance(metadata.get("file_id"), str) else None
        )
        safe_details: dict[str, object] = {
            "cycle": supplied_details.get("cycle", metadata.get("cycle")),
            "corr_id": corr_id,
            "file_id": file_id,
        }
        safe_details.update(supplied_details)
        try:
            breadcrumb_trace.record(
                "model_stream",
                "stop_resume_trace_{}".format(stage),
                safe_details,
                stage="model_stream_{}".format(stage),
                event_type="diagnostic",
                corr_id=corr_id,
                file_id=file_id,
                trace_scope="flow",
            )
        except Exception:
            # Diagnostics must never affect SSE/model delivery.
            self.logger.debug("Ignoring model stream breadcrumb failure", exc_info=True)

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

    @staticmethod
    def _download_timestamp_clock() -> datetime:
        """Single clock source for persisted download-recency timestamps."""
        return datetime.now()

    def _record_download_start(self, file: ModelFile) -> None:
        """Record a confirmed fresh start using the canonical path-pair identity."""
        timestamp = self._download_timestamp_clock().timestamp()
        if not isinstance(getattr(self.__persist, "downloaded_timestamps", None), dict):
            self.__persist.downloaded_timestamps = {}
        self.__persist.downloaded_timestamps[file.file_id] = timestamp
        self.__model_builder.set_downloaded_timestamps(self.__persist.downloaded_timestamps)

    def _record_download_completion(self, file: ModelFile) -> None:
        """Backfill recency when a start was not observed before completion."""
        if not isinstance(getattr(self.__persist, "downloaded_timestamps", None), dict):
            self.__persist.downloaded_timestamps = {}
        if file.file_id not in self.__persist.downloaded_timestamps:
            self.__persist.downloaded_timestamps[file.file_id] = self._download_timestamp_clock().timestamp()
        self.__model_builder.set_downloaded_timestamps(self.__persist.downloaded_timestamps)

    def _mark_successful_final_move_handoff(self, file_id: str) -> None:
        """Quarantine a stale active-scan root while a completed move settles."""
        if not hasattr(self, "_Controller__successful_final_move_handoff_file_ids"):
            self.__successful_final_move_handoff_file_ids = set()
        self.__successful_final_move_handoff_file_ids.add(file_id)

    def _mark_current_process_final_publication(self, file_id: str) -> None:
        if not hasattr(self, "_Controller__current_process_final_publication_file_ids"):
            self.__current_process_final_publication_file_ids = set()
        self.__current_process_final_publication_file_ids.add(file_id)
        evict_active_file_ids = getattr(self.__model_builder, "evict_active_file_ids", None)
        if callable(evict_active_file_ids):
            evict_active_file_ids({file_id})
        self.__active_scan_process.force_scan()

    def _final_move_succeeded_files_for_model(self) -> set[str]:
        """Return move markers that represent a user-selected staging workflow."""
        if not self.__explicit_staging_path_configured:
            return set()
        return set(self.__persist.final_move_succeeded_file_names)

    def _sync_final_move_succeeded_files_to_model(self) -> None:
        self.__model_builder.set_final_move_succeeded_files(
            self._final_move_succeeded_files_for_model()
        )

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
                self._record_download_start(file)
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
        diagnostics = getattr(self.__context, "performance_diagnostics", None)
        if diagnostics is not None:
            diagnostics.increment("model_full_snapshot_listener_registrations")
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
        if self.__path_pair_runtime_error is not None:
            for callback in command.callbacks:
                callback.on_failure(self.__path_pair_runtime_error, 503)
            return
        if getattr(command, "flow_id", None) is None:
            command.flow_id = self.__next_command_flow_id(command)
        path_pair_id: Optional[str] = None
        if isinstance(command.filename, str):
            try:
                decoded = json.loads(command.filename)
                if isinstance(decoded, list) and len(decoded) == 2 and isinstance(decoded[0], str):
                    path_pair_id = decoded[0]
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if self.__path_pair_relocation_reserved(path_pair_id):
            for callback in command.callbacks:
                callback.on_failure("Path pair relocation is in progress", 409)
            return
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
                        # A user-initiated delete must not inherit an
                        # auto-queue command's lifecycle-only authority when
                        # the requests coalesce.  The pending command remains
                        # the queue representative, but its authority becomes
                        # manual.  An auto-queue request behind a manual one
                        # deliberately leaves that manual authority intact.
                        if (
                                command.action == Controller.Command.Action.DELETE_REMOTE
                                and getattr(command, "origin", "manual") != "auto_queue"
                                and getattr(duplicate_delete_command, "origin", "manual") == "auto_queue"
                        ):
                            duplicate_delete_command.origin = command.origin
                            duplicate_delete_command.lifecycle_token = None
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
        diagnostics = getattr(self.__context, "performance_diagnostics", None)
        if diagnostics is not None:
            diagnostics.increment("model_full_snapshot_requests")
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

    def __build_path_pair_staging_path(self, pair: PathPair) -> str:
        if not self.__explicit_staging_path_configured:
            return self.__build_staging_path(pair.local_path)
        # Keep pair staging roots stable across display-name changes and safely
        # contained even if an imported pair id contains path separators.
        pair_directory = hashlib.sha256(pair.id.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.__staging_path, pair_directory)

    @staticmethod
    def __persist_key_candidates(name: str, path_pair_id: Optional[str] = None) -> set[str]:
        return {ModelFile.build_file_id(name, path_pair_id)}

    @staticmethod
    def __has_persist_key(keys: set[str], name: str, path_pair_id: Optional[str] = None) -> bool:
        if not keys:
            return False
        return bool(keys.intersection(Controller.__persist_key_candidates(name, path_pair_id)))

    @staticmethod
    def __clear_persist_key(keys: set[str], name: str, path_pair_id: Optional[str] = None) -> None:
        keys.difference_update(Controller.__persist_key_candidates(name, path_pair_id))

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
        except (OSError, TypeError, ValueError):
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
        breadcrumb_trace = getattr(self.__context, "breadcrumb_trace", None)
        if breadcrumb_trace is None:
            return
        try:
            if not breadcrumb_trace.is_enabled():
                return
        except Exception:
            return

        temp_path = None
        sidecar_path = None
        try:
            if local_base_dir_path is not None and not is_dir:
                temp_path = os.path.join(local_base_dir_path, file_name + Constants.LFTP_TEMP_FILE_SUFFIX)
                sidecar_path = temp_path + ".lftp-pget-status"
            temp_details = self.__get_stop_resume_trace_file_details(temp_path, include_allocated_size=True)
            sidecar_details = self.__get_stop_resume_trace_file_details(sidecar_path)
        except Exception:
            # A diagnostic probe must never interfere with the command path.
            return
        # Keep boundary records safe for post-hoc diagnostics: canonical file
        # identity and scalar filesystem facts are useful, while local/remote
        # paths, command data, and credentials are intentionally omitted.
        try:
            breadcrumb_trace.record(
                "controller",
                "stop_resume_boundary",
                {
                    "reason": reason,
                    "file_id": file_id,
                    "path_pair_id": path_pair_id,
                    "current_state": current_state,
                    "is_dir": is_dir,
                    "stopped_marked": stopped_marked,
                    "temp": temp_details,
                    "sidecar": sidecar_details,
                },
                stage="controller_boundary_{}".format(reason),
                event_type="diagnostic",
                corr_id="stop-resume:{}:{}".format(
                    file_id,
                    getattr(self, "_Controller__stop_resume_trace_cycle_id", 0),
                ),
                file_id=file_id,
                trace_scope="flow",
            )
        except Exception:
            # Diagnostics must never alter transfer commands or model delivery.
            self.logger.debug("Ignoring stop/resume boundary breadcrumb failure", exc_info=True)

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

    def clear_extracted_marker(self, file: ModelFile) -> None:
        with self.__persist.state_transaction():
            self.__clear_extracted_marker_in_state_transaction(file)

    def __clear_extracted_marker_in_state_transaction(self, file: ModelFile) -> None:
        stale_extracted_file_names: set[str] = set()
        if file.file_id in self.__persist.extracted_file_names:
            stale_extracted_file_names.add(file.file_id)
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

    @staticmethod
    def __safe_existing_directory(path: str) -> bool:
        try:
            path_stat = os.lstat(path)
        except OSError:
            return False
        return stat.S_ISDIR(path_stat.st_mode) and not stat.S_ISLNK(path_stat.st_mode)

    @staticmethod
    def __safe_merge_child(parent: str, child_name: str) -> Optional[str]:
        """Build one contained child path without following parent links."""
        separators = {os.sep}
        if os.altsep:
            separators.add(os.altsep)
        if not child_name or child_name in (".", "..") or any(
                separator in child_name for separator in separators):
            return None
        try:
            candidate = os.path.normpath(os.path.join(parent, child_name))
            if os.path.normcase(os.path.commonpath([os.path.abspath(parent), candidate])) != \
                    os.path.normcase(os.path.abspath(parent)):
                return None
            if not Controller.__safe_existing_directory(parent):
                return None
            return candidate
        except (OSError, ValueError):
            return None

    @staticmethod
    def __staging_collision_is_verified_equivalent(
            source_stat: os.stat_result, destination_stat: os.stat_result) -> bool:
        """Recognize the same portable source identity without overwriting.

        LFTP preserves mtimes at second precision.  Matching regular-file
        size and that precision is only a prefilter; claimed leaves receive a
        bounded descriptor-level byte comparison before staging cleanup.
        """
        return stat.S_ISREG(source_stat.st_mode) and stat.S_ISREG(destination_stat.st_mode) and \
            source_stat.st_size == destination_stat.st_size and \
            source_stat.st_mtime_ns // 1_000_000_000 == destination_stat.st_mtime_ns // 1_000_000_000

    def __merge_staging_directory_no_replace(
            self, src: str, dst: str, path_pair_id: Optional[str] = None) -> bool:
        """Publish missing descendants and retain every collision in staging.

        A split-root directory is expected after an interrupted portable
        publication.  Descendants are individually published through the
        existing no-clobber primitive; collided leaves are deliberately left
        at the staging pathname so a later scan/recovery cannot erase either
        authoritative final data or unresolved residue.
        """
        if not self.__safe_existing_directory(src) or not self.__safe_existing_directory(dst):
            raise OSError(errno.ELOOP, "directory merge encountered unsafe path")
        self.__reject_nested_mounts_or_reparse_points(src)
        self.__reject_nested_mounts_or_reparse_points(dst)
        settled_claim = self.__settle_collision_claim_for_tree(src, dst)
        if settled_claim == "pending":
            self.__collision_merge_deferred = True
        elif settled_claim == "retry":
            self.__collision_merge_deferred = True
            return False
        elif settled_claim == "failed":
            raise OSError(errno.EAGAIN, "collision claim could not be safely restored")
        active_claim = getattr(self, "_Controller__collision_compare_claim", None)
        active_claimed_path = active_claim[1] if active_claim is not None else None
        active_sidecar_path = active_claim[4] if active_claim is not None and len(active_claim) > 4 else None
        private_claim_artifacts, recovery_overflow = self.__recover_collision_claims(
            src, path_pair_id, active_claimed_path,
        )
        if recovery_overflow:
            # No descendant publication has started.  Retain the whole
            # directory rather than allowing a second scandir to expose an
            # exact-token artifact that was beyond the bounded recovery scan.
            return False
        if active_claimed_path is not None and self.__path_is_within(active_claimed_path, src):
            private_claim_artifacts.add(active_claimed_path)
            if active_sidecar_path is not None:
                private_claim_artifacts.add(active_sidecar_path)
        for entry in sorted(os.scandir(src), key=lambda item: item.name):
            source_child = self.__safe_merge_child(src, entry.name)
            destination_child = self.__safe_merge_child(dst, entry.name)
            if source_child is None or destination_child is None:
                raise OSError(errno.ELOOP, "directory merge child escapes root", entry.name)
            if source_child in private_claim_artifacts:
                continue
            active_claim = getattr(self, "_Controller__collision_compare_claim", None)
            if active_claim is not None and os.path.normcase(os.path.abspath(source_child)) == \
                    os.path.normcase(os.path.abspath(active_claim[1])):
                continue
            try:
                source_stat = os.lstat(source_child)
            except FileNotFoundError:
                # A source can be atomically claimed or externally replaced
                # after scandir; never act on the stale directory entry.
                continue
            try:
                destination_stat = os.lstat(destination_child)
            except FileNotFoundError:
                self.__publish_staging_no_replace(source_child, destination_child)
                continue
            if stat.S_ISDIR(source_stat.st_mode) and not stat.S_ISLNK(source_stat.st_mode) and \
                    stat.S_ISDIR(destination_stat.st_mode) and not stat.S_ISLNK(destination_stat.st_mode):
                self.__merge_staging_directory_no_replace(source_child, destination_child, path_pair_id)
                continue
            if self.__staging_collision_is_verified_equivalent(source_stat, destination_stat):
                cached_outcome = self.__cached_collision_outcome(source_child, destination_child)
                if cached_outcome is not None:
                    continue
                outcome = self.__claim_and_compare_collision_leaf(source_child, destination_child, path_pair_id)
                if outcome == "pending":
                    self.__collision_merge_deferred = True
                    continue
                raise OSError(errno.EAGAIN, "collision claim could not be scheduled")
            # Non-equivalent file/type collisions are retained in staging.
            # A retry may still publish unrelated missing descendants, but
            # this root remains an actionable no-overwrite conflict.
        try:
            os.rmdir(src)
        except OSError as error:
            if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
                return False
            raise
        self.__sync_directory_if_supported(os.path.dirname(src))
        self.__sync_directory_if_supported(dst)
        return True

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
        if self.__source_has_lftp_temp_artifact(staging_path, src, trace_file_id):
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
            if self.__safe_existing_directory(src) and self.__safe_existing_directory(dst):
                self.__collision_merge_deferred = False
                if not self.__merge_staging_directory_no_replace(src, dst, path_pair_id):
                    if self.__collision_merge_deferred:
                        return Controller.MoveFromStagingResult.DEFERRED
                    return Controller.MoveFromStagingResult.CONFLICT
            else:
                self.__publish_staging_no_replace(src, dst)
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
        except FileExistsError as error:
            self.logger.warning(
                "Refusing to overwrite existing final target for '%s': %s",
                name,
                error,
            )
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "destination_conflict",
                    "error": str(error),
                })
            return Controller.MoveFromStagingResult.CONFLICT
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

    @staticmethod
    def __rename_no_replace(src: str, dst: str) -> None:
        """Atomically publish src at dst without replacing an existing target."""
        if os.name == "nt":
            # Windows rename fails when dst exists.  Its operation is the
            # no-clobber primitive; any unsupported filesystem fails closed.
            os.rename(src, dst)
            return
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "destination already exists", dst)
        try:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
        except (AttributeError, OSError):
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(src), -100, os.fsencode(dst), 1) != 0:
            error_no = ctypes.get_errno()
            raise OSError(error_no, os.strerror(error_no), dst)

    @staticmethod
    def __is_no_replace_capability_error(error: OSError) -> bool:
        """Return whether a no-clobber primitive is unavailable, not failed."""
        unsupported_errors = {
            errno.EXDEV,
            errno.EINVAL,
            errno.ENOSYS,
            errno.ENOTSUP,
        }
        eopnotsupp = getattr(errno, "EOPNOTSUPP", None)
        if eopnotsupp is not None:
            unsupported_errors.add(eopnotsupp)
        return error.errno in unsupported_errors

    @staticmethod
    def __can_copy_instead_of_link(error: OSError) -> bool:
        """Whether a failed hardlink can safely fall back to exclusive copy."""
        fallback_errors = {
            errno.EXDEV,
            errno.EINVAL,
            errno.ENOSYS,
            errno.ENOTSUP,
            errno.EPERM,
            errno.EACCES,
            errno.EMLINK,
        }
        eopnotsupp = getattr(errno, "EOPNOTSUPP", None)
        if eopnotsupp is not None:
            fallback_errors.add(eopnotsupp)
        return error.errno in fallback_errors

    @staticmethod
    def __remove_published_source(
            src: str, expected_stat: os.stat_result, expected_snapshot: Optional[bytes],
            validate_before_delete: Optional[Callable[[str], None]] = None) -> None:
        """Remove a source only after its replacement has been published.

        Claim the source into an unguessable private sibling before deletion.
        A noncooperating process can still race the claim rename itself, but a
        mismatched claim is retained rather than deleted; no replacement at
        the public source pathname is removed by cleanup.
        """
        if not Controller.__same_path_identity(src, expected_stat):
            raise OSError(errno.EAGAIN, "staging source changed before deletion", src)
        source_parent = os.path.dirname(src)
        for _ in range(16):
            claimed_path = os.path.join(source_parent, ".seedsync-retire-" + secrets.token_hex(24))
            if not os.path.lexists(claimed_path):
                break
        else:
            raise OSError(errno.EEXIST, "could not reserve private source cleanup path", src)
        os.rename(src, claimed_path)
        try:
            # The public source name is gone after the claim rename.  Persist
            # the parent before validating or deleting so a crash cannot lose
            # both the public name and an unpersisted retained claim.
            Controller.__sync_directory_if_supported(source_parent)
            if not Controller.__same_path_identity(claimed_path, expected_stat) or \
                    (expected_snapshot is not None and
                     Controller.__source_tree_snapshot(claimed_path, ignore_root_ctime=True) != expected_snapshot):
                raise OSError(errno.EAGAIN, "staging source changed during cleanup claim", src)
            if validate_before_delete is not None:
                validate_before_delete(claimed_path)
            if os.path.isdir(claimed_path) and not os.path.islink(claimed_path):
                Controller.__reject_nested_mounts_or_reparse_points(claimed_path)
                shutil.rmtree(claimed_path)
            else:
                os.unlink(claimed_path)
        finally:
            # Also persist both retained mismatch claims and successful
            # deletions.  Capability-limited platforms keep their documented
            # best-effort behavior in __sync_directory_if_supported.
            Controller.__sync_directory_if_supported(source_parent)

    @staticmethod
    def __claimed_regular_file_matches_destination(
            claimed_source: str, expected_source_stat: os.stat_result,
            destination: str, expected_destination_stat: os.stat_result,
            cancel_event: Optional[Event] = None,
            compared_signatures: Optional[list[tuple[int, int, int, int, int, int]]] = None) -> bool:
        """Compare claimed and final regular leaves without trusting metadata.

        Both public leaves are opened without following symlinks where the
        platform supports it, and their full descriptor identities are checked
        before and after bounded byte reads.  A race or I/O error raises so no
        source leaf is consumed on uncertain evidence.
        """
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        source_fd: Optional[int] = None
        destination_fd: Optional[int] = None
        try:
            source_fd = os.open(claimed_source, flags)
            destination_fd = os.open(destination, flags)
            source_before = os.fstat(source_fd)
            destination_before = os.fstat(destination_fd)
            source_before_signature = Controller.__regular_collision_stat_signature(source_before)
            destination_before_signature = Controller.__regular_collision_stat_signature(destination_before)
            if not Controller.__collision_stat_signatures_match(
                    source_before_signature,
                    Controller.__regular_collision_stat_signature(expected_source_stat),
            ) or not Controller.__collision_stat_signatures_match(
                    destination_before_signature,
                    Controller.__regular_collision_stat_signature(expected_destination_stat),
            ):
                raise OSError(errno.EAGAIN, "collision leaf changed before content comparison")
            if compared_signatures is not None:
                compared_signatures.extend((source_before_signature, destination_before_signature))
            if source_before.st_size != destination_before.st_size:
                return False

            remaining = source_before.st_size
            while remaining:
                if cancel_event is not None and cancel_event.is_set():
                    raise OSError(errno.ECANCELED, "collision comparison cancelled")
                read_size = min(_COLLISION_COMPARE_CHUNK_BYTES, remaining)
                source_chunk = Controller.__read_descriptor_chunk(source_fd, read_size)
                destination_chunk = Controller.__read_descriptor_chunk(destination_fd, read_size)
                if len(source_chunk) != read_size or len(destination_chunk) != read_size:
                    raise OSError(errno.EIO, "collision leaf changed during content comparison")
                if source_chunk != destination_chunk:
                    return False
                remaining -= read_size

            source_after = os.fstat(source_fd)
            destination_after = os.fstat(destination_fd)
            if not Controller.__collision_stat_signatures_match(
                    Controller.__regular_collision_stat_signature(source_after), source_before_signature,
            ) or not Controller.__collision_stat_signatures_match(
                    Controller.__regular_collision_stat_signature(destination_after), destination_before_signature,
            ):
                raise OSError(errno.EAGAIN, "collision leaf changed during content comparison")
            return True
        finally:
            if destination_fd is not None:
                os.close(destination_fd)
            if source_fd is not None:
                os.close(source_fd)

    @staticmethod
    def __read_descriptor_chunk(file_descriptor: int, size: int) -> bytes:
        """Read exactly one bounded regular-file comparison chunk."""
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = os.read(file_descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def __regular_collision_stat_signature(candidate: os.stat_result) -> tuple[int, int, int, int, int, int]:
        if not stat.S_ISREG(candidate.st_mode):
            raise OSError(errno.EAGAIN, "collision leaf is no longer regular")
        return (
            candidate.st_dev, candidate.st_ino, candidate.st_size,
            candidate.st_mtime_ns, candidate.st_ctime_ns, candidate.st_mode,
        )

    @staticmethod
    def __collision_signature(path: str) -> tuple[int, int, int, int, int, int]:
        return Controller.__regular_collision_stat_signature(os.lstat(path))

    @staticmethod
    def __collision_stat_signatures_match(
            actual: tuple[int, int, int, int, int, int],
            expected: tuple[int, int, int, int, int, int]) -> bool:
        """Compare stable descriptor evidence on every supported platform.

        Windows changes the ctime reported through an open descriptor even for
        a read-only open.  The job still captures and keys full lstat
        signatures before and after the read; only the descriptor-local check
        omits that platform artifact.
        """
        if os.name == "nt":
            return actual[:4] + actual[5:] == expected[:4] + expected[5:]
        return actual == expected

    @staticmethod
    def __collision_cache_signature(
            signature: tuple[int, int, int, int, int, int]) -> tuple[int, int, int, int, int, int]:
        """Keep the complete restored source identity in terminal cache keys."""
        return signature

    @staticmethod
    def __compare_collision_leaf_job(
            source: str, destination: str,
            source_signature: tuple[int, int, int, int, int, int],
            destination_signature: tuple[int, int, int, int, int, int],
            cancel_event: Event) -> tuple[str, tuple[int, int, int, int, int, int],
                                         tuple[int, int, int, int, int, int]]:
        if source_signature[2] > _COLLISION_COMPARE_MAX_BYTES:
            return "over_budget", source_signature, destination_signature
        try:
            source_stat = os.lstat(source)
            destination_stat = os.lstat(destination)
            if Controller.__regular_collision_stat_signature(source_stat) != source_signature or \
                    Controller.__regular_collision_stat_signature(destination_stat) != destination_signature:
                return "changed", source_signature, destination_signature
            compared_signatures: list[tuple[int, int, int, int, int, int]] = []
            outcome = "equal" if Controller.__claimed_regular_file_matches_destination(
                source, source_stat, destination, destination_stat, cancel_event, compared_signatures,
            ) else "mismatch"
            if len(compared_signatures) != 2:
                return "error", source_signature, destination_signature
            compared_source_signature, compared_destination_signature = compared_signatures
            if outcome == "equal" and (
                    not Controller.__collision_stat_signatures_match(
                        Controller.__collision_signature(source), compared_source_signature,
                    ) or not Controller.__collision_stat_signatures_match(
                        Controller.__collision_signature(destination), compared_destination_signature,
                    )
            ):
                return "changed", compared_source_signature, compared_destination_signature
            return outcome, compared_source_signature, compared_destination_signature
        except OSError:
            try:
                return "error", Controller.__collision_signature(source), Controller.__collision_signature(destination)
            except OSError:
                return "error", source_signature, destination_signature

    def __collision_cache_key(
            self, original_source: str, destination: str,
            source_signature: tuple[int, int, int, int, int, int],
            destination_signature: tuple[int, int, int, int, int, int]) -> tuple[str, str, tuple[int, int, int, int, int, int],
                                                                                tuple[int, int, int, int, int, int]]:
        return (
            original_source,
            destination,
            self.__collision_cache_signature(source_signature),
            destination_signature,
        )

    @staticmethod
    def __path_is_within(path: str, root: str) -> bool:
        try:
            return os.path.normcase(os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])) == \
                os.path.normcase(os.path.abspath(root))
        except ValueError:
            return False

    def __cached_collision_outcome(self, source: str, destination: str) -> Optional[str]:
        try:
            key = self.__collision_cache_key(
                source, destination, self.__collision_signature(source), self.__collision_signature(destination),
            )
        except OSError:
            return None
        if getattr(self, "_Controller__collision_compare_key", None) == key:
            return getattr(self, "_Controller__collision_compare_result", None)
        return None

    @staticmethod
    def __collision_claim_sidecar_path(claimed_path: str) -> str:
        return claimed_path + ".json"

    @staticmethod
    def __collision_claim_basename_is_valid(value: object) -> bool:
        if not isinstance(value, str) or not value or len(value) > 255 or "\x00" in value:
            return False
        return value not in (".", "..") and "/" not in value and "\\" not in value

    @staticmethod
    def __collision_claim_owner_is_valid(value: object) -> bool:
        return value is None or (isinstance(value, str) and 0 < len(value) <= 512 and "\x00" not in value)

    @classmethod
    def __read_collision_claim_sidecar(cls, sidecar_path: str) -> Optional[tuple[str, Optional[str]]]:
        try:
            sidecar_stat = os.lstat(sidecar_path)
            if not stat.S_ISREG(sidecar_stat.st_mode) or sidecar_stat.st_size > _COLLISION_CLAIM_SIDECAR_MAX_BYTES:
                return None
            with open(sidecar_path, "rb") as handle:
                payload = handle.read(_COLLISION_CLAIM_SIDECAR_MAX_BYTES + 1)
            if len(payload) > _COLLISION_CLAIM_SIDECAR_MAX_BYTES:
                return None
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict) or set(data) != {"version", "original_basename", "path_pair_id"} or \
                    data["version"] != 1 or not cls.__collision_claim_basename_is_valid(data["original_basename"]) or \
                    not cls.__collision_claim_owner_is_valid(data["path_pair_id"]):
                return None
            return data["original_basename"], data["path_pair_id"]
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            return None

    @classmethod
    def __write_collision_claim_sidecar(
            cls, sidecar_path: str, original_basename: str, path_pair_id: Optional[str]) -> None:
        if not cls.__collision_claim_basename_is_valid(original_basename) or \
                not cls.__collision_claim_owner_is_valid(path_pair_id):
            raise OSError(errno.EINVAL, "invalid collision claim ownership")
        payload = json.dumps(
            {"version": 1, "original_basename": original_basename, "path_pair_id": path_pair_id},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
        if len(payload) > _COLLISION_CLAIM_SIDECAR_MAX_BYTES:
            raise OSError(errno.E2BIG, "collision claim ownership metadata is too large")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(sidecar_path, flags, 0o600)
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError(errno.EIO, "failed to write collision claim ownership metadata")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        cls.__sync_directory_if_supported(os.path.dirname(sidecar_path))

    @classmethod
    def __remove_collision_claim_sidecar(cls, sidecar_path: Optional[str]) -> None:
        if sidecar_path is None:
            return
        try:
            os.unlink(sidecar_path)
        except FileNotFoundError:
            return
        cls.__sync_directory_if_supported(os.path.dirname(sidecar_path))

    def __recover_collision_claims(
            self, source_directory: str, path_pair_id: Optional[str],
            active_claimed_path: Optional[str] = None) -> tuple[set[str], bool]:
        """Recover only complete, owned private claims before merge traversal.

        A claim name alone is deliberately never enough to infer a public name.
        Invalid, foreign, or unpaired metadata is retained and excluded from
        publication; exact-token sidecars are reserved private artifacts too.
        """
        artifacts: set[str] = set()
        claims: dict[str, str] = {}
        sidecars: dict[str, str] = {}
        for entry in sorted(os.scandir(source_directory), key=lambda item: item.name):
            claim_match = re.fullmatch(r"\.seedsync-retire-([0-9a-f]{48})", entry.name)
            sidecar_match = re.fullmatch(r"\.seedsync-retire-([0-9a-f]{48})\.json", entry.name)
            if claim_match:
                claims[claim_match.group(1)] = entry.path
            elif sidecar_match:
                sidecars[sidecar_match.group(1)] = entry.path
            if len(claims) + len(sidecars) > _COLLISION_CLAIM_SCAN_LIMIT:
                self.logger.warning("Too many private collision claim artifacts in '%s'; retaining them", source_directory)
                return artifacts, True
        for token in sorted(set(claims) | set(sidecars)):
            claim = claims.get(token)
            sidecar = sidecars.get(token)
            if claim is not None and claim == active_claimed_path:
                artifacts.add(claim)
                if sidecar:
                    artifacts.add(sidecar)
                continue
            metadata = self.__read_collision_claim_sidecar(sidecar) if sidecar else None
            if claim is None:
                self.logger.warning("Retaining unpaired private collision sidecar '%s'", sidecar)
                artifacts.add(cast(str, sidecar))
                continue
            try:
                if not stat.S_ISREG(os.lstat(claim).st_mode):
                    raise OSError(errno.EINVAL, "claim is not a regular file")
            except OSError:
                self.logger.warning("Retaining unsafe private collision claim '%s'", claim)
                artifacts.add(claim)
                if sidecar:
                    artifacts.add(sidecar)
                continue
            if metadata is None:
                self.logger.warning("Retaining private collision claim '%s' without valid ownership sidecar", claim)
                artifacts.add(claim)
                if sidecar:
                    artifacts.add(sidecar)
                continue
            original_basename, owner = metadata
            original = self.__safe_merge_child(source_directory, original_basename)
            if original is None or owner != path_pair_id:
                self.logger.warning("Retaining private collision claim '%s' with foreign or unsafe ownership", claim)
                artifacts.update((claim, cast(str, sidecar)))
                continue
            if os.path.lexists(original):
                self.logger.warning("Retaining private collision claim '%s': original '%s' is occupied", claim, original)
                artifacts.update((claim, cast(str, sidecar)))
                continue
            try:
                self.__rename_no_replace(claim, original)
                self.__sync_directory_if_supported(source_directory)
                self.__remove_collision_claim_sidecar(sidecar)
            except OSError as error:
                self.logger.warning("Retaining private collision claim '%s': restore failed: %s", claim, error)
                artifacts.update((claim, cast(str, sidecar)))
        return artifacts, False

    def __claim_collision_source(self, source: str) -> str:
        source_parent = os.path.dirname(source)
        for _ in range(16):
            claimed_path = os.path.join(source_parent, ".seedsync-retire-" + secrets.token_hex(24))
            sidecar_path = self.__collision_claim_sidecar_path(claimed_path)
            if not os.path.lexists(claimed_path) and not os.path.lexists(sidecar_path):
                break
        else:
            raise OSError(errno.EEXIST, "could not reserve private collision claim", source)
        self.__write_collision_claim_sidecar(
            sidecar_path, os.path.basename(source), getattr(self, "_Controller__collision_claim_path_pair_id", None),
        )
        try:
            self.__rename_no_replace(source, claimed_path)
            self.__sync_directory_if_supported(source_parent)
        except Exception:
            self.__remove_collision_claim_sidecar(sidecar_path)
            raise
        return claimed_path

    def __claim_and_compare_collision_leaf(
            self, source: str, destination: str, path_pair_id: Optional[str] = None) -> str:
        if not hasattr(self, "_Controller__collision_compare_lock"):
            self.__collision_compare_lock = Lock()
            self.__collision_compare_executor = None
            self.__collision_compare_future = None
            self.__collision_compare_key = None
            self.__collision_compare_result = None
            self.__collision_compare_claim = None
            self.__collision_compare_cancel_event = None
        with self.__collision_compare_lock:
            if self.__collision_compare_future is not None or self.__collision_compare_claim is not None:
                return "pending"
            self.__collision_claim_path_pair_id = path_pair_id
            try:
                claimed_source = self.__claim_collision_source(source)
            finally:
                self.__collision_claim_path_pair_id = None
            sidecar_path = self.__collision_claim_sidecar_path(claimed_source)
            try:
                source_signature = self.__collision_signature(claimed_source)
                destination_signature = self.__collision_signature(destination)
            except OSError:
                try:
                    self.__rename_no_replace(claimed_source, source)
                    self.__sync_directory_if_supported(os.path.dirname(source))
                    self.__remove_collision_claim_sidecar(sidecar_path)
                except OSError as restore_error:
                    self.logger.error(
                        "Retained collision claim '%s' for '%s' versus '%s': %s",
                        claimed_source, source, destination, restore_error,
                    )
                raise
            key = self.__collision_cache_key(source, destination, source_signature, destination_signature)
            self.__collision_compare_claim = (source, claimed_source, destination, key, sidecar_path, path_pair_id)
            self.__collision_compare_key = None
            self.__collision_compare_result = None
            self.__collision_compare_cancel_event = Event()
            cancel_event = self.__collision_compare_cancel_event
            compare_epoch = getattr(self, "_Controller__collision_compare_epoch", 0)
            if self.__collision_compare_executor is None:
                self.__collision_compare_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seedsync-collision")
            self.__collision_compare_future = self.__collision_compare_executor.submit(
                Controller.__compare_collision_leaf_job,
                claimed_source, destination, source_signature, destination_signature,
                cancel_event,
            )
            self.__collision_compare_future.add_done_callback(
                lambda completed_future: self.__restore_cancelled_collision_claim(
                    completed_future,
                    source,
                    claimed_source,
                    destination,
                    cancel_event,
                    compare_epoch,
                    sidecar_path,
                )
            )
            return "pending"

    def __restore_cancelled_collision_claim(
            self, completed_future: Optional[Future], original_source: str, claimed_source: str,
            destination: str, cancel_event: Event, compare_epoch: int,
            sidecar_path: Optional[str] = None) -> None:
        """Return a cancelled private claim without blocking controller exit."""
        if not cancel_event.is_set():
            return
        if not hasattr(self, "_Controller__collision_compare_lock"):
            self.__collision_compare_lock = Lock()
        with self.__collision_compare_lock:
            owns_current_generation = getattr(self, "_Controller__collision_compare_epoch", 0) == compare_epoch
            if not owns_current_generation:
                # A stale worker has no authority over a remapped generation.
                # Keep its durable pair private for owner-aware merge recovery.
                return
            if completed_future is not None:
                try:
                    completed_future.result()
                except Exception:
                    pass
            try:
                self.__rename_no_replace(claimed_source, original_source)
                self.__sync_directory_if_supported(os.path.dirname(original_source))
                self.__remove_collision_claim_sidecar(sidecar_path)
                current_claim = getattr(self, "_Controller__collision_compare_claim", None)
                if current_claim is not None and current_claim[1] == claimed_source:
                    self.__collision_compare_future = None
                    self.__collision_compare_claim = None
                    self.__collision_compare_key = None
                    self.__collision_compare_result = None
                    self.__collision_compare_cancel_event = None
            except OSError as restore_error:
                if owns_current_generation and os.path.lexists(original_source) and not os.path.lexists(claimed_source):
                    self.__collision_compare_future = None
                    self.__collision_compare_claim = None
                    self.__collision_compare_key = None
                    self.__collision_compare_result = None
                    self.__collision_compare_cancel_event = None
                    return
                self.logger.error(
                    "Retained cancelled collision claim '%s' for '%s' versus '%s': %s",
                    claimed_source, original_source, destination, restore_error,
                )

    def __settle_collision_claim_for_tree(self, source_root: str, destination_root: str) -> Optional[str]:
        claim = getattr(self, "_Controller__collision_compare_claim", None)
        if claim is None:
            return None
        original_source, claimed_source, destination, _ = claim[:4]
        sidecar_path = claim[4] if len(claim) > 4 else None
        if not self.__path_is_within(original_source, source_root) or \
                not self.__path_is_within(destination, destination_root):
            return None
        with self.__collision_compare_lock:
            future = self.__collision_compare_future
            if future is not None and not future.done():
                return "pending"
            try:
                outcome, source_signature, destination_signature = future.result() if future is not None else (
                    "error", self.__collision_signature(claimed_source), self.__collision_signature(destination),
                )
            except Exception:
                outcome, source_signature, destination_signature = "error", None, None
            self.__collision_compare_future = None
            if outcome == "equal" and source_signature is not None and destination_signature is not None:
                try:
                    if not self.__collision_stat_signatures_match(
                            self.__collision_signature(claimed_source), source_signature,
                    ) or not self.__collision_stat_signatures_match(
                            self.__collision_signature(destination), destination_signature,
                    ):
                        outcome = "changed"
                    else:
                        os.unlink(claimed_source)
                        self.__sync_directory_if_supported(os.path.dirname(claimed_source))
                        self.__remove_collision_claim_sidecar(sidecar_path)
                        self.__collision_compare_claim = None
                        self.__collision_compare_key = None
                        self.__collision_compare_result = None
                        return "equal"
                except OSError:
                    outcome = "changed"
            try:
                self.__rename_no_replace(claimed_source, original_source)
                self.__sync_directory_if_supported(os.path.dirname(original_source))
                self.__remove_collision_claim_sidecar(sidecar_path)
                restored_signature = self.__collision_signature(original_source)
                current_destination_signature = self.__collision_signature(destination)
                if outcome in ("mismatch", "error", "over_budget"):
                    self.__collision_compare_key = self.__collision_cache_key(
                        original_source, destination, restored_signature, current_destination_signature,
                    )
                    self.__collision_compare_result = outcome
                else:
                    self.__collision_compare_key = None
                    self.__collision_compare_result = None
                self.__collision_compare_claim = None
                return "terminal" if outcome in ("mismatch", "error", "over_budget") else "retry"
            except OSError as restore_error:
                self.logger.error(
                    "Retained collision claim '%s' for '%s' versus '%s': %s",
                    claimed_source, original_source, destination, restore_error,
                )
                self.__collision_compare_claim = None
                self.__collision_compare_key = None
                self.__collision_compare_result = None
                return "failed"

    def __shutdown_collision_compare_worker(self) -> None:
        if not hasattr(self, "_Controller__collision_compare_lock"):
            self.__collision_compare_lock = Lock()
        with self.__collision_compare_lock:
            executor = getattr(self, "_Controller__collision_compare_executor", None)
            cancel_event = getattr(self, "_Controller__collision_compare_cancel_event", None)
            claim = getattr(self, "_Controller__collision_compare_claim", None)
            future = getattr(self, "_Controller__collision_compare_future", None)
            if cancel_event is not None:
                cancel_event.set()
            # Do not clear a running claim here.  Its callback holds the same
            # lock and either restores under this generation (ordinary exit)
            # or is fenced by reset's subsequent epoch change.
            settle_now = claim is not None and (future is None or future.done())
        if settle_now:
            original_source, claimed_source, destination, _ = claim[:4]
            sidecar_path = claim[4] if len(claim) > 4 else None
            restore_event = cancel_event if cancel_event is not None else Event()
            restore_event.set()
            self.__restore_cancelled_collision_claim(
                future,
                original_source, claimed_source, destination,
                restore_event,
                getattr(self, "_Controller__collision_compare_epoch", 0), sidecar_path,
            )
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        with self.__collision_compare_lock:
            self.__collision_compare_executor = None
            if claim is None or settle_now:
                self.__collision_compare_future = None
                self.__collision_compare_key = None
                self.__collision_compare_result = None
                self.__collision_compare_claim = None
                self.__collision_compare_cancel_event = None

    def _has_active_collision_comparison(self, name: str, path_pair_id: Optional[str] = None) -> bool:
        claim = getattr(self, "_Controller__collision_compare_claim", None)
        if claim is None:
            return False
        resolved = self.__resolve_safe_final_move_paths(name, path_pair_id)
        return resolved is not None and self.__path_is_within(claim[0], resolved[2])

    has_active_collision_compare = _has_active_collision_comparison

    def __cancel_and_settle_collision_claim_for_refresh(self) -> bool:
        """Restore any active claim before path-pair roots can be remapped."""
        claim = getattr(self, "_Controller__collision_compare_claim", None)
        if claim is None:
            return True
        original_source, claimed_source, destination, _ = claim[:4]
        sidecar_path = claim[4] if len(claim) > 4 else None
        cancel_event = getattr(self, "_Controller__collision_compare_cancel_event", None)
        future = getattr(self, "_Controller__collision_compare_future", None)
        if cancel_event is not None:
            cancel_event.set()
        if future is not None:
            try:
                future.result(timeout=5)
            except TimeoutError:
                self.logger.error(
                    "Deferring path-pair refresh while collision claim '%s' for '%s' is still active",
                    claimed_source, original_source,
                )
                return False
            except Exception:
                pass
        with self.__collision_compare_lock:
            try:
                if os.path.lexists(claimed_source):
                    self.__rename_no_replace(claimed_source, original_source)
                    self.__sync_directory_if_supported(os.path.dirname(original_source))
                if os.path.lexists(original_source) and not os.path.lexists(claimed_source):
                    self.__remove_collision_claim_sidecar(sidecar_path)
            except OSError as restore_error:
                if os.path.lexists(original_source) and not os.path.lexists(claimed_source):
                    self.__collision_compare_future = None
                    self.__collision_compare_claim = None
                    self.__collision_compare_key = None
                    self.__collision_compare_result = None
                    self.__collision_compare_cancel_event = None
                    return True
                self.logger.error(
                    "Deferring path-pair refresh; retained collision claim '%s' for '%s' versus '%s': %s",
                    claimed_source, original_source, destination, restore_error,
                )
                return False
            self.__collision_compare_future = None
            self.__collision_compare_claim = None
            self.__collision_compare_key = None
            self.__collision_compare_result = None
            self.__collision_compare_cancel_event = None
            return True

    @staticmethod
    def __same_path_identity(path: str, expected: os.stat_result) -> bool:
        """Check that a published path still names the object we created."""
        try:
            return os.path.samestat(os.lstat(path), expected)
        except FileNotFoundError:
            return False

    @staticmethod
    def __source_tree_snapshot(path: str, ignore_root_ctime: bool = False) -> bytes:
        """Return stable, no-follow source evidence before source deletion."""
        digest = hashlib.sha256()

        def visit(candidate: str, relative: str) -> None:
            candidate_stat = os.lstat(candidate)
            digest.update(os.fsencode(relative))
            digest.update(repr((
                candidate_stat.st_mode, candidate_stat.st_dev,
                candidate_stat.st_ino, candidate_stat.st_size,
                candidate_stat.st_mtime_ns,
                None if ignore_root_ctime and relative == "." else candidate_stat.st_ctime_ns,
            )).encode("ascii"))
            if stat.S_ISLNK(candidate_stat.st_mode):
                digest.update(os.fsencode(os.readlink(candidate)))
                return
            if stat.S_ISDIR(candidate_stat.st_mode):
                with os.scandir(candidate) as entries:
                    for entry in sorted(entries, key=lambda item: item.name):
                        visit(entry.path, os.path.join(relative, entry.name))

        visit(path, ".")
        return digest.digest()

    @staticmethod
    def __publication_tree_manifest(path: str) -> Tuple[Tuple[object, ...], ...]:
        """Describe a published tree without following links or using inode ids."""
        entries: List[Tuple[object, ...]] = []

        def visit(candidate: str, relative: str) -> None:
            candidate_stat = os.lstat(candidate)
            kind = stat.S_IFMT(candidate_stat.st_mode)
            mode = stat.S_IMODE(candidate_stat.st_mode)
            if os.name == "nt":
                # Python 3.11 cannot apply file or directory metadata through
                # an owned descriptor on Windows.  Keep restrictive creation
                # modes instead of racing a public pathname, and exclude only
                # those unsupported permission bits from the proof.
                mode = -1
            link_target = os.readlink(candidate) if stat.S_ISLNK(candidate_stat.st_mode) else None
            size = -1 if stat.S_ISDIR(candidate_stat.st_mode) else candidate_stat.st_size
            mtime = -1 if stat.S_ISLNK(candidate_stat.st_mode) else candidate_stat.st_mtime_ns
            if os.name == "nt":
                # Descriptor-based timestamp restoration is unavailable with
                # the supported Python version for Windows as well.
                mtime = -1
            entries.append((relative, kind, mode, size,
                            mtime, link_target))
            if stat.S_ISDIR(candidate_stat.st_mode) and not stat.S_ISLNK(candidate_stat.st_mode):
                with os.scandir(candidate) as children:
                    for child in sorted(children, key=lambda item: item.name):
                        visit(child.path, os.path.join(relative, child.name))

        visit(path, ".")
        return tuple(entries)

    @staticmethod
    def __reject_nested_mounts_or_reparse_points(path: str) -> None:
        """Fail closed before recursively copying or deleting a directory."""
        root_stat = os.lstat(path)
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        root_attributes = getattr(root_stat, "st_file_attributes", 0)
        if reparse_point and root_attributes & reparse_point:
            raise OSError(errno.EXDEV, "reparse point staging root", path)
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            return
        root_path = os.path.normcase(os.path.abspath(path))
        if sys.platform.startswith("linux"):
            try:
                mount_points = Controller.__linux_mountinfo_mountpoints()
            except (OSError, ValueError) as error:
                raise OSError(getattr(error, "errno", None) or errno.EIO,
                              "could not inspect Linux mount boundaries", path) from error
            for mount_point in mount_points:
                normalized_mount = os.path.normcase(os.path.abspath(mount_point))
                try:
                    nested = os.path.commonpath([root_path, normalized_mount]) == root_path
                except ValueError:
                    nested = False
                if nested and normalized_mount != root_path:
                    raise OSError(errno.EXDEV, "nested Linux mount in staging tree", mount_point)

        def visit(candidate: str) -> None:
            with os.scandir(candidate) as entries:
                for entry in entries:
                    child_stat = os.lstat(entry.path)
                    attributes = getattr(child_stat, "st_file_attributes", 0)
                    if reparse_point and attributes & reparse_point:
                        raise OSError(errno.EXDEV, "reparse point in staging tree", entry.path)
                    if stat.S_ISDIR(child_stat.st_mode) and not stat.S_ISLNK(child_stat.st_mode):
                        if child_stat.st_dev != root_stat.st_dev or os.path.ismount(entry.path):
                            raise OSError(errno.EXDEV, "nested mount in staging tree", entry.path)
                        visit(entry.path)

        visit(path)

    @staticmethod
    def __linux_mountinfo_mountpoints() -> List[str]:
        """Read Linux mount points, preserving kernel mountinfo path escapes."""
        escape_values = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}

        def decode_mountinfo_path(value: str) -> str:
            return re.sub(r"\\(040|011|012|134)", lambda match: escape_values[match.group(1)], value)

        mount_points = []
        with open("/proc/self/mountinfo", encoding="utf-8") as mountinfo:
            for line in mountinfo:
                fields = line.rstrip("\n").split(" ")
                if len(fields) < 6:
                    raise ValueError("malformed /proc/self/mountinfo entry")
                mount_points.append(decode_mountinfo_path(fields[4]))
        return mount_points

    @staticmethod
    def __sync_publish_temporary(path: str) -> None:
        """Flush copied regular files before their names become final targets."""
        def sync_file(file_path: str) -> None:
            # Windows requires a writable descriptor for fsync even though the
            # copied temporary file is not modified here.  Private temporary
            # files are synced before their source mode is copied onto them.
            flags = os.O_RDWR if os.name == "nt" else os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(file_path, flags)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

        path_stat = os.lstat(path)
        if stat.S_ISREG(path_stat.st_mode):
            sync_file(path)
            return
        if not stat.S_ISDIR(path_stat.st_mode):
            return
        for root, _directories, file_names in os.walk(path):
            for file_name in file_names:
                candidate = os.path.join(root, file_name)
                if stat.S_ISREG(os.lstat(candidate).st_mode):
                    sync_file(candidate)

    @staticmethod
    def __sync_directory_if_supported(path: str) -> None:
        """Flush a directory entry where the platform exposes directory fsync."""
        if os.name == "nt":
            return
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(path, flags)
        except OSError as error:
            if Controller.__is_no_replace_capability_error(error):
                return
            raise
        try:
            try:
                os.fsync(fd)
            except OSError as error:
                if not Controller.__is_no_replace_capability_error(error):
                    raise
        finally:
            os.close(fd)

    @classmethod
    def __copy_to_publish_temporary(cls, src: str, destination_parent: str) -> Tuple[str, Optional[str]]:
        """Copy src to a private destination-side path without following links."""
        source_stat = os.lstat(src)
        if stat.S_ISDIR(source_stat.st_mode):
            cls.__reject_nested_mounts_or_reparse_points(src)
            temporary_root = tempfile.mkdtemp(prefix=".seedsync-publish-", dir=destination_parent)
            temporary_path = os.path.join(temporary_root, "payload")
            try:
                shutil.copytree(src, temporary_path, symlinks=True, copy_function=cls.__copy_regular_file_to_temporary)
            except BaseException:
                shutil.rmtree(temporary_root, ignore_errors=True)
                raise
            return temporary_path, temporary_root

        fd, temporary_path = tempfile.mkstemp(prefix=".seedsync-publish-", dir=destination_parent)
        os.close(fd)
        try:
            if stat.S_ISLNK(source_stat.st_mode):
                os.unlink(temporary_path)
                os.symlink(os.readlink(src), temporary_path)
            elif stat.S_ISREG(source_stat.st_mode):
                cls.__copy_regular_file_to_temporary(src, temporary_path)
            else:
                raise OSError(errno.ENOTSUP, "unsupported staging source type", src)
        except BaseException:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise
        return temporary_path, None

    @classmethod
    def __copy_regular_file_to_temporary(cls, src: str, dst: str) -> str:
        """Copy and flush private content before source permissions are copied."""
        shutil.copyfile(src, dst, follow_symlinks=False)
        cls.__sync_publish_temporary(dst)
        shutil.copystat(src, dst, follow_symlinks=False)
        return dst

    @staticmethod
    def __apply_owned_metadata(file_descriptor: int, source_stat: os.stat_result) -> None:
        """Apply metadata only through an owned descriptor, never a public path."""
        if os.name == "nt" or not hasattr(os, "fchmod"):
            return
        os.fchmod(file_descriptor, stat.S_IMODE(source_stat.st_mode))
        try:
            os.utime(file_descriptor, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        except (AttributeError, NotImplementedError, TypeError, ValueError):
            # Some platforms do not expose fd-based utime.  Keep restrictive
            # creation metadata rather than race a public pathname.
            pass

    @classmethod
    def __publish_regular_file_exclusively(cls, temporary_path: str, dst: str) -> None:
        """Copy a temporary regular file with O_EXCL and verify its identity."""
        expected_manifest = cls.__publication_tree_manifest(temporary_path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(dst, flags, 0o600)
        published_stat = os.fstat(fd)
        try:
            with open(temporary_path, "rb") as source, os.fdopen(fd, "wb", closefd=False) as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            cls.__apply_owned_metadata(fd, os.lstat(temporary_path))
            if not cls.__same_path_identity(dst, published_stat):
                raise OSError(errno.EAGAIN, "final target changed during publication", dst)
            cls.__sync_directory_if_supported(os.path.dirname(dst))
            if not cls.__same_path_identity(dst, published_stat):
                raise OSError(errno.EAGAIN, "final target changed during publication", dst)
            if cls.__publication_tree_manifest(dst) != expected_manifest:
                raise OSError(errno.EAGAIN, "final target changed during publication", dst)
        except BaseException:
            # Retain a partial destination rather than unlinking a pathname
            # that an external process could have replaced after our check.
            raise
        finally:
            os.close(fd)

    @classmethod
    def __publish_temporary_directory(cls, temporary_path: str, dst: str) -> None:
        """Materialize a private directory through exclusive entries.

        No portable system call atomically renames a directory without replacing
        an existing destination.  Reserving the final directory with mkdir is
        no-clobber; if publication later fails the complete source is retained.
        The reserved partial directory is deliberately not removed because a
        concurrent writer could have added entries that do not belong to us.
        """
        expected_manifest = cls.__publication_tree_manifest(temporary_path)
        temporary_stat = os.lstat(temporary_path)
        os.mkdir(dst, 0o700)
        published_stat = os.lstat(dst)
        directory_fd: Optional[int] = None
        try:
            if os.name != "nt" and hasattr(os, "fchmod"):
                directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                directory_fd = os.open(dst, directory_flags)
                if not os.path.samestat(os.fstat(directory_fd), published_stat):
                    raise OSError(errno.EAGAIN, "final directory changed during publication", dst)
            with os.scandir(temporary_path) as entries:
                for entry in entries:
                    destination_child = os.path.join(dst, entry.name)
                    try:
                        cls.__rename_no_replace(entry.path, destination_child)
                    except OSError as error:
                        if not cls.__is_no_replace_capability_error(error):
                            raise
                        cls.__publish_temporary_no_replace(entry.path, destination_child)
                    if not cls.__same_path_identity(dst, published_stat):
                        raise OSError(errno.EAGAIN, "final directory changed during publication", dst)
            if directory_fd is not None:
                cls.__apply_owned_metadata(directory_fd, temporary_stat)
            if not cls.__same_path_identity(dst, published_stat):
                raise OSError(errno.EAGAIN, "final directory changed during publication", dst)
            cls.__sync_directory_if_supported(dst)
            cls.__sync_directory_if_supported(os.path.dirname(dst))
            if not cls.__same_path_identity(dst, published_stat):
                raise OSError(errno.EAGAIN, "final directory changed during publication", dst)
            if cls.__publication_tree_manifest(dst) != expected_manifest:
                raise OSError(errno.EAGAIN, "final directory tree changed during publication", dst)
            os.rmdir(temporary_path)
        except BaseException:
            raise
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

    @classmethod
    def __publish_temporary_no_replace(cls, temporary_path: str, dst: str) -> None:
        """Publish a destination-side temporary object without clobbering dst."""
        temporary_stat = os.lstat(temporary_path)
        expected_manifest = cls.__publication_tree_manifest(temporary_path)
        if stat.S_ISDIR(temporary_stat.st_mode):
            cls.__publish_temporary_directory(temporary_path, dst)
            return
        if stat.S_ISLNK(temporary_stat.st_mode):
            link_target = os.readlink(temporary_path)
            os.symlink(link_target, dst)
            published_stat = os.lstat(dst)
            try:
                if not stat.S_ISLNK(published_stat.st_mode) or os.readlink(dst) != link_target:
                    raise OSError(errno.EAGAIN, "final symlink changed during publication", dst)
                cls.__sync_directory_if_supported(os.path.dirname(dst))
                if not cls.__same_path_identity(dst, published_stat):
                    raise OSError(errno.EAGAIN, "final symlink changed during publication", dst)
                if cls.__publication_tree_manifest(dst) != expected_manifest:
                    raise OSError(errno.EAGAIN, "final symlink changed during publication", dst)
            except BaseException:
                # Retain the destination on failure for the same reason as
                # regular-file fallback: cleanup must not delete a racer.
                raise
            os.unlink(temporary_path)
            return
        if not stat.S_ISREG(temporary_stat.st_mode):
            raise OSError(errno.ENOTSUP, "unsupported temporary publication type", temporary_path)
        try:
            os.link(temporary_path, dst, follow_symlinks=False)
            if not cls.__same_path_identity(dst, temporary_stat):
                raise OSError(errno.EAGAIN, "final target changed during publication", dst)
            cls.__sync_directory_if_supported(os.path.dirname(dst))
            if not cls.__same_path_identity(dst, temporary_stat):
                raise OSError(errno.EAGAIN, "final target changed during publication", dst)
            if cls.__publication_tree_manifest(dst) != expected_manifest:
                raise OSError(errno.EAGAIN, "final target changed during publication", dst)
        except OSError as error:
            if not cls.__can_copy_instead_of_link(error):
                raise
            cls.__publish_regular_file_exclusively(temporary_path, dst)
        os.unlink(temporary_path)

    @classmethod
    def __publish_staging_no_replace(cls, src: str, dst: str) -> None:
        """Publish staging content once; preserve src on collision or failure.

        Native no-replace rename remains the fast path.  Filesystems such as
        Unraid shfs can reject renameat2 flags with EINVAL; in that case a
        complete private copy is made in the destination directory and then
        published with exclusive primitives.  Permission, capacity, read-only,
        and I/O errors are intentionally not treated as capability fallbacks.
        """
        source_manifest = cls.__publication_tree_manifest(src)
        try:
            cls.__rename_no_replace(src, dst)
            if os.path.lexists(src) or not os.path.lexists(dst):
                raise OSError(errno.EIO, "native publication did not reach the expected final state", dst)
            published_stat = os.lstat(dst)
            cls.__sync_directory_if_supported(os.path.dirname(dst))
            cls.__sync_directory_if_supported(os.path.dirname(src))
            # A process which replaces dst after this check is outside this
            # pathname-only publication contract; it cannot be prevented once
            # this call has returned to an uncooperative external writer.
            if not cls.__same_path_identity(dst, published_stat):
                raise OSError(errno.EAGAIN, "final target changed during native publication", dst)
            if cls.__publication_tree_manifest(dst) != source_manifest:
                raise OSError(errno.EAGAIN, "final target changed during native publication", dst)
            return
        except OSError as error:
            if not cls.__is_no_replace_capability_error(error):
                raise

        destination_parent = os.path.dirname(dst)
        source_stat = os.lstat(src)
        source_snapshot = cls.__source_tree_snapshot(src)
        source_claim_snapshot = cls.__source_tree_snapshot(src, ignore_root_ctime=True)
        temporary_path: Optional[str] = None
        temporary_root: Optional[str] = None
        publication_error = False
        try:
            temporary_path, temporary_root = cls.__copy_to_publish_temporary(src, destination_parent)
            temporary_manifest = cls.__publication_tree_manifest(temporary_path)
            try:
                cls.__rename_no_replace(temporary_path, dst)
                if not os.path.lexists(dst) or os.path.lexists(temporary_path):
                    raise OSError(errno.EIO, "temporary publication did not reach the expected final state", dst)
                published_stat = os.lstat(dst)
                cls.__sync_directory_if_supported(os.path.dirname(dst))
                if not cls.__same_path_identity(dst, published_stat):
                    raise OSError(errno.EAGAIN, "final target changed during native publication", dst)
                if cls.__publication_tree_manifest(dst) != temporary_manifest:
                    raise OSError(errno.EAGAIN, "final target changed during native publication", dst)
            except OSError as error:
                if not cls.__is_no_replace_capability_error(error):
                    raise
                cls.__publish_temporary_no_replace(temporary_path, dst)
            temporary_path = None
            if cls.__source_tree_snapshot(src) != source_snapshot:
                raise OSError(errno.EAGAIN, "staging source changed during publication", src)
            cls.__remove_published_source(src, source_stat, source_claim_snapshot)
            cls.__sync_directory_if_supported(os.path.dirname(src))
        except BaseException:
            publication_error = True
            raise
        finally:
            cleanup_paths = []
            if temporary_path is not None:
                cleanup_paths.append(temporary_path)
            if temporary_root is not None:
                cleanup_paths.append(temporary_root)
            for cleanup_path in cleanup_paths:
                try:
                    if os.path.isdir(cleanup_path) and not os.path.islink(cleanup_path):
                        shutil.rmtree(cleanup_path)
                    else:
                        os.unlink(cleanup_path)
                except FileNotFoundError:
                    continue
                except OSError:
                    # Keep uncertain private residue.  In a failure path it
                    # must not mask the error that controls source safety.
                    if not publication_error:
                        raise

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

    def __source_has_lftp_temp_artifact(self, staging_path: str, src: str, file_id: str) -> bool:
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

        def is_contained(candidate: str) -> bool:
            try:
                return os.path.normcase(os.path.commonpath([
                    resolved_staging_root, os.path.realpath(candidate)
                ])) == os.path.normcase(resolved_staging_root)
            except (OSError, ValueError):
                return False

        def remote_leaf_exists(relative_path: str) -> bool:
            try:
                result = self.__model_builder.is_remote_leaf_path(file_id, relative_path)
            except Exception:
                return False
            return result if type(result) is bool else False

        def visit(candidate: str, relative_path: str) -> bool:
            if not is_contained(candidate):
                return True
            try:
                candidate_stat = os.lstat(candidate)
            except OSError:
                return True
            if stat.S_ISLNK(candidate_stat.st_mode):
                return True
            if not stat.S_ISDIR(candidate_stat.st_mode):
                name = os.path.basename(candidate)
                if name.endswith(".lftp-pget-status"):
                    return True
                if not relative_path or not name.endswith(suffix):
                    return False
                # A remote leaf really named ``foo.lftp`` is payload.  An
                # otherwise absent ``foo.lftp`` beside remote ``foo`` is the
                # pget artifact and must remain in staging.
                return not remote_leaf_exists(relative_path) and \
                    remote_leaf_exists(relative_path[:-len(suffix)])
            try:
                with os.scandir(candidate) as entries:
                    for entry in entries:
                        child_relative = entry.name if not relative_path else relative_path + "/" + entry.name
                        if visit(entry.path, child_relative):
                            return True
            except OSError:
                return True
            return False

        # A single file whose requested name itself ends in .lftp remains a
        # supported payload.  Directory descendants use the remote scan to
        # distinguish a real ``foo.lftp`` from a pget temporary for ``foo``.
        if os.path.isdir(src) and not os.path.islink(src):
            return visit(src, "")
        return os.path.lexists(src + suffix) or os.path.lexists(src + ".lftp-pget-status")

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

    def __has_ambiguous_split_local_target(self, file: ModelFile) -> bool:
        """Whether deleting one root could discard final split-root content."""
        path_pair = self.__get_path_pair(file.path_pair_id)
        final_root = path_pair.local_path if path_pair is not None else self.__legacy_local_path
        staging_root = self.__get_staging_path(file.path_pair_id if path_pair is not None else None)
        if not final_root or not staging_root:
            return False
        final_target = Controller.__safe_final_move_candidate(final_root, file.name)
        staging_target = Controller.__safe_final_move_candidate(staging_root, file.name)
        if final_target is None or staging_target is None:
            return True
        try:
            return os.path.lexists(final_target) and (
                os.path.lexists(staging_target) or
                os.path.lexists(staging_target + Constants.LFTP_TEMP_FILE_SUFFIX)
            )
        except OSError:
            return True

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
        if file.file_id in self.__persist.extracted_file_names:
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
        remote_files_by_pair: dict[Optional[str], dict[str, SystemFile]] = {}
        for remote_file in remote_files:
            remote_files_by_pair.setdefault(getattr(remote_file, "path_pair_id", None), {})[remote_file.name] = remote_file

        staging_roots = self.__path_pair_staging_paths or {None: self.__staging_path}
        for path_pair_id, staging_path in staging_roots.items():
            try:
                staging_entries = os.listdir(staging_path)
            except OSError as error:
                self.logger.warning("Failed to inspect staging path '%s': %s", staging_path, error)
                continue

            remote_files_for_pair = remote_files_by_pair.get(path_pair_id, {})
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

                remote_file = remote_files_for_pair.get(file_name)
                if remote_file is None or \
                        self.__is_previously_downloaded(file_name, path_pair_id) or \
                        self.__is_explicitly_stopped(file_name, path_pair_id):
                    continue

                path_pair = self.__get_path_pair(path_pair_id)
                final_root = path_pair.local_path if path_pair is not None else self.__legacy_local_path
                final_path = os.path.join(final_root, file_name)
                try:
                    if not is_dir and os.path.isfile(final_path) and os.path.getsize(final_path) == remote_file.size:
                        # A completed final target wins over stale staging
                        # artifacts left by an interrupted previous process.
                        continue
                except OSError:
                    pass
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
                    queue_kwargs: dict[str, object] = {}
                    exclude_patterns = self.__transfer_exclude_patterns(file_id, is_dir)
                    if exclude_patterns:
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
        with self.__work_state_lock:
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
        """Fence every dequeued command until this drain has finished."""
        drain_file_ids: set[str] = set()
        self.__command_dispatch_drain_file_ids = drain_file_ids
        try:
            # The same outer lock protects the relocation busy snapshot and
            # all command-side lifecycle mutations in this drain.
            with self.__work_state_lock:
                self.__process_commands_impl()
        finally:
            with self.__work_state_lock:
                getattr(self, "_Controller__pending_command_dispatch_file_ids", set()).difference_update(
                    drain_file_ids
                )
            self.__command_dispatch_drain_file_ids = None

    def __process_commands_impl(self):
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
            if self.__path_pair_runtime_error is not None:
                _notify_failure(command, self.__path_pair_runtime_error, 503, file)
                continue
            if not self.__begin_command_dispatch(file.file_id, file.path_pair_id):
                _notify_failure(command, "Path pair relocation is in progress", 409, file)
                continue
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
                elif not file.remote_has_transferable_content:
                    _notify_failure(
                        command,
                        "File '{}' has no transferable remote content".format(command.filename),
                        409,
                        file,
                    )
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
                        queue_kwargs: dict[str, object] = {}
                        exclude_patterns = self.__transfer_exclude_patterns(file.file_id, file.is_dir)
                        if exclude_patterns:
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
                            self.__advance_transfer_lifecycle(file.file_id)
                            getattr(self, "_Controller__current_process_final_publication_file_ids", set()).discard(file.file_id)
                            self._sync_final_move_succeeded_files_to_model()
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
                    stopped_marked = file.file_id in self.__persist.stopped_file_names
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
                        self.__ensure_extract_worker_started()
                        self._record_worker_dispatch("extract", file.file_id)
                        self.__extract_process.extract(extract_request, flow_id=command.flow_id)
                    except Exception:
                        self._record_worker_terminal_ids({file.file_id}, set())
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
                        self.__ensure_validate_worker_started()
                        self._record_worker_dispatch("validate", file.file_id)
                        self.__validate_process.validate(file)
                    except Exception:
                        self._record_worker_terminal_ids(set(), {file.file_id})
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
                elif self.__has_ambiguous_split_local_target(file):
                    _notify_failure(
                        command,
                        "Local file '{}' has both final and staging content; refusing ambiguous delete".format(
                            command.filename
                        ),
                        409,
                        file,
                    )
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
                    # Deletion changes the transfer lifecycle as soon as it
                    # is admitted. A later auto-delete in this same drain
                    # cannot retain authority captured before local deletion.
                    self.__advance_transfer_lifecycle(file.file_id)
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
                # Manual retries are allowed only after current scanner/model
                # evidence proves the staging tree still matches the remote
                # identity.  A terminal collision may contain equally sized,
                # equally timestamped final/staging leaves that are both stale
                # relative to the remote source; filesystem-only merging must
                # never delete that staging evidence.
                if not self.__model_builder.has_complete_local_coverage(file.file_id) or \
                        self.__model_builder.has_unresolved_staging_collision(file.file_id):
                    _notify_failure(command, "Final move requires current collision-free coverage", 409, file)
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
                        self._record_download_completion(file)
                        self.__persist.downloaded_file_names.add(file.file_id)
                        if result == Controller.MoveFromStagingResult.COMPLETED:
                            self.__persist.final_move_succeeded_file_names.add(file.file_id)
                            self._mark_successful_final_move_handoff(file.file_id)
                            self._mark_current_process_final_publication(file.file_id)
                        self._complete_download_start_lifecycle(file.file_id)
                        self.clear_extracted_marker(file)
                        self.__pending_completion_file_names = {
                            entry for entry in self.__pending_completion_file_names
                            if ModelFile.build_file_id(entry[0], entry[1]) != file.file_id
                        }
                        getattr(self, "_Controller__pending_completion_progress_floors", {}).pop(
                            file.file_id,
                            None,
                        )
                        self.__model_builder.set_downloaded_files(self.__persist.downloaded_file_names)
                        self._sync_final_move_succeeded_files_to_model()
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
                if getattr(command, "origin", "manual") == "auto_queue" and (
                        getattr(command, "lifecycle_token", None) != self.remote_delete_lifecycle_token(file)
                        or not self.is_remote_delete_eligible(file)
                ):
                    _notify_failure(
                        command,
                        "Auto-queue remote delete no longer has current lifecycle authority",
                        409,
                        file,
                    )
                    continue
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
                    # This consumes an auto-queue token that passed the
                    # check above, invalidating subsequent stale commands
                    # while remote deletion is active.
                    self.__advance_transfer_lifecycle(file.file_id)
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
            model_file_count = self.__model.file_count

        self.__memory_monitor.log_if_due(
            model_file_count=model_file_count,
            downloaded_file_count=len(self.__persist.downloaded_file_names),
            extracted_file_count=len(self.__persist.extracted_file_names),
            stopped_file_count=len(self.__persist.stopped_file_names),
            active_download_count=len(self.__active_downloading_file_names),
            active_extract_count=len(self.__active_extracting_file_names),
            active_command_count=len(self.__active_command_processes)
        )
        # This is deliberately independent of the legacy memory log interval.
        # The collector's own bounded interval keeps procfs reads cheap and any
        # diagnostic failure is isolated from the controller loop.
        try:
            self.__context.performance_diagnostics.sample_if_due(self.__performance_diagnostics_gauges)
        except Exception:
            pass

    def __performance_diagnostics_gauges(self) -> dict[str, int]:
        """Read existing ownership counts only when a diagnostics sample is due."""
        with self.__model_lock:
            model_root_count = self.__model.file_count
            model_listener_count = self.__model.listener_count
            model_tree_file_count = self.__model.tree_file_count
        return {
            "model_root_count": model_root_count,
            "model_tree_file_count": model_tree_file_count,
            "model_listener_count": model_listener_count,
            "path_pair_count": len(self.__path_pairs_by_id),
            "active_download_count": len(self.__active_downloading_file_names),
            "active_extract_count": len(self.__active_extracting_file_names),
            "active_command_count": len(self.__active_command_processes),
        }

    def get_memory_ownership_census(self) -> dict[str, object]:
        """Return an on-demand, bounded, privacy-safe ownership census."""
        general = getattr(getattr(self.__context, "config", None), "general", None)
        if type(getattr(general, "performance_diagnostics_enabled", None)) is not bool or \
                not general.performance_diagnostics_enabled:
            return disabled_ownership_census()
        roots: tuple[OwnershipRoot, ...] | None = None
        try:
            roots = self.__capture_memory_ownership_roots()
            return build_ownership_census(roots)
        except Exception:
            # A diagnostic request must never change normal controller behavior.
            return unavailable_ownership_census()
        finally:
            roots = None
            release_ownership_census_working_memory()

    def __capture_memory_ownership_roots(self) -> tuple[OwnershipRoot, ...]:
        """Capture only shallow references while honoring updater lock ordering."""
        # ModelUpdater holds work-state before model lock while it switches
        # scan/status state and applies a new model; preserve that ordering.
        with self.__work_state_lock:
            with self.__model_lock:
                live_model = self.__model
                live_files = tuple(live_model.iter_files())
                builder = self.__model_builder
                cached_model = getattr(builder, "_ModelBuilder__cached_model", None)
                cached_files = () if cached_model is None or cached_model is live_model else tuple(cached_model.iter_files())
                roots: list[OwnershipRoot] = [
                    OwnershipRoot("live_model_graph", live_files),
                    OwnershipRoot("builder_cached_model_graph", cached_files,
                                  aliases_live_model=cached_model is live_model),
                    snapshot_container("builder_local_system_file_graph", getattr(builder, "_ModelBuilder__local_files", None)),
                    snapshot_container("builder_active_system_file_graph", getattr(builder, "_ModelBuilder__active_files", None)),
                    snapshot_container("builder_remote_system_file_graph", getattr(builder, "_ModelBuilder__remote_files", None)),
                    snapshot_container("builder_active_file_ids", getattr(builder, "_ModelBuilder__active_file_ids", None)),
                    snapshot_container("builder_lftp_statuses", getattr(builder, "_ModelBuilder__lftp_statuses", None)),
                    snapshot_container("builder_extract_statuses", getattr(builder, "_ModelBuilder__extract_statuses", None)),
                    snapshot_container("builder_validation_statuses", getattr(builder, "_ModelBuilder__validation_statuses", None)),
                    snapshot_container("builder_recent_transfer_snapshots", getattr(builder, "_ModelBuilder__recent_live_transfer_snapshots", None)),
                    snapshot_container("builder_retained_transfer_snapshots", getattr(builder, "_ModelBuilder__retained_stopped_transfer_snapshots", None)),
                    snapshot_container("controller_active_downloads", getattr(self, "_Controller__active_downloading_file_names", None)),
                    snapshot_container("controller_active_extracts", getattr(self, "_Controller__active_extracting_file_names", None)),
                    snapshot_container("controller_pending_completion", getattr(self, "_Controller__pending_completion_file_names", None)),
                    snapshot_container("controller_pending_extract", getattr(self, "_Controller__pending_extract_file_ids", None)),
                    snapshot_container("controller_pending_validation", getattr(self, "_Controller__pending_validation_file_ids", None)),
                    snapshot_container("controller_move_retries", getattr(self, "_Controller__move_retry_due", None)),
                    snapshot_container("controller_deferred_moves", getattr(self, "_Controller__deferred_move_file_ids", None)),
                    snapshot_container("controller_malformed_status_only", getattr(self, "_Controller__malformed_status_only_file_ids", None)),
                    snapshot_container("controller_pending_auto_purge", getattr(self, "_Controller__pending_auto_purge_file_ids", None)),
                ]
        return tuple(roots)

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
        if self.__extract_process.pid is not None:
            try:
                self.__extract_process.propagate_exception()
            except Exception as exc:
                self.logger.warning(
                    "Ignoring extract worker failure during controller loop: {}".format(str(exc)),
                    exc_info=True
                )
            self.__report_dead_worker_once(self.__extract_process, "extract")
        if self.__validate_process.pid is not None:
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
                                self.__advance_transfer_lifecycle(command_process.file_id)
                                self.__persist.move_failure_counts.pop(command_process.file_id, None)
                                self.__deferred_move_file_ids.discard(command_process.file_id)
                                self.__move_retry_due.pop(command_process.file_id, None)
                                self.__persist.final_move_succeeded_file_names.discard(command_process.file_id)
                                getattr(self, "_Controller__current_process_final_publication_file_ids", set()).discard(
                                    command_process.file_id
                                )
                                self._sync_final_move_succeeded_files_to_model()
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
                                self.__advance_transfer_lifecycle(command_process.file_id)
                                self._clear_download_start_lifecycle(command_process.file_id)
                                event_file = command_process.event_file
                                if not isinstance(getattr(self.__persist, "downloaded_timestamps", None), dict):
                                    self.__persist.downloaded_timestamps = {}
                                self.__persist.downloaded_timestamps.pop(command_process.file_id, None)
                                self.__model_builder.set_downloaded_timestamps(
                                    self.__persist.downloaded_timestamps
                                )
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
                                    getattr(self, "_Controller__current_process_final_publication_file_ids", set()).discard(
                                        command_process.file_id
                                    )
                                    self._sync_final_move_succeeded_files_to_model()
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
