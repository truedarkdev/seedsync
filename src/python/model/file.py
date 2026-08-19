# Copyright 2017, Inderpreet Singh, All rights reserved.

from collections.abc import Iterator
from datetime import datetime
from enum import Enum
import json
from typing import Optional, List
import copy
import os
import time


class ModelFile:
    """
    Represents a file or directory
    The information in this object may be inconsistent. E.g. the size of a directory
    may not match the sum of its children. This is allowed as a source may have
    updated only certain levels in the hierarchy. Specifically for this example,
    an Lftp status provides local sizes for a downloading directory but not its
    children.
    """
    class State(Enum):
        DEFAULT = 0
        DOWNLOADING = 1
        QUEUED = 2
        DOWNLOADED = 3
        DELETED = 4
        EXTRACTING = 5
        EXTRACTED = 6
        VALIDATING = 7
        VALIDATED = 8
        CORRUPT = 9
        MOVE_FAILED = 10

    __slots__ = (
        "__name",
        "__flags",
        "__state",
        "__remote_size",
        "__local_size",
        "__local_created_timestamp",
        "__local_modified_timestamp",
        "__remote_created_timestamp",
        "__remote_modified_timestamp",
        "__runtime",
        "__update_timestamp",
        "__children",
        "__parent",
        "__path_pair_id",
        "__path_pair_name",
    )
    __DIR, __REMOTE_PRESENT, __LOCAL_PRESENT, __REMOTE_CONTENT = 1, 2, 4, 8
    __REMOTE_PRESENT_EXPLICIT, __LOCAL_PRESENT_EXPLICIT, __REMOTE_CONTENT_EXPLICIT = 16, 32, 64

    def __init__(self, name: str, is_dir: bool):
        self.__name = name  # file or folder name
        self.__flags = self.__DIR if is_dir else 0
        self.__state = ModelFile.State.DEFAULT  # status
        self.__remote_size: Optional[int] = None  # remote size in bytes, None if file does not exist
        self.__local_size: Optional[int] = None  # local size in bytes, None if file does not exist
        # Presence is intentionally independent from sizes: a zero-byte file is
        # present, while a directory can exist without any transferable files.
        self.__local_created_timestamp: Optional[datetime] = None
        self.__local_modified_timestamp: Optional[datetime] = None
        self.__remote_created_timestamp: Optional[datetime] = None
        self.__remote_modified_timestamp: Optional[datetime] = None
        self.__runtime: Optional[dict[str, object]] = None
        # timestamp of the latest update
        # Note: timestamp is not part of equality operator
        self.__update_timestamp: datetime | float = time.time()
        # Most files are leaves.  Allocate a mutable list only for directories
        # that actually receive children; public snapshots still return lists.
        self.__children: Optional[List[ModelFile]] = None
        self.__parent: Optional[ModelFile] = None  # direct predecessor
        self.__path_pair_id: Optional[str] = None
        self.__path_pair_name: Optional[str] = None

    def __flag(self, flag: int) -> bool:
        return bool(self.__flags & flag)

    def __set_flag(self, flag: int, value: bool) -> None:
        self.__flags = self.__flags | flag if value else self.__flags & ~flag

    def __runtime_value(self, key: str, default: object = None) -> object:
        return default if self.__runtime is None else self.__runtime.get(key, default)

    def __set_runtime(self, key: str, value: object, default: object = None) -> None:
        if value is default or type(value) is type(default) and value == default:
            if self.__runtime is not None:
                self.__runtime.pop(key, None)
                if not self.__runtime:
                    self.__runtime = None
            return
        if self.__runtime is None:
            self.__runtime = {}
        self.__runtime[key] = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelFile):
            return NotImplemented
        # disregard in comparisons:
        #   timestamp: we don't care about it
        #   parent: semantics are to check self and children only
        #   children: check these manually for easier debugging
        if self.__comparison_attributes() != other.__comparison_attributes():
            return False

        # Check children's properties
        my_children = self.__children or ()
        other_children = other.__children or ()
        if len(my_children) != len(other_children):
            return False
        my_children_dict = {f.name: f for f in my_children}
        other_children_dict = {f.name: f for f in other_children}
        if my_children_dict.keys() != other_children_dict.keys():
            return False
        for name in my_children_dict.keys():
            if my_children_dict[name] != other_children_dict[name]:
                return False

        return True

    def __repr__(self) -> str:
        return str(self.__diagnostic_attributes())

    def __comparison_attributes(self) -> tuple[object, ...]:
        """Fields historically compared through ``__dict__``.

        Update time, parent linkage, children, and explicit-presence bookkeeping
        intentionally remain outside ModelFile equality.
        """
        return (
            self.__name,
            self.is_dir,
            self.__state,
            self.__remote_size,
            self.__local_size,
            self.remote_present,
            self.local_present,
            self.remote_has_transferable_content,
            self.transferred_size,
            self.display_size_total,
            self.display_transferred_size,
            self.download_progress,
            self.downloading_speed,
            self.eta,
            self.is_extractable,
            self.is_stoppable,
            self.__local_created_timestamp,
            self.__local_modified_timestamp,
            self.__remote_created_timestamp,
            self.__remote_modified_timestamp,
            self.downloaded_timestamp,
            self.validation_progress,
            self.validation_error,
            self.corrupt_chunks,
            self.final_move_succeeded,
            self.__path_pair_id,
            self.__path_pair_name,
        )

    def __diagnostic_attributes(self) -> dict[str, object]:
        return {
            "_ModelFile__name": self.__name,
            "_ModelFile__is_dir": self.is_dir,
            "_ModelFile__state": self.__state,
            "_ModelFile__remote_size": self.__remote_size,
            "_ModelFile__local_size": self.__local_size,
            "_ModelFile__remote_present": self.remote_present,
            "_ModelFile__local_present": self.local_present,
            "_ModelFile__remote_has_transferable_content": self.remote_has_transferable_content,
            "_ModelFile__remote_presence_explicit": self.__flag(self.__REMOTE_PRESENT_EXPLICIT),
            "_ModelFile__local_presence_explicit": self.__flag(self.__LOCAL_PRESENT_EXPLICIT),
            "_ModelFile__remote_content_explicit": self.__flag(self.__REMOTE_CONTENT_EXPLICIT),
            "_ModelFile__transferred_size": self.transferred_size,
            "_ModelFile__display_size_total": self.display_size_total,
            "_ModelFile__display_transferred_size": self.display_transferred_size,
            "_ModelFile__download_progress": self.download_progress,
            "_ModelFile__downloading_speed": self.downloading_speed,
            "_ModelFile__eta": self.eta,
            "_ModelFile__is_extractable": self.is_extractable,
            "_ModelFile__is_stoppable": self.is_stoppable,
            "_ModelFile__local_created_timestamp": self.__local_created_timestamp,
            "_ModelFile__local_modified_timestamp": self.__local_modified_timestamp,
            "_ModelFile__remote_created_timestamp": self.__remote_created_timestamp,
            "_ModelFile__remote_modified_timestamp": self.__remote_modified_timestamp,
            "_ModelFile__downloaded_timestamp": self.downloaded_timestamp,
            "_ModelFile__validation_progress": self.validation_progress,
            "_ModelFile__validation_error": self.validation_error,
            "_ModelFile__corrupt_chunks": self.corrupt_chunks,
            "_ModelFile__final_move_succeeded": self.final_move_succeeded,
            "_ModelFile__update_timestamp": self.update_timestamp,
            "_ModelFile__children": self.__children or [],
            "_ModelFile__parent": self.__parent,
            "_ModelFile__path_pair_id": self.__path_pair_id,
            "_ModelFile__path_pair_name": self.__path_pair_name,
        }

    @property
    def name(self) -> str: return self.__name

    @property
    def is_dir(self) -> bool: return self.__flag(self.__DIR)

    @property
    def state(self) -> State: return self.__state

    @state.setter
    def state(self, state: State):
        if type(state) != ModelFile.State:
            raise TypeError
        self.__state = state

    @property
    def remote_size(self) -> Optional[int]: return self.__remote_size

    @remote_size.setter
    def remote_size(self, remote_size: Optional[int]):
        if type(remote_size) == int:
            if remote_size < 0:
                raise ValueError
            self.__remote_size = remote_size
        elif remote_size is None:
            self.__remote_size = remote_size
        else:
            raise TypeError
        if not self.__flag(self.__REMOTE_PRESENT_EXPLICIT):
            self.__set_flag(self.__REMOTE_PRESENT, remote_size is not None)
        if not self.__flag(self.__REMOTE_CONTENT_EXPLICIT):
            # Directly assembled compatibility objects may only carry a
            # positive directory size; treat that as content-bearing. The
            # model builder sets an explicit recursive signal for scanned
            # trees, including genuinely empty directories.
            self.__set_flag(self.__REMOTE_CONTENT, remote_size is not None and (not self.is_dir or remote_size > 0))

    @property
    def remote_present(self) -> bool: return self.__flag(self.__REMOTE_PRESENT)

    @remote_present.setter
    def remote_present(self, remote_present: bool):
        if type(remote_present) is not bool:
            raise TypeError
        self.__set_flag(self.__REMOTE_PRESENT_EXPLICIT, True)
        self.__set_flag(self.__REMOTE_PRESENT, remote_present)

    @property
    def local_present(self) -> bool: return self.__flag(self.__LOCAL_PRESENT)

    @local_present.setter
    def local_present(self, local_present: bool):
        if type(local_present) is not bool:
            raise TypeError
        self.__set_flag(self.__LOCAL_PRESENT_EXPLICIT, True)
        self.__set_flag(self.__LOCAL_PRESENT, local_present)

    @property
    def remote_has_transferable_content(self) -> bool:
        return self.__flag(self.__REMOTE_CONTENT)

    @remote_has_transferable_content.setter
    def remote_has_transferable_content(self, value: bool):
        if type(value) is not bool:
            raise TypeError
        self.__set_flag(self.__REMOTE_CONTENT_EXPLICIT, True)
        self.__set_flag(self.__REMOTE_CONTENT, value)

    @property
    def local_size(self) -> Optional[int]: return self.__local_size

    @local_size.setter
    def local_size(self, local_size: Optional[int]):
        if type(local_size) == int:
            if local_size < 0:
                raise ValueError
            self.__local_size = local_size
        elif local_size is None:
            self.__local_size = local_size
        else:
            raise TypeError
        if not self.__flag(self.__LOCAL_PRESENT_EXPLICIT):
            self.__set_flag(self.__LOCAL_PRESENT, local_size is not None)

    @property
    def transferred_size(self) -> Optional[int]: return self.__runtime_value("transferred_size")  # type: ignore[return-value]

    @transferred_size.setter
    def transferred_size(self, transferred_size: Optional[int]):
        if type(transferred_size) == int:
            if transferred_size < 0:
                raise ValueError
            self.__set_runtime("transferred_size", transferred_size)
        elif transferred_size is None:
            self.__set_runtime("transferred_size", transferred_size)
        else:
            raise TypeError

    @property
    def display_size_total(self) -> Optional[int]: return self.__runtime_value("display_size_total")  # type: ignore[return-value]

    @display_size_total.setter
    def display_size_total(self, size: Optional[int]):
        if type(size) == int and size >= 0 or size is None:
            self.__set_runtime("display_size_total", size)
        else:
            raise TypeError

    @property
    def display_transferred_size(self) -> Optional[int]: return self.__runtime_value("display_transferred_size")  # type: ignore[return-value]

    @display_transferred_size.setter
    def display_transferred_size(self, size: Optional[int]):
        if type(size) == int and size >= 0 or size is None:
            self.__set_runtime("display_transferred_size", size)
        else:
            raise TypeError

    @property
    def download_progress(self) -> Optional[int]: return self.__runtime_value("download_progress")  # type: ignore[return-value]

    @download_progress.setter
    def download_progress(self, download_progress: Optional[int]):
        if type(download_progress) == int:
            if download_progress < 0 or download_progress > 100:
                raise ValueError
            self.__set_runtime("download_progress", download_progress)
        elif download_progress is None:
            self.__set_runtime("download_progress", download_progress)
        else:
            raise TypeError

    @property
    def downloading_speed(self) -> Optional[int]: return self.__runtime_value("downloading_speed")  # type: ignore[return-value]

    @downloading_speed.setter
    def downloading_speed(self, downloading_speed: Optional[int]):
        if type(downloading_speed) == int:
            if downloading_speed < 0:
                raise ValueError
            self.__set_runtime("downloading_speed", downloading_speed)
        elif downloading_speed is None:
            self.__set_runtime("downloading_speed", downloading_speed)
        else:
            raise TypeError

    @property
    def update_timestamp(self) -> datetime:
        return self.__update_timestamp if isinstance(self.__update_timestamp, datetime) else datetime.fromtimestamp(self.__update_timestamp)

    @update_timestamp.setter
    def update_timestamp(self, update_timestamp: datetime):
        if type(update_timestamp) != datetime:
            raise TypeError
        self.__update_timestamp = update_timestamp

    @property
    def eta(self) -> Optional[int]: return self.__runtime_value("eta")  # type: ignore[return-value]

    @eta.setter
    def eta(self, eta: Optional[int]):
        if type(eta) == int:
            if eta < 0:
                raise ValueError
            self.__set_runtime("eta", eta)
        elif eta is None:
            self.__set_runtime("eta", eta)
        else:
            raise TypeError

    @property
    def is_extractable(self) -> bool: return self.__runtime_value("is_extractable", False)  # type: ignore[return-value]

    @is_extractable.setter
    def is_extractable(self, is_extractable: bool):
        self.__set_runtime("is_extractable", is_extractable, False)

    @property
    def is_stoppable(self) -> bool: return self.__runtime_value("is_stoppable", False)  # type: ignore[return-value]

    @is_stoppable.setter
    def is_stoppable(self, is_stoppable: bool):
        self.__set_runtime("is_stoppable", is_stoppable, False)

    @property
    def local_created_timestamp(self) -> datetime | None: return self.__local_created_timestamp

    @local_created_timestamp.setter
    def local_created_timestamp(self, local_created_timestamp: datetime):
        if type(local_created_timestamp) != datetime:
            raise TypeError
        self.__local_created_timestamp = local_created_timestamp

    @property
    def local_modified_timestamp(self) -> datetime | None: return self.__local_modified_timestamp

    @local_modified_timestamp.setter
    def local_modified_timestamp(self, local_modified_timestamp: datetime):
        if type(local_modified_timestamp) != datetime:
            raise TypeError
        self.__local_modified_timestamp = local_modified_timestamp

    @property
    def remote_created_timestamp(self) -> datetime | None: return self.__remote_created_timestamp

    @remote_created_timestamp.setter
    def remote_created_timestamp(self, remote_created_timestamp: datetime):
        if type(remote_created_timestamp) != datetime:
            raise TypeError
        self.__remote_created_timestamp = remote_created_timestamp

    @property
    def remote_modified_timestamp(self) -> datetime | None: return self.__remote_modified_timestamp

    @remote_modified_timestamp.setter
    def remote_modified_timestamp(self, remote_modified_timestamp: datetime):
        if type(remote_modified_timestamp) != datetime:
            raise TypeError
        self.__remote_modified_timestamp = remote_modified_timestamp

    @property
    def downloaded_timestamp(self) -> datetime | None: return self.__runtime_value("downloaded_timestamp")  # type: ignore[return-value]

    @downloaded_timestamp.setter
    def downloaded_timestamp(self, downloaded_timestamp: datetime | None):
        if downloaded_timestamp is not None and type(downloaded_timestamp) != datetime:
            raise TypeError
        self.__set_runtime("downloaded_timestamp", downloaded_timestamp)

    @property
    def validation_progress(self) -> Optional[int]:
        return self.__runtime_value("validation_progress")  # type: ignore[return-value]

    @validation_progress.setter
    def validation_progress(self, validation_progress: Optional[int]):
        if type(validation_progress) == int:
            if validation_progress < 0 or validation_progress > 100:
                raise ValueError
            self.__set_runtime("validation_progress", validation_progress)
        elif validation_progress is None:
            self.__set_runtime("validation_progress", None)
        else:
            raise TypeError

    @property
    def validation_error(self) -> Optional[str]:
        return self.__runtime_value("validation_error")  # type: ignore[return-value]

    @validation_error.setter
    def validation_error(self, validation_error: Optional[str]):
        if validation_error is not None and type(validation_error) != str:
            raise TypeError
        self.__set_runtime("validation_error", validation_error)

    @property
    def corrupt_chunks(self) -> Optional[List[int]]:
        value = self.__runtime_value("corrupt_chunks")
        return None if value is None else copy.copy(value)  # type: ignore[arg-type]

    @corrupt_chunks.setter
    def corrupt_chunks(self, corrupt_chunks: Optional[List[int]]):
        if corrupt_chunks is None:
            self.__set_runtime("corrupt_chunks", None)
            return
        if type(corrupt_chunks) != list or not all(type(chunk) == int and chunk >= 0 for chunk in corrupt_chunks):
            raise TypeError
        self.__set_runtime("corrupt_chunks", copy.copy(corrupt_chunks))

    @property
    def final_move_succeeded(self) -> bool:
        return bool(self.__runtime_value("final_move_succeeded", False))

    @final_move_succeeded.setter
    def final_move_succeeded(self, value: bool):
        if type(value) is not bool:
            raise TypeError
        self.__set_runtime("final_move_succeeded", value, False)

    @property
    def full_path(self) -> str:
        """Full path including all predecessors"""
        if self.__parent:
            return os.path.join(self.__parent.full_path, self.name)
        return self.name

    @staticmethod
    def build_file_id(full_path: str, path_pair_id: Optional[str]) -> str:
        if path_pair_id is None:
            return full_path
        return json.dumps([path_pair_id, full_path], separators=(",", ":"))

    @property
    def file_id(self) -> str:
        return ModelFile.build_file_id(self.full_path, self.path_pair_id)

    def add_child(self, child_file: "ModelFile") -> None:
        if not self.is_dir:
            raise TypeError("Cannot add child to a non-directory")
        if child_file is self:
            raise ValueError("Cannot add parent as a child")
        if child_file.name in (f.name for f in self.__children or ()):
            raise ValueError("Cannot add child more than once")
        if self.__children is None:
            self.__children = []
        self.__children.append(child_file)
        child_file.__parent = self

    def get_children(self) -> List["ModelFile"]:
        return copy.copy(self.__children) if self.__children is not None else []

    @property
    def child_count(self) -> int:
        """Number of direct children without allocating a list snapshot."""
        return len(self.__children) if self.__children is not None else 0

    def iter_children(self) -> Iterator["ModelFile"]:
        # Read-only iterator over the live child list. Do not add or remove
        # children while consuming it; use get_children() if a mutation-tolerant
        # snapshot copy is needed.
        return iter(self.__children) if self.__children is not None else iter(())

    @property
    def parent(self) -> Optional["ModelFile"]:
        return self.__parent

    @property
    def path_pair_id(self) -> Optional[str]:
        return self.__path_pair_id

    @path_pair_id.setter
    def path_pair_id(self, path_pair_id: Optional[str]):
        if path_pair_id is not None and type(path_pair_id) != str:
            raise TypeError
        self.__path_pair_id = path_pair_id

    @property
    def path_pair_name(self) -> Optional[str]:
        return self.__path_pair_name

    @path_pair_name.setter
    def path_pair_name(self, path_pair_name: Optional[str]):
        if path_pair_name is not None and type(path_pair_name) != str:
            raise TypeError
        self.__path_pair_name = path_pair_name
