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

`Resume from next` is the next starting point for review work after the recorded pass. It should normally be the first unreviewed commit after the inclusive `Last reviewed upstream commit`.

`New upstream since last pass` should normally stay `n/a` or `none` until a later refresh pass happens after fetching remotes.

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
- none

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

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- `Verification Milestone A` exposed real Python failures in `tests/integration/test_controller/test_controller.py` around bad remote config validation, including `test_bad_config_remote_address_raises_exception`, `test_bad_config_remote_path_to_scan_script_raises_exception`, and `test_bad_config_remote_username_raises_exception`. Revisit those as likely Subject 10/18 follow-up when backend config/controller validation work is under review.

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- `Verification Milestone A` exposed real Python failures in `tests/integration/test_controller/test_controller.py` around bad remote config validation, including `test_bad_config_remote_address_raises_exception`, `test_bad_config_remote_path_to_scan_script_raises_exception`, and `test_bad_config_remote_username_raises_exception`. Revisit those as likely Subject 10/18 follow-up when backend config/controller validation work is under review.

## Subject 7 - About, Modal, And Shared UI Components

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- `Verification Milestone A` exposed real Python failures in `tests/integration/test_controller/test_controller.py` around bad remote config validation, including `test_bad_config_remote_address_raises_exception`, `test_bad_config_remote_path_to_scan_script_raises_exception`, and `test_bad_config_remote_username_raises_exception`. Revisit those here together with Subject 10 config validation work instead of reopening the milestone.

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- `Verification Milestone A` exposed real Python failures in `tests/integration/test_controller/test_controller.py` around bad remote config validation, including `test_bad_config_remote_address_raises_exception`, `test_bad_config_remote_path_to_scan_script_raises_exception`, and `test_bad_config_remote_username_raises_exception`. Revisit those here together with Subject 10 config validation work instead of reopening the milestone.

## Subject 8 - Settings UI

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 9 - Logs UI

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 10 - Config And Settings Backend

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 11 - Model, Serialization, And Web API

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 12 - Files Page And File Operations UI

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 13 - SSH And Remote Command Handling

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 14 - Transfers And LFTP

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 15 - Scanning

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 16 - Auto Queue

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 17 - Extraction And Archive Handling

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 18 - Core Controller

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 19 - Dashboard And Main Layout UI

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 20 - Cleanup, Deletion, And File Safety

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 21 - Cross-Cutting UX Or Workflow Conflicts

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none
