#!/usr/bin/env python3
"""Create a deterministic, synthetic local/remote performance topology.

``uniform`` is the historical six-pair x 32,000-files-per-side fixture.
``mixed`` adds one ordinary active pair and one >=200k-node idle pair.  The
normalized topology returned by :func:`normalize_topology_spec` is the shared
authority used by this generator and ``seed_config.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_PAIRS = 6
DEFAULT_NODES_PER_PAIR = 32_000
MIXED_ACTIVE_NODES = 64
MIXED_HIGH_CARD_NODES = 200_000
EXTENSIONS = (".bin", ".dat", ".json", ".part", ".txt", ".tmp")
PAIR_LOCAL_DIRECTORIES = ("path-pair-01", "path-pair-02", "path-pair-03", "path-pair-04", "path-pair-05", "path-pair-06")
TOPOLOGY_ALGORITHM = "index-stable-shared-branch-v2"


def normalize_topology_spec(
    profile: str = "uniform",
    pairs: int = DEFAULT_PAIRS,
    nodes_per_pair: int = DEFAULT_NODES_PER_PAIR,
    high_card_enabled: bool = True,
) -> dict[str, Any]:
    """Return one canonical, JSON-safe topology/config specification."""
    if profile not in {"uniform", "mixed"}:
        raise ValueError("profile must be uniform or mixed")
    if profile == "uniform":
        if pairs < 1 or pairs > len(PAIR_LOCAL_DIRECTORIES):
            raise ValueError("pairs must be between 1 and 6")
        if nodes_per_pair < 1:
            raise ValueError("nodes_per_pair must be positive")
        entries = [
            {
                "id": f"pair-{number:02d}",
                "directory": PAIR_LOCAL_DIRECTORIES[number - 1],
                "role": "ordinary",
                "nodes_local": nodes_per_pair,
                "nodes_remote": nodes_per_pair,
                "enabled": True,
                "auto_queue": True,
                "remote_only_targets": [],
            }
            for number in range(1, pairs + 1)
        ]
    else:
        # Keep the mixed profile deliberately small in pair count while making
        # the high-cardinality pair itself large enough to expose idle cost.
        if pairs not in {2, DEFAULT_PAIRS}:
            raise ValueError("mixed profile uses exactly two pairs")
        if nodes_per_pair != DEFAULT_NODES_PER_PAIR:
            raise ValueError("mixed profile does not accept nodes_per_pair overrides")
        entries = [
            {
                "id": "pair-01",
                "directory": PAIR_LOCAL_DIRECTORIES[0],
                "role": "ordinary-active",
                "nodes_local": MIXED_ACTIVE_NODES,
                "nodes_remote": MIXED_ACTIVE_NODES,
                "enabled": True,
                "auto_queue": False,
                "remote_only_targets": [
                    {
                        "relative_path": "path-pair-01/active-queue-target/remote-only-target.bin",
                        "size_bytes": 32 * 1024 * 1024,
                    }
                ],
            },
            {
                "id": "pair-02",
                "directory": PAIR_LOCAL_DIRECTORIES[1],
                "role": "high-cardinality-idle",
                "nodes_local": MIXED_HIGH_CARD_NODES,
                "nodes_remote": MIXED_HIGH_CARD_NODES,
                "enabled": bool(high_card_enabled),
                "auto_queue": True,
                "remote_only_targets": [],
            },
        ]
    return {
        "profile": profile,
        "algorithm": TOPOLOGY_ALGORITHM,
        "pairs": entries,
    }


def _data_spec(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": spec["profile"],
        "algorithm": spec["algorithm"],
        "pairs": [
            {
                "id": pair["id"],
                "directory": pair["directory"],
                "role": pair["role"],
                "nodes_local": pair["nodes_local"],
                "nodes_remote": pair["nodes_remote"],
                "remote_only_targets": pair["remote_only_targets"],
            }
            for pair in spec["pairs"]
        ],
    }


def topology_fingerprint(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_data_spec(spec), sort_keys=True).encode("utf-8")).hexdigest()


def config_fingerprint(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()


def _legacy_uniform_fingerprint(topology: dict[str, Any]) -> str:
    legacy = {
        key: value for key, value in topology.items()
        if key not in {
            "remote_only_directory_nodes", "expected_merged_nodes_by_pair",
            "expected_file_counts_by_pair", "enabled_pair_ids",
            "enabled_expected_merged_model_tree_nodes",
            "enabled_expected_model_tree_file_count",
            "local_file_nodes", "remote_file_nodes",
            "local_directory_nodes", "remote_directory_nodes",
        }
    }
    return hashlib.sha256(json.dumps(legacy, sort_keys=True).encode("utf-8")).hexdigest()


def _node_parts(pair_directory: str, index: int) -> tuple[Path, str]:
    depth = 1 + (index % 6)
    parts = [pair_directory, f"bucket-{index // 2048:03d}"]
    if depth >= 2:
        parts.append(f"branch-{(index // 512) % 4:02d}")
    if depth >= 3:
        parts.append(f"section-{(index // 128) % 4:02d}")
    if depth >= 4:
        parts.append(f"leaf-{(index // 64) % 2:02d}")
    if depth >= 5:
        parts.append(f"twig-{(index // 32) % 2:02d}")
    if depth >= 6:
        parts.append(f"shard-{(index // 16) % 2:02d}")
    return Path(*parts), f"node-{index:08d}{EXTENSIONS[index % len(EXTENSIONS)]}"


def _set_owner(path: Path, uid: int, gid: int, mode: int) -> None:
    try:
        os.chown(path, uid, gid)
    except (AttributeError, OSError):
        pass
    try:
        path.chmod(mode)
    except (AttributeError, OSError):
        pass


def _write_side(root: Path, pair_directory: str, nodes: int, uid: int, gid: int) -> tuple[Counter[int], Counter[str], int]:
    depth_counts: Counter[int] = Counter()
    extension_counts: Counter[str] = Counter()
    pair_root = root / pair_directory
    directory_paths: set[Path] = {pair_root}
    pair_root.mkdir(parents=True, exist_ok=True)
    _set_owner(pair_root, uid, gid, 0o775)
    for index in range(nodes):
        relative_dir, filename = _node_parts(pair_directory, index)
        destination_dir = root / relative_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        _set_owner(destination_dir, uid, gid, 0o775)
        directory_paths.update(root / Path(*relative_dir.parts[:offset]) for offset in range(2, len(relative_dir.parts) + 1))
        destination = destination_dir / filename
        destination.write_bytes(f"seedsync-performance:{pair_directory}/{index}\n".encode("ascii"))
        _set_owner(destination, uid, gid, 0o664)
        depth_counts[len(relative_dir.parts) - 1] += 1
        extension_counts[destination.suffix] += 1
    return depth_counts, extension_counts, len(directory_paths)


def _fixture_marker(root: Path) -> Path:
    return root / ".seedsync-performance-fixture.json"


def _manifest_for_spec(spec: dict[str, Any], topology: dict[str, Any], fixture_fp: str) -> dict[str, Any]:
    topology = _with_experiment_expectations(topology, spec)
    path_pairs = []
    for pair in spec["pairs"]:
        directory = pair["directory"]
        path_pairs.append({
            "id": pair["id"],
            "name": f"Performance Pair {int(pair['id'][-2:]):02d}",
            "local_path": f"/mounts/{directory}",
            "remote_path": f"/home/remoteuser/files/{directory}",
            "directory": directory,
            "role": pair["role"],
            "nodes_local": pair["nodes_local"],
            "nodes_remote": pair["nodes_remote"],
            "nodes_per_side": pair["nodes_local"] if pair["nodes_local"] == pair["nodes_remote"] else None,
            "enabled": pair["enabled"],
            "auto_queue": pair["auto_queue"],
            "remote_only_targets": pair["remote_only_targets"],
        })
    return {
        "schema": "seedsync.performance-lab.fixture.v1",
        "fixture_fingerprint": fixture_fp,
        "config_fingerprint": config_fingerprint(spec),
        "synthetic_only": True,
        "profile": spec["profile"],
        "data_topology_spec": _data_spec(spec),
        "experiment_spec": spec,
        "topology": topology,
        "path_pairs": path_pairs,
    }


def _with_experiment_expectations(topology: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Attach enabled-pair model expectations without changing data identity."""
    merged_by_pair = topology.get("expected_merged_nodes_by_pair")
    files_by_pair = topology.get("expected_file_counts_by_pair")
    if not isinstance(merged_by_pair, dict) or not isinstance(files_by_pair, dict):
        raise RuntimeError("fixture topology is missing per-pair model expectations")
    enabled_ids = {pair["id"] for pair in spec["pairs"] if pair["enabled"]}
    result = dict(topology)
    result["enabled_pair_ids"] = [pair["id"] for pair in spec["pairs"] if pair["enabled"]]
    result["enabled_expected_merged_model_tree_nodes"] = sum(
        int(merged_by_pair[pair_id]) for pair_id in enabled_ids
    )
    result["enabled_expected_model_tree_file_count"] = sum(
        int(files_by_pair[pair_id]) for pair_id in enabled_ids
    )
    return result


