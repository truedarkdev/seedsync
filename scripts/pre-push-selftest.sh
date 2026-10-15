#!/usr/bin/env bash
# Cheap hook plumbing tests only; fixture make targets never run product suites.
set -euo pipefail
source_root=$(git rev-parse --show-toplevel)
mkdir -p "$source_root/tmp"
fixture=$(mktemp -d "$source_root/tmp/pre-push-selftest.XXXXXX")
printf 'Retained local fixture: %s\n' "$fixture"
git init --bare -q "$fixture/remote.git"
git init -q "$fixture/work"
cd "$fixture/work"
git config user.name 'Hook fixture'
git config user.email 'hook@example.invalid'
git config core.autocrlf false
mkdir scripts
cp "$source_root"/scripts/pre-push-{gate,hook,install}.sh scripts/
cat > Makefile <<'EOF'
.PHONY: run-tests-python run-tests-angular
run-tests-python run-tests-angular:
	@printf '%s\n' '$@' >> .git/gates.log
	@test ! -e .git/fail-$@
	@if test -e .git/dirty-during-test; then echo changed >> source; fi
EOF
printf 'original\n' > source
git add scripts Makefile source
git -c core.hooksPath=/dev/null commit -qm 'fixture baseline'
old=$(git rev-parse HEAD)
printf 'second\n' >> source
git add source
git -c core.hooksPath=/dev/null commit -qm 'fixture HEAD'
head=$(git rev-parse HEAD)
git branch older "$old"
git remote add fixture "$fixture/remote.git"
# Exercise an effective custom hooks directory and replay of the exact refs.
mkdir custom-hooks
printf '/custom-hooks/\n' > .git/info/exclude
git config core.hooksPath custom-hooks
cat > custom-hooks/pre-push <<'EOF'
#!/usr/bin/env bash
cat >> .git/custom-refs.log
EOF
chmod +x custom-hooks/pre-push
original=$(git hash-object custom-hooks/pre-push)
bash scripts/pre-push-install.sh
[[ "$(git hash-object custom-hooks/pre-push.seedsync-previous)" == "$original" ]]
[[ "$(git config core.hooksPath)" == custom-hooks ]]
bash scripts/pre-push-install.sh
expect_reject() {
    if git push fixture "$@" >> "$fixture/push.log" 2>&1; then
        printf 'Unexpected push success: %s\n' "$*" >&2; exit 1
    fi
}
git push fixture HEAD:refs/heads/pass >> "$fixture/push.log" 2>&1
[[ "$(cat .git/gates.log)" == $'run-tests-python\nrun-tests-angular' ]]
grep -q "$head refs/heads/pass" .git/custom-refs.log
printf 'dirty\n' >> source
expect_reject HEAD:refs/heads/dirty
git show HEAD:source > source
printf 'untracked\n' > unexpected
expect_reject HEAD:refs/heads/untracked
mv unexpected .git/unexpected
expect_reject older:refs/heads/older
touch .git/fail-run-tests-python
expect_reject HEAD:refs/heads/python-failure
mv .git/fail-run-tests-python .git/python-failure-tested
touch .git/fail-run-tests-angular
expect_reject HEAD:refs/heads/angular-failure
mv .git/fail-run-tests-angular .git/angular-failure-tested
touch .git/dirty-during-test
expect_reject HEAD:refs/heads/changed-during-test
mv .git/dirty-during-test .git/dirty-during-test-tested
git show HEAD:source > source
before=$(wc -l < .git/gates.log)
git push fixture :refs/heads/pass >> "$fixture/push.log" 2>&1
[[ "$(wc -l < .git/gates.log)" -eq "$before" ]]
[[ -z "$(git --git-dir="$fixture/remote.git" for-each-ref --format='%(refname)')" ]]
printf 'PASS: actual local pushes: full success, Python/Angular failure, dirty/untracked, mismatched ref, source changed during tests, deletion; custom hook preserved.\n'
# From WSL, additionally invoke Windows Git so its real Git Bash hook dispatches
# into WSL. The fixture Makefile still supplies only cheap stub targets.
windows_git='/mnt/c/Program Files/Git/cmd/git.exe'
if [[ -x "$windows_git" ]] && command -v wslpath >/dev/null; then
    "$windows_git" -C "$(wslpath -w "$PWD")" push "$(wslpath -w "$fixture/remote.git")" HEAD:refs/heads/windows >> "$fixture/windows-push.log" 2>&1
    touch .git/fail-run-tests-python
    if "$windows_git" -C "$(wslpath -w "$PWD")" push "$(wslpath -w "$fixture/remote.git")" HEAD:refs/heads/windows-failure >> "$fixture/windows-push.log" 2>&1; then
        printf 'Windows dispatcher lost the suite failure.\n' >&2; exit 1
    fi
    mv .git/fail-run-tests-python .git/windows-failure-tested
    [[ "$(git --git-dir="$fixture/remote.git" for-each-ref --format='%(refname)')" == refs/heads/windows ]]
    printf 'PASS: Windows Git -> Git Bash -> WSL actual local push and failure propagation.\n'
else
    printf 'NOT EXERCISED: Windows Git dispatch (requires WSL with Windows Git).\n'
fi
