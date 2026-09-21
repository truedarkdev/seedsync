#!/usr/bin/env bash

# Focused contract checks for the root-only, default-off py-spy image helper.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../../.." && pwd -P)"
WRAPPER="${ROOT_DIR}/src/docker/build/docker-image/seedsync-py-spy"
DOCKER_BIN="${SEEDSYNC_DOCKER_BIN:-docker}"
IMAGE="${SEEDSYNC_PY_SPY_TEST_IMAGE:-seedsync-local:compose}"

die() {
    printf '%s\n' "py-spy wrapper contract: $*" >&2
    exit 1
}

command -v bash >/dev/null 2>&1 || die "bash is required"
bash -n "${WRAPPER}"
grep -Fq 'py_spy_enabled' "${WRAPPER}" || die "wrapper gate is missing"
grep -Fq -- '--nonblocking' "${WRAPPER}" || die "wrapper must use nonblocking py-spy modes"
if grep -F -- '--locals' "${WRAPPER}" >/dev/null; then
    die "dump mode must not collect locals"
fi

"${DOCKER_BIN}" image inspect "${IMAGE}" >/dev/null 2>&1 \
    || die "test image is unavailable: ${IMAGE}"

workdir="$(mktemp -d "${TMPDIR:-/tmp}/seedsync-py-spy-contract.XXXXXX")"
fake_container=""
cleanup() {
    if [[ -n "${fake_container}" ]]; then
        "${DOCKER_BIN}" rm -f "${fake_container}" >/dev/null 2>&1 || true
    fi
    rm -rf -- "${workdir}"
}
trap cleanup EXIT

settings="${workdir}/settings.cfg"
printf '%s\n' '[General]' 'py_spy_enabled = False' > "${settings}"
settings_mount_path="${settings}"
if [[ "${DOCKER_BIN}" == *.exe ]]; then
    settings_mount_path="$(wslpath -w "${settings}")"
fi

run_wrapper() {
    local gate_value="$1"
    shift
    printf '%s\n' '[General]' "py_spy_enabled = ${gate_value}" > "${settings}"
    "${DOCKER_BIN}" run --rm --user root \
        --mount "type=bind,src=${settings_mount_path},dst=/config/settings.cfg,readonly" \
        --entrypoint /usr/local/sbin/seedsync-py-spy "${IMAGE}" "$@"
}

if output="$(run_wrapper False dump 2>&1)"; then
    die "default-disabled wrapper unexpectedly succeeded"
fi
if [[ "${output}" != *"ERROR: py-spy request refused"* ]]; then
    die "default-disabled refusal was not generic"
fi
if [[ "${output}" == *"py_spy_enabled"* || "${output}" == *"settings.cfg"* ]]; then
    die "refusal leaked configuration details"
fi

for args in \
    'True unknown' \
    'True dump --locals' \
    'True record 31' \
    'True record 1 21' \
    'True record --duration'; do
    # shellcheck disable=SC2086
    if run_wrapper ${args} >/dev/null 2>&1; then
        die "invalid mode or bound unexpectedly succeeded: ${args}"
    fi
done
huge_duration="$(printf '9%.0s' {1..65})"
if run_wrapper True record "${huge_duration}" >/dev/null 2>&1; then
    die "oversized normalized duration unexpectedly succeeded"
fi

printf '%s\n' '[General]' 'py_spy_enabled = True' > "${settings}"
settings_before="$(sha256sum "${settings}")"
if run_wrapper True dump >/dev/null 2>&1; then
    die "zero-target dump unexpectedly succeeded"
fi
settings_after="$(sha256sum "${settings}")"
[[ "${settings_before}" == "${settings_after}" ]] \
    || die "wrapper changed the mounted application configuration"

mode_line="$("${DOCKER_BIN}" run --rm --user root --entrypoint /bin/sh "${IMAGE}" \
    -c 'stat -c "%u:%g:%a" /usr/local/sbin/seedsync-py-spy; command -v py-spy')"
[[ "${mode_line}" == *$'0:0:700'* ]] || die "wrapper is not root-only mode 0700"
[[ "${mode_line}" == *"/usr/local/bin/py-spy"* ]] || die "py-spy executable is missing"