def _migrate_legacy_uniform_manifest(
    retained: dict[str, Any], marker_fingerprint: str, spec: dict[str, Any]
) -> dict[str, Any] | None:
    """Validate and migrate a pre-profile uniform marker in place."""
    if spec["profile"] != "uniform":
        return None
    topology = retained.get("topology")
    pairs = topology.get("pairs") if isinstance(topology, dict) else None
    nodes = topology.get("nodes_per_pair_per_side") if isinstance(topology, dict) else None
    if not isinstance(topology, dict) or not isinstance(pairs, int) or not isinstance(nodes, int):
        return None
    if pairs != len(spec["pairs"]) or nodes != spec["pairs"][0]["nodes_local"]:
        return None
    if topology.get("algorithm") != TOPOLOGY_ALGORITHM:
        return None
    retained_pairs = retained.get("path_pairs")
    expected_pairs = [(pair["id"], pair["directory"]) for pair in spec["pairs"]]
    if not isinstance(retained_pairs, list) or [
        (entry.get("id"), entry.get("directory"))
        for entry in retained_pairs if isinstance(entry, dict)
    ] != expected_pairs:
        return None
    if retained.get("fixture_fingerprint") != marker_fingerprint:
        return None
    calculated = hashlib.sha256(json.dumps(topology, sort_keys=True).encode("utf-8")).hexdigest()
    if calculated != marker_fingerprint:
        return None
    directory_count = topology.get("directory_nodes_per_side")
    if not isinstance(directory_count, int) or directory_count < pairs:
        return None
    directory_per_pair = directory_count // pairs
    if directory_per_pair * pairs != directory_count:
        return None
    migrated = dict(topology)
    migrated["expected_merged_nodes_by_pair"] = {
        pair["id"]: nodes + directory_per_pair - 1 for pair in spec["pairs"]
    }
    migrated["expected_file_counts_by_pair"] = {pair["id"]: nodes for pair in spec["pairs"]}
    return migrated


