from __future__ import annotations

import http.server
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="G19.3 is a Linux root-only FD capture")


SPEC = importlib.util.spec_from_file_location(
    "incoming_queue_capture_runner", Path(__file__).parents[1] / "incoming_queue_capture_runner.py",
)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


@pytest.fixture(autouse=True)
def _short_supported_window(monkeypatch):
    monkeypatch.setattr(runner, "SUPPORTED_PYTHON", sys.version_info[:2], raising=False)
    monkeypatch.setattr(runner, "PASSIVE_WINDOW_SECONDS", 0.08, raising=False)
    monkeypatch.setattr(runner, "POLL_CADENCE_SECONDS", 0.01, raising=False)


PAIR = "pair-1"
ROOT = "root-1"
LEAF = "leaf-1"
LEAF_PATH = "/server/model/v1/pairs/pair-1/children?parent_file_id=root-1&limit=200"


def _health(path: Path, session: str = "session-1", *, lost: int = 0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "breadcrumbs.health.json").write_text(json.dumps({
        "schema": "breadcrumb_durable_health.v1", "session_id": session,
        "state": "RUNNING", "offline_retrieval": "valid", "health_publish_ok": True,
        "session_boundary_written": True, "lost": lost, "critical_lost": 0, "normal_lost": 0,
        "unknown": 0, "unresolved": 0, "writer_failures": 0, "flush_failures": 0,
        "partial_write_failures": 0, "health_publish_failures": 0,
        "retention_prune_failures": 0, "rotation_count": 0,
    }), encoding="utf-8")


def _spool(tmp_path: Path, *, record: bytes = b"") -> Path:
    path = tmp_path / "breadcrumbs"
    _health(path)
    (path / "breadcrumbs.jsonl").write_bytes(record)
    return path


def _payload(version: int = 7) -> dict[str, object]:
    return {
        "path_pair_id": PAIR, "model_version": version, "records": [{
            "file_id": LEAF, "name": "leaf.bin", "path_pair_id": PAIR,
            "is_dir": False, "has_children": False, "children": [], "state": "active", "size": 0,
            "local_size": 0, "remote_size": 12, "transferred_size": 5, "display_size_total": 12,
            "display_transferred_size": 5, "download_progress": 41, "downloading_speed": 7, "eta": 3,
            "progress": 99, "bytes_done": 1, "bytes_total": 12, "private_detail": "private-response-body",
            "complete_local_coverage": False,
            "remote_present": True, "local_present": True,
        }],
    }


class Response:
    def __init__(self, status: int, body: bytes):
        self.status, self.body, self.headers = status, body, {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int = -1):
        return self.body if _size < 0 else self.body[:_size]


def _config(tmp_path: Path, spool: Path, **overrides):
    key = tmp_path / "key"
    key.write_text("private-credential", encoding="utf-8")
    key.chmod(0o600)
    data = {
        "selector": {"pair": {"id": PAIR, "name": "Pair"}, "root": {"id": ROOT, "name": "Incoming"}, "leaf_id": LEAF, "leaf_name": "leaf.bin"},
        "key_path": str(key), "spool_path": str(spool), "artifact_path": str(tmp_path / "capture.jsonl"),
    }
    data.update(overrides)
    return runner.Config.from_mapping(data)


