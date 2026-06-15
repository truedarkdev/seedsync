# Integration Tracker

This document is the live, future-facing ledger for upstream integration work.

Use it to answer two questions quickly:
- are we fully processed through the last reviewed fork tip?
- where do we resume when new upstream commits appear?

This tracker is intentionally fork-first, not subject-first.
The detailed subject-by-subject history is no longer kept here.

## How To Use This Tracker

For each active fork, keep:
- the last fully processed upstream commit
- the fork tip seen at the last full review
- the last review date
- the current caught-up status
- a short summary of what is already integrated locally
- a short note for how to resume when new commits appear

Refresh rule:
- fetch remotes first
- compare the current fork tip to `Last fully processed upstream commit`
- if the tip moved, review only the new upstream commits after that commit
- once the new work is processed, update this tracker to the new fully processed tip

## Entry Template

```md
## <fork>
- Source branch: <fork>/<branch>
- Last fully processed upstream commit: <commit>
- Fork tip at last full review: <commit>
- Last full review date: <YYYY-MM-DD>
- Status: caught up through this tip | refresh needed
- Integrated so far: <short summary>
- Resume when new upstream appears: <short instruction>
- Notes: <optional>
```

## thejuran
- Source branch: `thejuran/master`
- Last fully processed upstream commit: `bcebdf8eaec5d3abf5586aad2278f2c77667cd71`
- Fork tip at last full review: `bcebdf8eaec5d3abf5586aad2278f2c77667cd71`
- Last full review date: `2026-03-29`
- Status: closing lane; final legacy commit sweep remaining before removal
- Integrated so far: conservative imports and local adaptations now cover the worthwhile reviewed work through `bcebdf8eaec5d3abf5586aad2278f2c77667cd71`, including the host-header guard, the requests lock refresh, the AutoQueue-to-Settings merge, and the `remote_scanner` quoting hardening. Broader auth/CSP/token-flow, dashboard restyle, Playwright/QEMU timing, and planning/release docs remained intentionally out of this refresh so the default SeedSync behavior stays conservative and recognizable.
- Resume when new upstream appears: fetch `thejuran/master`, review the remaining legacy commits in strict oldest-to-newest order, then remove this lane once that final sweep is complete.
- Notes:
  - This is now a closing legacy lane. `thejuran-arr` is the ongoing successor source, and `thejuran` should be removed from this tracker after the final old-fork commit sweep is fully processed.
  - This refresh has been folded back into the main tracker; the temporary frozen ledger is retired.
  - Deferred from this refresh: the broader auth/CSP/token bundle, dashboard visual restyles, Playwright/QEMU E2E timing churn, and release/planning bookkeeping.
  - Doc/planning artifacts were intentionally skipped, including the six doc-only commits from `00a0c86214261ad58592eb6978b71a948ae91e47` through `fa96fdf303b99919f10698511f12a3801d9f2cfe` and later phase-opening / completion docs.
  - `5c899615b5ce4ece819e766951efe7be10907892` was adapted locally for `api_token` config support and stronger config API redaction; `a29cae1108bf481181bc363903de4d8c6db765e3` was intentionally skipped as planning-only documentation.
  - Phase 48/49 hardening/runtime work split across local adaptations and deferrals: startup warnings (`cd07bfcf`), path-traversal guards and tests, transient SSH scan recovery (`04ed3ace76b760ef6263f22ec376fc97d55f9279`), plus covered-elsewhere items such as `7bf7fb2826b759bf1feb456a250e6d00cb35651c`, `f63e7cf5935433389acce7cc943859a834147ff4`, and `1eae2c56a9f2556d0ead2cafa8cbe93b7fd5349f`.
  - The CI/dependency cluster was treated as Angular-19-specific policy noise against the legacy Angular 4 baseline, with `cea63a27b502869241d704aabe266e18200d167c` split to a separate legacy dependency-audit task.
  - The Angular migration trio landed as `c5eb482bfbf3d8eab73d31f746ef795272a3505a` `intentionally skipped`, `db497146b623aaa860d641c24b78e18919215242` `needs new integration task`, and `3767adafbc3893ad85643de7b2b7212d8dc7b2e9` `needs subject reopen`; the migration-tail commits `c6318bc50b88ab1ffa978852193d61be321cc42a`, `ab74f8c53d9daf1e765315808bf995a6ea01974a`, `32387fca344f2b3c75eb9ab2e0f2cf4eceb58da1`, and `36c13b3f0acf5b9f0df738cd9964a44f31885771` closed the frozen range, and the `c6318bc50b88ab1ffa978852193d61be321cc42a` follow-up is now integrated locally via the Subject 21 note.

