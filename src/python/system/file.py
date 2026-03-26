# Copyright 2017, Inderpreet Singh, All rights reserved.

from typing import List
from datetime import datetime


class SystemFile:
    """
    Represents a system file or directory
    """
    def __init__(self,
                 name: str,
                 size: int,
                 is_dir: bool = False,
                 time_created: datetime = None,
                 time_modified: datetime = None,
                 is_staging: bool = False):
        if size < 0:
            raise ValueError("File size must be greater than zero")
        self.__name = name
        self.__size = size  # in bytes
        self.__is_dir = is_dir
        self.__timestamp_created = time_created
        self.__timestamp_modified = time_modified
        self.__children = []
        self.__path_pair_id = None
        self.__path_pair_name = None
        self.__is_staging = is_staging

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        return str(self.__dict__)

    @property
    def name(self) -> str: return self.__name

    @property
    def size(self) -> int: return self.__size

    @property
    def is_dir(self) -> bool: return self.__is_dir

    @property
    def timestamp_created(self) -> datetime: return self.__timestamp_created

    @property
    def timestamp_modified(self) -> datetime: return self.__timestamp_modified

    @property
    def children(self) -> List["SystemFile"]: return self.__children

    @property
    def path_pair_id(self) -> str: return self.__path_pair_id

    @path_pair_id.setter
    def path_pair_id(self, path_pair_id: str):
        if path_pair_id is not None and type(path_pair_id) != str:
            raise TypeError
        self.__path_pair_id = path_pair_id

    @property
    def path_pair_name(self) -> str: return self.__path_pair_name

    @path_pair_name.setter
    def path_pair_name(self, path_pair_name: str):
        if path_pair_name is not None and type(path_pair_name) != str:
            raise TypeError
        self.__path_pair_name = path_pair_name

    @property
    def is_staging(self) -> bool: return self.__is_staging

    @is_staging.setter
    def is_staging(self, is_staging: bool):
        if type(is_staging) != bool:
            raise TypeError
        self.__is_staging = is_staging

    def add_child(self, file: "SystemFile"):
        if not self.__is_dir:
            raise TypeError("Cannot add children to a file")
        self.__children.append(file)

    def to_dict(self) -> dict:
        d = {
            "name": self.__name,
            "size": self.__size,
            "is_dir": self.__is_dir,
        }
        if self.__timestamp_created is not None:
            d["time_created"] = self.__timestamp_created.isoformat()
        if self.__timestamp_modified is not None:
            d["time_modified"] = self.__timestamp_modified.isoformat()
        if self.__path_pair_id is not None:
            d["path_pair_id"] = self.__path_pair_id
        if self.__path_pair_name is not None:
            d["path_pair_name"] = self.__path_pair_name
        if self.__is_staging:
            d["is_staging"] = True
        if self.__children:
            d["children"] = [child.to_dict() for child in self.__children]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "SystemFile":
        time_created = None
        time_modified = None
        if "time_created" in data and data["time_created"] is not None:
            time_created = datetime.fromisoformat(data["time_created"])
        if "time_modified" in data and data["time_modified"] is not None:
            time_modified = datetime.fromisoformat(data["time_modified"])
        system_file = cls(
            name=data["name"],
            size=data["size"],
            is_dir=data.get("is_dir", False),
            time_created=time_created,
            time_modified=time_modified,
            is_staging=data.get("is_staging", False),
        )
        system_file.path_pair_id = data.get("path_pair_id")
        system_file.path_pair_name = data.get("path_pair_name")
        for child_data in data.get("children", []):
            system_file.add_child(cls.from_dict(child_data))
        return system_file
