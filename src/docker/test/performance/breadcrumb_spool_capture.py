"""Evidence-only, descriptor-relative reader for the durable breadcrumb spool.

The reader is deliberately a small Linux primitive.  It never creates, writes,
renames, or deletes anything below the application spool.  A verified directory
descriptor is the authority for all spool reads; the path accepted by the
constructor is only a compatibility convenience for opening that descriptor.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Mapping

from incoming_recovery_observer import JsonArtifactSink, ObserverCaptureError


_ACTIVE_NAME = "breadcrumbs.jsonl"
_HEALTH_NAME = "breadcrumbs.health.json"
_MARKER_NAME = "breadcrumbs.health.invalid"
_ROTATED_NAME = re.compile(r"^breadcrumbs-(\d+)-(\d+)\.jsonl$")
_MAX_HEALTH_BYTES = 256 * 1024
_READ_CHUNK_BYTES = 8192

# These are the fields emitted by the maintained breadcrumb producers.  The
# reader intentionally does not copy arbitrary metadata, message text, or
# labels into the evidence artifact.
_CATEGORIES = frozenset(
    {
        "queue.lifecycle", "queue.admission", "queue.executor", "queue.authority",
        "queue.readiness", "queue.exclusion", "model.lifecycle", "model.progress", "model_page_snapshot",
        "model.publication", "model.finalization", "model.root_progress", "status",
        "transfer.lftp", "transfer.lftp.executor", "transfer.lftp.membership",
        "transfer.lftp.status", "transfer.stop", "scan.authority", "scan.failure",
        "scan.result", "scan.terminal_publication", "final_move.publication",
        "finalization.child", "controller", "scanner_process", "lftp.sidecar",
        "lftp.command", "lftp.status", "completion.gate", "transfer.lftp.command", "root.progress",
    }
)
_SOURCES = frozenset(
    {"controller", "model_updater", "status", "scanner", "scanner_process", "lftp", "web", "system"}
)
_EVENT_TYPES = frozenset(
    {"queue", "callback", "status", "completion", "progress", "state_transition", "failure", "diagnostic", "breadcrumb"}
)
_STAGES = frozenset(
    {
        "admission", "dispatch", "publication", "terminal", "queue_lifecycle", "queue_authority_handoff",
        "queue_readiness", "queue_exclusion", "model", "model_progress", "model_delta", "model_update",
        "model_publication", "model_summary", "model_finalization", "scan", "scan_adoption", "scan_authority",
        "scan_accumulator", "extract", "controller", "controller_boundary", "persist_transaction", "lifecycle",
        "completion_gate", "final_move_publication", "finalization_child", "transfer_stop", "command",
        "lftp_executor", "lftp_sidecar_validation", "lftp_path_pair_annotation", "lftp_status_poll",
        "lftp_command_boundary", "scoped_model_stream", "retirement_cleanup", "path_pair_runtime",
        "root_progress_status", "completion_gate", "ordinary", "refresh", "finish", "transfer", "model_page_snapshot",
    }
)
_LEVELS = frozenset({"info", "warning", "error", "critical", "debug"})
_NUMERIC_KEYS = frozenset(
    {
        "attempt", "accepted", "age_ms", "bytes", "bytes_done", "bytes_total", "completed", "count",
        "duration_ms", "elapsed_ms", "expected_size", "files", "files_done", "files_total", "generation",
        "matched", "pending", "percent", "processed", "progress", "queue_depth", "retries", "scanned",
        "size", "status_code", "total", "transferred", "unknown", "unresolved", "version", "written",
    }
)
_HEALTH_COUNTERS = (
    "lost", "critical_lost", "normal_lost", "unknown", "unresolved", "writer_failures", "flush_failures",
    "partial_write_failures", "health_publish_failures", "retention_prune_failures",
)

_QUEUE_LIFECYCLE_EVENTS = frozenset({
    "queue_admitted", "executor_start", "executor_return", "executor_error", "lftp_child", "status_membership",
    "root_default", "queue_retired", "controller_force_close", "operation_scheduled", "operation_retired",
})
_QUEUE_LIFECYCLE_PHASES = frozenset({
    "admission", "executor", "lftp", "status", "publication", "retirement", "teardown", "precheck", "drain",
    "send", "prompt", "connecting", "error_recovery", "unknown",
})
_QUEUE_LIFECYCLE_OUTCOMES = frozenset({
    "accepted", "entered", "returned", "success", "observed", "present", "absent", "ambiguous", "published",
    "retired", "scheduled", "error", "unknown",
})
_QUEUE_LIFECYCLE_ERRORS = frozenset({
    "none", "eof", "exit", "signal", "timeout", "command_error", "parser_error", "terminal_backlog",
    "unhealthy_snapshot", "unknown",
})
_QUEUE_LIFECYCLE_BOUNDARIES = frozenset({
    "queue_admission", "executor", "retirement", "lftp_command", "status_membership", "root_publication",
    "queue_retirement", "teardown",
})
_QUEUE_LIFECYCLE_ENUMS = {
    "status_health": frozenset({"healthy", "unhealthy", "unknown"}),
    "membership": frozenset({"present", "absent", "ambiguous", "unknown"}),
    "status_parse": frozenset({"accepted_empty", "unknown"}),
    "status_result_shape": frozenset({"empty_or_prompt", "queue_done", "job_present", "ambiguous", "parse_error", "unknown"}),
    "status_recovery": frozenset({"none", "connection_grace", "unknown"}),
    "status_state": frozenset({"queued", "running", "none", "unknown"}),
    "membership_reason": frozenset({"filtered", "none", "unknown"}),
    "root_state": frozenset({"default", "unknown"}),
    "coverage": frozenset({"incomplete", "unknown"}),
    "publication_outcome": frozenset({"default_incomplete", "unknown"}),
    "read_buffer_source": frozenset({"public_buffer", "private_buffer", "unavailable"}),
    "duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+", "unknown"}),
    "read_buffer_byte_length_bucket": frozenset({"0", "1-64", "65-1024", "1025-16384", "16385-65536", "65537+", "unknown"}),
    "status_count_bucket": frozenset({"0", "1", "2+", "unknown"}),
    "command_kind": frozenset({"queue", "status", "unknown"}),
    "command_outcome": frozenset({"success", "prompt_timeout", "eof", "error", "unknown"}),
    "reaped": frozenset({"signal", "exit", "alive", "not_alive", "unknown"}),
    "future_state": frozenset({"finalized", "pending", "unknown"}),
}
_QUEUE_READINESS_SCHEMAS = frozenset({"queue_readiness.v1", "queue_readiness.v2"})
_QUEUE_READINESS_EVENTS = frozenset({"queue_callback", "queue_http_wait"})
_QUEUE_READINESS_PHASES = frozenset({"entry", "wait_return", "unknown"})
_QUEUE_READINESS_OUTCOMES = frozenset({"accepted", "success", "failure", "failed", "timeout", "unknown"})
_QUEUE_READINESS_ORIGINS = frozenset({"auto_queue", "manual", "unknown"})
_QUEUE_READINESS_REASONS = frozenset({
    "none", "awaiting_scan_publication", "initial_scan_authority_deadline", "comparison_pending",
    "scoped_rescan_token_unknown", "scoped_rescan_request_failed", "scoped_rescan_requested", "unknown",
})
_QUEUE_READINESS_BOOLEANS = frozenset({"ready"})
_MODEL_PAGE_SNAPSHOT_OUTCOMES = frozenset({"success", "error"})
_MODEL_PAGE_SNAPSHOT_ERRORS = frozenset({"cancelled", "model_error", "runtime_error", "unknown"})
_MODEL_PAGE_SNAPSHOT_NUMBERS = frozenset({
    "scope_version", "global_version", "lock_wait_ms", "lock_hold_ms", "snapshot_elapsed_ms",
})
_QUEUE_LIFECYCLE_BOOLS = frozenset({
    "fresh", "healthy", "process_alive_before", "process_alive_after", "process_alive",
    "pre_send_drain_queue_done", "pre_send_drain_job_or_progress", "pre_send_drain_prompt_or_echo",
})
_COMPLETION_GATE_DECISIONS = frozenset({"blocked", "no_retirement", "excluded", "pending", "attempt_eligible", "deferred", "unknown"})
_COMPLETION_GATE_REASONS = frozenset({
    "completion_detection_not_authoritative", "still_active", "explicit_stop", "lftp_job_finished", "candidate_pair_unselected",
    "no_model_diff", "candidate_authorized", "adopted", "retry_or_failure_limit", "completion_evidence_missing",
    "completion_authority_missing", "complete_local_coverage", "unknown",
})
_COMPLETION_GATE_OUTCOMES = frozenset({"stopped", "deferred", "downloaded", "unknown"})
_COMPLETION_GATE_BOOLS = frozenset({
    "poll_eligible", "poll_fresh", "poll_healthy", "previous_active", "current_active", "marker_observed", "local_scan_forced",
    "explicit_stop", "build_ran", "candidate_present", "live_present", "local_size_present", "remote_size_present",
    "complete_local_coverage", "exact_final_file_proof", "model_diff_present", "completion_proved", "physical_proof",
})
_LFTP_COMMAND_KINDS = frozenset({"get", "mirror", "pget"})
_LFTP_COMMAND_PHASES = frozenset({"submitted", "prompt_ready", "prompt_timeout", "process_eof", "backend_error"})
_LFTP_COMMAND_OUTPUTS = frozenset({"empty", "connecting", "backend_error", "other"})
_LFTP_COMMAND_COUNT_BUCKETS = frozenset({"0", "1", "2-4", "5-16", "17-64", "65-256", "257+", "unknown"})
_LFTP_COMMAND_BYTE_BUCKETS = frozenset({"0", "1-127", "128-511", "512-2047", "2048-8191", "8192-32767", "32768+", "unknown"})


def _hash(value: object) -> str:
    """Return a fixed, non-reversible correlation token."""
    if not isinstance(value, str):
        value = ""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


def _counter(value: object) -> int:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else 0


def _loss_snapshot(health: Mapping[str, object]) -> dict[str, int]:
    return {key: _counter(health.get(key)) for key in _HEALTH_COUNTERS}


def _deltas(current: Mapping[str, object], baseline: Mapping[str, int]) -> dict[str, int]:
    values: dict[str, int] = {}
    for key in _HEALTH_COUNTERS:
        now = _counter(current.get(key))
        old = baseline.get(key, 0)
        values[key] = now - old if now >= old else -1
    return values


def _numeric_values(row: Mapping[str, object], metadata: Mapping[str, object], details: Mapping[str, object]) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for source in (row, metadata, details):
        for key in _NUMERIC_KEYS:
            value = source.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if isinstance(value, float) and (not math.isfinite(value) or abs(value) > 2**63 - 1):
                continue
            if abs(value) > 2**63 - 1:
                continue
            result[key] = value
    return result


def _queue_lifecycle_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    if details.get("schema") != "queue.lifecycle.v1":
        return None
    def fixed(key: str, allowed: frozenset[str]) -> str:
        value = details.get(key)
        return value if isinstance(value, str) and value in allowed else "unknown"
    facts: dict[str, object] = {
        "schema": "queue.lifecycle.v1",
        "event": fixed("event", _QUEUE_LIFECYCLE_EVENTS),
        "phase": fixed("phase", _QUEUE_LIFECYCLE_PHASES),
        "boundary": fixed("boundary", _QUEUE_LIFECYCLE_BOUNDARIES),
        "outcome": fixed("outcome", _QUEUE_LIFECYCLE_OUTCOMES),
        "error_class": fixed("error_class", _QUEUE_LIFECYCLE_ERRORS),
    }
    for key, allowed in _QUEUE_LIFECYCLE_ENUMS.items():
        value = details.get(key)
        if isinstance(value, str) and value in allowed:
            facts[key] = value
    for key in _QUEUE_LIFECYCLE_BOOLS:
        value = details.get(key)
        if type(value) is bool:
            facts[key] = value
    return facts


def _completion_gate_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    fields = {"decision", "reason", "terminal_outcome", "pending_transition", "explicit_stop"}
    if not fields.intersection(details):
        return None
    result: dict[str, object] = {"boundary": "completion_gate"}
    for key, allowed in (("decision", _COMPLETION_GATE_DECISIONS), ("reason", _COMPLETION_GATE_REASONS), ("terminal_outcome", _COMPLETION_GATE_OUTCOMES)):
        value = details.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    value = details.get("pending_transition")
    if isinstance(value, str) and value in {"registered", "cleared", "retained"}:
        result["pending_transition"] = value
    for key in _COMPLETION_GATE_BOOLS:
        value = details.get(key)
        if type(value) is bool:
            result[key] = value
    return result


def _lftp_command_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    if details.get("schema") != "lftp.command_boundary.v1":
        return None
    result: dict[str, object] = {"schema": "lftp.command_boundary.v1"}
    for key, allowed in (("transfer_kind", _LFTP_COMMAND_KINDS), ("phase", _LFTP_COMMAND_PHASES), ("output_class", _LFTP_COMMAND_OUTPUTS), ("argument_count_bucket", _LFTP_COMMAND_COUNT_BUCKETS), ("exclude_count_bucket", _LFTP_COMMAND_COUNT_BUCKETS), ("submission_byte_length_bucket", _LFTP_COMMAND_BYTE_BUCKETS)):
        value = details.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    if type(details.get("process_alive")) is bool:
        result["process_alive"] = details["process_alive"]
    return result


def _queue_readiness_facts(message: object, details: Mapping[str, object]) -> dict[str, object] | None:
    """Project only the finite, source-shaped Queue readiness discriminator."""
    schema = details.get("schema")
    # The maintained producer writes the event discriminator in row.message;
    # details are the finite v1 payload and are not authoritative for it.
    if schema not in _QUEUE_READINESS_SCHEMAS or message not in _QUEUE_READINESS_EVENTS:
        return None
    event = message
    result: dict[str, object] = {"schema": schema, "event": event}
    for key, allowed in (
        ("phase", _QUEUE_READINESS_PHASES),
        ("outcome", _QUEUE_READINESS_OUTCOMES),
        ("origin", _QUEUE_READINESS_ORIGINS),
        ("reason", _QUEUE_READINESS_REASONS),
    ):
        value = details.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
        else:
            result[key] = "unknown"
    for key in _QUEUE_READINESS_BOOLEANS:
        value = details.get(key)
        if type(value) is bool:
            result[key] = value
    for key in ("callback_index", "callback_count", "error_code", "queue_depth"):
        value = details.get(key)
        if type(value) is int and 0 <= value <= 2**63 - 1:
            result[key] = value
    return result


def _model_page_snapshot_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    """Project the maintained model-page diagnostic's finite source fields."""
    outcome = details.get("outcome")
    if not isinstance(outcome, str) or outcome not in _MODEL_PAGE_SNAPSHOT_OUTCOMES:
        return None
    result: dict[str, object] = {"outcome": outcome}
    if outcome == "error":
        error_class = details.get("error_class")
        result["error_class"] = error_class if isinstance(error_class, str) and error_class in _MODEL_PAGE_SNAPSHOT_ERRORS else "unknown"
    for key in _MODEL_PAGE_SNAPSHOT_NUMBERS:
        value = details.get(key)
        if type(value) is int and 0 <= value <= 2**31 - 1:
            result[key] = value
    return result


