# Integration Tracker

This document is the live ledger for integration work across `thejuran`, `rapidcopy`, and future forks.

Use this file to track:
- what has been reviewed
- what has been integrated
- what is still pending
- what is already covered elsewhere
- what was skipped
- what new upstream work appeared after the last pass

This file is for moving state. The durable rules live in `AGENTS.md`.

## How To Use This Tracker

- Keep one section per integration subject.
- For each subject, keep one subsection per active source fork.
- Prefer recording a specific last reviewed upstream commit as the main resume marker.
- Add a reviewed range when the pass covered a bounded slice rather than a clean tip.
- Record candidates as you find them instead of waiting until the end.
- Record the resulting local commit for integrated items when available.
- Use `covered elsewhere` when the issue is already solved by another integrated change, by current base behavior, or by a cleaner local solution.
- Normal pending integration work stays here.
- Unresolved maintainer questions should also be recorded in `Pending Maintainer Decisions` in `AGENTS.md`.
- Keep this file summary-level. If a subject gets dense, move detailed notes into `doc/integration-notes/S<subject-number>-<fork>.md` and link to them here.
- For Subject 21 and future user-facing conflict passes, record each meaningful candidate or conflict with its value classification, faithfulness grade against original SeedSync, scope, gateability, chosen action (`default`, `optional`, `adapt`, or `reject`), and whether maintainer input was needed.

## Entry Template

Use this structure for each fork subsection:

```md
### <fork-name>

- State: not started | in progress | reviewed | needs refresh
- High-risk: yes | no
- Integration base: <branch> @ <commit>
- Source branch: <branch>
- Fork tip seen at pass start: <commit>
- Reviewed in this pass: <range or n/a>
- Last reviewed upstream commit (inclusive): <commit>
- Resume from next: <commit or range>
- New upstream since last pass: <range, commits, or none>
- Pass date: <YYYY-MM-DD or n/a>

Integrated:
- <upstream commit or batch> -> <local commit if available> : <short note>

Pending:
- <upstream commit or batch> : <short note>

Covered elsewhere:
- <upstream commit or batch> : <short note>

Skipped:
- <upstream commit or batch> : <short reason if important>

Maintainer decisions:
- <link or short note, or none>

Verification:
- tests run: <short note>
- manual checks: <short note>
- status: verified | partially verified | not verified yet

Notes:
- <anything important for resuming later, including cross-subject references>
```

For Subject 21 and similar user-facing conflict subjects, add a short evaluation block under `Notes` or in linked subject notes for each meaningful candidate:

```md
- Candidate: <short name>
  - Value: <classification>
  - Faithfulness: <A|B|C|D|F>
  - Scope: <local|sectional|global>
  - Gateability: <cleanly isolatable|somewhat messy|not cleanly isolatable>
  - Action: <default|optional|adapt|reject>
  - Maintainer input: yes | no
  - Rationale: <short note>
```

After individual Subject 21 evaluations, record a brief cumulative default-drift review stating whether the default experience still feels recognizably like original SeedSync and whether any changes were demoted from default to optional or rejected because of drift.

`Resume from next` is the next starting point for review work after the recorded pass. It should normally be the first unreviewed commit after the inclusive `Last reviewed upstream commit`.

`New upstream since last pass` should normally stay `n/a` or `none` until a later refresh pass happens after fetching remotes.

## Post-Integration Audit Ledger

The post-integration audit uses a separate tracked ledger from the normal subject sections above.

Audit document roles:
- `AGENTS.md` is the canonical rulebook for the audit workflow, escalation thresholds, reviewer gates, row schema expectations, and exit criteria
- `doc/post-integration-audit-rules.md` holds the audit workflow and rulebook
- `doc/post-integration-audit-active.md` is the active per-commit audit ledger for unfinished rows
- `doc/post-integration-audit.md` is the audit landing page and archive index
- `doc/integration-tracker.md` records reopened subjects, resulting local integration work, and summary state after the audit finds a real gap

Audit workflow:
- work one fork at a time
- while `rapidcopy` still has unfinished audit rows, resume `rapidcopy` first and do not switch to `thejuran` or another fork unless the maintainer explicitly changes the order
- once a maintainer-approved audit batch has started, do not stop before the full batch is complete unless a real reviewer-worthy or maintainer-worthy exception blocks progress
- inventory every fork-local upstream commit into the audit ledger before making dispositions
- process commits oldest to newest
- within the active fork, process first-pass triage in groups of the 3 oldest remaining commits, then continue with the next 3 oldest remaining commits until the batch is complete
- after each finished 3-commit triage group, immediately write those completed rows into `doc/post-integration-audit-active.md` before starting the next 3 commits; do not rely on memory to carry finished dispositions across groups
- after every 9 completed commits in a batch, treat that as a continue-check only: update your mental remaining count and keep going immediately unless the batch is actually complete
- keep a per-commit disposition even when several commits later map to one follow-up task
- do not implement missed work during the audit by default; convert it into a specific follow-up integration task or explicit subject reopen
- after each audit run, update the workflow prompt/templates if the run exposed a repeatable lesson or failure mode; record durable prompt-shape learnings in the tracked audit docs before continuing
- after each audit run, explicitly evaluate whether `explorer-fast` showed good judgment on that commit or cluster of commits; note whether it was appropriately calibrated, over-escalating to `reviewer`, under-escalating obvious risks, or misclassifying likely dispositions
- once an autonomous audit wave has started, do not pause at a tidy checkpoint or mini-summary just because a batch finished; continue through the planned wave until it is complete or a real reviewer-worthy or maintainer-worthy exception appears
- batch boundary rule: finish the current planned batch autonomously, then stop and produce a full batch report before starting the next batch
- the batch report should include: commits processed, disposition summary, reviewer count, any maintainer-relevant exceptions, and whether workflow/prompt improvements are needed
- after each finished batch, wait for explicit maintainer confirmation of the next batch and its size before continuing
- persist the last maintainer-approved audit batch size in the audit ledger and inherit that same size on the next resume unless the maintainer explicitly changes it
- when committing audit-only ledger or tracker state, use an `audit` label such as `docs(audit): ...` instead of reusing a completed `subjectNN` label

Per-commit subagent workflow:
- first use `explorer-fast` triage for each commit, or `explorer` if `explorer-fast` is unavailable
- submit first-pass triage work oldest-first, with at most the 3 oldest remaining commits in flight at once unless the maintainer explicitly changes that limit
- require triage output to include: likely subject, triage outcome, confidence, evidence type, and whether `reviewer` is needed
- when the evidence type is `direct local match`, require the explorer to cite the matching local commit hash explicitly in the rationale instead of only referring to nearby parity or surrounding work
- for docs-only commits that are not backed by an exact local commit match, require the agent to cite the exact current local command, sentence, or section that covers the upstream intent; if it cannot do that concretely, escalate to `reviewer`
- confidence values: `high`, `medium`, `low`
- evidence types: `direct local match`, `tracker match`, `behavioral inference`, `unclear`
- the orchestrator should judge the triage quality, not just consume it; compare the explorer result against local evidence and any later reviewer outcome, and tighten or relax the prompt if the explorer is drifting toward blanket escalation or unwarranted confidence
- when repeated `explorer-fast` runs on a low-risk audit stretch keep producing high-confidence `direct local match` results with explicit local commit hashes and those matches hold up under spot checks, the orchestrator may switch to light-touch confirmation instead of re-deriving every match manually
- when the maintainer asks for a low-context audit mode, the orchestrator should depend more on subagent evidence, do less manual reconstruction, and escalate suspicious or weakly supported cases to `reviewer` instead of investigating them deeply by hand
- the orchestrator may close a commit directly only when the triage outcome is `already integrated likely`, `covered elsewhere likely`, or `likely intentional skip`, confidence is `high`, and the evidence is concrete
- otherwise escalate that single commit to `reviewer`
- `reviewer` checks whether the commit is truly already covered, only partially covered, intentionally skipped, or should become a follow-up task
- keep reviewer prompts narrower than explorer prompts: give the reviewer one upstream commit, the triage result, the concrete local evidence already found, and a small fixed output schema instead of asking it to rediscover broad repo context from scratch
- for `covered elsewhere` closures, require `reviewer` whenever the evidence is only `behavioral inference` or the mapped integration subject is high-risk

Fork-audit completion rule:
- do not mark a fork audit `reviewed` until every commit in the recorded audit range exists in the ledger, every row has a final disposition, every unresolved row links to follow-up work, any row marked `partial` explains what is already present and what follow-up remains, and a short delta check confirms whether new upstream commits appeared after the recorded fork tip at audit start

Recommended audit ledger fields:

```md
## <fork-name>

- Audit base: <local branch @ commit>
- Source branch: <fork branch>
- Fork tip at audit start: <commit>
- Inventory status: complete | partial
- Audit state: not started | in progress | reviewed
- Pass date: <YYYY-MM-DD>

| Commit | Upstream commit subject | Mapped integration subject | Triage outcome | Confidence | Evidence | Reviewer needed | Coverage | Final disposition | Follow-up / proof |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| <hash> | <upstream subject> | <subject / milestone / unknown> | <already integrated likely / covered elsewhere likely / likely intentional skip / possible gap / unclear> | <high / medium / low> | <direct local match / tracker match / behavioral inference / unclear> | <yes / no> | <full / partial / none> | <already integrated / covered elsewhere / intentionally skipped / needs subject reopen / needs new integration task / maintainer decision needed> | <note or task link> |
```

## Post-Integration Audit Follow-Up Summary

Post-audit consolidation task:
- after the full post-integration audit closes, build a deduplicated reopen matrix before implementation work begins
- group reopen rows by workstream, owning subject, affected files, and shared upstream commit cluster
- use that matrix to drive implementation ordering and to map each finished follow-up back to every audit row it closes

- Pass date: `2026-03-11`
- Active fork: `rapidcopy`
- Batch size completed this pass: `15`

Reopened subjects:
- Subjects `8`, `10`, and `11`: reopen together for the deferred path-pair settings/API/config cluster surfaced by `d1436386`, `a33981b5`, `58ead058`, and `88ffbd00`, including path-pair CRUD/UI, path-pair-aware config validation, and Angular config-schema/null-handling fixes.
- Subjects `10` and `11`: reopen for the missing path-pair dashboard/stats cluster from `778d1d8c` plus its dependent tests in `64afa027` and `bc8348c8`.
- Subject `20`: reopen for `c300b72f`, because the current staging-path integration still leaves `DELETE_LOCAL` pointed at the final local path instead of falling back to the staging path when the file has not been moved yet.

New integration tasks:
- Validation/download-integrity feature chain: `227b5a34` (download validation API/model/UI) remains absent and should be reviewed as a dedicated backend-heavy follow-up rather than folded into the audit.
- Docker cache-ignore hygiene: `50502647` adds `.ruff_cache` and `.mypy_cache` exclusions for Docker build contexts; current `master` uses Dockerfile-specific ignore files instead of a root `.dockerignore`, but those files still do not exclude the cache directories, so handle this as a small Subject 4 packaging follow-up.
- File progress percentage edge cases: `14adf8b9` fixes rounded percentages and the `0%`-when-both-sizes-are-zero case in `view-file.service`; current `master` still uses `Math.trunc` and still shows `100%` when `remoteSize` is unknown, so handle this as a small Subject 12 files-UI follow-up without importing the accompanying modernization-plan bookkeeping.
- Logs UI text search: `2054b149` mixes already-present on-disk log persistence with a still-missing logs-page search/filter surface; handle the missing user-facing search half as a small Subject 9 follow-up rather than reopening backend logging.
- Multi-path user docs refresh: `5d2edbe4` exposed that current docs still do not describe the shipped path-pair workflow and files-list source labels; handle as a small docs-only follow-up without importing dark-mode material.
- Network mounts and `/mounts` Docker-path policy: `0b49f975` and `58c588b7` remain unintegrated and should be revisited as one packaging/config/runtime task rather than split apart.
- Runtime SSH directory ignore hardening: `de8b602b` shows that current `.gitignore` still lacks an explicit `ssh/` entry even though key filenames are ignored; handle this as a small Subject 4 packaging/hygiene follow-up.
- LFTP parser test SyntaxWarning cleanup: `58af9ee8` is still missing locally, and `python3 -m py_compile src/python/tests/unittests/test_lftp/test_job_status_parser.py` still emits invalid-escape warnings that should be handled as a small Subject 14 follow-up instead of being buried inside broader dependency churn.

Intentional audit closures worth remembering:
- RapidCopy identity changes (`08d714e6`, `ebe416f8`) remain rejected under the existing SeedSync-default branding rule.
- Theme-system and Playwright-replatforming commits in this batch (`fb4e7db4`, `5c02e93b`, `696866cd`, `5df693d7`, related QA docs) stay intentionally deferred rather than treated as missed conservative integrations.
- Self-update via external update server (`936ae4b2`) stays intentionally skipped as a broader product/workflow feature, not an audit miss.

Audit workflow learnings:
- Explorer-first triage should check whether a commit only touches a stack already marked as intentionally deferred or already assigned to an open follow-up task before proposing a new task; during this batch, derivative Playwright and validation commits initially over-escalated until they were reconciled against the existing deferral/follow-up ledger.
- Reviewer subagents should stay read-only during audit waves; have reviewers return evidence only, keep ledger edits in the main thread, and use `worker` only for intentional implementation tasks.
- Recount the exact row span before archiving and summary updates; this batch rolled 30 rapidcopy rows into one wave, so the archive chunk and status totals needed a post-archive count check instead of assuming the requested wave size.

Rapidcopy audit status:
- `rapidcopy` is now fully closed through frozen tip `c300b72f808772b00cc977ccceaa23f3c373ce33`; the next audit fork is `thejuran` once the maintainer confirms the next batch.

Thejuran audit status:
- Pass date: `2026-03-11`
- Batch size completed this pass: `27`
- Commits processed: `bdcc28746933ce5b41c6789e2104c3977780caa8` through `c94d626afe857f6072ba6eb9a7fc891d993b5485`
- Disposition summary: `5` already integrated, `3` covered elsewhere, `1` intentionally skipped, `18` need subject reopen, `0` need new integration task, `0` maintainer decisions
- Reviewer count: `5`
- Reopened subjects: Subjects `3`, `4`, `5`, and `14`, concentrated around the deferred Python 3.11 / Node 20 modernization chain, self-contained Docker packaging path, and GLIBC compatibility follow-ups
- Workflow learnings: keep merge-commit triage anchored to the effective file scope before inheriting every parent concern, and treat structurally different local packaging paths as reviewer-worthy whenever the upstream fix is compatibility-sensitive

Thejuran audit status:
- Pass date: `2026-03-11`
- Batch size completed this pass: `27`
- Commits processed: `04b1f81ec0ec565cc8d773d57e9f4c05bfa68d64` through `28a8b22351762d5785e7157ae4a57df09239b16f`
- Disposition summary: `3` already integrated, `5` covered elsewhere, `0` intentionally skipped, `19` need subject reopen, `0` need new integration task, `0` maintainer decisions
- Reviewer count: `19`
- Reopened subjects: Subjects `2`, `3`, `4`, `5`, `11`, and `13`, centered on the deferred Python 3.11 / Node 20 Angular test-tooling chain, the PyInstaller GLIBC mitigation path, and a remaining partial WebApp/OpenSSH test-hardening gap
- Workflow learnings: when a merge commit mixes one already-integrated fix with one still-missing branch, keep the final row partial and reopen the owning subjects instead of inheriting only the “covered elsewhere” parent; for test-only commits, compare both unit and integration suites before closing a row as fully covered

