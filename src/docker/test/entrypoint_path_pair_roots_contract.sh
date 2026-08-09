#!/usr/bin/env bash
# Bounded entrypoint-only contract. Storage-use validation belongs to Python.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../../.." && pwd -P)"
ENTRYPOINT="${ROOT_DIR}/src/docker/build/docker-image/entrypoint.sh"
IMAGE="${SEEDSYNC_ENTRYPOINT_TEST_IMAGE:-seedsync-local:compose}"
RUN_ID="${SEEDSYNC_ENTRYPOINT_ROOTS_RUN_ID:-entrypoint-roots-$(date +%s)-$$}"
PROBE_DIR="$(mktemp -d /tmp/seedsync-entrypoint-roots.XXXXXX)"
RUNTIME_UID=99
RUNTIME_GID=100
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
SUCCESS=0
declare -a CASE_CONTAINERS=()

cleanup() {
    local container
    for container in "${CASE_CONTAINERS[@]}"; do
        timeout 10s docker rm -f "$container" >/dev/null 2>&1 || true
    done
    timeout 15s docker run --rm --user 0:0 \
        --mount "type=bind,src=${PROBE_DIR},dst=/runtime" \
        --entrypoint /bin/sh "$IMAGE" -c "chmod -R u+rwX /runtime && chown -R ${HOST_UID}:${HOST_GID} /runtime" \
        >/dev/null 2>&1 || true
    if [ "$SUCCESS" = "1" ]; then
        rm -rf -- "$PROBE_DIR"
    else
        echo "WARNING: retaining failed fixture ${PROBE_DIR}" >&2
    fi
}
trap cleanup EXIT INT TERM

if ! timeout 15s docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: required Docker image ${IMAGE} is unavailable or Docker is unhealthy" >&2
    exit 1
fi

make_runtime_dir() {
    local path="$1"
    mkdir -p -- "$path"
    timeout 15s docker run --rm --user 0:0 \
        --mount "type=bind,src=${path},dst=/runtime" \
        --entrypoint /bin/sh "$IMAGE" -c "chown -R ${RUNTIME_UID}:${RUNTIME_GID} /runtime && chmod 0775 /runtime"
}

run_case() {
    local name="$1"
    shift
    CASE_CONTAINERS+=("${RUN_ID}-${name}")
    timeout 30s docker run --rm --name "${RUN_ID}-${name}" "$@"
}

# UID 99 must reach normal entrypoint execution without an explicit
# /downloads bind; no configuration parsing or configured-root probing occurs.
no_downloads_config="${PROBE_DIR}/no-downloads-config"
make_runtime_dir "$no_downloads_config"
run_case no-downloads --user "${RUNTIME_UID}:${RUNTIME_GID}" \
    --mount "type=bind,src=${no_downloads_config},dst=/config" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" /bin/true \
    > "${PROBE_DIR}/no-downloads.log" 2>&1

# The original writable /downloads bind remains a supported entrypoint path.
legacy_config="${PROBE_DIR}/legacy-config"
legacy_downloads="${PROBE_DIR}/legacy-downloads"
make_runtime_dir "$legacy_config"
make_runtime_dir "$legacy_downloads"
run_case legacy-downloads --user "${RUNTIME_UID}:${RUNTIME_GID}" \
    --mount "type=bind,src=${legacy_config},dst=/config" \
    --mount "type=bind,src=${legacy_downloads},dst=/downloads" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" /bin/true \
    > "${PROBE_DIR}/legacy-downloads.log" 2>&1

SUCCESS=1
echo "entrypoint roots contract: passed"
