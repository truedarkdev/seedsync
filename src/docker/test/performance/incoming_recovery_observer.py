"""Schema-safe, side-effect-gated observer support for Incoming recovery runs.

This module intentionally has no SeedSync application dependency.  Operators inject an
HTTP transport; `preflight` performs only GETs and `queue` refuses to invoke a
POST transport until the exact same gate instance has passed validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
import json


class ObserverSchemaError(ValueError):
    """A supported API response did not meet the observer contract."""


class ObserverCaptureError(ObserverSchemaError):
    """The observer could not persist a required capture boundary."""


@dataclass(frozen=True)
class QueueTransportResponse:
    """Status-bearing Queue transport result; its body is private input only."""

    status: int
    body: bytes = b""

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ObserverSchemaError("Queue transport status is invalid")
        if not isinstance(self.body, bytes):
            raise ObserverSchemaError("Queue transport body must be bytes")


@dataclass
class GuardedQueueRunner:
    """Run one preflighted Queue POST with a bounded passive observer.

    The caller owns transport construction, including its 35-second timeout;
    this small coordinator makes the one-shot gate and observer ordering
    testable without retaining credentials or response bodies.
    """

    gate: "QueueGate"
    get: Callable[[str], object]
    send: Callable[[str], object]
    start_passive: Callable[[], Callable[[], None]]
    environment: Mapping[str, str]
    client_timeout_seconds: int = 35

    def run(self) -> object:
        if self.environment.get("INCOMING_RECOVERY_ALLOW_QUEUE") != "1":
            raise ObserverSchemaError("Queue gate environment is not enabled")
        if self.client_timeout_seconds < 35:
            raise ObserverSchemaError("Queue client timeout must cover the handler budget")
        self.gate.preflight(self.get)
        stop_passive = self.start_passive()
        if not callable(stop_passive):
            raise ObserverCaptureError("passive observer did not provide a stop boundary")
        try:
            result = self.gate.queue(self.send)
        except BaseException as queue_error:
            try:
                stop_passive()
            except BaseException as stop_error:
                # The Queue outcome is the primary operation evidence.  Keep a
                # passive-capture failure as its cause without replacing it.
                raise queue_error from stop_error
            raise
        try:
            stop_passive()
        except BaseException:
            # A successful Queue cannot be accepted without its required
            # concurrent capture boundary.
            raise
        return result


@dataclass
class PassiveQueueCaller:
    """Source-controlled adapter that keeps sequential GET observation alive during Queue."""

    gate: "QueueGate"
    get: Callable[[str], object]
    send: Callable[[str], object]
    passive_paths: Sequence[str]
    artifact_path: str | os.PathLike[str]
    environment: Mapping[str, str]
    client_timeout_seconds: int = 35
    passive_sample_interval_seconds: float = 0.0

    def run(self) -> object:
        stop = threading.Event()
        started = threading.Event()
        failures: list[BaseException] = []

        def collect(
            started_boundary: threading.Event | None = None,
            stop_event: threading.Event | None = None,
        ) -> None:
            try:
                first_sample = True
                while True:
                    sampler = FixedGetSampler(self.passive_paths, artifact_path=self.artifact_path,
                                              continue_on_error=False)
                    sampler.sample(
                        self.get,
                        before_request=started_boundary.set if first_sample and started_boundary is not None else None,
                        stop_event=stop_event,
                    )
                    first_sample = False
                    if self.passive_sample_interval_seconds <= 0 or stop_event is None or \
                            stop_event.wait(self.passive_sample_interval_seconds):
                        return
            except BaseException as exc:
                failures.append(exc)

        def start_passive() -> Callable[[], None]:
            # Complete one sequential GET sample before the POST boundary, then
            # continue a bounded second sample concurrently with that request.
            collect()
            if failures:
                raise ObserverCaptureError("passive sample failed before Queue")
            # The second observer cannot authorize Queue merely because its
            # thread was scheduled: it first persists its own GET boundary.
            thread = threading.Thread(
                target=collect, args=(started, stop), name="incoming-queue-passive",
            )
            thread.start()
            if not started.wait(1):
                thread.join()
                if failures:
                    raise ObserverCaptureError("passive sample failed before Queue") from failures[0]
                raise ObserverCaptureError("passive observer did not start")
            def finish() -> None:
                stop.set()
                # The injected live GET has its own five-second transport
                # bound.  Never return while a daemon observer could still
                # modify evidence after the Queue outcome is reported.
                thread.join()
                if failures:
                    raise ObserverCaptureError("passive sample failed during Queue")
            return finish

        runner = GuardedQueueRunner(self.gate, self.get, self.send, start_passive,
                                    self.environment, self.client_timeout_seconds)
        return runner.run()


_SCAN_AUTHORITY_OUTCOMES = frozenset({"adopt", "publish", "reject", "no_op"})
_SCAN_AUTHORITY_REASONS = frozenset({
    "no_scan_event", "pair_delta_adopted", "source_buckets_adopted",
    "source_buckets_adopted_after_pair_fallback",
    "published_after_active_delta_rejection", "published_after_pair_fallback",
    "published", "active_delta_rejected", "pair_delta_fallback", "joint_not_final",
    "comparison_proven_noop", "unknown_overlay_retained", "no_change",
})
_QUEUE_PREFLIGHT_REASONS = frozenset({
    "initial_scan_authority_deadline",
    "initial_local_scan_unknown", "initial_remote_scan_unknown",
    "initial_local_scan_token_missing", "initial_remote_scan_token_missing",
    "initial_local_scan_failed", "initial_remote_scan_failed",
    "initial_local_scan_session_changed", "initial_remote_scan_session_changed",
    "initial_local_scan_generation_reset", "initial_remote_scan_generation_reset",
    "scoped_rescan_token_unknown", "scoped_rescan_request_failed",
    "stopped", "file_missing", "stop_state_unknown", "path_pair_refresh",
})

# These are intentionally small, fixed vocabularies. The observer may receive
# an arbitrary URL from a caller, but no URL or caller-provided route is ever
# written to an artifact or returned by the discriminator.
_ENDPOINT_CLASSES = frozenset({
    "path_pairs", "config", "model_summary", "model_roots", "status",
    "performance_diagnostics", "auth", "unknown",
})
_ARTIFACT_PHASES = frozenset({"before", "after"})
_MAX_REQUEST_LATENCY_MS = 7 * 24 * 60 * 60 * 1000.0
_SLOW_REQUEST_LATENCY_MS = 5000.0
_HIGH_CPU_PERCENT = 75.0
_LOW_CPU_PERCENT = 25.0
_ACTIVE_STAGE_WALL_SECONDS = 0.5

_SCAN_STAGES = frozenset({
    "local_scan_filesystem_traversal", "local_scan_managed_extract",
    "local_scan_staging_merge", "local_scan_aggregation",
    "local_scan_progress_publication", "remote_scan_transport_read",
    "remote_scan_stream_parsing", "remote_scan_aggregation",
    "remote_scan_progress_publication", "model_update_scan_intake",
})
_SERIALIZATION_STAGES = frozenset({
    "model_summary_serialization", "model_scoped_serialization",
    "model_summary_sse_emission", "model_scoped_sse_emission",
})
_LOCK_STAGES = frozenset({
    "model_update_lock_wait", "model_update_lock_hold",
    "model_update_finalization_model_lock_wait", "model_update_finalization_model_lock_hold",
})
_KNOWN_DIAGNOSTIC_STAGES = frozenset({
    *_SCAN_STAGES, *_SERIALIZATION_STAGES,
    "model_update_lock_wait", "model_update_lock_hold",
    "model_update_finalization_model_lock_wait", "model_update_finalization_model_lock_hold",
    "model_update_state_preparation", "model_update_status_ingestion",
    "model_update_builder_sync", "model_update_lifecycle_maintenance",
    "model_update_build_finalization", "model_builder_set_local_files",
    "model_builder_set_remote_files", "model_builder_set_active_files",
    "model_builder_set_lftp_statuses", "model_builder_set_stopped_files",
    "controller_process", "controller_job", "model_build",
})

def _mapping(payload: object, name: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ObserverSchemaError(f"{name} must be an object")
    return payload


def endpoint_class(path: object) -> str:
    """Return a fixed public class for a probe route without retaining its URL."""
    if not isinstance(path, str) or not path:
        return "unknown"
    try:
        parsed = urlsplit(path)
    except ValueError:
        return "unknown"
    route = parsed.path if parsed.path else path.split("?", 1)[0]
    if route == "/server/path-pairs":
        return "path_pairs"
    if route == "/server/config/get":
        return "config"
    if route == "/server/model/v1/summary":
        return "model_summary"
    if route == "/server/status":
        return "status"
    if route == "/server/admin/performance-diagnostics/v1":
        return "performance_diagnostics"
    if route.startswith("/server/auth/"):
        return "auth"
    if route.startswith("/server/model/v1/pairs/") and route.endswith("/roots"):
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if query == [("limit", "200")]:
            return "model_roots"
    return "unknown"


def _status_code(value: object) -> int | None:
    status = getattr(value, "status", getattr(value, "code", None))
    return status if type(status) is int and 100 <= status <= 599 else None


def _request_latency_ms(start_ns: object, end_ns: object) -> float | None:
    """Convert monotonic clock boundaries to a bounded, safe millisecond value."""
    if type(start_ns) is not int or type(end_ns) is not int or end_ns < start_ns:
        return None
    elapsed_ms = (end_ns - start_ns) / 1_000_000.0
    if not math.isfinite(elapsed_ms) or elapsed_ms < 0 or elapsed_ms > _MAX_REQUEST_LATENCY_MS:
        return None
    return round(elapsed_ms, 3)


def path_pairs(payload: object) -> Sequence[Mapping[str, Any]]:
    data = _mapping(payload, "path-pairs").get("data")
    if not isinstance(data, list) or not all(isinstance(item, Mapping) for item in data):
        raise ObserverSchemaError("path-pairs.data must be a list of objects")
    return data


def model_roots(payload: object) -> Sequence[Mapping[str, Any]]:
    records = _mapping(payload, "model roots").get("records")
    if not isinstance(records, list) or not all(isinstance(item, Mapping) for item in records):
        raise ObserverSchemaError("model-roots.records must be a list of objects")
    return records


def autoqueue_enabled(payload: object) -> bool:
    autoqueue = _mapping(payload, "config").get("autoqueue")
    if not isinstance(autoqueue, Mapping) or not isinstance(autoqueue.get("enabled"), bool):
        raise ObserverSchemaError("config.autoqueue.enabled must be a boolean")
    return autoqueue["enabled"]


def controller_status(payload: object) -> Mapping[str, Any]:
    controller = _mapping(payload, "status").get("controller")
    if not isinstance(controller, Mapping):
        raise ObserverSchemaError("status.controller must be an object")
    return controller


def scan_authority_evidence(payload: object) -> Mapping[str, object]:
    """Return the public, aggregate scan lifecycle needed to explain Queue."""
    authority = _mapping(payload, "model summary").get("scan_authority")
    if not isinstance(authority, Mapping):
        raise ObserverSchemaError("model summary.scan_authority must be an object")

    integer_keys = (
        "publication_id", "model_version", "local_scan_generation",
        "remote_scan_generation", "scanned_pair_count", "completed_pair_count",
        "unknown_pair_count",
    )
    boolean_keys = ("final", "joint_final", "joint_authoritative", "joint_authoritative_after")
    evidence: dict[str, object] = {"schema": "incoming-recovery-scan-lifecycle.v1"}
    for key in integer_keys:
        value = authority.get(key)
        evidence[key] = value if type(value) is int and 0 <= value <= 2_147_483_647 else None
    for key in boolean_keys:
        value = authority.get(key)
        evidence[key] = value if type(value) is bool else None
    # These fixed controller enums explain publication state without exposing
    # path-pair identity, scanner session tokens, request identity, or payloads.
    outcome = authority.get("outcome")
    reason = authority.get("reason")
    evidence["outcome"] = outcome if isinstance(outcome, str) and outcome in _SCAN_AUTHORITY_OUTCOMES else "unknown"
    evidence["reason"] = reason if isinstance(reason, str) and reason in _SCAN_AUTHORITY_REASONS else "unknown"
    return evidence


def queue_response_evidence(status_code: object, body: object) -> Mapping[str, object]:
    """Classify Queue HTTP responses without retaining untrusted response text."""
    status = status_code if type(status_code) is int and 100 <= status_code <= 599 else None
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body if isinstance(body, str) else ""
    normalized = " ".join(text.split())
    reason = "response_unavailable"
    if status == 200:
        reason = "queue_accepted" if normalized.startswith("Queued file '") else "success_response"
    elif status == 409:
        prefix = "Queue preflight cancelled: "
        if normalized.startswith(prefix):
            candidate = normalized[len(prefix):]
            reason = candidate if candidate in _QUEUE_PREFLIGHT_REASONS else "preflight_cancelled_unrecognized"
        elif normalized == "Path pair relocation is in progress":
            reason = "path_pair_relocation"
        elif normalized == "Local deletion is still pending; Queue was rejected":
            reason = "local_delete_pending"
        elif normalized == "Queue collision cleanup is still pending; Queue was rejected":
            reason = "collision_cleanup_pending"
        elif normalized == "Queue preflight is not safe":
            reason = "queue_preflight_unsafe"
        elif normalized == "Queue stop state is unavailable; Queue was rejected":
            reason = "stop_state_unknown"
        elif "has no transferable remote content" in normalized:
            reason = "remote_content_unavailable"
        elif normalized == "Queue supports directory roots only; queue the containing directory":
            reason = "directory_root_invalid"
        else:
            reason = "unrecognized_409_response"
    elif status is not None:
        reason = "http_{}".format(status)
    return {
        "schema": "incoming-recovery-queue-response.v1",
        "status_code": status,
        "body_reason": reason,
    }


def _safe_error_type(exc: BaseException) -> str:
    """Return a fixed public category; never expose a custom class name."""
    if isinstance(exc, HTTPError):
        if exc.code in (401, 403):
            return "auth_error"
        return "http_error"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "transport_error"
    return "observer_error"


def error_artifact(exc: Exception) -> Mapping[str, object]:
    """Return a bounded JSON-safe error record without request or payload details."""
    artifact: dict[str, object] = {
        "schema": "incoming-recovery-observer-error.v1", "kind": "preflight_error",
        "error_type": _safe_error_type(exc),
    }
    if isinstance(exc, HTTPError):
        status = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else None
        artifact["status_code"] = status
    return artifact


_ARTIFACT_PRIVATE_KEYS = frozenset({
    "url", "request_url", "path", "request_path", "headers", "header",
    "body", "response_body", "payload", "credentials", "credential", "token",
    "password", "secret", "message", "detail", "file_id", "pair_id", "root_id",
})
_ARTIFACT_ROOT_STATES = frozenset({"default", "active", "queued", "stopped", "complete", "unknown"})
_ARTIFACT_ALLOWED_KEYS = frozenset({
    "schema", "event", "phase", "outcome", "success", "kind", "error_type",
    "status_code", "body_reason", "request_index", "response_kind", "root_state",
    "root_found", "target_verified", "root_verified", "first_failure", "evidence",
    "error", "failure", "capture_failure", "queue_evidence", "scan_lifecycle", "sample_index",
    "endpoint_class", "request_latency_ms", "classification", "reason", "request", "diagnostics", "capture",
    "utc_ms", "monotonic_ms", "timeout_seconds", "timeout", "invocation_utc_ms", "invocation_monotonic_ms",
    "response_utc_ms", "response_monotonic_ms", "timeout_utc_ms", "timeout_monotonic_ms", "completion_proven", "response_outcome", "physical_outcome",
    "capture_start_utc_ms", "capture_start_monotonic_ms", "capture_end_utc_ms", "capture_end_monotonic_ms",
    "model_outcome", "reader_outcome", "timed_out", "config_hash", "runtime_hash", "workload_hash",
    "selector_hash", "leaf_hash", "model_version",
    "model_snapshot",
    "runtime_version",
})
_ARTIFACT_SAFE_REASONS = _QUEUE_PREFLIGHT_REASONS | frozenset({
    "queue_accepted", "success_response", "response_unavailable", "path_pair_relocation",
    "local_delete_pending", "collision_cleanup_pending", "queue_preflight_unsafe",
    "remote_content_unavailable", "directory_root_invalid", "unrecognized_409_response",
    "preflight_cancelled_unrecognized", "first_failure", "get_failed", "get_succeeded",
})
_ARTIFACT_ERROR_TYPES = frozenset({
    "auth_error", "http_error", "timeout", "transport_error", "observer_error",
})
_ARTIFACT_RESPONSE_KINDS = frozenset({"none", "mapping", "bytes", "text", "number", "object"})


def _safe_body_reason(value: object) -> str:
    if isinstance(value, str) and value in _ARTIFACT_SAFE_REASONS:
        return value
    if isinstance(value, str) and value.startswith("http_"):
        code = value[5:]
        if len(code) == 3 and code.isdigit() and 100 <= int(code) <= 599:
            return value
    return "unknown"


_CAPTURE_SOURCES = frozenset({
    "controller", "model_updater", "status", "scanner", "scanner_process", "lftp", "web", "system", "unknown",
})
_CAPTURE_EVENTS = frozenset({
    "queue", "callback", "status", "completion", "progress", "state_transition", "failure", "diagnostic", "breadcrumb", "unknown",
})
_CAPTURE_CATEGORIES = frozenset({
    "queue.lifecycle", "queue.admission", "queue.executor", "queue.authority", "queue.readiness", "queue.exclusion", "model_page_snapshot",
    "model.lifecycle", "model.progress", "model.publication", "model.finalization", "model.root_progress", "status",
    "transfer.lftp", "transfer.lftp.executor", "transfer.lftp.membership", "transfer.lftp.status", "transfer.stop",
    "scan.authority", "scan.failure", "scan.result", "scan.terminal_publication", "final_move.publication",
    "finalization.child", "controller", "scanner_process", "lftp.sidecar", "lftp.command", "lftp.status",
    "completion.gate", "transfer.lftp.command", "root.progress", "unknown",
})
_CAPTURE_STAGES = frozenset({
    "admission", "dispatch", "publication", "terminal", "queue_lifecycle", "queue_authority_handoff", "queue_readiness",
    "queue_exclusion", "model", "model_progress", "model_delta", "model_update", "model_publication", "model_summary",
    "model_finalization", "model_page_snapshot", "scan", "scan_adoption", "scan_authority", "scan_accumulator", "extract", "controller",
    "controller_boundary", "persist_transaction", "lifecycle", "completion_gate", "final_move_publication",
    "finalization_child", "transfer_stop", "command", "lftp_executor", "lftp_sidecar_validation",
    "lftp_path_pair_annotation", "lftp_status_poll", "lftp_command_boundary", "scoped_model_stream", "retirement_cleanup",
    "path_pair_runtime", "root_progress_status", "completion_gate", "lftp_command_boundary", "ordinary", "refresh", "finish", "transfer", "unknown",
})
_CAPTURE_KINDS = frozenset({"event", "progress", "status", "completion", "unknown"})
_CAPTURE_LEVELS = frozenset({"info", "warning", "error", "critical", "debug", "unknown"})
_CAPTURE_NUMBER_KEYS = frozenset({
    "attempt", "accepted", "age_ms", "bytes", "bytes_done", "bytes_total", "completed", "count", "duration_ms",
    "elapsed_ms", "expected_size", "files", "files_done", "files_total", "generation", "matched", "pending",
    "percent", "processed", "progress", "queue_depth", "retries", "scanned", "size", "status_code", "total",
    "transferred", "unknown", "unresolved", "version", "written",
})
_CAPTURE_LIFECYCLE_EVENTS = frozenset({
    "queue_admitted", "executor_start", "executor_return", "executor_error", "lftp_child", "status_membership",
    "root_default", "queue_retired", "controller_force_close", "operation_scheduled", "operation_retired", "unknown",
})
_CAPTURE_LIFECYCLE_PHASES = frozenset({
    "admission", "executor", "lftp", "status", "publication", "retirement", "teardown", "precheck", "drain", "send",
    "prompt", "connecting", "error_recovery", "unknown",
})
_CAPTURE_LIFECYCLE_OUTCOMES = frozenset({
    "accepted", "entered", "returned", "success", "observed", "present", "absent", "ambiguous", "published", "retired",
    "scheduled", "error", "unknown",
})
_CAPTURE_LIFECYCLE_ERRORS = frozenset({
    "none", "eof", "exit", "signal", "timeout", "command_error", "parser_error", "terminal_backlog", "unhealthy_snapshot", "unknown",
})
_CAPTURE_LIFECYCLE_BOUNDARIES = frozenset({
    "queue_admission", "executor", "retirement", "lftp_command", "status_membership", "root_publication", "queue_retirement", "teardown", "unknown",
})
_CAPTURE_LIFECYCLE_ENUMS = {
    "status_health": {"healthy", "unhealthy", "unknown"}, "membership": {"present", "absent", "ambiguous", "unknown"},
    "status_parse": {"accepted_empty", "unknown"}, "status_result_shape": {"empty_or_prompt", "queue_done", "job_present", "ambiguous", "parse_error", "unknown"},
    "status_recovery": {"none", "connection_grace", "unknown"}, "status_state": {"queued", "running", "none", "unknown"},
    "membership_reason": {"filtered", "none", "unknown"}, "root_state": {"default", "unknown"}, "coverage": {"incomplete", "unknown"},
    "publication_outcome": {"default_incomplete", "unknown"}, "read_buffer_source": {"public_buffer", "private_buffer", "unavailable"},
    "duration_bucket": {"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+", "unknown"},
    "read_buffer_byte_length_bucket": {"0", "1-64", "65-1024", "1025-16384", "16385-65536", "65537+", "unknown"},
    "status_count_bucket": {"0", "1", "2+", "unknown"}, "command_kind": {"queue", "status", "unknown"},
    "command_outcome": {"success", "prompt_timeout", "eof", "error", "unknown"}, "reaped": {"signal", "exit", "alive", "not_alive", "unknown"},
    "future_state": {"finalized", "pending", "unknown"},
}
_CAPTURE_LIFECYCLE_BOOLS = {
    "fresh", "healthy", "process_alive_before", "process_alive_after", "process_alive", "pre_send_drain_queue_done",
    "pre_send_drain_job_or_progress", "pre_send_drain_prompt_or_echo",
}
_CAPTURE_READINESS_SCHEMAS = {"queue_readiness.v1", "queue_readiness.v2"}
_CAPTURE_READINESS_EVENTS = {"queue_callback", "queue_http_wait"}
_CAPTURE_READINESS_PHASES = {"entry", "wait_return", "unknown"}
_CAPTURE_READINESS_OUTCOMES = {"accepted", "success", "failure", "failed", "timeout", "unknown"}
_CAPTURE_READINESS_ORIGINS = {"auto_queue", "manual", "unknown"}
_CAPTURE_READINESS_REASONS = {"none", "awaiting_scan_publication", "initial_scan_authority_deadline", "comparison_pending", "scoped_rescan_token_unknown", "scoped_rescan_request_failed", "scoped_rescan_requested", "unknown"}
_CAPTURE_READINESS_BOOLEANS = {"ready"}
_CAPTURE_MODEL_PAGE_SNAPSHOT_OUTCOMES = {"success", "error", "unknown"}
_CAPTURE_MODEL_PAGE_SNAPSHOT_ERRORS = {"cancelled", "model_error", "runtime_error", "unknown"}
_CAPTURE_PROGRESS_LINEAGE_SCHEMA = "model_progress_lineage_summary.v1"
_CAPTURE_PROGRESS_LINEAGE_MAX_VERSION_RANGES = 8
_CAPTURE_PROGRESS_LINEAGE_SOURCES = {
    "fresh_healthy", "fresh_unhealthy", "cached_retry", "cached_idle", "retry_empty",
    "cached_inflight", "inflight_empty", "cached_unhealthy", "unhealthy_empty",
    "cached_error", "error_empty",
}
_CAPTURE_PROGRESS_LINEAGE_DECISIONS = {"full_build", "active_delta", "cached"}
_CAPTURE_PROGRESS_LINEAGE_BUILD_KINDS = {"candidate", "full", "none"}
_CAPTURE_PROGRESS_LINEAGE_STATUS_COUNT_BUCKETS = {"0", "1", "2-4", "5+"}
_CAPTURE_PROGRESS_LINEAGE_DURATION_BUCKETS = {"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+"}
_CAPTURE_PROGRESS_LINEAGE_MISSING_PHASES = {"none", "status_consume", "model_mutation", "updater_decision", "multiple"}
_CAPTURE_GATE_DECISIONS = {"blocked", "no_retirement", "excluded", "pending", "attempt_eligible", "deferred", "unknown"}
_CAPTURE_GATE_REASONS = {
    "completion_detection_not_authoritative", "still_active", "explicit_stop", "lftp_job_finished", "candidate_pair_unselected",
    "no_model_diff", "candidate_authorized", "adopted", "retry_or_failure_limit", "completion_evidence_missing", "completion_authority_missing", "complete_local_coverage", "unknown",
}
_CAPTURE_GATE_OUTCOMES = {"stopped", "deferred", "downloaded", "unknown"}
_CAPTURE_COMMAND_KINDS = {"get", "mirror", "pget"}
_CAPTURE_COMMAND_PHASES = {"submitted", "prompt_ready", "prompt_timeout", "process_eof", "backend_error"}
_CAPTURE_COMMAND_OUTPUTS = {"empty", "connecting", "backend_error", "other"}
_CAPTURE_COMMAND_COUNT_BUCKETS = {"0", "1", "2-4", "5-16", "17-64", "65-256", "257+", "unknown"}
_CAPTURE_COMMAND_BYTE_BUCKETS = {"0", "1-127", "128-511", "512-2047", "2048-8191", "8192-32767", "32768+", "unknown"}


def _sanitize_capture_projection(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {"kind": "unknown"}
    result: dict[str, object] = {}
    for key, allowed in (
        ("kind", _CAPTURE_KINDS), ("source", _CAPTURE_SOURCES), ("category", _CAPTURE_CATEGORIES),
        ("event_type", _CAPTURE_EVENTS), ("stage", _CAPTURE_STAGES), ("level", _CAPTURE_LEVELS),
    ):
        item = value.get(key)
        result[key] = item if isinstance(item, str) and item in allowed else "unknown"
    numbers = value.get("numbers")
    if isinstance(numbers, Mapping):
        bounded: dict[str, int | float] = {}
        for key in _CAPTURE_NUMBER_KEYS:
            item = numbers.get(key)
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                continue
            if isinstance(item, float) and (not math.isfinite(item) or abs(item) > 2 ** 63 - 1):
                continue
            if abs(item) <= 2 ** 63 - 1:
                bounded[key] = item
        if bounded:
            result["numbers"] = bounded
    for key in ("flow_hash", "corr_hash", "file_hash", "pair_hash"):
        item = value.get(key)
        if isinstance(item, str) and len(item) == 16 and all(char in "0123456789abcdef" for char in item):
            result[key] = item
    lifecycle = value.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        safe: dict[str, object] = {"schema": "queue.lifecycle.v1"} if lifecycle.get("schema") == "queue.lifecycle.v1" else {}
        for key, allowed in (("event", _CAPTURE_LIFECYCLE_EVENTS), ("phase", _CAPTURE_LIFECYCLE_PHASES), ("boundary", _CAPTURE_LIFECYCLE_BOUNDARIES), ("outcome", _CAPTURE_LIFECYCLE_OUTCOMES), ("error_class", _CAPTURE_LIFECYCLE_ERRORS)):
            item = lifecycle.get(key)
            safe[key] = item if isinstance(item, str) and item in allowed else "unknown"
        for key, allowed in _CAPTURE_LIFECYCLE_ENUMS.items():
            item = lifecycle.get(key)
            if isinstance(item, str) and item in allowed:
                safe[key] = item
        for key in _CAPTURE_LIFECYCLE_BOOLS:
            item = lifecycle.get(key)
            if type(item) is bool:
                safe[key] = item
        result["lifecycle"] = safe
    gate = value.get("completion_gate")
    if isinstance(gate, Mapping):
        safe_gate: dict[str, object] = {"boundary": "completion_gate"}
        for key, allowed in (("decision", _CAPTURE_GATE_DECISIONS), ("reason", _CAPTURE_GATE_REASONS), ("terminal_outcome", _CAPTURE_GATE_OUTCOMES)):
            item = gate.get(key)
            if isinstance(item, str) and item in allowed:
                safe_gate[key] = item
        if isinstance(gate.get("pending_transition"), str) and gate["pending_transition"] in {"registered", "cleared", "retained"}:
            safe_gate["pending_transition"] = gate["pending_transition"]
        for key in {"poll_eligible", "poll_fresh", "poll_healthy", "previous_active", "current_active", "marker_observed", "local_scan_forced", "explicit_stop", "build_ran", "candidate_present", "live_present", "local_size_present", "remote_size_present", "complete_local_coverage", "exact_final_file_proof", "model_diff_present", "completion_proved"}:
            if type(gate.get(key)) is bool:
                safe_gate[key] = gate[key]
        result["completion_gate"] = safe_gate
    command = value.get("lftp_command")
    if isinstance(command, Mapping):
        safe_command: dict[str, object] = {"schema": "lftp.command_boundary.v1"} if command.get("schema") == "lftp.command_boundary.v1" else {}
        for key, allowed in (("transfer_kind", _CAPTURE_COMMAND_KINDS), ("phase", _CAPTURE_COMMAND_PHASES), ("output_class", _CAPTURE_COMMAND_OUTPUTS), ("argument_count_bucket", _CAPTURE_COMMAND_COUNT_BUCKETS), ("exclude_count_bucket", _CAPTURE_COMMAND_COUNT_BUCKETS), ("submission_byte_length_bucket", _CAPTURE_COMMAND_BYTE_BUCKETS)):
            item = command.get(key)
            if isinstance(item, str) and item in allowed:
                safe_command[key] = item
        if type(command.get("process_alive")) is bool:
            safe_command["process_alive"] = command["process_alive"]
        result["lftp_command"] = safe_command
    readiness = value.get("queue_readiness")
    if isinstance(readiness, Mapping):
        safe_readiness: dict[str, object] = {}
        schema = readiness.get("schema")
        safe_readiness["schema"] = schema if isinstance(schema, str) and schema in _CAPTURE_READINESS_SCHEMAS else "queue_readiness.v1"
        event = readiness.get("event")
        safe_readiness["event"] = event if isinstance(event, str) and event in _CAPTURE_READINESS_EVENTS else "unknown"
        for key, allowed in (
            ("phase", _CAPTURE_READINESS_PHASES),
            ("outcome", _CAPTURE_READINESS_OUTCOMES),
            ("origin", _CAPTURE_READINESS_ORIGINS),
            ("reason", _CAPTURE_READINESS_REASONS),
        ):
            item = readiness.get(key)
            safe_readiness[key] = item if isinstance(item, str) and item in allowed else "unknown"
        for key in _CAPTURE_READINESS_BOOLEANS:
            if type(readiness.get(key)) is bool:
                safe_readiness[key] = readiness[key]
        for key in ("callback_index", "callback_count", "error_code", "queue_depth"):
            item = readiness.get(key)
            if type(item) is int and 0 <= item <= 2**63 - 1:
                safe_readiness[key] = item
        result["queue_readiness"] = safe_readiness
    model_page_snapshot = value.get("model_page_snapshot")
    if isinstance(model_page_snapshot, Mapping):
        safe_snapshot: dict[str, object] = {}
        outcome = model_page_snapshot.get("outcome")
        safe_snapshot["outcome"] = outcome if isinstance(outcome, str) and outcome in _CAPTURE_MODEL_PAGE_SNAPSHOT_OUTCOMES else "unknown"
        if safe_snapshot["outcome"] == "error":
            error_class = model_page_snapshot.get("error_class")
            safe_snapshot["error_class"] = error_class if isinstance(error_class, str) and error_class in _CAPTURE_MODEL_PAGE_SNAPSHOT_ERRORS else "unknown"
        for key in ("scope_version", "global_version", "lock_wait_ms", "lock_hold_ms", "snapshot_elapsed_ms"):
            item = model_page_snapshot.get(key)
            if type(item) is int and 0 <= item <= 2**31 - 1:
                safe_snapshot[key] = item
        result["model_page_snapshot"] = safe_snapshot
    lineage = value.get("model_progress_lineage")
    if isinstance(lineage, Mapping):
        safe_lineage = _sanitize_progress_lineage(lineage)
        if safe_lineage is not None:
            result["model_progress_lineage"] = safe_lineage
    return result


def _sanitize_progress_lineage(value: Mapping[str, object]) -> Mapping[str, object] | None:
    if value.get("schema") != _CAPTURE_PROGRESS_LINEAGE_SCHEMA:
        return None
    missing_phase = value.get("missing_phase")
    if not isinstance(missing_phase, str) or missing_phase not in _CAPTURE_PROGRESS_LINEAGE_MISSING_PHASES:
        return None
    result: dict[str, object] = {
        "schema": _CAPTURE_PROGRESS_LINEAGE_SCHEMA,
        "missing_phase": missing_phase,
    }
    status = value.get("status_consume")
    if status is not None:
        if not isinstance(status, Mapping):
            return None
        source = status.get("source")
        monotonic_ms = status.get("monotonic_ms")
        if not isinstance(source, str) or source not in _CAPTURE_PROGRESS_LINEAGE_SOURCES:
            return None
        if type(status.get("fresh")) is not bool or type(status.get("healthy")) is not bool:
            return None
        if type(monotonic_ms) is not int or not 0 <= monotonic_ms <= 2**63 - 1:
            return None
        result["status_consume"] = {
            "source": source,
            "fresh": status["fresh"],
            "healthy": status["healthy"],
            "monotonic_ms": monotonic_ms,
        }
    mutation = value.get("model_mutation")
    if mutation is not None:
        if not isinstance(mutation, Mapping):
            return None
        safe_mutation: dict[str, object] = {}
        for key in ("model_version_first", "model_version_last", "scope_version", "monotonic_ms"):
            item = mutation.get(key)
            if type(item) is not int or not 0 <= item <= 2**63 - 1:
                return None
            safe_mutation[key] = item
        ranges = mutation.get("mutation_version_ranges")
        if ranges is not None:
            if not isinstance(ranges, list) or len(ranges) > _CAPTURE_PROGRESS_LINEAGE_MAX_VERSION_RANGES:
                return None
            safe_ranges: list[list[int]] = []
            for version_range in ranges:
                if not isinstance(version_range, list) or len(version_range) != 2:
                    return None
                start, end = version_range
                if type(start) is not int or type(end) is not int or not 0 <= start <= end <= 2**63 - 1:
                    return None
                safe_ranges.append([start, end])
            safe_mutation["mutation_version_ranges"] = safe_ranges
        truncated = mutation.get("mutation_ranges_truncated")
        if truncated is not None:
            if type(truncated) is not bool:
                return None
            safe_mutation["mutation_ranges_truncated"] = truncated
        omitted_count = mutation.get("mutation_ranges_omitted_count")
        if omitted_count is not None:
            if type(omitted_count) is not int or not 0 <= omitted_count <= 2_147_483_647:
                return None
            safe_mutation["mutation_ranges_omitted_count"] = omitted_count
        result["model_mutation"] = safe_mutation
    decision = value.get("updater_decision")
    if decision is not None:
        if not isinstance(decision, Mapping):
            return None
        safe_decision: dict[str, object] = {}
        for key, allowed in (
                ("decision", _CAPTURE_PROGRESS_LINEAGE_DECISIONS),
                ("build_kind", _CAPTURE_PROGRESS_LINEAGE_BUILD_KINDS),
                ("status_count_bucket", _CAPTURE_PROGRESS_LINEAGE_STATUS_COUNT_BUCKETS),
                ("updater_cycle_duration_bucket", _CAPTURE_PROGRESS_LINEAGE_DURATION_BUCKETS),
        ):
            item = decision.get(key)
            if item is not None:
                if not isinstance(item, str) or item not in allowed:
                    return None
                safe_decision[key] = item
        monotonic_ms = decision.get("monotonic_ms")
        if type(monotonic_ms) is not int or not 0 <= monotonic_ms <= 2**63 - 1:
            return None
        safe_decision["monotonic_ms"] = monotonic_ms
        result["updater_decision"] = safe_decision
    return result


def _sanitize_capture(value: object) -> Mapping[str, object]:
    """Keep bounded, typed, correlation-safe incremental spool receipts only."""
    if not isinstance(value, Mapping):
        return {"state": "invalid"}
    result: dict[str, object] = {}
    allowed = {
        "state": {"armed", "record", "loss", "stopped", "unsupported"},
        "source": _CAPTURE_SOURCES,
        "event_type": _CAPTURE_EVENTS,
        "category": _CAPTURE_CATEGORIES,
        "stage": _CAPTURE_STAGES,
        "level": _CAPTURE_LEVELS,
        "reason": {"session", "marker", "cursor", "unseen_rotation", "budget", "partial", "unsupported", "output", "health", "malformed", "oversized", "unread", "missing", "prune", "unknown"},
    }
    for key, values in allowed.items():
        item = value.get(key)
        if item in values:
            result[key] = item
    for key in ("utc_ms", "created_ms", "created_ns", "monotonic_ms", "cursor_start", "cursor_end", "bytes_read", "records", "lost", "rotation_count", "health_lost", "health_unknown", "health_unresolved", "irrelevant", "unsupported", "discarded"):
        item = value.get(key)
        if type(item) is int and 0 <= item <= 2 ** 63 - 1:
            result[key] = item
    for key in ("flow_hash", "corr_hash", "file_hash", "pair_hash", "session_hash"):
        item = value.get(key)
        if isinstance(item, str) and len(item) == 16 and all(char in "0123456789abcdef" for char in item):
            result[key] = item
    if "projection" in value:
        result["projection"] = _sanitize_capture_projection(value.get("projection"))
    budget = value.get("budget")
    if isinstance(budget, Mapping):
        bounded_budget: dict[str, int] = {}
        for key in ("max_bytes", "max_millis", "max_records", "max_output_bytes", "bytes_read", "elapsed_ms"):
            item = budget.get(key)
            if type(item) is int and 0 <= item <= 2 ** 63 - 1:
                bounded_budget[key] = item
        if bounded_budget:
            result["budget"] = bounded_budget
    return result or {"state": "invalid"}


def _sanitize_scan_lifecycle(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {"schema": "incoming-recovery-scan-lifecycle.v1"}
    sanitized: dict[str, object] = {"schema": "incoming-recovery-scan-lifecycle.v1"}
    for key in (
        "publication_id", "model_version", "local_scan_generation", "remote_scan_generation",
        "scanned_pair_count", "completed_pair_count", "unknown_pair_count",
    ):
        item = value.get(key)
        sanitized[key] = item if type(item) is int and 0 <= item <= 2_147_483_647 else None
    for key in ("final", "joint_final", "joint_authoritative", "joint_authoritative_after"):
        item = value.get(key)
        sanitized[key] = item if type(item) is bool else None
    outcome = value.get("outcome")
    reason = value.get("reason")
    sanitized["outcome"] = outcome if isinstance(outcome, str) and outcome in _SCAN_AUTHORITY_OUTCOMES else "unknown"
    sanitized["reason"] = reason if isinstance(reason, str) and reason in _SCAN_AUTHORITY_REASONS else "unknown"
    return sanitized


def _sanitize_artifact(value: object) -> Mapping[str, object]:
    """Allow-list artifact fields so injected sinks cannot retain HTTP/private data."""
    if not isinstance(value, Mapping):
        return {"schema": "incoming-recovery-observer-artifact.v1", "outcome": "invalid"}

    sanitized: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key in _ARTIFACT_PRIVATE_KEYS or key not in _ARTIFACT_ALLOWED_KEYS:
            continue
        if key in {"evidence", "queue_evidence", "failure", "capture_failure"}:
            nested = _sanitize_artifact(item)
            sanitized[key] = nested
        elif key == "error":
            nested = _sanitize_artifact(item)
            sanitized[key] = nested
        elif key == "scan_lifecycle":
            sanitized[key] = _sanitize_scan_lifecycle(item)
        elif key == "root_state":
            sanitized[key] = item if isinstance(item, str) and item in _ARTIFACT_ROOT_STATES else "unknown"
        elif key == "body_reason":
            sanitized[key] = _safe_body_reason(item)
        elif key == "error_type":
            sanitized[key] = item if isinstance(item, str) and item in _ARTIFACT_ERROR_TYPES else "observer_error"
        elif key == "status_code":
            sanitized[key] = item if type(item) is int and 100 <= item <= 599 else None
        elif key == "request_latency_ms":
            sanitized[key] = (
                round(float(item), 3)
                if type(item) in (int, float) and math.isfinite(float(item))
                and 0 <= float(item) <= _MAX_REQUEST_LATENCY_MS else None
            )
        elif key in {"utc_ms", "monotonic_ms", "invocation_utc_ms", "invocation_monotonic_ms", "response_utc_ms", "response_monotonic_ms", "timeout_utc_ms", "timeout_monotonic_ms", "capture_start_utc_ms", "capture_start_monotonic_ms", "capture_end_utc_ms", "capture_end_monotonic_ms"}:
            sanitized[key] = item if type(item) is int and 0 <= item <= 2**63 - 1 else None
        elif key in {"timeout_seconds", "timeout"}:
            sanitized[key] = item if type(item) in (int, float) and math.isfinite(float(item)) and 0 <= float(item) <= _MAX_REQUEST_LATENCY_MS / 1000 else None
        elif key in {"config_hash", "runtime_hash", "workload_hash"}:
            sanitized[key] = item if isinstance(item, str) and len(item) == 16 and all(char in "0123456789abcdef" for char in item) else None
        elif key in {"selector_hash", "leaf_hash"}:
            sanitized[key] = item if isinstance(item, str) and len(item) == 16 and all(char in "0123456789abcdef" for char in item) else None
        elif key == "model_version":
            sanitized[key] = item if type(item) is int and 0 <= item <= 2**31 - 1 else None
        elif key == "runtime_version":
            sanitized[key] = item if isinstance(item, str) and len(item) <= 32 and all(char.isdigit() or char == "." for char in item) else "unknown"
        elif key in {"completion_proven", "timed_out"}:
            sanitized[key] = item if type(item) is bool else False
        elif key in {"response_outcome", "physical_outcome", "model_outcome", "reader_outcome"}:
            sanitized[key] = item if isinstance(item, str) and len(item) <= 64 and item.replace("_", "").isalnum() else "unknown"
        elif key == "endpoint_class":
            sanitized[key] = item if isinstance(item, str) and item in _ENDPOINT_CLASSES else "unknown"
        elif key in {"classification", "reason"}:
            allowed = _DISCRIMINATOR_LABELS if key == "classification" else _DISCRIMINATOR_REASONS
            sanitized[key] = item if isinstance(item, str) and item in allowed else "inconclusive"
        elif key == "request":
            sanitized[key] = _sanitize_discriminator_request(item)
        elif key == "diagnostics":
            sanitized[key] = _sanitize_discriminator_diagnostics(item)
        elif key == "capture":
            sanitized[key] = _sanitize_capture(item)
        elif key == "model_snapshot":
            if isinstance(item, Mapping):
                safe_snapshot: dict[str, object] = {
                    "schema": "incoming-recovery-model-snapshot.v1", "kind": "leaf",
                }
                leaf_hash = item.get("leaf_hash")
                safe_snapshot["leaf_hash"] = (
                    leaf_hash
                    if isinstance(leaf_hash, str) and len(leaf_hash) == 16
                    and all(char in "0123456789abcdef" for char in leaf_hash)
                    else "unknown"
                )
                state = item.get("state")
                safe_snapshot["state"] = state if isinstance(state, str) and state in _ARTIFACT_ROOT_STATES else "unknown"
                for field in (
                    "size", "local_size", "remote_size", "transferred_size",
                    "display_size_total", "display_transferred_size", "downloading_speed", "eta",
                ):
                    number = item.get(field)
                    if type(number) is int and 0 <= number <= 2**63 - 1:
                        safe_snapshot[field] = number
                download_progress = item.get("download_progress")
                if type(download_progress) is int and 0 <= download_progress <= 100:
                    safe_snapshot["download_progress"] = download_progress
                model_version = item.get("model_version")
                if type(model_version) is int and 0 <= model_version <= 2**31 - 1:
                    safe_snapshot["model_version"] = model_version
                for field in ("remote_present", "local_present", "complete_local_coverage", "final_move_succeeded", "explicitly_stopped"):
                    boolean = item.get(field)
                    if type(boolean) is bool:
                        safe_snapshot[field] = boolean
                sanitized[key] = safe_snapshot
            else:
                sanitized[key] = {
                    "schema": "incoming-recovery-model-snapshot.v1", "kind": "leaf",
                    "leaf_hash": "unknown", "state": "unknown",
                }
        elif key in {"request_index", "sample_index"}:
            sanitized[key] = item if type(item) is int and item >= 0 else None
        elif key in {"root_found", "target_verified", "root_verified", "first_failure", "success"}:
            sanitized[key] = item if type(item) is bool else False
        elif key in {"schema", "event", "phase", "outcome", "kind", "response_kind"}:
            if key == "response_kind":
                sanitized[key] = item if isinstance(item, str) and item in _ARTIFACT_RESPONSE_KINDS else "object"
            else:
                sanitized[key] = item if isinstance(item, str) else "unknown"
    return sanitized


class JsonArtifactSink:
    """Append newline-delimited JSON artifacts and flush each one durably."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def persist(self, artifact: Mapping[str, object]) -> None:
        record = _sanitize_artifact(artifact)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    append = persist

    def __call__(self, artifact: Mapping[str, object]) -> None:
        self.persist(artifact)


