# Testing Confidence

This document is the tracked, canonical home for local test-suite confidence
notes in this repo.

It records:

- which lanes have fresh local evidence
- what each lane proves
- the minimum evidence ladder to use for each change class
- durable artifact guidance for exact-result runs
- the current open gaps that remain future work

It is intentionally conservative. A lane being listed here as healthy does not
mean the whole repo is fully covered, only that the specific evidence below was
gathered and is still useful as a baseline.

Freshness rule:

- lane statuses are point-in-time evidence, not evergreen certification
- revalidate a lane after major environment changes, after a relevant
  regression, or when the evidence date is no longer recent enough for the
  decision at hand

## Current Lane Inventory

| Lane | Status | What it proves | Reference / entrypoint | Evidence |
| --- | --- | --- | --- | --- |
| Python unit/backend | Healthy as of 2026-03-30 | The targeted backend controller path still passes on the supported WSL/Linux Python lane. | `src/python` pytest lane; targeted nodeid `tests/unittests/test_controller/test_controller.py::TestController::test_refresh_path_pairs_rebuilds_runtime_state_and_forces_rescan` | 2026-03-30 local WSL run |
| Python integration/controller | Healthy as of 2026-03-30 | The controller integration path still passes on the supported WSL/Linux lane. | `src/python` pytest lane; targeted nodeid `tests/integration/test_controller/test_controller.py::TestController::test_initial_model` | 2026-03-30 local WSL run |
| Frontend host harness | Healthy as of 2026-03-30 | The Angular/Karma host harness still boots and runs the smoke suite successfully. | `src/angular` headless Karma lane; smoke run `291/291 SUCCESS` | 2026-03-30 local host run |
| Docker/browser/e2e | Unknown at runtime | The Compose files still parse, but the live browser-backed runtime path was not exercised today. | `src/docker/test/e2e/compose.yml` plus `src/docker/test/e2e/compose-remote-dev.yml` | 2026-03-30 local compose parse only |
| Native Windows Poetry path | Blocked on this machine | The local Windows Python environment is outside the repo-supported Poetry range, so this host path cannot be treated as a valid confidence lane here. | `src/python` Poetry environment | 2026-03-30 local host check; Python 3.13.12 vs supported `>=3.11,<3.13` |

## Minimum Evidence Ladder

Use the smallest lane set that honestly proves the change, then widen only
when the change crosses layers or runtime surfaces.

| Change class | Minimum evidence | Notes |
| --- | --- | --- |
| Backend-only | One targeted unit test or narrow controller/integration slice that covers the touched backend path | If the change touches controller state, filesystem state, or path-pair handling, prefer a controller integration slice over unit-only proof. |
| Frontend-only | Headless Angular/Karma smoke over the touched area | If the change affects browser rendering, served assets, or Docker-built frontend output, add a live UI/runtime check instead of relying on host-only smoke. |
| Mixed API/UI | Backend proof plus frontend smoke | Add browser/runtime evidence if the user-visible path depends on the live app shell. |
| Transfer / stop-resume / scan / path-pair regression | Targeted backend proof plus WSL/Linux controller integration | If the change can be exercised through the Docker-served app, do not close it without live Docker validation. |

The ladder is a floor, not a ceiling. Broader regressions can and should add
more evidence, but the minimum should always be visible and specific.

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

## Open Gaps

These items are deliberately left as future work. They are not missing
documentation.

- Docker/browser/e2e runtime lane: still unproven at runtime. Today only the
  Compose configuration parse was exercised, so the browser-backed lane still
  needs a real live run before it can be called healthy.
- Controller/transfer regression breadth: the current evidence is targeted and
  narrow. Wider stop-resume, path-remap, and transfer-state scenarios still need
  broader coverage before this lane can be treated as fully confidence-building
  for those behaviors.
- Native Windows Poetry validation: blocked on this machine because the local
  Python version is 3.13.12, outside the repo-supported `>=3.11,<3.13` range.
  If Windows host validation becomes necessary, it needs a supported Python
  environment first.

## Closure Note

This task is complete as a documentation and process deliverable because the
tracked canonical artifact now exists and the open gaps are explicitly named
above.

The gaps remain open future work, but they are no longer hidden in a local
planning note.
