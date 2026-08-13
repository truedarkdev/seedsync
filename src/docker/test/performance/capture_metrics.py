#!/usr/bin/env python3
"""Validate and summarize a bounded diagnostics snapshot for the lab.

The summary intentionally treats a high-cost scan followed by quiet idle as a
different outcome from a process that stays busy after the scan.  Baselines
are accepted only when the retained fixture reaches its expected model size,
has several consecutive high-CPU samples, and attributes CPU to at least one
of the fixed scanner/model-update stages without dropped duration spans.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable


FIXED_METRICS = (
    "local_scan_filesystem_traversal", "local_scan_managed_extract", "local_scan_staging_merge",
    "local_scan_aggregation", "local_scan_progress_publication", "model_update_state_preparation",
    "model_builder_set_local_files", "model_builder_set_remote_files", "model_builder_set_active_files",
    "model_builder_set_lftp_statuses", "model_builder_set_stopped_files",
    "remote_scan_transport_read", "remote_scan_stream_parsing", "remote_scan_aggregation",
    "remote_scan_progress_publication",
    "model_update_scan_intake", "model_update_status_ingestion", "model_update_builder_sync",
    "model_update_lifecycle_maintenance", "model_update_build_finalization",
)
MODEL_BUILDER_INVALIDATION_COUNTERS = frozenset((
    "model_builder_cache_invalidation_local_files",
    "model_builder_cache_invalidation_remote_files",
    "model_builder_cache_invalidation_active_files",
    "model_builder_cache_invalidation_lftp_statuses",
    "model_builder_cache_invalidation_unknown_local_pairs",
    "model_builder_cache_invalidation_local_root_paths",
    "model_builder_cache_invalidation_downloaded_files",
    "model_builder_cache_invalidation_downloaded_timestamps",
    "model_builder_cache_invalidation_extract_statuses",
    "model_builder_cache_invalidation_extracted_files",
    "model_builder_cache_invalidation_stopped_files",
    "model_builder_cache_invalidation_move_failed_files",
    "model_builder_cache_invalidation_final_move_succeeded_files",
    "model_builder_cache_invalidation_validation_statuses",
    "model_builder_cache_invalidation_clear",
    "model_builder_cache_invalidation_explicit",
))
DEFAULT_SETTLED_IDLE_CPU_PERCENT = 1.0
DEFAULT_EXTERNAL_READINESS_SAMPLES = 3
DEFAULT_EXTERNAL_MODEL_STABILITY_SAMPLES = 2
DEFAULT_EXTERNAL_POST_TARGET_OBSERVATION_SECONDS = 150


def _number(value: object) -> float | None:
    return float(value) if type(value) in (int, float) else None


def _sample_cpu(sample: dict[str, object]) -> float:
    values = (_number(sample.get("process_cpu_percent_one_core")),
              _number(sample.get("cgroup_cpu_percent_one_core")))
    return max((value for value in values if value is not None), default=0.0)


def _sample_model_file_count(sample: dict[str, object]) -> int | None:
    gauges = sample.get("gauges")
    if not isinstance(gauges, dict):
        return None
    value = gauges.get("model_tree_file_count")
    return int(value) if type(value) is int else None


def percentile(values: Iterable[float], percentile_rank: float = 0.95) -> float | None:
    numbers = sorted(float(value) for value in values if type(value) in (int, float) and value >= 0)
    if not numbers:
        return None
    index = max(0, min(len(numbers) - 1, int(len(numbers) * percentile_rank + 0.999999) - 1))
    return round(numbers[index], 3)


def latest_model_status(
    diagnostics: dict[str, object],
) -> tuple[int | None, int | None, int | None]:
    """Return the latest sample's model cardinality, sequence, and build count.

    The performance lab uses this single status snapshot when deciding whether
    startup has reached a settled model.  Missing or malformed values remain
    ``None`` so a cardinality-only sample cannot be mistaken for a zero-build
    settled boundary.
    """
    samples = diagnostics.get("samples")
    if not isinstance(samples, list) or not samples or not isinstance(samples[-1], dict):
        return None, None, None
    latest = samples[-1]

    gauges = latest.get("gauges")
    model_count = gauges.get("model_tree_file_count") if isinstance(gauges, dict) else None
    model_count = model_count if type(model_count) is int and model_count >= 0 else None
    sequence = latest.get("sequence")
    sequence_value = sequence if type(sequence) is int and sequence >= 0 else None
    stage_window = latest.get("stage_window")
    metrics = stage_window.get("metrics") if isinstance(stage_window, dict) else None
    model_build = metrics.get("model_build") if isinstance(metrics, dict) else None
    build_count = model_build.get("count") if isinstance(model_build, dict) else None
    build_count_value = build_count if type(build_count) is int and build_count >= 0 else None
    return model_count, sequence_value, build_count_value


def latest_model_summary_status(summary: dict[str, object]) -> dict[str, int | None]:
    """Reduce the authenticated model summary to bounded readiness fields.

    The summary endpoint intentionally exposes only root-level aggregates.  We
    retain no pair names, IDs, paths, or file records; ``root_count`` is the
    cardinality used by the diagnostics-off readiness loop and ``model_version``
    is the stability boundary.
    """
    if not isinstance(summary, dict):
        return {"root_count": None, "model_version": None, "pair_count": None,
                "active_count": None, "queued_count": None, "completed_count": None}
    version = summary.get("model_version")
    version = version if type(version) is int and version >= 0 else None
    pairs = summary.get("path_pairs")
    if not isinstance(pairs, list):
        return {"root_count": None, "model_version": version, "pair_count": None,
                "active_count": None, "queued_count": None, "completed_count": None}
    totals = {"root_count": 0, "active_count": 0, "queued_count": 0, "completed_count": 0}
    for pair in pairs:
        if not isinstance(pair, dict):
            return {"root_count": None, "model_version": version, "pair_count": None,
                    "active_count": None, "queued_count": None, "completed_count": None}
        for field in totals:
            value = pair.get(field)
            if type(value) is not int or value < 0:
                return {"root_count": None, "model_version": version, "pair_count": None,
                        "active_count": None, "queued_count": None, "completed_count": None}
            totals[field] += value
    return {**totals, "model_version": version, "pair_count": len(pairs)}


def summarize_docker_stats(
    samples: list[dict[str, object]], role: str | None = None,
) -> dict[str, object]:
    """Summarize sanitized external Docker CPU/memory samples."""
    valid = []
    for sample in samples if isinstance(samples, list) else []:
        if not isinstance(sample, dict):
            continue
        cpu, memory, at_ms = sample.get("cpu_percent"), sample.get("memory_percent"), sample.get("t_epoch_ms")
        if type(cpu) not in (int, float) or cpu < 0 or type(memory) not in (int, float) or memory < 0:
            continue
        if type(at_ms) is not int or at_ms < 0:
            continue
        valid.append({"cpu_percent": float(cpu), "memory_percent": float(memory), "t_epoch_ms": at_ms})
    cpus = [item["cpu_percent"] for item in valid]
    memories = [item["memory_percent"] for item in valid]
    average_cpu = (sum(cpus) / len(cpus)) if cpus else None
    average_memory = (sum(memories) / len(memories)) if memories else None
    return {
        "sample_count": len(valid),
        "role": role,
        "average_cpu_percent_one_core": average_cpu,
        "peak_cpu_percent_one_core": max(cpus, default=None),
        "average_memory_percent": average_memory,
        "cpu_percent_p95": percentile(cpus),
        "cpu_percent_max": max(cpus, default=None),
        "memory_percent_p95": percentile(memories),
        "memory_percent_max": max(memories, default=None),
        "first_epoch_ms": valid[0]["t_epoch_ms"] if valid else None,
        "last_epoch_ms": valid[-1]["t_epoch_ms"] if valid else None,
    }


def _expected_summary_root_count(manifest: dict[str, object]) -> int:
    """Derive the synthetic fixture's compact-summary root cardinality.

    Fixture buckets contain 2,048 files per first-level root.  Remote-only
    targets seeded directly beneath the pair root add one compact root.
    """
    pairs = manifest.get("path_pairs") if isinstance(manifest, dict) else None
    if not isinstance(pairs, list):
        return 0
    total = 0
    for pair in pairs:
        if not isinstance(pair, dict) or pair.get("enabled") is False:
            continue
        nodes = pair.get("nodes_local")
        if type(nodes) is not int or nodes < 0:
            continue
        total += (nodes + 2047) // 2048
        directory = pair.get("directory")
        for target in pair.get("remote_only_targets", []):
            if not isinstance(target, dict):
                continue
            parts = [part for part in str(target.get("relative_path", "")).split("/") if part]
            if parts and parts[0] == directory:
                parts = parts[1:]
            if len(parts) == 1:
                total += 1
    return total


def _external_target_readiness(
    model_samples: list[dict[str, object]], expected: int,
    target_index: int | None, settled_idle_cpu_percent: float,
) -> dict[str, object]:
    """Validate the bounded diagnostics-off target boundary.

    The shell harness records only compact model-summary fields and the latest
    sanitized app CPU sample.  This check deliberately requires both a stable
    root cardinality/model version and a separate consecutive quiet CPU suffix
    before the target.  A later model change therefore cannot be hidden by an
    earlier quiet sample.
    """
    readiness: dict[str, object] = {
        "condition": (
            "target requires the expected root cardinality and model version "
            "for at least 2 consecutive successful summaries plus 3 "
            "consecutive app CPU samples at or below the hard 1.0% gate; "
            "readiness resets on cardinality/version change"
        ),
        "target_sample_index": target_index,
        "target_sample_index_valid": False,
        "model_cardinality_at_target": None,
        "model_version_at_target": None,
        "model_stable_consecutive_samples": 0,
        "required_model_stable_samples": DEFAULT_EXTERNAL_MODEL_STABILITY_SAMPLES,
        "app_cpu_quiet_consecutive_samples": 0,
        "required_app_cpu_quiet_samples": DEFAULT_EXTERNAL_READINESS_SAMPLES,
        "app_cpu_threshold_percent": settled_idle_cpu_percent,
        "reset_on_model_change": True,
        "model_stable_after_target": True,
        "ready": False,
    }
    if target_index is None or not 0 <= target_index < len(model_samples):
        return readiness
    target = model_samples[target_index]
    if not isinstance(target, dict):
        return readiness
    sample_index = target.get("sample_index")
    target_root = target.get("root_count")
    target_version = target.get("model_version")
    readiness["target_sample_index_valid"] = type(sample_index) is int and sample_index == target_index
    readiness["model_cardinality_at_target"] = target_root
    readiness["model_version_at_target"] = target_version
    if not readiness["target_sample_index_valid"] or target_index == 0:
        return readiness
    if type(target_root) is not int or target_root != expected:
        return readiness
    if type(target_version) is not int or target_version < 0:
        return readiness
    for sample in model_samples[target_index + 1:]:
        if (not isinstance(sample, dict) or sample.get("root_count") != target_root or
                sample.get("model_version") != target_version):
            readiness["model_stable_after_target"] = False
            return readiness

    model_streak = 0
    quiet_streak = 0
    quiet_suffix_open = True
    previous_index: int | None = None
    for position in range(target_index, -1, -1):
        sample = model_samples[position]
        if not isinstance(sample, dict):
            break
        sample_id = sample.get("sample_index")
        contiguous = (
            type(sample_id) is int and
            (previous_index is None or sample_id == previous_index - 1)
        )
        same_model = sample.get("root_count") == expected and sample.get("model_version") == target_version
        if not contiguous or not same_model:
            break
        model_streak += 1
        cpu = sample.get("app_cpu_percent")
        quiet = type(cpu) in (int, float) and 0.0 <= float(cpu) <= settled_idle_cpu_percent
        if quiet_suffix_open and quiet:
            quiet_streak += 1
        else:
            quiet_suffix_open = False
        previous_index = sample_id

    readiness["model_stable_consecutive_samples"] = model_streak
    readiness["app_cpu_quiet_consecutive_samples"] = quiet_streak
    readiness["ready"] = (
        model_streak >= DEFAULT_EXTERNAL_MODEL_STABILITY_SAMPLES and
        quiet_streak >= DEFAULT_EXTERNAL_READINESS_SAMPLES
    )
    return readiness


def summarize_external(
    model_samples: list[dict[str, object]], docker_stats: list[dict[str, object]],
    manifest: dict[str, object], label: str, breadcrumb_mode: str | None = None,
    phase_timing: dict[str, object] | None = None,
    target_index: int | None = None,
    remote_docker_stats: list[dict[str, object]] | None = None,
    settled_idle_cpu_percent: float = DEFAULT_SETTLED_IDLE_CPU_PERCENT,
) -> dict[str, object]:
    """Summarize the diagnostics-off readiness and external resource lane."""
    expected = _expected_summary_root_count(manifest)
    normalized = [sample for sample in model_samples if isinstance(sample, dict)]
    observed = normalized[-1] if normalized else {}
    target_readiness = _external_target_readiness(
        normalized, expected, target_index, settled_idle_cpu_percent,
    )
    stable = bool(target_readiness["model_stable_consecutive_samples"] >= DEFAULT_EXTERNAL_MODEL_STABILITY_SAMPLES)
    stats = summarize_docker_stats(docker_stats, "app")
    remote_stats = summarize_docker_stats(remote_docker_stats or [], "remote-helper")
    timing = phase_timing if isinstance(phase_timing, dict) else {}
    post_target_required = timing.get("post_target_observation_required_seconds")
    post_target_elapsed = timing.get("post_scan_settled_idle_observation_ms")
    post_target_observation_complete = (
        type(post_target_required) is int and
        post_target_required >= DEFAULT_EXTERNAL_POST_TARGET_OBSERVATION_SECONDS and
        type(post_target_elapsed) is int and
        post_target_elapsed >= DEFAULT_EXTERNAL_POST_TARGET_OBSERVATION_SECONDS * 1000
    )
    scan_sample_count = target_index + 1 if target_index is not None and 0 <= target_index < len(normalized) else len(normalized)
    baseline_checks = {
        "expected_summary_root_cardinality": observed.get("root_count") == expected and expected > 0,
        "model_version_stable": stable,
        "model_target_readiness": bool(target_readiness["ready"]),
        "post_target_observation_complete": post_target_observation_complete,
        "external_docker_stats_available": stats["sample_count"] > 0,
        "breadcrumb_mode_recorded": breadcrumb_mode in {"on", "off"},
    }
    target_ms = timing.get("model_target_epoch_ms")
    post_stats = [sample for sample in docker_stats if isinstance(sample, dict)
                  and isinstance(target_ms, int) and type(sample.get("t_epoch_ms")) is int
                  and sample["t_epoch_ms"] >= target_ms]
    post_stats_summary = summarize_docker_stats(post_stats, "app")
    post_remote_stats = [sample for sample in (remote_docker_stats or []) if isinstance(sample, dict)
                         and isinstance(target_ms, int) and type(sample.get("t_epoch_ms")) is int
                         and sample["t_epoch_ms"] >= target_ms]
    post_remote_stats_summary = summarize_docker_stats(post_remote_stats, "remote-helper")
    app_average = post_stats_summary.get("average_cpu_percent_one_core")
    app_cpu_acceptance_applicable = target_index is not None and bool(post_stats)
    app_cpu_acceptance_pass = (
        app_cpu_acceptance_applicable and type(app_average) in (int, float)
        and float(app_average) <= settled_idle_cpu_percent
    )
    acceptance_checks = {
        "model_target_readiness": bool(target_readiness["ready"]),
        "post_target_observation_complete": post_target_observation_complete,
        "app_cpu_acceptance_applicable": app_cpu_acceptance_applicable,
        "app_cpu_average_within_threshold": app_cpu_acceptance_pass,
        "remote_helper_stats_available": remote_stats["sample_count"] > 0,
        "remote_helper_post_target_stats_available": post_remote_stats_summary["sample_count"] > 0,
    }
    if target_index is not None:
        baseline_checks["app_cpu_average_within_threshold"] = app_cpu_acceptance_pass
        baseline_checks["remote_helper_stats_available"] = remote_stats["sample_count"] > 0
        baseline_checks["remote_helper_post_target_stats_available"] = post_remote_stats_summary["sample_count"] > 0
    baseline_valid = label != "baseline" or all(baseline_checks.values())
    return {
        "schema": "seedsync.performance-lab.metrics.v2",
        "label": label,
        "measurement_mode": "external-summary",
        "fixture_fingerprint": manifest.get("fixture_fingerprint"),
        "config_fingerprint": manifest.get("config_fingerprint"),
        "diagnostics_schema": None,
        "sample_count": len(normalized),
        "expected_summary_root_cardinality": expected,
        "observed_summary_root_cardinality": observed.get("root_count"),
        "model_version": observed.get("model_version"),
        "model_version_stable": stable,
        "target_readiness": target_readiness,
        "post_target_observation_required_seconds": post_target_required,
        "post_target_observation_elapsed_ms": post_target_elapsed,
        "baseline_checks": baseline_checks,
        "baseline_valid": baseline_valid,
        "acceptance_checks": acceptance_checks,
        "acceptance_valid": all(acceptance_checks.values()),
        "app_cpu_threshold_percent": settled_idle_cpu_percent,
        "app_cpu_average_percent_one_core": app_average,
        "app_cpu_peak_percent_one_core": post_stats_summary.get("peak_cpu_percent_one_core"),
        "app_cpu_acceptance_applicable": app_cpu_acceptance_applicable,
        "app_cpu_acceptance_pass": app_cpu_acceptance_pass,
        "full_scan_phase": {
            "name": "full_scan", "sample_count": scan_sample_count,
            "model_target_epoch_ms": target_ms,
            "model_target_version": normalized[target_index].get("model_version")
                if target_index is not None and 0 <= target_index < len(normalized) else None,
        },
        "settled_idle_phase": {
            "name": "settled_idle", "sample_count": len(post_stats),
            "app_docker_stats": post_stats_summary,
            "remote_helper_docker_stats": post_remote_stats_summary,
        },
        "post_target_phase": {
            "name": "post_target", "sample_count": len(post_stats),
            "app_docker_stats": post_stats_summary,
            "remote_helper_docker_stats": post_remote_stats_summary,
        },
        "phase_timing": timing,
        "external_docker_stats": stats,
        "app_docker_stats": stats,
        "remote_helper_docker_stats": remote_stats,
        "post_target_external_docker_stats": post_stats_summary,
        "post_target_app_docker_stats": post_stats_summary,
        "post_target_remote_helper_docker_stats": post_remote_stats_summary,
        "fixed_stage_cpu_attribution": [],
        "missing_fixed_metrics": list(FIXED_METRICS),
        "breadcrumb": {"mode": breadcrumb_mode},
        "steady_idle_target_met": app_cpu_acceptance_pass,
    }


def _windows(diagnostics: dict[str, object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    samples = diagnostics.get("samples", [])
    if not isinstance(samples, list):
        return result
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        window = sample.get("stage_window")
        metrics = window.get("metrics") if isinstance(window, dict) else None
        if isinstance(metrics, dict):
            result.append({
                "sequence": sample.get("sequence"),
                "metrics": metrics,
                "elapsed_seconds": window.get("elapsed_seconds") if isinstance(window, dict) else None,
                "cpu": _sample_cpu(sample),
                "model_tree_file_count": _sample_model_file_count(sample),
            })
    return result


def _metric_values(windows: Iterable[dict[str, object]]) -> dict[str, dict[str, float | int | None]]:
    totals: dict[str, dict[str, float | int | None]] = defaultdict(
        lambda: {"count": 0, "total_wall_seconds": 0.0, "total_cpu_seconds": 0.0,
                 "wall_time_percent": 0.0, "cpu_percent_one_core": 0.0}
    )
    elapsed = 0.0
    for entry in windows:
        metrics = entry.get("metrics")
        if not isinstance(metrics, dict):
            continue
        window_elapsed = _number(entry.get("elapsed_seconds")) or 1.0
        elapsed += window_elapsed
        for metric, values in metrics.items():
            if not isinstance(values, dict):
                continue
            item = totals[metric]
            item["count"] = int(item["count"] or 0) + int(values.get("count", 0))
            item["total_wall_seconds"] = float(item["total_wall_seconds"] or 0.0) + (_number(values.get("total_wall_seconds")) or 0.0)
            item["total_cpu_seconds"] = float(item["total_cpu_seconds"] or 0.0) + (_number(values.get("total_cpu_seconds")) or 0.0)
    for values in totals.values():
        values["wall_time_percent"] = (float(values["total_wall_seconds"] or 0.0) / elapsed * 100.0) if elapsed else 0.0
        values["cpu_percent_one_core"] = (float(values["total_cpu_seconds"] or 0.0) / elapsed * 100.0) if elapsed else 0.0
    return dict(totals)


def _phase(windows: list[dict[str, object]], name: str) -> dict[str, object]:
    metrics = _metric_values(windows)
    dominant = None
    dominant_wall = 0.0
    attributed = []
    for metric, values in metrics.items():
        if metric not in FIXED_METRICS:
            continue
        wall = float(values.get("wall_time_percent") or 0.0)
        if wall > dominant_wall:
            dominant, dominant_wall = metric, wall
        if float(values.get("total_cpu_seconds") or 0.0) > 0.0:
            attributed.append(metric)
    cpu_values = [float(entry["cpu"]) for entry in windows]
    model_values = [entry["model_tree_file_count"] for entry in windows if entry.get("model_tree_file_count") is not None]
    return {
        "name": name,
        "sample_count": len(windows),
        "first_sequence": windows[0].get("sequence") if windows else None,
        "last_sequence": windows[-1].get("sequence") if windows else None,
        "peak_cpu_percent_one_core": max(cpu_values, default=0.0),
        "average_cpu_percent_one_core": (sum(cpu_values) / len(cpu_values)) if cpu_values else 0.0,
        "latest_model_tree_file_count": model_values[-1] if model_values else None,
        "dominant_stage": dominant,
        "dominant_wall_time_percent": dominant_wall,
        "fixed_stage_cpu_attribution": sorted(attributed),
        "fixed_metrics": {metric: values for metric, values in metrics.items() if metric in FIXED_METRICS},
    }


def _high_cpu_streak(windows: list[dict[str, object]], threshold: float) -> int:
    best = current = 0
    previous_sequence = None
    for entry in windows:
        sequence = entry.get("sequence")
        contiguous = previous_sequence is None or (type(sequence) is int and type(previous_sequence) is int and sequence == previous_sequence + 1)
        current = current + 1 if contiguous and float(entry["cpu"]) >= threshold else 0
        best = max(best, current)
        previous_sequence = sequence
    return best


def summarize(
    diagnostics: dict[str, object], manifest: dict[str, object], label: str,
    expected_stage: str | None = None, min_cpu_percent: float = 50.0,
    min_high_cpu_samples: int = 3, model_count_tolerance: float = 0.15,
    minimum_merged_nodes: int = 200_000,
    settled_idle_cpu_percent: float = DEFAULT_SETTLED_IDLE_CPU_PERCENT,
    breadcrumbs: dict[str, object] | None = None, breadcrumb_mode: str | None = None,
    target_sequence: int | None = None,
) -> dict[str, object]:
    """Return a phase-aware summary; ``expected_stage`` is retained for API compatibility."""
    windows = _windows(diagnostics)
    topology = manifest.get("topology") if isinstance(manifest.get("topology"), dict) else {}
    physical_expected_file_count = int(
        topology.get("expected_model_tree_file_count", 0) or 0
    )
    physical_expected_merged_nodes = int(
        topology.get("expected_merged_model_tree_nodes", 0) or 0
    )
    expected_file_count = int(
        topology.get("enabled_expected_model_tree_file_count", physical_expected_file_count) or 0
    )
    expected_merged_nodes = int(
        topology.get("enabled_expected_merged_model_tree_nodes", physical_expected_merged_nodes) or 0
    )
    model_values = [entry["model_tree_file_count"] for entry in windows if entry.get("model_tree_file_count") is not None]
    observed_model_count = model_values[-1] if model_values else None
    model_count_valid = (
        observed_model_count is not None and expected_merged_nodes > 0 and
        abs(observed_model_count - expected_merged_nodes) <= expected_merged_nodes * model_count_tolerance
    )
    full_scan_windows = [entry for entry in windows if target_sequence is None or
                         (type(entry.get("sequence")) is int and entry["sequence"] <= target_sequence)]
    post_target_windows = [entry for entry in windows if target_sequence is not None and
                           type(entry.get("sequence")) is int and entry["sequence"] > target_sequence]
    classification_windows = full_scan_windows if target_sequence is not None else windows
    active_windows = []
    settled_windows = []
    for entry in classification_windows:
        values = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
        fixed_wall = sum((_number(item.get("wall_time_percent")) or 0.0) for metric, item in values.items()
                         if metric in FIXED_METRICS and isinstance(item, dict))
        entry["fixed_wall_time_percent"] = fixed_wall
        if fixed_wall > 0.0 or float(entry["cpu"]) >= min_cpu_percent:
            active_windows.append(entry)
        if (entry.get("model_tree_file_count") is not None and model_count_valid and
                float(entry["cpu"]) < settled_idle_cpu_percent and fixed_wall < settled_idle_cpu_percent):
            settled_windows.append(entry)
    post_active_windows = []
    post_settled_windows = []
    for entry in post_target_windows:
        values = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
        fixed_wall = sum((_number(item.get("wall_time_percent")) or 0.0) for metric, item in values.items()
                         if metric in FIXED_METRICS and isinstance(item, dict))
        entry["fixed_wall_time_percent"] = fixed_wall
        if fixed_wall > 0.0 or float(entry["cpu"]) >= min_cpu_percent:
            post_active_windows.append(entry)
        if (entry.get("model_tree_file_count") is not None and model_count_valid and
                float(entry["cpu"]) < settled_idle_cpu_percent and fixed_wall < settled_idle_cpu_percent):
            post_settled_windows.append(entry)
    active_metrics = _metric_values(active_windows)
    attributed = sorted(
        metric for metric, values in active_metrics.items()
        if metric in FIXED_METRICS and float(values.get("total_cpu_seconds") or 0.0) > 0.0
    )
    counters = diagnostics.get("counters") if isinstance(diagnostics.get("counters"), dict) else {}
    dropped_value = counters.get("duration_spans_dropped")
    dropped_spans = int(dropped_value) if type(dropped_value) is int else None
    rebuild_reason_counters = {
        key: int(value)
        for key, value in counters.items()
        if isinstance(key, str) and key.startswith("model_rebuild_") and type(value) is int
    }
    model_builder_invalidation_counters = {
        key: int(value)
        for key, value in counters.items()
        if key in MODEL_BUILDER_INVALIDATION_COUNTERS
        and type(value) is int
    }
    high_cpu_streak = _high_cpu_streak(windows, min_cpu_percent)
    post_high_cpu_streak = _high_cpu_streak(post_target_windows, min_cpu_percent)
    post_metrics = _metric_values(post_active_windows)
    post_attributed = sorted(
        metric for metric, values in post_metrics.items()
        if metric in FIXED_METRICS and float(values.get("total_cpu_seconds") or 0.0) > 0.0
    )
    expected_cardinality_valid = physical_expected_merged_nodes >= minimum_merged_nodes
    baseline_checks = {
        "expected_merged_model_tree_nodes": expected_cardinality_valid,
        "model_tree_file_count_near_target": model_count_valid,
        "high_cpu_consecutive_samples": high_cpu_streak >= min_high_cpu_samples,
        "fixed_stage_cpu_attribution": bool(attributed),
        "duration_spans_dropped_zero": dropped_spans == 0,
        "breadcrumb_mode_recorded": breadcrumb_mode in {"on", "off"},
    }
    if target_sequence is not None:
        baseline_checks["post_target_high_cpu_staged"] = (
            post_high_cpu_streak >= min_high_cpu_samples and bool(post_attributed)
        )
    baseline_valid = label != "baseline" or all(baseline_checks.values())
    post_target_all = _phase(post_target_windows, "post_target_all")
    app_cpu_acceptance_applicable = target_sequence is not None and len(post_target_windows) >= min_high_cpu_samples
    app_cpu_average = post_target_all["average_cpu_percent_one_core"] if app_cpu_acceptance_applicable else None
    app_cpu_acceptance_pass = (
        app_cpu_acceptance_applicable and type(app_cpu_average) in (int, float)
        and float(app_cpu_average) <= settled_idle_cpu_percent
    )
    if target_sequence is not None:
        baseline_checks["app_cpu_average_within_threshold"] = app_cpu_acceptance_pass
    baseline_valid = label != "baseline" or all(baseline_checks.values())
    steady_idle_target_met = app_cpu_acceptance_pass
    return {
        "schema": "seedsync.performance-lab.metrics.v2",
        "label": label,
        "fixture_fingerprint": manifest.get("fixture_fingerprint"),
        "config_fingerprint": manifest.get("config_fingerprint"),
        "diagnostics_schema": diagnostics.get("schema"),
        "sample_count": len(diagnostics.get("samples", [])) if isinstance(diagnostics.get("samples"), list) else 0,
        "expected_baseline_stage": expected_stage,
        "physical_expected_merged_model_tree_nodes": physical_expected_merged_nodes,
        "enabled_expected_merged_model_tree_nodes": expected_merged_nodes,
        "expected_merged_model_tree_nodes": expected_merged_nodes,
        "minimum_expected_merged_model_tree_nodes": minimum_merged_nodes,
        "physical_expected_model_tree_file_count": physical_expected_file_count,
        "enabled_expected_model_tree_file_count": expected_file_count,
        "expected_model_tree_file_count": expected_file_count,
        "expected_model_tree_node_count": expected_merged_nodes,
        "observed_model_tree_file_count": observed_model_count,
        "model_count_tolerance": model_count_tolerance,
        "peak_cpu_percent_one_core": max((float(entry["cpu"]) for entry in windows), default=0.0),
        "high_cpu_consecutive_samples": high_cpu_streak,
        "post_target_sample_sequence": target_sequence,
        "post_target_high_cpu_consecutive_samples": post_high_cpu_streak,
        "post_target_fixed_stage_cpu_attribution": post_attributed,
        "duration_spans_dropped": dropped_spans,
        "model_rebuild_reason_counters": dict(sorted(rebuild_reason_counters.items())),
        "model_builder_invalidation_counters": dict(sorted(model_builder_invalidation_counters.items())),
        "breadcrumb": {
            "mode": breadcrumb_mode,
            "enabled": breadcrumbs.get("enabled") if isinstance(breadcrumbs, dict) else None,
            "max_entries": breadcrumbs.get("max_entries") if isinstance(breadcrumbs, dict) else None,
            "entry_count": breadcrumbs.get("entry_count") if isinstance(breadcrumbs, dict) else None,
            "version": breadcrumbs.get("version") if isinstance(breadcrumbs, dict) else None,
        },
        "fixed_stage_cpu_attribution": attributed,
        "baseline_checks": baseline_checks,
        "baseline_valid": baseline_valid,
        "full_scan_phase": _phase(full_scan_windows if target_sequence is not None else active_windows, "full_scan"),
        "settled_idle_phase": _phase(post_target_windows if target_sequence is not None else settled_windows, "settled_idle"),
        "post_target_phase": _phase(post_target_windows, "post_target"),
        "post_target_all_phase": post_target_all,
        "steady_idle_target_met": steady_idle_target_met,
        "acceptance_checks": {
            "app_cpu_acceptance_applicable": app_cpu_acceptance_applicable,
            "app_cpu_average_within_threshold": app_cpu_acceptance_pass,
        },
        "acceptance_valid": app_cpu_acceptance_pass,
        "app_cpu_threshold_percent": settled_idle_cpu_percent,
        "app_cpu_average_percent_one_core": app_cpu_average,
        "app_cpu_peak_percent_one_core": post_target_all["peak_cpu_percent_one_core"] if app_cpu_acceptance_applicable else None,
        "app_cpu_acceptance_applicable": app_cpu_acceptance_applicable,
        "app_cpu_acceptance_pass": app_cpu_acceptance_pass,
        "missing_fixed_metrics": [metric for metric in FIXED_METRICS if metric not in active_metrics],
        "active_stage": diagnostics.get("active_stage"),
        "active_scanner_stage": diagnostics.get("active_scanner_stage"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("diagnostics", "external-summary"), default="diagnostics")
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--model-summary", type=Path)
    parser.add_argument("--docker-stats", type=Path)
    parser.add_argument("--remote-docker-stats", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--expected-stage")
    parser.add_argument("--min-cpu-percent", type=float, default=50.0)
    parser.add_argument("--min-high-cpu-samples", type=int, default=3)
    parser.add_argument("--model-count-tolerance", type=float, default=0.15)
    parser.add_argument("--minimum-merged-nodes", type=int, default=200_000)
    parser.add_argument("--settled-idle-cpu-percent", type=float, default=DEFAULT_SETTLED_IDLE_CPU_PERCENT)
    parser.add_argument("--breadcrumbs", type=Path)
    parser.add_argument("--phase-timing", type=Path)
    parser.add_argument("--breadcrumb-mode", choices=("on", "off"))
    parser.add_argument("--target-sequence", type=int)
    args = parser.parse_args()
    if not 0.0 < args.settled_idle_cpu_percent <= DEFAULT_SETTLED_IDLE_CPU_PERCENT:
        parser.error("--settled-idle-cpu-percent must be greater than 0 and no more than 1.0 for acceptance")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.mode == "external-summary":
        if not args.model_summary or not args.docker_stats:
            parser.error("--model-summary and --docker-stats are required for --mode external-summary")
        model_payload = json.loads(args.model_summary.read_text(encoding="utf-8"))
        stats_payload = json.loads(args.docker_stats.read_text(encoding="utf-8"))
        remote_stats_payload = json.loads(args.remote_docker_stats.read_text(encoding="utf-8")) \
            if args.remote_docker_stats else {}
        timing_path = args.phase_timing
        timing = json.loads(timing_path.read_text(encoding="utf-8")) if timing_path else None
        model_samples = model_payload.get("samples", []) if isinstance(model_payload, dict) else []
        docker_samples = stats_payload.get("samples", []) if isinstance(stats_payload, dict) else []
        remote_docker_samples = remote_stats_payload.get("samples", []) \
            if isinstance(remote_stats_payload, dict) else []
        summary = summarize_external(model_samples, docker_samples, manifest, args.label,
                                     args.breadcrumb_mode, timing, args.target_sequence,
                                     remote_docker_samples, args.settled_idle_cpu_percent)
    else:
        if not args.diagnostics:
            parser.error("--diagnostics is required for --mode diagnostics")
        diagnostics = json.loads(args.diagnostics.read_text(encoding="utf-8"))
        breadcrumbs = json.loads(args.breadcrumbs.read_text(encoding="utf-8")) if args.breadcrumbs else None
        summary = summarize(
            diagnostics, manifest, args.label, args.expected_stage, args.min_cpu_percent,
            args.min_high_cpu_samples, args.model_count_tolerance, args.minimum_merged_nodes,
            args.settled_idle_cpu_percent, breadcrumbs, args.breadcrumb_mode,
            args.target_sequence,
        )
        if args.docker_stats:
            stats_payload = json.loads(args.docker_stats.read_text(encoding="utf-8"))
            docker_samples = stats_payload.get("samples", []) if isinstance(stats_payload, dict) else []
            timing = json.loads(args.phase_timing.read_text(encoding="utf-8")) \
                if args.phase_timing else {}
            target_ms = timing.get("model_target_epoch_ms") if isinstance(timing, dict) else None
            post_stats = [
                sample for sample in docker_samples if isinstance(sample, dict)
                and type(target_ms) is int and type(sample.get("t_epoch_ms")) is int
                and sample["t_epoch_ms"] >= target_ms
            ]
            summary["external_docker_stats"] = summarize_docker_stats(docker_samples, "app")
            summary["post_target_external_docker_stats"] = summarize_docker_stats(post_stats, "app")
            remote_samples_payload = json.loads(args.remote_docker_stats.read_text(encoding="utf-8")) \
                if args.remote_docker_stats else {}
            remote_samples = remote_samples_payload.get("samples", []) \
                if isinstance(remote_samples_payload, dict) else []
            post_remote = [sample for sample in remote_samples if isinstance(sample, dict)
                           and type(target_ms) is int and type(sample.get("t_epoch_ms")) is int
                           and sample["t_epoch_ms"] >= target_ms]
            summary["app_docker_stats"] = summarize_docker_stats(docker_samples, "app")
            summary["remote_helper_docker_stats"] = summarize_docker_stats(remote_samples, "remote-helper")
            summary["post_target_app_docker_stats"] = summarize_docker_stats(post_stats, "app")
            summary["post_target_remote_helper_docker_stats"] = summarize_docker_stats(post_remote, "remote-helper")
            summary["settled_idle_phase"]["app_docker_stats"] = summary["post_target_app_docker_stats"]
            summary["settled_idle_phase"]["remote_helper_docker_stats"] = summary["post_target_remote_helper_docker_stats"]
            summary["post_target_phase"]["app_docker_stats"] = summary["post_target_app_docker_stats"]
            summary["post_target_phase"]["remote_helper_docker_stats"] = summary["post_target_remote_helper_docker_stats"]
            app_stats = summary["post_target_app_docker_stats"]
            app_average = app_stats.get("average_cpu_percent_one_core")
            applicable = args.target_sequence is not None and bool(post_stats)
            passed = applicable and type(app_average) in (int, float) \
                and float(app_average) <= args.settled_idle_cpu_percent
            summary["app_cpu_threshold_percent"] = args.settled_idle_cpu_percent
            summary["app_cpu_average_percent_one_core"] = app_average
            summary["app_cpu_peak_percent_one_core"] = app_stats.get("peak_cpu_percent_one_core")
            summary["app_cpu_acceptance_applicable"] = applicable
            summary["app_cpu_acceptance_pass"] = passed
            remote_stats = summary["remote_helper_docker_stats"]
            remote_post_stats = summary["post_target_remote_helper_docker_stats"]
            remote_available = remote_stats["sample_count"] > 0
            remote_post_available = remote_post_stats["sample_count"] > 0
            summary["acceptance_checks"] = {
                "app_cpu_acceptance_applicable": applicable,
                "app_cpu_average_within_threshold": passed,
                "remote_helper_stats_available": remote_available,
                "remote_helper_post_target_stats_available": remote_post_available,
            }
            summary["acceptance_valid"] = passed and remote_available and remote_post_available
            if args.target_sequence is not None:
                summary["baseline_checks"]["remote_helper_stats_available"] = remote_available
                summary["baseline_checks"]["remote_helper_post_target_stats_available"] = remote_post_available
                summary["baseline_valid"] = args.label != "baseline" or all(summary["baseline_checks"].values())
            summary["steady_idle_target_met"] = passed
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    acceptance_valid = bool(summary.get("acceptance_valid", False))
    baseline_valid = args.label != "baseline" or bool(summary.get("baseline_valid"))
    return 0 if acceptance_valid and baseline_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
