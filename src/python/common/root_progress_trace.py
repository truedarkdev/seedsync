# Copyright 2026, SeedSync Contributors, All rights reserved.

"""Bounded, privacy-safe breadcrumbs for active root progress diagnostics.

This module deliberately contains no model or transfer ownership.  It only
turns already-authorized scalar observations into opt-in breadcrumbs and
coalesces stable observations before they reach the collector.
"""

from __future__ import annotations

import json
import hashlib
import math
import time
from collections import OrderedDict
from threading import RLock
from typing import Any, Mapping, Optional
from weakref import ReferenceType, WeakKeyDictionary, ref

from .breadcrumb_trace import BreadcrumbTraceEmitter, opaque_trace_correlation


ROOT_PROGRESS_TRACE_CATEGORY = "model.progress"
ROOT_PROGRESS_TRACE_SCHEMA = "model_root_progress.v1"
ROOT_PROGRESS_TRACE_SOURCE = "root_progress"
ROOT_PROGRESS_HEALTH_SCHEMA = "model_root_progress_health.v1"
ROOT_PROGRESS_HEALTH_OUTCOMES = (
    "trace_policy_disabled",
    "tracer_deduplicated",
    "emitter_rejected",
    "emitter_enqueued",
    "collector_accepted",
    "collector_evicted",
    "collector_rejected",
    "summary_observed",
)
_MAX_SUBJECTS = 128
NUMERIC_PROGRESS_SCHEMA = "model_numeric_progress.v1"
NUMERIC_PROGRESS_FIELDS = (
    "status_bytes", "status_total", "selected_bytes", "local_bytes", "remote_bytes",
    "ancestor_bytes", "serialized_bytes", "serialized_total", "epoch", "model_version",
    "live_root_bytes", "live_descendant_bytes", "nonlive_descendant_bytes", "blocked_descendant_bytes",
    "status_age_ms", "input_epoch", "active_bytes", "active_generation", "active_produced_ms", "active_admitted_ms",
)
_NUMERIC_PHASES = frozenset({"status", "builder", "builder_leaf", "publication", "json", "sse", "active_scan"})


def numeric_progress_details(details: Mapping[str, object]) -> dict[str, object]:
    """Finite numeric evidence; decimal strings preserve uint63 exactly."""
    def number(value: object) -> Optional[str]:
        if type(value) is int and 0 <= value <= 2**63 - 1:
            return str(value)
        if isinstance(value, str) and len(value) <= 19 and value.isascii() and value.isdigit():
            parsed = int(value)
            if parsed <= 2**63 - 1 and str(parsed) == value:
                return value
        return None
    poll = details.get("poll_correlation")
    valid_poll = isinstance(poll, str) and poll.startswith("lftp-poll:") and len(poll) == 26 and \
        all(char in "0123456789abcdef" for char in poll[10:])
    origin = details.get("input_correlation")
    valid_origin = isinstance(origin, str) and len(origin) == 26 and origin.startswith("lftp-poll:") and all(c in "0123456789abcdef" for c in origin[10:])
    shape = details.get("record_shape")
    source = details.get("status_source")
    values = details.get("values", details)
    values = values if isinstance(values, Mapping) else {}
    return {
        "values": {key: number(values.get(key)) for key in NUMERIC_PROGRESS_FIELDS},
        # A failed/current poll identifies the evaluation, not cached credit's producer.
        "poll_correlation": poll if valid_poll and source not in (
            "cached_inflight", "cached_retry", "cached_error", "cached_unhealthy", "cached_idle",
        ) else None,
        "input_correlation": origin if valid_origin else None,
        "overlay_deferred": details.get("overlay_deferred") if type(details.get("overlay_deferred")) is bool else None,
        "builder_cache_present": details.get("builder_cache_present") if type(details.get("builder_cache_present")) is bool else None,
        "active_adopted": details.get("active_adopted") if type(details.get("active_adopted")) is bool else None,
        "record_shape": shape if shape in ("at", "got", "none") else "none",
        "status_source": source if source in (
            "fresh_healthy", "fresh_unhealthy", "cached_inflight", "inflight_empty", "cached_retry",
            "retry_empty", "cached_error", "error_empty", "cached_unhealthy", "unhealthy_empty",
            "cached_idle", "unknown",
        ) else "unknown",
        "status_fresh": details.get("status_fresh") if type(details.get("status_fresh")) is bool else None,
        "status_healthy": details.get("status_healthy") if type(details.get("status_healthy")) is bool else None,
        "live_credit": details.get("live_credit") if type(details.get("live_credit")) is bool else None,
    }
