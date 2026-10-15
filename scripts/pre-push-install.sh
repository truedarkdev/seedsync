#!/usr/bin/env bash
# Install in the effective hooks directory without changing core.hooksPath.
set -euo pipefail
root=$(git rev-parse --show-toplevel)
cd "$root"
hooks=$(git rev-parse --git-path hooks)
mkdir -p "$hooks"
hooks=$(cd "$hooks" && pwd)
hook="$hooks/pre-push"
previous="$hooks/pre-push.seedsync-previous"
template="$root/scripts/pre-push-hook.sh"
if [[ -L "$hook" ]]; then
    printf 'Refusing to replace a symbolic-link pre-push hook.\n' >&2
    exit 1
fi
if [[ -e "$hook" ]] && ! cmp -s "$hook" "$template"; then
    if grep -qx '# seedsync-maintained-pre-push-v1' "$hook"; then
        printf 'Installed SeedSync wrapper differs; inspect it before replacing it.\n' >&2
        exit 1
    fi
    if [[ -e "$previous" || -L "$previous" ]]; then
        printf 'Refusing to overwrite the preserved pre-push hook.\n' >&2
        exit 1
    fi
    # Copy first: the existing hook remains intact if preservation fails.
    cp -p "$hook" "$previous"
elif [[ ! -e "$hook" && ( -e "$previous" || -L "$previous" ) ]]; then
    printf 'Preserved pre-push exists without its wrapper; inspect before installing.\n' >&2
    exit 1
fi
cp "$template" "$hook"
chmod +x "$hook"
printf 'Installed SeedSync gate: %s\n' "$hook"
