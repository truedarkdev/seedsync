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
- Last dispositioned fork-unique row: `327` (`5492b3aa97b8fe70abacb400993880917258cf54`)
- Status: active / in progress
- Manifest: [doc/integration-notes/nitrobass24-initial-audit.md](/mnt/c/Git/seedsync/doc/integration-notes/nitrobass24-initial-audit.md)
- Integrated so far: audit chunks `88-127`, `128-167`, `168-207`, `208-247`, `248-287`, and `288-327` are fully dispositioned; rows `88`, `94`, `95`, `100`, `102`, `123`, `129`, `137`, `141`, `142`, `143`, `165`, `179`, `183`, `185`, `186`, `193`, `195`, `249`, `250`, `251`, `253`, `256`, `258`, `260`, `267`, `268`, `279`, `295`, `297`, `300`, `301`, `303`, `304`, `309`, and `310` are locally adapted across `89362f026db73c655d4a286c57c5abbe860c8fcb`, `1d30a9ec7c85a80801f8a04d2e56a6e3db269f95`, `391b171a0797d45745e5908d513181b7e97a8751`, `c5d042018043cc274603e075b166bfa6b519b354`, `5368c551be44634cf4178510ca15cda1700242d5`, `edbdeac2ff7bb034fdd25700beef9523bf75816a`, `ff1499f4b41f9b6abb61d5fc446992aebcdd554e`, `e99ac88cd5bc33a7ac1a1494d47af09d24c36906`, `9f57de252b156587da48913e29809cf3c04724a3`, `0e189f2ccb9ddd3378347a56a47620d02e9001ea`, `e9b50b39638a6fd7327d2480393a986e3cf2ead2`, `b2049ad13e0fb98eca019bb764c1fa31ab725adc`, `e55e6a4402b6b715cab09a1311d14c47186b8988`, `a9d02db2feaf03923207fc394356eef126c3c6e0`, `f7d52a4e537013fd52e8dfaf70f5885a954cddaa`, `1df4fa8601a08dd2a0a960cde19b2ad4dd3efae1`, `aa46ccd3814de04198e41ecdabf1d1d1d6354161`, `ae841fb1df6c88a8d8fd60641abcca5681588b6f`, `9d9c795a`, `cedadd93`, `46cd02fe`, `f1a407c5`, `c1a3a77c`, `518422ee`, and `034832ed`; chunk `168-207` currently has `15 already integrated`, `16 covered elsewhere`, `3 intentionally skipped`, `0 needs area reopen`, `0 needs new integration task`, `6 adapted rows`, and `0 maintainer decision needed`.
- Chunk `248-287` is complete: `0 already integrated`, `17 covered elsewhere`, `7 intentionally skipped`, `0 needs integration/adapt locally`, `3 needs area reopen`, `3 needs new integration task`, 10 adapted rows (`249`, `250`, `251`, `253`, `256`, `258`, `260`, `267`, `268`, `279`), and `0 maintainer decision needed`.
- Chunk `288-327` is dispositioned: `0 already integrated`, `18 covered elsewhere`, `10 intentionally skipped`, `1 needs integration/adapt locally`, `3 needs area reopen`, `0 needs new integration task`, 8 adapted rows (`295`, `297`, `300`, `301`, `303`, `304`, `309`, `310`), and `0 maintainer decision needed`.
- Queue / status: chunk `288-327` implementation queue row is `327`. Security-hardening area reopen rows `302`, `306`, and `307` should be handled as one cohesive security lane rather than cherry-picked individually. Chunk `248-287` implementation queue is empty. Rows `309` and `310` Docker/package dependency trimming are adapted locally in `034832ed`. Row `304` eager settings-service initialization and directly required config/autoqueue/logging hardening are adapted locally in `518422ee`. Row `303` Angular CSP-friendly build output is adapted locally in `c1a3a77c`. Row `300` restricted remote scanfs install fallback behavior is adapted locally in `f1a407c5`. Rows `295`, `297`, and `301` explicit-UMASK/runtime-permissions behavior is adapted locally in `46cd02fe`. Row `267` delete-action and remote-delete cleanup behavior is adapted locally in `cedadd93`; row `268` Docker UMASK environment support is adapted locally in `9d9c795a`. Rows `256` and `258` extraction dispatch failure reporting and staging fallback archive lookup are adapted locally in `ae841fb1df6c88a8d8fd60641abcca5681588b6f`; pair-specific staging fallback coverage remains a separate follow-up if needed. Rows `253` and `260` unrar/RAR5 Docker packaging support are adapted locally in `aa46ccd3814de04198e41ecdabf1d1d1d6354161` without upstream's deb12-specific pin or changelog edit. Row `249` parser and controller lifecycle/zombie-handling behavior is adapted locally across `f7d52a4e537013fd52e8dfaf70f5885a954cddaa` and `22908425df8bd2cf586f39990b56bdc581dd2e2a`. Row `250` incomplete staged-directory guarding is adapted locally in `1df4fa8601a08dd2a0a960cde19b2ad4dd3efae1`. Row `279` SSH password log redaction is adapted locally in `a9d02db2feaf03923207fc394356eef126c3c6e0`; a separate non-blocking security follow-up remains for `general.webhook_secret` config API serialization. Rows `273`, `282`, and `286` relate to CI/build workflow topology; rows `275` and `287` relate to docs/UMASK/Unraid docs path; row `285` relates to Docker/runtime packaging optimization; rows `261`, `277`, `292`, and `321` are website-only dependency rows and are intentionally skipped because this workspace lacks the website subtree.
- Resume/refresh: current frozen manifest is dispositioned through row `327`. Continue implementation queue before starting row `328`. A later refresh is needed because `nitrobass24/develop` advanced to `ab7fb1aea644712d50c2fabf19ae48744185c2d4` after the original frozen tip `38d6ef22d36b6a75c164bc754bac9cd2842e8722`.
- Notes:
  - This repo is not GitHub-marked as a fork, but it is SeedSync-derived.
  - `remote.nitrobass24.tagOpt=--no-tags`; branch refs anchor this lane, and the pre-existing local `refs/tags/v1.0.0` is intentionally ignored.
  - `38d6ef22d36b6a75c164bc754bac9cd2842e8722` is the frozen tip only, not a processed checkpoint.
  - The common base with local `origin/master` is `ff2a1039935beccbbf7ec76134b41d2e91137742`; rows `1-87` are baseline accounting rows and rows `88-956` are the fork-unique audit workload.
  - Treat this as a selective source because it is modernization-heavy.
  - Chunk `88-127` is complete: rows `88`, `94`, `95`, `100`, `102`, and `123` are locally adapted, 0 `needs integration` rows remain, 3 `needs area reopen` rows (`92`, `93`, `122`), 1 `maintainer decision needed` row (`105`), 20 `intentionally skipped`, and 10 `covered elsewhere`.
  - Chunk `128-167` is complete: 4 `already integrated`, 20 `covered elsewhere`, 10 `intentionally skipped`, 0 `needs integration` rows, 6 adapted rows (`129`, `137`, `141`, `142`, `143`, `165`), 0 `needs area reopen`, 0 `needs new integration task`, and 0 `maintainer decision needed`.
  - Chunk `168-207` is complete: 15 `already integrated`, 16 `covered elsewhere` (`172` and `187` folded into the future theming/visualization track), 3 `intentionally skipped` (including row `184` Settings layout), 0 `needs area reopen`, 0 `needs new integration task`, 6 adapted rows (`179`, `183`, `185`, `186`, `193`, `195`), and 0 `maintainer decision needed`.
  - Chunk `208-247` is complete: 27 `already integrated`, 13 `covered elsewhere`, 0 `intentionally skipped`, 0 `needs area reopen`, 0 `needs new integration task`, 0 adapted rows, and 0 `maintainer decision needed`; no local adaptation commit is needed for this chunk because it is already represented in local history.
  - Follow-up: row `249` is adapted locally across `f7d52a4e537013fd52e8dfaf70f5885a954cddaa` and `22908425df8bd2cf586f39990b56bdc581dd2e2a`, and row `251` is adapted locally in `f7d52a4e537013fd52e8dfaf70f5885a954cddaa`; reviewer/verifier accepted rangeless chunk headers, missing chunk data lines, preservation of following non-chunk status lines, focused parser coverage, direct parser probe, Docker/Playwright smoke, post-startup controller AppError propagation, preservation of degraded-startup behavior, full `test_seedsync.py`, HTTP 200, and Playwright bootstrap rendering.
  - Follow-up: row `250` incomplete staged-directory guarding is adapted locally in `1df4fa8601a08dd2a0a960cde19b2ad4dd3efae1`; reviewer/verifier accepted the known-remote-child guard for both recent-snapshot and scan-only staging completion paths, preservation of complete-child and unknown-size-child behavior, focused model-builder coverage, Docker build/start, HTTP 200, and Playwright smoke.
  - Follow-up: rows `253` and `260` RAR5/unrar packaging support are adapted locally in `aa46ccd3814de04198e41ecdabf1d1d1d6354161`; reviewer/verifier accepted full `unrar` availability in the runtime Docker image and Python test image, omission of upstream's deb12-specific pin on the local bullseye base, Docker app rebuild, HTTP 200, and Playwright bootstrap rendering. Actual RAR5 archive extraction through the app/patool path remains an unexercised non-blocking validation gap, and Debian package dependency policy remains a separate follow-up if needed.
  - Follow-up: rows `256` and `258` extraction failure reporting and staging fallback archive lookup are adapted locally in `ae841fb1df6c88a8d8fd60641abcca5681588b6f`; reviewer/verifier/commit-reviewer accepted controller-visible `extract_failed` breadcrumbs, identity-aware active extraction filtering that preserves the active scanner tuple contract, fallback archive lookup through the staging root, focused controller/dispatch/process tests, Docker rebuild, HTTP 200, and Playwright bootstrap rendering. Pair-specific staging fallback remains a separate follow-up if needed.
  - Follow-up: row `279` SSH password log redaction is adapted locally in `a9d02db2feaf03923207fc394356eef126c3c6e0`; reviewer/security/verifier accepted the focused context-log redaction, targeted unit coverage, direct runtime redaction probe, and Docker/Playwright smoke. Security noted a separate non-blocking gap where `general.webhook_secret` may still need config API serialization redaction.
  - Next nitrobass24 work: continue the remaining chunk `248-287` implementation queue (`267`, `268`) before opening row `288` in the next exact 40-row contiguous audit oldest-to-newest.
  - Follow-up: row `183` is adapted locally in `e55e6a4402b6b715cab09a1311d14c47186b8988` as a first-class `Smart Status` sort option, with `Status` preserved as the legacy sort and `Status Reverse` preserved as the reverse mode. Reviewer and verifier accepted the coherent status-header cycle, Smart-vs-legacy ordering, stable Sort button/menu widths, and focused Angular unit coverage.
  - Follow-up: rows `185`, `186`, `193`, and `195` transfer-state stability are adapted locally in `b2049ad13e0fb98eca019bb764c1fa31ab725adc`; reviewer/verifier accepted pending-completion proof before marker persistence, stopped/partial transfer guards, duplicate completion side-effect prevention, active/local model-builder separation, active-overlay cache invalidation, focused regression tests, and rebuilt Docker/browser smoke.
  - Follow-up: row `179` LFTP socket-buffer support is adapted locally in `e9b50b39638a6fd7327d2480393a986e3cf2ead2`; reviewer/security/verifier accepted bounded byte-size validation, blank-to-disable settings behavior, sink-side LFTP setter validation, Angular settings normalization, and live Docker/browser set-and-clear coverage with runtime restored to `8M`.
  - Follow-up: row `129` scanfs architecture is adapted locally in `0e189f2ccb9ddd3378347a56a47620d02e9001ea`; reviewer/security/verifier accepted the default `linux/amd64` remote scanfs platform with explicit `SCANFS_PLATFORM` override, Makefile allowlist guard, Docker image/deb packaging coverage, and residual non-blocking floating-base-image reproducibility risk.
  - Follow-up: row `142` optional-password/config-sentinel subfeature is adapted locally in `9f57de252b156587da48913e29809cf3c04724a3`; reviewer accepted the softened helper copy, verifier confirmed backend/Angular/live Docker/browser behavior, and security accepted the non-blocking existing follow-up that config-set responses still echo raw submitted values.
  - Follow-up: row `143` auto-delete-after-download is adapted locally in `e99ac88cd5bc33a7ac1a1494d47af09d24c36906` as a default-off opt-in setting; security/reviewer gates required and accepted transition-only delete scheduling, no startup or pattern-backfill remote deletion, no reconciliation-only extracted deletion, and reuse of the existing `DELETE_REMOTE` command path.
  - Follow-up: row `141` and the row `142` shell-detection/scanner-install subfeature are adapted locally in `ff1499f4b41f9b6abb61d5fc446992aebcdd554e`; security review accepted residual low non-blocking risk around remediation-text disclosure/copy-paste guidance and commit-readiness recorded the non-blocking diagnostic-breadth caveat for exact login-shell-shaped generic SSH failures.
  - Follow-up: row `137` and the row `142` remote-path/tilde-delete subfeature are adapted locally in `edbdeac2ff7bb034fdd25700beef9523bf75816a`; security review accepted residual non-blocking risks around `rm -rf` being high-consequence and `~user` expansion targeting another remote home when configured.
  - Follow-up: row `165` access logging is adapted locally in `5368c551be44634cf4178510ca15cda1700242d5`; security review accepted the non-blocking tradeoff that normal-mode request forensics are reduced while default request metadata retention improves.
  - Follow-up: row `88`/`100` PUID/PGID support intentionally treats PUID/PGID and mounted paths as trusted operator-controlled Docker inputs; security review accepted residual non-blocking risks around existing principal reuse and reused-account home mutation.
  - Follow-up: row `102`/`123` compatibility is implemented for the Docker image scanfs build only; evaluate the separate `src/docker/build/deb/Dockerfile` scanfs packaging lane when deb packaging compatibility is in scope.

## Reference Notes
- Subject 21 user-facing conflict review: [doc/integration-notes/S21-user-facing-conflicts.md](/mnt/c/Git/seedsync/doc/integration-notes/S21-user-facing-conflicts.md)
  - The confirm-modal replacement and hardening follow-up from `c6318bc50b88ab1ffa978852193d61be321cc42a` is integrated locally.
