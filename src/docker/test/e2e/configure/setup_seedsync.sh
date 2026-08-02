#!/bin/bash

set -euo pipefail

STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-90}
POLL_INTERVAL=${POLL_INTERVAL:-2}
CONFIGURE_CURL_TIMEOUT=${CONFIGURE_CURL_TIMEOUT:-15}
readonly API_TOKEN="${SEEDSYNC_E2E_API_TOKEN:?SEEDSYNC_E2E_API_TOKEN is required for e2e auth seeding}"
readonly AUTHORIZATION_HEADER_FILE="$(mktemp)"
readonly api_key_dir="${SEEDSYNC_API_KEY_DIR:-/config}"
readonly api_key_owner="${SEEDSYNC_API_KEY_OWNER:-}"
readonly settings_path="${api_key_dir%/}/settings.cfg"
readonly base_url="http://myapp:8800"

trap 'rm -f -- "${AUTHORIZATION_HEADER_FILE}"' EXIT
printf 'Authorization: Bearer %s\n' "${API_TOKEN}" > "${AUTHORIZATION_HEADER_FILE}"
chmod 600 "${AUTHORIZATION_HEADER_FILE}"

wait_for_app_ready() {
  local label="$1"
  local app_ready='False'
  local end=$((SECONDS+STARTUP_TIMEOUT))

  while [ ${SECONDS} -lt ${end} ];
  do
    if curl --fail --silent --show-error --max-time "${CONFIGURE_CURL_TIMEOUT}" \
      --header "@${AUTHORIZATION_HEADER_FILE}" \
      "${base_url}/server/status" >/dev/null 2>&1; then
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
  local response_body
  local response_stderr
  local response_status
  local curl_exit

  response_body="$(mktemp)"
  response_stderr="$(mktemp)"
  if response_status=$(
    curl --silent --show-error --max-time "${CONFIGURE_CURL_TIMEOUT}" \
      --request "${method}" \
      --header "@${AUTHORIZATION_HEADER_FILE}" \
      --output "${response_body}" \
      --write-out '%{http_code}' \
      "${url}" 2>"${response_stderr}"
  ); then
    if [[ "${response_status}" != 2* ]]; then
      echo "E2E Configure request returned HTTP ${response_status}: ${method} ${url}"
      if [[ -s "${response_body}" ]]; then
        echo "Response body:"
        cat "${response_body}"
      else
        echo "Response body: <empty>"
      fi
      if [[ -s "${response_stderr}" ]]; then
        echo "Curl stderr:"
        cat "${response_stderr}"
      fi
      rm -f "${response_body}" "${response_stderr}"
      return 1
    fi
  else
    curl_exit=$?
    echo "E2E Configure request failed: ${method} ${url} (curl exit ${curl_exit})"
    if [[ -s "${response_body}" ]]; then
      echo "Response body:"
      cat "${response_body}"
    else
      echo "Response body: <empty>"
    fi
    if [[ -s "${response_stderr}" ]]; then
      echo "Curl stderr:"
      cat "${response_stderr}"
    fi
    rm -f "${response_body}" "${response_stderr}"
    return "${curl_exit}"
  fi

  if [[ -s "${response_body}" ]]; then
    cat "${response_body}"
    echo
  fi

  rm -f "${response_body}" "${response_stderr}"
}

call_api_json() {
  local url="$1"
  local json_body="$2"
  local response_body
  local response_stderr
  local response_status
  local curl_exit

  response_body="$(mktemp)"
  response_stderr="$(mktemp)"
  if response_status=$(
    curl --silent --show-error --max-time "${CONFIGURE_CURL_TIMEOUT}" \
      --request POST \
      --header "@${AUTHORIZATION_HEADER_FILE}" \
      --header "Content-Type: application/json" \
      --data "${json_body}" \
      --output "${response_body}" \
      --write-out '%{http_code}' \
      "${url}" 2>"${response_stderr}"
  ); then
    if [[ "${response_status}" != 2* ]]; then
      echo "E2E Configure request returned HTTP ${response_status}: POST ${url}"
      if [[ -s "${response_body}" ]]; then
        echo "Response body:"
        cat "${response_body}"
      else
        echo "Response body: <empty>"
      fi
      if [[ -s "${response_stderr}" ]]; then
        echo "Curl stderr:"
        cat "${response_stderr}"
      fi
      rm -f "${response_body}" "${response_stderr}"
      return 1
    fi
  else
    curl_exit=$?
    echo "E2E Configure request failed: POST ${url} (curl exit ${curl_exit})"
    if [[ -s "${response_body}" ]]; then
      echo "Response body:"
      cat "${response_body}"
    else
      echo "Response body: <empty>"
    fi
    if [[ -s "${response_stderr}" ]]; then
      echo "Curl stderr:"
      cat "${response_stderr}"
    fi
    rm -f "${response_body}" "${response_stderr}"
    return "${curl_exit}"
  fi

  if [[ -s "${response_body}" ]]; then
    cat "${response_body}"
    echo
  fi

  rm -f "${response_body}" "${response_stderr}"
}

wait_for_app_ready "Seedsync app is up (before configuring)"
call_api_json "${base_url}/server/config/set/general/log_level" '{"value":"DEBUG"}'
call_api_json "${base_url}/server/config/set/general/verbose" '{"value":true}'
call_api_json "${base_url}/server/config/set/lftp/local_path" '{"value":"/downloads"}'
call_api_json "${base_url}/server/config/set/lftp/remote_address" '{"value":"remote"}'
call_api_json "${base_url}/server/config/set/lftp/remote_username" '{"value":"remoteuser"}'
call_api_json "${base_url}/server/config/set/lftp/remote_password" '{"value":"remotepass"}'
call_api_json "${base_url}/server/config/set/lftp/remote_port" '{"value":1234}'
call_api_json "${base_url}/server/config/set/lftp/remote_path" '{"value":"/home/remoteuser/files"}'
call_api_json "${base_url}/server/config/set/autoqueue/patterns_only" '{"value":true}'

wait_for_app_ready "Seedsync app is up (before restart)"
if [[ -n "${api_key_owner}" ]]; then
  chown -R "${api_key_owner}" "${api_key_dir}"
fi
chmod 700 "${api_key_dir}"
chmod 600 "${settings_path}"
call_api "${base_url}/server/command/restart" POST

wait_for_app_ready "Seedsync app is up (after configuring)"

echo
echo "Done configuring SeedSync app"
