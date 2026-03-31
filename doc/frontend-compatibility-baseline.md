# Frontend Compatibility Baseline

Status: Phase 0 complete; Phase 1 in progress for the frontend modernization /
compatibility migration task.

This note captures the current contract before any toolchain or dependency
changes are attempted. The global OpenSSL legacy-provider workaround question
was resolved by proof on 2026-03-31.

## Current Frontend Contract

- `src/angular/package.json` still defines the legacy frontend as Angular 4.2.4
  with `@angular/cli` 1.3.2.
- `src/angular/package-lock.json` is `lockfileVersion: 3`, so the repo already
  relies on a newer npm lockfile format than the Angular 4-era codebase.
- The proven Dockerized runtime currently resolves root `node-sass` to
  `9.0.0`.
- The Angular CLI subtree still names `node-sass` 4.14.1 in lockfile metadata,
  but that legacy 4.x expectation is not a second installed runtime copy in the
  current built image. Treat the nested CLI `node-sass` 4.x subtree as
  intentional transitive metadata debt for now, not as an active second runtime
  dependency, until a controlled lockfile refresh is justified.
- `typescript` is split between the app-level `^3.2.2` and older CLI
  compatibility ranges inside the lockfile.
- `src/docker/build/docker-image/Dockerfile` builds the Angular layer from
  `node:20-bookworm-slim` and installs with `npm install --legacy-peer-deps`.
- `src/docker/build/deb/Dockerfile` uses the same Angular build image and the
  same install behavior for the Debian packaging path.
- `src/docker/test/angular/Dockerfile` reuses the Angular build environment,
  adds Chromium, and runs the headless Karma lane with
  `--browsers ChromeHeadlessCI --single-run`.
- `src/docker/test/angular/compose.yml` mounts `src/angular/src` read-only into
  the test container, so the Angular test lane is effectively exercising the
  live workspace tree.
- `Makefile` exposes the primary local Angular gate through `run-tests-angular`,
  which builds `seedsync/build/angular/env` and then runs the compose-based
  test service.
- `.github/workflows/master.yml` wires CI to the same contract by calling
  `make run-tests-angular` in the `unittests-angular` job before the build jobs
  consume Angular artifacts.

## 2026-03-31 Proof

- Dockerized Angular test lane passed with `TOTAL: 293 SUCCESS`.
- `src/docker/build/deb/Dockerfile` Angular env stage built successfully.
- `src/docker/build/docker-image/Dockerfile` Angular path built successfully.

## Supported Compatibility Target

Keep the legacy frontend on a clearly supported Node 20 / npm 10 baseline with
reproducible installs and tests. On this machine, that means the documented
local host baseline of Node `v20.18.3` and npm `10.8.2`, mirrored by the
Node 20 Docker images used by the Angular build and test lanes.

## First Validation Gates To Protect

1. `make run-tests-angular` remains the primary frontend verification gate.
2. The Dockerized Angular lane keeps producing the same effective test contract
   for local Docker, Debian packaging, and CI.
3. The downstream build paths that consume Angular output continue to work:
   - `src/docker/build/docker-image/Dockerfile`
   - `src/docker/build/deb/Dockerfile`
   - `.github/workflows/master.yml`
4. If frontend assets or Docker build inputs change, the lane must be rebuilt
   rather than assuming a restart is enough.

## Resume Notes By Later Phase

| Open question | Later phase | Why it waits |
| --- | --- | --- |
| Should we investigate a controlled lockfile refresh to reduce warning/noise from the `node-sass` runtime/metadata mismatch? | Phase 2 | The active runtime shape is now proven; only decide on refresh churn if the warning/noise reduction is worth it. |
| Should the lockfile be regenerated around the current install contract? | Phase 2 | Only revisit as a controlled refresh decision after the install strategy and churn tradeoff are both settled. |
| Are Docker, Makefile, and CI fully aligned on the same effective Angular lane? | Phase 3 | Pipeline hardening should happen after the contract is documented and the install path is stable. |
| What is the smallest Angular / Angular CLI upgrade path that preserves the Dockerized frontend lane? | Phase 4 | Angular modernization is part of this task, but it should happen as its own explicit phase after the current compatibility floor is stable. |
| When should we migrate from `node-sass` to Dart Sass? | Phase 5 | Plan the Sass migration after the Angular upgrade phase has established the new supported toolchain floor. |

## Resume Sentence

Finish the current compatibility-hardening slices first, then treat Angular /
Angular CLI upgrade as the next explicit modernization phase before planning the
move from `node-sass` to Dart Sass. Do not treat the current nested CLI
`node-sass` 4.x lockfile subtree as cleanup-by-default; revisit it only through
a controlled lockfile-refresh decision if the warning/noise reduction is worth
the churn.
