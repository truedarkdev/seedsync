from pathlib import Path
import importlib.util
from io import BytesIO
import json
import os
import sys
import threading
from urllib.error import HTTPError
import pytest

SPEC = importlib.util.spec_from_file_location(
    "incoming_recovery_observer",
    Path(__file__).parents[1] / "incoming_recovery_observer.py",
)
observer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = observer
assert SPEC.loader is not None
SPEC.loader.exec_module(observer)

CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "breadcrumb_spool_capture",
    Path(__file__).parents[1] / "breadcrumb_spool_capture.py",
)
capture = importlib.util.module_from_spec(CAPTURE_SPEC)
sys.modules[CAPTURE_SPEC.name] = capture
assert CAPTURE_SPEC.loader is not None
CAPTURE_SPEC.loader.exec_module(capture)

for _parent in Path(__file__).resolve().parents:
    _common_root = _parent / "src/python"
    if _common_root.is_dir():
        sys.path.insert(0, str(_common_root))
        break
try:
    from common.breadcrumb_trace import BreadcrumbTraceCollector
    from system import SystemFile
    from controller import ModelBuilder
except ImportError:
    BreadcrumbTraceCollector = None
    SystemFile = None
    ModelBuilder = None

RUNNER_SPEC = importlib.util.spec_from_file_location(
    "incoming_live_queue_runner", Path(__file__).parents[1] / "incoming_live_queue_runner.py")
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
assert RUNNER_SPEC.loader is not None
RUNNER_SPEC.loader.exec_module(runner)

PAIR = "pair-1"
ROOT = '["pair-1","Incoming"]'


def responses():
    return {
        "/server/path-pairs": {"success": True, "data": [{"id": PAIR, "name": "Pr0n", "auto_queue": False}]},
        "/server/config/get": {"autoqueue": {"enabled": False}},
        "/server/model/v1/summary": {"scan_authority": {
            "publication_id": 5, "model_version": 8,
            "local_scan_generation": 3, "remote_scan_generation": 4,
            "scanned_pair_count": 6, "completed_pair_count": 6, "unknown_pair_count": 0,
            "final": True, "joint_final": True,
            "joint_authoritative": True, "joint_authoritative_after": True,
            "outcome": "no_op", "reason": "comparison_proven_noop",
        }},
        "/server/model/v1/pairs/pair-1/roots?limit=200": {"records": [{"file_id": ROOT, "name": "Incoming", "state": "default"}]},
        "/server/status": {"controller": {"latest_local_scan_time": "1"}},
    }


def test_preflight_uses_real_envelopes_and_never_sends_queue():
    calls = []
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    evidence = gate.preflight(lambda path: calls.append(path) or responses()[path])
    assert evidence["root_found"] is True
    assert evidence["scan_lifecycle"]["local_scan_generation"] == 3
    assert gate.passed is True
    assert all(not path.startswith("/server/command/queue/") for path in calls)


def test_wrong_path_pair_envelope_blocks_queue_and_serializes_no_sink():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    sent = []
    bad = responses()
    bad["/server/path-pairs"] = {"success": True, "data": {"id": PAIR}}
    with pytest.raises(observer.ObserverSchemaError, match="data must be a list"):
        gate.preflight(lambda path: bad[path])
    with pytest.raises(observer.ObserverSchemaError, match="blocked"):
        gate.queue(sent.append)
    assert sent == []


def test_queue_http_error_records_sanitized_reason_and_scan_lifecycle():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    gate.preflight(lambda path: responses()[path])

    def send(_path):
        raise HTTPError(
            "http://local/server/command/queue/hidden", 409, "Conflict", None,
            BytesIO(b"Queue preflight cancelled: initial_scan_authority_deadline"),
        )

    with pytest.raises(HTTPError):
        gate.queue(send)

    assert gate.last_queue_evidence == {
        "schema": "incoming-recovery-queue-response.v1",
        "status_code": 409,
        "body_reason": "initial_scan_authority_deadline",
        "scan_lifecycle": {
            "schema": "incoming-recovery-scan-lifecycle.v1",
            "publication_id": 5, "model_version": 8,
            "local_scan_generation": 3, "remote_scan_generation": 4,
            "scanned_pair_count": 6, "completed_pair_count": 6, "unknown_pair_count": 0,
            "final": True, "joint_final": True,
            "joint_authoritative": True, "joint_authoritative_after": True,
            "outcome": "no_op", "reason": "comparison_proven_noop",
        },
    }


def test_queue_http_error_never_retains_unrecognized_body_text():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    gate.preflight(lambda path: responses()[path])

    with pytest.raises(HTTPError):
        gate.queue(lambda _path: (_ for _ in ()).throw(HTTPError(
            "http://local", 409, "Conflict", None, BytesIO(b"Bearer private-value"),
        )))

    assert gate.last_queue_evidence is not None
    assert gate.last_queue_evidence["body_reason"] == "unrecognized_409_response"
    assert "private-value" not in str(gate.last_queue_evidence)
    assert observer.error_artifact(observer.ObserverSchemaError("x"))["schema"] == "incoming-recovery-observer-error.v1"


def test_observer_allowlists_response_and_scan_authority_enums():
    body = b"Queue preflight cancelled: Bearer_private_value"
    assert observer.queue_response_evidence(409, body)["body_reason"] == "preflight_cancelled_unrecognized"
    assert observer.queue_response_evidence(409, b"Queue preflight cancelled: path_pair_refresh")["body_reason"] == "path_pair_refresh"

    summary = responses()["/server/model/v1/summary"]
    summary["scan_authority"].update({"outcome": ["operator_identity"], "reason": {"secret": "private-value"}})
    evidence = observer.scan_authority_evidence(summary)
    assert evidence["outcome"] == "unknown"
    assert evidence["reason"] == "unknown"
    assert "private-value" not in str(evidence)


