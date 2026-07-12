# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
from abc import ABC, abstractmethod
import multiprocessing
from datetime import datetime
from typing import List, Optional, Protocol
import queue
from multiprocessing.queues import Queue as MPQueue
from multiprocessing.synchronize import Event as EventType

from common import overrides, AppProcess, AppError
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


class ScannerResult:
    """
    Results of a system scan
    """
    def __init__(self,
                 timestamp: datetime,
                 files: List[SystemFile],
                 malformed_status_only_file_ids: Optional[List[str]] = None,
                 managed_extract_file_ids: Optional[List[str]] = None,
                 failed: bool = False,
                 error_message: str | None = None):
        self.timestamp = timestamp
        self.files = files
        self.malformed_status_only_file_ids = [] if malformed_status_only_file_ids is None else malformed_status_only_file_ids
        self.managed_extract_file_ids = [] if managed_extract_file_ids is None else managed_extract_file_ids
        self.failed = failed
        self.error_message = error_message


class ScannerProcess(AppProcess):
    """
    Process to scan a file system and publish the result
    """
    def __init__(self,
                 scanner: IScanner, interval_in_ms: int,
                 verbose: bool = True,
                 breadcrumb_trace: Optional[_BreadcrumbEmitter] = None):
        """
        Create a scanner process
        :param scanner: IScanner implementation
        :param interval_in_ms: Minimum interval (in ms) between results
        """
        super().__init__(name=scanner.__class__.__name__)
        self.__queue: Optional[MPQueue[ScannerResult]] = multiprocessing.Queue()
        self.__scan_target_queue: Optional[MPQueue[Optional[str]]] = multiprocessing.Queue()
        self.__wake_event: Optional[EventType] = multiprocessing.Event()
        self.__scanner = scanner
        self.__interval_in_ms = interval_in_ms
        self.__last_recoverable_error_message: Optional[str] = None
        self.verbose = verbose
        self.__breadcrumb_trace = breadcrumb_trace

    @overrides(AppProcess)
    def run_init(self) -> None:
        # Set the base logger for scanner
        self.__scanner.set_base_logger(self.logger)

    @overrides(AppProcess)
    def run_cleanup(self) -> None:
        pass

    @overrides(AppProcess)
    def run_loop(self) -> None:
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
        set_scan_target_path_pair_ids = getattr(self.__scanner, "set_scan_target_path_pair_ids", None)
        if callable(set_scan_target_path_pair_ids):
            set_scan_target_path_pair_ids(scan_target_path_pair_ids)
        try:
            files = self.__scanner.scan()
            malformed_status_only_file_ids = self.__scanner.pop_malformed_status_only_file_ids()
            managed_extract_file_ids = self.__scanner.pop_managed_extract_file_ids()
            self.__last_recoverable_error_message = None
            result = ScannerResult(timestamp=timestamp_start,
                                   files=files,
                                   malformed_status_only_file_ids=malformed_status_only_file_ids,
                                   managed_extract_file_ids=managed_extract_file_ids)
            self.__record_breadcrumb(
                "scan_completed",
                {
                    "scanner": self.__scanner.__class__.__name__,
                    "file_count": len(files),
                    "malformed_status_only_file_count": len(malformed_status_only_file_ids),
                    "managed_extract_file_count": len(managed_extract_file_ids),
                },
                corr_id=self.__trace_corr_id(),
                flow_id=flow_id,
                path_pair_id=self.__trace_path_pair_id(),
                path_pair_name=self.__trace_path_pair_name(),
            )
        except ScannerError as e:
            # Non-recoverable errors continue up as a fatal error
            if not e.recoverable:
                self.__record_breadcrumb(
                    "scan_failed",
                    {
                        "scanner": self.__scanner.__class__.__name__,
                        "recoverable": False,
                        "error_message": str(e),
                    },
                    event_type="failure",
                    corr_id=self.__trace_corr_id(),
                    flow_id=flow_id,
                    path_pair_id=self.__trace_path_pair_id(),
                    path_pair_name=self.__trace_path_pair_name(),
                )
                raise
            error_message = str(e)
            files = e.files if e.files is not None else []
            malformed_status_only_file_ids = self.__scanner.pop_malformed_status_only_file_ids()
            managed_extract_file_ids = self.__scanner.pop_managed_extract_file_ids()
            if error_message != self.__last_recoverable_error_message:
                self.logger.warning(
                    "Recoverable scanner error; returning failed result: {}".format(error_message)
                )
                self.__last_recoverable_error_message = error_message
            result = ScannerResult(timestamp=timestamp_start,
                                   files=files,
                                   malformed_status_only_file_ids=malformed_status_only_file_ids,
                                   managed_extract_file_ids=managed_extract_file_ids,
                                   failed=True,
                                   error_message=error_message)
            self.__record_breadcrumb(
                "scan_failed",
                {
                    "scanner": self.__scanner.__class__.__name__,
                    "recoverable": True,
                    "file_count": len(files),
                    "malformed_status_only_file_count": len(malformed_status_only_file_ids),
                    "managed_extract_file_count": len(managed_extract_file_ids),
                    "error_message": error_message,
                },
                event_type="failure",
                corr_id=self.__trace_corr_id(),
                flow_id=flow_id,
                path_pair_id=self.__trace_path_pair_id(),
                path_pair_name=self.__trace_path_pair_name(),
            )
        finally:
            if callable(set_scan_target_path_pair_ids):
                set_scan_target_path_pair_ids(None)
        assert self.__queue is not None
        self.__queue.put(result)
        delta_in_s = (datetime.now() - timestamp_start).total_seconds()
        delta_in_ms = int(delta_in_s * 1000)
        if self.verbose:
            self.logger.debug("Scan took {:.3f}s".format(delta_in_s))

        # Wait until the next interval, or until a wake event is fired
        if delta_in_ms < self.__interval_in_ms:
            wait_time_in_s = float(self.__interval_in_ms - delta_in_ms) / 1000.0
            assert self.__wake_event is not None
            self.__wake_event.wait(timeout=wait_time_in_s)
            self.__wake_event.clear()

    @overrides(AppProcess)
    def close_queues(self) -> None:
        self.__queue = self._close_multiprocessing_queue(self.__queue)
        self.__scan_target_queue = self._close_multiprocessing_queue(self.__scan_target_queue)
        if self.__wake_event is not None:
            self.__wake_event = None
        super().close_queues()

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
                latest_scan = self.__queue.get(block=False)
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