Thejuran audit status:
- Pass date: `2026-03-11`
- Batch size completed this pass: `27`
- Commits processed: `84f64738a1589f4939afb022cd1b456d7063d692` through `e5416c59858c986bb2b81ae6035e337fbf95b78c`
- Disposition summary: `1` already integrated, `9` covered elsewhere, `4` intentionally skipped, `13` need subject reopen, `0` need new integration task, `0` maintainer decisions
- Reviewer count: `2`
- Reopened subjects: Subjects `2`, `3`, `4`, `5`, `11`, `13`, and `17`, concentrated around extraction/webtest reliability skips, the deferred Node 20 / libsass / build-tooling chain, and the still-missing Docker systemd/cgroups compatibility fixes for stage/deb E2E coverage
- Workflow learnings: when upstream temporarily adds then later removes a workaround, close the temporary step against the later rollback instead of inflating reopen counts; when local packaging covers the same dependency need through a different Dockerfile topology, use reviewer confirmation before calling the row fully covered elsewhere

Thejuran audit status:
- Pass date: `2026-03-11`
- Batch size completed this pass: `27`
- Commits processed: `7e7289e4ee2ab15364b82c01840e6553987dcc03` through `c73205b19c707ac74da18513a0e56ec0d5fcbebb`
- Disposition summary: `4` already integrated, `3` covered elsewhere, `7` intentionally skipped, `13` need subject reopen, `0` need new integration task, `0` maintainer decisions
- Reviewer count: `1`
- Reopened subjects: Subjects `2`, `4`, `5`, `11`, `14`, and `18`, still concentrated around the stage/deb systemd-cgroups packaging path, the GLIBC/ARM verification lane, and a remaining WebApp Bottle compatibility gap
- Workflow learnings: when a broad upstream debug session interleaves real fixes with temporary diagnostics, archive the debug-only commits as intentional skips instead of reopening noise; when an upstream workaround is later rolled back, close the transient step against the rollback even if the surrounding workstream still needs reopening

## Subject 1 - Documentation And Maintainer Notes

### thejuran

- State: reviewed
- High-risk: no
- Integration base: master @ 38455d4f7fbb902a781f59d2cc93d3824ae231e9
- Source branch: thejuran/master
- Fork tip seen at pass start: a8561cdc318460de32de082e3cf33f6b6a0093cb
- Reviewed in this pass: origin/master..thejuran/master (Subject 1 filtered)
- Last reviewed upstream commit (inclusive): a8561cdc318460de32de082e3cf33f6b6a0093cb
- Resume from next: none at current tip
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- adapted from `8fbf770` -> working tree: add GitHub issue templates, PR template, and a repo-neutral security policy
- adapted from `77b7ee1`, `7ad47dc`, `11aca99`, and `18ad6ce` -> working tree: refresh repository-facing docs links, modernize README copy, and document Docker SSH key usage without importing fork-specific branding
- local documentation corrections: update MkDocs repo metadata and fix developer guide repository, docs deployment, and registry examples for this fork

Pending:
- none

Covered elsewhere:
- `77b7ee1`: README screenshot update covered by the adapted README refresh in this pass
- `18ad6ce`: Docker SSH key guidance covered by the updated installation docs in this pass

Skipped:
- CI-only `.github/workflows/master.yml` documentation-adjacent commits: out of scope for Subject 1 and better handled under Subject 2
- `.planning/*` process artifacts and `doc/logs_55588916360.zip`: not durable maintainer or user documentation for this repo
- `4cbdaa5`: GLIBC 2.29+/Ubuntu 20.04+ requirement note depends on packaging/runtime changes that belong under compatibility and packaging subjects, not Subject 1
- `2ce3852`: broader GitHub Pages site overhaul mixes content refresh with docs-site structure and workflow changes; current pass took the portable content-level improvements only
- `c71fc80`, `5208aab`, `e3a074e`, `93bba55`: CLAUDE.md maintainer-note updates are fork-specific and not portable to this repo's `AGENTS.md` policy model
- `6c08a77`, `a9ce3dc`, `fab7829`: changelog/install version-history updates skipped because this repo does not yet maintain a stable MkDocs changelog page and those pinned version references would age poorly without a release-note strategy

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check` on all Subject 1 files
- manual checks: reviewed imported template/policy content and adapted documentation links/content for repo fit
- status: partially verified

Notes:
- Started from a dirty worktree with pre-existing line-ending churn in documentation files; this pass normalized the touched Subject 1 files to make the content changes reviewable.

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: master @ 38455d4f7fbb902a781f59d2cc93d3824ae231e9
- Source branch: rapidcopy/master
- Fork tip seen at pass start: c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269
- Reviewed in this pass: origin/master..rapidcopy/master (Subject 1 filtered)
- Last reviewed upstream commit (inclusive): c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269
- Resume from next: none at current tip
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- no direct rapidcopy cherry-picks in this pass

Pending:
- none

Covered elsewhere:
- `18ad6ce`: equivalent user-facing Docker SSH key guidance was integrated through the adapted local docs refresh
- minor README cleanup commits such as `5522203` and `ba93749`: superseded by the broader README refresh completed in this pass

Skipped:
- `AGENTS.md` session notes and `doc/MODERNIZATION-PLAN.md` planning artifacts: not durable repo policy
- rebrand-heavy documentation such as `6d59994`: conflicts with the repo goal of remaining recognizably SeedSync
- workflow-heavy documentation commits such as `94b8f07` and `5df693d`: better handled under Subject 2 or later code-aligned subjects
- broad README and developer-doc rewrites such as `b5373c9`, `c392a29`, `e4814be`, `5d2edbe`, and `34698f2`: useful in parts, but too tied to RapidCopy branding, feature set, or modernization claims that do not match this branch yet

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check` on all Subject 1 files
- manual checks: reviewed candidate commit summaries, spot-checked representative diffs, and reconciled overlapping README/doc ideas into the local adapted docs refresh
- status: partially verified

Notes:
- Rapidcopy has many README and maintainer-note commits, but a large share are tied to rebranding, CI migration, or ephemeral session tracking rather than durable Subject 1 documentation improvements.
- `poetry` was not available in the shell, so MkDocs rendering was not verified locally in this pass.

## Subject 2 - Tests, CI, And Verification Assets

### thejuran

- State: reviewed
- High-risk: no
- Integration base: master @ 9792f9806466ff91fd47ad5216b62bc2c5c466ac
- Source branch: thejuran/master
- Fork tip seen at pass start: a8561cdc318460de32de082e3cf33f6b6a0093cb
- Reviewed in this pass: origin/master..thejuran/master (Subject 2 filtered)
- Last reviewed upstream commit (inclusive): a8561cdc318460de32de082e3cf33f6b6a0093cb
- Resume from next: none at current tip
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- adapted from `1f0fa87` -> `8c7be91`: update web handler integration tests to use POST for queue/stop/extract and DELETE for deletion endpoints
- adapted from `a4356d4` and `56463ad` -> `8c7be91`: add pytest timeout and coverage configuration, shared pytest fixtures, coverage output ignores, and a `coverage-python` Make target
- adapted from `2bea28e` plus repo-neutral portions of later workflow refreshes -> `8c7be91`: update deprecated GitHub Actions versions and switch GHCR CI logins to `GITHUB_TOKEN`

Pending:
- none

Covered elsewhere:
- thejuran workflow and docker publish refinements such as `bbf1310`, `fd9c25f`, `eab6146`, and `a0b0e5f`: current pass took the safe action-version and GHCR-auth portions only; broader publishing behavior is better handled under packaging and install
- test-only fixes coupled to later code subjects, such as `5e2a62c`, `4c1bbab`, and `a4faeef`: deferred because they depend on behavior changes that belong under extraction, cleanup, and controller subjects rather than standalone Subject 2 work

Skipped:
- Angular and Node compatibility migrations such as `ff2f075`, `d8fcb08`, `6a695ac`, and the Angular upgrade chain: too coupled to dependency and build-tooling work for a conservative Subject 2 pass
- e2e environment and deb/docker matrix overhauls such as `0ac4470`, `b4fb946`, `c73205b`, `87d2d14`, and `984b8a1`: useful but tightly coupled to packaging, compatibility, and runtime-environment changes that should land with those subjects
- selector-only e2e updates such as `1047141` and `641ea85`: depend on UI changes that have not been integrated yet
- large new Python and Angular test suites such as `494ff3d`, `e9ac251`, `1896c58`, `637ab8e`, and later bulk-action/service test expansions: valuable, but too entangled with broader code changes to import cleanly during this narrow infrastructure-first pass

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check`; `python3 -m py_compile src/python/tests/conftest.py src/python/tests/integration/test_web/test_handler/test_controller.py`
- manual checks: parsed `src/python/pyproject.toml` with `tomllib` and reviewed the resulting CI/test-config diff against thejuran candidates
- status: partially verified

Notes:
- A targeted `python3 -m pytest tests/integration/test_web/test_handler/test_controller.py -q` attempt failed in this shell because local Python dependencies such as `tblib` are not installed outside the project environment.

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: master @ 9792f9806466ff91fd47ad5216b62bc2c5c466ac
- Source branch: rapidcopy/master
- Fork tip seen at pass start: c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269
- Reviewed in this pass: origin/master..rapidcopy/master (Subject 2 filtered)
- Last reviewed upstream commit (inclusive): c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269
- Resume from next: none at current tip
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- no direct rapidcopy cherry-picks in this pass

Pending:
- none

Covered elsewhere:
- workflow modernization in `94b8f07` and Playwright-enabling CI pieces in `5df693d`: conservative workflow refresh completed locally without adopting the rapidcopy-specific Playwright stack
- Python test tooling modernization in `035dc8c`: core coverage tooling landed through the adapted thejuran-based pytest improvements without bringing in rapidcopy-specific lint and type-check policy
- parser and validation test-coverage additions such as `58af9ee`, `981d707`, `9d58f10`, `64afa02`, `866921b`, and `ee0718a`: better reviewed together with their corresponding feature/code subjects instead of as isolated Subject 2 imports

Skipped:
- Playwright migration and backend/UI-only split test infrastructure such as `696866c` and `5df693d`: promising, but too entangled with rebrand-specific paths, new directories, and broader frontend/test-stack modernization for this conservative pass
- rapidcopy-specific e2e/docker test harness renames and branding changes such as `a74c19b`, `6d59994`, and related `RAPIDCOPY_*` variable renames: not portable to this repo as-is
- broad Angular 18 and Python 3.11 modernization-linked test churn such as `e0985b2`, `7f22141`, and `93e10ab`: belongs under dependencies, build tooling, and compatibility subjects
- mypy/ruff policy and dev-tooling additions such as `035dc8c`, `677be93`, and `d87f403`: useful but outside the narrow test-and-verification scope we completed here

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check`; `python3 -m py_compile src/python/tests/conftest.py src/python/tests/integration/test_web/test_handler/test_controller.py`
- manual checks: reviewed workflow, test harness, and test-suite candidate clusters against current base and classified rapidcopy-only stacks by portability
- status: partially verified

Notes:
- Rapidcopy has substantial test innovation, especially around Playwright and feature-specific suites, but much of it is best integrated alongside later dependency, compatibility, and feature-subject work rather than as a standalone Subject 2 import.

## Subject 3 - Dependencies And Build Tooling

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `master` @ `19fb3ec3dd56c9f40afb331e2f461c3fd98cc18c`
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Reviewed in this pass: `origin/master..thejuran/master` (Subject 3 filtered)
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: next commit after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- adapted `7f2de68`, `45ee834`, `a4356d4`, and `56463ad`: added `package-mode = false`, aligned `pytest`, `pytest-timeout`, and `pytest-cov` with the current Python 3.8 toolchain, regenerated `src/python/poetry.lock`, and switched the Makefile to `docker compose` in local commit `d7954f9`

Pending:
- none

Covered elsewhere:
- `0d0037d`: pytest `pythonpath` configuration already integrated in Subject 2 via `8c7be91`
- `2bea28e` and `8a661e2`: GitHub Actions modernization and lowercase GHCR handling already integrated in Subject 2 via `8c7be91`
- `c73205b`, `3f7af5c`, `b4fb946`, `4cbdaa5`, `2ae5173`, and `9392653`: ARM64, GLIBC, and CI matrix changes belong primarily to Subjects 4 and 5 rather than this tooling-only pass

Skipped:
- `9d72249`, `9a25b36`, `7042028`, and `4f19ec6`: broad Python 3.11 dependency refresh and lockfile modernization skipped for now because this branch still targets the existing Python 3.8 runtime baseline
- Angular and Node upgrade chain from `ff2f075` through `5706eaf`: skipped for now because it would replace the current frontend/runtime stack and is too cross-cutting for a conservative Subject 3 pass

Maintainer decisions:
- none

Verification:
- tests run: `poetry lock --no-update` using a standalone Python 3.8 interpreter, `poetry check`, `poetry install --no-interaction --no-root`, and `poetry run pytest --version`
- manual checks: reviewed Makefile and Poetry metadata diffs against the current Python 3.8 baseline; confirmed the Subject 2 test-tooling additions were previously unsatisfiable until this pass aligned the versions
- status: verified for the integrated batch

Notes:
- This pass deliberately kept the repo on the current Python 3.8 toolchain. The larger Python 3.11, GLIBC, ARM64, and Angular modernization work was reviewed and explicitly left out rather than mixed into a small tooling correction batch.

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `master` @ `19fb3ec3dd56c9f40afb331e2f461c3fd98cc18c`
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Reviewed in this pass: `origin/master..rapidcopy/master` (Subject 3 filtered)
- Last reviewed upstream commit (inclusive): `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Resume from next: next commit after `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- none directly; the local Subject 3 batch stayed closer to the current SeedSync toolchain than rapidcopy's broader modernization path

Pending:
- none

Covered elsewhere:
- `d37afa7`, `1513a91`, `e4ea3f4`, `9b2945f`, and `c70d948`: Poetry, pytest, and GitHub Actions adoption are already present in the current base or were advanced in Subject 2
- `94b8f07` and `5df693d`: workflow and verification documentation belong to Subject 2 and later milestone validation rather than this subject

Skipped:
- `be58538`, `ad95ab2`, `035dc8c`, and `ab07571`: Python 3.11 dependency uplift, lockfile refresh, and new lint/type-check toolchain skipped for now because they would move the repo off the current Python/runtime baseline
- `e0985b2` and `696866c`: Angular 18 and Playwright replatforming skipped for now because they are broad framework migrations rather than conservative tooling maintenance
- rapidcopy-specific rename and publication-path changes in the Subject 3 surface were skipped because this repository remains a SeedSync integration fork rather than a rebrand

Maintainer decisions:
- none

Verification:
- tests run: reused the local Subject 3 verification from `d7954f9`; no rapidcopy-specific code was imported directly
- manual checks: compared rapidcopy's dependency and tooling changes against the current base and rejected the replatforming/rebranding portions as unnecessary divergence for this fork
- status: verified for the integrated batch and reviewed for the skipped items

Notes:
- Rapidcopy has useful modernization ideas, but in this subject they are mostly packaged as Python 3.11, linting, Angular 18, and Playwright replatforming. Those were consciously left out to keep the fork conservative while broader compatibility subjects are still pending.

