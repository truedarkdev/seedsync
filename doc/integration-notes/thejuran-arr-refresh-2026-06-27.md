# thejuran-arr Refresh - 2026-06-27

Status: tracking only; no integration started.

## Range Envelope

- Source branch: `thejuran-arr/main`
- Refresh span: `e9d1e2627b7492f5025c6a9e55236dcd5b7d23db..3db8b48bfd20e7ed873343ddc45b7e47d27e3b0e`
- First commit in span: `d2985b1a` - `test(60): complete UAT - 4 passed, 0 issues`
- Tip commit: `3db8b48b` - `chore: archive v1.4.1 milestone (Scanner Auto-Recovery -> tagged v1.5.0)`

## Counts

- Total commits in span: `1319`
- Non-merge commits: `1226`
- Merge commits: `93`

## Category Summary

- This history is too large to treat as one implementation pass, but the complete commit inventory below preserves every commit that needs future disposition.
- Expect useful work to cluster around bootstrap/history, core runtime behavior, UI/service changes, release/docs, dependency updates, and milestone housekeeping.
- Identity-shifting work, backend replacements, and broad product-shape changes should stay separate from routine refresh chunks.

## Suggested Chunking

1. Start at `d2985b1a` and process oldest-to-newest.
2. Use merge boundaries as natural checkpoints and finish one merge train before opening the next.
3. Prefer chunks of roughly 25-50 non-merge commits, or one coherent milestone window if the history is especially dense.
4. After each chunk, record the next resume commit and note any items that need `deferred`, `maintainer-decision`, or `new-task` treatment.
5. Do not start integration on this branch in the current pass; this note is only the tracking envelope for future work.

## Resume Reminder

- Resume from the next unprocessed commit after the current checkpoint when the branch is picked up again.
- Keep this note current instead of rebuilding the whole span from chat history.

## Complete Commit Inventory

