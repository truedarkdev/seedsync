# Frontend Compatibility Baseline

Status: compatibility-hardening groundwork complete through Phase 3; Phase 4 is
closed based on the supported Angular 21 / Node 24 / RxJS 7 Dockerized
Angular/Karma closure lane and completed slices 1-7.

Current Phase 4 checkpoint: slices 1-7 completed and closed.
Slice 1 landed the toolchain-first Angular 21 workspace migration with Node 24
Docker/frontend lane alignment, Docker build/test command modernization, and
narrow compatibility bridges for legacy modal/storage usage.
Slice 2 modernized the page-layer RxJS imports/operators and directly related
page specs while keeping service-layer RxJS APIs and bridge retirement deferred
to later Phase 4 slices.
Slice 3 modernized the service/runtime RxJS imports/APIs while still staying on
RxJS 6.6.7 and explicitly deferred the RxJS 7 bump, `rxjs-compat` removal, and
bridge retirement to later slices.
Slice 4 raised the tracked frontend manifest to RxJS 7, removed
`rxjs-compat`, and fixed the remaining legacy deep-import breakpoints in
tests/mocks while keeping broader bridge cleanup deferred.
Slice 5 retired the modal/storage compatibility bridge from the active frontend
path and removed verified-unused Angular 4-era config artifacts.
Slice 6 retired the legacy Angular 4-era e2e scaffold under `src/angular/e2e`
after proving the active Angular workspace no longer references it.

## Post-Phase-4 Follow-Ups

The following frontend follow-up slices landed after Phase 4 closed:

- `1c641bc5` `fix(e2e): stabilize legacy webdriver lane`
- `5d55b78b` `fix(e2e): contain legacy angular sync loss on navigation`
- `c935538e` `fix(e2e): stabilize dashboard legacy fixture assumptions`
- `dc65d44f` `fix(e2e): await legacy matcher work before teardown`

These slices stabilized the legacy `src/e2e` lane. The live-app Playwright
UI/UX sweep was later rerun on the correct live Docker baseline, so it is no
longer an open follow-up.
Host and WSL are now aligned to Node `v24.0.0` / npm `11.3.0`.

This note captures the current contract before any toolchain or dependency
changes are attempted. The global OpenSSL legacy-provider workaround question
was resolved by proof on 2026-03-31.

## Current Frontend Contract

- `src/angular/package.json` targets Angular 21.2.5 with `@angular/cli`
  21.2.5.
- `src/angular/package-lock.json` is `lockfileVersion: 3`, so the repo already
  relies on a newer npm lockfile format than the Angular 4-era codebase.
- A controlled Node 24 lockfile-refresh check left
  `src/angular/package-lock.json` byte-for-byte identical
  (`560692` bytes, same SHA-256, still `lockfileVersion: 3`, still no
  `packageManager` field), so it did not create a tracked lockfile-policy
  change.
- The proven Dockerized runtime currently resolves root `node-sass` to
  `9.0.0`.
- The Angular CLI subtree still names `node-sass` 4.14.1 in lockfile metadata,
  but that legacy 4.x expectation is not a second installed runtime copy in the
  current built image. Treat the nested CLI `node-sass` 4.x subtree as
  intentional transitive metadata debt for now, not as an active second runtime
  dependency; the controlled lockfile-refresh check did not change it, so
  revisit it through Sass modernization rather than lockfile-policy churn.
- `typescript` is split between the app-level `^3.2.2` and older CLI
  compatibility ranges inside the lockfile.
- `src/docker/build/docker-image/Dockerfile` builds the Angular layer from
  `node:24-bookworm-slim` and installs with `npm install --legacy-peer-deps`.
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

## 2026-04-01 Phase 4 Slice 3 Proof

- Service/runtime RxJS imports were modernized from legacy forms like
  `rxjs/Observable`, `rxjs/Subject`, `rxjs/Subscription`, `rxjs/Rx`, and
  `rxjs/add/...` to canonical imports from `rxjs` plus `rxjs/operators`.
- Runtime `Observable.create(...)` call sites were replaced with
  `new Observable(...)` while preserving the existing service behavior.
- Remaining static `Observable.combineLatest(...)` service usage was updated to
  canonical RxJS 6-style `combineLatest([...])` usage in the touched runtime
  layer.
- The slice intentionally stayed on `rxjs` `6.6.7` and did not remove
  `rxjs-compat`.
