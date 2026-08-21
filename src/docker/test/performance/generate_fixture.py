#!/usr/bin/env python3
"""Create a deterministic, synthetic local/remote performance topology.

``uniform`` is the historical six-pair x 32,000-files-per-side fixture.
``mixed`` adds one ordinary active pair and one >=200k-node idle pair.
``cadence`` adds one asymmetric active pair and five small isolation pairs.
The normalized topology returned by :func:`normalize_topology_spec` is the shared
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
CADENCE_ACTIVE_LOCAL_NODES = 1_200
CADENCE_ACTIVE_REMOTE_NODES = 1_149
CADENCE_ACTIVE_SHARED_NODES = 750
CADENCE_ACTIVE_LOCAL_ONLY_NODES = 450
CADENCE_ACTIVE_REMOTE_ONLY_NODES = 399
CADENCE_ACTIVE_FRACTIONAL_MTIME_COUNT = 12
CADENCE_ISOLATION_NODES = 12
CADENCE_TARGET_FILE_COUNT = 1_199
CADENCE_TARGET_DIRECTORY_COUNT = 21
CADENCE_TARGET_MAX_DEPTH = 3
CADENCE_TARGET_LARGE_FILE_COUNT = 1_001
CADENCE_TARGET_LARGE_FILE_SIZE_BYTES = 1_316 * 1024 * 1024
CADENCE_TARGET_SMALL_FILE_SIZE_BYTES = 64 * 1024
CADENCE_TARGET_SIZE_BYTES = (
    CADENCE_TARGET_LARGE_FILE_COUNT * CADENCE_TARGET_LARGE_FILE_SIZE_BYTES
    + (CADENCE_TARGET_FILE_COUNT - CADENCE_TARGET_LARGE_FILE_COUNT)
    * CADENCE_TARGET_SMALL_FILE_SIZE_BYTES
)
CADENCE_TARGET_STORAGE_SIZE_BYTES = (
    CADENCE_TARGET_LARGE_FILE_SIZE_BYTES + CADENCE_TARGET_SMALL_FILE_SIZE_BYTES
)
CADENCE_TARGET_DIRECTORY_NAME = "remote-workload"
EXTENSIONS = (".bin", ".dat", ".json", ".part", ".txt", ".tmp")
PAIR_LOCAL_DIRECTORIES = ("path-pair-01", "path-pair-02", "path-pair-03", "path-pair-04", "path-pair-05", "path-pair-06")
TOPOLOGY_ALGORITHM = "index-stable-shared-branch-v2"
CADENCE_FIELDS = (
    "layout", "shared_nodes", "local_only_nodes", "remote_only_nodes",
    "fractional_mtime_overlap_count", "fractional_mtime_overlaps", "staging_case",
)


def normalize_topology_spec(
    profile: str = "uniform",
    pairs: int = DEFAULT_PAIRS,
    nodes_per_pair: int = DEFAULT_NODES_PER_PAIR,
    high_card_enabled: bool = True,
) -> dict[str, Any]:
    """Return one canonical, JSON-safe topology/config specification."""
    if profile not in {"uniform", "mixed", "cadence"}:
        raise ValueError("profile must be uniform, mixed, or cadence")
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
    elif profile == "mixed":
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
                        # Keep the active remote-only file at the pair root so
                        # the dashboard's v1 root-page transport renders it
                        # directly; child pages are not part of this harness.
                        "relative_path": "path-pair-01/remote-only-target.bin",
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
    else:
        if pairs != DEFAULT_PAIRS:
            raise ValueError("cadence profile uses exactly six pairs")
        if nodes_per_pair != DEFAULT_NODES_PER_PAIR:
            raise ValueError("cadence profile does not accept nodes_per_pair overrides")
        active_directory = "active-01"
        active_id = "path-pair-01"
        shared = CADENCE_ACTIVE_SHARED_NODES
        local_only = CADENCE_ACTIVE_LOCAL_ONLY_NODES
        remote_only = CADENCE_ACTIVE_REMOTE_ONLY_NODES
        if shared + local_only != CADENCE_ACTIVE_LOCAL_NODES:
            raise RuntimeError("cadence local node constants are inconsistent")
        if shared + remote_only != CADENCE_ACTIVE_REMOTE_NODES:
            raise RuntimeError("cadence remote node constants are inconsistent")
        target_relative_path = f"{active_directory}/{CADENCE_TARGET_DIRECTORY_NAME}"
        entries = [{
            "id": active_id,
            "name": "Path Pair 01",
            "directory": active_directory,
            "role": "ordinary-active",
            "nodes_local": CADENCE_ACTIVE_LOCAL_NODES,
            "nodes_remote": CADENCE_ACTIVE_REMOTE_NODES,
            "enabled": True,
            "auto_queue": False,
            "layout": "asymmetric-shared-sides-v1",
            "shared_nodes": shared,
            "local_only_nodes": local_only,
            "remote_only_nodes": remote_only,
            "fractional_mtime_overlap_count": CADENCE_ACTIVE_FRACTIONAL_MTIME_COUNT,
            "remote_only_targets": [{
                "kind": "directory",
                "relative_path": target_relative_path,
                "size_bytes": CADENCE_TARGET_SIZE_BYTES,
                "storage_mode": "real-bytes-hardlink-deduplicated",
                "storage_size_bytes": CADENCE_TARGET_STORAGE_SIZE_BYTES,
                "file_count": CADENCE_TARGET_FILE_COUNT,
                "directory_count": CADENCE_TARGET_DIRECTORY_COUNT,
                "max_depth": CADENCE_TARGET_MAX_DEPTH,
                "large_file_count": CADENCE_TARGET_LARGE_FILE_COUNT,
                "large_file_size_bytes": CADENCE_TARGET_LARGE_FILE_SIZE_BYTES,
                "small_file_size_bytes": CADENCE_TARGET_SMALL_FILE_SIZE_BYTES,
                "mtime_ns": 1_700_000_100_123_456_789,
            }],
        }]
        entries[0]["fractional_mtime_overlaps"] = _cadence_fractional_mtime_overlaps(entries[0])
        for number in range(2, DEFAULT_PAIRS + 1):
            entries.append({
                "id": f"path-pair-{number:02d}",
                "name": f"Path Pair {number:02d}",
                "directory": f"sample-{number:02d}",
                "role": "isolation",
                "nodes_local": CADENCE_ISOLATION_NODES,
                "nodes_remote": CADENCE_ISOLATION_NODES,
                "enabled": True,
                "auto_queue": True,
                "remote_only_targets": [],
            })
    return {
        "profile": profile,
        "algorithm": TOPOLOGY_ALGORITHM,
        "pairs": entries,
    }


def _data_spec(spec: dict[str, Any]) -> dict[str, Any]:
    pairs = []
    for pair in spec["pairs"]:
        entry = {
            "id": pair["id"],
            "directory": pair["directory"],
            "role": pair["role"],
            "nodes_local": pair["nodes_local"],
            "nodes_remote": pair["nodes_remote"],
            "remote_only_targets": pair["remote_only_targets"],
        }
        # These fields describe physical fixture identity.  Keep them out of
        # the historical profiles so their retained fingerprints remain
        # byte-for-byte compatible with existing volumes.
        for key in CADENCE_FIELDS:
            if key in pair:
                entry[key] = pair[key]
        pairs.append(entry)
    return {
        "profile": spec["profile"],
        "algorithm": spec["algorithm"],
        "pairs": pairs,
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
            "depth_histogram_local", "depth_histogram_remote",
            "extension_histogram_local", "extension_histogram_remote",
            "shared_file_nodes", "local_only_file_nodes", "remote_only_file_nodes",
            "shared_directory_nodes", "local_only_directory_nodes",
            "staging_file_nodes", "staging_directory_nodes", "staging_bytes",
            "fractional_mtime_overlap_count", "file_counts_by_pair",
            "directory_counts_by_pair",
            "summary_children_by_pair",
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


def _cadence_node_parts(pair_directory: str, zone: str, index: int) -> tuple[Path, str]:
    """Return stable, neutral paths for the asymmetric cadence pair."""
    parts = [
        pair_directory,
        zone,
        f"sample-bucket-{index // 64:03d}",
        f"sample-branch-{(index // 16) % 8:02d}",
        f"sample-leaf-{index % 8:02d}",
    ]
    return Path(*parts), f"sample-{zone}-{index:05d}{EXTENSIONS[index % len(EXTENSIONS)]}"


def _cadence_fractional_mtime_overlaps(pair: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    for index in range(int(pair["fractional_mtime_overlap_count"])):
        relative_dir, filename = _cadence_node_parts(pair["directory"], "shared", index)
        relative_path = (relative_dir / filename).as_posix()
        second = 1_700_000_000 + index
        cases.append({
            "relative_path": relative_path,
            "local_mtime_ns": second * 1_000_000_000 + 123_456_789,
            "remote_mtime_ns": second * 1_000_000_000 + 987_654_321,
        })
    return cases


def _cadence_target_directories(target: dict[str, Any]) -> list[Path]:
    """Return the target root and a stable 3-level directory distribution."""
    target_root = Path(*str(target["relative_path"]).split("/"))
    directories = [target_root]
    directories.extend(target_root / f"group-{index:02d}" for index in range(8))
    directories.extend(
        target_root / f"group-{index:02d}" / "branch-00"
        for index in range(7)
    )
    directories.extend(
        target_root / f"group-{index:02d}" / "branch-00" / "leaf-00"
        for index in range(5)
    )
    if len(directories) != int(target["directory_count"]):
        raise RuntimeError("cadence directory target constants are inconsistent")
    if max(len(path.parts) - len(target_root.parts) for path in directories) != int(target["max_depth"]):
        raise RuntimeError("cadence directory target depth is inconsistent")
    return directories


def _cadence_target_entries(target: dict[str, Any]) -> list[tuple[Path, str, int]]:
    """Build deterministic target file paths and real-byte sizes."""
    directories = _cadence_target_directories(target)
    file_count = int(target["file_count"])
    large_count = int(target["large_file_count"])
    if file_count < 1 or large_count < 1 or large_count > file_count:
        raise RuntimeError("cadence target file constants are inconsistent")
    entries = []
    for index in range(file_count):
        directory = directories[1 + (index % (len(directories) - 1))]
        size = (
            int(target.get("large_file_size_bytes", CADENCE_TARGET_LARGE_FILE_SIZE_BYTES))
            if index < large_count
            else int(target.get("small_file_size_bytes", CADENCE_TARGET_SMALL_FILE_SIZE_BYTES))
        )
        entries.append((directory, f"payload-{index:04d}.bin", size))
    expected_size = sum(size for _directory, _filename, size in entries)
    if expected_size != int(target["size_bytes"]):
        raise RuntimeError("cadence target aggregate size is inconsistent")
    return entries


def _write_real_bytes(destination: Path, size: int, seed: str) -> None:
    """Write deterministic non-sparse bytes without retaining large buffers."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    chunk = (digest * ((64 * 1024 + len(digest) - 1) // len(digest)))[:64 * 1024]
    with destination.open("wb") as handle:
        remaining = size
        while remaining:
            payload = chunk[:min(len(chunk), remaining)]
            handle.write(payload)
            remaining -= len(payload)