## Subject 4 - Packaging And Install

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `master` @ `17b3d7b8aa74e5a5d73f4223fef3521695c3f29d`
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Reviewed in this pass: `origin/master..thejuran/master` (Subject 4 filtered)
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: next commit after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- adapted `a27e231` and `0fffbdf`: removed obsolete `dh-systemd` usage, raised the debhelper baseline, and stopped trying to derive host shared-library dependencies for the PyInstaller-built Debian package in local Subject 4 packaging updates
- adapted `9759b76`, `ef283cc`, and `f2b4889`: modernized the deb build image so the PyInstaller stage still builds on current bases and ensured `scanfs` plus the Angular bundle land under the bundled `_internal` runtime layout expected by the packaged binary
- adapted parts of `11b0944`, `7adbd5f`, `c83efbc`, `c32649c`, and `21bb73c`: fixed Docker build compatibility on current bases by creating the Angular output directory, making apt source edits work on modern slim images, using the image's bundled `pip` for Poetry, switching to `poetry install --only main`, normalizing `FROM ... AS` casing, normalizing copied helper-script line endings inside the image, and adding an OCI source label in the runtime image
- adapted `777917a` as a packaging prerequisite: enabled `skipLibCheck` in `src/angular/tsconfig.json` so the legacy Angular build remains runnable inside the current Docker packaging flow

Pending:
- none

Covered elsewhere:
- `c94d626`, `a8a6eba`, and other GLIBC/older-host compatibility follow-ups were reviewed here but belong primarily to Subject 5, where runtime compatibility choices will be handled explicitly

Skipped:
- `5ea6f8e`: skipped the self-contained runtime-image rewrite because the smaller conservative fix was to keep the existing staging/export workflow and only repair the broken packaging steps
- thejuran's broader cgroup, ARM64, and CI/publish refinements in the packaging surface were skipped here because they are better handled under Subjects 5 and the later verification milestone rather than mixed into this packaging pass

Maintainer decisions:
- none

Verification:
- tests run: `make deb`; `docker build -f src/docker/build/deb/Dockerfile --target seedsync_build_angular_export -t localtest/seedsync/build/angular/export:s4 .`; `docker build -f src/docker/build/deb/Dockerfile --target seedsync_build_scanfs_export -t localtest/seedsync/build/scanfs/export:s4 .`; `docker build -f src/docker/build/docker-image/Dockerfile --target seedsync_run --build-arg STAGING_REGISTRY=localtest --build-arg STAGING_VERSION=s4 -t seedsync:s4 .`
- manual checks: `git diff --check` on the touched packaging files; inspected `setup_default_config.sh` after normalizing CRLF line endings; ran `docker run --rm --entrypoint sh seedsync:s4 -lc 'id ... python /app/python/seedsync.py -c /config --html /app/html --scanfs /app/scanfs --exit'` to confirm runtime file permissions, generated config ownership, and startup behavior
- status: verified for the integrated batch; runtime smoke check reaches SeedSync startup and then exits with `AppError: Config is incomplete`, which matches the placeholder default configuration rather than a packaging failure

Notes:
- This pass intentionally repaired the existing Debian and Docker packaging flow instead of taking the larger thejuran runtime-image redesign. The image now builds on current Docker bases, preserves the repo's staging/export structure, and keeps the remaining runtime-compatibility decisions for Subject 5.

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `master` @ `17b3d7b8aa74e5a5d73f4223fef3521695c3f29d`
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Reviewed in this pass: `origin/master..rapidcopy/master` (Subject 4 filtered)
- Last reviewed upstream commit (inclusive): `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Resume from next: next commit after `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- adapted `207b75e` and `2e175c0`: ensured the runtime image fixes file modes for `/scripts/setup_default_config.sh` and the copied `/app/python` tree so the non-root container can execute the packaged startup path reliably, while also normalizing copied helper scripts defensively for CRLF-prone Windows-backed checkouts

Pending:
- none

Covered elsewhere:
- rapidcopy's Poetry- and Python-version uplift in packaging overlaps with the already completed Subject 3 dependency/tooling baseline work and the Subject 5 compatibility decisions still to come

Skipped:
- rapidcopy's top-level `Dockerfile`, compose, rename, publication, and rebranding changes were skipped because this repo remains a conservative SeedSync integration fork rather than adopting the rapidcopy container/product identity
- broader rapidcopy packaging changes tied to Python 3.11, new publication paths, and different runtime defaults were skipped because they are larger workflow choices than this subject needed

Maintainer decisions:
- none

Verification:
- tests run: reused the local Subject 4 packaging verification above; the integrated rapidcopy-derived permission fixes are exercised by the successful runtime image build and in-container smoke check
- manual checks: compared rapidcopy's packaging surface against the current base and took only the portable permission corrections
- status: verified for the integrated batch and reviewed for the skipped items

Notes:
- Rapidcopy had a few clean container-permission fixes that fit this fork well. The rest of its packaging surface was bundled with repo-specific naming and publication choices, so those were consciously left out.

## Subject 5 - Compatibility And Platform Support

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `master` @ `0c03c18`
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Reviewed in this pass: `0c03c18..a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: new commits after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- New upstream since last pass: none at review time
- Pass date: 2026-03-08

Integrated:
- `8c4edb2` adapted locally in this pass: replaced `css-element-queries`/`ResizeSensor` with native `ResizeObserver` in the legacy Angular shell, without dragging in thejuran's newer Angular workspace layout or Bootstrap changes
- `721e694` adapted locally in this pass: took the Safari toolbar color-bleed fix for sticky header rendering
- `05bc17a` adapted locally in this pass: applied the Debian 12 test-image compatibility fixes to `src/docker/test/angular/Dockerfile` and `src/docker/test/python/Dockerfile`

Pending:
- none

Covered elsewhere:
- `da6a4c6` and `9d72249`: larger Python 3.11 and lockfile changes belong to Subject 3 and were already reviewed there
- `246c063`: the GitHub API CSP allowlist is not needed in the current base because `src/python/web/web_app.py` does not set a CSP header today; the broader frontend/script-side implications are addressed by adapting `8c4edb2` instead
- `e5416c5`, `b5cf1d2`, `87d2d14`, `55e5823`, `984b8a1`, `e7aece9`, `f65a996`, and `5e2cc8e`: cgroup v2/systemd and staged E2E host-model work is better handled during `Verification Milestone A` than mixed into this compatibility pass

Skipped:
- `246c063` jQuery-removal portion: skipped because the current Angular 4/Bootstrap 4 shell still consumes jQuery-based scripts, so removing it here would mix compatibility work with a broader frontend behavior change
- `0e6370e`: skipped because loosening CSP with `script-src 'unsafe-inline'` is a security tradeoff that does not belong in this compatibility-only pass
- `c94d626`, `996ae6a`, `a8a6eba`, `4cbdaa5`, and `2ae5173`: skipped because the current Python 3.8 packaging path did not reproduce the newer Python 3.11 GLIBC floor those commits were compensating for, and the existing built deb artifacts in this repo did not show the claimed raised GLIBC requirement

Maintainer decisions:
- none

Verification:
- tests run: `docker compose -f src/docker/test/angular/compose.yml build` (passed), `make run-tests-angular` (reached Karma startup, then failed because the current Angular test container still lacks a usable Chrome binary), `make tests-python` (failed before the touched Dockerfile layer because the existing `seedsync_run_python_devenv` image currently errors during `poetry install` with `ImportError: cannot import name 'atomic_open' from requests.utils`)
- manual checks: reviewed adapted diffs against `8c4edb2`, `721e694`, and `05bc17a`; extracted the current deb artifact and checked bundled `seedsync`/`scanfs` binaries for GLIBC symbol strings before deciding not to take the newer GLIBC-floor documentation and CI-matrix reductions
- status: partially verified

Notes:
- The Angular/browser fixes were adapted onto the repo's legacy `.angular-cli.json`/Angular 4 layout rather than importing thejuran's newer workspace structure wholesale
- The Angular test-image build still emits Debian stretch repository 404 warnings from the inherited base image, and the runtime test path still needs a separate Chrome-binary fix even though the touched Dockerfile path built successfully after installing `wget` and `gnupg`

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `master` @ `0c03c18`
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Reviewed in this pass: `0c03c18..c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Last reviewed upstream commit (inclusive): `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Resume from next: new commits after `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- New upstream since last pass: none at review time
- Pass date: 2026-03-08

Integrated:
- adapted the path-pair persistence, legacy-config migration, and `Context` startup plumbing portion of `d143638` as the first Subject 15 implementation batch

Pending:
- none

Covered elsewhere:
- `5db8f34`, `c1e079a`, `3ad06ce`, `62e14e2`, `c487178`, and `2238a32`: LFTP parser/timeout/PTY hardening belongs primarily to Subject 14
- `2614ae6` and `866921b`: validation batching and settle-delay work belongs with the validation/scanning/controller subjects rather than platform compatibility alone
- `5d5a90a` and `aeb27fa`: scanfs pickle/JSON compatibility work belongs to Subject 15 and also needs security review
- `0b49f97`, `58c588b`, and `a33981b`: network-mount and Docker-path validation work belongs to the packaging/config/settings subjects
- `696866c` and `5df693d`: Playwright migration and backend-aware E2E gating belong to Subject 2 and `Verification Milestone A`
- `7f22141` and `e4814be`: Angular 18 migration and frontend compatibility notes belong to the broader frontend modernization subjects, not this conservative compatibility pass

Skipped:
- `cb55471`: not taken in Subject 5 because the connection-cap safety check is more appropriately decided with the LFTP/config behavior work in later subjects, where the user-facing cap and accompanying settings guidance can be reviewed together
- `0c73e23`: not taken because changing the default process umask to `002` is a user-visible permission-policy change, not a no-drama platform fix
- `4fca389`: not taken because this repo's current packaging path already differs substantially from rapidcopy's top-level Dockerfile flow, so the packaging baseline should continue to be handled through Subject 4 plus milestone verification instead of cross-importing a different build graph here

Maintainer decisions:
- none

Verification:
- tests run: none specific to rapidcopy-only candidate imports; this fork's Subject 5 review in this pass was classification-only
- manual checks: reviewed candidate clusters against current base and mapped them either to later subjects or to explicit skip reasons so Subject 5 does not retain normal pending items
- status: review-only

Notes:
- Rapidcopy currently carries many good fixes, but most compatibility-adjacent items in the reviewed range are tightly coupled to later subject areas rather than being clean Subject 5 imports on their own

## Subject 6 - Security And Hardening

### thejuran

- State: reviewed
- High-risk: yes
- Integration base: `master` @ `549c378`
- Source branch: `thejuran/master`
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: subject-filtered review of `origin/master..thejuran/master`
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: `a8561cd..thejuran/master`
- New upstream since last pass: none at pass end
- Pass date: 2026-03-08

Integrated:
- adapted from `thejuran` `f6643db`: stop tracking the committed staging private key, ignore future private-key filenames, and generate a fresh stage-image SSH keypair at build time instead of copying a committed secret into the image
- adapted from `thejuran` `e34ba5e`: harden `Sshcp` to pass argument lists directly to `pexpect.spawn`, accept new host keys while rejecting changed ones, and preserve usable bad-host/bad-port/wrong-password behavior across newer SSH prompt variants
- adapted from `thejuran` `492944f`: shell-quote remote delete paths before issuing `rm -rf` over SSH
- adapted from `thejuran` `0a4a410`, `b9a3220`, and `9048377`: redact `lftp.remote_password` from config API serialization and scrub password-like values from SSE log messages and tracebacks

Pending:
- none

Covered elsewhere:
- `246c063` and `8c4edb2`: CSP-violation cleanup tied to later Angular/runtime changes; revisit with the broader web/UI subject work instead of forcing partial CSP behavior into the legacy frontend now
- `108018f` and `abef04a`: replacing pickle-based remote scan serialization belongs with the broader scan/remote-scanner behavior work in later backend subjects

Skipped:
- `a92af56`: webhook HMAC authentication is not applicable to the current base because the webhook feature set is not present here yet
- `6e680df`: SSRF protection for arr-style test-connection endpoints is not applicable to the current base because those endpoints are not present yet
- `8271bd6` and `9365743`: confirm-modal XSS hardening is not applicable because the current base does not yet contain `ConfirmModalService`
- `4c485d9` and `0e6370e`: broad response-header/CSP changes were not taken in this pass because the useful low-risk hardening was already captured elsewhere, while the CSP policy tradeoffs need to be evaluated together with later web/API work

Maintainer decisions:
- none

Verification:
- tests run: `python3 -m py_compile src/python/web/serialize/serialize_config.py src/python/web/serialize/serialize_log_record.py src/python/controller/delete/delete_process.py src/python/ssh/sshcp.py src/python/tests/unittests/test_web/test_serialize/test_serialize_config.py src/python/tests/unittests/test_web/test_serialize/test_serialize_log_record.py src/python/tests/unittests/test_ssh/test_sshcp.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -v tests/unittests/test_web/test_serialize/test_serialize_config.py tests/unittests/test_web/test_serialize/test_serialize_log_record.py tests/unittests/test_ssh/test_sshcp.py` (37 passed); `docker build -f src/docker/stage/deb/Dockerfile -t seedsync/test/stage-deb-security .`
- manual checks: confirmed the stage image now generates its SSH keypair during build and no longer depends on the committed private key file
- status: verified

Notes:
- This pass intentionally kept Subject 6 to low-controversy hardening. It avoided introducing a new API auth contract or broad CSP restrictions while still closing the most obvious secret-handling and SSH-safety gaps.

### rapidcopy

- State: reviewed
- High-risk: yes
- Integration base: `master` @ `549c378`
- Source branch: `rapidcopy/master`
- Fork tip seen at pass start: `c65ddf6`
- Reviewed in this pass: subject-filtered review of `origin/master..rapidcopy/master`
- Last reviewed upstream commit (inclusive): `c65ddf6`
- Resume from next: `c65ddf6..rapidcopy/master`
- New upstream since last pass: none at pass end
- Pass date: 2026-03-08

Integrated:
- adapted from `rapidcopy` `32acba6` and `78a3fde` selectively: keep the security wins that fit the current base without taking the broader auth/CSRF contract changes, namely safer SSH argument handling/host-key behavior, password redaction, private-key hygiene, and command quoting

Pending:
- none

Covered elsewhere:
- `bb539a5` and `de8b602`: private-key hygiene is covered by the local adaptation of thejuran's committed-key removal plus the `.gitignore` update in this pass
- the password-redaction and shell-command-safety portions of `32acba6` and `78a3fde` are covered by the local/thejuran-adapted changes integrated in this pass

Skipped:
- `9f91d1c`: API-key authentication layer was not taken because it introduces a user-facing auth contract, browser-side key storage, and SSE auth behavior that should be evaluated with broader web/API changes instead of as a standalone hardening import
- the remaining `9e1aeea`, `32acba6`, and `78a3fde` items around CSP, CSRF/origin enforcement, input caps, path-pair validation, and pickle removal were not taken here because they either belong to later subject areas or require broader deployment/model review than this conservative hardening pass

Maintainer decisions:
- none

Verification:
- tests run: same local verification batch as the thejuran entry for this pass
- manual checks: compared rapidcopy's harder auth/CSRF posture against current base and chose the lower-controversy subset that strengthens security without changing the repo's current access model
- status: verified

Notes:
- Rapidcopy contains a larger, more opinionated security program. This pass deliberately took only the compatible hardening ideas and left the control-plane contract changes for later subject review if they become desirable.

## Verification Milestone A - Tooling, Packaging, And Compatibility Validation

### local validation

- State: reviewed
- High-risk: no
- Integration base: `master` @ `55dac61`
- Source branch: local milestone validation
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: milestone complete
- New upstream since last pass: n/a
- Pass date: 2026-03-08

