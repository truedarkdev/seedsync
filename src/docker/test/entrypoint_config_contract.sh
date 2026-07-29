#!/usr/bin/env bash
# Retained Docker evidence for the entrypoint configuration-root trust boundary.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../../.." && pwd -P)"
ENTRYPOINT="${ROOT_DIR}/src/docker/build/docker-image/entrypoint.sh"
IMAGE="${SEEDSYNC_ENTRYPOINT_TEST_IMAGE:-seedsync-local:compose}"
RUN_ID="${SEEDSYNC_ENTRYPOINT_CONTRACT_RUN_ID:-entrypoint-config-contract-20260726-r18}"
EVIDENCE_DIR="${ROOT_DIR}/tmp/upgrade-v086/${RUN_ID}"
WINDOWS_CONFIG="${EVIDENCE_DIR}/windows-drvfs-config"
POSIX_PROBE="$(mktemp -d /tmp/seedsync-entrypoint-contract.XXXXXX)"
POSIX_VOLUME="seedsync-entrypoint-config-${RUN_ID}"
RUNTIME_UID=1101
RUNTIME_GID=1102
IMAGE_DEFAULT_UID=1000
IMAGE_DEFAULT_GID=1000
WRONG_OWNER_UID=2201
WRONG_OWNER_GID=2201

cleanup_posix_probe() {
    local cleanup_uid cleanup_gid
    cleanup_uid="$(id -u)"
    cleanup_gid="$(id -g)"
    if docker image inspect "$IMAGE" >/dev/null 2>&1; then
        docker run --rm --user 0:0 \
            --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
            --entrypoint /bin/sh "$IMAGE" -c "chown -R ${cleanup_uid}:${cleanup_gid} /probe && chmod -R u+rwx /probe" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$POSIX_PROBE"
}
trap cleanup_posix_probe EXIT

if [ -e "$EVIDENCE_DIR" ]; then
    echo "ERROR: retained evidence directory already exists: $EVIDENCE_DIR" >&2
    exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: required test image $IMAGE is unavailable; build this repository's Docker image first" >&2
    exit 1
fi
if docker volume inspect "$POSIX_VOLUME" >/dev/null 2>&1; then
    echo "ERROR: retained test volume already exists: $POSIX_VOLUME" >&2
    exit 1
fi

mkdir -p "$EVIDENCE_DIR" "$WINDOWS_CONFIG"
chmod 0777 "$WINDOWS_CONFIG"
{ stat -c 'path=%n uid=%u gid=%g mode=%a' "$WINDOWS_CONFIG"; stat -f -c 'filesystem=%T' "$WINDOWS_CONFIG"; } > "${EVIDENCE_DIR}/windows-drvfs-before.txt"
docker compose -f "${ROOT_DIR}/compose.local.yml" -f "${ROOT_DIR}/compose.windows.yml" config \
    > "${EVIDENCE_DIR}/compose-windows-effective.yml"
grep -A3 -F 'source: seedsync-local-config' "${EVIDENCE_DIR}/compose-windows-effective.yml" | grep -Fx '        target: /config'

docker volume create --name "$POSIX_VOLUME" \
    --label "seedsync.entrypoint.contract=${RUN_ID}" > "${EVIDENCE_DIR}/posix-volume-name.txt"
docker run --rm --name "${RUN_ID}-posix-root-fixture" \
    --user 0:0 \
    --mount "type=volume,src=${POSIX_VOLUME},dst=/config" \
    --entrypoint /bin/sh "$IMAGE" -c "\
        chown 0:0 /config && chmod 0755 /config && \
        mkdir -p /config/root-owned /config/runtime-owned /config/default-owned && \
        printf 'root-owned sentinel\\n' > /config/root-owned/sentinel && \
        printf 'runtime-owned sentinel\\n' > /config/runtime-owned/sentinel && \
        printf 'default-owned sentinel\\n' > /config/default-owned/sentinel && \
        chown -R 0:0 /config/root-owned && \
        chown -R ${RUNTIME_UID}:${RUNTIME_GID} /config/runtime-owned && \
        chown -R ${IMAGE_DEFAULT_UID}:${IMAGE_DEFAULT_GID} /config/default-owned"
