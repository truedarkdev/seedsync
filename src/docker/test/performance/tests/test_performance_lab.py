import json
import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PERF_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PERF_DIR))
sys.path.insert(0, str(PERF_DIR.parents[2] / "python"))

from capture_metrics import (
    FIXED_METRICS,
    latest_model_status,
    latest_model_summary_status,
    summarize,
    summarize_docker_stats,
    summarize_external,
)
import generate_fixture as fixture_module
from generate_fixture import (
    MIXED_HIGH_CARD_NODES,
    config_fingerprint,
    generate_fixture,
    normalize_topology_spec,
    topology_fingerprint,
)
import sanitize_docker_state
from seed_config import seed_config
from common.config import Config
from web.auth_store import ApiKeyStore, _verify_secret


def test_fixture_is_deterministic_and_idempotent(tmp_path):
    local_root = tmp_path / "local"
    remote_root = tmp_path / "remote"
    manifest_path = tmp_path / "manifest.json"
    first = generate_fixture(local_root, remote_root, manifest_path, pairs=2, nodes_per_pair=40)
    first_file = local_root / "path-pair-01" / "bucket-000" / "node-00000000.bin"
    first_payload = first_file.read_bytes()
    second = generate_fixture(local_root, remote_root, manifest_path, pairs=2, nodes_per_pair=40)
    assert second["fixture_fingerprint"] == first["fixture_fingerprint"]
    assert second["topology"]["file_nodes_per_side"] == 80
    assert second["topology"]["expected_merged_model_tree_nodes"] > 80
    assert first_file.read_bytes() == first_payload
    assert (local_root / ".seedsync-performance-fixture.json").exists()
    if os.name == "posix":
        assert stat.S_IMODE((local_root / "path-pair-01" / "bucket-000").stat().st_mode) == 0o775


def test_mixed_profile_normalizes_roles_counts_and_enabled_state():
    spec = normalize_topology_spec("mixed", high_card_enabled=False)
    assert [pair["role"] for pair in spec["pairs"]] == ["ordinary-active", "high-cardinality-idle"]
    assert spec["pairs"][0]["auto_queue"] is False
    assert spec["pairs"][0]["nodes_local"] == spec["pairs"][0]["nodes_remote"] == 64
    assert spec["pairs"][1]["nodes_local"] == spec["pairs"][1]["nodes_remote"] == MIXED_HIGH_CARD_NODES
    assert spec["pairs"][1]["enabled"] is False
    target = spec["pairs"][0]["remote_only_targets"][0]
    assert target["relative_path"] == "path-pair-01/remote-only-target.bin"
    assert target["size_bytes"] >= 32 * 1024 * 1024


def test_mixed_profile_rejects_misleading_topology_overrides():
    with pytest.raises(ValueError, match="exactly two pairs"):
        normalize_topology_spec("mixed", pairs=1)
    with pytest.raises(ValueError, match="nodes_per_pair"):
        normalize_topology_spec("mixed", nodes_per_pair=4)


def test_run_metadata_mixed_profile_does_not_claim_uniform_nodes_per_pair():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    assert '"nodes_per_pair": requested_nodes if os.environ["PERF_PROFILE"] == "uniform" else None' in lab_source
    assert '"pair_node_counts"' in lab_source


def test_mixed_fingerprints_are_deterministic_and_enabled_ab_is_data_stable():
    enabled = normalize_topology_spec("mixed", high_card_enabled=True)
    disabled = normalize_topology_spec("mixed", high_card_enabled=False)
    assert topology_fingerprint(enabled) == topology_fingerprint(disabled)
    assert config_fingerprint(enabled) != config_fingerprint(disabled)
    assert topology_fingerprint(enabled) == topology_fingerprint(normalize_topology_spec("mixed"))


def test_fixture_retained_topology_mismatch_is_rejected(tmp_path):
    local_root, remote_root, manifest = tmp_path / "local", tmp_path / "remote", tmp_path / "manifest.json"
    generate_fixture(local_root, remote_root, manifest, pairs=1, nodes_per_pair=4)
    with pytest.raises(RuntimeError, match="different topology|do not match"):
        generate_fixture(local_root, remote_root, manifest, pairs=2, nodes_per_pair=4)


def test_mixed_config_records_roles_enabled_and_remote_target(tmp_path):
    config_dir = tmp_path / "config"
    seed_config(config_dir, "local-test-token", profile="mixed", high_card_enabled=False)
    payload = json.loads((config_dir / "path_pairs.json").read_text(encoding="utf-8"))
    assert payload["profile"] == "mixed"
    assert payload["topology_fingerprint"] == topology_fingerprint(payload["experiment_spec"])
    assert payload["config_fingerprint"] == config_fingerprint(payload["experiment_spec"])
    assert [(pair["role"], pair["enabled"], pair["auto_queue"]) for pair in payload["path_pairs"]] == [
        ("ordinary-active", True, False), ("high-cardinality-idle", False, True)
    ]
    assert payload["path_pairs"][0]["remote_only_targets"][0]["relative_path"] == "path-pair-01/remote-only-target.bin"


def test_mixed_retained_manifest_reuses_fixture_but_refreshes_enabled_expectations(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture_module, "MIXED_HIGH_CARD_NODES", 4)
    local_root, remote_root = tmp_path / "local", tmp_path / "remote"
    enabled_manifest = generate_fixture(local_root, remote_root, tmp_path / "enabled.json", profile="mixed")
    disabled_manifest = generate_fixture(
        local_root, remote_root, tmp_path / "disabled.json", profile="mixed", high_card_enabled=False
    )
    assert disabled_manifest["fixture_fingerprint"] == enabled_manifest["fixture_fingerprint"]
    assert disabled_manifest["config_fingerprint"] != enabled_manifest["config_fingerprint"]
    assert disabled_manifest["topology"]["enabled_expected_merged_model_tree_nodes"] < enabled_manifest["topology"]["enabled_expected_merged_model_tree_nodes"]
    assert disabled_manifest["topology"]["enabled_pair_ids"] == ["pair-01"]
    assert disabled_manifest["topology"]["local_file_nodes"] == 64 + fixture_module.MIXED_HIGH_CARD_NODES
    assert disabled_manifest["topology"]["remote_file_nodes"] == 64 + fixture_module.MIXED_HIGH_CARD_NODES + 1
    assert disabled_manifest["topology"]["local_directory_nodes"] == disabled_manifest["topology"]["remote_directory_nodes"]
    target = remote_root / "path-pair-01" / "remote-only-target.bin"
    assert target.stat().st_size == 32 * 1024 * 1024