Integrated:
- adapted from `thejuran` `8cab3ee` plus local dev-stage repair: keep the Docker Poetry bootstrap on a Python 3.8-compatible release and reinstall Poetry in `seedsync_run_python_devenv` before the second `poetry install`, so the app's pinned `requests` dependency no longer breaks Poetry itself during test-image setup
- adapted from `thejuran` `e981c6b`, `f38ae7e`, and `655b6b2` plus a stretch-safe local install path: switch the Angular test image to archived Debian Stretch Chromium packages, add a stable `google-chrome-stable` symlink, and harden the Karma headless launchers used by `make run-tests-angular`
- local test-image normalization: strip CRLF from `src/docker/test/python/entrypoint.sh` inside the Python test image so the container entrypoint runs correctly on Windows-backed checkouts

Pending:
- none

Covered elsewhere:
- the earlier Subject 5 browser/runtime notes about missing Chrome are resolved by this milestone's Angular test-image changes
- the earlier Subject 5 Python/Poetry environment blocker is resolved by this milestone's Docker Poetry bootstrap changes

Skipped:
- full Angular/Node/Playwright replatforming: not needed to make the current verification path runnable
- broader Python runtime uplift: not needed to make the current Python 3.8 test path runnable

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check`; `make tests-python` (passed); `make run-tests-angular` (passed, 183 tests); `make run-tests-python` (environment now runs end-to-end under Docker and reaches real pytest execution; early suite failures observed in `tests/integration/test_controller/test_controller.py`, including `test_bad_config_remote_address_raises_exception`, `test_bad_config_remote_path_to_scan_script_raises_exception`, and `test_bad_config_remote_username_raises_exception`)
- manual checks: confirmed `docker compose` test images build with working browser/runtime paths; verified `.github/workflows/master.yml` still calls the same `make run-tests-python` and `make run-tests-angular` entrypoints exercised locally
- status: partially verified

Notes:
- This milestone completed its main purpose: Python and Angular verification are now runnable in the current local/CI container model instead of failing on missing tooling, missing browser binaries, or broken Poetry bootstraps.
- The remaining Python issues are no longer environment blockers. They are real test or application failures and should be handled under the relevant later code subjects rather than by more packaging/tooling churn.


## Subject 7 - About, Modal, And Shared UI Components

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `master` @ `07d5716`
- Source branch: `thejuran/master`
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: subject-filtered review of `origin/master..thejuran/master`
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: `a8561cd..thejuran/master`
- New upstream since last pass: none at pass end
- Pass date: 2026-03-08

Integrated:
- adapted from `thejuran` `67179ea`: stop `AppComponent` route-event subscriptions from accumulating by moving them under a `takeUntil`/`destroy$` lifecycle cleanup
- adapted from `thejuran` `b1b7ec9`: stop long-lived `SettingsPage` and `AutoQueuePage` subscriptions from leaking across page teardown by adding the same `takeUntil` cleanup pattern
- adapted from `thejuran` `fdafd54`: refresh the About page to point users at the maintained fork and issue tracker while keeping the existing SeedSync presentation style

Pending:
- none

Covered elsewhere:
- `8c4edb2`: the current base already uses a local `ResizeObserver` compatibility fix in `AppComponent`, so the CSP-safe resize-observer part of thejuran's later shared-shell work was already present here
- the current base already uses `ngx-modialog` for destructive-action confirmation flows, so replacing the modal stack is not required to keep Subject 7 stable

Skipped:
- `12a05c8`: the ASCII-art About page redesign is more opinionated than this fork's conservative UI direction
- `8271bd6`, `9365743`, `31889ad`, `52b72a6`, and `fdb2b7f`: the newer `ConfirmModalService` stack and related modal hardening were not taken because this base does not use that modal implementation yet, so importing it here would turn a focused shared-UI pass into a broader modal-framework swap

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check -- src/angular/src/app/pages/main/app.component.ts src/angular/src/app/pages/settings/settings-page.component.ts src/angular/src/app/pages/autoqueue/autoqueue-page.component.ts src/angular/src/app/pages/about/about-page.component.html src/angular/src/app/pages/about/about-page.component.scss`; `make run-tests-angular` (passed, 183 tests)
- manual checks: confirmed the About page stays visually aligned with the existing SeedSync shell while updating the repository links to the maintained fork
- status: verified

Notes:
- `Verification Milestone A` exposed real Python failures in `tests/integration/test_controller/test_controller.py` around bad remote config validation, including `test_bad_config_remote_address_raises_exception`, `test_bad_config_remote_path_to_scan_script_raises_exception`, and `test_bad_config_remote_username_raises_exception`. Revisit those here together with Subject 10 config validation work instead of reopening the milestone.

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `master` @ `07d5716`
- Source branch: `rapidcopy/master`
- Fork tip seen at pass start: `c65ddf6`
- Reviewed in this pass: subject-filtered review of `origin/master..rapidcopy/master`
- Last reviewed upstream commit (inclusive): `c65ddf6`
- Resume from next: `c65ddf6..rapidcopy/master`
- New upstream since last pass: none at pass end
- Pass date: 2026-03-08

Integrated:
- no direct imports; the low-risk subscription cleanup and conservative About-page refresh taken from thejuran cover the strongest compatible shared-UI wins for the current base

Pending:
- none

Covered elsewhere:
- `129d919`: scroll-to-top routing behavior is already present in the current base, and Subject 7's `AppComponent` cleanup keeps that existing behavior healthy instead of re-importing it
- the modal-confirmation intent behind `a8c1b80` and `13fdce1` is already covered by the current base's `ngx-modialog` delete confirmations

Skipped:
- `797ebfa`: restart confirmation and mixed UI tweaks were not taken because the compatible shared-component portion is either already covered or belongs with Subject 8 settings behavior rather than this conservative shared-UI pass
- `e6530cb`: modal text-overflow tweaks were not taken separately because the current modal stack differs and the issue was not reproducible enough to justify a broad modal restyle
- `08d714e`, `6d59994`, `fb4e7db`, and `936ae4b`: rebrand, theming, and self-update work is outside this subject's conservative shared-component scope

Maintainer decisions:
- none

Verification:
- tests run: same local verification batch as the thejuran entry for this pass
- manual checks: compared rapidcopy's shared-UI changes against the current base and kept the lower-controversy subset that improves lifecycle hygiene without pulling in rebrand, theme, or modal-stack churn
- status: verified

Notes:
- `Verification Milestone A` exposed real Python failures in `tests/integration/test_controller/test_controller.py` around bad remote config validation, including `test_bad_config_remote_address_raises_exception`, `test_bad_config_remote_path_to_scan_script_raises_exception`, and `test_bad_config_remote_username_raises_exception`. Revisit those here together with Subject 10 config validation work instead of reopening the milestone.

## Subject 8 - Settings UI

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `master` @ `394d834`
- Source branch: `thejuran/master`
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: subject-filtered review of `origin/master..thejuran/master`
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: `a8561cd..thejuran/master`
- New upstream since last pass: none at pass end
- Pass date: 2026-03-08

Integrated:
- none

Pending:
- none

Covered elsewhere:
- `b1b7ec9`: the settings-page subscription cleanup was already integrated in Subject 7
- the current base already keeps the existing settings layout and command/restart wiring healthy without needing thejuran's broader settings-schema additions

Skipped:
- `9929c51`: auto-delete settings UI depends on backend/config sections that are not present in the current base; revisit with the related backend subjects instead of adding dead controls here
- `7bebe91`, `2b89bbc`, and `84a365a`: Sonarr/Radarr, webhook guidance, and connection-test settings UI require backend/config support that does not exist in the current base, so they were deferred rather than imported as broken or misleading controls
- `528e845`: appearance/theme controls depend on a theme subsystem that is not present in the current base
- `b7fdff1`: terminal-style settings presentation is a style-forward redesign that does not fit this fork's conservative UI direction

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check -- src/angular/src/app/common/localization.ts src/angular/src/app/pages/settings/options-list.ts src/angular/src/app/pages/settings/settings-page.component.ts`; `make run-tests-angular` (passed, 183 tests)
- manual checks: confirmed the thejuran settings candidates mostly depend on future backend/schema work and would create dead or misleading UI if imported now
- status: verified

Notes:
- none

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `master` @ `394d834`
- Source branch: `rapidcopy/master`
- Fork tip seen at pass start: `c65ddf6`
- Reviewed in this pass: subject-filtered review of `origin/master..rapidcopy/master`
- Last reviewed upstream commit (inclusive): `c65ddf6`
- Resume from next: `c65ddf6..rapidcopy/master`
- New upstream since last pass: none at pass end
- Pass date: 2026-03-08

Integrated:
- adapted from `rapidcopy` `207caf5`: improve the `Max Total Connections` settings guidance, but soften the wording to match the current base by warning that values above 32 are not recommended instead of falsely implying an enforced hard cap
- adapted from `rapidcopy` `797ebfa` selectively: add a restart confirmation step before issuing the settings-page restart command, implemented with the current `ngx-modialog` stack instead of `window.confirm`

Pending:
- none

Covered elsewhere:
- `20ebcbc`: the current base already uses a plain label-wrapped checkbox layout instead of the Bootstrap `form-check` structure that rapidcopy was fixing, so there was no direct overlap bug left to import here
- the percentage-cap and disabled-button tooltip portions of `797ebfa` are already outside Subject 8 or covered elsewhere in the current base

Skipped:
- `a33981b`: path-pair migration, Docker path warnings, and related validation changes cross into backend/config behavior and should be handled with the later config/path subjects instead of as a standalone settings-UI import

Maintainer decisions:
- none

Verification:
- tests run: same local verification batch as the thejuran entry for this pass
- manual checks: confirmed the updated connections help text matches current backend behavior, and that the restart safeguard now uses the repo's existing modal stack instead of introducing a second confirmation style
- status: verified

Notes:
- none

## Subject 9 - Logs UI

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `master` @ `befa59f`
- Source branch: `thejuran/master`
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: subject-filtered review of `origin/master..thejuran/master`
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: `a8561cd..thejuran/master`
- New upstream since last pass: none at pass end
- Pass date: 2026-03-08

Integrated:
- adapted from `thejuran` `eef4c32` and `1e8afe7`: move the logs subscription to a view-ready lifecycle, guard `ViewChild` access, and only auto-scroll when the logs page is actually visible
- adapted from `thejuran` `8dd7bf4`: show a clear logs-page empty state while waiting for the service connection or the first log event
- adapted from `thejuran` `a7122fc` and `b53fe7d`: harden `LogService` against malformed JSON log events and route parse failures through the existing Angular logger, with a unit test to keep the behavior stable

Pending:
- none

Covered elsewhere:
- `db1d26c`: the older scroll-button and sticky-marker logs improvements were already present in the current base before this pass
- `0538f71`: the current `ReplaySubject`-based log stream already caches historical log records across page navigation

Skipped:
- `4933ba0`: the current logs stylesheet already uses aggressive line-breaking rules, so the older long-line layout fix did not add enough value to justify extra churn
- `d857098`, `3f1d7d2`, `0f24cbf`, and related logs-theme restyles were left out because they are presentation-forward and better handled with broader UI/theming work than this conservative logs pass

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check -- src/angular/src/app/common/localization.ts src/angular/src/app/services/logs/log.service.ts src/angular/src/app/pages/logs/logs-page.component.html src/angular/src/app/pages/logs/logs-page.component.scss src/angular/src/app/pages/logs/logs-page.component.ts src/angular/src/app/tests/unittests/services/logs/log.service.spec.ts`; `make run-tests-angular` (passed, 184 tests)
- manual checks: confirmed the logs page no longer depends on `ViewChild` references before the view exists and now shows a clear waiting state instead of a blank page
- status: verified

Notes:
- none

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `master` @ `befa59f`
- Source branch: `rapidcopy/master`
- Fork tip seen at pass start: `c65ddf6`
- Reviewed in this pass: subject-filtered review of `origin/master..rapidcopy/master`
- Last reviewed upstream commit (inclusive): `c65ddf6`
- Resume from next: `c65ddf6..rapidcopy/master`
- New upstream since last pass: none at pass end
- Pass date: 2026-03-08

Integrated:
- adapted from `rapidcopy` `d4e4b7e` selectively: cap the live logs DOM at 500 rendered records so long-running sessions do not keep growing the page without bound

Pending:
- none

Covered elsewhere:
- the connection/waiting-state UX and malformed-log resilience from the rapidcopy-era logs work are covered by thejuran-derived adaptations already taken in this pass

Skipped:
- `2054b14`: log-file persistence and text-search UI are useful but substantially broaden the logs feature surface, so they were deferred in favor of the lower-risk stability and clarity fixes
- the broader logs-related UI/theming changes from `4527bfe`, `fb4e7db`, and later modernization/rebrand commits were not taken here because they either belong to other subjects or are more opinionated than this conservative logs pass

Maintainer decisions:
- none

Verification:
- tests run: same local verification batch as the thejuran entry for this pass
- manual checks: confirmed the live logs view now evicts old rendered entries while preserving the existing stream/backscroll behavior users already expect
- status: verified

Notes:
- none

## Subject 10 - Config And Settings Backend

### thejuran

- State: reviewed
- High-risk: no
- Integration base: 0bca4c1
- Source branch: thejuran/master
- Fork tip seen at pass start: a8561cd
- Reviewed in this pass: origin/master..thejuran/master (subject-relevant config/settings backend files)
- Last reviewed upstream commit (inclusive): a8561cd
- Resume from next: new relevant commits after a8561cd
- New upstream since last pass: none recorded at pass close
- Pass date: 2026-03-08

Integrated:
- `19f697d` adapted `bb283e6` to replace `distutils.util.strtobool` in `src/python/common/config.py` with a local Python 3.12-safe helper and added focused unit coverage.
- `ffd7415` followed up the Subject 10 review by tightening `num_max_total_connections` validation so `0` no longer bypasses the safety cap and extended the focused unit coverage.

Pending:
- none

Covered elsewhere:
- `0a4a410`, `9048377` config-response redaction additions belong with Subject 11 API/config exposure follow-up and Subject 6 hardening reconciliation rather than this backend-only pass.
- `6e680df`, `850f500`, `a92af56` add new config-adjacent API/auth surface and should be reviewed with Subject 11 and security-sensitive web/API work, not folded into Subject 10.

Skipped:
- `7242704`, `312f460`, `d12305e`, `24a9698`, `e053b96` were not taken in this pass because they introduce broader Sonarr/Radarr/AutoDelete config schema that is tightly coupled to larger feature chains not yet accepted here.

Maintainer decisions:
- none

Verification:
- tests run: `cd src/python && poetry run pytest -q tests/unittests/test_common/test_config.py tests/unittests/test_seedsync.py`
- manual checks: reviewed `common/config.py` against the adapted upstream slices to keep the patch limited to parser compatibility only and rechecked the post-review fix so the connection-limit guard now matches current lftp semantics
- status: verified for integrated batch

Notes:
- `Verification Milestone A` controller config failures were rechecked during Subject 10 scoping; they were not directly solved by the isolated thejuran config commits and remain classified under later backend/controller work.

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: 0bca4c1
- Source branch: rapidcopy/master
- Fork tip seen at pass start: 6ce7c19
- Reviewed in this pass: origin/master..rapidcopy/master (subject-relevant config/settings backend files)
- Last reviewed upstream commit (inclusive): 6ce7c19
- Resume from next: new relevant commits after 6ce7c19
- New upstream since last pass: none recorded at pass close
- Pass date: 2026-03-08

