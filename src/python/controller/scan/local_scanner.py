# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
from typing import List, Optional

from .scanner_process import IScanner, ScannerError
from common import overrides, Localization, Constants
from common.managed_extract import (
    is_managed_extract_marker_name,
    read_managed_extract_marker,
    resolve_managed_extract_file_id,
)
from system import SystemScanner, SystemFile, SystemScannerError


class LocalScanner(IScanner):
    """
    Scanner implementation to scan the local filesystem
    """
    def __init__(self,
                 local_path: str,
                 use_temp_file: bool,
                 staging_path: Optional[str] = None,
                 managed_extract_folders_enabled: bool = True,
                 path_pair_id: str = None,
                 path_pair_name: str = None):
        self.__local_path = local_path
        self.__staging_path = staging_path
        self.__scanner = SystemScanner(local_path)
        if use_temp_file:
            self.__scanner.set_lftp_temp_suffix(Constants.LFTP_TEMP_FILE_SUFFIX)
        self.__staging_scanner = None
        if self.__is_valid_scan_path(local_path) and self.__is_valid_scan_path(staging_path) and \
                self.__normalize_path(staging_path) != self.__normalize_path(local_path):
            self.__staging_scanner = SystemScanner(staging_path)
            if use_temp_file:
                self.__staging_scanner.set_lftp_temp_suffix(Constants.LFTP_TEMP_FILE_SUFFIX)
        self.logger = logging.getLogger("LocalScanner")
        self.__managed_extract_folders_enabled = managed_extract_folders_enabled
        self.__managed_extract_file_ids = set()
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
        self.__managed_extract_file_ids = set()
        if not self.__is_valid_scan_path(self.__local_path):
            raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)
        try:
            result = self.__scanner.scan()
        except SystemScannerError:
            self.logger.exception("Caught SystemScannerError")
            raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)

        if self.__managed_extract_folders_enabled:
            result = self.__prune_managed_extract_entries(result, self.__local_path)

        exclude_name = self.__get_nested_staging_name()
        if exclude_name is not None:
            result = [system_file for system_file in result if system_file.name != exclude_name]

        if self.__staging_scanner is not None:
            try:
                staging_result = self.__staging_scanner.scan()
            except SystemScannerError:
                self.logger.exception("Caught SystemScannerError")
                raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)

            if self.__managed_extract_folders_enabled:
                staging_result = self.__prune_managed_extract_entries(staging_result, self.__staging_path)

            local_names = {system_file.name: index for index, system_file in enumerate(result)}
            for staging_file in staging_result:
                self.__mark_staging_file_tree(staging_file)
                if staging_file.name not in local_names:
                    local_names[staging_file.name] = len(result)
                    result.append(staging_file)
                else:
                    existing_file = result[local_names[staging_file.name]]
                    result[local_names[staging_file.name]] = self.__merge_duplicate_local_entries(
                        existing_file,
                        staging_file
                    )
        return result

    def pop_managed_extract_file_ids(self) -> List[str]:
        managed_extract_file_ids = sorted(self.__managed_extract_file_ids)
        self.__managed_extract_file_ids = set()
        return managed_extract_file_ids

    @staticmethod
    def __normalize_path(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    @staticmethod
    def __is_valid_scan_path(path) -> bool:
        return isinstance(path, str) and path.strip() and os.path.isabs(path)

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

    def __prune_managed_extract_entries(self, system_files: List[SystemFile], root_path: str) -> List[SystemFile]:
        pruned_files = []
        for system_file in system_files:
            pruned_file = self.__prune_managed_extract_tree(
                system_file,
                os.path.join(root_path, system_file.name)
            )
            if pruned_file is not None:
                pruned_files.append(pruned_file)
        pruned_files.sort(key=lambda child: child.name)
        return pruned_files

    def __prune_managed_extract_tree(self, system_file: SystemFile, disk_path: str) -> Optional[SystemFile]:
        if not self.__managed_extract_folders_enabled:
            return system_file

        if is_managed_extract_marker_name(system_file.name):
            return None

        if not system_file.is_dir:
            return self.__clone_system_file(system_file)

        marker_child = None
        for child in system_file.children:
            if is_managed_extract_marker_name(child.name):
                marker_child = child
                break
        if marker_child is not None:
            marker_path = os.path.join(disk_path, marker_child.name)
            marker = read_managed_extract_marker(marker_path)
            managed_extract_file_id = resolve_managed_extract_file_id(marker) if marker is not None else None
            if managed_extract_file_id is not None:
                self.__managed_extract_file_ids.add(managed_extract_file_id)
                return None

        pruned_children = []
        for child in system_file.children:
            if marker_child is not None and child.name == marker_child.name:
                continue
            pruned_child = self.__prune_managed_extract_tree(
                child,
                os.path.join(disk_path, child.name)
            )
            if pruned_child is not None:
                pruned_children.append(pruned_child)
        return self.__clone_system_file(system_file, pruned_children)

    @staticmethod
    def __clone_system_file(system_file: SystemFile, children: Optional[List[SystemFile]] = None) -> SystemFile:
        cloned = SystemFile(
            system_file.name,
            sum(child.size for child in children) if children is not None else system_file.size,
            system_file.is_dir,
            time_created=system_file.timestamp_created,
            time_modified=system_file.timestamp_modified,
            is_staging=system_file.is_staging
        )
        cloned.path_pair_id = system_file.path_pair_id
        cloned.path_pair_name = system_file.path_pair_name
        cloned.status_sidecar_ready = system_file.status_sidecar_ready
        for child in children if children is not None else system_file.children:
            cloned.add_child(child)
        return cloned

    @staticmethod
    def __should_prefer_existing_local_file(existing_file: SystemFile, staging_file: SystemFile) -> bool:
        return not existing_file.is_staging and \
            not existing_file.is_dir and \
            not staging_file.is_dir and \
            existing_file.size >= staging_file.size

    @staticmethod
    def __build_merged_directory(existing_file: SystemFile, staging_file: SystemFile) -> SystemFile:
        merged_children = []
        staging_children_by_name = {child.name: child for child in staging_file.children}
        consumed_staging_names = set()

        for existing_child in existing_file.children:
            staging_child = staging_children_by_name.get(existing_child.name)
            if staging_child is None:
                merged_children.append(existing_child)
                continue
            consumed_staging_names.add(existing_child.name)
            merged_children.append(
                LocalScanner.__merge_duplicate_local_entries(existing_child, staging_child)
            )

        for staging_child in staging_file.children:
            if staging_child.name in consumed_staging_names:
                continue
            merged_children.append(staging_child)

        merged_children.sort(key=lambda child: child.name)
        merged_file = SystemFile(
            existing_file.name,
            sum(child.size for child in merged_children),
            True,
            time_created=existing_file.timestamp_created,
            time_modified=existing_file.timestamp_modified,
            is_staging=False
        )
        merged_file.path_pair_id = existing_file.path_pair_id
        merged_file.path_pair_name = existing_file.path_pair_name
        for child in merged_children:
            merged_file.add_child(child)
        return merged_file

    @staticmethod
    def __merge_duplicate_local_entries(existing_file: SystemFile, staging_file: SystemFile) -> SystemFile:
        if existing_file.is_dir and staging_file.is_dir:
            return LocalScanner.__build_merged_directory(existing_file, staging_file)
        if existing_file.is_dir != staging_file.is_dir:
            return existing_file
        if LocalScanner.__should_prefer_existing_local_file(existing_file, staging_file):
            return existing_file
        return staging_file
