# E2E Tests

## WSL/Linux Baseline

If you are debugging the local Linux/WSL lanes, run the shared preflight first:

```bash
make preflight-linux-wsl
```

The helper runs the full baseline by default, but the direct script can target
individual slices when you only need one lane:

```bash
bash src/docker/test/check_linux_wsl_baseline.sh --live-ssh-lftp
bash src/docker/test/check_linux_wsl_baseline.sh --archive-backed
bash src/docker/test/check_linux_wsl_baseline.sh --reusable-remote-fixture
```

The helper checks the live SSH/LFTP lane, the archive-backed lane, and the
reusable remote fixture lane in separate buckets:

* Live SSH/LFTP lane:
  * Python 3.11 or 3.12
  * OpenSSH client (`ssh`)
  * `lftp`
  * a non-interactive SSH login-style probe against `seedsynctest@127.0.0.1:22`

* Archive-backed lane:
  * `rar`
  * `unrar`

The helper does not start the remote fixture for you. If you need the
Compose-managed reusable remote SSH lane, bootstrap it separately with:

```bash
make run-remote-server
```

That service publishes SSH on `127.0.0.1:1234`. The baseline helper keeps that
fixture bootstrap step separate from the host-side SSH probe, and it keeps the
archive-backed prerequisites separate from the live SSH/LFTP lane.

See [doc/DeveloperReadme.md](../../doc/DeveloperReadme.md) for the shared lane
breakdown and the archive-backed prerequisites.
See [doc/testing-confidence.md](../../doc/testing-confidence.md) for the
canonical lane inventory and minimum-evidence ladder.

### To run e2e tests in dev mode:

1. Install dependencies

   ```bash
   cd src/e2e
   npm ci --legacy-peer-deps
   ```

2. Choose which dev image to run: deb install or docker image

    - deb install

        ```bash
        make run-tests-e2e STAGING_VERSION=latest SEEDSYNC_ARCH=<arch code> DEV=1
        ```

    - docker image

        ```bash
        make run-tests-e2e SEEDSYNC_DEB=`readlink -f build/*.deb` SEEDSYNC_OS=<os code> DEV=1
        ```

3. Compile and run the tests

    ```bash
    cd src/e2e/
    rm -rf tmp && \
        npm test
    ```

### About

The dev end-to-end tests use the following docker images:

1. myapp: Installs and runs the seedsync deb package
2. chrome: Runs the selenium server
3. remote: Runs a remote SSH server

The automated e2e tests additionally have:

4. tests: Runs the e2e tests

Notes:

1. In dev mode, the app is visible at [http://localhost:8800](http://localhost:8800).
   The shared Protractor URL source now defaults to the Docker service names
   used by the containerized lane:

   - app: `http://myapp:8800/`
   - Selenium: `http://chrome:4444/wd/hub`

   If you need to run the legacy harness against host-exposed services,
   override both URLs explicitly:

   - `SEEDSYNC_E2E_APP_BASE_URL` for the app base URL
   - `SEEDSYNC_E2E_SELENIUM_ADDRESS` for the Selenium endpoint

2. The app requires a fully configured settings.cfg.
   This is done automatically during the start of the docker image that runs
   the app.

3. The reusable remote SSH fixture that backs the lane starts with:

   ```bash
   make run-remote-server
   ```

   That service publishes SSH on `127.0.0.1:1234`. The Linux/WSL baseline
   helper only treats that endpoint as a separately bootstrapped fixture, not
   as a host prerequisite. The Dockerized e2e lane now waits for that fixture
   before starting Protractor, but it still expects the remote service to be
   reachable on the Compose network as `remote:1234`.
