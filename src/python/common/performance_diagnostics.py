# Copyright 2026, SeedSync Contributors, All rights reserved.

"""Bounded, local-only resource diagnostics.

This module deliberately stores only a fixed set of numeric process and cgroup
metrics.  It is not a general event recorder: callers cannot attach labels,
paths, commands, payloads, or identifiers to a sample.
"""

from __future__ import annotations

import copy
import json
import math
import os
import platform
import time
from collections import deque
from threading import Lock
from typing import Any, Callable, Deque, Mapping, Optional


PERFORMANCE_DIAGNOSTICS_SCHEMA = "seedsync.performance-diagnostics.v1"
DEFAULT_RETENTION_DEPTH = 120
MAX_RETENTION_DEPTH = 240
DEFAULT_SAMPLE_INTERVAL_SECONDS = 5
MIN_SAMPLE_INTERVAL_SECONDS = 1
MAX_SAMPLE_INTERVAL_SECONDS = 3600
MAX_EXPORT_SAMPLES = 64
MAX_EXPORT_BYTES = 48 * 1024
DURATION_MODEL_UPDATE = "model_update"
DURATION_CONTROLLER_JOB = "controller_job"
DURATION_CONTROLLER_PROCESS = "controller_process"
DURATION_AUTO_QUEUE_PROCESS = "auto_queue_process"
DURATION_CONTROLLER_PROPAGATE_EXCEPTIONS = "controller_propagate_exceptions"
DURATION_CONTROLLER_CLEANUP_COMMANDS = "controller_cleanup_commands"
DURATION_CONTROLLER_PROCESS_COMMANDS = "controller_process_commands"
DURATION_CONTROLLER_CONFIGURATION = "controller_configuration"
DURATION_CONTROLLER_AUXILIARY_REAP = "controller_auxiliary_reap"
DURATION_CONTROLLER_DIAGNOSTICS = "controller_diagnostics"
DURATION_MODEL_BUILD = "model_build"
DURATION_MODEL_BUILDER_SET_LOCAL_FILES = "model_builder_set_local_files"
DURATION_MODEL_BUILDER_SET_REMOTE_FILES = "model_builder_set_remote_files"
DURATION_MODEL_BUILDER_SET_ACTIVE_FILES = "model_builder_set_active_files"
DURATION_MODEL_BUILDER_SET_LFTP_STATUSES = "model_builder_set_lftp_statuses"
DURATION_MODEL_BUILDER_SET_STOPPED_FILES = "model_builder_set_stopped_files"
DURATION_MODEL_UPDATE_STATE_PREPARATION = "model_update_state_preparation"
DURATION_MODEL_UPDATE_SCAN_INTAKE = "model_update_scan_intake"
DURATION_MODEL_UPDATE_STATUS_INGESTION = "model_update_status_ingestion"
DURATION_MODEL_UPDATE_BUILDER_SYNC = "model_update_builder_sync"
DURATION_MODEL_UPDATE_LIFECYCLE_MAINTENANCE = "model_update_lifecycle_maintenance"
DURATION_MODEL_UPDATE_BUILD_FINALIZATION = "model_update_build_finalization"
DURATION_MODEL_UPDATE_TRACE_SETUP = "model_update_trace_setup"
DURATION_MODEL_UPDATE_LOCK_WAIT = "model_update_lock_wait"
DURATION_MODEL_UPDATE_TRACE_FINALIZATION = "model_update_trace_finalization"
# Fixed model-rebuild trigger names.  These are intentionally closed-world
# labels: diagnostics must never accept caller-provided paths or identifiers.
MODEL_REBUILD_REASON_TERMINALIZABLE_COLLISION = "terminalizable_collision"
MODEL_REBUILD_REASON_MOVE_RETRY_DUE = "move_retry_due"
MODEL_REBUILD_REASON_COLLISION_RETRY = "collision_retry"
MODEL_REBUILD_REASON_DEFERRED_MOVE_PENDING = "deferred_move_pending"
# Fixed ModelBuilder cache-invalidation sources.  These labels intentionally
# carry no file, path-pair, or cardinality data.
MODEL_BUILDER_INVALIDATION_LOCAL_FILES = "model_builder_cache_invalidation_local_files"
MODEL_BUILDER_INVALIDATION_REMOTE_FILES = "model_builder_cache_invalidation_remote_files"
MODEL_BUILDER_INVALIDATION_ACTIVE_FILES = "model_builder_cache_invalidation_active_files"
MODEL_BUILDER_INVALIDATION_LFTP_STATUSES = "model_builder_cache_invalidation_lftp_statuses"
MODEL_BUILDER_INVALIDATION_UNKNOWN_LOCAL_PAIRS = "model_builder_cache_invalidation_unknown_local_pairs"
MODEL_BUILDER_INVALIDATION_LOCAL_ROOT_PATHS = "model_builder_cache_invalidation_local_root_paths"
MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES = "model_builder_cache_invalidation_downloaded_files"
MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS = "model_builder_cache_invalidation_downloaded_timestamps"
MODEL_BUILDER_INVALIDATION_EXTRACT_STATUSES = "model_builder_cache_invalidation_extract_statuses"
MODEL_BUILDER_INVALIDATION_EXTRACTED_FILES = "model_builder_cache_invalidation_extracted_files"
MODEL_BUILDER_INVALIDATION_STOPPED_FILES = "model_builder_cache_invalidation_stopped_files"
MODEL_BUILDER_INVALIDATION_MOVE_FAILED_FILES = "model_builder_cache_invalidation_move_failed_files"
MODEL_BUILDER_INVALIDATION_FINAL_MOVE_SUCCEEDED_FILES = "model_builder_cache_invalidation_final_move_succeeded_files"
MODEL_BUILDER_INVALIDATION_VALIDATION_STATUSES = "model_builder_cache_invalidation_validation_statuses"
MODEL_BUILDER_INVALIDATION_CLEAR = "model_builder_cache_invalidation_clear"
MODEL_BUILDER_INVALIDATION_EXPLICIT = "model_builder_cache_invalidation_explicit"
# Scanner stages intentionally use fixed names.  They are safe to expose in
# support snapshots because callers cannot supply labels, paths, or ids.
DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL = "local_scan_filesystem_traversal"
DURATION_LOCAL_SCAN_MANAGED_EXTRACT = "local_scan_managed_extract"
DURATION_LOCAL_SCAN_STAGING_MERGE = "local_scan_staging_merge"
DURATION_LOCAL_SCAN_AGGREGATION = "local_scan_aggregation"
DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION = "local_scan_progress_publication"
DURATION_REMOTE_SCAN_TRANSPORT_READ = "remote_scan_transport_read"
DURATION_REMOTE_SCAN_STREAM_PARSING = "remote_scan_stream_parsing"
DURATION_REMOTE_SCAN_AGGREGATION = "remote_scan_aggregation"
DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION = "remote_scan_progress_publication"

