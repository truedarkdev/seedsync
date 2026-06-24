# Copyright 2017, Inderpreet Singh, All rights reserved.

from typing import Iterable, List, Sequence, Tuple
import fnmatch

from system import SystemFile


ExcludePattern = Tuple[str, bool]


def parse_exclude_patterns(exclude_patterns: str | Iterable[str] | None) -> List[str]:
    if exclude_patterns is None:
        return []

    if isinstance(exclude_patterns, str):
        raw_patterns = exclude_patterns.split(",")
    else:
        raw_patterns = exclude_patterns

    parsed_patterns = []
    seen_patterns = set()
    for pattern in raw_patterns:
        if pattern is None:
            continue
        normalized = pattern.strip()
        if not normalized or normalized in seen_patterns:
            continue
        seen_patterns.add(normalized)
        parsed_patterns.append(normalized)
    return parsed_patterns


def compile_exclude_patterns(exclude_patterns: str | Iterable[str] | None) -> List[ExcludePattern]:
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
        is_staging=system_file.is_staging,
    )
    cloned.path_pair_id = system_file.path_pair_id
    cloned.path_pair_name = system_file.path_pair_name
    cloned.status_sidecar_ready = system_file.status_sidecar_ready
    for child in children if children is not None else system_file.children:
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

    filtered_children = []
    for child in system_file.children:
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
    exclude_patterns: str | Iterable[str] | None,
) -> List[SystemFile]:
    if files is None:
        return []

    patterns = compile_exclude_patterns(exclude_patterns)
    if not patterns:
        return list(files)

    filtered_files = []
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
