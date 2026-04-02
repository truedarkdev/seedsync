# Testing Confidence

This document is the tracked, canonical operating guide for local test-suite
confidence in this repo.

Use it during substantive tasks, not just planning:

- identify the minimum evidence lane set before changing code or docs
- consult the lane rows below that match the touched area
- record any new lane run, failure, or freshness downgrade in the task notes
  or tracker
- if the task exposes a repeat gap, update this doc before you call the task
  done

Lane status shorthand:

- Healthy: fresh evidence exists and still matches the current environment
- Unknown: the lane exists, but there is no current proof in this workspace
- Blocked: the lane cannot be treated as proof on this machine
- Flaky: the lane has been unreliable enough that it should not stand alone

Freshness rule:

- lane statuses are point-in-time evidence, not evergreen certification
- refresh a lane after toolchain, image, browser, Compose, or repo changes that
  can affect it
- if the evidence is stale for the decision at hand, downgrade the lane until
  it is rerun

## Current Lane Inventory

| Lane | Status | What it proves | Reference / entrypoint | Evidence |
| --- | --- | --- | --- | --- |
| Python web/auth backend | Healthy as of 2026-04-02 | The backend external-API auth foundation now has fresh local proof for scoped route gating, legacy-token restrictions, passwordless loopback UI-session behavior, cookie-auth write hardening, the browser shell/static bootstrap boundary, `/server/stream` cookie alignment, and the controller/config web-handler paths that consume that auth model. | `src/python` pytest lanes for `tests/unittests/test_web/test_auth_store.py`, `tests/unittests/test_web/test_handler/test_admin_handler.py`, `tests/unittests/test_web/test_handler/test_config_handler.py`, `tests/unittests/test_web/test_web_app.py`, `tests/integration/test_web/test_web_app.py`, `tests/integration/test_web/test_handler/test_config.py`, `tests/integration/test_web/test_handler/test_controller.py`, and the slice-4 rerun for `tests/unittests/test_web/test_web_app.py`, `tests/unittests/test_web/test_handler/test_config_handler.py`, `tests/integration/test_web/test_handler/test_config.py`, `tests/integration/test_web/test_handler/test_auto_queue.py` | 2026-04-02 local Python runs; targeted rerun passed with junit artifact `tmp/pytest/slice4-python.xml` |
| Python unit/backend | Healthy as of 2026-03-30 | The targeted backend controller path still passes on the supported WSL/Linux Python lane. | `src/python` pytest lane; targeted nodeid `tests/unittests/test_controller/test_controller.py::TestController::test_refresh_path_pairs_rebuilds_runtime_state_and_forces_rescan` | 2026-03-30 local WSL run |
| Python integration/controller | Healthy as of 2026-03-30 | The controller integration path still passes on the supported WSL/Linux lane. | `src/python` pytest lane; targeted nodeid `tests/integration/test_controller/test_controller.py::TestController::test_initial_model` | 2026-03-30 local WSL run |
| WSL backend stop-state integration | Healthy as of 2026-03-30 | The stop-state backend contract now has fresh WSL/Linux proof, so the regression is no longer an open implementation gap. | Targeted `src/python` pytest slice for stop-state coverage | 2026-03-30 local WSL run; 4 targeted tests passed in 26.56s; artifact `tmp/pytest/wsl-backend.junit.xml` |
| Host Angular/Karma full-suite proof | Healthy as of 2026-04-01 for Node 24 host proof | The Angular/Karma host harness now boots and runs the full suite successfully on the Node 24 host path. That is comparison evidence only; the supported frontend closure lane remains the Dockerized Angular/Karma path on Angular 21 / Node 24 / RxJS 7. | `src/angular` headless Karma lane; host log `C:\Git\seedsync\tmp\pytest\host-angular-node24-smoke.log` | 2026-04-01 local host run; Node `v24.0.0` via nvm4w, npm `11.3.0`, Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe`, attempted `--include` filter ignored by npm (`npm warn invalid config include=...`), suite reported `TOTAL: 592 SUCCESS` |
| Dockerized Angular build/test | Healthy as of 2026-04-01 | The Dockerized Angular lane still passes cleanly after the RxJS 7 manifest bump, `rxjs-compat` removal, and app-owned Sass `@import` migration, so the Angular 21 / Node 24 Docker verifier path remains the supported frontend closure lane. | `src/docker/test/angular/compose.yml` default verifier path plus Angular build images | 2026-04-01 local Docker proof; exact `docker compose -f src/docker/test/angular/compose.yml up --build --abort-on-container-exit --exit-code-from tests` exited `0` with `TOTAL: 296 SUCCESS`; the subsequent `docker build -f src/docker/build/docker-image/Dockerfile --target seedsync_build_angular .` also passed; remaining Sass warnings were from vendored `font-awesome` SCSS in `node_modules`, not app-owned `@import` usage |
| Angular API access UI/service lanes | Healthy as of 2026-04-02 | The Settings-page API Access section now has fresh proof for the shared confirm-modal click path, the API-access component interactions, and the live Docker-served browser flow for create, edit, rotate, revoke, legacy disable, legacy clear, and the revoked-key lifecycle slice: revoked keys hidden by default, reveal toggle, locked revoked rows, permanent delete, and repeat-revoke rejection. The supported local Docker UI bootstrap path still depends on the explicit trusted Docker-gateway runtime hook rather than a pure loopback transport assumption. | Targeted `src/angular` Karma lane for `tests/unittests/services/utils/modal.service.spec.ts`, prior targeted `src/angular` Karma lanes for `tests/unittests/services/settings/api-access.service.spec.ts`, `tests/unittests/pages/settings/api-access.component.spec.ts`, and `tests/unittests/pages/settings/settings-page.component.spec.ts`, plus live browser proof against `http://127.0.0.1:8800/settings` | 2026-04-02 local proof; targeted backend pytest, Angular/Karma proof, and live Docker/browser validation all contributed to the revoked-key lifecycle evidence; shared modal regression ran in the ChromeHeadless Angular suite with `TOTAL: 308 SUCCESS` from `tmp/angular-modal-service-spec.log`; rebuilt live Docker browser validation confirmed real pointer clicks on API Access rotate/revoke modals and exercised create, edit, revoke, legacy disable, legacy clear, and revoked-key lifecycle behavior with screenshots under `tmp/slice5-*`; earlier 2026-04-01 targeted API-access service/component/settings-page artifacts remain valid at `tmp/pytest/api-access-service.spec.out.log`, `tmp/pytest/api-access-component.spec.out.log`, and `tmp/pytest/settings-page.component.spec.out.log` |
| Legacy Protractor/e2e | Flaky as of 2026-04-01 | The Dockerized lane still gets through compose startup, configure, remote/Selenium readiness, and browser session bootstrap. The targeted settings teardown path is now fixed for the exercised compose-backed slice, and a narrow dashboard canary on the same teardown path also exits cleanly. The broader legacy lane is still not closure evidence because the full dashboard suite and other legacy coverage remain unresolved. | `src/docker/test/e2e/compose.yml` plus targeted legacy Protractor specs under `src/e2e/tests` | 2026-04-01 verifier evidence: settings ended as `Executed 1 of 1 spec SUCCESS` with `chrome #01 passed` and no old teardown noise; dashboard canary ended as `Executed 1 of 8 specs INCOMPLETE (7 SKIPPED)` with exit `0`; broader dashboard failures remain out of scope for this slice; artifacts `tmp/pytest/e2e-settings-teardown-final-rerun-v2-20260401.log` and `tmp/pytest/e2e-dashboard-canary-20260401.log` |
| Native Windows Poetry path | Blocked on this machine | The local Windows Python environment is outside the repo-supported Poetry range, so this host path cannot be treated as a valid confidence lane here. | `src/python` Poetry environment | 2026-03-30 local host check; Python 3.13.12 vs supported `>=3.11,<3.13` |

