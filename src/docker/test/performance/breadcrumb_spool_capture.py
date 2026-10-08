"""Evidence-only, descriptor-relative reader for the durable breadcrumb spool.

The reader is deliberately a small Linux primitive.  It never creates, writes,
renames, or deletes anything below the application spool.  A verified directory
descriptor is the authority for all spool reads; the path accepted by the
constructor is only a compatibility convenience for opening that descriptor.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Mapping

from incoming_recovery_observer import JsonArtifactSink, ObserverCaptureError


_ACTIVE_NAME = "breadcrumbs.jsonl"
_HEALTH_NAME = "breadcrumbs.health.json"
_MARKER_NAME = "breadcrumbs.health.invalid"
_ROTATED_NAME = re.compile(r"^breadcrumbs-(\d+)-(\d+)\.jsonl$")
_MAX_HEALTH_BYTES = 256 * 1024
_READ_CHUNK_BYTES = 8192

# These are the fields emitted by the maintained breadcrumb producers.  The
# reader intentionally does not copy arbitrary metadata, message text, or
# labels into the evidence artifact.
_CATEGORIES = frozenset(
    {
        "queue.lifecycle", "queue.admission", "queue.executor", "queue.authority",
        "queue.readiness", "queue.exclusion", "model.lifecycle", "model.progress", "model_page_snapshot",
        "model.publication", "model.finalization", "model.root_progress", "status",
        "transfer.lftp", "transfer.lftp.executor", "transfer.lftp.membership",
        "transfer.lftp.status", "transfer.stop", "scan.authority", "scan.adoption", "scan.failure",
        "scan.result", "scan.terminal_publication", "scan.lifecycle", "scan.root_shape", "final_move.publication",
        "finalization.child", "controller", "scanner_process", "lftp.sidecar",
        "lftp.command", "lftp.status", "completion.gate", "transfer.lftp.command", "root.progress",
    }
)
_SOURCES = frozenset(
    {"controller", "model_updater", "status", "scanner", "scanner_process", "lftp", "web", "system"}
)
_EVENT_TYPES = frozenset(
    {"queue", "callback", "status", "completion", "progress", "state_transition", "failure", "diagnostic", "breadcrumb"}
)
_STAGES = frozenset(
    {
        "admission", "dispatch", "publication", "terminal", "queue_lifecycle", "queue_authority_handoff",
        "queue_readiness", "queue_exclusion", "model", "model_progress", "model_delta", "model_update",
        "model_publication", "model_summary", "model_finalization", "scan", "scan_adoption", "scan_authority",
        "model_builder_input_arbitration",
        "scan_accumulator", "extract", "controller", "controller_boundary", "persist_transaction", "lifecycle",
        "completion_gate", "final_move_publication", "finalization_child", "transfer_stop", "command",
        "lftp_executor", "lftp_sidecar_validation", "lftp_path_pair_annotation", "lftp_status_poll",
        "lftp_command_boundary", "scoped_model_stream", "retirement_cleanup", "path_pair_runtime",
        "root_progress_status", "completion_gate", "model_builder_local_root_proof_transition", "ordinary", "refresh", "finish", "transfer", "model_page_snapshot",
    }
)
_LEVELS = frozenset({"info", "warning", "error", "critical", "debug"})
_NUMERIC_KEYS = frozenset(
    {
        "attempt", "accepted", "age_ms", "bytes", "bytes_done", "bytes_total", "completed", "count",
        "duration_ms", "elapsed_ms", "expected_size", "files", "files_done", "files_total", "generation",
        "matched", "pending", "percent", "processed", "progress", "queue_depth", "retries", "scanned",
        "size", "status_code", "total", "transferred", "unknown", "unresolved", "version", "written",
    }
)
_HEALTH_COUNTERS = (
    "lost", "critical_lost", "normal_lost", "unknown", "unresolved", "writer_failures", "flush_failures",
    "partial_write_failures", "health_publish_failures", "retention_prune_failures",
)

_QUEUE_LIFECYCLE_EVENTS = frozenset({
    "queue_admitted", "executor_start", "executor_return", "executor_error", "lftp_child", "status_membership",
    "root_default", "queue_retired", "controller_force_close", "operation_scheduled", "operation_retired",
})
_QUEUE_LIFECYCLE_PHASES = frozenset({
    "admission", "executor", "lftp", "status", "publication", "retirement", "teardown", "precheck", "drain",
    "send", "prompt", "connecting", "error_recovery", "unknown",
})
_QUEUE_LIFECYCLE_OUTCOMES = frozenset({
    "accepted", "entered", "returned", "success", "observed", "present", "absent", "ambiguous", "published",
    "retired", "scheduled", "error", "unknown",
})
_QUEUE_LIFECYCLE_ERRORS = frozenset({
    "none", "eof", "exit", "signal", "timeout", "command_error", "parser_error", "terminal_backlog",
    "unhealthy_snapshot", "unknown",
})
_QUEUE_LIFECYCLE_BOUNDARIES = frozenset({
    "queue_admission", "executor", "retirement", "lftp_command", "status_membership", "root_publication",
    "queue_retirement", "teardown",
})
_QUEUE_LIFECYCLE_ENUMS = {
    "status_health": frozenset({"healthy", "unhealthy", "unknown"}),
    "membership": frozenset({"present", "absent", "ambiguous", "unknown"}),
    "status_parse": frozenset({"accepted_empty", "unknown"}),
    "status_result_shape": frozenset({"empty_or_prompt", "queue_done", "job_present", "ambiguous", "parse_error", "unknown"}),
    "status_recovery": frozenset({"none", "connection_grace", "unknown"}),
    "status_state": frozenset({"queued", "running", "none", "unknown"}),
    "membership_reason": frozenset({"filtered", "none", "unknown"}),
    "root_state": frozenset({"default", "unknown"}),
    "coverage": frozenset({"incomplete", "unknown"}),
    "publication_outcome": frozenset({"default_incomplete", "unknown"}),
    "read_buffer_source": frozenset({"public_buffer", "private_buffer", "unavailable"}),
    "duration_bucket": frozenset({"0-4", "5-19", "20-99", "100-499", "500-1999", "2000+", "unknown"}),
    "read_buffer_byte_length_bucket": frozenset({"0", "1-64", "65-1024", "1025-16384", "16385-65536", "65537+", "unknown"}),
    "status_count_bucket": frozenset({"0", "1", "2+", "unknown"}),
    "command_kind": frozenset({"queue", "status", "unknown"}),
    "command_outcome": frozenset({"success", "prompt_timeout", "eof", "error", "unknown"}),
    "reaped": frozenset({"signal", "exit", "alive", "not_alive", "unknown"}),
    "future_state": frozenset({"finalized", "pending", "unknown"}),
}
_LFTP_STATUS_POLL_MESSAGE = "lftp_status_poll"
_LFTP_STATUS_MEMBERSHIP_MESSAGE = "lftp_status_membership_transition"
_LFTP_STATUS_AUTHORITY_SCHEMA = "lftp_status_authority.v1"
_LFTP_STATUS_MEMBERSHIP_SCHEMA = "lftp_status_membership.v2"
_LFTP_STATUS_SOURCES = frozenset({
    "cached_error", "cached_idle", "cached_inflight", "cached_retry", "cached_unhealthy",
    "error_empty", "fresh_healthy", "fresh_unhealthy", "inflight_empty", "retry_empty",
    "unhealthy_empty", "unknown",
})
_LFTP_STATUS_OUTCOMES = _LFTP_STATUS_SOURCES | frozenset({"inflight", "fresh_healthy_empty"})
_LFTP_STATUS_PARSER_OUTCOMES = frozenset({
    "complete_success", "blank_empty", "partial_queue", "hard_error", "not_attempted", "unknown",
})
_LFTP_STATUS_FAILURES = frozenset({
    "none", "inflight", "retry_pending", "parser_error", "command_error", "timeout", "eof",
    "terminal_backlog", "unhealthy_snapshot", "unknown",
})
_LFTP_STATUS_FUTURE_STATES = frozenset({"none", "pending", "done", "unknown"})
_LFTP_STATUS_COUNT_MAX = 128
_LFTP_STATUS_MEMBERSHIP_COUNT_MAX = 2048
_QUEUE_READINESS_SCHEMAS = frozenset({"queue_readiness.v1", "queue_readiness.v2"})
_QUEUE_READINESS_EVENTS = frozenset({
    "queue_admission", "queue_callback", "queue_dispatch_boundary", "queue_final_decision",
    "queue_http_wait", "queue_model_boundary", "queue_preflight_outcome", "queue_rescan_readiness",
})
_QUEUE_READINESS_PHASES = frozenset({
    "admission", "dispatch_boundary", "entry", "final_decision", "model_boundary", "preflight",
    "rescan_deadline", "rescan_deadline_grace", "rescan_ready", "rescan_request", "rescan_unknown",
    "wait_return", "unknown",
})
_QUEUE_READINESS_OUTCOMES = frozenset({
    "accepted", "clear", "deferred", "dispatched", "failed", "failure", "pending", "ready",
    "rejected", "success", "timeout", "unknown",
})
_QUEUE_READINESS_ORIGINS = frozenset({"auto_queue", "manual", "unknown"})
_QUEUE_READINESS_REASONS = frozenset({
    "none", "awaiting_scan_publication", "initial_scan_authority_deadline", "comparison_pending",
    "scoped_rescan_token_unknown", "scoped_rescan_request_failed", "scoped_rescan_requested", "unknown",
    "authoritative_local_root", "authoritative_scoped_scan", "collision_equal_scoped_rescan",
    "collision_authority_stale", "collision_not_terminalizable", "collision_preflight_error",
    "collision_deferred_terminal", "collision_authority_unknown", "no_unresolved_collision",
    "duplicate_waiter_coalesced", "idempotent_claim_settlement_pending", "initial_scan_authority_pending",
    "initial_local_scan_failed", "initial_local_scan_unknown", "initial_remote_scan_failed",
    "initial_remote_scan_unknown", "initial_local_scan_session_changed", "initial_remote_scan_session_changed",
    "initial_local_scan_generation_reset", "initial_remote_scan_generation_reset",
    "collision_initial_local_scan_failed", "collision_initial_local_scan_unknown",
    "collision_initial_remote_scan_failed", "collision_initial_remote_scan_unknown",
    "collision_initial_local_scan_session_changed", "collision_initial_remote_scan_session_changed",
    "collision_initial_local_scan_generation_reset", "collision_initial_remote_scan_generation_reset",
})
_QUEUE_READINESS_BOOLEANS = frozenset({"ready"})
_QUEUE_READINESS_BOOLEAN_FIELDS = frozenset({
    "accepted", "already_active", "already_dispatched", "collision_terminalizable", "collision_unresolved",
    "dispatch_attempted", "exclusions_present", "explicit_stop", "is_dir", "local_reconciled",
    "local_root_scan_proof_current", "operation_sequence_present", "path_pair_scope_configured",
    "queue_depth_known", "remote_available", "remote_reconciled", "retry_after_ambiguity",
    "stop_boundary", "transferable_remote_content", "unknown_local_overlay",
})
_QUEUE_READINESS_EXTRA_ENUMS = {
    "decision_boundary": frozenset({"scan_authority"}),
    "decision": frozenset({"defer"}),
    "collision_observation": frozenset({"absent", "not_applicable", "present", "unknown"}),
    "dispatch_mode": frozenset({"async", "sync"}),
    "local_scope_basis": frozenset({"pair", "selected_root"}),
}
_MODEL_PAGE_SNAPSHOT_OUTCOMES = frozenset({"success", "error"})
_MODEL_PAGE_SNAPSHOT_ERRORS = frozenset({"cancelled", "model_error", "runtime_error", "unknown"})
_MODEL_PAGE_SNAPSHOT_NUMBERS = frozenset({
    "scope_version", "global_version", "lock_wait_ms", "lock_hold_ms", "snapshot_elapsed_ms",
})
_MODEL_FINALIZATION_SCHEMA = "model_finalization.v2"
_MODEL_FINALIZATION_PHASES = frozenset({"entry", "complete", "error"})
_MODEL_FINALIZATION_OUTCOMES = frozenset({"started", "completed", "failed"})
_MODEL_FINALIZATION_STAGES = frozenset({
    "unknown", "update_lock_wait", "update_lock_hold", "update_lock_release",
    "model_lock_wait", "model_lock_hold", "model_lock_release", "model_build",
    "candidate_composition", "diff_construction", "diff_application", "completion_gate",
    "marker_reconciliation", "pair_adoption", "full_adoption", "final_publication",
    "summary_publication",
})
_MODEL_FINALIZATION_DURATION_MAX_MS = 86_400_000
_MODEL_FINALIZATION_DURATION_KEYS = ("wall_ms", "thread_cpu_ms", "lock_wait_ms", "lock_hold_ms")
_MODEL_FINALIZATION_BOUNDARY_MAX_NS = 2**63 - 1
_MODEL_PROGRESS_LINEAGE_SCHEMA = "model_progress_lineage_summary.v1"
_MODEL_PROGRESS_LINEAGE_MAX_VERSION_RANGES = 8
_MODEL_PROGRESS_LINEAGE_SOURCES = frozenset({
    "fresh_healthy", "fresh_unhealthy", "cached_retry", "cached_idle", "retry_empty",
    "cached_inflight", "inflight_empty", "cached_unhealthy", "unhealthy_empty",
    "cached_error", "error_empty",
})
_MODEL_PROGRESS_LINEAGE_DECISIONS = frozenset({"full_build", "active_delta", "cached"})
_MODEL_PROGRESS_LINEAGE_BUILD_KINDS = frozenset({"candidate", "full", "none"})
_MODEL_PROGRESS_LINEAGE_STATUS_COUNT_BUCKETS = frozenset({"0", "1", "2-4", "5+"})
_MODEL_PROGRESS_LINEAGE_DURATION_BUCKETS = frozenset({
    "0-4", "5-19", "20-99", "100-499", "500-1999", "2000+",
})
_MODEL_PROGRESS_LINEAGE_MISSING_PHASES = frozenset({
    "none", "status_consume", "model_mutation", "updater_decision", "multiple",
})
_MODEL_INPUT_ARBITRATION_SCHEMA = "model_input_arbitration.v1"
_MODEL_INPUT_ARBITRATION_PHASES = frozenset({"summary", "root_fill", "remote_leaf_fill", "root_source"})
_MODEL_INPUT_ARBITRATION_SUBJECTS = frozenset({"summary", "root", "remote_leaf"})
_MODEL_INPUT_ARBITRATION_SCOPES = frozenset({"root"})
_MODEL_INPUT_ARBITRATION_SELECTIONS = frozenset({"absent", "local", "active", "split", "unknown"})
_MODEL_INPUT_ARBITRATION_STATES = frozenset({"queued", "downloading", "default", "downloaded", "move_failed"})
_MODEL_INPUT_ARBITRATION_SELECTION_REASONS = frozenset({
    "nested_active_omitted", "split_merge", "local_staging_retained",
    "local_retained_over_active_staging", "active_selected", "local_selected",
    "absent", "unknown",
})
_MODEL_INPUT_ARBITRATION_SPLIT_PROVENANCE = frozenset({"split_candidate_local"})
_COMPLETION_SCAN_JOIN_SCHEMA = "completion_scan_join.v1"
_COMMAND_ADMISSION_TIMING_SCHEMA = "command_admission_timing.v1"
_COMMAND_ADMISSION_TIMING_FIELDS = (
    "handler_entry_ns", "handler_resolved_ns", "admission_attempt_ns",
    "flow_id_lock_attempt_ns", "flow_id_lock_acquired_ns",
    "flow_lock_attempt_ns", "flow_lock_acquired_ns", "queue_put_start_ns",
    "queue_put_ns", "admission_complete_ns", "work_lock_attempt_ns",
    "work_lock_acquired_ns", "queue_get_ns", "model_resolved_ns",
)
_COMPLETION_SCAN_JOIN_MODES = frozenset({"legacy", "progressive", "unknown"})
_SCAN_TICK_MODES = frozenset({"legacy", "progressive"})
_SCAN_LIFECYCLE_MESSAGES = frozenset({"scan_accumulator_before_apply", "scan_accumulator_after_apply"})
_SCAN_LIFECYCLE_COUNTERS = (
    "event_count", "full_event_count", "distinct_generation_count", "min_generation", "max_generation",
    "scanned_pair_count", "completed_pair_count", "unknown_pair_count", "root_count", "file_count",
    "monotonic_ms", "accumulator_working_root_count", "accumulator_committed_root_count",
    "accumulator_authoritative_root_count", "accumulator_touched_root_count",
)
_SCAN_TICK_BOOLEAN_KEYS = {
    "present": "p", "progress": "pr", "final": "f", "failed": "fa", "full": "fu",
    "new_authority": "na",
}
_DECIMAL_IDENTITY = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
_OPAQUE_TOKEN = re.compile(r"^[0-9a-f]{16}$")
_SCAN_SESSION_DIGEST = re.compile(r"^[0-9a-f]{12}$")
_INPUT_ARBITRATION_BUILD_TOKEN = re.compile(r"^input-arbitration:([0-9a-f]{16})$")
_QUEUE_LIFECYCLE_BOOLS = frozenset({
    "fresh", "healthy", "process_alive_before", "process_alive_after", "process_alive",
    "pre_send_drain_queue_done", "pre_send_drain_job_or_progress", "pre_send_drain_prompt_or_echo",
})
_COMPLETION_GATE_DECISIONS = frozenset({"blocked", "no_retirement", "excluded", "pending", "attempt_eligible", "deferred", "skipped", "result", "cancelled", "unknown"})
_CONFIRMED_LOCAL_DELETE_MESSAGE = "confirmed_local_delete_source"
_CONFIRMED_LOCAL_DELETE_SCHEMA = "confirmed_local_delete_source.v1"
_CONFIRMED_DELETE_FENCE_MESSAGE = "confirmed_delete_fence_disposition"
_CONFIRMED_DELETE_FENCE_SCHEMA = "confirmed_delete_fence_disposition.v1"
_CONFIRMED_DELETE_TARGET_SCOPES = frozenset({"exact_root", "nested_path"})
_CONFIRMED_DELETE_FENCE_DECISIONS = frozenset({"released", "reset"})
_CONFIRMED_DELETE_RELEASE_BASES = frozenset({"current_root", "healthy_full_snapshot", "session_reset"})
_COMPLETION_GATE_REASONS = frozenset({
    "completion_detection_not_authoritative", "still_active", "explicit_stop", "lftp_job_finished", "candidate_pair_unselected",
    "no_model_diff", "candidate_authorized", "adopted", "retry_or_failure_limit", "completion_evidence_missing",
    "completion_authority_missing", "complete_local_coverage", "not_pending", "explicitly_stopped",
    "discarded_missing_local", "terminal_state", "model_missing", "local_scope_unreconciled",
    "remote_scope_unreconciled", "poll_unhealthy", "idle_unauthoritative", "active_exact_owner",
    "active_unscoped_owner", "staging_identity_unverified", "local_coverage_incomplete",
    "move_completed", "move_already_completed", "move_deferred", "move_failed",
    "move_no_applicable", "move_conflict", "move_boundary_unavailable", "move_reservation_unavailable",
    "candidate_pair_unselected", "already_attempted", "retry_limit_reached", "retry_not_due",
    "explicit_stop", "unknown",
})
_COMPLETION_GATE_OUTCOMES = frozenset({"stopped", "deferred", "downloaded", "unknown"})
_PENDING_COMPLETION_MOVE_MESSAGES = frozenset({"pending_completion_move_decision"})
_COMPLETION_GATE_DECISION_MESSAGES = frozenset({"completion_gate_decision"})
_PENDING_COMPLETION_AUTHORITY_WAKE_MESSAGE = "pending_completion_authority_wake"
_LOCAL_ROOT_PROOF_TRANSITION_MESSAGE = "local_root_proof_transition"
_LOCAL_ROOT_PROOF_TRANSITION_SCHEMA = "local_root_proof_transition.v1"
_LOCAL_ROOT_PROOF_TRANSITION_MAX_TARGETS = 8
_LOCAL_ROOT_PROOF_TRANSITION_RELATIONS = frozenset({"match", "mismatch", "unknown", "not_checked"})
_LOCAL_ROOT_PROOF_TRANSITION_ENTRIES = frozenset({"absent", "malformed", "present", "unknown"})
_LOCAL_ROOT_PROOF_TRANSITION_WRITERS = frozenset({"set_local_files", "adopt_local_root_scan_sources", "unknown"})
_LOCAL_ROOT_PROOF_TRANSITION_ROUTES = frozenset({
    "legacy_scan_final", "progressive_pair_fallback", "progressive_joint_no_delta",
    "progressive_root_sources", "progressive_root_sources_empty", "nonprogressive_empty",
    "accumulator_missing", "source_collection_failed", "unknown",
})
_LOCAL_ROOT_PROOF_TRANSITION_STATES = frozenset({
    "completed", "failed", "pruned", "cleared", "bound", "rebound", "retained", "absent", "unknown",
})
_LOCAL_ROOT_PROOF_SHAPES = frozenset({"directory_root", "non_directory", "unknown"})
_LOCAL_ROOT_PROOF_RELATIONS = frozenset({"match", "mismatch", "unknown"})
_LOCAL_ROOT_PROOF_ENTRIES = frozenset({"absent", "malformed", "present", "unknown"})
_LOCAL_ROOT_PROOF_EXPECTED = _LOCAL_ROOT_PROOF_RELATIONS | frozenset({"not_checked"})
_PENDING_COMPLETION_MOVE_PHASES = frozenset({"authority", "move", "candidate"})
_PENDING_COMPLETION_MOVE_PROOF_SOURCES = frozenset({
    "model_lookup", "local_scan_scope", "remote_scan_scope", "lftp_status",
    "staging_remote_identity", "complete_local_coverage", "move_result", "move_reservation",
    "candidate_scope", "explicit_stop",
})
_PENDING_COMPLETION_MOVE_RESULTS = frozenset({
    "not_attempted", "completed", "already_completed", "deferred", "failed",
    "no_move_applicable", "conflict", "unknown",
})
_FINAL_MOVE_PUBLICATION_MESSAGES = frozenset({"final_move_publication"})
_FINAL_MOVE_PUBLICATION_PHASES = frozenset({
    "cleanup", "copy", "copy_complete", "copy_failure", "create_temporary",
    "fallback", "publish", "remove_source", "reserve_start", "verify",
})
_FINAL_MOVE_PUBLICATION_OPERATIONS = frozenset({
    "cleanup", "copy", "create_temporary", "fallback", "publish", "remove_source", "reserve", "verify",
})
_FINAL_MOVE_PUBLICATION_SIDES = frozenset({"destination", "destination_parent", "source", "temporary"})
_FINAL_MOVE_ERRNO_CLASSES = frozenset({
    "none", "enoent", "eexist", "permission", "enospc", "exdev", "einval", "enosys",
    "eio", "eagain", "eloop", "eisdir", "enotempty", "ecanceled", "enotsup", "unknown",
})
_FINAL_MOVE_MUTATION_STATES = frozenset({"no_mutation", "mutated", "uncertain"})
_FINAL_MOVE_EXISTS_FIELDS = frozenset({
    "temporary_created", "residue_present", "source_exists", "destination_parent_exists", "destination_exists",
})
_FINAL_MOVE_COPY_METRICS = frozenset({"copy_bytes_ns", "temporary_fsync_ns", "metadata_copy_ns"})
_FINAL_MOVE_REMOVE_SOURCE_METRICS = frozenset({
    "outer_source_recheck_ns", "source_identity_check_ns", "claim_rename_ns",
    "claim_parent_sync_ns", "claimed_identity_tree_validation_ns", "claim_delete_ns",
    "claim_final_sync_ns", "caller_parent_sync_ns",
})
_AUTOMATIC_MOVE_BOUNDARY_SCHEMA = "automatic_move_copy_boundary.v1"
_AUTOMATIC_MOVE_BOUNDARY_PHASE_DECISIONS = {
    "prepublish_gate": frozenset({"deferred", "failed", "published"}),
    "copy_yield": frozenset({"started"}),
    "copy_reacquired": frozenset({"ready", "failed"}),
}
_AUTOMATIC_MOVE_BOUNDARY_REASONS = frozenset({
    "stale_authority", "stop_or_delete_pending", "copy_failed", "published", "publication_failed",
})
_AUTOMATIC_MOVE_CHANGED_DIMENSIONS = frozenset({
    "progress_epoch", "refresh", "active_paths", "scan_publication",
    "scanner_authority", "pending_job", "published_snapshot", "candidate_file",
})
_AUTOMATIC_MOVE_CHANGED_DIMENSIONS_MAX = 8
_COMPLETION_GATE_BOOLS = frozenset({
    "poll_eligible", "poll_fresh", "poll_healthy", "previous_active", "current_active", "marker_observed", "local_scan_forced",
    "explicit_stop", "build_ran", "candidate_present", "live_present", "local_size_present", "remote_size_present",
    "complete_local_coverage", "exact_final_file_proof", "model_diff_present", "completion_proved", "physical_proof",
})
_LFTP_COMMAND_KINDS = frozenset({"get", "mirror", "pget"})
_LFTP_COMMAND_PHASES = frozenset({"submitted", "prompt_ready", "prompt_timeout", "process_eof", "backend_error"})
_LFTP_COMMAND_OUTPUTS = frozenset({"empty", "connecting", "backend_error", "other"})
_LFTP_COMMAND_COUNT_BUCKETS = frozenset({"0", "1", "2-4", "5-16", "17-64", "65-256", "257+", "unknown"})
_LFTP_COMMAND_BYTE_BUCKETS = frozenset({"0", "1-127", "128-511", "512-2047", "2048-8191", "8192-32767", "32768+", "unknown"})


def _hash(value: object) -> str:
    """Return a fixed, non-reversible correlation token."""
    if not isinstance(value, str):
        value = ""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


def _counter(value: object) -> int:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else 0


def _loss_snapshot(health: Mapping[str, object]) -> dict[str, int]:
    return {key: _counter(health.get(key)) for key in _HEALTH_COUNTERS}


def _deltas(current: Mapping[str, object], baseline: Mapping[str, int]) -> dict[str, int]:
    values: dict[str, int] = {}
    for key in _HEALTH_COUNTERS:
        now = _counter(current.get(key))
        old = baseline.get(key, 0)
        values[key] = now - old if now >= old else -1
    return values


def _numeric_values(row: Mapping[str, object], metadata: Mapping[str, object], details: Mapping[str, object]) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for source in (row, metadata, details):
        for key in _NUMERIC_KEYS:
            value = source.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if isinstance(value, float) and (not math.isfinite(value) or abs(value) > 2**63 - 1):
                continue
            if abs(value) > 2**63 - 1:
                continue
            result[key] = value
    return result


def _allowed_field(
    row: Mapping[str, object], metadata: Mapping[str, object], key: str, allowed: frozenset[str],
) -> str:
    """Read durable-envelope fields first, with bounded legacy compatibility."""
    for source in (row, metadata):
        value = source.get(key)
        if isinstance(value, str) and value in allowed:
            return value
    return "unknown"


def _queue_lifecycle_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    if details.get("schema") != "queue.lifecycle.v1":
        return None
    def fixed(key: str, allowed: frozenset[str]) -> str:
        value = details.get(key)
        return value if isinstance(value, str) and value in allowed else "unknown"

    facts: dict[str, object] = {
        "schema": "queue.lifecycle.v1",
        "event": fixed("event", _QUEUE_LIFECYCLE_EVENTS),
        "phase": fixed("phase", _QUEUE_LIFECYCLE_PHASES),
        "boundary": fixed("boundary", _QUEUE_LIFECYCLE_BOUNDARIES),
        "outcome": fixed("outcome", _QUEUE_LIFECYCLE_OUTCOMES),
        "error_class": fixed("error_class", _QUEUE_LIFECYCLE_ERRORS),
    }
    for key, allowed in _QUEUE_LIFECYCLE_ENUMS.items():
        value = details.get(key)
        if isinstance(value, str) and value in allowed:
            facts[key] = value
    for key in _QUEUE_LIFECYCLE_BOOLS:
        value = details.get(key)
        if type(value) is bool:
            facts[key] = value
    return facts


def _lftp_status_authority_facts(details: Mapping[str, object], message: object) -> dict[str, object] | None:
    if message != _LFTP_STATUS_POLL_MESSAGE or details.get("schema") != _LFTP_STATUS_AUTHORITY_SCHEMA:
        return None

    def fixed(key: str, allowed: frozenset[str]) -> str | None:
        value = details.get(key)
        if value is None:
            return None if key in details else "unknown"
        return value if isinstance(value, str) and value in allowed else "unknown"

    def boolean(key: str) -> bool | None:
        value = details.get(key)
        return value if type(value) is bool else None

    result: dict[str, object] = {
        "schema": _LFTP_STATUS_AUTHORITY_SCHEMA,
        "phase": fixed("phase", frozenset({"status_inlet", "unknown"})),
        "outcome": fixed("outcome", _LFTP_STATUS_OUTCOMES),
        "parser_outcome": fixed("parser_outcome", _LFTP_STATUS_PARSER_OUTCOMES),
        "source": fixed("source", _LFTP_STATUS_SOURCES),
        "failure_reason": fixed("failure_reason", _LFTP_STATUS_FAILURES),
    }
    for key in ("fresh", "healthy", "cache_present", "retry_active", "idle_authoritative", "poll_due"):
        result[key] = boolean(key)
    for key in (
        "status_count", "active_count", "raw_status_count", "filtered_status_count",
        "queue_dispatch_pending_count", "lftp_queue_operation_pending_count", "lftp_queue_operation_done_count",
    ):
        value = details.get(key)
        result[key] = value if type(value) is int and 0 <= value <= _LFTP_STATUS_COUNT_MAX else None
    future_state = details.get("status_future_state")
    result["status_future_state"] = (
        future_state if isinstance(future_state, str) and future_state in _LFTP_STATUS_FUTURE_STATES else
        ("unknown" if future_state is not None else None)
    )
    return result


def _lftp_status_membership_facts(details: Mapping[str, object], message: object) -> dict[str, object] | None:
    if message != _LFTP_STATUS_MEMBERSHIP_MESSAGE or details.get("schema") != _LFTP_STATUS_MEMBERSHIP_SCHEMA:
        return None

    def fixed(key: str, allowed: frozenset[str]) -> str | None:
        value = details.get(key)
        if value is None:
            return None if key in details else "unknown"
        return value if isinstance(value, str) and value in allowed else "unknown"

    def boolean(key: str) -> bool | None:
        value = details.get(key)
        return value if type(value) is bool else None

    result: dict[str, object] = {
        "schema": _LFTP_STATUS_MEMBERSHIP_SCHEMA,
        "phase": fixed("phase", frozenset({"status_membership", "unknown"})),
        "source": fixed("source", _LFTP_STATUS_SOURCES),
    }
    for key in ("fresh", "healthy", "idle_authoritative", "raw_status_count_overflow", "filtered_status_count_overflow"):
        result[key] = boolean(key)
    for key in ("chunk_index", "chunk_count", "transition_count", "raw_status_count", "filtered_status_count"):
        value = details.get(key)
        result[key] = value if type(value) is int and 0 <= value <= _LFTP_STATUS_MEMBERSHIP_COUNT_MAX else None
    digest = details.get("transition_set_digest")
    result["transition_set_digest"] = (
        digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{16}", digest) else "unknown"
    )
    return result


def _completion_gate_facts(details: Mapping[str, object], message: object = None) -> dict[str, object] | None:
    if message == _CONFIRMED_LOCAL_DELETE_MESSAGE:
        schema_known = details.get("schema") == _CONFIRMED_LOCAL_DELETE_SCHEMA
        result: dict[str, object] = {
            "boundary": "completion_gate",
            "message": _CONFIRMED_LOCAL_DELETE_MESSAGE,
            "schema": _CONFIRMED_LOCAL_DELETE_SCHEMA if schema_known else "unknown",
        }
        for key in ("delete_confirmed", "fence_result", "local_scan_binding_known", "local_generation_known"):
            value = details.get(key)
            result[key] = value if schema_known and type(value) is bool else None
        scope = details.get("target_scope")
        result["target_scope"] = (
            scope if schema_known and isinstance(scope, str) and scope in _CONFIRMED_DELETE_TARGET_SCOPES
            else ("unknown" if scope is not None or not schema_known else None)
        )
        generation = details.get("local_generation")
        result["local_generation"] = (
            str(generation) if schema_known and type(generation) is int and 0 <= generation <= 2**63 - 1
            else (None if generation is None else "unknown")
        )
        return result

    if message == _CONFIRMED_DELETE_FENCE_MESSAGE:
        schema_known = details.get("schema") == _CONFIRMED_DELETE_FENCE_SCHEMA
        result = {
            "boundary": "completion_gate",
            "message": _CONFIRMED_DELETE_FENCE_MESSAGE,
            "schema": _CONFIRMED_DELETE_FENCE_SCHEMA if schema_known else "unknown",
        }
        decision = details.get("decision")
        result["decision"] = (
            decision if schema_known and isinstance(decision, str) and decision in _CONFIRMED_DELETE_FENCE_DECISIONS
            else ("unknown" if decision is not None or not schema_known else None)
        )
        basis = details.get("release_basis")
        result["release_basis"] = (
            basis if schema_known and isinstance(basis, str) and basis in _CONFIRMED_DELETE_RELEASE_BASES
            else ("unknown" if basis is not None or not schema_known else None)
        )
        for key in (
            "captured_generation_known", "event_generation_known", "local_scan_binding_known",
            "scan_started_at_known", "root_present", "root_final_complete", "root_staging_complete", "root_is_dir",
        ):
            value = details.get(key)
            result[key] = value if schema_known and type(value) is bool else None
        for key in ("captured_generation", "event_generation", "scan_started_at_ms", "root_size"):
            value = details.get(key)
            result[key] = (
                str(value) if schema_known and type(value) is int and 0 <= value <= 2**63 - 1
                else (None if value is None else "unknown")
            )
        return result

    if isinstance(message, str) and message in _PENDING_COMPLETION_MOVE_MESSAGES:
        result: dict[str, object] = {
            "boundary": "completion_gate",
            "message": message,
            "schema": details.get("schema") if details.get("schema") == "pending_completion_move_decision.v1" else "unknown",
        }
        for key, allowed in (
            ("phase", _PENDING_COMPLETION_MOVE_PHASES),
            ("decision", _COMPLETION_GATE_DECISIONS),
            ("reason", _COMPLETION_GATE_REASONS),
            ("proof_source", _PENDING_COMPLETION_MOVE_PROOF_SOURCES),
            ("move_result", _PENDING_COMPLETION_MOVE_RESULTS),
        ):
            value = details.get("stage") if key == "phase" else details.get(key)
            result[key] = value if isinstance(value, str) and value in allowed else (None if value is None else "unknown")
        return result

    fields = {"decision", "reason", "terminal_outcome", "pending_transition", "explicit_stop"}
    if not fields.intersection(details):
        return None
    result: dict[str, object] = {"boundary": "completion_gate"}
    if isinstance(message, str) and message in _COMPLETION_GATE_DECISION_MESSAGES:
        result["message"] = message
    for key, allowed in (("decision", _COMPLETION_GATE_DECISIONS), ("reason", _COMPLETION_GATE_REASONS), ("terminal_outcome", _COMPLETION_GATE_OUTCOMES)):
        value = details.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    if message == _PENDING_COMPLETION_AUTHORITY_WAKE_MESSAGE:
        result["message"] = message
        result["schema"] = (
            "pending_completion_authority_wake.v1"
            if details.get("schema") == "pending_completion_authority_wake.v1" else "unknown"
        )
        shape = details.get("local_root_proof_shape")
        start_relation = details.get("local_root_scan_start_relation")
        checks = details.get("local_root_proof_checks")
        checks = checks if isinstance(checks, Mapping) else {}
        result["local_root_proof"] = {
            "shape": shape if isinstance(shape, str) and shape in _LOCAL_ROOT_PROOF_SHAPES else "unknown",
            "inputs_known": details.get("local_root_proof_inputs_known")
            if type(details.get("local_root_proof_inputs_known")) is bool else None,
            "checker_available": details.get("local_root_proof_checker_available")
            if type(details.get("local_root_proof_checker_available")) is bool else None,
            "result": details.get("local_root_proof_result")
            if type(details.get("local_root_proof_result")) is bool else None,
            "start_relation": start_relation
            if isinstance(start_relation, str) and start_relation in {"before", "after"} else "unknown",
            "entry": checks.get("entry")
            if isinstance(checks.get("entry"), str) and checks.get("entry") in _LOCAL_ROOT_PROOF_ENTRIES else "unknown",
            "run": checks.get("run")
            if isinstance(checks.get("run"), str) and checks.get("run") in _LOCAL_ROOT_PROOF_RELATIONS else "unknown",
            "generation": checks.get("generation")
            if isinstance(checks.get("generation"), str) and checks.get("generation") in _LOCAL_ROOT_PROOF_RELATIONS else "unknown",
            "builder": checks.get("builder")
            if isinstance(checks.get("builder"), str) and checks.get("builder") in _LOCAL_ROOT_PROOF_RELATIONS else "unknown",
            "expected": checks.get("expected")
            if isinstance(checks.get("expected"), str) and checks.get("expected") in _LOCAL_ROOT_PROOF_EXPECTED else "unknown",
        }
    value = details.get("pending_transition")
    if isinstance(value, str) and value in {"registered", "cleared", "retained"}:
        result["pending_transition"] = value
    for key in _COMPLETION_GATE_BOOLS:
        value = details.get(key)
        if type(value) is bool:
            result[key] = value
    if isinstance(message, str) and message in _COMPLETION_GATE_DECISION_MESSAGES:
        physical_proof = details.get("physical_proof")
        result["physical_proof"] = (
            physical_proof if isinstance(physical_proof, str) and physical_proof in {"proven", "missing", "unknown"}
            else (None if physical_proof is None else "unknown")
        )
        result["exact_final_file_proof"] = (
            details.get("exact_final_file_proof") if type(details.get("exact_final_file_proof")) is bool else None
        )
    return result


def _local_root_proof_transition_facts(details: Mapping[str, object], message: object = None) -> dict[str, object] | None:
    if message != _LOCAL_ROOT_PROOF_TRANSITION_MESSAGE:
        return None
    result: dict[str, object] = {
        "schema": _LOCAL_ROOT_PROOF_TRANSITION_SCHEMA if details.get("schema") == _LOCAL_ROOT_PROOF_TRANSITION_SCHEMA else "unknown",
        "root_corr": details.get("root_corr") if isinstance(details.get("root_corr"), str)
        and re.fullmatch(r"completion:[0-9a-f]{16}", details["root_corr"]) else None,
    }
    for key, allowed in (
        ("writer", _LOCAL_ROOT_PROOF_TRANSITION_WRITERS),
        ("route", _LOCAL_ROOT_PROOF_TRANSITION_ROUTES),
        ("transition", _LOCAL_ROOT_PROOF_TRANSITION_STATES),
        ("before_proof", _LOCAL_ROOT_PROOF_TRANSITION_ENTRIES),
        ("after_proof", _LOCAL_ROOT_PROOF_TRANSITION_ENTRIES),
        ("before_source", _LOCAL_ROOT_PROOF_TRANSITION_RELATIONS),
        ("after_source", _LOCAL_ROOT_PROOF_TRANSITION_RELATIONS),
        ("before_expected", _LOCAL_ROOT_PROOF_TRANSITION_RELATIONS),
        ("after_expected", _LOCAL_ROOT_PROOF_TRANSITION_RELATIONS),
        ("coverage", frozenset({"complete", "partial", "omitted", "unknown"})),
        ("input_presence", frozenset({"present", "absent", "invalid", "not_applicable", "unknown"})),
        ("outcome", frozenset({"completed", "failed", "unknown"})),
    ):
        value = details.get(key)
        result[key] = value if isinstance(value, str) and value in allowed else "unknown"
    for key in ("selected_count", "omitted_count", "target_index"):
        value = details.get(key)
        maximum = _LOCAL_ROOT_PROOF_TRANSITION_MAX_TARGETS - 1 if key == "target_index" else 2 ** 31 - 1
        result[key] = value if type(value) is int and 0 <= value <= maximum else None
    return result


def _final_move_publication_facts(details: Mapping[str, object], message: object) -> dict[str, object] | None:
    if not isinstance(message, str) or message not in _FINAL_MOVE_PUBLICATION_MESSAGES:
        return None
    result: dict[str, object] = {"message": message}
    for key, allowed in (
        ("phase", _FINAL_MOVE_PUBLICATION_PHASES),
        ("operation", _FINAL_MOVE_PUBLICATION_OPERATIONS),
        ("side", _FINAL_MOVE_PUBLICATION_SIDES),
        ("errno_class", _FINAL_MOVE_ERRNO_CLASSES),
        ("mutation_state", _FINAL_MOVE_MUTATION_STATES),
    ):
        value = details.get(key)
        result[key] = value if isinstance(value, str) and value in allowed else (None if value is None else "unknown")
    for key in _FINAL_MOVE_EXISTS_FIELDS:
        value = details.get(key)
        result[key] = value if type(value) is bool else None
    for key in _FINAL_MOVE_COPY_METRICS:
        value = details.get(key)
        if type(value) is int and 0 <= value < 2**63:
            result[key] = value
    for key in _FINAL_MOVE_REMOVE_SOURCE_METRICS:
        value = details.get(key)
        if key in details:
            result[key] = value if value is None or (type(value) is int and 0 <= value < 2**63) else None
    return result


def _automatic_move_copy_boundary_facts(
    message: object, details: Mapping[str, object],
) -> dict[str, object] | None:
    """Project the finalized, finite automatic-move copy boundary envelope."""
    if message != "automatic_move_copy_boundary" or details.get("schema") != _AUTOMATIC_MOVE_BOUNDARY_SCHEMA:
        return None
    phase = details.get("phase")
    decision = details.get("decision")
    allowed_decisions = _AUTOMATIC_MOVE_BOUNDARY_PHASE_DECISIONS.get(phase) if isinstance(phase, str) else None
    if allowed_decisions is None or not isinstance(decision, str) or decision not in allowed_decisions:
        return None
    result: dict[str, object] = {
        "schema": _AUTOMATIC_MOVE_BOUNDARY_SCHEMA,
        "phase": phase,
        "decision": decision,
    }
    reason = details.get("reason")
    if isinstance(reason, str) and reason in _AUTOMATIC_MOVE_BOUNDARY_REASONS:
        result["reason"] = reason
    duration = details.get("duration_ms")
    if phase == "copy_reacquired" and type(duration) is int and 0 <= duration <= 600_000:
        result["duration_ms"] = duration
    dimensions = details.get("changed_dimensions")
    if reason == "stale_authority" and isinstance(dimensions, list):
        bounded: list[str] = []
        for item in dimensions:
            if isinstance(item, str) and item in _AUTOMATIC_MOVE_CHANGED_DIMENSIONS and item not in bounded:
                bounded.append(item)
                if len(bounded) >= _AUTOMATIC_MOVE_CHANGED_DIMENSIONS_MAX:
                    break
        result["changed_dimensions"] = bounded
    return result


def _lftp_command_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    if details.get("schema") != "lftp.command_boundary.v1":
        return None
    result: dict[str, object] = {"schema": "lftp.command_boundary.v1"}
    for key, allowed in (("transfer_kind", _LFTP_COMMAND_KINDS), ("phase", _LFTP_COMMAND_PHASES), ("output_class", _LFTP_COMMAND_OUTPUTS), ("argument_count_bucket", _LFTP_COMMAND_COUNT_BUCKETS), ("exclude_count_bucket", _LFTP_COMMAND_COUNT_BUCKETS), ("submission_byte_length_bucket", _LFTP_COMMAND_BYTE_BUCKETS)):
        value = details.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    if type(details.get("process_alive")) is bool:
        result["process_alive"] = details["process_alive"]
    return result


def _delete_local_command_facts(
    row: Mapping[str, object], metadata: Mapping[str, object], details: Mapping[str, object],
) -> dict[str, object] | None:
    """Project only bounded Delete Local outcome facts from command breadcrumbs."""
    if _allowed_field(row, metadata, "category", _CATEGORIES) != "controller":
        return None
    if _allowed_field(row, metadata, "stage", _STAGES) != "command":
        return None
    message = row.get("message", metadata.get("message"))
    if not isinstance(message, str) or message not in {"command_dispatched", "command_finished", "command_failed"}:
        return None
    facts: dict[str, object] = {}
    for key, minimum in (
        ("delete_local_planned_location_count", 0),
        ("delete_local_removed_location_count", 0),
    ):
        if key not in details:
            continue
        value = details[key]
        if value is None or (type(value) is int and minimum <= value <= 2):
            facts[key] = value
    value = details.get("delete_local_result_known")
    if type(value) is bool:
        facts["delete_local_result_known"] = value
    value = details.get("delete_local_partial_failure")
    if value is None or type(value) is bool:
        if "delete_local_partial_failure" in details:
            facts["delete_local_partial_failure"] = value
    return facts or None


def _queue_readiness_facts(message: object, details: Mapping[str, object]) -> dict[str, object] | None:
    """Project only the finite, source-shaped Queue readiness discriminator."""
    schema = details.get("schema")
    # The maintained producer writes the event discriminator in row.message;
    # details are the finite v1 payload and are not authoritative for it.
    if schema not in _QUEUE_READINESS_SCHEMAS or message not in _QUEUE_READINESS_EVENTS:
        return None
    event = message
    result: dict[str, object] = {"schema": schema, "event": event}
    for key, allowed in (
        ("phase", _QUEUE_READINESS_PHASES),
        ("outcome", _QUEUE_READINESS_OUTCOMES),
        ("origin", _QUEUE_READINESS_ORIGINS),
        ("reason", _QUEUE_READINESS_REASONS),
    ):
        value = details.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
        else:
            result[key] = "unknown"
    for key in _QUEUE_READINESS_BOOLEANS:
        value = details.get(key)
        if type(value) is bool:
            result[key] = value
    for key, allowed in _QUEUE_READINESS_EXTRA_ENUMS.items():
        value = details.get(key)
        if key in details:
            result[key] = value if isinstance(value, str) and value in allowed else "unknown"
    for key in _QUEUE_READINESS_BOOLEAN_FIELDS:
        value = details.get(key)
        if key in details:
            result[key] = value if type(value) is bool else None
    for key in ("callback_index", "callback_count", "error_code", "queue_depth"):
        value = details.get(key)
        if type(value) is int and 0 <= value <= 2**63 - 1:
            result[key] = value
        elif key in details:
            result[key] = None
    for key in ("publication_id", "model_version"):
        value = details.get(key)
        if key in details:
            result[key] = value if type(value) is int and 0 <= value <= 2**31 - 1 else None
    return result


def _model_page_snapshot_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    """Project the maintained model-page diagnostic's finite source fields."""
    outcome = details.get("outcome")
    if not isinstance(outcome, str) or outcome not in _MODEL_PAGE_SNAPSHOT_OUTCOMES:
        return None
    result: dict[str, object] = {"outcome": outcome}
    if outcome == "error":
        error_class = details.get("error_class")
        result["error_class"] = error_class if isinstance(error_class, str) and error_class in _MODEL_PAGE_SNAPSHOT_ERRORS else "unknown"
    for key in _MODEL_PAGE_SNAPSHOT_NUMBERS:
        value = details.get(key)
        if type(value) is int and 0 <= value <= 2**31 - 1:
            result[key] = value
    return result