_ALLOWED_DETAIL_KEYS = frozenset({
    "scope_digest", "model_version", "scope_version", "outcome", "reason",
    "status_source", "status_state", "status_type", "decision", "build_kind",
    "publication", "event", "progress_percent", "percent_bucket",
    "speed_bucket", "eta_bucket", "root_count", "page_count", "record_count",
    "target_count", "duration_ms", "duration_bucket", "observed_ms",
    "representation", "status_count_bucket", "active_count_bucket",
    "sampled_count_bucket", "sampled_truncated",
})


def _bucket(value: object, bounds: tuple[int, ...]) -> Optional[str]:
    if type(value) is not int or value < 0:
        return None
    lower = 0
    for upper in bounds:
        if value <= upper:
            return "{}-{}".format(lower, upper)
        lower = upper + 1
    return "{}+".format(bounds[-1] + 1)


def percent_bucket(value: object) -> Optional[str]:
    """Return a coarse percentage bucket without retaining transfer bytes."""
    if type(value) not in (int, float) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    number = max(0.0, min(100.0, number))
    return _bucket(int(number), (9, 24, 49, 74, 99, 100))


def scalar_percent(value: object) -> Optional[int]:
    if type(value) not in (int, float) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0, min(100, int(round(number))))


def speed_bucket(value: object) -> Optional[str]:
    return _bucket(value, (1023, 1024 * 1024, 10 * 1024 * 1024, 100 * 1024 * 1024))


def eta_bucket(value: object) -> Optional[str]:
    return _bucket(value, (9, 59, 299, 1799, 7199))


def duration_bucket(value: object) -> Optional[str]:
    return _bucket(value, (4, 19, 99, 499, 1999))


def count_bucket(value: object) -> Optional[str]:
    """Return a small count bucket for aggregate diagnostics."""
    return _bucket(value, (0, 1, 4, 16, 64, 127))


def _safe_details(details: Mapping[str, object]) -> dict[str, object]:
    """Keep the progress schema scalar and reject accidental identity fields."""
    result: dict[str, object] = {}
    for key, value in details.items():
        if key not in _ALLOWED_DETAIL_KEYS:
            continue
        if value is None or type(value) in (bool, int, float, str):
            result[key] = value
    return result