docker run --rm --name "${RUN_ID}-posix-root-before" \
    --user 0:0 \
    --mount "type=volume,src=${POSIX_VOLUME},dst=/config" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a" /config' \
    > "${EVIDENCE_DIR}/posix-root-before.txt"
grep -Fxq 'uid=0 gid=0 mode=755' "${EVIDENCE_DIR}/posix-root-before.txt"
docker run --rm --name "${RUN_ID}-posix-first" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" \
    --mount "type=volume,src=${POSIX_VOLUME},dst=/config" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root \
    > "${EVIDENCE_DIR}/posix-first.log" 2>&1
docker run --rm --name "${RUN_ID}-posix-second" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" \
    --mount "type=volume,src=${POSIX_VOLUME},dst=/config" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root \
    > "${EVIDENCE_DIR}/posix-idempotence.log" 2>&1
docker run --rm --name "${RUN_ID}-posix-stat" \
    --mount "type=volume,src=${POSIX_VOLUME},dst=/config" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a" /config /config/root-owned /config/root-owned/sentinel /config/runtime-owned /config/runtime-owned/sentinel /config/default-owned /config/default-owned/sentinel; stat -f -c "filesystem=%T" /config' \
    > "${EVIDENCE_DIR}/posix-after.txt"
if ! grep -Fxq "uid=${RUNTIME_UID} gid=${RUNTIME_GID} mode=700" "${EVIDENCE_DIR}/posix-after.txt" || \
   [[ "$(grep -Fc "uid=${RUNTIME_UID} gid=${RUNTIME_GID}" "${EVIDENCE_DIR}/posix-after.txt")" -ne 7 ]] || \
   ! grep -Eq '^filesystem=(ext2/ext3|ext4|xfs|btrfs|zfs|tmpfs|overlay)$' "${EVIDENCE_DIR}/posix-after.txt"; then
    echo "ERROR: named-volume config root did not reach the expected non-default owner/mode" >&2
    exit 1
fi
docker run --rm --name "${RUN_ID}-posix-preflight" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" \
    --mount "type=volume,src=${POSIX_VOLUME},dst=/config" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" \
    /bin/true \
    > "${EVIDENCE_DIR}/posix-preflight.log" 2>&1

if docker run --rm --name "${RUN_ID}-drvfs-negative" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" \
    --mount "type=bind,src=${WINDOWS_CONFIG},dst=/config" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root \
    > "${EVIDENCE_DIR}/windows-drvfs-negative.log" 2>&1; then
    echo "ERROR: Windows/DrvFS-backed config root unexpectedly passed the filesystem contract" >&2
    exit 1
fi
{ stat -c 'path=%n uid=%u gid=%g mode=%a' "$WINDOWS_CONFIG"; stat -f -c 'filesystem=%T' "$WINDOWS_CONFIG"; } > "${EVIDENCE_DIR}/windows-drvfs-after.txt"
grep -Eq 'filesystem type .* is not an allowed local POSIX filesystem' "${EVIDENCE_DIR}/windows-drvfs-negative.log"

SPACE_CONFIG="$POSIX_PROBE/config with space"
RUNTIME_OWNER_CONFIG="$POSIX_PROBE/runtime-owner-config"
WRONG_OWNER_CONFIG="$POSIX_PROBE/wrong-owner-config"
UNSAFE_ROOT_CONFIG="$POSIX_PROBE/unsafe-root-config"
UNSAFE_RUNTIME_CONFIG="$POSIX_PROBE/unsafe-runtime-config"
mkdir -p "$POSIX_PROBE/config" "$SPACE_CONFIG" "$POSIX_PROBE/downloads" "$POSIX_PROBE/mounts" "$POSIX_PROBE/root-link-target"
chmod 0755 "$POSIX_PROBE"
printf 'downloads sentinel\n' > "$POSIX_PROBE/downloads/sentinel"
printf 'mounts sentinel\n' > "$POSIX_PROBE/mounts/sentinel"
chmod 0755 "$POSIX_PROBE/config" "$POSIX_PROBE/downloads" "$POSIX_PROBE/mounts" "$POSIX_PROBE/root-link-target"
docker run --rm --name "${RUN_ID}-space-mountinfo" \
    --mount "type=bind,src=${SPACE_CONFIG},dst=/probe/config with space" \
    --entrypoint /bin/sh "$IMAGE" -c 'grep -F "\\040" /proc/self/mountinfo' \
    > "${EVIDENCE_DIR}/space-mountinfo.txt"
