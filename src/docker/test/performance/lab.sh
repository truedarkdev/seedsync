#!/usr/bin/env bash
set -Eeuo pipefail

# Bounded, repeatable performance lab. It has no cleanup or volume-removal
# command: fixture volumes and artifacts remain available for comparison.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR/../../../.." rev-parse --show-toplevel)"
PROJECT_WAS_EXPLICIT="${PERF_PROJECT:-}"
PROJECT="${PERF_PROJECT:-seedsync-performance-lab}"
RUN_ID="${PERF_RUN_ID:-$(date -u +%Y%m%dt%H%M%Sz)}"
if [[ ! "$RUN_ID" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]]; then
  echo "PERF_RUN_ID must be a lowercase safe slug (1-64 characters; alphanumeric, underscore, hyphen)" >&2
  exit 2
fi
ARTIFACT_ROOT="${PERF_ARTIFACT_ROOT:-$REPO_ROOT/tmp/pytest/performance-lab}"
ARTIFACT_DIR="$ARTIFACT_ROOT/$RUN_ID"
PERF_IMAGE="${PERF_IMAGE:-}"
PERF_API_TOKEN="${PERF_API_TOKEN:-seedsync-performance-local-token}"
PERF_BREADCRUMB_MODE="${PERF_BREADCRUMB_MODE:-on}"
PERF_DIAGNOSTICS_MODE="${PERF_DIAGNOSTICS_MODE:-on}"
PERF_BROWSER_DESTRUCTIVE_APPROVED="${PERF_BROWSER_DESTRUCTIVE_APPROVED:-off}"
PERF_MOVE_FAILURE_MODE="${PERF_MOVE_FAILURE_MODE:-stale}"
PERF_REMOTE_ADDRESS="${PERF_REMOTE_ADDRESS:-remote}"
PERF_PAIRS="${PERF_PAIRS:-6}"
PERF_NODES_PER_PAIR="${PERF_NODES_PER_PAIR:-32000}"
PERF_PROFILE="${PERF_PROFILE:-uniform}"
PERF_HIGH_CARD_ENABLED="${PERF_HIGH_CARD_ENABLED:-on}"
if [[ "$PERF_PROFILE" == cadence ]]; then
  # Cadence is intentionally a small asymmetric isolation fixture; keep a
  # fixed floor below its expected merged tree without weakening uniform/mixed.
  PERF_MINIMUM_MERGED_NODES=2000
else
  PERF_MINIMUM_MERGED_NODES=200000
fi
PERF_HOST_PORT="${PERF_HOST_PORT:-18800}"
PERF_NODE_BINARY="${PERF_NODE_BINARY:-node}"
PERF_PLAYWRIGHT_MODULE="${PERF_PLAYWRIGHT_MODULE:-${SEEDSYNC_PLAYWRIGHT_MODULE:-playwright}}"
PERF_NODE_PATH="${PERF_NODE_PATH:-${SEEDSYNC_PLAYWRIGHT_NODE_PATH:-}}"
PERF_MEASURE_TIMEOUT_SECONDS="${PERF_MEASURE_TIMEOUT_SECONDS:-900}"
PERF_SETTLED_SAMPLES="${PERF_SETTLED_SAMPLES:-6}"
PERF_SAMPLE_SLEEP_SECONDS="${PERF_SAMPLE_SLEEP_SECONDS:-2}"
PERF_POST_TARGET_POLL_SECONDS="${PERF_POST_TARGET_POLL_SECONDS:-10}"
# The production-shaped remote scan repeats every 120 seconds.  A shorter
# post-target window can incorrectly certify the quiet gap before that refresh
# restarts, so the default acceptance window spans one complete refresh edge.
PERF_POST_TARGET_OBSERVATION_SECONDS="${PERF_POST_TARGET_OBSERVATION_SECONDS:-150}"
PERF_CGROUP_CAPTURE_LAG_MAX_SECONDS=5
PERF_SETTLED_IDLE_CPU_PERCENT="${PERF_SETTLED_IDLE_CPU_PERCENT:-1.0}"
# Diagnostics-off readiness is intentionally stricter than the final average:
# the app must report three consecutive low-CPU observations before the full
# post-target window starts. This count is fixed so the <=1% boundary cannot
# be weakened by a worker override.
PERF_EXTERNAL_READINESS_SAMPLES=3
PERF_EXTERNAL_MODEL_STABILITY_SAMPLES=2

if [[ -z "$PERF_IMAGE" ]]; then
  echo "PERF_IMAGE must name the exact SeedSync image or candidate" >&2
  exit 2
fi
if [[ "$PERF_BREADCRUMB_MODE" != on && "$PERF_BREADCRUMB_MODE" != off ]]; then
  echo "PERF_BREADCRUMB_MODE must be on or off" >&2
  exit 2
fi
if [[ "$PERF_DIAGNOSTICS_MODE" != on && "$PERF_DIAGNOSTICS_MODE" != off ]]; then
  echo "PERF_DIAGNOSTICS_MODE must be on or off" >&2
  exit 2
fi
if [[ "$PERF_BROWSER_DESTRUCTIVE_APPROVED" != on && "$PERF_BROWSER_DESTRUCTIVE_APPROVED" != off ]]; then
  echo "PERF_BROWSER_DESTRUCTIVE_APPROVED must be on or off" >&2
  exit 2
fi
if [[ "$PERF_MOVE_FAILURE_MODE" != stale && "$PERF_MOVE_FAILURE_MODE" != none ]]; then
  echo "PERF_MOVE_FAILURE_MODE must be stale or none" >&2
  exit 2
fi
if [[ "$PERF_PROFILE" != uniform && "$PERF_PROFILE" != mixed && "$PERF_PROFILE" != cadence ]]; then
  echo "PERF_PROFILE must be uniform, mixed, or cadence" >&2
  exit 2
fi
if [[ "$PERF_HIGH_CARD_ENABLED" != on && "$PERF_HIGH_CARD_ENABLED" != off ]]; then
  echo "PERF_HIGH_CARD_ENABLED must be on or off" >&2
  exit 2