# Short aliases keep call sites readable while retaining one canonical wire
# name for each stage.
DURATION_LOCAL_SCAN_FILESYSTEM = DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL
DURATION_LOCAL_SCAN_STAGING = DURATION_LOCAL_SCAN_STAGING_MERGE
DURATION_LOCAL_SCAN_PROGRESS = DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION

_DURATION_METRICS_ORDER = (
    DURATION_MODEL_UPDATE, DURATION_CONTROLLER_JOB, DURATION_CONTROLLER_PROCESS, DURATION_AUTO_QUEUE_PROCESS,
    DURATION_CONTROLLER_PROPAGATE_EXCEPTIONS, DURATION_CONTROLLER_CLEANUP_COMMANDS,
    DURATION_CONTROLLER_PROCESS_COMMANDS, DURATION_CONTROLLER_CONFIGURATION,
    DURATION_CONTROLLER_AUXILIARY_REAP, DURATION_CONTROLLER_DIAGNOSTICS,
    DURATION_MODEL_BUILD, DURATION_MODEL_BUILDER_SET_LOCAL_FILES, DURATION_MODEL_BUILDER_SET_REMOTE_FILES,
    DURATION_MODEL_BUILDER_SET_ACTIVE_FILES, DURATION_MODEL_BUILDER_SET_LFTP_STATUSES,
    DURATION_MODEL_BUILDER_SET_STOPPED_FILES, DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL,
    DURATION_LOCAL_SCAN_MANAGED_EXTRACT,
    DURATION_LOCAL_SCAN_STAGING_MERGE, DURATION_LOCAL_SCAN_AGGREGATION,
    DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION, DURATION_REMOTE_SCAN_TRANSPORT_READ,
    DURATION_REMOTE_SCAN_STREAM_PARSING, DURATION_REMOTE_SCAN_AGGREGATION,
    DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION, DURATION_MODEL_UPDATE_STATE_PREPARATION,
    DURATION_MODEL_UPDATE_SCAN_INTAKE, DURATION_MODEL_UPDATE_STATUS_INGESTION,
    DURATION_MODEL_UPDATE_BUILDER_SYNC, DURATION_MODEL_UPDATE_LIFECYCLE_MAINTENANCE,
    DURATION_MODEL_UPDATE_BUILD_FINALIZATION, DURATION_MODEL_UPDATE_TRACE_SETUP,
    DURATION_MODEL_UPDATE_LOCK_WAIT, DURATION_MODEL_UPDATE_TRACE_FINALIZATION,
)

_ATTRIBUTION_GROUPS = {
    "controller_job": (
        DURATION_CONTROLLER_JOB,
        (DURATION_CONTROLLER_PROCESS, DURATION_AUTO_QUEUE_PROCESS),
    ),
    "controller_process": (
        DURATION_CONTROLLER_PROCESS,
        (
            DURATION_CONTROLLER_PROPAGATE_EXCEPTIONS,
            DURATION_CONTROLLER_CLEANUP_COMMANDS,
            DURATION_CONTROLLER_PROCESS_COMMANDS,
            DURATION_CONTROLLER_CONFIGURATION,
            DURATION_MODEL_UPDATE,
            DURATION_CONTROLLER_AUXILIARY_REAP,
            DURATION_CONTROLLER_DIAGNOSTICS,
        ),
    ),
    "model_update": (
        DURATION_MODEL_UPDATE,
        (
            DURATION_MODEL_UPDATE_TRACE_SETUP,
            DURATION_MODEL_UPDATE_LOCK_WAIT,
            DURATION_MODEL_UPDATE_STATE_PREPARATION,
            DURATION_MODEL_UPDATE_SCAN_INTAKE,
            DURATION_MODEL_UPDATE_STATUS_INGESTION,
            DURATION_MODEL_UPDATE_BUILDER_SYNC,
            DURATION_MODEL_UPDATE_LIFECYCLE_MAINTENANCE,
            DURATION_MODEL_UPDATE_BUILD_FINALIZATION,
            DURATION_MODEL_UPDATE_TRACE_FINALIZATION,
        ),
    ),
}
_SCANNER_DURATION_METRICS_ORDER = (
    DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, DURATION_LOCAL_SCAN_MANAGED_EXTRACT,
    DURATION_LOCAL_SCAN_STAGING_MERGE, DURATION_LOCAL_SCAN_AGGREGATION,
    DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION, DURATION_REMOTE_SCAN_TRANSPORT_READ,
    DURATION_REMOTE_SCAN_STREAM_PARSING, DURATION_REMOTE_SCAN_AGGREGATION,
    DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION,
)

