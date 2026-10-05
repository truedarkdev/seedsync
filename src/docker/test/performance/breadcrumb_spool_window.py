"""Read-only bounded historical snapshots of retained breadcrumb rotations.

Raw bytes are handed to a caller-owned private sink before decoding or schema
projection. The returned summary contains counters and finite status values
only; it never contains source names, row values, or paths.
"""
from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from typing import Any

from breadcrumb_spool_capture import BreadcrumbSpoolCapture, _ACTIVE_NAME, _ROTATED_NAME, _projection
from incoming_recovery_observer import ObserverCaptureError


MAX_WINDOW_FILES = 4
MAX_WINDOW_BYTES = 32 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 1024
_READ_CHUNK_BYTES = 8192


def select_window_files(
    inventory: Mapping[str, tuple[int, int, int]], start_ms: int, end_ms: int,
) -> dict[str, Any]:
    """Select rotated segments whose rename boundaries overlap ``[start,end]``.

    Rotation filenames encode rename time, not first-row time. The first
    retained rotation has no provable predecessor, so a matching interval is
    still returned for positive evidence but completeness remains unknown.
    """
    if type(start_ms) is not int or type(end_ms) is not int or start_ms < 0 or end_ms < start_ms:
        raise ValueError("requested window is invalid")
    rotations: list[tuple[int, int, str]] = []
    for name, identity in inventory.items():
        match = _ROTATED_NAME.fullmatch(name)
        if match is not None:
            rotations.append((int(match.group(1)), int(match.group(2)), name))
    rotations.sort()
    start_ns, end_ns = start_ms * 1_000_000, end_ms * 1_000_000
    candidates: list[str] = []
    reasons: set[str] = {"retention_unknown"}
    for index, (boundary_ns, _sequence, name) in enumerate(rotations):
        previous_boundary = rotations[index - 1][0] if index else None
        # A segment ends at its rename boundary and begins at the previous
        # rename. If the predecessor was already pruned, keep the candidate
        # for observations while explicitly refusing a completeness claim.
        if boundary_ns >= start_ns and (previous_boundary is None or previous_boundary <= end_ns):
            candidates.append(name)
            if previous_boundary is None:
                reasons.add("missing_predecessor")
    if _ACTIVE_NAME in inventory:
        latest_boundary = rotations[-1][0] if rotations else None
        if latest_boundary is None or latest_boundary <= end_ns:
            candidates.append(_ACTIVE_NAME)
            if latest_boundary is None:
                reasons.add("missing_predecessor")
    else:
        reasons.add("active_missing")
    if len(candidates) > MAX_WINDOW_FILES:
        omitted = len(candidates) - MAX_WINDOW_FILES
        # Preserve the oldest and newest boundaries under pressure; omitted
        # middle segments make the result explicitly incomplete.
        selected = candidates[:2] + candidates[-2:]
        selected = list(dict.fromkeys(selected))
        reasons.add("file_cap")
    else:
        omitted = 0
        selected = candidates
    return {
        "selected_names": tuple(selected),
        "candidate_count": len(candidates),
        "omitted_files": omitted,
        "reasons": reasons,
    }


def _inventory(directory_fd: int) -> tuple[dict[str, tuple[int, int, int]], bool]:
    entries: dict[str, tuple[int, int, int]] = {}
    exceeded = False
    try:
        iterator = os.scandir(directory_fd)
    except OSError as exc:
        raise ObserverCaptureError("spool directory cannot be inventoried") from exc
    try:
        for index, entry in enumerate(iterator):
            if index >= MAX_DIRECTORY_ENTRIES:
                exceeded = True
                break
            if entry.name != _ACTIVE_NAME and _ROTATED_NAME.fullmatch(entry.name) is None:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ObserverCaptureError("spool file cannot be inspected") from exc
            if not stat.S_ISREG(info.st_mode):
                raise ObserverCaptureError("spool entry is not a regular file")
            entries[entry.name] = (int(info.st_dev), int(info.st_ino), int(info.st_size))
    finally:
        iterator.close()
    return entries, exceeded