def _model_finalization_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    """Project the updater's fixed, bounded model-finalization span schema."""
    if details.get("schema") != _MODEL_FINALIZATION_SCHEMA:
        return None
    phase = details.get("phase")
    stage = details.get("stage")
    outcome = details.get("outcome")
    if not (
        isinstance(phase, str) and phase in _MODEL_FINALIZATION_PHASES
        and isinstance(stage, str) and stage in _MODEL_FINALIZATION_STAGES
        and isinstance(outcome, str) and outcome in _MODEL_FINALIZATION_OUTCOMES
    ):
        return None
    result: dict[str, object] = {
        "schema": _MODEL_FINALIZATION_SCHEMA,
        "phase": phase,
        "stage": stage,
        "outcome": outcome,
    }
    for key in _MODEL_FINALIZATION_DURATION_KEYS:
        value = details.get(key)
        result[key] = value if type(value) is int and 0 <= value <= _MODEL_FINALIZATION_DURATION_MAX_MS else None
    boundary_ns = details.get("boundary_monotonic_ns")
    result["boundary_monotonic_ns"] = (
        str(boundary_ns)
        if type(boundary_ns) is int and 0 <= boundary_ns <= _MODEL_FINALIZATION_BOUNDARY_MAX_NS
        else None
    )
    return result


