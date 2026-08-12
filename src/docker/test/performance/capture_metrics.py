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
    minimum_merged_nodes: int = 200_000, settled_idle_cpu_percent: float = 10.0,
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
    steady_idle_target_met = (
        len(post_target_windows) >= min_high_cpu_samples and
        float(post_target_all["peak_cpu_percent_one_core"]) < settled_idle_cpu_percent
    )
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
        "settled_idle_phase": _phase(post_settled_windows if target_sequence is not None else settled_windows, "settled_idle"),
        "post_target_phase": _phase(post_active_windows, "post_target"),
        "post_target_all_phase": post_target_all,
        "steady_idle_target_met": steady_idle_target_met,
        "missing_fixed_metrics": [metric for metric in FIXED_METRICS if metric not in active_metrics],
        "active_stage": diagnostics.get("active_stage"),
        "active_scanner_stage": diagnostics.get("active_scanner_stage"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--expected-stage")
    parser.add_argument("--min-cpu-percent", type=float, default=50.0)
    parser.add_argument("--min-high-cpu-samples", type=int, default=3)
    parser.add_argument("--model-count-tolerance", type=float, default=0.15)
    parser.add_argument("--minimum-merged-nodes", type=int, default=200_000)
    parser.add_argument("--settled-idle-cpu-percent", type=float, default=10.0)
    parser.add_argument("--breadcrumbs", type=Path)
    parser.add_argument("--breadcrumb-mode", choices=("on", "off"))
    parser.add_argument("--target-sequence", type=int)
    args = parser.parse_args()
    diagnostics = json.loads(args.diagnostics.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    breadcrumbs = json.loads(args.breadcrumbs.read_text(encoding="utf-8")) if args.breadcrumbs else None
    summary = summarize(
        diagnostics, manifest, args.label, args.expected_stage, args.min_cpu_percent,
        args.min_high_cpu_samples, args.model_count_tolerance, args.minimum_merged_nodes,
        args.settled_idle_cpu_percent, breadcrumbs, args.breadcrumb_mode,
        args.target_sequence,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if args.label != "baseline" or bool(summary["baseline_valid"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