## Minimum Evidence Ladder

Use the smallest lane set that honestly proves the change, then widen only
when the change crosses layers or runtime surfaces.

| Change class | Minimum evidence | Notes |
| --- | --- | --- |
| Backend-only | One targeted unit test or narrow controller/integration slice that covers the touched backend path | If the change touches controller state, filesystem state, or path-pair handling, prefer a controller integration slice over unit-only proof. |
| Frontend-only | Targeted frontend spec/component coverage, plus a browser smoke check when the behavior is only observable in the DOM/runtime | If the change affects browser rendering, served assets, or Docker-built frontend output, add live UI/runtime proof instead of relying on host-only smoke. |
| Runtime-visible UI behavior | Host Angular/Karma smoke plus browser or Docker/live proof when the live app decides the outcome | Host smoke alone is supportive evidence only; it does not close the task by itself. |
| Mixed API/UI | Backend proof plus frontend/browser proof of render or interaction | Add browser/runtime evidence if the user-visible path depends on the live app shell. |
| Transfer / stop-resume / scan / path-pair regression | Targeted backend proof plus WSL/Linux controller integration | If the change can be exercised through the Docker-served app, require live Docker validation before closing. |

The ladder is a floor, not a ceiling. Broader regressions can and should add
more evidence, but the minimum should always be visible and specific.

