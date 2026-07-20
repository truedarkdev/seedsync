#!/usr/bin/env bash
set -euo pipefail

readonly LEGACY_COMMIT="ff2a1039935beccbbf7ec76134b41d2e91137742"
readonly ROOT_DIR="$(git rev-parse --show-toplevel)"
readonly LAB_DIR="${ROOT_DIR}/src/docker/test/upgrade-v086"
readonly CACHE_DIR="${ROOT_DIR}/tmp/upgrade-v086/cache"
readonly RUNS_DIR="${ROOT_DIR}/tmp/upgrade-v086/runs"
readonly IMAGE_TAG="seedsync/upgrade-v086:legacy-ff2a10399"
readonly PYTHON_BASE_DIGEST="sha256:e191a71397fd61fbddb6712cd43ef9a2c17df0b5e7ba67607128554cd6bff267"
readonly ANGULAR_BASE_DIGEST="sha256:360ac6d2ab708d2d682b70dd4f89e4340d48a5710f8e2acb86993efdbd1c1487"

redact() {
  sed -E 's/(remote_password|SEEDSYNC_LAB_REMOTE_PASSWORD|password)[[:space:]]*[:=][[:space:]]*[^[:space:],}]*/\1=<redacted>/Ig'
}

die() { echo "upgrade-v086: $*" >&2; exit 1; }

validate_run_id() {
  local id="$1"
  [[ "$id" != "." && "$id" != ".." ]] || die "RUN_ID cannot be . or .."
  [[ "$id" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$ ]] || die "RUN_ID must be 1-32 alphanumeric characters, underscores, or hyphens"
}

validate_host_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] || die "HOST_PORT must be numeric"
  (( port >= 1024 && port <= 65535 )) || die "HOST_PORT must be between 1024 and 65535"
}

ensure_runs_root() {
  umask 077
  [[ ! -L "${ROOT_DIR}/tmp" && ! -L "${ROOT_DIR}/tmp/upgrade-v086" ]] || die "upgrade-v086 artifact parent must not be a symlink"
  mkdir -p "${ROOT_DIR}/tmp/upgrade-v086"
  [[ "$(realpath -e "${ROOT_DIR}/tmp/upgrade-v086")" == "$(realpath -e "${ROOT_DIR}")/tmp/upgrade-v086" ]] || die "upgrade-v086 artifact parent escaped repository"
  mkdir -p "${RUNS_DIR}"
  [[ ! -L "${RUNS_DIR}" ]] || die "runs root must not be a symlink"
  local root_real
  root_real="$(realpath -e "${RUNS_DIR}")" || die "unable to resolve runs root"
  [[ "$root_real" == "$(realpath -e "${ROOT_DIR}")/tmp/upgrade-v086/runs" ]] || die "runs root escaped repository tmp"
}