fi
if ! [[ "$PERF_SETTLED_IDLE_CPU_PERCENT" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
   (( $(awk "BEGIN { print ($PERF_SETTLED_IDLE_CPU_PERCENT <= 0 || $PERF_SETTLED_IDLE_CPU_PERCENT > 1.0) }") )); then
  echo "PERF_SETTLED_IDLE_CPU_PERCENT must be greater than 0 and no more than 1.0 for acceptance" >&2
  exit 2
fi

export PERF_IMAGE PERF_API_TOKEN PERF_BREADCRUMB_MODE PERF_DIAGNOSTICS_MODE PERF_BROWSER_DESTRUCTIVE_APPROVED PERF_MOVE_FAILURE_MODE PERF_REMOTE_ADDRESS PERF_PAIRS PERF_NODES_PER_PAIR PERF_HOST_PORT
export PERF_PROFILE PERF_HIGH_CARD_ENABLED
export PERF_MINIMUM_MERGED_NODES
export PERF_POST_TARGET_OBSERVATION_SECONDS
export PERF_SETTLED_IDLE_CPU_PERCENT
export PERF_NODE_BINARY PERF_PLAYWRIGHT_MODULE PERF_NODE_PATH
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
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
import sys as _sys
_sys.path.insert(0, os.environ["PERF_LAB_SOURCE_DIR"])
from generate_fixture import normalize_topology_spec
requested_pairs = int(os.environ["PERF_PAIRS"])
requested_nodes = int(os.environ["PERF_NODES_PER_PAIR"])
topology_spec = normalize_topology_spec(
    os.environ["PERF_PROFILE"], requested_pairs, requested_nodes,
    os.environ["PERF_HIGH_CARD_ENABLED"] == "on",
)
from seed_config import rate_limit_for_profile
rate_limit = rate_limit_for_profile(os.environ["PERF_PROFILE"])
effective_high_card_enabled = next(
    (pair.get("enabled") for pair in topology_spec["pairs"]
     if pair.get("role") == "high-cardinality-idle"), True
)
payload = {
    "schema": "seedsync.performance-lab.run.v1",
    "phase": sys.argv[2],
    "run_id_digest": hashlib.sha256(os.environ["PERF_RUN_ID"].encode("utf-8")).hexdigest(),
    "project_digest": hashlib.sha256(os.environ["PERF_PROJECT"].encode("utf-8")).hexdigest(),
    "image_tag_digest": hashlib.sha256(os.environ["PERF_IMAGE"].encode("utf-8")).hexdigest(),
    "breadcrumb_mode": os.environ["PERF_BREADCRUMB_MODE"],
    "diagnostics_mode": os.environ["PERF_DIAGNOSTICS_MODE"],
    "move_failure_mode": os.environ["PERF_MOVE_FAILURE_MODE"],
    "remote_address_digest": hashlib.sha256(os.environ["PERF_REMOTE_ADDRESS"].encode("utf-8")).hexdigest(),
    "pairs": len(topology_spec["pairs"]),
    "nodes_per_pair": requested_nodes if os.environ["PERF_PROFILE"] == "uniform" else None,
    "requested_pairs": requested_pairs,
    "requested_nodes_per_pair": requested_nodes,
    "profile": os.environ["PERF_PROFILE"],
    "high_card_enabled": effective_high_card_enabled,
    "topology_spec": topology_spec,
    "pair_node_counts": {
        pair["id"]: {
            "local": pair["nodes_local"], "remote": pair["nodes_remote"],
            "enabled": pair["enabled"], "role": pair["role"],
        }
        for pair in topology_spec["pairs"]
    },
    "host_port": int(os.environ["PERF_HOST_PORT"]),
    "rate_limit_bytes_per_second": rate_limit,
    "post_target_observation_seconds": int(os.environ["PERF_POST_TARGET_OBSERVATION_SECONDS"]),
    "settled_idle_cpu_threshold_percent": float(os.environ["PERF_SETTLED_IDLE_CPU_PERCENT"]),
    "minimum_expected_merged_model_tree_nodes": int(os.environ["PERF_MINIMUM_MERGED_NODES"]),
    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
}

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

write_compose_summary() {
  local output_path="$1"
  local app_id remote_id app_fields remote_fields
  app_id="$(compose ps --all -q app 2>/dev/null || true)"
  remote_id="$(compose ps --all -q remote 2>/dev/null || true)"
  app_fields=""
  remote_fields=""
  if [[ -n "$app_id" ]]; then
    app_fields="$(docker inspect --format '{{.State.Status}}{{"\t"}}{{.State.Running}}{{"\t"}}{{.State.StartedAt}}{{"\t"}}{{.State.FinishedAt}}{{"\t"}}{{.RestartCount}}{{"\t"}}{{.Config.Image}}{{"\t"}}{{.Platform}}' "$app_id" 2>/dev/null || true)"
  fi
  if [[ -n "$remote_id" ]]; then
    remote_fields="$(docker inspect --format '{{.State.Status}}{{"\t"}}{{.State.Running}}{{"\t"}}{{.State.StartedAt}}{{"\t"}}{{.State.FinishedAt}}{{"\t"}}{{.RestartCount}}{{"\t"}}{{.Config.Image}}{{"\t"}}{{.Platform}}' "$remote_id" 2>/dev/null || true)"
  fi
  python3 - "$output_path" "$PROJECT" "$app_fields" "$remote_fields" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

def record(role, fields):
    values = fields.split("\t") if fields else []
    values += [""] * (7 - len(values))
    status, running, started, finished, restarts, image, platform = values[:7]
    return {
        "role": role,
        "status": status or None,
        "running": True if running == "true" else False if running == "false" else None,
        "started_at": started or None,
        "finished_at": finished or None,
        "restart_count": int(restarts) if restarts.isdigit() else None,
        "image_tag_digest": hashlib.sha256(image.encode("utf-8")).hexdigest() if image else None,
        "platform": platform or None,
    }

payload = {
    "schema": "seedsync.performance-lab.compose-state.v1",
    "project_digest": hashlib.sha256(sys.argv[2].encode("utf-8")).hexdigest(),
    "services": [record("app", sys.argv[3]), record("remote-helper", sys.argv[4])],
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
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
  write_compose_summary "$ARTIFACT_DIR/compose-state-start.json"
  echo "Started project $PROJECT; artifacts: $ARTIFACT_DIR"
}

status() {
  ensure_artifacts
  write_compose_summary "$ARTIFACT_DIR/compose-state-status.json"
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
  fields="$(docker inspect --format '{{.State.Status}}{{"\t"}}{{.State.Running}}{{"\t"}}{{.State.StartedAt}}{{"\t"}}{{.State.FinishedAt}}{{"\t"}}{{.RestartCount}}{{"\t"}}{{.Config.Image}}{{"\t"}}{{.Platform}}{{"\t"}}{{.SizeRw}}{{"\t"}}{{.SizeRootFs}}{{"\t"}}{{.HostConfig.NanoCpus}}{{"\t"}}{{.HostConfig.Memory}}{{"\t"}}{{.HostConfig.PidsLimit}}{{"\t"}}{{len .Mounts}}' "$container_id" 2>/dev/null || true)"
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
  fields="$(docker image inspect --format '{{.Architecture}}{{"\t"}}{{.Os}}{{"\t"}}{{.Created}}{{"\t"}}{{.Size}}{{"\t"}}{{len .RootFS.Layers}}' "$image_id" 2>/dev/null || true)"
  fields="${fields}"$'\t'"${image_id}"
  [[ -n "$fields" ]] || return 0
  python3 "$SCRIPT_DIR/sanitize_docker_state.py" image "$output_path" "$role" "$fields"
}

write_stats_summary() {
  local container_id="$1"
  local output_path="$2"
  local role="$3"
  local fields
  fields="$(docker stats --no-stream --format '{{.CPUPerc}}{{"\t"}}{{.MemPerc}}{{"\t"}}{{.PIDs}}' "$container_id" 2>/dev/null || true)"
  [[ -n "$fields" ]] || return 0
  python3 "$SCRIPT_DIR/sanitize_docker_state.py" stats "$output_path" "$role" "$fields"
}

sample_docker_stats() {
  local output_path="$1"
  local role="$2"
  local service="$role"
  [[ "$service" == remote-helper ]] && service=remote
  local container_id fields timestamp
  container_id="$(compose ps -q "$service" 2>/dev/null || true)"
  [[ -n "$container_id" ]] || return 0
  fields="$(docker stats --no-stream --format '{{.CPUPerc}}{{"\t"}}{{.MemPerc}}{{"\t"}}{{.PIDs}}' "$container_id" 2>/dev/null || true)"
  [[ -n "$fields" ]] || return 0
  timestamp="$(date -u +%s%3N)"
  python3 "$SCRIPT_DIR/sanitize_docker_state.py" stats-sample "$output_path" "$role" "${timestamp}"$'\t'"${fields}"
}

capture_cgroup_cpu_boundary() {
  local output_path="$1"
  local role="$2"
  local boundary="$3"
  local phase_timestamp="$4"
  local service="$role" container_id cpu_stat observed_timestamp
  [[ "$service" == remote-helper ]] && service=remote
  container_id="$(compose ps -q "$service" 2>/dev/null || true)"
  [[ -n "$container_id" ]] || {
    echo "missing live $role container for cgroup CPU boundary" >&2
    return 1
  }
  # Read only the cgroup counter. The sanitizer retains no raw response,
  # container ID, path, command, or other Docker metadata. A failed read is
  # fatal so partial boundaries cannot certify a run.
  cpu_stat="$(docker exec "$container_id" cat /sys/fs/cgroup/cpu.stat 2>/dev/null)"
  observed_timestamp="$(date -u +%s%3N)"
  printf '%s' "$cpu_stat" | python3 "$SCRIPT_DIR/sanitize_docker_state.py" \
    cgroup-stat "$output_path" "$role" "$boundary" "$phase_timestamp" "$observed_timestamp"
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
  local model_summary_url="$base_url/server/model/v1/summary"
  local app_docker_stats_series="$phase_dir/app-docker-stats-series.json"
  local remote_docker_stats_series="$phase_dir/remote-helper-docker-stats-series.json"
  local cgroup_cpu_series="$phase_dir/cgroup-cpu-series.json"
  local model_summary_series="$phase_dir/model-summary-samples.json"
  printf '%s\n' '{"schema":"seedsync.performance-lab.container-stats-series.v1","role":"app","samples":[]}' > "$app_docker_stats_series"
  printf '%s\n' '{"schema":"seedsync.performance-lab.container-stats-series.v1","role":"remote-helper","samples":[]}' > "$remote_docker_stats_series"
  printf '%s\n' '{"schema":"seedsync.performance-lab.cgroup-cpu-series.v1","samples":[]}' > "$cgroup_cpu_series"
  printf '%s\n' '{"schema":"seedsync.performance-lab.model-summary-series.v1","samples":[]}' > "$model_summary_series"
  python3 - "$phase_dir/window-note.json" <<'PY'
import json, os, sys
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
print(topology.get("enabled_expected_merged_model_tree_nodes",
                   topology["expected_merged_model_tree_nodes"]))
PY
)"
  local expected_summary_count
  expected_summary_count="$(python3 - "$ARTIFACT_DIR/fixture-manifest.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
topology = manifest.get("topology", {})
total = 0
summary_children_by_pair = topology.get("summary_children_by_pair")
for pair in manifest.get("path_pairs", []):
    if pair.get("enabled") is False:
        continue
    if isinstance(summary_children_by_pair, dict) and pair.get("id") in summary_children_by_pair:
        total += int(summary_children_by_pair[pair["id"]])
        continue
    nodes = pair.get("nodes_local")
    if type(nodes) is not int or nodes < 0:
        continue
    total += (nodes + 2047) // 2048
    directory = pair.get("directory")
    for target in pair.get("remote_only_targets", []):
        parts = [part for part in str(target.get("relative_path", "")).split("/") if part]
        if parts and parts[0] == directory:
            parts = parts[1:]
        if len(parts) == 1:
            total += 1
print(total)
PY
)"
  local deadline=$(( $(date +%s) + PERF_MEASURE_TIMEOUT_SECONDS ))
  local index=0
  local last_success_index=0
  local summary_stable_count=0 summary_last_version="" summary_last_root_count="" summary_success_count=0
  local summary_quiet_count=0 target_stable_count="" target_quiet_count=""
  local target_root_count="" target_model_version=""
  local end_diagnostics_path="" end_model_summary_path=""
  local end_state_validated=0 end_state_root_count="" end_state_model_version=""
  local end_state_sequence="" end_state_build_count=""
  while (( $(date +%s) < deadline )); do
    index=$((index + 1))
    sample_docker_stats "$app_docker_stats_series" app || true
    sample_docker_stats "$remote_docker_stats_series" remote-helper || true
    if [[ -n "$target_ms" && -z "$settled_ms" ]]; then
      local observation_now_ms
      observation_now_ms="$(date -u +%s%3N)"
      if (( observation_now_ms < target_ms + (PERF_POST_TARGET_OBSERVATION_SECONDS + PERF_CGROUP_CAPTURE_LAG_MAX_SECONDS) * 1000 )); then
        # After target, resource-only sampling keeps observer load comparable
        # for diagnostics on/off. Do not serialize model state on each poll.
        sleep "$PERF_POST_TARGET_POLL_SECONDS"
        continue
      fi
      local final_valid=0
      if [[ "$PERF_DIAGNOSTICS_MODE" == off ]]; then
        end_model_summary_path="$phase_dir/model-summary-at-end.json"
        if curl --silent --show-error --fail --max-time 120 \
          -H "Authorization: Bearer $PERF_API_TOKEN" "$model_summary_url" > "$end_model_summary_path"; then
          read -r final_summary_count final_summary_version <<<"$(python3 - "$end_model_summary_path" <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["PERF_LAB_SOURCE_DIR"])
from capture_metrics import latest_model_summary_status
status = latest_model_summary_status(json.load(open(sys.argv[1], encoding="utf-8")))
print(status.get("root_count", ""), status.get("model_version", ""))
PY
)"
          if [[ "$final_summary_count" == "$target_root_count" &&
                "$final_summary_version" == "$target_model_version" ]]; then
            final_valid=1
          fi
          end_state_root_count="$final_summary_count"
          end_state_model_version="$final_summary_version"
        fi
      else
        end_diagnostics_path="$phase_dir/diagnostics-at-end.json"
        if curl --silent --show-error --fail --max-time 120 \
          -H "Authorization: Bearer $PERF_API_TOKEN" "$diagnostics_url" > "$end_diagnostics_path"; then
          read -r final_count final_sequence final_build_count <<<"$(python3 - "$end_diagnostics_path" <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["PERF_LAB_SOURCE_DIR"])
from capture_metrics import latest_model_status
count, sequence, build_count = latest_model_status(json.load(open(sys.argv[1], encoding="utf-8")))
print(count or "", sequence or "", build_count if build_count is not None else "")
PY
)"
          if [[ -n "$final_count" && -n "$final_sequence" && "$final_build_count" == 0 &&
                "$final_count" == "$target_root_count" ]] &&
             (( final_sequence >= target_sequence + PERF_SETTLED_SAMPLES )); then
            final_valid=1
          fi
          end_state_root_count="$final_count"
          end_state_sequence="$final_sequence"
          end_state_build_count="$final_build_count"
        fi
      fi
      if (( final_valid == 0 )); then
        # End-state instability invalidates the candidate boundary. Start a
        # fresh target window; never certify a partial or stale observation.
        target_ms=""; settled_ms=""; target_index=""; target_sequence=""
        target_root_count=""; target_model_version=""
        target_stable_count=""; target_quiet_count=""
        end_state_validated=0; end_state_root_count=""; end_state_model_version=""
        end_state_sequence=""; end_state_build_count=""
        summary_quiet_count=0; summary_stable_count=0
        summary_last_version=""; summary_last_root_count=""
        printf '%s\n' '{"schema":"seedsync.performance-lab.cgroup-cpu-series.v1","samples":[]}' > "$cgroup_cpu_series"
        sleep "$PERF_SAMPLE_SLEEP_SECONDS"
        continue
      fi
      end_state_validated=1
      local end_phase_ms
      end_phase_ms="$(date -u +%s%3N)"
      # The phase boundary is the timestamp immediately before the cgroup
      # reads. Sanitized records retain each read's later observed timestamp.
      settled_ms="$end_phase_ms"
      capture_cgroup_cpu_boundary "$cgroup_cpu_series" app end "$end_phase_ms"
      capture_cgroup_cpu_boundary "$cgroup_cpu_series" remote-helper end "$end_phase_ms"
