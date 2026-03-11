# Post-Audit Reopen Matrix Phase A

This document is the raw Phase A matrix requested after the post-integration audit closed.

Phase A rules used here:
- start from every audit ledger row whose final disposition is `needs subject reopen`
- evaluate the concrete missing feature or missing implementation slice named by that row
- create a raw feature bucket when that missing slice is not already represented
- append the commit hash to every raw feature bucket that the row materially supports
- allow overlap when one audit row or merge commit clearly points at more than one missing feature
- prefer traceability over aggressive deduplication; Phase B can merge or split buckets later

Audit coverage baseline:
- Total reopen rows inventoried from the audit ledger: `136`
- Source files covered: `rapidcopy-004`, `rapidcopy-007`, `thejuran-001`, `thejuran-002`, `thejuran-003`, `thejuran-004`, `thejuran-005`, `thejuran-006`, `thejuran-007`, `thejuran-008`, `thejuran-010`, `thejuran-011`, `thejuran-012`, `thejuran-014`, `thejuran-018`, `thejuran-020`, `thejuran-021`

## Raw Feature Buckets

### RAF-001 - Python 3.11 baseline and dependency refresh
- Subjects: `3`, `4`, `5`
- Missing slice: move the deferred Python baseline from the current Python 3.8-era build and dependency stack to the reviewed Python 3.11-compatible packaging/runtime baseline.
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

### RAF-002 - Poetry lockfile and metadata modernization
- Subjects: `3`
- Missing slice: adopt the deferred Poetry metadata and lockfile structure changes that were reviewed as part of the Python 3.11 toolchain update.
- Commits:
  - `7042028e8a8c7a570656a98d53fa46af240f379b`
  - `461fa45757cd05e61be965c4d3413dfad6f656c8`

### RAF-003 - Self-contained docker-image build path
- Subjects: `4`
- Missing slice: replace the current staging-registry-dependent docker-image packaging path with the reviewed self-contained build flow.
- Commits:
  - `5ea6f8e4e246257055517817a847a64268e325fa`
  - `21fe17d6edc4c8d2295f2b28e3fe5d2232c1ef56`
  - `74f20dc737b4d1f79bb60978d012dc79bf8739c6`

### RAF-004 - Angular build-image compatibility for native dependencies
- Subjects: `3`, `4`
- Missing slice: carry the reviewed Angular build-stage compatibility work for npm install behavior, `node-sass`, Python/build-essential tooling, and native-build warning suppression.
- Commits:
  - `a3a9bdf601c750a588016d9ce0ccd5ab1c6c4df7`
  - `fc9ca7be2ff2ce0efb0079772d643fe0a946cc86`
  - `0b26dcf6b8debd9857aae789cf8c3023e8393ea0`
  - `a5da13c80bf8d70ee8ab8ea88a0feec4fa20605d`
  - `b9cfb0fbf1c784569bf6ca4775ac437693e9a093`
  - `c112737b4e7aaad2604b55e41ae58cea5660f9b9`

### RAF-005 - PyInstaller GLIBC mitigation path
- Subjects: `4`, `5`
- Missing slice: land a consistent GLIBC mitigation strategy for the deb and docker-image build paths, including the reviewed Ubuntu 20.04 and manylinux alternatives that are still absent locally.
- Commits:
  - `996ae6a551ae28b302d49444af69a1258e1eac0d`
  - `950d9fc79d5f06a2cc0d7b88aa89e10fbf869103`
  - `c94d626afe857f6072ba6eb9a7fc891d993b5485`
  - `04b1f81ec0ec565cc8d773d57e9f4c05bfa68d64`
  - `a8a6ebaa605aba2a29098cf278bd052f3e3118a8`
  - `31b68ec981dc800b0c85abd7a053c39645c86103`

### RAF-006 - Angular/Karma/Node test-tooling modernization
- Subjects: `2`, `3`, `5`
- Missing slice: carry the deferred Angular test-stack modernization needed for newer Node/Karma compatibility, including reporter, dependency, and Docker test-image cleanup.
- Commits:
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
  - `068bb9dda0a014ce024b800b736da229e3d6c7fd`

### RAF-007 - Angular framework and RxJS modernization chain
- Subjects: `2`, `3`, `5`
- Missing slice: preserve the raw reviewed Angular framework-upgrade lane as a still-missing feature family instead of scattering it across isolated package bumps.
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

### RAF-008 - Stage/deb systemd container compatibility
- Subjects: `2`, `4`, `5`
- Missing slice: bring over the reviewed stage/deb systemd-in-container fixes around compose format, `cgroupns`, init path, `CMD`, entrypoint handoff, startup ordering, and container masking.
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