def test_input_arbitration_projection_is_finite_and_preserves_unknowns(tmp_path):
    projection = {
        "kind": "event", "source": "model_builder", "category": "scan.authority",
        "event_type": "info", "stage": "model_build", "level": "info", "numbers": {},
        "input_arbitration": {
            "schema": "model_input_arbitration.v1", "phase": "root_fill", "subject_kind": "root",
            "build_token": "0123456789abcdef", "root_id": "fedcba9876543210",
            "subject_id": "fedcba9876543210", "input_scope": "root", "selection": "active",
            "root_rows_sampled": 2, "root_rows_omitted": 0,
            "remote_leaf_rows_sampled": 1, "remote_leaf_rows_omitted": 0,
            "local_proof_generation": None, "local_input_size": 0, "active_input_size": 5,
            "remote_input_size": 8, "filled_local_size": 0, "filled_remote_size": 8,
            "filled_transferred_size": None, "local_proof_generation_known": False,
            "active_generation_known": True, "local_input_present": True, "local_input_is_staging": False,
            "selection_reason": "active_selected", "local_staging_descendant": True,
            "local_staging_collision_descendant": None, "split_has_verified_leaf": False,
            "local_candidate_eligible": True, "local_size_ge_active": True, "remote_indicates_newer": True,
            "split_candidate_local_present": True, "split_candidate_local_size": 1072,
            "split_candidate_local_mtime_ns": "1790873610000000000",
            "split_candidate_local_mtime_known": True, "split_candidate_remote_size": 1072,
            "split_candidate_remote_mtime_ns": "1790873610500000000",
            "split_candidate_remote_mtime_known": True, "split_candidate_strict_verified": False,
            "split_candidate_portable_queue_exclusion_match": True,
            "split_candidate_provenance": "split_candidate_local",
            "active_input_present": True, "active_input_is_staging": False, "remote_input_present": True,
            "remote_input_is_dir": True, "filled_local_present": True, "filled_remote_present": True,
            "filled_remote_transferable": "unknown", "filled_state": "unknown",
            "private_path": "/private/should-not-survive",
        },
    }
    sink = observer.JsonArtifactSink(tmp_path / "input-arbitration.jsonl")
    sink.persist({
        "schema": "incoming-recovery-observer-sample.v1", "event": "capture", "outcome": "success",
        "capture": {"state": "record", "source": "controller", "projection": projection},
    })
    record = json.loads((tmp_path / "input-arbitration.jsonl").read_text(encoding="utf-8"))
    safe = record["capture"]["projection"]["input_arbitration"]
    assert safe["build_token"] == "0123456789abcdef"
    assert safe["root_id"] == safe["subject_id"] == "fedcba9876543210"
    assert safe["local_input_size"] == 0 and safe["local_proof_generation"] is None
    assert safe["selection_reason"] == "active_selected"
    assert safe["local_staging_descendant"] is True and safe["local_staging_collision_descendant"] is None
    assert safe["split_candidate_local_mtime_ns"] == "1790873610000000000"
    assert int(safe["split_candidate_local_mtime_ns"]) > 2**53
    assert safe["split_candidate_provenance"] == "split_candidate_local"
    assert safe["filled_remote_transferable"] == safe["filled_state"] == "unknown"
    assert "private_path" not in safe

    # New finalized envelopes nest source, selection, and split-leaf facts.
    # The sink sanitizer normalizes only the allowlisted keys and keeps wide
    # raw mtime identities as decimal strings.
    canonical = dict(projection)
    canonical_facts = dict(projection["input_arbitration"])
    for key in (
        "local_input_present", "local_input_size", "local_input_is_staging",
        "active_input_present", "active_input_size", "active_input_is_staging",
        "remote_input_present", "remote_input_size", "remote_input_is_dir",
    ):
        canonical_facts.pop(key, None)
    for key in (
        "local_staging_descendant", "local_staging_collision_descendant", "split_has_verified_leaf",
        "local_candidate_eligible", "local_size_ge_active", "remote_indicates_newer",
    ):
        canonical_facts.pop(key, None)
    for key in (
        "split_candidate_local_present", "split_candidate_local_size", "split_candidate_local_mtime_ns",
        "split_candidate_local_mtime_known", "split_candidate_remote_size", "split_candidate_remote_mtime_ns",
        "split_candidate_remote_mtime_known", "split_candidate_strict_verified",
        "split_candidate_portable_queue_exclusion_match", "split_candidate_provenance",
    ):
        canonical_facts.pop(key, None)
    canonical_facts.update({
        "source_inputs": {
            "lp": True, "lb": 0, "ls": False,
            "ap": True, "ab": 5, "as": False,
            "rp": True, "rb": 8, "rd": True,
            "private_path": "/private/drop",
        },
        "selection_guards": {
            "sd": True, "cd": None,
            "sv": False, "lce": True,
            "sga": True, "rn": True,
            "private_flag": True,
        },
        "split_candidate": {
            "local_present": True, "local_size": 1072, "local_mtime_ns": "1790873610000000000",
            "local_mtime_known": True, "remote_size": 1072, "remote_mtime_ns": "1790873610500000000",
            "remote_mtime_known": True, "strict_verified": False,
            "portable_queue_exclusion_match": True, "provenance": "split_candidate_local",
            "private_path": "/private/drop",
        },
    })
    canonical["input_arbitration"] = canonical_facts
    canonical_safe = observer._sanitize_capture_projection(canonical)["input_arbitration"]
    assert canonical_safe["local_input_size"] == 0
    assert canonical_safe["local_candidate_eligible"] is True
    assert canonical_safe["local_staging_collision_descendant"] is None
    assert canonical_safe["split_candidate_local_mtime_ns"] == "1790873610000000000"
    assert int(canonical_safe["split_candidate_local_mtime_ns"]) > 2**53
    assert canonical_safe["split_candidate_provenance"] == "split_candidate_local"
    assert "private_path" not in json.dumps(canonical_safe)

    # Preserve the immediately preceding long nested envelope as well.
    previous_nested = dict(canonical)
    previous_facts = dict(canonical_facts)
    previous_facts["source_inputs"] = {
        "local_input_present": True, "local_input_size": 0, "local_input_is_staging": False,
        "active_input_present": True, "active_input_size": 5, "active_input_is_staging": False,
        "remote_input_present": True, "remote_input_size": 8, "remote_input_is_dir": True,
    }
    previous_facts["selection_guards"] = {
        "staging_descendant": True, "collision_descendant": None, "verified_split_leaf": False,
        "local_candidate_eligible": True, "size_ge_active": True, "remote_newer": True,
    }
    previous_nested["input_arbitration"] = previous_facts
    previous_safe = observer._sanitize_capture_projection(previous_nested)["input_arbitration"]
    assert previous_safe["local_input_size"] == 0
    assert previous_safe["local_staging_collision_descendant"] is None
    assert previous_safe["remote_indicates_newer"] is True

    finalized_like = {
        "source": "model_builder", "category": "scan.authority", "level": "info",
        "stage": "model_builder_input_arbitration", "event_type": "diagnostic",
        "message": "input_arbitration_witness", "corr_id": "input-arbitration:0123456789abcdef",
        "flow_id": "input-arbitration:0123456789abcdef", "trace_scope": "flow",
        "version": 42, "created_ms": 100,
        "details": canonical_facts,
    }
    projected, irrelevant = capture._projection(finalized_like)
    assert not irrelevant and projected is not None
    capture_sink = observer.JsonArtifactSink(tmp_path / "nested-consumer-sink.jsonl")
    capture_sink.persist({
        "schema": "incoming-recovery-observer-sample.v1", "event": "capture", "outcome": "success",
        "capture": {"state": "record", "source": "controller", "projection": projected},
    })
    saved = json.loads((tmp_path / "nested-consumer-sink.jsonl").read_text(encoding="utf-8"))
    saved_facts = saved["capture"]["projection"]["input_arbitration"]
    assert saved_facts["build_token"] == "0123456789abcdef"
    assert saved_facts["selection_reason"] == "active_selected"
    assert saved_facts["split_candidate_local_mtime_ns"] == "1790873610000000000"
    assert "private_path" not in json.dumps(saved_facts)

    malformed = dict(projection)
    malformed["input_arbitration"] = {
        **projection["input_arbitration"],
        "root_id": "/private/path", "build_token": "not-an-app-token",
        "local_input_size": True, "remote_input_present": 1,
        "selection_reason": "/private/path", "local_staging_descendant": 1,
        "split_candidate_local_mtime_ns": 1790873610000000000,
        "split_candidate_remote_mtime_ns": "01790873610500000000",
        "split_candidate_provenance": {"private": "value"},
        "filled_state": {"private": "value"},
    }
    bounded = observer._sanitize_capture_projection(malformed)["input_arbitration"]
    assert bounded["root_id"] == bounded["build_token"] == "unknown"
    assert bounded["local_input_size"] == bounded["remote_input_present"] == "unknown"
    assert bounded["selection_reason"] == bounded["local_staging_descendant"] == "unknown"
    assert bounded["split_candidate_local_mtime_ns"] == bounded["split_candidate_remote_mtime_ns"] == "unknown"
    assert bounded["split_candidate_provenance"] == "unknown"
    assert bounded["filled_state"] == "unknown"
    assert observer._sanitize_capture_projection({**projection, "input_arbitration": {"schema": "wrong"}}).get("input_arbitration") is None


def test_scan_join_and_root_source_projection_are_bounded_and_preserve_unknowns():
    value = {
        "kind": "event", "source": "scanner", "category": "scan.adoption",
        "event_type": "diagnostic", "stage": "scan_adoption", "level": "info", "numbers": {},
        "scan_join": {
            "schema": "completion_scan_join.v1", "local_scan_mode": "progressive",
            "scan_source_root_object_digest": "0123456789abcdef",
            "accepted_root_object_digest": "fedcba9876543210", "source_pair_scanned": True,
            "source_pair_completed": False, "source_pair_unknown": None, "source_pair_targeted": True,
            "target_pair_covered": None, "private_path": "/private/drop",
        },
    }
    safe = observer._sanitize_capture_projection(value)
    assert safe["category"] == "scan.adoption" and safe["stage"] == "scan_adoption"
    assert safe["scan_join"]["scan_source_root_object_digest"] == "0123456789abcdef"
    assert safe["scan_join"]["source_pair_unknown"] is None
    assert "private_path" not in json.dumps(safe)

    finalized_scan = {
        "source": "controller", "category": "scan.authority", "level": "info",
        "stage": "scan_authority", "event_type": "state_transition", "message": "scan_root_adopted",
        "corr_id": "completion:0123456789abcdef", "flow_id": None,
        "trace_scope": "flow", "created_ms": 123, "version": 7,
        "details": {"schema": "completion_scan_join.v1", "p": {
            "m": "legacy", "so": "0123456789abcdef", "ao": "fedcba9876543210",
            "sp": True, "cp": False, "up": None, "tp": True, "tc": None,
        }},
    }
    projected_scan, irrelevant = capture._projection(finalized_scan)
    assert not irrelevant and projected_scan is not None
    assert projected_scan["numbers"]["version"] == 7
    assert projected_scan["scan_join"]["local_scan_mode"] == "legacy"
    assert projected_scan["scan_join"]["target_pair_covered"] is None
    malformed = dict(value)
    malformed["scan_join"] = {
        **value["scan_join"], "scan_source_root_object_digest": "/secret/path",
        "source_pair_scanned": 1, "local_scan_mode": "secret-label",
    }
    bounded = observer._sanitize_capture_projection(malformed)["scan_join"]
    assert bounded["scan_source_root_object_digest"] == "unknown"
    assert bounded["source_pair_scanned"] == "unknown"
    assert bounded["local_scan_mode"] == "unknown"

    root_source = {
        "schema": "model_input_arbitration.v1", "phase": "root_source", "subject_kind": "root",
        "build_token": "0123456789abcdef", "root_id": "fedcba9876543210",
        "subject_id": "fedcba9876543210", "input_scope": "root", "selection": "local",
        "local_root_object_digest": "0011223344556677", "local_proof_session_digest": "8899aabbccddeeff",
        "local_proof_object_bound": False,
    }
    root_safe = observer._sanitize_capture_projection({**value, "input_arbitration": root_source})
    assert root_safe["input_arbitration"]["phase"] == "root_source"
    assert root_safe["input_arbitration"]["local_root_object_digest"] == "0011223344556677"
    assert root_safe["input_arbitration"]["local_proof_session_digest"] == "8899aabbccddeeff"
    assert root_safe["input_arbitration"]["local_proof_object_bound"] is False
    finalized_root_source = {
        "source": "model_updater", "category": "scan.authority", "level": "info",
        "stage": "model_builder_input_arbitration", "event_type": "diagnostic",
        "message": "input_arbitration_witness", "corr_id": "input-arbitration:0123456789abcdef",
        "flow_id": "input-arbitration:0123456789abcdef", "trace_scope": "flow",
        "created_ms": 124, "version": 8,
        "details": {**root_source, "p": {
            "local_root_object_digest": "0011223344556677",
            "local_proof_session_digest": "8899aabbccddeeff", "local_proof_object_bound": False,
        }},
    }
    projected_root, irrelevant = capture._projection(finalized_root_source)
    assert not irrelevant and projected_root is not None
    assert projected_root["input_arbitration"]["phase"] == "root_source"
    assert projected_root["input_arbitration"]["local_proof_object_bound"] is False
    root_source["local_root_object_digest"] = "/secret/path"
    root_source["local_proof_object_bound"] = 0
    malformed_root = observer._sanitize_capture_projection({**value, "input_arbitration": root_source})
    assert malformed_root["input_arbitration"]["local_root_object_digest"] == "unknown"
    assert malformed_root["input_arbitration"]["local_proof_object_bound"] == "unknown"


