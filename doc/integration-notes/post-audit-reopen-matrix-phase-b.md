# Post-Audit Reopen Matrix Phase B

This document is the deduplicated Phase B execution matrix derived from the raw Phase A reopen matrix.

Phase B rules used here:
- preserve audit traceability from every `needs subject reopen` row back to one or more final execution tasks
- merge raw buckets only when the implementation scope is clearly shared and the merge does not hide materially different work
- keep broad modernization lanes separate from narrower compatibility or hardening tasks
- record dependencies so the final list can be executed one task at a time without losing ordering context

Inputs used:
- [post-audit-reopen-matrix-phase-a.md](/mnt/c/Git/seedsync/doc/integration-notes/post-audit-reopen-matrix-phase-a.md)
- [integration-tracker.md](/mnt/c/Git/seedsync/doc/integration-tracker.md)
- independent Phase B proposal from `explorer-fast`
- independent Phase B boundary review from `reviewer`

## Final Execution Tasks

### PB-001 - Python 3.11 baseline and Poetry metadata refresh
- Status: closed - the repo now targets Python `~3.11`, the Poetry metadata/lock were refreshed to Poetry `2.3.2`, and both docker build paths now use explicit `python:3.11-slim-bullseye` bases with main-session verification.
- RAF buckets: `RAF-001`, `RAF-002`
- Why this is one task: the lockfile and metadata work is part of the same deferred Python 3.11 baseline shift.
- Dependencies: none
- Commits:
  - `7adbd5f516d96483f6fdcb27a303f52be7677774`
  - `9a25b36d94236c339939b04fe96fb19eb193903c`
  - `ccc3f9c62fd34291d9a2bf5eaec484fce6353fdb`
  - `9d72249416c5616fb8f51e175d038fdb3e161201`
  - `6a4e77c35c112244ee00758a446a9c564e47c385`
  - `da6a4c6f74a33f9de0a11af16ce0ee453f302897`
  - `7909e693454c45cfe2119cdad190ca62ca4f096d`
  - `4f19ec6aef10b6f14eaf20db297a3cd2333001bd`
  - `1336c07e3046629a342c8a4d65c12d73211a50af`
  - `7042028e8a8c7a570656a98d53fa46af240f379b`
  - `461fa45757cd05e61be965c4d3413dfad6f656c8`

### PB-002 - Self-contained docker-image build path
- Status: closed - the docker-image build now compiles Angular assets and `scanfs` internally, and the final image no longer depends on separately published staged export images. Cross-architecture publish coverage remains in `PB-009`.
- RAF buckets: `RAF-003`
- Why this stays separate: it replaces the docker-image packaging path and should not be collapsed into release automation or broader container policy work.
- Dependencies: `PB-001`
- Commits:
  - `5ea6f8e4e246257055517817a847a64268e325fa`
  - `21fe17d6edc4c8d2295f2b28e3fe5d2232c1ef56`
  - `74f20dc737b4d1f79bb60978d012dc79bf8739c6`