def _persist_artifact(sink: object | None, artifact: Mapping[str, object]) -> None:
    if sink is None:
        return
    record = _sanitize_artifact(artifact)
    if hasattr(sink, "persist"):
        sink.persist(record)  # type: ignore[attr-defined]
    elif hasattr(sink, "append"):
        sink.append(record)  # type: ignore[attr-defined]
    elif callable(sink):
        sink(record)
    else:
        raise TypeError("artifact sink must be callable or expose persist/append")


def _response_kind(response: object) -> str:
    """Classify a response using fixed categories rather than its class name."""
    if response is None:
        return "none"
    if isinstance(response, Mapping):
        return "mapping"
    if isinstance(response, (bytes, bytearray)):
        return "bytes"
    if isinstance(response, str):
        return "text"
    if isinstance(response, (int, float)) and not isinstance(response, bool):
        return "number"
    return "object"


class FixedGetSampler:
    """Run a fixed GET list synchronously, persisting each boundary in order.

    Request timing starts after the ``before`` artifact is persisted and ends
    immediately when the injected GET returns or raises. This keeps artifact
    sink latency out of the request measurement.
    """

    def __init__(
        self,
        paths: Sequence[str],
        artifact_sink: object | None = None,
        *,
        continue_on_error: bool = True,
        clock: Callable[[], int] | None = None,
        artifact_path: str | os.PathLike[str] | None = None,
    ):
        if isinstance(paths, (str, bytes)) or not all(isinstance(path, str) and path for path in paths):
            raise ValueError("sampler paths must be non-empty strings")
        if artifact_sink is not None and artifact_path is not None:
            raise ValueError("provide artifact_sink or artifact_path, not both")
        self.paths = tuple(paths)
        if artifact_path is not None:
            artifact_sink = JsonArtifactSink(artifact_path)
        self.artifact_sink = (
            JsonArtifactSink(artifact_sink)
            if isinstance(artifact_sink, (str, os.PathLike)) else artifact_sink
        )
        self.continue_on_error = continue_on_error
        self.clock = clock or time.monotonic_ns
        self.first_failure: Mapping[str, object] | None = None
        self.capture_failure: Mapping[str, object] | None = None
        self.last_records: list[Mapping[str, object]] = []

    def _persist(self, artifact: Mapping[str, object], phase: str) -> bool:
        try:
            _persist_artifact(self.artifact_sink, artifact)
        except Exception:
            self.capture_failure = {
                "schema": "incoming-recovery-observer-capture-failure.v1",
                "phase": phase, "kind": "artifact_sink_failure",
            }
            return False
        return True

    def sample(
        self,
        get: Callable[[str], object],
        *,
        before_request: Callable[[], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> list[Mapping[str, object]]:
        records: list[Mapping[str, object]] = []
        self.last_records = records
        for index, path in enumerate(self.paths):
            if stop_event is not None and stop_event.is_set():
                break
            safe_endpoint = endpoint_class(path)
            # The next GET cannot begin until this record has been persisted.
            before_persisted = self._persist({
                "schema": "incoming-recovery-observer-sample.v1", "event": "get",
                "phase": "before", "request_index": index,
                "endpoint_class": safe_endpoint,
            }, "get_before")
            if not before_persisted:
                raise ObserverCaptureError("GET sampler artifact capture failed before request")
            if before_request is not None:
                before_request()
            started_ns = self.clock()
            try:
                response = get(path)
            except Exception as exc:
                ended_ns = self.clock()
                failure = {
                    "schema": "incoming-recovery-observer-sample.v1", "event": "get",
                    "phase": "after", "outcome": "failure", "request_index": index,
                    "endpoint_class": safe_endpoint,
                    "request_latency_ms": _request_latency_ms(started_ns, ended_ns),
                    "error": {**error_artifact(exc), "kind": "get_error"},
                }
                if self.first_failure is None:
                    failure = {**failure, "first_failure": True}
                    self.first_failure = failure
                persisted = self._persist(failure, "get_failure")
                if not persisted:
                    failure = {
                        **failure, "outcome": "capture_failure", "capture_failure": self.capture_failure,
                    }
                public_failure = _sanitize_artifact(failure)
                if not persisted:
                    # Keep the historical return shape when the sink itself
                    # fails; the richer timing remains in ``last_records``.
                    public_failure = {
                        key: value for key, value in public_failure.items()
                        if key not in {"endpoint_class", "request_latency_ms"}
                    }
                records.append(public_failure)
                if not persisted:
                    self.last_records = records
                    return records
                if not self.continue_on_error:
                    self.last_records = records
                    raise
                continue
            ended_ns = self.clock()
            status = _status_code(response)
            if status is not None and status >= 400:
                error_type = "auth_error" if status in (401, 403) else "http_error"
                failure = {
                    "schema": "incoming-recovery-observer-sample.v1", "event": "get",
                    "phase": "after", "outcome": "failure", "request_index": index,
                    "endpoint_class": safe_endpoint,
                    "request_latency_ms": _request_latency_ms(started_ns, ended_ns),
                    "error": {
                        "schema": "incoming-recovery-observer-error.v1",
                        "kind": "get_error", "error_type": error_type,
                        "status_code": status,
                    },
                }
                if self.first_failure is None:
                    failure = {**failure, "first_failure": True}
                    self.first_failure = failure
                persisted = self._persist(failure, "get_failure")
                if not persisted:
                    failure = {
                        **failure, "outcome": "capture_failure", "capture_failure": self.capture_failure,
                    }
                public_failure = _sanitize_artifact(failure)
                if not persisted:
                    public_failure = {
                        key: value for key, value in public_failure.items()
                        if key not in {"endpoint_class", "request_latency_ms"}
                    }
                records.append(public_failure)
                if not persisted:
                    self.last_records = records
                    return records
                if not self.continue_on_error:
                    self.last_records = records
                    raise ObserverSchemaError("GET returned an HTTP error")
                continue
            success = {
                "schema": "incoming-recovery-observer-sample.v1", "event": "get",
                "phase": "after", "outcome": "success", "request_index": index,
                "endpoint_class": safe_endpoint,
                "request_latency_ms": _request_latency_ms(started_ns, ended_ns),
                "response_kind": _response_kind(response),
            }
            persisted = self._persist(success, "get_success")
            if not persisted:
                # The response was obtained, but its durable boundary was not.
                # Return that distinction and stop before starting another GET.
                success = {
                    **success, "outcome": "capture_failure", "capture_failure": self.capture_failure,
                }
            records.append(_sanitize_artifact(success))
            if not persisted:
                self.last_records = records
                return records
        self.last_records = records
        return records

    collect = sample

    def discriminate(self, diagnostics: object | None = None) -> Mapping[str, object]:
        """Classify the most recent fixed probe using sanitized diagnostics."""
        return discriminate_api_responsiveness(self.last_records, diagnostics)


_DISCRIMINATOR_LABELS = frozenset({
    "responsive", "likely_gil_or_cpu", "lock_wait_or_hold",
    "scan_traversal_or_intake", "model_serialization_or_sse",
    "observer_interference", "inconclusive",
})
_FAILURE_REASONS = frozenset({
    "auth_error", "timeout", "http_error", "transport_error",
    "observer_error", "mixed_request_failure", "malformed_request_evidence",
    "malformed_diagnostics", "diagnostics_unavailable", "slow_without_signal",
    "capture_failure",
})
_DISCRIMINATOR_REASONS = _FAILURE_REASONS | frozenset({
    "request_latency_within_bound", "serialization_stage_or_counter",
    "scan_stage_or_duration", "high_cpu_with_slow_request",
    "long_active_stage_with_low_cpu",
})
_DIAGNOSTIC_SCHEMA = "seedsync.performance-diagnostics.v1"
_DISCRIMINATOR_SCHEMA = "incoming-recovery-api-discriminator.v1"


def _finite_nonnegative(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _percentile(values: Sequence[float], rank: float = 0.95) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * rank + 0.999999) - 1))
    return round(ordered[index], 3)


