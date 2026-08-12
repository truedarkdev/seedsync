import json
import hashlib
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
    assert target["relative_path"].endswith("remote-only-target.bin")
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
    assert payload["path_pairs"][0]["remote_only_targets"][0]["relative_path"].endswith("remote-only-target.bin")


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
    assert disabled_manifest["topology"]["local_directory_nodes"] + 1 == disabled_manifest["topology"]["remote_directory_nodes"]
    target = remote_root / "path-pair-01" / "active-queue-target" / "remote-only-target.bin"
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
