# Frontend Compatibility Baseline

Status: tracked baseline note for the frontend modernization / compatibility
migration task.

This note captures the current contract before any toolchain or dependency
changes are attempted.

## Current Frontend Contract

- `src/angular/package.json` still defines the legacy frontend as Angular 4.2.4
  with `@angular/cli` 1.3.2.
- `src/angular/package-lock.json` is `lockfileVersion: 3`, so the repo already
  relies on a newer npm lockfile format than the Angular 4-era codebase.
- The lockfile keeps a mixed dependency shape:
  - root `node-sass` is pinned at `^9.0.0`
  - the Angular CLI subtree still carries `node-sass` 4.14.1
  - `typescript` is split between the app-level `^3.2.2` and older CLI
    compatibility ranges inside the lockfile
- `src/docker/build/docker-image/Dockerfile` builds the Angular layer from
  `node:20-bookworm-slim`, installs with `npm install --legacy-peer-deps`, and
  still sets `NODE_OPTIONS=--openssl-legacy-provider`.
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
| Does `NODE_OPTIONS=--openssl-legacy-provider` remain necessary? | Phase 1 | Test this after the baseline is locked, then either keep it isolated or remove it with proof. |
| What is the smallest safe `node-sass` strategy? | Phase 2 | The dependency shape is still mixed, so this needs a dedicated compatibility pass. |
| Should the lockfile be regenerated around the current install contract? | Phase 2 | Only revisit after the install strategy is settled. |
| Are Docker, Makefile, and CI fully aligned on the same effective Angular lane? | Phase 3 | Pipeline hardening should happen after the contract is documented and the install path is stable. |
| Does a broader Angular framework migration belong in this task? | Phase 4 or a separate task | If Angular 4 or CLI 1.x becomes the real blocker, reopen it as a dedicated modernization effort. |

## Resume Sentence

Start the next slice by stabilizing the install and build contract, then verify
whether the legacy Angular stack still needs the OpenSSL workaround before any
dependency cleanup is attempted.