### PB-003 - Angular build and test-stack compatibility
- Status: closed - the shared Angular build/test path now uses a modern Node 20 base, Node-20-compatible Sass/Karma tooling, and a current Chromium test image path while keeping Angular 4.x app/framework dependencies unchanged.
- RAF buckets: `RAF-004`, `RAF-006`
- Why this is one task: both buckets belong to the same narrower Node/Angular compatibility lane around native dependency builds, Karma, and Docker test-image behavior.
- Closure note: main-session Docker verification passed after pinning the resolved Node 20 toolchain in `src/angular/package-lock.json`, and the code changes stay scoped to compatibility rather than PB-004 framework modernization.
- Dependencies: `PB-001`
- Commits:
  - `a3a9bdf601c750a588016d9ce0ccd5ab1c6c4df7`
  - `fc9ca7be2ff2ce0efb0079772d643fe0a946cc86`
  - `0b26dcf6b8debd9857aae789cf8c3023e8393ea0`
  - `a5da13c80bf8d70ee8ab8ea88a0feec4fa20605d`
  - `b9cfb0fbf1c784569bf6ca4775ac437693e9a093`
  - `c112737b4e7aaad2604b55e41ae58cea5660f9b9`
  - `36123cda271c9d3dceb282058b505ff14e418c3d`
  - `9a9d10dc49dee0e4df8c48c1606403188a4e9a2d`
  - `96296468a27d31ec826670d4c26cf118d0e51bff`
  - `19bfb50ea97f60361522a860645dfd24fcdf8465`
  - `f3dfabd0205fb4ffef38acff19d79804d9ec7a34`
  - `562955a424912265a6e816f53a50ce07859931ca`
  - `6a695ac555d46fa6193909657c322a621fb2a7fe`
  - `d8fcb08fa46bd1acb9b9d3b4b4ba4f1e0e8d0029`
  - `ff2f07551cc37fdf3636332b10710e5dc1711747`
  - `1c8fa8a41d2b0a06e1a6b26553bd5b0d245714c6`
  - `85cb4e0b1a2c63fd20b9d1b6ec39ae23e8007ffd`
  - `79c98be844f6826247f02411e5b093d61257fae6`
  - `cee7689fe8d710c5139e7d5e87887845614111e2`
  - `18cdfffa7f615c63fd3c5f3c2d4a2d661b10d74d`
  - `068bb9dda0a014ce024b800b736da229e3d6c7fd`

### PB-004 - Angular framework and RxJS modernization chain
- Status: still needed - the broad Angular/RxJS modernization chain remains deferred.
- RAF buckets: `RAF-007`
- Why this stays separate: this is the broader Angular replatforming lane, not just compatibility cleanup.
- Dependencies: `PB-001`, `PB-003`
- Commits:
  - `1efe46634d1fb8ec16df7011fb2890eed328de84`
  - `cde6bbe9e525f2d70318c7b9363cb91bc3a427f2`
  - `805de78a559228c96eb7a9e5511502fcf4c3acbf`
  - `b7325d108fd052f60f8090b5f9a63587549e5198`
  - `9a974e1b99595f394babcc1ed636d9fe2237ddff`
  - `3130b2ceb28a2acd91f77b1c1624c98ee7ead83e`
  - `9dc14f99b0a2e588df204da2858e12da5b07dca5`
  - `56e9f092aea90ebec4aa8e317ef831438b2516b4`
  - `5fdae7852c37632f126b2f297fab6106f4b0f9c3`
  - `b91337a7246c93ea69c0f23727c846de05d5d3bb`
  - `7661944d5472c4ad559da0fe8f7bb8476766dca2`
  - `f2eb4ee4fcbdf640a85b34636fee37e96edb104d`
  - `ef7765619d5dd13a9c8e73149d51a51b6f366e02`
  - `4f19ec6aef10b6f14eaf20db297a3cd2333001bd`
  - `1336c07e3046629a342c8a4d65c12d73211a50af`

### PB-005 - PyInstaller GLIBC mitigation path
- Status: partially needed - deb-side mitigation appears partial, but the cross-path GLIBC policy remains unresolved.
- RAF buckets: `RAF-005`
- Why this stays separate: the actual artifact/runtime compatibility choice is still its own packaging decision.
- Dependencies: `PB-001`
- Commits:
  - `996ae6a551ae28b302d49444af69a1258e1eac0d`
  - `950d9fc79d5f06a2cc0d7b88aa89e10fbf869103`
  - `c94d626afe857f6072ba6eb9a7fc891d993b5485`
  - `04b1f81ec0ec565cc8d773d57e9f4c05bfa68d64`
  - `a8a6ebaa605aba2a29098cf278bd052f3e3118a8`
  - `31b68ec981dc800b0c85abd7a053c39645c86103`

