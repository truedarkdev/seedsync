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
- Last fully processed upstream commit: `20db99f5af5fce18d92f440d147935e80dd4fb72`
- Fork tip at last full review: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Last full review date: `2026-03-21`
- Status: refresh needed
- Integrated so far: conservative imports and local adaptations now cover the worthwhile reviewed work across docs, CI, packaging, compatibility, security hardening, web/API hardening, files page behavior, SSH/runtime handling, transfers/LFTP, scanning, extraction, controller hardening, dashboard updates, and cleanup safety while leaving explicitly rejected or out-of-direction changes out.
- Resume when new upstream appears: resume the current refresh from `e3badf3c2365d72f79dbb628c5c3a2dbbdc580e7` after the phase-48 block dispositions (`7063a03b8e4cd212b597adbb2827720c872a6130` marked `needs new integration task`, `e670f5d436089766732175f907b1fcb993991ddd` adapted locally, and the two phase-48 docs commits intentionally skipped), then continue oldest-to-newest through the remaining commits after `20db99f5af5fce18d92f440d147935e80dd4fb72`.
- Notes: `thejuran/master` is now at `36c13b3f0acf5b9f0df738cd9964a44f31885771` with 21 newer commits beyond the fully processed checkpoint. The six doc-only commits from `00a0c86214261ad58592eb6978b71a948ae91e47` through `fa96fdf303b99919f10698511f12a3801d9f2cfe` were reviewed and dispositioned as `intentionally skipped` because they are upstream planning artifacts already represented by this repo's own integration ledger/tracker workflow. Upstream `5c899615b5ce4ece819e766951efe7be10907892` was adapted locally with minimal diffs to add `api_token` config support plus stronger config API redaction, and upstream `a29cae1108bf481181bc363903de4d8c6db765e3` was dispositioned as `intentionally skipped` as planning-only documentation. Phase-48 startup warnings from upstream `e670f5d436089766732175f907b1fcb993991ddd` were adapted conservatively in local commit `cd07bfcf` (advisory warnings only, no webhook subsystem reconstruction). Upstream `7063a03b8e4cd212b597adbb2827720c872a6130` was dispositioned as `needs new integration task` because its webhook payload-size hardening targets webhook paths that are not present locally. Local adaptation of upstream `7f025d78` + `b5afc96c` landed in `f4efff8e` as the persist-permissions hardening batch. Local adaptation of upstream `97bb7d01` + `b7c5a40f` + `e99c1ec3` landed in `caa3ebb8` as the restart-route and SSH-topology-redaction batch; because that batch also aligned the local e2e restart caller early, upstream `1eae2c56a9f2556d0ead2cafa8cbe93b7fd5349f` will likely disposition as `covered elsewhere` when reached in order.

## rapidcopy
- Source branch: `rapidcopy/master`
- Last fully processed upstream commit: `d9a3e882dd2680f7db146de092de3fa586ea1d86`
- Fork tip at last full review: `d9a3e882dd2680f7db146de092de3fa586ea1d86`
- Last full review date: `2026-03-18`
- Status: caught up through this tip
- Integrated so far: locally useful rapidcopy ideas have already been adapted where they fit this fork, especially around path-pairs, UI workflow polish, packaging/runtime hardening, logs/files improvements, and targeted reliability fixes, while branding, theme-system, and other identity-shifting changes remain intentionally out.
- Resume when new upstream appears: fetch `rapidcopy`, diff new commits after `d9a3e882dd2680f7db146de092de3fa586ea1d86`, and classify each new commit as integrate, adapt, reject, or defer before starting implementation.
- Notes: adapted rapidcopy commit `674992ac09857dc6fa8ca9642bd6a50597e2bb29` as a local `.select-all` header-checkbox alignment fix; did not take its Python `WebApp.stop()` hunk because local code already uses a consistent private stop flag implementation. `thejuran` remains at its existing processed checkpoint with no newer commits.
