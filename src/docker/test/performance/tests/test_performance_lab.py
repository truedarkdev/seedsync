import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

PERF_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PERF_DIR))
sys.path.insert(0, str(PERF_DIR.parents[2] / "python"))

from capture_metrics import FIXED_METRICS, latest_model_status, summarize
from generate_fixture import generate_fixture
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
    assert summary["settled_idle_phase"]["sample_count"] == 1
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
        "restart_count", "image", "platform", "size_rw_bytes", "size_rootfs_bytes",
        "mount_count", "resource_limits",
    }
    assert set(container["resource_limits"]) == {"nano_cpus", "memory_bytes", "pids_limit"}

    image_fields = "\t".join(["amd64", "linux", "2026-01-01T00:00:00Z", "123", "2", *private_fields])
    image_path = tmp_path / "image.json"
    sanitize_docker_state._image(str(image_path), "app", image_fields)
    image = json.loads(image_path.read_text(encoding="utf-8"))
    assert set(image) == {"schema", "role", "architecture", "os", "created_at", "size_bytes", "layer_count"}
    serialized = json.dumps({"container": container, "image": image})
    assert sentinel not in serialized
    assert "/config/private-mount" not in serialized
    assert "--command-with-secret" not in serialized
    assert "private-container-id" not in serialized


def test_lab_never_retains_raw_docker_logs_inspect_or_process_output():
    lab_source = (PERF_DIR / "lab.sh").read_text(encoding="utf-8")
    assert "docker logs" not in lab_source
    assert "docker inspect \"$app_id\" >" not in lab_source
    assert "docker top \"$app_id\" >" not in lab_source
    assert "app-exited-state.json" in lab_source


def test_compose_uses_isolated_default_network_with_optional_external_overlay():
    compose_source = (PERF_DIR / "compose.yml").read_text(encoding="utf-8")
    external_source = (PERF_DIR / "compose.external-network.yml").read_text(encoding="utf-8")

    assert "PERF_EXTERNAL_NETWORK" not in compose_source
    assert "external: true" in external_source
    assert "name: ${PERF_EXTERNAL_NETWORK:?PERF_EXTERNAL_NETWORK must name an existing Docker network}" \
        in external_source


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


def test_seed_can_omit_move_failure_for_trigger_isolation(tmp_path):
    config_dir = tmp_path / "config"
    seed_config(config_dir, "local-test-token", pairs=2, breadcrumb_mode="on", move_failure_mode="none")
    persisted = json.loads((config_dir / "controller.persist").read_text(encoding="utf-8"))
    assert persisted["move_failure_counts"] == {}
    if os.name == "posix":
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o770
