"""Real-filesystem tests for the Linux descriptor-relative spool reader."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="G18-reader is a Linux FD primitive")

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("incoming_recovery_observer", ROOT / "incoming_recovery_observer.py")
observer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = observer
spec.loader.exec_module(observer)
spec = importlib.util.spec_from_file_location("breadcrumb_spool_capture", ROOT / "breadcrumb_spool_capture.py")
capture = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = capture
spec.loader.exec_module(capture)


def _health(path: Path, *, session: str = "session-1", lost: int = 0, rotation_count: int = 0) -> None:
    payload = {
        "schema": "breadcrumb_durable_health.v1",
        "session_id": session,
        "offline_retrieval": "valid",
        "session_boundary_written": True,
        "health_publish_ok": True,
        "state": "RUNNING",
        "lost": lost,
        "critical_lost": 0,
        "normal_lost": 0,
        "unknown": 0,
        "unresolved": 0,
        "writer_failures": 0,
        "flush_failures": 0,
        "partial_write_failures": 0,
        "health_publish_failures": 0,
        "retention_prune_failures": 0,
        "rotation_count": rotation_count,
    }
    path.joinpath("breadcrumbs.health.json").write_text(json.dumps(payload), encoding="utf-8")


def _row(*, category: str = "queue.lifecycle", event_type: str = "state_transition", stage: str = "queue_lifecycle", created: int = 7, details: dict[str, object] | None = None, label: str = "safe", source: str = "controller", identity: str = "default") -> bytes:
    return (json.dumps({
        "source": source,
        "message": label,
        "created_ms": created,
        "details": details or {},
        "metadata": {
            "category": category,
            "event_type": event_type,
            "stage": stage,
            "level": "info",
            "flow_id": f"private-flow-label-{identity}",
            "corr_id": f"private-correlation-label-{identity}",
            "file_id": f"private-file-label-{identity}",
            "path_pair_id": f"private-pair-label-{identity}",
        },
    }, separators=(",", ":")) + "\n").encode()


def _reader(path: Path, artifact: Path, **kwargs: object):
    return capture.BreadcrumbSpoolCapture(path, observer.JsonArtifactSink(artifact), **kwargs)


def _artifacts(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _prepare(tmp_path: Path) -> tuple[Path, Path]:
    spool = tmp_path / "breadcrumbs"
    spool.mkdir(parents=True)
    (spool / "breadcrumbs.jsonl").write_bytes(b"")
    _health(spool)
    return spool, tmp_path / "capture.jsonl"


def test_fd_reader_excludes_prearm_and_completes_partial_rows_across_rename(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    active = spool / "breadcrumbs.jsonl"
    active.write_bytes(_row(created=1))
    reader = _reader(spool, artifact)
    try:
        reader.start()
        with active.open("ab") as handle:
            handle.write(_row(created=2)[:40])
        assert reader.poll() == 0
        with active.open("ab") as handle:
            handle.write(_row(created=2)[40:])
        assert reader.poll() == 1
        active.rename(spool / "breadcrumbs-100-1.jsonl")
        active.write_bytes(_row(event_type="completion", stage="terminal", created=3))
        assert reader.poll() == 1
        assert reader.poll() == 0
    finally:
        reader.close()
    records = _artifacts(artifact)
    projections = [item["capture"]["projection"] for item in records if item["capture"].get("state") == "record"]
    assert [item.get("numbers", {}) for item in projections] == [{}, {}]
    assert {item["kind"] for item in projections} == {"event", "completion"}
    assert all(len(item["flow_hash"]) == 16 for item in projections)
    assert "private-flow-label" not in artifact.read_text(encoding="utf-8")


def test_entire_active_rotation_between_polls_reads_old_fd_and_new_active(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    active = spool / "breadcrumbs.jsonl"
    reader = _reader(spool, artifact)
    try:
        reader.start()
        active.write_bytes(_row(created=11))
        active.rename(spool / "breadcrumbs-200-1.jsonl")
        active.write_bytes(_row(created=12))
        assert reader.poll() == 2
        rotated = spool / "breadcrumbs-200-1.jsonl"
        with rotated.open("ab") as handle:
            handle.write(_row(created=13))
        rotated.unlink()
        assert reader.poll() == 1
    finally:
        reader.close()
    created = [item["capture"]["created_ms"] for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    assert created == [11, 12, 13]


def test_unseen_rotated_file_is_explicit_loss(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs-999-1.jsonl").write_bytes(_row())
        with pytest.raises(observer.ObserverCaptureError):
            reader.poll()
    finally:
        reader.close()
    losses = [item["capture"] for item in _artifacts(artifact) if item["capture"].get("state") == "loss"]
    assert losses and losses[-1]["reason"] == "unseen_rotation"


def test_arm_rejects_over_cap_before_opening_files(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    (spool / "breadcrumbs-1-1.jsonl").write_bytes(b"")
    (spool / "breadcrumbs-2-2.jsonl").write_bytes(b"")
    reader = _reader(spool, artifact, max_files=2)
    try:
        with pytest.raises(observer.ObserverCaptureError):
            reader.start()
        assert not reader._files
    finally:
        reader.close()


def test_held_fd_survives_unlink_and_ancestor_replacement(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    original = spool
    reader = _reader(spool, artifact)
    try:
        reader.start()
        moved = tmp_path / "moved-breadcrumbs"
        original.rename(moved)
        replacement = tmp_path / "breadcrumbs"
        replacement.mkdir()
        (replacement / "breadcrumbs.jsonl").write_bytes(b"")
        _health(replacement, session="replacement")
        (moved / "breadcrumbs.jsonl").write_bytes(_row(created=20))
        assert reader.poll() == 1
    finally:
        reader.close()
    assert "replacement" not in artifact.read_text(encoding="utf-8")


def test_symlink_and_nonregular_candidates_fail_closed(tmp_path: Path) -> None:
    spool = tmp_path / "breadcrumbs"
    spool.mkdir()
    target = tmp_path / "outside.jsonl"
    target.write_bytes(b"")
    (spool / "breadcrumbs.jsonl").symlink_to(target)
    _health(spool)
    reader = _reader(spool, tmp_path / "capture.jsonl")
    try:
        with pytest.raises(observer.ObserverCaptureError):
            reader.start()
    finally:
        reader.close()

    spool2 = tmp_path / "nonregular"
    spool2.mkdir()
    (spool2 / "breadcrumbs.jsonl").mkdir()
    _health(spool2)
    reader2 = _reader(spool2, tmp_path / "nonregular-capture.jsonl")
    try:
        with pytest.raises(observer.ObserverCaptureError):
            reader2.start()
    finally:
        reader2.close()


def test_marker_and_session_races_are_reported(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs.health.invalid").write_text("marker", encoding="utf-8")
        with pytest.raises(observer.ObserverCaptureError):
            reader.poll()
    finally:
        reader.close()
    spool2, artifact2 = _prepare(tmp_path / "second")
    reader2 = _reader(spool2, artifact2)
    try:
        reader2.start()
        _health(spool2, session="session-2")
        with pytest.raises(observer.ObserverCaptureError):
            reader2.poll()
    finally:
        reader2.close()

    spool3, artifact3 = _prepare(tmp_path / "missing-health")
    reader3 = _reader(spool3, artifact3)
    try:
        reader3.start()
        (spool3 / "breadcrumbs.health.json").unlink()
        with pytest.raises(observer.ObserverCaptureError):
            reader3.poll()
    finally:
        reader3.close()
    losses = [item["capture"] for item in _artifacts(artifact3) if item["capture"].get("state") == "loss"]
    assert losses and losses[-1]["reason"] == "unread"


@pytest.mark.parametrize("mutation", ["missing", "publish", "type"])
def test_arm_rejects_malformed_or_missing_authoritative_health(tmp_path: Path, mutation: str) -> None:
    spool, artifact = _prepare(tmp_path / mutation)
    health_path = spool / "breadcrumbs.health.json"
    payload = json.loads(health_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        del payload["lost"]
    elif mutation == "publish":
        payload["health_publish_ok"] = False
    else:
        payload["unknown"] = True
    health_path.write_text(json.dumps(payload), encoding="utf-8")
    reader = _reader(spool, artifact)
    try:
        with pytest.raises(observer.ObserverCaptureError):
            reader.start()
    finally:
        reader.close()


def test_health_loss_is_delta_and_budget_is_not_loss(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    _health(spool, lost=9)
    reader = _reader(spool, artifact, max_bytes=8)
    try:
        reader.start()
        (spool / "breadcrumbs.jsonl").write_bytes(_row(created=30))
        reader.poll()
    finally:
        reader.close()
    records = _artifacts(artifact)
    assert records[0]["capture"]["lost"] == 0
    assert any(item["capture"].get("reason") == "budget" for item in records)
    assert not any(item["capture"].get("state") == "loss" for item in records)

    spool2, artifact2 = _prepare(tmp_path / "delta")
    _health(spool2, lost=9)
    reader2 = _reader(spool2, artifact2)
    try:
        reader2.start()
        _health(spool2, lost=10)
        with pytest.raises(observer.ObserverCaptureError):
            reader2.poll()
    finally:
        reader2.close()
    loss = [item["capture"] for item in _artifacts(artifact2) if item["capture"].get("state") == "loss"][-1]
    assert loss["reason"] == "health" and loss["lost"] == 1


def test_typed_projection_preserves_dotted_fields_and_numeric_values_only(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    reader = _reader(spool, artifact)
    try:
        reader.start()
        active = spool / "breadcrumbs.jsonl"
        with active.open("ab") as handle:
            handle.write(_row(category="model.progress", event_type="progress", stage="model_progress", details={"processed": 7, "total": 9, "percent": 77.5, "secret_count": 99}, label="raw-private-message") )
            handle.write(_row(category="private.category", label="private-category-label"))
        assert reader.poll() == 1
    finally:
        reader.close()
    text = artifact.read_text(encoding="utf-8")
    assert "raw-private-message" not in text
    assert "private-category-label" not in text
    records = _artifacts(artifact)
    projection = next(item["capture"]["projection"] for item in records if item["capture"].get("state") == "record")
    assert projection["category"] == "model.progress"
    assert projection["stage"] == "model_progress"
    assert projection["numbers"] == {"percent": 77.5, "processed": 7, "total": 9}
    unsupported = [item["capture"] for item in records if item["capture"].get("state") == "unsupported"]
    assert unsupported and unsupported[-1]["irrelevant"] == 1


def test_composed_producer_rows_retain_finite_lifecycle_facts_and_hashes(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    rows = [
        _row(
            details={"schema": "queue.lifecycle.v1", "event": "queue_admitted", "source": "controller", "phase": "admission", "outcome": "accepted", "error_class": "none", "boundary": "queue_admission", "command_kind": "queue"},
            identity="admission",
        ),
        _row(
            details={"schema": "queue.lifecycle.v1", "event": "executor_return", "source": "controller", "phase": "executor", "outcome": "returned", "error_class": "none", "boundary": "executor", "process_alive_after": True},
            identity="executor",
        ),
        _row(
            details={"schema": "queue.lifecycle.v1", "event": "status_membership", "source": "controller", "phase": "status", "outcome": "present", "error_class": "none", "boundary": "status_membership", "membership": "present", "status_health": "healthy"},
            identity="status",
        ),
        _row(
            category="completion.gate", event_type="diagnostic", stage="completion_gate", source="model_updater",
            details={"decision": "deferred", "reason": "no_model_diff", "terminal_outcome": "deferred", "pending_transition": "retained", "explicit_stop": False},
            identity="completion",
        ),
        _row(
            category="transfer.lftp.command", event_type="state_transition", stage="lftp_command_boundary", source="lftp",
            details={"schema": "lftp.command_boundary.v1", "transfer_kind": "get", "phase": "submitted", "process_alive": True, "output_class": "empty", "argument_count_bucket": "2-4", "exclude_count_bucket": "0", "submission_byte_length_bucket": "128-511"},
            identity="command",
        ),
    ]
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs.jsonl").write_bytes(b"".join(rows))
        assert reader.poll() == 5
    finally:
        reader.close()
    records = [item["capture"]["projection"] for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    lifecycle = [item["lifecycle"] for item in records if "lifecycle" in item]
    assert [(item["event"], item["outcome"], item["boundary"]) for item in lifecycle] == [
        ("queue_admitted", "accepted", "queue_admission"),
        ("executor_return", "returned", "executor"),
        ("status_membership", "present", "status_membership"),
    ]
    completion = next(item["completion_gate"] for item in records if "completion_gate" in item)
    assert completion["boundary"] == "completion_gate" and completion["terminal_outcome"] == "deferred"
    command = next(item["lftp_command"] for item in records if "lftp_command" in item)
    assert command["transfer_kind"] == "get" and command["phase"] == "submitted"
    hashes = [item["flow_hash"] for item in records]
    assert len(set(hashes)) == 5
    text = artifact.read_text(encoding="utf-8")
    assert all(label not in text for label in ("private-flow-label", "private-correlation-label", "private-file-label", "private-pair-label"))


def test_malformed_and_oversized_lines_are_explicit_loss(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs.jsonl").write_bytes(b"{not-json}\n")
        with pytest.raises(observer.ObserverCaptureError):
            reader.poll()
    finally:
        reader.close()
    assert [item["capture"]["reason"] for item in _artifacts(artifact) if item["capture"].get("state") == "loss"][-1] == "malformed"

    spool2, artifact2 = _prepare(tmp_path / "oversized")
    reader2 = _reader(spool2, artifact2, max_tail_bytes=4)
    try:
        reader2.start()
        (spool2 / "breadcrumbs.jsonl").write_bytes(b"12345")
        with pytest.raises(observer.ObserverCaptureError):
            reader2.poll()
    finally:
        reader2.close()
    assert [item["capture"]["reason"] for item in _artifacts(artifact2) if item["capture"].get("state") == "loss"][-1] == "oversized"


def test_output_record_cap_reserves_terminal_receipt(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    reader = _reader(spool, artifact, max_records=4)
    try:
        reader.start()
        with (spool / "breadcrumbs.jsonl").open("ab") as handle:
            handle.write(_row(created=1) + _row(created=2) + _row(created=3))
        with pytest.raises(observer.ObserverCaptureError):
            reader.poll()
        reader.stop()
    finally:
        reader.close()
    records = _artifacts(artifact)
    assert len(records) <= 4
    assert records[-1]["capture"]["state"] == "loss"
    assert records[-1]["capture"]["reason"] == "output"


def test_stop_requires_fresh_marker_session_loss_and_state_checks(tmp_path: Path) -> None:
    cases = ("marker", "session", "loss", "state")
    for case in cases:
        spool, artifact = _prepare(tmp_path / case)
        reader = _reader(spool, artifact)
        try:
            reader.start()
            health_path = spool / "breadcrumbs.health.json"
            payload = json.loads(health_path.read_text(encoding="utf-8"))
            if case == "marker":
                (spool / "breadcrumbs.health.invalid").write_text("marker", encoding="utf-8")
            elif case == "session":
                payload["session_id"] = "different-session"
                health_path.write_text(json.dumps(payload), encoding="utf-8")
            elif case == "loss":
                payload["lost"] += 1
                health_path.write_text(json.dumps(payload), encoding="utf-8")
            else:
                payload["state"] = "BROKEN"
                health_path.write_text(json.dumps(payload), encoding="utf-8")
            with pytest.raises(observer.ObserverCaptureError):
                reader.stop(health={"session_id": "stale-caller-snapshot"})
        finally:
            reader.close()
        records = _artifacts(artifact)
        assert records[-1]["capture"]["state"] == "loss"
        assert not any(item["capture"].get("state") == "stopped" for item in records)


def test_stop_persists_only_after_fresh_valid_postchecks(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    reader = _reader(spool, artifact)
    reader.start()
    reader.stop(health={"session_id": "stale-caller-snapshot"})
    records = _artifacts(artifact)
    assert records[-1]["capture"]["state"] == "stopped"


def test_stop_does_not_claim_completion_with_unread_or_partial_data(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs.jsonl").write_bytes(_row()[:25])
        reader.poll()
        with pytest.raises(observer.ObserverCaptureError):
            reader.stop()
    finally:
        reader.close()
    records = _artifacts(artifact)
    assert records[-1]["capture"]["state"] == "loss"
    assert records[-1]["capture"]["reason"] == "partial"
    assert not any(item["capture"].get("state") == "stopped" for item in records)


def test_time_budget_is_reported_without_claiming_loss(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    reader = _reader(spool, artifact, max_millis=1, max_bytes=1048576, max_records=4096, max_output_bytes=4 * 1024 * 1024)
    try:
        reader.start()
        with (spool / "breadcrumbs.jsonl").open("ab") as handle:
            handle.write(b"".join(_row(created=index) for index in range(1, 1000)))
        reader.poll()
    finally:
        reader.close()
    records = _artifacts(artifact)
    assert any(item["capture"].get("reason") == "budget" for item in records)
    assert not any(item["capture"].get("state") == "loss" for item in records)


def test_queue_readiness_source_projection_is_finite_and_redacted(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    row = _row(
        category="queue.readiness", event_type="callback", stage="queue_readiness", label="queue_callback",
        details={
            "schema": "queue_readiness.v1", "phase": "entry",
            "outcome": "failure", "origin": "manual", "callback_index": 0,
            "callback_count": 1, "error_code": 503, "private_detail": "do-not-copy",
        },
    )
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs.jsonl").write_bytes(row)
        assert reader.poll() == 1
    finally:
        reader.close()
    text = artifact.read_text(encoding="utf-8")
    assert "do-not-copy" not in text
    projection = next(item["capture"]["projection"] for item in _artifacts(artifact) if item["capture"].get("state") == "record")
    assert projection["queue_readiness"] == {
        "schema": "queue_readiness.v1", "event": "queue_callback", "phase": "entry",
        "outcome": "failure", "origin": "manual", "reason": "unknown", "callback_index": 0,
        "callback_count": 1, "error_code": 503,
    }


def test_model_page_snapshot_source_projection_is_finite_and_redacted(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    row = _row(
        category="model_page_snapshot", event_type="diagnostic", stage="model_page_snapshot",
        details={
            "scope_version": 4, "global_version": 9, "lock_wait_ms": 1,
            "lock_hold_ms": 2, "snapshot_elapsed_ms": 3, "outcome": "success",
            "secret_detail": "do-not-copy",
        }, label="private-message",
    )
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs.jsonl").write_bytes(row)
        assert reader.poll() == 1
    finally:
        reader.close()
    text = artifact.read_text(encoding="utf-8")
    assert "private-message" not in text and "do-not-copy" not in text
    projection = next(item["capture"]["projection"] for item in _artifacts(artifact) if item["capture"].get("state") == "record")
    assert projection["model_page_snapshot"] == {
        "outcome": "success", "scope_version": 4, "global_version": 9,
        "lock_wait_ms": 1, "lock_hold_ms": 2, "snapshot_elapsed_ms": 3,
    }


def test_poll_drains_beyond_single_eight_kib_chunk_with_budget_receipt(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    payload = b"".join(_row(created=index, details={"bytes_done": index, "bytes_total": 100000}) for index in range(1, 220))
    assert len(payload) > 8192
    reader = _reader(spool, artifact, max_bytes=64 * 1024, max_millis=250, max_records=512, max_output_bytes=512 * 1024)
    try:
        reader.start()
        # Bytes present before H0 are history and intentionally excluded.
        (spool / "breadcrumbs.jsonl").write_bytes(payload)
        reader.poll()
        projection = reader.projection()
    finally:
        reader.close()
    assert projection["source_bytes"] > 8192
    assert projection["source_bytes"] <= 64 * 1024
