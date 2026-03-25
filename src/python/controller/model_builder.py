# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
import logging
from typing import List, Optional, Set
import math

# my libs
from system import SystemFile
from lftp import LftpJobStatus
from model import ModelFile, Model, ModelError
from .extract import ExtractStatus, Extract
from .validate import ValidateStatus


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
        self.__downloaded_files = None
        self.__extract_statuses = dict()
        self.__extracted_files = set()
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
    def __apply_path_pair_metadata(model_file: ModelFile, path_pair_id: Optional[str], path_pair_name: Optional[str]):
        model_file.path_pair_id = path_pair_id
        model_file.path_pair_name = path_pair_name

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

    def set_validation_statuses(self, validation_statuses: List[ValidateStatus]):
        prev_validation_statuses = self.__validation_statuses
        self.__validation_statuses = {status.file_id: status for status in validation_statuses}
        if self.__validation_statuses != prev_validation_statuses:
            self.__cached_model = None

    def clear(self):
        self.__local_files.clear()
        self.__remote_files.clear()
        self.__lftp_statuses.clear()
        self.__downloaded_files = None
        self.__extract_statuses.clear()
        self.__extracted_files.clear()
        self.__validation_statuses.clear()
        self.__cached_model = None

    def has_changes(self) -> bool:
        """
        Returns true is model has changes and requires rebuild
        :return:
        """
        return self.__cached_model is None

    def build_model(self) -> Model:
        if self.__cached_model is not None:
            return self.__cached_model

        model = Model()
        model.set_base_logger(logging.getLogger("dummy"))  # ignore the logs for this temp model
        live_transferred_file_ids = set()
        all_file_ids = set().union(self.__local_files.keys(), self.__remote_files.keys())
        source_file_ids = set(self.__local_files.keys()).union(self.__remote_files.keys())
        for status_file_id in self.__lftp_statuses.keys():
            if status_file_id not in source_file_ids:
                all_file_ids.add(status_file_id)

        for file_id in all_file_ids:
            remote = self.__remote_files.get(file_id, None)
            local = self.__local_files.get(file_id, None)
            status = self.__lftp_statuses.get(file_id, None)
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
                                  _transfer_state: Optional[LftpJobStatus.TransferState]):
                # set local and remote sizes
                if _remote:
                    _model_file.remote_size = _remote.size
                if _local:
                    _model_file.local_size = _local.size

                # Note: no longer use lftp's file sizes
                #       they represent remaining size for resumed downloads

                # set the downloading speed and eta
                if _transfer_state:
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
                            _model_file.transferred_size = min(_local.size, _remote.size)

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
            if status:
                model_file.state = ModelFile.State.QUEUED if status.state == LftpJobStatus.State.QUEUED \
                                   else ModelFile.State.DOWNLOADING
            # fill the rest
            __fill_model_file(model_file,
                              remote,
                              local,
                              status.total_transfer_state if status and
                              status.state == LftpJobStatus.State.RUNNING
                              else None)

            # Traverse SystemFile children tree in BFS order
            # Store (remote, local, status, model_file) tuple in traversal frontier where remote and local
            # correspond to the same node in both remote and local SystemFile trees, status corresponds
            # to the LFTP status for the entire tree, and model_file corresponds to the generated ModelFile
            # for the pair
            # Note: in this case the frontier contains nodes that have already been process, it is
            #       merely used for traversing children
            frontier = []
            if remote or local:
                frontier.append((remote, local, status, model_file))
            while frontier:
                _remote, _local, _status, _model_file = frontier.pop(0)
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

                    # find the transfer state (if it exists) corresponding to this child
                    # Note: transfer states are in full paths
                    # Note2: transfer states don't include root path
                    _child_status_path = os.path.join(*(_child_model_file.full_path.split(os.sep)[1:]))
                    _child_transfer_state = None
                    if _status:
                        _child_transfer_state = next((ts for n, ts in _status.get_active_file_transfer_states()
                                                     if n == _child_status_path), None)
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
                    if _is_dir:
                        _child_model_file.state = ModelFile.State.DEFAULT
                    elif _child_transfer_state:
                        _child_model_file.state = ModelFile.State.DOWNLOADING
                    elif _remote_child and _local_child and _local_child.size >= _remote_child.size:
                        _child_model_file.state = ModelFile.State.DOWNLOADED
                    elif _remote_child and model_file.state in (ModelFile.State.QUEUED, ModelFile.State.DOWNLOADING):
                        _child_model_file.state = ModelFile.State.QUEUED
                    else:
                        _child_model_file.state = ModelFile.State.DEFAULT

                    # fill the rest
                    __fill_model_file(_child_model_file,
                                      _remote_child,
                                      _local_child,
                                      _child_transfer_state)
                    # add child to frontier
                    frontier.append((_remote_child, _local_child, _status, _child_model_file))

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
                        model_file.local_size is not None and \
                        model_file.remote_size is not None and \
                        model_file.local_size >= model_file.remote_size:
                    # root is a finished single file
                    model_file.state = ModelFile.State.DOWNLOADED
                elif not model_file.is_dir and \
                        model_file.local_size is not None and \
                        model_file.remote_size is None and \
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

        self.__cached_model = model
        return model
