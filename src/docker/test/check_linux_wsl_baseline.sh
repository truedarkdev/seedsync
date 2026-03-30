#!/bin/bash
set -euo pipefail

error() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "$*"
}

check_command() {
  local command_name="$1"
  local resolved_path

  resolved_path="$(command -v "${command_name}" || true)"
  [[ -n "${resolved_path}" ]] || error "${command_name} is required"

  info "  ${command_name}: ${resolved_path}"
}

check_python_version() {
  local python_version python_major_minor

  python_version="$(python3 - <<'PY'
import sys
print("{}.{}.{}".format(*sys.version_info[:3]))
PY
)"
  python_major_minor="${python_version%.*}"

  case "${python_major_minor}" in
    3.11|3.12)
      ;;
    *)
      error "Python ${python_version} is outside the supported repo range (>=3.11,<3.13)"
      ;;
  esac

  info "  python3: ${python_version}"
}

check_tcp_port() {
  local host="$1"
  local port="$2"

  python3 - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

try:
    with socket.create_connection((host, port), timeout=2):
        pass
except OSError as exc:
    raise SystemExit(f"{host}:{port} is not reachable: {exc}") from exc
PY

  info "  ${host}:${port}: reachable"
}

check_ssh_login() {
  local host="$1"
  local port="$2"
  local user="$3"

  ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=5 \
    -o KbdInteractiveAuthentication=no \
    -o LogLevel=error \
    -o NumberOfPasswordPrompts=0 \
    -o PasswordAuthentication=no \
    -o PreferredAuthentications=publickey \
    -o StrictHostKeyChecking=accept-new \
    -p "${port}" \
    "${user}@${host}" \
    true

  info "  ${user}@${host}:${port}: login-style SSH probe passed"
}

check_live_ssh_lftp_lane_prereqs() {
  info "Live SSH/LFTP lane"

  check_command python3
  check_python_version
  check_command ssh
  check_command lftp
  check_ssh_login 127.0.0.1 22 seedsynctest

  info "  live SSH/LFTP lane prerequisites satisfied"
}

check_archive_backed_lane_prereqs() {
  info "Archive-backed lane"

  check_command rar
  check_command unrar

  info "  archive-backed lane prerequisites satisfied"
}

check_reusable_remote_fixture_prereqs() {
  info "Reusable remote fixture lane"
  info "  remote fixture bootstrap is separate; run make run-remote-server to bring up 127.0.0.1:1234"
  check_tcp_port 127.0.0.1 1234
}

usage() {
  cat <<'EOF'
Usage: check_linux_wsl_baseline.sh [--all] [--live-ssh-lftp] [--archive-backed] [--reusable-remote-fixture]

Without lane flags, the helper runs the full baseline:
  * live SSH/LFTP lane
  * archive-backed lane
  * reusable remote fixture lane

Lane flags can be combined to run a custom subset.
EOF
}

run_live_ssh_lftp_lane=0
run_archive_backed_lane=0
run_reusable_remote_fixture_lane=0
explicit_lane_selection=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      run_live_ssh_lftp_lane=1
      run_archive_backed_lane=1
      run_reusable_remote_fixture_lane=1
      explicit_lane_selection=1
      ;;
    --live-ssh-lftp)
      run_live_ssh_lftp_lane=1
      explicit_lane_selection=1
      ;;
    --archive-backed)
      run_archive_backed_lane=1
      explicit_lane_selection=1
      ;;
    --reusable-remote-fixture)
      run_reusable_remote_fixture_lane=1
      explicit_lane_selection=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      error "Unknown argument: $1"
      ;;
  esac

  shift
done

if [[ "${explicit_lane_selection}" -eq 0 ]]; then
  run_live_ssh_lftp_lane=1
  run_archive_backed_lane=1
  run_reusable_remote_fixture_lane=1
fi

info "WSL/Linux baseline preflight"
info "Checking host-side prerequisites for the selected lane(s)"

if [[ "${run_live_ssh_lftp_lane}" -eq 1 ]]; then
  check_live_ssh_lftp_lane_prereqs
fi

if [[ "${run_archive_backed_lane}" -eq 1 ]]; then
  check_archive_backed_lane_prereqs
fi

if [[ "${run_reusable_remote_fixture_lane}" -eq 1 ]]; then
  check_reusable_remote_fixture_prereqs
fi

info "WSL/Linux baseline preflight passed"
