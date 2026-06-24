# Testing Confidence

This document is a current-state confidence index for local testing lanes.
It records the best current evidence only, not a historical run log.

Maintenance rule:

- keep current status, freshness, minimum evidence, durable artifact pointers,
  and known gaps
- do not accumulate rerun chronology, long artifact lists, or investigation
  history here
- put detailed run history, failures, and sequence notes in task notes,
  tracker entries, or saved artifacts, then summarize only the durable outcome
  here
- if the best evidence changes, update the lane row instead of appending more
  narrative

Lane status shorthand:

- Healthy: fresh evidence exists and matches the current environment
- Unknown: the lane exists, but there is no current proof in this workspace
- Blocked: the lane cannot be treated as proof on this machine
- Flaky: the lane is unreliable enough that it should not stand alone

Freshness rule:

- lane statuses are point-in-time evidence, not evergreen certification
- refresh a lane after toolchain, image, browser, Compose, repo, or runtime
  changes that can affect it
- if evidence is stale for the decision at hand, downgrade the lane until it
  is rerun

## Current Status Index

| Lane | Status | Best current evidence | Freshness / notes |
| --- | --- | --- | --- |
| Python web/auth backend | Healthy | Green WSL scripted Python full-suite rerun, including the formerly failing bootstrap trust cases, plus live Docker and Playwright proof for bootstrap, claim, remember, and cookie/session behavior. | Fresh as of 2026-04-08; keep detailed run history in task notes or artifacts. |
| Python unit/backend | Healthy | Current focused backend evidence covers controller/model-update, auto-queue, and row `727` web security-header/auth behavior. | Fresh row `727` verifier/final evidence is from 2026-06-24: WSL web/auth slice passed, diff hygiene passed, Docker rebuild/start and HTTP reachability passed, live headers were checked on `/bootstrap` and `/does-not-exist`, and Playwright smoke/screenshot evidence was captured at `tmp/verifier-browser-smoke.png`. Broader WSL full-suite evidence remains available from 2026-04-08. |
| Python integration/controller | Healthy | Green WSL scripted Python full-suite rerun, including controller and web-handler integration coverage. | Fresh as of 2026-04-08. |
| WSL backend stop-state integration | Healthy | Targeted WSL/Linux stop-state slice for the backend contract. | Fresh as of 2026-03-30. |
| Host Angular/Karma full-suite proof | Healthy for host comparison only | Node 24 host Angular/Karma suite completes successfully. | Comparison evidence only; not the supported closure lane. Fresh as of 2026-04-01. |
| Dockerized Angular build/test | Healthy | Dockerized Angular verifier path passes after the current frontend compatibility updates. | Supported frontend closure lane. Fresh as of 2026-04-01. |
| Angular sidebar path-pair refresh UI | Healthy | Targeted Angular spec plus live Docker/Playwright proof for immediate path-pair visibility on load and reload. | Fresh as of 2026-04-04. |
| Angular dashboard path-pair startup UI | Healthy | Targeted Angular spec plus live Docker/Playwright proof for correct dashboard startup routing and render, direct path-pair cards, responsive 3/2/1 layout, and ETA visibility on active cards only. | Fresh as of 2026-04-04. |
| Angular API access UI/service lanes | Healthy | Targeted Angular specs plus live Docker/Playwright proof for API Access create, edit, rotate, revoke, legacy disable/clear, and revoked-key cleanup flows. | Fresh as of 2026-04-04. |
| Legacy Protractor/e2e | Flaky | Docker compose startup and targeted legacy slices still pass, but the broader lane is not closure evidence. | Keep separate from the supported Angular closure path. |
| Native Windows Poetry path | Blocked | Local Windows Python is outside the repo-supported Poetry range. | Do not treat this host path as proof here. |

## Minimum Evidence Ladder

Use the smallest lane set that honestly proves the change, then widen only
when the change crosses layers or runtime surfaces.

| Change class | Minimum evidence | Notes |
| --- | --- | --- |
| Backend-only | One targeted unit test or narrow controller/integration slice | Prefer controller integration when the change touches controller state, filesystem state, or path-pair handling. |
| Frontend-only | Targeted frontend spec/component coverage | Add browser smoke if the behavior is only observable in the DOM/runtime. |
| Runtime-visible UI behavior | Host Angular/Karma smoke plus browser or Docker/live proof | Host smoke is supportive evidence only. |
| Mixed API/UI | Backend proof plus frontend/browser proof | Add browser/runtime evidence when the live app shell decides the outcome. |
| Transfer / stop-resume / scan / path-pair regression | Targeted backend proof plus WSL/Linux controller integration | Require live Docker validation before closing if the change is user-visible there. |

The ladder is a floor, not a ceiling.

## Freshness And Lifecycle

- treat lane health as working status, not permanent certification
- if a lane fails, flakes, or is blocked by environment or tooling drift,
  downgrade it immediately and note the blocker elsewhere if detail is needed
- if a task depends on runtime-visible proof and the live lane has not been
  exercised in the current environment, do not treat host smoke as a
  substitute
- when a lane is no longer the best proof path for a task, keep it listed but
  stop relying on it as closure evidence

## Gap Escalation

- if the minimum lane set does not prove the task, record the gap here or in
  the tracker instead of leaving it in memory
- if the same gap keeps showing up across tasks, convert it into follow-up
  work
- if live Docker or browser proof is required but blocked, name the blocker and
  treat it as lane confidence debt, not unfinished product implementation

## Durable Artifacts

When exact outcomes matter, write the run to `tmp/pytest/` at the repo root
and keep the artifact name descriptive.

- prefer `--junitxml=tmp/pytest/<lane-name>.xml` for pass/fail-sensitive runs
- keep the full log with `tee tmp/pytest/<lane-name>.log` when terminal output
  may truncate useful detail
- put batch-run artifacts under `tmp/pytest/runs/<timestamp>/` when a scripted
  runner already creates that layout
- save the nodeid or lane label in the filename so later evidence can be
  matched back to the exact command quickly

## Known Gaps

- Legacy Protractor/e2e: still flaky and not closure evidence. Keep it
  separate from the supported Angular closure path until a fresh full-lane
  rerun is available.
- Native Windows Poetry validation: blocked on this machine because the local
  Python version is outside the repo-supported range. Use a supported Python
  environment first if Windows host validation becomes necessary.
- CI/Docker/live-suite hardening from the 845-849 row slice has verifier/final
  validation on the Docker-served app and Docker Python harness. The default
  SSH/LFTP lane keeps mock/unit coverage active while live cases stay gated by
  `SEEDSYNC_LIVE_SSH_TESTS`.
- Scanner directory-symlink coverage is Linux/WSL-only on this host because
  Windows lacks the required symlink privilege. Keep the new symlink tests in
  the Linux/WSL verifier path.