## Freshness And Lifecycle

- Treat lane health as a working status, not a permanent label.
- If a lane fails, starts flaking, or is blocked by environment or tooling
  drift, downgrade it immediately and note the blocker.
- If a task depends on runtime-visible proof and the live lane has not been
  exercised in the current environment, do not treat host smoke as a
  substitute.
- When a lane is no longer the best proof path for a task, keep it listed but
  stop relying on it as closure evidence.

## Gap Escalation

- If the minimum lane set does not prove the task, record the gap here or in
  the tracker instead of leaving it in memory.
- If the same gap keeps showing up across tasks, convert it into follow-up
  work.
- If live Docker or browser proof is required but blocked, name the blocker and
  treat it as lane confidence debt, not as unfinished product implementation.
- Host Angular/Karma Node 24 proof is now available on this machine, but it
  remains comparison evidence rather than the supported closure lane.
- The final live-app Playwright UI/UX sweep completed on 2026-04-01 after a
  corrected rerun against the live Docker baseline.
- The earlier sweep was invalidated by stale runtime state because the local
  app had been started before the remote test server was ready, and `/files`
  was not a valid route target for this app.
- The corrected rerun on valid routes passed with stable screenshots and no
  remote-scan error banner, so this item is no longer an open closeout gate.

## Durable Artifacts

When exact outcomes matter, write the run to `tmp/pytest/` at the repo root and
keep the artifact name descriptive.

- Prefer `--junitxml=tmp/pytest/<lane-name>.xml` for pass/fail-sensitive runs.
- Keep the full log with `tee tmp/pytest/<lane-name>.log` when the terminal may
  truncate useful detail.
- Put batch-run artifacts under `tmp/pytest/runs/<timestamp>/` when a scripted
  runner already creates that layout.
- Save the nodeid or lane label in the filename so later evidence can be
  matched back to the exact command quickly.

## Known Gaps

These items are deliberately left as future work. They are not missing
documentation.

- Stop-resume browser/e2e lane: the backend stop-state slice now has fresh
  WSL/Linux proof, but the legacy browser-backed lane remains separate from
  the Angular workspace upgrade closure path. The current slices have
  normalized the Selenium/Chrome wiring, made configure deterministic,
  bridged the legacy WebDriver bootstrap, contained the old Angular
  testability-sync failure on the targeted dashboard/settings path, hardened
  the dashboard fixture assumptions, and fixed the targeted settings
  teardown path for the exercised compose-backed slice. The broader legacy
  e2e lane still needs a fresh full-lane rerun before it can be treated as
  healthy.
- Native Windows Poetry validation: blocked on this machine because the local
  Python version is 3.13.12, outside the repo-supported `>=3.11,<3.13` range.
  If Windows host validation becomes necessary, it needs a supported Python
  environment first.
