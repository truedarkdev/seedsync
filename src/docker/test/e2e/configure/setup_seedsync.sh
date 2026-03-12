#!/bin/bash

set -euo pipefail

WAIT_FOR_IT_TIMEOUT=${WAIT_FOR_IT_TIMEOUT:-90}
CONFIGURE_CURL_TIMEOUT=${CONFIGURE_CURL_TIMEOUT:-15}
readonly base_url="http://myapp:8800"

wait_for_app() {
  ./wait-for-it.sh -t "${WAIT_FOR_IT_TIMEOUT}" myapp:8800 -- echo "$1"
}

call_api() {
  curl --fail --silent --show-error --max-time "${CONFIGURE_CURL_TIMEOUT}" "$1"
  echo
}

wait_for_app "Seedsync app is up (before configuring)"
call_api "${base_url}/server/config/set/general/debug/true"
call_api "${base_url}/server/config/set/general/verbose/true"
call_api "${base_url}/server/config/set/lftp/local_path/%252Fdownloads"
call_api "${base_url}/server/config/set/lftp/remote_address/remote"
call_api "${base_url}/server/config/set/lftp/remote_username/remoteuser"
call_api "${base_url}/server/config/set/lftp/remote_password/remotepass"
call_api "${base_url}/server/config/set/lftp/remote_port/1234"
call_api "${base_url}/server/config/set/lftp/remote_path/%252Fhome%252Fremoteuser%252Ffiles"
call_api "${base_url}/server/config/set/autoqueue/patterns_only/true"

call_api "${base_url}/server/command/restart"

wait_for_app "Seedsync app is up (after configuring)"

echo
echo "Done configuring SeedSync app"
