#!/usr/bin/env bash
# Full retained-fixture upgrade verifier.  This creates only unique lab
# containers and tmp/upgrade-v086/runs/<RUN_ID> evidence; it never removes a
# prior run, image, network, backup, or fixture directory.
set -euo pipefail
umask 077

# The retained lab may delete the cwd inherited by this verifier.  Locate the
# repository from this tracked script and repair cwd before any subshell,
# redirection, or lab helper can inherit it.
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly ROOT_DIR="$(git -C "${SCRIPT_DIR}/../../../.." rev-parse --show-toplevel)"
cd -- "$ROOT_DIR" || { echo "upgrade-v086 ship-readiness: unable to enter repository root" >&2; exit 1; }
readonly LAB_DIR="${ROOT_DIR}/src/docker/test/upgrade-v086"
readonly LAB="${LAB_DIR}/lab.sh"
readonly HELPER="${LAB_DIR}/ship_readiness.py"
readonly BROWSER="${LAB_DIR}/ship_readiness_browser.mjs"
readonly CURRENT_PROXY_CONFIG="${LAB_DIR}/current-proxy-nginx.conf"
readonly LEGACY_COMMIT="ff2a1039935beccbbf7ec76134b41d2e91137742"
readonly PLAYWRIGHT_NODE_PATH_DEFAULT="/mnt/c/Users/johan/AppData/Local/Temp/codex-playwright-tools/node_modules"
BROWSER_SESSION_PID=""
BROWSER_SESSION_START_TIME=""
BROWSER_SESSION_RAW_DIR=""
BROWSER_SESSION_PROFILE_DIR=""
BROWSER_SESSION_EVIDENCE=""
BROWSER_SESSION_REAPED=1
PRIVATE_STAGING_ROOT=""
PRIVATE_SCREENSHOT_ROOT=""
PRIVATE_LOG_ROOT=""