# Exercise the real wrapper against one isolated non-root target, while a
# disposable fake py-spy records the constructed arguments.  This catches
# unsupported or missing dump flags that static text checks cannot prove.
fake_target="${workdir}/fake-seedsync.py"
fake_pyspy="${workdir}/fake-py-spy"
printf '%s\n' 'import time' 'while True:' '    time.sleep(1)' > "${fake_target}"
printf '%s\n' \
    '#!/bin/sh' \
    'printf "%s\\n" "$@" > /tmp/fake-py-spy-args' \
    'if [ "$1" = record ]; then' \
    '  output=""' \
    '  while [ "$#" -gt 0 ]; do' \
    '    if [ "$1" = --output ]; then output="$2"; shift 2; continue; fi' \
    '    shift' \
    '  done' \
    '  if [ -e /tmp/fake-py-spy-noisy ]; then dd if=/dev/zero bs=70000 count=1 status=none >&2; fi' \
    '  if [ -e /tmp/fake-py-spy-timeout ]; then exec sleep 60; fi' \
    '  if [ -e /tmp/fake-py-spy-fail ]; then printf "%s\\n" fake-failure >&2; exit 2; fi' \
    '  printf "%s\\n" fake-profile > "$output"' \
    '  printf "%s\\n" fake-stderr >&2' \
    'elif [ "$1" = dump ]; then' \
    '  if [ -e /tmp/fake-py-spy-dump-noisy ]; then yes D | head -c 2097152; fi' \
    '  if [ -e /tmp/fake-py-spy-dump-timeout ]; then printf "%s\\n" fake-dump-failure >&2; exec sleep 60; fi' \
    '  printf "%s\\n" fake-dump-output' \
    'fi' > "${fake_pyspy}"
chmod 755 "${fake_pyspy}"
fake_target_mount_path="${fake_target}"
fake_pyspy_mount_path="${fake_pyspy}"
if [[ "${DOCKER_BIN}" == *.exe ]]; then
    fake_target_mount_path="$(wslpath -w "${fake_target}")"
    fake_pyspy_mount_path="$(wslpath -w "${fake_pyspy}")"
fi
fake_container="seedsync-py-spy-contract-${RANDOM}-${RANDOM}"
"${DOCKER_BIN}" rm -f "${fake_container}" >/dev/null 2>&1 || true
"${DOCKER_BIN}" run -d --name "${fake_container}" --user root \
    --mount "type=bind,src=${settings_mount_path},dst=/config/settings.cfg,readonly" \
    --mount "type=bind,src=${fake_target_mount_path},dst=/tmp/fake-seedsync.py,readonly" \
    --mount "type=bind,src=${fake_pyspy_mount_path},dst=/usr/local/bin/py-spy,readonly" \
    --entrypoint /bin/bash "${IMAGE}" \
    -lc 'cp /tmp/fake-seedsync.py /app/python/seedsync.py && chown 1000:1000 /app/python/seedsync.py && exec setpriv --reuid=1000 --regid=1000 --clear-groups /bin/bash -c "exec -a python /usr/local/bin/python /app/python/seedsync.py & wait"' >/dev/null
target_ready=0
for _ in $(seq 1 20); do
    if "${DOCKER_BIN}" exec --user root "${fake_container}" /bin/sh -c '
        for proc in /proc/[0-9]*; do
            [ -r "$proc/cmdline" ] || continue
            command_line="$(tr "\\000" " " < "$proc/cmdline")"
            case "$command_line" in
                "python /app/python/seedsync.py"*) exit 0 ;;
            esac
        done
        exit 1
    ' >/dev/null 2>&1; then
        target_ready=1
        break
    fi
    sleep 0.1
done
[[ "${target_ready}" -eq 1 ]] || die "isolated fake target did not start"
target_pid="$("${DOCKER_BIN}" exec --user root "${fake_container}" /bin/sh -c '
    for proc in /proc/[0-9]*; do
        [ -r "$proc/cmdline" ] || continue
        command_line="$(tr "\\000" " " < "$proc/cmdline")"
        case "$command_line" in
            "python /app/python/seedsync.py"*) printf "%s\\n" "${proc##*/}"; exit 0 ;;
        esac
    done
    exit 1