| # | Commit | Date | Kind | Subject |
|---:|---|---|---|---|
| 1 | `d2985b1a` | 2026-04-09 | commit | test(60): complete UAT - 4 passed, 0 issues |
| 2 | `3814eb88` | 2026-04-10 | commit | docs(61): capture phase context |
| 3 | `2be25194` | 2026-04-10 | commit | docs(state): record phase 61 context session |
| 4 | `8862d89a` | 2026-04-10 | commit | docs(61): create Phase 61 branding integration plans |
| 5 | `fc4fafa0` | 2026-04-10 | commit | docs(61): fix 61-03 logo resize to honor D-10 target height |
| 6 | `6904b574` | 2026-04-10 | commit | docs(state): record phase 61 planning session |
| 7 | `30fa938a` | 2026-04-10 | commit | feat(61-01): stage canonical brand sources in doc/brand/ |
| 8 | `2f385f67` | 2026-04-10 | commit | feat(61-02): replace legacy favicon with SeedSyncarr arrow mark (PLSH-04) |
| 9 | `eecf78d3` | 2026-04-10 | commit | feat(61-03): replace docs site favicon and logo with SeedSyncarr branding (PLSH-05) |
| 10 | `405bbfcf` | 2026-04-10 | commit | feat(61-04): add SeedSyncarr wordmark above README screenshot (PLSH-07) |
| 11 | `d66cc1ea` | 2026-04-10 | commit | feat(61-05): set GitHub repo social preview to SeedSyncarr banner (PLSH-06) |
| 12 | `c430b405` | 2026-04-10 | commit | test(61): complete UAT - 4 passed, 0 issues |
| 13 | `e7e16ee7` | 2026-04-13 | commit | docs: start milestone v1.1.0 UI Redesign - Triggarr Style |
| 14 | `90c75592` | 2026-04-13 | commit | docs: define milestone v1.1.0 requirements |
| 15 | `1b1e96d9` | 2026-04-13 | commit | docs: create milestone v1.1.0 roadmap (6 phases) |
| 16 | `955988e7` | 2026-04-14 | commit | docs(62): capture phase context |
| 17 | `66d72952` | 2026-04-14 | commit | docs(state): record phase 62 context session |
| 18 | `6c5ff88d` | 2026-04-14 | commit | docs(62): research nav bar foundation phase |
| 19 | `2518146c` | 2026-04-14 | commit | docs(phase-62): add validation strategy |
| 20 | `46f9799e` | 2026-04-14 | commit | docs(62): create phase plan |
| 21 | `5cfe932d` | 2026-04-14 | commit | feat(nav): add backdrop blur, amber brand split, active indicator, and connection badge (62-01) |
| 22 | `37ff8d92` | 2026-04-14 | commit | feat(nav): add notification bell with dropdown panel and remove alert bar (62-02) |
| 23 | `1340b8a0` | 2026-04-14 | commit | test(62): complete UAT - 10 passed, 0 issues, 2 skipped |
| 24 | `fb3db9f3` | 2026-04-14 | commit | docs(63): research phase domain |
| 25 | `6349a7be` | 2026-04-14 | commit | docs(phase-63): add validation strategy |
| 26 | `9344cd78` | 2026-04-14 | commit | docs(63): create phase plan |
| 27 | `5b3999d8` | 2026-04-14 | commit | feat(63-01): add stats strip with 4 metric cards above file list |
| 28 | `d4729800` | 2026-04-14 | commit | feat(63-02): add transfer table with search, filters, badges, progress, pagination |
| 29 | `3feff949` | 2026-04-14 | commit | test(63): complete UAT - 6 passed, 0 issues, 4 skipped |
| 30 | `76c040d6` | 2026-04-14 | commit | fix(63): code review fixes + visual alignment to design spec |
| 31 | `1ca2a962` | 2026-04-14 | commit | docs(64): research phase domain |
| 32 | `ebab6fcf` | 2026-04-14 | commit | docs(phase-64): add validation strategy |
| 33 | `e42c5c90` | 2026-04-14 | commit | docs(64): create phase plan |
| 34 | `78332d1e` | 2026-04-14 | commit | feat(64): add dashboard log pane component (DASH-12, DASH-13, DASH-14) |
| 35 | `a9aa4e8b` | 2026-04-14 | commit | test(64): complete UAT - 2 passed, 0 issues, 4 skipped |
| 36 | `17773ded` | 2026-04-14 | commit | docs(65): capture phase context |
| 37 | `1b17840c` | 2026-04-14 | commit | docs(state): record phase 65 context session |
| 38 | `fe862cd2` | 2026-04-14 | commit | docs(65): research settings page visual upgrade |
| 39 | `b68cc0b3` | 2026-04-14 | commit | docs(phase-65): add validation strategy |
| 40 | `ddddd641` | 2026-04-14 | commit | docs(65): create phase plan |
| 41 | `39d3765a` | 2026-04-14 | commit | fix(65): revise plans based on checker feedback |
| 42 | `38c9f3b9` | 2026-04-14 | commit | feat(65-01): settings page masonry layout, icon headers, toggle switches |
| 43 | `d8d80f3e` | 2026-04-14 | commit | feat(65-02): brand cards, webhook copy, floating save bar |
| 44 | `b7633363` | 2026-04-14 | commit | fix(65): timer leak, keyboard bypass, fragile webhook discriminator |
| 45 | `1edee2fa` | 2026-04-14 | commit | fix(65): toggle layout - label left, switch right per spec |
| 46 | `39537ac1` | 2026-04-14 | commit | fix(65): LFTP Connection Limits to right column + 2-col grid per spec |
| 47 | `1bbc4ab4` | 2026-04-14 | commit | fix(65): Remote Server 2-col grid and Connected badge per spec |
| 48 | `a49c4bb5` | 2026-04-14 | commit | fix(65): enable toggles in card headers per spec |
| 49 | `3b886c96` | 2026-04-14 | commit | fix(65): separator lines in General Options per spec |
| 50 | `7ab51f68` | 2026-04-14 | commit | fix(65): human-readable time labels on File Discovery fields per spec |
| 51 | `ffeb52b7` | 2026-04-14 | commit | fix(65): uppercase labels, password eye icon, input shadow-inner styling per spec |
| 52 | `7580bc46` | 2026-04-14 | commit | fix(65): path icons, pattern chips, save bar, API security card per spec |
| 53 | `45d4818d` | 2026-04-14 | commit | fix(65): use Phosphor icons for Sonarr/Radarr card headers per spec |
| 54 | `4fb143dc` | 2026-04-14 | commit | test(65): complete UAT - 10 passed, 0 issues |
| 55 | `06c3daf6` | 2026-04-14 | commit | docs(66): capture phase context |
| 56 | `2321fea8` | 2026-04-14 | commit | docs(state): record phase 66 context session |
| 57 | `8e70b12f` | 2026-04-14 | commit | docs(66): research logs page phase |
| 58 | `64d28472` | 2026-04-14 | commit | docs(phase-66): add validation strategy |
| 59 | `82f81b7e` | 2026-04-14 | commit | docs(66): create phase plan |
| 60 | `71bb552e` | 2026-04-14 | commit | feat(66): rewrite logs page with terminal viewer, toolbar, status bar, and tests |
| 61 | `940cee14` | 2026-04-14 | commit | test(66): complete UAT - 8 passed, 0 issues |
| 62 | `03296b62` | 2026-04-14 | commit | docs(phase-66): add security threat verification |
| 63 | `40dfe9d9` | 2026-04-14 | commit | docs(phase-66): update validation strategy - nyquist compliant |
| 64 | `469a0df1` | 2026-04-14 | commit | fix: code review fixes across phases 62-66 |
| 65 | `22ab44e3` | 2026-04-14 | commit | docs: generate project documentation |
| 66 | `ed475724` | 2026-04-14 | commit | docs(67): capture phase context |
| 67 | `6fec0c73` | 2026-04-14 | commit | docs(state): record phase 67 context session |
| 68 | `23dffa2e` | 2026-04-14 | commit | docs(67): research phase - about page redesign |
| 69 | `5eb3a7dc` | 2026-04-14 | commit | docs(phase-67): add validation strategy |
| 70 | `886ca0b3` | 2026-04-14 | commit | docs(67): create phase plan |
| 71 | `496886e8` | 2026-04-14 | commit | fix(67): address checker revision issues across plans and support docs |
| 72 | `0b0c160e` | 2026-04-14 | commit | feat(67-01): add angularVersion to AboutPageComponent and create unit test scaffold |
| 73 | `1a385613` | 2026-04-14 | commit | feat(67-01): rewrite About page HTML template and SCSS with pixel-exact mockup values |
| 74 | `4b1b464b` | 2026-04-14 | commit | docs(67-01): complete About page rewrite plan - 15/15 tests pass, 4-section layout delivered |
| 75 | `e2a0efa3` | 2026-04-14 | commit | docs(67-02): visual verification approved - About page matches AIDesigner mockup |
| 76 | `34ab8418` | 2026-04-14 | commit | docs(67): add code review report |
| 77 | `ca5a8dba` | 2026-04-14 | commit | fix(67): address 8 deep code review findings |
| 78 | `8b8b451d` | 2026-04-14 | commit | fix(67): address round 2 review findings |
| 79 | `ed8123b7` | 2026-04-14 | commit | docs(phase-67): complete phase execution |
| 80 | `f2611d33` | 2026-04-14 | commit | docs(phase-67): evolve PROJECT.md after phase completion |
| 81 | `e0a30cca` | 2026-04-14 | commit | docs: fix tracking for phases 62-66 - mark complete with passing UATs |
| 82 | `eb06908a` | 2026-04-14 | commit | fix: restore settings route removed in phase 62-66 code review |
| 83 | `a7beee11` | 2026-04-14 | commit | docs(v1.1.0): milestone audit - 32/32 requirements, settings route regression fixed |
| 84 | `f9652717` | 2026-04-14 | commit | docs: add Phase 68 - UI Polish (palette, clickable version, favicon, docs) |
| 85 | `2a84d656` | 2026-04-14 | commit | docs(68): create phase plan |
| 86 | `f1846d40` | 2026-04-14 | commit | feat(62-01): nav bar backdrop blur, amber brand, active indicator, connection badge |
| 87 | `a6a0c6db` | 2026-04-14 | commit | docs(62-01): complete nav bar foundation plan summary |
| 88 | `093cd189` | 2026-04-14 | commit | feat(62-02): use innerHTML for notification bell text binding |
| 89 | `5a77a092` | 2026-04-14 | commit | docs(62-02): complete notification bell plan summary |
| 90 | `fcc8854c` | 2026-04-14 | commit | docs(62): add code review report |
| 91 | `68bc550a` | 2026-04-14 | commit | fix(62): CR-01 replace innerHTML with text interpolation to prevent XSS |
| 92 | `df82e1ae` | 2026-04-14 | commit | fix(62): WR-01 align test template and assertion with production text 'Connection Stable' |
| 93 | `695a3938` | 2026-04-14 | commit | fix(62): WR-02 clear auto-hide timer on manual toast dismiss |
| 94 | `76513b83` | 2026-04-14 | commit | docs(62): add code review fix report |
| 95 | `a414bd16` | 2026-04-14 | commit | fix(62): address TuringMind deep review findings |
| 96 | `ff73e4f0` | 2026-04-14 | commit | docs(62): add phase verification report |
| 97 | `0a6669ed` | 2026-04-14 | commit | docs(phase-62): complete phase execution |
| 98 | `9c3b30f9` | 2026-04-14 | commit | docs(63-01): complete stats strip plan summary |
| 99 | `d05d92f3` | 2026-04-14 | commit | docs(phase-63): update tracking after wave 1 |
| 100 | `64efb640` | 2026-04-14 | commit | feat(63-02): transfer table with search, filters, badges, progress, pagination |
| 101 | `3e790c74` | 2026-04-14 | commit | docs(63-02): complete transfer table plan summary |
| 102 | `951c4c0f` | 2026-04-14 | commit | docs(phase-63): update tracking after wave 2 |
| 103 | `4d8477b1` | 2026-04-14 | commit | docs(63): add code review report |
| 104 | `01894d55` | 2026-04-14 | commit | fix(63): WR-01 change misleading Free label to Tracked on storage stat cards |
| 105 | `2b717638` | 2026-04-14 | commit | fix(63): WR-02 add DashboardStatsService to FilesPageComponent providers |
| 106 | `e8475a06` | 2026-04-14 | commit | docs(63): add code review fix report |
| 107 | `389307bf` | 2026-04-14 | commit | fix(63): address TuringMind deep review findings |
| 108 | `ae1b1155` | 2026-04-14 | commit | docs(63): add phase verification report |
| 109 | `acfe9454` | 2026-04-14 | commit | docs(phase-63): complete phase execution |
| 110 | `08804754` | 2026-04-14 | commit | docs(phase-65): complete phase execution |
| 111 | `7b78b71d` | 2026-04-14 | commit | docs(phase-66): complete phase execution |
| 112 | `2e9773da` | 2026-04-14 | commit | refactor(68-01): consolidate SCSS palette drift in settings and logs pages |
| 113 | `8d68e7ae` | 2026-04-14 | commit | feat(68-01): replace nav bar brand icon with favicon, migrate version-check to APP_VERSION |
| 114 | `bd01faf4` | 2026-04-14 | commit | docs(68-01): complete UI polish plan 01 - palette consolidation and brand favicon |
| 115 | `b35a2cfd` | 2026-04-14 | commit | feat(68-02): make version badges clickable links to GitHub releases |
| 116 | `06a5298d` | 2026-04-14 | commit | docs(68-02): update dashboard screenshot for v1.1.0 UI |
| 117 | `3dc47a08` | 2026-04-14 | commit | docs(68-02): complete clickable version badges and dashboard screenshot plan |
| 118 | `a52655bc` | 2026-04-14 | commit | docs(phase-68): complete phase execution |
| 119 | `9cd2ab89` | 2026-04-14 | commit | docs(v1.1.0): milestone audit - 37/37 requirements, 5 tech debt items |
| 120 | `2aaa566e` | 2026-04-14 | commit | fix: remove duplicate DashboardStatsService provider, add null guards to pagination |
| 121 | `93901bc4` | 2026-04-14 | commit | chore: archive v1.1.0 milestone (dev release) |
| 122 | `64a96fe4` | 2026-04-14 | commit | chore: remove REQUIREMENTS.md for v1.1.0 milestone |
| 123 | `f694c930` | 2026-04-14 | commit | fix: resolve eslint errors - quotes, max-len, unused vars |
| 124 | `90c07e53` | 2026-04-14 | commit | fix: add missing return type annotations to satisfy eslint --max-warnings 0 |
| 125 | `e1b348e9` | 2026-04-14 | commit | fix(e2e): update selectors for redesigned about and settings pages |
| 126 | `bfbff3a7` | 2026-04-15 | commit | docs: track E2E selector update as follow-up todo |
| 127 | `236442c4` | 2026-04-15 | commit | docs: add Phase 69 - E2E Selector Update for redesigned dashboard |
| 128 | `c290cd1c` | 2026-04-15 | commit | docs(69): add validation strategy |
| 129 | `4386eae6` | 2026-04-15 | commit | docs(69): create phase plan - E2E selector update for transfer-table |
| 130 | `40ecae60` | 2026-04-15 | commit | docs(69): resolve checker issues - mark open questions resolved, fix key_links |
| 131 | `cdb0cb50` | 2026-04-15 | commit | feat(69-01): rewrite DashboardPage for transfer-table selectors |
| 132 | `a5c957bc` | 2026-04-15 | commit | feat(69-01): update specs - new golden data, skip file-actions and bulk-actions tests |
| 133 | `d6a055ee` | 2026-04-15 | commit | docs(69-01): complete E2E selector update plan - transfer-table page objects |
| 134 | `768fe642` | 2026-04-15 | commit | docs(69): add code review report |
| 135 | `c956ab19` | 2026-04-15 | commit | fix(69): WR-01 remove redundant DashboardPage re-instantiation in tests |
| 136 | `1aac3a8e` | 2026-04-15 | commit | docs(69): add code review fix report |
| 137 | `a02e15ca` | 2026-04-15 | commit | fix(69): improve navigateTo diagnostic - wait for table before rows |
| 138 | `331de81c` | 2026-04-15 | commit | fix(69): guard waitForAtLeastFileCount against content hydration race |
| 139 | `3ae5b950` | 2026-04-15 | commit | test(69): persist human verification items as UAT |
| 140 | `f4b1161a` | 2026-04-15 | commit | docs(69): commit planning artifacts - phase complete |
| 141 | `ba21140a` | 2026-04-15 | commit | chore: gitignore local AI tooling directories |
| 142 | `b8cac1cf` | 2026-04-15 | commit | chore: add security scan tooling and initial report |
| 143 | `558ba7fb` | 2026-04-15 | commit | fix(e2e): update golden file sizes for transfer-row precision |
| 144 | `395924a2` | 2026-04-15 | commit | test: clear UAT debt - verify phases 62-64 on live instance, resolve phase 69 |
| 145 | `dfc750b0` | 2026-04-15 | commit | docs: add drill-down segment filter design spec |
| 146 | `412a49b8` | 2026-04-15 | commit | docs(70): add validation strategy |
| 147 | `4e743598` | 2026-04-15 | commit | docs(70): create phase plan |
| 148 | `4eec1e59` | 2026-04-15 | commit | feat(70-01): add drill-down state, toggle-collapse logic, and sub-status filtering |
| 149 | `91421246` | 2026-04-15 | commit | feat(70-01): replace segment filter template with drill-down HTML and add SCSS classes |
| 150 | `73190c41` | 2026-04-15 | commit | docs(70-01): complete drill-down segment filter plan |
| 151 | `bcc26300` | 2026-04-15 | commit | test(70-02): update TEST_TEMPLATE and existing assertions for drill-down |
| 152 | `f74cbb56` | 2026-04-15 | commit | test(70-02): add 10 new drill-down sub-status unit tests |
| 153 | `937bdab1` | 2026-04-15 | commit | docs(70-02): complete drill-down unit tests plan |
| 154 | `5f3da22d` | 2026-04-15 | commit | docs(70): add code review report |
| 155 | `1e37cc93` | 2026-04-15 | commit | fix(70): WR-01 use :has() selector for search icon focus highlight |
| 156 | `4ec23158` | 2026-04-15 | commit | docs(70): add code review fix report |
| 157 | `9d14dbb1` | 2026-04-15 | commit | fix(70): restore distinctUntilChanged, add sub-status toggle-off, fix btn-sub height |
| 158 | `07bab633` | 2026-04-15 | commit | perf(70): add shareReplay(1) to segmentedFiles$ to avoid triple filter execution |
| 159 | `2e99fa28` | 2026-04-15 | commit | fix(70): address all TuringMind review findings |
| 160 | `dbabb392` | 2026-04-15 | commit | docs(70): add phase verification report |
| 161 | `b0ced2d2` | 2026-04-15 | commit | test(70): persist human verification items as UAT |
| 162 | `742934f4` | 2026-04-15 | commit | docs(phase-70): complete phase execution |
| 163 | `8a58039f` | 2026-04-15 | commit | docs(phase-70): evolve PROJECT.md after phase completion |
| 164 | `d5113232` | 2026-04-15 | commit | docs(v1.1.0): update milestone audit - all phases complete, all requirements satisfied |
| 165 | `275afcb8` | 2026-04-15 | commit | chore: archive v1.1.0 milestone files |
| 166 | `64f03c84` | 2026-04-15 | commit | docs: evolve PROJECT.md after v1.1.0-dev milestone |
| 167 | `54f7527c` | 2026-04-15 | commit | chore: archive v1.1.0 phase directories to milestones/ |
| 168 | `126d12ce` | 2026-04-15 | commit | docs: add phase 71 - push stable release |
| 169 | `45310292` | 2026-04-15 | commit | fix(lint): resolve console.log and max-len violations for CI |
| 170 | `38583b1e` | 2026-04-16 | commit | chore(deps-dev): bump hono from 4.12.12 to 4.12.14 in /src/angular |
| 171 | `c41dda8e` | 2026-04-17 | merge | Merge pull request #13 from thejuran/dependabot/npm_and_yarn/src/angular/hono-4.12.14 |
| 172 | `38ccb56e` | 2026-04-15 | commit | docs: add storage capacity percentage tiles design spec |
| 173 | `8589314c` | 2026-04-15 | commit | docs: start milestone v1.2.0 Storage Capacity Tiles |
| 174 | `7a3dfe7f` | 2026-04-17 | commit | docs: capture todo - add dashboard filter for every torrent status |
| 175 | `d9b29947` | 2026-04-18 | commit | ci(deps): track root package.json in dependabot |
| 176 | `9c1f00dd` | 2026-04-19 | commit | chore(deps): bump follow-redirects in /src/angular (#12) |
| 177 | `7e1611d1` | 2026-04-19 | commit | chore(deps): bump the npm_and_yarn group across 1 directory with 19 updates (#16) |
| 178 | `61926090` | 2026-04-19 | commit | chore(deps-dev): bump ruff from 0.15.10 to 0.15.11 in /src/python (#15) |
| 179 | `b0f31a66` | 2026-04-19 | commit | docs: capture todo - restore dashboard file selection and action bar .planning/todos/pending/2026-04-19-restore-dashboard-file-selection-and-action-bar.md .planning/STATE.md |
| 180 | `438b6740` | 2026-04-19 | commit | test(e2e): make dashboard file list assertion order-independent |
| 181 | `3bc138da` | 2026-04-19 | commit | chore(deps): bump requests from 2.33.0 to 2.33.1 in /src/python (#8) |
| 182 | `fc20784e` | 2026-04-19 | commit | chore(deps): bump patool from 4.0.3 to 4.0.4 in /src/python (#9) |
| 183 | `45ad1370` | 2026-04-19 | commit | chore(deps): bump pytz from 2025.2 to 2026.1.post1 in /src/python (#10) |
| 184 | `9435aa04` | 2026-04-19 | commit | docs: promote dashboard UI todos to v1.2.0 phases 72-73 |
| 185 | `6b78764b` | 2026-04-19 | commit | docs: add phase 74 - storage capacity tiles |
| 186 | `22148de4` | 2026-04-19 | commit | docs: rename phase 74 to short slug |
| 187 | `1c79d715` | 2026-04-19 | commit | docs(72): capture phase context for dashboard selection and action bar restore .planning/phases/72-restore-dashboard-file-selection-and-action-bar/72-CONTEXT.md .planning/phases/72-restore-dashboard-file-selection-and-action-bar/72-DISCUSSION-LOG.md .planning/phases/72-restore-dashboard-file-selection-and-action-bar/variant-A-floating-bar.html .planning/phases/72-restore-dashboard-file-selection-and-action-bar/variant-B-card-internal-bar.html |
| 188 | `41abf783` | 2026-04-19 | commit | docs(state): record phase 72 context session .planning/STATE.md |
| 189 | `bb286313` | 2026-04-19 | commit | docs(72): plan dashboard file selection and action bar restoration |
| 190 | `2a9666cd` | 2026-04-19 | commit | feat(72-01): rewrite bulk-actions-bar template to Variant B literal port |
| 191 | `103ace35` | 2026-04-19 | commit | chore(72-02): delete 4 obsolete component sets and 2 specs (D-18) |
| 192 | `fae08374` | 2026-04-19 | commit | feat(72-01): rewrite bulk-actions-bar SCSS as Variant B literal hex port |
| 193 | `671d5dfb` | 2026-04-19 | commit | docs(72-02): complete plan 02 - delete obsolete component sets (D-18) |
| 194 | `92508ecb` | 2026-04-19 | commit | test(72-01): add Variant B DOM contract specs to bulk-actions-bar spec |
| 195 | `600b8bc8` | 2026-04-19 | commit | docs(72-01): complete bulk-actions-bar Variant B port plan summary |
| 196 | `e2cc9377` | 2026-04-19 | merge | chore: merge executor worktree (worktree-agent-aba55126) - 72-02 delete obsolete components |
| 197 | `6a0c741c` | 2026-04-19 | merge | chore: merge executor worktree (worktree-agent-ac0ef4ba) - 72-01 bulk-actions-bar Variant B port |
| 198 | `e6199ec1` | 2026-04-19 | commit | docs(phase-72): update tracking after wave 1 (72-01, 72-02 complete) |
| 199 | `278e180d` | 2026-04-19 | commit | feat(72-03): add isSelected signal, checkboxToggle emitter, and HostBindings to TransferRow |
| 200 | `d033f2e0` | 2026-04-19 | commit | feat(72-03): add leading checkbox cell + selected-row SCSS to TransferRow |
| 201 | `eeb26dd4` | 2026-04-19 | commit | test(72-03): add checkbox + selection-signal specs to TransferRow spec |
| 202 | `f4c7e9d2` | 2026-04-19 | commit | docs(72-03): complete plan 03 - TransferRow checkbox + selection signal |
| 203 | `84ea8411` | 2026-04-19 | commit | feat(72-04): wire selection, header checkbox state, Esc handler, and bulk action dispatch in TransferTable |
| 204 | `9ee6e4bd` | 2026-04-19 | commit | feat(72-04): add header select-all checkbox, BulkActionsBar insertion, and colspan bump |
| 205 | `c621d0b7` | 2026-04-19 | commit | test(72-04): add selection, Esc, header-checkbox, shift-click, and bulk-dispatch specs |
| 206 | `45f40299` | 2026-04-19 | commit | docs(72-04): complete plan 04 - TransferTable orchestration + bulk dispatch |
| 207 | `1a4c7aa5` | 2026-04-19 | commit | docs(phase-72): update tracking after wave 3 (72-03, 72-04 complete) |
| 208 | `aa781b7d` | 2026-04-19 | commit | feat(72-05): add selection + action-bar helpers to DashboardPage page object |
| 209 | `f92de674` | 2026-04-19 | commit | test(72-05): restore 5 dashboard selection/action E2E tests (D-19) |
| 210 | `8e5cc3f4` | 2026-04-19 | commit | docs(72-05): complete plan 05 - E2E Playwright restore (D-19) |
| 211 | `cd15648c` | 2026-04-19 | commit | docs(phase-72): mark phase complete - all 5 plans shipped |
| 212 | `3e02c1c7` | 2026-04-19 | commit | docs(phase-72): add security threat verification - 16/16 threats closed |
| 213 | `aa305d5d` | 2026-04-19 | commit | docs(phase-72): add validation strategy - 8 covered, 1 CI-gated, 0 missing |
| 214 | `626f1b3d` | 2026-04-19 | commit | refactor(phase-72): extract BulkActionDispatcher and move shift-click anchor into FileSelectionService |
| 215 | `d338177d` | 2026-04-19 | commit | docs(73): capture phase context |
| 216 | `b91f37b9` | 2026-04-19 | commit | docs(73): add patterns map for planner .planning/phases/73-dashboard-filter-for-every-torrent-status/73-PATTERNS.md |
| 217 | `ad169892` | 2026-04-19 | commit | plan(73): 5 plans in 3 waves - extend segment filter + URL persistence |
| 218 | `90789c6e` | 2026-04-19 | commit | feat(73-01): widen activeSegment union to include 'done' at all 3 type sites |
| 219 | `41e1c689` | 2026-04-19 | commit | feat(73-01): add Done branch to segmentedFiles$ and add DEFAULT to Active branch |
| 220 | `86164252` | 2026-04-19 | commit | docs(73-01): complete plan 01 - segment union widened + Done branch added |
| 221 | `23937281` | 2026-04-19 | merge | chore: merge executor worktree (worktree-agent-a1d4e369) - plan 73-01 |
| 222 | `51a0c7ad` | 2026-04-19 | commit | feat(73-02): add Done segment + Pending sub to transfer-table template |
| 223 | `3d1fbbed` | 2026-04-19 | commit | feat(73-03): inject Router+ActivatedRoute and add ngOnInit URL hydration |
| 224 | `ed5a57a9` | 2026-04-19 | commit | docs(73-02): complete plan 02 - Done segment + Pending sub template changes |
| 225 | `67241f1b` | 2026-04-19 | commit | feat(73-03): add _writeFilterToUrl helper and wire into segment/sub change handlers |
| 226 | `96dfb75c` | 2026-04-19 | commit | docs(73-03): complete URL query-param persistence plan - SUMMARY |
| 227 | `e3ae093e` | 2026-04-19 | merge | chore: merge executor worktree (worktree-agent-a8de61f8) |
| 228 | `929cca71` | 2026-04-19 | merge | chore: merge executor worktree (worktree-agent-a58ef820) |
| 229 | `dc700dd8` | 2026-04-19 | commit | test(73-04): add Router/ActivatedRoute mocks + update TEST_TEMPLATE + 4-button assertion |
| 230 | `bfe58d99` | 2026-04-19 | commit | test(73-04): add 6 filter-logic tests - Done branch + Pending sub + selection-clear |
| 231 | `3bc69e72` | 2026-04-19 | commit | test(73-04): add URL query-param persistence describe - 11 new tests (D-09/D-10/D-11) |
| 232 | `f20625b7` | 2026-04-19 | commit | docs(73-04): complete plan 04 - 17 new tests covering Done branch + URL persistence |
| 233 | `14d456cd` | 2026-04-19 | commit | test(73-05): add getSegmentButton and getSubButton to DashboardPage page-object |
| 234 | `c2d34076` | 2026-04-19 | commit | test(73-05): add 3 e2e tests - Done expand, Pending reveal, URL round-trip |
| 235 | `1c74a8c7` | 2026-04-19 | commit | docs(73-05): complete plan 05 - 3 e2e tests + 2 page-object locator methods |
| 236 | `48bd3e5f` | 2026-04-19 | commit | test(73): persist human verification items as UAT - runtime e2e suite |
| 237 | `7f05dc67` | 2026-04-19 | commit | refactor(73): consolidate segment?statuses mapping to single SEGMENT_STATUSES source |
| 238 | `5d575882` | 2026-04-19 | commit | refactor(73): harden createWithQuery test helper against injector drift |
| 239 | `11858312` | 2026-04-19 | commit | refactor(73): swallow router.navigate rejections on URL filter sync (F-3) |
| 240 | `f64df63c` | 2026-04-19 | commit | refactor(73): sanitize invalid URL params after silent fallback in ngOnInit (F-2) |
| 241 | `591eef22` | 2026-04-19 | commit | test(73): add e2e coverage for invalid-URL silent fallback + sanitization (F-5) |
| 242 | `ca702cf4` | 2026-04-19 | commit | docs(74): capture phase context |
| 243 | `51b17847` | 2026-04-19 | commit | docs(state): record phase 74 context session |
| 244 | `0565b7db` | 2026-04-19 | commit | docs(74): create phase plan for storage capacity tiles |
| 245 | `562d71c7` | 2026-04-19 | commit | test(74-01): add failing tests for StorageStatus component |
| 246 | `0aa039ce` | 2026-04-19 | commit | test(74-03): add failing tests for ServerStatus storage block (RED) |
| 247 | `a348a18b` | 2026-04-19 | commit | feat(74-01): add StorageStatus component to Status model |
| 248 | `7a460860` | 2026-04-19 | commit | test(74-01): add failing tests for storage block serialization |
| 249 | `1e6faf0f` | 2026-04-19 | commit | feat(74-03): extend ServerStatus DTO with storage block (snake->camel mapping) |
| 250 | `bf326742` | 2026-04-19 | commit | feat(74-01): extend SerializeStatusJson.status() with storage block |
| 251 | `4d5e8de1` | 2026-04-19 | commit | test(74-03): add failing tests for DashboardStats capacity + combineLatest (RED) |
| 252 | `5d01d28a` | 2026-04-19 | commit | docs(74-01): complete StorageStatus model + serializer storage block plan |
| 253 | `9f58d39c` | 2026-04-19 | commit | feat(74-03): widen DashboardStats + combineLatest pipeline (GREEN) |
| 254 | `f4acdb97` | 2026-04-19 | commit | fix(74-03): add null capacity fields to stats-strip mock to unblock TS compile |
| 255 | `321c3103` | 2026-04-19 | commit | docs(74-03): complete plan - SUMMARY.md |
| 256 | `9435df24` | 2026-04-19 | merge | chore: merge executor worktree (worktree-agent-aff6472b) |
| 257 | `556ab6aa` | 2026-04-19 | merge | chore: merge executor worktree (worktree-agent-a9f0fb32) |
| 258 | `dd6147fe` | 2026-04-19 | commit | docs(phase-74): update tracking after wave 1 |
| 259 | `d789d135` | 2026-04-19 | commit | test(74-02): add failing tests for df parser + scan capacity tuple (RED) |
| 260 | `35609c7c` | 2026-04-19 | commit | feat(74-02): wire shutil.disk_usage + df -B1 capacity into scanners (GREEN) |
| 261 | `5a91c708` | 2026-04-19 | commit | test(74-02): add failing tests for >1% gate + per-side capacity (RED) |
| 262 | `c193ebb4` | 2026-04-19 | commit | feat(74-02): >1% capacity gate + per-side independence in controller (GREEN) |
| 263 | `2129f0db` | 2026-04-19 | commit | docs(74-02): complete plan - SUMMARY.md |
| 264 | `1075ddad` | 2026-04-19 | commit | feat(74-04): add --warning/--danger SCSS modifiers + DecimalPipe import |
| 265 | `4c29687a` | 2026-04-19 | commit | feat(74-04): port capacity-mode template to Remote and Local tiles |
| 266 | `b3906078` | 2026-04-19 | commit | test(74-04): cover capacity render, fallback, thresholds, per-tile independence |
| 267 | `4109184d` | 2026-04-19 | commit | docs(74-04): complete plan - SUMMARY.md |
| 268 | `b1d74e1d` | 2026-04-19 | commit | docs(phase-74): update tracking after wave 2 |
| 269 | `60af5b01` | 2026-04-19 | commit | docs(74): add code review report |
| 270 | `536eb229` | 2026-04-19 | commit | fix(74): restore IScanner tuple contract in ActiveScanner |
| 271 | `b89278fe` | 2026-04-19 | commit | fix(74): per-field capacity gate in _update_controller_status |
| 272 | `d9a33150` | 2026-04-19 | commit | refactor(74): use @let to dedupe pct expressions in stats-strip |
| 273 | `1fa30bfe` | 2026-04-19 | commit | chore(74): tighten test mock + Optional annotations |
| 274 | `08cbc9b9` | 2026-04-19 | commit | docs(74): record deep-review findings + resolutions |
| 275 | `6e1fea1a` | 2026-04-19 | commit | test(73): resolve HUMAN-UAT - e2e accepted on structural verification, runtime deferred to CI |
| 276 | `6c9f0d54` | 2026-04-19 | commit | test(73): complete UAT - 11 passed, 0 issues (structural verification) |
| 277 | `10153784` | 2026-04-19 | commit | docs(phase-73): add security threat verification |
| 278 | `323b3bbb` | 2026-04-19 | commit | docs(phase-73): add nyquist validation strategy |
| 279 | `c6c08e56` | 2026-04-19 | commit | test(74): pause UAT - 6 tests deferred until after next dev release |
| 280 | `fa0e8545` | 2026-04-19 | commit | docs(phase-74): add security threat verification |
| 281 | `f65cf1c6` | 2026-04-19 | commit | test(phase-74): add Nyquist validation tests |
| 282 | `ebfc92a5` | 2026-04-19 | commit | docs(phase-74): add validation strategy |
| 283 | `e96d765a` | 2026-04-19 | commit | docs(milestone-v1.1.0): add milestone audit report |
| 284 | `5a7f6653` | 2026-04-19 | commit | test(72): complete UAT - 11 passed, 0 issues (structural verification) |
| 285 | `614edfbe` | 2026-04-19 | commit | test(74): augment UAT with 11 structural tests - 11 passed, 6 deferred |
| 286 | `e8ccb9fd` | 2026-04-19 | commit | docs(milestone-v1.1.0): re-audit after verification gap closure - status passed |
| 287 | `41729685` | 2026-04-19 | commit | chore: close v1.1.0 milestone - roll phases 72-74 into archive |
| 288 | `b1a15b63` | 2026-04-19 | commit | fix(lint): replace any casts and add missing return types in phase 72-74 tests |
| 289 | `a0f7e1ce` | 2026-04-20 | commit | fix(72): wrap bulk-actions-bar button labels in spans for a11y + E2E |
| 290 | `50f72046` | 2026-04-20 | commit | fix(auto-delete): skip Timer delete when file or any child is active (#18) |
| 291 | `815a4acd` | 2026-04-20 | commit | ci: simplify inherited-from-original cruft (#17) |
| 292 | `0ae867f4` | 2026-04-20 | commit | docs: start milestone v1.1.1 Post-Redesign Cleanup |
| 293 | `1fcd4fa1` | 2026-04-20 | commit | docs: define milestone v1.1.1 requirements (12 REQs) |
| 294 | `8373780a` | 2026-04-20 | commit | docs: create milestone v1.1.1 roadmap (8 phases, 75-82) |
| 295 | `dee3ff9f` | 2026-04-20 | commit | docs(75): capture phase context for per-child import state (GH #19) |
| 296 | `30d586d7` | 2026-04-20 | commit | docs(state): record phase 75 context session |
| 297 | `05a945e1` | 2026-04-20 | commit | docs(75): plan per-child import state (4 plans, 3 waves) (GH #19) |
| 298 | `4ae0e9b7` | 2026-04-20 | commit | test(75-02): migrate webhook_manager assertions to tuple shape |
| 299 | `caf14276` | 2026-04-20 | commit | feat(75-01): add imported_children field + add_imported_child helper |
| 300 | `0488498c` | 2026-04-20 | commit | feat(75-02): widen WebhookManager.process return to List[Tuple[str, str]] |
| 301 | `2376856a` | 2026-04-20 | commit | test(75-01): add failing tests for imported_children round-trip |
| 302 | `d7366f96` | 2026-04-20 | commit | docs(75-02): complete widen webhook_manager.process return type plan |
| 303 | `e2e06373` | 2026-04-20 | commit | feat(75-01): extend ControllerPersist serialization for imported_children |
| 304 | `06c26cac` | 2026-04-20 | commit | docs(75-01): complete per-child-import-state-gh-19 plan 01 |
| 305 | `cbc88293` | 2026-04-20 | merge | chore: merge executor worktree (worktree-agent-adacef5a) |
| 306 | `1499e534` | 2026-04-20 | merge | chore: merge executor worktree (worktree-agent-a06f9e58) |
| 307 | `c527279a` | 2026-04-20 | commit | docs(phase-75): update tracking after wave 1 |
| 308 | `ddb28bc7` | 2026-04-20 | commit | test(75-03): migrate test_controller_unit.py process return_value to tuple form |
| 309 | `d2f554db` | 2026-04-20 | commit | feat(75-03): integrate per-child state + coverage guard into auto-delete |
| 310 | `dcafc8b0` | 2026-04-20 | commit | docs(75-03): complete per-child-import-state-gh-19 plan 03 |
| 311 | `32f69cda` | 2026-04-20 | merge | chore: merge executor worktree (worktree-agent-a3bcd2b9) |
| 312 | `d3483e50` | 2026-04-20 | commit | docs(phase-75): update tracking after wave 2 |
| 313 | `0e1c1256` | 2026-04-20 | commit | test(75-04): migrate test_auto_delete process.return_value sites to tuple form |
| 314 | `52cb4b7b` | 2026-04-20 | commit | test(75-04): add D-19 coverage guard cases + D-20 rehydration case |
| 315 | `a750176c` | 2026-04-20 | commit | docs(75-04): complete per-child-import-state-gh-19 plan 04 |
| 316 | `b21806d5` | 2026-04-20 | merge | chore: merge executor worktree (worktree-agent-a1566fb1) |
| 317 | `4fbd0715` | 2026-04-20 | commit | docs(phase-75): mark all plans complete after wave 3 |
| 318 | `974a3b4c` | 2026-04-20 | commit | docs(75): add code review report |
| 319 | `0928baf4` | 2026-04-20 | commit | fix(75): WR-01 skip add_imported_child when matched_name == root_name |
| 320 | `7434c3e1` | 2026-04-20 | commit | fix(75): WR-02 move imported_children.pop before delete_local dispatch |
| 321 | `bc43dc0e` | 2026-04-20 | commit | docs(75): add review fix report (2 warnings resolved) |
| 322 | `e708045d` | 2026-04-20 | commit | fix(75): collapse WR-02 pop into coverage-guard lock + is_dir guard in BFS |
| 323 | `f92893a1` | 2026-04-20 | commit | fix(75): harden webhook log sanitization, persist validation, BFS bound |
| 324 | `1aecd890` | 2026-04-20 | commit | fix(75): pop imported_children on BFS-limit skip, simplify leaf guard, sanitize enqueue log |
| 325 | `ff2b0d8d` | 2026-04-20 | commit | fix(75): complete log sanitization + off-by-one in BFS node limit |
| 326 | `70c91af3` | 2026-04-20 | commit | fix(75): revert BFS off-by-one -- `>` caps at exactly 10,000 nodes |
| 327 | `e203cfdc` | 2026-04-20 | commit | docs(75): add verification report -- passed 10/10 must-haves |
| 328 | `e991b19a` | 2026-04-20 | commit | docs(phase-75): complete phase execution |
| 329 | `bb679a76` | 2026-04-20 | commit | docs(76): capture phase context |
| 330 | `27c07e0e` | 2026-04-20 | commit | docs(state): record phase 76 context session |
| 331 | `129b30b2` | 2026-04-20 | commit | docs(76): create phase plan - 4 waves for FIX-01 multiselect bulk-bar union |
| 332 | `e019b21f` | 2026-04-20 | commit | test(76): add failing FIX-01 characterization tests (D-01, Option A relocation) |
| 333 | `3980b75d` | 2026-04-20 | commit | docs(76): mark plan 01 complete |
| 334 | `bf8edd31` | 2026-04-20 | commit | docs(76): add Wave 2 root-cause analysis (awaiting checkpoint) |
| 335 | `46ecfffe` | 2026-04-20 | commit | fix(76): null-guard DELETED remoteSize in isQueueable + isRemotelyDeletable - drives FIX-01 characterization green |
| 336 | `05b7932b` | 2026-04-20 | commit | docs(76): mark plan 02 complete |
| 337 | `68990d81` | 2026-04-20 | commit | test(76-03): add D-09 mixed-selection coverage (all-DELETED, DELETED+DOWNLOADING, DELETED+DOWNLOADED+STOPPED) |
| 338 | `8a12807d` | 2026-04-20 | commit | docs(76-03): complete Wave 3 D-09 coverage plan |
| 339 | `c78dc329` | 2026-04-20 | commit | docs(76): record Wave 4 full-suite verification - FIX-01 shipped |
| 340 | `a24826f4` | 2026-04-20 | commit | docs(76): mark plan 04 complete |
| 341 | `48c55f76` | 2026-04-20 | commit | docs(76): add code review report |
| 342 | `3dbba759` | 2026-04-20 | commit | test(76): persist human verification items as UAT |
| 343 | `f89cd6ea` | 2026-04-20 | commit | docs(76): add phase verification report |
| 344 | `6915aed0` | 2026-04-20 | commit | docs(phase-76): complete phase execution |
| 345 | `96502e12` | 2026-04-20 | commit | docs(phase-76): evolve PROJECT.md after phase completion |
| 346 | `0235eaa0` | 2026-04-20 | commit | docs(77): capture phase context |
| 347 | `c66cc40c` | 2026-04-20 | commit | docs(state): record phase 77 context session |
| 348 | `4ae8d939` | 2026-04-20 | commit | docs(77): research phase Playwright E2E domain |
| 349 | `1df7cdad` | 2026-04-20 | commit | docs(77): add validation strategy |
| 350 | `25c71a0e` | 2026-04-20 | commit | feat(77-01): add seed-state fixture for E2E status lifecycle |
| 351 | `8f12c614` | 2026-04-20 | commit | feat(77-01): extend DashboardPage with 9 helpers for UAT-01/UAT-02 |
| 352 | `aca03b00` | 2026-04-20 | commit | docs(77-01): complete Wave 1 E2E test infrastructure plan |
| 353 | `fd9e98ce` | 2026-04-20 | merge | chore: merge executor worktree (worktree-agent-ae2e20f8) |
| 354 | `f4d3da65` | 2026-04-20 | commit | docs(phase-77): update tracking after wave 1 |
| 355 | `e8a58634` | 2026-04-20 | commit | test(77-02): add UAT-01 describe.serial scaffolding + 3 non-destructive specs |
| 356 | `cc48864d` | 2026-04-20 | commit | test(77-02): add consolidated bulk-bar dispatch spec and FIX-01 union spec |
| 357 | `7bb67fc5` | 2026-04-20 | commit | docs(77-02): complete Wave 2 UAT-01 selection and bulk bar plan |
| 358 | `2413580f` | 2026-04-20 | merge | chore: merge executor worktree (worktree-agent-a02ab79a) |
| 359 | `fe0f858c` | 2026-04-20 | commit | docs(phase-77): update tracking after wave 2 |
| 360 | `835b8f26` | 2026-04-20 | commit | test(77-03): add UAT-02 describe.serial scaffolding + 4 populated-filter specs |
| 361 | `56c75123` | 2026-04-20 | commit | test(77-03): add 4 UAT-02 empty-state filter specs and 2 URL round-trip specs |
| 362 | `2168cf22` | 2026-04-20 | commit | docs(77-03): complete Wave 3 UAT-02 status filter and URL round-trip plan |
| 363 | `23661171` | 2026-04-20 | merge | chore: merge executor worktree (worktree-agent-a511bea5) |
| 364 | `0465b1a7` | 2026-04-20 | commit | docs(phase-77): update tracking after wave 3 |
| 365 | `110533ca` | 2026-04-20 | commit | chore(77-04): record preflight env audit - harness blocker surfaced |
| 366 | `5c4d7702` | 2026-04-20 | commit | docs(state): record Plan 04 pause at Task 1 infra gate |
| 367 | `2663fab7` | 2026-04-20 | commit | docs(77-04): complete Wave 4 verification summary (CI-as-evidence path) |
| 368 | `73e36ca0` | 2026-04-20 | commit | docs(phase-77): update tracking after wave 4 |
| 369 | `8c3fee64` | 2026-04-20 | commit | docs(77): add code review report (0 critical, 3 warning, 6 info) |
| 370 | `1dbf2ec1` | 2026-04-20 | commit | fix(77): WR-03 stabilize getSelectedCount via DOM checkbox count |
| 371 | `3904585e` | 2026-04-20 | commit | fix(77): WR-02 race DOWNLOADING vs DOWNLOADED in STOPPED seed |
| 372 | `cd2f2bb5` | 2026-04-20 | commit | fix(77): WR-01 narrow UAT-02 Pending spec harness-composition comment |
| 373 | `b68c6fa0` | 2026-04-20 | commit | docs(77): code review fix report (3 warnings resolved) |
| 374 | `67dac2c3` | 2026-04-20 | commit | fix(77): narrow STOPPED seed race via filtered count (deep-review finding #1) |
| 375 | `a0c29a9b` | 2026-04-20 | commit | fix(77): retry-safe re-seed on FIX-01 DELETED guard (deep-review finding #2) |
| 376 | `1ed10ef3` | 2026-04-20 | commit | docs(77): add verification report (4/4 structural; CI-pending) |
| 377 | `9791d084` | 2026-04-20 | commit | test(77): persist human verification items as UAT |
| 378 | `920b5ad9` | 2026-04-20 | commit | docs(phase-77): complete phase execution |
| 379 | `bb3936e8` | 2026-04-20 | commit | docs(phase-77): evolve PROJECT.md after phase completion |
| 380 | `dd2dddef` | 2026-04-21 | commit | docs(78): capture phase context |
| 381 | `b853adec` | 2026-04-21 | commit | docs(state): record phase 78 context session |
| 382 | `f3a225a1` | 2026-04-21 | commit | docs(78): create phase plan |
| 383 | `e222d811` | 2026-04-21 | commit | feat(78-01): disposable SSH target compose + Dockerfile |
| 384 | `c8ecf94e` | 2026-04-21 | commit | feat(78-01): bound-local-fs.sh + Phase 78 settings.cfg |
| 385 | `2c089a51` | 2026-04-21 | commit | feat(78-01): README-setup + dockerize backend + live-scan evidence |
| 386 | `1ca96e3b` | 2026-04-21 | commit | feat(78-01): checkpoint evidence - dashboard UAT-ready |
| 387 | `e3055528` | 2026-04-21 | commit | docs(78-01): SUMMARY.md - live-seedbox env stood up |
| 388 | `d954babb` | 2026-04-21 | commit | docs(78-02): scaffold 78-UAT.md + 78-HUMAN-UAT.md (D-12) |
| 389 | `7f6eaf4b` | 2026-04-21 | commit | test(78-02): Tests 1+2 pass - Remote + Local capacity mode happy path |
| 390 | `31cbd853` | 2026-04-21 | commit | test(78-02): Test 3 pass - three df-failure modes + per-tile independence |
| 391 | `cbfe3371` | 2026-04-21 | commit | test(78-02): Tests 4 + 5 pass - threshold colors + per-tile independence |
| 392 | `3d117417` | 2026-04-21 | commit | test(78-02): Test 6 pass - Download Speed + Active Tasks tiles unchanged |
| 393 | `eb6a6ac2` | 2026-04-21 | commit | docs(78-02): close UAT-03 - all 6 tests pass, stack torn down |
| 394 | `875ddc8a` | 2026-04-21 | commit | docs(78-02): SUMMARY.md - 6/6 pass, UAT-03 closed, stack down |
| 395 | `883f211d` | 2026-04-21 | commit | docs(78): verification pass + ROADMAP checkboxes ticked |
| 396 | `f31b630b` | 2026-04-21 | commit | docs(state): mark phase 78 complete in STATE.md |
| 397 | `9b747a55` | 2026-04-21 | commit | docs(79): capture phase context |
| 398 | `5b114fe7` | 2026-04-21 | commit | docs(state): record phase 79 context session |
| 399 | `81d4ebdb` | 2026-04-21 | commit | docs(79): add research + validation strategy |
| 400 | `08b2f71f` | 2026-04-21 | commit | docs(79): pattern mapping |
| 401 | `378c9683` | 2026-04-21 | commit | docs(79): create phase plans for test infra cleanup |
| 402 | `131c0742` | 2026-04-21 | commit | feat(79-01): add PYTHONWARNINGS env + -p no:cacheprovider to test Dockerfile |
| 403 | `eddd52e0` | 2026-04-21 | commit | chore(79-01): remove dead pytest config from pyproject.toml (D-02 + D-04) |
| 404 | `8c401af7` | 2026-04-21 | commit | docs(79-01): summary + webob cgi follow-up todo |
| 405 | `2253353c` | 2026-04-21 | commit | feat(79-02): add CSP-listener Playwright fixture (D-07 + D-09 + D-10) |
| 406 | `8d28c944` | 2026-04-21 | commit | refactor(79-02): swap 6 spec imports to csp-listener fixture (D-08) |
| 407 | `a5f8c886` | 2026-04-21 | commit | test(79-02): add CSP canary spec with seeded inline-script injection (D-11 + D-12 + D-13) |
| 408 | `3822b352` | 2026-04-21 | commit | docs(79-02): summary documenting runtime-verification deferrals |
| 409 | `48a472ab` | 2026-04-21 | commit | docs(state): begin phase 79 execution |
| 410 | `ed9ba481` | 2026-04-21 | merge | chore: merge plan 79-01 worktree (worktree-agent-aadb9e07) |
| 411 | `e3873a08` | 2026-04-21 | merge | chore: merge plan 79-02 worktree (worktree-agent-ad5e7a19) |
| 412 | `09c1b282` | 2026-04-21 | commit | docs(phase-79): mark plans 01 + 02 complete in ROADMAP |
| 413 | `d5aef1f3` | 2026-04-21 | commit | docs(79): add code review report |
| 414 | `501d47b5` | 2026-04-21 | commit | fix(79): apply deep-review findings #1, #3, #4 |
| 415 | `e88b1c5d` | 2026-04-21 | commit | test(79): persist verification report and human-UAT for CI-gated items |
| 416 | `e3f35af7` | 2026-04-21 | commit | docs(phase-79): complete phase execution |
| 417 | `ff7182b4` | 2026-04-21 | commit | docs(phase-79): evolve PROJECT.md after phase completion |
| 418 | `8ec5e75e` | 2026-04-21 | commit | docs(80): research phase domain - Dependabot override, arm64 rar, enum removal |
| 419 | `a2cf2ade` | 2026-04-21 | commit | docs(phase-80): add validation strategy |
| 420 | `c7185a1a` | 2026-04-21 | commit | plan(phase-80): add plans, patterns, validation map for small cleanups |
| 421 | `1cd54245` | 2026-04-21 | commit | plan(phase-80): shift VALIDATION row citations in Plans 02/03 after SEC-01 rows inserted |
| 422 | `40a1d912` | 2026-04-21 | commit | fix(80-01): add npm overrides to pin basic-ftp to ^5.3.0 |
| 423 | `b3f105b5` | 2026-04-21 | commit | feat(80-03): remove WAITING_FOR_IMPORT from Python enum and serializer dict |
| 424 | `d2170b28` | 2026-04-21 | commit | feat(80-03): remove WAITING_FOR_IMPORT from Angular TypeScript files |
| 425 | `8f04b6f7` | 2026-04-21 | commit | docs(80-01): complete Dependabot basic-ftp override plan summary |
| 426 | `85845771` | 2026-04-21 | commit | docs(80-03): append TECH-02 decision row to PROJECT.md Key Decisions |
| 427 | `7bc19d21` | 2026-04-21 | commit | chore(80-02): capture amd64 pytest collection baseline (pre-edit parity anchor) |
| 428 | `c0eb1552` | 2026-04-21 | commit | docs(80-03): complete WAITING_FOR_IMPORT enum removal plan - TECH-02 closed |
| 429 | `5b4df31a` | 2026-04-21 | commit | feat(80-02): arch-gate rar install in Python test Dockerfile |
| 430 | `ef5a9167` | 2026-04-21 | commit | feat(80-02): gate rar-dependent test classes with class-level skipIf |
| 431 | `78d4b069` | 2026-04-21 | commit | docs(80-02): partial summary - tasks 1-3 complete, task 4 awaiting arm64 human-verify |
| 432 | `4cee782e` | 2026-04-21 | merge | chore: merge executor worktree (80-01) |
| 433 | `802d933e` | 2026-04-21 | merge | chore: merge executor worktree (80-02) |
| 434 | `83f0a8eb` | 2026-04-21 | merge | chore: merge executor worktree (80-03) |
| 435 | `5cd93b07` | 2026-04-21 | commit | docs(phase-80): update tracking after wave 1 - 80-01 + 80-03 complete, 80-02 paused at Task 4 |
| 436 | `fe8143b5` | 2026-04-21 | commit | docs(80-02): close human-verify checkpoint - all 5 acceptance criteria passed |
| 437 | `6f440318` | 2026-04-22 | commit | docs(80): add code review report - clean (0/0/0) |
| 438 | `adf2d5b7` | 2026-04-22 | commit | docs(80): verifier pass - all 3 reqs (SEC-01, TECH-01, TECH-02) closed |
| 439 | `8feb7f14` | 2026-04-22 | commit | docs(81): research optional Fernet encryption at rest (SEC-02) |
| 440 | `befe9f16` | 2026-04-22 | commit | docs(81): add validation strategy |
| 441 | `9d5f6665` | 2026-04-22 | commit | docs(81): create phase plan - 3 waves for Fernet encryption at rest (SEC-02) |
| 442 | `39cf5d57` | 2026-04-22 | commit | wip: plan-phase 81 paused at plan-checker (usage limit) |
| 443 | `81a61584` | 2026-04-22 | commit | docs(81): add pattern map |
| 444 | `c85ffc42` | 2026-04-22 | commit | chore(81-01): add cryptography>=44.0.0,<47 to pyproject.toml and regenerate poetry.lock |
| 445 | `e5747d76` | 2026-04-22 | commit | feat(81-01): create common/encryption.py Fernet primitive module |
| 446 | `5b354215` | 2026-04-22 | commit | test(81-01): create test_encryption.py with 8 unit tests for encryption module |
| 447 | `da056a20` | 2026-04-22 | commit | docs(81-01): complete plan 01 summary - Fernet primitive module |
| 448 | `61a2cba5` | 2026-04-22 | merge | chore: merge executor worktree (worktree-agent-ab26c08e) |
| 449 | `6f489d27` | 2026-04-22 | commit | feat(81-02): add Config.Encryption inner class, _SECRET_FIELD_PATHS, set_keyfile_path, _decrypt_errors |
| 450 | `25ba8b4e` | 2026-04-22 | commit | feat(81-02): widen Config.from_str/to_str with Fernet encrypt/decrypt hooks |
| 451 | `ee4c733b` | 2026-04-22 | commit | test(81-02): add 6 SEC-02 test methods + golden-string update for [Encryption] section |
| 452 | `517b543d` | 2026-04-22 | commit | docs(81-02): complete Config.Encryption + serialization-seam encryption plan |
| 453 | `3ca6ea60` | 2026-04-22 | merge | chore: merge executor worktree (worktree-agent-aec58c61) |
| 454 | `324f171e` | 2026-04-22 | commit | feat(81-03): wire keyfile injection + re-encrypt hook + decrypt warnings in seedsyncarr.py |
| 455 | `c4bda69c` | 2026-04-22 | commit | test(81-03): add decrypt-warning + no-raise + startup re-encrypt tests |
| 456 | `d04d4d65` | 2026-04-22 | commit | docs(81-03): add [Encryption] section to CONFIGURATION.md |
| 457 | `3d67f1a4` | 2026-04-22 | commit | docs(81-03): complete seedsyncarr startup hooks + tests + CONFIGURATION.md plan |
| 458 | `71090bdb` | 2026-04-22 | merge | chore: merge executor worktree (worktree-agent-a200b6fd) |
| 459 | `6bf9b887` | 2026-04-22 | commit | fix(81): WR-01 catch ValueError from Fernet(key) for corrupted keyfiles |
| 460 | `dae3b977` | 2026-04-22 | commit | fix(81): WR-02 use Type["Config"] annotation on from_str classmethod |
| 461 | `cfc1b511` | 2026-04-22 | commit | docs(81): add code review report and fix report |
| 462 | `85cf3ade` | 2026-04-22 | commit | fix(81): address deep review findings - variable shadow, ValueError guard, redaction, test isolation |
| 463 | `eaf879ed` | 2026-04-22 | commit | fix(81): address all remaining deep review findings |
| 464 | `e9437387` | 2026-04-22 | commit | fix(81): address deep review round 2 - security boundary, error handling, test gaps |
| 465 | `03434774` | 2026-04-22 | commit | fix(81): address deep review round 3 - ConfigError guard, import facade, cause assertion |
| 466 | `c57e79d1` | 2026-04-22 | commit | fix(81): assert __suppress_context__ in EncryptionError test |
| 467 | `dee16e50` | 2026-04-22 | commit | docs(82): capture phase context .planning/phases/82-release-prep-retro-v110-notes-v111-tag/82-CONTEXT.md .planning/phases/82-release-prep-retro-v110-notes-v111-tag/82-DISCUSSION-LOG.md |
| 468 | `56fcacce` | 2026-04-22 | commit | docs(state): record phase 82 context session .planning/STATE.md |
| 469 | `21f6db8b` | 2026-04-22 | commit | docs(82): research release prep - changelog, version bump, deb packaging, CI /Users/julianamacbook/seedsyncarr/.planning/phases/82-release-prep-retro-v110-notes-v111-tag/82-RESEARCH.md |
| 470 | `1f5f515a` | 2026-04-22 | commit | chore(deps-dev): bump the npm_and_yarn group (#21) |
| 471 | `e8df26e1` | 2026-04-22 | commit | chore(deps-dev): bump puppeteer in the root group (#20) |
| 472 | `4a5ec459` | 2026-04-22 | commit | docs(82): create phase plan - 4 plans across 3 waves |
| 473 | `39059be6` | 2026-04-22 | merge | Merge branch 'main' of https://github.com/thejuran/seedsyncarr |
| 474 | `141f2598` | 2026-04-22 | commit | fix: replace ReDoS-vulnerable regex with linear scan in hasNestedQuantifiers |
| 475 | `2bf29119` | 2026-04-22 | commit | docs: mark SEC-01, SEC-02, TECH-01, TECH-02 complete in REQUIREMENTS.md |
| 476 | `fcfcaf6f` | 2026-04-22 | commit | chore(82-02): create debian/DEBIAN/control for deb packaging |
| 477 | `b9e70a37` | 2026-04-22 | commit | docs(82-01): add retroactive v1.1.0 CHANGELOG entry and create GitHub Release |
| 478 | `88cf332a` | 2026-04-22 | commit | feat(82-02): add publish-deb-package CI job and release-notes.md template |
| 479 | `a095849c` | 2026-04-22 | commit | docs(82-01): create plan summary for retroactive v1.1.0 release notes |
| 480 | `8c21bb2a` | 2026-04-22 | commit | docs(82-02): complete debian packaging infrastructure plan summary |
| 481 | `69672a9b` | 2026-04-22 | merge | chore: merge executor worktree (worktree-agent-a364cc80) |
| 482 | `4cd994be` | 2026-04-22 | merge | chore: merge executor worktree (worktree-agent-a9a0c74b) |
| 483 | `c0b0531f` | 2026-04-22 | commit | docs(82-03): write v1.1.1 CHANGELOG entry and populate release-notes.md |
| 484 | `3a81af3d` | 2026-04-22 | commit | chore(82-03): bump version strings from 1.0.0 to 1.1.1 across all version files |
| 485 | `51d7f1f5` | 2026-04-22 | commit | docs(82-03): complete v1.1.1 version bump and release notes plan |
| 486 | `b56448eb` | 2026-04-22 | merge | chore: merge executor worktree (worktree-agent-aead17b2) |
| 487 | `c871eb35` | 2026-04-22 | commit | fix: resolve lint errors in Python tests and Angular sources |
| 488 | `28d94f4d` | 2026-04-22 | commit | fix: remove unused Dict import in controller_persist.py |
| 489 | `46264224` | 2026-04-22 | commit | ci: gate Docker build on lint jobs passing |
| 490 | `52208429` | 2026-04-22 | commit | wip: phase 82 paused at 82-04 T2/T2 - awaiting CI verification .planning/phases/82-release-prep-retro-v110-notes-v111-tag/.continue-here.md .planning/HANDOFF.json |
| 491 | `269a3e86` | 2026-04-22 | commit | fix: self-host Phosphor Icons, drop Google Fonts and Debian packaging |
| 492 | `ad724d04` | 2026-04-22 | commit | fix(e2e): fix CSP canary, segment button locator, and seed timeouts |
| 493 | `49b6c1a2` | 2026-04-22 | commit | fix(e2e): fix whitespace-sensitive regex anchors in Playwright locators |
| 494 | `79bf4333` | 2026-04-22 | commit | fix(e2e): retry STOPPED seed when small files outrun the DOWNLOADING window |
| 495 | `257b6200` | 2026-04-22 | commit | fix(e2e): enlarge illusion.jpg fixture to 2MB so STOPPED seed can catch DOWNLOADING |
| 496 | `921b6b0c` | 2026-04-22 | commit | fix(e2e): stop immediately after queue instead of racing the DOWNLOADING badge |
| 497 | `c7359d74` | 2026-04-22 | commit | docs: capture todo - tighten Shield Semgrep rules + add security scan report |
| 498 | `61a92b8b` | 2026-04-22 | commit | fix(e2e): expose lftp rate_limit via config API to fix STOPPED seed race |
| 499 | `e95cf6cf` | 2026-04-22 | commit | fix(e2e): restore illusion.jpg to 2MB, fix UAT-01 toast->notification, fix UAT-02 sub=pending |
| 500 | `055d4b51` | 2026-04-22 | commit | fix(e2e): throttle lftp for UAT-01 Stop window, add retry guards for UAT-01/02 |
| 501 | `d82d242a` | 2026-04-22 | commit | fix(e2e): assert Extract disabled (no archive fixtures), fix UAT-02 sub=stopped URL param |
| 502 | `aa822e09` | 2026-04-22 | commit | fix(e2e): UAT-02 syncing sub-filter URL param is sub=downloading not sub=syncing |
| 503 | `9f451c51` | 2026-04-23 | commit | fix: replace docs symlink with file copy for PyPI sdist build |
| 504 | `394ba212` | 2026-04-23 | commit | fix: make bulk-actions bar fixed to bottom of viewport |
| 505 | `f54e32cb` | 2026-04-23 | commit | chore: bump version to 1.1.2 and add changelog entry |
| 506 | `82e2d2f9` | 2026-04-23 | commit | chore: close phase 82-04 summary and mark v1.1.1 milestone complete |
| 507 | `1715eb3b` | 2026-04-23 | commit | chore: archive v1.1.1 milestone - UAT passed, roadmap collapsed |
| 508 | `da1f0e3b` | 2026-04-23 | commit | chore: remove REQUIREMENTS.md - fresh for next milestone |
| 509 | `d80222fd` | 2026-04-23 | commit | chore: update STATE.md - v1.1.1 milestone archived |
| 510 | `5c7274da` | 2026-04-24 | commit | docs: start milestone v1.1.2 Test Suite Audit .planning/PROJECT.md .planning/STATE.md |
| 511 | `28b059bd` | 2026-04-24 | commit | docs: define milestone v1.1.2 requirements .planning/REQUIREMENTS.md |
| 512 | `27d4d210` | 2026-04-24 | commit | docs: create milestone v1.1.2 roadmap (4 phases) .planning/ROADMAP.md .planning/STATE.md .planning/REQUIREMENTS.md |
| 513 | `65ed7eb6` | 2026-04-24 | commit | docs(83): capture phase context |
| 514 | `87245e9a` | 2026-04-24 | commit | docs(state): record phase 83 context session |
| 515 | `6dbc8dd6` | 2026-04-24 | commit | docs(83): research phase python test audit .planning/phases/83-python-test-audit/83-RESEARCH.md |
| 516 | `3209e49a` | 2026-04-24 | commit | docs(83): add validation strategy .planning/phases/83-python-test-audit/83-VALIDATION.md |
| 517 | `b32e9153` | 2026-04-24 | commit | docs(83): create phase plan - verify zero staleness and record coverage baseline |
| 518 | `2fdf6731` | 2026-04-24 | commit | docs(83-01): verify zero staleness and record 85.05% coverage baseline |
| 519 | `9b848c0f` | 2026-04-24 | merge | chore: merge executor worktree (worktree-agent-aacef761dbf006574) |
| 520 | `6ef1b725` | 2026-04-24 | commit | fix: restore 83-01-SUMMARY.md removed by worktree cleanup false positive |
| 521 | `01de7f5e` | 2026-04-24 | commit | docs(phase-83): complete phase execution - zero stale tests, 85.05% coverage verified .planning/ROADMAP.md .planning/STATE.md .planning/phases/83-python-test-audit/83-VERIFICATION.md |
| 522 | `f3662dd7` | 2026-04-24 | commit | docs(phase-83): evolve PROJECT.md after phase completion .planning/PROJECT.md |
| 523 | `d2e3b65a` | 2026-04-24 | commit | docs(84): capture phase context .planning/phases/84-angular-test-audit/84-CONTEXT.md .planning/phases/84-angular-test-audit/84-DISCUSSION-LOG.md |
| 524 | `4ac2dacf` | 2026-04-24 | commit | docs(state): record phase 84 context session .planning/STATE.md |
| 525 | `1c9aa16e` | 2026-04-24 | commit | docs(84): research phase domain .planning/phases/84-angular-test-audit/84-RESEARCH.md |
| 526 | `55e4b628` | 2026-04-24 | commit | docs(84): add research and validation strategy .planning/phases/84-angular-test-audit/84-RESEARCH.md .planning/phases/84-angular-test-audit/84-VALIDATION.md |
| 527 | `aef5f7c7` | 2026-04-24 | commit | docs(84): create phase plan .planning/phases/84-angular-test-audit/84-01-PLAN.md .planning/phases/84-angular-test-audit/84-02-PLAN.md .planning/ROADMAP.md |
| 528 | `4d52c336` | 2026-04-24 | commit | docs(84): plan phase - 2 plans in 2 waves .planning/phases/84-angular-test-audit/84-01-PLAN.md .planning/phases/84-angular-test-audit/84-02-PLAN.md .planning/phases/84-angular-test-audit/84-PATTERNS.md .planning/STATE.md .planning/ROADMAP.md |
| 529 | `50d8eeab` | 2026-04-24 | commit | docs(84-01): Angular staleness audit - zero stale tests, coverage baseline 83.34%/69.01%/79.73%/84.21% |
| 530 | `f46495da` | 2026-04-24 | merge | chore: merge executor worktree (worktree-agent-ad33d0f7815ce268d) |
| 531 | `d9916dd0` | 2026-04-24 | commit | docs(phase-84): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 532 | `0b77e541` | 2026-04-24 | commit | refactor(84-02): migrate 6 spec files from HttpClientTestingModule to provideHttpClient API |
| 533 | `d555e629` | 2026-04-24 | commit | docs(84-02): verify karma.conf.js angularCli key, document post-migration coverage and CI noise cleanup |
| 534 | `905d85a7` | 2026-04-24 | commit | docs(84-02): complete HttpClientTestingModule migration plan - SUMMARY.md |
| 535 | `e2a53fe9` | 2026-04-24 | merge | chore: merge executor worktree (worktree-agent-a526b00a2477db174) |
| 536 | `6609405c` | 2026-04-24 | commit | docs(phase-84): update tracking after wave 2 .planning/ROADMAP.md .planning/STATE.md |
| 537 | `08a8f059` | 2026-04-24 | commit | fix(84): restore phase 84 planning artifacts lost during worktree merge |
| 538 | `85aff071` | 2026-04-24 | commit | fix(84): move phase 84 artifacts to correct .planning/ path |
| 539 | `80d28075` | 2026-04-24 | commit | docs(84): add code review report |
| 540 | `dca117bc` | 2026-04-24 | commit | fix(84): WR-01 add afterEach httpMock.verify in autoqueue.service.spec.ts |
| 541 | `b5156944` | 2026-04-24 | commit | fix(84): WR-02 add afterEach httpMock.verify in config.service.spec.ts |
| 542 | `442638f7` | 2026-04-24 | commit | fix(84): WR-03 add afterEach httpMock.verify in server-command.service.spec.ts |
| 543 | `b23fe01b` | 2026-04-24 | commit | docs(84): add code review fix report |
| 544 | `05cf88c4` | 2026-04-24 | commit | fix(84): flush init requests in instance-creation tests |
| 545 | `40fb5658` | 2026-04-24 | commit | docs(phase-84): complete phase execution .planning/ROADMAP.md .planning/STATE.md .planning/phases/84-angular-test-audit/84-VERIFICATION.md |
| 546 | `69008dc6` | 2026-04-24 | commit | docs(phase-84): evolve PROJECT.md after phase completion .planning/PROJECT.md |
| 547 | `0b761844` | 2026-04-24 | commit | docs(phase-84): add security threat verification |
| 548 | `493405e6` | 2026-04-24 | commit | docs(phase-84): add/update validation strategy |
| 549 | `62672660` | 2026-04-24 | commit | docs(roadmap): add gap closure phases 85-86 from milestone audit |
| 550 | `4adbbbe1` | 2026-04-24 | commit | docs(phase-85): research E2E test audit domain |
| 551 | `fb7dfd86` | 2026-04-24 | commit | docs(phase-85): add validation strategy .planning/phases/85-e2e-test-audit/85-VALIDATION.md |
| 552 | `81159d25` | 2026-04-24 | commit | docs(85): create E2E test audit phase plan .planning/phases/85-e2e-test-audit/85-01-PLAN.md .planning/ROADMAP.md |
| 553 | `d0197284` | 2026-04-24 | commit | docs(85-01): complete E2E staleness audit -- zero removals, all 7 specs LIVE |
| 554 | `85d8b85e` | 2026-04-24 | merge | chore: merge executor worktree (worktree-agent-aa4a6da691cfa54a5) |
| 555 | `9cf6fdea` | 2026-04-24 | commit | docs(phase-85): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 556 | `a6b75e42` | 2026-04-24 | commit | test(85): persist human verification items as UAT .planning/phases/85-e2e-test-audit/85-HUMAN-UAT.md .planning/phases/85-e2e-test-audit/85-VERIFICATION.md |
| 557 | `4ef353a7` | 2026-04-24 | commit | test(85): complete E2E harness UAT - 33 passed, 2 pre-existing arm64 sort failures .planning/phases/85-e2e-test-audit/85-HUMAN-UAT.md .planning/phases/85-e2e-test-audit/85-VERIFICATION.md |
| 558 | `ea6eda43` | 2026-04-24 | commit | docs(phase-85): complete phase execution .planning/ROADMAP.md .planning/STATE.md .planning/phases/85-e2e-test-audit/85-VERIFICATION.md |
| 559 | `88be892f` | 2026-04-24 | commit | docs(phase-85): evolve PROJECT.md after phase completion .planning/PROJECT.md |
| 560 | `c0f6ced7` | 2026-04-24 | commit | docs(86): capture phase context .planning/phases/86-final-validation/86-CONTEXT.md .planning/phases/86-final-validation/86-DISCUSSION-LOG.md |
| 561 | `57212bba` | 2026-04-24 | commit | docs(state): record phase 86 context session .planning/STATE.md |
| 562 | `a1ca7df1` | 2026-04-24 | commit | docs(86): research phase domain .planning/phases/86-final-validation/86-RESEARCH.md |
| 563 | `b6d2994f` | 2026-04-24 | commit | docs(phase-86): add validation strategy .planning/phases/86-final-validation/86-VALIDATION.md |
| 564 | `e982b2a0` | 2026-04-24 | commit | docs(86): create phase plan .planning/phases/86-final-validation/86-01-PLAN.md .planning/phases/86-final-validation/86-02-PLAN.md .planning/ROADMAP.md |
| 565 | `ac6258ed` | 2026-04-24 | commit | fix(86-01): enable autoqueue in E2E harness + create arm64 sort todo |
| 566 | `5fbf78ae` | 2026-04-24 | commit | docs(86-01): complete fix-autoqueue-harness plan summary |
| 567 | `a34b685e` | 2026-04-24 | merge | chore: merge executor worktree (worktree-agent-a8ec981580cc208e9) |
| 568 | `d236c2b6` | 2026-04-24 | commit | docs(phase-86): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 569 | `e7716cd7` | 2026-04-24 | commit | docs(86-02): document coverage baselines and ship v1.1.2 milestone |
| 570 | `30ca6d89` | 2026-04-24 | commit | docs(86-02): complete verify-ci-green plan - v1.1.2 milestone shipped |
| 571 | `78a2d837` | 2026-04-24 | commit | docs(86): add code review report |
| 572 | `74ed4c42` | 2026-04-24 | commit | fix(86): WR-01 add set -euo pipefail to prevent silent failures |
| 573 | `605809b6` | 2026-04-24 | commit | fix(86): WR-02 add --fail flag to curl calls to catch HTTP errors |
| 574 | `c72fd6f2` | 2026-04-24 | commit | docs(86): add code review fix report |
| 575 | `2b3ec133` | 2026-04-24 | commit | chore: archive v1.1.2 milestone files |
| 576 | `dbfe3021` | 2026-04-24 | commit | chore: remove REQUIREMENTS.md for v1.1.2 milestone, add retrospective |
| 577 | `f722adb6` | 2026-04-24 | commit | chore: archive v1.1.2 phase directories to milestones/ |
| 578 | `65656e58` | 2026-04-24 | commit | docs: add backlog 999.1-999.7 from full-suite deep code review |
| 579 | `c17cb0d5` | 2026-04-24 | commit | docs: start milestone v1.2.0 Test & Quality Hardening .planning/PROJECT.md .planning/STATE.md .planning/todos/pending/2026-04-24-migrate-config-set-to-post-body.md .planning/todos/done/2026-04-14-encrypt-credentials-at-rest.md .planning/todos/done/2026-02-08-clean-up-test-warnings.md .planning/todos/pending/2026-04-14-encrypt-credentials-at-rest.md .planning/todos/pending/2026-02-08-clean-up-test-warnings.md |
| 580 | `6ed8c6f3` | 2026-04-24 | commit | docs: define milestone v1.2.0 requirements .planning/REQUIREMENTS.md |
| 581 | `bf4831e7` | 2026-04-24 | commit | docs: create milestone v1.2.0 roadmap (10 phases) .planning/ROADMAP.md .planning/STATE.md .planning/REQUIREMENTS.md |
| 582 | `884bed58` | 2026-04-24 | commit | docs(87): capture phase context .planning/phases/87-python-test-fixes-critical-warning/87-CONTEXT.md .planning/phases/87-python-test-fixes-critical-warning/87-DISCUSSION-LOG.md |
| 583 | `d140395e` | 2026-04-24 | commit | docs(state): record phase 87 context session .planning/STATE.md |
| 584 | `be0cfec4` | 2026-04-24 | commit | docs(87): research phase python test fixes .planning/phases/87-python-test-fixes-critical-warning/87-RESEARCH.md |
| 585 | `558e69fd` | 2026-04-24 | commit | docs(87): add validation strategy .planning/phases/87-python-test-fixes-critical-warning/87-VALIDATION.md |
| 586 | `8e8a498c` | 2026-04-24 | commit | docs(87): create phase plan .planning/phases/87-python-test-fixes-critical-warning/87-01-PLAN.md .planning/phases/87-python-test-fixes-critical-warning/87-02-PLAN.md .planning/ROADMAP.md |
| 587 | `d82c39ae` | 2026-04-24 | commit | fix(87-02): chmod scope, logger fixture reset, explicit ANY imports (PYFIX-06/07/08) |
| 588 | `7a061126` | 2026-04-24 | commit | fix(87-01): fix critical false-coverage bugs PYFIX-01 and PYFIX-02 |
| 589 | `87349949` | 2026-04-24 | commit | fix(87-02): wrap bare open() calls in context managers (PYFIX-09/10) |
| 590 | `8a353852` | 2026-04-24 | commit | docs(87-02): complete warning-level python test fixes plan .planning/phases/87-python-test-fixes-critical-warning/87-02-SUMMARY.md |
| 591 | `4c70437b` | 2026-04-24 | commit | fix(87-01): fix temp file leaks and mock guard PYFIX-03 PYFIX-04 PYFIX-05 |
| 592 | `ae68ef4b` | 2026-04-24 | commit | docs(87-01): complete python-test-fixes-critical-warning plan 01 |
| 593 | `98a60cda` | 2026-04-24 | merge | chore: merge executor worktree (worktree-agent-a15389083549ecfeb) |
| 594 | `ac5e9045` | 2026-04-24 | commit | docs(phase-87): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 595 | `6ebaff42` | 2026-04-24 | commit | docs(87): add code review report |
| 596 | `4ab3161d` | 2026-04-24 | commit | fix(87): address deep review findings - dead imports, unclosed handle, qualified ANY |
| 597 | `ceb03d18` | 2026-04-24 | commit | docs(88): capture phase context .planning/phases/88-python-test-fixes-medium-cleanup/88-CONTEXT.md .planning/phases/88-python-test-fixes-medium-cleanup/88-DISCUSSION-LOG.md |
| 598 | `de910474` | 2026-04-24 | commit | docs(state): record phase 88 context session .planning/STATE.md |
| 599 | `b8a2577a` | 2026-04-24 | commit | docs(88): research phase domain for Python test fixes medium & cleanup |
| 600 | `65f9a632` | 2026-04-24 | commit | docs(88): add research, validation strategy, and pattern map .planning/phases/88-python-test-fixes-medium-cleanup/88-RESEARCH.md .planning/phases/88-python-test-fixes-medium-cleanup/88-VALIDATION.md .planning/phases/88-python-test-fixes-medium-cleanup/88-PATTERNS.md |
| 601 | `1ab28a48` | 2026-04-24 | commit | docs(88): create phase plan - 3 plans in 1 wave for PYFIX-11 through PYFIX-19 |
| 602 | `4c2d3094` | 2026-04-24 | commit | fix(88-01): PYFIX-11/14/15/19 - XSS test, tmpdir cleanup, bottle import, conditional assertion |
| 603 | `967699f5` | 2026-04-24 | commit | fix(88-02): scanner busy-wait CPU spin + lftp handler leak (PYFIX-12, PYFIX-16) |
| 604 | `b9aa9c21` | 2026-04-24 | commit | fix(88-01): PYFIX-16 - remove logger handler in tearDown for 3 integration test files |
| 605 | `583ad619` | 2026-04-24 | commit | perf(88-03): replace time.sleep with Event-based sync in dispatch and extract_process tests |
| 606 | `2ff698d0` | 2026-04-24 | commit | fix(88-02): inject time.sleep(0.01) into all 41 lftp busy-wait loops (PYFIX-18) |
| 607 | `24de838d` | 2026-04-24 | commit | docs(88-01): complete plan 01 summary - PYFIX-11/14/15/16/19 medium test fixes |
| 608 | `5f93e1cd` | 2026-04-24 | commit | perf(88-03): replace sleep-based sync with deterministic join and fix handler leak |
| 609 | `89762b6e` | 2026-04-24 | commit | docs(88-02): complete busy-wait CPU fix and handler leak fix plan summary |
| 610 | `87a32eaf` | 2026-04-24 | commit | docs(88-03): complete plan 03 summary |
| 611 | `161fa985` | 2026-04-24 | merge | chore: merge executor worktree 88-01 (worktree-agent-a234febed1501959b) |
| 612 | `b9bd731e` | 2026-04-24 | merge | chore: merge executor worktree 88-02 (worktree-agent-a272cfab1009c21e9) |
| 613 | `ddc087d8` | 2026-04-24 | merge | chore: merge executor worktree 88-03 (worktree-agent-a2f700b5ebe925b93) |
| 614 | `c3098842` | 2026-04-24 | commit | docs(phase-88): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 615 | `f5bc3c23` | 2026-04-24 | commit | docs(88): add code review report |
| 616 | `53df313e` | 2026-04-24 | commit | fix(88): WR-01 use 'or' instead of 'and' in busy-wait condition to wait for both extraction and callback |
| 617 | `df4bd057` | 2026-04-24 | commit | fix(88): WR-02 remove logging handler leak in TestExtractDispatch and TestExtractDispatchThreadSafety |
| 618 | `68adc56e` | 2026-04-24 | commit | fix(88): WR-03 remove logging handler leak in TestExtractProcess |
| 619 | `d1b130db` | 2026-04-24 | commit | fix(88): WR-04 remove logging handler leak in TestScannerProcess |
| 620 | `b94ce1ba` | 2026-04-24 | commit | fix(88): WR-05 add thread liveness check after join with timeout in test_job |
| 621 | `a68d3bd7` | 2026-04-24 | commit | docs(88): add code review fix report |
| 622 | `705c00ae` | 2026-04-24 | commit | docs(phase-88): complete phase execution - verification passed, requirements updated .planning/ROADMAP.md .planning/STATE.md .planning/REQUIREMENTS.md .planning/phases/88-python-test-fixes-medium-cleanup/88-VERIFICATION.md |
| 623 | `5107f7be` | 2026-04-24 | commit | docs(phase-88): evolve PROJECT.md after phase completion .planning/PROJECT.md |
| 624 | `6bc74c54` | 2026-04-24 | commit | docs(phase-88): add security threat verification |
| 625 | `4a3e1a76` | 2026-04-24 | commit | fix(88): increase IPC queue drain sleep from 50ms to 200ms in test_multiprocessing_logger |
| 626 | `86e4b09b` | 2026-04-24 | commit | docs(phase-88): add/update validation strategy |
| 627 | `93b2862e` | 2026-04-24 | commit | docs(v1.2.0): add milestone audit report - 19/68 requirements satisfied |
| 628 | `b2b479d3` | 2026-04-25 | commit | docs(89): research phase domain .planning/phases/89-python-test-architecture/89-RESEARCH.md |
| 629 | `0a627782` | 2026-04-25 | commit | docs(phase-89): add research and validation strategy .planning/phases/89-python-test-architecture/89-RESEARCH.md .planning/phases/89-python-test-architecture/89-VALIDATION.md |
| 630 | `b2f540d7` | 2026-04-25 | commit | docs(89): create phase plan - Python test architecture refactoring |
| 631 | `a5276e7c` | 2026-04-25 | commit | docs(phase-89): add execution plans .planning/phases/89-python-test-architecture/89-01-PLAN.md .planning/phases/89-python-test-architecture/89-02-PLAN.md .planning/phases/89-python-test-architecture/89-PATTERNS.md |
| 632 | `abce35a2` | 2026-04-25 | commit | docs(89-02): document Python test coverage gaps (PYARCH-04) |
| 633 | `628ef4b2` | 2026-04-25 | commit | docs(89-02): document name-mangling trade-off in Python tests (PYARCH-05) |
| 634 | `fa77c3ab` | 2026-04-25 | commit | docs(89-02): complete Python test architecture documentation plan |
| 635 | `c0348936` | 2026-04-25 | commit | refactor(89-01): extract test helpers, conftest delegation, and BaseControllerTestCase |
| 636 | `ead4c33e` | 2026-04-25 | commit | refactor(89-01): move misclassified integration test, extract INI template |
| 637 | `a2f77606` | 2026-04-25 | commit | docs(89-01): complete python test infrastructure refactoring plan |
| 638 | `8bb941bd` | 2026-04-25 | merge | chore: merge executor worktree (worktree-agent-acf93b0825f48e127) |
| 639 | `2cde2363` | 2026-04-25 | merge | chore: merge executor worktree (worktree-agent-a1457c6286d8d4d75) |
| 640 | `61d0ad4e` | 2026-04-25 | commit | docs(phase-89): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 641 | `a2c085f0` | 2026-04-25 | commit | docs(89): add code review report |
| 642 | `ce4fb411` | 2026-04-25 | commit | docs(90): capture phase context .planning/phases/90-angular-test-fixes/90-CONTEXT.md .planning/phases/90-angular-test-fixes/90-DISCUSSION-LOG.md |
| 643 | `d0a4ba1c` | 2026-04-25 | commit | docs(state): record phase 90 context session .planning/STATE.md |
| 644 | `a938bb08` | 2026-04-25 | commit | docs(90): research phase domain .planning/phases/90-angular-test-fixes/90-RESEARCH.md |
| 645 | `eb527f44` | 2026-04-25 | commit | docs(90): add research and validation strategy .planning/phases/90-angular-test-fixes/90-RESEARCH.md .planning/phases/90-angular-test-fixes/90-VALIDATION.md |
| 646 | `28dfdaa6` | 2026-04-25 | commit | docs(90): create phase plan .planning/phases/90-angular-test-fixes/90-01-PLAN.md .planning/phases/90-angular-test-fixes/90-02-PLAN.md .planning/ROADMAP.md |
| 647 | `4be045d3` | 2026-04-25 | commit | fix(90-01): add discardPeriodicTasks to stream-service.registry.spec.ts |
| 648 | `4e92875e` | 2026-04-25 | commit | fix(90-01): fix double-cast type erasure and add toBeDefined guards |
| 649 | `9ac7de56` | 2026-04-25 | commit | fix(90-02): add subscription teardown to view-file and notification service specs |
| 650 | `2ffbc5a1` | 2026-04-25 | commit | docs(90-01): complete angular test fixes - zone cleanup, type safety, assertion guards |
| 651 | `5dddfe58` | 2026-04-25 | commit | fix(90-02): add subscription teardown to file-selection and transfer-row specs |
| 652 | `1bf3c674` | 2026-04-25 | commit | docs(90-02): complete angular subscription leak fixes plan |
| 653 | `bae0654d` | 2026-04-25 | merge | chore: merge executor worktree (worktree-agent-a127c1976e7c18526) |
| 654 | `4a034bd7` | 2026-04-25 | merge | chore: merge executor worktree (worktree-agent-a592dde3a2423a75f) |
| 655 | `2f38ccd5` | 2026-04-25 | commit | docs(phase-90): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 656 | `cf2d5a7b` | 2026-04-25 | commit | docs(90): add code review report |
| 657 | `0a0b19e5` | 2026-04-25 | commit | fix(90): WR-01 correct stale DELETED comments to EXTRACTING and EXTRACTED |
| 658 | `9b6738dd` | 2026-04-25 | commit | docs(90): add code review fix report |
| 659 | `951b1299` | 2026-04-27 | commit | fix(90): address deep review findings - type safety and zone cleanup |
| 660 | `b0397c13` | 2026-04-27 | commit | docs(phase-90): complete phase execution .planning/ROADMAP.md .planning/STATE.md .planning/phases/90-angular-test-fixes/90-VERIFICATION.md |
| 661 | `fd459603` | 2026-04-27 | commit | docs(phase-90): evolve PROJECT.md after phase completion .planning/PROJECT.md |
| 662 | `33cc238c` | 2026-04-27 | commit | docs(91): capture phase context .planning/phases/91-e2e-test-fixes-platform/91-CONTEXT.md .planning/phases/91-e2e-test-fixes-platform/91-DISCUSSION-LOG.md |
| 663 | `a045a316` | 2026-04-27 | commit | docs(state): record phase 91 context session .planning/STATE.md |
| 664 | `b6b602d1` | 2026-04-27 | commit | docs(91): research phase domain .planning/phases/91-e2e-test-fixes-platform/91-RESEARCH.md |
| 665 | `f5c7bcd0` | 2026-04-27 | commit | docs(phase-91): add validation strategy .planning/phases/91-e2e-test-fixes-platform/91-VALIDATION.md |
| 666 | `8d5dea4d` | 2026-04-27 | commit | docs(91): create phase plan .planning/phases/91-e2e-test-fixes-platform/91-01-PLAN.md .planning/phases/91-e2e-test-fixes-platform/91-02-PLAN.md .planning/ROADMAP.md |
| 667 | `e9e79f7b` | 2026-04-27 | commit | refactor(91-01): extract escapeRegex to shared helpers.ts (E2EFIX-07) |
| 668 | `8287ed1a` | 2026-04-27 | commit | fix(91-01): fix innerHTML and :has-text() in AutoQueue page object (E2EFIX-01, E2EFIX-05) |
| 669 | `01173a14` | 2026-04-27 | commit | docs(91-01): complete E2E page object API fixes plan summary |
| 670 | `a22f852a` | 2026-04-27 | commit | fix(91-02): navigate before API calls in settings-error.spec.ts beforeEach |
| 671 | `873322d1` | 2026-04-27 | commit | fix(91-02): fix dashboard spec - remove waitForTimeout, add response assertions, CSP comments |
| 672 | `ff36b171` | 2026-04-27 | commit | feat(91-02): add locale en-US to playwright.config.ts for arm64 sort determinism |
| 673 | `94e1fa0b` | 2026-04-27 | commit | docs(91-02): complete E2E spec quality fixes and arm64 platform config plan |
| 674 | `f9496946` | 2026-04-27 | commit | docs(phase-91): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 675 | `71c5c5e7` | 2026-04-27 | commit | docs(91): add code review report |
| 676 | `72e10f62` | 2026-04-27 | commit | fix(91): use exact regex match in addPattern instead of substring match |
| 677 | `dbfe3cee` | 2026-04-27 | commit | docs(92): research phase domain .planning/phases/92-e2e-infrastructure/92-RESEARCH.md |
| 678 | `5748a828` | 2026-04-27 | commit | docs(92): add research and validation strategy .planning/phases/92-e2e-infrastructure/92-RESEARCH.md .planning/phases/92-e2e-infrastructure/92-VALIDATION.md |
| 679 | `8a705ad2` | 2026-04-27 | commit | docs(92): create phase plan for E2E infrastructure fixes |
| 680 | `1d84bc77` | 2026-04-27 | commit | fix(92-01): initialize SERVER_UP and SCAN_DONE variables before polling loops |
| 681 | `4ad8d406` | 2026-04-27 | commit | fix(92-01): add myapp healthcheck and service_healthy dependency condition |
| 682 | `ae4370f1` | 2026-04-27 | commit | docs(92-01): create execution summary |
| 683 | `b9b1fac7` | 2026-04-27 | commit | docs(92-01): complete plan 01 - update state, roadmap, requirements |
| 684 | `53f3bb9a` | 2026-04-27 | commit | fix(92-02): replace bare except with specific exceptions and add __main__ guard |
| 685 | `ca31b08d` | 2026-04-27 | commit | docs(92-02): create execution summary |
| 686 | `a87c9005` | 2026-04-27 | commit | docs(92): add code review report |
| 687 | `71f49f90` | 2026-04-27 | commit | fix(92): WR-01 add -f flag to curl calls to surface HTTP errors |
| 688 | `0787708e` | 2026-04-27 | commit | fix(92): WR-02 add set -euo pipefail and modernize command substitution |
| 689 | `85206949` | 2026-04-27 | commit | docs(92): add code review fix report |
| 690 | `6bce7f0d` | 2026-04-27 | commit | fix(92): guard diagnostic curl against set -e and harden parse_status.py exceptions |
| 691 | `ee36d635` | 2026-04-27 | commit | fix(92): guard tput calls against missing TERM in Docker CI |
| 692 | `9895e9be` | 2026-04-27 | commit | docs(92): add verification report and human UAT items |
| 693 | `c0a4c04a` | 2026-04-27 | commit | docs(phase-92): complete phase execution .planning/ROADMAP.md .planning/STATE.md .planning/phases/92-e2e-infrastructure/92-VERIFICATION.md |
| 694 | `91f0729c` | 2026-04-27 | commit | docs(phase-92): add security threat verification |
| 695 | `64b3f32e` | 2026-04-27 | commit | test(phase-92): add Nyquist validation tests |
| 696 | `10410196` | 2026-04-27 | commit | docs(phase-92): add/update validation strategy |
| 697 | `41b98dfa` | 2026-04-27 | commit | fix(phase-92): commit E2EINFRA-03 and fix stale milestone tracking |
| 698 | `fa38aa83` | 2026-04-28 | commit | docs(93): research CI and Docker hardening phase .planning/phases/93-ci-docker-hardening/93-RESEARCH.md |
| 699 | `c17aee1e` | 2026-04-28 | commit | docs(phase-93): add validation strategy .planning/phases/93-ci-docker-hardening/93-VALIDATION.md |
| 700 | `a29e21e9` | 2026-04-28 | commit | docs(93): create phase plan for CI & Docker hardening |
| 701 | `333f3266` | 2026-04-28 | commit | feat(93-02): harden python test container Dockerfile and entrypoint |
| 702 | `20023e89` | 2026-04-28 | commit | feat(93-01): harden CI permissions, pin actions to SHA, fix job ordering |
| 703 | `0fa2d572` | 2026-04-28 | commit | feat(93-01): add conditional registry cache to Makefile tests-python target |
| 704 | `0565cae4` | 2026-04-28 | commit | docs(93-01): complete CI hardening plan summary |
| 705 | `7c97fe0c` | 2026-04-28 | commit | feat(93-02): remove password-auth tests from all three test files |
| 706 | `a48fdf2d` | 2026-04-28 | commit | docs(93-02): complete python test container SSH hardening plan |
| 707 | `e55d73ba` | 2026-04-28 | merge | chore: merge executor worktree (worktree-agent-a7fd661cbc8cbdcb5) |
| 708 | `6f67cb3a` | 2026-04-28 | merge | chore: merge executor worktree (93-01 CI workflow hardening) |
| 709 | `f732bf56` | 2026-04-28 | merge | chore: merge executor worktree (93-02 Python test container hardening) |
| 710 | `0738dfb3` | 2026-04-28 | commit | docs(phase-93): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 711 | `7c4ab43d` | 2026-04-28 | commit | feat(93-03): harden E2E remote Dockerfile with ephemeral key and non-root sshd |
| 712 | `747a7561` | 2026-04-28 | commit | feat(93-03): wire ephemeral SSH key flow into Makefile, compose, and setup script |
| 713 | `d0b5d619` | 2026-04-28 | commit | docs(93-03): complete E2E remote container SSH hardening plan |
| 714 | `bbdc6833` | 2026-04-28 | merge | chore: merge executor worktree (93-03 E2E remote container hardening) |
| 715 | `452432ad` | 2026-04-28 | commit | docs(phase-93): update tracking after wave 2 .planning/ROADMAP.md .planning/STATE.md |
| 716 | `96e5f8c5` | 2026-04-28 | commit | docs(93): add code review report |
| 717 | `3aeae1ab` | 2026-04-28 | commit | fix(93): CR-01 quote GitHub Actions expressions to prevent shell injection |
| 718 | `5063c62d` | 2026-04-28 | commit | fix(93): WR-01 remove residual chpasswd for seedsyncarrtest user |
| 719 | `8a38e1bf` | 2026-04-28 | commit | fix(93): WR-02 quote $@ in entrypoint.sh to prevent word splitting |
| 720 | `e06699be` | 2026-04-28 | commit | fix(93): WR-03 report SSH keygen failures instead of silently ignoring |
| 721 | `db1d9d3d` | 2026-04-28 | commit | fix(93): WR-04 document that remote service requires Makefile build for SSH_PUBKEY |
| 722 | `fef0d57c` | 2026-04-28 | commit | docs(93): add code review fix report |
| 723 | `b9cb2ff4` | 2026-04-28 | commit | fix(93): apply deep review hardening across CI, Docker, and test suites |
| 724 | `c5263435` | 2026-04-28 | commit | docs(93): add phase verification report |
| 725 | `a1cf75b4` | 2026-04-28 | commit | test(93): persist human verification items as UAT |
| 726 | `6eb9eda4` | 2026-04-28 | commit | fix(93): fix SSH key auth broken by account lock and missing group ownership |
| 727 | `7f7451d3` | 2026-04-28 | commit | docs(93): update UAT results - all tests passing |
| 728 | `b2dfaf54` | 2026-04-28 | commit | docs(phase-93): complete phase execution .planning/ROADMAP.md .planning/STATE.md .planning/REQUIREMENTS.md .planning/phases/93-ci-docker-hardening/93-VERIFICATION.md |
| 729 | `ea2ef392` | 2026-04-28 | commit | docs(phase-93): evolve PROJECT.md after phase completion .planning/PROJECT.md |
| 730 | `931900f4` | 2026-04-28 | commit | docs(94): capture phase context .planning/phases/94-test-coverage-backend/94-CONTEXT.md .planning/phases/94-test-coverage-backend/94-DISCUSSION-LOG.md |
| 731 | `f89ac00d` | 2026-04-28 | commit | docs(state): record phase 94 context session .planning/STATE.md |
| 732 | `e14c7bb5` | 2026-04-28 | commit | docs(94): research phase domain .planning/phases/94-test-coverage-backend/94-RESEARCH.md |
| 733 | `46f69014` | 2026-04-28 | commit | docs(phase-94): add validation strategy .planning/phases/94-test-coverage-backend/94-VALIDATION.md |
| 734 | `27b2b731` | 2026-04-28 | commit | docs(94): create phase plan -- backend test coverage |
| 735 | `70ae3745` | 2026-04-28 | commit | docs(94): create phase plan |
| 736 | `d76e6c90` | 2026-04-28 | commit | feat(94-01): convert helpers.py to package and add WSGI stream harness |
| 737 | `9221a38b` | 2026-04-28 | commit | feat(94-01): unskip and update SSE streaming integration tests |
| 738 | `bdc474af` | 2026-04-28 | commit | test(94-02): add webhook integration tests through Bottle web layer |
| 739 | `02d80c77` | 2026-04-28 | commit | test(94-02): add DeleteRemoteProcess unit tests |
| 740 | `f482025a` | 2026-04-28 | commit | test(94-02): add ActiveScanner unit tests |
| 741 | `e2cbd6b8` | 2026-04-28 | commit | docs(94-02): complete backend test coverage plan 02 |
| 742 | `a0135a49` | 2026-04-28 | commit | docs(94-01): complete SSE streaming integration tests plan |
| 743 | `3b8aa7f4` | 2026-04-28 | commit | docs(phase-94): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 744 | `243fedc8` | 2026-04-28 | commit | docs(94): add code review report |
| 745 | `2d4147c6` | 2026-04-28 | commit | fix(94): WR-01 remove assertion from Timer thread that silently swallows failures |
| 746 | `e71a7eca` | 2026-04-28 | commit | fix(94): WR-02 remove dead mock setup for serialize.model() never called by production code |
| 747 | `37a0dad4` | 2026-04-28 | commit | fix(94): WR-03 connect test logger handler to production logger name DeleteRemoteProcess |
| 748 | `e8e79d04` | 2026-04-28 | commit | docs(94): add code review fix report |
| 749 | `da890413` | 2026-04-28 | commit | fix(94): harden test assertions from deep code review |
| 750 | `f8ca322b` | 2026-04-28 | commit | docs(95): capture phase context .planning/phases/95-test-coverage-e2e/95-CONTEXT.md .planning/phases/95-test-coverage-e2e/95-DISCUSSION-LOG.md |
| 751 | `5379d0f3` | 2026-04-28 | commit | docs(state): record phase 95 context session .planning/STATE.md |
| 752 | `e05e00a0` | 2026-04-28 | commit | docs(95): research phase domain .planning/phases/95-test-coverage-e2e/95-RESEARCH.md |
| 753 | `8aa93c42` | 2026-04-28 | commit | docs(95): create phase plan .planning/phases/95-test-coverage-e2e/95-01-PLAN.md .planning/phases/95-test-coverage-e2e/95-02-PLAN.md .planning/ROADMAP.md |
| 754 | `00d728ef` | 2026-04-28 | commit | docs(95): create phase plan |
| 755 | `c5ead8db` | 2026-04-28 | commit | feat(95-02): extend SettingsPage with config field methods |
| 756 | `8453f44e` | 2026-04-28 | commit | feat(95-01): add LogsPage page object with all locator methods |
| 757 | `a38b42eb` | 2026-04-28 | commit | feat(95-02): create Settings fields E2E spec |
| 758 | `e657c38a` | 2026-04-28 | commit | feat(95-01): add Logs page E2E specs with structural smoke tests |
| 759 | `588a4060` | 2026-04-28 | commit | docs(95-01): complete Logs page E2E plan summary |
| 760 | `efb5b4b5` | 2026-04-28 | commit | docs(95-02): complete Settings fields E2E plan summary |
| 761 | `352d97db` | 2026-04-28 | merge | chore: merge executor worktree (worktree-agent-a7814b6d5c09e8514) |
| 762 | `2fa6d382` | 2026-04-28 | commit | docs(phase-95): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 763 | `9e7b11d2` | 2026-04-28 | commit | docs(95): add code review report |
| 764 | `52dff572` | 2026-04-28 | commit | fix(95): WR-01 log and re-throw afterEach restoration failures |
| 765 | `89717a44` | 2026-04-28 | commit | fix(95): WR-02 add access-log warning and use synthetic API key string |
| 766 | `628e55b6` | 2026-04-28 | commit | fix(95): WR-03 remove unused getConnectionDot() method |
| 767 | `1014add7` | 2026-04-28 | commit | fix(95): WR-04 remove unused getSaveSettingsButton() method |
| 768 | `3a507d72` | 2026-04-28 | commit | fix(95): WR-05 add stub-mode note and remove duplicate SSE wait in status bar test |
| 769 | `a22cc683` | 2026-04-28 | commit | docs(95): add code review fix report |
| 770 | `70a33c1a` | 2026-04-28 | commit | fix(95): deep review fixes - afterEach resilience, type safety, test isolation |
| 771 | `f8f805e5` | 2026-04-28 | commit | docs(96): capture phase context .planning/phases/96-rate-limiting-tooling/96-CONTEXT.md .planning/phases/96-rate-limiting-tooling/96-DISCUSSION-LOG.md |
| 772 | `cc2cd15d` | 2026-04-28 | commit | docs(state): record phase 96 context session .planning/STATE.md |
| 773 | `befeece9` | 2026-04-28 | commit | docs(96): research phase domain .planning/phases/96-rate-limiting-tooling/96-RESEARCH.md |
| 774 | `5a117f7e` | 2026-04-28 | commit | docs(96): create phase plan - 3 plans in 2 waves |
| 775 | `73c633ad` | 2026-04-28 | commit | docs(96): create phase plan - 3 plans in 2 waves |
| 776 | `12d4d52b` | 2026-04-28 | commit | test(96-01): add failing tests for rate_limit sliding-window decorator |
| 777 | `8856035a` | 2026-04-28 | commit | feat(96-01): implement sliding-window rate_limit decorator factory |
| 778 | `bf0fc8b0` | 2026-04-28 | commit | feat(96-02): tighten js-nosql-injection-where and js-xss-eval-user-input Semgrep rules |
| 779 | `3d936bc9` | 2026-04-28 | commit | docs(96-01): complete rate_limit decorator plan - 14 tests, 1151 passing |
| 780 | `20b02e12` | 2026-04-28 | commit | docs(96-02): complete tighten-semgrep-rules plan |
| 781 | `df8c15b4` | 2026-04-28 | merge | chore: merge executor worktree (worktree-agent-a713287b6cef261d3) |
| 782 | `7c7cf264` | 2026-04-28 | merge | chore: merge executor worktree (worktree-agent-a1c82f5082c34a56c) |
| 783 | `bf7d8c38` | 2026-04-28 | commit | docs(phase-96): update tracking after wave 1 .planning/ROADMAP.md .planning/STATE.md |
| 784 | `e8e8fede` | 2026-04-28 | commit | feat(96-03): apply rate_limit decorator to ConfigHandler and StatusHandler |
| 785 | `6e66d6a1` | 2026-04-28 | commit | feat(96-03): refactor ControllerHandler and update all handler rate limit tests |
| 786 | `2f3cc6a0` | 2026-04-28 | commit | docs(96-03): complete apply-rate-limit-to-handlers plan |
| 787 | `b46b130c` | 2026-04-28 | merge | chore: merge executor worktree (worktree-agent-ab9feb3ecad556ac6) |
| 788 | `5c674341` | 2026-04-28 | commit | docs(phase-96): update tracking after wave 2 .planning/ROADMAP.md .planning/STATE.md |
| 789 | `69613599` | 2026-04-28 | commit | fix(96): resolve code review findings - mock time in flaky test, add anonymous function Semgrep exclusions src/python/tests/unittests/test_web/test_handler/test_controller_handler.py shield-claude-skill/configs/semgrep-rules/javascript.yaml |
| 790 | `06c6f135` | 2026-04-28 | commit | docs(96): mark review clean after fixes applied .planning/phases/96-rate-limiting-tooling/96-REVIEW.md |
| 791 | `a912015a` | 2026-04-28 | commit | feat(96): harden rate limiter and modernize handler code |
| 792 | `cc87e7bf` | 2026-04-28 | commit | chore: archive v1.2.0 milestone files |
| 793 | `222a02fa` | 2026-04-28 | commit | chore: remove REQUIREMENTS.md for v1.2.0 milestone |
| 794 | `71ab06ee` | 2026-04-28 | commit | chore: archive v1.2.0 phase directories to milestones/ |
| 795 | `60337842` | 2026-04-29 | commit | chore(deps-dev): bump pyinstaller from 6.19.0 to 6.20.0 in /src/python |
| 796 | `1eb12916` | 2026-04-29 | commit | chore(deps-dev): bump ruff from 0.15.11 to 0.15.12 in /src/python |
| 797 | `92b7f1b7` | 2026-04-29 | commit | chore(deps): bump the npm_and_yarn group in /src/angular with 14 updates |
| 798 | `1691477f` | 2026-04-29 | commit | chore: init gsd |
| 799 | `2eb255d1` | 2026-04-29 | merge | Merge pull request #24 from thejuran/dependabot/npm_and_yarn/src/angular/npm_and_yarn-52e5b63951 |
| 800 | `0ad308d5` | 2026-04-29 | merge | Merge pull request #22 from thejuran/dependabot/pip/src/python/pyinstaller-6.20.0 |
| 801 | `9a41ad63` | 2026-04-29 | merge | Merge pull request #23 from thejuran/dependabot/pip/src/python/ruff-0.15.12 |
| 802 | `1463dd9c` | 2026-04-29 | merge | Merge branch 'main' of https://github.com/thejuran/seedsyncarr |
| 803 | `78fbc1cb` | 2026-04-29 | commit | chore(deps-dev): bump postcss from 8.5.8 to 8.5.12 in /src/angular (#25) |
| 804 | `a28a127a` | 2026-04-29 | commit | fix(ci): resolve Python test build and lint failures |
| 805 | `838dbf09` | 2026-04-29 | commit | fix(ci): force default buildx builder for compose steps |
| 806 | `d2ca16ed` | 2026-04-29 | commit | fix(ci): add missing remote_password and rate_limit to test_controller fixture |
| 807 | `5f97b8fd` | 2026-04-29 | commit | fix(ci): chown scanfs_remote dir to testgroup for SSH user write access |
| 808 | `e8efb0b6` | 2026-04-29 | commit | fix(ci): chown remote dir tree to testgroup for SSH user write access |
| 809 | `c2b9cb29` | 2026-04-29 | commit | fix(ci): scope compose build to tests and configure services |
| 810 | `afc0dc9d` | 2026-04-29 | commit | fix(ci): set remote_password in e2e setup to satisfy incomplete-config check |
| 811 | `60baa92f` | 2026-04-29 | commit | fix(ci): repair e2e ssh key mount and update actions |
| 812 | `fcc2332c` | 2026-05-05 | commit | fix(scanner): prevent failed scans from wiping model and detect dead scanner processes |
| 813 | `38a9815e` | 2026-05-05 | commit | fix(scanner): harden dead-process detection and cleanup |
| 814 | `bc1dd883` | 2026-05-05 | commit | docs: update v1.2.2 release metadata |
| 815 | `f196be8e` | 2026-05-05 | commit | chore(deps-dev): bump ip-address from 10.1.0 to 10.2.0 (#27) |
| 816 | `6bba8062` | 2026-05-05 | commit | ci: add release metadata guard |
| 817 | `44cd5f68` | 2026-05-05 | commit | fix(deps): override angular ip-address advisory |
| 818 | `c415ac37` | 2026-05-06 | commit | chore(deps-dev): bump puppeteer from 24.42.0 to 24.43.0 |
| 819 | `41709fbe` | 2026-05-06 | commit | chore(deps): bump pytz from 2026.1.post1 to 2026.2 |
| 820 | `b646ea5d` | 2026-05-06 | commit | chore(deps): bump the npm_and_yarn group in /src/angular |
| 821 | `db8205b2` | 2026-05-08 | commit | chore(deps-dev): bump hono from 4.12.14 to 4.12.18 in /src/angular |
| 822 | `6ce9e823` | 2026-05-08 | merge | Merge pull request #31 from thejuran/dependabot/npm_and_yarn/src/angular/hono-4.12.18 |
| 823 | `1ddad6be` | 2026-05-08 | commit | chore: clean up Angular Sass deprecation warnings |
| 824 | `92bdf802` | 2026-05-09 | commit | chore(deps): bump fast-uri from 3.1.0 to 3.1.2 in /src/angular (#32) |
| 825 | `bae4e448` | 2026-05-11 | commit | chore(deps): bump urllib3 from 2.6.3 to 2.7.0 in /src/python (#33) |
| 826 | `ab57acf6` | 2026-05-11 | commit | docs: prepare v1.2.3 release |
| 827 | `b5a4f3a8` | 2026-05-13 | commit | chore(deps-dev): bump puppeteer in the root group (#34) |
| 828 | `fb1a24b5` | 2026-05-13 | commit | chore(deps): bump requests from 2.33.1 to 2.34.0 in /src/python (#35) |
| 829 | `8135888c` | 2026-05-13 | commit | chore(deps): bump the npm_and_yarn group in /src/angular with 20 updates (#36) |
| 830 | `01cf2c39` | 2026-05-19 | commit | chore(deps): bump the pip group across 1 directory with 2 updates (#37) |
| 831 | `200254e1` | 2026-05-20 | commit | chore(deps): bump requests from 2.34.0 to 2.34.2 in /src/python (#39) |
| 832 | `ca0e1e71` | 2026-05-20 | commit | chore(deps): bump patool from 4.0.4 to 4.0.5 in /src/python (#40) |
| 833 | `4269a347` | 2026-05-20 | commit | chore(deps-dev): bump ruff from 0.15.12 to 0.15.13 in /src/python (#41) |
| 834 | `d021352c` | 2026-05-20 | commit | chore(deps-dev): bump puppeteer from 24.43.1 to 25.0.4 in the root group (#38) |
| 835 | `d04334d6` | 2026-05-20 | commit | chore(deps): bump the npm_and_yarn group in /src/angular with 16 updates (#42) |
| 836 | `ef075318` | 2026-05-20 | commit | docs: prepare v1.2.4 release |
| 837 | `40040650` | 2026-05-24 | commit | chore(deps): bump qs and body-parser in /src/angular (#43) |
| 838 | `46259f6a` | 2026-05-28 | commit | chore(deps): bump tmp from 0.2.5 to 0.2.7 in /src/angular (#48) |
| 839 | `e8797386` | 2026-05-28 | commit | chore(deps-dev): bump puppeteer from 25.0.4 to 25.1.0 in the root group (#44) |
| 840 | `8eb81113` | 2026-05-28 | commit | chore(deps-dev): bump testfixtures from 11.0.0 to 12.0.0 in /src/python (#45) |
| 841 | `00541e7f` | 2026-05-28 | commit | chore(deps): bump the npm_and_yarn group in /src/angular with 17 updates (#47) |
| 842 | `9a3475f3` | 2026-05-28 | commit | chore(deps-dev): bump ruff from 0.15.13 to 0.15.14 in /src/python (#46) |
| 843 | `de86766d` | 2026-05-25 | commit | docs: map existing codebase |
| 844 | `8f099cc4` | 2026-05-25 | commit | chore: remove stale HANDOFF.json from phase 82 |
| 845 | `22616f95` | 2026-05-28 | commit | docs(260528-khw): pre-dispatch plan for dependabot triage |
| 846 | `f4d49448` | 2026-05-28 | commit | docs(quick-260528-khw): triage and merge dependabot PRs, resolve open security alert |
| 847 | `7ae4a75c` | 2026-05-28 | commit | docs: prepare v1.2.5 release |
| 848 | `725d499b` | 2026-05-28 | commit | docs: brainstorm spec for v1.3.0 test coverage gaps milestone |
| 849 | `d357f666` | 2026-05-28 | commit | docs: start milestone v1.3.0 Test Coverage Gaps |
| 850 | `afd97b71` | 2026-05-28 | commit | docs: define milestone v1.3.0 requirements |
| 851 | `f3858e74` | 2026-05-28 | commit | docs: create milestone v1.3.0 roadmap (4 phases) |
| 852 | `01ae748e` | 2026-05-28 | commit | docs(97): capture phase context |
| 853 | `8824fab4` | 2026-05-28 | commit | docs(state): record phase 97 context session |
| 854 | `66e4e818` | 2026-05-28 | commit | docs(97): create phase plan |
| 855 | `e5f42ae8` | 2026-05-28 | commit | docs(97): create phase plan |
| 856 | `0f4c79e2` | 2026-05-28 | commit | docs(97): revise plans per codex adversarial review (real-parser LFTP test, coverage-emitting baseline cmd, SSRF allow-case + only-if-red D-01, non-hollow shutdown test, SHA-gated baseline verify) |
| 857 | `2177e44a` | 2026-05-28 | commit | docs(97-01): capture v1.3.0 coverage baseline (Python host/provisional + Angular) |
| 858 | `27381892` | 2026-05-28 | commit | docs(97-01): add SUMMARY for v1.3.0 coverage baseline plan |
| 859 | `cbcec2e8` | 2026-05-28 | merge | chore: merge executor worktree (97-01 baseline) |
| 860 | `683f56a5` | 2026-05-28 | commit | docs(phase-97): update tracking after wave 1 (baseline 85.19% Python / Angular recorded) |
| 861 | `fbecf348` | 2026-05-28 | commit | test(97-03): add TestValidateUrl SSRF coverage for _validate_url |
| 862 | `2197ac96` | 2026-05-28 | commit | test(97-02): cover handler-raise capture + propagate_exception re-raise/no-op |
| 863 | `385e4cc9` | 2026-05-28 | commit | docs(97-03): complete SSRF _validate_url coverage plan |
| 864 | `ba64f376` | 2026-05-28 | commit | test(97-04): cover LFTP status() parser-error counter (real parser + controller) |
| 865 | `fcdda458` | 2026-05-28 | commit | test(97-02): cover empty-queue resilience + non-hollow clean shutdown |
| 866 | `d0329c46` | 2026-05-28 | commit | test(97-04): cover LFTP status() counter reset on subsequent success |
| 867 | `a2e824d3` | 2026-05-28 | commit | docs(97-02): complete MultiprocessingLogger listener-shutdown coverage plan |
| 868 | `584c42f6` | 2026-05-28 | commit | docs(97-04): complete LFTP parser-error counter coverage plan |
| 869 | `b0b17718` | 2026-05-28 | merge | chore: merge executor worktree (97-02 MP-logger coverage) |
| 870 | `547bb939` | 2026-05-28 | merge | chore: merge executor worktree (97-03 SSRF coverage) |
| 871 | `54579f71` | 2026-05-28 | merge | chore: merge executor worktree (97-04 LFTP coverage) |
| 872 | `470b8d44` | 2026-05-28 | commit | docs(phase-97): update tracking after wave 2 (97-02/03/04 complete; mp-logger spawn analog deferred to v1.4.0) |
| 873 | `9f090bb5` | 2026-05-28 | commit | docs(97): add code review report |
| 874 | `d5e156e4` | 2026-05-28 | commit | docs(97): add phase verification report (passed 5/5) |
| 875 | `5bda93ed` | 2026-05-28 | commit | fix(review-pass-1): replace fixed sleep with listener-shutdown poll in handler-raise tests |
| 876 | `c8de7809` | 2026-05-29 | commit | fix(review-pass-2): enqueue test records directly onto listener queue |
| 877 | `fa6b6877` | 2026-05-29 | commit | chore: gitignore .turingmind/ code-review working state |
| 878 | `027d7b49` | 2026-05-29 | commit | docs(98): capture phase context |
| 879 | `402518bf` | 2026-05-29 | commit | docs(state): record phase 98 context session |
| 880 | `ca6b0188` | 2026-05-29 | commit | docs(98): research phase - escapeHtml XSS coverage planning |
| 881 | `533bd84a` | 2026-05-29 | commit | docs(98): create phase plan |
| 882 | `f246df1b` | 2026-05-29 | commit | docs(98): add VALIDATION.md and mark research Open Questions resolved |
| 883 | `7147dca6` | 2026-05-29 | commit | docs(98): create phase plan |
| 884 | `fdc188c4` | 2026-05-29 | commit | docs(98): incorporate codex adversarial finding into plan |
| 885 | `1c83016c` | 2026-05-29 | commit | test(98-01): add XSS describe scaffold, escape/hasOnAttribute helpers, D-04 unit tests |
| 886 | `987c4cfa` | 2026-05-29 | commit | test(98-01): add D-03/D-05 end-to-end DOM XSS tests for all six inputs; supersede partial XSS tests |
| 887 | `5db6d61e` | 2026-05-29 | commit | test(98-01): add D-02 skipCount-exemption documenting test and runtime-boundary probe |
| 888 | `6a047ee2` | 2026-05-29 | commit | docs(98-01): complete XSS / escapeHtml coverage plan - 12 new tests, COVMED-04 closed |
| 889 | `4d4192b4` | 2026-05-29 | merge | chore: merge executor worktree (worktree-agent-a93c3cdb06cadb99a) |
| 890 | `9d3069cc` | 2026-05-29 | commit | docs(phase-98): update tracking after wave 1 |
| 891 | `2f0513d7` | 2026-05-29 | commit | docs(98): add code review report |
| 892 | `da731026` | 2026-05-29 | commit | docs(phase-98): complete phase execution |
| 893 | `23797393` | 2026-05-29 | commit | docs(98): commit pattern map and sync milestone checklist (97+98 complete) |
| 894 | `a58ea46c` | 2026-05-29 | commit | refactor(98): extract hasJavascriptUrl test helper (turingmind M1) |
| 895 | `a17af76a` | 2026-05-29 | commit | docs(99): capture phase context |
| 896 | `6353fb00` | 2026-05-29 | commit | docs(state): record phase 99 context session |
| 897 | `38034c8b` | 2026-05-29 | commit | docs(99): create phase plan (99-01 auto-delete toggle, 99-02 BoundedOrderedSet eviction) |
| 898 | `a8de7978` | 2026-05-29 | commit | docs(99): create phase plan |
| 899 | `3849a36d` | 2026-05-29 | commit | docs(99): revise plans per codex adversarial review (F1 sync-gate, F2 mandatory log asserts, F3 positive control, F5 spec-refinement note) |
| 900 | `b0e1c093` | 2026-05-29 | commit | docs(99): fail-closed Event gate in 99-01 (codex F1 follow-up - wrapper aborts re-read on wait timeout instead of proceeding past unset gate) |
| 901 | `2c74a73f` | 2026-05-29 | commit | docs(99): reset _gate_timed_out per-run in 99-01 (codex non-blocking observation - avoid stale True leaking across test methods) |
| 902 | `a4b33d94` | 2026-05-29 | commit | test(99-02): add test_eviction_order_after_touch regression test (COVLOW-02) |
| 903 | `50917b10` | 2026-05-29 | commit | docs(99-02): complete plan - COVLOW-02 BoundedOrderedSet eviction-after-touch regression test |
| 904 | `32d9c269` | 2026-05-29 | commit | test(99-01): add TestAutoDeleteToggleDuringTimer with Event-gated live-Timer tests |
| 905 | `7f0c3520` | 2026-05-29 | commit | docs(99-01): complete TestAutoDeleteToggleDuringTimer plan |
| 906 | `2b51e64d` | 2026-05-29 | merge | merge(99-01): TestAutoDeleteToggleDuringTimer live-Timer toggle tests (COVLOW-01) |
| 907 | `deb4cdf4` | 2026-05-29 | merge | merge(99-02): BoundedOrderedSet eviction-after-touch regression test (COVLOW-02) |
| 908 | `5a04bf02` | 2026-05-29 | commit | docs(99): record phase execution progress (2/2 plans complete) |
| 909 | `dd204d0c` | 2026-05-29 | commit | docs(99): phase verification passed (6/6 must-haves, COVLOW-01/02 closed) |
| 910 | `82a000d8` | 2026-05-29 | commit | docs(99): mark phase complete - COVLOW-01/02 closed, verified, deep-review clean |
| 911 | `a538e1c6` | 2026-05-29 | commit | docs(100): capture phase context |
| 912 | `dffb2c85` | 2026-05-29 | commit | docs(state): record phase 100 context session |
| 913 | `bdc33cab` | 2026-05-29 | commit | docs(100): research phase domain |
| 914 | `3fcc0ef7` | 2026-05-29 | commit | docs(phase-100): add validation strategy |
| 915 | `b6c6691a` | 2026-05-29 | commit | docs(100): create phase plan - 3 plans (COVLOW-03/04 regressions + RATCHET-02 CI ratchet) |
| 916 | `27672aaa` | 2026-05-29 | commit | docs(100): cite CONTEXT decision IDs in plans (coverage gate) |
| 917 | `76a4898f` | 2026-05-29 | commit | docs(100): record planning completion + pattern map |
| 918 | `df453ef3` | 2026-05-29 | commit | fix(100): correct 100-03 coverage cmd + container-inclusive re-measure + monotonic guard (codex) |
| 919 | `6e5e4713` | 2026-05-29 | commit | fix(100): container-inclusive coverage cmd (COVERAGE_FILE redirect) + purge stale host-only guidance (codex r2) |
| 920 | `f02c7b21` | 2026-05-29 | commit | docs(100): demote stale host coverage refs to provisional in RESEARCH/VALIDATION (codex r3) |
| 921 | `1f4170ef` | 2026-05-29 | commit | test(100-01): add SSE heartbeat-vs-timeout race regression tests (COVLOW-03) |
| 922 | `8286956b` | 2026-05-29 | commit | test(100-02): token-rotation regression test via _resetAuthInterceptorCache seam |
| 923 | `9fc07650` | 2026-05-29 | commit | docs(100-01): complete heartbeat-vs-timeout race plan - COVLOW-03 closed |
| 924 | `85bc6435` | 2026-05-29 | commit | docs(100-02): complete token-rotation regression test plan (COVLOW-04) |
| 925 | `201d5123` | 2026-05-29 | merge | chore: merge executor worktree (worktree-agent-acf230f8013ffe728) |
| 926 | `777d6625` | 2026-05-29 | merge | chore: merge executor worktree (worktree-agent-a65d79384825d1186) |
| 927 | `76882524` | 2026-05-29 | commit | docs(phase-100): update tracking after wave 1 |
| 928 | `dfbab2ed` | 2026-05-29 | commit | feat(100-03): ratchet CI coverage thresholds (RATCHET-02) |
| 929 | `0ff788b5` | 2026-05-29 | commit | docs(100-03): record v1.3.0 coverage ratchet before/after (D-07/D-08) |
| 930 | `12df97ed` | 2026-05-29 | commit | docs(100-03): complete plan - SUMMARY.md, STATE.md progress, RATCHET-02 closed |
| 931 | `c30a4627` | 2026-05-31 | commit | chore: archive v1.3.0 milestone files |
| 932 | `a3039e69` | 2026-05-31 | commit | chore: remove REQUIREMENTS.md for v1.3.0 milestone |
| 933 | `1124a9e9` | 2026-05-31 | commit | chore: gitignore GSD auto-checkpoint HANDOFF.json files |
| 934 | `ed5bf0df` | 2026-05-31 | commit | chore: archive completed v1.3.0 slice-1 phase dirs (97-100) |
| 935 | `58cdf9af` | 2026-05-31 | commit | docs: start v1.3.0 slice 2 of 4 - Known Bugs + Security (phases 101-103) |
| 936 | `2b24e206` | 2026-05-31 | commit | docs(101): capture phase context |
| 937 | `d915f9e9` | 2026-05-31 | commit | docs(state): record phase 101 context session |
| 938 | `c2261a62` | 2026-05-31 | commit | docs(101): research phase webhook and log-injection security cluster |
| 939 | `2d7423b4` | 2026-05-31 | commit | docs(101): correct SEC-01/SEC-02 decisions post-research |
| 940 | `a6682895` | 2026-05-31 | commit | docs(phase-101): add validation strategy |
| 941 | `a6b1cc80` | 2026-05-31 | commit | docs(101): create phase plan (4 plans, 2 waves - webhook + log-injection security cluster) |
| 942 | `68909c08` | 2026-05-31 | commit | docs(101): cite CONTEXT decisions in plan must_haves |
| 943 | `f6cb37a7` | 2026-05-31 | commit | docs(101): add pattern map |
| 944 | `dec2b7c1` | 2026-05-31 | commit | docs(101): fold adversarial blocker fixes into plans 01-02 |
| 945 | `470ec263` | 2026-05-31 | commit | docs(101): fold adversarial evidence into plan 03 (SEC-02) |
| 946 | `b3f826c9` | 2026-05-31 | commit | docs(101): adversarial rewrite 1 - fix verify-gate blocker + fold mediums |
| 947 | `32b96064` | 2026-05-31 | commit | docs(101): adversarial-review revision - 5 plans, fail-closed guard ordering, expanded SEC-01 taint set |
| 948 | `40c6595d` | 2026-05-31 | commit | docs(101): adversarial rewrite 2 - close Plan 05 verify-gate blocker + finish doc supersession |
| 949 | `611ffab6` | 2026-05-31 | commit | docs(101): adversarial round-2 - add lftp SEC-01 plan (101-06) + per-site gate hardening |
| 950 | `41cd88f5` | 2026-05-31 | commit | docs(101): fold SEC-01 round-3 Option-C sites into plans 04/06 |
| 951 | `9579cd23` | 2026-05-31 | commit | test(101-01): add failing TestSanitizeLogValue unit tests (RED) |
| 952 | `afc4922b` | 2026-05-31 | commit | feat(101-01): implement sanitize_log_value helper and re-export (GREEN) |
| 953 | `a98b584f` | 2026-05-31 | commit | test(101-03): add failing tests for always-blank secret fields (SEC-02 RED) |
| 954 | `55527e7c` | 2026-05-31 | commit | docs(101-01): complete sanitize_log_value plan summary |
| 955 | `5704537c` | 2026-05-31 | commit | feat(101-03): always-blank webhook_secret and api_token in config GET response (SEC-02) |
| 956 | `7ee23e8e` | 2026-05-31 | commit | feat(101-02): declare webhook_require_secret flag + first-run default + back-compat |
| 957 | `5633d8e6` | 2026-05-31 | commit | test(101-03): add handler-level integration test for blank secret fields (SEC-02) |
| 958 | `90440d0f` | 2026-05-31 | commit | docs(101-03): complete always-blank config secret fields plan summary (SEC-02) |
| 959 | `6766e096` | 2026-05-31 | commit | feat(101-02): fail-closed 503 guard outside rate_limit + rate-limit both webhook routes |
| 960 | `340567ee` | 2026-05-31 | commit | feat(101-02): extend startup warning for webhook_require_secret-without-secret (D-07) |
| 961 | `f8188d7b` | 2026-05-31 | commit | docs(101-02): complete webhook fail-closed + rate-limit plan summary |
| 962 | `a6d16068` | 2026-05-31 | merge | chore: merge executor worktree (worktree-agent-a57305ec1b2399b59) |
| 963 | `84fbfd76` | 2026-05-31 | merge | chore: merge executor worktree (worktree-agent-aee69183b1ed9d091) |
| 964 | `efeb1c47` | 2026-05-31 | merge | chore: merge executor worktree (worktree-agent-a5b94bf9af1331dee) |
| 965 | `7b815dd9` | 2026-05-31 | commit | docs(phase-101): update tracking after wave 1 |
| 966 | `f13fbbfa` | 2026-05-31 | commit | chore: normalize config.json trailing newline (SDK rewrite during execute) |
| 967 | `2ab422c0` | 2026-05-31 | commit | test(101-04): add failing CWE-117 sanitization tests for webhook_manager (RED) |
| 968 | `87b45ea5` | 2026-05-31 | commit | feat(101-04): replace inline newline-escape copies with sanitize_log_value in webhook_manager (GREEN) |
| 969 | `87a7171b` | 2026-05-31 | commit | test(101-06): add failing tests for lftp.py kill/run_command log sanitization (RED) |
| 970 | `1ceef53e` | 2026-05-31 | commit | test(101-04): add failing CWE-117 sanitization tests for controller webhook/command sites (RED) |
| 971 | `f507a7cc` | 2026-05-31 | commit | feat(101-06): sanitize lftp.py kill/run_command log sites via sanitize_log_value (GREEN) |
| 972 | `18c736e7` | 2026-05-31 | commit | test(101-06): add failing tests for job_status_parser.py parse-error log sanitization (RED) |
| 973 | `e85f3fbd` | 2026-05-31 | commit | feat(101-06): sanitize job_status_parser.py parse-error log sites via sanitize_log_value (GREEN) |
| 974 | `2fdc96e4` | 2026-05-31 | commit | test(101-06): add failing tests for remote_scanner.py JSON-decode-error log sanitization (RED) |
| 975 | `75478ec8` | 2026-05-31 | commit | feat(101-04): route controller webhook/command log sites through sanitize_log_value (GREEN) |
| 976 | `0c5108b8` | 2026-05-31 | commit | feat(101-06): sanitize remote_scanner.py JSON-decode-error log via sanitize_log_value (GREEN) |
| 977 | `75d11e20` | 2026-05-31 | commit | docs(101-04): complete webhook/command log-injection cluster plan summary |
| 978 | `965cf7e8` | 2026-05-31 | commit | docs(101-06): complete lftp+remote-scanner log-injection sanitization plan summary |
| 979 | `08ce3d83` | 2026-05-31 | merge | chore: merge executor worktree (worktree-agent-afc9886b62a17f06a) |
| 980 | `1cf406f4` | 2026-05-31 | merge | chore: merge executor worktree (worktree-agent-a5d74594423ffff46) |
| 981 | `35f9f2ba` | 2026-05-31 | commit | docs(phase-101): update tracking after wave 2 |
| 982 | `3c1b6310` | 2026-05-31 | commit | test(101-05): add failing CWE-117 sanitization tests for auto-delete timer + exit-cancel log sites (RED) |
| 983 | `45ff4bd3` | 2026-05-31 | commit | feat(101-05): sanitize auto-delete timer + exit-cancel log sites in controller.py (GREEN) |
| 984 | `70354771` | 2026-05-31 | commit | test(101-05): add failing CWE-117 sanitization tests for model add/remove/update debug log sites (RED) |
| 985 | `f64a8742` | 2026-05-31 | commit | feat(101-05): add sanitize_log_value import and wrap add/remove/update debug logs in model/model.py (GREEN) |
| 986 | `ac857186` | 2026-05-31 | commit | docs(101-05): complete auto-delete timer + model log-injection sanitization plan summary |
| 987 | `b645fa18` | 2026-05-31 | merge | chore: merge executor worktree (worktree-agent-a395527b3c5fea5d2) |
| 988 | `9f5d7470` | 2026-05-31 | commit | docs(phase-101): update tracking after wave 3 (all 6 plans complete) |
| 989 | `4e0071d4` | 2026-05-31 | commit | docs(101): add code review report |
| 990 | `051109c0` | 2026-05-31 | commit | docs(101): add phase verification report (passed) |
| 991 | `2f1818c8` | 2026-05-31 | commit | docs(phase-101): mark phase complete in tracking (6/6 plans, verified passed) |
| 992 | `c9836577` | 2026-05-31 | commit | docs(102): capture phase context |
| 993 | `294e7a1e` | 2026-05-31 | commit | docs(state): record phase 102 context session |
| 994 | `2222d416` | 2026-05-31 | commit | docs(102): create phase plan (BUG-03 shutdown guard, INFRA-01 spawn-safe tests) |
| 995 | `469d9c72` | 2026-05-31 | commit | docs(state): record phase 102 planned |
| 996 | `19256a5d` | 2026-05-31 | commit | docs(102): adversarial rewrite 2 - lock-serialized BUG-03 guard; defer INFRA-01 |
| 997 | `5cb2e475` | 2026-05-31 | commit | docs(state): record phase 102 re-planned (rewrite 2) |
| 998 | `51225c41` | 2026-05-31 | commit | test(102-01): add RED shutdown-guard regression tests (BUG-03 criterion #2) |
| 999 | `537804df` | 2026-05-31 | commit | feat(102-01): BUG-03 criterion #2 - dedicated shutdown Event + serialized guards |
| 1000 | `a611d357` | 2026-05-31 | commit | docs(102-01): complete BUG-03 criterion #2 plan summary |
| 1001 | `64c4c3a7` | 2026-05-31 | merge | chore: merge executor worktree (102-01 BUG-03 shutdown guard) |
| 1002 | `09fe1187` | 2026-05-31 | commit | fix(102-01): init __shutdown_event in manual-construction test helper |
| 1003 | `6d31f6b3` | 2026-05-31 | commit | docs(102): execute complete - BUG-03 shutdown guard landed |
| 1004 | `57ca66a2` | 2026-05-31 | commit | test(102-02): RED - assert imported_children.pop holds __model_lock at final-commit |
| 1005 | `738e4397` | 2026-05-31 | commit | fix(102-02): nest __auto_delete_lock inside __model_lock for imported_children.pop |
| 1006 | `9054eea7` | 2026-05-31 | commit | docs(102-02): add code-review fix summary for model-lock serialization |
| 1007 | `aa2c3b85` | 2026-05-31 | merge | chore: merge executor worktree (102-02 model-lock serialization fix) |
| 1008 | `df9f2742` | 2026-05-31 | commit | docs(state): record phase 102 deep-review complete (race fix landed, suite green) |
| 1009 | `0b4c2c41` | 2026-05-31 | commit | docs(103): capture phase context |
| 1010 | `d91665d6` | 2026-05-31 | commit | docs(state): record phase 103 context session |
| 1011 | `54926653` | 2026-05-31 | commit | docs(103): research phase angular defects (BUG-01 + BUG-04) |
| 1012 | `d86ed52d` | 2026-05-31 | commit | docs(103): add research and validation strategy |
| 1013 | `a312aa2c` | 2026-05-31 | commit | docs(103): create phase plan (BUG-01 Renderer2 modal, BUG-04 SSE teardown) |
| 1014 | `a500d05a` | 2026-05-31 | commit | docs(103): add pattern map |
| 1015 | `c7447beb` | 2026-05-31 | commit | docs(103): revise plans - fix BUG-04 RED discriminator (duplicate-disconnect side-effect), BUG-01 RED-cause prose (inverted D-05 probe), RESEARCH supersession note |
| 1016 | `64e8309d` | 2026-05-31 | commit | docs(state): begin phase 103 execution |
| 1017 | `61125e15` | 2026-05-31 | commit | test(103-01): RED - update spec to post-BUG-01 structural contract |
| 1018 | `03b073d6` | 2026-05-31 | commit | feat(103-01): GREEN - replace innerHTML sink with Renderer2 structural construction |
| 1019 | `dbfab18d` | 2026-05-31 | commit | docs(103-01): complete BUG-01 innerHTML elimination plan |
| 1020 | `3751941f` | 2026-05-31 | commit | test(103-02): RED - BUG-04 same-tick reconnect collision regression test |
| 1021 | `74beb81b` | 2026-05-31 | commit | fix(103-02): BUG-04 unsubscribe _currentSubscription at top of reconnectDueToTimeout() |
| 1022 | `20e41c4b` | 2026-05-31 | commit | docs(103-02): complete BUG-04 SSE same-tick subscription teardown plan |
| 1023 | `3b7a9ae8` | 2026-05-31 | merge | chore: merge executor worktree (worktree-agent-adb430e4c2306c221) |
| 1024 | `38708696` | 2026-05-31 | merge | chore: merge executor worktree (worktree-agent-abb9ed90707a8999d) |
| 1025 | `50a83402` | 2026-05-31 | commit | docs(phase-103): update tracking after wave 1 |
| 1026 | `87f922cc` | 2026-05-31 | commit | fix(review-pass-1): replace stale line-number comment refs with symbolic references |
| 1027 | `9e29510c` | 2026-05-31 | commit | fix(review-pass-1): buildModalContent takes a typed options object (transposition-safe) |
| 1028 | `9453d71d` | 2026-05-31 | commit | fix(roadmap): restore roadmap clobbered by annotate-dependencies, mark phase 103 complete |
| 1029 | `376c4aaa` | 2026-05-31 | commit | docs(state): reconcile phase 103 tracking |
| 1030 | `a4ac4b84` | 2026-05-31 | commit | docs(audit): v1.3.0 slice 2 milestone audit (gaps_found = process artifacts; all reqs functionally satisfied) |
| 1031 | `c6db066b` | 2026-05-31 | commit | chore: close v1.3.0 slice 2 (Known Bugs + Security) - lightweight slice close, no tag |
| 1032 | `15cc4e8d` | 2026-05-31 | commit | docs: start milestone v1.3.0-s3 Frontend Deps + Dead Code |
| 1033 | `a6a4ed4c` | 2026-05-31 | commit | docs: define milestone v1.3.0-s3 requirements (DEPS-01a/b/c, DEPS-02) |
| 1034 | `07636388` | 2026-05-31 | commit | docs: create milestone v1.3.0-s3 roadmap (3 phases) |
| 1035 | `a492f6cd` | 2026-05-31 | commit | docs(104): capture phase context |
| 1036 | `a53e8614` | 2026-05-31 | commit | docs(state): record phase 104 context session |
| 1037 | `48a7010b` | 2026-05-31 | commit | docs(state): record phase 104 context session |
| 1038 | `b2381ad2` | 2026-06-01 | commit | docs(104): research phase domain |
| 1039 | `e344bccc` | 2026-06-01 | commit | docs(104): create phase plan |
| 1040 | `e2603d66` | 2026-06-01 | commit | docs(104): tag D-01/D-04 in plan truths; mark research questions resolved |
| 1041 | `fd0d0d86` | 2026-06-01 | commit | docs(104): revise plans per codex adversarial review |
| 1042 | `dde9d95c` | 2026-06-01 | commit | docs(104-01): capture pre-removal bundle baseline and D-01 BEFORE gate |
| 1043 | `9a463752` | 2026-06-01 | commit | chore(104-01): remove jquery (DEPS-01a) - atomic commit 1 of 2 |
| 1044 | `1a42cdba` | 2026-06-01 | commit | chore(104-01): remove css-element-queries (DEPS-01c) - atomic commit 2 of 2 |
| 1045 | `0d338f47` | 2026-06-01 | commit | docs(104-01): complete plan 01 - jquery + css-element-queries removal summary |
| 1046 | `01eb3f37` | 2026-06-01 | commit | feat(104-02): AFTER production build, bundle delta, dist library-code grep |
| 1047 | `b0993271` | 2026-06-01 | commit | feat(104-02): Karma coverage floors verified - 611/611 pass, all floors hold |
| 1048 | `a08c3325` | 2026-06-01 | commit | docs(104-02): complete plan 02 - AFTER verification summary (D-01 AFTER + D-02 + Karma floors + smoke test APPROVED) |
| 1049 | `6dfe6ba3` | 2026-06-01 | commit | docs(104-02): mark Phase 104 complete in STATE.md + ROADMAP.md (2/2 plans done) |
| 1050 | `8e63f1cd` | 2026-06-01 | commit | docs(phase-104): verification passed - DEPS-01a + DEPS-01c fully verified |
| 1051 | `e2340dcb` | 2026-06-01 | commit | docs(105): capture phase context |
| 1052 | `781baf24` | 2026-06-01 | commit | docs(state): record phase 105 context session |
| 1053 | `ff81495e` | 2026-06-01 | commit | docs(phase-105): research FA->Phosphor migration - verified mapping table + full surface map |
| 1054 | `4c8b2aa0` | 2026-06-01 | commit | docs(phase-105): add validation strategy |
| 1055 | `840a55db` | 2026-06-01 | commit | docs(phase-105): create phase plan - FA->Phosphor migration (4 plans, 3 waves) |
| 1056 | `4111101c` | 2026-06-01 | commit | docs(105): create phase plan |
| 1057 | `e2956561` | 2026-06-01 | commit | docs(105): add pattern map |
| 1058 | `a0bd1064` | 2026-06-01 | commit | docs(105): revise plans per codex adversarial review (broaden residual-fa gate, Q4 shared signoff source, fix fa-server citation) |
| 1059 | `47155853` | 2026-06-01 | commit | docs(105-01): author complete 39-class fa->ph mapping table + capture D-07 BEFORE bundle baseline |
| 1060 | `33e5027e` | 2026-06-01 | commit | docs(105-01): fill all 8 DECISION lines + write 105-01-SUMMARY.md |
| 1061 | `84d7a803` | 2026-06-01 | commit | docs(105-01): complete plan 01 - STATE.md + ROADMAP.md updated (1/4 plans done) |
| 1062 | `5c1b5197` | 2026-06-01 | commit | feat(105-02): migrate files-cluster templates to Phosphor + add ph-spin CSS prereq |
| 1063 | `5ade12be` | 2026-06-01 | commit | feat(105-03): migrate settings cluster - FA->Phosphor across all five D-05 layers |
| 1064 | `e15a1d3d` | 2026-06-01 | commit | test(105-02): update 3 files-cluster specs to Phosphor classes (inline templates + DOM assertions) |
| 1065 | `868ec8fa` | 2026-06-01 | commit | docs(105-02): complete plan 02 - files-cluster Phosphor migration SUMMARY (611/611 PASS, floors held) |
| 1066 | `201876f1` | 2026-06-01 | commit | feat(105-03): migrate logs + main clusters - FA->Phosphor, spec updated, Karma 611/611 green |
| 1067 | `c4aa41fc` | 2026-06-01 | commit | docs(105-03): complete plan 03 - settings/logs/main cluster migrated, Karma 611/611, floors held |
| 1068 | `a47387b5` | 2026-06-01 | merge | chore: merge executor worktree (105-02 files-cluster Phosphor migration) |
| 1069 | `c778af8b` | 2026-06-01 | merge | chore: merge executor worktree (105-03 settings/logs/main Phosphor migration) |
| 1070 | `b591a3a4` | 2026-06-01 | commit | docs(phase-105): update tracking after wave 2 (105-02 + 105-03 complete) |
| 1071 | `314fd6a1` | 2026-06-01 | commit | chore(105-04): drop font-awesome dep + regen lock + AFTER bundle delta recorded |
| 1072 | `12a04fac` | 2026-06-01 | commit | docs(105-04): complete plan 04 - FA dep drop + bundle delta + D-04 smoke test APPROVED |
| 1073 | `6fb1f88d` | 2026-06-01 | commit | docs(phase-105): verification passed (7/7 must-haves) + record session |
| 1074 | `b4d7b579` | 2026-06-01 | commit | docs(106): capture phase context |
| 1075 | `79fc8f98` | 2026-06-01 | commit | docs(state): record phase 106 context session |
| 1076 | `9428419a` | 2026-06-01 | commit | docs(phase-106): create phase plan (DEPS-02 mock-fixture bundle hygiene, 2 plans/2 waves) |
| 1077 | `b3cf4f13` | 2026-06-01 | commit | docs(106): add decision-coverage citations + record planned phase |
| 1078 | `00a77a4f` | 2026-06-01 | commit | docs(106): tighten plan verification per codex adversarial review (true before/after delta, no temp toggle, browser DOM smoke, artifact tracking) |
| 1079 | `2ead4382` | 2026-06-01 | commit | feat(106-01): relocate mock fixture, add env flag, add prod stub, extend fileReplacements, delete dead file |
| 1080 | `7aa11d05` | 2026-06-01 | commit | fix(106-01): add environment.test.ts (useMockModel false) + test fileReplacements; fix fixture ModelFile import |
| 1081 | `6186bed6` | 2026-06-01 | commit | feat(106-01): record Phase-106 bundle baseline, dist absence proof, Karma floor record |
| 1082 | `92d2079e` | 2026-06-01 | commit | docs(106-01): complete plan 01 - mock fixture bundle hygiene SUMMARY + self-check PASSED |
| 1083 | `c6be84c1` | 2026-06-01 | merge | chore: merge executor worktree (106-01 mock-fixture bundle hygiene) |
| 1084 | `537ce65f` | 2026-06-01 | commit | docs(phase-106): update tracking after wave 1 (106-01 complete) |
| 1085 | `13ab1a66` | 2026-06-01 | commit | docs(106-02): dev-mode smoke test APPROVED - mock toggle renders via env flag (DEPS-02 COMPAT) |
| 1086 | `9df7d2b1` | 2026-06-01 | commit | docs(phase-106): update tracking after wave 2 (106-02 complete) |
| 1087 | `f9e56153` | 2026-06-01 | commit | docs(phase-106): verification passed (DEPS-02, 10/10 decisions verified goal-backward) |
| 1088 | `948cfaaa` | 2026-06-01 | commit | docs(state): record phase 106 complete |
| 1089 | `cb1b2e3b` | 2026-06-01 | commit | docs: start milestone v1.3.0-s4 Backend Architecture Refactor + Test Infra |
| 1090 | `d7c346ff` | 2026-06-01 | commit | docs: define milestone v1.3.0-s4 requirements (ARCH-01/02/03 + INFRA-01) |
| 1091 | `f27ba30b` | 2026-06-01 | commit | docs: create milestone v1.3.0-s4 roadmap (3 phases) |
| 1092 | `5fe1985e` | 2026-06-01 | commit | docs(107): capture phase context |
| 1093 | `f41fac71` | 2026-06-01 | commit | docs(state): record phase 107 context session |
| 1094 | `94a365ca` | 2026-06-01 | commit | docs(107): add research + validation strategy |
| 1095 | `9ee0f6dc` | 2026-06-01 | commit | docs(107): create phase plan (INFRA-01 MP-logger spawn safety) |
| 1096 | `4a2cf7fc` | 2026-06-01 | commit | docs(107): cite D-01/D-02/D-03 in plan truths (decision-coverage gate) |
| 1097 | `c6dcb917` | 2026-06-01 | commit | docs(107): rewrite plan to fix spawn-pickle blocker (adversarial rewrite 1) |
| 1098 | `5eb00ad5` | 2026-06-01 | commit | feat(107-01): spawn-safe queue context and spawn-picklable instance |
| 1099 | `67476dd6` | 2026-06-01 | commit | feat(107-01): promote spawn closures to module scope, assert child exitcode == 0 |
| 1100 | `e40fdda6` | 2026-06-01 | commit | docs(107-01): complete MP-logger spawn safety plan - SUMMARY + deferred items |
| 1101 | `fcec3f8f` | 2026-06-01 | merge | chore: merge executor worktree (107-01 MP-logger spawn safety) |
| 1102 | `a4dfea19` | 2026-06-01 | commit | docs(phase-107): update tracking after wave 1 |
| 1103 | `a85a59e3` | 2026-06-01 | commit | docs(state): record AppProcess spawn-pickle tech debt (Phase 107 review finding) |
| 1104 | `f5514d3a` | 2026-06-01 | commit | docs(108): capture phase context |
| 1105 | `44d9ec15` | 2026-06-01 | commit | docs(state): record phase 108 context session |
| 1106 | `8a7a52a9` | 2026-06-01 | commit | docs(108): create phase plan - ARCH-02 declarative secrets + ARCH-03 dispatch dedup |
| 1107 | `cc8f870a` | 2026-06-01 | commit | docs(108): create phase plan (redact descope + bulk defer amendments) |
| 1108 | `32a7a3e0` | 2026-06-01 | commit | docs(108): harden plans with codex adversarial findings (discovery scope, test viability, verify cmds) |
| 1109 | `b853a18d` | 2026-06-01 | commit | test(108-02): backfill single-action failure + exact timeout body tests (F4) |
| 1110 | `1b736ca6` | 2026-06-01 | commit | test(108-01): add failing tests for Config secret_fields() discovery API (RED) |
| 1111 | `43ee5186` | 2026-06-01 | commit | feat(108-02): extract _dispatch_command helper; thin five single-action handlers |
| 1112 | `1db7de01` | 2026-06-01 | commit | docs(108-02): complete ARCH-03 dispatch-dedup plan - SUMMARY |
| 1113 | `126ad92a` | 2026-06-01 | commit | feat(108-01): add secret flag to PropMetadata, flag 5 PROPs, add secret_fields() API (GREEN) |
| 1114 | `7f6c0baa` | 2026-06-01 | commit | feat(108-01): delete _SECRET_FIELD_PATHS tuple; repoint seedsyncarr startup loop |
| 1115 | `0c0859d3` | 2026-06-01 | commit | docs(108-01): complete ARCH-02 declarative secret discovery plan - SUMMARY |
| 1116 | `ecaa1357` | 2026-06-01 | merge | chore: merge executor worktree (108-01 ARCH-02 declarative secret discovery) |
| 1117 | `34fc7837` | 2026-06-01 | merge | chore: merge executor worktree (108-02 ARCH-03 dispatch dedup) |
| 1118 | `cfe943a6` | 2026-06-01 | commit | docs(phase-108): update tracking after wave 1 |
| 1119 | `749313af` | 2026-06-01 | commit | docs(phase-108): complete phase execution (reviewed clean; ARCH-02+ARCH-03 traceability + STATE advance) |
| 1120 | `50041455` | 2026-06-01 | commit | docs(109): capture phase context |
| 1121 | `c00319fe` | 2026-06-01 | commit | docs(state): record phase 109 context session |
| 1122 | `f49765fe` | 2026-06-01 | commit | docs(phase-109): research controller decomposition |
| 1123 | `10511b63` | 2026-06-01 | commit | docs(phase-109): add research + validation strategy |
| 1124 | `d8cdc0d1` | 2026-06-01 | commit | docs(109): create phase plan - Controller decomposition (3 sequential plans) |
| 1125 | `55b2352b` | 2026-06-01 | commit | docs(109): add decision traceability + record planning |
| 1126 | `e5f03d4e` | 2026-06-01 | commit | docs(109): adversarial fix - forwarding wrappers for test-pinned helpers (109-03) |
| 1127 | `cd08865d` | 2026-06-01 | commit | docs(109): add pattern map |
| 1128 | `f1e75501` | 2026-06-01 | commit | feat(109-01): create CommandProcessor collaborator with four extracted handle methods |
| 1129 | `df415a8a` | 2026-06-01 | commit | feat(109-01): wire CommandProcessor into Controller, delete four handle methods |
| 1130 | `a2b9fe39` | 2026-06-01 | commit | docs(109-01): complete CommandProcessor extraction plan - SUMMARY |
| 1131 | `847cb50d` | 2026-06-01 | commit | docs(109-01): update STATE, ROADMAP, REQUIREMENTS after plan completion |
| 1132 | `0f73584f` | 2026-06-01 | commit | feat(109-02): create AutoDeleteManager collaborator with BFS+coverage logic |
| 1133 | `5e2b7a30` | 2026-06-01 | commit | feat(109-02): wire AutoDeleteManager into Controller; keep WR-02 lock harness on Controller |
| 1134 | `04f69d3c` | 2026-06-01 | commit | docs(109-02): complete AutoDeleteManager extraction plan - SUMMARY |
| 1135 | `0639221d` | 2026-06-01 | commit | feat(109-03): create ModelPipeline collaborator with scan->build->diff->apply pipeline logic |
| 1136 | `4fb13b42` | 2026-06-01 | commit | feat(109-03): wire ModelPipeline into Controller; replace pipeline bodies with forwarding wrappers |
| 1137 | `1be53942` | 2026-06-01 | commit | docs(109-03): complete ModelPipeline extraction plan - SUMMARY |
| 1138 | `6cdb8945` | 2026-06-01 | commit | docs(109): phase verification passed (5/5 criteria, ARCH-01) |
| 1139 | `cd2022b3` | 2026-06-02 | commit | chore(lint): fix pre-existing ruff failures in test files |
| 1140 | `d82407b4` | 2026-06-02 | commit | docs(audit): v1.3.0 milestone audit PASSED (4/4 reqs, integration intact, walkthrough clean) |
| 1141 | `9814e683` | 2026-06-02 | commit | chore: archive v1.3.0 milestone |
| 1142 | `caa788b9` | 2026-06-02 | commit | chore: remove REQUIREMENTS.md for v1.3.0 milestone |
| 1143 | `c8ed90cf` | 2026-06-02 | commit | fix(security): broaden confirm-modal XSS test helper to all dangerous URL schemes |
| 1144 | `b8f4895f` | 2026-06-02 | commit | docs: finalize v1.3.0 milestone bookkeeping post-tag |
| 1145 | `f05002f2` | 2026-06-02 | commit | chore(release): bump version to 1.3.0 |
| 1146 | `2e2311c4` | 2026-06-02 | commit | docs: refresh codebase map post-v1.3.0 |
| 1147 | `b0a7c397` | 2026-06-02 | commit | docs: start milestone v1.4.0 Launch-Hardening for Public Release |
| 1148 | `4bf8ca1f` | 2026-06-02 | commit | docs: define milestone v1.4.0 requirements |
| 1149 | `8ffceeef` | 2026-06-02 | commit | docs: create milestone v1.4.0 roadmap (4 phases) |
| 1150 | `26247e6f` | 2026-06-02 | commit | docs: tag 1 pending todo with resolves_phase after milestone v1.4.0 roadmap |
| 1151 | `4a44d13c` | 2026-06-02 | commit | docs(110): capture phase context |
| 1152 | `1a7a9aac` | 2026-06-02 | commit | docs(state): record phase 110 context session |
| 1153 | `3a68d51a` | 2026-06-02 | commit | docs(roadmap): unwrap active v1.4.0 <details> so GSD SDK resolves phases |
| 1154 | `38962ca7` | 2026-06-02 | commit | docs(110): research phase hostile-reader discovery pass |
| 1155 | `8fd9152d` | 2026-06-02 | commit | docs(110): create hostile-reader discovery pass plan (1 plan, 1 wave) |
| 1156 | `15ad4c92` | 2026-06-02 | commit | docs(110): cite decision IDs in plan truths for coverage gate |
| 1157 | `b87676ce` | 2026-06-02 | commit | docs(110): revise plan per codex adversarial review (1 high + 3 medium) |
| 1158 | `8ec562b6` | 2026-06-02 | commit | docs(110): forbid repo-local report creation outright (codex re-review high) |
| 1159 | `d235a42a` | 2026-06-02 | commit | feat(110-01): write hostile-reader findings artifact with fold/park dispositions |
| 1160 | `1e4f54d7` | 2026-06-02 | commit | docs(110-01): complete hostile-reader discovery pass plan - checkpoint at Task 3 |
| 1161 | `2954055c` | 2026-06-02 | commit | docs(110): lock findings dispositions - maintainer approved (fix scope for 111-113) |
| 1162 | `54808d2d` | 2026-06-02 | commit | docs(state): mark phase 110 complete - ready for phase 111 |
| 1163 | `d392ef53` | 2026-06-02 | commit | docs(111): capture phase context |
| 1164 | `9ef36d9b` | 2026-06-02 | commit | docs(state): record phase 111 context session |
| 1165 | `f1956d53` | 2026-06-02 | commit | docs(111): research phase domain |
| 1166 | `afcd5d63` | 2026-06-02 | commit | docs(111): create phase plan - config-set GET->POST migration (3 plans, 2 waves) |
| 1167 | `5dffae8f` | 2026-06-02 | commit | docs(111): add validation strategy (Nyquist dimension 8) |
| 1168 | `793b55d1` | 2026-06-02 | commit | docs(111): revise plans + research per plan-checker (round 1) |
| 1169 | `0807a48d` | 2026-06-02 | commit | docs(state): record phase 111 planning session |
| 1170 | `61c8d9d3` | 2026-06-02 | commit | docs(111): harden plan per codex adversarial review (type guards, CFG-02 404/405 split, invalid-JSON test) |
| 1171 | `a0744395` | 2026-06-02 | commit | docs(111): align PATTERNS+RESEARCH snippets with hardened handler (codex rewrite 2) |
| 1172 | `ee6b0953` | 2026-06-02 | commit | docs(111): tighten CFG-02 404/405 split in plan + research test-map |
| 1173 | `dabdcc3d` | 2026-06-02 | commit | docs(111): forbid dead JSON try/except in all actionable handler snippets (codex FINDING 3 full closure) |
| 1174 | `386f68c9` | 2026-06-02 | commit | feat(111-01): rewrite config.py - POST handler + route registration + remove unquote |
| 1175 | `fe0e07d6` | 2026-06-02 | commit | feat(111-01): migrate integration tests GET->POST + add error-surface coverage |
| 1176 | `fffa81ad` | 2026-06-02 | commit | feat(111-01): migrate unit tests - mock bottle.request.json; replace encoding tests; fix rate-limit |
| 1177 | `d5943159` | 2026-06-02 | commit | docs(111-01): complete config-set POST migration plan - SUMMARY |
| 1178 | `b55339a1` | 2026-06-02 | merge | chore: merge executor worktree (111-01 backend POST migration) |
| 1179 | `84aff278` | 2026-06-02 | commit | docs(phase-111): update tracking after wave 1 |
| 1180 | `ce9b0577` | 2026-06-02 | commit | feat(111-03): migrate 11 config-set curls in setup_seedsyncarr.sh to POST JSON |
| 1181 | `3c27e170` | 2026-06-02 | commit | feat(111-02): extend RestService.post with optional JSON body |
| 1182 | `e07fed23` | 2026-06-02 | commit | feat(111-02): rewrite ConfigService.set transport to POST JSON body |
| 1183 | `1eb8c41a` | 2026-06-02 | commit | feat(111-03): migrate settings.page.ts (7 helpers) and seed-state.ts to page.request.post |
| 1184 | `1a0c55d3` | 2026-06-02 | commit | feat(111-03): migrate 2 inline rate_limit calls in dashboard.page.spec.ts to page.request.post |
| 1185 | `bad07ab3` | 2026-06-02 | commit | feat(111-02): migrate config.service.spec.ts GET-URL expectations to POST |
| 1186 | `8528c602` | 2026-06-02 | commit | docs(111-03): complete E2E POST migration plan - SUMMARY.md |
| 1187 | `89dc27e3` | 2026-06-02 | commit | docs(111-02): complete Angular client POST migration - SUMMARY |
| 1188 | `6486fe2f` | 2026-06-02 | merge | chore: merge executor worktree (111-02 Angular POST migration) |
| 1189 | `7344b2e1` | 2026-06-02 | merge | chore: merge executor worktree (111-03 E2E POST migration) |
| 1190 | `2784b339` | 2026-06-02 | commit | docs(phase-111): update tracking after wave 2 |
| 1191 | `aef5981c` | 2026-06-02 | commit | docs(111): phase verification - 9/9 must-haves passed; live E2E deferred to NAS walkthrough |
| 1192 | `419268e0` | 2026-06-02 | commit | docs(state): mark phase 111 complete - config-set POST migration, deep review clean |
| 1193 | `8559041e` | 2026-06-02 | commit | docs(112): capture phase context |
| 1194 | `9cc519f7` | 2026-06-02 | commit | docs(state): record phase 112 context session |
| 1195 | `5166b891` | 2026-06-02 | commit | docs(112): research phase - spawn fix repro, GUARD-01..06 findings |
| 1196 | `cdc12e0f` | 2026-06-02 | commit | docs(phase-112): add validation strategy |
| 1197 | `e72ab174` | 2026-06-02 | commit | docs(112): create phase plan - 3 file-disjoint plans, 1 wave |
| 1198 | `0e7e1dbc` | 2026-06-02 | commit | docs(112): finalize phase plan (decision-coverage citations) |
| 1199 | `7e66aee7` | 2026-06-02 | commit | docs(112): fold codex adversarial-review findings into plans |
| 1200 | `ce418f95` | 2026-06-02 | commit | docs(phase-112): begin phase execution |
| 1201 | `633ce9b2` | 2026-06-02 | commit | chore(112-01): add .orchestrator.json and .playwright-mcp/ to .gitignore (GUARD-05) |
| 1202 | `042e212d` | 2026-06-02 | commit | test(112-02): add RED tests for GUARD-02 matrix + GUARD-06 fallback (112-02 Task 1) |
| 1203 | `478dfbbb` | 2026-06-02 | commit | feat(112-02): GUARD-02 warning correctness + GUARD-01 prominence (112-02 Task 2) |
| 1204 | `af0a08cc` | 2026-06-02 | commit | test(112-03): add failing DeleteLocalProcess rmtree-failure test (RED) |
| 1205 | `df35a2c9` | 2026-06-02 | commit | feat(112-03): GUARD-03 - replace ignore_errors=True with logged try/except OSError |
| 1206 | `304d95a2` | 2026-06-02 | commit | docs(112-03): complete GUARD-03 plan - logged local delete failures |
| 1207 | `d94684c4` | 2026-06-02 | commit | feat(112-02): GUARD-06 surface legacy fallback warning via configured logger (112-02 Task 3) |
| 1208 | `bbfbecd8` | 2026-06-02 | commit | docs(112-02): complete GUARD-01/02/06 startup-warning hardening plan |
| 1209 | `fc604e18` | 2026-06-02 | commit | feat(112-01): add AppProcess.__getstate__/__setstate__ for spawn-safe pickling (GUARD-04) |
| 1210 | `1cf02551` | 2026-06-02 | commit | docs(112-01): complete GUARD-04/GUARD-05 plan - AppProcess spawn-safe, gitignore hardened |
| 1211 | `13bde3ee` | 2026-06-02 | merge | chore: merge executor worktree (worktree-agent-a9f6c06976be6ce42) |
| 1212 | `53e62d8f` | 2026-06-02 | merge | chore: merge executor worktree (worktree-agent-a758b1f58191b1bed) |
| 1213 | `2df8ee8b` | 2026-06-02 | merge | chore: merge executor worktree (worktree-agent-ab3f21d3359dac2b0) |
| 1214 | `c1751ea3` | 2026-06-02 | commit | docs(phase-112): update tracking after wave 1 |
| 1215 | `03eb307f` | 2026-06-02 | commit | docs(phase-112): verification passed (6/6 must-haves) |
| 1216 | `e1d2c383` | 2026-06-02 | commit | docs(state): record phase 112 complete |
| 1217 | `17e0ba82` | 2026-06-02 | commit | fix(review-pass-1): make AppProcess Thread-drop contract discoverable (arch-002) |
| 1218 | `69401ccc` | 2026-06-02 | commit | fix(review-pass-1): annotate _parse_args tuple return type (py-001) |
| 1219 | `6fcb62f9` | 2026-06-02 | commit | docs(state): phase 112 deep-review clean |
| 1220 | `b3aef0c1` | 2026-06-02 | commit | docs(113): capture phase context |
| 1221 | `3609fde1` | 2026-06-02 | commit | docs(state): record phase 113 context session |
| 1222 | `12b7b16f` | 2026-06-02 | commit | docs(113): create phase plan |
| 1223 | `da2e47cf` | 2026-06-02 | commit | docs(113): cite D-NN decision IDs in plan must_haves for coverage gate |
| 1224 | `155ce9d5` | 2026-06-02 | commit | docs(113): revise plans for codex adversarial findings (claim accuracy, install-runnability, repo-wide LICENSE audit, outbound-push anti-brag gates) |
| 1225 | `f969353c` | 2026-06-02 | commit | docs(113): mark un-rate-limited endpoint list non-exhaustive + add restart (codex re-review blocker) |
| 1226 | `f529ed05` | 2026-06-02 | commit | chore(113-01): rename LICENSE.txt to LICENSE; fix all user-facing links (HR-05 / D-09) |
| 1227 | `50328e98` | 2026-06-02 | commit | docs(113-01): first-draft README targeted rewrite (LAUNCH-02, D-01..D-04) |
| 1228 | `d8cff660` | 2026-06-02 | commit | feat(113-02): write cynical-reader teardown artifact (LAUNCH-01, D-10) |
| 1229 | `db6f2b77` | 2026-06-02 | commit | docs(113-02): complete cynical-reader teardown plan |
| 1230 | `71e56f81` | 2026-06-02 | commit | docs(113-01): first-draft SECURITY.md posture + CONTRIBUTING.md freshen (LAUNCH-04/05) |
| 1231 | `401c3c82` | 2026-06-02 | commit | docs(113-01): add [1.4.0] CHANGELOG entry + compare-link footers (LAUNCH-06, D-11) |
| 1232 | `949d5b3d` | 2026-06-02 | commit | docs(113-01): add CODE_OF_CONDUCT.md (Contributor Covenant 2.1) (LAUNCH-05, D-09) |
| 1233 | `025c5de6` | 2026-06-02 | commit | docs(113-01): complete plan summary |
| 1234 | `9cf9452b` | 2026-06-02 | merge | chore: merge executor worktree (worktree-agent-a1f99460b01fee206) |
| 1235 | `f0efcfbd` | 2026-06-02 | merge | chore: merge executor worktree (worktree-agent-a7abdb96e3ad62431) |
| 1236 | `fc63320b` | 2026-06-02 | commit | docs(phase-113): update tracking after wave 1 |
| 1237 | `cb9268be` | 2026-06-02 | commit | feat(113-03): run codex adversarial content pass over Plan-01 drafts |
| 1238 | `dd78f428` | 2026-06-02 | commit | docs(113-03): complete codex content pass plan summary |
| 1239 | `7db5433a` | 2026-06-02 | merge | chore: merge executor worktree (worktree-agent-a97f00b4c15942fcf) |
| 1240 | `f37fe6b3` | 2026-06-02 | commit | docs(phase-113): update tracking after wave 2 |
| 1241 | `2b41f9ec` | 2026-06-02 | commit | docs(113-04): finalize public docs per teardown + codex findings; wire screenshot refs; draft repo metadata (LAUNCH-02/03/04/05/06) |
| 1242 | `1804c271` | 2026-06-02 | commit | docs(113-04): complete plan summary (finalization + human-verify approved) |
| 1243 | `03720445` | 2026-06-02 | commit | docs(phase-113): update tracking after wave 3 |
| 1244 | `ea7ec456` | 2026-06-02 | commit | docs(113): phase verification (6/6 must-haves; editorial sign-off self-approved by orchestrator per operator instruction) |
| 1245 | `8b03be8e` | 2026-06-02 | commit | fix(review-pass-1): doc-precision fixes from turingmind deep review |
| 1246 | `ca817ecb` | 2026-06-02 | commit | docs(v1.4.0): milestone audit - fix GUARD-04 CHANGELOG mechanism wording, sync requirements traceability |
| 1247 | `2f04969c` | 2026-06-03 | merge | Merge launch-hardening: v1.4.0 - Launch-Hardening for Public Release (phases 110-113) |
| 1248 | `879266c5` | 2026-06-03 | commit | fix(ui): clamp transfer progress at 100% for extracted files |
| 1249 | `f4da0ee6` | 2026-06-03 | commit | docs(readme): real dashboard screenshot from NAS walkthrough; single-shot Screenshots section |
| 1250 | `31b4a7f8` | 2026-06-03 | commit | docs(v1.4.0): record walkthrough outcome in milestone audit |
| 1251 | `7c3c7b79` | 2026-06-03 | commit | docs(v1.4.0): finalize release notes - rewrite release-notes.md for v1.4.0, bump CHANGELOG date, drop launch framing |
| 1252 | `c9da559b` | 2026-06-03 | commit | chore: archive v1.4.0 milestone |
| 1253 | `c14debee` | 2026-06-03 | commit | chore: remove REQUIREMENTS.md for v1.4.0 milestone (archived to milestones/v1.4.0-REQUIREMENTS.md) |
| 1254 | `318acc27` | 2026-06-03 | commit | chore: archive v1.4.0 milestone files (roadmap collapse, requirements+audit archive, milestone entry) |
| 1255 | `de7a7f99` | 2026-06-03 | commit | chore(release): bump version to 1.4.0 across package metadata |
| 1256 | `24ed8b2e` | 2026-06-04 | commit | chore(deps): bump webob (#51) |
| 1257 | `957a8969` | 2026-06-04 | commit | chore(deps-dev): bump ruff from 0.15.14 to 0.15.15 in /src/python (#49) |
| 1258 | `ac087a5b` | 2026-06-04 | commit | chore(deps): bump the npm_and_yarn group in /src/angular with 18 updates (#50) |
| 1259 | `6b4e4757` | 2026-06-04 | commit | docs(quick-260604-g9c): handle open Dependabot PRs and alerts |
| 1260 | `feef3c69` | 2026-06-04 | commit | docs(quick-260604-gmy): fix Angular v22 strict-template type errors, merge PR #50 |
| 1261 | `2457f1eb` | 2026-06-08 | commit | fix(docker): honor PUID/PGID at runtime so mounts stay writable |
| 1262 | `dffa3359` | 2026-06-08 | commit | fix(autoqueue): demote per-cycle "Process cycle" log from info to debug |
| 1263 | `81bb377c` | 2026-06-08 | commit | docs(readme): document PUID/PGID for /config and /downloads ownership |
| 1264 | `42e9534c` | 2026-06-08 | merge | Merge pull request #52 from thejuran/fix/docker-puid-pgid-honoring |
| 1265 | `3890e172` | 2026-06-08 | commit | fix(docker): give remapped user a writable SSH home so remote scan works |
| 1266 | `003f2eea` | 2026-06-08 | commit | fix(docker): make SSH-home chown tolerant of read-only bind mounts |
| 1267 | `f099d739` | 2026-06-08 | merge | Merge pull request #53 from thejuran/fix/entrypoint-ssh-home |
| 1268 | `54ccbe94` | 2026-06-10 | commit | chore(deps-dev): bump ruff from 0.15.15 to 0.15.16 in /src/python (#54) |
| 1269 | `2cb5475b` | 2026-06-10 | commit | chore(deps-dev): bump the npm_and_yarn group (#55) |
| 1270 | `ad8c6c40` | 2026-06-09 | commit | docs: map existing codebase |
| 1271 | `a53b9de4` | 2026-06-14 | commit | fix(deps): pin esbuild to 0.28.1 to resolve Dependabot alerts #20, #21 |
| 1272 | `5f40396f` | 2026-06-15 | commit | chore(deps): bump @angular/core from 22.0.0 to 22.0.1 in /src/angular (#56) |
| 1273 | `8b671f2a` | 2026-06-15 | commit | docs(planning): config profile + debug/patterns/comparison artifacts |
| 1274 | `ab0da5ea` | 2026-06-15 | commit | chore(release): v1.4.1 - security maintenance |
| 1275 | `d47e230f` | 2026-06-15 | commit | chore(deps): bump @angular/compiler in /src/angular (#57) |
| 1276 | `af7f1e92` | 2026-06-15 | commit | fix: mp-logger spawn-hang (NAS deploy blocker) + clear all open Dependabot alerts (#59) |
| 1277 | `31ec66a4` | 2026-06-15 | commit | chore(release): v1.4.2 - spawn-hang fix + security maintenance |
| 1278 | `bca2190c` | 2026-06-21 | commit | docs: start milestone v1.4.1 Scanner Auto-Recovery |
| 1279 | `0a81147a` | 2026-06-21 | commit | docs: define milestone v1.4.1 requirements |
| 1280 | `63d23caa` | 2026-06-21 | commit | docs: create milestone v1.4.1 roadmap (2 phases) |
| 1281 | `67b7db67` | 2026-06-21 | commit | docs(114): capture phase context |
| 1282 | `ef749a38` | 2026-06-21 | commit | docs(state): record phase 114 context session |
| 1283 | `01bf24a9` | 2026-06-21 | commit | docs(114): research scanner auto-recovery domain |
| 1284 | `b183629d` | 2026-06-21 | commit | docs(phase-114): add validation strategy |
| 1285 | `59b5756e` | 2026-06-21 | commit | docs(114): create phase plan (2 plans, 1 wave) |
| 1286 | `4d2b5795` | 2026-06-21 | commit | docs(114): rewrite scanner-auto-recovery plans for codex findings |
| 1287 | `921bf33f` | 2026-06-21 | commit | docs(114): tighten RECOV-01 reset-at-cap to fresh budget (codex follow-on) |
| 1288 | `36f9cf91` | 2026-06-21 | commit | docs(114): fix ServiceRestart ctor spec + align in-scan retry scope (codex) |
| 1289 | `0ea26a2b` | 2026-06-21 | commit | docs(114): extend bounded name-resolution retry to the install path (close codex HIGH coverage gap) |
| 1290 | `bdd098d0` | 2026-06-21 | commit | docs(114): broaden name-resolution matcher to all resolver-string surfaces (close codex HIGH coverage gap) |
| 1291 | `80ab3ba3` | 2026-06-21 | commit | feat(114-02): add pure _should_auto_restart helper + restart constants |
| 1292 | `cfe5e370` | 2026-06-21 | commit | test(114-01): name-resolution matcher + tuple + RED retry tests (all resolver surfaces) |
| 1293 | `cb92dbde` | 2026-06-21 | commit | feat(114-02): bounded controller auto-restart wiring (RECOV-01) |
| 1294 | `ec6dfa74` | 2026-06-21 | commit | docs(114-02): complete bounded controller auto-restart plan |
| 1295 | `f497cd29` | 2026-06-21 | commit | feat(114-01): shared bounded name-resolution retry for scan + install ops |
| 1296 | `ee097880` | 2026-06-21 | commit | docs(114-01): complete scanner name-resolution in-scan retry plan |
| 1297 | `e209c1c7` | 2026-06-21 | merge | chore: merge executor worktree (114-01 scanner name-resolution retry) |
| 1298 | `ed494483` | 2026-06-21 | merge | chore: merge executor worktree (114-02 controller auto-restart) |
| 1299 | `ca2b31d6` | 2026-06-21 | commit | docs(phase-114): update tracking after wave 1 |
| 1300 | `663211ae` | 2026-06-22 | commit | fix(review-pass-1): Unsanitized SSH error message logged without sanitize_log_value |
| 1301 | `4669d769` | 2026-06-22 | commit | docs(115): capture phase context |
| 1302 | `c8e7d882` | 2026-06-22 | commit | docs(state): record phase 115 context session |
| 1303 | `5f471609` | 2026-06-22 | commit | docs(115): create phase plan |
| 1304 | `d5c7ac2d` | 2026-06-22 | commit | docs(115): harden plan - SHA-pinned merge gate, blocker-on-non-MERGED, BEHIND-tolerant |
| 1305 | `aec57ca1` | 2026-06-22 | commit | docs(115): harden SHA-pin merge gate, remove auto-replacement-PR machinery |
| 1306 | `796f457d` | 2026-06-22 | commit | chore(deps): bump the npm_and_yarn group in /src/angular with 18 updates (#64) |
| 1307 | `8a341b93` | 2026-06-22 | commit | chore(deps-dev): bump hono from 4.12.23 to 4.12.26 in /src/angular (#65) |
| 1308 | `452c290e` | 2026-06-22 | commit | chore(deps-dev): bump undici from 7.27.0 to 7.28.0 in /src/angular (#66) |
| 1309 | `4d7a35d3` | 2026-06-22 | commit | chore(deps-dev): bump pyinstaller from 6.20.0 to 6.21.0 in /src/python (#60) |
| 1310 | `161a3571` | 2026-06-22 | commit | chore(deps-dev): bump ruff from 0.15.16 to 0.15.17 in /src/python (#61) |
| 1311 | `90635273` | 2026-06-22 | commit | chore(deps-dev): bump testfixtures from 12.0.0 to 12.1.0 in /src/python (#62) |
| 1312 | `cb15f08c` | 2026-06-22 | commit | chore(deps-dev): bump pytest from 9.0.3 to 9.1.0 in /src/python (#63) |
| 1313 | `c6b20b44` | 2026-06-22 | merge | Merge branch 'main' of https://github.com/thejuran/seedsyncarr |
| 1314 | `4e61d89e` | 2026-06-22 | commit | docs(115-01): complete dependency-security-maintenance plan |
| 1315 | `39133ffc` | 2026-06-22 | commit | fix(deps): override piscina >=5.2.0 to close Dependabot alert #37 (DEPS-01) (#67) |
| 1316 | `dd42716e` | 2026-06-22 | commit | docs(115-01): resolve piscina alert #37 via override PR #67 - DEPS-01 met |
| 1317 | `98d0c34b` | 2026-06-22 | commit | docs(v1.4.1): milestone audit + back-fill SCAN/RECOV traceability |
| 1318 | `93f3f3e2` | 2026-06-22 | commit | chore(release): v1.5.0 - scanner auto-recovery + dependency security maintenance |
| 1319 | `3db8b48b` | 2026-06-22 | commit | chore: archive v1.4.1 milestone (Scanner Auto-Recovery -> tagged v1.5.0) |