### PB-006 - Stage/deb systemd container compatibility
- Status: still needed - stage/deb systemd+cgroup container compatibility is still missing.
- RAF buckets: `RAF-008`
- Why this stays separate: this is the systemd/cgroups/container-runtime lane for the stage/deb path.
- Dependencies: `PB-002`
- Commits:
  - `7e7289e4ee2ab15364b82c01840e6553987dcc03`
  - `87d2d141f9683493406e854bc6739590a5acbaad`
  - `99e6d9e361bb52092e2462ba2705226101c01bc7`
  - `f65a996907872d846b1da1d0269839f819c94bbb`
  - `5e2cc8e63bb124041e4199b3f8e5d885eecac705`
  - `f25353f0dfd1ba8054597cfce221080cb7350692`
  - `984b8a1525ef80b109663a8b75f1f7e2d0454b83`
  - `558411badf857516199529284583d869ca9da1ac`
  - `f278379b6a69bf70d2031731fe177d5fdfff443d`
  - `e5416c59858c986bb2b81ae6035e337fbf95b78c`
  - `49e8d61c652e557d1c2b0324c8b01b24ae885f40`

### PB-007 - Deb E2E GLIBC floor and Ubuntu policy
- Status: closed - active workflow, Makefile policy messaging, and developer docs now align on the Ubuntu 20.04+ / GLIBC 2.29+ deb E2E floor.
- RAF buckets: `RAF-009`
- Why this stays separate: this is the policy/docs/workflow reflection of the GLIBC floor, and should follow the actual mitigation choice.
- Dependencies: `PB-005`
- Commits:
  - `2ae51736a47e993de71e8660fe7e39b5b2e2c78a`
  - `4cbdaa549f20e6bd572a8ddc7da93cb5870c59d9`
  - `49e8d61c652e557d1c2b0324c8b01b24ae885f40`

### PB-008 - Legacy E2E dashboard and AutoQueue stability
- Status: still needed - the E2E dashboard/AutoQueue reliability slice is still missing.
- RAF buckets: `RAF-010`
- Why this stays separate: these are app-specific E2E reliability fixes on the current legacy suite.
- Dependencies: `PB-003`, `PB-006`
- Commits:
  - `8fac10e480cbb283a3be3f1e452f8f776a46c69f`
  - `b4c393ef9f3b78c82e1246b79d0d98ae0070531a`
  - `50a8ce3af858f5daf16421c4b55f1445fe76b532`

### PB-009 - ARM and cross-architecture CI/E2E support
- Status: partially needed - partial cross-arch groundwork exists, but platform wiring and the ARM64 lane are incomplete.
- RAF buckets: `RAF-011`
- Why this stays separate: architecture support can move on a different schedule than GLIBC policy or publish automation.
- Dependencies: `PB-006`
- Commits:
  - `3f7af5c79aaff746c595b37097e84cbbd61e84e9`
  - `c73205b19c707ac74da18513a0e56ec0d5fcbebb`
  - `b4fb9465ab46a0cb21c7ba2f1bc17b0b4c7f98f1`
  - `0ac447026442c5f181ea2c845025f1f0e4c6bff3`
  - `9392653b597c519b9b825ec508ef9464e025999a`
  - `f01806802f833451d072042958ca7e3c535b67e9`
  - `49e8d61c652e557d1c2b0324c8b01b24ae885f40`
  - `5fdae7852c37632f126b2f297fab6106f4b0f9c3`

### PB-010 - Release and docker publish workflow modernization
- Status: partially needed - release/publish workflow exists, but deprecated release actions and docker-publish modernization remain.
- RAF buckets: `RAF-017`
- Why this stays separate: the remaining work is publication automation, not packaging-path replacement.
- Dependencies: `PB-002`, `PB-009`
- Commits:
  - `416713e8cddf0c937e673bfc75c9022cc9b1d247`
  - `bbf1310881fd5181d68da2614b9b4f02378365c2`
  - `fd9c25fd318de6181cf8e5f53f1c806868ec28f9`
  - `f01806802f833451d072042958ca7e3c535b67e9`

