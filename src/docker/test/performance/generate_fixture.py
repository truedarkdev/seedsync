#!/usr/bin/env python3
"""Create a deterministic, synthetic local/remote filesystem topology.

The default is 32,000 file nodes per side for each of six pairs.  Shared
directory branches add about 3,830 directories per pair. The pair roots are
not model nodes, producing roughly 214,974 merged model-tree nodes while keeping the physical fixture entirely
synthetic.  The script never reads host paths or production data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path


DEFAULT_PAIRS = 6
DEFAULT_NODES_PER_PAIR = 32_000
EXTENSIONS = (".bin", ".dat", ".json", ".part", ".txt", ".tmp")
PAIR_LOCAL_DIRECTORIES = ("path-pair-01", "path-pair-02", "path-pair-03", "path-pair-04", "path-pair-05", "path-pair-06")


def _node_parts(pair_id: str, index: int) -> tuple[Path, str]:
    depth = 1 + (index % 6)
    # Shared buckets keep directory cardinality realistic instead of creating
    # one unique directory chain per file.
    parts = [
        pair_id,
        "bucket-{0:03d}".format(index // 2048),
    ]
    if depth >= 2:
        parts.append("branch-{0:02d}".format((index // 512) % 4))
    if depth >= 3:
        parts.append("section-{0:02d}".format((index // 128) % 4))
    if depth >= 4:
        parts.append("leaf-{0:02d}".format((index // 64) % 2))
    if depth >= 5:
        parts.append("twig-{0:02d}".format((index // 32) % 2))
    if depth >= 6:
        parts.append("shard-{0:02d}".format((index // 16) % 2))
    extension = EXTENSIONS[index % len(EXTENSIONS)]
    return Path(*parts), "node-{0:08d}{1}".format(index, extension)


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
    directory_paths: set[Path] = set()
    pair_root.mkdir(parents=True, exist_ok=True)
    _set_owner(pair_root, uid, gid, 0o775)
    directory_paths.add(pair_root)
    for index in range(nodes):
        relative_dir, filename = _node_parts(pair_directory, index)
        destination_dir = root / relative_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        _set_owner(destination_dir, uid, gid, 0o775)
        directory_paths.update(
            root / Path(*relative_dir.parts[:offset])
            for offset in range(2, len(relative_dir.parts) + 1)
        )
        destination = destination_dir / filename
        # Keep content deterministic and small.  No media or user data is
        # represented by this fixture.
        payload = "seedsync-performance:{}/{}\n".format(pair_directory, index).encode("ascii")
        with destination.open("wb") as handle:
            handle.write(payload)
        _set_owner(destination, uid, gid, 0o664)
        depth_counts[len(relative_dir.parts) - 1] += 1
        extension_counts[destination.suffix] += 1
    return depth_counts, extension_counts, len(directory_paths)


def _fixture_marker(root: Path) -> Path:
    return root / ".seedsync-performance-fixture.json"


def generate_fixture(local_root: Path, remote_root: Path, manifest_path: Path, pairs: int, nodes_per_pair: int) -> dict[str, object]:
    if pairs < 1 or pairs > 6:
        raise ValueError("pairs must be between 1 and 6")
    if nodes_per_pair < 1:
        raise ValueError("nodes_per_pair must be positive")
    local_root.mkdir(parents=True, exist_ok=True)
    remote_root.mkdir(parents=True, exist_ok=True)
    expected_topology = {"pairs": pairs, "nodes_per_pair_per_side": nodes_per_pair,
                         "algorithm": "index-stable-shared-branch-v2"}
    request_fingerprint = hashlib.sha256(
        json.dumps(expected_topology, sort_keys=True).encode("utf-8")
    ).hexdigest()
    existing_markers = []
    for root in (local_root, remote_root):
        marker = _fixture_marker(root)
        if marker.exists():
            existing_markers.append(json.loads(marker.read_text(encoding="utf-8")))
        elif any(root.iterdir()):
            raise RuntimeError("fixture volume is non-empty without a matching marker; refusing regeneration")
    if existing_markers:
        if len(existing_markers) != 2 or any(
            marker.get("fixture_fingerprint") != existing_markers[0].get("fixture_fingerprint")
            for marker in existing_markers
        ):
            raise RuntimeError("retained fixture markers disagree; refusing regeneration")
        manifest = existing_markers[0].get("manifest")
        if not isinstance(manifest, dict):
            raise RuntimeError("retained fixture marker has no manifest")
        topology = manifest.get("topology")
        if not isinstance(topology, dict) or any(
            topology.get(name) != expected_topology[name] for name in expected_topology
        ):
            raise RuntimeError("retained fixture marker does not match requested topology; refusing regeneration")
        expected_pairs = [
            ("pair-{0:02d}".format(index), directory)
            for index, directory in enumerate(PAIR_LOCAL_DIRECTORIES[:pairs], start=1)
        ]
        retained_pairs = manifest.get("path_pairs")
        if not isinstance(retained_pairs, list) or [
            (entry.get("id"), entry.get("directory"))
            for entry in retained_pairs if isinstance(entry, dict)
        ] != expected_pairs:
            raise RuntimeError("retained fixture marker has a different path-pair layout; refusing regeneration")
        calculated_fingerprint = hashlib.sha256(
            json.dumps(topology, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if manifest.get("fixture_fingerprint") != calculated_fingerprint:
            raise RuntimeError("retained fixture marker has an invalid topology fingerprint")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest
    pair_entries = []
    depth_histogram: Counter[int] = Counter()
    extension_histogram: Counter[str] = Counter()
    directory_count_per_side = 0
    for pair_number in range(1, pairs + 1):
        pair_id = "pair-{0:02d}".format(pair_number)
        pair_directory = PAIR_LOCAL_DIRECTORIES[pair_number - 1]
        local_depth, local_extensions, local_directories = _write_side(
            local_root, pair_directory, nodes_per_pair, 99, 100
        )
        remote_depth, remote_extensions, remote_directories = _write_side(
            remote_root, pair_directory, nodes_per_pair, 1000, 1000
        )
        if local_depth != remote_depth or local_extensions != remote_extensions:
            raise RuntimeError("fixture side topology diverged for {}".format(pair_id))
        depth_histogram.update(local_depth)
        extension_histogram.update(local_extensions)
        if local_directories != remote_directories:
            raise RuntimeError("fixture directory topology diverged for {}".format(pair_id))
        directory_count_per_side += local_directories
        pair_entries.append({
            "id": pair_id,
            "name": "Performance Pair {0:02d}".format(pair_number),
            "local_path": "/mounts/{}".format(pair_directory),
            "remote_path": "/home/remoteuser/files/{}".format(pair_directory),
            "directory": pair_directory,
            "nodes_per_side": nodes_per_pair,
        })
    topology = {
        "pairs": pairs,
        "nodes_per_pair_per_side": nodes_per_pair,
        "total_node_count": pairs * nodes_per_pair * 2,
        "depth_histogram_per_side": {str(key): value * pairs for key, value in sorted(depth_histogram.items())},
        "extension_histogram_per_side": dict(sorted(extension_histogram.items())),
        "algorithm": "index-stable-shared-branch-v2",
        "file_nodes_per_side": pairs * nodes_per_pair,
        "physical_file_nodes_per_side": pairs * nodes_per_pair,
        "total_file_node_count_both_sides": pairs * nodes_per_pair * 2,
        "directory_nodes_per_side": directory_count_per_side,
        "physical_nodes_both_sides": (pairs * nodes_per_pair + directory_count_per_side) * 2,
        "expected_merged_model_tree_nodes": pairs * nodes_per_pair + directory_count_per_side - pairs,
        "expected_model_tree_file_count": pairs * nodes_per_pair,
    }
    fingerprint = hashlib.sha256(json.dumps(topology, sort_keys=True).encode("utf-8")).hexdigest()
    manifest: dict[str, object] = {
        "schema": "seedsync.performance-lab.fixture.v1",
        "fixture_fingerprint": fingerprint,
        "synthetic_only": True,
        "topology": topology,
        "path_pairs": pair_entries,
    }
    for root in (local_root, remote_root):
        marker = _fixture_marker(root)
        marker.write_text(json.dumps({
            "fixture_fingerprint": fingerprint,
            "request_fingerprint": request_fingerprint,
            "manifest": manifest,
        }, indent=2) + "\n", encoding="utf-8")
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
    args = parser.parse_args()
    manifest = generate_fixture(
        args.local_root, args.remote_root, args.manifest, args.pairs, args.nodes_per_pair
    )
    print(json.dumps({
        "fixture_fingerprint": manifest["fixture_fingerprint"],
        "total_node_count": manifest["topology"]["total_node_count"],
        "pairs": manifest["topology"]["pairs"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
