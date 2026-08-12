#!/usr/bin/env bash
set -Eeuo pipefail

# Bounded, repeatable performance lab. It has no cleanup or volume-removal
# command: fixture volumes and artifacts remain available for comparison.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR/../../../.." rev-parse --show-toplevel)"
PROJECT="${PERF_PROJECT:-seedsync-performance-lab}"
RUN_ID="${PERF_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
ARTIFACT_ROOT="${PERF_ARTIFACT_ROOT:-$REPO_ROOT/tmp/pytest/performance-lab}"
ARTIFACT_DIR="$ARTIFACT_ROOT/$RUN_ID"
PERF_IMAGE="${PERF_IMAGE:-}"
PERF_API_TOKEN="${PERF_API_TOKEN:-seedsync-performance-local-token}"
PERF_BREADCRUMB_MODE="${PERF_BREADCRUMB_MODE:-on}"
PERF_MOVE_FAILURE_MODE="${PERF_MOVE_FAILURE_MODE:-stale}"
PERF_REMOTE_ADDRESS="${PERF_REMOTE_ADDRESS:-remote}"
PERF_PAIRS="${PERF_PAIRS:-6}"
PERF_NODES_PER_PAIR="${PERF_NODES_PER_PAIR:-32000}"
PERF_HOST_PORT="${PERF_HOST_PORT:-18800}"
PERF_MEASURE_TIMEOUT_SECONDS="${PERF_MEASURE_TIMEOUT_SECONDS:-900}"
PERF_SETTLED_SAMPLES="${PERF_SETTLED_SAMPLES:-6}"
PERF_SAMPLE_SLEEP_SECONDS="${PERF_SAMPLE_SLEEP_SECONDS:-2}"
# The production-shaped remote scan repeats every 120 seconds.  A shorter
# post-target window can incorrectly certify the quiet gap before that refresh
# restarts, so the default acceptance window spans one complete refresh edge.
PERF_POST_TARGET_OBSERVATION_SECONDS="${PERF_POST_TARGET_OBSERVATION_SECONDS:-150}"

if [[ -z "$PERF_IMAGE" ]]; then
  echo "PERF_IMAGE must name the exact SeedSync image or candidate" >&2
  exit 2
fi
if [[ "$PERF_BREADCRUMB_MODE" != on && "$PERF_BREADCRUMB_MODE" != off ]]; then
  echo "PERF_BREADCRUMB_MODE must be on or off" >&2
  exit 2
fi
if [[ "$PERF_MOVE_FAILURE_MODE" != stale && "$PERF_MOVE_FAILURE_MODE" != none ]]; then
  echo "PERF_MOVE_FAILURE_MODE must be stale or none" >&2
  exit 2
fi

export PERF_IMAGE PERF_API_TOKEN PERF_BREADCRUMB_MODE PERF_MOVE_FAILURE_MODE PERF_REMOTE_ADDRESS PERF_PAIRS PERF_NODES_PER_PAIR PERF_HOST_PORT
export PERF_POST_TARGET_OBSERVATION_SECONDS
export PERF_LAB_SOURCE_DIR="$SCRIPT_DIR"
export PERF_ARTIFACT_DIR="$ARTIFACT_DIR"
export PERF_PROJECT="$PROJECT"
export PERF_RUN_ID="$RUN_ID"

compose() {
  local compose_files=(--file "$SCRIPT_DIR/compose.yml")
  if [[ -n "${PERF_EXTERNAL_NETWORK:-}" ]]; then
    export PERF_EXTERNAL_NETWORK
    compose_files+=(--file "$SCRIPT_DIR/compose.external-network.yml")
  fi
  docker compose --project-name "$PROJECT" "${compose_files[@]}" "$@"
}

ensure_artifacts() {
  mkdir -p "$ARTIFACT_DIR"
}

