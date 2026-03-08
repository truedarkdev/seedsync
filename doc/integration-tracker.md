# Integration Tracker

This document is the live ledger for integration work across `thejuran`, `rapidcopy`, and future forks.

Use this file to track:
- what has been reviewed
- what has been integrated
- what is still pending
- what is already covered elsewhere
- what was skipped
- what new upstream work appeared after the last pass

This file is for moving state. The durable rules live in `AGENTS.md`.

## How To Use This Tracker

- Keep one section per integration subject.
- For each subject, keep one subsection per active source fork.
- Prefer recording a specific last reviewed upstream commit as the main resume marker.
- Add a reviewed range when the pass covered a bounded slice rather than a clean tip.
- Record candidates as you find them instead of waiting until the end.
- Record the resulting local commit for integrated items when available.
- Use `covered elsewhere` when the issue is already solved by another integrated change, by current base behavior, or by a cleaner local solution.
- Normal pending integration work stays here.
- Unresolved maintainer questions should also be recorded in `Pending Maintainer Decisions` in `AGENTS.md`.
- Keep this file summary-level. If a subject gets dense, move detailed notes into `doc/integration-notes/S<subject-number>-<fork>.md` and link to them here.

## Entry Template

Use this structure for each fork subsection:

```md
### <fork-name>

- State: not started | in progress | reviewed | needs refresh
- High-risk: yes | no
- Integration base: <branch> @ <commit>
- Source branch: <branch>
- Fork tip seen at pass start: <commit>
- Reviewed in this pass: <range or n/a>
- Last reviewed upstream commit (inclusive): <commit>
- Resume from next: <commit or range>
- New upstream since last pass: <range, commits, or none>
- Pass date: <YYYY-MM-DD or n/a>

Integrated:
- <upstream commit or batch> -> <local commit if available> : <short note>

Pending:
- <upstream commit or batch> : <short note>

Covered elsewhere:
- <upstream commit or batch> : <short note>

Skipped:
- <upstream commit or batch> : <short reason if important>

Maintainer decisions:
- <link or short note, or none>

Verification:
- tests run: <short note>
- manual checks: <short note>
- status: verified | partially verified | not verified yet

Notes:
- <anything important for resuming later, including cross-subject references>
```

`Resume from next` is the next starting point for review work after the recorded pass. It should normally be the first unreviewed commit after the inclusive `Last reviewed upstream commit`.

`New upstream since last pass` should normally stay `n/a` or `none` until a later refresh pass happens after fetching remotes.

## Subject 1 - Documentation And Maintainer Notes

### thejuran

- State: reviewed
- High-risk: no
- Integration base: master @ 38455d4f7fbb902a781f59d2cc93d3824ae231e9
- Source branch: thejuran/master
- Fork tip seen at pass start: a8561cdc318460de32de082e3cf33f6b6a0093cb
- Reviewed in this pass: origin/master..thejuran/master (Subject 1 filtered)
- Last reviewed upstream commit (inclusive): a8561cdc318460de32de082e3cf33f6b6a0093cb
- Resume from next: none at current tip
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- adapted from `8fbf770` -> working tree: add GitHub issue templates, PR template, and a repo-neutral security policy
- adapted from `77b7ee1`, `7ad47dc`, `11aca99`, and `18ad6ce` -> working tree: refresh repository-facing docs links, modernize README copy, and document Docker SSH key usage without importing fork-specific branding
- local documentation corrections: update MkDocs repo metadata and fix developer guide repository, docs deployment, and registry examples for this fork

Pending:
- none

Covered elsewhere:
- `77b7ee1`: README screenshot update covered by the adapted README refresh in this pass
- `18ad6ce`: Docker SSH key guidance covered by the updated installation docs in this pass