def _empty_diagnostics(available: bool = False) -> dict[str, object]:
    return {
        "available": available,
        "sample_count": 0,
        "cpu_average_percent_one_core": None,
        "cpu_peak_percent_one_core": None,
        "active_stage": None,
        "active_stage_wall_seconds": None,
        "active_scanner_stage": None,
        "active_scanner_stage_wall_seconds": None,
        "duration_stages": [],
        "duration_wall_seconds": {},
        "duration_cpu_seconds": {},
        "serialization_counter_total": 0,
    }


def _sanitize_discriminator_request(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, object] = {}
    for key in (
        "sample_count", "success_count", "failure_count", "auth_error_count",
        "timeout_count",
    ):
        item = value.get(key)
        result[key] = item if type(item) is int and item >= 0 else 0
    for key in ("max_latency_ms", "p95_latency_ms"):
        item = value.get(key)
        result[key] = (
            round(float(item), 3)
            if item is not None and type(item) in (int, float)
            and math.isfinite(float(item)) and 0 <= float(item) <= _MAX_REQUEST_LATENCY_MS
            else None
        )
    reason = value.get("failure_reason")
    result["failure_reason"] = reason if isinstance(reason, str) and reason in _FAILURE_REASONS else None
    classes = value.get("endpoint_classes")
    result["endpoint_classes"] = sorted({
        item for item in classes if isinstance(item, str) and item in _ENDPOINT_CLASSES
    }) if isinstance(classes, list) else []
    return result