die() { echo "upgrade-v086 ship-readiness: $*" >&2; exit 1; }
stabilize_repo_cwd() { cd -- "$ROOT_DIR" || die "unable to enter stable repository directory"; }
redact() { python "$HELPER" redact-stdin; }
run_id() { printf '%s' "${RUN_ID:-ship-$(date -u +%Y%m%dt%H%M%S)-$$}"; }
validate_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$ && "$1" == "${1,,}" ]] || die "RUN_ID must be lowercase 1-32 safe characters";
}
validate_port() {
  local name="$1" value="$2"
  [[ "$value" =~ ^[1-9][0-9]{0,4}$ ]] || die "$name must be a canonical decimal TCP port from 1 to 65535"
  (( 10#$value >= 1 && 10#$value <= 65535 )) || die "$name must be a canonical decimal TCP port from 1 to 65535"
}
run_dir() { printf '%s/tmp/upgrade-v086/runs/%s' "$ROOT_DIR" "$1"; }
evidence_dir() { printf '%s' "${SEEDSYNC_SHIP_EVIDENCE_DIR:-$(run_dir "$1")/evidence/ship-readiness}"; }
initialize_private_staging_root() {
  local id="$1" base candidate
  [[ "$ROOT_DIR" == /mnt/* ]] || return 0
  for base in /var/tmp /tmp; do
    [[ -d "$base" && ! -L "$base" ]] || continue
    candidate="$(mktemp -d "${base}/seedsync-upgrade-v086-private-${id}.XXXXXX")" || continue
    mkdir -m 700 "$candidate/screenshots" "$candidate/logs"
    chmod 700 "$candidate" "$candidate/screenshots" "$candidate/logs"
    if python - "$candidate" "$candidate/screenshots" "$candidate/logs" <<'PY'
import os, stat, sys
for raw in sys.argv[1:]:
    info = os.lstat(raw)
    if not (stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700):
        raise SystemExit(1)
PY
    then
      PRIVATE_STAGING_ROOT="$candidate"
      PRIVATE_SCREENSHOT_ROOT="$candidate/screenshots"
      PRIVATE_LOG_ROOT="$candidate/logs"
      return 0
    fi
  done
  die "unable to create a private WSL evidence staging root"
}
private_log_mount_dir() { printf '%s' "${PRIVATE_LOG_ROOT:-$(run_dir "$1")/logs}"; }
phase() { python "$HELPER" progress --output "$(evidence_dir "$1")/progress.json" --phase "$2" --state "$3" --detail "${4:-}"; }
matrix() { printf '%s/matrix.json' "$(evidence_dir "$1")"; }
row() { python "$HELPER" matrix-update --matrix "$(matrix "$1")" --row "$2" --status "$3" --artifact "$4" --detail "${5:-}"; }
timeout_seconds() {
  local variable="$1" fallback="$2" value="${!1:-$2}"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "$variable must be a positive integer number of seconds"
  printf '%s' "$value"
}
capture_command_diagnostics() {
  local id="$1" label="$2" command_log="$3" output="$(evidence_dir "$1")/${2}-diagnostics.txt"
  {
    printf 'run_id=%s\ncommand=%s\nat=%s\n' "$id" "$label" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '\ncontainers:\n'
    docker ps -a --filter "name=ship-$(printf '%s' "$id" | tr '[:upper:]' '[:lower:]')" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' || true
    printf '\nprocesses:\n'
    ps -eo pid=,ppid=,etime=,args= | grep -F "$id" || true
    printf '\ncommand-log-tail:\n'
    tail -n 120 "$command_log" 2>/dev/null || true
  } | redact > "$output"
  printf '%s' "$output"
}
bounded_command() {
  local id="$1" phase_name="$2" label="$3" limit="$4" command_log="$5"; shift 5
  local started="$SECONDS" watcher status diagnostics
  phase "$id" "$phase_name" running "${label}; timeout ${limit}s; log evidence/ship-readiness/$(basename "$command_log")"
  (
    while sleep 30; do
      phase "$id" "$phase_name" running "${label} still running after $((SECONDS - started))s of ${limit}s timeout"
    done
  ) &
  watcher="$!"
  if timeout --foreground --signal=TERM --kill-after=20s "${limit}s" "$@" 2>&1 | redact > "$command_log"; then
    status=0
  else
    status="${PIPESTATUS[0]}"
  fi
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true
  if [[ "$status" -ne 0 ]]; then
    diagnostics="$(capture_command_diagnostics "$id" "$label" "$command_log")"
    phase "$id" "$phase_name" failed "${label} exited ${status}; diagnostics evidence/ship-readiness/$(basename "$diagnostics")"
    return "$status"
  fi
  phase "$id" "$phase_name" running "${label} completed in $((SECONDS - started))s"
}
playwright_node_path() { printf '%s' "${SEEDSYNC_PLAYWRIGHT_NODE_PATH:-$PLAYWRIGHT_NODE_PATH_DEFAULT}"; }
node_binary() {
  if [[ -n "${SEEDSYNC_NODE_BIN:-}" ]]; then printf '%s' "$SEEDSYNC_NODE_BIN"; return; fi
  if command -v node >/dev/null 2>&1; then command -v node; return; fi
  local candidate
  candidate="$(find "$HOME/.nvm/versions/node" -maxdepth 3 -type f -path '*/bin/node' -print 2>/dev/null | sort -V | tail -n 1)"
  [[ -n "$candidate" ]] || die "a WSL Node runtime is required; set SEEDSYNC_NODE_BIN"
  printf '%s' "$candidate"
}
run_browser() {
  local id="$1" node_path node_bin; shift; node_path="$(playwright_node_path)"; node_bin="$(node_binary)"
  [[ -d "$node_path" ]] || die "Playwright NODE_PATH is unavailable: $node_path"
  ( umask 077; NODE_PATH="$node_path" SEEDSYNC_PLAYWRIGHT_MODULE=playwright SEEDSYNC_SHIP_EVIDENCE_HELPER="$HELPER" SEEDSYNC_SHIP_RUN_ID="$id" SEEDSYNC_SHIP_PRIVATE_SCREENSHOT_ROOT="$PRIVATE_SCREENSHOT_ROOT" "$node_bin" "$BROWSER" "$@" )
}
run_browser_bounded() {
  local id="$1" phase_name="$2" base_url="$3" evidence="$4" mode="$5" limit raw_dir profile_dir raw_stdout raw_stderr diagnostic failure status started="$SECONDS" node_path node_bin
  limit="$(timeout_seconds SEEDSYNC_SHIP_BROWSER_TIMEOUT_SECONDS 90)"
  node_path="$(playwright_node_path)"; node_bin="$(node_binary)"
  [[ -d "$node_path" && -x "$node_bin" ]] || die "Playwright Node runtime is unavailable"
  raw_dir="$(mktemp -d /tmp/seedsync-browser-command.XXXXXX)" || die "unable to create private browser command workspace"
  chmod 700 "$raw_dir"; profile_dir="$raw_dir/browser-profile"; raw_stdout="$raw_dir/stdout"; raw_stderr="$raw_dir/stderr"
  phase "$id" "$phase_name" running "browser launch ${mode}; timeout ${limit}s"
  if timeout --foreground --signal=TERM --kill-after=20s "${limit}s" env NODE_PATH="$node_path" SEEDSYNC_PLAYWRIGHT_MODULE=playwright SEEDSYNC_SHIP_EVIDENCE_HELPER="$HELPER" SEEDSYNC_SHIP_RUN_ID="$id" SEEDSYNC_SHIP_PRIVATE_SCREENSHOT_ROOT="$PRIVATE_SCREENSHOT_ROOT" SEEDSYNC_BROWSER_PROFILE_DIR="$profile_dir" "$node_bin" "$BROWSER" "$base_url" "$evidence" "$mode" >"$raw_stdout" 2>"$raw_stderr"; then status=0; else status="$?"; fi
  diagnostic="$evidence/browser-${mode}-command-diagnostics.txt"
  {
    printf 'schema=1\nphase=%s\nmode=%s\nexit_code=%s\ntimed_out=%s\n' "$phase_name" "$mode" "$status" "$([[ "$status" == 124 ]] && printf true || printf false)"
    printf '\nstdout-tail:\n'; tail -c 16384 "$raw_stdout" 2>/dev/null || true
    printf '\nstderr-tail:\n'; tail -c 16384 "$raw_stderr" 2>/dev/null || true
  } | redact > "$diagnostic"
  chmod 600 "$diagnostic"; rm -f -- "$raw_stdout" "$raw_stderr"; rm -rf -- "$profile_dir"; rmdir -- "$raw_dir" 2>/dev/null || true
  if [[ "$status" -ne 0 ]]; then
    failure="$evidence/browser-${mode}-failure.json"
    if [[ "$status" == 124 ]]; then
      python "$HELPER" browser-command-failure --output "$failure" --phase "$phase_name" --mode "$mode" --exit-code "$status" --timed-out
    else
      python "$HELPER" browser-command-failure --output "$failure" --phase "$phase_name" --mode "$mode" --exit-code "$status"
    fi
    FIRST_FAILURE_DETAIL="${FIRST_FAILURE_DETAIL:-${phase_name}: browser ${mode} exited ${status}; diagnostics evidence/ship-readiness/$(basename "$diagnostic")}"
    phase "$id" "$phase_name" failed "$FIRST_FAILURE_DETAIL"
    return "$status"
  fi
  phase "$id" "$phase_name" running "browser ${mode} completed in $((SECONDS - started))s"
}
browser_process_is_session_leader() {
  local pid="$1" start_time="$2"
  [[ "$pid" =~ ^[1-9][0-9]*$ && "$start_time" =~ ^[1-9][0-9]*$ ]] || return 1
  python "$HELPER" session-leader-status --pid "$pid" --start-time "$start_time" >/dev/null 2>&1
}
browser_process_has_session_identity() {
  local pid="$1" start_time="$2"
  [[ "$pid" =~ ^[1-9][0-9]*$ && "$start_time" =~ ^[1-9][0-9]*$ ]] || return 1
  python "$HELPER" session-leader-status --allow-zombie --pid "$pid" --start-time "$start_time" >/dev/null 2>&1
}
browser_session_descendant_status() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]] || return 1
  # 0=present, 1=empty, 2=inconclusive; callers must not treat 2 as empty.
  python "$HELPER" session-descendants-status --leader "$1" >/dev/null 2>&1
}
kill_browser_session_descendants() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]] || return 1
  # The helper uses final-')' proc parsing and pidfds for all signals.
  python "$HELPER" pidfd-kill-session-descendants --leader "$1" >/dev/null 2>&1
}
kill_browser_session_leader() {
  local pid="$1" start_time="$2"
  [[ "$pid" =~ ^[1-9][0-9]*$ && "$start_time" =~ ^[1-9][0-9]*$ ]] || return 1
  python "$HELPER" pidfd-kill-session-leader --pid "$pid" --start-time "$start_time" >/dev/null 2>&1
}
signal_browser_session_leader() {
  local pid="$1" start_time="$2" control_signal="$3"
  [[ "$pid" =~ ^[1-9][0-9]*$ && "$start_time" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$control_signal" == TERM || "$control_signal" == USR1 ]] || return 1
  python "$HELPER" pidfd-signal-session-leader --pid "$pid" --start-time "$start_time" --signal "$control_signal" >/dev/null 2>&1
}
publish_browser_session_log() {
  local raw_log="$1" evidence="$2" output="$evidence/browser-session.log" temporary
  [[ -d "$evidence" ]] || return 1
  [[ -f "$raw_log" || ! -f "$output" ]] || return 0
  temporary="$(mktemp "$evidence/.browser-session.log.XXXXXX")" || return 1
  if [[ -f "$raw_log" ]] && [[ "${SEEDSYNC_BROWSER_SESSION_REDACTOR_FAIL:-}" != 1 ]] && python "$HELPER" redact-stdin < "$raw_log" > "$temporary" 2>/dev/null; then
    chmod 600 "$temporary"
  else
    rm -f -- "$temporary"
    printf '%s\n' '{"schema":1,"status":"redaction-failed","artifact":"browser-session.log"}' > "$temporary"
    chmod 600 "$temporary"
  fi
  mv -f -- "$temporary" "$output"
  chmod 600 "$output"
}
cleanup_browser_session_workspace() {
  local raw_dir="$1" raw_log="$2" profile_dir="$3" evidence="$4"
  [[ "$raw_dir" == /tmp/seedsync-browser-session.* && "$profile_dir" == "$raw_dir/browser-profile" ]] || return 1
  publish_browser_session_log "$raw_log" "$evidence" || true
  rm -f -- "$raw_log"
  rm -rf -- "$profile_dir"
  rmdir -- "$raw_dir" 2>/dev/null || true
  [[ ! -e "$raw_dir" ]]
}
browser_claim_reuse_worker() {
  local base="$1" evidence="$2" raw_dir="$3" raw_log="$4" profile_dir="$5" node_path="$6" node_bin="$7" node_pid="" node_status=1 shutdown_requested=0 published=0
  [[ "$raw_dir" == /tmp/seedsync-browser-session.* && "$profile_dir" == "$raw_dir/browser-profile" ]] || exit 1
  request_browser_worker_shutdown() {
    shutdown_requested=1
    if [[ -n "$node_pid" ]] && kill -0 "$node_pid" 2>/dev/null; then kill -TERM "$node_pid" 2>/dev/null || true; fi
    if (( ! published )); then publish_browser_session_log "$raw_log" "$evidence" || true; published=1; fi
  }
  trap request_browser_worker_shutdown USR1 HUP INT TERM
  if [[ "${SEEDSYNC_BROWSER_SESSION_PROBE:-}" == stubborn ]]; then
    printf '%s\n' "$BASHPID" > "$evidence/browser-session-probe-worker.pid"
    bash -s -- "$raw_log" "$profile_dir" "$evidence/browser-session-probe-descendant.pid" "${SEEDSYNC_BROWSER_SESSION_PROBE_CONTINUE_FILE:-}" <<'SH' & node_pid="$!"
raw_log="$1"; profile_dir="$2"; descendant_file="$3"; continue_file="$4"
mkdir -p -- "$profile_dir"
printf 'api_key=browser-session-probe-secret\n' > "$raw_log"
bash -c 'trap "" TERM; sleep 30' & descendant="$!"
printf '%s\n' "$descendant" > "$descendant_file"
# The parent self-check releases this probe only after both recorded identities
# pass strict same-session/process-group validation.  This keeps the probe
# membership snapshot live instead of racing a short fixed worker lifetime.
if [[ -n "$continue_file" ]]; then
  for (( attempt = 0; attempt < 120; attempt++ )); do
    [[ -e "$continue_file" ]] && break
    sleep .05
  done
fi
SH
  else
    NODE_PATH="$node_path" SEEDSYNC_PLAYWRIGHT_MODULE=playwright SEEDSYNC_SHIP_EVIDENCE_HELPER="$HELPER" SEEDSYNC_SHIP_RUN_ID="$SEEDSYNC_SHIP_RUN_ID" SEEDSYNC_SHIP_PRIVATE_SCREENSHOT_ROOT="${SEEDSYNC_SHIP_PRIVATE_SCREENSHOT_ROOT:-}" SEEDSYNC_BROWSER_PROFILE_DIR="$profile_dir" SEEDSYNC_BROWSER_HANDOVER_RECOVERY=1 "$node_bin" "$BROWSER" "$base" "$evidence" claim-reuse > "$raw_log" 2>&1 & node_pid="$!"
  fi
  while true; do
    if wait "$node_pid"; then node_status=0; break; fi
    node_status="$?"
    kill -0 "$node_pid" 2>/dev/null || break
  done
  (( published )) || publish_browser_session_log "$raw_log" "$evidence" || true
  exit "$node_status"
}
browser_claim_reuse_supervisor() {
  local base="$1" evidence="$2" raw_dir="$3" raw_log="$4" profile_dir="$5" node_path="$6" node_bin="$7" worker_pid="" worker_status=1 shutdown_requested=0 deadline=0 attempt descendant_status=0
  [[ "$raw_dir" == /tmp/seedsync-browser-session.* && "$profile_dir" == "$raw_dir/browser-profile" ]] || exit 1
  request_browser_supervisor_shutdown() {
    shutdown_requested=1
    deadline=$((SECONDS + 6))
    if [[ -n "$worker_pid" ]] && kill -0 "$worker_pid" 2>/dev/null; then kill -USR1 "$worker_pid" 2>/dev/null || true; fi
  }
  trap request_browser_supervisor_shutdown USR1 HUP INT TERM
  "$BASH" "$SCRIPT_DIR/ship_readiness.sh" browser-claim-worker "$base" "$evidence" "$raw_dir" "$raw_log" "$profile_dir" "$node_path" "$node_bin" & worker_pid="$!"
  while true; do
    if wait "$worker_pid"; then worker_status=0; break; fi
    worker_status="$?"
    kill -0 "$worker_pid" 2>/dev/null || break
  done
  deadline=$((SECONDS + 6))
  while true; do
    if browser_session_descendant_status "$BASHPID"; then descendant_status=0; else descendant_status="$?"; fi
    [[ "$descendant_status" == 1 ]] && break
    [[ "$descendant_status" == 0 ]] || { publish_browser_session_log "$raw_log" "$evidence" || true; exit 1; }
    if (( SECONDS >= deadline )); then
      # This session is owned by the parent-tracked supervisor; kill only
      # verified members of this exact session/group, never a name pattern.
      if ! kill_browser_session_descendants "$BASHPID"; then
        publish_browser_session_log "$raw_log" "$evidence" || true
        exit 1
      fi
      for (( attempt = 0; attempt < 120; attempt++ )); do
        if browser_session_descendant_status "$BASHPID"; then descendant_status=0; else descendant_status="$?"; fi
        [[ "$descendant_status" == 1 ]] && break
        [[ "$descendant_status" == 0 ]] || exit 1
        sleep .05
      done
      if browser_session_descendant_status "$BASHPID"; then exit 1; else descendant_status="$?"; fi
      [[ "$descendant_status" == 1 ]] || exit 1
      break
    fi
    sleep .05
  done
  publish_browser_session_log "$raw_log" "$evidence" || true
  if [[ ! -f "$evidence/browser-claim-ready.json" ]]; then
    publish_browser_session_failure "$evidence" after-first-claim "$worker_status" || true
  fi
  cleanup_browser_session_workspace "$raw_dir" "$raw_log" "$profile_dir" "$evidence" || exit 1
  exit "$worker_status"
}
publish_browser_session_failure() {
  local evidence="$1" phase="$2" status="$3" output="$evidence/browser-session-failure.json" browser_evidence=false log_present=false
  [[ "$phase" == after-first-claim && "$status" =~ ^[0-9]+$ && -d "$evidence" ]] || return 1
  [[ -f "$evidence/browser.json" ]] && browser_evidence=true
  [[ -s "$evidence/browser-session.log" ]] && log_present=true
  python - "$output" "$phase" "$status" "$browser_evidence" "$log_present" <<'PY'
import json, os, sys
output, phase, status, browser_evidence, log_present = sys.argv[1:]
payload = {
    "schema": 1,
    "phase": phase,
    "status": "failed-before-ready",
    "exit_code": int(status),
    "ready_marker": False,
    "browser_evidence_present": browser_evidence == "true",
    "redacted_session_log_present": log_present == "true",
}
temporary = output + ".tmp"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True)
    stream.write("\n")
os.chmod(temporary, 0o600)
os.replace(temporary, output)
PY
}
start_browser_claim_reuse() {
  local id="$1" base="$2" evidence="$3" node_path node_bin raw_dir raw_log profile_dir
  node_path="$(playwright_node_path)"; node_bin="$(node_binary)"
  [[ -d "$node_path" ]] || die "Playwright NODE_PATH is unavailable: $node_path"
  raw_dir="$(mktemp -d /tmp/seedsync-browser-session.XXXXXX)" || die "unable to create private browser-session workspace"
  chmod 700 "$raw_dir"; raw_log="$raw_dir/raw.log"; profile_dir="$raw_dir/browser-profile"
  printf '%s\n' "$raw_dir" > "$evidence/browser-session.raw-dir"
  BROWSER_SESSION_RAW_DIR="$raw_dir"
  BROWSER_SESSION_PROFILE_DIR="$profile_dir"
  BROWSER_SESSION_EVIDENCE="$evidence"
  BROWSER_SESSION_REAPED=0
  # The supervisor is the sole session leader and its worker stays a direct
  # child in that session.  Parent recovery therefore has one verified group.
  SEEDSYNC_SHIP_RUN_ID="$id" SEEDSYNC_SHIP_PRIVATE_SCREENSHOT_ROOT="$PRIVATE_SCREENSHOT_ROOT" setsid "$BASH" "$SCRIPT_DIR/ship_readiness.sh" browser-claim-supervisor "$base" "$evidence" "$raw_dir" "$raw_log" "$profile_dir" "$node_path" "$node_bin" &
  BROWSER_SESSION_PID="$!"
  BROWSER_SESSION_START_TIME="$(python "$HELPER" proc-start-time --pid "$BROWSER_SESSION_PID" 2>/dev/null || true)"
  if ! browser_session_is_known_live_child; then
    # An unverified PID is never signaled by the shell.  A valid start-time
    # still permits the helper to bind a pidfd and independently prove the
    # leader identity before TERM; otherwise leave the private workspace for
    # failure diagnosis and only reap a child that has already exited.
    if [[ "$BROWSER_SESSION_START_TIME" =~ ^[1-9][0-9]*$ ]]; then
      signal_browser_session_leader "$BROWSER_SESSION_PID" "$BROWSER_SESSION_START_TIME" TERM || true
    fi
    if ! kill -0 "$BROWSER_SESSION_PID" 2>/dev/null; then wait "$BROWSER_SESSION_PID" 2>/dev/null || true; fi
    die "unable to establish browser-session child identity"
  fi
  printf '%s\n' "$BROWSER_SESSION_PID" > "$evidence/browser-session.pid"
}
browser_session_is_known_live_child() {
  [[ "$BROWSER_SESSION_REAPED" == 0 && "$BROWSER_SESSION_PID" =~ ^[1-9][0-9]*$ && "$BROWSER_SESSION_START_TIME" =~ ^[1-9][0-9]*$ ]] || return 1
  browser_process_is_session_leader "$BROWSER_SESSION_PID" "$BROWSER_SESSION_START_TIME"
}
browser_session_has_known_identity() {
  [[ "$BROWSER_SESSION_REAPED" == 0 && "$BROWSER_SESSION_PID" =~ ^[1-9][0-9]*$ && "$BROWSER_SESSION_START_TIME" =~ ^[1-9][0-9]*$ ]] || return 1
  browser_process_has_session_identity "$BROWSER_SESSION_PID" "$BROWSER_SESSION_START_TIME"
}
cleanup_browser_claim_reuse() {
  local attempt descendant_status=0
  [[ "$BROWSER_SESSION_REAPED" == 0 ]] || return 0
  if browser_session_is_known_live_child; then
    signal_browser_session_leader "$BROWSER_SESSION_PID" "$BROWSER_SESSION_START_TIME" USR1 || return 1
  fi
  for (( attempt = 0; attempt < 120; attempt++ )); do
    browser_session_is_known_live_child || break
    sleep .05
  done
  # If the supervisor died, its unreaped zombie still reserves this verified
  # session ID.  Parent may therefore kill only that exact known group.
  if browser_session_has_known_identity; then
    if browser_session_descendant_status "$BROWSER_SESSION_PID"; then
      kill_browser_session_descendants "$BROWSER_SESSION_PID" || return 1
    else
      attempt="$?"
      [[ "$attempt" == 1 ]] || return 1
    fi
  else
    # A normal supervisor can finish its own pidfd cleanup and be reaped by
    # Bash before this parent observes the final state.  Only accept that
    # race when the exact private workspace is already gone; otherwise the
    # unknown session remains protected and this cleanup fails closed.
    if [[ ! -e "$BROWSER_SESSION_RAW_DIR" ]]; then
      BROWSER_SESSION_REAPED=1
      rm -f -- "$BROWSER_SESSION_EVIDENCE/browser-session.raw-dir"
      BROWSER_SESSION_PID=""; BROWSER_SESSION_START_TIME=""; BROWSER_SESSION_RAW_DIR=""; BROWSER_SESSION_PROFILE_DIR=""; BROWSER_SESSION_EVIDENCE=""
      return 0
    fi
    return 1
  fi
  for (( attempt = 0; attempt < 120; attempt++ )); do
    if browser_session_descendant_status "$BROWSER_SESSION_PID"; then :; else descendant_status="$?"; fi
    [[ "${descendant_status:-0}" == 1 ]] && break
    [[ "${descendant_status:-0}" == 0 ]] || return 1
    sleep .05
  done
  if browser_session_descendant_status "$BROWSER_SESSION_PID"; then return 1; else descendant_status="$?"; fi
  [[ "$descendant_status" == 1 ]] || return 1
  if browser_session_is_known_live_child; then kill_browser_session_leader "$BROWSER_SESSION_PID" "$BROWSER_SESSION_START_TIME" || return 1; fi
  wait "$BROWSER_SESSION_PID" 2>/dev/null || true
  BROWSER_SESSION_REAPED=1
  [[ "$BROWSER_SESSION_RAW_DIR" == /tmp/seedsync-browser-session.* && "$BROWSER_SESSION_PROFILE_DIR" == "$BROWSER_SESSION_RAW_DIR/browser-profile" ]] || die "browser-session workspace escaped its private /tmp contract"
  cleanup_browser_session_workspace "$BROWSER_SESSION_RAW_DIR" "$BROWSER_SESSION_RAW_DIR/raw.log" "$BROWSER_SESSION_PROFILE_DIR" "$BROWSER_SESSION_EVIDENCE" || die "browser-session raw workspace was not cleaned: $BROWSER_SESSION_RAW_DIR"
  rm -f -- "$BROWSER_SESSION_EVIDENCE/browser-session.raw-dir"
  BROWSER_SESSION_PID=""; BROWSER_SESSION_START_TIME=""; BROWSER_SESSION_RAW_DIR=""; BROWSER_SESSION_PROFILE_DIR=""; BROWSER_SESSION_EVIDENCE=""
}
wait_browser_claim_reuse_ready() {
  local evidence="$1" attempts=0
  [[ "$BROWSER_SESSION_REAPED" == 0 && "$evidence" == "$BROWSER_SESSION_EVIDENCE" ]] || die "browser-session parent state is unavailable"
  until [[ -f "$evidence/browser-claim-ready.json" ]]; do
    if ! browser_session_is_known_live_child; then
      cleanup_browser_claim_reuse
      die "in-memory browser claim session exited before restart handoff; see $evidence/browser-session.log"
    fi
    attempts=$((attempts + 1))
    if (( attempts >= 240 )); then
      cleanup_browser_claim_reuse
      die "in-memory browser claim session did not become ready"
    fi
    sleep .5
  done
}
request_browser_stability() {
  local id="$1" evidence="$2" request="$2/browser-stability-request.json"
  python - "$id" "$request" <<'PY'
import json, os, sys, time
run_id, output = sys.argv[1:]
payload = {"schema": 1, "run_id": run_id, "request_kind": "pre-restart-stability", "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
temporary = output + ".tmp"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True); stream.write("\n")
os.chmod(temporary, 0o600); os.replace(temporary, output)
PY
}
wait_browser_stability_ready() {
  local id="$1" evidence="$2" attempts=0 ready="$2/browser-stability-ready.json" request="$2/browser-stability-request.json"
  [[ "$BROWSER_SESSION_REAPED" == 0 && "$evidence" == "$BROWSER_SESSION_EVIDENCE" ]] || die "browser-session parent state is unavailable"
  until [[ -f "$ready" ]]; do
    if ! browser_session_is_known_live_child; then
      cleanup_browser_claim_reuse
      die "in-memory browser session exited before stability handoff; see $evidence/browser-session.log"
    fi
    attempts=$((attempts + 1)); (( attempts < 360 )) || { cleanup_browser_claim_reuse; die "browser stability checkpoint did not become ready"; }
    sleep .5
  done
  python - "$id" "$request" "$ready" <<'PY'
import json, re, sys
run_id, request_path, ready_path = sys.argv[1:]
request = json.load(open(request_path, encoding="utf-8"))
if (not isinstance(request, dict) or set(request) != {"schema", "run_id", "request_kind", "requested_at"}
        or request.get("schema") != 1 or request.get("run_id") != run_id
        or request.get("request_kind") != "pre-restart-stability"
        or not isinstance(request.get("requested_at"), str)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", request["requested_at"])):
    raise SystemExit("browser stability request is invalid")
value = json.load(open(ready_path, encoding="utf-8"))
expected = {"schema", "run_id", "request_kind", "requested_at", "error_generation", "runtime_error_count", "diagnostic_failure_count", "model_rows", "status", "stability_window_ms", "ready_at"}
if (not isinstance(value, dict) or set(value) != expected or value.get("schema") != 1 or value.get("run_id") != run_id
        or value.get("request_kind") != "pre-restart-stability" or value.get("requested_at") != request["requested_at"] or value.get("status") != 200
        or not isinstance(value.get("ready_at"), str)
        or any(type(value.get(key)) is not int or value[key] < 0 for key in ("error_generation", "runtime_error_count", "diagnostic_failure_count", "model_rows", "stability_window_ms"))
        or value["runtime_error_count"] != 0 or value["diagnostic_failure_count"] != 0):
    raise SystemExit("browser stability ready evidence is invalid")
print(value["error_generation"])
PY
}
report_browser_restart_invalidation() {
  local evidence="$1" name
  for name in browser-stability-invalid.json browser-restart-invalid.json; do
    if [[ -f "$evidence/$name" ]]; then
      printf 'browser restart handoff invalidation (%s):\n' "$name" >&2
      cat "$evidence/$name" >&2
      return 0
    fi
  done
  return 1
}
arm_browser_restart() {
  local id="$1" evidence="$2" generation="$3" output="$2/browser-restart-arm.json"
  python - "$id" "$generation" "$output" <<'PY'
import json, os, sys
run_id, generation, output = sys.argv[1:]
payload = {"schema": 1, "run_id": run_id, "stability_generation": int(generation), "arm_generation": int(generation) + 1, "restart_armed": True}
temporary = output + ".tmp"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True); stream.write("\n")
os.chmod(temporary, 0o600); os.replace(temporary, output)
print(payload["arm_generation"])
PY
}
wait_browser_restart_arm_ack() {
  local id="$1" evidence="$2" generation="$3" arm_generation="$4" attempts=0 arm="$2/browser-restart-arm.json" ack="$2/browser-restart-arm-ack.json"
  [[ "$BROWSER_SESSION_REAPED" == 0 && "$evidence" == "$BROWSER_SESSION_EVIDENCE" ]] || die "browser-session parent state is unavailable"
  until [[ -f "$ack" ]]; do
    if ! browser_session_is_known_live_child; then
      report_browser_restart_invalidation "$evidence" || true
      cleanup_browser_claim_reuse
      die "in-memory browser session exited before restart arm acknowledgement; see $evidence/browser-session.log"
    fi
    attempts=$((attempts + 1)); (( attempts < 240 )) || { report_browser_restart_invalidation "$evidence" || true; cleanup_browser_claim_reuse; die "browser restart arm acknowledgement did not become ready"; }
    sleep .5
  done
  if ! python - "$id" "$generation" "$arm_generation" "$arm" "$ack" <<'PY'
import json, sys
run_id, generation, arm_generation, arm_path, ack_path = sys.argv[1:]
generation, arm_generation = int(generation), int(arm_generation)
arm = json.load(open(arm_path, encoding="utf-8"))
ack = json.load(open(ack_path, encoding="utf-8"))
expected_arm = {"schema", "run_id", "stability_generation", "arm_generation", "restart_armed"}
expected_ack = expected_arm | {"acknowledged", "acknowledged_error_generation", "acknowledged_epoch_ms"}
if (not isinstance(arm, dict) or set(arm) != expected_arm or arm != {"schema": 1, "run_id": run_id, "stability_generation": generation, "arm_generation": arm_generation, "restart_armed": True}):
    raise SystemExit("browser restart arm is invalid")
if (not isinstance(ack, dict) or set(ack) != expected_ack or ack.get("schema") != 1 or ack.get("run_id") != run_id
        or ack.get("stability_generation") != generation or ack.get("arm_generation") != arm_generation
        or ack.get("restart_armed") is not True or ack.get("acknowledged") is not True
        or ack.get("acknowledged_error_generation") != generation
        or type(ack.get("acknowledged_epoch_ms")) is not int or ack["acknowledged_epoch_ms"] <= 0):
    raise SystemExit("browser restart arm acknowledgement is invalid")
PY
  then
    report_browser_restart_invalidation "$evidence" || true
    cleanup_browser_claim_reuse
    die "browser restart arm acknowledgement is invalid"
  fi
}
publish_browser_restart_stop_dispatch() {
  local id="$1" evidence="$2" generation="$3" arm_generation="$4" ack="$2/browser-restart-arm-ack.json" output="$2/browser-restart-stop-dispatch.json"
  python - "$id" "$generation" "$arm_generation" "$ack" "$output" <<'PY'
import json, os, sys, time
run_id, generation, arm_generation, ack_path, output = sys.argv[1:]
generation, arm_generation = int(generation), int(arm_generation)
ack = json.load(open(ack_path, encoding="utf-8"))
if (not isinstance(ack, dict) or ack.get("schema") != 1 or ack.get("run_id") != run_id
        or ack.get("stability_generation") != generation or ack.get("arm_generation") != arm_generation
        or ack.get("acknowledged") is not True or ack.get("acknowledged_error_generation") != generation):
    raise SystemExit("browser restart arm acknowledgement is invalid before stop dispatch")
payload = {"schema": 1, "run_id": run_id, "stability_generation": generation, "arm_generation": arm_generation,
           "acknowledged_error_generation": generation, "restart_stop_dispatched": True,
           "stop_dispatch_epoch_ms": time.time_ns() // 1_000_000}
temporary = output + ".tmp"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True); stream.write("\n")
os.chmod(temporary, 0o600); os.replace(temporary, output)
PY
}
finish_browser_claim_reuse() {
  local evidence="$1" generation="$2" arm_generation="$3" status
  [[ "$BROWSER_SESSION_REAPED" == 0 && "$evidence" == "$BROWSER_SESSION_EVIDENCE" ]] || die "browser-session parent state is unavailable"
  python - "$ACTIVE_RUN_ID" "$generation" "$arm_generation" "$evidence/browser-restart-request.json" <<'PY'
import json, os, sys
run_id, generation, arm_generation, output = sys.argv[1:]
payload = {"schema": 1, "run_id": run_id, "stability_generation": int(generation), "arm_generation": int(arm_generation), "restart_requested": True}
temporary = output + ".tmp"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True); stream.write("\n")
os.chmod(temporary, 0o600); os.replace(temporary, output)
PY
  if wait "$BROWSER_SESSION_PID"; then status=0; else status="$?"; fi
  if [[ "$status" -ne 0 ]]; then
    report_browser_restart_invalidation "$evidence" || true
    cleanup_browser_claim_reuse
    die "in-memory browser reuse session failed; see $evidence/browser-session.log"
  fi
  if [[ ! -f "$evidence/browser-reuse.json" ]]; then
    report_browser_restart_invalidation "$evidence" || true
    cleanup_browser_claim_reuse
    die "in-memory browser reuse session did not retain browser-reuse evidence"
  fi
  cleanup_browser_claim_reuse
}
browser_dispatch_self_check() {
  local node_path node_bin output
  node_path="$(playwright_node_path)"; node_bin="$(node_binary)"
  output="$(NODE_PATH="$node_path" SEEDSYNC_PLAYWRIGHT_MODULE=playwright "$node_bin" "$BROWSER" --dispatch-check)"
  python - "$output" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
if not (payload["interpreter"].endswith("node") or "/node" in payload["interpreter"]):
    raise SystemExit("browser dispatch did not use Node")
if "playwright" not in payload["playwright"]:
    raise SystemExit("browser dispatch did not resolve Playwright")
PY
}
browser_shutdown_self_check() {
  local node_bin timeout_result failure_result
  node_bin="$(node_binary)"
  timeout_result="$("$node_bin" "$BROWSER" --shutdown-self-check timeout)"
  failure_result="$("$node_bin" "$BROWSER" --shutdown-self-check failure)"
  python - "$timeout_result" "$failure_result" <<'PY'
import json, sys
for result, expected in zip(sys.argv[1:], ("timed out", "simulated close failure")):
    payload = json.loads(result)
    if payload.get("fallback") is not True or expected not in payload.get("reason", ""):
        raise SystemExit("browser shutdown fallback self-check did not report " + expected)
PY
}
browser_readiness_policy_self_check() {
  python - "$BROWSER" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
required = (
    "waitUntil: 'domcontentloaded'", "navigateReady", "requireFixtureRows",
    "requireApi", "captureFailure", "bodySnippet", "consoleAndPageErrors",
)
if "network" + "idle" in source:
    raise SystemExit("browser verifier must not wait for network idle")
missing = [item for item in required if item not in source]
if missing:
    raise SystemExit("browser readiness contract is missing: " + ", ".join(missing))
PY
}
browser_session_temp_cleanup_self_check() {
  local failed_dir terminated_dir child attempt
  failed_dir="$(mktemp -d /tmp/seedsync-browser-session-self-check-failed.XXXXXX)"
  if (
    raw_log="$failed_dir/raw.log"
    cleanup() { rm -f -- "$raw_log"; rmdir -- "$failed_dir" 2>/dev/null || true; }
    trap cleanup EXIT HUP INT TERM
    printf 'raw failure canary\n' > "$raw_log"
    false
  ); then
    die "browser-session failure cleanup self-check unexpectedly passed"
  fi
  [[ ! -e "$failed_dir" ]] || die "browser-session failure cleanup left raw workspace: $failed_dir"
  terminated_dir="$(mktemp -d /tmp/seedsync-browser-session-self-check-term.XXXXXX)"
  (
    raw_log="$terminated_dir/raw.log"
    worker_pid=""
    cleanup() {
      if [[ -n "$worker_pid" ]] && kill -0 "$worker_pid" 2>/dev/null; then kill -TERM "$worker_pid" 2>/dev/null || true; fi
      [[ -n "$worker_pid" ]] && wait "$worker_pid" 2>/dev/null || true
      rm -f -- "$raw_log"; rmdir -- "$terminated_dir" 2>/dev/null || true
    }
    trap cleanup EXIT
    trap 'cleanup; exit 143' TERM
    printf 'raw termination canary\n' > "$raw_log"
    sleep 30 & worker_pid="$!"
    wait "$worker_pid"
  ) & child="$!"
  for (( attempt = 0; attempt < 40; attempt++ )); do
    [[ -f "$terminated_dir/raw.log" ]] && break
    sleep .05
  done
  [[ -f "$terminated_dir/raw.log" ]] || die "browser-session termination self-check did not create exact raw target"
  kill -TERM "$child"
  if wait "$child"; then die "browser-session termination self-check unexpectedly passed"; fi
  [[ ! -e "$terminated_dir" ]] || die "browser-session termination cleanup left raw workspace: $terminated_dir"
}
browser_parent_cleanup_self_check() {
  local evidence raw_dir profile_dir child descendant unrelated hint_dir failed_evidence failed_raw failed_profile normal_evidence normal_raw normal_profile normal_child normal_descendant normal_started normal_status crash_evidence crash_raw crash_profile crash_child crash_descendant signal_dir signal_profile signal_evidence signal_child signal_descendant
  launch_browser_supervisor_probe() {
    local probe_raw="$1" probe_profile="$2" probe_evidence="$3" redactor_mode="$4" attempt worker_probe descendant_probe identity probe_continue
    BROWSER_SESSION_RAW_DIR="$probe_raw"; BROWSER_SESSION_PROFILE_DIR="$probe_profile"; BROWSER_SESSION_EVIDENCE="$probe_evidence"
    BROWSER_SESSION_REAPED=0
    probe_continue="$probe_evidence/browser-session-probe-continue"
    rm -f -- "$probe_continue"
    setsid env SEEDSYNC_BROWSER_SESSION_PROBE=stubborn SEEDSYNC_BROWSER_SESSION_PROBE_CONTINUE_FILE="$probe_continue" SEEDSYNC_BROWSER_SESSION_REDACTOR_FAIL="$redactor_mode" "$BASH" "$SCRIPT_DIR/ship_readiness.sh" browser-claim-supervisor probe "$probe_evidence" "$probe_raw" "$probe_raw/raw.log" "$probe_profile" ignored ignored &
    BROWSER_SESSION_PID="$!"
    BROWSER_SESSION_START_TIME="$(python "$HELPER" proc-start-time --pid "$BROWSER_SESSION_PID" 2>/dev/null || true)"
    for (( attempt = 0; attempt < 80; attempt++ )); do
      [[ -s "$probe_evidence/browser-session-probe-descendant.pid" && -s "$probe_evidence/browser-session-probe-worker.pid" ]] && browser_session_is_known_live_child && break
      sleep .05
    done
    worker_probe="$(cat "$probe_evidence/browser-session-probe-worker.pid" 2>/dev/null || true)"; descendant_probe="$(cat "$probe_evidence/browser-session-probe-descendant.pid" 2>/dev/null || true)"
    for identity in "$worker_probe" "$descendant_probe"; do
      [[ "$identity" =~ ^[1-9][0-9]*$ ]] || die "browser-session supervisor cleanup probe did not establish a verified supervisor"
      python "$HELPER" session-member-status --same-process-group --leader "$BROWSER_SESSION_PID" --pid "$identity" >/dev/null 2>&1 || die "browser-session probe child escaped the supervisor session"
    done
    # Let the probe worker finish only after the strict membership snapshot.
    printf '%s\n' ready > "$probe_continue"
  }
  evidence="$(mktemp -d /tmp/seedsync-browser-parent-evidence.XXXXXX)"
  raw_dir="$(mktemp -d /tmp/seedsync-browser-session.parent.XXXXXX)"
  profile_dir="$raw_dir/browser-profile"
  hint_dir="$(mktemp -d /tmp/seedsync-browser-parent-hint.XXXXXX)"
  printf 'evidence hint canary\n' > "$hint_dir/sentinel"
  launch_browser_supervisor_probe "$raw_dir" "$profile_dir" "$evidence" ""; child="$BROWSER_SESSION_PID"; descendant="$(cat "$evidence/browser-session-probe-descendant.pid")"
  sleep 30 & unrelated="$!"
  printf '%s\n' "$unrelated" > "$evidence/browser-session.pid"
  printf '%s\n' "$hint_dir" > "$evidence/browser-session.raw-dir"
  printf 'node_status=73\nworker_pid=%s\n' "$unrelated" > "$evidence/forged-completion"
  grep -a -F -q -- 'control_token' "/proc/${child}/cmdline" && die "browser supervisor command line retained a completion capability"
  grep -a -F -q -- 'control_token' "/proc/${child}/environ" && die "browser supervisor environment retained a completion capability"
  cleanup_browser_claim_reuse
  kill -0 "$unrelated" 2>/dev/null || die "tampered browser-session PID file signalled an unrelated process"
  kill -TERM "$unrelated" 2>/dev/null || true; wait "$unrelated" 2>/dev/null || true
  kill -0 "$child" 2>/dev/null && die "parent ordinary-failure cleanup left browser-session leader running"
  kill -0 "$descendant" 2>/dev/null && die "parent ordinary-failure cleanup left browser descendant running"
  [[ ! -e "$raw_dir" ]] || die "parent ordinary-failure cleanup left raw workspace: $raw_dir"
  [[ -f "$hint_dir/sentinel" ]] || die "tampered browser-session workspace pointer removed an unrelated directory"
  [[ -s "$evidence/browser-session.log" ]] || die "parent ordinary-failure cleanup did not publish redacted browser diagnostics"
  ! grep -F -q -- 'browser-session-probe-secret' "$evidence/browser-session.log" || die "parent ordinary-failure cleanup retained raw browser diagnostics"
  python - "$evidence/browser-session-failure.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
expected = {"schema": 1, "phase": "after-first-claim", "status": "failed-before-ready", "ready_marker": False}
if any(payload.get(key) != value for key, value in expected.items()) or type(payload.get("exit_code")) is not int:
    raise SystemExit("browser-session early failure diagnostic is incomplete")
PY
  ! grep -F -q -- 'browser-session-probe-secret' "$evidence/browser-session-failure.json" || die "browser-session early failure diagnostic retained a secret"
  rm -f -- "$hint_dir/sentinel"; rmdir -- "$hint_dir"; rm -f -- "$evidence/browser-session.pid" "$evidence/browser-session-probe-descendant.pid" "$evidence/browser-session-probe-worker.pid" "$evidence/browser-session-probe-continue" "$evidence/browser-session.log" "$evidence/browser-session-failure.json" "$evidence/forged-completion"; rmdir -- "$evidence"
  cleanup_browser_claim_reuse
  normal_evidence="$(mktemp -d /tmp/seedsync-browser-parent-normal-evidence.XXXXXX)"
  normal_raw="$(mktemp -d /tmp/seedsync-browser-session.parent-normal.XXXXXX)"; normal_profile="$normal_raw/browser-profile"
  launch_browser_supervisor_probe "$normal_raw" "$normal_profile" "$normal_evidence" ""; normal_child="$BROWSER_SESSION_PID"; normal_descendant="$(cat "$normal_evidence/browser-session-probe-descendant.pid")"; normal_started="$SECONDS"
  # Do not request shutdown: the probe's direct child exits normally while its
  # TERM-resistant descendant remains, so the supervisor must start its own deadline.
  printf 'node_status=73\nworker_pid=%s\n' "$unrelated" > "$normal_evidence/forged-completion"
  if wait "$normal_child"; then normal_status=0; else normal_status="$?"; fi
  (( normal_status == 0 )) || die "forged completion record spoofed normal worker status: $normal_status"
  (( SECONDS - normal_started >= 5 )) || die "forged completion record armed fallback before normal worker completion"
  cleanup_browser_claim_reuse
  kill -0 "$normal_descendant" 2>/dev/null && die "normal completion deadline left browser descendant running"
  [[ ! -e "$normal_raw" ]] || die "normal completion deadline left raw browser workspace"
  [[ -s "$normal_evidence/browser-session.log" ]] || die "normal completion deadline did not publish redacted diagnostics"
  rm -f -- "$normal_evidence/browser-session-probe-descendant.pid" "$normal_evidence/browser-session-probe-worker.pid" "$normal_evidence/browser-session-probe-continue" "$normal_evidence/browser-session.log" "$normal_evidence/browser-session-failure.json" "$normal_evidence/forged-completion"; rmdir -- "$normal_evidence"
  cleanup_browser_claim_reuse
  crash_evidence="$(mktemp -d /tmp/seedsync-browser-parent-crash-evidence.XXXXXX)"
  crash_raw="$(mktemp -d /tmp/seedsync-browser-session.parent-crash.XXXXXX)"; crash_profile="$crash_raw/browser-profile"
  launch_browser_supervisor_probe "$crash_raw" "$crash_profile" "$crash_evidence" ""; crash_child="$BROWSER_SESSION_PID"; crash_descendant="$(cat "$crash_evidence/browser-session-probe-descendant.pid")"
  kill -KILL "$crash_child" 2>/dev/null || true
  cleanup_browser_claim_reuse && die "supervisor-crash cleanup claimed an unverified group was reaped"
  kill -0 "$crash_descendant" 2>/dev/null || die "supervisor-crash probe lost its protected descendant unexpectedly"
  [[ -d "$crash_raw" ]] || die "supervisor-crash cleanup did not preserve the in-use raw workspace"
  kill -KILL "$crash_descendant" 2>/dev/null || true
  for (( attempt = 0; attempt < 120; attempt++ )); do kill -0 "$crash_descendant" 2>/dev/null || break; sleep .05; done
  BROWSER_SESSION_REAPED=1
  cleanup_browser_session_workspace "$crash_raw" "$crash_raw/raw.log" "$crash_profile" "$crash_evidence" || die "supervisor-crash self-check could not remove its exact protected workspace"
  rm -f -- "$crash_evidence/browser-session-probe-descendant.pid" "$crash_evidence/browser-session-probe-worker.pid" "$crash_evidence/browser-session-probe-continue" "$crash_evidence/browser-session.log"; rmdir -- "$crash_evidence"
  failed_evidence="$(mktemp -d /tmp/seedsync-browser-parent-redactor-failed-evidence.XXXXXX)"
  failed_raw="$(mktemp -d /tmp/seedsync-browser-session.parent-redactor-failed.XXXXXX)"; failed_profile="$failed_raw/browser-profile"
  launch_browser_supervisor_probe "$failed_raw" "$failed_profile" "$failed_evidence" 1
  SEEDSYNC_BROWSER_SESSION_REDACTOR_FAIL=1 cleanup_browser_claim_reuse
  [[ ! -e "$failed_raw" ]] || die "redactor-failure cleanup left raw browser workspace"
  grep -F -q -- '"status":"redaction-failed"' "$failed_evidence/browser-session.log" || die "redactor-failure cleanup did not publish fixed safe marker"
  ! grep -F -q -- 'browser-session-probe-secret' "$failed_evidence/browser-session.log" || die "redactor-failure cleanup retained raw browser diagnostics"
  rm -f -- "$failed_evidence/browser-session-probe-descendant.pid" "$failed_evidence/browser-session-probe-worker.pid" "$failed_evidence/browser-session-probe-continue" "$failed_evidence/browser-session.log" "$failed_evidence/browser-session-failure.json"; rmdir -- "$failed_evidence"
  signal_dir="$(mktemp -d /tmp/seedsync-browser-session.parent-signal.XXXXXX)"
  signal_profile="$signal_dir/browser-profile"
  signal_evidence="$(mktemp -d /tmp/seedsync-browser-parent-signal-evidence.XXXXXX)"
  (
    launch_browser_supervisor_probe "$signal_dir" "$signal_profile" "$signal_evidence" ""; signal_child="$BROWSER_SESSION_PID"
    printf '%s\n' "$signal_child" > "$signal_evidence/leader.pid"
    trap 'cleanup_browser_claim_reuse; exit 143' TERM
    kill -TERM "$BASHPID"
    sleep 1
  ) || true
  signal_child="$(cat "$signal_evidence/leader.pid")"
  signal_descendant="$(cat "$signal_evidence/browser-session-probe-descendant.pid")"
  kill -0 "$signal_child" 2>/dev/null && die "parent signal cleanup left browser-session leader running"
  kill -0 "$signal_descendant" 2>/dev/null && die "parent signal cleanup left browser descendant running"
  [[ ! -e "$signal_dir" ]] || die "parent signal cleanup left raw workspace: $signal_dir"
  [[ -s "$signal_evidence/browser-session.log" ]] || die "parent signal cleanup did not publish redacted browser diagnostics"
  ! grep -F -q -- 'browser-session-probe-secret' "$signal_evidence/browser-session.log" || die "parent signal cleanup retained raw browser diagnostics"
  rm -f -- "$signal_evidence/leader.pid" "$signal_evidence/browser-session-probe-descendant.pid" "$signal_evidence/browser-session-probe-worker.pid" "$signal_evidence/browser-session-probe-continue" "$signal_evidence/browser-session.log" "$signal_evidence/browser-session-failure.json"; rmdir -- "$signal_evidence"
}
fresh_repo_shell() {
  # Do not trust the caller's cwd: the WSL lab can clean a previous directory.
  (cd -- "$ROOT_DIR" && env -u LC_BYOBU bash --noprofile --norc -c 'cd -- "$1"; shift; exec "$@"' bash "$ROOT_DIR" "$@")
}
run_lab() {
  # Each lab operation receives a fresh shell rooted at the stable repository
  # directory.  In particular, lab cleanup must never leave this verifier in a
  # deleted WSL cwd before its next start/status/restart operation.
  local id="$1" port="$2"; shift 2
  fresh_repo_shell env RUN_ID="$id" HOST_PORT="$port" SEEDSYNC_SHIP_PRIVATE_LOG_ROOT="$PRIVATE_LOG_ROOT" bash "$LAB" "$@"
}
run_lab_bounded() {
  local id="$1" port="$2" phase_name="$3" label="$4" limit="$5" output="$6"; shift 6
  bounded_command "$id" "$phase_name" "$label" "$limit" "$output" \
    env -u LC_BYOBU bash --noprofile --norc -c 'cd -- "$1"; shift; exec "$@"' bash "$ROOT_DIR" env RUN_ID="$id" HOST_PORT="$port" SEEDSYNC_SHIP_PRIVATE_LOG_ROOT="$PRIVATE_LOG_ROOT" bash "$LAB" "$@"
}
run_lab_bootstrap_bounded() {
  local id="$1" port="$2" limit="$3" output_dir="${ROOT_DIR}/tmp/upgrade-v086/bootstrap/${id}"
  local log="${output_dir}/legacy-build.log" progress="${output_dir}/progress.json" started="$SECONDS" watcher status
  mkdir -p "$output_dir"
  python "$HELPER" progress --output "$progress" --phase bootstrap-legacy-build --state running --detail "legacy build timeout ${limit}s; log ${log}"
  (
    while sleep 30; do
      python "$HELPER" progress --output "$progress" --phase bootstrap-legacy-build --state running --detail "legacy build still running after $((SECONDS - started))s of ${limit}s timeout"
    done
  ) & watcher="$!"
  if timeout --foreground --signal=TERM --kill-after=20s "${limit}s" env -u LC_BYOBU bash --noprofile --norc -c 'cd -- "$1"; shift; exec "$@"' bash "$ROOT_DIR" env RUN_ID="$id" HOST_PORT="$port" bash "$LAB" build 2>&1 | redact > "$log"; then status=0; else status="${PIPESTATUS[0]}"; fi
  kill "$watcher" 2>/dev/null || true; wait "$watcher" 2>/dev/null || true
  if [[ "$status" -ne 0 ]]; then
    { docker ps -a; tail -n 120 "$log"; } 2>&1 | redact > "${output_dir}/legacy-build-diagnostics.txt" || true
    printf '%s\0' 'remotepass' | python "$HELPER" audit-retained-run --root "$output_dir" --output "${output_dir}/retained-run-audit.json" --canaries-stdin || true
    python "$HELPER" progress --output "$progress" --phase bootstrap-legacy-build --state failed --detail "legacy build exited ${status}; diagnostics ${output_dir}/legacy-build-diagnostics.txt"
    return "$status"
  fi
  python "$HELPER" progress --output "$progress" --phase bootstrap-legacy-build --state passed --detail "legacy build completed in $((SECONDS - started))s"
}

