# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
from abc import ABC, abstractmethod
import multiprocessing
import threading
from collections import deque
from datetime import datetime
from typing import Callable, List, Optional, Protocol
import queue
import uuid
from multiprocessing.queues import Queue as MPQueue
from multiprocessing.synchronize import Event as EventType

from common import AppError
from common.app_process import ExceptionWrapper
from common.performance_diagnostics import FixedDurationRecorder, PerformanceDiagnosticsCollector
from system import SystemFile


class _BreadcrumbEmitter(Protocol):
    def record(self, source: str, message: str, details: object = None, **metadata: object) -> None: ...


class ScannerError(AppError):
    """
    Indicates a scanner error

    Args:
        recoverable: indicates scans can be retried
    """
    def __init__(self,
                 message: str,
                 recoverable: bool = False,
                 files: Optional[List[SystemFile]] = None):
        super().__init__(message)
        self.recoverable = recoverable
        self.files = files


class IScanner(ABC):
    """
    Interface to scan the system.
    This hides the scanning implementation from the scanner process.
    """
    @abstractmethod
    def scan(self) -> List[SystemFile]:
        """Scan system"""
        pass

    @abstractmethod
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        pass

    def pop_malformed_status_only_file_ids(self) -> List[str]:
        return []

    def pop_managed_extract_file_ids(self) -> List[str]:
        return []

    def scanned_path_pair_ids(self) -> set[str | None]:
        """Roots covered by a successful scan; empty roots still count."""
        return {None}

    def failed_path_pair_ids(self) -> set[str | None]:
        """Path pairs whose current generation ended in a recoverable error."""
        return set()

    def set_progress_callback(self, callback: Optional["ScanProgressCallback"]) -> None:
        """Receive bounded manifest/root/completion events during a scan."""
        # Optional for legacy scanners.  Concrete scanners override this hook.
        return None


ScanProgressCallback = Callable[
    [List[SystemFile], Optional[str], Optional[str], Optional[set[str]], bool],
    None,
]


class ScannerResult:
    """
    Results of a system scan
    """
    def __init__(self,
                 timestamp: datetime,
                 files: List[SystemFile],
                 malformed_status_only_file_ids: Optional[List[str]] = None,
                 managed_extract_file_ids: Optional[List[str]] = None,
                 scanned_path_pair_ids: Optional[set[str | None]] = None,
                 failed: bool = False,
                 error_message: str | None = None,
                 generation: int = 0,
                 is_progress: bool = False,
                 root_names: Optional[set[str]] = None,
                 completed_path_pair_ids: Optional[set[str | None]] = None,
                 is_scan_final: bool = True,
                 unknown_path_pair_ids: Optional[set[str | None]] = None,
                 session_token: Optional[str] = None,
                 is_full_snapshot: bool = False,
                 full_snapshot_path_pair_ids: Optional[set[str | None]] = None,
                 is_targeted_scan: bool = False,
                 duration_aggregates: Optional[dict[str, dict[str, float | int]]] = None,
                 duration_aggregate_token: Optional[tuple[str, int, int]] = None):
        self.timestamp = timestamp
        self.files = files
        self.malformed_status_only_file_ids = [] if malformed_status_only_file_ids is None else malformed_status_only_file_ids
        self.managed_extract_file_ids = [] if managed_extract_file_ids is None else managed_extract_file_ids
        self.scanned_path_pair_ids = {None} if scanned_path_pair_ids is None else scanned_path_pair_ids
        self.failed = failed
        self.error_message = error_message
        self.generation = generation
        self.is_progress = is_progress
        self.root_names = root_names
        self.completed_path_pair_ids = set() if completed_path_pair_ids is None else completed_path_pair_ids
        self.is_scan_final = is_scan_final
        self.unknown_path_pair_ids = set() if unknown_path_pair_ids is None else unknown_path_pair_ids
        self.session_token = session_token
        self.is_full_snapshot = is_full_snapshot
        self.full_snapshot_path_pair_ids = set() if full_snapshot_path_pair_ids is None else full_snapshot_path_pair_ids
        self.is_targeted_scan = is_targeted_scan
        self.duration_aggregates = duration_aggregates
        self.duration_aggregate_token = duration_aggregate_token


class _ScannerQueueReleaseMarker:
    """Tiny queue item that lets the feeder release its prior large payload."""
    pass


_PUBLISH_LOCK = threading.Lock()


def _is_authoritative_scan_result(item: object) -> bool:
    return isinstance(item, ScannerResult) and (item.is_scan_final or item.is_full_snapshot)


def _is_progress_completion_result(item: object) -> bool:
    return isinstance(item, ScannerResult) and item.is_progress and bool(item.completed_path_pair_ids) \
        and not _is_authoritative_scan_result(item)


