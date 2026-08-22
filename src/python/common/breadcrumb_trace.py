# Copyright 2026, SeedSync Contributors, All rights reserved.

from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
import queue
import time
from collections import deque
from threading import Lock, RLock
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
_SHARED_POLICY_MAX_BYTES = 64 * 1024
_EFFECTIVE_POLICY_CATEGORY_CACHE_MAX = 128
_POLICY_LEVEL_RANK = {level: index for index, level in enumerate(BREADCRUMB_POLICY_LEVELS)}


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
            category = str(category)
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
    ):
        self.__record_queue = record_queue
        self.__enabled_gate = enabled_gate
        self.__policy_revision = policy_revision
        self.__policy_epoch = policy_epoch
        self.__policy_generation = policy_generation
        self.__policy_length = policy_length
        self.__policy_bytes = policy_bytes
        self.__rejected_count = rejected_count
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

    def record(self, source: str, message: str, details: object = None, **metadata: Any) -> str:
        if not self.is_enabled():
            return "disabled"
        if not self.is_effectively_enabled(metadata.get("category", source), metadata.get("level", "info")):
            return "dropped"

        created_ns = time.time_ns()
        record = _bounded_ingress_record(source, message, details, metadata, created_ns, self.__policy_revision, self.__policy_epoch)
        if record is None:
            self.__reject()
            return "dropped"
        try:
            self.__record_queue.put_nowait(record)
            # This only acknowledges process-queue admission. The collector
            # remains authoritative for retention, policy, and deduplication.
            return "enqueued"
        except queue.Full:
            self.__reject()
            return "dropped"

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

    def __reject(self) -> None:
        try:
            with self.__rejected_count.get_lock():
                self.__rejected_count.value += 1
        except Exception:
            pass


def _bounded_ingress_record(source: object, message: object, details: object,
                            metadata: Mapping[str, Any], created_ns: int,
                            policy_revision: multiprocessing.Value, policy_epoch: multiprocessing.Value) -> Optional[Dict[str, Any]]:
    """Build a pickle-safe, redacted and byte-capped child-to-parent record."""
    def sanitize(value: object, key: Optional[str] = None, depth: int = 0) -> object:
        if key and any(word in key.lower() for word in ("password", "secret", "token", "auth", "cookie", "session", "api_key")):
            return "**REDACTED**"
        if depth >= 3:
            return "<truncated>"
        if isinstance(value, str):
            return (redact_sensitive_text(value) or "")[:256]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, Mapping):
            return {
                str(k)[:64]: sanitize(v, str(k), depth + 1)
                for k, v in list(value.items())[:_INGRESS_MAPPING_MAX_ITEMS]
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(v, None, depth + 1) for v in list(value)[:16]]
        return "<{}>".format(type(value).__name__)

    try:
        revision = int(policy_revision.value)
        epoch = int(policy_epoch.value)
    except Exception:
        revision, epoch = 0, 0
    record = {
        "source": sanitize(source), "message": sanitize(message),
        "details": sanitize(details), "metadata": sanitize(metadata),
        "created_ns": created_ns, "created_ms": int(created_ns / 1_000_000),
        "policy_revision": revision,
        "policy_epoch": epoch,
    }
    if not isinstance(record["source"], str) or not isinstance(record["message"], str):
        return None
    try:
        return record if len(json.dumps(record, separators=(",", ":")).encode("utf-8")) <= _INGRESS_RECORD_MAX_BYTES else None
    except (TypeError, ValueError, OverflowError):
        return None


