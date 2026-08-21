#!/usr/bin/env python3
"""Seed a production-shaped config, six path pairs, and one admin API key."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from generate_fixture import CADENCE_FIELDS, _data_spec, config_fingerprint, normalize_topology_spec, topology_fingerprint


# Active-profile transfer must remain observable for the browser timeline while
# leaving the historical uniform profile unchanged.  These are synthetic
# fixture settings, not production defaults.
MIXED_RATE_LIMIT_BYTES_PER_SECOND = 64_000
CADENCE_RATE_LIMIT_BYTES_PER_SECOND = 64_000


def rate_limit_for_profile(profile: str) -> int:
    return {
        "uniform": 0,
        "mixed": MIXED_RATE_LIMIT_BYTES_PER_SECOND,
        "cadence": CADENCE_RATE_LIMIT_BYTES_PER_SECOND,
    }[profile]


def _hash_secret(secret: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 200_000)
    return "pbkdf2_sha256$200000${}${}".format(
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _synthetic_file_id(pair_id: str, index: int) -> str:
    extension = ("bin", "dat", "json", "part", "txt", "tmp")[index % 6]
    return json.dumps([pair_id, "node-{0:08d}.{1}".format(index, extension)], separators=(",", ":"))


def _seed_persist(
    config_dir: Path,
    move_failure_mode: str,
    pair_ids: list[str],
    resume_source_identities: dict[str, tuple[int, int]] | None = None,
) -> None:
    downloaded = [_synthetic_file_id(pair_ids[index % len(pair_ids)], index)
                  for index in range(24)]
    controller = {
        "downloaded": downloaded,
        "downloaded_timestamps": {key: 1700000000.0 + index for index, key in enumerate(downloaded)},
        "extracted": [],
        "stopped": [],
        "move_failure_counts": {downloaded[23]: 1} if move_failure_mode == "stale" else {},
        "final_move_succeeded": downloaded[:2],
        "marker_identity_migration": 0,
    }
    if resume_source_identities:
        controller["resume_sources"] = {
            file_id: {"size": identity[0], "mtime": identity[1]}
            for file_id, identity in resume_source_identities.items()
        }
    (config_dir / "controller.persist").write_text(json.dumps(controller, indent=2) + "\n", encoding="utf-8")
    (config_dir / "autoqueue.persist").write_text(json.dumps({"patterns": []}, indent=2) + "\n", encoding="utf-8")


def seed_config(config_dir: Path, api_token: str, pairs: int = 6, breadcrumb_mode: str = "on",
                move_failure_mode: str = "stale", remote_address: str = "remote",
                profile: str = "uniform", high_card_enabled: bool = True,
                diagnostics_mode: str = "on") -> None:
    if not api_token.strip():
        raise ValueError("api token must be nonblank")
    if pairs < 1 or pairs > 6:
        raise ValueError("pairs must be between 1 and 6")
    if breadcrumb_mode not in {"on", "off"}:
        raise ValueError("breadcrumb_mode must be on or off")
    if diagnostics_mode not in {"on", "off"}:
        raise ValueError("diagnostics_mode must be on or off")
    if move_failure_mode not in {"stale", "none"}:
        raise ValueError("move_failure_mode must be stale or none")
    if not remote_address.strip():
        raise ValueError("remote_address must be nonblank")
    breadcrumb_enabled = "True" if breadcrumb_mode == "on" else "False"
    diagnostics_enabled = "True" if diagnostics_mode == "on" else "False"
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(config_dir, 99, 100)
    except (AttributeError, PermissionError, OSError):
        pass
    try:
        config_dir.chmod(0o770)
    except (AttributeError, PermissionError, OSError):
        pass
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    spec = normalize_topology_spec(profile, pairs, high_card_enabled=high_card_enabled)
    rate_limit = rate_limit_for_profile(profile)
    if profile == "cadence":
        parallel_files = 4
        connections_per_root_file = 1
        connections_per_dir_file = 1
        total_connections = 4
    else:
        parallel_files = 4
        connections_per_root_file = 4
        connections_per_dir_file = 4
        total_connections = 16
    pair_entries = [
        {
            "id": pair["id"],
            "name": pair.get("name", "Performance Pair {0:02d}".format(int(pair["id"][-2:]))),
            "remote_path": "/home/remoteuser/files/{}".format(pair["directory"]),
            "local_path": "/mounts/{}".format(pair["directory"]),
            "directory": pair["directory"],
            "role": pair["role"],
            "nodes_local": pair["nodes_local"],
            "nodes_remote": pair["nodes_remote"],
            "enabled": pair["enabled"],
            "auto_queue": pair["auto_queue"],
            "remote_only_targets": pair["remote_only_targets"],
            **{
                key: pair[key]
                for key in CADENCE_FIELDS
                if key in pair
            },
        }
        for pair in spec["pairs"]
    ]
    settings = f"""[General]
log_level = INFO
verbose = False
exclude_patterns =
allowed_hostname =
browser_handover_recovery_version =
disable_browser_auth = False
breadcrumb_trace_enabled = {breadcrumb_enabled}
breadcrumb_trace_retention_depth = 128
performance_diagnostics_enabled = {diagnostics_enabled}
performance_diagnostics_retention_depth = 120
performance_diagnostics_sample_interval_seconds = 5
config_api_redact_remote_details = True

