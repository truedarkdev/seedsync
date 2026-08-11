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


PAIR_LOCAL_DIRECTORIES = ("path-pair-01", "path-pair-02", "path-pair-03", "path-pair-04", "path-pair-05", "path-pair-06")


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


def _seed_persist(config_dir: Path, move_failure_mode: str) -> None:
    downloaded = [_synthetic_file_id("pair-{0:02d}".format((index % 6) + 1), index)
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
    (config_dir / "controller.persist").write_text(json.dumps(controller, indent=2) + "\n", encoding="utf-8")
    (config_dir / "autoqueue.persist").write_text(json.dumps({"patterns": []}, indent=2) + "\n", encoding="utf-8")


def seed_config(config_dir: Path, api_token: str, pairs: int = 6, breadcrumb_mode: str = "on",
                move_failure_mode: str = "stale") -> None:
    if not api_token.strip():
        raise ValueError("api token must be nonblank")
    if pairs < 1 or pairs > 6:
        raise ValueError("pairs must be between 1 and 6")
    if breadcrumb_mode not in {"on", "off"}:
        raise ValueError("breadcrumb_mode must be on or off")
    if move_failure_mode not in {"stale", "none"}:
        raise ValueError("move_failure_mode must be stale or none")
    breadcrumb_enabled = "True" if breadcrumb_mode == "on" else "False"
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
    pair_entries = [
        {
            "id": "pair-{0:02d}".format(number),
            "name": "Performance Pair {0:02d}".format(number),
            "remote_path": "/home/remoteuser/files/{}".format(PAIR_LOCAL_DIRECTORIES[number - 1]),
            "local_path": "/mounts/{}".format(PAIR_LOCAL_DIRECTORIES[number - 1]),
            "enabled": True,
            "auto_queue": True,
        }
        for number in range(1, pairs + 1)
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
performance_diagnostics_enabled = True
performance_diagnostics_retention_depth = 120
performance_diagnostics_sample_interval_seconds = 5
config_api_redact_remote_details = True

[Lftp]
transfer_backend = lftp
remote_address = remote
remote_username = remoteuser
remote_password = remotepass
remote_port = 1234
remote_path = /home/remoteuser/files
local_path = /mounts
remote_path_to_scan_script = /tmp
remote_python_path = python3
use_ssh_key = False
num_max_parallel_downloads = 1
num_max_parallel_files_per_download = 4
num_max_connections_per_root_file = 4
num_max_connections_per_dir_file = 4
num_max_total_connections = 16
use_temp_file = True
rate_limit = 0
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
    (config_dir / "path_pairs.json").write_text(
        json.dumps({"version": 1, "path_pairs": pair_entries}, indent=2) + "\n",
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
    _seed_persist(config_dir, move_failure_mode)
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
    parser.add_argument("--api-token", required=True)
    parser.add_argument("--pairs", type=int, default=6)
    parser.add_argument("--breadcrumb-mode", choices=("on", "off"), default="on")
    parser.add_argument("--move-failure-mode", choices=("stale", "none"), default="stale")
    args = parser.parse_args()
    seed_config(args.config_dir, args.api_token, args.pairs, args.breadcrumb_mode, args.move_failure_mode)
    print(json.dumps({"schema": "seedsync-performance-lab.config.v1", "pairs": args.pairs,
                      "breadcrumb_mode": args.breadcrumb_mode, "move_failure_mode": args.move_failure_mode}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