def _sanitize_discriminator_diagnostics(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return _empty_diagnostics()
    result = _empty_diagnostics(bool(value.get("available")))
    result["sample_count"] = value.get("sample_count") if type(value.get("sample_count")) is int \
        and value.get("sample_count") >= 0 else 0
    for key in ("cpu_average_percent_one_core", "cpu_peak_percent_one_core"):
        item = value.get(key)
        result[key] = round(float(item), 3) if type(item) in (int, float) \
            and math.isfinite(float(item)) and float(item) >= 0 else None
    for key in ("active_stage", "active_scanner_stage"):
        result[key] = _safe_stage(value.get(key))
        wall = value.get(f"{key}_wall_seconds")
        result[f"{key}_wall_seconds"] = round(float(wall), 6) if type(wall) in (int, float) \
            and math.isfinite(float(wall)) and float(wall) >= 0 else None
    stages = value.get("duration_stages")
    result["duration_stages"] = sorted({
        item for item in stages if isinstance(item, str) and item in _KNOWN_DIAGNOSTIC_STAGES
    }) if isinstance(stages, list) else []
    for source_key, target_key in (("duration_wall_seconds", "duration_wall_seconds"),
                                   ("duration_cpu_seconds", "duration_cpu_seconds")):
        source = value.get(source_key)
        result[target_key] = {
            metric: round(float(item), 6)
            for metric, item in source.items()
            if isinstance(metric, str) and metric in _KNOWN_DIAGNOSTIC_STAGES
            and type(item) in (int, float) and math.isfinite(float(item)) and float(item) >= 0
        } if isinstance(source, Mapping) else {}
    counter = value.get("serialization_counter_total")
    result["serialization_counter_total"] = counter if type(counter) is int and counter >= 0 else 0
    return result


def _safe_stage(value: object) -> str | None:
    return value if isinstance(value, str) and value in _KNOWN_DIAGNOSTIC_STAGES else None


def _diagnostic_stage_value(value: object, field: str) -> tuple[str | None, float | None, bool]:
    if not isinstance(value, Mapping):
        return None, None, False
    name = value.get("name")
    if name is not None and not isinstance(name, str):
        return None, None, False
    wall = value.get(field)
    if wall is None:
        return _safe_stage(name), None, True
    number = _finite_nonnegative(wall)
    return _safe_stage(name), number, number is not None


def _sanitize_diagnostics(diagnostics: object | None) -> tuple[dict[str, object], bool, str | None]:
    """Reduce the known diagnostics snapshot to a fixed, public evidence shape."""
    if diagnostics is None:
        return _empty_diagnostics(False), True, "diagnostics_unavailable"
    if not isinstance(diagnostics, Mapping):
        return _empty_diagnostics(), False, "malformed_diagnostics"
    if diagnostics.get("schema") != _DIAGNOSTIC_SCHEMA:
        return _empty_diagnostics(), False, "malformed_diagnostics"

    result = _empty_diagnostics(True)
    cpu_values: list[float] = []
    samples = diagnostics.get("samples", [])
    if not isinstance(samples, list) or not all(isinstance(sample, Mapping) for sample in samples):
        return _empty_diagnostics(), False, "malformed_diagnostics"
    current = diagnostics.get("current", {})
    if current is not None and not isinstance(current, Mapping):
        return _empty_diagnostics(), False, "malformed_diagnostics"
    # ``current`` is the only CPU evidence used for classification. Retained
    # samples are historical and cannot establish causality for this probe.
    cpu_source = current if isinstance(current, Mapping) else {}
    for field in ("process_cpu_percent_one_core", "cgroup_cpu_percent_one_core"):
        if field not in cpu_source:
            continue
        value = cpu_source[field]
        if value is None:
            continue
        number = _finite_nonnegative(value)
        if number is None:
            return _empty_diagnostics(), False, "malformed_diagnostics"
        cpu_values.append(number)
    if cpu_values:
        result["sample_count"] = len(samples)
        result["cpu_average_percent_one_core"] = round(sum(cpu_values) / len(cpu_values), 3)
        result["cpu_peak_percent_one_core"] = round(max(cpu_values), 3)

    for key, output_key, field in (
        ("active_stage", "active_stage", "wall_seconds"),
        ("active_scanner_stage", "active_scanner_stage", "wall_seconds"),
    ):
        if key not in diagnostics:
            continue
        name, wall, valid = _diagnostic_stage_value(diagnostics[key], field)
        if not valid:
            return _empty_diagnostics(), False, "malformed_diagnostics"
        result[output_key] = name
        result[f"{output_key}_wall_seconds"] = wall

    duration_wall: dict[str, float] = {}
    duration_cpu: dict[str, float] = {}

    def add_duration(metric: object, values: object, *, active: bool = False) -> bool:
        if not isinstance(metric, str) or metric not in _KNOWN_DIAGNOSTIC_STAGES:
            return True
        if not isinstance(values, Mapping):
            return False
        wall_key = "max_wall_seconds" if active else "total_wall_seconds"
        wall_value = values.get(wall_key)
        cpu_value = values.get("total_cpu_seconds")
        if wall_value is not None:
            wall = _finite_nonnegative(wall_value)
            if wall is None:
                return False
            duration_wall[metric] = max(duration_wall.get(metric, 0.0), wall)
        if cpu_value is not None:
            cpu = _finite_nonnegative(cpu_value)
            if cpu is None:
                return False
            duration_cpu[metric] = max(duration_cpu.get(metric, 0.0), cpu)
        return True

    for field, active in (("durations", False), ("active_stages", True)):
        values = diagnostics.get(field, {})
        if values is not None and not isinstance(values, Mapping):
            return _empty_diagnostics(), False, "malformed_diagnostics"
        for metric, item in values.items() if isinstance(values, Mapping) else ():
            if not add_duration(metric, item, active=active):
                return _empty_diagnostics(), False, "malformed_diagnostics"

    for sample in samples:
        window = sample.get("stage_window")
        if window is None:
            continue
        if not isinstance(window, Mapping):
            return _empty_diagnostics(), False, "malformed_diagnostics"
        metrics = window.get("metrics", {})
        if metrics is not None and not isinstance(metrics, Mapping):
            return _empty_diagnostics(), False, "malformed_diagnostics"
        for metric, item in metrics.items() if isinstance(metrics, Mapping) else ():
            if not add_duration(metric, item):
                return _empty_diagnostics(), False, "malformed_diagnostics"

    counters = diagnostics.get("counters", {})
    if counters is not None and not isinstance(counters, Mapping):
        return _empty_diagnostics(), False, "malformed_diagnostics"
    serialization_total = 0
    for metric in _SERIALIZATION_STAGES:
        if not isinstance(counters, Mapping) or metric not in counters:
            continue
        value = counters[metric]
        if type(value) is not int or value < 0:
            return _empty_diagnostics(), False, "malformed_diagnostics"
        serialization_total += value

    result["duration_wall_seconds"] = {
        metric: round(duration_wall[metric], 6) for metric in sorted(duration_wall)
    }
    result["duration_cpu_seconds"] = {
        metric: round(duration_cpu[metric], 6) for metric in sorted(duration_cpu)
    }
    result["duration_stages"] = sorted(set(duration_wall) | set(duration_cpu))
    result["serialization_counter_total"] = serialization_total
    return result, True, None


def _request_evidence(records: object) -> tuple[dict[str, object], bool, str | None]:
    if isinstance(records, FixedGetSampler):
        records = records.last_records
    if isinstance(records, Mapping):
        records = records.get("samples")
    if not isinstance(records, list) or not records:
        return {}, False, "malformed_request_evidence"

    after: list[Mapping[str, object]] = []
    phases: dict[int, set[str]] = {}
    endpoints: dict[int, str] = {}
    for record in records:
        if not isinstance(record, Mapping):
            return {}, False, "malformed_request_evidence"
        if record.get("schema") != "incoming-recovery-observer-sample.v1" \
                or record.get("event") != "get":
            return {}, False, "malformed_request_evidence"
        index = record.get("request_index")
        phase = record.get("phase")
        endpoint = record.get("endpoint_class")
        if type(index) is not int or index < 0 or not isinstance(phase, str) \
                or phase not in _ARTIFACT_PHASES or not isinstance(endpoint, str) \
                or endpoint not in _ENDPOINT_CLASSES:
            return {}, False, "malformed_request_evidence"
        index_phases = phases.setdefault(index, set())
        if phase in index_phases or (index in endpoints and endpoints[index] != endpoint):
            return {}, False, "malformed_request_evidence"
        index_phases.add(phase)
        endpoints[index] = endpoint
        if phase != "after":
            continue
        outcome = record.get("outcome")
        if not isinstance(outcome, str) or outcome not in {"success", "failure", "capture_failure"}:
            return {}, False, "malformed_request_evidence"
        latency = record.get("request_latency_ms")
        if latency is None:
            return {}, False, "malformed_request_evidence"
        safe_latency = _finite_nonnegative(latency)
        if safe_latency is None or safe_latency > _MAX_REQUEST_LATENCY_MS:
            return {}, False, "malformed_request_evidence"
        after.append(record)
        if outcome == "success":
            if "error" in record or "capture_failure" in record:
                return {}, False, "malformed_request_evidence"
        elif outcome == "failure":
            error = record.get("error")
            if not isinstance(error, Mapping) or error.get("schema") != "incoming-recovery-observer-error.v1":
                return {}, False, "malformed_request_evidence"
            error_type = error.get("error_type")
            if not isinstance(error_type, str) or error_type not in _ARTIFACT_ERROR_TYPES:
                return {}, False, "malformed_request_evidence"
            status = error.get("status_code")
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                return {}, False, "malformed_request_evidence"
            if "capture_failure" in record:
                return {}, False, "malformed_request_evidence"
        else:
            capture_failure = record.get("capture_failure")
            if not isinstance(capture_failure, Mapping) \
                    or capture_failure.get("schema") != "incoming-recovery-observer-capture-failure.v1" \
                    or capture_failure.get("kind") != "artifact_sink_failure" \
                    or not isinstance(capture_failure.get("phase"), str):
                return {}, False, "malformed_request_evidence"

    if not after or sorted(phases) != list(range(len(phases))):
        return {}, False, "malformed_request_evidence"
    # ``FixedGetSampler.sample`` deliberately returns only post-request
    # records, while its durable artifact contains both boundaries.  Accept
    # either representation, but reject a mixed/partial artifact.
    phase_shapes = set(tuple(sorted(value)) for value in phases.values())
    if phase_shapes not in ({("after",)}, {("after", "before")}):
        return {}, False, "malformed_request_evidence"

    latencies = [round(float(record["request_latency_ms"]), 3) for record in after]
    failures = [record for record in after if record.get("outcome") != "success"]
    failure_types = []
    for record in failures:
        error = record.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("error_type"), str):
            failure_types.append(error["error_type"])
    unique_failures = set(failure_types)
    if len(unique_failures) == 1:
        failure_reason = next(iter(unique_failures))
    elif unique_failures:
        failure_reason = "mixed_request_failure"
    else:
        failure_reason = "capture_failure" if failures else None
    return {
        "sample_count": len(after),
        "success_count": sum(record.get("outcome") == "success" for record in after),
        "failure_count": len(failures),
        "auth_error_count": failure_types.count("auth_error"),
        "timeout_count": failure_types.count("timeout"),
        "failure_reason": failure_reason,
        "max_latency_ms": round(max(latencies), 3),
        "p95_latency_ms": _percentile(latencies),
        "endpoint_classes": sorted({record["endpoint_class"] for record in after}),
    }, True, None