### RAF-009 - Deb E2E GLIBC floor and Ubuntu matrix narrowing
- Subjects: `2`, `4`, `5`
- Missing slice: reflect the reviewed GLIBC 2.29+/Ubuntu 20.04+ deb E2E compatibility policy in workflow, Makefile, and docs.
- Commits:
  - `2ae51736a47e993de71e8660fe7e39b5b2e2c78a`
  - `4cbdaa549f20e6bd572a8ddc7da93cb5870c59d9`
  - `49e8d61c652e557d1c2b0324c8b01b24ae885f40`

### RAF-010 - E2E dashboard and AutoQueue stability fixes
- Subjects: `2`
- Missing slice: bring over the reviewed E2E wait/cleanup, setup timeout, and text-normalization fixes that keep the legacy end-to-end suite stable.
- Commits:
  - `8fac10e480cbb283a3be3f1e452f8f776a46c69f`
  - `b4c393ef9f3b78c82e1246b79d0d98ae0070531a`
  - `50a8ce3af858f5daf16421c4b55f1445fe76b532`

### RAF-011 - ARM and cross-architecture CI/E2E support
- Subjects: `2`, `4`, `5`
- Missing slice: keep the raw missing ARM and cross-platform container support together, including platform pinning, native runners, and deb/docker test-lane decisions.
- Commits:
  - `3f7af5c79aaff746c595b37097e84cbbd61e84e9`
  - `c73205b19c707ac74da18513a0e56ec0d5fcbebb`
  - `b4fb9465ab46a0cb21c7ba2f1bc17b0b4c7f98f1`
  - `0ac447026442c5f181ea2c845025f1f0e4c6bff3`
  - `9392653b597c519b9b825ec508ef9464e025999a`
  - `f01806802f833451d072042958ca7e3c535b67e9`
  - `49e8d61c652e557d1c2b0324c8b01b24ae885f40`
  - `5fdae7852c37632f126b2f297fab6106f4b0f9c3`

### RAF-012 - Bottle/WebApp `_stop_flag` attribute compatibility
- Subjects: `11`, `13`
- Missing slice: carry the reviewed Bottle/WebApp private-attribute compatibility fix that the local tree still lacks.
- Commits:
  - `0cb32280056fcfdad740dc34d8e505e961dc4740`
  - `a1deb233c837c7172cd391924ba334d39ad42ad2`
  - `068bb9dda0a014ce024b800b736da229e3d6c7fd`
  - `49e8d61c652e557d1c2b0324c8b01b24ae885f40`

### RAF-013 - Intentional test skips for unstable extract and streaming coverage
- Subjects: `2`, `17`
- Missing slice: preserve the reviewed reliability-oriented skip/guard decisions for tests that still fail or time out on the current local stack.
- Commits:
  - `84f64738a1589f4939afb022cd1b456d7063d692`
  - `20a3e0134ac69929cda17d6748450a6c1eef8657`
  - `068bb9dda0a014ce024b800b736da229e3d6c7fd`

### RAF-014 - Build toolchain packages in Docker build images
- Subjects: `3`, `4`, `5`
- Missing slice: add the reviewed `build-essential`-style tooling expected by the deferred packaging paths.
- Commits:
  - `e15601a82c5613cda1bbf8813cda0bff430a542b`
  - `3eefa631509926d4999f37872f418043e4baa37d`

### RAF-015 - CI/build warning and vulnerability cleanup bundle
- Subjects: `2`, `3`, `4`, `5`
- Missing slice: keep the reviewed warning/security cleanup bundle visible as its own follow-up instead of hiding it inside unrelated modernization.
- Commits:
  - `f56d78ac98272b8cee4e47a09b1fea79d0e6a3af`

### RAF-016 - OpenSSH 9 test compatibility
- Subjects: `2`, `13`
- Missing slice: complete the reviewed SSH test hardening for newer OpenSSH versions.
- Commits:
  - `d11b3f3788f78fbda60b03c0ec419058561f6788`
  - `77df923608aa0a8cd20f1838eccb43233f8e4223`

### RAF-017 - Release and publish workflow modernization
- Subjects: `1`, `2`, `4`, `5`
- Missing slice: keep release creation and docker publish workflow updates together as the raw missing publication automation family.
- Commits:
  - `416713e8cddf0c937e673bfc75c9022cc9b1d247`
  - `bbf1310881fd5181d68da2614b9b4f02378365c2`
  - `fd9c25fd318de6181cf8e5f53f1c806868ec28f9`
  - `f01806802f833451d072042958ca7e3c535b67e9`