def _model_progress_lineage_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    """Project the fixed durable updater-lineage summary vocabulary."""
    if details.get("schema") != _MODEL_PROGRESS_LINEAGE_SCHEMA:
        return None
    missing_phase = details.get("missing_phase")
    if not isinstance(missing_phase, str) or missing_phase not in _MODEL_PROGRESS_LINEAGE_MISSING_PHASES:
        return None
    result: dict[str, object] = {
        "schema": _MODEL_PROGRESS_LINEAGE_SCHEMA,
        "missing_phase": missing_phase,
    }

    status = details.get("status_consume")
    if status is not None:
        if not isinstance(status, Mapping):
            return None
        projected_status: dict[str, object] = {}
        source = status.get("source")
        if isinstance(source, str) and source in _MODEL_PROGRESS_LINEAGE_SOURCES:
            projected_status["source"] = source
        else:
            return None
        for key in ("fresh", "healthy"):
            value = status.get(key)
            if type(value) is bool:
                projected_status[key] = value
            else:
                return None
        monotonic_ms = status.get("monotonic_ms")
        if type(monotonic_ms) is not int or monotonic_ms < 0 or monotonic_ms > 2**63 - 1:
            return None
        projected_status["monotonic_ms"] = monotonic_ms
        result["status_consume"] = projected_status

    mutation = details.get("model_mutation")
    if mutation is not None:
        if not isinstance(mutation, Mapping):
            return None
        projected_mutation: dict[str, object] = {}
        for key in ("model_version_first", "model_version_last", "scope_version", "monotonic_ms"):
            value = mutation.get(key)
            if type(value) is not int or value < 0 or value > 2**63 - 1:
                return None
            projected_mutation[key] = value
        ranges = mutation.get("mutation_version_ranges")
        if ranges is not None:
            if not isinstance(ranges, list) or len(ranges) > _MODEL_PROGRESS_LINEAGE_MAX_VERSION_RANGES:
                return None
            projected_ranges: list[list[int]] = []
            for version_range in ranges:
                if not isinstance(version_range, list) or len(version_range) != 2:
                    return None
                start, end = version_range
                if type(start) is not int or type(end) is not int or not 0 <= start <= end <= 2**63 - 1:
                    return None
                projected_ranges.append([start, end])
            projected_mutation["mutation_version_ranges"] = projected_ranges
        truncated = mutation.get("mutation_ranges_truncated")
        if truncated is not None:
            if type(truncated) is not bool:
                return None
            projected_mutation["mutation_ranges_truncated"] = truncated
        omitted_count = mutation.get("mutation_ranges_omitted_count")
        if omitted_count is not None:
            if type(omitted_count) is not int or not 0 <= omitted_count <= 2_147_483_647:
                return None
            projected_mutation["mutation_ranges_omitted_count"] = omitted_count
        result["model_mutation"] = projected_mutation

    decision = details.get("updater_decision")
    if decision is not None:
        if not isinstance(decision, Mapping):
            return None
        projected_decision: dict[str, object] = {}
        value = decision.get("decision")
        if value is not None:
            if not isinstance(value, str) or value not in _MODEL_PROGRESS_LINEAGE_DECISIONS:
                return None
            projected_decision["decision"] = value
        value = decision.get("build_kind")
        if value is not None:
            if not isinstance(value, str) or value not in _MODEL_PROGRESS_LINEAGE_BUILD_KINDS:
                return None
            projected_decision["build_kind"] = value
        value = decision.get("status_count_bucket")
        if value is not None:
            if not isinstance(value, str) or value not in _MODEL_PROGRESS_LINEAGE_STATUS_COUNT_BUCKETS:
                return None
            projected_decision["status_count_bucket"] = value
        value = decision.get("updater_cycle_duration_bucket")
        if value is not None:
            if not isinstance(value, str) or value not in _MODEL_PROGRESS_LINEAGE_DURATION_BUCKETS:
                return None
            projected_decision["updater_cycle_duration_bucket"] = value
        monotonic_ms = decision.get("monotonic_ms")
        if type(monotonic_ms) is not int or monotonic_ms < 0 or monotonic_ms > 2**63 - 1:
            return None
        projected_decision["monotonic_ms"] = monotonic_ms
        result["updater_decision"] = projected_decision
    return result