def capture_window(
    spool: int | str | os.PathLike[str], start_ms: int, end_ms: int, *,
    retain_raw: Callable[[str, int, bytes], None],
    consume_projected: Callable[[Mapping[str, Any]], None] | None = None,
    projector: Callable[[Mapping[str, Any]], tuple[dict[str, Any] | None, bool]] = _projection,
) -> dict[str, Any]:
    """Retain selected raw chunks before parsing and finite schema projection.

    ``retain_raw`` owns secure private storage. It receives only a validated
    basename, byte offset, and raw chunk; raw bytes never enter the summary or
    typed artifact sink. Historical coverage is always ``unknown`` because
    app retention may already have pruned a needed segment.
    """
    if not callable(retain_raw) or (consume_projected is not None and not callable(consume_projected)):
        raise ValueError("raw retention and consumer callbacks must be callable")
    summary: dict[str, Any] = {
        "window_ms": [start_ms, end_ms], "coverage": "unknown", "files_considered": 0,
        "files_read": 0, "omitted_files": 0, "bytes_retained": 0,
        "rows_projected": 0, "rows_rejected": 0,
        "raw_retention": "complete", "omissions": ["retention_unknown"],
    }
    reasons = {"retention_unknown"}
    try:
        directory_fd = BreadcrumbSpoolCapture._take_directory_fd(spool)
    except ObserverCaptureError:
        summary["omissions"] = ["read_failed", "retention_unknown"]
        summary["raw_retention"] = "unknown"
        return summary
    try:
        inventory, directory_exceeded = _inventory(directory_fd)
        selection = select_window_files(inventory, start_ms, end_ms)
        reasons.update(selection["reasons"])
        summary["files_considered"] = selection["candidate_count"]
        summary["omitted_files"] = selection["omitted_files"]
        if directory_exceeded:
            reasons.add("directory_limit")
        if (summary["omitted_files"] or directory_exceeded or "active_missing" in reasons):
            summary["raw_retention"] = "partial"
        bytes_left = MAX_WINDOW_BYTES
        stop = False
        for name in selection["selected_names"]:
            if stop:
                break
            dev, ino, size = inventory[name]
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(name, flags, dir_fd=directory_fd)
            except OSError:
                reasons.add("read_failed")
                summary["raw_retention"] = "unknown"
                break
            try:
                opened = os.fstat(fd)
                if (int(opened.st_dev), int(opened.st_ino)) != (dev, ino) or not stat.S_ISREG(opened.st_mode):
                    reasons.add("file_changed")
                    summary["raw_retention"] = "unknown"
                    break
                read_limit = min(size, bytes_left)
                if size > bytes_left:
                    reasons.add("byte_cap")
                    summary["raw_retention"] = "partial"
                offset = 0
                tail = b""
                summary["files_read"] += 1
                while offset < read_limit:
                    try:
                        chunk = os.pread(fd, min(_READ_CHUNK_BYTES, read_limit - offset), offset)
                    except (AttributeError, OSError):
                        reasons.add("read_failed")
                        summary["raw_retention"] = "unknown"
                        stop = True
                        break
                    if not chunk:
                        reasons.add("read_failed")
                        summary["raw_retention"] = "unknown"
                        stop = True
                        break
                    try:
                        retain_raw(name, offset, chunk)
                    except Exception:
                        reasons.add("raw_retention_failed")
                        summary["raw_retention"] = "failed"
                        stop = True
                        break
                    summary["bytes_retained"] += len(chunk)
                    bytes_left -= len(chunk)
                    offset += len(chunk)
                    complete_lines = (tail + chunk).split(b"\n")
                    tail = complete_lines.pop()
                    for raw_line in complete_lines:
                        line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line
                        if not line:
                            continue
                        try:
                            value = json.loads(line.decode("utf-8"))
                        except (UnicodeError, json.JSONDecodeError):
                            reasons.add("malformed_row")
                            summary["rows_rejected"] += 1
                            continue
                        if not isinstance(value, Mapping):
                            reasons.add("malformed_row")
                            summary["rows_rejected"] += 1
                            continue
                        created_ms = value.get("created_ms")
                        if type(created_ms) is not int and type(value.get("created_ns")) is int:
                            created_ms = value["created_ns"] // 1_000_000
                        if type(created_ms) is not int:
                            reasons.add("untimestamped_row")
                            summary["rows_rejected"] += 1
                            continue
                        if not start_ms <= created_ms <= end_ms:
                            continue
                        try:
                            projected, irrelevant = projector(value)
                        except Exception:
                            reasons.add("projection_failed")
                            summary["rows_rejected"] += 1
                            continue
                        if projected is None:
                            reasons.add("projection_rejected")
                            summary["rows_rejected"] += 1
                            continue
                        if not irrelevant:
                            try:
                                if consume_projected is not None:
                                    consume_projected(projected)
                            except Exception:
                                reasons.add("consumer_failed")
                                summary["rows_rejected"] += 1
                                continue
                            summary["rows_projected"] += 1
                if tail:
                    reasons.add("partial_line")
                after = os.fstat(fd)
                if int(after.st_size) != size or (int(after.st_dev), int(after.st_ino)) != (dev, ino):
                    reasons.add("file_changed")
                    summary["raw_retention"] = "unknown"
                if size > read_limit:
                    stop = True
            finally:
                os.close(fd)
        summary["omissions"] = sorted(reasons)
        return summary
    except (OSError, ObserverCaptureError):
        reasons.add("read_failed")
        summary["raw_retention"] = "unknown"
        summary["omissions"] = sorted(reasons)
        return summary
    finally:
        os.close(directory_fd)