python3 - "$phase_dir/phase-timing.json" "$started_ms" "$first_ms" "$target_ms" "$settled_ms" \
    "$end_state_validated" "$target_root_count" "$end_state_root_count" "$target_model_version" \
    "$end_state_model_version" "$target_sequence" "$end_state_sequence" "$end_state_build_count" <<'PY'
import json, os, sys
started, first, target, settled = map(int, sys.argv[2:6])
def integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "schema": "seedsync.performance-lab.phase-timing.v1",
        "started_epoch_ms": started, "first_observation_epoch_ms": first,
        "model_target_epoch_ms": target, "settled_epoch_ms": settled,
        "cold_start_to_first_diagnostics_ms": first - started,
        "startup_to_first_observation_ms": first - started,
        "full_scan_ms": target - first,
        "post_scan_settled_idle_observation_ms": settled - target,
        "post_target_observation_required_seconds": int(os.environ["PERF_POST_TARGET_OBSERVATION_SECONDS"]),
        "end_state_validated": sys.argv[6] == "1",
        "target_root_count": integer(sys.argv[7]), "end_root_count": integer(sys.argv[8]),
        "target_model_version": integer(sys.argv[9]), "end_model_version": integer(sys.argv[10]),
        "target_sequence": integer(sys.argv[11]), "end_sequence": integer(sys.argv[12]),
        "end_build_count": integer(sys.argv[13]),
    }, indent=2) + "\n")