def test_local_scan_tick_and_lifecycle_projection_preserve_unknowns_and_sanitize():
    tick = observer._sanitize_capture_projection({
        "kind": "event", "source": "model_updater", "category": "scan.adoption",
        "event_type": "state_transition", "stage": "scan_adoption", "level": "info", "numbers": {},
        "local_scan_tick": {
            "present": True, "mode": "progressive", "session_digest": "0123456789ab",
            "generation": 7, "progress": True, "final": False, "failed": False,
            "full": None, "new_authority": True, "private_path": "/secret/root",
        },
    })
    assert tick["local_scan_tick"] == {
        "present": True, "mode": "progressive", "session_digest": "0123456789ab",
        "generation": 7, "progress": True, "final": False, "failed": False,
        "full": None, "new_authority": True,
    }
    assert "private_path" not in json.dumps(tick)

    lifecycle = observer._sanitize_capture_projection({
        "kind": "event", "source": "model_updater", "category": "scan.lifecycle",
        "event_type": "diagnostic", "stage": "scan_accumulator", "level": "info", "numbers": {},
        "scan_lifecycle": {
            "message": "scan_accumulator_after_apply", "scanner_side": "local",
            "session_digest": "fedcba987654", "event_count": 2, "completed_pair_count": 1,
            "targeted": True, "final": False, "fence": "none", "private_label": "do-not-copy",
        },
    })
    assert lifecycle["category"] == "scan.lifecycle"
    assert lifecycle["scan_lifecycle"]["message"] == "scan_accumulator_after_apply"
    assert lifecycle["scan_lifecycle"]["completed_pair_count"] == 1
    assert lifecycle["scan_lifecycle"]["scanned_pair_count"] == "unknown"
    assert "private_label" not in json.dumps(lifecycle)

    malformed = observer._sanitize_capture_projection({
        **tick, "local_scan_tick": {
            "present": 1, "mode": "private-mode", "session_digest": "/path",
            "generation": True, "progress": "true", "final": None,
        },
    })["local_scan_tick"]
    assert malformed["present"] == malformed["mode"] == malformed["session_digest"] == "unknown"
    assert malformed["generation"] == malformed["progress"] == "unknown"
    assert malformed["final"] is None
    wrong_width = observer._sanitize_capture_projection({
        **tick, "local_scan_tick": {**tick["local_scan_tick"], "session_digest": "0123456789abcdef"},
    })["local_scan_tick"]
    assert wrong_width["session_digest"] == "unknown"
    oversized_generation = observer._sanitize_capture_projection({
        **tick, "local_scan_tick": {"p": True, "m": "legacy", "s": None, "g": 2**63},
    })["local_scan_tick"]
    assert oversized_generation["generation"] == "unknown"


def test_scanner_process_projection_sanitizer_preserves_bounded_current_run_facts():
    wide = str(2**53 + 17)
    safe = observer._sanitize_capture_projection({
        "kind": "event", "source": "scanner_process", "category": "scanner_process",
        "event_type": "diagnostic", "stage": "scan", "level": "info", "numbers": {},
        "scanner_process_event": {
            "message": "force_scan", "side": "local", "run_digest": "unknown",
            "scan_run_ref": "0123456789ab", "generation": None,
            "current_generation": wide, "scanner_ref": "0123456789abcdef", "request_ref": "fedcba9876543210",
            "decision": "queued", "targeted": None, "final": None, "full": None,
            "progress": None, "failed": None, "utc": "2026-10-09T03:00:00.123+00:00",
            "monotonic_ms": wide, "requested_refs": ["0123456789abcdef"], "requested_count": "1",
            "requested_truncated": False, "private_path": "/secret/root",
        },
    })
    facts = safe["scanner_process_event"]
    assert facts["current_generation"] == wide
    assert facts["monotonic_ms"] == wide
    assert facts["generation"] is None
    assert facts["run_digest"] == "unknown"
    assert facts["scan_run_ref"] == "0123456789ab"
    assert facts["requested_refs"] == ["0123456789abcdef"]
    assert "private_path" not in json.dumps(safe)

    for key, wrong_value in (
        ("source", "controller"), ("category", "controller"), ("stage", "foreign"),
    ):
        foreign = observer._sanitize_capture_projection({**safe, key: wrong_value})
        assert "scanner_process_event" not in foreign

    malformed = observer._sanitize_capture_projection({
        **safe, "scanner_process_event": {
            **facts, "scan_run_ref": "private-session", "utc": "/private/path",
            "generation": True, "current_generation": 2**63,
            "targeted": 1, "requested_refs": ["0123456789abcdef"] * 9,
            "requested_count": "01", "requested_truncated": "false", "decision": "private",
        },
    })["scanner_process_event"]
    assert malformed["generation"] == malformed["current_generation"] == "unknown"
    assert malformed["scan_run_ref"] == "unknown"
    assert malformed["utc"] is None
    assert malformed["targeted"] == "unknown"
    assert malformed["requested_refs"] is None
    assert malformed["requested_count"] == "unknown"
    assert malformed["requested_truncated"] == "unknown"
    assert malformed["decision"] == "unknown"


def test_lifecycle_persist_projection_sanitizer_preserves_only_bounded_authority_facts():
    before = observer._sanitize_capture_projection({
        "source": "model_builder", "category": "lifecycle.persist", "stage": "persist_authority",
        "lifecycle_persist": {
            "event": "persist_authority_before", "corr_ref": "0123456789abcdef", "fence": "none",
            "pre_state": "downloaded", "local_present": True, "remote_present": False,
            "local_size_present": True, "downloaded_marker_present": True,
            "final_move_marker_present": False, "unknown_local": False,
            "path": "/private/path",
        },
    })
    facts = before["lifecycle_persist"]
    assert facts["event"] == "persist_authority_before"
    assert facts["corr_ref"] == "0123456789abcdef"
    assert facts["pre_state"] == "downloaded" and facts["fence"] == "none"
    assert facts["local_present"] is True and facts["remote_present"] is False
    assert "private/path" not in json.dumps(before)

    after = observer._sanitize_capture_projection({
        "source": "model_builder", "category": "lifecycle.persist", "stage": "persist_authority",
        "lifecycle_persist": {
            "event": "persist_authority_after", "corr_ref": "fedcba9876543210",
            "state_category": "default", "fence": "none",
        },
    })["lifecycle_persist"]
    assert after == {
        "event": "persist_authority_after", "corr_ref": "fedcba9876543210",
        "fence": "none", "state_category": "default",
    }

    wide = str(2**53 + 19)
    publication = observer._sanitize_capture_projection({
        "source": "model_builder", "category": "lifecycle.persist", "stage": "persist_authority",
        "lifecycle_persist": {
            "event": "model_live_publication", "corr_ref": "0123456789abcdef",
            "candidate_state": "downloaded", "live_state": "downloaded",
            "build_kind": "full_build", "adoption_kind": "full_model_adopted",
            "candidate_version": wide, "live_version": "9",
            "scope_version": wide, "local_size": wide, "child_count": "2",
            "exact_local_flow_join": {
                "schema": "exact_local_flow_join.v1", "pre_poll_selected": False,
                "input": "member", "basis": "accepted_current", "generation": wide,
                "bucket": "member", "path": "/private/path",
            },
            "path": "/private/path", "file_name": "private-name.iso",
        },
    })["lifecycle_persist"]
    assert publication == {
        "event": "model_live_publication", "corr_ref": "0123456789abcdef",
        "candidate_state": "downloaded", "live_state": "downloaded",
        "build_kind": "full_build", "adoption_kind": "full_model_adopted",
        "candidate_version": wide, "live_version": "9",
        "exact_local_flow_join": {
            "schema": "exact_local_flow_join.v1", "pre_poll_selected": False,
            "input": "member", "basis": "accepted_current", "generation": wide, "bucket": "member",
        },
        "scope_version": wide, "local_size": wide, "child_count": "2",
    }
    assert "private-name.iso" not in json.dumps(publication) and "/private/path" not in json.dumps(publication)

    malformed_publication = observer._sanitize_capture_projection({
        "source": "model_builder", "category": "lifecycle.persist", "stage": "persist_authority",
        "lifecycle_persist": {
            "event": "model_live_publication", "corr_ref": "0123456789abcdef",
            "candidate_state": "private-state", "live_state": "downloaded",
            "build_kind": "private-build", "adoption_kind": "full_model_adopted",
            "candidate_version": True, "live_version": str(2**63),
            "scope_version": True, "local_size": str(2**63), "child_count": "-1",
            "exact_local_flow_join": {
                "schema": "exact_local_flow_join.v1", "pre_poll_selected": 1,
                "input": ["member"], "basis": "accepted_current", "generation": True, "bucket": "member",
            },
        },
    })["lifecycle_persist"]
    assert malformed_publication["candidate_state"] == malformed_publication["build_kind"] == "unknown"
    assert malformed_publication["candidate_version"] is malformed_publication["live_version"] is None
    assert malformed_publication["scope_version"] is malformed_publication["local_size"] is None
    assert malformed_publication["child_count"] is None
    assert malformed_publication["exact_local_flow_join"] == {
        "schema": "exact_local_flow_join.v1", "pre_poll_selected": None,
        "input": "unknown", "basis": "accepted_current", "generation": None, "bucket": "member",
    }

    malformed = observer._sanitize_capture_projection({
        "source": "model_builder", "category": "lifecycle.persist", "stage": "persist_authority",
        "lifecycle_persist": {
            "event": "persist_authority_before", "corr_ref": "/private/path", "fence": "secret",
            "pre_state": "secret", "local_present": 1, "remote_present": "false",
        },
    })["lifecycle_persist"]
    assert malformed["corr_ref"] is None
    assert malformed["fence"] == malformed["pre_state"] == "unknown"
    assert malformed["local_present"] is malformed["remote_present"] is None
    for key, wrong_value in (
        ("source", "controller"), ("category", "model.lifecycle"), ("stage", "lifecycle"),
    ):
        foreign = observer._sanitize_capture_projection({**before, key: wrong_value})
        assert "lifecycle_persist" not in foreign


