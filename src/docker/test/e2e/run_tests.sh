#!/bin/bash

set -euo pipefail

red=`tput setaf 1`
green=`tput setaf 2`
reset=`tput sgr0`

STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-60}
POLL_INTERVAL=${POLL_INTERVAL:-2}
CURL_TIMEOUT=${CURL_TIMEOUT:-5}
SERVER_UP='False'

END=$((SECONDS+STARTUP_TIMEOUT))
while [ ${SECONDS} -lt ${END} ];
do
  SERVER_UP=$(
      curl --fail --silent --show-error --max-time "${CURL_TIMEOUT}" myapp:8800/server/status 2>/dev/null | \
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
  node_modules/protractor/bin/protractor tmp/conf.js
else
  echo "${red}E2E Test failed to detect Seedsync server${reset}"
  exit 1
fi
