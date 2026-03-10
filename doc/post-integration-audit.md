# Post-Integration Audit Ledger

Use this ledger after all integration subjects are complete.

Document roles:
- `AGENTS.md` is the canonical audit rulebook and exit-criteria source
- this file is the active per-commit audit ledger
- `doc/integration-tracker.md` records reopened subjects, resulting local integration work, and summary audit state

Purpose:
- mark off each upstream commit as it is checked against current local state
- make the final fork-by-fork coverage pass resumable without relying on memory
- capture any missed work cleanly enough to reopen the right subject

Recommended process:
1. pick one fork
2. inventory every fork-local upstream commit in this ledger before making any dispositions
3. walk its remaining commit history oldest to newest
4. compare each inventoried commit in the recorded range against the pinned local audit base
5. record the triage and final disposition here
6. reopen the related subject or create a new integration task if the audit finds missed work
7. after each audit run, update the workflow prompt/templates if the run exposed a repeatable lesson or failure mode, and record that learning here or in `doc/integration-tracker.md` before continuing
8. after each audit run, note whether `explorer-fast` showed good judgment on the commit being reviewed, including whether it over-escalated, under-escalated, or misclassified the likely disposition
9. if working inside a planned autonomous audit wave, do not stop at a natural checkpoint just to summarize progress; keep going until the wave is finished or a real reviewer/maintainer exception interrupts it
10. when the planned batch is finished, stop there: summarize the full batch result, count how many reviewers were spawned, note any workflow improvements, and wait for maintainer confirmation of the next batch and its size before continuing

Reviewer gate:
- use `explorer-fast` or `explorer` for first-pass triage
- require `reviewer` whenever the confidence is not `high`, the evidence is only `behavioral inference` or `unclear`, the mapped integration subject is high-risk, the commit seems only partially covered, or the commit is being closed as `covered elsewhere` without a concrete direct match
- keep reviewer prompts narrow: provide one upstream commit, the triage result, the concrete local evidence already found, and a small fixed output schema instead of asking the reviewer to rediscover broad repo context from scratch
- do not treat `explorer-fast` as automatically correct; compare its output against the orchestrator's local evidence and the eventual reviewer result so the prompt can be recalibrated if it starts routing too many or too few commits to review
- when an explorer claims `direct local match`, it should name the matching local commit hash explicitly so the orchestrator can confirm quickly without reconstructing the match from surrounding context
- for docs-only commits without an exact local commit match, the agent should cite the exact current local command, sentence, or section that already covers the upstream intent; if it cannot point to that concrete live-doc evidence, send the commit to `reviewer`
- after a repeated run of accurate high-confidence `direct local match` results on a similar low-risk stretch, the orchestrator may use spot checks and light-touch confirmation instead of fully reconstructing every match by hand
- in a maintainer-directed low-context audit mode, prefer subagent-led evidence gathering and send anything suspicious, under-supported, or oddly classified to `reviewer` instead of expanding the orchestrator's own manual investigation

Coverage values:
- `full`
- `partial`
- `none`

Final disposition values:
- `already integrated`
- `covered elsewhere`
- `intentionally skipped`
- `needs subject reopen`
- `needs new integration task`
- `maintainer decision needed`

Fork-audit completion rule:
- do not mark a fork audit `reviewed` until every commit in the recorded range appears here, every row has a final disposition, every unresolved row links to follow-up work, any row marked `partial` explains what is already present and what follow-up remains, and a short delta check confirms whether new upstream commits appeared after the recorded fork tip at audit start

## thejuran

Audit base: `origin/master @ ff2a1039935beccbbf7ec76134b41d2e91137742`
Source branch: `thejuran/master`
Fork tip at audit start: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
Inventory status: `complete`
Audit state: `in progress`
Pass date: `2026-03-10`

