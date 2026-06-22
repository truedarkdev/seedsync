# Copyright 2024, RapidCopy Contributors, All rights reserved.

import logging
import multiprocessing
import os
import queue
from typing import Dict, List, Optional, Tuple

from .scanner_process import IScanner
from common import overrides, Constants
from model import ModelFile
from system import SystemFile, SystemScanner, SystemScannerError


class MultiPathActiveScanner(IScanner):
    """
    Scanner that routes active file scans to the correct local root.
    """

    def __init__(self, path_pair_paths: Dict[str, str], use_temp_file: bool = False):
        self.logger = logging.getLogger("MultiPathActiveScanner")
        self.__use_temp_file = use_temp_file
        self.__scanners = {
            path_pair_id: SystemScanner(local_path)
            for path_pair_id, local_path in path_pair_paths.items()
        }
        if use_temp_file:
            for scanner in self.__scanners.values():
                scanner.set_lftp_temp_suffix(Constants.LFTP_TEMP_FILE_SUFFIX)
        self.__active_files_queue = multiprocessing.Queue()
        self.__active_files: List[Tuple[str, Optional[str], Optional[str]]] = []
        self.__malformed_status_only_file_ids = []

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("MultiPathActiveScanner")

    def set_active_files(self, files: List[Tuple[str, Optional[str], Optional[str]]]):
        """
        Set active files as tuples of (name, path_pair_id, path_pair_name).
        """
        self.__active_files_queue.put(files)

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        self.__malformed_status_only_file_ids = []
        try:
            while True:
                self.__active_files = self.__active_files_queue.get(block=False)
        except queue.Empty:
            pass

        results = []
        for file_name, path_pair_id, path_pair_name in self.__active_files:
            scanner = self.__scanners.get(path_pair_id)
            if scanner is None and path_pair_id is None and len(self.__scanners) == 1:
                scanner = next(iter(self.__scanners.values()))
            if scanner is None:
                self.logger.warning(
                    "Skipping active scan for '%s': no scanner for path pair '%s'",
                    file_name,
                    path_pair_id
                )
                continue
            try:
                system_file = scanner.scan_single(file_name)
                system_file.path_pair_id = path_pair_id
                system_file.path_pair_name = path_pair_name
                results.append(system_file)
            except SystemScannerError as ex:
                status_only_partial = self.__is_status_only_partial(scanner, file_name, path_pair_id)
                if status_only_partial is True:
                    continue
                if status_only_partial is None:
                    self.__malformed_status_only_file_ids.append(
                        ModelFile.build_file_id(file_name, path_pair_id)
                    )
                self.logger.warning(str(ex))
        return results

    def pop_malformed_status_only_file_ids(self) -> List[str]:
        malformed_status_only_file_ids = self.__malformed_status_only_file_ids
        self.__malformed_status_only_file_ids = []
        return malformed_status_only_file_ids

    def close(self):
        if self.__active_files_queue is None:
            return
        self.__active_files_queue.close()
        self.__active_files_queue.join_thread()
        self.__active_files_queue = None

    def __is_status_only_partial(
            self,
            scanner: SystemScanner,
            file_name: str,
            path_pair_id: Optional[str]
    ) -> Optional[bool]:
        if not self.__use_temp_file:
            return False

        base_path = os.path.join(scanner.path_to_scan, file_name)
        temp_path = base_path + Constants.LFTP_TEMP_FILE_SUFFIX
        if os.path.exists(base_path) or os.path.exists(temp_path):
            return False
        status_path = temp_path + ".lftp-pget-status"
        if not os.path.isfile(status_path):
            return False
        with open(status_path, "r") as handle:
            return True if SystemScanner._lftp_status_file_size(handle.read()) is not None else None