def test_legacy_uniform_marker_is_validated_and_migrated_without_regeneration(tmp_path):
    local_root, remote_root = tmp_path / "local", tmp_path / "remote"
    original = generate_fixture(local_root, remote_root, tmp_path / "original.json", pairs=1, nodes_per_pair=4)
    legacy_topology = {
        key: value for key, value in original["topology"].items()
        if key not in {
            "expected_merged_nodes_by_pair", "expected_file_counts_by_pair",
            "enabled_pair_ids", "enabled_expected_merged_model_tree_nodes",
            "enabled_expected_model_tree_file_count",
            "remote_only_directory_nodes", "local_file_nodes", "remote_file_nodes",
            "local_directory_nodes", "remote_directory_nodes",
        }
    }
    legacy_pairs = [{key: pair[key] for key in ("id", "name", "local_path", "remote_path", "directory", "nodes_per_side")} for pair in original["path_pairs"]]
    legacy_fingerprint = hashlib.sha256(json.dumps(legacy_topology, sort_keys=True).encode("utf-8")).hexdigest()
    legacy_manifest = {
        "schema": "seedsync.performance-lab.fixture.v1",
        "fixture_fingerprint": legacy_fingerprint,
        "synthetic_only": True,
        "topology": legacy_topology,
        "path_pairs": legacy_pairs,
    }
    marker = {"fixture_fingerprint": legacy_fingerprint, "request_fingerprint": legacy_fingerprint, "manifest": legacy_manifest}
    for root in (local_root, remote_root):
        (root / ".seedsync-performance-fixture.json").write_text(json.dumps(marker), encoding="utf-8")
    migrated = generate_fixture(local_root, remote_root, tmp_path / "migrated.json", pairs=1, nodes_per_pair=4)
    assert migrated["data_topology_spec"]["profile"] == "uniform"
    assert migrated["fixture_fingerprint"] == legacy_fingerprint
    assert (local_root / "path-pair-01" / "bucket-000" / "node-00000000.bin").exists()
    repeated = generate_fixture(local_root, remote_root, tmp_path / "migrated-again.json", pairs=1, nodes_per_pair=4)
    assert repeated["fixture_fingerprint"] == migrated["fixture_fingerprint"]
    assert repeated["config_fingerprint"] == migrated["config_fingerprint"]


def test_seed_config_cli_metadata_uses_normalized_profile_counts():
    source = (PERF_DIR / "seed_config.py").read_text(encoding="utf-8")
    assert '"pairs": len(spec["pairs"])' in source
    assert '"requested_pairs": args.pairs' in source
    assert '"pair_node_counts"' in source
    assert '"config_fingerprint": config_fingerprint(spec)' in source