def _is_protected_scan_result(item: object) -> bool:
    """Return whether an item must survive ordinary bounded progress loss."""
    return _is_authoritative_scan_result(item) or _is_progress_completion_result(item)


def _scan_result_pair_ids(item: object) -> set[str | None]:
    if not isinstance(item, ScannerResult):
        return set()
    return set(item.scanned_path_pair_ids) | set(item.completed_path_pair_ids) \
        | set(item.full_snapshot_path_pair_ids) | set(item.unknown_path_pair_ids)


def _is_failure_scan_result(item: object) -> bool:
    return isinstance(item, ScannerResult) and (item.failed or bool(item.unknown_path_pair_ids))


def _replacement_index(retained: list[object], result: ScannerResult) -> Optional[int]:
    """Choose a bounded eviction without discarding unrelated failure state first."""
    ordinary = next((
        index for index, item in enumerate(retained)
        if not _is_protected_scan_result(item)
    ), None)
    if ordinary is not None:
        return ordinary

    incoming_pairs = _scan_result_pair_ids(result)
    incoming_generation = result.generation
    same_pair = [
        index for index, item in enumerate(retained)
        if isinstance(item, ScannerResult)
        and bool(_scan_result_pair_ids(item) & incoming_pairs)
        and item.generation <= incoming_generation
    ]
    same_completion = next((
        index for index in same_pair if _is_progress_completion_result(retained[index])
    ), None)
    if same_completion is not None:
        return same_completion
    if _is_progress_completion_result(result):
        return None
    if not _is_authoritative_scan_result(result):
        return None
    same_healthy = next((
        index for index in same_pair if not _is_failure_scan_result(retained[index])
    ), None)
    if same_healthy is not None:
        return same_healthy
    same_failure = next((
        index for index in same_pair if _is_failure_scan_result(retained[index])
    ), None)
    if same_failure is not None:
        return same_failure
    unrelated_completion = next((
        index for index, item in enumerate(retained)
        if _is_progress_completion_result(item)
    ), None)
    if unrelated_completion is not None:
        return unrelated_completion
    unrelated_healthy = next((
        index for index, item in enumerate(retained)
        if not _is_failure_scan_result(item)
    ), None)
    if unrelated_healthy is not None:
        return unrelated_healthy
    # Every remaining item is an unrelated failure/unknown record. Capacity
    # is finite, so retain newest arrivals while callers still receive the
    # bounded queue's newest authoritative evidence.
    return 0


def _publish_bounded_result(output_queue: object,
                            result: ScannerResult) -> None:
    """Publish without blocking forever when a scan outruns its consumer.

    Ordinary root progress is deliberately lossy at the queue boundary. Pair
    completion markers and final/full snapshots are retained: completion is
    useful progress metadata for every scanned pair, while the accumulator
    separately requires the lossless full snapshot before treating it as
    authority for absence or completion.
    """
    with _PUBLISH_LOCK:
        while True:
            try:
                put_nowait = getattr(output_queue, "put_nowait")
                put_nowait(result)
                return
            except queue.Full:
                retained: list[object] = []
                try:
                    while True:
                        retained.append(output_queue.get_nowait())
                except queue.Empty:
                    pass
                except (OSError, EOFError, ValueError):
                    return
                if not retained:
                    # A consumer can drain the queue between the failed put
                    # and this producer's non-blocking drain. Retry the
                    # direct put instead of treating an empty protected set
                    # as evictable state.
                    continue
                drop_index = _replacement_index(retained, result)
                if drop_index is None:
                    # An ordinary root batch, or a completion for a new pair
                    # when every slot is protected, is safely lossy.
                    for item in retained:
                        try:
                            output_queue.put_nowait(item)
                        except (queue.Full, OSError, EOFError, ValueError):
                            return
                    return
                retained.pop(drop_index)
                for item in retained:
                    try:
                        output_queue.put_nowait(item)
                    except (queue.Full, OSError, EOFError, ValueError):
                        return

            except (OSError, EOFError, ValueError):
                return


def _record_scan_breadcrumb(scanner: IScanner, breadcrumb_trace: Optional[_BreadcrumbEmitter], flow_id: str,
                            message: str, details: dict[str, object], event_type: str = "state_transition") -> None:
    if breadcrumb_trace is None:
        return
    path_pair_id = getattr(scanner, "path_pair_id", None)
    path_pair_name = getattr(scanner, "path_pair_name", None)
    breadcrumb_trace.record("scanner_process", message, details, stage="scan", event_type=event_type,
                            corr_id=path_pair_id if isinstance(path_pair_id, str) else scanner.__class__.__name__,
                            flow_id=flow_id, path_pair_id=path_pair_id if isinstance(path_pair_id, str) else None,
                            path_pair_name=path_pair_name if isinstance(path_pair_name, str) else None)