| Commit | Upstream commit subject | Mapped integration subject | Triage outcome | Confidence | Evidence | Reviewer needed | Coverage | Final disposition | Follow-up / proof |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bdcc28746933ce5b41c6789e2104c3977780caa8` | Increase lftp timeout from 3 to 180 seconds | unknown | unprocessed | — | — | — | — | — | — |
| `81b307cad52617168bab2768a7fd95c8f5960439` | Merge pull request #1 from Jules1651/claude/increase-lftp-timeout-QieKs | unknown | unprocessed | — | — | — | — | — | — |
| `7adbd5f516d96483f6fdcb27a303f52be7677774` | Fix Dockerfile for modern Python 3.11-slim base image | unknown | unprocessed | — | — | — | — | — | — |
| `7042028e8a8c7a570656a98d53fa46af240f379b` | Update poetry.lock for Python 3.11 and fix deprecated syntax | unknown | unprocessed | — | — | — | — | — | — |
| `9a25b36d94236c339939b04fe96fb19eb193903c` | Update all dependencies for Python 3.11 compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `ccc3f9c62fd34291d9a2bf5eaec484fce6353fdb` | Merge pull request #2 from Jules1651/claude/audit-dockerfile-dependencies-iLt4o | unknown | unprocessed | — | — | — | — | — | — |
| `7f2de6841927c945f8496a9294939def11fba697` | Fix Poetry package-mode configuration | unknown | unprocessed | — | — | — | — | — | — |
| `0d0037d19d4583548f388c379d7799b8a9ecdce4` | Add pytest pythonpath configuration | unknown | unprocessed | — | — | — | — | — | — |
| `461fa45757cd05e61be965c4d3413dfad6f656c8` | Merge pull request #3 from Jules1651/claude/fix-poetry-config-TPLzN | unknown | unprocessed | — | — | — | — | — | — |
| `5ea6f8e4e246257055517817a847a64268e325fa` | Make docker-image Dockerfile self-contained | unknown | unprocessed | — | — | — | — | — | — |
| `21fe17d6edc4c8d2295f2b28e3fe5d2232c1ef56` | Merge pull request #4 from Jules1651/claude/fix-dockerfile-staging-registry-ubDd8 | unknown | unprocessed | — | — | — | — | — | — |
| `11b0944d53590a675d6607c70bb6ac0d436830f8` | Fix Docker Angular build by creating /build directory | unknown | unprocessed | — | — | — | — | — | — |
| `74f20dc737b4d1f79bb60978d012dc79bf8739c6` | Merge pull request #5 from Jules1651/claude/fix-docker-dist-directory-g8Td5 | unknown | unprocessed | — | — | — | — | — | — |
| `777917ad43f1b9c78767312ceac3674b087357d4` | Add skipLibCheck to fix @types/eventsource conflict | unknown | unprocessed | — | — | — | — | — | — |
| `1979e311324cbaaeed600dcb92e43ab46b3d8a8c` | Merge pull request #6 from Jules1651/claude/fix-eventsource-types-n451I | unknown | unprocessed | — | — | — | — | — | — |
| `ee190111c7782640f05b7b0b6b60797ff08e1197` | Add CLAUDE.MD with project documentation for Claude Code | unknown | unprocessed | — | — | — | — | — | — |
| `4f19ec6aef10b6f14eaf20db297a3cd2333001bd` | Phase 1: Update infrastructure dependencies | unknown | unprocessed | — | — | — | — | — | — |
| `1336c07e3046629a342c8a4d65c12d73211a50af` | Merge pull request #7 from Jules1651/claude/update-claude-md-LfeJh | unknown | unprocessed | — | — | — | — | — | — |
| `a3a9bdf601c750a588016d9ce0ccd5ab1c6c4df7` | Fix npm install for Node 20 with legacy Angular 4.x | unknown | unprocessed | — | — | — | — | — | — |
| `fc9ca7be2ff2ce0efb0079772d643fe0a946cc86` | Merge pull request #8 from Jules1651/claude/update-claude-md-LfeJh | unknown | unprocessed | — | — | — | — | — | — |
| `0b26dcf6b8debd9857aae789cf8c3023e8393ea0` | Fix node-sass version for Node 20 compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `a5da13c80bf8d70ee8ab8ea88a0feec4fa20605d` | Merge pull request #9 from Jules1651/claude/update-claude-md-LfeJh | unknown | unprocessed | — | — | — | — | — | — |
| `9d72249416c5616fb8f51e175d038fdb3e161201` | Fix Python version constraint and regenerate poetry.lock | unknown | unprocessed | — | — | — | — | — | — |
| `6a4e77c35c112244ee00758a446a9c564e47c385` | Merge pull request #10 from Jules1651/claude/update-claude-md-LfeJh | unknown | unprocessed | — | — | — | — | — | — |
| `996ae6a551ae28b302d49444af69a1258e1eac0d` | Fix GLIBC compatibility by using Ubuntu 20.04 for PyInstaller build | unknown | unprocessed | — | — | — | — | — | — |
| `950d9fc79d5f06a2cc0d7b88aa89e10fbf869103` | Merge pull request #11 from Jules1651/claude/fix-python-library-error-T5i55 | unknown | unprocessed | — | — | — | — | — | — |
| `c94d626afe857f6072ba6eb9a7fc891d993b5485` | Fix GLIBC compatibility in docker-image Dockerfile | unknown | unprocessed | — | — | — | — | — | — |
| `04b1f81ec0ec565cc8d773d57e9f4c05bfa68d64` | Merge pull request #12 from Jules1651/claude/fix-python-library-error-T5i55 | unknown | unprocessed | — | — | — | — | — | — |
| `a8a6ebaa605aba2a29098cf278bd052f3e3118a8` | Use manylinux_2_28 for PyInstaller builds to fix GLIBC compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `31b68ec981dc800b0c85abd7a053c39645c86103` | Merge pull request #13 from Jules1651/claude/fix-python-library-error-T5i55 | unknown | unprocessed | — | — | — | — | — | — |
| `da6a4c6f74a33f9de0a11af16ce0ee453f302897` | Switch to python:3.11-slim-bullseye for PyInstaller builds | unknown | unprocessed | — | — | — | — | — | — |
| `7909e693454c45cfe2119cdad190ca62ca4f096d` | Merge pull request #14 from Jules1651/claude/fix-python-library-error-T5i55 | unknown | unprocessed | — | — | — | — | — | — |
| `36123cda271c9d3dceb282058b505ff14e418c3d` | Phase 2: Modernize test tooling | unknown | unprocessed | — | — | — | — | — | — |
| `9a9d10dc49dee0e4df8c48c1606403188a4e9a2d` | Fix dependency compatibility for Angular 4.x | unknown | unprocessed | — | — | — | — | — | — |
| `05bc17a3f6c2eb0f6d551602a408fb16b29da18c` | Fix test Dockerfiles for Debian 12 compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `96296468a27d31ec826670d4c26cf118d0e51bff` | Merge pull request #15 from Jules1651/claude/start-phase-2-YXy2p | unknown | unprocessed | — | — | — | — | — | — |
| `c32649c208286c3b6dcb41a1273cb30335ada582` | Fix Dockerfile linting: use uppercase AS in FROM statements | unknown | unprocessed | — | — | — | — | — | — |
| `45ee8347c954536dbdde1cc0304df9320bf5668e` | Use 'docker compose' instead of 'docker-compose' in Makefile | unknown | unprocessed | — | — | — | — | — | — |
| `19bfb50ea97f60361522a860645dfd24fcdf8465` | Merge master: resolve e2e Dockerfile conflict with uppercase AS | unknown | unprocessed | — | — | — | — | — | — |
| `f3dfabd0205fb4ffef38acff19d79804d9ec7a34` | Remove tslint.json from Angular test Dockerfile | unknown | unprocessed | — | — | — | — | — | — |
| `96ba53b4c196dde795111a0a1f50c280785d6156` | Fix Python test Dockerfile: use mkdir -p for /var/run/sshd | unknown | unprocessed | — | — | — | — | — | — |
| `f38ae7e40953970666b61b20ef05ed54da454423` | Fix Chrome headless flags for newer Chrome versions | unknown | unprocessed | — | — | — | — | — | — |
| `562955a424912265a6e816f53a50ce07859931ca` | Merge pull request #16 from Jules1651/claude/fix-unit-tests-1hU5b | unknown | unprocessed | — | — | — | — | — | — |
| `6a695ac555d46fa6193909657c322a621fb2a7fe` | Replace karma-mocha-reporter with karma-spec-reporter for Node 20 compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `d8fcb08fa46bd1acb9b9d3b4b4ba4f1e0e8d0029` | Downgrade Node.js to v16 for Angular CLI 1.3 Karma test compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `ff2f07551cc37fdf3636332b10710e5dc1711747` | Upgrade Karma to v6.x for Node 20 compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `1c8fa8a41d2b0a06e1a6b26553bd5b0d245714c6` | Pin type definitions and Jasmine to TypeScript 2.4.x compatible versions | unknown | unprocessed | — | — | — | — | — | — |
| `85cb4e0b1a2c63fd20b9d1b6ec39ae23e8007ffd` | Force exact @types versions in Docker build for TypeScript 2.4.x compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `79c98be844f6826247f02411e5b093d61257fae6` | Use older @types/node@8.0.0 for TypeScript 2.4.x compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `a4356d4e7ca5cbaa9567287f29b82b11113dc502` | Add pytest-timeout to prevent hanging tests | unknown | unprocessed | — | — | — | — | — | — |
| `a1deb233c837c7172cd391924ba334d39ad42ad2` | Fix SSH error handling and WebApp attribute conflict for Python tests | unknown | unprocessed | — | — | — | — | — | — |
| `77df923608aa0a8cd20f1838eccb43233f8e4223` | Make SSH/Controller test assertions more flexible for newer OpenSSH | unknown | unprocessed | — | — | — | — | — | — |
| `1aae41116d6f99478c2b4fabda3b6d06a9bef7aa` | Fix RAR extraction and extract filename tests | unknown | unprocessed | — | — | — | — | — | — |
| `28a8b22351762d5785e7157ae4a57df09239b16f` | Make SSH/SCP test assertions more permissive for various SSH versions | unknown | unprocessed | — | — | — | — | — | — |
| `84f64738a1589f4939afb022cd1b456d7063d692` | Skip test_extract_archive_overwrites_existing test | unknown | unprocessed | — | — | — | — | — | — |
| `20a3e0134ac69929cda17d6748450a6c1eef8657` | Skip WebApp streaming tests that timeout with webtest | unknown | unprocessed | — | — | — | — | — | — |
| `b75da034c3985181afed1b5d39e79165e997dca1` | Add 'connection closed' to SSH test error assertions | unknown | unprocessed | — | — | — | — | — | — |
| `068bb9dda0a014ce024b800b736da229e3d6c7fd` | Merge pull request #28 from Jules1651/claude/fix-angular-test-failure-XvPoC | unknown | unprocessed | — | — | — | — | — | — |
| `50a8ce3af858f5daf16421c4b55f1445fe76b532` | Update setup_seedsync.sh | unknown | unprocessed | — | — | — | — | — | — |
| `443fdcba574d3261a2f34b70988b91bd1e5f17e8` | Update setup_seedsync.sh | unknown | unprocessed | — | — | — | — | — | — |
| `631a50fe1df2492f79a96a1d1052a1e0310e4175` | Update Dockerfile | unknown | unprocessed | — | — | — | — | — | — |
| `ad81dd9346839fbd8143b10f49776158b4c4e4f6` | Replace node-sass with sass (dart-sass) to fix build warnings | unknown | unprocessed | — | — | — | — | — | — |
| `a1f23761ef208353ff546dc3f8580bd304bf1455` | Revert "Replace node-sass with sass (dart-sass) to fix build warnings" | unknown | unprocessed | — | — | — | — | — | — |
| `b9cfb0fbf1c784569bf6ca4775ac437693e9a093` | Fix libsass build warnings by suppressing C++ compiler warnings | unknown | unprocessed | — | — | — | — | — | — |
| `2bea28e09832705c0e17d5876aea391ad65f1848` | Update deprecated GitHub Actions to latest versions | unknown | unprocessed | — | — | — | — | — | — |
| `a27e231956a0d5f13cfd4dd3b0573f717fe12e56` | Remove dh-systemd from Build-Depends | unknown | unprocessed | — | — | — | — | — | — |
| `0fffbdf1b7a96ab65b059c3610ea2c92d1107046` | Skip dh_shlibdeps for PyInstaller-built executables | unknown | unprocessed | — | — | — | — | — | — |
| `8a661e284e855e9b893d1751913931a57d347d30` | Use lowercase repository name for GHCR | unknown | unprocessed | — | — | — | — | — | — |
| `9759b76d1a4513bb427f29b0c3641d1106e5fbf9` | Add gcc and libffi-dev to Python build environment | unknown | unprocessed | — | — | — | — | — | — |
| `984b8a1525ef80b109663a8b75f1f7e2d0454b83` | Fix e2e tests for cgroups v2 (GitHub Actions Ubuntu 24.04) | unknown | unprocessed | — | — | — | — | — | — |
| `55e582332eb4ebc31f487d553ad437def762cf30` | Remove invalid cgroup: host from compose.yml | unknown | unprocessed | — | — | — | — | — | — |
| `c83efbcb6ed8387c86a7621741a090698fb98e41` | Install Poetry via pip instead of installer script | unknown | unprocessed | — | — | — | — | — | — |
| `558411badf857516199529284583d869ca9da1ac` | Mask systemd-resolved in e2e test containers | unknown | unprocessed | — | — | — | — | — | — |
| `e15601a82c5613cda1bbf8813cda0bff430a542b` | Add build-essential to docker-image build environment | unknown | unprocessed | — | — | — | — | — | — |
| `3eefa631509926d4999f37872f418043e4baa37d` | Add build-essential to pyinstaller build stage | unknown | unprocessed | — | — | — | — | — | — |
| `c112737b4e7aaad2604b55e41ae58cea5660f9b9` | Add Python and build tools to Angular build stage | unknown | unprocessed | — | — | — | — | — | — |
| `9526eb9292843eac5b239ae231e3c399d24d394e` | Add zlib1g-dev for PyInstaller bootloader compilation | unknown | unprocessed | — | — | — | — | — | — |
| `bd3a63b9bc56feefbcdba2ded932673cf25b2dab` | Ensure /etc/resolv.conf is a regular file in e2e containers | unknown | unprocessed | — | — | — | — | — | — |
| `49474ba3780e6ff6b11279ad3e19814e7e92bcc8` | Remove resolv.conf modification from e2e container Dockerfiles | unknown | unprocessed | — | — | — | — | — | — |
| `f278379b6a69bf70d2031731fe177d5fdfff443d` | Fix CMD format in stage/deb Dockerfile | unknown | unprocessed | — | — | — | — | — | — |
| `e5416c59858c986bb2b81ae6035e337fbf95b78c` | Add cgroups v2 support for systemd in Docker containers | unknown | unprocessed | — | — | — | — | — | — |
| `7e7289e4ee2ab15364b82c01840e6553987dcc03` | Update compose files to use compose spec format | unknown | unprocessed | — | — | — | — | — | — |
| `0e228818abde8249fe414b1563814e7f8716ce68` | Remove unsupported cgroup attribute from compose file | unknown | unprocessed | — | — | — | — | — | — |
| `b5cf1d2a037634b3a872cfd31834d06dcd1c5266` | Add cgroup: host for cgroups v2 systemd support | unknown | unprocessed | — | — | — | — | — | — |
| `87d2d141f9683493406e854bc6739590a5acbaad` | Configure Docker daemon with cgroupns=host for e2e tests | unknown | unprocessed | — | — | — | — | — | — |
| `99e6d9e361bb52092e2462ba2705226101c01bc7` | Fix network-online.target blocking seedsync service start | unknown | unprocessed | — | — | — | — | — | — |
| `f65a996907872d846b1da1d0269839f819c94bbb` | Fix stage/deb Dockerfile to match ubuntu-systemd base images | unknown | unprocessed | — | — | — | — | — | — |
| `5e2cc8e63bb124041e4199b3f8e5d885eecac705` | Fix multiple e2e test issues found in thorough review | unknown | unprocessed | — | — | — | — | — | — |
| `f25353f0dfd1ba8054597cfce221080cb7350692` | Use /sbin/init instead of /lib/systemd/systemd in stage/deb | unknown | unprocessed | — | — | — | — | — | — |
| `f4b496b8f02a61dda01e2ba123969f6528831a1c` | Merge cgroupns config with existing Docker daemon.json | unknown | unprocessed | — | — | — | — | — | — |
| `601c7df394830b9b429da51ab84efd0617ed496b` | Add debug output to entrypoint to diagnose e2e test failures | unknown | unprocessed | — | — | — | — | — | — |
| `e7aece98b07cf8dc6e8938ac9c401deaada9fead` | Disable systemd mode for E2E tests - run seedsync directly | unknown | unprocessed | — | — | — | — | — | — |
| `fde8158dc5a738f57f48d3688cee6f51530fddb6` | Remove systemctl enable from install script (not using systemd) | unknown | unprocessed | — | — | — | — | — | — |
| `d1694dd1e5c10156ffde88b3667c80c420c1b335` | Fix entrypoint to run seedsync as user 'user' with correct HOME | unknown | unprocessed | — | — | — | — | — | — |
| `c1a1a5ed177f26e7f6bcb2095d6f6700a4452b65` | Add comprehensive debugging output to E2E test entrypoint | unknown | unprocessed | — | — | — | — | — | — |
| `a782f7e75826573c47be1bfc0b76e5357fa23e6e` | Fix myapp container log capture to use OS-specific container names | unknown | unprocessed | — | — | — | — | — | — |
| `f2b4889765a4cb53475b3a452577fd5024a8bc4c` | Fix scanfs path in deb build - copy to _internal directory | unknown | unprocessed | — | — | — | — | — | — |
| `0cb32280056fcfdad740dc34d8e505e961dc4740` | Fix Bottle _stop_flag attribute conflict in web_app.py | unknown | unprocessed | — | — | — | — | — | — |
| `2ae51736a47e993de71e8660fe7e39b5b2e2c78a` | Update deb E2E test matrix for GLIBC 2.29+ compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `4cbdaa549f20e6bd572a8ddc7da93cb5870c59d9` | Update Makefile and docs for GLIBC 2.29+ requirement | unknown | unprocessed | — | — | — | — | — | — |
| `ef283ccf187c2fd6ede23df98c96747f2e8579f3` | Fix HTML path in deb build - copy to _internal/html | unknown | unprocessed | — | — | — | — | — | — |
| `8fac10e480cbb283a3be3f1e452f8f776a46c69f` | Fix E2E test failures for autoqueue and dashboard tests | unknown | unprocessed | — | — | — | — | — | — |
| `7897c8ea7c4e85aaf59beaf53fd095604d813f3c` | Catch LftpJobStatusParserError in controller to prevent server crash | unknown | unprocessed | — | — | — | — | — | — |
| `9e84b9e54d3284a90fb9864b4612fb1ded3f3914` | Handle LftpJobStatusParserError in kill command | unknown | unprocessed | — | — | — | — | — | — |
| `b4c393ef9f3b78c82e1246b79d0d98ae0070531a` | Fix E2E test: trim whitespace from extracted text content | unknown | unprocessed | — | — | — | — | — | — |
| `3f7af5c79aaff746c595b37097e84cbbd61e84e9` | Fix Docker ARM E2E tests: match remote container architecture | unknown | unprocessed | — | — | — | — | — | — |
| `2e2a60e030d40ca33c6da4795c2b86d5c604f2b8` | Remove redundant platforms build key from compose.yml | unknown | unprocessed | — | — | — | — | — | — |
| `c73205b19c707ac74da18513a0e56ec0d5fcbebb` | Set DOCKER_DEFAULT_PLATFORM for cross-architecture builds | unknown | unprocessed | — | — | — | — | — | — |
| `b4fb9465ab46a0cb21c7ba2f1bc17b0b4c7f98f1` | Fix Docker ARM E2E: pre-build remote container for target platform | unknown | unprocessed | — | — | — | — | — | — |
| `0ac447026442c5f181ea2c845025f1f0e4c6bff3` | Exclude arm64 from Docker e2e tests - too slow under QEMU | unknown | unprocessed | — | — | — | — | — | — |
| `49e8d61c652e557d1c2b0324c8b01b24ae885f40` | Merge pull request #36 from Jules1651/claude/review-previous-context-AI8Oj | unknown | unprocessed | — | — | — | — | — | — |
| `58ef287b6153397c68233ae1efe5980463ac45ac` | Add files via upload | unknown | unprocessed | — | — | — | — | — | — |
| `655b6b2990c909b99ee0f9faf5b45e641f728af3` | Add ChromeHeadlessCI custom launcher for CI environment compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `162d98c2ebedcca0e40c354801659884f7f8699d` | Merge pull request #38 from Jules1651/claude/fix-angular-tests-Tmji1 | unknown | unprocessed | — | — | — | — | — | — |
| `416713e8cddf0c937e673bfc75c9022cc9b1d247` | Replace deprecated release actions with GitHub CLI (#48) | unknown | unprocessed | — | — | — | — | — | — |
| `48ff14b526b248d686b0459e0979ce6944c05385` | Add memory leak prevention to core file services (#50) | unknown | unprocessed | — | — | — | — | — | — |
| `1efe46634d1fb8ec16df7011fb2890eed328de84` | Update RxJS imports in file services to use direct module paths (#51) | unknown | unprocessed | — | — | — | — | — | — |
| `4c381e498f4def1d02cdd7da0a8b9fbf2a1790cb` | Refactor Controller.__process_commands() into specialized handlers (#52) | unknown | unprocessed | — | — | — | — | — | — |
| `942d4651f91192cb98535c34f8f3e29b794e8c8a` | Refactor JobStatusParser with factory pattern and extracted components (#53) | unknown | unprocessed | — | — | — | — | — | — |
| `7ba443baaf9713c7a8a0756c583e33dc0a1bcb8e` | Enable stricter TypeScript/ESLint rules and fix any type usage (#56) | unknown | unprocessed | — | — | — | — | — | — |
| `bc454eb73cbcfec0bff85d032f69a03cfb9b7ff9` | Delete logs_55588916360.zip | unknown | unprocessed | — | — | — | — | — | — |
| `cde6bbe9e525f2d70318c7b9363cb91bc3a427f2` | Upgrade Angular from v4.2.4 to v5.2.11 (Chunk 8.1) (#57) | unknown | unprocessed | — | — | — | — | — | — |
| `805de78a559228c96eb7a9e5511502fcf4c3acbf` | Upgrade Angular from v5.2.11 to v6.1.10 (Chunk 8.2) (#58) | unknown | unprocessed | — | — | — | — | — | — |
| `cee7689fe8d710c5139e7d5e87887845614111e2` | Fix Chrome Headless test disconnections by removing busy-wait loops (#60) | unknown | unprocessed | — | — | — | — | — | — |
| `b7325d108fd052f60f8090b5f9a63587549e5198` | Upgrade Angular from v7.2.16 to v8.2.14 (Chunk 8.4) (#61) | unknown | unprocessed | — | — | — | — | — | — |
| `9a974e1b99595f394babcc1ed636d9fe2237ddff` | Upgrade Angular from v8.2.14 to v9.1.13 (Chunk 8.5) (#62) | unknown | unprocessed | — | — | — | — | — | — |
| `3130b2ceb28a2acd91f77b1c1624c98ee7ead83e` | Upgrade Angular from v9.1.13 to v10.2.5 (Chunk 8.6) (#63) | unknown | unprocessed | — | — | — | — | — | — |
| `dace8a3c7c9128599f2e40b3d5adef79d626a130` | Rename CLAUDE.MD to CLAUDE.md | unknown | unprocessed | — | — | — | — | — | — |
| `9dc14f99b0a2e588df204da2858e12da5b07dca5` | Claude/angular 9 to 10 upgrade qn qxw (#64) | unknown | unprocessed | — | — | — | — | — | — |
| `56e9f092aea90ebec4aa8e317ef831438b2516b4` | Claude/angular 9 to 10 upgrade qn qxw (#67) | unknown | unprocessed | — | — | — | — | — | — |
| `5fdae7852c37632f126b2f297fab6106f4b0f9c3` | Upgrade Angular from v11.2.14 to v12.2.17 (Chunk 8.8) (#68) | unknown | unprocessed | — | — | — | — | — | — |
| `b91337a7246c93ea69c0f23727c846de05d5d3bb` | Claude/angular 11 to 12 upgrade f a7ph (#70) | unknown | unprocessed | — | — | — | — | — | — |
| `7661944d5472c4ad559da0fe8f7bb8476766dca2` | Upgrade Angular from v13.4.0 to v16.2.12 (Chunk 8.9) (#71) | unknown | unprocessed | — | — | — | — | — | — |
| `f2eb4ee4fcbdf640a85b34636fee37e96edb104d` | Upgrade Angular from v16.2.12 to v19.2.18 (Chunk 9) (#72) | unknown | unprocessed | — | — | — | — | — | — |
| `ac81004f23a546fd22f99baae39d817afbbaed7c` | Angular 19 optional improvements: standalone, SCSS @use, ESLint 9 (#73) | unknown | unprocessed | — | — | — | — | — | — |
| `ef7765619d5dd13a9c8e73149d51a51b6f366e02` | Fix npm peer dependency conflicts to allow install without --legacy-peer-deps (#74) | unknown | unprocessed | — | — | — | — | — | — |
| `c1a4a2f88c467dc93b2e6e98a1b22c273b55f87a` | Claude/review first section a yyt d (#75) | unknown | unprocessed | — | — | — | — | — | — |
| `b632b05799921219fb613a83b66d7b7fd8a03e8f` | Fix backend memory leaks: job status, event queue, and monitoring (#76) | unknown | unprocessed | — | — | — | — | — | — |
| `017c920c6070b3dd5934822bbed057e639a4eb6a` | Add comprehensive legacy codebase analysis and modernization report (#77) | unknown | unprocessed | — | — | — | — | — | — |
| `01bffd87afc2512122cff538acefadc9f9d81784` | Add modernization action plan with 15 Claude Code-optimized sessions (#78) | unknown | unprocessed | — | — | — | — | — | — |
| `4f58c8f172e90cd64ed6ab1051353a3d914f0f50` | Fix format string bugs (Session 1: Quick Wins) (#79) | unknown | unprocessed | — | — | — | — | — | — |
| `2323761d7ff1bf6bcd67f1ef8b955ae6aa54ba18` | Add thread safety to Model and AutoQueuePersist listeners (Session 4) (#80) | unknown | unprocessed | — | — | — | — | — | — |
| `b32eb93c83ad85cfa9aaefb8ec3242815d84223a` | Fix Angular memory leaks in BaseWebService (Session 5) (#82) | unknown | unprocessed | — | — | — | — | — | — |
| `aaeddbf8f3448cc93d6ce980ce4617dcbbb9e3ae` | Fix Angular memory leaks in FileOptionsComponent (Session 6) (#83) | unknown | unprocessed | — | — | — | — | — | — |
| `812f8a9049e8bfee83901464cdd346f5e6a76d57` | Fix Angular memory leaks in ViewFileFilterService, ViewFileSortService, VersionCheckService (Session 7) (#84) | unknown | unprocessed | — | — | — | — | — | — |
| `57f460b601e0b6b39e9f78551320b5c60609989e` | Replace deep copy with freeze-on-add immutability pattern (Session 8) (#85) | unknown | unprocessed | — | — | — | — | — | — |
| `c52554b3863cf6e90b34a127061f8085c95944ca` | Implement bounded collections with LRU eviction for downloaded/extrac… (#86) | unknown | unprocessed | — | — | — | — | — | — |
| `2969ae11d21b9a2beb9eae2143ecc9cde1e07d58` | Add Session 16: Frontend Dependency Modernization (#87) | unknown | unprocessed | — | — | — | — | — | — |
| `be70feb8bf417d54e44f3d96b69f5896f37f748c` | Claude/review modernization plan y jk ep (#88) | unknown | unprocessed | — | — | — | — | — | — |
| `e0c2fabf282b95d5637043f4c2440878ca938de3` | Complete Session 10: Queue drain and SSE polling review (#89) | unknown | unprocessed | — | — | — | — | — | — |
| `88d96a1642bcf5274fecb1452c20b259dd0cf90d` | Improve HTTP status codes for REST semantics (Session 11) (#90) | unknown | unprocessed | — | — | — | — | — | — |
| `2f914d4cb68638de2e1ff93c7f531e79a870d22d` | Refactor build_model() from 249 lines to 28 lines (Session 12) (#91) | unknown | unprocessed | — | — | — | — | — | — |
| `1bd91fc1eb79fd542516dde92861899f97b559e0` | Refactor __update_model() from 137 lines to 36 lines (Session 13) (#92) | unknown | unprocessed | — | — | — | — | — | — |
| `2bc18bd83ab129368f2652da0a44c89930732851` | Extract ScanManager from Controller (Session 14) (#93) | unknown | unprocessed | — | — | — | — | — | — |
| `56ea03260a654f0f215d245e9db2c07183bac137` | Add publication plan for fork release under thejuran (#94) | unknown | unprocessed | — | — | — | — | — | — |
| `c539ed97cdfad838bf28735d86f0c4be47611ce8` | Extract LftpManager and FileOperationManager from Controller (Session… (#95) | unknown | unprocessed | — | — | — | — | — | — |
| `5706eafbc2693da115f536d799c1bfb6e05bfc7f` | Upgrade Bootstrap 4→5 and modernize frontend dependencies (Session 16) (#96) | unknown | unprocessed | — | — | — | — | — | — |
| `f56d78ac98272b8cee4e47a09b1fea79d0e6a3af` | Fix CI/CD warnings and security vulnerabilities (Session 17) (#97) | unknown | unprocessed | — | — | — | — | — | — |
| `c71fc801054033ea4570741e74401928a62258de` | Update CLAUDE.md to reflect modernization changes (#98) | unknown | unprocessed | — | — | — | — | — | — |
| `3ffaa4d1d709bb57bc859bfcea83ef5ab0b7895c` | Claude/plan project publication g5 rr o (#99) | unknown | unprocessed | — | — | — | — | — | — |
| `d11b3f3788f78fbda60b03c0ec419058561f6788` | Fix SSH password test failure on OpenSSH 9.x (#101) | unknown | unprocessed | — | — | — | — | — | — |
| `e289470c37e8241676ba2271dbb8b2d61e2102e3` | Add ACKNOWLEDGMENTS.md to credit original author (#102) | unknown | unprocessed | — | — | — | — | — | — |
| `8fbf770ba497744be4d6f41aa182225788cf459e` | Add GitHub templates and security policy (Session 6) (#103) | unknown | unprocessed | — | — | — | — | — | — |
| `f01806802f833451d072042958ca7e3c535b67e9` | Claude/publication plan session 6 vs dja (#104) | unknown | unprocessed | — | — | — | — | — | — |
| `9392653b597c519b9b825ec508ef9464e025999a` | Add full ARM64 CI/CD support with native runners (#105) | unknown | unprocessed | — | — | — | — | — | — |
| `5208aab2b00e9d6c6740107cf764b3342ba33007` | Add releases and versioning documentation to CLAUDE.md (#107) | unknown | unprocessed | — | — | — | — | — | — |
| `15a9918ec8254fcd37ff691c0d7fd5c6fd650fa5` | Fix checkbox settings not saving (boolean to string conversion) (#108) | unknown | unprocessed | — | — | — | — | — | — |
| `fdafd54d0d8ad69eec877ba4336ca46ce2e28777` | Update About page to mirror repository information (#109) | unknown | unprocessed | — | — | — | — | — | — |
| `1ecea11303913ba349a9503204dd07c98ba21ff0` | Persist dashboard status filter selection to localStorage (#110) | unknown | unprocessed | — | — | — | — | — | — |
| `7a88f02b0f7eb83786e3d4e8dc70dbf5b4780d4d` | Fix logs page hang caused by unsafe lock management (#111) | unknown | unprocessed | — | — | — | — | — | — |
| `12f2a68792419bf2b781576b38115567096aee08` | Fix idle SSE connection not auto-reconnecting (#112) | unknown | unprocessed | — | — | — | — | — | — |
| `3b98bd8f8b49cd8dc39456d20e9dd9a35eff73ee` | Fix auto-queue re-queuing STOPPED files on startup (#113) | unknown | unprocessed | — | — | — | — | — | — |
| `fd9c25fd318de6181cf8e5f53f1c806868ec28f9` | Add GitHub Actions workflow for Docker image publishing | unknown | unprocessed | — | — | — | — | — | — |
| `cbec5640d5d45d862a1d455de01e3d573426ac54` | Claude/fix logs seedsync hang 7d kcz (#114) | unknown | unprocessed | — | — | — | — | — | — |
| `321e74291d90da38755b921ea4fd20c084d1b641` | Claude/fix autoqueue button bco of (#115) | unknown | unprocessed | — | — | — | — | — | — |
| `701ba9279643569f8f807881b8a9efbb7e6df995` | Claude/fix remote files requeue jpi j2 (#116) | unknown | unprocessed | — | — | — | — | — | — |
| `c0aa56b9b9e5d6aeb3c0efa6e0a8d90b98461d4d` | Claude/fix logs seedsync hang 7d kcz (#117) | unknown | unprocessed | — | — | — | — | — | — |
| `1048d87b9f896d72df95d54343bed429e5759373` | Claude/fix remote files requeue jpi j2 (#118) | unknown | unprocessed | — | — | — | — | — | — |
| `284d843d842d66da5e96e4765565494642be72b0` | Claude/fix logs seedsync hang 7d kcz (#119) | unknown | unprocessed | — | — | — | — | — | — |
| `33c01698b33448942ced2dea299de3362c1ca21e` | Claude/fix logs seedsync hang 7d kcz (#120) | unknown | unprocessed | — | — | — | — | — | — |
| `33cc1cf0480adaaa2c50cf1cf6c0f7ffb6be71e8` | Add delay between SSE events to prevent connection flooding (#121) | unknown | unprocessed | — | — | — | — | — | — |
| `cd8c770bdcf9d4684404eda316ce8c87857eaade` | Add server-side heartbeat ping for SSE connection keepalive (#122) | unknown | unprocessed | — | — | — | — | — | — |
| `eef4c325c3c83dc61cb889b756cee905821ca199` | Fix logs page blank issue caused by ViewChild timing (#123) | unknown | unprocessed | — | — | — | — | — | — |
| `71e18003aed594606732f8819b0e30ad54b76c26` | Claude/fix logs seedsync hang 7d kcz (#124) | unknown | unprocessed | — | — | — | — | — | — |
| `8dd7bf4eeede21899cc1ea2f641ec84b5997a1e2` | Add status indicator to logs page when no logs displayed (#125) | unknown | unprocessed | — | — | — | — | — | — |
| `8a200219d68bfbe9ea7f1f01605162c2569a203d` | Claude/fix remote files requeue jpi j2 (#126) | unknown | unprocessed | — | — | — | — | — | — |
| `a1b467e8f030e56b10412b2c1a725413ab49b5c9` | Claude/fix autoqueue restart 3 pyyw (#127) | unknown | unprocessed | — | — | — | — | — | — |
| `d7d356b87cc43350812e3316106e13b5707f8eb1` | Claude/bulk file actions dxsg e (#130) | unknown | unprocessed | — | — | — | — | — | — |
| `9e290af82776a4c2615dff6fb851694beacb26fd` | Claude/fix autoqueue restart 3 pyyw (#128) | unknown | unprocessed | — | — | — | — | — | — |
| `f5d4d240a973a0a7e8e143f75a067a5115bd0969` | Claude/fix autoqueue restart 3 pyyw (#131) | unknown | unprocessed | — | — | — | — | — | — |
| `a07a754554e4428a600b694c6ee769b69aaf287f` | Claude/add uat plan p fje9 (#132) | unknown | unprocessed | — | — | — | — | — | — |
| `bbf1310881fd5181d68da2614b9b4f02378365c2` | Add GitHub Actions workflow for Docker image publishing | unknown | unprocessed | — | — | — | — | — | — |
| `5d7c50abca299a6f7ba368fb4f950d29606bb609` | Delete .github/workflows/docker-publish | unknown | unprocessed | — | — | — | — | — | — |
| `21bb73c1e35b50cda8982a37590d3535e37eccb2` | Add image source label to Dockerfile | unknown | unprocessed | — | — | — | — | — | — |
| `2ce3852afaea41560ef7a67b132c0174d62c09ff` | Enhance GitHub Pages documentation site (#133) | unknown | unprocessed | — | — | — | — | — | — |
| `df868bc7d3eead05363b897fa278508e0747bc3a` | Add bulk command API endpoint for multi-file actions (#141) | unknown | unprocessed | — | — | — | — | — | — |
| `890c6dedb19762c603aec848d7a572649aa10ef9` | Add UI styling unification action plan (#142) | unknown | unprocessed | — | — | — | — | — | — |
| `043de1e690bd05732421413800dd48e7f2af7bb8` | Claude/optimize selection performance 8 lt6 q (#143) | unknown | unprocessed | — | — | — | — | — | — |
| `5c7bfc853588aa5885b4bca2e68fe0c102eadfbd` | Optimize bulk endpoint with parallel command processing (#144) | unknown | unprocessed | — | — | — | — | — | — |
| `a4cbdc6bc850eb5de08380d99e2ed9b67d409a6b` | Fix critical security issues in bulk endpoint (#146) | unknown | unprocessed | — | — | — | — | — | — |
| `6e4c40a47d99136940d80f69e165d1e8b7a16bf1` | Add Session 14: Virtual scrolling plan for checkbox performance (#147) | unknown | unprocessed | — | — | — | — | — | — |
| `53bccd73bbfebc9b31ffc5725fa12ac8e73b59c6` | Implement virtual scrolling for checkbox performance (Session 14) (#148) | unknown | unprocessed | — | — | — | — | — | — |
| `345a322952f3e870bccc42740cb61b8e3a53b0ee` | Re-enable CDK virtual scrolling for select-all performance (#149) | unknown | unprocessed | — | — | — | — | — | — |
| `f9dac34a11b0d1af0f0096661406819d28b63d2f` | Claude/bulk file actions session 16 ibn c6 (#151) | unknown | unprocessed | — | — | — | — | — | — |
| `2a016f9f96788b47f1140ddd4e98612c4c39c4e0` | Move file action buttons into selected row and fix UI issues | unknown | unprocessed | — | — | — | — | — | — |
| `fd5b0ac27ea4a5286bc6b8ad4db1cc1aba694a9b` | Fix virtual scrolling and improve UX in file component (Session 17) | unknown | unprocessed | — | — | — | — | — | — |
| `3262cd2566fb47a1eb797027fa73f0541ba83ae8` | Fix race conditions and memory leak in bulk selection (Session 18) | unknown | unprocessed | — | — | — | — | — | — |
| `4533679f0a0193f19bd8bf083bec3229e9d617fd` | Complete bulk actions critical fixes (Phases 2-5) | unknown | unprocessed | — | — | — | — | — | — |
| `7297af2890b4ee0ffc4f637e08f50ae9e28f8462` | Fix critical code review issues and add comprehensive test coverage | unknown | unprocessed | — | — | — | — | — | — |
| `ea8efbb57a6449898cfbeb4063be840c84fb3ab1` | docs: map existing codebase | unknown | unprocessed | — | — | — | — | — | — |
| `8c1214c8c0ccd22973da35844bab5354dbf75f10` | docs: initialize project | unknown | unprocessed | — | — | — | — | — | — |
| `16ccb3e5c36756d7023bcb42e48284e3a0f9d386` | chore: add project config | unknown | unprocessed | — | — | — | — | — | — |
| `335e77c329928bc60d59fb2a07b79a6c83c8fe4d` | docs: research Bootstrap 5 SCSS styling patterns | unknown | unprocessed | — | — | — | — | — | — |
| `44e78f8063a8aaa459347172418e5606e8a13cc1` | docs: define v1 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `14d5fe39148a25c3b83692385fed2d2dc3c5c3bf` | docs: create roadmap (5 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `502342c312d2339a74058046f7959c113db531dd` | docs(01): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `b2ad44eeeb3eb04e674e6dfaccc094e0c317e541` | Remove tests for intentionally removed Select all matching feature (#152) | unknown | unprocessed | — | — | — | — | — | — |
| `42c51ee64f77d7a43ca3cf05220af4043d51f7f4` | docs(phase-1): research Bootstrap SCSS setup domain | unknown | unprocessed | — | — | — | — | — | — |
| `f501f95e7cd140d09ec54a66c905ae4e8d58ca41` | docs(01): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `4bfdea3366bcb2d2a40f70d44930a0f6b7b692be` | feat(01-01): create Bootstrap SCSS infrastructure files | unknown | unprocessed | — | — | — | — | — | — |
| `eed016f1bb4705200a01df35d4991e3660e6b37f` | feat(01-01): update styles.scss with Bootstrap SCSS imports | unknown | unprocessed | — | — | — | — | — | — |
| `698bb09ded4a22c2e40632d9771d0047cb48e2d2` | feat(01-01): update angular.json build configuration | unknown | unprocessed | — | — | — | — | — | — |
| `e981c6bf1663819d669a5b522fdf5b8206aa5234` | fix(01-01): add ARM64 support using Chromium in test Dockerfile | unknown | unprocessed | — | — | — | — | — | — |
| `8155866072c0b8d91a089894ba29e3a6575a9325` | docs(01-01): complete Bootstrap SCSS setup plan | unknown | unprocessed | — | — | — | — | — | — |
| `165e4b6a07affdafec188189ecac5b40989f3f84` | docs(01): complete Bootstrap SCSS Setup phase | unknown | unprocessed | — | — | — | — | — | — |
| `3a6fd457ca3077189e887a149c85196db7962651` | docs(2): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `c32ea8a6fc554ad8dfc226865bbbb517e61b576b` | docs(02): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `34f7a90dcfb8a7dabfe89f751c29c077b4cf856e` | feat(02-01): define Bootstrap theme color overrides | unknown | unprocessed | — | — | — | — | — | — |
| `af6fd073c3a121eaa07862db150bceeb3fc7bd1f` | refactor(02-01): update _common.scss to use Bootstrap shade-color function | unknown | unprocessed | — | — | — | — | — | — |
| `d25046c9e3842a36c73f273a9e899e621d756b55` | docs(02-01): complete Color Variable Consolidation plan | unknown | unprocessed | — | — | — | — | — | — |
| `d9dc461fee50eef0b6f78b5e31f5b335474468e7` | feat(02-02): migrate autoqueue to Bootstrap semantic colors | unknown | unprocessed | — | — | — | — | — | — |
| `0f24cbf51e7fc34eee374484aee339dee65c4681` | feat(02-02): migrate logs page to Bootstrap alert variables | unknown | unprocessed | — | — | — | — | — | — |
| `1fc116c52c78997def06481bcbc2b2e2ea5b50cf` | feat(02-02): migrate option and file-list to Bootstrap variables | unknown | unprocessed | — | — | — | — | — | — |
| `406ba687c41cc78360eebe00805c1131dc92c57a` | docs(02-02): complete component color migration plan | unknown | unprocessed | — | — | — | — | — | — |
| `ee6a1487cc0c1f5eef7e972c15e42afa3533d13c` | docs(phase-2): complete Color Variable Consolidation phase | unknown | unprocessed | — | — | — | — | — | — |
| `7a45c9773b4073075ee1b98ce70fe9ed5b72fd8e` | docs(03): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `daffbc6303e36da5fe3254c81575986cdafc8738` | docs(03): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `d42bb3992078bf4394f0ade03657eea2ee84c295` | feat(03-01): migrate selection banner to secondary colors | unknown | unprocessed | — | — | — | — | — | — |
| `8349898576b11ae0f1bc398494e75bf37de18876` | feat(03-01): add hover transition to file rows | unknown | unprocessed | — | — | — | — | — | — |
| `51a18350a644d867a16991408edd1406f5d23ab3` | docs(03-01): complete selection color unification plan | unknown | unprocessed | — | — | — | — | — | — |
| `b1b96d6d1e7e842a08ba22eee5769ccc40a8beb5` | docs(03): add phase research | unknown | unprocessed | — | — | — | — | — | — |
| `042e53efb95475c655c83be5a41789d20ed37f22` | docs(03): complete selection-color-unification phase | unknown | unprocessed | — | — | — | — | — | — |
| `be318d5460ff9a2ae2572d0ac209913c6efaa836` | docs(04): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `884642733b9ba6a63a635813928b78cd9f0c69c2` | docs(04): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `5ec43a23c3120aac36765b5f06cb47abfefba060` | docs(04): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `cedd39b48fddda3eb32cedb6b351fe75e736cc0a` | feat(04-01): migrate file-actions-bar button variants and sizing | unknown | unprocessed | — | — | — | — | — | — |
| `8ccde77ed85a23a9f0ee254411eba2b040697b4a` | feat(04-01): migrate bulk-actions-bar button variants and sizing | unknown | unprocessed | — | — | — | — | — | — |
| `f857cac373a0f8e1ae130eb39c28e5ede2d726a4` | feat(04-02): migrate hidden .actions to Bootstrap buttons | unknown | unprocessed | — | — | — | — | — | — |
| `0887499449160a865c581e5be924be06c7423066` | docs(04-01): complete file actions button standardization plan | unknown | unprocessed | — | — | — | — | — | — |
| `1d6aa0137901d4e2be87e5bdb82f7fd73bcb9ad8` | docs(04-02): complete hidden actions Bootstrap migration plan | unknown | unprocessed | — | — | — | — | — | — |
| `fb41449f2b7b2f6eb7de74ae1b8efd1ada68f6fb` | docs(04): complete Button Standardization - File Actions phase | unknown | unprocessed | — | — | — | — | — | — |
| `a3e38e14c993c78fe4bbf6455e35dae6307f7ed3` | docs(05): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `55ace99c493112429f9245f0ba85bd7138a19c06` | docs(05): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `dc6efbd3383c2c2a98068d641f14e9d86def742e` | feat(05-01): migrate Settings Restart button to Bootstrap | unknown | unprocessed | — | — | — | — | — | — |
| `cb7b1aea6b291563aeac70b1e2fab3c9f0231a42` | feat(05-01): migrate AutoQueue add/remove buttons to Bootstrap | unknown | unprocessed | — | — | — | — | — | — |
| `f60085423eb68730910016445607c9ace5a15bdb` | docs(05-01): complete Settings and AutoQueue button standardization plan | unknown | unprocessed | — | — | — | — | — | — |
| `69332ad39058a2c6156dfd8bd116ea1304136b6b` | refactor(05-02): remove @extend %button from Logs page SCSS | unknown | unprocessed | — | — | — | — | — | — |
| `509dae3e9664a849286c22dff6c0b99782a9b09e` | refactor(05-02): remove %button placeholder from _common.scss | unknown | unprocessed | — | — | — | — | — | — |
| `61d6bc1c493b7202299a5a2bf3db56c65bbd6ef8` | docs(05-02): complete button placeholder cleanup plan | unknown | unprocessed | — | — | — | — | — | — |
| `310a9e15654bf70def980f9afe3a20fedc52a658` | docs(phase-5): complete Button Standardization - Other Pages phase | unknown | unprocessed | — | — | — | — | — | — |
| `4090f00b3500a56798eaa02b6e34e634d252f278` | chore: complete v1.0 Unify UI Styling milestone | unknown | unprocessed | — | — | — | — | — | — |
| `15aee39be6869941200bbeee04c0e4cd35d5b31f` | fix: address code review issues from v1.0 milestone | unknown | unprocessed | — | — | — | — | — | — |
| `2033297bba95a5e236cdcab2ff04e43e64d8f352` | merge: resolve conflicts with origin/master | unknown | unprocessed | — | — | — | — | — | — |
| `641ea852d2870ff0d0a9f1e8821642d6a4f5ef71` | fix(e2e): update autoqueue selectors for Bootstrap buttons | unknown | unprocessed | — | — | — | — | — | — |
| `524df5335396f655f82a61283a6839faae935b7c` | Merge pull request #153 from thejuran/claude/unify-ui-styling-gsd | unknown | unprocessed | — | — | — | — | — | — |
| `e775d8f85030858273264ee8cf83498a28c80b3e` | fix: prevent AutoQueue from re-queueing already-downloaded files | unknown | unprocessed | — | — | — | — | — | — |
| `fa1e6e8177fa3c756517be294493b06e91886e0e` | test: add mock for is_file_downloaded in AutoQueue tests | unknown | unprocessed | — | — | — | — | — | — |
| `10cf823993466773d3f4856dc3bb6d724eae7d23` | Merge pull request #154 from thejuran/claude/unify-ui-styling-gsd | unknown | unprocessed | — | — | — | — | — | — |
| `debde0c1da57aacf92e44aa4e558a9c74c6a88fe` | docs: capture todo - Fix Safari URL bar color bleed | unknown | unprocessed | — | — | — | — | — | — |
| `ed56490514942ebb148fa985a4d8a732b5ea81e5` | docs: capture todo - Fix AutoQueue re-queueing already-downloaded files | unknown | unprocessed | — | — | — | — | — | — |
| `721e694fcf63e6f06e12a610962b2c9a313795eb` | fix: prevent Safari 26+ toolbar color bleed from alert banners | unknown | unprocessed | — | — | — | — | — | — |
| `08d7a5fb2eab4ee911321a7d47f88d11992884f5` | docs: mark Safari color bleed todo as done | unknown | unprocessed | — | — | — | — | — | — |
| `bd81180bc7b9b8ec5cf49a2f09519b9de01b19fb` | docs: mark AutoQueue todo as done (already implemented) | unknown | unprocessed | — | — | — | — | — | — |
| `aa0ae75d654aca5169f929a567c7091ec12208dd` | docs: start milestone v1.1 Dropdown & Form Migration | unknown | unprocessed | — | — | — | — | — | — |
| `40ab1fe6e3c7065459b232c1098ad096c2f913b4` | docs: define milestone v1.1 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `2bc4c79400fa51c840704460f770bbed2f7a0517` | docs: create milestone v1.1 roadmap (3 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `271169d2ef3dd859d8fe496e4710477f8f47800d` | docs(06): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `4b9c39aafd57b6b00a3c5df80f7f6b239a082932` | docs(phase-6): research Bootstrap dropdown migration | unknown | unprocessed | — | — | — | — | — | — |
| `13db297daabb631fa996f2c9eca99421f98c0b26` | docs(06): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `9d275fd4fd6f058067ffebd2d681d4dda98face7` | feat(06-01): add Bootstrap dark dropdown theme overrides | unknown | unprocessed | — | — | — | — | — | — |
| `da35b7eb9332d8c2d8fd7fd413140908fa37b1fd` | refactor(06-01): migrate dropdowns to Bootstrap and remove SCSS placeholders | unknown | unprocessed | — | — | — | — | — | — |
| `b3011297982218b6e6f628fe3b3601d6a8e9f582` | feat(06-01): add close-on-scroll behavior for dropdowns | unknown | unprocessed | — | — | — | — | — | — |
| `778ec7027ecfd0b3e8a2d7b768e5a39bb606f89b` | docs(06-01): complete Dropdown Migration plan | unknown | unprocessed | — | — | — | — | — | — |
| `0ea643db6e48a4763ce3a989fdb9154884570bd2` | docs(phase-06): complete Dropdown Migration phase | unknown | unprocessed | — | — | — | — | — | — |
| `15168d8b83af0ea1be27f8c8b8a446a86aed95f1` | docs(07): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `b1149fee428f1d03ce5fc6141d5fe30652f8ebf2` | docs(phase-07): research form input standardization domain | unknown | unprocessed | — | — | — | — | — | — |
| `49ded7c2a37a7d30bc212a22011e76d0f7d060a3` | docs(07): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `6f8c1d796a47e242978b42e6cc356e21dba263e0` | feat(07-01): add Bootstrap form variable overrides for teal focus states | unknown | unprocessed | — | — | — | — | — | — |
| `472e82d76bb7deb694e5f5b9e95d2113f259dd22` | feat(07-01): add dark theme form overrides for consistent appearance | unknown | unprocessed | — | — | — | — | — | — |
| `35d495edcee1326e4966e59aa24832372e50481b` | refactor(07-01): clean up option.component.scss for dark theme compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `8053800a87762fad4c2c556b22a7bca0d9da71a6` | docs(07-01): complete form input standardization plan | unknown | unprocessed | — | — | — | — | — | — |
| `9f3ac4f11e20b31123de31c006402c76e9031728` | docs(06-01): update plan with animation details | unknown | unprocessed | — | — | — | — | — | — |
| `d4ac7807103b4075a20204faf6beffc746781ea3` | docs(phase-7): complete form input standardization phase | unknown | unprocessed | — | — | — | — | — | — |
| `5e77f99211b5f882e897eef5d72c7ca8d2a7ab0e` | docs(08): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `a21c2e857f129ef9a0fcd04cbc911640c5258559` | docs(phase-8): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `bceec5702467dfc3b1d2b270c4bfae2550849e08` | docs(08): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `483b2a5ca8c377215ede4420d7b41b6215a77b0f` | docs(08-01): complete Test Suite & SCSS Cleanup plan | unknown | unprocessed | — | — | — | — | — | — |
| `61d81e91e4c8f9f8e7c49d9329fa594b414eec3e` | docs(08-02): complete Visual QA Walkthrough plan | unknown | unprocessed | — | — | — | — | — | — |
| `84f2988ec16477f4440a4c92b475f8689972fa1c` | docs(08): complete Final Polish phase | unknown | unprocessed | — | — | — | — | — | — |
| `ab807887f19a4df15df562d398c78db41d55bc7f` | chore: complete v1.1 milestone | unknown | unprocessed | — | — | — | — | — | — |
| `6ce8086328384f7bb4eb7dff21fe1c2d171152e7` | fix(ui): make file-actions-bar sticky when scrolling | unknown | unprocessed | — | — | — | — | — | — |
| `ebe0cd69265f867e310c308f8e75b3b8b7972194` | fix(ui): enable internal scrolling for file list viewport | unknown | unprocessed | — | — | — | — | — | — |
| `f3af3fb35aeb1596d261d22491ffaea2a3d912ab` | fix: prevent re-downloading externally deleted files | unknown | unprocessed | — | — | — | — | — | — |
| `3cce918f053305d5c384987f5a9a83d4df341a87` | docs: start milestone v1.2 UI Cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `61d96e102305d0e01d88a4d76ca27a9aff2fb74a` | docs(9): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `10c949f089b8830a54775c62d3e642b4c1d56020` | docs(09): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `0fd31eaae98de6ecccff28eb8561c3fe837ed81b` | refactor(09-01): remove Details button and showDetails state | unknown | unprocessed | — | — | — | — | — | — |
| `70fc557d7f3243158f889c08d3e20ee88b1aec3e` | refactor(09-01): remove Pin button and pinFilter state | unknown | unprocessed | — | — | — | — | — | — |
| `118a561379defcb9048779e671af931b5255c8de` | docs(09-01): complete remove-obsolete-buttons plan | unknown | unprocessed | — | — | — | — | — | — |
| `9f326c7e4b0e41b52ec2f7dde04de9ab9a3f1238` | docs(phase-09): complete remove obsolete buttons phase | unknown | unprocessed | — | — | — | — | — | — |
| `f9b96bba9b97860a652e90430444c711dab19479` | chore: complete v1.2 milestone | unknown | unprocessed | — | — | — | — | — | — |
| `4b21dfd6d6e1d6d1b2d7f0b65d7125c92bb30a03` | docs: start milestone v1.3.0 Polish & Clarity | unknown | unprocessed | — | — | — | — | — | — |
| `4d04b6cc27639202ad9490caec6c9960e3ec922b` | docs: define milestone v1.3.0 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `7acd6270fb3fe101c11e417a75d6a803ef03814d` | docs: create milestone v1.3.0 roadmap (2 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `07cce9a96842ae2863fa15ce3414ccf246d02726` | docs(10): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `be537460187b7db1b94083f1aef8ff110584f274` | docs(10): create phase plan for lint cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `03afc160bff8bdfe721b0b33bfccb96fb440c29e` | style(10-01): fix var declaration and quote style issues | unknown | unprocessed | — | — | — | — | — | — |
| `4662f0b6e039049429aa99ad51e170c8d3d02e63` | fix(10-01): add intent comments to empty functions | unknown | unprocessed | — | — | — | — | — | — |
| `974f3466b8c2f9c18fd99e44f0edfda3e5221ef4` | fix(10-02): add explicit return types to base services | unknown | unprocessed | — | — | — | — | — | — |
| `6c21606ad81d1361d21b18187ff015afdee04045` | docs(10-01): complete style and empty function fixes plan | unknown | unprocessed | — | — | — | — | — | — |
| `6b2f25f7281dce130bb52e0c5ada14bd2b14732b` | fix(10-03): add return types to common utilities | unknown | unprocessed | — | — | — | — | — | — |
| `7e099bd8526b6a70d104372a86f2b960ec4257f2` | fix(10-02): add explicit return types to domain services | unknown | unprocessed | — | — | — | — | — | — |
| `3e4043e2826381e3ef48c9ff6c3ce35ff1e2b6ef` | fix(10-02): add explicit return types to utility services | unknown | unprocessed | — | — | — | — | — | — |
| `dd999423254191fce4b8b7581b5bae253fd5c8f4` | fix(10-03): add return types to page components | unknown | unprocessed | — | — | — | — | — | — |
| `11fd451c3a59de1f9abcd7477a864e89d3bac873` | docs(10-02): complete service layer return types plan | unknown | unprocessed | — | — | — | — | — | — |
| `3925aebb1842f66ff8e3059bb12da5a638c79d1f` | fix(10-03): add return types to test files | unknown | unprocessed | — | — | — | — | — | — |
| `803eac6c66a40cd33dd67340e99dc87a8d9b2441` | docs(10-03): complete pages/common/tests return types plan | unknown | unprocessed | — | — | — | — | — | — |
| `353ba4655caf06b7927ed4f3ba683f1d20951caf` | fix(10-04): replace any types with proper TypeScript types in application code | unknown | unprocessed | — | — | — | — | — | — |
| `6233abfa601c640fea3fd732c191b5ab527da5a9` | fix(10-04): replace any types with proper TypeScript types in test code | unknown | unprocessed | — | — | — | — | — | — |
| `09581ea02ee55e997c456487cb651cc125267221` | fix(10-04): replace non-null assertions with optional chaining | unknown | unprocessed | — | — | — | — | — | — |
| `d312698f7a6906b635ce523e4709766b9487d3f6` | docs(10-04): complete any types & non-null assertions plan | unknown | unprocessed | — | — | — | — | — | — |
| `9f6fe614149cce0411e91f54625c7fb17fc13872` | docs(phase-10): complete lint-cleanup phase | unknown | unprocessed | — | — | — | — | — | — |
| `187f6e8e6ecaae135ab3cc63e7ab132608dd76b8` | docs(11): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `306d123d1b4f8a9038494d21b4d41a7dc7b00cd6` | docs(11): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `8b9d66dbba1bd3eb8be562b5455f5d545805c1a6` | docs(11): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `45dc4de152d223d87ed4a7d11037d476a73a155e` | fix(11): revise plan to use on-demand count computation | unknown | unprocessed | — | — | — | — | — | — |
| `c630cf5cc05e94e2d70b636585e96c552084cfb2` | feat(11-01): add on-demand count computation triggered by dropdown open | unknown | unprocessed | — | — | — | — | — | — |
| `821c730bcdb8f9227f262455371507376d6db54d` | feat(11-01): display counts in status dropdown with disabled states | unknown | unprocessed | — | — | — | — | — | — |
| `f09ded1e96d1e6f57f6acf26bcd45449c0c4daf2` | docs(11-01): complete status dropdown counts plan | unknown | unprocessed | — | — | — | — | — | — |
| `541f04e375d12c3c76f97b38cc7bd99901c8ead6` | docs(phase-11): complete status dropdown counts phase | unknown | unprocessed | — | — | — | — | — | — |
| `800399d61a6cf0ea5a61be2846420c0d6909eccf` | chore: complete v1.3 milestone | unknown | unprocessed | — | — | — | — | — | — |
| `b9c0612828f1a1c599973fd433c761d10c9628ed` | fix: prevent re-queuing of evicted downloaded files | unknown | unprocessed | — | — | — | — | — | — |
| `e45bf81680b0ba90052915cb94570416de0eb312` | chore: bump version to 1.2.0 | unknown | unprocessed | — | — | — | — | — | — |
| `e662c50f236df3a4a3deb55977d893f026143dd2` | fix: use BoundedOrderedSet in model_builder tests | unknown | unprocessed | — | — | — | — | — | — |
| `6c08a775c26228e600add857538d15f2abe3abd6` | docs: add v1.2.0 changelog entry | unknown | unprocessed | — | — | — | — | — | — |
| `7ad47dc7db562528f1d611c937a08e7fad3aeb8b` | docs: update install page and README for v1.2.0 | unknown | unprocessed | — | — | — | — | — | — |
| `11aca9972238266ffd7e1c4b4c2ca89660c6cc5f` | docs: update home page platform table | unknown | unprocessed | — | — | — | — | — | — |
| `64142c4e9702a34795eae0d9f58c5341d5ae0877` | docs: update home page with fork reference and new screenshot | unknown | unprocessed | — | — | — | — | — | — |
| `93bba55788a1bb15585458a4f1bf34a85fcda32e` | docs: add documentation update steps to release checklist | unknown | unprocessed | — | — | — | — | — | — |
| `77b7ee18e2a14aa6a57518830421d73db02c0b63` | docs: update README screenshot to current UI | unknown | unprocessed | — | — | — | — | — | — |
| `bf0db6e9ee2984ea04937ebe889d44e7b0dbb86a` | fix: use ViewFile computed properties for action availability in file-actions-bar | unknown | unprocessed | — | — | — | — | — | — |
| `f1b6bf2504c62b6b927bc1fcd3b7bf4117699084` | docs: start v1.2.1 release notes | unknown | unprocessed | — | — | — | — | — | — |
| `d17c957c4701aa04d622c4c8b8c49a7229ca2121` | docs: start milestone v1.4 Sass @use Migration | unknown | unprocessed | — | — | — | — | — | — |
| `c0dad8ceb0bc59f28e295f90b9a7cea996ad534d` | docs: complete Sass @use migration research | unknown | unprocessed | — | — | — | — | — | — |
| `21602b4f82dbf61c49788064dd2468ad0f8c559c` | docs: v1.4 research complete, roadmap and requirements defined | unknown | unprocessed | — | — | — | — | — | — |
| `e7f020abab61bf3e376c5520d305b09f9aba2c7d` | docs(13): create phase plan for styles entry point migration | unknown | unprocessed | — | — | — | — | — | — |
| `b603110b95a30acdd1ce14e94215c727fcf4c77e` | Phase 13: Migrate styles.scss entry point to @use for application modules | unknown | unprocessed | — | — | — | — | — | — |
| `1001fdfd57a7c05c0e8281e9fa9841c82b041db7` | docs(14-01): complete v1.4 Sass @use migration validation | unknown | unprocessed | — | — | — | — | — | — |
| `e94d89ac1db05dfb1e0daab180ee81368a7d8c3b` | chore: archive v1.4 Sass @use Migration milestone | unknown | unprocessed | — | — | — | — | — | — |
| `c55e4c06a1a1f3a63ee3b60fc17a4613e1f71d55` | docs: start v1.5 Backend Testing milestone | unknown | unprocessed | — | — | — | — | — | — |
| `56463ad4380c45790b55d04e6e493d9a5afc90b3` | feat(15-01): add coverage tooling, shared fixtures, and Makefile target | unknown | unprocessed | — | — | — | — | — | — |
| `86544fe643d57c97ded38e27810f91a6d8748ae8` | docs(15-01): complete coverage tooling & shared fixtures plan | unknown | unprocessed | — | — | — | — | — | — |
| `de38e44be8727a5470c0b87f0560a734aff79234` | chore: update state for Phase 15 completion | unknown | unprocessed | — | — | — | — | — | — |
| `537456c7fa27e00dd6fee6d0454aa3d910814f54` | docs(16): create phase plan for common module tests | unknown | unprocessed | — | — | — | — | — | — |
| `ea8a655312bfc7df4f3977b3a95dbd7d09fad137` | docs(16): plan Phase 16 — Common Module Tests | unknown | unprocessed | — | — | — | — | — | — |
| `91fa01014f157e3a14b63dd6c63dafc491289417` | test(16-01): add tests for constants, error, and localization modules | unknown | unprocessed | — | — | — | — | — | — |
| `5e39fdeb2bdbb5f2339293020220a259d17a40ae` | test(16-01): add tests for context and types modules | unknown | unprocessed | — | — | — | — | — | — |
| `23b7052e0e5153452202b1b3c954ac18aeb45718` | docs(16-01): complete common module tests plan | unknown | unprocessed | — | — | — | — | — | — |
| `2d866f1d12d476236e332c46e3350964b4d879cf` | chore: update state for Phase 16 completion | unknown | unprocessed | — | — | — | — | — | — |
| `94d34ba841ee60b77313c65b6adf2a6fb8e1f994` | docs(17): create phase plans for web handler unit tests | unknown | unprocessed | — | — | — | — | — | — |
| `3a8317642f249862d47299d534090e36738ac528` | docs(17): plan Phase 17 — Web Handler Unit Tests | unknown | unprocessed | — | — | — | — | — | — |
| `1896c58c06f7eeb22d3c43bf774dbea3bdf352d3` | test(17-01): add unit tests for 4 web request/response handlers | unknown | unprocessed | — | — | — | — | — | — |
| `637ab8e0f5282b6e4baf36aeedfa079013d1e0d3` | test(17-02): add unit tests for HeartbeatStreamHandler and ModelStreamHandler | unknown | unprocessed | — | — | — | — | — | — |
| `42e1b2482ccecd9d2172cb661412c6c4045e2ec0` | docs(17-01): complete request/response handler unit tests plan | unknown | unprocessed | — | — | — | — | — | — |
| `074630c01f1dab79e8c7ee1c7fbe2df56b5b89fe` | test(17-02): add unit tests for StatusStreamHandler and StatusListener | unknown | unprocessed | — | — | — | — | — | — |
| `9f0795d7b356f9f75a8dda8943695af54c5aa049` | docs(17-02): complete stream handler unit tests plan | unknown | unprocessed | — | — | — | — | — | — |
| `494ff3d505f8c63e0ef3deb4691a69f33978cdc9` | test(18-01): add comprehensive unit tests for Controller class | unknown | unprocessed | — | — | — | — | — | — |
| `2fbad19775526112ae29bb452556897c17273dd6` | docs(18-01): complete controller unit tests plan | unknown | unprocessed | — | — | — | — | — | — |
| `e9ac2514904821bfaef1e191e258a72706c125de` | test(18-02): add 54 controller pipeline and ControllerJob unit tests | unknown | unprocessed | — | — | — | — | — | — |
| `03869bcec77c791d7ca77276f0654b9d226ad3c1` | docs(18): complete Phase 18 state updates and summaries | unknown | unprocessed | — | — | — | — | — | — |
| `1a71ac086c623b65efa0bdb3614bc9b9d9707c03` | chore: archive v1.5 Backend Testing milestone | unknown | unprocessed | — | — | — | — | — | — |
| `5c3526f706c6b76aec2ee14c60b194b3a0566d0d` | feat: add Remote Added and Local Added timestamp columns to file list | unknown | unprocessed | — | — | — | — | — | — |
| `569622eb3b39d6ffe3a9e27cbc5dc5143fe8c96a` | fix: fall back to st_ctime when st_birthtime unavailable on Linux | unknown | unprocessed | — | — | — | — | — | — |
| `ab39d7f922af42630c4c0b4bc846bb652aac5aba` | chore: bump version to v1.3.0 | unknown | unprocessed | — | — | — | — | — | — |
| `a9ce3dc19f56336df52187ab0a1da89f8b43856b` | docs: add v1.3.0 changelog and update install pinned version | unknown | unprocessed | — | — | — | — | — | — |
| `7d85e6aed9233e07d92c255f075e7533007a5686` | fix: patch created timestamps in integration tests for Linux st_ctime fallback | unknown | unprocessed | — | — | — | — | — | — |
| `0fff1466b73e1289590b409df993f58fa4368b40` | docs: capture todo - clean up CI test runner warnings | unknown | unprocessed | — | — | — | — | — | — |
| `a0b0e5fb2b70db373a7107d0c56303ef839bb838` | fix: add checkout step to publish-deb job and handle existing releases | unknown | unprocessed | — | — | — | — | — | — |
| `55a45c440b6d8316eb864844e45ac2e0746c8b2d` | docs: start milestone v1.6 CI Cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `56e2bfe068106ba566e3617ed7ea886d2c03cbae` | docs: define milestone v1.6 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `ce9259ecf3831b38987f57524cd790b125ee4846` | docs: create milestone v1.6 roadmap (2 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `c4d8b4fc5d526e0a48dacff906be5f7a1ddcb1a8` | docs(20): create phase plan for CI workflow consolidation | unknown | unprocessed | — | — | — | — | — | — |
| `eab61461f5cfff854bc5637979a6f982be192044` | feat(20-01): add :dev Docker publishing job to master.yml | unknown | unprocessed | — | — | — | — | — | — |
| `268a86b72d78994acf0e801c3e9099e3158094ec` | feat(20-01): delete docker-publish.yml and update CLAUDE.md | unknown | unprocessed | — | — | — | — | — | — |
| `2ffe1588d309ec1d837c1bef1ed9c01524a56ff3` | docs(20-01): complete CI workflow consolidation plan | unknown | unprocessed | — | — | — | — | — | — |
| `fb4dcacf35ed9c9080f1c5c8bbb95266cce61c49` | docs(phase-20): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `9eafceabebe11f054725fec53b6046d91ec8cf39` | feat(21-01): suppress pytest cache and cgi deprecation warnings | unknown | unprocessed | — | — | — | — | — | — |
| `c3d369f1e1f4a7b6b15f52959a87a7dcb5da24f7` | docs(phase-21): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `2c6d72293a78ce6daf8f8490ed09baa5ac17da6f` | chore: archive v1.6 CI Cleanup milestone | unknown | unprocessed | — | — | — | — | — | — |
| `bc35c346974a2a41600d2116bcfe4da12f9391c9` | docs: start milestone v1.7 Sonarr Integration | unknown | unprocessed | — | — | — | — | — | — |
| `6c7b38423e5bdba1cdb1e56ed866beeaa94caacd` | docs: complete v1.7 Sonarr integration research | unknown | unprocessed | — | — | — | — | — | — |
| `9cc22591ec6b080ef97d14b9f73f89bde69b9bb2` | docs: define milestone v1.7 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `261c640f2c567145ab069a7dce1faeb929ee49fa` | docs: create milestone v1.7 roadmap (4 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `dc44ab93f59259909bfd5b45a0d76ee0e3a22857` | docs(22): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `724270465656e62998fae3ece2d9f31f0af86aa1` | feat(22-01): add Config.Sonarr InnerConfig class | unknown | unprocessed | — | — | — | — | — | — |
| `312f4604c1f6d927f28a4751396c528ab25d17e4` | feat(22-01): add Sonarr defaults to _create_default_config | unknown | unprocessed | — | — | — | — | — | — |
| `850f5006b3063a75d7485831ee7d2d6f096f9a4a` | feat(22-01): add Sonarr test connection endpoint | unknown | unprocessed | — | — | — | — | — | — |
| `e14ed8c1ab9890bdf9cd6c9eb5cec997f0d6713d` | docs(22-01): complete Backend Config + Test Connection plan | unknown | unprocessed | — | — | — | — | — | — |
| `587e8de2febf1f5a139027401f9174715893be48` | feat(22-02): add ISonarr interface and SonarrRecord to config model | unknown | unprocessed | — | — | — | — | — | — |
| `0b53f93f5b968eb05d6bf69fcd7fd714e893b263` | feat(22-02): add testSonarrConnection method to ConfigService | unknown | unprocessed | — | — | — | — | — | — |
| `2b89bbcc4659d77f2a2157c38b68e22b1aaab78f` | feat(22-02): add *arr Integration section to settings template | unknown | unprocessed | — | — | — | — | — | — |
| `b5c92463c9fd8878090db32661ab1c18ba30140d` | feat(22-02): add test connection logic and state to settings component | unknown | unprocessed | — | — | — | — | — | — |
| `5f857957c79b2ac3604f70f6f44dc3b18db197c2` | feat(22-02): add test connection styles | unknown | unprocessed | — | — | — | — | — | — |
| `6c91ec334fbb053e92b19b268a46dd2153d38eea` | fix(22-02): remove inferrable type annotation to pass lint | unknown | unprocessed | — | — | — | — | — | — |
| `d8ba19b82a5ccdd56f67d10d4c4f8e9e04f230ab` | docs(22-02): complete Frontend Settings UI plan | unknown | unprocessed | — | — | — | — | — | — |
| `159206a1486495ffa5d93887785f76052150d957` | docs: update state for Phase 22 completion | unknown | unprocessed | — | — | — | — | — | — |
| `6eb2eb2fe51c82472d2b5e651451ccbb116541d6` | docs(23): create phase plan for API Client Integration | unknown | unprocessed | — | — | — | — | — | — |
| `c9f5aae8aaec559413d5c7085a34dcbddead59ec` | docs(23): fix memory monitor assertion count in test plan | unknown | unprocessed | — | — | — | — | — | — |
| `6420549983176eddbabe4af3922774a26da9306e` | feat(23-01): add SonarrManager, imported_file_names persist, Controller integration | unknown | unprocessed | — | — | — | — | — | — |
| `aec281c1460749b3f24943979fd869be460a5e38` | test(23-02): add SonarrManager tests, update persist and controller tests | unknown | unprocessed | — | — | — | — | — | — |
| `e7ebb963be85ab0e1da26971329099693903a8bf` | docs: update state for Phase 23 completion | unknown | unprocessed | — | — | — | — | — | — |
| `a613da88a21632f245aed9fddbdb10122558a423` | feat(24-01): add ImportStatus enum and import_status property to ModelFile | unknown | unprocessed | — | — | — | — | — | — |
| `9444eb209a8a386b8ca427532044bff2dff14551` | feat(24-01): serialize import_status in SerializeModel | unknown | unprocessed | — | — | — | — | — | — |
| `5b52854bf837b1618ab6d6726cb7c7e2de8d9330` | feat(24-01): set import_status on model files in Controller | unknown | unprocessed | — | — | — | — | — | — |
| `9a3301a13d2933d0666407184fdf3e673b4605e4` | feat(24-01): add import_status to frontend ModelFile | unknown | unprocessed | — | — | — | — | — | — |
| `4f2d84e8c69ae0a2f053baaf57709c453516e7ed` | feat(24-01): add importStatus to ViewFile and ViewFileService mapping | unknown | unprocessed | — | — | — | — | — | — |
| `3a0031add83793a2f57ba55be1877158a87f59ad` | feat(24-01): create ToastService for ephemeral notifications | unknown | unprocessed | — | — | — | — | — | — |
| `6bf18e7cc02604c3c16c52460be734e25958c182` | feat(24-01): integrate toast container into app component | unknown | unprocessed | — | — | — | — | — | — |
| `f0bde8e6bfb2cfa4ec2045b6471107ec3690bde4` | docs(24-01): complete import_status pipeline and toast service plan | unknown | unprocessed | — | — | — | — | — | — |
| `b98b68bedcb470eb31b156f8774e19fbdb388a56` | feat(24-02): add import status badge to file component template | unknown | unprocessed | — | — | — | — | — | — |
| `50cb979af86bd0d9b7178b0f7e9686d7bcc70986` | feat(24-02): add import badge styling to file component SCSS | unknown | unprocessed | — | — | — | — | — | — |
| `13d8e961d14b64563ba8c58c750fcaeedc6717c4` | feat(24-02): add import detection and toast triggering in file-list component | unknown | unprocessed | — | — | — | — | — | — |
| `8f94beb187e57b4cedbfa32d846d0ee8c1eeb3a8` | docs(24-02): complete import badge and toast triggering plan | unknown | unprocessed | — | — | — | — | — | — |
| `428bd189048287885add5276758183cdc4cfef84` | fix(24): add import_status to expected ModelFile objects in model-file.service.spec.ts | unknown | unprocessed | — | — | — | — | — | — |
| `64d6447d17ad6206e644094b421d197317da397e` | docs(25): create phase plan for auto-delete with safety | unknown | unprocessed | — | — | — | — | — | — |
| `24a9698240691b45b7ae8d1c1d5f1a7b68c10fde` | feat(25-01): add Config.AutoDelete InnerConfig section and default values | unknown | unprocessed | — | — | — | — | — | — |
| `9929c512be75fc05b1b1aa3793f27941cbf22889` | feat(25-01): add frontend AutoDelete config model and settings UI section | unknown | unprocessed | — | — | — | — | — | — |
| `9f399fe0114b0be3fa89eac9ac6e9d88c43b783d` | docs(25-01): complete Config.AutoDelete section plan | unknown | unprocessed | — | — | — | — | — | — |
| `c8f01c54dd9dbf655d5ff50f702b50cfe1884a3a` | feat(25-02): add auto-delete scheduling and execution to Controller | unknown | unprocessed | — | — | — | — | — | — |
| `a4faeef7c260eb20a088fa80c6b6a727dfc055b3` | test(25-02): add unit tests for auto-delete scheduling, execution, and safety | unknown | unprocessed | — | — | — | — | — | — |
| `af07454290586b77c70b20a7e405f6334af7a84d` | docs(25-02): complete auto-delete timer logic plan | unknown | unprocessed | — | — | — | — | — | — |
| `02edd2378cdeb9baf3dcd4da1df1311c27c4f923` | fix(tests): add AutoDelete section to test_to_file golden string and test_has_section | unknown | unprocessed | — | — | — | — | — | — |
| `e053b96ee900ab9bad9bd7432399866ee0ba618a` | fix(config): default Sonarr and AutoDelete values for existing installs | unknown | unprocessed | — | — | — | — | — | — |
| `e6460717d47d36fe29a8e20e070b43e19efaf7e2` | test(22): complete UAT - 6 passed, 0 issues | unknown | unprocessed | — | — | — | — | — | — |
| `7f8b4b892cd6212bd4113bbacd21b7b00881e462` | test(23): complete UAT - 3 passed, 0 issues | unknown | unprocessed | — | — | — | — | — | — |
| `b356607e795f3e48264f284dc165d597c37fa58c` | fix(toast): prevent repeated import toast notifications on every poll cycle | unknown | unprocessed | — | — | — | — | — | — |
| `57b815a8b748d13d328d4af440023198542d4c5b` | chore: archive v1.7 milestone | unknown | unprocessed | — | — | — | — | — | — |
| `c5b71a9d676badab6b8064f2c79de1e0d8470b0f` | docs: start milestone v1.8 Radarr + Webhooks | unknown | unprocessed | — | — | — | — | — | — |
| `c72277e91a5893eb031b28dfa4a8dbbc8c396781` | docs: update v1.8 research with detailed webhook payloads | unknown | unprocessed | — | — | — | — | — | — |
| `1fbb08da7bbcef485c348fe2b71a2f11c2c5a4c3` | docs(26): create phase plans for Radarr config and shared *arr settings UI | unknown | unprocessed | — | — | — | — | — | — |
| `d12305e7da5ea3f4c8cab5bc9a7098f8b517838f` | feat(26-01): add Config.Radarr InnerConfig class with backward-compatible parsing | unknown | unprocessed | — | — | — | — | — | — |
| `815a19ded49e1fe2b151b5695c70f487e26b499a` | feat(26-01): add Radarr test connection endpoint and unit tests | unknown | unprocessed | — | — | — | — | — | — |
| `86126ce3d79a272faea8b36c78854f93eeccc0e0` | docs(26-01): complete Radarr backend config plan summary and update state | unknown | unprocessed | — | — | — | — | — | — |
| `d68fcf7423b76086e6ef5abb402058fbad05d52d` | feat(26-02): add IRadarr frontend model and testRadarrConnection service method | unknown | unprocessed | — | — | — | — | — | — |
| `7bebe91132a9e985fad0bd7fc8df556f766760c4` | feat(26-02): refactor *arr Integration UI with Sonarr and Radarr subsections | unknown | unprocessed | — | — | — | — | — | — |
| `29da0430084814ef3f238d2f8182a4405b8901a3` | docs(26-02): complete Radarr frontend config and shared *arr UI plan summary | unknown | unprocessed | — | — | — | — | — | — |
| `f480f8ef7140665ca1486d6572fb9772fe137db6` | docs(phase-27): research webhook import detection domain | unknown | unprocessed | — | — | — | — | — | — |
| `4c773c39edf2c58a0a5f85749b9de3f796b5af7a` | docs(27): create phase plans for webhook import detection | unknown | unprocessed | — | — | — | — | — | — |
| `84a365a79bca8a784de5738ab480e3b756336fa7` | feat(27-02): add webhook URL display to Settings page | unknown | unprocessed | — | — | — | — | — | — |
| `e3b554a88d48eddb67710ac6ee756df79f11e4f8` | docs(27-02): complete webhook URL display plan | unknown | unprocessed | — | — | — | — | — | — |
| `cd8d78ad40cd1bd47f7ed414e84a90bb8135e70f` | feat(27-01): replace SonarrManager with WebhookManager | unknown | unprocessed | — | — | — | — | — | — |
| `87d1aa77852962e0587e964b68ad1001f208a937` | test(27-01): update tests for WebhookManager migration | unknown | unprocessed | — | — | — | — | — | — |
| `b976c75b53418bfa270e5f01863af048b5bdeb93` | docs(27-01): complete webhook backend plan | unknown | unprocessed | — | — | — | — | — | — |
| `5e6204249cfc71a538ea674215be53a1864fb9eb` | docs(phase-27): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `e778d96b4b5d8a87a71b9fccc7fd4ba36ee31d6e` | docs(28): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `9a2d5dbf1024cfc78e22934ca23f6b8b050e6a1d` | docs(28-01): phase 28 verification summary - all Angular tests passing | unknown | unprocessed | — | — | — | — | — | — |
| `6c1d514ece67608ea215a31e35aef1f35d66741c` | docs: mark phase 28 and v1.8 milestone complete | unknown | unprocessed | — | — | — | — | — | — |
| `0d5b8502e7b2d08fd7d950f4497589d30f67b282` | chore: archive v1.8 milestone — Radarr + Webhooks shipped | unknown | unprocessed | — | — | — | — | — | — |
| `b981c7b26301140871380372fff21f74220ed7c0` | docs: start milestone v2.0 Dark Mode & Polish | unknown | unprocessed | — | — | — | — | — | — |
| `b9966c020e02a80b126920cbeebfb7382b13300e` | docs: complete v2.0 domain research | unknown | unprocessed | — | — | — | — | — | — |
| `870b4d10a90e2cf1d6305001ecf5936bd280d131` | docs: define milestone v2.0 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `1d88ba4fa2ea21ec8918b3bf410fbce8874fdd6c` | docs: create milestone v2.0 roadmap (4 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `fb256ba142f93d9109163acec7f764b4468c1b95` | docs(29): create phase plan for theme infrastructure | unknown | unprocessed | — | — | — | — | — | — |
| `ad35d2e5096542c8a49954797479a2cdbe9b02f3` | feat(29-01): create ThemeService with signal-based state management | unknown | unprocessed | — | — | — | — | — | — |
| `7e4157ba9a6eeb376b8147b82619200c5b3220c1` | feat(29-01): add FOUC prevention and register ThemeService | unknown | unprocessed | — | — | — | — | — | — |
| `9423a971071ff657ad7745d8c66681c3ffb25669` | docs(29-01): complete theme infrastructure plan | unknown | unprocessed | — | — | — | — | — | — |
| `ce373a2b3132bfe6d8345a5180da5539910e004b` | test(29-02): add comprehensive ThemeService unit tests | unknown | unprocessed | — | — | — | — | — | — |
| `691d94526989859c78c03d8e98195804171d7315` | docs(29-02): complete ThemeService testing plan | unknown | unprocessed | — | — | — | — | — | — |
| `6d2e19681f383599c2d8c53cbee54532711a1e99` | docs(phase-29): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `281be15dcaf6d8de05936d090bf91fc0725f1347` | docs(30): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `76300c10c990704ddcea40e1617f52c9b3dfe421` | docs(30): create phase plan for SCSS audit and color fixes | unknown | unprocessed | — | — | — | — | — | — |
| `9a32aa9f5b52d21ab643a6bacb47b7d95d0bb47f` | feat(30-01): add custom CSS variables and theme-aware form controls | unknown | unprocessed | — | — | — | — | — | — |
| `88d6258e823df33a6a8826453573b1db065997c6` | feat(30-01): remove hardcoded data-bs-theme from dropdowns | unknown | unprocessed | — | — | — | — | — | — |
| `92d4c2ad8d3ecc958ef6745714150d7dd84bb7a7` | docs(30-01): complete custom CSS variables and theme-aware forms plan | unknown | unprocessed | — | — | — | — | — | — |
| `3f1d7d2ef611f7a0e10e2b4e43f039bbbf0e2d84` | feat(30-02): migrate app, file-list, and logs component SCSS to theme-aware CSS variables | unknown | unprocessed | — | — | — | — | — | — |
| `27fb582e902fb10e5c374545d5e048e206bfe15f` | feat(30-02): migrate about, file, sidebar, and header component SCSS to theme-aware CSS variables | unknown | unprocessed | — | — | — | — | — | — |
| `0eeef6cfae686625b4c1a148caa985d41680d497` | docs(30-02): complete Component SCSS Migrations plan | unknown | unprocessed | — | — | — | — | — | — |
| `e5989061518771a04fbf84723027840e3bc602de` | docs(phase-30): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `fda674ebf60de396385ec53ddbb2e3086f564aa9` | docs(31): research theme toggle UI patterns | unknown | unprocessed | — | — | — | — | — | — |
| `04d822e8d15aa60cb41bf2842830dff33228b61b` | docs(31): create phase plan for theme toggle UI | unknown | unprocessed | — | — | — | — | — | — |
| `528e845b009eb2becf78630e71e839a12d7e5d37` | feat(31-01): add Appearance section with theme toggle to Settings page | unknown | unprocessed | — | — | — | — | — | — |
| `facb52b4e10fa652630695fc17335625f6258d40` | test(31-01): add unit tests for Settings page theme toggle | unknown | unprocessed | — | — | — | — | — | — |
| `64088736c96d4a01e5e2404ec5dafd058c32b226` | docs(31-01): complete Theme Toggle UI plan | unknown | unprocessed | — | — | — | — | — | — |
| `f88f2372f9eff5c1aca5afe501c04e951dc1a80b` | docs(phase-31): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `5808c129f5b78be98470a128ecd5dbaafd3bc6f6` | docs(32): research cosmetic fixes phase | unknown | unprocessed | — | — | — | — | — | — |
| `c6ab63e2d77826d85b34bc5ab35db31a32aaa125` | docs(32): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `123580b03c578fcd0b4efa3ed7da36a129de5f54` | feat(32-01): update *arr text references to Sonarr/Radarr | unknown | unprocessed | — | — | — | — | — | — |
| `2e54493ae6fbe2a5e1d07f2eafcac79ba3306749` | feat(32-01): add WAITING_FOR_IMPORT enum value across full pipeline | unknown | unprocessed | — | — | — | — | — | — |
| `db1115c28351813eb02cd754d5561723da0c7816` | docs(32-01): complete cosmetic fixes plan | unknown | unprocessed | — | — | — | — | — | — |
| `4ccc650be532e81f0a56aeef1f4c775a82bb7c5a` | docs(phase-32): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `73f647cfc09135a6f3fdd25271bafefd5ae7ee7f` | chore: complete v2.0 milestone | unknown | unprocessed | — | — | — | — | — | — |
| `5975b9f10520a4b7450a61fd3d207946a807f088` | fix: pass webhook_manager to Controller in integration tests | unknown | unprocessed | — | — | — | — | — | — |
| `b66401c62a144a144a768b12b6351da480dc46ae` | fix: pass webhook_manager to WebAppBuilder in web integration tests | unknown | unprocessed | — | — | — | — | — | — |
| `650c42de4276b5214112a1d9eae1dca90ebd5bf5` | wip: v2.0 milestone completion paused — awaiting CI + tag move | unknown | unprocessed | — | — | — | — | — | — |
| `31e2aae534f0937ed1ff9d2151492bb4dfe0f32d` | fix: dark mode for sidebar/action bars/selections + toast replay on restart | unknown | unprocessed | — | — | — | — | — | — |
| `4a83863dc0e930710cfa24e3aa5d2757fd7b755b` | fix: match webhook imports against child files, not just root-level names | unknown | unprocessed | — | — | — | — | — | — |
| `066c40572f6698aa5312dbd8cc1fbc5ecf5a6db5` | chore: bump version to 2.0.1 | unknown | unprocessed | — | — | — | — | — | — |
| `fab7829417c2322c0fd2d8cf29b458ea5c5e5198` | docs: update changelog and install docs for v2.0.1 | unknown | unprocessed | — | — | — | — | — | — |
| `d2de00c27589f38e2a4c607072eb21f92c37b5a6` | chore: archive v2.0.1 hotfix — webhook child file matching | unknown | unprocessed | — | — | — | — | — | — |
| `176299aef64a1f829ca1622497ae9df931ec297b` | docs: resolve debug session webhook-import-delete-broken | unknown | unprocessed | — | — | — | — | — | — |
| `3d2a3a2e624dba6d7b937dde55a5453cae7a92d6` | chore: start v3.0 Terminal UI Overhaul milestone | unknown | unprocessed | — | — | — | — | — | — |
| `eb951ec32200687b093ba60f877fe0c7d29c024a` | docs: define v3.0 requirements — 21 Terminal UI Overhaul requirements | unknown | unprocessed | — | — | — | — | — | — |
| `1595638c93a4b77f4d84345c8a7817a03f480458` | docs: define v3.0 roadmap — 5 phases (33-37), 21 requirements mapped | unknown | unprocessed | — | — | — | — | — | — |
| `06f978ab1802876e328c1996c8d791ab35451a99` | docs(33): research phase foundation | unknown | unprocessed | — | — | — | — | — | — |
| `771ebe206f1b9fc175b17928235d2febf8828704` | docs(33): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `ef728cc212bb83954e746203adfc04c545fccf86` | feat(33-01): load Google Fonts, hardcode dark theme in index.html | unknown | unprocessed | — | — | — | — | — | — |
| `6865ea03563937cf8e25e1e56aba3782c0ee8324` | feat(33-01): replace Bootstrap SCSS variables with Terminal/Hacker palette | unknown | unprocessed | — | — | — | — | — | — |
| `bc9c3991eb291364a9347cacd56b8d041cf165c8` | docs(33-01): complete foundation plan 01 — Terminal palette SCSS + font loading | unknown | unprocessed | — | — | — | — | — | — |
| `42d75b039b7feef31a1508b2327a4f1ce616a9aa` | feat(33-02): dark-only overrides + custom scrollbars | unknown | unprocessed | — | — | — | — | — | — |
| `29e7d5d0a995e64fcc3e26e74ba9443fab6d249d` | feat(33-02): Terminal CSS custom properties + CRT overlay + keyframes | unknown | unprocessed | — | — | — | — | — | — |
| `7ea6763672e35f2ac4cc3be61f60d5a1293cf46c` | docs(33-02): complete Terminal CSS custom properties plan | unknown | unprocessed | — | — | — | — | — | — |
| `678e217ed36ddd51302bd34f670b95d601190fda` | fix(33): force ThemeService to dark-only, stop localStorage override | unknown | unprocessed | — | — | — | — | — | — |
| `945688ae8a3eaaaf62005e14261a80e5cf5b4be6` | fix(33): set input-btn-font-family so all interactive elements use IBM Plex Sans | unknown | unprocessed | — | — | — | — | — | — |
| `c5ea4b2387a8b7ce5c8809058c936efe98da6a98` | docs(33-03): complete visual verification plan — Phase 33 Foundation done | unknown | unprocessed | — | — | — | — | — | — |
| `96f8e361aa03746bbc168ecc3620e444b45f77dc` | docs(phase-33): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `72036b06f026be3378c4475421d7e55179a30e20` | docs(state): advance to phase 34, fix progress bar | unknown | unprocessed | — | — | — | — | — | — |
| `ed25892fbf208207d917ef8bf7fff686b56704dc` | docs(34): research phase shell | unknown | unprocessed | — | — | — | — | — | — |
| `af7f17b89936d76c6b974019647d10a8c8195607` | docs(34): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `f64325e331a0c46bb566693252fed475e8653b3b` | feat(34-01): restructure sidebar HTML and SCSS for icon-rail layout | unknown | unprocessed | — | — | — | — | — | — |
| `9dd1d796382ab7dfffbc4012804cf5d27e15e33d` | feat(34-01): implement icon-rail CSS on large screens, preserve mobile behavior | unknown | unprocessed | — | — | — | — | — | — |
| `b4bcaecf9f1409dd2b97da7ac06385e07b44d9d3` | docs(34-01): complete icon-rail sidebar plan — Phase 34 Plan 1/2 done | unknown | unprocessed | — | — | — | — | — | — |
| `a32dfad7d1efa27f912c66b657658ccdba1e1947` | feat(34-02): add prompt indicator and version footer to sidebar | unknown | unprocessed | — | — | — | — | — | — |
| `7015357020d9f5b1c60b61117e00e75b37f18a29` | fix(34-02): icon visibility and label hover across component boundary | unknown | unprocessed | — | — | — | — | — | — |
| `4974f60d51db6ea031f420b551a510b27d2ee052` | docs(34-02): complete prompt indicator + version footer plan — Phase 34 Plan 2/2 done | unknown | unprocessed | — | — | — | — | — | — |
| `2ce7551f0e2c6954fb8588e7103b8765e1e7fd51` | docs(phase-34): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `666d355e67fb580264dac23e804d2891a635a22a` | docs(35): research phase dashboard | unknown | unprocessed | — | — | — | — | — | — |
| `b3039a9afd5c0da229ebbe206b691983ce41abda` | docs(35): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `093033b64d819ecee8501917e4e894794d306990` | feat(35-02): add @HostBinding hostClass and statusDotClass getters | unknown | unprocessed | — | — | — | — | — | — |
| `72699bdf4eda452bc5e469b8b45d5792e9f31144` | feat(35-02): replace status SVGs with dots, add borders and glow | unknown | unprocessed | — | — | — | — | — | — |
| `ea6720ac39d0c70e4b54ef294d35f37bdc2a44de` | docs(35-01): complete terminal prompt search icon plan — Phase 35 Plan 1/3 done | unknown | unprocessed | — | — | — | — | — | — |
| `4c2f5eb3978709f40c52442cb3158c376b3bef10` | docs(35-02): complete status borders, glow animation, and CSS dots plan | unknown | unprocessed | — | — | — | — | — | — |
| `93f1a0f6ef65f4346eeb661541df58da7f33bc44` | feat(35-03): add ASCII progress bar replacing Bootstrap progress | unknown | unprocessed | — | — | — | — | — | — |
| `b9c232c7e4b328beb656a2b848cc7657fbac1ab0` | feat(35-03): convert action buttons to ghost outline style with glow hover | unknown | unprocessed | — | — | — | — | — | — |
| `11d6200c716b9d895c78f2fbdefe3f82da496788` | docs(35-03): complete ASCII progress bars and ghost buttons plan — Phase 35 done | unknown | unprocessed | — | — | — | — | — | — |
| `2a94269a7777416b719ce61048d81de624ebd6d4` | docs(phase-35): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `4b7f767d11f3797f53b77cb522f68c9db93b833e` | docs(36): research phase secondary-pages domain | unknown | unprocessed | — | — | — | — | — | — |
| `a2a502d73770c6773c889ac4f2441d5a971de8de` | docs(36): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `b7fdff1a996a44669c62634cf42efe0f4d1b8a69` | feat(36-01): terminal-style Settings headers and remove Appearance card | unknown | unprocessed | — | — | — | — | — | — |
| `d8570982918f1ab12c644d4735a779e888b45916` | feat(36-02): terminal-pure log level colors and status message styling | unknown | unprocessed | — | — | — | — | — | — |
| `0bdeef590c10070b7c81fc6fd65703ea6b09ebe5` | feat(36-01): AutoQueue ghost buttons, Fira Code patterns, terminal description | unknown | unprocessed | — | — | — | — | — | — |
| `12a05c863a966f58bc55b2a8c27a5072ac999eb8` | feat(36-02): ASCII art About page with terminal markers and monospace version | unknown | unprocessed | — | — | — | — | — | — |
| `0e1a511ec207bc3885739e8d3d39dd3be6a555f6` | docs(36-01): complete secondary-pages plan 01 | unknown | unprocessed | — | — | — | — | — | — |
| `7dd5406376cb4dd32aec78e7eab7eb06b5b690f4` | docs(36-02): complete terminal log colors and ASCII art About plan — Phase 36 done | unknown | unprocessed | — | — | — | — | — | — |
| `23ca64aedcbc137ec2b06a575d44853e2ad10458` | docs(phase-36): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `0e5e0ad0a17d4c63da4885450c063637a6f8d577` | docs(37): research phase theme cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `c755220ca60ef40d78f27441b8c42addd92ddaaa` | docs(37): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `869112297afd6e30086023ea9c996b3101606bca` | chore(37-01): delete ThemeService, theme types, and theme test files | unknown | unprocessed | — | — | — | — | — | — |
| `5c0e469b3fb9c824f3a488ca528728f7ab522765` | chore(37-01): remove ThemeService from app.config.ts | unknown | unprocessed | — | — | — | — | — | — |
| `8039874ee2cfbe4bb200b0b517487874760f725c` | docs(37-01): complete theme-cleanup plan 01 — deleted ThemeService and all dead code | unknown | unprocessed | — | — | — | — | — | — |
| `1d32a97527398909e4ac34d8a1f7b174dabfe82e` | docs(phase-37): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `b9e0211fde1f6cbfe91591dc172da72e483ab0b4` | docs: add v3.0 milestone audit report | unknown | unprocessed | — | — | — | — | — | — |
| `2695d851d6f3384e883be58dba23a39a5a022dd0` | docs(roadmap): add gap closure phase 38 | unknown | unprocessed | — | — | — | — | — | — |
| `486763ae85f1073589cfce762ca03623ad5f6c0b` | docs(38): research terminal polish traceability phase | unknown | unprocessed | — | — | — | — | — | — |
| `a79c7bb575187f04b5560777a2fac54d0a74876e` | docs(38): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `96f7d86ba8ba3adee903d7da75da61c8cbe70581` | fix(38-01): fix CSS variable typo in sidebar version text and update requirements traceability | unknown | unprocessed | — | — | — | — | — | — |
| `16156606165cc3ccdf4772f895f639fb184d65fa` | docs(38-01): complete terminal polish & traceability plan summary and state update | unknown | unprocessed | — | — | — | — | — | — |
| `0926853b5bf2432659bf5db22aa6d06a46ab1249` | docs(phase-38): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `68c1406ff3c15dab9e958e6d330c9a3c59a7d642` | docs: v3.0 milestone audit — passed (21/21 requirements, 6/6 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `cf0a5fcbfb885805aa151d9b333bfe5def327b77` | chore: archive v3.0 Terminal UI Overhaul milestone | unknown | unprocessed | — | — | — | — | — | — |
| `10471415471d7dae0be5468c564a46df241d703e` | fix(e2e): update selectors for v3.0 terminal UI class changes | unknown | unprocessed | — | — | — | — | — | — |
| `ab15f80e5db0e69ea3ba5af9cde40572982bc9ab` | docs: start milestone v3.1 Harden & Fix | unknown | unprocessed | — | — | — | — | — | — |
| `a4f6c76e6f592816b19c18f45ac0ff90dbdf2601` | docs: define milestone v3.1 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `b8e767537a9008471f14f44ec783e6059a9ea496` | docs: create milestone v3.1 roadmap (7 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `15dad1544485baffd91b2fa5ced260c707b1c07f` | docs(39): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `e3d9e70eb256c4a4a166ccf2d6dad52fee2eecce` | docs(39): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `f6643db8b5211276552014abdf9061deac3a1b98` | fix(39-01): remove committed RSA private key and protect with .gitignore | unknown | unprocessed | — | — | — | — | — | — |
| `108018f28ae67724aeb46363623abb73694cb69d` | feat(39-02): replace pickle with JSON in SystemFile and scan_fs | unknown | unprocessed | — | — | — | — | — | — |
| `e34ba5e11f298509463d5697835f71dfacc29776` | fix(39-01): harden SSH host key verification across all connection paths | unknown | unprocessed | — | — | — | — | — | — |
| `abef04aff6188b5bef0c3faf4c5192e782e93b4c` | feat(39-02): migrate remote_scanner to JSON deserialization | unknown | unprocessed | — | — | — | — | — | — |
| `62cf83a7e5fca62c7388548a02ef9f874c0c8601` | docs(39-01): complete critical SSH security hardening plan | unknown | unprocessed | — | — | — | — | — | — |
| `44294c9c9b095c4fb8b5345d6c53e1570481fbc5` | docs(39-02): complete JSON migration (pickle RCE elimination) plan | unknown | unprocessed | — | — | — | — | — | — |
| `f5fe429248b04515a87067ed22ab41cac9c46f2a` | docs(phase-39): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `3c2a330d9a261b4c46f72a493ccfbd95f0f3fa7d` | docs(40): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `0a4a4108f98e201a61e6d60fc1c5588afcccee5f` | feat(40-01): redact sensitive fields from config GET response | unknown | unprocessed | — | — | — | — | — | — |
| `b9a32201a1d422d49e50e3dec5d1d1619529cb5f` | feat(40-01): scrub passwords from SSE log stream records | unknown | unprocessed | — | — | — | — | — | — |
| `6e680df653ce3396c8a3787031d88de2111ad2f2` | feat(40-02): add SSRF protection and error sanitization to test-connection endpoints | unknown | unprocessed | — | — | — | — | — | — |
| `a92af56ed211c7e7eb383ff23637010d1f591f29` | feat(40-03): add HMAC authentication to webhook endpoints | unknown | unprocessed | — | — | — | — | — | — |
| `826c7ff8b952a00941e93214bc341914c813dd6b` | docs(40-01): complete credential endpoint security plan | unknown | unprocessed | — | — | — | — | — | — |
| `4c485d921d97cc454841ba84f4a2b55d4b88a9b9` | feat(40-03): add security headers to all API responses | unknown | unprocessed | — | — | — | — | — | — |
| `492944f4f584effe43fcc3408ce2f6b72925ee6e` | feat(40-02): escape shell metacharacters in DeleteRemoteProcess using shlex.quote | unknown | unprocessed | — | — | — | — | — | — |
| `73d04b125ab7edfaf55b6ffc8aa205d4aa27eecc` | docs(40-03): complete HMAC webhook auth and security headers plan | unknown | unprocessed | — | — | — | — | — | — |
| `03a9c085d00b37066d5e4878e1240277dc423b1a` | docs(40-02): complete SSRF protection + shell escaping + error sanitization plan | unknown | unprocessed | — | — | — | — | — | — |
| `ad18fcd3de8f5eb981fddc2dd1c8d26d0f5de411` | docs(phase-40): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `f6d82c40638d051c07bd0c65f684a58fea93dd29` | docs(41): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `f5e54875456eceb84ac70dece6275a0486022527` | fix(41-01): add model lock to auto-delete callback and webhook import checks | unknown | unprocessed | — | — | — | — | — | — |
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
## rapidcopy

Audit base: `origin/master @ ff2a1039935beccbbf7ec76134b41d2e91137742`
Source branch: `rapidcopy/master`
Fork tip at audit start: `c300b72f808772b00cc977ccceaa23f3c373ce33`
Inventory status: `complete`
Audit state: `in progress`
Pass date: `2026-03-10`

| Commit | Upstream commit subject | Mapped integration subject | Triage outcome | Confidence | Evidence | Reviewer needed | Coverage | Final disposition | Follow-up / proof |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `0d5cdd1ec61367830e99b7e8df6e3b9984dd5780` | docker: refactored deb e2e test | subject 4 / packaging and install | already integrated likely | medium | direct local match | yes | full | already integrated | Local `3c0ca094` has the same subject/body and near-identical file-level change set: `Makefile`, removal of `scripts/tests/run_e2e_tests.py`, migration into `src/docker/stage/deb/*`, new `ubu1804`/`ubu2004` compose files, and the related e2e/configure and README updates. The only visible difference is that local history initially preserved `src/docker/install/id_rsa` by renaming it into stage assets rather than deleting it in this same commit, which does not leave the upstream refactor uncovered. |
| `133e7d9b231100642f26de3c6c4bbde5cf11284d` | docker: fixed stage image for ubu2004 | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` judgment looked well-calibrated on this run: it found local commit `abce9285` with the same subject and identical five-file touch set (`Makefile`, `src/docker/stage/deb/Dockerfile`, `entrypoint.sh`, and the `ubuntu-18.04`/`ubuntu-20.04` systemd Dockerfiles) and correctly avoided reviewer escalation. The local diff shape matches the upstream follow-up exactly, including the stage-image/systemd adjustments. |
| `5bde3a8640b5d53849b77151da459539d19d17e4` | Updated Makefile | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` looked well-calibrated on this docs-oriented follow-up: it identified local commit `63a7e717` with the same message, timestamp family, and identical changed paths (`doc/DeveloperReadme.md` and `src/e2e/README.md`). This row carries no remaining functional gap beyond already-landed Docker/e2e documentation updates. |
| `eec50ec3f1508c81c9c0cbf051788c8ae8493d9a` | docker: fix for running image as any user | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` again showed good judgment here: it matched the upstream change to local commit `7297b590`, which is contained on `master`, with the same three touched files (`src/docker/build/docker-image/Dockerfile`, `run_as_user`, and `ssh`). The local and upstream commits carry the same any-user image-runtime fix, so no reviewer escalation was needed. |
| `0063f8be653bc13fc325f311eaa8f55eeba5bd40` | lftp: handle extra lines before jobs status | subject 18 / transfer job status parsing | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` was accurate on this narrow parser fix too: it found local commit `f030ccb4` with the same subject/message and the same single-file change in `src/python/lftp/lftp.py`. The local hunk carries the same regex broadening around the `jobs -v` parsing path, so the behavioral fix is already present on `master`. |
| `ec6c48aa17214c44da88eb137f6daf6f8e5ddee9` | lftp: fixed a parser error with 2-line chmod | subject 18 / transfer job status parsing | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` remained well-calibrated on this parser/test follow-up: it matched local commit `f7093ea3`, which is on `master`, with the same two touched files (`src/python/lftp/job_status_parser.py` and the matching unit test). The fix and its test coverage are already present locally. |
| `211704be15b5d9372c0bdfeb2c3f72c69f98f1d8` | Updated to version v0.7.1 | subject 4 / packaging and version metadata | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` found local commit `74b02bdb` with the same subject and the same four touched files (`src/angular/package.json`, the about page template, `src/debian/changelog`, and `src/e2e/tests/about.page.spec.ts`). The version-metadata update is already present on `master`, so no reviewer escalation was needed. |
| `3e3270eb8c4ccbabde2837af2b53845ab9dbe0ae` | Updated developer readme | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` also judged this small docs commit well: it matched local commit `181acb21` with the same single-file change in `doc/DeveloperReadme.md` and the same release-checklist wording/hunk. This is already-landed documentation parity rather than missed work. |
| `8f2fbc8d5b2abaf884ef13669432e881b2ded56b` | docker: fixed docker guest .ssh path back | subject 4 / packaging and version metadata | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` stayed accurate on the functional fix itself: it matched local commit `680c2904`, which is on `master`, with the same single-file change in `src/docker/build/docker-image/run_as_user`. The `.ssh` path correction is already present locally. |
| `18ad6ce98cd41d581cafa1671f9986943c714b33` | Updated README with docker ssh keys instructions | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | The explorer result was directionally correct but a little under-specific in its rationale; local confirmation found the exact matching docs commit `032c5d38` on `master`, with the same single-file `README.md` update. The substance is already integrated, but this run showed that `direct local match` claims should explicitly name the matching local commit hash. |
| `5522203e201dcad0fda39b390cc0d07281735c1c` | Minor README fix | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` again found a clean same-message local match, `fa494153`, which is on `master` and carries the same `README.md` hunk-level formatting fix. This was a well-calibrated no-review case. |
| `bf23f0fab23b626d0678e2d70eb111955968ce9e` | Updated version to v0.7.2 | subject 4 / packaging and version metadata | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` handled the tightened prompt well here: it explicitly named local commit `8b1007c8`, on `master`, with the same three touched files (`src/angular/package.json`, `src/debian/changelog`, and `src/e2e/tests/about.page.spec.ts`). This confirms the v0.7.2 metadata bump is already present locally. |
| `33d0dae09e3cf8ddae71672067094b90890e843b` | docker: fixed scp error with unknown user | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | The explorer again returned a clean explicit local match, `dc3392b6`, with the same two-file Docker image fix (`src/docker/build/docker-image/Dockerfile` and the added `scp` helper). This was another well-calibrated no-review case. |
| `291a694511aaec0b9a97d7e28117e9ef1b3ffd8c` | Updated version to v0.7.3 | subject 4 / packaging and version metadata | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` named local commit `0227990f` explicitly, again on `master`, with the same version-bump file set as the upstream row. The tightened prompt produced the level of evidence we want for light-touch orchestration. |
| `8d6da57c0ddb1f459d0f826b992d3a88f8ace439` | Test remote server instructions in dev readme | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` returned a clean explicit local match, `e0352779`, with the same `Makefile` plus `doc/DeveloperReadme.md` remote-server additions. This remained a well-supported low-context closure. |
| `1bccd13657afe83898b1a20d846e88e3317ddbe2` | Py-scanner: recoverable errors from remote scanner | subject 13 / remote scan reliability and error semantics | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` matched this scanner error-semantics change to local commit `9c05838a`, with the same remote-scanner/controller/ssh file family and corresponding tests. The recoverable-error behavior is already present locally. |
| `20a2ade887b29c17f7277dfe270063c8004ee026` | py-scanfs: scan errors printed in a single line | subject 13 / ssh and remote command handling | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` named local commit `ab30dde0` explicitly and the rationale stayed concrete: same `src/python/scan_fs.py` error-handling change, already present on `master`. No reviewer escalation was needed. |
| `9ed00ca7c86ceb2c51ddf0ffe7070e7ba92573cf` | py-ssh: more descriptive error messages | subject 13 / ssh and remote command handling | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` named local commit `3f84b724` explicitly and kept the evidence concrete to the `src/python/ssh/sshcp.py` hunk set. The more descriptive SSH error-path behavior is already present locally. |
| `f2d906e10f1f1fd8b6efdacfd681aebe9bb7f852` | py-scanner: more non-recoverable errors | subject 13 / remote scan reliability and error semantics | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` matched this change to local commit `06dd9fda`, with the same remote-scanner/localization/test file family and corresponding non-recoverable scan behavior. This remained a clean no-review closure. |
| `de964a1c1d5b2e0a9a028a8fb65131ec1986a3f4` | py-controller: send remote scan errors to web | subject 13 / remote scan reliability and error semantics | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` found explicit local match `b962d1aa` with the same controller/status/serializer/test updates. The remote scan error surfacing to the web layer is already integrated. |
| `bc523d75fee1976ebc03d7003e7572c1ab5cfbe7` | py-lftp: fixed parsing out of log line | subject 14 / lftp job status parser hardening | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` named local commit `3f1af408` explicitly, with the same parser/lftp/test file set and matching regression tests. This parser-hardening change is already integrated. |
| `49ffdf12b925d9306d50bd4f7a845f67fb826bb3` | docker: updated dockerignore file | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` matched this packaging hygiene change to local commit `b8e1afd9`, with the same two `.dockerignore` file hunks. This was another clean no-review closure. |
| `757da155caec8515575faef519395fae19c229ca` | Ng: notification for remote server error | subject 13 / remote scan reliability and error semantics | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` found explicit local match `712e82b9` covering the same Angular status-model, header notification, localization, and spec updates. The remote server error notification path is already present locally. |
| `52b9ebda04b25a6d40cf0376c9416ab2a50efebb` | Py-scanner: remote md5sum only fails on bad ssh | subject 13 / remote scan reliability and error semantics | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` matched this change to local commit `cbcc9f6b`, with the same remote-scanner plus integration/unit test updates. The md5sum SSH-failure handling is already present locally. |
| `50f6b45f3cd86b63c499ec6f5ea7a66536ccbe91` | Updated version to v0.8.0 | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` found exact local match `80359d21`, with the same package/changelog/e2e version-bump files. This stayed a clean low-context closure. |
| `d3acc00892246dba42cf222a4875f2e01527fe6d` | Py: fixed python tests for docker | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` named local commit `7a4a81db` explicitly, with the same three-file Python test/ssh adjustment set. The docker-facing Python test fix is already integrated. |
| `dbacc3faf89ab1513246cfddb62933d15a337318` | travis: initial config | subject 4 / packaging and install | likely intentional skip | medium | behavioral inference | no | full | covered elsewhere | Reviewer confirmed this Travis-only CI setup is fully superseded by later local history: local commit `c76a36ac` removes `.travis.yml`, and active CI intent now lives in `.github/workflows/master.yml`. The upstream Travis path became irrelevant because the repo intentionally migrated to GitHub Actions. |
| `8d9a40f3fe3d3cbcd8152d41a9c58358b5f20fc0` | travis: switch dist to ubuntu 18.04 | subject 4 / packaging and install | covered elsewhere likely | high | direct local match | no | full | covered elsewhere | Reviewer confirmed the right read here is `covered elsewhere`: local history includes the same Travis tweak in `8f5a7015`, but that entire Travis config lineage was later retired by local commit `c76a36ac` removing `.travis.yml`. |
| `d91445ab4efc2240dad1e7a6d2f35707085105bd` | travis: upgrade docker to latest version | subject 4 / packaging and install | likely intentional skip | medium | tracker match | no | full | covered elsewhere | The explorer identified this as another Travis-only CI change on a now-removed `.travis.yml` lineage. With the adjacent Travis commits confirmed by reviewers as intentionally superseded by GitHub Actions, this commit falls into the same covered-elsewhere CI migration bucket. |
| `c1d1530fee5db28ca1379a1c2e9c207bb4479890` | travis: different method of upgrading docker | subject 4 / packaging and install | covered elsewhere likely | medium | behavioral inference | no | full | covered elsewhere | This is another Travis-only `.travis.yml` tweak on a CI path that local history intentionally retired. The broader Travis-to-GitHub-Actions migration already accounted for this lineage, so this specific command-path variant is covered elsewhere. |
| `710ab6920e532ad6db5f10f5a73c1f35d919a15f` | travis: pull external images before docker build | subject 4 / packaging and install | covered elsewhere likely | high | tracker match | no | full | covered elsewhere | `explorer-fast` tied this directly to the same retired `.travis.yml` lineage and local Travis removal commit `c76a36ac`. The workaround is irrelevant under the current CI system. |
| `e5698066491246bd34c2447f69f4eb3c1df9878a` | travis: docker pull needs to be multiple cmds | subject 4 / packaging and install | covered elsewhere likely | high | direct local match | no | full | covered elsewhere | This Travis-only pull-command adjustment is present in the old Travis lineage and then superseded by local commit `c76a36ac` removing `.travis.yml`. It is covered elsewhere by the repo’s deliberate CI migration away from Travis. |
| `a34057a58dbd116f34ffe8447756de3de6859664` | travis: run all tests | subject 2 / tests, CI, and verification | covered elsewhere likely | high | behavioral inference | no | full | covered elsewhere | The upstream intent of running the full Python/Angular/E2E surface is present locally under GitHub Actions rather than Travis. This Travis transport commit is covered elsewhere by the repo’s current CI workflow. |
| `f9b129e8f224d424365df6c5697ead7c3193305c` | travis: plain progress for buildkit | subject 4 / packaging and install | covered elsewhere likely | high | direct local match | no | full | covered elsewhere | `explorer-fast` found old-lineage local match `e6be9f4f` and confirmed the Travis-only adjustment was later superseded by local Travis removal. |
| `1a5721e2c55e16a1a94b42e508abf6b3c238c5e0` | docker: e2e tests timeout if app not configured | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` matched this directly to local commit `b9bcd9f6`, with the same `src/docker/test/e2e/run_tests.sh` timeout/polling behavior. This e2e guard is already integrated. |
| `3ab8e066f72fb4051a9b55f1446a228687a10785` | make run-e2e-tests exit with error if tests fail | subject 2 / tests, CI, and verification | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` matched this to local commit `55e2555b`, with the same Makefile/test-doc behavior for propagating E2E failure exit codes. The active local test harness already includes this guard. |
| `23064e7de1b86383142e5d87d4b33e83b2841dfc` | python and angular tests error propagate to make | subject 2 / tests, CI, and verification | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` found explicit local match `ae887c34` carrying the same `--exit-code-from tests` Makefile changes for Python and Angular test targets. This behavior is already present locally. |
| `91e586f0a3ddb631eda3b1c235a68b01bbed0d02` | Made python and angular docker tests less noisy | subject 4 / packaging and install | covered elsewhere likely | high | direct local match | no | full | covered elsewhere | `explorer-fast` found old-lineage local match `2106680c` and noted the same Docker test image changes were later adapted further in local follow-up commits. The upstream substance is already taken and evolved locally. |
| `891350813ed8ff2dcf49796c2efddab56cdced99` | travis: moved unit tests to before_install | subject 2 / tests, CI, and verification | covered elsewhere likely | high | direct local match | no | full | covered elsewhere | `explorer-fast` was well-calibrated here: it named exact local commit `e8e77441`, which carries the same `.travis.yml` patch, and local commit `c76a36ac` later removes Travis entirely. The upstream behavior was taken in local history, then intentionally superseded by the GitHub Actions migration. |
| `4238daa13f98bc9855220f7ff2dbd6d82dc2f0a2` | docker: increase pexpect timeout in stage/deb | subject 4 / packaging and install | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` named exact local commit `d27e4fc4` with the same one-line change in `src/docker/stage/deb/expect_seedsync.exp`, and current `master` still carries `set timeout 10`. This was a clean no-review direct match. |
| `25a2a8c5da5d474e99e45595a48577f88ea5add5` | Updated dev readme | subject 4 / packaging and install | covered elsewhere likely | medium | behavioral inference | no | full | covered elsewhere | Reviewer confirmed this docs-only change is already present in live local docs: the updated Debian E2E example in `doc/DeveloperReadme.md` already uses ``SEEDSYNC_DEB=`readlink -f build/*.deb` `` in the same test-suite section. The upstream intent was absorbed into a broader later documentation evolution rather than left out. |
| `202412af93e584d311415b85753f095cd27a70c3` | py-lftp: increase timeout for lftp tests | subject 14 / transfers and lftp | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` found exact local commit `7ef751b3`, with the same `src/python/lftp/lftp.py` and `src/python/tests/unittests/test_lftp/test_lftp.py` timeout/polling updates. This stayed a well-supported no-review closure. |
| `78f7f71f71067c19ad15bedc6008c02ffdb6ce62` | docs: created mkdocs documentation | subject 1 / documentation and maintainer notes | already integrated likely | high | direct local match | no | full | already integrated | `explorer-fast` named exact local commit `00942cf2`, which is on `master` and carries the same MkDocs bootstrap: `src/python/mkdocs.yml`, `src/python/docs/index.md`, the new docs images, and the accompanying `doc/DeveloperReadme.md` plus Pipfile updates. This was another clean direct-match audit row. |
| `6f0528ef43e60432bb479caf908eaa0b0c92a82e` | Updated README | subject 1 / documentation and maintainer notes | covered elsewhere likely | high | tracker match | no | full | covered elsewhere | The explorer’s first-pass classification was directionally right but needed local grounding. Current `master` already contains the two `.gitignore` entries at lines 6 and 7, and the same documentation deploy section plus commands in `doc/DeveloperReadme.md` at lines 153 to 157. The substance is already present locally across earlier docs commits such as `1160840d` and `9792f980`, so this closes as covered elsewhere without reviewer escalation. |
| `01430ca433c48adad161b292df6acaaba3f363e4` | py-lftp: fixed parser error | unknown | unprocessed | — | — | — | — | — | — |
| `6edc9f52d7176531541f9fadb494dd5d11c8a37a` | Updated version to v0.8.1 | unknown | unprocessed | — | — | — | — | — | — |
| `481e0402bf66e0966235d1eb576aca142b490828` | py-lftp: fix error with timeout | unknown | unprocessed | — | — | — | — | — | — |
| `5a195f2c38ffaf74142d162d3d29ceba128a2ff5` | Added tests for unicode support | unknown | unprocessed | — | — | — | — | — | — |
| `e27ea997eeb11c649957c7720636ae730d283db6` | Py-lftp: fixed more lftp parser errors | unknown | unprocessed | — | — | — | — | — | — |
| `3b4ad116dd0594f8ecb48b09fe3255438d20466e` | Updated version to v0.8.2 | unknown | unprocessed | — | — | — | — | — | — |
| `746c1c83faeccf6d0e165c481b4f6b182063e9ef` | Simplified README | unknown | unprocessed | — | — | — | — | — | — |
| `7e0746682c805d25c61926dcc49ce135d144ba80` | README: added badges | unknown | unprocessed | — | — | — | — | — | — |
| `902eb15e0dce40361b53e2a1502aa9794b48d006` | py-lftp: ignore first two consecutive status errs | unknown | unprocessed | — | — | — | — | — | — |
| `9b01756f3bc36c4253bbce31c4af979ae5258713` | Updated README | unknown | unprocessed | — | — | — | — | — | — |
| `f6d35c1918790c42b73d32d9f2448bd25d808065` | Updated version to v0.8.3 | unknown | unprocessed | — | — | — | — | — | — |
| `8486c9b4b9c2cbbb247e72f2fb60d2331f08bcd8` | Updated documentation | unknown | unprocessed | — | — | — | — | — | — |
| `ed2e39e6cb189575a78e90297f42d2054f3d3318` | docs: added blurb about locale errors | unknown | unprocessed | — | — | — | — | — | — |
| `3c21ca37fb662ab47e4e9a6d43f9817a34d8a537` | py-parser: another parser case handled | unknown | unprocessed | — | — | — | — | — | — |
| `defe08f4d45779d8cbe811eb9d6968245e8e9bb2` | Updated Pip dependencies | unknown | unprocessed | — | — | — | — | — | — |
| `f67a3d6df17d48408fa29901b83e9f143fbe5e3a` | Py: print platform arch on first start | unknown | unprocessed | — | — | — | — | — | — |
| `34151bcce0c42bbe838e01af1381c97850accdf8` | Multi-arch build system and instructions | unknown | unprocessed | — | — | — | — | — | — |
| `14b7995e43af90308a791bc95838127abcb0bb42` | Small DevReadme update | unknown | unprocessed | — | — | — | — | — | — |
| `f81d5d72638319f8656fd5a4ede28d432c0317ce` | docker: fixed python tests failure | unknown | unprocessed | — | — | — | — | — | — |
| `70e7f583bf43f5ed1d089f2726ce1a561e18aecb` | Updated dev readme | unknown | unprocessed | — | — | — | — | — | — |
| `b00405f6c1f50c4e41346d2a20ee92f5ac010ddb` | Updated dev readme | unknown | unprocessed | — | — | — | — | — | — |
| `84a379c6a6afe7a4281cde65628013940a206a72` | Updated docs for raspi installation | unknown | unprocessed | — | — | — | — | — | — |
| `259b2146e8a6a40f0815111deaf3f3168059f32f` | Updated version to v0.8.4 | unknown | unprocessed | — | — | — | — | — | — |
| `de80a5806c502fe62ea3b369449d1aa8d7fb0212` | Remove build shield until CI is fixed | unknown | unprocessed | — | — | — | — | — | — |
| `5c33ac27a932e6604ad714bb8f0c0704bbb15405` | Updated dev readme | unknown | unprocessed | — | — | — | — | — | — |
| `b8d0536f58f5e9e57a011689b538603546367343` | docs: added requirements blurb | unknown | unprocessed | — | — | — | — | — | — |
| `e4ea3f4afeb4333ef3464d09858ad6def3ea6ad9` | Py: Adds poetry config | unknown | unprocessed | — | — | — | — | — | — |
| `29352de21a10cfe3395ab14a5cfb8ff3cc645745` | Py: improves lftp test error reporting | unknown | unprocessed | — | — | — | — | — | — |
| `24e54ffa86b570151996cafebe61cd05691a9403` | Py: sanitizes non-utf8 chars at scan boundaries | unknown | unprocessed | — | — | — | — | — | — |
| `6d2627e9eb2e3ee1e46aafa111a2bd70c7bc8acd` | Py: minor fixes to web handler tests | unknown | unprocessed | — | — | — | — | — | — |
| `b1d5782ca457c98bbabae45c8787e2ae556bf8b9` | Docs: switches pipenv -> poetry | unknown | unprocessed | — | — | — | — | — | — |
| `1513a919a4e9c1fd7dd123618a727a72f542c41e` | Py: switches from green -> pytest | unknown | unprocessed | — | — | — | — | — | — |
| `37aa0a34d92e7cd49eebfae8b416152d2f29419b` | Py: removes pipenv files | unknown | unprocessed | — | — | — | — | — | — |
| `d37afa736e04b622e39aeadcae6b40189dc1d47e` | Updated build system to use poetry | unknown | unprocessed | — | — | — | — | — | — |
| `9b2945f78cdadd52efa915762fa293cae359aaf2` | Adds sample Github Actions workflow | unknown | unprocessed | — | — | — | — | — | — |
| `a5266554c33d23c4ea20bf47b74b76c6ed6bbd8a` | Make: consolidates env vars names to be consistent | unknown | unprocessed | — | — | — | — | — | — |
| `af628be2f43ed1a5fd6e8d8842d043e0da4c8267` | Make: Further consolidates REGISTRY env var | unknown | unprocessed | — | — | — | — | — | — |
| `a3b71cbdd399660fd6a47ae49071a5cb3e50ddf0` | Make: makes staging registry configurable | unknown | unprocessed | — | — | — | — | — | — |
| `0173ef2cdba30616bdae67c6c9de011f708c6a87` | Make: makes version configurable during build | unknown | unprocessed | — | — | — | — | — | — |
| `c6f04193cd731f042499316d74768b22c1b2c496` | Adds buildx driver opts for all GA jobs | unknown | unprocessed | — | — | — | — | — | — |
| `cbda9b40e5a1a6df89378686986d9fb1d2a57675` | Fixes staging registry var in staging compose | unknown | unprocessed | — | — | — | — | — | — |
| `c70d948ea696a5018101be14999e353255afa48c` | GA: adds full workflow for master branch | unknown | unprocessed | — | — | — | — | — | — |
| `3ab429dde46f9f8b5f2622be970e5035a8671e8f` | Make: Adds build cache for final docker image | unknown | unprocessed | — | — | — | — | — | — |
| `dba0deaa2a64fbad3bacda6264a161fd73cd58bd` | Make: enables caching for intermediate images | unknown | unprocessed | — | — | — | — | — | — |
| `bd6b9c4f58656e99f59dfa60129e0794f7ace8e4` | GA: Adds deb and docker image release jobs | unknown | unprocessed | — | — | — | — | — | — |
| `bb1fad869eba72cc8b797e5a5a31ea8a6233148d` | Make: Renames SEEDSYNC_VERSION -> STAGING_VERSION | unknown | unprocessed | — | — | — | — | — | — |
| `032ec606b8ce8706430690155825842fccc6dc96` | Make: Separates staging and release versions | unknown | unprocessed | — | — | — | — | — | — |
| `ccda64ce7bcc5225bdc5fe08c205ae411bbf8283` | Reduce docker image size by half by via slim | unknown | unprocessed | — | — | — | — | — | — |
| `7206940bf8ef72e5e1ecd6be7edb175254cf6419` | Removes travis config | unknown | unprocessed | — | — | — | — | — | — |
| `f097e93bb746b31e1e5f1bdc2493ca0307749ce9` | Removes unrar from release, adds rar to unittests | unknown | unprocessed | — | — | — | — | — | — |
| `cf67f504c978c10e8a4e477b7c065a39ac0d5bd9` | Updates DevReadme with new release instructions | unknown | unprocessed | — | — | — | — | — | — |
| `e1a156db02acdaf17895452815bfb95d023279e1` | Changelist for Release v0.8.5 | unknown | unprocessed | — | — | — | — | — | — |
| `40165b0355a3b1d2ba9689ffcba75770bf0fc7f5` | Fixes broken rar extraction. | unknown | unprocessed | — | — | — | — | — | — |
| `302a447b5e4f1cea288686dd5507ba2a18a66c1b` | Changelist for Release v0.8.6 | unknown | unprocessed | — | — | — | — | — | — |
| `ba93749b4a5cf0a7a6d97fc059765a9759f19c18` | Update README.md | unknown | unprocessed | — | — | — | — | — | — |
| `ce8d4e973bfda1bee5084531aca19f9894edc584` | Add comprehensive codebase analysis by Claude Sonnet 3.5 | unknown | unprocessed | — | — | — | — | — | — |
| `3178f754906060804f5bcad1a708db6877c20820` | Add comprehensive codebase analysis by Claude 4 | unknown | unprocessed | — | — | — | — | — | — |
| `be585385dd1c75d409b49a87452bbd3533145c28` | Upgrade Python 3.8 to 3.11 with modernization plan | unknown | unprocessed | — | — | — | — | — | — |
| `561bf5a66957e853d29e4a3ad6b9a3c05c6efa4c` | Fix regex deprecation warnings with raw string literals | unknown | unprocessed | — | — | — | — | — | — |
| `ad95ab278bef98c6cdc31bd3b3db5fd678c542d5` | Pin Python dependencies to specific version ranges | unknown | unprocessed | — | — | — | — | — | — |
| `5dd2b7f43a96a509fd3272fb519b5e04c3f59c7e` | Update DEB build Dockerfile to Python 3.11 and Ubuntu 22.04 | unknown | unprocessed | — | — | — | — | — | — |
| `4ff5f19a1dc67567e2a91e2bc94f01f58ce15e0e` | Fix SSH error message tests for newer OpenSSH versions | unknown | unprocessed | — | — | — | — | — | — |
| `035dc8c5c9f3669bfeb9ffef82246ce1be1a7d31` | Add ruff, mypy, and pytest-cov dev tooling | unknown | unprocessed | — | — | — | — | — | — |
| `677be93ea08fbc05794cd7332c821d3c7d0bea1c` | Apply ruff auto-fixes for code quality | unknown | unprocessed | — | — | — | — | — | — |
| `d458abd66a89128e9f4c7bc15b3ccccbc19a4237` | Fix error message bugs and improve type checking | unknown | unprocessed | — | — | — | — | — | — |
| `a9320fd3836f77585f025341281473e4177745ba` | Remove redundant .keys() calls in dict iteration | unknown | unprocessed | — | — | — | — | — | — |
| `94c0172759be11a9c590bde77ac7a3e65e940394` | Apply additional ruff code quality improvements | unknown | unprocessed | — | — | — | — | — | — |
| `1131714a4cec772cad9e28d06d49bcc39e8e0ce7` | Add exception chaining with 'from' clause (B904) | unknown | unprocessed | — | — | — | — | — | — |
| `b1ac34689185ac2f254f6e946f0f74f5160acd80` | Use contextlib.suppress for exception suppression (SIM105) | unknown | unprocessed | — | — | — | — | — | — |
| `7de94c8081aefc994bb77616b4e2db4c05b151ef` | Update ruff config to ignore acceptable patterns | unknown | unprocessed | — | — | — | — | — | — |
| `d87f403f8b15610857ca0a1727e54ebd2f9d1792` | Fix all mypy type checking errors | unknown | unprocessed | — | — | — | — | — | — |
| `34698f2e196f86a3bf4c1a9575460aa200e3fb9f` | Update README and Developer documentation | unknown | unprocessed | — | — | — | — | — | — |
| `6d59994dc164c6f8f08366e085ad1749da245a1e` | chore: rebrand SeedSync to RapidCopy throughout codebase | unknown | unprocessed | — | — | — | — | — | — |
| `a74c19b6d93e6f745d5f0d0b0c3bcb745a4d0c7f` | chore: rename Docker stages and image references to rapidcopy | unknown | unprocessed | — | — | — | — | — | — |
| `e0985b23e9824d616ebc075f26f66a2b45e8f12f` | feat: migrate Angular 4.2.4 to Angular 18.2.0 | unknown | unprocessed | — | — | — | — | — | — |
| `7f2214161127268706933600f7a771c01e282db0` | feat: add download rate limit feature and fix Angular 18 compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `fd2a8f332e45ad99fc2e1532cc977b55c2cf64a1` | feat: add centralized logging system with configurable log levels | unknown | unprocessed | — | — | — | — | — | — |
| `5e8bf5ecb799e86d51036c943ae4b65a06c2d9ec` | fix: update Immutable.js Record classes for 4.x compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `e4814be47cc9516dbc11140aae404bced79939d8` | docs: update documentation for Angular 18 and new features | unknown | unprocessed | — | — | — | — | — | — |
| `bb539a5697c229e10a53cd0b38b47b9e7cd58716` | SECURITY: Remove private SSH key from repository | unknown | unprocessed | — | — | — | — | — | — |
| `93e10abbedb2d0235dc9fb7bc7c2a7bec58216c1` | refactor: modernize codebase with security fixes and compatibility updates | unknown | unprocessed | — | — | — | — | — | — |
| `d1436386eed1ff80876cff7731c00bb5b308a54d` | feat: add multiple source/destination path pair support | unknown | unprocessed | — | — | — | — | — | — |
| `671a0c397ce2319f300e3eb0209461e94235fab3` | feat: add path pair badge to file list UI | unknown | unprocessed | — | — | — | — | — | — |
| `227b5a34b60e524c51e3d35bf67814a6be70867f` | feat: add download validation with chunk-level checksums | unknown | unprocessed | — | — | — | — | — | — |
| `08d714e68274b6c28ae479c1981617c8cf6d0113` | chore: rebrand logo and text from SeedSync to RapidCopy | unknown | unprocessed | — | — | — | — | — | — |
| `fb4e7db43e6fd333993e363c2838c80c639830a7` | feat: add dark mode with theme toggle in sidebar | unknown | unprocessed | — | — | — | — | — | — |
| `31f7150bdf3f02ea3e822206d24f4ff07ef37e78` | docs: add deployment guide and improve SSH key auth UX | unknown | unprocessed | — | — | — | — | — | — |
| `94b8f072c476615e2183db4b2064d18939dc84da` | ci: modernize GitHub Actions workflow and add documentation | unknown | unprocessed | — | — | — | — | — | — |
| `5c02e93b2bb8ba21514b24902a0b4e69a91af04d` | test: add comprehensive unit tests for ThemeService | unknown | unprocessed | — | — | — | — | — | — |
| `1690826316cbb56b941d8f23a71fdc357dbb1554` | feat: add multi-path active scanning support | unknown | unprocessed | — | — | — | — | — | — |
| `981d7075462eb8e1e18d683c742d40043419446b` | test: add comprehensive unit tests for MultiPathActiveScanner | unknown | unprocessed | — | — | — | — | — | — |
| `9d58f10db97fc77f855bd2a76dcc00efd9857399` | test: add integration tests for multi-path controller scanning | unknown | unprocessed | — | — | — | — | — | — |
| `5d2edbe4756686c1d9c9f683832428f46095518d` | docs: add documentation for multi-path feature and dark mode | unknown | unprocessed | — | — | — | — | — | — |
| `778d1d8c9d961b03f6a47067f3d7d9a6de2ec653` | feat: add path pair statistics component to dashboard | unknown | unprocessed | — | — | — | — | — | — |
| `64afa027f0b42fcf71945312d306d68708856e3e` | test: add E2E tests for multi-path feature | unknown | unprocessed | — | — | — | — | — | — |
| `bc8348c85b89094af4e761fbd90192d92f1ebf80` | test: add unit tests for PathPairStatsComponent | unknown | unprocessed | — | — | — | — | — | — |
| `02f2641ae93453748eb3fe594f64ee0f0496fc24` | feat: add root Dockerfile and docker-compose.yml for simplified local builds | unknown | unprocessed | — | — | — | — | — | — |
| `ebe416f8ce959909ed95008c213c836bc6723dcb` | feat: update favicon to match new RapidCopy logo | unknown | unprocessed | — | — | — | — | — | — |
| `a33981b5574494390907c48694937a67679c4c44` | fix: improve settings validation and add Docker path warnings | unknown | unprocessed | — | — | — | — | — | — |
| `58ead0584c79a580046a9dda6acded851bc2107c` | fix: sync Angular config schema with backend and use CSS variables for theming | unknown | unprocessed | — | — | — | — | — | — |
| `88ffbd0000d50173834647a952fa8d6786914a53` | fix: improve config value access and null handling for checkboxes | unknown | unprocessed | — | — | — | — | — | — |
| `696866cde0fe0be8397957b329fba6d0c3f847c0` | feat: migrate E2E tests from Protractor to Playwright | unknown | unprocessed | — | — | — | — | — | — |
| `0b49f975e55c8510e4f77d657b434da4b744b33a` | feat: add network mount support (NFS/CIFS) | unknown | unprocessed | — | — | — | — | — | — |
| `936ae4b25f2653f57e5726119a68b7100a33fe52` | feat: add self-update service with external update server | unknown | unprocessed | — | — | — | — | — | — |
| `58c588b7f987df748f24105c1abf97e755684d52` | fix: allow path pairs to use /mounts directory in Docker | unknown | unprocessed | — | — | — | — | — | — |
| `c898015e8674d0a253485e949e31085e9f3c012a` | docs: add QA testing environment and guide | unknown | unprocessed | — | — | — | — | — | — |
| `35bf15d8861c557198d3b65e8be4b758426636eb` | docs: add comprehensive use cases and features documentation | unknown | unprocessed | — | — | — | — | — | — |
| `5df693d702b65e00c801c2cf5a7ded3d6f25180b` | feat: enable backend-dependent Playwright tests and add CI/CD pipeline | unknown | unprocessed | — | — | — | — | — | — |
| `2ed36f269e6456476c79a4418442c08619d7dfd2` | fix: resolve Playwright test stability issues with WebSocket-based app | unknown | unprocessed | — | — | — | — | — | — |
| `4c1cc1678f1456142a1f75d82dec9a813671dc20` | fix: use /server/config/get for backend availability checks | unknown | unprocessed | — | — | — | — | — | — |
| `5d5a90a099b5ecdafe844f5a1f17c19861ed012c` | fix: add pickle fallback to remote_scanner for legacy scanfs binaries | unknown | unprocessed | — | — | — | — | — | — |
| `5d3e1e85636def6c5abc91a91a5381adc807b5bf` | fix: add VALIDATING state to model serialization | unknown | unprocessed | — | — | — | — | — | — |
| `4fdda8c59f8af46bdf09fc287a70922d93ce389e` | docs: add comprehensive testing state documentation | unknown | unprocessed | — | — | — | — | — | — |
| `4527bfecead67af92e73e83f0b74636256db3e7b` | fix: resolve UI bugs and validation infinite loop | unknown | unprocessed | — | — | — | — | — | — |
| `aeb27fa17044290df17ad718cbf4781243bf03bf` | fix: use JSON output in scanfs and add missing model states | unknown | unprocessed | — | — | — | — | — | — |
| `7663db93ba870176236cfbfbaa0f8668496267a2` | fix: update favicon to match new RapidCopy logo | unknown | unprocessed | — | — | — | — | — | — |
| `2614ae61c5924889c77e1cc89abbd1dae0b801f0` | fix: batch remote checksum commands to prevent ARG_MAX overflow | unknown | unprocessed | — | — | — | — | — | — |
| `1700fcc9aacd6216c07a739b15555ffe7184b113` | test: add comprehensive UI tests for all pages | unknown | unprocessed | — | — | — | — | — | — |
| `be7c52e01fbf83867dc2508a444f7ac406844a32` | fix: correct Restart button test to check sidebar link | unknown | unprocessed | — | — | — | — | — | — |
| `25145f67dd72514f714366b9b2228d5788d7d786` | config: increase default validation chunk size from 10MB to 50MB | unknown | unprocessed | — | — | — | — | — | — |
| `c392a29a1559c116d7e610767cb98dd11f43cd80` | docs: rewrite README with comprehensive feature documentation | unknown | unprocessed | — | — | — | — | — | — |
| `9f4cbb15bac48b66cb0a84cdbc9a9a1439938f49` | docs: add Docker Hub publishing to-do | unknown | unprocessed | — | — | — | — | — | — |
| `d20b84d3b799e0a9a291fadf755dbcd24d43f081` | fix: prevent validation status leak and ensure proper cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `4cd7fc1da6a2130047e65109caac271568b2a91c` | fix: preserve validation states across model rebuilds | unknown | unprocessed | — | — | — | — | — | — |
| `b62970aef970c121a2373b9ee74ca9330828f4bc` | fix: show status for local-only files missing remote counterpart | unknown | unprocessed | — | — | — | — | — | — |
| `d0662cab2b152f600b2af7b90345197b83eeb9e7` | feat: switch validation from MD5 to xxHash (xxh128) for ~20x speedup | unknown | unprocessed | — | — | — | — | — | — |
| `323e3edf31e38e3b7cec2273b54dfae65a029e51` | fix: use ng-bootstrap directives for dropdowns and Bootstrap 5 data attributes | unknown | unprocessed | — | — | — | — | — | — |
| `f1fc34caf11125a2a6f94be5260dade1122f5a89` | feat: add clickable column headers for sorting on the dashboard | unknown | unprocessed | — | — | — | — | — | — |
| `d9c69750c195915cde988a592b40679effa96f15` | fix: add validation states to status sort priority map | unknown | unprocessed | — | — | — | — | — | — |
| `e158fe2a88c57ab29607a34904cfeae1fc0487df` | config: update docker-compose.yml with production volume mounts | unknown | unprocessed | — | — | — | — | — | — |
| `58af9ee80588740b05a766d6ade57d1f06ce7823` | fix: resolve invalid escape sequence warnings in test_job_status_parser | unknown | unprocessed | — | — | — | — | — | — |
| `bd93c7711ddc4de91216bdbb1146b167ee7aea39` | docs: update AGENTS.md with build environment and pending work | unknown | unprocessed | — | — | — | — | — | — |
| `394a921c714d77fc9e873ceb177de53bb128f4d4` | config: switch git remote to SSH and update AGENTS.md | unknown | unprocessed | — | — | — | — | — | — |
| `ab075718e3cb3fb7bca18fd347c2d10f2ae98d97` | chore: update poetry.lock to latest dependency versions | unknown | unprocessed | — | — | — | — | — | — |
| `dda1cb2eda952922887a50932010515832fdfaa9` | feat: implement inline validation (validate_after_chunk) | unknown | unprocessed | — | — | — | — | — | — |
| `96bcf0ba3ea979de004c2f62eda9bd1afe41c873` | docs: update AGENTS.md — mark inline validation done, refresh todo list | unknown | unprocessed | — | — | — | — | — | — |
| `30809bf2bcf006ece17742661d030495a52378bb` | feat: wire inline validation to LFTP partial re-downloads for corrupt chunks | unknown | unprocessed | — | — | — | — | — | — |
| `73b25b7facfdf9567dc688de7628242b69373cef` | docs: update AGENTS.md — mark corrupt chunk re-download complete | unknown | unprocessed | — | — | — | — | — | — |
| `50502647e6649c617af0e9bdac1f8471ff5fd804` | chore: add .ruff_cache and .mypy_cache to .dockerignore | unknown | unprocessed | — | — | — | — | — | — |
| `157d003d6b20dc5a7c531784b30a37ab82aadab9` | fix: pget_range - run directly instead of via queue, drop -c flag | unknown | unprocessed | — | — | — | — | — | — |
| `92587f3b96c6c932fc42faf6e04a704ca5540b4d` | docs: update AGENTS.md — session wrap-up notes | unknown | unprocessed | — | — | — | — | — | — |
| `14adf8b97c38137294b9fbd177cb44e8ac142934` | Fix checkbox alignment, download percentages, and update MODERNIZATION-PLAN | unknown | unprocessed | — | — | — | — | — | — |
| `e038c213ed074e2d5f2fb5844cc120d0f4c6bd82` | Fix download speed: validate after download completes, not during | unknown | unprocessed | — | — | — | — | — | — |
| `797ebfaac2f306d62ae02be14c5143c84c0a1555` | UI improvements: percentage cap, button tooltips, restart confirmation | unknown | unprocessed | — | — | — | — | — | — |
| `20ebcbcb72331a02362f595383cbff6def6e3303` | Fix checkbox alignment by replacing Bootstrap form-check with plain flex label | unknown | unprocessed | — | — | — | — | — | — |
| `4acc00b0334e8851f94604e6fc290d75e8ba9e7f` | Add config backup-on-save and document correct docker run command | unknown | unprocessed | — | — | — | — | — | — |
| `2054b14925026e8d84645f598d71832f2b409b77` | feat: log file persistence + UI text search | unknown | unprocessed | — | — | — | — | — | — |
| `fc5711398081c0e313d9e4276488bc71816dbf48` | Tasks 4, 5, 7: validation settings UI, RAR fixtures, unit test fixes | unknown | unprocessed | — | — | — | — | — | — |
| `a6a11892954ef66deae95fbf9a2b066138e355ba` | Fix path pair stats: progress %/bytes overcounting | unknown | unprocessed | — | — | — | — | — | — |
| `de8b602b3d08955a7d721d03e93201fcbb59f561` | chore: add ssh/ to .gitignore to prevent committing runtime SSH keys | unknown | unprocessed | — | — | — | — | — | — |
| `79f7cabf12cddfa90b3d5484f0eb8a88a1b609d9` | feat: validation settings UI, RAR fixtures, unit test fixes, stats fix | unknown | unprocessed | — | — | — | — | — | — |
| `b5373c9bcaffacca167694a513d45b4978923bad` | docs: add Docker Hub badge, update image references, replace To-Do with Docker Hub section | unknown | unprocessed | — | — | — | — | — | — |
| `9e1aeead4907840106250e29249ffac7e81f0f19` | security: apply trivial hardening fixes (T1-T7) | unknown | unprocessed | — | — | — | — | — | — |
| `32acba61010215c7adb0ccf8a8eaa3afc29cc2ca` | security: apply easy-tier hardening fixes (E1-E6) | unknown | unprocessed | — | — | — | — | — | — |
| `78a3fdea5db2784e7bb4fe675ab901e124033b00` | security: apply moderate-tier hardening fixes (M1-M3) | unknown | unprocessed | — | — | — | — | — | — |
| `9f91d1c41fb9a76e6ae393663ce7fce6e6f02b63` | security: H1 - API key authentication layer | unknown | unprocessed | — | — | — | — | — | — |
| `b6986e1296efab645f066f0f57883ce299e6ecef` | fix: add api_key to IWeb interface in Angular config model | unknown | unprocessed | — | — | — | — | — | — |
| `1661b935411f30b2eab97b3badcc9a115eba0402` | fix: restore LFTP queue command format and add api_key to Angular config model | unknown | unprocessed | — | — | — | — | — | — |
| `ea4ae40f41a32e2a539bc32b8257fb744dc57551` | feat: multi-select and bulk file operations on dashboard | unknown | unprocessed | — | — | — | — | — | — |
| `2238a326037bbf5a106b452a5bdf4a7b238bb0fa` | fix: set PTY width to 10000 cols to prevent LFTP line-wrapping crash | unknown | unprocessed | — | — | — | — | — | — |
| `4516bd5392c5bf116fddbba98ff0a360e0278089` | fix: only backup settings.cfg when content has changed | unknown | unprocessed | — | — | — | — | — | — |
| `866921b6929e8c9134cca25d5c03341bf5e75da7` | Add settle_delay_secs to eliminate OS page-cache false positives in validation | unknown | unprocessed | — | — | — | — | — | — |
| `e2f17945a86002e18a530eea1400c3d1cab04b2b` | Add settle_delay_secs to rapidcopy.py default config | unknown | unprocessed | — | — | — | — | — | — |
| `4fca389709ff228c72d70f3c04ee7679b8f74fba` | fix: build scanfs against Debian Bullseye (GLIBC 2.31) for Ubuntu 22.04 compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `207b75e34e1add2a543c74a424ca907c69b60c6c` | fix: add read+execute permission on setup_default_config.sh | unknown | unprocessed | — | — | — | — | — | — |
| `2e175c0cb0ff6d5f56af3d9f2dd9d4833dc941e1` | fix: ensure Python source files are world-readable after COPY | unknown | unprocessed | — | — | — | — | — | — |
| `d4e4b7e0c467c87dc527a6fad30fc11c39dea060` | Fix logs page lockup: cap live DOM and default to INFO level | unknown | unprocessed | — | — | — | — | — | — |
| `ee0718ab6633cfc00d2b822bb4a658fde8f43226` | Add dashboard pagination and fix test_config for deleted_age_off_secs | unknown | unprocessed | — | — | — | — | — | — |
| `3ad06ceae2056a18873a7fd7d966987fd88dd58b` | fix: handle malformed lftp queue lines gracefully to prevent controller crash | unknown | unprocessed | — | — | — | — | — | — |
| `c1e079af7712ab1b51b3ca346383db91af6f4952` | fix: make LftpJobStatusParser resilient - skip bad output instead of crashing | unknown | unprocessed | — | — | — | — | — | — |
| `5db8f3435087ca93e03c7d66b3d48bdd447d37d0` | fix: increase pexpect timeout to 30s and demote timeout log to DEBUG | unknown | unprocessed | — | — | — | — | — | — |
| `62e14e23af040f3b59eae30ecd6190182e408826` | fix: catch LftpError from raise_pending_error to prevent controller crash | unknown | unprocessed | — | — | — | — | — | — |
| `8d6b436862d634f08f76b5061e04f69fef88146f` | feat: add prioritize to move a queued file to the front of the download queue | unknown | unprocessed | — | — | — | — | — | — |
| `c4871787f264bfb186659a6c61c5d6fe7ed34212` | fix: catch pexpect EOF to prevent lftp process crash from killing controller | unknown | unprocessed | — | — | — | — | — | — |
| `cb554718f7525cf02a1b2c3560c661d89ecf6d5b` | fix: cap num_max_total_connections at 32 to prevent FD_SETSIZE crash | unknown | unprocessed | — | — | — | — | — | — |
| `207caf5f92258d42b75841a3aca21713f40286ba` | feat: update settings UI to document 32-connection cap on Max Total Connections | unknown | unprocessed | — | — | — | — | — | — |
| `0c73e2374481b4ac4b1f3796237e02ac7d72a2fa` | Fix file permissions: set process umask 002 so downloads get 664/775 | unknown | unprocessed | — | — | — | — | — | — |
| `c65ddf6e01c6ee9ed4e21bf3c84bf29398f48269` | Merge pull request #3 from ppastur/fix/scanfs-glibc-compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `6ce7c197746e0fad2655662b82f041221e760787` | feat: staging directory + interrupted-download auto-resume | unknown | unprocessed | — | — | — | — | — | — |
| `c300b72f808772b00cc977ccceaa23f3c373ce33` | Fix DELETE_LOCAL to fall back to staging path when file not in local_path | unknown | unprocessed | — | — | — | — | — | — |