class RootProgressTrace:
    """State-change coalescer shared by one breadcrumb collector."""

    def __init__(self, breadcrumb_trace: object):
        # The tracer is the value in a WeakKeyDictionary.  Do not retain the
        # collector from that value or the weak key can never be collected.
        try:
            self.__trace: ReferenceType[object] | object = ref(breadcrumb_trace)
        except TypeError:
            # A non-weak-referenceable process emitter is an exceptional
            # compatibility surface and is not stored in the weak registry.
            self.__trace = breadcrumb_trace
        self.__lock = RLock()
        self.__next_sequence = 0
        self.__last_signatures: OrderedDict[tuple[str, str], str] = OrderedDict()
        self.__generation: Optional[int] = None
        self.__numeric_subjects: OrderedDict[str, None] = OrderedDict()
        self.__numeric_publications: OrderedDict[tuple[str, int], dict[str, object]] = OrderedDict()
        self.__numeric_inputs: OrderedDict[str, dict[str, object]] = OrderedDict()
        self.__numeric_last_emit: OrderedDict[tuple[str, str], float] = OrderedDict()

    def select_numeric_subject(self, digest: str) -> None:
        """Reuse only subjects admitted by the existing Builder witness selector."""
        try:
            if not (self.enabled("info") or self.enabled("info", category="scan.authority")) or not isinstance(digest, str) or len(digest) != 16 or \
                    any(char not in "0123456789abcdef" for char in digest):
                return
            with self.__lock:
                self.__numeric_subjects[digest] = None
                self.__numeric_subjects.move_to_end(digest)
                while len(self.__numeric_subjects) > 8:
                    self.__numeric_subjects.popitem(last=False)
        except Exception:
            pass

    def numeric_subject_selected(self, digest: str) -> bool:
        with self.__lock:
            return digest in self.__numeric_subjects

    def record_numeric(self, digest: str, phase: str, details: Mapping[str, object]) -> bool:
        """One INFO witness per selected root/phase/second; never owns model state."""
        try:
            critical_join = phase == "active_scan" or (phase == "builder" and details.get("overlay_deferred") is True)
            category = "scan.authority" if critical_join else ROOT_PROGRESS_TRACE_CATEGORY
            if phase not in _NUMERIC_PHASES or not self.enabled("info", category=category):
                return False
            with self.__lock:
                self.__sync_generation_locked(self.__trace_object())
                if digest not in self.__numeric_subjects:
                    return False
                safe = numeric_progress_details(details)
                inherited = self.__numeric_inputs.get(digest, {})
                if "input_epoch" in details and safe["values"]["input_epoch"] != inherited.get("input_epoch"):
                    inherited = {}
                provenance_keys = ("status_age_ms", "input_epoch", "active_bytes", "active_generation", "active_produced_ms", "active_admitted_ms")
                if phase in {"status", "active_scan", "builder", "builder_leaf"}:
                    current = dict(inherited)
                    for field in provenance_keys:
                        if field in details:
                            current[field] = safe["values"][field]
                    for field in ("input_correlation", "overlay_deferred", "builder_cache_present", "active_adopted"):
                        if field in details:
                            current[field] = safe[field]
                    self.__numeric_inputs[digest] = current
                    self.__numeric_inputs.move_to_end(digest)
                    while len(self.__numeric_inputs) > 8:
                        self.__numeric_inputs.popitem(last=False)
                    inherited = current
                for field in provenance_keys:
                    if field not in details:
                        safe["values"][field] = inherited.get(field)
                for field in ("input_correlation", "overlay_deferred", "builder_cache_present", "active_adopted"):
                    if field not in details:
                        safe[field] = inherited.get(field)
                version = details.get("model_version")
                key = (digest, version) if type(version) is int and version >= 0 else None
                if phase == "publication" and key is not None:
                    self.__numeric_publications[key] = {"poll_correlation": safe["poll_correlation"], "input_correlation": safe["input_correlation"], "overlay_deferred": safe["overlay_deferred"], "builder_cache_present": safe["builder_cache_present"], "active_adopted": safe["active_adopted"], "values": dict(safe["values"])}
                    self.__numeric_publications.move_to_end(key)
                    while len(self.__numeric_publications) > 128:
                        self.__numeric_publications.popitem(last=False)
                elif phase in ("json", "sse"):
                    publication = self.__numeric_publications.get(key, {})
                    for field in ("poll_correlation", "input_correlation", "overlay_deferred", "builder_cache_present", "active_adopted"):
                        safe[field] = publication.get(field)
                    for field in provenance_keys:
                        safe["values"][field] = publication.get("values", {}).get(field)
                rate_key = (digest, phase)
                now = time.monotonic()
                if now - self.__numeric_last_emit.get(rate_key, float("-inf")) < 1:
                    self.__health(self.__trace_object(), "tracer_deduplicated", "numeric_" + phase)
                    return False
                self.__numeric_last_emit[rate_key] = now
                self.__numeric_last_emit.move_to_end(rate_key)
                while len(self.__numeric_last_emit) > 128:
                    self.__numeric_last_emit.popitem(last=False)
            safe["phase"] = phase
            safe["monotonic_ms"] = int(time.monotonic_ns() // 1_000_000)
            return self.record(digest, "numeric_" + phase, safe, level="info")
        except Exception:
            return False

    def record_numeric_payload(self, payload: object, phase: str, model_version: object) -> None:
        """Inspect computed page records only, matching the existing selected roots."""
        try:
            if not self.enabled("info") or not isinstance(payload, dict):
                return
            records = payload.get("records")
            if not isinstance(records, list):
                return
            # The producer's page is already bounded; no children are walked.
            for row in records:
                if not isinstance(row, dict):
                    continue
                identity = row.get("file_id")
                if not isinstance(identity, str):
                    continue
                self.record_numeric(opaque_trace_correlation(identity), phase, {
                    "serialized_bytes": row.get("transferred_size"),
                    "serialized_total": row.get("remote_size"), "local_bytes": row.get("local_size"),
                    "model_version": model_version,
                })
        except Exception:
            pass

    def __trace_object(self) -> object:
        trace = self.__trace
        if isinstance(trace, ReferenceType):
            return trace()
        return trace

    def __sync_generation_locked(self, trace: object) -> None:
        generation_reader = getattr(trace, "trace_generation", None)
        if not callable(generation_reader):
            return
        try:
            generation = generation_reader()
        except Exception:
            return
        if type(generation) is not int:
            return
        if self.__generation is None:
            self.__generation = generation
        elif self.__generation != generation:
            self.__last_signatures.clear()
            self.__numeric_publications.clear()
            self.__numeric_inputs.clear()
            self.__numeric_last_emit.clear()
            self.__generation = generation

    def enabled(self, level: str = "debug", *, category: str = ROOT_PROGRESS_TRACE_CATEGORY) -> bool:
        trace = self.__trace_object()
        if trace is None:
            return False
        effective = getattr(trace, "is_effectively_enabled", None)
        if callable(effective):
            try:
                result = effective(category, level)
                return result if isinstance(result, bool) else False
            except Exception:
                return False
        enabled = getattr(trace, "is_enabled", None)
        if callable(enabled):
            try:
                result = enabled()
                return result if isinstance(result, bool) else False
            except Exception:
                return False
        return callable(getattr(trace, "record", None))

    @staticmethod
    def __health(
            trace: object, outcome: str, stage: str, job_digest: Optional[str] = None,
            details: Optional[Mapping[str, object]] = None, count_attempt: bool = True,
    ) -> None:
        recorder = getattr(trace, "record_root_progress_health", None)
        if not callable(recorder):
            return
        try:
            recorder(
                outcome,
                stage,
                job_digest,
                details if isinstance(details, Mapping) else None,
                count_attempt=count_attempt,
            )
        except Exception:
            # Diagnostics must never affect model publication or transfer work.
            return

    def record_summary(
            self,
            identity: object,
            stage: str,
            details: Mapping[str, object],
    ) -> bool:
        """Publish one bounded representation summary without a noisy event."""
        if not self.enabled("debug"):
            return False
        try:
            trace = self.__trace_object()
            if trace is None:
                return False
            job_digest = opaque_trace_correlation(identity)
            self.__health(
                trace, "summary_observed", stage, job_digest, _safe_details(details),
                count_attempt=False,
            )
            return True
        except Exception:
            return False

    def record(
        self,
        identity: object,
        stage: str,
        details: Mapping[str, object],
        *,
        level: str = "debug",
    ) -> bool:
        """Record one bounded state/progress change, returning admission status."""
        trace = self.__trace_object()
        if trace is None:
            return False
        try:
            job_digest = opaque_trace_correlation(identity)
            numeric = stage.startswith("numeric_") and stage[8:] in _NUMERIC_PHASES
            safe = numeric_progress_details(details) if numeric else _safe_details(details)
            critical_join = numeric and (stage == "numeric_active_scan" or (stage == "numeric_builder" and safe["overlay_deferred"] is True))
            category = "scan.authority" if critical_join else ROOT_PROGRESS_TRACE_CATEGORY
            if not self.enabled(level, category=category):
                self.__health(trace, "trace_policy_disabled", stage)
                return False
            if numeric:
                safe["phase"] = stage[8:]
                mono = details.get("monotonic_ms")
                safe["monotonic_ms"] = mono if type(mono) is int and 0 <= mono <= 2**63 - 1 else None
            safe["schema"] = NUMERIC_PROGRESS_SCHEMA if numeric else ROOT_PROGRESS_TRACE_SCHEMA
            safe["job_digest"] = job_digest
            # Timestamp is useful for correlating a trace with logs, but is not
            # part of the dedupe signature so a stable poll does not churn.
            observed_ms = int(time.time_ns() / 1_000_000)
            safe["observed_ms"] = observed_ms
            # Status polls are state-change coalesced across model ticks, so a
            # version-only change does not create a breadcrumb. Publication
            # and serialization boundaries must retain version changes: they
            # are the correlation evidence needed to explain a delayed page.
            ignored_signature_keys = {"observed_ms", "duration_ms", "monotonic_ms"}
            if numeric:
                ignored_signature_keys.update({"poll_correlation", "input_correlation"})
            if stage == "status":
                ignored_signature_keys.update({"model_version", "scope_version"})
            signature_details = {
                key: value for key, value in safe.items()
                if key not in ignored_signature_keys
            }
            if numeric:
                # Retain an unknown-to-known provenance transition without
                # churning stable values when only the opaque poll changes.
                signature_details["poll_known"] = safe["poll_correlation"] is not None
                signature_details["input_known"] = safe["input_correlation"] is not None
                critical_join = stage == "numeric_active_scan" or (stage == "numeric_builder" and safe["overlay_deferred"] is True)
                if critical_join:
                    signature_details["retention_window"] = int(time.monotonic() // 30)
            signature = json.dumps(signature_details, sort_keys=True, separators=(",", ":"))
            key = (job_digest, stage)
            with self.__lock:
                self.__sync_generation_locked(trace)
                if self.__last_signatures.get(key) == signature:
                    self.__last_signatures.move_to_end(key)
                    self.__health(trace, "tracer_deduplicated", stage, job_digest, safe)
                    return False
                next_sequence = self.__next_sequence + 1
                safe["sequence"] = next_sequence
                recorder = getattr(trace, "record", None)
                if not callable(recorder):
                    self.__health(trace, "emitter_rejected", stage, job_digest, safe)
                    return False
                outcome = recorder(
                    ROOT_PROGRESS_TRACE_SOURCE,
                    "root_progress_{}".format(stage),
                    safe,
                    stage="root_progress_{}".format(stage),
                    event_type="diagnostic",
                    category=category,
                    level=level,
                    corr_id="root-progress:{}".format(job_digest),
                    flow_id="root-progress:{}".format(job_digest),
                    trace_scope="flow",
                    _coalesce_key="root-progress:{}:{}:{}".format(
                        job_digest, stage,
                        hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16],
                    ),
                    _root_progress=True,
                )
                if outcome in {None, "retained", "accepted", "coalesced"}:
                    health_outcome = "collector_accepted"
                elif outcome == "evicted":
                    health_outcome = "collector_evicted"
                elif outcome == "enqueued":
                    health_outcome = "emitter_enqueued"
                elif isinstance(trace, BreadcrumbTraceEmitter):
                    health_outcome = "emitter_rejected"
                else:
                    health_outcome = "collector_rejected"
                self.__health(trace, health_outcome, stage, job_digest, safe)
                accepted = outcome in {None, "retained", "enqueued", "accepted", "coalesced"}
                if accepted:
                    self.__last_signatures[key] = signature
                    self.__last_signatures.move_to_end(key)
                    while len(self.__last_signatures) > _MAX_SUBJECTS:
                        self.__last_signatures.popitem(last=False)
                    self.__next_sequence = next_sequence
                return accepted
        except Exception:
            return False


_TRACERS: "WeakKeyDictionary[object, RootProgressTrace]" = WeakKeyDictionary()
_TRACERS_LOCK = RLock()


def root_progress_tracer(breadcrumb_trace: object) -> Optional[RootProgressTrace]:
    """Return a collector-local tracer without mutating/pickling the collector."""
    if breadcrumb_trace is None:
        return None
    with _TRACERS_LOCK:
        try:
            tracer = _TRACERS.get(breadcrumb_trace)
            if tracer is None:
                tracer = RootProgressTrace(breadcrumb_trace)
                _TRACERS[breadcrumb_trace] = tracer
            return tracer
        except TypeError:
            # Process emitters are not weak-referenceable; callers still get a
            # policy-gated tracer, but collector-local cross-call dedupe is not
            # available for that exceptional compatibility surface.
            return RootProgressTrace(breadcrumb_trace)
