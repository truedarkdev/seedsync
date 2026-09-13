#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ENTRYPOINT="${ROOT_DIR}/src/docker/build/docker-image/entrypoint.sh"
DOCKERFILE="${ROOT_DIR}/src/docker/build/docker-image/Dockerfile"
IMAGE="${SEEDSYNC_ENTRYPOINT_TEST_IMAGE:-seedsync-local:compose}"
DOCKER_BIN="${SEEDSYNC_DOCKER_BIN:-docker}"
ENTRYPOINT_MOUNT_PATH="${ENTRYPOINT}"
if [[ "${DOCKER_BIN}" == *.exe ]]; then
    ENTRYPOINT_MOUNT_PATH="$(wslpath -w "${ENTRYPOINT}")"
fi

if ! "${DOCKER_BIN}" image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "ERROR: Docker test image is unavailable: ${IMAGE}" >&2
    exit 1
fi

if grep -F -- '--logdir' "${DOCKERFILE}" >/dev/null; then
    echo "ERROR: Dockerfile must not enable durable breadcrumb logdir by default" >&2
    exit 1
fi
if grep -F -- 'ORG_POSTARGS' "${ENTRYPOINT}" >/dev/null || grep -F -- 'eval ' "${ENTRYPOINT}" >/dev/null; then
    echo "ERROR: durable breadcrumb logdir must not use passthrough or eval" >&2
    exit 1
fi

run_probe() {
    local value_marker="$1"
    shift
    local -a env_args=()
    if [ "${value_marker}" != "__unset__" ]; then
        env_args=(-e "SEEDSYNC_ENABLE_DURABLE_BREADCRUMB_LOGDIR=${value_marker}")
    fi

    "${DOCKER_BIN}" run --rm \
        --mount "type=bind,src=${ENTRYPOINT_MOUNT_PATH},dst=/scripts/entrypoint.sh,readonly" \
        --entrypoint /scripts/entrypoint.sh \
        -e PUID=1000 \
        -e PGID=1000 \
        "${env_args[@]}" \
        "${IMAGE}" \
        "$@"
}

run_probe_with_volume() {
    local volume_name="$1"
    local value_marker="$2"
    shift 2
    local -a env_args=()
    if [ "${value_marker}" != "__unset__" ]; then
        env_args=(-e "SEEDSYNC_ENABLE_DURABLE_BREADCRUMB_LOGDIR=${value_marker}")
    fi

    "${DOCKER_BIN}" run --rm \
        --mount "type=volume,src=${volume_name},dst=/config" \
        --mount "type=bind,src=${ENTRYPOINT_MOUNT_PATH},dst=/scripts/entrypoint.sh,readonly" \
        --entrypoint /scripts/entrypoint.sh \
        -e PUID=1000 \
        -e PGID=1000 \
        "${env_args[@]}" \
        "${IMAGE}" \
        "$@"
}

assert_last_line() {
    local expected="$1"
    shift
    local output
    output="$("$@")"
    if [ "$(printf '%s\n' "${output}" | tail -n 1)" != "${expected}" ]; then
        echo "ERROR: expected final probe line '${expected}', got:" >&2
        printf '%s\n' "${output}" >&2
        exit 1
    fi
}

assert_last_line '[]' run_probe __unset__ python3 -c 'import sys; print(sys.argv[1:])'
assert_last_line '[]' run_probe 0 python3 -c 'import sys; print(sys.argv[1:])'
assert_last_line "['--logdir', '/config/logs']" run_probe 1 python3 -c 'import sys; print(sys.argv[1:])'

assert_last_line "['--web-bind-host', 'foo']" run_probe 0 /bin/bash -lc "exec python3 -c 'import sys; print(sys.argv[1:])' --web-bind-host foo"
assert_last_line "['--logdir', '/config/logs', '--web-bind-host', 'foo']" run_probe 1 /bin/bash -lc "exec python3 -c 'import sys; print(sys.argv[1:])' --web-bind-host foo"

for invalid_value in invalid ""; do
    if invalid_output="$(run_probe "${invalid_value}" --bootstrap-default-config 2>&1)"; then
        echo "ERROR: invalid durable breadcrumb logdir value was accepted: [${invalid_value}]" >&2
        exit 1
    fi
    if ! printf '%s\n' "${invalid_output}" | grep -F -- 'SEEDSYNC_ENABLE_DURABLE_BREADCRUMB_LOGDIR must be 0 or 1' >/dev/null; then
        echo "ERROR: invalid durable breadcrumb logdir value did not fail with the expected message: [${invalid_value}]" >&2
        printf '%s\n' "${invalid_output}" >&2
        exit 1
    fi
done

RUN_ID="$(date -u +%Y%m%dT%H%M%S%N)-${BASHPID}-${RANDOM}-${RANDOM}"
TEST_VOLUME="seedsync-entrypoint-durable-${RUN_ID}"
TEST_VOLUME_CREATED=0
cleanup_test_volume() {
    if [ "${TEST_VOLUME_CREATED}" -eq 1 ]; then
        "${DOCKER_BIN}" volume rm "${TEST_VOLUME}" >/dev/null 2>&1 || true
    fi
}
trap cleanup_test_volume EXIT

if "${DOCKER_BIN}" volume inspect "${TEST_VOLUME}" >/dev/null 2>&1; then
    echo "ERROR: refusing to use pre-existing test volume: ${TEST_VOLUME}" >&2
    exit 1
fi
VOLUME_LABEL="seedsync-entrypoint-contract-run=${RUN_ID}"
if ! "${DOCKER_BIN}" volume create --label "${VOLUME_LABEL}" "${TEST_VOLUME}" >/dev/null; then
    echo "ERROR: failed to create test volume: ${TEST_VOLUME}" >&2
    exit 1
fi
if ! CREATED_VOLUME_LABEL="$("${DOCKER_BIN}" volume inspect --format '{{ index .Labels "seedsync-entrypoint-contract-run" }}' "${TEST_VOLUME}")"; then
    echo "ERROR: could not verify test volume ownership: ${TEST_VOLUME}" >&2
    exit 1
fi
if [ "${CREATED_VOLUME_LABEL}" != "${RUN_ID}" ]; then
    echo "ERROR: test volume ownership marker mismatch: ${TEST_VOLUME}" >&2
    exit 1
fi
TEST_VOLUME_CREATED=1
"${DOCKER_BIN}" run --rm \
    --mount "type=volume,src=${TEST_VOLUME},dst=/config" \
    --entrypoint /bin/sh \
    "${IMAGE}" \
    -c 'if [ -e /config/logs ]; then mv /config/logs /config/logs-removed; fi; test ! -e /config/logs'
run_probe_with_volume "${TEST_VOLUME}" 1 \
    python3 /app/python/seedsync.py \
    -c /config --html /app/html --scanfs /app/python/scan_fs.py \
    --web-bind-host 0.0.0.0 --exit >/dev/null 2>&1
assert_last_line '700:1000:1000' run_probe_with_volume "${TEST_VOLUME}" 0 /bin/sh -c 'stat -c "%a:%u:%g" /config/logs'

echo "durable breadcrumb logdir entrypoint contract passed"
