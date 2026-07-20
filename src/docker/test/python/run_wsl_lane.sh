#!/usr/bin/env bash
set -Eeuo pipefail
CALLER_UMASK="$(umask)"
umask 077

# Run the Python suite from WSL in bounded batches. Live SSH tests are opt-in.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../../.." && pwd -P)"
WORKDIR="${REPO_ROOT}/src/python"
REMOTE_FILES_DIR="${SEEDSYNC_REMOTE_FILES_DIR:-${REPO_ROOT}/build/docker-local/remote-files}"
REMOTE_PORT=1234
BATCH_SIZE=30
REMOTE_WAIT_SECONDS=60
START_AT=1
LIMIT=""
LIVE_SSH=0
PROVISION_TEST_TOOLS=0
PREFLIGHT_ONLY=0
SELF_TEST=0
SSH_KNOWN_HOSTS_FILE=""

die() { echo "ERROR: $*" >&2; exit 2; }
positive_int() { [[ "$1" =~ ^[1-9][0-9]*$ ]] || die "expected a positive integer, got '$1'"; }
restore_caller_umask() { umask "$CALLER_UMASK"; }
private_dir() {
  (umask 077; mkdir -p -- "$1"; chmod 700 "$1")
}
caller_dir() {
  (umask "$CALLER_UMASK"; mkdir -p -- "$1")
}
private_file() {
  (umask 077; : >"$1"; chmod 600 "$1")
}
run_pytest() {
  (
    restore_caller_umask
    cd "$WORKDIR"
    poetry run python -m pytest "$@"
  )
}
test_tools_provision_mode() {
  local uid="$1" sudo_ready="$2"
  if [[ "$uid" -eq 0 ]]; then
    echo root
  elif [[ "$sudo_ready" == yes ]]; then
    echo sudo-noninteractive
  else
    echo unavailable
  fi
}
token_source() {
  [[ -n "$1" ]] && echo supplied || echo generated
}
cleanup_lane_ssh() {
  if [[ -n "$SSH_KNOWN_HOSTS_FILE" ]]; then
    rm -f -- "$SSH_KNOWN_HOSTS_FILE"
  fi
}
prepare_lane_ssh() {
  if [[ -n "$SSH_KNOWN_HOSTS_FILE" ]]; then
    cleanup_lane_ssh
  fi
  SSH_KNOWN_HOSTS_FILE="$(mktemp "/tmp/seedsync-wsl-known-hosts.XXXXXX")" || die "could not create a private SSH known-hosts file"
  [[ "$SSH_KNOWN_HOSTS_FILE" =~ ^/tmp/seedsync-wsl-known-hosts\.[A-Za-z0-9]+$ ]] || die "generated SSH known-hosts path failed the safety check"
  chmod 600 "$SSH_KNOWN_HOSTS_FILE"
  trap cleanup_lane_ssh EXIT
}
check_7z_rar_codec() {
  command -v 7z >/dev/null 2>&1 || return 1
  7z i 2>/dev/null | grep -Ei '(^|[[:space:]])rar([[:space:]]|$)' >/dev/null
}
check_perl_crc32() {
  command -v perl >/dev/null 2>&1 || return 1
  perl -MString::CRC32 -e 1 >/dev/null 2>&1
}
perl_crc32_decision() {
  [[ "$1" == yes && "$2" == yes ]] && echo ready || echo missing
}