def _run_scanner_once(scanner: IScanner, output_queue: Optional[object],
                      scan_target_path_pair_ids: Optional[set[str]], control_connection: object,
                      breadcrumb_trace: Optional[_BreadcrumbEmitter], flow_id: str,
                      mp_log_queue: Optional[MPQueue[logging.LogRecord]], mp_log_level: Optional[int],
                      generation: int = 0, session_token: str = "",
                      result_via_control: bool = False,
                      performance_diagnostics_enabled: bool = False,
                      performance_diagnostics_generation: int = 0) -> None:
    """Run one scan in a spawn-context process, without a nested forkserver."""
    logger = logging.getLogger("{}ScanRun".format(scanner.__class__.__name__))
    if mp_log_queue is not None:
        from logging.handlers import QueueHandler

        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)
        root_logger.addHandler(QueueHandler(mp_log_queue))
        if mp_log_level is not None:
            root_logger.setLevel(mp_log_level)
        logger = root_logger.getChild("{}ScanRun".format(scanner.__class__.__name__))

    setter = getattr(scanner, "set_scan_target_path_pair_ids", None)
    duration_recorder = FixedDurationRecorder(performance_diagnostics_enabled)
    diagnostics_setter = getattr(scanner, "set_performance_diagnostics", None)
    if callable(diagnostics_setter):
        diagnostics_setter(duration_recorder)
    progress_emitted = False
    progress_files_by_pair: dict[Optional[str], list[SystemFile]] = {}
    control_send_lock = threading.Lock()

    def send_control_message(message: object) -> None:
        # MultiPathRemoteScanner invokes progress callbacks from worker
        # threads.  multiprocessing.Connection does not guarantee framing
        # when concurrent writers share one endpoint, so serialize every
        # progress/final/status send in the child.
        with control_send_lock:
            control_connection.send(message)

    def publish_progress(files: List[SystemFile], path_pair_id: Optional[str], path_pair_name: Optional[str],
                         root_names: Optional[set[str]], complete: bool) -> None:
        nonlocal progress_emitted
        progress_emitted = True
        for system_file in files:
            system_file.path_pair_id = path_pair_id
            system_file.path_pair_name = path_pair_name
        progress_files_by_pair.setdefault(path_pair_id, []).extend(files)
        progress_result = ScannerResult(
            datetime.now(),
            files,
            scanned_path_pair_ids={path_pair_id},
            generation=generation,
            is_progress=True,
            root_names=None if root_names is None else set(root_names),
            completed_path_pair_ids={path_pair_id} if complete else set(),
            is_scan_final=False,
            session_token=session_token,
        )
        if result_via_control:
            send_control_message(("result", progress_result))
        elif output_queue is not None:
            _publish_bounded_result(output_queue, progress_result)
        if complete:
            pair_snapshot = ScannerResult(
                datetime.now(),
                list(progress_files_by_pair.get(path_pair_id, [])),
                scanned_path_pair_ids={path_pair_id},
                generation=generation,
                is_progress=True,
                completed_path_pair_ids={path_pair_id},
                is_full_snapshot=True,
                full_snapshot_path_pair_ids={path_pair_id},
                session_token=session_token,
            )
            if result_via_control:
                send_control_message(("result", pair_snapshot))
            elif output_queue is not None:
                _publish_bounded_result(output_queue, pair_snapshot)

    outcome: tuple[str, object | None] = ("success", None)
    try:
        scanner.set_base_logger(logger)
        scanner.set_progress_callback(publish_progress)
        if callable(setter):
            setter(scan_target_path_pair_ids)
        timestamp = datetime.now()
        try:
            files = scanner.scan()
            malformed = scanner.pop_malformed_status_only_file_ids()
            managed = scanner.pop_managed_extract_file_ids()
            scanned_ids = scanner.scanned_path_pair_ids()
            result = ScannerResult(
                timestamp,
                files,
                malformed,
                managed,
                scanned_ids,
                generation=generation,
                is_progress=progress_emitted,
                completed_path_pair_ids=scanned_ids if progress_emitted else set(),
                is_full_snapshot=progress_emitted,
                full_snapshot_path_pair_ids=scanned_ids if progress_emitted else set(),
                is_targeted_scan=scan_target_path_pair_ids is not None,
                session_token=session_token,
                duration_aggregates=duration_recorder.snapshot(),
                duration_aggregate_token=(session_token, generation, performance_diagnostics_generation),
            )
            _record_scan_breadcrumb(scanner, breadcrumb_trace, flow_id, "scan_completed",
                                    {"scanner": scanner.__class__.__name__, "file_count": len(files),
                                     "malformed_status_only_file_count": len(malformed),
                                     "managed_extract_file_count": len(managed)})
        except ScannerError as error:
            if not error.recoverable:
                _record_scan_breadcrumb(scanner, breadcrumb_trace, flow_id, "scan_failed",
                                        {"scanner": scanner.__class__.__name__, "recoverable": False,
                                         "error_message": str(error)}, "failure")
                raise
            files = error.files if error.files is not None else []
            malformed = scanner.pop_malformed_status_only_file_ids()
            managed = scanner.pop_managed_extract_file_ids()
            failed_ids = getattr(scanner, "failed_path_pair_ids", scanner.scanned_path_pair_ids)()
            result = ScannerResult(
                timestamp,
                [] if progress_emitted else files,
                malformed,
                managed,
                failed_ids,
                failed=True,
                error_message=str(error),
                generation=generation,
                # A setup failure can happen before the first stream batch.
                # Treat its affected IDs as a progressive event so the
                # reconciler marks them unknown and preserves last-good data.
                is_progress=progress_emitted or bool(failed_ids),
                unknown_path_pair_ids=failed_ids,
                is_targeted_scan=scan_target_path_pair_ids is not None,
                session_token=session_token,
                duration_aggregates=duration_recorder.snapshot(),
                duration_aggregate_token=(session_token, generation, performance_diagnostics_generation),
            )
            outcome = ("recoverable", str(error))
            _record_scan_breadcrumb(scanner, breadcrumb_trace, flow_id, "scan_failed",
                                    {"scanner": scanner.__class__.__name__, "recoverable": True,
                                     "file_count": len(files), "malformed_status_only_file_count": len(malformed),
                                     "managed_extract_file_count": len(managed), "error_message": str(error)}, "failure")
        if result_via_control:
            # A spawn child must not leave a multiprocessing.Queue feeder
            # thread behind.  Send the authoritative aggregate over the
            # control pipe; the coordinator drains it while the worker is
            # still alive and publishes it to its bounded local queue.
            send_control_message(("result", result))
        elif output_queue is not None:
            _publish_bounded_result(output_queue, result)
        # The parent-facing queue/pipe now owns the result; keep no completed
        # scan graph in the coordinator or in this child while it exits.
        del result
        del files
        del malformed
        del managed
    except Exception as error:
        logger.debug("Process caught an exception")
        outcome = ("fatal", ExceptionWrapper(error))
    finally:
        scanner.set_progress_callback(None)
        if callable(diagnostics_setter):
            diagnostics_setter(None)
        if callable(setter):
            setter(None)
        recycled_state: object | None = None
        try:
            exporter = getattr(scanner, "export_recycled_state", None)
            if callable(exporter):
                recycled_state = exporter()
        except Exception as error:
            if outcome[0] != "fatal":
                outcome = ("fatal", ExceptionWrapper(error))
        try:
            send_control_message((outcome[0], outcome[1], recycled_state))
        finally:
            control_connection.close()