def _input_arbitration_facts(row: Mapping[str, object], metadata: Mapping[str, object],
                             details: Mapping[str, object]) -> dict[str, object] | None:
    """Project the producer's capped, finalized input-arbitration envelope."""
    if details.get("schema") != _MODEL_INPUT_ARBITRATION_SCHEMA:
        return None
    source_inputs = details.get("source_inputs")
    source_inputs = source_inputs if isinstance(source_inputs, Mapping) else details
    selection_guards = details.get("selection_guards")
    selection_guards = selection_guards if isinstance(selection_guards, Mapping) else details
    split_candidate = details.get("split_candidate")
    split_candidate = split_candidate if isinstance(split_candidate, Mapping) else details

    def compact_key(source: Mapping[str, object], *keys: str) -> str:
        return next((key for key in keys if key in source), keys[-1])

    def fixed(key: str, allowed: frozenset[str]) -> str:
        value = details.get(key)
        return value if isinstance(value, str) and value in allowed else "unknown"

    def optional_fixed(key: str, allowed: frozenset[str]) -> str | None:
        if key not in details:
            return "unknown"
        value = details[key]
        if value is None:
            return None
        return value if isinstance(value, str) and value in allowed else "unknown"

    def opaque(key: str) -> str:
        value = details.get(key)
        return value if isinstance(value, str) and _OPAQUE_TOKEN.fullmatch(value) else "unknown"

    def integer(key: str, source: Mapping[str, object] | None = None) -> int | None | str:
        source = details if source is None else source
        if key not in source:
            return "unknown"
        value = source[key]
        if value is None:
            return None
        return value if type(value) is int and 0 <= value <= 2**63 - 1 else "unknown"

    def boolean(key: str, source: Mapping[str, object] | None = None) -> bool | None | str:
        source = details if source is None else source
        if key not in source:
            return "unknown"
        value = source[key]
        if value is None:
            return None
        return value if type(value) is bool else "unknown"

    def decimal_identity(key: str, source: Mapping[str, object], raw_key: str | None = None) -> str | None:
        raw_key = key if raw_key is None else raw_key
        """Keep wide nanosecond identities exact through JSON projection."""
        if raw_key not in source:
            return "unknown"
        value = source[raw_key]
        if value is None:
            return None
        if not isinstance(value, str) or not _DECIMAL_IDENTITY.fullmatch(value):
            return "unknown"
        return value if int(value) <= 2**63 - 1 else "unknown"

    corr_id = row.get("corr_id", metadata.get("corr_id"))
    flow_id = row.get("flow_id", metadata.get("flow_id"))
    trace_scope = row.get("trace_scope", metadata.get("trace_scope"))
    match = _INPUT_ARBITRATION_BUILD_TOKEN.fullmatch(corr_id) if isinstance(corr_id, str) else None
    build_token = match.group(1) if match and flow_id == corr_id and trace_scope == "flow" else "unknown"

    result: dict[str, object] = {
        "schema": _MODEL_INPUT_ARBITRATION_SCHEMA,
        "phase": fixed("phase", _MODEL_INPUT_ARBITRATION_PHASES),
        "subject_kind": fixed("subject_kind", _MODEL_INPUT_ARBITRATION_SUBJECTS),
        "build_token": build_token,
        "root_id": opaque("root_id"),
        "subject_id": opaque("subject_id"),
        "input_scope": fixed("input_scope", _MODEL_INPUT_ARBITRATION_SCOPES),
        "selection": fixed("selection", _MODEL_INPUT_ARBITRATION_SELECTIONS),
        "selection_reason": optional_fixed("selection_reason", _MODEL_INPUT_ARBITRATION_SELECTION_REASONS),
    }
    for key in ("root_rows_sampled", "root_rows_omitted", "remote_leaf_rows_sampled", "remote_leaf_rows_omitted"):
        value = integer(key)
        result[key] = value
    result["local_proof_generation"] = integer("local_proof_generation")
    for key, compact in (("local_input_size", "lb"), ("active_input_size", "ab"),
                         ("remote_input_size", "rb")):
        result[key] = integer(compact_key(source_inputs, compact, key), source_inputs)
    for key in ("filled_local_size", "filled_remote_size", "filled_transferred_size"):
        result[key] = integer(key)
    for key, compact in (("local_input_present", "lp"), ("local_input_is_staging", "ls"),
                         ("active_input_present", "ap"), ("active_input_is_staging", "as"),
                         ("remote_input_present", "rp"), ("remote_input_is_dir", "rd")):
        result[key] = boolean(compact_key(source_inputs, compact, key), source_inputs)
    for key in ("local_proof_generation_known", "active_generation_known", "filled_local_present",
                "filled_remote_present", "filled_remote_transferable"):
        result[key] = boolean(key)
    for key, compact, previous_nested in (
        ("local_staging_descendant", "sd", "staging_descendant"),
        ("local_staging_collision_descendant", "cd", "collision_descendant"),
        ("split_has_verified_leaf", "sv", "verified_split_leaf"),
        ("local_candidate_eligible", "lce", "local_candidate_eligible"),
        ("local_size_ge_active", "sga", "size_ge_active"),
        ("remote_indicates_newer", "rn", "remote_newer"),
    ):
        result[key] = boolean(compact_key(selection_guards, compact, previous_nested, key), selection_guards)

    split_boolean_fields = {
        "split_candidate_local_present": "local_present",
        "split_candidate_local_mtime_known": "local_mtime_known",
        "split_candidate_remote_mtime_known": "remote_mtime_known",
        "split_candidate_strict_verified": "strict_verified",
        "split_candidate_portable_queue_exclusion_match": "portable_queue_exclusion_match",
    }
    for output_key, nested_key in split_boolean_fields.items():
        raw_key = nested_key if split_candidate is not details else output_key
        result[output_key] = boolean(raw_key, split_candidate)
    for output_key, nested_key in (("split_candidate_local_size", "local_size"),
                                   ("split_candidate_remote_size", "remote_size")):
        raw_key = nested_key if split_candidate is not details else output_key
        result[output_key] = integer(raw_key, split_candidate)
    for output_key, nested_key in (("split_candidate_local_mtime_ns", "local_mtime_ns"),
                                   ("split_candidate_remote_mtime_ns", "remote_mtime_ns")):
        raw_key = nested_key if split_candidate is not details else output_key
        result[output_key] = decimal_identity(output_key, split_candidate, raw_key)
    provenance_key = "provenance" if split_candidate is not details else "split_candidate_provenance"
    if provenance_key not in split_candidate:
        result["split_candidate_provenance"] = "unknown"
    elif split_candidate[provenance_key] is None:
        result["split_candidate_provenance"] = None
    else:
        provenance = split_candidate[provenance_key]
        result["split_candidate_provenance"] = (
            provenance if isinstance(provenance, str) and provenance in _MODEL_INPUT_ARBITRATION_SPLIT_PROVENANCE
            else "unknown"
        )
    state = details.get("filled_state")
    result["filled_state"] = state if isinstance(state, str) and state in _MODEL_INPUT_ARBITRATION_STATES else "unknown"
    if result["phase"] == "root_source":
        provenance = details.get("root_source")
        provenance = provenance if isinstance(provenance, Mapping) else details.get("p")
        provenance = provenance if isinstance(provenance, Mapping) else {}

        def nested_opaque(key: str) -> str | None:
            if key not in provenance:
                return "unknown"
            value = provenance[key]
            if value is None:
                return None
            return value if isinstance(value, str) and _OPAQUE_TOKEN.fullmatch(value) else "unknown"

        result["local_root_object_digest"] = (
            nested_opaque("lo") if "lo" in provenance else nested_opaque("local_root_object_digest")
        )
        result["local_proof_session_digest"] = (
            nested_opaque("ps") if "ps" in provenance else nested_opaque("local_proof_session_digest")
        )
        proof_bound_key = "pb" if "pb" in provenance else "local_proof_object_bound"
        result["local_proof_object_bound"] = boolean(proof_bound_key, provenance)
    return result