usage() {
  cat <<'EOF'
Usage: bash src/docker/test/python/run_wsl_lane.sh [options]

Runs pytest from /mnt/c/Git/seedsync/src/python in batches of collected tests and
writes artifacts to tmp/pytest/runs/<timestamp>/. Live SSH/LFTP tests remain
opt-in. The intentional excessive-connections skip is preserved.

  --live-ssh                 Start/reuse the named Docker e2e remote and enable live tests.
  --batch-size N             Collected test nodeids per batch (default: 30).
  --start-at N               1-based batch to start (default: 1).
  --limit N                  Run at most N selected test nodeids (smoke check).
  --provision-test-tools      Explicitly apt-get install missing WSL test tools.
  --provision-archive-tools   Deprecated alias for --provision-test-tools.
  --preflight-only           Check prerequisites and (with --live-ssh) remote health, then exit.
  --self-test                Run deterministic selection/accounting checks without external prerequisites.
  -h, --help                 Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live-ssh) LIVE_SSH=1; shift ;;
    --batch-size) [[ $# -ge 2 ]] || die "--batch-size requires a value"; positive_int "$2"; BATCH_SIZE="$2"; shift 2 ;;
    --start-at) [[ $# -ge 2 ]] || die "--start-at requires a value"; positive_int "$2"; START_AT="$2"; shift 2 ;;
    --limit) [[ $# -ge 2 ]] || die "--limit requires a value"; positive_int "$2"; LIMIT="$2"; shift 2 ;;
    --provision-test-tools|--provision-archive-tools) PROVISION_TEST_TOOLS=1; shift ;;
    --preflight-only) PREFLIGHT_ONLY=1; shift ;;
    --self-test) SELF_TEST=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option '$1' (use --help)" ;;
  esac
done

if [[ "$SELF_TEST" -eq 1 ]]; then
  self_test_nodes=(node-a node-b node-c node-d node-e)
  self_test_offset=$(( (2 - 1) * 2 ))
  self_test_selected=("${self_test_nodes[@]:self_test_offset:2}")
  [[ "${self_test_selected[*]}" == "node-c node-d" ]] || die "self-test selection failed"
  self_test_harness_errors=0
  self_test_exit_code=7
  [[ "$self_test_exit_code" -eq 0 ]] || self_test_harness_errors=1
  [[ "$self_test_harness_errors" -eq 1 ]] || die "self-test unexpected-exit accounting failed"
  self_test_tee_exit_code=1
  [[ "$self_test_tee_exit_code" -eq 0 ]] || self_test_harness_errors=$((self_test_harness_errors + 1))
  [[ "$self_test_harness_errors" -eq 2 ]] || die "self-test tee failure accounting failed"
  [[ "$(test_tools_provision_mode 0 no)" == root ]] || die "self-test root provisioning mode failed"
  [[ "$(test_tools_provision_mode 1000 yes)" == sudo-noninteractive ]] || die "self-test noninteractive sudo mode failed"
  [[ "$(test_tools_provision_mode 1000 no)" == unavailable ]] || die "self-test unavailable provisioning mode failed"
  [[ "$(token_source supplied-token)" == supplied ]] || die "self-test supplied token selection failed"
  [[ "$(token_source "")" == generated ]] || die "self-test generated token selection failed"
  [[ "$(perl_crc32_decision yes yes)" == ready ]] || die "self-test Perl CRC32 dependency decision failed"
  [[ "$(perl_crc32_decision yes no)" == missing ]] || die "self-test missing Perl CRC32 module decision failed"
  restore_caller_umask
  [[ "$(umask)" == "$CALLER_UMASK" ]] || die "self-test caller umask restore failed"
  umask 077
  self_test_artifact_dir="$(mktemp -d "/tmp/seedsync-wsl-artifacts.XXXXXX")"
  private_dir "$self_test_artifact_dir"
  self_test_artifact_file="${self_test_artifact_dir}/artifact"
  private_file "$self_test_artifact_file"
  [[ "$(stat -c '%a' "$self_test_artifact_dir")" == 700 ]] || die "self-test artifact directory mode failed"
  [[ "$(stat -c '%a' "$self_test_artifact_file")" == 600 ]] || die "self-test artifact file mode failed"
  rm -f -- "$self_test_artifact_file"
  rmdir -- "$self_test_artifact_dir"
  self_test_fixture_parent="$(mktemp -d "/tmp/seedsync-wsl-fixture.XXXXXX")"
  self_test_fixture_dir="${self_test_fixture_parent}/fixture"
  caller_dir "$self_test_fixture_dir"
  self_test_expected_mode="$(python3 - "$CALLER_UMASK" <<'PY'
import sys
print(format(0o777 & ~int(sys.argv[1], 8), "o"))
PY
)"
  [[ "$(stat -c '%a' "$self_test_fixture_dir")" == "$self_test_expected_mode" ]] || die "self-test caller fixture directory mode failed"
  rmdir -- "$self_test_fixture_dir"
  rmdir -- "$self_test_fixture_parent"
  if command -v 7z >/dev/null 2>&1; then
    check_7z_rar_codec || die "self-test 7z RAR codec probe failed"
  fi
  self_test_tmpdir="${TMPDIR-}"
  TMPDIR="/tmp/unsafe'; touch /tmp/should-not-execute; ;"
  prepare_lane_ssh
  [[ "$SSH_KNOWN_HOSTS_FILE" != *unsafe* ]] || die "self-test known-hosts path accepted caller-controlled TMPDIR"
  self_test_previous_known_hosts="$SSH_KNOWN_HOSTS_FILE"
  prepare_lane_ssh
  [[ ! -e "$self_test_previous_known_hosts" ]] || die "self-test known-hosts replacement left an orphaned file"
  cleanup_lane_ssh
  TMPDIR="$self_test_tmpdir"
  echo "WSL Python lane self-test passed (selection + dependency + umask + harness accounting)."
  exit 0