write_run_metadata() {
  local phase="$1"
  python3 - "$ARTIFACT_DIR/run-manifest.json" "$phase" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema": "seedsync.performance-lab.run.v1",
    "phase": sys.argv[2],
    "run_id": os.environ["PERF_RUN_ID"],
    "project": os.environ["PERF_PROJECT"],
    "image": os.environ["PERF_IMAGE"],
    "breadcrumb_mode": os.environ["PERF_BREADCRUMB_MODE"],
    "move_failure_mode": os.environ["PERF_MOVE_FAILURE_MODE"],
    "remote_address": os.environ["PERF_REMOTE_ADDRESS"],
    "pairs": int(os.environ["PERF_PAIRS"]),
    "nodes_per_pair": int(os.environ["PERF_NODES_PER_PAIR"]),
    "host_port": int(os.environ["PERF_HOST_PORT"]),
    "post_target_observation_seconds": int(os.environ["PERF_POST_TARGET_OBSERVATION_SECONDS"]),
    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

prepare() {
  ensure_artifacts
  write_run_metadata prepare
  compose run --rm fixture
  compose run --rm config_seed
  compose build remote
  cp "$ARTIFACT_DIR/fixture-manifest.json" "$ARTIFACT_DIR/fixture-manifest.prepare.json"
  echo "Prepared retained synthetic fixture and config in $ARTIFACT_DIR"
}

start() {
  ensure_artifacts
  write_run_metadata start
  date -u +%s%3N > "$ARTIFACT_DIR/start-epoch-ms.txt"
  compose up --detach remote app
  compose ps > "$ARTIFACT_DIR/compose-ps-start.txt"
  echo "Started project $PROJECT; artifacts: $ARTIFACT_DIR"
}

status() {
  ensure_artifacts
  compose ps | tee "$ARTIFACT_DIR/compose-ps-status.txt"
  local diagnostics_url="http://127.0.0.1:$PERF_HOST_PORT/server/admin/performance-diagnostics/v1"
  if curl --silent --show-error --fail --max-time 10 \
      -H "Authorization: Bearer $PERF_API_TOKEN" "$diagnostics_url" \
      > "$ARTIFACT_DIR/diagnostics-status.json"; then
    curl --silent --show-error --fail --max-time 10 \
      -H "Authorization: Bearer $PERF_API_TOKEN" \
      "http://127.0.0.1:$PERF_HOST_PORT/server/breadcrumbs/get" \
      > "$ARTIFACT_DIR/breadcrumbs-status.json" || true
    echo "Diagnostics captured in $ARTIFACT_DIR"
  else
    echo "App is not serving diagnostics at $diagnostics_url" >&2
    return 1
  fi
}

write_container_summary() {
  local container_id="$1"
  local output_path="$2"
  local role="$3"
  local fields
  # Select fixed fields before they cross into a retained artifact.  The raw
  # inspect object contains environment, mounts, commands, labels, and IDs.
  fields="$(docker inspect --format '{{.State.Status}}\t{{.State.Running}}\t{{.State.StartedAt}}\t{{.State.FinishedAt}}\t{{.RestartCount}}\t{{.Config.Image}}\t{{.Platform}}\t{{.SizeRw}}\t{{.SizeRootFs}}\t{{.HostConfig.NanoCpus}}\t{{.HostConfig.Memory}}\t{{.HostConfig.PidsLimit}}\t{{len .Mounts}}' "$container_id" 2>/dev/null || true)"
  [[ -n "$fields" ]] || return 0
  python3 "$SCRIPT_DIR/sanitize_docker_state.py" container "$output_path" "$role" "$fields"
}

write_image_summary() {
  local container_id="$1"
  local output_path="$2"
  local role="$3"
  local image_id fields
  image_id="$(docker inspect --format '{{.Image}}' "$container_id" 2>/dev/null || true)"
  [[ -n "$image_id" ]] || return 0
  fields="$(docker image inspect --format '{{.Architecture}}\t{{.Os}}\t{{.Created}}\t{{.Size}}\t{{len .RootFS.Layers}}' "$image_id" 2>/dev/null || true)"
  [[ -n "$fields" ]] || return 0
  python3 "$SCRIPT_DIR/sanitize_docker_state.py" image "$output_path" "$role" "$fields"
}

write_stats_summary() {
  local container_id="$1"
  local output_path="$2"
  local role="$3"
  local fields
  fields="$(docker stats --no-stream --format '{{.CPUPerc}}\t{{.MemPerc}}\t{{.PIDs}}' "$container_id" 2>/dev/null || true)"
  [[ -n "$fields" ]] || return 0
  python3 "$SCRIPT_DIR/sanitize_docker_state.py" stats "$output_path" "$role" "$fields"
}

write_process_summary() {
  local container_id="$1"
  local output_path="$2"
  local role="$3"
  local process_count
  # Count processes without retaining docker top's command lines or PIDs.
  process_count="$(docker top "$container_id" 2>/dev/null | awk 'NR > 1 { count++ } END { print count + 0 }' || true)"
  [[ -n "$process_count" ]] || return 0
  python3 "$SCRIPT_DIR/sanitize_docker_state.py" processes "$output_path" "$role" "$process_count"
}

capture_container_state() {
  local output_dir="$1"
  local app_id remote_id
  mkdir -p "$output_dir"
  app_id="$(compose ps -q app)"
  remote_id="$(compose ps -q remote)"
  if [[ -n "$app_id" ]]; then
    write_container_summary "$app_id" "$output_dir/app-inspect.json" app
    write_stats_summary "$app_id" "$output_dir/app-docker-stats.json" app
    write_process_summary "$app_id" "$output_dir/app-processes.json" app
    write_image_summary "$app_id" "$output_dir/app-image-inspect.json" app
  fi
  if [[ -n "$remote_id" ]]; then
    write_container_summary "$remote_id" "$output_dir/remote-inspect.json" remote
    write_stats_summary "$remote_id" "$output_dir/remote-docker-stats.json" remote
    write_process_summary "$remote_id" "$output_dir/remote-processes.json" remote
  fi
}

capture_ownership_census() (
  local output_path="$1"
  local ownership_url="$2"
  local raw_path
  mkdir -p "$(dirname "$output_path")"
  raw_path="$(mktemp "${output_path}.raw.XXXXXX")"
  trap 'rm -f -- "$raw_path"' EXIT

  # Never retain the raw response. Validate a closed numeric shape and write
  # only fixed ownership fields to the retained artifact.
  if curl --silent --fail --max-time 120 \
      -H "Authorization: Bearer $PERF_API_TOKEN" "$ownership_url" > "$raw_path"; then
    if ! python3 - "$raw_path" "$output_path" <<'PY'
import json
import sys
from pathlib import Path

MAX_INT = (1 << 63) - 1
FIXED_OWNERS = frozenset({
    "live_model_graph", "builder_cached_model_graph",
    "builder_local_system_file_graph", "builder_active_system_file_graph",
    "builder_remote_system_file_graph", "builder_active_file_ids",
    "builder_lftp_statuses", "builder_extract_statuses", "builder_validation_statuses",
    "builder_recent_transfer_snapshots", "builder_retained_transfer_snapshots",
    "controller_active_downloads", "controller_active_extracts",
    "controller_pending_completion", "controller_pending_extract",
    "controller_pending_validation", "controller_move_retries", "controller_deferred_moves",
    "controller_malformed_status_only", "controller_pending_auto_purge",
})


def bounded_int(value):
    return value if type(value) is int and 0 <= value <= MAX_INT else None


raw_path, output_path = sys.argv[1:]
payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
if type(payload) is not dict or payload.get("schema") != "seedsync.memory-ownership-census.v1":
    raise ValueError("invalid ownership schema")
enabled = payload.get("enabled")
if type(enabled) is not bool:
    raise ValueError("invalid ownership enabled state")
available = payload.get("available", enabled)
if type(available) is not bool:
    raise ValueError("invalid ownership availability")
truncated = payload.get("truncated")
visited = bounded_int(payload.get("visited_object_count"))
total_bytes = bounded_int(payload.get("total_shallow_bytes"))
graph_truncated = payload.get("graph_truncated")
graph_visited = bounded_int(payload.get("graph_visited_node_count"))
owners = payload.get("owners")
if type(truncated) is not bool or visited is None or total_bytes is None or type(graph_truncated) is not bool \
        or graph_visited is None or type(owners) is not dict:
    raise ValueError("invalid ownership totals")
if len(owners) > len(FIXED_OWNERS) or any(name not in FIXED_OWNERS for name in owners):
    raise ValueError("invalid ownership labels")

sanitized_owners = {}
for name, owner in owners.items():
    if type(owner) is not dict or set(owner) - {
        "object_count", "shallow_bytes", "graph_node_count", "graph_shallow_bytes", "graph_truncated",
        "aliases_live_model",
    }:
        raise ValueError("invalid ownership record")
    object_count = bounded_int(owner.get("object_count"))
    shallow_bytes = bounded_int(owner.get("shallow_bytes"))
    graph_node_count = bounded_int(owner.get("graph_node_count"))
    graph_shallow_bytes = bounded_int(owner.get("graph_shallow_bytes"))
    owner_graph_truncated = owner.get("graph_truncated")
    if object_count is None or shallow_bytes is None or graph_node_count is None or graph_shallow_bytes is None \
            or type(owner_graph_truncated) is not bool:
        raise ValueError("invalid ownership values")
    item = {
        "object_count": object_count,
        "shallow_bytes": shallow_bytes,
        "graph_node_count": graph_node_count,
        "graph_shallow_bytes": graph_shallow_bytes,
        "graph_truncated": owner_graph_truncated,
    }
    if "aliases_live_model" in owner:
        aliases_live_model = owner["aliases_live_model"]
        if type(aliases_live_model) is not bool:
            raise ValueError("invalid ownership alias")
        item["aliases_live_model"] = aliases_live_model
    sanitized_owners[name] = item

status = "disabled" if not enabled else ("ok" if available else "unavailable")
sanitized = {
    "schema": "seedsync.memory-ownership-census.v1",
    "capture_status": status,
    "enabled": enabled,
    "available": available,
    "truncated": truncated,
    "visited_object_count": visited,
    "total_shallow_bytes": total_bytes,
    "graph_truncated": graph_truncated,
    "graph_visited_node_count": graph_visited,
    "owners": sanitized_owners,
}
if enabled and not available:
    failure_stage = payload.get("failure_stage")
    failure_kind = payload.get("failure_kind")
    if failure_stage not in {"capture_roots", "build_census"}:
        raise ValueError("invalid ownership failure stage")
    if failure_kind not in {"memory_error", "recursion_error", "runtime_error", "unexpected_error"}:
        raise ValueError("invalid ownership failure kind")
    sanitized["failure_stage"] = failure_stage
    sanitized["failure_kind"] = failure_kind
Path(output_path).write_text(json.dumps(sanitized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
    then
      printf '%s\n' '{"schema":"seedsync.memory-ownership-census.v1","capture_status":"unavailable","enabled":false,"available":false,"truncated":false,"visited_object_count":0,"total_shallow_bytes":0,"graph_truncated":false,"graph_visited_node_count":0,"owners":{}}' > "$output_path"
    fi
  else
    printf '%s\n' '{"schema":"seedsync.memory-ownership-census.v1","capture_status":"unavailable","enabled":false,"available":false,"truncated":false,"visited_object_count":0,"total_shallow_bytes":0,"graph_truncated":false,"graph_visited_node_count":0,"owners":{}}' > "$output_path"
  fi
)

measure() {
  local label="${1:-}"
  if [[ "$label" != baseline && "$label" != candidate ]]; then
    echo "usage: $0 measure baseline|candidate" >&2
    exit 2
  fi
  ensure_artifacts
  write_run_metadata "measure-$label"
  local phase_dir="$ARTIFACT_DIR/$label"
  mkdir -p "$phase_dir"
  # Own the measured startup: restart the app immediately before resetting
  # diagnostics so the retained window belongs to this run, not a prior
  # status/start command.
  compose stop app >/dev/null 2>&1 || true
  date -u +%s%3N > "$ARTIFACT_DIR/start-epoch-ms.txt"
  compose up --detach remote app
  capture_container_state "$phase_dir"
  local base_url="http://127.0.0.1:$PERF_HOST_PORT"
  local diagnostics_url="$base_url/server/admin/performance-diagnostics/v1"
  python3 - "$phase_dir/window-note.json" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "schema": "seedsync.performance-lab.window.v1",
        "collector_window": "new-process",
        "reset_after_start": False,
        "reason": "A restarted app owns a fresh in-memory diagnostics and breadcrumb window; resetting after readiness would erase the cold scan.",
    }, indent=2) + "\n")
PY
  local started_ms
  started_ms="$(cat "$ARTIFACT_DIR/start-epoch-ms.txt" 2>/dev/null || date -u +%s%3N)"
  local first_ms="" target_ms="" settled_ms="" target_index=""
  local target_sequence=""
  local expected_count
  expected_count="$(python3 - "$ARTIFACT_DIR/fixture-manifest.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    topology = json.load(handle)["topology"]
print(topology["expected_merged_model_tree_nodes"])
PY
)"
  local deadline=$(( $(date +%s) + PERF_MEASURE_TIMEOUT_SECONDS ))
  local index=0
  local last_success_index=0
  while (( $(date +%s) < deadline )); do
    index=$((index + 1))
    local diagnostics_path="$phase_dir/diagnostics-$(printf '%04d' "$index").json"
    local breadcrumbs_path="$phase_dir/breadcrumbs-$(printf '%04d' "$index").json"
    if ! curl --silent --show-error --fail --max-time 20 \
      -H "Authorization: Bearer $PERF_API_TOKEN" "$diagnostics_url" > "$diagnostics_path"; then
      local app_id app_running
      app_id="$(compose ps --all -q app)"
      app_running=""
      if [[ -n "$app_id" ]]; then
        app_running="$(docker inspect --format '{{.State.Running}}' "$app_id" 2>/dev/null || true)"
      fi
      if [[ "$app_running" != true ]]; then
        if [[ -n "$app_id" ]]; then
          # Runtime logs can contain paths, remote details, and command text.
          # Retain only the same fixed container-state allowlist used by the
          # normal evidence path.
          write_container_summary "$app_id" "$phase_dir/app-exited-state.json" app
        fi
        echo "App exited before diagnostics became ready; see $phase_dir/app-exited-state.json" >&2
        return 1
      fi
      sleep "$PERF_SAMPLE_SLEEP_SECONDS"
      continue
    fi
    last_success_index="$index"
    curl --silent --show-error --fail --max-time 20 \
      -H "Authorization: Bearer $PERF_API_TOKEN" "$base_url/server/breadcrumbs/get" \
      > "$breadcrumbs_path" || true
    [[ -n "$first_ms" ]] || first_ms="$(date -u +%s%3N)"
    local observed_count observed_sequence observed_build_count
    read -r observed_count observed_sequence observed_build_count <<<"$(python3 - "$diagnostics_path" <<'PY'