def _inconclusive(reason: str, request: Mapping[str, object] | None = None,
                  diagnostics: Mapping[str, object] | None = None) -> Mapping[str, object]:
    safe_reason = reason if reason in _FAILURE_REASONS else "malformed_request_evidence"
    return {
        "schema": _DISCRIMINATOR_SCHEMA,
        "classification": "inconclusive",
        "reason": safe_reason,
        "request": dict(request or {}),
        "diagnostics": dict(diagnostics or _empty_diagnostics()),
    }


def discriminate_api_responsiveness(
    records: object, diagnostics: object | None = None,
) -> Mapping[str, object]:
    """Classify a fixed authenticated GET probe against sanitized diagnostics.

    This function is deliberately total: malformed or incomplete evidence
    yields ``inconclusive`` and never causes an operational decision from
    untrusted input. Auth failures and timeouts remain separate in ``reason``.
    """
    request, request_valid, request_reason = _request_evidence(records)
    if not request_valid:
        return _inconclusive(request_reason or "malformed_request_evidence")
    safe_diagnostics, diagnostics_valid, diagnostics_reason = _sanitize_diagnostics(diagnostics)
    if not diagnostics_valid:
        return _inconclusive(diagnostics_reason or "malformed_diagnostics", request, safe_diagnostics)

    failure_reason = request.get("failure_reason")
    if failure_reason is not None:
        return _inconclusive(str(failure_reason), request, safe_diagnostics)
    if diagnostics_reason == "diagnostics_unavailable" and request["max_latency_ms"] >= _SLOW_REQUEST_LATENCY_MS:
        return _inconclusive("diagnostics_unavailable", request, safe_diagnostics)

    max_latency = float(request["max_latency_ms"])
    slow = max_latency >= _SLOW_REQUEST_LATENCY_MS
    if not slow:
        return {
            "schema": _DISCRIMINATOR_SCHEMA,
            "classification": "responsive",
            "reason": "request_latency_within_bound",
            "request": dict(request),
            "diagnostics": dict(safe_diagnostics),
        }

    # A failed artifact boundary is a direct signal that observer work altered
    # the probe. It is evaluated before application-side diagnoses.
    if request.get("failure_count", 0) and request.get("failure_reason") == "capture_failure":
        classification = "observer_interference"
        reason = "capture_failure"
    else:
        active_stage = safe_diagnostics.get("active_stage")
        active_scanner_stage = safe_diagnostics.get("active_scanner_stage")
        active_stages = set()
        raw_active_stages = diagnostics.get("active_stages") if isinstance(diagnostics, Mapping) else None
        if isinstance(raw_active_stages, Mapping):
            for name, values in raw_active_stages.items():
                if name not in _KNOWN_DIAGNOSTIC_STAGES or not isinstance(values, Mapping):
                    continue
                count = values.get("count")
                wall = values.get("max_wall_seconds")
                if type(count) is int and count > 0 and type(wall) in (int, float) \
                        and math.isfinite(float(wall)) and float(wall) >= _ACTIVE_STAGE_WALL_SECONDS:
                    active_stages.add(name)
        scan_signal = active_scanner_stage in _SCAN_STAGES or bool(active_stages & _SCAN_STAGES)
        serialization_signal = (
            bool(active_stages & _SERIALIZATION_STAGES)
            or active_stage in _SERIALIZATION_STAGES
        )
        cpu_peak = safe_diagnostics.get("cpu_peak_percent_one_core")
        active_wall = safe_diagnostics.get("active_stage_wall_seconds")
        lock_signal = (
            isinstance(active_stage, str)
            and active_stage not in _SCAN_STAGES
            and active_stage not in _SERIALIZATION_STAGES
            and type(active_wall) in (int, float)
            and float(active_wall) >= _ACTIVE_STAGE_WALL_SECONDS
            and (cpu_peak is None or float(cpu_peak) <= _LOW_CPU_PERCENT)
        )
        lock_signal = lock_signal or bool(active_stages & _LOCK_STAGES)
        if serialization_signal:
            classification, reason = "model_serialization_or_sse", "serialization_stage_or_counter"
        elif scan_signal:
            classification, reason = "scan_traversal_or_intake", "scan_stage_or_duration"
        elif type(cpu_peak) in (int, float) and float(cpu_peak) >= _HIGH_CPU_PERCENT:
            classification, reason = "likely_gil_or_cpu", "high_cpu_with_slow_request"
        elif lock_signal:
            classification, reason = "lock_wait_or_hold", "long_active_stage_with_low_cpu"
        else:
            classification, reason = "inconclusive", "slow_without_signal"

    return {
        "schema": _DISCRIMINATOR_SCHEMA,
        "classification": classification,
        "reason": reason,
        "request": dict(request),
        "diagnostics": dict(safe_diagnostics),
    }