### PB-011 - Path-pair settings, API, dashboard, and dependent tests
- Status: closed - explicit local proof is `ac8b6447`, `f4a62a75`, and `754b6a50`.
- RAF buckets: `RAF-022`, `RAF-023`, `RAF-024`, `RAF-025`, `RAF-026`
- Why this is one task: the tracker already reopens these as one path-pair feature family spanning CRUD, stats UI, validation, schema, and tests.
- Dependencies: none on other Phase B tasks
- Commits:
  - `d1436386eed1ff80876cff7731c00bb5b308a54d`
  - `778d1d8c9d961b03f6a47067f3d7d9a6de2ec653`
  - `64afa027f0b42fcf71945312d306d68708856e3e`
  - `bc8348c85b89094af4e761fbd90192d92f1ebf80`
  - `a33981b5574494390907c48694937a67679c4c44`
  - `58ead0584c79a580046a9dda6acded851bc2107c`
  - `88ffbd0000d50173834647a952fa8d6786914a53`
  - `15a9918ec8254fcd37ff691c0d7fd5c6fd650fa5`

### PB-012 - WebApp `_stop_flag` compatibility
- Status: conditional future follow-up only - there is still no active implementation gap evidenced after the PB-001 Bottle `0.12.25` refresh, so revisit this only if a real `_stop_flag` regression appears.
- RAF buckets: `RAF-012`
- Why this stays separate: it is a narrow Bottle/WebApp runtime compatibility slice.
- Dependencies: none
- Commits:
  - `0cb32280056fcfdad740dc34d8e505e961dc4740`
  - `a1deb233c837c7172cd391924ba334d39ad42ad2`
  - `068bb9dda0a014ce024b800b736da229e3d6c7fd`
  - `49e8d61c652e557d1c2b0324c8b01b24ae885f40`

### PB-013 - Test reliability skips and common-module coverage
- Status: closed - the missing common-module tests have landed locally, and the upstream skip decorators were intentionally not adopted because the targeted tests pass in the supported Docker Python harness.
- RAF buckets: `RAF-013`, `RAF-033`
- Why this is one task: both buckets are test-only reopen work rather than runtime behavior, and they can be executed as one verification-focused lane.
- Dependencies: `PB-008` is a helpful stability baseline
- Commits:
  - `84f64738a1589f4939afb022cd1b456d7063d692`
  - `20a3e0134ac69929cda17d6748450a6c1eef8657`
  - `068bb9dda0a014ce024b800b736da229e3d6c7fd`
  - `537456c7fa27e00dd6fee6d0454aa3d910814f54`
  - `ea8a655312bfc7df4f3977b3a95dbd7d09fad137`
  - `91fa01014f157e3a14b63dd6c63dafc491289417`
  - `5e39fdeb2bdbb5f2339293020220a259d17a40ae`
  - `23b7052e0e5153452202b1b3c954ac18aeb45718`
  - `2d866f1d12d476236e332c46e3350964b4d879cf`

### PB-014 - Backend bounded-state and memory-monitoring hardening
- Status: partially needed - bounded stream queues plus safe downloaded-file pruning/cache invalidation have landed, but the attempted persisted downloaded-file cap was rolled back as unsafe and broader bounded-state and memory-monitor work still remain.
- RAF buckets: `RAF-018`
- Why this stays separate: it is broader than the narrower status-lock or endpoint hardening slices.
- Dependencies: none
- Commits:
  - `c1a4a2f88c467dc93b2e6e98a1b22c273b55f87a`
  - `b632b05799921219fb613a83b66d7b7fd8a03e8f`
  - `c52554b3863cf6e90b34a127061f8085c95944ca`

### PB-015 - Status/log lock-safety hardening
- Status: closed - exception-safe status/listener and cached-log locking has landed locally with focused unit coverage.
- RAF buckets: `RAF-019`
- Why this stays separate: part of the wider locking work already landed locally, so the remaining delta should stay isolated.
- Dependencies: none
- Commits:
  - `4f58c8f172e90cd64ed6ab1051353a3d914f0f50`
  - `7a88f02b0f7eb83786e3d4e8dc70dbf5b4780d4d`

