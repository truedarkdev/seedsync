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

info "WSL/Linux baseline preflight"
info "Checking host-side prerequisites used by SSH-backed and archive-backed lanes"

check_command python3
check_python_version
check_command ssh
check_command lftp
check_command rar
check_command unrar
check_ssh_login 127.0.0.1 22 seedsynctest

info "  remote fixture bootstrap is separate; run make run-remote-server to bring up 127.0.0.1:1234"
check_tcp_port 127.0.0.1 1234

info "WSL/Linux baseline preflight passed"
