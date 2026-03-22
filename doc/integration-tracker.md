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
- Last fully processed upstream commit: `36c13b3f0acf5b9f0df738cd9964a44f31885771`
- Fork tip at last full review: `36c13b3f0acf5b9f0df738cd9964a44f31885771`
- Last full review date: `2026-03-22`
- Status: caught up through this tip
- Integrated so far: conservative imports and local adaptations now cover the worthwhile reviewed work across docs, CI, packaging, compatibility, security hardening, web/API hardening, files page behavior, SSH/runtime handling, transfers/LFTP, scanning, extraction, controller hardening, dashboard updates, and cleanup safety while leaving explicitly rejected or out-of-direction changes out. The frozen thejuran review range is now fully processed through `36c13b3f0acf5b9f0df738cd9964a44f31885771`.
- Resume when new upstream appears: fetch `thejuran/master`, compare against `36c13b3f0acf5b9f0df738cd9964a44f31885771`, and review only commits newer than that frozen tip in strict oldest-to-newest order.
- Notes:
  - Fully processed through `36c13b3f0acf5b9f0df738cd9964a44f31885771`; `thejuran/master` is caught up at that tip.
  - Doc/planning artifacts were intentionally skipped, including the six doc-only commits from `00a0c86214261ad58592eb6978b71a948ae91e47` through `fa96fdf303b99919f10698511f12a3801d9f2cfe` and later phase-opening / completion docs.
  - `5c899615b5ce4ece819e766951efe7be10907892` was adapted locally for `api_token` config support and stronger config API redaction; `a29cae1108bf481181bc363903de4d8c6db765e3` was intentionally skipped as planning-only documentation.
  - Phase 48/49 hardening/runtime work split across local adaptations and deferrals: startup warnings (`cd07bfcf`), path-traversal guards and tests, transient SSH scan recovery (`04ed3ace76b760ef6263f22ec376fc97d55f9279`), plus covered-elsewhere items such as `7bf7fb2826b759bf1feb456a250e6d00cb35651c`, `f63e7cf5935433389acce7cc943859a834147ff4`, and `1eae2c56a9f2556d0ead2cafa8cbe93b7fd5349f`.
  - The CI/dependency cluster was treated as Angular-19-specific policy noise against the legacy Angular 4 baseline, with `cea63a27b502869241d704aabe266e18200d167c` split to a separate legacy dependency-audit task.
  - The Angular migration trio landed as `c5eb482bfbf3d8eab73d31f746ef795272a3505a` `intentionally skipped`, `db497146b623aaa860d641c24b78e18919215242` `needs new integration task`, and `3767adafbc3893ad85643de7b2b7212d8dc7b2e9` `needs subject reopen`; the migration-tail commits `c6318bc50b88ab1ffa978852193d61be321cc42a`, `ab74f8c53d9daf1e765315808bf995a6ea01974a`, `32387fca344f2b3c75eb9ab2e0f2cf4eceb58da1`, and `36c13b3f0acf5b9f0df738cd9964a44f31885771` closed the frozen range, with `c6318bc50b88ab1ffa978852193d61be321cc42a` left in `### Active subject reopens`.

### Active subject reopens
- Subject 21 - Angular migration follow-ups: `3767adafbc3893ad85643de7b2b7212d8dc7b2e9`, `c6318bc50b88ab1ffa978852193d61be321cc42a`

## rapidcopy
- Source branch: `rapidcopy/master`
- Last fully processed upstream commit: `d9a3e882dd2680f7db146de092de3fa586ea1d86`
- Fork tip at last full review: `d9a3e882dd2680f7db146de092de3fa586ea1d86`
- Last full review date: `2026-03-18`
- Status: caught up through this tip
- Integrated so far: locally useful rapidcopy ideas have already been adapted where they fit this fork, especially around path-pairs, UI workflow polish, packaging/runtime hardening, logs/files improvements, and targeted reliability fixes, while branding, theme-system, and other identity-shifting changes remain intentionally out.
- Resume when new upstream appears: fetch `rapidcopy`, diff new commits after `d9a3e882dd2680f7db146de092de3fa586ea1d86`, and classify each new commit as integrate, adapt, reject, or defer before starting implementation.
- Notes: adapted rapidcopy commit `674992ac09857dc6fa8ca9642bd6a50597e2bb29` as a local `.select-all` header-checkbox alignment fix; did not take its Python `WebApp.stop()` hunk because local code already uses a consistent private stop flag implementation. `thejuran` remains at its existing processed checkpoint with no newer commits.

## Reference Notes
- Subject 21 user-facing conflict review: [doc/integration-notes/S21-user-facing-conflicts.md](/mnt/c/Git/seedsync/doc/integration-notes/S21-user-facing-conflicts.md)
