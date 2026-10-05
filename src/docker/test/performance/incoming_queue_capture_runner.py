#!/usr/bin/env python3
"""Default-off, root-only passive model/spool capture entrypoint."""
from __future__ import annotations

from dataclasses import dataclass, replace
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import stat
import sys
import threading
import time
from typing import Callable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit
import urllib.request

_HERE = Path(__file__).resolve().parent
SCHEMA = "incoming-recovery-passive-capture.v2"
SUPPORTED_PYTHON = (3, 12)
DEFAULT_API_BASE = "http://127.0.0.1:8800"
PASSIVE_WINDOW_SECONDS = 120.0
POLL_CADENCE_SECONDS = 1.0
LEAF_GET_TIMEOUT_SECONDS = 5.0
MAX_GET_BYTES = 1024 * 1024


class CaptureError(RuntimeError):
    pass


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("maintained performance helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_observer = _load("incoming_recovery_observer", _HERE / "incoming_recovery_observer.py")
sys.modules["incoming_recovery_observer"] = _observer
_spool = _load("breadcrumb_spool_capture_passive", _HERE / "breadcrumb_spool_capture.py")
JsonArtifactSink = _observer.JsonArtifactSink
BreadcrumbSpoolCapture = _spool.BreadcrumbSpoolCapture
ObserverCaptureError = _observer.ObserverCaptureError


def _text(value: object, limit: int = 256) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _digest(value: object) -> str:
    if isinstance(value, bytes):
        data = value
    else:
        data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def _regular(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CaptureError("protected path is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CaptureError("protected path is not a regular file")


def _private(path: Path) -> None:
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise CaptureError("protected path permissions are too broad")


def _base_url(value: object) -> str:
    value = DEFAULT_API_BASE if value is None else value
    if not isinstance(value, str):
        raise CaptureError("api base is invalid")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise CaptureError("api base is invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class Config:
    pair_id: str
    pair_name: str
    root_id: str
    root_name: str
    leaf_id: str
    leaf_name: str
    key_path: Path
    spool_path: Path
    artifact_path: Path
    physical_path: Path | None = None
    api_base_url: str = DEFAULT_API_BASE
    max_poll_bytes: int = 256 * 1024
    poll_millis: int = 250
    max_records: int = 2048
    max_output_bytes: int = 2 * 1024 * 1024
    config_digest: str | None = None
    config_source: Path | None = None

    @classmethod
    def from_mapping(cls, value: object) -> "Config":
        if not isinstance(value, Mapping) or not isinstance(value.get("selector"), Mapping):
            raise CaptureError("selector config is required")
        selector = value["selector"]
        pair = selector.get("pair") if isinstance(selector.get("pair"), Mapping) else selector
        root = selector.get("root") if isinstance(selector.get("root"), Mapping) else selector
        fields = (pair.get("id"), pair.get("name"), root.get("id"), root.get("name"), selector.get("leaf_id"), selector.get("leaf_name"))
        if not all(_text(item) for item in fields):
            raise CaptureError("exact selector identity is required")
        if root["id"] == selector["leaf_id"] and root["name"] != selector["leaf_name"]:
            raise CaptureError("root file selector names must match")
        paths: dict[str, Path] = {}
        for name in ("key_path", "spool_path", "artifact_path"):
            raw = value.get(name)
            if not isinstance(raw, str) or not os.path.isabs(raw):
                raise CaptureError("protected path is invalid")
            path = Path(raw)
            if path.parent.is_symlink():
                raise CaptureError("protected path parent is a symlink")
            paths[name] = path
        _regular(paths["key_path"])
        _private(paths["key_path"])
        physical = value.get("physical_path")
        physical_path = None
        if physical is not None:
            if not isinstance(physical, str) or not os.path.isabs(physical):
                raise CaptureError("physical path is invalid")
            physical_path = Path(physical)
        max_bytes = value.get("max_poll_bytes", 256 * 1024)
        poll_millis = value.get("poll_millis", 250)
        max_records = value.get("max_records", 2048)
        max_output = value.get("max_output_bytes", 2 * 1024 * 1024)
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1024 * 1024:
            raise CaptureError("poll byte bound is invalid")
        if type(poll_millis) is not int or not 1 <= poll_millis <= 2000:
            raise CaptureError("poll time bound is invalid")
        if type(max_records) is not int or not 4 <= max_records <= 4096:
            raise CaptureError("record bound is invalid")
        if type(max_output) is not int or not 1024 <= max_output <= 4 * 1024 * 1024:
            raise CaptureError("artifact bound is invalid")
        return cls(
            pair["id"], pair["name"], root["id"], root["name"], selector["leaf_id"], selector["leaf_name"],
            paths["key_path"], paths["spool_path"], paths["artifact_path"], physical_path,
            _base_url(value.get("api_base_url")), max_bytes, poll_millis, max_records, max_output,
        )

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> "Config":
        config_path = Path(path)
        _regular(config_path)
        _private(config_path)
        if config_path.parent.is_symlink():
            raise CaptureError("protected config parent is a symlink")
        try:
            raw = config_path.read_bytes()
            config = cls.from_mapping(json.loads(raw.decode("utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CaptureError("protected config cannot be read") from exc
        return replace(config, config_digest=_digest(raw), config_source=config_path)

    @property
    def selector_hash(self) -> str:
        return _digest({
            "pair_id": self.pair_id, "pair_name": self.pair_name,
            "root_id": self.root_id, "root_name": self.root_name,
        })

    @property
    def leaf_hash(self) -> str:
        # This intentionally excludes all mutable model fields, including state
        # and wire model_version.
        return _digest({
            "pair_id": self.pair_id, "root_id": self.root_id,
            "leaf_id": self.leaf_id, "leaf_name": self.leaf_name,
        })


def _leaf_path(config: Config) -> str:
    if config.root_id == config.leaf_id:
        return "/server/model/v1/pairs/{}/roots?limit=200".format(
            quote(config.pair_id, safe=""),
        )
    return "/server/model/v1/pairs/{}/children?parent_file_id={}&limit=200".format(
        quote(config.pair_id, safe=""), quote(config.root_id, safe=""),
    )


def _wire_model_version(payload: object) -> int | None:
    if isinstance(payload, Mapping):
        value = payload.get("model_version")
        if type(value) is int and 0 <= value <= 2**31 - 1:
            return value
    return None


def _leaf_receipt(config: Config, payload: object) -> tuple[Mapping[str, object], int | None, Mapping[str, object]]:
    if not isinstance(payload, Mapping) or payload.get("path_pair_id") != config.pair_id:
        raise CaptureError("leaf page scope is invalid")
    records = payload.get("records")
    if not isinstance(records, list) or not 1 <= len(records) <= 200:
        raise CaptureError("leaf page shape is invalid")
    matches = [record for record in records if isinstance(record, Mapping) and record.get("file_id") == config.leaf_id and record.get("name") == config.leaf_name]
    if len(matches) != 1:
        raise CaptureError("selected leaf receipt is invalid")
    record = matches[0]
    if record.get("path_pair_id") != config.pair_id:
        raise CaptureError("selected target scope is invalid")
    if config.root_id == config.leaf_id and record.get("is_dir") is True:
        child_count = record.get("child_count")
        if type(child_count) is not int or child_count < 0 or record.get("has_children") is not (child_count > 0) or record.get("children") not in ([], None):
            raise CaptureError("selected root shape is invalid")
    elif record.get("is_dir") is not False or record.get("has_children") is not False or record.get("children") not in ([], None):
        raise CaptureError("selected target shape is invalid")
    return {"selector_hash": config.selector_hash, "leaf_hash": config.leaf_hash}, _wire_model_version(payload), record


def _physical_receipt(config: Config, record: Mapping[str, object]) -> dict[str, object]:
    if config.physical_path is None:
        return {"outcome": "not_configured"}
    try:
        info = config.physical_path.lstat()
    except FileNotFoundError:
        return {"outcome": "absent"}
    except OSError:
        return {"outcome": "unavailable"}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return {"outcome": "invalid"}
    size = int(info.st_size)
    expected = record.get("local_size", record.get("size"))
    match = type(expected) is int and expected >= 0 and size == expected
    return {"outcome": "initial_observed" if match else "mismatch", "size": size, "size_match": match}


def _model_snapshot(identity: Mapping[str, object], model_version: int | None, record: Mapping[str, object]) -> dict[str, object]:
    """Retain finite current leaf evidence needed to interpret cadence."""
    snapshot: dict[str, object] = {
        "schema": "incoming-recovery-model-snapshot.v1", "kind": "leaf",
        "leaf_hash": identity["leaf_hash"], "model_version": model_version,
        "state": record.get("state") if isinstance(record.get("state"), str) else "unknown",
    }
    for key in (
        "size", "local_size", "remote_size", "transferred_size", "display_size_total",
        "display_transferred_size", "download_progress", "downloading_speed", "eta",
    ):
        value = record.get(key)
        if type(value) is int and 0 <= value <= 2**63 - 1 and (key != "download_progress" or value <= 100):
            snapshot[key] = value
    for key in ("remote_present", "remote_has_transferable_content", "local_present", "complete_local_coverage", "final_move_succeeded", "explicitly_stopped"):
        value = record.get(key)
        if type(value) is bool:
            snapshot[key] = value
    return snapshot


def _visible_state(snapshot: Mapping[str, object]) -> str:
    """Mirror the Controller's bounded visible-state predicate for one record."""
    state = snapshot.get("state")
    if not isinstance(state, str):
        return "unknown"
    if snapshot.get("local_present") is True and snapshot.get("remote_has_transferable_content") is False:
        return "local_only"
    final_move = snapshot.get("final_move_succeeded") is True
    explicitly_stopped = snapshot.get("explicitly_stopped") is True
    if state in {"default", "downloaded"} and explicitly_stopped and not final_move:
        return "stopped"
    if state == "default":
        display_total = snapshot.get("display_size_total")
        display_transferred = snapshot.get("display_transferred_size")
        has_display_union = type(display_total) is int and type(display_transferred) is int
        complete_display = (
            has_display_union and display_total > 0 and display_transferred >= display_total
        )
        total = display_total if has_display_union else snapshot.get("remote_size")
        transferred = display_transferred if has_display_union else snapshot.get("transferred_size")
        complete_progress = (
            snapshot.get("complete_local_coverage") is True
            and (not has_display_union or complete_display)
            and snapshot.get("explicitly_stopped") is False
        )
        retained_progress = (
            snapshot.get("remote_has_transferable_content") is True
            and (total if type(total) is int else 0) > 0
            and (
                (transferred if type(transferred) is int else 0) > 0
                or (snapshot.get("download_progress") if type(snapshot.get("download_progress")) is int else 0) > 0
            )
        )
        if complete_progress:
            return "downloaded"
        if retained_progress:
            return "stopped"
    if state == "downloaded" and final_move:
        return "move_succeeded"
    return state


class Runner:
    def __init__(self, config: Config, *, opener: Callable[..., object] = urllib.request.urlopen):
        self.config = config
        self.opener = opener
        self.sink: JsonArtifactSink | None = None
        self._event_lock = threading.Lock()
        self._accept_events = True

    def _event(self, event: str, *, phase: str, outcome: str = "success", force: bool = False, **fields: object) -> None:
        if self.sink is None:
            return
        with self._event_lock:
            if not self._accept_events and not force:
                return
            self.sink.persist({"schema": SCHEMA, "event": event, "phase": phase, "outcome": outcome, **fields})

    @staticmethod
    def _platform_preflight() -> None:
        if not sys.platform.startswith("linux") or not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise CaptureError("root-only Linux execution is required")
        if sys.version_info[:2] != SUPPORTED_PYTHON:
            raise CaptureError("supported runtime requires the deployed Python 3.12 series")

    def _assert_config_unchanged(self) -> None:
        if self.config.config_source is None or self.config.config_digest is None:
            return
        try:
            digest = _digest(self.config.config_source.read_bytes())
        except OSError as exc:
            raise CaptureError("protected config is unavailable") from exc
        if digest != self.config.config_digest:
            raise CaptureError("protected config changed")

    def _get(self, path: str) -> tuple[Mapping[str, object], int | None, int]:
        if path != _leaf_path(self.config):
            raise CaptureError("GET route is not allowlisted")
        selector_hash, leaf_hash = self.config.selector_hash, self.config.leaf_hash
        invocation_utc = time.time_ns() // 1_000_000
        invocation_mono = time.monotonic_ns() // 1_000_000
        self._event(
            "model_get_invoked", phase="model_poll", timeout_seconds=LEAF_GET_TIMEOUT_SECONDS,
            invocation_utc_ms=invocation_utc, invocation_monotonic_ms=invocation_mono,
            selector_hash=selector_hash, leaf_hash=leaf_hash,
        )
        response_utc: int | None = None
        response_mono: int | None = None
        try:
            key = self.config.key_path.read_text(encoding="utf-8").strip()
            if not key:
                raise CaptureError("protected credential is empty")
            base = urlsplit(self.config.api_base_url)
            target = urlunsplit((base.scheme, base.netloc, base.path.rstrip("/") + path, "", ""))
            request = urllib.request.Request(target, method="GET", headers={"Authorization": "Bearer " + key})
            with self.opener(request, timeout=LEAF_GET_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", None)
                body = response.read(MAX_GET_BYTES + 1)
                response_utc = time.time_ns() // 1_000_000
                response_mono = time.monotonic_ns() // 1_000_000
                if not isinstance(body, (bytes, bytearray)) or len(body) > MAX_GET_BYTES:
                    raise CaptureError("model response is oversized")
                if type(status) is not int or not 200 <= status <= 299:
                    raise CaptureError("model response status is invalid")
                payload = json.loads(bytes(body).decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise CaptureError("model response shape is invalid")
            self._event(
                "model_get_response", phase="model_poll", response_utc_ms=response_utc,
                response_monotonic_ms=response_mono, status_code=status,
                selector_hash=selector_hash, leaf_hash=leaf_hash,
                model_version=_wire_model_version(payload),
            )
            return payload, _wire_model_version(payload), response_mono
        except BaseException as exc:
            if response_utc is None:
                response_utc = time.time_ns() // 1_000_000
                response_mono = time.monotonic_ns() // 1_000_000
            self._event(
                "model_get_failure", phase="model_poll", outcome="failure",
                response_utc_ms=response_utc, response_monotonic_ms=response_mono,
                error_type="timeout" if isinstance(exc, TimeoutError) else "transport_error",
                selector_hash=selector_hash, leaf_hash=leaf_hash,
            )
            raise

    def run(self) -> dict[str, object]:
        self._platform_preflight()
        self._assert_config_unchanged()
        self.sink = JsonArtifactSink(self.config.artifact_path)
        capture_start_utc_ms: int | None = None
        capture_start_monotonic_ms: int | None = None
        reader: BreadcrumbSpoolCapture | None = None
        physical: dict[str, object] = {"outcome": "not_observed"}
        model_outcome = "not_observed"
        reader_outcome = "not_armed"
        terminal_written = False
        model_thread: threading.Thread | None = None
        model_result: list[tuple[Mapping[str, object], int | None, Mapping[str, object], int]] = []
        model_errors: list[str] = []
        target_observation: dict[str, object] = {
            "outcome": "unknown", "visible_state": "unknown",
            "explicitly_stopped": None,
            "first_sample_monotonic_ms": None, "last_sample_monotonic_ms": None,
            "expected_interval_ms": max(1, int(POLL_CADENCE_SECONDS * 1000)),
            "max_gap_ms": max(1, int((POLL_CADENCE_SECONDS + LEAF_GET_TIMEOUT_SECONDS) * 1000)),
            "max_observed_gap_ms": 0, "gap_count": 0,
            "last_error_type": None, "_last_terminal_state": None,
            "_completed_seen": False,
            "_previous_bytes": None,
        }

        def target_summary(*, final: bool = False) -> dict[str, object]:
            outcome = target_observation["outcome"]
            if outcome == "unknown" and target_observation["first_sample_monotonic_ms"] is None and target_observation["last_error_type"]:
                outcome = "selector_unknown" if target_observation["last_error_type"] == "selector_error" else "read_failure"
            elif final and outcome == "unknown":
                outcome = "deadline"
            return {
                "target_outcome": outcome,
                "target_visible_state": target_observation["visible_state"],
                "target_explicitly_stopped": target_observation["explicitly_stopped"],
                "target_first_sample_monotonic_ms": target_observation["first_sample_monotonic_ms"],
                "target_last_sample_monotonic_ms": target_observation["last_sample_monotonic_ms"],
                "target_expected_interval_ms": target_observation["expected_interval_ms"],
                "target_max_gap_ms": target_observation["max_gap_ms"],
                "target_max_observed_gap_ms": target_observation["max_observed_gap_ms"],
                "target_gap_count": target_observation["gap_count"],
            }

        def capture_summary() -> dict[str, object]:
            if reader is None:
                return {
                    "state": "loss", "reason": "unknown", "records": 0, "bytes_read": 0,
                    "unsupported": 0, "discarded": 0,
                    "budget": {
                        "max_bytes": self.config.max_poll_bytes, "max_millis": self.config.poll_millis,
                        "max_records": self.config.max_records, "max_output_bytes": self.config.max_output_bytes,
                        "bytes_read": 0, "elapsed_ms": 0,
                    },
                }
            projection = reader.projection()
            loss = projection["loss"]
            return {
                "state": "loss" if loss["count"] else "stopped", "reason": loss["reason"] or "unknown",
                "records": projection["records"], "bytes_read": projection["source_bytes"],
                "unsupported": projection["unsupported"], "discarded": projection["discarded"],
                "budget": projection["budget"], "health": projection["health"],
            }

        def terminal(outcome: str) -> None:
            nonlocal terminal_written
            if terminal_written:
                return
            self._accept_events = False
            self._event(
                "terminal", phase="finished", outcome=outcome, force=True,
                capture_start_utc_ms=capture_start_utc_ms,
                capture_start_monotonic_ms=capture_start_monotonic_ms,
                capture_end_utc_ms=time.time_ns() // 1_000_000,
                capture_end_monotonic_ms=time.monotonic_ns() // 1_000_000,
                physical_outcome=physical.get("outcome", "not_observed"),
                model_outcome=model_outcome, reader_outcome=reader_outcome,
                completion_proven=False, **target_summary(final=True), capture=capture_summary(),
            )
            terminal_written = True

        def consume_model() -> None:
            nonlocal model_thread, model_outcome
            if model_thread is None or model_thread.is_alive():
                return
            model_thread.join()
            if model_result:
                identity, model_version, record, response_monotonic_ms = model_result.pop(0)
                model_outcome = "observed"
                snapshot = _model_snapshot(identity, model_version, record)
                visible = _visible_state(snapshot)
                snapshot["visible_state"] = visible
                prior = target_observation["last_sample_monotonic_ms"]
                if type(prior) is int:
                    gap = max(0, response_monotonic_ms - prior)
                    target_observation["max_observed_gap_ms"] = max(target_observation["max_observed_gap_ms"], gap)
                    if gap > target_observation["max_gap_ms"]:
                        target_observation["gap_count"] += 1
                else:
                    target_observation["first_sample_monotonic_ms"] = response_monotonic_ms
                target_observation["last_sample_monotonic_ms"] = response_monotonic_ms
                target_observation["visible_state"] = visible
                target_observation["explicitly_stopped"] = snapshot.get("explicitly_stopped")
                bytes_now = snapshot.get("transferred_size")
                bytes_before = target_observation["_previous_bytes"]
                if visible in {"downloaded", "move_succeeded", "extracted"}:
                    target_observation["_completed_seen"] = True
                    target_observation["outcome"] = "completed"
                elif target_observation["_completed_seen"] and visible in {"default", "stopped", "deleted", "move_failed", "corrupt"}:
                    target_observation["outcome"] = "rollback"
                elif visible == "stopped" and snapshot.get("explicitly_stopped") is False and type(bytes_now) is int and type(bytes_before) is int and bytes_now < bytes_before:
                    target_observation["outcome"] = "rollback"
                elif visible == "stopped":
                    target_observation["outcome"] = "stopped"
                elif target_observation["outcome"] in {"stopped", "rollback"}:
                    target_observation["outcome"] = "unknown"
                target_observation["_previous_bytes"] = bytes_now if type(bytes_now) is int else bytes_before
                self._event(
                    "model_snapshot", phase="model_poll", selector_hash=identity["selector_hash"],
                    leaf_hash=identity["leaf_hash"], model_version=model_version,
                    response_monotonic_ms=response_monotonic_ms, model_snapshot=snapshot,
                )
                terminal_state = target_observation["outcome"]
                if terminal_state in {"completed", "stopped", "rollback"} and target_observation["_last_terminal_state"] != terminal_state:
                    self._event(
                        "target_terminal", phase="target_watch",
                        outcome="success" if terminal_state == "completed" else "failure",
                        **target_summary(),
                    )
                    target_observation["_last_terminal_state"] = terminal_state
            elif model_errors:
                model_outcome = "observed_with_loss" if model_outcome == "observed" else "loss"
                target_observation["last_error_type"] = model_errors[-1]
                target_observation["outcome"] = "selector_unknown" if model_errors[-1] == "selector_error" else "read_failure"
                model_errors.clear()
            model_thread = None

        def model_poll() -> None:
            try:
                payload, _model_version, response_monotonic_ms = self._get(_leaf_path(self.config))
            except BaseException as exc:
                model_errors.append("timeout" if isinstance(exc, TimeoutError) else "transport_error")
                return
            try:
                identity, version, record = _leaf_receipt(self.config, payload)
            except BaseException:
                model_errors.append("selector_error")
                return
            model_result.append((identity, version, record, response_monotonic_ms))

        def start_model() -> None:
            nonlocal model_thread
            if model_thread is not None:
                return
            model_result.clear()
            model_errors.clear()
            model_thread = threading.Thread(target=model_poll, name="incoming-model-poll", daemon=True)
            model_thread.start()

        try:
            runtime = {"implementation": platform.python_implementation(), "version": platform.python_version()}
            self._event("runtime_verified", phase="runtime", runtime_hash=_digest(runtime), runtime_version=runtime["version"])
            reader = BreadcrumbSpoolCapture(
                self.config.spool_path, self.sink, max_bytes=self.config.max_poll_bytes,
                max_millis=self.config.poll_millis, max_records=self.config.max_records,
                max_output_bytes=self.config.max_output_bytes,
            )
            payload, model_version, _initial_response_monotonic_ms = self._get(_leaf_path(self.config))
            try:
                identity, _model_version, selected = _leaf_receipt(self.config, payload)
            except CaptureError:
                target_observation["last_error_type"] = "selector_error"
                target_observation["outcome"] = "selector_unknown"
                raise
            physical = _physical_receipt(self.config, selected)
            model_outcome = "observed"
            self._event(
                "leaf_receipt", phase="leaf", selector_hash=identity["selector_hash"],
                leaf_hash=identity["leaf_hash"], model_version=model_version,
                physical_outcome=physical["outcome"],
            )
            self._assert_config_unchanged()
            # H0/H1 are established before the passive window begins. Any
            # bytes already present are intentionally pre-arm history.
            reader.start()
            capture_start_utc_ms = time.time_ns() // 1_000_000
            capture_start_monotonic_ms = time.monotonic_ns() // 1_000_000
            self._event(
                "capture_started", phase="capture",
                capture_start_utc_ms=capture_start_utc_ms,
                capture_start_monotonic_ms=capture_start_monotonic_ms,
            )
            reader.poll()
            reader_outcome = "healthy"
            deadline = time.monotonic() + PASSIVE_WINDOW_SECONDS
            while time.monotonic() < deadline:
                self._assert_config_unchanged()
                consume_model()
                if reader_outcome != "loss":
                    try:
                        reader.poll()
                    except ObserverCaptureError:
                        reader_outcome = "loss"
                start_model()
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(POLL_CADENCE_SECONDS, remaining))
            if model_thread is not None:
                model_thread.join(timeout=LEAF_GET_TIMEOUT_SECONDS)
                if model_thread.is_alive():
                    model_outcome = "observed_with_loss" if model_outcome == "observed" else "loss"
                    target_observation["last_error_type"] = "timeout"
                else:
                    consume_model()
            try:
                reader.poll()
            except ObserverCaptureError:
                reader_outcome = "loss"
            try:
                reader.stop()
            except ObserverCaptureError:
                reader_outcome = "loss"
            terminal_outcome = "failure" if reader_outcome == "loss" or "loss" in model_outcome else "success"
            terminal(terminal_outcome)
            return {
                "schema": SCHEMA, "outcome": terminal_outcome,
                "completion_proven": False, "physical_outcome": physical["outcome"],
                "model_outcome": model_outcome, "reader_outcome": reader_outcome,
                **target_summary(final=True),
            }
        except BaseException as exc:
            if target_observation["first_sample_monotonic_ms"] is None:
                if target_observation["outcome"] == "unknown":
                    target_observation["outcome"] = "read_failure"
                if target_observation["last_error_type"] is None:
                    target_observation["last_error_type"] = "timeout" if isinstance(exc, TimeoutError) else "observer_error"
            try:
                if model_thread is not None:
                    model_thread.join(timeout=LEAF_GET_TIMEOUT_SECONDS)
                terminal("failure")
            except Exception:
                pass
            raise
        finally:
            if reader is not None:
                reader.close()


def run(config_path: str | os.PathLike[str]) -> dict[str, object]:
    Runner._platform_preflight()
    return Runner(Config.from_path(config_path)).run()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded passive model/spool capture; requires a protected private config.")
    parser.add_argument("--config", required=True, help="protected private JSON configuration")
    args = parser.parse_args(argv)
    try:
        result = run(args.config)
    except Exception:
        print(json.dumps({"schema": SCHEMA, "outcome": "failure"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
