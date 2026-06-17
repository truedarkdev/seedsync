# Copyright 2024, RapidCopy Contributors, All rights reserved.

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .error import AppError
from .persist import PersistError


class PathPairError(AppError):
    """
    Exception indicating a path pair error
    """
    pass


class PathPairConflictError(PathPairError):
    """
    Exception indicating a conflicting path pair name
    """
    pass


DOCKER_DOWNLOADS_BASE = "/downloads"
DOCKER_MOUNTS_BASE = "/mounts"


def is_running_in_docker() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as handle:
            return "docker" in handle.read()
    except OSError:
        return False


@dataclass
class PathPair:
    """
    Represents a single remote to local path mapping.
    """
    remote_path: str
    local_path: str
    name: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    enabled: bool = True
    auto_queue: bool = True

    def __post_init__(self):
        if not self.name:
            self.name = os.path.basename(self.remote_path.rstrip("/")) or "Default"

    def validate(self) -> List[str]:
        if type(self.remote_path) != str:
            raise PathPairError("Path pair '{}': remote_path must be a string".format(self.name))
        if not self.remote_path.strip():
            raise PathPairError("Path pair '{}': remote_path cannot be empty".format(self.name))
        if type(self.local_path) != str:
            raise PathPairError("Path pair '{}': local_path must be a string".format(self.name))
        if not self.local_path.strip():
            raise PathPairError("Path pair '{}': local_path cannot be empty".format(self.name))
        if type(self.id) != str:
            raise PathPairError("Path pair '{}': id must be a string".format(self.name))
        if not self.id:
            raise PathPairError("Path pair '{}': id cannot be empty".format(self.name))
        warnings = []
        if is_running_in_docker():
            local_path = os.path.normpath(self.local_path)
            downloads_base = os.path.normpath(DOCKER_DOWNLOADS_BASE)
            mounts_base = os.path.normpath(DOCKER_MOUNTS_BASE)
            is_under_downloads = local_path == downloads_base or local_path.startswith(downloads_base + os.sep)
            is_under_mounts = local_path == mounts_base or local_path.startswith(mounts_base + os.sep)
            if not is_under_downloads and not is_under_mounts:
                warnings.append(
                    "Path pair '{}': Local path '{}' is not under '{}' or '{}'. In Docker, local "
                    "paths should be subdirectories of '{}' for ordinary local storage or '{}' "
                    "for additional mounted or network-backed paths.".format(
                        self.name,
                        self.local_path,
                        DOCKER_DOWNLOADS_BASE,
                        DOCKER_MOUNTS_BASE,
                        DOCKER_DOWNLOADS_BASE,
                        DOCKER_MOUNTS_BASE,
                    )
                )
        return warnings


@dataclass
class PathPairCollection:
    """
    Collection of path pairs with light schema metadata.
    """
    path_pairs: List[PathPair] = field(default_factory=list)
    version: int = 1

    def get_enabled_pairs(self) -> List[PathPair]:
        return [pair for pair in self.path_pairs if pair.enabled]

    def get_pair_by_id(self, pair_id: str) -> Optional[PathPair]:
        for pair in self.path_pairs:
            if pair.id == pair_id:
                return pair
        return None

    def get_pair_by_name(self, name: str) -> Optional[PathPair]:
        for pair in self.path_pairs:
            if pair.name == name:
                return pair
        return None

    def add_pair(self, pair: PathPair):
        warnings = pair.validate()
        if self.get_pair_by_id(pair.id):
            raise PathPairError("Path pair with id '{}' already exists".format(pair.id))
        if self.get_pair_by_name(pair.name):
            raise PathPairConflictError("Path pair with name '{}' already exists".format(pair.name))
        self.path_pairs.append(pair)
        return warnings

    def update_pair(self, pair: PathPair):
        warnings = pair.validate()
        for index, existing in enumerate(self.path_pairs):
            if existing.id == pair.id:
                if existing.name != pair.name and self.get_pair_by_name(pair.name):
                    raise PathPairConflictError("Path pair with name '{}' already exists".format(pair.name))
                self.path_pairs[index] = pair
                return warnings
        raise PathPairError("Path pair with id '{}' not found".format(pair.id))

    def remove_pair(self, pair_id: str):
        for index, pair in enumerate(self.path_pairs):
            if pair.id == pair_id:
                del self.path_pairs[index]
                return
        raise PathPairError("Path pair with id '{}' not found".format(pair_id))

    def reorder_pairs(self, pair_ids: List[str]):
        existing_ids = [pair.id for pair in self.path_pairs]
        if sorted(pair_ids) != sorted(existing_ids):
            raise PathPairError("Reorder list must contain all existing path pair IDs")

        pairs_by_id = {pair.id: pair for pair in self.path_pairs}
        self.path_pairs = [pairs_by_id[pair_id] for pair_id in pair_ids]