def _write_cadence_directory_target(
    root: Path, target: dict[str, Any], uid: int, gid: int
) -> tuple[set[str], set[Path], Counter[int], Counter[str]]:
    """Materialize one remote-only directory and return exact accounting."""
    entries = _cadence_target_entries(target)
    directories = _cadence_target_directories(target)
    for directory in directories:
        (root / directory).mkdir(parents=True, exist_ok=True)
        _set_owner(root / directory, uid, gid, 0o775)
    file_paths: set[str] = set()
    directory_paths: set[Path] = set(directories)
    depth_histogram: Counter[int] = Counter()
    extension_histogram: Counter[str] = Counter()
    template_paths: dict[int, Path] = {}
    for relative_dir, filename, size in entries:
        destination = relative_dir / filename
        destination_path = root / destination
        template_path = template_paths.get(size)
        if template_path is None:
            _write_real_bytes(
                destination_path,
                size,
                f"seedsync-performance-cadence:{destination.as_posix()}",
            )
            template_paths[size] = destination_path
        else:
            os.link(template_path, destination_path)
        _set_owner(root / destination, uid, gid, 0o664)
        file_paths.add(destination.as_posix())
        depth_histogram[len(relative_dir.parts) - len(Path(*str(target["relative_path"]).split("/")).parts)] += 1
        extension_histogram[(root / destination).suffix] += 1
    if target.get("mtime_ns") is not None:
        for directory in directories:
            os.utime(root / directory, ns=(target["mtime_ns"], target["mtime_ns"]))
    if len(file_paths) != int(target["file_count"]) or len(directory_paths) != int(target["directory_count"]):
        raise RuntimeError("cadence directory target cardinality does not match descriptor")
    return file_paths, directory_paths, depth_histogram, extension_histogram