ACTIVE_RUN_ID=""
FINAL_OUTCOME="failed"
FIRST_FAILURE_DETAIL=""
AUDIT_RAN=0
first_recorded_failure_detail() {
  local id="$1"
  python - "$(evidence_dir "$id")/progress.tsv" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
if not path.exists():
    raise SystemExit(0)
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    fields = line.split("\t", 3)
    if len(fields) == 4 and fields[2] == "failed" and fields[1] != "failed":
        print(fields[3])
        break
PY
}
finish_full() {
  local code="$?" id="$ACTIVE_RUN_ID" detail recorded_detail
  [[ -n "$id" && -d "$(evidence_dir "$id")" ]] || return "$code"
  set +e
  cleanup_browser_claim_reuse || true
  if [[ "$code" -ne 0 ]]; then
    recorded_detail="$(first_recorded_failure_detail "$id" 2>/dev/null || true)"
    detail="${FIRST_FAILURE_DETAIL:-${recorded_detail:-full verifier exited with ${code}}}"
    phase "$id" failed failed "$detail"
    FINAL_OUTCOME="failed"
    [[ "$AUDIT_RAN" == 1 ]] || audit_retained_run "$id" || true
  fi
  python "$HELPER" summary --matrix "$(matrix "$id")" --output "$(evidence_dir "$id")/summary.json" --failures "$(evidence_dir "$id")/failures.json" --outcome "$FINAL_OUTCOME" --detail "full verifier exit ${code}"
  return "$code"
}

require_tools() {
  command -v timeout >/dev/null || die "timeout is required for bounded verifier commands"
  command -v docker >/dev/null || die "docker is required"
  command -v curl >/dev/null || die "curl is required"
  [[ -x "$(node_binary)" ]] || die "a usable WSL Node runtime is required for Playwright evidence"
  command -v python >/dev/null || die "python is required"
  docker compose version >/dev/null || die "docker compose is required"
  [[ -f "$BROWSER" ]] || die "browser verifier is missing"
  python "$HELPER" self-test >/dev/null || die "ship-readiness Python archive preflight failed"
  browser_dispatch_self_check || die "Playwright Node dispatch is unavailable; set SEEDSYNC_PLAYWRIGHT_NODE_PATH to the WSL node_modules path"
}

capture_http_diagnostics() {
  local id="$1" phase_name="$2" url="$3" output="$4" container="$5" stderr_path="$6" diagnostic="${output}.diagnostics.txt"
  {
    printf 'at=%s\nphase=%s\nurl=%s\ncontainer=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$phase_name" "$url" "$container"
    printf '\ncurl-stderr:\n'
    cat "$stderr_path" 2>/dev/null || true
    printf '\ncontainer-port-bindings:\n'
    docker port "$container" 2>&1 || true
    docker inspect --format 'status={{.State.Status}} running={{.State.Running}} exit={{.State.ExitCode}} ports={{json .NetworkSettings.Ports}} networks={{json .NetworkSettings.Networks}}' "$container" 2>&1 || true
    printf '\ncontainer-log-tail:\n'
    docker logs --tail 120 "$container" 2>&1 || true
  } | redact > "$diagnostic"
  printf '%s' "$diagnostic"
}
wait_http() (
  local url="$1" output="$2" id="${3:-}" phase_name="${4:-}" container="${5:-}" attempts=0 diagnostic raw_dir raw_headers raw_body raw_stderr
  raw_dir="$(mktemp -d /tmp/seedsync-http.XXXXXX)" || die "unable to create private HTTP workspace"
  chmod 700 "$raw_dir"
  raw_headers="$raw_dir/headers"; raw_body="$raw_dir/body"; raw_stderr="$raw_dir/stderr"
  cleanup_http_temp() { rm -f -- "$raw_headers" "$raw_body" "$raw_stderr"; rmdir -- "$raw_dir" 2>/dev/null || true; }
  trap cleanup_http_temp EXIT HUP INT TERM
  while ! curl --fail --silent --show-error --max-time 5 -D "$raw_headers" "$url" > "$raw_body" 2> "$raw_stderr"; do
    attempts=$((attempts + 1))
    if [[ -n "$id" && $((attempts % 10)) -eq 0 ]]; then
      phase "$id" "$phase_name" running "waiting for ${url}; attempt ${attempts}/60"
    fi
    if (( attempts >= 60 )); then
      if [[ -n "$id" ]]; then
        diagnostic="$(capture_http_diagnostics "$id" "$phase_name" "$url" "$output" "$container" "$raw_stderr")"
        phase "$id" "$phase_name" failed "endpoint did not become healthy after 60 attempts; diagnostics evidence/ship-readiness/$(basename "$diagnostic")"
        die "endpoint did not become healthy: $url; see $diagnostic"
      fi
      die "endpoint did not become healthy: $url"
    fi
    sleep 1
  done
  redact < "$raw_body" > "$output"
)

