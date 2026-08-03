# Copyright 2024, RapidCopy Contributors, All rights reserved.

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from threading import RLock
from collections.abc import Callable
from typing import List, Optional, TypeVar, cast

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
_MutationResult = TypeVar("_MutationResult")


def legacy_default_path_pair_id(remote_path: str, local_path: str) -> str:
    """Stable owner for legacy bare controller history.

    Keep the v0.8.6 UUID5 namespace/name unchanged so a restart after the
    path-pair file is written can still identify the same Default pair.
    """
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL, "seedsync:v086:{}\n{}".format(remote_path, local_path)
    ))


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    return dict(pairs)


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

    def __post_init__(self) -> None:
        if type(self.name) != str:
            raise PathPairError("Path pair '{}': name must be a string".format(self.name))
        if not self.name and type(self.remote_path) == str:
            self.name = os.path.basename(self.remote_path.rstrip("/")) or "Default"

    def validate(self) -> List[str]:
        if type(self.name) != str:
            raise PathPairError("Path pair '{}': name must be a string".format(self.name))
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
        if type(self.enabled) != bool:
            raise PathPairError("Path pair '{}': enabled must be a boolean".format(self.name))
        if type(self.auto_queue) != bool:
            raise PathPairError("Path pair '{}': auto_queue must be a boolean".format(self.name))
        warnings: List[str] = []
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
    path_pairs: List[PathPair] = field(default_factory=lambda: list[PathPair]())
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

    def add_pair(self, pair: PathPair) -> List[str]:
        warnings = pair.validate()
        if self.get_pair_by_id(pair.id):
            raise PathPairError("Path pair with id '{}' already exists".format(pair.id))
        if self.get_pair_by_name(pair.name):
            raise PathPairConflictError("Path pair with name '{}' already exists".format(pair.name))
        self.path_pairs.append(pair)
        return warnings

    def update_pair(self, pair: PathPair) -> List[str]:
        warnings = pair.validate()
        for index, existing in enumerate(self.path_pairs):
            if existing.id == pair.id:
                if existing.name != pair.name and self.get_pair_by_name(pair.name):
                    raise PathPairConflictError("Path pair with name '{}' already exists".format(pair.name))
                self.path_pairs[index] = pair
                return warnings
        raise PathPairError("Path pair with id '{}' not found".format(pair.id))

    def remove_pair(self, pair_id: str) -> None:
        for index, pair in enumerate(self.path_pairs):
            if pair.id == pair_id:
                del self.path_pairs[index]
                return
        raise PathPairError("Path pair with id '{}' not found".format(pair_id))

    def reorder_pairs(self, pair_ids: List[str]) -> None:
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
        self._lock = RLock()

    @property
    def file_path(self) -> str:
        return self._file_path

    @property
    def collection(self) -> PathPairCollection:
        with self._lock:
            if self._collection is None:
                self.load()
            collection = self._collection
            if collection is None:
                raise RuntimeError("Path pair collection failed to load")
            return collection

    def load(self) -> PathPairCollection:
        with self._lock:
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

    def __backup_file(self) -> None:
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

    def save(self) -> None:
        with self._lock:
            if self._collection is None:
                raise PathPairError("No path pair collection loaded")

            temp_path = None
            try:
                os.makedirs(self._config_dir, exist_ok=True)
                descriptor, temp_path = tempfile.mkstemp(
                    dir=self._config_dir,
                    prefix=".{}-".format(self.FILENAME),
                    suffix=".tmp",
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(self.to_str())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self._file_path)
                temp_path = None
            except OSError as exc:
                raise PersistError("Failed to save path pairs: {}".format(exc)) from exc
            finally:
                if temp_path is not None:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

    def __mutate_and_save(
        self,
        mutation: Callable[[PathPairCollection], _MutationResult],
    ) -> _MutationResult:
        collection = self.collection
        original_pairs = list(collection.path_pairs)
        original_version = collection.version
        try:
            result = mutation(collection)
            self.save()
            return result
        except Exception:
            collection.path_pairs = original_pairs
            collection.version = original_version
            raise

    def get_all_pairs(self) -> List[PathPair]:
        with self._lock:
            return self.collection.path_pairs

    def get_enabled_pairs(self) -> List[PathPair]:
        with self._lock:
            return self.collection.get_enabled_pairs()

    def get_pair_by_id(self, pair_id: str) -> Optional[PathPair]:
        with self._lock:
            return self.collection.get_pair_by_id(pair_id)

    def add_pair(self, pair: PathPair) -> List[str]:
        with self._lock:
            return self.__mutate_and_save(lambda collection: collection.add_pair(pair))

    def update_pair(self, pair: PathPair) -> List[str]:
        with self._lock:
            return self.__mutate_and_save(lambda collection: collection.update_pair(pair))

    def remove_pair(self, pair_id: str) -> None:
        with self._lock:
            self.__mutate_and_save(lambda collection: collection.remove_pair(pair_id))

    def reorder_pairs(self, pair_ids: List[str]) -> None:
        with self._lock:
            self.__mutate_and_save(lambda collection: collection.reorder_pairs(pair_ids))

    def from_str(self, content: str) -> PathPairCollection:
        try:
            decoded_value: object = json.loads(content, object_pairs_hook=_json_object)
            if not isinstance(decoded_value, dict):
                raise TypeError("top-level path pair data must be a JSON object")
            decoded = cast(dict[str, object], decoded_value)
            raw_path_pairs: object = decoded.get("path_pairs", [])
            if raw_path_pairs is None:
                raw_path_pairs = []
            if not isinstance(raw_path_pairs, list):
                raise TypeError("path_pairs must be a list")

            path_pairs: List[PathPair] = []
            decoded_pairs = cast(List[object], raw_path_pairs)
            for decoded_pair in decoded_pairs:
                if not isinstance(decoded_pair, dict):
                    raise TypeError("path pair entries must be JSON objects")
                pair_data = cast(dict[str, object], decoded_pair)
                pair_id: object = pair_data.get("id", str(uuid.uuid4()))
                name: object = pair_data.get("name", "")
                remote_path: object = pair_data.get("remote_path")
                local_path: object = pair_data.get("local_path")
                enabled: object = pair_data.get("enabled", True)
                auto_queue: object = pair_data.get("auto_queue", True)
                if not isinstance(pair_id, str) or not isinstance(name, str):
                    raise TypeError("path pair id and name must be strings")
                if not isinstance(remote_path, str) or not isinstance(local_path, str):
                    raise TypeError("path pair paths must be strings")
                if not isinstance(enabled, bool) or not isinstance(auto_queue, bool):
                    raise TypeError("path pair flags must be booleans")
                pair = PathPair(
                    id=pair_id,
                    name=name,
                    remote_path=remote_path,
                    local_path=local_path,
                    enabled=enabled,
                    auto_queue=auto_queue,
                )
                pair.validate()
                path_pairs.append(pair)

            version: object = decoded.get("version", 1)
            if not isinstance(version, int):
                raise TypeError("version must be an integer")

            return PathPairCollection(
                path_pairs=path_pairs,
                version=version,
            )
        except (PathPairError, ValueError, TypeError, KeyError) as exc:
            raise PersistError("Invalid path pairs JSON: {}".format(exc)) from exc

    def to_str(self) -> str:
        with self._lock:
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
        with self._lock:
            # Legacy paths are imported only for installations that have never
            # persisted a path-pair collection.  An existing empty collection
            # is intentional (for example, after deleting the final pair) and
            # must remain empty across restarts.
            if self.collection.path_pairs or os.path.lexists(self._file_path):
                return False
            if not remote_path or not local_path:
                return False
            if remote_path.startswith("<") or local_path.startswith("<"):
                return False

            pair = PathPair(
                id=legacy_default_path_pair_id(remote_path, local_path),
                name="Default",
                remote_path=remote_path,
                local_path=local_path,
                enabled=True,
                auto_queue=True,
            )
            self.__mutate_and_save(lambda collection: collection.add_pair(pair))
            return True
