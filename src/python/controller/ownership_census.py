# Copyright 2026, SeedSync Contributors, All rights reserved.

"""Bounded, privacy-safe shallow ownership census helpers."""

from __future__ import annotations

import sys
import gc
from array import array
from collections import deque
from dataclasses import is_dataclass, fields
from datetime import date, datetime
from enum import Enum
from typing import Iterable, NamedTuple

from model import ModelFile
from system import SystemFile


class OwnershipRoot(NamedTuple):
    label: str
    values: tuple[object, ...]
    shallow_container_id: int | None = None
    shallow_container_bytes: int = 0
    aliases_live_model: bool | None = None


class _CompactIdSet:
    """Exact object-id set backed by a raw contiguous integer array.

    A normal ``set[int]`` needs a Python integer and hash-table entry for every
    visited object.  A large census can therefore retain tens of MiB in
    pymalloc arenas after the diagnostic request.  Object ids are non-zero
    pointer-sized integers, so open addressing can keep the same exact cycle
    protection in a compact buffer that the platform allocator can release.
    """

    __slots__ = ("__slots", "__size")

    def __init__(self) -> None:
        self.__slots = array("Q", [0]) * 1024
        self.__size = 0

    def __len__(self) -> int:
        return self.__size

    @property
    def storage_bytes(self) -> int:
        return len(self.__slots) * self.__slots.itemsize

    def release(self) -> None:
        """Drop the large traversal table before the response leaves scope."""
        self.__slots = array("Q", [0]) * 1024
        self.__size = 0

    def __contains__(self, value: int) -> bool:
        slots = self.__slots
        mask = len(slots) - 1
        index = self.__index(value, mask)
        while True:
            current = slots[index]
            if current == 0:
                return False
            if current == value:
                return True
            index = (index + 1) & mask

    def add_if_absent(self, value: int) -> bool:
        if self.__size * 10 >= len(self.__slots) * 7:
            self.__resize(len(self.__slots) * 2)
        slots = self.__slots
        mask = len(slots) - 1
        index = self.__index(value, mask)
        while True:
            current = slots[index]
            if current == 0:
                slots[index] = value
                self.__size += 1
                return True
            if current == value:
                return False
            index = (index + 1) & mask

    @staticmethod
    def __index(value: int, mask: int) -> int:
        # CPython object addresses are aligned; discard the always-zero low
        # bits before applying a 64-bit multiplicative mix.
        return (((value >> 4) * 11400714819323198485) & 0xFFFFFFFFFFFFFFFF) & mask

    def __resize(self, capacity: int) -> None:
        previous = self.__slots
        self.__slots = array("Q", [0]) * capacity
        self.__size = 0
        for value in previous:
            if value:
                self.add_if_absent(value)


def snapshot_container(label: str, value: object) -> OwnershipRoot:
    """Capture a container's immediate references while the caller holds its lock."""
    if isinstance(value, dict):
        return OwnershipRoot(label, tuple(item for pair in value.items() for item in pair), id(value), _size_of(value))
    if isinstance(value, (list, tuple, set, frozenset, deque)):
        return OwnershipRoot(label, tuple(value), id(value), _size_of(value))
    return OwnershipRoot(label, ())


def build_ownership_census(roots: Iterable[OwnershipRoot], max_objects: int = 2_000_000) -> dict[str, object]:
    """Traverse only explicitly allowed values and return fixed, numeric aggregates."""
    seen = _CompactIdSet()
    owners: dict[str, dict[str, int | bool]] = {}
    truncated = False

    def add(value: object, owner: dict[str, int | bool]) -> bool:
        nonlocal truncated
        value_id = id(value)
        if value_id in seen:
            return False
        if len(seen) >= max_objects:
            truncated = True
            return False
        seen.add_if_absent(value_id)
        owner["object_count"] += 1
        owner["shallow_bytes"] += _size_of(value)
        return True

    def walk(value: object, owner: dict[str, int | bool]) -> None:
        if not add(value, owner):
            return
        if isinstance(value, (ModelFile, SystemFile)):
            for child in _iter_slot_values(value):
                walk(child, owner)
        elif isinstance(value, dict):
            try:
                for key, child in value.items():
                    walk(key, owner)
                    walk(child, owner)
            except RuntimeError:
                return
        elif isinstance(value, (list, tuple, set, frozenset, deque)):
            try:
                for child in value:
                    walk(child, owner)
            except RuntimeError:
                return
        elif is_dataclass(value) and not isinstance(value, type):
            for field in fields(value):
                try:
                    walk(getattr(value, field.name), owner)
                except (AttributeError, RuntimeError):
                    continue
        elif isinstance(value, (str, bytes, bytearray, int, float, bool, type(None), datetime, date, Enum)):
            return

    for root in roots:
        owner: dict[str, int | bool] = {"object_count": 0, "shallow_bytes": 0}
        if root.aliases_live_model is not None:
            owner["aliases_live_model"] = root.aliases_live_model
        owners[root.label] = owner
        if root.shallow_container_id is not None:
            if root.shallow_container_id not in seen:
                if len(seen) >= max_objects:
                    truncated = True
                    break
                seen.add_if_absent(root.shallow_container_id)
                owner["object_count"] += 1
                owner["shallow_bytes"] += root.shallow_container_bytes
        for value in root.values:
            walk(value, owner)

    total_bytes = sum(int(owner["shallow_bytes"]) for owner in owners.values())
    result = {
        "schema": "seedsync.memory-ownership-census.v1",
        "enabled": True,
        "truncated": truncated,
        "visited_object_count": len(seen),
        "total_shallow_bytes": total_bytes,
        "owners": owners,
    }
    seen.release()
    return result


def disabled_ownership_census() -> dict[str, object]:
    return {
        "schema": "seedsync.memory-ownership-census.v1",
        "enabled": False,
        "truncated": False,
        "visited_object_count": 0,
        "total_shallow_bytes": 0,
        "owners": {},
    }


def unavailable_ownership_census() -> dict[str, object]:
    return {
        "schema": "seedsync.memory-ownership-census.v1",
        "enabled": True,
        "available": False,
        "truncated": False,
        "visited_object_count": 0,
        "total_shallow_bytes": 0,
        "owners": {},
    }


def _size_of(value: object) -> int:
    try:
        return sys.getsizeof(value)
    except (TypeError, ValueError):
        return 0


def release_ownership_census_working_memory() -> None:
    """Best-effort release of large, diagnostic-only traversal buffers."""
    try:
        gc.collect()
        if not sys.platform.startswith("linux"):
            return
        import ctypes

        libc = ctypes.CDLL(None)
        malloc_trim = getattr(libc, "malloc_trim", None)
        if malloc_trim is None:
            return
        malloc_trim.argtypes = [ctypes.c_size_t]
        malloc_trim.restype = ctypes.c_int
        malloc_trim(0)
    except Exception:
        # Diagnostics must never affect normal controller behavior.
        return


def _iter_slot_values(value: ModelFile | SystemFile):
    slots = getattr(type(value), "__slots__", ())
    for slot in slots:
        if slot == "__weakref__" or isinstance(value, ModelFile) and slot == "__parent":
            continue
        attribute = "_{}{}".format(type(value).__name__, slot) if slot.startswith("__") else slot
        try:
            yield getattr(value, attribute)
        except AttributeError:
            continue