PY
      break
    fi
    if [[ "$PERF_DIAGNOSTICS_MODE" == off ]]; then
      local summary_path=""
      summary_path="$(mktemp "$phase_dir/.model-summary.XXXXXX")"
      if ! curl --silent --show-error --fail --max-time 20 \
        -H "Authorization: Bearer $PERF_API_TOKEN" "$model_summary_url" > "$summary_path"; then
        rm -f -- "$summary_path"
        # A failed summary leaves no paired model/CPU observation. It must
        # break the quiet suffix before an external target can be declared;
        # successful-summary model stability remains intentionally intact.
        summary_quiet_count=0
        local app_id app_running
        app_id="$(compose ps --all -q app)"
        app_running=""
        if [[ -n "$app_id" ]]; then
          app_running="$(docker inspect --format '{{.State.Running}}' "$app_id" 2>/dev/null || true)"
        fi
        if [[ "$app_running" != true ]]; then
          [[ -n "$app_id" ]] && write_container_summary "$app_id" "$phase_dir/app-exited-state.json" app
          echo "App exited before model summary became ready; see $phase_dir/app-exited-state.json" >&2
          return 1
        fi
        sleep "$PERF_SAMPLE_SLEEP_SECONDS"
        continue
      fi
      summary_success_count=$((summary_success_count + 1))
      local summary_sample_index=$((summary_success_count - 1))
      last_success_index="$summary_success_count"
      [[ -n "$first_ms" ]] || first_ms="$(date -u +%s%3N)"
      local observed_summary_count observed_model_version summary_pair_count
      local observed_app_cpu
      read -r observed_summary_count observed_model_version summary_pair_count observed_app_cpu <<<"$(python3 - "$summary_path" "$app_docker_stats_series" <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["PERF_LAB_SOURCE_DIR"])