def _completion_scan_join_facts(details: Mapping[str, object], message: object) -> dict[str, object] | None:
    if details.get("schema") != _COMPLETION_SCAN_JOIN_SCHEMA or message != "scan_root_adopted":
        return None
    payload = details.get("p")
    payload = payload if isinstance(payload, Mapping) else {}

    def lookup(compact: str, canonical: str) -> str:
        return compact if compact in payload else canonical

    def opaque(compact: str, canonical: str) -> str | None:
        key = lookup(compact, canonical)
        if key not in payload:
            return "unknown"
        value = payload[key]
        if value is None:
            return None
        return value if isinstance(value, str) and _OPAQUE_TOKEN.fullmatch(value) else "unknown"

    def boolean(compact: str, canonical: str) -> bool | None | str:
        key = lookup(compact, canonical)
        if key not in payload:
            return "unknown"
        value = payload[key]
        return value if value is None or type(value) is bool else "unknown"

    mode = payload.get("m", payload.get("local_scan_mode"))
    return {
        "schema": _COMPLETION_SCAN_JOIN_SCHEMA,
        "local_scan_mode": mode if isinstance(mode, str) and mode in _COMPLETION_SCAN_JOIN_MODES else "unknown",
        "scan_source_root_object_digest": opaque("so", "scan_source_root_object_digest"),
        "accepted_root_object_digest": opaque("ao", "accepted_root_object_digest"),
        "source_pair_scanned": boolean("sp", "source_pair_scanned"),
        "source_pair_completed": boolean("cp", "source_pair_completed"),
        "source_pair_unknown": boolean("up", "source_pair_unknown"),
        "source_pair_targeted": boolean("tp", "source_pair_targeted"),
        "target_pair_covered": boolean("tc", "target_pair_covered"),
    }


