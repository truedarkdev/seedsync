# Copyright 2017, Inderpreet Singh, All rights reserved.

"""Persist key construction and parsing helpers."""

from __future__ import annotations


# ASCII Unit Separator is a safe composite-key delimiter for file names.
KEY_SEP = "\x1f"


def persist_key(pair_id: str | None, name: str) -> str:
    """Build a namespaced persist key.

    The default pair keeps the bare file name. Path-pair-aware keys use the
    ASCII Unit Separator so the pair id and file name stay unambiguous.
    """
    return f"{pair_id}{KEY_SEP}{name}" if pair_id else name


def strip_persist_key(key: str, pair_id: str | None) -> str:
    """Strip a pair-specific prefix from a persist key.

    Both the current unit-separator encoding and the legacy colon separator
    remain accepted so previously persisted keys can still be read.
    """
    if not pair_id:
        return key
    for sep in (KEY_SEP, ":"):
        prefix = f"{pair_id}{sep}"
        if key.startswith(prefix):
            return key[len(prefix) :]
    return key
