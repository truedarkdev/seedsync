# Copyright 2024, RapidCopy Contributors, All rights reserved.

import logging
from typing import List, Optional

from .scanner_process import IScanner, ScannerError
from .local_scanner import LocalScanner
from .remote_scanner import RemoteScanner
from common import overrides
from system import SystemFile


class MultiPathLocalScanner(IScanner):
    """
    Scanner that aggregates local scan results from multiple path pairs.
    """

    def __init__(self, scanners: List[LocalScanner]):
        self.logger = logging.getLogger("MultiPathLocalScanner")
        self.__scanners = scanners
        self.__scan_target_path_pair_ids: Optional[set[str]] = None

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("MultiPathLocalScanner")
        for scanner in self.__scanners:
            scanner.set_base_logger(self.logger)

    def set_scan_target_path_pair_ids(self, path_pair_ids: Optional[set[str]]) -> None:
        self.__scan_target_path_pair_ids = None if path_pair_ids is None else set(path_pair_ids)

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        all_files: List[SystemFile] = []
        for scanner in self.__scanners:
            if (
                self.__scan_target_path_pair_ids is not None
                and scanner.path_pair_id not in self.__scan_target_path_pair_ids
            ):
                continue
            try:
                files = scanner.scan()
                for system_file in files:
                    system_file.path_pair_id = scanner.path_pair_id
                    system_file.path_pair_name = scanner.path_pair_name
                all_files.extend(files)
            except ScannerError as err:
                self.logger.warning(
                    "Failed to scan local path for pair '{}': {}".format(scanner.path_pair_name, str(err))
                )
                if not err.recoverable:
                    raise
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

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("MultiPathRemoteScanner")
        for scanner in self.__scanners:
            scanner.set_base_logger(self.logger)

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        all_files: List[SystemFile] = []
        recoverable_errors: List[str] = []
        for scanner in self.__scanners:
            try:
                files = scanner.scan()
                for system_file in files:
                    system_file.path_pair_id = scanner.path_pair_id
                    system_file.path_pair_name = scanner.path_pair_name
                all_files.extend(files)
            except ScannerError as err:
                error_message = "Failed to scan remote path for pair '{}': {}".format(
                    scanner.path_pair_name,
                    str(err)
                )
                self.logger.warning(error_message)
                if not err.recoverable:
                    raise
                partial_files = err.files if err.files is not None else []
                for system_file in partial_files:
                    system_file.path_pair_id = scanner.path_pair_id
                    system_file.path_pair_name = scanner.path_pair_name
                all_files.extend(partial_files)
                recoverable_errors.append(error_message)
        if recoverable_errors:
            raise ScannerError(
                "Remote scan completed with recoverable errors: {}".format("; ".join(recoverable_errors)),
                recoverable=True,
                files=all_files
            )
        return all_files
