# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
from typing import List, Optional

from .scanner_process import IScanner, ScannerError
from common import overrides, Localization, Constants
from system import SystemScanner, SystemFile, SystemScannerError


class LocalScanner(IScanner):
    """
    Scanner implementation to scan the local filesystem
    """
    def __init__(self,
                 local_path: str,
                 use_temp_file: bool,
                 staging_path: Optional[str] = None,
                 path_pair_id: str = None,
                 path_pair_name: str = None):
        self.__local_path = local_path
        self.__staging_path = staging_path
        self.__scanner = SystemScanner(local_path)
        if use_temp_file:
            self.__scanner.set_lftp_temp_suffix(Constants.LFTP_TEMP_FILE_SUFFIX)
        self.__staging_scanner = None
        if staging_path and self.__normalize_path(staging_path) != self.__normalize_path(local_path):
            self.__staging_scanner = SystemScanner(staging_path)
            if use_temp_file:
                self.__staging_scanner.set_lftp_temp_suffix(Constants.LFTP_TEMP_FILE_SUFFIX)
        self.logger = logging.getLogger("LocalScanner")
        self.__path_pair_id = path_pair_id
        self.__path_pair_name = path_pair_name

    @property
    def path_pair_id(self) -> str:
        return self.__path_pair_id

    @property
    def path_pair_name(self) -> str:
        return self.__path_pair_name

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("LocalScanner")

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        try:
            result = self.__scanner.scan()
        except SystemScannerError:
            self.logger.exception("Caught SystemScannerError")
            raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)

        exclude_name = self.__get_nested_staging_name()
        if exclude_name is not None:
            result = [system_file for system_file in result if system_file.name != exclude_name]

        if self.__staging_scanner is not None:
            try:
                staging_result = self.__staging_scanner.scan()
            except SystemScannerError:
                self.logger.exception("Caught SystemScannerError")
                raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)

            local_names = {system_file.name: index for index, system_file in enumerate(result)}
            for staging_file in staging_result:
                self.__mark_staging_file_tree(staging_file)
                if staging_file.name not in local_names:
                    local_names[staging_file.name] = len(result)
                    result.append(staging_file)
                else:
                    result[local_names[staging_file.name]] = staging_file
        return result

    @staticmethod
    def __normalize_path(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def __get_nested_staging_name(self) -> Optional[str]:
        if not self.__staging_path:
            return None
        staging_parent = os.path.dirname(self.__normalize_path(self.__staging_path))
        if staging_parent != self.__normalize_path(self.__local_path):
            return None
        return os.path.basename(self.__staging_path.rstrip(os.sep))

    @staticmethod
    def __mark_staging_file_tree(system_file: SystemFile):
        system_file.is_staging = True
        for child in system_file.children:
            LocalScanner.__mark_staging_file_tree(child)