fi

preflight_errors=()
record_preflight_error() { preflight_errors+=("$1"); }
command -v python3 >/dev/null 2>&1 || record_preflight_error "python3 is required in WSL"
if ! command -v poetry >/dev/null 2>&1; then
  if [[ -x "${HOME}/.local/bin/poetry" ]]; then
    export PATH="${HOME}/.local/bin:${PATH}"
  else
    record_preflight_error "poetry is required in WSL; install it with the repo-supported Poetry setup"
  fi
fi
command -v ssh >/dev/null 2>&1 || record_preflight_error "ssh is required; install openssh-client in WSL"
command -v lftp >/dev/null 2>&1 || record_preflight_error "lftp is required; install with: sudo apt-get update && sudo apt-get install -y lftp"
missing_test_tools=()
command -v perl >/dev/null 2>&1 || missing_test_tools+=(perl)
if command -v perl >/dev/null 2>&1 && ! check_perl_crc32; then
  missing_test_tools+=(libstring-crc32-perl)
fi
if [[ "$LIVE_SSH" -eq 1 ]] && ! command -v docker >/dev/null 2>&1; then
  record_preflight_error "docker is required for --live-ssh; run this lane inside WSL with Docker available"
fi
if command -v poetry >/dev/null 2>&1; then
  run_pytest --version >/dev/null 2>&1 || record_preflight_error "poetry/pytest unavailable; from ${WORKDIR}, run 'poetry install' and retry"
  if poetry_python_version="$(cd "$WORKDIR" && poetry run python -c 'import sys; print("%s.%s" % sys.version_info[:2])')"; then
    case "$poetry_python_version" in
      3.11|3.12) ;;
      *) record_preflight_error "Poetry Python ${poetry_python_version} is outside the supported WSL range (3.11 or 3.12)" ;;
    esac
  else
    record_preflight_error "could not resolve the Poetry Python interpreter; from ${WORKDIR}, run 'poetry install' and retry"
  fi
fi

command -v 7z >/dev/null 2>&1 || missing_test_tools+=(7z)
command -v rar >/dev/null 2>&1 || missing_test_tools+=(rar)
if command -v 7z >/dev/null 2>&1 && ! check_7z_rar_codec; then
  missing_test_tools+=(p7zip-rar)