def _projection(row: Mapping[str, object]) -> tuple[dict[str, object] | None, bool]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    details = row.get("details") if isinstance(row.get("details"), Mapping) else {}
    assert isinstance(metadata, Mapping) and isinstance(details, Mapping)
    raw_category = metadata.get("category")
    category = raw_category if isinstance(raw_category, str) and raw_category in _CATEGORIES else "unknown"
    if category == "unknown":
        return None, True
    raw_source = row.get("source", metadata.get("source"))
    source = raw_source if isinstance(raw_source, str) and raw_source in _SOURCES else "unknown"
    raw_event = metadata.get("event_type")
    event_type = raw_event if isinstance(raw_event, str) and raw_event in _EVENT_TYPES else "unknown"
    raw_stage = metadata.get("stage")
    stage = raw_stage if isinstance(raw_stage, str) and raw_stage in _STAGES else "unknown"
    raw_level = metadata.get("level")
    level = raw_level if isinstance(raw_level, str) and raw_level in _LEVELS else "unknown"
    numbers = _numeric_values(row, metadata, details)
    lifecycle = _queue_lifecycle_facts(details) if category == "queue.lifecycle" else None
    gate = _completion_gate_facts(details) if category == "completion.gate" else None
    command = _lftp_command_facts(details) if category == "transfer.lftp.command" else None
    readiness = _queue_readiness_facts(row.get("message"), details) if category == "queue.readiness" else None
    model_page_snapshot = _model_page_snapshot_facts(details) if category == "model_page_snapshot" else None
    if category == "queue.readiness" and readiness is None:
        return None, True
    if category == "model_page_snapshot" and model_page_snapshot is None:
        return None, True
    lifecycle_event = lifecycle.get("event") if lifecycle else None
    if event_type == "completion" or stage == "terminal" or lifecycle_event in {"queue_retired", "operation_retired"} or details.get("final") is True or details.get("completed") is True:
        kind = "completion"
    elif category == "model.progress" or event_type == "progress" or lifecycle_event == "status_membership" or any(key in numbers for key in ("progress", "percent", "processed")):
        kind = "progress"
    elif category in {"status", "transfer.lftp.status", "lftp.status"} or event_type == "status":
        kind = "status"
    else:
        kind = "event"
    result = {
        "kind": kind,
        "source": source,
        "category": category,
        "event_type": event_type,
        "stage": stage,
        "level": level,
        "numbers": numbers,
    }
    if lifecycle is not None:
        result["lifecycle"] = lifecycle
    if gate is not None:
        result["completion_gate"] = gate
    if command is not None:
        result["lftp_command"] = command
    if readiness is not None:
        result["queue_readiness"] = readiness
    if model_page_snapshot is not None:
        result["model_page_snapshot"] = model_page_snapshot
    return result, False


