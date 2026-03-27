# E2E Tests

## WSL/Linux Baseline

If you are debugging the local Linux/WSL lanes, run the shared preflight first:

```bash
make preflight-linux-wsl
```

This helper checks:

* Python 3.11 or 3.12
* OpenSSH client (`ssh`)
* `lftp`
* `rar` and `unrar`
* `127.0.0.1:22` for the Python integration SSH lane
* `127.0.0.1:1234` for the Compose-managed reusable remote fixture lane

The reusable remote SSH fixture is started with:

```bash
make run-remote-server
```

See [doc/DeveloperReadme.md](../../doc/DeveloperReadme.md) for the shared lane
breakdown and the archive-backed prerequisites.

### To run e2e tests in dev mode:

1. Install dependencies

   ```bash
   cd src/e2e
   npm install
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
        ./node_modules/typescript/bin/tsc && \
        ./node_modules/protractor/bin/protractor tmp/conf.js
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
   However the url used in test is still [http://myapp:8800](http://myapp:8800)
   as that's how the selenium server accesses it.

2. The app requires a fully configured settings.cfg.
   This is done automatically during the start of the docker image that runs
   the app.

3. The reusable remote SSH fixture that backs the lane starts with:

   ```bash
   make run-remote-server
   ```

   That service publishes SSH on `localhost:1234`, which is the endpoint used
   by the Linux/WSL baseline helper and by the Compose-backed remote fixture
   path.