Integrated:
- `19f697d` adapted `cb55471` to cap `Config.Lftp.num_max_total_connections` at `32` with a clear config error and focused unit coverage.
- `4056a53` adapted `4516bd5` into `src/python/seedsync.py` so `persist()` only backs up and rewrites `settings.cfg` when the rendered config content actually changes.
- `ffd7415` followed up the Subject 10 review by making the `persist()` write gate recover cleanly when `settings.cfg` is missing and by aligning the config cap with the current unlimited-on-`0` lftp behavior.

Pending:
- none

Covered elsewhere:
- `9f91d1c` adds API-key auth and broader web contract changes that belong with Subject 6/Subject 11, not this backend-config-only pass.
- mixed test/config fragments from `fc57113` and `ee0718a` that support validation/files feature work belong with their primary subjects rather than being imported standalone here.

Skipped:
- `4acc00b` backup-on-save rotation changes were skipped because they change user-visible backup policy and are partly coupled to larger path-pair work outside this subject.

Maintainer decisions:
- none

Verification:
- tests run: `cd src/python && poetry run pytest -q tests/unittests/test_common/test_config.py tests/unittests/test_seedsync.py`
- manual checks: reviewed the adapted persist path against `rapidcopy` `4516bd5` to keep behavior limited to write/no-write gating without bringing in broader backup-policy changes, then re-reviewed the missing-file path after the independent reviewer caught the regression
- status: verified for integrated batches

Notes:
- Subject 10 is complete for the currently reviewed `thejuran` and `rapidcopy` ranges; remaining config-adjacent upstream work was either classified under later subjects or consciously skipped for coupling/user-facing policy reasons.

## Subject 11 - Model, Serialization, And Web API

### thejuran

- State: reviewed
- High-risk: no
- Integration base: 25c99a4
- Source branch: thejuran/master
- Fork tip seen at pass start: a8561cd
- Reviewed in this pass: origin/master..thejuran/master (subject-relevant web/model/serialize/handler files)
- Last reviewed upstream commit (inclusive): a8561cd
- Resume from next: new relevant commits after a8561cd
- New upstream since last pass: none recorded at pass close
- Pass date: 2026-03-08

Integrated:
- `d0b9195` adapted the low-risk REST status portions of `88d96a1` so config and auto-queue handlers now return `404` for missing resources and `409` for duplicate pattern creation, with the matching integration assertions updated.
- `d0b9195` adapted current-architecture handler and stream unit coverage from `1896c58`, `637ab8e`, and `074630c` into focused tests for config, auto-queue, server, status, model-stream, and status-stream behavior.

Pending:
- none

Covered elsewhere:
- `bb283e6` was already integrated in Subject 10 via `19f697d`.
- `0a4a410`, `b9a3220`, and `9048377` were already handled during Subject 6 security/config-redaction work.

Skipped:
- `88d96a1` controller-side callback status propagation was skipped in this pass because it overlaps controller command behavior and belongs with later Subject 18 review.
- `a50a6ec` and `05a0003` were skipped because the POST/DELETE mutation routing and endpoint-timeout wrappers depend on a broader newer controller/web contract than this branch currently carries.
- `0cb3228` was skipped because the current pinned Bottle `0.12.19` path does not reproduce the `_stop_flag` attribute conflict; revisit only if Bottle is upgraded later.
- `4c485d9` and `6e680df` remain classified under Subject 6 because they are primarily security-hardening changes rather than low-risk API semantics.
- `df868bc`, `5c7bfc8`, `a4cbdc6`, `4533679`, and `7297af2` remain classified under Subject 12 because they are bulk file-operation API work coupled to files-page behavior.
- `850f500`, `815a19d`, `cd8d78a`, `a92af56`, `2e54493`, and `9444eb2` were skipped here because they introduce larger webhook/import feature surfaces that belong with later model/controller feature subjects rather than this conservative API pass.

Maintainer decisions:
- none

Verification:
- tests run: `cd src/python && poetry run pytest -q tests/unittests/test_web/test_handler/test_auto_queue_handler.py tests/unittests/test_web/test_handler/test_config_handler.py tests/unittests/test_web/test_handler/test_server_handler.py tests/unittests/test_web/test_handler/test_status_handler.py tests/unittests/test_web/test_handler/test_stream_model_handler.py tests/unittests/test_web/test_handler/test_stream_status_handler.py tests/integration/test_web/test_handler/test_auto_queue.py tests/integration/test_web/test_handler/test_config.py`
- manual checks: confirmed the adapted `88d96a1` slice stayed limited to clear REST status semantics for config and auto-queue handlers, and rechecked `0cb3228` against the current Bottle `0.12.19` environment before classifying it as a future dependency-upgrade follow-up instead of taking it blindly
- status: verified for integrated batch

Notes:
- Subject 11 stays deliberately narrow in this pass: low-risk handler semantics and test coverage were taken, while broader controller contract and feature-surface changes were classified under later subjects instead of being half-merged here.

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: 25c99a4
- Source branch: rapidcopy/master
- Fork tip seen at pass start: 6ce7c19
- Reviewed in this pass: origin/master..rapidcopy/master (subject-relevant web/model/serialize/handler files)
- Last reviewed upstream commit (inclusive): 6ce7c19
- Resume from next: new relevant commits after 6ce7c19
- New upstream since last pass: none recorded at pass close
- Pass date: 2026-03-08

Integrated:
- none

Pending:
- none

Covered elsewhere:
- `de964a1` status error/failed field serialization is already present in current base behavior and did not require a new import.

Skipped:
- `5d3e1e8` and `aeb27fa` were skipped here because they depend on validation model states that are not yet present in this branch and belong with later validation/scanning work.
- `8d6b436` was skipped because queued-file prioritization changes span file-operations and transfer semantics, which belong with Subjects 12 and 14 instead of this API-only pass.
- `9f91d1c`, `9e1aeea`, `32acba6`, and `78a3fde` remain classified with Subject 6 and broader API-contract decisions rather than being pulled into this low-controversy Subject 11 batch.

Maintainer decisions:
- none

Verification:
- tests run: none for rapidcopy-only candidates; no rapidcopy code was integrated in this pass
- manual checks: reviewed rapidcopy's subject-relevant model/serialize/API deltas against current base and confirmed the remaining items are either already covered or belong to later validation, security, or transfer subjects
- status: reviewed and classified

Notes:
- Subject 11 is complete for the currently reviewed `thejuran` and `rapidcopy` ranges; remaining upstream work in this area was either integrated, consciously skipped, or classified under other subjects.

## Subject 12 - Files Page And File Operations UI

### thejuran

- State: reviewed
- High-risk: no
- Integration base: f38666c
- Source branch: thejuran/master
- Fork tip seen at pass start: a8561cd
- Reviewed in this pass: e839ad8..thejuran/master (subject-relevant files page, file options, file list, and file service commits)
- Last reviewed upstream commit (inclusive): a8561cd
- Resume from next: a8561cd
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- `f38666c` adapted `1ecea11`, `c630cf5`, and `821c730` for persisted files toolbar filters and live status counts in the existing file-options UI
- `f85bad2` adapted the conservative sortable-header slice of rapidcopy `f1fc34c` onto the existing files list with persisted frontend sort methods for name, status, size, speed, and eta
- `df4f2bd` adapted thejuran `df868bc`: add backend bulk command route/helpers plus focused Python handler coverage
- `9c3ad4a` adapted thejuran `df868bc`: add frontend bulk-command transport and POST payload support
- `d97633a` adapted thejuran `df868bc`: wire conservative checkbox selection and bulk actions into the existing files page
- `462a475` adapted thejuran `df868bc`: add Angular unit coverage for bulk selection, bulk command transport, and selection-clearing behavior
- `515437c` adapted thejuran `714dcaf` and prerequisite behavior from `a50a6ec`: align single-file Angular command clients with the POST/DELETE mutation contract required by the integrated bulk-actions work
- `e0b235e` adapted rapidcopy `2a016f9` / `fd5b0ac`: guard the selected-row auto-scroll lifecycle path so the file-row ViewChild is not dereferenced before it exists

Pending:
- none

Covered elsewhere:
- Angular and dependency modernization chain belongs to Subject 3
- security and broad web/API work belongs to Subjects 6 and 11
- any residual file-list interaction churn from `2a016f9` and `fd5b0ac` beyond the landed ViewChild lifecycle guard is too small and optional to keep this subject open after the lifecycle guard landed in `e0b235e`
- thejuran `f9dac34`, `3262cd2`, and `4533679` remain optional follow-up UX and hardening work beyond the conservative bulk-actions import landed for Subject 12

Skipped:
- visual restyle/theme churn is out of scope for this pass
- validation-state and broader frontend modernization chains stay deferred to later subject work
- optional file-page metadata and import-polish cluster from `13d8e96`, `5c3526f`, and `b98b68b` stays out of this conservative Subject 12 close-out

Maintainer decisions:
- none

Verification:
- tests run: `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/integration/test_web/test_handler/test_controller.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_model_builder.py`; `make run-tests-angular`
- manual checks: none
- status: partially verified; focused Python handler tests passed and Dockerized Angular suite passed with 206 tests after the bulk-actions and local-only batches

Notes:
- first Subject 12 implementation batch committed in `f38666c`
- persisted-filter null-default follow-up committed in `8bf456b`
- conservative desktop header sorting committed in `f85bad2`
- the bulk-actions cluster is now integrated in five provenance-preserving local commits instead of one large patch
- the integrated local batches intentionally stop short of thejuran `f9dac34`, `3262cd2`, and `4533679`; keyboard shortcuts, range selection, progress feedback, and later hardening remain optional follow-up work rather than forced imports
- remaining Subject 12 value is now concentrated in optional rapidcopy file-row polish rather than the earlier bulk-actions foundation

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: f38666c
- Source branch: rapidcopy/master
- Fork tip seen at pass start: 6ce7c19
- Reviewed in this pass: e839ad8..rapidcopy/master (subject-relevant files page, file list, and file service commits)
- Last reviewed upstream commit (inclusive): 6ce7c19
- Resume from next: 6ce7c19
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- `f38666c` adapted the low-risk dropdown compatibility portion of `323e3ed`
- `f85bad2` adapted the conservative sortable-header and extended sort-method portion of `f1fc34c` onto the existing files list without pulling in pagination or action-cluster changes
- bulk-actions outcome is now covered elsewhere by the five local/thejuran-based batches in `df4f2bd`, `9c3ad4a`, `d97633a`, `462a475`, and `515437c`
- `e0b235e` adapted the low-risk lifecycle-safety portion of `2a016f9` / `fd5b0ac` so selected-row auto-scroll waits for the file-row element to exist
- `4ae0103` adapted `b62970a`: preserve the downloaded state and file-row hinting for local-only files that have lost their remote counterpart
- `847b3f1` adapted the files-page pagination slice of `ee0718a` without bringing over the unrelated test-config changes from that upstream commit

Pending:
- none

Covered elsewhere:
- broad Angular 18 modernization chain belongs to Subject 3
- multi path-pair support belongs to Subjects 10 and 11
- any residual file-list interaction pieces from `f1fc34c` beyond the conservative sortable-header import are too small and optional to keep Subject 12 open after `f85bad2` and `847b3f1`
- path-pair dashboard cluster from `778d1d8` and `a6a1189` belongs to Subjects 10 and 11
- queue-prioritize cluster from `8d6b436` belongs with transfer and command semantics work rather than this files-page subject

Skipped:
- chunk-validation feature chain in `227b5a3` stays deferred to later backend-heavy subjects
- dark-mode and broader visual-system changes stay out of scope for this pass
- optional file-row UX polish from `671a0c3` and `797ebfa` stays deferred after the conservative pagination, lifecycle, and local-only batches

Maintainer decisions:
- none

Verification:
- tests run: `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/integration/test_web/test_handler/test_controller.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_model_builder.py`; `make run-tests-angular`
- manual checks: none
- status: partially verified; focused Python handler tests passed and Dockerized Angular suite passed with 206 tests after the bulk-actions and local-only batches

Notes:
- first Subject 12 implementation batch committed in `f38666c`
- persisted-filter null-default follow-up committed in `8bf456b`
- the conservative sortable-header slice, lifecycle guard, local-only state fix, and pagination controls are now committed in separate provenance-preserving batches
- rapidcopy-specific follow-up beyond this conservative close-out is optional file-row polish rather than required Subject 12 functionality

## Subject 13 - SSH And Remote Command Handling

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `4d8e22d`
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: `origin/master..a8561cd` for Subject 13 files and related commits
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: next thejuran Subject 13 candidate after `a8561cd`
- New upstream since last pass: none recorded at pass time
- Pass date: 2026-03-09

Integrated:
- adapted `108018f` in `0c88994` to switch the managed `scan_fs` payload and `SystemFile` transport helpers from pickle to JSON
- adapted `abef04a` in `5ad98ce` to decode remote scan results from JSON and update the focused remote-scanner tests
- `bb283e6` is already covered in current master through earlier compatibility updates to config and SSH handling
- SSH host-key hardening from `e34ba5e` is already covered elsewhere by the current `sshcp.py` and Docker SSH defaults
- delete-process shell escaping from `492944f` is already covered elsewhere in current master

Pending:
- none

Covered elsewhere:
- the SSH expect-pattern expansion portion of `a1deb23` is already present in current `src/python/ssh/sshcp.py`

Skipped:
- `a92af56` stays out of Subject 13 because webhook HMAC authentication belongs under web/auth security work
- the `src/python/web/web_app.py` private-attribute rename portion of `a1deb23` is classified out of Subject 13 as web-app/runtime behavior rather than SSH and remote-command handling

Maintainer decisions:
- none

Verification:
- tests run: `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_scan/test_remote_scanner.py`
- manual checks: verified `0c88994` and `5ad98ce` match the intended JSON producer/consumer contract in `SystemFile`, `scan_fs.py`, and `remote_scanner.py`
- status: focused verification passed

Notes:
- the managed first-run install path recopies the local `scanfs` artifact when the remote copy differs, so the clean JSON-only migration does not need the rapidcopy pickle fallback in normal operation

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `4d8e22d`
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `6ce7c19`
- Reviewed in this pass: `origin/master..6ce7c19` for Subject 13 files and related commits
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: next rapidcopy Subject 13 candidate after `6ce7c19`
- New upstream since last pass: none recorded at pass time
- Pass date: 2026-03-09

Integrated:
- recoverable/non-recoverable remote scanner handling from `1bccd13`, `f2d906e`, and `52b9ebd` is already covered in current master
- descriptive SSH errors from `9ed00ca` are already covered in current `sshcp.py`
- Docker SSH wrapper fixes from `d3acc00`, `eec50ec`, and `33d0dae` are already covered elsewhere in current master
- the remote scan transport concern is satisfied by the local JSON migration commits `0c88994` and `5ad98ce`

Pending:
- none

Covered elsewhere:
- none beyond the integrated equivalents listed above

Skipped:
- `5d5a90a` pickle fallback is not selected for the main Subject 13 batch because this repo manages the remote scanfs install and can migrate both ends of the protocol together
- `cb55471` and `207caf5` move to the transfers/config subject because they change LFTP connection limits rather than SSH and remote-command handling
- broader rapidcopy security/auth and path-pair work in `9f91d1c`, `78a3fde`, `32acba6`, `9e1aeea`, `d143638`, and related UI/config commits stays out of this conservative Subject 13 pass

Maintainer decisions:
- none

Verification:
- tests run: `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_scan/test_remote_scanner.py`
- manual checks: confirmed the managed `scanfs` install path keeps the producer and consumer sides aligned, so rapidcopy's fallback is unnecessary for the conservative Subject 13 close-out
- status: focused verification passed

Notes:
- rapidcopy's most relevant Subject 13-only input was the legacy pickle fallback idea, but verification did not expose a compatibility gap after the managed JSON migration