def generate_fixture(
    local_root: Path,
    remote_root: Path,
    manifest_path: Path,
    pairs: int = DEFAULT_PAIRS,
    nodes_per_pair: int = DEFAULT_NODES_PER_PAIR,
    profile: str = "uniform",
    high_card_enabled: bool = True,
) -> dict[str, Any]:
    spec = normalize_topology_spec(profile, pairs, nodes_per_pair, high_card_enabled)
    local_root.mkdir(parents=True, exist_ok=True)
    remote_root.mkdir(parents=True, exist_ok=True)
    data_spec = _data_spec(spec)
    fixture_fp = topology_fingerprint(spec)
    existing_markers = []
    for root in (local_root, remote_root):
        marker = _fixture_marker(root)
        if marker.exists():
            existing_markers.append(json.loads(marker.read_text(encoding="utf-8")))
        elif any(root.iterdir()):
            raise RuntimeError("fixture volume is non-empty without a matching marker; refusing regeneration")
    if existing_markers:
        if len(existing_markers) != 2 or any(marker.get("fixture_fingerprint") != existing_markers[0].get("fixture_fingerprint") for marker in existing_markers):
            raise RuntimeError("retained fixture markers do not match requested filesystem topology; refusing regeneration")
        retained = existing_markers[0].get("manifest")
        if not isinstance(retained, dict):
            raise RuntimeError("retained fixture marker has an invalid topology fingerprint")
        if "data_topology_spec" not in retained:
            migrated_topology = _migrate_legacy_uniform_manifest(
                retained, existing_markers[0]["fixture_fingerprint"], spec
            )
            if migrated_topology is None:
                raise RuntimeError("retained legacy fixture marker does not match requested topology; refusing regeneration")
            manifest = _manifest_for_spec(spec, migrated_topology, existing_markers[0]["fixture_fingerprint"])
            marker_payload = {"fixture_fingerprint": existing_markers[0]["fixture_fingerprint"], "data_spec": data_spec, "manifest": manifest}
            for root in (local_root, remote_root):
                _fixture_marker(root).write_text(json.dumps(marker_payload, indent=2) + "\n", encoding="utf-8")
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            return manifest
        if retained.get("data_topology_spec") != data_spec:
            raise RuntimeError("retained fixture marker has a different topology; refusing regeneration")
        retained_fingerprint = existing_markers[0]["fixture_fingerprint"]
        if profile == "uniform":
            if _legacy_uniform_fingerprint(retained["topology"]) != retained_fingerprint:
                raise RuntimeError("retained fixture marker has an invalid legacy topology fingerprint")
            fixture_fp = retained_fingerprint
        elif retained_fingerprint != fixture_fp:
            raise RuntimeError("retained fixture marker has a different topology fingerprint; refusing regeneration")
        manifest = _manifest_for_spec(spec, retained["topology"], fixture_fp)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest

    pair_entries = []
    depth_histogram: Counter[int] = Counter()
    extension_histogram: Counter[str] = Counter()
    directory_count_per_side = 0
    remote_only_count = 0
    remote_only_directory_count = 0
    expected_nodes_by_pair: dict[str, int] = {}
    expected_file_counts_by_pair: dict[str, int] = {}
    for pair in spec["pairs"]:
        pair_id, directory = pair["id"], pair["directory"]
        local_depth, local_extensions, local_directories = _write_side(local_root, directory, pair["nodes_local"], 99, 100)
        remote_depth, remote_extensions, remote_directories = _write_side(remote_root, directory, pair["nodes_remote"], 1000, 1000)
        if local_depth != remote_depth or local_extensions != remote_extensions or local_directories != remote_directories:
            raise RuntimeError(f"fixture side topology diverged for {pair_id}")
        depth_histogram.update(local_depth)
        extension_histogram.update(local_extensions)
        for target in pair["remote_only_targets"]:
            target_path = remote_root / target["relative_path"]
            target_path.parent.mkdir(parents=True, exist_ok=True)
            chunk = ("seedsync-performance:remote-only-target\n" * 256).encode("ascii")
            with target_path.open("wb") as handle:
                remaining = target["size_bytes"]
                while remaining:
                    payload = chunk[:remaining]
                    handle.write(payload)
                    remaining -= len(payload)
            _set_owner(target_path.parent, 1000, 1000, 0o775)
            _set_owner(target_path, 1000, 1000, 0o664)
            remote_only_count += 1
            remote_only_directory_count += 1
        directory_count_per_side += local_directories
        expected_nodes_by_pair[pair_id] = (
            pair["nodes_local"] + local_directories - 1
            + len(pair["remote_only_targets"]) * 2
        )
        expected_file_counts_by_pair[pair_id] = pair["nodes_local"] + len(pair["remote_only_targets"])
    file_nodes_per_side = sum(pair["nodes_local"] for pair in spec["pairs"])
    remote_file_nodes = sum(pair["nodes_remote"] for pair in spec["pairs"]) + remote_only_count
    local_directory_nodes = directory_count_per_side
    remote_directory_nodes = directory_count_per_side + remote_only_directory_count
    topology = {
        "pairs": len(spec["pairs"]),
        "nodes_per_pair_per_side": nodes_per_pair if profile == "uniform" else None,
        "total_node_count": sum(pair["nodes_local"] + pair["nodes_remote"] for pair in spec["pairs"]) + remote_only_count,
        "depth_histogram_per_side": {str(key): value for key, value in sorted(depth_histogram.items())},
        "extension_histogram_per_side": dict(sorted(extension_histogram.items())),
        "algorithm": TOPOLOGY_ALGORITHM,
        "file_nodes_per_side": file_nodes_per_side,
        "physical_file_nodes_per_side": file_nodes_per_side,
        "local_file_nodes": file_nodes_per_side,
        "remote_file_nodes": remote_file_nodes,
        "total_file_node_count_both_sides": sum(pair["nodes_local"] + pair["nodes_remote"] for pair in spec["pairs"]) + remote_only_count,
        "directory_nodes_per_side": directory_count_per_side,
        "remote_only_directory_nodes": remote_only_directory_count,
        "local_directory_nodes": local_directory_nodes,
        "remote_directory_nodes": remote_directory_nodes,
        "physical_nodes_both_sides": (file_nodes_per_side + directory_count_per_side) * 2 + remote_only_count + remote_only_directory_count,
        "expected_merged_model_tree_nodes": file_nodes_per_side + directory_count_per_side - len(spec["pairs"]) + remote_only_count + remote_only_directory_count,
        "expected_model_tree_file_count": file_nodes_per_side + remote_only_count,
        "expected_merged_nodes_by_pair": expected_nodes_by_pair,
        "expected_file_counts_by_pair": expected_file_counts_by_pair,
    }
    fixture_fp = _legacy_uniform_fingerprint(topology) if profile == "uniform" else fixture_fp
    manifest = _manifest_for_spec(spec, topology, fixture_fp)
    marker_payload = {"fixture_fingerprint": fixture_fp, "data_spec": data_spec, "manifest": manifest}
    for root in (local_root, remote_root):
        _fixture_marker(root).write_text(json.dumps(marker_payload, indent=2) + "\n", encoding="utf-8")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--remote-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=DEFAULT_PAIRS)
    parser.add_argument("--nodes-per-pair", type=int, default=DEFAULT_NODES_PER_PAIR)
    parser.add_argument("--profile", choices=("uniform", "mixed"), default="uniform")
    parser.add_argument("--high-card-enabled", choices=("on", "off"), default="on")
    args = parser.parse_args()
    manifest = generate_fixture(
        args.local_root, args.remote_root, args.manifest, args.pairs, args.nodes_per_pair,
        args.profile, args.high_card_enabled == "on",
    )
    print(json.dumps({
        "fixture_fingerprint": manifest["fixture_fingerprint"],
        "config_fingerprint": manifest["config_fingerprint"],
        "total_node_count": manifest["topology"]["total_node_count"],
        "pairs": manifest["topology"]["pairs"],
        "profile": manifest["profile"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