from capture_metrics import latest_model_summary_status
status = latest_model_summary_status(json.load(open(sys.argv[1], encoding="utf-8")))
app_cpu = ""
try:
    stats = json.load(open(sys.argv[2], encoding="utf-8"))
    samples = stats.get("samples", []) if isinstance(stats, dict) else []
    if samples and isinstance(samples[-1], dict) and type(samples[-1].get("cpu_percent")) in (int, float):
        app_cpu = str(samples[-1]["cpu_percent"])
except (OSError, ValueError, TypeError):
    pass
print(*(str(status.get(key)) if status.get(key) is not None else ""
        for key in ("root_count", "model_version", "pair_count")), app_cpu)
PY
)"
      python3 - "$model_summary_series" "$summary_path" "$summary_sample_index" "$(date -u +%s%3N)" "$app_docker_stats_series" <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["PERF_LAB_SOURCE_DIR"])
from capture_metrics import latest_model_summary_status
series_path, summary_path, sample_index, epoch_ms, app_stats_path = sys.argv[1:]
status = latest_model_summary_status(json.load(open(summary_path, encoding="utf-8")))
app_cpu = None
try:
    stats = json.load(open(app_stats_path, encoding="utf-8"))
    samples = stats.get("samples", []) if isinstance(stats, dict) else []
    if samples and isinstance(samples[-1], dict) and type(samples[-1].get("cpu_percent")) in (int, float):
        app_cpu = samples[-1]["cpu_percent"]
except (OSError, ValueError, TypeError):
    pass
payload = json.loads(open(series_path, encoding="utf-8").read())
payload.setdefault("samples", []).append({"sample_index": int(sample_index), "t_epoch_ms": int(epoch_ms),
                                           "app_cpu_percent": app_cpu, **status})
open(series_path, "w", encoding="utf-8").write(json.dumps(payload, indent=2) + "\n")
PY
      rm -f -- "$summary_path"
      if [[ -n "$observed_summary_count" && -n "$observed_model_version" &&
            "$observed_summary_count" == "$summary_last_root_count" &&
            "$observed_model_version" == "$summary_last_version" ]]; then
        summary_stable_count=$((summary_stable_count + 1))
      elif [[ -n "$observed_summary_count" && -n "$observed_model_version" ]]; then
        summary_stable_count=1
        summary_last_root_count="$observed_summary_count"
        summary_last_version="$observed_model_version"
        summary_quiet_count=0
      else
        summary_stable_count=0
        summary_last_version=""
        summary_last_root_count=""
        summary_quiet_count=0
      fi
      local low_cpu_sample=0
      if [[ -n "$observed_app_cpu" ]] &&
         (( $(awk "BEGIN { print ($observed_app_cpu >= 0 && $observed_app_cpu <= $PERF_SETTLED_IDLE_CPU_PERCENT) }") )); then
        low_cpu_sample=1
      fi
      if (( low_cpu_sample == 1 )) &&
         [[ -n "$observed_summary_count" && "$observed_summary_count" == "$summary_last_root_count" &&
            -n "$observed_model_version" && "$observed_model_version" == "$summary_last_version" ]]; then
        summary_quiet_count=$((summary_quiet_count + 1))
      else
        summary_quiet_count=0
      fi
      if [[ -z "$target_ms" && -n "$observed_summary_count" && -n "$observed_model_version" ]] && \
         (( observed_summary_count == expected_summary_count &&
            summary_stable_count >= PERF_EXTERNAL_MODEL_STABILITY_SAMPLES &&
            summary_quiet_count >= PERF_EXTERNAL_READINESS_SAMPLES )); then
        target_ms="$(date -u +%s%3N)"
        # The external summarizer indexes the retained successful-sample
        # array, not poll attempts. Startup curl failures therefore must not
        # create gaps in this zero-based boundary.
        target_index="$summary_sample_index"
        target_sequence="$observed_model_version"
         target_root_count="$observed_summary_count"
         target_model_version="$observed_model_version"
         target_stable_count="$summary_stable_count"
         target_quiet_count="$summary_quiet_count"
         capture_cgroup_cpu_boundary "$cgroup_cpu_series" app target "$target_ms"
         capture_cgroup_cpu_boundary "$cgroup_cpu_series" remote-helper target "$target_ms"
        python3 - "$phase_dir/cold-start.json" "$started_ms" "$first_ms" "$target_ms" "$expected_summary_count" "$target_sequence" "$target_index" "$target_stable_count" "$target_quiet_count" "$PERF_SETTLED_IDLE_CPU_PERCENT" "$PERF_POST_TARGET_OBSERVATION_SECONDS" <<'PY'
import json, sys
started, first, target, expected, version, sample_index, stable_count, quiet_count = map(int, sys.argv[2:10])
cpu_threshold = float(sys.argv[10])
observation_seconds = int(sys.argv[11])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(json.dumps({"schema": "seedsync.performance-lab.cold-start.v1",
        "started_epoch_ms": started, "first_diagnostics_epoch_ms": first,
        "model_target_epoch_ms": target, "startup_to_first_diagnostics_ms": first - started,
        "full_scan_to_model_target_ms": target - first,
        "expected_model_summary_root_cardinality": expected,
        "target_model_version": version, "target_sample_index": sample_index,
        "target_readiness": {
            "condition": "expected root cardinality and model version stable for at least 2 consecutive successful summaries plus 3 consecutive app CPU samples at or below the hard 1.0% gate; reset on cardinality/version change",
            "model_stable_consecutive_samples": stable_count,
            "app_cpu_quiet_consecutive_samples": quiet_count,
            "app_cpu_threshold_percent": cpu_threshold,
            "required_post_target_observation_seconds": observation_seconds,
        }}, indent=2) + "\n")