## Subject 14 - Transfers And LFTP

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `7f3afd2`
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: `origin/master..a8561cd` for Subject 14 files and related commits
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: next thejuran Subject 14 candidate after `a8561cd`
- New upstream since last pass: none recorded at pass time
- Pass date: 2026-03-09

Integrated:
- adapted `65dc7fe` as the fourth Subject 14 batch to avoid mutating the persisted downloaded-file set during `clear()`, guard downloaded/deleted checks when no downloaded set is loaded, and only mark remote directories as `DOWNLOADED` when they contain remote leaf files and all such leaves are downloaded
- adapted the `lftp.py` portion of `c52554b` as the sixth Subject 14 batch to guard debug logging when `pexpect.after` is `None`, avoiding secondary `AttributeError` crashes in timeout/error paths

Pending:
- none

Covered elsewhere:
- the timeout aspect of `bdcc287` is intentionally covered by the smaller local `30s` adaptation of rapidcopy `5db8f34` instead of taking thejuran's broader `180s` value
- the handler-method portion of `a50a6ec` is already covered by Subject 12 commit `515437c`
- adapted equivalents of `7897c8e` and `9e84b9e` land in the local controller-containment batch for Subject 14
- the import-status portion of `5b52854` does not apply on this branch because the current base has no `import_status` model field, imported-file persistence, or Sonarr/import-tracking path to extend yet
- the `lftp.py` `pexpect.after is None` guard from `c52554b` is integrated as its own narrow Subject 14 hardening batch; the rest of `c52554b` remains broader bounded-set persistence work

Skipped:
- `4c381e4` is classified out of the first Subject 14 pass because it is broad controller command refactoring rather than a bounded transfer/LFTP fix
- noisy early auto-queue/download-state commits `b9c0612` and `f3af3fb` should be adapted manually if needed rather than cherry-picked with their mixed artifact churn
- the remaining memory-monitor, bounded-queue, and bounded-set persistence work from `b632b05` and `c52554b` is skipped in this conservative Subject 14 pass because it is a broader backend memory-management redesign that touches stream delivery, controller persistence, and new infrastructure beyond the transfer/LFTP hardening already landed
- `c539ed9` is skipped because it is a larger transfer/controller refactor rather than a bounded reliability fix
- the non-handler portions of `a50a6ec` are skipped because they mix broader controller contract changes into an area already partly covered by Subject 12's HTTP-method alignment
- the auto-queue restart and re-queue cluster in `1048d87`, `3b98bd8`, `9e290af`, `f5d4d24`, and `e775d8f` is skipped because it changes queue workflow behavior more than LFTP reliability and is not needed for the conservative transfer hardening goal of this pass

Maintainer decisions:
- none

Verification:
- tests run: `docker compose -f src/docker/test/python/compose.yml run --rm tests python -m pytest -v tests/unittests/test_controller/test_model_builder.py::TestModelBuilder::test_build_children_state_default_remote_dir_without_remote_leaf_files tests/unittests/test_controller/test_model_builder.py::TestModelBuilder::test_clear_does_not_mutate_downloaded_files tests/unittests/test_controller/test_model_builder.py::TestModelBuilder::test_build_state tests/unittests/test_controller/test_model_builder.py::TestModelBuilder::test_build_children_state_downloaded_full tests/unittests/test_controller/test_model_builder.py::TestModelBuilder::test_build_children_state_downloaded_partial tests/unittests/test_controller/test_model_builder.py::TestModelBuilder::test_build_children_state_downloaded_partial_extra`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_lftp/test_lftp.py`
- manual checks: reviewed thejuran transfer/LFTP candidates, kept only the transfer-state correctness subset from `65dc7fe`, left the `5b52854` import-status work out because the required import-tracking model does not exist on this branch, and then extracted only the `lftp.py` `pexpect.after is None` guard from `c52554b` instead of taking the broader bounded-set and memory-monitor work
- status: transfer-state correctness and `pexpect.after` hardening batches verified

Notes:
- first implementation batch should stay limited to `lftp.py`, `job_status_parser.py`, and focused parser tests before broader transfer behavior work is attempted

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `7f3afd2`
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `6ce7c19`
- Reviewed in this pass: `origin/master..6ce7c19` for Subject 14 files and related commits
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: next rapidcopy Subject 14 candidate after `6ce7c19`
- New upstream since last pass: none recorded at pass time
- Pass date: 2026-03-09

Integrated:
- adapted `2238a32`, `3ad06ce`, and `c1e079a` as the first Subject 14 batch to widen the LFTP PTY, skip wrapped `jobs -v` queue fragments safely, and return partial parser results instead of crashing on malformed queue/job output
- adapted `62e14e2` with thejuran-style controller containment from `7897c8e` and `9e84b9e` as the second Subject 14 batch so non-fatal LFTP/parser failures log warnings or fail callbacks instead of crashing controller processing
- adapted `5db8f34` as the third Subject 14 batch to raise the LFTP prompt timeout from 3 seconds to 30 seconds and demote routine timeout noise from exception to debug logging
- adapted `c487178` as the fifth Subject 14 batch to translate unexpected `pexpect` EOFs inside `__run_command()` into `LftpError` so terminated `lftp` sessions follow the existing non-fatal controller containment path
- adapted the download-rate-limit portion of `7f22141` as a later Subject 14 batch so users can set LFTP `net:limit-rate` through the existing config and settings UI without taking the upstream Angular 18 and Docker modernization churn

Pending:
- none

Covered elsewhere:
- the connection-cap backend limit associated with `cb55471` is already enforced in current `Config`; only the user-facing settings copy remains to review later
- parser-related older rapidcopy fixes in `0063f8b`, `ec6c48a`, `bc523d7`, `01430ca`, `481e040`, `902eb15`, `3c21ca3`, and `24e54ff` should be revisited after the first parser batch to avoid over-importing overlapping hardening all at once
- the Angular 18 compatibility, test-mock modernization, and Docker build changes bundled into `7f22141` belong to earlier compatibility/tooling subjects rather than this transfer/LFTP pass

Skipped:
- `d143638` multi-path source/destination pairs is classified out of the initial Subject 14 pass because it is broader path-plumbing work, not a bounded transfer/LFTP fix
- `6d59994` rebrand and unrelated modernization churn are out of scope for Subject 14
- `8d6b436` is skipped because queue prioritization expands queue semantics and API/UI behavior beyond the conservative transfer/LFTP reliability scope
- `6ce7c19` is skipped because staging directories and interrupted-download auto-resume change transfer workflow semantics and on-disk behavior rather than just hardening the existing LFTP path
- validation-heavy transfer work in `e038c21`, `866921b`, `dda1cb2`, `30809bf`, `2614ae6`, `d20b84d`, `4cd7fc1`, `157d003`, `227b5a3`, and `d0662ca` is skipped because it bundles broader contract and behavior changes rather than isolated low-risk fixes
- `207caf5` is skipped because the current settings text already warns that values above 32 are not recommended and that 0 means no limit, so the extra FD-limit rationale does not justify a separate wording-only import here

Maintainer decisions:
- none

Verification:
- tests run: `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_lftp/test_job_status_parser.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_lftp/test_lftp.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests python3 -m pytest /src/tests/unittests/test_controller/test_controller.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_lftp/test_lftp.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_common/test_config.py tests/unittests/test_controller/test_controller.py`; `make run-tests-angular`
- manual checks: reviewed rapidcopy transfer/LFTP candidates, normalized worker line endings, tightened the wrapped-queue handling so the parser hardening does not regress quoted-path coverage, kept the controller containment batch scoped to warning/callback handling rather than broader EOF semantics, chose the conservative rapidcopy-style 30 second timeout instead of thejuran's broader 180 second wait, adapted the narrow EOF-to-`LftpError` handling only in `lftp.py` without taking the upstream settings/default-connection changes, and then extracted only the download-rate-limit feature from `7f22141` while leaving its unrelated Angular 18 and Docker modernization out of Subject 14
- status: parser-hardening, controller-containment, timeout-tuning, EOF-containment, and rate-limit batches verified

Notes:
- start with the parser-hardening batch, then verify focused LFTP tests before moving to timeout tuning or controller containment

## Subject 15 - Scanning

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `master` (`dc7de7f`)
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: `origin/master..a8561cd` for Subject 15 files and related commits
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: next thejuran Subject 15 candidate after `a8561cd`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- none

Pending:
- none

Covered elsewhere:
- `108018f` and `abef04a` are already satisfied by Subject 13's local JSON scan transport migration (`0c88994`, `5ad98ce`)
- `de7dde0` is already present in the local history as the existing remote `scanfs` auto-install path
- `06dd9fd` is already present in the local history as existing remote-scanner non-recoverable error handling

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: confirmed current `scan_fs.py` emits JSON, current `remote_scanner.py` consumes JSON, and the managed remote `scanfs` install path already matches the reviewed thejuran scanner transport direction
- status: review only; no new code landed

Notes:
- this pass found no remaining thejuran-specific Subject 15 implementation gap beyond behavior already integrated under Subject 13 or existing base history

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `master` (`dc7de7f`)
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `6ce7c19`
- Reviewed in this pass: `origin/master..6ce7c19` for Subject 15 files and related commits
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: next rapidcopy Subject 15 candidate after `6ce7c19`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- adapted a narrow `d143638` groundwork slice to add path-pair metadata fields on `SystemFile` and `ModelFile`, helper multi-path scanner classes, and model serialization support without enabling controller/runtime multi-path behavior yet
- added a follow-up backend identity groundwork slice after `b403384` so `Model`, `ModelDiff`, and `ModelBuilder` can represent duplicate top-level names across different path pairs safely before command/runtime wiring begins
- added an additive command-identity contract batch so web handlers, Angular model/view plumbing, bulk selection, and bulk requests can carry hidden `file_id` values while preserving current display names and legacy unambiguous filename routes
- adapted the backend/runtime portion of `d143638`, `1690826`, and `981d707` so controller scan wiring, active download scanning, lftp job identity, and per-path-pair queue/stop/delete handling are now safe for duplicate top-level names across enabled path pairs
- adapted `9d58f10` into a standalone local integration module so multi-path controller scanning, queueing, and delete operations are verified without touching the dirty legacy integration test file
- added a local Angular follow-up so `path_pair_name` reaches `ViewFile` and the files list renders a small source label, keeping duplicate top-level names distinguishable after multi-path runtime enablement

Pending:
- none

Covered elsewhere:
- `6f4e2ac` is already present in the local history as the remote scanfs directory-path fix
- `aeb27fa` is already satisfied by Subject 13's local JSON scan transport migration (`0c88994`, `5ad98ce`)
- `de964a1`, `1bccd13`, `f2d906e`, `20a2ade`, and `52b9ebd` already have equivalent local history in `b962d1a`, `9c05838`, `06dd9fd`, `ab30dde`, and `cbcc9f6`
- `24e54ff` already has an equivalent local history entry in `1140f21`

Skipped:
- `5d5a90a` is not selected for this conservative repo because it reintroduces unsafe pickle deserialization into remote scan transport after Subject 13 intentionally migrated the protocol to managed JSON-only `scanfs` installs
- `227b5a3`, `2614ae6`, `d0662ca`, `dda1cb2`, `30809bf`, `866921b`, `4527bfe`, and the validation/settings/test portions of `fc57113` are tracked out of Subject 15 because they belong primarily to later validation-focused work rather than core scanning
- `9e1aeea` and `32acba6` are tracked out of Subject 15 because their scanner-adjacent pieces belong primarily to Subject 6 security hardening rather than scan-behavior integration
- `0b49f97` is out of scope for Subject 15 because network mount support is broader settings/runtime infrastructure, not scanner correctness
- `79f7cab` is out of scope for Subject 15 because it is a validation-process file plus mixed follow-up bundle, not a bounded scanning change
- the settings/API portion of `d143638` is reclassified out of Subject 15 because path-pair persistence already exists locally and the remaining subject-critical work is runtime consumption plus focused tests, not CRUD settings surfaces
- `a33981b` is reclassified out of Subject 15 because its validation warnings and Docker-path guidance belong primarily to later settings/config UX work rather than scanner correctness
- `58c588b` is reclassified out of Subject 15 because allowing `/mounts` in Docker is a later packaging/runtime-path policy decision, not a scanner-correctness blocker

Maintainer decisions:
- none

Verification:
- tests run: `python3 -m py_compile src/python/common/path_pair.py src/python/common/context.py src/python/common/__init__.py src/python/seedsync.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_seedsync.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests python - <<'PY' ... PathPairManager migration smoke test ... PY`; `python3 -m py_compile src/python/controller/controller.py src/python/controller/model_builder.py src/python/controller/scan/__init__.py src/python/controller/scan/multi_path_active_scanner.py src/python/lftp/job_status.py src/python/lftp/job_status_parser.py src/python/lftp/lftp.py src/python/tests/unittests/test_controller/test_controller.py src/python/tests/unittests/test_controller/test_scan/test_multi_path_active_scanner.py src/python/tests/unittests/test_lftp/test_job_status.py src/python/tests/unittests/test_lftp/test_job_status_parser.py src/python/tests/unittests/test_lftp/test_lftp.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_controller.py tests/unittests/test_controller/test_scan/test_multi_path_active_scanner.py tests/unittests/test_lftp/test_job_status.py tests/unittests/test_lftp/test_job_status_parser.py tests/unittests/test_lftp/test_lftp.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/integration/test_controller/test_controller_multi_path.py`; `make run-tests-angular`
- manual checks: reviewed rapidcopy scanner and path-pair candidates against current master, confirmed the JSON-only remote scan protocol is already present locally, confirmed the legacy pickle fallback was later removed upstream as a security hardening step, separated validation-heavy or network-mount work out of this scanning subject, smoke-tested `PathPairManager` load plus legacy-config migration in the docker test container, narrowed the next backend batch to additive model/file identity groundwork after review rejected earlier runtime enablement as unsafe, verified the backend runtime follow-up against the worker patch before recording it, and then confirmed the standalone controller integration module and Angular path-pair label follow-up against local test runs
- status: foundation batch verified except for an existing `tests/unittests/test_seedsync.py::TestSeedsync::test_default_config` failure on `Lftp.rate_limit` default initialization that is outside this batch's file scope; the later backend runtime/path-pair follow-up passed the targeted unit suite with `97 passed`, the standalone multi-path controller integration module passed with `2 passed`, and `make run-tests-angular` passed with `210 tests completed`

Notes:
- the Subject 15 multi-path/path-pair scanning stack was intentionally landed in multiple small commits rather than a single bulk import so controller identity, runtime behavior, integration coverage, and UI disambiguation stayed reviewable
- the first implementation batch is the path-pair persistence, migration, and context foundation adapted from `d143638`; later Subject 15 batches added controller/scanner behavior, active-scan routing, integration tests, and a minimal UI disambiguation label on top
- a broader runtime-enablement attempt was intentionally rejected after review because duplicate top-level names across path pairs still collide in model/controller identity; this follow-up batch keeps only metadata and helper groundwork, with no controller/runtime behavior change
- the current backend identity slice is intended to keep duplicate-name handling additive and internal for now: model storage, diffing, and SSE identity become path-pair-aware before command contracts or controller runtime behavior change
- this command-contract follow-up keeps the visible filename UI unchanged for now; the hidden `file_id` path is threaded through services and handlers first, while duplicate-name rendering nuances in the existing template remain deferred until the broader multi-path runtime/UI enablement batch
- settings/API CRUD work for path pairs is not treated as a Subject 15 blocker here because path-pair persistence is already loaded at startup; the remaining subject-critical work is runtime consumption plus focused tests and UI disambiguation

## Subject 16 - Auto Queue

### thejuran

- State: reviewed
- High-risk: no
- Integration base: `master` (`4dc96ac`)
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: `origin/master..a8561cd` for Subject 16 files and related commits
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: next thejuran Subject 16 candidate after `a8561cd`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- adapted `3b98bd8` and `a1b467e` as `907e1ee` so auto-queue now separates new files from modified files, treats remote discovery (`None -> value`) differently from true remote updates, and uses hidden `file_id` command identity to stay safe with Subject 15 path-pair duplicates
- adapted `9e290af` and `f5d4d24` as `a0f86f5` so explicit STOP and DELETE_LOCAL actions persist across restarts via `stopped_file_names`, manual QUEUE clears that stopped state again, and the persist format remains backward-compatible when older controller state lacks the new key
- adapted `2323761` as `555d1d1` so `Model` and `AutoQueuePersist` listener notification uses copy-under-lock handling instead of iterating a listener list that may be modified concurrently
- adapted `7631bfb` as `6a30ae4` so `AutoQueueService.remove()` re-reads the live pattern list before removing an item and safely no-ops if the target pattern is already gone when the response returns

Pending:
- none

Covered elsewhere:
- `88d96a1` overlaps with existing handler semantics work already landed in `d0b9195`
- `e775d8f` is already satisfied locally because current `ModelBuilder` marks missing-but-previously-downloaded files as `DELETED` using `file_id`-aware persisted state, so they no longer pass the auto-queue `DEFAULT`-state filter
- `b1b7ec9` is already effectively covered locally because the AutoQueue page already unsubscribes with `takeUntil`, so its lifecycle fix was not needed as a separate Subject 16 batch

Skipped:
- `b9c0612` is not needed in this branch because its bug depends on thejuran's bounded LRU downloaded tracker; local `downloaded_file_names` remains an unbounded set, so there is no equivalent eviction path to harden here
- the shared stream reconnect timer cleanup portion of `7631bfb` is tracked out of Subject 16 because it belongs to shared stream infrastructure rather than auto-queue feature behavior

Maintainer decisions:
- none

Verification:
- tests run: `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_auto_queue.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_auto_queue.py tests/unittests/test_controller/test_controller.py tests/unittests/test_controller/test_controller_persist.py`; `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_auto_queue.py tests/unittests/test_model/test_model.py tests/unittests/test_model/test_diff.py`; `make run-tests-angular`
- manual checks: reviewed thejuran auto-queue runtime, controller persist, and test candidates against current local Subject 15 `file_id` / path-pair-aware controller state; confirmed the most important gap was restart/requeue correctness, then reviewed the landed `907e1ee`, `a0f86f5`, `555d1d1`, and `6a30ae4` batches against the upstream clusters, re-checked the remaining `e775d8f`, `b9c0612`, and `b1b7ec9` candidates against current local behavior, and used `git diff --ignore-cr-at-eol` to separate the Angular stale-index fix from workspace CRLF noise
- status: Subject 16 backend/runtime, listener hardening, and Angular stale-index handling landed; the focused suites passed with `53 passed`, `67 passed`, and `77 passed`, and Angular passed with `211 tests completed`

Notes:
- thejuran carries the substantive Subject 16 runtime fixes in this pass; the local adaptation was made `file_id`-safe because Subject 15 made duplicate visible filenames valid across path pairs while `AutoQueue` previously deduplicated by visible name
- Subject 16 is complete for this pass: both forks were reviewed, the substantive thejuran runtime fixes landed in four backend commits plus one narrow Angular follow-up, and the remaining reviewed candidates were either covered elsewhere or consciously skipped with repo-specific reasons

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: `master` (`4dc96ac`)
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `6ce7c19`
- Reviewed in this pass: `origin/master..6ce7c19` for Subject 16 files and related commits
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: next rapidcopy Subject 16 candidate after `6ce7c19`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- the auto-queue-touched security hardening cluster `9f91d1c`, `32acba6`, `78a3fde`, and `9e1aeea` is tracked primarily under Subject 6 rather than Subject 16
- the mechanical cleanup cluster `d87f403`, `1131714`, `677be93`, and `94c0172` is not selected for this conservative pass because it is mostly style/type churn without bounded auto-queue behavior value
- the Angular modernization cluster `93e10ab`, `e0985b2`, and `5e8bf5e` is not selected for this conservative pass because it is framework-era cleanup rather than a narrow auto-queue fix
- the Playwright additions `5df693d`, `696866c`, `2ed36f2`, and `1700fcc` are verification assets that can be revisited later under test/e2e work, not core Subject 16 behavior blockers
- `6d2627e` is a small integration-test maintenance follow-up that can be revisited after the main thejuran backend fixes if later handler verification shows a real gap

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: reviewed rapidcopy auto-queue commit surface against the current local backend and found no substantial new runtime semantics beyond the thejuran restart/requeue work; the relevant rapidcopy items in this range are mostly hardening, cleanup, modernization, or secondary verification assets
- status: review only; no new code landed

Notes:
- rapidcopy is low-coverage for core Subject 16 behavior in this pass; the real implementation work is expected to come from thejuran with local adaptation for current `file_id` identity

## Subject 17 - Extraction And Archive Handling

### thejuran

- State: reviewed
- High-risk: no
- Integration base: master (`c9e17d3`)
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: `origin/master..a8561cd` for Subject 17 files and related commits
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: next thejuran Subject 17 candidate after `a8561cd`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- `08743df` adapted from `1aae411` to stabilize extract integration coverage without adding checked-in archive fixtures; it makes extract archive creation synchronous, aligns the Python test image with RAR codec support, and fixes the focused re-extract assertions to search callback history instead of only the most recent update
- `37c3a06` adapted from `784e1ff`, `713825d`, and `5e2a62c` to make `ExtractDispatch` queue/listener handling thread-safe and add focused dispatcher race coverage

Pending:
- none

Covered elsewhere:
- the older extraction feature stack already exists in current base history, so no new import was needed for `542f0b5`, `d9bdf89`, `46f930e`, `1c5fa63`, `44f113f`, `3ad020c`, `ca8d70d`, `a01579e`, `ff1e79d`, `fd4e0fc`, `aa9ca32`, `0dc6454`, `7aa2684`, and `05c3de1`
- `747cb82` runtime RAR support was already covered by the current Docker image, and the remaining test-image alignment landed locally in `08743df`

Skipped:
- `84f6473` skips an extract overwrite assertion instead of fixing behavior; the focused extract suite passed after the stable-test fixes, so reducing coverage was not necessary

Maintainer decisions:
- none

Verification:
- tests run:
  - `docker compose -f src/docker/test/python/compose.yml build`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_extract/test_dispatch.py tests/unittests/test_controller/test_extract/test_extract_process.py tests/integration/test_controller/test_extract/test_extract.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_file tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_directory tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_directory_multilevel tests/integration/test_controller/test_controller.py::TestController::test_command_extract_local_directory tests/integration/test_controller/test_controller.py::TestController::test_command_reextract_after_extracting_remote_file tests/integration/test_controller/test_controller.py::TestController::test_command_extract_remote_only_fails tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_directory_to_separate_path`