_SAMPLE_FIELDS = frozenset((
    "process_cpu_percent_one_core", "cgroup_cpu_percent_one_core", "process_rss_bytes", "process_anon_bytes",
    "process_threads", "process_fds", "cgroup_cpu_usage_usec", "cgroup_memory_current_bytes",
    "cgroup_memory_anon_bytes", "cgroup_memory_file_bytes", "cgroup_memory_kernel_bytes",
))
_COUNTERS = frozenset((
    "samples_collected", "samples_dropped", "sampler_failures",
    "duration_spans_dropped",
    "model_full_snapshot_requests", "model_full_snapshot_listener_registrations",
    "model_scoped_snapshot_registrations",
    "model_summary_snapshot_registrations", "model_page_serializations", "model_summary_serializations",
    "model_rebuild_terminalizable_collision", "model_rebuild_move_retry_due",
    "model_rebuild_collision_retry", "model_rebuild_deferred_move_pending",
    "progressive_delta_publications", "progressive_delta_root_visits",
    "scan_priority_requests", "scan_priority_interrupts",
    "scan_priority_targeted_runs", "scan_priority_full_followups",
    "model_builder_cache_invalidation_local_files", "model_builder_cache_invalidation_remote_files",
    "model_builder_cache_invalidation_active_files", "model_builder_cache_invalidation_lftp_statuses",
    "model_builder_cache_invalidation_unknown_local_pairs",
    "model_builder_cache_invalidation_local_root_paths", "model_builder_cache_invalidation_downloaded_files",
    "model_builder_cache_invalidation_downloaded_timestamps", "model_builder_cache_invalidation_extract_statuses",
    "model_builder_cache_invalidation_extracted_files", "model_builder_cache_invalidation_stopped_files",
    "model_builder_cache_invalidation_move_failed_files", "model_builder_cache_invalidation_final_move_succeeded_files",
    "model_builder_cache_invalidation_validation_statuses", "model_builder_cache_invalidation_clear",
    "model_builder_cache_invalidation_explicit",
))
_GAUGES = frozenset((
    "model_root_count", "model_tree_file_count", "model_listener_count", "path_pair_count", "active_download_count",
    "active_extract_count", "active_command_count",
))
_DURATION_METRICS = frozenset(_DURATION_METRICS_ORDER)
_MAX_NUMERIC_VALUE = (1 << 63) - 1
_MAX_ACTIVE_DURATION_SPANS = 32


def _numeric(value: object) -> Optional[float | int]:
    if type(value) is int and 0 <= value <= _MAX_NUMERIC_VALUE:  # bool is intentionally excluded.
        return value
    if type(value) is float and math.isfinite(value) and 0 <= value <= _MAX_NUMERIC_VALUE:
        return value
    return None


def _derived(value: object, digits: int) -> Optional[float]:
    """Return a finite, JSON-safe derived diagnostic value."""
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or numeric < 0 or numeric > _MAX_NUMERIC_VALUE:
        return None
    rounded = round(numeric, digits)
    return rounded if math.isfinite(rounded) and rounded <= _MAX_NUMERIC_VALUE else None


class FixedDurationRecorder:
    """Child-only fixed-stage recorder with a pickle-safe aggregate snapshot."""

    def __init__(
        self,
        enabled: bool,
        monotonic_fn: Callable[[], float] = time.monotonic,
        thread_time_fn: Callable[[], float] = getattr(time, "thread_time", time.process_time),
    ) -> None:
        self.__enabled = type(enabled) is bool and enabled
        self.__monotonic = monotonic_fn
        self.__thread_time = thread_time_fn
        self.__lock = Lock()
        self.__active: dict[int, tuple[str, float, float]] = {}
        self.__next_token = 1
        self.__durations: dict[str, dict[str, float | int]] = {}

    def begin_duration(self, metric: str) -> Optional[tuple[float, float, int]]:
        if not self.__enabled or metric not in _DURATION_METRICS:
            return None
        try:
            wall_started = self.__monotonic()
            cpu_started = self.__thread_time()
            with self.__lock:
                token = self.__next_token
                self.__next_token += 1
                self.__active[token] = (metric, wall_started, cpu_started)
            return wall_started, cpu_started, token
        except Exception:
            return None

    def finish_duration(self, metric: str, started_at: object) -> None:
        if started_at is None:
            return
        try:
            started = tuple(started_at)  # type: ignore[arg-type]
            if len(started) < 3 or type(started[2]) is not int:
                return
            with self.__lock:
                active = self.__active.pop(started[2], None)
            if active is None or active[0] != metric:
                return
            wall_value = max(0.0, float(self.__monotonic() - started[0]))
            cpu_value = max(0.0, float(self.__thread_time() - started[1]))
            with self.__lock:
                aggregate = self.__durations.setdefault(metric, {
                    "count": 0,
                    "total_wall_seconds": 0.0,
                    "max_wall_seconds": 0.0,
                    "total_cpu_seconds": 0.0,
                    "cpu_observation_count": 0,
                })
                aggregate["count"] = int(aggregate["count"]) + 1
                aggregate["total_wall_seconds"] = float(aggregate["total_wall_seconds"]) + wall_value
                aggregate["max_wall_seconds"] = max(
                    float(aggregate["max_wall_seconds"]), wall_value,
                )
                aggregate["total_cpu_seconds"] = float(aggregate["total_cpu_seconds"]) + cpu_value
                aggregate["cpu_observation_count"] = int(aggregate["cpu_observation_count"]) + 1
        except Exception:
            return

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        with self.__lock:
            return {
                metric: dict(values)
                for metric, values in self.__durations.items()
                if metric in _DURATION_METRICS
            }