PY
      sleep "$PERF_SAMPLE_SLEEP_SECONDS"
      continue
      fi
    fi
    local diagnostics_path="$phase_dir/diagnostics-$(printf '%04d' "$index").json"
    # Monitoring needs only the latest retained sample plus cumulative
    # counters/durations. Re-fetching the full retained history here makes the
    # observer itself a recurring CPU load and contaminates idle acceptance.
    if ! curl --silent --show-error --fail --max-time 20 \
      -H "Authorization: Bearer $PERF_API_TOKEN" "$diagnostics_url?limit=1" > "$diagnostics_path"; then
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
       target_root_count="$observed_count"
       target_model_version="$observed_sequence"
       cp "$diagnostics_path" "$phase_dir/diagnostics-at-target.json"
       capture_cgroup_cpu_boundary "$cgroup_cpu_series" app target "$target_ms"
       capture_cgroup_cpu_boundary "$cgroup_cpu_series" remote-helper target "$target_ms"
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
    fi
    sleep "$PERF_SAMPLE_SLEEP_SECONDS"
  done
  if (( last_success_index == 0 )); then
    echo "Timed out waiting for diagnostics readiness" >&2
    return 1
  fi
  local metrics_status=0
  if [[ "$PERF_DIAGNOSTICS_MODE" == on ]]; then
    # Fetch each potentially expensive support payload exactly once after the
    # timed observation. Their serialization cannot then inflate a later idle
    # sample, while the full retained history remains available for analysis.
    if [[ -n "$end_diagnostics_path" && -f "$end_diagnostics_path" ]]; then
      cp "$end_diagnostics_path" "$phase_dir/diagnostics.json"
    elif ! curl --silent --show-error --fail --max-time 120 \
        -H "Authorization: Bearer $PERF_API_TOKEN" "$diagnostics_url" \
        > "$phase_dir/diagnostics.json"; then
      echo "Failed to capture final diagnostics history" >&2
      return 1
    fi
    if ! curl --silent --show-error --fail --max-time 20 \
      -H "Authorization: Bearer $PERF_API_TOKEN" "$base_url/server/breadcrumbs/get" \
      > "$phase_dir/breadcrumbs.json"; then
      printf '{}\n' > "$phase_dir/breadcrumbs.json"
    fi
    local target_argument=()
    if [[ -n "$target_sequence" ]]; then
      target_argument=(--target-sequence "$target_sequence")
    fi
    python3 "$SCRIPT_DIR/capture_metrics.py" \
      --diagnostics "$phase_dir/diagnostics.json" \
      --manifest "$ARTIFACT_DIR/fixture-manifest.json" \
      --output "$phase_dir/metrics-summary.json" \
      --label "$label" \
      --breadcrumbs "$phase_dir/breadcrumbs.json" \
      --docker-stats "$app_docker_stats_series" \
      --remote-docker-stats "$remote_docker_stats_series" \
      --cgroup-stats "$cgroup_cpu_series" \
      --phase-timing "$phase_dir/phase-timing.json" \
      --settled-idle-cpu-percent "$PERF_SETTLED_IDLE_CPU_PERCENT" \
      --minimum-merged-nodes "$PERF_MINIMUM_MERGED_NODES" \
      --breadcrumb-mode "$PERF_BREADCRUMB_MODE" \
      "${target_argument[@]}" || metrics_status=$?
  else
    local diagnostics_disabled_path="$phase_dir/diagnostics.json"
    printf '%s\n' '{"schema":"seedsync.performance-diagnostics.v1","enabled":false,"samples":[],"counters":{}}' > "$diagnostics_disabled_path"
    printf '{}\n' > "$phase_dir/breadcrumbs.json"
    local external_target_argument=()
    if [[ -n "$target_index" ]]; then external_target_argument=(--target-sequence "$target_index"); fi
    python3 "$SCRIPT_DIR/capture_metrics.py" \
      --mode external-summary \
      --model-summary "$model_summary_series" \
      --docker-stats "$app_docker_stats_series" \
      --remote-docker-stats "$remote_docker_stats_series" \
      --cgroup-stats "$cgroup_cpu_series" \
      --manifest "$ARTIFACT_DIR/fixture-manifest.json" \
      --output "$phase_dir/metrics-summary.json" \
      --label "$label" \
      --phase-timing "$phase_dir/phase-timing.json" \
      --settled-idle-cpu-percent "$PERF_SETTLED_IDLE_CPU_PERCENT" \
      --minimum-merged-nodes "$PERF_MINIMUM_MERGED_NODES" \
      --breadcrumb-mode "$PERF_BREADCRUMB_MODE" \
      "${external_target_argument[@]}" || metrics_status=$?
  fi
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
import json, os, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    baseline = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    candidate = json.load(handle)
def image_identity(summary_path):
    image_path = os.path.join(os.path.dirname(summary_path), "app-image-inspect.json")
    try:
        with open(image_path, encoding="utf-8") as handle:
            return json.load(handle).get("identity_digest")
    except (OSError, ValueError, TypeError):
        return None
baseline_image_identity = image_identity(sys.argv[1])
candidate_image_identity = image_identity(sys.argv[2])
comparison = {
    "schema": "seedsync.performance-lab.comparison.v1",
    "same_fixture_fingerprint": baseline.get("fixture_fingerprint") == candidate.get("fixture_fingerprint"),
    "same_image_identity": baseline_image_identity is not None and baseline_image_identity == candidate_image_identity,
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
    "baseline_external_docker_stats": baseline.get("external_docker_stats"),
    "candidate_external_docker_stats": candidate.get("external_docker_stats"),
    "baseline_app_docker_stats": baseline.get("app_docker_stats"),
    "candidate_app_docker_stats": candidate.get("app_docker_stats"),
    "baseline_remote_helper_docker_stats": baseline.get("remote_helper_docker_stats"),
    "candidate_remote_helper_docker_stats": candidate.get("remote_helper_docker_stats"),
    "baseline_post_target_external_docker_stats": baseline.get("post_target_external_docker_stats"),
    "candidate_post_target_external_docker_stats": candidate.get("post_target_external_docker_stats"),
    "baseline_post_target_app_docker_stats": baseline.get("post_target_app_docker_stats"),
    "candidate_post_target_app_docker_stats": candidate.get("post_target_app_docker_stats"),
    "baseline_post_target_remote_helper_docker_stats": baseline.get("post_target_remote_helper_docker_stats"),
    "candidate_post_target_remote_helper_docker_stats": candidate.get("post_target_remote_helper_docker_stats"),
    "baseline_app_cgroup_cpu": baseline.get("app_cgroup_cpu"),
    "candidate_app_cgroup_cpu": candidate.get("app_cgroup_cpu"),
    "baseline_remote_helper_cgroup_cpu": baseline.get("remote_helper_cgroup_cpu"),
    "candidate_remote_helper_cgroup_cpu": candidate.get("remote_helper_cgroup_cpu"),
    "baseline_cpu_acceptance_source": baseline.get("cpu_acceptance_source"),
    "candidate_cpu_acceptance_source": candidate.get("cpu_acceptance_source"),
}
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    handle.write(json.dumps(comparison, indent=2) + "\n")
if not comparison["same_fixture_fingerprint"] or not comparison["same_image_identity"]:
    raise SystemExit("baseline and candidate must use the same fixture and immutable image")
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
  write_compose_summary "$ARTIFACT_DIR/compose-state-stop.json"
  echo "Stopped app and remote containers; retained volumes and artifacts"
}

