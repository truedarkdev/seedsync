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

from .breadcrumb_trace import opaque_trace_correlation


ROOT_PROGRESS_TRACE_CATEGORY = "model.progress"
ROOT_PROGRESS_TRACE_SCHEMA = "model_root_progress.v1"
ROOT_PROGRESS_TRACE_SOURCE = "root_progress"
_MAX_SUBJECTS = 128
_ALLOWED_DETAIL_KEYS = frozenset({
    "scope_digest", "model_version", "scope_version", "outcome", "reason",
    "status_source", "status_state", "status_type", "decision", "build_kind",
    "publication", "event", "progress_percent", "percent_bucket",
    "speed_bucket", "eta_bucket", "root_count", "page_count", "record_count",
    "target_count", "duration_ms", "duration_bucket", "observed_ms",
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
            self.__generation = generation

    def enabled(self, level: str = "debug") -> bool:
        trace = self.__trace_object()
        if trace is None:
            return False
        effective = getattr(trace, "is_effectively_enabled", None)
        if callable(effective):
            try:
                result = effective(ROOT_PROGRESS_TRACE_CATEGORY, level)
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

    def record(
        self,
        identity: object,
        stage: str,
        details: Mapping[str, object],
        *,
        level: str = "debug",
    ) -> bool:
        """Record one bounded state/progress change, returning admission status."""
        if not self.enabled(level):
            return False
        try:
            trace = self.__trace_object()
            if trace is None:
                return False
            job_digest = opaque_trace_correlation(identity)
            safe = _safe_details(details)
            safe["schema"] = ROOT_PROGRESS_TRACE_SCHEMA
            safe["job_digest"] = job_digest
            # Timestamp is useful for correlating a trace with logs, but is not
            # part of the dedupe signature so a stable poll does not churn.
            observed_ms = int(time.time_ns() / 1_000_000)
            safe["observed_ms"] = observed_ms
            # Status polls are state-change coalesced across model ticks, so a
            # version-only change does not create a breadcrumb. Publication
            # and serialization boundaries must retain version changes: they
            # are the correlation evidence needed to explain a delayed page.
            ignored_signature_keys = {"observed_ms", "duration_ms"}
            if stage == "status":
                ignored_signature_keys.update({"model_version", "scope_version"})
            signature_details = {
                key: value for key, value in safe.items()
                if key not in ignored_signature_keys
            }
            signature = json.dumps(signature_details, sort_keys=True, separators=(",", ":"))
            key = (job_digest, stage)
            with self.__lock:
                self.__sync_generation_locked(trace)
                if self.__last_signatures.get(key) == signature:
                    self.__last_signatures.move_to_end(key)
                    return False
                next_sequence = self.__next_sequence + 1
                safe["sequence"] = next_sequence
                recorder = getattr(trace, "record", None)
                if not callable(recorder):
                    return False
                outcome = recorder(
                    ROOT_PROGRESS_TRACE_SOURCE,
                    "root_progress_{}".format(stage),
                    safe,
                    stage="root_progress_{}".format(stage),
                    event_type="diagnostic",
                    category=ROOT_PROGRESS_TRACE_CATEGORY,
                    level=level,
                    corr_id="root-progress:{}".format(job_digest),
                    flow_id="root-progress:{}".format(job_digest),
                    trace_scope="flow",
                    _coalesce_key="root-progress:{}:{}:{}".format(
                        job_digest, stage,
                        hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16],
                    ),
                )
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
