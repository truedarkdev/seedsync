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
| Python web/auth backend | Healthy | Final WSL verifier at `tmp/pytest/runs/20260720-113859`: 57 nodeid batches across 91 files, 1701 selected/executed, 1700 passed, 0 failed, 1 intentional stress skip, and 0 harness errors; live SSH/LFTP/controller and 7z/archive coverage exercised. Docker remote `127.0.0.1:1234` and app HTTP `127.0.0.1:8800` were healthy. Automated Playwright loaded `/bootstrap` with HTTP 200, title `SeedSync browser access`, visible local handoff/remembered-browser/API-key controls, and no page or console errors; no claim/data mutation occurred. Screenshot: `tmp/pytest/browser-final-20260720/bootstrap.png`. | Fresh as of 2026-07-20. |
| Concurrent SSE/browser responsiveness | Healthy | Final WSL web/server verifier passed 94 tests. Rebuilt Docker image `sha256:ef1085979c962f6396b865182e35cfcbd05241c271d2b636edf7eb0ed87c0542` admitted 12 authenticated SSE streams with initial events, rejected the 13th promptly with 503, kept normal and invalid-auth requests responsive, recovered capacity after disconnect and restart, and remained responsive with empty/partial clients. Three simultaneous independent Playwright contexts completed the real API-key remember flow, reached the dashboard, and established HTTP 200 streams with initial status events. | Fresh as of 2026-08-03. Detailed evidence: `tmp/pytest/api-key-local-dns-responsiveness-verifier/`; graceful quiet-client release was bounded at about 16 seconds in the saturated probe. |
| Historical logs and AI diagnostics | Healthy | Final verifier: 48 passed with 2 platform skips; Angular log service and page specs passed 8/8 each. The admin-only `seedsync.log-history.v1` API, bounded scan/record/response output, canonical/path-aware redaction, private JSONL storage, rollover/restart behavior, and Docker/API storage flow passed; Playwright confirmed compact controls, ERROR filtering, and the accessible failure state. | Fresh as of 2026-07-12; maintainer-approved UI and independent reviewer/security gates passed. |
| Python unit/backend | Healthy | Current focused backend evidence covers controller/model-update, scanner/delete/extract race handling, SSH child cleanup, WSGI request timeouts, auto-queue, move-completion ordering, and remote-scanner/config redaction; the Docker extraction suite passed 28 tests with 7-Zip 25.01 and RAR codecs, and the Python config/scan_fs slice passed 44 tests using the local extraction path; upstream official/prebuilt 7zz packaging remains intentionally skipped as standalone rows 418/419. | Fresh as of 2026-07-12: focused extraction/config/controller slices are green; broader backend evidence includes 145 focused tests with 56 environment-gated skips plus Docker API, Playwright dashboard, and SSE checks. Broader WSL full-suite evidence remains available from 2026-04-08. |
| Global Strict Pyright | Healthy | The checked-in production baseline is strict: all 99 production files and all fifteen scoped strict configurations report zero diagnostics. Final focused WSL evidence includes `scan_fs` 2/2, Seedsync 46/46, and remote scanner 37/37; actual Python 3.8.10 also compiled the self-contained scanner and preserved its recursive JSON tree/sidecar contract. | Fresh as of 2026-07-13. Reviewer and security gates passed. Docker confirmed SSH/`scan_fs` success, remote scanning, and LFTP queue/model activity; fresh-auth Playwright confirmed claim/remember `201` plus actual Dashboard, Logs, and Settings content before the original app was restored healthy. CI enforcement remains separate platform-topology work, not a gap in row 561. |
| Queue authority and stop/resume | Healthy | Final controller Queue/STOP slice passed 40 tests, model-updater passed 7, and adjacent command coverage passed 25. In the rebuilt Docker app, two authenticated Queue requests inside the stale/coalescing window produced exactly one LFTP `queue mirror`; immediate STOP produced one `queue --delete`, returned HTTP 200, and left the exact path-pair-aware fixture default/non-stoppable. | Fresh as of 2026-07-12; reviewer and security gates passed, authenticated Playwright/API evidence is in `tmp/playwright/queue-authority/live-queue-stop.png`, and retries after the bounded unobserved-acknowledgement window intentionally retain at-least-once semantics. |
| Migrated file status reconciliation | Healthy | WSL verifier coverage passed the model-builder, updater, serializer, AutoQueue, and controller reconciliation slices, including the exact 15-test persistence/restart/pruning rerun. Dockerized Angular passed all 111 task-owned specs. A rebuilt candidate image served `/bootstrap` with the workspace serializer, and Playwright proved Local Only, Deleted, empty-directory, and zero-byte behavior across four rows, three isolated same-basename path-pair identities, and a scoped `model-updated` transition with no browser errors. | Fresh as of 2026-08-03. Durable browser evidence is in `tmp/pytest/verifier-status-reconciliation/playwright-matrix.json`; the broad Angular run's unrelated modal fixture failure remains the bounded lane gap below. |
| Outbound notifications | Healthy | Provider-aware auth/redaction, SSRF-safe pinned transport, bounded queue/retry behavior, download/extraction/successful-remote-delete completion events, and default-off exactly-once `download_start` with authoritative stale-state pruning are covered for Generic Webhook and Apprise API. | Fresh as of 2026-07-11: focused backend/controller and Angular passes plus independent reviewer/security passes are green; exact live `lscr.io/linuxserver/apprise-api` and signed Generic Webhook delivery were confirmed, and Docker/Playwright passed on full image `sha256:1766e2412ee052d49f837f8868df1cd72b654b85f71e2ee3b59b2e20a199f1bf`. |
| Python integration/controller | Healthy | The final WSL verifier at `tmp/pytest/runs/20260720-113859` completed 57 nodeid batches across 91 files with 1701 selected/executed (1700 passed, 0 failed, 1 intentional stress skip, 0 harness errors), including live SSH/LFTP/controller and archive-backed coverage. | Fresh as of 2026-07-20; Docker remote `127.0.0.1:1234` and app HTTP `127.0.0.1:8800` were healthy. |
| Python spawned process/runtime | Healthy | Guarded `spawn`, queue-based child logging, spawn-safe breadcrumbs, process lifecycle cleanup, and real scanner/extract/validate/delete startup are covered by a clean 331-test focused WSL worker slice and a 249-test verifier slice. A rebuilt/recreated Docker app remained healthy through repeated scans and API checks with no pickle, teardown, or logger errors; authenticated Playwright dashboard and Settings checks passed. | Fresh as of 2026-07-12. Two isolated scanner `resource_tracker` warnings appeared only in the verifier test harness and were not reproduced in the worker slice or live runtime. |
| LFTP credential process isolation | Healthy | Final WSL verifier passed 174 tests with 34 environment skips. Real SFTP password and key authentication and real verified-CA FTPS authentication, listing, and protected data transfer passed with the password absent from child argv/environment and retained artifacts; FTPS wrong-password and untrusted-certificate probes failed closed with sanitized output and clean child exit. Fresh image `sha256:99adfb3aa15df2854e940141dc8aeae3f0413fd4203139edf39e2317bdcd6d83` passed first claim, authenticated config/status/stream checks, Settings inspection, and remembered-browser restart in Playwright with no browser errors. | Fresh as of 2026-08-03. The config-file-only legacy argv rollback is omitted from serialized config/UI, blocked through the API, and documented as process-visible; detailed artifacts are under `tmp/pytest/lftp-credential-process-argv-verifier/`. |
| Final-move failure/retry | Healthy | Terminal move-failure surfacing and retry, performed-move-only success metadata, no-op Downloaded preservation, lifecycle reset/pruning, and contained symlink-safe path resolution have focused backend/frontend coverage plus live Docker/API and Playwright browser proof. | Fresh as of 2026-07-12: the focused controller/status deadlock, move-retry, and delete slice passed 28 tests; the Docker app was healthy with status/scan success. Independent reviewer and security gates passed; known duplicate-root extracted-marker and native-Windows separator expectations are unrelated and remain outside this lane. |
| WSL backend stop-state integration | Healthy | Targeted WSL/Linux stop-state slice for the backend contract. | Fresh as of 2026-03-30. |
| Host Angular/Karma full-suite proof | Healthy for host comparison only | Node 24 host Angular/Karma suite completes successfully. | Comparison evidence only; not the supported closure lane. Fresh as of 2026-04-01. |
| Dockerized Angular build/test | Healthy with bounded baseline gap | The latest Dockerized task slice passed 111/111 on Linux Chrome 151. The broad collection reached 487/488; its sole failure is the independently reproduced, untouched `modal.service` fixture gap. Prior responsive file-list specs remain green at 5/5, 13/13, and 29/29, with authenticated Playwright coverage from 360px-1440px. | Supported frontend closure lane. Task-owned reconciliation coverage and rebuilt-app Playwright are fresh as of 2026-08-03; keep the unrelated modal-service gap separate from candidate conclusions. |
| Angular files-page large-list virtualization | Healthy | Focused Angular virtualization coverage passed 65/65; the production build and rebuilt Docker/current bundle served authenticated Playwright against 1,218 logical files. Page-size persistence, measured-height mount/window bounds, scrolling, details, mobile/no-overflow behavior, action interactions, and model refresh all passed. | Maintainer-approved; reviewer and verifier passed. Fresh as of 2026-07-26. Durable evidence: `tmp/virtualized-files-final-verifier/final/live-clone/summary.json`. |
| Angular mobile file-row progress layout | Healthy | Final verifier passed 68/68 focused Angular tests, production build, rebuilt Docker/current assets, and authenticated Playwright with 1,220 files at 360/390/760/899/900/1440px. Details, long names, ARIA semantics, selection/bulk actions, virtualization remeasurement, and no-overflow/error checks passed. | Maintainer-approved; reviewer reported no findings; desktop layout preserved. Fresh as of 2026-07-26. Durable evidence: `tmp/mobile-progress-final-verifier/final/proven-helper/summary.json`. |
| v0.8.6 historical upgrade and recovery | Corrective image published; own-install retest pending | The retained `ship-20260801-r54` transfer, extraction, restore, reboot-parity, and audit evidence remain current. The live Unraid rollout exposed two bounded post-migration handoff failures in the earlier image: an indefinitely pending proxy-held `/bootstrap` readiness request, followed by false completed-lineage failure after the ephemeral first-claim proof expired. The durable live receipt and retained backup prove the migration itself completed. The correction bounds and retries readiness requests, accepts only the exact empty expired-proof lifecycle (while rejecting consumed or incoherent histories), reads bounded old-image histories up to 1 MiB, and coalesces unchanged future snapshots. Focused Angular tests passed 25/25; focused WSL auth/lineage tests are green; and the rebuilt full image restarted the retained completed/unclaimed lab with a 205 KiB history, redirected `/dashboard` to `/bootstrap`, rendered `SeedSync browser access` in Playwright without browser errors, kept `/server/status` at 401, and stopped duplicate 30-second history growth. | Fresh as of 2026-08-02. First-run migration and administrator claim remain intentionally open to any reachable client until the first successful claim; unfinished setup must stay on a trusted network. The corrected image is published, and the maintainer-owned movie retest remains the rollout gate. |
| Docker release publication | Corrected image published and registry-checked | `truedarkdev/seedsync:0.9.0` and `truedarkdev/seedsync:latest` resolve to the same OCI index, `sha256:753f8e194a7b115cbddaa7fed5a1de77b06d61bbe2426f307f3307ebbf6daa52`, for `linux/amd64`, `linux/arm64`, and `linux/arm/v7`, with provenance attestations. Every runtime image carries version `0.9.0`, source `https://github.com/truedarkdev/seedsync`, and revision `bdc2d652f86da2f668cc70531dc73cc299bd35e4`. A disposable container pulled by the published digest returned `/bootstrap` 200 and unauthenticated `/server/status` 401; Playwright rendered the first-run claim page from that container. | Fresh as of 2026-08-02. Both release tags moved atomically. The maintainer-owned Unraid movie retest is still the live rollout gate. |
| Dependency tooling / Dependabot | Healthy with bounded gaps | Dependabot v2 structural/exact validation passed; Poetry check passed; healthy Docker runtime plus authenticated Playwright dashboard/settings evidence remain current. GitHub server-side validation is unavailable; npm coverage intentionally targets direct `/src/angular/package.json` only because the generated lockfile is untracked. | Fresh as of 2026-07-12; GitHub Actions automation and Angular transitive lockfile/`npm ci` hardening remain deferred. |
| Angular 21 standalone migration | Healthy | Final verifier confirmed the remapped standalone architecture in a fresh exact Docker image: configured remote scan, two-browser remembered-auth flow, dashboard/settings/logs/about routes, `/server/status` 200, and `/server/stream` 200; production has `bootstrapApplication`/standalone components with no `AppModule`/`NgModule`. | Maintainer-approved visual behavior; reviewer/security passes completed. Fresh as of 2026-07-10. |
| Docker runtime/process supervision | Healthy | The config-root contract remains current in strict and legacy non-root modes. The superseded published image `sha256:0e754c774ddcd4bd078902b1e224e90f5193ad62e76d72f3be8560980cf9d652` passed unknown numeric identity `99:100`: owner-private nss_wrapper files supplied the runtime passwd/group record, `ssh -G` succeeded, and the exact image remained healthy through the no-allowlist proxy claim flow. Earlier `1101:1102`, idempotence, normal-controller, path-pair, and retained-backup evidence remains current because the corrected publication does not alter the entrypoint/config-root implementation. | Fresh as of 2026-08-02. The maintainer-owned live movie swap remains the own-install rollout gate; first-run browser claiming no longer requires proxy-source configuration. |
| Angular sidebar path-pair refresh UI | Healthy | Targeted Angular spec plus live Docker/Playwright proof for immediate path-pair visibility on load and reload. | Fresh as of 2026-04-04. |
| Angular dashboard path-pair startup UI | Healthy | Targeted Angular spec plus live Docker/Playwright proof for correct dashboard startup routing and render, direct path-pair cards, responsive 3/2/1 layout, and ETA visibility on active cards only. | Fresh as of 2026-04-04. |
| Path-pair settings and AutoQueue overrides | Healthy | `cae3ad3e4` covers disabled legacy Server/Local Directory and global AutoQueue controls when pairs are enabled, zero-enabled restoration, shared-pattern behavior, and stale-buffer transitions; `91f921407` covers startup legacy-path fallback. Focused Angular/Python tests plus Docker/Playwright checks passed; authenticated Playwright Settings confirms the Remote Python Path default copy and inline-validation/xfer_verify wording. | Fresh as of 2026-07-12. |
| Path-pair marker canonicalization | Healthy | Final WSL verifier slices passed 537 tests with 5 environment skips; the Dockerized Python slice passed 47 tests with 1 Docker-specific warning assertion deselected. A rebuilt candidate converted all five identity-bearing persistence collections from v0.8.6/legacy inputs to three exact canonical path-pair IDs, resolved count collisions by maximum value, and remained semantically identical after restart. Live Docker/Playwright used the real path-pairs API and remembered auth to prove three same-basename rows stayed isolated through a scoped update with no browser errors. | Fresh as of 2026-08-03. The separately gated multi-path integration probe had 3 environment-prerequisite skips; direct persistence and live browser matrices cover the task acceptance path. |
| Downloaded-time file sorting | Healthy | Final WSL verifier coverage passed 519 tests with 5 environment skips, and Dockerized Angular passed all 81 task-owned specs. A rebuilt candidate preserved exact canonical timestamps across restart, kept legacy missing times unknown, and passed scoped refresh, stale-prune, and successful remote-delete cleanup checks. Live Playwright clicked Downloaded Newest and Oldest across three same-basename path pairs, kept unknown values last in both directions, and re-sorted only the updated identity with no browser errors. | Fresh as of 2026-08-03. Candidate image `sha256:83ead004fbb51fbc3724627c1ebb7bb61e59a04d88483c0fbf2fa0ae41138098`; durable browser evidence is in `tmp/pytest/files-sort-downloaded-time-verifier/playwright-downloaded-sort.json`. The broad Docker Angular run's unrelated modal fixture failure remains the bounded lane gap above. |
| Path-pair persistence integrity | Healthy with a bounded durability gap | Final reviewed/verifier evidence covers coordinated `ControllerPersist` snapshot/mutation transactions, including AutoQueue marker clearing; 230 controller tests plus exact concurrency/AutoQueue/refresh regressions pass, and the Docker app was rebuilt and remained healthy through the persistence interval with authenticated Playwright bootstrap/Settings/VerifyPair evidence. | Ordinary writes use same-directory atomic replacement. Crash-level rename durability lacks parent-directory fsync, and replacement inode metadata/ACL preservation is not explicitly defined. Fresh as of 2026-07-19. |
| Angular API access UI/service lanes | Healthy | Targeted Angular specs plus live Docker/Playwright proof for API Access create, edit, rotate, revoke, legacy disable/clear, and revoked-key cleanup flows. | Fresh as of 2026-04-04. |
| Legacy Protractor/e2e | Blocked | Not exercised: the Makefile e2e lane requires `SEEDSYNC_DEB` or `STAGING_VERSION`, and the required credentials/tokens are absent. | Exclude this lane from repository-wide green claims; this is an execution blocker, not a test failure. Fresh as of 2026-07-20. |
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

- Legacy Protractor/e2e: blocked/not exercised because the Makefile lane requires
  `SEEDSYNC_DEB` or `STAGING_VERSION` plus credentials/tokens that are absent;
  exclude it from repository-wide green claims.
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
- A broader controller/model-builder/scanner-process probe still reproduces
  seven unchanged expectation failures around downloaded/extracted state and
  targeted scan queues. They are not regressions from the rapidcopy/tini
  candidate, but the broad controller lane needs a separate expectation audit
  before it can be treated as wholly green.
- Full Angular collection/runs currently include an unrelated baseline
  `modal.service` failure; the row890/918 focused ViewFile and file-list specs
  remain green.
- On Windows-backed WSL `/mnt/c` DrvFS, artifact modes may be mount-derived
  rather than POSIX-enforced; use Windows ACLs or a Linux-owned location when
  POSIX privacy guarantees are required.
