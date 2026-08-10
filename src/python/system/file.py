# Copyright 2017, Inderpreet Singh, All rights reserved.

from collections.abc import Iterator
from typing import List, NotRequired, TypedDict
from datetime import datetime


class SystemFileData(TypedDict):
    name: str
    size: int
    is_dir: bool
    time_created: NotRequired[str | None]
    time_modified: NotRequired[str | None]
    path_pair_id: NotRequired[str]
    path_pair_name: NotRequired[str]
    is_staging: NotRequired[bool]
    children: NotRequired[list["SystemFileData"]]


class SystemFile:
    """
    Represents a system file or directory
    """
    __slots__ = (
        "__name",
        "__size",
        "__flags",
        "__timestamp_created",
        "__timestamp_modified",
        "__children",
        "__path_pair_id",
        "__path_pair_name",
    )
    __DIR, __STAGING, __STATUS_READY = 1, 2, 4

    def __init__(self,
                 name: str,
                 size: int,
                 is_dir: bool = False,
                 time_created: datetime | None = None,
                 time_modified: datetime | None = None,
        is_staging: bool = False):
        if size < 0:
            raise ValueError("File size must be zero or greater")
        self.__name = name
        self.__size = size  # in bytes
        self.__flags = self.__DIR if is_dir else 0
        self.__timestamp_created = time_created
        self.__timestamp_modified = time_modified
        # Most scan nodes are leaves.  Preserve the mutable public ``children``
        # list, but allocate it only when a caller actually needs it.
        self.__children: list[SystemFile] | None = None
        self.__path_pair_id: str | None = None
        self.__path_pair_name: str | None = None
        if is_staging:
            self.__flags |= self.__STAGING

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SystemFile):
            return NotImplemented
        return (
            self.__name == other.__name
            and self.__size == other.__size
            and self.is_dir == other.is_dir
            and self.__timestamp_created == other.__timestamp_created
            and self.__timestamp_modified == other.__timestamp_modified
            and (self.__children or []) == (other.__children or [])
            and self.__path_pair_id == other.__path_pair_id
            and self.__path_pair_name == other.__path_pair_name
            and self.is_staging == other.is_staging
            and self.status_sidecar_ready == other.status_sidecar_ready
        )

    def __repr__(self) -> str:
        return str(self.__diagnostic_attributes())

    def __diagnostic_attributes(self) -> dict[str, object]:
        """Keep the pre-slots diagnostic repr field view without a per-object dict."""
        return {
            "_SystemFile__name": self.__name,
            "_SystemFile__size": self.__size,
            "_SystemFile__is_dir": self.is_dir,
            "_SystemFile__timestamp_created": self.__timestamp_created,
            "_SystemFile__timestamp_modified": self.__timestamp_modified,
            "_SystemFile__children": self.__children or [],
            "_SystemFile__path_pair_id": self.__path_pair_id,
            "_SystemFile__path_pair_name": self.__path_pair_name,
            "_SystemFile__is_staging": self.is_staging,
            "_SystemFile__status_sidecar_ready": self.status_sidecar_ready,
        }

    @property
    def name(self) -> str: return self.__name

    @property
    def size(self) -> int: return self.__size

    @property
    def is_dir(self) -> bool: return bool(self.__flags & self.__DIR)

    @property
    def timestamp_created(self) -> datetime | None: return self.__timestamp_created

    @property
    def timestamp_modified(self) -> datetime | None: return self.__timestamp_modified

    @property
    def children(self) -> List["SystemFile"]:
        if self.__children is None:
            self.__children = []
        return self.__children

    def iter_children(self) -> Iterator["SystemFile"]:
        return iter(self.__children) if self.__children is not None else iter(())

    @property
    def path_pair_id(self) -> str | None: return self.__path_pair_id

    @path_pair_id.setter
    def path_pair_id(self, path_pair_id: str | None):
        if path_pair_id is not None and type(path_pair_id) != str:
            raise TypeError
        self.__path_pair_id = path_pair_id

    @property
    def path_pair_name(self) -> str | None: return self.__path_pair_name

    @path_pair_name.setter
    def path_pair_name(self, path_pair_name: str | None):
        if path_pair_name is not None and type(path_pair_name) != str:
            raise TypeError
        self.__path_pair_name = path_pair_name

    @property
    def is_staging(self) -> bool: return bool(self.__flags & self.__STAGING)

    @is_staging.setter
    def is_staging(self, is_staging: bool):
        if type(is_staging) != bool:
            raise TypeError
        self.__flags = self.__flags | self.__STAGING if is_staging else self.__flags & ~self.__STAGING

    @property
    def status_sidecar_ready(self) -> bool: return bool(self.__flags & self.__STATUS_READY)

    @status_sidecar_ready.setter
    def status_sidecar_ready(self, status_sidecar_ready: bool):
        if type(status_sidecar_ready) != bool:
            raise TypeError
        self.__flags = self.__flags | self.__STATUS_READY if status_sidecar_ready else self.__flags & ~self.__STATUS_READY

    def add_child(self, file: "SystemFile"):
        if not self.is_dir:
            raise TypeError("Cannot add children to a file")
        if self.__children is None:
            self.__children = []
        self.__children.append(file)

    def to_dict(self) -> SystemFileData:
        d: SystemFileData = {
            "name": self.__name,
            "size": self.__size,
            "is_dir": self.is_dir,
        }
        if self.__timestamp_created is not None:
            d["time_created"] = self.__timestamp_created.isoformat()
        if self.__timestamp_modified is not None:
            d["time_modified"] = self.__timestamp_modified.isoformat()
        if self.__path_pair_id is not None:
            d["path_pair_id"] = self.__path_pair_id
        if self.__path_pair_name is not None:
            d["path_pair_name"] = self.__path_pair_name
        if self.is_staging:
            d["is_staging"] = True
        if self.__children:
            d["children"] = [child.to_dict() for child in self.__children]
        return d

    @classmethod
    def from_dict(cls, data: SystemFileData) -> "SystemFile":
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