### RAF-018 - Backend bounded collections and memory monitoring
- Subjects: `11`, `14`, `18`
- Missing slice: land the reviewed backend memory-growth protections around bounded tracking, monitoring, and cache/queue cleanup.
- Commits:
  - `c1a4a2f88c467dc93b2e6e98a1b22c273b55f87a`
  - `b632b05799921219fb613a83b66d7b7fd8a03e8f`
  - `c52554b3863cf6e90b34a127061f8085c95944ca`

### RAF-019 - Status/log lock-safety hardening
- Subjects: `11`, `18`
- Missing slice: apply the reviewed thread-safety improvements around shared status copies and log streaming locks.
- Commits:
  - `4f58c8f172e90cd64ed6ab1051353a3d914f0f50`
  - `7a88f02b0f7eb83786e3d4e8dc70dbf5b4780d4d`

### RAF-020 - Angular subscription cleanup across shared and file services
- Subjects: `3`, `12`, `19`
- Missing slice: carry the reviewed `OnDestroy` / `takeUntil` cleanup path for Angular services and file-options subscriptions.
- Commits:
  - `48ff14b526b248d686b0459e0979ce6944c05385`
  - `b32eb93c83ad85cfa9aaefb8ec3242815d84223a`
  - `aaeddbf8f3448cc93d6ce980ce4617dcbbb9e3ae`
  - `812f8a9049e8bfee83901464cdd346f5e6a76d57`

### RAF-021 - Bulk endpoint concurrency and hardening
- Subjects: `6`, `11`, `12`
- Missing slice: preserve the raw backend bulk-endpoint follow-up as one feature family covering parallelism, timeout handling, validation, and request hardening.
- Commits:
  - `5c7bfc853588aa5885b4bca2e68fe0c102eadfbd`
  - `a4cbdc6bc850eb5de08380d99e2ed9b67d409a6b`
  - `7297af2890b4ee0ffc4f637e08f50ae9e28f8462`

### RAF-022 - Path-pair CRUD and API surface
- Subjects: `10`, `11`
- Missing slice: add the still-missing path-pair CRUD/API endpoints and Angular management surface.
- Commits:
  - `d1436386eed1ff80876cff7731c00bb5b308a54d`

### RAF-023 - Path-pair dashboard statistics UI
- Subjects: `10`, `11`
- Missing slice: add the reviewed path-pair dashboard stats component and associated wiring.
- Commits:
  - `778d1d8c9d961b03f6a47067f3d7d9a6de2ec653`

### RAF-024 - Path-pair feature test coverage
- Subjects: `2`, `10`, `11`
- Missing slice: keep the dependent path-pair E2E and unit coverage attached to the still-missing multi-path feature family.
- Commits:
  - `64afa027f0b42fcf71945312d306d68708856e3e`
  - `bc8348c85b89094af4e761fbd90192d92f1ebf80`

### RAF-025 - Path-pair-aware settings validation and Docker path warnings
- Subjects: `8`, `10`
- Missing slice: carry the reviewed settings/backend validation and Docker path-warning work that becomes relevant once path-pair-aware settings are completed.
- Commits:
  - `a33981b5574494390907c48694937a67679c4c44`

### RAF-026 - Settings config schema and null-handling robustness
- Subjects: `8`, `10`
- Missing slice: add the reviewed Angular config-schema alignment, checkbox coercion, helper accessors, and null-safe settings access.
- Commits:
  - `58ead0584c79a580046a9dda6acded851bc2107c`
  - `88ffbd0000d50173834647a952fa8d6786914a53`
  - `15a9918ec8254fcd37ff691c0d7fd5c6fd650fa5`

### RAF-027 - SSE idle reconnect and heartbeat infrastructure
- Subjects: `9`, `11`
- Missing slice: add the reviewed heartbeat stream and idle reconnect behavior that keeps SSE connections alive and recoverable.
- Commits:
  - `12f2a68792419bf2b781576b38115567096aee08`
  - `cd8c770bdcf9d4684404eda316ce8c87857eaade`
  - `71e18003aed594606732f8819b0e30ad54b76c26`

### RAF-028 - SSE fair scheduling and yield pacing
- Subjects: `9`, `11`
- Missing slice: apply the reviewed server-side fair-interleaving and yield pacing changes so one active producer cannot monopolize the stream.
- Commits:
  - `284d843d842d66da5e96e4765565494642be72b0`
  - `33c01698b33448942ced2dea299de3362c1ca21e`
  - `33cc1cf0480adaaa2c50cf1cf6c0f7ffb6be71e8`
  - `71e18003aed594606732f8819b0e30ad54b76c26`