def _create_scanner_worker(scanner: IScanner, output_queue: Optional[object],
                           scan_target_path_pair_ids: Optional[set[str]], control_connection: object,
                           breadcrumb_trace: Optional[_BreadcrumbEmitter], flow_id: str,
                           mp_log_queue: Optional[MPQueue[logging.LogRecord]], mp_log_level: Optional[int],
                           generation: int = 0, session_token: str = "",
                           performance_diagnostics_enabled: bool = False,
                           performance_diagnostics_generation: int = 0) -> multiprocessing.Process:
    """Use spawn explicitly: coordinators can be forkserver children and threaded."""
    return multiprocessing.get_context("spawn").Process(
        name="{}ScanRun".format(scanner.__class__.__name__),
        target=_run_scanner_once,
        args=(scanner, output_queue, scan_target_path_pair_ids, control_connection, breadcrumb_trace, flow_id,
              mp_log_queue, mp_log_level, generation, session_token, True,
              performance_diagnostics_enabled, performance_diagnostics_generation),
    )


class ScannerProcess:
    """
    Process to scan a file system and publish the result
    """
    def __init__(self,
                 scanner: IScanner, interval_in_ms: int,
                 verbose: bool = True,
                 breadcrumb_trace: Optional[_BreadcrumbEmitter] = None,
                 recycle_scan_worker: bool = False,
                 performance_diagnostics: Optional[PerformanceDiagnosticsCollector] = None):
        """
        Create a scanner process
        :param scanner: IScanner implementation
        :param interval_in_ms: Minimum interval (in ms) between results
        """
        self.name = scanner.__class__.__name__
        self.logger = logging.getLogger(self.name)
        # Spawned workers publish over their control pipe.  Keeping the
        # coordinator-facing queue local avoids a second multiprocessing
        # feeder thread and gives the same bounded/drop-oldest semantics to
        # both inline and recycled scans.
        self.__queue: Optional[queue.Queue[ScannerResult | _ScannerQueueReleaseMarker]] = queue.Queue(maxsize=128)
        self.__queue_is_multiprocessing = False
        self.__scan_target_queue: Optional[queue.Queue[Optional[str]]] = queue.Queue()
        self.__wake_event: Optional[threading.Event] = threading.Event()
        self.__scanner = scanner
        self.__interval_in_ms = interval_in_ms
        self.__last_recoverable_error_message: Optional[str] = None
        self.verbose = verbose
        self.__breadcrumb_trace = breadcrumb_trace
        self.__recycle_scan_worker = recycle_scan_worker
        self.__performance_diagnostics = performance_diagnostics
        self.__ingested_duration_tokens: deque[tuple[str, int, int]] = deque(maxlen=64)
        self.__scan_worker: Optional[multiprocessing.Process] = None
        self.__scan_worker_started_at: Optional[datetime] = None
        self.__scan_worker_control_connection: object | None = None
        self.__scan_worker_pending_status: object | None = None
        self.__scan_worker_force_pending = False
        self.__scan_generation = 0
        self.__session_token = uuid.uuid4().hex
        self.__thread: Optional[threading.Thread] = None
        self.__terminate_event = threading.Event()
        self.__exception: Optional[BaseException] = None
        self.__exception_lock = threading.Lock()
        self._mp_log_queue: Optional[MPQueue[logging.LogRecord]] = None
        self._mp_log_level: Optional[int] = None

    def run_init(self) -> None:
        if not self.__recycle_scan_worker:
            self.__scanner.set_base_logger(self.logger)

    def run_cleanup(self) -> None:
        self.__teardown_scan_worker()

    def run_loop(self) -> None:
        if not self.__recycle_scan_worker:
            self.__run_inline_scan()
            return
        self.__run_recycled_scan()

    @property
    def pid(self) -> Optional[int]:
        thread = self.__thread
        return thread.ident if thread is not None else None

    @property
    def session_token(self) -> str:
        return self.__session_token

    def set_mp_log_queue(self, log_queue: MPQueue[logging.LogRecord], log_level: int) -> None:
        self._mp_log_queue = log_queue
        self._mp_log_level = log_level

    def start(self) -> None:
        if self.__thread is not None:
            raise RuntimeError("Scanner coordinator has already been started")
        self.__thread = threading.Thread(target=self.__thread_main, name=self.name, daemon=True)
        self.__thread.start()

    def terminate(self) -> None:
        self.__terminate_event.set()
        wake_event = self.__wake_event
        if wake_event is not None:
            wake_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        thread = self.__thread
        if thread is not None:
            thread.join(timeout)

    def is_alive(self) -> bool:
        thread = self.__thread
        return thread.is_alive() if thread is not None else False

    def propagate_exception(self) -> None:
        with self.__exception_lock:
            exception = self.__exception
            self.__exception = None
        if exception is not None:
            raise exception

    def __thread_main(self) -> None:
        try:
            self.run_init()
            while not self.__terminate_event.is_set():
                self.run_loop()
        except Exception as error:
            self.logger.debug("Scanner coordinator caught an exception", exc_info=True)
            with self.__exception_lock:
                self.__exception = error
        finally:
            self.run_cleanup()

    def __run_recycled_scan(self) -> None:
        if self.__scan_worker is not None:
            self.__poll_scan_worker()
            return
        timestamp_start = datetime.now()
        if self.verbose:
            self.logger.debug("Running a scan")
        flow_id = "{}:{}".format(self.__scanner.__class__.__name__, int(timestamp_start.timestamp() * 1000))
        self.__record_breadcrumb(
            "scan_started",
            {
                "scanner": self.__scanner.__class__.__name__,
                "interval_ms": self.__interval_in_ms,
            },
            flow_id=flow_id,
        )
        scan_target_path_pair_ids = self.__drain_scan_target_path_pair_ids()
        assert self.__queue is not None
        spawn_context = multiprocessing.get_context("spawn")
        receive_connection, send_connection = spawn_context.Pipe(duplex=False)
        self.__scan_generation += 1
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            diagnostics_enabled, diagnostics_generation = False, 0
        else:
            diagnostics_enabled, diagnostics_generation = diagnostics.duration_worker_state()
        worker = _create_scanner_worker(self.__scanner, None, scan_target_path_pair_ids, send_connection,
                                        self.__breadcrumb_trace, flow_id, self._mp_log_queue, self._mp_log_level,
                                        self.__scan_generation, self.__session_token,
                                        diagnostics_enabled, diagnostics_generation)
        worker.daemon = True
        worker.start()
        send_connection.close()
        self.__scan_worker = worker
        self.__scan_worker_started_at = timestamp_start
        self.__scan_worker_control_connection = receive_connection
        # A failed-to-start or very small one-shot worker may already have
        # exited.  Surface its outcome now; normal scans remain asynchronous.
        if not worker.is_alive():
            self.__poll_scan_worker()

    def __run_inline_scan(self) -> None:
        timestamp_start = datetime.now()
        if self.verbose:
            self.logger.debug("Running a scan")
        flow_id = "{}:{}".format(self.__scanner.__class__.__name__, int(timestamp_start.timestamp() * 1000))
        self.__record_breadcrumb("scan_started", {"scanner": self.__scanner.__class__.__name__,
                                                   "interval_ms": self.__interval_in_ms}, flow_id=flow_id)
        scan_target_path_pair_ids = self.__drain_scan_target_path_pair_ids()
        setter = getattr(self.__scanner, "set_scan_target_path_pair_ids", None)
        if callable(setter):
            setter(scan_target_path_pair_ids)
        self.__scan_generation += 1
        progress_emitted = False
        progress_files_by_pair: dict[Optional[str], list[SystemFile]] = {}

        def publish_progress(files: List[SystemFile], path_pair_id: Optional[str], path_pair_name: Optional[str],
                             root_names: Optional[set[str]], complete: bool) -> None:
            nonlocal progress_emitted
            progress_emitted = True
            for system_file in files:
                system_file.path_pair_id = path_pair_id
                system_file.path_pair_name = path_pair_name
            progress_files_by_pair.setdefault(path_pair_id, []).extend(files)
            assert self.__queue is not None
            _publish_bounded_result(self.__queue, ScannerResult(
                datetime.now(), files,
                scanned_path_pair_ids={path_pair_id},
                generation=self.__scan_generation,
                is_progress=True,
                root_names=None if root_names is None else set(root_names),
                completed_path_pair_ids={path_pair_id} if complete else set(),
                is_scan_final=False,
                session_token=self.__session_token,
            ))
            if complete:
                _publish_bounded_result(self.__queue, ScannerResult(
                    datetime.now(), list(progress_files_by_pair.get(path_pair_id, [])),
                    scanned_path_pair_ids={path_pair_id},
                    generation=self.__scan_generation,
                    is_progress=True,
                    completed_path_pair_ids={path_pair_id},
                    is_full_snapshot=True,
                    full_snapshot_path_pair_ids={path_pair_id},
                    session_token=self.__session_token,
                ))
        self.__scanner.set_progress_callback(publish_progress)
        try:
            files = self.__scanner.scan()
            malformed = self.__scanner.pop_malformed_status_only_file_ids()
            managed = self.__scanner.pop_managed_extract_file_ids()
            self.__last_recoverable_error_message = None
            result = ScannerResult(timestamp_start, files, malformed, managed,
                                    self.__scanner.scanned_path_pair_ids(), generation=self.__scan_generation,
                                    is_progress=progress_emitted,
                completed_path_pair_ids=self.__scanner.scanned_path_pair_ids()
                                    if progress_emitted else set(),
                                    is_full_snapshot=progress_emitted,
                                    full_snapshot_path_pair_ids=self.__scanner.scanned_path_pair_ids()
                                    if progress_emitted else set(),
                                    is_targeted_scan=scan_target_path_pair_ids is not None,
                                    session_token=self.__session_token)
            self.__record_breadcrumb("scan_completed", {"scanner": self.__scanner.__class__.__name__,
                                                          "file_count": len(files),
                                                          "malformed_status_only_file_count": len(malformed),
                                                          "managed_extract_file_count": len(managed)}, flow_id=flow_id)
        except ScannerError as error:
            if not error.recoverable:
                self.__record_breadcrumb("scan_failed", {"scanner": self.__scanner.__class__.__name__,
                                                           "recoverable": False, "error_message": str(error)},
                                         event_type="failure", flow_id=flow_id)
                raise
            files = error.files if error.files is not None else []
            malformed = self.__scanner.pop_malformed_status_only_file_ids()
            managed = self.__scanner.pop_managed_extract_file_ids()
            error_message = str(error)
            if error_message != self.__last_recoverable_error_message:
                self.logger.warning("Recoverable scanner error; returning failed result: {}".format(error_message))
                self.__last_recoverable_error_message = error_message
            failed_ids = getattr(self.__scanner, "failed_path_pair_ids", self.__scanner.scanned_path_pair_ids)()
            result = ScannerResult(timestamp_start, [] if progress_emitted else files, malformed, managed,
                                   failed_ids,
                                   failed=True, error_message=error_message, generation=self.__scan_generation,
                                   is_progress=progress_emitted or bool(failed_ids),
                                   unknown_path_pair_ids=failed_ids,
                                   is_targeted_scan=scan_target_path_pair_ids is not None,
                                   session_token=self.__session_token)
            self.__record_breadcrumb("scan_failed", {"scanner": self.__scanner.__class__.__name__,
                                                       "recoverable": True, "file_count": len(files),
                                                       "malformed_status_only_file_count": len(malformed),
                                                       "managed_extract_file_count": len(managed),
                                                       "error_message": error_message}, event_type="failure", flow_id=flow_id)
        finally:
            self.__scanner.set_progress_callback(None)
            if callable(setter):
                setter(None)
        assert self.__queue is not None
        assert self.__queue is not None
        _publish_bounded_result(self.__queue, result)
        # Do not retain the completed graph in this long-lived coordinator.
        del result
        del files
        del malformed
        del managed
        delta_in_ms = int((datetime.now() - timestamp_start).total_seconds() * 1000)
        if self.verbose:
            self.logger.debug("Scan took {:.3f}s".format(float(delta_in_ms) / 1000.0))
        if delta_in_ms < self.__interval_in_ms:
            assert self.__wake_event is not None
            self.__wake_event.wait(timeout=float(self.__interval_in_ms - delta_in_ms) / 1000.0)
            self.__wake_event.clear()

    def __poll_scan_worker(self) -> None:
        worker = self.__scan_worker
        assert worker is not None
        started_at = self.__scan_worker_started_at
        worker.join(timeout=0.05)
        if worker.is_alive():
            status = self.__drain_scan_worker_messages()
            if status is not None:
                # A child can finish its control-pipe sends just before its
                # process object reports dead.  Retain the terminal status
                # until the normal teardown path applies it exactly once.
                self.__scan_worker_pending_status = status
            assert self.__wake_event is not None
            if self.__wake_event.wait(timeout=0.05):
                self.__wake_event.clear()
                self.__scan_worker_force_pending = True
            return

        try:
            # The child has exited, so its terminal status must be retained;
            # allow a short pipe-delivery window before tearing the endpoint
            # down (especially important on Windows spawn).
            status = self.__drain_scan_worker_messages(wait_timeout=1.0)
            if status is None:
                status = self.__scan_worker_pending_status
            self.__scan_worker_pending_status = None
            if isinstance(status, tuple) and len(status) >= 3:
                applier = getattr(self.__scanner, "apply_recycled_state", None)
                if callable(applier):
                    applier(status[2])
            if isinstance(status, tuple) and status[0] == "fatal":
                status[1].re_raise()
            error_message = status[1] if isinstance(status, tuple) and status[0] == "recoverable" else None
            if isinstance(error_message, str) and error_message != self.__last_recoverable_error_message:
                self.logger.warning("Recoverable scanner error; returning failed result: {}".format(error_message))
                self.__last_recoverable_error_message = error_message
            elif error_message is None:
                self.__last_recoverable_error_message = None
        finally:
            self.__teardown_scan_worker(terminate=False)

        delta_in_ms = int((datetime.now() - started_at).total_seconds() * 1000) if started_at is not None else 0
        if self.verbose:
            self.logger.debug("Scan took {:.3f}s".format(float(delta_in_ms) / 1000.0))
        if not self.__scan_worker_force_pending and delta_in_ms < self.__interval_in_ms:
            assert self.__wake_event is not None
            self.__wake_event.wait(timeout=float(self.__interval_in_ms - delta_in_ms) / 1000.0)
            self.__wake_event.clear()
        self.__scan_worker_force_pending = False

    def __drain_scan_worker_messages(self, wait_timeout: float = 0.0) -> object | None:
        """Forward bounded progress and the final aggregate from a child.

        The child sends result envelopes synchronously over the control pipe,
        so it cannot strand a multiprocessing.Queue feeder at interpreter
        shutdown.  The coordinator owns the local bounded queue and may drop
        intermediate progress when the model updater is busy; the final
        aggregate is marked as full/authoritative and replaces stale entries.
        """
        connection = self.__scan_worker_control_connection
        if connection is None:
            return None
        status = None
        first = True
        while True:
            try:
                ready = connection.poll(wait_timeout if first else 0.0)
                first = False
                if not ready:
                    break
                message = connection.recv()
            except (OSError, EOFError):
                break
            if isinstance(message, tuple) and len(message) == 2 and message[0] == "result":
                result = message[1]
                if isinstance(result, ScannerResult):
                    self.__ingest_duration_aggregates(result)
                    assert self.__queue is not None
                    _publish_bounded_result(self.__queue, result)
            else:
                status = message
        return status

    def __ingest_duration_aggregates(self, result: ScannerResult) -> None:
        diagnostics = self.__performance_diagnostics
        aggregates = result.duration_aggregates
        token = result.duration_aggregate_token
        if diagnostics is None or not isinstance(aggregates, dict) or not isinstance(token, tuple) or len(token) != 3:
            return
        if not isinstance(token[0], str) or type(token[1]) is not int or type(token[2]) is not int:
            return
        if token in self.__ingested_duration_tokens:
            return
        self.__ingested_duration_tokens.append(token)
        for metric, aggregate in aggregates.items():
            if isinstance(metric, str):
                diagnostics.observe_duration_aggregate(metric, aggregate, expected_generation=token[2])

    def __teardown_scan_worker(self, terminate: bool = True) -> None:
        worker = self.__scan_worker
        connection = self.__scan_worker_control_connection
        self.__scan_worker = None
        self.__scan_worker_control_connection = None
        self.__scan_worker_pending_status = None
        self.__scan_worker_started_at = None
        if connection is not None:
            connection.close()
        if worker is None:
            return
        if worker.pid is not None:
            if terminate and worker.is_alive():
                worker.terminate()
            worker.join(timeout=1)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
        if not worker.is_alive():
            worker.close()

    def close_queues(self) -> None:
        if self.__queue_is_multiprocessing and self.__queue is not None:
            self.__queue.close()
            self.__queue.join_thread()
        self.__queue = None
        self.__queue_is_multiprocessing = False
        self.__scan_target_queue = None
        self.__wake_event = None
        self._mp_log_queue = None
        self._mp_log_level = None

    def __trace_corr_id(self) -> str:
        return self.__trace_path_pair_id() or self.__scanner.__class__.__name__

    def __trace_path_pair_id(self) -> Optional[str]:
        path_pair_id = getattr(self.__scanner, "path_pair_id", None)
        return path_pair_id if isinstance(path_pair_id, str) else None

    def __trace_path_pair_name(self) -> Optional[str]:
        path_pair_name = getattr(self.__scanner, "path_pair_name", None)
        return path_pair_name if isinstance(path_pair_name, str) else None

    def __record_breadcrumb(self, message: str, details: dict[str, object], event_type: str = "state_transition",
                            corr_id: str | None = None, flow_id: str | None = None,
                            path_pair_id: str | None = None, path_pair_name: str | None = None) -> None:
        if self.__breadcrumb_trace is None:
            return
        self.__breadcrumb_trace.record(
            "scanner_process",
            message,
            details,
            stage="scan",
            event_type=event_type,
            corr_id=corr_id if corr_id is not None else self.__trace_corr_id(),
            flow_id=flow_id,
            path_pair_id=path_pair_id if path_pair_id is not None else self.__trace_path_pair_id(),
            path_pair_name=path_pair_name if path_pair_name is not None else self.__trace_path_pair_name(),
        )

    def pop_latest_result(self) -> Optional[ScannerResult]:
        """
        Process-safe method to retrieve latest scan result
        Returns None if no new scan result was generated since the last time
        this method was called
        :return:
        """
        latest_scan = None
        while True:
            try:
                assert self.__queue is not None
                item = self.__queue.get(block=False)
                if isinstance(item, _ScannerQueueReleaseMarker):
                    continue
                latest_scan = item
            except queue.Empty:
                break
            except (OSError, EOFError) as exc:
                self.logger.warning("Scanner queue read failed: {}".format(exc))
                return latest_scan
        return latest_scan

    def pop_results(self) -> List[ScannerResult]:
        """Drain queued scan events in publication order.

        ``pop_latest_result`` remains for legacy callers that intentionally
        coalesce snapshots.  Progressive reconciliation uses this bounded
        drain so a manifest and root batches cannot be dropped between ticks.
        """
        results: List[ScannerResult] = []
        while True:
            try:
                assert self.__queue is not None
                item = self.__queue.get(block=False)
                if isinstance(item, _ScannerQueueReleaseMarker):
                    continue
                if isinstance(item, ScannerResult):
                    results.append(item)
            except queue.Empty:
                break
            except (OSError, EOFError) as exc:
                self.logger.warning("Scanner queue read failed: {}".format(exc))
                break
        return results

    def force_scan(self, path_pair_id: Optional[str] = None) -> None:
        """Force process to wake and do an immediate scan"""
        assert self.__scan_target_queue is not None
        self.__scan_target_queue.put(path_pair_id)
        assert self.__wake_event is not None
        self.__wake_event.set()

    def __drain_scan_target_path_pair_ids(self) -> Optional[set[str]]:
        scan_target_path_pair_ids: set[str] = set()
        full_scan_requested = False
        while True:
            try:
                assert self.__scan_target_queue is not None
                scan_target_path_pair_id = self.__scan_target_queue.get(block=False)
            except queue.Empty:
                break
            except (OSError, EOFError) as exc:
                self.logger.warning("Scanner target queue read failed: {}".format(exc))
                break
            if scan_target_path_pair_id is None:
                full_scan_requested = True
            elif not full_scan_requested:
                scan_target_path_pair_ids.add(scan_target_path_pair_id)
        if full_scan_requested:
            return None
        if not scan_target_path_pair_ids:
            return None
        return scan_target_path_pair_ids
