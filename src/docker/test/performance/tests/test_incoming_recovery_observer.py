from pathlib import Path
import importlib.util
from io import BytesIO
import json
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
    gate.queue(lambda path: sent.append(path) or object())
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

    with pytest.raises(TimeoutError):
        gate.queue(send)
    assert gate.queue_attempted is True
    assert gate.capture_failure == {
        "schema": "incoming-recovery-observer-capture-failure.v1",
        "phase": "queue_failure", "kind": "artifact_sink_failure",
    }
    with pytest.raises(observer.ObserverSchemaError, match="already attempted"):
        gate.queue(send)
    assert len(sent) == 1
    assert "timeout-private-detail" not in str(gate.capture_failure)


def test_failing_sink_does_not_mask_queue_http_error():
    gate = observer.QueueGate(PAIR, "Pr0n", ROOT, "Incoming", artifact_sink=[])
    gate.preflight(lambda path: responses()[path])
    gate.artifact_sink = FailingSink()
    sent = []

    def send(path):
        sent.append(path)
        raise HTTPError("http://private", 409, "Conflict", None, BytesIO(b"private-body"))

    with pytest.raises(HTTPError):
        gate.queue(send)
    assert gate.queue_attempted is True
    assert gate.capture_failure == {
        "schema": "incoming-recovery-observer-capture-failure.v1",
        "phase": "queue_failure", "kind": "artifact_sink_failure",
    }
    with pytest.raises(observer.ObserverSchemaError, match="already attempted"):
        gate.queue(send)
    assert len(sent) == 1


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
        return 200

    runner = observer.GuardedQueueRunner(gate, get, send, start_passive,
        {"INCOMING_RECOVERY_ALLOW_QUEUE": "1"})
    assert runner.run() == 200
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
