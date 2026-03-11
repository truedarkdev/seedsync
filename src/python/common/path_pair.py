# Copyright 2024, RapidCopy Contributors, All rights reserved.

import json
import os
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

    def validate(self):
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

    def add_pair(self, pair: PathPair):
        pair.validate()
        if self.get_pair_by_id(pair.id):
            raise PathPairError("Path pair with id '{}' already exists".format(pair.id))
        self.path_pairs.append(pair)

    def update_pair(self, pair: PathPair):
        pair.validate()
        for index, existing in enumerate(self.path_pairs):
            if existing.id == pair.id:
                self.path_pairs[index] = pair
                return
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
        except (OSError, ValueError) as exc:
            raise PersistError("Failed to load path pairs: {}".format(exc)) from exc

        return self._collection

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
        self.collection.add_pair(pair)
        self.save()

    def update_pair(self, pair: PathPair):
        self.collection.update_pair(pair)
        self.save()

    def remove_pair(self, pair_id: str):
        self.collection.remove_pair(pair_id)
        self.save()

    def reorder_pairs(self, pair_ids: List[str]):
        self.collection.reorder_pairs(pair_ids)
        self.save()

    def from_str(self, content: str) -> PathPairCollection:
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise PersistError("Invalid JSON: {}".format(exc)) from exc

        path_pairs = []
        for pair_data in data.get("path_pairs", []):
            try:
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
            except KeyError as exc:
                raise PersistError("Missing required field in path pair: {}".format(exc)) from exc

        return PathPairCollection(
            path_pairs=path_pairs,
            version=data.get("version", 1),
        )

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
