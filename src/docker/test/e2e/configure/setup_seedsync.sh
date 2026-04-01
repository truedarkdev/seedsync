#!/bin/bash

set -euo pipefail

STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-90}
POLL_INTERVAL=${POLL_INTERVAL:-2}
CONFIGURE_CURL_TIMEOUT=${CONFIGURE_CURL_TIMEOUT:-15}
readonly base_url="http://myapp:8800"

wait_for_app_ready() {
  local label="$1"
  local app_ready='False'
  local end=$((SECONDS+STARTUP_TIMEOUT))

  while [ ${SECONDS} -lt ${end} ];
  do
    if curl --fail --silent --show-error --max-time "${CONFIGURE_CURL_TIMEOUT}" "${base_url}/server/status" >/dev/null 2>&1; then
      app_ready='True'
      break
    fi
    echo "E2E Configure is waiting for Seedsync server reachability... (${SECONDS}s/${STARTUP_TIMEOUT}s)"
    sleep "${POLL_INTERVAL}"
  done

  if [[ "${app_ready}" == 'True' ]]; then
    echo "$label"
  else
    echo "E2E Configure failed to detect Seedsync server reachability"
    exit 1
  fi
}

call_api() {
  local url="$1"
  local method="${2:-GET}"
  curl --fail --silent --show-error --max-time "${CONFIGURE_CURL_TIMEOUT}" --request "${method}" "${url}"
  echo
}

wait_for_app_ready "Seedsync app is up (before configuring)"
call_api "${base_url}/server/config/set/general/debug/true"
call_api "${base_url}/server/config/set/general/verbose/true"
call_api "${base_url}/server/config/set/lftp/local_path/%252Fdownloads"
call_api "${base_url}/server/config/set/lftp/remote_address/remote"
call_api "${base_url}/server/config/set/lftp/remote_username/remoteuser"
call_api "${base_url}/server/config/set/lftp/remote_password/remotepass"
call_api "${base_url}/server/config/set/lftp/remote_port/1234"
call_api "${base_url}/server/config/set/lftp/remote_path/%252Fhome%252Fremoteuser%252Ffiles"
call_api "${base_url}/server/config/set/autoqueue/patterns_only/true"

wait_for_app_ready "Seedsync app is up (before restart)"
call_api "${base_url}/server/command/restart" POST

wait_for_app_ready "Seedsync app is up (after configuring)"

echo
echo "Done configuring SeedSync app"