@dataclass
class _FileState:
    fd: int
    dev: int
    ino: int
    name: str
    cursor: int
    tail: bytes
    active: bool
    last_seen: int


class BreadcrumbSpoolCapture:
    """Read a bounded, typed projection from an already verified spool FD."""

    def __init__(
        self,
        spool: int | str | os.PathLike[str],
        sink: JsonArtifactSink,
        *,
        max_bytes: int = 131072,
        max_millis: int = 250,
        max_records: int = 128,
        max_tail_bytes: int = 65536,
        max_files: int = 32,
        max_output_bytes: int = 262144,
    ) -> None:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
            raise ObserverCaptureError("capture byte bound is invalid")
        if type(max_millis) is not int or not 1 <= max_millis <= 2000:
            raise ObserverCaptureError("capture time bound is invalid")
        if type(max_records) is not int or not 4 <= max_records <= 4096:
            raise ObserverCaptureError("capture record bound is invalid")
        if type(max_tail_bytes) is not int or not 1 <= max_tail_bytes <= 65536:
            raise ObserverCaptureError("capture partial-line bound is invalid")
        if type(max_files) is not int or not 2 <= max_files <= 256:
            raise ObserverCaptureError("capture file bound is invalid")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 4 * 1024 * 1024:
            raise ObserverCaptureError("capture output bound is invalid")
        self.sink = sink
        self.max_bytes = max_bytes
        self.max_millis = max_millis
        self.max_records = max_records
        self.max_tail_bytes = max_tail_bytes
        self.max_files = max_files
        self.max_output_bytes = max_output_bytes
        self._directory_fd = self._take_directory_fd(spool)
        self._owns_directory_fd = True
        self._files: OrderedDict[tuple[int, int], _FileState] = OrderedDict()
        self._session: str | None = None
        self._health_baseline: dict[str, int] = {}
        self._last_health: dict[str, object] = {}
        self._active_key: tuple[int, int] | None = None
        self._armed = False
        self._terminal = False
        self._poll_index = 0
        self._emitted_records = 0
        self._emitted_bytes = 0
        self._source_bytes = 0
        self._unsupported_records = 0
        self._loss_reason: str | None = None
        self._loss_count = 0

    @classmethod
    def from_verified_directory_fd(cls, directory_fd: int, sink: JsonArtifactSink, **kwargs: object) -> "BreadcrumbSpoolCapture":
        return cls(directory_fd, sink, **kwargs)

    @staticmethod
    def _take_directory_fd(spool: int | str | os.PathLike[str]) -> int:
        if isinstance(spool, int):
            try:
                info = os.fstat(spool)
            except OSError as exc:
                raise ObserverCaptureError("verified spool directory FD is unavailable") from exc
            if not stat.S_ISDIR(info.st_mode):
                raise ObserverCaptureError("verified spool directory FD is not a directory")
            try:
                return os.dup(spool)
            except OSError as exc:
                raise ObserverCaptureError("verified spool directory FD cannot be held") from exc
        path = Path(spool)
        try:
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ObserverCaptureError("spool path is not a real directory")
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            after = os.fstat(descriptor)
            if (int(before.st_dev), int(before.st_ino)) != (int(after.st_dev), int(after.st_ino)):
                os.close(descriptor)
                raise ObserverCaptureError("spool directory changed while opening")
            return descriptor
        except ObserverCaptureError:
            raise
        except OSError as exc:
            raise ObserverCaptureError("spool directory cannot be opened safely") from exc

    def close(self) -> None:
        for state in list(self._files.values()):
            try:
                os.close(state.fd)
            except OSError:
                pass
        self._files.clear()
        if self._owns_directory_fd:
            try:
                os.close(self._directory_fd)
            except OSError:
                pass
            self._owns_directory_fd = False

    def __enter__(self) -> "BreadcrumbSpoolCapture":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _marker_present(self) -> bool:
        try:
            info = os.stat(_MARKER_NAME, dir_fd=self._directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ObserverCaptureError("spool marker cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ObserverCaptureError("spool marker is unsafe")
        return True

    def _read_health(self) -> dict[str, object]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(_HEALTH_NAME, flags, dir_fd=self._directory_fd)
        except OSError as exc:
            raise ObserverCaptureError("spool health is unavailable") from exc
        try:
            info = os.fstat(fd)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ObserverCaptureError("spool health is unsafe")
            if info.st_size > _MAX_HEALTH_BYTES:
                raise ObserverCaptureError("spool health is oversized")
            chunks: list[bytes] = []
            remaining = int(info.st_size)
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            try:
                value = json.loads(b"".join(chunks).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ObserverCaptureError("spool health is unreadable") from exc
        except ObserverCaptureError:
            raise
        except OSError as exc:
            raise ObserverCaptureError("spool health is unreadable") from exc
        finally:
            os.close(fd)
        if not isinstance(value, dict) or value.get("schema") != "breadcrumb_durable_health.v1":
            raise ObserverCaptureError("spool health schema is invalid")
        session = value.get("session_id")
        if not isinstance(session, str) or not session:
            raise ObserverCaptureError("spool session is invalid")
        if value.get("offline_retrieval") != "valid" or value.get("session_boundary_written") is not True:
            raise ObserverCaptureError("spool health is not armable")
        if value.get("health_publish_ok") is not True:
            raise ObserverCaptureError("spool health publication is invalid")
        for key in (*_HEALTH_COUNTERS, "rotation_count"):
            counter = value.get(key)
            if type(counter) is not int or not 0 <= counter <= 2**63 - 1:
                raise ObserverCaptureError("spool health counter is invalid")
        return value

    def _inventory(self) -> dict[str, tuple[int, int, int]]:
        result: dict[str, tuple[int, int, int]] = {}
        try:
            entries = os.scandir(self._directory_fd)
        except OSError as exc:
            raise ObserverCaptureError("spool directory cannot be inventoried") from exc
        try:
            for entry in entries:
                name = entry.name
                if name != _ACTIVE_NAME and _ROTATED_NAME.fullmatch(name) is None:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ObserverCaptureError("spool file cannot be inspected") from exc
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise ObserverCaptureError("spool file is not a regular file")
                result[name] = (int(info.st_dev), int(info.st_ino), int(info.st_size))
        finally:
            entries.close()
        return result

    def _open_file(self, name: str, *, cursor: int, active: bool) -> _FileState:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = -1
        try:
            fd = os.open(name, flags, dir_fd=self._directory_fd)
            info = os.fstat(fd)
        except OSError as exc:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise ObserverCaptureError("spool file cannot be opened") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise ObserverCaptureError("spool file is not a regular file")
        if cursor < 0 or cursor > int(info.st_size):
            os.close(fd)
            raise ObserverCaptureError("spool cursor is invalid")
        key = (int(info.st_dev), int(info.st_ino))
        return _FileState(fd, key[0], key[1], name, cursor, b"", active, self._poll_index)

    def _close_file(self, key: tuple[int, int]) -> None:
        state = self._files.pop(key, None)
        if state is not None:
            try:
                os.close(state.fd)
            except OSError:
                pass

    def _emit(self, state: str, *, cursor_start: int = 0, cursor_end: int = 0, bytes_read: int = 0,
              records: int = 0, reason: str | None = None, projection: Mapping[str, object] | None = None,
              health: Mapping[str, object] | None = None, budget: Mapping[str, object] | None = None,
              **extra: object) -> bool:
        if self._terminal and state != "stopped":
            return False
        current_health = health or {}
        delta = _deltas(current_health, self._health_baseline)
        capture: dict[str, object] = {
            "state": state,
            "utc_ms": int(time.time_ns() // 1_000_000),
            "monotonic_ms": int(time.monotonic() * 1000),
            "cursor_start": max(0, int(cursor_start)),
            "cursor_end": max(0, int(cursor_end)),
            "bytes_read": max(0, int(bytes_read)),
            "records": max(0, int(records)),
            "lost": max(0, delta.get("lost", 0)),
            "health_lost": max(0, delta.get("lost", 0)),
            "health_unknown": max(0, delta.get("unknown", 0)),
            "health_unresolved": max(0, delta.get("unresolved", 0)),
            "session_hash": _hash(self._session),
            "rotation_count": _counter(current_health.get("rotation_count")),
        }
        if reason is not None:
            capture["reason"] = reason
        if projection is not None:
            capture["projection"] = dict(projection)
        if budget is not None:
            capture["budget"] = dict(budget)
        capture.update(extra)
        artifact = {"schema": "breadcrumb-spool-capture.v1", "event": "spool", "phase": state, "outcome": "success", "capture": capture}
        encoded_size = len(json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        # Leave room for one terminal receipt.  A minimal loss/terminal receipt
        # is still attempted when the ordinary output budget is exhausted.
        if state not in {"loss", "stopped"} and (
            self._emitted_records >= self.max_records - 2 or self._emitted_bytes + encoded_size > self.max_output_bytes
        ):
            return False
        try:
            self.sink.persist(artifact)
        except Exception as exc:
            raise ObserverCaptureError("capture artifact cannot be persisted") from exc
        self._emitted_records += 1
        self._emitted_bytes += encoded_size
        if state == "stopped":
            self._terminal = True
        return True

    def _loss(self, reason: str, health: Mapping[str, object], **fields: object) -> None:
        self._loss_reason = reason
        self._loss_count += 1
        self._emit("loss", reason=reason, health=health, **fields)
        self._terminal = True

    def start(self) -> None:
        if self._armed:
            raise ObserverCaptureError("capture is already armed")
        if self._marker_present():
            raise ObserverCaptureError("spool marker is present")
        h0 = self._read_health()
        i0 = self._inventory()
        if _ACTIVE_NAME not in i0:
            raise ObserverCaptureError("active spool file is missing")
        if len(i0) > self.max_files:
            raise ObserverCaptureError("spool file inventory exceeds capture FD bound")
        opened: list[_FileState] = []
        try:
            for name in sorted(i0, key=lambda item: (item != _ACTIVE_NAME, item)):
                dev, ino, size = i0[name]
                state = self._open_file(name, cursor=size, active=name == _ACTIVE_NAME)
                if (state.dev, state.ino) != (dev, ino):
                    raise ObserverCaptureError("spool file changed while arming")
                opened.append(state)
            # The descriptor, rather than a pathname stat, defines the arm
            # boundary.  Recheck EOF after all opens so rows written while the
            # inventory was being acquired remain pre-arm data.
            for state in opened:
                size = int(os.fstat(state.fd).st_size)
                if size < state.cursor:
                    raise ObserverCaptureError("spool file changed while arming")
                state.cursor = size
            h1 = self._read_health()
            i1 = self._inventory()
            if self._marker_present() or h1.get("session_id") != h0.get("session_id") or _loss_snapshot(h1) != _loss_snapshot(h0):
                raise ObserverCaptureError("spool health changed while arming")
            if set(i1) != set(i0) or any(i1[name][:2] != i0[name][:2] for name in i0):
                raise ObserverCaptureError("spool inventory changed while arming")
            self._files = OrderedDict(((state.dev, state.ino), state) for state in opened)
            self._active_key = next(key for key, state in self._files.items() if state.active)
            self._session = str(h1["session_id"])
            self._health_baseline = _loss_snapshot(h1)
            self._last_health = dict(h1)
            self._armed = True
            self._emit("armed", health=h1, budget=self._budget_snapshot())
        except Exception:
            for state in opened:
                try:
                    os.close(state.fd)
                except OSError:
                    pass
            raise

    def _budget_snapshot(self, *, bytes_read: int = 0, elapsed_ms: int = 0) -> dict[str, object]:
        return {
            "max_bytes": self.max_bytes,
            "max_millis": self.max_millis,
            "max_records": self.max_records,
            "max_output_bytes": self.max_output_bytes,
            "bytes_read": max(0, bytes_read),
            "elapsed_ms": max(0, elapsed_ms),
        }

    def _discover(self, inventory: Mapping[str, tuple[int, int, int]], health: Mapping[str, object]) -> None:
        active_info = inventory.get(_ACTIVE_NAME)
        if active_info is None:
            self._loss("missing", health)
            raise ObserverCaptureError("active spool file disappeared")
        current_active = (active_info[0], active_info[1])
        known_keys = set(self._files)
        # Existing descriptors are authoritative even after rename.  Refresh
        # their display names without reopening a path.
        for name, (dev, ino, _size) in inventory.items():
            key = (dev, ino)
            state = self._files.get(key)
            if state is not None:
                state.name = name
                state.active = name == _ACTIVE_NAME
                state.last_seen = self._poll_index
        if current_active not in known_keys:
            if self._active_key is None or self._active_key not in self._files:
                self._loss("unknown", health)
                raise ObserverCaptureError("new active has no known predecessor")
            # The previous active must still be visible under an app-owned
            # rotated name.  A vanished descriptor or an arbitrary rename is
            # not enough evidence to call the replacement an unambiguous
            # rotation, so never begin such a file at offset zero.
            if self._active_key not in {(dev, ino) for dev, ino, _size in inventory.values()}:
                self._loss("unknown", health)
                raise ObserverCaptureError("new active rotation is ambiguous")
            unknown_rotated = [
                name for name, (dev, ino, _size) in inventory.items()
                if name != _ACTIVE_NAME and (dev, ino) not in known_keys
            ]
            if unknown_rotated:
                self._loss("unseen_rotation", health)
                raise ObserverCaptureError("rotated spool file was not observed")
            state = self._open_file(_ACTIVE_NAME, cursor=0, active=True)
            self._files[(state.dev, state.ino)] = state
        self._active_key = current_active
        # A new rotated path that was not held from arm/previous poll cannot be
        # attributed without risking a gap, even if its name looks app-owned.
        for name, (dev, ino, _size) in inventory.items():
            if name != _ACTIVE_NAME and (dev, ino) not in self._files:
                self._loss("unseen_rotation", health)
                raise ObserverCaptureError("unseen rotated spool file")
        self._prune(health)

    def _prune(self, health: Mapping[str, object]) -> None:
        if len(self._files) <= self.max_files:
            return
        candidates = sorted(
            (state for key, state in self._files.items() if key != self._active_key),
            key=lambda item: item.last_seen,
        )
        for state in candidates:
            try:
                size = int(os.fstat(state.fd).st_size)
            except OSError as exc:
                self._loss("unread", health)
                raise ObserverCaptureError("held spool file cannot be inspected") from exc
            if state.cursor < size or state.tail:
                self._loss("prune", health)
                raise ObserverCaptureError("file cap would prune unread spool data")
            self._close_file((state.dev, state.ino))
            if len(self._files) <= self.max_files:
                break

    def _read_state(self, state: _FileState, remaining: int, started: float, health: Mapping[str, object]) -> tuple[int, int, int, bool]:
        try:
            info = os.fstat(state.fd)
        except OSError as exc:
            self._loss("unread", health)
            raise ObserverCaptureError("held spool file cannot be inspected") from exc
        if not stat.S_ISREG(info.st_mode):
            self._loss("unknown", health)
            raise ObserverCaptureError("held spool descriptor changed type")
        size = int(info.st_size)
        if size < state.cursor:
            self._loss("cursor", health, cursor_start=state.cursor, cursor_end=size)
            raise ObserverCaptureError("spool file was truncated")
        available = size - state.cursor
        if available <= 0:
            state.last_seen = self._poll_index
            return 0, 0, 0, False
        if remaining <= 0 or (time.monotonic() - started) * 1000 >= self.max_millis:
            return 0, 0, 0, True
        # Keep one descriptor read/parse unit small enough that the monotonic
        # poll deadline remains meaningful even when a single file is large.
        amount = min(available, remaining, _READ_CHUNK_BYTES)
        try:
            data = os.pread(state.fd, amount, state.cursor)
        except (AttributeError, OSError) as exc:
            self._loss("unread", health)
            raise ObserverCaptureError("spool file cannot be read") from exc
        if not data and amount:
            self._loss("unread", health)
            raise ObserverCaptureError("spool file read made no progress")
        start = state.cursor - len(state.tail)
        state.cursor += len(data)
        combined = state.tail + data
        chunks = combined.split(b"\n")
        tail = chunks.pop() if chunks else b""
        if len(tail) > self.max_tail_bytes:
            self._loss("oversized", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data))
            raise ObserverCaptureError("partial breadcrumb exceeds bound")
        state.tail = tail
        state.last_seen = self._poll_index
        parsed = emitted = irrelevant = 0
        for raw in chunks:
            line = raw[:-1] if raw.endswith(b"\r") else raw
            if not line:
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                self._loss("malformed", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data))
                raise ObserverCaptureError("malformed breadcrumb row")
            if not isinstance(value, Mapping):
                self._loss("malformed", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data))
                raise ObserverCaptureError("breadcrumb row is not an object")
            typed, is_irrelevant = _projection(value)
            if is_irrelevant or typed is None:
                irrelevant += 1
                continue
            metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
            details = value.get("details") if isinstance(value.get("details"), Mapping) else {}
            assert isinstance(metadata, Mapping) and isinstance(details, Mapping)
            projection = dict(typed)
            projection.update(
                {
                    "flow_hash": _hash(metadata.get("flow_id")),
                    "corr_hash": _hash(metadata.get("corr_id", metadata.get("correlation_id"))),
                    "file_hash": _hash(metadata.get("file_id")),
                    "pair_hash": _hash(metadata.get("path_pair_id")),
                }
            )
            if not self._emit(
                "record", cursor_start=start, cursor_end=state.cursor, bytes_read=len(data), records=1,
                health=health, projection=projection, created_ms=_counter(value.get("created_ms")),
            ):
                self._loss("output", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data), records=1)
                raise ObserverCaptureError("capture output cap refused a parsed row")
            parsed += 1
            emitted += 1
        if irrelevant:
            self._unsupported_records += irrelevant
            if not self._emit(
                "unsupported", cursor_start=start, cursor_end=state.cursor, bytes_read=len(data), records=irrelevant,
                reason="unsupported", health=health, irrelevant=irrelevant,
            ):
                self._loss("output", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data), records=irrelevant)
                raise ObserverCaptureError("capture output cap refused unsupported rows")
        return len(data), emitted, irrelevant, False

    def poll(self) -> int:
        if not self._armed:
            raise ObserverCaptureError("capture is not armed")
        if self._terminal:
            return 0
        self._poll_index += 1
        started = time.monotonic()
        try:
            h0 = self._read_health()
        except ObserverCaptureError:
            self._loss("unread", self._last_health)
            raise
        try:
            marker_before = self._marker_present()
        except ObserverCaptureError:
            self._loss("marker", h0)
            raise
        if marker_before:
            self._loss("marker", h0)
            raise ObserverCaptureError("spool marker appeared")
        if h0.get("session_id") != self._session:
            self._loss("session", h0)
            raise ObserverCaptureError("spool session changed")
        deltas = _deltas(h0, self._health_baseline)
        monitored = {"lost", "unknown", "unresolved", "writer_failures", "flush_failures", "partial_write_failures", "health_publish_failures", "retention_prune_failures"}
        if any(value < 0 for value in deltas.values()) or any(value > 0 for key, value in deltas.items() if key in monitored):
            self._loss("health", h0)
            raise ObserverCaptureError("spool health reports new loss")
        self._last_health = dict(h0)
        try:
            inventory = self._inventory()
            self._discover(inventory, h0)
        except ObserverCaptureError:
            if not self._terminal:
                self._loss("unknown", h0)
            raise
        total = emitted = 0
        budget_hit = False
        # A single read is deliberately capped at 8 KiB, but a poll must keep
        # draining until its configured byte/time budget is reached.  Without
        # this loop a nominal 256 KiB poll silently became an 8 KiB poll.
        for state in list(self._files.values()):
            while True:
                remaining = self.max_bytes - total
                read, count, _irrelevant, hit = self._read_state(state, remaining, started, h0)
                total += read
                self._source_bytes += read
                emitted += count
                budget_hit = budget_hit or hit
                if (
                    budget_hit
                    or total >= self.max_bytes
                    or (time.monotonic() - started) * 1000 >= self.max_millis
                    or read == 0
                ):
                    if total >= self.max_bytes or (time.monotonic() - started) * 1000 >= self.max_millis:
                        budget_hit = True
                    break
        try:
            h1 = self._read_health()
        except ObserverCaptureError:
            self._loss("unread", self._last_health, bytes_read=total)
            raise
        try:
            marker_after = self._marker_present()
        except ObserverCaptureError:
            self._loss("marker", h1, bytes_read=total)
            raise
        if marker_after or h1.get("session_id") != self._session:
            self._loss("marker" if marker_after else "session", h1, bytes_read=total)
            raise ObserverCaptureError("spool changed while reading")
        post_deltas = _deltas(h1, self._health_baseline)
        if any(value < 0 for value in post_deltas.values()) or any(value > 0 for key, value in post_deltas.items() if key in monitored):
            self._loss("health", h1, bytes_read=total)
            raise ObserverCaptureError("spool health reports new loss")
        self._last_health = dict(h1)
        if budget_hit:
            elapsed = int((time.monotonic() - started) * 1000)
            self._emit(
                "unsupported", reason="budget", bytes_read=total, records=emitted, health=h1,
                budget=self._budget_snapshot(bytes_read=total, elapsed_ms=elapsed),
            )
        return emitted

    def projection(self) -> dict[str, object]:
        """Return bounded reader state without exposing source labels or paths."""
        return {
            "armed": self._armed,
            "terminal": self._terminal,
            "session_hash": _hash(self._session),
            "polls": self._poll_index,
            "records": self._emitted_records,
            "bytes": self._emitted_bytes,
            "source_bytes": self._source_bytes,
            "unsupported": self._unsupported_records,
            "discarded": self._unsupported_records,
            "loss": {"count": self._loss_count, "reason": self._loss_reason},
            "budget": self._budget_snapshot(),
            "health": {
                key: _counter(self._last_health.get(key))
                for key in ("lost", "unknown", "unresolved", "rotation_count")
            },
        }

    def stop(self, *, health: Mapping[str, object] | None = None) -> None:
        if self._terminal:
            self.close()
            return
        if not self._armed:
            self.close()
            return
        # ``health`` remains a source-compatible argument for old callers but
        # is intentionally ignored.  A caller-supplied snapshot cannot prove
        # the marker/session/loss/state boundary at terminal publication.
        del health
        try:
            final_health = self._read_health()
        except ObserverCaptureError:
            self._loss("unread", self._last_health)
            self.close()
            raise
        try:
            marker = self._marker_present()
        except ObserverCaptureError:
            self._loss("marker", final_health)
            self.close()
            raise
        if marker:
            self._loss("marker", final_health)
            self.close()
            raise ObserverCaptureError("spool marker appeared while stopping")
        if final_health.get("session_id") != self._session:
            self._loss("session", final_health)
            self.close()
            raise ObserverCaptureError("spool session changed while stopping")
        deltas = _deltas(final_health, self._health_baseline)
        monitored = {"lost", "unknown", "unresolved", "writer_failures", "flush_failures", "partial_write_failures", "health_publish_failures", "retention_prune_failures"}
        if any(value < 0 for value in deltas.values()) or any(value > 0 for key, value in deltas.items() if key in monitored):
            self._loss("health", final_health)
            self.close()
            raise ObserverCaptureError("spool health reports new loss while stopping")
        if final_health.get("state") not in {"RUNNING", "TERMINAL"}:
            self._loss("unknown", final_health)
            self.close()
            raise ObserverCaptureError("spool health state is invalid while stopping")
        for state in self._files.values():
            try:
                size = int(os.fstat(state.fd).st_size)
            except OSError as exc:
                self._loss("unread", final_health)
                self.close()
                raise ObserverCaptureError("spool data cannot be inspected while stopping") from exc
            if state.cursor < size:
                self._loss("unread", final_health, cursor_start=state.cursor, cursor_end=size)
                self.close()
                raise ObserverCaptureError("spool data remains unread while stopping")
            if state.tail:
                self._loss("partial", final_health, cursor_start=max(0, state.cursor - len(state.tail)), cursor_end=state.cursor)
                self.close()
                raise ObserverCaptureError("partial spool data remains while stopping")
        self._last_health = dict(final_health)
        try:
            self._emit("stopped", health=final_health, budget=self._budget_snapshot())
        finally:
            self.close()