## thejuran-arr
- Source branch: `thejuran-arr/main`
- Last fully processed upstream commit: `unreviewed`
- Fork tip at last full review: `e9d1e2627b7492f5025c6a9e55236dcd5b7d23db`
- Last full review date: `pending`
- Status: refresh needed
- Integrated so far: none yet; this is the renamed `thejuran/seedsyncarr` history and it starts as a separate line, not as a continuation of the old `thejuran/seedsync` checkpoint.
- Resume when new upstream appears: fetch `thejuran-arr`, then review the history strictly oldest-to-newest from the first commit in this lane.
- Notes:
  - The old `thejuran` checkpoint `bcebdf8eaec5d3abf5586aad2278f2c77667cd71` remains the processed base for the legacy fork only and does not appear in this lane's history.
  - Local remote HEAD observed for this lane: `e9d1e2627b7492f5025c6a9e55236dcd5b7d23db`.

### Active subject reopens
- Subject 21 - Angular migration follow-up: `3767adafbc3893ad85643de7b2b7212d8dc7b2e9`

## rapidcopy
- Source branch: `rapidcopy/master`
- Last fully processed upstream commit: `dc9c68c37c43eba7487654dacf7c7b08f64eb12a`
- Fork tip at last full review: `dc9c68c37c43eba7487654dacf7c7b08f64eb12a`
- Last full review date: `2026-04-01`
- Status: caught up through this tip
- Integrated so far: locally useful rapidcopy ideas have already been adapted where they fit this fork, especially around path-pairs, UI workflow polish, packaging/runtime hardening, logs/files improvements, and targeted reliability fixes, while branding, theme-system, and other identity-shifting changes remain intentionally out.
- Resume when new upstream appears: fetch `rapidcopy`, then continue from the next commit after `dc9c68c37c43eba7487654dacf7c7b08f64eb12a` in strict oldest-to-newest order.
- Notes:
  - Completed frozen refresh on `2026-04-01`; the frozen range was `1b96fb80938d398d7fca701771f11c13df5a0bc7` through `dc9c68c37c43eba7487654dacf7c7b08f64eb12a`.
  - Per-commit explorer re-audit finalized the disputed frozen-range dispositions as `cb20dc899ccabce453c2ae4d44e9e0153f7a74ea` `intentionally skipped` because the current workflow shape has moved past that exact `if:` guard pattern, plus `acf1a0c64eb4becb14ffec3104613238bd8cbbd5` and `dc9c68c37c43eba7487654dacf7c7b08f64eb12a` as `needs new integration task` because both are stale failure-cleanup items that should only be revived on fresh repro.
  - The temporary frozen-refresh ledger was removed after all 13 commits in the pass received individual explorer audits, so the unresolved follow-up work now lives only in this tracker.
  - Commit `c766bd96366a1da7ffbf8f65df05a6bd904c3106` was adapted locally by hardening `lftp.queue()` command construction and adding targeted queue test coverage; WSL verification covered that adaptation.
  - Adapted rapidcopy commit `674992ac09857dc6fa8ca9642bd6a50597e2bb29` as a local `.select-all` header-checkbox alignment fix; did not take its Python `WebApp.stop()` hunk because local code already uses a consistent private stop flag implementation.
  - `thejuran` now has a new active frozen refresh ledger beyond its prior processed checkpoint.

