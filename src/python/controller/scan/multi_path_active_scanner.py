# Copyright 2024, RapidCopy Contributors, All rights reserved.

import logging
import multiprocessing
import queue
from typing import Dict, List, Optional, Tuple

from .scanner_process import IScanner
from common import overrides
from system import SystemFile, SystemScanner, SystemScannerError


class MultiPathActiveScanner(IScanner):
    """
    Scanner that routes active file scans to the correct local root.
    """

    def __init__(self, path_pair_paths: Dict[str, str]):
        self.logger = logging.getLogger("MultiPathActiveScanner")
        self.__scanners = {
            path_pair_id: SystemScanner(local_path)
            for path_pair_id, local_path in path_pair_paths.items()
        }
        self.__active_files_queue = multiprocessing.Queue()
        self.__active_files: List[Tuple[str, Optional[str], Optional[str]]] = []

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
                self.logger.warning(str(ex))
        return results
