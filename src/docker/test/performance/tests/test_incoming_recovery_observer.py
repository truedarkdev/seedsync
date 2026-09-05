from pathlib import Path
import importlib.util
from io import BytesIO
import sys
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