## nitrobass24
- Source branch: `nitrobass24/develop`
- Full manifest rows: `956 total reachable commits`
- Baseline rows already in `origin/master`: `87` rows, `1-87`, through common base `ff2a1039935beccbbf7ec76134b41d2e91137742`
- Fork-unique audit rows: `869` rows, `88-956`, `ec38aaf6e6ca0ab2479fcd003d15679007101021` through `38d6ef22d36b6a75c164bc754bac9cd2842e8722`
- Initial audit frozen tip: `38d6ef22d36b6a75c164bc754bac9cd2842e8722`
- Last dispositioned fork-unique row: `167` (`c636dd68fd9f22a9d7cf09ca0ba8633f1eccca2a`)
- Status: active / in progress
- Manifest: [doc/integration-notes/nitrobass24-initial-audit.md](/mnt/c/Git/seedsync/doc/integration-notes/nitrobass24-initial-audit.md)
- Integrated so far: audit chunks `88-127` and `128-167` are fully dispositioned; rows `88`, `94`, `95`, `100`, `102`, `123`, `137`, and `165` are locally adapted in `89362f026db73c655d4a286c57c5abbe860c8fcb`, `1d30a9ec7c85a80801f8a04d2e56a6e3db269f95`, `391b171a0797d45745e5908d513181b7e97a8751`, `c5d042018043cc274603e075b166bfa6b519b354`, `5368c551be44634cf4178510ca15cda1700242d5`, and `edbdeac2ff7bb034fdd25700beef9523bf75816a`; row `142` has its remote-path/tilde-delete subfeature adapted in `edbdeac2ff7bb034fdd25700beef9523bf75816a`; rows `129`, `141`, `142`, and `143` remain in the active implementation queue.
- Resume when new upstream appears: run `git fetch --no-tags nitrobass24 --prune`, verify the branch tip, then continue from the active implementation queue before moving to row `168` in exact 40-row contiguous chunks, oldest-to-newest; rows `1-87` are baseline accounting rows, rows `88-167` are already dispositioned, and do not start the next chunk until every row in the current chunk has a disposition.
- Notes:
  - This repo is not GitHub-marked as a fork, but it is SeedSync-derived.
  - `remote.nitrobass24.tagOpt=--no-tags`; branch refs anchor this lane, and the pre-existing local `refs/tags/v1.0.0` is intentionally ignored.
  - `38d6ef22d36b6a75c164bc754bac9cd2842e8722` is the frozen tip only, not a processed checkpoint.
  - The common base with local `origin/master` is `ff2a1039935beccbbf7ec76134b41d2e91137742`; rows `1-87` are baseline accounting rows and rows `88-956` are the fork-unique audit workload.
  - Treat this as a selective source because it is modernization-heavy.
  - Chunk `88-127` is complete: rows `88`, `94`, `95`, `100`, `102`, and `123` are locally adapted, 0 `needs integration` rows remain, 3 `needs area reopen` rows (`92`, `93`, `122`), 1 `maintainer decision needed` row (`105`), 20 `intentionally skipped`, and 10 `covered elsewhere`.
  - Chunk `128-167` is dispositioned: 4 `already integrated`, 20 `covered elsewhere`, 10 `intentionally skipped`, 4 `needs integration` rows (`129`, `141`, `142`, `143`), 2 adapted rows (`137`, `165`), 0 `needs area reopen`, 0 `needs new integration task`, and 0 `maintainer decision needed`; row `142` remains queued because only its remote-path/tilde-delete subfeature is adapted so far.
  - Next nitrobass24 work: implement the remaining chunk `128-167` queue before opening row `168`; row `129` needs a separate scanfs architecture design because a literal amd64-only pin conflicts with multi-arch image artifacts while a target-platform default conflicts with the common x86_64 seedbox copy path, row `141` is remote shell-not-found messaging, row `142` still needs shell-detection/optional-password/config-sentinel follow-up after its remote-path/tilde-delete subfeature landed, and row `143` covers optional auto-delete-after-download behavior.
  - Follow-up: row `137` and the row `142` remote-path/tilde-delete subfeature are adapted locally in `edbdeac2ff7bb034fdd25700beef9523bf75816a`; security review accepted residual non-blocking risks around `rm -rf` being high-consequence and `~user` expansion targeting another remote home when configured.
  - Follow-up: row `165` access logging is adapted locally in `5368c551be44634cf4178510ca15cda1700242d5`; security review accepted the non-blocking tradeoff that normal-mode request forensics are reduced while default request metadata retention improves.
  - Follow-up: row `88`/`100` PUID/PGID support intentionally treats PUID/PGID and mounted paths as trusted operator-controlled Docker inputs; security review accepted residual non-blocking risks around existing principal reuse and reused-account home mutation.
  - Follow-up: row `102`/`123` compatibility is implemented for the Docker image scanfs build only; evaluate the separate `src/docker/build/deb/Dockerfile` scanfs packaging lane when deb packaging compatibility is in scope.

## Reference Notes
- Subject 21 user-facing conflict review: [doc/integration-notes/S21-user-facing-conflicts.md](/mnt/c/Git/seedsync/doc/integration-notes/S21-user-facing-conflicts.md)
  - The confirm-modal replacement and hardening follow-up from `c6318bc50b88ab1ffa978852193d61be321cc42a` is integrated locally.