def _local_scan_tick_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    payload = details.get("local_scan_tick")
    if not isinstance(payload, Mapping):
        return None

    def boolean(key: str) -> bool | None | str:
        if key not in payload:
            return "unknown"
        value = payload[key]
        return value if value is None or type(value) is bool else "unknown"

    mode = payload.get("m")
    session = payload.get("s")
    generation = payload.get("g")
    result: dict[str, object] = {
        "present": boolean("p"),
        "mode": mode if isinstance(mode, str) and mode in _SCAN_TICK_MODES else "unknown",
        "session_digest": (
            "unknown" if "s" not in payload else
            session if session is None or (isinstance(session, str) and _SCAN_SESSION_DIGEST.fullmatch(session)) else "unknown"
        ),
        "generation": (
            "unknown" if "g" not in payload else
            generation if generation is None or (type(generation) is int and 0 <= generation <= 2**63 - 1)
            else "unknown"
        ),
    }
    for field, compact in _SCAN_TICK_BOOLEAN_KEYS.items():
        if field == "present":
            continue
        result[field] = boolean(compact)
    return result


def _scan_lifecycle_facts(
        details: Mapping[str, object], message: object, corr_id: object,
) -> dict[str, object] | None:
    if not isinstance(message, str) or message not in _SCAN_LIFECYCLE_MESSAGES:
        return None
    result: dict[str, object] = {"message": message}
    side = details.get("scanner_side")
    result["scanner_side"] = side if isinstance(side, str) and side in {"local", "remote"} else "unknown"
    session_match = (
        re.fullmatch(r"(?:local|remote):([0-9a-f]{12})", corr_id)
        if result["scanner_side"] in {"local", "remote"} and isinstance(corr_id, str) else None
    )
    if session_match is not None and corr_id.startswith(result["scanner_side"] + ":"):
        result["session_digest"] = session_match.group(1)
    else:
        result["session_digest"] = "unknown"
    for key in _SCAN_LIFECYCLE_COUNTERS:
        value = details.get(key)
        result[key] = (
            "unknown" if key not in details else
            value if value is None or (type(value) is int and 0 <= value <= 2**63 - 1) else "unknown"
        )
    for key in ("targeted", "final", "full", "progress", "failed", "handoff_root_present"):
        if key not in details:
            result[key] = "unknown"
        else:
            value = details[key]
            result[key] = value if value is None or type(value) is bool else "unknown"
    fence = details.get("fence")
    result["fence"] = fence if fence == "none" else "unknown"
    return result


def select_input_arbitration_rows(records: object, root_id: object) -> dict[str, object]:
    """Select app-emitted root rows and their same-build leaf/summary rows.

    ``root_id`` must be the opaque token read from a finalized app record.  An
    empty result is deliberately ``unknown``: it cannot prove the root was
    absent from an incomplete or lossy capture.
    """
    if not isinstance(root_id, str) or not _OPAQUE_TOKEN.fullmatch(root_id) or not isinstance(records, list):
        return {"state": "unknown", "build_tokens": [], "records": []}
    token_rows: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        facts = record.get("input_arbitration")
        if not isinstance(facts, Mapping) or facts.get("root_id") != root_id:
            continue
        token = facts.get("build_token")
        if isinstance(token, str) and _OPAQUE_TOKEN.fullmatch(token):
            token_rows.add(token)
    if not token_rows:
        return {"state": "unknown", "build_tokens": [], "records": []}
    selected = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        facts = record.get("input_arbitration")
        if not isinstance(facts, Mapping) or facts.get("build_token") not in token_rows:
            continue
        if facts.get("root_id") == root_id or facts.get("subject_kind") == "summary":
            selected.append(record)
    return {"state": "selected", "build_tokens": sorted(token_rows), "records": selected}


def _command_admission_timing_facts(details: Mapping[str, object]) -> dict[str, object] | None:
    if "admission_timing" not in details:
        return None
    timing = details.get("admission_timing")
    timing = timing if isinstance(timing, Mapping) else {}
    schema_known = timing.get("schema") == _COMMAND_ADMISSION_TIMING_SCHEMA
    correlation = timing.get("correlation")
    facts: dict[str, object] = {
        "schema": _COMMAND_ADMISSION_TIMING_SCHEMA if schema_known else "unknown",
        "correlation": (
            correlation
            if isinstance(correlation, str) and len(correlation) == 16 and re.fullmatch(r"[0-9a-f]{16}", correlation)
            else "unknown"
        ),
    }
    for key in _COMMAND_ADMISSION_TIMING_FIELDS:
        item = timing.get(key)
        facts[key] = (
            str(item)
            if schema_known and type(item) is int and 0 <= item <= 2**63 - 1
            else None
        )
    return facts


def _projection(row: Mapping[str, object]) -> tuple[dict[str, object] | None, bool]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    details = row.get("details") if isinstance(row.get("details"), Mapping) else {}
    assert isinstance(metadata, Mapping) and isinstance(details, Mapping)
    category = _allowed_field(row, metadata, "category", _CATEGORIES)
    if category == "unknown":
        return None, True
    source = _allowed_field(row, metadata, "source", _SOURCES)
    event_type = _allowed_field(row, metadata, "event_type", _EVENT_TYPES)
    stage = _allowed_field(row, metadata, "stage", _STAGES)
    level = _allowed_field(row, metadata, "level", _LEVELS)
    message = row.get("message", metadata.get("message"))
    numbers = _numeric_values(row, metadata, details)
    lifecycle = _queue_lifecycle_facts(details) if category == "queue.lifecycle" else None
    lftp_status_authority = (
        _lftp_status_authority_facts(details, message)
        if category == "transfer.lftp.status" else None
    )
    lftp_status_membership = (
        _lftp_status_membership_facts(details, message)
        if category == "transfer.lftp.membership" else None
    )
    gate = _completion_gate_facts(details, message) if category == "completion.gate" else None
    proof_transition = (
        _local_root_proof_transition_facts(details, message)
        if category == "scan.root_shape" else None
    )
    final_move = (
        _final_move_publication_facts(details, message) if category == "final_move.publication" else None
    )
    automatic_move_boundary = (
        _automatic_move_copy_boundary_facts(message, details)
        if category == "completion.gate" else None
    )
    command = _lftp_command_facts(details) if category == "transfer.lftp.command" else None
    delete_local = _delete_local_command_facts(row, metadata, details)
    readiness = _queue_readiness_facts(row.get("message"), details) if category == "queue.readiness" else None
    model_page_snapshot = _model_page_snapshot_facts(details) if category == "model_page_snapshot" else None
    model_finalization = _model_finalization_facts(details) if category == "model.finalization" else None
    model_progress_lineage = _model_progress_lineage_facts(details) if category == "model.progress" else None
    command_admission_timing = (
        _command_admission_timing_facts(details)
        if source == "controller" and category == "controller" and stage == "command" else None
    )
    input_arbitration = _input_arbitration_facts(row, metadata, details)
    scan_join = (
        _completion_scan_join_facts(details, message)
        if category == "scan.authority" and message == "scan_root_adopted" else None
    )
    has_local_scan_tick = category == "scan.adoption" or (
        category == "scan.authority" and details.get("schema") == _MODEL_INPUT_ARBITRATION_SCHEMA
        and details.get("phase") == "root_source"
    )
    local_scan_tick = _local_scan_tick_facts(details) if has_local_scan_tick else None
    scan_lifecycle = _scan_lifecycle_facts(details, message, row.get("corr_id")) if category == "scan.lifecycle" else None
    if category == "final_move.publication" and isinstance(message, str) and message in _FINAL_MOVE_PUBLICATION_MESSAGES and final_move is None:
        return None, True
    if category == "queue.readiness" and readiness is None:
        return None, True
    if category == "model_page_snapshot" and model_page_snapshot is None:
        return None, True
    if category == "model.finalization" and model_finalization is None:
        return None, True
    if details.get("schema") == _MODEL_PROGRESS_LINEAGE_SCHEMA and model_progress_lineage is None:
        return None, True
    if details.get("schema") == _MODEL_INPUT_ARBITRATION_SCHEMA and (
        category != "scan.authority" or row.get("message", metadata.get("message")) != "input_arbitration_witness"
    ):
        return None, True
    if details.get("schema") == _COMPLETION_SCAN_JOIN_SCHEMA and (
        category != "scan.authority" or message != "scan_root_adopted" or scan_join is None
    ):
        return None, True
    lifecycle_event = lifecycle.get("event") if lifecycle else None
    if event_type == "completion" or stage == "terminal" or lifecycle_event in {"queue_retired", "operation_retired"} or details.get("final") is True or details.get("completed") is True:
        kind = "completion"
    elif category == "model.progress" or event_type == "progress" or lifecycle_event == "status_membership" or any(key in numbers for key in ("progress", "percent", "processed")):
        kind = "progress"
    elif category in {"status", "transfer.lftp.status", "lftp.status"} or event_type == "status":
        kind = "status"
    else:
        kind = "event"
    result = {
        "kind": kind,
        "source": source,
        "category": category,
        "event_type": event_type,
        "stage": stage,
        "level": level,
        "numbers": numbers,
    }
    if lifecycle is not None:
        result["lifecycle"] = lifecycle
    if lftp_status_authority is not None:
        result["lftp_status_authority"] = lftp_status_authority
    if lftp_status_membership is not None:
        result["lftp_status_membership"] = lftp_status_membership
    if gate is not None:
        result["completion_gate"] = gate
    if proof_transition is not None:
        result["local_root_proof_transition"] = proof_transition
    if final_move is not None:
        result["final_move_publication"] = final_move
    if automatic_move_boundary is not None:
        result["automatic_move_copy_boundary"] = automatic_move_boundary
    if command is not None:
        result["lftp_command"] = command
    if delete_local is not None:
        result.update(delete_local)
    if readiness is not None:
        result["queue_readiness"] = readiness
    if model_page_snapshot is not None:
        result["model_page_snapshot"] = model_page_snapshot
    if model_finalization is not None:
        result["model_finalization"] = model_finalization
    if model_progress_lineage is not None:
        result["model_progress_lineage"] = model_progress_lineage
    if command_admission_timing is not None:
        result["command_admission_timing"] = command_admission_timing
    if input_arbitration is not None:
        result["input_arbitration"] = input_arbitration
    if scan_join is not None:
        result["scan_join"] = scan_join
    if local_scan_tick is not None:
        result["local_scan_tick"] = local_scan_tick
    if scan_lifecycle is not None:
        result["scan_lifecycle"] = scan_lifecycle
    return result, False


