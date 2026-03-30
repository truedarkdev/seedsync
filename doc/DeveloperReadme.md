[TOC]



# Environment Setup

## Install dependencies
1. Install [Node.js 20 LTS](https://nodejs.org/en/download) (comes with npm). The Angular toolchain here is validated against the Docker baseline, not the old Node 12-era setup.

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
   sudo apt-get install -y lftp python3-dev rar
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

For the current lane inventory, minimum-evidence ladder, durable artifact
guidance, and open gaps, see [doc/testing-confidence.md](testing-confidence.md).

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

If you need junit XML for a local run, create the repo-root `tmp/pytest/`
tree first and write the file there. From `src/python`, a typical path looks
like:

```bash
mkdir -p ../../tmp/pytest
poetry run pytest --junitxml=../../tmp/pytest/python-unit.xml
```

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

On Windows, the validated local command path on Node 20 is:

```powershell
Set-Location C:\Git\seedsync\src\angular
$env:CHROME_BIN = "C:\Program Files\Google\Chrome\Application\chrome.exe"
npm ci --legacy-peer-deps
npm test -- --watch=false --single-run --browsers=ChromeHeadless
```

Set `CHROME_BIN` explicitly when Chrome is installed but not on `PATH`. The
headless launcher configured in `karma.conf.js` works without a GUI Chrome
session. If you want an interactive browser run for local debugging, override
the browser explicitly, for example:

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
# Docker image (arch=amd64,arm64,arm/v7)
make run-tests-e2e STAGING_VERSION=latest SEEDSYNC_ARCH=<arch code>
# Debian package (active DEB e2e lane: `ubu2004`, using the Ubuntu 20.04 lane)
make run-tests-e2e SEEDSYNC_DEB=`readlink -f build/*.deb` SEEDSYNC_OS=ubu2004
```

By default images are pulled from `localhost:5000`. To test image from a registry other than the local, use `STAGING_REGISTRY=`.
For example:

```bash
make run-tests-e2e STAGING_VERSION=latest SEEDSYNC_ARCH=arm64 STAGING_REGISTRY=ghcr.io/truedarkdev
```

`SEEDSYNC_ARCH` is resolved through `src/docker/test/resolve_platform.sh`, so the
active E2E architecture codes remain `amd64`, `arm64`, and `arm/v7` while the
Docker platform string stays consistent across `make`, Compose, and CI.



# Release

## Continuous Integration

This method uses GitHub Actions plus `gh release` on the hosted runner to post releases.

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
6. Upload deb file to github
7. Tag and upload image to Dockerhub (see below)

### Docker image upload to Dockerhub

```bash
make docker-image-release RELEASE_VERSION=<version> RELEASE_REGISTRY=<registry namespace>
make docker-image-release RELEASE_VERSION=latest RELEASE_REGISTRY=<registry namespace>
```



# Development

## Remote Server

Use the following commands to start and stop the reusable SSH test server for
development testing. This reuses the same Compose-managed `remote` service and
the same image used by the end-to-end tests.

```bash
make run-remote-server
make stop-remote-server
```

By default, the service mounts the repo-root-relative directory
`build/docker-local/remote-files` into `/home/remoteuser/files`. That keeps the
manual helper files alongside the local SeedSync Docker setup while leaving the
checked-in e2e fixture data untouched.

To point the server at a different host folder, set `SEEDSYNC_REMOTE_FILES_DIR`
before starting it:

```bash
SEEDSYNC_REMOTE_FILES_DIR=/path/to/local/files make run-remote-server
```

If you want to use the default without an override, drop the files you want the
SSH test server to expose into `build/docker-local/remote-files` at the repo
root.

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