validate_browser_target_binding() (
  local manifest="$1"
  local run_manifest="$2"
  local output_path="$3"
  local app_id inspect_path marker_path volume_name
  app_id="$(compose ps -q app 2>/dev/null || true)"
  [[ -n "$app_id" ]] || { echo "live app service is not running; refusing browser target binding" >&2; return 1; }
  inspect_path="$(mktemp)"
  marker_path="$(mktemp)"
  trap 'rm -f -- "$inspect_path" "$marker_path"' EXIT
  docker inspect "$app_id" > "$inspect_path"
  volume_name="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/mounts"}}{{.Name}}{{end}}{{end}}' "$app_id" 2>/dev/null || true)"
  [[ "$volume_name" == "${PROJECT}_perf_local_fixture" ]] || {
    echo "live app /mounts mount is not the exact synthetic Docker volume; refusing browser target binding" >&2
    return 1
  }
  docker run --rm --mount "type=volume,source=$volume_name,target=/fixture,readonly" \
    "${PERF_FIXTURE_IMAGE:-python:3.12-slim}" python3 -c \
    'import json; print(json.dumps(json.load(open("/fixture/.seedsync-performance-fixture.json"))))' \
    > "$marker_path"
  python3 - "$manifest" "$run_manifest" "$inspect_path" "$marker_path" "$PROJECT" "$app_id" "$volume_name" "$output_path" <<'PY'
import hashlib
import json
import posixpath
import re
import sys
from pathlib import Path

manifest_path, run_path, inspect_path, marker_path, project, app_id, volume_name, output_path = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
run_manifest = json.loads(Path(run_path).read_text(encoding="utf-8"))
inspect_payload = json.loads(Path(inspect_path).read_text(encoding="utf-8"))
inspect = inspect_payload[0] if isinstance(inspect_payload, list) and inspect_payload else {}
labels = ((inspect.get("Config") or {}).get("Labels") or {})
if labels.get("com.docker.compose.project") != project:
    raise SystemExit("live app compose project label does not match requested project")
if labels.get("com.docker.compose.service") != "app":
    raise SystemExit("live container is not the app compose service")
mounts = inspect.get("Mounts") or []
mount = next((item for item in mounts if item.get("Destination") == "/mounts"), None)
if not isinstance(mount, dict) or mount.get("Type") != "volume" \
        or mount.get("Name") != volume_name or not mount.get("Source"):
    raise SystemExit("live app /mounts mount type, source, or destination does not match")
marker = json.loads(Path(marker_path).read_text(encoding="utf-8"))
fixture_fingerprint = manifest.get("fixture_fingerprint")
if marker.get("fixture_fingerprint") != fixture_fingerprint:
    raise SystemExit("live fixture marker fingerprint does not match manifest")
marker_manifest = marker.get("manifest")
if not isinstance(marker_manifest, dict) \
        or marker_manifest.get("data_topology_spec") != manifest.get("data_topology_spec"):
    raise SystemExit("live fixture marker topology does not match manifest")
if marker_manifest.get("profile") != run_manifest.get("profile"):
    raise SystemExit("live fixture marker profile does not match run identity")
project_digest = hashlib.sha256(project.encode("utf-8")).hexdigest()
if run_manifest.get("project_digest") != project_digest:
    raise SystemExit("run identity does not match live compose project")
manifest_high_card_enabled = next(
    (pair.get("enabled") for pair in manifest.get("path_pairs", [])
     if pair.get("role") == "high-cardinality-idle"), True
)
if run_manifest.get("profile") != manifest.get("profile") \
        or run_manifest.get("high_card_enabled") != manifest_high_card_enabled:
    raise SystemExit("run identity does not match fixture profile")
run_id_digest = run_manifest.get("run_id_digest")
if not isinstance(run_id_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", run_id_digest):
    raise SystemExit("run identity digest is missing or invalid")
targets = []
target_descriptors = []
for pair in manifest.get("path_pairs", []):
    if pair.get("role") != "ordinary-active":
        continue
    for target in pair.get("remote_only_targets", []):
        parts = [part for part in str(target.get("relative_path", "")).split("/") if part]
        if parts and parts[0] == pair.get("directory"):
            parts = parts[1:]
        targets.append(posixpath.join(str(pair.get("local_path", "")), *parts))
        target_descriptors.append(target)
if len(targets) != 1:
    raise SystemExit("manifest must resolve exactly one browser target")
if len(target_descriptors) != 1:
    raise SystemExit("manifest must resolve exactly one browser target descriptor")
descriptor = target_descriptors[0]
kind = descriptor.get("kind", "file")
if kind not in ("file", "directory"):
    raise SystemExit("manifest browser target kind is unsupported")
if kind == "directory":
    for field in ("size_bytes", "file_count", "directory_count", "max_depth", "storage_size_bytes"):
        if not isinstance(descriptor.get(field), int) or descriptor[field] <= 0:
            raise SystemExit(f"manifest browser directory target has invalid {field}")
    if descriptor["storage_size_bytes"] > descriptor["size_bytes"]:
        raise SystemExit("manifest browser directory target storage bound exceeds logical aggregate")
    if descriptor.get("storage_mode") != "real-bytes-hardlink-deduplicated":
        raise SystemExit("manifest browser directory target has unexpected storage mode")
def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()
topology_payload = json.dumps(manifest.get("data_topology_spec"), sort_keys=True, separators=(",", ":"))
binding_target = {"kind": kind}
if kind == "directory":
    binding_target.update({
        "size_bytes": descriptor["size_bytes"],
        "storage_mode": descriptor["storage_mode"],
        "storage_size_bytes": descriptor["storage_size_bytes"],
        "file_count": descriptor["file_count"],
        "directory_count": descriptor["directory_count"],
        "max_depth": descriptor["max_depth"],
    })
binding = {
    "schema": "seedsync.performance-lab.browser-target-binding.v1",
    "validated": True,
    "service": "app",
    "target_path": targets[0],
    "target": binding_target,
    "container_id_digest": digest(app_id),
    "project_digest": project_digest,
    "volume": {
        "type": mount.get("Type"), "target": mount.get("Destination"),
        "source_name_digest": digest(volume_name), "source_path_digest": digest(mount.get("Source")),
    },
    "fixture": {
        "fixture_fingerprint": fixture_fingerprint,
        "manifest_topology_digest": digest(topology_payload),
        "marker_topology_digest": digest(json.dumps(marker_manifest.get("data_topology_spec"), sort_keys=True, separators=(",", ":"))),
    },
    "run_id_digest": run_id_digest,
}
Path(output_path).write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "container_id_digest": binding["container_id_digest"],
    "volume_source_name_digest": binding["volume"]["source_name_digest"],
    "target_path": binding["target_path"],
}, sort_keys=True))
PY
)

