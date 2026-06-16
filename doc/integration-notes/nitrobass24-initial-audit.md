# nitrobass24 Initial Audit Manifest

This manifest freezes the full reachable history from `nitrobass24/develop` for audit accounting, with baseline rows 1-87 and fork-unique rows 88-956.

- Source branch: `nitrobass24/develop`
- Initial audit frozen tip: `38d6ef22d36b6a75c164bc754bac9cd2842e8722`
- First commit: `8b1007c8a8a2d412637c2c1b642b81ba49d94cbd`
- Last commit: `38d6ef22d36b6a75c164bc754bac9cd2842e8722`
- Total reachable rows: `956`
- Non-merge commits: `732`
- Baseline rows already in `origin/master`: `87`, rows `1-87`
- Fork-unique audit rows: `869`, rows `88-956`, `ec38aaf6e6ca0ab2479fcd003d15679007101021` through `38d6ef22d36b6a75c164bc754bac9cd2842e8722`
- Common base with local `origin/master`: `ff2a1039935beccbbf7ec76134b41d2e91137742`; row `87` is the final baseline accounting row, and rows `88-956` are the fork-unique audit workload.
- Fetch posture: use `git fetch --no-tags nitrobass24 --prune`; branch refs anchor this lane because local `refs/tags/v1.0.0` collides with upstream `refs/tags/v1.0.0` and is intentionally ignored.
- Disposition rule: rows `1-87` are `baseline / already in origin`; rows `88-956` start `pending` and remain the audit workload until dispositioned.
- Frozen ordering: oldest-to-newest topological order from `git log --reverse --topo-order`.