Skipped:
- CI-only `.github/workflows/master.yml` documentation-adjacent commits: out of scope for Subject 1 and better handled under Subject 2
- `.planning/*` process artifacts and `doc/logs_55588916360.zip`: not durable maintainer or user documentation for this repo
- `4cbdaa5`: GLIBC 2.29+/Ubuntu 20.04+ requirement note depends on packaging/runtime changes that belong under compatibility and packaging subjects, not Subject 1
- `2ce3852`: broader GitHub Pages site overhaul mixes content refresh with docs-site structure and workflow changes; current pass took the portable content-level improvements only
- `c71fc80`, `5208aab`, `e3a074e`, `93bba55`: CLAUDE.md maintainer-note updates are fork-specific and not portable to this repo's `AGENTS.md` policy model
- `6c08a77`, `a9ce3dc`, `fab7829`: changelog/install version-history updates skipped because this repo does not yet maintain a stable MkDocs changelog page and those pinned version references would age poorly without a release-note strategy

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check` on all Subject 1 files
- manual checks: reviewed imported template/policy content and adapted documentation links/content for repo fit
- status: partially verified

Notes:
- Started from a dirty worktree with pre-existing line-ending churn in documentation files; this pass normalized the touched Subject 1 files to make the content changes reviewable.

### rapidcopy

- State: reviewed
- High-risk: no
- Integration base: master @ 38455d4f7fbb902a781f59d2cc93d3824ae231e9
- Source branch: rapidcopy/master
- Fork tip seen at pass start: c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269
- Reviewed in this pass: origin/master..rapidcopy/master (Subject 1 filtered)
- Last reviewed upstream commit (inclusive): c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269
- Resume from next: none at current tip
- New upstream since last pass: none
- Pass date: 2026-03-08

Integrated:
- no direct rapidcopy cherry-picks in this pass

Pending:
- none

Covered elsewhere:
- `18ad6ce`: equivalent user-facing Docker SSH key guidance was integrated through the adapted local docs refresh
- minor README cleanup commits such as `5522203` and `ba93749`: superseded by the broader README refresh completed in this pass

Skipped:
- `AGENTS.md` session notes and `doc/MODERNIZATION-PLAN.md` planning artifacts: not durable repo policy
- rebrand-heavy documentation such as `6d59994`: conflicts with the repo goal of remaining recognizably SeedSync
- workflow-heavy documentation commits such as `94b8f07` and `5df693d`: better handled under Subject 2 or later code-aligned subjects
- broad README and developer-doc rewrites such as `b5373c9`, `c392a29`, `e4814be`, `5d2edbe`, and `34698f2`: useful in parts, but too tied to RapidCopy branding, feature set, or modernization claims that do not match this branch yet

Maintainer decisions:
- none

Verification:
- tests run: `git diff --check` on all Subject 1 files
- manual checks: reviewed candidate commit summaries, spot-checked representative diffs, and reconciled overlapping README/doc ideas into the local adapted docs refresh
- status: partially verified

Notes:
- Rapidcopy has many README and maintainer-note commits, but a large share are tied to rebranding, CI migration, or ephemeral session tracking rather than durable Subject 1 documentation improvements.
- `poetry` was not available in the shell, so MkDocs rendering was not verified locally in this pass.

## Subject 2 - Tests, CI, And Verification Assets

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 3 - Dependencies And Build Tooling

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 4 - Packaging And Install

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 5 - Compatibility And Platform Support

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 6 - Security And Hardening

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 7 - About, Modal, And Shared UI Components

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 8 - Settings UI

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 9 - Logs UI

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 10 - Config And Settings Backend

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 11 - Model, Serialization, And Web API

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 12 - Files Page And File Operations UI

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 13 - SSH And Remote Command Handling

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 14 - Transfers And LFTP

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 15 - Scanning

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 16 - Auto Queue

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 17 - Extraction And Archive Handling

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 18 - Core Controller

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 19 - Dashboard And Main Layout UI

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 20 - Cleanup, Deletion, And File Safety

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

## Subject 21 - Cross-Cutting UX Or Workflow Conflicts

### thejuran

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: thejuran/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none

### rapidcopy

- State: not started
- High-risk: no
- Integration base: n/a
- Source branch: rapidcopy/master
- Fork tip seen at pass start: n/a
- Reviewed in this pass: n/a
- Last reviewed upstream commit (inclusive): n/a
- Resume from next: n/a
- New upstream since last pass: n/a
- Pass date: n/a

Integrated:
- none

Pending:
- none

Covered elsewhere:
- none

Skipped:
- none

Maintainer decisions:
- none

Verification:
- tests run: none
- manual checks: none
- status: not verified yet

Notes:
- none