import json
import os
from pathlib import Path

import sys
sys.path.insert(0, os.environ["PERF_LAB_SOURCE_DIR"])
from capture_metrics import latest_model_status

model_count, sequence, build_count = latest_model_status(
    json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
)
print(
    (str(model_count) if model_count is not None else "") + " " +
    (str(sequence) if sequence is not None else "") + " " +
    (str(build_count) if build_count is not None else "")
)
PY
)"
    # Cardinality alone is not a settled boundary: a long startup build can
    # publish the full tree while later catch-up builds are still queued. Start
    # recurring-refresh acceptance only after one complete zero-build window.
    if [[ -z "$target_ms" && -n "$observed_count" && -n "$observed_sequence" && \
          "$observed_build_count" == 0 ]] && \
       (( observed_count >= expected_count * 99 / 100 && observed_count <= expected_count * 101 / 100 )); then
      target_ms="$(date -u +%s%3N)"
      target_index="$index"
      target_sequence="$observed_sequence"
      cp "$diagnostics_path" "$phase_dir/diagnostics-at-target.json"
      python3 - "$phase_dir/cold-start.json" "$started_ms" "$first_ms" "$target_ms" "$expected_count" "$target_sequence" <<'PY'
import json, sys
started, first, target, expected, sequence = map(int, sys.argv[2:])
payload = {
    "schema": "seedsync.performance-lab.cold-start.v1",
    "started_epoch_ms": started,
    "first_diagnostics_epoch_ms": first,
    "model_target_epoch_ms": target,
    "startup_to_first_diagnostics_ms": first - started,
    "full_scan_to_model_target_ms": target - first,
    "expected_model_tree_file_count": expected,
    "target_sample_sequence": sequence,
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, indent=2) + "\n")
PY
    elif [[ -n "$target_ms" && -z "$settled_ms" && -n "$target_sequence" && -n "$observed_sequence" ]]; then
      local observation_now_ms
      observation_now_ms="$(date -u +%s%3N)"
      if (( observed_sequence < target_sequence + PERF_SETTLED_SAMPLES )) || \
         (( observation_now_ms < target_ms + PERF_POST_TARGET_OBSERVATION_SECONDS * 1000 )); then
        sleep "$PERF_SAMPLE_SLEEP_SECONDS"
        continue
      fi
      settled_ms="$observation_now_ms"
      python3 - "$phase_dir/phase-timing.json" "$first_ms" "$target_ms" "$settled_ms" <<'PY'
