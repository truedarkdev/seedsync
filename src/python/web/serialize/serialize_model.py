# Copyright 2017, Inderpreet Singh, All rights reserved.

from enum import Enum
import json
import time
from typing import List, Optional

from .serialize import Serialize
from model import ModelFile


class SerializeModel(Serialize):
    """
    This class defines the serialization interface between the python backend
    and the EventSource client frontend for the model stream.
    """

    class UpdateEvent:
        class Change(Enum):
            ADDED = 0
            REMOVED = 1
            UPDATED = 2

        def __init__(
            self,
            change: Change,
            old_file: Optional[ModelFile],
            new_file: Optional[ModelFile],
            trace_metadata: Optional[dict[str, object]] = None,
        ) -> None:
            self.change = change
            self.old_file = old_file
            self.new_file = new_file
            self.trace_metadata = trace_metadata
            self.enqueue_timestamp_ms: Optional[int] = None
            self.queue_size_before_enqueue: Optional[int] = None
            self.queue_dropped_count_before_enqueue: Optional[int] = None
            self.__enqueue_monotonic_ns: Optional[int] = None

        def mark_enqueued(
            self,
            timestamp_ms: int,
            queue_size: Optional[int],
            dropped_count: Optional[int],
        ) -> None:
            self.enqueue_timestamp_ms = timestamp_ms
            self.queue_size_before_enqueue = queue_size
            self.queue_dropped_count_before_enqueue = dropped_count
            self.__enqueue_monotonic_ns = time.monotonic_ns()

        def mark_stream_emitted(
            self,
            sequence: int,
            queue_size: Optional[int] = None,
            dropped_count: Optional[int] = None,
        ) -> None:
            if self.trace_metadata is None:
                return
            emitted_at_ms = int(time.time_ns() / 1_000_000)
            emitted_monotonic_ns = time.monotonic_ns()
            metadata = dict(self.trace_metadata)
            metadata["enqueue_timestamp_ms"] = self.enqueue_timestamp_ms
            metadata["stream_emit_sequence"] = sequence
            metadata["stream_emit_timestamp_ms"] = emitted_at_ms
            metadata["queue_wait_ms"] = (
                max(0, emitted_monotonic_ns - self.__enqueue_monotonic_ns) // 1_000_000
                if self.__enqueue_monotonic_ns is not None else None
            )
            metadata["queue_size_before_enqueue"] = self.queue_size_before_enqueue
            metadata["queue_dropped_count_before_enqueue"] = self.queue_dropped_count_before_enqueue
            metadata["queue_size"] = queue_size
            metadata["queue_dropped_count"] = dropped_count
            self.trace_metadata = metadata

    # Event keys
    __EVENT_INIT = "model-init"
    __EVENT_UPDATE = {
        UpdateEvent.Change.ADDED: "model-added",
        UpdateEvent.Change.REMOVED: "model-removed",
        UpdateEvent.Change.UPDATED: "model-updated"
    }
    __KEY_UPDATE_OLD_FILE = "old_file"
    __KEY_UPDATE_NEW_FILE = "new_file"
    __KEY_UPDATE_TRACE = "trace"

    # Model file keys
    __KEY_FILE_NAME = "name"
    __KEY_FILE_IS_DIR = "is_dir"
    __KEY_FILE_STATE = "state"
    __VALUES_FILE_STATE = {
        ModelFile.State.DEFAULT: "default",
        ModelFile.State.QUEUED: "queued",
        ModelFile.State.DOWNLOADING: "downloading",
        ModelFile.State.DOWNLOADED: "downloaded",
        ModelFile.State.DELETED: "deleted",
        ModelFile.State.EXTRACTING: "extracting",
        ModelFile.State.EXTRACTED: "extracted",
        ModelFile.State.VALIDATING: "validating",
        ModelFile.State.VALIDATED: "validated",
        ModelFile.State.CORRUPT: "corrupt",
        ModelFile.State.MOVE_FAILED: "move_failed"
    }
    __KEY_FILE_REMOTE_SIZE = "remote_size"
    __KEY_FILE_LOCAL_SIZE = "local_size"
    __KEY_FILE_REMOTE_PRESENT = "remote_present"
    __KEY_FILE_LOCAL_PRESENT = "local_present"
    __KEY_FILE_REMOTE_HAS_TRANSFERABLE_CONTENT = "remote_has_transferable_content"
    __KEY_FILE_TRANSFERRED_SIZE = "transferred_size"
    __KEY_FILE_DOWNLOAD_PROGRESS = "download_progress"
    __KEY_FILE_DOWNLOADING_SPEED = "downloading_speed"
    __KEY_FILE_ETA = "eta"
    __KEY_FILE_IS_EXTRACTABLE = "is_extractable"
    __KEY_FILE_IS_STOPPABLE = "is_stoppable"
    __KEY_FILE_LOCAL_CREATED_TIMESTAMP = "local_created_timestamp"
    __KEY_FILE_LOCAL_MODIFIED_TIMESTAMP = "local_modified_timestamp"
    __KEY_FILE_REMOTE_CREATED_TIMESTAMP = "remote_created_timestamp"
    __KEY_FILE_REMOTE_MODIFIED_TIMESTAMP = "remote_modified_timestamp"
    __KEY_FILE_DOWNLOADED_TIMESTAMP = "downloaded_timestamp"
    __KEY_FILE_FULL_PATH = "full_path"
    __KEY_FILE_ID = "file_id"
    __KEY_FILE_PATH_PAIR_ID = "path_pair_id"
    __KEY_FILE_PATH_PAIR_NAME = "path_pair_name"
    __KEY_FILE_VALIDATION_PROGRESS = "validation_progress"
    __KEY_FILE_VALIDATION_ERROR = "validation_error"
    __KEY_FILE_CORRUPT_CHUNKS = "corrupt_chunks"
    __KEY_FILE_FINAL_MOVE_SUCCEEDED = "final_move_succeeded"
    __KEY_FILE_CHILDREN = "children"

    @staticmethod
    def __model_file_to_json_dict(model_file: ModelFile) -> dict[str, object]:
        json_dict: dict[str, object] = {}
        json_dict[SerializeModel.__KEY_FILE_NAME] = model_file.name
        json_dict[SerializeModel.__KEY_FILE_IS_DIR] = model_file.is_dir
        json_dict[SerializeModel.__KEY_FILE_STATE] = SerializeModel.__VALUES_FILE_STATE[model_file.state]
        json_dict[SerializeModel.__KEY_FILE_REMOTE_SIZE] = model_file.remote_size
        json_dict[SerializeModel.__KEY_FILE_LOCAL_SIZE] = model_file.local_size
        json_dict[SerializeModel.__KEY_FILE_REMOTE_PRESENT] = model_file.remote_present
        json_dict[SerializeModel.__KEY_FILE_LOCAL_PRESENT] = model_file.local_present
        json_dict[SerializeModel.__KEY_FILE_REMOTE_HAS_TRANSFERABLE_CONTENT] = \
            model_file.remote_has_transferable_content
        json_dict[SerializeModel.__KEY_FILE_TRANSFERRED_SIZE] = model_file.transferred_size
        json_dict[SerializeModel.__KEY_FILE_DOWNLOAD_PROGRESS] = model_file.download_progress
        json_dict[SerializeModel.__KEY_FILE_DOWNLOADING_SPEED] = model_file.downloading_speed
        json_dict[SerializeModel.__KEY_FILE_ETA] = model_file.eta
        json_dict[SerializeModel.__KEY_FILE_IS_EXTRACTABLE] = model_file.is_extractable
        json_dict[SerializeModel.__KEY_FILE_IS_STOPPABLE] = model_file.is_stoppable
        json_dict[SerializeModel.__KEY_FILE_LOCAL_CREATED_TIMESTAMP] = \
            str(model_file.local_created_timestamp.timestamp()) if model_file.local_created_timestamp else None
        json_dict[SerializeModel.__KEY_FILE_LOCAL_MODIFIED_TIMESTAMP] = \
            str(model_file.local_modified_timestamp.timestamp()) if model_file.local_modified_timestamp else None
        json_dict[SerializeModel.__KEY_FILE_REMOTE_CREATED_TIMESTAMP] = \
            str(model_file.remote_created_timestamp.timestamp()) if model_file.remote_created_timestamp else None
        json_dict[SerializeModel.__KEY_FILE_REMOTE_MODIFIED_TIMESTAMP] = \
            str(model_file.remote_modified_timestamp.timestamp()) if model_file.remote_modified_timestamp else None
        json_dict[SerializeModel.__KEY_FILE_DOWNLOADED_TIMESTAMP] = \
            str(model_file.downloaded_timestamp.timestamp()) if model_file.downloaded_timestamp else None
        json_dict[SerializeModel.__KEY_FILE_FULL_PATH] = model_file.full_path
        json_dict[SerializeModel.__KEY_FILE_ID] = model_file.file_id
        json_dict[SerializeModel.__KEY_FILE_PATH_PAIR_ID] = model_file.path_pair_id
        json_dict[SerializeModel.__KEY_FILE_PATH_PAIR_NAME] = model_file.path_pair_name
        json_dict[SerializeModel.__KEY_FILE_VALIDATION_PROGRESS] = model_file.validation_progress
        json_dict[SerializeModel.__KEY_FILE_VALIDATION_ERROR] = model_file.validation_error
        json_dict[SerializeModel.__KEY_FILE_CORRUPT_CHUNKS] = model_file.corrupt_chunks
        json_dict[SerializeModel.__KEY_FILE_FINAL_MOVE_SUCCEEDED] = model_file.final_move_succeeded
        json_dict[SerializeModel.__KEY_FILE_CHILDREN] = [
            SerializeModel.__model_file_to_json_dict(child)
            for child in model_file.get_children()
        ]
        return json_dict

    def model(self, model_files: List[ModelFile]) -> str:
        """
        Serialize the model
        :return:
        """
        model_json_list = [SerializeModel.__model_file_to_json_dict(f) for f in model_files]
        model_json = json.dumps(model_json_list)
        return self._sse_pack(event=SerializeModel.__EVENT_INIT,
                              data=model_json)

    def update_event(self, event: UpdateEvent) -> str:
        model_file_json_dict = {
            SerializeModel.__KEY_UPDATE_OLD_FILE:
                SerializeModel.__model_file_to_json_dict(event.old_file) if event.old_file else None,
            SerializeModel.__KEY_UPDATE_NEW_FILE:
                SerializeModel.__model_file_to_json_dict(event.new_file) if event.new_file else None
        }
        if event.trace_metadata is not None:
            model_file_json_dict[SerializeModel.__KEY_UPDATE_TRACE] = event.trace_metadata
        model_file_json = json.dumps(model_file_json_dict)
        return self._sse_pack(event=SerializeModel.__EVENT_UPDATE[event.change],
                              data=model_file_json)
