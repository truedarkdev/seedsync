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
- Last fully processed upstream commit: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Fork tip at last full review: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Last full review date: `2026-03-13`
- Status: caught up through this tip
- Integrated so far: conservative imports and local adaptations now cover the worthwhile reviewed work across docs, CI, packaging, compatibility, security hardening, web/API hardening, files page behavior, SSH/runtime handling, transfers/LFTP, scanning, extraction, controller hardening, dashboard updates, and cleanup safety while leaving explicitly rejected or out-of-direction changes out.
- Resume when new upstream appears: fetch `thejuran`, diff new commits after `a8561cdc318460de32de082e3cf33f6b6a0093cb`, and evaluate them directly against current local behavior before opening any new integration batch.
- Notes: treat this commit as the current fully processed checkpoint, not as a claim that every earlier commit was directly integrated verbatim.

## rapidcopy
- Source branch: `rapidcopy/master`
- Last fully processed upstream commit: `d9a3e882dd2680f7db146de092de3fa586ea1d86`
- Fork tip at last full review: `d9a3e882dd2680f7db146de092de3fa586ea1d86`
- Last full review date: `2026-03-18`
- Status: caught up through this tip
- Integrated so far: locally useful rapidcopy ideas have already been adapted where they fit this fork, especially around path-pairs, UI workflow polish, packaging/runtime hardening, logs/files improvements, and targeted reliability fixes, while branding, theme-system, and other identity-shifting changes remain intentionally out.
- Resume when new upstream appears: fetch `rapidcopy`, diff new commits after `d9a3e882dd2680f7db146de092de3fa586ea1d86`, and classify each new commit as integrate, adapt, reject, or defer before starting implementation.
- Notes: adapted rapidcopy commit `674992ac09857dc6fa8ca9642bd6a50597e2bb29` as a local `.select-all` header-checkbox alignment fix; did not take its Python `WebApp.stop()` hunk because local code already uses a consistent private stop flag implementation. `thejuran` remains at its existing processed checkpoint with no newer commits.