import json, sys
first, target, settled = map(int, sys.argv[2:])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "schema": "seedsync.performance-lab.phase-timing.v1",
        "cold_start_to_first_diagnostics_ms": 0,
        "full_scan_ms": target - first,
        "post_scan_settled_idle_observation_ms": settled - target,
    }, indent=2) + "\n")
PY
      break
    fi
    sleep "$PERF_SAMPLE_SLEEP_SECONDS"
  done
  if (( last_success_index == 0 )); then
    echo "Timed out waiting for diagnostics readiness" >&2
    return 1
  fi
  local latest="$phase_dir/diagnostics-$(printf '%04d' "$last_success_index").json"
  cp "$latest" "$phase_dir/diagnostics.json"
  local latest_breadcrumbs="$phase_dir/breadcrumbs-$(printf '%04d' "$last_success_index").json"
  if [[ -f "$latest_breadcrumbs" ]]; then
    cp "$latest_breadcrumbs" "$phase_dir/breadcrumbs.json"
  else
    printf '{}\n' > "$phase_dir/breadcrumbs.json"
  fi
  local target_argument=()
  if [[ -n "$target_sequence" ]]; then
    target_argument=(--target-sequence "$target_sequence")
  fi
  local metrics_status=0
  python3 "$SCRIPT_DIR/capture_metrics.py" \
    --diagnostics "$phase_dir/diagnostics.json" \
    --manifest "$ARTIFACT_DIR/fixture-manifest.json" \
    --output "$phase_dir/metrics-summary.json" \
    --label "$label" \
    --breadcrumbs "$phase_dir/breadcrumbs.json" \
    --breadcrumb-mode "$PERF_BREADCRUMB_MODE" \
    "${target_argument[@]}" || metrics_status=$?
  capture_container_state "$phase_dir/final"
  capture_ownership_census \
    "$phase_dir/ownership-final.json" \
    "$base_url/server/admin/performance-diagnostics/v1/ownership"
  if [[ "$label" == baseline ]]; then
    printf '%s\n' "$phase_dir" > "$ARTIFACT_ROOT/baseline-latest.txt"
  fi
  local baseline_phase_dir=""
  if [[ -f "$ARTIFACT_ROOT/baseline-latest.txt" ]]; then
    baseline_phase_dir="$(cat "$ARTIFACT_ROOT/baseline-latest.txt")"
  fi
  if [[ "$label" == candidate && -n "$baseline_phase_dir" && -f "$baseline_phase_dir/metrics-summary.json" ]]; then
    python3 - "$baseline_phase_dir/metrics-summary.json" "$phase_dir/metrics-summary.json" "$phase_dir/comparison.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    baseline = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    candidate = json.load(handle)