def _side_entries(pair: dict[str, Any], side: str) -> list[tuple[Path, str]]:
    if pair.get("layout") != "asymmetric-shared-sides-v1":
        return [
            (relative_dir, filename)
            for index in range(pair[f"nodes_{side}"])
            for relative_dir, filename in (_node_parts(pair["directory"], index),)
        ]
    entries = [
        _cadence_node_parts(pair["directory"], "shared", index)
        for index in range(pair["shared_nodes"])
    ]
    if side == "local":
        entries.extend(
            _cadence_node_parts(pair["directory"], "final-only", index)
            for index in range(pair["local_only_nodes"])
        )
    else:
        entries.extend(
            _cadence_node_parts(pair["directory"], "remote-only", index)
            for index in range(pair["remote_only_nodes"])
        )
    return entries


def _set_owner(path: Path, uid: int, gid: int, mode: int) -> None:
    try:
        os.chown(path, uid, gid)
    except (AttributeError, OSError):
        pass
    try:
        path.chmod(mode)
    except (AttributeError, OSError):
        pass


def _write_entries(
    root: Path,
    entries: list[tuple[Path, str]],
    uid: int,
    gid: int,
    mtime_ns_by_path: dict[str, int] | None = None,
) -> tuple[Counter[int], Counter[str], int, set[Path]]:
    depth_counts: Counter[int] = Counter()
    extension_counts: Counter[str] = Counter()
    directory_paths: set[Path] = set()
    for relative_dir, filename in entries:
        for offset in range(1, len(relative_dir.parts) + 1):
            directory = root / Path(*relative_dir.parts[:offset])
            directory.mkdir(parents=True, exist_ok=True)
            _set_owner(directory, uid, gid, 0o775)
        destination_dir = root / relative_dir
        directory_paths.update(Path(*relative_dir.parts[:offset]) for offset in range(1, len(relative_dir.parts) + 1))
        destination = destination_dir / filename
        destination.write_bytes(f"seedsync-performance:{relative_dir.as_posix()}/{filename}\n".encode("ascii"))
        _set_owner(destination, uid, gid, 0o664)
        if mtime_ns_by_path and relative_dir.joinpath(filename).as_posix() in mtime_ns_by_path:
            mtime_ns = mtime_ns_by_path[relative_dir.joinpath(filename).as_posix()]
            os.utime(destination, ns=(mtime_ns, mtime_ns))
        depth_counts[len(relative_dir.parts) - 1] += 1
        extension_counts[destination.suffix] += 1
    return depth_counts, extension_counts, len(directory_paths), directory_paths


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


