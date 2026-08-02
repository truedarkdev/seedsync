#!/bin/bash

set -euo pipefail

require_nonblank_env() {
  local var_name="$1"
  local message="$2"
  local value="${!var_name:-}"

  if [[ -z "${value//[[:space:]]/}" ]]; then
    echo "${message}" >&2
    exit 1
  fi

  printf "%s" "${value}"
}

api_token="$(require_nonblank_env SEEDSYNC_E2E_API_TOKEN "SEEDSYNC_E2E_API_TOKEN is required for e2e auth seeding")"
browser_api_token="$(require_nonblank_env SEEDSYNC_E2E_BROWSER_API_TOKEN "SEEDSYNC_E2E_BROWSER_API_TOKEN is required for e2e auth seeding")"
browser_session_secret="$(require_nonblank_env SEEDSYNC_E2E_BROWSER_SESSION_SECRET "SEEDSYNC_E2E_BROWSER_SESSION_SECRET is required for remembered-session bootstrap")"

api_key_dir="${SEEDSYNC_API_KEY_DIR:-/config}"
api_key_owner="${SEEDSYNC_API_KEY_OWNER:-}"
api_key_path="${api_key_dir%/}/api-keys.json"
api_key_log_dir="${api_key_dir%/}/log"
settings_path="${api_key_dir%/}/settings.cfg"
settings_cfg_has_required_sections() {
  [[ -f "${settings_path}" ]] || return 1

  local required_section
  for required_section in General Lftp Validate Controller Web AutoQueue Logging; do
    if ! grep -Eq "^\[${required_section}\][[:space:]]*$" "${settings_path}"; then
      return 1
    fi
  done

  return 0
}

write_default_settings_cfg() {
  cat > "${settings_path}" <<EOF
[General]
log_level = INFO
verbose = False
exclude_patterns =
api_token =
allowed_hostname =
browser_handover_recovery_version =
breadcrumb_trace_enabled = False
breadcrumb_trace_retention_depth = 128
config_api_redact_remote_details = True

[Lftp]
transfer_backend = lftp
remote_address = <replace me>
remote_username = <replace me>
remote_password = <replace me>
remote_port = 22
remote_path = <replace me>
local_path = <replace me>
remote_path_to_scan_script = /tmp
remote_python_path = python3
use_ssh_key = False
num_max_parallel_downloads = 2
num_max_parallel_files_per_download = 4
num_max_connections_per_root_file = 4
num_max_connections_per_dir_file = 4
num_max_total_connections = 16
use_temp_file = False
rate_limit = 0
net_socket_buffer = 8M
staging_path =
protocol = sftp
remote_ftp_port = 21
ftp_ssl_verify_certificate = True

[Validate]
xfer_verify = True

[Controller]
interval_ms_remote_scan = 30000
interval_ms_local_scan = 10000
interval_ms_downloading_scan = 1000
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
EOF
}

umask 077
mkdir -p "${api_key_dir}" "${api_key_log_dir}"
if ! settings_cfg_has_required_sections; then
  write_default_settings_cfg
fi
SEEDSYNC_E2E_API_TOKEN="${api_token}" \
SEEDSYNC_E2E_BROWSER_API_TOKEN="${browser_api_token}" \
SEEDSYNC_E2E_BROWSER_SESSION_SECRET="${browser_session_secret}" \
python3 - "${api_key_path}" <<'PY'
import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone

primary_secret = os.environ["SEEDSYNC_E2E_API_TOKEN"]
browser_secret = os.environ["SEEDSYNC_E2E_BROWSER_API_TOKEN"]
browser_session_secret = os.environ["SEEDSYNC_E2E_BROWSER_SESSION_SECRET"]
output_path = sys.argv[1]
now = datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_secret(secret):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 200000)
    return "pbkdf2_sha256$200000${}${}".format(
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


primary_record = {
    "id": str(uuid.uuid4()),
    "name": "e2e-configure",
    "scopes": ["read", "write"],
    "secret_hash": hash_secret(primary_secret),
    "created_at": now,
    "updated_at": now,
    "revoked_at": None,
}

browser_record = {
    "id": str(uuid.uuid4()),
    "name": "e2e-browser",
    "scopes": ["admin"],
    "secret_hash": hash_secret(browser_secret),
    "created_at": now,
    "updated_at": now,
    "revoked_at": None,
}

payload = {
    "version": 3,
    "api_keys": [primary_record, browser_record],
    "ui_sessions": [
        {
            "secret": browser_session_secret,
            "scopes": ["admin"],
            "created_at": now,
            "expires_at": "",
            "bootstrap": False,
            "remembered": True,
            "api_key_id": browser_record["id"],
            "api_key_secret_hash": browser_record["secret_hash"],
        }
    ],
    "browser_handover_claimed_version": "",
}

tmp_path = "{}.tmp".format(output_path)
with open(tmp_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
os.replace(tmp_path, output_path)
print("Seeded e2e auth fixture into {}".format(output_path))
PY

if [[ -n "${api_key_owner}" ]]; then
  chown -R "${api_key_owner}" "${api_key_dir}"
fi

chmod 700 "${api_key_dir}" "${api_key_log_dir}"
chmod 600 "${api_key_path}"
