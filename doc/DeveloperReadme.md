[TOC]



# Environment Setup

## Install dependencies
1. Install [Node.js 24.x](https://nodejs.org/en/download) (comes with npm). The Angular toolchain here is validated against the current Node `v24.0.0` / npm `11.3.0` baseline, not the old Node 12-era setup.

2. Install [Poetry](https://python-poetry.org/docs/#installation):

3. Install docker and docker-compose:
https://docs.docker.com/engine/installation/linux/docker-ce/ubuntu/#install-docker-ce
https://docs.docker.com/compose/install/

4. Install docker buildx
   
    1. https://github.com/docker/buildx/issues/132#issuecomment-582218096
    2. https://github.com/docker/buildx/issues/132#issuecomment-636041307
    
5. Build dependencies

   ```bash
   sudo apt-get install -y jq
   ```

6. Install the rest:
   ```bash
   sudo apt-get install -y lftp perl libstring-crc32-perl python3-dev p7zip-full p7zip-rar rar
   ```

## Fetch code
```bash
git clone https://github.com/truedarkdev/seedsync.git
cd seedsync
```

## Setup Poetry project
```bash
cd src/python
poetry install
```

## Setup angular node modules
```bash
cd src/angular
npm ci --legacy-peer-deps
```

## Setup end-to-end tests node modules
```bash
cd src/e2e
npm install
```

# Build

1. Set up docker buildx for multi-arch builds

   ```bash
   docker buildx create --name mybuilder --driver docker-container --driver-opt image=moby/buildkit:master,network=host
   docker buildx use mybuilder
   docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
   docker buildx inspect --bootstrap
   # Make sure the following architectures are listed: linux/amd64, linux/arm64, linux/arm/v7
   ```

2. Multi-arch docker images can only be stored in a registry.
   Create local docker registry to store multi-arch images

   ```bash
   docker run -d -p 5000:5000 --restart=always --name registry registry:2
   ```

3. Run these commands inside the root directory.
   ```bash
   make clean
   make
   make verify-deb-glibc
   ```
   `make docker-image` now builds the Angular web assets and `scanfs` binary
   inside the final image build; it no longer depends on separately pushed
   staged export images.
   `make verify-deb-glibc` extracts the built `.deb` and verifies the bundled
   `seedsync` and `scanfs` binaries stay within the active Ubuntu 20.04+
   compatibility floor (default max required GLIBC symbol version: `2.31`).

4. The .deb package will be generated inside `build` directory.
   The docker image will be pushed to the local registry as `seedsync:latest`. See if using:

   ```bash
   curl -X GET http://localhost:5000/v2/_catalog
   ```
   
   To inspect the architectures of image:
   
   ```bash
   docker buildx imagetools inspect localhost:5000/seedsync:latest
   ```
   
   To use a different registry during the build process, use `STAGING_REGISTRY=`.
   For example:
   
   ```bash
   make STAGING_REGISTRY=another-registry:5000
   ```
   
   To build a tag other than `latest`, use `STAGING_VERSION=`.
   For example:
   
   ```bash
   make STAGING_VERSION=0.0.1
   ```
   
   



## Python Dev Build and Run

### Build scanfs

```bash
make scanfs
```

`SCANFS_PLATFORM` selects the architecture of the generated remote `scanfs`
binary. It defaults to `linux/amd64` because `scanfs` runs on the remote
seedbox, not the local build host. Override it for non-x86 remote servers, for
example:

```bash
make SCANFS_PLATFORM=linux/arm64 scanfs
make SCANFS_PLATFORM=linux/arm64 deb
make SCANFS_PLATFORM=linux/arm64 docker-image
```

If your build host is not already prepared for cross-architecture Docker
builds, `make scanfs` and `make deb` may need Docker BuildKit plus binfmt/QEMU
support first. See the multi-arch setup note in the Build section above.

### Run python

```bash
cd src/python
mkdir -p build/config
poetry run python seedsync.py -c build/config --html ../angular/dist --scanfs build/scanfs
```



## Angular Dev Build and Run

```bash
cd src/angular
node_modules/@angular/cli/bin/ng build
node_modules/@angular/cli/bin/ng serve
```

Dev build will be served at [http://localhost:4200](http://localhost:4200)



## Documentation

### Preview documentation in browser

```bash
cd src/python
poetry run mkdocs serve
```

Preview will be served at  [http://localhost:8000](http://localhost:8000)

### Deploy documentation

```bash
poetry run mkdocs gh-deploy
git push origin gh-pages
```



# Setup dev environment

## PyCharm
1. Set project root to top-level `seedsync` directory

2. Switch interpreter to virtualenv

3. Mark src/python as 'Sources Root'

4. Add run configuration

   | Config      | Value                                                        |
   | ----------- | ------------------------------------------------------------ |
   | Name        | seedsync                                                     |
   | Script path | seedsync.py                                                  |
   | Parameters  | -c ./build/config --html ../angular/dist --scanfs ./build/scanfs |

   

# Run tests

For the current lane inventory, minimum-evidence ladder, per-task update
rules, durable artifact guidance, freshness rules, and known gaps, see
[doc/testing-confidence.md](testing-confidence.md).

## Manual

### Python Unit Tests

Create a new user account for python tests, and add the current user to its authorized keys.
Also add the test account to the current user group so it may access any files created by the current user.
Note: the current user must have SSH keys already generated.

```bash
sudo adduser -q --disabled-password --disabled-login --gecos 'seedsynctest' seedsynctest
sudo bash -c "echo seedsynctest:seedsyncpass | chpasswd"
sudo -u seedsynctest mkdir /home/seedsynctest/.ssh
sudo -u seedsynctest chmod 700 /home/seedsynctest/.ssh
cat ~/.ssh/id_rsa.pub | sudo -u seedsynctest tee /home/seedsynctest/.ssh/authorized_keys
sudo -u seedsynctest chmod 664 /home/seedsynctest/.ssh/authorized_keys
sudo usermod -a -G $USER seedsynctest
```

Run from PyCharm

OR

Run from terminal

```bash
cd src/python
poetry run pytest
```

If you are running the live SSH/LFTP/controller suites, keep `src/python` as
the working directory and export `SEEDSYNC_LIVE_SSH_TESTS=1` first. The
default local pytest lane should leave that variable unset so the mock/unit
SSH and LFTP coverage still runs.

If you need junit XML for a local run, create the repo-root `tmp/pytest/`
tree first and write the file there. From `src/python`, a typical path looks
like:

```bash
mkdir -p ../../tmp/pytest
poetry run pytest --junitxml=../../tmp/pytest/python-unit.xml
```

### WSL bounded live Python lane

To run the meaningful live SSH/LFTP/controller tests and archive-backed tests,
use the tracked WSL lane from the repository root:

```bash
make run-tests-python-wsl
```

The lane starts or reuses the named `seedsync_test_e2e_remote` Docker service
with `SEEDSYNC_REMOTE_FILES_DIR` set explicitly, waits for `127.0.0.1:1234`,
checks the fixture `remoteuser`/`remotepass` login on port `1234` plus the
local `seedsynctest` SSH login on port `22`, then validates Poetry/pytest,
LFTP, Perl with its `String::CRC32` module (required by LFTP's
`verify-file` helper), `7z`, its RAR codec, and `rar` before enabling
`SEEDSYNC_LIVE_SSH_TESTS=1`. Missing test tools fail fast with an install
command (`perl`, `libstring-crc32-perl`, `p7zip-full`, `p7zip-rar`, and
`rar`); `--provision-test-tools` is an explicit opt-in to apt-based
provisioning (`--provision-archive-tools` remains a deprecated alias).
Provisioning is automation-safe: the lane uses direct apt as
root or noninteractive `sudo -n`; it never prompts for a sudo password. If
that is unavailable, use the reported `wsl.exe -u root -- bash -lc ...`
command and rerun preflight. Compose auth tokens are accepted from the
environment or generated ephemerally when absent; token values are never
printed or written to artifacts. Supplied tokens are scoped to Compose/auth
bootstrap and are cleared before Python collection in every invocation mode;
they are not used as
general pytest credentials. The runner requests mode `0700` for run
directories and `0600` for artifact files, on filesystems that honor POSIX
modes; Windows-backed WSL `/mnt/c` DrvFS commonly reports or applies
mount-derived permissions instead. Use Windows ACLs or a Linux-owned checkout
or artifact location when POSIX privacy guarantees are required. The runner
restores the caller's umask before pytest so fixture-created files remain
accessible to the test SSH user. No secret values are written to artifacts.
Pytest nodeids are collected once and run in batches of about
30 tests (not files), with collection and execution counts recorded. The lane
writes JUnit, logs, `progress.tsv`, `progress.txt`, `summary.txt`,
`failures.txt`, and the selected manifest under
`tmp/pytest/runs/<timestamp>/`.

For a worker self-check, run only the preflight or one selected nodeid:

```bash
make run-tests-python-wsl EXTRA_ARGS="--preflight-only"
bash src/docker/test/python/run_wsl_lane.sh --live-ssh --limit 1
bash src/docker/test/python/run_wsl_lane.sh --self-test
```

Without `--provision-test-tools`, preflight reports all host-side missing
prerequisites in one pass and never installs Python or test tooling implicitly.
The deterministic self-test
covers batch selection, the Perl CRC32 dependency decision, and treats a
simulated unexpected pytest exit as a harness error.

These checks are implementation feedback, not verification. The complete
`make run-tests-python-wsl` command is the verifier/final validation lane; do
not claim the WSL lane as final evidence unless every batch completes with a
zero-failure summary. The intentional
`test_download_with_excessive_connections` stress test remains skipped by its
existing marker.

### Linux/WSL SSH and Archive Baseline

Before chasing failures in the local Linux/WSL live SSH/LFTP, archive-backed,
or reusable remote fixture lanes, run the shared preflight helper from the
repo root:

```bash
make preflight-linux-wsl
# or, if you want the helper directly
bash src/docker/test/check_linux_wsl_baseline.sh
```

The helper defaults to the full baseline, but the direct script also accepts
lane flags when you only want one slice:

```bash
bash src/docker/test/check_linux_wsl_baseline.sh --live-ssh-lftp
bash src/docker/test/check_linux_wsl_baseline.sh --archive-backed
bash src/docker/test/check_linux_wsl_baseline.sh --reusable-remote-fixture
```

The helper checks host-side prerequisites in separate buckets:

* Live SSH/LFTP lane:
  * the current repo-supported Python range (`>=3.11,<3.13`)
  * the OpenSSH client
  * `lftp`
  * a non-interactive SSH login-style probe against `seedsynctest@127.0.0.1:22`

* Archive-backed lane:
  * `rar`
  * `unrar`

That SSH probe proves the live lane can complete a command over SSH without
interactive prompts while still accepting new host keys. It does not start or
validate the reusable remote fixture, and it does not need archive tooling.

Remote-fixture bootstrap expectations are separate:

* `127.0.0.1:1234` is the Compose-managed reusable remote fixture lane
* `--reusable-remote-fixture` only checks that the host-side endpoint is
  reachable; it does not bootstrap the fixture for you
* start it with `make run-remote-server` before you expect that endpoint to be
  reachable

Lane-specific notes:

* Python integration tests use the localhost SSH target on port 22. They need
  the live SSH/LFTP lane prerequisites above, but they do not need the archive
  tooling unless the test path actually creates or extracts rar-backed
  archives.

  The current host-side command remains:

  ```bash
  cd src/python
  poetry run pytest -p no:cacheprovider
  ```

* The reusable remote SSH fixture for the e2e lane is started with:

  ```bash
  make run-remote-server
  ```

  That service publishes SSH on port 1234. The preflight helper keeps the
  1234 reachability check separate from the host tool and SSH login probe so
  the host prerequisites and fixture bootstrap expectations stay distinct.

* Archive-backed extraction coverage depends on `rar` and `unrar` being
  installed on the Linux/WSL host. The extractor shells out to archive tooling,
  so a missing binary is an environment problem rather than a repo regression.
  The live SSH/LFTP lane does not depend on those archive tools.

See [src/e2e/README.md](../src/e2e/README.md) for the e2e lane-specific
command summary.

### Native Windows Backend Tests

Use this lane for host-native backend and unit tests on Windows. The repo
currently supports Python `>=3.11,<3.13`, so Python 3.11 or 3.12 is the
supported native Windows range here. Python 3.13 is the common drift case on
this machine and is outside the supported backend-test lane.

From a Windows shell:

```powershell
Set-Location C:\Git\seedsync\src\python
poetry install
poetry run pytest -p no:cacheprovider
```

If you need junit XML on the native Windows lane, create the repo-root
`tmp\pytest\` tree first and write the file there so the artifacts stay in
the gitignored temp tree.

## Annual Major-Version/Platform Upgrade Readiness

SeedSync uses an annual readiness push for major platform, framework, and
toolchain upgrades, beginning with the 2027 review tracked as
`platform-upgrade-readiness-2027`. The 2026 baseline remains the Debian-based
topology and Python `>=3.11,<3.13` support range described above.

Each readiness push inventories Python, Angular and other frameworks, Docker
base images, packaging/build tools, GitHub Actions, and material library
families. Candidates are assessed for upstream support and EOL dates, security
exposure, and ecosystem compatibility, then handled as separate scoped
migrations with their own validation. This is a readiness and planning cycle,
not an automatic mass upgrade; support claims must not change until the
corresponding migration is completed and validated.

Stable releases and long-term support take priority over adopting the newest
available version. The target is selected from versions available at review
time using local support, ecosystem, migration-risk, and maintenance evidence;
source-fork target choices are not inherited.

Routine safe direct dependency updates continue weekly through Dependabot.
Urgent security, EOL, or compatibility blockers may trigger an earlier,
dedicated upgrade outside the annual push.

If you already have GNU Make available on the Windows host, the repository
also provides a convenience wrapper:

```powershell
Set-Location C:\Git\seedsync
make run-tests-python-native
```

Keep using the Docker-based suite below for Linux-dependent integration
coverage. This native lane is only the supported host path for backend/unit
tests that can run on Windows.

### Angular Unit Tests

On this machine, the host Angular/Karma path has now been exercised
successfully on Node 24. The supported frontend closure lane is still the
Dockerized Angular/Karma path on Angular 21 / Node 24 / RxJS 7.

The validated local command path on Node 24 is:

```powershell
Set-Location C:\Git\seedsync\src\angular
$env:CHROME_BIN = "C:\Program Files\Google\Chrome\Application\chrome.exe"
npm ci --legacy-peer-deps
npm test -- --watch=false --single-run --browsers=ChromeHeadless
```

Set `CHROME_BIN` explicitly when Chrome is installed but not on `PATH`. The
headless launcher configured in `karma.conf.js` works without a GUI Chrome
session. This host run exercised the full Angular/Karma suite because the
attempted `--include` selection was ignored by the npm test path
(`npm warn invalid config include=...`). Use host proof as comparison evidence;
if the behavior is runtime-visible in the live app shell, browser, or
Docker-served runtime, pair it with live UI/runtime proof because host smoke
alone is not enough. If you want an interactive browser run for local
debugging, override the browser explicitly, for example:

```bash
npm test -- --browsers Chrome
```

### E2E Tests

[See here](../src/e2e/README.md)

## Docker-based Test Suite

```bash
# Python tests
make run-tests-python

# Angular tests
make run-tests-angular

# E2E Tests
#
# The e2e lane needs three non-secret fixture values. The examples below use
# dummy local values so the documented entrypoints work as written.
# Docker image (arch=amd64,arm64,arm/v7)
SEEDSYNC_E2E_API_TOKEN=seedsync-e2e-api-token \
SEEDSYNC_E2E_BROWSER_API_TOKEN=seedsync-e2e-browser-api-token \
SEEDSYNC_E2E_BROWSER_SESSION_SECRET=seedsync-e2e-browser-session-secret \
make run-tests-e2e STAGING_VERSION=latest SEEDSYNC_ARCH=<arch code>
# Debian package (active DEB e2e lane: `ubu2004`, using the Ubuntu 20.04 lane)
SEEDSYNC_E2E_API_TOKEN=seedsync-e2e-api-token \
SEEDSYNC_E2E_BROWSER_API_TOKEN=seedsync-e2e-browser-api-token \
SEEDSYNC_E2E_BROWSER_SESSION_SECRET=seedsync-e2e-browser-session-secret \
make run-tests-e2e SEEDSYNC_DEB=`readlink -f build/*.deb` SEEDSYNC_OS=ubu2004
```

By default images are pulled from `localhost:5000`. To test image from a registry other than the local, use `STAGING_REGISTRY=`.
For example:

```bash
SEEDSYNC_E2E_API_TOKEN=seedsync-e2e-api-token \
SEEDSYNC_E2E_BROWSER_API_TOKEN=seedsync-e2e-browser-api-token \
SEEDSYNC_E2E_BROWSER_SESSION_SECRET=seedsync-e2e-browser-session-secret \
make run-tests-e2e STAGING_VERSION=latest SEEDSYNC_ARCH=arm64 STAGING_REGISTRY=ghcr.io/truedarkdev
```

`SEEDSYNC_ARCH` is resolved through `src/docker/test/resolve_platform.sh`, so the
active E2E architecture codes remain `amd64`, `arm64`, and `arm/v7` while the
Docker platform string stays consistent across `make`, Compose, and CI.



# Release

## Continuous Integration

GitHub Actions builds and tests tagged releases, then promotes the exact tested
multi-architecture image to the versioned and `latest` Docker Hub tags. Deb
packages remain build- and E2E-tested but are not currently published.

1. Do all of these in one change
   1. Version update in `src/angular/package.json`
   2. Version update and changelog in `src/debian/changelog`.
      Use command `LANG=C date -R` to get the date.
   3. Update `src/e2e/tests/about.page.spec.ts`
   4. Update Copyright date in `about-page.component.html`
2. Tag the commit as vX.X.X
3. Push tag to Github



## Manual Method

This manual method is deprecated in favour of the Github Actions based CI.

### Checklist

1. Do all of these in one change
    1. Version update in `src/angular/package.json`
    2. Version update and changelog in `src/debian/changelog`.
       Use command `LANG=C date -R` to get the date.
    3. Update `src/e2e/tests/about.page.spec.ts`
    4. Update Copyright date in `about-page.component.html`
2. Tag the commit as vX.X.X
3. Deploy documentation to github
4. make clean && make
5. Run all tests
6. Keep the build- and E2E-tested deb artifact local; direct/deb publication is currently disabled
7. Tag and upload the image to Docker Hub (see below)

### Docker image upload to Docker Hub

```bash
make docker-image-release \
  STAGING_REGISTRY=<tested registry namespace> \
  STAGING_VERSION=<tested image tag> \
  STAGING_DIGEST=sha256:<tested image digest> \
  RELEASE_VERSION=<version> \
  RELEASE_REGISTRY=<release registry namespace>
```

This command does not rebuild the image. It verifies the staging tag still
matches the supplied tested digest, promotes that exact multi-architecture
manifest to both `<version>` and `latest`, and verifies both published tags
resolve to the same digest.



# Development

## Remote Server

Use the following commands to start and stop the reusable SSH test server for
development testing. This reuses the same Compose-managed `remote` service and
the same image used by the end-to-end tests.

```bash
make run-remote-server
make stop-remote-server
```

The supported startup path is `make run-remote-server`. That target
auto-supplies `SEEDSYNC_REMOTE_FILES_DIR` as the repo-root convenience
directory `build/docker-local/remote-files` unless you override it, so the
remote test server mounts the intended host folder into `/home/remoteuser/files`.

To point the server at a different host folder, override
`SEEDSYNC_REMOTE_FILES_DIR` when starting the supported make target:

```bash
SEEDSYNC_REMOTE_FILES_DIR=/path/to/local/files make run-remote-server
```

If you bypass the make target and call Compose directly with this file set,
set `SEEDSYNC_REMOTE_FILES_DIR` explicitly. Direct Compose runs require the
variable, and it should point at the host directory you want mounted as
`/home/remoteuser/files`.

On this workspace, the common local convenience choice is
`build/docker-local/remote-files` in the repo root, but it is a local choice,
not a global public default.

## Browser Access Model

The intended browser-access UX is:

- the first browser uses the bootstrap or handover flow to claim access
- once claimed, a remembered browser should behave like a durable trusted
  client
- that remembered-browser state is a hidden implementation detail of the API
  key credential
- normal return visits should reopen the app without sending the user back
  through `/bootstrap`
- `/bootstrap` should mainly appear for first claim or explicit recovery
- deleting or revoking the API key clears any remembered-browser state tied
  to that key
- bootstrap and recovery sessions remain short-lived

When investigating or changing this area, do not treat a persistent cookie by
itself as sufficient proof that the remembered-browser behavior is correct.
The browser cookie and the server-side authorization retention model both need
to match the intended durable-browser UX.

The connection parameters for the remote server are:

| Option         | Value                             |
| -------------- | --------------------------------- |
| Remote Address | localhost                         |
| Remote Port    | 1234                              |
| Username       | remoteuser                        |
| Pass           | remotepass                        |
| Remote Path    | /home/remoteuser/files            |

The SSH port is published on `localhost:1234` when the remote server is
started through the Compose helper above. Use that host address with the
manual helper; `host.docker.internal` is not needed for this loopback-only
bind.



## Run Docker Image

Use the following command to run the docker image locally:

```bash
docker run --rm -p 8800:8800 localhost:5000/seedsync:latest
```
