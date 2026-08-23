# Copyright 2026, SeedSync Contributors, All rights reserved.

"""Bounded parsing for LFTP pget status sidecars.

LFTP normally writes a position/limit pair for each pget segment.  During a
late checkpoint it can expose only the base position, however.  That form is
still useful as a conservative resume checkpoint: the position is the only
bytes we can prove are covered, and the declared size is never used as
coverage.
"""

from dataclasses import dataclass
import re
from typing import Optional


MAX_LFTP_PGET_STATUS_BYTES = 64 * 1024
MAX_LFTP_PGET_SEGMENTS = 256


@dataclass(frozen=True)
class LftpPgetStatus:
    """Validated pget checkpoint data used by scanners and Queue admission."""

    total_size: int
    covered_size: int
    base_only: bool


_SIZE_PATTERN = re.compile(r"size=(\d+)")
_POSITION_PATTERN = re.compile(r"(\d+)\.pos=(\d+)")
_LIMIT_PATTERN = re.compile(r"(\d+)\.limit=(\d+)")


def _parse_decimal(value: str) -> Optional[int]:
    try:
        return int(value)
    except (ValueError, OverflowError):
        return None


def parse_lftp_pget_status(status: str) -> Optional[LftpPgetStatus]:
    """Parse one LFTP pget status snapshot, or return ``None`` if unsafe.

    A base-only snapshot is accepted only in the unambiguous single-segment
    form ``size=N`` followed by ``0.pos=P``.  Its covered size is ``P`` rather
    than ``N``.  Paired segment maps retain the existing hole-based coverage
    calculation and require contiguous, indexed, non-overlapping records.
    """
    if not isinstance(status, str):
        return None
    try:
        if len(status.encode("utf-8")) > MAX_LFTP_PGET_STATUS_BYTES:
            return None
    except UnicodeError:
        return None
    lines = [line.strip() for line in status.splitlines() if line.strip()]
    if not lines:
        return None

    size_match = re.fullmatch(_SIZE_PATTERN, lines.pop(0))
    if size_match is None:
        return None
    total_size = _parse_decimal(size_match.group(1))
    if total_size is None:
        return None

    # LFTP may flush a late checkpoint before writing the segment limit.  The
    # position is the only conservative coverage available in that form.
    if len(lines) == 1:
        position_match = re.fullmatch(_POSITION_PATTERN, lines[0])
        if position_match is None:
            return None
        segment_index = _parse_decimal(position_match.group(1))
        covered_size = _parse_decimal(position_match.group(2))
        if segment_index != 0 or covered_size is None or covered_size > total_size:
            return None
        return LftpPgetStatus(total_size, covered_size, base_only=True)

    if not lines or len(lines) % 2:
        return None
    if len(lines) // 2 > MAX_LFTP_PGET_SEGMENTS:
        return None

    empty_size = 0
    ranges: list[tuple[int, int]] = []
    for index in range(0, len(lines), 2):
        position_match = re.fullmatch(_POSITION_PATTERN, lines[index])
        limit_match = re.fullmatch(_LIMIT_PATTERN, lines[index + 1])
        expected_segment = index // 2
        if position_match is None or limit_match is None:
            return None
        position_segment = _parse_decimal(position_match.group(1))
        limit_segment = _parse_decimal(limit_match.group(1))
        position = _parse_decimal(position_match.group(2))
        limit = _parse_decimal(limit_match.group(2))
        if position_segment != expected_segment or limit_segment != expected_segment or \
                position is None or limit is None:
            return None
        if position > total_size or limit > total_size or limit < position:
            return None
        if any(position < previous_limit and previous_position < limit
               for previous_position, previous_limit in ranges):
            return None
        ranges.append((position, limit))
        empty_size += limit - position

    if empty_size > total_size:
        return None
    return LftpPgetStatus(total_size, total_size - empty_size, base_only=False)


def parse_lftp_pget_status_bytes(status: bytes) -> Optional[LftpPgetStatus]:
    """Decode and parse one bounded raw sidecar payload."""
    if not isinstance(status, bytes) or len(status) > MAX_LFTP_PGET_STATUS_BYTES:
        return None
    try:
        decoded = status.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return parse_lftp_pget_status(decoded)
