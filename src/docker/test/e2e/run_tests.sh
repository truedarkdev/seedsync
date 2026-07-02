#!/bin/bash

set -euo pipefail

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
  red=`tput setaf 1`
  green=`tput setaf 2`
  reset=`tput sgr0`
else
  red=""
  green=""
  reset=""
fi

STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-60}
POLL_INTERVAL=${POLL_INTERVAL:-2}
CURL_TIMEOUT=${CURL_TIMEOUT:-5}
readonly API_TOKEN="${SEEDSYNC_E2E_API_TOKEN:?SEEDSYNC_E2E_API_TOKEN is required for e2e test execution}"
readonly BROWSER_SESSION_SECRET="${SEEDSYNC_E2E_BROWSER_SESSION_SECRET:?SEEDSYNC_E2E_BROWSER_SESSION_SECRET is required for remembered-session bootstrap}"
export SEEDSYNC_E2E_API_TOKEN="${API_TOKEN}"
export SEEDSYNC_E2E_BROWSER_SESSION_SECRET="${BROWSER_SESSION_SECRET}"
readonly AUTHORIZATION_HEADER_FILE="$(mktemp)"
SERVER_UP='False'

trap 'rm -f -- "${AUTHORIZATION_HEADER_FILE}"' EXIT
printf 'Authorization: Bearer %s\n' "${API_TOKEN}" > "${AUTHORIZATION_HEADER_FILE}"
chmod 600 "${AUTHORIZATION_HEADER_FILE}"

wait_for_remote_ready() {
  local remote_up='False'
  local end=$((SECONDS+STARTUP_TIMEOUT))

  while [ ${SECONDS} -lt ${end} ];
  do
    if python - <<'PY' >/dev/null 2>&1
import socket
with socket.create_connection(("remote", 1234), timeout=2):
    pass
PY
    then
      remote_up='True'
      break
    fi
    echo "E2E Test is waiting for the remote SSH fixture... (${SECONDS}s/${STARTUP_TIMEOUT}s)"
    sleep "${POLL_INTERVAL}"
  done

  if [[ "${remote_up}" == 'True' ]]; then
    echo "${green}E2E Test detected that the remote SSH fixture is READY${reset}"
  else
    echo "${red}E2E Test failed to detect the remote SSH fixture${reset}"
    exit 1
  fi
}

wait_for_selenium_ready() {
  local selenium_up='False'
  local end=$((SECONDS+STARTUP_TIMEOUT))

  while [ ${SECONDS} -lt ${end} ];
  do
    if curl --fail --silent --show-error --max-time "${CURL_TIMEOUT}" http://chrome:4444/status 2>/dev/null | \
        grep -q '"ready": true'; then
      selenium_up='True'
      break
    fi
    echo "E2E Test is waiting for Selenium to become ready... (${SECONDS}s/${STARTUP_TIMEOUT}s)"
    sleep "${POLL_INTERVAL}"
  done

  if [[ "${selenium_up}" == 'True' ]]; then
    echo "${green}E2E Test detected that Selenium is READY${reset}"
  else
    echo "${red}E2E Test failed to detect Selenium readiness${reset}"
    exit 1
  fi
}

END=$((SECONDS+STARTUP_TIMEOUT))
while [ ${SECONDS} -lt ${END} ];
do
  SERVER_UP=$(
      curl --fail --silent --show-error --max-time "${CURL_TIMEOUT}" \
        --header "@${AUTHORIZATION_HEADER_FILE}" \
        myapp:8800/server/status 2>/dev/null | \
        python ./parse_seedsync_status.py 2>/dev/null || echo 'False'
  )
  if [[ "${SERVER_UP}" == 'True' ]]; then
    break
  fi
  echo "E2E Test is waiting for Seedsync server to come up... (${SECONDS}s/${STARTUP_TIMEOUT}s)"
  sleep "${POLL_INTERVAL}"
done


if [[ "${SERVER_UP}" == 'True' ]]; then
  echo "${green}E2E Test detected that Seedsync server is UP${reset}"
  wait_for_remote_ready
  wait_for_selenium_ready
  node -r /app/selenium-webdriver-promise-shim.cjs /app/node_modules/protractor/bin/protractor tmp/conf.js
else
  echo "${red}E2E Test failed to detect Seedsync server${reset}"
  exit 1
fi
