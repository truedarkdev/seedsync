#!/usr/bin/env bash
set -euo pipefail

readonly LEGACY_COMMIT="ff2a1039935beccbbf7ec76134b41d2e91137742"
# This lab can be invoked after a previous operation removed its caller's
# directory.  Derive the repository from this script's stable location, not
# from the inherited cwd, then repair that cwd before any helper starts.
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly ROOT_DIR="$(git -C "${SCRIPT_DIR}/../../../.." rev-parse --show-toplevel)"
cd -- "$ROOT_DIR" || { echo "upgrade-v086: unable to enter repository root" >&2; exit 1; }
readonly LAB_DIR="${ROOT_DIR}/src/docker/test/upgrade-v086"
readonly CACHE_DIR="${ROOT_DIR}/tmp/upgrade-v086/cache"
readonly RUNS_DIR="${ROOT_DIR}/tmp/upgrade-v086/runs"
readonly IMAGE_TAG="seedsync/upgrade-v086:legacy-ff2a10399"
readonly FIXTURE_MANIFEST="${LAB_DIR}/fixture-manifest.json"
readonly FIXTURE_GENERATOR="${LAB_DIR}/fixture.py"
readonly EVIDENCE_HELPER="${LAB_DIR}/ship_readiness.py"
readonly PYTHON_BASE_DIGEST="sha256:e191a71397fd61fbddb6712cd43ef9a2c17df0b5e7ba67607128554cd6bff267"
readonly ANGULAR_BASE_DIGEST="sha256:360ac6d2ab708d2d682b70dd4f89e4340d48a5710f8e2acb86993efdbd1c1487"

redact() { python "$EVIDENCE_HELPER" redact-stdin; }

die() { echo "upgrade-v086: $*" >&2; exit 1; }

validate_run_id() {
  local id="$1"
  [[ "$id" != "." && "$id" != ".." ]] || die "RUN_ID cannot be . or .."
  [[ "$id" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$ ]] || die "RUN_ID must be 1-32 alphanumeric characters, underscores, or hyphens"
  [[ "$id" == "${id,,}" ]] || die "RUN_ID must be lowercase so container, network, and volume identities cannot diverge"
}

config_volume_name() { printf 'seedsync-upgrade-v086-config-%s' "$1"; }
config_initializer_name() { printf 'seedsync-upgrade-v086-config-init-%s' "$1"; }
validator_container_name() { printf 'seedsync-upgrade-v086-validator-%s' "$1"; }
protected_volume_name() { printf 'seedsync-upgrade-v086-protected-%s' "$1"; }
protected_initializer_name() { printf 'seedsync-upgrade-v086-protected-init-%s' "$1"; }
snapshotter_container_name() { printf 'seedsync-upgrade-v086-snapshotter-%s' "$1"; }
downloads_snapshotter_container_name() { printf 'seedsync-upgrade-v086-downloads-snapshotter-%s' "$1"; }
downloads_restorer_container_name() { printf 'seedsync-upgrade-v086-downloads-restorer-%s' "$1"; }

verify_config_volume() {
  local id="$1" volume
  validate_run_id "$id"
  volume="$(config_volume_name "$id")"
  python - "$volume" "$id" <<'PY'
import json, subprocess, sys
item = json.loads(subprocess.check_output(["docker", "volume", "inspect", sys.argv[1]], text=True))[0]
expected_name, expected_id = sys.argv[1:]
if item.get("Name") != expected_name:
    raise SystemExit("config volume name mismatch")
if item.get("Driver") != "local":
    raise SystemExit("config volume driver is not local")
labels = item.get("Labels") or {}
if labels.get("seedsync.upgrade-v086.run-id") != expected_id:
    raise SystemExit("config volume run-id label mismatch")
if labels.get("seedsync.upgrade-v086.role") != "config":
    raise SystemExit("config volume role label mismatch")
PY
}