class BreadcrumbTraceNoopEmitter:
    def is_enabled(self) -> bool:
        return False

    def is_effectively_enabled(self, category: object, level: object = "info") -> bool:
        return False

    def record(self, source: str, message: str, details: object = None, **metadata: Any) -> str:
        return "disabled"


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
    __MAX_COLLECTION_DEPTH = 3
    __MAX_LIST_ITEMS = 16
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
    __COMMAND_KEYWORDS = (
        "command",
        "cmd",
        "argv",
        "args",
        "script",
        "shell",
    )

    def __init__(
        self,
        enabled_getter: Callable[[], bool],
        max_entries: Optional[int] = None,
        memory_budget_bytes: int = DEFAULT_BREADCRUMB_MEMORY_BUDGET_BYTES,
        policy: Optional[Mapping[str, object]] = None,
        policy_persist: Optional[Callable[[Mapping[str, object]], None]] = None,
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
        self.__lost_ranges: Deque[Dict[str, Any]] = deque()
        self.__gap_watermark_to = 0
        self.__external_records: Optional[multiprocessing.Queue[object]] = None
        self.__external_records_lock = Lock()
        self.__enabled_gate = multiprocessing.RawValue("b", 0)
        self.__worker_policy_revision = multiprocessing.Value("L", 0)
        self.__worker_policy_epoch = multiprocessing.Value("L", 0)
        self.__worker_policy_generation = multiprocessing.RawValue("L", 0)
        self.__worker_policy_length = multiprocessing.RawValue("L", 0)
        self.__worker_policy_bytes = multiprocessing.RawArray("B", _SHARED_POLICY_MAX_BYTES)
        self.__ingress_rejected_count = multiprocessing.Value("L", 0)
        self.__ingress_rejected_seen = 0
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
            if self.__external_records is None:
                # Keep disabled child traffic bounded even when the retained
                # count limit is intentionally unlimited.  This is a queue
                # ingress guard, not a retained-event limit; the collector's
                # byte budget remains authoritative after sanitization.
                # Queue capacity is at most one sixteenth of the retained
                # budget even at the ingress record maximum.
                ingress_capacity = max(1, self.__memory_budget_bytes // 16 // _INGRESS_RECORD_MAX_BYTES)
                queue_limit = ingress_capacity if self.__max_entries is None else min(self.__max_entries, ingress_capacity)
                self.__external_records = multiprocessing.Queue(maxsize=queue_limit)
            # The emitter crosses the spawn boundary. Keep the collector callback,
            # locks, and owning Context in the parent; the shared gate preserves
            # runtime enable/disable updates.
            return BreadcrumbTraceEmitter(
                self.__external_records, self.__enabled_gate, self.__worker_policy_revision, self.__worker_policy_epoch,
                self.__worker_policy_generation, self.__worker_policy_length, self.__worker_policy_bytes,
                self.__ingress_rejected_count,
            )

    def is_enabled(self) -> bool:
        enabled = self.__read_enabled_state()
        self.__set_enabled_gate(enabled)
        return enabled

    def is_effectively_enabled(self, category: object, level: object = "info") -> bool:
        """Return the system/category/level gate before constructing a breadcrumb."""
        return self.is_enabled() and self.__effective_policy.allows(category, level)

    def sync_enabled_state(self) -> bool:
        enabled = self.__read_enabled_state()
        self.__set_enabled_gate(enabled)
        return enabled

    @property
    def max_entries(self) -> int:
        return self.__max_entries

    @property
    def version(self) -> int:
        return self.__version

    @property
    def memory_budget_bytes(self) -> int:
        return self.__memory_budget_bytes

    @property
    def retained_bytes(self) -> int:
        with self.__lock:
            return self.__retained_bytes

    def capabilities(self) -> Dict[str, Any]:
        """Return the small, stable service contract used by handlers."""
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

    def reset_policy(self, expected_revision: Optional[int] = None, persist: bool = False) -> Dict[str, Any]:
        """Restore the default policy, leaving retained evidence untouched."""
        return self.apply_policy({}, expected_revision=expected_revision, persist=persist)

    def __validate_policy_candidate(self, candidate: object) -> Dict[str, Any]:
        errors: List[str] = []
        if candidate is None:
            candidate_mapping: Mapping[object, object] = {}
        elif isinstance(candidate, str):
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
            raw_rules = {
                key: value for key, value in candidate_mapping.items()
                if key not in {"default", "default_level", "rules", "categories", "retention"}
            }
        if not isinstance(raw_rules, Mapping):
            errors.append("rules must be a mapping")
            raw_rules = {}

        rules: Dict[str, str] = {}
        for raw_category, raw_level in raw_rules.items():
            if not isinstance(raw_category, str) or not raw_category.strip():
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
        if not isinstance(protected_categories, list) or not all(isinstance(item, str) and item.strip() for item in protected_categories):
            errors.append("retention protected_categories must be a list of non-empty strings")
            protected_categories = []
        decision_types = retention_value.get("latest_decision_event_types", ["decision", "state_transition"])
        if not isinstance(decision_types, list) or not all(isinstance(item, str) and item.strip() for item in decision_types):
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
            "evicted": 0,
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

    def __protected_indices(self) -> set[int]:
        retention = self.__policy["retention"]
        latest_decision: Dict[str, int] = {}
        for index, entry in enumerate(self.__entries):
            if entry.get("event_type") in retention["latest_decision_event_types"]:
                latest_decision[str(entry.get("category") or "unknown")] = index
        protected = set(latest_decision.values())
        for index, entry in enumerate(self.__entries):
            if entry.get("level") in retention["protected_levels"] or self.__is_category_protected(str(entry.get("category") or "")):
                protected.add(index)
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
                return
            delta = rejected - self.__ingress_rejected_seen
            if delta <= 0:
                return
            start = self.__version + 1
            self.__version += delta
            self.__dropped_count += delta
            self.__record_gap_range(start, self.__version, "ingress_rejected")
            self.__ingress_rejected_seen = rejected

    def clear(self, scope: object = None, **filters: Any) -> Dict[str, Any]:
        """Clear retained evidence, optionally restricted to one scope.

        Policy state and accounting remain intact.  ``scope`` may be a filter
        mapping (for example ``{"corr_id": "flow-1"}``); keyword filters are
        accepted as a handler-friendly equivalent.
        """
        self.__drain_external_records(limit=None, force=True)
        clear_filters: Dict[str, Any] = {}
        if isinstance(scope, Mapping):
            clear_filters.update({str(key): value for key, value in scope.items()})
        elif isinstance(scope, str) and scope not in {"", "all", "evidence"}:
            clear_filters["category"] = scope
        clear_filters.update(filters)
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
            self.__last_signature = self.__signature(self.__entries[-1]) if self.__entries else None
            self.__coalesce_entries.clear()
            self.__refresh_failure_locked()
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
            self.__last_reset_version = self.__version
            self.__last_reset_reason = "reset"
            self.__reset_generation += 1
            self.__last_clear_scope = None
            self.__window_truncated_pending = False
            return {"cleared": True, "scope": "all", "cleared_count": cleared_count, "version": self.__version}

    def record(self, source: str, message: str, details: object = None, **metadata: Any) -> str:
        if not self.is_effectively_enabled(metadata.get("category", source), metadata.get("level", "info")):
            return "disabled"
        self.__drain_external_records_if_due(limit=self.__max_entries)
        return self.__record_entry(source, message, details, allow_when_disabled=True, **metadata)

    def __record_entry(
        self,
        source: str,
        message: str,
        details: object = None,
        allow_when_disabled: bool = False,
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
        if not isinstance(coalesce_key, str) or not coalesce_key:
            coalesce_key = None
        if not isinstance(category, str):
            category = str(category)
        if not isinstance(level, str):
            level = "info"
        level = self.__normalize_level(level) or "info"
        if metadata:
            extra_details = dict(metadata)
            if details is None:
                details = extra_details
            elif isinstance(details, dict):
                detail_mapping = cast(Dict[str, Any], details)
                details = {**detail_mapping, **extra_details}
            else:
                details = {"value": details, **extra_details}

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
            coalesced_index = next(
                (index for index, candidate in enumerate(self.__entries) if candidate is coalesced_entry),
                None,
            )
            if self.__entries and (signature == self.__last_signature or coalesced_index is not None):
                if coalesced_index is None:
                    coalesced_index = len(self.__entries) - 1
                last_entry = self.__entries[coalesced_index]
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
                self.__evict_to_budget()
                return "retained" if self.__entry_range_is_retained(self.__version) else "dropped"

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
            self.__evict_to_budget()
            return "retained" if self.__entry_range_is_retained(self.__version) else "dropped"

    def __entry_range_is_retained(self, version: int) -> bool:
        return any(
            int(entry.get("version", 0)) <= version <= int(
                entry.get("last_seen_version", entry.get("version", 0)),
            )
            for entry in self.__entries
        )

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

    def __evict_to_budget(self) -> None:
        while self.__entries and (
            self.__retained_bytes > self.__memory_budget_bytes
            or self.__max_entries is not None and len(self.__entries) > self.__max_entries
        ):
            protected = self.__protected_indices()
            candidate_indices = [index for index in range(len(self.__entries)) if index not in protected]
            if not candidate_indices:
                candidate_indices = list(range(len(self.__entries)))
            category_counts: Dict[str, int] = {}
            for index in candidate_indices:
                category = str(self.__entries[index].get("category") or "unknown")
                category_counts[category] = category_counts.get(category, 0) + 1
            # Prefer the oldest record from the noisiest eligible category.
            chosen_index = max(candidate_indices, key=lambda index: (
                category_counts[str(self.__entries[index].get("category") or "unknown")], -index,
            ))
            evicted = self.__entries[chosen_index]
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
        self.__retained_bytes = max(0, self.__retained_bytes)
        self.__last_signature = self.__signature(self.__entries[-1]) if self.__entries else None
        self.__refresh_failure_locked()

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
        requested_filters = dict(filters)
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
        for key, value in filters.items():
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

    def __sanitize_value(self, value: Any, key: Optional[str], depth: int) -> Any:
        if value is None:
            return None
        if key is not None and self.__is_sensitive_key(key):
            return "<redacted>"
        if depth >= self.__MAX_COLLECTION_DEPTH:
            return self.__truncate_string(self.__sanitize_string_content(str(value)))
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            if key is not None and self.__is_command_key(key):
                return "<redacted>"
            return self.__truncate_string(self.__sanitize_string_content(value))
        if isinstance(value, dict):
            sanitized: Dict[str, Any] = {}
            mapping = cast(Dict[object, object], value)
            for item_key, item_value in mapping.items():
                item_key_str = str(item_key)
                if self.__is_sensitive_key(item_key_str):
                    sanitized[item_key_str] = "<redacted>"
                else:
                    sanitized[item_key_str] = self.__sanitize_value(
                        item_value,
                        item_key_str,
                        depth + 1
                    )
            return sanitized
        if isinstance(value, (list, tuple, set)):
            if key is not None and self.__is_command_key(key):
                return "<redacted>"
            sanitized_list: List[Any] = []
            items = list(cast(Iterable[object], value))
            for index, item in enumerate(items):
                if index >= self.__MAX_LIST_ITEMS:
                    sanitized_list.append("<truncated>")
                    break
                sanitized_list.append(self.__sanitize_value(item, key, depth + 1))
            return sanitized_list
        return self.__truncate_string(self.__sanitize_string_content(str(value)))

    def __is_sensitive_key(self, key: str) -> bool:
        lowered = key.lower()
        if lowered in BreadcrumbTraceCollector.__SAFE_DIAGNOSTIC_KEYS:
            return False
        return any(
            keyword in lowered
            for keyword in BreadcrumbTraceCollector.__SENSITIVE_KEYWORDS
        )

    def __is_command_key(self, key: str) -> bool:
        lowered = key.lower()
        return any(keyword in lowered for keyword in BreadcrumbTraceCollector.__COMMAND_KEYWORDS)

    def __truncate_string(self, value: Any) -> Any:
        if value is None:
            return None
        value = str(value)
        if len(value) <= BreadcrumbTraceCollector.__MAX_DETAIL_STRING_LENGTH:
            return value
        return value[:BreadcrumbTraceCollector.__MAX_DETAIL_STRING_LENGTH] + "...<truncated>"

    def __sanitize_optional_string(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        return cast(str, self.__truncate_string(self.__sanitize_string_content(str(value))))

    def __sanitize_string_content(self, value: str) -> str:
        return redact_sensitive_text(value) or ""

    def __drain_external_records_if_due(self, limit: Optional[int]) -> None:
        if self.__external_records is None:
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
    ) -> None:
        self.__consume_ingress_rejections()
        if self.__external_records is None:
            self.__external_queue_last_drain_count = 0
            self.__external_queue_last_drain_limit = limit if limit is not None else self.__max_entries
            self.__external_queue_drain_limited = False
            return
        if force:
            self.__external_queue_last_drain_monotonic = time.monotonic()
        drained_count = 0
        drain_limited = False
        drain_limit = limit if limit is not None else self.__max_entries
        while limit is None or drained_count < limit:
            try:
                if wait_for_first_record and drained_count == 0:
                    external_record = self.__get_external_record(
                        wait_timeout=BreadcrumbTraceCollector.__EXTERNAL_QUEUE_FIRST_RECORD_WAIT_SECONDS
                    )
                else:
                    external_record = self.__get_external_record(wait_timeout=None)
            except queue.Empty:
                break
            drained_count += 1
            if not isinstance(external_record, dict):
                continue
            record_mapping = cast(Dict[str, Any], external_record)
            source = record_mapping.get("source")
            message = record_mapping.get("message")
            if not isinstance(source, str) or not isinstance(message, str):
                continue
            metadata_value = record_mapping.get("metadata", {})
            metadata: Dict[str, Any] = (
                cast(Dict[str, Any], metadata_value) if isinstance(metadata_value, dict) else {}
            )
            revision = record_mapping.get("policy_revision")
            epoch = record_mapping.get("policy_epoch")
            if isinstance(revision, int) and isinstance(epoch, int):
                with self.__lock:
                    if epoch == self.__policy_epoch:
                        self.__worker_policy_ack_revision = revision
                        self.__worker_policy_ack_epoch = epoch
            self.__record_entry(
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
                worker_policy_revision=revision,
                worker_policy_epoch=epoch,
                created_ns=record_mapping.get("created_ns"),
                created_ms=record_mapping.get("created_ms"),
            )
        if limit is not None and drained_count >= limit:
            try:
                drain_limited = not self.__external_records.empty()
            except Exception:
                drain_limited = True
        self.__external_queue_last_drain_count = drained_count
        self.__external_queue_last_drain_limit = drain_limit
        self.__external_queue_drain_limited = drain_limited

    def __get_external_record(self, wait_timeout: Optional[float]) -> object:
        external_records = self.__external_records
        if external_records is None:
            raise queue.Empty
        if wait_timeout is not None:
            get_method = getattr(external_records, "get", None)
            if callable(get_method):
                record = get_method(timeout=wait_timeout)
                return record
        return external_records.get_nowait()
