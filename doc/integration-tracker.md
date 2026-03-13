# Integration Tracker

This document is the live, future-facing ledger for integration work across
`thejuran`, `rapidcopy`, and future forks.

Use it to answer three questions quickly:
- what we have already integrated
- how far each subject review reached upstream
- where to resume when new upstream commits appear

Keep this file lean:
- record the last reviewed upstream commit and resume point for each subject
- summarize landed work at a high level instead of preserving commit-by-commit history
- record only live pending work or refresh needs
- move dense one-off notes into `doc/integration-notes/` only when needed

Global sources:
- `thejuran`: `thejuran/master`
- `rapidcopy`: `rapidcopy/master`

## Entry Template

```md
## Subject <n> - <name>

### <fork>
- State: not started | in progress | reviewed | needs refresh
- Last reviewed upstream commit (inclusive): <commit>
- Resume from next: <commit, range, or note>
- Pass date: <YYYY-MM-DD>
- Integrated so far: <high-level summary>
- Pending when resuming: <none or short note>
- Notes: <optional high-level reminder>
```

Refresh rule:
- after fetching remotes, compare the current fork tip against `Last reviewed upstream commit (inclusive)`
- if the tip moved, keep the existing summary and update only the sections needed for the new review

## Subject 1 - Documentation And Maintainer Notes

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: compare new documentation commits after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Pass date: `2026-03-08`
- Integrated so far: repo-neutral documentation refreshes landed, including issue/PR templates, README cleanup, and Docker SSH guidance without importing fork branding.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Resume from next: compare new documentation commits after `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Pass date: `2026-03-08`
- Integrated so far: no separate rapidcopy docs batch was needed; useful path-pair documentation ideas were adapted locally without taking RapidCopy branding or broader rewrite churn.
- Pending when resuming: none.

## Subject 2 - Tests, CI, And Verification Assets

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: compare new test and CI commits after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Pass date: `2026-03-08`
- Integrated so far: handler integration tests were aligned to current HTTP methods, pytest coverage/timeout support was added, and low-risk GitHub Actions token/version updates landed.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Resume from next: compare new test and CI commits after `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Pass date: `2026-03-08`
- Integrated so far: no separate rapidcopy-only CI batch was needed; overlapping verification improvements were absorbed through other subject work.
- Pending when resuming: none.

## Subject 3 - Dependencies And Build Tooling

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: compare new dependency and build-tooling commits after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Pass date: `2026-03-08`
- Integrated so far: the conservative toolchain refresh landed, including modern Poetry metadata, current Bottle/Python support, and the Angular/Node compatibility path needed to keep legacy builds running.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Resume from next: compare new dependency and build-tooling commits after `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Pass date: `2026-03-08`
- Integrated so far: broad modernization from rapidcopy was not taken wholesale; the needed compatibility pieces were adapted into the local toolchain instead.
- Pending when resuming: none.

## Subject 4 - Packaging And Install

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: compare new packaging commits after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Pass date: `2026-03-08`
- Integrated so far: Docker and deb packaging were hardened so Angular assets and `scanfs` build correctly, runtime images stay compatible on current bases, and SSH defaults remain conservative.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Resume from next: compare new packaging commits after `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Pass date: `2026-03-08`
- Integrated so far: no direct rapidcopy packaging lane was carried separately; the useful packaging and runtime ideas were folded into local Docker and deb fixes.
- Pending when resuming: none.

## Subject 5 - Compatibility And Platform Support

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: compare new compatibility commits after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Pass date: `2026-03-08`
- Integrated so far: conservative platform support landed across Docker/browser/test compatibility, GLIBC verification, OpenSSH test behavior, and the active Ubuntu 20.04 deb lane.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Resume from next: compare new compatibility commits after `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269`
- Pass date: `2026-03-08`
- Integrated so far: compatibility ideas that matched this fork were adapted locally; no separate rapidcopy compatibility backlog remained at last review.
- Pending when resuming: none.

