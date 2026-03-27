# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
import os
from datetime import datetime
from typing import Optional

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
                                path_pair_id: Optional[str] = None) -> dict:
    marker = {
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
                                 path_pair_id: Optional[str] = None):
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


def read_managed_extract_marker(marker_path: str) -> Optional[dict]:
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            marker = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None

    if not isinstance(marker, dict):
        return None
    if marker.get("schema_version") != MANAGED_EXTRACT_MARKER_SCHEMA_VERSION:
        return None
    if not isinstance(marker.get("archive_name"), str) or not marker["archive_name"]:
        return None
    return marker


def resolve_managed_extract_file_id(marker: dict) -> Optional[str]:
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