classify_api_responsiveness = discriminate_api_responsiveness


SequentialGetSampler = FixedGetSampler
SynchronousGetSampler = FixedGetSampler


def sample_gets(
    get: Callable[[str], object], paths: Sequence[str], artifact_sink: object | None = None,
) -> list[Mapping[str, object]]:
    return FixedGetSampler(paths, artifact_sink).sample(get)

def queue_path(file_name: str, file_id: str, path_pair_id: str) -> str:
    if not all(isinstance(value, str) and value for value in (file_name, file_id, path_pair_id)):
        raise ObserverSchemaError("queue identity must contain non-empty strings")
    return "/server/command/queue/{}?{}".format(
        quote(file_name, safe=""), urlencode({"file_id": file_id, "path_pair_id": path_pair_id}))


@dataclass
class QueueGate:
    target_pair_id: str
    target_pair_name: str
    target_root_id: str
    target_root_name: str
    expected_pair_local_path: str | None = None
    expected_root_relative_path: str | None = None
    require_pending_transfer: bool = False
    passed: bool = False
    scan_lifecycle: Mapping[str, object] | None = None
    last_queue_evidence: Mapping[str, object] | None = None
    first_failure_evidence: Mapping[str, object] | None = None
    queue_attempted: bool = False
    artifact_sink: object | None = None
    artifact_path: str | os.PathLike[str] | None = None
    attempt_state_path: str | os.PathLike[str] | None = None
    capture_failure: Mapping[str, object] | None = None

    def _consume_durable_attempt(self) -> None:
        if self.attempt_state_path is None:
            return
        path = Path(self.attempt_state_path)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ObserverSchemaError("Queue durable attempt is already consumed") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write('{"schema":"incoming-recovery-queue-attempt.v1","state":"queue_attempt_started"}\n')
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "posix":
                directory = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except Exception as exc:
            # Never remove an exclusive marker after any persistence failure.
            # An incomplete marker is intentionally consumed and makes a
            # restart fail closed rather than risking a second Queue POST.
            raise ObserverCaptureError("Queue durable attempt marker persistence failed") from exc

    def __post_init__(self) -> None:
        if self.artifact_sink is not None and self.artifact_path is not None:
            raise ValueError("provide artifact_sink or artifact_path, not both")
        if self.artifact_path is not None:
            self.artifact_sink = JsonArtifactSink(self.artifact_path)
        elif isinstance(self.artifact_sink, (str, os.PathLike)):
            self.artifact_sink = JsonArtifactSink(self.artifact_sink)

    def _persist(self, artifact: Mapping[str, object], phase: str) -> bool:
        try:
            _persist_artifact(self.artifact_sink, artifact)
        except Exception:
            # Do not let a diagnostic sink replace the application/API failure.
            self.capture_failure = {
                "schema": "incoming-recovery-observer-capture-failure.v1",
                "phase": phase, "kind": "artifact_sink_failure",
            }
            return False
        return True

    def preflight(self, get: Callable[[str], object]) -> Mapping[str, object]:
        """Validate the live API envelopes with GET-only transport; never sends Queue."""
        self.passed = False
        try:
            pairs = path_pairs(get("/server/path-pairs"))
            pair = next((item for item in pairs if item.get("id") == self.target_pair_id), None)
            if pair is None or pair.get("name") != self.target_pair_name or pair.get("auto_queue") is not False:
                raise ObserverSchemaError("target pair is not isolated")
            if self.expected_pair_local_path is not None and pair.get("local_path") != self.expected_pair_local_path:
                raise ObserverSchemaError("target pair local path changed")
            if any(item.get("auto_queue") is True for item in pairs):
                raise ObserverSchemaError("a competing pair has AutoQueue enabled")
            if autoqueue_enabled(get("/server/config/get")):
                raise ObserverSchemaError("global AutoQueue is enabled")
            scan_lifecycle = scan_authority_evidence(get("/server/model/v1/summary"))
            roots = model_roots(get("/server/model/v1/pairs/{}/roots?limit=200".format(quote(self.target_pair_id, safe=""))))
            root = next((item for item in roots if str(item.get("file_id")) == self.target_root_id), None)
            if root is None or root.get("name") != self.target_root_name:
                raise ObserverSchemaError("target root identity changed")
            if self.expected_root_relative_path is not None and root.get("full_path") != self.expected_root_relative_path:
                raise ObserverSchemaError("target root relative path changed")
            if self.require_pending_transfer:
                expected_flags = {
                    "remote_present": True,
                    "remote_has_transferable_content": True,
                    "local_present": True,
                    "complete_local_coverage": False,
                    "final_move_succeeded": False,
                    "explicitly_stopped": False,
                }
                for field_name, expected_value in expected_flags.items():
                    if root.get(field_name) is not expected_value:
                        raise ObserverSchemaError("target lifecycle is not queue-ready: {}".format(field_name))
            status = controller_status(get("/server/status"))
            # Keep identities only in process memory; the persisted packet is redacted.
            evidence = {"pair_id": self.target_pair_id, "root_found": True,
                        "root_state": root.get("state"),
                        "controller_keys": sorted(str(key) for key in status.keys()),
                        "scan_lifecycle": scan_lifecycle}
            json.dumps(evidence, sort_keys=True)
            self.scan_lifecycle = scan_lifecycle
            captured = self._persist({
                "schema": "incoming-recovery-observer-event.v1", "event": "preflight",
                "outcome": "success", "target_verified": True, "root_verified": True,
                "evidence": evidence,
            }, "preflight_success")
            if not captured:
                raise ObserverSchemaError("preflight artifact capture failed")
            self.passed = True
            return evidence
        except Exception as exc:
            # Preserve the original API/schema failure for the caller after capture.
            self._persist({
                "schema": "incoming-recovery-observer-event.v1", "event": "preflight",
                "outcome": "failure", "error": error_artifact(exc),
            }, "preflight_failure")
            raise

    def queue(self, send: Callable[[str], object]) -> object:
        if not self.passed:
            raise ObserverSchemaError("Queue blocked until preflight passes")
        if self.queue_attempted:
            raise ObserverSchemaError("Queue already attempted for this gate")
        # Set before invoking the injected transport so re-entry or any exception
        # cannot cause a second POST, including after a repeated preflight.
        self.queue_attempted = True
        try:
            self._consume_durable_attempt()
            if not self._persist({"schema": "incoming-recovery-observer-event.v1", "event": "queue_attempt_started", "outcome": "success"}, "queue_attempt_started"):
                raise ObserverCaptureError("Queue attempt marker capture failed")
        except Exception:
            raise
        try:
            response = send(queue_path(self.target_root_name, self.target_root_id, self.target_pair_id))
        except HTTPError as exc:
            try:
                body = exc.read()
            except Exception:
                body = b""
            self.last_queue_evidence = {
                **queue_response_evidence(exc.code, body),
                "scan_lifecycle": self.scan_lifecycle,
            }
            if self.first_failure_evidence is None:
                self.first_failure_evidence = self.last_queue_evidence
            self._persist({
                "schema": "incoming-recovery-observer-event.v1", "event": "queue",
                "outcome": "failure", "first_failure": self.first_failure_evidence is self.last_queue_evidence,
                "queue_evidence": self.last_queue_evidence,
            }, "queue_failure")
            raise
        except Exception as exc:
            self.last_queue_evidence = {
                "schema": "incoming-recovery-queue-error.v1", "kind": "queue_error",
                "error_type": _safe_error_type(exc), "scan_lifecycle": self.scan_lifecycle,
            }
            if self.first_failure_evidence is None:
                self.first_failure_evidence = self.last_queue_evidence
            self._persist({
                "schema": "incoming-recovery-observer-event.v1", "event": "queue",
                "outcome": "failure", "first_failure": self.first_failure_evidence is self.last_queue_evidence,
                "failure": self.last_queue_evidence,
            }, "queue_failure")
            raise
        if isinstance(response, tuple):
            raise ObserverSchemaError("Queue transport tuple is incompatible; return QueueTransportResponse")
        status = getattr(response, "status", getattr(response, "code", None))
        if type(status) is not int or not 100 <= status <= 599:
            raise ObserverSchemaError("Queue transport did not return a status-bearing response")
        body = getattr(response, "body", b"")
        if not isinstance(body, bytes):
            raise ObserverSchemaError("Queue transport response body must be bytes")
        self.last_queue_evidence = {
            **queue_response_evidence(status, body),
            "scan_lifecycle": self.scan_lifecycle,
        }
        status_failure = not 200 <= status < 300
        first_failure = False
        if status_failure:
            if self.first_failure_evidence is None:
                self.first_failure_evidence = self.last_queue_evidence
                first_failure = True
            self._persist({
                "schema": "incoming-recovery-observer-event.v1", "event": "queue",
                "outcome": "failure", "first_failure": first_failure,
                "queue_evidence": self.last_queue_evidence,
            }, "queue_failure")
        else:
            self._persist({
                "schema": "incoming-recovery-observer-event.v1", "event": "queue",
                "outcome": "success", "queue_evidence": self.last_queue_evidence,
            }, "queue_success")
        return response
