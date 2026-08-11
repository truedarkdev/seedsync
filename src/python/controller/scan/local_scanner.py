# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
from typing import List, Optional

from .scanner_process import IScanner, ScannerError, ScanProgressCallback
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
                 path_pair_id: Optional[str] = None,
                 path_pair_name: Optional[str] = None):
        self.__local_path = local_path
        self.__staging_path = staging_path
        self.__scanner = SystemScanner(local_path)
        if use_temp_file:
            self.__scanner.set_lftp_temp_suffix(Constants.LFTP_TEMP_FILE_SUFFIX)
        self.__staging_scanner = None
        if self.__is_valid_scan_path(local_path) and isinstance(staging_path, str) and \
                self.__is_valid_scan_path(staging_path) and \
                self.__normalize_path(staging_path) != self.__normalize_path(local_path):
            self.__staging_scanner = SystemScanner(staging_path)
            if use_temp_file:
                self.__staging_scanner.set_lftp_temp_suffix(Constants.LFTP_TEMP_FILE_SUFFIX)
        self.logger = logging.getLogger("LocalScanner")
        self.__managed_extract_folders_enabled = managed_extract_folders_enabled
        self.__managed_extract_file_ids: set[str] = set()
        self.__path_pair_id = path_pair_id
        self.__path_pair_name = path_pair_name
        self.__progress_callback: Optional[ScanProgressCallback] = None

    @property
    def path_pair_id(self) -> Optional[str]:
        return self.__path_pair_id

    @property
    def path_pair_name(self) -> Optional[str]:
        return self.__path_pair_name

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("LocalScanner")

    @overrides(IScanner)
    def set_progress_callback(self, callback: Optional[ScanProgressCallback]) -> None:
        self.__progress_callback = callback

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        self.__managed_extract_file_ids = set()
        if not self.__is_valid_scan_path(self.__local_path):
            raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)
        if self.__progress_callback is not None:
            return self.__scan_progressively()
        try:
            result = self.__scanner.scan()
            if self.__scanner.scan_had_errors:
                raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=True)
        except SystemScannerError:
            self.logger.exception("Caught SystemScannerError")
            raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)
        except OSError:
            self.logger.exception("Caught local filesystem error")
            raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=True)

        if self.__managed_extract_folders_enabled:
            result = self.__prune_managed_extract_entries(result, self.__local_path)

        exclude_name = self.__get_nested_staging_name()
        if exclude_name is not None:
            result = [system_file for system_file in result if system_file.name != exclude_name]

        if self.__staging_scanner is not None:
            try:
                staging_result = self.__staging_scanner.scan()
                if self.__staging_scanner.scan_had_errors:
                    raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=True)
            except SystemScannerError:
                self.logger.exception("Caught SystemScannerError")
                raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)
            except OSError:
                self.logger.exception("Caught local staging filesystem error")
                raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=True)

            if self.__managed_extract_folders_enabled and self.__staging_path is not None:
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

    def __scan_progressively(self) -> List[SystemFile]:
        """Publish a manifest and bounded top-level root batches.

        Each root is still scanned locally with the existing recursive scanner;
        the callback only changes publication timing and does not alter the
        resulting tree, exclusions, staging merge, or managed-marker rules.
        """
        assert self.__progress_callback is not None
        try:
            self.__scanner.reset_scan_errors()
            if self.__staging_scanner is not None:
                self.__staging_scanner.reset_scan_errors()
            root_names = set(self.__scanner.root_names())
            staging_names: set[str] = set()
            if self.__staging_scanner is not None:
                staging_names = set(self.__staging_scanner.root_names())
            exclude_name = self.__get_nested_staging_name()
            if exclude_name is not None:
                root_names.discard(exclude_name)
                staging_names.discard(exclude_name)
            all_names = sorted(root_names.union(staging_names))
            aggregate_results: List[SystemFile] = []
            self.__progress_callback([], self.__path_pair_id, self.__path_pair_name, set(all_names), False)
            for root_name in all_names:
                result: Optional[SystemFile] = None
                if root_name in root_names:
                    result = self.__scanner.scan_single_if_present(root_name)
                    if result is not None and self.__managed_extract_folders_enabled:
                        pruned = self.__prune_managed_extract_entries([result], self.__local_path)
                        result = pruned[0] if pruned else None
                if root_name in staging_names and self.__staging_scanner is not None:
                    staging_result = self.__staging_scanner.scan_single_if_present(root_name)
                    if staging_result is not None:
                        if self.__managed_extract_folders_enabled and self.__staging_path is not None:
                            pruned = self.__prune_managed_extract_entries([staging_result], self.__staging_path)
                            staging_result = pruned[0] if pruned else None
                        if staging_result is not None:
                            self.__mark_staging_file_tree(staging_result)
                            result = staging_result if result is None else self.__merge_duplicate_local_entries(
                                result, staging_result
                            )
                if result is not None:
                    aggregate_results.append(result)
                    self.__progress_callback([result], self.__path_pair_id, self.__path_pair_name, None, False)
            if self.__scanner.scan_had_errors or (
                    self.__staging_scanner is not None and self.__staging_scanner.scan_had_errors):
                raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=True)
            self.__progress_callback([], self.__path_pair_id, self.__path_pair_name, None, True)
            return aggregate_results
        except SystemScannerError:
            self.logger.exception("Caught SystemScannerError")
            raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=False)
        except OSError:
            self.logger.exception("Caught local filesystem error")
            raise ScannerError(Localization.Error.LOCAL_SERVER_SCAN, recoverable=True)

    def pop_managed_extract_file_ids(self) -> List[str]:
        managed_extract_file_ids = sorted(self.__managed_extract_file_ids)
        self.__managed_extract_file_ids = set()
        return managed_extract_file_ids

    @staticmethod
    def __normalize_path(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    @staticmethod
    def __is_valid_scan_path(path: object) -> bool:
        return bool(isinstance(path, str) and path.strip() and os.path.isabs(path))

    def __get_nested_staging_name(self) -> Optional[str]:
        if not self.__staging_path:
            return None
        staging_parent = os.path.dirname(self.__normalize_path(self.__staging_path))
        if staging_parent != self.__normalize_path(self.__local_path):
            return None
        return os.path.basename(self.__staging_path.rstrip(os.sep))

    @staticmethod
    def __mark_staging_file_tree(system_file: SystemFile) -> None:
        system_file.is_staging = True
        for child in system_file.iter_children():
            LocalScanner.__mark_staging_file_tree(child)

    def __prune_managed_extract_entries(self, system_files: List[SystemFile], root_path: str) -> List[SystemFile]:
        pruned_files: List[SystemFile] = []
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
        for child in system_file.iter_children():
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

        pruned_children: List[SystemFile] = []
        for child in system_file.iter_children():
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
            mtime_ns=system_file.mtime_ns,
            is_staging=system_file.is_staging
        )
        cloned.path_pair_id = system_file.path_pair_id
        cloned.path_pair_name = system_file.path_pair_name
        cloned.status_sidecar_ready = system_file.status_sidecar_ready
        cloned.has_staging_collision = system_file.has_staging_collision
        for child in children if children is not None else system_file.iter_children():
            cloned.add_child(child)
        return cloned

    @staticmethod
    @staticmethod
    def __build_merged_directory(existing_file: SystemFile, staging_file: SystemFile) -> SystemFile:
        merged_children: List[SystemFile] = []
        staging_children_by_name = {child.name: child for child in staging_file.iter_children()}
        consumed_staging_names: set[str] = set()

        for existing_child in existing_file.iter_children():
            staging_child = staging_children_by_name.get(existing_child.name)
            if staging_child is None:
                merged_children.append(existing_child)
                continue
            consumed_staging_names.add(existing_child.name)
            merged_children.append(
                LocalScanner.__merge_duplicate_local_entries(existing_child, staging_child)
            )

        for staging_child in staging_file.iter_children():
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
            mtime_ns=existing_file.mtime_ns,
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
            # Keep the final-root shape, but record that an incompatible
            # staging residue still exists.  Treating this as an ordinary
            # final leaf would otherwise permit trusted exclusion/progress
            # even though publication must retain a type collision.
            merged_file = LocalScanner.__clone_system_file(existing_file)
            merged_file.has_staging_collision = True
            return merged_file
        # The final root is authoritative by location.  A split-root recovery
        # can contain a completed final leaf beside an older or sparse staging
        # leaf with the same relative identity; selecting by apparent size
        # would let preallocation or a stale sidecar replace final content in
        # the model.  Keep the final leaf regardless of its byte count.
        merged_file = LocalScanner.__clone_system_file(existing_file)
        merged_file.has_staging_collision = True
        return merged_file