- manual checks: reviewed the committed diffs with `git diff --ignore-cr-at-eol` because raw stats in this Windows-backed workspace were inflated by line-ending churn
- status: verified; the extract-focused suite passed with `46 passed`, and the focused controller extract nodes passed with `7 passed`

Notes:
- Subject 17 is complete for this pass: thejuran supplied the substantive reliability fixes, while the older extraction feature history was already present in the current base and only needed selective stabilization plus dispatcher hardening

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: master (`c9e17d3`)
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `6ce7c19`
- Reviewed in this pass: `origin/master..6ce7c19` for Subject 17 files and related commits
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: next rapidcopy Subject 17 candidate after `6ce7c19`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- `08743df` takes the stable-test intent from `fc57113` for extraction coverage, but keeps runtime-generated archives instead of adding checked-in binary RAR fixtures and leaves the broader validation/UI changes out of Subject 17

Pending:
- none

Covered elsewhere:
- `40165b0` broken RAR runtime support was already covered by the current Docker image with non-free apt sources, `p7zip-rar`, and the `Rar29.so` codec alias

Skipped:
- `32acba6` post-extraction path-walk detection was reviewed but not integrated because it only catches some escaped outputs after extraction and would overstate protection if presented as full zip-slip prevention; its `path_pairs` local-path restriction is also outside this base and outside Subject 17 scope
- `79f7cab` validation process work belongs to validation/download integrity rather than extraction handling
- `d87f403`, `561bf5a`, `677be93`, and `1131714` were style/refactor-only for this subject

Maintainer decisions:
- none

Verification:
- tests run:
  - `docker compose -f src/docker/test/python/compose.yml build`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_extract/test_dispatch.py tests/unittests/test_controller/test_extract/test_extract_process.py tests/integration/test_controller/test_extract/test_extract.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_file tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_directory tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_directory_multilevel tests/integration/test_controller/test_controller.py::TestController::test_command_extract_local_directory tests/integration/test_controller/test_controller.py::TestController::test_command_reextract_after_extracting_remote_file tests/integration/test_controller/test_controller.py::TestController::test_command_extract_remote_only_fails tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_directory_to_separate_path`
- manual checks: security review concluded that the `32acba6` extract-side hardening is only partial post-extraction detection and should not be represented as full zip-slip protection in this pass
- status: verified; the rapidcopy-reviewed extraction candidates were either accounted for by existing local behavior, adapted test stabilization, or consciously skipped with subject-specific reasons

Notes:
- rapidcopy contributed a useful testing direction for Subject 17, but its extraction-safety commit mixed a limited post-extraction check with out-of-scope path-pair policy, so this pass kept the narrower stable-test value and left the broader hardening out

## Subject 18 - Core Controller

### thejuran

- State: reviewed
- High-risk: no
- Integration base: master (`335d7df`)
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: `origin/master..a8561cd` for Subject 18 files and related commits
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: next thejuran Subject 18 candidate after `a8561cd`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- `988fed0` adapted from `d2e4bef` to guard `ModelBuilder` ETA estimation when `remote_size` is still `None`
- `f0f62ba` adapted from `cbec564` and `4f58c8f` to use exception-safe model locking in controller access/update paths and include caught LFTP error text in queue/stop failures
- `257d064` adapted from `88d96a1` to propagate controller command failure status codes through the web callback path so single-file command endpoints can return `404`, `409`, or `500`
- `2410a7d` adapted from the `ControllerJob` test slice of `e9ac251` without taking the broader refactor-coupled controller unit suite

Pending:
- none

Covered elsewhere:
- `7897c8e` and `9e84b9e` were already adapted in Subject 14's controller-containment batch
- `65dc7fe` downloaded-state/model-builder correctness was already adapted in Subject 14
- `3b98bd8`, `a1b467e`, `9e290af`, `f5d4d24`, `e775d8f`, and related auto-queue restart/requeue fixes were already handled in Subject 16
- the handler-method portion of `a50a6ec` was already handled in Subject 12; this pass only took the later callback/status-code slice from `88d96a1`

Skipped:
- `48f9a68`, `8daf221`, `5b52854`, `6420549`, `cd8d78a`, `4a83863`, `5210436`, `f5e5487`, `b356607`, and `c8f01c5` were left out because they depend on import/webhook/auto-delete feature surfaces that do not exist in current base and would broaden this conservative pass beyond core controller fixes
- `4c381e4`, `1bd91fc`, `2f914d4`, `2bc18bd`, `c539ed9`, `57f460b`, `2323761`, `b632b05`, and the remaining `c52554b` work were skipped because they are larger refactors or infrastructure redesigns rather than bounded controller correctness fixes for this pass
- the main `494ff3d` and `e9ac251` controller unit-suite expansions were skipped because they target thejuran's later manager/refactor architecture rather than the current local controller shape

Maintainer decisions:
- none

Verification:
- tests run:
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_model_builder.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_controller.py tests/unittests/test_controller/test_model_builder.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_controller.py tests/integration/test_web/test_handler/test_controller.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_controller_job.py`
- manual checks: reviewed worker diffs with `git diff --ignore-cr-at-eol`, re-checked prior tracker deferrals so the status-code slice only covered the controller callback path from `88d96a1`, and confirmed the existing remote-scan error surfacing from rapidcopy `de964a1` is already present locally
- status: verified; the targeted controller/model suite passed with `50 passed`, then `59 passed`, the controller + handler status-code checks passed with `26 passed`, and `ControllerJob` lifecycle coverage passed with `3 passed`

Notes:
- Subject 18 stayed deliberately narrow in this pass: controller/model crash proofing, exception-safe locking, callback status-code propagation, and a small `ControllerJob` test follow-up landed, while feature-heavy import/webhook, validation, workflow, and refactor work was consciously left out

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: master (`335d7df`)
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `6ce7c19`
- Reviewed in this pass: `origin/master..6ce7c19` for Subject 18 files and related commits
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: next rapidcopy Subject 18 candidate after `6ce7c19`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- none

Pending:
- none

Covered elsewhere:
- `d143638`, `1690826`, `981d707`, and `9d58f10` were already adapted in Subject 15's path-pair and multi-path controller work
- `62e14e2` was already adapted in Subject 14's controller/LFTP containment batch
- the rate-limit portion of `7f22141` was already adapted in Subject 14
- `de964a1` remote scan error surfacing is already present locally in current status/controller/serialize code

Skipped:
- `8d6b436` and `6ce7c19` change queue or transfer workflow semantics and are better considered with broader workflow/transfer subjects, not this conservative controller pass
- the validation stack in `227b5a3`, `866921b`, `d0662ca`, `dda1cb2`, `30809bf`, `e038c21`, `25145f6`, `2614ae6`, `4cd7fc1`, `d20b84d`, and `4527bfe` was skipped because it is a large feature surface spanning backend, settings, UI, and packaging rather than a conservative controller-core fix
- `1bccd13`, `f2d906e`, `52b9ebd`, and `5d5a90a` stay with scanning or remote-scanner compatibility rather than this controller pass
- `93e10ab`, `5df693d`, `d87f403`, `1131714`, `d458abd`, `677be93`, `561bf5a`, and `a9320fd` are modernization, CI, or cleanup-only for this subject

Maintainer decisions:
- none