### PB-016 - Angular subscription cleanup leftovers
- Status: closed - the files/options lifecycle now stores and tears down its subscriptions on destroy, so the remaining files-page cleanup gap is closed.
- RAF buckets: `RAF-020`
- Why this stays separate: this may still need an internal split later between shared-service cleanup and files-page cleanup, but it should not be merged with unrelated frontend modernization.
- Dependencies: none
- Commits:
  - `48ff14b526b248d686b0459e0979ce6944c05385`
  - `b32eb93c83ad85cfa9aaefb8ec3242815d84223a`
  - `aaeddbf8f3448cc93d6ce980ce4617dcbbb9e3ae`
  - `812f8a9049e8bfee83901464cdd346f5e6a76d57`
  - `52402b269d790c1c8d8599143ced4af818c8f499`

### PB-017 - Bulk endpoint concurrency and hardening
- Status: closed - bulk requests now enforce payload bounds, reject overlapping in-flight bulk requests, and preserve the bounded callback wait and timeout-summary behavior across retries.
- RAF buckets: `RAF-021`
- Why this stays separate: the remaining work is a concentrated controller/bulk-endpoint hardening lane that may still split internally during implementation.
- Dependencies: none
- Commits:
  - `5c7bfc853588aa5885b4bca2e68fe0c102eadfbd`
  - `a4cbdc6bc850eb5de08380d99e2ed9b67d409a6b`
  - `7297af2890b4ee0ffc4f637e08f50ae9e28f8462`
  - `85cbe899c72cb8e98de8d545e5b1c4aa7d3c7b22`
  - `52402b269d790c1c8d8599143ced4af818c8f499`

### PB-018 - SSE server transport reliability
- Status: closed - the server stream now registers a heartbeat handler and uses one-event-per-handler fair yielding plus short per-event pacing so busy producers do not monopolize or flood the multiplexed SSE stream.
- RAF buckets: `RAF-027`, `RAF-028`
- Why this is one task: heartbeat/reconnect and fair-yield transport behavior are one server-side SSE execution unit.
- Dependencies: `PB-015`
- Commits:
  - `12f2a68792419bf2b781576b38115567096aee08`
  - `cd8c770bdcf9d4684404eda316ce8c87857eaade`
  - `71e18003aed594606732f8819b0e30ad54b76c26`
  - `284d843d842d66da5e96e4765565494642be72b0`
  - `33c01698b33448942ced2dea299de3362c1ca21e`
  - `33cc1cf0480adaaa2c50cf1cf6c0f7ffb6be71e8`

### PB-019 - SSE client dispatch hardening
- Status: closed - the client-side SSE registry now guards orphaned events, avoids stacked reconnect attempts, and the remaining status/model payload parsing gaps are handled defensively.
- RAF buckets: `RAF-029`
- Why this stays separate: this is the client-side registry/timer/payload guard lane, not the server transport lane.
- Dependencies: `PB-018`
- Commits:
  - `d9ed3f99d71c5b7a7e33abf7dfeb65872ce9bd04`
  - `e736b6932d22afaf8a05a1e6e5ec7906dd1b6818`
  - `a7122fc164c1c64bab0bc3049007eac3d89e2750`
  - `7631bfba10ef76d193b09e692e0188b931e82404`

### PB-020 - Single-action endpoint timeout guard
- Status: closed - single-action web handlers now apply the shared 30 second timeout and return `504` when the callback never completes.
- RAF buckets: `RAF-030`
- Why this stays separate: it is the narrow single-file callback-wait timeout lane and should remain directly traceable.
- Dependencies: `PB-017`
- Commits:
  - `05a00038c6e383acb842b9fef3ad0113287bbe4c`
  - `f288e3abedcbc92c1723934fc6f1d41d0d589f20`