class PathPairManager:
    """
    Loads and stores path-pair configuration separately from settings.cfg.
    """

    FILENAME = "path_pairs.json"

    def __init__(self, config_dir: str):
        self._config_dir = config_dir
        self._file_path = os.path.join(config_dir, self.FILENAME)
        self._collection = None

    @property
    def file_path(self) -> str:
        return self._file_path

    @property
    def collection(self) -> PathPairCollection:
        if self._collection is None:
            self.load()
        return self._collection

    def load(self) -> PathPairCollection:
        if not os.path.exists(self._file_path):
            self._collection = PathPairCollection()
            return self._collection

        try:
            with open(self._file_path, "r", encoding="utf-8") as handle:
                self._collection = self.from_str(handle.read())
        except (OSError, ValueError, PersistError):
            self.__backup_file()
            self._collection = PathPairCollection()
            return self._collection

        return self._collection

    def __backup_file(self):
        file_name = os.path.basename(self._file_path)
        file_dir = os.path.dirname(self._file_path)
        i = 1
        while True:
            backup_path = os.path.join(file_dir, "{}.{}.bak".format(file_name, i))
            if not os.path.exists(backup_path):
                break
            i += 1
        try:
            shutil.copy(self._file_path, backup_path)
        except OSError:
            pass

    def save(self):
        if self._collection is None:
            raise PathPairError("No path pair collection loaded")

        try:
            os.makedirs(self._config_dir, exist_ok=True)
            with open(self._file_path, "w", encoding="utf-8") as handle:
                handle.write(self.to_str())
        except OSError as exc:
            raise PersistError("Failed to save path pairs: {}".format(exc)) from exc

    def get_all_pairs(self) -> List[PathPair]:
        return self.collection.path_pairs

    def get_enabled_pairs(self) -> List[PathPair]:
        return self.collection.get_enabled_pairs()

    def get_pair_by_id(self, pair_id: str) -> Optional[PathPair]:
        return self.collection.get_pair_by_id(pair_id)

    def add_pair(self, pair: PathPair):
        warnings = self.collection.add_pair(pair)
        self.save()
        return warnings

    def update_pair(self, pair: PathPair):
        warnings = self.collection.update_pair(pair)
        self.save()
        return warnings

    def remove_pair(self, pair_id: str):
        self.collection.remove_pair(pair_id)
        self.save()

    def reorder_pairs(self, pair_ids: List[str]):
        self.collection.reorder_pairs(pair_ids)
        self.save()

    def from_str(self, content: str) -> PathPairCollection:
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise TypeError("top-level path pair data must be a JSON object")

            raw_path_pairs = data.get("path_pairs", [])
            if raw_path_pairs is None:
                raw_path_pairs = []
            if not isinstance(raw_path_pairs, list):
                raise TypeError("path_pairs must be a list")

            path_pairs = []
            for pair_data in raw_path_pairs:
                if not isinstance(pair_data, dict):
                    raise TypeError("path pair entries must be JSON objects")
                path_pairs.append(
                    PathPair(
                        id=pair_data.get("id", str(uuid.uuid4())),
                        name=pair_data.get("name", ""),
                        remote_path=pair_data["remote_path"],
                        local_path=pair_data["local_path"],
                        enabled=pair_data.get("enabled", True),
                        auto_queue=pair_data.get("auto_queue", True),
                    )
                )

            version = data.get("version", 1)
            if not isinstance(version, int):
                raise TypeError("version must be an integer")

            return PathPairCollection(
                path_pairs=path_pairs,
                version=version,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise PersistError("Invalid path pairs JSON: {}".format(exc)) from exc

    def to_str(self) -> str:
        if self._collection is None:
            raise PathPairError("No path pair collection loaded")
        return json.dumps(
            {
                "version": self._collection.version,
                "path_pairs": [asdict(pair) for pair in self._collection.path_pairs],
            },
            indent=2,
        )

    def migrate_from_config(self, remote_path: str, local_path: str) -> bool:
        if self.collection.path_pairs:
            return False
        if not remote_path or not local_path:
            return False
        if remote_path.startswith("<") or local_path.startswith("<"):
            return False

        self.collection.add_pair(
            PathPair(
                name="Default",
                remote_path=remote_path,
                local_path=local_path,
                enabled=True,
                auto_queue=True,
            )
        )
        self.save()
        return True