### RAF-029 - SSE client dispatch hardening
- Subjects: `11`
- Missing slice: harden the client-side SSE registry against unknown events, stacked reconnect timers, and malformed payloads.
- Commits:
  - `d9ed3f99d71c5b7a7e33abf7dfeb65872ce9bd04`
  - `e736b6932d22afaf8a05a1e6e5ec7906dd1b6818`
  - `a7122fc164c1c64bab0bc3049007eac3d89e2750`
  - `7631bfba10ef76d193b09e692e0188b931e82404`

### RAF-030 - Individual action endpoint timeout guard
- Subjects: `11`
- Missing slice: add the reviewed bounded wait timeout for single-item command endpoints.
- Commits:
  - `05a00038c6e383acb842b9fef3ad0113287bbe4c`

### RAF-031 - CSP allowlist for GitHub API version checks
- Subjects: `6`
- Missing slice: align the CSP `connect-src` policy with the existing GitHub version-check call path.
- Commits:
  - `246c0639d4a7c7aeb0c1ddcbb4ef0947d32f6882`

### RAF-032 - LFTP timeout and terminate-loop hardening
- Subjects: `14`
- Missing slice: bring over the reviewed app-process sleep and `pexpect.TIMEOUT` observability improvements.
- Commits:
  - `a53869eee59348cb727541099d399692f38a5df9`

### RAF-033 - Common module unit-test coverage
- Subjects: `2`
- Missing slice: add the still-missing common-module tests and keep the planning rows tied to that concrete coverage gap.
- Commits:
  - `537456c7fa27e00dd6fee6d0454aa3d910814f54`
  - `ea8a655312bfc7df4f3977b3a95dbd7d09fad137`
  - `91fa01014f157e3a14b63dd6c63dafc491289417`
  - `5e39fdeb2bdbb5f2339293020220a259d17a40ae`
  - `23b7052e0e5153452202b1b3c954ac18aeb45718`
  - `2d866f1d12d476236e332c46e3350964b4d879cf`

### RAF-034 - Files-page dropdown migration
- Subjects: `12`
- Missing slice: carry the reviewed file-options dropdown migration away from the legacy placeholder-based implementation.
- Commits:
  - `da35b7eb9332d8c2d8fd7fd413140908fa37b1fd`
  - `b3011297982218b6e6f628fe3b3601d6a8e9f582`
  - `778ec7027ecfd0b3e8a2d7b768e5a39bb606f89b`

### RAF-035 - Files-page action-bar visibility while scrolling
- Subjects: `12`
- Missing slice: add the reviewed sticky and internal-scroll behavior that keeps bulk actions visible during file-list scrolling.
- Commits:
  - `6ce8086328384f7bb4eb7dff21fe1c2d171152e7`
  - `ebe0cd69265f867e310c308f8e75b3b8b7972194`

### RAF-036 - AutoQueue and files-page disabled-state hardening
- Subjects: `12`, `16`
- Missing slice: apply the remaining reviewed guard-return and disabled-state hardening in the AutoQueue/files UI path.
- Commits:
  - `15aee39be6869941200bbeee04c0e4cd35d5b31f`

### RAF-037 - Linux `st_ctime` fallback for created timestamps
- Subjects: `15`
- Missing slice: use `st_ctime` when `st_birthtime` is unavailable and keep the corresponding integration coverage with it.
- Commits:
  - `569622eb3b39d6ffe3a9e27cbc5dc5143fe8c96a`
  - `7d85e6aed9233e07d92c255f075e7533007a5686`

### RAF-038 - Packaging-side SSH host-key hardening
- Subjects: `4`, `13`
- Missing slice: remove the insecure docker-image SSH defaults so packaging matches the already-hardened runtime connection path.
- Commits:
  - `e34ba5e11f298509463d5697835f71dfacc29776`

### RAF-039 - `DELETE_LOCAL` fallback to staging path
- Subjects: `20`
- Missing slice: when a transfer still lives in the staging path, route `DELETE_LOCAL` to the staging file instead of only the final local path.
- Commits:
  - `c300b72f808772b00cc977ccceaa23f3c373ce33`

## Phase A Notes

- This is intentionally a raw matrix, not the final deduplicated implementation plan.
- Merge commits are kept in the same buckets as their underlying missing feature slices so that audit provenance is not lost.
- Some commits appear in more than one raw feature bucket because the audit row itself described multiple still-missing slices.
- Phase B should focus on merging buckets that are truly one implementation unit and splitting buckets that still hide distinct deliverables.
