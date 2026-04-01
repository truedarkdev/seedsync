# rapidcopy - Frozen Refresh Ledger

Pass date: 2026-04-01
Source branch: `rapidcopy/master`
Frozen range: `1b96fb80938d398d7fca701771f11c13df5a0bc7` through `dc9c68c37c43eba7487654dacf7c7b08f64eb12a`

This ledger records the frozen rapidcopy refresh range in strict oldest-to-newest order. Every row starts at `not yet reviewed` and should be dispositioned in sequence before the tracker tip is advanced.

Resume note: continue from the first `not yet reviewed` row in this ledger, oldest to newest, until the full frozen range is dispositioned.

| Order | Commit | Subject | Initial review state |
| --- | --- | --- | --- |
| 1 | `1b96fb80938d398d7fca701771f11c13df5a0bc7` | `fix: fill header checkbox column to full row height` | `not yet reviewed` |
| 2 | `8c2290bc856e76ac35503f8d96290782a397de72` | `fix: resolve WebApp.stop() AttributeError from bottle.py plugin conflict` | `not yet reviewed` |
| 3 | `b7fc141f221db198acf538b5b6d073c4676e48bf` | `fix: use local staging path for multi-path downloads to prevent NFS corruption` | `not yet reviewed` |
| 4 | `31f2e6c6fead74d1b96d72a4ad8c18c2d49159d3` | `Merge master into fix/staging-path-nfs-corruption` | `not yet reviewed` |
| 5 | `fd8a32c0e8346a7300320dac1924fa2134fd221e` | `Merge pull request #10 from rccypher/fix/staging-path-nfs-corruption` | `not yet reviewed` |
| 6 | `cb20dc899ccabce453c2ae4d44e9e0153f7a74ea` | `ci: fix workflow_dispatch by replacing secrets check in if conditions` | `not yet reviewed` |
| 7 | `3dcd35aecaad965ba3f8031f2b0371e7a8549d9a` | `Merge pull request #6 from ppastur/fix/css-header-checkbox-gap` | `not yet reviewed` |
| 8 | `b6c093aa6918e21ff1081c4084c791a22a29cb2c` | `Merge pull request #7 from ppastur/fix/webapp-stop-bottle-conflict` | `not yet reviewed` |
| 9 | `24a12d3c5a405175d93706c53e05a09a614ead70` | `ci: fix pipeline failures - docker compose V2 and missing package-lock` | `not yet reviewed` |
| 10 | `9e75538285f93ce7d80e7ee0af9a8a3d76e8f021` | `ci: fix Angular test Dockerfile - update removed config file references` | `not yet reviewed` |
| 11 | `acf1a0c64eb4becb14ffec3104613238bd8cbbd5` | `fix: resolve 67 Python test failures` | `not yet reviewed` |
| 12 | `c766bd96366a1da7ffbf8f65df05a6bd904c3106` | `fix: resolve remaining Python test failures` | `not yet reviewed` |
| 13 | `dc9c68c37c43eba7487654dacf7c7b08f64eb12a` | `fix: resolve remaining 20 Python test failures` | `not yet reviewed` |