## Subject 6 - Security And Hardening

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new security commits after `a8561cd`
- Pass date: `2026-03-08`
- Integrated so far: low-controversy hardening landed first, including config/API redaction, safer runtime defaults, and conservative web/API protections without introducing broader auth or control-plane churn.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6`
- Resume from next: compare new security commits after `c65ddf6`
- Pass date: `2026-03-08`
- Integrated so far: overlapping hardening work is already reflected through local security batches; no distinct rapidcopy-only security slice remained at last review.
- Pending when resuming: none.

## Subject 7 - About, Modal, And Shared UI Components

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new shared-UI commits after `a8561cd`
- Pass date: `2026-03-08`
- Integrated so far: lifecycle cleanup for long-lived subscriptions landed and the About page was updated to point to the maintained fork without importing the more opinionated redesigns.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6`
- Resume from next: compare new shared-UI commits after `c65ddf6`
- Pass date: `2026-03-08`
- Integrated so far: no separate rapidcopy shared-UI batch was needed beyond overlaps already handled through the local modal and shell decisions.
- Pending when resuming: none.

## Subject 8 - Settings UI

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new settings UI commits after `a8561cd`
- Pass date: `2026-03-08`
- Integrated so far: the existing settings UI was kept conservative; only compatible cleanup and settings exposure work that matched shipped backend behavior landed locally.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6`
- Resume from next: compare new settings UI commits after `c65ddf6`
- Pass date: `2026-03-08`
- Integrated so far: no rapidcopy settings redesign was taken; local settings remain aligned to the backend actually shipped on this branch.
- Pending when resuming: none.

## Subject 9 - Logs UI

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new logs UI commits after `a8561cd`
- Pass date: `2026-03-08`
- Integrated so far: logs page lifecycle fixes, waiting-state handling, malformed-log resilience, and clearer search/filter wording landed conservatively.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c65ddf6`
- Resume from next: compare new logs UI commits after `c65ddf6`
- Pass date: `2026-03-08`
- Integrated so far: broader rapidcopy logs expansion was not imported; only low-risk behavior and UX polish already adapted locally matter here now.
- Pending when resuming: none.

## Subject 10 - Config And Settings Backend

### thejuran
- State: reviewed
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new config-backend commits after `a8561cd`
- Pass date: `2026-03-08`
- Integrated so far: parser compatibility, path-pair-aware config handling, and conservative backend settings support have already landed locally.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new config-backend commits after `6ce7c19`
- Pass date: `2026-03-08`
- Integrated so far: rapidcopy config/backend ideas are either already integrated locally or intentionally left with their owning feature subjects.
- Pending when resuming: none.

## Subject 11 - Model, Serialization, And Web API

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new web/API commits after `a8561cd`
- Pass date: `2026-03-08`
- Integrated so far: low-risk REST semantics, focused handler coverage, SSE hardening, and bounded single-action callback behavior have landed conservatively.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new web/API commits after `6ce7c19`
- Pass date: `2026-03-08`
- Integrated so far: the rapidcopy-side API overlap is either already present locally or deliberately deferred to broader controller/feature subjects.
- Pending when resuming: none.

## Subject 12 - Files Page And File Operations UI

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new files-page commits after `a8561cd`
- Pass date: `2026-03-08`
- Integrated so far: the files page now includes conservative bulk actions, POST/DELETE transport alignment, stable sorting, pagination, dropdown/toolbar visibility fixes, and disabled-state/action guards.
- Pending when resuming: optional polish only; no known required gap from the reviewed range.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new files-page commits after `6ce7c19`
- Pass date: `2026-03-08`
- Integrated so far: useful rapidcopy file-list behavior such as percentage/reporting fixes and narrow UI guard rails has already been adapted locally.
- Pending when resuming: none.

## Subject 13 - SSH And Remote Command Handling

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new SSH/runtime commits after `a8561cd`
- Pass date: `2026-03-09`
- Integrated so far: remote command handling and Docker SSH defaults were hardened conservatively, including runtime host-key behavior and test-environment compatibility fixes.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new SSH/runtime commits after `6ce7c19`
- Pass date: `2026-03-09`
- Integrated so far: no separate rapidcopy SSH lane remained beyond the local hardening already in place.
- Pending when resuming: none.

## Subject 14 - Transfers And LFTP

### thejuran
- State: needs refresh
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new transfer and LFTP commits after `a8561cd`
- Pass date: `2026-03-09`
- Integrated so far: downloaded-state correctness, bounded waits, EOF/timeout containment, warning-level timeout logging, and transfer parser hardening have landed in focused local batches.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new transfer and LFTP commits after `6ce7c19`
- Pass date: `2026-03-09`
- Integrated so far: the useful rapidcopy transfer reliability slices were adapted locally; broader workflow changes were intentionally left out.
- Pending when resuming: none.

## Subject 15 - Scanning