class ProcessContainerSampler:
    """Cheap best-effort sampler using procfs when it is available."""

    def __init__(
        self,
        monotonic_fn: Callable[[], float] = time.monotonic,
        process_time_fn: Callable[[], float] = time.process_time,
        platform_system: Callable[[], str] = platform.system,
        read_text: Optional[Callable[[str], str]] = None,
        listdir: Callable[[str], list[str]] = os.listdir,
    ) -> None:
        self.__monotonic = monotonic_fn
        self.__process_time = process_time_fn
        self.__platform_system = platform_system
        self.__read_text = read_text or self.__read_file
        self.__listdir = listdir
        self.__previous_cpu: Optional[tuple[float, float]] = None
        self.__previous_cgroup_cpu: Optional[tuple[float, int]] = None

    @staticmethod
    def __read_file(path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as source:
            return source.read()

    @staticmethod
    def _parse_kib_status(text: str) -> dict[str, int]:
        values: dict[str, int] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            number = value.strip().split(maxsplit=1)
            if number and number[0].isdigit():
                values[key] = int(number[0]) * 1024
        return values

    @staticmethod
    def _parse_cpu_stat(text: str) -> Optional[int]:
        for line in text.splitlines():
            key, _, value = line.partition(" ")
            if key == "usage_usec" and value.strip().isdigit():
                return int(value.strip())
        return None

    @staticmethod
    def _parse_memory_stat(text: str) -> dict[str, int]:
        values: dict[str, int] = {}
        for line in text.splitlines():
            key, _, value = line.partition(" ")
            if key in {"anon", "file", "kernel"} and value.strip().isdigit():
                values[key] = int(value.strip())
        return values

    def __cpu_percent(self, now: float) -> Optional[float]:
        cpu_time = self.__process_time()
        previous = self.__previous_cpu
        self.__previous_cpu = (now, cpu_time)
        if previous is None or now <= previous[0]:
            return None
        return round(max(0.0, (cpu_time - previous[1]) / (now - previous[0]) * 100.0), 3)

    def __cgroup_cpu_percent(self, now: float, usage_usec: Optional[int]) -> Optional[float]:
        if usage_usec is None:
            return None
        previous = self.__previous_cgroup_cpu
        self.__previous_cgroup_cpu = (now, usage_usec)
        if previous is None or now <= previous[0] or usage_usec < previous[1]:
            return None
        return round((usage_usec - previous[1]) / ((now - previous[0]) * 1_000_000) * 100.0, 3)

    def __cgroup_path(self) -> Optional[str]:
        try:
            for line in self.__read_text("/proc/self/cgroup").splitlines():
                if line.startswith("0::"):
                    relative = line.partition("::")[2].strip().lstrip("/")
                    return os.path.join("/sys/fs/cgroup", relative)
        except OSError:
            pass
        return None

    def sample(self) -> dict[str, Optional[float | int]]:
        now = self.__monotonic()
        sample: dict[str, Optional[float | int]] = {key: None for key in _SAMPLE_FIELDS}
        sample["process_cpu_percent_one_core"] = self.__cpu_percent(now)
        if self.__platform_system().lower() != "linux":
            return sample
        try:
            stat_text = self.__read_text("/proc/self/stat")
            # comm is parenthesized and may contain spaces; fixed proc field
            # indexes are only safe after the final closing parenthesis.
            stat = stat_text[stat_text.rfind(")") + 1:].split()
            if len(stat) > 21 and stat[21].isdigit():
                sysconf = getattr(os, "sysconf", None)
                page_size = sysconf("SC_PAGE_SIZE") if callable(sysconf) else 4096
                sample["process_rss_bytes"] = int(stat[21]) * page_size
            if len(stat) > 17 and stat[17].isdigit():
                sample["process_threads"] = int(stat[17])
        except (OSError, ValueError):
            pass
        try:
            status_text = self.__read_text("/proc/self/status")
            status = self._parse_kib_status(status_text)
            sample["process_anon_bytes"] = status.get("RssAnon")
            for line in status_text.splitlines():
                if line.startswith("Threads:") and line.partition(":")[2].strip().isdigit():
                    sample["process_threads"] = int(line.partition(":")[2].strip())
                    break
        except OSError:
            pass
        try:
            sample["process_fds"] = len(self.__listdir("/proc/self/fd"))
        except OSError:
            pass
        cgroup = self.__cgroup_path()
        if cgroup is None:
            return sample
        try:
            usage_usec = self._parse_cpu_stat(
                self.__read_text(os.path.join(cgroup, "cpu.stat"))
            )
            sample["cgroup_cpu_usage_usec"] = usage_usec
            sample["cgroup_cpu_percent_one_core"] = self.__cgroup_cpu_percent(now, usage_usec)
        except OSError:
            pass
        try:
            memory_current = self.__read_text(os.path.join(cgroup, "memory.current")).strip()
            sample["cgroup_memory_current_bytes"] = int(memory_current) if memory_current.isdigit() else None
        except OSError:
            pass
        try:
            memory = self._parse_memory_stat(self.__read_text(os.path.join(cgroup, "memory.stat")))
            sample["cgroup_memory_anon_bytes"] = memory.get("anon")
            sample["cgroup_memory_file_bytes"] = memory.get("file")
            sample["cgroup_memory_kernel_bytes"] = memory.get("kernel")
        except OSError:
            pass
        return sample


class PerformanceDiagnosticsCollector:
    """Thread-safe bounded collector for fixed numeric diagnostic data."""

    def __init__(
        self,
        enabled_getter: Callable[[], bool],
        retention_depth: int = DEFAULT_RETENTION_DEPTH,
        sample_interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
        sampler: Optional[ProcessContainerSampler] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        thread_time_fn: Callable[[], float] = getattr(time, "thread_time", time.process_time),
    ) -> None:
        if not 1 <= retention_depth <= MAX_RETENTION_DEPTH:
            raise ValueError("retention_depth is outside safe bounds")
        if not MIN_SAMPLE_INTERVAL_SECONDS <= sample_interval_seconds <= MAX_SAMPLE_INTERVAL_SECONDS:
            raise ValueError("sample_interval_seconds is outside safe bounds")
        self.__enabled_getter = enabled_getter
        self.__retention_depth = retention_depth
        self.__sample_interval_seconds = sample_interval_seconds
        self.__sampler = sampler or ProcessContainerSampler()
        self.__monotonic = monotonic_fn
        self.__thread_time = thread_time_fn
        self.__samples: Deque[dict[str, object]] = deque(maxlen=retention_depth)
        self.__lock = Lock()
        self.__sequence = 0
        self.__session = 1
        self.__session_start_sequence = 0
        self.__next_sample_at: Optional[float] = None
        self.__counters = {name: 0 for name in _COUNTERS}
        self.__current = {name: None for name in _SAMPLE_FIELDS}
        self.__peaks = {name: None for name in _SAMPLE_FIELDS}
        self.__gauges = {name: None for name in _GAUGES}
        self.__gauge_peaks = {name: None for name in _GAUGES}
        self.__durations: dict[str, dict[str, float | int]] = {}
        self.__window_started_at = monotonic_fn()
        self.__stage_window_current: dict[str, object] = {}
        self.__stage_window_peaks: dict[str, dict[str, Optional[float]]] = {
            metric: {"calls_per_second": None, "wall_time_percent": None, "cpu_percent_one_core": None}
            for metric in _DURATION_METRICS
        }
        # A bounded span table lets snapshots identify a stage that is still
        # running (for example, a local filesystem walk) without retaining any
        # caller-owned labels or objects.  Scanner work is capped at four
        # threads, but leave room for nested controller stages as well.
        self.__active_spans: dict[int, tuple[str, float, float, int]] = {}
        self.__active_counts = {metric: 0 for metric in _DURATION_METRICS}
        self.__next_span_id = 1
        self.__active_generation = 0
        self.__enabled_state: Optional[bool] = None

    @property
    def retention_depth(self) -> int:
        return self.__retention_depth

    def is_enabled(self) -> bool:
        try:
            enabled = bool(self.__enabled_getter())
        except Exception:
            enabled = False
        self.__observe_enabled_state(enabled)
        return enabled

    def __observe_enabled_state(self, enabled: bool) -> None:
        with self.__lock:
            previous = self.__enabled_state
            self.__enabled_state = enabled
            if previous is not None and previous != enabled:
                self.__active_generation += 1
                self.__retire_active_locked(increment_generation=False)

    def duration_worker_state(self) -> tuple[bool, int]:
        """Atomically observe enablement and capture a worker generation."""
        try:
            enabled = bool(self.__enabled_getter())
        except Exception:
            enabled = False
        self.__observe_enabled_state(enabled)
        with self.__lock:
            return enabled, self.__active_generation

    def duration_generation(self) -> int:
        """Return the current generation after observing the enable state."""
        return self.duration_worker_state()[1]

    def __clear_active_if_disabled(self, enabled: Optional[bool] = None) -> None:
        """Drop in-flight spans when diagnostics is hot-disabled.

        A span's ``finish_duration`` still safely accepts its old token after
        this cleanup, so toggling diagnostics cannot leave active counts stuck.
        """
        try:
            if enabled is None:
                enabled = self.is_enabled()
            if enabled:
                return
        except Exception:
            return
        with self.__lock:
            self.__retire_active_locked(increment_generation=False)

    def __retire_active_locked(self, *, increment_generation: bool = True) -> None:
        self.__active_spans.clear()
        for metric in self.__active_counts:
            self.__active_counts[metric] = 0
        # Invalidate begin/finish calls that captured a span before the
        # disable transition but have not yet committed it.
        if increment_generation:
            self.__active_generation += 1

    def increment(self, metric: str, count: int = 1) -> None:
        if not self.is_enabled() or metric not in _COUNTERS or type(count) is not int or count < 0:
            return
        with self.__lock:
            self.__counters[metric] += count

    def set_gauges(self, values: Mapping[str, object]) -> None:
        if not self.is_enabled():
            return
        with self.__lock:
            self.__set_gauges_locked(values)

    def __set_gauges_locked(self, values: Mapping[str, object]) -> dict[str, Optional[float | int]]:
        sanitized = {name: _numeric(values.get(name)) for name in _GAUGES}
        for metric, numeric in sanitized.items():
            if numeric is None:
                continue
            self.__gauges[metric] = numeric
            peak = self.__gauge_peaks[metric]
            if peak is None or numeric > peak:
                self.__gauge_peaks[metric] = numeric
        return sanitized

    def observe_duration(self, metric: str, wall_seconds: object, cpu_seconds: object = None) -> None:
        # Detailed callers use only code-defined metric names, never runtime labels.
        try:
            enabled = self.is_enabled()
            if not enabled or metric not in _DURATION_METRICS:
                return
            with self.__lock:
                generation = self.__active_generation
            wall_value = _numeric(wall_seconds)
            cpu_value = _numeric(cpu_seconds) if cpu_seconds is not None else None
            if wall_value is None:
                return
            with self.__lock:
                if generation != self.__active_generation or self.__enabled_state is not True:
                    return
                self.__observe_duration_locked(metric, wall_value, cpu_value)
        except Exception:
            return

    def observe_duration_aggregate(
        self, metric: str, aggregate: object, expected_generation: Optional[int] = None
    ) -> None:
        """Merge a bounded child-process aggregate into the current window."""
        try:
            enabled = self.is_enabled()
            if not enabled or metric not in _DURATION_METRICS or not isinstance(aggregate, Mapping) or \
                    (expected_generation is not None and type(expected_generation) is not int):
                return
            count = aggregate.get("count")
            total_wall = _numeric(aggregate.get("total_wall_seconds"))
            max_wall = _numeric(aggregate.get("max_wall_seconds"))
            total_cpu = _numeric(aggregate.get("total_cpu_seconds"))
            cpu_count = aggregate.get("cpu_observation_count")
            if type(count) is not int or count < 0 or total_wall is None or max_wall is None or \
                    max_wall < 0 or total_wall < 0 or max_wall > total_wall or \
                    type(cpu_count) is not int or cpu_count < 0 or \
                    cpu_count > count or (cpu_count and (total_cpu is None or total_cpu < 0)):
                return
            if count == 0:
                return
            with self.__lock:
                if self.__enabled_state is not True or \
                        (expected_generation is not None and expected_generation != self.__active_generation):
                    return
                current = self.__durations.setdefault(metric, {
                    "count": 0,
                    "total_wall_seconds": 0.0,
                    "max_wall_seconds": 0.0,
                    "total_cpu_seconds": 0.0,
                    "cpu_observation_count": 0,
                })
                current["count"] = int(current["count"]) + count
                current["total_wall_seconds"] = float(current["total_wall_seconds"]) + float(total_wall)
                current["max_wall_seconds"] = max(float(current["max_wall_seconds"]), float(max_wall))
                if cpu_count:
                    current["total_cpu_seconds"] = float(current["total_cpu_seconds"]) + float(total_cpu)
                    current["cpu_observation_count"] = int(current["cpu_observation_count"]) + cpu_count
        except Exception:
            return

    def __observe_duration_locked(
        self, metric: str, wall_value: float | int, cpu_value: Optional[float | int]
    ) -> None:
        aggregate = self.__durations.setdefault(metric, {
            "count": 0, "total_wall_seconds": 0.0, "max_wall_seconds": 0.0,
            "total_cpu_seconds": 0.0, "cpu_observation_count": 0,
        })
        aggregate["count"] = int(aggregate["count"]) + 1
        aggregate["total_wall_seconds"] = float(aggregate["total_wall_seconds"]) + float(wall_value)
        aggregate["max_wall_seconds"] = max(float(aggregate["max_wall_seconds"]), float(wall_value))
        if cpu_value is not None:
            aggregate["total_cpu_seconds"] = float(aggregate["total_cpu_seconds"]) + float(cpu_value)
            aggregate["cpu_observation_count"] = int(aggregate["cpu_observation_count"]) + 1

    def begin_duration(self, metric: str) -> Optional[tuple[float, float, int, int]]:
        """Start one fixed-name timing span, with a disabled fast path."""
        try:
            enabled = self.is_enabled()
            if not enabled or metric not in _DURATION_METRICS:
                return None
            # Capture the generation before potentially blocking clock calls.
            # Reset/disable increments it while holding the same lock, so a
            # paused begin cannot insert a pre-reset span into the new state.
            with self.__lock:
                generation = self.__active_generation
            wall_started = self.__monotonic()
            cpu_started = self.__thread_time()
            with self.__lock:
                if generation != self.__active_generation:
                    return None
                if self.__enabled_state is not True:
                    self.__retire_active_locked(increment_generation=False)
                    return None
                if len(self.__active_spans) >= _MAX_ACTIVE_DURATION_SPANS:
                    self.__counters["duration_spans_dropped"] += 1
                    return None
                span_id = self.__next_span_id
                self.__next_span_id += 1
                if self.__next_span_id > _MAX_NUMERIC_VALUE:
                    self.__next_span_id = 1
                self.__active_spans[span_id] = (metric, wall_started, cpu_started, generation)
                self.__active_counts[metric] += 1
            # The span id and generation are opaque numeric tokens used only
            # for lifecycle cleanup; neither is exported.
            return wall_started, cpu_started, span_id, generation
        except Exception:
            return None

    def finish_duration(self, metric: str, started_at: Optional[tuple[float, ...]]) -> None:
        if started_at is None:
            return
        try:
            enabled_before = self.is_enabled()
            self.__clear_active_if_disabled(enabled_before)
            span_id = started_at[2] if len(started_at) >= 3 else None
            if type(span_id) is int:
                with self.__lock:
                    active = self.__active_spans.get(span_id)
                    if active is None:
                        return
                    active_generation = active[3]
                wall_value = _numeric(self.__monotonic() - started_at[0])
                cpu_value = _numeric(self.__thread_time() - started_at[1])
                enabled_now = self.is_enabled()
                with self.__lock:
                    # Reset/disable may have retired the span while clocks
                    # were being sampled.  Only the original generation may
                    # decrement counts or append a duration.
                    active = self.__active_spans.pop(span_id, None)
                    if active is None or active[3] != active_generation or \
                            active_generation != self.__active_generation:
                        return
                    active_metric = active[0]
                    self.__active_counts[active_metric] = max(0, self.__active_counts[active_metric] - 1)
                    if metric != active_metric:
                        return
                    if not enabled_now or self.__enabled_state is not True:
                        self.__retire_active_locked(increment_generation=False)
                        return
                    if wall_value is not None and enabled_now:
                        self.__observe_duration_locked(metric, wall_value, cpu_value)
                return
            if metric not in _DURATION_METRICS:
                return
            enabled = self.is_enabled()
            if not enabled:
                return
            with self.__lock:
                generation = self.__active_generation
            wall_value = _numeric(self.__monotonic() - started_at[0])
            cpu_value = _numeric(self.__thread_time() - started_at[1])
            enabled_after = self.is_enabled()
            if wall_value is None or not enabled_after:
                return
            with self.__lock:
                if generation != self.__active_generation or self.__enabled_state is not True:
                    return
                self.__observe_duration_locked(metric, wall_value, cpu_value)
        except Exception:
            return

    def sample_if_due(self, gauge_supplier: Optional[Callable[[], Mapping[str, object]]] = None) -> bool:
        # Diagnostics are opt-in: disabled mode does not touch procfs/cgroups or
        # allocate/retain samples.  The gate is only a boolean getter on the
        # controller hot path.
        try:
            enabled = self.is_enabled()
            if not enabled:
                return False
            now = self.__monotonic()
            with self.__lock:
                if self.__next_sample_at is not None and now < self.__next_sample_at:
                    return False
                self.__next_sample_at = now + self.__sample_interval_seconds
            sample = self.__sampler.sample()
            if not isinstance(sample, Mapping):
                raise TypeError("diagnostic sampler returned a non-mapping")
            gauges: Mapping[str, object] = {}
            if gauge_supplier is not None:
                try:
                    gauges = gauge_supplier()
                except Exception:
                    gauges = {}
            self.record_sample(sample, gauges=gauges, close_window_at=now)
            return True
        except Exception:
            try:
                self.increment("sampler_failures")
            except Exception:
                pass
            return False

    def record_sample(
        self, values: Mapping[str, object], *, gauges: Optional[Mapping[str, object]] = None,
        close_window_at: Optional[float] = None,
    ) -> None:
        enabled = self.is_enabled()
        if not enabled:
            return
        sanitized = {name: _numeric(values.get(name)) for name in _SAMPLE_FIELDS}
        with self.__lock:
            if len(self.__samples) == self.__retention_depth:
                self.__counters["samples_dropped"] += 1
            self.__sequence += 1
            record: dict[str, object] = {"sequence": self.__sequence, **sanitized}
            if gauges is not None:
                record["gauges"] = self.__set_gauges_locked(gauges)
            if close_window_at is not None:
                record["stage_window"] = self.__close_duration_window_locked(close_window_at)
            self.__samples.append(record)
            self.__counters["samples_collected"] += 1
            self.__current.update(sanitized)
            for name, value in sanitized.items():
                peak = self.__peaks[name]
                if value is not None and (peak is None or value > peak):
                    self.__peaks[name] = value

    def __close_duration_window_locked(self, closed_at: float) -> dict[str, object]:
        elapsed = max(0.0, closed_at - self.__window_started_at)
        window: dict[str, object] = {}
        for metric in _DURATION_METRICS_ORDER:
            aggregate = self.__durations.get(metric, {
                "count": 0, "total_wall_seconds": 0.0, "max_wall_seconds": 0.0,
                "total_cpu_seconds": 0.0, "cpu_observation_count": 0,
            })
            count = int(aggregate["count"])
            total_wall = float(aggregate["total_wall_seconds"])
            total_cpu = float(aggregate["total_cpu_seconds"])
            cpu_observation_count = int(aggregate["cpu_observation_count"])
            window[metric] = {
                "count": count,
                "total_wall_seconds": _derived(total_wall, 6),
                "max_wall_seconds": _derived(float(aggregate["max_wall_seconds"]), 6),
                "average_wall_seconds": _derived(total_wall / count, 6) if count else None,
                "total_cpu_seconds": _derived(total_cpu, 6) if cpu_observation_count else None,
                "calls_per_second": _derived(count / elapsed, 6) if elapsed > 0 else None,
                "wall_time_percent": _derived(total_wall / elapsed * 100.0, 3) if elapsed > 0 else None,
                "cpu_percent_one_core": _derived(total_cpu / elapsed * 100.0, 3)
                if elapsed > 0 and cpu_observation_count else None,
            }
        closed_window = {"elapsed_seconds": _derived(elapsed, 6), "metrics": window}
        attribution: dict[str, object] = {}
        for name, (parent_name, child_names) in _ATTRIBUTION_GROUPS.items():
            parent_cpu = window[parent_name]["total_cpu_seconds"]
            child_cpu_values = [window[child_name]["total_cpu_seconds"] for child_name in child_names]
            named_cpu = sum(float(value) for value in child_cpu_values if type(value) in (int, float))
            if type(parent_cpu) not in (int, float):
                attribution[name] = {
                    "parent_cpu_seconds": None,
                    "named_cpu_seconds": _derived(named_cpu, 6),
                    "unattributed_cpu_seconds": None,
                    "coverage_percent": None,
                }
                continue
            parent_value = float(parent_cpu)
            unattributed_cpu = max(0.0, parent_value - named_cpu)
            coverage = min(100.0, named_cpu / parent_value * 100.0) if parent_value > 0 else \
                (100.0 if named_cpu == 0 else None)
            attribution[name] = {
                "parent_cpu_seconds": _derived(parent_value, 6),
                "named_cpu_seconds": _derived(named_cpu, 6),
                "unattributed_cpu_seconds": _derived(unattributed_cpu, 6),
                "coverage_percent": _derived(coverage, 3) if coverage is not None else None,
            }
        closed_window["attribution"] = attribution
        self.__stage_window_current = copy.deepcopy(closed_window)
        for metric, values in window.items():
            if not isinstance(values, dict):
                continue
            for name in ("calls_per_second", "wall_time_percent", "cpu_percent_one_core"):
                value = values.get(name)
                if type(value) not in (int, float):
                    continue
                peak = self.__stage_window_peaks[metric][name]
                if peak is None or value > peak:
                    self.__stage_window_peaks[metric][name] = float(value)
        self.__durations.clear()
        self.__window_started_at = closed_at
        return closed_window

    def reset(self) -> None:
        with self.__lock:
            self.__samples.clear()
            self.__session += 1
            self.__session_start_sequence = self.__sequence
            self.__current = {name: None for name in _SAMPLE_FIELDS}
            self.__peaks = {name: None for name in _SAMPLE_FIELDS}
            self.__gauges = {name: None for name in _GAUGES}
            self.__gauge_peaks = {name: None for name in _GAUGES}
            self.__durations.clear()
            self.__stage_window_current = {}
            self.__stage_window_peaks = {
                metric: {"calls_per_second": None, "wall_time_percent": None, "cpu_percent_one_core": None}
                for metric in _DURATION_METRICS
            }
            self.__counters = {name: 0 for name in _COUNTERS}
            self.__next_sample_at = None
            self.__window_started_at = self.__monotonic()
            self.__active_spans.clear()
            self.__active_counts = {metric: 0 for metric in _DURATION_METRICS}
            self.__active_generation += 1

    def __active_stage_snapshot_locked(
        self, now: float
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        """Return bounded per-stage activity and the longest active stage."""
        active_stages: dict[str, object] = {}
        longest_name: Optional[str] = None
        longest_wall = 0.0
        longest_count = 0
        for metric in _DURATION_METRICS_ORDER:
            count = self.__active_counts[metric]
            max_wall = 0.0
            for active_metric, wall_started, _cpu_started, _generation in self.__active_spans.values():
                if active_metric != metric:
                    continue
                max_wall = max(max_wall, max(0.0, now - wall_started))
            active_stages[metric] = {
                "count": count,
                "max_wall_seconds": _derived(max_wall, 6),
                # ``thread_time`` is scoped to the calling thread.  A
                # snapshot is commonly requested from the controller thread
                # while a scanner stage runs on another thread, so active CPU
                # cannot be inferred without an OS-specific per-thread clock.
                "max_cpu_seconds": None,
            }
            if count > 0 and (longest_name is None or max_wall > longest_wall):
                longest_name = metric
                longest_wall = max_wall
                longest_count = count
        active_stage: dict[str, object] = {
            "name": longest_name,
            "count": longest_count,
            "wall_seconds": _derived(longest_wall, 6),
            "cpu_seconds": None,
        }
        scanner_name: Optional[str] = None
        scanner_wall = 0.0
        scanner_count = 0
        for metric in _SCANNER_DURATION_METRICS_ORDER:
            values = active_stages[metric]
            if not isinstance(values, dict):
                continue
            count = values.get("count")
            wall = values.get("max_wall_seconds")
            if type(count) is not int or count <= 0 or type(wall) not in (int, float):
                continue
            if scanner_name is None or float(wall) > scanner_wall:
                scanner_name = metric
                scanner_wall = float(wall)
                scanner_count = count
        active_scanner_stage: dict[str, object] = {
            "name": scanner_name,
            "count": scanner_count,
            "wall_seconds": _derived(scanner_wall, 6),
            "cpu_seconds": None,
        }
        return active_stages, active_stage, active_scanner_stage

    def snapshot(self, since_sequence: Optional[int] = None, limit: Optional[int] = None) -> dict[str, object]:
        bounded_limit = self.__retention_depth if limit is None else min(max(1, limit), self.__retention_depth)
        enabled = self.is_enabled()
        self.__clear_active_if_disabled(enabled)
        with self.__lock:
            samples = [sample for sample in self.__samples if since_sequence is None or int(sample["sequence"]) > since_sequence]
            truncated = len(samples) > bounded_limit
            samples = samples[-bounded_limit:]
            durations = copy.deepcopy(self.__durations)
            now = self.__monotonic()
            window_elapsed_seconds = max(0.0, now - self.__window_started_at)
            active_stages, active_stage, active_scanner_stage = self.__active_stage_snapshot_locked(now)
            for aggregate in durations.values():
                total_wall_seconds = float(aggregate["total_wall_seconds"])
                aggregate["average_wall_seconds"] = _derived(total_wall_seconds / int(aggregate["count"]), 6) \
                    if int(aggregate["count"]) else None
                aggregate["in_progress_calls_per_second"] = _derived(
                    int(aggregate["count"]) / window_elapsed_seconds, 6
                ) if window_elapsed_seconds > 0 else None
                aggregate["in_progress_wall_time_percent"] = _derived(
                    total_wall_seconds / window_elapsed_seconds * 100.0, 3
                ) if window_elapsed_seconds > 0 else None
                aggregate["in_progress_cpu_percent_one_core"] = _derived(
                    float(aggregate["total_cpu_seconds"]) / window_elapsed_seconds * 100.0, 3
                ) if window_elapsed_seconds > 0 and int(aggregate["cpu_observation_count"]) else None
            return {
                "schema": PERFORMANCE_DIAGNOSTICS_SCHEMA,
                "enabled": enabled,
                "session": self.__session,
                "session_start_sequence": self.__session_start_sequence,
                "sequence": self.__sequence,
                "since_sequence": since_sequence,
                "retention_depth": self.__retention_depth,
                "sample_interval_seconds": self.__sample_interval_seconds,
                "window_elapsed_seconds": _derived(window_elapsed_seconds, 6),
                "sample_count": len(samples),
                "truncated": truncated,
                "counters": copy.deepcopy(self.__counters),
                "current": copy.deepcopy(self.__current),
                "peaks": copy.deepcopy(self.__peaks),
                "gauges": copy.deepcopy(self.__gauges),
                "gauge_peaks": copy.deepcopy(self.__gauge_peaks),
                "durations": durations,
                "active_stage_counts": copy.deepcopy(self.__active_counts),
                "active_stages": active_stages,
                "active_stage": active_stage,
                "active_scanner_stage": active_scanner_stage,
                "stage_window_current": copy.deepcopy(self.__stage_window_current),
                "stage_window_peaks": copy.deepcopy(self.__stage_window_peaks),
                "samples": copy.deepcopy(samples),
            }

    def export_snapshot(self) -> dict[str, object]:
        snapshot = self.snapshot(limit=MAX_EXPORT_SAMPLES)
        # Numeric-only payload is intentionally small. Keep an explicit cap even if
        # future schema expansion adds fields.
        snapshot["export_max_bytes"] = MAX_EXPORT_BYTES
        snapshot["export_max_samples"] = MAX_EXPORT_SAMPLES
        try:
            samples = snapshot["samples"]
            if isinstance(samples, list):
                for drop_count in range(len(samples) + 1):
                    snapshot["samples"] = samples[drop_count:]
                    snapshot["sample_count"] = len(snapshot["samples"])
                    snapshot["truncated"] = bool(snapshot["truncated"]) or drop_count > 0
                    encoded = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
                    if len(encoded.encode("utf-8")) <= MAX_EXPORT_BYTES:
                        return snapshot
        except (TypeError, ValueError, OverflowError):
            pass
        # Future schema additions must not make the support endpoint unbounded.
        return {
            "schema": PERFORMANCE_DIAGNOSTICS_SCHEMA,
            "session": snapshot["session"],
            "sequence": snapshot["sequence"],
            "truncated": True,
            "export_max_bytes": MAX_EXPORT_BYTES,
            "export_max_samples": MAX_EXPORT_SAMPLES,
        }
