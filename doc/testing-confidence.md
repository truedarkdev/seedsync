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
| Python unit/backend | Healthy as of 2026-03-30 | The targeted backend controller path still passes on the supported WSL/Linux Python lane. | `src/python` pytest lane; targeted nodeid `tests/unittests/test_controller/test_controller.py::TestController::test_refresh_path_pairs_rebuilds_runtime_state_and_forces_rescan` | 2026-03-30 local WSL run |
| Python integration/controller | Healthy as of 2026-03-30 | The controller integration path still passes on the supported WSL/Linux lane. | `src/python` pytest lane; targeted nodeid `tests/integration/test_controller/test_controller.py::TestController::test_initial_model` | 2026-03-30 local WSL run |
| WSL backend stop-state integration | Healthy as of 2026-03-30 | The stop-state backend contract now has fresh WSL/Linux proof, so the regression is no longer an open implementation gap. | Targeted `src/python` pytest slice for stop-state coverage | 2026-03-30 local WSL run; 4 targeted tests passed in 26.56s; artifact `tmp/pytest/wsl-backend.junit.xml` |
| Frontend host harness | Healthy as of 2026-03-30 for host-level smoke only | The Angular/Karma host harness still boots and runs the smoke suite successfully, but that only proves the host harness. | `src/angular` headless Karma lane; smoke run `291/291 SUCCESS` | 2026-03-30 local host run |
| Dockerized Angular build/test | Healthy as of 2026-04-01 | The Dockerized Angular lane still passes cleanly after the page-layer RxJS modernization slice, so the Angular 21 / Node 24 Docker verifier path remains the supported frontend closure lane. | `src/docker/test/angular/compose.yml` default verifier path plus Angular build images | 2026-04-01 local Docker proof; exact `docker compose -f src/docker/test/angular/compose.yml up --build --abort-on-container-exit --exit-code-from tests` exited `0` with `TOTAL: 296 SUCCESS`; page-level RxJS modernization candidate verified on that lane |
| Docker/browser/e2e | Blocked by harness/env on this machine | The Compose files still parse, but the live browser-backed runtime path cannot be treated as proof here until the Selenium/Chrome lane builds and the localhost:4444/default-bridge assumptions are fixed. | `src/docker/test/e2e/compose.yml` plus `src/docker/test/e2e/compose-remote-dev.yml` | 2026-03-30 local compose parse only; browser lane build failure in `src/docker/test/e2e/chrome/Dockerfile` (`libgconf-2-4`); current e2e network assumptions around `localhost:4444` and the default bridge |
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
  WSL/Linux proof, but the live browser-backed lane is still blocked here by
  the Selenium/Chrome build failure in `src/docker/test/e2e/chrome/Dockerfile`
  (`libgconf-2-4`) and the current `localhost:4444` / default bridge
  assumptions. That is a lane-confidence blocker, not an open stop-state
  implementation task.
- Native Windows Poetry validation: blocked on this machine because the local
  Python version is 3.13.12, outside the repo-supported `>=3.11,<3.13` range.
  If Windows host validation becomes necessary, it needs a supported Python
  environment first.