def test_lftp_status_health_projection_preserves_unknowns_and_omits_private_rows():
    projected, irrelevant = capture._projection({
        "category": "transfer.lftp.status", "message": "lftp_status_poll",
        "source": "model_updater", "event_type": "state_transition", "stage": "lftp_status",
        "level": "warning", "corr_id": "lftp:0123456789abcdef",
        "flow_id": "lftp-poll:0123456789abcdef", "trace_scope": "flow",
        "details": {
            "schema": "lftp_status_authority.v1", "phase": "status_inlet",
            "outcome": "fresh_unhealthy", "parser_outcome": "hard_error",
            "source": "fresh_unhealthy", "failure_reason": "unhealthy_snapshot",
            "fresh": True, "healthy": False, "cache_present": False, "retry_active": True,
            "idle_authoritative": None, "poll_due": True, "status_count": 0,
            "active_count": 0, "raw_status_count": 0, "filtered_status_count": 0,
            "queue_dispatch_pending_count": 1, "lftp_queue_operation_pending_count": 0,
            "lftp_queue_operation_done_count": 0, "status_future_state": "pending",
            "private_path": "secret/path", "transitions": ["private-file-id"],
        },
    })
    assert not irrelevant and projected is not None
    safe = observer._sanitize_capture_projection(projected)
    status = safe["lftp_status_authority"]
    assert status["source"] == "fresh_unhealthy" and status["healthy"] is False
    assert status["idle_authoritative"] is None and status["status_future_state"] == "pending"
    assert "private_path" not in json.dumps(safe) and "private-file-id" not in json.dumps(safe)

    projected_membership, irrelevant = capture._projection({
        "category": "transfer.lftp.membership", "message": "lftp_status_membership_transition",
        "source": "model_updater", "event_type": "state_transition", "stage": "lftp_status_membership",
        "level": "info", "corr_id": "lftp:fedcba9876543210",
        "flow_id": "lftp-poll:0123456789abcdef", "trace_scope": "flow",
        "details": {
            "schema": "lftp_status_membership.v2", "phase": "status_membership",
            "source": "fresh_healthy", "fresh": True, "healthy": True,
            "idle_authoritative": None, "raw_status_count_overflow": False,
            "filtered_status_count_overflow": False, "chunk_index": 0, "chunk_count": 1,
            "transition_count": 1, "raw_status_count": 1, "filtered_status_count": 1,
            "transition_set_digest": "0123456789abcdef", "poll_correlation": "private-correlation",
            "transitions": ["private-path-and-file-token"],
        },
    })
    assert not irrelevant and projected_membership is not None
    safe_membership = observer._sanitize_capture_projection(projected_membership)["lftp_status_membership"]
    assert safe_membership["idle_authoritative"] is None and safe_membership["transition_count"] == 1
    assert "transitions" not in safe_membership and "poll_correlation" not in safe_membership
    malformed = observer._sanitize_capture_projection({
        **projected_membership,
        "lftp_status_membership": {
            **projected_membership["lftp_status_membership"], "chunk_index": True,
            "transition_count": 2049, "idle_authoritative": "false",
            "transition_set_digest": "private-value",
        },
    })["lftp_status_membership"]
    assert malformed["chunk_index"] is None and malformed["transition_count"] is None
    assert malformed["idle_authoritative"] is None and malformed["transition_set_digest"] == "unknown"


