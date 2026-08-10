# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
from abc import ABC, abstractmethod
import multiprocessing
import threading
from datetime import datetime
from typing import List, Optional, Protocol
import queue
from multiprocessing.queues import Queue as MPQueue
from multiprocessing.synchronize import Event as EventType

from common import AppError
from common.app_process import ExceptionWrapper
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
                 error_message: str | None = None):
        self.timestamp = timestamp
        self.files = files
        self.malformed_status_only_file_ids = [] if malformed_status_only_file_ids is None else malformed_status_only_file_ids
        self.managed_extract_file_ids = [] if managed_extract_file_ids is None else managed_extract_file_ids
        self.scanned_path_pair_ids = {None} if scanned_path_pair_ids is None else scanned_path_pair_ids
        self.failed = failed
        self.error_message = error_message


class _ScannerQueueReleaseMarker:
    """Tiny queue item that lets the feeder release its prior large payload."""
    pass


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


def _run_scanner_once(scanner: IScanner, output_queue: MPQueue[ScannerResult | _ScannerQueueReleaseMarker],
                      scan_target_path_pair_ids: Optional[set[str]], control_connection: object,
                      breadcrumb_trace: Optional[_BreadcrumbEmitter], flow_id: str,
                      mp_log_queue: Optional[MPQueue[logging.LogRecord]], mp_log_level: Optional[int]) -> None:
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
    outcome: tuple[str, object | None] = ("success", None)
    try:
        scanner.set_base_logger(logger)
        if callable(setter):
            setter(scan_target_path_pair_ids)
        timestamp = datetime.now()
        try:
            files = scanner.scan()
            malformed = scanner.pop_malformed_status_only_file_ids()
            managed = scanner.pop_managed_extract_file_ids()
            result = ScannerResult(timestamp, files, malformed, managed, scanner.scanned_path_pair_ids())
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
            result = ScannerResult(timestamp, files, malformed, managed, failed=True, error_message=str(error))
            outcome = ("recoverable", str(error))
            _record_scan_breadcrumb(scanner, breadcrumb_trace, flow_id, "scan_failed",
                                    {"scanner": scanner.__class__.__name__, "recoverable": True,
                                     "file_count": len(files), "malformed_status_only_file_count": len(malformed),
                                     "managed_extract_file_count": len(managed), "error_message": str(error)}, "failure")
        output_queue.put(result)
        output_queue.put(_ScannerQueueReleaseMarker())
        # The parent-facing queue now owns the result; keep no completed scan
        # graph in the coordinator or in this child while it exits.
        del result
        del files
        del malformed
        del managed
    except Exception as error:
        logger.debug("Process caught an exception")
        outcome = ("fatal", ExceptionWrapper(error))
    finally:
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
            control_connection.send((outcome[0], outcome[1], recycled_state))
        finally:
            control_connection.close()


def _create_scanner_worker(scanner: IScanner, output_queue: MPQueue[ScannerResult | _ScannerQueueReleaseMarker],
                           scan_target_path_pair_ids: Optional[set[str]], control_connection: object,
                           breadcrumb_trace: Optional[_BreadcrumbEmitter], flow_id: str,
                           mp_log_queue: Optional[MPQueue[logging.LogRecord]], mp_log_level: Optional[int]) -> multiprocessing.Process:
    """Use spawn explicitly: coordinators can be forkserver children and threaded."""
    return multiprocessing.get_context("spawn").Process(
        name="{}ScanRun".format(scanner.__class__.__name__),
        target=_run_scanner_once,
        args=(scanner, output_queue, scan_target_path_pair_ids, control_connection, breadcrumb_trace, flow_id,
              mp_log_queue, mp_log_level),
    )


class ScannerProcess:
    """
    Process to scan a file system and publish the result
    """
    def __init__(self,
                 scanner: IScanner, interval_in_ms: int,
                 verbose: bool = True,
                 breadcrumb_trace: Optional[_BreadcrumbEmitter] = None,
                 recycle_scan_worker: bool = False):
        """
        Create a scanner process
        :param scanner: IScanner implementation
        :param interval_in_ms: Minimum interval (in ms) between results
        """
        self.name = scanner.__class__.__name__
        self.logger = logging.getLogger(self.name)
        # A recycled child crosses from a forkserver coordinator into an
        # explicit spawn child; the ordinary inline path keeps its old queue.
        self.__queue: Optional[MPQueue[ScannerResult | _ScannerQueueReleaseMarker]] = \
            multiprocessing.get_context("spawn").Queue() if recycle_scan_worker else queue.Queue()
        self.__queue_is_multiprocessing = recycle_scan_worker
        self.__scan_target_queue: Optional[queue.Queue[Optional[str]]] = queue.Queue()
        self.__wake_event: Optional[threading.Event] = threading.Event()
        self.__scanner = scanner
        self.__interval_in_ms = interval_in_ms
        self.__last_recoverable_error_message: Optional[str] = None
        self.verbose = verbose
        self.__breadcrumb_trace = breadcrumb_trace
        self.__recycle_scan_worker = recycle_scan_worker
        self.__scan_worker: Optional[multiprocessing.Process] = None
        self.__scan_worker_started_at: Optional[datetime] = None
        self.__scan_worker_control_connection: object | None = None
        self.__scan_worker_force_pending = False
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
        worker = _create_scanner_worker(self.__scanner, self.__queue, scan_target_path_pair_ids, send_connection,
                                        self.__breadcrumb_trace, flow_id, self._mp_log_queue, self._mp_log_level)
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
        try:
            files = self.__scanner.scan()
            malformed = self.__scanner.pop_malformed_status_only_file_ids()
            managed = self.__scanner.pop_managed_extract_file_ids()
            self.__last_recoverable_error_message = None
            result = ScannerResult(timestamp_start, files, malformed, managed, self.__scanner.scanned_path_pair_ids())
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
            result = ScannerResult(timestamp_start, files, malformed, managed, failed=True, error_message=error_message)
            self.__record_breadcrumb("scan_failed", {"scanner": self.__scanner.__class__.__name__,
                                                       "recoverable": True, "file_count": len(files),
                                                       "malformed_status_only_file_count": len(malformed),
                                                       "managed_extract_file_count": len(managed),
                                                       "error_message": error_message}, event_type="failure", flow_id=flow_id)
        finally:
            if callable(setter):
                setter(None)
        assert self.__queue is not None
        self.__queue.put(result)
        self.__queue.put(_ScannerQueueReleaseMarker())
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
            assert self.__wake_event is not None
            if self.__wake_event.wait(timeout=0.05):
                self.__wake_event.clear()
                self.__scan_worker_force_pending = True
            return

        try:
            connection = self.__scan_worker_control_connection
            try:
                status = connection.recv() if connection is not None and connection.poll() else None
            except (OSError, EOFError):
                status = None
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

    def __teardown_scan_worker(self, terminate: bool = True) -> None:
        worker = self.__scan_worker
        connection = self.__scan_worker_control_connection
        self.__scan_worker = None
        self.__scan_worker_control_connection = None
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
