# Copyright 2026, SeedSync Contributors, All rights reserved.

from __future__ import annotations

import copy
import hashlib
import json
import math
import multiprocessing
import os
import queue
import re
import stat
import tempfile
import time
from collections import OrderedDict, deque
from threading import Event, Lock, RLock, Thread
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional, Protocol, cast

from .redaction import redact_sensitive_text


# Kept only in this process so correlations never disclose canonical runtime
# identities.  Breadcrumbs are diagnostic hints, not a durable identity map.
_OPAQUE_TRACE_CORRELATION_KEY = os.urandom(16)


# This is deliberately a byte budget rather than a second implicit entry cap.
# A caller may opt out of the count limit (``max_entries=None``), while the
# retained, sanitized representation remains bounded by this exact default.
DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES = 256 * 1024 * 1024
# Descriptive alias used by configuration/context integrations.
DEFAULT_BREADCRUMB_TRACE_MEMORY_BUDGET_BYTES = DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES
DEFAULT_BREADCRUMB_TRACE_MAX_ENTRIES = 0
DEFAULT_BREADCRUMB_POLICY_LEVEL = "info"
BREADCRUMB_POLICY_LEVELS = ("off", "error", "warning", "info", "debug", "trace")
# Child processes must never hand an arbitrary object graph to multiprocessing.
# This is deliberately a distinct, small ingress allowance; the configured
# 256 MiB budget applies only to retained, sanitized evidence.
_INGRESS_RECORD_MAX_BYTES = 8 * 1024
# Structured diagnostics may contain more fields than the generic collection
# limit, but child-process ingress must retain a finite mapping bound.
_INGRESS_MAPPING_MAX_ITEMS = 24
_INGRESS_LIST_MAX_ITEMS = 16
_INGRESS_NODE_BUDGET = 256
_INGRESS_STRING_MAX_CHARS = 256
_INGRESS_FIXED_JSON_OVERHEAD = 2048
_INGRESS_TAIL_OVERHEAD = 256
_SHARED_POLICY_MAX_BYTES = 64 * 1024
_EFFECTIVE_POLICY_CATEGORY_CACHE_MAX = 128
_POLICY_LEVEL_RANK = {level: index for index, level in enumerate(BREADCRUMB_POLICY_LEVELS)}


def _bounded_redaction_text(value: str, limit: int = 4096) -> str:
    """Keep redaction work bounded while retaining both key/value edges."""
    if len(value) <= limit:
        return value
    half = max(1, limit // 2)
    return value[:half] + "...<omitted>..." + value[-half:]


def _bounded_keyword_match(value: str, keywords: Iterable[str], limit: int = 4096) -> bool:
    bounded = _bounded_redaction_text(value, limit)
    lowered = bounded.lower()
    return any(keyword in lowered for keyword in keywords)


def _bounded_mapping_copy(value: object, max_items: int = _INGRESS_MAPPING_MAX_ITEMS) -> Dict[Any, Any]:
    """Copy only a fixed prefix of a Mapping without expanding its iterator."""
    if not isinstance(value, Mapping):
        return {}
    bounded: Dict[Any, Any] = {}
    try:
        iterator = iter(value.items())
        for _index in range(max(0, int(max_items))):
            try:
                item_key, item_value = next(iterator)
            except StopIteration:
                break
            bounded[item_key] = item_value
    except Exception:
        return bounded
    return bounded

# The child ingress is deliberately split into two fixed lanes.  The normal
# lane can be exhausted by high-volume progress observations without
# preventing a lifecycle/failure boundary from crossing the process boundary.
# These are ingress limits, not retention limits.
# Keep the pre-durable normal ingress allowance (2048 records) when the
# diagnostic drainer is disabled. The reserved critical lane is additional;
# enabling the drainer still gives both lanes fixed, explicit bounds.
BREADCRUMB_INGRESS_NORMAL_CAPACITY = 2048
BREADCRUMB_INGRESS_CRITICAL_CAPACITY = 64
BREADCRUMB_INGRESS_TOTAL_CAPACITY = (
    BREADCRUMB_INGRESS_NORMAL_CAPACITY + BREADCRUMB_INGRESS_CRITICAL_CAPACITY
)
BREADCRUMB_INGRESS_CRITICAL_BURST = 8
# A producer call owns at most one token while it crosses the emitter path.
# Keeping this separate from Queue capacity bounds close-time quiescence state
# even if a child process is hostile or stalls inside a handoff.
_BREADCRUMB_INGRESS_INFLIGHT_CAPACITY = BREADCRUMB_INGRESS_TOTAL_CAPACITY
_BREADCRUMB_LIFECYCLE_OPEN = 0
_BREADCRUMB_LIFECYCLE_CLOSING = 1
_BREADCRUMB_LIFECYCLE_CLOSED = 2
_ADMISSION_ACCEPTED = "ADMITTED"
_ADMISSION_REJECTED_OPEN = "REJECTED_OPEN"
_ADMISSION_INERT = "INERT"

# The parent-local durable queues are intentionally smaller than process
# ingress. Their aggregate bound is independent of retained-memory settings.
BREADCRUMB_DURABLE_TOTAL_CAPACITY = 256
BREADCRUMB_DURABLE_CRITICAL_CAPACITY = 64
BREADCRUMB_DURABLE_NORMAL_CAPACITY = 192
BREADCRUMB_DURABLE_RECORD_MAX_BYTES = 64 * 1024
BREADCRUMB_DURABLE_AGGREGATE_MAX_BYTES = 8 * 1024 * 1024
BREADCRUMB_DURABLE_MAX_FILES = 8
BREADCRUMB_DURABLE_MAX_BYTES = 64 * 1024 * 1024
BREADCRUMB_DURABLE_OWNER_STATES = (
    "DISABLED", "RUNNING", "DRAINING", "INCOMPLETE", "TERMINAL",
)
_DURABLE_ROTATED_NAME = re.compile(r"^breadcrumbs-(\d+)-(\d+)\.jsonl$")
_DURABLE_QUARANTINE_NAME = re.compile(r"^breadcrumbs-quarantine-(\d+)-(\d+)\.jsonl$")
_DURABLE_HEALTH_NAME = "breadcrumbs.health.json"
_DURABLE_HEALTH_INVALID_MARKER = "breadcrumbs.health.invalid"
_DURABLE_SESSION_BOUNDARY_SCHEMA = "breadcrumb_durable_session_boundary.v1"
# Every ordinary JSONL record is bound to the writer session.  Keep this
# small fixed allowance in the admission estimate for the metadata appended
# by the writer after the producer snapshot has been accepted.
_DURABLE_SESSION_ID_FIELD_ESTIMATE_BYTES = len(
    b'"session_id":"0123456789abcdef",',
)
_DURABLE_RETENTION_SCAN_LIMIT = 1024
_DURABLE_UPSTREAM_HEALTH_KEYS = frozenset({
    "critical_ingress_enqueued", "normal_ingress_enqueued",
    "critical_ingress_rejected", "normal_ingress_rejected",
    "critical_ingress_overflow", "normal_ingress_overflow",
    "critical_ingress_ack", "normal_ingress_ack",
    "critical_ingress_reserved", "normal_ingress_reserved",
    "collector_accepted", "collector_rejected", "collector_evicted",
    "collector_durable_rejected", "ingress_shutdown_leftovers",
    "ingress_unresolved", "critical_ingress_unknown",
    "normal_ingress_unknown", "ingress_reservation_unknown",
})

# Durable capture is a separate opt-in from the in-memory trace.  In
# particular, creating a Context with the normal trace disabled must not
# create directories, files, or background threads.
DEFAULT_BREADCRUMB_TRACE_DURABLE_ENABLED = False
DEFAULT_BREADCRUMB_TRACE_DURABLE_PATH = ""
DEFAULT_BREADCRUMB_DURABLE_BATCH_SIZE = 32
DEFAULT_BREADCRUMB_DURABLE_FLUSH_INTERVAL_SECONDS = 0.25
DEFAULT_BREADCRUMB_DURABLE_ROTATE_BYTES = 4 * 1024 * 1024
DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS = 2.0
# Direct spool construction is private, but its knobs still need a fixed
# upper bound so callers cannot silently turn bounded async capture into an
# unbounded latency or batch-memory contract.
BREADCRUMB_DURABLE_BATCH_SIZE_MAX = BREADCRUMB_DURABLE_TOTAL_CAPACITY
BREADCRUMB_DURABLE_FLUSH_INTERVAL_MAX_SECONDS = DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS


def is_critical_breadcrumb(
    source: object,
    message: object,
    details: object = None,
    metadata: Optional[Mapping[str, object]] = None,
    **metadata_fields: object,
) -> bool:
    """Return the fixed internal priority for one sanitized-bound record.

    Priority is intentionally derived only from the event's semantic fields;
    callers cannot select a lane.  ``model.progress`` and ordinary scanner
    progress/root-shape records remain normal unless they are explicitly a
    warning, error, or failure.
    """
    if metadata_fields:
        fields = _bounded_mapping_copy(metadata)
        for key, value in _bounded_mapping_copy(metadata_fields).items():
            if len(fields) >= _INGRESS_MAPPING_MAX_ITEMS and key not in fields:
                break
            fields[key] = value
    else:
        fields = metadata or {}
    category = fields.get("category", source)
    event_type = fields.get("event_type", "breadcrumb")
    level = fields.get("level", "info")
    category_text = category if isinstance(category, str) else "<{}>".format(type(category).__name__[:48])
    message_text = message if isinstance(message, str) else "<{}>".format(type(message).__name__[:48])
    event_type_text = event_type.strip().lower() if isinstance(event_type, str) else "breadcrumb"
    level_text = level.strip().lower() if isinstance(level, str) else "info"
    if level_text in {"warning", "error", "critical"} or event_type_text == "failure":
        return True
    # Exact category boundaries owned by Queue/LFTP/scanner publication code.
    # High-volume queue.exclusion/readiness and model progress remain normal.
    if category_text in {"queue.admission", "queue.executor", "queue.lifecycle"}:
        return True
    # These are the approved informational LFTP lifecycle/membership records;
    # status polling, command and PTY traffic are deliberately not included.
    if category_text in {"transfer.lftp.executor", "transfer.lftp.membership"}:
        return True
    if category_text == "transfer.lftp" and message_text in {
        "lifecycle", "lftp_lifecycle", "lftp_membership",
        "lftp_path_pair_annotation",
    }:
        return True
    if category_text in {
        "transfer.stop", "final_move.publication", "scan.authority",
        "scan.missing_root", "scan.failure", "scan.terminal_publication",
    }:
        return True
    # A scanner failure/authority marker that predates the more specific
    # category names still has an exact message boundary.  Do not use a broad
    # "scan" prefix, which would make ordinary root-shape traffic critical.
    if category_text.startswith("scan.") and message_text in {
        "scan_failed", "scan_failure", "missing_root", "scan_authority",
        "terminal_publication", "scan_terminal_publication",
    }:
        return True
    if category_text == "scanner_process" and message_text in {
        "scan_failed", "scan_failure", "scan_missing_root",
    }:
        return True
    if (
        category_text == "scanner_process"
        and message_text == "scan_result_published"
        and isinstance(details, Mapping)
        and details.get("final") is True
    ):
        return True
    if (
        category_text == "scan.result"
        and isinstance(details, Mapping)
        and details.get("is_scan_final") is True
    ):
        return True
    return False


def _estimate_durable_value_bytes(value: object, depth: int = 0) -> int:
    """Cheap upper-bound estimate used before the durable deep-copy handoff."""
    if depth > 5:
        return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
    if value is None or isinstance(value, bool):
        return 32
    if isinstance(value, int):
        if value.bit_length() > 128:
            return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
        return min(len(str(value)) + 8, BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1)
    if isinstance(value, float):
        if not math.isfinite(value) or len(repr(value)) > 32:
            return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
        return len(repr(value)) + 8
    if isinstance(value, str):
        # Avoid scanning an oversized string. Six bytes per character covers
        # UTF-8 plus JSON escaping; the writer performs the exact measurement.
        character_count = len(value)
        if character_count > BREADCRUMB_DURABLE_RECORD_MAX_BYTES:
            return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
        return min(character_count * 6 + 8, BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1)
    if isinstance(value, Mapping):
        total = 16
        try:
            for index, (key, item) in enumerate(value.items()):
                if index >= _INGRESS_MAPPING_MAX_ITEMS:
                    return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
                bounded_key = (
                    _bounded_redaction_text(key, _INGRESS_STRING_MAX_CHARS)
                    if isinstance(key, str) else "<key>"
                )
                total += _estimate_durable_value_bytes(bounded_key, depth + 1)
                total += _estimate_durable_value_bytes(item, depth + 1)
                if total > BREADCRUMB_DURABLE_RECORD_MAX_BYTES:
                    return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
        except Exception:
            return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
        return total
    if isinstance(value, (list, tuple)):
        total = 16
        try:
            for index, item in enumerate(value):
                # Collector sanitization may append one fixed truncation
                # marker after its retained prefix.
                if index > _INGRESS_LIST_MAX_ITEMS:
                    return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
                total += _estimate_durable_value_bytes(item, depth + 1)
                if total > BREADCRUMB_DURABLE_RECORD_MAX_BYTES:
                    return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
        except Exception:
            return BREADCRUMB_DURABLE_RECORD_MAX_BYTES + 1
        return total
    return 128


def _durable_error_class(error: BaseException, flush_failed: bool = False) -> str:
    if isinstance(error, _DurableQuarantineFailure):
        return "active_file_quarantine_failure"
    if isinstance(error, _DurableRollbackFailure):
        return "active_file_quarantined"
    if isinstance(error, _DurableRetentionFailure):
        return "retention_failure"
    if isinstance(error, _DurableShortWrite):
        return "partial_write"
    if flush_failed:
        return "flush_failure"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, (TypeError, ValueError, OverflowError, UnicodeError, json.JSONDecodeError)):
        return "serialization_failure"
    if isinstance(error, OSError):
        return "storage_unavailable"
    return "writer_failure"


class _DurableRetentionFailure(OSError):
    """The bounded rotation set could not be inspected/pruned safely."""


class _DurableRollbackFailure(OSError):
    """A failed write could not be removed from the active JSONL file."""


class _DurableQuarantineFailure(OSError):
    """A failed write could not be quarantined after rollback failure."""


class _DurableShortWrite(OSError):
    """The file object accepted fewer bytes than one JSONL record."""