@pytest.mark.skipif(os.name == "nt", reason="requires native durable file acknowledgement")
@pytest.mark.skipif(BreadcrumbTraceCollector is None or ModelBuilder is None, reason="production builder unavailable")
def test_finalized_nested_input_witness_reaches_capture_and_json_sink(tmp_path):
    spool = tmp_path / "real-producer-spool"
    spool.mkdir()
    output = tmp_path / "real-producer-sink.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=128,
        policy={"default": "off", "rules": {"scan.authority": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    builder = ModelBuilder()
    epoch_ns = 1_790_873_610_000_000_000
    remote = SystemFile("bounded-root", 2_981_866_544, True, mtime_ns=epoch_ns + 24_000_000_000)
    remote.add_child(SystemFile("payload.iso", 2_981_865_472, False, mtime_ns=epoch_ns + 24_000_000_000))
    remote.add_child(SystemFile("checksums.txt", 1_072, False, mtime_ns=epoch_ns + 500_000_000))
    local = SystemFile("bounded-root", 35_759_708_487, True, mtime_ns=epoch_ns)
    local.add_child(SystemFile("payload.iso", 2_981_865_472, False, is_staging=True,
                               mtime_ns=epoch_ns + 24_000_000_000))
    local.add_child(SystemFile("checksums.txt", 1_072, False, mtime_ns=epoch_ns))
    active = SystemFile("bounded-root", 2_981_865_472, True)
    active.add_child(SystemFile("payload.iso", 2_981_865_472, False, mtime_ns=epoch_ns + 24_000_000_000))
    builder.set_remote_files([remote])
    builder.set_local_files([local])
    builder.set_active_files([active])
    builder.set_stop_resume_trace_breadcrumb(collector.create_emitter())
    try:
        assert builder.build_model().get_file("bounded-root") is not None
        assert collector.flush_durable(2.0)
    finally:
        collector.close(timeout=2.0)

    finalized = [
        json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    source_rows = [row for row in finalized if row.get("message") == "input_arbitration_witness"]
    assert len(source_rows) == 5
    assert all(row.get("trace_scope") == "flow" and row.get("corr_id") == row.get("flow_id")
               and row["corr_id"].startswith("input-arbitration:") for row in source_rows)
    sink = observer.JsonArtifactSink(output)
    versions = []
    for row in source_rows:
        projection, irrelevant = capture._projection(row)
        assert not irrelevant and projection is not None
        versions.append(projection["numbers"]["version"])
        sink.persist({
            "schema": "incoming-recovery-observer-sample.v1", "event": "capture", "outcome": "success",
            "capture": {"state": "record", "source": "controller", "projection": projection},
        })
    assert versions == sorted(versions) and len(set(versions)) == 5

    saved = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    facts = [row["capture"]["projection"]["input_arbitration"] for row in saved]
    root_source = next(item for item in facts if item["phase"] == "root_source")
    assert root_source["local_root_object_digest"] != "unknown"
    assert root_source["local_proof_object_bound"] in {True, False, None, "unknown"}
    root = next(item for item in facts if item["phase"] == "root_fill")
    assert root["selection"] == "split"
    assert root["selection_reason"] == "split_merge"
    assert root["local_candidate_eligible"] is None
    assert root["local_size_ge_active"] is None
    assert root["local_staging_descendant"] is True
    assert root["remote_indicates_newer"] is None
    checksum = next(item for item in facts if item["split_candidate_remote_size"] == 1_072)
    assert checksum["split_candidate_local_mtime_ns"] == str(epoch_ns)
    assert checksum["split_candidate_remote_mtime_ns"] == str(epoch_ns + 500_000_000)
    assert int(checksum["split_candidate_local_mtime_ns"]) > 2**53
    assert checksum["split_candidate_strict_verified"] is False
    assert checksum["split_candidate_portable_queue_exclusion_match"] is True
    assert checksum["split_candidate_provenance"] == "split_candidate_local"


def test_delete_local_projection_sanitizer_preserves_only_typed_outcome_facts(tmp_path):
    projection = {
        "kind": "completion", "source": "controller", "category": "controller",
        "event_type": "completion", "stage": "command", "level": "info",
        "delete_local_planned_location_count": 2,
        "delete_local_removed_location_count": 1,
        "delete_local_result_known": True,
        "delete_local_partial_failure": True,
        "file_name": "private-name.bin", "local_path": "/private/location",
    }
    sink = observer.JsonArtifactSink(tmp_path / "delete-local-projection.jsonl")
    sink.persist({
        "schema": "incoming-recovery-observer-sample.v1", "event": "capture", "outcome": "success",
        "capture": {"state": "record", "source": "controller", "projection": projection},
    })
    text = (tmp_path / "delete-local-projection.jsonl").read_text(encoding="utf-8")
    safe = json.loads(text)["capture"]["projection"]
    assert safe["delete_local_planned_location_count"] == 2
    assert safe["delete_local_removed_location_count"] == 1
    assert safe["delete_local_result_known"] is True
    assert safe["delete_local_partial_failure"] is True
    assert "file_name" not in safe and "local_path" not in safe
    assert "private-name.bin" not in text and "/private/location" not in text

    unknown = observer._sanitize_capture_projection({
        **projection,
        "delete_local_planned_location_count": None,
        "delete_local_removed_location_count": None,
        "delete_local_result_known": False,
        "delete_local_partial_failure": None,
    })
    assert unknown["delete_local_planned_location_count"] is None
    assert unknown["delete_local_removed_location_count"] is None
    assert unknown["delete_local_result_known"] is False
    assert unknown["delete_local_partial_failure"] is None

    malformed = observer._sanitize_capture_projection({
        **projection,
        "delete_local_planned_location_count": True,
        "delete_local_removed_location_count": 3,
        "delete_local_result_known": 1,
        "delete_local_partial_failure": "false",
    })
    assert not any(key.startswith("delete_local_") for key in malformed)


def test_queue_readiness_projection_sanitizer_preserves_finite_boundaries():
    value = {
        "kind": "event", "source": "controller", "category": "queue.readiness",
        "event_type": "diagnostic", "stage": "queue_readiness", "level": "info",
        "queue_readiness": {
            "schema": "queue_readiness.v2", "event": "queue_final_decision",
            "phase": "final_decision", "origin": "manual", "outcome": "deferred",
            "reason": "initial_scan_authority_pending", "decision_boundary": "scan_authority",
            "decision": "defer", "dispatch_attempted": False, "local_reconciled": False,
            "remote_reconciled": False, "unknown_local_overlay": True,
            "path_pair_scope_configured": True, "publication_id": 4, "model_version": 9,
            "unapproved_field": "private-value",
        },
    }
    safe = observer._sanitize_capture_projection(value)
    assert safe["queue_readiness"]["event"] == "queue_final_decision"
    assert safe["queue_readiness"]["local_reconciled"] is False
    assert safe["queue_readiness"]["publication_id"] == 4
    assert "unapproved_field" not in json.dumps(safe)
    assert "private-value" not in json.dumps(safe)

    malformed = observer._sanitize_capture_projection({
        "kind": "event", "source": "controller", "category": "queue.readiness",
        "event_type": "diagnostic", "stage": "queue_readiness", "level": "info",
        "queue_readiness": {
            "schema": "private-schema", "event": "private-event", "phase": "private-phase",
            "origin": "private-origin", "outcome": "private-outcome", "reason": "private-reason",
            "dispatch_attempted": "false", "model_version": 2**31,
        },
    })
    assert malformed["queue_readiness"]["schema"] == "queue_readiness.v1"
    assert malformed["queue_readiness"]["event"] == "unknown"
    assert malformed["queue_readiness"]["phase"] == "unknown"
    assert malformed["queue_readiness"]["dispatch_attempted"] is None
    assert malformed["queue_readiness"]["model_version"] is None


def test_queue_readiness_scan_diagnostics_sanitize_exact_decimal_and_unknowns():
    wide = str(2**53 + 19)
    safe = observer._sanitize_capture_projection({
        "queue_readiness": {
            "schema": "queue_readiness.v1", "event": "queue_rescan_readiness",
            "phase": "rescan_unknown", "origin": "manual", "outcome": "failed",
            "reason": "initial_local_scan_failed",
            "scan_failure": {
                "side": "local", "kind": "failed", "scope": "targeted", "target_covered": True,
                "terminal": False, "recoverable": True, "result_generation": wide,
                "run_matches_baseline": True, "generation_vs_baseline": "newer",
                "failure_generation_source": "pair", "failure_generation": wide,
                "failure_generation_vs_baseline": "newer",
                "failure_ignored_reason": "session_mismatch", "baseline_generation": str(int(wide) - 1),
                "failure_run_ref": "012345abcdef",
                "path": "/private/path", "session": "private-session",
            },
            "fresh_root_proof": {
                "result": "rejected", "reason": "below_work_floor", "root_shape_ok": True,
                "remote_ready": True, "overlay_unknown": True, "process_generation": wide,
                "effective_work_floor": "4", "root_generation": wide, "source_present": True,
                "run_matches_current": True, "builder_proof_matches_source": False,
                "root_vs_work_floor": "below", "root_vs_process_generation": "at_or_below",
                "run_matches_baseline": True, "generation_vs_baseline": "equal",
                "path": "/private/path", "session": "private-session",
            },
        },
    })
    readiness = safe["queue_readiness"]
    assert readiness["scan_failure"]["result_generation"] == wide
    assert readiness["scan_failure"]["failure_generation_source"] == "pair"
    assert readiness["scan_failure"]["failure_generation"] == wide
    assert readiness["scan_failure"]["failure_generation_vs_baseline"] == "newer"
    assert readiness["scan_failure"]["failure_ignored_reason"] == "session_mismatch"
    assert readiness["scan_failure"]["baseline_generation"] == str(int(wide) - 1)
    assert readiness["scan_failure"]["failure_run_ref"] == "012345abcdef"
    assert readiness["fresh_root_proof"]["process_generation"] == wide
    assert readiness["fresh_root_proof"]["root_generation"] == wide
    assert "/private/path" not in json.dumps(safe) and "private-session" not in json.dumps(safe)

    malformed = observer._sanitize_capture_projection({
        "queue_readiness": {
            "schema": "queue_readiness.v1", "event": "queue_rescan_readiness",
            "scan_failure": {"side": "local", "terminal": 1, "result_generation": "01",
                             "failure_generation_source": "not-allowed",
                             "failure_generation": True, "failure_generation_vs_baseline": "bad",
                             "failure_ignored_reason": "private", "baseline_generation": True,
                             "failure_run_ref": "/private/session"},
            "fresh_root_proof": {"result": "accepted", "root_shape_ok": "true",
                                 "root_generation": str(2**63)},
        },
    })["queue_readiness"]
    assert malformed["scan_failure"]["side"] == "local"
    assert malformed["scan_failure"]["terminal"] is None
    assert malformed["scan_failure"]["result_generation"] is None
    assert malformed["scan_failure"]["failure_generation_source"] == "unknown"
    assert malformed["scan_failure"]["failure_generation"] is None
    assert malformed["scan_failure"]["failure_generation_vs_baseline"] == "unknown"
    assert malformed["scan_failure"]["failure_ignored_reason"] == "unknown"
    assert malformed["scan_failure"]["baseline_generation"] is None
    assert malformed["scan_failure"]["failure_run_ref"] is None
    assert malformed["fresh_root_proof"]["root_shape_ok"] is None
    assert malformed["fresh_root_proof"]["root_generation"] is None


def test_completion_move_projection_sanitizer_preserves_finite_facts():
    safe = observer._sanitize_capture_projection({
        "completion_gate": {
            "boundary": "completion_gate", "message": "pending_completion_move_decision",
            "schema": "pending_completion_move_decision.v1", "phase": "move", "decision": "result",
            "reason": "move_deferred", "proof_source": "move_result", "move_result": "deferred",
            "raw_path": "/private/path",
        },
        "final_move_publication": {
            "message": "final_move_publication", "phase": "publish", "operation": "publish",
            "side": "destination", "errno_class": "eexist", "mutation_state": "uncertain",
            "temporary_created": None, "residue_present": False, "source_exists": True,
            "destination_parent_exists": True, "destination_exists": True, "copy_bytes_ns": 2**53 + 1,
            "outer_source_recheck_ns": 2**53 + 3, "source_identity_check_ns": None,
            "claim_rename_ns": 5, "claim_parent_sync_ns": 7,
            "claimed_identity_tree_validation_ns": 11, "claim_delete_ns": 13,
            "claim_final_sync_ns": 17, "caller_parent_sync_ns": 19,
            "destination_path": "/private/path",
        },
    })
    assert safe["completion_gate"] == {
        "boundary": "completion_gate", "message": "pending_completion_move_decision",
        "schema": "pending_completion_move_decision.v1", "phase": "move", "decision": "result",
        "reason": "move_deferred", "proof_source": "move_result", "move_result": "deferred",
    }
    assert safe["final_move_publication"]["temporary_created"] is None
    assert safe["final_move_publication"]["residue_present"] is False
    assert safe["final_move_publication"]["destination_exists"] is True
    assert safe["final_move_publication"]["copy_bytes_ns"] == 2**53 + 1
    assert safe["final_move_publication"]["outer_source_recheck_ns"] == 2**53 + 3
    assert safe["final_move_publication"]["source_identity_check_ns"] is None
    assert safe["final_move_publication"]["claim_final_sync_ns"] == 17
    assert "raw_path" not in safe["completion_gate"]
    assert "destination_path" not in safe["final_move_publication"]

    malformed = observer._sanitize_capture_projection({
        "completion_gate": {
            "boundary": "completion_gate", "message": "pending_completion_move_decision",
            "schema": "private", "phase": "private", "decision": "private", "reason": "private",
            "proof_source": "private", "move_result": "private",
        },
        "final_move_publication": {
            "message": "final_move_publication", "phase": "private", "operation": "private",
            "side": "private", "errno_class": "private", "mutation_state": "private",
            "destination_exists": "true",
        },
    })
    assert malformed["completion_gate"]["schema"] == "unknown"
    assert all(malformed["completion_gate"][key] == "unknown" for key in (
        "phase", "decision", "reason", "proof_source", "move_result",
    ))
    assert malformed["final_move_publication"]["phase"] == "unknown"
    assert malformed["final_move_publication"]["destination_exists"] is None


def test_pending_completion_wake_projection_sanitizer_preserves_only_finite_proof_facts():
    safe = observer._sanitize_capture_projection({
        "completion_gate": {
            "boundary": "completion_gate", "message": "pending_completion_authority_wake",
            "schema": "pending_completion_authority_wake.v1", "decision": "blocked",
            "reason": "local_scope_unreconciled", "local_root_proof": {
                "shape": "directory_root", "inputs_known": True, "checker_available": True,
                "result": False, "start_relation": "after", "entry": "present", "run": "mismatch",
                "generation": "match", "builder": "match", "expected": "not_checked",
                "private_session": "do-not-copy", "raw_path": "/private/root",
            },
            "raw_path": "/private/root",
        },
    })
    assert safe["completion_gate"] == {
        "boundary": "completion_gate", "message": "pending_completion_authority_wake",
        "schema": "pending_completion_authority_wake.v1", "decision": "blocked",
        "reason": "local_scope_unreconciled", "local_root_proof": {
            "shape": "directory_root", "inputs_known": True, "checker_available": True,
            "result": False, "start_relation": "after", "entry": "present", "run": "mismatch",
            "generation": "match", "builder": "match", "expected": "not_checked",
        },
    }

    malformed = observer._sanitize_capture_projection({
        "completion_gate": {
            "boundary": "completion_gate", "message": "pending_completion_authority_wake",
            "schema": "private-schema", "local_root_proof": {
                "shape": "private", "inputs_known": "false", "checker_available": None,
                "result": "unknown", "start_relation": ["private"], "entry": "private",
                "run": "private", "generation": "private", "builder": "private", "expected": "private",
            },
        },
    })["completion_gate"]
    assert malformed["schema"] == "unknown"
    assert malformed["local_root_proof"] == {
        "shape": "unknown", "inputs_known": None, "checker_available": None,
        "result": None, "start_relation": "unknown", "entry": "unknown", "run": "unknown",
        "generation": "unknown", "builder": "unknown", "expected": "unknown",
    }


def test_pending_completion_wake_projection_keeps_unknown_distinct_from_false():
    projection = capture._projection({
        "source": "model_updater", "message": "pending_completion_authority_wake",
        "category": "completion.gate", "event_type": "diagnostic", "stage": "completion_gate",
        "level": "info", "details": {
            "schema": "pending_completion_authority_wake.v1", "decision": "blocked",
            "reason": "local_scope_unreconciled", "local_root_proof_shape": "directory_root",
            "local_root_proof_result": "unknown", "local_root_proof_checks": {
                "entry": "malformed", "run": "unknown", "generation": "mismatch",
                "builder": "unknown", "expected": "not_checked",
            },
        },
    })[0]
    proof = projection["completion_gate"]["local_root_proof"]
    assert proof["result"] is None
    assert proof["entry"] == "malformed"
    assert proof["run"] == "unknown"
    assert proof["generation"] == "mismatch"
    assert proof["builder"] == "unknown"


def test_model_finalization_projection_sanitizer_keeps_only_bounded_schema_fields():
    safe = observer._sanitize_capture_projection({
        "model_finalization": {
            "schema": "model_finalization.v2", "phase": "complete", "stage": "model_lock_wait",
            "outcome": "completed", "wall_ms": 17, "thread_cpu_ms": 3,
            "lock_wait_ms": 17, "lock_hold_ms": None, "boundary_monotonic_ns": "9007199254741110",
            "private_detail": "discard",
        },
    })["model_finalization"]
    assert safe == {
        "schema": "model_finalization.v2", "phase": "complete", "stage": "model_lock_wait",
        "outcome": "completed", "wall_ms": 17, "thread_cpu_ms": 3,
        "lock_wait_ms": 17, "lock_hold_ms": None, "boundary_monotonic_ns": "9007199254741110",
    }

    malformed = observer._sanitize_capture_projection({
        "model_finalization": {
            "schema": "private", "phase": "private", "stage": "private", "outcome": "private",
            "wall_ms": True, "thread_cpu_ms": -1, "lock_wait_ms": 1.5,
            "lock_hold_ms": 86_400_001, "boundary_monotonic_ns": 2**53 + 1,
        },
    })["model_finalization"]
    assert malformed == {
        "schema": "unknown", "phase": "unknown", "stage": "unknown", "outcome": "unknown",
        "wall_ms": None, "thread_cpu_ms": None, "lock_wait_ms": None, "lock_hold_ms": None,
        "boundary_monotonic_ns": None,
    }


def test_pending_transfer_gate_requires_exact_local_paths_and_safe_lifecycle_before_queue():
    gate = observer.QueueGate(
        PAIR, "Pr0n", ROOT, "Incoming",
        expected_pair_local_path="/mounts/pr0n", expected_root_relative_path="Incoming",
        require_pending_transfer=True,
    )
    ready = responses()
    ready["/server/path-pairs"]["data"][0]["local_path"] = "/mounts/pr0n"
    ready["/server/model/v1/pairs/pair-1/roots?limit=200"]["records"][0].update({
        "full_path": "Incoming",
        "remote_present": True,
        "remote_has_transferable_content": True,
        "local_present": True,
        "complete_local_coverage": False,
        "final_move_succeeded": False,
        "explicitly_stopped": False,
    })
    gate.preflight(lambda path: ready[path])

    blocked = observer.QueueGate(
        PAIR, "Pr0n", ROOT, "Incoming",
        expected_pair_local_path="/mounts/pr0n", expected_root_relative_path="Incoming",
        require_pending_transfer=True,
    )
    ready["/server/model/v1/pairs/pair-1/roots?limit=200"]["records"][0]["complete_local_coverage"] = True
    with pytest.raises(observer.ObserverSchemaError, match="complete_local_coverage"):
        blocked.preflight(lambda path: ready[path])
    sent = []
    with pytest.raises(observer.ObserverSchemaError, match="blocked"):
        blocked.queue(sent.append)
    assert sent == []


def test_fresh_download_gate_is_opt_in_and_requires_remote_only_shape():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", require_remote_only=True)
    ready = responses()
    ready["/server/model/v1/pairs/pair-1/roots?limit=200"]["records"][0].update({
        "remote_present": True, "remote_has_transferable_content": True,
        "local_present": False, "complete_local_coverage": False,
        "final_move_succeeded": False, "explicitly_stopped": False,
    })
    gate.preflight(lambda path: ready[path])

    for changed in (
        {"local_present": True}, {"complete_local_coverage": True},
        {"explicitly_stopped": True}, {"remote_has_transferable_content": False},
    ):
        blocked = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", require_remote_only=True)
        candidate = responses()
        candidate["/server/model/v1/pairs/pair-1/roots?limit=200"]["records"][0].update({
            "remote_present": True, "remote_has_transferable_content": True,
            "local_present": False, "complete_local_coverage": False,
            "final_move_succeeded": False, "explicitly_stopped": False,
            **changed,
        })
        with pytest.raises(observer.ObserverSchemaError, match="not queue-ready"):
            blocked.preflight(lambda path: candidate[path])

    with pytest.raises(observer.ObserverSchemaError, match="mutually exclusive"):
        observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", require_pending_transfer=True,
                           require_remote_only=True)


def test_transport_timing_sanitizer_preserves_exact_decimal_and_drops_malformed_values():
    artifact = observer._sanitize_artifact({
        "schema": "incoming-recovery-observer-event.v1", "event": "transport_timing",
        "transport_timing": {
            "request_kind": "get", "endpoint_class": "status", "outcome": "success",
            "start_utc_ns": str(2**53 + 7), "end_utc_ns": str(2**53 + 17),
            "start_monotonic_ns": str(2**53 + 9), "end_monotonic_ns": str(2**53 + 19),
            "elapsed_ns": 10, "status_code": 200, "secret": "discard",
        },
    })["transport_timing"]
    assert artifact["start_utc_ns"] == str(2**53 + 7)
    assert artifact["end_monotonic_ns"] == str(2**53 + 19)
    assert artifact["elapsed_ns"] == 10
    malformed = observer._sanitize_artifact({
        "transport_timing": {
            "request_kind": [], "endpoint_class": {}, "outcome": True,
            "start_utc_ns": 2**53 + 7, "end_utc_ns": "-1",
            "start_monotonic_ns": "9223372036854775808", "end_monotonic_ns": "1.0",
            "elapsed_ns": True, "status_code": True, "error_type": [],
        },
    })["transport_timing"]
    assert malformed["request_kind"] == "unknown"
    assert malformed["endpoint_class"] == "unknown"
    assert malformed["outcome"] == "unknown"
    assert all(malformed[field] is None for field in (
        "start_utc_ns", "end_utc_ns", "start_monotonic_ns", "end_monotonic_ns",
        "elapsed_ns", "status_code", "error_type",
    ))


def test_json_sink_persists_sanitized_preflight_success_and_failure(tmp_path):
    artifact_path = tmp_path / "observer.jsonl"
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_path=artifact_path)
    gate.preflight(lambda path: responses()[path])

    bad = responses()
    bad["/server/path-pairs"] = {"data": "private-response-body", "headers": "Bearer secret"}
    blocked = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_path=artifact_path)
    with pytest.raises(observer.ObserverSchemaError):
        blocked.preflight(lambda path: bad[path])

    records = [json.loads(line) for line in artifact_path.read_text(encoding="utf-8").splitlines()]
    assert [record["outcome"] for record in records] == ["success", "failure"]
    serialized = artifact_path.read_text(encoding="utf-8")
    assert PAIR not in serialized
    assert "private-response-body" not in serialized
    assert "Bearer secret" not in serialized
    assert "/server/path-pairs" not in serialized