')"
[[ "${target_pid}" =~ ^[0-9]+$ ]] || die "fake target pid was not resolved"

# A pre-existing path with the historical artifact-root name must not be
# followed.  The wrapper creates a unique root-owned directory directly in
# sticky /tmp instead.
"${DOCKER_BIN}" exec --user root "${fake_container}" /bin/sh -c \
    'rm -f /tmp/seedsync-py-spy && ln -s /tmp/fake-py-spy-attacker /tmp/seedsync-py-spy'
artifact_path="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    /usr/local/sbin/seedsync-py-spy record 1 1)"
[[ "${artifact_path}" == /tmp/seedsync-py-spy.*/* ]] \
    || die "record artifact escaped the generated private directory"
artifact_dir="${artifact_path%/*}"
[[ "${artifact_dir}" == /tmp/seedsync-py-spy.* ]] \
    || die "record artifact directory was not directly beneath /tmp"
artifact_dir_stat="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    stat -c '%u:%g:%a:%F' -- "${artifact_dir}")"
[[ "${artifact_dir_stat}" == '0:0:700:directory' ]] \
    || die "record artifact directory is not root-only"
artifact_stat="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    stat -c '%u:%g:%a:%s' -- "${artifact_path}")"
IFS=: read -r artifact_owner artifact_group artifact_mode artifact_bytes <<< "${artifact_stat}"
[[ "${artifact_owner}" == 0 && "${artifact_group}" == 0 && "${artifact_mode}" == 600 ]] \
    || die "record artifact owner/mode is not root-only"
[[ "${artifact_bytes}" =~ ^[0-9]+$ && "${artifact_bytes}" -le 8388608 ]] \
    || die "record artifact exceeded the output bound"
record_args="$("${DOCKER_BIN}" exec --user root "${fake_container}" cat /tmp/fake-py-spy-args)"
[[ "${record_args}" == *$'record\n'* ]] || die "record mode did not invoke py-spy record"
[[ "${record_args}" == *"--nonblocking"* ]] || die "record mode omitted --nonblocking"
[[ "${record_args}" == *"--idle"* ]] || die "record mode omitted supported --idle"
stderr_stat="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    stat -c '%u:%g:%a:%s' -- "${artifact_dir}/py-spy.stderr")"
IFS=: read -r stderr_owner stderr_group stderr_mode stderr_bytes <<< "${stderr_stat}"
[[ "${stderr_owner}" == 0 && "${stderr_group}" == 0 && "${stderr_mode}" == 600 ]] \
    || die "profiler stderr sidecar is not root-only"
[[ "${stderr_bytes}" =~ ^[0-9]+$ && "${stderr_bytes}" -le 65536 ]] \
    || die "profiler stderr sidecar exceeded its bound"
status_content="$("${DOCKER_BIN}" exec --user root "${fake_container}" cat "${artifact_dir}/status")"
[[ "${status_content}" == *$'state=completed\n'* ]] || die "successful record status was not retained"
[[ "${status_content}" == *$'stderr_truncated=0\n'* ]] || die "stderr loss status was incorrect"
[[ "${status_content}" == *'output_cap_bytes=8388608'* ]] || die "output bound was not retained"
status_stat="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    stat -c '%u:%g:%a:%s' -- "${artifact_dir}/status")"
IFS=: read -r status_owner status_group status_mode status_bytes <<< "${status_stat}"
[[ "${status_owner}" == 0 && "${status_group}" == 0 && "${status_mode}" == 600 ]] \
    || die "record status sidecar is not root-only"
[[ "${status_bytes}" =~ ^[0-9]+$ && "${status_bytes}" -le 1024 ]] \
    || die "record status sidecar exceeded its bound"
"${DOCKER_BIN}" exec --user root "${fake_container}" test -L /tmp/seedsync-py-spy \
    || die "pre-existing artifact-root symlink was unexpectedly replaced"

# A noisy profiler must retain only the bounded private sample and mark the
# discarded remainder without exposing raw stderr to the caller.
"${DOCKER_BIN}" exec --user root "${fake_container}" touch /tmp/fake-py-spy-noisy
noisy_output="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    /usr/local/sbin/seedsync-py-spy record 1 1)"
noisy_dir="${noisy_output%/*}"
noisy_status="$("${DOCKER_BIN}" exec --user root "${fake_container}" cat "${noisy_dir}/status")"
[[ "${noisy_status}" == *$'state=completed\n'* && "${noisy_status}" == *$'stderr_truncated=1\n'* ]] \
    || die "noisy profiler stderr was not recorded as truncated"
noisy_stderr_stat="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    stat -c '%u:%g:%a:%s' -- "${noisy_dir}/py-spy.stderr")"
IFS=: read -r noisy_owner noisy_group noisy_mode noisy_bytes <<< "${noisy_stderr_stat}"
[[ "${noisy_owner}" == 0 && "${noisy_group}" == 0 && "${noisy_mode}" == 600 ]] \
    || die "noisy profiler stderr sidecar is not root-only"
[[ "${noisy_bytes}" =~ ^[0-9]+$ && "${noisy_bytes}" -le 65536 ]] \
    || die "noisy profiler stderr sidecar exceeded its bound"
"${DOCKER_BIN}" exec --user root "${fake_container}" rm -f /tmp/fake-py-spy-noisy

# Dump stdout and stderr must remain bounded instead of silently flowing to
# the console or being discarded by an unbounded pipe.
"${DOCKER_BIN}" exec --user root "${fake_container}" touch /tmp/fake-py-spy-dump-noisy
if dump_output="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    /usr/local/sbin/seedsync-py-spy dump 2>&1)"; then
    die "oversized dump unexpectedly succeeded"
fi
[[ "${dump_output}" == *"ERROR: py-spy request refused"* \
    && "${dump_output}" != *$'D\n'* ]] \
    || die "oversized dump was not failed closed without raw console output"
dump_error_dir="$("${DOCKER_BIN}" exec --user root "${fake_container}" /bin/sh -c '
    for dir in /tmp/seedsync-py-spy.*; do
        [ -f "$dir/status" ] || continue
        grep -Fxq state=failed "$dir/status" || continue
        [ -f "$dir/dump.txt" ] || continue
        printf "%s\\n" "$dir"
    done
')"
[[ -n "${dump_error_dir}" ]] || die "oversized dump status was not retained"
dump_error_status="$("${DOCKER_BIN}" exec --user root "${fake_container}" cat "${dump_error_dir}/status")"
[[ "${dump_error_status}" == *$'state=failed\n'* ]] \
    || die "oversized dump status did not identify failure"
dump_error_stat="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    stat -c '%u:%g:%a:%s' -- "${dump_error_dir}/dump.txt")"
IFS=: read -r dump_owner dump_group dump_mode dump_bytes <<< "${dump_error_stat}"
[[ "${dump_owner}" == 0 && "${dump_group}" == 0 && "${dump_mode}" == 600 ]] \
    || die "dump output capture is not root-only"
[[ "${dump_bytes}" =~ ^[0-9]+$ && "${dump_bytes}" -le 1048576 ]] \
    || die "dump output capture exceeded its bound"
"${DOCKER_BIN}" exec --user root "${fake_container}" rm -f /tmp/fake-py-spy-dump-noisy

# Dump must also have an external timeout, and that timeout must not terminate
# the Python application being profiled.
"${DOCKER_BIN}" exec --user root "${fake_container}" touch /tmp/fake-py-spy-dump-timeout
dump_started="${SECONDS}"
if dump_output="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    /usr/local/sbin/seedsync-py-spy dump 2>&1)"; then
    die "timed-out dump unexpectedly succeeded"
fi
dump_elapsed=$((SECONDS - dump_started))
(( dump_elapsed <= 10 )) || die "dump timeout was not externally bounded"
[[ "${dump_output}" == *"ERROR: py-spy request refused"* \
    && "${dump_output}" != *"fake-dump-failure"* ]] \
    || die "dump timeout leaked raw profiler stderr"
"${DOCKER_BIN}" exec --user root "${fake_container}" /bin/bash -c \
    'kill -0 "$1"' bash "${target_pid}" \
    || die "dump timeout terminated the Python target"
"${DOCKER_BIN}" exec --user root "${fake_container}" rm -f /tmp/fake-py-spy-dump-timeout

# A timed-out or failed profiler must be bounded and leave only private,
# bounded evidence; no partial profile may be reported as usable output.
"${DOCKER_BIN}" exec --user root "${fake_container}" touch /tmp/fake-py-spy-timeout
if "${DOCKER_BIN}" exec --user root "${fake_container}" \
    /usr/local/sbin/seedsync-py-spy record 1 1 >/dev/null 2>&1; then
    die "timed-out record unexpectedly succeeded"
fi
timeout_dir="$("${DOCKER_BIN}" exec --user root "${fake_container}" /bin/sh -c '
    for dir in /tmp/seedsync-py-spy.*; do
        [ -f "$dir/status" ] || continue
        [ ! -f "$dir/dump.txt" ] || continue
        grep -Fxq state=timeout "$dir/status" || continue
        printf "%s\\n" "$dir"
    done
')"
[[ -n "${timeout_dir}" ]] || die "timed-out record status was not retained"
timeout_status="$("${DOCKER_BIN}" exec --user root "${fake_container}" cat "${timeout_dir}/status")"
[[ "${timeout_status}" == *$'state=timeout\n'* && "${timeout_status}" == *$'timeout=1\n'* ]] \
    || die "timeout status did not identify the bounded timeout"
if "${DOCKER_BIN}" exec --user root "${fake_container}" test -e "${timeout_dir}/profile.json"; then
    die "timed-out partial profile was not cleaned up"
fi
timeout_stderr_stat="$("${DOCKER_BIN}" exec --user root "${fake_container}" \
    stat -c '%u:%g:%a:%s' -- "${timeout_dir}/py-spy.stderr")"
IFS=: read -r timeout_stderr_owner timeout_stderr_group timeout_stderr_mode timeout_stderr_bytes <<< "${timeout_stderr_stat}"
[[ "${timeout_stderr_owner}" == 0 && "${timeout_stderr_group}" == 0 && "${timeout_stderr_mode}" == 600 ]] \
    || die "timeout stderr sidecar is not root-only"
[[ "${timeout_stderr_bytes}" =~ ^[0-9]+$ && "${timeout_stderr_bytes}" -le 65536 ]] \
    || die "timeout stderr sidecar exceeded its bound"
"${DOCKER_BIN}" exec --user root "${fake_container}" rm -f /tmp/fake-py-spy-timeout
"${DOCKER_BIN}" exec --user root "${fake_container}" touch /tmp/fake-py-spy-fail
if "${DOCKER_BIN}" exec --user root "${fake_container}" \
    /usr/local/sbin/seedsync-py-spy record 1 1 >/dev/null 2>&1; then
    die "failed record unexpectedly succeeded"
fi
failure_dir="$("${DOCKER_BIN}" exec --user root "${fake_container}" /bin/sh -c '
    for dir in /tmp/seedsync-py-spy.*; do
        [ -f "$dir/status" ] || continue
        [ ! -f "$dir/dump.txt" ] || continue
        grep -Fxq state=failed "$dir/status" || continue
        printf "%s\\n" "$dir"
    done
')"
[[ -n "${failure_dir}" ]] || die "failed record status was not retained"
if "${DOCKER_BIN}" exec --user root "${fake_container}" test -e "${failure_dir}/profile.json"; then
    die "failed partial profile was not cleaned up"
fi
"${DOCKER_BIN}" exec --user root "${fake_container}" rm -f /tmp/fake-py-spy-fail

"${DOCKER_BIN}" exec --user root "${fake_container}" \
    /usr/local/sbin/seedsync-py-spy dump >/dev/null
dump_args="$("${DOCKER_BIN}" exec --user root "${fake_container}" cat /tmp/fake-py-spy-args)"
[[ "${dump_args}" == *$'dump\n'* ]] || die "dump mode did not invoke py-spy dump"
[[ "${dump_args}" == *$'--pid\n'* ]] || die "dump mode omitted the fixed target pid"
[[ "${dump_args}" == *"--nonblocking"* ]] || die "dump mode omitted --nonblocking"
[[ "${dump_args}" != *$'--idle\n'* ]] || die "dump mode passed unsupported --idle"
[[ "${dump_args}" != *$'--locals\n'* ]] || die "dump mode passed --locals"

printf '%s\n' 'py-spy wrapper contract passed'