- The exact default Angular Docker verifier path passed again on the final
  candidate:
  `docker compose -f src/docker/test/angular/compose.yml up --build
  --abort-on-container-exit --exit-code-from tests`
- The full suite again reported `TOTAL: 296 SUCCESS` on that exact path.

## 2026-04-01 Phase 4 Slice 4 Proof

- `src/angular/package.json` now targets `rxjs` `^7.8.2`.
- `rxjs-compat` has been removed from the tracked frontend manifest.
- The remaining legacy deep imports that would break after removing
  `rxjs-compat` were updated in the affected tests/mocks to use root imports
  from `rxjs`.
- A repo-wide grep over `src/angular` no longer found `rxjs-compat`,
  `rxjs/Observable`, `rxjs/Subject`, `rxjs/Subscription`, `rxjs/Rx`, or
  `rxjs/add/...` references.
- The exact default Angular Docker verifier path passed again on the final
  candidate:
  `docker compose -f src/docker/test/angular/compose.yml up --build
  --abort-on-container-exit --exit-code-from tests`
- The full suite again reported `TOTAL: 296 SUCCESS` on that exact path.

## 2026-04-01 Phase 4 Slice 5 Proof

- The modal compat bridge was retired from the active frontend path by moving
  the implementation into `modal.service.ts` and updating the app module,
  page-layer consumers, and unit specs to the new service path.
- The storage compat bridge was retired from the active frontend path by
  moving the implementation into `storage.service.ts` and updating the file
  options service plus its unit coverage to the new provider path.
- The modal overlay/dialog internals no longer advertise the legacy compat
  class names.
- `src/angular/tslint.json` and `src/angular/protractor.conf.js` were removed
  after verifying that current `package.json`, `angular.json`, Makefile, and
  active Angular/Docker build-test paths do not reference them.
- `src/angular/src/environments/environment.ts` no longer points at the removed
  `.angular-cli.json` workspace comment and now references `angular.json`.
- The narrow Angular unit lane for the touched service/component specs passed
  after the import updates.

## 2026-04-01 Phase 4 Slice 6 Proof

- `src/angular/angular.json` now stands as the authoritative Angular workspace
  config for the active frontend lane and only defines the Karma test target
  from `src/**/*.spec.ts`.
- The active-entrypoint reference check covered both `src/angular/angular.json`
  and `src/angular/package.json` before the retired scaffold was removed.
- The obsolete Angular 4-era e2e scaffold files under `src/angular/e2e` were
  removed: `app.e2e-spec.ts`, `app.po.ts`, and `tsconfig.e2e.json`.
- A repo-wide search over `src/angular` no longer finds active workspace
  references to the retired scaffold path.
- The active frontend verification path remains the Dockerized Karma lane, so
  this retirement is isolated from the live Angular runtime contract.

## Phase 4 Closure Record

- The current frontend has retired the modal/storage compatibility bridge from
  the active runtime path, but the repo still carries other historical
  Angular 4-era/deprecated scaffolding only where it is separately proven
  unused.
- The current Dockerized frontend lane has now been lifted to a Node 24-based
  Angular 21 workspace/toolchain checkpoint.
- The host Angular/Karma path has now been exercised successfully on Node 24
  on this machine, but the Dockerized lane remains the supported closure
  lane.
- The live-app Playwright UI/UX sweep has now been completed on the correct
  live Docker baseline and is not a remaining closeout gate.
- The intended Phase 4 destination is Angular 21 on Node 24 LTS.
- Temporary bridge slices are acceptable during the migration, but Angular 21
  plus Node 24 LTS is the target to steer every Phase 4 slice toward.
- The gap from Angular 4 / CLI 1.3 to Angular 21 is large enough that this
  should be treated as a multi-slice program, not a one-shot package bump.
- The first two upgrade slices are now complete:
  - slice 1: workspace/toolchain + Docker lane modernization
  - slice 2: page-layer RxJS modernization
- The first seven upgrade slices are now complete:
  - slice 1: workspace/toolchain + Docker lane modernization
  - slice 2: page-layer RxJS modernization
  - slice 3: service/runtime RxJS modernization on RxJS 6.6.7
  - slice 4: RxJS 7 manifest bump + `rxjs-compat` removal
  - slice 5: modal/storage bridge retirement + verified-unused legacy config
    removal
  - slice 6: legacy Angular 4-era e2e scaffold retirement under
    `src/angular/e2e`
  - slice 7: app-owned Sass `@import` modernization under `src/angular/src/`
