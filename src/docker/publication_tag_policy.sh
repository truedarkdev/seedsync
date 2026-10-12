#!/usr/bin/env bash
# This allowlist does not authorize publication. Keep all checks before Docker.
set -euo pipefail

reject() { printf 'ERROR: Docker publication policy: %s\n' "$1" >&2; exit 1; }
[[ $# == 3 ]] || reject 'expected mode, registry/repository prefix, and tag'
mode=$1
prefix=$2
tag=$3
# Make passes values through the environment; never evaluate them as shell text.
[[ "$prefix" =~ ^[a-z0-9][a-z0-9./:_-]*$ && "$prefix" != */ && "$prefix" != *//* ]] \
    || reject 'invalid registry/repository prefix'
first=${prefix%%/*}
hub=false
case "${first%%:*}" in
    docker.io|index.docker.io|registry-1.docker.io|registry.hub.docker.com|hub.docker.com) hub=true ;;
    *)
        case "$first" in localhost|*.*|*:*) ;; *) hub=true ;; esac
        ;;
esac
[[ "$tag" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] || reject 'invalid image tag'
case "$mode" in
    staging)
        [[ "$hub" == false ]] || reject 'Docker Hub staging is forbidden (including cache export); use a local registry or GHCR'
        ;;
    release)
        [[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || reject 'release version must be numeric X.Y.Z without a v prefix or prerelease suffix'
        ;;
    tag) ;;
    *) reject 'unknown guard mode' ;;
esac
if [[ "$hub" == true ]]; then
    [[ "$tag" == latest || "$tag" == debugging || "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || reject 'Docker Hub permits only latest, debugging, or numeric X.Y.Z'
fi