def test_queue_http_error_is_persisted_and_reraised_without_private_data(tmp_path):
    artifact_path = tmp_path / "queue.jsonl"
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_path=artifact_path)
    gate.preflight(lambda path: responses()[path])

    def send(_path):
        raise HTTPError(
            "http://private/server/command/queue/secret", 409, "Conflict",
            {"Authorization": "Bearer header-secret"},
            BytesIO(b"Queue preflight cancelled: initial_scan_authority_deadline body-secret"),
        )

    with pytest.raises(HTTPError):
        gate.queue(send)

    serialized = artifact_path.read_text(encoding="utf-8")
    assert "body-secret" not in serialized
    assert "header-secret" not in serialized
    assert "private/server" not in serialized
    records = [json.loads(line) for line in serialized.splitlines()]
    assert records[-1]["event"] == "queue"
    assert records[-1]["outcome"] == "failure"
    assert records[-1]["queue_evidence"]["body_reason"] == "preflight_cancelled_unrecognized"


def test_queue_timeout_is_persisted_and_reraised_and_first_failure_is_retained(tmp_path):
    artifact_path = tmp_path / "timeout.jsonl"
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_path=artifact_path)
    gate.preflight(lambda path: responses()[path])
    calls = []

    def send(_path):
        calls.append(True)
        raise TimeoutError("timeout body should not be retained")

    with pytest.raises(TimeoutError):
        gate.queue(send)
    first = gate.first_failure_evidence
    assert first is gate.last_queue_evidence
    with pytest.raises(observer.ObserverSchemaError, match="already attempted"):
        gate.queue(send)
    assert len(calls) == 1
    assert "timeout body should not be retained" not in artifact_path.read_text(encoding="utf-8")


def test_repeated_preflight_does_not_reset_one_post_attempt():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    sent = []
    get = lambda path: responses()[path]
    gate.preflight(get)
    gate.queue(lambda path: sent.append(path) or observer.QueueTransportResponse(200))
    gate.preflight(get)
    with pytest.raises(observer.ObserverSchemaError, match="already attempted"):
        gate.queue(lambda path: sent.append(path) or object())
    assert len(sent) == 1


def test_fixed_get_sampler_persists_before_each_next_get_without_overlap(tmp_path):
    artifact_path = tmp_path / "samples.jsonl"
    active = 0
    max_active = 0
    order = []

    def get(path):
        nonlocal active, max_active
        assert active == 0
        active += 1
        max_active = max(max_active, active)
        order.append(path)
        active -= 1
        return {"private": "response-body"}

    sampler = observer.FixedGetSampler(
        ("/server/private?token=secret", "/server/status"), artifact_sink=artifact_path,
    )
    records = sampler.sample(get)
    assert [record["outcome"] for record in records] == ["success", "success"]
    assert max_active == 1
    assert order == ["/server/private?token=secret", "/server/status"]
    persisted = artifact_path.read_text(encoding="utf-8")
    assert "token=secret" not in persisted
    assert "response-body" not in persisted
    phases = [(json.loads(line)["phase"], json.loads(line)["request_index"])
              for line in persisted.splitlines()]
    assert phases == [("before", 0), ("after", 0), ("before", 1), ("after", 1)]


def test_fixed_get_sampler_accepts_artifact_path_and_rejects_ambiguous_sink(tmp_path):
    artifact_path = tmp_path / "samples.jsonl"
    sampler = observer.FixedGetSampler(("/server/status",), artifact_path=artifact_path)
    sampler.sample(lambda _path: {})
    assert artifact_path.exists()
    with pytest.raises(ValueError, match="artifact_sink or artifact_path"):
        observer.FixedGetSampler(("/server/status",), artifact_sink=[], artifact_path=artifact_path)


class PrivateResponse409:
    status = 409


class PrivateResponse500:
    code = 500


class FailingSink:
    def __call__(self, _artifact):
        raise RuntimeError("sink-private-detail")


class FailsAfterGetSink:
    def __init__(self):
        self.records = []

    def __call__(self, artifact):
        if artifact.get("phase") == "after":
            raise RuntimeError("after-sink-private-detail")
        self.records.append(artifact)


def test_sampler_fails_closed_before_get_when_boundary_capture_fails():
    calls = []
    sampler = observer.FixedGetSampler(("/server/status",), artifact_sink=FailingSink())
    with pytest.raises(observer.ObserverCaptureError, match="before request"):
        sampler.sample(lambda path: calls.append(path))
    assert calls == []
    assert sampler.capture_failure == {
        "schema": "incoming-recovery-observer-capture-failure.v1",
        "phase": "get_before", "kind": "artifact_sink_failure",
    }