- Any later cleanup follow-up should focus on newly discovered Angular 4-era
  leftovers that are still separately justified by current repo usage.

## 2026-04-01 Phase 4 Slice 7 Proof

- App-owned Sass consumers under `src/angular/src/` were migrated from
  deprecated `@import` usage to Dart Sass-friendly `@use ... as *` imports.
- `src/angular/src/styles.scss` no longer imports the shared common partial,
  because it does not consume the shared Sass symbols itself.
- Shared variables and placeholders from `src/angular/src/app/common/_common.scss`
  still resolve in the affected component styles through the module import
  path.
- The exact default Angular Docker verifier path passed again on the final
  candidate:
  `docker compose -f src/docker/test/angular/compose.yml up --build
  --abort-on-container-exit --exit-code-from tests`
- The full suite reported `TOTAL: 296 SUCCESS` on that exact path.
- The production Angular build stage also passed:
  `docker build -f src/docker/build/docker-image/Dockerfile --target
  seedsync_build_angular .`
- Remaining Sass deprecation warnings in that build now come from
  `node_modules/font-awesome/scss/font-awesome.scss`, so the open Sass warning
  debt is third-party/transitive rather than app-owned `@import` usage.

## 2026-04-01 Live-App Playwright UI/UX Sweep Proof

- The earlier live-app Playwright sweep was invalidated by stale runtime state
  because the local app had been started before the remote test server was
  ready.
- `/files` was not a valid route target in this app, so it should not be cited
  as a supported sweep route.
- After the correct Docker baseline was established and the local app was
  restarted, the final rerun on valid routes passed with stable screenshots
  and no remote-scan error banner.
- Evidence artifacts: `C:\Users\johan\AppData\Local\Temp\codex-playwright-tools\ui-sweep-final-2026-04-01-summary.json`,
  `C:\Users\johan\AppData\Local\Temp\codex-playwright-tools\ui-sweep-final-2026-04-01-dashboard.png`,
  `C:\Users\johan\AppData\Local\Temp\codex-playwright-tools\ui-sweep-final-2026-04-01-dashboard-series.png`,
  `C:\Users\johan\AppData\Local\Temp\codex-playwright-tools\ui-sweep-final-2026-04-01-settings.png`,
  `C:\Users\johan\AppData\Local\Temp\codex-playwright-tools\ui-sweep-final-2026-04-01-logs.png`,
  `C:\Users\johan\AppData\Local\Temp\codex-playwright-tools\ui-sweep-final-2026-04-01-about.png`.

## Supported Compatibility Target

The supported closure lane for the upgraded frontend is the Dockerized
Angular/Karma path on Angular 21 / Node 24 / RxJS 7 with reproducible installs
and tests. The local host Angular/Karma path has now been exercised
successfully on Node 24 on this machine; the older Node `v20.18.3` /
npm `10.8.2` harness remains the documented legacy comparison path and is not
closure-grade proof.

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

## Later Follow-Ups

These items remain tracked for later modernization or cleanup work. Phase 4 is
closed, and the remaining frontend-modernization items are the ones listed
below.

| Item | Later phase | Why it stays later |
| --- | --- | --- |
| Legacy `src/e2e` Protractor modernization | Phase 4+ | This is active legacy test infrastructure, but it is outside the Angular workspace upgrade closure path and belongs to a later modernization track. |
| Third-party Sass deprecation cleanup (`font-awesome`) | Phase 5 | The app-owned Sass `@import` migration is complete; the remaining warnings come from vendored `font-awesome` SCSS in `node_modules`, so any follow-up now targets third-party/transitive debt rather than app-owned import usage. |

## Closure Note

Phase 4 is closed on the supported Angular 21 / Node 24 / RxJS 7 Dockerized
Karma lane after slices 1-7. The host Angular/Karma path has now been
exercised successfully on Node 24 locally as comparison evidence. The live-app
Playwright UI/UX sweep is complete, so it is no longer a closeout gate or a
later follow-up. Treat the items listed above as later follow-ups, not as open
blockers to the closed Phase 4 work. Do not treat the current nested CLI
`node-sass` 4.x lockfile metadata as cleanup-by-default; the controlled
lockfile-refresh check did not change it, so revisit it only through a Sass
migration or broader frontend modernization slice if the warning/noise
reduction is worth the churn.
App-owned Sass `@import` usage under `src/angular/src/` has now been migrated;
the remaining Sass warnings are from vendored `font-awesome` SCSS and are a
separate third-party follow-up, not unresolved app-owned Sass debt.