wait_migration_status() (
  local url="$1" output="$2" id="$3" phase_name="$4" container="$5" attempts=0 diagnostic raw_dir raw_body raw_stderr status
  # The status response contains the one-time CSRF action.  Stream it directly
  # through the whitelisting helper so no raw response reaches retained evidence.
  raw_dir="$(mktemp -d /tmp/seedsync-migration-status.XXXXXX)" || die "unable to create private migration-status workspace"
  chmod 700 "$raw_dir"
  raw_body="$raw_dir/body"; raw_stderr="$raw_dir/stderr"
  cleanup_migration_status_temp() { rm -f -- "$raw_body" "$raw_stderr"; rmdir -- "$raw_dir" 2>/dev/null || true; }
  trap cleanup_migration_status_temp EXIT HUP INT TERM
  while true; do
    if curl --fail --silent --show-error --max-time 5 "$url" > "$raw_body" 2> "$raw_stderr" && python "$HELPER" migration-status-evidence --output "$output" < "$raw_body"; then
      return
    fi
    attempts=$((attempts + 1))
    if (( attempts % 10 == 0 )); then
      phase "$id" "$phase_name" running "waiting for ${url}; attempt ${attempts}/60"
    fi
    if (( attempts >= 60 )); then
      diagnostic="$(capture_http_diagnostics "$id" "$phase_name" "$url" "$output" "$container" "$raw_stderr")"
      phase "$id" "$phase_name" failed "migration status did not become healthy after 60 attempts; diagnostics evidence/ship-readiness/$(basename "$diagnostic")"
      die "migration status did not become healthy: $url; see $diagnostic"
    fi
    sleep 1
  done
)

cross_migration_runtime_boundary() {
  local id="$1" status_file="$2" container="$3" output="$(evidence_dir "$1")/migration-terminal-transition.json" running diagnostic
  # apply_migration() has already polled the migration API through the
  # terminal complete state. Stop that process only after its durable state is
  # proven, then start a fresh process so preflight selects normal WebApp.
  python "$HELPER" migration-terminal-transition-evidence --output "$output" < "$status_file"
  running="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)"
  if [[ "$running" == true ]]; then
    stop_container "$id" migration-current-normal-transition-stop current-normal-transition-stop "$container"
  elif [[ "$running" != false ]]; then
    diagnostic="${output%.json}.diagnostics.txt"
    printf 'schema=1\nphase=migration-current-normal-transition-stop\ncontainer_running=%s\nmigration_state=complete\nmigration_operation=succeeded\n' \
      "${running:-unknown}" > "$diagnostic"
    phase "$id" migration-current-normal-transition-stop failed "migration runtime container state is unavailable; diagnostics evidence/ship-readiness/$(basename "$diagnostic")"
    die "migration runtime container state is unavailable; see $diagnostic"
  else
    phase "$id" migration-current-normal-transition-stop passed "migration runtime was already stopped after terminal completion"
  fi
}

wait_normal_runtime_readiness() (
  local base="$1" output="$2" id="$3" phase_name="$4" container="$5" attempts=0 raw_dir raw_stderr migration_status status_status bootstrap_status diagnostic
  raw_dir="$(mktemp -d /tmp/seedsync-normal-runtime.XXXXXX)" || die "unable to create private normal runtime workspace"
  chmod 700 "$raw_dir"
  raw_stderr="$raw_dir/stderr"
  cleanup_normal_runtime_temp() { rm -f -- "$raw_stderr"; rmdir -- "$raw_dir" 2>/dev/null || true; }
  trap cleanup_normal_runtime_temp EXIT HUP INT TERM
  route_status() {
    local endpoint="$1" code
    if code="$(curl --silent --show-error --max-time 5 --output /dev/null --write-out '%{http_code}' "${base}${endpoint}" 2> "$raw_stderr")"; then
      [[ "$code" =~ ^[1-5][0-9]{2}$ ]] && printf '%s' "$code" || printf '000'
    else
      printf '000'
    fi
  }
  while true; do
    migration_status="$(route_status /server/migration/v1/status)"
    status_status="$(route_status /server/status)"
    bootstrap_status="$(route_status /bootstrap)"
    if python "$HELPER" normal-runtime-transition-evidence --migration-status "$migration_status" --status "$status_status" --bootstrap "$bootstrap_status" --output "$output" 2> "$raw_stderr"; then
      phase "$id" "$phase_name" passed "normal WebApp routes replaced the completed migration runtime"
      return
    fi
    attempts=$((attempts + 1))
    if (( attempts % 10 == 0 )); then
      phase "$id" "$phase_name" running "waiting for normal WebApp routes after migration restart; attempt ${attempts}/60"
    fi
    if (( attempts >= 60 )); then
      diagnostic="${output%.json}.diagnostics.txt"
      printf 'schema=1\nphase=%s\nattempts=%s\nmigration_status=%s\nstatus_status=%s\nbootstrap_status=%s\n' \
        "$phase_name" "$attempts" "$migration_status" "$status_status" "$bootstrap_status" > "$diagnostic"
      phase "$id" "$phase_name" failed "normal WebApp routes did not become ready after migration restart; diagnostics evidence/ship-readiness/$(basename "$diagnostic")"
      die "normal WebApp routes did not become ready after migration restart; see $diagnostic"
    fi
    sleep 1
  done
)

capture_preclaim_auth_diagnostics() {
  local phase_name="$1" output="$2" attempts="$3" curl_status="$4" http_status="$5" diagnostic="${output}.diagnostics.txt"
  [[ "$curl_status" =~ ^[0-9]+$ ]] || curl_status="unknown"
  [[ "$http_status" =~ ^[0-9]{3}$ ]] || http_status="none"
  # Do not retain curl stderr, HTTP headers, response bodies, container logs,
  # or inspection output here: any of them can contain untrusted credentials.
  printf 'schema=1\nphase=%s\nattempts=%s\ncurl_exit=%s\nhttp_status=%s\n' \
    "$phase_name" "$attempts" "$curl_status" "$http_status" > "$diagnostic"
  printf '%s' "$diagnostic"
}

