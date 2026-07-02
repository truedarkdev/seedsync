# Creates environment for e2e tests
FROM node:20-bookworm-slim as seedsync_test_e2e_env

COPY src/e2e/package*.json /app/
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl python3 python-is-python3 && rm -rf /var/lib/apt/lists/*
RUN npm ci --legacy-peer-deps


# Builds and runs e2e tests
FROM seedsync_test_e2e_env as seedsync_test_e2e

COPY \
    src/e2e/conf.ts \
    src/e2e/selenium-webdriver-promise-shim.cjs \
    src/e2e/tsconfig.json \
    src/e2e/urls.ts \
    /app/
COPY src/e2e/tests /app/tests
COPY \
    src/docker/test/e2e/auth_seed.sh \
    src/docker/test/e2e/run_tests.sh \
    src/docker/test/e2e/parse_seedsync_status.py \
    /app/

WORKDIR /app

RUN sed -i 's/\r$//' /app/auth_seed.sh /app/run_tests.sh && chmod +x /app/auth_seed.sh /app/run_tests.sh
RUN npm run build

CMD ["/app/run_tests.sh"]