def test_sampler_reports_post_get_capture_failure_and_stops_before_next_get():
    sink = FailsAfterGetSink()
    calls = []
    sampler = observer.FixedGetSampler(("/server/one", "/server/two"), artifact_sink=sink)
    records = sampler.sample(lambda path: calls.append(path) or {"private": "body"})
    assert calls == ["/server/one"]
    assert records == [{
        "schema": "incoming-recovery-observer-sample.v1", "event": "get",
        "phase": "after", "outcome": "capture_failure", "request_index": 0,
        "endpoint_class": "unknown", "request_latency_ms": pytest.approx(records[0]["request_latency_ms"]),
        "response_kind": "mapping",
        "capture_failure": {
            "schema": "incoming-recovery-observer-capture-failure.v1",
            "phase": "get_success", "kind": "artifact_sink_failure",
        },
    }]
    assert "after-sink-private-detail" not in str(records)


def test_sampler_stops_after_get_exception_when_failure_capture_fails():
    sink = FailsAfterGetSink()
    calls = []
    sampler = observer.FixedGetSampler(("/server/one", "/server/two"), artifact_sink=sink)

    def get(path):
        calls.append(path)
        raise TimeoutError("transport-private-detail")

    records = sampler.sample(get)
    assert calls == ["/server/one"]
    assert records[0]["outcome"] == "capture_failure"
    assert records[0]["error"]["error_type"] == "timeout"
    assert records[0]["capture_failure"] == {
        "schema": "incoming-recovery-observer-capture-failure.v1",
        "phase": "get_failure", "kind": "artifact_sink_failure",
    }
    assert "transport-private-detail" not in str(records)


def test_sampler_records_latency_outside_capture_and_discriminator_respects_five_second_budget():
    clock = iter((1_000_000_000, 5_999_000_000, 6_000_000_000, 11_000_000_000))
    sampler = observer.FixedGetSampler(
        ("/server/status", "/server/model/v1/summary"), clock=lambda: next(clock),
    )
    records = sampler.sample(lambda _path: {"private": "response"})
    assert [record["request_latency_ms"] for record in records] == [4999.0, 5000.0]
    assert [record["endpoint_class"] for record in records] == ["status", "model_summary"]
    result = sampler.discriminate({
        "schema": "seedsync.performance-diagnostics.v1",
        "current": {"process_cpu_percent_one_core": 0.0},
        "active_stages": {},
    })
    assert result["classification"] == "inconclusive"
    assert result["reason"] == "slow_without_signal"
    assert "private" not in str(result)


@pytest.mark.parametrize(
    ("diagnostics", "classification"),
    [
        ({"schema": "seedsync.performance-diagnostics.v1", "current": {"process_cpu_percent_one_core": 90.0}, "active_stages": {}}, "likely_gil_or_cpu"),
        ({"schema": "seedsync.performance-diagnostics.v1", "current": {"process_cpu_percent_one_core": 1.0}, "active_stages": {
            "model_update_lock_hold": {"count": 1, "max_wall_seconds": 5.0},
        }}, "lock_wait_or_hold"),
        ({"schema": "seedsync.performance-diagnostics.v1", "current": {"process_cpu_percent_one_core": 1.0}, "active_stages": {
            "local_scan_filesystem_traversal": {"count": 1, "max_wall_seconds": 5.0},
        }}, "scan_traversal_or_intake"),
        ({"schema": "seedsync.performance-diagnostics.v1", "current": {"process_cpu_percent_one_core": 1.0}, "active_stages": {
            "model_summary_serialization": {"count": 1, "max_wall_seconds": 5.0},
        }}, "model_serialization_or_sse"),
    ],
)
def test_discriminator_classifies_current_diagnostics_only(diagnostics, classification):
    sampler = observer.FixedGetSampler(("/server/status",), clock=iter((0, 5_000_000_000)).__next__)
    sampler.sample(lambda _path: {})
    result = sampler.discriminate(diagnostics)
    assert result["classification"] == classification


def test_discriminator_fails_closed_for_auth_and_malformed_diagnostics():
    sampler = observer.FixedGetSampler(("/server/status",), clock=iter((0, 1)).__next__)
    sampler.sample(lambda _path: type("Response", (), {"status": 403})())
    assert sampler.discriminate()["reason"] == "auth_error"
    bad_records = [{"schema": "incoming-recovery-observer-sample.v1", "event": "get"}]
    assert observer.discriminate_api_responsiveness(bad_records, {"schema": "wrong"})["classification"] == "inconclusive"


def test_discriminator_rejects_duplicate_and_untyped_artifact_or_diagnostic_values():
    record = {
        "schema": "incoming-recovery-observer-sample.v1", "event": "get", "phase": "after",
        "request_index": 0, "endpoint_class": "status", "outcome": "success", "request_latency_ms": 1,
    }
    malformed_cases = (
        [record, dict(record)],
        [{**record, "phase": []}],
        [{**record, "outcome": "failure", "error": {"error_type": []}}],
    )
    for records in malformed_cases:
        assert observer.discriminate_api_responsiveness(records)["classification"] == "inconclusive"
    assert observer.discriminate_api_responsiveness([record], {"samples": None})["classification"] == "inconclusive"
    assert observer.discriminate_api_responsiveness([record], {"samples": [None]})["classification"] == "inconclusive"
    assert observer.discriminate_api_responsiveness([record], {"current": {}})["classification"] == "inconclusive"
    failed = {**record, "outcome": "failure"}
    captured = {**record, "outcome": "capture_failure"}
    assert observer.discriminate_api_responsiveness([failed])["classification"] == "inconclusive"
    assert observer.discriminate_api_responsiveness([captured])["classification"] == "inconclusive"


def test_sampler_clears_stale_records_and_stops_on_http_status_when_requested():
    sampler = observer.FixedGetSampler(("/server/status",), clock=iter((0, 1, 2, 3)).__next__)
    sampler.sample(lambda _path: {})
    sampler.artifact_sink = FailingSink()
    with pytest.raises(observer.ObserverCaptureError):
        sampler.sample(lambda _path: {})
    assert sampler.discriminate()["classification"] == "inconclusive"

    strict = observer.FixedGetSampler(("/server/status", "/server/model/v1/summary"), continue_on_error=False)
    with pytest.raises(observer.ObserverSchemaError, match="HTTP error"):
        strict.sample(lambda _path: type("Response", (), {"status": 403})())
    assert len(strict.last_records) == 1
    assert strict.last_records[0]["error"]["error_type"] == "auth_error"


@pytest.mark.parametrize(
    ("response", "reason"),
    [(PrivateResponse409(), "unrecognized_409_response"), (PrivateResponse500(), "http_500")],
)
def test_error_status_response_is_persisted_as_failure_with_safe_first_evidence(tmp_path, response, reason):
    artifact_path = tmp_path / "status-response.jsonl"
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_path=artifact_path)
    gate.preflight(lambda path: responses()[path])
    gate.queue(lambda _path: response)

    record = json.loads(artifact_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "queue"
    assert record["outcome"] == "failure"
    assert record["first_failure"] is True
    assert record["queue_evidence"]["status_code"] in (409, 500)
    assert record["queue_evidence"]["body_reason"] == reason
    serialized = artifact_path.read_text(encoding="utf-8")
    assert "PrivateResponse" not in serialized


def test_failing_sink_does_not_mask_schema_failure():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_sink=FailingSink())
    bad = responses()
    bad["/server/path-pairs"] = {"data": "malformed"}
    with pytest.raises(observer.ObserverSchemaError, match="data must be a list"):
        gate.preflight(lambda path: bad[path])
    assert gate.capture_failure == {
        "schema": "incoming-recovery-observer-capture-failure.v1",
        "phase": "preflight_failure", "kind": "artifact_sink_failure",
    }


def test_failing_sink_does_not_mask_queue_error_or_allow_a_second_post():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_sink=[])
    gate.preflight(lambda path: responses()[path])
    gate.artifact_sink = FailingSink()
    sent = []

    def send(path):
        sent.append(path)
        raise TimeoutError("timeout-private-detail")

    with pytest.raises(observer.ObserverCaptureError):
        gate.queue(send)
    assert gate.queue_attempted is True
    assert gate.capture_failure == {
        "schema": "incoming-recovery-observer-capture-failure.v1",
            "phase": "queue_attempt_started", "kind": "artifact_sink_failure",
    }
    with pytest.raises(observer.ObserverSchemaError, match="already attempted"):
        gate.queue(send)
    assert len(sent) == 0
    assert "timeout-private-detail" not in str(gate.capture_failure)


def test_failing_sink_does_not_mask_queue_http_error():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_sink=[])
    gate.preflight(lambda path: responses()[path])
    gate.artifact_sink = FailingSink()
    sent = []

    def send(path):
        sent.append(path)
        raise HTTPError("http://private", 409, "Conflict", None, BytesIO(b"private-body"))

    with pytest.raises(observer.ObserverCaptureError):
        gate.queue(send)
    assert gate.queue_attempted is True
    assert gate.capture_failure == {
        "schema": "incoming-recovery-observer-capture-failure.v1",
            "phase": "queue_attempt_started", "kind": "artifact_sink_failure",
    }
    with pytest.raises(observer.ObserverSchemaError, match="already attempted"):
        gate.queue(send)
    assert len(sent) == 0


def test_custom_exception_and_response_names_are_not_persisted(tmp_path):
    class BearerPrivateException(Exception):
        pass

    artifact_path = tmp_path / "names.jsonl"
    sampler = observer.FixedGetSampler(("/server/status",), artifact_sink=artifact_path)
    sampler.sample(lambda _path: (_ for _ in ()).throw(BearerPrivateException("secret")))
    sampler = observer.FixedGetSampler(("/server/status",), artifact_sink=artifact_path)
    sampler.sample(lambda _path: PrivateResponse409())
    serialized = artifact_path.read_text(encoding="utf-8")
    assert "BearerPrivateException" not in serialized
    assert "PrivateResponse409" not in serialized
    assert "secret" not in serialized


def test_guarded_queue_runner_orders_passive_observer_before_exactly_one_post():
    events = []
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")

    def get(path):
        events.append("get")
        return responses()[path]

    def start_passive():
        events.append("passive-start")
        return lambda: events.append("passive-stop")

    def send(_path):
        events.append("post")
        return observer.QueueTransportResponse(200)

    runner = observer.GuardedQueueRunner(gate, get, send, start_passive,
        {"INCOMING_RECOVERY_ALLOW_QUEUE": "1"})
    assert runner.run().status == 200
    assert events.index("passive-start") < events.index("post") < events.index("passive-stop")
    with pytest.raises(observer.ObserverSchemaError, match="already attempted"):
        runner.run()
    assert events.count("post") == 1


