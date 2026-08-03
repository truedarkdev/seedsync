# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import uuid
from contextlib import contextmanager
from threading import RLock
from typing import Iterator, cast

from common import Constants, Persist, PersistError, overrides
from model import ModelFile

from .persist_keys import KEY_SEP


class ControllerPersist(Persist):
    """Persisted controller state, with a one-time canonical ID boundary."""

    __KEY_DOWNLOADED_FILE_NAMES = "downloaded"
    __KEY_EXTRACTED_FILE_NAMES = "extracted"
    __KEY_STOPPED_FILE_NAMES = "stopped"
    __KEY_MOVE_FAILURE_COUNTS = "move_failure_counts"
    __KEY_FINAL_MOVE_SUCCEEDED = "final_move_succeeded"
    __KEY_MARKER_IDENTITY_MIGRATION = "marker_identity_migration"
    __MARKER_IDENTITY_PHASE_ONE = 1
    __MARKER_IDENTITY_PHASE_TWO = 2

    def __init__(self):
        self.__lock = RLock()
        self.downloaded_file_names: set[str] = set()
        self.extracted_file_names: set[str] = set()
        self.stopped_file_names: set[str] = set()
        self.move_failure_counts: dict[str, int] = {}
        self.final_move_succeeded_file_names: set[str] = set()
        self.__marker_identity_migration = 0

    @contextmanager
    def state_transaction(self) -> Iterator[None]:
        with self.__lock:
            yield

    @staticmethod
    def _read_string_set(value: object, field_name: str) -> set[str]:
        if not isinstance(value, list):
            raise TypeError("{} must be an array of strings".format(field_name))
        items = cast(list[object], value)
        if not all(isinstance(item, str) for item in items):
            raise TypeError("{} must be an array of strings".format(field_name))
        return set(cast(list[str], items))

    @staticmethod
    def _canonical_file_id(key: str, default_path_pair_id: str | None) -> str | None:
        """Convert legacy keys only at this startup persistence boundary."""
        try:
            parsed = json.loads(key)
        except (TypeError, ValueError):
            parsed = None
        if (
            isinstance(parsed, list)
            and len(parsed) == 2
            and isinstance(parsed[0], str)
            and isinstance(parsed[1], str)
            and key == ModelFile.build_file_id(parsed[1], parsed[0])
        ):
            return key
        if KEY_SEP in key:
            path_pair_id, name = key.split(KEY_SEP, 1)
            return ModelFile.build_file_id(name, path_pair_id) if path_pair_id else None
        # Only the UUID-colon form was a legacy scope encoding. A regular
        # filename containing a colon must remain unambiguous input, not scope.
        if len(key) > 37 and key[36] == ":":
            try:
                uuid.UUID(key[:36])
            except ValueError:
                pass
            else:
                return ModelFile.build_file_id(key[37:], key[:36])
        # Bare history is unsafe after path pairs exist. It can bind only to
        # the deterministic v0.8.6/default-config Default supplied by caller.
        if default_path_pair_id is not None:
            return ModelFile.build_file_id(key, default_path_pair_id)
        return None

    @classmethod
    def _canonicalize_set(cls, keys: set[str], default_path_pair_id: str | None) -> set[str]:
        return {
            canonical for key in keys
            if (canonical := cls._canonical_file_id(key, default_path_pair_id)) is not None
        }

    @classmethod
    def _canonicalize_counts(
            cls, counts: dict[str, int], default_path_pair_id: str | None) -> dict[str, int]:
        canonical: dict[str, int] = {}
        for key, count in counts.items():
            normalized = cls._canonical_file_id(key, default_path_pair_id)
            if normalized is not None:
                canonical[normalized] = max(canonical.get(normalized, 0), count)
        return canonical

    def canonicalize_file_identities(self, default_path_pair_id: str | None = None) -> bool:
        """Perform one phase of the restart-safe canonical marker migration.

        A true return requires an immediate ``to_file`` call. ``Persist`` uses
        an atomic replace, so phase one (legacy plus canonical) survives an
        interruption and phase two can safely leave canonical-only data.
        """
        with self.state_transaction():
            if self.__marker_identity_migration >= self.__MARKER_IDENTITY_PHASE_TWO:
                return False
            canonical_sets = tuple(
                self._canonicalize_set(values, default_path_pair_id)
                for values in (
                    self.downloaded_file_names,
                    self.extracted_file_names,
                    self.stopped_file_names,
                    self.final_move_succeeded_file_names,
                )
            )
            canonical_counts = self._canonicalize_counts(self.move_failure_counts, default_path_pair_id)
            if self.__marker_identity_migration == 0:
                self.downloaded_file_names.update(canonical_sets[0])
                self.extracted_file_names.update(canonical_sets[1])
                self.stopped_file_names.update(canonical_sets[2])
                self.final_move_succeeded_file_names.update(canonical_sets[3])
                for key, count in canonical_counts.items():
                    self.move_failure_counts[key] = max(self.move_failure_counts.get(key, 0), count)
                self.__marker_identity_migration = self.__MARKER_IDENTITY_PHASE_ONE
                return True
            self.downloaded_file_names = canonical_sets[0]
            self.extracted_file_names = canonical_sets[1]
            self.stopped_file_names = canonical_sets[2]
            self.final_move_succeeded_file_names = canonical_sets[3]
            self.move_failure_counts = canonical_counts
            self.__marker_identity_migration = self.__MARKER_IDENTITY_PHASE_TWO
            return True

    @classmethod
    @overrides(Persist)
    def from_str(cls: type["ControllerPersist"], content: str) -> "ControllerPersist":
        persist = cls()
        try:
            raw_persist = json.loads(content)
            if not isinstance(raw_persist, dict):
                raise TypeError("controller persist must be an object")
            dct = cast(dict[str, object], raw_persist)
            persist.downloaded_file_names = cls._read_string_set(dct[cls.__KEY_DOWNLOADED_FILE_NAMES], "downloaded")
            persist.extracted_file_names = cls._read_string_set(dct[cls.__KEY_EXTRACTED_FILE_NAMES], "extracted")
            persist.stopped_file_names = cls._read_string_set(dct.get(cls.__KEY_STOPPED_FILE_NAMES, []), "stopped")
            raw_counts = dct.get(cls.__KEY_MOVE_FAILURE_COUNTS, {})
            if not isinstance(raw_counts, dict):
                raise TypeError("move_failure_counts must be an object")
            persist.move_failure_counts = {
                key: count for key, count in cast(dict[object, object], raw_counts).items()
                if isinstance(key, str) and key and type(count) is int and 0 <= count <= 4
            }
            persist.final_move_succeeded_file_names = cls._read_string_set(
                dct.get(cls.__KEY_FINAL_MOVE_SUCCEEDED, []), "final_move_succeeded"
            )
            phase = dct.get(cls.__KEY_MARKER_IDENTITY_MIGRATION, 0)
            if type(phase) is not int or phase not in (0, 1, 2):
                raise TypeError("marker_identity_migration must be 0, 1, or 2")
            persist.__marker_identity_migration = phase
            return persist
        except (ValueError, TypeError, KeyError) as e:
            raise PersistError("Error parsing ControllerPersist - {}: {}".format(type(e).__name__, str(e)))

    @overrides(Persist)
    def to_str(self) -> str:
        with self.state_transaction():
            dct: dict[str, object] = {
                self.__KEY_DOWNLOADED_FILE_NAMES: list(self.downloaded_file_names),
                self.__KEY_EXTRACTED_FILE_NAMES: list(self.extracted_file_names),
                self.__KEY_STOPPED_FILE_NAMES: list(self.stopped_file_names),
                self.__KEY_MOVE_FAILURE_COUNTS: dict(self.move_failure_counts),
                self.__KEY_FINAL_MOVE_SUCCEEDED: list(self.final_move_succeeded_file_names),
                self.__KEY_MARKER_IDENTITY_MIGRATION: self.__marker_identity_migration,
            }
        return json.dumps(dct, indent=Constants.JSON_PRETTY_PRINT_INDENT)
