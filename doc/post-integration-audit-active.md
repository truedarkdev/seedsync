# Post-Integration Audit Active Ledger

This file contains only unfinished audit rows.

Open this file by default during active audit work. Completed rows move into per-fork archive files in `doc/post-integration-audit-archive/` in chunks of up to 50 finished commits.

Related files:
- Rules and workflow: [post-integration-audit-rules.md](/mnt/c/Git/seedsync/doc/post-integration-audit-rules.md)
- Audit landing page and archive index: [post-integration-audit.md](/mnt/c/Git/seedsync/doc/post-integration-audit.md)

## thejuran

Audit base: `origin/master @ ff2a1039935beccbbf7ec76134b41d2e91137742`
Source branch: `thejuran/master`
Fork tip at audit start: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
Inventory status: `complete`
Audit state: `in progress`
Pass date: `2026-03-11`
Maintainer-approved batch size: `27`

Open rows in this file: `77 / 672 remaining`

| Commit | Upstream commit subject | Mapped integration subject | Triage outcome | Confidence | Evidence | Reviewer needed | Coverage | Final disposition | Follow-up / proof |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `713825d930552dd30814a6daaf90194c871f0a44` | fix(41-02): thread-safe queue access and listener lock in ExtractDispatch | unknown | unprocessed | — | — | — | — | — | — |
| `4c1bbabd592f8afc1af21e1bc86aa00635024f94` | test(41-01): add thread-safety tests for auto-delete and webhook import locks | unknown | unprocessed | — | — | — | — | — | — |
| `5e2a62c7b6108b0abb1153fdbbf6588a858646a8` | test(41-02): add thread-safety tests for ExtractDispatch queue mutex and copy-under-lock | unknown | unprocessed | — | — | — | — | — | — |
| `5d321c8d145d5d871211f2a671d694e3161805d9` | docs(41-01): complete thread-safety model lock plan | unknown | unprocessed | — | — | — | — | — | — |
| `248533df05dde4265687ea087a6213d38af5fb5e` | docs(41-02): complete ExtractDispatch queue mutex and copy-under-lock plan | unknown | unprocessed | — | — | — | — | — | — |
| `be53b866d0768f9b5606323ca8a8b96ee8467c3b` | docs(phase-41): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `57ec9ee1ae031129d8fdba17c0da6202cb8ba45e` | docs(42): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `e736b6932d22afaf8a05a1e6e5ec7906dd1b6818` | fix(42-02): guard SSE dispatch against unknown event names (CRASH-04) | unknown | unprocessed | — | — | — | — | — | — |
| `05a00038c6e383acb842b9fef3ad0113287bbe4c` | fix(42-03): add bounded 30s timeout to all individual action endpoint waits | unknown | unprocessed | — | — | — | — | — | — |
| `52104364e91c70af1cd797c13d9d0241c635d23b` | fix(42-01): fix propagate_exception redundant raise and WebhookManager bare except | unknown | unprocessed | — | — | — | — | — | — |
| `a7122fc164c1c64bab0bc3049007eac3d89e2750` | fix(42-02): wrap JSON.parse in try/catch across all SSE stream services (CRASH-05) | unknown | unprocessed | — | — | — | — | — | — |
| `d2e4befb839e10f50f1cabb7a31a873b27b3f9cc` | fix(42-01): guard _estimate_root_eta against None remote_size (CRASH-02) | unknown | unprocessed | — | — | — | — | — | — |
| `7b2cdd37129df37f1a116d790f921046534eab53` | docs(42-03): complete bounded action timeout plan | unknown | unprocessed | — | — | — | — | — | — |
| `0bac5618cc9156379dcabf85897b0140826e2ad1` | docs(42-01): complete crash-prevention plan 01 — propagate_exception, ETA guard, bare except | unknown | unprocessed | — | — | — | — | — | — |
| `eeb9c21d633627e52d87135cea8717c9e9ce88a3` | docs(42-02): complete Angular SSE crash prevention plan | unknown | unprocessed | — | — | — | — | — | — |
| `42e2267fbe897cf410796c527379e9da70bcd5f6` | docs(phase-42): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `bcde805f051c0f56c261c5023ca63f58ff0f77a8` | docs(43): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `67179ea469f7af57eb1313dfda8ca052b4d62387` | fix(43-02): fix AppComponent subscription leaks with takeUntil/destroy$ | unknown | unprocessed | — | — | — | — | — | — |
| `8271bd6a4b0a9abcb5b240935485ad20f8cda822` | fix(43-01): sanitize ConfirmModalService innerHTML inputs to prevent XSS | unknown | unprocessed | — | — | — | — | — | — |
| `7631bfba10ef76d193b09e692e0188b931e82404` | fix(43-03): fix AutoQueueService stale index and StreamDispatchService timer cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `b1b7ec92d25d3bcfbe34f5933d1c600994da2c32` | fix(43-02): fix SettingsPage and AutoQueuePage subscription leaks | unknown | unprocessed | — | — | — | — | — | — |
| `5664431f0cce74d0758bdaf88b917e9cf6d717db` | refactor(43-01): replace nested subscribe anti-pattern in RestService with pipe operators | unknown | unprocessed | — | — | — | — | — | — |
| `03ee46c14f9a0ee7761f236cbd22639a9a274f11` | refactor(43-03): consolidate file-options async pipe to single subscription | unknown | unprocessed | — | — | — | — | — | — |
| `ae642f635393805bc16d9e5e7d66cd09fe9da325` | docs(43-02): complete subscription leak fix plan — takeUntil/destroy$ in 3 components | unknown | unprocessed | — | — | — | — | — | — |
| `c3215d8f8f1eb4cfa55dd2be4bd6383cb950e4a7` | docs(43-01): complete XSS fix and RestService pipe refactor plan | unknown | unprocessed | — | — | — | — | — | — |
| `53f1748e368b49fabe806a0309c3da7e969f406a` | docs(43-03): complete stale index fix, timer cleanup, async pipe consolidation plan | unknown | unprocessed | — | — | — | — | — | — |
| `2d54902c1abf5b7e2818e8832663f2c36adf3070` | docs(phase-43): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `758ab1178f88ebceab419518aebd2442d97e6425` | docs(44): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `bb283e65205d262fbb214a6bb08286add158b70c` | fix(44-01): replace distutils.strtobool and fix type comparisons | unknown | unprocessed | — | — | — | — | — | — |
| `a50a6eca15fb183798faaeb2178a1a4a95789520` | fix(44-03): convert mutation endpoints to POST/DELETE; instance-level rate limiter; improve type annotations | unknown | unprocessed | — | — | — | — | — | — |
| `a53869eee59348cb727541099d399692f38a5df9` | fix(44-02): add sleep to busy-poll loop and log TIMEOUT in lftp.py | unknown | unprocessed | — | — | — | — | — | — |
| `9b4e3b6f78d0659fef305c8503ea513a56e46d24` | feat(44-05): document hardcoded test credentials as intentional test-only values | unknown | unprocessed | — | — | — | — | — | — |
| `714dcaf2c566cead4b9c751385fce993865e04ba` | feat(44-03): update Angular frontend to use POST/DELETE for mutation endpoints | unknown | unprocessed | — | — | — | — | — | — |
| `aa7593718bbbbf6a1115e098b6c6ab18884f6888` | docs(44-01): complete distutils replacement, isinstance() migration, ModelFile unfreeze() plan | unknown | unprocessed | — | — | — | — | — | — |
| `258d81d012c67f5613efbbd37dc8ef15f7909b92` | docs(44-03): complete HTTP method correctness and rate limiter isolation plan | unknown | unprocessed | — | — | — | — | — | — |
| `fcc46821baf9ba59d4eb14066987071c6d6abcf9` | docs(44-05): complete test credential documentation plan | unknown | unprocessed | — | — | — | — | — | — |
| `4b5394687c5491d332c97522906156ee67858fd0` | docs(44-02): complete pexpect arg list, TIMEOUT logging, busy-poll sleep plan | unknown | unprocessed | — | — | — | — | — | — |
| `65dc7fe41374470564f2952dd0182cdcc1fb019d` | fix(44-04): correct __downloaded_files type to BoundedOrderedSet; fix directory DOWNLOADED edge case | unknown | unprocessed | — | — | — | — | — | — |
| `48f9a68e3b8a5cf7d61dc7b1873d12971dc473fa` | refactor(44-04): consolidate import_status code paths into _set_import_status helper | unknown | unprocessed | — | — | — | — | — | — |
| `8f0b48b896a06c02a79943e8404739fbb03dfe34` | docs(44-04): complete type semantics fix and import status consolidation plan | unknown | unprocessed | — | — | — | — | — | — |
| `c22bfcb8729d9be219047399f3783376ccabd165` | docs(44-04): mark CODE-07, CODE-10, CODE-12 requirements complete | unknown | unprocessed | — | — | — | — | — | — |
| `4f182fc1d6a047cf4f9a0768b540beceab555248` | docs(phase-44): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `eebf86ad789b09319e072fb6d01bd49cab43f093` | docs(45): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `e3a074eea2557f15162bbe0538d6f7f86a363135` | docs(45-01): update CLAUDE.md version reference and API response codes | unknown | unprocessed | — | — | — | — | — | — |
| `fdb2b7f855949c49800ba541a81774cffdd8ece1` | feat(45-02): add keyboard focus trap and focus restoration to ConfirmModalService | unknown | unprocessed | — | — | — | — | — | — |
| `2fa98d1ba374796413b67ed6fef2d706651aa767` | test(45-02): add focus trap and ARIA attribute tests for ConfirmModalService | unknown | unprocessed | — | — | — | — | — | — |
| `3600465bb4bb79a47560bfafc91e46af3b40409c` | docs(45-01): complete documentation-accessibility plan | unknown | unprocessed | — | — | — | — | — | — |
| `16637d7320ea7e637102ac98958d902b4a65f87d` | docs(45-02): complete confirm modal focus trap plan | unknown | unprocessed | — | — | — | — | — | — |
| `d288d60cf0ec445bbd5ea468be78f293f7525050` | docs(45-02): add self-check and finalize SUMMARY.md | unknown | unprocessed | — | — | — | — | — | — |
| `801c437c969ccfb9d2c2e696107212c360d385d6` | docs(45-03): complete keyboard navigation and ARIA attributes plan | unknown | unprocessed | — | — | — | — | — | — |
| `a644ef80c53307b17b8daf61a42fb16543725b20` | docs(45-03): add self-check to SUMMARY.md | unknown | unprocessed | — | — | — | — | — | — |
| `4bdeb328cdc567802e6ebce799b9542ef2882dbc` | docs(phase-45): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `5204d3b4617e86a197841fa8084010c7abf9c1d4` | docs(v3.1): milestone audit — 44/44 requirements satisfied, 7/7 phases passed | unknown | unprocessed | — | — | — | — | — | — |
| `2e21c54a035b08d1c16dbff616c90e9417547256` | docs: fix stale SEC-04/SEC-05 traceability status (Pending→Complete) | unknown | unprocessed | — | — | — | — | — | — |
| `7dfff3ce6c91651a90f7cd1affc073d6e31f42f1` | docs(v3.1): add Phase 46 — 12 code review fixes from deep review | unknown | unprocessed | — | — | — | — | — | — |
| `8bacdcc20e226f692539302ebc521569d44aaa55` | docs(46-code-review-fixes): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `904837730abbbb54595191cfba85f38147537c56` | fix(46-01): redact webhook_secret in config API and use getMessage() for log redaction | unknown | unprocessed | — | — | — | — | — | — |
| `9365743d64bef6997f97e40b1cf8929ec6341334` | fix(46-03): full focus trap and XSS sanitization in confirm modal | unknown | unprocessed | — | — | — | — | — | — |
| `d9ed3f99d71c5b7a7e33abf7dfeb65872ce9bd04` | fix(46-04): clear _reconnectTimer before reassignment; fix unknown-event test (CR-06, CR-08) | unknown | unprocessed | — | — | — | — | — | — |
| `b2d5533452fe55edda941aa5efa7805a07f417b3` | docs(46-01): complete code-review-fixes plan 01 - webhook_secret redaction + getMessage() log fix | unknown | unprocessed | — | — | — | — | — | — |
| `b53fe7d5305ad51eecb99a8670779f9fe564b219` | fix(46-04): LogService injects LoggerService; RestService extracts error helpers (CR-09, CR-11) | unknown | unprocessed | — | — | — | — | — | — |
| `784e1ff023e73304c17a2587873aafff5383ca30` | fix(46-02): atomic extract() duplicate-check+insert and resilient worker finally | unknown | unprocessed | — | — | — | — | — | — |
| `a0dfd21050e11376a124f637034fd7b9b1ad3342` | docs(46-04): complete code review fixes plan 04 (CR-06, CR-08, CR-09, CR-11) | unknown | unprocessed | — | — | — | — | — | — |
| `8daf2218cb7707aec2702c9bd382bdc3de647763` | fix(46-02): rename unfreeze() to _unfreeze() and narrow _set_import_status except scope | unknown | unprocessed | — | — | — | — | — | — |
| `469c0758efdee2a210592f72a5e6111f6c553e24` | docs(46-02): complete code-review-fixes plan 02 summary and state update | unknown | unprocessed | — | — | — | — | — | — |
| `ab1759d45111b14dbec5d3602d64bf741287c1b8` | docs(phase-46): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `2eba5bfd9a7931540d5b2b8be52125013d504ff4` | chore: complete v3.1 Harden & Fix milestone | unknown | unprocessed | — | — | — | — | — | — |
| `1f0fa87fbafdf7d8b5f8bf72bdf13b5076df0489` | fix: update integration tests to use POST/DELETE for mutation endpoints | unknown | unprocessed | — | — | — | — | — | — |
| `52b72a6cc9147bedbdd9eb00f3d432a74870c544` | fix(e2e): add explicit z-index to confirmation modal to prevent sidebar overlap | unknown | unprocessed | — | — | — | — | — | — |
| `31889adf1469efadb80d1ec2aab3e82f39453b75` | fix(e2e): add explicit position:fixed to confirmation modal and backdrop | unknown | unprocessed | — | — | — | — | — | — |
| `0b26f0ad2df0f1b2aa0515f39f93a14ae3f2534b` | fix: prevent name column from being squeezed to zero on medium screens | unknown | unprocessed | — | — | — | — | — | — |
| `a48763dd0da2967518b69fa6a0a16d394c60623c` | fix: raise timestamp column breakpoint to 1200px to prevent name squeeze | unknown | unprocessed | — | — | — | — | — | — |
| `246c0639d4a7c7aeb0c1ddcbb4ef0947d32f6882` | fix: resolve CSP violations blocking GitHub API and inline event handlers | unknown | unprocessed | — | — | — | — | — | — |
| `8c4edb27e9ef60ced0dfe3c3dfc398c91fe1e03e` | fix: replace css-element-queries with native ResizeObserver to fix CSP violation | unknown | unprocessed | — | — | — | — | — | — |
| `6a8024da060a68fe4dd8b25c15a67750094916c2` | chore: add todo for e2e CSP violation detection | unknown | unprocessed | — | — | — | — | — | — |
| `0e6370eae00cd01353b014f9b2b34d3581103e37` | fix: add unsafe-inline to script-src CSP for inline event handler compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `a8561cdc318460de32de082e3cf33f6b6a0093cb` | chore: bump version to 3.1.2 | unknown | unprocessed | — | — | — | — | — | — |