comparison = {
    "schema": "seedsync.performance-lab.comparison.v1",
    "same_fixture_fingerprint": baseline.get("fixture_fingerprint") == candidate.get("fixture_fingerprint"),
    "baseline_breadcrumb": baseline.get("breadcrumb"),
    "candidate_breadcrumb": candidate.get("breadcrumb"),
    "baseline_full_scan": baseline.get("full_scan_phase"),
    "candidate_full_scan": candidate.get("full_scan_phase"),
    "baseline_settled_idle": baseline.get("settled_idle_phase"),
    "candidate_settled_idle": candidate.get("settled_idle_phase"),
    "baseline_post_target": baseline.get("post_target_phase"),
    "candidate_post_target": candidate.get("post_target_phase"),
    "baseline_rebuild_reasons": baseline.get("model_rebuild_reason_counters"),
    "candidate_rebuild_reasons": candidate.get("model_rebuild_reason_counters"),
    "baseline_model_builder_invalidations": baseline.get("model_builder_invalidation_counters"),
    "candidate_model_builder_invalidations": candidate.get("model_builder_invalidation_counters"),
}
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    handle.write(json.dumps(comparison, indent=2) + "\n")
if not comparison["same_fixture_fingerprint"]:
    raise SystemExit("baseline and candidate fixture fingerprints differ")
PY
  fi
  echo "Measured $label; artifacts: $phase_dir"
  if (( metrics_status != 0 )); then
    return "$metrics_status"
  fi
}

stop() {
  ensure_artifacts
  write_run_metadata stop
  compose stop app remote
  compose ps > "$ARTIFACT_DIR/compose-ps-stop.txt"
  echo "Stopped app and remote containers; retained volumes and artifacts"
}

case "${1:-}" in
  prepare) prepare ;;
  start) start ;;
  status) status ;;
  measure) shift; measure "$@" ;;
  stop) stop ;;
  *) echo "usage: $0 prepare|start|status|measure baseline|candidate|stop" >&2; exit 2 ;;
esac