class BreadcrumbDurableSpool:
    """Bounded parent-local JSONL spool with isolated durable I/O.

    Producers only perform a bounded deep-copy handoff and ``put_nowait``.
    JSON encoding, directory/file access, flushing, rotation, and health
    publication happen on the writer thread.  A separate queue and counters
    ensure critical saturation is visible instead of silently looking like a
    healthy trace.
    """

    def __init__(
        self,
        path: str,
        *,
        total_capacity: int = BREADCRUMB_DURABLE_TOTAL_CAPACITY,
        critical_capacity: int = BREADCRUMB_DURABLE_CRITICAL_CAPACITY,
        normal_capacity: Optional[int] = BREADCRUMB_DURABLE_NORMAL_CAPACITY,
        batch_size: int = DEFAULT_BREADCRUMB_DURABLE_BATCH_SIZE,
        flush_interval_seconds: float = DEFAULT_BREADCRUMB_DURABLE_FLUSH_INTERVAL_SECONDS,
        rotate_bytes: int = DEFAULT_BREADCRUMB_DURABLE_ROTATE_BYTES,
        shutdown_timeout_seconds: float = DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS,
        max_files: int = BREADCRUMB_DURABLE_MAX_FILES,
        max_bytes: int = BREADCRUMB_DURABLE_MAX_BYTES,
        upstream_health_getter: Optional[Callable[[], Mapping[str, int]]] = None,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("durable breadcrumb path must be a non-empty string")
        if type(total_capacity) is not int or total_capacity < 2:
            raise ValueError("total_capacity must be at least 2")
        if type(critical_capacity) is not int or not 1 <= critical_capacity < total_capacity:
            raise ValueError("critical_capacity must leave normal capacity")
        if normal_capacity is None:
            normal_capacity = total_capacity - critical_capacity
        if type(normal_capacity) is not int or normal_capacity < 1:
            raise ValueError("normal_capacity must be positive")
        if critical_capacity + normal_capacity > total_capacity:
            raise ValueError("lane capacities exceed total_capacity")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be positive")
        try:
            requested_flush_interval = float(flush_interval_seconds)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("flush_interval_seconds must be positive") from error
        if (
            type(flush_interval_seconds) not in (int, float)
            or not math.isfinite(requested_flush_interval)
            or requested_flush_interval <= 0
        ):
            raise ValueError("flush_interval_seconds must be positive")
        if type(rotate_bytes) is not int or rotate_bytes < 1:
            raise ValueError("rotate_bytes must be positive")
        if type(max_files) is not int or max_files < 2:
            raise ValueError("max_files must be at least 2")
        if type(max_bytes) is not int or max_bytes < rotate_bytes:
            raise ValueError("max_bytes must cover one active file")
        self.path = os.path.abspath(path)
        self.total_capacity = total_capacity
        self.critical_capacity = critical_capacity
        self.normal_capacity = normal_capacity
        self.batch_size = min(batch_size, BREADCRUMB_DURABLE_BATCH_SIZE_MAX)
        self.flush_interval_seconds = min(
            requested_flush_interval,
            BREADCRUMB_DURABLE_FLUSH_INTERVAL_MAX_SECONDS,
        )
        self.rotate_bytes = rotate_bytes
        self.shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.__upstream_health_getter = upstream_health_getter
        self.__critical_records: queue.Queue[tuple[Dict[str, Any], bool, int]] = queue.Queue(maxsize=critical_capacity)
        self.__normal_records: queue.Queue[tuple[Dict[str, Any], bool, int]] = queue.Queue(maxsize=normal_capacity)
        self.__stop = Event()
        self.__wake = Event()
        self.__lock = Lock()
        self.__queued_bytes = 0
        self.__inflight_bytes = 0
        self.__writing = False
        self.__critical_budget = BREADCRUMB_INGRESS_CRITICAL_BURST
        self.__next_health_publish_at = 0.0
        self.__thread = Thread(target=self.__run, name="breadcrumb-durable-writer", daemon=True)
        self.__closed = False
        self.__closing = False
        self.__shutdown_incomplete = False
        self.__unresolved_marked = 0
        self.__unknown_marked = 0
        self.__file_handle: Optional[Any] = None
        self.__file_path: Optional[str] = None
        self.__file_size = 0
        self.__rotation_index = 0
        self.__retention_failed = False
        self.__active_file_disabled = False
        self.__directory_ready = False
        self.__terminal_state = "TERMINAL"
        self.__session_id = hashlib.blake2s(
            os.urandom(16), digest_size=8,
        ).hexdigest()
        self.__session_boundary_written = False
        self.__health: Dict[str, Any] = {
            "schema": "breadcrumb_durable_health.v1",
            "session_id": self.__session_id,
            "state": "RUNNING",
            "enabled": True,
            "total_capacity": total_capacity,
            "critical_capacity": critical_capacity,
            "normal_capacity": normal_capacity,
            "accepted": 0,
            "written": 0,
            "lost": 0,
            "critical_lost": 0,
            "normal_lost": 0,
            "writer_failures": 0,
            "flush_count": 0,
            "rotation_count": 0,
            "last_error_class": None,
            "flush_failures": 0,
            "partial_write_failures": 0,
            "retention_prune_count": 0,
            "retention_prune_failures": 0,
            "active_file_quarantined": 0,
            "active_file_quarantine_failures": 0,
            "closed": False,
            "terminal": False,
            "incomplete": False,
            "shutdown_incomplete": False,
            "unresolved": 0,
            "unknown": 0,
            "health_publish_failures": 0,
            "health_publish_ok": True,
            "session_boundary_written": False,
            "session_boundary_failures": 0,
            "offline_retrieval_invalidated": False,
            # Offline retrieval is unavailable until the writer commits its
            # session boundary in JSONL. Windows never claims this contract.
            "offline_retrieval": "unavailable" if os.name != "nt" else "unsupported",
            # POSIX writes are fsync-backed. Windows uses an explicit
            # best-effort label because directory handles are not uniformly
            # fsync-able there; it never claims stronger durability.
            "durability": "fsync" if os.name != "nt" else "buffered_best_effort",
            "path_safety": "trusted_linux_path_checks" if os.name != "nt" else "windows_acl_unverified",
        }
        # Replace any stale terminal sidecar before the writer starts.  This
        # marker is synchronous so a crash immediately after construction
        # cannot leave a previous session looking terminal/complete.
        self.__publish_health()
        self.__thread.start()

    @property
    def critical_queue(self) -> queue.Queue[tuple[Dict[str, Any], bool, int]]:
        return self.__critical_records

    @property
    def normal_queue(self) -> queue.Queue[tuple[Dict[str, Any], bool, int]]:
        return self.__normal_records

    def enqueue(self, record: Mapping[str, Any], critical: bool) -> bool:
        """Admit one already-sanitized record without waiting or doing I/O."""
        if not isinstance(record, dict):
            # Collector and ingress handoffs are canonical built-in dicts.
            # Refuse arbitrary Mapping implementations whose copy operation
            # could materialize an unbounded iterator on this hot path.
            self.__record_loss(critical)
            return False
        estimated_bytes = (
            _estimate_durable_value_bytes(record)
            + _DURABLE_SESSION_ID_FIELD_ESTIMATE_BYTES
        )
        if estimated_bytes > BREADCRUMB_DURABLE_RECORD_MAX_BYTES:
            self.__record_loss(critical)
            return False
        with self.__lock:
            if (
                self.__closed or self.__closing or self.__stop.is_set()
                or self.__active_file_disabled or self.__retention_failed
                or self.__queued_bytes + self.__inflight_bytes + estimated_bytes
                > BREADCRUMB_DURABLE_AGGREGATE_MAX_BYTES
            ):
                self.__record_loss_locked(critical)
                return False
        # ``record`` is a private collector entry. Coalescing and retention
        # mutate nested values, so the writer receives an independent bounded
        # snapshot after the cheap byte bound and before collector eviction.
        try:
            snapshot = copy.deepcopy(record)
        except Exception:
            self.__record_loss(critical)
            return False
        target = self.__critical_records if critical else self.__normal_records
        try:
            with self.__lock:
                if (
                    self.__closed or self.__closing or self.__stop.is_set()
                    or self.__active_file_disabled or self.__retention_failed
                    or self.__queued_bytes + self.__inflight_bytes + estimated_bytes
                    > BREADCRUMB_DURABLE_AGGREGATE_MAX_BYTES
                ):
                    self.__record_loss_locked(critical)
                    return False
                target.put_nowait((snapshot, critical, estimated_bytes))
                self.__queued_bytes += estimated_bytes
                self.__health["accepted"] += 1
        except (queue.Full, OSError, ValueError):
            self.__record_loss(critical)
            return False
        self.__wake.set()
        return True

    def __record_loss(self, critical: bool) -> None:
        with self.__lock:
            self.__record_loss_locked(critical)

    def __record_loss_locked(self, critical: bool) -> None:
        self.__health["lost"] += 1
        self.__health["critical_lost" if critical else "normal_lost"] += 1

    def __run(self) -> None:
        # Directory creation is deliberately owned by the writer.  A failed
        # mkdir therefore cannot delay a model/scanner producer.
        boundary_committed = self.__publish_session_boundary()
        if not boundary_committed:
            # A session without its independently committed boundary cannot
            # publish valid offline evidence.  Retire this owner and account
            # any already-queued records as loss; re-enable creates a fresh
            # owner and therefore a fresh boundary.
            self.__stop.set()
        self.__publish_health()
        with self.__lock:
            self.__next_health_publish_at = time.monotonic() + self.flush_interval_seconds
        self.__prune_rotated_files()
        while True:
            batch = self.__take_batch()
            if batch:
                with self.__lock:
                    self.__writing = True
                try:
                    self.__write_batch(batch)
                finally:
                    with self.__lock:
                        self.__inflight_bytes = max(
                            0, self.__inflight_bytes - sum(item[2] for item in batch)
                        )
                        self.__writing = False
            if self.__stop.is_set() and self.__queues_empty():
                break
            if not batch:
                self.__wake.wait(self.flush_interval_seconds)
                self.__wake.clear()
                # Periodic health publication captures upstream lane rejects
                # even when no record was admitted to the writer queue.
                self.__publish_health_if_due()
        self.__close_file()
        with self.__lock:
            if self.__shutdown_incomplete:
                self.__health["enabled"] = False
                self.__health["closed"] = False
                self.__health["terminal"] = False
                self.__health["incomplete"] = True
                self.__health["state"] = "INCOMPLETE"
            else:
                self.__health["enabled"] = False
                self.__health["closed"] = True
                self.__health["terminal"] = True
                self.__health["incomplete"] = False
                self.__health["state"] = self.__terminal_state
        self.__publish_health()

    def __publish_session_boundary(self) -> bool:
        """Commit the writer session binding before any ordinary record.

        The boundary is a metadata-only JSONL record.  It is fsynced as its
        own write, including the parent directory on supported POSIX hosts,
        before health may advertise offline retrieval as valid.  A failed
        boundary permanently disables this owner; no ordinary record can be
        mistaken for an independently committed session.
        """
        boundary = {
            "schema": _DURABLE_SESSION_BOUNDARY_SCHEMA,
            "session_id": self.__session_id,
            "session_boundary": True,
            "state": "OPEN",
            "created_ms": int(time.time_ns() / 1_000_000),
        }
        try:
            encoded = (
                json.dumps(
                    boundary,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            self.__open_file(len(encoded))
            if self.__file_handle is None:
                raise OSError("durable breadcrumb file is not open")
            boundary_start = self.__file_size
            boundary_write_succeeded = False
            try:
                written = self.__file_handle.write(encoded)
                if written is None:
                    written = len(encoded)
                if written != len(encoded):
                    with self.__lock:
                        self.__health["partial_write_failures"] += 1
                    raise _DurableShortWrite("short session boundary write")
                self.__file_size += int(written)
                boundary_write_succeeded = True
                self.__file_handle.flush()
                os.fsync(self.__file_handle.fileno())
                if os.name != "nt" and not self.__fsync_directory():
                    raise OSError("session boundary directory sync unavailable")
            except Exception as boundary_error:
                if not boundary_write_succeeded and not isinstance(
                    boundary_error, _DurableShortWrite,
                ):
                    with self.__lock:
                        self.__health["partial_write_failures"] += 1
                if not self.__rollback_file(boundary_start):
                    self.__quarantine_active_file()
                raise boundary_error
            with self.__lock:
                self.__session_boundary_written = True
                self.__health["session_boundary_written"] = True
            return True
        except Exception as error:
            self.__close_file()
            with self.__lock:
                self.__active_file_disabled = True
                self.__shutdown_incomplete = True
                self.__session_boundary_written = False
                self.__health["session_boundary_written"] = False
                self.__health["session_boundary_failures"] += 1
                self.__health["writer_failures"] += 1
                self.__health["last_error_class"] = _durable_error_class(error)
            return False

    def __publish_health_if_due(self) -> bool:
        """Throttle routine health sidecar writes to the configured cadence."""
        now = time.monotonic()
        with self.__lock:
            if now < self.__next_health_publish_at:
                return False
            self.__next_health_publish_at = now + self.flush_interval_seconds
        self.__publish_health()
        return True

    def __take_batch(self) -> List[tuple[Dict[str, Any], bool, int]]:
        result: List[tuple[Dict[str, Any], bool, int]] = []
        batch_deadline: Optional[float] = None
        # Critical records are preferred but a normal item is admitted after a
        # bounded burst, preventing a permanently busy failure lane from
        # starving regular context.
        critical_budget = self.__critical_budget
        while len(result) < self.batch_size:
            selected: Optional[queue.Queue[tuple[Dict[str, Any], bool, int]]] = None
            if critical_budget > 0:
                selected = self.__critical_records
            else:
                selected = self.__normal_records
            try:
                item = selected.get_nowait()
                result.append(item)
                if batch_deadline is None:
                    batch_deadline = time.monotonic() + self.flush_interval_seconds
                with self.__lock:
                    self.__queued_bytes = max(0, self.__queued_bytes - item[2])
                    self.__inflight_bytes += item[2]
                if selected is self.__critical_records:
                    critical_budget -= 1
                else:
                    critical_budget = BREADCRUMB_INGRESS_CRITICAL_BURST
                continue
            except queue.Empty:
                if selected is self.__critical_records:
                    try:
                        item = self.__normal_records.get_nowait()
                        result.append(item)
                        if batch_deadline is None:
                            batch_deadline = time.monotonic() + self.flush_interval_seconds
                        with self.__lock:
                            self.__queued_bytes = max(0, self.__queued_bytes - item[2])
                            self.__inflight_bytes += item[2]
                        critical_budget = BREADCRUMB_INGRESS_CRITICAL_BURST
                        continue
                    except queue.Empty:
                        pass
                else:
                    try:
                        item = self.__critical_records.get_nowait()
                        result.append(item)
                        if batch_deadline is None:
                            batch_deadline = time.monotonic() + self.flush_interval_seconds
                        with self.__lock:
                            self.__queued_bytes = max(0, self.__queued_bytes - item[2])
                            self.__inflight_bytes += item[2]
                        critical_budget = max(0, BREADCRUMB_INGRESS_CRITICAL_BURST - 1)
                        continue
                    except queue.Empty:
                        pass
                if not result or self.__stop.is_set():
                    break
                remaining = max(0.0, batch_deadline - time.monotonic())
                if remaining <= 0:
                    break
                self.__wake.wait(remaining)
                self.__wake.clear()
        self.__critical_budget = critical_budget
        return result

    def __queues_empty(self) -> bool:
        return self.__critical_records.empty() and self.__normal_records.empty()

    def __validate_directory_path(self) -> None:
        """Reject symlink/reparse components before touching durable files."""
        path = os.path.abspath(self.path)
        drive, tail = os.path.splitdrive(path)
        current = drive + os.sep if os.path.isabs(tail) else drive
        for component in tail.replace("/", os.sep).split(os.sep):
            if not component:
                continue
            current = os.path.join(current, component)
            try:
                directory_stat = os.lstat(current)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise PermissionError("durable parent cannot be inspected") from error
            reparse = int(getattr(directory_stat, "st_file_attributes", 0)) & 0x400
            if stat.S_ISLNK(directory_stat.st_mode) or reparse:
                raise PermissionError("durable parent contains a symlink or reparse point")
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise NotADirectoryError("durable path component is not a directory")

    def __ensure_directory(self) -> None:
        self.__validate_directory_path()
        if not self.__directory_ready:
            try:
                os.makedirs(self.path, mode=0o700, exist_ok=True)
                self.__validate_directory_path()
                os.chmod(self.path, 0o700)
            except OSError as error:
                raise PermissionError("durable directory permissions cannot be enforced") from error
            self.__directory_ready = True
        if os.name != "nt":
            try:
                directory_mode = stat.S_IMODE(os.stat(self.path, follow_symlinks=False).st_mode)
            except OSError as error:
                raise PermissionError("durable directory permissions cannot be verified") from error
            if directory_mode != 0o700:
                raise PermissionError("durable directory permissions are not private")

    def __fsync_directory(self) -> bool:
        """Fsync the parent directory where the platform exposes that contract."""
        if os.name == "nt":
            with self.__lock:
                self.__health["durability"] = "buffered_best_effort"
            return False
        directory_descriptor = -1
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_descriptor = os.open(self.path, flags)
            os.fsync(directory_descriptor)
            return True
        finally:
            if directory_descriptor >= 0:
                try:
                    os.close(directory_descriptor)
                except OSError:
                    pass

    def __health_marker_path(self) -> str:
        return os.path.join(self.path, _DURABLE_HEALTH_INVALID_MARKER)

    def __clear_health_invalidation_marker(self, *, sync: bool) -> bool:
        """Clear only our regular invalidation marker after sidecar commit."""
        marker_path = self.__health_marker_path()
        try:
            marker_stat = os.lstat(marker_path)
        except FileNotFoundError:
            return True
        except OSError as error:
            raise PermissionError("health invalidation marker cannot be inspected") from error
        reparse = int(getattr(marker_stat, "st_file_attributes", 0)) & 0x400
        if stat.S_ISLNK(marker_stat.st_mode) or reparse or not stat.S_ISREG(marker_stat.st_mode):
            raise PermissionError("health invalidation marker is not a regular file")
        if not sync:
            # A non-fsynced health projection may be lost across a crash.
            # Keep the marker sticky until a fully synchronized publication
            # commits and clears it.
            return False
        os.unlink(marker_path)
        if sync and not self.__fsync_directory():
            raise OSError("health invalidation marker directory sync unavailable")
        return True

    def __write_health_invalidation_marker(self, *, sync: bool) -> bool:
        """Atomically publish offline invalidation when sidecar publication fails.

        The marker is deliberately separate from ``breadcrumbs.health.json``:
        retaining an older terminal sidecar is useful forensic evidence, but
        an offline reader must reject it until a later health publication
        succeeds and clears this marker.  Windows does not claim this
        contract because ACL privacy cannot be verified there.
        """
        if os.name == "nt":
            return False
        descriptor = -1
        temporary_path = None
        try:
            self.__ensure_directory()
            payload = json.dumps({
                "schema": "breadcrumb_durable_health_invalidation.v1",
                "session_id": self.__session_id,
                "state": "INVALID",
                "reason": "health_publish_failure",
                "updated_ms": int(time.time_ns() / 1_000_000),
            }, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            descriptor, temporary_path = tempfile.mkstemp(
                prefix="breadcrumbs.health.invalid.", suffix=".tmp", dir=self.path,
            )
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:
                os.chmod(temporary_path, 0o600)
            temporary_stat = os.stat(temporary_path, follow_symlinks=False)
            if not stat.S_ISREG(temporary_stat.st_mode):
                raise PermissionError("health invalidation marker is not a regular file")
            if stat.S_IMODE(temporary_stat.st_mode) != 0o600:
                raise PermissionError("health invalidation marker permissions are not private")
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                written = handle.write(payload)
                if written is not None and written != len(payload):
                    raise OSError("short health invalidation marker write")
                handle.flush()
                # Invalidation is a safety boundary even when the associated
                # health projection is deliberately buffered (sync=False).
                # Always fsync this marker so a crash cannot resurrect an old
                # terminal sidecar without a durable reject signal.
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.__health_marker_path())
            temporary_path = None
            if not self.__fsync_directory():
                raise OSError("health invalidation marker directory sync unavailable")
            return True
        except Exception:
            return False
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def __open_file(self, required_bytes: int = 0) -> None:
        if self.__retention_failed or self.__active_file_disabled:
            raise _DurableRetentionFailure("durable file output is disabled")
        if required_bytes > self.max_bytes:
            # A single record larger than the total retention bound cannot be
            # written without violating the fixed byte cap.  Fail closed.
            raise _DurableRetentionFailure("durable record exceeds retention bound")
        self.__ensure_directory()
        active_path = os.path.join(self.path, "breadcrumbs.jsonl")
        if self.__file_handle is not None and self.__file_path == active_path:
            if self.__file_size > self.max_bytes:
                self.__retention_failed = True
                raise _DurableRetentionFailure("active durable file exceeds retention bound")
            if self.__file_size + required_bytes <= self.rotate_bytes:
                return
            self.__rotate_active(active_path)
        else:
            try:
                active_stat = os.stat(active_path, follow_symlinks=False)
            except FileNotFoundError:
                active_stat = None
            except OSError as error:
                raise _DurableRetentionFailure("active durable file cannot be inspected") from error
            if active_stat is not None:
                if not stat.S_ISREG(active_stat.st_mode):
                    raise _DurableRetentionFailure("active durable path is not a regular file")
                existing_size = int(active_stat.st_size)
                if existing_size > self.max_bytes:
                    self.__retention_failed = True
                    raise _DurableRetentionFailure("active durable file exceeds retention bound")
                if existing_size + required_bytes > self.rotate_bytes:
                    if self.__file_handle is not None:
                        self.__close_file()
                    self.__rotate_active(active_path)
        if self.__retention_failed or self.__active_file_disabled:
            raise _DurableRetentionFailure("durable file output is disabled")
        # A concurrent/foreign rename cannot make an unsafe path become the
        # active output between inspection and open. O_NOFOLLOW is used where
        # available; the descriptor is always closed on every failure path.
        try:
            active_stat = os.stat(active_path, follow_symlinks=False)
        except FileNotFoundError:
            active_stat = None
        except OSError as error:
            raise _DurableRetentionFailure("active durable file cannot be inspected") from error
        if active_stat is not None and not stat.S_ISREG(active_stat.st_mode):
            raise _DurableRetentionFailure("active durable path is not a regular file")
        if active_stat is not None and int(active_stat.st_size) > self.max_bytes:
            self.__retention_failed = True
            raise _DurableRetentionFailure("active durable file exceeds retention bound")
        try:
            flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(active_path, flags, 0o600)
        except OSError as error:
            raise _DurableRetentionFailure("active durable file cannot be opened") from error
        try:
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                else:
                    os.chmod(active_path, 0o600)
                verified_mode = stat.S_IMODE(
                    os.stat(active_path, follow_symlinks=False).st_mode,
                )
                if os.name != "nt" and verified_mode != 0o600:
                    raise PermissionError("active durable file permissions are not private")
            except OSError as error:
                raise PermissionError(
                    "active durable file permissions cannot be enforced",
                ) from error
            self.__file_handle = os.fdopen(descriptor, "ab", buffering=0)
            descriptor = -1
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self.__file_path = active_path
        try:
            active_size = os.stat(active_path, follow_symlinks=False).st_size
            if active_size > self.max_bytes:
                self.__close_file()
                self.__retention_failed = True
                raise _DurableRetentionFailure("active durable file exceeds retention bound")
            self.__file_size = int(active_size)
        except _DurableRetentionFailure:
            raise
        except OSError as error:
            self.__close_file()
            raise _DurableRetentionFailure("active durable file cannot be inspected") from error

    def __next_available_file(self, prefix: str) -> str:
        """Return one bounded, collision-safe app-owned output name."""
        for _attempt in range(_DURABLE_RETENTION_SCAN_LIMIT):
            self.__rotation_index += 1
            candidate = os.path.join(
                self.path,
                "{}-{}-{}.jsonl".format(prefix, time.time_ns(), self.__rotation_index),
            )
            try:
                os.stat(candidate, follow_symlinks=False)
            except FileNotFoundError:
                return candidate
            except OSError as error:
                raise _DurableRetentionFailure("durable output name cannot be inspected") from error
        raise _DurableRetentionFailure("durable output name collision limit reached")

    def __rotate_active(self, active_path: str) -> None:
        if self.__retention_failed or self.__active_file_disabled:
            raise _DurableRetentionFailure("durable rotation is disabled")
        try:
            active_stat = os.stat(active_path, follow_symlinks=False)
        except (FileNotFoundError, OSError) as error:
            raise _DurableRetentionFailure("active durable file cannot be rotated") from error
        if not stat.S_ISREG(active_stat.st_mode) or int(active_stat.st_size) > self.max_bytes:
            self.__retention_failed = True
            raise _DurableRetentionFailure("active durable file is not retainable")
        self.__close_file()
        rotated = self.__next_available_file("breadcrumbs")
        try:
            os.replace(active_path, rotated)
        except OSError as error:
            raise _DurableRetentionFailure("durable active file cannot be rotated") from error
        with self.__lock:
            self.__health["rotation_count"] += 1
        if not self.__prune_rotated_files():
            self.__active_file_disabled = True
            raise _DurableRetentionFailure("durable rotation retention failed")

    def __prune_rotated_files(self) -> bool:
        """Retain only validated, app-named rotated files within both bounds."""
        try:
            entries: List[tuple[int, int, str, int]] = []
            matched_count = 0
            scanned_count = 0
            with os.scandir(self.path) as directory:
                for entry in directory:
                    scanned_count += 1
                    if scanned_count > _DURABLE_RETENTION_SCAN_LIMIT:
                        # A hostile directory may contain arbitrary unrelated
                        # names. Bound inspection work as well as retained
                        # candidate materialization and fail closed.
                        raise _DurableRetentionFailure("durable retention scan limit reached")
                    match = _DURABLE_ROTATED_NAME.match(entry.name)
                    if match is None:
                        match = _DURABLE_QUARANTINE_NAME.match(entry.name)
                    if match is None:
                        continue
                    matched_count += 1
                    if matched_count > _DURABLE_RETENTION_SCAN_LIMIT:
                        # Never materialize or inspect an attacker-sized
                        # directory. Fail closed before any new rotation.
                        raise _DurableRetentionFailure("durable retention scan limit reached")
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            raise _DurableRetentionFailure("durable retained path is not a regular file")
                        size = int(entry.stat(follow_symlinks=False).st_size)
                    except OSError:
                        # An owned-looking file that cannot be inspected must
                        # not be ignored: doing so could let retention grow.
                        raise
                    entries.append((int(match.group(1)), int(match.group(2)), entry.name, size))
            entries.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
            try:
                active_stat = os.stat(os.path.join(self.path, "breadcrumbs.jsonl"), follow_symlinks=False)
            except FileNotFoundError:
                active_stat = None
            except OSError:
                raise
            if active_stat is not None:
                if not stat.S_ISREG(active_stat.st_mode):
                    raise _DurableRetentionFailure("active durable path is not a regular file")
                active_size = int(active_stat.st_size)
                if active_size > self.max_bytes:
                    raise _DurableRetentionFailure("active durable file exceeds retention bound")
            else:
                active_size = 0
            retained_bytes = active_size
            # Reserve one slot for the active file even while it is absent
            # during rotation/startup; this keeps the fixed file cap stable
            # across the rollover boundary.
            retained_count = 1
            remove: List[str] = []
            for _timestamp, _index, name, size in entries:
                if (
                    retained_count < self.max_files
                    and retained_bytes + size <= self.max_bytes
                ):
                    retained_count += 1
                    retained_bytes += size
                else:
                    remove.append(name)
            for name in remove:
                # unlink never follows a symlink; the strict name/type check
                # above prevents unrelated files from entering the set.
                os.unlink(os.path.join(self.path, name))
            if remove:
                with self.__lock:
                    self.__health["retention_prune_count"] += len(remove)
            return True
        except Exception:
            self.__retention_failed = True
            with self.__lock:
                self.__health["retention_prune_failures"] += 1
                self.__health["last_error_class"] = "retention_failure"
            return False

    def __write_batch(self, batch: List[tuple[Dict[str, Any], bool, int]]) -> None:
        written_count = 0
        success_accounted = False
        batch_start_size: Optional[int] = None
        flush_failed = False
        try:
            # Encoding and byte measurement are writer-owned. The batch is
            # already bounded by the parent-local queue and record budget.
            with self.__lock:
                if not self.__session_boundary_written:
                    raise _DurableRetentionFailure(
                        "durable session boundary is not committed",
                    )
            encoded = []
            for record, _critical, _estimated_bytes in batch:
                # Bind every durable record to this writer session. The
                # independent boundary is committed by ``__run`` before this
                # method can write any ordinary record.
                payload_record = dict(record)
                payload_record["session_id"] = self.__session_id
                encoded.append((json.dumps(
                    payload_record, separators=(",", ":"), ensure_ascii=False,
                    allow_nan=False,
                ) + "\n").encode("utf-8"))
            self.__open_file(sum(len(item) for item in encoded))
            if self.__file_handle is None:
                raise OSError("durable breadcrumb file is not open")
            batch_start_size = self.__file_size
            for item in encoded:
                item_start = self.__file_size
                try:
                    written = self.__file_handle.write(item)
                    if written is None:
                        written = len(item)
                    if written != len(item):
                        with self.__lock:
                            self.__health["partial_write_failures"] += 1
                        raise _DurableShortWrite("short durable breadcrumb write")
                except Exception as write_error:
                    # A write may have raised after writing a prefix. Always
                    # return to this item's boundary before closing the file;
                    # otherwise the next writer could create malformed JSONL.
                    if not isinstance(write_error, _DurableShortWrite):
                        with self.__lock:
                            self.__health["partial_write_failures"] += 1
                    if not self.__rollback_file(item_start):
                        if not self.__quarantine_active_file():
                            raise _DurableQuarantineFailure(
                                "durable active file quarantine failed"
                            )
                        raise _DurableRollbackFailure(
                            "durable write rollback failed"
                        )
                    raise
                self.__file_size += int(written)
                written_count += 1
            try:
                self.__file_handle.flush()
                # A successful durable acknowledgement includes both the
                # file data and the directory entry.  On Windows the helper
                # records an explicit buffered-best-effort capability label;
                # it never lets the health projection claim fsync strength.
                os.fsync(self.__file_handle.fileno())
                self.__fsync_directory()
            except Exception:
                flush_failed = True
                written_count = 0
                # Remove the unflushed batch when possible. This keeps the
                # JSONL output and written/lost accounting truthful.
                if batch_start_size is not None and not self.__rollback_file(batch_start_size):
                    if not self.__quarantine_active_file():
                        raise _DurableQuarantineFailure(
                            "durable active file quarantine failed"
                        )
                    raise _DurableRollbackFailure(
                        "durable flush rollback failed"
                    )
                raise
            # The bytes and flush have succeeded. Account them before the
            # retention check so a later prune failure cannot rewrite a true
            # successful flush as data loss.
            with self.__lock:
                self.__health["written"] += len(batch)
                self.__health["flush_count"] += 1
                self.__health["last_error_class"] = None
            written_count = len(batch)
            success_accounted = True
            # Keep the active file in the same fixed total-byte bound as the
            # retained rotations. A prune failure disables further output
            # after this already-flushed batch rather than allowing growth.
            if not self.__prune_rotated_files():
                self.__active_file_disabled = True
                raise _DurableRetentionFailure("durable retention failed after write")
            self.__publish_health_if_due()
        except Exception as error:
            self.__close_file()
            with self.__lock:
                # Storage/serialization failure is latched for this owner;
                # retrying an unusable path would create unbounded loss and
                # repeated writer churn. Re-enable creates a fresh owner.
                self.__active_file_disabled = True
                self.__health["writer_failures"] += 1
                if flush_failed:
                    self.__health["flush_failures"] += 1
                self.__health["last_error_class"] = _durable_error_class(error, flush_failed)
                if not success_accounted:
                    self.__health["written"] += written_count
                    lost = len(batch) - written_count
                    self.__health["lost"] += lost
                    self.__health["critical_lost"] += sum(
                        1 for _record, critical, _estimated_bytes in batch[written_count:] if critical
                    )
                    self.__health["normal_lost"] += sum(
                        1 for _record, critical, _estimated_bytes in batch[written_count:] if not critical
                    )
            self.__publish_health()

    def __rollback_file(self, item_start: int) -> bool:
        handle = self.__file_handle
        if handle is None:
            return False
        try:
            handle.seek(item_start)
            handle.truncate()
            self.__file_size = item_start
            return True
        except Exception:
            return False

    def __quarantine_active_file(self) -> bool:
        """Disable the active path after an unrecoverable write rollback."""
        active_path = self.__file_path
        self.__active_file_disabled = True
        self.__close_file()
        renamed = False
        if active_path is not None:
            try:
                active_stat = os.stat(active_path, follow_symlinks=False)
                if stat.S_ISREG(active_stat.st_mode):
                    quarantine_path = self.__next_available_file("breadcrumbs-quarantine")
                    os.replace(active_path, quarantine_path)
                    renamed = True
            except Exception:
                # Disabling the active writer is the safety property. A failed
                # rename leaves no path available for continuation.
                pass
        with self.__lock:
            if renamed:
                self.__health["active_file_quarantined"] += 1
                self.__health["last_error_class"] = "active_file_quarantined"
            else:
                self.__health["active_file_quarantine_failures"] += 1
                self.__health["last_error_class"] = "active_file_quarantine_failure"
        if renamed and not self.__prune_rotated_files():
            # The quarantine is already detached. Retention failure keeps the
            # writer disabled and remains represented by fixed health fields.
            self.__active_file_disabled = True
        return renamed

    def __close_file(self) -> None:
        handle = self.__file_handle
        self.__file_handle = None
        self.__file_path = None
        self.__file_size = 0
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def __publish_health(self, *, sync: bool = True) -> bool:
        try:
            self.__ensure_directory()
            # Windows chmod cannot establish or verify ACL privacy. Keep the
            # JSONL writer available as explicitly buffered best effort, but
            # fail closed for the health/manifest sidecar so no unverified
            # private-health claim can be published.
            if os.name == "nt":
                with self.__lock:
                    if self.__health.get("health_publish_ok", True):
                        self.__health["last_error_class"] = "health_publish_failure"
                        self.__health["health_publish_failures"] += 1
                    self.__health["health_publish_ok"] = False
                    self.__health["shutdown_incomplete"] = True
                    self.__health["closed"] = False
                    self.__health["terminal"] = False
                    self.__health["incomplete"] = True
                    self.__health["state"] = "INCOMPLETE"
                    self.__health["offline_retrieval_invalidated"] = False
                    self.__health["offline_retrieval"] = "unsupported"
                    self.__shutdown_incomplete = True
                return False
            marker_sticky = False
            if not sync:
                # A buffered sidecar can regress to an older terminal file
                # after a crash. Establish the fsynced invalidation marker
                # first and leave it in place until a later sync=True
                # publication proves the sidecar is current.
                marker_sticky = self.__write_health_invalidation_marker(sync=True)
                if not marker_sticky:
                    raise OSError("health invalidation marker unavailable")
            with self.__lock:
                # Publish the same optimistic health projection that the
                # sidecar will carry; failures below roll it back to false.
                boundary_committed = self.__session_boundary_written
                self.__health["health_publish_ok"] = True
                self.__health["offline_retrieval_invalidated"] = marker_sticky
                self.__health["offline_retrieval"] = (
                    "invalidated"
                    if marker_sticky
                    else "valid" if boundary_committed else "unavailable"
                )
                if not sync or os.name == "nt":
                    self.__health["durability"] = "buffered_best_effort"
                payload = copy.deepcopy(self.__health)
                payload["critical_pending"] = self.__critical_records.qsize()
                payload["normal_pending"] = self.__normal_records.qsize()
                payload["queued_bytes"] = self.__queued_bytes
                payload["inflight_bytes"] = self.__inflight_bytes
                payload["writing"] = self.__writing
                payload["updated_ms"] = int(time.time_ns() / 1_000_000)
            if self.__upstream_health_getter is not None:
                try:
                    upstream = self.__upstream_health_getter()
                    if isinstance(upstream, Mapping):
                        for key, value in upstream.items():
                            if key in _DURABLE_UPSTREAM_HEALTH_KEYS and isinstance(value, int) and not isinstance(value, bool):
                                bounded_value = max(0, value)
                                payload["upstream_" + key] = bounded_value
                                if key == "ingress_reservation_unknown":
                                    payload["unknown"] = max(payload.get("unknown", 0), bounded_value)
                                elif key == "ingress_unresolved":
                                    payload["unresolved"] = max(payload.get("unresolved", 0), bounded_value)
                except Exception:
                    payload["upstream_health_unavailable"] = 1
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            descriptor, temporary_path = tempfile.mkstemp(prefix="breadcrumbs.health.", suffix=".tmp", dir=self.path)
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                else:
                    os.chmod(temporary_path, 0o600)
                temporary_stat = os.stat(temporary_path, follow_symlinks=False)
                if not stat.S_ISREG(temporary_stat.st_mode):
                    raise PermissionError("health sidecar temporary path is not a regular file")
                # Windows' chmod only exposes a read-only compatibility bit;
                # ACL privacy is not verifiable here. The health projection
                # reports ``windows_acl_unverified`` instead of claiming the
                # POSIX private-file guarantee.
                if os.name != "nt" and stat.S_IMODE(temporary_stat.st_mode) != 0o600:
                    raise PermissionError("health sidecar permissions are not private")
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = -1
                    written = handle.write(encoded)
                    if written is not None and written != len(encoded):
                        raise OSError("short health sidecar write")
                    handle.flush()
                    if sync:
                        os.fsync(handle.fileno())
                os.replace(temporary_path, os.path.join(self.path, _DURABLE_HEALTH_NAME))
                temporary_path = None
                if sync:
                    self.__fsync_directory()
                marker_cleared = self.__clear_health_invalidation_marker(sync=sync)
            finally:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                if temporary_path is not None and os.path.exists(temporary_path):
                    try:
                        os.unlink(temporary_path)
                    except OSError:
                        pass
            with self.__lock:
                boundary_committed = self.__session_boundary_written
                self.__health["health_publish_ok"] = True
                self.__health["offline_retrieval_invalidated"] = not marker_cleared
                self.__health["offline_retrieval"] = (
                    "valid"
                    if marker_cleared and boundary_committed
                    else "invalidated" if not marker_cleared else "unavailable"
                )
            return True
        except Exception:
            # Health publication is intentionally isolated and never emits a
            # breadcrumb, logs, or feeds back into the diagnostic queues.
            marker_written = self.__write_health_invalidation_marker(sync=sync)
            with self.__lock:
                self.__health["last_error_class"] = "health_publish_failure"
                self.__health["health_publish_failures"] += 1
                self.__health["health_publish_ok"] = False
                self.__health["shutdown_incomplete"] = True
                self.__health["closed"] = False
                self.__health["terminal"] = False
                self.__health["incomplete"] = True
                self.__health["state"] = "INCOMPLETE"
                self.__health["offline_retrieval_invalidated"] = marker_written
                self.__health["offline_retrieval"] = (
                    "invalidated"
                    if marker_written
                    else "unavailable"
                )
                self.__shutdown_incomplete = True
            return False

    def snapshot(self) -> Dict[str, Any]:
        with self.__lock:
            payload = copy.deepcopy(self.__health)
        payload["critical_pending"] = self.__critical_records.qsize()
        payload["normal_pending"] = self.__normal_records.qsize()
        with self.__lock:
            payload["queued_bytes"] = self.__queued_bytes
            payload["inflight_bytes"] = self.__inflight_bytes
            payload["writing"] = self.__writing
        if self.__upstream_health_getter is not None:
            try:
                upstream = self.__upstream_health_getter()
                if isinstance(upstream, Mapping):
                    for key, value in upstream.items():
                        if key in _DURABLE_UPSTREAM_HEALTH_KEYS and isinstance(value, int) and not isinstance(value, bool):
                            bounded_value = max(0, value)
                            payload["upstream_" + key] = bounded_value
                            if key == "ingress_reservation_unknown":
                                payload["unknown"] = max(payload.get("unknown", 0), bounded_value)
                            elif key == "ingress_unresolved":
                                payload["unresolved"] = max(payload.get("unresolved", 0), bounded_value)
            except Exception:
                payload["upstream_health_unavailable"] = 1
        payload["thread_alive"] = self.__thread.is_alive()
        return payload

    def mark_incomplete(self, unresolved: int = 0, unknown: int = 0) -> Dict[str, Any]:
        """Mark timeout state and request writer-owned health publication.

        This caller-side path is deliberately filesystem-free.  The writer
        may publish the updated projection after it regains ownership; until
        then the in-memory/API view remains explicitly unavailable offline.
        """
        with self.__lock:
            self.__shutdown_incomplete = True
            self.__health["closed"] = False
            self.__health["enabled"] = False
            self.__health["terminal"] = False
            self.__health["incomplete"] = True
            self.__health["shutdown_incomplete"] = True
            self.__health["state"] = "INCOMPLETE"
            self.__health["health_publish_ok"] = False
            self.__health["offline_retrieval"] = (
                "unsupported" if os.name == "nt" else "unavailable"
            )
            # Let the existing writer own any later sidecar/marker publication
            # without allowing this timeout caller to perform filesystem I/O.
            self.__next_health_publish_at = 0.0
            unresolved_count = max(0, int(unresolved))
            unresolved_count = max(0, unresolved_count - self.__unresolved_marked)
            self.__unresolved_marked += unresolved_count
            if unresolved_count:
                self.__health["unresolved"] += unresolved_count
            unknown_count = max(0, int(unknown))
            unknown_count = max(0, unknown_count - self.__unknown_marked)
            self.__unknown_marked += unknown_count
            if unknown_count:
                # Unknown reservations are not proven loss. Keep them
                # visible in the non-terminal projection until reconciliation.
                self.__health["unknown"] += unknown_count
        self.__wake.set()
        return self.snapshot()

    def close(
        self, timeout: Optional[float] = None, *, final_state: str = "TERMINAL",
    ) -> Dict[str, Any]:
        wait_for = self.shutdown_timeout_seconds if timeout is None else max(0.0, float(timeout))
        already_closed = False
        if final_state not in {"DISABLED", "TERMINAL"}:
            final_state = "TERMINAL"
        with self.__lock:
            if self.__closed:
                already_closed = True
            else:
                self.__terminal_state = final_state
                self.__health["state"] = "DRAINING"
                self.__health["closed"] = False
                if final_state == "DISABLED":
                    self.__health["enabled"] = False
                self.__health["terminal"] = False
                # Admission and the stop transition share one lock. Once
                # closing is visible, no producer can turn a record into an
                # accepted item after the writer has observed its terminal
                # queue state.
                if not self.__closing:
                    self.__closing = True
                    self.__stop.set()
                    self.__wake.set()
        if already_closed:
            return self.snapshot()
        self.__thread.join(wait_for)
        with self.__lock:
            if not self.__thread.is_alive():
                self.__closed = True
                if self.__shutdown_incomplete:
                    self.__health["closed"] = False
                    self.__health["terminal"] = False
                    self.__health["incomplete"] = True
                    self.__health["enabled"] = False
                    self.__health["state"] = "INCOMPLETE"
            # If the writer misses the deadline, ownership stays with its
            # thread and queued records remain represented as pending. A
            # producer never races it by draining or closing these queues.
            writer_alive = self.__thread.is_alive()
        if writer_alive:
            return self.mark_incomplete()
        if self.__shutdown_incomplete:
            self.__publish_health()
        return self.snapshot()


class _EffectiveBreadcrumbPolicy:
    """Immutable, read-only policy projection used on breadcrumb hot paths."""

    def __init__(self, default: str, rules: Iterable[tuple[str, str]]):
        self.__default = default
        self.__rules = tuple(rules)
        self.__resolved_categories: Dict[str, str] = {}

    @classmethod
    def from_policy(cls, policy: Mapping[str, object]) -> "_EffectiveBreadcrumbPolicy":
        rules = policy.get("rules", {})
        return cls(
            str(policy.get("default", DEFAULT_BREADCRUMB_POLICY_LEVEL)),
            tuple((str(category), str(level)) for category, level in rules.items())
            if isinstance(rules, Mapping) else (),
        )

    def allows(self, category: object, level: object) -> bool:
        if not isinstance(category, str):
            category = "<{}>".format(type(category).__name__[:48])
        else:
            category = _bounded_redaction_text(category, 256)
        if not isinstance(level, str):
            level = "info"
        normalized_level = level.strip().lower()
        event_rank = _POLICY_LEVEL_RANK.get(normalized_level, _POLICY_LEVEL_RANK["info"])
        configured_level = self.__resolved_categories.get(category)
        if configured_level is None:
            configured_level = self.__resolve(category)
            if len(self.__resolved_categories) >= _EFFECTIVE_POLICY_CATEGORY_CACHE_MAX:
                self.__resolved_categories.clear()
            self.__resolved_categories[category] = configured_level
        if normalized_level == "off" or configured_level == "off":
            return False
        configured_rank = _POLICY_LEVEL_RANK.get(configured_level, _POLICY_LEVEL_RANK["info"])
        return event_rank <= configured_rank

    def has_explicit_rule(self, category: object) -> bool:
        """Return whether a category is covered by a configured rule.

        This deliberately differs from :meth:`allows`: callers use it for
        unusually high-volume diagnostics that must never inherit the policy
        default merely because tracing is enabled elsewhere.
        """
        if not isinstance(category, str):
            category = "<{}>".format(type(category).__name__[:48])
        else:
            category = _bounded_redaction_text(category, 256)
        category_parts = category.split(".") if category else [""]
        return any(
            BreadcrumbTraceCollector._BreadcrumbTraceCollector__category_match_score(
                pattern, category, category_parts,
            ) >= 0
            for pattern, _level in self.__rules
        )

    def __resolve(self, category: str) -> str:
        category_parts = category.split(".") if category else [""]
        selected = self.__default
        best_score = -1
        for pattern, level in self.__rules:
            score = BreadcrumbTraceCollector._BreadcrumbTraceCollector__category_match_score(
                pattern, category, category_parts,
            )
            if score > best_score:
                best_score = score
                selected = level
        return selected


def opaque_trace_correlation(identity: object) -> str:
    """Return a short process-local opaque correlation for a canonical identity."""
    if not isinstance(identity, str):
        return "unknown"
    return hashlib.blake2s(
        identity.encode("utf-8"), key=_OPAQUE_TRACE_CORRELATION_KEY, digest_size=8,
    ).hexdigest()


def trace_session_digest(session_token: object) -> str:
    """Return a non-identifying digest for a scanner session token."""
    if not isinstance(session_token, str):
        return "unknown"
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()[:12]


class _EnabledGate(Protocol):
    value: object


class BreadcrumbTraceEmitter:
    def __init__(
        self,
        record_queue: multiprocessing.Queue[object],
        enabled_gate: _EnabledGate,
        policy_revision: multiprocessing.Value,
        policy_epoch: multiprocessing.Value,
        policy_generation: multiprocessing.RawValue,
        policy_length: multiprocessing.RawValue,
        policy_bytes: multiprocessing.RawArray,
        rejected_count: multiprocessing.Value,
        root_progress_enqueued_count: multiprocessing.Value,
        root_progress_rejected_count: multiprocessing.Value,
        root_progress_disabled_count: multiprocessing.Value,
        root_progress_deduplicated_count: multiprocessing.Value,
        root_progress_summary_count: multiprocessing.Value,
        critical_record_queue: Optional[multiprocessing.Queue[object]] = None,
        critical_rejected_count: Optional[multiprocessing.Value] = None,
        normal_rejected_count: Optional[multiprocessing.Value] = None,
        critical_enqueued_count: Optional[multiprocessing.Value] = None,
        normal_enqueued_count: Optional[multiprocessing.Value] = None,
        closed_gate: Optional[_EnabledGate] = None,
        admission_lock: Optional[Any] = None,
        ingress_inflight_count: Optional[multiprocessing.Value] = None,
        critical_reservation_count: Optional[multiprocessing.Value] = None,
        normal_reservation_count: Optional[multiprocessing.Value] = None,
        critical_reservation_unknown_count: Optional[multiprocessing.Value] = None,
        normal_reservation_unknown_count: Optional[multiprocessing.Value] = None,
        durable_reconcile_gate: Optional[_EnabledGate] = None,
    ):
        # ``record_queue`` remains the positional normal-lane compatibility
        # argument for old child constructors. New collectors pass a distinct
        # critical lane so normal pressure cannot consume its reservation.
        self.__record_queue = record_queue
        self.__critical_record_queue = critical_record_queue or record_queue
        self.__enabled_gate = enabled_gate
        self.__policy_revision = policy_revision
        self.__policy_epoch = policy_epoch
        self.__policy_generation = policy_generation
        self.__policy_length = policy_length
        self.__policy_bytes = policy_bytes
        self.__rejected_count = rejected_count
        self.__root_progress_enqueued_count = root_progress_enqueued_count
        self.__root_progress_rejected_count = root_progress_rejected_count
        self.__root_progress_disabled_count = root_progress_disabled_count
        self.__root_progress_deduplicated_count = root_progress_deduplicated_count
        self.__root_progress_summary_count = root_progress_summary_count
        self.__critical_rejected_count = critical_rejected_count
        self.__normal_rejected_count = normal_rejected_count
        self.__critical_enqueued_count = critical_enqueued_count
        self.__normal_enqueued_count = normal_enqueued_count
        self.__closed_gate = closed_gate
        self.__admission_lock = admission_lock
        # One common admission token covers both regular and root-health
        # emitter calls.  The collector closes the shared gate, waits for all
        # pre-gate tokens, then folds their counters exactly once.
        self.__ingress_inflight_count = ingress_inflight_count
        self.__critical_reservation_count = critical_reservation_count
        self.__normal_reservation_count = normal_reservation_count
        self.__critical_reservation_unknown_count = critical_reservation_unknown_count
        self.__normal_reservation_unknown_count = normal_reservation_unknown_count
        self.__durable_reconcile_gate = durable_reconcile_gate
        self.__effective_policy: Optional[_EffectiveBreadcrumbPolicy] = None
        self.__effective_policy_generation = -1

    def is_enabled(self) -> bool:
        """Return the shared opt-in state without allocating a breadcrumb.

        Transfer/model hot paths use this cheap gate before doing any diagnostic
        work (for example, filesystem stats).  The gate is shared with the
        collector so a settings change takes effect without restarting workers.
        """
        try:
            return bool(self.__enabled_gate.value)
        except Exception:
            return False

    def is_effectively_enabled(self, category: object, level: object = "info") -> bool:
        """Return the system/category/level gate without queueing or sanitizing."""
        if not self.is_enabled():
            return False
        policy = self.__current_effective_policy()
        return policy is not None and policy.allows(category, level)

    def is_explicitly_configured(self, category: object) -> bool:
        """Return whether ``category`` has an explicit policy rule."""
        if not self.is_enabled():
            return False
        policy = self.__current_effective_policy()
        return policy is not None and policy.has_explicit_rule(category)

    def record(self, source: str, message: str, details: object = None, **metadata: Any) -> str:
        admitted, disposition = self.__begin_ingress_token()
        if not admitted:
            # A lock/cap rejection while OPEN is a real bounded loss and must
            # be visible. Once CLOSING is published the call is an inert
            # no-op, so it cannot mutate terminal accounting.
            rejection_admitted = (
                disposition == _ADMISSION_REJECTED_OPEN
                and self.__begin_rejection_token()
            )
            if rejection_admitted:
                try:
                    if self.__ingress_state_is_open() and self.is_enabled():
                        critical = is_critical_breadcrumb(source, message, details, metadata)
                        self.__reject(critical)
                        self.__root_progress_reject_if_applicable(
                            metadata.get("category", source), metadata.get("_root_progress"),
                        )
                finally:
                    self.__end_ingress_token()
            # The helper's disposition is only considered a loss candidate;
            # rejection token is the final linearized disposition; if close
            # won before that token was acquired, the caller is inert rather
            # than reporting a stale dropped result.
            return "dropped" if rejection_admitted else "disabled"
        try:
            return self.__record_internal(source, message, details, **metadata)
        finally:
            self.__end_ingress_token()

    def __record_internal(self, source: str, message: str, details: object = None, **metadata: Any) -> str:
        if not self.is_enabled():
            return "disabled"
        if not self.is_effectively_enabled(metadata.get("category", source), metadata.get("level", "info")):
            return "dropped"
        created_ns = time.time_ns()
        record = _bounded_ingress_record(source, message, details, metadata, created_ns, self.__policy_revision, self.__policy_epoch)
        if record is None:
            self.__reject(is_critical_breadcrumb(source, message, details, metadata))
            self.__root_progress_reject_if_applicable(
                metadata.get("category", source), metadata.get("_root_progress"),
            )
            return "dropped"
        critical = is_critical_breadcrumb(source, message, details, metadata)
        admission_lock = self.__admission_lock
        acquired = False
        reservation_counter = (
            self.__critical_reservation_count
            if critical else self.__normal_reservation_count
        )
        unknown_counter = (
            self.__critical_reservation_unknown_count
            if critical else self.__normal_reservation_unknown_count
        )
        reservation_held = False
        try:
            if admission_lock is not None:
                # Producer admission is never allowed to wait behind close.
                acquired = bool(admission_lock.acquire(False))
                if not acquired:
                    self.__reject(critical)
                    self.__root_progress_reject_if_applicable(
                        metadata.get("category", source), metadata.get("_root_progress"),
                    )
                    return "dropped"
            if self.__closed_gate is not None and int(self.__closed_gate.value) != _BREADCRUMB_LIFECYCLE_OPEN:
                self.__reject(critical)
                self.__root_progress_reject_if_applicable(
                    metadata.get("category", source), metadata.get("_root_progress"),
                )
                return "dropped"
            if reservation_counter is not None:
                try:
                    # Reserve before Queue.put_nowait.  If this process is
                    # killed at the boundary, close-time accounting sees the
                    # reservation as unresolved instead of losing the record.
                    with reservation_counter.get_lock():
                        reservation_counter.value += 1
                    reservation_held = True
                except Exception:
                    self.__reject(critical)
                    self.__root_progress_reject_if_applicable(
                        metadata.get("category", source), metadata.get("_root_progress"),
                    )
                    return "dropped"
            try:
                (self.__critical_record_queue if critical else self.__record_queue).put_nowait(record)
            except (queue.Full, OSError, ValueError):
                if reservation_held:
                    self.__release_reservation(reservation_counter, unknown_counter)
                    reservation_held = False
                raise
            self.__root_progress_increment_if_applicable(
                metadata.get("category", source), metadata.get("_root_progress"),
                self.__root_progress_enqueued_count,
            )
            lane_counter = self.__critical_enqueued_count if critical else self.__normal_enqueued_count
            if lane_counter is not None:
                if not self.__increment_shared_counter(lane_counter):
                    self.__increment_shared_counter(unknown_counter)
            if reservation_held:
                self.__release_reservation(reservation_counter, unknown_counter)
                reservation_held = False
            # This only acknowledges process-queue admission. The collector
            # remains authoritative for retention, policy, and deduplication.
            return "enqueued"
        except (queue.Full, OSError, ValueError):
            self.__reject(critical)
            self.__root_progress_reject_if_applicable(
                metadata.get("category", source), metadata.get("_root_progress"),
            )
            return "dropped"
        finally:
            if reservation_held:
                self.__release_reservation(reservation_counter, unknown_counter)
            if acquired:
                try:
                    admission_lock.release()
                except Exception:
                    pass

    @staticmethod
    def __increment_shared_counter(counter: Optional[multiprocessing.Value]) -> bool:
        if counter is None:
            return False
        try:
            with counter.get_lock():
                counter.value += 1
            return True
        except Exception:
            # A counter failure must remain visible to the collector rather
            # than being swallowed as if the transition had settled.
            try:
                counter.value = int(counter.value) + 1
                return True
            except Exception:
                return False

    @classmethod
    def __release_reservation(
            cls,
            counter: Optional[multiprocessing.Value],
            unknown_counter: Optional[multiprocessing.Value] = None,
    ) -> bool:
        if counter is None:
            return True
        try:
            with counter.get_lock():
                counter.value = max(0, int(counter.value) - 1)
            return True
        except Exception:
            # Preserve UNKNOWN separately; callers must not reinterpret a
            # failed RESERVED -> ACKED settlement as proven loss.
            cls.__increment_shared_counter(unknown_counter)
            return False

    def __begin_ingress_token(self) -> tuple[bool, str]:
        """Atomically admit a bounded pre-gate call or return its state.

        The second result is the final disposition at this admission point,
        not a stale caller-side copy of the lifecycle bit. A rejected OPEN
        call may account bounded loss; CLOSING/CLOSED is inert and performs no
        rejection, loss, version, gap, or spool mutation.
        """
        gate = self.__closed_gate
        admission_lock = self.__admission_lock
        if gate is None or admission_lock is None:
            return True, _ADMISSION_ACCEPTED
        acquired = False
        try:
            acquired = bool(admission_lock.acquire(False))
        except Exception:
            acquired = False
        if not acquired:
            try:
                return False, (
                    _ADMISSION_REJECTED_OPEN
                    if int(gate.value) == _BREADCRUMB_LIFECYCLE_OPEN
                    else _ADMISSION_INERT
                )
            except Exception:
                return False, _ADMISSION_INERT
        try:
            try:
                if int(gate.value) != _BREADCRUMB_LIFECYCLE_OPEN:
                    return False, _ADMISSION_INERT
            except Exception:
                return False, _ADMISSION_INERT
            reconcile_gate = self.__durable_reconcile_gate
            if reconcile_gate is not None:
                try:
                    if bool(reconcile_gate.value):
                        return False, _ADMISSION_REJECTED_OPEN
                except Exception:
                    return False, _ADMISSION_REJECTED_OPEN
            counter = self.__ingress_inflight_count
            if counter is None:
                return True, _ADMISSION_ACCEPTED
            try:
                with counter.get_lock():
                    if int(counter.value) >= _BREADCRUMB_INGRESS_INFLIGHT_CAPACITY:
                        return False, _ADMISSION_REJECTED_OPEN
                    counter.value += 1
            except Exception:
                return False, _ADMISSION_REJECTED_OPEN
            return True, _ADMISSION_ACCEPTED
        finally:
            try:
                admission_lock.release()
            except Exception:
                pass

    def __ingress_state_is_open(self) -> bool:
        try:
            return self.__closed_gate is None or int(self.__closed_gate.value) == _BREADCRUMB_LIFECYCLE_OPEN
        except Exception:
            return False

    def __begin_rejection_token(self) -> bool:
        """Hold a short shared token while an OPEN rejection is accounted."""
        if not self.__ingress_state_is_open():
            return False
        counter = self.__ingress_inflight_count
        if counter is None:
            return True
        try:
            with counter.get_lock():
                counter.value += 1
            if self.__ingress_state_is_open():
                return True
            self.__end_ingress_token()
            return False
        except Exception:
            try:
                counter.value = int(counter.value) + 1
                if self.__ingress_state_is_open():
                    return True
                self.__end_ingress_token()
                return False
            except Exception:
                return False

    def __end_ingress_token(self) -> None:
        counter = self.__ingress_inflight_count
        if counter is None:
            return
        try:
            with counter.get_lock():
                counter.value = max(0, int(counter.value) - 1)
        except Exception:
            pass

    def record_root_progress_health(
            self, outcome: object, stage: object, job_digest: object = None,
            details: object = None, count_attempt: bool = True,
    ) -> bool:
        """Forward supported bounded health outcomes across the process boundary.

        Only policy-disabled, tracer-deduplicated, and summary observations
        have shared counters; collector-owned outcomes are recorded locally.
        """
        del stage, job_digest, details, count_attempt
        admitted, disposition = self.__begin_ingress_token()
        if not admitted:
            rejection_admitted = (
                disposition == _ADMISSION_REJECTED_OPEN
                and self.__begin_rejection_token()
            )
            if rejection_admitted:
                try:
                    if self.__ingress_state_is_open() and self.is_enabled():
                        self.__reject(False)
                finally:
                    self.__end_ingress_token()
            return False
        try:
            return self.__record_root_progress_health_internal(outcome)
        finally:
            self.__end_ingress_token()

    def __record_root_progress_health_internal(self, outcome: object) -> bool:
        if type(outcome) is not str:
            return False
        counter = {
            "trace_policy_disabled": self.__root_progress_disabled_count,
            "tracer_deduplicated": self.__root_progress_deduplicated_count,
            "summary_observed": self.__root_progress_summary_count,
        }.get(outcome)
        if counter is None:
            return False
        try:
            with counter.get_lock():
                counter.value += 1
            return True
        except Exception:
            return False

    @staticmethod
    def __is_root_progress(category: object, marker: object) -> bool:
        return category == "model.progress" and marker is True

    @classmethod
    def __root_progress_increment_if_applicable(
            cls, category: object, marker: object, counter: multiprocessing.Value,
    ) -> None:
        if not cls.__is_root_progress(category, marker):
            return
        try:
            with counter.get_lock():
                counter.value += 1
        except Exception:
            pass

    def __root_progress_reject_if_applicable(self, category: object, marker: object) -> None:
        self.__root_progress_increment_if_applicable(
            category, marker, self.__root_progress_rejected_count,
        )

    def __current_effective_policy(self) -> Optional[_EffectiveBreadcrumbPolicy]:
        try:
            generation = int(self.__policy_generation.value)
            if generation == self.__effective_policy_generation:
                return self.__effective_policy
            # An odd generation is a writer-owned publication window. Fail
            # closed rather than reading a partial shared buffer.
            if generation % 2:
                return None
            length = int(self.__policy_length.value)
            if length < 0 or length > _SHARED_POLICY_MAX_BYTES:
                return None
            raw = bytes(self.__policy_bytes[:length])
            if generation != int(self.__policy_generation.value):
                return None
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, Mapping):
                return None
            self.__effective_policy = _EffectiveBreadcrumbPolicy.from_policy(parsed)
            self.__effective_policy_generation = generation
            return self.__effective_policy
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return None

    def __reject(self, critical: bool = False) -> None:
        try:
            with self.__rejected_count.get_lock():
                self.__rejected_count.value += 1
        except Exception:
            pass
        counter = self.__critical_rejected_count if critical else self.__normal_rejected_count
        if counter is not None:
            try:
                with counter.get_lock():
                    counter.value += 1
            except Exception:
                pass


def _bounded_ingress_record(source: object, message: object, details: object,
                            metadata: Mapping[str, Any], created_ns: int,
                            policy_revision: multiprocessing.Value, policy_epoch: multiprocessing.Value) -> Optional[Dict[str, Any]]:
    """Build a pickle-safe, redacted, iterator-bounded child record.

    The producer deliberately does not JSON-serialize or walk an unbounded
    object graph.  The parent writer owns the durable byte measurement.
    """
    node_budget = [_INGRESS_NODE_BUDGET]
    # Account in conservative JSON-byte units without serializing on the
    # producer. Six bytes per retained character covers UTF-8 plus JSON
    # escaping (including control characters), while the fixed reserve covers
    # the canonical record keys, numeric fields, and outer punctuation.
    output_budget = [
        _INGRESS_RECORD_MAX_BYTES - _INGRESS_FIXED_JSON_OVERHEAD - _INGRESS_TAIL_OVERHEAD
    ]

    def charge(characters: int, structural: int = 16) -> bool:
        cost = max(0, int(characters)) * 6 + max(0, int(structural))
        if output_budget[0] >= cost:
            output_budget[0] -= cost
            return True
        else:
            output_budget[0] = 0
            return False

    def truncated() -> str:
        # Keep the accounting bounded even when the last available budget is
        # smaller than the fixed truncation marker. The empty fallback is
        # deliberately tiny; the reserved tail covers all final scalars.
        return "<truncated>" if charge(len("<truncated>"), 24) else ""

    def sanitize(value: object, key: Optional[str] = None, depth: int = 0) -> object:
        if node_budget[0] <= 0 or output_budget[0] <= 16:
            return truncated()
        node_budget[0] -= 1
        if key and _bounded_keyword_match(
                key, ("password", "secret", "token", "auth", "cookie", "session", "api_key"),
        ):
            return "**REDACTED**" if charge(len("**REDACTED**"), 24) else ""
        if depth >= 3:
            return truncated()
        if isinstance(value, str):
            # Redaction sees a bounded prefix and suffix before the final
            # retained-string truncation, so a secret marker at either edge
            # is not lost by an early slice.
            bounded = _bounded_redaction_text(value)
            redacted = (redact_sensitive_text(bounded) or "")[:_INGRESS_STRING_MAX_CHARS]
            return redacted if charge(len(redacted), 24) else ""
        if value is None or isinstance(value, bool):
            return value if charge(0, 24) else None
        if isinstance(value, int):
            if value.bit_length() > 128:
                return truncated()
            numeric_text = str(value)
            return value if charge(len(numeric_text), 16) else 0
        if isinstance(value, float):
            if not math.isfinite(value) or len(repr(value)) > 32:
                return truncated()
            return value if charge(len(repr(value)), 16) else 0.0
        if isinstance(value, Mapping):
            sanitized: Dict[str, Any] = {}
            if not charge(0, 24):  # object braces
                return sanitized
            try:
                items = iter(value.items())
                for _index in range(_INGRESS_MAPPING_MAX_ITEMS):
                    if output_budget[0] <= 96:
                        break
                    try:
                        item_key, item_value = next(items)
                    except StopIteration:
                        break
                    # An oversized or non-string key is not safely
                    # classifiable. Redact its value first, before any
                    # prefix/suffix truncation or keyword scan, and never
                    # allow an unbounded/custom key conversion to decide
                    # whether the value is safe.
                    value_pre_sanitized = False
                    if type(item_key) is str:
                        oversized_key = len(item_key) > 64
                        if oversized_key:
                            sanitized_value = sanitize(
                                item_value, "<oversized_key_secret>", depth + 1,
                            )
                            value_pre_sanitized = True
                        item_key_str = item_key[:64]
                        key_is_sensitive = oversized_key or _bounded_keyword_match(
                            item_key,
                            (
                                "password", "secret", "token", "auth", "cookie",
                                "session", "api_key",
                            ),
                        )
                    else:
                        sanitized_value = sanitize(
                            item_value, "<unclassifiable_key_secret>", depth + 1,
                        )
                        value_pre_sanitized = True
                        item_key_str = "<{}>".format(type(item_key).__name__[:48])
                        key_is_sensitive = True
                    # Mapping keys are JSON strings too; include UTF-8 and
                    # escaping bounds, not only Python character length.
                    charge(len(item_key_str), 24)
                    if not value_pre_sanitized:
                        sanitized_value = sanitize(
                            item_value,
                            item_key_str + ("_secret" if key_is_sensitive else ""),
                            depth + 1,
                        )
                    sanitized[item_key_str] = sanitized_value
            except Exception:
                return truncated()
            return sanitized
        if isinstance(value, (list, tuple)):
            sanitized_list: List[Any] = []
            if not charge(0, 24):  # array brackets
                return sanitized_list
            try:
                items = iter(value)
                for _index in range(_INGRESS_LIST_MAX_ITEMS):
                    if output_budget[0] <= 96:
                        break
                    try:
                        item = next(items)
                    except StopIteration:
                        break
                    sanitized_list.append(sanitize(item, None, depth + 1))
            except Exception:
                return truncated()
            return sanitized_list
        unknown = "<{}>".format(type(value).__name__[:48])
        return unknown if charge(len(unknown), 24) else ""

    try:
        revision = int(policy_revision.value)
        epoch = int(policy_epoch.value)
    except Exception:
        revision, epoch = 0, 0
    revision = max(0, min(2 ** 63 - 1, revision))
    epoch = max(0, min(2 ** 63 - 1, epoch))
    try:
        bounded_created_ns = int(created_ns)
    except Exception:
        bounded_created_ns = 0
    bounded_created_ns = max(-(2 ** 63), min(2 ** 63 - 1, bounded_created_ns))
    bounded_created_ms = int(bounded_created_ns / 1_000_000)
    try:
        record = {
            "source": sanitize(source), "message": sanitize(message),
            "details": sanitize(details), "metadata": sanitize(metadata),
            "created_ns": bounded_created_ns, "created_ms": bounded_created_ms,
            "policy_revision": revision,
            "policy_epoch": epoch,
        }
    except Exception:
        return None
    if not isinstance(record["source"], str) or not isinstance(record["message"], str):
        return None
    return record


class BreadcrumbTraceNoopEmitter:
    def is_enabled(self) -> bool:
        return False

    def is_effectively_enabled(self, category: object, level: object = "info") -> bool:
        return False

    def is_explicitly_configured(self, category: object) -> bool:
        return False

    def record(self, source: str, message: str, details: object = None, **metadata: Any) -> str:
        return "disabled"

    def record_root_progress_health(
            self, outcome: object, stage: object, job_digest: object = None,
            details: object = None, count_attempt: bool = True,
    ) -> bool:
        return False


class BreadcrumbTraceCollector:
    """
    Small bounded in-memory breadcrumb collector.

    The collector is intentionally lightweight and opt-in. Callers can keep
    recording breadcrumbs without having to guard every call site; the
    collector checks the enabled flag on each write and simply no-ops when the
    facility is disabled. Retained entries stay available across disable/enable
    transitions until an explicit reset clears them.
    """

    __MAX_DETAIL_STRING_LENGTH = 256
    # Keep enough depth to redact nested credentials while retaining a hard
    # stop for hostile object graphs. Truncating a collection (rather than
    # stringifying it) is important: repr(dict) can leak a secret below the
    # depth boundary.
    __MAX_COLLECTION_DEPTH = 8
    __MAX_MAPPING_ITEMS = _INGRESS_MAPPING_MAX_ITEMS
    __MAX_LIST_ITEMS = 16
    __MAX_DETAIL_KEY_LENGTH = 128
    __MAX_SANITIZE_NODES = 256
    __EXTERNAL_QUEUE_FIRST_RECORD_WAIT_SECONDS = 0.02
    __EXTERNAL_QUEUE_DRAIN_INTERVAL_SECONDS = 0.1
    DEFAULT_MEMORY_BUDGET_BYTES = DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES
    __SENSITIVE_KEYWORDS = (
        "password",
        "passwd",
        "secret",
        "token",
        "credential",
        "authorization",
        "auth",
        "cookie",
        "session",
        "api_key",
        "apikey",
    )
    # These exact aggregate diagnostic fields describe authority state.  Keep
    # the exception token-safe: credential-bearing keys such as
    # ``authoritative_auth`` must still take the normal redaction path.
    __SAFE_DIAGNOSTIC_KEYS = frozenset({
        "joint_authoritative_before",
        "joint_authoritative_after",
        "joint_authoritative",
    })
    # A producer may first project a closed enum before sending diagnostic
    # details to the collector.  Preserve only those exact enum values when
    # the field name also matches the command-key redaction rule; arbitrary
    # command-shaped payloads remain redacted below.
    __SAFE_DIAGNOSTIC_ENUM_FIELDS = {
        "command_kind": frozenset({"queue", "status", "unknown"}),
        "command_outcome": frozenset({
            "success", "prompt_timeout", "eof", "error", "unknown",
        }),
    }
    __COMMAND_KEYWORDS = (
        "command",
        "cmd",
        "argv",
        "args",
        "script",
        "shell",
    )
    __ACTIVE_DELTA_DIAGNOSTIC_COUNT_KEYS = (
        "lftp_touched_count",
        "active_touched_count",
        "lftp_regressed_count",
        "pending_token_count",
    )
    __ACTIVE_DELTA_DIAGNOSTIC_MAX_COUNT = 128
    __ACTIVE_DELTA_REJECTION_REASONS = frozenset({
        "active_delta_selector_rejected", "active_delta_authorization_rejected",
    })
    __ACTIVE_DELTA_REJECTION_CATEGORY_ORDER = (
        "scan", "lifecycle", "status", "root_identity", "ambiguity", "authority", "overlay", "unknown",
    )
    __ACTIVE_DELTA_SELECTOR_FAILURES = frozenset({
        "invalid_invalidation_scope", "no_selected_roots", "unknown_root", "lftp_regressed",
        "active_root_not_selected", "status_missing", "status_file_id_mismatch",
        "status_not_queued_or_running", "ambiguous_global_visibility",
    })
    __ACTIVE_DELTA_STATUS_PROVENANCE_KEYS = frozenset({
        "poll_source", "fresh", "healthy", "failure_reason", "raw_count_bucket",
        "filtered_count_bucket", "active_scan_root_present", "raw_status_match",
        "filtered_status_match", "canonical_match", "file_id_match", "pair_match",
        "retry_active", "future_state", "poll_decision",
    })
    __ACTIVE_DELTA_STATUS_PROVENANCE_BOOL_KEYS = frozenset({
        "fresh", "healthy", "active_scan_root_present", "raw_status_match",
        "filtered_status_match", "canonical_match", "file_id_match", "pair_match",
        "retry_active",
    })
    __ACTIVE_DELTA_STATUS_POLL_SOURCES = frozenset({
        "fresh_healthy", "fresh_unhealthy", "cached_retry", "cached_idle", "retry_empty",
        "cached_inflight", "inflight_empty", "cached_unhealthy", "unhealthy_empty", "cached_error", "error_empty",
    })
    __ACTIVE_DELTA_STATUS_FAILURE_REASONS = frozenset({
        "inflight", "retry_pending", "timeout", "eof", "command_error", "parser_error",
        "unhealthy_snapshot",
    })
    __ACTIVE_DELTA_STATUS_FUTURE_STATES = frozenset({"none", "pending", "done"})
    __ACTIVE_DELTA_STATUS_COUNT_BUCKETS = frozenset({"0", "1", "2-4", "5+"})
    __ACTIVE_DELTA_POLL_DECISION_BOOL_KEYS = frozenset({
        "idle_authoritative", "next_poll_present", "builder_pending_active_delta",
    })
    __ACTIVE_DELTA_POLL_DECISION_BUCKET_KEYS = frozenset({
        "last_status_count_bucket", "active_scan_result_root_count_bucket",
        "builder_active_touched_count_bucket", "builder_lftp_touched_count_bucket",
    })
    __ACTIVE_DELTA_POLL_DUE_REASONS = frozenset({
        "no_idle_authority", "cadence_due", "unhealthy_cached_status", "active_scan_lftp_transition",
    })
    __ACTIVE_DELTA_POLL_SUPPRESSED_REASONS = frozenset({
        "cached_status", "idle_authoritative", "retry_backoff",
    })
    __ROOT_PROGRESS_HEALTH_OUTCOMES = frozenset({
        "trace_policy_disabled", "tracer_deduplicated", "emitter_rejected",
        "emitter_enqueued", "collector_accepted", "collector_evicted",
        "collector_rejected", "summary_observed",
    })
    __ROOT_PROGRESS_HEALTH_STAGES = frozenset({
        "status", "status_list", "decision", "serialization_start",
        "serialization_end", "sse", "other",
    })
    __ROOT_PROGRESS_HEALTH_REPRESENTATIONS = frozenset({
        "status_list", "root_target", "scoped_sse", "summary_sse", "other",
    })
    __ROOT_PROGRESS_HEALTH_DETAIL_KEYS = frozenset({
        "representation", "status_source", "status_state", "status_type",
        "status_count_bucket", "active_count_bucket", "sampled_count_bucket",
        "sampled_truncated", "progress_percent", "percent_bucket",
        "speed_bucket", "eta_bucket", "model_version", "scope_version",
        "publication", "event", "outcome", "reason", "decision",
        "build_kind", "root_count", "page_count", "record_count",
        "target_count", "duration_bucket",
    })
    __ROOT_PROGRESS_HEALTH_MAX_COUNTER = 2_147_483_647
    __ROOT_PROGRESS_HEALTH_MAX_REPRESENTATIONS = 8
    # This deliberately lives outside the ordinary event deque: a busy trace
    # must not erase the small causal record needed to explain a progress gap.
    __PROGRESS_LINEAGE_MAX_SPANS = 32
    __PROGRESS_LINEAGE_MAX_STEPS = 12
    __PROGRESS_LINEAGE_MAX_VERSION_RANGES = 8
    __PROGRESS_LINEAGE_PHASES = frozenset({
        "status_submit", "status_start", "status_finish", "status_consume",
        "status_sample",
        "pre_active_delta", "active_delta_selector", "active_delta_builder",
        "active_delta_authorization", "active_delta_adoption", "updater_decision",
        "active_progress_overlay_admission", "direct_root_counter_publish",
        "model_mutation", "scoped_stream_emit",
    })
    __PROGRESS_LINEAGE_DETAIL_KEYS = frozenset({
        "outcome", "source", "fresh", "healthy", "status_count_bucket",
        "active_count_bucket", "queue_age_bucket", "lock_wait_bucket",
        "lock_wait_duration_bucket", "publish_duration_bucket", "duration_bucket",
        "decision", "build_kind", "model_version",
        "scope_version", "model_version_first", "model_version_last",
        "mutation_count_bucket", "scoped_stream_count_bucket",
        "selected_pair_duration_bucket", "global_copy_duration_bucket",
        "partial_build_duration_bucket", "updater_cycle_duration_bucket",
        "controller_cycle_duration_bucket", "overlay_admission",
        "active_scan_equivalence",
        "record_shape", "record_has_bytes", "record_has_percent",
        "record_has_speed", "record_has_eta", "job_correlation",
        "target_correlation",
        "lifecycle_epoch", "job_match", "epoch_match",
        "prior_source", "new_source", "destination",
        "prior_counter_bucket", "new_counter_bucket",
        "prior_percent_bucket", "new_percent_bucket",
        "monotonic_relation", "sidecar_shape", "sidecar_total_match",
        "coverage_disk_relation", "pending_transition", "physical_proof",
        "explicit_stop", "terminal_outcome", "retirement_cause",
        "scan_health", "scan_freshness", "collector_admission",
        "collector_drop", "collector_eviction", "collector_truncation",
        "stream_linkage",
    })
    __PROGRESS_LINEAGE_ENUMS = {
        "outcome": frozenset({
            "ok", "exception", "mutated", "unchanged", "accepted", "poll_gate",
            "unavailable", "job_identity", "lifecycle_epoch", "root_authority",
            "counter_shape", "scan_topology", "scan_lifecycle", "invalidation_scan", "invalidation_lifecycle",
            "invalidation_overlay", "invalidation_authority", "invalidation_unknown",
            "invalidation_mixed", "invalidation_scope", "roots", "unknown_root",
            "global_safety", "status_shape",
        }),
        "record_shape": frozenset({"at", "got", "none"}),
        "job_correlation": frozenset(),
        "target_correlation": frozenset(),
        "prior_source": frozenset({"overlay", "base", "none", "unknown"}),
        "new_source": frozenset({"overlay", "base", "none", "unknown"}),
        "destination": frozenset({"overlay", "base", "none", "unknown"}),
        "prior_counter_bucket": frozenset({"none", "0", "1-9", "10-24", "25-49", "50-74", "75-99", "100"}),
        "new_counter_bucket": frozenset({"none", "0", "1-9", "10-24", "25-49", "50-74", "75-99", "100"}),
        "prior_percent_bucket": frozenset({"none", "0", "1-9", "10-24", "25-49", "50-74", "75-99", "100"}),
        "new_percent_bucket": frozenset({"none", "0", "1-9", "10-24", "25-49", "50-74", "75-99", "100"}),
        "monotonic_relation": frozenset({"advance", "same", "regress", "unknown"}),
        "sidecar_shape": frozenset({"absent", "invalid", "multi_segment", "base_only", "unknown"}),
        "sidecar_total_match": frozenset({"true", "false", "unknown"}),
        "coverage_disk_relation": frozenset({
            "sidecar_ahead", "equal", "sidecar_behind", "unknown",
        }),
        "pending_transition": frozenset({"registered", "retained", "cleared", "unknown"}),
        "physical_proof": frozenset({"proven", "missing", "unknown"}),
        "terminal_outcome": frozenset({"downloaded", "stopped", "deferred", "rejected", "unknown"}),
        "retirement_cause": frozenset({
            "lftp_job_finished", "explicit_stop", "still_active",
            "completion_detection_not_authoritative", "unknown",
        }),
        "scan_health": frozenset({"healthy", "unhealthy", "requested", "unknown"}),
        "scan_freshness": frozenset({"fresh", "stale", "unknown"}),
        "collector_admission": frozenset({"accepted", "rejected", "unknown"}),
        "stream_linkage": frozenset({"linked", "unlinked", "unknown"}),
        "overlay_admission": frozenset({
            "poll_gate", "unavailable", "invalidation_scope", "roots",
            "unknown_root", "global_safety", "status_shape", "accepted", "exception",
            "invalidation_scan", "invalidation_lifecycle", "invalidation_overlay",
            "invalidation_authority", "invalidation_unknown", "invalidation_mixed",
        }),
        "active_scan_equivalence": frozenset({
            "none", "model_owned", "root_set", "unknown", "status", "type", "pair", "sidecar",
            "collision", "metadata", "topology",
        }),
        "source": frozenset({
            "fresh_healthy", "fresh_unhealthy", "cached_retry", "cached_idle",
            "retry_empty", "cached_inflight", "inflight_empty", "cached_unhealthy",
            "unhealthy_empty", "cached_error", "error_empty",
        }),
        "status_count_bucket": frozenset({"0", "1", "2-4", "5+"}),
        "active_count_bucket": frozenset({"0", "1", "2-4", "5+"}),
        "queue_age_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "lock_wait_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "lock_wait_duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "publish_duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "decision": frozenset({"full_build", "active_delta", "cached"}),
        "build_kind": frozenset({"candidate", "full", "none"}),
        "selected_pair_duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "global_copy_duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "partial_build_duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "updater_cycle_duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
        "controller_cycle_duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}),
    }

    def __init__(
        self,
        enabled_getter: Callable[[], bool],
        max_entries: Optional[int] = None,
        memory_budget_bytes: int = DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES,
        policy: Optional[Mapping[str, object]] = None,
        policy_persist: Optional[Callable[[Mapping[str, object]], None]] = None,
        durable_enabled: bool = DEFAULT_BREADCRUMB_TRACE_DURABLE_ENABLED,
        durable_path: Optional[str] = None,
        durable_enabled_getter: Optional[Callable[[], bool]] = None,
        durable_path_getter: Optional[Callable[[], Optional[str]]] = None,
    ):
        if max_entries is not None and (type(max_entries) is not int or max_entries < 0):
            raise ValueError("max_entries must be a non-negative integer or None")
        if max_entries == 0:
            max_entries = None
        if type(memory_budget_bytes) is not int or memory_budget_bytes < 1:
            raise ValueError("memory_budget_bytes must be greater than 0")
        policy_result = self.__validate_policy_candidate(policy or {})
        if not bool(policy_result["valid"]):
            raise ValueError("invalid breadcrumb policy: {}".format(policy_result["errors"]))
        self.__enabled_getter = enabled_getter
        if type(durable_enabled) is not bool:
            raise ValueError("durable_enabled must be a boolean")
        self.__durable_enabled_getter = durable_enabled_getter or (lambda: durable_enabled)
        self.__durable_path_getter = durable_path_getter or (lambda: durable_path)
        self.__durable_enabled_state = False
        self.__durable_owner_state = "DISABLED"
        self.__durable_spool: Optional[BreadcrumbDurableSpool] = None
        self.__durable_last_snapshot: Optional[Dict[str, Any]] = None
        self.__ingress_drainer: Optional[Thread] = None
        self.__ingress_drainer_stop = Event()
        self.__ingress_drainer_wakeup = Event()
        self.__ingress_drainer_active = False
        self.__max_entries = max_entries
        self.__memory_budget_bytes = memory_budget_bytes
        self.__entries: Deque[Dict[str, Any]] = deque()
        self.__entry_sizes: Deque[int] = deque()
        self.__retained_bytes = 0
        self.__accepted_count = 0
        self.__coalesced_count = 0
        self.__dropped_count = 0
        self.__policy_dropped_count = 0
        self.__oversized_dropped_count = 0
        self.__evicted_count = 0
        self.__durable_enqueue_rejected_count = 0
        self.__lost_ranges: Deque[Dict[str, Any]] = deque()
        self.__gap_watermark_to = 0
        self.__external_records: Optional[multiprocessing.Queue[object]] = None
        self.__critical_external_records: Optional[multiprocessing.Queue[object]] = None
        self.__normal_external_records: Optional[multiprocessing.Queue[object]] = None
        self.__external_records_lock = Lock()
        self.__external_drain_lock = Lock()
        # Serialize runtime enable/disable with final collector shutdown so
        # the spool owner cannot be replaced or closed concurrently.
        self.__durable_lifecycle_lock = Lock()
        self.__enabled_gate = multiprocessing.RawValue("b", 0)
        self.__ingress_closed_gate = multiprocessing.RawValue("b", 0)
        self.__durable_reconcile_gate = multiprocessing.RawValue("b", 0)
        # One nonblocking producer admission section makes close a true
        # quiescence gate: once it sets the shared bit, no emitter can put a
        # record whose ack/loss is outside the final drain accounting.
        self.__ingress_admission_lock = multiprocessing.Lock()
        self.__ingress_inflight_count = multiprocessing.Value("L", 0)
        self.__collector_closing = False
        self.__collector_closed = False
        self.__worker_policy_revision = multiprocessing.Value("L", 0)
        self.__worker_policy_epoch = multiprocessing.Value("L", 0)
        self.__worker_policy_generation = multiprocessing.RawValue("L", 0)
        self.__worker_policy_length = multiprocessing.RawValue("L", 0)
        self.__worker_policy_bytes = multiprocessing.RawArray("B", _SHARED_POLICY_MAX_BYTES)
        self.__ingress_rejected_count = multiprocessing.Value("L", 0)
        self.__ingress_rejected_seen = 0
        self.__critical_ingress_rejected_count = multiprocessing.Value("L", 0)
        self.__critical_ingress_rejected_seen = 0
        self.__critical_ingress_rejected_total = 0
        self.__normal_ingress_rejected_count = multiprocessing.Value("L", 0)
        self.__normal_ingress_rejected_seen = 0
        self.__normal_ingress_rejected_total = 0
        self.__critical_ingress_enqueued_count = multiprocessing.Value("L", 0)
        self.__normal_ingress_enqueued_count = multiprocessing.Value("L", 0)
        self.__critical_ingress_ack_count = multiprocessing.Value("L", 0)
        self.__normal_ingress_ack_count = multiprocessing.Value("L", 0)
        self.__critical_ingress_reservation_count = multiprocessing.Value("L", 0)
        self.__normal_ingress_reservation_count = multiprocessing.Value("L", 0)
        self.__critical_ingress_unknown_count = multiprocessing.Value("L", 0)
        self.__normal_ingress_unknown_count = multiprocessing.Value("L", 0)
        self.__ingress_shutdown_leftovers = 0
        self.__ingress_shutdown_leftovers_recorded = 0
        self.__ingress_unresolved = 0
        self.__ingress_unresolved_recorded = 0
        self.__root_progress_enqueued_count = multiprocessing.Value("L", 0)
        self.__root_progress_enqueued_seen = 0
        self.__root_progress_rejected_count = multiprocessing.Value("L", 0)
        self.__root_progress_rejected_seen = 0
        self.__root_progress_disabled_count = multiprocessing.Value("L", 0)
        self.__root_progress_disabled_seen = 0
        self.__root_progress_deduplicated_count = multiprocessing.Value("L", 0)
        self.__root_progress_deduplicated_seen = 0
        self.__root_progress_summary_count = multiprocessing.Value("L", 0)
        self.__root_progress_summary_seen = 0
        self.__worker_policy_ack_revision = 0
        self.__worker_policy_ack_epoch = 0
        self.__policy_epoch = 0
        self.__lock = RLock()
        self.__version = 0
        self.__last_reset_version = 0
        self.__last_reset_reason: Optional[str] = None
        self.__reset_generation = 0
        self.__window_truncated_pending = False
        self.__external_queue_last_drain_count = 0
        self.__external_queue_last_drain_limit = self.__max_entries
        self.__external_queue_drain_limited = False
        self.__external_queue_last_drain_monotonic: Optional[float] = None
        self.__last_signature: Optional[str] = None
        self.__coalesce_entries: Dict[str, Dict[str, Any]] = {}
        self.__last_failure_entry: Optional[Dict[str, Any]] = None
        self.__last_failure_version: Optional[int] = None
        # This is deliberately independent of the chronological deque.  It
        # retains the latest authorization rejection after a busy progress
        # trace evicts the event that first established it.
        self.__latest_active_delta_rejection_summary: Optional[Dict[str, Any]] = None
        self.__root_progress_health: Dict[str, Any] = {
            "schema": "model_root_progress_health.v1",
            "attempt_semantics": "one_per_root_progress_attempt",
            "attempt_count": 0,
            "outcome_counts": {
                outcome: 0 for outcome in (
                    "trace_policy_disabled", "tracer_deduplicated", "emitter_rejected",
                    "emitter_enqueued", "collector_accepted", "collector_evicted",
                    "collector_rejected", "summary_observed",
                )
            },
            "latest": None,
            "latest_summary": None,
            "latest_by_representation": {},
        }
        self.__progress_lineage: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        # Unlike the spans, these fixed counters survive an evidence clear so
        # a post-cleanup snapshot can still say whether the target lifecycle
        # reached this collector.  Allocate the maps only on the first
        # enabled lineage write.
        self.__progress_lineage_health: Optional[Dict[str, Any]] = None
        self.__progress_lineage_reset_count = 0
        self.__policy: Dict[str, Any] = cast(Dict[str, Any], policy_result["policy"])
        self.__effective_policy = _EffectiveBreadcrumbPolicy.from_policy(self.__policy)
        self.__policy_revision = 0
        self.__policy_persist = policy_persist
        self.__policy_persistence_state = "not_requested"
        self.__last_clear_scope: Optional[Dict[str, Any]] = None
        self.__category_accounting: Dict[str, Dict[str, int]] = {}
        self.__publish_effective_policy()
        self.sync_enabled_state()

    def create_emitter(self) -> BreadcrumbTraceEmitter:
        with self.__external_records_lock:
            # Resource creation is serialized by the same gate used by emitter
            # admission.  Once CLOSING is visible, a late caller gets an inert
            # emitter and cannot create queues, a drainer, or a spool.
            with self.__ingress_admission_lock:
                if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
                    return BreadcrumbTraceNoopEmitter()
                if self.__normal_external_records is None:
                    # Keep disabled child traffic bounded even when the retained
                    # count limit is intentionally unlimited.  This is a queue
                    # ingress guard, not a retained-event limit; the collector's
                    # byte budget remains authoritative after sanitization.
                    # Queue capacity is at most one sixteenth of the retained
                    # budget even at the ingress record maximum.
                    critical_capacity = BREADCRUMB_INGRESS_CRITICAL_CAPACITY
                    # Preserve the previous collector contract for callers
                    # that intentionally select a small count bound.  An
                    # unlimited/zero count keeps the fixed 2048 normal lane;
                    # the reserved critical lane remains independent.
                    normal_capacity = BREADCRUMB_INGRESS_NORMAL_CAPACITY
                    if self.__max_entries is not None:
                        normal_capacity = max(
                            1,
                            min(self.__max_entries, normal_capacity),
                        )
                    self.__critical_external_records = multiprocessing.Queue(maxsize=critical_capacity)
                    self.__normal_external_records = multiprocessing.Queue(maxsize=normal_capacity)
                    # Retain the old private alias for test doubles and legacy
                    # introspection; all real emitters use both explicit lanes.
                    self.__external_records = self.__normal_external_records
                with self.__durable_lifecycle_lock:
                    self.__ensure_durable_workers()
                # The emitter crosses the spawn boundary. Keep the collector callback,
                # locks, and owning Context in the parent; the shared gate preserves
                # runtime enable/disable updates.
                return BreadcrumbTraceEmitter(
                    self.__normal_external_records, self.__enabled_gate, self.__worker_policy_revision, self.__worker_policy_epoch,
                    self.__worker_policy_generation, self.__worker_policy_length, self.__worker_policy_bytes,
                    self.__ingress_rejected_count,
                    self.__root_progress_enqueued_count,
                    self.__root_progress_rejected_count,
                    self.__root_progress_disabled_count,
                    self.__root_progress_deduplicated_count,
                    self.__root_progress_summary_count,
                    critical_record_queue=self.__critical_external_records,
                    critical_rejected_count=self.__critical_ingress_rejected_count,
                    normal_rejected_count=self.__normal_ingress_rejected_count,
                    critical_enqueued_count=self.__critical_ingress_enqueued_count,
                    normal_enqueued_count=self.__normal_ingress_enqueued_count,
                    closed_gate=self.__ingress_closed_gate,
                    admission_lock=self.__ingress_admission_lock,
                    ingress_inflight_count=self.__ingress_inflight_count,
                    critical_reservation_count=self.__critical_ingress_reservation_count,
                    normal_reservation_count=self.__normal_ingress_reservation_count,
                    critical_reservation_unknown_count=self.__critical_ingress_unknown_count,
                    normal_reservation_unknown_count=self.__normal_ingress_unknown_count,
                    durable_reconcile_gate=self.__durable_reconcile_gate,
                )

    def durable_snapshot(self) -> Dict[str, Any]:
        # Session replacement and final projection both run under this lock.
        # Serialize API snapshots with those transitions so an old cache is
        # never paired with a replacement spool's owner state.
        with self.__durable_lifecycle_lock:
            return self.__durable_snapshot_body()

    def __durable_snapshot_body(self) -> Dict[str, Any]:
        spool = self.__durable_spool
        if spool is None:
            if self.__durable_last_snapshot is not None:
                snapshot = copy.deepcopy(self.__durable_last_snapshot)
            else:
                snapshot = {
                    "schema": "breadcrumb_durable_health.v1",
                    "enabled": False,
                    "state": self.__durable_owner_state,
                    "session_id": None,
                    "thread_alive": False,
                    "ingress_drainer_alive": False,
                    "accepted": 0,
                    "written": 0,
                    "lost": 0,
                    "critical_lost": 0,
                    "normal_lost": 0,
                    "closed": self.__durable_owner_state == "TERMINAL",
                    "terminal": self.__durable_owner_state == "TERMINAL",
                    "incomplete": self.__durable_owner_state == "INCOMPLETE",
                    "unresolved": self.__ingress_unresolved,
                    "unknown": (
                        self.__shared_counter_value(self.__critical_ingress_unknown_count)
                        + self.__shared_counter_value(self.__normal_ingress_unknown_count)
                    ),
                }
        else:
            snapshot = spool.snapshot()
        if self.__acquire_collector_lock():
            try:
                owner_state = self.__durable_owner_state
                drainer_alive = bool(
                    self.__ingress_drainer is not None
                    and self.__ingress_drainer.is_alive()
                )
            finally:
                self.__lock.release()
        else:
            # A terminal snapshot must remain retrievable even when a legacy
            # lock test double rejects a late read. The durable spool/cache
            # payload is authoritative; expose the last known owner state
            # without mutating it.
            owner_state = snapshot.get("owner_state", self.__durable_owner_state)
            drainer_alive = bool(snapshot.get("ingress_drainer_alive", False))
        snapshot["owner_state"] = owner_state
        snapshot["ingress_drainer_alive"] = drainer_alive
        snapshot["ingress_queues_open"] = bool(
            self.__critical_external_records is not None
            or self.__normal_external_records is not None
        )
        snapshot.update(self.__collector_retirement_status(snapshot))
        return snapshot

    def __collector_retirement_status(self, snapshot: Mapping[str, Any]) -> Dict[str, Any]:
        """Expose the authoritative collector quiescence boundary.

        ``thread_alive`` and queue state only describe the durable writer.
        Direct collector callers can still hold an admission token while the
        writer is already idle, so runtime exclusion must also observe the
        shared token/reservation counters and the collector lifecycle gate.
        UNKNOWN reservations are retained as truthful uncertainty, but have
        no live caller to wait for and therefore do not prevent retirement.
        """
        inflight_tokens = self.__shared_counter_value(self.__ingress_inflight_count)
        known_reservations = (
            self.__shared_counter_value(self.__critical_ingress_reservation_count)
            + self.__shared_counter_value(self.__normal_ingress_reservation_count)
        )
        lifecycle_state = self.__ingress_lifecycle_state()
        lifecycle_name = {
            _BREADCRUMB_LIFECYCLE_OPEN: "OPEN",
            _BREADCRUMB_LIFECYCLE_CLOSING: "CLOSING",
            _BREADCRUMB_LIFECYCLE_CLOSED: "CLOSED",
        }.get(lifecycle_state, "CLOSED")
        try:
            pending = (
                int(snapshot.get("critical_pending", 0))
                + int(snapshot.get("normal_pending", 0))
            )
        except (TypeError, ValueError, OverflowError):
            pending = 1
        writing = bool(snapshot.get("writing", False))
        collector_retired = (
            lifecycle_state == _BREADCRUMB_LIFECYCLE_CLOSED
            and inflight_tokens == 0
            and known_reservations == 0
            and not bool(snapshot.get("thread_alive", False))
            and not bool(snapshot.get("ingress_drainer_alive", False))
            and not bool(snapshot.get("ingress_queues_open", False))
            and pending == 0
            and not writing
        )
        return {
            "collector_lifecycle": lifecycle_name,
            "inflight_tokens": inflight_tokens,
            "known_reservations": known_reservations,
            "collector_retired": collector_retired,
        }

    def ingress_snapshot(self) -> Dict[str, Any]:
        """Return direct, bounded lane health for diagnostic tests/tools."""
        self.__consume_ingress_rejections()
        critical, normal = self.__ingress_queues()
        critical_enqueued = self.__shared_counter_value(self.__critical_ingress_enqueued_count)
        normal_enqueued = self.__shared_counter_value(self.__normal_ingress_enqueued_count)
        critical_ack = self.__shared_counter_value(self.__critical_ingress_ack_count)
        normal_ack = self.__shared_counter_value(self.__normal_ingress_ack_count)
        critical_reserved = self.__shared_counter_value(self.__critical_ingress_reservation_count)
        normal_reserved = self.__shared_counter_value(self.__normal_ingress_reservation_count)
        critical_unknown = self.__shared_counter_value(self.__critical_ingress_unknown_count)
        normal_unknown = self.__shared_counter_value(self.__normal_ingress_unknown_count)
        return {
            "schema": "breadcrumb_ingress_health.v1",
            "critical_capacity": getattr(critical, "_maxsize", 0) if critical is not None else 0,
            "normal_capacity": getattr(normal, "_maxsize", 0) if normal is not None else 0,
            "critical_pending": max(0, critical_enqueued - critical_ack) + critical_reserved + critical_unknown,
            "normal_pending": max(0, normal_enqueued - normal_ack) + normal_reserved + normal_unknown,
            "critical_enqueued": critical_enqueued,
            "normal_enqueued": normal_enqueued,
            "critical_ack": critical_ack,
            "normal_ack": normal_ack,
            "critical_reserved": critical_reserved,
            "normal_reserved": normal_reserved,
            "critical_unknown": critical_unknown,
            "normal_unknown": normal_unknown,
            "reservation_unknown": critical_unknown + normal_unknown,
            "critical_reservation_state": (
                "UNKNOWN" if critical_unknown else "RESERVED" if critical_reserved else "ACKED"
            ),
            "normal_reservation_state": (
                "UNKNOWN" if normal_unknown else "RESERVED" if normal_reserved else "ACKED"
            ),
            "critical_rejected_count": self.__critical_ingress_rejected_total,
            "normal_rejected_count": self.__normal_ingress_rejected_total,
            "shutdown_leftovers": self.__ingress_shutdown_leftovers,
            "unresolved": self.__ingress_unresolved,
            "critical_burst": BREADCRUMB_INGRESS_CRITICAL_BURST,
        }

    def wait_for_ingress(self, timeout: float = 1.0) -> bool:
        """Wait for the parent drainer to observe currently queued records."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            if not self.__ingress_has_records():
                return True
            self.__ingress_drainer_wakeup.set()
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        return not self.__ingress_has_records()

    def flush_durable(self, timeout: float = 1.0) -> bool:
        """Wait for both ingress and durable queues to become empty."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            self.__consume_ingress_rejections()
            self.wait_for_ingress(min(0.02, max(0.0, deadline - time.monotonic())))
            spool = self.__durable_spool
            spool_health = spool.snapshot() if spool is not None else None
            cached_health = self.__durable_last_snapshot
            owner_incomplete = self.__durable_owner_state == "INCOMPLETE"
            cached_incomplete = isinstance(cached_health, Mapping) and (
                cached_health.get("incomplete")
                or cached_health.get("state") == "INCOMPLETE"
            )
            cached_loss = isinstance(cached_health, Mapping) and any(
                int(cached_health.get(key, 0)) > 0
                for key in (
                    "lost", "unknown", "writer_failures", "flush_failures",
                    "retention_prune_failures", "health_publish_failures",
                )
                if isinstance(cached_health.get(key, 0), (int, float))
            )
            durable_healthy = spool_health is None and not cached_incomplete and not cached_loss
            if spool_health is not None:
                durable_healthy = (
                    spool_health.get("critical_pending", 0) == 0
                    and spool_health.get("normal_pending", 0) == 0
                    and not spool_health.get("writing", False)
                    and not spool_health.get("incomplete", False)
                    and spool_health.get("state") != "INCOMPLETE"
                    and bool(spool_health.get("health_publish_ok", True))
                    and int(spool_health.get("lost", 0)) == 0
                    and int(spool_health.get("unknown", 0)) == 0
                    and int(spool_health.get("writer_failures", 0)) == 0
                    and int(spool_health.get("flush_failures", 0)) == 0
                    and int(spool_health.get("retention_prune_failures", 0)) == 0
                    and int(spool_health.get("health_publish_failures", 0)) == 0
                    and int(spool_health.get("written", 0))
                    >= int(spool_health.get("accepted", 0))
                )
            if (
                not self.__ingress_has_records()
                and self.__shared_counter_value(self.__critical_ingress_unknown_count) == 0
                and self.__shared_counter_value(self.__normal_ingress_unknown_count) == 0
                and self.__durable_enqueue_rejected_count == 0
                and self.__dropped_count == 0
                and not owner_incomplete
                and durable_healthy
            ):
                return True
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        return False

    def close(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Quiesce ingress, drain, and close durable output to one deadline."""
        wait_for = DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + wait_for
        already_closed = False
        with self.__lock:
            if self.__collector_closed:
                already_closed = True
        if already_closed:
            return self.durable_snapshot()
        if not self.__close_ingress_gate(deadline):
            # A producer admission section should be tiny, but never turn a
            # close call into an unbounded wait if a foreign queue owner is
            # misbehaving. Ownership remains with the live workers.
            self.__force_closing_gate()
            return self.__mark_durable_incomplete(self.__durable_spool, deadline)
        remaining = max(0.0, deadline - time.monotonic())
        try:
            lifecycle_acquired = bool(self.__durable_lifecycle_lock.acquire(timeout=remaining))
        except (TypeError, ValueError):
            lifecycle_acquired = bool(self.__durable_lifecycle_lock.acquire(False)) if remaining > 0 else False
        except Exception:
            lifecycle_acquired = False
        if not lifecycle_acquired:
            return self.__mark_durable_incomplete(self.__durable_spool, deadline)
        try:
            drainer = self.__ingress_drainer
            if drainer is not None:
                self.__ingress_drainer_stop.set()
                self.__ingress_drainer_wakeup.set()
                drainer.join(max(0.0, deadline - time.monotonic()))
                if drainer.is_alive():
                    # The drainer retains ownership of both multiprocessing
                    # lanes until it has acknowledged every admitted item.
                    return self.__mark_durable_incomplete(self.__durable_spool, deadline)
                with self.__lock:
                    if self.__ingress_drainer is drainer:
                        self.__ingress_drainer = None
                        self.__ingress_drainer_active = False
            # Explicitly drain/ack both lanes after the owner is terminal.
            # Repeat across feeder latency until shared enqueue/ack counts
            # reach a stable zero or the same absolute deadline expires.
            if not self.__drain_ingress_until_quiescent(deadline):
                # A concurrent API/snapshot drain still owns the serialization
                # lock. Do not close the writer or lanes until it quiesces.
                return self.__mark_durable_incomplete(self.__durable_spool, deadline)
            # The common token gate excludes new producer calls. Wait for all
            # pre-gate tokens and fold their shared counters once before the
            # writer publishes its terminal health sidecar.
            if not self.__finalize_ingress_accounting(deadline):
                return self.__mark_durable_incomplete(self.__durable_spool, deadline)
            # A token may have crossed the gate before the drainer's final
            # queue check and completed its put only while the token was being
            # settled. Consume that now-visible handoff before terminal file
            # publication; no producer can start a new token at this point.
            if self.__ingress_known_pending_count() > 0 and not self.__drain_ingress_until_quiescent(deadline):
                return self.__mark_durable_incomplete(self.__durable_spool, deadline)
            # UNKNOWN reservations have no queue item that can be drained.
            # Retire the recorder with an INCOMPLETE projection while keeping
            # UNKNOWN visible; never turn that uncertainty into proven loss.
            unknown_reservations = (
                self.__shared_counter_value(self.__critical_ingress_unknown_count)
                + self.__shared_counter_value(self.__normal_ingress_unknown_count)
            )
            remaining = self.__ingress_known_pending_count()
            if remaining:
                # A closed feeder may leave an admitted item unreachable.
                # Account it as shutdown loss, but never race an active
                # drainer to do so.
                with self.__lock:
                    unaccounted = max(0, remaining - self.__ingress_shutdown_leftovers_recorded)
                    self.__ingress_shutdown_leftovers += unaccounted
                    self.__ingress_shutdown_leftovers_recorded += unaccounted
            spool = self.__durable_spool
            spool_snapshot: Optional[Dict[str, Any]] = None
            retired_incomplete_owner = False
            if spool is not None:
                if unknown_reservations:
                    spool.mark_incomplete(
                        unresolved=unknown_reservations,
                        unknown=unknown_reservations,
                    )
                spool_snapshot = self.__close_durable_spool(
                    spool, max(0.0, deadline - time.monotonic()), "TERMINAL",
                )
                retired_incomplete_owner = (
                    bool(spool_snapshot.get("incomplete"))
                    and not bool(spool_snapshot.get("thread_alive"))
                    and not bool(spool_snapshot.get("writing"))
                    and int(spool_snapshot.get("critical_pending", 0)) == 0
                    and int(spool_snapshot.get("normal_pending", 0)) == 0
                )
                if (
                    spool_snapshot.get("thread_alive")
                    or not spool_snapshot.get("terminal", True)
                    or not spool_snapshot.get("health_publish_ok", True)
                ) and not retired_incomplete_owner:
                    return self.__mark_durable_incomplete(spool, deadline)
            # The writer has published terminal health only after all local
            # records accepted before the gate and all final rejection
            # counters were accounted. Mark the shared lifecycle terminal
            # before releasing/closing producer queues.
            remaining = max(0.0, deadline - time.monotonic())
            acquired = False
            try:
                acquired = bool(self.__ingress_admission_lock.acquire(timeout=remaining))
            except (TypeError, ValueError):
                acquired = bool(self.__ingress_admission_lock.acquire(False)) if remaining > 0 else False
            except Exception:
                acquired = False
            if not acquired:
                return self.__mark_durable_incomplete(spool, deadline)
            try:
                remaining = max(0.0, deadline - time.monotonic())
                if not self.__lock.acquire(timeout=remaining):
                    return self.__mark_durable_incomplete(spool, deadline)
                try:
                    self.__collector_closed = True
                    self.__ingress_closed_gate.value = _BREADCRUMB_LIFECYCLE_CLOSED
                    self.__durable_enabled_state = False
                    if spool is None and not retired_incomplete_owner and not unknown_reservations:
                        self.__durable_owner_state = "TERMINAL"
                    elif spool is None and unknown_reservations:
                        self.__durable_owner_state = "INCOMPLETE"
                    # Publish the cache and detach the owner only after the
                    # lifecycle is CLOSED under the same admission lock that
                    # excluded producers. This keeps the returned snapshot,
                    # API view, and sidecar on one committed projection; a
                    # lock/publication failure above remains INCOMPLETE and
                    # retains the live owner for bounded retirement.
                    if spool is not None and spool_snapshot is not None:
                        self.__durable_last_snapshot = copy.deepcopy(spool_snapshot)
                        self.__durable_owner_state = (
                            "INCOMPLETE" if retired_incomplete_owner else "TERMINAL"
                        )
                        if self.__durable_spool is spool:
                            self.__durable_spool = None
                finally:
                    self.__lock.release()
            finally:
                try:
                    self.__ingress_admission_lock.release()
                except Exception:
                    pass
        finally:
            self.__durable_lifecycle_lock.release()
        for candidate in (self.__critical_external_records, self.__normal_external_records):
            if candidate is None:
                continue
            try:
                candidate.cancel_join_thread()
            except Exception:
                pass
            try:
                candidate.close()
            except Exception:
                pass
        self.__critical_external_records = None
        self.__normal_external_records = None
        self.__external_records = None
        return self.durable_snapshot()

    def __ingress_lifecycle_state(self) -> int:
        try:
            return int(self.__ingress_closed_gate.value)
        except Exception:
            # A broken shared state must fail closed for late emitters.
            return _BREADCRUMB_LIFECYCLE_CLOSED

    def __close_ingress_gate(self, deadline: float) -> bool:
        """Linearize the one-way OPEN -> CLOSING producer transition."""
        remaining = max(0.0, deadline - time.monotonic())
        acquired = False
        try:
            acquired = bool(self.__ingress_admission_lock.acquire(timeout=remaining))
        except (TypeError, ValueError):
            # Compatibility with small lock test doubles that only expose a
            # blocking acquire. The real multiprocessing lock takes timeout.
            if remaining <= 0:
                return False
            try:
                acquired = bool(self.__ingress_admission_lock.acquire(False))
            except Exception:
                acquired = False
        except Exception:
            acquired = False
        if not acquired:
            return False
        try:
            # Publish the shared transition first, while still holding the
            # admission lock. Collector-local bookkeeping may fail to acquire
            # its lock, but emitters must already observe CLOSING and become
            # inert rather than racing a partially closed collector.
            state = self.__ingress_lifecycle_state()
            if state == _BREADCRUMB_LIFECYCLE_OPEN:
                self.__ingress_closed_gate.value = _BREADCRUMB_LIFECYCLE_CLOSING
            elif state == _BREADCRUMB_LIFECYCLE_CLOSED:
                self.__collector_closed = True
            remaining = max(0.0, deadline - time.monotonic())
            if not self.__lock.acquire(timeout=remaining):
                return False
            try:
                if state in {_BREADCRUMB_LIFECYCLE_OPEN, _BREADCRUMB_LIFECYCLE_CLOSING}:
                    self.__collector_closing = True
                elif state == _BREADCRUMB_LIFECYCLE_CLOSED:
                    self.__collector_closed = True
            finally:
                self.__lock.release()
            return True
        finally:
            try:
                self.__ingress_admission_lock.release()
            except Exception:
                pass

    def __force_closing_gate(self) -> None:
        """Fail closed when the admission lock itself cannot be acquired."""
        try:
            state = self.__ingress_lifecycle_state()
            if state == _BREADCRUMB_LIFECYCLE_OPEN:
                self.__ingress_closed_gate.value = _BREADCRUMB_LIFECYCLE_CLOSING
            if state != _BREADCRUMB_LIFECYCLE_CLOSED:
                self.__collector_closing = True
        except Exception:
            # A broken shared gate is already treated as CLOSED by readers;
            # preserve that inert behavior rather than reopening admission.
            try:
                self.__ingress_closed_gate.value = _BREADCRUMB_LIFECYCLE_CLOSING
            except Exception:
                pass

    def __finalize_ingress_accounting(self, deadline: float) -> bool:
        """Fold all pre-gate token results exactly once."""
        # The OPEN -> CLOSING transition already prevents new tokens.  Do not
        # reacquire the admission lock while waiting: a producer admitted just
        # before that transition may still need the same lock to settle its
        # queue reservation.
        if not self.__wait_for_ingress_tokens(deadline):
            return False
        self.__consume_ingress_rejections()
        return True

    def __wait_for_ingress_tokens(self, deadline: float) -> bool:
        while True:
            try:
                with self.__ingress_inflight_count.get_lock():
                    active = max(0, int(self.__ingress_inflight_count.value))
            except Exception:
                active = 0
            reservations = (
                self.__shared_counter_value(self.__critical_ingress_reservation_count)
                + self.__shared_counter_value(self.__normal_ingress_reservation_count)
            )
            if active == 0 and reservations == 0:
                return True
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                return False
            self.__ingress_drainer_wakeup.wait(min(0.005, remaining))
            self.__ingress_drainer_wakeup.clear()

    def __drain_ingress_until_quiescent(self, deadline: float) -> bool:
        first = True
        while time.monotonic() < deadline:
            if self.__ingress_known_pending_count() <= 0:
                return True
            drained = self.__drain_external_records(
                limit=None,
                wait_for_first_record=first,
                force=True,
                deadline=deadline,
            )
            first = False
            if drained < 0:
                return False
            if self.__ingress_known_pending_count() <= 0:
                return True
            if drained == 0:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                self.__ingress_drainer_wakeup.wait(min(0.01, remaining))
                self.__ingress_drainer_wakeup.clear()
        # A reservation or feeder item still pending at the deadline is not a
        # successful drain. Leave the owner INCOMPLETE so terminal publication
        # cannot claim exact closure.
        return self.__ingress_known_pending_count() <= 0

    shutdown = close

    def __durable_requested(self) -> bool:
        try:
            return bool(self.__durable_enabled_getter())
        except Exception:
            return False

    def __durable_path(self) -> Optional[str]:
        try:
            path = self.__durable_path_getter()
        except Exception:
            return None
        return path if isinstance(path, str) and path else None

    @staticmethod
    def __shared_counter_value(counter: multiprocessing.Value) -> int:
        try:
            with counter.get_lock():
                return max(0, int(counter.value))
        except Exception:
            try:
                return max(0, int(counter.value))
            except Exception:
                return 0

    def __acquire_collector_lock(self, timeout: float = 0.0) -> bool:
        """Acquire the collector lock without assuming a specific lock API."""
        try:
            return bool(self.__lock.acquire(timeout=max(0.0, float(timeout))))
        except (TypeError, ValueError):
            try:
                return bool(self.__lock.acquire(False))
            except Exception:
                return False
        except Exception:
            return False

    @staticmethod
    def __close_durable_spool(
            spool: BreadcrumbDurableSpool, timeout: float, final_state: str,
    ) -> Dict[str, Any]:
        """Close a spool while tolerating legacy test doubles/older callers."""
        try:
            return spool.close(timeout, final_state=final_state)
        except TypeError as error:
            # A narrow compatibility path keeps patched/older close methods
            # usable without swallowing TypeErrors raised by the writer.
            if "final_state" not in str(error):
                raise
            return spool.close(timeout)

    def __durable_upstream_health(self) -> Mapping[str, int]:
        # This callback is read by the writer thread and deliberately does not
        # acquire the collector lock (record admission holds collector->spool
        # ordering). Values are monotonic snapshots and may lag by one event.
        return {
            "critical_ingress_enqueued": self.__shared_counter_value(self.__critical_ingress_enqueued_count),
            "normal_ingress_enqueued": self.__shared_counter_value(self.__normal_ingress_enqueued_count),
            "critical_ingress_rejected": self.__shared_counter_value(self.__critical_ingress_rejected_count),
            "normal_ingress_rejected": self.__shared_counter_value(self.__normal_ingress_rejected_count),
            "critical_ingress_overflow": self.__shared_counter_value(self.__critical_ingress_rejected_count),
            "normal_ingress_overflow": self.__shared_counter_value(self.__normal_ingress_rejected_count),
            "critical_ingress_ack": self.__shared_counter_value(self.__critical_ingress_ack_count),
            "normal_ingress_ack": self.__shared_counter_value(self.__normal_ingress_ack_count),
            "critical_ingress_reserved": self.__shared_counter_value(
                self.__critical_ingress_reservation_count,
            ),
            "normal_ingress_reserved": self.__shared_counter_value(
                self.__normal_ingress_reservation_count,
            ),
            "critical_ingress_unknown": self.__shared_counter_value(
                self.__critical_ingress_unknown_count,
            ),
            "normal_ingress_unknown": self.__shared_counter_value(
                self.__normal_ingress_unknown_count,
            ),
            "ingress_reservation_unknown": (
                self.__shared_counter_value(self.__critical_ingress_unknown_count)
                + self.__shared_counter_value(self.__normal_ingress_unknown_count)
            ),
            "collector_accepted": self.__accepted_count,
            "collector_rejected": self.__dropped_count,
            "collector_evicted": self.__evicted_count,
            "collector_durable_rejected": self.__durable_enqueue_rejected_count,
            "ingress_shutdown_leftovers": self.__ingress_shutdown_leftovers,
            "ingress_unresolved": self.__ingress_unresolved,
        }

    def __mark_durable_incomplete(
            self,
            spool: Optional[BreadcrumbDurableSpool],
            deadline: float,
    ) -> Dict[str, Any]:
        """Mark one truthful non-terminal owner snapshot on timeout."""
        unknown = (
            self.__shared_counter_value(self.__critical_ingress_unknown_count)
            + self.__shared_counter_value(self.__normal_ingress_unknown_count)
        )
        unresolved = max(
            self.__shared_counter_value(self.__ingress_inflight_count),
            self.__shared_counter_value(self.__critical_ingress_reservation_count)
            + self.__shared_counter_value(self.__normal_ingress_reservation_count),
            unknown,
        )
        acquired = self.__acquire_collector_lock()
        if acquired:
            try:
                if unresolved > self.__ingress_unresolved_recorded:
                    self.__ingress_unresolved = unresolved
                    self.__ingress_unresolved_recorded = unresolved
                self.__durable_owner_state = "INCOMPLETE"
                unresolved = self.__ingress_unresolved
            finally:
                self.__lock.release()
        else:
            # The durable writer remains the authoritative fallback when the
            # collector cache cannot be locked.  Include the current shared
            # counters directly so the returned health is still truthful.
            unresolved = max(unresolved, self.__ingress_unresolved)
        if spool is None:
            # Reconciliation already owns the lifecycle lock; use the body
            # directly to avoid recursively acquiring the non-reentrant lock.
            snapshot = self.__durable_snapshot_body()
            snapshot.update({
                "state": "INCOMPLETE", "closed": False,
                "terminal": False, "incomplete": True,
                "unresolved": unresolved,
                "unknown": unknown,
            })
            if self.__acquire_collector_lock():
                try:
                    self.__durable_last_snapshot = copy.deepcopy(snapshot)
                    self.__durable_owner_state = "INCOMPLETE"
                finally:
                    self.__lock.release()
            snapshot["owner_state"] = "INCOMPLETE"
            snapshot["ingress_drainer_alive"] = bool(
                self.__ingress_drainer is not None and self.__ingress_drainer.is_alive()
            )
            snapshot["ingress_queues_open"] = bool(
                self.__critical_external_records is not None
                or self.__normal_external_records is not None
            )
            snapshot.update(self.__collector_retirement_status(snapshot))
            return snapshot
        snapshot = spool.mark_incomplete(unresolved, unknown=unknown)
        if snapshot.get("thread_alive"):
            # The timeout marker is caller-side state only.  Give the already
            # live writer one short, bounded opportunity to persist that
            # projection so a close-time failure is not paired with a stale
            # terminal sidecar.  ``snapshot`` performs no filesystem work;
            # all persistence remains inside the writer thread.
            try:
                publish_wait = min(
                    0.05,
                    max(0.0, float(getattr(
                        spool, "shutdown_timeout_seconds", 0.05,
                    ))),
                )
            except (TypeError, ValueError, OverflowError):
                publish_wait = 0.05
            now = time.monotonic()
            publish_deadline = min(now + publish_wait, deadline)
            while time.monotonic() < publish_deadline:
                refreshed = spool.snapshot()
                snapshot = refreshed
                if (
                    not refreshed.get("thread_alive")
                    or refreshed.get("health_publish_ok")
                ):
                    break
                time.sleep(min(0.005, max(0.0, publish_deadline - time.monotonic())))
        if self.__acquire_collector_lock():
            try:
                self.__durable_last_snapshot = copy.deepcopy(snapshot)
                self.__durable_owner_state = "INCOMPLETE"
            finally:
                self.__lock.release()
        snapshot["owner_state"] = "INCOMPLETE"
        snapshot["ingress_drainer_alive"] = bool(
            self.__ingress_drainer is not None and self.__ingress_drainer.is_alive()
        )
        snapshot["ingress_queues_open"] = bool(
            self.__critical_external_records is not None
            or self.__normal_external_records is not None
        )
        snapshot.update(self.__collector_retirement_status(snapshot))
        return snapshot

    def __reconcile_durable_owner(self, final_state: str) -> bool:
        # Freeze all queue admission while retiring the old durable owner.
        # This keeps disable/re-enable single-owner reconciliation bounded and
        # turns concurrent OPEN calls into explicit, accounted loss.
        timeout = DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS
        deadline = time.monotonic() + timeout
        try:
            acquired = bool(self.__ingress_admission_lock.acquire(timeout=timeout))
        except (TypeError, ValueError):
            acquired = bool(self.__ingress_admission_lock.acquire(False))
        except Exception:
            acquired = False
        if not acquired:
            self.__mark_durable_incomplete(self.__durable_spool, deadline)
            return False
        try:
            self.__durable_reconcile_gate.value = 1
        except Exception:
            try:
                self.__ingress_admission_lock.release()
            except Exception:
                pass
            self.__mark_durable_incomplete(self.__durable_spool, deadline)
            return False
        # Existing tokens may need the admission lock to finish their queue
        # handoff, so release it before waiting for quiescence.  The separate
        # durable gate rejects any new token while this owner is reconciled.
        try:
            self.__ingress_admission_lock.release()
        except Exception:
            self.__mark_durable_incomplete(self.__durable_spool, deadline)
            return False
        succeeded = self.__reconcile_durable_owner_unlocked(final_state)
        if succeeded:
            try:
                self.__durable_reconcile_gate.value = 0
            except Exception:
                pass
        return succeeded

    def __reconcile_durable_owner_unlocked(self, final_state: str) -> bool:
        """Drain and retire the sole old owner before disable/re-enable."""
        deadline = time.monotonic() + DEFAULT_BREADCRUMB_DURABLE_SHUTDOWN_TIMEOUT_SECONDS
        drainer = self.__ingress_drainer
        if drainer is not None:
            self.__ingress_drainer_stop.set()
            self.__ingress_drainer_wakeup.set()
            drainer.join(max(0.0, deadline - time.monotonic()))
            if drainer.is_alive():
                self.__mark_durable_incomplete(self.__durable_spool, deadline)
                return False
            with self.__lock:
                if self.__ingress_drainer is drainer:
                    self.__ingress_drainer = None
                    self.__ingress_drainer_active = False
        if not self.__drain_ingress_until_quiescent(deadline):
            self.__mark_durable_incomplete(self.__durable_spool, deadline)
            return False
        # The durable reconciliation gate blocks new emitter/direct tokens,
        # while pre-gate tokens remain free to settle their queue handoff.
        if not self.__wait_for_ingress_tokens(deadline):
            self.__mark_durable_incomplete(self.__durable_spool, deadline)
            return False
        self.__consume_ingress_rejections()
        spool = self.__durable_spool
        unknown_reservations = (
            self.__shared_counter_value(self.__critical_ingress_unknown_count)
            + self.__shared_counter_value(self.__normal_ingress_unknown_count)
        )
        if spool is not None:
            if unknown_reservations:
                spool.mark_incomplete(
                    unresolved=unknown_reservations,
                    unknown=unknown_reservations,
                )
            snapshot = self.__close_durable_spool(
                spool, max(0.0, deadline - time.monotonic()), final_state,
            )
            retired_incomplete = (
                bool(snapshot.get("incomplete"))
                and not bool(snapshot.get("thread_alive"))
                and not bool(snapshot.get("writing"))
                and int(snapshot.get("critical_pending", 0)) == 0
                and int(snapshot.get("normal_pending", 0)) == 0
            )
            if (
                    snapshot.get("thread_alive")
                    or (
                        not snapshot.get("terminal", True) and not retired_incomplete
                    )
            ) and not retired_incomplete:
                self.__mark_durable_incomplete(spool, deadline)
                return False
            if not self.__acquire_collector_lock(max(0.0, deadline - time.monotonic())):
                self.__mark_durable_incomplete(spool, deadline)
                return False
            try:
                self.__durable_last_snapshot = copy.deepcopy(snapshot)
                # A timed-out owner may be safely retired after its writer
                # thread and queues have settled. Preserve its INCOMPLETE
                # projection for retrieval, but detach it before creating the
                # one and only replacement RUNNING owner.
                self.__durable_owner_state = "INCOMPLETE" if retired_incomplete else final_state
                if self.__durable_spool is spool:
                    self.__durable_spool = None
            finally:
                self.__lock.release()
        else:
            if not self.__acquire_collector_lock(max(0.0, deadline - time.monotonic())):
                self.__mark_durable_incomplete(None, deadline)
                return False
            try:
                self.__durable_owner_state = (
                    "INCOMPLETE" if unknown_reservations else final_state
                )
            finally:
                self.__lock.release()
        return True

    def __sync_durable_state(self, enabled: bool) -> None:
        with self.__durable_lifecycle_lock:
            requested = enabled and self.__durable_requested() and self.__durable_path() is not None
            with self.__lock:
                closing = self.__collector_closing or self.__collector_closed
                owner_state = self.__durable_owner_state
                spool_exists = self.__durable_spool is not None or self.__ingress_drainer is not None
            if closing:
                return
            if requested:
                # A timed-out disable remains the owner until its drainer and
                # spool have reconciled. Never start a second owner beside it.
                if spool_exists and owner_state in {"DRAINING", "INCOMPLETE"}:
                    if not self.__reconcile_durable_owner("DISABLED"):
                        return
                with self.__lock:
                    # The old terminal projection is no longer authoritative
                    # once a replacement is requested. Clear it while the
                    # lifecycle lock is held; the new spool publishes its
                    # RUNNING sidecar before becoming the visible owner.
                    if self.__durable_spool is None:
                        self.__durable_last_snapshot = None
                    self.__durable_enabled_state = True
                    self.__durable_owner_state = "DISABLED"
                self.__ensure_durable_workers()
                with self.__lock:
                    if self.__durable_spool is not None:
                        self.__durable_owner_state = "RUNNING"
                return
            with self.__lock:
                durable_enabled = self.__durable_enabled_state
                self.__durable_enabled_state = False
                spool_exists = self.__durable_spool is not None or self.__ingress_drainer is not None
                if spool_exists:
                    self.__durable_owner_state = "DRAINING"
                else:
                    self.__durable_owner_state = "DISABLED"
            if durable_enabled or spool_exists:
                self.__reconcile_durable_owner("DISABLED")

    def __ensure_durable_workers(self) -> None:
        if not self.__durable_enabled_state:
            return
        if self.__durable_spool is None:
            path = self.__durable_path()
            if path is None:
                return
            new_spool = BreadcrumbDurableSpool(
                path,
                total_capacity=BREADCRUMB_DURABLE_TOTAL_CAPACITY,
                critical_capacity=BREADCRUMB_DURABLE_CRITICAL_CAPACITY,
                normal_capacity=BREADCRUMB_DURABLE_NORMAL_CAPACITY,
                upstream_health_getter=self.__durable_upstream_health,
            )
            with self.__lock:
                if self.__durable_spool is None:
                    self.__durable_spool = new_spool
                else:
                    # The lifecycle lock normally makes this impossible, but
                    # never leave a second writer owner running if a legacy
                    # caller races worker setup.
                    new_spool.close(timeout=0.0, final_state="DISABLED")
                    return
        if self.__normal_external_records is None:
            # Local parent records still use the durable writer, while the
            # cross-process drainer starts when the first emitter is created.
            return
        if self.__ingress_drainer is None or not self.__ingress_drainer.is_alive():
            self.__ingress_drainer_stop.clear()
            self.__ingress_drainer_active = True
            self.__ingress_drainer = Thread(
                target=self.__run_ingress_drainer,
                name="breadcrumb-ingress-drainer",
                daemon=True,
            )
            self.__ingress_drainer.start()

    def __run_ingress_drainer(self) -> None:
        # Shared enqueue/ack counters, rather than Queue.empty(), are the
        # shutdown protocol. They remain correct across multiprocessing
        # feeder latency and make both lanes observable.
        while not self.__ingress_drainer_stop.is_set() or self.__ingress_has_records():
            drained = self.__drain_external_records(
                limit=128, force=True, from_drainer=True,
            )
            if drained == 0:
                self.__ingress_drainer_wakeup.wait(0.02)
                self.__ingress_drainer_wakeup.clear()
        # Account for a last child rejection counter update before exit.
        self.__consume_ingress_rejections()
        self.__ingress_drainer_active = False

    def __ingress_queues(self) -> tuple[Optional[Any], Optional[Any]]:
        if self.__critical_external_records is None and self.__normal_external_records is None:
            # Compatibility for tests and pre-lane callers that install the
            # legacy private queue directly.
            return None, self.__external_records
        return self.__critical_external_records, self.__normal_external_records

    def __ingress_pending_count(self) -> int:
        """Return all pending evidence, including unresolved UNKNOWN state."""
        return self.__ingress_known_pending_count() + (
            self.__shared_counter_value(self.__critical_ingress_unknown_count)
            + self.__shared_counter_value(self.__normal_ingress_unknown_count)
        )

    def __ingress_known_pending_count(self) -> int:
        """Return drainable queue/reservation work, excluding UNKNOWN state."""
        critical, normal = self.__ingress_queues()
        if critical is None and normal is None:
            return 0
        return (
            max(
                0,
                self.__shared_counter_value(self.__critical_ingress_enqueued_count)
                - self.__shared_counter_value(self.__critical_ingress_ack_count),
            )
            + self.__shared_counter_value(self.__critical_ingress_reservation_count)
            + max(
                0,
                self.__shared_counter_value(self.__normal_ingress_enqueued_count)
                - self.__shared_counter_value(self.__normal_ingress_ack_count),
            )
            + self.__shared_counter_value(self.__normal_ingress_reservation_count)
        )

    def __ingress_has_records(self) -> bool:
        if self.__ingress_queues() == (None, None):
            # Legacy test doubles are drained opportunistically by get_nowait;
            # no multiprocessing Queue.empty() result is used for shutdown.
            return False
        return self.__ingress_known_pending_count() > 0

    def is_enabled(self) -> bool:
        enabled = self.__read_enabled_state()
        self.__set_enabled_gate(enabled)
        return enabled

    def is_effectively_enabled(self, category: object, level: object = "info") -> bool:
        """Return the system/category/level gate before constructing a breadcrumb."""
        return self.is_enabled() and self.__effective_policy.allows(category, level)

    def is_explicitly_configured(self, category: object) -> bool:
        """Return whether ``category`` has an explicit policy rule."""
        return self.is_enabled() and self.__effective_policy.has_explicit_rule(category)

    def sync_enabled_state(self) -> bool:
        enabled = self.__read_enabled_state()
        self.__set_enabled_gate(enabled)
        self.__sync_durable_state(enabled)
        return enabled

    @property
    def max_entries(self) -> int:
        return self.__max_entries

    @property
    def version(self) -> int:
        return self.__version

    def trace_generation(self) -> int:
        """Return the retention generation for producer-side dedupe state."""
        with self.__lock:
            return self.__reset_generation

    def record_root_progress_health(
            self,
            outcome: object,
            stage: object,
            job_digest: object = None,
            details: object = None,
            count_attempt: bool = True,
    ) -> bool:
        return bool(self.__run_direct_mutation(
            lambda: self.__record_root_progress_health_body(
                outcome, stage, job_digest, details, count_attempt,
            ),
            lambda: False,
        ))

    def __record_root_progress_health_body(
            self,
            outcome: object,
            stage: object,
            job_digest: object = None,
            details: object = None,
            count_attempt: bool = True,
    ) -> bool:
        """Retain bounded root-progress admission health outside the event deque."""
        if not isinstance(outcome, str) or outcome not in self.__ROOT_PROGRESS_HEALTH_OUTCOMES:
            return False
        normalized_stage = stage if isinstance(stage, str) and stage in self.__ROOT_PROGRESS_HEALTH_STAGES else "other"
        normalized_digest = job_digest if isinstance(job_digest, str) else "unknown"
        if normalized_digest != "unknown":
            digest = normalized_digest.removeprefix("root-progress:")
            if len(digest) != 16 or any(character not in "0123456789abcdef" for character in digest):
                normalized_digest = "unknown"
            else:
                normalized_digest = digest
        safe_details: Dict[str, Any] = {}
        if isinstance(details, Mapping):
            try:
                for index, (key, value) in enumerate(details.items()):
                    if index >= self.__MAX_MAPPING_ITEMS:
                        break
                    if not isinstance(key, str) or key not in self.__ROOT_PROGRESS_HEALTH_DETAIL_KEYS:
                        continue
                    if value is None or type(value) is bool:
                        safe_details[str(key)] = value
                    elif type(value) is int:
                        safe_details[str(key)] = max(-2_147_483_648, min(2_147_483_647, value))
                    elif type(value) is float and math.isfinite(value):
                        safe_details[str(key)] = value
                    elif type(value) is str:
                        safe_details[str(key)] = self.__truncate_string(
                            self.__sanitize_string_content(value),
                        )
            except Exception:
                return False
        representation = safe_details.get("representation")
        if representation not in self.__ROOT_PROGRESS_HEALTH_REPRESENTATIONS:
            representation = None
        now_ms = int(time.time_ns() / 1_000_000)
        latest = {
            "outcome": outcome,
            "stage": normalized_stage,
            "job_digest": normalized_digest,
            "observed_ms": now_ms,
            "details": safe_details,
        }
        with self.__lock:
            if count_attempt:
                self.__root_progress_health["attempt_count"] = min(
                    self.__ROOT_PROGRESS_HEALTH_MAX_COUNTER,
                    int(self.__root_progress_health["attempt_count"]) + 1,
                )
            counts = self.__root_progress_health["outcome_counts"]
            counts[outcome] = min(
                self.__ROOT_PROGRESS_HEALTH_MAX_COUNTER,
                int(counts.get(outcome, 0)) + 1,
            )
            if outcome == "summary_observed":
                self.__root_progress_health["latest_summary"] = latest
            else:
                self.__root_progress_health["latest"] = latest
            if representation is not None:
                by_representation = self.__root_progress_health["latest_by_representation"]
                if representation not in by_representation and len(by_representation) >= self.__ROOT_PROGRESS_HEALTH_MAX_REPRESENTATIONS:
                    representation = "other"
                by_representation[representation] = latest
            return True

    def __root_progress_health_snapshot(self) -> Dict[str, Any]:
        health = copy.deepcopy(self.__root_progress_health)
        health["enabled"] = self.is_effectively_enabled("model.progress", "debug")
        return health

    def record_progress_lineage(
            self, correlation: object, phase: object, details: object = None,
    ) -> bool:
        return bool(self.__run_direct_mutation(
            lambda: self.__record_progress_lineage_body(correlation, phase, details),
            lambda: False,
        ))

    def __record_progress_lineage_body(
            self, correlation: object, phase: object, details: object = None,
    ) -> bool:
        """Append one opaque active-progress causal step outside event retention.

        Callers pass only fixed enums/buckets.  The collector owns timestamping,
        bounds, and snapshot lifetime so a normal breadcrumb deque eviction
        cannot erase an otherwise complete status-to-stream lineage.
        """
        if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
            return False
        if not self.is_effectively_enabled("model.progress", "debug"):
            return False
        health = self.__progress_lineage_health_for_write()
        health["attempt_count"] += 1
        if not isinstance(correlation, str) or not correlation.startswith("lftp-poll:") or \
                len(correlation) != len("lftp-poll:") + 16:
            health["reject_counts"]["invalid_correlation"] += 1
            return False
        if not isinstance(phase, str) or phase not in self.__PROGRESS_LINEAGE_PHASES:
            health["reject_counts"]["invalid_phase"] += 1
            return False
        health["phase_calls"][phase] += 1
        suffix = correlation[len("lftp-poll:"):]
        if any(character not in "0123456789abcdef" for character in suffix):
            health["reject_counts"]["invalid_correlation"] += 1
            return False
        safe: Dict[str, Any] = {}
        try:
            if isinstance(details, Mapping):
                for index, (key, value) in enumerate(details.items()):
                    if index >= self.__MAX_MAPPING_ITEMS:
                        break
                    if not isinstance(key, str) or key not in self.__PROGRESS_LINEAGE_DETAIL_KEYS:
                        continue
                    if value is None or type(value) is bool:
                        safe[key] = value
                    elif type(value) is int and key in {
                            "model_version", "scope_version", "model_version_first", "model_version_last",
                            "lifecycle_epoch",
                    } and value >= 0:
                        safe[key] = value
                    elif key == "job_correlation" and isinstance(value, str) and \
                            value.startswith("lftp-job:") and len(value) == len("lftp-job:") + 16 and \
                            all(character in "0123456789abcdef" for character in value[len("lftp-job:"):]):
                        safe[key] = value
                    elif key == "target_correlation" and isinstance(value, str) and \
                            value.startswith("model-target:") and len(value) == len("model-target:") + 16 and \
                            all(character in "0123456789abcdef" for character in value[len("model-target:"):]):
                        safe[key] = value
                    elif isinstance(value, str) and value in self.__PROGRESS_LINEAGE_ENUMS.get(key, frozenset()):
                        safe[key] = value
        except Exception:
            health["reject_counts"]["details_error"] += 1
            return False
        step = {"phase": phase, "monotonic_ms": time.monotonic_ns() // 1_000_000, "details": safe}
        with self.__lock:
            span = self.__progress_lineage.get(correlation)
            if span is None:
                span = {"correlation": correlation, "steps": []}
                self.__progress_lineage[correlation] = span
                health["spans_created"] += 1
            else:
                self.__progress_lineage.move_to_end(correlation)
            steps = span["steps"]
            if phase == "scoped_stream_emit":
                stream_step = next((candidate for candidate in steps if candidate["phase"] == "scoped_stream_emit"), None)
                if stream_step is not None:
                    count = min(128, int(span.get("scoped_stream_emit_count", 1)) + 1)
                    span["scoped_stream_emit_count"] = count
                    stream_step["details"].update(safe)
                    stream_step["details"]["scoped_stream_count_bucket"] = self.__progress_lineage_count_bucket(count)
                    stream_step["monotonic_ms"] = step["monotonic_ms"]
                    health["phase_accepted"][phase] += 1
                    return True
            if phase == "updater_decision":
                decision_step = next((candidate for candidate in steps if candidate["phase"] == phase), None)
                if decision_step is not None:
                    decision_step["details"].update(safe)
                    decision_step["monotonic_ms"] = step["monotonic_ms"]
                    health["phase_accepted"][phase] += 1
                    return True
            if phase == "model_mutation" and safe.get("outcome") == "mutated" and \
                    type(safe.get("model_version")) is int:
                version = safe["model_version"]
                ranges = span.setdefault("mutation_version_ranges", [])
                if not ranges or version != ranges[-1][1] + 1:
                    if len(ranges) >= self.__PROGRESS_LINEAGE_MAX_VERSION_RANGES:
                        span["mutation_ranges_truncated"] = True
                        span["mutation_ranges_omitted_count"] = min(
                            2_147_483_647,
                            int(span.get("mutation_ranges_omitted_count", 0)) + 1,
                        )
                        health["reject_counts"]["mutation_range_truncated"] += 1
                        return False
                    ranges.append([version, version])
                else:
                    ranges[-1][1] = version
                mutation_step = next((candidate for candidate in steps if candidate["phase"] == "model_mutation"), None)
                if mutation_step is not None:
                    details = mutation_step["details"]
                    details["model_version"] = version
                    details["model_version_first"] = ranges[0][0]
                    details["model_version_last"] = version
                    details["mutation_count_bucket"] = self.__progress_lineage_count_bucket(
                        sum((end - start) + 1 for start, end in ranges)
                    )
                    mutation_step["monotonic_ms"] = step["monotonic_ms"]
                    health["phase_accepted"][phase] += 1
                    return True
                safe["model_version_first"] = version
                safe["model_version_last"] = version
                safe["mutation_count_bucket"] = "1"
            if len(steps) >= self.__PROGRESS_LINEAGE_MAX_STEPS:
                del steps[0]
            steps.append(step)
            if phase == "scoped_stream_emit":
                span["scoped_stream_emit_count"] = 1
                step["details"]["scoped_stream_count_bucket"] = "1"
            while len(self.__progress_lineage) > self.__PROGRESS_LINEAGE_MAX_SPANS:
                self.__progress_lineage.popitem(last=False)
                health["spans_evicted"] += 1
            health["phase_accepted"][phase] += 1
        return True

    def record_progress_lineage_for_model_version(
            self, model_version: object, phase: object, details: object = None,
    ) -> bool:
        return bool(self.__run_direct_mutation(
            lambda: self.__record_progress_lineage_for_model_version_body(
                model_version, phase, details,
            ),
            lambda: False,
        ))

    def __record_progress_lineage_for_model_version_body(
            self, model_version: object, phase: object, details: object = None,
    ) -> bool:
        """Atomically append a stream step to its exact mutation lineage."""
        if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
            return False
        if not self.is_effectively_enabled("model.progress", "debug") or type(model_version) is not int:
            return False
        with self.__lock:
            correlation = next((key for key, span in reversed(self.__progress_lineage.items())
                                if any(start <= model_version <= end
                                       for start, end in span.get("mutation_version_ranges", ()))), None)
            if correlation is None:
                health = self.__progress_lineage_health_for_write()
                health["attempt_count"] += 1
                health["phase_calls"]["scoped_stream_emit"] += 1
                health["reject_counts"]["unmapped_model_version"] += 1
                return False
            return self.__record_progress_lineage_locked(correlation, phase, details)

    def __record_progress_lineage_locked(
            self, correlation: str, phase: object, details: object,
    ) -> bool:
        """Reuse the public validator while retaining mapping selection atomicity."""
        # RLock permits the public writer's lock acquisition; keep the one
        # lookup/append operation indivisible from clear and span eviction.
        return self.__record_progress_lineage_body(correlation, phase, details)

    @staticmethod
    def __progress_lineage_count_bucket(count: int) -> str:
        if count <= 1:
            return "1"
        if count <= 4:
            return "2-4"
        if count <= 16:
            return "5-16"
        if count <= 32:
            return "17-32"
        return "33+"

    def __progress_lineage_snapshot(self) -> Dict[str, Any]:
        return {
            "schema": "model_progress_lineage.v1",
            "enabled": self.is_effectively_enabled("model.progress", "debug"),
            "max_spans": self.__PROGRESS_LINEAGE_MAX_SPANS,
            "max_steps_per_span": self.__PROGRESS_LINEAGE_MAX_STEPS,
            "max_version_ranges_per_span": self.__PROGRESS_LINEAGE_MAX_VERSION_RANGES,
            "spans": copy.deepcopy(list(self.__progress_lineage.values())),
        }

    def __progress_lineage_health_for_write(self) -> Dict[str, Any]:
        """Lazily allocate fixed admission accounting after the debug gate."""
        health = self.__progress_lineage_health
        if health is None:
            health = {
                "schema": "model_progress_lineage_health.v1",
                "attempt_count": 0,
                "phase_calls": {phase: 0 for phase in self.__PROGRESS_LINEAGE_PHASES},
                "phase_accepted": {phase: 0 for phase in self.__PROGRESS_LINEAGE_PHASES},
                "reject_counts": {
                    "invalid_correlation": 0,
                    "invalid_phase": 0,
                    "details_error": 0,
                    "mutation_range_truncated": 0,
                    "unmapped_model_version": 0,
                },
                "spans_created": 0,
                "spans_evicted": 0,
            }
            self.__progress_lineage_health = health
        return health

    def __progress_lineage_health_snapshot(self) -> Dict[str, Any]:
        health = self.__progress_lineage_health
        if health is None:
            health = {
                "schema": "model_progress_lineage_health.v1",
                "attempt_count": 0,
                "phase_calls": {phase: 0 for phase in self.__PROGRESS_LINEAGE_PHASES},
                "phase_accepted": {phase: 0 for phase in self.__PROGRESS_LINEAGE_PHASES},
                "reject_counts": {
                    "invalid_correlation": 0,
                    "invalid_phase": 0,
                    "details_error": 0,
                    "mutation_range_truncated": 0,
                    "unmapped_model_version": 0,
                },
                "spans_created": 0,
                "spans_evicted": 0,
            }
        snapshot = copy.deepcopy(health)
        snapshot["enabled"] = self.is_effectively_enabled("model.progress", "debug")
        snapshot["lineage_resets"] = self.__progress_lineage_reset_count
        return snapshot

    def __reset_root_progress_health_locked(self) -> None:
        self.__root_progress_health = {
            "schema": "model_root_progress_health.v1",
            "attempt_semantics": "one_per_root_progress_attempt",
            "attempt_count": 0,
            "outcome_counts": {
                outcome: 0 for outcome in (
                    "trace_policy_disabled", "tracer_deduplicated", "emitter_rejected",
                    "emitter_enqueued", "collector_accepted", "collector_evicted",
                    "collector_rejected", "summary_observed",
                )
            },
            "latest": None,
            "latest_summary": None,
            "latest_by_representation": {},
        }
        self.__progress_lineage.clear()
        self.__progress_lineage_reset_count = min(
            self.__ROOT_PROGRESS_HEALTH_MAX_COUNTER, self.__progress_lineage_reset_count + 1,
        )

    @staticmethod
    def __clear_scope_includes_root_progress(clear_filters: Mapping[str, Any]) -> bool:
        category = clear_filters.get("category")
        if category == "model.progress":
            return True
        category_prefix = clear_filters.get("category_prefix")
        if isinstance(category_prefix, str) and "model.progress".startswith(category_prefix):
            return True
        return clear_filters.get("source") == "root_progress"

    @property
    def memory_budget_bytes(self) -> int:
        return self.__memory_budget_bytes

    @property
    def retained_bytes(self) -> int:
        with self.__lock:
            return self.__retained_bytes

    def capabilities(self) -> Dict[str, Any]:
        """Return the small, stable service contract used by handlers."""
        durable_path = self.__durable_path()
        with self.__lock:
            return {
                "schema": "breadcrumb-trace",
                "levels": list(BREADCRUMB_POLICY_LEVELS),
                "policy": True,
                "policy_revision": self.__policy_revision,
                "query": True,
                "export": True,
                "scoped_clear": True,
                "sanitization": True,
                "memory_budget_bytes": self.__memory_budget_bytes,
                "max_memory_bytes": self.__memory_budget_bytes,
                "max_entries": self.__max_entries,
                "unlimited_count": self.__max_entries is None,
                "ingress_record_max_bytes": _INGRESS_RECORD_MAX_BYTES,
                "worker_policy_propagation": "shared_revision_gate",
                # This is intentionally a direct-retrieval contract, not a
                # second API/export surface. Context supplies the fixed
                # app-owned ``<logdir>/breadcrumbs`` mount; when no logdir is
                # configured the state is explicitly unavailable.
                "durable_retrieval": {
                    "offline": os.name != "nt",
                    "api_independent": os.name != "nt",
                    "mode": "fixed_app_owned_mounted_path",
                    "availability": (
                        "configured_path"
                        if durable_path and os.name != "nt" else "unavailable"
                    ),
                    "platform": (
                        "trusted_linux_path_checks"
                        if os.name != "nt" else "windows_acl_unverified"
                    ),
                    "unsupported_reason": (
                        None if os.name != "nt" else "windows_acl_unverified"
                    ),
                    "jsonl_name": "breadcrumbs.jsonl",
                    "health_name": _DURABLE_HEALTH_NAME,
                    "invalid_marker_name": _DURABLE_HEALTH_INVALID_MARKER,
                    "session_boundary_schema": _DURABLE_SESSION_BOUNDARY_SCHEMA,
                    "runtime_validity_field": "health.offline_retrieval",
                    "boundary_commit": "independent_fsync_before_records",
                    "failure_semantics": (
                        "if boundary, health, and invalidation publication all "
                        "fail, offline retrieval is unavailable; existing files "
                        "must not be inferred current"
                    ),
                    "manifest": "health_sidecar+invalidation_marker+session_boundary",
                    "reader_rule": (
                        "require matching session_id, offline_retrieval == valid, "
                        "terminal-or-incomplete health, health_publish_ok, and no "
                        "invalidation marker; a committed session_boundary record "
                        "must precede ordinary records, and newest JSONL session_id "
                        "must match health; unavailable, unsupported, or missing "
                        "boundary is reject"
                    ),
                    "max_files": BREADCRUMB_DURABLE_MAX_FILES,
                    "max_bytes": BREADCRUMB_DURABLE_MAX_BYTES,
                },
            }

    def policy_snapshot(self) -> Dict[str, Any]:
        with self.__lock:
            policy = copy.deepcopy(self.__policy)
            return {
                "revision": self.__policy_revision,
                "policy_revision": self.__policy_revision,
                "policy": policy,
                "default": policy["default"],
                "default_level": policy["default"],
                "rules": copy.deepcopy(policy["rules"]),
                "retention": copy.deepcopy(policy["retention"]),
                "effective_retention": self.__effective_retention_policy(),
                "levels": list(BREADCRUMB_POLICY_LEVELS),
                "worker_propagation": self.__worker_policy_status(),
                "persistence": {
                    "source": "configured" if self.__policy_persist else "runtime_only",
                    "state": self.__policy_persistence_state,
                },
            }

    def validate_policy(self, candidate: object) -> Dict[str, Any]:
        """Validate without changing the active policy or its revision."""
        return self.__validate_policy_candidate(candidate)

    def apply_policy(
            self,
            candidate: object,
            expected_revision: Optional[int] = None,
            persist: bool = False,
    ) -> Dict[str, Any]:
        return cast(Dict[str, Any], self.__run_direct_mutation(
            lambda: self.__apply_policy_body(candidate, expected_revision, persist),
            lambda: self.__closed_policy_result(),
        ))

    def __apply_policy_body(
            self,
            candidate: object,
            expected_revision: Optional[int] = None,
            persist: bool = False,
    ) -> Dict[str, Any]:
        if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
            response = self.policy_snapshot()
            response.update({"applied": False, "ok": False, "conflict": False, "closed": True})
            return response
        result = self.__validate_policy_candidate(candidate)
        if not bool(result["valid"]):
            raise ValueError("invalid breadcrumb policy: {}".format(result["errors"]))
        with self.__lock:
            if expected_revision is not None and expected_revision != self.__policy_revision:
                response = self.policy_snapshot()
                response.update({"applied": False, "ok": False, "conflict": True})
                return response
            old_policy = self.__policy
            old_revision = self.__policy_revision
            self.__policy = cast(Dict[str, Any], result["policy"])
            self.__effective_policy = _EffectiveBreadcrumbPolicy.from_policy(self.__policy)
            self.__policy_revision += 1
            self.__policy_epoch += 1
            self.__publish_effective_policy()
            with self.__worker_policy_revision.get_lock():
                self.__worker_policy_revision.value = self.__policy_revision
            with self.__worker_policy_epoch.get_lock():
                self.__worker_policy_epoch.value = self.__policy_epoch
            if persist:
                if self.__policy_persist is None:
                    self.__policy = old_policy
                    self.__effective_policy = _EffectiveBreadcrumbPolicy.from_policy(self.__policy)
                    self.__policy_revision = old_revision
                    self.__policy_epoch += 1
                    self.__publish_effective_policy()
                    with self.__worker_policy_revision.get_lock():
                        self.__worker_policy_revision.value = old_revision
                    with self.__worker_policy_epoch.get_lock():
                        self.__worker_policy_epoch.value = self.__policy_epoch
                    self.__policy_persistence_state = "unavailable"
                    raise ValueError("breadcrumb policy persistence is unavailable")
                try:
                    self.__policy_persist(copy.deepcopy(self.__policy))
                    self.__policy_persistence_state = "persisted"
                except Exception:
                    self.__policy = old_policy
                    self.__effective_policy = _EffectiveBreadcrumbPolicy.from_policy(self.__policy)
                    self.__policy_revision = old_revision
                    self.__policy_epoch += 1
                    self.__publish_effective_policy()
                    with self.__worker_policy_revision.get_lock():
                        self.__worker_policy_revision.value = old_revision
                    with self.__worker_policy_epoch.get_lock():
                        self.__worker_policy_epoch.value = self.__policy_epoch
                    self.__policy_persistence_state = "failed_rolled_back"
                    raise
            else:
                self.__policy_persistence_state = "runtime_only"
            response = self.policy_snapshot()
            response.update({"applied": True, "ok": True, "conflict": False})
            return response

    def __closed_policy_result(self) -> Dict[str, Any]:
        response = self.policy_snapshot()
        response.update({"applied": False, "ok": False, "conflict": False, "closed": True})
        return response

    def reset_policy(self, expected_revision: Optional[int] = None, persist: bool = False) -> Dict[str, Any]:
        """Restore the default policy, leaving retained evidence untouched."""
        return self.apply_policy({}, expected_revision=expected_revision, persist=persist)

    def __validate_policy_candidate(self, candidate: object) -> Dict[str, Any]:
        errors: List[str] = []
        if candidate is None:
            candidate_mapping: Mapping[object, object] = {}
        elif isinstance(candidate, str):
            if len(candidate) > _SHARED_POLICY_MAX_BYTES:
                return {"valid": False, "ok": False, "errors": ["policy is too large"], "policy": None}
            compact_rules: Dict[str, str] = {}
            compact_default: Optional[str] = None
            for item in candidate.split(","):
                if "=" not in item:
                    return {"valid": False, "ok": False, "errors": ["policy entries must use category=level"], "policy": None}
                category, raw_level = (part.strip() for part in item.split("=", 1))
                if category in {"*", "default", "default_level"}:
                    compact_default = raw_level
                else:
                    compact_rules[category] = raw_level
            candidate_mapping = {"default": compact_default or "info", "rules": compact_rules}
        elif isinstance(candidate, Mapping):
            candidate_mapping = candidate
        else:
            return {"valid": False, "ok": False, "errors": ["policy must be a mapping"], "policy": None}

        default_value = candidate_mapping.get(
            "default",
            candidate_mapping.get("default_level", DEFAULT_BREADCRUMB_POLICY_LEVEL),
        )
        default_level = self.__normalize_level(default_value)
        if default_level is None:
            errors.append("default level must be one of {}".format(", ".join(BREADCRUMB_POLICY_LEVELS)))

        raw_rules: object = candidate_mapping.get("rules", candidate_mapping.get("categories"))
        if raw_rules is None:
            # A compact policy may be supplied directly as {"transfer": "debug"}.
            compact_rules: Dict[object, object] = {}
            for index, (key, value) in enumerate(candidate_mapping.items()):
                if index >= 256:
                    break
                if key not in {"default", "default_level", "rules", "categories", "retention"}:
                    compact_rules[key] = value
            raw_rules = compact_rules
        if not isinstance(raw_rules, Mapping):
            errors.append("rules must be a mapping")
            raw_rules = {}

        rules: Dict[str, str] = {}
        for index, (raw_category, raw_level) in enumerate(raw_rules.items()):
            if index >= 256:
                errors.append("too many policy rules")
                break
            if not isinstance(raw_category, str) or len(raw_category) > self.__MAX_DETAIL_KEY_LENGTH or not raw_category.strip():
                errors.append("rule categories must be non-empty strings")
                continue
            category = raw_category.strip()
            if isinstance(raw_level, Mapping):
                raw_level = raw_level.get("level", raw_level.get("value"))
            level = self.__normalize_level(raw_level)
            if level is None:
                errors.append("invalid level for category {}".format(category))
                continue
            rules[category] = level

        retention_value = candidate_mapping.get("retention", {})
        if retention_value is None:
            retention_value = {}
        if not isinstance(retention_value, Mapping):
            errors.append("retention must be a mapping")
            retention_value = {}
        protected_categories = retention_value.get("protected_categories", [])
        if (
            not isinstance(protected_categories, list)
            or len(protected_categories) > 256
            or not all(isinstance(item, str) and len(item) <= self.__MAX_DETAIL_KEY_LENGTH and item.strip() for item in protected_categories)
        ):
            errors.append("retention protected_categories must be a list of non-empty strings")
            protected_categories = []
        decision_types = retention_value.get("latest_decision_event_types", ["decision", "state_transition"])
        if (
            not isinstance(decision_types, list)
            or len(decision_types) > 256
            or not all(isinstance(item, str) and len(item) <= self.__MAX_DETAIL_KEY_LENGTH and item.strip() for item in decision_types)
        ):
            errors.append("retention latest_decision_event_types must be a list of non-empty strings")
            decision_types = ["decision", "state_transition"]
        reserve_fraction = retention_value.get("protected_reserve_fraction", 0.10)
        if type(reserve_fraction) not in (int, float) or not 0 <= reserve_fraction <= 1:
            errors.append("retention protected_reserve_fraction must be between 0 and 1")
            reserve_fraction = 0.10
        normalized = {
            "default": default_level or DEFAULT_BREADCRUMB_POLICY_LEVEL,
            "rules": dict(sorted(rules.items())),
            "retention": {
                "protected_categories": sorted(set(protected_categories)),
                "protected_levels": ["error", "warning"],
                "latest_decision_event_types": sorted(set(decision_types)),
                "protected_reserve_fraction": float(reserve_fraction),
            },
        }
        worker_payload = json.dumps(
            {"default": normalized["default"], "rules": normalized["rules"]},
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        if len(worker_payload) > _SHARED_POLICY_MAX_BYTES:
            errors.append("policy is too large for worker propagation")
        return {"valid": not errors, "ok": not errors, "errors": errors, "policy": normalized}

    @staticmethod
    def __normalize_level(value: object) -> Optional[str]:
        if not isinstance(value, str):
            return None
        if len(value) > 32:
            return None
        level = value.strip().lower()
        return level if level in BREADCRUMB_POLICY_LEVELS else None

    def __resolved_level(self, category: str) -> str:
        with self.__lock:
            policy = self.__policy
            best_score = -1
            selected = str(policy["default"])
            category_parts = category.split(".") if category else [""]
            for pattern, level in policy["rules"].items():
                score = self.__category_match_score(pattern, category, category_parts)
                if score > best_score:
                    best_score = score
                    selected = level
            return selected

    @staticmethod
    def __category_match_score(pattern: str, category: str, category_parts: List[str]) -> int:
        pattern = pattern.strip()
        if pattern == "*":
            return 0
        pattern_parts = pattern.split(".")
        if len(pattern_parts) > len(category_parts):
            return -1
        for index, pattern_part in enumerate(pattern_parts):
            if pattern_part == "*":
                continue
            if pattern_part != category_parts[index]:
                return -1
        # A rule for a parent category is inherited by descendants.  A
        # trailing wildcard is explicit but has the same useful inheritance.
        return len(pattern_parts) * 10 + (5 if len(pattern_parts) == len(category_parts) else 0)

    def __policy_allows(self, category: str, level: str) -> bool:
        return self.__effective_policy.allows(category, level)

    def __effective_retention_policy(self) -> Dict[str, Any]:
        requested = self.__policy["retention"]
        return {
            "requested": copy.deepcopy(requested),
            "protected_reserve_bytes": int(self.__memory_budget_bytes * requested["protected_reserve_fraction"]),
            "protected_reserve_is_preference": True,
            "eviction": "overrepresented_unprotected_category_then_oldest",
        }

    def __category_counter(self, entry: Mapping[str, Any]) -> Dict[str, int]:
        category = str(entry.get("category") or "unknown")
        # Accounting cannot become a second unbounded retention store. The
        # cardinality derives from the configured byte budget, not a hidden
        # event cap; overflow is still explicitly represented.
        category_limit = max(16, self.__memory_budget_bytes // 4096)
        if category not in self.__category_accounting and len(self.__category_accounting) >= category_limit:
            category = "__other_categories__"
        return self.__category_accounting.setdefault(category, {
            "admitted": 0, "coalesced": 0, "policy_dropped": 0, "oversized_dropped": 0,
            "evicted": 0, "closed_dropped": 0,
        })

    def __category_accounting_snapshot(self) -> Dict[str, Dict[str, int]]:
        result = copy.deepcopy(self.__category_accounting)
        response_limit = max(16, min(256, self.__memory_budget_bytes // 4096))
        for entry, size in zip(self.__entries, self.__entry_sizes):
            # Keep retained values snapshot-local rather than accumulating
            # stale retained bytes across evictions and clears.
            category = str(entry.get("category") or "unknown")
            if category not in result and len(result) >= response_limit:
                category = "__other_categories__"
            current = result.setdefault(category, {
                "admitted": 0, "coalesced": 0, "policy_dropped": 0, "oversized_dropped": 0, "evicted": 0,
                "closed_dropped": 0,
            })
            current["retained_count"] = current.get("retained_count", 0) + 1
            current["retained_bytes"] = current.get("retained_bytes", 0) + size
        for counter in result.values():
            counter.setdefault("retained_count", 0)
            counter.setdefault("retained_bytes", 0)
        if len(result) > response_limit:
            ranked = sorted(result, key=lambda category: (
                result[category].get("retained_bytes", 0) + result[category].get("admitted", 0), category
            ), reverse=True)
            overflow = ranked[response_limit:]
            kept = {category: result[category] for category in ranked[:response_limit]}
            aggregate: Dict[str, int] = {}
            for category in overflow:
                for key, value in result[category].items():
                    aggregate[key] = aggregate.get(key, 0) + value
            kept["__other_categories__"] = aggregate
            return kept
        return result

    @staticmethod
    def __filtered_category_accounting(entries: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for entry in entries:
            category = str(entry.get("category") or "unknown")
            result[category] = result.get(category, 0) + 1
        return result

    def __is_category_protected(self, category: str) -> bool:
        return any(self.__category_match_score(pattern, category, category.split(".")) >= 0
                   for pattern in self.__policy["retention"]["protected_categories"])

    def __protected_indices(
        self,
        entries: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> set[int]:
        """Return protected entry indices for one lock-held retention pass.

        ``entries`` may be the immutable tuple snapshot already made by the
        eviction caller.  Keeping the optional default preserves compatibility
        for callers that inspect the collector's live entries directly.
        """
        retention = self.__policy["retention"]
        entries_to_scan = self.__entries if entries is None else entries
        latest_decision: Dict[str, int] = {}
        protected = set()
        protected_category_cache: Dict[str, bool] = {}
        for index, entry in enumerate(entries_to_scan):
            if entry.get("event_type") in retention["latest_decision_event_types"]:
                latest_decision[str(entry.get("category") or "unknown")] = index
            if entry.get("level") in retention["protected_levels"]:
                protected.add(index)
                continue
            category = str(entry.get("category") or "")
            if category not in protected_category_cache:
                protected_category_cache[category] = self.__is_category_protected(category)
            if protected_category_cache[category]:
                protected.add(index)
        protected.update(latest_decision.values())
        return protected

    def __read_enabled_state(self) -> bool:
        try:
            return bool(self.__enabled_getter())
        except Exception:
            return False

    def __set_enabled_gate(self, enabled: bool) -> None:
        try:
            self.__enabled_gate.value = 1 if enabled else 0
        except Exception:
            pass

    def __publish_effective_policy(self) -> None:
        """Publish a compact policy copy for spawned emitters.

        Writers hold ``__lock``. Readers use a seqlock-style generation and
        only decode this payload after a policy update, never per event.
        """
        payload = json.dumps(
            {"default": self.__policy["default"], "rules": self.__policy["rules"]},
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        if len(payload) > _SHARED_POLICY_MAX_BYTES:
            raise ValueError("breadcrumb policy is too large for worker propagation")
        generation = int(self.__worker_policy_generation.value)
        if generation % 2 == 0:
            generation += 1
        self.__worker_policy_generation.value = generation
        self.__worker_policy_bytes[:len(payload)] = payload
        self.__worker_policy_length.value = len(payload)
        self.__worker_policy_generation.value = generation + 1

    def __worker_policy_status(self) -> Dict[str, Any]:
        return {
            "mode": "shared_revision_gate",
            "target_revision": self.__policy_revision,
            "target_epoch": self.__policy_epoch,
            "ack_revision": self.__worker_policy_ack_revision,
            "ack_epoch": self.__worker_policy_ack_epoch,
            "current_epoch": self.__policy_epoch,
            "pending": self.__worker_policy_ack_epoch != self.__policy_epoch,
            "convergence": "not_globally_observed",
        }

    def __consume_ingress_rejections(self) -> None:
        with self.__lock:
            try:
                with self.__ingress_rejected_count.get_lock():
                    rejected = int(self.__ingress_rejected_count.value)
            except Exception:
                rejected = self.__ingress_rejected_seen
            delta = rejected - self.__ingress_rejected_seen
            if delta > 0:
                start = self.__version + 1
                self.__version += delta
                self.__dropped_count += delta
                self.__record_gap_range(start, self.__version, "ingress_rejected")
                self.__ingress_rejected_seen = rejected
            for counter, seen_name, total_name in (
                (
                    self.__critical_ingress_rejected_count,
                    "_BreadcrumbTraceCollector__critical_ingress_rejected_seen",
                    "_BreadcrumbTraceCollector__critical_ingress_rejected_total",
                ),
                (
                    self.__normal_ingress_rejected_count,
                    "_BreadcrumbTraceCollector__normal_ingress_rejected_seen",
                    "_BreadcrumbTraceCollector__normal_ingress_rejected_total",
                ),
            ):
                try:
                    with counter.get_lock():
                        current = int(counter.value)
                except Exception:
                    continue
                previous = int(getattr(self, seen_name))
                if current > previous:
                    setattr(self, seen_name, current)
                    setattr(self, total_name, getattr(self, total_name) + current - previous)
            self.__consume_root_progress_counter_locked(
                "emitter_rejected", self.__root_progress_rejected_count,
            )
            self.__consume_root_progress_counter_locked(
                "emitter_enqueued", self.__root_progress_enqueued_count,
            )
            self.__consume_root_progress_counter_locked(
                "trace_policy_disabled", self.__root_progress_disabled_count,
            )
            self.__consume_root_progress_counter_locked(
                "tracer_deduplicated", self.__root_progress_deduplicated_count,
            )
            self.__consume_root_progress_counter_locked(
                "summary_observed", self.__root_progress_summary_count,
                count_attempt=False,
            )

    def __consume_root_progress_counter_locked(
            self, outcome: str, counter: multiprocessing.Value, *,
            count_attempt: bool = True,
    ) -> None:
        """Fold child ingress outcomes into the non-evictable health summary."""
        if outcome not in self.__ROOT_PROGRESS_HEALTH_OUTCOMES:
            return
        seen_name = {
            "emitter_rejected": "_BreadcrumbTraceCollector__root_progress_rejected_seen",
            "emitter_enqueued": "_BreadcrumbTraceCollector__root_progress_enqueued_seen",
            "trace_policy_disabled": "_BreadcrumbTraceCollector__root_progress_disabled_seen",
            "tracer_deduplicated": "_BreadcrumbTraceCollector__root_progress_deduplicated_seen",
            "summary_observed": "_BreadcrumbTraceCollector__root_progress_summary_seen",
        }.get(outcome)
        if seen_name is None:
            return
        try:
            with counter.get_lock():
                current = int(counter.value)
        except Exception:
            return
        seen = int(getattr(self, seen_name))
        delta = current - seen
        if delta <= 0:
            return
        setattr(self, seen_name, current)
        health = self.__root_progress_health
        if count_attempt:
            health["attempt_count"] = min(
                self.__ROOT_PROGRESS_HEALTH_MAX_COUNTER,
                int(health["attempt_count"]) + delta,
            )
        counts = health["outcome_counts"]
        counts[outcome] = min(
            self.__ROOT_PROGRESS_HEALTH_MAX_COUNTER,
            int(counts.get(outcome, 0)) + delta,
        )
        latest = {
            "outcome": outcome,
            "stage": "other",
            "job_digest": "unknown",
            "observed_ms": int(time.time_ns() / 1_000_000),
            "details": {},
        }
        if outcome == "summary_observed":
            health["latest_summary"] = latest
        else:
            health["latest"] = latest

    def clear(self, scope: object = None, **filters: Any) -> Dict[str, Any]:
        return cast(Dict[str, Any], self.__run_direct_mutation(
            lambda: self.__clear_body(scope, **filters),
            lambda: {
                "cleared": False,
                "scope": "closed",
                "cleared_count": 0,
                "version": self.__version,
            },
        ))

    def __clear_body(self, scope: object = None, **filters: Any) -> Dict[str, Any]:
        """Clear retained evidence, optionally restricted to one scope.

        Policy state and accounting remain intact.  ``scope`` may be a filter
        mapping (for example ``{"corr_id": "flow-1"}``); keyword filters are
        accepted as a handler-friendly equivalent.
        """
        if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
            return {
                "cleared": False,
                "scope": "closed",
                "cleared_count": 0,
                "version": self.__version,
            }
        self.__drain_external_records(limit=None, force=True)
        clear_filters: Dict[str, Any] = {}
        if isinstance(scope, Mapping):
            for key, value in _bounded_mapping_copy(scope).items():
                if isinstance(key, str):
                    clear_filters[key] = value
        elif isinstance(scope, str) and scope not in {"", "all", "evidence"}:
            clear_filters["category"] = scope
        for key, value in _bounded_mapping_copy(filters).items():
            if isinstance(key, str):
                clear_filters[key] = value
        for alias, canonical in {
            "correlation_id": "corr_id",
            "correlation": "corr_id",
            "flow": "flow_id",
        }.items():
            if alias in clear_filters:
                clear_filters[canonical] = clear_filters.pop(alias)
        supported = {"corr_id", "flow_id", "stage", "event_type", "path_pair_id", "file_id", "source", "category", "level", "category_prefix"}
        unknown = set(clear_filters).difference(supported)
        if unknown or any(isinstance(value, Mapping) for value in clear_filters.values()):
            raise ValueError("unsupported breadcrumb clear scope")
        with self.__lock:
            if not clear_filters:
                removed_count = len(self.__entries)
                self.__entries.clear()
                self.__entry_sizes.clear()
                self.__retained_bytes = 0
                self.__latest_active_delta_rejection_summary = None
                self.__reset_root_progress_health_locked()
            else:
                kept_entries: Deque[Dict[str, Any]] = deque()
                kept_sizes: Deque[int] = deque()
                removed_count = 0
                retained_bytes = 0
                for entry, size in zip(self.__entries, self.__entry_sizes):
                    if self.__entry_matches(entry, clear_filters):
                        removed_count += 1
                        self.__record_gap_range(
                            int(entry.get("version", 0)),
                            int(entry.get("last_seen_version", entry.get("version", 0))),
                            "clear",
                        )
                    else:
                        kept_entries.append(entry)
                        kept_sizes.append(size)
                        retained_bytes += size
                self.__entries = kept_entries
                self.__entry_sizes = kept_sizes
                self.__retained_bytes = retained_bytes
                if self.__active_delta_rejection_summary_matches(clear_filters):
                    self.__latest_active_delta_rejection_summary = None
            self.__last_signature = self.__signature(self.__entries[-1]) if self.__entries else None
            self.__coalesce_entries.clear()
            self.__refresh_failure_locked()
            if self.__clear_scope_includes_root_progress(clear_filters):
                self.__reset_root_progress_health_locked()
            self.__last_reset_version = self.__version
            self.__last_reset_reason = "clear"
            self.__reset_generation += 1
            self.__last_clear_scope = copy.deepcopy(clear_filters) if clear_filters else None
            self.__window_truncated_pending = False
            return {
                "cleared": True,
                "scope": copy.deepcopy(clear_filters) if clear_filters else "all",
                "cleared_count": removed_count,
                "version": self.__version,
            }

    def reset(self) -> Dict[str, Any]:
        """Legacy alias for clearing all evidence (without changing policy)."""
        return cast(Dict[str, Any], self.__run_direct_mutation(
            self.__reset_body,
            lambda: {
                "cleared": False,
                "scope": "closed",
                "cleared_count": 0,
                "version": self.__version,
            },
        ))

    def __reset_body(self) -> Dict[str, Any]:
        if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
            return {"cleared": False, "scope": "closed", "cleared_count": 0, "version": self.__version}
        self.__drain_external_records(limit=None, force=True)
        with self.__lock:
            cleared_count = len(self.__entries)
            self.__entries.clear()
            self.__entry_sizes.clear()
            self.__retained_bytes = 0
            self.__last_signature = None
            self.__coalesce_entries.clear()
            self.__last_failure_entry = None
            self.__last_failure_version = None
            self.__latest_active_delta_rejection_summary = None
            self.__reset_root_progress_health_locked()
            self.__last_reset_version = self.__version
            self.__last_reset_reason = "reset"
            self.__reset_generation += 1
            self.__last_clear_scope = None
            self.__window_truncated_pending = False
            return {"cleared": True, "scope": "all", "cleared_count": cleared_count, "version": self.__version}

    def record_active_delta_authorization_rejection(
            self, corr_id: object, model_version: object, diagnostics: object,
    ) -> bool:
        """Compatibility wrapper for the original authorization-only writer."""
        return self.record_active_delta_rejection(
            corr_id, model_version, "active_delta_authorization_rejected", diagnostics,
        )

    def record_active_delta_rejection(
            self, corr_id: object, model_version: object, reason: object, diagnostics: object,
    ) -> bool:
        return bool(self.__run_direct_mutation(
            lambda: self.__record_active_delta_rejection_body(
                corr_id, model_version, reason, diagnostics,
            ),
            lambda: False,
        ))

    def __record_active_delta_rejection_body(
            self, corr_id: object, model_version: object, reason: object, diagnostics: object,
    ) -> bool:
        """Retain bounded aggregate evidence for the latest rejected root delta.

        The caller supplies an already-opaque target correlation.  This is a
        semantic diagnostic summary, rather than another event-retention path,
        so it is intentionally not subject to deque eviction.
        """
        if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
            return False
        if not self.is_effectively_enabled("model.progress", "debug"):
            return False
        safe_corr_id = self.__sanitize_optional_string(corr_id)
        if not isinstance(safe_corr_id, str) or not self.__is_root_progress_correlation(safe_corr_id):
            return False
        if type(reason) is not str or reason not in self.__ACTIVE_DELTA_REJECTION_REASONS:
            return False
        safe_model_version = model_version if type(model_version) is int and model_version >= 0 else None
        safe_diagnostics = self.__active_delta_diagnostics_summary(reason, diagnostics)
        with self.__lock:
            self.__latest_active_delta_rejection_summary = {
                "corr_id": safe_corr_id,
                "flow_id": safe_corr_id,
                "source": "root_progress",
                "category": "model.progress",
                "level": "debug",
                "stage": "root_progress_decision",
                "event_type": "diagnostic",
                "trace_scope": "flow",
                "reason": reason,
                "model_version": safe_model_version,
                "diagnostics": safe_diagnostics,
            }
        return True

    def __active_delta_rejection_summary_matches(self, filters: Mapping[str, Any]) -> bool:
        summary = self.__latest_active_delta_rejection_summary
        return summary is not None and self.__entry_matches(summary, filters)

    @staticmethod
    def __is_root_progress_correlation(value: str) -> bool:
        prefix = "root-progress:"
        digest = value[len(prefix):] if value.startswith(prefix) else ""
        return len(digest) == 16 and all(character in "0123456789abcdef" for character in digest)

    @classmethod
    def __active_delta_diagnostics_summary(cls, reason: str, diagnostics: object) -> Dict[str, Any]:
        """Project builder diagnostics to fixed, bounded aggregate counters."""
        if not isinstance(diagnostics, Mapping):
            return {}
        result: Dict[str, Any] = {}
        invalidation_reasons = diagnostics.get("invalidation_reasons")
        if isinstance(invalidation_reasons, (list, tuple, set, frozenset)):
            result["invalidation_reason_count"] = min(
                len(invalidation_reasons), cls.__ACTIVE_DELTA_DIAGNOSTIC_MAX_COUNT,
            )
        rejection_categories = diagnostics.get("rejection_categories")
        category_set = {"authority"} if reason == "active_delta_authorization_rejected" else set()
        if isinstance(rejection_categories, (list, tuple, set, frozenset)):
            for index, category in enumerate(rejection_categories):
                if index >= cls.__ACTIVE_DELTA_DIAGNOSTIC_MAX_COUNT:
                    break
                if isinstance(category, str) and category in cls.__ACTIVE_DELTA_REJECTION_CATEGORY_ORDER:
                    category_set.add(category)
        if category_set:
            result["rejection_categories"] = [
                category for category in cls.__ACTIVE_DELTA_REJECTION_CATEGORY_ORDER
                if category in category_set
            ]
        selector_failure = diagnostics.get("selector_failure")
        if type(selector_failure) is str and selector_failure in cls.__ACTIVE_DELTA_SELECTOR_FAILURES:
            result["selector_failure"] = selector_failure
        status_missing_provenance = diagnostics.get("status_missing_provenance")
        if isinstance(status_missing_provenance, Mapping):
            safe_provenance: Dict[str, Any] = {}
            for key, value in status_missing_provenance.items():
                if key not in cls.__ACTIVE_DELTA_STATUS_PROVENANCE_KEYS:
                    continue
                if key in cls.__ACTIVE_DELTA_STATUS_PROVENANCE_BOOL_KEYS and type(value) is bool:
                    safe_provenance[key] = value
                elif key == "poll_source" and type(value) is str and value in cls.__ACTIVE_DELTA_STATUS_POLL_SOURCES:
                    safe_provenance[key] = value
                elif key == "failure_reason" and type(value) is str and value in cls.__ACTIVE_DELTA_STATUS_FAILURE_REASONS:
                    safe_provenance[key] = value
                elif key == "future_state" and type(value) is str and value in cls.__ACTIVE_DELTA_STATUS_FUTURE_STATES:
                    safe_provenance[key] = value
                elif key in {"raw_count_bucket", "filtered_count_bucket"} and \
                        type(value) is str and value in cls.__ACTIVE_DELTA_STATUS_COUNT_BUCKETS:
                    safe_provenance[key] = value
            if safe_provenance:
                poll_decision = status_missing_provenance.get("poll_decision")
                if isinstance(poll_decision, Mapping):
                    safe_decision: Dict[str, Any] = {}
                    for key, value in poll_decision.items():
                        if key in cls.__ACTIVE_DELTA_POLL_DECISION_BOOL_KEYS and type(value) is bool:
                            safe_decision[key] = value
                        elif key in cls.__ACTIVE_DELTA_POLL_DECISION_BUCKET_KEYS and \
                                type(value) is str and value in cls.__ACTIVE_DELTA_STATUS_COUNT_BUCKETS:
                            safe_decision[key] = value
                        elif key == "poll_due_reason" and type(value) is str and \
                                value in cls.__ACTIVE_DELTA_POLL_DUE_REASONS:
                            safe_decision[key] = value
                        elif key == "poll_suppressed_reason" and type(value) is str and \
                                value in cls.__ACTIVE_DELTA_POLL_SUPPRESSED_REASONS:
                            safe_decision[key] = value
                    if safe_decision:
                        safe_provenance["poll_decision"] = safe_decision
                result["status_missing_provenance"] = safe_provenance
        for key in cls.__ACTIVE_DELTA_DIAGNOSTIC_COUNT_KEYS:
            value = diagnostics.get(key)
            if type(value) is int and value >= 0:
                result[key] = min(value, cls.__ACTIVE_DELTA_DIAGNOSTIC_MAX_COUNT)
        token_reason_counts = diagnostics.get("token_reason_counts")
        if isinstance(token_reason_counts, Mapping):
            values = []
            for index, value in enumerate(token_reason_counts.values()):
                if index >= cls.__ACTIVE_DELTA_DIAGNOSTIC_MAX_COUNT:
                    break
                if type(value) is int and value >= 0:
                    values.append(value)
            result["token_reason_kind_count"] = min(
                len(values), cls.__ACTIVE_DELTA_DIAGNOSTIC_MAX_COUNT,
            )
            result["token_reason_total_count"] = min(
                sum(values), cls.__ACTIVE_DELTA_DIAGNOSTIC_MAX_COUNT,
            )
        return result

    def __begin_direct_admission(self) -> tuple[bool, str]:
        """Join the same bounded gate used by child emitters.

        Direct collector writers run in the parent, but they still need a
        reservation so close cannot publish terminal health while a mutation
        is between its admission check and collector lock.
        """
        try:
            if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
                return False, _ADMISSION_INERT
            if not self.__ingress_admission_lock.acquire(False):
                # Re-read the lifecycle after the nonblocking lock miss.  A
                # concurrent closer may already have published CLOSING while
                # holding the same lock; that path must remain an inert
                # post-gate call rather than adding a late rejection gap.
                return False, (
                    _ADMISSION_REJECTED_OPEN
                    if self.__ingress_lifecycle_state() == _BREADCRUMB_LIFECYCLE_OPEN
                    else _ADMISSION_INERT
                )
            try:
                if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
                    return False, _ADMISSION_INERT
                reconcile_gate = self.__durable_reconcile_gate
                if reconcile_gate is not None:
                    try:
                        if bool(reconcile_gate.value):
                            return False, _ADMISSION_REJECTED_OPEN
                    except Exception:
                        return False, _ADMISSION_REJECTED_OPEN
                with self.__ingress_inflight_count.get_lock():
                    if int(self.__ingress_inflight_count.value) >= _BREADCRUMB_INGRESS_INFLIGHT_CAPACITY:
                        return False, _ADMISSION_REJECTED_OPEN
                    self.__ingress_inflight_count.value += 1
                return True, _ADMISSION_ACCEPTED
            finally:
                self.__ingress_admission_lock.release()
        except Exception:
            try:
                return False, (
                    _ADMISSION_REJECTED_OPEN
                    if self.__ingress_lifecycle_state() == _BREADCRUMB_LIFECYCLE_OPEN
                    else _ADMISSION_INERT
                )
            except Exception:
                return False, _ADMISSION_INERT

    def __record_direct_rejection(self, disposition: str) -> bool:
        """Account an OPEN rejection and return its final disposition."""
        if disposition != _ADMISSION_REJECTED_OPEN or not self.__begin_rejection_token():
            return False
        try:
            if self.__ingress_lifecycle_state() == _BREADCRUMB_LIFECYCLE_OPEN:
                # Use the shared rejection stream so this path does not wait
                # on another collector mutator's lock. Close folds it after
                # all short rejection tokens settle.
                self.__increment_shared_counter(self.__ingress_rejected_count)
            return True
        finally:
            self.__end_direct_admission()

    def record(self, source: str, message: str, details: object = None, **metadata: Any) -> str:
        if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
            return "disabled"
        if not self.is_effectively_enabled(metadata.get("category", source), metadata.get("level", "info")):
            return "disabled"
        # This is an internal drain-only control and can never be supplied by
        # a caller through the public collector API.
        metadata.pop("_from_ingress", None)
        admitted, disposition = self.__begin_direct_admission()
        if not admitted:
            rejection_admitted = self.__record_direct_rejection(disposition)
            return "dropped" if rejection_admitted else "disabled"
        try:
            return self.__record_direct_body(source, message, details, **metadata)
        finally:
            self.__end_direct_admission()

    def __record_direct_body(
            self, source: str, message: str, details: object = None,
            **metadata: Any,
    ) -> str:
        # The public method uses the shared admission helper below.  This body
        # is kept separate so it cannot accidentally bypass token release.
        self.__drain_external_records_if_due(limit=self.__max_entries)
        return self.__record_entry(
            source, message, details, allow_when_disabled=True,
            _from_ingress=True, **metadata,
        )

    def __end_direct_admission(self) -> None:
        try:
            with self.__ingress_inflight_count.get_lock():
                self.__ingress_inflight_count.value = max(
                    0, int(self.__ingress_inflight_count.value) - 1,
                )
        except Exception:
            pass

    def __begin_rejection_token(self) -> bool:
        if self.__ingress_lifecycle_state() != _BREADCRUMB_LIFECYCLE_OPEN:
            return False
        try:
            with self.__ingress_inflight_count.get_lock():
                self.__ingress_inflight_count.value += 1
            if self.__ingress_lifecycle_state() == _BREADCRUMB_LIFECYCLE_OPEN:
                return True
            self.__end_direct_admission()
            return False
        except Exception:
            try:
                self.__ingress_inflight_count.value = int(self.__ingress_inflight_count.value) + 1
                if self.__ingress_lifecycle_state() == _BREADCRUMB_LIFECYCLE_OPEN:
                    return True
                self.__end_direct_admission()
            except Exception:
                pass
            return False

    @staticmethod
    def __increment_shared_counter(counter: multiprocessing.Value) -> bool:
        try:
            with counter.get_lock():
                counter.value += 1
            return True
        except Exception:
            try:
                counter.value = int(counter.value) + 1
                return True
            except Exception:
                return False

    def __run_direct_mutation(
            self, callback: Callable[[], Any], closed_result: Callable[[], Any],
    ) -> Any:
        """Run one collector-owned mutation under the shared lifecycle gate."""
        admitted, disposition = self.__begin_direct_admission()
        if not admitted:
            self.__record_direct_rejection(disposition)
            return closed_result()
        try:
            return callback()
        finally:
            self.__end_direct_admission()

    def __record_entry(
        self,
        source: str,
        message: str,
        details: object = None,
        allow_when_disabled: bool = False,
        _from_ingress: bool = False,
        **metadata: Any,
    ) -> str:
        if not allow_when_disabled and not self.is_enabled():
            return "disabled"

        created_ns = metadata.pop("created_ns", None)
        created_ms = metadata.pop("created_ms", None)
        if created_ns is None:
            created_ns = time.time_ns()
        if created_ms is None:
            created_ms = int(created_ns / 1_000_000)

        stage = metadata.pop("stage", None)
        event_type = metadata.pop("event_type", "breadcrumb")
        corr_id = metadata.pop("corr_id", None)
        flow_id = metadata.pop("flow_id", None)
        file_id = metadata.pop("file_id", None)
        path_pair_id = metadata.pop("path_pair_id", None)
        path_pair_name = metadata.pop("path_pair_name", None)
        trace_scope = metadata.pop("trace_scope", "flow")
        category = metadata.pop("category", source)
        level = metadata.pop("level", "info")
        worker_policy_revision = metadata.pop("worker_policy_revision", None)
        worker_policy_epoch = metadata.pop("worker_policy_epoch", None)
        # Producers may supply a privacy-safe, opaque semantic signature for
        # collector-owned coalescing. It is intentionally consumed here and
        # never retained or exported with the breadcrumb.
        coalesce_key = metadata.pop("_coalesce_key", None)
        root_progress_observation = metadata.pop("_root_progress", False) is True
        if not isinstance(coalesce_key, str) or not coalesce_key:
            coalesce_key = None
        if not isinstance(category, str):
            category = "<{}>".format(type(category).__name__[:48])
        if not isinstance(level, str):
            level = "info"
        level = self.__normalize_level(level) or "info"
        if metadata:
            # Bound both sides of the merge before sanitization.  In
            # particular, never expand an attacker-controlled Mapping with
            # ``dict(mapping)`` or ``{**mapping}``; those copies can consume
            # unbounded iterators before the sanitizer gets a chance to cap
            # them.
            merged_details: Dict[Any, Any] = {}
            detail_items: List[tuple[Any, Any]] = []
            if isinstance(details, Mapping):
                detail_items.extend(self.__bounded_detail_items(details))
            elif details is not None:
                detail_items.append(("value", details))
            detail_items.extend(self.__bounded_detail_items(metadata))
            for item_key, item_value in detail_items:
                if len(merged_details) >= self.__MAX_MAPPING_ITEMS and item_key not in merged_details:
                    break
                merged_details[item_key] = item_value
            details = merged_details

        sanitized_details = self.__sanitize_value(details, key=None, depth=0)
        if sanitized_details is None:
            sanitized_details = {}

        entry: Dict[str, Any] = {
            "created_ms": created_ms,
            "created_ns": created_ns,
            "source": self.__sanitize_optional_string(source),
            "category": self.__sanitize_optional_string(category),
            "level": level,
            "stage": self.__sanitize_optional_string(stage if stage is not None else message),
            "event_type": self.__sanitize_optional_string(event_type),
            "corr_id": self.__sanitize_optional_string(corr_id),
            "flow_id": self.__sanitize_optional_string(flow_id),
            "file_id": self.__sanitize_optional_string(file_id),
            "path_pair_id": self.__sanitize_optional_string(path_pair_id),
            "path_pair_name": self.__sanitize_optional_string(path_pair_name),
            "trace_scope": self.__sanitize_optional_string(trace_scope),
            "message": self.__sanitize_optional_string(message),
            "details": sanitized_details,
            "worker_policy_revision": worker_policy_revision if isinstance(worker_policy_revision, int) else None,
            "worker_policy_epoch": worker_policy_epoch if isinstance(worker_policy_epoch, int) else None,
        }

        with self.__lock:
            if (self.__collector_closing or self.__collector_closed) and not _from_ingress:
                # Post-CLOSING direct calls are inert. In particular, do not
                # advance version/gap/drop accounting after terminal folding.
                return "disabled"
            if not self.__policy_allows(str(entry["category"]), level):
                self.__version += 1
                self.__dropped_count += 1
                self.__policy_dropped_count += 1
                self.__category_counter(entry)["policy_dropped"] += 1
                self.__record_gap_range(self.__version, self.__version, "policy")
                return "dropped"
            self.__accepted_count += 1
            self.__category_counter(entry)["admitted"] += 1
            signature = self.__signature(entry, coalesce_key)
            coalesced_entry = self.__coalesce_entries.get(signature) if coalesce_key is not None else None
            coalesced_index = None
            if coalesced_entry is not None:
                coalesced_index = next(
                    (index for index, candidate in enumerate(self.__entries) if candidate is coalesced_entry),
                    None,
                )
            if self.__entries and (signature == self.__last_signature or coalesced_index is not None):
                if coalesced_index is None:
                    coalesced_index = len(self.__entries) - 1
                last_entry = self.__entries[coalesced_index]
                # Coalescing can raise an earlier entry's last-seen version;
                # failure projection therefore remains ordered by entry position.
                refresh_failure = (
                    event_type == "failure" or last_entry.get("event_type") == "failure"
                )
                last_entry["repeat_count"] = last_entry.get("repeat_count", 1) + 1
                last_entry["last_seen_ms"] = created_ms
                last_entry["last_seen_ns"] = created_ns
                self.__version += 1
                self.__coalesced_count += 1
                self.__category_counter(last_entry)["coalesced"] += 1
                last_entry["last_seen_version"] = self.__version
                old_size = self.__entry_sizes[coalesced_index]
                new_size = self.__estimate_entry_bytes(last_entry)
                self.__entry_sizes[coalesced_index] = new_size
                self.__retained_bytes += new_size - old_size
                if event_type == "failure":
                    self.__last_failure_entry = copy.deepcopy(last_entry)
                    self.__last_failure_version = self.__version
                self.__enqueue_durable_entry(last_entry)
                evicted = self.__evict_to_budget()
                if refresh_failure and not evicted:
                    self.__refresh_failure_locked()
                if not evicted or self.__entry_range_is_retained(self.__version):
                    return "retained"
                return "evicted" if root_progress_observation else "dropped"

            self.__version += 1
            entry["version"] = self.__version
            entry["repeat_count"] = 1
            entry["last_seen_ms"] = created_ms
            entry["last_seen_ns"] = created_ns
            entry["last_seen_version"] = self.__version
            entry_size = self.__estimate_entry_bytes(entry)
            if entry_size > self.__memory_budget_bytes:
                self.__dropped_count += 1
                self.__oversized_dropped_count += 1
                self.__category_counter(entry)["oversized_dropped"] += 1
                self.__record_gap_range(self.__version, self.__version, "oversized")
                return "dropped"
            self.__entries.append(entry)
            self.__entry_sizes.append(entry_size)
            self.__retained_bytes += entry_size
            self.__last_signature = signature
            if coalesce_key is not None:
                self.__coalesce_entries[signature] = entry
            if event_type == "failure":
                self.__last_failure_entry = copy.deepcopy(entry)
                self.__last_failure_version = self.__version
            self.__enqueue_durable_entry(entry)
            evicted = self.__evict_to_budget()
            if not evicted or self.__entry_range_is_retained(self.__version):
                return "retained"
            return "evicted" if root_progress_observation else "dropped"

    def __entry_range_is_retained(self, version: int) -> bool:
        return any(
            int(entry.get("version", 0)) <= version <= int(
                entry.get("last_seen_version", entry.get("version", 0)),
            )
            for entry in self.__entries
        )

    def __enqueue_durable_entry(self, entry: Mapping[str, Any]) -> None:
        spool = self.__durable_spool
        if spool is None:
            return
        try:
            accepted = spool.enqueue(
                entry,
                is_critical_breadcrumb(
                    entry.get("source"), entry.get("message"), entry.get("details"), entry,
                ),
            )
            if not accepted:
                self.__durable_enqueue_rejected_count += 1
        except Exception:
            # Diagnostic persistence is strictly best effort and isolated from
            # retention/model ownership. Spool itself records ordinary loss.
            self.__durable_enqueue_rejected_count += 1
            return

    @staticmethod
    def __entry_matches(entry: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
        aliases = {"category": "category", "source": "source"}
        for key, expected in filters.items():
            if expected is None:
                continue
            if key == "category_prefix":
                if not str(entry.get("category", "")).startswith(str(expected)):
                    return False
                continue
            field = aliases.get(key, key)
            if field == "scope":
                continue
            if entry.get(field) != expected:
                return False
        return True

    @staticmethod
    def __estimate_entry_bytes(entry: Mapping[str, Any]) -> int:
        try:
            # Include a small object overhead allowance so the configured byte
            # budget is conservative for Python dict/list bookkeeping too.
            encoded = json.dumps(entry, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
            return len(encoded) + 128
        except (TypeError, ValueError, OverflowError):
            return 256

    def __record_gap_range(self, start: int, end: int, reason: str) -> None:
        if start < 1 or end < start:
            return
        if self.__lost_ranges:
            previous = self.__lost_ranges[-1]
            if previous["reason"] == reason and start <= int(previous["to_version"]) + 1:
                previous["to_version"] = max(int(previous["to_version"]), end)
                return
        while len(self.__lost_ranges) >= 512:
            removed = self.__lost_ranges.popleft()
            self.__gap_watermark_to = max(self.__gap_watermark_to, int(removed["to_version"]))
        self.__lost_ranges.append({"from_version": start, "to_version": end, "reason": reason})

    def __evict_to_budget(self) -> bool:
        evicted_any = False
        while self.__entries and (
            self.__retained_bytes > self.__memory_budget_bytes
            or self.__max_entries is not None and len(self.__entries) > self.__max_entries
        ):
            # Repeated candidate-indexed deque reads make eviction quadratic;
            # this lock-held snapshot preserves the iteration's selection while
            # live deques remain the deletion targets.
            entries = tuple(self.__entries)
            protected = self.__protected_indices(entries)
            candidate_indices = [index for index in range(len(entries)) if index not in protected]
            if not candidate_indices:
                candidate_indices = list(range(len(entries)))
            category_counts: Dict[str, int] = {}
            for index in candidate_indices:
                category = str(entries[index].get("category") or "unknown")
                category_counts[category] = category_counts.get(category, 0) + 1
            # Prefer the oldest record from the noisiest eligible category.
            chosen_index = max(candidate_indices, key=lambda index: (
                category_counts[str(entries[index].get("category") or "unknown")], -index,
            ))
            evicted = entries[chosen_index]
            evicted_size = self.__entry_sizes[chosen_index]
            del self.__entries[chosen_index]
            del self.__entry_sizes[chosen_index]
            for signature, candidate in tuple(self.__coalesce_entries.items()):
                if candidate is evicted:
                    del self.__coalesce_entries[signature]
            self.__retained_bytes -= evicted_size
            self.__evicted_count += 1
            self.__category_counter(evicted)["evicted"] += 1
            self.__record_gap_range(
                int(evicted.get("version", 0)),
                int(evicted.get("last_seen_version", evicted.get("version", 0))),
                "evicted",
            )
            self.__window_truncated_pending = True
            evicted_any = True
        self.__retained_bytes = max(0, self.__retained_bytes)
        self.__last_signature = self.__signature(self.__entries[-1]) if self.__entries else None
        if evicted_any:
            self.__refresh_failure_locked()
        return evicted_any

    def __refresh_failure_locked(self) -> None:
        latest: Optional[Dict[str, Any]] = None
        for entry in self.__entries:
            if entry.get("event_type") == "failure":
                latest = entry
        if latest is None:
            self.__last_failure_entry = None
            self.__last_failure_version = None
        else:
            self.__last_failure_entry = copy.deepcopy(latest)
            self.__last_failure_version = int(latest.get("last_seen_version", latest.get("version", 0)))

    def snapshot(
        self,
        since_version: Optional[int] = None,
        limit: Optional[int] = None,
        corr_id: Optional[str] = None,
        flow_id: Optional[str] = None,
        stage: Optional[str] = None,
        event_type: Optional[str] = None,
        path_pair_id: Optional[str] = None,
        file_id: Optional[str] = None,
        order: str | None = "asc",
        source: Optional[str] = None,
        category: Optional[str] = None,
        level: Optional[str] = None,
        until_version: Optional[int] = None,
        exact_version: Optional[int] = None,
        category_prefix: Optional[str] = None,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be greater than 0")
        if order is None or order == "":
            order = "asc"
        if order not in {"asc", "desc"}:
            raise ValueError("order must be 'asc' or 'desc'")

        query = {
            "since_version": since_version,
            "limit": limit,
            "corr_id": corr_id,
            "flow_id": flow_id,
            "stage": stage,
            "event_type": event_type,
            "path_pair_id": path_pair_id,
            "file_id": file_id,
            "order": order,
            "source": source,
            "category": category,
            "level": level,
        }

        self.__drain_external_records(
            limit=self.__max_entries,
            wait_for_first_record=True,
            force=True,
        )
        with self.__lock:
            enabled = self.is_enabled()
            entries = self.__filter_entries(
                self.__entries,
                since_version=since_version,
                limit=limit,
                corr_id=corr_id,
                flow_id=flow_id,
                stage=stage,
                event_type=event_type,
                path_pair_id=path_pair_id,
                file_id=file_id,
                order=order,
                source=source,
                category=category,
                level=level,
                until_version=until_version, exact_version=exact_version,
                category_prefix=category_prefix, start_time_ms=start_time_ms, end_time_ms=end_time_ms,
            )
            window_reset = (since_version is None and self.__reset_generation > 0) or (
                since_version is not None and since_version <= self.__last_reset_version
            )
            window_reset_reason = self.__last_reset_reason if window_reset else None
            if window_reset and window_reset_reason is None:
                window_reset_reason = "reset"
            if not enabled:
                payload = self.__build_snapshot_payload(
                    entries=copy.deepcopy(entries),
                    all_entries=self.__entries,
                    query=query,
                    window_reset=window_reset,
                    window_reset_reason=window_reset_reason,
                    window_truncated=self.__window_truncated_pending,
                    external_queue_drain_count=self.__external_queue_last_drain_count,
                    external_queue_drain_limit=self.__external_queue_last_drain_limit,
                    external_queue_drain_limited=self.__external_queue_drain_limited,
                )
                self.__window_truncated_pending = False
                return payload

            payload = self.__build_snapshot_payload(
                entries=copy.deepcopy(entries),
                all_entries=self.__entries,
                query=query,
                window_reset=window_reset,
                window_reset_reason=window_reset_reason,
                window_truncated=self.__window_truncated_pending,
                external_queue_drain_count=self.__external_queue_last_drain_count,
                external_queue_drain_limit=self.__external_queue_last_drain_limit,
                external_queue_drain_limited=self.__external_queue_drain_limited,
            )
            self.__window_truncated_pending = False
            return payload

    def __filter_entries(
        self,
        entries: Iterable[Dict[str, Any]],
        since_version: Optional[int],
        limit: Optional[int],
        corr_id: Optional[str],
        flow_id: Optional[str],
        stage: Optional[str],
        event_type: Optional[str],
        path_pair_id: Optional[str],
        file_id: Optional[str],
        order: str,
        source: Optional[str] = None,
        category: Optional[str] = None,
        level: Optional[str] = None,
        until_version: Optional[int] = None,
        exact_version: Optional[int] = None,
        category_prefix: Optional[str] = None,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        iterator = reversed(entries) if order == "desc" and isinstance(entries, deque) else entries
        filters = {"source": source, "category": category, "level": level, "corr_id": corr_id,
                   "flow_id": flow_id, "stage": stage, "event_type": event_type,
                   "path_pair_id": path_pair_id, "file_id": file_id}
        for entry in iterator:
            first = int(entry.get("version", -1)); last = int(entry.get("last_seen_version", first))
            if since_version is not None and last <= since_version: continue
            if until_version is not None and first > until_version: continue
            if exact_version is not None and not (first <= exact_version <= last): continue
            if category_prefix is not None and not str(entry.get("category", "")).startswith(category_prefix): continue
            if start_time_ms is not None and int(entry.get("created_ms", 0)) < start_time_ms: continue
            if end_time_ms is not None and int(entry.get("created_ms", 0)) > end_time_ms: continue
            if not self.__entry_matches(entry, filters): continue
            result.append(entry)
            if limit is not None and len(result) >= limit: break
        return result

    def query_events(self, **filters: Any) -> Dict[str, Any]:
        """Return a bounded, version-aware event query.

        ``snapshot`` remains the compatibility name.  This service-facing
        alias adds ``events`` and explicit version gaps while accepting the
        same filters plus ``category``, ``level``, and ``source``.
        """
        # Normalize the handler-facing vocabulary at this single owner.  The
        # legacy snapshot keeps its original names, while v1 callers can use
        # correlation/time/version aliases without needing a second cache.
        requested_filters = _bounded_mapping_copy(filters)
        aliases = {
            "correlation_id": "corr_id",
            "correlation": "corr_id",
            "flow": "flow_id",
            "min_version": "since_version",
            "max_version": "until_version",
            "created_after_ms": "start_time_ms",
            "created_before_ms": "end_time_ms",
            "min_time_ms": "start_time_ms",
            "max_time_ms": "end_time_ms",
        }
        normalized: Dict[str, Any] = {}
        for key, value in _bounded_mapping_copy(filters).items():
            normalized[aliases.get(key, key)] = value
        until_version = normalized.pop("until_version", None)
        exact_version = normalized.pop("version", None)
        category_prefix = normalized.pop("category_prefix", None)
        start_time_ms = normalized.pop("start_time_ms", None)
        end_time_ms = normalized.pop("end_time_ms", None)
        # Stream-only controls are intentionally not retention filters.
        for key in ("follow", "interval_ms", "heartbeat_ms", "format"):
            normalized.pop(key, None)
        allowed = {
            "since_version", "limit", "corr_id", "flow_id", "stage", "event_type",
            "path_pair_id", "file_id", "order", "source", "category", "level",
        }
        snapshot_filters = {key: value for key, value in normalized.items() if key in allowed}
        snapshot_filters.update({
            "until_version": until_version, "exact_version": exact_version,
            "category_prefix": category_prefix, "start_time_ms": start_time_ms,
            "end_time_ms": end_time_ms,
        })
        payload = self.snapshot(**snapshot_filters)
        since_version = payload.get("since_version")
        with self.__lock:
            gaps: List[Dict[str, Any]] = []
            if isinstance(since_version, int):
                if since_version < self.__gap_watermark_to:
                    gaps.append({"from_version": since_version + 1, "to_version": self.__gap_watermark_to,
                                 "reason": "gap_watermark"})
                for gap in self.__lost_ranges:
                    start = max(int(gap["from_version"]), since_version + 1)
                    end = min(int(gap["to_version"]), self.__version)
                    if start <= end:
                        gaps.append({"from_version": start, "to_version": end, "reason": gap["reason"]})
                if payload.get("window_reset") and since_version < self.__last_reset_version:
                    reset_start = since_version + 1
                    reset_end = self.__last_reset_version
                    if reset_start <= reset_end:
                        gaps.append({
                            "from_version": reset_start,
                            "to_version": reset_end,
                            "reason": payload.get("window_reset_reason") or "reset",
                        })
            payload["events"] = copy.deepcopy(payload.get("entries", []))
            payload["query"].update({key: value for key, value in requested_filters.items() if key not in {"follow", "interval_ms", "heartbeat_ms", "format"}})
            payload["gaps"] = gaps
            payload["gap_count"] = len(gaps)
            payload["gap"] = bool(gaps)
            payload["gap_detected"] = bool(gaps)
            payload["query_bounded"] = True
        return payload

    def export_events(self, **filters: Any) -> Dict[str, Any]:
        """Build a JSON-ready export that is bounded by bytes and event count."""
        max_bytes = filters.pop("max_bytes", 1 * 1024 * 1024)
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be greater than 0")
        max_events = filters.pop("max_events", None)
        if max_events is not None and (type(max_events) is not int or max_events < 1):
            raise ValueError("max_events must be greater than 0")
        payload = self.query_events(**filters)
        events = payload.get("events", [])
        if not isinstance(events, list):
            events = []
        if max_events is not None and len(events) > max_events:
            events = events[-max_events:]
        truncated = len(events) != len(payload.get("events", []))
        payload["events"] = []
        payload["entries"] = []
        payload["export_max_bytes"] = max_bytes
        payload["export_max_events"] = max_events
        # One-pass size admission avoids repeatedly serializing an ever
        # smaller export window. Entries are already sanitized by retention.
        base_size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        selected: List[Dict[str, Any]] = []
        used = base_size
        for event in events:
            event_size = len(json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
            # The JSON payload contains the event in both compatibility arrays.
            if used + (2 * event_size) + 4 > max_bytes:
                truncated = True
                continue
            selected.append(event)
            used += (2 * event_size) + 4
        events = selected
        payload["events"] = events
        payload["entries"] = copy.deepcopy(events)
        payload["export_truncated"] = truncated
        payload["export_event_count"] = len(events)
        try:
            final_size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError, OverflowError):
            final_size = max_bytes + 1
        if final_size > max_bytes:
            # A future field added to the query payload must not defeat the
            # export limit when the caller asks for a very small envelope.
            minimal: Dict[str, Any] = {
                "events": [],
                "entries": [],
                "export_max_bytes": max_bytes,
                "export_truncated": True,
                "export_event_count": 0,
            }
            try:
                minimal_size = len(json.dumps(minimal, separators=(",", ":")).encode("utf-8"))
            except (TypeError, ValueError, OverflowError):
                minimal_size = max_bytes + 1
            payload = minimal if minimal_size <= max_bytes else {}
        return payload

    def __build_snapshot_payload(
        self,
        entries: List[Dict[str, Any]],
        all_entries: List[Dict[str, Any]],
        query: Dict[str, Any],
        window_reset: bool,
        window_reset_reason: Optional[str],
        window_truncated: bool,
        external_queue_drain_count: int,
        external_queue_drain_limit: int,
        external_queue_drain_limited: bool,
    ) -> Dict[str, Any]:
        payload = {
            "enabled": self.is_enabled(),
            "version": self.__version,
            "since_version": query["since_version"],
            "last_reset_version": self.__last_reset_version,
            "last_reset_reason": self.__last_reset_reason,
            "reset_generation": self.__reset_generation,
            "window_reset": window_reset,
            "window_reset_reason": window_reset_reason,
            "window_truncated": window_truncated,
            "max_entries": self.__max_entries,
            "memory_budget_bytes": self.__memory_budget_bytes,
            "retained_bytes": self.__retained_bytes,
            "retained_count": len(self.__entries),
            "entry_count": len(entries),
            "query": query,
            "filtering": {
                "requested": copy.deepcopy(query),
                "returned_by_category": self.__filtered_category_accounting(entries),
                "returned_count": len(entries),
            },
            "external_queue_drain_count": external_queue_drain_count,
            "external_queue_drain_limit": external_queue_drain_limit,
            "external_queue_drain_limited": external_queue_drain_limited,
            "latest_failure_version": self.__last_failure_version,
            "latest_failure_entry": copy.deepcopy(self.__last_failure_entry),
            "failure_summary": self.__build_failure_summary(all_entries),
            "root_progress_health": self.__root_progress_health_snapshot(),
            "progress_lineage": self.__progress_lineage_snapshot(),
            "progress_lineage_health": self.__progress_lineage_health_snapshot(),
            "active_delta_rejection_summary": copy.deepcopy(
                self.__latest_active_delta_rejection_summary
            ),
            "accounting": {
                "accepted_count": self.__accepted_count,
                "recorded_count": self.__accepted_count,
                "coalesced_count": self.__coalesced_count,
                "dropped_count": self.__dropped_count,
                "policy_dropped_count": self.__policy_dropped_count,
                "oversized_dropped_count": self.__oversized_dropped_count,
                "evicted_count": self.__evicted_count,
                "retained_count": len(self.__entries),
                "retained_bytes": self.__retained_bytes,
                "memory_budget_bytes": self.__memory_budget_bytes,
                "count_limit": self.__max_entries,
                "categories": self.__category_accounting_snapshot(),
            },
            "retention": {
                "bytes": self.__retained_bytes,
                "count": len(self.__entries),
                "budget_bytes": self.__memory_budget_bytes,
                "count_limit": self.__max_entries,
                "policy": self.__effective_retention_policy(),
            },
            "drops": {
                "total": self.__dropped_count,
                "policy": self.__policy_dropped_count,
                "oversized": self.__oversized_dropped_count,
            },
            "evictions": self.__evicted_count,
            "gaps": copy.deepcopy(list(self.__lost_ranges)),
            "gap_watermark_to_version": self.__gap_watermark_to,
            "gap": bool(self.__lost_ranges),
            "gap_detected": bool(self.__lost_ranges),
            "policy_revision": self.__policy_revision,
            "worker_propagation": self.__worker_policy_status(),
            "entries": entries,
        }
        return payload

    def __build_failure_summary(self, entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if self.__last_failure_entry is None:
            return None

        failure_entry = copy.deepcopy(self.__last_failure_entry)
        failure_version = failure_entry.get("version")
        corr_id = failure_entry.get("corr_id")
        trace_scope = failure_entry.get("trace_scope") or "flow"

        recent_entries: List[Dict[str, Any]] = []
        for entry in entries:
            if failure_version is not None and entry["version"] > failure_version:
                continue
            if trace_scope == "aggregate":
                if entry.get("trace_scope") != "aggregate":
                    continue
            else:
                if corr_id is not None and entry.get("corr_id") != corr_id:
                    continue
                if corr_id is None:
                    failure_file_id = failure_entry.get("file_id")
                    failure_path_pair_id = failure_entry.get("path_pair_id")
                    if failure_path_pair_id is not None:
                        if entry.get("path_pair_id") != failure_path_pair_id:
                            continue
                    elif failure_file_id is not None:
                        if entry.get("file_id") != failure_file_id:
                            continue
                    else:
                        continue
            recent_entries.append(entry)

        if not recent_entries:
            recent_entries = [failure_entry]

        recent_stage_trail: List[Dict[str, Any]] = []
        for entry in recent_entries[-5:]:
            recent_stage_trail.append({
                "version": entry["version"],
                "corr_id": entry["corr_id"],
                "stage": entry["stage"],
                "message": entry["message"],
                "event_type": entry["event_type"],
                "trace_scope": entry["trace_scope"],
                "file_id": entry["file_id"],
                "path_pair_id": entry["path_pair_id"],
                "path_pair_name": entry["path_pair_name"],
            })

        return {
            "corr_id": failure_entry["corr_id"],
            "stage": failure_entry["stage"],
            "message": failure_entry["message"],
            "version": failure_entry["version"],
            "event_type": failure_entry["event_type"],
            "file_id": failure_entry["file_id"],
            "path_pair_id": failure_entry["path_pair_id"],
            "path_pair_name": failure_entry["path_pair_name"],
            "created_ms": failure_entry["created_ms"],
            "created_ns": failure_entry["created_ns"],
            "repeat_count": failure_entry["repeat_count"],
            "trace_scope": trace_scope,
            "recent_stage_trail": recent_stage_trail,
            "window_entry_count": len(recent_entries),
        }

    def __signature(self, entry: Dict[str, Any], coalesce_key: Optional[str] = None) -> str:
        if coalesce_key is not None:
            return "coalesce:" + coalesce_key
        signature_payload = {
            "source": entry["source"],
            "category": entry.get("category"),
            "level": entry.get("level"),
            "stage": entry["stage"],
            "event_type": entry["event_type"],
            "corr_id": entry["corr_id"],
            "flow_id": entry["flow_id"],
            "file_id": entry["file_id"],
            "path_pair_id": entry["path_pair_id"],
            "path_pair_name": entry["path_pair_name"],
            "trace_scope": entry["trace_scope"],
            "message": entry["message"],
            "details": entry["details"],
        }
        return json.dumps(signature_payload, sort_keys=True, default=str)

    def __sanitize_value(
            self, value: Any, key: Optional[str], depth: int,
            _node_budget: Optional[List[int]] = None,
    ) -> Any:
        # Every recursive branch shares one node budget. This protects the
        # parent collector from mappings/lists whose iterator is effectively
        # unbounded, while still allowing deep credential keys to be visited.
        if _node_budget is None:
            _node_budget = [self.__MAX_SANITIZE_NODES]
        if _node_budget[0] <= 0:
            return "<truncated>"
        _node_budget[0] -= 1
        if key is not None and key.lower() in self.__SAFE_DIAGNOSTIC_ENUM_FIELDS:
            allowed_values = self.__SAFE_DIAGNOSTIC_ENUM_FIELDS[key.lower()]
            if type(value) is str and allowed_values is not None and value in allowed_values:
                return value
            return "<redacted>"
        if value is None:
            return None
        if key is not None and self.__is_sensitive_key(key):
            return "<redacted>"
        if depth >= self.__MAX_COLLECTION_DEPTH and isinstance(
                value, (Mapping, list, tuple, set, frozenset)):
            # Never repr() a collection at the boundary: nested secret values
            # must not be copied into a diagnostic string.
            return "<truncated>"
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value if value.bit_length() <= 128 else "<truncated>"
        if isinstance(value, float):
            return value if math.isfinite(value) and len(repr(value)) <= 32 else "<truncated>"
        if isinstance(value, str):
            if key is not None and self.__is_command_key(key):
                return "<redacted>"
            return self.__truncate_string(
                self.__sanitize_string_content(_bounded_redaction_text(value))
            )
        if isinstance(value, Mapping):
            sanitized: Dict[str, Any] = {}
            try:
                iterator = iter(value.items())
                for index in range(self.__MAX_MAPPING_ITEMS):
                    try:
                        item_key, item_value = next(iterator)
                    except StopIteration:
                        break
                    # Redact first when a key is oversized or unclassifiable.
                    # Only then derive a bounded key representation and run
                    # prefix/suffix-sensitive classification.
                    value_pre_sanitized = False
                    if type(item_key) is str:
                        oversized_key = len(item_key) > self.__MAX_DETAIL_KEY_LENGTH
                        if oversized_key:
                            sanitized_value = self.__sanitize_value(
                                item_value, "<oversized_key_secret>", depth + 1,
                                _node_budget,
                            )
                            value_pre_sanitized = True
                        item_key_str = _bounded_redaction_text(item_key)
                        key_is_sensitive = oversized_key or self.__is_sensitive_key(item_key_str)
                    else:
                        # Do not call str() on attacker-controlled key
                        # objects; their conversion may be unbounded or
                        # execute arbitrary code. Treat the value as secret.
                        sanitized_value = self.__sanitize_value(
                            item_value, "<unclassifiable_key_secret>", depth + 1,
                            _node_budget,
                        )
                        value_pre_sanitized = True
                        item_key_str = "<{}>".format(type(item_key).__name__[:48])
                        key_is_sensitive = True
                    # Redact key contents too, then apply a strict key bound;
                    # otherwise a long key can bypass detail-size limits.
                    item_key_str = self.__sanitize_string_content(item_key_str)
                    if len(item_key_str) > self.__MAX_DETAIL_KEY_LENGTH:
                        suffix = "...<truncated>"
                        item_key_str = (
                            item_key_str[:self.__MAX_DETAIL_KEY_LENGTH - len(suffix)]
                            + suffix
                        )
                    if key_is_sensitive or self.__is_sensitive_key(item_key_str):
                        sanitized[item_key_str] = "<redacted>"
                    elif value_pre_sanitized:
                        sanitized[item_key_str] = sanitized_value
                    else:
                        sanitized[item_key_str] = self.__sanitize_value(
                            item_value, item_key_str, depth + 1, _node_budget,
                        )
            except Exception:
                sanitized["<truncated>"] = "<truncated>"
            return sanitized
        if isinstance(value, (list, tuple, set, frozenset)):
            if key is not None and self.__is_command_key(key):
                return "<redacted>"
            sanitized_list: List[Any] = []
            try:
                iterator = iter(value)
                for index in range(self.__MAX_LIST_ITEMS):
                    try:
                        item = next(iterator)
                    except StopIteration:
                        break
                    sanitized_list.append(
                        self.__sanitize_value(item, key, depth + 1, _node_budget),
                    )
                else:
                    sanitized_list.append("<truncated>")
            except Exception:
                sanitized_list.append("<truncated>")
            return sanitized_list
        try:
            string_value = str(value)
        except Exception:
            string_value = "<unprintable>"
        return self.__truncate_string(self.__sanitize_string_content(string_value))

    @classmethod
    def __bounded_detail_items(cls, value: object) -> List[tuple[Any, Any]]:
        """Copy at most the collector's detail budget from a Mapping."""
        if not isinstance(value, Mapping):
            return []
        bounded: List[tuple[Any, Any]] = []
        try:
            iterator = iter(value.items())
            for _index in range(cls.__MAX_MAPPING_ITEMS):
                try:
                    item_key, item_value = next(iterator)
                except StopIteration:
                    break
                bounded.append((item_key, item_value))
        except Exception:
            # Sanitization will retain a bounded truncation marker if the
            # source Mapping fails during iteration.
            return bounded
        return bounded

    def __is_sensitive_key(self, key: str) -> bool:
        lowered = _bounded_redaction_text(key).lower()
        if lowered in BreadcrumbTraceCollector.__SAFE_DIAGNOSTIC_KEYS:
            return False
        return any(
            keyword in lowered
            for keyword in BreadcrumbTraceCollector.__SENSITIVE_KEYWORDS
        )

    def __is_command_key(self, key: str) -> bool:
        lowered = _bounded_redaction_text(key).lower()
        return any(keyword in lowered for keyword in BreadcrumbTraceCollector.__COMMAND_KEYWORDS)

    def __truncate_string(self, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            if isinstance(value, (int, float, bool)):
                value = str(value)
            else:
                value = "<{}>".format(type(value).__name__[:48])
        value = _bounded_redaction_text(value)
        if len(value) <= BreadcrumbTraceCollector.__MAX_DETAIL_STRING_LENGTH:
            return value
        return value[:BreadcrumbTraceCollector.__MAX_DETAIL_STRING_LENGTH] + "...<truncated>"

    def __sanitize_optional_string(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            bounded = _bounded_redaction_text(value)
        elif isinstance(value, int):
            bounded = str(value) if value.bit_length() <= 128 else "<truncated>"
        elif isinstance(value, (float, bool)):
            bounded = str(value)
        else:
            bounded = "<{}>".format(type(value).__name__[:48])
        return cast(str, self.__truncate_string(self.__sanitize_string_content(bounded)))

    def __sanitize_string_content(self, value: str) -> str:
        return redact_sensitive_text(_bounded_redaction_text(value)) or ""

    def __drain_external_records_if_due(self, limit: Optional[int]) -> None:
        if self.__ingress_drainer_active:
            # With the durable path enabled this thread is the sole consumer;
            # API/snapshot callers only observe collector state.
            return
        if self.__ingress_queues() == (None, None):
            return
        now = time.monotonic()
        last_drain = self.__external_queue_last_drain_monotonic
        if last_drain is not None and now - last_drain < self.__EXTERNAL_QUEUE_DRAIN_INTERVAL_SECONDS:
            return
        self.__external_queue_last_drain_monotonic = now
        self.__drain_external_records(limit=limit)

    def __drain_external_records(
        self,
        limit: Optional[int] = None,
        wait_for_first_record: bool = False,
        force: bool = False,
        from_drainer: bool = False,
        deadline: Optional[float] = None,
    ) -> int:
        # Snapshot/API drains and the dedicated drainer are fully serialized;
        # this also covers the close transition after the drainer joins.
        if deadline is None:
            acquired = self.__external_drain_lock.acquire()
        else:
            remaining = max(0.0, deadline - time.monotonic())
            acquired = self.__external_drain_lock.acquire(timeout=remaining)
        if not acquired:
            return -1
        try:
            return self.__drain_external_records_unlocked(
                limit=limit,
                wait_for_first_record=wait_for_first_record,
                force=force,
                from_drainer=from_drainer,
                deadline=deadline,
            )
        finally:
            self.__external_drain_lock.release()

    def __drain_external_records_unlocked(
        self,
        limit: Optional[int] = None,
        wait_for_first_record: bool = False,
        force: bool = False,
        from_drainer: bool = False,
        deadline: Optional[float] = None,
    ) -> int:
        self.__consume_ingress_rejections()
        if self.__ingress_drainer_active and not from_drainer:
            return 0
        critical_queue, normal_queue = self.__ingress_queues()
        if critical_queue is None and normal_queue is None:
            self.__external_queue_last_drain_count = 0
            self.__external_queue_last_drain_limit = limit if limit is not None else self.__max_entries
            self.__external_queue_drain_limited = False
            return 0
        if force:
            self.__external_queue_last_drain_monotonic = time.monotonic()
        drained_count = 0
        drain_limited = False
        drain_limit = limit if limit is not None else self.__max_entries
        critical_budget = BREADCRUMB_INGRESS_CRITICAL_BURST
        while (limit is None or drained_count < limit) and (
            deadline is None or time.monotonic() < deadline
        ):
            try:
                if wait_for_first_record and drained_count == 0:
                    first_wait = BreadcrumbTraceCollector.__EXTERNAL_QUEUE_FIRST_RECORD_WAIT_SECONDS
                    if deadline is not None:
                        first_wait = min(first_wait, max(0.0, deadline - time.monotonic()))
                    external_record = self.__get_external_record(
                        wait_timeout=first_wait,
                        critical_budget=critical_budget,
                    )
                else:
                    external_record = self.__get_external_record(
                        wait_timeout=None, critical_budget=critical_budget,
                    )
            except queue.Empty:
                break
            lane: object = None
            if isinstance(external_record, tuple) and len(external_record) == 2:
                external_record, lane = external_record
                critical_budget = (
                    critical_budget - 1
                    if lane == "critical"
                    else BREADCRUMB_INGRESS_CRITICAL_BURST
                )
            drained_count += 1
            if not isinstance(external_record, dict):
                self.__ack_ingress_lane(lane)
                continue
            record_mapping = cast(Dict[str, Any], external_record)
            source = record_mapping.get("source")
            message = record_mapping.get("message")
            if not isinstance(source, str) or not isinstance(message, str):
                self.__ack_ingress_lane(lane)
                continue
            metadata_value = record_mapping.get("metadata", {})
            metadata: Dict[str, Any] = (
                cast(Dict[str, Any], metadata_value) if isinstance(metadata_value, dict) else {}
            )
            # Never let a worker-provided key collide with the private drain
            # authorization below.
            metadata.pop("_from_ingress", None)
            revision = record_mapping.get("policy_revision")
            epoch = record_mapping.get("policy_epoch")
            if isinstance(revision, int) and isinstance(epoch, int):
                with self.__lock:
                    if epoch == self.__policy_epoch:
                        self.__worker_policy_ack_revision = revision
                        self.__worker_policy_ack_epoch = epoch
            record_outcome = self.__record_entry(
                source,
                message,
                record_mapping.get("details"),
                allow_when_disabled=True,
                stage=metadata.get("stage"),
                event_type=metadata.get("event_type", "breadcrumb"),
                category=metadata.get("category", source),
                level=metadata.get("level", "info"),
                corr_id=metadata.get("corr_id"),
                flow_id=metadata.get("flow_id"),
                file_id=metadata.get("file_id"),
                path_pair_id=metadata.get("path_pair_id"),
                path_pair_name=metadata.get("path_pair_name"),
                _coalesce_key=metadata.get("_coalesce_key"),
                _root_progress=metadata.get("_root_progress"),
                worker_policy_revision=revision,
                worker_policy_epoch=epoch,
                created_ns=record_mapping.get("created_ns"),
                created_ms=record_mapping.get("created_ms"),
                _from_ingress=True,
            )
            if metadata.get("category") == "model.progress" and metadata.get("_root_progress") is True:
                health_outcome = {
                    "retained": "collector_accepted",
                    "coalesced": "collector_accepted",
                    "evicted": "collector_evicted",
                }.get(record_outcome, "collector_rejected")
                stage = metadata.get("stage")
                if isinstance(stage, str) and stage.startswith("root_progress_"):
                    stage = stage[len("root_progress_"):]
                details = record_mapping.get("details")
                job_digest = details.get("job_digest") if isinstance(details, Mapping) else None
                self.__record_root_progress_health_body(
                    health_outcome, stage, job_digest, details, count_attempt=False,
                )
            self.__ack_ingress_lane(lane)
        if limit is not None and drained_count >= limit:
            if self.__critical_external_records is None and self.__normal_external_records is None:
                # Only legacy in-process test doubles lack admission/ack
                # counters. Preserve their bounded-drain telemetry without
                # using this compatibility probe for multiprocessing
                # shutdown decisions.
                legacy = self.__external_records
                try:
                    drain_limited = not bool(legacy.empty()) if legacy is not None and hasattr(legacy, "empty") else True
                except Exception:
                    drain_limited = True
            else:
                drain_limited = self.__ingress_has_records()
        self.__external_queue_last_drain_count = drained_count
        self.__external_queue_last_drain_limit = drain_limit
        self.__external_queue_drain_limited = drain_limited
        return drained_count

    def __ack_ingress_lane(self, lane: object) -> None:
        counter = (
            self.__critical_ingress_ack_count
            if lane == "critical" else self.__normal_ingress_ack_count
        )
        try:
            with counter.get_lock():
                counter.value += 1
        except Exception:
            unknown_counter = (
                self.__critical_ingress_unknown_count
                if lane == "critical" else self.__normal_ingress_unknown_count
            )
            # The item was consumed but ACK publication failed. Retain an
            # UNKNOWN reservation so close/flush cannot claim a settled lane.
            self.__increment_shared_counter(unknown_counter)

    def __get_external_record(
        self,
        wait_timeout: Optional[float],
        critical_budget: int = BREADCRUMB_INGRESS_CRITICAL_BURST,
    ) -> object:
        critical_records, normal_records = self.__ingress_queues()
        if critical_records is None and normal_records is None:
            raise queue.Empty

        def get_nowait(candidate: object) -> object:
            if candidate is None:
                raise queue.Empty()
            return getattr(candidate, "get_nowait")()

        # A first-record wait covers multiprocessing.Queue feeder latency.
        if wait_timeout is not None:
            wait_queue = critical_records if critical_records is not None else normal_records
            get_method = getattr(wait_queue, "get", None) if wait_queue is not None else None
            if callable(get_method):
                try:
                    return get_method(timeout=wait_timeout), "critical" if critical_records is not None else "normal"
                except queue.Empty:
                    pass
        if critical_budget > 0:
            try:
                return get_nowait(critical_records), "critical"
            except (queue.Empty, OSError):
                pass
        try:
            return get_nowait(normal_records), "normal"
        except (queue.Empty, OSError):
            pass
        if critical_budget <= 0:
            return get_nowait(critical_records), "critical"
        # If both lanes are empty this propagates Empty to the bounded caller.
        return get_nowait(normal_records), "normal"