### PB-021 - CSP allowlist for GitHub API version checks
- Status: closed - explicit local proof is `cc62a633`.
- RAF buckets: `RAF-031`
- Why this stays separate: it is a narrow security/configuration adjustment.
- Dependencies: none
- Commits:
  - `246c0639d4a7c7aeb0c1ddcbb4ef0947d32f6882`

### PB-022 - LFTP timeout and terminate-loop hardening
- Status: closed - `AppProcess.terminate()` now uses a short sleep-based poll before forced termination, and both `lftp.py` `pexpect.TIMEOUT` handlers log at warning level with focused unit coverage.
- RAF buckets: `RAF-032`
- Why this stays separate: it is a focused process-control/observability fix.
- Closure note: local proof exists in `src/python/tests/unittests/test_common/test_app_process.py` and `src/python/tests/unittests/test_lftp/test_lftp.py`.
- Dependencies: none
- Commits:
  - `a53869eee59348cb727541099d399692f38a5df9`

### PB-023 - Files-page dropdown and action-visibility improvements
- Status: closed - explicit local proof is `76ac3f04`.
- RAF buckets: `RAF-034`, `RAF-035`
- Why this is one task: both buckets belong to the same files-page interaction and visibility lane.
- Dependencies: none
- Commits:
  - `da35b7eb9332d8c2d8fd7fd413140908fa37b1fd`
  - `b3011297982218b6e6f628fe3b3601d6a8e9f582`
  - `778ec7027ecfd0b3e8a2d7b768e5a39bb606f89b`
  - `6ce8086328384f7bb4eb7dff21fe1c2d171152e7`
  - `ebe0cd69265f867e310c308f8e75b3b8b7972194`

### PB-024 - AutoQueue/files disabled-state hardening
- Status: closed - explicit local proof is `8764202f`.
- RAF buckets: `RAF-036`
- Why this stays separate: it is the smaller defense-in-depth tail of the files/AutoQueue UI lane.
- Dependencies: `PB-023`
- Commits:
  - `15aee39be6869941200bbeee04c0e4cd35d5b31f`

### PB-025 - Linux `st_ctime` fallback for created timestamps
- Status: closed - explicit local proof is `c9027296`.
- RAF buckets: `RAF-037`
- Why this stays separate: it is one small scanner compatibility task with its own test coverage.
- Dependencies: none
- Commits:
  - `569622eb3b39d6ffe3a9e27cbc5dc5143fe8c96a`
  - `7d85e6aed9233e07d92c255f075e7533007a5686`

### PB-026 - Packaging-side SSH host-key hardening
- Status: closed - explicit local proof is `6acac2db`.
- RAF buckets: `RAF-038`
- Why this stays separate: it is a packaging/runtime security alignment change, not a general SSH-runtime rewrite.
- Dependencies: `PB-002`
- Commits:
  - `e34ba5e11f298509463d5697835f71dfacc29776`

### PB-027 - `DELETE_LOCAL` fallback to staging path
- Status: closed - explicit local proof is `7c19fdf5`.
- RAF buckets: `RAF-039`
- Why this stays separate: it is a concrete runtime-correctness fix in the transfer/delete path.
- Dependencies: none
- Commits:
  - `c300b72f808772b00cc977ccceaa23f3c373ce33`

## Phase B Notes

- This is the execution queue derived from Phase A, not the implementation log.
- Narrow safe merges were taken where both subagent review and tracker context supported them:
  - `RAF-001 + RAF-002`
  - `RAF-004 + RAF-006`
  - `RAF-027 + RAF-028`
  - `RAF-022` through `RAF-026` as one explicit path-pair feature family
  - `RAF-034 + RAF-035` as one files-page interaction lane
  - `RAF-013 + RAF-033` as one test-only lane
- Broad or partially covered lanes were intentionally kept separate to preserve traceability, especially `RAF-007`, `RAF-005`, `RAF-008`, `RAF-009`, `RAF-011`, `RAF-018`, `RAF-019`, `RAF-020`, `RAF-021`, and `RAF-029`.
