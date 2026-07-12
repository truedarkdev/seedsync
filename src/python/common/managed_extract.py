# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
import os
from collections.abc import Mapping
from datetime import datetime
from typing import NotRequired, Optional, TypedDict, cast

from model import ModelFile


MANAGED_EXTRACT_MARKER_FILE_NAME = ".seedsync-extract.json"
MANAGED_EXTRACT_MARKER_SCHEMA_VERSION = 1
_ARCHIVE_FOLDER_SUFFIXES = {
    ".7z",
    ".bz2",
    ".gz",
    ".lz",
    ".rar",
    ".tar",
    ".tbz2",
    ".tgz",
    ".zip",
    ".zipx",
}


class ManagedExtractMarker(TypedDict):
    schema_version: int
    archive_name: str
    extracted_at: NotRequired[str]
    archive_file_id: NotRequired[str]
    path_pair_id: NotRequired[str]


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    return dict(pairs)


def is_managed_extract_marker_name(file_name: str) -> bool:
    return file_name == MANAGED_EXTRACT_MARKER_FILE_NAME


def build_managed_extract_folder_name(archive_name: str) -> str:
    folder_name = os.path.basename(archive_name.rstrip(os.sep))
    while True:
        root, suffix = os.path.splitext(folder_name)
        if not root or suffix.lower() not in _ARCHIVE_FOLDER_SUFFIXES:
            return folder_name
        folder_name = root


def build_managed_extract_folder_path(out_dir_path: str, archive_name: str) -> str:
    return os.path.join(out_dir_path, build_managed_extract_folder_name(archive_name))


def build_managed_extract_marker(archive_name: str,
                                archive_file_id: Optional[str] = None,
                                path_pair_id: Optional[str] = None) -> ManagedExtractMarker:
    marker: ManagedExtractMarker = {
        "schema_version": MANAGED_EXTRACT_MARKER_SCHEMA_VERSION,
        "archive_name": archive_name,
        "extracted_at": datetime.now().isoformat(),
    }
    if archive_file_id is not None:
        marker["archive_file_id"] = archive_file_id
    if path_pair_id is not None:
        marker["path_pair_id"] = path_pair_id
    return marker


def write_managed_extract_marker(out_dir_path: str,
                                 archive_name: str,
                                 archive_file_id: Optional[str] = None,
                                 path_pair_id: Optional[str] = None) -> None:
    os.makedirs(out_dir_path, exist_ok=True)
    marker_path = os.path.join(out_dir_path, MANAGED_EXTRACT_MARKER_FILE_NAME)
    with open(marker_path, "w", encoding="utf-8") as handle:
        json.dump(
            build_managed_extract_marker(
                archive_name=archive_name,
                archive_file_id=archive_file_id,
                path_pair_id=path_pair_id
            ),
            handle,
            indent=2,
            sort_keys=True
        )
        handle.write("\n")


def read_managed_extract_marker(marker_path: str) -> Optional[ManagedExtractMarker]:
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            marker_value: object = json.load(handle, object_pairs_hook=_json_object)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None

    if not isinstance(marker_value, dict):
        return None
    marker = cast(dict[str, object], marker_value)
    schema_version: object = marker.get("schema_version")
    archive_name: object = marker.get("archive_name")
    if schema_version != MANAGED_EXTRACT_MARKER_SCHEMA_VERSION:
        return None
    if not isinstance(archive_name, str) or not archive_name:
        return None
    extracted_at: object = marker.get("extracted_at")
    archive_file_id: object = marker.get("archive_file_id")
    path_pair_id: object = marker.get("path_pair_id")
    if extracted_at is not None and not isinstance(extracted_at, str):
        return None
    if archive_file_id is not None and not isinstance(archive_file_id, str):
        return None
    if path_pair_id is not None and not isinstance(path_pair_id, str):
        return None
    result: ManagedExtractMarker = {
        "schema_version": MANAGED_EXTRACT_MARKER_SCHEMA_VERSION,
        "archive_name": archive_name,
    }
    if extracted_at is not None:
        result["extracted_at"] = extracted_at
    if archive_file_id is not None:
        result["archive_file_id"] = archive_file_id
    if path_pair_id is not None:
        result["path_pair_id"] = path_pair_id
    return result


def resolve_managed_extract_file_id(marker: Mapping[str, object]) -> Optional[str]:
    archive_name = marker.get("archive_name")
    path_pair_id = marker.get("path_pair_id")
    if not isinstance(archive_name, str) or not archive_name:
        return None
    if path_pair_id is not None and not isinstance(path_pair_id, str):
        return None

    expected_file_id = ModelFile.build_file_id(archive_name, path_pair_id)
    archive_file_id = marker.get("archive_file_id")
    if archive_file_id is None or archive_file_id == "":
        return expected_file_id
    if not isinstance(archive_file_id, str):
        return None
    if archive_file_id != expected_file_id:
        return None
    return archive_file_id