def _trace(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _opener(calls: list[str], *, versions: list[int] | None = None, release: threading.Event | None = None):
    versions = versions or [7]
    count = [0]

    def open_request(request, timeout):
        assert request.method == "GET"
        path = request.full_url.split("8800", 1)[-1]
        assert path == LEAF_PATH
        calls.append(path)
        index = min(count[0], len(versions) - 1)
        count[0] += 1
        if release is not None and count[0] >= 2:
            assert release.wait(1)
        return Response(200, json.dumps(_payload(versions[index])).encode())

    return open_request


def test_root_only_and_supported_runtime_preflight(monkeypatch, tmp_path):
    config = _config(tmp_path, _spool(tmp_path))
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(runner.CaptureError, match="root-only"):
        runner.Runner(config).run()
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(runner, "SUPPORTED_PYTHON", (99, 99))
    with pytest.raises(runner.CaptureError, match="supported runtime"):
        runner.Runner(config).run()


def test_help_is_generic_private_config_documentation(capsys):
    with pytest.raises(SystemExit):
        runner._main(["--help"])
    output = capsys.readouterr().out
    assert "protected private JSON configuration" in output
    assert "private-credential" not in output and "pair-1" not in output


def test_passive_get_is_whitelisted_and_persists_wire_times_without_secrets(monkeypatch, tmp_path):
    spool = _spool(tmp_path)
    config = _config(tmp_path, spool)
    calls: list[str] = []
    monkeypatch.setenv("NO_QUEUE_ACTION_ENV", "unused")
    result = runner.Runner(config, opener=_opener(calls, versions=[7, 8])).run()
    assert calls and all(path == LEAF_PATH for path in calls)
    assert result["physical_outcome"] == "not_configured"
    records = _trace(config.artifact_path)
    invoked = next(item for item in records if item["event"] == "model_get_invoked")
    response = next(item for item in records if item["event"] == "model_get_response")
    assert isinstance(invoked["invocation_utc_ms"], int)
    assert isinstance(invoked["invocation_monotonic_ms"], int)
    assert isinstance(response["response_utc_ms"], int)
    assert isinstance(response["response_monotonic_ms"], int)
    assert response["model_version"] == 7
    started = next(item for item in records if item["event"] == "capture_started")
    terminal = next(item for item in records if item["event"] == "terminal")
    leaf = next(item for item in records if item["event"] == "leaf_receipt")
    assert records.index(started) < records.index(leaf)
    assert terminal["capture_start_utc_ms"] == started["capture_start_utc_ms"]
    assert terminal["capture_start_monotonic_ms"] == started["capture_start_monotonic_ms"]
    assert terminal["capture_end_utc_ms"] >= terminal["capture_start_utc_ms"]
    assert terminal["capture_end_monotonic_ms"] >= terminal["capture_start_monotonic_ms"]
    text = config.artifact_path.read_text(encoding="utf-8")
    assert "private-credential" not in text and "pair-1" not in text and "leaf-1" not in text
    assert not any("POST" in item for item in calls)


def test_identity_hash_excludes_mutable_state_and_wire_model_version_is_projected(monkeypatch, tmp_path):
    spool = _spool(tmp_path)
    config = _config(tmp_path, spool)
    calls: list[str] = []
    result = runner.Runner(config, opener=_opener(calls, versions=[7, 8])).run()
    records = _trace(config.artifact_path)
    receipts = [item for item in records if item["event"] in {"leaf_receipt", "model_snapshot"}]
    assert len(receipts) >= 2
    assert len({item["leaf_hash"] for item in receipts}) == 1
    assert len({item["selector_hash"] for item in receipts}) == 1
    assert next(item for item in receipts if item["event"] == "leaf_receipt")["model_version"] == 7
    model_event = next(item for item in receipts if item["event"] == "model_snapshot")
    assert model_event["model_version"] == 8
    assert model_event["model_snapshot"]["state"] == "active"
    assert model_event["model_snapshot"]["local_size"] == 0
    assert model_event["model_snapshot"]["remote_size"] == 12
    assert model_event["model_snapshot"]["complete_local_coverage"] is False
    assert {field: model_event["model_snapshot"][field] for field in (
        "transferred_size", "display_size_total", "display_transferred_size",
        "download_progress", "downloading_speed", "eta",
    )} == {
        "transferred_size": 5, "display_size_total": 12, "display_transferred_size": 5,
        "download_progress": 41, "downloading_speed": 7, "eta": 3,
    }
    assert all(field not in model_event["model_snapshot"] for field in (
        "progress", "bytes_done", "bytes_total", "private_detail",
    ))
    assert result["completion_proven"] is False


def test_model_snapshot_rejects_nonfinite_wire_numbers_and_private_fields():
    identity = {"selector_hash": "0123456789abcdef", "leaf_hash": "fedcba9876543210"}
    valid = {
        "state": "active", "local_size": 0, "remote_size": 12,
        "transferred_size": 5, "display_size_total": 12, "display_transferred_size": 5,
        "download_progress": 41, "downloading_speed": 7, "eta": 3,
    }
    snapshot = runner._model_snapshot(identity, 8, {**valid, "private_detail": "private-response-body"})
    assert all(snapshot[field] == valid[field] for field in (
        "transferred_size", "display_size_total", "display_transferred_size",
        "download_progress", "downloading_speed", "eta",
    ))
    assert "private_detail" not in snapshot
    for field, malformed in (
        ("transferred_size", True), ("display_size_total", -1),
        ("display_transferred_size", 1.5), ("download_progress", 101),
        ("downloading_speed", -1), ("eta", 1.5),
    ):
        rejected = runner._model_snapshot(identity, 8, {**valid, field: malformed})
        assert field not in rejected
    sanitized = runner._observer._sanitize_artifact({
        "model_snapshot": {
            **valid, "model_version": 8, "private_detail": "private-response-body",
            "progress": 99, "bytes_done": 1, "bytes_total": 12,
            "download_progress": 101,
        },
    })
    safe_snapshot = sanitized["model_snapshot"]
    assert {field: safe_snapshot[field] for field in (
        "transferred_size", "display_size_total", "display_transferred_size",
        "downloading_speed", "eta",
    )} == {
        "transferred_size": 5, "display_size_total": 12, "display_transferred_size": 5,
        "downloading_speed": 7, "eta": 3,
    }
    assert "download_progress" not in safe_snapshot
    assert all(field not in safe_snapshot for field in (
        "progress", "bytes_done", "bytes_total", "private_detail",
    ))


def test_real_local_http_and_durable_writer_drain_while_one_leaf_get_waits(monkeypatch, tmp_path):
    spool = _spool(tmp_path)
    poll_seen = threading.Event()
    release = threading.Event()
    writer_done = threading.Event()
    calls: list[str] = []
    count = [0]
    spool_path = spool / "breadcrumbs.jsonl"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler API
            assert self.path == LEAF_PATH
            calls.append(self.path)
            count[0] += 1
            if count[0] >= 2:
                poll_seen.set()
                assert release.wait(1)
            body = json.dumps(_payload(7 + count[0])).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def writer():
        assert poll_seen.wait(1)
        with spool_path.open("ab") as handle:
            handle.write((json.dumps({
                "source": "controller", "message": "queue_callback", "created_ms": 1,
                "details": {"schema": "queue_readiness.v1", "phase": "entry", "outcome": "accepted", "private_detail": "redact"},
                "metadata": {"category": "queue.readiness", "event_type": "callback", "stage": "queue_readiness", "level": "info", "corr_id": "private-correlation"},
            }) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        writer_done.set()
        release.set()

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    try:
        config = _config(tmp_path, spool, api_base_url=f"http://127.0.0.1:{server.server_port}")
        result = runner.Runner(config).run()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        server_thread.join(2)
        writer_thread.join(2)
    assert writer_done.is_set() and calls
    assert result["reader_outcome"] == "healthy"
    records = _trace(config.artifact_path)
    projections = [item["capture"]["projection"] for item in records if item.get("capture", {}).get("projection")]
    readiness = next(item["queue_readiness"] for item in projections if "queue_readiness" in item)
    assert readiness["event"] == "queue_callback"
    assert "private-correlation" not in config.artifact_path.read_text(encoding="utf-8")


def test_failure_after_sink_creation_writes_fixed_terminal_without_raw_error(monkeypatch, tmp_path):
    spool = _spool(tmp_path)
    config = _config(tmp_path, spool)

    def failing_opener(request, timeout):
        assert request.method == "GET"
        raise TimeoutError("private transport detail")

    with pytest.raises(TimeoutError):
        runner.Runner(config, opener=failing_opener).run()
    text = config.artifact_path.read_text(encoding="utf-8")
    assert "private transport detail" not in text
    terminal = _trace(config.artifact_path)[-1]
    assert terminal["event"] == "terminal"
    assert terminal["phase"] == "finished"
    assert terminal["completion_proven"] is False
    records = _trace(config.artifact_path)
    started = next(item for item in records if item["event"] == "capture_started")
    assert terminal["capture_start_utc_ms"] == started["capture_start_utc_ms"]
    assert terminal["capture_end_utc_ms"] >= terminal["capture_start_utc_ms"]
    assert terminal["capture_end_monotonic_ms"] >= terminal["capture_start_monotonic_ms"]


def test_config_digest_is_immutable_during_passive_window(monkeypatch, tmp_path):
    spool = _spool(tmp_path)
    key = tmp_path / "key"
    key.write_text("private-credential", encoding="utf-8")
    key.chmod(0o600)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "selector": {"pair": {"id": PAIR, "name": "Pair"}, "root": {"id": ROOT, "name": "Incoming"}, "leaf_id": LEAF, "leaf_name": "leaf.bin"},
        "key_path": str(key), "spool_path": str(spool), "artifact_path": str(tmp_path / "capture.jsonl"),
    }), encoding="utf-8")
    config_path.chmod(0o600)
    config = runner.Config.from_path(config_path)
    changed = [False]

    def opener(request, timeout):
        if not changed[0]:
            changed[0] = True
            config_path.write_text(config_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return Response(200, json.dumps(_payload()).encode())

    with pytest.raises(runner.CaptureError, match="protected config changed"):
        runner.Runner(config, opener=opener).run()
    assert _trace(config.artifact_path)[-1]["event"] == "terminal"


def test_missing_physical_is_observed_distinct_from_not_configured(monkeypatch, tmp_path):
    spool = _spool(tmp_path)
    config = _config(tmp_path, spool, physical_path=str(tmp_path / "not-present.bin"))
    result = runner.Runner(config, opener=_opener([])).run()
    assert result["physical_outcome"] == "absent"
