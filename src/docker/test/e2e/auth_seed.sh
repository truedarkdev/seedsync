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

if [[ -n "${SEEDSYNC_TRUSTED_BROWSER_BOOTSTRAP_SOURCES:-}" ]]; then
  echo "SEEDSYNC_TRUSTED_BROWSER_BOOTSTRAP_SOURCES is no longer supported; trusted browser sources are derived from chrome DNS" >&2
  exit 1
fi

api_key_dir="${SEEDSYNC_API_KEY_DIR:-/config}"
api_key_owner="${SEEDSYNC_API_KEY_OWNER:-}"
api_key_path="${api_key_dir%/}/api-keys.json"
api_key_log_dir="${api_key_dir%/}/log"
settings_path="${api_key_dir%/}/settings.cfg"
trusted_browser_bootstrap_sources_path="${api_key_dir%/}/trusted_browser_bootstrap_remote_addrs"

resolve_trusted_browser_bootstrap_sources() {
  python3 - <<'PY'
import ipaddress
import socket
import time

service_name = "chrome"
deadline = time.time() + 30
trusted_sources = []
seen = set()

while time.time() < deadline and not trusted_sources:
    try:
        resolved_addresses = socket.getaddrinfo(
            service_name,
            None,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror:
        resolved_addresses = []

    for family, _socktype, _proto, _canonname, sockaddr in resolved_addresses:
        try:
            source_ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue

        if family == socket.AF_INET6 or source_ip.version == 6:
            source = f"{source_ip}/128"
        else:
            source = f"{source_ip}/32"
        if source not in seen:
            seen.add(source)
            trusted_sources.append(source)

    if not trusted_sources:
        time.sleep(0.5)

if not trusted_sources:
    raise SystemExit(
        "Unable to determine trusted browser bootstrap sources for the Docker compose network"
    )

print(",".join(trusted_sources))
PY
}

set_general_option() {
  local key="$1"
  local value="$2"

  mkdir -p "${api_key_dir}"
  if [[ ! -f "${settings_path}" ]]; then
    write_default_settings_cfg "${value}"
    return
  fi

  if grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "${settings_path}"; then
    sed -i -E "s|^[[:space:]]*${key}[[:space:]]*=.*$|${key} = ${value}|" "${settings_path}"
  else
    general_line="$(grep -n "^\[General\][[:space:]]*$" "${settings_path}" | head -n 1 | cut -d: -f1)"
    if [[ -n "${general_line}" ]]; then
      insert_line="$((general_line + 1))"
      sed -i "${insert_line}i ${key} = ${value}" "${settings_path}"
    else
      printf "\n[General]\n%s = %s\n" "${key}" "${value}" >> "${settings_path}"
    fi
  fi
}

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
  local trusted_browser_bootstrap_sources="$1"

  cat > "${settings_path}" <<EOF
[General]
log_level = INFO
verbose = False
exclude_patterns =
api_token =
allowed_hostname =
trusted_browser_bootstrap_remote_addrs = ${trusted_browser_bootstrap_sources}
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

trusted_browser_bootstrap_sources="$(resolve_trusted_browser_bootstrap_sources)"
if [[ -z "${trusted_browser_bootstrap_sources//[[:space:]]/}" ]]; then
  echo "Unable to determine trusted browser bootstrap sources for the Docker compose network" >&2
  exit 1
fi

umask 077
mkdir -p "${api_key_dir}" "${api_key_log_dir}"
if ! settings_cfg_has_required_sections; then
  write_default_settings_cfg "${trusted_browser_bootstrap_sources}"
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

set_general_option trusted_browser_bootstrap_remote_addrs "${trusted_browser_bootstrap_sources}"
printf "%s\n" "${trusted_browser_bootstrap_sources}" > "${trusted_browser_bootstrap_sources_path}"

if [[ -n "${api_key_owner}" ]]; then
  chown -R "${api_key_owner}" "${api_key_dir}"
fi

chmod 700 "${api_key_dir}" "${api_key_log_dir}"
chmod 600 "${api_key_path}"
