# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import re
from contextlib import contextmanager
from threading import RLock
from typing import cast, Iterator

from common import overrides, Constants, Persist, PersistError

from .persist_keys import KEY_SEP, persist_key, strip_persist_key


# Matches a UUID-style pair_id followed by the legacy ':' separator.
# Used to migrate old persist keys from 'pair_id:name' to 'pair_id\x1fname'.
_LEGACY_KEY_RE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):(.*)",
    re.IGNORECASE,
)


class ControllerPersist(Persist):
    """
    Persisting state for controller
    """

    # Keys
    __KEY_DOWNLOADED_FILE_NAMES = "downloaded"
    __KEY_EXTRACTED_FILE_NAMES = "extracted"
    __KEY_STOPPED_FILE_NAMES = "stopped"
    __KEY_MOVE_FAILURE_COUNTS = "move_failure_counts"
    __KEY_FINAL_MOVE_SUCCEEDED = "final_move_succeeded"

    def __init__(self):
        self.__lock = RLock()
        self.downloaded_file_names: set[str] = set()
        self.extracted_file_names: set[str] = set()
        self.stopped_file_names: set[str] = set()
        self.move_failure_counts: dict[str, int] = {}
        self.final_move_succeeded_file_names: set[str] = set()

    @contextmanager
    def state_transaction(self) -> Iterator[None]:
        """Guard a complete mutation transaction against persistence snapshots."""
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
    def _migrate_legacy_keys(keys: set[str]) -> set[str]:
        """Replace legacy 'pair_id:name' keys with 'pair_id\\x1fname' keys."""
        migrated: set[str] = set()
        for key in keys:
            if KEY_SEP in key:
                migrated.add(key)
                continue
            m = _LEGACY_KEY_RE.match(key)
            if m:
                pair_id = m.group(1)
                migrated.add(persist_key(pair_id, strip_persist_key(key, pair_id)))
            else:
                migrated.add(key)
        return migrated

    @classmethod
    @overrides(Persist)
    def from_str(cls: type["ControllerPersist"], content: str) -> "ControllerPersist":
        persist = cls()
        try:
            raw_persist = json.loads(content)
            if not isinstance(raw_persist, dict):
                raise TypeError("controller persist must be an object")
            dct = cast(dict[str, object], raw_persist)
            persist.downloaded_file_names = cls._read_string_set(
                dct[ControllerPersist.__KEY_DOWNLOADED_FILE_NAMES], "downloaded"
            )
            persist.extracted_file_names = cls._read_string_set(
                dct[ControllerPersist.__KEY_EXTRACTED_FILE_NAMES], "extracted"
            )
            persist.stopped_file_names = cls._read_string_set(
                dct.get(ControllerPersist.__KEY_STOPPED_FILE_NAMES, []), "stopped"
            )
            raw_move_failure_counts = dct.get(ControllerPersist.__KEY_MOVE_FAILURE_COUNTS, {})
            if not isinstance(raw_move_failure_counts, dict):
                raise TypeError("move_failure_counts must be an object")
            move_failure_items = cast(dict[object, object], raw_move_failure_counts)
            persist.move_failure_counts = {
                file_id: count
                for file_id, count in move_failure_items.items()
                if isinstance(file_id, str)
                and file_id != ""
                and type(count) is int
                and 0 <= count <= 4
            }
            raw_final_move_succeeded = dct.get(ControllerPersist.__KEY_FINAL_MOVE_SUCCEEDED, [])
            if not isinstance(raw_final_move_succeeded, list):
                raise TypeError("final_move_succeeded must be an array")
            final_move_items = cast(list[object], raw_final_move_succeeded)
            persist.final_move_succeeded_file_names = {
                file_id for file_id in final_move_items
                if isinstance(file_id, str) and file_id != ""
            }
            persist.downloaded_file_names = ControllerPersist._migrate_legacy_keys(persist.downloaded_file_names)
            persist.extracted_file_names = ControllerPersist._migrate_legacy_keys(persist.extracted_file_names)
            persist.stopped_file_names = ControllerPersist._migrate_legacy_keys(persist.stopped_file_names)
            persist.final_move_succeeded_file_names = ControllerPersist._migrate_legacy_keys(
                persist.final_move_succeeded_file_names
            )
            return persist
        except (ValueError, TypeError, KeyError) as e:
            raise PersistError("Error parsing ControllerPersist - {}: {}".format(
                type(e).__name__, str(e))
            )

    @overrides(Persist)
    def to_str(self) -> str:
        with self.state_transaction():
            dct: dict[str, object] = {}
            dct[ControllerPersist.__KEY_DOWNLOADED_FILE_NAMES] = list(self.downloaded_file_names)
            dct[ControllerPersist.__KEY_EXTRACTED_FILE_NAMES] = list(self.extracted_file_names)
            dct[ControllerPersist.__KEY_STOPPED_FILE_NAMES] = list(self.stopped_file_names)
            dct[ControllerPersist.__KEY_MOVE_FAILURE_COUNTS] = dict(self.move_failure_counts)
            dct[ControllerPersist.__KEY_FINAL_MOVE_SUCCEEDED] = list(self.final_move_succeeded_file_names)
        return json.dumps(dct, indent=Constants.JSON_PRETTY_PRINT_INDENT)