browser() {
  local label="${1:-}"
  if [[ -z "$label" || ! "$label" =~ ^[a-z0-9][a-z0-9_-]{0,31}$ ]]; then
    echo "usage: $0 browser <label> (lowercase artifact label)" >&2
    exit 2
  fi
  ensure_artifacts
  local phase_dir="$ARTIFACT_DIR/$label"
  mkdir -p "$phase_dir"
  local manifest="$ARTIFACT_DIR/fixture-manifest.json"
  [[ -f "$manifest" ]] || { echo "fixture manifest is missing: $manifest" >&2; return 1; }
  local target_kind
  target_kind="$(python3 - "$manifest" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
targets = [target for pair in manifest.get("path_pairs", [])
           if pair.get("role") == "ordinary-active"
           for target in pair.get("remote_only_targets", [])]
if len(targets) != 1:
    raise SystemExit("manifest must resolve exactly one browser target")
print(targets[0].get("kind", "file"))
PY
)"
  case "$target_kind" in
    directory)
      if [[ -z "$PROJECT_WAS_EXPLICIT" ]]; then
        echo "directory browser target requires a fresh unique PERF_PROJECT; do not reuse the default project" >&2
        return 2
      fi
      local browser_marker="$ARTIFACT_DIR/browser-run.marker"
      if [[ -e "$browser_marker" ]]; then
        echo "directory browser target already ran for this PERF_PROJECT; use a fresh unique PERF_PROJECT" >&2
        return 2
      fi
      printf '%s\n' 'seedsync.performance-lab.browser-run.v1' > "$browser_marker"
      ;;
    file) ;;
    *) echo "manifest browser target kind is unsupported: $target_kind" >&2; return 1 ;;
  esac
  write_run_metadata "browser-$label"
  local output="$phase_dir/browser-timeline.json"
  local binding="$phase_dir/browser-target-binding.json"
  # Capture the sanitized immutable image identity before browser actions. The
  # run manifest retains only a one-way digest of the mutable image tag.
  capture_container_state "$phase_dir"
  local image_identity_digest
  image_identity_digest="$(python3 - "$phase_dir/app-image-inspect.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle).get("identity_digest")
except (OSError, ValueError, TypeError):
    value = None
if isinstance(value, str) and len(value) == 64:
    print(value)
PY
)"
  [[ "$image_identity_digest" =~ ^[0-9a-fA-F]{64}$ ]] || {
    echo "sanitized immutable app image identity is missing: $phase_dir/app-image-inspect.json" >&2
    return 1
  }
  validate_browser_target_binding "$manifest" "$ARTIFACT_DIR/run-manifest.json" "$binding"
  if [[ "$target_kind" == file ]]; then
    echo "Live browser Delete Local binding (sanitized digests):"
    cat "$binding"
    local delete_targets
    delete_targets="$(python3 - "$manifest" <<'PY'
import json, posixpath, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
for pair in manifest.get("path_pairs", []):
    if pair.get("role") != "ordinary-active":
        continue
    for target in pair.get("remote_only_targets", []):
        parts = [part for part in str(target.get("relative_path", "")).split("/") if part]
        if parts and parts[0] == pair.get("directory"):
            parts = parts[1:]
        print(posixpath.join(str(pair.get("local_path", "")), *parts))
PY
)"
    [[ -n "$delete_targets" ]] || { echo "no synthetic Delete Local target found" >&2; return 1; }
    echo "Browser probe Delete Local targets (synthetic Docker volume only):"
    printf '  %s\n' "$delete_targets"
    if [[ "$PERF_BROWSER_DESTRUCTIVE_APPROVED" != on ]]; then
      echo "Refusing destructive browser probe without explicit approval; set PERF_BROWSER_DESTRUCTIVE_APPROVED=on only after the displayed target set is approved" >&2
      return 2
    fi
  else
    echo "Live browser target binding (sanitized digests):"
    cat "$binding"
  fi
  local node_path_args=()
  if [[ -n "$PERF_NODE_PATH" ]]; then
    node_path_args=(env "NODE_PATH=$PERF_NODE_PATH")
  fi
  # The browser harness reads PERF_API_TOKEN directly from its environment;
  # credentials are never passed as arguments or written to run metadata.
  set +e
  "${node_path_args[@]}" "$PERF_NODE_BINARY" "$SCRIPT_DIR/browser_probe.js" \
    --label "$label" \
    --base-url "http://127.0.0.1:$PERF_HOST_PORT" \
    --manifest "$manifest" \
    --run-manifest "$ARTIFACT_DIR/run-manifest.json" \
    --binding "$binding" \
    --image-identity-digest "$image_identity_digest" \
    --output "$output"
  local status=$?
  set -e
  echo "Browser timeline artifact: $output"
  return "$status"
}

case "${1:-}" in
  prepare) prepare ;;
  start) start ;;
  status) status ;;
  measure) shift; measure "$@" ;;
  browser) shift; browser "$@" ;;
  stop) stop ;;
  *) echo "usage: $0 prepare|start|status|measure baseline|candidate|browser label|stop" >&2; exit 2 ;;
esac