def test_seed_config_cli_reads_token_from_environment_only(tmp_path):
    source = (PERF_DIR / "seed_config.py").read_text(encoding="utf-8")
    compose = (PERF_DIR / "compose.yml").read_text(encoding="utf-8")
    lab = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    assert "--api-token" not in source
    assert "--api-token" not in compose
    assert "--api-token" not in lab
    token = "env-only-performance-token"
    env = os.environ.copy()
    env["PERF_API_TOKEN"] = token
    config_dir = tmp_path / "config"
    result = subprocess.run(
        [sys.executable, str(PERF_DIR / "seed_config.py"), "--config-dir", str(config_dir), "--pairs", "1"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert token not in result.stdout and token not in result.stderr
    assert (config_dir / "settings.cfg").exists()
    missing_env = env.copy()
    missing_env.pop("PERF_API_TOKEN", None)
    missing = subprocess.run(
        [sys.executable, str(PERF_DIR / "seed_config.py"), "--config-dir", str(tmp_path / "missing")],
        capture_output=True, text=True, env=missing_env, check=False,
    )
    assert missing.returncode != 0
    assert "PERF_API_TOKEN" in missing.stderr
    assert token not in missing.stdout and token not in missing.stderr


def test_browser_dashboard_selects_pair_before_waiting_for_file_list():
    source = (PERF_DIR / "browser_probe.js").read_text(encoding="utf-8")
    dashboard = source[source.find("await page.goto(new URL('/dashboard'"):source.find("await traverseTargetRows")]
    assert "waitForDashboardShell(page, timeoutMs)" in dashboard
    assert dashboard.index("selectPairFromSidebar(page, target.pair_name, timeoutMs)") < dashboard.index(
        "page.locator('#file-list').waitFor({state: 'visible', timeout: timeoutMs})"
    )
    assert "const modeHandle = await page.waitForFunction" in source
    assert "return 'direct'" in source and "return link ? 'sidebar' : false" in source


def test_capture_uses_physical_cardinality_but_enabled_model_expectation():
    manifest = {
        "fixture_fingerprint": "fixture",
        "config_fingerprint": "config",
        "topology": {
            "expected_merged_model_tree_nodes": 200001,
            "expected_model_tree_file_count": 200001,
            "enabled_expected_merged_model_tree_nodes": 100,
            "enabled_expected_model_tree_file_count": 100,
        },
    }
    summary = summarize(_diagnostics(model_count=100), manifest, "baseline", min_cpu_percent=50,
                        min_high_cpu_samples=3, breadcrumb_mode="on")
    assert summary["physical_expected_merged_model_tree_nodes"] == 200001
    assert summary["enabled_expected_merged_model_tree_nodes"] == 100
    assert summary["baseline_checks"]["expected_merged_model_tree_nodes"] is True
    assert summary["baseline_checks"]["model_tree_file_count_near_target"] is True
    assert summary["baseline_valid"] is True
    assert summary["config_fingerprint"] == "config"


def _diagnostics(model_count=200001):
    metrics = {
        "local_scan_filesystem_traversal": {
            "count": 1, "total_wall_seconds": 0.5, "total_cpu_seconds": 0.45,
            "wall_time_percent": 50.0, "cpu_percent_one_core": 45.0,
        },
        "model_update_builder_sync": {
            "count": 1, "total_wall_seconds": 0.2, "total_cpu_seconds": 0.15,
            "wall_time_percent": 20.0, "cpu_percent_one_core": 15.0,
        },
    }
    samples = []
    for sequence, cpu in ((1, 70), (2, 75), (3, 72), (4, 5)):
        samples.append({
            "sequence": sequence,
            "process_cpu_percent_one_core": cpu,
            "cgroup_cpu_percent_one_core": cpu,
            "gauges": {"model_tree_file_count": model_count},
            "stage_window": {"elapsed_seconds": 1.0, "metrics": metrics if cpu > 10 else {}},
        })
    return {
        "schema": "seedsync.performance-diagnostics.v1",
        "counters": {"duration_spans_dropped": 0},
        "samples": samples,
    }


def test_metrics_require_attributed_cpu_and_record_idle_phase():
    manifest = {
        "fixture_fingerprint": "fixture",
        "topology": {
            "expected_merged_model_tree_nodes": 200001,
            "expected_model_tree_file_count": 100,
        },
    }
    summary = summarize(
        _diagnostics(), manifest, "baseline", min_cpu_percent=50,
        min_high_cpu_samples=3, breadcrumb_mode="on",
        breadcrumbs={"enabled": True, "max_entries": 128, "entry_count": 4},
    )
    assert summary["baseline_valid"] is True
    assert summary["full_scan_phase"]["dominant_stage"] == "local_scan_filesystem_traversal"
    assert summary["settled_idle_phase"]["sample_count"] == 0
    assert summary["breadcrumb"]["mode"] == "on"
    assert summary["steady_idle_target_met"] is False


def test_target_sequence_separates_post_scan_idle_from_reproduction():
    manifest = {
        "fixture_fingerprint": "fixture",
        "topology": {
            "expected_merged_model_tree_nodes": 200001,
            "expected_model_tree_file_count": 100,
        },
    }
    summary = summarize(
        _diagnostics(), manifest, "baseline", min_cpu_percent=50,
        min_high_cpu_samples=3, breadcrumb_mode="on", target_sequence=3,
    )
    assert summary["settled_idle_phase"]["sample_count"] == 1
    assert summary["baseline_checks"]["post_target_high_cpu_staged"] is False
    assert summary["baseline_valid"] is False
    assert summary["steady_idle_target_met"] is False


def test_baseline_rejects_dropped_spans():
    manifest = {
        "fixture_fingerprint": "fixture",
        "topology": {
            "expected_merged_model_tree_nodes": 200001,
            "expected_model_tree_file_count": 100,
        },
    }
    diagnostics = _diagnostics()
    diagnostics["counters"]["duration_spans_dropped"] = 1
    summary = summarize(diagnostics, manifest, "baseline", breadcrumb_mode="off")
    assert summary["baseline_valid"] is False
    assert summary["baseline_checks"]["duration_spans_dropped_zero"] is False


def test_summary_exposes_fixed_model_builder_invalidation_counters():
    manifest = {
        "fixture_fingerprint": "fixture",
        "topology": {
            "expected_merged_model_tree_nodes": 200001,
            "expected_model_tree_file_count": 100,
        },
    }
    diagnostics = _diagnostics()
    diagnostics["counters"].update({
        "model_builder_cache_invalidation_remote_files": 4,
        "model_builder_cache_invalidation_downloaded_files": 2,
        "model_builder_cache_invalidation_private_path": 99,
    })
    summary = summarize(diagnostics, manifest, "candidate", breadcrumb_mode="off")
    assert summary["model_builder_invalidation_counters"] == {
        "model_builder_cache_invalidation_downloaded_files": 2,
        "model_builder_cache_invalidation_remote_files": 4,
    }


def test_summary_allowlists_model_builder_setter_durations():
    setter_metrics = {
        "model_builder_set_local_files", "model_builder_set_remote_files",
        "model_builder_set_active_files", "model_builder_set_lftp_statuses",
        "model_builder_set_stopped_files",
    }
    assert setter_metrics.issubset(FIXED_METRICS)

    diagnostics = _diagnostics()
    diagnostics["samples"][0]["stage_window"]["metrics"].update({
        metric: {
            "count": 1, "total_wall_seconds": 0.1, "total_cpu_seconds": 0.05,
            "wall_time_percent": 10.0, "cpu_percent_one_core": 5.0,
        }
        for metric in setter_metrics
    })
    summary = summarize(
        diagnostics,
        {"topology": {"expected_merged_model_tree_nodes": 200001}},
        "candidate",
        breadcrumb_mode="off",
    )
    assert setter_metrics.issubset(summary["full_scan_phase"]["fixed_metrics"])


def test_latest_model_status_requires_zero_build_count_for_settled_boundary():
    diagnostics = _diagnostics()
    latest = diagnostics["samples"][-1]
    latest["stage_window"]["metrics"] = {"model_build": {"count": 1}}
    model_count, sequence, build_count = latest_model_status(diagnostics)
    assert (model_count, sequence, build_count) == (200001, 4, 1)
    assert not (model_count == 200001 and build_count == 0)

    latest["stage_window"]["metrics"]["model_build"]["count"] = 0
    model_count, sequence, build_count = latest_model_status(diagnostics)
    assert (model_count, sequence, build_count) == (200001, 4, 0)
    assert model_count == 200001 and build_count == 0


def test_latest_model_status_returns_missing_build_count_as_unsettled():
    diagnostics = _diagnostics()
    assert latest_model_status(diagnostics) == (200001, 4, None)
    diagnostics["samples"][-1]["stage_window"] = {"metrics": {"model_build": {"count": "0"}}}
    assert latest_model_status(diagnostics) == (200001, 4, None)


def test_latest_model_status_fails_closed_on_malformed_trailing_sample():
    diagnostics = _diagnostics()
    diagnostics["samples"].append("malformed")
    assert latest_model_status(diagnostics) == (None, None, None)


def test_latest_model_status_rejects_negative_fields():
    diagnostics = _diagnostics()
    diagnostics["samples"][-1]["sequence"] = -1
    diagnostics["samples"][-1]["gauges"]["model_tree_file_count"] = -1
    diagnostics["samples"][-1]["stage_window"] = {"metrics": {"model_build": {"count": -1}}}
    assert latest_model_status(diagnostics) == (None, None, None)


def test_latest_model_status_rejects_invalid_sequence_without_hiding_other_fields():
    diagnostics = _diagnostics()
    diagnostics["samples"][-1]["stage_window"] = {"metrics": {"model_build": {"count": 0}}}
    diagnostics["samples"][-1]["sequence"] = "4"
    assert latest_model_status(diagnostics) == (200001, None, 0)


def test_model_summary_status_is_bounded_and_aggregates_cardinality_without_ids():
    summary = {
        "model_version": 7,
        "path_pairs": [
            {"path_pair_id": "private-a", "root_count": 2, "active_count": 1, "queued_count": 0, "completed_count": 1},
            {"path_pair_id": "private-b", "root_count": 3, "active_count": 0, "queued_count": 2, "completed_count": 1},
        ],
    }
    status = latest_model_summary_status(summary)
    assert status == {
        "root_count": 5, "model_version": 7, "pair_count": 2,
        "active_count": 1, "queued_count": 2, "completed_count": 2,
    }
    assert "private-a" not in json.dumps(status)
    assert latest_model_summary_status({"model_version": 1, "path_pairs": [{"root_count": "bad"}]})["root_count"] is None


def test_external_summary_requires_stable_model_version_and_sanitized_docker_stats():
    manifest = {
        "fixture_fingerprint": "fixture", "config_fingerprint": "config",
        "path_pairs": [{"enabled": True, "nodes_local": 64, "directory": "path-pair-01",
                         "remote_only_targets": [{"relative_path": "path-pair-01/remote-only-target.bin"}]}],
    }
    samples = [
        {"sample_index": 0, "t_epoch_ms": 1000, "root_count": 2, "model_version": 4, "app_cpu_percent": 8.0},
        {"sample_index": 1, "t_epoch_ms": 2000, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.8},
        {"sample_index": 2, "t_epoch_ms": 3000, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.7},
        {"sample_index": 3, "t_epoch_ms": 4000, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.6},
    ]
    stats = [
        {"t_epoch_ms": 1000, "cpu_percent": 8.0, "memory_percent": 3.0, "process_count": 4},
        {"t_epoch_ms": 5000, "cpu_percent": 0.8, "memory_percent": 3.2, "process_count": 4},
    ]
    summary = summarize_external(samples, stats, manifest, "baseline", "off",
        {"model_target_epoch_ms": 4000, "post_scan_settled_idle_observation_ms": 150000,
         "post_target_observation_required_seconds": 150}, target_index=3,
        remote_docker_stats=[
            {"t_epoch_ms": 1000, "cpu_percent": 2.0, "memory_percent": 4.0},
            {"t_epoch_ms": 5000, "cpu_percent": 2.1, "memory_percent": 4.1},
        ])
    assert summary["measurement_mode"] == "external-summary"
    assert summary["baseline_valid"] is True
    assert summary["model_version_stable"] is True
    assert summary["external_docker_stats"]["sample_count"] == 2
    assert summary["post_target_external_docker_stats"]["sample_count"] == 1
    assert summary["steady_idle_target_met"] is True
    assert summary["acceptance_valid"] is True


def _external_manifest():
    return {
        "fixture_fingerprint": "fixture", "config_fingerprint": "config",
        "path_pairs": [{"enabled": True, "nodes_local": 64, "directory": "path-pair-01",
                         "remote_only_targets": [{"relative_path": "path-pair-01/remote-only-target.bin"}]}],
    }


def _external_stats():
    return [{"t_epoch_ms": 1000, "cpu_percent": 0.5, "memory_percent": 3.0}]


def test_external_summary_does_not_target_early_stable_cardinality_while_cpu_is_high():
    samples = [
        {"sample_index": 0, "root_count": 2, "model_version": 4, "app_cpu_percent": 80.0},
        {"sample_index": 1, "root_count": 2, "model_version": 4, "app_cpu_percent": 70.0},
        {"sample_index": 2, "root_count": 2, "model_version": 4, "app_cpu_percent": 60.0},
    ]
    summary = summarize_external(samples, _external_stats(), _external_manifest(), "candidate", "off",
                                  {"model_target_epoch_ms": 3000}, target_index=2,
                                  remote_docker_stats=_external_stats())
    assert summary["target_readiness"]["model_stable_consecutive_samples"] == 3
    assert summary["target_readiness"]["app_cpu_quiet_consecutive_samples"] == 0
    assert summary["target_readiness"]["ready"] is False
    assert summary["acceptance_checks"]["model_target_readiness"] is False
    assert summary["acceptance_valid"] is False


def test_external_summary_targets_only_after_consecutive_quiet_samples_and_keeps_zero_based_index():
    samples = [
        {"sample_index": 0, "root_count": 2, "model_version": 4, "app_cpu_percent": 80.0},
        {"sample_index": 1, "root_count": 2, "model_version": 4, "app_cpu_percent": 70.0},
        {"sample_index": 2, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.8},
        {"sample_index": 3, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.7},
        {"sample_index": 4, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.6},
    ]
    summary = summarize_external(samples, _external_stats(), _external_manifest(), "candidate", "off",
                                  {"model_target_epoch_ms": 5000}, target_index=4,
                                  remote_docker_stats=_external_stats())
    assert summary["target_readiness"]["target_sample_index_valid"] is True
    assert summary["target_readiness"]["app_cpu_quiet_consecutive_samples"] == 3
    assert summary["target_readiness"]["ready"] is True
    assert summary["full_scan_phase"]["sample_count"] == 5


def test_external_summary_resets_readiness_on_model_version_or_cardinality_change():
    samples = [
        {"sample_index": 0, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.5},
        {"sample_index": 1, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.5},
        {"sample_index": 2, "root_count": 2, "model_version": 5, "app_cpu_percent": 0.5},
        {"sample_index": 3, "root_count": 2, "model_version": 5, "app_cpu_percent": 0.5},
        {"sample_index": 4, "root_count": 2, "model_version": 5, "app_cpu_percent": 0.5},
    ]
    before_quiet = summarize_external(samples, _external_stats(), _external_manifest(), "candidate", "off",
                                       {"model_target_epoch_ms": 3000}, target_index=2,
                                       remote_docker_stats=_external_stats())
    after_quiet = summarize_external(samples, _external_stats(), _external_manifest(), "candidate", "off",
                                      {"model_target_epoch_ms": 5000}, target_index=4,
                                      remote_docker_stats=_external_stats())
    assert before_quiet["target_readiness"]["ready"] is False
    assert before_quiet["target_readiness"]["model_stable_consecutive_samples"] == 1
    assert after_quiet["target_readiness"]["ready"] is True
    assert after_quiet["target_readiness"]["reset_on_model_change"] is True


def test_external_summary_rejects_target_index_mismatch_and_missing_samples():
    samples = [
        {"sample_index": 0, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.5},
        {"sample_index": 1, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.5},
        {"sample_index": 4, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.5},
    ]
    mismatch = summarize_external(samples, _external_stats(), _external_manifest(), "candidate", "off",
                                   {"model_target_epoch_ms": 3000}, target_index=2,
                                   remote_docker_stats=_external_stats())
    missing = summarize_external([], [], _external_manifest(), "candidate", "off", {}, target_index=0)
    assert mismatch["target_readiness"]["target_sample_index_valid"] is False
    assert mismatch["acceptance_valid"] is False
    assert missing["target_readiness"]["target_sample_index_valid"] is False
    assert missing["acceptance_checks"]["model_target_readiness"] is False
    assert missing["acceptance_valid"] is False


def test_external_summary_requires_full_post_target_observation_window():
    samples = [
        {"sample_index": 0, "root_count": 2, "model_version": 4, "app_cpu_percent": 80.0},
        {"sample_index": 1, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.8},
        {"sample_index": 2, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.7},
        {"sample_index": 3, "root_count": 2, "model_version": 4, "app_cpu_percent": 0.6},
    ]
    summary = summarize_external(
        samples, [{"t_epoch_ms": 5000, "cpu_percent": 0.5, "memory_percent": 3.0}],
        _external_manifest(), "candidate", "off",
        {"model_target_epoch_ms": 4000, "post_scan_settled_idle_observation_ms": 149999,
         "post_target_observation_required_seconds": 149}, target_index=3,
        remote_docker_stats=[{"t_epoch_ms": 5000, "cpu_percent": 0.5, "memory_percent": 3.0}],
    )
    assert summary["target_readiness"]["ready"] is True
    assert summary["acceptance_checks"]["post_target_observation_complete"] is False
    assert summary["acceptance_valid"] is False


def test_external_stats_parser_drops_malformed_samples():
    summary = summarize_docker_stats([
        {"t_epoch_ms": 1, "cpu_percent": 1.0, "memory_percent": 2.0},
        {"t_epoch_ms": "bad", "cpu_percent": 3.0, "memory_percent": 4.0},
    ])
    assert summary["sample_count"] == 1


def _settled_diagnostics(cpus):
    metrics = {"model_update_builder_sync": {
        "count": 1, "total_wall_seconds": 0.01, "total_cpu_seconds": 0.01,
        "wall_time_percent": 1.0, "cpu_percent_one_core": 1.0,
    }}
    return {
        "schema": "seedsync.performance-diagnostics.v1",
        "counters": {"duration_spans_dropped": 0},
        "samples": [{
            "sequence": index + 1, "process_cpu_percent_one_core": cpu,
            "cgroup_cpu_percent_one_core": cpu,
            "gauges": {"model_tree_file_count": 200001},
            "stage_window": {"elapsed_seconds": 1.0, "metrics": metrics if index < 2 else {}},
        } for index, cpu in enumerate(cpus)],
    }


def test_app_cpu_acceptance_uses_settled_average_and_reports_peak_secondary():
    manifest = {"fixture_fingerprint": "fixture", "topology": {
        "expected_merged_model_tree_nodes": 200001,
        "expected_model_tree_file_count": 200001,
    }}
    passed = summarize(_settled_diagnostics([75, 80, 0.8, 1.2, 1.0]), manifest, "candidate",
                       min_high_cpu_samples=3, target_sequence=2, breadcrumb_mode="on")
    assert passed["app_cpu_threshold_percent"] == 1.0
    assert passed["app_cpu_average_percent_one_core"] == 1.0
    assert passed["app_cpu_peak_percent_one_core"] == 1.2
    assert passed["app_cpu_acceptance_pass"] is True
    assert passed["acceptance_valid"] is True

    failed = summarize(_settled_diagnostics([75, 80, 8.0, 12.5, 9.0]), manifest, "candidate",
                       min_high_cpu_samples=3, target_sequence=2, breadcrumb_mode="on")
    assert failed["app_cpu_average_percent_one_core"] > 1.0
    assert failed["app_cpu_peak_percent_one_core"] == 12.5
    assert failed["acceptance_valid"] is False


def test_capture_cli_enforces_app_average_gate_for_candidate_and_baseline(tmp_path):
    manifest = tmp_path / "manifest.json"
    diagnostics = tmp_path / "diagnostics.json"
    output = tmp_path / "summary.json"
    manifest.write_text(json.dumps({"fixture_fingerprint": "fixture", "topology": {
        "expected_merged_model_tree_nodes": 200001,
        "expected_model_tree_file_count": 200001,
    }}), encoding="utf-8")
    diagnostics.write_text(json.dumps(_settled_diagnostics([75, 80, 8.0, 12.5, 9.0])), encoding="utf-8")
    command = [sys.executable, str(PERF_DIR / "capture_metrics.py"),
               "--diagnostics", str(diagnostics), "--manifest", str(manifest),
               "--output", str(output), "--label"]
    candidate = subprocess.run(command + ["candidate", "--target-sequence", "2"], capture_output=True, text=True)
    baseline = subprocess.run(command + ["baseline", "--target-sequence", "2"], capture_output=True, text=True)
    capped = subprocess.run(command + ["candidate", "--target-sequence", "2",
                                       "--settled-idle-cpu-percent", "2.0"],
                            capture_output=True, text=True)
    assert candidate.returncode == 2
    assert baseline.returncode == 2
    assert capped.returncode == 2
    assert "no more than 1.0 for acceptance" in capped.stderr
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["app_cpu_average_percent_one_core"] > 1.0
    assert summary["acceptance_valid"] is False


def test_capture_cli_rejects_empty_remote_helper_series_in_diagnostics_on_and_off(tmp_path):
    manifest = tmp_path / "manifest.json"
    diagnostics = tmp_path / "diagnostics.json"
    app_stats = tmp_path / "app-stats.json"
    remote_stats = tmp_path / "remote-stats.json"
    model_summary = tmp_path / "model-summary.json"
    timing = tmp_path / "timing.json"
    output = tmp_path / "summary.json"
    manifest.write_text(json.dumps({"fixture_fingerprint": "fixture", "topology": {
        "expected_merged_model_tree_nodes": 200001,
        "expected_model_tree_file_count": 200001,
    }, "path_pairs": [{"enabled": True, "nodes_local": 64, "directory": "path-pair-01",
                        "remote_only_targets": []}]}), encoding="utf-8")
    diagnostics.write_text(json.dumps(_settled_diagnostics([75, 80, 0.8, 1.0, 1.0])), encoding="utf-8")
    app_stats.write_text(json.dumps({"samples": [
        {"t_epoch_ms": 1000, "cpu_percent": 0.8, "memory_percent": 2.0},
        {"t_epoch_ms": 2000, "cpu_percent": 0.9, "memory_percent": 2.1},
    ]}), encoding="utf-8")
    remote_stats.write_text(json.dumps({"samples": [
        {"t_epoch_ms": "invalid", "cpu_percent": -1, "memory_percent": "invalid"},
    ]}), encoding="utf-8")
    model_summary.write_text(json.dumps({"samples": [
        {"t_epoch_ms": 1000, "root_count": 1, "model_version": 1},
        {"t_epoch_ms": 2000, "root_count": 1, "model_version": 1},
    ]}), encoding="utf-8")
    timing.write_text(json.dumps({"model_target_epoch_ms": 1000}), encoding="utf-8")
    diagnostics_result = subprocess.run([
        sys.executable, str(PERF_DIR / "capture_metrics.py"), "--diagnostics", str(diagnostics),
        "--docker-stats", str(app_stats), "--manifest", str(manifest), "--output", str(output),
        "--label", "candidate", "--target-sequence", "2", "--phase-timing", str(timing),
    ], capture_output=True, text=True)
    external_result = subprocess.run([
        sys.executable, str(PERF_DIR / "capture_metrics.py"), "--mode", "external-summary",
        "--model-summary", str(model_summary), "--docker-stats", str(app_stats),
        "--remote-docker-stats", str(remote_stats),
        "--manifest", str(manifest), "--output", str(output), "--label", "candidate",
        "--target-sequence", "1", "--phase-timing", str(timing),
    ], capture_output=True, text=True)
    assert diagnostics_result.returncode == 2
    assert external_result.returncode == 2
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["acceptance_checks"]["remote_helper_stats_available"] is False
    assert summary["acceptance_valid"] is False


def test_lab_rejects_unsafe_run_ids_and_cpu_threshold_above_acceptance_cap():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    assert "^[a-z0-9][a-z0-9_-]{0,63}$" in lab_source
    assert '"run_id": os.environ["PERF_RUN_ID"]' not in lab_source
    assert '"run_id_digest": hashlib.sha256' in lab_source
    assert "> 1.0" in lab_source
    wsl = shutil.which("wsl")
    if wsl is None:
        pytest.skip("WSL is unavailable for shell-level validation checks")
    wsl_lab = str(PERF_DIR).replace("C:", "/mnt/c").replace("\\", "/") + "/lab.sh"
    for run_id in ("../escape", "a/b", "UpperCase", "a\\b"):
        command = f"PERF_IMAGE=synthetic PERF_RUN_ID={shlex.quote(run_id)} bash {shlex.quote(wsl_lab)} status"
        result = subprocess.run([wsl, "bash", "-c", command], capture_output=True, text=True)
        assert result.returncode == 2
        assert "PERF_RUN_ID must be a lowercase safe slug" in result.stderr
    command = f"PERF_IMAGE=synthetic PERF_SETTLED_IDLE_CPU_PERCENT=1.1 bash {shlex.quote(wsl_lab)} status"
    result = subprocess.run([wsl, "bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == 2
    assert "no more than 1.0 for acceptance" in result.stderr


def test_off_mode_summary_failure_breaks_quiet_readiness_suffix():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    failure_start = lab_source.index('> "$summary_path"; then')
    failure_end = lab_source.index("      summary_success_count=", failure_start)
    failure_block = lab_source[failure_start:failure_end]
    assert "summary_quiet_count=0" in failure_block
    assert failure_block.index("summary_quiet_count=0") < failure_block.index("continue")


def test_external_summary_separates_app_and_remote_helper_resource_roles():
    manifest = {"fixture_fingerprint": "fixture", "config_fingerprint": "config",
                "path_pairs": [{"enabled": True, "nodes_local": 64, "directory": "path-pair-01",
                                 "remote_only_targets": []}]}
    model_samples = [
        {"sample_index": 0, "t_epoch_ms": 1000, "root_count": 1, "model_version": 4, "app_cpu_percent": 9.0},
        {"sample_index": 1, "t_epoch_ms": 2000, "root_count": 1, "model_version": 4, "app_cpu_percent": 0.9},
        {"sample_index": 2, "t_epoch_ms": 3000, "root_count": 1, "model_version": 4, "app_cpu_percent": 0.8},
        {"sample_index": 3, "t_epoch_ms": 4000, "root_count": 1, "model_version": 4, "app_cpu_percent": 0.7},
    ]
    summary = summarize_external(
        model_samples,
        [{"t_epoch_ms": 1000, "cpu_percent": 1.2, "memory_percent": 3.0},
         {"t_epoch_ms": 3000, "cpu_percent": 0.8, "memory_percent": 3.1}],
        manifest, "candidate", "off", {"model_target_epoch_ms": 500,
                                         "post_scan_settled_idle_observation_ms": 150000,
                                         "post_target_observation_required_seconds": 150}, target_index=3,
        remote_docker_stats=[{"t_epoch_ms": 1000, "cpu_percent": 42.0, "memory_percent": 11.0},
                             {"t_epoch_ms": 3000, "cpu_percent": 38.0, "memory_percent": 11.2}],
    )
    assert summary["app_docker_stats"]["role"] == "app"
    assert summary["remote_helper_docker_stats"]["role"] == "remote-helper"
    assert summary["post_target_app_docker_stats"]["average_cpu_percent_one_core"] == 1.0
    assert summary["post_target_remote_helper_docker_stats"]["average_cpu_percent_one_core"] == 40.0
    assert summary["app_cpu_acceptance_pass"] is True


def test_docker_state_summaries_drop_private_inspect_payload_fields(tmp_path):
    sentinel = "PRIVATE_SENTINEL_TOKEN"
    private_fields = (
        "ENV_SECRET=" + sentinel,
        "/config/private-mount",
        "--command-with-secret",
        "com.example.private-label=" + sentinel,
        "sha256:private-container-id",
    )
    container_fields = "\t".join([
        "running", "true", "2026-01-01T00:00:00Z", "", "0", "seedsync:test",
        "linux/amd64", "0", "0", "0", "0", "0", "1", *private_fields,
    ])
    container_path = tmp_path / "container.json"
    sanitize_docker_state._container(str(container_path), "app", container_fields)
    container = json.loads(container_path.read_text(encoding="utf-8"))
    assert set(container) == {
        "schema", "role", "status", "running", "started_at", "finished_at",
        "restart_count", "image_tag_digest", "platform", "size_rw_bytes", "size_rootfs_bytes",
        "mount_count", "resource_limits",
    }
    assert set(container["resource_limits"]) == {"nano_cpus", "memory_bytes", "pids_limit"}

    image_fields = "\t".join(["amd64", "linux", "2026-01-01T00:00:00Z", "123", "2", "sha256:immutable-image", *private_fields])
    image_path = tmp_path / "image.json"
    sanitize_docker_state._image(str(image_path), "app", image_fields)
    image = json.loads(image_path.read_text(encoding="utf-8"))
    assert set(image) == {"schema", "role", "architecture", "os", "created_at", "size_bytes", "layer_count", "identity_digest"}
    assert len(image["identity_digest"]) == 64
    serialized = json.dumps({"container": container, "image": image})
    assert sentinel not in serialized
    assert "/config/private-mount" not in serialized
    assert "--command-with-secret" not in serialized
    assert "private-container-id" not in serialized
    assert "immutable-image" not in serialized


def test_lab_never_retains_raw_docker_logs_inspect_or_process_output():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    assert "docker logs" not in lab_source
    assert "docker inspect \"$app_id\" > \"$ARTIFACT_DIR" not in lab_source
    assert "docker top \"$app_id\" >" not in lab_source
    assert "app-exited-state.json" in lab_source


def test_lab_sanitizes_user_controlled_docker_identifiers_and_compose_state():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    sanitizer = (PERF_DIR / "sanitize_docker_state.py").read_text(encoding="utf-8")
    assert '"project_digest"' in lab_source
    assert '"image_tag_digest"' in lab_source
    assert '"remote_address_digest"' in lab_source
    assert "compose-state.v1" in lab_source
    assert 'compose ps >' not in lab_source
    assert 'compose ps | tee' not in lab_source
    assert '"image_tag_digest": _digest(image)' in sanitizer
    assert '"image": image' not in sanitizer


def test_browser_delete_local_requires_live_project_service_volume_and_marker_binding():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    browser_source = (PERF_DIR / "browser_probe.js").read_text(encoding="utf-8")
    assert "validate_browser_target_binding" in lab_source
    assert "com.docker.compose.project" in lab_source
    assert "com.docker.compose.service" in lab_source
    assert 'item.get("Destination") == "/mounts"' in lab_source
    assert "perf_local_fixture" in lab_source
    assert ".seedsync-performance-fixture.json" in lab_source
    assert "browser-target-binding.v1" in lab_source
    assert "container_id_digest" in lab_source and "source_name_digest" in lab_source
    assert "--binding" in lab_source and "bindingPath" in browser_source
    assert "validated live app/project/service/volume/fixture binding" in browser_source
    assert lab_source.index("validate_browser_target_binding") < lab_source.index(
        "PERF_BROWSER_DESTRUCTIVE_APPROVED" , lab_source.index("browser()")
    )


def test_readme_documents_mixed_profile_through_browser_and_cpu_gate():
    readme = (PERF_DIR / "README.md").read_text(encoding="utf-8")
    flow = readme[readme.index("export PERF_PROFILE=mixed"):readme.index("Worker self-check", readme.index("export PERF_PROFILE=mixed"))]
    browser_flow = readme[readme.index("For a browser timeline"):readme.index("The browser lane")]
    assert "PERF_PROFILE=mixed" in flow and "PERF_PROFILE=mixed" in browser_flow
    assert "lab.sh prepare" in flow and "lab.sh start" in browser_flow and "lab.sh browser candidate" in browser_flow
    assert "settled app-container CPU average" in readme
    assert "remote-helper" in readme


def test_compose_uses_isolated_default_network_with_optional_external_overlay():
    compose_source = (PERF_DIR / "compose.yml").read_text(encoding="utf-8")
    external_source = (PERF_DIR / "compose.external-network.yml").read_text(encoding="utf-8")

    assert "PERF_EXTERNAL_NETWORK" not in compose_source
    assert "external: true" in external_source
    assert "name: ${PERF_EXTERNAL_NETWORK:?PERF_EXTERNAL_NETWORK must name an existing Docker network}" \
        in external_source
    assert "${PERF_REMOTE_ADDRESS:?PERF_REMOTE_ADDRESS must name the external-network remote alias}" \
        in external_source


def test_compose_waits_for_remote_ssh_health_before_starting_app():
    compose_source = (PERF_DIR / "compose.yml").read_text(encoding="utf-8")
    remote_dockerfile = (PERF_DIR / "remote.Dockerfile").read_text(encoding="utf-8")

    assert "condition: service_healthy" in compose_source
    assert "HEALTHCHECK" in remote_dockerfile
    assert "ssh-keyscan -T 1 -p 1234 127.0.0.1" in remote_dockerfile


def test_measure_captures_one_sanitized_ownership_artifact_after_resource_measurement():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    ownership_capture = '"$phase_dir/ownership-final.json"'
    assert lab_source.count(ownership_capture) == 1
    assert lab_source.index('capture_metrics.py') < lab_source.index('capture_container_state "$phase_dir/final"')
    assert lab_source.index('capture_container_state "$phase_dir/final"') < lab_source.index(ownership_capture)
    ownership_helper = lab_source[lab_source.index("capture_ownership_census()") : lab_source.index("\nmeasure()")]
    timeout_match = re.search(r"--max-time\s+(\d+)", ownership_helper)
    assert timeout_match is not None and int(timeout_match.group(1)) >= 120
    assert "trap 'rm -f -- \"$raw_path\"' EXIT" in ownership_helper
    assert '"Authorization: Bearer $PERF_API_TOKEN"' in lab_source
    assert '"capture_status":"unavailable"' in lab_source


def test_measure_uses_compact_monitoring_and_defers_support_snapshots():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    measure_source = lab_source[lab_source.index("measure()") :]
    loop_source = measure_source[measure_source.index("while ((") : measure_source.index("done\n  if ((")]
    final_diagnostics = '> "$phase_dir/diagnostics.json"'
    final_breadcrumbs = '> "$phase_dir/breadcrumbs.json"'

    assert '"$diagnostics_url?limit=1"' in loop_source
    assert "/server/breadcrumbs/get" not in loop_source
    assert 'PERF_POST_TARGET_POLL_SECONDS="${PERF_POST_TARGET_POLL_SECONDS:-10}"' in lab_source
    assert 'sleep "$PERF_POST_TARGET_POLL_SECONDS"' in loop_source
    assert measure_source.count(final_diagnostics) == 1
    assert measure_source.count('"$base_url/server/breadcrumbs/get"') == 1
    assert measure_source.index(final_diagnostics) > measure_source.index("post_scan_settled_idle_observation_ms")
    assert measure_source.index(final_breadcrumbs) > measure_source.index(final_diagnostics)


def test_ownership_capture_validates_fixed_schema_and_drops_unexpected_fields():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    assert 'seedsync.memory-ownership-census.v1' in lab_source
    assert 'FIXED_OWNERS' in lab_source
    assert 'visited_object_count' in lab_source
    assert 'total_shallow_bytes' in lab_source
    assert 'graph_visited_node_count' in lab_source
    assert 'graph_truncated' in lab_source
    assert 'object_count' in lab_source
    assert 'shallow_bytes' in lab_source
    assert 'graph_node_count' in lab_source
    assert 'graph_shallow_bytes' in lab_source
    assert 'json.dumps(sanitized' in lab_source
    assert 'raw_path' in lab_source


def test_seeded_api_key_uses_current_store_hash_format(tmp_path):
    config_dir = tmp_path / "config"
    seed_config(config_dir, "local-test-token", pairs=2, breadcrumb_mode="on")
    store = ApiKeyStore.from_str((config_dir / "api-keys.json").read_text(encoding="utf-8"))
    assert len(store.api_keys) == 1
    assert _verify_secret("local-test-token", store.api_keys[0].secret_hash)
    assert "breadcrumb_trace_enabled = True" in (config_dir / "settings.cfg").read_text(encoding="utf-8")
    config = Config.from_file(str(config_dir / "settings.cfg"))
    assert config.general.breadcrumb_trace_enabled is True
    assert config.general.performance_diagnostics_sample_interval_seconds == 5
    assert config.general.disable_browser_auth is False
    assert config.general.config_api_redact_remote_details is True
    assert config.lftp.use_legacy_lftp_password_argv is False
    assert config.controller.interval_ms_local_scan == 86400000
    assert config.autoqueue.enabled is True
    assert config.notifications.enabled is False
    assert config.notifications.download_start is False
    assert config.notifications.download_complete is True
    assert config.notifications.extraction_complete is True
    assert config.notifications.delete_complete is True
    persisted = json.loads((config_dir / "controller.persist").read_text(encoding="utf-8"))
    assert len(persisted["move_failure_counts"]) == 1


def test_seed_config_accepts_unique_remote_address(tmp_path):
    config_dir = tmp_path / "config"

    seed_config(
        config_dir,
        "local-test-token",
        pairs=2,
        breadcrumb_mode="off",
        remote_address="seedsync-performance-lab-remote-1",
    )

    config = Config.from_file(str(config_dir / "settings.cfg"))
    assert config.lftp.remote_address == "seedsync-performance-lab-remote-1"


def test_seed_can_omit_move_failure_for_trigger_isolation(tmp_path):
    config_dir = tmp_path / "config"
    seed_config(config_dir, "local-test-token", pairs=2, breadcrumb_mode="on", move_failure_mode="none")
    persisted = json.loads((config_dir / "controller.persist").read_text(encoding="utf-8"))
    assert persisted["move_failure_counts"] == {}
    if os.name == "posix":
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o770


def test_mixed_disabled_pair_is_absent_from_seeded_persist(tmp_path):
    config_dir = tmp_path / "config"
    seed_config(config_dir, "local-test-token", profile="mixed", high_card_enabled=False)
    persisted = json.loads((config_dir / "controller.persist").read_text(encoding="utf-8"))
    assert persisted["downloaded"]
    assert all(json.loads(file_id)[0] == "pair-01" for file_id in persisted["downloaded"])


def test_mixed_rate_limit_is_recorded_and_uniform_default_is_unchanged(tmp_path):
    mixed_dir = tmp_path / "mixed-config"
    uniform_dir = tmp_path / "uniform-config"
    seed_config(mixed_dir, "local-test-token", profile="mixed", high_card_enabled=False)
    seed_config(uniform_dir, "local-test-token", pairs=2, profile="uniform")

    mixed_config = Config.from_file(str(mixed_dir / "settings.cfg"))
    uniform_config = Config.from_file(str(uniform_dir / "settings.cfg"))
    assert int(mixed_config.lftp.rate_limit) == 2_000_000
    assert int(uniform_config.lftp.rate_limit) == 0
    mixed_pairs = json.loads((mixed_dir / "path_pairs.json").read_text(encoding="utf-8"))
    assert mixed_pairs["rate_limit_bytes_per_second"] == 2_000_000
    assert mixed_pairs["diagnostics_mode"] == "on"


def test_diagnostics_mode_is_seeded_and_recorded(tmp_path):
    config_dir = tmp_path / "config"
    seed_config(config_dir, "local-test-token", diagnostics_mode="off")
    config = Config.from_file(str(config_dir / "settings.cfg"))
    assert config.general.performance_diagnostics_enabled is False
    payload = json.loads((config_dir / "path_pairs.json").read_text(encoding="utf-8"))
    assert payload["diagnostics_mode"] == "off"


def test_browser_harness_is_lazy_configurable_and_manifest_driven():
    source = (PERF_DIR / "browser_probe.js").read_text(encoding="utf-8")
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    assert "--self-test" in source
    assert "process.env.PERF_API_TOKEN" in source
    assert "require(moduleName)" in source
    assert "PERF_PLAYWRIGHT_MODULE" in source
    assert "NODE_PATH" in lab_source
    assert "ordinary-active" in source and "remote_only_targets" in source
    assert "active-queue-target" not in source and "remote-only-target.bin" not in source
    assert "path_segments: parts[0] === pair.directory ? parts.slice(1) : parts" in source
    assert "selectPairFromSidebar" in source and "visibleRowByName" in source
    assert "MutationObserver" in source and "EventSource" in source
    assert "eventSourceApply" in source and "receive_to_dom_ms" in source
    assert "target_dom_latency" in source and "latencyStats" in source
    assert "latestRelevantApply" in source and "/server/model/v1/" in source
    assert "String(item.pathname || '') === String(scopedPath || '')" in source
    assert "findScopedModelPath" in source and "model-summary" not in source
    assert "target.pair_id === 'pair-01'" in source
    assert "JSON.stringify(target).includes('pair-01')" in source
    for event_name in ("model-page", "model-invalidate", "model-patch", "model-reset"):
        assert event_name in source
    assert "lastSignature" in source and "if (signature === lastSignature) return" in source
    assert "waitForEnabledAction" in source
    assert "control_enabled_wait_ms" in source
    progress_gap = source[source.index("function progressGap"):source.index("function latencyStats")]
    assert "progress > 0 && progress < 100" in progress_gap
    assert "status !== 'stopped'" in progress_gap and "status !== 'downloaded'" in progress_gap
    assert "await exerciseActions(page, target, targetId, timeoutMs, evidence)" in source
    assert "readTargetId(page, target.name, timeoutMs)" in source
    assert "async function readTargetId(page, name, timeoutMs)" in source
    assert "const actions = evidence.actions" in source
    assert "const cleanupRecord =" in source and "record.steps.push" in source
    assert "evidence.actions = await exerciseActions" not in source
    assert "expected_request_aborts" in source and "net::ERR_ABORTED" in source
    assert "isNavigationRequest" in source and "resourceType" in source
    assert "waitForActiveMaterialization" in source
    active_materialization = source[
        source.index("async function waitForActiveMaterialization"):
        source.index("async function waitForEnabledAction")
    ]
    assert "progress > 0 && progress < 100" in active_materialization
    assert "status === 'downloading'" not in active_materialization
    finally_block = source[source.index("} finally {"):source.index("function baseEvidence")]
    assert "await closeQuietly(context);" in finally_block
    assert "await closeQuietly(browser);" in finally_block
    assert "['deleted']" in source
    assert "record.restored_state = 'deleted'" in source
    assert "run_id_digest" in source and "image_tag_digest" in source and "project_digest" in source
    assert "stableDigest" in source
    assert "image: runManifest" not in source and "project: runManifest" not in source
    assert "requestfailed" in source and "pageerror" in source
    assert "Remember browser" in source and "first-run claim" in source
    assert ".toLowerCase()" in source and "body.includes('remembered browser')" in source
    assert "browser)" in lab_source and "--run-manifest" in lab_source
    assert "PERF_HOST_PORT" in lab_source
    assert "PERF_BROWSER_DESTRUCTIVE_APPROVED" in source
    assert "PERF_BROWSER_DESTRUCTIVE_APPROVED" in lab_source
    assert "Browser probe Delete Local targets" in lab_source
    assert '"same_image_identity"' in lab_source
    assert 'identity_digest' in lab_source
    assert '--docker-stats "$app_docker_stats_series"' in lab_source
    assert '--remote-docker-stats "$remote_docker_stats_series"' in lab_source
    assert 'sample_docker_stats "$remote_docker_stats_series" remote-helper' in lab_source
    assert '"model_target_epoch_ms": target' in lab_source
    assert "summary_success_count" in lab_source
    assert 'target_index="$summary_sample_index"' in lab_source
    assert 'external_target_argument=(--target-sequence "$target_index")' in lab_source
    assert "PERF_EXTERNAL_READINESS_SAMPLES=3" in lab_source
    assert "--image-identity-digest" in lab_source
    assert "image_identity_digest" in source and "image_tag_digest" in source
    assert "normalizeAppPath" in source and "<scope-digest:" in source
    action_slice = source[source.index("async function exerciseActions"):source.index("function finalizeEvidence")]
    assert "try {" in action_slice and "finally" in action_slice
    assert "residual_state" in source and "cleanup.pass" in source
    assert "PERF_DIAGNOSTICS_MODE" in lab_source
    assert "model/v1/summary" in lab_source
    assert "stats-sample" in lab_source and "docker-stats-series" in lab_source
    assert "external-summary" in lab_source
    assert "diagnostics-mode" in (PERF_DIR / "compose.yml").read_text(encoding="utf-8")
    main_slice = source[source.find("async function main"):source.find("function baseEvidence")]
    assert "process.env.PERF_API_TOKEN" in main_slice
    assert "--api-token" not in main_slice


def test_browser_self_test_is_documented_as_worker_check():
    readme = (PERF_DIR / "README.md").read_text(encoding="utf-8")
    assert "browser_probe.js --self-test" in readme
    assert "worker self-check" in readme.lower()
    assert "verifier/final validation" in readme


def test_browser_probe_self_test_runs_without_playwright():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for the browser probe self-test")
    result = subprocess.run(
        [node, str(PERF_DIR / "browser_probe.js"), "--self-test"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "seedsync.performance-lab.browser-self-test.v1"
    assert payload["pass"] is True


def test_browser_probe_refuses_delete_actions_without_explicit_approval(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for the browser probe approval check")
    manifest_path = tmp_path / "fixture-manifest.json"
    output_path = tmp_path / "browser-timeline.json"
    manifest_path.write_text(json.dumps({
        "schema": "seedsync.performance-lab.fixture.v1",
        "synthetic_only": True,
        "path_pairs": [{
            "id": "pair-01",
            "name": "Performance Pair 01",
            "role": "ordinary-active",
            "directory": "path-pair-01",
            "local_path": "/mounts/path-pair-01",
            "remote_only_targets": [{"relative_path": "path-pair-01/remote-only-target.bin"}],
        }],
    }), encoding="utf-8")
    environment = os.environ.copy()
    environment["PERF_API_TOKEN"] = "synthetic-test-token"
    environment.pop("PERF_BROWSER_DESTRUCTIVE_APPROVED", None)
    result = subprocess.run([
        node, str(PERF_DIR / "browser_probe.js"),
        "--label", "candidate",
        "--base-url", "http://127.0.0.1:18800",
        "--manifest", str(manifest_path),
        "--output", str(output_path),
    ], capture_output=True, text=True, check=False, env=environment)
    assert result.returncode != 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["pass"] is False
    assert payload["failure_classification"] == "destructive-approval"
    assert "PERF_BROWSER_DESTRUCTIVE_APPROVED=on" in result.stderr