wait_preclaim_auth_challenge() (
  local url="$1" output="$2" id="$3" phase_name="$4" container="$5" max_attempts="${6:-60}" attempts=0 diagnostic response curl_status http_status response_body temp_root raw_dir raw_headers raw_stderr raw_owner
  # A direct curl does not carry the browser's claim cookie.  Require the
  # exact SeedSync 401 token challenge instead of treating that expected state
  # as an unreachable service, and retain only whitelisted response facts.
  [[ "$max_attempts" =~ ^[1-9][0-9]*$ ]] || die "pre-claim auth attempt limit must be a positive integer"
  temp_root="${SEEDSYNC_SHIP_PRECLAIM_TEMP_ROOT:-/tmp}"
  [[ "$temp_root" == /tmp || "$temp_root" == /tmp/* ]] || die "pre-claim auth temporary files must stay under /tmp"
  [[ -d "$temp_root" ]] || die "pre-claim auth temporary root is unavailable: $temp_root"
  raw_dir="$(mktemp -d "$temp_root/seedsync-preclaim-auth.XXXXXX")" || die "unable to create pre-claim auth temporary workspace"
  raw_headers="$raw_dir/headers"
  raw_stderr="$raw_dir/stderr"
  raw_owner="$raw_dir/owner.pid"
  printf '%s\n' "$BASHPID" > "$raw_owner"
  cleanup_preclaim_auth_temp() {
    rm -f -- "$raw_headers" "$raw_stderr" "$raw_owner"
    rmdir -- "$raw_dir" 2>/dev/null || true
  }
  trap cleanup_preclaim_auth_temp EXIT
  trap 'cleanup_preclaim_auth_temp; exit 129' HUP
  trap 'cleanup_preclaim_auth_temp; exit 130' INT
  trap 'cleanup_preclaim_auth_temp; exit 143' TERM
  while true; do
    http_status=""
    if response="$(curl --silent --show-error --max-time 5 -D "$raw_headers" --write-out $'\n%{http_code}' "$url" 2> "$raw_stderr")"; then
      curl_status=0
    else
      curl_status="$?"
    fi
    if [[ "$curl_status" == 0 && ${#response} -ge 4 ]]; then
      http_status="${response: -3}"
      response_body="${response:0:${#response}-3}"
      response_body="${response_body%$'\n'}"
      if printf '%s' "$response_body" | python "$HELPER" preclaim-auth-challenge-evidence --status "$http_status" --headers "$raw_headers" --output "$output"; then
        return
      fi
    fi
    attempts=$((attempts + 1))
    if (( attempts % 10 == 0 )); then
      phase "$id" "$phase_name" running "waiting for expected pre-claim API-token challenge at ${url}; attempt ${attempts}/${max_attempts}"
    fi
    if (( attempts >= max_attempts )); then
      diagnostic="$(capture_preclaim_auth_diagnostics "$phase_name" "$output" "$attempts" "$curl_status" "$http_status")"
      phase "$id" "$phase_name" failed "pre-claim API-token challenge did not become healthy after ${max_attempts} attempts; diagnostics evidence/ship-readiness/$(basename "$diagnostic")"
      die "pre-claim API-token challenge did not become healthy: $url; see $diagnostic"
    fi
    sleep 1
  done
)

wait_browser_handover_bootstrap() (
  local url="$1" output="$2" id="$3" phase_name="$4" container="$5" attempts=0 curl_status http_status diagnostic raw_dir raw_body raw_stderr raw_status
  # The recovery handover deliberately serves /bootstrap before any browser
  # claim. Retain only its readiness status: the page body is untrusted UI.
  raw_dir="$(mktemp -d /tmp/seedsync-browser-bootstrap.XXXXXX)" || die "unable to create private browser bootstrap workspace"
  chmod 700 "$raw_dir"
  raw_body="$raw_dir/body"; raw_stderr="$raw_dir/stderr"; raw_status="$raw_dir/status"
  cleanup_browser_handover_bootstrap_temp() { rm -f -- "$raw_body" "$raw_stderr" "$raw_status"; rmdir -- "$raw_dir" 2>/dev/null || true; }
  trap cleanup_browser_handover_bootstrap_temp EXIT HUP INT TERM
  while true; do
    http_status=""
    if curl --silent --show-error --max-time 5 --output "$raw_body" --write-out '%{http_code}' "$url" > "$raw_status" 2> "$raw_stderr"; then
      curl_status=0
      http_status="$(cat "$raw_status")"
      if [[ "$http_status" == 200 ]]; then
        python - "$output" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"schema": 1, "phase": "before-first-claim", "endpoint": "bootstrap", "http_status": 200}, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
        return
      fi
    else
      curl_status="$?"
    fi
    attempts=$((attempts + 1))
    if (( attempts % 10 == 0 )); then
      phase "$id" "$phase_name" running "waiting for recovery browser handover at /bootstrap; attempt ${attempts}/60"
    fi
    if (( attempts >= 60 )); then
      diagnostic="$(capture_preclaim_auth_diagnostics "$phase_name" "$output" "$attempts" "$curl_status" "$http_status")"
      phase "$id" "$phase_name" failed "recovery browser handover did not become ready after 60 attempts; diagnostics evidence/ship-readiness/$(basename "$diagnostic")"
      die "recovery browser handover did not become ready: $url; see $diagnostic"
    fi
    sleep 1
  done
)

capture_inventory() {
  local id="$1" label="$2" root="$3"; shift 3
  python "$HELPER" inventory --root "$root" --output "$(evidence_dir "$id")/${label}.json" "$@"
}

config_volume() {
  printf 'seedsync-upgrade-v086-config-%s' "$1"
}

validator_container() {
  printf 'seedsync-upgrade-v086-validator-%s' "$1"
}
snapshotter_container() {
  printf 'seedsync-upgrade-v086-snapshotter-%s' "$1"
}
protected_volume() {
  printf 'seedsync-upgrade-v086-protected-%s' "$1"
}
validator_evidence_path() {
  printf '/evidence/ship-readiness/%s' "$1"
}

verify_config_volume() { fresh_repo_shell bash "$LAB" verify-volume "$1" >/dev/null || die "config volume identity check failed"; }
verify_validator() { fresh_repo_shell bash "$LAB" verify-validator "$1" >/dev/null || die "read-only validator isolation check failed"; }
verify_protected_volume() { fresh_repo_shell bash "$LAB" verify-protected "$1" >/dev/null || die "protected volume identity check failed"; }
verify_snapshotter() { fresh_repo_shell bash "$LAB" verify-snapshotter "$1" >/dev/null || die "protected snapshotter isolation check failed"; }

capture_volume_inventory() {
  local id label legacy_flag output
  id="$1"
  label="$2"
  legacy_flag="${3:-}"
  output="$(evidence_dir "$id")/${label}.json"
  stabilize_repo_cwd
  if [[ "$legacy_flag" == --legacy-config ]]; then
    capture_volume_helper_output "$id" "$output" inventory --root /config --legacy-config
  else
    capture_volume_helper_output "$id" "$output" inventory --root /config
  fi
}

volume_helper() {
  local id="$1" validator; shift
  validator="$(validator_container "$id")"
  verify_config_volume "$id"
  verify_validator "$id"
  # The tracked helper is interpreted from stdin inside a retained, networkless,
  # non-root validator with a read-only rootfs and read-only config/evidence mounts.
  docker exec -i "$validator" python -c 'import sys; exec(compile(sys.stdin.read(), "ship_readiness.py", "exec"))' "$@" < "$HELPER"
}

capture_volume_behavior_contract() {
  local id="$1" model="$2" fixture="$3" output="$4"
  capture_volume_helper_output "$id" "$output" behavior-contract --model "$model" --settings /config/settings.cfg --controller /config/controller.persist --autoqueue /config/autoqueue.persist --fixture "$fixture"
}

current_product_preclaim_auth_contract() {
  local id="$1" image="seedsync/upgrade-v086:current-${1,,}" validator="seedsync-upgrade-v086-current-auth-validator-${1,,}" output="$(evidence_dir "$1")/current-product-preclaim-auth-contract.json" log="$(evidence_dir "$1")/current-product-preclaim-auth-validator.log" failure="$(evidence_dir "$1")/current-product-preclaim-auth-contract-failure.json" image_id image_digest
  [[ ! -e "$output" ]] || die "current product auth contract evidence already exists: $output"
  docker container inspect "$validator" >/dev/null 2>&1 && die "current product auth validator already exists for $id; choose a fresh RUN_ID"
  image_id="$(docker image inspect --format '{{.Id}}' "$image")" || die "current product image is unavailable for auth contract validation"
  image_digest="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}unpublished:{{.Id}}{{end}}' "$image")" || die "current product image digest is unavailable for auth contract validation"
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die "current product image ID is not immutable"
  [[ "$image_digest" =~ ^(unpublished:sha256:|[^[:space:]@]+@sha256:)[0-9a-f]{64}$ ]] || die "current product image digest is not safe"
  bounded_command "$id" migration-current-auth-validator-create current-auth-validator-create "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-product-preclaim-auth-validator-create.log" docker create --name "$validator" --network none --read-only --user 1000:1000 --security-opt no-new-privileges:true --cap-drop ALL --env PYTHONPATH=/app/python --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=current-product-preclaim-auth-validator" --mount "type=volume,src=$(config_volume "$id"),dst=/config,readonly" --mount "type=bind,src=$(evidence_dir "$id"),dst=/evidence,readonly" --entrypoint python "$image_id" -c '
import json, sys
from pathlib import Path
try:
    from web.auth_store import validate_completed_migration_preclaim_auth_state
except ImportError:
    print(json.dumps({"schema": 1, "validator": "current-product-preclaim-auth", "status": "failed", "reason": "product-validator-module-unavailable"}, sort_keys=True))
    raise SystemExit(73)
try:
    validate_completed_migration_preclaim_auth_state(Path("/config"))
except Exception as error:
    print(json.dumps({"schema": 1, "validator": "current-product-preclaim-auth", "status": "failed", "reason": "product-validator-rejected-state", "error_type": type(error).__name__}, sort_keys=True))
    raise SystemExit(74)
print(json.dumps({"schema": 1, "validator": "current-product-preclaim-auth", "status": "passed"}, sort_keys=True))
'
  python - "$validator" "$image_id" <<'PY'
import json, subprocess, sys
name, image_id = sys.argv[1:]
item = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
mounts = item.get("Mounts") or []
host = item.get("HostConfig") or {}
checks = {
    "image": item.get("Image") == image_id,
    "network": (host.get("NetworkMode") or "") == "none",
    "read_only_rootfs": bool(host.get("ReadonlyRootfs")),
    "config_mount_read_only": len([mount for mount in mounts if mount.get("Destination") == "/config" and mount.get("RW") is False]) == 1,
    "evidence_mount_read_only": len([mount for mount in mounts if mount.get("Destination") == "/evidence" and mount.get("RW") is False]) == 1,
    "user": (item.get("Config") or {}).get("User") == "1000:1000",
    "no_new_privileges": "no-new-privileges:true" in set(host.get("SecurityOpt") or []),
    "cap_drop_all": "ALL" in set(host.get("CapDrop") or []),
    "role": ((item.get("Config") or {}).get("Labels") or {}).get("seedsync.upgrade-v086.role") == "current-product-preclaim-auth-validator",
}

failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("current product auth validator containment failed: " + ", ".join(failed))
PY
  if ! bounded_command "$id" migration-current-auth-validator-run current-auth-validator-run "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$log" docker start --attach "$validator"; then
    python - "$failure" "$image" "$image_id" "$image_digest" "$validator" <<'PY'
import json, os, sys
output, image_ref, image_id, image_digest, container = sys.argv[1:]
payload = {"schema": 1, "validator": "current-product-preclaim-auth", "status": "failed", "reason": "validator-command-failed", "image_ref": image_ref, "image_id": image_id, "image_digest": image_digest, "container": container}
with open(output + ".tmp", "w", encoding="utf-8") as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
os.chmod(output + ".tmp", 0o600)
os.replace(output + ".tmp", output)
PY
    die "current product auth validator did not complete; see evidence/ship-readiness/$(basename "$failure")"
  fi
  python - "$log" "$output" "$image" "$image_id" "$image_digest" "$validator" <<'PY'
import json, os, pathlib, sys
log, output, image_ref, image_id, image_digest, container = map(pathlib.Path, sys.argv[1:])
try:
    result = json.loads(log.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
    raise SystemExit("current product auth validator produced no safe JSON result") from error
if result != {"schema": 1, "validator": "current-product-preclaim-auth", "status": "passed"}:
    raise SystemExit("current product auth validator result was not a safe pass")
payload = {
    "schema": 1, "validator": "current-product-preclaim-auth", "status": "passed",
    "image_ref": str(image_ref), "image_id": str(image_id), "image_digest": str(image_digest),
    "image_provenance": "immutable-current-image-id", "container": str(container),
    "containment": {"network": "none", "read_only_rootfs": True, "config_mount_read_only": True,
                    "evidence_mount_read_only": True, "user": "1000:1000", "no_new_privileges": True, "cap_drop_all": True},
}
temporary = output.with_name("." + output.name + ".tmp")
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
os.chmod(temporary, 0o600)
os.replace(temporary, output)
PY
  phase "$id" migration-current-auth-validator-run passed "current immutable product auth validator passed; evidence/ship-readiness/$(basename "$output")"
}

current_product_claimed_auth_contract() {
  local id="$1" image="seedsync/upgrade-v086:current-${1,,}" validator="seedsync-upgrade-v086-current-claimed-auth-validator-${1,,}" output="$(evidence_dir "$1")/current-product-claimed-auth-contract.json" log="$(evidence_dir "$1")/current-product-claimed-auth-validator.log" image_id image_digest
  [[ ! -e "$output" ]] || die "current claimed product auth contract evidence already exists: $output"
  docker container inspect "$validator" >/dev/null 2>&1 && die "current claimed product auth validator already exists for $id; choose a fresh RUN_ID"
  image_id="$(docker image inspect --format '{{.Id}}' "$image")" || die "current product image is unavailable for claimed auth validation"
  image_digest="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}unpublished:{{.Id}}{{end}}' "$image")" || die "current product image digest is unavailable for claimed auth validation"
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die "current product image ID is not immutable"
  bounded_command "$id" migration-current-claimed-auth-validator-create current-claimed-auth-validator-create "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-product-claimed-auth-validator-create.log" docker create --name "$validator" --network none --read-only --user 1000:1000 --security-opt no-new-privileges:true --cap-drop ALL --env PYTHONPATH=/app/python --label "seedsync.upgrade-v086.run-id=${id}" --label "seedsync.upgrade-v086.role=current-product-claimed-auth-validator" --mount "type=volume,src=$(config_volume "$id"),dst=/config,readonly" --mount "type=bind,src=$(evidence_dir "$id"),dst=/evidence,readonly" --entrypoint python "$image_id" -c '
import json
from migration import MigrationCoordinator, MigrationState
try:
    decision = MigrationCoordinator("/config").preflight()
    if decision.state != MigrationState.COMPLETE or decision.completed_auth_phase != "claimed":
        raise ValueError("claimed phase was not accepted")
except Exception as error:
    print(json.dumps({"schema": 1, "validator": "current-product-claimed-auth", "status": "failed", "reason": "product-validator-rejected-state", "error_type": type(error).__name__}, sort_keys=True))
    raise SystemExit(74)
print(json.dumps({"schema": 1, "validator": "current-product-claimed-auth", "status": "passed", "phase": "claimed", "marker_binding": "receipt-and-backup"}, sort_keys=True))
'
  if ! bounded_command "$id" migration-current-claimed-auth-validator-run current-claimed-auth-validator-run "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$log" docker start --attach "$validator"; then
    die "current claimed product auth validator did not complete; see evidence/ship-readiness/$(basename "$log")"
  fi
  python - "$log" "$output" "$image" "$image_id" "$image_digest" "$validator" <<'PY'
import json, os, pathlib, sys
log, output, image_ref, image_id, image_digest, container = map(pathlib.Path, sys.argv[1:])
result = json.loads(log.read_text(encoding="utf-8"))
if result != {"schema": 1, "validator": "current-product-claimed-auth", "status": "passed", "phase": "claimed", "marker_binding": "receipt-and-backup"}:
    raise SystemExit("current claimed product auth validator result was not a safe pass")
payload = {"schema": 1, "validator": "current-product-claimed-auth", "status": "passed", "phase": "claimed", "marker_binding": "receipt-and-backup", "image_ref": str(image_ref), "image_id": str(image_id), "image_digest": str(image_digest), "image_provenance": "immutable-current-image-id", "container": str(container), "containment": {"network": "none", "read_only_rootfs": True, "config_mount_read_only": True, "evidence_mount_read_only": True, "user": "1000:1000", "no_new_privileges": True, "cap_drop_all": True}}
temporary = output.with_name("." + output.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, output)
PY
  phase "$id" migration-current-claimed-auth-validator-run passed "current immutable product claimed-auth validator passed; evidence/ship-readiness/$(basename "$output")"
}

capture_volume_helper_failure() {
  local id="$1" output="$2" status="$3" stderr_path="$4" label diagnostic excerpt
  label="$(basename "$output")"
  diagnostic="${output%.json}-helper-failure.json"
  excerpt="$(head -c 4096 "$stderr_path" | tr '\000' '?' | redact)"
  python - "$diagnostic" "$label" "$status" "$excerpt" <<'PY'
import json, re, sys
output, label, status, excerpt = sys.argv[1:]
safe_label = re.sub(r"[^A-Za-z0-9._-]", "_", label)[:160]
payload = {
    "schema": 1,
    "helper_output": safe_label,
    "exit_status": int(status) if status.isdigit() else None,
    "stderr_present": bool(excerpt),
    "stderr_excerpt_redacted": excerpt[:4096],
}
with open(output, "w", encoding="utf-8") as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
  phase "$id" "volume-helper-${label%.json}" failed "volume helper output failed; safe diagnostics evidence/ship-readiness/$(basename "$diagnostic")"
}

capture_volume_helper_output() (
  local id="$1" output="$2" temp_root raw_dir raw_stdout raw_stderr status
  shift 2
  [[ ! -e "$output" ]] || die "volume helper output already exists: $output"
  temp_root="${SEEDSYNC_SHIP_VOLUME_HELPER_TEMP_ROOT:-/tmp}"
  [[ "$temp_root" == /tmp || "$temp_root" == /tmp/* ]] || die "volume helper temporary files must stay under /tmp"
  [[ -d "$temp_root" ]] || die "volume helper temporary root is unavailable: $temp_root"
  raw_dir="$(mktemp -d "$temp_root/seedsync-volume-helper.XXXXXX")" || die "unable to create volume helper temporary workspace"
  raw_stdout="$raw_dir/stdout"
  raw_stderr="$raw_dir/stderr"
  cleanup_volume_helper_temp() {
    rm -f -- "$raw_stdout" "$raw_stderr"
    rmdir -- "$raw_dir" 2>/dev/null || true
  }
  trap cleanup_volume_helper_temp EXIT
  trap 'cleanup_volume_helper_temp; exit 129' HUP
  trap 'cleanup_volume_helper_temp; exit 130' INT
  trap 'cleanup_volume_helper_temp; exit 143' TERM
  if volume_helper "$id" "$@" --output - > "$raw_stdout" 2> "$raw_stderr"; then
    if [[ ! -s "$raw_stdout" ]]; then
      status=1
    elif python - "$raw_stdout" "$output" <<'PY'
import json, os, sys, tempfile
source, output = map(__import__("pathlib").Path, sys.argv[1:])
raw = source.read_bytes()
if not raw.strip():
    raise SystemExit("volume helper output was empty")
json.loads(raw.decode("utf-8"))
temporary = None
try:
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix="." + output.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = __import__("pathlib").Path(stream.name)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output)
    temporary = None
finally:
    if temporary is not None:
        temporary.unlink(missing_ok=True)
PY
    then
      return
    else
      status="$?"
    fi
  else
    status="$?"
  fi
  capture_volume_helper_failure "$id" "$output" "$status" "$raw_stderr"
  return "$status"
)

seed_volume_settings() {
  local container="$1" output="$2"
  docker exec -i "$container" python - <<'PY' > "$output"
import configparser, json
path = "/config/settings.cfg"; parser = configparser.ConfigParser(interpolation=None); parser.optionxform = str
with open(path, encoding="utf-8") as stream: parser.read_file(stream)
values = {"General":{"debug":"false","verbose":"true"},"Lftp":{"num_max_parallel_downloads":"2","num_max_parallel_files_per_download":"3","num_max_connections_per_root_file":"4","num_max_connections_per_dir_file":"5","num_max_total_connections":"6","use_temp_file":"true"},"Controller":{"interval_ms_remote_scan":"2000","interval_ms_local_scan":"3000","interval_ms_downloading_scan":"4000","extract_path":"/downloads","use_local_path_as_extract_path":"true"},"Web":{"port":"8800"},"AutoQueue":{"enabled":"true","patterns_only":"true","auto_extract":"true"}}
for section, items in values.items():
    if not parser.has_section(section): parser.add_section(section)
    for key, value in items.items(): parser.set(section, key, value)
with open(path, "w", encoding="utf-8", newline="\n") as stream: parser.write(stream)
print(json.dumps({"seeded_sections": {section: sorted(items) for section, items in values.items()}}, sort_keys=True))
PY
}

snapshot_volume_config() {
  local id="$1" label="$2" inventory_label="$3" validator snapshotter manifest validation binding metadata inventory_path
  stabilize_repo_cwd
  validator="$(validator_container "$id")"
  snapshotter="$(snapshotter_container "$id")"
  manifest="$(evidence_dir "$id")/${label}-protected-storage.json"
  validation="$(evidence_dir "$id")/${label}-archive-validation.json"
  binding="$(evidence_dir "$id")/${label}-archive-inventory-binding.json"
  inventory_path="$(evidence_dir "$id")/${inventory_label}.json"
  [[ -s "$inventory_path" ]] || die "archive inventory input is missing or empty: ${inventory_label}"
  verify_config_volume "$id"
  verify_protected_volume "$id"
  verify_validator "$id"
  verify_snapshotter "$id"
  # The archive never crosses to a DrvFS bind mount. A non-root, networkless
  # snapshotter reads /config and writes only the labelled POSIX named volume;
  # the validator can then postvalidate that exact archive through a read-only mount.
  metadata="$(docker exec "$snapshotter" sh -c 'archive="/protected/$1.tar"; test ! -e "$archive"; umask 077; tar -C /config -cpf "$archive" .; test "$(stat -c "%u:%g:%a" /protected)" = "1000:1000:700"; test "$(stat -c "%u:%g:%a" "$archive")" = "1000:1000:600"; sha256sum "$archive" | cut -d" " -f1' sh "$label")" \
    || die "protected config snapshot did not satisfy the POSIX storage contract"
  [[ "$metadata" =~ ^[0-9a-f]{64}$ ]] || die "protected config snapshot digest is invalid"
  capture_volume_helper_output "$id" "$validation" validate-archive --archive "/protected/${label}.tar"
  capture_volume_helper_output "$id" "$binding" bind-archive-inventory --archive "/protected/${label}.tar" --inventory "$(validator_evidence_path "${inventory_label}.json")"
  python - "$manifest" "$label" "$(protected_volume "$id")" "$metadata" "$binding" <<'PY'
import json, sys
output, label, volume, digest, binding_path = sys.argv[1:]
binding = json.load(open(binding_path, encoding="utf-8"))
if binding.get("exact_inventory_match") is not True:
    raise SystemExit("protected archive inventory binding is incomplete")
json.dump({"schema": 1, "classification": "protected-synthetic-secret", "storage": "docker-named-volume", "volume": volume, "archive": label + ".tar", "sha256": digest, "archive_mode": "0600", "parent_mode": "0700", "owner": "1000:1000", "inventory_sha256": binding["inventory_sha256"], "inventory_entry_count": binding["entry_count"], "validator_access": "read-only postvalidated without extraction"}, open(output, "w"), sort_keys=True)
PY
}

verify_snapshot_for_consumer() {
  local id="$1" label="$2" inventory_label="$3" output="$4" inventory_path manifest_path
  inventory_path="$(evidence_dir "$id")/${inventory_label}.json"
  manifest_path="$(evidence_dir "$id")/${label}-protected-storage.json"
  [[ -s "$inventory_path" ]] || die "archive consumer inventory is missing or empty: ${inventory_label}"
  [[ -s "$manifest_path" ]] || die "archive consumer manifest is missing or empty: ${label}"
  capture_volume_helper_output "$id" "$output" verify-protected-archive --archive "/protected/${label}.tar" --inventory "$(validator_evidence_path "${inventory_label}.json")" --manifest "$(validator_evidence_path "${label}-protected-storage.json")"
}

capture_before_filesystem_failure() {
  local id="$1" step="$2" status="$3" stderr_file="$4" diagnostic validator
  stabilize_repo_cwd
  diagnostic="$(evidence_dir "$id")/before-filesystem-${step}-failure.txt"
  validator="$(validator_container "$id")"
  {
    printf 'schema=1\nphase=before-filesystem-inventory\nstep=%s\nexit_status=%s\n' "$step" "$status"
    printf '\ncommand-stderr:\n'
    [[ -f "$stderr_file" ]] && cat "$stderr_file"
    printf '\nvalidator-state:\n'
    docker inspect --format 'status={{.State.Status}} running={{.State.Running}} exit={{.State.ExitCode}} user={{.Config.User}} readonly_rootfs={{.HostConfig.ReadonlyRootfs}} network={{.HostConfig.NetworkMode}}' "$validator" 2>&1 || true
    printf '\nvalidator-contract:\n'
    bash "$LAB" verify-validator "$id" 2>&1 || true
    printf '\nprotected-volume-contract:\n'
    bash "$LAB" verify-protected "$id" 2>&1 || true
    printf '\nsnapshotter-contract:\n'
    bash "$LAB" verify-snapshotter "$id" 2>&1 || true
    printf '\nvolume-contract:\n'
    bash "$LAB" verify-volume "$id" 2>&1 || true
  } | redact > "$diagnostic"
  row "$id" before-filesystem-inventory failed "evidence/ship-readiness/$(basename "$diagnostic")" "${step} failed with exit ${status}"
  phase "$id" before-filesystem-inventory failed "${step} failed with exit ${status}; diagnostics evidence/ship-readiness/$(basename "$diagnostic")"
}

capture_before_filesystem_inventory() {
  local id="$1" inventory_stderr snapshot_stderr status
  stabilize_repo_cwd
  inventory_stderr="$(evidence_dir "$id")/before-filesystem-inventory.stderr.txt"
  snapshot_stderr="$(evidence_dir "$id")/before-filesystem-snapshot.stderr.txt"
  phase "$id" before-filesystem-inventory running "capture read-only config inventory and protected snapshot after legacy shutdown"
  if capture_volume_inventory "$id" before-config --legacy-config 2> >(redact > "$inventory_stderr"); then
    :
  else
    status="$?"
    capture_before_filesystem_failure "$id" inventory "$status" "$inventory_stderr"
    return "$status"
  fi
  python "$HELPER" assert-legacy-auth-absence --inventory "$(evidence_dir "$id")/before-config.json" --output "$(evidence_dir "$id")/before-legacy-auth-absence.json"
  if snapshot_volume_config "$id" before-config before-config 2> >(redact > "$snapshot_stderr"); then
    :
  else
    status="$?"
    capture_before_filesystem_failure "$id" snapshot "$status" "$snapshot_stderr"
    return "$status"
  fi
  row "$id" before-filesystem-inventory passed "evidence/ship-readiness/before-config.json"
}

capture_redacted_volume_settings() {
  local id="$1" target="$(evidence_dir "$1")/${2}.cfg" validator="$(validator_container "$1")"
  verify_validator "$id"
  docker exec "$validator" cat /config/settings.cfg | redact > "$target"
}

preflight_volume_private_storage() {
  local id="$1" container="$2" output="$(evidence_dir "$1")/config-volume-private-storage.json"
  # This leaves a retained, owner-only synthetic lock in the volume.  A second
  # O_EXCL acquisition must fail, proving the named-volume lock semantics used
  # by the migration coordinator rather than merely trusting Docker metadata.
  docker exec "$container" python -c 'import json, os, stat; p="/config/.ship-readiness-volume-preflight.lock"; fd=os.open(p, os.O_CREAT|os.O_EXCL|os.O_RDWR, 0o600); os.fchmod(fd,0o600); mode=stat.S_IMODE(os.fstat(fd).st_mode); blocked=False
try:
 os.open(p, os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
except FileExistsError:
 blocked=True
finally:
 os.close(fd)
if mode != 0o600 or not blocked:
 raise SystemExit("named-volume owner-only exclusive-create preflight failed")
print(json.dumps({"schema":1,"storage":"docker-named-volume","mode":format(mode,"04o"),"exclusive_create_blocked":blocked}))' > "$output"
}

capture_redacted_settings() {
  local id="$1" source="$2" target="$(evidence_dir "$id")/${3}.cfg"
  redact < "$source" > "$target"
}

security_probes() {
  local id="$1" base="$2" out="$(evidence_dir "$id")/migration-security-probes.txt"
  : > "$out"
  # These probes must remain before the real apply; all success codes here are
  # failures.  They cover hostile authority, cross-origin mutation, malformed
  # framing/body, and a concurrent apply attempt (the driver owns the latter).
  local name code
  for name in host origin body; do
    case "$name" in
      host) code="$(curl -sS -o /dev/null -w '%{http_code}' -H 'Host: attacker.invalid' "${base}/server/migration/v1/status")" ;;
      origin) code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Origin: https://attacker.invalid' -H 'Content-Type: application/json' --data '{}' "${base}/server/migration/v1/apply")" ;;
      body) code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Origin: ${base}" -H 'Content-Type: text/plain' --data '{}' "${base}/server/migration/v1/apply")" ;;
    esac
    printf '%s=%s\n' "$name" "$code" >> "$out"
    [[ "$code" =~ ^(400|403|404|409)$ ]] || die "migration guard probe unexpectedly accepted $name (HTTP $code)"
  done
  row "$id" migration-guards passed "evidence/ship-readiness/$(basename "$out")"
}

capture_apply_failure_bundle() {
  local id="$1" base="$2" current="seedsync-upgrade-v086-current-${1,,}" proxy="seedsync-upgrade-v086-current-proxy-${1,,}" evidence="$(evidence_dir "$1")" started now
  started="$(head -n 1 "${evidence}/progress.tsv" 2>/dev/null | cut -f1)"; [[ -n "$started" ]] || started="10 minutes ago"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  capture_volume_inventory "$id" migration-failure-config-inventory --legacy-config || true
  capture_volume_helper_output "$id" "${evidence}/migration-failure-files.json" migration-failure-files --root /config || true
  docker logs "$current" 2>&1 | redact > "${evidence}/migration-failure-current.log" || true
  docker logs "$proxy" 2>&1 | redact > "${evidence}/migration-failure-current-proxy.log" || true
  docker inspect --format 'name={{.Name}} status={{.State.Status}} running={{.State.Running}} exit={{.State.ExitCode}} restart_count={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} started={{.State.StartedAt}} ports={{json .NetworkSettings.Ports}} networks={{json .NetworkSettings.Networks}}' "$current" "$proxy" 2>&1 | redact > "${evidence}/migration-failure-container-state.txt" || true
  docker top "$current" -eo pid,ppid,stat,etime,args 2>&1 | redact > "${evidence}/migration-failure-current-processes.txt" || true
  : > "${evidence}/migration-failure-docker-events.txt"
  for container in "$current" "$proxy"; do
    timeout 10s docker events --since "$started" --until "$now" --filter "container=${container}" --format '{{.Time}} {{.Status}} {{.Actor.Attributes.name}}' 2>&1 | redact >> "${evidence}/migration-failure-docker-events.txt" || true
  done
  python - "$base" "${evidence}/migration-failure-http.json" <<'PY'
import json, sys, time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
base, output = sys.argv[1:]
def shape(value):
    if isinstance(value, dict): return {'type': 'object', 'keys': sorted(map(str, value))}
    if isinstance(value, list): return {'type': 'array', 'length': len(value)}
    return {'type': type(value).__name__}
def error_summary(error): return {'type': type(error).__name__, 'message_present': bool(getattr(error, 'args', ()))}
payload = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'url': base + '/server/migration/v1/status'}
try:
    with urlopen(payload['url'], timeout=10) as response:
        body = response.read().decode('utf-8')
        payload['status'] = response.status
        payload['headers'] = {key: value for key, value in response.headers.items() if key.lower() not in {'set-cookie', 'cookie', 'authorization'}}
        try:
            decoded = json.loads(body)
            payload['body_shape'] = shape(decoded)
        except json.JSONDecodeError:
            payload['body_shape'] = {'type': 'text', 'length': len(body)}
except (HTTPError, URLError, OSError) as error:
    payload['error'] = error_summary(error)
with open(output, 'w', encoding='utf-8') as stream:
    json.dump(payload, stream, indent=2, sort_keys=True)
PY
  audit_retained_run "$id" || true
}

apply_migration() {
  local id="$1" base="$2" out="$(evidence_dir "$id")/migration-status.json"
  if python - "$base" "$out" "$HELPER" <<'PY'
import importlib.util, json, sys, time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
base, output, helper_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location('ship_readiness_evidence', helper_path)
if spec is None or spec.loader is None:
    raise SystemExit('unable to load ship-readiness evidence helper')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
status_url = base + '/server/migration/v1/status'
observed = {'status_url': status_url, 'accepted': None, 'single_flight': None, 'final': None, 'timeline': []}
def error_summary(error): return {'type': type(error).__name__, 'message_present': bool(getattr(error, 'args', ()))}
def persist_failure(error):
    observed['failure'] = error_summary(error)
    with open(output, 'w', encoding='utf-8') as stream:
        json.dump(observed, stream, indent=2, sort_keys=True)
def status_snapshot(value): return helper.migration_status_evidence(value)
def event(name, status=None, body=None, error=None):
    item = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'event': name}
    if status is not None: item['http_status'] = status
    if body is not None: item['migration_status'] = status_snapshot(body)
    if error is not None: item['error'] = error_summary(error)
    observed['timeline'].append(item)
def request(url, data=None, headers=None):
    req = Request(url, data=data, headers=headers or {})
    with urlopen(req, timeout=10) as response:
        return response.status, json.loads(response.read().decode('utf-8'))
try:
    code, status = request(status_url)
    event('initial-status', code, status)
    observed['initial'] = {'http_status': code, 'migration_status': status_snapshot(status)}
    if code != 200 or status.get('state') != 'required':
        raise AssertionError('initial migration status contract failed')
    csrf = status['action']['csrf_token']; migration_id = status['migration_id']
    body = json.dumps({'confirmation': 'MIGRATE ' + migration_id, 'retry': False}).encode()
    helper.audit_retained_run(
        Path(output).parents[2], [csrf, 'MIGRATE ' + migration_id],
        Path(output).with_name('migration-secret-audit.json'),
    )
    headers = {'Origin': base, 'Content-Type': 'application/json', 'X-SeedSync-Migration-CSRF': csrf}
    code, accepted = request(base + '/server/migration/v1/apply', body, headers)
    event('apply-accepted', code, accepted)
    observed['accepted'] = {'http_status': code, 'migration_status': status_snapshot(accepted)}
    if code != 202:
        raise AssertionError('migration apply was not accepted')
    # A second same-token request is the single-flight probe; it must not start a
    # second transaction. Capture its HTTP status without treating timing as a pass.
    try:
        request(base + '/server/migration/v1/apply', body, headers)
        raise AssertionError('second apply unexpectedly accepted')
    except HTTPError as error:
        event('duplicate-apply', error.code, error=error)
        observed['single_flight'] = error.code
        if error.code != 409:
            raise AssertionError('duplicate migration apply did not report conflict')
    for _ in range(90):
        code, status = request(status_url)
        event('poll-status', code, status)
        observed['final'] = {'http_status': code, 'migration_status': status_snapshot(status)}
        if status.get('operation', {}).get('status') in ('succeeded', 'failed'):
            break
        time.sleep(.5)
    if status.get('operation', {}).get('status') != 'succeeded' or status.get('state') != 'complete':
        raise AssertionError('migration did not reach complete succeeded state')
    helper.audit_retained_run(
        Path(output).parents[2], [csrf, 'MIGRATE ' + migration_id],
        Path(output).with_name('migration-secret-audit-after-apply.json'),
    )
except Exception as error:
    event('failure', error=error)
    persist_failure(error)
    raise
with open(output, 'w', encoding='utf-8') as stream:
    json.dump(observed, stream, indent=2, sort_keys=True)
PY
  then
    row "$id" migration-restart-retry passed "evidence/ship-readiness/$(basename "$out")"
  else
    local status="$?"
    capture_apply_failure_bundle "$id" "$base" || true
    return "$status"
  fi
}

focused_migration_tests() {
  local id="$1" output="$(evidence_dir "$id")/focused-migration-tests.xml" log="$(evidence_dir "$id")/focused-migration-tests.log"
  # These intentionally focused tests own mutation/tamper/recovery details;
  # the Docker lane supplies the customer-shaped before/after/reboot evidence.
  bounded_command "$id" migration-focused-tests focused-migration-tests "$(timeout_seconds SEEDSYNC_SHIP_FOCUSED_PYTEST_TIMEOUT_SECONDS 900)" "$log" bash -lc 'cd -- "$1"; shift; exec python -m pytest -q "$@"' bash "$ROOT_DIR/src/python" \
    tests/unittests/test_migration_backup_restore.py::TestMigrationBackupRestore::test_tamper_blocks_before_destination_mutation_and_interrupted_restore_reruns \
    tests/unittests/test_migration_backup_restore.py::TestMigrationBackupRestore::test_live_normal_runtime_exclusion_blocks_apply_and_restore \
    tests/unittests/test_migration_coordinator.py::TestMigrationCoordinator::test_failure_retry_and_interrupted_restart_are_explicit \
    tests/unittests/test_migration_coordinator.py::TestMigrationCoordinator::test_legacy_shape_with_current_auth_state_is_ambiguous \
    tests/unittests/test_web/test_migration_web_app.py::TestMigrationWebApp::test_apply_is_background_single_flight_and_success_requests_normal_startup \
    tests/unittests/test_web/test_migration_web_app.py::TestMigrationWebApp::test_background_apply_failure_records_safe_diagnostic_without_request_values \
    --junitxml="$output"
  row "$id" focused-security-probes passed "evidence/ship-readiness/$(basename "$output")"
}

focused_after_tests() {
  local id="$1" safe_xml="$(evidence_dir "$id")/after-safe-operations.xml" safe_log="$(evidence_dir "$id")/after-safe-operations.log" notification_xml="$(evidence_dir "$id")/after-notification-redaction.xml" notification_log="$(evidence_dir "$id")/after-notification-redaction.log"
  bounded_command "$id" after-focused-safe-tests focused-safe-tests "$(timeout_seconds SEEDSYNC_SHIP_FOCUSED_PYTEST_TIMEOUT_SECONDS 900)" "$safe_log" bash -lc 'cd -- "$1"; shift; exec python -m pytest -q "$@"' bash "$ROOT_DIR/src/python" \
    tests/integration/test_web/test_handler/test_controller.py::TestControllerHandler::test_validate \
    tests/integration/test_web/test_handler/test_controller.py::TestControllerHandler::test_delete_local_rejects_path_traversal_without_path_leak \
    tests/unittests/test_web/test_handler/test_config_handler.py::TestConfigHandlerSet::test_set_general_exclude_patterns_requests_reconfigure_after_persist \
    tests/unittests/test_controller/test_controller.py::TestController::test_constructor_uses_rclone_backend_factory_when_selected \
    --junitxml="$safe_xml"
  row "$id" after-safe-operations passed "evidence/ship-readiness/$(basename "$safe_xml")"
  bounded_command "$id" after-focused-notification-tests focused-notification-tests "$(timeout_seconds SEEDSYNC_SHIP_FOCUSED_PYTEST_TIMEOUT_SECONDS 900)" "$notification_log" bash -lc 'cd -- "$1"; shift; exec python -m pytest -q "$@"' bash "$ROOT_DIR/src/python" \
    tests/unittests/test_web/test_serialize/test_serialize_config.py::TestSerializeConfig::test_notifications_are_redacted_with_explicit_configured_state \
    tests/unittests/test_web/test_handler/test_notifications_handler.py::TestNotificationsAdminHandler::test_admin_can_select_apprise_without_exposing_endpoint_key \
    --junitxml="$notification_xml"
  row "$id" after-notification-redaction passed "evidence/ship-readiness/$(basename "$notification_xml")"
  bounded_command "$id" after-focused-angular-tests focused-angular-tests "$(timeout_seconds SEEDSYNC_SHIP_FOCUSED_ANGULAR_TIMEOUT_SECONDS 1200)" "$(evidence_dir "$id")/after-files-pagination.log" bash -lc 'cd -- "$1"; shift; exec npx ng test "$@"' bash "$ROOT_DIR/src/angular" --watch=false --browsers=ChromeHeadless --progress=false \
    --include tests/unittests/services/files/view-file.service.spec.ts \
    --include tests/unittests/pages/files/file-list.component.spec.ts
  row "$id" after-files-pagination passed "evidence/ship-readiness/after-files-pagination.log"
}

publish_private_log() {
  local id="$1" source output
  [[ -n "$PRIVATE_LOG_ROOT" ]] || return 0
  source="$PRIVATE_LOG_ROOT/seedsync.log"
  output="$(evidence_dir "$id")/private-log-publication-summary.json"
  if [[ ! -e "$source" ]]; then
    [[ ! -e "$(run_dir "$id")/logs/seedsync.log" && ! -e "$(run_dir "$id")/logs/seedsync.log.publication.json" ]] || die "published log exists without its private source"
    return 0
  fi
  python "$HELPER" publish-private-log --source "$source" --root "$(run_dir "$id")" --run-id "$id" --output "$output"
}

audit_retained_run() {
  local id="$1" output="$(evidence_dir "$1")/retained-run-audit.json" failure="$(evidence_dir "$1")/retained-run-audit-failure.json" diagnostics="$(evidence_dir "$1")/retained-run-audit-diagnostics.txt" raw_dir raw_stdout raw_stderr status limit reason
  local -a private_log_arg=()
  # The known lab credential is supplied only over stdin and is never written
  # to the report. The audit also rejects raw browser-profile/status artifacts.
  AUDIT_RAN=1
  publish_private_log "$id"
  if [[ -n "$PRIVATE_LOG_ROOT" && -e "$PRIVATE_LOG_ROOT/seedsync.log" ]]; then
    private_log_arg=(--private-log-source "$PRIVATE_LOG_ROOT/seedsync.log")
  fi
  limit="$(timeout_seconds SEEDSYNC_SHIP_AUDIT_TIMEOUT_SECONDS 45)"
  raw_dir="$(mktemp -d /tmp/seedsync-retained-audit.XXXXXX)" || return 1
  chmod 700 "$raw_dir"; raw_stdout="$raw_dir/stdout"; raw_stderr="$raw_dir/stderr"
  phase "$id" audit-retained-evidence running "retained evidence audit; timeout ${limit}s"
  if printf '%s\0' 'remotepass' | timeout --foreground --signal=TERM --kill-after=10s "${limit}s" python "$HELPER" audit-retained-run --root "$(run_dir "$id")" --output "$output" --canaries-stdin "${private_log_arg[@]}" >"$raw_stdout" 2>"$raw_stderr"; then status=0; else status="${PIPESTATUS[1]}"; fi
  {
    printf 'schema=1\nphase=audit-retained-evidence\nexit_code=%s\ntimed_out=%s\n' "$status" "$([[ "$status" == 124 ]] && printf true || printf false)"
    printf '\nstdout-tail:\n'; tail -c 16384 "$raw_stdout" 2>/dev/null || true
    printf '\nstderr-tail:\n'; tail -c 16384 "$raw_stderr" 2>/dev/null || true
  } | redact > "$diagnostics"
  chmod 600 "$diagnostics"; rm -f -- "$raw_stdout" "$raw_stderr"; rmdir -- "$raw_dir" 2>/dev/null || true
  if [[ "$status" -ne 0 ]]; then
    reason="failed"; [[ "$status" == 124 ]] && reason="timeout"
    python "$HELPER" audit-failure-status --output "$failure" --reason "$reason" --exit-code "$status"
    FIRST_FAILURE_DETAIL="${FIRST_FAILURE_DETAIL:-audit retained evidence ${reason}; diagnostics evidence/ship-readiness/$(basename "$diagnostics")}"
    phase "$id" audit-retained-evidence failed "$FIRST_FAILURE_DETAIL"
    return "$status"
  fi
  phase "$id" audit-retained-evidence passed "retained evidence audit completed"
}

start_current() {
  local id="$1" port="$2" dir="$(run_dir "$1")" network="seedsync-upgrade-v086-lab-${1,,}" browser_network="seedsync-upgrade-v086-browser-${1,,}" image="seedsync/upgrade-v086:current-${1,,}" name="seedsync-upgrade-v086-current-${1,,}" proxy="seedsync-upgrade-v086-current-proxy-${1,,}"
  verify_config_volume "$id"
  docker container inspect "$name" >/dev/null 2>&1 && die "current container already exists for $id; choose a fresh RUN_ID"
  docker container inspect "$proxy" >/dev/null 2>&1 && die "current proxy already exists for $id; choose a fresh RUN_ID"
  bounded_command "$id" migration-current-image-build current-image-build "$(timeout_seconds SEEDSYNC_SHIP_CURRENT_BUILD_TIMEOUT_SECONDS 2700)" "$(evidence_dir "$id")/current-image-build.log" \
    docker build --pull=false -t "$image" -f "$ROOT_DIR/src/docker/build/docker-image/Dockerfile" "$ROOT_DIR"
  bounded_command "$id" migration-current-start current-container-start "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-container-id.txt" \
    docker run -d --name "$name" --network "$network" --network-alias current \
    -e SEEDSYNC_WEB_BIND_HOST=0.0.0.0 -e "SEEDSYNC_BROWSER_HANDOVER_RECOVERY_VERSION=upgrade-v086-${id}" --mount "type=volume,src=$(config_volume "$id"),dst=/config" -v "$dir/downloads:/downloads" -v "$dir/mounts:/mounts" -v "$(private_log_mount_dir "$id"):/logs" "$image"
  bounded_command "$id" migration-current-proxy-start current-proxy-start "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-proxy-id.txt" \
    docker run -d --name "$proxy" --network "$browser_network" -p "127.0.0.1:${port}:8800" --read-only --security-opt no-new-privileges:true --cap-drop ALL \
    --tmpfs /tmp:mode=1777 --tmpfs /var/run:mode=1777 -v "$CURRENT_PROXY_CONFIG:/etc/nginx/nginx.conf:ro" \
    --entrypoint /usr/sbin/nginx seedsync/upgrade-v086/proxy -g 'daemon off;'
  bounded_command "$id" migration-current-proxy-connect-lab current-proxy-connect-lab "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-proxy-connect-lab.log" \
    docker network connect "$network" "$proxy"
  assert_current_topology "$id" "$port"
  capture_current_provenance "$id"
}
configure_current_browser_bootstrap_runtime() {
  local id="$1" image="$2" proxy="seedsync-upgrade-v086-current-proxy-${1,,}" network="seedsync-upgrade-v086-lab-${1,,}" writer="seedsync-upgrade-v086-browser-bootstrap-config-${1,,}" cidr output
  cidr="$(python - "$proxy" "$network" <<'PY'
import ipaddress, json, subprocess, sys
proxy, network = sys.argv[1:]
item = json.loads(subprocess.check_output(["docker", "inspect", proxy], text=True))[0]
details = (item.get("NetworkSettings") or {}).get("Networks") or {}
address = details.get(network, {}).get("IPAddress")
try:
    parsed = ipaddress.ip_address(address)
except ValueError as error:
    raise SystemExit("current proxy lab address is invalid") from error
if parsed.version != 4 or not parsed.is_private or parsed.is_loopback:
    raise SystemExit("current proxy lab address is not an isolated IPv4 source")
print("{}/32".format(parsed))
PY
)" || die "unable to determine the current proxy's isolated lab address"
  output="$(evidence_dir "$id")/current-browser-bootstrap-runtime.json"
  bounded_command "$id" migration-current-browser-bootstrap-config current-browser-bootstrap-config "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-browser-bootstrap-config.log" \
    docker run --rm --name "$writer" --network none --user 1000:1000 --read-only --security-opt no-new-privileges:true --cap-drop ALL --tmpfs /tmp:mode=1777 \
    --mount "type=volume,src=$(config_volume "$id"),dst=/config" --entrypoint python "$image" -c '
import configparser, ipaddress, os, sys
cidr = sys.argv[1]
network = ipaddress.ip_network(cidr, strict=True)
if network.version != 4 or network.prefixlen != 32 or network.network_address.is_loopback or not network.network_address.is_private:
    raise SystemExit("trusted bootstrap source must be an isolated IPv4 /32")
path = "/config/settings.cfg"
parser = configparser.RawConfigParser()
parser.optionxform = str
if not parser.read(path, encoding="utf-8") or not parser.has_section("General"):
    raise SystemExit("current settings are unavailable for browser bootstrap runtime configuration")
parser.set("General", "trusted_browser_bootstrap_remote_addrs", cidr)
temporary = path + ".browser-bootstrap.tmp"
with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
    parser.write(stream)
os.chmod(temporary, 0o600)
os.replace(temporary, path)
' "$cidr"
  python - "$output" <<'PY'
import json, os, sys
output = sys.argv[1]
temporary = output + ".tmp"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump({
        "schema": 1,
        "phase": "after-migration-before-first-claim",
        "trusted_source": "current-proxy-isolated-lab-ipv4-32",
        "recovery_version_input": "configured-at-current-start",
    }, stream, sort_keys=True)
    stream.write("\n")
os.chmod(temporary, 0o600)
os.replace(temporary, output)
PY
}

assert_current_topology() {
  local id="$1" port="$2" output="$(evidence_dir "$1")/current-topology.json" failure="$(evidence_dir "$1")/current-topology-failure.json"
  python - "$id" "$port" "$output" "$failure" <<'PY'
import json, subprocess, sys
run_id, port, output, failure_output = sys.argv[1:]
names = {
    'current': 'seedsync-upgrade-v086-current-' + run_id.lower(),
    'current_proxy': 'seedsync-upgrade-v086-current-proxy-' + run_id.lower(),
    'legacy': 'seedsync-upgrade-v086-' + run_id.lower(),
    'legacy_proxy': 'seedsync-upgrade-v086-proxy-' + run_id.lower(),
    'remote': 'seedsync-upgrade-v086-ssh-' + run_id.lower(),
    'validator': 'seedsync-upgrade-v086-validator-' + run_id.lower(),
    'snapshotter': 'seedsync-upgrade-v086-snapshotter-' + run_id.lower(),
}
inspect = json.loads(subprocess.check_output(['docker', 'inspect', *names.values()], text=True))
by_name = {entry['Name'].lstrip('/'): entry for entry in inspect}
lab = 'seedsync-upgrade-v086-lab-' + run_id.lower()
browser = 'seedsync-upgrade-v086-browser-' + run_id.lower()
summary = {}
for role, name in names.items():
    entry = by_name[name]
    summary[role] = {
        'name': name,
        'networks': sorted(entry['NetworkSettings']['Networks']),
        'ports': entry['NetworkSettings']['Ports'],
        'image_id': entry['Image'],
        'running': entry['State']['Running'],
        'read_only_rootfs': entry['HostConfig']['ReadonlyRootfs'],
        'cap_drop': sorted(entry['HostConfig'].get('CapDrop') or []),
        'security_opt': sorted(entry['HostConfig'].get('SecurityOpt') or []),
        'network_mode': entry['HostConfig'].get('NetworkMode'),
        'user': entry['Config'].get('User'),
        'mounts': [{'destination': mount.get('Destination'), 'name': mount.get('Name'), 'rw': mount.get('RW')} for mount in entry.get('Mounts', [])],
    }
run_prefix = 'seedsync-upgrade-v086-'
run_suffix = '-' + run_id.lower()
running_all = set(filter(None, subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}'], text=True).splitlines()))
running = {name for name in running_all if name.startswith(run_prefix) and name.endswith(run_suffix)}
expected_running = {names['current'], names['current_proxy'], names['remote'], names['validator'], names['snapshotter']}
def fail(reason):
    payload = {
        'schema': 1, 'run_id': run_id, 'reason': reason,
        'lab_network': lab, 'browser_network': browser,
        'expected_running': sorted(expected_running), 'running_containers': sorted(running),
        'containers': summary,
    }
    with open(failure_output, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
    raise SystemExit('current topology assertion failed: ' + reason)
def require(condition, reason):
    if not condition: fail(reason)
require(summary['current']['networks'] == [lab], 'current app must be lab-only')
require(summary['legacy']['networks'] == [lab], 'legacy app must remain lab-only')
require(summary['remote']['networks'] == [lab], 'remote fixture must remain lab-only')
require(summary['current_proxy']['networks'] == sorted([browser, lab]), 'current proxy must bridge browser and lab only')
require(summary['legacy_proxy']['networks'] == sorted([browser, lab]), 'legacy proxy wiring must remain recorded')
require(all(not bindings for bindings in summary['current']['ports'].values()), 'current app must not publish host ports')
bindings = summary['current_proxy']['ports'].get('8800/tcp') or []
require(bindings == [{'HostIp': '127.0.0.1', 'HostPort': port}], 'current proxy must own the configured loopback port')
require(summary['current_proxy']['read_only_rootfs'] is True, 'current proxy root filesystem must be read-only')
require(summary['current_proxy']['cap_drop'] == ['ALL'], 'current proxy capabilities must be dropped')
require('no-new-privileges:true' in summary['current_proxy']['security_opt'], 'current proxy must prohibit new privileges')
require(summary['validator']['network_mode'] == 'none', 'validator must have no network')
require(summary['validator']['read_only_rootfs'] is True, 'validator root filesystem must be read-only')
require(summary['validator']['cap_drop'] == ['ALL'], 'validator capabilities must be dropped')
require('no-new-privileges:true' in summary['validator']['security_opt'], 'validator must prohibit new privileges')
require(summary['validator']['user'] == '1000:1000', 'validator must run as the retained-volume owner')
validator_config = [mount for mount in summary['validator']['mounts'] if mount['destination'] == '/config']
require(len(validator_config) == 1 and validator_config[0]['name'] == 'seedsync-upgrade-v086-config-' + run_id.lower() and validator_config[0]['rw'] is False, 'validator config mount must be the exact retained volume read-only')
validator_protected = [mount for mount in summary['validator']['mounts'] if mount['destination'] == '/protected']
require(len(validator_protected) == 1 and validator_protected[0]['name'] == 'seedsync-upgrade-v086-protected-' + run_id.lower() and validator_protected[0]['rw'] is False, 'validator protected mount must be the exact retained volume read-only')
require(summary['snapshotter']['network_mode'] == 'none', 'snapshotter must have no network')
require(summary['snapshotter']['read_only_rootfs'] is True, 'snapshotter root filesystem must be read-only')
require(summary['snapshotter']['cap_drop'] == ['ALL'], 'snapshotter capabilities must be dropped')
require('no-new-privileges:true' in summary['snapshotter']['security_opt'], 'snapshotter must prohibit new privileges')
require(summary['snapshotter']['user'] == '1000:1000', 'snapshotter must run as the retained-volume owner')
snapshotter_config = [mount for mount in summary['snapshotter']['mounts'] if mount['destination'] == '/config']
snapshotter_protected = [mount for mount in summary['snapshotter']['mounts'] if mount['destination'] == '/protected']
require(len(snapshotter_config) == 1 and snapshotter_config[0]['name'] == 'seedsync-upgrade-v086-config-' + run_id.lower() and snapshotter_config[0]['rw'] is False, 'snapshotter config mount must be the exact retained volume read-only')
require(len(snapshotter_protected) == 1 and snapshotter_protected[0]['name'] == 'seedsync-upgrade-v086-protected-' + run_id.lower() and snapshotter_protected[0]['rw'] is True, 'snapshotter protected mount must be the exact retained volume writable')
require(running == expected_running, 'only current app, current proxy, remote fixture, validator, and snapshotter may run for this lab')
require(summary['legacy']['running'] is False and summary['legacy_proxy']['running'] is False, 'legacy app and proxy must be stopped')
dual_homed = {role for role, entry in summary.items() if entry['running'] and set(entry['networks']) == {lab, browser}}
require(dual_homed == {'current_proxy'}, 'current proxy must be the sole running browser-to-lab bridge')
with open(output, 'w', encoding='utf-8') as stream:
    json.dump({'run_id': run_id, 'lab_network': lab, 'browser_network': browser, 'running_containers': sorted(running), 'containers': summary}, stream, indent=2, sort_keys=True)
PY
}

capture_current_provenance() {
  local id="$1" output="$(evidence_dir "$1")/current-runtime-provenance.json"
  python - "$ROOT_DIR" "$id" "$output" <<'PY'
import hashlib, json, pathlib, subprocess, sys
root, run_id, output = map(pathlib.Path, sys.argv[1:])
def command(*args): return subprocess.check_output(args, cwd=root, text=True).strip()
def digest(path): return hashlib.sha256((root / path).read_bytes()).hexdigest()
name = 'seedsync-upgrade-v086-current-' + run_id.name.lower()
container = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', container['Image']], text=True))[0]
inputs = [
    'src/docker/build/docker-image/Dockerfile', 'src/docker/build/docker-image/entrypoint.sh',
    'src/python/pyproject.toml', 'src/python/poetry.lock', 'src/angular/package.json',
    'src/docker/test/upgrade-v086/current-proxy-nginx.conf',
]
payload = {
    'run_id': run_id.name,
    'git_head': command('git', 'rev-parse', 'HEAD'),
    'git_tree': command('git', 'rev-parse', 'HEAD^{tree}'),
    'worktree_dirty_fingerprint': hashlib.sha256((command('git', 'status', '--porcelain=v1', '-z') + command('git', 'diff', '--binary') + command('git', 'diff', '--cached', '--binary')).encode()).hexdigest(),
    'build_input_hashes': {path: digest(path) for path in inputs},
    'image_inspect': {'id': image['Id'], 'repo_digests': image.get('RepoDigests', []), 'created': image.get('Created'), 'config': image.get('Config', {}), 'rootfs': image.get('RootFS', {})},
    'container_inspect': {'name': name, 'id': container['Id'], 'image_id': container['Image'], 'created': container['Created'], 'host_config': container['HostConfig'], 'network_settings': container['NetworkSettings']},
}
if payload['container_inspect']['image_id'] != payload['image_inspect']['id']:
    raise SystemExit('current container image identity differs from inspected image')
json.dump(payload, open(output, 'w'), indent=2, sort_keys=True)
PY
}

stop_container() {
  local id="$1" phase_name="$2" label="$3" name="$4" dispatch_mode="${5:-}" generation="${6:-}" arm_generation="${7:-}"
  docker container inspect "$name" >/dev/null 2>&1 || return 0
  if [[ "$dispatch_mode" == restart-dispatch ]]; then
    # Publish from the timeout-launched command immediately before exec'ing
    # docker, so a successful marker is never produced merely on function entry.
    export -f publish_browser_restart_stop_dispatch
    bounded_command "$id" "$phase_name" "$label" "$(timeout_seconds SEEDSYNC_SHIP_STOP_TIMEOUT_SECONDS 45)" "$(evidence_dir "$id")/${label}.log" bash -c '
      publish_browser_restart_stop_dispatch "$1" "$2" "$3" "$4" || exit $?
      exec docker stop --time 20 "$5"
    ' -- "$id" "$(evidence_dir "$id")" "$generation" "$arm_generation" "$name"
    return
  elif [[ -n "$dispatch_mode" ]]; then
    die "unknown stop dispatch mode: $dispatch_mode"
  fi
  bounded_command "$id" "$phase_name" "$label" "$(timeout_seconds SEEDSYNC_SHIP_STOP_TIMEOUT_SECONDS 45)" "$(evidence_dir "$id")/${label}.log" docker stop --time 20 "$name"
}

wait_for_downloads() {
  local id="$1" root="$(run_dir "$1")/downloads" output="$(evidence_dir "$1")/after-transfer-observations.txt" archive_validation="$(evidence_dir "$1")/after-transfer-archive-validation.json" attempts=0
  until [[ -f "$root/negative-nonmatch.bin" && -f "$root/root-directory-stopped/partial.txt" && -f "$root/transient-manual.zip" ]] && find "$root" -type f -name payload.bin -print -quit | grep -q .; do
    attempts=$((attempts + 1)); (( attempts < 120 )) || die "queued transfer/resume did not settle within 120 seconds"
    sleep 1
  done
  # The fixture archive is inspected in-place by the repository helper.  It
  # never extracts on the WSL host and writes a safe failure artifact first.
  python "$HELPER" verify-download-archive --archive "$root/transient-manual.zip" --fixture-manifest "$LAB_DIR/fixture-manifest.json" --case-id transient-manual --output "$archive_validation"
  {
    sha256sum "$root/negative-nonmatch.bin" "$root/root-directory-stopped/partial.txt" "$root/transient-manual.zip"
    find "$root" -type f -name payload.bin -exec sha256sum {} +
    printf 'archive-validation=%s\n' "$(basename "$archive_validation")"
  } > "$output"
}

assert_current_runtime_health() {
  local id="$1" container="seedsync-upgrade-v086-current-${1,,}" output="$(evidence_dir "$1")/current-runtime-health.txt"
  docker inspect --format 'status={{.State.Status}} running={{.State.Running}} exit={{.State.ExitCode}} started={{.State.StartedAt}}' "$container" > "$output"
  docker logs "$container" 2>&1 | redact >> "$output"
  grep -q 'status=running running=true exit=0' "$output" || die "current runtime is not running"
  ! grep -Eq 'ScannerError|Traceback \(most recent call last\)' "$output" || die "current runtime logs show scan/controller failure"
}

migration_backup_dir() {
  local id="$1" output
  output="$(evidence_dir "$id")/migration-backup-reference.json"
  capture_volume_helper_output "$id" "$output" assert-migration --config-root /config --auth-store-phase post-start --product-auth-contract "$(validator_evidence_path current-product-preclaim-auth-contract.json)"
  python - "$output" <<'PY'
import json, sys
reference = json.load(open(sys.argv[1], encoding="utf-8"))["receipt"]["backup"]
if not isinstance(reference, str) or not reference.startswith("migration-backups/"):
    raise SystemExit("migration backup reference is unsafe")
print("/config/" + reference)
PY
}

full() {
  local id; id="$(run_id)"; validate_id "$id"
  local legacy_port="${HOST_PORT:-18806}" current_port="${CURRENT_PORT:-18816}"
  validate_port HOST_PORT "$legacy_port"
  validate_port CURRENT_PORT "$current_port"
  [[ "$legacy_port" != "$current_port" ]] || die "HOST_PORT and CURRENT_PORT must differ"
  require_tools
  run_lab_bootstrap_bounded "$id" "$legacy_port" "$(timeout_seconds SEEDSYNC_SHIP_LEGACY_BUILD_TIMEOUT_SECONDS 2700)"
  mkdir -p "$(evidence_dir "$id")"
  initialize_private_staging_root "$id"
  python "$HELPER" matrix-init --run-id "$id" --output "$(matrix "$id")"
  ACTIVE_RUN_ID="$id"
  trap finish_full EXIT
  trap 'cleanup_browser_claim_reuse; exit 129' HUP
  trap 'cleanup_browser_claim_reuse; exit 130' INT
  trap 'cleanup_browser_claim_reuse; exit 143' TERM
  phase "$id" preflight running "unique run ${id}; legacy port ${legacy_port}; current port ${current_port}"
  phase "$id" before running
  run_lab_bounded "$id" "$legacy_port" before-legacy-start legacy-start "$(timeout_seconds SEEDSYNC_SHIP_LEGACY_LAB_TIMEOUT_SECONDS 900)" "$(evidence_dir "$id")/legacy-start.log" start
  # The pinned entrypoint creates its historical config; seed the representative
  # non-default fixture only after that creation, then restart to load it.
  local legacy_container="seedsync-upgrade-v086-${id,,}"
  preflight_volume_private_storage "$id" "$legacy_container"
  seed_volume_settings "$legacy_container" "$(evidence_dir "$id")/before-settings-seed.json"
  stop_container "$id" before-seed-restart-stop before-seed-restart-stop "$legacy_container"
  run_lab_bounded "$id" "$legacy_port" before-legacy-restart legacy-restart "$(timeout_seconds SEEDSYNC_SHIP_LEGACY_LAB_TIMEOUT_SECONDS 900)" "$(evidence_dir "$id")/legacy-restart.log" start
  run_lab_bounded "$id" "$legacy_port" before-legacy-status legacy-status "$(timeout_seconds SEEDSYNC_SHIP_LEGACY_LAB_TIMEOUT_SECONDS 900)" "$(evidence_dir "$id")/legacy-status.log" status
  phase "$id" before-legacy-http-wait running "waiting for legacy root HTTP readiness"
  wait_http "http://127.0.0.1:${legacy_port}/" "$(evidence_dir "$id")/before-legacy-root.html" "$id" before-legacy-http-wait "$legacy_container"
  phase "$id" before-legacy-browser-launch running "launching legacy browser evidence"
  run_browser_bounded "$id" before-legacy-browser-launch "http://127.0.0.1:${legacy_port}" "$(evidence_dir "$id")" legacy
  phase "$id" before-legacy-browser-assert running "asserting first legacy browser evidence"
  python "$HELPER" assert-legacy-browser --input "$(evidence_dir "$id")/browser-legacy.json" --output "$(evidence_dir "$id")/before-legacy-browser-contract.json"
  capture_redacted_volume_settings "$id" before-settings-redacted
  capture_inventory "$id" before-downloads "$(run_dir "$id")/downloads"
  capture_inventory "$id" before-remote-files "$(run_dir "$id")/remote-files"
  cp "$(run_dir "$id")/evidence/model.json" "$(evidence_dir "$id")/before-model.json"
  capture_volume_behavior_contract "$id" /evidence/ship-readiness/before-model.json /evidence/fixture-evidence.json "$(evidence_dir "$id")/before-behavior-contract.json"
  row "$id" before-legacy-ui-api-model passed "evidence/ship-readiness/before-legacy-browser-contract.json"
  row "$id" before-legacy-ui-api-model passed "evidence/ship-readiness/before-behavior-contract.json"
  phase "$id" migration-stop-legacy running "stop only the legacy app; remote fixture and run evidence are retained"
  stop_container "$id" migration-stop-legacy migration-stop-legacy "seedsync-upgrade-v086-${id,,}"
  stop_container "$id" migration-stop-legacy-proxy migration-stop-legacy-proxy "seedsync-upgrade-v086-proxy-${id,,}"
  capture_before_filesystem_inventory "$id"
  start_current "$id" "$current_port"
  local current="http://127.0.0.1:${current_port}"
  wait_migration_status "$current/server/migration/v1/status" "$(evidence_dir "$id")/migration-required.json" "$id" migration-current-status "seedsync-upgrade-v086-current-${id,,}"
  security_probes "$id" "$current"
  apply_migration "$id" "$current"
  python "$HELPER" assert-migration-apply-auth-boundary --status "$(evidence_dir "$id")/migration-status.json" --legacy-auth "$(evidence_dir "$id")/before-legacy-auth-absence.json" --output "$(evidence_dir "$id")/migration-apply-auth-contract.json"
  cross_migration_runtime_boundary "$id" "$(evidence_dir "$id")/migration-status.json" "seedsync-upgrade-v086-current-${id,,}"
  configure_current_browser_bootstrap_runtime "$id" "seedsync/upgrade-v086:current-${id,,}"
  bounded_command "$id" migration-current-normal-transition-start current-normal-transition-start "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-normal-transition-start.txt" docker start "seedsync-upgrade-v086-current-${id,,}"
  wait_normal_runtime_readiness "$current" "$(evidence_dir "$id")/after-normal-runtime-transition.json" "$id" migration-current-normal-transition-ready "seedsync-upgrade-v086-current-${id,,}"
  focused_migration_tests "$id"
  local current_container="seedsync-upgrade-v086-current-${id,,}"
  capture_volume_inventory "$id" after-config
  capture_redacted_volume_settings "$id" after-settings-redacted
  # Normal startup may still hold its first-run auth persistence cycle open.
  # Validate only the graceful-stop, durable pre-claim state, then restart the
  # same current container before exercising the browser handover.
  stop_container "$id" migration-current-preclaim-auth-flush current-preclaim-auth-flush "$current_container"
  current_product_preclaim_auth_contract "$id"
  bounded_command "$id" migration-current-preclaim-restart current-preclaim-restart "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-preclaim-restart.txt" docker start "$current_container"
  wait_normal_runtime_readiness "$current" "$(evidence_dir "$id")/after-current-preclaim-restart.json" "$id" migration-current-preclaim-restart-ready "$current_container"
  capture_volume_helper_output "$id" "$(evidence_dir "$id")/migration-contract.json" assert-migration --config-root /config --auth-store-phase post-start --product-auth-contract "$(validator_evidence_path current-product-preclaim-auth-contract.json)"
  local retained_backup_dir
  retained_backup_dir="$(migration_backup_dir "$id")"
  capture_volume_helper_output "$id" "$(evidence_dir "$id")/migrated-settings-contract.json" assert-migrated-settings --before "$retained_backup_dir/data/settings.cfg" --after /config/settings.cfg
  start_browser_claim_reuse "$id" "$current" "$(evidence_dir "$id")"
  wait_browser_claim_reuse_ready "$(evidence_dir "$id")"
  wait_for_downloads "$id"
  # The in-memory browser session must prove its pre-restart state before the
  # current container is stopped.  Claim-ready only means the shell can begin
  # this checkpoint; it is not authorization to consume the restart handoff.
  request_browser_stability "$id" "$(evidence_dir "$id")"
  local stability_generation restart_arm_generation
  stability_generation="$(wait_browser_stability_ready "$id" "$(evidence_dir "$id")")" || die "browser stability generation is unavailable"
  restart_arm_generation="$(arm_browser_restart "$id" "$(evidence_dir "$id")" "$stability_generation")" || die "browser restart arm generation is unavailable"
  wait_browser_restart_arm_ack "$id" "$(evidence_dir "$id")" "$stability_generation" "$restart_arm_generation"
  python "$HELPER" assert-browser --input "$(evidence_dir "$id")/browser.json" --output "$(evidence_dir "$id")/after-browser-claim-contract.json"
  capture_volume_helper_output "$id" "$(evidence_dir "$id")/current-model-contract.json" assert-current-model --before-settings "$retained_backup_dir/data/settings.cfg" --path-pairs /config/path_pairs.json --browser /evidence/ship-readiness/browser.json --fixture /evidence/fixture-evidence.json
  stop_container "$id" migration-current-restart-stop migration-current-restart-stop "seedsync-upgrade-v086-current-${id,,}" restart-dispatch "$stability_generation" "$restart_arm_generation"
  current_product_claimed_auth_contract "$id"
  bounded_command "$id" migration-current-restart-start current-restart "$(timeout_seconds SEEDSYNC_SHIP_CONTAINER_TIMEOUT_SECONDS 90)" "$(evidence_dir "$id")/current-restart.txt" docker start "seedsync-upgrade-v086-current-${id,,}"
  wait_normal_runtime_readiness "$current" "$(evidence_dir "$id")/after-restart-claimed-auth.json" "$id" migration-current-restart-status "seedsync-upgrade-v086-current-${id,,}"
  finish_browser_claim_reuse "$(evidence_dir "$id")" "$stability_generation" "$restart_arm_generation"
  python "$HELPER" assert-browser --input "$(evidence_dir "$id")/browser-reuse.json" --output "$(evidence_dir "$id")/after-browser-restart-contract.json" --reuse
  capture_volume_helper_output "$id" "$(evidence_dir "$id")/autoqueue-contract.json" assert-autoqueue --before-settings "$retained_backup_dir/data/settings.cfg" --persist /config/autoqueue.persist --browser /evidence/ship-readiness/browser.json --fixture /evidence/fixture-evidence.json --controller /config/controller.persist
  printf 'controller.persist and autoqueue.persist preserved across current restart\n' > "$(evidence_dir "$id")/current-restart-persist.txt"
  assert_current_runtime_health "$id"
  row "$id" migration-retained-backup passed "evidence/ship-readiness/migration-contract.json"
  row "$id" migration-retained-backup passed "evidence/ship-readiness/current-runtime-provenance.json"
  row "$id" migration-transform passed "evidence/ship-readiness/migration-contract.json"
  row "$id" migration-transform passed "evidence/ship-readiness/migration-apply-auth-contract.json"
  row "$id" migration-transform passed "evidence/ship-readiness/current-topology.json"
  row "$id" after-first-claim passed "evidence/ship-readiness/after-browser-claim-contract.json"
  row "$id" after-authenticated-api passed "evidence/ship-readiness/after-browser-claim-contract.json"
  row "$id" after-legacy-values passed "evidence/ship-readiness/migrated-settings-contract.json"
  row "$id" after-scan-model passed "evidence/ship-readiness/current-model-contract.json"
  row "$id" after-scan-model passed "evidence/ship-readiness/current-runtime-health.txt"
  row "$id" after-extract-autoqueue passed "evidence/ship-readiness/autoqueue-contract.json"
  row "$id" after-extract-autoqueue passed "evidence/ship-readiness/after-transfer-observations.txt"
  row "$id" after-remembered-browser passed "evidence/ship-readiness/after-browser-restart-contract.json"
  row "$id" after-transfer-resume passed "evidence/ship-readiness/after-transfer-observations.txt"
  focused_after_tests "$id"
  phase "$id" restore-current-stop running "offline restore from retained backup"
  local backup
  backup="${retained_backup_dir##*/}"
  [[ -n "$backup" ]] || die "retained migration backup was not found"
  stop_container "$id" restore-current-stop restore-current-stop "seedsync-upgrade-v086-current-${id,,}"
  snapshot_volume_config "$id" after-current-restart after-config
  verify_config_volume "$id"
  verify_protected_volume "$id"
  verify_snapshot_for_consumer "$id" after-current-restart after-config "$(evidence_dir "$id")/after-current-restart-archive-consumer-verification.json"
  bounded_command "$id" restore-offline restore-offline "$(timeout_seconds SEEDSYNC_SHIP_RESTORE_TIMEOUT_SECONDS 180)" "$(evidence_dir "$id")/restore.log" docker run --name "seedsync-upgrade-v086-restore-${id,,}" --network none \
    --mount "type=volume,src=$(protected_volume "$id"),dst=/protected,readonly" \
    --mount "type=volume,src=$(config_volume "$id"),dst=/config" "seedsync/upgrade-v086:current-${id,,}" \
    /bin/bash -lc "python -c 'import tarfile; tarfile.open(\"/protected/after-current-restart.tar\").getmembers()' && exec python /app/python/seedsync.py -c /config --html /app/html --scanfs /app/python/scan_fs.py --restore-migration-backup '$backup' --confirm-restore --confirm-stopped"
  row "$id" restore-offline passed "evidence/ship-readiness/restore.log"
  run_lab_bounded "$id" "$legacy_port" restore-legacy-start restore-legacy-start "$(timeout_seconds SEEDSYNC_SHIP_LEGACY_LAB_TIMEOUT_SECONDS 900)" "$(evidence_dir "$id")/restore-legacy-start.log" start
  run_lab_bounded "$id" "$legacy_port" restore-legacy-status restore-legacy-status "$(timeout_seconds SEEDSYNC_SHIP_LEGACY_LAB_TIMEOUT_SECONDS 900)" "$(evidence_dir "$id")/restore-legacy-status.log" status
  run_browser "$id" "http://127.0.0.1:${legacy_port}" "$(evidence_dir "$id")" legacy-restore
  python "$HELPER" assert-legacy-browser --input "$(evidence_dir "$id")/browser-legacy-restore.json" --output "$(evidence_dir "$id")/after-restore-legacy-browser-contract.json"
  cp "$(run_dir "$id")/evidence/model.json" "$(evidence_dir "$id")/after-reboot-model.json"
  capture_volume_behavior_contract "$id" /evidence/ship-readiness/after-reboot-model.json /evidence/fixture-evidence.json "$(evidence_dir "$id")/after-reboot-behavior-contract.json"
  stop_container "$id" restore-legacy-stop restore-legacy-stop "$legacy_container"
  stop_container "$id" restore-legacy-proxy-stop restore-legacy-proxy-stop "seedsync-upgrade-v086-proxy-${id,,}"
  capture_volume_inventory "$id" restore-config --legacy-config
  snapshot_volume_config "$id" after-restore-config restore-config
  python - "$HELPER" "$(evidence_dir "$id")/before-config.json" "$(evidence_dir "$id")/restore-config.json" "$(evidence_dir "$id")/restore-config-compare.json" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ship_readiness", sys.argv[1]); helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
before, after, output = map(__import__("pathlib").Path, sys.argv[2:])
differences = helper.compare(json.loads(before.read_text()), json.loads(after.read_text()))
helper.json_dump(output, {"legacy_inventory_equal": not differences, "different_paths": differences, "comparison_storage": "docker-named-volume"})
if differences: raise SystemExit("restored config inventory differs")
PY
  row "$id" restore-exact-inventory passed "evidence/ship-readiness/restore-config-compare.json"
  row "$id" restore-infrastructure passed "evidence/ship-readiness/restore-config-compare.json"
  python "$HELPER" compare-contract --expected "$(evidence_dir "$id")/before-behavior-contract.json" --actual "$(evidence_dir "$id")/after-reboot-behavior-contract.json" --output "$(evidence_dir "$id")/reboot-parity.json"
  row "$id" restore-pinned-reboot passed "evidence/ship-readiness/after-restore-legacy-browser-contract.json"
  row "$id" restore-pinned-reboot passed "evidence/ship-readiness/reboot-parity.json"
  audit_retained_run "$id"
  python "$HELPER" matrix-verify --matrix "$(matrix "$id")"
  phase "$id" complete passed "all matrix rows have evidence; containers and artifacts intentionally retained"
  FINAL_OUTCOME="passed"
  printf 'Ship-readiness evidence: %s\n' "$(evidence_dir "$id")"
}

worker_self_check() {
  # This is intentionally non-destructive: no build, no run directory, no Docker container.
  python "$HELPER" self-test
  python "$LAB_DIR/test_ship_readiness.py"
  bash -n "$0" "$LAB"
  fresh_repo_shell bash -c 'test "$PWD" = "$1"' bash "$ROOT_DIR"
  browser_dispatch_self_check
  browser_shutdown_self_check
  browser_readiness_policy_self_check
  browser_session_temp_cleanup_self_check
  browser_parent_cleanup_self_check
  bounded_command_self_check
  port_validation_self_check
  preclaim_auth_lifecycle_self_check
  volume_helper_output_self_check
  topology_and_apply_contract_self_check
  fresh_repo_shell bash "$LAB" preflight
  echo "ship-readiness worker self-check: passed (not final verification)"
}
volume_helper_output_self_check() {
  local temp_root evidence raw_stdout_canary='volume-helper-stdout-canary' raw_stderr_canary='volume-helper-stderr-canary' json_canary='volume-helper-json-canary' url_canary='volume-helper-url-canary' header_canary='volume-helper-header-canary' cookie_canary='volume-helper-cookie-canary' quoted_cookie_canary='volume-helper-quoted-cookie-canary'
  temp_root="$(mktemp -d /tmp/seedsync-volume-helper-lifecycle.XXXXXX)"
  evidence="$(mktemp -d /tmp/seedsync-volume-helper-evidence.XXXXXX)"
  (
    evidence_dir() { printf '%s' "$evidence"; }
    phase() { printf '%s\n' "$*" >> "$evidence/phases.txt"; }
    volume_helper() { printf '{"schema":1,"ok":true}\n'; }
    SEEDSYNC_SHIP_VOLUME_HELPER_TEMP_ROOT="$temp_root" capture_volume_helper_output helper-success "$evidence/success.json" self-check
  )
  [[ -s "$evidence/success.json" ]] || die "volume helper success self-check did not create a non-empty final JSON"
  [[ -z "$(find "$temp_root" -mindepth 1 -print -quit)" ]] || die "volume helper success self-check left raw temporary files"
  if (
    evidence_dir() { printf '%s' "$evidence"; }
    phase() { printf '%s\n' "$*" >> "$evidence/phases.txt"; }
    volume_helper() {
      printf '{"partial":"%s"' "$raw_stdout_canary"
      printf 'Set-Cookie: %s\nCookie: %s\n"Cookie": %s\napi_key=%s\n{"api_key":"%s"}\nhttps://invalid/?token=%s\n"Authorization": %s\n' "$raw_stderr_canary" "$cookie_canary" "$quoted_cookie_canary" "$raw_stderr_canary" "$json_canary" "$url_canary" "$header_canary" >&2
      return 7
    }
    SEEDSYNC_SHIP_VOLUME_HELPER_TEMP_ROOT="$temp_root" capture_volume_helper_output helper-failure "$evidence/failure.json" self-check
  ); then
    die "volume helper failure self-check unexpectedly passed"
  fi
  [[ ! -e "$evidence/failure.json" ]] || die "volume helper failure self-check retained a final JSON"
  [[ -s "$evidence/failure-helper-failure.json" ]] || die "volume helper failure self-check did not retain safe diagnostics"
  grep -q 'helper-failure failed' "$evidence/phases.txt" || die "volume helper failure self-check did not record a failed phase"
  ! grep -R -F -q -- "$raw_stdout_canary" "$evidence" || die "volume helper failure self-check retained raw stdout"
  ! grep -R -F -q -- "$raw_stderr_canary" "$evidence" || die "volume helper failure self-check retained raw stderr"
  ! grep -R -F -q -- "$json_canary" "$evidence" || die "volume helper failure self-check retained quoted JSON secret"
  ! grep -R -F -q -- "$url_canary" "$evidence" || die "volume helper failure self-check retained URL query credential"
  ! grep -R -F -q -- "$header_canary" "$evidence" || die "volume helper failure self-check retained quoted authorization header"
  ! grep -R -F -q -- "$cookie_canary" "$evidence" || die "volume helper failure self-check retained Cookie header"
  ! grep -R -F -q -- "$quoted_cookie_canary" "$evidence" || die "volume helper failure self-check retained quoted Cookie header"
  [[ -z "$(find "$temp_root" -mindepth 1 -print -quit)" ]] || die "volume helper failure self-check left raw temporary files"
}
preclaim_auth_lifecycle_assert_clean() {
  local temp_root="$1" evidence="$2" label="$3" header_canary="$4" stderr_canary="$5"
  [[ -z "$(find "$temp_root" -mindepth 1 -print -quit)" ]] || die "pre-claim ${label} left raw temporary files"
  [[ ! -e "$evidence/${label}.json.headers" && ! -e "$evidence/${label}.json.stderr" ]] || die "pre-claim ${label} retained raw response sidecars"
  ! grep -R -F -q -- "$header_canary" "$evidence" || die "pre-claim ${label} retained header canary"
  ! grep -R -F -q -- "$stderr_canary" "$evidence" || die "pre-claim ${label} retained stderr canary"
}
preclaim_auth_lifecycle_self_check() {
  local temp_root evidence header_canary='preclaim-header-canary' stderr_canary='preclaim-stderr-canary' retry_counter pid owner_pid status attempts=0
  temp_root="$(mktemp -d /tmp/seedsync-preclaim-lifecycle.XXXXXX)"
  evidence="$(mktemp -d /tmp/seedsync-preclaim-evidence.XXXXXX)"
  (
    phase() { :; }
    curl() {
      local headers=""
      while (( $# )); do case "$1" in -D) headers="$2"; shift 2 ;; *) shift ;; esac; done
      printf 'Content-Type: text/html; charset=UTF-8\r\nSet-Cookie: %s\r\n' "$header_canary" > "$headers"
      printf '%s\n' "$stderr_canary" >&2
      printf '<h1>Error: 401 Unauthorized</h1>Missing API token\n401'
    }
    SEEDSYNC_SHIP_PRECLAIM_TEMP_ROOT="$temp_root" wait_preclaim_auth_challenge mock "$evidence/success.json" preclaim-success preclaim-success mock 1 >/dev/null 2>&1
  )
  preclaim_auth_lifecycle_assert_clean "$temp_root" "$evidence" success "$header_canary" "$stderr_canary"
  retry_counter="$evidence/retry.calls"
  (
    phase() { :; }
    curl() {
      local headers="" calls=0 body
      while (( $# )); do case "$1" in -D) headers="$2"; shift 2 ;; *) shift ;; esac; done
      [[ -f "$retry_counter" ]] && calls="$(cat "$retry_counter")"
      calls=$((calls + 1)); printf '%s' "$calls" > "$retry_counter"
      printf 'Content-Type: text/html; charset=UTF-8\r\nSet-Cookie: %s\r\n' "$header_canary" > "$headers"
      printf '%s\n' "$stderr_canary" >&2
      if [[ "$calls" == 1 ]]; then body='wrong pre-claim page'; else body='<h1>Error: 401 Unauthorized</h1>Missing API token'; fi
      printf '%s\n401' "$body"
    }
    SEEDSYNC_SHIP_PRECLAIM_TEMP_ROOT="$temp_root" wait_preclaim_auth_challenge mock "$evidence/retry.json" preclaim-retry preclaim-retry mock 2 >/dev/null 2>&1
  )
  [[ "$(cat "$retry_counter")" == 2 ]] || die "pre-claim retry self-check did not retry once"
  preclaim_auth_lifecycle_assert_clean "$temp_root" "$evidence" retry "$header_canary" "$stderr_canary"
  if (
    phase() { :; }
    curl() {
      local headers=""
      while (( $# )); do case "$1" in -D) headers="$2"; shift 2 ;; *) shift ;; esac; done
      printf 'Content-Type: text/html; charset=UTF-8\r\nSet-Cookie: %s\r\n' "$header_canary" > "$headers"
      printf '%s\n' "$stderr_canary" >&2
      printf '<h1>Error: 503 Service Unavailable</h1>\n503'
    }
    SEEDSYNC_SHIP_PRECLAIM_TEMP_ROOT="$temp_root" wait_preclaim_auth_challenge mock "$evidence/timeout.json" preclaim-timeout preclaim-timeout mock 1 >/dev/null 2>&1
  ); then
    die "pre-claim timeout self-check unexpectedly passed"
  fi
  preclaim_auth_lifecycle_assert_clean "$temp_root" "$evidence" timeout "$header_canary" "$stderr_canary"
  (
    phase() { :; }
    curl() {
      local headers=""
      while (( $# )); do case "$1" in -D) headers="$2"; shift 2 ;; *) shift ;; esac; done
      printf 'Content-Type: text/html; charset=UTF-8\r\nSet-Cookie: %s\r\n' "$header_canary" > "$headers"
      printf '%s\n' "$stderr_canary" >&2
      printf 'wrong pre-claim page\n401'
    }
    SEEDSYNC_SHIP_PRECLAIM_TEMP_ROOT="$temp_root" wait_preclaim_auth_challenge mock "$evidence/interrupted.json" preclaim-interrupted preclaim-interrupted mock 60 >/dev/null 2>&1
  ) &
  pid="$!"
  until [[ -n "$(find "$temp_root" -mindepth 1 -print -quit)" ]]; do
    attempts=$((attempts + 1)); (( attempts < 40 )) || die "pre-claim interruption self-check did not create raw temporary files"
    sleep .05
  done
  owner_pid="$(cat "$temp_root"/seedsync-preclaim-auth.*/owner.pid)"
  kill -TERM "$owner_pid"
  if wait "$pid"; then die "pre-claim interruption self-check unexpectedly succeeded"; else status="$?"; fi
  [[ "$status" == 143 ]] || die "pre-claim interruption self-check exited ${status}, expected 143"
  for (( attempts = 0; attempts < 40; attempts++ )); do
    [[ -z "$(find "$temp_root" -mindepth 1 -print -quit)" ]] && break
    sleep .05
  done
  preclaim_auth_lifecycle_assert_clean "$temp_root" "$evidence" interrupted "$header_canary" "$stderr_canary"
}
capture_volume_inventory_scoping_self_check() {
  local scratch
  scratch="$(mktemp -d "${ROOT_DIR}/tmp/upgrade-v086/inventory-scope-self-check.XXXXXX")"
  (
    evidence_dir() { printf '%s' "$scratch"; }
    volume_helper() {
      [[ "$1" == "inventory-scope-self-check" ]] || return 1
      shift
      [[ "$*" == "inventory --root /config --output - --legacy-config" ]] || return 1
      printf '{"schema":1,"entries":[]}\n'
    }
    capture_volume_inventory inventory-scope-self-check before-config --legacy-config
  )
  python - "$scratch/before-config.json" <<'PY'
import json, sys
if json.load(open(sys.argv[1], encoding="utf-8")) != {"schema": 1, "entries": []}:
    raise SystemExit("inventory scoping self-check output mismatch")
PY
}
capture_volume_inventory_cwd_self_check() {
  local scratch
  scratch="$(mktemp -d "${ROOT_DIR}/tmp/upgrade-v086/inventory-cwd-self-check.XXXXXX")"
  (
    cd -- "$scratch"
    evidence_dir() { printf '%s' "$scratch"; }
    volume_helper() {
      [[ "$PWD" == "$ROOT_DIR" ]] || return 1
      [[ "$1" == "inventory-cwd-self-check" ]] || return 1
      printf '{"schema":1,"entries":[]}\n'
    }
    capture_volume_inventory inventory-cwd-self-check before-config --legacy-config
  )
  [[ -s "$scratch/before-config.json" ]] || die "inventory cwd self-check did not retain output"
}
invalid_cwd_inventory_helper_self_check() {
  local scratch="${1:?scratch directory required}"
  [[ -d "$scratch" ]] || die "invalid-cwd self-check scratch directory is unavailable"
  evidence_dir() { printf '%s' "$scratch"; }
  volume_helper() {
    [[ "$PWD" == "$ROOT_DIR" ]] || return 1
    [[ "$1" == "invalid-cwd-self-check" ]] || return 1
    printf '{"schema":1,"entries":[]}\n'
  }
  capture_volume_inventory invalid-cwd-self-check before-config --legacy-config
  [[ -s "$scratch/before-config.json" ]] || die "invalid-cwd inventory self-check did not retain output"
}
protected_storage_self_check() {
  local id="${RUN_ID:-probe-$(date -u +%Y%m%dt%H%M%S)-$$}"
  validate_id "$id"
  RUN_ID="$id" fresh_repo_shell bash "$LAB" protected-storage-self-check
}
validator_evidence_path_self_check() {
  local id="${RUN_ID:-probe-$(date -u +%Y%m%dt%H%M%S)-$$}"
  validate_id "$id"
  RUN_ID="$id" fresh_repo_shell bash "$LAB" validator-evidence-path-self-check
}
bounded_command_self_check() {
  local scratch id="wrapper-self-check" status
  scratch="$(mktemp -d "${ROOT_DIR}/tmp/upgrade-v086/wrapper-self-check.XXXXXX")"
  SEEDSYNC_SHIP_EVIDENCE_DIR="$scratch" bounded_command "$id" self-check-success success 5 "$scratch/success.log" bash -c 'exit 0'
  grep -q 'success completed' "$scratch/progress.tsv"
  if SEEDSYNC_SHIP_EVIDENCE_DIR="$scratch" bounded_command "$id" self-check-failure failure 5 "$scratch/failure.log" bash -c 'exit 7'; then
    die "bounded command failure self-check unexpectedly passed"
  else status="$?"; fi
  [[ "$status" == 7 && -s "$scratch/failure-diagnostics.txt" ]] || die "bounded command failure self-check did not retain diagnostics"
  if SEEDSYNC_SHIP_EVIDENCE_DIR="$scratch" bounded_command "$id" self-check-timeout timeout 1 "$scratch/timeout.log" bash -c 'sleep 2'; then
    die "bounded command timeout self-check unexpectedly passed"
  else status="$?"; fi
  [[ "$status" == 124 && -s "$scratch/timeout-diagnostics.txt" ]] || die "bounded command timeout self-check did not retain diagnostics"
}
port_validation_self_check() {
  local invalid
  for invalid in 0 0001 65536 99999 abc '1;touch'; do
    if bash "$0" validate-port HOST_PORT "$invalid" >/dev/null 2>&1; then
      die "invalid port unexpectedly accepted: $invalid"
    fi
  done
  bash "$0" validate-port HOST_PORT 1
  bash "$0" validate-port CURRENT_PORT 65535
}
topology_and_apply_contract_self_check() {
  python - <<'PY'
def assert_topology(containers):
    running = {name for name, item in containers.items() if item['running']}
    checks = (
        (running == {'current', 'current_proxy', 'remote', 'validator', 'snapshotter'}, 'running roles'),
        (containers['current']['networks'] == {'lab'}, 'current networks'),
        (containers['remote']['networks'] == {'lab'}, 'remote networks'),
        (containers['validator']['networks'] == set(), 'validator networks'),
        (containers['snapshotter']['networks'] == set(), 'snapshotter networks'),
        (containers['legacy']['running'] is False, 'legacy stopped'),
        (containers['legacy_proxy']['running'] is False, 'legacy proxy stopped'),
        ({name for name, item in containers.items() if item['running'] and item['networks'] == {'lab', 'browser'}} == {'current_proxy'}, 'sole bridge'),
    )
    failed = [label for passed, label in checks if not passed]
    if failed:
        raise ValueError(', '.join(failed))
valid = {
    'current': {'running': True, 'networks': {'lab'}},
    'current_proxy': {'running': True, 'networks': {'lab', 'browser'}},
    'remote': {'running': True, 'networks': {'lab'}},
    'validator': {'running': True, 'networks': set()},
    'snapshotter': {'running': True, 'networks': set()},
    'legacy': {'running': False, 'networks': {'lab'}},
    'legacy_proxy': {'running': False, 'networks': {'lab', 'browser'}},
}
assert_topology(valid)
invalid = {name: dict(item) for name, item in valid.items()}
invalid['legacy_proxy'] = {'running': True, 'networks': {'lab', 'browser'}}
try:
    assert_topology(invalid)
except ValueError:
    pass
else:
    raise AssertionError('running legacy proxy was accepted')
status = {'action': {'csrf_token': 'test'}, 'migration_id': 'legacy-to-current', 'operation': {'status': 'idle'}, 'state': 'required'}
if status['action']['csrf_token'] != 'test' or status['operation']['status'] != 'idle':
    raise SystemExit('well-formed migration status contract was rejected')
try:
    {}['action']['csrf_token']
except KeyError:
    pass
else:
    raise AssertionError('malformed migration status was accepted')
PY
}

case "${1:-}" in
  browser-claim-worker) browser_claim_reuse_worker "${2:?base URL required}" "${3:?evidence directory required}" "${4:?raw workspace required}" "${5:?raw log required}" "${6:?profile directory required}" "${7:?Node module path required}" "${8:?Node binary required}" ;;
  browser-claim-supervisor) browser_claim_reuse_supervisor "${2:?base URL required}" "${3:?evidence directory required}" "${4:?raw workspace required}" "${5:?raw log required}" "${6:?profile directory required}" "${7:?Node module path required}" "${8:?Node binary required}" ;;
  preflight) require_tools; "$LAB" preflight; python "$HELPER" self-test ;;
  worker-self-check) worker_self_check ;;
  browser-session-temp-cleanup-self-check) browser_session_temp_cleanup_self_check ;;
  browser-parent-cleanup-self-check) browser_parent_cleanup_self_check ;;
  preclaim-auth-lifecycle-self-check) preclaim_auth_lifecycle_self_check ;;
  volume-helper-output-self-check) volume_helper_output_self_check ;;
  inventory-scope-self-check) capture_volume_inventory_scoping_self_check ;;
  inventory-cwd-self-check) capture_volume_inventory_cwd_self_check ;;
  invalid-cwd-inventory-helper-self-check) invalid_cwd_inventory_helper_self_check "${2:?scratch directory required}" ;;
  protected-storage-self-check) protected_storage_self_check ;;
  validator-evidence-path-self-check) validator_evidence_path_self_check ;;
  validate-port) validate_port "${2:?port name required}" "${3:?port value required}" ;;
  full) full ;;
  *) echo "Usage: ship_readiness.sh <preflight|worker-self-check|browser-session-temp-cleanup-self-check|browser-parent-cleanup-self-check|preclaim-auth-lifecycle-self-check|volume-helper-output-self-check|inventory-scope-self-check|inventory-cwd-self-check|invalid-cwd-inventory-helper-self-check|protected-storage-self-check|validator-evidence-path-self-check|full>" >&2; exit 2 ;;
esac
