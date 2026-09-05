"""Schema-safe, side-effect-gated observer support for Incoming recovery runs.

This module intentionally has no SeedSync application dependency.  Operators inject an
HTTP transport; `preflight` performs only GETs and `queue` refuses to invoke a
POST transport until the exact same gate instance has passed validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
import json


class ObserverSchemaError(ValueError):
    """A supported API response did not meet the observer contract."""


class ObserverCaptureError(ObserverSchemaError):
    """The observer could not persist a required capture boundary."""


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

def _mapping(payload: object, name: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ObserverSchemaError(f"{name} must be an object")
    return payload


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
})
_ARTIFACT_SAFE_REASONS = _QUEUE_PREFLIGHT_REASONS | frozenset({
    "queue_accepted", "success_response", "response_unavailable", "path_pair_relocation",
    "local_delete_pending", "collision_cleanup_pending", "queue_preflight_unsafe",
    "remote_content_unavailable", "directory_root_invalid", "unrecognized_409_response",
    "preflight_cancelled_unrecognized", "first_failure", "get_failed", "get_succeeded",
})
_ARTIFACT_ERROR_TYPES = frozenset({"http_error", "timeout", "transport_error", "observer_error"})
_ARTIFACT_RESPONSE_KINDS = frozenset({"none", "mapping", "bytes", "text", "number", "object"})


def _safe_body_reason(value: object) -> str:
    if isinstance(value, str) and value in _ARTIFACT_SAFE_REASONS:
        return value
    if isinstance(value, str) and value.startswith("http_"):
        code = value[5:]
        if len(code) == 3 and code.isdigit() and 100 <= int(code) <= 599:
            return value
    return "unknown"


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
    """Run a fixed GET list synchronously, persisting each boundary in order."""

    def __init__(
        self,
        paths: Sequence[str],
        artifact_sink: object | None = None,
        *,
        continue_on_error: bool = True,
    ):
        if isinstance(paths, (str, bytes)) or not all(isinstance(path, str) and path for path in paths):
            raise ValueError("sampler paths must be non-empty strings")
        self.paths = tuple(paths)
        self.artifact_sink = (
            JsonArtifactSink(artifact_sink)
            if isinstance(artifact_sink, (str, os.PathLike)) else artifact_sink
        )
        self.continue_on_error = continue_on_error
        self.first_failure: Mapping[str, object] | None = None
        self.capture_failure: Mapping[str, object] | None = None

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

    def sample(self, get: Callable[[str], object]) -> list[Mapping[str, object]]:
        records: list[Mapping[str, object]] = []
        for index, path in enumerate(self.paths):
            # The next GET cannot begin until this record has been persisted.
            before_persisted = self._persist({
                "schema": "incoming-recovery-observer-sample.v1", "event": "get",
                "phase": "before", "request_index": index,
            }, "get_before")
            if not before_persisted:
                raise ObserverCaptureError("GET sampler artifact capture failed before request")
            try:
                response = get(path)
            except Exception as exc:
                failure = {
                    "schema": "incoming-recovery-observer-sample.v1", "event": "get",
                    "phase": "after", "outcome": "failure", "request_index": index,
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
                records.append(_sanitize_artifact(failure))
                if not persisted:
                    return records
                if not self.continue_on_error:
                    raise
                continue
            success = {
                "schema": "incoming-recovery-observer-sample.v1", "event": "get",
                "phase": "after", "outcome": "success", "request_index": index,
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
                return records
        return records

    collect = sample


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
    capture_failure: Mapping[str, object] | None = None

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
        status = getattr(response, "status", getattr(response, "code", 200))
        self.last_queue_evidence = {
            **queue_response_evidence(status, b""),
            "scan_lifecycle": self.scan_lifecycle,
        }
        status_failure = type(status) is int and status >= 400
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