def test_guarded_queue_runner_fails_before_observer_or_post_without_gate():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    runner = observer.GuardedQueueRunner(gate, lambda path: responses()[path],
        lambda _path: pytest.fail("POST must not run"), lambda: pytest.fail("observer must not start"), {}, 35)
    with pytest.raises(observer.ObserverSchemaError, match="environment"):
        runner.run()


def test_guarded_queue_runner_rejects_a_timeout_shorter_than_the_handler_budget():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    runner = observer.GuardedQueueRunner(gate, lambda path: responses()[path],
        lambda _path: pytest.fail("POST must not run"), lambda: pytest.fail("observer must not start"),
        {"INCOMING_RECOVERY_ALLOW_QUEUE": "1"}, 30)
    with pytest.raises(observer.ObserverSchemaError, match="timeout"):
        runner.run()


def test_guarded_queue_runner_preserves_queue_error_when_passive_stop_fails():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    runner = observer.GuardedQueueRunner(
        gate, lambda path: responses()[path],
        lambda _path: (_ for _ in ()).throw(RuntimeError("queue failed")),
        lambda: lambda: (_ for _ in ()).throw(ValueError("passive failed")),
        {"INCOMING_RECOVERY_ALLOW_QUEUE": "1"},
    )
    with pytest.raises(RuntimeError, match="queue failed") as raised:
        runner.run()
    assert isinstance(raised.value.__cause__, ValueError)


def test_passive_queue_caller_samples_before_and_during_one_post(tmp_path):
    events = []
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    def get(path):
        events.append("get")
        return responses()[path]
    def send(_path):
        assert events.count("get") >= 6  # QueueGate preflight plus first passive GET.
        events.append("post")
        return observer.QueueTransportResponse(200)
    caller = observer.PassiveQueueCaller(gate, get, send, ("/server/status",),
        tmp_path / "passive.jsonl", {"INCOMING_RECOVERY_ALLOW_QUEUE": "1"})
    assert caller.run().status == 200
    assert events.count("post") == 1


def test_passive_queue_caller_requires_persisted_second_boundary_before_post(monkeypatch, tmp_path):
    events = []

    class FakeSampler:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def sample(self, _get, *, before_request=None, stop_event=None):
            FakeSampler.calls += 1
            if before_request is None:
                events.append("first-sample")
            else:
                events.append("second-before-persisted")
                before_request()
            return []

    monkeypatch.setattr(observer, "FixedGetSampler", FakeSampler)
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    caller = observer.PassiveQueueCaller(
        gate, lambda path: responses()[path],
        lambda _path: events.append("post") or observer.QueueTransportResponse(200),
        ("/server/status",), tmp_path / "passive.jsonl", {"INCOMING_RECOVERY_ALLOW_QUEUE": "1"},
    )
    assert caller.run().status == 200
    assert events.index("second-before-persisted") < events.index("post")


def test_experimental_passive_sampler_repeats_sequentially_while_post_blocks(tmp_path):
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    status_calls = 0
    repeated = threading.Event()

    def get(path):
        nonlocal status_calls
        if path == "/server/status":
            status_calls += 1
            if status_calls >= 4:
                repeated.set()
        return responses()[path]

    def send(_path):
        assert repeated.wait(1)
        return observer.QueueTransportResponse(200)

    caller = observer.PassiveQueueCaller(
        gate, get, send, ("/server/status",), tmp_path / "passive.jsonl",
        {"INCOMING_RECOVERY_ALLOW_QUEUE": "1"}, passive_sample_interval_seconds=0.001,
    )
    assert caller.run().status == 200
    assert status_calls >= 4


def test_passive_queue_caller_stops_before_a_second_concurrent_get(tmp_path):
    passive_calls = []
    status_calls = 0
    second_get_started = threading.Event()
    release_second_get = threading.Event()
    release_timer = []
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")

    def get(path):
        nonlocal status_calls
        if path == "/server/status":
            status_calls += 1
        if path in {"/server/status", "/server/config/get"} and status_calls >= 2:
            passive_calls.append(path)
            if path == "/server/status" and status_calls == 3:
                second_get_started.set()
                assert release_second_get.wait(1)
        return responses()[path]

    def send(_path):
        assert second_get_started.wait(1)
        timer = threading.Timer(0.02, release_second_get.set)
        release_timer.append(timer)
        timer.start()
        return observer.QueueTransportResponse(200)

    caller = observer.PassiveQueueCaller(
        gate, get, send, ("/server/status", "/server/config/get"),
        tmp_path / "passive.jsonl", {"INCOMING_RECOVERY_ALLOW_QUEUE": "1"},
    )
    assert caller.run().status == 200
    for timer in release_timer:
        timer.join()
    # First synchronous sample has two GETs; the concurrent sample's first
    # GET completes after Queue and must observe stop before another begins.
    assert passive_calls == ["/server/status", "/server/config/get", "/server/status"]


def test_queue_transport_non_200_is_persisted_and_tuple_is_rejected(tmp_path):
    artifact = tmp_path / "queue.jsonl"
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_path=artifact)
    gate.preflight(lambda path: responses()[path])
    result = gate.queue(lambda _path: observer.QueueTransportResponse(409, b'{"message":"Queue is not ready"}'))
    assert result.status == 409
    assert gate.last_queue_evidence["status_code"] == 409
    assert gate.last_queue_evidence["body_reason"] == "unrecognized_409_response"
    assert 'message' not in artifact.read_text(encoding="utf-8")
    other = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    other.preflight(lambda path: responses()[path])
    with pytest.raises(observer.ObserverSchemaError, match="tuple"):
        other.queue(lambda _path: (409, 2.0, {}))
    assert other.queue_attempted is True


def test_http_adapter_maps_http_error_to_real_queue_status_once(tmp_path):
    key = tmp_path / "key"; key.write_text("private", encoding="utf-8")
    body = BytesIO(b'{"message":"private"}')
    def opener(_request, timeout):
        assert timeout == 35
        raise HTTPError("http://private", 504, "timeout", None, body)
    result = runner.HttpAdapter(key, opener).request("/server/command/queue/x", "POST")
    assert result.status == 504
    assert result.body == b'{"message":"private"}'


def test_durable_attempt_marker_precedes_transport_and_survives_restart(tmp_path):
    marker = tmp_path / "attempt.json"
    events = []
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", attempt_state_path=marker)
    gate.preflight(lambda path: responses()[path])
    gate.queue(lambda _path: events.append(marker.read_text()) or observer.QueueTransportResponse(200))
    assert events and "queue_attempt_started" in events[0]
    resumed = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", attempt_state_path=marker)
    resumed.preflight(lambda path: responses()[path])
    with pytest.raises(observer.ObserverSchemaError, match="consumed"):
        resumed.queue(lambda _path: pytest.fail("POST must not run"))


def test_durable_marker_persistence_failure_remains_consumed_for_restart(monkeypatch, tmp_path):
    marker = tmp_path / "attempt.json"
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", attempt_state_path=marker)
    gate.preflight(lambda path: responses()[path])
    monkeypatch.setattr(observer.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(observer.ObserverCaptureError, match="marker persistence"):
        gate.queue(lambda _path: pytest.fail("POST must not run"))
    assert marker.exists()
    resumed = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", attempt_state_path=marker)
    resumed.preflight(lambda path: responses()[path])
    with pytest.raises(observer.ObserverSchemaError, match="already consumed"):
        resumed.queue(lambda _path: pytest.fail("POST must not run"))


def test_confirmed_delete_gate_sanitizer_keeps_only_schema_shaped_facts():
    safe = observer._sanitize_capture_projection({
        "kind": "event", "source": "model_updater", "category": "completion.gate",
        "event_type": "diagnostic", "stage": "completion_gate", "level": "info",
        "completion_gate": {
            "boundary": "completion_gate", "message": "confirmed_local_delete_source",
            "schema": "confirmed_local_delete_source.v1", "delete_confirmed": True,
            "fence_result": False, "target_scope": "nested_path",
            "local_scan_binding_known": False, "local_generation_known": True,
            "local_generation": str(2**53 + 17), "private_path": "/private/root/file",
            "session_token": "private-session",
        },
    })["completion_gate"]
    assert safe == {
        "boundary": "completion_gate", "message": "confirmed_local_delete_source",
        "schema": "confirmed_local_delete_source.v1", "delete_confirmed": True,
        "fence_result": False, "target_scope": "nested_path",
        "local_scan_binding_known": False, "local_generation_known": True,
        "local_generation": str(2**53 + 17),
    }

    malformed = observer._sanitize_capture_projection({
        "completion_gate": {
            "boundary": "completion_gate", "message": "confirmed_delete_fence_disposition",
            "schema": "confirmed_delete_fence_disposition.v1", "decision": "released",
            "release_basis": "current_root", "captured_generation_known": True,
            "captured_generation": str(2**63), "event_generation_known": "true",
            "event_generation": "01", "local_scan_binding_known": False,
            "scan_started_at_known": True, "scan_started_at_ms": "9007199254740993",
            "root_present": True, "root_final_complete": True,
            "root_staging_complete": "true", "root_is_dir": True,
            "root_size": str(2**53 + 101), "raw_path": "/private/root",
        },
    })["completion_gate"]
    assert malformed["decision"] == "released" and malformed["release_basis"] == "current_root"
    assert malformed["captured_generation_known"] is True
    assert malformed["captured_generation"] == "unknown"
    assert malformed["event_generation_known"] is None and malformed["event_generation"] == "unknown"
    assert malformed["local_scan_binding_known"] is False
    assert malformed["scan_started_at_ms"] == "9007199254740993"
    assert malformed["root_final_complete"] is True and malformed["root_staging_complete"] is None
    assert malformed["root_size"] == str(2**53 + 101)
    assert "raw_path" not in json.dumps(malformed)

    wrong_schema = observer._sanitize_capture_projection({
        "completion_gate": {
            "boundary": "completion_gate", "message": "confirmed_local_delete_source",
            "schema": "private", "fence_result": True, "local_generation": "12",
            "private_path": "/private/root",
        },
    })["completion_gate"]
    assert wrong_schema["schema"] == "unknown"
    assert wrong_schema["fence_result"] is None and wrong_schema["local_generation"] == "unknown"
    assert "private_path" not in json.dumps(wrong_schema)
