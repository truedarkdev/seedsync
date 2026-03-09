# Copyright 2017, Inderpreet Singh, All rights reserved.

from collections import namedtuple
from enum import Enum
from typing import List, Optional, Tuple

from model import ModelFile


class LftpJobStatus:
    """
    Represents the status of a single Lftp jobs
    """
    class Type(Enum):
        MIRROR = "mirror"
        PGET = "pget"

    class State(Enum):
        QUEUED = 0
        RUNNING = 1

    class TransferState(namedtuple("TransferState",
                                   ["size_local",
                                    "size_remote",
                                    "percent_local",
                                    "speed",
                                    "eta"])):
        """
        State of transfer for a file or entire download
          size_local: size in bytes that have been downloaded
          size_remote: size in bytes on the remote server (may not be available)
          percent_local: percent of bytes that have been downloaded (0-100)
          speed: transfer speed in bytes per second
          eta: est. remaining transfer time in seconds
        """
        pass

    def __init__(self,
                 job_id: int,
                 job_type: Type,
                 state: State,
                 name: str,
                 flags: str,
                 remote_path: Optional[str] = None,
                 local_path: Optional[str] = None):
        self.__id = job_id
        self.__type = job_type
        self.__state = state
        self.__name = name
        self.__flags = flags
        self.__remote_path = remote_path.strip() if remote_path is not None else None
        self.__local_path = local_path.strip() if local_path is not None else None
        self.__path_pair_id = None
        self.__path_pair_name = None
        self.__total_transfer_state = LftpJobStatus.TransferState(None, None, None, None, None)
        # dict of active file transfer states, maps filename to their transfer state
        # there's no hierarchical info for now
        self.__active_files_state = {}

    @property
    def id(self) -> int: return self.__id

    @property
    def type(self) -> Type: return self.__type

    @property
    def state(self) -> "LftpJobStatus.State": return self.__state

    @property
    def name(self) -> str: return self.__name

    @property
    def remote_path(self) -> Optional[str]: return self.__remote_path

    @property
    def local_path(self) -> Optional[str]: return self.__local_path

    @property
    def path_pair_id(self) -> Optional[str]: return self.__path_pair_id

    @path_pair_id.setter
    def path_pair_id(self, path_pair_id: Optional[str]):
        if path_pair_id is not None and type(path_pair_id) != str:
            raise TypeError
        self.__path_pair_id = path_pair_id

    @property
    def path_pair_name(self) -> Optional[str]: return self.__path_pair_name

    @path_pair_name.setter
    def path_pair_name(self, path_pair_name: Optional[str]):
        if path_pair_name is not None and type(path_pair_name) != str:
            raise TypeError
        self.__path_pair_name = path_pair_name

    @property
    def file_id(self) -> str:
        return ModelFile.build_file_id(self.__name, self.__path_pair_id)

    @property
    def total_transfer_state(self) -> TransferState:
        return self.__total_transfer_state

    @total_transfer_state.setter
    def total_transfer_state(self, total_transfer_state: TransferState):
        if self.__state == LftpJobStatus.State.QUEUED:
            raise TypeError("Cannot set transfer state on job of type queue")
        self.__total_transfer_state = total_transfer_state

    def add_active_file_transfer_state(self, filename: str, transfer_state: TransferState):
        if self.__state == LftpJobStatus.State.QUEUED:
            raise TypeError("Cannot set transfer state on job of type queue")
        self.__active_files_state[filename] = transfer_state

    def get_active_file_transfer_states(self) -> List[Tuple[str, TransferState]]:
        """
        Returns list of pairs (filename, transfer state)
        :return:
        """
        return list(zip(self.__active_files_state.keys(), self.__active_files_state.values()))

    def __eq__(self, other):
        ignored = {
            "_LftpJobStatus__remote_path",
            "_LftpJobStatus__local_path",
            "_LftpJobStatus__path_pair_id",
            "_LftpJobStatus__path_pair_name",
        }
        self_dict = {k: v for k, v in self.__dict__.items() if k not in ignored}
        other_dict = {k: v for k, v in other.__dict__.items() if k not in ignored}
        return self_dict == other_dict

    def __str__(self):
        return str(self.__dict__)

    def __repr__(self):
        return str(self.__dict__)
