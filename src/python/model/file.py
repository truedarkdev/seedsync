# Copyright 2017, Inderpreet Singh, All rights reserved.

from collections.abc import Iterator
from datetime import datetime
from enum import Enum
import json
from typing import Optional, List
import copy
import os


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

    def __init__(self, name: str, is_dir: bool):
        self.__name = name  # file or folder name
        self.__is_dir = is_dir  # True if this is a dir, False if file
        self.__state = ModelFile.State.DEFAULT  # status
        self.__remote_size: Optional[int] = None  # remote size in bytes, None if file does not exist
        self.__local_size: Optional[int] = None  # local size in bytes, None if file does not exist
        # Presence is intentionally independent from sizes: a zero-byte file is
        # present, while a directory can exist without any transferable files.
        self.__remote_present = False
        self.__local_present = False
        self.__remote_has_transferable_content = False
        self.__remote_presence_explicit = False
        self.__local_presence_explicit = False
        self.__remote_content_explicit = False
        self.__transferred_size: Optional[int] = None  # transferred size in bytes, None if file does not exist
        self.__download_progress: Optional[int] = None  # active download progress percent, None if unavailable
        self.__downloading_speed: Optional[int] = None  # in bytes / sec, None if not downloading
        self.__eta: Optional[int] = None  # est. time remaining in seconds, None if not available
        self.__is_extractable = False  # whether file is an archive or dir contains archives
        self.__is_stoppable = False  # whether stop is currently safe and enabled
        self.__local_created_timestamp: Optional[datetime] = None
        self.__local_modified_timestamp: Optional[datetime] = None
        self.__remote_created_timestamp: Optional[datetime] = None
        self.__remote_modified_timestamp: Optional[datetime] = None
        self.__downloaded_timestamp: Optional[datetime] = None
        self.__validation_progress: Optional[int] = None
        self.__validation_error: Optional[str] = None
        self.__corrupt_chunks: Optional[List[int]] = None
        self.__final_move_succeeded = False
        # timestamp of the latest update
        # Note: timestamp is not part of equality operator
        self.__update_timestamp = datetime.now()
        self.__children: List[ModelFile] = []  # children files
        self.__parent: Optional[ModelFile] = None  # direct predecessor
        self.__path_pair_id: Optional[str] = None
        self.__path_pair_name: Optional[str] = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelFile):
            return NotImplemented
        # disregard in comparisons:
        #   timestamp: we don't care about it
        #   parent: semantics are to check self and children only
        #   children: check these manually for easier debugging
        ka = set(self.__dict__).difference({
            "_ModelFile__update_timestamp",
            "_ModelFile__parent",
            "_ModelFile__children",
            "_ModelFile__remote_presence_explicit",
            "_ModelFile__local_presence_explicit",
            "_ModelFile__remote_content_explicit",
        })
        kb = set(other.__dict__).difference({
            "_ModelFile__update_timestamp",
            "_ModelFile__parent",
            "_ModelFile__children",
            "_ModelFile__remote_presence_explicit",
            "_ModelFile__local_presence_explicit",
            "_ModelFile__remote_content_explicit",
        })
        # Check self properties
        if ka != kb:
            return False
        if not all(self.__dict__[k] == other.__dict__[k] for k in ka):
            return False

        # Check children's properties
        if len(self.__children) != len(other.__children):
            return False
        my_children_dict = {f.name: f for f in self.__children}
        other_children_dict = {f.name: f for f in other.__children}
        if my_children_dict.keys() != other_children_dict.keys():
            return False
        for name in my_children_dict.keys():
            if my_children_dict[name] != other_children_dict[name]:
                return False

        return True

    def __repr__(self) -> str:
        return str(self.__dict__)

    @property
    def name(self) -> str: return self.__name

    @property
    def is_dir(self) -> bool: return self.__is_dir

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
        if not self.__remote_presence_explicit:
            self.__remote_present = remote_size is not None
        if not self.__remote_content_explicit:
            # Directly assembled compatibility objects may only carry a
            # positive directory size; treat that as content-bearing. The
            # model builder sets an explicit recursive signal for scanned
            # trees, including genuinely empty directories.
            self.__remote_has_transferable_content = (
                remote_size is not None and (not self.__is_dir or remote_size > 0)
            )

    @property
    def remote_present(self) -> bool: return self.__remote_present

    @remote_present.setter
    def remote_present(self, remote_present: bool):
        if type(remote_present) is not bool:
            raise TypeError
        self.__remote_presence_explicit = True
        self.__remote_present = remote_present

    @property
    def local_present(self) -> bool: return self.__local_present

    @local_present.setter
    def local_present(self, local_present: bool):
        if type(local_present) is not bool:
            raise TypeError
        self.__local_presence_explicit = True
        self.__local_present = local_present

    @property
    def remote_has_transferable_content(self) -> bool:
        return self.__remote_has_transferable_content

    @remote_has_transferable_content.setter
    def remote_has_transferable_content(self, value: bool):
        if type(value) is not bool:
            raise TypeError
        self.__remote_content_explicit = True
        self.__remote_has_transferable_content = value

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
        if not self.__local_presence_explicit:
            self.__local_present = local_size is not None

    @property
    def transferred_size(self) -> Optional[int]: return self.__transferred_size

    @transferred_size.setter
    def transferred_size(self, transferred_size: Optional[int]):
        if type(transferred_size) == int:
            if transferred_size < 0:
                raise ValueError
            self.__transferred_size = transferred_size
        elif transferred_size is None:
            self.__transferred_size = transferred_size
        else:
            raise TypeError

    @property
    def download_progress(self) -> Optional[int]: return self.__download_progress

    @download_progress.setter
    def download_progress(self, download_progress: Optional[int]):
        if type(download_progress) == int:
            if download_progress < 0 or download_progress > 100:
                raise ValueError
            self.__download_progress = download_progress
        elif download_progress is None:
            self.__download_progress = download_progress
        else:
            raise TypeError

    @property
    def downloading_speed(self) -> Optional[int]: return self.__downloading_speed

    @downloading_speed.setter
    def downloading_speed(self, downloading_speed: Optional[int]):
        if type(downloading_speed) == int:
            if downloading_speed < 0:
                raise ValueError
            self.__downloading_speed = downloading_speed
        elif downloading_speed is None:
            self.__downloading_speed = downloading_speed
        else:
            raise TypeError

    @property
    def update_timestamp(self) -> datetime: return self.__update_timestamp

    @update_timestamp.setter
    def update_timestamp(self, update_timestamp: datetime):
        if type(update_timestamp) != datetime:
            raise TypeError
        self.__update_timestamp = update_timestamp

    @property
    def eta(self) -> Optional[int]: return self.__eta

    @eta.setter
    def eta(self, eta: Optional[int]):
        if type(eta) == int:
            if eta < 0:
                raise ValueError
            self.__eta = eta
        elif eta is None:
            self.__eta = eta
        else:
            raise TypeError

    @property
    def is_extractable(self) -> bool: return self.__is_extractable

    @is_extractable.setter
    def is_extractable(self, is_extractable: bool):
        self.__is_extractable = is_extractable

    @property
    def is_stoppable(self) -> bool: return self.__is_stoppable

    @is_stoppable.setter
    def is_stoppable(self, is_stoppable: bool):
        self.__is_stoppable = is_stoppable

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
    def downloaded_timestamp(self) -> datetime | None: return self.__downloaded_timestamp

    @downloaded_timestamp.setter
    def downloaded_timestamp(self, downloaded_timestamp: datetime | None):
        if downloaded_timestamp is not None and type(downloaded_timestamp) != datetime:
            raise TypeError
        self.__downloaded_timestamp = downloaded_timestamp

    @property
    def validation_progress(self) -> Optional[int]:
        return self.__validation_progress

    @validation_progress.setter
    def validation_progress(self, validation_progress: Optional[int]):
        if type(validation_progress) == int:
            if validation_progress < 0 or validation_progress > 100:
                raise ValueError
            self.__validation_progress = validation_progress
        elif validation_progress is None:
            self.__validation_progress = None
        else:
            raise TypeError

    @property
    def validation_error(self) -> Optional[str]:
        return self.__validation_error

    @validation_error.setter
    def validation_error(self, validation_error: Optional[str]):
        if validation_error is not None and type(validation_error) != str:
            raise TypeError
        self.__validation_error = validation_error

    @property
    def corrupt_chunks(self) -> Optional[List[int]]:
        return None if self.__corrupt_chunks is None else copy.copy(self.__corrupt_chunks)

    @corrupt_chunks.setter
    def corrupt_chunks(self, corrupt_chunks: Optional[List[int]]):
        if corrupt_chunks is None:
            self.__corrupt_chunks = None
            return
        if type(corrupt_chunks) != list or not all(type(chunk) == int and chunk >= 0 for chunk in corrupt_chunks):
            raise TypeError
        self.__corrupt_chunks = copy.copy(corrupt_chunks)

    @property
    def final_move_succeeded(self) -> bool:
        return self.__final_move_succeeded

    @final_move_succeeded.setter
    def final_move_succeeded(self, value: bool):
        if type(value) is not bool:
            raise TypeError
        self.__final_move_succeeded = value

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
        if child_file.name in (f.name for f in self.__children):
            raise ValueError("Cannot add child more than once")
        self.__children.append(child_file)
        child_file.__parent = self

    def get_children(self) -> List["ModelFile"]:
        return copy.copy(self.__children)

    def iter_children(self) -> Iterator["ModelFile"]:
        # Read-only iterator over the live child list. Do not add or remove
        # children while consuming it; use get_children() if a mutation-tolerant
        # snapshot copy is needed.
        return iter(self.__children)

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
