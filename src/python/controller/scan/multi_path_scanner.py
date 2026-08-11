# Copyright 2024, RapidCopy Contributors, All rights reserved.

import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, List, Optional

from .scanner_process import IScanner, ScannerError, ScanProgressCallback
from .local_scanner import LocalScanner
from .remote_scanner import RemoteScanner
from common import overrides
from system import SystemFile


def _run_bounded_scan_tasks(scanners: List[object], scan_one: Callable[[object], object],
                            thread_name_prefix: str) -> List[object]:
    """Run one task per selected scanner with a bounded rolling worker pool.

    ``Executor.map`` submits the whole iterable up front and yields results in
    input order.  That ordering can leave later path pairs invisible when an
    early worker is slow or fails.  Keep at most four futures in flight, submit
    the next scanner as soon as any worker finishes, and restore input order
    only after every selected pair has had a chance to run.
    """
    if not scanners:
        return []

    max_workers = min(4, len(scanners))
    completed: dict[int, object] = {}
    pending = {}
    next_index = 0
    fatal_error: Optional[Exception] = None

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix) as executor:
        while next_index < max_workers:
            pending[executor.submit(scan_one, scanners[next_index])] = next_index
            next_index += 1

        while pending:
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                try:
                    completed[index] = future.result()
                except Exception as error:
                    fatal_error = error

            if fatal_error is not None:
                # A fatal worker error preserves the existing fail-fast
                # contract.  Cancel queued futures; running tasks are joined
                # by the executor context before the error is re-raised.
                for future in pending:
                    future.cancel()
                break

            for _ in done:
                if next_index < len(scanners):
                    pending[executor.submit(scan_one, scanners[next_index])] = next_index
                    next_index += 1

    if fatal_error is not None:
        raise fatal_error

    ordered_results: List[object] = []
    for index in range(len(scanners)):
        result = completed[index]
        ordered_results.append(result)
    return ordered_results


class MultiPathLocalScanner(IScanner):
    """
    Scanner that aggregates local scan results from multiple path pairs.
    """

    def __init__(self, scanners: List[LocalScanner]):
        self.logger = logging.getLogger("MultiPathLocalScanner")
        self.__scanners = scanners
        self.__scan_target_path_pair_ids: Optional[set[str]] = None
        self.__progress_callback: Optional[ScanProgressCallback] = None
        self.__failed_path_pair_ids: set[str | None] = set()

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("MultiPathLocalScanner")
        for scanner in self.__scanners:
            scanner.set_base_logger(self.logger)

    @overrides(IScanner)
    def set_progress_callback(self, callback: Optional[ScanProgressCallback]) -> None:
        self.__progress_callback = callback
        for scanner in self.__scanners:
            scanner.set_progress_callback(callback)

    def set_scan_target_path_pair_ids(self, path_pair_ids: Optional[set[str]]) -> None:
        self.__scan_target_path_pair_ids = None if path_pair_ids is None else set(path_pair_ids)

    @overrides(IScanner)
    def scanned_path_pair_ids(self) -> set[str | None]:
        if self.__scan_target_path_pair_ids is not None:
            return {
                scanner.path_pair_id for scanner in self.__scanners
                if scanner.path_pair_id in self.__scan_target_path_pair_ids
            }
        return {scanner.path_pair_id for scanner in self.__scanners}

    @overrides(IScanner)
    def failed_path_pair_ids(self) -> set[str | None]:
        return set(self.__failed_path_pair_ids)

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        all_files: List[SystemFile] = []
        recoverable_errors: List[str] = []
        self.__failed_path_pair_ids = set()
        scanners = [scanner for scanner in self.__scanners if self.__scan_target_path_pair_ids is None or
                    scanner.path_pair_id in self.__scan_target_path_pair_ids]

        def scan_one(scanner: LocalScanner) -> tuple[LocalScanner, List[SystemFile], Optional[ScannerError]]:
            try:
                return scanner, scanner.scan(), None
            except ScannerError as err:
                if not err.recoverable:
                    raise
                return scanner, err.files or [], err

        scan_results = _run_bounded_scan_tasks(scanners, scan_one, "local-scan")
        for scanner, files, err in scan_results:
            if err is not None:
                error_message = "Failed to scan local path for pair '{}': {}".format(
                    scanner.path_pair_name, str(err)
                )
                self.logger.warning(error_message)
                if not err.recoverable:
                    raise err
                self.__failed_path_pair_ids.add(scanner.path_pair_id)
                recoverable_errors.append(error_message)
            for system_file in files:
                system_file.path_pair_id = scanner.path_pair_id
                system_file.path_pair_name = scanner.path_pair_name
            all_files.extend(files)
        if recoverable_errors:
            raise ScannerError(
                "Local scan completed with recoverable errors: {}".format("; ".join(recoverable_errors)),
                recoverable=True,
                files=all_files,
            )
        return all_files

    def pop_managed_extract_file_ids(self) -> List[str]:
        managed_extract_file_ids: List[str] = []
        for scanner in self.__scanners:
            managed_extract_file_ids.extend(scanner.pop_managed_extract_file_ids())
        return sorted(set(managed_extract_file_ids))


