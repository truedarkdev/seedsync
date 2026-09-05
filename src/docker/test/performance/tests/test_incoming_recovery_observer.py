from pathlib import Path
import importlib.util
import sys
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
        "/server/model/v1/pairs/pair-1/roots?limit=200": {"records": [{"file_id": ROOT, "name": "Incoming", "state": "default"}]},
        "/server/status": {"controller": {"latest_local_scan_time": "1"}},
    }


def test_preflight_uses_real_envelopes_and_never_sends_queue():
    calls = []
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming")
    evidence = gate.preflight(lambda path: calls.append(path) or responses()[path])
    assert evidence["root_found"] is True
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
    assert observer.error_artifact(observer.ObserverSchemaError("x"))["schema"] == "incoming-recovery-observer-error.v1"