def _repair_retained_pair_root_ownership(
    local_root: Path, remote_root: Path, spec: dict[str, Any]
) -> None:
    """Restore the runtime ownership contract without changing fixture topology."""
    for pair in spec["pairs"]:
        directory = pair["directory"]
        for root, uid, gid in ((local_root, 99, 100), (remote_root, 1000, 1000)):
            pair_root = root / directory
            if pair_root.is_dir():
                _set_owner(pair_root, uid, gid, 0o775)


def _manifest_for_spec(spec: dict[str, Any], topology: dict[str, Any], fixture_fp: str) -> dict[str, Any]:
    topology = _with_experiment_expectations(topology, spec)
    path_pairs = []
    for pair in spec["pairs"]:
        directory = pair["directory"]
        entry = {
            "id": pair["id"],
            "name": pair.get("name", f"Performance Pair {int(pair['id'][-2:]):02d}"),
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
        }
        for key in CADENCE_FIELDS:
            if key in pair:
                entry[key] = pair[key]
        path_pairs.append(entry)
    staging_cases = [
        {"pair_id": pair["id"], **pair["staging_case"]}
        for pair in spec["pairs"] if "staging_case" in pair
    ]
    manifest = {
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
    if staging_cases:
        manifest["staging_cases"] = staging_cases
    return manifest


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


def _write_manifest_and_markers(
    local_root: Path,
    remote_root: Path,
    manifest_path: Path,
    data_spec: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    marker_payload = {
        "fixture_fingerprint": manifest["fixture_fingerprint"],
        "data_spec": data_spec,
        "manifest": manifest,
    }
    for root in (local_root, remote_root):
        _fixture_marker(root).write_text(json.dumps(marker_payload, indent=2) + "\n", encoding="utf-8")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _generate_cadence_fixture(
    local_root: Path,
    remote_root: Path,
    manifest_path: Path,
    spec: dict[str, Any],
    data_spec: dict[str, Any],
    fixture_fp: str,
) -> dict[str, Any]:
    """Generate the asymmetric cadence fixture and its accounting."""
    depth_histogram: Counter[int] = Counter()
    remote_depth_histogram: Counter[int] = Counter()
    extension_histogram: Counter[str] = Counter()
    remote_extension_histogram: Counter[str] = Counter()
    local_directory_paths: set[Path] = set()
    remote_directory_paths: set[Path] = set()
    staging_file_count = 0
    staging_directory_count = 0
    staging_bytes = 0
    expected_nodes_by_pair: dict[str, int] = {}
    expected_file_counts_by_pair: dict[str, int] = {}
    pair_file_counts: dict[str, dict[str, int]] = {}
    pair_directory_counts: dict[str, dict[str, int]] = {}
    summary_children_by_pair: dict[str, int] = {}
    for pair in spec["pairs"]:
        pair_id, directory = pair["id"], pair["directory"]
        local_entries = _side_entries(pair, "local")
        remote_entries = _side_entries(pair, "remote")
        fractional_cases = pair.get("fractional_mtime_overlaps", [])
        local_mtimes = {case["relative_path"]: case["local_mtime_ns"] for case in fractional_cases}
        remote_mtimes = {case["relative_path"]: case["remote_mtime_ns"] for case in fractional_cases}
        local_depth, local_extensions, local_directories, local_paths = _write_entries(
            local_root, local_entries, 99, 100, local_mtimes
        )
        remote_depth, remote_extensions, remote_directories, remote_paths = _write_entries(
            remote_root, remote_entries, 1000, 1000, remote_mtimes
        )
        depth_histogram.update(local_depth)
        remote_depth_histogram.update(remote_depth)
        extension_histogram.update(local_extensions)
        remote_extension_histogram.update(remote_extensions)
        local_directory_paths.update(local_paths)
        remote_directory_paths.update(remote_paths)
        target_file_paths: set[str] = set()
        target_directory_paths: set[Path] = set()
        for target in pair["remote_only_targets"]:
            if target.get("kind") == "directory":
                files, directories, target_depth, target_extensions = _write_cadence_directory_target(
                    remote_root, target, 1000, 1000
                )
                target_file_paths.update(files)
                target_directory_paths.update(directories)
                remote_paths.update(directories)
                remote_directory_paths.update(directories)
                remote_depth_histogram.update(target_depth)
                remote_extension_histogram.update(target_extensions)
                continue
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
            if target.get("mtime_ns") is not None:
                os.utime(target_path, ns=(target["mtime_ns"], target["mtime_ns"]))
            target_relative = target_path.relative_to(remote_root)
            target_file_paths.add(target_relative.as_posix())
            target_parent_paths = {
                Path(*target_relative.parts[:offset])
                for offset in range(1, len(target_relative.parts))
            }
            remote_paths.update(target_parent_paths)
            remote_directory_paths.update(target_parent_paths)
        # Optional staging cases are materialized only when explicitly present
        # in a topology spec; the cadence directory intentionally has none so
        # fresh startup remains remote-only and queueable.
        staging = pair.get("staging_case")
        if staging:
            staging_path = local_root / staging["relative_path"]
            status_path = local_root / staging["status_relative_path"]
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            _set_owner(staging_path.parent, 99, 100, 0o775)
            if staging.get("kind") == "directory":
                staging_path.mkdir(parents=True, exist_ok=True)
                child_path = local_root / staging["child_relative_path"]
                child_path.parent.mkdir(parents=True, exist_ok=True)
                _write_real_bytes(child_path, staging["size_bytes"], "seedsync-performance-partial-transfer")
                _set_owner(staging_path, 99, 100, 0o775)
                _set_owner(child_path, 99, 100, 0o664)
            else:
                staging_path.write_bytes(b"seedsync-performance:partial-transfer\n" * 128)
                with staging_path.open("r+b") as handle:
                    handle.truncate(staging["size_bytes"])
            status_path.write_text(
                "size={total}\n0.pos={completed}\n0.limit={total}\n".format(
                    total=staging["total_bytes"], completed=staging["completed_bytes"]
                ),
                encoding="ascii",
            )
            _set_owner(status_path, 99, 100, 0o664)
            if staging.get("kind") != "directory":
                _set_owner(staging_path, 99, 100, 0o664)
            os.utime(staging_path, ns=(staging["mtime_ns"], staging["mtime_ns"]))
            os.utime(status_path, ns=(staging["mtime_ns"], staging["mtime_ns"]))
            staging_file_count += 2
            staging_directory_count += 1
            staging_bytes += staging["size_bytes"] + status_path.stat().st_size
        local_files = {directory_path.joinpath(filename).as_posix() for directory_path, filename in local_entries}
        remote_files = {directory_path.joinpath(filename).as_posix() for directory_path, filename in remote_entries}
        target_files = target_file_paths
        union_files = local_files | remote_files | target_files
        remote_only_files = (remote_files | target_files) - local_files
        local_only_files = local_files - (remote_files | target_files)
        union_directories = local_paths | remote_paths
        expected_nodes_by_pair[pair_id] = len(union_files) + len(union_directories) - 1
        expected_file_counts_by_pair[pair_id] = len(union_files)
        summary_children_by_pair[pair_id] = len({
            parts[1] for file_path in union_files
            if (parts := file_path.split("/")) and len(parts) > 1 and parts[0] == directory
        })
        pair_file_counts[pair_id] = {
            "local": len(local_files),
            "remote": len(remote_files) + len(target_files),
            "shared": len(local_files & remote_files),
            "local_only": len(local_only_files),
            "remote_only": len(remote_only_files),
        }
        pair_directory_counts[pair_id] = {
            "local": len(local_paths),
            "remote": len(remote_paths),
            "shared": len(local_paths & remote_paths),
            "local_only": len(local_paths - remote_paths),
            "remote_only": len(remote_paths - local_paths),
        }
    file_nodes_per_side = sum(counts["local"] for counts in pair_file_counts.values())
    remote_file_nodes = sum(counts["remote"] for counts in pair_file_counts.values())
    local_directory_nodes = len(local_directory_paths)
    remote_directory_nodes = len(remote_directory_paths)
    local_only_directory_nodes = len(local_directory_paths - remote_directory_paths)
    remote_only_directory_nodes = len(remote_directory_paths - local_directory_paths)
    shared_directory_nodes = len(local_directory_paths & remote_directory_paths)
    total_file_nodes = file_nodes_per_side + remote_file_nodes
    expected_model_nodes = sum(expected_nodes_by_pair.values())
    expected_model_files = sum(expected_file_counts_by_pair.values())
    topology = {
        "pairs": len(spec["pairs"]),
        "nodes_per_pair_per_side": None,
        "total_node_count": total_file_nodes,
        "depth_histogram_per_side": {str(key): value for key, value in sorted(depth_histogram.items())},
        "depth_histogram_local": {str(key): value for key, value in sorted(depth_histogram.items())},
        "depth_histogram_remote": {str(key): value for key, value in sorted(remote_depth_histogram.items())},
        "extension_histogram_per_side": dict(sorted(extension_histogram.items())),
        "extension_histogram_local": dict(sorted(extension_histogram.items())),
        "extension_histogram_remote": dict(sorted(remote_extension_histogram.items())),
        "algorithm": TOPOLOGY_ALGORITHM,
        "file_nodes_per_side": file_nodes_per_side,
        "physical_file_nodes_per_side": file_nodes_per_side,
        "local_file_nodes": file_nodes_per_side,
        "remote_file_nodes": remote_file_nodes,
        "total_file_node_count_both_sides": total_file_nodes,
        "shared_file_nodes": sum(counts["shared"] for counts in pair_file_counts.values()),
        "local_only_file_nodes": sum(counts["local_only"] for counts in pair_file_counts.values()),
        "remote_only_file_nodes": sum(counts["remote_only"] for counts in pair_file_counts.values()),
        "directory_nodes_per_side": local_directory_nodes if local_directory_nodes == remote_directory_nodes else None,
        "shared_directory_nodes": shared_directory_nodes,
        "local_only_directory_nodes": local_only_directory_nodes,
        "remote_only_directory_nodes": remote_only_directory_nodes,
        "local_directory_nodes": local_directory_nodes,
        "remote_directory_nodes": remote_directory_nodes,
        "staging_file_nodes": staging_file_count,
        "staging_directory_nodes": staging_directory_count,
        "staging_bytes": staging_bytes,
        "fractional_mtime_overlap_count": sum(
            len(pair.get("fractional_mtime_overlaps", [])) for pair in spec["pairs"]
        ),
        "physical_nodes_both_sides": file_nodes_per_side + remote_file_nodes + local_directory_nodes + remote_directory_nodes + staging_file_count + staging_directory_count,
        "expected_merged_model_tree_nodes": expected_model_nodes,
        "expected_model_tree_file_count": expected_model_files,
        "expected_merged_nodes_by_pair": expected_nodes_by_pair,
        "expected_file_counts_by_pair": expected_file_counts_by_pair,
        "file_counts_by_pair": pair_file_counts,
        "directory_counts_by_pair": pair_directory_counts,
        "summary_children_by_pair": summary_children_by_pair,
    }
    manifest = _manifest_for_spec(spec, topology, fixture_fp)
    return _write_manifest_and_markers(
        local_root, remote_root, manifest_path, data_spec, manifest
    )

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
            _repair_retained_pair_root_ownership(local_root, remote_root, spec)
            return _write_manifest_and_markers(
                local_root, remote_root, manifest_path, data_spec, manifest
            )
        if retained.get("data_topology_spec") != data_spec:
            raise RuntimeError("retained fixture marker has a different topology; refusing regeneration")
        retained_fingerprint = existing_markers[0]["fixture_fingerprint"]
        if profile == "uniform":
            if _legacy_uniform_fingerprint(retained["topology"]) != retained_fingerprint:
                raise RuntimeError("retained fixture marker has an invalid legacy topology fingerprint")
            fixture_fp = retained_fingerprint
        elif retained_fingerprint != fixture_fp:
            raise RuntimeError("retained fixture marker has a different topology fingerprint; refusing regeneration")
        _repair_retained_pair_root_ownership(local_root, remote_root, spec)
        manifest = _manifest_for_spec(spec, retained["topology"], fixture_fp)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest

    if profile == "cadence":
        return _generate_cadence_fixture(
            local_root, remote_root, manifest_path, spec, data_spec, fixture_fp,
        )

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
            pair_root = remote_root / directory
            try:
                remote_only_directory_count += len(target_path.parent.relative_to(pair_root).parts)
            except ValueError:
                remote_only_directory_count += 0
        directory_count_per_side += local_directories
        expected_nodes_by_pair[pair_id] = (
            pair["nodes_local"] + local_directories - 1
            + sum(
                1 + len((remote_root / target["relative_path"]).parent.relative_to(remote_root / directory).parts)
                for target in pair["remote_only_targets"]
            )
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
    return _write_manifest_and_markers(
        local_root, remote_root, manifest_path, data_spec, manifest
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--remote-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=DEFAULT_PAIRS)
    parser.add_argument("--nodes-per-pair", type=int, default=DEFAULT_NODES_PER_PAIR)
    parser.add_argument("--profile", choices=("uniform", "mixed", "cadence"), default="uniform")
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