class MultiPathRemoteScanner(IScanner):
    """
    Scanner that aggregates remote scan results from multiple path pairs.
    """

    def __init__(self, scanners: List[RemoteScanner]):
        self.logger = logging.getLogger("MultiPathRemoteScanner")
        self.__scanners = scanners
        self.__scan_target_path_pair_ids: Optional[set[str]] = None
        self.__failed_path_pair_ids: set[str | None] = set()

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("MultiPathRemoteScanner")
        for scanner in self.__scanners:
            scanner.set_base_logger(self.logger)

    def set_scan_target_path_pair_ids(self, path_pair_ids: Optional[set[str]]) -> None:
        self.__scan_target_path_pair_ids = None if path_pair_ids is None else set(path_pair_ids)

    @overrides(IScanner)
    def set_progress_callback(self, callback: Optional[ScanProgressCallback]) -> None:
        for scanner in self.__scanners:
            scanner.set_progress_callback(callback)

    @overrides(IScanner)
    def scanned_path_pair_ids(self) -> set[str | None]:
        if self.__scan_target_path_pair_ids is not None:
            return {
                scanner.path_pair_id for scanner in self.__scanners
                if scanner.path_pair_id in self.__scan_target_path_pair_ids
            }
        return {scanner.path_pair_id for scanner in self.__scanners}

    @overrides(IScanner)
    def failed_path_pair_ids(self) -> set[str | None]:
        return set(self.__failed_path_pair_ids)

    def export_recycled_state(self) -> tuple[object, ...]:
        return tuple(scanner.export_recycled_state() for scanner in self.__scanners)

    def apply_recycled_state(self, state: object) -> None:
        if not isinstance(state, tuple) or len(state) != len(self.__scanners):
            raise TypeError("Invalid recycled multi-path remote scanner state")
        for scanner, scanner_state in zip(self.__scanners, state):
            scanner.apply_recycled_state(scanner_state)

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        all_files: List[SystemFile] = []
        recoverable_errors: List[str] = []
        self.__failed_path_pair_ids = set()
        scanners = [scanner for scanner in self.__scanners if self.__scan_target_path_pair_ids is None or
                    scanner.path_pair_id in self.__scan_target_path_pair_ids]

        # RemoteScanner performs setup (including scanfs check/copy) and the
        # scan over one SSH transport.  Keep those operations serialized across
        # path pairs: multiple first-run transports to the same destination can
        # contend for one password prompt and leave later scans blocked for the
        # SSH timeout.  Results and progress therefore retain input order while
        # local scans continue to use the bounded worker pool above.
        for scanner in scanners:
            err: Optional[ScannerError] = None
            try:
                files = scanner.scan()
            except ScannerError as scan_error:
                if not scan_error.recoverable:
                    raise
                err = scan_error
                files = scan_error.files or []
            if err is not None:
                error_message = "Failed to scan remote path for pair '{}': {}".format(
                    scanner.path_pair_name, str(err)
                )
                self.logger.warning(error_message)
                if not err.recoverable:
                    raise err
                self.__failed_path_pair_ids.add(scanner.path_pair_id)
                recoverable_errors.append(error_message)
            for system_file in files:
                system_file.path_pair_id = scanner.path_pair_id
                system_file.path_pair_name = scanner.path_pair_name
            all_files.extend(files)
        if recoverable_errors:
            raise ScannerError(
                "Remote scan completed with recoverable errors: {}".format("; ".join(recoverable_errors)),
                recoverable=True,
                files=all_files
            )
        return all_files