Verification:
- tests run:
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_model_builder.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_controller.py tests/unittests/test_controller/test_model_builder.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_controller.py tests/integration/test_web/test_handler/test_controller.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_controller_job.py`
- manual checks: compared rapidcopy controller candidates against the now-finished local Subject 18 batches, confirmed that the useful error/status surfacing is already present locally, and kept the validation and workflow-changing features out of this conservative pass
- status: verified; the rapidcopy-reviewed controller candidates were either already covered by earlier subjects or consciously skipped for subject-specific reasons while the final local Subject 18 verification passed

Notes:
- rapidcopy did not supply additional conservative controller-core work beyond items already present locally or better classified under other subjects

## Subject 19 - Dashboard And Main Layout UI

### thejuran

- State: reviewed
- High-risk: no
- Integration base: master (`7641d16`)
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: `origin/master..a8561cd` for Subject 19 files and related commits
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: next thejuran Subject 19 candidate after `a8561cd`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- none

Pending:
- none

Covered elsewhere:
- `8c4edb2` and `67179ea` are already present locally in `AppComponent` through the existing `ResizeObserver` header sizing and `takeUntil` cleanup path
- `721e694` Safari toolbar tint protection is already present locally in `app.component.scss`

Skipped:
- the icon-rail series in `f64325e`, `9dd1d79`, and `7015357` was skipped for this conservative pass because it is a deliberate desktop layout redesign rather than a narrow dashboard or main-layout fix
- `a32dfad` and `96f7d86` were skipped because the sidebar version footer and prompt-indicator styling depend on the icon-rail redesign and duplicate version information already shown on the About page
- `6bf18e7` was skipped because it introduces a separate toast-container UI path and broader notification architecture that is not present in the current base

Maintainer decisions:
- none

Verification:
- tests run:
  - `make run-tests-angular`
- manual checks: confirmed the useful `ResizeObserver`, subscription-cleanup, remote-error notification, unicode e2e, and Safari toolbar-tint fixes from thejuran's dashboard or main-layout area are already present locally and kept the remaining icon-rail and toast-container redesign work out of this conservative pass
- status: verified; the Angular suite passed with `211 tests completed`

Notes:
- thejuran's Subject 19 candidates were either already covered locally or broadened into optional layout or notification redesign work that did not fit this conservative pass

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: master (`7641d16`)
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `6ce7c19`
- Reviewed in this pass: `origin/master..6ce7c19` for Subject 19 files and related commits
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: next rapidcopy Subject 19 candidate after `6ce7c19`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- `bc32e6b` adapted from `a33981b` to add sidebar restart progress, success, and failure feedback using the current `NotificationService` and the existing `WebReaction` flow without taking rapidcopy's broader settings and validation changes

Pending:
- none

Covered elsewhere:
- `757da15` remote-server-error notification handling is already present locally in `header.component.ts`
- `5a195f2` unicode dashboard e2e expectations are already present locally

Skipped:
- `fb4e7db` was skipped because the dark-mode toggle and theme system span multiple pages and shared services, making it a broader optional UI feature rather than a conservative main-layout fix
- `93e10ab` was skipped because it is a broad modernization and compatibility batch with main-layout overlap, not a narrow Subject 19 change
- `08d714e` and `6d59994` were skipped because they are branding changes that conflict with the repo's maintained SeedSync identity

Maintainer decisions:
- none

Verification:
- tests run:
  - `make run-tests-angular`
- manual checks: reviewed the rapidcopy sidebar idea against the local `RestService` implementation, confirmed restart still returns an immediate backend success response, and isolated a legacy Karma harness failure triggered by an attempted new sidebar component spec before keeping the runtime change and full-suite verification
- status: verified; the Angular suite passed with `211 tests completed`

Notes:
- rapidcopy supplied one narrow main-layout improvement worth adapting locally; the rest of the reviewed layout-adjacent work was either already present or intentionally broader than this pass, so Subject 19 closes as a small review-state commit plus one focused sidebar feedback batch

## Subject 20 - Cleanup, Deletion, And File Safety

### thejuran

- State: reviewed
- High-risk: yes
- Integration base: master (`2f6dc01`)
- Source branch: thejuran/master
- Fork tip seen at pass start: `a8561cd`
- Reviewed in this pass: `origin/master..a8561cd` for delete, cleanup, and file-safety paths
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: next thejuran Subject 20 candidate after `a8561cd`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- `4606c90` adapted the focused test intent from `492944f` into the current local test layout so `DeleteRemoteProcess` shell quoting and `DeleteLocalProcess` directory cleanup behavior are explicitly covered under the existing backend runtime

Pending:
- none

Covered elsewhere:
- `ae151c8` and `81702da` are already present locally through the current controller delete actions, web-handler wiring, stronger HTTP-method semantics, and `file_id`/path-pair-aware command routing
- `f8c00fc` and `492944f` are already present locally in runtime behavior because `DeleteRemoteProcess` now uses `shlex.quote`
- `fa9ba6f` is already present locally because local directory deletion already uses `shutil.rmtree(..., ignore_errors=True)`
- `f3af3fb` is already covered by current local behavior because this base no longer prunes `downloaded_file_names`, so externally deleted files stay tracked and are not re-downloaded automatically

Skipped:
- `c8f01c5`, `a4faeef`, `6420549`, `5b52854`, `b98b68b`, `50cb979`, `13d8e96`, `cd8d78a`, `4a83863`, `5210436`, and related import/webhook or auto-delete work were skipped for this subject because the current base does not carry thejuran's Sonarr/Webhook import architecture and a partial import would broaden this conservative pass beyond deletion safety
- `b9c0612`, `e775d8f`, and `f5e5487` were classified under prior auto-queue/controller subjects and are already handled or intentionally resolved elsewhere in current master

Maintainer decisions:
- none

Verification:
- tests run:
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_delete_process.py`
- manual checks: compared thejuran delete-path commits against current `Controller`, delete-process, and web-handler code; confirmed that the runtime safety behavior is already present locally and then closed the remaining gap with focused delete-process coverage in the current test layout
- status: verified; the focused delete-process suite passed with `4 passed`

Notes:
- thejuran's direct delete-command work was already present locally; this pass only needed the narrow coverage follow-up for the current delete-process safety behavior

### rapidcopy

- State: reviewed
- High-risk: yes
- Integration base: master (`2f6dc01`)
- Source branch: rapidcopy/master
- Fork tip seen at pass start: `6ce7c19`
- Reviewed in this pass: `origin/master..6ce7c19` for delete, cleanup, and file-safety paths
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: next rapidcopy Subject 20 candidate after `6ce7c19`
- New upstream since last pass: none recorded
- Pass date: 2026-03-09

Integrated:
- `07da49b` adapted the configuration and scanner foundation from `6ce7c19` so staging paths are supported in config/defaults and `LocalScanner` can merge staged in-progress files with the final local directory without surfacing the nested `incomplete` directory as a user file
- `e20cac4` adapted the controller/runtime portion of `6ce7c19` so LFTP downloads and active scans use staging paths, completed downloads move into the final local directory only after completion, and interrupted staged transfers are re-queued safely after the first successful remote scan in both single-path and path-pair modes
- `f2acdcf` followed up the staged-transfer recovery path so files explicitly stopped by the user are not re-queued on restart, including duplicate visible names across different path pairs
- local follow-up commit exposes `lftp.staging_path` in the current Angular settings UI and config record so the already integrated staging-path runtime can be configured from the existing settings page, with focused Angular settings tests updated accordingly

Pending:
- `c300b72f`: reopen Subject 20 to make `DELETE_LOCAL` fall back to the staging path when the file has not yet moved into the final local path.

Covered elsewhere:
- `677be93` is cleanup-only and offers no functional delete or file-safety change over current local code

Skipped:
- `d20b84d` was skipped because it depends on rapidcopy's validation-process architecture, which is not present in the current base
- `0c73e23` was skipped because it is a permissions note rather than a direct cleanup, deletion, or file-safety change for this base
- the broader security/UI/config portions bundled around `32acba6`, `78a3fde`, `227b5a3`, `866921b`, `d0662ca`, `dda1cb2`, `30809bf`, `e038c21`, `25145f6`, `4cd7fc1`, and related validation or control-plane work are intentionally left to their primary subjects instead of being half-merged here

Maintainer decisions:
- none

Verification:
- tests run:
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_common/test_config.py tests/unittests/test_controller/test_scan/test_local_scanner.py tests/unittests/test_controller/test_delete_process.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/unittests/test_controller/test_controller.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/integration/test_controller/test_controller_multi_path.py`
  - `docker compose -f src/docker/test/python/compose.yml run --rm tests pytest -q tests/integration/test_controller/test_controller.py::TestController::test_command_queue_file tests/integration/test_controller/test_controller.py::TestController::test_command_queue_directory tests/integration/test_controller/test_controller.py::TestController::test_persist_downloaded tests/integration/test_controller/test_controller.py::TestController::test_command_extract_after_downloading_remote_file`
  - `make run-tests-angular`
- manual checks: inspected `6ce7c19` against the current local controller, scanner, config, and settings layout; kept the staging-path safety core, adapted it to the repo's current path-pair-aware controller API, added the explicit-stop recovery guard required by existing stopped-file persistence semantics, and left rapidcopy's broader validation and control-plane work out of this subject
- status: verified; the focused config/scanner/delete suite passed with `23 passed`, the focused controller suite passed with `18 passed`, the multi-path integration suite passed with `2 passed`, and the targeted single-path controller integration selection passed with `4 passed`

Notes:
- rapidcopy supplied the main new Subject 20 feature in this pass: staging/incomplete-download handling to keep partial files out of the final downloads directory until completion, with the adaptation kept path-pair-safe for the current local base and aligned with the repo's existing explicit-stop semantics

## Subject 21 - Cross-Cutting UX Or Workflow Conflicts

### thejuran

- State: reviewed
- High-risk: no
- Integration base: master @ 242f1345199ef377100dcbdba167a750e1938c85
- Source branch: thejuran/master
- Fork tip seen at pass start: a8561cdc318460de32de082e3cf33f6b6a0093cb
- Reviewed in this pass: origin/master..thejuran/master (Subject 21 filtered)
- Last reviewed upstream commit (inclusive): a8561cdc318460de32de082e3cf33f6b6a0093cb
- Resume from next: none at current tip
- New upstream since last pass: none
- Pass date: 2026-03-10

Integrated:
- adapted from `0b26f0ad` and `a48763dd` -> working tree: guard the dashboard filename column against medium-width squeeze without importing thejuran's broader timestamp and terminal-style layout changes
- adapted from `9365743d`, `31889adf`, and `52b72a6c` -> working tree: add keyboard focus trapping and focus restoration to the existing `ngx-modialog` confirm flow through a local modal accessibility helper instead of importing thejuran's custom modal service

Pending:
- none

Covered elsewhere:
- `821c730b` and `c630cf5c` style status-count affordances are already present locally in the current file-options filter UI
- current master already carries the light-theme default shell and original SeedSync status iconography, so Subject 21 does not need a compensating re-import from thejuran to preserve identity

Skipped:
- `ef728cc2`, `6865ea03`, `29e7d5d0`, `42d75b03`, `0bdeef59`, `b7fdff1a`, `d8570982`, and `12a05c86` are not being taken as defaults because the terminal presentation suite is weakly faithful to original SeedSync and would change the product's visual identity
- `72699bdf`, `93f1a0f6`, and `b9c232c7` were rejected because the dashboard status-dot, ASCII-progress, and ghost-button restyle is flavor rather than a faithful default improvement

Maintainer decisions:
- none

Verification:
- tests run:
  - `make run-tests-angular`
- manual checks: reviewed the surviving thejuran Subject 21 UI diffs against current master, kept the original SeedSync default shell and status presentation, adapted the medium-width filename readability fix, and adapted focus trap / focus restoration onto the existing confirm modal path
- status: partially verified; Angular suite passed with `211 tests completed`

Notes:
- evaluation details: [S21-user-facing-conflicts.md](/mnt/c/Git/seedsync/doc/integration-notes/S21-user-facing-conflicts.md)
- Candidate: dashboard filename width guard
  - Value: accessibility / readability / clarity improvement
  - Faithfulness: A
  - Scope: local
  - Gateability: cleanly isolatable
  - Action: default
  - Maintainer input: no
  - Rationale: keeps filenames readable on medium-width dashboards without changing the default SeedSync layout model
- Candidate: terminal presentation suite
  - Value: mostly taste / style / flavor
  - Faithfulness: D
  - Scope: global
  - Gateability: somewhat messy
  - Action: reject for now; defer to future visualization-settings subject
  - Maintainer input: yes
  - Rationale: the maintainer wants broader theming and visualization settings later, but only after the default stays clearly SeedSync-faithful; this suite should be revisited as part of a dedicated optional visual-mode project, not Subject 21
- Candidate: status-dot / ASCII-progress / ghost-button dashboard visuals
  - Value: mostly taste / style / flavor
  - Faithfulness: D
  - Scope: sectional
  - Gateability: cleanly isolatable
  - Action: reject
  - Maintainer input: no
  - Rationale: replaces original SeedSync dashboard styling with fork personality rather than faithful refinement
- Candidate: confirm modal accessibility hardening
  - Value: correctness / bug fix
  - Faithfulness: B
  - Scope: sectional
  - Gateability: somewhat messy
  - Action: adapt
  - Maintainer input: no
  - Rationale: Subject 21 keeps the current modal stack and adapts only the missing focus trap and focus restoration behavior
- deferred future work: see [S21-user-facing-conflicts.md](/mnt/c/Git/seedsync/doc/integration-notes/S21-user-facing-conflicts.md) for the planned follow-up on global theming and visualization settings after the faithful default baseline is complete

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: master @ 242f1345199ef377100dcbdba167a750e1938c85
- Source branch: rapidcopy/master
- Fork tip seen at pass start: c300b72f808772b00cc977ccceaa23f3c373ce33
- Reviewed in this pass: origin/master..rapidcopy/master (Subject 21 filtered)
- Last reviewed upstream commit (inclusive): c300b72f808772b00cc977ccceaa23f3c373ce33
- Resume from next: none at current tip
- New upstream since last pass: none
- Pass date: 2026-03-10

Integrated:
- none

Pending:
- none

Covered elsewhere:
- `ee0718ab`, `f1fc34ca`, `ea4ae40f`, `821c730b`, and `c630cf5c` are already present locally through the current dashboard pagination, sorting, bulk-selection, and status-count work
- `d4e4b7e0` is already covered in the current logs page by the existing live-record cap; the remaining rapidcopy behavior assumes a log search/filter model that current master does not carry
- `d1436386`, `0b49f975`, `fc571139`, `936ae4b2`, and `9f91d1c4` belong to their primary feature or backend subjects rather than Subject 21

Skipped:
- `08d714e6` and `6d59994d` were rejected because RapidCopy branding would replace SeedSync identity rather than preserving it as the default
- rapidcopy's theme toggle and dark-mode surface are not being taken as defaults because they create a new global settings model and change product presentation precedent

Maintainer decisions:
- none

Verification:
- tests run:
  - `make run-tests-angular`
- manual checks: reviewed the surviving rapidcopy Subject 21 UX deltas against current master, confirmed the dashboard pagination/sorting/bulk-selection/status-count work is already present locally, and isolated the remaining global theme system as a separate product-direction choice
- status: partially verified; Angular suite passed with `211 tests completed`

Notes:
- evaluation details: [S21-user-facing-conflicts.md](/mnt/c/Git/seedsync/doc/integration-notes/S21-user-facing-conflicts.md)
- Candidate: theme toggle and light/dark theme system
  - Value: useful enhancement
  - Faithfulness: C
  - Scope: global
  - Gateability: cleanly isolatable
  - Action: reject for now; defer to future visualization-settings subject
  - Maintainer input: yes
  - Rationale: the maintainer wants a theme-selection mechanism later, but only after the faithful SeedSync default is firmly in place; rapidcopy's theme system should be revisited as part of that later coherent settings project
- Candidate: dashboard pagination, sorting, bulk selection, and status counts
  - Value: useful enhancement
  - Faithfulness: A
  - Scope: sectional
  - Gateability: cleanly isolatable
  - Action: covered elsewhere
  - Maintainer input: no
  - Rationale: these dashboard workflow improvements are already present locally
- Candidate: rebrand assets and RapidCopy naming
  - Value: mostly taste / style / flavor
  - Faithfulness: F
  - Scope: global
  - Gateability: cleanly isolatable
  - Action: reject
  - Maintainer input: no
  - Rationale: replacing SeedSync branding is outside this fork's product direction

Cumulative default-drift review:
- current defaults still feel recognizably like SeedSync
- no previously landed default changes were demoted in this pass
- future theme and visualization work is intentionally deferred until after the faithful default baseline; see [S21-user-facing-conflicts.md](/mnt/c/Git/seedsync/doc/integration-notes/S21-user-facing-conflicts.md)
