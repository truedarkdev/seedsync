#!/usr/bin/env bash
# Verify the exact runtime image digest and inventory its final rootfs.
set -euo pipefail

usage() {
    echo "Usage: $0 <image-reference> <sha256:digest> [platform]" >&2
}

die() {
    echo "runtime image artifact gate: $*" >&2
    exit 1
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
    usage
    exit 1
fi

image_reference="$1"
expected_digest="$2"
platform="${3:-${SEEDSYNC_PLATFORM:-}}"
docker_bin="${SEEDSYNC_DOCKER_BIN:-docker}"

[[ -n "${image_reference}" && "${image_reference}" != *[[:space:]]* ]] \
    || die "image reference must be a non-empty value without whitespace"
[[ "${expected_digest}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "digest must match sha256:<64 lowercase hexadecimal characters>"

command -v "${docker_bin}" >/dev/null 2>&1 \
    || die "Docker CLI not found: ${docker_bin}"
command -v tar >/dev/null 2>&1 \
    || die "tar is required to inspect the exported rootfs"

resolved_digest="$(
    "${docker_bin}" buildx imagetools inspect "${image_reference}" \
        --format '{{json .Manifest.Digest}}' | tr -d '\"\r\n'
)"
if [[ "${resolved_digest}" != "${expected_digest}" ]]; then
    echo "expected=${expected_digest}" >&2
    echo "resolved=${resolved_digest}" >&2
    die "staging image tag does not resolve to the supplied tested digest"
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/seedsync-runtime-inventory.XXXXXX")"
container_id=""
cleanup() {
    set +e
    if [[ -n "${container_id}" ]]; then
        "${docker_bin}" rm "${container_id}" >/dev/null 2>&1
    fi
    rm -rf -- "${workdir}"
}
trap cleanup EXIT

create_args=(create)
if [[ -n "${platform}" ]]; then
    create_args+=(--platform "${platform}")
fi
create_args+=("${image_reference}@${expected_digest}")
container_id="$("${docker_bin}" "${create_args[@]}")"
[[ -n "${container_id}" ]] || die "Docker did not return a container id"

rootfs_tar="${workdir}/rootfs.tar"
"${docker_bin}" export --output "${rootfs_tar}" "${container_id}" >/dev/null

findings="${workdir}/findings.tsv"
tar -tf "${rootfs_tar}" | awk '
function component_path(path, component,    parts, count, i, j, result) {
    count = split(path, parts, "/")
    for (i = 1; i <= count; i++) {
        if (parts[i] == component) {
            result = parts[1]
            for (j = 2; j <= i; j++) result = result "/" parts[j]
            return result
        }
    }
    return ""
}

function app_test_path(path,    parts, count, i, j, result) {
    count = split(path, parts, "/")
    if (parts[1] != "app") return ""
    for (i = 2; i <= count; i++) {
        if (parts[i] == "test" || parts[i] == "tests" || parts[i] == "__tests__") {
            result = parts[1]
            for (j = 2; j <= i; j++) result = result "/" parts[j]
            return result
        }
    }
    return ""
}

function dependency_test_path(path,    parts, count, i, j, k, result) {
    count = split(path, parts, "/")
    for (i = 1; i <= count; i++) {
        if (parts[i] == "site-packages" || parts[i] == "dist-packages") {
            for (j = i + 1; j <= count; j++) {
                if (parts[j] == "test" || parts[j] == "tests" || parts[j] == "__tests__") {
                    result = parts[1]
                    for (k = 2; k <= j; k++) result = result "/" parts[k]
                    return result
                }
            }
        }
    }
    return ""
}

function emit(reason, path,    i, existing) {
    # Directory descendants are reported through the first matching
    # directory only, keeping CI failure output useful for large packages.
    for (i = 1; i <= finding_count[reason]; i++) {
        existing = findings[reason SUBSEP i]
        if (path == existing || index(path, existing "/") == 1) return
    }
    findings[reason SUBSEP ++finding_count[reason]] = path
    print reason "\t/" path
}

{
    path = $0
    sub(/^\.\//, "", path)
    sub(/\/$/, "", path)
    if (path == "" || path == ".") next

    # The application source and installed Python dependencies must not carry
    # their test suites into the shipped runtime image.
    app_test = app_test_path(path)
    if (app_test != "") {
        emit("app-test-directory", app_test)
        next
    }
    dependency_test = dependency_test_path(path)
    if (dependency_test != "") {
        emit("dependency-test-directory", dependency_test)
        next
    }
    if (path ~ /(^|\/)\.pytest_cache(\/|$)/) {
        emit("pytest-cache", component_path(path, ".pytest_cache"))
        next
    }
    if (path ~ /^app\/python\/tmp(\/|$)/) {
        emit("app-local-temp-directory", "app/python/tmp")
        next
    }

    # Package-manager logs and indexes are build-time artifacts, not runtime
    # state. Keep the inventory strict for both files and retained directories.
    if (path ~ /^var\/log\/apt(\/|$)/) {
        emit("apt-log", "var/log/apt")
        next
    }
    if (path ~ /^var\/log\/dpkg\.log/) {
        emit("dpkg-log", "var/log/dpkg.log")
        next
    }
    if (path ~ /^var\/log\/alternatives\.log/) {
        emit("alternatives-log", "var/log/alternatives.log")
        next
    }

    if (path ~ /^tmp\/seedsync-bootstrap(\/|$)/) {
        emit("bootstrap-sidecar", "tmp/seedsync-bootstrap")
        next
    }
}' | sort -u > "${findings}"

if [[ -s "${findings}" ]]; then
    echo "runtime image artifact gate: forbidden final-rootfs entries:" >&2
    cat "${findings}" >&2
    exit 1
fi

if [[ -n "${platform}" ]]; then
    echo "runtime image artifact gate passed for digest ${expected_digest} (platform ${platform})"
else
    echo "runtime image artifact gate passed for digest ${expected_digest}"
fi