if docker run --rm --name "${RUN_ID}-space-root" \
    --mount "type=bind,src=${SPACE_CONFIG},dst=/probe/config with space" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root '/probe/config with space' \
    > "${EVIDENCE_DIR}/space-root.log" 2>&1; then
    :
else
    echo "ERROR: mountinfo \\040 root path did not pass the config-root contract" >&2
    exit 1
fi
grep -Fq 'Verified config root: /probe/config with space' "${EVIDENCE_DIR}/space-root.log"

ln -s root-link-target "$POSIX_PROBE/root-link"
stat -c 'path=%n uid=%u gid=%g mode=%a inode=%i' "$POSIX_PROBE/root-link-target" "$POSIX_PROBE/downloads" "$POSIX_PROBE/mounts" > "${EVIDENCE_DIR}/root-link-before.txt"
if docker run --rm --name "${RUN_ID}-root-link" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --mount "type=bind,src=${POSIX_PROBE}/downloads,dst=/downloads" \
    --mount "type=bind,src=${POSIX_PROBE}/mounts,dst=/mounts" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/root-link \
    > "${EVIDENCE_DIR}/root-link-negative.log" 2>&1; then
    echo "ERROR: root symlink unexpectedly passed the config-only contract" >&2
    exit 1
fi
stat -c 'path=%n uid=%u gid=%g mode=%a inode=%i' "$POSIX_PROBE/root-link-target" "$POSIX_PROBE/downloads" "$POSIX_PROBE/mounts" > "${EVIDENCE_DIR}/root-link-after.txt"
cmp "${EVIDENCE_DIR}/root-link-before.txt" "${EVIDENCE_DIR}/root-link-after.txt"
grep -Fq 'root is a symlink' "${EVIDENCE_DIR}/root-link-negative.log"

printf 'not a config directory\n' > "$POSIX_PROBE/not-a-directory"
chmod 0644 "$POSIX_PROBE/not-a-directory"
stat -c 'path=%n uid=%u gid=%g mode=%a inode=%i' "$POSIX_PROBE/not-a-directory" > "${EVIDENCE_DIR}/root-file-before.txt"
if docker run --rm --name "${RUN_ID}-root-file" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --mount "type=bind,src=${POSIX_PROBE}/downloads,dst=/downloads" \
    --mount "type=bind,src=${POSIX_PROBE}/mounts,dst=/mounts" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/not-a-directory \
    > "${EVIDENCE_DIR}/root-file-negative.log" 2>&1; then
    echo "ERROR: non-directory root unexpectedly passed the config-only contract" >&2
    exit 1
fi
stat -c 'path=%n uid=%u gid=%g mode=%a inode=%i' "$POSIX_PROBE/not-a-directory" > "${EVIDENCE_DIR}/root-file-after.txt"
cmp "${EVIDENCE_DIR}/root-file-before.txt" "${EVIDENCE_DIR}/root-file-after.txt"
grep -Fq 'cannot open a real root directory' "${EVIDENCE_DIR}/root-file-negative.log"

ln -s ../root-link-target "$POSIX_PROBE/config/nested-link"
stat -c 'path=%n uid=%u gid=%g mode=%a inode=%i' "$POSIX_PROBE/root-link-target" > "${EVIDENCE_DIR}/nested-link-before.txt"
if docker run --rm --name "${RUN_ID}-nested-link" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --mount "type=bind,src=${POSIX_PROBE}/downloads,dst=/downloads" \
    --mount "type=bind,src=${POSIX_PROBE}/mounts,dst=/mounts" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/config \
    > "${EVIDENCE_DIR}/nested-link-negative.log" 2>&1; then
    echo "ERROR: nested symlink unexpectedly passed the config-only contract" >&2
    exit 1
