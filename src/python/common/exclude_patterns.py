# Copyright 2017, Inderpreet Singh, All rights reserved.

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple
import fnmatch

from system import SystemFile


ExcludePattern = Tuple[str, bool]


@dataclass(frozen=True)
class ExactPathExclusion:
    """A verified source-root-relative path, not a user glob pattern."""
    relative_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, str):
            raise TypeError("relative_path must be a string")
        if not self.relative_path or self.relative_path.startswith("/") or any(
                part in ("", ".", "..") for part in self.relative_path.split("/")):
            raise ValueError("relative_path must be contained and root-relative")
        if any(ord(character) < 32 or ord(character) == 127 for character in self.relative_path):
            raise ValueError("relative_path cannot contain control characters")


def partition_transfer_exclusions(
        exclude_patterns: str | Iterable[str | ExactPathExclusion | None] | None,
) -> tuple[List[str], List[ExactPathExclusion]]:
    """Keep configured globs and exact recovery exclusions distinct."""
    if isinstance(exclude_patterns, str) or exclude_patterns is None:
        return parse_exclude_patterns(exclude_patterns), []
    user_patterns: List[str] = []
    exact_paths: List[ExactPathExclusion] = []
    for pattern in exclude_patterns:
        if isinstance(pattern, ExactPathExclusion):
            if pattern not in exact_paths:
                exact_paths.append(pattern)
        else:
            user_patterns.append(pattern)
    return parse_exclude_patterns(user_patterns), exact_paths


def parse_exclude_patterns(exclude_patterns: str | Iterable[str | None] | None) -> List[str]:
    if exclude_patterns is None:
        return []

    if isinstance(exclude_patterns, str):
        raw_patterns = exclude_patterns.split(",")
    else:
        raw_patterns = exclude_patterns

    parsed_patterns: List[str] = []
    seen_patterns: set[str] = set()
    for pattern in raw_patterns:
        if pattern is None:
            continue
        normalized = pattern.strip()
        if not normalized or normalized in seen_patterns:
            continue
        seen_patterns.add(normalized)
        parsed_patterns.append(normalized)
    return parsed_patterns


def compile_exclude_patterns(exclude_patterns: str | Iterable[str | None] | None) -> List[ExcludePattern]:
    return [
        (pattern.rstrip("/"), pattern.endswith("/"))
        for pattern in parse_exclude_patterns(exclude_patterns)
    ]


def _matches_exclude(relative_path: str, is_dir: bool, patterns: Sequence[ExcludePattern]) -> bool:
    return any(
        fnmatch.fnmatchcase(relative_path, pattern) and (not dir_only or is_dir)
        for pattern, dir_only in patterns
    )


def _clone_system_file(system_file: SystemFile, children: Sequence[SystemFile] | None = None) -> SystemFile:
    cloned = SystemFile(
        system_file.name,
        sum(child.size for child in children) if children is not None else system_file.size,
        system_file.is_dir,
        time_created=system_file.timestamp_created,
        time_modified=system_file.timestamp_modified,
        mtime_ns=system_file.mtime_ns,
        is_staging=system_file.is_staging,
    )
    cloned.path_pair_id = system_file.path_pair_id
    cloned.path_pair_name = system_file.path_pair_name
    cloned.status_sidecar_ready = system_file.status_sidecar_ready
    cloned.has_staging_collision = system_file.has_staging_collision
    for child in children if children is not None else system_file.iter_children():
        cloned.add_child(child)
    return cloned


def _child_relative_path(parent_relative_path: str | None, child_name: str) -> str:
    if not parent_relative_path:
        return child_name
    return f"{parent_relative_path}/{child_name}"


def _filter_excluded_tree(
    system_file: SystemFile,
    relative_path: str | None,
    patterns: Sequence[ExcludePattern],
) -> SystemFile | None:
    if relative_path is not None and _matches_exclude(relative_path, system_file.is_dir, patterns):
        return None

    if not system_file.is_dir:
        return _clone_system_file(system_file)

    filtered_children: List[SystemFile] = []
    for child in system_file.iter_children():
        filtered_child = _filter_excluded_tree(
            child,
            _child_relative_path(relative_path, child.name),
            patterns,
        )
        if filtered_child is not None:
            filtered_children.append(filtered_child)
    return _clone_system_file(system_file, filtered_children)


def filter_excluded_files(
    files: Sequence[SystemFile] | None,
    exclude_patterns: str | Iterable[str | None] | None,
) -> List[SystemFile]:
    if files is None:
        return []

    patterns = compile_exclude_patterns(exclude_patterns)
    if not patterns:
        return list(files)

    filtered_files: List[SystemFile] = []
    for system_file in files:
        if _matches_exclude(system_file.name, system_file.is_dir, patterns):
            continue
        if system_file.is_dir:
            filtered_file = _filter_excluded_tree(system_file, None, patterns)
        else:
            filtered_file = _clone_system_file(system_file)
        if filtered_file is not None:
            filtered_files.append(filtered_file)
    return filtered_files
