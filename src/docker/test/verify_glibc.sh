#!/bin/bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <deb-or-binary-path> [max-glibc-version]" >&2
  exit 1
fi

artifact_path="$1"
max_glibc_version="${2:-2.31}"

if [[ ! -f "${artifact_path}" ]]; then
  echo "Artifact not found: ${artifact_path}" >&2
  exit 1
fi

if ! command -v readelf >/dev/null 2>&1; then
  echo "readelf is required to inspect ${artifact_path}" >&2
  exit 1
fi

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

if [[ "${artifact_path}" == *.deb ]]; then
  if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb is required to inspect ${artifact_path}" >&2
    exit 1
  fi

  dpkg-deb -x "${artifact_path}" "${workdir}/pkg"
  mapfile -t binaries < <(find "${workdir}/pkg" -type f \( -name 'seedsync' -o -name 'scanfs' \) | sort)
  if [[ ${#binaries[@]} -eq 0 ]]; then
    echo "No seedsync/scanfs binaries found in ${artifact_path}" >&2
    exit 1
  fi
else
  binaries=("${artifact_path}")
fi

max_seen=""
for binary in "${binaries[@]}"; do
  if [[ "${artifact_path}" == *.deb ]]; then
    display_path="${binary#${workdir}/pkg/}"
  else
    display_path="${binary}"
  fi

  echo "Inspecting ${display_path}"
  current_versions="$(readelf --version-info "${binary}" 2>/dev/null | grep -o 'GLIBC_[0-9.]*' | sed 's/^GLIBC_//' | sort -Vu || true)"
  if [[ -z "${current_versions}" ]]; then
    echo "  No GLIBC symbol versions referenced"
    continue
  fi

  current_max="$(printf '%s\n' "${current_versions}" | tail -n 1)"
  echo "  Highest required GLIBC symbol: ${current_max}"

  if [[ -z "${max_seen}" ]] || [[ "$(printf '%s\n%s\n' "${max_seen}" "${current_max}" | sort -V | tail -n 1)" == "${current_max}" ]]; then
    max_seen="${current_max}"
  fi
done

if [[ -z "${max_seen}" ]]; then
  echo "No GLIBC-linked binaries required version checks in ${artifact_path}"
  exit 0
fi

if [[ "$(printf '%s\n%s\n' "${max_glibc_version}" "${max_seen}" | sort -V | tail -n 1)" != "${max_glibc_version}" ]]; then
  echo "GLIBC verification failed: ${artifact_path} requires ${max_seen}, above allowed ${max_glibc_version}" >&2
  exit 1
fi

echo "GLIBC verification passed: ${artifact_path} stays within ${max_glibc_version}"