| Index | SHA | Date | Subject | Disposition |
| --- | --- | --- | --- | --- |
| 1 | `8b1007c8a8a2d412637c2c1b642b81ba49d94cbd` | 2020-05-31 | Updated version to v0.7.2 | baseline / already in origin |
| 2 | `dc3392b6419e534506b3e0558723d9bf83abb55a` | 2020-05-31 | docker: fixed scp error with unknown user | baseline / already in origin |
| 3 | `0227990f46c0e9fa4d6696dcba911a4fcad16c7a` | 2020-05-31 | Updated version to v0.7.3 | baseline / already in origin |
| 4 | `e0352779c0831f476215ea3808ec0efb64e9cdf1` | 2020-06-05 | Test remote server instructions in dev readme | baseline / already in origin |
| 5 | `9c05838abc4f400f2ce8ee2cf73ce4467054b043` | 2020-06-07 | Py-scanner: recoverable errors from remote scanner | baseline / already in origin |
| 6 | `ab30dde08e1741964692160c9a070dbad59f8cb9` | 2020-06-08 | py-scanfs: scan errors printed in a single line | baseline / already in origin |
| 7 | `3f84b72499db2222f0c4b3458d39504111742f15` | 2020-06-08 | py-ssh: more descriptive error messages | baseline / already in origin |
| 8 | `06dd9fda74594482cbbd9d171d415de1897fa937` | 2020-06-08 | py-scanner: more non-recoverable errors | baseline / already in origin |
| 9 | `b962d1aacdebb9d19ffa3c83898b429721b70bdb` | 2020-06-08 | py-controller: send remote scan errors to web | baseline / already in origin |
| 10 | `3f1af408a7449a1da5fa014f49a5f9c8c7129446` | 2020-06-08 | py-lftp: fixed parsing out of log line | baseline / already in origin |
| 11 | `b8e1afd9456305f5b33ca811e373cbc66e0be51e` | 2020-06-08 | docker: updated dockerignore file | baseline / already in origin |
| 12 | `712e82b96d0904829a25ffa715e73223a7b07965` | 2020-06-09 | Ng: notification for remote server error | baseline / already in origin |
| 13 | `cbcc9f6b64588546186a8f7dcc9b9a49f10981c0` | 2020-06-09 | Py-scanner: remote md5sum only fails on bad ssh | baseline / already in origin |
| 14 | `80359d21e49dda1489ef5d87edba58f470ce2771` | 2020-06-09 | Updated version to v0.8.0 | baseline / already in origin |
| 15 | `7a4a81dbc7238b595338ddf1242f2860854e5dcd` | 2020-06-11 | Py: fixed python tests for docker | baseline / already in origin |
| 16 | `3233e7510b8531a7194198eb85931f01d55a69bf` | 2020-06-13 | travis: initial config | baseline / already in origin |
| 17 | `8f5a7015ee515caaaf5a601a0f49c89302ceaa57` | 2020-06-13 | travis: switch dist to ubuntu 18.04 | baseline / already in origin |
| 18 | `e722fc18276e66480456f574653cc1286db572b2` | 2020-06-13 | travis: upgrade docker to latest version | baseline / already in origin |
| 19 | `e6f5d134abbddad7a8b18cbc747e73445a09ab15` | 2020-06-13 | travis: different method of upgrading docker | baseline / already in origin |
| 20 | `20b1cdc65227491c7d3e477937eb4f8b45ef6d69` | 2020-06-13 | travis: pull external images before docker build | baseline / already in origin |
| 21 | `8dd1362fa9a87ec6a7a0b917b4a75eeaa315e5b4` | 2020-06-13 | travis: docker pull needs to be multiple cmds | baseline / already in origin |
| 22 | `9254b60363cf44623334fdfbdfb47596c0560ba3` | 2020-06-13 | travis: run all tests | baseline / already in origin |
| 23 | `e6be9f4f5ee2e769b1cf5618560330410e7d6f4b` | 2020-06-13 | travis: plain progress for buildkit | baseline / already in origin |
| 24 | `b9bcd9f6b8cda1f7d249d8eeb2e873e5c5185051` | 2020-06-13 | docker: e2e tests timeout if app not configured | baseline / already in origin |
| 25 | `55e2555be28d9b5df64d6aee4bdf13c0b15c3285` | 2020-06-13 | make run-e2e-tests exit with error if tests fail | baseline / already in origin |
| 26 | `ae887c34f604470961ed5b2d385183c6c5b40747` | 2020-06-13 | python and angular tests error propagate to make | baseline / already in origin |
| 27 | `2106680c2cb2e0cd6a2ceb2661118e16f12ffc84` | 2020-06-13 | Made python and angular docker tests less noisy | baseline / already in origin |
| 28 | `e8e77441fed9a6d5f3fb820055bb76f282b2985b` | 2020-06-13 | travis: moved unit tests to before_install | baseline / already in origin |
| 29 | `d27e4fc44714fe7b2796533f914b839223bf2336` | 2020-06-13 | docker: increase pexpect timeout in stage/deb | baseline / already in origin |
| 30 | `053aba6943dfa076fc1521717d23701c5e4e43e2` | 2020-06-13 | Updated dev readme | baseline / already in origin |
| 31 | `7ef751b35237ad0591b085bed7930be16a263c0d` | 2020-06-14 | py-lftp: increase timeout for lftp tests | baseline / already in origin |
| 32 | `00942cf2928c9edeb045037c60ce29eaa5a34ae1` | 2020-06-17 | docs: created mkdocs documentation | baseline / already in origin |
| 33 | `1160840d1fdff6a9efd90e6252a176c1da7221cd` | 2020-06-17 | Updated README | baseline / already in origin |
| 34 | `8437d7a5a7f0ae584a7551f7eea4cd839022819a` | 2020-06-17 | py-lftp: fixed parser error | baseline / already in origin |
| 35 | `f236d2cfcce3c272b11bc87490a7be389238c593` | 2020-06-17 | Updated version to v0.8.1 | baseline / already in origin |
| 36 | `ca814b2a418f5de0db054211a11a58eec11d89a8` | 2020-06-18 | py-lftp: fix error with timeout | baseline / already in origin |
| 37 | `2f2de7569e0a94d97c1e35820434fd6f27e56fa9` | 2020-06-26 | Added tests for unicode support | baseline / already in origin |
| 38 | `8ed6c0a620f9dfd0eab39800d2e9c1971353ff0b` | 2020-06-26 | Py-lftp: fixed more lftp parser errors | baseline / already in origin |
| 39 | `9816ddfb56399f6866601d91f32b053dc8936cb9` | 2020-06-26 | Updated version to v0.8.2 | baseline / already in origin |
| 40 | `cea1781e2ce7dd8ae86b1092cacbada29d168024` | 2020-06-26 | Simplified README | baseline / already in origin |
| 41 | `39d3a1f07b9489588d9eadd7bd2d6e5dd5c917ad` | 2020-06-26 | README: added badges | baseline / already in origin |
| 42 | `9745f7075735f9bcb3aa63c989b14e3720b7772e` | 2020-06-29 | py-lftp: ignore first two consecutive status errs | baseline / already in origin |
| 43 | `b18a9ff8c65268f9dfa79b43498826ceba7c3da2` | 2020-06-29 | Updated README | baseline / already in origin |
| 44 | `22e2137e90bc4205b196f054c596f5b1945d5d90` | 2020-06-29 | Updated version to v0.8.3 | baseline / already in origin |
| 45 | `b214a4c56a46eb20bfccc7b329e47b375778f094` | 2020-06-30 | Updated documentation | baseline / already in origin |
| 46 | `d076d7fe316bf803803c0a28e5e0e0d94ae0b90c` | 2020-08-08 | docs: added blurb about locale errors | baseline / already in origin |
| 47 | `17e2d29a440ba206237571810a1c0877f184ba94` | 2020-08-14 | py-parser: another parser case handled | baseline / already in origin |
| 48 | `7226f140c1c5cdfa905f47915d711acc70081601` | 2020-08-14 | Updated Pip dependencies | baseline / already in origin |
| 49 | `24d5ead92904223cbbca585c0d342014cf3cf71d` | 2020-08-15 | Py: print platform arch on first start | baseline / already in origin |
| 50 | `02629a9e3a1e7f36af996d2f56c083b2e5134efa` | 2020-08-15 | Multi-arch build system and instructions | baseline / already in origin |
| 51 | `5a3b1fa11feae955f90da54988281467e3edc5a1` | 2020-08-15 | Small DevReadme update | baseline / already in origin |
| 52 | `86d05a0303edb97c27160ea95bf16bdefe277ba5` | 2020-08-16 | docker: fixed python tests failure | baseline / already in origin |
| 53 | `5a78f4ebfd82787194e8fb7ae526fa988ef5bbe9` | 2020-08-16 | Updated dev readme | baseline / already in origin |
| 54 | `6b180f6f64e6e0ad41faa1eca2942c0c16337b8f` | 2020-08-19 | Updated dev readme | baseline / already in origin |
| 55 | `ae2e3fa6dc7507d07e5db6a836a5c0e9a12239ef` | 2020-08-19 | Updated docs for raspi installation | baseline / already in origin |
| 56 | `7af5e0dd7f047e9fff6e4377b021bd2f1eca4320` | 2020-08-19 | Updated version to v0.8.4 | baseline / already in origin |
| 57 | `a1137fd3537d4b5409fa66fd3aa734d803272484` | 2020-08-19 | Remove build shield until CI is fixed | baseline / already in origin |
| 58 | `b4bf9e65b281b9f2581b926ed2db709442e4378c` | 2020-08-19 | Updated dev readme | baseline / already in origin |
| 59 | `33f4e4f4b96c2826c57275e8eb979b115ad38541` | 2020-08-21 | docs: added requirements blurb | baseline / already in origin |
| 60 | `1cf198aa83e44553584986f52d4ca95cee8fc650` | 2020-12-20 | Py: Adds poetry config | baseline / already in origin |
| 61 | `7e197308a7b0ba12e76e227892aab7c05e2952f7` | 2020-12-20 | Py: improves lftp test error reporting | baseline / already in origin |
| 62 | `1140f21d86547a5b44cd3ec33e8095d31e161c0a` | 2020-12-20 | Py: sanitizes non-utf8 chars at scan boundaries | baseline / already in origin |
| 63 | `8609a2e7ecb7ee89776a7ead20283d41761d8e8e` | 2020-12-20 | Py: minor fixes to web handler tests | baseline / already in origin |
| 64 | `20d7457eb8f22293b00db27e1d799ac902cdec7d` | 2020-12-21 | Docs: switches pipenv -> poetry | baseline / already in origin |
| 65 | `b233ce57ce7dbbb515ef9795b96170d4029fc8dd` | 2020-12-21 | Py: switches from green -> pytest | baseline / already in origin |
| 66 | `b4cdc79d46daf81e2a6e353faccaa76e12413f10` | 2020-12-21 | Py: removes pipenv files | baseline / already in origin |
| 67 | `32939c4137ec0f8efc12441d5a7827c5256a0e44` | 2020-12-28 | Updated build system to use poetry | baseline / already in origin |
| 68 | `c4b99183524167fe7992fb282d98f933fa3c2604` | 2020-12-28 | Adds sample Github Actions workflow | baseline / already in origin |
| 69 | `9f92247fffdee7e79ae94c69db750992775044e0` | 2020-12-28 | Make: consolidates env vars names to be consistent | baseline / already in origin |
| 70 | `00a97dbce6f3b0e754da65f170f19c8fabd218ed` | 2020-12-28 | Make: Further consolidates REGISTRY env var | baseline / already in origin |
| 71 | `ed7970119c69d93414c2615f7545f203835d8c16` | 2020-12-28 | Make: makes staging registry configurable | baseline / already in origin |
| 72 | `1099e6aff10677c3dd9769a8c9dfb3d4c095dece` | 2020-12-29 | Make: makes version configurable during build | baseline / already in origin |
| 73 | `0fd99b7dd8018e520b476dda9767706b9636deb8` | 2020-12-29 | Adds buildx driver opts for all GA jobs | baseline / already in origin |
| 74 | `564550c6442e3a5deda7b4968d2fd0d4b95c41fc` | 2020-12-29 | Fixes staging registry var in staging compose | baseline / already in origin |
| 75 | `eeb4e397fa64874c263b50f22ac1902cfc7182b5` | 2020-12-29 | GA: adds full workflow for master branch | baseline / already in origin |
| 76 | `60e0821e5353c10a82355ad02115f74cbcf36fb4` | 2020-12-29 | Make: Adds build cache for final docker image | baseline / already in origin |
| 77 | `e449d50964ce09318a4c05c8a2e83b8877500ffd` | 2020-12-29 | Make: enables caching for intermediate images | baseline / already in origin |
| 78 | `a7653ffb0d79fac9e716e6cb0e88a29c26c77e8a` | 2020-12-29 | GA: Adds deb and docker image release jobs | baseline / already in origin |
| 79 | `2587b6c3701d1962adfd71f3d1d6b58ce71c0518` | 2020-12-29 | Make: Renames SEEDSYNC_VERSION -> STAGING_VERSION | baseline / already in origin |
| 80 | `80c70f219588f09bb5e9dea1445c2688a69e6f42` | 2020-12-29 | Make: Separates staging and release versions | baseline / already in origin |
| 81 | `323581719e167b25559262bd4db995221456232b` | 2020-12-30 | Reduce docker image size by half by via slim | baseline / already in origin |
| 82 | `c76a36acd5ca95117ec70942d954ef589a7b11bb` | 2020-12-30 | Removes travis config | baseline / already in origin |
| 83 | `3a6582d4889be150b78366727b6786d766421116` | 2020-12-30 | Removes unrar from release, adds rar to unittests | baseline / already in origin |
| 84 | `fabd235d4ec34bf538bc4a950180e247c6ec2d5a` | 2020-12-30 | Updates DevReadme with new release instructions | baseline / already in origin |
| 85 | `cc5d04a5b9e1c9a67ca99e9d7d2afe96b2ec9a04` | 2020-12-30 | Changelist for Release v0.8.5 | baseline / already in origin |
| 86 | `747cb82080db7381936a0981849735c5bbdfa835` | 2020-12-30 | Fixes broken rar extraction. | baseline / already in origin |
| 87 | `ff2a1039935beccbbf7ec76134b41d2e91137742` | 2020-12-30 | Changelist for Release v0.8.6 | baseline / already in origin |
| 88 | `ec38aaf6e6ca0ab2479fcd003d15679007101021` | 2024-11-29 | Create entrypoint.sh | adapted locally in `89362f026db73c655d4a286c57c5abbe860c8fcb` |
| 89 | `9ea6d980594eaabb8eabe95b768ac631ede674c8` | 2024-11-29 | Update Dockerfile | covered elsewhere |
| 90 | `009eade5876cb8f522259c863b16b2789631233e` | 2024-11-29 | Create docker-image.yml | covered elsewhere |
| 91 | `3154172f7eb38b12b8e597766ce5602baa27e5ab` | 2026-01-25 | Fix Docker image build - complete multi-stage Dockerfile | covered elsewhere |
| 92 | `0b4a6c249a2ad04700f7dd3cec0189f73dad48b9` | 2026-01-25 | Docker-only deployment with optimized image (240MB) | needs area reopen |
| 93 | `541b63093adaa7fbd214b2bc864238257b423061` | 2026-01-25 | Update CI/CD and documentation for Docker-only deployment | needs area reopen |
| 94 | `18643263e6e48e520058f6f2251e0fdfbccc69d5` | 2026-01-25 | Fix Python 3.12 regex escape sequence warnings | adapted locally in `1d30a9ec7c85a80801f8a04d2e56a6e3db269f95` |
| 95 | `0e0a7d159f470faa970ff566fdb8bca46807ac95` | 2026-01-25 | Fix release workflow to mark new releases as latest | adapted locally in `391b171a0797d45745e5908d513181b7e97a8751` |
| 96 | `e310c0582a98645ca4c2bb566a9c0b691a8232be` | 2026-01-25 | Update CHANGELOG for v0.9.2 [skip ci] | intentionally skipped |
| 97 | `420deefd657e0890f0e450d16d0982997d69798f` | 2026-01-25 | Update dependencies to address security vulnerabilities | covered elsewhere |
| 98 | `01d12edb29397a9799bea906e015671d82259e3c` | 2026-01-25 | Remove poetry.lock - using requirements.txt instead [skip ci] | intentionally skipped |
| 99 | `b7d197d6ec332ecd29ecc3d4f7703de14c85a0c1` | 2026-01-25 | Update CHANGELOG for v0.9.3 | intentionally skipped |
| 100 | `122d4888084933a1e09410681c84e8176aeb0448` | 2026-01-26 | Fix Docker entrypoint UID/GID handling for Synology and shared groups | adapted locally in `89362f026db73c655d4a286c57c5abbe860c8fcb` |
| 101 | `3bef8dbd1d26f36e9967633fa7230341bc36b55d` | 2026-01-26 | Merge pull request #6 from nitrobass24/fix/issue-4 | covered elsewhere |
| 102 | `9bb477a43e6ffff9a06a0bef34a21fbee7ba92dc` | 2026-01-26 | Fix scanfs glibc compatibility for older seedbox servers | adapted locally in `c5d042018043cc274603e075b166bfa6b519b354` |
| 103 | `26e6113df8a135a2b003826931c5e74ae2074170` | 2026-01-26 | Merge pull request #7 from nitrobass24/fix/issue-5 | covered elsewhere |
| 104 | `151fef6b4e27893966827a373aee9101551b0e5b` | 2026-01-26 | Update modernization plan with Angular 17 and v0.9.4 fixes | intentionally skipped |
| 105 | `7fbcda45defc0ebeadb1fadb771081238f570472` | 2026-01-26 | Update GitHub repository references from ipsingh06 to nitrobass24 | maintainer decision needed |
| 106 | `4e96f8ff497d8d03b7a36f87c273309ac71c73df` | 2026-01-26 | Remove Deb package references - Docker-only deployment | intentionally skipped |
| 107 | `c247e71cfb67a5b319712010f658e3d866fcc387` | 2026-01-26 | Add docs site under website | intentionally skipped |
| 108 | `785318bcb1e8afe28e8c852fc4ebe75311df547f` | 2026-01-26 | Add docs lockfile | intentionally skipped |
| 109 | `1c6077c21f7e5e8fc9951555b3ba3c936167190a` | 2026-01-26 | Merge pull request #8 from nitrobass24/docs-website | covered elsewhere |
| 110 | `70746b441a8f9551fbcf5a5a96b7be37eba3f8a1` | 2026-01-26 | Add documentation website link to README | intentionally skipped |
| 111 | `0bc4613cf61d8d69815809dffcf6c51fdaecd850` | 2026-01-26 | Remove legacy mkdocs documentation | intentionally skipped |
| 112 | `366bf9c28eb5f7bcbe3bb33422bdeca0e0b78cd0` | 2026-01-26 | Fix footer background styling issue | intentionally skipped |
| 113 | `ebb0c5c9b33bdd54fe8a4fef8c5b0ee97c2f6244` | 2026-01-26 | Fix Edit this page background to blend with page | intentionally skipped |
| 114 | `545b89a3e610c25acb20a7600e0dd2930fc2c0d1` | 2026-01-26 | Add downloads badge to README | intentionally skipped |
| 115 | `28b0a2b1b8cd78c50b77cddc2522d1d8b1dc99a4` | 2026-01-26 | Add GHCR image size badge to README | intentionally skipped |
| 116 | `00a75d193012d896a0b655cfa81df8cbbbd95891` | 2026-01-26 | Add GHCR Docker pulls badge to README | intentionally skipped |
| 117 | `96aabb63a3c52cab9d60bb9573675621cac66371` | 2026-01-26 | Disable Docker attestations to remove unknown arch entries | intentionally skipped |
| 118 | `b3392197fbc6859962ee992108d7f6898717779c` | 2026-01-26 | Update MODERNIZATION_PLAN.md | intentionally skipped |
| 119 | `5f203bedcfcc9c7424592fc0c0a7740ddd726f7b` | 2026-01-26 | Update Dockerfile | covered elsewhere |
| 120 | `037709a08976c609ba87ca4c7e63ec0bb9a202ef` | 2026-01-26 | Update Dockerfile | intentionally skipped |
| 121 | `c2cebd403176852ce1670a8f5a5c016903ac9fb3` | 2026-01-26 | Merge pull request #11 from nitrobass24/nitrobass24-patch-1 | covered elsewhere |
| 122 | `575629393f42eb238bb1fe1c25129f305f28987e` | 2026-01-26 | Migrate Angular 4 to Angular 17 with standalone components | needs area reopen |
| 123 | `c59bccc6e1cefe371f11ceba683e117c4d397028` | 2026-01-26 | Fix scanfs builder to use Python 3.11 for buster compatibility | adapted locally in `c5d042018043cc274603e075b166bfa6b519b354` |
| 124 | `b39d67b67fd6f0889a2fb852e6aef9ff57dd3a93` | 2026-01-26 | Switch scanfs builder to Debian Bullseye | intentionally skipped |
| 125 | `053cc36385ed89d5fe370d6c3852aa27a49b6e47` | 2026-01-26 | Merge pull request #10 from nitrobass24/angular-upgrade | covered elsewhere |
| 126 | `0508a7a841b7b0a777e97fddb673cd1b114a3a7c` | 2026-01-26 | Update CHANGELOG and MODERNIZATION_PLAN for v0.10.0 | intentionally skipped |
| 127 | `0b1619f9aa3e29190e833a6777dd44f02088338c` | 2026-01-26 | Add CLAUDE.md with project instructions and release process | intentionally skipped |
| 128 | `43c35ae37a3027516bba76e592dcd14e8b6acbdd` | 2026-01-26 | Rollback Angular 17 to Angular 4 (v0.10.1) | intentionally skipped |
| 129 | `e6d32c4e5629ac22fd83ca16ce4d6375ed43660a` | 2026-01-26 | Fix scanfs binary architecture for ARM local machines | adapted locally in `0e189f2ccb9ddd3378347a56a47620d02e9001ea` |
| 130 | `b7b45d3772576f444c5e5a85db9c426ade39fbb0` | 2026-01-27 | Fix restart button, settings persistence, and update About page | covered elsewhere |
| 131 | `ce7527f395cc673be5cbd61e745d5aa34ae78def` | 2026-01-27 | Update CLAUDE.md with release process details | intentionally skipped |
| 132 | `11d003f80d727aecc78766d6d925b4f6bacc1ddc` | 2026-01-27 | Remove angular-v17 from master - preserved in angular-17-upgrade branch | intentionally skipped |
| 133 | `c46c4e32e4d17cdc5a74ccdb2244956126283307` | 2026-01-27 | Update CLAUDE.md - document branch structure | intentionally skipped |
| 134 | `372f7e0cc860a106567f579a6932ccd88d11f9cc` | 2026-01-27 | Add path filter to docs workflow - only deploy when website/ changes | intentionally skipped |
| 135 | `9cbb21b92368a363a91c6aa8e1296a1f4ad61bbf` | 2026-01-27 | Only publish Docker images on release tags | covered elsewhere |
| 136 | `b3a37b1be1dcd754c32dfe14eaaf7d235c64ed91` | 2026-01-27 | Document CI/CD workflow behavior in CLAUDE.md | intentionally skipped |
| 137 | `d2c42620e69a115880af42c4656e841534c295a6` | 2026-01-27 | Fix remote paths with tilde (~) for shell expansion (#14) | adapted locally in `edbdeac2ff7bb034fdd25700beef9523bf75816a` |
| 138 | `dc3da40b92ca4d7885d8d35259eee21f86803d0d` | 2026-01-27 | Fix LftpJobStatusParser crash on empty output (#15) | covered elsewhere |
| 139 | `cac415e05d7a60c9568a980a0f5dd40189f11c17` | 2026-01-27 | Add CI workflow for develop branch | intentionally skipped |
| 140 | `f1359e90029a1cce0c0379924d47399f7a645d83` | 2026-01-27 | Strip ANSI escape codes from LFTP output before parsing | covered elsewhere |
| 141 | `59db35c230ec4422c7b76411e11052470399f931` | 2026-02-02 | Improve error messaging for remote shell not found | adapted locally in `ff1499f4b41f9b6abb61d5fc446992aebcdd554e` |
| 142 | `054fb83fc855b7feba7b3104550fd7dc3b775d67` | 2026-02-07 | Release v0.10.5 - Fix tilde delete, shell detection, optional password, bandwidth limit | adapted locally across `edbdeac2ff7bb034fdd25700beef9523bf75816a`, `ff1499f4b41f9b6abb61d5fc446992aebcdd554e`, and `9f57de252b156587da48913e29809cf3c04724a3` |
| 143 | `7af489e78c1fba931bb8eef950da89c34d25edfe` | 2026-02-07 | Release v0.10.6 - Auto-delete from remote after download (#25) | adapted locally in `e99ac88cd5bc33a7ac1a1494d47af09d24c36906` |
| 144 | `7260932ab331bf069f844a6ef0a56d6d703b48ca` | 2026-01-27 | Merge pull request #16 from nitrobass24/develop | covered elsewhere |
| 145 | `69aef40a1f346c5c280e32db03e92c092b6f8742` | 2026-02-07 | Merge pull request #28 from nitrobass24/develop | covered elsewhere |
| 146 | `afc3f5824a8b5b8bd889b6af5d110b604ad19b14` | 2026-02-07 | Merge master into develop | covered elsewhere |
| 147 | `2ee0e21bae50451c04effd89bc1abb1c5773dcf3` | 2026-02-07 | Migrate frontend from Angular 4 to Angular 21 | already integrated |
| 148 | `61fbfc50e0366a0895d532b65ff6c094465b0436` | 2026-02-07 | Fix SSE streaming bootstrap, checkbox rendering, and Dockerfile for Angular 21 | covered elsewhere |
| 149 | `cb2a9ff12e020224de4679099c628b5d5a83b2f7` | 2026-02-07 | Add Bootstrap JS for dropdowns, eagerly init filter/sort services, simplify settings | covered elsewhere |
| 150 | `b1db68bd05444c598a30366dce17740a26976c1a` | 2026-02-07 | Clean up build warnings: adjust budgets and fix Dockerfile platform arg | covered elsewhere |
| 151 | `2cb09a6a697e8007e8fba1796e5c59c7bd340ffe` | 2026-02-07 | Prepare v0.11.0: fix FA icon, dynamic version, remove old code, update docs | covered elsewhere |
| 152 | `8e57a35d2f2f0b87907c2a4fa40476ae73c3a689` | 2026-02-07 | Add CI workflow for angular-21-upgrade branch | intentionally skipped |
| 153 | `e79915a466de4d54f9ecf87261bb721f02c920d3` | 2026-02-07 | Add comprehensive Angular unit tests (125 tests across 15 files) | covered elsewhere |
| 154 | `5d540655341e9376608df6cb6ad89d16a5c377ae` | 2026-02-08 | Track package-lock.json for reproducible Docker builds | covered elsewhere |
| 155 | `8b409c5c76b0c89c1e8212ef0d143ee34a29009f` | 2026-02-08 | Prep v0.11.0 merge: update changelog, remove branch CI | covered elsewhere |
| 156 | `066fa3f5a7a1b5d3ee74af1fde9cb8132e229677` | 2026-02-07 | Merge pull request #29 from nitrobass24/develop | covered elsewhere |
| 157 | `464c04f596b90c81056baefa3541953b331b88fc` | 2026-02-07 | Update docs for v0.10.5 and v0.10.6 features | covered elsewhere |
| 158 | `a793d106fbf9e60da1e57026917b046ffc84e1b7` | 2026-02-07 | Merge pull request #30 from nitrobass24/develop | covered elsewhere |
| 159 | `8a23231c34e3c6288098d84d67ceac073e5d7601` | 2026-02-08 | Merge master into angular-21-upgrade | covered elsewhere |
| 160 | `f5b08cc5b2523003e06c957d43d632bb9b2a4f31` | 2026-02-08 | Merge Angular 4 → 21 frontend rewrite (v0.11.0) | already integrated |
| 161 | `71b2e77e35dfca6d01ded29d057fe5ce930f78ec` | 2026-02-08 | Update CLAUDE.md for Angular 21 and current branch state | intentionally skipped |
| 162 | `04cefc303acc4566ebc2d61e201fa62b9942ef26` | 2026-02-08 | Fix config file overwritten on container restart | already integrated |
| 163 | `c9d539121f5ea9e06e1ba89faa61649e97b75781` | 2026-02-08 | Release v0.11.1 - Fix config overwritten on container restart | covered elsewhere |
| 164 | `de7b7bdd8f856f484ba6bd3aa2a86d24c2c34908` | 2026-02-08 | Add docker pull command to release notes template | intentionally skipped |
| 165 | `9faf724a51c47b6872e1e4b2258a847c0c8d9dda` | 2026-02-09 | Move web access logs to DEBUG level to reduce log noise | adapted locally in `5368c551be44634cf4178510ca15cda1700242d5` |
| 166 | `b63244d7e2c5bcb5e26e452ece0de17b40edd8d5` | 2026-02-09 | Release v0.11.2 - Move web access logs to DEBUG level | covered elsewhere |
| 167 | `c636dd68fd9f22a9d7cf09ca0ba8633f1eccca2a` | 2026-02-09 | Harden config persistence against data loss | already integrated |
| 168 | `864c1c205db3d22dceafb0f510f952ef61880dbf` | 2026-02-09 | Release v0.11.3 - Harden config persistence against data loss | covered elsewhere |
| 169 | `edac8873cb8c43c171b6c995376d0f356418fe34` | 2026-02-09 | Merge pull request #34 from nitrobass24/fix/config-persistence-hardening | covered elsewhere |
| 170 | `90ce595d0296b77ecf8d47d3b3119fb8332fdc32` | 2026-02-11 | Add staging directory feature for fast-disk downloads | already integrated |
| 171 | `a0992912215e0b279ccd1cd82b311d702b17fc7a` | 2026-02-11 | Merge pull request #36 from nitrobass24/feat/staging-directory | covered elsewhere |
| 172 | `c2037c0ff3370246b57facce2aa08f4ecb5cadfd` | 2026-02-12 | Add dark mode support with theme toggle (#133) (#37) | covered elsewhere |
| 173 | `35586499b6bb8ac6ad91132b70ce4c3b32020621` | 2026-02-12 | Fix staging directory not moving completed downloads (#38) | already integrated |
| 174 | `382cf14a2e2ee17613661b4c59a37d642fa17601` | 2026-02-12 | Fix staging directory download completion and file detection (#39) | already integrated |
| 175 | `9682f456e1a4e7f4fd1887a6c58ab2e38c47a60d` | 2026-02-12 | Add git workflow rules to CLAUDE.md | intentionally skipped |
| 176 | `75210e8352ed797816a30c21d2e04cadcfcd1181` | 2026-02-12 | Advanced LFTP settings and staging bug fixes (#40) | already integrated |
| 177 | `ad6163357d1242682facc892d5e1e3770bd620d7` | 2026-02-12 | Remove duplicate ci-develop.yml workflow (#42) | intentionally skipped |
| 178 | `f6d665d919320d1a68a146a2d10cff5e55d16b92` | 2026-02-12 | Fix Advanced LFTP settings not editable for existing configs (#43) | already integrated |
| 179 | `66cc07450513c27931ff352fdb99dd65613c9ef8` | 2026-02-12 | Change net_socket_buffer from int to string to support suffixed values (#44) | adapted locally in `e9b50b39638a6fd7327d2480393a986e3cf2ead2` |
| 180 | `b9c53132ddd3dbb51e27c7f3ffea540bc318db77` | 2026-02-12 | Graceful config upgrades: backfill defaults and save to disk (#45) | already integrated |
| 181 | `ff5bc7ca3ac1c17d1d610f66b8213abf559df593` | 2026-02-12 | Add remote server diagnostics on first connection (#41) | already integrated |
| 182 | `b6cf2023dc99e81fb88b9df402e503063d91bf6f` | 2026-02-12 | Clarify that 0 means unlimited for Max Total Connections (#46) | already integrated |
| 183 | `c487f3ec5a64362efa0149e1ec8317ed5e661102` | 2026-02-12 | Fix dashboard status sort: reorder groups and sort oldest first (#47) | adapted locally in `e55e6a4402b6b715cab09a1311d14c47186b8988` |
| 184 | `2993a5d7e51e7c89a80671598826fc935c4700db` | 2026-02-12 | Move File Discovery to left column below Archive Extraction (#48) | intentionally skipped |
| 185 | `425b0d3982dd56c3b0ce42bf0f9d8163a6bfe9d5` | 2026-02-12 | Fix crash when remote file is removed during sync (#49) | adapted locally in `b2049ad13e0fb98eca019bb764c1fa31ab725adc` |
| 186 | `cabb47906e4a8ba297720c199002777132c14aa0` | 2026-02-12 | Preserve DOWNLOADED state after remote file is auto-deleted (#50) | adapted locally in `b2049ad13e0fb98eca019bb764c1fa31ab725adc` |
| 187 | `ef28f9884f45f9a07326edc8d46439af43b53e3d` | 2026-02-12 | Redesign dark mode with 3-surface elevation system and semantic colors (#51) | covered elsewhere |
| 188 | `fa5aa24b5c2cb383274a5bf799a4b4d59a6517fa` | 2026-02-13 | Bump version to 0.12.0 and update changelog for release | covered elsewhere |
| 189 | `281c2333f6416777802b6dc3d238c0ad1bde7c9f` | 2026-02-13 | Update docs for v0.12.0 features | covered elsewhere |
| 190 | `e8d3b78a783a7a0b48c8bf069a54e4bc64cfe9a5` | 2026-02-13 | Merge pull request #52 from nitrobass24/develop | covered elsewhere |
| 191 | `f1b1877f9a157b37ab2f0eebc6c32210e2950e70` | 2026-02-13 | Fix lftp pexpect timeout recovery to prevent buffer corruption (#20) | already integrated |
| 192 | `b465d67419dae7a1ed52663f53dde34cc1d2cc9e` | 2026-02-13 | Merge pull request #54 from nitrobass24/fix/lftp-pexpect-timeout-recovery | covered elsewhere |
| 193 | `12bfce70a1d1e846d15603bf3343a374f82f0d23` | 2026-02-14 | Fix download state loss after LFTP job completion | adapted locally in `b2049ad13e0fb98eca019bb764c1fa31ab725adc` |
| 194 | `fc65492bbe197d7c50828bfd6debcdab4e5bb889` | 2026-02-14 | Merge pull request #55 from nitrobass24/fix/download-state-loss-after-lftp-completion | covered elsewhere |
| 195 | `f2cb5d8e72b52f823aae6ea1ac1129326000beea` | 2026-02-14 | Fix progress bar glitch during downloads caused by race condition | adapted locally in `b2049ad13e0fb98eca019bb764c1fa31ab725adc` |
| 196 | `fbc32607521d950a918e646b42732722cf13b5f1` | 2026-02-14 | Merge pull request #57 from nitrobass24/fix/progress-bar-glitch | covered elsewhere |
| 197 | `33e3a8fd4889949e2a64b54706c57a34829afdfe` | 2026-02-14 | Release v0.12.1 - Bug fixes for download stability | covered elsewhere |
| 198 | `43badbbe4363b94f16fabd36cd790dd800c9df91` | 2026-02-14 | Merge pull request #58 from nitrobass24/release/v0.12.1 | covered elsewhere |
| 199 | `1505cc221f07f60c9400a70dd103744dc5f43fd0` | 2026-02-15 | Fix scanfs timeout crash: pexpect leak, retry logic, SSH keepalive | already integrated |
| 200 | `834fc1c4a73b1fb7ae1c0a616d8856f6fe329eb6` | 2026-02-15 | Merge pull request #62 from nitrobass24/fix/scanfs-timeout-crash | covered elsewhere |
| 201 | `b1e250e958f785dfa917c962d4e5f461b70d10cb` | 2026-02-17 | Fix FileExistsError crash during cross-device move | already integrated |
| 202 | `b1950908347e8fc12689dedf52bdbccf464ab7dd` | 2026-02-17 | Fix app crash on transient SSH errors during scanfs installation | already integrated |
| 203 | `64aae2f57f253aa50910ea6678b7ba3ae2f65fa1` | 2026-02-18 | Fix LFTP transient errors crashing the app and reduce scanner log noise | already integrated |
| 204 | `06c53ea9e40f07ec7dd4388ace3276fe9ee915b5` | 2026-02-18 | Merge pull request #63 from nitrobass24/fix/install-scanfs-transient-crash | covered elsewhere |
| 205 | `ec2d08b480d0d1b01cf27c63998819eaa0b80255` | 2026-02-18 | Fix error handling bugs found during code review | already integrated |
| 206 | `38286023cadda2ab962c3690640fcfa199abf86b` | 2026-02-18 | Merge pull request #64 from nitrobass24/fix/review-findings-error-handling | covered elsewhere |
| 207 | `360e2d0605652e54bd0549bc53752b7ed88abe45` | 2026-02-19 | Fix LFTP status parser crash from interleaved 'jobs -v' command echo | already integrated |
| 208 | `5b44317fdad8d5a39b96ad7671dc8bf035d1c71d` | 2026-02-19 | Fix SSH shell crash on filenames with apostrophes (e.g. "Don't Look Now") | already integrated |
| 209 | `3fd9e6f6b62fe4810441cead6409166946ee679d` | 2026-02-19 | Merge pull request #66 from nitrobass24/fix/scanfs-timeout-crash | covered elsewhere |
| 210 | `bc15de5174a692a0c09bc2fa4a9d715466a3ac8a` | 2026-02-14 | Bump qs from 6.14.1 to 6.14.2 in /src/angular | already integrated |
| 211 | `2e47b83bece5fc86e9d76c0e9d7bb21590a2da1a` | 2026-02-19 | Merge pull request #53 from nitrobass24/dependabot/npm_and_yarn/src/angular/qs-6.14.2 | covered elsewhere |
| 212 | `d3c4ad331215321ea4a65e1af0cfe05d9d620c5e` | 2026-02-14 | Bump qs from 6.14.1 to 6.14.2 in /website | already integrated |
| 213 | `a98581ef9fae308bce5e1a098e97663e17df9c49` | 2026-02-19 | Merge pull request #59 from nitrobass24/dependabot/npm_and_yarn/website/qs-6.14.2 | covered elsewhere |
| 214 | `b3a08ff3806a3c435a33a1837ea63de6ea9754c6` | 2026-02-19 | Bump tar from 7.5.7 to 7.5.9 in /src/angular | already integrated |
| 215 | `bc573593e740763164a9755472c0b5697b572af4` | 2026-02-19 | Merge pull request #65 from nitrobass24/dependabot/npm_and_yarn/src/angular/tar-7.5.9 | covered elsewhere |
| 216 | `7add29da7f7c124e031d49c4eea5c1188a297252` | 2026-02-19 | Merge remote-tracking branch 'origin/master' into develop | covered elsewhere |
| 217 | `30d2f7fcb106c4577aa22499f131db31d7ecb5fd` | 2026-02-19 | Configure Dependabot to target develop branch | already integrated |
| 218 | `d2efd78ac52b892fa0353cd31e7c7328d9609e49` | 2026-02-19 | Fix wildly inaccurate directory download ETAs | already integrated |
| 219 | `373f887f0351196ae3c84780ede7b6a379b35f9e` | 2026-02-19 | Merge pull request #68 from nitrobass24/fix/directory-download-eta | covered elsewhere |
| 220 | `5f970b6fd466b3cf3283172f7acac73820ab319b` | 2026-02-19 | Bump hono from 4.11.8 to 4.12.0 in /src/angular | already integrated |
| 221 | `28717f66d3261e4fc717fb4fb6e015a0c74a79b9` | 2026-02-19 | Merge pull request #67 from nitrobass24/dependabot/npm_and_yarn/src/angular/hono-4.12.0 | covered elsewhere |
| 222 | `48aa74501d2a426f83e31e4b448dcd177bcbf599` | 2026-02-19 | Fix LFTP parser crash from terminal line wrapping on long paths | already integrated |
| 223 | `dbea249f9d28bca574a7150ae1d82b5d2cfe3fd4` | 2026-02-19 | Merge pull request #69 from nitrobass24/fix/lftp-parser-line-wrap-crash | covered elsewhere |
| 224 | `7c7586b664cad4985846c2a106ebad17db6f12a2` | 2026-02-20 | Implement post-download pipeline for correct extract/delete-remote ordering (#70) | already integrated |
| 225 | `960474f8736cb691abd77a5fef0e0045907c2b3e` | 2026-02-20 | Fix persist cleanup firing during in-flight staging moves (#71) | already integrated |
| 226 | `ef8e5e2b48012433e74aae91692d262b5d316ccf` | 2026-02-20 | Fix persist cleanup firing during in-flight staging moves | already integrated |
| 227 | `7a2ac5b81943765e56d9d8013e75e3d04d520d65` | 2026-02-20 | Fix extraction timing and re-download retry for staging pipeline | already integrated |
| 228 | `b4b35cb6daa98d7ea77fcff67b8c28f0585d1072` | 2026-02-20 | Merge pull request #72 from nitrobass24/feat/post-download-pipeline | covered elsewhere |
| 229 | `4a26970f27c4d146b04dedcb47b38fceae16996e` | 2026-02-20 | Add EXTRACT_FAILED visual state for failed extractions after 3 retries | already integrated |
| 230 | `b61bb62c7ac1171707dab8a1b2a3a55ef358f144` | 2026-02-20 | Merge pull request #73 from nitrobass24/feat/post-download-pipeline | covered elsewhere |
| 231 | `2eed690575600c966e239f152ceb7cff06afceea` | 2026-02-20 | Add CodeRabbit AI review configuration | already integrated |
| 232 | `4170a1e0380fa2d193313848b2b76a0296c6fde9` | 2026-02-20 | Release v0.12.2 - Extraction failure handling and stability fixes | covered elsewhere |
| 233 | `7afd6a0c290c3587ba2e50c46c9fbb8124cddaa4` | 2026-02-21 | Fix reading pexpect attributes after sp.close() in sshcp | already integrated |
| 234 | `622f87533cae8be4a047a0185a7b0ff12076fbf2` | 2026-02-21 | Fix model builder cache invalidation by copying persist sets | already integrated |
| 235 | `976fadfd240930f368111ba93d2d424e59338e1f` | 2026-02-21 | Add Dependabot entry for Python pip dependencies | already integrated |
| 236 | `d45cfb0920a5d3ac87a5dbe57645652df18f89a2` | 2026-02-21 | Fix reading exitstatus before process is reaped in sshcp | already integrated |
| 237 | `e1254afc8fb12e6861557ba0c1fe1ba8d388d891` | 2026-02-21 | Chain LftpError when re-raising as AppError for traceback preservation | already integrated |
| 238 | `83905bbf183f0386b21ebd6fb56481003601a091` | 2026-02-21 | Clear extraction retry count when file is re-queued for download | already integrated |
| 239 | `2a96c25bd2513776c5d8e0394ae3b4565178d1ab` | 2026-02-20 | Add CodeRabbit AI review configuration | already integrated |
| 240 | `527c52318e18d15235661cddce0594a100c5f105` | 2026-02-21 | Merge remote-tracking branch 'origin/master' into develop | covered elsewhere |
| 241 | `355a606f71f600581f30e2e527789b71f69323b7` | 2026-02-21 | Merge pull request #74 from nitrobass24/develop | covered elsewhere |
| 242 | `7127256314ffb4f5809d8c2f8f57fb9bc8a92c68` | 2026-02-21 | Fix UnicodeDecodeError crash when SSH output contains non-UTF-8 bytes | already integrated |
| 243 | `76aa3b7ce6835abe3c408a11125e538c96e9dc5a` | 2026-02-21 | Merge pull request #76 from nitrobass24/fix/sshcp-unicode-decode-error | covered elsewhere |
| 244 | `94150945c5ca9cfe5ccdc06432444bed5b627164` | 2026-02-21 | Release v0.12.3 — Fix SSH output decoding crash | covered elsewhere |
| 245 | `32bb751897709a66eeed193c0e2bd087bfa5733d` | 2026-02-21 | Merge pull request #77 from nitrobass24/develop | covered elsewhere |
| 246 | `fe0f4a68f2e039ccdf269d5683cd981059f7f620` | 2026-02-26 | Fix extract retry loop and staging delete in controller | already integrated |
| 247 | `d7fb5e9a6d1f779c66d7ca25d6b0c1540c275ff3` | 2026-02-26 | Fix late-binding closure bug in DELETE_LOCAL post_callback | already integrated |
| 248 | `ad7f19fae518bb463d4ec42c155cd934644d3f87` | 2026-02-26 | Merge pull request #82 from nitrobass24/fix/extract-retry-loop-and-staging-delete | covered elsewhere |
| 249 | `7a0b8952d6e0fc6868541ce612e6358af93ebd94` | 2026-02-27 | Fix chunk parser crash on rangeless format and zombie on controller death | adapted locally across `f7d52a4e537013fd52e8dfaf70f5885a954cddaa` and `22908425df8bd2cf586f39990b56bdc581dd2e2a` |
| 250 | `a9598f06ed1f9ed607b0b00556a7943704dcf403` | 2026-02-27 | Fix persist authority overriding children check for directories (#83) | adapted locally in `1df4fa8601a08dd2a0a960cde19b2ad4dd3efae1` |
| 251 | `da312d2187542f0d742e972e47e920ea43ab64ff` | 2026-02-27 | Add test for rangeless chunk line with no trailing data line | adapted locally in `f7d52a4e537013fd52e8dfaf70f5885a954cddaa` |
| 252 | `99638a213a30478b9574c594cf483232d553b55c` | 2026-02-27 | Merge pull request #85 from nitrobass24/fix/chunk-parser-and-zombie-crash | covered elsewhere |
| 253 | `a93b83f4cb1fef4703c0fd849048d4e79f415249` | 2026-02-27 | Replace unrar-free with full unrar for RAR5 support | adapted locally in `aa46ccd3814de04198e41ecdabf1d1d1d6354161` |
| 254 | `16e5f890488c195e61ff83b76c243b3455316101` | 2026-02-27 | Merge pull request #87 from nitrobass24/fix/extract-retry-loop | covered elsewhere |
| 255 | `5be88fd554abdc16da6bb2589901b99eb1e09601` | 2026-02-28 | Release v0.12.4 — Fix RAR5 extraction, incomplete directory re-download, parser crash | covered elsewhere |
| 256 | `dcbaf6234f1d12226299877de5f8c832cc1c1490` | 2026-02-28 | Report ExtractDispatchError as extraction failure | adapted locally in `ae841fb1df6c88a8d8fd60641abcca5681588b6f` |
| 257 | `f9eaecefe803a668c78f68a9898b82d0478ad74f` | 2026-02-28 | Add manual extract fix to CHANGELOG | intentionally skipped |
| 258 | `569959317b6ea8c43075e576e9319720ef973215` | 2026-02-28 | Fix extract not finding archives after staging move | adapted locally in `ae841fb1df6c88a8d8fd60641abcca5681588b6f` |
| 259 | `d880ba69603d053a05ac66cc5a1e849fdd4116d1` | 2026-02-28 | Add staging extract fix to CHANGELOG | intentionally skipped |
| 260 | `59039a55fb44c87400710d24dbf5b9e8bc465359` | 2026-02-28 | Pin unrar version and fix CHANGELOG markdown lint | adapted locally in `aa46ccd3814de04198e41ecdabf1d1d1d6354161` without upstream's deb12-specific pin or changelog edit |
| 261 | `91fce4e75bd62b74179f1ed2dab591fc65d52304` | 2026-02-21 | Bump ajv in /website | intentionally skipped |
| 262 | `945741b59fba2ce5735dec4ba94dde6c126e67d6` | 2026-02-24 | Merge pull request #75 from nitrobass24/dependabot/npm_and_yarn/website/multi-73726a8ab8 | covered elsewhere |
| 263 | `862fd5c5b84c90de3944ca0f8b47303a6151abce` | 2026-02-28 | Merge master into develop to sync dependabot updates | covered elsewhere |
| 264 | `e606495a6280d6b69036c0c38f8112a1cf078bb4` | 2026-02-28 | Merge pull request #88 from nitrobass24/develop | covered elsewhere |
| 265 | `047f7257e9b4011e13a12594ec95d1f8301992e0` | 2026-02-28 | Bump rollup from 4.57.1 to 4.59.0 in /src/angular | covered elsewhere |
| 266 | `6d79cff59c8c06ae5bd0ec5dcce954de306ea2f3` | 2026-02-28 | Merge pull request #89 from nitrobass24/dependabot/npm_and_yarn/src/angular/rollup-4.59.0 | covered elsewhere |
| 267 | `8faca1ff6d77be57e602db1ff8ef2051ecf825c9` | 2026-02-28 | Fix Delete Remote on directory hanging with infinite spinner | adapted locally in `cedadd93` |
| 268 | `6fade95b65fcfdec6916816abb1e03eaec529572` | 2026-02-28 | Add UMASK environment variable for file permission control | adapted locally in `9d9c795a` |
| 269 | `b09c573fc86fa5b6a780193094bdf1d68dd8fd8a` | 2026-02-28 | Merge pull request #92 from nitrobass24/fix/delete-remote-directory-hang | covered elsewhere |
| 270 | `27c0b4baee364da59371df18b85d51fd6a8302a0` | 2026-02-28 | Bump @angular/core from 21.1.3 to 21.1.6 in /src/angular | covered elsewhere |
| 271 | `cee4775c43878a12db8a96371d959b93f0765dc3` | 2026-02-28 | Bump all Angular packages to 21.2.0 | covered elsewhere |
| 272 | `aef64c9b81963399065045899cdbf36061df38cf` | 2026-02-28 | Merge pull request #90 from nitrobass24/dependabot/npm_and_yarn/src/angular/angular/core-21.1.6 | covered elsewhere |
| 273 | `848894327331dac57c9651a02abc0d5a6acefcbe` | 2026-02-28 | Parallelize CI builds and add Angular unit tests | needs area reopen |
| 274 | `b5be932551a64719b2b5520f90bcb2ade47c8e8f` | 2026-02-28 | Merge pull request #93 from nitrobass24/ci/parallel-builds-unit-tests | covered elsewhere |
| 275 | `2126a1275056974bdf6bde7e5d5ca91b5506363e` | 2026-02-28 | Add UMASK environment variable to README | needs new integration task |
| 276 | `a976b9e52d227b6a601f9ca8f98e683eae11d5c8` | 2026-02-28 | Merge pull request #94 from nitrobass24/docs/add-umask-to-readme | covered elsewhere |
| 277 | `7edb32561b1b192ba20e2b606d68e1ceaf8ab35e` | 2026-02-28 | Update website dependencies to address transitive vulnerabilities | intentionally skipped |
| 278 | `bdfb0ba4e8460fb529ccdd09b056ada10b5384f3` | 2026-02-28 | Merge pull request #95 from nitrobass24/fix/dependabot-security-alerts | covered elsewhere |
| 279 | `94af5b7095e89961b7d4ff4162c454f74f37d6c9` | 2026-02-28 | Mask SSH password in debug log output | adapted locally in `a9d02db2feaf03923207fc394356eef126c3c6e0` |
| 280 | `c9538d4f80b30f25cf885fceb9d84f4af80318fc` | 2026-02-28 | Merge pull request #98 from nitrobass24/fix/mask-password-in-debug-logs | covered elsewhere |
| 281 | `2d9ed7ecb2053c20b73a51a5569b7837a0573738` | 2026-02-28 | Release v0.12.5 — update CHANGELOG and bump version | intentionally skipped |
| 282 | `94bc62df5aa58d57847c1b95b714373e8394d160` | 2026-02-28 | Skip Angular build on arm64 via build-contexts override | needs area reopen |
| 283 | `e5b41f22a59fab4695bc21a1364f677b4744a081` | 2026-02-28 | Fix CHANGELOG heading spacing and align Angular version declarations | intentionally skipped |
| 284 | `4cb1ea7787a8be7dead50b144760b0391f4fb111` | 2026-02-28 | Release v0.12.5 — update CHANGELOG with CI optimization | intentionally skipped |
| 285 | `19efc550f406a2a7337491ed0d78b3d6ddd44f0e` | 2026-02-28 | Slim Docker image: remove ~25 MB of unnecessary packages (#100) | needs new integration task |
| 286 | `e7467fb52d385a2c0e81cc94c264bb78a4dcb244` | 2026-02-28 | Performance optimizations: Docker, CI, and runtime (#101) | needs area reopen |
| 287 | `533cce1b0cc1d0a2d26f7488f19299f2bb9cf5ad` | 2026-02-28 | Performance optimizations and Unraid install docs (#102) | needs new integration task |
| 288 | `5f37c1c13eaf99ff4eb1217d24b318af58cef9b8` | 2026-02-28 | Merge pull request #99 from nitrobass24/develop | covered elsewhere |
| 289 | `68800b4356cb3e50699a9fc35e946418672e4e32` | 2026-02-28 | Merge branch 'master' into develop | covered elsewhere |
| 290 | `33fec0d7700ae0cc6b2996401efcc99532b477cb` | 2026-02-28 | Release v0.12.6 — Unraid docs, Docker build optimizations | covered elsewhere |
| 291 | `1360d7376186daf6f7e5b76c9e69a5ec0fe731e2` | 2026-02-28 | Merge pull request #103 from nitrobass24/develop | covered elsewhere |
| 292 | `71b696af249116550a385af3c945435849c213ca` | 2026-03-01 | Remove unused wrangler dependency from website | intentionally skipped |
| 293 | `f865cf654b9efe8fe75b7f5dc660685730f5711e` | 2026-03-01 | Merge pull request #107 from nitrobass24/fix/remove-wrangler | covered elsewhere |
| 294 | `6b586f2ac1bb445b704af7f51e013f1714a171e8` | 2026-03-01 | Merge pull request #108 from nitrobass24/develop | covered elsewhere |
| 295 | `0b18669e887ba94b5b883d82848b2f69b07453ee` | 2026-03-02 | fix: apply UMASK env var via os.umask() at Python startup (#111) | adapted locally in `46cd02fe` |
| 296 | `e8d1cef7f9b557a03e0a545b01af267052886d6c` | 2026-03-02 | Release v0.12.7 — Fix UMASK not applied to downloaded files | covered elsewhere |
| 297 | `66ac8dec07733ff8e6e3670ea76b588b78c60a68` | 2026-03-02 | fix: create and chown /staging directory in container (#112) | adapted locally in `46cd02fe` |
| 298 | `a1d882db88660094912e40805b0c46cdaa7eea8f` | 2026-03-02 | Release v0.12.8 — Staging path fix and entrypoint permission improvements | covered elsewhere |
| 299 | `6ac0efb8ed959a1dab5b88f08d487c7205006915` | 2026-03-02 | Release v0.12.9 — UMASK diagnostic logging (#113) | covered elsewhere |
| 300 | `981edbb7a4a5cd5bfb2a6260af11c1bfe83ecc5c` | 2026-03-03 | fix: robust scanfs installation for restricted remote environments (#114) | adapted locally in `f1a407c5` |
| 301 | `722d21ce138d100f4616beddd59103bb3672923b` | 2026-03-03 | fix: disable sftp permission preservation to respect local umask (#115) | adapted locally in `46cd02fe` |
| 302 | `1fa6785aadfc231eac68a9c8ff6927237853ffe2` | 2026-03-04 | Security hardening bundle for v0.12.10 (#130) | adapted locally in `3e2485e5` |
| 303 | `530cb3f3e616cfb573b4274343499a6e14eeedb2` | 2026-03-04 | fix: make Angular build output CSP-compliant (#134) | adapted locally in `c1a3a77c` |
| 304 | `828214daa3a3eb3e120af1ce47359335ca3f833c` | 2026-03-04 | fix: eagerly initialize ConfigService to fix settings not loading (#136) | adapted locally in `518422ee` |
| 305 | `3dbe1b6f9dfad5ee21fa12ae682f3525168267a2` | 2026-03-04 | Prepare v0.12.10 release — security hardening and CSP fixes | covered elsewhere |
| 306 | `f15e7c4f409114710f4ef1d540c892cf99003471` | 2026-03-04 | fix: address round 5 review findings | adapted locally in `3e2485e5` |
| 307 | `bb5be5290330119b8cef54a759222d7ca4b8eb3f` | 2026-03-04 | fix: address round 6 review findings | adapted locally in `3e2485e5` |
| 308 | `562b19a189de07827e519251416645dc960946bc` | 2026-03-04 | Release v0.12.10 — Security hardening (#137) | covered elsewhere |
| 309 | `9f8a1846779fc224c3d2aac5ec26e3245a42723d` | 2026-03-04 | feat: slim Docker image by ~8 MB (170 → 162 MB) | adapted locally in `034832ed` |
| 310 | `ec57a43cc485e4c04f0f2b163dd86ef30dd59309` | 2026-03-04 | fix: consolidate Dockerfile RUN layers and document unpinned packages | adapted locally in `034832ed` |
| 311 | `483db92aeadc29b2a9d95edd5003eb92405d83c2` | 2026-03-04 | Merge pull request #139 from nitrobass24/feat/slim-docker-image | covered elsewhere |
| 312 | `ef373fa0d646f69631476ccabb24bfa1c9a0004e` | 2026-03-04 | docs: update MODERNIZATION_PLAN.md for v0.12.10 | intentionally skipped |
| 313 | `038e2120aa7243f0ae3e85d4620744b03d6b355c` | 2026-03-05 | chore: add feat/v0.13.0 to CodeRabbit base branches | intentionally skipped |
| 314 | `52692810c694e9d39c8595e498772cdec0ea3b31` | 2026-03-05 | chore: add dependabot config targeting develop | intentionally skipped |
| 315 | `0e9780b59bcff4ce97add59c09c8ac4f8463f2b4` | 2026-03-04 | Bump hono from 4.12.3 to 4.12.5 in /src/angular | intentionally skipped |
| 316 | `6ca7e55d0e2bb0855f975071c98baa791720fa89` | 2026-03-05 | Merge pull request #131 from nitrobass24/dependabot/npm_and_yarn/src/angular/hono-4.12.5 | covered elsewhere |
| 317 | `ede82684ee14a874192493f315feadbdf566b67b` | 2026-03-04 | Bump @hono/node-server from 1.19.9 to 1.19.10 in /src/angular | intentionally skipped |
| 318 | `32c1ac13bc2d843f0d74557593b9bbdcc70c1146` | 2026-03-05 | Merge pull request #132 from nitrobass24/dependabot/npm_and_yarn/src/angular/hono/node-server-1.19.10 | covered elsewhere |
| 319 | `f88893d26b6e41ab8cfb082b0655cc5f7d2dfcb3` | 2026-03-04 | Bump immutable from 5.1.4 to 5.1.5 in /src/angular | intentionally skipped |
| 320 | `75d3486168f3c6178bff2024c43738d78d698894` | 2026-03-05 | Merge pull request #133 from nitrobass24/dependabot/npm_and_yarn/src/angular/immutable-5.1.5 | covered elsewhere |
| 321 | `db3d84f405f39f63e9124caa98c277c62f643b9f` | 2026-03-05 | Bump svgo from 3.3.2 to 3.3.3 in /website | intentionally skipped |
| 322 | `c953ded2d1b6a8cd3101dade70ad0ad416a18d74` | 2026-03-05 | Merge pull request #135 from nitrobass24/dependabot/npm_and_yarn/website/svgo-3.3.3 | covered elsewhere |
| 323 | `171bc77b82fb27902f3b55abb2a03f1a6ac0608a` | 2026-03-05 | Bump tar from 7.5.9 to 7.5.10 in /src/angular | intentionally skipped |
| 324 | `5e1f1df37ae8f152554de73428f8424518d2dc4e` | 2026-03-05 | Merge pull request #138 from nitrobass24/dependabot/npm_and_yarn/src/angular/tar-7.5.10 | covered elsewhere |
| 325 | `d414a6d6093f91d1d67333ed2aa00a202a77624c` | 2026-03-05 | Revert "Bump tar from 7.5.9 to 7.5.10 in /src/angular" | intentionally skipped |
| 326 | `7accdea1007741f62f3197ee5392ba93b0b6aecb` | 2026-03-05 | Merge pull request #150 from nitrobass24/revert-138-dependabot/npm_and_yarn/src/angular/tar-7.5.10 | covered elsewhere |
| 327 | `5492b3aa97b8fe70abacb400993880917258cf54` | 2026-03-04 | feat: add structured JSON logging option (#127) | adapted locally in `f3f5c790` |
| 328 | `e7144fae4b4b125b1a3c7da7075236dc0badecb4` | 2026-03-04 | feat: add multi-select and bulk operations to file list (#123) | covered elsewhere |
| 329 | `5cb2a7a9793908186f73236a870ecb35d8e43671` | 2026-03-04 | feat: replace pickle serialization with JSON in scanfs (#129) | already integrated |
| 330 | `fd3dc0a2eaa8fd7ba55f611269d882e84e158730` | 2026-03-04 | fix: add isChecked to test helper for ViewFile spec | covered elsewhere |
| 331 | `74f9c08c55ae84e009b462e26176e50f552b9abf` | 2026-03-04 | feat: add webhook notifications on file events (#128) | needs area reopen |
| 332 | `bb5f6023a2c3df9b719f57e6036bdac8dd8d27c9` | 2026-03-04 | feat: add historical log query endpoint with search/filter UI (#124) | needs new integration task |
| 333 | `87d11b036ce8412b8840224c7ad00fbb30f30801` | 2026-03-05 | ci: enable build and publish for feat/v0.13.0 branch | needs area reopen |
| 334 | `be3ff0b60fa317ba2daea2e2ccbc3abf7902da2f` | 2026-03-05 | Merge branch 'develop' into feat/v0.13.0 | covered elsewhere |
| 335 | `6ba47dd35688925bf09fc21a81af2e137cee5e9c` | 2026-03-05 | ci: retrigger build | covered elsewhere |
| 336 | `2f7675353545a633c2bbc62ccfec0811de4f395a` | 2026-03-04 | feat: replace paste WSGI server with Bottle built-in (#140) | pending |
| 337 | `139d97cc472a8fcbfed73d5337ada1857030dee5` | 2026-03-04 | feat: add multiple path pairs support (#122) | pending |
| 338 | `85f289492e20d9c29c475675791e856152f34f20` | 2026-03-05 | fix: address code review findings for path pairs PR | pending |
| 339 | `61db57e7398215b2d469246e4279b50b2e4fef80` | 2026-03-05 | fix: address round 2 code review findings for path pairs PR | pending |
| 340 | `06d1e0f76b57af7798991ec67f2760a5286ab8a1` | 2026-03-05 | fix: address round 3 code review findings for path pairs PR | pending |
| 341 | `7aaceb68cc359969ef6a4a4d256e7cdc062eb326` | 2026-03-05 | Merge pull request #149 from nitrobass24/feat/multiple-path-pairs | pending |
| 342 | `41b2893ec56e572fd2dcee1ad2d84789a2cc645c` | 2026-03-05 | feat: make Python scanfs the primary scanner, drop PyInstaller (#80) (#148) | pending |
| 343 | `08118e118104d903d778bc42e3c0686c068792a6` | 2026-03-05 | fix: remove stale paste reference and dead modal localization strings | pending |
| 344 | `e0b1839a8f2ae7fcf2b219e420cb7c0bb1dfebf0` | 2026-03-05 | fix: make scan_fs.py self-contained for remote execution | pending |
| 345 | `27cf0e259a5cdb7998ca76c55ea1d2c32b09623e` | 2026-03-05 | feat: replace patool with direct subprocess calls (#141) (#145) | pending |
| 346 | `c8826cbebe81294b34b1d4aaedd0131de1f6663e` | 2026-03-05 | feat: add exclude patterns config for filtering remote files (#26) (#146) | pending |
| 347 | `059c6c79dfd468841bdcda54af7635cb00480b78` | 2026-03-05 | test: add composite key test coverage for ViewFileService (#159) | pending |
| 348 | `66ac4a0ec1462cbe3fb7c63cd713b4f3df019663` | 2026-03-05 | feat: Settings UI for path pairs CRUD (#160) | pending |
| 349 | `f33f1103e92e734225c13796c33272ff3958a25e` | 2026-03-05 | feat: per-pair LFTP and scanner instances in Controller (#155, #151) (#161) | pending |
| 350 | `c1bfecbfc255305a500a679cbc0a591e7ead3fc5` | 2026-03-05 | fix: improve Path Pairs settings UX (#162) | pending |
| 351 | `eea09145ae73864e18b6adf6a11f8774f0445ff8` | 2026-03-05 | fix: disable Server/Local Directory fields when path pairs active (#163) | pending |
| 352 | `faba6c1168cb5cd8f3fa97056a44ac3d19ff865a` | 2026-03-06 | feat: Alpine Docker image + dual-image CI (#164) | pending |
| 353 | `4580795021a17113fd6598fae9f6ed971e3cbb1e` | 2026-03-06 | fix: clean up startup logs — add path pairs, remove GUID (#165) | pending |
| 354 | `dde649dcc0f6aceb38e4a5be5a852e40f977494c` | 2026-03-06 | Release v0.13.0 — Multiple path pairs, Alpine image, and more | pending |
| 355 | `1385b05924d370d5cd91bd4c9af74610bafbd9da` | 2026-03-06 | docs: update README, website, and modernization plan for v0.13.0 | pending |
| 356 | `46a178fcaa992abf428925352acbcc37b9ea4d1e` | 2026-03-06 | fix: address code review findings across frontend and backend | pending |
| 357 | `9ff156dfa36c908802ec3c5339c40ea796eabb7c` | 2026-03-06 | fix: staging extraction bug + round 2 code review fixes | pending |
| 358 | `95813b589738f1d96415717de837da441e41a9b7` | 2026-03-06 | docs: document v0.13.0 known limitations in CHANGELOG and MODERNIZATION_PLAN | pending |
| 359 | `038de391a18fb64db1423445d74abb23ad1339d2` | 2026-03-06 | docs: remove stale LFTP parsing limitation (fixed in PR #66) | pending |
| 360 | `de0d58068189afc13a9e088bfd84dc043b23c1b5` | 2026-03-06 | Merge feat/v0.13.0 into develop — Multi-pair architecture & infrastructure | pending |
| 361 | `150c45a712e17fcb3b76c6ec1b3ed50fa3d83091` | 2026-03-06 | fix: enforce unique path pair names (#169) | pending |
| 362 | `b58b8a66121e4f351113d148c9ba306fd4971507` | 2026-03-06 | Merge pull request #172 from nitrobass24/fix/unique-pair-names | pending |
| 363 | `c25fc2c02909ec028b8b482393d388a91e3a33ea` | 2026-03-06 | ci: publish develop branch images to ghcr.io on every push | pending |
| 364 | `2e37d2c17349381a31499df8b837d6ae859173cb` | 2026-03-06 | Merge pull request #175 from nitrobass24/ci/publish-develop | pending |
| 365 | `9a76b9bf60e723286cd9f8feb31e6177a3f64164` | 2026-03-06 | fix: per-pair extraction paths and staging subdirectories (#167, #168) | pending |
| 366 | `fc92bb16985915f53a54e092c8891994ce99ca5b` | 2026-03-06 | fix: propagate pair_id through extraction pipeline and guard None staging path | pending |
| 367 | `d1e97e4a6f8de5b870f143f6bfda1818900144dc` | 2026-03-06 | fix: harden extract pipeline — pair_id on status, safer fallback, test updates | pending |
| 368 | `2805bc07fa7e9e2487bd048d6a8f98a5b3b3fcda` | 2026-03-06 | fix: filter active_extracting_file_names by pair_id | pending |
| 369 | `47d812b740c73c0426a1b03637126d8c6d84e886` | 2026-03-06 | Merge pull request #173 from nitrobass24/fix/per-pair-extraction | pending |
| 370 | `7f9b0f71e198f2ab0249aacc0431109942945ad3` | 2026-03-06 | fix: show warning banner when all path pairs are disabled (#170) | pending |
| 371 | `ed032da149b6b8f391d6f3d5478748d61712dba2` | 2026-03-06 | fix: suppress remote-scan banners when all pairs disabled and add noEnabledPairs test | pending |
| 372 | `d067ffa4a1a2b65fb77687133af9f02b99cfb7ef` | 2026-03-06 | Merge pull request #174 from nitrobass24/fix/graceful-pause-no-pairs | pending |
| 373 | `aa29fe586b94b76242292e25ce56ee95c1ef1bf5` | 2026-03-06 | ci: parallel arm64 builds on develop, unified publish job | pending |
| 374 | `926c64f230818953057bca8706eda64568924503` | 2026-03-06 | Merge pull request #176 from nitrobass24/ci/parallel-arm-develop | pending |
| 375 | `58c1ae381e179ad0314133c2fafe9397c3cda55f` | 2026-03-06 | fix: skip spurious staging moves on container restart (#177) | pending |
| 376 | `8e0f2843f3983d7a76aab712b6cc2a3f2da8a3e6` | 2026-03-06 | Merge pull request #179 from nitrobass24/fix/spurious-staging-moves | pending |
| 377 | `9d34fe76e67c0d54cd56803c460f8f7706a3f4be` | 2026-03-06 | feat: consolidate all extraction to 7z, remove unrar dependency | pending |
| 378 | `06b9ac31b92f9701dac0791e4794e9781b06a961` | 2026-03-06 | fix: add -- arg terminator to 7z calls and use same-volume temp dir | pending |
| 379 | `571acb788c5450181b5fe0b6d8fc3e18e63a7ed8` | 2026-03-06 | Merge pull request #178 from nitrobass24/feat/consolidate-7z | pending |
| 380 | `83390e72001b370b283357a2d200cd92b2a1321c` | 2026-03-06 | fix: healthcheck uses 127.0.0.1 instead of localhost | pending |
| 381 | `3a37dd855de455cd41c28b313f28ffa5cde59c69` | 2026-03-06 | fix: healthcheck respects custom web port via WEB_PORT env var | pending |
| 382 | `a9abd653b9de885b219fd405081bb035eb494bf3` | 2026-03-06 | Merge pull request #180 from nitrobass24/fix/healthcheck-ipv6 | pending |
| 383 | `aa202b5e33f89d10a809bd820833fa73ae9860ff` | 2026-03-06 | ci: group @angular/* dependabot updates into single PR | pending |
| 384 | `18c24c33187e0c1b10e113dc2cbd743bd6bd024b` | 2026-03-06 | Merge pull request #196 from nitrobass24/ci/dependabot-angular-group | pending |
| 385 | `62b0ef1e50af26c9e786db29d7b5d9d08764b397` | 2026-03-06 | docs: update CHANGELOG and MODERNIZATION_PLAN for v0.13.0 release | pending |
| 386 | `d6cc9956fd355973d5e7591665ac60fad1e7d739` | 2026-03-06 | docs: remove resolved known limitations from MODERNIZATION_PLAN | pending |
| 387 | `9ec504b304b934abac2138561dcd66fd4d0ef730` | 2026-03-06 | fix: address code review findings for v0.13.0 release | pending |
| 388 | `5aa82579de6c6238c3bf35cec52a474d9f751c13` | 2026-03-06 | fix: extract validation helper in path_pairs handler, catch AttributeError in scanner | pending |
| 389 | `109db3905b1d22a221b067ebd89391255f5e5cbd` | 2026-03-05 | Bump hono from 4.12.3 to 4.12.5 in /src/angular | pending |
| 390 | `4d99527feddaa5f992ee89bfcf76db8403aecf65` | 2026-03-06 | Merge pull request #153 from nitrobass24/dependabot/npm_and_yarn/src/angular/hono-4.12.5 | pending |
| 391 | `fd8f198c41d15b3fd032a2cd18238d9411cfec2e` | 2026-03-06 | Merge remote-tracking branch 'origin/master' into release/v0.13.0 | pending |
| 392 | `8b643396e89400f80cf8da68a1dc92371e6cb210` | 2026-03-06 | Release v0.13.0 — Multi-pair architecture & infrastructure | pending |
| 393 | `ee158a5216436b27aaedb9f0eb12f8a65a88ad77` | 2026-03-06 | Merge remote-tracking branch 'origin/master' into temp-merge-develop | pending |
| 394 | `d17451e5e86c59e87b0ccbdaae45d44b2b99ff25` | 2026-03-07 | chore(deps): bump docker/setup-qemu-action from 3 to 4 | pending |
| 395 | `5e1e0fe594cbd359abed5257003e944807fc53bf` | 2026-03-06 | Merge pull request #182 from nitrobass24/dependabot/github_actions/develop/docker/setup-qemu-action-4 | pending |
| 396 | `5fe59c3aa871d80acaa3ee1a71c93e2d5ec047d7` | 2026-03-07 | chore(deps): bump docker/metadata-action from 5 to 6 | pending |
| 397 | `552ef80cdea5d2e46992eae490f8c3082796796f` | 2026-03-06 | Merge pull request #183 from nitrobass24/dependabot/github_actions/develop/docker/metadata-action-6 | pending |
| 398 | `b51e10081335cd2d99022a0f635f1361ce4c2fcd` | 2026-03-07 | chore(deps): bump docker/setup-buildx-action from 3 to 4 | pending |
| 399 | `e12e48d4b6d1fe91ca5d1a7afae21a0490ab5a7f` | 2026-03-06 | Merge pull request #184 from nitrobass24/dependabot/github_actions/develop/docker/setup-buildx-action-4 | pending |
| 400 | `23bf3a218f0cc69b3fd896c4ebb64cfa491c0a7b` | 2026-03-07 | chore(deps): bump docker/login-action from 3 to 4 | pending |
| 401 | `8080ed051f87f34d6fdebd918c7dac5670a0ba0a` | 2026-03-06 | Merge pull request #185 from nitrobass24/dependabot/github_actions/develop/docker/login-action-4 | pending |
| 402 | `88973efb663d35a1f01fbc63912832ff9453fdfe` | 2026-03-07 | chore(deps): bump actions/checkout from 4 to 6 | pending |
| 403 | `25a141a9f6e78f33c6ec382b23497462f2d9e7ef` | 2026-03-06 | Merge pull request #186 from nitrobass24/dependabot/github_actions/develop/actions/checkout-6 | pending |
| 404 | `f0618b74b40ea50bdac2d3c20cde5b301eb8911e` | 2026-03-07 | fix: resolve CodeQL code scanning alerts | pending |
| 405 | `745a1160f4ccfd92770835b4a7916877ac3de07a` | 2026-03-07 | Merge pull request #197 from nitrobass24/fix/code-scanning-alerts | pending |
| 406 | `a04cf90cb886bd0c53cfb40efa5aded45f4964cd` | 2026-03-09 | Remove redundant pre-extraction archive verification (#204) | pending |
| 407 | `67b7f6941db907adcadc18e0d8b4a6361127aaa3` | 2026-03-09 | Remove extraction retry/re-download logic | pending |
| 408 | `f16976cbc11353a3b27ab329496ee903b6459aa5` | 2026-03-09 | Merge pull request #207 from nitrobass24/fix/204-extract-verify-false-negative | pending |
| 409 | `8860245dc0a3bd7cef661e82253b4a9b4cb8a981` | 2026-03-09 | Fix auto-queue commands missing pair_id for path pairs (#205) | pending |
| 410 | `61e0aeb0429b5c4a89571e7342a94fa35495635b` | 2026-03-09 | Fix type annotations for Optional pair_id in auto_queue | pending |
| 411 | `a0bd1af7501135cf7ae3d1713b7ebe539c4b7096` | 2026-03-09 | Merge pull request #206 from nitrobass24/fix/205-auto-delete-remote-path-pairs | pending |
| 412 | `7f68a4c2f5d3eb7521b4d23da2e8d137e0521ac9` | 2026-03-09 | chore(deps): bump actions/setup-node from 4 to 6 (#199) | pending |
| 413 | `67b8ac8d0ac408029519694aa0310f2ebc61ea95` | 2026-03-09 | chore(deps): bump docker/build-push-action from 5 to 7 (#200) | pending |
| 414 | `701750b83d3818c3f4d71e08849d61a35bf94002` | 2026-03-09 | chore(deps): bump actions/upload-pages-artifact from 3 to 4 (#202) | pending |
| 415 | `399faf2a8867675adb17d33d6028c24b61a95c8e` | 2026-03-09 | chore(deps): bump actions/upload-artifact from 4 to 7 (#201) | pending |
| 416 | `a6bd8fbdb2332a595ce9c50e3e224a863b950c8c` | 2026-03-09 | chore(deps): bump actions/download-artifact from 4 to 8 (#198) | pending |
| 417 | `32f43c241ee5f08a1613014b6692b9150260fa8e` | 2026-03-09 | Fix pending_completion never clearing for EXTRACTED/EXTRACT_FAILED with staging (#208) | pending |
| 418 | `9c8eb11c2681502bacbb1bcf886d4561cde207f3` | 2026-03-09 | Use official 7-Zip binary with RAR codec support (#210) | pending |
| 419 | `d18356f207bf7a465d77932faf2a6ed46ccdc9f4` | 2026-03-09 | Use pre-built 7zip image instead of compiling from source (#212) | pending |
| 420 | `90c31cfb780edf0585317a91fe5c3a17dee04fd1` | 2026-03-09 | Release v0.13.1 - RAR extraction fix and pipeline stability | pending |
| 421 | `ea924d8ebdae2628459853ac99bcdbb099750b62` | 2026-03-09 | Add recommended hard link setup to FAQ and README (#215) | pending |
| 422 | `699eefbbfabbfb697be3260c60af21a77f65fbf3` | 2026-03-10 | Release v0.13.2 - Code quality, test coverage, and bug fixes (#229) | pending |
| 423 | `bdb09d8bb2e03f6cbd60d124f429cde602bb9d22` | 2026-03-10 | Tighten .dockerignore to exclude more build context junk | pending |
| 424 | `e60972db789194cbe1da9e4dbd17c6e1d2c3203d` | 2026-03-10 | Migrate multiprocessing from fork to spawn (#232) | pending |
| 425 | `794c5683cdb24d6b020f63148f19b9b8c1c73d82` | 2026-03-10 | Add pair label column to file list (#156) (#233) | pending |
| 426 | `56b4fbaeb1db87b511850cdeaa09f08300e411c8` | 2026-03-10 | Fix file descriptor leak on restart causing OSError: No file descriptors available (#234) | pending |
| 427 | `e4ea4019afad457cf74f40f2e139128d82c6eca1` | 2026-03-10 | Fix duplicate entries when pairs share a local directory (#235) | pending |
| 428 | `d8cb5761cb2d16af3c4cf6a26a6b4b5215145f50` | 2026-03-11 | Add post-download integrity checking (#125) (#236) | pending |
| 429 | `e919d10cdf8ed8a875062ba87670b4a58f2af821` | 2026-03-11 | Fix exclude patterns with trailing slash (#239) (#240) | pending |
| 430 | `294805163db9b0a83d466eed2bf729d3454eb2a1` | 2026-03-11 | Add SSE heartbeat to prevent stale connections (#243) | pending |
| 431 | `81cc8333cd5c3b2d86258ed7c015eb78ec58dd44` | 2026-03-12 | Add lftp xfer:verify toggle for inline transfer verification (#242) (#247) | pending |
| 432 | `5f98531701e50a114112b6af09c3bea20710f9f6` | 2026-03-10 | chore(deps-dev): bump hono from 4.12.5 to 4.12.7 in /src/angular (#237) | pending |
| 433 | `657d1eb226fe3e8ecea3c3852c90cb7d02f3b436` | 2026-03-10 | chore(deps-dev): bump tar from 7.5.9 to 7.5.11 in /src/angular (#238) | pending |
| 434 | `b29e4020518c5a88ebe4286f73b70e465f3461f8` | 2026-03-12 | Fix parser crash on unrecognized lftp status lines (#253) (#258) | pending |
| 435 | `4bf82a958fb84a88eb08313b1c4ab9a64198bb25` | 2026-03-12 | Release v0.13.3 - Fix lftp parser crash on orphan progress lines | pending |
| 436 | `70ff50752440aa73479d9a0450bebfdce5269275` | 2026-03-12 | Merge branch 'master' into develop | pending |
| 437 | `1b4538a10c2540cb875a57ef0bd210757182b7a6` | 2026-03-12 | Add CI status, release, Angular, Python, and platform badges to README | pending |
| 438 | `87f6296d54f494643d825ea04e8b60e3cfcd9ebb` | 2026-03-12 | Move debug toggle to Logging section and convert log format to dropdown (#248) | pending |
| 439 | `b95555745a6636cbc30c08334e88164674ca4f50` | 2026-03-12 | Convert file action buttons to native button elements (#241) (#246) | pending |
| 440 | `f2b7c6506ce866587fb7547ea862d524900cd9ef` | 2026-03-12 | Add Phase 1 high-priority service tests (#225) (#245) | pending |
| 441 | `a0e0247876cc544093bcc342a91fc9075c259039` | 2026-03-12 | Remove Debian Docker variant, consolidate to Alpine-only (#231) (#244) | pending |
| 442 | `c03ace3c27328ee4d6bb51a35962342569bc4abe` | 2026-03-12 | Fix CI: add missing xfer_verify to test config helper (#262) | pending |
| 443 | `c902aa1be2ed78c9852e2df564d5b0f12d7063a2` | 2026-03-12 | CI: use pre-built Angular artifact for amd64 publish build | pending |
| 444 | `394482fb7feead11022b4ae0162c4bb9df4f8fae` | 2026-03-12 | Add info-level logging for LFTP queue commands | pending |
| 445 | `3452db2ad55c8672a973579458fdcff92bb86c1a` | 2026-03-12 | Fix pending_completion stuck for stopped downloads deleted locally | pending |
| 446 | `28ee84796ae4e902b75b9b139f361d8d2bca1419` | 2026-03-12 | Merge pull request #272 from nitrobass24/fix/pending-completion-stuck | pending |
| 447 | `9152a322fae65b073c2e4542bce245e6ce3530e9` | 2026-03-12 | Expose verbose logging setting in web UI (#266) | pending |
| 448 | `62a3c3e0f4daf35bbfbc501c6a7721a8735a0eb8` | 2026-03-12 | Merge pull request #270 from nitrobass24/fix/general-settings-ui | pending |
| 449 | `b6553c1b05b95f045b76cff2bba3e8d27fb94670` | 2026-03-12 | Fix file descriptor leak on restart (#265) | pending |
| 450 | `d129e2f1a6ce621497359772a218150c76c09189` | 2026-03-12 | Guard terminate() against None after close_queues() clears _terminate | pending |
| 451 | `9c41fcd581d901c36d7554cd8b3a44becf40497c` | 2026-03-12 | Merge pull request #268 from nitrobass24/fix/fd-leak-restart | pending |
| 452 | `3eaf5c00d51501663cc9b53e62e665014ffe1a6e` | 2026-03-12 | Fix model builder cache not invalidating when active files clear | pending |
| 453 | `2f22a004ff6389a853ec41e81b40beaa028448e6` | 2026-03-12 | Merge pull request #273 from nitrobass24/fix/active-files-cache-invalidation | pending |
| 454 | `01dd01c992544068a0dcf395f10687456051e616` | 2026-03-12 | Fix exclude patterns not passed to LFTP mirror command (#259) | pending |
| 455 | `9f414fc0c953a6cb36ea81cf2b193996f5535d2e` | 2026-03-12 | Deduplicate pattern parsing in filter_excluded_files | pending |
| 456 | `12ca4c5cbcced91eefa4920733e91885ac9eacb6` | 2026-03-12 | Merge pull request #261 from nitrobass24/fix/exclude-patterns-lftp | pending |
| 457 | `bd4a50aa6d5cd905b64346a79b0c8d08ad8b7b97` | 2026-03-12 | Remove Debian Docker variant, consolidate to Alpine-only (#231) (#244) | pending |
| 458 | `0b799c7f868468684928614571a3802f47f67de1` | 2026-03-12 | Fix CI: add missing xfer_verify to test config helper (#262) | pending |
| 459 | `f50f8d900aa46e7826102a37d1dc2e7f09d78b15` | 2026-03-12 | CI: use pre-built Angular artifact for amd64 publish build | pending |
| 460 | `14323fd7883ce21ad874a93aa07f208c054a0c0e` | 2026-03-12 | Fix parser crash on Unraid PTY line-wrap fragments (#260) | pending |
| 461 | `26193be812528080a7e756fbd801c525f48e2fe7` | 2026-03-12 | Set COLUMNS env var in pexpect spawn to prevent Unraid PTY wrapping | pending |
| 462 | `6b79bde4a81549eff8c1c79c9c9284c6aa054957` | 2026-03-12 | Fix off-by-one in consecutive status error threshold | pending |
| 463 | `96e79b85e93c1c7e1bf73b13c2ca5741d2be96f8` | 2026-03-12 | Merge pull request #263 from nitrobass24/fix/parser-resilience-unraid | pending |
| 464 | `f6e6663bbedc9486b36ed6217b8bdcdd4a43cb45` | 2026-03-12 | Release v0.13.4 - Exclude patterns fix and Unraid parser resilience | pending |
| 465 | `b13d7d5a1bba9a904b2f7266614764353abbd91f` | 2026-03-12 | Fix v0.13.4 changelog to only include hotfix PRs | pending |
| 466 | `21ca414843b8da2860917e318bcf4773f3ef4846` | 2026-03-12 | Add debug log for LFTP queue command | pending |
| 467 | `69c50edef63bfffa90d9107f7865252271720fbc` | 2026-03-12 | Fix exclude patterns: use --exclude-glob instead of --exclude | pending |
| 468 | `8cacc58ba5e31422f11b9305ea5c6bb1af3a71cf` | 2026-03-12 | Allow workflow_dispatch to publish Docker images | pending |
| 469 | `1fd8efbabaf949a07a46fa84e22279c983a694f6` | 2026-03-12 | Merge remote-tracking branch 'origin/master' into develop | pending |
| 470 | `a24d0431f4d7561d9336a24f89acad3ca4209df3` | 2026-03-12 | Silence redundant LftpModel log noise from temporary model (#267) | pending |
| 471 | `6977fffe38993d8d455466483b53401830f3c458` | 2026-03-12 | Disable propagation on dummy logger to prevent log leakage | pending |
| 472 | `fd0957621307f53d45277751abe1bcc6aa405dc2` | 2026-03-12 | Merge pull request #269 from nitrobass24/fix/model-log-noise | pending |
| 473 | `c7470447bf1dcbfd68f082ca0e75834b05c2fc52` | 2026-03-12 | Deduplicate amd64 Docker build in CI | pending |
| 474 | `62360ad7c8e8e41d6afc76598af01201e3902768` | 2026-03-12 | Merge pull request #274 from nitrobass24/ci/deduplicate-amd64-build | pending |
| 475 | `f3de4cdd2005a45f4857277a6d0c3e91a8e2da93` | 2026-03-12 | Fix CI: inline SHOULD_PUBLISH expression in job-level if | pending |
| 476 | `c4ca0ac30b7e2afddc47b1c5373ca2634e2ed00b` | 2026-03-12 | Prepare v0.14.0 release | pending |
| 477 | `cceea49d6b0670dfab0a072655f3d21b4f78559e` | 2026-03-12 | Release v0.13.5 - Exclude-glob fix and queue logging | pending |
| 478 | `bdc3d7d9e0dc14c580f915bfdd058de808dd18e8` | 2026-03-12 | Resolve merge conflicts and address PR review findings | pending |
| 479 | `7b106632f967d74b886e727f9d56ce4c8a7343f9` | 2026-03-13 | Improve integrity check settings clarity and validate button UX | pending |
| 480 | `dbfb2ae69e2dbede214437414fcf1bf6b1c229b2` | 2026-03-13 | Merge pull request #276 from nitrobass24/fix/validate-ui-clarity | pending |
| 481 | `4160d794f908aee1e158d14c45c4b859d0bdca3f` | 2026-03-14 | Update docs and changelog for v0.14.0 release | pending |
| 482 | `dc763668be0a9706ca970ebe879fd739df635e5e` | 2026-03-14 | Fix ModelFile nullable types, add validation tests, fix changelog duplicate | pending |
| 483 | `71ce3f1e30e7d4c799e7d9506a1e998ad845ae33` | 2026-03-14 | Fix xfer_verify description, add missing validation tests, remove changelog duplicate | pending |
| 484 | `6b45b21d2fb0e2a6a67ecd5c1546854d3b56ae79` | 2026-03-14 | Release v0.14.0 — Alpine-only image, integrity checking, verbose logging | pending |
| 485 | `a564c0d8ed0de2455ade504c7b8d9dbfbb493a43` | 2026-03-14 | Add missing v0.14.0 milestone features to changelog | pending |
| 486 | `d00e64c595b51f525535bb040cf1afed2de43e95` | 2026-03-15 | chore(deps): bump the angular group in /src/angular with 9 updates | pending |
| 487 | `ffe00c5a06271a3b989c8440300f7a9a07b77a01` | 2026-03-16 | Merge pull request #282 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-ae743420d6 | pending |
| 488 | `a685c848918e5b9d7ba139471b1578ec91a419cb` | 2026-03-14 | Add missing v0.14.0 milestone features to changelog | pending |
| 489 | `f03fc1f564590956929045a98cb46bb7d3930bee` | 2026-03-16 | Merge master into develop to sync v0.14.0 release | pending |
| 490 | `60bc9384ad985c4c4ed8f367f0591abc6fa4940d` | 2026-03-16 | chore(deps-dev): bump vitest from 4.0.18 to 4.1.0 in /src/angular (#284) | pending |
| 491 | `2949a72eb4a334e7c85f1421a4a48ea9741d4879` | 2026-03-16 | chore(deps-dev): bump jsdom from 27.4.0 to 29.0.0 in /src/angular (#283) | pending |
| 492 | `818f0934c6505b477843e1c6a144b01aea3e9b8d` | 2026-03-17 | Consolidate Python deps to PEP 621 pyproject.toml (#287) | pending |
| 493 | `ad3b590f3667a7c33d96d6149f3602bc2390139a` | 2026-03-17 | Add Ruff linting and formatting with CI enforcement (#288) | pending |
| 494 | `0e19eb573882241cc5946f375498755c519537b2` | 2026-03-18 | Fix parser crash on chunk line-wrap from long filenames (#290) | pending |
| 495 | `09fb7723d6160ac508493bdec41ffad333121735` | 2026-03-18 | Add Pyright type checking foundation (#249 Phase 1) (#292) | pending |
| 496 | `d761a6872ee96163e507cf94b68fb04efc0ca49c` | 2026-03-18 | Fix parser crash: skip unrecognized lines inside job context (#293) | pending |
| 497 | `cefc87fb8c4f95b01bf4e13b5139e44ba4e45c7d` | 2026-03-18 | Split Pyright into its own CI job for visibility | pending |
| 498 | `7e470883a7343bc3a75cde30d50306924932fc3b` | 2026-03-18 | Fix 91 Pyright errors across small modules (Phase 2) (#295) | pending |
| 499 | `c45ee2ac832af4a0faffc25f0e4ceed475950dc5` | 2026-03-18 | Fix false download completion on parser error (#296) | pending |
| 500 | `82343ce5f9222dd8b19153b0b9a62acfcc683304` | 2026-03-18 | Fix progress tracking for downloads with .lftp temp naming (#298) | pending |
| 501 | `0b7857b714e04cc4e0efd7fe5b9b363c2268be61` | 2026-03-18 | Prepare v0.14.1 release | pending |
| 502 | `2b3bf57fae85560cd5cc647864cb23a3ca7000d3` | 2026-03-18 | Fix scan_fs.py compatibility with Python 3.8+ remote servers | pending |
| 503 | `f96e41de21fd86db06d8babc4622db078be28249` | 2026-03-18 | Release v0.14.1 — Parser stability, progress tracking, tooling | pending |
| 504 | `484477b1d06a7b4c20e42c0cf521663260d8109a` | 2026-03-18 | Fix flaky test_scanner_process by retrying multiprocessing queue read | pending |
| 505 | `793e01ca9eae93ee730b40ed92caa26959bba635` | 2026-03-18 | Fix 86 Pyright errors in security-critical paths (#249 Phase 3) | pending |
| 506 | `be75a422fa74d040940fb75928c49ff0d1980ccb` | 2026-03-18 | Merge pull request #301 from nitrobass24/chore/pyright-phase3-security-paths | pending |
| 507 | `28e2aa73d12791cb1a8c7ed5d2bfb622e7141ca2` | 2026-03-18 | Fix final 80 Pyright errors — 0 errors in basic mode (#249 Phase 4) | pending |
| 508 | `1ba27be3c5a5ad9e2bad294bd7b58367868427a4` | 2026-03-18 | Make Pyright type check required in CI | pending |
| 509 | `2c0aff4e75316c1849928c68a7d19910753c7284` | 2026-03-18 | Merge pull request #302 from nitrobass24/chore/pyright-phase4-complex-modules | pending |
| 510 | `8bc7a98a18ec7de29c2f334cacd220b2eb731ce4` | 2026-03-18 | Add Playwright E2E test suite, remove old Protractor (#250) | pending |
| 511 | `f7d654fcb7a9235b9bc560c5b94d0c1e1cda1f95` | 2026-03-18 | Remove dead Docker E2E test infrastructure | pending |
| 512 | `c6206805c9cb12552b2e738bf77486733ba08874` | 2026-03-18 | Improve Playwright test robustness | pending |
| 513 | `8800c60de30edf17a2c3591ad09b44c439420ae4` | 2026-03-19 | Fix SSE-related test timeouts and selector issues | pending |
| 514 | `ff3a6f375b58bf92f34f94a61da7a4fafbe80753` | 2026-03-19 | Add SEEDSYNC_DISABLE_RATE_LIMIT env var for E2E testing | pending |
| 515 | `96e8b539df0009bfc06fb63a6b7637d9586e00d4` | 2026-03-19 | Fix page object selectors to match actual Angular DOM | pending |
| 516 | `60d13717f063260093161256e1be968a5626681e` | 2026-03-19 | Fix all Playwright test failures — 55 pass, 11 skip, 0 fail | pending |
| 517 | `a9ae89193ac2a1cba95ec1fa559014f04f288dbf` | 2026-03-19 | Remove test-results from git, update gitignore | pending |
| 518 | `8be2ab18bd59357e919511bd7226e6aa5143b2e8` | 2026-03-19 | Add Playwright E2E tests to CI | pending |
| 519 | `0018a5c1849dc4492ab5743501b5d4bfb2040f7f` | 2026-03-19 | Fix CI: use npm install instead of npm ci for Playwright | pending |
| 520 | `aadd8a89a268ca50d5163111b4a6776381abdefb` | 2026-03-19 | Clean up unused params, add try/finally for pair cleanup | pending |
| 521 | `e7b55773c7e69042c68e3345b5a4ed8128f5e0bf` | 2026-03-19 | Merge pull request #303 from nitrobass24/feat/playwright-e2e-tests | pending |
| 522 | `996fce92ab31d71fa5b9985cb6b4f97232df8e51` | 2026-03-22 | chore(deps): bump the angular group in /src/angular with 9 updates | pending |
| 523 | `486364fb1ff8390d516d199c60a4b3ba96392534` | 2026-03-23 | Merge pull request #307 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-d0bae5d802 | pending |
| 524 | `937918490ef7ef95bfbc78207ba5ef2f67d69e18` | 2026-03-22 | chore(deps): bump actions/setup-python from 5 to 6 | pending |
| 525 | `d6d8b719d80bc83e19140c4857c6445d1a6e94c1` | 2026-03-23 | Merge pull request #306 from nitrobass24/dependabot/github_actions/develop/actions/setup-python-6 | pending |
| 526 | `23e621fee0105789948a25e829c83d19449db8e3` | 2026-03-23 | chore(deps-dev): bump jsdom from 29.0.0 to 29.0.1 in /src/angular | pending |
| 527 | `cc2579727d0c37f54747aad05c7d3d3be3f2313d` | 2026-03-23 | Merge pull request #308 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/jsdom-29.0.1 | pending |
| 528 | `19b1c46594009ff2460695f893b0c1ffecba2b8a` | 2026-03-19 | Redact sensitive credentials from API responses (#257) | pending |
| 529 | `224c793ee85c6ac6dcf6db1e463dfe12ffff4ea1` | 2026-03-19 | Reject redacted sentinel in set handler, fix staticmethod | pending |
| 530 | `185386384e4f704e06b7c0889688a6d150320b7e` | 2026-03-23 | Merge pull request #305 from nitrobass24/fix/redact-credentials-api | pending |
| 531 | `5d3a3526bc242e76c346342cdb330c45634fbb6e` | 2026-03-19 | Reject control characters in decoded filenames (#300) | pending |
| 532 | `5bfb18cc8c1f0d090b3252ca365e77471dda06c0` | 2026-03-19 | Assert queue_command not called on rejected filenames | pending |
| 533 | `30547791ea22bebe129896e60dcabfa91720f7f5` | 2026-03-19 | Fix pre-existing test failures and add assert_not_called checks | pending |
| 534 | `7f25cd71e37cba96bf5e037ad1c934fa4fe201c4` | 2026-03-23 | Merge pull request #304 from nitrobass24/fix/reject-control-chars-filename | pending |
| 535 | `1f6c5af6d3dd6db3daf9526f1bc7b1aa98b44f15` | 2026-03-13 | Fix ModelFile type: local_size and remote_size are nullable | pending |
| 536 | `f6c0a84f68b0b3f32a38bb8e3ee986bcd41655a6` | 2026-03-23 | Prepare v0.14.2 release | pending |
| 537 | `6abce01f714238ec182db5a4d6d805c56fef2eba` | 2026-03-23 | Address PR review findings across security, tests, and code quality | pending |
| 538 | `0f94a26f0c220c74ab8e4fdb40378d93f0a8e01a` | 2026-03-23 | Fix CI failures and address second round of PR review findings | pending |
| 539 | `d1a1c54cd7cd2f2daa90524a9d9996356652ef7d` | 2026-03-23 | Harden E2E test cleanup and selector stability | pending |
| 540 | `88eea5633c1c380281f34129afd5252acbda18b1` | 2026-03-23 | Address fourth round PR review: exact name matching and safer test cleanup | pending |
| 541 | `1d900326576e235ad1d009bcb7107b6adc5c6a02` | 2026-03-23 | Harden settings E2E: select mutation in try, cleanup assertion, unique names | pending |
| 542 | `1fa5deedfa85fceae2cfed8fb38fa0eab03e1013` | 2026-03-23 | Merge pull request #309 from nitrobass24/release/v0.14.2 | pending |
| 543 | `04f0ecc5a2ebb770701ee8b1a19b956f96ae2bb6` | 2026-03-23 | Adopt uv for Python dependency management (#286) | pending |
| 544 | `25d2798d7c05e1585d3b1e5932fdc0efd579d834` | 2026-03-23 | Pin uv versions: setup-uv@v7, Docker image uv:0.11 | pending |
| 545 | `f82ea20e14bf0f9ff57ac3a0f70f9399b4664b2c` | 2026-03-23 | Merge pull request #311 from nitrobass24/feat/uv-dependency-management | pending |
| 546 | `17cc5e7eb4534531b33d688fa73ac6c77e40f1a3` | 2026-03-23 | Fix pre-existing code quality bugs from Ruff review (#289) | pending |
| 547 | `38d205254dd3d61f2bf2672c4e87bc7db5aa016b` | 2026-03-23 | Fix missed user@host format in Sshcp.copy() to use _remote_address() | pending |
| 548 | `34e67792bf917dccec314cd1f09dcf126b1eaf1f` | 2026-03-23 | Merge pull request #312 from nitrobass24/fix/ruff-real-bugs | pending |
| 549 | `85c99634b2994cf07e0e8f9d10b13b8cf4b6fad3` | 2026-03-23 | Add startup validation for required LFTP config fields (#310) | pending |
| 550 | `3c83762b9df49769be96ab694aa560750cd1d79f` | 2026-03-24 | Improve backward-compat error to list specific missing field(s) | pending |
| 551 | `bec227486367ecd2bbdd4924dc61089af423d12f` | 2026-03-24 | Use dynamic field names in backward-compat error message | pending |
| 552 | `15e036c086ebb86bfa85034545bc644ee9ace13a` | 2026-03-24 | Merge pull request #313 from nitrobass24/fix/validate-lftp-config | pending |
| 553 | `71353b54eb7861845d61a6eb1ef8db03af56efec` | 2026-03-24 | Add configurable remote Python path and Python 3.5 compat | pending |
| 554 | `629c7cb6cf7af2526af6da118097c2baf46190ab` | 2026-03-25 | Escape remote Python path and harden whitespace handling | pending |
| 555 | `56c18e7657a7eee591aaf2408751a464bb83f478` | 2026-03-25 | Fix ruff format: wrap long ternary expression | pending |
| 556 | `45bffa5fd182ce9c913751090e5ad22d2ae54ce5` | 2026-03-25 | Merge pull request #315 from nitrobass24/fix/remote-python-compat | pending |
| 557 | `e5ece7931ba76061e02f8ec18f7a5dd975b266c2` | 2026-03-25 | Prepare v0.14.3 release | pending |
| 558 | `afd660077496234f2caeaeb63702ef9772373077` | 2026-03-30 | chore(deps): bump actions/deploy-pages from 4 to 5 (#317) | pending |
| 559 | `c050bc155f58769da119540723c428b71c642433` | 2026-03-30 | chore(deps-dev): bump vitest from 4.1.0 to 4.1.2 in /src/angular (#319) | pending |
| 560 | `f0bb2401c6fd3af1806d924d4c653d5ed8173eef` | 2026-03-30 | chore(deps): bump the angular group in /src/angular with 9 updates (#318) | pending |
| 561 | `53127b28be4d0e60d2b7b289b280d762569759ba` | 2026-03-30 | Upgrade Pyright from basic to strict mode (closes #291) (#316) | pending |
| 562 | `16acf38684974fcaf4a16a25ca2ffdd65d041863` | 2026-03-30 | Fix website security vulnerabilities via npm audit fix (#324) | pending |
| 563 | `153188e29610773877c812a7b3a992c278ed9a6c` | 2026-03-30 | Bump pygments 2.19.2 → 2.20.0 to fix ReDoS vulnerability (#325) | pending |
| 564 | `b4877f330919f3778ba28359881a066df29e66fe` | 2026-03-30 | Release v0.14.4 - Pyright strict mode, security fixes | pending |
| 565 | `eedd9e7466cb1ea9d4d8560260de605657d607a0` | 2026-04-03 | Bump all dependencies to latest compatible versions (#326) (#333) | pending |
| 566 | `80720e4f3e78534b05776c4c52c081a19da07c38` | 2026-03-30 | chore(deps): bump path-to-regexp from 0.1.12 to 0.1.13 in /website (#321) | pending |
| 567 | `eb53c11047429f03c03ff21ac7974cc3993e8387` | 2026-03-30 | chore(deps): bump brace-expansion from 1.1.12 to 1.1.13 in /website (#323) | pending |
| 568 | `af892403fa09e481a3115680f80d1f5d86522bd6` | 2026-03-30 | chore(deps): bump pygments from 2.19.2 to 2.20.0 in /src/python (#322) | pending |
| 569 | `b63f595d2c01f1a150da8362696fc6dfb36b2b39` | 2026-03-30 | Merge branch 'develop' | pending |
| 570 | `08be3e06613f9eea7eb5ff032b3e440adbe9440c` | 2026-04-04 | Merge branch 'master' into develop | pending |
| 571 | `7640643651b9ee8adf3be67d5d3dfe5b21877b1b` | 2026-04-04 | Replace debug toggle with log level dropdown (#252) (#332) | pending |
| 572 | `9e866765262895d28fc2f232b4d97247ef2ca00f` | 2026-04-04 | Add size sorting to file list (#254) (#334) | pending |
| 573 | `850258e5c03a625f12755a6df18ec066de3866da` | 2026-04-05 | Add virtual scrolling for large file lists (#256) (#335) | pending |
| 574 | `34b03d88e16bef2e04220af5ed2df2de0a313857` | 2026-04-05 | chore(deps): bump lodash from 4.17.23 to 4.18.1 in /website (#336) | pending |
| 575 | `50962f4b2e70a53c4abc62a0f6a1a8f88a41e27f` | 2026-04-07 | Promote recommended workflow to front-and-center in docs | pending |
| 576 | `9727d504655afa7991f856677f0cee8e0cf753f2` | 2026-04-07 | Fix hyphenation of 'hard-linked' in usage docs | pending |
| 577 | `a59b61994709ed26c90e1b7681fd84147d892979` | 2026-04-07 | Merge pull request #340 from nitrobass24/docs/promote-recommended-workflow | pending |
| 578 | `48a84433b38000897ea57122a92f06dc5d0e2e54` | 2026-04-05 | chore(deps): bump the angular group in /src/angular with 9 updates | pending |
| 579 | `1bc3b08c1bf18d8c3db7fc2720b00da769041a44` | 2026-04-07 | Merge pull request #337 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-a0b9192591 | pending |
| 580 | `5e294a4a16a83ca770e48fa80b1774b46e4227fb` | 2026-04-07 | Fix bulk delete crash from unbounded process spawning (#338) | pending |
| 581 | `5e4fa2e55137bab4b68a875b4909b24e47d6b190` | 2026-04-07 | Fix infinite busy-loop in command process throttling | pending |
| 582 | `7e747cce0d3924ef040b27ff18c85e774102f0a8` | 2026-04-07 | Add debug logging when delete commands are deferred | pending |
| 583 | `ae49970c12b554e04bf6d621bdb1bae4930a1dd4` | 2026-04-07 | Fix ruff formatting in controller.py | pending |
| 584 | `10b2f00aacb3c07c4e273865386b780d8db801ec` | 2026-04-07 | Scope concurrency cap to delete operations and allow move retry on failure | pending |
| 585 | `c485d00f9569cab3f48943669915565984496640` | 2026-04-07 | Merge pull request #341 from nitrobass24/fix/bulk-delete-crash | pending |
| 586 | `6b547fc2399bfa66d25d55f5a3ede6ba367214e3` | 2026-04-08 | chore(deps-dev): bump @hono/node-server in /src/angular | pending |
| 587 | `1e6319a6489b5abc0f9205d5b4234e90095d2d8e` | 2026-04-07 | Merge pull request #343 from nitrobass24/dependabot/npm_and_yarn/src/angular/hono/node-server-1.19.13 | pending |
| 588 | `f8f9a68b2fa626ae152ad1d1495bbb5261ed2836` | 2026-04-07 | Bump jsdom 29.0.1→29.0.2 and vitest 4.1.2→4.1.3 | pending |
| 589 | `d869371dc14bc65b46344221eb30a4eb2d06f9af` | 2026-04-08 | Merge pull request #345 from nitrobass24/chore/bump-dev-deps | pending |
| 590 | `baea40acc4bb9dc341cf274fbd6f36fe0b054b09` | 2026-04-08 | chore(deps-dev): bump hono from 4.12.7 to 4.12.12 in /src/angular | pending |
| 591 | `6b93442c05f4ccfde9770c4a9c569eedfb4ddf3f` | 2026-04-08 | Merge pull request #344 from nitrobass24/dependabot/npm_and_yarn/src/angular/hono-4.12.12 | pending |
| 592 | `6302d39236f5cecdf1c9494ae0f5e7f0ab20399f` | 2026-04-13 | chore(deps-dev): bump vitest from 4.1.3 to 4.1.4 in /src/angular (#359) | pending |
| 593 | `849c864d2faf555090bdd0f69a5279556d81fba4` | 2026-04-13 | chore(deps-dev): update pytest-timeout requirement in /src/python (#354) | pending |
| 594 | `69410ad54432058cd1c0c616de28b565edf4a3b3` | 2026-04-13 | chore(deps): update tblib requirement in /src/python (#350) | pending |
| 595 | `5f3c1cad733b53b914c2be2b993d4c4e0a19e8e6` | 2026-04-13 | chore(deps): bump react from 19.2.4 to 19.2.5 in /website (#356) | pending |
| 596 | `511f58817a37df8299a0804ce15966780b307f4c` | 2026-04-13 | chore(deps): bump @docusaurus/core from 3.9.2 to 3.10.0 in /website (#355) | pending |
| 597 | `30b73b84e344c45a632f087cfe58f14b153c1d2e` | 2026-04-13 | chore(deps): update pexpect requirement in /src/python (#349) | pending |
| 598 | `d6ba7f735f67813d6826978bc04b55f7c285b130` | 2026-04-13 | chore(deps): bump @docusaurus/preset-classic in /website (#358) | pending |
| 599 | `891baac15b08771ba3db711228deb28b0bd324d4` | 2026-04-13 | chore(deps-dev): bump @docusaurus/types from 3.9.2 to 3.10.0 in /website (#353) | pending |
| 600 | `f0a5d28bd8529c07d373423ff95d8d20281c1a58` | 2026-04-13 | chore(deps-dev): bump @docusaurus/module-type-aliases in /website (#351) | pending |
| 601 | `9e384015f132a7f24fcb4db5667adbeb3d0ac07c` | 2026-04-13 | chore(deps): bump the angular group in /src/angular with 10 updates (#357) | pending |
| 602 | `5314804d7e6b6a63454992c2e819be062f801356` | 2026-04-13 | chore(deps-dev): bump pytest from 9.0.2 to 9.0.3 in /src/python (#360) | pending |
| 603 | `c6e757bdd7d67b2b4e46eb5c551a5b44a5896ea9` | 2026-04-13 | chore(deps): bump softprops/action-gh-release from 2 to 3 (#347) | pending |
| 604 | `e03d24b5f9e8aaf30376105d046795e7a9f212a7` | 2026-04-13 | chore(deps-dev): update ruff requirement in /src/python (#352) | pending |
| 605 | `14f505e7ecba3a78af60da4b576c1004e5acb5a0` | 2026-04-13 | chore(deps-dev): update testfixtures requirement in /src/python (#348) | pending |
| 606 | `56c0a806e330114d0913b5d599e723c05398cb58` | 2026-04-13 | Fix unused declarations in file-list component and view-file service (#361) | pending |
| 607 | `143a15cd691aee6aea483c3bb155f30923a3d6b0` | 2026-04-13 | Refactor god functions in header component and auto-queue (#342) | pending |
| 608 | `40bd27cee595e0f35e7e22103611348760b24960` | 2026-04-14 | Add Sonarr/Radarr integration (#328) (#362) | pending |
| 609 | `ad6aa298f05e399933bf799385866554a7572cf0` | 2026-04-17 | chore(deps-dev): bump hono from 4.12.12 to 4.12.14 in /src/angular (#365) | pending |
| 610 | `10c62dfb8b6aa3aeab265aa607dbad3c3df7f1f8` | 2026-04-17 | chore(deps): bump follow-redirects from 1.15.11 to 1.16.0 in /website (#364) | pending |
| 611 | `07e948c4506be44b0284a6b49f486e6ad3ed4359` | 2026-04-19 | chore(deps): bump the angular group in /src/angular with 8 updates (#368) | pending |
| 612 | `20259af3786c9ac6c30eba453d592322a1c28c52` | 2026-04-19 | chore(deps): bump react-dom from 19.2.4 to 19.2.5 in /website (#367) | pending |
| 613 | `3f21e754fab66339df6b652fe14041772368c044` | 2026-04-19 | chore(deps): bump actions/upload-pages-artifact from 4 to 5 (#366) | pending |
| 614 | `6ca434ee17304d5c4b3274217d3d218710d2cd2f` | 2026-04-19 | Fix file list scrolling on Android mobile browsers (#371) | pending |
| 615 | `0bbd15bc3e0c3270b251b26a618dca6f0df757e0` | 2026-04-20 | Add @angular-eslint and wire ng lint into CI (#377) | pending |
| 616 | `e3a8d6d306be8d3c02470c0cd0424d8ab4543f20` | 2026-04-20 | Fix Docusaurus 3.10.0 build: add @docusaurus/faster devDependency (#387) | pending |
| 617 | `a3557b31d021f1ca899978fc26fe54be5d0ce2b7` | 2026-04-20 | Fix high-severity npm audit vulnerabilities (#388) | pending |
| 618 | `76094bd1121e29d88eb121d8432d21ff0296a6b4` | 2026-04-20 | Strip exception details from integrations error response (#386) | pending |
| 619 | `4863c561982b1e49c347eae4668c0a096526ef97` | 2026-04-20 | Use 'with' on controller __model_lock to prevent deadlock on exception (#373) (#384) | pending |
| 620 | `fb02be92d30e483142a0e844b95caf3ed342bdcf` | 2026-04-20 | Stream log files line-by-line instead of readlines() (#385) | pending |
| 621 | `71f922b8d8adf4666263936ee8a0db39887b3218` | 2026-04-20 | Add Code Health section to CLAUDE.md (#393) | pending |
| 622 | `a4f4b9070e7c04e4fb933d704bba0ff931818ddd` | 2026-04-20 | Fix lint quick-wins and graduate 7 trivial rules to error (#378) (#389) | pending |
| 623 | `6d7b49e0330461e639f1ddf94055fa3e707506c3` | 2026-04-21 | Disable sequence diagrams and poem in .coderabbit.yaml | pending |
| 624 | `2b9f9f9baa0103d0135927bed5b0e69bbfc3b12b` | 2026-04-21 | Fix file list height on iOS/Android by measuring chrome dynamically (#396) | pending |
| 625 | `d289d3b4b2a4814032f78bc7a505aa31f859b64a` | 2026-04-21 | Replace 50 no-explicit-any violations with proper types (#390) | pending |
| 626 | `7c2c0b2738f696f65708bda611b2399ecb114bea` | 2026-04-21 | Fix 55 template a11y violations (buttons + alt-text + focusable interactives) (#391) | pending |
| 627 | `60635f3bca1413daa25cd8c2be732bd0964e5c37` | 2026-04-21 | Migrate 5 components to ChangeDetectionStrategy.OnPush (#392) | pending |
| 628 | `92ea8f5b25f6325a5fd620b312f665ea9c17529c` | 2026-04-22 | Add from-clause to 25 raise statements, remove B904 ignore (#399 Phase 3) | pending |
| 629 | `6aa15da9d40685e695f5b56725c4280d934e1868` | 2026-04-22 | Convert 305 .format() calls to f-strings, remove UP032 ignore (#399 Phase 2) | pending |
| 630 | `6956f14b1a829a966faea655ca7a9b3b0f3f3f61` | 2026-04-22 | Resolve 6 deferred ruff ignores (#399 Phase 1) (#400) | pending |
| 631 | `1b1feddb07cc14a9fbdb583f6699c54947cdf0f8` | 2026-04-22 | Merge remote-tracking branch 'origin/develop' into lint/ruff-phase2 | pending |
| 632 | `5c9238855b686018e2deb3553582dd5955aff6f9` | 2026-04-22 | Merge pull request #401 from nitrobass24/lint/ruff-phase2 | pending |
| 633 | `4a4278e73a82247a96ffce46036ca4aceadb97f6` | 2026-04-22 | Merge remote-tracking branch 'origin/develop' into lint/ruff-phase3 | pending |
| 634 | `5f1c11612f3d208858b2919f91f689a328d59cab` | 2026-04-22 | Use from e on FileNotFoundError handlers to preserve PATH context | pending |
| 635 | `b4111a54ebb31a4167bae4f6e67cae3aee64cdff` | 2026-04-22 | Merge pull request #402 from nitrobass24/lint/ruff-phase3 | pending |
| 636 | `51620cb277d5442139276ccb7b87f74fb1f8034e` | 2026-04-22 | Enable SIM, C4, RET, PERF, RUF rule sets (#399 Phase 4) | pending |
| 637 | `71f420294f08b91251acd93c8195b9e5fae2091e` | 2026-04-22 | Revert f-strings in scan_fs.py (Python 3.5 compat), remove redundant list() | pending |
| 638 | `8e0229bbb937e87c546b3d61755151bcdece7350` | 2026-04-22 | Merge pull request #403 from nitrobass24/lint/ruff-phase4 | pending |
| 639 | `1da2c1ae91d6bf1737b89709872062e41ad26cd7` | 2026-04-22 | Enable ruff C901 complexity enforcement with max-complexity=12 (#395) | pending |
| 640 | `4796c7f07560baa566b561ed12e05b6bb3291d48` | 2026-04-22 | Merge pull request #406 from nitrobass24/lint/enable-c901 | pending |
| 641 | `17737ebd52141f3e800f6eee7d94284d36e4abab` | 2026-04-22 | Redact all sensitive config in debug log, harden auto_queue responses | pending |
| 642 | `cc0f8c0cdd16a5298558cf69e730ac5092a77bea` | 2026-04-22 | Move nosniff constant to top of class, add Content-Type to GET handler | pending |
| 643 | `5561895d4c24b256980a36d167f8d2f522bf7241` | 2026-04-22 | Merge pull request #407 from nitrobass24/fix/security-hardening | pending |
| 644 | `04d8c9efa648426befa3622a7ecffaa2e8d921d8` | 2026-04-22 | Extract persist_keys module from controller.py (#394 Phase 1B) | pending |
| 645 | `a0ae4a1e5ababe8d03a377f2c3aea0b5dbb565b1` | 2026-04-22 | Extract exclude_patterns module from controller.py (#394 Phase 1A) | pending |
| 646 | `82b95cb5a41cfcee75cb7ba1e355a21692c68ef5` | 2026-04-22 | Merge pull request #410 from nitrobass24/refactor/controller-a-exclude-patterns | pending |
| 647 | `2cd6d5372d31c5934dedd19cb954b042bd3da887` | 2026-04-22 | Merge remote-tracking branch 'origin/develop' into refactor/controller-b-persist-keys | pending |
| 648 | `701b157129a654e685474991ceffda1aa3ecbbec` | 2026-04-23 | Merge pull request #411 from nitrobass24/refactor/controller-b-persist-keys | pending |
| 649 | `d0ac1a8706d6c179dc2b8bbd9e3883d0aa43d9cb` | 2026-04-22 | Extract pair_context module from controller.py (#394 Phase 1C) | pending |
| 650 | `04163a437395ded4d033e9121ed28058e0dca8bd` | 2026-04-23 | Validate extract_path when use_local_path_as_extract_path is False | pending |
| 651 | `420754021d0474559418377a9a83cf4e48a622e1` | 2026-04-23 | Use f-string for consistency in validate_config error message | pending |
| 652 | `9586c93b8bf275d2f07a7ce1e68db50b7c71ba85` | 2026-04-23 | Merge pull request #412 from nitrobass24/refactor/controller-c-pair-context | pending |
| 653 | `24ae033b4b5149c47fdd4464210867ef669551cb` | 2026-04-23 | Extract ModelRegistry from controller.py (#394 Phase 2D) | pending |
| 654 | `a819582e0e52d81e72cc1ec99e2ffe1c0a5ad1fb` | 2026-04-23 | Use RLock in ModelRegistry to prevent listener re-entry deadlock | pending |
| 655 | `7a505c75cd2886a63e05cdeaed6380d161d7722f` | 2026-04-23 | Merge pull request #413 from nitrobass24/refactor/controller-d-model-registry | pending |
| 656 | `a37c8822d61d4c0f8c9ba6ceec9cf014c3259317` | 2026-04-23 | Extract CommandPipeline from controller.py (#394 Phase 2E) | pending |
| 657 | `29fb94b62501834d4c94bde8c54c7759ebce22f5` | 2026-04-23 | Clean up command_pipeline: type hints, dedup staging, collapse lookup | pending |
| 658 | `f68788fe2cd5d86dc1e455465e0449d19d4a7639` | 2026-04-23 | Fix Pyright: add type arguments to Queue and list in CommandPipeline | pending |
| 659 | `bbb9a27885cbfae82f70f0beeac76019f01c9799` | 2026-04-23 | Fix Pyright: make cross-module methods public, remove unused local import | pending |
| 660 | `82ca00e0f6e812a4e161ececbb4f655e3494caed` | 2026-04-23 | Guard staging check in spawn_deferred_move, downgrade to debug | pending |
| 661 | `21636c6a6d7e94a98d9fa3dfbcc5b9c9b6b07a46` | 2026-04-23 | Merge pull request #414 from nitrobass24/refactor/controller-e-command-pipeline | pending |
| 662 | `0fb2fe3f26eccbe4b8c734f844dddd860ca74c2d` | 2026-04-23 | Reduce Sshcp.__run_command complexity from 19 to under 12 (#409) | pending |
| 663 | `34324dd4d40465ed5da677f59775bed50cf5ba7d` | 2026-04-23 | Reuse decoded before string instead of redundant decode calls | pending |
| 664 | `78b8b9deab3bac94991dc50eb0a04eb898b57870` | 2026-04-23 | Reduce ModelBuilder.build_model complexity from 58 to 7 (#408) | pending |
| 665 | `1044cf272c8cda29f594f582de2bb579b7c4da40` | 2026-04-23 | Replace pass with early return in _check_persist_authority | pending |
| 666 | `15db5ce632bfedfd33c686fa9366e9d97030cd4d` | 2026-04-23 | Extract ModelUpdater from controller.py (#394 Phase 3F) | pending |
| 667 | `541d347e21019c1bbabf112c8ba459c01bffd248` | 2026-04-23 | Fix Pyright: make sync_persist_callback public | pending |
| 668 | `940be9207bb4505df7609a8eb02e16fde111abaf` | 2026-04-23 | Add focused tests for extracted controller modules | pending |
| 669 | `db4c1998f65385edde0f85e2672536ee95bf62ba` | 2026-04-23 | Update CLAUDE.md: C901 is now enforced in CI | pending |
| 670 | `d205ac77d571c4f1f1e505e6f8bb162f44a82ee6` | 2026-04-23 | Format test_model_updater.py | pending |
| 671 | `c47d5969fcac3fea571715836e5eba0e1dd499c6` | 2026-04-23 | Review fixes: dedup sync_persist, fix stale docs, add edge case test | pending |
| 672 | `3953f880158852b580d98a6cd12db2ea91811331` | 2026-04-23 | Make PairContext scan attrs public, note test exclusion in CLAUDE.md | pending |
| 673 | `8dc6d42bbd1f4fdb485962a6a522a18af19265cd` | 2026-04-24 | Merge pull request #415 from nitrobass24/refactor/controller-f-model-updater | pending |
| 674 | `63e4c3df746e37cae1431b084b680c4a64951c80` | 2026-04-24 | Merge branch 'develop' into refactor/model-builder-complexity | pending |
| 675 | `f16f51c00698ce1e8236de82596db962cc60e4d7` | 2026-04-24 | Remove leading underscores from static helper parameters | pending |
| 676 | `399956cd0f682b2eb8b8c662ffeb7288f0e06df3` | 2026-04-24 | Rename model_file → root_model_file in _build_children parameter | pending |
| 677 | `deb86e554bd3891334e71841771b606c0d6f8db8` | 2026-04-24 | Merge pull request #416 from nitrobass24/refactor/model-builder-complexity | pending |
| 678 | `c9934bc6c3c95924585dd90f64f575c8a0df1705` | 2026-04-24 | Merge branch 'develop' into refactor/sshcp-run-command | pending |
| 679 | `c506cf5021d7f644e4e810cda41e18c7bfad0739` | 2026-04-24 | Merge pull request #417 from nitrobass24/refactor/sshcp-run-command | pending |
| 680 | `d6bf023b3957b6f1aba719b9105b46cc3b9b86b3` | 2026-04-24 | Decompose step() and _update_pair_model_state() to remove C901 noqas (#418 Phase 1) | pending |
| 681 | `4d2dd329051c7c2776f8e3649fe54a4f500a09cc` | 2026-04-25 | Merge pull request #419 from nitrobass24/refactor/c901-phase1 | pending |
| 682 | `3dd309ecc8ed12a7738f8f36db818bebc97ed769` | 2026-04-24 | Decompose update() into 10 focused methods (#418 Phase 2) | pending |
| 683 | `7c2db6d3d12bc6fd20d329924b64fda0e0d0b229` | 2026-04-25 | Review fixes: type hints, hoist predicate, split prune, drop unused return | pending |
| 684 | `371d91f9aad7b821bcef6eab23cadff03c7dbb9a` | 2026-04-25 | Add -> None return type annotation to update() | pending |
| 685 | `d6d915075c555327a99cea5c585449df59dacb81` | 2026-04-26 | Merge pull request #420 from nitrobass24/refactor/c901-phase2 | pending |
| 686 | `be47a0834efffc2ad29d183d7af3f2e7fb29395a` | 2026-04-26 | chore(deps-dev): bump typescript-eslint in /src/angular | pending |
| 687 | `9debbc13e3343698222b334f50e482e5f20aca64` | 2026-04-26 | Merge pull request #423 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/typescript-eslint-8.59.0 | pending |
| 688 | `60475db70265faf10ae444db4c793f80fd8a42e8` | 2026-04-26 | chore(deps): bump the angular group in /src/angular with 10 updates | pending |
| 689 | `6588dd0d1035ca3625fd88f862c257166c09394c` | 2026-04-26 | Merge pull request #421 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-c16b7220b9 | pending |
| 690 | `3eba51403f4838b0a23fc4a5ba40bb0faeb402b7` | 2026-04-26 | chore(deps-dev): bump vitest from 4.1.4 to 4.1.5 in /src/angular | pending |
| 691 | `43d3deea003b1515a508aaf336be5168c8c6d2f8` | 2026-04-26 | Merge pull request #422 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/vitest-4.1.5 | pending |
| 692 | `8bb2e1f2840a46cb54ea6af81d2baf9169cb5043` | 2026-04-26 | chore(deps): bump postcss from 8.5.6 to 8.5.12 in /src/angular | pending |
| 693 | `19e26655ba66fef2b8a74791c0acfcd3d9e2b335` | 2026-04-26 | Merge pull request #424 from nitrobass24/dependabot/npm_and_yarn/src/angular/postcss-8.5.12 | pending |
| 694 | `dc0b0d36bc0711bebe8802ff38b1370531f54277` | 2026-04-27 | Support multiple Sonarr/Radarr instances mapped to path pairs | pending |
| 695 | `2b89ac5803994f822fd20bef4acd38f2dea8f078` | 2026-04-27 | Fix CI: ruff format, pyright, and E2E selector collisions | pending |
| 696 | `d16e55a6bd5edb3deadf4317900670edb7ed82ba` | 2026-04-27 | Address review feedback for multi-instance *arr PR | pending |
| 697 | `309a8364d6d278a53b80d6f24a7ef03b591862cd` | 2026-04-28 | Review fixes: harden error handling, add cross-validation, close test gaps | pending |
| 698 | `9950f5860d35160327a7cfac83dab7a3ae581222` | 2026-04-28 | Fix lint: ruff format and zip strict parameter | pending |
| 699 | `5d564d61ab292aa7624e3b98dbf59377c1bd586d` | 2026-04-28 | Add type="button" to all integrations component buttons | pending |
| 700 | `a697133a441ab4b6e4843c11af40fc7779184143` | 2026-04-28 | Merge pull request #426 from nitrobass24/feat/multi-arr-instances | pending |
| 701 | `2c11288d711559a9d03be14cf7d7a0b053d4a405` | 2026-04-28 | Add Discord and Telegram notification presets (#329) | pending |
| 702 | `972661de67be2d51b26cf4c7a1ed34dfddeb2667` | 2026-04-28 | Review fixes: disabled binding, webhook_url redaction, Markdown escaping, full event context, Content-Type on 400s | pending |
| 703 | `817f875aec6f71bfc730584da2d63148c6624b51` | 2026-04-28 | Escape backticks in Discord embeds, add URL scheme check to test handler | pending |
| 704 | `99acb8035be7e193d58da07afdbd5b8ee7a2612a` | 2026-04-30 | Fix thread leak in _fire_raw if thread.start() raises | pending |
| 705 | `db64de7c4995a524fd378938e02f253917f274ff` | 2026-04-30 | Merge pull request #428 from nitrobass24/feat/discord-telegram-notifications | pending |
| 706 | `e4026afd04d54412e782289632688e3bd101d285` | 2026-04-30 | Fix oversized Sonarr/Radarr add buttons in integrations header | pending |
| 707 | `f57807bbd22c459ed76ef359483fccbd9864b9be` | 2026-04-30 | Merge pull request #429 from nitrobass24/fix/integrations-header-style | pending |
| 708 | `3dc9f1a623aeafbbc1d2323b931ec46d66e7e14e` | 2026-04-30 | Match integrations header to path-pairs style | pending |
| 709 | `41167bddf3f952a7b6dbc89617ff0e408bb10cd0` | 2026-04-30 | Fix arr-picker dropdown white background in dark mode | pending |
| 710 | `f637b5f93f5d79f587dd45f31b60cd02593c812d` | 2026-04-30 | Fix E2E: broaden getSection selector for .header elements | pending |
| 711 | `c05fc01cbfe44b17960ac34929b0db3921ba25d4` | 2026-04-30 | Use semantic h3 headings in integrations and path-pairs headers | pending |
| 712 | `d213aecebb41c6c5b733ac85d16bcb16c6297ad5` | 2026-04-30 | Merge pull request #430 from nitrobass24/fix/integrations-header-v2 | pending |
| 713 | `9f64bec47a623f807e0c677e3d12b0ae99e2d8db` | 2026-04-30 | Remove gray color override from card headers in dark mode | pending |
| 714 | `065bc0e91e1dbb37c2373a85a28e622a011fbb72` | 2026-04-30 | Merge pull request #431 from nitrobass24/fix/header-text-color-consistency | pending |
| 715 | `c1328dabf67c56f06546ac312ef57ed7f5f08ffc` | 2026-04-30 | Release v0.17.0 — Multi-instance *arr integration, Discord & Telegram notifications | pending |
| 716 | `35184494c73142a00de1644deb31eba409730334` | 2026-04-30 | Merge pull request #432 from nitrobass24/release/v0.17.0 | pending |
| 717 | `ede990a15131eabd1f2599eb3acb2feb53448d66` | 2026-04-30 | Remove stale Docker test infrastructure | pending |
| 718 | `10075814803b238ee58b093b45019396dfa6ee2e` | 2026-04-30 | Merge pull request #434 from nitrobass24/chore/remove-stale-test-dockerfiles | pending |
| 719 | `2b516b233655b61ee62ba143e7e09cef9ed1ec3c` | 2026-04-30 | Rewrite test-image Dockerfile as Alpine with Python 3.13 | pending |
| 720 | `9903e45c657b8f32dafbf57fbd7f7b03ba5d0e92` | 2026-04-30 | Merge pull request #435 from nitrobass24/chore/test-image-alpine | pending |
| 721 | `c0e433284c446bd8a5ebfb3a4973103e6d48530c` | 2026-04-30 | Collapse Dockerfile to 2 stages with Python 3.13-alpine | pending |
| 722 | `51ede44cca43fe3a6dcfd9db2f98e833c733e1b3` | 2026-04-30 | Merge pull request #436 from nitrobass24/chore/dockerfile-python313-alpine | pending |
| 723 | `cf856e6544b2196f2d1c46e2450f220aa1ea1749` | 2026-04-30 | Use RUN --mount for uv to cut image size from 114 MB to 64 MB (#437) | pending |
| 724 | `527238989651ad336b5021b06b0642bf97f2103c` | 2026-05-01 | Flush configs on write, differentiated restart notifications, LFTP hot-reload (#433) | pending |
| 725 | `750f94dca9253479b57476464f3d8769c1989981` | 2026-05-01 | PR 9: E2E — File Actions & Error States (#438) | pending |
| 726 | `eff4a4bb9b69122c38733d2124a31e1d7e10460a` | 2026-05-01 | Add Python integration tests to CI (#449) | pending |
| 727 | `9e95c56f404b269b10d1931f0dba98ba87cd4d2d` | 2026-05-01 | Add security middleware unit tests (47 tests) (#439) | pending |
| 728 | `ed2bdfea0bc5f39ef8cfd549d774284d85bd9754` | 2026-05-01 | Add controller core unit tests (36 tests) (#444) | pending |
| 729 | `8d93a209914669080058021f1e6a8fc27026e258` | 2026-05-01 | PR 3: Python — Web App Job & Context tests (#446) | pending |
| 730 | `5a2578a2d6c85336eaade7bbbcc81235b0c6d525` | 2026-05-01 | PR 4: Python — Handler Integration Test Expansion (#447) | pending |
| 731 | `8f3d45ea96e55940ff7694f97f50f9a9cd94ac5f` | 2026-05-01 | Add ViewFileFilterService tests (18 tests) (#440) | pending |
| 732 | `c01650bd75cc52f3f5318df80985f95f9adf8795` | 2026-05-01 | Add E2E tests for integrations CRUD (#441) | pending |
| 733 | `1d48e9ebf245e29b7b8811474c974d6b15f918ab` | 2026-05-01 | Add AutoQueueService and PathPairsService tests (25 tests) (#445) | pending |
| 734 | `2866c040b50898d47176bb4d52c960bfb2130cb7` | 2026-05-01 | Add FileOptions, Integrations, and Option component tests (28 tests) (#448) | pending |
| 735 | `55d77c66b5fb642a3240733b25eab2bfc2c2b68b` | 2026-05-01 | Add HeaderComponent and VersionCheckService tests (20 tests) (#443) | pending |
| 736 | `c3f8a912bb253e64b410c18fa4227b87877bc7f6` | 2026-05-01 | Cache Playwright browsers and npm deps in CI (#452) | pending |
| 737 | `12047c51106a05ecce18f3de3024fbe3063fd4a0` | 2026-05-01 | Track Playwright package-lock.json so CI cache step resolves (#453) | pending |
| 738 | `cbe75f3338ed2a2472f0c5f9c1ab23eca0714dcd` | 2026-05-01 | Add test count badges and update Python version to 3.13 (#451) | pending |
| 739 | `0cc3533ff4be17f7a2b36f4518f10a432eda0d56` | 2026-05-01 | PR 11: E2E — Settings Coverage Expansion (#442) | pending |
| 740 | `dc6b73b4d81dba0eaca65cd820b4239c4366c303` | 2026-05-02 | Refresh README to cover features through v0.17.0 (#454) | pending |
| 741 | `be50a3627e25da368bd67f5d75ce88eb67a97602` | 2026-05-02 | Fix flaky Hash Algorithm select wait condition (#455) | pending |
| 742 | `142dc6266eed7a535fc46a2eccf29b0cf4ff8b8b` | 2026-05-02 | Warm Playwright cache on develop and bump workers to 10 (#456) | pending |
| 743 | `3b6645193fd8cf65eec72fa8804a8cb104451cd2` | 2026-05-03 | Stop leaking StreamHandlers across test runs (#450) (#457) | pending |
| 744 | `30d2977bd7b0c71f6353c7b36e4e1d6531737b8d` | 2026-05-03 | chore(deps): bump @docusaurus/preset-classic in /website (#458) | pending |
| 745 | `cbe98eb08b863c57cb18b40e827888e322e86944` | 2026-05-03 | chore(deps-dev): bump @docusaurus/faster in /website (#460) | pending |
| 746 | `806ed99e875cacb5e04e26f9a91996b2f4a85e49` | 2026-05-03 | chore(deps): bump the angular group in /src/angular with 10 updates (#463) | pending |
| 747 | `fc6c9bfc3a7a283b5d134236b37f467b1636beed` | 2026-05-03 | chore(deps-dev): bump typescript-eslint in /src/angular (#464) | pending |
| 748 | `3a487fe05a258a0487ca84d887345b00c4bc8be3` | 2026-05-03 | chore(deps-dev): bump jsdom from 29.0.2 to 29.1.1 in /src/angular (#465) | pending |
| 749 | `a301510ec9ffc002b1bc5dd9315060b4f9a026cd` | 2026-05-03 | chore(deps-dev): bump @docusaurus/module-type-aliases in /website (#459) | pending |
| 750 | `899fc1e43b9cc98d276f6c7240b514e1a8cd2a6f` | 2026-05-03 | chore(deps-dev): bump eslint from 10.2.1 to 10.3.0 in /src/angular (#466) | pending |
| 751 | `9376c546d39ecccad7ae4a3d9df734a7088f5860` | 2026-05-03 | chore(deps-dev): bump @docusaurus/types in /website (#461) | pending |
| 752 | `660f99bec1b2003a892c6c6e348f1a2a27d45327` | 2026-05-03 | chore(deps): bump @docusaurus/core from 3.10.0 to 3.10.1 in /website (#462) | pending |
| 753 | `ecfa8f54f10e8faa8713022c28df53750322fb20` | 2026-05-04 | Address CodeRabbit test-quality findings (#468) | pending |
| 754 | `2598a6a12c8a6fc995daa76c28b562c272c335ae` | 2026-04-22 | Release v0.16.0 - Sonarr/Radarr integration, mobile scroll fix, full lint enforcement | pending |
| 755 | `f848721ae620f5e70c8a5970ad6fc1f77285d1ab` | 2026-04-22 | Fix review findings: a11y, dark-mode disabled, Content-Type, router cleanup | pending |
| 756 | `cb1c4f40598c8552123d8887254bd7ab1b95788a` | 2026-04-22 | Stop scanning log file after pagination cursor is exhausted | pending |
| 757 | `b8df7e7a7b75f43282185416c2e2ca4b42968118` | 2026-04-08 | Release v0.15.0 — Virtual scrolling, size sorting, log levels, bulk delete fix | pending |
| 758 | `160c9c5eaf5b206a1cb807f1cf3ab3ecd625e43f` | 2026-04-08 | Clarify docs change in changelog — hard-link workflow moved to Usage section | pending |
| 759 | `cb82386fcde799abd5ed2fe3a28a8bd4064b26f9` | 2026-04-08 | Fix review findings: log levels, mobile viewport, bulk error handling, doc labels | pending |
| 760 | `8af586869b28064f3ec808743c37020fef73c356` | 2026-04-08 | Merge pull request #346 from nitrobass24/release/v0.15.0 | pending |
| 761 | `9a57bc52b58127785ac159ef023ca695ecab1b1b` | 2026-04-22 | Merge remote-tracking branch 'origin/master' into release/v0.16.0 | pending |
| 762 | `fe7f2d18a6b23923f78c1a75efdd9af0be013fa1` | 2026-04-22 | Fix CI: remove stale SCSS variable ref, format integrations.py | pending |
| 763 | `21614b976416474c88acc132803739e0873369f6` | 2026-04-22 | Fix CodeRabbit config schema, hide inner checkbox from assistive tech | pending |
| 764 | `75c4562fcd3335710f4d6e3e3544af38b4d9c941` | 2026-04-22 | Merge pull request #397 from nitrobass24/release/v0.16.0 | pending |
| 765 | `af22c75c3bad52d0b689edb5b6879bd8f3e56ca7` | 2026-04-30 | Merge branch 'develop' | pending |
| 766 | `04d63202984385368a077c939ab9b921be8d9b5e` | 2026-05-04 | Merge branch 'master' into develop | pending |
| 767 | `a958f6abccb96910397c0a29c5d956eccff9ef15` | 2026-05-05 | Address round-2 CodeRabbit test-quality findings (#470) | pending |
| 768 | `65c1339e084860f98194c1d365903d9a9c1fc4c6` | 2026-05-05 | Address round-3 CodeRabbit findings (#471) | pending |
| 769 | `7cc5de450e9a747be57075645e123eb3bc9995d9` | 2026-05-05 | Pin Python builder to alpine3.23 to match the runtime stage (#472) | pending |
| 770 | `a317805215bf22bf9f3b7c989b07c818bc1d4960` | 2026-05-05 | Address round-4 CodeRabbit findings on PR #467 | pending |
| 771 | `e0f7ee6e352d808d44e76d9218677d716b54dd95` | 2026-05-05 | Address round-5 CodeRabbit findings on PR #467 | pending |
| 772 | `1c3241c23e49e53a3194f9d752e994e4507c8d57` | 2026-05-05 | Address round-6 CodeRabbit findings on PR #467 | pending |
| 773 | `5bc7aa5ceef3ae4d61890b80c1aa11ad65d409c1` | 2026-05-06 | Address round-7 CodeRabbit findings on PR #467 | pending |
| 774 | `686d6f2df1c34d58cae098fffefbc88836cb4573` | 2026-05-06 | Merge pull request #467 from nitrobass24/develop | pending |
| 775 | `4bbe4cf02f4fe3be42eeb22a66fdfb90f9ace07f` | 2026-05-13 | Update README to remove fork note | pending |
| 776 | `96f71f51bbef72cf18a3213627a75f232f237314` | 2026-05-07 | chore(deps): bump ip-address and express-rate-limit in /src/angular | pending |
| 777 | `973d4fb235d609cffaa140fecd7a68230d041858` | 2026-05-06 | Merge pull request #473 from nitrobass24/dependabot/npm_and_yarn/src/angular/multi-7bdfbe8666 | pending |
| 778 | `8317993985ad2313b7e4b4d0c6d3ec9b0943028d` | 2026-05-07 | chore(deps): bump hono from 4.12.14 to 4.12.18 in /src/angular | pending |
| 779 | `48991b8883f2d34604b22367057536377b203049` | 2026-05-06 | Merge pull request #474 from nitrobass24/dependabot/npm_and_yarn/src/angular/hono-4.12.18 | pending |
| 780 | `689b25b1ff6b7aecc128d55f0bfb36123fa63151` | 2026-05-09 | chore(deps): bump fast-uri from 3.1.0 to 3.1.2 in /website | pending |
| 781 | `0f5eb7434b3090abd9c452f8bef86b6cecf6e97b` | 2026-05-08 | Merge pull request #475 from nitrobass24/dependabot/npm_and_yarn/website/fast-uri-3.1.2 | pending |
| 782 | `78ea06e4fec4acfcebe526e73b2dffdd4601ac97` | 2026-05-09 | chore(deps): bump fast-uri from 3.1.0 to 3.1.2 in /src/angular | pending |
| 783 | `c4d72a3765be5ee317668d640a5007de5e2459a1` | 2026-05-08 | Merge pull request #476 from nitrobass24/dependabot/npm_and_yarn/src/angular/fast-uri-3.1.2 | pending |
| 784 | `0ecc19350bcf9b44d1b1c53fa1cac2d7c80a7910` | 2026-05-09 | chore(deps): bump @babel/plugin-transform-modules-systemjs in /website | pending |
| 785 | `2bf54f6fce43241b96c31dae1176938843df7c6c` | 2026-05-09 | Merge pull request #477 from nitrobass24/dependabot/npm_and_yarn/website/babel/plugin-transform-modules-systemjs-7.29.4 | pending |
| 786 | `62fa0eb928cb30d9deda98c9f139fe66d8dca17b` | 2026-05-10 | chore(deps): bump react from 19.2.5 to 19.2.6 in /website | pending |
| 787 | `5dc470b9109395b48b28cd9f830c7fdddcf43dc0` | 2026-05-10 | Merge pull request #478 from nitrobass24/dependabot/npm_and_yarn/website/develop/react-19.2.6 | pending |
| 788 | `ea84cc10e8c6ce79a3c35d13284103161baa3d7d` | 2026-05-10 | chore(deps): bump the angular group in /src/angular with 10 updates | pending |
| 789 | `b6c485bbe2971dc5fc7f9c5b3a9b8f2b36f0ed2d` | 2026-05-10 | Merge pull request #480 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-e0fbc3eb56 | pending |
| 790 | `0890c9e467e75f3b0f96d16578b3162d357bdeaf` | 2026-05-10 | chore(deps-dev): bump typescript-eslint in /src/angular | pending |
| 791 | `db5d8bfe7a77d89836532da1a93e014307542bb2` | 2026-05-10 | Merge pull request #481 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/typescript-eslint-8.59.2 | pending |
| 792 | `ce7d9a5ca8c9bf386ad0996dfb52ecc8b83d4460` | 2026-05-10 | chore(deps): bump react-dom from 19.2.5 to 19.2.6 in /website | pending |
| 793 | `48f73fa62b8a91b945404ef9d3f7b0d3e3dab7d3` | 2026-05-10 | Merge pull request #479 from nitrobass24/dependabot/npm_and_yarn/website/develop/react-dom-19.2.6 | pending |
| 794 | `89a30aaed2e472b6124ab22609c0701fd231a104` | 2026-05-13 | Update README.md | pending |
| 795 | `5e1c2948fdac78e8cd1eb103a9faa11531041d49` | 2026-05-16 | Set explicit User-Agent on Discord webhook payloads | pending |
| 796 | `c2d5c0d1fcc1b690134fd9f241ff215f573e1a91` | 2026-05-16 | Merge pull request #484 from nitrobass24/fix/discord-webhook-user-agent | pending |
| 797 | `7902319ed87afea4655598dabbd78910c25382cd` | 2026-05-16 | Release v0.18.1 - Fix Discord webhook 403, dependency bumps | pending |
| 798 | `6fc8342614feb29f90bd1cf2997972f4d6db469a` | 2026-05-16 | Release v0.18.1 - Sync develop → master | pending |
| 799 | `a4bfaeedf533af39626cc8e1656ecda902b1a115` | 2026-05-17 | chore(deps-dev): bump vitest from 4.1.5 to 4.1.6 in /src/angular | pending |
| 800 | `dc9fa065a461f4b0844b8310cad0b9d8ab611f78` | 2026-05-17 | Merge pull request #492 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/vitest-4.1.6 | pending |
| 801 | `5a241af76c33da954bb79d40114f2d84aae6e5ff` | 2026-05-17 | chore(deps-dev): bump angular-eslint in /src/angular | pending |
| 802 | `97166a3aa24d496fc81d0d3bf0cf165009343ccb` | 2026-05-17 | Merge pull request #491 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-eslint-21.4.0 | pending |
| 803 | `23919cf1872f23124e6b62c56d220b9005117241` | 2026-05-16 | Update CLAUDE.md for consistency and current state | pending |
| 804 | `227c61ed20185b37419970eae0484d3f8a362cc4` | 2026-05-17 | Merge pull request #487 from nitrobass24/chore/claude-md-consistency | pending |
| 805 | `5f46b89ad214f6401b6abe58451e5f763a0e2d48` | 2026-05-17 | chore(deps-dev): bump eslint from 10.3.0 to 10.4.0 in /src/angular | pending |
| 806 | `37cf36198726f3b9f076e2de8d668bd54f7c8f13` | 2026-05-17 | Merge pull request #490 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/eslint-10.4.0 | pending |
| 807 | `da763fe876aeea26a3e094616b5994aaedbac088` | 2026-05-17 | chore(deps-dev): bump typescript-eslint in /src/angular | pending |
| 808 | `e1ad2fa6b2eed90375865c394f3fa18ccbf2bc86` | 2026-05-17 | Merge pull request #489 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/typescript-eslint-8.59.3 | pending |
| 809 | `71eecbf65329e51d2475f308cd479b3468344398` | 2026-05-17 | chore(deps): bump the angular group across 1 directory with 10 updates | pending |
| 810 | `e6aa6ff8f2bd8e0cf40721492f6036ec3970690b` | 2026-05-17 | Merge pull request #488 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-b53251a3f7 | pending |
| 811 | `89711b48c6a38a9cbe702d7b0a1668aa1521a202` | 2026-05-17 | chore(deps): bump postcss from 8.5.6 to 8.5.14 in /website | pending |
| 812 | `8806d96e96a3b9fa44a9b83ad03d5ff15d0d91ec` | 2026-05-17 | Merge pull request #493 from nitrobass24/dependabot/npm_and_yarn/website/postcss-8.5.14 | pending |
| 813 | `b5aa1b15da2671a78d2d32074c28f1d7a38c3d98` | 2026-05-18 | Add notify_on_download_start option (#486) | pending |
| 814 | `eedf8631994dbf497584f91ce41923ab631562e0` | 2026-05-19 | Merge pull request #494 from nitrobass24/feat/notify-download-start | pending |
| 815 | `e20d02aca74eab906a2427b384bbf47fcce13845` | 2026-05-19 | Roll back config handler on persistence failure (#469.2) | pending |
| 816 | `8d107e8100e1599c01ee6c0e146965985c587cff` | 2026-05-19 | Skip rollback when concurrent update changed the value | pending |
| 817 | `155ac1b3f4a03955418ef409caed98b42113d3cd` | 2026-05-19 | Apply ruff format to test_config.py | pending |
| 818 | `2b907cbad366945c4613303e4e4a9fd74d7d25cc` | 2026-05-19 | Serialize config writers with a per-handler lock | pending |
| 819 | `eda0a369d03bbdcbca21a9c04fb1bbc06273cba2` | 2026-05-19 | Merge pull request #495 from nitrobass24/fix/config-handler-atomicity | pending |
| 820 | `4ea8f6c1e05409a869415c5e70b08f5e408284d0` | 2026-05-19 | Persist path_pairs before integrations on detach (#469.3) | pending |
| 821 | `f3b5faea1490966d900995588f4666db32ef523a` | 2026-05-19 | Tighten PR ref and assert 500 on persistence-failure test | pending |
| 822 | `8579b45d500eea163e9e2d0a90509cdcc0cca9e1` | 2026-05-19 | Merge pull request #496 from nitrobass24/fix/integrations-detach-ordering | pending |
| 823 | `cc669205852b50fdae1bcf5663962de7214fceb0` | 2026-05-20 | chore(deps): bump webpack-dev-server from 5.2.3 to 5.2.4 in /website | pending |
| 824 | `922d709afcf7cc2f3251860ffe475a6eb83b8e87` | 2026-05-20 | Merge pull request #498 from nitrobass24/dependabot/npm_and_yarn/website/webpack-dev-server-5.2.4 | pending |
| 825 | `e3de98bd37483fcf7819c9456703828ef341c6b9` | 2026-05-19 | Wrap auto-queue and path-pairs persistence in try/except (#469.1, #469.4) | pending |
| 826 | `b6180ab0ef85262b2ffb5c97638321c8ed55534a` | 2026-05-20 | Lock in in-memory-mutation-survives-failure contract in tests | pending |
| 827 | `6664729b57a671f4a17a5e6372051ec7e4396380` | 2026-05-21 | Merge pull request #497 from nitrobass24/fix/handler-persistence-try-except | pending |
| 828 | `8d17c39336e5aba5d8bee57cd950e9315204500d` | 2026-05-24 | chore(deps-dev): bump typescript-eslint in /src/angular | pending |
| 829 | `e4a669e590f554c6e9aa5ac379636758c6b91a4a` | 2026-05-24 | Merge pull request #503 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/typescript-eslint-8.59.4 | pending |
| 830 | `df18625e957502cc0a0de6c48e67207bcaf477e6` | 2026-05-23 | chore(deps): bump qs and express in /website | pending |
| 831 | `c419d8a713a44fbf552831d914d102b4e6681ec4` | 2026-05-24 | Merge pull request #499 from nitrobass24/dependabot/npm_and_yarn/website/multi-f792d6d6d9 | pending |
| 832 | `3b63816a0fda4b89e3743743e710ad9e2a28b3ec` | 2026-05-24 | chore(deps): bump the angular group in /src/angular with 10 updates | pending |
| 833 | `f579890359846825a1b85c950d960f5a457dd224` | 2026-05-24 | Merge pull request #501 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-3cae5b797e | pending |
| 834 | `edf200b0ba478286d4bd778e67959cf2d0d94677` | 2026-05-24 | chore(deps-dev): bump vitest from 4.1.6 to 4.1.7 in /src/angular | pending |
| 835 | `9b525d11e78bf29ea7c272c68d40109736744003` | 2026-05-24 | Merge pull request #502 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/vitest-4.1.7 | pending |
| 836 | `487cb4362a1a4200eb03882003356dccb39af09a` | 2026-05-24 | chore(deps): bump qs from 6.15.0 to 6.15.2 in /src/angular | pending |
| 837 | `6468e40f0bbb1733f474b8065a8bd59ddab3693d` | 2026-05-24 | Merge pull request #500 from nitrobass24/dependabot/npm_and_yarn/src/angular/qs-6.15.2 | pending |
| 838 | `0ec0839df5fc22cc4b080f4203a41304c2a7cac8` | 2026-05-31 | chore(deps-dev): bump typescript-eslint in /src/angular | pending |
| 839 | `b8f117dc989c3768b7b9f14dc75a0f45e5e638a0` | 2026-05-31 | chore(deps-dev): bump eslint from 10.4.0 to 10.4.1 in /src/angular | pending |
| 840 | `4d2623d0e4eda53f75714a10b43ca8be6d930daf` | 2026-05-31 | Merge pull request #506 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/eslint-10.4.1 | pending |
| 841 | `3ad81cbe3334b06ee579e03bf4a7f8245b012f66` | 2026-05-31 | chore(deps): bump the angular group in /src/angular with 10 updates | pending |
| 842 | `1b21a5078ec8e29337ea098b8bc22abd1f3ed53e` | 2026-05-31 | Merge pull request #504 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/angular-e80bc92022 | pending |
| 843 | `dcc850fe9d5fd2a5bba791f5931d79f4093758bd` | 2026-05-31 | Merge branch 'develop' into dependabot/npm_and_yarn/src/angular/develop/typescript-eslint-8.60.0 | pending |
| 844 | `add93c94c66a2ffeb4845606c38a65cdcf0f143d` | 2026-05-31 | Merge pull request #505 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/typescript-eslint-8.60.0 | pending |
| 845 | `57c904fe0917be1a23a70f9a1a9544be5ba9fbd8` | 2026-06-01 | Repo/CI hygiene + Docker hardened baseline (#527, #528) | pending |
| 846 | `84fdf2bdb20c1c874136e07e533bddf73d7a7946` | 2026-06-01 | Merge pull request #532 from nitrobass24/chore/audit-quick-wins-527-528 | pending |
| 847 | `747d2208f64fa79f8dda803c3aaa3776a03e3761` | 2026-06-01 | Tests: cover security-critical paths (#530) | pending |
| 848 | `cc31d942d88bb9ff3b662ba12b69794f06c434da` | 2026-06-01 | Fix: route integrations delete persistence through _persist_or_500 | pending |
| 849 | `50e8ff43e7b9638bb096b9bae2e15f3802420e57` | 2026-06-01 | CI: re-enable mock-based lftp/ssh/scanner unit tests; gate live suites (#529) | pending |
| 850 | `0b68d257c1ab656d27cf1585d1eae541f6fac187` | 2026-06-01 | docs: clarify working directory for the live-SSH pytest example (#529 review) | pending |
| 851 | `dc01b21b6b83546f6a59b76824bf8dae3404e4b0` | 2026-06-01 | Merge pull request #533 from nitrobass24/chore/ci-reenable-mock-tests-529 | pending |
| 852 | `d2388c32605c5d3cc016d6daf02f03ec7d15d1b7` | 2026-06-01 | Merge remote-tracking branch 'origin/develop' into test/cover-security-critical-paths-530 | pending |
| 853 | `af53304161db4728c7bffa1bc14bd70ce12fbf02` | 2026-06-01 | Merge pull request #534 from nitrobass24/test/cover-security-critical-paths-530 | pending |
| 854 | `6e98254079c1f1118eca3524202f733b84e3ebe3` | 2026-06-01 | Roll back config on any persist failure, not just OSError (#507) | pending |
| 855 | `8448ae67e2c82d59fad4fc04197fef944f05cf92` | 2026-06-01 | Harden periodic and shutdown persist against transient write failures (#512) | pending |
| 856 | `22c31fd3bb40ab62672ef75f48180e267cb55764` | 2026-06-01 | Fix job status parser robustness bugs (#517) | pending |
| 857 | `62215130aca1130859d583947a76a39c876d08ce` | 2026-06-01 | Fix tilde expansion in remote validate find/hash commands (#519) | pending |
| 858 | `61b330119b18c900004f8a4a1800a3a65d4cc0cb` | 2026-06-01 | Lock-guard AutoQueuePersist patterns and listener set (#509) | pending |
| 859 | `7ccae901214a6a2f888b08c67e071a2f58f3d339` | 2026-06-01 | Roll back auto-queue add/remove on persist failure (#518) | pending |
| 860 | `ea0cc8c29237a9f790331e5d5430f7146a866767` | 2026-06-01 | Surface staging->final move failures via result queue with retry (#510) | pending |
| 861 | `35bf824156d1bf2a390860581ae367920fd8f9f7` | 2026-06-01 | Isolate extract/validate worker faults from controller loop (#511) | pending |
| 862 | `24712b30658ecd19d33982506ab4e08fd176168d` | 2026-06-01 | Make Controller.exit() best-effort so hung lftp doesn't leak processes/FDs (#508) | pending |
| 863 | `7ff125c3628af073167b2c6272e1a6a90397e426` | 2026-06-01 | Bound controller action wait with 504 timeout (#526) | pending |
| 864 | `779a8982365de805b90194a0636db3714c697471` | 2026-06-02 | Make all Controller.exit() teardown calls best-effort (#508 review) | pending |
| 865 | `6bdf327431832e69858c63543f767e5cc0bae652` | 2026-06-02 | Surface a permanently-dead extract/validate worker at ERROR (#511 review) | pending |
| 866 | `66d19da36a1c763ff1423e774c9adebeb9e4471d` | 2026-06-02 | Strengthen auto-queue lock tests into deterministic guards (#509 review) | pending |
| 867 | `4761406b5a31efc8a7822b3c7ef6ed0905ff7c56` | 2026-06-02 | Extract and test shutdown classifier + final persist (#512 review) | pending |
| 868 | `01ae955955a590645460ea7def7a211552ae43a7` | 2026-06-02 | Document _remote_key nested-same-name limitation (#519 review) | pending |
| 869 | `6e00cadebcec88fce25faa10567466ccdd634cc8` | 2026-06-02 | Apply ruff format to #510/#519 test files | pending |
| 870 | `44d053d406fea1bed0f37ac09b5839b9c232a4c2` | 2026-06-02 | Add type annotations to teardown/worker-check helpers | pending |
| 871 | `360d7530aa845f223008b9ff455edecba87c360f` | 2026-06-02 | Drain new auto-queue patterns atomically per cycle (#537 review) | pending |
| 872 | `1aae69a9caa29c78481d99028edccc7b287c502f` | 2026-06-02 | Harden auto-queue handler: atomic check + broad rollback (#537 review) | pending |
| 873 | `97379ad5ef35ae3609283330f7dfdc7c272a373b` | 2026-06-02 | Reap finished moves and rescan only the owning pair (#537 review) | pending |
| 874 | `10dea66dbf47773844ed410a9aa913db491db595` | 2026-06-02 | Set __started before launching children so partial start cleans up (#537 review) | pending |
| 875 | `f2eb08c42a0f04d52c6ace2f9115ecefdeaee3c8` | 2026-06-02 | Start move process before publishing bookkeeping (#537 review) | pending |
| 876 | `0043d5e0e3167ebd381419d4c1a3627466d33075` | 2026-06-02 | Bound worker joins in Controller.exit() (#537 review) | pending |
| 877 | `bd61dd285b645d697a860d708d1912ceb9927138` | 2026-06-02 | Assert join in partial-start teardown test (#537 review) | pending |
| 878 | `416047c4bc98744646860fabc649eeee5a5562e2` | 2026-06-02 | Merge pull request #537 from nitrobass24/fix/backend-bugs-bucket-531 | pending |
| 879 | `7509a76491eba9012885a0a8fb6c4eec15db96eb` | 2026-06-02 | Fix auth-enabled init deadlock: bootstrap config off the stream + persist api key (#514) | pending |
| 880 | `4f8b5a570e7d8a5e6a5b1ef8b3065d66ff2cefff` | 2026-06-02 | Guard SSE handler JSON.parse with try/catch (#516) | pending |
| 881 | `f9cf2cceb4ec6f5140386d48c1c251f37524cf67` | 2026-06-02 | Surface single-file action errors and recover stuck rows (#513) | pending |
| 882 | `e6788e83e5bd922428cc331f6658d0a19022ab23` | 2026-06-02 | Require two-click confirm for bulk Delete Local/Remote (#515) | pending |
| 883 | `faa9a077b2ea5cd65eb135da7a6a0e462c529125` | 2026-06-02 | Reuse the shared REDACTED_SENTINEL constant (#514 review) | pending |
| 884 | `db22d895518bb63785e8b464973ea8e8b65f22ac` | 2026-06-02 | Complete #516: autoqueue parse guard, log-history failure state, path-pair toggle errors | pending |
| 885 | `81e358bdcb301941473bcc0d66f664dac3cdff36` | 2026-06-02 | Don't persist the api_key client-side (#514 / CodeQL) | pending |
| 886 | `257d4806c20819471e743933a5e9f02791e311c3` | 2026-06-02 | Use a distinct class for the log-history error banner (#516 / E2E) | pending |
| 887 | `d9598496e1fcc96d0ca16ce016bc88adbbdd8bfb` | 2026-06-02 | Address CodeRabbit review on #538 | pending |
| 888 | `1f0db282c7a2c8ea717977cbaaa96eec81e54f3c` | 2026-06-02 | Merge pull request #538 from nitrobass24/fix/frontend-bugs-bucket-531 | pending |
| 889 | `a03d21ac988198d227dc64585484f5e06a223b72` | 2026-06-02 | Perf: O(1) BFS frontiers + skip child-list copy in model_builder (#520) | pending |
| 890 | `906df1bf625d76e7b48b373c11480cbe94a56de8` | 2026-06-02 | Perf: coalesce SSE view rebuilds, memoize filter, single-pass bulk remove (#521) | pending |
| 891 | `5f1392e81f715d28ec3a123e16c0531200edf3b9` | 2026-06-02 | Cap live-log buffer and add stable trackBy on logs page (#522) | pending |
| 892 | `8bec37d3676b2458700770af56ba6d39556588e0` | 2026-06-02 | Fix #522 trackBy collision with per-object seq keys (review) | pending |
| 893 | `80d428d1645f467a725ed0fce45aeafed17dd0ae` | 2026-06-02 | Merge branch 'fix/frontend-bugs-bucket-531' into perf/performance-bucket-531 | pending |
| 894 | `41797ca09e1684f4eaa4293de0d2ad813c0b9674` | 2026-06-02 | Address CodeRabbit review on #540 | pending |
| 895 | `ff4e4b50962cd045ae7d16140cc9383c7060e53a` | 2026-06-02 | Merge pull request #540 from nitrobass24/perf/performance-bucket-531 | pending |
| 896 | `f2c096510596c1228614e81941662c53a467d91b` | 2026-06-07 | chore(deps-dev): bump typescript-eslint in /src/angular | pending |
| 897 | `b6f25527be99f56878c6428e742137e27b83ef41` | 2026-06-08 | Merge pull request #552 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/typescript-eslint-8.60.1 | pending |
| 898 | `3c03a85972d4b639d25fe99f82a8b928d04f6403` | 2026-06-07 | chore(deps): bump react from 19.2.6 to 19.2.7 in /website | pending |
| 899 | `59e9af2cadd85ddbe65ce0c1e30efdda6e19dd3d` | 2026-06-08 | Merge pull request #549 from nitrobass24/dependabot/npm_and_yarn/website/develop/react-19.2.7 | pending |
| 900 | `f01c87c46ade6ff9aac591de56dc5313851e7379` | 2026-06-04 | chore(deps): bump webob from 1.8.9 to 1.8.10 in /src/python | pending |
| 901 | `976cdc071dd3c70649873be1e18534b301e99b8e` | 2026-06-08 | Merge pull request #545 from nitrobass24/dependabot/uv/src/python/webob-1.8.10 | pending |
| 902 | `1a4fccef060586e4ffb5091804b069b20d545c04` | 2026-06-02 | Refactor: decompose LftpJobStatusParser.__parse_jobs (#523) | pending |
| 903 | `318608fa952e36f9552854d23f8549545faa5834` | 2026-06-02 | Refactor: extract capabilities module + selection service from ViewFileService (#524) | pending |
| 904 | `de96bcc886b9d09036a41443cba0a6fb6eb94876` | 2026-06-02 | Refactor: decouple controller commands/persist-sync; type config options (#525) | pending |
| 905 | `293936af266ecea788ea34e89166bf56c58c1855` | 2026-06-02 | Type-annotate job_status_parser helpers for strict pyright (#523) | pending |
| 906 | `542ab511ecbc080fa6df47599e064da746b185d8` | 2026-06-02 | Make config types actually typo-safe; add missing remote_python_path (#525 review) | pending |
| 907 | `00046d8ce5ef2449d0c8afceacf3e013fd9664b9` | 2026-06-02 | Apply ruff format to command_pipeline.py (#525) | pending |
| 908 | `22eccda2083086d8216deddca5f53760a1c7233c` | 2026-06-08 | Merge pull request #543 from nitrobass24/refactor/refactor-bucket-531 | pending |
| 909 | `f59386d3dd10269cb6b93e96df813c306773fcfc` | 2026-06-08 | chore(deps): bump astral-sh/setup-uv from 7.6.0 to 8.2.0 | pending |
| 910 | `45ef80a584323307f53c7d0e282d91119b5405c8` | 2026-06-08 | Merge pull request #547 from nitrobass24/dependabot/github_actions/develop/astral-sh/setup-uv-8.2.0 | pending |
| 911 | `2ad53f67ff8a16957725569722d222d58bfa904d` | 2026-06-08 | chore(deps): bump hono from 4.12.18 to 4.12.24 in /src/angular | pending |
| 912 | `965d57b4427cd1e324cc8cd78d370bd38e27c893` | 2026-06-08 | Merge pull request #554 from nitrobass24/dependabot/npm_and_yarn/src/angular/hono-4.12.24 | pending |
| 913 | `3add27772ce8a9f2c63bde7f1eff609934756a1b` | 2026-06-08 | chore(deps-dev): bump vitest from 4.1.7 to 4.1.8 in /src/angular | pending |
| 914 | `4b32fde8f12bb4288a056442a973a156dc1de335` | 2026-06-08 | Merge pull request #553 from nitrobass24/dependabot/npm_and_yarn/src/angular/develop/vitest-4.1.8 | pending |
| 915 | `b1879fceb963b18f3fa975e640c899c50755c361` | 2026-06-08 | chore(deps): bump react-dom from 19.2.6 to 19.2.7 in /website | pending |
| 916 | `f139c481cb02de941a418bda9380753e33a3d72d` | 2026-06-08 | Merge pull request #548 from nitrobass24/dependabot/npm_and_yarn/website/develop/react-dom-19.2.7 | pending |
| 917 | `471fbf256857b3fd8e3545ab65ca0cc0b34d5588` | 2026-06-08 | refactor(angular): migrate ConfigService/AutoQueueService to tap-based mutating-service contract (#542) | pending |
| 918 | `028d681455cbb38564d8c21e06e098b2e6250453` | 2026-06-08 | refactor(angular): extract ViewFileCommandService from ViewFileService (#541) | pending |
| 919 | `8b542599ba84f420779d90a52913cb9203a56ba0` | 2026-06-08 | perf(angular): virtualize logs list + batch change detection (#539) | pending |
| 920 | `2377af5a96a3fdafb55c14ecade33d2271e2b8d1` | 2026-06-08 | refactor(angular): return inner observable from ViewFileCommandService.createAction (#541 review) | pending |
| 921 | `db0a6e7b445dc8fecbdcbcdc1cbc720344666197` | 2026-06-08 | feat(angular): surface backend move_failed state in the UI (#536 FE follow-up) | pending |
| 922 | `185fcf4de34a9825df8d19058b5b7d1ad9902573` | 2026-06-08 | Merge pull request #557 from nitrobass24/feat/audit-frontend-followups | pending |
| 923 | `238fef1a3b23889a633391243f813f0a00649cf6` | 2026-06-08 | fix(lftp): peek before consuming pget/filename chunk-data line (#555) | pending |
| 924 | `653f36108071fbf3a2601a92127779cb1ea23da7` | 2026-06-08 | fix(controller): recreate dead workers (#535) + surface failed moves as MOVE_FAILED with in-session retry (#536) | pending |
| 925 | `506ed93d931511fcb5435509698d67161486df00` | 2026-06-08 | Merge pull request #558 from nitrobass24/fix/audit-backend-followups | pending |
| 926 | `bd09036f022e5ea90d0d809fcf05a83f528e1a60` | 2026-06-08 | docs(changelog): document the codebase-audit batch (#531) in [Unreleased] | pending |
| 927 | `72523aa54aeeef442e573121cffed2de3985257c` | 2026-06-08 | Merge pull request #559 from nitrobass24/docs/update-changelog | pending |
| 928 | `6659c6c185142d3f84f5d34eac86806fbb49fb33` | 2026-06-08 | feat(angular): upgrade Angular 21 to 22 (#556) | pending |
| 929 | `7d76326265eef5ebcbfa185cba450e844262dfa6` | 2026-06-08 | docs(changelog): note the Angular 22 upgrade under [Unreleased] (#556) | pending |
| 930 | `e38b7a210714b85d21d278c02a261988e99aa5b8` | 2026-06-08 | refactor(angular): drop temporary v22 optional-chaining migration shims (#556) | pending |
| 931 | `e14099c8617fcf0723d16a1500a87eeff46405bf` | 2026-06-08 | chore(deps-dev): bump typescript-eslint 8.60.1 -> 8.61.0 (#556) | pending |
| 932 | `d492d9f96ebc70e8049b8b38cede4a1afdc1c4f3` | 2026-06-08 | Merge pull request #560 from nitrobass24/feat/angular-22-upgrade | pending |
| 933 | `6524980f7e74d645ce78230d62cbf37534532487` | 2026-06-08 | chore: remove MODERNIZATION_PLAN.md (no longer maintained) | pending |
| 934 | `92e2449f759e6620f67ed49751ac46918984f952` | 2026-06-08 | Merge pull request #561 from nitrobass24/chore/remove-modernization-plan | pending |
| 935 | `bba846d54fa2966924cba95ab234bfd8291c75b3` | 2026-06-09 | feat(lftp): add optional FTPS transfer protocol | pending |
| 936 | `bdb6b839c2efadeb06872eb036eee18924c5f77e` | 2026-06-09 | feat(settings): grey out FTP-only options when protocol is SFTP | pending |
| 937 | `4b7e1c1b96dd905747e9c13a423276e76ea823c1` | 2026-06-09 | style: apply ruff format to FTPS files | pending |
| 938 | `d015e05fb116803e7fa64636978f4bf262a72bd0` | 2026-06-09 | fix(lftp): redact credentials in error paths, validate protocol | pending |
| 939 | `0fe975e47cadfb11851a3d288393d1718d3e15b9` | 2026-06-08 | fix(ssh): use full timeout for password prompt, disable GSSAPI auth | pending |
| 940 | `af3971f39ce86c6a3298c7b46b94f62fa8184290` | 2026-06-09 | Merge pull request #562 from nitrobass24/fix/sshcp-password-prompt-timeout | pending |
| 941 | `8caada62bec92b2dea90b017c1c6351ff9f95c9e` | 2026-06-09 | Merge branch 'develop' into feat/ftps-transfer-protocol | pending |
| 942 | `07154a4ed99aac31fb88b3e99068acac66a4b203` | 2026-06-09 | Merge pull request #564 from nitrobass24/feat/ftps-transfer-protocol | pending |
| 943 | `e13eaf3bb79424403966821126ff8fe6aaeec875` | 2026-06-09 | chore(deps): bump shell-quote from 1.8.3 to 1.8.4 in /website | pending |
| 944 | `e66d18e33fc8fd306a9043f5f48ea90fc3850e67` | 2026-06-09 | Merge pull request #565 from nitrobass24/dependabot/npm_and_yarn/website/shell-quote-1.8.4 | pending |
| 945 | `70e60d4bc2cdc4fc832bfbe84d92990313c5f7d3` | 2026-06-09 | fix(ssh): remove GSSAPIAuthentication=no — unsupported on Alpine openssh | pending |
| 946 | `4c3bdca4ce72fa04f24f1eb26906ce80c08f2f12` | 2026-06-09 | Merge pull request #566 from nitrobass24/fix/sshcp-gssapi-unsupported-alpine | pending |
| 947 | `06bc51029b2758d6243ae229c9ab7fbc9e3f8c66` | 2026-06-09 | fix(move): defer staging->final move while lftp temp files present | pending |
| 948 | `c374f34249ab9c114c82819d7c678047142087fd` | 2026-06-09 | feat(settings): move FTPS options into a dedicated section | pending |
| 949 | `3257d9dc6bb9343f6bc5b8aaf49e50efec57c23b` | 2026-06-09 | Merge pull request #567 from nitrobass24/fix/move-process-lftp-temp-race | pending |
| 950 | `a6a14e657343f42aeefa6942e9ce8022c7de80bf` | 2026-06-09 | fix(lftp): stop overriding Connections settings under FTPS | pending |
| 951 | `1e86386a6a4e63fa68a909ff04b8d54e34f8938d` | 2026-06-10 | Merge pull request #568 from nitrobass24/fix/ftps-respect-connections-settings | pending |
| 952 | `a041021a64335c8e9fe330d9fe6c6112a5140aa5` | 2026-06-10 | feat(settings): rename FTPS section to "Transfer Protocol" | pending |
| 953 | `60eb99b99adef90a3f21a005d586130ec62d0b1b` | 2026-06-10 | Merge pull request #569 from nitrobass24/feat/settings-rename-ftps-section | pending |
| 954 | `1d800aaa7c8e6815053f8ec53cbbc88c5bba7bb0` | 2026-06-10 | Release v1.0.0 - First stable release | pending |
| 955 | `686a1e3fbac7c49e32131bc8c3a24f69215482d6` | 2026-06-10 | fix(angular): use action-agnostic log in ViewFileCommandService.createAction | pending |
| 956 | `38d6ef22d36b6a75c164bc754bac9cd2842e8722` | 2026-06-10 | Release v1.0.0 - Sync develop → master | pending |
