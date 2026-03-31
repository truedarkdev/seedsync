# Frontend Compatibility Baseline

Status: compatibility-hardening groundwork complete through Phase 3; Phase 4
upgrade execution is active with Angular 21 on Node 24 LTS as the intended
destination for the frontend modernization / compatibility migration task.

Current Phase 4 checkpoint: slices 1-2 completed.
Slice 1 landed the toolchain-first Angular 21 workspace migration with Node 24
Docker/frontend lane alignment, Docker build/test command modernization, and
narrow compatibility bridges for legacy modal/storage usage.
Slice 2 modernized the page-layer RxJS imports/operators and directly related
page specs while keeping service-layer RxJS APIs and bridge retirement deferred
to later Phase 4 slices.

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
  `--browsers ChromeHeadlessCI --single-run` from `/app`.
- `src/docker/test/angular/compose.yml` now mounts the full `src/angular` tree
  read-only at `/app` and preserves image-installed dependencies through an
  `/app/node_modules` volume, so the Angular test lane uses a tree shape closer
  to the build and Debian lanes.
- `Makefile` exposes the primary local Angular gate through `run-tests-angular`,
  which builds `seedsync/build/angular/env` and then runs the compose-based
  test service.
- `.github/workflows/master.yml` wires CI to the same contract by calling
  `make run-tests-angular` in the `unittests-angular` job before the build jobs
  consume Angular artifacts.

## 2026-03-31 Phase 3 Proof

- Dockerized Angular test lane passed with `TOTAL: 293 SUCCESS`.
- `src/docker/build/deb/Dockerfile` Angular env stage built successfully.
- `src/docker/build/docker-image/Dockerfile` Angular path built successfully.
- Fresh-volume Angular test bootstrap passed after the full-tree `/app` mount
  change.
- Read-only `/app` probe passed while `ng test --browsers ChromeHeadlessCI
  --single-run` still completed successfully.

## 2026-03-31 Phase 4 Slice 1 Proof

- `src/angular/package.json` now targets Angular 21.x / Angular CLI 21.x with
  the accompanying Angular build/test toolchain floor.
- `src/angular/angular.json` now replaces the legacy `.angular-cli.json`
  workspace shape.
- `src/docker/build/docker-image/Dockerfile` and
  `src/docker/build/deb/Dockerfile` now build the Angular layer from
  `node:24-bookworm-slim`.
- `src/docker/test/angular/Dockerfile` now runs the Angular 21 Karma lane on
  Node 24 with a bounded writable Angular cache path.
- `src/docker/test/angular/compose.yml` now drives the exact Angular 21 test
  command directly and preserves writable `/app/node_modules` and
  `/app/.angular` volumes for the Dockerized Karma lane.
- The exact default Angular Docker verifier path passed cleanly:
  `docker compose -f src/docker/test/angular/compose.yml up --build
  --abort-on-container-exit --exit-code-from tests`
- The full suite reported `TOTAL: 296 SUCCESS` on that exact path.
- Prior Angular 21 migration blockers in `app.component.spec.ts`,
  `path-pairs.component.spec.ts`, `files-page.component.spec.ts`,
  `file.component.spec.ts`, and `notification.service.spec.ts` were exercised
  and passed in the full green Dockerized run.

## 2026-04-01 Phase 4 Slice 2 Proof

- Page-layer RxJS imports were modernized from legacy deep imports like
  `rxjs/Observable`, `rxjs/Subject`, and `rxjs/add/...` to root imports from
  `rxjs` plus pipeable operators from `rxjs/operators`.
- `takeUntil` call sites in page components now use `pipe(takeUntil(...))`
  instead of legacy chained side-effect operator wiring.
- The `file-list` pagination stream now uses `combineLatest([...])` instead of
  the legacy static `Observable.combineLatest(...)` pattern in the page layer.
- Directly related page specs were updated in lockstep to use modern RxJS
  helper imports such as `of` and the RxJS 6-compatible `throwError("boom")`
  form where needed.
- The exact default Angular Docker verifier path passed again on the final
  candidate:
  `docker compose -f src/docker/test/angular/compose.yml up --build
  --abort-on-container-exit --exit-code-from tests`
- The full suite again reported `TOTAL: 296 SUCCESS` on that exact path.

## Phase 4 Upgrade Investigation

- The current frontend still uses a legacy Angular CLI workspace shape:
  `.angular-cli.json`, Protractor-era config, and TSLint/Codelyzer.
- The current Dockerized frontend lane has now been lifted to a Node 24-based
  Angular 21 workspace/toolchain checkpoint.
- The intended Phase 4 destination is Angular 21 on Node 24 LTS.
- Temporary bridge slices are acceptable during the migration, but Angular 21
  plus Node 24 LTS is the target to steer every Phase 4 slice toward.
- The gap from Angular 4 / CLI 1.3 to Angular 21 is large enough that this
  should be treated as a multi-slice program, not a one-shot package bump.
- The first two upgrade slices are now complete:
  - slice 1: workspace/toolchain + Docker lane modernization
  - slice 2: page-layer RxJS modernization
- The next upgrade slice should stay runtime/service-focused: remove legacy
  service-layer RxJS API usage without regressing the Dockerized Karma lane.

## Supported Compatibility Target

Keep the active Phase 4 frontend lane on a clearly supported Angular 21 / Node
24 LTS baseline with reproducible Dockerized installs and tests. The local host
fallback remains the documented Node `v20.18.3` / npm `10.8.2` legacy harness
for comparison only until the host tooling is deliberately lifted to the same
modern floor.

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
| Are Docker, Makefile, and CI fully aligned on the same effective Angular lane? | Phase 3 | Substantially improved by the `/app` test-lane parity change; keep watching for new drift as upgrade work begins. |
| What is the next coherent Angular 21 runtime modernization slice after page-layer RxJS cleanup? | Phase 4 | Slice 2 is now complete; next slices should focus on service-layer RxJS modernization without regressing the green Dockerized Karma lane. |
| Which legacy compatibility bridges can be retired after the Angular 21 checkpoint is stable? | Phase 4 | Revisit the temporary modal/storage and `rxjs-compat` bridges once the Angular 21 lane has stayed green through follow-on app updates. |
| When should the local host frontend harness be lifted from the old Node 20 host baseline to the same Node 24 floor as Docker? | Phase 4 | Docker is now the supported proof lane for the upgraded frontend; decide on host-floor lift separately once the Angular 21 Docker lane has settled. |
| When should we migrate from `node-sass` to Dart Sass? | Phase 5 | Plan the Sass migration after the Angular upgrade phase has established the new supported toolchain floor. |

## Resume Sentence

Continue Phase 4 from the now-green Angular 21 / Node 24 slices 1-2 baseline
by taking the next bounded runtime modernization slice, most likely
service-layer RxJS cleanup, while preserving the Dockerized Karma lane as the
closure gate. Do not treat the current nested CLI `node-sass` 4.x lockfile
subtree as cleanup-by-default; revisit it only through a controlled
lockfile-refresh decision if the warning/noise reduction is worth the churn.
