#!/usr/bin/env bash
# Run the complete local gates against precisely the source being pushed.
set -euo pipefail
fail() { printf 'SeedSync pre-push: %s\n' "$*" >&2; exit 1; }
root=$(git rev-parse --show-toplevel) || exit 1
cd "$root"
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        # Disable MSYS argument rewriting; WSL accepts the native Windows cwd.
        MSYS_NO_PATHCONV=1 wsl.exe --cd "$(cygpath -w "$root")" --exec bash \
            ./scripts/pre-push-gate.sh
        exit $?
        ;;
    Linux*) ;;
    *) fail 'supported runtimes are Linux or Windows Git Bash with WSL' ;;
esac
head=$(git rev-parse --verify HEAD)
updates=0
while read -r local_ref local_oid remote_ref remote_oid extra; do
    [[ -n "$local_ref" && -n "$local_oid" && -n "$remote_ref" && -n "$remote_oid" && -z "${extra:-}" ]] || fail 'invalid push ref input'
    [[ "$local_oid" =~ ^[0-9a-f]+$ && ${#local_oid} -eq ${#head} ]] || fail 'invalid local object ID'
    [[ "$remote_oid" =~ ^[0-9a-f]+$ && ${#remote_oid} -eq ${#head} ]] || fail 'invalid remote object ID'
    [[ "$local_oid" =~ ^0+$ ]] && continue
    [[ "$local_oid" == "$head" ]] || fail 'every published object must be the tested HEAD (annotated tags are unsupported)'
    updates=$((updates + 1))
done
if [[ "$updates" -eq 0 ]]; then
    printf 'SeedSync pre-push: no source updates (deletions only); no suites required.\n' >&2
    exit 0
fi
assert_source() {
    [[ "$(git rev-parse --verify HEAD)" == "$head" ]] || fail 'HEAD changed during testing'
    [[ -z "$(git status --porcelain --untracked-files=all --ignore-submodules=none)" ]] || fail 'commit or remove local changes before pushing; tests must match HEAD'
    git diff --quiet HEAD -- || fail 'tracked source differs from HEAD'
}
assert_source
printf 'SeedSync pre-push: full Python and Angular suites for %s\n' "$head" >&2
make run-tests-python </dev/null
make run-tests-angular </dev/null
assert_source
printf 'SeedSync pre-push: both full suites passed for %s\n' "$head" >&2
