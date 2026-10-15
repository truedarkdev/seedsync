#!/usr/bin/env bash
# seedsync-maintained-pre-push-v1
set -euo pipefail
refs=$(mktemp)
trap 'rm -- "$refs"' EXIT
cat > "$refs"
previous="$(dirname "$0")/pre-push.seedsync-previous"
if [[ -x "$previous" ]]; then
    "$previous" "$@" < "$refs"
fi
root=$(git rev-parse --show-toplevel)
bash "$root/scripts/pre-push-gate.sh" < "$refs"