fi
stat -c 'path=%n uid=%u gid=%g mode=%a inode=%i' "$POSIX_PROBE/root-link-target" > "${EVIDENCE_DIR}/nested-link-after.txt"
cmp "${EVIDENCE_DIR}/nested-link-before.txt" "${EVIDENCE_DIR}/nested-link-after.txt"
grep -Fq 'contains a symlink' "${EVIDENCE_DIR}/nested-link-negative.log"
chmod 0755 "$POSIX_PROBE/config"

rm "$POSIX_PROBE/config/nested-link"
mkdir -p "$POSIX_PROBE/nested-mount-source"
chmod 0755 "$POSIX_PROBE/nested-mount-source"
stat -c 'path=%n uid=%u gid=%g mode=%a inode=%i' "$POSIX_PROBE/nested-mount-source" > "${EVIDENCE_DIR}/nested-mount-before.txt"
if docker run --rm --name "${RUN_ID}-nested-mount" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --mount "type=bind,src=${POSIX_PROBE}/nested-mount-source,dst=/probe/config/nested-mount" \
    --mount "type=bind,src=${POSIX_PROBE}/downloads,dst=/downloads" \
    --mount "type=bind,src=${POSIX_PROBE}/mounts,dst=/mounts" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/config \
    > "${EVIDENCE_DIR}/nested-mount-negative.log" 2>&1; then
    echo "ERROR: nested mount unexpectedly passed the config-only contract" >&2
    exit 1
fi
stat -c 'path=%n uid=%u gid=%g mode=%a inode=%i' "$POSIX_PROBE/nested-mount-source" > "${EVIDENCE_DIR}/nested-mount-after.txt"
cmp "${EVIDENCE_DIR}/nested-mount-before.txt" "${EVIDENCE_DIR}/nested-mount-after.txt"
grep -Fq 'contains a nested mount or device' "${EVIDENCE_DIR}/nested-mount-negative.log"
chmod 0755 "$POSIX_PROBE/config"

printf 'external hard-link sentinel\n' > "$POSIX_PROBE/hard-link-sentinel"
chmod 0640 "$POSIX_PROBE/hard-link-sentinel"
ln "$POSIX_PROBE/hard-link-sentinel" "$POSIX_PROBE/config/hard-link-alias"
stat -c 'path=%n uid=%u gid=%g mode=%a dev=%d inode=%i nlink=%h' \
    "$POSIX_PROBE/hard-link-sentinel" "$POSIX_PROBE/config/hard-link-alias" > "${EVIDENCE_DIR}/hard-link-before.txt"
if docker run --rm --name "${RUN_ID}-hard-link" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --mount "type=bind,src=${POSIX_PROBE}/downloads,dst=/downloads" \
    --mount "type=bind,src=${POSIX_PROBE}/mounts,dst=/mounts" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/config \
    > "${EVIDENCE_DIR}/hard-link-negative.log" 2>&1; then
    echo "ERROR: regular-file hard link unexpectedly passed the config-only contract" >&2
    exit 1
fi
chmod 0755 "$POSIX_PROBE/config"
stat -c 'path=%n uid=%u gid=%g mode=%a dev=%d inode=%i nlink=%h' \
    "$POSIX_PROBE/hard-link-sentinel" "$POSIX_PROBE/config/hard-link-alias" > "${EVIDENCE_DIR}/hard-link-after.txt"
cmp "${EVIDENCE_DIR}/hard-link-before.txt" "${EVIDENCE_DIR}/hard-link-after.txt"
grep -Fq 'regular config files must not have hard links' "${EVIDENCE_DIR}/hard-link-negative.log"

docker run --rm --name "${RUN_ID}-runtime-owner-fixture" \
    --user 0:0 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c "\
        mkdir -p /probe/runtime-owner-config && \
        chown ${RUNTIME_UID}:${RUNTIME_GID} /probe/runtime-owner-config && \
        chmod 0755 /probe/runtime-owner-config"
docker run --rm --name "${RUN_ID}-runtime-owner-first" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/runtime-owner-config \
    > "${EVIDENCE_DIR}/runtime-owner-first.log" 2>&1
