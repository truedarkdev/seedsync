# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
from abc import ABC, abstractmethod
import multiprocessing
from datetime import datetime
from typing import Any, List, Optional, cast
import queue

from common import overrides, AppProcess, AppError
from system import SystemFile


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
    def set_base_logger(self, base_logger: logging.Logger):
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
                 breadcrumb_trace=None):
        """
        Create a scanner process
        :param scanner: IScanner implementation
        :param interval_in_ms: Minimum interval (in ms) between results
        """
        super().__init__(name=scanner.__class__.__name__)
        self.__queue: Any | None = multiprocessing.Queue()
        self.__wake_event: Any | None = multiprocessing.Event()
        self.__scanner = scanner
        self.__interval_in_ms = interval_in_ms
        self.__last_recoverable_error_message = None
        self.verbose = verbose
        self.__breadcrumb_trace = breadcrumb_trace

    @overrides(AppProcess)
    def run_init(self):
        # Set the base logger for scanner
        self.__scanner.set_base_logger(self.logger)

    @overrides(AppProcess)
    def run_cleanup(self):
        pass

    @overrides(AppProcess)
    def run_loop(self):
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
    def close_queues(self):
        if self.__queue is not None:
            self.__queue.close()
            self.__queue.join_thread()
            self.__queue = None
        if self.__wake_event is not None:
            self.__wake_event = None
        super().close_queues()

    def __trace_corr_id(self):
        return self.__trace_path_pair_id() or self.__scanner.__class__.__name__

    def __trace_path_pair_id(self):
        return cast(Optional[str], getattr(self.__scanner, "path_pair_id", None))

    def __trace_path_pair_name(self):
        return cast(Optional[str], getattr(self.__scanner, "path_pair_name", None))

    def __record_breadcrumb(self, message: str, details: dict, event_type: str = "state_transition",
                            corr_id: str | None = None, flow_id: str | None = None,
                            path_pair_id: str | None = None, path_pair_name: str | None = None):
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

    def force_scan(self):
        """Force process to wake and do an immediate scan"""
        assert self.__wake_event is not None
        self.__wake_event.set()
