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
DURATION_MODEL_BUILD = "model_build"

_SAMPLE_FIELDS = frozenset((
    "process_cpu_percent_one_core", "cgroup_cpu_percent_one_core", "process_rss_bytes", "process_anon_bytes",
    "process_threads", "process_fds", "cgroup_cpu_usage_usec", "cgroup_memory_current_bytes",
    "cgroup_memory_anon_bytes", "cgroup_memory_file_bytes", "cgroup_memory_kernel_bytes",
))
_COUNTERS = frozenset((
    "samples_collected", "samples_dropped", "sampler_failures",
    "model_full_snapshot_requests", "model_full_snapshot_listener_registrations",
    "model_scoped_snapshot_registrations",
    "model_summary_snapshot_registrations", "model_page_serializations", "model_summary_serializations",
))
_GAUGES = frozenset((
    "model_root_count", "model_tree_file_count", "model_listener_count", "path_pair_count", "active_download_count",
    "active_extract_count", "active_command_count",
))
_DURATION_METRICS = frozenset((
    DURATION_MODEL_UPDATE, DURATION_CONTROLLER_JOB, DURATION_CONTROLLER_PROCESS, DURATION_AUTO_QUEUE_PROCESS,
    DURATION_MODEL_BUILD,
))
_MAX_NUMERIC_VALUE = (1 << 63) - 1


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

    @property
    def retention_depth(self) -> int:
        return self.__retention_depth

    def is_enabled(self) -> bool:
        try:
            return bool(self.__enabled_getter())
        except Exception:
            return False

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
            if not self.is_enabled() or metric not in _DURATION_METRICS:
                return
            wall_value = _numeric(wall_seconds)
            cpu_value = _numeric(cpu_seconds) if cpu_seconds is not None else None
            if wall_value is None:
                return
            with self.__lock:
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
        except Exception:
            return

    def begin_duration(self, metric: str) -> Optional[tuple[float, float]]:
        """Start one fixed-name timing span, with a disabled fast path."""
        try:
            if not self.is_enabled() or metric not in _DURATION_METRICS:
                return None
            return self.__monotonic(), self.__thread_time()
        except Exception:
            return None

    def finish_duration(self, metric: str, started_at: Optional[tuple[float, float]]) -> None:
        if started_at is None:
            return
        try:
            self.observe_duration(
                metric, self.__monotonic() - started_at[0], self.__thread_time() - started_at[1]
            )
        except Exception:
            return

    def sample_if_due(self, gauge_supplier: Optional[Callable[[], Mapping[str, object]]] = None) -> bool:
        # Diagnostics are opt-in: disabled mode does not touch procfs/cgroups or
        # allocate/retain samples.  The gate is only a boolean getter on the
        # controller hot path.
        try:
            if not self.is_enabled():
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
        if not self.is_enabled():
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
        for metric in _DURATION_METRICS:
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

    def snapshot(self, since_sequence: Optional[int] = None, limit: Optional[int] = None) -> dict[str, object]:
        bounded_limit = self.__retention_depth if limit is None else min(max(1, limit), self.__retention_depth)
        with self.__lock:
            samples = [sample for sample in self.__samples if since_sequence is None or int(sample["sequence"]) > since_sequence]
            truncated = len(samples) > bounded_limit
            samples = samples[-bounded_limit:]
            durations = copy.deepcopy(self.__durations)
            window_elapsed_seconds = max(0.0, self.__monotonic() - self.__window_started_at)
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
                "enabled": self.is_enabled(),
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