@dataclass
class _FileState:
    fd: int
    dev: int
    ino: int
    name: str
    cursor: int
    tail: bytes
    active: bool
    last_seen: int


class BreadcrumbSpoolCapture:
    """Read a bounded, typed projection from an already verified spool FD."""

    def __init__(
        self,
        spool: int | str | os.PathLike[str],
        sink: JsonArtifactSink,
        *,
        max_bytes: int = 131072,
        max_millis: int = 250,
        max_records: int = 128,
        max_tail_bytes: int = 65536,
        max_files: int = 32,
        max_output_bytes: int = 262144,
    ) -> None:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
            raise ObserverCaptureError("capture byte bound is invalid")
        if type(max_millis) is not int or not 1 <= max_millis <= 2000:
            raise ObserverCaptureError("capture time bound is invalid")
        if type(max_records) is not int or not 4 <= max_records <= 4096:
            raise ObserverCaptureError("capture record bound is invalid")
        if type(max_tail_bytes) is not int or not 1 <= max_tail_bytes <= 65536:
            raise ObserverCaptureError("capture partial-line bound is invalid")
        if type(max_files) is not int or not 2 <= max_files <= 256:
            raise ObserverCaptureError("capture file bound is invalid")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 4 * 1024 * 1024:
            raise ObserverCaptureError("capture output bound is invalid")
        self.sink = sink
        self.max_bytes = max_bytes
        self.max_millis = max_millis
        self.max_records = max_records
        self.max_tail_bytes = max_tail_bytes
        self.max_files = max_files
        self.max_output_bytes = max_output_bytes
        self._directory_fd = self._take_directory_fd(spool)
        self._owns_directory_fd = True
        self._files: OrderedDict[tuple[int, int], _FileState] = OrderedDict()
        self._session: str | None = None
        self._health_baseline: dict[str, int] = {}
        self._last_health: dict[str, object] = {}
        self._active_key: tuple[int, int] | None = None
        self._armed = False
        self._terminal = False
        self._poll_index = 0
        self._emitted_records = 0
        self._emitted_bytes = 0
        self._source_bytes = 0
        self._unsupported_records = 0
        self._loss_reason: str | None = None
        self._loss_count = 0

    @classmethod
    def from_verified_directory_fd(cls, directory_fd: int, sink: JsonArtifactSink, **kwargs: object) -> "BreadcrumbSpoolCapture":
        return cls(directory_fd, sink, **kwargs)

    @staticmethod
    def _take_directory_fd(spool: int | str | os.PathLike[str]) -> int:
        if isinstance(spool, int):
            try:
                info = os.fstat(spool)
            except OSError as exc:
                raise ObserverCaptureError("verified spool directory FD is unavailable") from exc
            if not stat.S_ISDIR(info.st_mode):
                raise ObserverCaptureError("verified spool directory FD is not a directory")
            try:
                return os.dup(spool)
            except OSError as exc:
                raise ObserverCaptureError("verified spool directory FD cannot be held") from exc
        path = Path(spool)
        try:
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ObserverCaptureError("spool path is not a real directory")
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            after = os.fstat(descriptor)
            if (int(before.st_dev), int(before.st_ino)) != (int(after.st_dev), int(after.st_ino)):
                os.close(descriptor)
                raise ObserverCaptureError("spool directory changed while opening")
            return descriptor
        except ObserverCaptureError:
            raise
        except OSError as exc:
            raise ObserverCaptureError("spool directory cannot be opened safely") from exc

    def close(self) -> None:
        for state in list(self._files.values()):
            try:
                os.close(state.fd)
            except OSError:
                pass
        self._files.clear()
        if self._owns_directory_fd:
            try:
                os.close(self._directory_fd)
            except OSError:
                pass
            self._owns_directory_fd = False

    def __enter__(self) -> "BreadcrumbSpoolCapture":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _marker_present(self) -> bool:
        try:
            info = os.stat(_MARKER_NAME, dir_fd=self._directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ObserverCaptureError("spool marker cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ObserverCaptureError("spool marker is unsafe")
        return True

    def _read_health(self) -> dict[str, object]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(_HEALTH_NAME, flags, dir_fd=self._directory_fd)
        except OSError as exc:
            raise ObserverCaptureError("spool health is unavailable") from exc
        try:
            info = os.fstat(fd)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ObserverCaptureError("spool health is unsafe")
            if info.st_size > _MAX_HEALTH_BYTES:
                raise ObserverCaptureError("spool health is oversized")
            chunks: list[bytes] = []
            remaining = int(info.st_size)
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            try:
                value = json.loads(b"".join(chunks).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ObserverCaptureError("spool health is unreadable") from exc
        except ObserverCaptureError:
            raise
        except OSError as exc:
            raise ObserverCaptureError("spool health is unreadable") from exc
        finally:
            os.close(fd)
        if not isinstance(value, dict) or value.get("schema") != "breadcrumb_durable_health.v1":
            raise ObserverCaptureError("spool health schema is invalid")
        session = value.get("session_id")
        if not isinstance(session, str) or not session:
            raise ObserverCaptureError("spool session is invalid")
        if value.get("offline_retrieval") != "valid" or value.get("session_boundary_written") is not True:
            raise ObserverCaptureError("spool health is not armable")
        if value.get("health_publish_ok") is not True:
            raise ObserverCaptureError("spool health publication is invalid")
        for key in (*_HEALTH_COUNTERS, "rotation_count"):
            counter = value.get(key)
            if type(counter) is not int or not 0 <= counter <= 2**63 - 1:
                raise ObserverCaptureError("spool health counter is invalid")
        return value

    def _inventory(self) -> dict[str, tuple[int, int, int]]:
        result: dict[str, tuple[int, int, int]] = {}
        try:
            entries = os.scandir(self._directory_fd)
        except OSError as exc:
            raise ObserverCaptureError("spool directory cannot be inventoried") from exc
        try:
            for entry in entries:
                name = entry.name
                if name != _ACTIVE_NAME and _ROTATED_NAME.fullmatch(name) is None:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ObserverCaptureError("spool file cannot be inspected") from exc
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise ObserverCaptureError("spool file is not a regular file")
                result[name] = (int(info.st_dev), int(info.st_ino), int(info.st_size))
        finally:
            entries.close()
        return result

    def _open_file(self, name: str, *, cursor: int, active: bool) -> _FileState:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = -1
        try:
            fd = os.open(name, flags, dir_fd=self._directory_fd)
            info = os.fstat(fd)
        except OSError as exc:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise ObserverCaptureError("spool file cannot be opened") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise ObserverCaptureError("spool file is not a regular file")
        if cursor < 0 or cursor > int(info.st_size):
            os.close(fd)
            raise ObserverCaptureError("spool cursor is invalid")
        key = (int(info.st_dev), int(info.st_ino))
        return _FileState(fd, key[0], key[1], name, cursor, b"", active, self._poll_index)

    def _close_file(self, key: tuple[int, int]) -> None:
        state = self._files.pop(key, None)
        if state is not None:
            try:
                os.close(state.fd)
            except OSError:
                pass

    def _emit(self, state: str, *, cursor_start: int = 0, cursor_end: int = 0, bytes_read: int = 0,
              records: int = 0, reason: str | None = None, projection: Mapping[str, object] | None = None,
              health: Mapping[str, object] | None = None, budget: Mapping[str, object] | None = None,
              **extra: object) -> bool:
        if self._terminal and state != "stopped":
            return False
        current_health = health or {}
        delta = _deltas(current_health, self._health_baseline)
        capture: dict[str, object] = {
            "state": state,
            "utc_ms": int(time.time_ns() // 1_000_000),
            "monotonic_ms": int(time.monotonic() * 1000),
            "cursor_start": max(0, int(cursor_start)),
            "cursor_end": max(0, int(cursor_end)),
            "bytes_read": max(0, int(bytes_read)),
            "records": max(0, int(records)),
            "lost": max(0, delta.get("lost", 0)),
            "health_lost": max(0, delta.get("lost", 0)),
            "health_unknown": max(0, delta.get("unknown", 0)),
            "health_unresolved": max(0, delta.get("unresolved", 0)),
            "session_hash": _hash(self._session),
            "rotation_count": _counter(current_health.get("rotation_count")),
        }
        if reason is not None:
            capture["reason"] = reason
        if projection is not None:
            capture["projection"] = dict(projection)
        if budget is not None:
            capture["budget"] = dict(budget)
        capture.update(extra)
        artifact = {"schema": "breadcrumb-spool-capture.v1", "event": "spool", "phase": state, "outcome": "success", "capture": capture}
        encoded_size = len(json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        # Leave room for one terminal receipt.  A minimal loss/terminal receipt
        # is still attempted when the ordinary output budget is exhausted.
        if state not in {"loss", "stopped"} and (
            self._emitted_records >= self.max_records - 2 or self._emitted_bytes + encoded_size > self.max_output_bytes
        ):
            return False
        try:
            self.sink.persist(artifact)
        except Exception as exc:
            raise ObserverCaptureError("capture artifact cannot be persisted") from exc
        self._emitted_records += 1
        self._emitted_bytes += encoded_size
        if state == "stopped":
            self._terminal = True
        return True

    def _loss(self, reason: str, health: Mapping[str, object], **fields: object) -> None:
        self._loss_reason = reason
        self._loss_count += 1
        self._emit("loss", reason=reason, health=health, **fields)
        self._terminal = True

    def start(self) -> None:
        if self._armed:
            raise ObserverCaptureError("capture is already armed")
        if self._marker_present():
            raise ObserverCaptureError("spool marker is present")
        h0 = self._read_health()
        i0 = self._inventory()
        if _ACTIVE_NAME not in i0:
            raise ObserverCaptureError("active spool file is missing")
        if len(i0) > self.max_files:
            raise ObserverCaptureError("spool file inventory exceeds capture FD bound")
        opened: list[_FileState] = []
        try:
            for name in sorted(i0, key=lambda item: (item != _ACTIVE_NAME, item)):
                dev, ino, size = i0[name]
                state = self._open_file(name, cursor=size, active=name == _ACTIVE_NAME)
                if (state.dev, state.ino) != (dev, ino):
                    raise ObserverCaptureError("spool file changed while arming")
                opened.append(state)
            # The descriptor, rather than a pathname stat, defines the arm
            # boundary.  Recheck EOF after all opens so rows written while the
            # inventory was being acquired remain pre-arm data.
            for state in opened:
                size = int(os.fstat(state.fd).st_size)
                if size < state.cursor:
                    raise ObserverCaptureError("spool file changed while arming")
                state.cursor = size
            h1 = self._read_health()
            i1 = self._inventory()
            if self._marker_present() or h1.get("session_id") != h0.get("session_id") or _loss_snapshot(h1) != _loss_snapshot(h0):
                raise ObserverCaptureError("spool health changed while arming")
            if set(i1) != set(i0) or any(i1[name][:2] != i0[name][:2] for name in i0):
                raise ObserverCaptureError("spool inventory changed while arming")
            self._files = OrderedDict(((state.dev, state.ino), state) for state in opened)
            self._active_key = next(key for key, state in self._files.items() if state.active)
            self._session = str(h1["session_id"])
            self._health_baseline = _loss_snapshot(h1)
            self._last_health = dict(h1)
            self._armed = True
            self._emit("armed", health=h1, budget=self._budget_snapshot())
        except Exception:
            for state in opened:
                try:
                    os.close(state.fd)
                except OSError:
                    pass
            raise

    def _budget_snapshot(self, *, bytes_read: int = 0, elapsed_ms: int = 0) -> dict[str, object]:
        return {
            "max_bytes": self.max_bytes,
            "max_millis": self.max_millis,
            "max_records": self.max_records,
            "max_output_bytes": self.max_output_bytes,
            "bytes_read": max(0, bytes_read),
            "elapsed_ms": max(0, elapsed_ms),
        }

    def _discover(self, inventory: Mapping[str, tuple[int, int, int]], health: Mapping[str, object]) -> None:
        active_info = inventory.get(_ACTIVE_NAME)
        if active_info is None:
            self._loss("missing", health)
            raise ObserverCaptureError("active spool file disappeared")
        current_active = (active_info[0], active_info[1])
        known_keys = set(self._files)
        # Existing descriptors are authoritative even after rename.  Refresh
        # their display names without reopening a path.
        for name, (dev, ino, _size) in inventory.items():
            key = (dev, ino)
            state = self._files.get(key)
            if state is not None:
                state.name = name
                state.active = name == _ACTIVE_NAME
                state.last_seen = self._poll_index
        if current_active not in known_keys:
            if self._active_key is None or self._active_key not in self._files:
                self._loss("unknown", health)
                raise ObserverCaptureError("new active has no known predecessor")
            # The previous active must still be visible under an app-owned
            # rotated name.  A vanished descriptor or an arbitrary rename is
            # not enough evidence to call the replacement an unambiguous
            # rotation, so never begin such a file at offset zero.
            if self._active_key not in {(dev, ino) for dev, ino, _size in inventory.values()}:
                self._loss("unknown", health)
                raise ObserverCaptureError("new active rotation is ambiguous")
            unknown_rotated = [
                name for name, (dev, ino, _size) in inventory.items()
                if name != _ACTIVE_NAME and (dev, ino) not in known_keys
            ]
            if unknown_rotated:
                self._loss("unseen_rotation", health)
                raise ObserverCaptureError("rotated spool file was not observed")
            state = self._open_file(_ACTIVE_NAME, cursor=0, active=True)
            self._files[(state.dev, state.ino)] = state
        self._active_key = current_active
        # A new rotated path that was not held from arm/previous poll cannot be
        # attributed without risking a gap, even if its name looks app-owned.
        for name, (dev, ino, _size) in inventory.items():
            if name != _ACTIVE_NAME and (dev, ino) not in self._files:
                self._loss("unseen_rotation", health)
                raise ObserverCaptureError("unseen rotated spool file")
        self._prune(health)

    def _prune(self, health: Mapping[str, object]) -> None:
        if len(self._files) <= self.max_files:
            return
        candidates = sorted(
            (state for key, state in self._files.items() if key != self._active_key),
            key=lambda item: item.last_seen,
        )
        for state in candidates:
            try:
                size = int(os.fstat(state.fd).st_size)
            except OSError as exc:
                self._loss("unread", health)
                raise ObserverCaptureError("held spool file cannot be inspected") from exc
            if state.cursor < size or state.tail:
                self._loss("prune", health)
                raise ObserverCaptureError("file cap would prune unread spool data")
            self._close_file((state.dev, state.ino))
            if len(self._files) <= self.max_files:
                break

    def _read_state(self, state: _FileState, remaining: int, started: float, health: Mapping[str, object]) -> tuple[int, int, int, bool]:
        try:
            info = os.fstat(state.fd)
        except OSError as exc:
            self._loss("unread", health)
            raise ObserverCaptureError("held spool file cannot be inspected") from exc
        if not stat.S_ISREG(info.st_mode):
            self._loss("unknown", health)
            raise ObserverCaptureError("held spool descriptor changed type")
        size = int(info.st_size)
        if size < state.cursor:
            self._loss("cursor", health, cursor_start=state.cursor, cursor_end=size)
            raise ObserverCaptureError("spool file was truncated")
        available = size - state.cursor
        if available <= 0:
            state.last_seen = self._poll_index
            return 0, 0, 0, False
        if remaining <= 0 or (time.monotonic() - started) * 1000 >= self.max_millis:
            return 0, 0, 0, True
        # Keep one descriptor read/parse unit small enough that the monotonic
        # poll deadline remains meaningful even when a single file is large.
        amount = min(available, remaining, _READ_CHUNK_BYTES)
        try:
            data = os.pread(state.fd, amount, state.cursor)
        except (AttributeError, OSError) as exc:
            self._loss("unread", health)
            raise ObserverCaptureError("spool file cannot be read") from exc
        if not data and amount:
            self._loss("unread", health)
            raise ObserverCaptureError("spool file read made no progress")
        start = state.cursor - len(state.tail)
        state.cursor += len(data)
        combined = state.tail + data
        chunks = combined.split(b"\n")
        tail = chunks.pop() if chunks else b""
        if len(tail) > self.max_tail_bytes:
            self._loss("oversized", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data))
            raise ObserverCaptureError("partial breadcrumb exceeds bound")
        state.tail = tail
        state.last_seen = self._poll_index
        parsed = emitted = irrelevant = 0
        for raw in chunks:
            line = raw[:-1] if raw.endswith(b"\r") else raw
            if not line:
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                self._loss("malformed", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data))
                raise ObserverCaptureError("malformed breadcrumb row")
            if not isinstance(value, Mapping):
                self._loss("malformed", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data))
                raise ObserverCaptureError("breadcrumb row is not an object")
            typed, is_irrelevant = _projection(value)
            if is_irrelevant or typed is None:
                irrelevant += 1
                continue
            metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
            details = value.get("details") if isinstance(value.get("details"), Mapping) else {}
            assert isinstance(metadata, Mapping) and isinstance(details, Mapping)
            projection = dict(typed)
            projection.update(
                {
                    "flow_hash": _hash(value.get("flow_id", metadata.get("flow_id"))),
                    "corr_hash": _hash(value.get("corr_id", value.get("correlation_id", metadata.get("corr_id", metadata.get("correlation_id"))))),
                    "file_hash": _hash(value.get("file_id", metadata.get("file_id"))),
                    "pair_hash": _hash(value.get("path_pair_id", metadata.get("path_pair_id"))),
                }
            )
            if not self._emit(
                "record", cursor_start=start, cursor_end=state.cursor, bytes_read=len(data), records=1,
                health=health, projection=projection,
                created_ms=_counter(value.get("created_ms")),
                created_ns=_counter(value.get("created_ns")),
            ):
                self._loss("output", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data), records=1)
                raise ObserverCaptureError("capture output cap refused a parsed row")
            parsed += 1
            emitted += 1
        if irrelevant:
            self._unsupported_records += irrelevant
            if not self._emit(
                "unsupported", cursor_start=start, cursor_end=state.cursor, bytes_read=len(data), records=irrelevant,
                reason="unsupported", health=health, irrelevant=irrelevant,
            ):
                self._loss("output", health, cursor_start=start, cursor_end=state.cursor, bytes_read=len(data), records=irrelevant)
                raise ObserverCaptureError("capture output cap refused unsupported rows")
        return len(data), emitted, irrelevant, False

    def poll(self) -> int:
        if not self._armed:
            raise ObserverCaptureError("capture is not armed")
        if self._terminal:
            return 0
        self._poll_index += 1
        started = time.monotonic()
        try:
            h0 = self._read_health()
        except ObserverCaptureError:
            self._loss("unread", self._last_health)
            raise
        try:
            marker_before = self._marker_present()
        except ObserverCaptureError:
            self._loss("marker", h0)
            raise
        if marker_before:
            self._loss("marker", h0)
            raise ObserverCaptureError("spool marker appeared")
        if h0.get("session_id") != self._session:
            self._loss("session", h0)
            raise ObserverCaptureError("spool session changed")
        deltas = _deltas(h0, self._health_baseline)
        monitored = {"lost", "unknown", "unresolved", "writer_failures", "flush_failures", "partial_write_failures", "health_publish_failures", "retention_prune_failures"}
        if any(value < 0 for value in deltas.values()) or any(value > 0 for key, value in deltas.items() if key in monitored):
            self._loss("health", h0)
            raise ObserverCaptureError("spool health reports new loss")
        self._last_health = dict(h0)
        try:
            inventory = self._inventory()
            self._discover(inventory, h0)
        except ObserverCaptureError:
            if not self._terminal:
                self._loss("unknown", h0)
            raise
        total = emitted = 0
        budget_hit = False
        # A single read is deliberately capped at 8 KiB, but a poll must keep
        # draining until its configured byte/time budget is reached.  Without
        # this loop a nominal 256 KiB poll silently became an 8 KiB poll.
        for state in list(self._files.values()):
            while True:
                remaining = self.max_bytes - total
                read, count, _irrelevant, hit = self._read_state(state, remaining, started, h0)
                total += read
                self._source_bytes += read
                emitted += count
                budget_hit = budget_hit or hit
                if (
                    budget_hit
                    or total >= self.max_bytes
                    or (time.monotonic() - started) * 1000 >= self.max_millis
                    or read == 0
                ):
                    if total >= self.max_bytes or (time.monotonic() - started) * 1000 >= self.max_millis:
                        budget_hit = True
                    break
        try:
            h1 = self._read_health()
        except ObserverCaptureError:
            self._loss("unread", self._last_health, bytes_read=total)
            raise
        try:
            marker_after = self._marker_present()
        except ObserverCaptureError:
            self._loss("marker", h1, bytes_read=total)
            raise
        if marker_after or h1.get("session_id") != self._session:
            self._loss("marker" if marker_after else "session", h1, bytes_read=total)
            raise ObserverCaptureError("spool changed while reading")
        post_deltas = _deltas(h1, self._health_baseline)
        if any(value < 0 for value in post_deltas.values()) or any(value > 0 for key, value in post_deltas.items() if key in monitored):
            self._loss("health", h1, bytes_read=total)
            raise ObserverCaptureError("spool health reports new loss")
        self._last_health = dict(h1)
        if budget_hit:
            elapsed = int((time.monotonic() - started) * 1000)
            self._emit(
                "unsupported", reason="budget", bytes_read=total, records=emitted, health=h1,
                budget=self._budget_snapshot(bytes_read=total, elapsed_ms=elapsed),
            )
        return emitted

    def projection(self) -> dict[str, object]:
        """Return bounded reader state without exposing source labels or paths."""
        return {
            "armed": self._armed,
            "terminal": self._terminal,
            "session_hash": _hash(self._session),
            "polls": self._poll_index,
            "records": self._emitted_records,
            "bytes": self._emitted_bytes,
            "source_bytes": self._source_bytes,
            "unsupported": self._unsupported_records,
            "discarded": self._unsupported_records,
            "loss": {"count": self._loss_count, "reason": self._loss_reason},
            "budget": self._budget_snapshot(),
            "health": {
                key: _counter(self._last_health.get(key))
                for key in ("lost", "unknown", "unresolved", "rotation_count")
            },
        }

    def stop(self, *, health: Mapping[str, object] | None = None) -> None:
        if self._terminal:
            self.close()
            return
        if not self._armed:
            self.close()
            return
        # ``health`` remains a source-compatible argument for old callers but
        # is intentionally ignored.  A caller-supplied snapshot cannot prove
        # the marker/session/loss/state boundary at terminal publication.
        del health
        try:
            final_health = self._read_health()
        except ObserverCaptureError:
            self._loss("unread", self._last_health)
            self.close()
            raise
        try:
            marker = self._marker_present()
        except ObserverCaptureError:
            self._loss("marker", final_health)
            self.close()
            raise
        if marker:
            self._loss("marker", final_health)
            self.close()
            raise ObserverCaptureError("spool marker appeared while stopping")
        if final_health.get("session_id") != self._session:
            self._loss("session", final_health)
            self.close()
            raise ObserverCaptureError("spool session changed while stopping")
        deltas = _deltas(final_health, self._health_baseline)
        monitored = {"lost", "unknown", "unresolved", "writer_failures", "flush_failures", "partial_write_failures", "health_publish_failures", "retention_prune_failures"}
        if any(value < 0 for value in deltas.values()) or any(value > 0 for key, value in deltas.items() if key in monitored):
            self._loss("health", final_health)
            self.close()
            raise ObserverCaptureError("spool health reports new loss while stopping")
        if final_health.get("state") not in {"RUNNING", "TERMINAL"}:
            self._loss("unknown", final_health)
            self.close()
            raise ObserverCaptureError("spool health state is invalid while stopping")
        for state in self._files.values():
            try:
                size = int(os.fstat(state.fd).st_size)
            except OSError as exc:
                self._loss("unread", final_health)
                self.close()
                raise ObserverCaptureError("spool data cannot be inspected while stopping") from exc
            if state.cursor < size:
                self._loss("unread", final_health, cursor_start=state.cursor, cursor_end=size)
                self.close()
                raise ObserverCaptureError("spool data remains unread while stopping")
            if state.tail:
                self._loss("partial", final_health, cursor_start=max(0, state.cursor - len(state.tail)), cursor_end=state.cursor)
                self.close()
                raise ObserverCaptureError("partial spool data remains while stopping")
        self._last_health = dict(final_health)
        try:
            self._emit("stopped", health=final_health, budget=self._budget_snapshot())
        finally:
            self.close()