check_run_tree() {
  local id="$1"
  validate_run_id "$id"
  local run_dir="${RUNS_DIR}/${id}"
  [[ -d "$run_dir" && ! -L "$run_dir" ]] || die "run directory is missing or a symlink"
  local run_real root_real child child_real
  run_real="$(realpath -e "$run_dir")" || die "unable to resolve run directory"
  root_real="$(realpath -e "${RUNS_DIR}")" || die "unable to resolve runs root"
  [[ "$run_real" == "$root_real"/* ]] || die "run directory escaped runs root"
  for child in config downloads mounts logs remote-files evidence; do
    [[ -d "${run_dir}/${child}" && ! -L "${run_dir}/${child}" ]] || die "run mount is missing or a symlink: ${child}"
    child_real="$(realpath -e "${run_dir}/${child}")" || die "unable to resolve run mount: ${child}"
    [[ "$child_real" == "$run_real"/* ]] || die "run mount escaped run directory: ${child}"
  done
}

run_id() {
  if [[ -n "${RUN_ID:-}" ]]; then
    validate_run_id "${RUN_ID}"
    printf '%s' "${RUN_ID}"
  else
    printf 'run-%s-%s' "$(date -u +%Y%m%dt%H%M%S)" "$$"
  fi
}

require_tools() {
  command -v git >/dev/null || die "git is required"
  command -v docker >/dev/null || die "docker is required"
  command -v python >/dev/null || die "python is required for network overlap checks"
  docker compose version >/dev/null || die "docker compose is required"
  git cat-file -e "${LEGACY_COMMIT}^{commit}" 2>/dev/null || die "historical commit ${LEGACY_COMMIT} is unavailable; fetch it without switching the worktree"
}

prepare_source() {
  local source_dir="${CACHE_DIR}/source/${LEGACY_COMMIT}"
  mkdir -p "${CACHE_DIR}/source"
  if [[ ! -d "${source_dir}/src/python" ]]; then
    mkdir -p "${source_dir}"
    git archive "${LEGACY_COMMIT}" | tar -xf - -C "${source_dir}"
  fi
  [[ ! -L "${source_dir}" && ! -L "${source_dir}/src/python" ]] || die "cached source mount must not be a symlink"
  local cache_real source_real
  cache_real="$(realpath -e "${CACHE_DIR}")" || die "unable to resolve source cache"
  source_real="$(realpath -e "${source_dir}")" || die "unable to resolve cached source"
  [[ "$source_real" == "$cache_real"/source/* ]] || die "cached source escaped artifact cache"
  [[ -f "${LAB_DIR}/angular-package-lock.json" ]] || die "lab-owned Angular package lockfile is missing"
  if [[ ! -f "${source_dir}/src/angular/package-lock.json" ]] || ! cmp -s "${LAB_DIR}/angular-package-lock.json" "${source_dir}/src/angular/package-lock.json"; then
    cp "${LAB_DIR}/angular-package-lock.json" "${source_dir}/src/angular/package-lock.json" || die "unable to install lab-owned Angular lockfile into source cache"
  fi
  mkdir -p "${source_dir}/.upgrade-v086"
  cp "${LAB_DIR}/entrypoint.sh" "${source_dir}/.upgrade-v086/entrypoint.sh"
  printf '%s' "${source_dir}"
}

create_run() {
  local id="$1"
  validate_run_id "$id"
  ensure_runs_root
  local run_dir="${RUNS_DIR}/${id}"
  [[ ! -e "$run_dir" && ! -L "$run_dir" ]] || die "run already exists or is a symlink: ${id}; choose a new RUN_ID"
  mkdir -m 700 "$run_dir" || die "unable to atomically create run directory"
  for child in config downloads mounts logs remote-files evidence; do
    mkdir -m 700 "${run_dir}/${child}"
    [[ ! -L "${run_dir}/${child}" ]] || die "run child is a symlink: ${child}"
  done
  local run_real root_real
  run_real="$(realpath -e "$run_dir")" || die "unable to resolve run directory"
  root_real="$(realpath -e "${RUNS_DIR}")"
  [[ "$run_real" == "$root_real"/* ]] || die "run directory escaped runs root"
  check_run_tree "$id"
  printf 'synthetic legacy download for %s\n' "${id}" > "${run_dir}/downloads/legacy-fixture.txt"
  printf 'synthetic remote fixture for %s\n' "${id}" > "${run_dir}/remote-files/legacy-fixture.txt"
  printf '{"run_id":"%s","source_commit":"%s","source_tree":"%s","image":"%s","credentials":"synthetic-only"}\n' \
    "$id" "$LEGACY_COMMIT" "$(git rev-parse "${LEGACY_COMMIT}^{tree}")" "$IMAGE_TAG" > "${run_dir}/evidence/manifest.json"
  [[ ! -L "${RUNS_DIR}/latest" ]] || die "latest pointer must not be a symlink"
  local latest_tmp="${RUNS_DIR}/.latest.${id}.$$"
  printf '%s\n' "$id" > "$latest_tmp"
  mv -f "$latest_tmp" "${RUNS_DIR}/latest"
  printf '%s' "${run_dir}"
}

preflight() {
  require_tools
  echo "legacy commit: ${LEGACY_COMMIT}"
  echo "legacy tree:   $(git rev-parse "${LEGACY_COMMIT}^{tree}")"
  echo "docker:        $(docker version --format '{{.Server.Version}}')"
  echo "cache:         ${CACHE_DIR}"
  echo "runs:          ${RUNS_DIR}"
}

build() {
  require_tools
  local id
  id="${RUN_ID:-$(run_id)}"
  validate_run_id "$id"
  local source_dir
  source_dir="$(prepare_source)"
  local tree
  tree="$(git rev-parse "${LEGACY_COMMIT}^{tree}")"
  local dockerfile_digest entrypoint_digest compose_digest remote_dockerfile_digest proxy_dockerfile_digest proxy_config_digest lab_script_digest angular_lock_digest helper_digest
  dockerfile_digest="$(sha256sum "${LAB_DIR}/Dockerfile" | cut -d' ' -f1)"
  entrypoint_digest="$(sha256sum "${LAB_DIR}/entrypoint.sh" | cut -d' ' -f1)"
  compose_digest="$(sha256sum "${LAB_DIR}/compose.yml" | cut -d' ' -f1)"
  remote_dockerfile_digest="$(sha256sum "${LAB_DIR}/remote.Dockerfile" | cut -d' ' -f1)"
  proxy_dockerfile_digest="$(sha256sum "${LAB_DIR}/proxy.Dockerfile" | cut -d' ' -f1)"
  proxy_config_digest="$(sha256sum "${LAB_DIR}/proxy-nginx.conf" | cut -d' ' -f1)"
  lab_script_digest="$(sha256sum "${LAB_DIR}/lab.sh" | cut -d' ' -f1)"
  angular_lock_digest="$(sha256sum "${LAB_DIR}/angular-package-lock.json" | cut -d' ' -f1)"
  helper_digest="$(cat "${LAB_DIR}/Dockerfile" "${LAB_DIR}/entrypoint.sh" "${LAB_DIR}/compose.yml" "${LAB_DIR}/remote.Dockerfile" "${LAB_DIR}/proxy.Dockerfile" "${LAB_DIR}/proxy-nginx.conf" "${LAB_DIR}/lab.sh" "${LAB_DIR}/angular-package-lock.json" | sha256sum | cut -d' ' -f1)"
  if docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1 \
    && [[ "$(docker image inspect --format '{{index .Config.Labels "org.seedsync.upgrade-v086.source-commit"}}' "${IMAGE_TAG}")" == "${LEGACY_COMMIT}" ]] \
    && [[ "$(docker image inspect --format '{{index .Config.Labels "org.seedsync.upgrade-v086.source-tree"}}' "${IMAGE_TAG}")" == "${tree}" ]] \
    && [[ "$(docker image inspect --format '{{index .Config.Labels "org.seedsync.upgrade-v086.lab-helper-digest"}}' "${IMAGE_TAG}")" == "${helper_digest}" ]]; then
    echo "cached image: ${IMAGE_TAG}"
  else
    docker build --pull=false \
      --build-arg "SOURCE_COMMIT=${LEGACY_COMMIT}" \
      --build-arg "SOURCE_TREE=${tree}" \
      --build-arg "LAB_HELPER_DIGEST=${helper_digest}" \
      --build-arg "ANGULAR_BASE_DIGEST=${ANGULAR_BASE_DIGEST}" \
      --build-arg "ANGULAR_PACKAGE_LOCK_DIGEST=${angular_lock_digest}" \
      --tag "${IMAGE_TAG}" \
      --file "${LAB_DIR}/Dockerfile" "${source_dir}"
  fi
  local run_dir
  run_dir="$(create_run "$id")"
  local image_id image_digest
  image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE_TAG}")"
  image_digest="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}unpublished:${image_id}{{end}}' "${IMAGE_TAG}")"
  local base_digest="${PYTHON_BASE_DIGEST}"
  printf '{"run_id":"%s","source_commit":"%s","source_tree":"%s","lab_helper_digest":"%s","dockerfile_digest":"%s","entrypoint_digest":"%s","compose_digest":"%s","remote_dockerfile_digest":"%s","proxy_dockerfile_digest":"%s","proxy_config_digest":"%s","lab_script_digest":"%s","angular_package_lock_digest":"%s","image":"%s","image_id":"%s","image_digest":"%s","base_image":"python:3.8-slim-bullseye","base_digest":"%s","angular_base_image":"node:12.16","angular_base_digest":"%s","credentials":"synthetic-only"}\n' \
    "$id" "$LEGACY_COMMIT" "${tree}" "${helper_digest}" "${dockerfile_digest}" "${entrypoint_digest}" "${compose_digest}" "${remote_dockerfile_digest}" "${proxy_dockerfile_digest}" "${proxy_config_digest}" "${lab_script_digest}" "${angular_lock_digest}" "$IMAGE_TAG" "$image_id" "$image_digest" "$base_digest" "$ANGULAR_BASE_DIGEST" > "${run_dir}/evidence/manifest.json"
  echo "run: ${id}"
  echo "image: ${image_id}"
}

selected_run_dir() {
  local id="${RUN_ID:-}"
  if [[ -z "$id" ]]; then
    [[ -s "${RUNS_DIR}/latest" ]] || die "RUN_ID is required (or run build first)"
    id="$(<"${RUNS_DIR}/latest")"
  fi
  validate_run_id "$id"
  check_run_tree "$id"
  printf '%s' "${id}"
}

network_names() {
  local id="$1"
  printf 'seedsync-upgrade-v086-lab-%s\nseedsync-upgrade-v086-browser-%s\n' "${id,,}" "${id,,}"
}

ensure_networks() {
  local id="$1"
  local lab_network browser_network
  local network_list=()
  mapfile -t network_list < <(network_names "$id")
  lab_network="${network_list[0]}"
  browser_network="${network_list[1]}"
  local hash seed_a seed_b seed_c attempt octet_a octet_b octet_c lab_subnet browser_subnet
  hash="$(printf '%s' "$id" | sha256sum | cut -c1-4)"
  seed_a=$((16#${hash:0:2}))
  seed_b=$((16#${hash:2:2}))
  seed_c=0
  local existing_subnets
  existing_subnets="$(docker network ls -q | xargs -r docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}}{{"\n"}}{{end}}')"
  local selected=false
  for attempt in $(seq 0 255); do
    octet_a=$(( (seed_a + attempt) % 256 ))
    octet_b=$seed_b
    octet_c=$seed_c
    lab_subnet="10.${octet_a}.${octet_b}.${octet_c}/28"
    browser_subnet="11.${octet_a}.${octet_b}.${octet_c}/28"
    if python -c 'import ipaddress, sys; candidates = [ipaddress.ip_network(value) for value in sys.argv[1:]]; existing = [ipaddress.ip_network(line.strip()) for line in sys.stdin if line.strip()]; raise SystemExit(0 if all(not candidate.overlaps(network) for candidate in candidates for network in existing) else 1)' \
      "$lab_subnet" "$browser_subnet" <<<"$existing_subnets"
    then
      selected=true
      break
    fi
  done
  [[ "$selected" == true ]] || die "unable to allocate isolated per-run network subnets"
  for spec in "${lab_network}|${lab_subnet}|true" "${browser_network}|${browser_subnet}|false"; do
    IFS='|' read -r name subnet internal <<<"$spec"
    if docker network inspect "$name" >/dev/null 2>&1; then
      [[ "$(docker network inspect --format '{{index .Labels "seedsync.upgrade-v086.run-id"}}' "$name")" == "$id" ]] || die "network exists with unexpected ownership: $name"
    else
      local args=(network create --subnet "$subnet" --label "seedsync.upgrade-v086.run-id=$id" --label "seedsync.upgrade-v086.role=$name")
      [[ "$internal" == true ]] && args+=(--internal)
      docker "${args[@]}" "$name" >/dev/null || die "unable to create isolated network $name"
    fi
  done
}

compose() {
  local id="$1"
  shift
  local run_dir="${RUNS_DIR}/${id}"
  local project="seedsync-upgrade-v086-$(printf '%s' "$id" | tr '[:upper:]' '[:lower:]')"
  local networks
  networks=($(network_names "$id"))
  SOURCE_DIR="$(prepare_source)" RUN_ID="$id" RUN_DIR="$run_dir" HOST_PORT="${HOST_PORT:-18806}" LAB_NETWORK="${networks[0]}" BROWSER_NETWORK="${networks[1]}" \
    docker compose -p "$project" -f "${LAB_DIR}/compose.yml" "$@"
}

record_runtime_digests() {
  local id="$1"
  local run_dir="${RUNS_DIR}/${id}"
  local dockerfile_digest entrypoint_digest compose_digest remote_dockerfile_digest proxy_dockerfile_digest proxy_config_digest lab_script_digest angular_lock_digest
  dockerfile_digest="$(sha256sum "${LAB_DIR}/Dockerfile" | cut -d' ' -f1)"
  entrypoint_digest="$(sha256sum "${LAB_DIR}/entrypoint.sh" | cut -d' ' -f1)"
  compose_digest="$(sha256sum "${LAB_DIR}/compose.yml" | cut -d' ' -f1)"
  remote_dockerfile_digest="$(sha256sum "${LAB_DIR}/remote.Dockerfile" | cut -d' ' -f1)"
  proxy_dockerfile_digest="$(sha256sum "${LAB_DIR}/proxy.Dockerfile" | cut -d' ' -f1)"
  proxy_config_digest="$(sha256sum "${LAB_DIR}/proxy-nginx.conf" | cut -d' ' -f1)"
  lab_script_digest="$(sha256sum "${LAB_DIR}/lab.sh" | cut -d' ' -f1)"
  angular_lock_digest="$(sha256sum "${LAB_DIR}/angular-package-lock.json" | cut -d' ' -f1)"
  local container="seedsync-upgrade-v086-${id}"
  docker cp "${container}:/usr/share/doc/seedsync-upgrade-v086/inventory/dpkg.txt" "${run_dir}/evidence/dpkg-inventory.txt"
  docker cp "${container}:/usr/share/doc/seedsync-upgrade-v086/inventory/pip.txt" "${run_dir}/evidence/pip-inventory.txt"
  docker cp "${container}:/usr/share/doc/seedsync-upgrade-v086/inventory/npm.json" "${run_dir}/evidence/npm-inventory.json"
  {
    printf 'dockerfile_digest=%s\n' "$dockerfile_digest"
    printf 'entrypoint_digest=%s\n' "$entrypoint_digest"
    printf 'compose_digest=%s\n' "$compose_digest"
    printf 'remote_dockerfile_digest=%s\n' "$remote_dockerfile_digest"
    printf 'proxy_dockerfile_digest=%s\n' "$proxy_dockerfile_digest"
    printf 'proxy_config_digest=%s\n' "$proxy_config_digest"
    printf 'lab_script_digest=%s\n' "$lab_script_digest"
    printf 'angular_package_lock_digest=%s\n' "$angular_lock_digest"
    printf 'dpkg_inventory_digest=%s\n' "$(sha256sum "${run_dir}/evidence/dpkg-inventory.txt" | cut -d' ' -f1)"
    printf 'pip_inventory_digest=%s\n' "$(sha256sum "${run_dir}/evidence/pip-inventory.txt" | cut -d' ' -f1)"
    printf 'npm_inventory_digest=%s\n' "$(sha256sum "${run_dir}/evidence/npm-inventory.json" | cut -d' ' -f1)"
    printf 'legacy_image_id=%s\n' "$(docker image inspect --format '{{.Id}}' "${IMAGE_TAG}")"
    printf 'legacy_image_digest=%s\n' "$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}unpublished:{{.Id}}{{end}}' "${IMAGE_TAG}")"
    printf 'remote_image_id=%s\n' "$(docker image inspect --format '{{.Id}}' seedsync/upgrade-v086/remote)"
    printf 'remote_image_digest=%s\n' "$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}unpublished:{{.Id}}{{end}}' seedsync/upgrade-v086/remote)"
    printf 'proxy_image_id=%s\n' "$(docker image inspect --format '{{.Id}}' seedsync/upgrade-v086/proxy)"
    printf 'proxy_image_digest=%s\n' "$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}unpublished:{{.Id}}{{end}}' seedsync/upgrade-v086/proxy)"
  } > "${run_dir}/evidence/image-digests.txt"
}

wait_for_remote_scan() {
  local id="$1"
  local run_dir="${RUNS_DIR}/${id}"
  local status_file="${run_dir}/evidence/server-status.json"
  local attempts=0
  local max_attempts=30
  while (( attempts < max_attempts )); do
    if curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${HOST_PORT:-18806}/server/status" > "${status_file}"; then
      local state
      state="$(python - "${status_file}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
server = status.get("server", {})
controller = status.get("controller", {})
if controller.get("latest_remote_scan_failed") is True:
    print("failed")
elif (server.get("up") is True
      and controller.get("latest_remote_scan_time") is not None
      and controller.get("latest_remote_scan_failed") is False):
    print("ready")
else:
    print("waiting")
PY
)"
      case "$state" in
        ready) return 0 ;;
        failed)
          compose "$id" logs --no-color | redact > "${run_dir}/evidence/compose.log"
          die "remote scan failed; see ${status_file} and compose.log"
          ;;
      esac
    fi
    attempts=$((attempts + 1))
    sleep 2
  done
  compose "$id" logs --no-color | redact > "${run_dir}/evidence/compose.log"
  die "timed out waiting for first successful remote scan; see ${status_file} and compose.log"
}

start() {
  local id
  id="$(selected_run_dir)"
  validate_host_port "${HOST_PORT:-18806}"
  ensure_networks "$id"
  compose "$id" up -d --build --force-recreate
  local run_dir="${RUNS_DIR}/${id}"
  record_runtime_digests "$id"
  compose "$id" ps > "${run_dir}/evidence/compose-ps.txt"
  compose "$id" logs --no-color | redact > "${run_dir}/evidence/compose.log"
  echo "http://127.0.0.1:${HOST_PORT:-18806}/"
}

status() {
  local id
  id="$(selected_run_dir)"
  local run_dir="${RUNS_DIR}/${id}"
  validate_host_port "${HOST_PORT:-18806}"
  ensure_networks "$id"
  local running_services
  running_services="$(compose "$id" ps --services --status running)"
  grep -qx 'legacy' <<<"${running_services}" || die "legacy service is not running"
  grep -qx 'upgrade_remote' <<<"${running_services}" || die "remote service is not running"
  grep -qx 'browser_proxy' <<<"${running_services}" || die "browser proxy service is not running"
  compose "$id" ps
  curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${HOST_PORT:-18806}/" | redact > "${run_dir}/evidence/http-root.html" || die "legacy HTTP endpoint is unhealthy"
  wait_for_remote_scan "$id"
  compose "$id" logs --no-color | redact > "${run_dir}/evidence/compose.log"
  if grep -q 'ScannerError' "${run_dir}/evidence/compose.log"; then
    die "ScannerError present in collected logs"
  fi
}

stop() {
  local id
  id="$(selected_run_dir)"
  validate_host_port "${HOST_PORT:-18806}"
  compose "$id" stop
}

usage() {
  cat <<'EOF'
Usage: lab.sh <preflight|build|start|status|stop>

RUN_ID selects a retained run; build creates a unique run when omitted.
HOST_PORT defaults to 18806 and binds only to loopback.
EOF
}

main() {
  umask 077
  case "${1:-}" in
    preflight) preflight ;;
    build) build ;;
    start) start ;;
    status) status ;;
    stop) stop ;;
    *) usage; return 2 ;;
  esac
}

main "$@"
