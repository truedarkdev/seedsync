# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
from typing import List, Optional
import multiprocessing
import queue

from .scanner_process import IScanner
from common import overrides, Constants
from system import SystemScanner, SystemScannerError, SystemFile


class _StatusFileScanner(SystemScanner):
    @staticmethod
    def status_file_size(content: str) -> Optional[int]:
        return SystemScanner._lftp_status_file_size(content)


class ActiveScanner(IScanner):
    """
    Scanner implementation to scan the active files only
    A caller sets the names of the active files that need to be scanned.
    A multiprocessing.Queue is used to store the names because the set and scan
    methods are called by different processes.
    """
    def __init__(self, local_path: str, use_temp_file: bool = False):
        self.__scanner = SystemScanner(local_path)
        self.__use_temp_file = use_temp_file
        if use_temp_file:
            self.__scanner.set_lftp_temp_suffix(Constants.LFTP_TEMP_FILE_SUFFIX)
        self.__active_files_queue: multiprocessing.Queue[List[str]] = multiprocessing.Queue()
        self.__active_files_queue_closed = False
        self.__active_files: List[str] = []  # latest state
        self.__malformed_status_only_file_ids: List[str] = []
        self.logger = logging.getLogger(self.__class__.__name__)

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild(self.__class__.__name__)

    def set_active_files(self, file_names: List[str]) -> None:
        """
        Set the list of active file names. Only these files will be scanned.
        :param file_names:
        :return:
        """
        self.__active_files_queue.put(file_names)

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        self.__malformed_status_only_file_ids = []
        # Grab the latest list of active files, if any
        try:
            # A freshly enqueued multiprocessing item can lag briefly behind
            # put() while the feeder thread publishes it to the pipe.
            self.__active_files = self.__active_files_queue.get(block=True, timeout=0.01)
            while True:
                self.__active_files = self.__active_files_queue.get(block=False)
        except queue.Empty:
            pass

        # Do the scan
        # self.logger.debug("Scanning files: {}".format(str(self.__active_files)))
        result: List[SystemFile] = []
        for file_name in self.__active_files:
            try:
                result.append(self.__scanner.scan_single(file_name))
            except SystemScannerError as ex:
                # Ignore errors here, file may have been deleted
                status_only_partial = self.__is_status_only_partial(file_name)
                if status_only_partial is True:
                    continue
                if status_only_partial is None:
                    self.__malformed_status_only_file_ids.append(file_name)
                self.logger.warning(str(ex))
        return result

    def pop_malformed_status_only_file_ids(self) -> List[str]:
        malformed_status_only_file_ids = self.__malformed_status_only_file_ids
        self.__malformed_status_only_file_ids = []
        return malformed_status_only_file_ids

    def close(self) -> None:
        if self.__active_files_queue_closed:
            return
        self.__active_files_queue.close()
        self.__active_files_queue.join_thread()
        self.__active_files_queue_closed = True

    def __is_status_only_partial(self, file_name: str) -> Optional[bool]:
        if not self.__use_temp_file:
            return False

        base_path = os.path.join(self.__scanner.path_to_scan, file_name)
        temp_path = base_path + Constants.LFTP_TEMP_FILE_SUFFIX
        if os.path.exists(base_path) or os.path.exists(temp_path):
            return False
        status_path = temp_path + ".lftp-pget-status"
        if not os.path.isfile(status_path):
            return False
        with open(status_path, "r") as handle:
            return True if _StatusFileScanner.status_file_size(handle.read()) is not None else None
