# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
import logging
from dataclasses import dataclass
from typing import List, Optional, Set
import math

# my libs
from system import SystemFile
from lftp import LftpJobStatus
from model import ModelFile, Model, ModelError
from .extract import ExtractStatus, Extract
from .validate import ValidateStatus


@dataclass
class _RecentLiveTransferSnapshot:
    root_file_id: str
    size_local: Optional[int]
    percent_local: Optional[int]
    speed: Optional[int]
    eta: Optional[int]


class ModelBuilder:
    """
    ModelBuilder combines all the difference sources of file system info
    to build a model. These sources include:
      * downloading file system as a Dict[name, SystemFile]
      * local file system as a Dict[name, SystemFile]
      * remote file system as a Dict[name, SystemFile]
      * lftp status as Dict[name, LftpJobStatus]
    """
    def __init__(self):
        self.logger = logging.getLogger("ModelBuilder")
        self.__local_files = dict()
        self.__remote_files = dict()
        self.__lftp_statuses = dict()
        self.__recent_live_transfer_snapshots = dict()
        self.__retained_stopped_transfer_snapshots = dict()
        self.__downloaded_files = None
        self.__extract_statuses = dict()
        self.__extracted_files = set()
        self.__stopped_files = set()
        self.__validation_statuses = dict()
        self.__cached_model = None

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("ModelBuilder")

    @staticmethod
    def __root_file_id(name: str, path_pair_id: Optional[str]) -> str:
        return ModelFile.build_file_id(name, path_pair_id)

    @staticmethod
    def __model_file_matches_persisted_name(model_file: ModelFile, persisted_names: Optional[Set[str]]) -> bool:
        if persisted_names is None:
            return False
        return model_file.file_id in persisted_names or model_file.name in persisted_names

    @staticmethod
    def __candidate_stopped_file_ids(file_id: Optional[str],
                                     remote: Optional[SystemFile] = None,
                                     local: Optional[SystemFile] = None,
                                     status: Optional[LftpJobStatus] = None) -> Set[str]:
        candidate_ids = set()
        if file_id is not None:
            candidate_ids.add(file_id)

        path_pair_id = remote.path_pair_id if remote and remote.path_pair_id is not None else \
            local.path_pair_id if local and local.path_pair_id is not None else \
            status.path_pair_id if status and status.path_pair_id is not None else None
        if path_pair_id is None:
            return candidate_ids

        full_path = status.name if status is not None else \
            remote.name if remote is not None else \
            local.name if local is not None else None
        if full_path is None:
            return candidate_ids

        candidate_ids.add(ModelFile.build_file_id(full_path, path_pair_id))
        return candidate_ids

    def __is_stopped_file(self,
                          file_id: str,
                          remote: Optional[SystemFile] = None,
                          local: Optional[SystemFile] = None,
                          status: Optional[LftpJobStatus] = None) -> bool:
        if any(candidate_id in self.__stopped_files for candidate_id in
               self.__candidate_stopped_file_ids(file_id, remote, local, status)):
            return True
        if remote is None:
            remote = self.__remote_files.get(file_id)
        if local is None:
            local = self.__local_files.get(file_id)
        if remote is None and local is None and status is None:
            status = self.__lftp_statuses.get(file_id)
        if status is not None and status.name in self.__stopped_files:
            return True
        return (remote is not None and remote.name in self.__stopped_files) or \
               (local is not None and local.name in self.__stopped_files)

    @staticmethod
    def __apply_path_pair_metadata(model_file: ModelFile, path_pair_id: Optional[str], path_pair_name: Optional[str]):
        model_file.path_pair_id = path_pair_id
        model_file.path_pair_name = path_pair_name

    @staticmethod
    def __is_authoritative_local_file(local_file: Optional[SystemFile]) -> bool:
        return local_file is not None and not getattr(local_file, "is_staging", False)

    @staticmethod
    def __local_size_is_authoritative_progress(local_file: Optional[SystemFile],
                                               remote_file: Optional[SystemFile],
                                               retained_size_local: Optional[int]) -> bool:
        if not ModelBuilder.__is_authoritative_local_file(local_file):
            return False
        if local_file is None or local_file.size is None or retained_size_local is None:
            return False
        return local_file.size >= retained_size_local or \
            (remote_file is not None and remote_file.size is not None and local_file.size >= remote_file.size)

    @staticmethod
    def __normalize_download_progress(percent_local):
        if percent_local is None:
            return None
        if type(percent_local) == float:
            # Treat fractional values below 1.0 as 0-1 progress fractions.
            # Keep an exact 1.0 as a literal 1% reading rather than 100%.
            if percent_local < 1:
                return int(round(percent_local * 100))
            return int(round(percent_local))
        return percent_local

    def __store_recent_live_transfer_snapshot(self,
                                              file_id: str,
                                              root_file_id: str,
                                              transfer_state: LftpJobStatus.TransferState):
        snapshot = _RecentLiveTransferSnapshot(
            root_file_id=root_file_id,
            size_local=transfer_state.size_local,
            percent_local=ModelBuilder.__normalize_download_progress(transfer_state.percent_local),
            speed=transfer_state.speed,
            eta=transfer_state.eta
        )
        if snapshot.size_local is None:
            return
        self.__recent_live_transfer_snapshots[file_id] = snapshot

    def __store_retained_stopped_transfer_snapshot(self,
                                                   file_id: str,
                                                   root_file_id: str,
                                                   transfer_state: LftpJobStatus.TransferState):
        snapshot = _RecentLiveTransferSnapshot(
            root_file_id=root_file_id,
            size_local=transfer_state.size_local,
            percent_local=ModelBuilder.__normalize_download_progress(transfer_state.percent_local),
            speed=transfer_state.speed,
            eta=transfer_state.eta
        )
        if snapshot.size_local is None:
            return
        self.__retained_stopped_transfer_snapshots[file_id] = snapshot

    @staticmethod
    def __build_retained_transfer_state(size_local: Optional[int],
                                        size_remote: Optional[int],
                                        percent_local: Optional[int]) -> Optional[LftpJobStatus.TransferState]:
        if size_local is None:
            return None
        return LftpJobStatus.TransferState(
            size_local,
            size_remote,
            ModelBuilder.__normalize_download_progress(percent_local),
            None,
            None
        )

    def __sweep_recent_live_transfer_snapshots(self, seen_file_ids: Optional[Set[str]] = None):
        for file_id, snapshot in list(self.__recent_live_transfer_snapshots.items()):
            root_status = self.__lftp_statuses.get(snapshot.root_file_id)
            if self.__is_stopped_file(file_id) or self.__is_stopped_file(snapshot.root_file_id, status=root_status):
                if seen_file_ids is not None and file_id not in seen_file_ids:
                    self.__recent_live_transfer_snapshots.pop(file_id, None)
                continue
            if self.__lftp_statuses.get(snapshot.root_file_id) is not None:
                continue
            if seen_file_ids is not None and file_id not in seen_file_ids:
                self.__recent_live_transfer_snapshots.pop(file_id, None)

    def __evict_recent_live_transfer_snapshots(self, root_file_id: str):
        for file_id, snapshot in list(self.__recent_live_transfer_snapshots.items()):
            if snapshot.root_file_id == root_file_id:
                self.__recent_live_transfer_snapshots.pop(file_id, None)

    def __has_pending_recent_live_transfer_snapshots(self) -> bool:
        for file_id, snapshot in self.__recent_live_transfer_snapshots.items():
            root_status = self.__lftp_statuses.get(snapshot.root_file_id)
            if self.__is_stopped_file(file_id) or self.__is_stopped_file(snapshot.root_file_id, status=root_status):
                continue
            if self.__lftp_statuses.get(snapshot.root_file_id) is None:
                return True
        return False

    def __get_recent_live_transfer_state(self,
                                         file_id: str,
                                         remote: Optional[SystemFile],
                                         local: Optional[SystemFile],
                                         root_remote: Optional[SystemFile] = None,
                                         root_local: Optional[SystemFile] = None) -> Optional[LftpJobStatus.TransferState]:
        snapshot = self.__recent_live_transfer_snapshots.get(file_id)
        if snapshot is None:
            return None
        root_status = self.__lftp_statuses.get(snapshot.root_file_id)
        stop_remote = root_remote if root_remote is not None else remote
        stop_local = root_local if root_local is not None else local
        if self.__is_stopped_file(snapshot.root_file_id, stop_remote, stop_local, root_status):
            self.__recent_live_transfer_snapshots.pop(file_id, None)
            return None
        if self.__lftp_statuses.get(snapshot.root_file_id) is not None:
            return None
        if remote is None or local is None or local.size is None or snapshot.size_local is None:
            self.__recent_live_transfer_snapshots.pop(file_id, None)
            return None
        if self.__local_size_is_authoritative_progress(local, remote, snapshot.size_local):
            self.__recent_live_transfer_snapshots.pop(file_id, None)
            return None

        return LftpJobStatus.TransferState(
            snapshot.size_local,
            remote.size,
            snapshot.percent_local,
            snapshot.speed,
            snapshot.eta
        )

    @staticmethod
    def __has_clear_transfer_reset_signal(local: Optional[SystemFile],
                                          current_transfer_state: LftpJobStatus.TransferState,
                                          retained_snapshot: _RecentLiveTransferSnapshot) -> bool:
        normalized_percent = ModelBuilder.__normalize_download_progress(current_transfer_state.percent_local)
        if current_transfer_state.size_local == 0 or normalized_percent == 0:
            return True
        return ModelBuilder.__is_authoritative_local_file(local) and \
            local is not None and \
            local.size is not None and \
            retained_snapshot.size_local is not None and \
            local.size < retained_snapshot.size_local

    def __coalesce_retained_stopped_transfer_state(self,
                                                   file_id: str,
                                                   remote: Optional[SystemFile],
                                                   local: Optional[SystemFile],
                                                   current_transfer_state: LftpJobStatus.TransferState
                                                   ) -> LftpJobStatus.TransferState:
        retained_snapshot = self.__retained_stopped_transfer_snapshots.get(file_id)
        if retained_snapshot is None or retained_snapshot.size_local is None:
            return current_transfer_state
        if self.__has_clear_transfer_reset_signal(local, current_transfer_state, retained_snapshot):
            self.__retained_stopped_transfer_snapshots.pop(file_id, None)
            return current_transfer_state
        current_percent = ModelBuilder.__normalize_download_progress(current_transfer_state.percent_local)
        retained_percent = retained_snapshot.percent_local
        size_has_caught_up = current_transfer_state.size_local is not None and \
            current_transfer_state.size_local >= retained_snapshot.size_local
        percent_has_caught_up = retained_percent is None or \
            (current_percent is not None and current_percent >= retained_percent)
        if size_has_caught_up and percent_has_caught_up:
            self.__retained_stopped_transfer_snapshots.pop(file_id, None)
            return current_transfer_state
        coalesced_size_local = retained_snapshot.size_local
        if size_has_caught_up:
            coalesced_size_local = current_transfer_state.size_local
        coalesced_percent_local = retained_percent
        if percent_has_caught_up:
            coalesced_percent_local = current_percent
        return LftpJobStatus.TransferState(
            coalesced_size_local,
            remote.size if remote is not None else current_transfer_state.size_remote,
            coalesced_percent_local,
            current_transfer_state.speed,
            current_transfer_state.eta
        )

    def __get_retained_recent_transfer_state(self,
                                             file_id: str,
                                             remote: Optional[SystemFile],
                                             local: Optional[SystemFile]) -> Optional[LftpJobStatus.TransferState]:
        snapshot = self.__recent_live_transfer_snapshots.get(file_id)
        if snapshot is None:
            return None
        if snapshot.size_local is None:
            self.__recent_live_transfer_snapshots.pop(file_id, None)
            return None
        if remote is None:
            return self.__build_retained_transfer_state(snapshot.size_local, None, snapshot.percent_local)
        return self.__build_retained_transfer_state(snapshot.size_local, remote.size, snapshot.percent_local)

    def set_active_files(self, active_files: List[SystemFile]):
        # Update the local file state with this latest information
        for file in active_files:
            self.__local_files[self.__root_file_id(file.name, file.path_pair_id)] = file
        # Invalidate the cache
        if len(active_files) > 0:
            self.__cached_model = None

    def set_local_files(self, local_files: List[SystemFile]):
        prev_local_files = self.__local_files
        self.__local_files = {
            self.__root_file_id(file.name, file.path_pair_id): file for file in local_files
        }
        # Invalidate the cache
        if self.__local_files != prev_local_files:
            self.__cached_model = None

    def set_remote_files(self, remote_files: List[SystemFile]):
        prev_remote_files = self.__remote_files
        self.__remote_files = {
            self.__root_file_id(file.name, file.path_pair_id): file for file in remote_files
        }
        # Invalidate the cache
        if self.__remote_files != prev_remote_files:
            self.__cached_model = None

    def set_lftp_statuses(self, lftp_statuses: List[LftpJobStatus]):
        prev_lftp_statuses = self.__lftp_statuses
        self.__lftp_statuses = {file.file_id: file for file in lftp_statuses}
        # Invalidate the cache
        if self.__lftp_statuses != prev_lftp_statuses:
            self.__cached_model = None

    def set_downloaded_files(self, downloaded_files: Set[str]):
        prev_downloaded_files = self.__downloaded_files
        self.__downloaded_files = set(downloaded_files)
        # Invalidate the cache
        if self.__downloaded_files != prev_downloaded_files:
            self.__cached_model = None

    def set_extract_statuses(self, extract_statuses: List[ExtractStatus]):
        prev_extract_statuses = self.__extract_statuses
        self.__extract_statuses = {status.name: status for status in extract_statuses}
        # Invalidate the cache
        if self.__extract_statuses != prev_extract_statuses:
            self.__cached_model = None

    def set_extracted_files(self, extracted_files: Set[str]):
        prev_extracted_files = self.__extracted_files
        self.__extracted_files = extracted_files
        # Invalidate the cache
        if self.__extracted_files != prev_extracted_files:
            self.__cached_model = None

    def set_stopped_files(self, stopped_files: Set[str]):
        prev_stopped_files = self.__stopped_files
        self.__stopped_files = set(stopped_files)
        self.__sweep_recent_live_transfer_snapshots()
        # Invalidate the cache
        if self.__stopped_files != prev_stopped_files:
            self.__cached_model = None

    def set_validation_statuses(self, validation_statuses: List[ValidateStatus]):
        prev_validation_statuses = self.__validation_statuses
        self.__validation_statuses = {status.file_id: status for status in validation_statuses}
        if self.__validation_statuses != prev_validation_statuses:
            self.__cached_model = None

    def clear(self):
        self.__local_files.clear()
        self.__remote_files.clear()
        self.__lftp_statuses.clear()
        self.__recent_live_transfer_snapshots.clear()
        self.__retained_stopped_transfer_snapshots.clear()
        self.__downloaded_files = None
        self.__extract_statuses.clear()
        self.__extracted_files.clear()
        self.__stopped_files.clear()
        self.__validation_statuses.clear()
        self.__cached_model = None

    def has_changes(self) -> bool:
        """
        Returns true is model has changes and requires rebuild
        :return:
        """
        return self.__cached_model is None or self.__has_pending_recent_live_transfer_snapshots()

    def build_model(self) -> Model:
        if self.__cached_model is not None and not self.__has_pending_recent_live_transfer_snapshots():
            return self.__cached_model

        model = Model()
        model.set_base_logger(logging.getLogger("dummy"))  # ignore the logs for this temp model
        live_transferred_file_ids = set()
        seen_file_ids = set()
        all_file_ids = set().union(self.__local_files.keys(), self.__remote_files.keys())
        source_file_ids = set(self.__local_files.keys()).union(self.__remote_files.keys())
        for status_file_id in self.__lftp_statuses.keys():
            if status_file_id not in source_file_ids:
                all_file_ids.add(status_file_id)

        for file_id in all_file_ids:
            seen_file_ids.add(file_id)
            remote = self.__remote_files.get(file_id, None)
            local = self.__local_files.get(file_id, None)
            status = self.__lftp_statuses.get(file_id, None)
            is_stopped = self.__is_stopped_file(file_id, remote, local)
            name = remote.name if remote else local.name if local else file_id
            if remote is None and local is None and status is None:
                # this should never happen, but just in case
                raise ModelError("Zero sources have a file object")

            # sanity check between the sources
            is_dir = remote.is_dir if remote else local.is_dir if local else status.type == LftpJobStatus.Type.MIRROR
            if (remote and is_dir != remote.is_dir) or \
               (local and is_dir != local.is_dir) or \
               (status and is_dir != (status.type == LftpJobStatus.Type.MIRROR)):
                raise ModelError("Mismatch in is_dir between sources")

            def __fill_model_file(_model_file: ModelFile,
                                  _remote: Optional[SystemFile],
                                  _local: Optional[SystemFile],
                                  _transfer_state: Optional[LftpJobStatus.TransferState],
                                  _store_recent_snapshot: bool,
                                  _recent_snapshot_root_file_id: Optional[str]):
                # set local and remote sizes
                if _remote:
                    _model_file.remote_size = _remote.size
                if _local:
                    _model_file.local_size = _local.size

                # Note: no longer use lftp's file sizes
                #       they represent remaining size for resumed downloads

                # set the downloading speed and eta
                if _transfer_state:
                    if _store_recent_snapshot:
                        self.__store_recent_live_transfer_snapshot(
                            _model_file.file_id,
                            _recent_snapshot_root_file_id if _recent_snapshot_root_file_id is not None else _model_file.file_id,
                            _transfer_state
                        )
                    download_progress = ModelBuilder.__normalize_download_progress(_transfer_state.percent_local)
                    if download_progress is not None:
                        _model_file.download_progress = download_progress
                    if _transfer_state.size_local is not None:
                        _model_file.transferred_size = _transfer_state.size_local
                        live_transferred_file_ids.add(_model_file.file_id)
                    _model_file.downloading_speed = _transfer_state.speed
                    _model_file.eta = _transfer_state.eta

                # set the transferred size (only if file or dir exists on both ends)
                if _local and _remote:
                    if _model_file.is_dir:
                        if _model_file.transferred_size is None:
                            # dir transferred size is updated by child files
                            _model_file.transferred_size = 0
                    else:
                        if _model_file.transferred_size is None:
                            if self.__is_authoritative_local_file(_local):
                                _model_file.transferred_size = min(_local.size, _remote.size)

                        if _model_file.transferred_size is not None:
                            # also update all parent directories
                            _parent_file = _model_file.parent
                            while _parent_file is not None:
                                if _parent_file.file_id in live_transferred_file_ids:
                                    break
                                if _parent_file.transferred_size is None:
                                    _parent_file.transferred_size = 0
                                _parent_file.transferred_size += _model_file.transferred_size
                                _parent_file = _parent_file.parent

                # set the is_extractable flag
                if not _model_file.is_dir and Extract.is_archive_fast(_model_file.name):
                    _model_file.is_extractable = True
                    # Also set the flag for all of its parents
                    _parent_file = _model_file.parent
                    while _parent_file is not None:
                        _parent_file.is_extractable = True
                        _parent_file = _parent_file.parent

                # set the timestamps
                if _local:
                    if _local.timestamp_created:
                        _model_file.local_created_timestamp = _local.timestamp_created
                    if _local.timestamp_modified:
                        _model_file.local_modified_timestamp = _local.timestamp_modified
                if _remote:
                    if _remote.timestamp_created:
                        _model_file.remote_created_timestamp = _remote.timestamp_created
                    if _remote.timestamp_modified:
                        _model_file.remote_modified_timestamp = _remote.timestamp_modified

            model_file = ModelFile(name, is_dir)
            path_pair_id = remote.path_pair_id if remote and remote.path_pair_id is not None else \
                local.path_pair_id if local else status.path_pair_id if status else None
            path_pair_name = remote.path_pair_name if remote and remote.path_pair_name is not None else \
                local.path_pair_name if local else status.path_pair_name if status else None
            self.__apply_path_pair_metadata(model_file, path_pair_id, path_pair_name)
            # set the file state
            # for now we only set to Queued or Downloading
            # later after all children are built, we can set to Downloaded after performing a check
            recent_transfer_state = None
            retained_transfer_state = None
            raw_current_transfer_state = status.total_transfer_state if status and \
                status.state == LftpJobStatus.State.RUNNING else None
            current_transfer_state = raw_current_transfer_state if not is_stopped else None
            if is_stopped and raw_current_transfer_state is not None:
                retained_transfer_state = self.__build_retained_transfer_state(
                    raw_current_transfer_state.size_local,
                    remote.size if remote else None,
                    raw_current_transfer_state.percent_local
                )
                self.__store_recent_live_transfer_snapshot(
                    model_file.file_id,
                    status.file_id if status is not None else model_file.file_id,
                    raw_current_transfer_state
                )
                self.__store_retained_stopped_transfer_snapshot(
                    model_file.file_id,
                    status.file_id if status is not None else model_file.file_id,
                    raw_current_transfer_state
                )
            elif current_transfer_state is not None:
                current_transfer_state = self.__coalesce_retained_stopped_transfer_state(
                    file_id,
                    remote,
                    local,
                    current_transfer_state
                )
            if current_transfer_state is None and status is None and not is_stopped:
                recent_transfer_state = self.__get_recent_live_transfer_state(file_id, remote, local)
            if retained_transfer_state is None and status is None and is_stopped:
                retained_transfer_state = self.__get_retained_recent_transfer_state(file_id, remote, local)
            if status and not is_stopped:
                model_file.state = ModelFile.State.QUEUED if status.state == LftpJobStatus.State.QUEUED \
                                   else ModelFile.State.DOWNLOADING
                if status.state == LftpJobStatus.State.QUEUED:
                    self.__evict_recent_live_transfer_snapshots(status.file_id)
            elif recent_transfer_state:
                model_file.state = ModelFile.State.DOWNLOADING
            # fill the rest
            __fill_model_file(model_file,
                              remote,
                              local,
                              current_transfer_state if current_transfer_state is not None
                              else recent_transfer_state if recent_transfer_state is not None
                              else retained_transfer_state,
                              current_transfer_state is not None,
                              status.file_id if status is not None else None)

            # Traverse SystemFile children tree in BFS order
            # Store (remote, local, status, model_file) tuple in traversal frontier where remote and local
            # correspond to the same node in both remote and local SystemFile trees, status corresponds
            # to the LFTP status for the entire tree, and model_file corresponds to the generated ModelFile
            # for the pair
            # Note: in this case the frontier contains nodes that have already been process, it is
            #       merely used for traversing children
            frontier = []
            if remote or local:
                frontier.append((remote, local, status, model_file, remote, local))
            while frontier:
                _remote, _local, _status, _model_file, _root_remote, _root_local = frontier.pop(0)
                _remote_children = {sf.name: sf for sf in _remote.children} if _remote else {}
                _local_children = {sf.name: sf for sf in _local.children} if _local else {}
                _all_children_names = set().union(_remote_children.keys(), _local_children.keys())
                for _child_name in _all_children_names:
                    _remote_child = _remote_children.get(_child_name, None)
                    _local_child = _local_children.get(_child_name, None)
                    _is_dir = _remote_child.is_dir if _remote_child else _local_child.is_dir
                    # sanity check is_dir
                    if (_remote_child and _is_dir != _remote_child.is_dir) or \
                       (_local_child and _is_dir != _local_child.is_dir):
                        raise ModelError("Mismatch in is_dir between child sources")
                    _child_model_file = ModelFile(_child_name, _is_dir)
                    self.__apply_path_pair_metadata(
                        _child_model_file,
                        _model_file.path_pair_id,
                        _model_file.path_pair_name
                    )

                    # add it to the parent right away so we can access the full path
                    _model_file.add_child(_child_model_file)
                    seen_file_ids.add(_child_model_file.file_id)
                    _child_is_stopped = _child_model_file.file_id in self.__stopped_files

                    # Set the state, first matching criteria below decides state
                    #   child is a directory: Default
                    #   child is active: Downloading
                    #   child local_size >= remote_size: Downloaded
                    #   remote child exists and root is Queued or Downloading: Queued
                    #   Default
                    # Result:
                    #   subdirectories are always Default
                    #   downloading files are Downloading
                    #   finished files are Downloaded
                    #   Queued and Downloading root's unfinished files are Queued
                    #   Local-only files are Default
                    _child_current_transfer_state = None
                    _child_recent_transfer_state = None
                    if _status and _status.state == LftpJobStatus.State.RUNNING and \
                            not self.__is_stopped_file(_status.file_id, _root_remote, _root_local, _status) and \
                            not _child_is_stopped:
                        # Transfer states are in root-relative paths.
                        _child_status_path = "/".join(_child_model_file.full_path.split(os.sep)[1:])
                        _child_current_transfer_state = next((ts for n, ts in _status.get_active_file_transfer_states()
                                                             if n == _child_status_path), None)
                    if _child_current_transfer_state is None and _status is None:
                        _child_recent_transfer_state = self.__get_recent_live_transfer_state(
                            _child_model_file.file_id,
                            _remote_child,
                            _local_child,
                            _root_remote,
                            _root_local
                        )
                    if _is_dir:
                        _child_model_file.state = ModelFile.State.DEFAULT
                    elif _child_current_transfer_state:
                        _child_model_file.state = ModelFile.State.DOWNLOADING
                    elif _child_recent_transfer_state:
                        _child_model_file.state = ModelFile.State.DOWNLOADING
                    elif _remote_child and \
                            self.__is_authoritative_local_file(_local_child) and \
                            _local_child.size >= _remote_child.size:
                        _child_model_file.state = ModelFile.State.DOWNLOADED
                    elif _remote_child and not _child_is_stopped and \
                            model_file.state in (ModelFile.State.QUEUED, ModelFile.State.DOWNLOADING):
                        _child_model_file.state = ModelFile.State.QUEUED
                    else:
                        _child_model_file.state = ModelFile.State.DEFAULT

                    # fill the rest
                    __fill_model_file(_child_model_file,
                                      _remote_child,
                                      _local_child,
                                      _child_current_transfer_state if _child_current_transfer_state is not None
                                      else _child_recent_transfer_state,
                                      _child_current_transfer_state is not None,
                                      status.file_id if status is not None else None)
                    # add child to frontier
                    frontier.append((_remote_child, _local_child, _status, _child_model_file, _root_remote, _root_local))

            # estimate the ETA for the root if it's not available
            if model_file.state == ModelFile.State.DOWNLOADING and \
                    model_file.eta is None and \
                    model_file.downloading_speed is not None and \
                    model_file.downloading_speed > 0 and \
                    model_file.remote_size is not None and \
                    model_file.transferred_size is not None:
                # First-order estimate
                remaining_size = max(model_file.remote_size - model_file.transferred_size, 0)
                model_file.eta = int(math.ceil(remaining_size / model_file.downloading_speed))

            # now we can determine if root is Downloaded
            # root is Downloaded if all child remote files are Downloaded
            # again we use BFS to traverse
            if model_file.state == ModelFile.State.DEFAULT:
                if not model_file.is_dir and \
                        not (is_stopped and retained_transfer_state is not None) and \
                        model_file.local_size is not None and \
                        model_file.remote_size is not None and \
                        self.__is_authoritative_local_file(local) and \
                        model_file.local_size >= model_file.remote_size:
                    # root is a finished single file
                    model_file.state = ModelFile.State.DOWNLOADED
                elif not model_file.is_dir and \
                        model_file.local_size is not None and \
                        model_file.remote_size is None and \
                        self.__is_authoritative_local_file(local) and \
                        self.__model_file_matches_persisted_name(model_file, self.__downloaded_files):
                    # keep previously-downloaded local-only files recognizable
                    model_file.state = ModelFile.State.DOWNLOADED
                elif model_file.is_dir and model_file.remote_size is not None:
                    # root is a directory that also exists remotely
                    # check all the children
                    all_downloaded = True
                    has_downloadable_children = False
                    frontier = []
                    frontier += model_file.get_children()
                    while frontier:
                        _child_file = frontier.pop(0)
                        if not _child_file.is_dir and \
                                _child_file.remote_size is not None:
                            has_downloadable_children = True
                            if _child_file.state != ModelFile.State.DOWNLOADED:
                                all_downloaded = False
                                break
                        frontier += _child_file.get_children()
                    if has_downloadable_children and all_downloaded:
                        model_file.state = ModelFile.State.DOWNLOADED

            # next we determine if root was Deleted
            # root is Deleted if it does not exist locally, but was downloaded in the past
            if model_file.state == ModelFile.State.DEFAULT and \
                    model_file.local_size is None and \
                    self.__model_file_matches_persisted_name(model_file, self.__downloaded_files):
                model_file.state = ModelFile.State.DELETED

            # next we check if root is Extracting
            # root is Extracting if it's part of an extract status, in an expected state,
            # and exists locally
            # if root is NOT in an expected state, then ignore the extract status
            # and report a warning message, as this shouldn't be happening
            if model_file.name in self.__extract_statuses:
                extract_status = self.__extract_statuses[model_file.name]
                if model_file.is_dir != extract_status.is_dir:
                    raise ModelError("Mismatch in is_dir between file and extract status")
                if model_file.state in (
                    ModelFile.State.DEFAULT,
                    ModelFile.State.DOWNLOADED
                ) and model_file.local_size is not None:
                    model_file.state = ModelFile.State.EXTRACTING
                else:
                    if model_file.local_size is None:
                        self.logger.warning("File {} has extract status but doesn't exist locally!".format(
                            model_file.name
                        ))
                    else:
                        self.logger.warning("File {} has extract status but is in state {}".format(
                            model_file.name,
                            str(model_file.state)
                        ))

            # next we check if root is Extracted
            # root is Extracted if it is in Downloaded state and in extracted files list
            # Note: Default files aren't marked extracted because they can still be queued
            #       for download, and it doesn't make sense to queue after extracting
            #       If a Default file is extracted, it will return back to the Default state
            if self.__model_file_matches_persisted_name(model_file, self.__extracted_files) and \
                    model_file.state == ModelFile.State.DOWNLOADED:
                    model_file.state = ModelFile.State.EXTRACTED

            validation_status = self.__validation_statuses.get(model_file.file_id)
            if validation_status is not None and model_file.state in (
                    ModelFile.State.DEFAULT,
                    ModelFile.State.DOWNLOADED,
                    ModelFile.State.EXTRACTED,
                    ModelFile.State.VALIDATING,
                    ModelFile.State.VALIDATED,
                    ModelFile.State.CORRUPT
            ) and model_file.local_size is not None and model_file.remote_size is not None:
                model_file.state = validation_status.state
                model_file.validation_progress = validation_status.progress
                model_file.validation_error = validation_status.error
                model_file.corrupt_chunks = validation_status.corrupt_chunks

            model.add_file(model_file)

        self.__sweep_recent_live_transfer_snapshots(seen_file_ids)
        self.__cached_model = model
        return model