docker run --rm --name "${RUN_ID}-runtime-owner-second" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/runtime-owner-config \
    > "${EVIDENCE_DIR}/runtime-owner-idempotence.log" 2>&1
docker run --rm --name "${RUN_ID}-runtime-owner-stat" \
    --user 0:0 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a" /probe/runtime-owner-config' \
    > "${EVIDENCE_DIR}/runtime-owner-after.txt"
grep -Fxq "uid=${RUNTIME_UID} gid=${RUNTIME_GID} mode=700" "${EVIDENCE_DIR}/runtime-owner-after.txt"

docker run --rm --name "${RUN_ID}-wrong-owner-fixture" \
    --user 0:0 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c "\
        mkdir -p /probe/wrong-owner-config && \
        printf 'wrong-owner sentinel\\n' > /probe/wrong-owner-config/sentinel && \
        chown -R ${WRONG_OWNER_UID}:${WRONG_OWNER_GID} /probe/wrong-owner-config && \
        chmod 0777 /probe/wrong-owner-config && chmod 0640 /probe/wrong-owner-config/sentinel"
docker run --rm --name "${RUN_ID}-wrong-owner-before" \
    --user 0:0 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "path=%n uid=%u gid=%g mode=%a dev=%d inode=%i" /probe/wrong-owner-config /probe/wrong-owner-config/sentinel' \
    > "${EVIDENCE_DIR}/wrong-owner-before.txt"
if docker run --rm --name "${RUN_ID}-wrong-owner-negative" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/wrong-owner-config \
    > "${EVIDENCE_DIR}/wrong-owner-negative.log" 2>&1; then
    echo "ERROR: untrusted-owner config root unexpectedly passed admission" >&2
    exit 1
fi
docker run --rm --name "${RUN_ID}-wrong-owner-after" \
    --user 0:0 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "path=%n uid=%u gid=%g mode=%a dev=%d inode=%i" /probe/wrong-owner-config /probe/wrong-owner-config/sentinel' \
    > "${EVIDENCE_DIR}/wrong-owner-after.txt"
cmp "${EVIDENCE_DIR}/wrong-owner-before.txt" "${EVIDENCE_DIR}/wrong-owner-after.txt"
grep -Fq 'refusing before any ownership or tree mutation' "${EVIDENCE_DIR}/wrong-owner-negative.log"

docker run --rm --name "${RUN_ID}-unsafe-owner-fixture" \
    --user 0:0 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c "\
        mkdir -p /probe/unsafe-root-config /probe/unsafe-runtime-config && \
        ln -s /must-not-be-read /probe/unsafe-root-config/unsafe-child && \
        ln -s /must-not-be-read /probe/unsafe-runtime-config/unsafe-child && \
        chown -R 0:0 /probe/unsafe-root-config && \
        chown -R ${RUNTIME_UID}:${RUNTIME_GID} /probe/unsafe-runtime-config && \
        chmod 0777 /probe/unsafe-root-config /probe/unsafe-runtime-config"
for unsafe_name in unsafe-root-config unsafe-runtime-config; do
    unsafe_label="${unsafe_name%-config}"
    docker run --rm --name "${RUN_ID}-${unsafe_label}-before" \
        --user 0:0 \
        --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
        --entrypoint /bin/sh "$IMAGE" -c "stat -c 'path=%n uid=%u gid=%g mode=%a dev=%d inode=%i' /probe/${unsafe_name} /probe/${unsafe_name}/unsafe-child" \
        > "${EVIDENCE_DIR}/${unsafe_label}-before.txt"
    if docker run --rm --name "${RUN_ID}-${unsafe_label}-negative" \
        -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" \
        --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
        --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
        --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root "/probe/${unsafe_name}" \
        > "${EVIDENCE_DIR}/${unsafe_label}-negative.log" 2>&1; then
        echo "ERROR: group/other-writable ${unsafe_label} unexpectedly passed admission" >&2
        exit 1
    fi
    docker run --rm --name "${RUN_ID}-${unsafe_label}-after" \
        --user 0:0 \
        --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
        --entrypoint /bin/sh "$IMAGE" -c "stat -c 'path=%n uid=%u gid=%g mode=%a dev=%d inode=%i' /probe/${unsafe_name} /probe/${unsafe_name}/unsafe-child" \
        > "${EVIDENCE_DIR}/${unsafe_label}-after.txt"
    cmp "${EVIDENCE_DIR}/${unsafe_label}-before.txt" "${EVIDENCE_DIR}/${unsafe_label}-after.txt"
    grep -Fq 'grants group or other write access; refusing before any tree read or mutation' "${EVIDENCE_DIR}/${unsafe_label}-negative.log"