fi
if [[ ${#missing_test_tools[@]} -gt 0 ]]; then
  if [[ "$PROVISION_TEST_TOOLS" -eq 1 ]]; then
    if [[ "$EUID" -ne 0 ]]; then
      command -v sudo >/dev/null 2>&1 || record_preflight_error "test tools missing (${missing_test_tools[*]}) and sudo is unavailable; install explicitly with: wsl.exe -u root -- bash -lc 'apt-get update && apt-get install -y perl libstring-crc32-perl p7zip-full p7zip-rar rar' ; rerun this preflight afterward"
    fi
  else
    record_preflight_error "WSL test tools are missing: ${missing_test_tools[*]}. Install with: sudo apt-get update && sudo apt-get install -y perl libstring-crc32-perl p7zip-full p7zip-rar rar (or rerun with --provision-test-tools)"
  fi
fi
if [[ ${#preflight_errors[@]} -gt 0 ]]; then
  printf 'WSL Python lane preflight found %s issue(s):\n' "${#preflight_errors[@]}" >&2
  printf '  - %s\n' "${preflight_errors[@]}" >&2
  exit 2
fi
if [[ ${#missing_test_tools[@]} -gt 0 && "$PROVISION_TEST_TOOLS" -eq 1 ]]; then
  echo "Provisioning missing WSL test tools (${missing_test_tools[*]}) with apt-get (explicit opt-in)."
  if [[ "$(test_tools_provision_mode "$EUID" no)" == root ]]; then
    apt-get update
    apt-get install -y perl libstring-crc32-perl p7zip-full p7zip-rar rar
  elif sudo -n true >/dev/null 2>&1; then
    sudo -n apt-get update
    sudo -n apt-get install -y perl libstring-crc32-perl p7zip-full p7zip-rar rar
  else
    die "noninteractive sudo is unavailable; install explicitly with: wsl.exe -u root -- bash -lc 'apt-get update && apt-get install -y perl libstring-crc32-perl p7zip-full p7zip-rar rar' ; rerun this preflight afterward"
  fi
  test_tools_after_provision=()
  command -v perl >/dev/null 2>&1 || test_tools_after_provision+=(perl)
  check_perl_crc32 || test_tools_after_provision+=(libstring-crc32-perl)
  command -v 7z >/dev/null 2>&1 || test_tools_after_provision+=(7z)
  command -v rar >/dev/null 2>&1 || test_tools_after_provision+=(rar)
  check_7z_rar_codec || test_tools_after_provision+=(p7zip-rar)
  if [[ ${#test_tools_after_provision[@]} -gt 0 ]]; then
    die "test-tool provisioning completed but prerequisites remain unavailable: ${test_tools_after_provision[*]}"
  fi
fi

check_tcp_port() {
  python3 - "$1" "$2" <<'PY'
import socket
import sys
with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2):
    pass
PY
}

check_ssh_login() {
  prepare_lane_ssh
  if command -v sshpass >/dev/null 2>&1; then
    sshpass -p remotepass ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$SSH_KNOWN_HOSTS_FILE" \
      -o ConnectTimeout=5 -p "$REMOTE_PORT" remoteuser@127.0.0.1 true
  else
    # The fixture's documented password contract is checked without requiring
    # sshpass, which is not part of the baseline WSL package set.
    lftp -u remoteuser,remotepass "sftp://127.0.0.1:${REMOTE_PORT}" \
      -e "set sftp:connect-program 'ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${SSH_KNOWN_HOSTS_FILE}'; set sftp:auto-confirm yes; pwd; bye" >/dev/null 2>&1
  fi
}

check_local_ssh_login() {
  ssh -o BatchMode=yes -o ConnectTimeout=5 -o KbdInteractiveAuthentication=no \
    -o LogLevel=error -o NumberOfPasswordPrompts=0 -o PasswordAuthentication=no \
    -o PreferredAuthentications=publickey -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="$SSH_KNOWN_HOSTS_FILE" \
    -p 22 seedsynctest@127.0.0.1 true
}

prepare_e2e_auth_environment() {
  local generated_api=no generated_browser=no generated_session=no
  if [[ -z "${SEEDSYNC_E2E_API_TOKEN:-}" ]]; then
    SEEDSYNC_E2E_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" || die "could not generate an ephemeral e2e API token"
    generated_api=yes
  fi
  if [[ -z "${SEEDSYNC_E2E_BROWSER_API_TOKEN:-}" ]]; then
    SEEDSYNC_E2E_BROWSER_API_TOKEN="${SEEDSYNC_E2E_API_TOKEN}"
    generated_browser="${generated_api}"
  fi
  if [[ -z "${SEEDSYNC_E2E_BROWSER_SESSION_SECRET:-}" ]]; then
    SEEDSYNC_E2E_BROWSER_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" || die "could not generate an ephemeral e2e browser session secret"
    generated_session=yes
  fi
  export SEEDSYNC_E2E_API_TOKEN SEEDSYNC_E2E_BROWSER_API_TOKEN SEEDSYNC_E2E_BROWSER_SESSION_SECRET
  export SEEDSYNC_API_KEY_DIR="${SEEDSYNC_API_KEY_DIR:-/config}"
  export SEEDSYNC_E2E_API_TOKEN_GENERATED="${generated_api}"
  export SEEDSYNC_E2E_BROWSER_API_TOKEN_GENERATED="${generated_browser}"
  export SEEDSYNC_E2E_BROWSER_SESSION_SECRET_GENERATED="${generated_session}"
  export SEEDSYNC_REMOTE_FILES_DIR="${REMOTE_FILES_DIR}"
}

validate_e2e_compose_environment() {
  local required_name
  for required_name in SEEDSYNC_E2E_API_TOKEN SEEDSYNC_E2E_BROWSER_API_TOKEN SEEDSYNC_E2E_BROWSER_SESSION_SECRET SEEDSYNC_REMOTE_FILES_DIR; do
    [[ -n "${!required_name:-}" ]] || die "${required_name} is required for the e2e Compose lane"
  done
}

ensure_named_remote() {
  command -v docker >/dev/null 2>&1 || die "docker is required for --live-ssh"
  caller_dir "$REMOTE_FILES_DIR"
  prepare_e2e_auth_environment
  validate_e2e_compose_environment
  local compose_files=(
    -f src/docker/test/e2e/compose.yml
    -f src/docker/stage/docker-image/compose.yml
    -f src/docker/test/e2e/compose-remote-dev.yml
  )
  echo "Starting/reusing named e2e remote (SEEDSYNC_REMOTE_FILES_DIR=${REMOTE_FILES_DIR})."
  if ! (cd "$REPO_ROOT" && COMPOSE_DOCKER_CLI_BUILD=1 DOCKER_BUILDKIT=1 \
    SEEDSYNC_REMOTE_FILES_DIR="$REMOTE_FILES_DIR" \
    STAGING_REGISTRY="${STAGING_REGISTRY:-localhost:5000}" STAGING_VERSION="${STAGING_VERSION:-latest}" \
    docker compose "${compose_files[@]}" config >/dev/null); then
    die "merged e2e Compose configuration is invalid; inspect the compose files and required environment variables"
  fi
  (cd "$REPO_ROOT" && COMPOSE_DOCKER_CLI_BUILD=1 DOCKER_BUILDKIT=1 \
    SEEDSYNC_REMOTE_FILES_DIR="$REMOTE_FILES_DIR" \
    STAGING_REGISTRY="${STAGING_REGISTRY:-localhost:5000}" STAGING_VERSION="${STAGING_VERSION:-latest}" \
    docker compose "${compose_files[@]}" up -d --build remote)
  local deadline=$((SECONDS + REMOTE_WAIT_SECONDS))
  until check_tcp_port 127.0.0.1 "$REMOTE_PORT" >/dev/null 2>&1; do
    (( SECONDS >= deadline )) && die "named e2e remote did not become healthy on 127.0.0.1:${REMOTE_PORT}; inspect docker compose ps remote"
    sleep 2
  done
  echo "Named e2e remote is reachable on 127.0.0.1:${REMOTE_PORT}."
  check_ssh_login || die "fixture SSH/LFTP login failed for remoteuser@127.0.0.1:${REMOTE_PORT} (password remotepass; see DeveloperReadme.md)"
  check_local_ssh_login || die "local live-test SSH prerequisite failed for seedsynctest@127.0.0.1:22; create the test account/authorized key as documented in DeveloperReadme.md"
}

if [[ "$LIVE_SSH" -eq 1 ]]; then
  ensure_named_remote
  export SEEDSYNC_LIVE_SSH_TESTS=1
fi
unset SEEDSYNC_E2E_API_TOKEN SEEDSYNC_E2E_BROWSER_API_TOKEN SEEDSYNC_E2E_BROWSER_SESSION_SECRET
if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
  echo "WSL Python lane preflight passed."
  exit 0
fi

private_dir "${REPO_ROOT}/tmp/pytest/runs"
timestamp="$(date -u +%Y%m%d-%H%M%S)"
run_dir="${REPO_ROOT}/tmp/pytest/runs/${timestamp}"
suffix=1
while [[ -e "$run_dir" ]]; do run_dir="${REPO_ROOT}/tmp/pytest/runs/${timestamp}-${suffix}"; ((suffix += 1)); done
private_dir "$run_dir"

collection_log="${run_dir}/collection.log"
private_file "$collection_log"
restore_caller_umask
set +e
run_pytest --collect-only -q tests >"$collection_log" 2>&1
collection_exit=$?
set -e
mapfile -t all_nodes < <(sed -E '/^[[:space:]]*$/d; /collected$/d; /^=+/d; /^-+/d; /^WARNING/d; /^ERROR/d' "$collection_log" | grep '^tests/' || true)
collection_count=${#all_nodes[@]}
if [[ "$collection_exit" -ne 0 || "$collection_count" -eq 0 ]]; then
  private_file "${run_dir}/failures.txt"
  printf 'kind\tbatch\ttest_nodeids\texit_code\tlog\tdetail\n' >"${run_dir}/failures.txt"
  printf 'harness_error\tcollection\t__collection__\t%s\t%s\tcollection failed or produced no nodeids\n' "$collection_exit" "$collection_log" >>"${run_dir}/failures.txt"
  private_file "${run_dir}/summary.txt"
  printf 'status=failed\nrun_dir=%s\nworkdir=%s\ncollection_count=%s\nselected_tests=0\ntotal_batches=0\ncompleted_batches=0\ntests_run=0\npassed=0\nfailed=0\nskipped=0\nharness_errors=1\n' "$run_dir" "$WORKDIR" "$collection_count" >"${run_dir}/summary.txt"
  echo "WSL Python lane collection failed; see ${run_dir}/collection.log" >&2
  exit 1
fi

start_index=$(( (START_AT - 1) * BATCH_SIZE ))
discovered_batches=$(( (collection_count + BATCH_SIZE - 1) / BATCH_SIZE ))
(( start_index < collection_count )) || die "--start-at ${START_AT} is beyond the ${discovered_batches} discovered batches"
if [[ -n "$LIMIT" ]]; then selected_nodes=("${all_nodes[@]:$start_index:$LIMIT}"); else selected_nodes=("${all_nodes[@]:$start_index}"); fi
selected_count=${#selected_nodes[@]}
(( selected_count > 0 )) || die "test selection is empty"

private_file "${run_dir}/test-nodeids.txt"
printf '%s\n' "${selected_nodes[@]}" >"${run_dir}/test-nodeids.txt"
private_file "${run_dir}/environment.txt"
printf 'repo_root=%s\nworkdir=%s\ncaller_umask=%s\nbatch_size=%s\nlive_ssh=%s\nseed_sync_live_ssh_tests=%s\ncollection_count=%s\nselected_tests=%s\nstart_at_batch=%s\nremote_files_dir=%s\n' \
  "${REPO_ROOT}" "${WORKDIR}" "${CALLER_UMASK}" "${BATCH_SIZE}" "${LIVE_SSH}" "${SEEDSYNC_LIVE_SSH_TESTS:-0}" "${collection_count}" "${selected_count}" "${START_AT}" "${REMOTE_FILES_DIR}" >"${run_dir}/environment.txt"
if [[ "$LIVE_SSH" -eq 1 ]]; then
  printf 'e2e_api_token_generated=%s\ne2e_browser_api_token_generated=%s\ne2e_browser_session_secret_generated=%s\n' \
    "${SEEDSYNC_E2E_API_TOKEN_GENERATED:-no}" "${SEEDSYNC_E2E_BROWSER_API_TOKEN_GENERATED:-no}" "${SEEDSYNC_E2E_BROWSER_SESSION_SECRET_GENERATED:-no}" >>"${run_dir}/environment.txt"
fi
total_batches=$(( (selected_count + BATCH_SIZE - 1) / BATCH_SIZE ))
private_file "${run_dir}/progress.tsv"
printf 'completed_batches\ttotal_batches\tcollection_count\tselected_tests\ttests_run\tpassed\tfailed\tskipped\tharness_errors\tlatest_batch\tfailing_batches\n' >"${run_dir}/progress.tsv"
private_file "${run_dir}/failures.txt"
printf 'kind\tbatch\ttest_nodeids\texit_code\tlog\tdetail\n' >"${run_dir}/failures.txt"
private_file "${run_dir}/progress.txt"
private_file "${run_dir}/summary.txt"
completed=0; tests_run=0; passed=0; failed=0; skipped=0; harness_errors=0; failing_batches=()

for ((offset = 0; offset < selected_count; offset += BATCH_SIZE)); do
  batch_index=$((offset / BATCH_SIZE + 1))
  batch_name=$(printf 'batch-%03d' "$batch_index")
  batch_xml="${run_dir}/${batch_name}.xml"
  batch_log="${run_dir}/${batch_name}.log"
  private_file "$batch_xml"
  private_file "$batch_log"
  batch_nodes=("${selected_nodes[@]:offset:BATCH_SIZE}")
  echo "[${batch_index}/${total_batches}] ${#batch_nodes[@]} tests"
  set +e
  run_pytest -q "${batch_nodes[@]}" --junitxml="$batch_xml" 2>&1 | tee "$batch_log"
  pipeline_status=("${PIPESTATUS[@]}")
  pytest_exit=${pipeline_status[0]:-125}
  tee_exit=${pipeline_status[1]:-125}
  set -e

  read -r junit_status batch_tests batch_failures batch_errors batch_skipped < <(python3 - "$batch_xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
try:
    root = ET.parse(sys.argv[1]).getroot()
except (FileNotFoundError, ET.ParseError):
    print("INVALID 0 0 0 0")
    raise SystemExit
suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
if not suites:
    print("INVALID 0 0 0 0")
    raise SystemExit
tests = failures = errors = skipped = 0
for suite in suites:
    tests += int(suite.attrib.get("tests", 0)); failures += int(suite.attrib.get("failures", 0))
    errors += int(suite.attrib.get("errors", 0)); skipped += int(suite.attrib.get("skipped", 0))
print("OK", tests, failures, errors, skipped)
PY
  )
  batch_harness=0
  if [[ "$junit_status" != "OK" || "$batch_tests" -eq 0 ]]; then batch_harness=1; fi
  batch_failed=$((batch_failures + batch_errors))
  if [[ "$pytest_exit" -ne 0 && "$batch_failed" -eq 0 ]]; then batch_harness=1; fi
  if [[ "$tee_exit" -ne 0 ]]; then batch_harness=1; fi
  batch_passed=$((batch_tests - batch_failed - batch_skipped)); (( batch_passed < 0 )) && batch_passed=0
  tests_run=$((tests_run + batch_tests)); passed=$((passed + batch_passed)); failed=$((failed + batch_failed)); skipped=$((skipped + batch_skipped)); harness_errors=$((harness_errors + batch_harness)); completed=$((completed + 1))
  if [[ "$batch_failed" -gt 0 || "$batch_harness" -gt 0 ]]; then
    failing_batches+=("$batch_name")
    if [[ "$batch_failed" -gt 0 ]]; then
      printf 'test_failure\t%s\t%s\t%s\t%s\tJUnit failures=%s errors=%s\n' "$batch_name" "${batch_nodes[*]}" "$pytest_exit" "$batch_log" "$batch_failures" "$batch_errors" >>"${run_dir}/failures.txt"
    fi
    if [[ "$batch_harness" -gt 0 ]]; then
      printf 'harness_error\t%s\t%s\t%s\t%s\tpytest exit=%s tee exit=%s or invalid/empty JUnit\n' "$batch_name" "${batch_nodes[*]}" "$pytest_exit" "$batch_log" "$pytest_exit" "$tee_exit" >>"${run_dir}/failures.txt"
    fi
  fi
  latest="${batch_name} pytest_exit=${pytest_exit} tee_exit=${tee_exit} tests=${batch_tests} failed=${batch_failed} skipped=${batch_skipped} harness=${batch_harness}"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$completed" "$total_batches" "$collection_count" "$selected_count" "$tests_run" "$passed" "$failed" "$skipped" "$harness_errors" "$latest" "${failing_batches[*]:-none}" >>"${run_dir}/progress.tsv"
  status=in_progress
  if [[ "$completed" -eq "$total_batches" ]]; then status=complete; [[ "$failed" -gt 0 || "$harness_errors" -gt 0 ]] && status=failed; fi
  printf 'status=%s\nrun_dir=%s\nworkdir=%s\ncollection_count=%s\nselected_tests=%s\ntotal_batches=%s\ncompleted_batches=%s\ntests_run=%s\npassed=%s\nfailed=%s\nskipped=%s\nharness_errors=%s\n' \
    "$status" "$run_dir" "$WORKDIR" "$collection_count" "$selected_count" "$total_batches" "$completed" "$tests_run" "$passed" "$failed" "$skipped" "$harness_errors" >"${run_dir}/progress.txt"
  cp -- "${run_dir}/progress.txt" "${run_dir}/summary.txt"
done

if [[ "$failed" -gt 0 || "$harness_errors" -gt 0 ]]; then
  echo "WSL Python lane failed: test failures=${failed}, harness errors=${harness_errors}. Artifacts: ${run_dir}" >&2
  exit 1
fi
echo "WSL Python lane completed: ${tests_run} tests (${passed} passed, ${skipped} skipped). Artifacts: ${run_dir}"
