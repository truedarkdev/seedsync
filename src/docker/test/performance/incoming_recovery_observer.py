"""Schema-safe, side-effect-gated observer support for Incoming recovery runs.

This module intentionally has no SeedSync application dependency.  Operators inject an
HTTP transport; `preflight` performs only GETs and `queue` refuses to invoke a
POST transport until the exact same gate instance has passed validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
import json


class ObserverSchemaError(ValueError):
    """A supported API response did not meet the observer contract."""


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


def error_artifact(exc: Exception) -> Mapping[str, str]:
    """Return a bounded JSON-safe error record without request or payload details."""
    return {"schema": "incoming-recovery-observer-error.v1", "kind": "preflight_error",
            "error_type": type(exc).__name__}

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

    def preflight(self, get: Callable[[str], object]) -> Mapping[str, object]:
        """Validate the live API envelopes with GET-only transport; never sends Queue."""
        self.passed = False
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
        # Enforce that every emitted dry-run observation is JSON-safe before enabling Queue.
        evidence = {"pair_id": self.target_pair_id, "root_found": True,
                    "root_state": root.get("state"),
                    "controller_keys": sorted(str(key) for key in status.keys()),
                    "scan_lifecycle": scan_lifecycle}
        json.dumps(evidence, sort_keys=True)
        self.scan_lifecycle = scan_lifecycle
        self.last_queue_evidence = None
        self.passed = True
        return evidence

    def queue(self, send: Callable[[str], object]) -> object:
        if not self.passed:
            raise ObserverSchemaError("Queue blocked until preflight passes")
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
            raise
        status = getattr(response, "status", getattr(response, "code", 200))
        self.last_queue_evidence = {
            **queue_response_evidence(status, b""),
            "scan_lifecycle": self.scan_lifecycle,
        }
        return response