done

docker run --rm --name "${RUN_ID}-barrier-fixture" \
    --user 0:0 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c "\
        mkdir -p /probe/barrier-root-config && \
        printf 'barrier sentinel\\n' > /probe/barrier-root-config/attacker-writable-child && \
        chown 0:0 /probe/barrier-root-config && chmod 0755 /probe/barrier-root-config && \
        chown ${WRONG_OWNER_UID}:${WRONG_OWNER_GID} /probe/barrier-root-config/attacker-writable-child && \
        chmod 0666 /probe/barrier-root-config/attacker-writable-child"
docker run --rm --name "${RUN_ID}-barrier-before" \
    --user 0:0 --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a dev=%d inode=%i" /probe/barrier-root-config/attacker-writable-child; sha256sum /probe/barrier-root-config/attacker-writable-child' \
    > "${EVIDENCE_DIR}/barrier-before.txt"
docker run --rm --name "${RUN_ID}-barrier-prepare" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" -e SEEDSYNC_CONFIG_ROOT_TEST_DELAY_SECONDS=10 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/barrier-root-config \
    > "${EVIDENCE_DIR}/barrier-prepare.log" 2>&1 &
barrier_prepare_pid=$!
for attempt in $(seq 1 60); do
    [ "$(stat -c '%a' "$POSIX_PROBE/barrier-root-config")" = "0" ] && break
    sleep 0.1
done
[ "$(stat -c '%a' "$POSIX_PROBE/barrier-root-config")" = "0" ]
printf 'mode=0000\n' > "${EVIDENCE_DIR}/barrier-mode-window.txt"
if docker run --rm --name "${RUN_ID}-barrier-attacker" --user "${WRONG_OWNER_UID}:${WRONG_OWNER_GID}" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" --entrypoint /bin/sh "$IMAGE" -c 'printf hacked > /probe/barrier-root-config/attacker-writable-child' \
    > "${EVIDENCE_DIR}/barrier-attacker.log" 2>&1; then
    echo "ERROR: attacker wrote through the mode-0000 barrier" >&2
    exit 1
fi
grep -Eqi 'permission denied|operation not permitted' "${EVIDENCE_DIR}/barrier-attacker.log"
docker run --rm --name "${RUN_ID}-barrier-window-stat" --user 0:0 --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a dev=%d inode=%i" /probe/barrier-root-config/attacker-writable-child; sha256sum /probe/barrier-root-config/attacker-writable-child' \
    > "${EVIDENCE_DIR}/barrier-window.txt"
cmp "${EVIDENCE_DIR}/barrier-before.txt" "${EVIDENCE_DIR}/barrier-window.txt"
if wait "$barrier_prepare_pid"; then
    echo "ERROR: attacker-owned config descendant unexpectedly passed ownership admission" >&2
    exit 1
fi
docker run --rm --name "${RUN_ID}-barrier-after" --user 0:0 --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a dev=%d inode=%i" /probe/barrier-root-config/attacker-writable-child; sha256sum /probe/barrier-root-config/attacker-writable-child' \
    > "${EVIDENCE_DIR}/barrier-after.txt"
cmp "${EVIDENCE_DIR}/barrier-before.txt" "${EVIDENCE_DIR}/barrier-after.txt"
grep -Fq "attacker-writable-child owner UID ${WRONG_OWNER_UID} is neither trusted root, the image default UID 1000, nor the runtime UID ${RUNTIME_UID}" "${EVIDENCE_DIR}/barrier-prepare.log"