verify_protected_volume() {
  local id="$1" volume
  validate_run_id "$id"
  volume="$(protected_volume_name "$id")"
  python - "$volume" "$id" <<'PY'
import json, subprocess, sys
item = json.loads(subprocess.check_output(["docker", "volume", "inspect", sys.argv[1]], text=True))[0]
expected_name, expected_id = sys.argv[1:]
labels = item.get("Labels") or {}
checks = {
    "name": item.get("Name") == expected_name,
    "driver": item.get("Driver") == "local",
    "run-id label": labels.get("seedsync.upgrade-v086.run-id") == expected_id,
    "role label": labels.get("seedsync.upgrade-v086.role") == "protected-artifacts",
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("protected volume contract failed: " + ", ".join(failed))
PY
}

verify_validator_container() {
  local id="$1" name volume protected_volume
  validate_run_id "$id"
  name="$(validator_container_name "$id")"
  volume="$(config_volume_name "$id")"
  protected_volume="$(protected_volume_name "$id")"
  python - "$name" "$volume" "$protected_volume" "$id" <<'PY'
import json, subprocess, sys
name, volume, protected_volume, run_id = sys.argv[1:]
item = json.loads(subprocess.check_output(["docker", "container", "inspect", name], text=True))[0]
host = item.get("HostConfig") or {}
mounts = item.get("Mounts") or []
config_mounts = [entry for entry in mounts if entry.get("Destination") == "/config"]
protected_mounts = [entry for entry in mounts if entry.get("Destination") == "/protected"]
evidence_mounts = [entry for entry in mounts if entry.get("Destination") == "/evidence"]
checks = {
    "container name": item.get("Name", "").lstrip("/") == name,
    "running": (item.get("State") or {}).get("Running") is True,
    "non-root user": (item.get("Config") or {}).get("User") == "1000:1000",
    "read-only rootfs": host.get("ReadonlyRootfs") is True,
    "network none": host.get("NetworkMode") == "none",
    "all capabilities dropped": sorted(host.get("CapDrop") or []) == ["ALL"],
    "no new privileges": "no-new-privileges:true" in (host.get("SecurityOpt") or []),
    "one config mount": len(config_mounts) == 1,
    "expected config volume": bool(config_mounts) and config_mounts[0].get("Name") == volume,
    "read-only config mount": bool(config_mounts) and config_mounts[0].get("RW") is False,
    "one protected mount": len(protected_mounts) == 1,
    "expected protected volume": bool(protected_mounts) and protected_mounts[0].get("Name") == protected_volume,
    "read-only protected mount": bool(protected_mounts) and protected_mounts[0].get("RW") is False,
    "one evidence mount": len(evidence_mounts) == 1,
    "read-only evidence mount": bool(evidence_mounts) and evidence_mounts[0].get("Type") == "bind" and bool(evidence_mounts[0].get("Source")) and evidence_mounts[0].get("RW") is False,
    "run label": ((item.get("Config") or {}).get("Labels") or {}).get("seedsync.upgrade-v086.run-id") == run_id,
    "role label": ((item.get("Config") or {}).get("Labels") or {}).get("seedsync.upgrade-v086.role") == "validator",
}
failed = [label for label, passed in checks.items() if not passed]
if failed:
    raise SystemExit("validator container contract failed: " + ", ".join(failed))
PY
  docker exec "$name" sh -c 'test "$(stat -c "%u:%g:%a" /config)" = "1000:1000:700"' \
    || die "retained config volume ownership or mode changed"
  docker exec "$name" sh -c 'test "$(stat -c "%u:%g:%a" /protected)" = "1000:1000:700"' \
    || die "retained protected volume ownership or mode changed"
  docker exec "$name" sh -c 'test -d /evidence' \
    || die "validator evidence mount is unavailable"
}

verify_snapshotter_container() {
  local id="$1" name config_volume protected_volume
  validate_run_id "$id"
  name="$(snapshotter_container_name "$id")"
  config_volume="$(config_volume_name "$id")"
  protected_volume="$(protected_volume_name "$id")"
  python - "$name" "$config_volume" "$protected_volume" "$id" <<'PY'
import json, subprocess, sys
name, config_volume, protected_volume, run_id = sys.argv[1:]
item = json.loads(subprocess.check_output(["docker", "container", "inspect", name], text=True))[0]
host = item.get("HostConfig") or {}
mounts = {entry.get("Destination"): entry for entry in item.get("Mounts") or []}
checks = {
    "running": (item.get("State") or {}).get("Running") is True,
    "non-root user": (item.get("Config") or {}).get("User") == "1000:1000",
    "read-only rootfs": host.get("ReadonlyRootfs") is True,
    "network none": host.get("NetworkMode") == "none",
    "all capabilities dropped": sorted(host.get("CapDrop") or []) == ["ALL"],
    "no new privileges": "no-new-privileges:true" in (host.get("SecurityOpt") or []),
    "read-only config": mounts.get("/config", {}).get("Name") == config_volume and mounts.get("/config", {}).get("RW") is False,
    "writable protected storage": mounts.get("/protected", {}).get("Name") == protected_volume and mounts.get("/protected", {}).get("RW") is True,
    "run label": ((item.get("Config") or {}).get("Labels") or {}).get("seedsync.upgrade-v086.run-id") == run_id,
    "role label": ((item.get("Config") or {}).get("Labels") or {}).get("seedsync.upgrade-v086.role") == "snapshotter",
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("snapshotter container contract failed: " + ", ".join(failed))
PY
  docker exec "$name" sh -c 'test "$(stat -c "%u:%g:%a" /config)" = "1000:1000:700" && test "$(stat -c "%u:%g:%a" /protected)" = "1000:1000:700"' \
    || die "snapshotter storage ownership or mode changed"
}

verify_downloads_helper_container() {
  local id="$1" role="$2" name="$3" protected_rw="$4" downloads_rw="$5" allow_stopped="${6:-false}" run_dir protected_volume
  validate_run_id "$id"
  check_run_tree "$id"
  run_dir="${RUNS_DIR}/${id}"
  protected_volume="$(protected_volume_name "$id")"
  python - "$name" "$protected_volume" "$id" "$role" "$(realpath -e "${run_dir}/downloads")" "$protected_rw" "$downloads_rw" "$allow_stopped" <<'PY'
import json, os, subprocess, sys
name, protected_volume, run_id, role, downloads_source, protected_rw, downloads_rw, allow_stopped = sys.argv[1:]
item = json.loads(subprocess.check_output(["docker", "container", "inspect", name], text=True))[0]
host = item.get("HostConfig") or {}
mounts = item.get("Mounts") or []
protected = [entry for entry in mounts if entry.get("Destination") == "/protected"]
downloads = [entry for entry in mounts if entry.get("Destination") == "/downloads"]
labels = (item.get("Config") or {}).get("Labels") or {}
downloads_mount_source = downloads[0].get("Source", "") if downloads else ""
downloads_source_exact = bool(downloads) and os.path.realpath(downloads_mount_source) == downloads_source
# Docker Desktop can rewrite a later writable WSL bind to its private proxy
# path.  A running restorer proves that proxy refers to the exact source by
# matching /downloads device+inode with the already exact-verified snapshotter.
docker_desktop_wsl_proxy = (
    role == "downloads-restorer"
    and downloads_mount_source.startswith("/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/")
)
checks = {
    "container name": item.get("Name", "").lstrip("/") == name,
    "running": (item.get("State") or {}).get("Running") is True or allow_stopped == "true",
    "non-root user": (item.get("Config") or {}).get("User") == "1000:1000",
    "read-only rootfs": host.get("ReadonlyRootfs") is True,
    "network none": host.get("NetworkMode") == "none",
    "all capabilities dropped": sorted(host.get("CapDrop") or []) == ["ALL"],
    "no new privileges": "no-new-privileges:true" in (host.get("SecurityOpt") or []),
    "exactly two mounts": len(mounts) == 2,
    "one protected mount": len(protected) == 1,
    "expected protected volume": bool(protected) and protected[0].get("Name") == protected_volume,
    "protected mount access": bool(protected) and protected[0].get("RW") is (protected_rw == "true"),
    "one downloads bind": len(downloads) == 1,
    "downloads is a bind": bool(downloads) and downloads[0].get("Type") == "bind",
    "exact downloads source": downloads_source_exact or docker_desktop_wsl_proxy,
    "downloads mount access": bool(downloads) and downloads[0].get("RW") is (downloads_rw == "true"),
    "run label": labels.get("seedsync.upgrade-v086.run-id") == run_id,
    "role label": labels.get("seedsync.upgrade-v086.role") == role,
}
failed = [label for label, passed in checks.items() if not passed]
if failed:
    raise SystemExit("downloads helper contract failed: " + ", ".join(failed))
PY
  if [[ "$allow_stopped" == true && "$(docker inspect --format '{{.State.Running}}' "$name")" != true ]]; then return 0; fi
  docker exec "$name" sh -c 'test "$(stat -c "%u:%g:%a" /protected)" = "1000:1000:700" && test -d /downloads && test ! -L /downloads' \
    || die "downloads helper storage contract changed"
  if [[ "$role" == downloads-restorer ]]; then
    local snapshotter snapshot_identity restorer_identity
    snapshotter="$(downloads_snapshotter_container_name "$id")"
    verify_downloads_snapshotter_container "$id" || die "downloads restorer source identity requires the exact verified snapshotter"
    snapshot_identity="$(docker exec "$snapshotter" stat -c '%d:%i' /downloads)" \
      || die "unable to read downloads snapshotter source identity"
    restorer_identity="$(docker exec "$name" stat -c '%d:%i' /downloads)" \
      || die "unable to read downloads restorer source identity"
    [[ "$restorer_identity" == "$snapshot_identity" ]] \
      || die "downloads restorer bind does not reference the exact snapshotter source"
  fi
}

verify_downloads_snapshotter_container() {
  local id="$1"
  verify_downloads_helper_container "$id" "downloads-snapshotter" "$(downloads_snapshotter_container_name "$id")" true false
}

verify_downloads_restorer_container() {
  local id="$1" allow_stopped="${2:-false}"
  verify_downloads_helper_container "$id" "downloads-restorer" "$(downloads_restorer_container_name "$id")" false true "$allow_stopped"
}

create_downloads_restorer_container() {
  local id="$1" name run_dir protected_volume
  validate_run_id "$id"
  check_run_tree "$id"
  run_dir="${RUNS_DIR}/${id}"
  protected_volume="$(protected_volume_name "$id")"
  verify_protected_volume "$id"
  name="$(downloads_restorer_container_name "$id")"
  if docker container inspect "$name" >/dev/null 2>&1; then
    # An interrupted restore intentionally retains this fixed helper. Reuse it
    # only after the full name/label/mount/isolation contract is rechecked.
    verify_downloads_restorer_container "$id" true || die "existing downloads restorer failed its exact isolation contract"
    if [[ "$(docker inspect --format '{{.State.Running}}' "$name")" != true ]]; then
      docker start "$name" >/dev/null || die "unable to restart verified downloads restorer"
    fi
    verify_downloads_restorer_container "$id" || die "restarted downloads restorer failed its exact isolation contract"
    return 0
  fi
  docker create --name "$name" --network none --read-only --user 1000:1000 \
    --security-opt no-new-privileges:true --cap-drop ALL \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=downloads-restorer" \
    --mount "type=volume,src=${protected_volume},dst=/protected,readonly" \
    --mount "type=bind,src=${run_dir}/downloads,dst=/downloads" \
    --entrypoint /bin/sh "$IMAGE_TAG" -c 'while :; do sleep 3600; done' >/dev/null \
    || die "unable to create protected downloads restorer"
  docker start "$name" >/dev/null || die "unable to start protected downloads restorer"
  verify_downloads_restorer_container "$id" || die "downloads restorer failed its isolation contract"
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
  python "${FIXTURE_GENERATOR}" validate --manifest "${FIXTURE_MANIFEST}" >/dev/null || die "fixture manifest validation failed"
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
  local mode="${2:-stable}"
  [[ "$mode" == stable || "$mode" == transient ]] || die "invalid run mode"
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
  python "${FIXTURE_GENERATOR}" materialize --manifest "${FIXTURE_MANIFEST}" --run-dir "${run_dir}" || die "unable to materialize fixture manifest"
  local config_volume protected_volume
  config_volume="$(config_volume_name "$id")"
  protected_volume="$(protected_volume_name "$id")"
  ! docker volume inspect "$config_volume" >/dev/null 2>&1 || die "retained config volume already exists; choose a fresh RUN_ID"
  ! docker volume inspect "$protected_volume" >/dev/null 2>&1 || die "retained protected volume already exists; choose a fresh RUN_ID"
  docker volume create --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=config" "$config_volume" >/dev/null || die "unable to create retained config volume"
  docker volume create --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=protected-artifacts" "$protected_volume" >/dev/null || die "unable to create retained protected volume"
  verify_config_volume "$id" || die "created config volume failed identity verification"
  verify_protected_volume "$id" || die "created protected volume failed identity verification"
  local initializer protected_initializer validator snapshotter downloads_snapshotter
  initializer="$(config_initializer_name "$id")"
  protected_initializer="$(protected_initializer_name "$id")"
  validator="$(validator_container_name "$id")"
  snapshotter="$(snapshotter_container_name "$id")"
  downloads_snapshotter="$(downloads_snapshotter_container_name "$id")"
  ! docker container inspect "$initializer" >/dev/null 2>&1 || die "config initializer container already exists; choose a fresh RUN_ID"
  ! docker container inspect "$protected_initializer" >/dev/null 2>&1 || die "protected initializer container already exists; choose a fresh RUN_ID"
  ! docker container inspect "$validator" >/dev/null 2>&1 || die "validator container already exists; choose a fresh RUN_ID"
  ! docker container inspect "$snapshotter" >/dev/null 2>&1 || die "snapshotter container already exists; choose a fresh RUN_ID"
  ! docker container inspect "$downloads_snapshotter" >/dev/null 2>&1 || die "downloads snapshotter container already exists; choose a fresh RUN_ID"
  docker create --name "$initializer" --network none --read-only --user 0:0 \
    --security-opt no-new-privileges:true --cap-drop ALL --cap-add CHOWN --cap-add FOWNER \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=config-initializer" \
    --mount "type=bind,src=${run_dir}/config,dst=/fixture-config,readonly" \
    --mount "type=volume,src=${config_volume},dst=/config" --entrypoint /bin/sh "$IMAGE_TAG" \
    -c 'chown 0:0 /config && chmod 0700 /config && cp /fixture-config/controller.persist /config/controller.persist && cp /fixture-config/autoqueue.persist /config/autoqueue.persist && touch /config/.ship-readiness-volume-initialized && chmod 0600 /config/.ship-readiness-volume-initialized /config/controller.persist /config/autoqueue.persist && chown 1000:1000 /config/.ship-readiness-volume-initialized /config/controller.persist /config/autoqueue.persist /config && test "$(stat -c "%u:%g:%a" /config)" = "1000:1000:700" && stat -c "owner=%u:%g mode=%a path=%n" /config' >/dev/null \
    || die "unable to create retained config volume initializer"
  docker start --attach "$initializer" > "${run_dir}/evidence/config-volume-initialization.txt" \
    || die "unable to initialize retained config volume ownership"
  docker create --name "$protected_initializer" --network none --read-only --user 0:0 \
    --security-opt no-new-privileges:true --cap-drop ALL --cap-add CHOWN --cap-add FOWNER \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=protected-initializer" \
    --mount "type=volume,src=${protected_volume},dst=/protected" --entrypoint /bin/sh "$IMAGE_TAG" \
    -c 'chown 1000:1000 /protected && chmod 0700 /protected && test "$(stat -c "%u:%g:%a" /protected)" = "1000:1000:700"' >/dev/null \
    || die "unable to create retained protected volume initializer"
  docker start --attach "$protected_initializer" > "${run_dir}/evidence/protected-volume-initialization.txt" \
    || die "unable to initialize retained protected volume ownership"
  docker create --name "$validator" --network none --read-only --user 1000:1000 \
    --security-opt no-new-privileges:true --cap-drop ALL \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=validator" \
    --mount "type=volume,src=${config_volume},dst=/config,readonly" \
    --mount "type=volume,src=${protected_volume},dst=/protected,readonly" \
    --mount "type=bind,src=${run_dir}/evidence,dst=/evidence,readonly" \
    --entrypoint /bin/sh "$IMAGE_TAG" -c 'while :; do sleep 3600; done' >/dev/null \
    || die "unable to create read-only config validator"
  docker start "$validator" >/dev/null || die "unable to start read-only config validator"
  docker create --name "$snapshotter" --network none --read-only --user 1000:1000 \
    --security-opt no-new-privileges:true --cap-drop ALL \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=snapshotter" \
    --mount "type=volume,src=${config_volume},dst=/config,readonly" \
    --mount "type=volume,src=${protected_volume},dst=/protected" \
    --entrypoint /bin/sh "$IMAGE_TAG" -c 'while :; do sleep 3600; done' >/dev/null \
    || die "unable to create protected snapshotter"
  docker start "$snapshotter" >/dev/null || die "unable to start protected snapshotter"
  docker create --name "$downloads_snapshotter" --network none --read-only --user 1000:1000 \
    --security-opt no-new-privileges:true --cap-drop ALL \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=downloads-snapshotter" \
    --mount "type=bind,src=${run_dir}/downloads,dst=/downloads,readonly" \
    --mount "type=volume,src=${protected_volume},dst=/protected" \
    --entrypoint /bin/sh "$IMAGE_TAG" -c 'while :; do sleep 3600; done' >/dev/null \
    || die "unable to create protected downloads snapshotter"
  docker start "$downloads_snapshotter" >/dev/null || die "unable to start protected downloads snapshotter"
  verify_validator_container "$id" || die "read-only config validator failed its isolation contract"
  verify_snapshotter_container "$id" || die "protected snapshotter failed its isolation contract"
  verify_downloads_snapshotter_container "$id" || die "downloads snapshotter failed its isolation contract"
  printf '%s\n' "$mode" > "${run_dir}/lab-mode"
  python - "$config_volume" "${run_dir}/evidence/config-volume.json" "$protected_volume" <<'PY'
import json, sys
json.dump({"schema": 1, "volume": sys.argv[1], "protected_volume": sys.argv[3], "retained": True, "mount_target": "/config", "protected_mount_target": "/protected", "storage": "docker-named-volume"}, open(sys.argv[2], "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
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

cwd_probe() {
  local expected_root="${1:?expected repository root required}"
  [[ "$ROOT_DIR" == "$expected_root" ]] || die "repository root did not resolve from the script location"
  [[ "$PWD" == "$ROOT_DIR" ]] || die "lab did not repair its inherited working directory"
  [[ "$(git rev-parse --show-toplevel)" == "$ROOT_DIR" ]] || die "git did not run from the repaired repository directory"
}

protected_storage_self_check() {
  local id="${RUN_ID:-probe-$(date -u +%Y%m%dt%H%M%S)-$$}"
  validate_run_id "$id"
  local volume="seedsync-upgrade-v086-protected-probe-${id}"
  local initializer="seedsync-upgrade-v086-protected-probe-init-${id}"
  local writer="seedsync-upgrade-v086-protected-probe-writer-${id}"
  local reader="seedsync-upgrade-v086-protected-probe-reader-${id}"
  ! docker volume inspect "$volume" >/dev/null 2>&1 || die "protected storage self-check volume already exists; choose a fresh RUN_ID"
  for name in "$initializer" "$writer" "$reader"; do
    ! docker container inspect "$name" >/dev/null 2>&1 || die "protected storage self-check container already exists; choose a fresh RUN_ID"
  done
  docker volume create --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=protected-storage-self-check" "$volume" >/dev/null
  docker create --name "$initializer" --network none --read-only --user 0:0 --security-opt no-new-privileges:true --cap-drop ALL --cap-add CHOWN --cap-add FOWNER \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=protected-storage-self-check-initializer" \
    --mount "type=volume,src=${volume},dst=/protected" --entrypoint /bin/sh "$IMAGE_TAG" \
    -c 'chown 1000:1000 /protected && chmod 0700 /protected && test "$(stat -c "%u:%g:%a" /protected)" = "1000:1000:700"' >/dev/null
  docker start --attach "$initializer" >/dev/null || die "protected storage self-check initializer failed"
  docker create --name "$writer" --network none --read-only --user 1000:1000 --security-opt no-new-privileges:true --cap-drop ALL \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=protected-storage-self-check-writer" \
    --mount "type=volume,src=${volume},dst=/protected" --entrypoint /bin/sh "$IMAGE_TAG" \
    -c 'umask 077 && printf probe > /protected/source && tar -C /protected -cpf /protected/probe.tar source && test "$(stat -c "%u:%g:%a" /protected/source)" = "1000:1000:600" && test "$(stat -c "%u:%g:%a" /protected/probe.tar)" = "1000:1000:600"' >/dev/null
  docker start --attach "$writer" >/dev/null || die "protected storage self-check writer failed"
  docker create --name "$reader" --network none --read-only --user 1000:1000 --security-opt no-new-privileges:true --cap-drop ALL \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=protected-storage-self-check-reader" \
    --mount "type=volume,src=${volume},dst=/protected,readonly" --entrypoint /bin/sh "$IMAGE_TAG" \
    -c 'test "$(stat -c "%u:%g:%a" /protected)" = "1000:1000:700" && test "$(stat -c "%u:%g:%a" /protected/probe.tar)" = "1000:1000:600" && tar -tf /protected/probe.tar | grep -qx source && test "$(tar -xOf /protected/probe.tar source)" = probe' >/dev/null
  docker start --attach "$reader" >/dev/null || die "protected storage self-check read-only archive access failed"
  printf '{"schema":1,"storage":"docker-named-volume","volume":"%s","archive_mode":"0600","parent_mode":"0700","writer":"non-root-networkless-read-only-rootfs","reader":"non-root-networkless-read-only-mount"}\n' "$volume"
}

validator_evidence_path_self_check() {
  local id="${RUN_ID:-probe-$(date -u +%Y%m%dt%H%M%S)-$$}"
  validate_run_id "$id"
  local evidence_dir="${ROOT_DIR}/tmp/upgrade-v086/validator-evidence-path-self-check-${id}"
  local container="seedsync-upgrade-v086-validator-evidence-path-${id}"
  [[ ! -e "$evidence_dir" && ! -L "$evidence_dir" ]] || die "validator evidence self-check directory already exists; choose a fresh RUN_ID"
  ! docker container inspect "$container" >/dev/null 2>&1 || die "validator evidence self-check container already exists; choose a fresh RUN_ID"
  mkdir -p "${evidence_dir}/ship-readiness"
  printf '{"schema":1,"entries":[]}\n' > "${evidence_dir}/ship-readiness/before-config.json"
  docker create --name "$container" --network none --read-only --user 1000:1000 --security-opt no-new-privileges:true --cap-drop ALL \
    --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=validator-evidence-path-self-check" \
    --mount "type=bind,src=${evidence_dir},dst=/evidence,readonly" --entrypoint /bin/sh "$IMAGE_TAG" \
    -c 'test -s /evidence/ship-readiness/before-config.json && test ! -e /evidence/before-config.json && python -c "import json; json.load(open(\"/evidence/ship-readiness/before-config.json\"))"' >/dev/null
  docker start --attach "$container" >/dev/null || die "validator evidence path self-check failed"
  printf '{"schema":1,"host_evidence_dir":"%s","container_evidence_dir":"/evidence","inventory":"/evidence/ship-readiness/before-config.json","container":"%s"}\n' "$evidence_dir" "$container"
}

build() {
  require_tools
  local mode="${1:-stable}"
  [[ "$mode" == stable || "$mode" == transient ]] || die "invalid build mode"
  local id
  id="${RUN_ID:-$(run_id)}"
  validate_run_id "$id"
  local source_dir
  source_dir="$(prepare_source)"
  local tree
  tree="$(git rev-parse "${LEGACY_COMMIT}^{tree}")"
  local dockerfile_digest entrypoint_digest compose_digest remote_dockerfile_digest proxy_dockerfile_digest proxy_config_digest lab_script_digest angular_lock_digest fixture_manifest_digest fixture_generator_digest transient_probe_digest helper_digest
  dockerfile_digest="$(sha256sum "${LAB_DIR}/Dockerfile" | cut -d' ' -f1)"
  entrypoint_digest="$(sha256sum "${LAB_DIR}/entrypoint.sh" | cut -d' ' -f1)"
  compose_digest="$(sha256sum "${LAB_DIR}/compose.yml" | cut -d' ' -f1)"
  remote_dockerfile_digest="$(sha256sum "${LAB_DIR}/remote.Dockerfile" | cut -d' ' -f1)"
  proxy_dockerfile_digest="$(sha256sum "${LAB_DIR}/proxy.Dockerfile" | cut -d' ' -f1)"
  proxy_config_digest="$(sha256sum "${LAB_DIR}/proxy-nginx.conf" | cut -d' ' -f1)"
  lab_script_digest="$(sha256sum "${LAB_DIR}/lab.sh" | cut -d' ' -f1)"
  angular_lock_digest="$(sha256sum "${LAB_DIR}/angular-package-lock.json" | cut -d' ' -f1)"
  fixture_manifest_digest="$(sha256sum "${FIXTURE_MANIFEST}" | cut -d' ' -f1)"
  fixture_generator_digest="$(sha256sum "${FIXTURE_GENERATOR}" | cut -d' ' -f1)"
  transient_probe_digest="$(sha256sum "${LAB_DIR}/transient.py" | cut -d' ' -f1)"
  helper_digest="$(cat "${LAB_DIR}/Dockerfile" "${LAB_DIR}/entrypoint.sh" "${LAB_DIR}/compose.yml" "${LAB_DIR}/remote.Dockerfile" "${LAB_DIR}/proxy.Dockerfile" "${LAB_DIR}/proxy-nginx.conf" "${LAB_DIR}/lab.sh" "${LAB_DIR}/angular-package-lock.json" "${FIXTURE_MANIFEST}" "${FIXTURE_GENERATOR}" "${LAB_DIR}/transient.py" | sha256sum | cut -d' ' -f1)"
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
  run_dir="$(create_run "$id" "$mode")"
  local image_id image_digest
  image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE_TAG}")"
  image_digest="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}unpublished:${image_id}{{end}}' "${IMAGE_TAG}")"
  local base_digest="${PYTHON_BASE_DIGEST}"
  printf '{"run_id":"%s","source_commit":"%s","source_tree":"%s","lab_helper_digest":"%s","dockerfile_digest":"%s","entrypoint_digest":"%s","compose_digest":"%s","remote_dockerfile_digest":"%s","proxy_dockerfile_digest":"%s","proxy_config_digest":"%s","lab_script_digest":"%s","angular_package_lock_digest":"%s","fixture_manifest_digest":"%s","fixture_generator_digest":"%s","transient_probe_digest":"%s","image":"%s","image_id":"%s","image_digest":"%s","base_image":"python:3.8-slim-bullseye","base_digest":"%s","angular_base_image":"node:12.16","angular_base_digest":"%s","credentials":"synthetic-only"}\n' \
    "$id" "$LEGACY_COMMIT" "${tree}" "${helper_digest}" "${dockerfile_digest}" "${entrypoint_digest}" "${compose_digest}" "${remote_dockerfile_digest}" "${proxy_dockerfile_digest}" "${proxy_config_digest}" "${lab_script_digest}" "${angular_lock_digest}" "${fixture_manifest_digest}" "${fixture_generator_digest}" "${transient_probe_digest}" "$IMAGE_TAG" "$image_id" "$image_digest" "$base_digest" "$ANGULAR_BASE_DIGEST" > "${run_dir}/evidence/manifest.json"
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

run_mode() {
  local id="$1"
  local mode_file="${RUNS_DIR}/${id}/lab-mode"
  [[ -s "$mode_file" ]] || die "run mode marker is missing"
  local mode
  mode="$(tr -d '\r\n' < "$mode_file")"
  [[ "$mode" == stable || "$mode" == transient ]] || die "invalid run mode marker"
  printf '%s' "$mode"
}

require_mode() {
  local id="$1"
  local expected="$2"
  [[ "$(run_mode "$id")" == "$expected" ]] || die "run mode is $(run_mode "$id"); expected $expected"
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
  local transient_mode=0
  if [[ "${1:-}" == --transient-mode ]]; then
    transient_mode=1
    shift
  fi
  local run_dir="${RUNS_DIR}/${id}"
  local private_log_root="${SEEDSYNC_SHIP_PRIVATE_LOG_ROOT:-${run_dir}/logs}"
  [[ -d "$private_log_root" && ! -L "$private_log_root" ]] || die "private log mount is missing or a symlink"
  if [[ -n "${SEEDSYNC_SHIP_PRIVATE_LOG_ROOT:-}" ]]; then
    python - "$private_log_root" <<'PY' || die "private log mount is not owner-only WSL staging"
import os, stat, sys
info = os.lstat(sys.argv[1])
raise SystemExit(not (stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                      and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700))
PY
  fi
  local project="seedsync-upgrade-v086-$(printf '%s' "$id" | tr '[:upper:]' '[:lower:]')"
  local networks
  networks=($(network_names "$id"))
  local autoqueue_enabled autoqueue_patterns_only autoqueue_auto_extract
  read -r autoqueue_enabled autoqueue_patterns_only autoqueue_auto_extract < <(python "${FIXTURE_GENERATOR}" config --manifest "${FIXTURE_MANIFEST}")
  local lftp_home=""
  [[ "$transient_mode" == 1 ]] && lftp_home="/config/.lftp"
  verify_config_volume "$id" || die "config volume identity check failed before compose mount"
  SOURCE_DIR="$(prepare_source)" RUN_ID="$id" RUN_DIR="$run_dir" SEEDSYNC_SHIP_PRIVATE_LOG_ROOT="$private_log_root" CONFIG_VOLUME="$(config_volume_name "$id")" HOST_PORT="${HOST_PORT:-18806}" LAB_NETWORK="${networks[0]}" BROWSER_NETWORK="${networks[1]}" AUTOQUEUE_ENABLED="$autoqueue_enabled" AUTOQUEUE_PATTERNS_ONLY="$autoqueue_patterns_only" AUTOQUEUE_AUTO_EXTRACT="$autoqueue_auto_extract" LAB_TRANSIENT_MODE="$transient_mode" TRANSIENT_LFTP_HOME="$lftp_home" \
    docker compose -p "$project" -f "${LAB_DIR}/compose.yml" "$@"
}

record_runtime_digests() {
  local id="$1"
  local run_dir="${RUNS_DIR}/${id}"
  local dockerfile_digest entrypoint_digest compose_digest remote_dockerfile_digest proxy_dockerfile_digest proxy_config_digest lab_script_digest angular_lock_digest fixture_manifest_digest fixture_generator_digest transient_probe_digest
  dockerfile_digest="$(sha256sum "${LAB_DIR}/Dockerfile" | cut -d' ' -f1)"
  entrypoint_digest="$(sha256sum "${LAB_DIR}/entrypoint.sh" | cut -d' ' -f1)"
  compose_digest="$(sha256sum "${LAB_DIR}/compose.yml" | cut -d' ' -f1)"
  remote_dockerfile_digest="$(sha256sum "${LAB_DIR}/remote.Dockerfile" | cut -d' ' -f1)"
  proxy_dockerfile_digest="$(sha256sum "${LAB_DIR}/proxy.Dockerfile" | cut -d' ' -f1)"
  proxy_config_digest="$(sha256sum "${LAB_DIR}/proxy-nginx.conf" | cut -d' ' -f1)"
  lab_script_digest="$(sha256sum "${LAB_DIR}/lab.sh" | cut -d' ' -f1)"
  angular_lock_digest="$(sha256sum "${LAB_DIR}/angular-package-lock.json" | cut -d' ' -f1)"
  fixture_manifest_digest="$(sha256sum "${FIXTURE_MANIFEST}" | cut -d' ' -f1)"
  fixture_generator_digest="$(sha256sum "${FIXTURE_GENERATOR}" | cut -d' ' -f1)"
  transient_probe_digest="$(sha256sum "${LAB_DIR}/transient.py" | cut -d' ' -f1)"
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
    printf 'fixture_manifest_digest=%s\n' "$fixture_manifest_digest"
    printf 'fixture_generator_digest=%s\n' "$fixture_generator_digest"
    printf 'transient_probe_digest=%s\n' "$transient_probe_digest"
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

model_snapshot() {
  python - "http://127.0.0.1:${HOST_PORT:-18806}/server/stream" <<'PY'
import json
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
    data = []
    event = None
    while True:
        line = response.readline()
        if not line:
            break
        if line.startswith(b"event:"):
            event = line.decode("utf-8").split(":", 1)[1].strip()
        if line.startswith(b"data:"):
            data.append(line.decode("utf-8").split(":", 1)[1].strip())
        elif line in (b"\n", b"\r\n") and data and event == "model-init":
            print("".join(data))
            break
        elif line in (b"\n", b"\r\n"):
            data = []
            event = None
PY
}

validate_model_snapshot() {
  local model_file="$1"
  python - "${FIXTURE_MANIFEST}" "$model_file" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
model = json.load(open(sys.argv[2], encoding="utf-8"))
by_name = {item["name"]: item for item in model}
expected_names = {case["name"] for case in manifest["cases"]}
generated_roots = set(manifest.get("generated_roots", []))
if set(by_name) != expected_names | generated_roots:
    raise SystemExit("model roots differ from fixture manifest: expected {} got {}".format(sorted(expected_names | generated_roots), sorted(by_name)))

def walk(items):
    for item in items:
        yield item
        yield from walk(item.get("children", []))

all_items = list(walk(model))
excluded = set(manifest["excluded"])
if any(item["name"] in excluded for item in all_items):
    raise SystemExit("scanner exclusion leaked into model")

import io
import zipfile

def payload(source):
    if "content" in source:
        return str(source["content"]).encode("utf-8")
    if "generated_bytes" in source:
        size = int(source["generated_bytes"])
        return (b"seedsync-v086-transient-" * ((size // 24) + 1))[:size]
    if "archive" in source:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as archive:
            for member, content in source["archive"].items():
                archive.writestr(member, payload(content) if isinstance(content, dict) else str(content).encode("utf-8"))
        return out.getvalue()
    return None

def expected_node(name, remote, local, state=None):
    if isinstance(local, dict) and local.get("same_as_remote"):
        local = remote
    remote_dir = isinstance(remote, dict) and "directory" in remote
    local_dir = isinstance(local, dict) and "directory" in local
    is_dir = remote_dir or local_dir
    node = {"name": name, "is_dir": is_dir, "remote_size": None, "local_size": None, "children": []}
    if is_dir:
        def expand(mapping):
            tree = {}
            for relative, value in mapping.items():
                cursor = tree
                parts = relative.split("/")
                for part in parts[:-1]:
                    cursor = cursor.setdefault(part, {})
                cursor[parts[-1]] = value
            return tree
        remote_children = expand(remote.get("directory", {})) if remote_dir else {}
        local_children = expand(local.get("directory", {})) if local_dir else {}
        for child in sorted(set(remote_children) | set(local_children)):
            r = remote_children.get(child)
            l = local_children.get(child)
            if l == {"same_as_remote": True}:
                l = r
            if isinstance(r, str):
                r = {"content": r}
            if isinstance(l, str):
                l = {"content": l}
            if isinstance(r, dict) and not ("content" in r or "generated_bytes" in r or "archive" in r or "directory" in r):
                r = {"directory": r}
            if isinstance(l, dict) and not ("content" in l or "generated_bytes" in l or "archive" in l or "directory" in l):
                l = {"directory": l}
            node["children"].append(expected_node(child, r, l))
        node["remote_size"] = sum(child["remote_size"] or 0 for child in node["children"]) if remote_dir else None
        node["local_size"] = sum(child["local_size"] or 0 for child in node["children"]) if local_dir else None
    else:
        if remote is not None:
            remote_payload = payload(remote)
            if remote_payload is None:
                raise ValueError("unsupported remote source for {}: {}".format(name, remote))
            node["remote_size"] = len(remote_payload)
        if local is not None:
            local_payload = payload(local)
            if local_payload is None:
                raise ValueError("unsupported local source for {}: {}".format(name, local))
            node["local_size"] = len(local_payload)
    node["state"] = state or ("downloaded" if not is_dir and node["remote_size"] is not None and node["local_size"] is not None and node["local_size"] >= node["remote_size"] else "default")
    return node

def compare_tree(actual, expected, path):
    if set(actual) != set(expected):
        raise SystemExit("{} child names differ: expected {} got {}".format(path, sorted(expected), sorted(actual)))
    for name, exp in expected.items():
        item = actual[name]
        for field in ("is_dir", "remote_size", "local_size", "state"):
            if item.get(field) != exp[field]:
                raise SystemExit("{} field {} expected {!r} got {!r}".format(path + "/" + name, field, exp[field], item.get(field)))
        compare_tree({child["name"]: child for child in item.get("children", [])}, {child["name"]: child for child in exp["children"]}, path + "/" + name)

expected = {}
for case in manifest["cases"]:
    remote = case.get("remote")
    local = case.get("local")
    if local and local.get("same_as_remote"):
        local = remote
    node = expected_node(case["name"], remote, local, case["expected"]["backend_state"])
    if case["expected"]["backend_state"] in {"downloaded", "extracted"} and node["local_size"] is None:
        node["local_size"] = node["remote_size"]
    expected[case["name"]] = node
generated = {}
for case in manifest["cases"]:
    if case["expected"]["autoqueue"] != "auto-extract":
        continue
    for member, content in case["remote"].get("archive", {}).items():
        parts = member.split("/")
        if parts and parts[0] == "extracted":
            parts = parts[1:]
        cursor = generated
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {"__node__": expected_node(part, None, {"directory": {}})})
        member_source = content if isinstance(content, dict) else {"content": str(content)}
        cursor[parts[-1]] = {"__node__": expected_node(parts[-1], None, member_source)}
if generated:
    def generated_node(name, tree):
        children = []
        for child, value in tree.items():
            if "__node__" in value:
                node = value["__node__"]
                if isinstance(value, dict) and len(value) == 1:
                    children.append(node)
            else:
                children.append(generated_node(child, value))
        return {"name": name, "is_dir": True, "state": "default", "remote_size": None, "local_size": sum(c.get("local_size") or 0 for c in children), "children": children}
    expected["extracted"] = generated_node("extracted", generated)
actual = {item["name"]: item for item in model}
compare_tree(actual, expected, "model")

for case in manifest["cases"]:
    item = by_name[case["name"]]
    expected = case["expected"]
    backend = item["state"]
    remote = item.get("remote_size") or 0
    local = item.get("local_size") or 0
    ui = "stopped" if backend == "default" and local > 0 and remote > 0 else backend
    if backend != expected["backend_state"] or ui != expected["ui_status"]:
        raise SystemExit("{} expected backend/ui {}/{} got {}/{}".format(case["id"], expected["backend_state"], expected["ui_status"], backend, ui))
print("ok")
PY
}

wait_for_manifest_model() {
  local id="$1"
  local run_dir="${RUNS_DIR}/${id}"
  local model_file="${run_dir}/evidence/model.json"
  local error_file="${run_dir}/evidence/model-validation-error.txt"
  local attempts=0
  while (( attempts < 45 )); do
    if model_snapshot > "${model_file}.tmp" 2>/dev/null && validate_model_snapshot "${model_file}.tmp" > /dev/null 2> "$error_file"; then
      mv "${model_file}.tmp" "$model_file"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 1
  done
  [[ -s "${model_file}.tmp" ]] && mv "${model_file}.tmp" "$model_file"
  die "fixture model did not settle to manifest expectations; see ${model_file}"
}

validate_persisted_markers() {
  local id="$1"
  local container="seedsync-upgrade-v086-${id}"
  docker exec "$container" cat /config/controller.persist | python - "${FIXTURE_MANIFEST}" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
persist = json.load(sys.stdin)
if set(persist) != {"downloaded", "extracted"}:
    raise SystemExit("controller.persist keys differ from historical contract: {}".format(sorted(persist)))
for key in ("downloaded", "extracted"):
    if not isinstance(persist[key], list) or len(persist[key]) != len(set(persist[key])) or not all(isinstance(item, str) for item in persist[key]):
        raise SystemExit("controller.persist {} marker array is malformed".format(key))
    actual = set(persist[key])
    required = {case["name"] for case in manifest["cases"] if case["expected"]["persistence"].get(key)}
    if actual != required:
        raise SystemExit("controller.persist {} markers differ: expected {} got {}".format(key, sorted(required), sorted(actual)))
PY
}

wait_for_persisted_markers() {
  local id="$1"
  local run_dir="${RUNS_DIR}/${id}"
  local error_file="${run_dir}/evidence/persist-validation-error.txt"
  local attempts=0
  while (( attempts < 30 )); do
    if validate_persisted_markers "$id" > /dev/null 2> "$error_file"; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 1
  done
  die "controller.persist did not settle to exact manifest markers; see ${error_file}"
}

start() {
  local mode="${1:-stable}"
  [[ "$mode" == stable || "$mode" == transient ]] || die "invalid start mode"
  local id
  id="$(selected_run_dir)"
  require_mode "$id" "$mode"
  validate_host_port "${HOST_PORT:-18806}"
  ensure_networks "$id"
  if [[ "$mode" == transient ]]; then
    compose "$id" --transient-mode up -d --build --force-recreate
  else
    compose "$id" up -d --build --force-recreate
  fi
  local run_dir="${RUNS_DIR}/${id}"
  record_runtime_digests "$id"
  compose "$id" ps > "${run_dir}/evidence/compose-ps.txt"
  compose "$id" logs --no-color | redact > "${run_dir}/evidence/compose.log"
  echo "http://127.0.0.1:${HOST_PORT:-18806}/"
}

status() {
  local id
  id="$(selected_run_dir)"
  require_mode "$id" stable
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
  wait_for_manifest_model "$id"
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

restart() {
  local id
  id="$(selected_run_dir)"
  require_mode "$id" stable
  stop
  start
  wait_for_remote_scan "$id"
  wait_for_manifest_model "$id"
  wait_for_persisted_markers "$id"
}

transient() {
  local id
  id="$(selected_run_dir)"
  require_mode "$id" transient
  validate_host_port "${HOST_PORT:-18806}"
  local transient_lftp_home=/config/.lftp transient_parallel_jobs=1 transient_parallel_files=1 transient_connections=1
  [[ "$transient_lftp_home" == /config/.lftp && "$transient_parallel_jobs" =~ ^[1-9][0-9]*$ && "$transient_parallel_files" =~ ^[1-9][0-9]*$ && "$transient_connections" =~ ^[1-9][0-9]*$ ]] || die "invalid fixed transient lftp controls"
  local run_dir="${RUNS_DIR}/${id}"
  [[ ! -e "${run_dir}/evidence/transient-state.json" && ! -e "${run_dir}/evidence/transient-summary.json" ]] || die "transient probe is single-use for a run; choose a fresh RUN_ID"
  local transient_name
  while IFS= read -r transient_name; do
    [[ ! -e "${run_dir}/downloads/${transient_name}" ]] || die "transient fixture already has local output: ${transient_name}; choose a fresh RUN_ID"
  done < <(python - "${FIXTURE_MANIFEST}" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for case in manifest["cases"]:
    if case.get("transient"):
        print(case["name"])
PY
)
  local legacy_container="seedsync-upgrade-v086-${id}"
  docker exec "$legacy_container" sh -c 'test ! -e /config/.lftp && umask 077 && mkdir /config/.lftp && printf "set net:limit-rate 256K\\nset cmd:queue-parallel 1\\nset mirror:parallel-transfer-count 1\\nset pget:default-n 1\\nset mirror:use-pget-n 1\\nset net:connection-limit 1\\nset net:timeout 3\\nset net:max-retries 1\\nset net:reconnect-interval-base 1\\n" > /config/.lftp/rc' || die "unable to create transient lftp controls in retained config volume"
  stop
  start transient
  local container="seedsync-upgrade-v086-${id}"
  docker exec "$container" sh -c 'test "$LFTP_HOME" = /config/.lftp && test "$(grep -Ec "^set (net:limit-rate 256K|cmd:queue-parallel 1|mirror:parallel-transfer-count 1|pget:default-n 1|mirror:use-pget-n 1|net:connection-limit 1|net:timeout 3|net:max-retries 1|net:reconnect-interval-base 1)$" /config/.lftp/rc)" -eq 9 && grep -E "^(num_max_parallel_downloads|num_max_parallel_files_per_download|num_max_connections_per_root_file|num_max_connections_per_dir_file|num_max_total_connections) = 1$" /config/settings.cfg && cat /config/.lftp/rc' > "${run_dir}/evidence/lftp-controls.txt" || die "transient lftp controls were not loaded inside legacy container"
  timeout 35s docker exec "$container" sh -c '
    probe_dir="/tmp/upgrade-v086-lftp-probe-$$" && mkdir "$probe_dir"
    LFTP_HOME=/config/.lftp lftp -p 1234 -u remoteuser,remotepass sftp://upgrade_remote <<EOF || true
set cmd:queue-parallel 1
set net:limit-rate 256K
set net:timeout 3
set net:max-retries 1
set net:reconnect-interval-base 1
set mirror:parallel-transfer-count 1
set pget:default-n 1
queue pget -c "/home/remoteuser/files/transient-large.bin" -o "$probe_dir/"
queue pget -c "/home/remoteuser/files/transient-manual.zip" -o "$probe_dir/"
jobs -v
sleep 2
jobs -v
kill all
bye
EOF
  ' | sed -E 's#(sftp://[^:]+:)[^@]+@#\1<redacted>@#g' > "${run_dir}/evidence/lftp-jobs.txt" || die "transient lftp jobs probe failed"
  grep -q "Commands queued:" "${run_dir}/evidence/lftp-jobs.txt" || die "transient lftp jobs probe did not expose a queued command"
  timeout 20s docker exec "$container" sh -c '
    printf "value=0\\n"
    LFTP_HOME=/config/.lftp lftp -p 1234 -u remoteuser,remotepass sftp://upgrade_remote <<EOF || true
set cmd:queue-parallel 0
set net:timeout 3
set net:max-retries 1
set -a | grep cmd:queue-parallel
bye
EOF
    printf "value=false\\n"
    LFTP_HOME=/config/.lftp lftp -p 1234 -u remoteuser,remotepass sftp://upgrade_remote <<EOF || true
set cmd:queue-parallel false
set net:timeout 3
set net:max-retries 1
bye
EOF
  ' 2>&1 | sed -E 's#(sftp://[^:]+:)[^@]+@#\1<redacted>@#g' > "${run_dir}/evidence/lftp-setting-guard.txt"
  printf 'historical_controller_minimum=1 (num_parallel_jobs setter rejects values below one)\n' >> "${run_dir}/evidence/lftp-setting-guard.txt"
  grep -q "set cmd:queue-parallel 0" "${run_dir}/evidence/lftp-setting-guard.txt" || die "transient lftp setting guard did not prove raw lftp accepts 0"
  grep -q "invalid unsigned number" "${run_dir}/evidence/lftp-setting-guard.txt" || die "transient lftp setting guard did not prove raw lftp rejects false"
  wait_for_remote_scan "$id"
  python "${LAB_DIR}/transient.py" --base-url "http://127.0.0.1:${HOST_PORT:-18806}" --manifest "${FIXTURE_MANIFEST}" --fixture-evidence "${run_dir}/evidence/fixture-evidence.json" --evidence "${run_dir}/evidence/transient-state.json" > "${run_dir}/evidence/transient-summary.json"
  compose "$id" logs --no-color | redact > "${run_dir}/evidence/compose.log"
}

usage() {
  cat <<'EOF'
Usage: lab.sh <preflight|build|build-transient|start|status|restart|transient|stop|check-run-tree|verify-volume|verify-protected|verify-snapshotter|verify-downloads-snapshotter|create-downloads-restorer|verify-downloads-restorer|protected-storage-self-check|validator-evidence-path-self-check|cwd-probe>

RUN_ID selects a retained run; build creates a unique run when omitted.
HOST_PORT defaults to 18806 and binds only to loopback.
EOF
}

main() {
  umask 077
  case "${1:-}" in
    preflight) preflight ;;
    build) build stable ;;
    build-transient) build transient ;;
    start) start "${2:-stable}" ;;
    status) status ;;
    restart) restart ;;
    transient) transient ;;
    stop) stop ;;
    check-run-tree) check_run_tree "${2:?RUN_ID required}" ;;
    verify-volume) verify_config_volume "${2:?RUN_ID required}" ;;
    verify-validator) verify_validator_container "${2:?RUN_ID required}" ;;
    verify-protected) verify_protected_volume "${2:?RUN_ID required}" ;;
    verify-snapshotter) verify_snapshotter_container "${2:?RUN_ID required}" ;;
    verify-downloads-snapshotter) verify_downloads_snapshotter_container "${2:?RUN_ID required}" ;;
    create-downloads-restorer) create_downloads_restorer_container "${2:?RUN_ID required}" ;;
    verify-downloads-restorer) verify_downloads_restorer_container "${2:?RUN_ID required}" ;;
    protected-storage-self-check) protected_storage_self_check ;;
    validator-evidence-path-self-check) validator_evidence_path_self_check ;;
    cwd-probe) cwd_probe "${2:?expected repository root required}" ;;
    *) usage; return 2 ;;
  esac
}

main "$@"
