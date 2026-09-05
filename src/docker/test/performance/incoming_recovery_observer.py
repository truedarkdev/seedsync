"""Schema-safe, side-effect-gated observer support for Incoming recovery runs.

This module intentionally has no SeedSync application dependency.  Operators inject an
HTTP transport; `preflight` performs only GETs and `queue` refuses to invoke a
POST transport until the exact same gate instance has passed validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode, quote
import json


class ObserverSchemaError(ValueError):
    """A supported API response did not meet the observer contract."""


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
    passed: bool = False

    def preflight(self, get: Callable[[str], object]) -> Mapping[str, object]:
        """Validate the live API envelopes with GET-only transport; never sends Queue."""
        self.passed = False
        pairs = path_pairs(get("/server/path-pairs"))
        pair = next((item for item in pairs if item.get("id") == self.target_pair_id), None)
        if pair is None or pair.get("name") != self.target_pair_name or pair.get("auto_queue") is not False:
            raise ObserverSchemaError("target pair is not isolated")
        if any(item.get("auto_queue") is True for item in pairs):
            raise ObserverSchemaError("a competing pair has AutoQueue enabled")
        if autoqueue_enabled(get("/server/config/get")):
            raise ObserverSchemaError("global AutoQueue is enabled")
        roots = model_roots(get("/server/model/v1/pairs/{}/roots?limit=200".format(quote(self.target_pair_id, safe=""))))
        root = next((item for item in roots if str(item.get("file_id")) == self.target_root_id), None)
        if root is None or root.get("name") != self.target_root_name:
            raise ObserverSchemaError("target root identity changed")
        status = controller_status(get("/server/status"))
        # Enforce that every emitted dry-run observation is JSON-safe before enabling Queue.
        evidence = {"pair_id": self.target_pair_id, "root_found": True,
                    "root_state": root.get("state"),
                    "controller_keys": sorted(str(key) for key in status.keys())}
        json.dumps(evidence, sort_keys=True)
        self.passed = True
        return evidence

    def queue(self, send: Callable[[str], object]) -> object:
        if not self.passed:
            raise ObserverSchemaError("Queue blocked until preflight passes")
        return send(queue_path(self.target_root_name, self.target_root_id, self.target_pair_id))