[Lftp]
transfer_backend = lftp
remote_address = {remote_address}
remote_username = remoteuser
remote_password = remotepass
remote_port = 1234
remote_path = /home/remoteuser/files
local_path = /mounts
remote_path_to_scan_script = /tmp
remote_python_path = python3
use_ssh_key = False
num_max_parallel_downloads = 1
num_max_parallel_files_per_download = {parallel_files}
num_max_connections_per_root_file = {connections_per_root_file}
num_max_connections_per_dir_file = {connections_per_dir_file}
num_max_total_connections = {total_connections}
use_temp_file = True
rate_limit = {rate_limit}
net_socket_buffer = 8M
staging_path =
protocol = sftp
remote_ftp_port = 21
ftp_ssl_verify_certificate = True
use_legacy_lftp_password_argv = False

[Validate]
xfer_verify = True

[Controller]
interval_ms_remote_scan = 120000
interval_ms_local_scan = 86400000
interval_ms_downloading_scan = 60000
extract_path = /tmp
use_local_path_as_extract_path = True
managed_extract_folders_enabled = True

[Web]
port = 8800

[AutoQueue]
enabled = True
patterns_only = False
auto_extract = True
auto_delete_remote = False

[Logging]
log_format = standard

[Notifications]
enabled = False
provider = webhook
webhook_url =
hmac_secret =
apprise_url =
apprise_tag =
allow_private_networks = False
download_start = False
download_complete = True
extraction_complete = True
delete_complete = True
    """
    (config_dir / "settings.cfg").write_text(settings, encoding="utf-8")
    path_pairs_payload = {
        "version": 1, "profile": profile,
        "diagnostics_mode": diagnostics_mode,
        "rate_limit_bytes_per_second": rate_limit,
        "connection_contract": {
            "parallel_files": parallel_files,
            "connections_per_root_file": connections_per_root_file,
            "connections_per_dir_file": connections_per_dir_file,
            "total_connections": total_connections,
        },
        "topology_fingerprint": topology_fingerprint(spec),
        "config_fingerprint": config_fingerprint(spec),
        "data_topology_spec": _data_spec(spec),
        "experiment_spec": spec,
        "path_pairs": pair_entries,
    }
    staging_cases = [
        {"pair_id": pair["id"], **pair["staging_case"]}
        for pair in spec["pairs"] if "staging_case" in pair
    ]
    if staging_cases:
        path_pairs_payload["staging_cases"] = staging_cases
    (config_dir / "path_pairs.json").write_text(
        json.dumps(path_pairs_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    api_key = {
        "id": str(uuid.uuid4()),
        "name": "performance-lab-admin",
        "scopes": ["admin", "read", "write"],
        "secret_hash": _hash_secret(api_token),
        "created_at": now,
        "updated_at": now,
        "revoked_at": None,
    }
    (config_dir / "api-keys.json").write_text(
        json.dumps({"version": 3, "api_keys": [api_key], "ui_sessions": [],
                    "browser_handover_claimed_version": ""}, indent=2) + "\n",
        encoding="utf-8",
    )
    resume_source_identities = {}
    if profile == "cadence":
        active_pair = spec["pairs"][0]
        target = active_pair["remote_only_targets"][0]
        if target.get("kind") != "directory":
            raise ValueError("cadence browser target must be a directory")
        # A directory MIRROR has aggregate progress but no file resume
        # identity. Leave resume_sources empty so Queue creates the active
        # state on a fresh remote-only target.
    _seed_persist(
        config_dir,
        move_failure_mode,
        [pair["id"] for pair in spec["pairs"] if pair["enabled"]],
        resume_source_identities,
    )
    for path in (config_dir / "settings.cfg", config_dir / "path_pairs.json", config_dir / "api-keys.json",
                 config_dir / "controller.persist", config_dir / "autoqueue.persist"):
        path.chmod(0o660)
        try:
            os.chown(path, 99, 100)
        except (AttributeError, PermissionError, OSError):
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=6)
    parser.add_argument("--breadcrumb-mode", choices=("on", "off"), default="on")
    parser.add_argument("--move-failure-mode", choices=("stale", "none"), default="stale")
    parser.add_argument("--remote-address", default="remote")
    parser.add_argument("--profile", choices=("uniform", "mixed", "cadence"), default="uniform")
    parser.add_argument("--high-card-enabled", choices=("on", "off"), default="on")
    parser.add_argument("--diagnostics-mode", choices=("on", "off"), default="on")
    args = parser.parse_args()
    api_token = os.environ.get("PERF_API_TOKEN", "")
    if not api_token.strip():
        parser.error("PERF_API_TOKEN must be set to a nonblank value in the environment")
    spec = normalize_topology_spec(
        args.profile, args.pairs, high_card_enabled=args.high_card_enabled == "on"
    )
    seed_config(
        args.config_dir,
        api_token,
        args.pairs,
        args.breadcrumb_mode,
        args.move_failure_mode,
        args.remote_address,
        args.profile,
        args.high_card_enabled == "on",
        args.diagnostics_mode,
    )
    rate_limit = rate_limit_for_profile(args.profile)
    effective_high_card_enabled = next(
        (pair.get("enabled") for pair in spec["pairs"]
         if pair.get("role") == "high-cardinality-idle"), True
    )
    print(json.dumps({"schema": "seedsync-performance-lab.config.v1", "pairs": len(spec["pairs"]),
                      "requested_pairs": args.pairs,
                      "profile": args.profile, "high_card_enabled": effective_high_card_enabled,
                      "diagnostics_mode": args.diagnostics_mode,
                      "pair_node_counts": {
                          pair["id"]: {
                              "local": pair["nodes_local"], "remote": pair["nodes_remote"],
                              "enabled": pair["enabled"], "role": pair["role"],
                          }
                          for pair in spec["pairs"]
                      },
                      "config_fingerprint": config_fingerprint(spec),
                      "rate_limit_bytes_per_second": rate_limit,
                      "breadcrumb_mode": args.breadcrumb_mode, "move_failure_mode": args.move_failure_mode,
                      "remote_address": args.remote_address}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
