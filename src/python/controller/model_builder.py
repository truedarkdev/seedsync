# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
import logging
from datetime import datetime
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, NamedTuple, Optional, Set, Tuple, cast
import math
import json

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


class _TransferState(NamedTuple):
    size_local: Optional[int]
    size_remote: Optional[int]
    percent_local: Optional[int | float]
    speed: Optional[int]
    eta: Optional[int]


@dataclass
class _BuiltRootFile:
    model_file: ModelFile
    normalized_local_root_path: Optional[str]
    is_local_only: bool
    seen_file_ids: set[str]


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
        self.__stop_resume_trace_logger = self.logger.getChild("StopResumeTrace")
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        self.__target_archive_trace_file_id = os.environ.get("SEEDSYNC_TARGET_ARCHIVE_TRACE_FILE_ID")
        if self.__target_archive_trace_file_id is not None and not self.__target_archive_trace_file_id.strip():
            self.__target_archive_trace_file_id = None
        self.__local_files: dict[str, SystemFile] = {}
        self.__active_files: dict[str, SystemFile] = {}
        self.__remote_files: dict[str, SystemFile] = {}
        self.__active_file_ids: set[str] = set()
        self.__lftp_statuses: dict[str, LftpJobStatus] = {}
        self.__recent_live_transfer_snapshots: dict[str, _RecentLiveTransferSnapshot] = {}
        self.__retained_stopped_transfer_snapshots: dict[str, _RecentLiveTransferSnapshot] = {}
        self.__downloaded_files: Optional[set[str]] = None
        self.__downloaded_timestamps: dict[str, float] = {}
        self.__extract_statuses: dict[str, ExtractStatus] = {}
        self.__extracted_files: set[str] = set()
        self.__stopped_files: set[str] = set()
        self.__validation_statuses: dict[str, ValidateStatus] = {}
        self.__move_failed_files: set[str] = set()
        self.__final_move_succeeded_files: set[str] = set()
        self.__local_root_paths: dict[Optional[str], str] = {}
        self.__local_staging_paths: dict[Optional[str], str] = {}
        self.__suppressed_ambiguous_extracted_file_names: set[str] = set()
        self.__cached_model: Optional[Model] = None
        self.__stop_resume_trace_file_id: Optional[str] = None
        self.__stop_resume_trace_cycle_id: Optional[int] = None
        self.__stop_resume_trace_emitted = False
        self.__stop_resume_trace_last_idle_signature: Optional[str] = None
        self.__target_archive_trace_last_signature: Optional[str] = None

    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("ModelBuilder")
        self.__stop_resume_trace_logger = self.logger.getChild("StopResumeTrace")
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")

    @staticmethod
    def __build_dummy_model_logger() -> logging.Logger:
        logger = logging.Logger("dummy.Model")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    def set_stop_resume_trace_file_id(self, file_id: Optional[str]) -> None:
        self.__stop_resume_trace_file_id = file_id.strip() if file_id is not None and file_id.strip() else None
        self.__stop_resume_trace_last_idle_signature = None

    def __is_stop_resume_trace_enabled(self) -> bool:
        return self.__stop_resume_trace_file_id is not None

    @staticmethod
    def __extract_trace_selector_name(identifier: Optional[str]) -> Optional[str]:
        if identifier is None:
            return None
        try:
            parsed_identifier = json.loads(identifier)
        except (TypeError, ValueError, json.JSONDecodeError):
            return identifier
        if isinstance(parsed_identifier, list):
            parsed_items = cast(list[object], parsed_identifier)
            if len(parsed_items) == 2 and isinstance(parsed_items[1], str):
                return parsed_items[1]
        return identifier

    def __is_target_archive_trace_enabled(self) -> bool:
        return self.__target_archive_trace_file_id is not None

    def __trace_target_archive_selector_matches_model_file(self, model_file: ModelFile, root_file_id: str) -> bool:
        if not self.__is_target_archive_trace_enabled():
            return False
        if self.__target_archive_trace_file_id == model_file.file_id:
            return True
        if model_file.file_id != root_file_id:
            return False
        selector_name = self.__extract_trace_selector_name(self.__target_archive_trace_file_id)
        return selector_name == model_file.name

    def __trace_selector_matches_model_file(self, model_file: ModelFile, root_file_id: str) -> bool:
        if not self.__is_stop_resume_trace_enabled():
            return False
        if self.__stop_resume_trace_file_id == model_file.file_id:
            return True
        if model_file.file_id != root_file_id:
            return False
        selector_name = self.__extract_trace_selector_name(self.__stop_resume_trace_file_id)
        return selector_name == model_file.name

    @staticmethod
    def __summarize_target_archive_source(arbitration_source: str,
                                          model_file: ModelFile,
                                          local: Optional[SystemFile],
                                          transfer_state: Optional[_TransferState]) -> str:
        if arbitration_source in (
            "recent_live_snapshot",
            "live_status",
        ):
            return "live_transfer"
        if arbitration_source in (
            "retained_recent_live_snapshot",
            "retained_stopped_snapshot",
            "retained_stopped_snapshot_from_live_status",
            "retained_stopped_snapshot_without_live_progress",
            "live_status_coalesced_with_retained_floor",
        ):
            return "retained_snapshot"
        if arbitration_source == "staging_completion_without_live_status":
            return "staging_only"
        if arbitration_source == "suppressed_by_authoritative_local_completion":
            return "authoritative_local"
        if model_file.state == ModelFile.State.EXTRACTED:
            return "persisted_extracted"
        if model_file.state == ModelFile.State.DOWNLOADED:
            return "persisted_downloaded"
        if ModelBuilder.__is_authoritative_local_file(local):
            return "authoritative_local"
        if transfer_state is not None:
            return "live_transfer"
        return "scan_only"

    def __trace_target_archive_event(self, event: str, payload: dict[str, object]) -> None:
        if not self.__is_target_archive_trace_enabled():
            return
        trace_payload: dict[str, object] = {
            "event": event,
            "target_selector": self.__target_archive_trace_file_id,
        }
        trace_payload.update(payload)
        signature = json.dumps(trace_payload, sort_keys=True)
        if signature == self.__target_archive_trace_last_signature:
            return
        self.__target_archive_trace_last_signature = signature
        self.__target_archive_trace_logger.info("target_archive_trace %s", signature)

    def __trace_target_archive_arbitration(self,
                                           model_file: ModelFile,
                                           root_file_id: str,
                                           is_stopped: bool,
                                           remote_present: bool,
                                           local_present: bool,
                                           local: Optional[SystemFile],
                                           active_present: bool,
                                           local_freshness: str,
                                           status: Optional[LftpJobStatus],
                                           transfer_state: Optional[_TransferState],
                                           arbitration_source: str) -> None:
        if not self.__trace_target_archive_selector_matches_model_file(model_file, root_file_id):
            return

        self.__trace_target_archive_event("arbitration", {
            "model_source": "rebuilt",
            "source_kind": ModelBuilder.__summarize_target_archive_source(
                arbitration_source,
                model_file,
                local,
                transfer_state,
            ),
            "local_freshness": local_freshness,
            "resolved_identity": {
                "file_id": model_file.file_id,
                "root_file_id": root_file_id,
                "path_pair_id": model_file.path_pair_id,
                "path_pair_name": model_file.path_pair_name,
            },
            "markers": {
                "downloaded": self.__model_file_matches_persisted_name(model_file, self.__downloaded_files),
                "extracted": self.__model_file_matches_persisted_name(model_file, self.__extracted_files),
                "stopped": is_stopped,
            },
            "raw_lftp_status": {
                "state": ModelBuilder.__enum_name(status.state) if status is not None else None,
                "job_id": status.id if status is not None else None,
                "file_id": status.file_id if status is not None else None,
                "transfer": ModelBuilder.__summarize_transfer_state(transfer_state),
            },
            "matched_local": {
                "name": local.name if local is not None else None,
                "path": self.__resolve_local_disk_path(model_file, local),
            },
            "local_data_role": ModelBuilder.__summarize_local_data_role(
                model_file,
                local,
                transfer_state,
                arbitration_source,
            ),
            "local_size_apparent": local.size if local is not None else None,
            "local_size_allocated": self.__get_allocated_local_size(model_file, local),
            "presence": {
                "remote": remote_present,
                "local": local_present,
                "active": active_present,
                "live_transfer": transfer_state is not None,
            },
            "arbitration_source": arbitration_source,
            "final_model": ModelBuilder.__summarize_rendered_model(model_file),
        })

    def begin_stop_resume_trace_cycle(self, cycle_id: int) -> None:
        self.__stop_resume_trace_cycle_id = cycle_id
        self.__stop_resume_trace_emitted = False

    def finish_stop_resume_trace_cycle(self, model: Model, build_triggered: bool) -> None:
        if not self.__is_stop_resume_trace_enabled() or self.__stop_resume_trace_emitted:
            return
        target_file = None
        try:
            assert self.__stop_resume_trace_file_id is not None
            target_file = model.get_file(self.__stop_resume_trace_file_id)
        except ModelError:
            for file_id in model.get_file_ids():
                candidate_file = model.get_file(file_id)
                if self.__trace_selector_matches_model_file(candidate_file, candidate_file.file_id):
                    target_file = candidate_file
                    break

        event = "target_not_rendered" if build_triggered else "no_rebuild"
        payload: dict[str, object] = {
            "model_source": "rebuilt" if build_triggered else "cached",
            "target_file_id": self.__stop_resume_trace_file_id,
            "final_model": self.__summarize_rendered_model(target_file),
        }
        idle_signature = json.dumps({
            "event": event,
            "payload": payload,
        }, sort_keys=True)
        if idle_signature == self.__stop_resume_trace_last_idle_signature:
            return
        self.__stop_resume_trace_last_idle_signature = idle_signature
        self.__trace_cycle_event(event, payload)

    @staticmethod
    def __root_file_id(name: str, path_pair_id: Optional[str]) -> str:
        return ModelFile.build_file_id(name, path_pair_id)

    @staticmethod
    def __model_file_matches_persisted_name(model_file: ModelFile, persisted_names: Optional[set[str]]) -> bool:
        if persisted_names is None:
            return False
        if model_file.file_id in persisted_names:
            return True
        return False

    @staticmethod
    def __extract_status_key(status: ExtractStatus) -> str:
        if status.file_id is not None:
            return status.file_id
        return ModelFile.build_file_id(status.name, status.path_pair_id)

    @staticmethod
    def __candidate_stopped_file_ids(file_id: Optional[str],
                                     remote: Optional[SystemFile] = None,
                                     local: Optional[SystemFile] = None,
                                     status: Optional[LftpJobStatus] = None) -> set[str]:
        candidate_ids: set[str] = set()
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
    def __apply_path_pair_metadata(
        model_file: ModelFile, path_pair_id: Optional[str], path_pair_name: Optional[str]
    ) -> None:
        model_file.path_pair_id = path_pair_id
        model_file.path_pair_name = path_pair_name

    @staticmethod
    def __enum_name(value: Optional[Enum]) -> Optional[str]:
        return value.name if value is not None else None

    @staticmethod
    def __collect_active_file_ids(system_file: SystemFile,
                                  file_ids: set[str],
                                  parent_path: Optional[str] = None,
                                  path_pair_id: Optional[str] = None) -> None:
        current_path = system_file.name if parent_path is None else os.path.join(parent_path, system_file.name)
        effective_path_pair_id = system_file.path_pair_id if system_file.path_pair_id is not None else path_pair_id
        file_ids.add(ModelFile.build_file_id(current_path, effective_path_pair_id))
        for child in system_file.children:
            ModelBuilder.__collect_active_file_ids(child, file_ids, current_path, effective_path_pair_id)

    @staticmethod
    def __summarize_transfer_state(
        transfer_state: Optional[_TransferState]
    ) -> Optional[dict[str, object]]:
        if transfer_state is None:
            return None
        return {
            "size_local": transfer_state.size_local,
            "size_remote": transfer_state.size_remote,
            "percent_local": transfer_state.percent_local,
            "speed": transfer_state.speed,
            "eta": transfer_state.eta,
        }

    @staticmethod
    def __summarize_rendered_model(model_file: Optional[ModelFile]) -> Optional[dict[str, object]]:
        if model_file is None:
            return None
        return {
            "state": ModelBuilder.__enum_name(model_file.state),
            "transferred_size": model_file.transferred_size,
            "download_progress": model_file.download_progress,
            "downloading_speed": model_file.downloading_speed,
            "eta": model_file.eta,
        }

    @staticmethod
    def __summarize_local_freshness(local_file: Optional[SystemFile]) -> str:
        if local_file is None:
            return "missing"
        return "staging" if getattr(local_file, "is_staging", False) else "authoritative"

    @staticmethod
    def __summarize_local_data_role(model_file: Optional[ModelFile],
                                    local_file: Optional[SystemFile],
                                    transfer_state: Optional[_TransferState],
                                    arbitration_source: str) -> Optional[str]:
        if local_file is None or model_file is None:
            return None
        if arbitration_source in (
            "suppressed_by_authoritative_local_completion",
            "suppressed_by_staging_completion_after_live_status_lost",
            "staging_completion_without_live_status",
        ):
            return "completion"
        if ModelBuilder.__is_authoritative_local_file(local_file) and \
                transfer_state is None and \
                model_file.transferred_size is not None and \
                model_file.transferred_size == local_file.size and \
                model_file.state != ModelFile.State.DOWNLOADED:
            return "progress"
        return "presence"

    @staticmethod
    def __is_stoppable_model_file(model_file: ModelFile,
                                  local_file: Optional[SystemFile],
                                  current_transfer_state: Optional[_TransferState]) -> bool:
        if model_file.state == ModelFile.State.QUEUED:
            return True
        if model_file.state != ModelFile.State.DOWNLOADING:
            return False
        if current_transfer_state is None:
            return False
        if model_file.is_dir:
            return True
        # Require the pget status sidecar before STOP can cut a live file download.
        return local_file is not None and getattr(local_file, "status_sidecar_ready", False)

    def set_local_root_paths(self,
                             local_root_paths: Dict[Optional[str], str],
                             local_staging_paths: Optional[Dict[Optional[str], str]] = None) -> None:
        next_local_root_paths = {path_pair_id: path for path_pair_id, path in local_root_paths.items() if path}
        next_local_staging_paths = {
            path_pair_id: path for path_pair_id, path in (local_staging_paths or {}).items() if path
        }
        if next_local_root_paths != self.__local_root_paths or next_local_staging_paths != self.__local_staging_paths:
            self.__cached_model = None
        self.__local_root_paths = next_local_root_paths
        self.__local_staging_paths = next_local_staging_paths

    def __resolve_local_disk_path(self,
                                  model_file: ModelFile,
                                  local_file: Optional[SystemFile]) -> Optional[str]:
        if local_file is None:
            return None
        root_paths = self.__local_staging_paths if getattr(local_file, "is_staging", False) else self.__local_root_paths
        resolved_root = root_paths.get(model_file.path_pair_id)
        if resolved_root is None:
            return None
        return os.path.join(resolved_root, model_file.full_path)

    def __resolve_normalized_local_root_path(self,
                                             local_file: Optional[SystemFile],
                                             path_pair_id: Optional[str]) -> Optional[str]:
        if local_file is not None and getattr(local_file, "is_staging", False):
            return None
        resolved_root = self.__local_root_paths.get(path_pair_id)
        if resolved_root is None:
            return None
        return os.path.normcase(os.path.normpath(resolved_root.replace("\\", "/")))

    def __get_allocated_local_size(self, model_file: ModelFile, local_file: Optional[SystemFile]) -> Optional[int]:
        local_path = self.__resolve_local_disk_path(model_file, local_file)
        if local_path is None or not os.path.exists(local_path):
            return None
        try:
            stat_result = os.stat(local_path)
        except (OSError, TypeError, ValueError):
            return None
        blocks = getattr(stat_result, "st_blocks", None)
        if blocks is None:
            return None
        try:
            return int(blocks) * 512
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def __summarize_snapshot_source(arbitration_source: str) -> str:
        if arbitration_source == "recent_live_snapshot":
            return "recent_live_snapshot"
        if arbitration_source == "retained_recent_live_snapshot":
            return "retained_recent_live_snapshot"
        if arbitration_source in (
            "retained_stopped_snapshot",
            "retained_stopped_snapshot_from_live_status",
            "retained_stopped_snapshot_without_live_progress",
            "live_status_coalesced_with_retained_floor",
        ):
            return "retained_stopped_snapshot"
        return "none"

    def __trace_cycle_event(self, event: str, payload: dict[str, object]) -> None:
        if not self.__is_stop_resume_trace_enabled():
            return
        trace_payload: dict[str, object] = {
            "cycle": self.__stop_resume_trace_cycle_id,
            "event": event,
        }
        trace_payload.update(payload)
        self.__stop_resume_trace_logger.info("stop_resume_trace %s", json.dumps(trace_payload, sort_keys=True))

    def __trace_target_arbitration(self,
                                   model_file: ModelFile,
                                   root_file_id: str,
                                   is_stopped: bool,
                                   remote_present: bool,
                                   local_present: bool,
                                   local: Optional[SystemFile],
                                   active_present: bool,
                                   local_freshness: str,
                                   status: Optional[LftpJobStatus],
                                   transfer_state: Optional[_TransferState],
                                   arbitration_source: str) -> None:
        if not self.__is_stop_resume_trace_enabled():
            return
        if not self.__trace_selector_matches_model_file(model_file, root_file_id):
            return
        self.__stop_resume_trace_emitted = True
        self.__stop_resume_trace_last_idle_signature = None
        self.__trace_cycle_event("arbitration", {
            "model_source": "rebuilt",
            "snapshot_source": ModelBuilder.__summarize_snapshot_source(arbitration_source),
            "local_freshness": local_freshness,
            "recent_snapshot_present": arbitration_source == "recent_live_snapshot",
            "retained_snapshot_present": arbitration_source in (
                "retained_recent_live_snapshot",
                "retained_stopped_snapshot",
                "retained_stopped_snapshot_from_live_status",
                "retained_stopped_snapshot_without_live_progress",
                "live_status_coalesced_with_retained_floor",
            ),
            "resolved_identity": {
                "file_id": model_file.file_id,
                "root_file_id": root_file_id,
                "path_pair_id": model_file.path_pair_id,
                "path_pair_name": model_file.path_pair_name,
            },
            "stopped": is_stopped,
            "raw_lftp_status": {
                "state": ModelBuilder.__enum_name(status.state) if status is not None else None,
                "job_id": status.id if status is not None else None,
                "file_id": status.file_id if status is not None else None,
                "transfer": ModelBuilder.__summarize_transfer_state(transfer_state),
            },
            "matched_local": {
                "name": local.name if local is not None else None,
                "path": self.__resolve_local_disk_path(model_file, local),
            },
            "local_data_role": ModelBuilder.__summarize_local_data_role(
                model_file,
                local,
                transfer_state,
                arbitration_source,
            ),
            "local_size_apparent": local.size if local is not None else None,
            "local_size_allocated": self.__get_allocated_local_size(model_file, local),
            "presence": {
                "remote": remote_present,
                "local": local_present,
                "active": active_present,
            },
            "arbitration_source": arbitration_source,
            "final_model": ModelBuilder.__summarize_rendered_model(model_file),
        })

    @staticmethod
    def __is_authoritative_local_file(local_file: Optional[SystemFile]) -> bool:
        return local_file is not None and not getattr(local_file, "is_staging", False)

    @staticmethod
    def __local_size_is_authoritative_progress(local_file: Optional[SystemFile],
                                               remote_file: Optional[SystemFile],
                                               retained_size_local: Optional[int]) -> bool:
        if local_file is None or not ModelBuilder.__is_authoritative_local_file(local_file):
            return False
        if retained_size_local is None:
            return False
        return local_file.size >= retained_size_local or \
            (remote_file is not None and local_file.size >= remote_file.size)

    @staticmethod
    def __local_file_proves_download_completion(local_file: Optional[SystemFile],
                                                remote_file: Optional[SystemFile]) -> bool:
        if local_file is None or not ModelBuilder.__is_authoritative_local_file(local_file):
            return False
        if remote_file is None:
            return False
        return local_file.size >= remote_file.size

    @staticmethod
    def __has_incomplete_remote_file_children(model_file: ModelFile) -> bool:
        frontier = deque(model_file.iter_children())
        while frontier:
            child_file = frontier.popleft()
            if child_file.is_dir:
                frontier.extend(child_file.iter_children())
            elif child_file.remote_size is not None and \
                    (child_file.local_size is None or child_file.local_size < child_file.remote_size):
                return True
        return False

    @staticmethod
    def __has_remote_transferable_content(remote_file: Optional[SystemFile]) -> bool:
        """Return whether a remote node contains a transferable file.

        Directory metadata alone is not transferable content. The remote scan
        has already been filtered for exclusions before it reaches the builder,
        so this recursive check also naturally ignores excluded descendants.
        A file is content even when its size is zero.
        """
        if remote_file is None:
            return False
        if not remote_file.is_dir:
            return True
        return any(
            ModelBuilder.__has_remote_transferable_content(child)
            for child in remote_file.children
        )

    @staticmethod
    def __normalize_download_progress(percent_local: Optional[int | float]) -> Optional[int]:
        if percent_local is None:
            return None
        if type(percent_local) == float:
            # Treat fractional values below 1.0 as 0-1 progress fractions.
            # Keep an exact 1.0 as a literal 1% reading rather than 100%.
            if percent_local < 1:
                return int(round(percent_local * 100))
            return int(round(percent_local))
        return int(percent_local)

    @staticmethod
    def __transfer_state(value: object) -> _TransferState:
        if not isinstance(value, tuple):
            raise ModelError("Invalid transfer state")
        transfer_tuple = cast(tuple[object, ...], value)
        if len(transfer_tuple) != 5:
            raise ModelError("Invalid transfer state")
        size_local, size_remote, percent_local, speed, eta = transfer_tuple
        if size_local is not None and not isinstance(size_local, int):
            raise ModelError("Invalid local transfer size")
        if size_remote is not None and not isinstance(size_remote, int):
            raise ModelError("Invalid remote transfer size")
        if percent_local is not None and not isinstance(percent_local, (int, float)):
            raise ModelError("Invalid transfer progress")
        if speed is not None and not isinstance(speed, int):
            raise ModelError("Invalid transfer speed")
        if eta is not None and not isinstance(eta, int):
            raise ModelError("Invalid transfer ETA")
        return _TransferState(size_local, size_remote, percent_local, speed, eta)

    @staticmethod
    def __remote_indicates_newer_content(local_file: Optional[SystemFile],
                                         remote_file: Optional[SystemFile]) -> bool:
        if local_file is None or remote_file is None:
            return False
        if not ModelBuilder.__local_file_proves_download_completion(local_file, remote_file):
            return True
        if local_file.timestamp_modified is None or remote_file.timestamp_modified is None:
            return False
        return remote_file.timestamp_modified > local_file.timestamp_modified

    def __store_recent_live_transfer_snapshot(self,
                                              file_id: str,
                                              root_file_id: str,
                                              transfer_state: _TransferState) -> None:
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
                                                   transfer_state: _TransferState) -> None:
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
    def __candidate_snapshot_root_aliases(root_file_id: Optional[str]) -> List[str]:
        if root_file_id is None:
            return []
        alias_candidates: list[str] = [root_file_id]
        try:
            parsed_root = json.loads(root_file_id)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed_root = None
        if isinstance(parsed_root, list):
            parsed_items = cast(list[object], parsed_root)
            if len(parsed_items) == 2 and isinstance(parsed_items[1], str):
                alias_candidates.append(parsed_items[1])
        return list(dict.fromkeys(alias_candidates))

    @staticmethod
    def __get_transfer_snapshot_alias_keys(snapshot_store: dict[str, _RecentLiveTransferSnapshot],
                                           root_file_id: Optional[str],
                                           excluded_keys: Optional[Set[str]] = None) -> List[str]:
        root_aliases = ModelBuilder.__candidate_snapshot_root_aliases(root_file_id)
        if not root_aliases:
            return []
        excluded_keys = excluded_keys if excluded_keys is not None else set()
        alias_keys: list[str] = []
        for root_alias in root_aliases:
            if root_alias not in excluded_keys and root_alias in snapshot_store:
                alias_keys.append(root_alias)
        for stored_file_id, snapshot in snapshot_store.items():
            if stored_file_id in excluded_keys or stored_file_id in root_aliases:
                continue
            if snapshot.root_file_id in root_aliases:
                alias_keys.append(stored_file_id)
        return list(dict.fromkeys(alias_keys))

    def __resolve_transfer_snapshot(self,
                                    snapshot_store: dict[str, _RecentLiveTransferSnapshot],
                                    file_id: str,
                                    root_file_id: Optional[str] = None
                                    ) -> Tuple[Optional[str], Optional[_RecentLiveTransferSnapshot]]:
        snapshot = snapshot_store.get(file_id)
        if snapshot is not None:
            return file_id, snapshot
        if root_file_id is None:
            return None, None
        alias_keys = self.__get_transfer_snapshot_alias_keys(snapshot_store, root_file_id, {file_id})
        if len(alias_keys) == 1:
            alias_key = alias_keys[0]
            return alias_key, snapshot_store.get(alias_key)
        return None, None

    @staticmethod
    def __promote_transfer_snapshot(snapshot_store: dict[str, _RecentLiveTransferSnapshot],
                                    resolved_file_id: Optional[str],
                                    canonical_file_id: str,
                                    canonical_root_file_id: Optional[str]
                                    ) -> Optional[_RecentLiveTransferSnapshot]:
        if resolved_file_id is None:
            return None
        snapshot = snapshot_store.get(resolved_file_id)
        if snapshot is None:
            return None
        if canonical_root_file_id is not None:
            snapshot.root_file_id = canonical_root_file_id
        if resolved_file_id != canonical_file_id:
            snapshot_store.pop(resolved_file_id, None)
            snapshot_store[canonical_file_id] = snapshot
        return snapshot

    def __resolve_retained_stopped_transfer_snapshot(
            self,
            file_id: str,
            root_file_id: Optional[str] = None) -> Tuple[Optional[str], Optional[_RecentLiveTransferSnapshot]]:
        resolved_file_id, snapshot = self.__resolve_transfer_snapshot(
            self.__retained_stopped_transfer_snapshots,
            file_id,
            root_file_id
        )
        promoted_snapshot = self.__promote_transfer_snapshot(
            self.__retained_stopped_transfer_snapshots,
            resolved_file_id,
            file_id,
            root_file_id
        )
        if promoted_snapshot is not None:
            return file_id, promoted_snapshot
        return resolved_file_id, snapshot

    def __resolve_recent_live_transfer_snapshot(
            self,
            file_id: str,
            root_file_id: Optional[str] = None) -> Tuple[Optional[str], Optional[_RecentLiveTransferSnapshot]]:
        resolved_file_id, snapshot = self.__resolve_transfer_snapshot(
            self.__recent_live_transfer_snapshots,
            file_id,
            root_file_id
        )
        promoted_snapshot = self.__promote_transfer_snapshot(
            self.__recent_live_transfer_snapshots,
            resolved_file_id,
            file_id,
            root_file_id
        )
        if promoted_snapshot is not None:
            return file_id, promoted_snapshot
        return resolved_file_id, snapshot

    def __evict_retained_stopped_transfer_snapshots(self,
                                                    resolved_file_id: str,
                                                    root_file_id: Optional[str] = None) -> None:
        self.__retained_stopped_transfer_snapshots.pop(resolved_file_id, None)

    def __evict_transfer_completion_snapshots(self,
                                              file_id: str,
                                              root_file_id: Optional[str] = None) -> None:
        recent_snapshot_key, _ = self.__resolve_recent_live_transfer_snapshot(file_id, root_file_id)
        if recent_snapshot_key is not None:
            self.__recent_live_transfer_snapshots.pop(recent_snapshot_key, None)

        retained_snapshot_key, _ = self.__resolve_retained_stopped_transfer_snapshot(file_id, root_file_id)
        if retained_snapshot_key is not None:
            self.__retained_stopped_transfer_snapshots.pop(retained_snapshot_key, None)

    @staticmethod
    def __resolve_root_file_id(file_id: str,
                               root_remote: Optional[SystemFile],
                               root_local: Optional[SystemFile]) -> str:
        if root_remote is not None:
            return ModelBuilder.__root_file_id(root_remote.name, root_remote.path_pair_id)
        if root_local is not None:
            return ModelBuilder.__root_file_id(root_local.name, root_local.path_pair_id)
        return file_id

    @staticmethod
    def __build_retained_transfer_state(size_local: Optional[int],
                                        size_remote: Optional[int],
                                        percent_local: Optional[int | float]) -> Optional[_TransferState]:
        if size_local is None:
            return None
        return _TransferState(
            size_local,
            size_remote,
            ModelBuilder.__normalize_download_progress(percent_local),
            None,
            None
        )

    def __sweep_recent_live_transfer_snapshots(self, seen_file_ids: Optional[Set[str]] = None) -> None:
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

    def __evict_recent_live_transfer_snapshots(self, root_file_id: str) -> None:
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
                                         root_local: Optional[SystemFile] = None) -> Optional[_TransferState]:
        root_file_id = self.__resolve_root_file_id(file_id, root_remote, root_local)
        resolved_file_id, snapshot = self.__resolve_recent_live_transfer_snapshot(file_id, root_file_id)
        if snapshot is None:
            return None
        root_status = self.__lftp_statuses.get(snapshot.root_file_id)
        stop_remote = root_remote if root_remote is not None else remote
        stop_local = root_local if root_local is not None else local
        if self.__is_stopped_file(snapshot.root_file_id, stop_remote, stop_local, root_status):
            self.__recent_live_transfer_snapshots.pop(
                resolved_file_id if resolved_file_id is not None else file_id,
                None
            )
            return None
        if self.__lftp_statuses.get(snapshot.root_file_id) is not None:
            return None
        if remote is None or local is None or snapshot.size_local is None:
            self.__recent_live_transfer_snapshots.pop(
                resolved_file_id if resolved_file_id is not None else file_id,
                None
            )
            return None
        if self.__local_size_is_authoritative_progress(local, remote, snapshot.size_local):
            self.__recent_live_transfer_snapshots.pop(
                resolved_file_id if resolved_file_id is not None else file_id,
                None
            )
            return None

        return _TransferState(
            snapshot.size_local,
            remote.size,
            snapshot.percent_local,
            snapshot.speed,
            snapshot.eta
        )

    @staticmethod
    def __has_clear_transfer_reset_signal(local: Optional[SystemFile],
                                          current_transfer_state: _TransferState,
                                          retained_snapshot: _RecentLiveTransferSnapshot) -> bool:
        if current_transfer_state.size_local == 0 or current_transfer_state.percent_local == 0:
            return True
        return False

    def __coalesce_retained_stopped_transfer_state(self,
                                                   file_id: str,
                                                   root_file_id: Optional[str],
                                                   remote: Optional[SystemFile],
                                                   local: Optional[SystemFile],
                                                   current_transfer_state: _TransferState
                                                   ) -> _TransferState:
        retained_snapshot_key, retained_snapshot = self.__resolve_retained_stopped_transfer_snapshot(
            file_id,
            root_file_id
        )
        if retained_snapshot is None or retained_snapshot.size_local is None:
            return current_transfer_state
        if self.__has_clear_transfer_reset_signal(local, current_transfer_state, retained_snapshot):
            self.__evict_retained_stopped_transfer_snapshots(
                retained_snapshot_key if retained_snapshot_key is not None else file_id,
                retained_snapshot.root_file_id
            )
            return current_transfer_state
        current_percent = ModelBuilder.__normalize_download_progress(current_transfer_state.percent_local)
        retained_percent = retained_snapshot.percent_local
        size_has_caught_up = current_transfer_state.size_local is not None and \
            current_transfer_state.size_local >= retained_snapshot.size_local
        percent_has_caught_up = retained_percent is None or \
            (current_percent is not None and current_percent >= retained_percent)
        if size_has_caught_up and percent_has_caught_up:
            self.__evict_retained_stopped_transfer_snapshots(
                retained_snapshot_key if retained_snapshot_key is not None else file_id,
                retained_snapshot.root_file_id
            )
            return current_transfer_state
        coalesced_size_local = retained_snapshot.size_local
        if size_has_caught_up:
            coalesced_size_local = current_transfer_state.size_local
        coalesced_percent_local = retained_percent
        if percent_has_caught_up:
            coalesced_percent_local = current_percent
        return _TransferState(
            coalesced_size_local,
            remote.size if remote is not None else current_transfer_state.size_remote,
            coalesced_percent_local,
            current_transfer_state.speed,
            current_transfer_state.eta
        )

    def __get_retained_stopped_transfer_state_without_live_progress(
            self,
            file_id: str,
            root_file_id: Optional[str],
            remote: Optional[SystemFile],
            local: Optional[SystemFile],
            preserve_when_local_growth_only: bool = False) -> Optional[_TransferState]:
        retained_snapshot_key, retained_snapshot = self.__resolve_retained_stopped_transfer_snapshot(
            file_id,
            root_file_id
        )
        if retained_snapshot is None or retained_snapshot.size_local is None:
            return None
        if self.__local_file_proves_download_completion(local, remote):
            self.__evict_transfer_completion_snapshots(
                file_id,
                root_file_id
            )
            return None
        if local is not None and ModelBuilder.__is_authoritative_local_file(local):
            if local.size == 0:
                self.__evict_retained_stopped_transfer_snapshots(
                    retained_snapshot_key if retained_snapshot_key is not None else file_id,
                    retained_snapshot.root_file_id
                )
                return None
            if local.size > retained_snapshot.size_local and not preserve_when_local_growth_only:
                return None
        return self.__build_retained_transfer_state(
            retained_snapshot.size_local,
            remote.size if remote is not None else None,
            retained_snapshot.percent_local
        )

    def __get_retained_recent_transfer_state(self,
                                             file_id: str,
                                             remote: Optional[SystemFile],
                                             local: Optional[SystemFile],
                                             root_remote: Optional[SystemFile] = None,
                                             root_local: Optional[SystemFile] = None) -> Optional[_TransferState]:
        root_file_id = self.__resolve_root_file_id(file_id, root_remote, root_local)
        resolved_file_id, snapshot = self.__resolve_recent_live_transfer_snapshot(file_id, root_file_id)
        if snapshot is None:
            return None
        if snapshot.size_local is None:
            self.__recent_live_transfer_snapshots.pop(
                resolved_file_id if resolved_file_id is not None else file_id,
                None
            )
            return None
        if self.__local_file_proves_download_completion(local, remote):
            self.__evict_transfer_completion_snapshots(file_id, root_file_id)
            return None
        if remote is None:
            return self.__build_retained_transfer_state(snapshot.size_local, None, snapshot.percent_local)
        return self.__build_retained_transfer_state(snapshot.size_local, remote.size, snapshot.percent_local)

    def set_active_files(self, active_files: List[SystemFile]) -> None:
        had_active_files = bool(self.__active_files)
        self.__active_file_ids = set()
        self.__active_files = {}
        for file in active_files:
            self.__collect_active_file_ids(file, self.__active_file_ids)
            self.__active_files[self.__root_file_id(file.name, file.path_pair_id)] = file
        # Invalidate the cache
        if had_active_files or len(active_files) > 0:
            self.__cached_model = None

    def __build_effective_local_files(self) -> Dict[str, SystemFile]:
        if not self.__active_files:
            return dict(self.__local_files)

        effective_local_files = dict(self.__local_files)
        for file_id, active_file in self.__active_files.items():
            existing_file = effective_local_files.get(file_id)
            remote_file = self.__remote_files.get(file_id)
            if existing_file is not None and getattr(existing_file, "is_staging", False):
                continue
            if existing_file is not None and \
                    self.__is_authoritative_local_file(existing_file) and \
                    getattr(active_file, "is_staging", False) and \
                    existing_file.size >= active_file.size and \
                    not self.__remote_indicates_newer_content(existing_file, remote_file):
                continue
            effective_local_files[file_id] = active_file
        return effective_local_files

    def set_local_files(self, local_files: List[SystemFile]) -> None:
        prev_local_files = self.__local_files
        self.__local_files = {
            self.__root_file_id(file.name, file.path_pair_id): file for file in local_files
        }
        # Invalidate the cache
        if self.__local_files != prev_local_files:
            self.__cached_model = None

    def set_remote_files(self, remote_files: List[SystemFile]) -> None:
        prev_remote_files = self.__remote_files
        self.__remote_files = {
            self.__root_file_id(file.name, file.path_pair_id): file for file in remote_files
        }
        # Invalidate the cache
        if self.__remote_files != prev_remote_files:
            self.__cached_model = None

    def set_lftp_statuses(self, lftp_statuses: List[LftpJobStatus]) -> None:
        prev_lftp_statuses = self.__lftp_statuses
        self.__lftp_statuses = {file.file_id: file for file in lftp_statuses}
        # Invalidate the cache
        if self.__lftp_statuses != prev_lftp_statuses:
            self.__cached_model = None

    def evict_recent_live_transfer_snapshots_missing_roots(self, active_root_file_ids: Set[str]) -> None:
        removed = False
        for file_id, snapshot in list(self.__recent_live_transfer_snapshots.items()):
            if snapshot.root_file_id in active_root_file_ids:
                continue
            root_status = self.__lftp_statuses.get(snapshot.root_file_id)
            if self.__is_stopped_file(file_id) or self.__is_stopped_file(snapshot.root_file_id, status=root_status):
                continue
            self.__recent_live_transfer_snapshots.pop(file_id, None)
            removed = True
        if removed:
            self.__cached_model = None

    def set_downloaded_files(self, downloaded_files: Set[str]) -> None:
        prev_downloaded_files = self.__downloaded_files
        self.__downloaded_files = set(downloaded_files)
        # Invalidate the cache
        if self.__downloaded_files != prev_downloaded_files:
            self.__cached_model = None

    def set_downloaded_timestamps(self, downloaded_timestamps: Dict[str, float]) -> None:
        previous = self.__downloaded_timestamps
        self.__downloaded_timestamps = dict(downloaded_timestamps)
        if self.__downloaded_timestamps != previous:
            self.__cached_model = None

    def set_extract_statuses(self, extract_statuses: List[ExtractStatus]) -> None:
        prev_extract_statuses = self.__extract_statuses
        self.__extract_statuses = {
            self.__extract_status_key(status): status for status in extract_statuses
        }
        # Invalidate the cache
        if self.__extract_statuses != prev_extract_statuses:
            self.__cached_model = None

    def set_extracted_files(self, extracted_files: Set[str]) -> None:
        prev_extracted_files = self.__extracted_files
        self.__extracted_files = extracted_files
        # Invalidate the cache
        if self.__extracted_files != prev_extracted_files:
            self.__cached_model = None

    def set_stopped_files(self, stopped_files: Set[str]) -> None:
        prev_stopped_files = self.__stopped_files
        self.__stopped_files = set(stopped_files)
        self.__sweep_recent_live_transfer_snapshots()
        # Invalidate the cache
        if self.__stopped_files != prev_stopped_files:
            self.__cached_model = None

    def set_move_failed_files(self, move_failed_files: Set[str]) -> None:
        previous = self.__move_failed_files
        self.__move_failed_files = set(move_failed_files)
        if self.__move_failed_files != previous:
            self.__cached_model = None

    def set_final_move_succeeded_files(self, file_ids: Set[str]) -> None:
        previous = self.__final_move_succeeded_files
        self.__final_move_succeeded_files = set(file_ids)
        if self.__final_move_succeeded_files != previous:
            self.__cached_model = None

    def set_validation_statuses(self, validation_statuses: List[ValidateStatus]) -> None:
        prev_validation_statuses = self.__validation_statuses
        self.__validation_statuses = {status.file_id: status for status in validation_statuses}
        if self.__validation_statuses != prev_validation_statuses:
            self.__cached_model = None

    def clear(self) -> None:
        self.__local_files.clear()
        self.__active_files.clear()
        self.__remote_files.clear()
        self.__active_file_ids.clear()
        self.__lftp_statuses.clear()
        self.__recent_live_transfer_snapshots.clear()
        self.__retained_stopped_transfer_snapshots.clear()
        self.__downloaded_files = None
        self.__downloaded_timestamps.clear()
        self.__extract_statuses.clear()
        self.__extracted_files.clear()
        self.__stopped_files.clear()
        self.__validation_statuses.clear()
        self.__move_failed_files.clear()
        self.__final_move_succeeded_files.clear()
        self.__suppressed_ambiguous_extracted_file_names.clear()
        self.__cached_model = None

    def has_changes(self) -> bool:
        """
        Returns true is model has changes and requires rebuild
        :return:
        """
        return self.__cached_model is None or self.__has_pending_recent_live_transfer_snapshots()

    def request_rebuild(self) -> None:
        self.__cached_model = None

    def build_model(self) -> Model:
        if self.__cached_model is not None and not self.__has_pending_recent_live_transfer_snapshots():
            return self.__cached_model

        model = Model()
        model.logger = self.__build_dummy_model_logger()  # ignore the logs for this temp model
        live_transferred_file_ids: set[str] = set()
        effective_local_files = self.__build_effective_local_files()
        all_file_ids: set[str] = set(effective_local_files).union(self.__remote_files)
        source_file_ids: set[str] = set(effective_local_files).union(self.__remote_files)
        for status_file_id in self.__lftp_statuses.keys():
            if status_file_id not in source_file_ids:
                all_file_ids.add(status_file_id)

        # A legacy name-only extraction marker cannot identify one of two
        # path-pair-scoped roots. Establish that ambiguity before root states
        # are rendered; the later visibility pass retains the suppression only
        # while the ambiguity still exists.
        source_name_counts: Dict[str, int] = {}
        for file_id in all_file_ids:
            source = effective_local_files.get(file_id) or self.__remote_files.get(file_id)
            if source is None:
                status = self.__lftp_statuses.get(file_id)
                source_name = status.name if status is not None else file_id
            else:
                source_name = source.name
            source_name_counts[source_name] = source_name_counts.get(source_name, 0) + 1
        self.__suppressed_ambiguous_extracted_file_names = {
            file_name
            for file_name, count in source_name_counts.items()
            if count > 1 and file_name in self.__extracted_files
        }

        built_root_files: List[_BuiltRootFile] = []
        for file_id in all_file_ids:
            remote = self.__remote_files.get(file_id, None)
            local = effective_local_files.get(file_id, None)
            status = self.__lftp_statuses.get(file_id, None)
            is_stopped = self.__is_stopped_file(file_id, remote, local)
            name = remote.name if remote else local.name if local else file_id
            if remote is None and local is None and status is None:
                # this should never happen, but just in case
                raise ModelError("Zero sources have a file object")

            # sanity check between the sources
            if remote is not None:
                is_dir = remote.is_dir
            elif local is not None:
                is_dir = local.is_dir
            else:
                assert status is not None
                is_dir = status.type == LftpJobStatus.Type.MIRROR
            if (remote and is_dir != remote.is_dir) or \
               (local and is_dir != local.is_dir) or \
               (status and is_dir != (status.type == LftpJobStatus.Type.MIRROR)):
                raise ModelError("Mismatch in is_dir between sources")

            model_file = ModelFile(name, is_dir)
            path_pair_id = remote.path_pair_id if remote and remote.path_pair_id is not None else \
                local.path_pair_id if local else status.path_pair_id if status else None
            path_pair_name = remote.path_pair_name if remote and remote.path_pair_name is not None else \
                local.path_pair_name if local else status.path_pair_name if status else None
            self.__apply_path_pair_metadata(model_file, path_pair_id, path_pair_name)
            root_seen_file_ids: set[str] = set()
            (
                current_transfer_state,
                recent_transfer_state,
                retained_transfer_state,
                raw_current_transfer_state,
                arbitration_source,
            ) = self.__resolve_root_transfer_state(
                file_id,
                model_file,
                remote,
                local,
                status,
                is_stopped,
            )
            fill_transfer_state = current_transfer_state
            if fill_transfer_state is None:
                fill_transfer_state = recent_transfer_state
            if fill_transfer_state is None:
                fill_transfer_state = retained_transfer_state
            self.__fill_model_file(
                model_file,
                remote,
                local,
                fill_transfer_state,
                current_transfer_state is not None,
                status.file_id if status is not None else None,
                live_transferred_file_ids,
            )
            self.__build_children(
                model_file,
                remote,
                local,
                status,
                root_seen_file_ids,
                live_transferred_file_ids,
            )
            self.__estimate_eta(model_file)
            incomplete_children, arbitration_source = self.__check_root_downloaded(
                file_id,
                model_file,
                remote,
                local,
                status,
                is_stopped,
                retained_transfer_state,
                arbitration_source,
            )
            self.__determine_state(model_file, local, incomplete_children)
            model_file.is_stoppable = self.__is_stoppable_model_file(
                model_file,
                local,
                current_transfer_state,
            )

            # Empty remote directory trees are metadata only. Keep a local
            # counterpart visible as Local Only, but do not create a row for a
            # remote-only tree with no transferable descendants.
            if (
                status is None
                and local is None
                and not model_file.remote_has_transferable_content
            ):
                continue

            if self.__is_stop_resume_trace_enabled():
                self.__trace_target_arbitration(
                    model_file,
                    status.file_id if status is not None else model_file.file_id,
                    is_stopped,
                    remote is not None,
                    local is not None,
                    local,
                    model_file.file_id in self.__active_file_ids,
                    ModelBuilder.__summarize_local_freshness(local),
                    status,
                    raw_current_transfer_state,
                    arbitration_source
                )
            if self.__is_target_archive_trace_enabled():
                self.__trace_target_archive_arbitration(
                    model_file,
                    status.file_id if status is not None else model_file.file_id,
                    is_stopped,
                    remote is not None,
                    local is not None,
                    local,
                    model_file.file_id in self.__active_file_ids,
                    ModelBuilder.__summarize_local_freshness(local),
                    status,
                    raw_current_transfer_state,
                    arbitration_source
                )

            built_root_files.append(_BuiltRootFile(
                model_file=model_file,
                normalized_local_root_path=self.__resolve_normalized_local_root_path(
                    local,
                    path_pair_id,
                ),
                is_local_only=local is not None
                and not model_file.remote_has_transferable_content
                and status is None,
                seen_file_ids=root_seen_file_ids,
            ))

        seen_names_by_path: Dict[str, Set[str]] = {}
        for built_root_file in built_root_files:
            normalized_local_root_path = built_root_file.normalized_local_root_path
            if normalized_local_root_path is None or built_root_file.is_local_only:
                continue
            if normalized_local_root_path not in seen_names_by_path:
                seen_names_by_path[normalized_local_root_path] = set()
            seen_names_by_path[normalized_local_root_path].add(built_root_file.model_file.name)

        local_only_root_winners: Dict[Tuple[str, str], _BuiltRootFile] = {}
        for built_root_file in built_root_files:
            normalized_local_root_path = built_root_file.normalized_local_root_path
            if normalized_local_root_path is None or not built_root_file.is_local_only:
                continue
            dedupe_key = (normalized_local_root_path, built_root_file.model_file.name)
            candidate_priority = (
                built_root_file.model_file.state.value,
                built_root_file.model_file.file_id,
            )
            current_winner = local_only_root_winners.get(dedupe_key)
            current_priority = (
                current_winner.model_file.state.value,
                current_winner.model_file.file_id,
            ) if current_winner is not None else None
            if current_priority is None or candidate_priority > current_priority:
                local_only_root_winners[dedupe_key] = built_root_file

        visible_root_files: List[_BuiltRootFile] = []
        for built_root_file in built_root_files:
            normalized_local_root_path = built_root_file.normalized_local_root_path
            if normalized_local_root_path is not None and normalized_local_root_path not in seen_names_by_path:
                seen_names_by_path[normalized_local_root_path] = set()
            if built_root_file.is_local_only and normalized_local_root_path is not None:
                if built_root_file.model_file.name in seen_names_by_path[normalized_local_root_path]:
                    continue
                dedupe_key = (normalized_local_root_path, built_root_file.model_file.name)
                if local_only_root_winners.get(dedupe_key) is not built_root_file:
                    continue
            visible_root_files.append(built_root_file)
            if normalized_local_root_path is not None:
                seen_names_by_path[normalized_local_root_path].add(built_root_file.model_file.name)

        seen_file_ids: set[str] = set()
        for built_root_file in visible_root_files:
            seen_file_ids.add(built_root_file.model_file.file_id)
            seen_file_ids.update(built_root_file.seen_file_ids)
            model.add_file(built_root_file.model_file)

        self.__sweep_recent_live_transfer_snapshots(seen_file_ids)
        self.__cached_model = model
        return model

    def __resolve_root_transfer_state(
        self,
        file_id: str,
        model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
        status: Optional[LftpJobStatus],
        is_stopped: bool,
    ) -> Tuple[
        Optional[_TransferState],
        Optional[_TransferState],
        Optional[_TransferState],
        Optional[_TransferState],
        str
    ]:
        # set the file state
        # for now we only set to Queued or Downloading
        # later after all children are built, we can set to Downloaded after performing a check
        recent_transfer_state = None
        retained_transfer_state = None
        arbitration_source = "scan_only"
        raw_current_transfer_state = self.__transfer_state(status.total_transfer_state) if status and \
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
            arbitration_source = "retained_stopped_snapshot_from_live_status"
        elif current_transfer_state is not None:
            current_transfer_state = self.__coalesce_retained_stopped_transfer_state(
                file_id,
                status.file_id if status is not None else model_file.file_id,
                remote,
                local,
                current_transfer_state
            )
            arbitration_source = "live_status"
            if current_transfer_state != raw_current_transfer_state:
                arbitration_source = "live_status_coalesced_with_retained_floor"
        elif status is not None:
            retained_transfer_state = self.__get_retained_stopped_transfer_state_without_live_progress(
                file_id,
                status.file_id,
                remote,
                local,
                preserve_when_local_growth_only=is_stopped
            )
            arbitration_source = "retained_stopped_snapshot_without_live_progress" \
                if retained_transfer_state is not None else \
                "suppressed_stopped_live_status" if is_stopped else "live_status_without_transfer_state"
        if current_transfer_state is None and status is None and not is_stopped:
            recent_transfer_state = self.__get_recent_live_transfer_state(file_id, remote, local)
            if recent_transfer_state is not None:
                arbitration_source = "recent_live_snapshot"
        if retained_transfer_state is None and status is None and is_stopped:
            retained_transfer_state = self.__get_retained_stopped_transfer_state_without_live_progress(
                file_id,
                model_file.file_id,
                remote,
                local,
                preserve_when_local_growth_only=True
            )
            if retained_transfer_state is not None:
                arbitration_source = "retained_stopped_snapshot"
        if retained_transfer_state is None and status is None and is_stopped:
            retained_transfer_state = self.__get_retained_recent_transfer_state(file_id, remote, local, remote, local)
            if retained_transfer_state is not None:
                arbitration_source = "retained_recent_live_snapshot"
        if status and not is_stopped:
            model_file.state = ModelFile.State.QUEUED if status.state == LftpJobStatus.State.QUEUED \
                               else ModelFile.State.DOWNLOADING
            if status.state == LftpJobStatus.State.QUEUED:
                self.__evict_recent_live_transfer_snapshots(status.file_id)
                if arbitration_source == "scan_only":
                    arbitration_source = "live_status_queued"
        elif recent_transfer_state:
            model_file.state = ModelFile.State.DOWNLOADING
        return (
            current_transfer_state,
            recent_transfer_state,
            retained_transfer_state,
            raw_current_transfer_state,
            arbitration_source,
        )

    def __build_children(
        self,
        root_model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
        status: Optional[LftpJobStatus],
        seen_file_ids: Set[str],
        live_transferred_file_ids: Set[str],
    ) -> None:
        # Traverse SystemFile children tree in BFS order
        # Store (remote, local, status, model_file) tuple in traversal frontier where remote and local
        # correspond to the same node in both remote and local SystemFile trees, status corresponds
        # to the LFTP status for the entire tree, and model_file corresponds to the generated ModelFile
        # for the pair
        # Note: in this case the frontier contains nodes that have already been process, it is
        #       merely used for traversing children
        frontier: deque[tuple[
            Optional[SystemFile], Optional[SystemFile], Optional[LftpJobStatus],
            ModelFile, Optional[SystemFile], Optional[SystemFile]
        ]] = deque()
        if remote or local:
            frontier.append((remote, local, status, root_model_file, remote, local))
        while frontier:
            _remote, _local, _status, _model_file, _root_remote, _root_local = frontier.popleft()
            _remote_children: dict[str, SystemFile] = {sf.name: sf for sf in _remote.children} if _remote else {}
            _local_children: dict[str, SystemFile] = {sf.name: sf for sf in _local.children} if _local else {}
            _all_children_names: set[str] = set(_remote_children).union(_local_children)
            for _child_name in _all_children_names:
                _remote_child = _remote_children.get(_child_name, None)
                _local_child = _local_children.get(_child_name, None)
                if _remote_child is not None:
                    _is_dir = _remote_child.is_dir
                else:
                    assert _local_child is not None
                    _is_dir = _local_child.is_dir
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
                _child_current_transfer_state: Optional[_TransferState] = None
                _child_recent_transfer_state: Optional[_TransferState] = None
                _child_arbitration_source = "scan_only"
                if _status and _status.state == LftpJobStatus.State.RUNNING and \
                        not self.__is_stopped_file(_status.file_id, _root_remote, _root_local, _status) and \
                        not _child_is_stopped:
                    # Transfer states are in root-relative paths.
                    _child_status_path = "/".join(_child_model_file.full_path.split(os.sep)[1:])
                    for active_name, active_state in _status.get_active_file_transfer_states():
                        if active_name == _child_status_path:
                            _child_current_transfer_state = self.__transfer_state(active_state)
                            break
                    if _child_current_transfer_state is not None:
                        _child_arbitration_source = "live_status"
                if _child_current_transfer_state is None and _status is None:
                    _child_recent_transfer_state = self.__get_recent_live_transfer_state(
                        _child_model_file.file_id,
                        _remote_child,
                        _local_child,
                        _root_remote,
                        _root_local
                    )
                    if _child_recent_transfer_state is not None:
                        _child_arbitration_source = "recent_live_snapshot"
                if _is_dir:
                    _child_model_file.state = ModelFile.State.DEFAULT
                elif _child_current_transfer_state:
                    _child_model_file.state = ModelFile.State.DOWNLOADING
                elif _child_recent_transfer_state:
                    _child_model_file.state = ModelFile.State.DOWNLOADING
                elif _remote_child and _local_child is not None and \
                        self.__is_authoritative_local_file(_local_child) and \
                        _local_child.size >= _remote_child.size:
                    _child_model_file.state = ModelFile.State.DOWNLOADED
                elif _remote_child and not _child_is_stopped and \
                        root_model_file.state in (ModelFile.State.QUEUED, ModelFile.State.DOWNLOADING):
                    _child_model_file.state = ModelFile.State.QUEUED
                    _child_arbitration_source = "queued_by_root_state"
                else:
                    _child_model_file.state = ModelFile.State.DEFAULT
                    if _child_is_stopped and _status is not None:
                        _child_arbitration_source = "suppressed_stopped_live_status"

                # fill the rest
                self.__fill_model_file(
                    _child_model_file,
                    _remote_child,
                    _local_child,
                    _child_current_transfer_state if _child_current_transfer_state is not None
                    else _child_recent_transfer_state,
                    _child_current_transfer_state is not None,
                    status.file_id if status is not None else None,
                    live_transferred_file_ids,
                )
                _child_model_file.is_stoppable = self.__is_stoppable_model_file(
                    _child_model_file,
                    _local_child,
                    _child_current_transfer_state
                )
                if self.__is_stop_resume_trace_enabled():
                    self.__trace_target_arbitration(
                        _child_model_file,
                        root_model_file.file_id,
                        _child_is_stopped,
                        _remote_child is not None,
                        _local_child is not None,
                        _local_child,
                        _child_model_file.file_id in self.__active_file_ids,
                        ModelBuilder.__summarize_local_freshness(_local_child),
                        _status,
                        _child_current_transfer_state,
                        _child_arbitration_source
                    )
                # add child to frontier
                frontier.append((_remote_child, _local_child, _status, _child_model_file, _root_remote, _root_local))

    def __fill_model_file(
        self,
        model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
        transfer_state: Optional[_TransferState],
        store_recent_snapshot: bool,
        recent_snapshot_root_file_id: Optional[str],
        live_transferred_file_ids: Set[str],
    ) -> None:
        # set local and remote sizes
        model_file.remote_present = remote is not None
        model_file.local_present = local is not None
        model_file.remote_has_transferable_content = self.__has_remote_transferable_content(remote)
        if remote:
            model_file.remote_size = remote.size
        if local:
            model_file.local_size = local.size

        # Note: no longer use lftp's file sizes
        #       they represent remaining size for resumed downloads

        # set the downloading speed and eta
        if transfer_state:
            if store_recent_snapshot:
                self.__store_recent_live_transfer_snapshot(
                    model_file.file_id,
                    recent_snapshot_root_file_id if recent_snapshot_root_file_id is not None else model_file.file_id,
                    transfer_state
                )
            download_progress = ModelBuilder.__normalize_download_progress(transfer_state.percent_local)
            if download_progress is not None:
                model_file.download_progress = download_progress
            if transfer_state.size_local is not None:
                model_file.transferred_size = transfer_state.size_local
                live_transferred_file_ids.add(model_file.file_id)
            model_file.downloading_speed = transfer_state.speed
            model_file.eta = transfer_state.eta

        # set the transferred size (only if file or dir exists on both ends)
        if local and remote:
            self.__update_transferred_size(model_file, remote, local, live_transferred_file_ids)

        # set the is_extractable flag
        self.__update_extractable_flag(model_file)

        # set the timestamps
        self.__update_timestamps(model_file, remote, local)
        downloaded_timestamp = self.__downloaded_timestamps.get(model_file.file_id)
        if downloaded_timestamp is not None:
            try:
                model_file.downloaded_timestamp = datetime.fromtimestamp(downloaded_timestamp)
            except (OverflowError, OSError, ValueError):
                # Persisted values are schema-validated as finite/nonnegative,
                # but platform datetime ranges are narrower than float epochs.
                # Treat an unrepresentable value as unknown rather than
                # breaking model refresh.
                model_file.downloaded_timestamp = None

    @staticmethod
    def __update_transferred_size(
        model_file: ModelFile,
        remote: SystemFile,
        local: SystemFile,
        live_transferred_file_ids: Set[str],
    ) -> None:
        if model_file.is_dir:
            if model_file.transferred_size is None:
                # dir transferred size is updated by child files
                model_file.transferred_size = 0
        else:
            if model_file.transferred_size is None:
                if ModelBuilder.__is_authoritative_local_file(local):
                    model_file.transferred_size = min(local.size, remote.size)

            if model_file.transferred_size is not None:
                # also update all parent directories
                _parent_file = model_file.parent
                while _parent_file is not None:
                    if _parent_file.file_id in live_transferred_file_ids:
                        break
                    if _parent_file.transferred_size is None:
                        _parent_file.transferred_size = 0
                    _parent_file.transferred_size += model_file.transferred_size
                    _parent_file = _parent_file.parent

    @staticmethod
    def __update_extractable_flag(model_file: ModelFile) -> None:
        if not model_file.is_dir and Extract.is_archive_fast(model_file.name):
            model_file.is_extractable = True
            # Also set the flag for all of its parents
            _parent_file = model_file.parent
            while _parent_file is not None:
                _parent_file.is_extractable = True
                _parent_file = _parent_file.parent

    @staticmethod
    def __update_timestamps(
        model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
    ) -> None:
        if local:
            if local.timestamp_created:
                model_file.local_created_timestamp = local.timestamp_created
            if local.timestamp_modified:
                model_file.local_modified_timestamp = local.timestamp_modified
        if remote:
            if remote.timestamp_created:
                model_file.remote_created_timestamp = remote.timestamp_created
            if remote.timestamp_modified:
                model_file.remote_modified_timestamp = remote.timestamp_modified

    @staticmethod
    def __estimate_eta(model_file: ModelFile) -> None:
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

    def __check_root_downloaded(
        self,
        file_id: str,
        model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
        status: Optional[LftpJobStatus],
        is_stopped: bool,
        retained_transfer_state: Optional[_TransferState],
        arbitration_source: str,
    ) -> Tuple[bool, str]:
        incomplete_children = False

        if model_file.state == ModelFile.State.DOWNLOADING and \
                self.__local_file_proves_download_completion(local, remote):
            self.__evict_transfer_completion_snapshots(
                file_id,
                status.file_id if status is not None else model_file.file_id
            )
            model_file.state = ModelFile.State.DOWNLOADED
            model_file.transferred_size = remote.size if remote is not None else model_file.local_size
            model_file.download_progress = None
            model_file.downloading_speed = None
            model_file.eta = None
            arbitration_source = "suppressed_by_authoritative_local_completion"
        if model_file.state == ModelFile.State.DOWNLOADING and \
                status is None and \
                not is_stopped and \
                remote is not None and \
                local is not None and \
                getattr(local, "is_staging", False) and \
                local.size >= remote.size and \
                not self.__has_incomplete_remote_file_children(model_file) and \
                self.__resolve_recent_live_transfer_snapshot(
                    file_id,
                    status.file_id if status is not None else model_file.file_id
                )[1] is not None:
            self.__evict_transfer_completion_snapshots(
                file_id,
                status.file_id if status is not None else model_file.file_id
            )
            model_file.state = ModelFile.State.DOWNLOADED
            model_file.transferred_size = remote.size
            model_file.download_progress = None
            model_file.downloading_speed = None
            model_file.eta = None
            arbitration_source = "suppressed_by_staging_completion_after_live_status_lost"

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
                    status is None and \
                    not is_stopped and \
                    model_file.local_size is not None and \
                    model_file.remote_size is not None and \
                    getattr(local, "is_staging", False) and \
                    model_file.local_size >= model_file.remote_size:
                # Keep scan-only recovery for full-size staging copies so
                # they can leave incomplete and continue through the
                # normal completion path.
                model_file.state = ModelFile.State.DOWNLOADED
                arbitration_source = "staging_completion_without_live_status"
            elif not model_file.is_dir and \
                    model_file.local_size is not None and \
                    model_file.remote_size is None and \
                    self.__is_authoritative_local_file(local) and \
                    self.__model_file_matches_persisted_name(model_file, self.__downloaded_files):
                # keep previously-downloaded local-only files recognizable
                model_file.state = ModelFile.State.DOWNLOADED
            elif model_file.is_dir and model_file.remote_size is not None:
                if status is None and \
                        not is_stopped and \
                        local is not None and \
                        getattr(local, "is_staging", False) and \
                        local.size >= model_file.remote_size:
                    # A fully staged directory copy should be treated as complete even
                    # if live transfer state has already disappeared.
                    if not self.__has_incomplete_remote_file_children(model_file):
                        model_file.state = ModelFile.State.DOWNLOADED
                        arbitration_source = "staging_completion_without_live_status"
                else:
                    # root is a directory that also exists remotely
                    # check all the children
                    all_downloaded = True
                    has_downloadable_children = False
                    frontier = deque(model_file.iter_children())
                    while frontier:
                        _child_file = frontier.popleft()
                        if not _child_file.is_dir and \
                                _child_file.remote_size is not None:
                            has_downloadable_children = True
                            if _child_file.state != ModelFile.State.DOWNLOADED:
                                all_downloaded = False
                                break
                        frontier.extend(_child_file.iter_children())
                    if has_downloadable_children and all_downloaded:
                        model_file.state = ModelFile.State.DOWNLOADED
                    else:
                        incomplete_children = True

        return incomplete_children, arbitration_source

    def __determine_state(self, model_file: ModelFile, local: Optional[SystemFile], incomplete_children: bool):
        model_file.final_move_succeeded = model_file.file_id in self.__final_move_succeeded_files
        self.__check_persist_authority(model_file, incomplete_children)
        self.__check_extracting(model_file)

        # next we check if root is Extracted
        # root is Extracted if it is in Downloaded state and in extracted files list
        # Note: Default files aren't marked extracted because they can still be queued
        #       for download, and it doesn't make sense to queue after extracting
        #       If a Default file is extracted, it will return back to the Default state
        has_exact_extracted_marker = model_file.file_id in self.__extracted_files
        if self.__model_file_matches_persisted_name(model_file, self.__extracted_files) and \
                model_file.state == ModelFile.State.DOWNLOADED and \
                self.__is_authoritative_local_file(local) and \
                (has_exact_extracted_marker or
                 model_file.name not in self.__suppressed_ambiguous_extracted_file_names):
                model_file.state = ModelFile.State.EXTRACTED

        self.__check_validating(model_file)
        # Terminal move failures are authoritative until an explicit local
        # cleanup or a successful retry clears their canonical identity.
        if model_file.file_id in self.__move_failed_files:
            model_file.state = ModelFile.State.MOVE_FAILED

    def __check_persist_authority(self, model_file: ModelFile, _incomplete_children: bool):
        # next we check persisted markers for previously downloaded files
        if self.__downloaded_files is None:
            return
        if model_file.state == ModelFile.State.DEFAULT and \
                model_file.local_size is None and \
                self.__model_file_matches_persisted_name(model_file, self.__downloaded_files):
            model_file.state = ModelFile.State.DELETED

    def __check_extracting(self, model_file: ModelFile):
        # next we check if root is Extracting
        # root is Extracting if it's part of an extract status, in an expected state,
        # and exists locally
        # if root is NOT in an expected state, then ignore the extract status
        # and report a warning message, as this shouldn't be happening
        if model_file.file_id not in self.__extract_statuses:
            return
        extract_status = self.__extract_statuses[model_file.file_id]
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

    def __check_validating(self, model_file: ModelFile):
        # next we check if root is Validating
        # root is Validating if it has a validate status, is in an expected state, and exists locally
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