### thejuran
- State: reviewed
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new scanning commits after `a8561cd`
- Pass date: `2026-03-09`
- Integrated so far: the Linux `st_ctime` fallback and companion scanning/test hardening are already covered locally.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new scanning commits after `6ce7c19`
- Pass date: `2026-03-09`
- Integrated so far: no distinct rapidcopy scanning gap remained after the local scanning follow-ups landed.
- Pending when resuming: none.

## Subject 16 - Auto Queue

### thejuran
- State: reviewed
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new AutoQueue commits after `a8561cd`
- Pass date: `2026-03-09`
- Integrated so far: the substantive AutoQueue fixes from thejuran were integrated in earlier backend and Angular batches, and the subject was closed without a known remaining gap.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new AutoQueue commits after `6ce7c19`
- Pass date: `2026-03-09`
- Integrated so far: rapidcopy AutoQueue overlap is already reflected through local subject work and later UI hardening.
- Pending when resuming: none.

## Subject 17 - Extraction And Archive Handling

### thejuran
- State: reviewed
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new extraction commits after `a8561cd`
- Pass date: `2026-03-09`
- Integrated so far: extraction dispatch thread safety, archive test stabilization, and focused overwrite/extract reliability fixes are already integrated.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new extraction commits after `6ce7c19`
- Pass date: `2026-03-09`
- Integrated so far: no distinct rapidcopy extraction lane remained once the conservative local extraction fixes landed.
- Pending when resuming: none.

## Subject 18 - Core Controller

### thejuran
- State: reviewed
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new controller commits after `a8561cd`
- Pass date: `2026-03-09`
- Integrated so far: controller hardening now includes bounded waits, memory/logging safeguards, stream containment, and targeted test coverage for the adopted behavior.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new controller commits after `6ce7c19`
- Pass date: `2026-03-09`
- Integrated so far: rapidcopy controller overlap was absorbed through local controller and web/API hardening rather than a separate direct import lane.
- Pending when resuming: none.

## Subject 19 - Dashboard And Main Layout UI

### thejuran
- State: reviewed
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new dashboard/layout commits after `a8561cd`
- Pass date: `2026-03-09`
- Integrated so far: dashboard and shell fixes that matched current SeedSync behavior landed conservatively, including path-pair-aware stats and narrow layout/lifecycle improvements.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new dashboard/layout commits after `6ce7c19`
- Pass date: `2026-03-09`
- Integrated so far: the useful dashboard workflow improvements are already present locally; rebrand and broader presentation shifts were left out.
- Pending when resuming: none.

## Subject 20 - Cleanup, Deletion, And File Safety

### thejuran
- State: reviewed
- Last reviewed upstream commit (inclusive): `a8561cd`
- Resume from next: compare new cleanup and deletion commits after `a8561cd`
- Pass date: `2026-03-09`
- Integrated so far: cleanup and deletion behavior was reviewed conservatively, and the useful file-safety hardening already landed without broad workflow changes.
- Pending when resuming: none.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `6ce7c19`
- Resume from next: compare new cleanup and deletion commits after `6ce7c19`
- Pass date: `2026-03-09`
- Integrated so far: no separate rapidcopy cleanup lane remained once the local file-safety work was in place.
- Pending when resuming: none.

## Subject 21 - Cross-Cutting UX Or Workflow Conflicts

### thejuran
- State: reviewed
- Last reviewed upstream commit (inclusive): `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Resume from next: compare new cross-cutting UX commits after `a8561cdc318460de32de082e3cf33f6b6a0093cb`
- Pass date: `2026-03-10`
- Integrated so far: user-facing improvements were only taken when they preserved the SeedSync default; broader restyles and product-direction shifts remained optional or rejected.
- Pending when resuming: none.
- Notes: continue using original SeedSync as the default baseline and treat global theming or identity changes as explicit product-direction decisions.

### rapidcopy
- State: reviewed
- Last reviewed upstream commit (inclusive): `c300b72f808772b00cc977ccceaa23f3c373ce33`
- Resume from next: compare new cross-cutting UX commits after `c300b72f808772b00cc977ccceaa23f3c373ce33`
- Pass date: `2026-03-10`
- Integrated so far: workflow-value improvements already present locally were kept, while rebrand and theme-system changes were intentionally not taken as defaults.
- Pending when resuming: none.
- Notes: continue to reject RapidCopy branding as a default and reevaluate any future theme system only as an optional, coherent settings project.
