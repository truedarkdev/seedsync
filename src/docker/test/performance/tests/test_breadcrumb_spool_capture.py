"""Real-filesystem tests for the Linux descriptor-relative spool reader."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
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

_COMMON_ROOTS = [Path("/src/python")]
_COMMON_ROOTS.extend(parent / "src/python" for parent in Path(__file__).resolve().parents)
for _common_root in _COMMON_ROOTS:
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
        "category": category,
        "event_type": event_type,
        "stage": stage,
        "level": "info",
        "flow_id": f"private-flow-label-{identity}",
        "corr_id": f"private-correlation-label-{identity}",
        "file_id": f"private-file-label-{identity}",
        "path_pair_id": f"private-pair-label-{identity}",
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


def test_automatic_move_boundary_finalized_rows_preserve_finite_facts_and_order(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    dimensions = [
        "scanner_authority", "pending_job", "private/path", "scanner_authority",
        "candidate_file", "unexpected_private_value", {"sensitive": "nested-private-value"},
    ]
    rows = [
        _row(category="completion.gate", label="automatic_move_copy_boundary", created=41,
             identity="repeat", details={"schema": "automatic_move_copy_boundary.v1", "phase": "prepublish_gate",
             "reason": "stale_authority", "decision": "deferred", "changed_dimensions": dimensions,
             "path": "/private/secret", "file_name": "private.bin"}),
        _row(category="completion.gate", label="automatic_move_copy_boundary", created=42,
             identity="repeat", details={"schema": "automatic_move_copy_boundary.v1", "phase": "copy_yield",
             "decision": "started", "duration_ms": 999, "source": "private/path"}),
        _row(category="completion.gate", label="automatic_move_copy_boundary", created=43,
             identity="repeat", details={"schema": "automatic_move_copy_boundary.v1", "phase": "copy_reacquired",
             "reason": "published", "decision": "ready", "duration_ms": 9187,
             "changed_dimensions": ["scanner_authority"]}),
    ]
    reader = _reader(spool, artifact)
    try:
        reader.start()
        with (spool / "breadcrumbs.jsonl").open("ab") as handle:
            handle.write(b"".join(rows))
        assert reader.poll() == 3
    finally:
        reader.close()
    captured = [item["capture"] for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    records = [item["projection"] for item in captured]
    assert all("automatic_move_copy_boundary" in item for item in records)
    facts = [item["automatic_move_copy_boundary"] for item in records]
    assert [item["phase"] for item in facts] == ["prepublish_gate", "copy_yield", "copy_reacquired"]
    assert facts[0] == {
        "schema": "automatic_move_copy_boundary.v1", "phase": "prepublish_gate",
        "decision": "deferred", "reason": "stale_authority",
        "changed_dimensions": ["scanner_authority", "pending_job", "candidate_file"],
    }
    assert facts[1] == {"schema": "automatic_move_copy_boundary.v1", "phase": "copy_yield", "decision": "started"}
    assert facts[2] == {
        "schema": "automatic_move_copy_boundary.v1", "phase": "copy_reacquired",
        "decision": "ready", "reason": "published",
    }
    assert records[2]["numbers"] == {"duration_ms": 9187}
    assert [item["created_ms"] for item in captured] == [41, 42, 43]
    assert len({item["corr_hash"] for item in records}) == 1
    serialized = json.dumps(records, sort_keys=True)
    for secret in ("/private/secret", "private.bin", "private/path", "unexpected_private_value", "nested-private-value"):
        assert secret not in serialized


@pytest.mark.parametrize("details_and_expected", [
    ({"schema": "automatic_move_copy_boundary.v2", "phase": "copy_yield", "decision": "started"}, None),
    ({"schema": "automatic_move_copy_boundary.v1", "phase": "copy_yield", "decision": "published"}, None),
    ({"schema": "automatic_move_copy_boundary.v1", "phase": "copy_reacquired", "decision": "ready", "reason": "secret", "duration_ms": -1}, {
        "schema": "automatic_move_copy_boundary.v1", "phase": "copy_reacquired", "decision": "ready",
    }),
])
def test_automatic_move_boundary_rejects_unknown_or_malformed_envelope(
    tmp_path: Path, details_and_expected: tuple[dict[str, object], dict[str, object] | None],
) -> None:
    details, expected = details_and_expected
    row = json.loads(_row(category="completion.gate", label="automatic_move_copy_boundary", details=details))
    projection, irrelevant = capture._projection(row)
    assert not irrelevant and projection is not None
    assert projection.get("automatic_move_copy_boundary") == expected


def test_delete_local_command_projection_is_bounded_typed_and_private(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    rows = [
        _row(
            category="controller", event_type="completion", stage="command",
            label="command_finished", identity="delete-success",
            details={
                "command": "DELETE_LOCAL", "delete_local_planned_location_count": 2,
                "delete_local_removed_location_count": 2, "delete_local_result_known": True,
                "delete_local_partial_failure": False, "file_name": "private-name.bin",
                "local_path": "/private/location",
            },
        ),
        _row(
            category="controller", event_type="failure", stage="command",
            label="command_failed", identity="delete-partial",
            details={
                "command": "DELETE_LOCAL", "delete_local_planned_location_count": 2,
                "delete_local_removed_location_count": 1, "delete_local_result_known": True,
                "delete_local_partial_failure": True,
            },
        ),
        _row(
            category="controller", stage="command", label="command_finished", identity="delete-unknown",
            details={
                "command": "DELETE_LOCAL", "delete_local_planned_location_count": None,
                "delete_local_removed_location_count": None, "delete_local_result_known": False,
                "delete_local_partial_failure": None,
            },
        ),
        _row(
            category="controller", event_type="failure", stage="command", label="command_failed", identity="delete-ambiguous-partial",
            details={
                "command": "DELETE_LOCAL", "delete_local_planned_location_count": 2,
                "delete_local_removed_location_count": 0, "delete_local_result_known": True,
                "delete_local_partial_failure": None,
            },
        ),
        _row(
            category="controller", event_type="failure", stage="command", label="command_failed", identity="delete-malformed",
            details={
                "command": "DELETE_LOCAL", "delete_local_planned_location_count": True,
                "delete_local_removed_location_count": 3, "delete_local_result_known": 1,
                "delete_local_partial_failure": "false",
            },
        ),
        _row(
            category="queue.lifecycle", event_type="completion", stage="command", label="command_finished", identity="queue-not-delete",
            details={
                "command": "QUEUE", "delete_local_planned_location_count": 2,
                "delete_local_removed_location_count": 2, "delete_local_result_known": True,
                "delete_local_partial_failure": False,
            },
        ),
    ]
    active = spool / "breadcrumbs.jsonl"
    reader = _reader(spool, artifact)
    try:
        reader.start()
        active.write_bytes(b"".join(rows))
        assert reader.poll() == len(rows)
    finally:
        reader.close()

    records = [item["capture"]["projection"] for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    successful = next(item for item in records if item.get("delete_local_removed_location_count") == 2)
    partial = next(item for item in records if item.get("delete_local_removed_location_count") == 1)
    unknown = next(item for item in records if item.get("delete_local_result_known") is False)
    assert successful["delete_local_planned_location_count"] == 2
    assert successful["delete_local_result_known"] is True
    assert successful["delete_local_partial_failure"] is False
    assert partial["delete_local_partial_failure"] is True
    assert unknown["delete_local_planned_location_count"] is None
    assert unknown["delete_local_removed_location_count"] is None
    assert unknown["delete_local_partial_failure"] is None
    ambiguous_partial = next(item for item in records if item.get("delete_local_removed_location_count") == 0)
    assert ambiguous_partial["delete_local_result_known"] is True
    assert ambiguous_partial["delete_local_partial_failure"] is None
    malformed = next(item for item in records if item["event_type"] == "failure" and "delete_local_partial_failure" not in item)
    assert not any(key.startswith("delete_local_") for key in malformed)
    queue = next(item for item in records if item["event_type"] == "completion" and "delete_local_planned_location_count" not in item)
    assert not any(key.startswith("delete_local_") for key in queue)
    text = artifact.read_text(encoding="utf-8")
    assert "private-name.bin" not in text and "/private/location" not in text


def _roundtrip_finalized_delete_local_rows(tmp_path: Path, finalized_path: str) -> tuple[list[dict[str, object]], str]:
    finalized = Path(finalized_path).read_bytes()
    rows = [json.loads(line) for line in finalized.splitlines() if line]
    boundary = next(row for row in rows if row.get("session_boundary") is True)
    spool, artifact = _prepare(tmp_path)
    _health(spool, session=boundary["session_id"])
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs.jsonl").write_bytes(finalized)
        reader.poll()
    finally:
        reader.close()
    return _artifacts(artifact), artifact.read_text(encoding="utf-8")


def test_actual_finalized_delete_local_rows_survive_projection_and_sink(tmp_path: Path) -> None:
    finalized_path = os.environ.get("SEEDSYNC_DELETE_LOCAL_FINALIZED_ROWS_PATH")
    if not finalized_path:
        pytest.skip("actual finalized Delete Local rows were not supplied")
    output, text = _roundtrip_finalized_delete_local_rows(tmp_path, finalized_path)
    projections = [
        item["capture"]["projection"]
        for item in output
        if item.get("capture", {}).get("state") == "record"
    ]
    facts = [item for item in projections if "delete_local_planned_location_count" in item]
    assert len(facts) == 2
    dispatched = next(item for item in facts if item["delete_local_result_known"] is False)
    finished = next(item for item in facts if item["delete_local_result_known"] is True)
    assert dispatched["delete_local_planned_location_count"] == 2
    assert dispatched["delete_local_removed_location_count"] is None
    assert dispatched["delete_local_partial_failure"] is None
    assert finished["delete_local_planned_location_count"] == 2
    assert finished["delete_local_removed_location_count"] == 2
    assert finished["delete_local_partial_failure"] is False
    assert "release.bin" not in text and "<redacted>" not in text


def test_actual_finalized_delete_local_failure_rows_survive_projection_and_sink(tmp_path: Path) -> None:
    finalized_path = os.environ.get("SEEDSYNC_DELETE_LOCAL_FINALIZED_FAILURE_ROWS_PATH")
    if not finalized_path:
        pytest.skip("actual finalized Delete Local failure rows were not supplied")
    output, text = _roundtrip_finalized_delete_local_rows(tmp_path, finalized_path)
    projections = [
        item["capture"]["projection"]
        for item in output
        if item.get("capture", {}).get("state") == "record"
    ]
    facts = [item for item in projections if "delete_local_planned_location_count" in item]
    assert len(facts) == 2
    dispatched = next(item for item in facts if item["delete_local_result_known"] is False)
    failed = next(item for item in facts if item["delete_local_result_known"] is True)
    assert dispatched["delete_local_planned_location_count"] == 2
    assert dispatched["delete_local_removed_location_count"] is None
    assert dispatched["delete_local_partial_failure"] is None
    assert failed["delete_local_planned_location_count"] == 2
    assert failed["delete_local_removed_location_count"] == 1
    assert failed["delete_local_partial_failure"] is True
    assert "release.bin" not in text and "<redacted>" not in text


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


@pytest.mark.skipif(BreadcrumbTraceCollector is None, reason="production breadcrumb collector is unavailable")
def test_collector_durable_flat_envelope_reaches_reader_projection(tmp_path: Path) -> None:
    spool = tmp_path / "collector-spool"
    spool.mkdir()
    artifact = tmp_path / "capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True,
        policy={"default": "off", "rules": {"queue.readiness": "info", "model_page_snapshot": "debug"}},
        durable_enabled=True,
        durable_path=str(spool),
        durable_enabled_getter=lambda: True,
    )
    try:
        assert collector.record(
            "controller", "baseline", {"schema": "queue_readiness.v1", "phase": "entry", "outcome": "accepted"},
            category="queue.readiness", event_type="callback", stage="queue_readiness", level="info",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader = _reader(spool, artifact)
        try:
            reader.start()
            assert collector.record(
                "controller", "queue_callback", {"schema": "queue_readiness.v1", "phase": "entry", "outcome": "accepted"},
                category="queue.readiness", event_type="callback", stage="queue_readiness", level="info",
                corr_id="private-correlation", flow_id="private-flow",
            ) in {"retained", "enqueued"}
            assert collector.record(
                "model_updater", "model_page_snapshot",
                {"scope_version": 4, "global_version": 9, "lock_wait_ms": 1, "lock_hold_ms": 2, "snapshot_elapsed_ms": 3, "outcome": "success"},
                category="model_page_snapshot", event_type="diagnostic", stage="model_page_snapshot", level="debug",
            ) in {"retained", "enqueued"}
            assert collector.flush_durable(2.0)
            assert reader.poll() == 2
        finally:
            reader.close()
        rows = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
        final_rows = [row for row in rows if row.get("message") in {"queue_callback", "model_page_snapshot"}]
        assert {row["category"] for row in final_rows} == {"queue.readiness", "model_page_snapshot"}
        assert all("metadata" not in row and isinstance(row.get("details"), dict) for row in final_rows)
    finally:
        collector.close(timeout=2.0)
    projections = [item["capture"]["projection"] for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    assert any(item.get("queue_readiness", {}).get("event") == "queue_callback" for item in projections)
    assert any(item.get("model_page_snapshot", {}).get("outcome") == "success" for item in projections)


@pytest.mark.skipif(BreadcrumbTraceCollector is None or ModelBuilder is None, reason="production model builder is unavailable")
def test_finalized_input_arbitration_selects_actual_root_builds_and_summaries(tmp_path: Path) -> None:
    spool = tmp_path / "input-arbitration-spool"
    spool.mkdir()
    artifact = tmp_path / "input-arbitration-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=128,
        policy={"default": "off", "rules": {"scan.authority": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact)
    builder = ModelBuilder()
    remote = SystemFile("private-root-label", 100, True)
    remote.add_child(SystemFile("private-leaf-label.bin", 100, False))
    local = SystemFile("private-root-label", 40, True)
    local.add_child(SystemFile("private-leaf-label.bin", 40, False))
    builder.set_remote_files([remote])
    builder.set_local_files([local])
    builder.set_stop_resume_trace_breadcrumb(collector.create_emitter())
    try:
        assert collector.record(
            "controller", "baseline", {"schema": "input_arbitration_test_baseline.v1"},
            category="scan.authority", level="info",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader.start()
        first_model = builder.build_model()
        active = SystemFile("private-root-label", 100, True)
        active.add_child(SystemFile("private-leaf-label.bin", 100, False))
        builder.set_active_files([active])
        second_model = builder.build_model()
        assert first_model is not second_model
        assert collector.flush_durable(2.0)
        assert reader.poll() == 6
    finally:
        reader.close()
        collector.close(timeout=2.0)

    durable = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    finalized = [row for row in durable if row.get("message") == "input_arbitration_witness"]
    assert len(finalized) == 6
    assert all("metadata" not in row and row.get("trace_scope") == "flow" for row in finalized)
    assert all(type(row.get("version")) is int for row in finalized)
    assert len({row["version"] for row in finalized}) == 6
    assert "private-root-label" not in json.dumps(finalized)
    assert "private-leaf-label.bin" not in json.dumps(finalized)

    projected = [
        item["capture"]["projection"] for item in _artifacts(artifact)
        if item["capture"].get("state") == "record"
        and item["capture"].get("projection", {}).get("category") == "scan.authority"
    ]
    assert len(projected) == 6
    roots = [item for item in projected if item["input_arbitration"]["phase"] == "root_fill"]
    assert len(roots) == 2
    root_token = roots[0]["input_arbitration"]["root_id"]
    assert root_token != "unknown" and roots[1]["input_arbitration"]["root_id"] == root_token
    selected = capture.select_input_arbitration_rows(projected, root_token)
    assert selected["state"] == "selected"
    assert len(selected["build_tokens"]) == 2
    assert all(len(token) == 16 for token in selected["build_tokens"])
    assert len(selected["records"]) == 6
    for token in selected["build_tokens"]:
        build_rows = [row for row in selected["records"] if row["input_arbitration"]["build_token"] == token]
        assert {row["input_arbitration"]["phase"] for row in build_rows} == {
            "root_fill", "remote_leaf_fill", "summary",
        }
        assert len({row["numbers"]["version"] for row in build_rows}) == 3
        assert {row["input_arbitration"]["subject_kind"] for row in build_rows} == {
            "root", "remote_leaf", "summary",
        }
    assert capture.select_input_arbitration_rows(projected, "0" * 16)["state"] == "unknown"

    # The durable writer flattens metadata today; retain compatibility with a
    # metadata-wrapped finalized envelope without copying arbitrary headers.
    actual = next(row for row in finalized if row.get("details", {}).get("phase") == "root_fill")
    metadata_keys = ("source", "category", "event_type", "stage", "level", "corr_id", "flow_id", "trace_scope", "message")
    wrapped = {
        "metadata": {key: actual[key] for key in metadata_keys},
        "details": actual["details"], "created_ms": actual["created_ms"],
        "created_ns": actual["created_ns"], "version": actual["version"],
        "private_header": "never-project",
    }
    wrapped_projection, irrelevant = capture._projection(wrapped)
    assert not irrelevant and wrapped_projection is not None
    facts = wrapped_projection["input_arbitration"]
    assert facts["root_id"] == root_token
    assert facts["build_token"] in selected["build_tokens"]
    assert "private_header" not in json.dumps(wrapped_projection)

    # Missing optional facts remain unknown, while explicit null and malformed
    # compact source values remain distinguishable.
    capped = dict(actual["details"])
    capped.pop("filled_state", None)
    capped.pop("filled_remote_transferable", None)
    source_inputs = dict(capped["source_inputs"])
    source_inputs["lb"] = None
    source_inputs["lp"] = 0
    capped["source_inputs"] = source_inputs
    safe, irrelevant = capture._projection({**actual, "details": capped})
    assert not irrelevant and safe is not None
    facts = safe["input_arbitration"]
    assert facts["filled_state"] == "unknown" and facts["filled_remote_transferable"] == "unknown"
    assert facts["local_input_size"] is None and facts["local_input_present"] == "unknown"


@pytest.mark.skipif(BreadcrumbTraceCollector is None or ModelBuilder is None, reason="production model builder is unavailable")
def test_finalized_split_arbitration_preserves_selection_guards_and_wide_mtimes(tmp_path: Path) -> None:
    spool = tmp_path / "split-arbitration-spool"
    spool.mkdir()
    artifact = tmp_path / "split-arbitration-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=128,
        policy={"default": "off", "rules": {"scan.authority": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact)
    baseline_builder = ModelBuilder()
    baseline_remote = SystemFile("baseline-root", 32, True)
    baseline_remote.add_child(SystemFile("baseline-leaf", 32, False))
    baseline_local = SystemFile("baseline-root", 16, True)
    baseline_local.add_child(SystemFile("baseline-leaf", 16, False))
    baseline_builder.set_remote_files([baseline_remote])
    baseline_builder.set_local_files([baseline_local])
    baseline_builder.set_stop_resume_trace_breadcrumb(collector.create_emitter())
    builder = ModelBuilder()
    epoch_ns = 1_790_873_610_000_000_000
    remote_root = SystemFile("mixed-root", 2_981_866_544, True, mtime_ns=epoch_ns + 24_000_000_000)
    remote_root.add_child(SystemFile("payload.iso", 2_981_865_472, False, mtime_ns=epoch_ns + 24_000_000_000))
    remote_checksum = SystemFile("checksums.txt", 1_072, False, mtime_ns=epoch_ns + 500_000_000)
    remote_root.add_child(remote_checksum)
    local_root = SystemFile("mixed-root", 35_759_708_487, True, mtime_ns=epoch_ns)
    local_root.add_child(SystemFile("payload.iso", 2_981_865_472, False, is_staging=True,
                                    mtime_ns=epoch_ns + 24_000_000_000))
    local_checksum = SystemFile("checksums.txt", 1_072, False, mtime_ns=epoch_ns)
    local_root.add_child(local_checksum)
    active_root = SystemFile("mixed-root", 2_981_865_472, True)
    active_root.add_child(SystemFile("payload.iso", 2_981_865_472, False,
                                     mtime_ns=epoch_ns + 24_000_000_000))
    builder.set_remote_files([remote_root])
    builder.set_local_files([local_root])
    builder.set_active_files([active_root])
    builder.set_stop_resume_trace_breadcrumb(collector.create_emitter())
    try:
        assert baseline_builder.build_model().get_file("baseline-root") is not None
        assert collector.flush_durable(2.0)
        initial = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(row.get("message") == "input_arbitration_witness" for row in initial)
        reader.start()
        model = builder.build_model()
        assert model.get_file("mixed-root") is not None
        assert collector.flush_durable(2.0)
        assert reader.poll() == 4
    finally:
        reader.close()
        collector.close(timeout=2.0)

    durable = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    finalized = [row for row in durable if row.get("message") == "input_arbitration_witness"]
    assert len(finalized) == 7
    assert all(row.get("category") == "scan.authority"
               and row.get("stage") == "model_builder_input_arbitration"
               and row.get("event_type") == "diagnostic"
               and row.get("trace_scope") == "flow" and row.get("corr_id") == row.get("flow_id")
               and re.fullmatch(r"input-arbitration:[0-9a-f]{16}", row["corr_id"]) for row in finalized)
    projections = [item["capture"]["projection"] for item in _artifacts(artifact)
                   if item["capture"].get("state") == "record"]
    assert len(projections) == 4
    assert all(item["category"] == "scan.authority" and item["event_type"] == "diagnostic"
               and item["stage"] == "model_builder_input_arbitration" for item in projections)
    phases = [item["input_arbitration"]["phase"] for item in projections]
    assert phases.count("root_fill") == 1 and phases.count("remote_leaf_fill") == 2
    assert phases.count("summary") == 1
    build_tokens = {item["input_arbitration"]["build_token"] for item in projections}
    assert len(build_tokens) == 1 and "unknown" not in build_tokens
    root_ids = {item["input_arbitration"]["root_id"] for item in projections
                if item["input_arbitration"]["phase"] != "summary"}
    assert len(root_ids) == 1 and all(re.fullmatch(r"[0-9a-f]{16}", token) for token in root_ids)
    leaf_facts = [item["input_arbitration"] for item in projections
                  if item["input_arbitration"]["phase"] == "remote_leaf_fill"]
    root_facts = next(item["input_arbitration"] for item in projections
                      if item["input_arbitration"]["phase"] == "root_fill")
    split_leaf = next(leaf for leaf in leaf_facts
                      if leaf["split_candidate_provenance"] == "split_candidate_local")
    for leaf in leaf_facts:
        if leaf is split_leaf:
            continue
        for key in ("input_scope", "selection", "local_proof_generation",
                    "local_proof_generation_known", "active_generation_known"):
            assert leaf[key] == root_facts[key]
    if split_leaf is not None:
        leaf = split_leaf
        assert leaf["input_scope"] == leaf["selection"] == "unknown"
        assert leaf["local_proof_generation"] == "unknown"
        assert leaf["local_proof_generation_known"] == leaf["active_generation_known"] == "unknown"
    root = next(item["input_arbitration"] for item in projections
                if item.get("input_arbitration", {}).get("phase") == "root_fill")
    assert root["selection"] == "active" and root["selection_reason"] == "active_selected"
    assert root["local_staging_descendant"] is True
    assert root["local_staging_collision_descendant"] is False
    assert root["split_has_verified_leaf"] is False
    assert root["local_candidate_eligible"] is True and root["local_size_ge_active"] is True
    assert root["remote_indicates_newer"] is True
    checksum = next(item["input_arbitration"] for item in projections
                    if item.get("input_arbitration", {}).get("split_candidate_remote_size") == 1_072)
    assert checksum["split_candidate_local_present"] is True
    assert checksum["split_candidate_local_size"] == checksum["split_candidate_remote_size"] == 1_072
    assert checksum["split_candidate_local_mtime_ns"] == str(epoch_ns)
    assert checksum["split_candidate_remote_mtime_ns"] == str(epoch_ns + 500_000_000)
    assert int(checksum["split_candidate_local_mtime_ns"]) > 2**53
    assert checksum["split_candidate_local_mtime_known"] is True
    assert checksum["split_candidate_remote_mtime_known"] is True
    assert checksum["split_candidate_strict_verified"] is False
    assert checksum["split_candidate_portable_queue_exclusion_match"] is True
    assert checksum["split_candidate_provenance"] == "split_candidate_local"
    assert len({item["version"] for item in finalized}) == 7
    versions = [item["numbers"]["version"] for item in projections]
    assert versions == sorted(versions) and len(set(versions)) == 4


@pytest.mark.skipif(BreadcrumbTraceCollector is None, reason="production breadcrumb collector is unavailable")
def test_production_lineage_summary_durable_round_trip_preserves_timestamps_and_range(tmp_path: Path) -> None:
    spool = tmp_path / "collector-spool"
    spool.mkdir()
    artifact = tmp_path / "capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True,
        policy={"default": "off", "rules": {"model.progress": "debug"}},
        durable_enabled=True,
        durable_path=str(spool),
        durable_enabled_getter=lambda: True,
    )
    correlation = "lftp-poll:0123456789abcdef"
    try:
        # Lineage steps are retained in-memory until their summary is
        # projected, so they cannot wake the durable writer by themselves.
        # Admit one ordinary record and flush it first; this uses the real
        # writer lifecycle to commit the session boundary and publish valid
        # health before the reader arms at EOF.
        assert collector.record(
            "model_updater", "durable_boundary_probe", {},
            category="model.progress", event_type="diagnostic",
            stage="model_summary", level="debug",
        ) in {"retained", "enqueued"}
        assert collector.wait_for_ingress(2.0)
        assert collector.flush_durable(2.0)
        durable_state = collector.durable_snapshot()
        assert durable_state["session_boundary_written"] is True
        assert durable_state["written"] >= 1
        assert collector.record_progress_lineage(
            correlation, "status_consume",
            {"source": "fresh_healthy", "fresh": True, "healthy": True},
        )
        for version in (4, 5, 7):
            assert collector.record_progress_lineage(
                correlation, "model_mutation",
                {"outcome": "mutated", "model_version": version, "scope_version": version},
            )
        assert collector.record_progress_lineage(
            correlation, "updater_decision",
            {"decision": "active_delta", "build_kind": "candidate", "status_count_bucket": "2-4",
             "updater_cycle_duration_bucket": "20-99"},
        )
        reader = _reader(spool, artifact)
        try:
            reader.start()
            assert collector.record_progress_lineage_summary(correlation)
            assert collector.flush_durable(2.0)
            assert reader.poll() == 1
        finally:
            reader.close()
        rows = [
            json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        row = next(item for item in rows if item.get("message") == "progress_lineage_summary")
        assert isinstance(row.get("created_ns"), int)
        assert row["corr_id"] == correlation
        assert row["details"]["model_mutation"] == {
            "model_version_first": 4,
            "model_version_last": 7,
            "scope_version": 7,
            "mutation_version_ranges": [[4, 5], [7, 7]],
            "monotonic_ms": row["details"]["model_mutation"]["monotonic_ms"],
        }
    finally:
        collector.close(timeout=2.0)
    record = next(
        item for item in _artifacts(artifact)
        if item["capture"].get("state") == "record"
    )
    projection = record["capture"]["projection"]
    assert record["capture"]["created_ns"] == row["created_ns"]
    assert projection["model_progress_lineage"]["missing_phase"] == "none"
    assert projection["model_progress_lineage"]["model_mutation"]["model_version_first"] == 4
    assert projection["model_progress_lineage"]["model_mutation"]["model_version_last"] == 7
    assert projection["model_progress_lineage"]["model_mutation"]["scope_version"] == 7
    assert projection["model_progress_lineage"]["model_mutation"]["mutation_version_ranges"] == [[4, 5], [7, 7]]
    assert all(
        not start <= 6 <= end
        for start, end in projection["model_progress_lineage"]["model_mutation"]["mutation_version_ranges"]
    )


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