docker run --rm --name "${RUN_ID}-repair-race-fixture" \
    --user 0:0 --mount "type=bind,src=${POSIX_PROBE},dst=/probe" --entrypoint /bin/sh "$IMAGE" -c "\
        mkdir -p /probe/repair-race-config && \
        printf 'repair race sentinel\\n' > /probe/repair-race-config/runtime-writable-child && \
        chown -R 0:0 /probe/repair-race-config && \
        chmod 0755 /probe/repair-race-config && chmod 0666 /probe/repair-race-config/runtime-writable-child"
docker run --rm --name "${RUN_ID}-repair-race-before" \
    --user 0:0 --mount "type=bind,src=${POSIX_PROBE},dst=/probe" --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a dev=%d inode=%i" /probe/repair-race-config/runtime-writable-child; sha256sum /probe/repair-race-config/runtime-writable-child' \
    > "${EVIDENCE_DIR}/repair-race-before.txt"
docker run --rm --name "${RUN_ID}-repair-race-prepare" \
    -e "PUID=${RUNTIME_UID}" -e "PGID=${RUNTIME_GID}" -e SEEDSYNC_CONFIG_ROOT_REPAIR_TEST_DELAY_SECONDS=10 \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" --mount "type=bind,src=${ENTRYPOINT},dst=/scripts/entrypoint.sh,readonly" \
    --entrypoint /scripts/entrypoint.sh "$IMAGE" --prepare-config-root /probe/repair-race-config \
    > "${EVIDENCE_DIR}/repair-race-prepare.log" 2>&1 &
repair_prepare_pid=$!
for attempt in $(seq 1 60); do
    [ "$(stat -c '%u:%a' "$POSIX_PROBE/repair-race-config")" = "${RUNTIME_UID}:0" ] && break
    sleep 0.1
done
[ "$(stat -c '%u:%a' "$POSIX_PROBE/repair-race-config")" = "${RUNTIME_UID}:0" ]
printf 'owner=%s mode=0000\n' "${RUNTIME_UID}" > "${EVIDENCE_DIR}/repair-race-mode-window.txt"
if docker run --rm --name "${RUN_ID}-repair-race-attacker" --user "${RUNTIME_UID}:${RUNTIME_GID}" \
    --mount "type=bind,src=${POSIX_PROBE},dst=/probe" --entrypoint /bin/sh "$IMAGE" -c 'printf hacked > /probe/repair-race-config/runtime-writable-child' \
    > "${EVIDENCE_DIR}/repair-race-attacker.log" 2>&1; then
    echo "ERROR: runtime UID wrote through the repair-phase mode-0000 barrier" >&2
    exit 1
fi
grep -Eqi 'permission denied|operation not permitted' "${EVIDENCE_DIR}/repair-race-attacker.log"
docker run --rm --name "${RUN_ID}-repair-race-window-stat" --user 0:0 --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a dev=%d inode=%i" /probe/repair-race-config/runtime-writable-child; sha256sum /probe/repair-race-config/runtime-writable-child' \
    > "${EVIDENCE_DIR}/repair-race-window.txt"
cmp "${EVIDENCE_DIR}/repair-race-before.txt" "${EVIDENCE_DIR}/repair-race-window.txt"
wait "$repair_prepare_pid"
docker run --rm --name "${RUN_ID}-repair-race-after" --user 0:0 --mount "type=bind,src=${POSIX_PROBE},dst=/probe" \
    --entrypoint /bin/sh "$IMAGE" -c 'stat -c "uid=%u gid=%g mode=%a dev=%d inode=%i" /probe/repair-race-config/runtime-writable-child; sha256sum /probe/repair-race-config/runtime-writable-child' \
    > "${EVIDENCE_DIR}/repair-race-after.txt"
grep -Fq "uid=${RUNTIME_UID} gid=${RUNTIME_GID} mode=666" "${EVIDENCE_DIR}/repair-race-after.txt"
sed -E 's/^uid=[0-9]+ gid=[0-9]+ //' "${EVIDENCE_DIR}/repair-race-before.txt" | cmp - <(sed -E 's/^uid=[0-9]+ gid=[0-9]+ //' "${EVIDENCE_DIR}/repair-race-after.txt")

echo "entrypoint config-root contract: passed; retained evidence ${EVIDENCE_DIR}; retained volume ${POSIX_VOLUME}"
