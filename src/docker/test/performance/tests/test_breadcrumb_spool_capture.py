"""Real-filesystem tests for the Linux descriptor-relative spool reader."""
from __future__ import annotations

import importlib.util
import errno
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from contextlib import ExitStack
from collections import Counter
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from datetime import datetime, timedelta
from queue import Queue

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
    from common.breadcrumb_trace import (
        BREADCRUMB_INGRESS_NORMAL_CAPACITY, BreadcrumbTraceCollector,
        opaque_trace_correlation, trace_session_digest,
    )
    from model import Model, ModelFile
    from system import SystemFile
    from controller import Controller, ModelBuilder
    from controller.controller import DeferredQueueIntent, PendingQueueDispatch
    from controller.scan import ScannerProcess, ScannerResult
    from lftp import LftpJobStatus
    from controller.model_updater import (
        ModelUpdater, _ModelFinalizationBreadcrumbSpan, _ProgressiveScanAccumulator, _ensure_progressive_scan_state,
        _record_lftp_status_breadcrumb,
        _record_lftp_status_membership_transition, _record_lifecycle_scan_breadcrumb,
    )
    from controller.scan.scanner_process import _record_root_shape_breadcrumb
    from tests.unittests.test_controller import test_model_updater as _model_updater_fixture_module
    from tests.unittests.test_controller.test_controller import TestController as _ControllerFixture
except ImportError:
    BreadcrumbTraceCollector = None
    ModelFile = None
    Model = None
    opaque_trace_correlation = None
    trace_session_digest = None
    Controller = None
    _record_lifecycle_scan_breadcrumb = None
    _record_root_shape_breadcrumb = None
    _ModelFinalizationBreadcrumbSpan = None
    _record_lftp_status_breadcrumb = None
    _record_lftp_status_membership_transition = None
    SystemFile = None
    ModelBuilder = None
    ModelUpdater = None
    DeferredQueueIntent = None
    PendingQueueDispatch = None
    ScannerResult = None
    ScannerProcess = None
    _ensure_progressive_scan_state = None
    _ProgressiveScanAccumulator = None
    _model_updater_fixture_module = None
    _ControllerFixture = None
    LftpJobStatus = None


def test_numeric_progress_actual_pipeline_finalized_identity_and_credit(tmp_path: Path) -> None:
    from common.root_progress_trace import root_progress_tracer
    from lftp import LftpJobStatusParser
    from controller.model_updater import _record_root_progress_status
    from tests.unittests.test_controller.test_model_builder import TestModelBuilder
    from tests.unittests.test_web.test_handler.test_model_api import TestModelApi
    from web.handler.model_api import ModelApiHandler
    from web.serialize.serialize_model import SerializeModel
    spool, artifact = _prepare(tmp_path)
    trace = BreadcrumbTraceCollector(lambda: True, max_entries=256,
        policy={"default": "info"}, durable_enabled=True, durable_path=str(spool))
    reader = _reader(spool, artifact, max_records=256)
    trace.record("controller", "boundary", category="controller", level="info")
    assert trace.wait_for_ingress(2) and trace.flush_durable(2)
    reader.start()
    fixtures = []
    outputs = []
    try:
        for mode in ("enabled", "disabled", "fault"):
            fixture = TestModelBuilder(); fixture.setUp(); fixtures.append(fixture)
            web = TestModelApi(); web.setUp(); fixtures.append(web)
            builder = fixture.model_builder
            root = SystemFile("sample-root", 1000, True)
            root.add_child(SystemFile("complete.bin", 200, False, mtime_ns=1_700_000_000_000_000_000))
            root.add_child(SystemFile("partial.bin", 800, False))
            local = SystemFile("sample-root", 231, True)
            local.add_child(SystemFile("complete.bin", 200, False, mtime_ns=1_700_000_000_000_000_000))
            partial = SystemFile("partial.bin", 31, False, is_staging=True)
            partial.status_sidecar_ready = True
            local.add_child(partial)
            status = LftpJobStatusParser().parse(
                "jobs -v\n[0] queue (sftp://user@example.invalid)\n"
                "sftp://user@example.invalid/remote\nQueue is running.\n"
                "[2] mirror -c /remote/sample-root /local/staging/ -- 31/700 (4%) 100B/s\n"
            )[0]
            assert status.name == "sample-root" and status.total_transfer_state.size_local == 31
            builder.set_remote_files([root]); builder.set_local_files([local]); builder.set_lftp_statuses([status])
            builder.set_stop_resume_trace_breadcrumb(trace.create_emitter())
            tracer = root_progress_tracer(trace)
            builder.set_numeric_progress_trace(tracer)
            poll = "lftp-poll:0123456789abcdef"
            controller_for_origin = web.controller
            controller_for_origin._Controller__last_lftp_statuses = [status]
            controller_for_origin._Controller__progress_publication_epoch = 2
            controller_for_origin._Controller__status_diagnostic_cache = {
                "statuses": controller_for_origin._Controller__last_lftp_statuses, "poll": poll,
                "accepted_monotonic_ns": time.monotonic_ns() - 2_000_000_000, "epoch": 2}
            from controller.model_updater import _status_input_provenance
            input_provenance = _status_input_provenance(controller_for_origin)
            builder.set_stop_resume_trace_cycle_context({"lftp_poll_correlation": poll,
                "lftp_status_source": "fresh_healthy", "lftp_status_fresh": True, "lftp_status_healthy": True,
                "input_provenance": input_provenance})
            web.controller._Controller__context.breadcrumb_trace = trace
            with ExitStack() as stack:
                if mode == "disabled": stack.enter_context(patch.object(trace, "is_effectively_enabled", return_value=False))
                if mode == "fault": stack.enter_context(patch.object(trace, "record", side_effect=RuntimeError("synthetic-detail")))
                if mode == "fault": stack.enter_context(patch.object(builder, "_ModelBuilder__record_numeric_credit", side_effect=RuntimeError("synthetic-credit")))
                model = builder.build_model(diagnostic_target_root_ids=("sample-root",))
                assert model.get_file("sample-root").transferred_size == 231
                from controller.scan import ScannerResult
                from copy import deepcopy
                scan = ScannerResult(datetime.now(), [deepcopy(local)], generation=7, session_token="synthetic-active-session")
                builder.set_stop_resume_trace_cycle_context({"input_provenance": input_provenance,
                    "active_scan_provenance": {"active_generation": scan.generation,
                    "active_produced_ms": int(scan.timestamp.timestamp() * 1000),
                    "active_admitted_ms": int(time.time_ns() // 1_000_000)}})
                builder.set_active_files(scan.files)
                builder.set_stop_resume_trace_cycle_context({"lftp_poll_correlation": poll,
                    "lftp_status_source": "fresh_healthy", "lftp_status_fresh": True,
                    "lftp_status_healthy": True, "input_provenance": input_provenance})
                assert model.published_file("sample-root") is not None
                controller = web.controller
                controller._Controller__model = model
                controller._Controller__scan_authority_snapshot = {"publication_id": 1}
                _record_root_progress_status(controller, [status], source="fresh_healthy", model_version=model.version,
                    fresh=True, healthy=True, poll_correlation=poll)
                ModelUpdater._record_status_publication_join(trace.create_emitter(), controller,
                    {"publication_id": 1, "model_version": model.version}, builder,
                    raw_statuses=[status], filtered_statuses=[status], consumed_statuses=[status],
                    raw_status_id_counts={status.file_id: 1}, active_status_file_ids={status.file_id},
                    malformed_status_file_ids=set(), status_source="fresh_healthy", status_fresh=True,
                    status_healthy=True, poll_correlation=poll)
                records = [SerializeModel._SerializeModel__model_file_to_json_dict(model.get_file("sample-root"))]
                payload = {"records": records, "model_version": model.version, "_global_model_version": model.version}
                handler = ModelApiHandler(controller, breadcrumb_trace=trace)
                body = handler._ModelApiHandler__json_response(payload).body
                sse = handler._ModelApiHandler__sse("scoped", "model-page", {"records": records},
                    version=1, global_model_version=model.version, scope_id="scope")
                outputs.append((body, sse))
            if mode == "enabled":
                assert trace.wait_for_ingress(2) and trace.flush_durable(2)
                finalized = [row for row in trace.snapshot()["entries"]
                    if row.get("details", {}).get("schema") == "model_numeric_progress.v1"]
                phases = {row["details"]["phase"]: row for row in finalized}
                assert {"status", "builder", "builder_leaf", "publication", "json", "sse"} <= phases.keys()
                values = phases["builder"]["details"]["values"]
                assert values["status_bytes"] == "31" and values["live_root_bytes"] == "231"
                assert values["blocked_descendant_bytes"] == "200"
                assert phases["builder"]["details"]["live_credit"] is True
                assert phases["json"]["details"]["values"]["serialized_bytes"] == "231"
                assert phases["sse"]["details"]["poll_correlation"] == poll
                assert phases["sse"]["details"]["input_correlation"] == poll
                assert phases["sse"]["details"]["values"]["active_generation"] == "7"
                assert phases["sse"]["details"]["values"]["active_bytes"] == "231"
                assert int(phases["sse"]["details"]["values"]["status_age_ms"]) >= 2000
            # B is the failed/inflight evaluation; credit remains A's 31 bytes.
            # Unknown cached producer origin must survive final publication/serialization.
            for index, cached_source in enumerate(("cached_unhealthy", "cached_inflight", "cached_retry")):
                evaluation_poll = "lftp-poll:{:016x}".format(index + 100)
                builder.set_stop_resume_trace_cycle_context({"lftp_poll_correlation": evaluation_poll,
                    "lftp_status_source": cached_source, "lftp_status_fresh": False, "lftp_status_healthy": False})
                with ExitStack() as stack:
                    if mode == "disabled": stack.enter_context(patch.object(trace, "is_effectively_enabled", return_value=False))
                    if mode == "fault": stack.enter_context(patch.object(trace, "record", side_effect=RuntimeError("synthetic-detail")))
                    stack.enter_context(patch("common.root_progress_trace.time.monotonic", return_value=time.monotonic() + 10 + index * 2))
                    _record_root_progress_status(controller, [status], source=cached_source, model_version=model.version,
                        fresh=False, healthy=False, poll_correlation=evaluation_poll)
                    ModelUpdater._record_status_publication_join(trace.create_emitter(), controller,
                        {"publication_id": 1, "model_version": model.version}, builder,
                        raw_statuses=[status], filtered_statuses=[status], consumed_statuses=[status],
                        raw_status_id_counts={status.file_id: 1}, active_status_file_ids={status.file_id},
                        malformed_status_file_ids=set(), status_source=cached_source, status_fresh=False,
                        status_healthy=False, poll_correlation=evaluation_poll)
                    cached_body = handler._ModelApiHandler__json_response(payload).body
                    cached_sse = handler._ModelApiHandler__sse("scoped", "model-page", {"records": records},
                        version=1, global_model_version=model.version, scope_id="scope")
                    assert cached_body == body and cached_sse == sse
                    outputs[-1] += (cached_body, cached_sse)
                if mode == "enabled":
                    assert trace.wait_for_ingress(2) and trace.flush_durable(2)
                    cached_rows = [row["details"] for row in trace.snapshot()["entries"]
                        if row.get("details", {}).get("schema") == "model_numeric_progress.v1"
                        and row["details"].get("status_source") == cached_source]
                    assert {"status", "publication"} <= {row["phase"] for row in cached_rows}
                    assert all(row["poll_correlation"] is None for row in cached_rows)
                    assert all(row["input_correlation"] == poll for row in cached_rows)
                    assert all(row["values"]["status_bytes"] == "31" for row in cached_rows if row["phase"] == "status")
                    transports = [row["details"] for row in trace.snapshot()["entries"]
                        if row.get("details", {}).get("phase") in ("json", "sse")]
                    assert any(row["poll_correlation"] is None for row in transports)
                    assert all(row["input_correlation"] == poll for row in transports)
                    assert all(row["values"]["serialized_bytes"] == "231" for row in transports)
            builder.set_stop_resume_trace_cycle_context({"lftp_poll_correlation": poll,
                "lftp_status_source": "fresh_healthy", "lftp_status_fresh": True, "lftp_status_healthy": True})
            with ExitStack() as stack:
                if mode == "disabled": stack.enter_context(patch.object(trace, "is_effectively_enabled", return_value=False))
                if mode == "fault": stack.enter_context(patch.object(trace, "record", side_effect=RuntimeError("synthetic-detail")))
                if mode == "fault": stack.enter_context(patch.object(builder, "_ModelBuilder__record_numeric_credit", side_effect=RuntimeError("synthetic-credit")))
                with patch("common.root_progress_trace.time.monotonic", return_value=time.monotonic() + 2):
                    # A fresh inactive inventory avoids inventing retirement
                    # authority from an empty poll of a retained live job.
                    builder.clear()
                    builder.set_remote_files([root]); builder.set_local_files([local])
                    inactive = builder.build_model(diagnostic_target_root_ids=("sample-root",))
                assert inactive.get_file("sample-root").transferred_size == 200
                outputs[-1] += (SerializeModel().model([inactive.get_file("sample-root")]),)
            if mode == "enabled":
                assert trace.wait_for_ingress(2) and trace.flush_durable(2)
                finalized = [row for row in trace.snapshot()["entries"]
                    if row.get("details", {}).get("schema") == "model_numeric_progress.v1"]
                inactive_rows = [row for row in finalized if row["details"]["phase"] == "builder"
                    and row["details"]["live_credit"] is False]
                assert len(inactive_rows) == 1
                assert inactive_rows[0]["details"]["values"]["nonlive_descendant_bytes"] == "200"
                assert inactive_rows[0]["details"]["values"]["live_root_bytes"] is None
        assert outputs[0] == outputs[1] == outputs[2]
        assert trace.wait_for_ingress(2) and trace.flush_durable(2)
        # Each registered read is byte/time bounded; consume finite continuation pages.
        for _ in range(4):
            if reader.poll() == 0:
                break
        captured = [row["capture"]["projection"] for row in _artifacts(artifact)
            if row["capture"].get("state") == "record" and "numeric_progress" in row["capture"].get("projection", {})]
        # Durable lane scheduling can reorder versions after in-memory finalization.
        # Compare the registered reader against its actual persisted producer envelope.
        finalized = [row for row in (json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text().splitlines())
            if row.get("details", {}).get("schema") == "model_numeric_progress.v1"]
        assert [row["numbers"]["version"] for row in captured] == [row["version"] for row in finalized]
        assert [row["numeric_progress"] for row in captured] == [observer._sanitize_numeric_progress(row["details"]) for row in finalized]
        assert len({row["corr_hash"] for row in captured}) == 1
        assert all(label not in artifact.read_text() for label in ("sample-root", "complete.bin", "partial.bin", "synthetic-detail"))
    finally:
        reader.close(); trace.close()
        for fixture in fixtures: fixture.doCleanups()


def test_numeric_progress_projection_preserves_wide_values_unknown_and_bounds() -> None:
    from common.root_progress_trace import numeric_progress_details
    details = numeric_progress_details({"status_bytes": 2**53 + 3, "selected_bytes": None,
        "remote_bytes": 2**63, "local_bytes": True, "serialized_bytes": 1.5,
        "path": "/synthetic/secret", "poll_correlation": "bad", "status_source": "/bad"})
    details.update(schema="model_numeric_progress.v1", phase="status", job_digest="a"*16,
                   observed_ms=12, monotonic_ms=13, sequence=1, filename="synthetic-label")
    projected = observer._sanitize_numeric_progress(details)
    assert projected["values"]["status_bytes"] == str(2**53 + 3)
    assert all(projected["values"][key] is None for key in ("selected_bytes", "remote_bytes", "local_bytes", "serialized_bytes"))
    assert projected["poll_correlation"] is None and projected["status_source"] == "unknown"
    assert "synthetic" not in json.dumps(projected)


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
        "upstream_critical_ingress_rejected": 0,
        "upstream_normal_ingress_rejected": 0,
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


def test_actual_finalized_remove_source_timings_survive_projection_and_sink(tmp_path: Path) -> None:
    success_path = os.environ.get("SEEDSYNC_CUSTODY_FINALIZED_ROWS_PATH")
    failure_path = os.environ.get("SEEDSYNC_CUSTODY_FAILURE_FINALIZED_ROWS_PATH")
    if not success_path or not failure_path:
        pytest.skip("actual finalized remove-source success and failure rows were not supplied")
    success_output, success_text = _roundtrip_finalized_delete_local_rows(tmp_path / "success", success_path)
    failure_output, failure_text = _roundtrip_finalized_delete_local_rows(tmp_path / "failure", failure_path)
    success_records = [
        item["capture"]["projection"] for item in success_output
        if item.get("capture", {}).get("state") == "record"
    ]
    failure_records = [
        item["capture"]["projection"] for item in failure_output
        if item.get("capture", {}).get("state") == "record"
    ]
    success = next(item["final_move_publication"] for item in success_records
                   if item.get("final_move_publication", {}).get("phase") == "remove_source")
    failure = next(item["final_move_publication"] for item in failure_records
                   if item.get("final_move_publication", {}).get("phase") == "remove_source")
    metric_keys = {
        "outer_source_recheck_ns", "source_identity_check_ns", "claim_rename_ns",
        "claim_parent_sync_ns", "claimed_identity_tree_validation_ns", "claim_delete_ns",
        "claim_final_sync_ns", "caller_parent_sync_ns",
    }
    assert metric_keys <= success.keys() and metric_keys <= failure.keys()
    assert all(type(success[key]) is int and success[key] >= 0 for key in metric_keys)
    assert all(type(failure[key]) is int and failure[key] >= 0 for key in metric_keys - {"caller_parent_sync_ns"})
    assert failure["caller_parent_sync_ns"] is None
    assert "retained-claim.bin" not in success_text + failure_text
    assert "staging" not in success_text + failure_text


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
            details={"schema": "queue.lifecycle.v1", "event": "status_membership", "source": "controller", "phase": "status", "outcome": "present", "error_class": "none", "boundary": "status_membership", "membership": "present", "status_health": "healthy", "job_type": "get", "job_correlation": "lftp-job:0123456789abcdef", "poll_correlation": "lftp-poll:fedcba9876543210"},
            identity="status",
        ),
        _row(
            details={"schema": "queue.lifecycle.v1", "event": "queue_admitted", "source": "controller", "phase": "admission", "outcome": "accepted", "error_class": "none", "boundary": "queue_admission", "job_type": "get", "job_correlation": "lftp-job:0123456789abcdef", "poll_correlation": "lftp-poll:fedcba9876543210"},
            identity="admission-no-join",
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
        assert reader.poll() == 6
    finally:
        reader.close()
    records = [item["capture"]["projection"] for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    lifecycle = [item["lifecycle"] for item in records if "lifecycle" in item]
    assert [(item["event"], item["outcome"], item["boundary"]) for item in lifecycle] == [
        ("queue_admitted", "accepted", "queue_admission"),
        ("executor_return", "returned", "executor"),
        ("status_membership", "present", "status_membership"),
        ("queue_admitted", "accepted", "queue_admission"),
    ]
    status_membership = lifecycle[2]
    assert (status_membership["job_type"], status_membership["job_correlation"], status_membership["poll_correlation"]) == (
        "get", "lftp-job:0123456789abcdef", "lftp-poll:fedcba9876543210",
    )
    assert not {"job_type", "job_correlation", "poll_correlation"} & lifecycle[3].keys()
    assert observer._sanitize_capture_projection({"lifecycle": status_membership})["lifecycle"] == status_membership
    completion = next(item["completion_gate"] for item in records if "completion_gate" in item)
    assert completion["boundary"] == "completion_gate" and completion["terminal_outcome"] == "deferred"
    command = next(item["lftp_command"] for item in records if "lftp_command" in item)
    assert command["transfer_kind"] == "get" and command["phase"] == "submitted"
    hashes = [item["flow_hash"] for item in records]
    assert len(set(hashes)) == 6
    text = artifact.read_text(encoding="utf-8")
    assert all(label not in text for label in ("private-flow-label", "private-correlation-label", "private-file-label", "private-pair-label"))


def test_actual_queue_lifecycle_status_join_survives_finalized_capture_and_observer(tmp_path: Path) -> None:
    if Controller is None or BreadcrumbTraceCollector is None or PendingQueueDispatch is None:
        pytest.skip("production controller breadcrumb emitter is unavailable")
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=64,
        policy={"default": "off", "rules": {"controller": "info", "queue.lifecycle": "debug"}},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact, max_records=64)
    producer = Controller.__new__(Controller)
    producer._Controller__context = SimpleNamespace(breadcrumb_trace=collector)
    producer._Controller__work_state_lock = threading.RLock()
    file = ModelFile("join-target.bin", False)
    producer._Controller__pending_queue_dispatches = {
        file.file_id: PendingQueueDispatch(0.0, file.name, None, False, 7),
    }
    status = LftpJobStatus(
        31, LftpJobStatus.Type.GET, LftpJobStatus.State.QUEUED, file.name, "/private/remote",
    )
    poll_correlation = "lftp-poll:0123456789abcdef"
    try:
        assert collector.record(
            "controller", "durable_boundary_probe", {}, category="controller",
            event_type="diagnostic", stage="queue_lifecycle_test_boundary", level="info",
        ) in {"retained", "enqueued"}
        assert collector.wait_for_ingress(2.0)
        assert collector.flush_durable(2.0)
        reader.start()

        recorder = Controller._Controller__incoming_recovery_flow_recorder(
            producer, file.file_id, 7, create=False,
        )
        assert recorder is not None
        recorder("queue_admitted", {})
        Controller._reconcile_pending_queue_dispatches_from_fresh_status(
            producer, [status], diagnostic_statuses=[status], poll_correlation=poll_correlation,
        )
        recorder("executor_return", {})
        assert collector.wait_for_ingress(2.0)
        assert collector.flush_durable(2.0)
        assert reader.poll() >= 3
    finally:
        reader.close()
        collector.close()

    captured = [item["capture"] for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    projections = [item["projection"] for item in captured]
    captured_lifecycle = [item for item in captured if item["projection"].get("category") == "queue.lifecycle"]
    lifecycle_records = [item["projection"] for item in captured_lifecycle]
    lifecycle = [item["lifecycle"] for item in lifecycle_records]
    assert [item["event"] for item in lifecycle] == ["queue_admitted", "status_membership", "executor_return"]
    joined = lifecycle[1]
    assert (joined["job_type"], joined["job_correlation"], joined["poll_correlation"]) == (
        "get", status.job_correlation, poll_correlation,
    )
    assert captured_lifecycle[0]["created_ns"] < captured_lifecycle[1]["created_ns"] < captured_lifecycle[2]["created_ns"]
    expected_flow = producer._Controller__fractional_queue_flow_id(file.file_id, 7)
    assert {item["flow_hash"] for item in lifecycle_records} == {capture._hash(expected_flow)}
    finalized = [observer._sanitize_capture_projection(item) for item in projections]
    finalized_lifecycle = [item["lifecycle"] for item in finalized if item.get("category") == "queue.lifecycle"]
    assert [item["event"] for item in finalized_lifecycle] == [
        "queue_admitted", "status_membership", "executor_return",
    ]
    assert finalized_lifecycle[1] == joined


def test_pending_completion_wake_projects_bounded_local_root_proof_comparisons(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    details = {
        "schema": "pending_completion_authority_wake.v1",
        "decision": "blocked",
        "reason": "local_scope_unreconciled",
        "local_root_proof_shape": "directory_root",
        "local_root_proof_inputs_known": True,
        "local_root_proof_checker_available": True,
        "local_root_proof_result": False,
        "local_root_scan_start_relation": "unknown",
        "local_root_proof_checks": {
            "entry": "present", "run": "mismatch", "generation": "match",
            "builder": "match", "expected": "not_checked",
        },
        "local_scan_alignment": {
            "process_generation": 7,
            "effective_work_floor": 4,
            "builder_proof_generation": 4,
            "accumulator_root_generation": 4,
            "accumulator_root_present": True,
            "root_run_matches_process": True,
            "builder_source_matches_accumulator": True,
            "root_start_known": True,
            "publication_retired_known": True,
            "root_vs_work_floor": "at_or_above",
            "root_start_vs_retired": "before",
            "request_baseline_known": True,
            "root_start_vs_request": "after",
        },
        "private_session": "private-session-label",
        "raw_path": "/private/root",
    }
    reader = _reader(spool, artifact)
    try:
        reader.start()
        (spool / "breadcrumbs.jsonl").write_bytes(_row(
            category="completion.gate", event_type="diagnostic", stage="completion_gate",
            source="model_updater", label="pending_completion_authority_wake", details=details,
        ))
        assert reader.poll() == 1
    finally:
        reader.close()
    text = artifact.read_text(encoding="utf-8")
    records = _artifacts(artifact)
    projection = next(
        item["capture"]["projection"] for item in records
        if item.get("capture", {}).get("state") == "record"
    )
    assert (projection["category"], projection["stage"], projection["event_type"]) == (
        "completion.gate", "completion_gate", "diagnostic",
    )
    assert projection["completion_gate"] == {
        "boundary": "completion_gate", "decision": "blocked",
        "reason": "local_scope_unreconciled", "message": "pending_completion_authority_wake",
        "schema": "pending_completion_authority_wake.v1",
        "local_root_proof": {
            "shape": "directory_root", "inputs_known": True, "checker_available": True,
            "result": False, "start_relation": "unknown", "entry": "present",
            "run": "mismatch", "generation": "match", "builder": "match",
            "expected": "not_checked",
        },
        "local_scan_alignment": {
            "process_generation": "7",
            "effective_work_floor": "4",
            "builder_proof_generation": "4",
            "accumulator_root_generation": "4",
            "accumulator_root_present": True,
            "root_run_matches_process": True,
            "builder_source_matches_accumulator": True,
            "root_start_known": True,
            "publication_retired_known": True,
            "root_vs_work_floor": "at_or_above",
            "root_start_vs_retired": "before",
            "request_baseline_known": None,
            "root_start_vs_request": "unknown",
        },
    }
    assert "private-session-label" not in text
    assert "/private/root" not in text
    assert "private-correlation-label" not in text

    malformed, irrelevant = capture._projection({
        "source": "model_updater", "category": "completion.gate",
        "event_type": "diagnostic", "stage": "completion_gate",
        "message": "pending_completion_authority_wake", "level": "info",
        "details": {
            "schema": "pending_completion_authority_wake.v1", "decision": "blocked",
            "reason": "local_scope_unreconciled", "local_root_proof_shape": "directory_root",
            "local_scan_alignment": {
                "process_generation": True,
                "effective_work_floor": 2**63,
                "builder_proof_generation": -1,
                "accumulator_root_generation": 4,
                "accumulator_root_present": "true",
                "root_run_matches_process": False,
                "builder_source_matches_accumulator": None,
                "root_start_known": True,
                "publication_retired_known": False,
                "root_vs_work_floor": "private",
                "root_start_vs_retired": ["before"],
                "request_baseline_known": True,
                "root_start_vs_request": "before",
            },
        },
    })
    assert not irrelevant
    malformed_alignment = malformed["completion_gate"]["local_scan_alignment"]
    assert malformed_alignment == {
        "process_generation": None, "effective_work_floor": None,
        "builder_proof_generation": None, "accumulator_root_generation": "4",
        "accumulator_root_present": None, "root_run_matches_process": False,
        "builder_source_matches_accumulator": None, "root_start_known": True,
        "publication_retired_known": False, "root_vs_work_floor": "unknown",
        "root_start_vs_retired": "unknown", "request_baseline_known": None,
        "root_start_vs_request": "unknown",
    }


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


def test_collision_batch_gates_survive_actual_finalizer_reader_and_sink(tmp_path: Path) -> None:
    assert _ControllerFixture is not None and BreadcrumbTraceCollector is not None
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(lambda: True, max_entries=256,
        policy={"default": "off", "rules": {"queue.readiness": "info", "controller": "info"}},
        durable_enabled=True, durable_path=str(spool))
    reader = _reader(spool, artifact, max_records=256)
    cases = {
        "owner": "owner_identity", "resolution": "root_resolution", "roots": "safe_roots",
        "authority": "authority", "shape": "snapshot", "empty": "snapshot", "count": "snapshot",
        "mount": "mount_boundary", "relative": "relative_path", "candidate": "candidate_path",
        "missing": "source_signature", "type": "source_signature", "regular_type": "source_signature",
        "permission": "destination_signature",
        "temp": "temp_artifact", "identity": "source_identity", "size": "remote_signature",
        "mtime": "remote_signature", "budget": "byte_budget", "accepted": "claim",
    }
    expected = []
    try:
        collector.record("controller", "boundary", {}, category="controller", level="info")
        assert collector.wait_for_ingress(2.0) and collector.flush_durable(2.0)
        reader.start()
        for case, gate in cases.items():
            decisions = []
            for mode in ("enabled", "disabled", "fault"):
                fixture = _ControllerFixture()
                fixture.setUp()
                try:
                    root, intent, builder, _, _, _ = fixture._prepare_incomplete_collision_batch(
                        type_mismatch=case == "regular_type")
                    producer = fixture.controller
                    producer._Controller__context.breadcrumb_trace = collector
                    with ExitStack() as stack:
                        def change(owner, name, **kwargs):
                            return stack.enter_context(patch.object(owner, name, **kwargs))
                        if mode == "disabled":
                            change(collector, "is_effectively_enabled", return_value=False)
                        elif mode == "fault":
                            change(collector, "record", side_effect=RuntimeError("private-emission-detail"))
                        if case == "owner": intent.path_pair_id = "other-pair"
                        elif case == "resolution": change(producer, "_Controller__resolve_safe_final_move_paths", return_value=None)
                        elif case == "roots": change(producer, "_Controller__safe_existing_directory", return_value=False)
                        elif case == "authority": producer._Controller__reconciled_remote_path_pair_ids = set()
                        elif case in ("shape", "empty", "count", "relative"):
                            change(builder, "get_staging_collision_relative_paths", return_value={
                                "shape": None, "empty": (), "count": ("leaf",) * 129, "relative": ("",),
                            }[case])
                        elif case == "mount": change(producer, "_Controller__reject_nested_mounts_or_reparse_points", side_effect=OSError("private-mount"))
                        elif case == "candidate":
                            original = producer._Controller__safe_final_move_candidate
                            change(producer, "_Controller__safe_final_move_candidate",
                                   side_effect=lambda base, name: None if "/" in name else original(base, name))
                        elif case in ("missing", "type", "permission"):
                            original = producer._Controller__collision_signature
                            error = {"missing": FileNotFoundError, "type": NotADirectoryError, "permission": PermissionError}[case]
                            calls = []
                            def signature(path):
                                calls.append(path)
                                if case != "permission" or len(calls) == 2:
                                    raise error("private-signature-path")
                                return original(path)
                            change(producer, "_Controller__collision_signature", side_effect=signature)
                        elif case == "temp": change(producer, "_Controller__source_has_lftp_temp_artifact", return_value=True)
                        elif case in ("identity", "size", "mtime"):
                            change(builder, "get_queue_collision_source_identity", return_value={
                                "identity": ("unknown", None, None), "size": ("file", 999, 1_700_000_000_000_000_000),
                                "mtime": ("file", 8, 1),
                            }[case])
                        elif case == "budget": stack.enter_context(patch("controller.controller._COLLISION_COMPARE_MAX_BYTES", 1))
                        before = len(collector.snapshot()["entries"])
                        decision = producer._Controller__queue_collision_leaf_batch(root, intent)
                        decisions.append(decision[:2])
                        rows = collector.snapshot()["entries"][before:]
                        if mode == "enabled":
                            assert len(rows) == 1  # One summary, never one per leaf.
                            facts = rows[0]["details"]
                            assert facts["collision_gate"] == gate
                            assert facts["collision_examined_count"] <= 2
                            assert facts["collision_checked_count"] <= 2
                            if case == "count": assert facts["collision_path_count"] == 129 and facts["collision_snapshot"] == "over_limit"
                            if case == "shape": assert facts["collision_snapshot"] == "not_tuple"
                            if case == "regular_type": assert facts["collision_exception"] == "not_regular"
                            if case == "size": assert facts["collision_sizes_match"] is False and facts["collision_mtimes_match"] is True
                            if case == "mtime": assert facts["collision_sizes_match"] is True and facts["collision_mtimes_match"] is False
                            if case == "accepted": assert facts["collision_checked_count"] == 2 and facts["collision_total_bytes"] == 16
                            expected.append(facts)
                        else: assert rows == []
                    future = getattr(producer, "_Controller__collision_compare_future", None)
                    if future is not None: future.result(timeout=5)
                finally:
                    fixture.doCleanups()
            assert decisions[0] == decisions[1] == decisions[2]
        assert collector.wait_for_ingress(2.0) and collector.flush_durable(2.0)
        finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text().splitlines()]
        finalized = [row for row in finalized if row.get("message") == "queue_preflight_outcome"]
        assert len(finalized) == len(expected)
        assert reader.poll() == len(expected)
        captured_rows = [row["capture"] for row in _artifacts(artifact) if row["capture"].get("state") == "record"]
        captured = [row["projection"] for row in captured_rows]
        assert [row["numbers"]["version"] for row in captured] == [row["version"] for row in finalized]
        assert [row["created_ms"] for row in captured_rows] == [row["created_ms"] for row in finalized]
        assert [row["corr_hash"] for row in captured] == [capture._hash(row["corr_id"]) for row in finalized]
        assert len({row["corr_hash"] for row in captured}) == 1  # Repeated correlations retain distinct versions/order.
        for row, facts in zip(captured, expected):
            assert row["queue_readiness"]["collision_gate"] == facts["collision_gate"]
            for key in facts:
                if key.startswith("collision_"): assert row["queue_readiness"][key] == facts[key]
        text = artifact.read_text()
        assert all(label not in text for label in ("release", "pair-a", "complete.bin", "private-"))
        assert all(len(json.dumps(row)) < 4096 for row in finalized)
    finally:
        reader.close()
        collector.close()


def test_claim_boundary_failures_survive_finalizer_reader_and_sink(tmp_path: Path) -> None:
    assert _ControllerFixture is not None and BreadcrumbTraceCollector is not None
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(lambda: True, max_entries=256,
        policy={"default": "off", "rules": {"queue.readiness": "info", "controller": "info"}},
        durable_enabled=True, durable_path=str(spool))
    reader = _reader(spool, artifact, max_records=256)
    cases = ("reserve", "preclaim_source", "preclaim_destination", "sidecar_create",
             "claim_rename", "claim_sync", "postclaim_source", "postclaim_destination",
             "postclaim_source_validation", "postclaim_destination_validation",
             "restore_rename", "restore_sync", "cleanup_after_rename", "cleanup_after_restore",
             "executor_create", "executor_submit", "executor_callbacks")
    expected = []
    try:
        collector.record("controller", "boundary", {}, category="controller", level="info")
        assert collector.wait_for_ingress(2.0) and collector.flush_durable(2.0)
        reader.start()
        for case in cases:
            decisions = []
            shapes = []
            for mode in ("enabled", "disabled", "emission_fault", "hook_fault"):
                fixture = _ControllerFixture()
                fixture.setUp()
                try:
                    root, intent, _, _, final, staging = fixture._prepare_incomplete_collision_batch()
                    producer = fixture.controller
                    producer._Controller__context.breadcrumb_trace = collector
                    primary = OSError(errno.EIO, "private-error-text", "/private/error-path")
                    secondary = OSError(errno.EACCES if case == "restore_rename" else errno.EXDEV, "private-restore-text")
                    in_claim = False
                    signature_calls = rename_calls = sync_calls = 0
                    caught = []
                    before_payload = sorted(p.read_bytes() for p in Path(staging, "release").rglob("*")
                        if p.is_file() and not p.name.endswith((".json", ".lftp-pget-status")))
                    final_before = {str(p.relative_to(final)): p.read_bytes()
                                    for p in Path(final).rglob("*") if p.is_file()}
                    with ExitStack() as stack:
                        def change(owner, name, **kwargs):
                            return stack.enter_context(patch.object(owner, name, **kwargs))
                        if mode == "disabled": change(collector, "is_effectively_enabled", return_value=False)
                        if mode == "emission_fault": change(collector, "record", side_effect=RuntimeError("private-emission"))
                        original_claim = producer._Controller__claim_and_compare_collision_leaf
                        def run_claim(*args, **kwargs):
                            nonlocal in_claim
                            in_claim = True
                            if mode == "hook_fault":
                                def broken_hook(*args, **kwargs): raise RuntimeError("private-hook")
                                kwargs["claim_diagnostic"] = broken_hook
                            try:
                                return original_claim(*args, **kwargs)
                            except Exception as error:
                                caught.append(error)
                                raise
                            finally: in_claim = False
                        change(producer, "_Controller__claim_and_compare_collision_leaf", side_effect=run_claim)
                        original_signature = producer._Controller__collision_signature
                        def signature(path):
                            nonlocal signature_calls
                            if not in_claim: return original_signature(path)
                            signature_calls += 1
                            selected = {"preclaim_source": 1, "preclaim_destination": 2,
                                "postclaim_source": 6, "postclaim_destination": 7,
                                "restore_rename": 6, "restore_sync": 6, "cleanup_after_restore": 6}
                            if signature_calls == selected.get(case): raise primary
                            value = original_signature(path)
                            if signature_calls == {"postclaim_source_validation": 6,
                                                   "postclaim_destination_validation": 7}.get(case):
                                return value[:2] + (value[2] + 1,) + value[3:]
                            return value
                        change(producer, "_Controller__collision_signature", side_effect=signature)
                        if case == "reserve":
                            original_exists = os.path.lexists
                            stack.enter_context(patch("controller.controller.os.path.lexists",
                                side_effect=lambda p: True if Path(p).name.startswith(".seedsync-retire-") else original_exists(p)))
                        if case == "sidecar_create": change(producer, "_Controller__write_collision_claim_sidecar", side_effect=primary)
                        original_rename = producer._Controller__rename_no_replace
                        def rename(*args):
                            nonlocal rename_calls
                            rename_calls += 1
                            if case in ("claim_rename", "cleanup_after_rename") and rename_calls == 1: raise primary
                            if case == "restore_rename" and rename_calls == 2: raise secondary
                            return original_rename(*args)
                        change(producer, "_Controller__rename_no_replace", side_effect=rename)
                        original_sync = producer._Controller__sync_directory_if_supported
                        def sync(path):
                            nonlocal sync_calls
                            sync_calls += 1
                            if case == "claim_sync" and sync_calls == 1: raise primary
                            if case == "restore_sync" and sync_calls == 2: raise secondary
                            return original_sync(path)
                        change(producer, "_Controller__sync_directory_if_supported", side_effect=sync)
                        if case in ("cleanup_after_rename", "cleanup_after_restore"):
                            change(producer, "_Controller__remove_collision_claim_sidecar", side_effect=secondary)
                        if case == "executor_create": stack.enter_context(patch("controller.controller.ThreadPoolExecutor", side_effect=primary))
                        if case == "executor_submit": stack.enter_context(patch("controller.controller.ThreadPoolExecutor.submit", side_effect=primary))
                        if case == "executor_callbacks": stack.enter_context(patch("controller.controller.Future.add_done_callback", side_effect=primary))
                        before = len(collector.snapshot()["entries"])
                        decision = producer._Controller__queue_collision_preflight(root, intent.command, intent)
                        decisions.append(decision[:2])
                        assert decision[:2] == ("reject", "collision_preflight_error")
                        assert len(caught) == 1
                        if case in ("reserve", "postclaim_source_validation", "postclaim_destination_validation"):
                            assert caught[0].errno == (errno.EEXIST if case == "reserve" else errno.EAGAIN)
                        else: assert caught[0] is (secondary if case == "cleanup_after_rename" else primary)
                        rows = [row for row in collector.snapshot()["entries"][before:]
                                if row["message"] == "queue_preflight_outcome"]
                        if mode in ("enabled", "hook_fault"):
                            assert len(rows) == 1
                            facts = rows[0]["details"]
                            assert facts["collision_gate"] == "claim"
                            if mode == "enabled":
                                step = "postclaim_source" if case in ("restore_rename", "restore_sync", "cleanup_after_restore") else \
                                    "claim_rename" if case == "cleanup_after_rename" else case
                                assert facts["collision_claim_step"] == step
                                assert facts["collision_claim_errno"] == (errno.EEXIST if case == "reserve" else \
                                    errno.EAGAIN if case.endswith("_validation") else errno.EIO)
                                assert facts["collision_claim_cleanup"] == ("failed" if case.startswith("cleanup_") else \
                                    "completed" if case in ("claim_rename", "postclaim_source", "postclaim_destination",
                                        "postclaim_source_validation", "postclaim_destination_validation") else "not_attempted")
                                assert facts["collision_claim_restore"] == ("failed" if case.startswith("restore_") else \
                                    "completed" if case.startswith("postclaim_") or case == "cleanup_after_restore" else "not_attempted")
                                if case.startswith("restore_"): assert facts["collision_claim_restore_errno"] == secondary.errno
                                if case.startswith("cleanup_"): assert facts["collision_claim_cleanup_errno"] == errno.EXDEV
                            expected.append(facts)
                        else: assert rows == []
                    future = getattr(producer, "_Controller__collision_compare_future", None)
                    if future is not None: future.result(timeout=5)
                    after_payload = sorted(p.read_bytes() for p in Path(staging, "release").rglob("*")
                        if p.is_file() and not p.name.endswith((".json", ".lftp-pget-status")))
                    assert after_payload == before_payload
                    assert final_before == {str(p.relative_to(final)): p.read_bytes()
                                            for p in Path(final).rglob("*") if p.is_file()}
                    assert Path(staging, "release", "nested", "partial.bin").read_bytes() == b"part"
                    assert Path(staging, "release", "nested", "partial.bin.lftp-pget-status").exists()
                    producer._Controller__lftp.queue.assert_not_called()
                    shapes.append((Path(staging, "release", "legacy", "extra.bin").exists(),
                        sum(p.is_file() for p in Path(staging, "release").rglob("*"))))
                finally: fixture.doCleanups()
            assert all(decision == decisions[0] for decision in decisions)
            assert all(shape == shapes[0] for shape in shapes)
        assert collector.wait_for_ingress(2.0) and collector.flush_durable(2.0)
        finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text().splitlines()]
        finalized = [row for row in finalized if row.get("message") == "queue_preflight_outcome"]
        assert len(finalized) == len(expected) and all(row["event_type"] == "failure" for row in finalized)
        assert reader.poll() == len(expected)
        captured = [row["capture"] for row in _artifacts(artifact) if row["capture"].get("state") == "record"]
        assert [row["projection"]["numbers"]["version"] for row in captured] == [row["version"] for row in finalized]
        assert [row["created_ms"] for row in captured] == [row["created_ms"] for row in finalized]
        assert [row["projection"]["corr_hash"] for row in captured] == [capture._hash(row["corr_id"]) for row in finalized]
        assert len({row["projection"]["corr_hash"] for row in captured}) == 1
        for row, facts in zip(captured, expected):
            for key in facts:
                if key.startswith("collision_claim_"): assert row["projection"]["queue_readiness"][key] == facts[key]
        assert all(label not in artifact.read_text() for label in ("private-", "release", "pair-a", ".seedsync-retire-"))
        assert all(len(json.dumps(row)) < 4096 for row in finalized)
    finally:
        reader.close()
        collector.close()


def test_collision_gate_projection_rejects_unbounded_or_private_fields() -> None:
    details = {"schema": "queue_readiness.v1", "collision_gate": "/private/path",
        "collision_exception": "private exception", "collision_total_bytes": 2**63,
        "collision_path_count": -1, "collision_checked_count": True,
        "collision_examined_count": "999", "collision_sizes_match": 1, "private_path": "/private/path",
        "collision_claim_step": "/private/path", "collision_claim_exception": "private-error",
        "collision_claim_cleanup": "private-error", "collision_claim_restore": "private-error",
        "collision_claim_errno": 65536, "collision_claim_cleanup_errno": -1, "collision_claim_restore_errno": True}
    facts = capture._queue_readiness_facts("queue_preflight_outcome", details)
    result = observer._sanitize_capture_projection({"queue_readiness": facts})["queue_readiness"]
    assert result["collision_gate"] == result["collision_exception"] == "unknown"
    assert all(result[key] is None for key in ("collision_total_bytes", "collision_path_count",
        "collision_checked_count", "collision_examined_count", "collision_sizes_match"))
    assert "/private/path" not in json.dumps(result)
    assert all(result[key] == "unknown" for key in ("collision_claim_step", "collision_claim_exception",
        "collision_claim_cleanup", "collision_claim_restore"))
    assert all(result[key] is None for key in ("collision_claim_errno", "collision_claim_cleanup_errno",
        "collision_claim_restore_errno"))


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


def test_terminal_failure_queue_fields_survive_finite_capture_projections() -> None:
    details = {
        "schema": "queue_readiness.v1", "phase": "final_decision", "origin": "manual",
        "outcome": "rejected", "reason": "collision_not_terminalizable",
        "dispatch_attempted": False, "intent_was_present": True, "intent_retired": False,
        "intent_settlement_succeeded": False, "intent_phase": "rescan",
        "intent_scoped_rescan_attempts": 1, "collision_claim_present": True,
        "collision_future_present": False, "private_path": "/must/not/escape",
    }
    readiness = capture._queue_readiness_facts("queue_final_decision", details)
    assert readiness is not None
    finalized = observer._sanitize_capture_projection({"queue_readiness": readiness})["queue_readiness"]
    assert finalized["outcome"] == "rejected"
    assert finalized["reason"] == "collision_not_terminalizable"
    assert finalized["intent_retired"] is False
    assert finalized["intent_settlement_succeeded"] is False
    assert finalized["intent_phase"] == "rescan"
    assert finalized["intent_scoped_rescan_attempts"] == 1
    assert finalized["collision_claim_present"] is True
    assert "/must/not/escape" not in json.dumps(finalized)

    row = {
        "message": "queue_authority_handoff", "source": "controller",
        "category": "queue.authority", "event_type": "failure",
        "stage": "queue_authority_handoff",
    }
    authority_details = {
        "schema": "queue_authority_scan_intake.v1", "event": "scan_intake",
        "side": "local", "outcome": "failed", "scan_final": True,
        "result_failed": True, "selected_failed": True,
        "failed_root_is_selected": None, "record_disposition": "pair_unknown",
    }
    intake = capture._queue_authority_handoff_facts(
        row, authority_details, row["source"], row["category"], row["event_type"], row["stage"],
    )
    assert intake is not None and intake["failed_root_is_selected"] is None
    safe_intake = observer._sanitize_capture_projection({"queue_authority_handoff": intake})[
        "queue_authority_handoff"
    ]
    assert safe_intake == intake
    authority_details["selected_failed"] = False
    assert capture._queue_authority_handoff_facts(
        row, authority_details, row["source"], row["category"], row["event_type"], row["stage"],
    ) is None


def test_actual_rejected_queue_terminal_window_survives_saturation_and_sink(tmp_path: Path) -> None:
    if Controller is None or BreadcrumbTraceCollector is None or _ControllerFixture is None:
        pytest.skip("production Controller and durable reader fixture are unavailable")
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=4096,
        policy={"default": "off", "rules": {
            "queue.readiness": "info", "controller": "info", "model.progress": "info",
        }},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact, max_records=4096, max_output_bytes=4 * 1024 * 1024)
    fixture = _ControllerFixture()
    fixture.setUp()
    controller = fixture.controller
    controller._Controller__context.breadcrumb_trace = collector
    root = ModelFile("fixture-root", True)
    root.path_pair_id = "fixture-pair"
    root.remote_size = 10
    root.remote_has_transferable_content = True
    model = Model()
    model.set_base_logger(controller.logger)
    model.add_file(root)
    controller._Controller__model = model
    controller._Controller__path_pairs_by_id = {
        "fixture-pair": SimpleNamespace(remote_path="/remote", local_path="/local"),
    }
    controller._Controller__path_pair_staging_paths = {"fixture-pair": "/local/incomplete"}
    controller._Controller__reconciled_local_path_pair_ids.add("fixture-pair")
    controller._Controller__reconciled_remote_path_pair_ids.add("fixture-pair")
    controller._Controller__model_builder.has_unresolved_staging_collision.return_value = True
    controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()
    controller._Controller__collision_compare_claim = None
    controller._Controller__collision_compare_future = None
    controller._Controller__has_current_local_root_scan_proof = lambda _file: True
    callback = MagicMock()
    command = Controller.Command(Controller.Command.Action.QUEUE, root.file_id, origin="manual")
    command.add_callback(callback)
    intent = DeferredQueueIntent(
        command, root.file_id, "fixture-pair", phase="rescan", scoped_rescan_attempts=1,
    )
    controller._Controller__deferred_queue_intents_map()[root.file_id] = intent
    controller._Controller__queue_collision_preflight = lambda _file, _command, current: (
        "reject", "collision_not_terminalizable", current,
    )
    try:
        assert collector.record(
            "controller", "durable_boundary_probe", {}, category="controller",
            event_type="diagnostic", stage="queue_readiness_test_boundary", level="info",
        ) in {"retained", "enqueued"}
        assert collector.wait_for_ingress(2.0) and collector.flush_durable(2.0)
        baseline_bytes = (spool / "breadcrumbs.jsonl").stat().st_size
        reader.start()

        # This exercises the actual admission producer before normal-lane
        # saturation; processing then emits the retained rejection and
        # callback at the production Queue callsite.
        controller.queue_command(command)
        emitter = collector.create_emitter()
        for index in range(BREADCRUMB_INGRESS_NORMAL_CAPACITY + 400):
            emitter.record("scanner", "progress-{}".format(index), category="model.progress")
        controller._Controller__process_commands()
        assert callback.on_failure.call_count == 1
        assert root.file_id not in controller._Controller__deferred_queue_intents_map()
        controller._Controller__lftp.queue.assert_not_called()
        assert collector.ingress_snapshot()["normal_rejected_count"] > 0
        assert collector.wait_for_ingress(10.0)
        durable_deadline = time.monotonic() + 30.0
        health = collector.durable_snapshot()
        while time.monotonic() < durable_deadline and not (
                health.get("written") == health.get("accepted") and
                health.get("critical_pending") == 0 and health.get("normal_pending") == 0 and
                health.get("writing") is False):
            time.sleep(0.01)
            health = collector.durable_snapshot()
        assert health.get("written") == health.get("accepted")
        assert health.get("lost") == health.get("critical_lost") + health.get("normal_lost")
        assert health.get("critical_lost") == 0
        assert health.get("upstream_normal_ingress_rejected", 0) > 0
        assert health.get("critical_pending") == health.get("normal_pending") == 0
        expected_bytes = (spool / "breadcrumbs.jsonl").stat().st_size - baseline_bytes
        deadline = time.monotonic() + 15.0
        while reader._source_bytes < expected_bytes and time.monotonic() < deadline:
            reader.poll()
        assert reader._source_bytes == expected_bytes, (
            "finalized reader byte drain incomplete: {} of {}".format(reader._source_bytes, expected_bytes)
        )
    finally:
        reader.close()
        collector.close(timeout=2.0)

    records = [item for item in _artifacts(artifact) if item.get("capture", {}).get("state") == "record"]
    projections = [item["capture"]["projection"] for item in records]
    readiness = [item["queue_readiness"] for item in projections if "queue_readiness" in item]
    admitted = next(item for item in readiness if item["event"] == "queue_admission")
    terminal = next(
        item for item in readiness if item["event"] == "queue_final_decision"
        and item.get("outcome") == "rejected" and "intent_was_present" in item
    )
    callback_row = next(
        item for item in readiness if item["event"] == "queue_callback" and item.get("outcome") == "failure"
    )
    assert admitted["phase"] == "admission"
    assert terminal["reason"] == "collision_not_terminalizable"
    assert terminal["intent_was_present"] is True
    assert terminal["intent_retired"] is True
    assert terminal["intent_settlement_succeeded"] is True
    assert callback_row["outcome"] == "failure"
    raw_rows = [
        json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    raw_terminal = next(
        row for row in raw_rows if row.get("message") == "queue_final_decision"
        and row.get("details", {}).get("reason") == "collision_not_terminalizable"
    )
    raw_callback = next(row for row in raw_rows if row.get("message") == "queue_callback")
    assert raw_terminal["event_type"] == raw_callback["event_type"] == "failure"
    boundary_index = next(
        index for index, row in enumerate(raw_rows) if row.get("message") == "durable_boundary_probe"
    )
    source_versions = [
        row["version"] for row in raw_rows[boundary_index + 1:]
        if type(row.get("version")) is int
    ]
    captured_versions = [
        item["capture"]["projection"].get("numbers", {}).get("version") for item in records
        if type(item["capture"]["projection"].get("numbers", {}).get("version")) is int
    ]
    assert captured_versions == source_versions
    assert observer._sanitize_capture_projection({"queue_readiness": terminal})["queue_readiness"] == terminal
    safe_capture = observer._sanitize_capture(records[-1]["capture"])
    assert safe_capture["health_normal_ingress_rejected"] > 0
    assert safe_capture["health_critical_ingress_rejected"] == 0
    assert safe_capture["health_lost"] == (
        safe_capture["health_critical_lost"] + safe_capture["health_normal_lost"]
    )
    serialized = artifact.read_text(encoding="utf-8")
    assert "fixture-root" not in serialized and "fixture-pair" not in serialized


def test_scanner_process_projection_keeps_force_baseline_separate_from_run() -> None:
    wide = 2**53 + 17
    forced, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "force_scan",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "0123456789abcdef", "details": {
            "scanner_side": "local", "decision": "queued", "request_correlation": "fedcba9876543210",
            "scan_run_ref": "0123456789ab", "current_generation": wide,
            "utc": "2026-10-09T03:00:00.123+00:00",
            "monotonic_ms": wide,
        },
    })
    assert irrelevant is False and forced is not None
    force = forced["scanner_process_event"]
    assert force["message"] == "force_scan"
    assert force["decision"] == "queued"
    assert force["current_generation"] == str(wide)
    assert force["generation"] is None
    assert force["run_digest"] == "unknown"
    assert force["scan_run_ref"] == "0123456789ab"
    assert force["scanner_ref"] == "0123456789abcdef"
    assert force["request_ref"] == "fedcba9876543210"
    assert force["monotonic_ms"] == str(wide)
    malformed_utc, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "force_scan",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "0123456789abcdef", "details": {
            "scanner_side": "local", "scan_run_ref": "0123456789ab", "utc": "/private/path",
        },
    })
    assert irrelevant is False and malformed_utc is not None
    assert malformed_utc["scanner_process_event"]["utc"] is None
    wrong_stage, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "force_scan",
        "event_type": "diagnostic", "stage": "foreign", "level": "info",
        "corr_id": "0123456789abcdef", "details": {"scan_run_ref": "0123456789ab"},
    })
    assert irrelevant is False and wrong_stage is not None
    assert "scanner_process_event" not in wrong_stage
    wrong_source, irrelevant = capture._projection({
        "source": "controller", "category": "scanner_process", "message": "force_scan",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "0123456789abcdef", "details": {"scan_run_ref": "0123456789ab"},
    })
    assert irrelevant is False and wrong_source is not None
    assert "scanner_process_event" not in wrong_source
    assert "generation" not in forced["numbers"]

    started, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "scan_started",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "local:0123456789ab:7", "details": {
            "scanner_side": "local", "generation": 7, "session_digest": "must-not-copy",
            "targeted": True, "full": False, "monotonic_ms": wide,
            "requested_members": ["0123456789abcdef"], "requested_pair_count": 1,
            "requested_truncated": False,
        },
    })
    assert irrelevant is False and started is not None
    run = started["scanner_process_event"]
    assert run["run_digest"] == force["scan_run_ref"]
    assert run["generation"] == "7"
    assert run["targeted"] is True and run["full"] is False
    assert run["requested_refs"] == ["0123456789abcdef"]
    assert run["requested_count"] == "1"
    assert run["requested_truncated"] is False
    assert "generation" not in started["numbers"]

    priority, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "scan_priority_requested",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "local:0123456789ab:7", "details": {
            "scanner_side": "local", "decision": "coalesced_pending",
            "request_correlation": "fedcba9876543210", "current_generation": 7,
            "require_successor": True,
        },
    })
    assert irrelevant is False and priority is not None
    priority_event = priority["scanner_process_event"]
    assert priority_event["message"] == "scan_priority_requested"
    assert priority_event["decision"] == "coalesced_pending"
    assert priority_event["require_successor"] is True
    safe_priority = observer._sanitize_capture_projection(priority)["scanner_process_event"]
    assert safe_priority["decision"] == "coalesced_pending"
    assert safe_priority["require_successor"] is True

    unknown_priority, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "scan_priority_requested",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "local:0123456789ab:7", "details": {
            "scanner_side": "local", "decision": "future-private-value",
            "require_successor": 1,
        },
    })
    assert irrelevant is False and unknown_priority is not None
    unknown_event = unknown_priority["scanner_process_event"]
    assert unknown_event["decision"] == "unknown"
    assert unknown_event["require_successor"] == "unknown"
    safe_unknown = observer._sanitize_capture_projection(unknown_priority)["scanner_process_event"]
    assert safe_unknown["decision"] == "unknown"
    assert safe_unknown["require_successor"] == "unknown"
    missing_priority, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "scan_priority_requested",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "local:0123456789ab:7", "details": {"scanner_side": "local"},
    })
    assert irrelevant is False and missing_priority is not None
    assert missing_priority["scanner_process_event"]["require_successor"] is None
    assert observer._sanitize_capture_projection(missing_priority)["scanner_process_event"][
        "require_successor"
    ] is None
    malformed_publication, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "scan_result_published",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "local:0123456789ab:7", "details": {"queue_published": 1},
    })
    assert irrelevant is False and malformed_publication is not None
    assert malformed_publication["scanner_process_event"]["queue_published"] == "unknown"
    assert observer._sanitize_capture_projection(malformed_publication)["scanner_process_event"][
        "queue_published"
    ] == "unknown"
    missing_publication, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "scan_result_published",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "local:0123456789ab:7", "details": {},
    })
    assert irrelevant is False and missing_publication is not None
    assert missing_publication["scanner_process_event"]["queue_published"] is None
    assert observer._sanitize_capture_projection(missing_publication)["scanner_process_event"][
        "queue_published"
    ] is None

    oversized, irrelevant = capture._projection({
        "source": "scanner_process", "category": "scanner_process", "message": "scan_result_published",
        "event_type": "diagnostic", "stage": "scan", "level": "info",
        "corr_id": "local:0123456789ab:8", "details": {
            "scanner_side": "local", "generation": 8, "scanned_members": ["a" * 16] * 9,
            "scanned_pair_count": 9, "scanned_truncated": False,
        },
    })
    assert irrelevant is False and oversized is not None
    assert oversized["scanner_process_event"]["scanned_refs"] is None
    assert oversized["scanner_process_event"]["scanned_count"] == "9"


@pytest.mark.skipif(
    BreadcrumbTraceCollector is None or ScannerProcess is None or ScannerResult is None,
    reason="production ScannerProcess and breadcrumb collector are unavailable",
)
def test_scanner_process_request_and_result_facts_reach_finalized_observer_sink(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(
        lambda: True,
        max_entries=32,
        policy={"default": "off", "rules": {"scanner_process": "info"}},
        durable_enabled=True,
        durable_path=str(spool),
        durable_enabled_getter=lambda: True,
    )

    class LocalProbeScanner:
        path_pair_id = "private-selected-pair"

        def scanned_path_pair_ids(self) -> set[str]:
            return {self.path_pair_id}

    process = ScannerProcess(
        scanner=LocalProbeScanner(), interval_in_ms=0, verbose=False,
        breadcrumb_trace=collector.create_emitter(),
    )
    private_session = process.session_token
    reader = None
    try:
        assert collector.record(
            "controller", "baseline", {"schema": "scanner_process_test_baseline.v1"},
            category="scanner_process", event_type="diagnostic", stage="scan", level="info",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader = _reader(spool, artifact)
        reader.start()

        process.prioritize_scan("private-selected-pair", require_successor=True)
        process.prioritize_scan("private-selected-pair")
        process._ScannerProcess__inline_scan_active.set()
        process._ScannerProcess__inline_scan_target_path_pair_ids = {"private-selected-pair"}
        process.prioritize_scan("private-selected-pair")
        process.prioritize_scan("private-selected-pair", require_successor=True)

        result = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"private-selected-pair"},
            completed_path_pair_ids={"private-selected-pair"}, generation=7,
            session_token=process.session_token, is_full_snapshot=False,
            is_targeted_scan=True,
        )
        process._ScannerProcess__publish_result(result)
        assert process.pop_latest_result() is result

        result_queue = process._ScannerProcess__queue
        assert result_queue is not None
        for _ in range(result_queue.maxsize):
            result_queue.put_nowait(ScannerResult(datetime.now(), []))
        process._ScannerProcess__publish_result(ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={"private-selected-pair"},
            generation=8, session_token=process.session_token,
            is_targeted_scan=True, is_progress=True, is_scan_final=False,
        ))

        assert collector.flush_durable(2.0)
        assert reader.poll() == 6
    finally:
        if reader is not None:
            reader.close()
        process.close_queues()
        collector.close(timeout=2.0)

    rows = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    actual_rows = [row for row in rows if row.get("message") in {
        "scan_priority_requested", "scan_result_published", "scan_result_dropped",
    }]
    assert len(actual_rows) == 6
    assert all(
        row["source"] == "scanner_process" and row["category"] == "scanner_process"
        and row["stage"] == "scan" for row in actual_rows
    )
    semantic_versions = [row["version"] for row in actual_rows]
    assert all(type(version) is int for version in semantic_versions)
    assert len(set(semantic_versions)) == 6
    producer_events = actual_rows

    semantic_priorities = [entry for entry in producer_events if entry["message"] == "scan_priority_requested"]
    assert Counter((entry["details"]["decision"], entry["details"]["require_successor"])
                   for entry in semantic_priorities) == Counter({
        ("queued", True): 1, ("coalesced_pending", False): 1,
        ("coalesced_pending", True): 1, ("coalesced_active", False): 1,
    })
    semantic_results = [entry for entry in producer_events if entry["message"] in {
        "scan_result_published", "scan_result_dropped",
    }]
    assert Counter((entry["message"], entry["details"]["queue_published"])
                   for entry in semantic_results) == Counter({
        ("scan_result_published", True): 1, ("scan_result_dropped", False): 1,
    })
    raw_text = (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8")
    assert "private-selected-pair" not in raw_text and private_session not in raw_text

    sink_rows = [item for item in _artifacts(artifact) if item.get("capture", {}).get("state") == "record"]
    projections = [item["capture"]["projection"] for item in sink_rows]
    scanner_events = [item["scanner_process_event"] for item in projections if "scanner_process_event" in item]
    assert len(scanner_events) == 6
    sink_versions = [projection["numbers"]["version"] for projection in projections
                     if "scanner_process_event" in projection]
    assert sink_versions == [row["version"] for row in actual_rows]
    assert all(item["capture"]["lost"] == 0 and item["capture"]["health_lost"] == 0 for item in sink_rows)
    assert not any(item.get("capture", {}).get("state") == "loss" for item in _artifacts(artifact))
    canonical_projections = [observer._sanitize_capture_projection(projection) for projection in projections]
    canonical = [projection["scanner_process_event"] for projection in canonical_projections
                 if "scanner_process_event" in projection]
    canonical_versions = [projection["numbers"]["version"] for projection in canonical_projections
                          if "scanner_process_event" in projection]
    assert canonical_versions == [row["version"] for row in actual_rows]
    assert [event["message"] for event in canonical] == [event["message"] for event in scanner_events]
    assert [event["decision"] for event in canonical] == [event["decision"] for event in scanner_events]
    assert [event["require_successor"] for event in canonical] == [event["require_successor"] for event in scanner_events]
    assert [event["queue_published"] for event in canonical] == [event["queue_published"] for event in scanner_events]
    sink_priority = [event for event in scanner_events if event["message"] == "scan_priority_requested"]
    assert Counter(event["decision"] for event in sink_priority) == Counter({
        "queued": 1, "coalesced_pending": 2, "coalesced_active": 1,
    })
    assert Counter((event["decision"], event["require_successor"]) for event in sink_priority) == Counter({
        ("queued", True): 1, ("coalesced_pending", False): 1,
        ("coalesced_pending", True): 1, ("coalesced_active", False): 1,
    })
    sink_results = {event["message"]: event for event in scanner_events if event["message"] in {
        "scan_result_published", "scan_result_dropped",
    }}
    assert {message: event["queue_published"] for message, event in sink_results.items()} == {
        "scan_result_published": True, "scan_result_dropped": False,
    }
    artifact_text = artifact.read_text(encoding="utf-8")
    assert "private-selected-pair" not in artifact_text and private_session not in artifact_text


def test_scan_failure_origin_facts_are_finite_and_preserve_unknown_root() -> None:
    generation = 2**53 + 17
    row = {
        "source": "scanner_process", "category": "scanner_process", "message": "scan_failed",
        "event_type": "failure", "stage": "scan", "level": "error",
        "corr_id": f"local:0123456789ab:{generation}",
        "details": {
            "scanner_side": "local", "generation": generation,
            "failure_site": "scan_had_errors", "failure_recoverable": True,
            "failure_errno": 13, "failure_exception": "permission_error",
            "failure_root_ref": None, "failure_pair_ref": "0123456789abcdef",
            "private_path": "/private/root", "exception_text": "do-not-copy",
        },
    }
    projected, irrelevant = capture._projection(row)
    assert irrelevant is False and projected is not None
    event = projected["scanner_process_event"]
    assert event["generation"] == str(generation)
    assert event["failure_site"] == "scan_had_errors"
    assert event["failure_recoverable"] is True
    assert event["failure_errno"] == 13
    assert event["failure_exception"] == "permission_error"
    assert event["failure_root_ref"] is None
    assert event["failure_pair_ref"] == "0123456789abcdef"
    assert "failure_errno" not in projected["numbers"]
    assert capture._scan_failure_origin_facts({
        "failure_exception": "unknown", "failure_errno": 65535,
    })["failure_exception"] == "unknown"
    assert capture._scan_failure_origin_facts({"failure_errno": 65535})["failure_errno"] == 65535

    finalized = observer._sanitize_capture_projection(projected)
    safe_event = finalized["scanner_process_event"]
    assert safe_event["generation"] == str(generation)
    assert safe_event["failure_root_ref"] is None
    assert safe_event["failure_pair_ref"] == "0123456789abcdef"
    serialized = json.dumps(finalized)
    assert "/private/root" not in serialized and "do-not-copy" not in serialized

    malformed = dict(event)
    malformed.update({
        "failure_site": "private-site", "failure_recoverable": 1,
        "failure_errno": 65536, "failure_exception": "private-exception",
        "failure_root_ref": "0123456789ABCDEf", "failure_pair_ref": "short",
    })
    safe_malformed = observer._sanitize_capture_projection({
        **projected, "scanner_process_event": malformed,
    })["scanner_process_event"]
    assert [safe_malformed[field] for field in (
        "failure_site", "failure_recoverable", "failure_errno", "failure_exception",
        "failure_root_ref", "failure_pair_ref", "failure_run_ref",
    )] == [None] * 7

    queue_details = {
        "schema": "queue_readiness.v1", "phase": "rescan_unknown", "outcome": "failed",
        "origin": "manual", "reason": "initial_local_scan_failed",
        "deferred_intent": {
            "phase": "initial_rescan", "reason": "local_ack_missing", "decision": "waiting",
            "rescan_requested": True, "fence_known": True,
            "local_base": 2**53 + 19, "remote_base": 4,
            "local_ack_state": "missing", "remote_ack_state": "newer",
            "local_ack_generation": None, "remote_ack_generation": 5,
            "proof": "not_checked", "pair_ready": None,
            "claim_state": "global_absent", "callback_count": 1, "retry_count": 2,
            "failure": "none", "stop_requested": False, "grace_consumed": False,
            "private_name": "do-not-copy",
        },
        "scan_failure": {
            "side": "local", "kind": "failed", "scope": "targeted", "target_covered": True,
            "terminal": False, "recoverable": False, "result_generation": generation,
            "run_matches_baseline": True, "generation_vs_baseline": "newer",
            "failure_generation_source": "pair", "failure_generation": generation - 1,
            "failure_generation_vs_baseline": "equal",
            "failure_ignored_reason": "equal", "baseline_generation": generation - 1,
            "failure_site": "scan_had_errors", "failure_recoverable": True,
            "failure_errno": 13, "failure_exception": "permission_error",
            "failure_root_ref": None, "failure_pair_ref": "0123456789abcdef",
            "private_path": "/private/root", "exception_text": "do-not-copy",
        },
    }
    queue_facts = capture._queue_readiness_facts("queue_rescan_readiness", queue_details)
    assert queue_facts is not None
    queue_projection = observer._sanitize_capture_projection({"queue_readiness": queue_facts})
    scan_failure = queue_projection["queue_readiness"]["scan_failure"]
    assert scan_failure["kind"] == "failed" and scan_failure["side"] == "local"
    assert scan_failure["recoverable"] is False
    assert scan_failure["result_generation"] == str(generation)
    assert scan_failure["failure_generation_source"] == "pair"
    assert scan_failure["failure_generation"] == str(generation - 1)
    assert scan_failure["failure_generation_vs_baseline"] == "equal"
    assert scan_failure["failure_ignored_reason"] == "equal"
    assert scan_failure["baseline_generation"] == str(generation - 1)
    assert scan_failure["failure_site"] == "scan_had_errors"
    assert scan_failure["failure_recoverable"] is True
    assert scan_failure["failure_errno"] == 13
    assert scan_failure["failure_exception"] == "permission_error"
    assert scan_failure["failure_root_ref"] is None
    assert scan_failure["failure_pair_ref"] == "0123456789abcdef"
    deferred_intent = queue_projection["queue_readiness"]["deferred_intent"]
    assert deferred_intent["phase"] == "initial_rescan"
    assert deferred_intent["reason"] == "local_ack_missing"
    assert deferred_intent["decision"] == "waiting"
    assert deferred_intent["local_base"] == str(2**53 + 19)
    assert deferred_intent["remote_ack_generation"] == "5"
    assert deferred_intent["claim_state"] == "global_absent"
    assert "do-not-copy" not in json.dumps(queue_projection)
    queue_serialized = json.dumps(queue_projection)
    assert "/private/root" not in queue_serialized and "do-not-copy" not in queue_serialized

    malformed_queue_details = {
        **queue_details,
        "deferred_intent": {
            **queue_details["deferred_intent"], "phase": "private-phase",
            "reason": "private-reason", "local_base": True, "callback_count": 2**63,
            "rescan_requested": 1,
        },
        "scan_failure": {
            **queue_details["scan_failure"],
            "failure_site": "private-site", "failure_recoverable": 1,
            "failure_errno": True, "failure_exception": "private-exception",
            "failure_root_ref": "not-a-reference", "failure_pair_ref": "private-pair",
        },
    }
    malformed_queue = capture._queue_readiness_facts("queue_rescan_readiness", malformed_queue_details)
    assert malformed_queue is not None
    safe_malformed_queue = observer._sanitize_capture_projection({
        "queue_readiness": malformed_queue,
    })["queue_readiness"]["scan_failure"]
    assert [safe_malformed_queue[field] for field in (
        "failure_site", "failure_recoverable", "failure_errno", "failure_exception",
        "failure_root_ref", "failure_pair_ref", "failure_run_ref",
    )] == [None] * 7
    malformed_intent = safe_malformed_queue["deferred_intent"]
    assert malformed_intent["phase"] == "unknown"
    assert malformed_intent["reason"] == "unknown"
    assert malformed_intent["local_base"] is None
    assert malformed_intent["callback_count"] is None
    assert malformed_intent["rescan_requested"] is None

    started, irrelevant = capture._projection({
        **row, "message": "scan_started", "details": {"scanner_side": "local", "generation": generation},
    })
    assert irrelevant is False and started is not None
    assert not any(field.startswith("failure_") for field in started["scanner_process_event"])


def test_lifecycle_persist_projection_is_finite_and_source_shaped() -> None:
    before, irrelevant = capture._projection({
        "source": "model_builder", "category": "lifecycle.persist", "message": "persist_authority_before",
        "event_type": "diagnostic", "stage": "persist_authority", "level": "info",
        "corr_id": "0123456789abcdef", "details": {
            "local_present": True, "remote_present": False, "local_size_present": True,
            "downloaded_marker_present": True, "final_move_marker_present": False, "unknown_local": False,
            "pre_state": "downloaded", "fence": "none", "file_name": "private-name.iso",
            "path": "/private/path",
        },
    })
    assert irrelevant is False and before is not None
    assert before["lifecycle_persist"] == {
        "event": "persist_authority_before", "corr_ref": "0123456789abcdef", "fence": "none",
        "pre_state": "downloaded", "local_present": True, "remote_present": False,
        "local_size_present": True, "downloaded_marker_present": True,
        "final_move_marker_present": False, "unknown_local": False,
    }
    assert "private-name.iso" not in json.dumps(before) and "/private/path" not in json.dumps(before)

    after, irrelevant = capture._projection({
        "source": "model_builder", "category": "lifecycle.persist", "message": "persist_authority_after",
        "event_type": "diagnostic", "stage": "persist_authority", "level": "info",
        "corr_id": "fedcba9876543210", "details": {"state_category": "default", "fence": "none"},
    })
    assert irrelevant is False and after is not None
    assert after["lifecycle_persist"] == {
        "event": "persist_authority_after", "corr_ref": "fedcba9876543210",
        "fence": "none", "state_category": "default",
    }

    live_publication, irrelevant = capture._projection({
        "source": "model_builder", "category": "lifecycle.persist", "message": "model_live_publication",
        "event_type": "diagnostic", "stage": "persist_authority", "level": "info",
        "corr_id": "0123456789abcdef", "details": {
            "candidate_state": "downloaded", "live_state": "downloaded",
            "build_kind": "full_build", "adoption_kind": "full_model_adopted",
            "candidate_version": 2**53 + 1, "live_version": 2**53 + 3,
            "scope_version": 2**53 + 5, "local_size": 2**53 + 7, "child_count": 3,
            "local_flow_schema": "exact_local_flow_join.v1",
            "local_flow_pre_poll_selected": False, "local_flow_input": "member",
            "local_flow_basis": "accepted_current", "local_flow_generation": 2**53 + 9,
            "local_flow_bucket": "member", "file_name": "private-name.iso", "path": "/private/path",
        },
    })
    assert not irrelevant and live_publication is not None
    assert live_publication["lifecycle_persist"] == {
        "event": "model_live_publication", "corr_ref": "0123456789abcdef",
        "candidate_state": "downloaded", "live_state": "downloaded",
        "build_kind": "full_build", "adoption_kind": "full_model_adopted",
        "candidate_version": str(2**53 + 1), "live_version": str(2**53 + 3),
        "scope_version": str(2**53 + 5), "local_size": str(2**53 + 7), "child_count": "3",
        "exact_local_flow_join": {
            "schema": "exact_local_flow_join.v1", "pre_poll_selected": False,
            "input": "member", "basis": "accepted_current",
            "generation": str(2**53 + 9), "bucket": "member",
        },
    }
    assert not {"scope_version", "local_size", "child_count"}.intersection(live_publication["numbers"])
    assert "private-name.iso" not in json.dumps(live_publication) and "/private/path" not in json.dumps(live_publication)

    malformed_publication, irrelevant = capture._projection({
        "source": "model_builder", "category": "lifecycle.persist", "message": "model_live_publication",
        "event_type": "diagnostic", "stage": "persist_authority", "level": "info",
        "corr_id": "0123456789abcdef", "details": {
            "candidate_state": "private-state", "live_state": "downloaded",
            "build_kind": "private-build", "adoption_kind": "full_model_adopted",
            "candidate_version": True, "live_version": 2**63,
            "scope_version": 2**53 + 11, "local_size": True, "child_count": -1,
            "local_flow_schema": "exact_local_flow_join.v1", "local_flow_pre_poll_selected": 1,
            "local_flow_input": ["private"], "local_flow_basis": "accepted_current",
            "local_flow_generation": 2**63, "local_flow_bucket": "member",
        },
    })
    assert not irrelevant and malformed_publication is not None
    malformed_facts = malformed_publication["lifecycle_persist"]
    assert malformed_facts["candidate_state"] == "unknown"
    assert malformed_facts["candidate_version"] is malformed_facts["live_version"] is None
    assert malformed_facts["scope_version"] == str(2**53 + 11)
    assert malformed_facts["local_size"] is malformed_facts["child_count"] is None
    assert malformed_facts["exact_local_flow_join"] == {
        "schema": "exact_local_flow_join.v1", "pre_poll_selected": None,
        "input": "unknown", "basis": "accepted_current", "generation": None, "bucket": "member",
    }

    malformed, irrelevant = capture._projection({
        "source": "model_builder", "category": "lifecycle.persist", "message": "persist_authority_before",
        "event_type": "diagnostic", "stage": "persist_authority", "level": "info",
        "corr_id": "/private/path", "details": {
            "pre_state": "private-state", "fence": "/private/fence", "local_present": 1,
        },
    })
    assert irrelevant is False and malformed is not None
    facts = malformed["lifecycle_persist"]
    assert facts["corr_ref"] is None and facts["pre_state"] == facts["fence"] == "unknown"
    assert facts["local_present"] is None
    for key, wrong_value in (
        ("source", "controller"), ("category", "model.lifecycle"), ("stage", "lifecycle"),
    ):
        foreign, irrelevant = capture._projection({
            "source": "model_builder", "category": "lifecycle.persist", "message": "persist_authority_before",
            "event_type": "diagnostic", "stage": "persist_authority", "level": "info",
            "corr_id": "0123456789abcdef", "details": {"pre_state": "downloaded", "fence": "none"},
            key: wrong_value,
        })
        assert irrelevant is False and foreign is not None
        assert "lifecycle_persist" not in foreign


@pytest.mark.skipif(os.name != "posix", reason="requires native durable file acknowledgement")
@pytest.mark.skipif(BreadcrumbTraceCollector is None or ModelBuilder is None or Model is None,
                    reason="production builder unavailable")
def test_model_live_publication_facts_reach_finalized_reader_and_sink(tmp_path: Path) -> None:
    spool = tmp_path / "live-publication-spool"
    spool.mkdir()
    output = tmp_path / "live-publication-sink.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=64,
        policy={"default": "off", "rules": {"lifecycle.persist": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    builder = ModelBuilder()
    pair_id = "pair-live"
    root_name = "live-scope"
    root_id = ModelFile.build_file_id(root_name, pair_id)
    local_root = SystemFile(root_name, 2**53 + 17, True)
    local_root.path_pair_id = pair_id
    local_root.add_child(SystemFile("payload.bin", 10, False))
    local_root.add_child(SystemFile("checksums.txt", 7, False))
    remote_root = SystemFile(root_name, 2**53 + 19, True)
    remote_root.path_pair_id = pair_id
    remote_root.add_child(SystemFile("payload.bin", 10, False))
    remote_root.add_child(SystemFile("checksums.txt", 7, False))
    builder.set_local_files([local_root])
    builder.set_remote_files([remote_root])
    builder.set_downloaded_files({root_id})
    builder.set_stop_resume_trace_breadcrumb(collector.create_emitter())

    try:
        candidate = builder.build_model()
        subject_ids = builder.record_lifecycle_candidate_publication(candidate, "full_build")
        assert subject_ids == {root_id}
        live = Model()
        live.add_file(candidate.get_file(root_id))
        # Advance the same pair's real model version once more while retaining
        # the selected root; the scope version remains a direct O(1) lookup.
        unrelated = ModelFile("other-live-file", False)
        unrelated.path_pair_id = pair_id
        live.add_file(unrelated)
        builder.record_lifecycle_live_publication(
            candidate, live, subject_ids, "full_build", "full_model_adopted",
        )
        assert collector.flush_durable(2.0)
    finally:
        collector.close(timeout=2.0)

    finalized = [
        json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    publication_rows = [
        row for row in finalized if row.get("message") in {"model_candidate", "model_live_publication"}
    ]
    assert [row["message"] for row in publication_rows] == ["model_candidate", "model_live_publication"]
    assert publication_rows[0]["corr_id"] == publication_rows[1]["corr_id"]
    assert all(row.get("source") == "model_builder" and row.get("category") == "lifecycle.persist"
               and row.get("stage") == "persist_authority" for row in publication_rows)
    assert all(row.get("file_id") is None and row.get("path_pair_id") is None for row in publication_rows)

    sink = observer.JsonArtifactSink(output)
    for row in publication_rows:
        projection, irrelevant = capture._projection(row)
        assert not irrelevant and projection is not None
        safe_projection = observer._sanitize_capture_projection(projection)
        sink.persist({
            "schema": "incoming-recovery-observer-sample.v1", "event": "capture", "outcome": "success",
            "capture": {"state": "record", "source": "controller", "projection": safe_projection},
        })

    saved = _artifacts(output)
    assert len(saved) == 2
    saved_facts = [row["capture"]["projection"]["lifecycle_persist"] for row in saved]
    assert [facts["event"] for facts in saved_facts] == ["model_candidate", "model_live_publication"]
    live_facts = saved_facts[1]
    assert live_facts["scope_version"] == str(live.scope_version(pair_id))
    assert live_facts["local_size"] == str(2**53 + 17)
    assert live_facts["child_count"] == "2"
    assert live_facts["candidate_version"] == str(candidate.version)
    assert live_facts["live_version"] == str(live.version)
    assert live_facts["exact_local_flow_join"]["schema"] == "exact_local_flow_join.v1"
    artifact_text = output.read_text(encoding="utf-8")
    assert root_name not in artifact_text and pair_id not in artifact_text

    # The new O(1) measurements remain behind the existing category gate.
    disabled_builder = ModelBuilder()
    with patch.object(live, "scope_version", side_effect=AssertionError("disabled diagnostic read")):
        disabled_builder.record_lifecycle_live_publication(
            candidate, live, subject_ids, "full_build", "full_model_adopted",
        )

    class FailingEmitter:
        @staticmethod
        def is_effectively_enabled(category: object, level: object = "info") -> bool:
            return category == "lifecycle.persist" and level == "info"

        @staticmethod
        def record(*args: object, **kwargs: object) -> str:
            raise RuntimeError("diagnostic emitter unavailable")

    failing_builder = ModelBuilder()
    failing_builder.set_stop_resume_trace_breadcrumb(FailingEmitter())
    failing_builder.record_lifecycle_live_publication(
        candidate, live, subject_ids, "full_build", "candidate_not_adopted",
    )


def test_queue_readiness_nested_scan_diagnostics_keep_exact_finite_values() -> None:
    wide = 2**53 + 19
    details = {
        "schema": "queue_readiness.v1", "phase": "rescan_unknown", "outcome": "failed",
        "origin": "manual", "reason": "initial_local_scan_failed",
        "scan_failure": {
            "side": "local", "kind": "failed", "scope": "targeted", "target_covered": True,
            "terminal": False, "recoverable": True, "result_generation": wide,
            "run_matches_baseline": True, "generation_vs_baseline": "newer",
            "failure_generation_source": "pair", "failure_generation": wide - 2,
            "failure_generation_vs_baseline": "older",
            "failure_ignored_reason": "older", "baseline_generation": wide - 3,
            "session_token": "do-not-copy", "path": "/private/path",
        },
        "fresh_root_proof": {
            "result": "accepted", "reason": "accepted", "root_shape_ok": True,
            "remote_ready": True, "overlay_unknown": True, "process_generation": wide,
            "effective_work_floor": 4, "root_generation": wide, "source_present": True,
            "run_matches_current": True, "builder_proof_matches_source": True,
            "root_proof_gate": {
                "gate": "accepted", "active_generation": wide, "outcome_generation": wide,
                "root_final_complete": True, "root_staging_complete": True,
                "confirmed_delete_generation": 4, "source_root_present": True,
                "source_root_matches": True,
            },
            "root_vs_work_floor": "at_or_above", "root_vs_process_generation": "at_or_below",
            "run_matches_baseline": True, "generation_vs_baseline": "newer",
            "session_token": "do-not-copy", "path": "/private/path",
        },
    }
    facts = capture._queue_readiness_facts("queue_rescan_readiness", details)
    assert facts is not None
    assert facts["scan_failure"]["result_generation"] == str(wide)
    assert facts["scan_failure"]["failure_generation"] == str(wide - 2)
    assert facts["scan_failure"]["failure_ignored_reason"] == "older"
    assert facts["scan_failure"]["baseline_generation"] == str(wide - 3)
    assert facts["fresh_root_proof"]["process_generation"] == str(wide)
    assert facts["fresh_root_proof"]["root_generation"] == str(wide)
    assert facts["fresh_root_proof"]["root_proof_gate"] == {
        "gate": "accepted", "active_generation": str(wide), "outcome_generation": str(wide),
        "root_final_complete": True, "root_staging_complete": True,
        "confirmed_delete_generation": "4", "source_root_present": True,
        "source_root_matches": True,
    }
    text = json.dumps(facts)
    assert "do-not-copy" not in text and "/private/path" not in text

    finalized_projection = observer._sanitize_capture_projection({"queue_readiness": facts})
    assert finalized_projection["queue_readiness"]["scan_failure"]["result_generation"] == str(wide)
    assert finalized_projection["queue_readiness"]["scan_failure"]["failure_generation"] == str(wide - 2)
    assert finalized_projection["queue_readiness"]["scan_failure"]["failure_ignored_reason"] == "older"
    assert finalized_projection["queue_readiness"]["scan_failure"]["baseline_generation"] == str(wide - 3)
    assert finalized_projection["queue_readiness"]["fresh_root_proof"]["root_generation"] == str(wide)

    malformed = capture._queue_readiness_facts("queue_rescan_readiness", {
        **details,
        "scan_failure": {"side": "secret", "terminal": 1, "result_generation": True},
        "fresh_root_proof": {"result": "secret", "reason": "/private", "root_shape_ok": "true",
                             "root_proof_gate": {"gate": "/private/gate", "active_generation": True,
                                                  "root_final_complete": "true",
                                                  "source_root_present": 1},
                             "process_generation": wide + 1},
    })
    assert malformed is not None
    malformed_final = observer._sanitize_capture_projection({"queue_readiness": malformed})
    assert malformed_final["queue_readiness"]["scan_failure"] == {
        "side": "unknown", "kind": "unknown", "scope": "unknown", "target_covered": None,
        "terminal": None, "recoverable": None, "result_generation": None,
        "run_matches_baseline": None, "generation_vs_baseline": "unknown",
        "failure_generation_source": "unknown", "failure_generation": None,
        "failure_generation_vs_baseline": "unknown",
        "failure_ignored_reason": "unknown", "baseline_generation": None,
        "failure_site": None, "failure_recoverable": None, "failure_errno": None,
        "failure_exception": None, "failure_root_ref": None, "failure_pair_ref": None,
        "failure_run_ref": None,
    }
    assert malformed_final["queue_readiness"]["fresh_root_proof"]["result"] == "unknown"
    assert malformed_final["queue_readiness"]["fresh_root_proof"]["root_shape_ok"] is None
    assert malformed_final["queue_readiness"]["fresh_root_proof"]["process_generation"] == str(wide + 1)
    assert malformed_final["queue_readiness"]["fresh_root_proof"]["root_proof_gate"] == {
        "gate": "unknown", "active_generation": None, "outcome_generation": None,
        "root_final_complete": None, "root_staging_complete": None,
        "confirmed_delete_generation": None, "source_root_present": None,
        "source_root_matches": None,
    }


def test_queue_readiness_boundaries_survive_actual_finalized_controller_emitter(tmp_path: Path) -> None:
    if Controller is None or BreadcrumbTraceCollector is None:
        pytest.skip("production controller breadcrumb emitter is unavailable")
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=64,
        policy={"default": "off", "rules": {"queue.readiness": "info", "queue.authority": "info", "controller": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact, max_records=256)
    producer = Controller.__new__(Controller)
    producer._Controller__context = SimpleNamespace(breadcrumb_trace=collector)
    secret = "private-queue-target.bin"
    try:
        # Exercise the production Controller readiness emitter across admission,
        # model decision, rescan deferral and dispatch boundaries.
        readiness_rows = (
            ("queue_admission", {"schema": "queue_readiness.v1", "phase": "admission", "origin": "manual",
                                 "accepted": True, "queue_depth": 2, "queue_depth_known": True,
                                 "private_path": "/must/not/escape"}),
            ("queue_model_boundary", {"schema": "queue_readiness.v1", "phase": "model_boundary", "origin": "manual",
                                      "explicit_stop": False, "already_active": False,
                                      "collision_observation": "absent", "collision_unresolved": False,
                                      "local_root_scan_proof_current": False}),
            ("queue_rescan_readiness", {"schema": "queue_readiness.v1", "phase": "rescan_request", "origin": "manual",
                                        "outcome": "pending", "reason": "scoped_rescan_requested", "ready": False}),
            ("queue_final_decision", {"schema": "queue_readiness.v2", "phase": "final_decision", "origin": "manual",
                                      "outcome": "deferred", "decision_boundary": "scan_authority",
                                      "decision": "defer", "reason": "initial_scan_authority_pending",
                                      "dispatch_attempted": False, "local_reconciled": False,
                                      "remote_reconciled": False, "unknown_local_overlay": True,
                                      "path_pair_scope_configured": True, "publication_id": 7, "model_version": 8}),
            ("queue_dispatch_boundary", {"schema": "queue_readiness.v1", "phase": "dispatch_boundary", "origin": "manual",
                                         "dispatch_attempted": True, "dispatch_mode": "async",
                                         "operation_sequence_present": True, "explicit_stop": False,
                                         "collision_observation": "absent", "exclusions_present": False}),
        )
        # Establish a genuine producer session boundary and nonempty finalized
        # spool before the independent reader arms at EOF. A hand-authored
        # health file would not prove the collector's current session is live.
        assert collector.record(
            "controller", "durable_boundary_probe", {},
            category="controller", event_type="diagnostic",
            stage="queue_readiness_test_boundary", level="info",
        ) in {"retained", "enqueued"}
        assert collector.wait_for_ingress(2.0)
        assert collector.flush_durable(2.0)
        durable_state = collector.durable_snapshot()
        assert durable_state["session_boundary_written"] is True
        assert durable_state["written"] >= 1
        baseline_written = durable_state["written"]
        reader.start()
        for message, details in readiness_rows:
            assert Controller._Controller__record_queue_readiness_trace(
                producer, secret, message, details,
            ) is None
        # Use the real failed-result intake to produce the final row. Its
        # fixed diagnostic projection must remain safe through durable drain,
        # the independent reader, and the real artifact sink.
        pair_id = "private-scan-pair"
        failed_file_id = ModelFile.build_file_id("private-scan-root", pair_id)
        failed_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, failed_file_id),
            failed_file_id, pair_id, phase="initial_rescan", rescan_requested=True,
            rescan_generations=(("private-local-session", 0), ("private-remote-session", 0)),
        )
        producer._Controller__deferred_queue_intents = {failed_file_id: failed_intent}
        producer._Controller__scan_authority_tokens = {"local": {}, "remote": {}}
        failed_result = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={pair_id}, failed=True,
            generation=1, session_token="private-local-session", is_targeted_scan=True,
            recoverable_failure_path_pair_ids={pair_id}, terminal_failure_path_pair_ids=set(),
            failure_origin={
                "failure_site": "scan_had_errors", "failure_recoverable": True,
                "failure_errno": 13, "failure_exception": "permission_error",
                "failure_root_ref": opaque_trace_correlation("scan-root|" + failed_file_id),
                "failure_pair_ref": opaque_trace_correlation("scan-pair|" + pair_id),
            },
        )
        failed_result._scan_failure_tokens_by_pair = {pair_id: ("private-local-session", 1)}
        Controller._record_path_pair_scan_tokens(producer, failed_result, None)

        ignored_pair_id = "private-ignored-pair"
        ignored_file_id = ModelFile.build_file_id("private-ignored-root", ignored_pair_id)
        ignored_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, ignored_file_id),
            ignored_file_id, ignored_pair_id, phase="initial_rescan", rescan_requested=True,
            rescan_generations=(("private-ignored-session", 12), ("private-remote-session", 0)),
        )
        ignored_result = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={ignored_pair_id}, failed=True,
            generation=13, session_token="private-ignored-session", is_targeted_scan=True,
            unknown_path_pair_ids={ignored_pair_id}, recoverable_failure_path_pair_ids={ignored_pair_id},
            terminal_failure_path_pair_ids=set(),
            failure_origin={
                "failure_site": "scan_had_errors", "failure_recoverable": True,
                "failure_errno": 13, "failure_exception": "permission_error",
                "failure_root_ref": None,
                "failure_pair_ref": opaque_trace_correlation("scan-pair|" + ignored_pair_id),
            },
        )
        ignored_result._scan_failure_tokens_by_pair = {
            ignored_pair_id: ("private-ignored-session", 11),
        }
        producer._Controller__deferred_queue_intents = {ignored_file_id: ignored_intent}
        Controller._record_path_pair_scan_tokens(producer, ignored_result, None)

        mismatch_pair_id = "private-mismatch-pair"
        mismatch_file_id = ModelFile.build_file_id("private-mismatch-root", mismatch_pair_id)
        mismatch_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, mismatch_file_id),
            mismatch_file_id, mismatch_pair_id, phase="initial_rescan", rescan_requested=True,
            rescan_generations=(("private-mismatch-baseline", 20), ("private-remote-session", 0)),
        )
        mismatch_result = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={mismatch_pair_id}, failed=True,
            generation=21, session_token="private-mismatch-result", is_targeted_scan=True,
            recoverable_failure_path_pair_ids={mismatch_pair_id}, terminal_failure_path_pair_ids=set(),
            failure_origin={
                "failure_site": "scan_had_errors", "failure_recoverable": True,
                "failure_errno": 13, "failure_exception": "permission_error",
                "failure_root_ref": opaque_trace_correlation(
                    "scan-root|" + ModelFile.build_file_id("different-root", mismatch_pair_id),
                ),
                "failure_pair_ref": opaque_trace_correlation("scan-pair|" + mismatch_pair_id),
            },
        )
        mismatch_result._scan_failure_tokens_by_pair = {
            mismatch_pair_id: ("private-mismatch-other", 21),
        }
        producer._Controller__deferred_queue_intents = {mismatch_file_id: mismatch_intent}
        Controller._record_path_pair_scan_tokens(producer, mismatch_result, None)

        incomplete_pair_id = "private-incomplete-pair"
        incomplete_file_id = ModelFile.build_file_id("private-incomplete-root", incomplete_pair_id)
        incomplete_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, incomplete_file_id),
            incomplete_file_id, incomplete_pair_id, phase="initial_rescan", rescan_requested=True,
            rescan_generations=(("private-incomplete-session", 2), ("private-remote-session", 0)),
        )
        incomplete_result = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids=set(), completed_path_pair_ids=set(), failed=False,
            generation=3, session_token="private-incomplete-session", is_scan_final=False,
            recoverable_failure_path_pair_ids={incomplete_pair_id},
            failure_origin={
                "failure_site": "scan_had_errors", "failure_recoverable": True,
                "failure_errno": 13, "failure_exception": "permission_error",
                "failure_root_ref": opaque_trace_correlation("scan-root|" + incomplete_file_id),
                "failure_pair_ref": opaque_trace_correlation("scan-pair|" + incomplete_pair_id),
            },
        )
        producer._Controller__deferred_queue_intents = {incomplete_file_id: incomplete_intent}
        Controller._record_path_pair_scan_tokens(producer, incomplete_result, None)

        unknown_pair_id = "private-unknown-pair"
        unknown_file_id = ModelFile.build_file_id("private-unknown-root", unknown_pair_id)
        unknown_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, unknown_file_id),
            unknown_file_id, unknown_pair_id, phase="initial_rescan", rescan_requested=True,
            rescan_generations=(("private-unknown-session", 5), ("private-remote-session", 0)),
        )
        out_of_scope_file_id = ModelFile.build_file_id("private-rescan-root", unknown_pair_id)
        out_of_scope_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, out_of_scope_file_id),
            out_of_scope_file_id, unknown_pair_id, phase="rescan", rescan_requested=True,
            rescan_generations=(("private-unknown-session", 5), ("private-remote-session", 0)),
        )
        unknown_result = ScannerResult(
            datetime.now(), [], failed=False, generation=6, session_token="private-unknown-session",
            is_scan_final=True, unknown_path_pair_ids={unknown_pair_id},
        )
        producer._Controller__deferred_queue_intents = {
            unknown_file_id: unknown_intent, out_of_scope_file_id: out_of_scope_intent,
        }
        Controller._record_path_pair_scan_tokens(producer, unknown_result, None)
        assert unknown_intent.rescan_failure_reason is not None
        assert out_of_scope_intent.rescan_failure_reason is None
        Controller._record_path_pair_scan_tokens(producer, unknown_result, None)

        invalid_pair_id = "private-invalid-pair"
        invalid_file_id = ModelFile.build_file_id("private-invalid-root", invalid_pair_id)
        invalid_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, invalid_file_id),
            invalid_file_id, invalid_pair_id, phase="initial_rescan", rescan_requested=True,
            rescan_generations=(("private-invalid-session", 8), ("private-remote-session", 0)),
        )
        invalid_result = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={invalid_pair_id},
            completed_path_pair_ids={invalid_pair_id}, failed=False, generation=9,
            session_token="private-invalid-session", is_scan_final=False,
        )
        invalid_result._scan_authority_tokens_by_pair = {invalid_pair_id: ("private-invalid-session", True)}
        producer._Controller__deferred_queue_intents = {invalid_file_id: invalid_intent}
        Controller._record_path_pair_scan_tokens(producer, invalid_result, None)

        recorded_pair_id = "private-recorded-pair"
        recorded_file_id = ModelFile.build_file_id("private-recorded-root", recorded_pair_id)
        recorded_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, recorded_file_id),
            recorded_file_id, recorded_pair_id, phase="initial_rescan", rescan_requested=True,
            rescan_generations=(("private-recorded-session", 10), ("private-remote-session", 0)),
        )
        recorded_result = ScannerResult(
            datetime.now(), [], scanned_path_pair_ids={recorded_pair_id},
            completed_path_pair_ids={recorded_pair_id}, failed=False, generation=11,
            session_token="private-recorded-session", is_scan_final=False,
        )
        recorded_result._scan_authority_tokens_by_pair = {
            recorded_pair_id: ("private-recorded-session", 11),
        }
        producer._Controller__deferred_queue_intents = {recorded_file_id: recorded_intent}
        Controller._record_path_pair_scan_tokens(producer, recorded_result, None)

        proof_pair_id = "private-proof-pair"
        proof_file_id = ModelFile.build_file_id("private-proof-root", proof_pair_id)
        proof_file = ModelFile("private-proof-root", True)
        proof_file.path_pair_id = proof_pair_id
        proof_intent = DeferredQueueIntent(
            Controller.Command(Controller.Command.Action.QUEUE, proof_file_id),
            proof_file_id, proof_pair_id, phase="initial_rescan", rescan_requested=True,
            rescan_generations=(("private-proof-session", 2), ("private-remote-session", 0)),
            rescan_failure_reason="initial_local_scan_recoverable_failed",
        )
        proof_root = SystemFile("private-proof-root", 1, True)
        proof_root.path_pair_id = proof_pair_id
        producer._Controller__deferred_queue_intents = {proof_file_id: proof_intent}
        producer._Controller__command_queue = Queue()
        producer._Controller__model = SimpleNamespace(get_file=lambda _file_id: proof_file)
        producer._Controller__persist = SimpleNamespace(stopped_file_names=set())
        producer._Controller__reconciled_remote_path_pair_ids = {proof_pair_id}
        producer._Controller__local_scan_process = SimpleNamespace(
            session_token="private-proof-session", generation=5,
            root_scan_authority_snapshot=lambda _pair_id: ("private-proof-session", 5, 3),
        )
        producer._Controller__remote_scan_process = SimpleNamespace(
            session_token="private-remote-session", generation=1,
        )
        proof_scan = ScannerResult(
            datetime.now(), [proof_root], scanned_path_pair_ids={proof_pair_id}, generation=4,
            is_progress=True, session_token="private-proof-session",
            root_scan_outcomes_by_pair={proof_pair_id: {"private-proof-root": (True, False)}},
        )
        proof_accumulator = _ProgressiveScanAccumulator()
        proof_accumulator.apply([proof_scan], {proof_pair_id})
        producer._Controller__progressive_local_scan_state = proof_accumulator
        producer._Controller__scan_authority_tokens = {"local": {}, "remote": {}}
        producer._Controller__model_builder = SimpleNamespace(
            unknown_local_path_pair_ids_snapshot=lambda: frozenset({proof_pair_id}),
            has_current_local_root_scan_proof=lambda *_args, **_kwargs: False,
        )
        producer._Controller__retire_deferred_queue_intent = lambda *_args, **_kwargs: False
        Controller._Controller__prepare_deferred_queue_retries(producer)
        assert proof_file_id in producer._Controller__deferred_queue_intents
        # Exercise the actual ordinary-rescan retained-wait branch as well as
        # the initial-rescan proof path above. The same live source is
        # inspected, but its diagnostic result cannot change the intent.
        proof_intent.phase = "rescan"
        proof_intent.diagnostic_last_wait_monotonic = None
        Controller._Controller__prepare_deferred_queue_retries(producer)
        assert proof_file_id in producer._Controller__deferred_queue_intents

        pending_pair_id = "private-pending-pair"
        pending_file_id = ModelFile.build_file_id("private-pending-root", pending_pair_id)
        pending_file = ModelFile("private-pending-root", True)
        pending_file.path_pair_id = pending_pair_id
        pending_command = Controller.Command(Controller.Command.Action.QUEUE, pending_file_id)
        pending_command.add_callback(SimpleNamespace())
        pending_intent = DeferredQueueIntent(
            pending_command, pending_file_id, pending_pair_id, phase="initial_rescan",
            rescan_requested=True,
            rescan_generations=(("private-pending-local", 2), ("private-pending-remote", 4)),
        )
        producer._Controller__deferred_queue_intents = {pending_file_id: pending_intent}
        producer._Controller__scan_authority_tokens = {"local": {}, "remote": {}}
        producer._Controller__command_queue = Queue()
        producer._Controller__model = SimpleNamespace(get_file=lambda _file_id: pending_file)
        producer._Controller__persist = SimpleNamespace(stopped_file_names=set())
        producer._Controller__local_scan_process = SimpleNamespace(
            session_token="private-pending-local", generation=2,
        )
        producer._Controller__remote_scan_process = SimpleNamespace(
            session_token="private-pending-remote", generation=4,
        )
        producer._Controller__reconciled_local_path_pair_ids = set()
        producer._Controller__reconciled_remote_path_pair_ids = set()
        producer._Controller__collision_compare_claim = None
        Controller._Controller__prepare_deferred_queue_retries(producer)
        assert pending_file_id in producer._Controller__deferred_queue_intents
        assert collector.wait_for_ingress(2.0)
        assert collector.flush_durable(2.0)
        after_written = collector.durable_snapshot()["written"]
        expected_records = after_written - baseline_written
        poll_counts = []
        actual_records = 0
        drain_deadline = time.monotonic() + 5.0
        while actual_records < expected_records and len(poll_counts) < 20 and \
                time.monotonic() < drain_deadline:
            count = reader.poll()
            poll_counts.append(count)
            actual_records += count
            if actual_records < expected_records and count == 0:
                time.sleep(0.01)
        assert actual_records == expected_records, (
            "durable reader drain incomplete: actual={} expected={} per_poll={}".format(
                actual_records, expected_records, poll_counts,
            )
        )
    finally:
        reader.close()
        collector.close(timeout=2.0)

    artifact_text = artifact.read_text(encoding="utf-8")
    assert secret not in artifact_text
    assert "/must/not/escape" not in artifact_text
    records = [item for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    projections = [item["capture"]["projection"] for item in records]
    readiness = [item["queue_readiness"] for item in projections if "queue_readiness" in item]
    intake = [item["queue_authority_handoff"] for item in projections if "queue_authority_handoff" in item]
    intake = [item for item in intake if item["event"] == "scan_intake"]
    assert [item["record_disposition"] for item in intake] == [
        "failed_recorded", "failed_ignored", "failed_ignored", "aggregate_incomplete",
        "unknown_recorded", "out_of_scope", "unknown_ignored", "pair_token_invalid", "token_recorded",
    ]
    assert intake[0]["pair_marker_state"] == "valid" and intake[0]["generation_relation"] == "newer"
    assert intake[0]["failed_root_is_selected"] is None
    assert intake[1]["generation_relation"] == "older"
    assert intake[1]["failed_root_is_selected"] is None
    assert intake[2]["run_match"] is False and intake[2]["generation_relation"] == "unknown"
    assert intake[3]["scan_final"] is False and intake[3]["generation_relation"] == "newer"
    assert intake[3]["result_failed"] is False and intake[3]["selected_recoverable"] is True
    assert intake[3]["selected_failed"] is None
    assert intake[3]["failed_root_is_selected"] is None
    assert intake[4]["selected_unknown"] is True and intake[4]["pair_marker_state"] == "absent"
    assert intake[4]["generation_relation"] == "newer"
    assert intake[5]["selected_unknown"] is True
    assert intake[6]["selected_unknown"] is True
    assert intake[4]["record_disposition"] == "unknown_recorded"
    assert intake[6]["record_disposition"] == "unknown_ignored"
    assert intake[7]["pair_marker_state"] == "invalid" and intake[7]["selected_completed"] is True
    assert intake[8]["pair_marker_state"] == "valid" and intake[8]["selected_completed"] is True
    assert intake[8]["generation_relation"] == "newer"
    mismatch_intake = next(item for item in intake if item["record_disposition"] == "failed_ignored"
                           and item["run_match"] is False)
    assert mismatch_intake["failed_root_is_selected"] is None
    assert observer._sanitize_capture_projection({"queue_authority_handoff": intake[0]})[
        "queue_authority_handoff"
    ] == intake[0]
    retained_intake = intake[7]
    assert observer._sanitize_capture_projection({"queue_authority_handoff": retained_intake})[
        "queue_authority_handoff"
    ] == retained_intake
    # The finalized spool merges critical and normal ingress lanes. Keep the
    # reader's returned order intact, while asserting that every production
    # boundary was retained without assuming producer call order.
    assert {item["event"] for item in readiness} >= {row[0] for row in readiness_rows}
    rescan_request = next(item for item in readiness if item["phase"] == "rescan_request")
    assert rescan_request["reason"] == "scoped_rescan_requested"
    scan_authority_decision = next(
        item for item in readiness
        if item["event"] == "queue_final_decision" and item.get("decision_boundary") == "scan_authority"
    )
    assert scan_authority_decision["local_reconciled"] is False
    assert scan_authority_decision["publication_id"] == 7
    scan_failure = next(item["scan_failure"] for item in readiness if "scan_failure" in item)
    assert scan_failure == {
        "side": "local", "kind": "failed", "scope": "targeted", "target_covered": True,
        "terminal": False, "recoverable": True, "result_generation": "1",
        "run_matches_baseline": True, "generation_vs_baseline": "newer",
        "failure_generation_source": "pair", "failure_generation": "1",
        "failure_generation_vs_baseline": "newer",
        "failure_ignored_reason": "unknown", "baseline_generation": None,
        "failure_site": "scan_had_errors", "failure_recoverable": True, "failure_errno": 13,
        "failure_exception": "permission_error",
        "failure_root_ref": opaque_trace_correlation("scan-root|" + failed_file_id),
        "failure_pair_ref": opaque_trace_correlation("scan-pair|" + pair_id),
        "failure_run_ref": trace_session_digest("private-local-session"),
    }
    ignored_failure = next(
        item["scan_failure"] for item in readiness
        if "scan_failure" in item and item["scan_failure"]["failure_generation"] == "11"
    )
    assert ignored_failure["kind"] == "unknown"
    assert ignored_failure["failure_generation_source"] == "pair"
    assert ignored_failure["failure_generation"] == "11"
    assert ignored_failure["failure_generation_vs_baseline"] == "older"
    assert ignored_failure["failure_ignored_reason"] == "older"
    assert ignored_failure["baseline_generation"] == "12"
    assert ignored_failure["result_generation"] == "13"
    assert ignored_failure["failure_run_ref"] == trace_session_digest("private-ignored-session")
    fresh_root_proof = next(item["fresh_root_proof"] for item in readiness if "fresh_root_proof" in item)
    assert fresh_root_proof["result"] == "rejected"
    assert fresh_root_proof["reason"] == "source_absent"
    assert fresh_root_proof["root_proof_gate"] == {
        "gate": "root_incomplete", "active_generation": "4", "outcome_generation": "4",
        "root_final_complete": True, "root_staging_complete": False,
        "confirmed_delete_generation": None, "source_root_present": False,
        "source_root_matches": False,
    }
    proof_retained_wait = next(
        item for item in readiness
        if item.get("phase") == "retained_intent_wait" and "fresh_root_proof" in item and
        item.get("deferred_intent", {}).get("remote_base") == "0"
    )
    assert proof_retained_wait["fresh_root_proof"]["result"] == "rejected"
    assert proof_retained_wait["fresh_root_proof"]["reason"] == "source_absent"
    ordinary_proof_retained_wait = next(
        item for item in readiness
        if item.get("phase") == "retained_intent_wait" and "fresh_root_proof" in item and
        item.get("deferred_intent", {}).get("phase") == "rescan" and
        item.get("deferred_intent", {}).get("remote_base") == "0"
    )
    assert ordinary_proof_retained_wait["fresh_root_proof"]["result"] == "rejected"
    assert ordinary_proof_retained_wait["fresh_root_proof"]["reason"] == "source_absent"
    proof_retained_projection = next(
        item for item in projections
        if item.get("queue_readiness", {}).get("phase") == "retained_intent_wait" and
        "fresh_root_proof" in item.get("queue_readiness", {}) and
        item["queue_readiness"].get("deferred_intent", {}).get("remote_base") == "0"
    )
    assert observer._sanitize_capture_projection(proof_retained_projection)["queue_readiness"][
        "fresh_root_proof"
    ] == proof_retained_wait["fresh_root_proof"]
    retained_wait = next(
        item["deferred_intent"] for item in readiness
        if "deferred_intent" in item and item["deferred_intent"].get("remote_base") == "4"
    )
    assert retained_wait["phase"] == "initial_rescan"
    assert retained_wait["reason"] == "local_ack_missing"
    assert retained_wait["decision"] == "waiting"
    assert retained_wait["local_base"] == "2"
    assert retained_wait["remote_base"] == "4"
    assert retained_wait["local_ack_state"] == "missing"
    assert retained_wait["callback_count"] == "1"
    assert retained_wait["claim_state"] == "global_absent"
    assert "private-pending" not in json.dumps(retained_wait)
    retained_projections = [
        item for item in projections
        if item.get("queue_readiness", {}).get("phase") == "retained_intent_wait" and
        item.get("queue_readiness", {}).get("deferred_intent", {}).get("remote_base") == "4"
    ]
    assert len(retained_projections) == 1
    retained_projection = retained_projections[0]
    assert observer._sanitize_capture_projection(retained_projection)["queue_readiness"]["deferred_intent"] == retained_wait
    persisted = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    finalized = [row for row in persisted if row.get("message") in
                 ({event for event, _ in readiness_rows} | {"queue_rescan_readiness", "queue_authority_handoff"})]
    assert len(finalized) >= len(readiness_rows) + len(intake)
    assert all("metadata" not in row and isinstance(row.get("details"), dict) for row in finalized)
    durable_intake = [row["details"] for row in persisted
                      if row.get("message") == "queue_authority_handoff" and
                      row.get("details", {}).get("event") == "scan_intake"]
    assert [row["record_disposition"] for row in durable_intake] == [
        "failed_recorded", "failed_ignored", "failed_ignored", "aggregate_incomplete",
        "unknown_recorded", "out_of_scope", "unknown_ignored", "pair_token_invalid", "token_recorded",
    ]
    assert durable_intake[0]["failed_root_is_selected"] is None
    assert "private-scan-pair" not in artifact_text
    assert "private-scan-root" not in artifact_text
    assert "private-local-session" not in artifact_text
    assert "private-ignored-pair" not in artifact_text
    assert "private-ignored-root" not in artifact_text
    assert "private-ignored-session" not in artifact_text
    assert "private-proof-session" not in artifact_text
    assert "private-proof-root" not in artifact_text
    assert "private-mismatch-pair" not in artifact_text
    assert "private-mismatch-root" not in artifact_text
    assert "private-mismatch-baseline" not in artifact_text
    assert "private-mismatch-result" not in artifact_text
    assert "private-mismatch-other" not in artifact_text
    assert "private-unknown-pair" not in artifact_text
    assert "private-unknown-root" not in artifact_text
    assert "private-unknown-session" not in artifact_text
    assert "private-invalid-pair" not in artifact_text
    assert "private-invalid-root" not in artifact_text
    assert "private-invalid-session" not in artifact_text
    assert "private-recorded-pair" not in artifact_text
    assert "private-recorded-root" not in artifact_text
    assert "private-recorded-session" not in artifact_text
    assert "private-incomplete-pair" not in artifact_text
    assert "private-incomplete-root" not in artifact_text
    assert "private-incomplete-session" not in artifact_text
    assert "private-rescan-root" not in artifact_text


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


@pytest.mark.skipif(BreadcrumbTraceCollector is None or _ModelFinalizationBreadcrumbSpan is None, reason="production finalization span is unavailable")
def test_actual_model_finalization_span_reaches_finalized_reader_and_json_sink(tmp_path: Path) -> None:
    spool = tmp_path / "model-finalization-spool"
    spool.mkdir()
    artifact = tmp_path / "model-finalization-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True,
        policy={"default": "off", "rules": {"queue.readiness": "info", "model.finalization": "info"}},
        durable_enabled=True,
        durable_path=str(spool),
        durable_enabled_getter=lambda: True,
    )
    controller = SimpleNamespace(
        _Controller__context=SimpleNamespace(breadcrumb_trace=collector),
        _Controller__lftp_status_poll_correlation="lftp-poll:0123456789abcdef",
    )

    def record_breadcrumb(**kwargs: object) -> None:
        collector.record(
            "controller", kwargs["message"], kwargs["details"],
            stage=kwargs["stage"], event_type=kwargs["event_type"],
            category=kwargs["category"], level=kwargs["level"],
            corr_id=kwargs["corr_id"], flow_id=kwargs.get("flow_id"),
            trace_scope=kwargs.get("trace_scope", "flow"),
        )

    controller._Controller__record_breadcrumb = record_breadcrumb
    reader = None
    try:
        assert collector.record(
            "controller", "baseline", {"schema": "queue_readiness.v1", "phase": "entry", "outcome": "accepted"},
            category="queue.readiness", event_type="callback", stage="queue_readiness", level="info",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader = _reader(spool, artifact)
        reader.start()
        entry_ns = 2**53 + 117
        terminal_ns = entry_ns + 8_000_000
        entry_cpu_ns = 2**53 + 31
        terminal_cpu_ns = entry_cpu_ns + 2_000_000
        span = _ModelFinalizationBreadcrumbSpan(
            controller, "model_lock_wait", "private-raw-correlation",
            defer_recording=True, eager_entry=True,
        )
        span.__enter__(boundary_ns=entry_ns, boundary_cpu_ns=entry_cpu_ns)
        assert collector.flush_durable(2.0)
        assert reader.poll() == 1
        span.__exit__(None, None, None, boundary_ns=terminal_ns, boundary_cpu_ns=terminal_cpu_ns)
        assert collector.flush_durable(2.0)
        assert reader.poll() == 0
        span.flush()
        assert collector.flush_durable(2.0)
        assert reader.poll() == 1
    finally:
        if reader is not None:
            reader.close()
        collector.close(timeout=2.0)

    durable_rows = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    finalized = [row for row in durable_rows if row.get("category") == "model.finalization"]
    assert [row["details"]["phase"] for row in finalized] == ["entry", "complete"]
    assert all(row["details"]["schema"] == "model_finalization.v2" for row in finalized)
    artifacts = [item for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    projections = [item["capture"]["projection"] for item in artifacts]
    spans = [item["model_finalization"] for item in projections]
    assert spans == [
        {
            "schema": "model_finalization.v2", "phase": "entry", "stage": "model_lock_wait",
            "outcome": "started", "wall_ms": 0, "thread_cpu_ms": 0,
            "lock_wait_ms": None, "lock_hold_ms": None, "boundary_monotonic_ns": str(entry_ns),
        },
        {
            "schema": "model_finalization.v2", "phase": "complete", "stage": "model_lock_wait",
            "outcome": "completed", "wall_ms": 8, "thread_cpu_ms": 2,
            "lock_wait_ms": 8, "lock_hold_ms": None, "boundary_monotonic_ns": str(terminal_ns),
        },
    ]
    assert projections[0]["corr_hash"] == projections[1]["corr_hash"]
    assert [item["capture"]["cursor_start"] for item in artifacts] == sorted(
        item["capture"]["cursor_start"] for item in artifacts
    )
    rendered = artifact.read_text(encoding="utf-8")
    assert "private-raw-correlation" not in rendered
    assert '"cpu_clock"' not in rendered
    assert str(entry_ns) in rendered and str(terminal_ns) in rendered

    malformed, irrelevant = capture._projection(json.loads(_row(
        category="model.finalization", stage="model_finalization", label="model_finalization_complete",
        details={
            "schema": "model_finalization.v2", "phase": "complete", "stage": "model_lock_wait",
            "outcome": "completed", "wall_ms": True, "thread_cpu_ms": 1.5,
            "lock_wait_ms": -1, "lock_hold_ms": 2**63, "boundary_monotonic_ns": None,
        },
    )))
    assert not irrelevant and malformed is not None
    assert malformed["model_finalization"] == {
        "schema": "model_finalization.v2", "phase": "complete", "stage": "model_lock_wait",
        "outcome": "completed", "wall_ms": None, "thread_cpu_ms": None,
        "lock_wait_ms": None, "lock_hold_ms": None, "boundary_monotonic_ns": None,
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


@pytest.mark.skipif(
    BreadcrumbTraceCollector is None or Controller is None or _record_lftp_status_breadcrumb is None
    or _record_lftp_status_membership_transition is None or LftpJobStatus is None,
    reason="production LFTP breadcrumb producer is unavailable",
)
def test_finalized_lftp_health_and_membership_reach_canonical_json_sink(tmp_path: Path) -> None:
    spool = tmp_path / "lftp-health-spool"
    spool.mkdir()
    artifact = tmp_path / "lftp-health-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True,
        policy={"default": "off", "rules": {
            "transfer.lftp.status": "warning", "transfer.lftp.membership": "info",
            "queue.lifecycle": "info",
        }},
        durable_enabled=True, durable_path=str(spool), durable_enabled_getter=lambda: True,
    )
    controller = Controller.__new__(Controller)
    controller._Controller__context = SimpleNamespace(breadcrumb_trace=collector)
    controller._Controller__model = SimpleNamespace(version=1)
    controller._Controller__lftp = SimpleNamespace(backend_name="lftp")
    controller._Controller__last_lftp_statuses = []
    controller._Controller__lftp_idle_status_authoritative = False
    controller._Controller__lftp_status_cache_expires_at = None
    controller._Controller__lftp_status_poll_retry_active = False
    controller._Controller__work_state_lock = threading.RLock()
    controller._Controller__pending_queue_dispatches = {}
    controller._Controller__progress_publication_epoch = 0
    try:
        assert collector.record(
            "controller", "baseline", {"schema": "baseline.v1"},
            category="transfer.lftp.status", level="warning",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader = _reader(spool, artifact)
        try:
            reader.start()
            _record_lftp_status_breadcrumb(
                controller, (), source="fresh_unhealthy", fresh=True, healthy=False,
                failure_reason="unhealthy_snapshot", poll_correlation="lftp-poll:0123456789abcdef",
            )
            status = LftpJobStatus(
                1, LftpJobStatus.Type.GET, LftpJobStatus.State.RUNNING,
                "private-status-label", "",
            )
            status.path_pair_id = "synthetic-pair"
            _record_lftp_status_membership_transition(
                controller, (status,), (), (), previous_malformed_ids=set(),
                current_malformed_ids=set(), source="fresh_unhealthy", fresh=True, healthy=False,
                poll_correlation="lftp-poll:0123456789abcdef",
            )
            for failure_reason in ("timeout", "eof", "terminal_backlog"):
                controller._Controller__lftp.last_status_poll_failure_reason = failure_reason
                _record_lftp_status_breadcrumb(
                    controller, (), source="error_empty", fresh=False, healthy=False,
                    poll_correlation="lftp-poll:0123456789abcdef",
                )
            assert collector.flush_durable(2.0)
            assert reader.poll() >= 5
        finally:
            reader.close()
        finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
        events = [row for row in finalized if row.get("message") in {
            "lftp_status_poll", "lftp_status_membership_transition",
        }]
        assert [row["message"] for row in events] == [
            "lftp_status_poll", "lftp_status_membership_transition",
            "lftp_status_poll", "lftp_status_poll", "lftp_status_poll",
        ]
        projections = [item["capture"]["projection"] for item in _artifacts(artifact) if item.get("capture", {}).get("state") == "record"]
        status_projections = [item["lftp_status_authority"] for item in projections if "lftp_status_authority" in item]
        membership_projection = next(item["lftp_status_membership"] for item in projections if "lftp_status_membership" in item)
        assert status_projections[0]["source"] == "fresh_unhealthy" and status_projections[0]["healthy"] is False
        assert status_projections[0]["idle_authoritative"] is None
        assert [item["failure_reason"] for item in status_projections] == [
            "unhealthy_snapshot", "timeout", "eof", "terminal_backlog",
        ]
        assert membership_projection["source"] == "fresh_unhealthy" and membership_projection["healthy"] is False
        assert membership_projection["transition_count"] == 1 and membership_projection["filtered_status_count"] == 0
        assert "transitions" not in membership_projection and "poll_correlation" not in membership_projection
        text = artifact.read_text(encoding="utf-8")
        assert "synthetic-status-id" not in text and "lftp-job:" not in text
    finally:
        collector.close(timeout=2.0)


@pytest.mark.skipif(BreadcrumbTraceCollector is None, reason="production breadcrumb collector is unavailable")
def test_finalized_completion_and_final_move_events_reach_json_sink_in_order(tmp_path: Path) -> None:
    spool = tmp_path / "completion-publication-spool"
    spool.mkdir()
    artifact = tmp_path / "completion-publication-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True,
        policy={"default": "off", "rules": {"queue.readiness": "info", "completion.gate": "info", "final_move.publication": "info"}},
        durable_enabled=True,
        durable_path=str(spool),
        durable_enabled_getter=lambda: True,
    )
    correlation = "completion:0123456789abcdef"
    flow = "lftp-poll:fedcba9876543210"
    events = (
        ("completion.gate", "completion_gate", "completion_gate_decision", {
            "completion_proved": True, "decision": "attempt_eligible", "reason": "complete_local_coverage",
            "physical_proof": "proven", "exact_final_file_proof": True,
            "pending_transition": "cleared", "terminal_outcome": "downloaded",
        }),
        ("completion.gate", "completion_gate", "pending_completion_move_decision", {
            "schema": "pending_completion_move_decision.v1", "stage": "authority", "decision": "skipped",
            "reason": "local_scope_unreconciled", "proof_source": "local_scan_scope", "move_result": "not_attempted",
        }),
        ("completion.gate", "completion_gate", "pending_completion_move_decision", {
            "schema": "pending_completion_move_decision.v1", "stage": "move", "decision": "result",
            "reason": "move_deferred", "proof_source": "move_result", "move_result": "deferred",
        }),
        ("final_move.publication", "final_move_publication", "final_move_publication", {
            "phase": "publish", "operation": "publish", "side": "destination", "errno_class": "eexist",
            "mutation_state": "uncertain", "source_exists": True, "destination_exists": True,
            "destination_parent_exists": True, "temporary_created": None, "residue_present": False,
            "source_path": "PRIVATE-SOURCE-PATH", "destination_path": "PRIVATE-DESTINATION-PATH",
        }),
        ("final_move.publication", "final_move_publication", "final_move_publication", {
            "phase": "verify", "operation": "verify", "side": "destination", "errno_class": "none",
            "mutation_state": "mutated", "source_exists": False, "destination_exists": True,
            "destination_parent_exists": True, "temporary_created": True, "residue_present": False,
            "copy_bytes_ns": 2, "temporary_fsync_ns": 3, "metadata_copy_ns": 4,
        }),
        ("final_move.publication", "final_move_publication", "final_move_publication", {
            "phase": "remove_source", "operation": "remove_source", "side": "source",
            "errno_class": "none", "mutation_state": "mutated", "source_exists": False,
            "outer_source_recheck_ns": 2**53 + 1, "source_identity_check_ns": 2,
            "claim_rename_ns": 3, "claim_parent_sync_ns": 4,
            "claimed_identity_tree_validation_ns": 5, "claim_delete_ns": 6,
            "claim_final_sync_ns": 7, "caller_parent_sync_ns": 8,
            "source_path": "/private/source",
        }),
        ("final_move.publication", "final_move_publication", "final_move_publication", {
            "phase": "PRIVATE-PHASE", "operation": "PRIVATE-OPERATION", "side": "PRIVATE-SIDE",
            "errno_class": "PRIVATE-ERRNO", "mutation_state": "PRIVATE-STATE",
            "destination_exists": "PRIVATE-BOOL", "destination_path": "PRIVATE-PATH",
        }),
        ("completion.gate", "completion_gate", "pending_completion_move_decision", {
            "schema": "pending_completion_move_decision.v1", "stage": "candidate", "decision": "cancelled",
            "reason": "explicit_stop", "proof_source": "explicit_stop", "move_result": "not_attempted",
        }),
        ("completion.gate", "completion_gate", "pending_completion_move_decision", {
            "schema": "PRIVATE-SCHEMA", "stage": "PRIVATE-PHASE", "decision": "PRIVATE-DECISION",
            "reason": "PRIVATE-REASON", "proof_source": "PRIVATE-PATH", "move_result": {"unsafe": True},
        }),
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
            for category, stage, message, details in events:
                assert collector.record(
                    "model_updater" if category == "completion.gate" else "controller",
                    message, details, category=category, event_type="diagnostic", stage=stage,
                    level="info", corr_id=correlation, flow_id=flow, trace_scope="flow",
                    file_id="private-file-identity", path_pair_id="private-pair-identity",
                ) in {"retained", "enqueued"}
            assert collector.flush_durable(2.0)
            assert reader.poll() == len(events)
        finally:
            reader.close()
        finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
        selected = [row for row in finalized if row.get("message") in {
            "completion_gate_decision", "pending_completion_move_decision", "final_move_publication",
        }]
        assert len(selected) == len(events)
        assert all(row.get("trace_scope") == "flow" and row.get("corr_id") == correlation for row in selected)
    finally:
        collector.close(timeout=2.0)


    saved = [item for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
    projections = [item["capture"]["projection"] for item in saved]
    projected_messages = [
        (item.get("completion_gate") or item.get("final_move_publication") or {}).get("message")
        for item in projections
    ]
    assert projected_messages == [row["message"] for row in selected]
    assert len({item["corr_hash"] for item in projections}) == 1
    remove_projection = next(item["final_move_publication"] for item in projections
                             if item.get("final_move_publication", {}).get("phase") == "remove_source")
    assert remove_projection["outer_source_recheck_ns"] == 2**53 + 1
    assert remove_projection["claim_final_sync_ns"] == 7
    assert "source_path" not in json.dumps(remove_projection)
    expected_numbers = [capture._numeric_values(row, row.get("metadata", {}), row.get("details", {}))
                        for row in selected]
    assert [item["numbers"] for item in projections] == expected_numbers
    assert [item["capture"]["cursor_start"] for item in saved] == sorted(item["capture"]["cursor_start"] for item in saved)
    gate_decision = next(item["completion_gate"] for item in projections
                         if item.get("completion_gate", {}).get("message") == "completion_gate_decision")
    assert gate_decision["completion_proved"] is True
    assert gate_decision["physical_proof"] == "proven"
    assert gate_decision["exact_final_file_proof"] is True
    gate_skipped = next(item["completion_gate"] for item in projections
                        if item.get("completion_gate", {}).get("phase") == "authority")
    assert gate_skipped["schema"] == "pending_completion_move_decision.v1"
    assert (gate_skipped["phase"], gate_skipped["decision"], gate_skipped["reason"],
            gate_skipped["proof_source"], gate_skipped["move_result"]) == (
        "authority", "skipped", "local_scope_unreconciled", "local_scan_scope", "not_attempted",
    )
    gate_deferred = next(item["completion_gate"] for item in projections
                         if item.get("completion_gate", {}).get("phase") == "move")
    assert (gate_deferred["phase"], gate_deferred["decision"], gate_deferred["move_result"]) == (
        "move", "result", "deferred",
    )
    failed_move = next(item["final_move_publication"] for item in projections
                       if item.get("final_move_publication", {}).get("phase") == "publish")
    assert failed_move["errno_class"] == "eexist" and failed_move["mutation_state"] == "uncertain"
    assert failed_move["temporary_created"] is None and failed_move["destination_exists"] is True
    assert any(item.get("final_move_publication", {}).get("phase") == "verify" for item in projections)
    candidate_cancel = next(item["completion_gate"] for item in projections
                            if item.get("completion_gate", {}).get("phase") == "candidate")
    assert (candidate_cancel["phase"], candidate_cancel["decision"], candidate_cancel["reason"],
            candidate_cancel["proof_source"]) == ("candidate", "cancelled", "explicit_stop", "explicit_stop")
    malformed_saved = next(item["completion_gate"] for item in projections
                           if item.get("completion_gate", {}).get("message") == "pending_completion_move_decision"
                           and item.get("completion_gate", {}).get("schema") == "unknown")
    assert all(malformed_saved[key] == "unknown" for key in ("phase", "decision", "reason", "proof_source", "move_result"))
    malformed_move = next(item["final_move_publication"] for item in projections
                          if item.get("final_move_publication", {}).get("phase") == "unknown")
    assert all(malformed_move[key] == "unknown" for key in (
        "operation", "side", "errno_class", "mutation_state",
    ))
    assert malformed_move["destination_exists"] is None
    assert "PRIVATE-" not in artifact.read_text(encoding="utf-8")

    sanitized = [observer._sanitize_capture_projection(item) for item in projections]
    assert next(item["completion_gate"] for item in sanitized
                if item.get("completion_gate", {}).get("phase") == "authority")["proof_source"] == "local_scan_scope"
    assert next(item["final_move_publication"] for item in sanitized
                if item.get("final_move_publication", {}).get("phase") == "publish")["errno_class"] == "eexist"

    malformed = capture._projection({
        "category": "completion.gate", "source": "model_updater", "event_type": "diagnostic",
        "stage": "completion_gate", "message": "pending_completion_move_decision",
        "corr_id": correlation,
        "details": {"schema": "PRIVATE-SCHEMA", "stage": "PRIVATE-PHASE", "decision": "PRIVATE-DECISION",
                    "reason": "PRIVATE-REASON", "proof_source": "PRIVATE-PATH", "move_result": {"unsafe": True}},
    })[0]
    assert malformed is not None
    assert malformed["completion_gate"]["schema"] == "unknown"
    assert all(malformed["completion_gate"][key] == "unknown" for key in ("phase", "decision", "reason", "proof_source", "move_result"))
    assert "PRIVATE-" not in json.dumps(malformed)

    bad_final_move = capture._projection({
        "category": "final_move.publication", "source": "controller", "event_type": "diagnostic",
        "stage": "final_move_publication", "message": "final_move_publication", "corr_id": correlation,
        "details": {"phase": "PRIVATE-PHASE", "operation": "PRIVATE-OP", "side": "PRIVATE-SIDE",
                    "errno_class": "PRIVATE-ERROR", "mutation_state": "PRIVATE-STATE",
                    "destination_path": "PRIVATE-PATH", "destination_exists": "PRIVATE-BOOL"},
    })[0]
    assert bad_final_move is not None
    facts = bad_final_move["final_move_publication"]
    assert all(facts[key] == "unknown" for key in ("phase", "operation", "side", "errno_class", "mutation_state"))
    assert facts["destination_exists"] is None
    assert "PRIVATE-" not in json.dumps(bad_final_move)


@pytest.mark.skipif(BreadcrumbTraceCollector is None or ModelUpdater is None, reason="production completion emitter is unavailable")
def test_confirmed_delete_fence_facts_reach_json_sink_from_finalized_producer(tmp_path: Path) -> None:
    spool = tmp_path / "delete-fence-spool"
    spool.mkdir()
    artifact = tmp_path / "delete-fence-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True,
        policy={"default": "off", "rules": {"completion.gate": "info", "queue.readiness": "info"}},
        durable_enabled=True,
        durable_path=str(spool),
        durable_enabled_getter=lambda: True,
    )
    session = "private-delete-session"
    pair_id = "private-delete-pair"
    root_name = "private-delete-root"
    generation = 2**53 + 17
    process = SimpleNamespace(session_token=session, generation=generation)
    controller = SimpleNamespace(
        _Controller__context=SimpleNamespace(breadcrumb_trace=collector),
        _Controller__local_scan_process=process,
        logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
    )
    updater = ModelUpdater(controller)
    controller._Controller__updater = updater
    accumulator = _ensure_progressive_scan_state(
        controller, "local", eager=True, bind_current_session=False,
    )

    root = SystemFile(root_name, 2**53 + 101, True)
    root.path_pair_id = pair_id
    root.add_child(SystemFile("private-payload.bin", 64))
    first_scan = ScannerResult(
        datetime.now(), [root], scanned_path_pair_ids={pair_id}, generation=generation,
        is_progress=True, root_names={root_name}, completed_path_pair_ids={pair_id},
        is_full_snapshot=True, full_snapshot_path_pair_ids={pair_id}, session_token=session,
        root_scan_outcomes_by_pair={pair_id: {root_name: (True, True)}}, scan_started_at=datetime.now(),
    )
    first_scan.files[0].path_pair_id = pair_id
    accumulator.apply([first_scan])

    try:
        assert collector.record(
            "controller", "baseline", {"schema": "queue_readiness.v1", "phase": "entry", "outcome": "accepted"},
            category="queue.readiness", event_type="callback", stage="queue_readiness", level="info",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader = _reader(spool, artifact)
        try:
            reader.start()
            file_id = ModelFile.build_file_id(f"{root_name}/private-payload.bin", pair_id)
            assert updater.confirm_local_delete(file_id, pair_id) is True

            process.generation = None
            assert updater.confirm_local_delete(file_id, pair_id) is False

            fresh_root = SystemFile(root_name, 2**53 + 103, True)
            fresh_root.path_pair_id = pair_id
            fresh_scan = ScannerResult(
                datetime.now(), [fresh_root], scanned_path_pair_ids={pair_id}, generation=generation + 1,
                is_progress=True, root_names={root_name}, completed_path_pair_ids={pair_id},
                is_full_snapshot=True, full_snapshot_path_pair_ids={pair_id}, session_token=session,
                root_scan_outcomes_by_pair={pair_id: {root_name: (True, True)}},
                scan_started_at=datetime.now(),
            )
            accumulator.apply([fresh_scan])
            assert collector.flush_durable(2.0)
            assert reader.poll() == 3
        finally:
            reader.close()
    finally:
        collector.close(timeout=2.0)

    rows = _artifacts(artifact)
    selected = [row for row in rows if row.get("capture", {}).get("state") == "record"
                and row["capture"]["projection"].get("completion_gate", {}).get("message") in {
                    "confirmed_local_delete_source", "confirmed_delete_fence_disposition",
                }]
    finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    emitted = [row for row in finalized if row.get("message") in {
        "confirmed_local_delete_source", "confirmed_delete_fence_disposition",
    }]
    assert [row["capture"]["projection"]["completion_gate"]["message"] for row in selected] == [
        "confirmed_local_delete_source", "confirmed_local_delete_source", "confirmed_delete_fence_disposition",
    ]
    assert [row["message"] for row in emitted] == [row["capture"]["projection"]["completion_gate"]["message"] for row in selected]
    assert len(emitted) == 3
    for source, sink in zip(emitted, selected):
        projection = sink["capture"]["projection"]
        assert projection["corr_hash"] == capture._hash(source["corr_id"])
        assert projection["file_hash"] == capture._hash(source["file_id"])
        assert projection["pair_hash"] == capture._hash(source["path_pair_id"])
    projections = [row["capture"]["projection"] for row in selected]
    success, refusal, disposition = [row["completion_gate"] for row in projections]
    assert success["schema"] == "confirmed_local_delete_source.v1"
    assert success["delete_confirmed"] is True and success["fence_result"] is True
    assert success["target_scope"] == "nested_path"
    assert success["local_scan_binding_known"] is True and success["local_generation_known"] is True
    assert success["local_generation"] == str(generation)
    assert refusal["fence_result"] is False and refusal["local_generation_known"] is False
    assert refusal["local_generation"] is None
    assert disposition["schema"] == "confirmed_delete_fence_disposition.v1"
    assert disposition["decision"] == "released" and disposition["release_basis"] == "current_root"
    assert disposition["event_generation"] == str(generation + 1)
    assert disposition["event_generation_known"] is True and disposition["local_scan_binding_known"] is True
    assert disposition["scan_started_at_known"] is True and disposition["scan_started_at_ms"] is not None
    assert disposition["root_present"] is True and disposition["root_final_complete"] is True
    assert disposition["root_staging_complete"] is True and disposition["root_is_dir"] is True
    assert disposition["root_size"] == str(2**53 + 103)
    assert len({projection["corr_hash"] for projection in projections}) == 1
    sanitized = [observer._sanitize_capture_projection(projection)["completion_gate"] for projection in projections]
    assert [item["fence_result"] for item in sanitized[:2]] == [True, False]
    assert sanitized[2]["event_generation"] == str(generation + 1)
    assert sanitized[2]["root_size"] == str(2**53 + 103)
    artifact_text = artifact.read_text(encoding="utf-8")
    for private in (session, pair_id, root_name, "private-payload.bin"):
        assert private not in artifact_text

    malformed, rejected = capture._projection({
        "category": "completion.gate", "source": "model_updater", "event_type": "diagnostic",
        "stage": "completion_gate", "level": "info", "message": "confirmed_local_delete_source",
        "details": {
            "schema": "confirmed_local_delete_source.v1", "delete_confirmed": "true",
            "fence_result": 1, "target_scope": "private/path", "local_scan_binding_known": False,
            "local_generation_known": True, "local_generation": True, "raw_path": "/private/root",
        },
    })
    assert malformed is not None and rejected is False
    malformed_gate = malformed["completion_gate"]
    assert malformed_gate["delete_confirmed"] is None and malformed_gate["fence_result"] is None
    assert malformed_gate["target_scope"] == "unknown" and malformed_gate["local_scan_binding_known"] is False
    assert malformed_gate["local_generation_known"] is True and malformed_gate["local_generation"] == "unknown"
    assert "raw_path" not in json.dumps(malformed)


@pytest.mark.skipif(BreadcrumbTraceCollector is None or ModelUpdater is None, reason="production completion emitter is unavailable")
def test_move_root_lifecycle_facts_reach_json_sink_from_finalized_producer(tmp_path: Path) -> None:
    spool = tmp_path / "move-lifecycle-spool"
    spool.mkdir()
    artifact = tmp_path / "move-lifecycle-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True,
        policy={"default": "off", "rules": {"completion.gate": "info"}},
        durable_enabled=True,
        durable_path=str(spool),
        durable_enabled_getter=lambda: True,
    )
    controller = SimpleNamespace(
        _Controller__context=SimpleNamespace(breadcrumb_trace=collector),
        logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
    )
    updater = ModelUpdater(controller)
    controller._Controller__updater = updater
    accumulator = _ensure_progressive_scan_state(
        controller, "local", eager=True, bind_current_session=False,
    )
    try:
        assert collector.record(
            "model_updater", "move_root_lifecycle_test_baseline",
            {"schema": "move_root_lifecycle_test_baseline.v1"},
            category="completion.gate", event_type="diagnostic", stage="completion_gate", level="info",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader = _reader(spool, artifact)
        try:
            reader.start()
            move_completed_at = datetime.now()
            token = updater.begin_final_move_local_root_invalidation(
                "private-root", "private-pair", 2**53 + 17,
            )
            updater.finish_final_move_local_root_invalidation(
                "private-root", "private-pair", token, True, move_completed_at,
            )
            root = SystemFile("private-root", 2**53 + 31, True)
            root.path_pair_id = "private-pair"
            scan = ScannerResult(
                move_completed_at + timedelta(seconds=2), [root], scanned_path_pair_ids={"private-pair"},
                generation=2**53 + 18, is_progress=True, root_names={"private-root"},
                root_scan_outcomes_by_pair={"private-pair": {"private-root": (True, True)}},
                scan_started_at=move_completed_at + timedelta(seconds=1),
            )
            accumulator.apply([scan])
            absent_move_completed_at = datetime.now()
            absent_token = updater.begin_final_move_local_root_invalidation(
                "private-absent-root", "private-pair", 2**53 + 18,
            )
            updater.finish_final_move_local_root_invalidation(
                "private-absent-root", "private-pair", absent_token, True, absent_move_completed_at,
            )
            absent_scan = ScannerResult(
                absent_move_completed_at + timedelta(seconds=2), [],
                scanned_path_pair_ids={"private-pair"}, completed_path_pair_ids={"private-pair"},
                is_scan_final=True, is_full_snapshot=True, full_snapshot_path_pair_ids={"private-pair"},
                generation=2**53 + 19,
                root_scan_outcomes_by_pair={"private-pair": {"private-absent-root": (False, False)}},
                scan_started_at=absent_move_completed_at - timedelta(seconds=1),
            )
            accumulator.apply([absent_scan])
            assert collector.flush_durable(2.0)
            assert reader.poll() >= 7
        finally:
            reader.close()
    finally:
        collector.close(timeout=2.0)

    finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    emitted = [row for row in finalized if row.get("message") == "move_root_invalidation_lifecycle"]
    assert [row["details"]["phase"] for row in emitted] == [
        "created", "committed", "released", "created", "committed", "evaluated", "released",
    ]
    selected = [row for row in _artifacts(artifact) if row.get("capture", {}).get("state") == "record"
                and row.get("capture", {}).get("projection", {}).get("completion_gate", {}).get("message") ==
                "move_root_invalidation_lifecycle"]
    projected = [row["capture"]["projection"] for row in selected]
    assert [row["completion_gate"]["phase"] for row in projected] == [
        "created", "committed", "released", "created", "committed", "evaluated", "released",
    ]
    assert len(emitted) == len(selected) == 7
    for source, sink in zip(emitted, selected):
        capture_row = sink["capture"]
        projection = capture_row["projection"]
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        assert capture_row.get("created_ns") == source.get("created_ns")
        assert capture_row.get("created_ms") == source.get("created_ms")
        assert projection["category"] == source.get("category", metadata.get("category"))
        assert projection["stage"] == source.get("stage", metadata.get("stage"))
        assert projection["event_type"] == source.get("event_type", metadata.get("event_type"))
        corr_id = source.get("corr_id", metadata.get("corr_id"))
        flow_id = source.get("flow_id", metadata.get("flow_id"))
        file_id = source.get("file_id", metadata.get("file_id"))
        pair_id = source.get("path_pair_id", metadata.get("path_pair_id"))
        assert projection["corr_hash"] == capture._hash(corr_id)
        assert projection["flow_hash"] == capture._hash(flow_id)
        assert projection["file_hash"] == capture._hash(file_id)
        assert projection["pair_hash"] == capture._hash(pair_id)
    released = projected[2]["completion_gate"]
    assert released["schema"] == "move_root_invalidation_lifecycle.v1"
    assert released["captured_generation"] == str(2**53 + 17)
    assert released["event_generation"] == str(2**53 + 18)
    assert released["completion_known"] is True and released["scan_started_after_completion"] is True
    assert released["root_present"] is True and released["root_final_complete"] is True
    assert released["root_staging_complete"] is True
    absent = projected[-2]["completion_gate"]
    assert absent["captured_generation"] == str(2**53 + 18)
    assert absent["event_generation"] == str(2**53 + 19)
    assert absent["phase"] == "evaluated" and absent["root_protected"] is False
    assert absent["protection_reason"] == "healthy_full_snapshot"
    assert absent["root_present"] is False and absent["scan_started_after_completion"] is False
    assert projected[-1]["completion_gate"]["phase"] == "released"
    assert projected[-1]["completion_gate"]["root_present"] is False
    safe = observer._sanitize_capture_projection(projected[-2])["completion_gate"]
    assert safe == absent
    artifact_text = artifact.read_text(encoding="utf-8")
    assert all(label not in artifact_text for label in ("private-root", "private-absent-root", "private-pair"))



@pytest.mark.skipif(BreadcrumbTraceCollector is None or Controller is None, reason="production controller is unavailable")
def test_controller_admission_timing_reaches_finalized_reader_sink(tmp_path: Path) -> None:
    spool = tmp_path / "command-admission-spool"
    spool.mkdir()
    artifact = tmp_path / "command-admission-capture.jsonl"
    trace = BreadcrumbTraceCollector(
        lambda: True, max_entries=32, policy={"default": "info"},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact, max_records=16, max_output_bytes=8192)
    controller = Controller.__new__(Controller)
    controller._Controller__context = SimpleNamespace(breadcrumb_trace=trace)
    command = Controller.Command(
        Controller.Command.Action.QUEUE, "private/path-sentinel.bin", flow_id="private-flow-sentinel",
    )
    timestamp_fields = (
        "handler_entry_ns", "handler_resolved_ns", "admission_attempt_ns",
        "flow_id_lock_attempt_ns", "flow_id_lock_acquired_ns",
        "flow_lock_attempt_ns", "flow_lock_acquired_ns", "queue_put_start_ns",
        "queue_put_ns", "admission_complete_ns", "work_lock_attempt_ns",
        "work_lock_acquired_ns", "queue_get_ns", "model_resolved_ns",
    )
    expected_base = 2**53 + 117
    messages = ("command_queued", "command_dequeued", "command_dispatched", "command_finished")
    missing_timing_command = Controller.Command(
        Controller.Command.Action.QUEUE, "private/without-timing.bin", flow_id="private-flow-without-timing",
    )
    try:
        reader.start()
        for index, message in enumerate(messages):
            timing = {key: None for key in timestamp_fields}
            timing.update({
                "handler_entry_ns": expected_base + index,
                "handler_resolved_ns": None,
                "admission_attempt_ns": 2**63 - 1,
                "flow_id_lock_attempt_ns": True,
                "queue_put_ns": "private-malformed-timestamp",
                "model_resolved_ns": 2**63,
                "previous_record_timing": ("private-message-sentinel", 1, 2, 1),
            })
            command.diagnostic_timing = timing
            controller._Controller__record_command_breadcrumb(  # exercise the existing producer
                command, message, {"private-detail-sentinel": "must-not-project"},
            )
        controller._Controller__record_command_breadcrumb(
            missing_timing_command, "command_finished", {},
        )
        assert trace.flush_durable(2.0)
        assert reader.poll() == len(messages) + 1
    finally:
        reader.close()
        trace.close(timeout=2.0)

    rows = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    record_messages = (*messages, "command_finished")
    emitted = [row for row in rows if row.get("message") in record_messages]
    assert [row["message"] for row in emitted] == list(record_messages)
    assert all(row["source"] == "controller" and row["category"] == "controller" and row["stage"] == "command" for row in emitted)
    timed_emitted = emitted[:len(messages)]
    assert all(row["details"]["admission_timing"]["schema"] == "command_admission_timing.v1" for row in timed_emitted)
    assert all(row["details"]["admission_timing"]["correlation"] == opaque_trace_correlation(command.flow_id) for row in timed_emitted)
    assert "admission_timing" not in emitted[-1]["details"]

    saved = [item for item in _artifacts(artifact) if item.get("capture", {}).get("state") == "record"]
    projections = [item["capture"]["projection"] for item in saved]
    facts = [projection["command_admission_timing"] for projection in projections if "command_admission_timing" in projection]
    assert len(facts) == len(messages)
    assert [item["handler_entry_ns"] for item in facts] == [str(expected_base + index) for index in range(len(messages))]
    assert len({item["correlation"] for item in facts}) == 1
    assert all(item["schema"] == "command_admission_timing.v1" for item in facts)
    assert all(item["admission_attempt_ns"] == str(2**63 - 1) for item in facts)
    assert all(item["handler_resolved_ns"] is None for item in facts)
    assert all(item["flow_id_lock_attempt_ns"] is None and item["queue_put_ns"] is None for item in facts)
    assert all(item["model_resolved_ns"] is None for item in facts)
    assert all(set(item) == {"schema", "correlation", *timestamp_fields} for item in facts)
    assert all("previous_record_timing" not in item for item in facts)
    assert [item["capture"]["projection"]["source"] for item in saved] == ["controller"] * (len(messages) + 1)
    assert [item["capture"]["projection"]["category"] for item in saved] == ["controller"] * (len(messages) + 1)
    assert [item["capture"]["projection"]["stage"] for item in saved] == ["command"] * (len(messages) + 1)
    assert "command_admission_timing" not in projections[-1]
    assert artifact.stat().st_size <= 8192
    assert all("private-" not in json.dumps(item) for item in saved)

    omitted, irrelevant = capture._projection({
        "source": "controller", "category": "controller", "stage": "command",
        "details": {"admission_timing": {
            "schema": "command_admission_timing.v1", "correlation": "0123456789abcdef",
            "handler_entry_ns": 2**53 + 117,
        }},
    })
    assert not irrelevant and omitted is not None
    omitted_facts = omitted["command_admission_timing"]
    assert omitted_facts["handler_entry_ns"] == str(expected_base)
    assert omitted_facts["handler_resolved_ns"] is None
    malformed, irrelevant = capture._projection({
        "source": "controller", "category": "controller", "stage": "command",
        "details": {"admission_timing": {
            "schema": "PRIVATE-SCHEMA", "correlation": "private-correlation",
            "handler_entry_ns": 123,
        }},
    })
    assert not irrelevant and malformed is not None
    malformed_facts = malformed["command_admission_timing"]
    assert malformed_facts["schema"] == "unknown" and malformed_facts["correlation"] == "unknown"
    assert all(malformed_facts[key] is None for key in timestamp_fields)
    assert "PRIVATE-" not in json.dumps(malformed)
    sanitized = observer._sanitize_capture_projection({
        "command_admission_timing": {
            "schema": "command_admission_timing.v1", "correlation": "0123456789abcdef",
            "handler_entry_ns": str(2**53 + 117), "handler_resolved_ns": "01",
            "admission_attempt_ns": str(2**63), "previous_record_timing": ["private"],
        },
    })["command_admission_timing"]
    assert sanitized["handler_entry_ns"] == str(2**53 + 117)
    assert sanitized["handler_resolved_ns"] is None and sanitized["admission_attempt_ns"] is None
    assert all(sanitized[key] is None for key in timestamp_fields if key not in {
        "handler_entry_ns", "handler_resolved_ns", "admission_attempt_ns",
    })
    assert set(sanitized) == {"schema", "correlation", *timestamp_fields}


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
        assert reader.poll() == 8
    finally:
        reader.close()
        collector.close(timeout=2.0)

    durable = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    finalized = [row for row in durable if row.get("message") == "input_arbitration_witness"]
    assert len(finalized) == 8
    assert all("metadata" not in row and row.get("trace_scope") == "flow" for row in finalized)
    assert all(type(row.get("version")) is int for row in finalized)
    assert len({row["version"] for row in finalized}) == 8
    assert "private-root-label" not in json.dumps(finalized)
    assert "private-leaf-label.bin" not in json.dumps(finalized)

    projected = [
        item["capture"]["projection"] for item in _artifacts(artifact)
        if item["capture"].get("state") == "record"
        and item["capture"].get("projection", {}).get("category") == "scan.authority"
    ]
    assert len(projected) == 8
    roots = [item for item in projected if item["input_arbitration"]["phase"] == "root_fill"]
    assert len(roots) == 2
    root_token = roots[0]["input_arbitration"]["root_id"]
    assert root_token != "unknown" and roots[1]["input_arbitration"]["root_id"] == root_token
    selected = capture.select_input_arbitration_rows(projected, root_token)
    assert selected["state"] == "selected"
    assert len(selected["build_tokens"]) == 2
    assert all(len(token) == 16 for token in selected["build_tokens"])
    assert len(selected["records"]) == 8
    for token in selected["build_tokens"]:
        build_rows = [row for row in selected["records"] if row["input_arbitration"]["build_token"] == token]
        assert {row["input_arbitration"]["phase"] for row in build_rows} == {
            "root_source", "root_fill", "remote_leaf_fill", "summary",
        }
        assert len({row["numbers"]["version"] for row in build_rows}) == 4
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
    assert facts["phase"] == "root_fill"
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


@pytest.mark.skipif(BreadcrumbTraceCollector is None or Controller is None, reason="production breadcrumb producer is unavailable")
def test_finalized_scan_adoption_and_root_source_reach_json_sink_with_unknowns(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=32,
        policy={"default": "off", "rules": {"scan.authority": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact)
    producer = Controller.__new__(Controller)
    producer._Controller__context = SimpleNamespace(breadcrumb_trace=collector)
    scan_details = {"p": {
        "m": "progressive", "so": "0123456789abcdef", "ao": "fedcba9876543210",
        "sp": True, "cp": False, "up": None, "tp": True, "tc": None,
        "ro": "incomplete", "rc": "complete", "rf": False, "rs": True,
        "private_path": "/private/drop",
    }}
    try:
        # Require a finalized production record before arming, then capture the
        # same production route's later root-adoption record.
        Controller._record_completion_scan_join_breadcrumb(
            producer, "0123456789abcdef", "active_scan_root_adopted", scan_details,
        )
        assert collector.flush_durable(2.0)
        reader.start()
        Controller._record_completion_scan_join_breadcrumb(
            producer, "0123456789abcdef", "scan_root_adopted", scan_details,
        )
        root_details = {
            "schema": "model_input_arbitration.v1", "phase": "root_source", "subject_kind": "root",
            "root_id": "0123456789abcdef", "subject_id": "fedcba9876543210", "input_scope": "root",
            "selection": "local", "p": {
                "lo": "0011223344556677", "ps": "8899aabbccddeeff", "pb": False,
                "private_path": "/private/drop",
            },
        }
        assert collector.record(
            "model_updater", "input_arbitration_witness", root_details,
            category="scan.authority", stage="model_builder_input_arbitration", event_type="diagnostic",
            level="info", corr_id="input-arbitration:0123456789abcdef",
            flow_id="input-arbitration:0123456789abcdef", trace_scope="flow",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        assert reader.poll() == 2
    finally:
        reader.close()
        collector.close(timeout=2.0)

    rows = _artifacts(artifact)
    projected = [row["capture"]["projection"] for row in rows if row["capture"].get("state") == "record"]
    assert len(projected) == 2
    finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    assert any(row.get("category") == "scan.authority" and row.get("stage") == "scan_authority"
               and row.get("event_type") == "state_transition" and row.get("message") == "scan_root_adopted"
               and row.get("details", {}).get("schema") == "completion_scan_join.v1" for row in finalized)
    adoption = next(item for item in projected if item["category"] == "scan.authority"
                    and item["stage"] == "scan_authority"
                    and item.get("scan_join", {}).get("schema") == "completion_scan_join.v1")
    expected = {
        "schema": "completion_scan_join.v1", "local_scan_mode": "progressive",
        "scan_source_root_object_digest": "0123456789abcdef",
        "accepted_root_object_digest": "fedcba9876543210", "source_pair_scanned": True,
        "source_pair_completed": False, "source_pair_unknown": None, "source_pair_targeted": True,
        "target_pair_covered": None, "root_scan_outcome": "incomplete",
        "root_outcome_coverage": "complete", "root_final_complete": False,
        "root_staging_complete": True,
    }
    expected.update({key: None for key in (
        "selected_root_ref", "root_in_scan_result", "root_in_setter_input", "root_is_staging",
        "handoff_suppressed", "current_process_final_publication", "scan_final", "scan_failed",
        "scanned_pair", "completed_pair", "unknown_pair", "scan_started_at_known",
        "selected_root_omissions", "active_scan_digest", "scan_generation", "scan_started_at_ms",
    )})
    assert adoption["scan_join"] == expected
    root = next(item for item in projected if item["category"] == "scan.authority"
                and item.get("input_arbitration", {}).get("phase") == "root_source")
    assert root["input_arbitration"]["phase"] == "root_source"
    assert root["input_arbitration"]["root_id"] == "0123456789abcdef"
    assert root["input_arbitration"]["local_root_object_digest"] == "0011223344556677"
    assert root["input_arbitration"]["local_proof_session_digest"] == "8899aabbccddeeff"
    assert root["input_arbitration"]["local_proof_object_bound"] is False
    assert "private_path" not in json.dumps(projected)


@pytest.mark.skipif(BreadcrumbTraceCollector is None or _model_updater_fixture_module is None, reason="production updater fixture is unavailable")
def test_postmove_reconcile_flat_roots_from_updater_reach_finalized_sink(tmp_path: Path) -> None:
    fixture = _model_updater_fixture_module.TestModelUpdater()
    names = ["sample-{}.bin".format(index) for index in range(8)]
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=128,
        policy={"default": "off", "rules": {"scan.root_shape": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )

    def exercise(trace):
        local_roots = [SystemFile(name, 10, False) for name in names]
        remote_roots = [SystemFile(name, 10, False) for name in names]
        def result(files, instance, generation):
            return ScannerResult(
                datetime.now(), files, generation=generation, session_token=instance,
                is_progress=True, is_scan_final=True, is_full_snapshot=True,
                scanned_path_pair_ids={None}, completed_path_pair_ids={None},
                full_snapshot_path_pair_ids={None},
            )
        builder = ModelBuilder()
        controller, _ = fixture._make_progressive_update_controller(
            None, None, authoritative=False, model_builder=builder, model=Model(),
        )
        controller._Controller__context.breadcrumb_trace = trace
        controller._Controller__current_process_final_publication_file_ids = set(names)
        controller._Controller__remote_scan_process = fixture._progressive_process(
            "remote-instance", [[result(remote_roots, "remote-instance", 1)], []],
        )
        controller._Controller__local_scan_process = fixture._progressive_process(
            "local-instance", [[result(local_roots, "local-instance", 1)],
                               [result(local_roots[:4], "local-instance", 2)]],
        )
        updater = ModelUpdater(controller)
        updater.update()
        if trace is collector:
            assert collector.flush_durable(2.0)
            reader.start()
        updater.update()
        return tuple((name, controller._Controller__model.get_file(name).local_size,
                      controller._Controller__model.get_file(name).state.name)
                     for name in names)

    reader = _reader(spool, artifact)
    try:
        observed = exercise(collector)
        assert collector.flush_durable(2.0)
        assert reader.poll() > 0
        off = SimpleNamespace(is_effectively_enabled=lambda *args, **kwargs: False)
        fault = SimpleNamespace(is_effectively_enabled=lambda *args, **kwargs: True,
                                record=MagicMock(side_effect=RuntimeError("trace failure")))
        assert exercise(off) == observed == exercise(fault)
        assert any(call.args[:2] == ("model_updater", "root_shape") for call in fault.record.call_args_list)
    finally:
        reader.close()
        collector.close(timeout=2.0)
    finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text().splitlines()]
    raw = [row for row in finalized if row.get("details", {}).get("postmove_reconcile_phase")]
    assert len(raw) == 64
    expected_refs = {opaque_trace_correlation(name) for name in names}
    assert {row["details"]["selected_root_ref"] for row in raw} == expected_refs
    assert all(len(row["details"]) <= 24 for row in raw)
    assert all(row["details"]["postmove_reconcile_omitted"] is False for row in raw)
    late = raw[32:]
    assert sum(row["details"]["local_input_entry"] == "absent_complete" for row in late) == 16
    for phase in ("reconcile_delta_input", "reconcile_delta_output", "reconcile_final_input", "reconcile_final_output"):
        scoped = [row["details"] for row in late if row["details"]["phase"] == phase]
        assert len(scoped) == 8
        assert sum(row["local_member"] is False for row in scoped) == (8 if phase == "reconcile_delta_output" else 4)
    selected = [row for row in _artifacts(artifact)
                if row["capture"].get("state") == "record"
                and "postmove_reconcile" in row["capture"]["projection"]]
    assert len(selected) == len(late)
    for source, captured in zip(late, selected):
        sink = captured["capture"]["projection"]
        facts = sink["postmove_reconcile"]
        assert facts["root_ref"] == source["details"]["selected_root_ref"]
        assert facts["local_input_entry"] == source["details"]["local_input_entry"]
        assert facts["local_member"] == source["details"]["local_member"]
        assert observer._sanitize_capture_projection(sink)["postmove_reconcile"] == facts
        assert captured["capture"]["created_ns"] == source["created_ns"]
        assert captured["capture"]["created_ms"] == source["created_ms"]
    assert not any(name in artifact.read_text() for name in names)


@pytest.mark.skipif(BreadcrumbTraceCollector is None or trace_session_digest is None or _record_lifecycle_scan_breadcrumb is None, reason="production breadcrumb producer is unavailable")
def test_builder_postterminal_absence_preserves_bucket_in_finalized_sink(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(
        lambda: True, policy={"default": "off", "rules": {"lifecycle.persist": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    builder = ModelBuilder()
    remote = SystemFile("record.bin", 10, False)
    remote.path_pair_id = "sample-pair"
    builder.set_remote_files([remote])
    candidate = builder.build_model()
    file_id = ModelFile.build_file_id(remote.name, remote.path_pair_id)
    assert candidate.get_file(file_id).state == ModelFile.State.DEFAULT
    assert candidate.get_file(file_id).local_size is None
    builder.set_downloaded_files({file_id})
    builder.set_stop_resume_trace_breadcrumb(collector)
    kwargs = {
        "local_scan_snapshot": {}, "local_scan_authority": {(remote.path_pair_id, remote.name): None},
        "local_pair_authority_tokens": {remote.path_pair_id: ("local-instance", 4)},
        "local_completed_pair_ids": {remote.path_pair_id}, "local_incomplete_pair_ids": set(),
        "local_process_session": "local-instance", "local_accumulator_session": "local-instance",
        "local_process_generation": 4, "pre_poll_file_ids": set(),
        "current_process_final_publication_file_ids": {file_id},
    }
    reader = _reader(spool, artifact)
    try:
        assert collector.record(
            "model_builder", "publication_test_baseline", {"ready": True},
            category="lifecycle.persist", stage="persist_authority", event_type="diagnostic", level="info",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader.start()
        subjects = builder.record_lifecycle_candidate_publication(candidate, "full_build", live=candidate, **kwargs)
        assert subjects == {file_id}
        builder.record_lifecycle_live_publication(candidate, candidate, subjects, "full_build", "full_model_adopted", **kwargs)
        assert collector.flush_durable(2.0)
        assert reader.poll() == 2
    finally:
        reader.close()
        collector.close(timeout=2.0)
    raw = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text().splitlines()]
    raw = [row for row in raw if row.get("message") in {"model_candidate", "model_live_publication"}]
    assert [row["message"] for row in raw] == ["model_candidate", "model_live_publication"]
    captured = [row for row in _artifacts(artifact) if row["capture"].get("state") == "record"]
    assert len(captured) == 2
    for source, sink in zip(raw, captured):
        details = source["details"]
        assert len(details) <= 24 and "monotonic_ms" in details
        assert details["local_flow_input_entry"] == "known_none"
        assert details["local_flow_bucket"] == "no_member"
        assert details["current_process_final_publication"] is True
        assert details["candidate_state"] == "default" and details["candidate_local_size"] is None
        facts = sink["capture"]["projection"]["lifecycle_persist"]
        assert facts["exact_local_flow_join"]["authority_entry"] == "known_none"
        assert facts["exact_local_flow_join"]["bucket"] == "no_member"
        assert observer._sanitize_capture_projection(sink["capture"]["projection"])["lifecycle_persist"] == facts
        assert sink["capture"]["created_ns"] == source["created_ns"]
    assert remote.name not in artifact.read_text() and "local-instance" not in artifact.read_text()


@pytest.mark.skipif(BreadcrumbTraceCollector is None or trace_session_digest is None or _record_lifecycle_scan_breadcrumb is None, reason="production breadcrumb producer is unavailable")
def test_finalized_local_scan_tick_and_lifecycle_reach_json_sink_in_order(tmp_path: Path) -> None:
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=16,
        policy={"default": "off", "rules": {"scan.adoption": "info", "scan.authority": "info", "scan.lifecycle": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact)
    session_digest = trace_session_digest("local scan session")
    assert re.fullmatch(r"[0-9a-f]{12}", session_digest)
    producer = SimpleNamespace(_Controller__context=SimpleNamespace(breadcrumb_trace=collector))
    tick = {
        "p": True, "m": "progressive", "s": session_digest, "g": 4,
        "pr": True, "f": False, "fa": False, "fu": False, "na": True,
        "private_path": "/private/drop",
    }
    try:
        assert collector.record(
            "model_updater", "baseline", {}, category="scan.adoption", stage="scan_adoption",
            event_type="state_transition", level="info",
        ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        reader.start()
        # A production collector writes and finalizes all four envelopes; the
        # consumer then reads the durable file and persists its JSON artifact.
        rows = (
            ("scan.adoption", "scan_adoption", "scan_adoption", "state_transition", {"local_scan_tick": tick}),
            ("scan.authority", "input_arbitration_witness", "model_builder_input_arbitration", "diagnostic", {
                "schema": "model_input_arbitration.v1", "phase": "root_source", "subject_kind": "root",
                "root_id": "0123456789abcdef", "subject_id": "fedcba9876543210", "input_scope": "root",
                "selection": "local", "p": {"lo": "0011223344556677", "ps": None, "pb": None},
                "local_scan_tick": tick,
            }),
            ("scan.lifecycle", "scan_accumulator_before_apply", "scan_accumulator", "diagnostic", {
                "scanner_side": "local", "event_count": 1,
                "full_event_count": 0, "distinct_generation_count": 1, "min_generation": 4,
                "max_generation": 4, "targeted": True, "generation": 4, "final": False,
                "full": False, "progress": True, "failed": False, "scanned_pair_count": 1,
                "completed_pair_count": 0, "unknown_pair_count": 1, "root_count": 1, "file_count": 0,
                "handoff_root_present": False, "fence": "none", "monotonic_ms": 50,
                "accumulator_working_root_count": 1, "accumulator_committed_root_count": 0,
                "accumulator_authoritative_root_count": 0, "accumulator_touched_root_count": 1,
                "private_label": "/secret/path",
            }),
            ("scan.lifecycle", "scan_accumulator_after_apply", "scan_accumulator", "diagnostic", {
                "scanner_side": "local", "event_count": 1,
                "full_event_count": 0, "distinct_generation_count": 1, "min_generation": 4,
                "max_generation": 4, "targeted": True, "generation": 4, "final": False,
                "full": False, "progress": True, "failed": False, "scanned_pair_count": 1,
                "completed_pair_count": 1, "unknown_pair_count": 0, "root_count": 1, "file_count": 1,
                "handoff_root_present": True, "fence": "none", "monotonic_ms": 51,
                "accumulator_working_root_count": 1, "accumulator_committed_root_count": 1,
                "accumulator_authoritative_root_count": 1, "accumulator_touched_root_count": 1,
            }),
        )
        for category, message, stage, event_type, details in rows:
            if category == "scan.lifecycle":
                _record_lifecycle_scan_breadcrumb(
                    producer, "local", "local scan session", message, details,
                )
                continue
            assert collector.record(
                "model_updater", message, details, category=category, stage=stage,
                event_type=event_type, level="info", corr_id="same:0123456789abcdef",
                flow_id="same:0123456789abcdef", trace_scope="flow",
            ) in {"retained", "enqueued"}
        assert collector.flush_durable(2.0)
        assert reader.poll() == 4
    finally:
        reader.close()
        collector.close(timeout=2.0)

    saved_envelopes = [row for row in _artifacts(artifact)
                       if row["capture"].get("state") == "record"]
    finalized_with_cursors: list[tuple[int, int, dict[str, object]]] = []
    cursor = 0
    for line in (spool / "breadcrumbs.jsonl").read_bytes().splitlines(keepends=True):
        end = cursor + len(line)
        if line.strip():
            finalized_with_cursors.append((cursor, end, json.loads(line)))
        cursor = end
    finalized = [row for _start, _end, row in finalized_with_cursors]
    baseline_index = next(
        index for index, row in enumerate(finalized)
        if row.get("category") == "scan.adoption" and row.get("message") == "baseline"
    )
    armed_rows = finalized_with_cursors[baseline_index + 1:]
    assert len(armed_rows) == len(saved_envelopes) == 4
    capture_clocks: list[tuple[int, int]] = []
    paired = list(zip(armed_rows, saved_envelopes))
    captured_ranges: list[tuple[int, int]] = []
    raw_identities: list[tuple[object, ...]] = []
    captured_identities: list[tuple[object, ...]] = []
    for (start, end, raw), envelope in paired:
        capture_row = envelope["capture"]
        assert capture_row["state"] == "record"
        capture_start, capture_end = capture_row["cursor_start"], capture_row["cursor_end"]
        assert capture_start <= start < end <= capture_end
        captured_ranges.append((capture_start, capture_end))
        projection = capture_row["projection"]
        assert (projection["category"], projection["stage"], projection["event_type"]) == (
            raw["category"], raw["stage"], raw["event_type"],
        )
        capture_clocks.append((capture_row["utc_ms"], capture_row["monotonic_ms"]))
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        expected_numbers = capture._numeric_values(raw, metadata, details)
        assert projection["numbers"] == expected_numbers
        corr_id = raw.get("corr_id", raw.get("correlation_id", metadata.get("corr_id", metadata.get("correlation_id"))))
        flow_id = raw.get("flow_id", metadata.get("flow_id"))
        file_id = raw.get("file_id", metadata.get("file_id"))
        pair_id = raw.get("path_pair_id", metadata.get("path_pair_id"))
        raw_identities.append((
            raw.get("created_ns"), raw.get("created_ms"), raw.get("category"), raw.get("stage"),
            raw.get("event_type"), capture._hash(corr_id), capture._hash(flow_id),
            capture._hash(file_id), capture._hash(pair_id), expected_numbers,
        ))
        captured_identities.append((
            capture_row.get("created_ns"), capture_row.get("created_ms"), projection.get("category"),
            projection.get("stage"), projection.get("event_type"), projection.get("corr_hash"),
            projection.get("flow_hash"), projection.get("file_hash"), projection.get("pair_hash"),
            projection.get("numbers"),
        ))
    assert all(left[0] <= right[0] and left[1] <= right[1]
               for left, right in zip(capture_clocks, capture_clocks[1:]))
    assert raw_identities == captured_identities
    assert all(any(batch_start <= start and end <= batch_end
                   for batch_start, batch_end in captured_ranges)
               for start, end, _row in armed_rows)
    saved = [envelope["capture"]["projection"] for envelope in saved_envelopes]
    lifecycle_rows = [row for row in finalized if row.get("category") == "scan.lifecycle"]
    assert len(lifecycle_rows) == 2
    assert [row["message"] for row in lifecycle_rows] == [
        "scan_accumulator_before_apply", "scan_accumulator_after_apply",
    ]
    assert all(row["corr_id"] == "local:" + session_digest for row in lifecycle_rows)
    assert all(row["details"].get("session_digest") != session_digest for row in lifecycle_rows)
    adoption_projection = next(
        projection for (_start, _end, raw), projection in zip(armed_rows, saved)
        if raw.get("category") == "scan.adoption"
    )
    root_source_projection = next(
        projection for (_start, _end, raw), projection in zip(armed_rows, saved)
        if raw.get("category") == "scan.authority"
        and raw.get("details", {}).get("phase") == "root_source"
    )
    before_projection = next(
        projection for (_start, _end, raw), projection in zip(armed_rows, saved)
        if raw.get("message") == "scan_accumulator_before_apply"
    )
    after_projection = next(
        projection for (_start, _end, raw), projection in zip(armed_rows, saved)
        if raw.get("message") == "scan_accumulator_after_apply"
    )
    assert adoption_projection["local_scan_tick"] == {
        "present": True, "mode": "progressive", "session_digest": session_digest, "generation": 4,
        "progress": True, "final": False, "failed": False, "full": False, "new_authority": True,
    }
    assert root_source_projection["local_scan_tick"] == adoption_projection["local_scan_tick"]
    assert before_projection["scan_lifecycle"]["message"] == "scan_accumulator_before_apply"
    assert before_projection["scan_lifecycle"]["session_digest"] == session_digest
    assert before_projection["scan_lifecycle"]["completed_pair_count"] == 0
    assert after_projection["scan_lifecycle"]["message"] == "scan_accumulator_after_apply"
    assert after_projection["scan_lifecycle"]["session_digest"] == session_digest
    assert after_projection["scan_lifecycle"]["completed_pair_count"] == 1
    assert before_projection["corr_hash"] == after_projection["corr_hash"]
    assert before_projection["scan_lifecycle"]["scanner_side"] == "local"
    assert "private_path" not in json.dumps(saved) and "private_label" not in json.dumps(saved)
    malformed, irrelevant = capture._projection({
        "category": "scan.adoption", "source": "model_updater", "event_type": "state_transition",
        "stage": "scan_adoption", "level": "info", "message": "scan_adoption",
        "details": {"local_scan_tick": {**tick, "s": "0123456789abcdef", "g": 2**63}},
    })
    assert not irrelevant and malformed is not None
    assert malformed["local_scan_tick"]["session_digest"] == "unknown"
    assert malformed["local_scan_tick"]["generation"] == "unknown"
    wrong_side, irrelevant = capture._projection({
        "category": "scan.lifecycle", "source": "model_updater", "event_type": "diagnostic",
        "stage": "scan_accumulator", "level": "info", "message": "scan_accumulator_after_apply",
        "corr_id": "remote:0123456789ab",
        "details": {"scanner_side": "local", "session_digest": "0123456789ab"},
    })
    assert not irrelevant and wrong_side is not None
    assert wrong_side["scan_lifecycle"]["session_digest"] == "unknown"


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
    remote_collision = SystemFile("collision.bin", 512, False, mtime_ns=epoch_ns + 5_000_000_000)
    remote_root.add_child(remote_collision)
    local_root = SystemFile("mixed-root", 35_759_708_487, True, mtime_ns=epoch_ns)
    local_root.add_child(SystemFile("payload.iso", 2_981_865_472, False, is_staging=True,
                                    mtime_ns=epoch_ns + 24_000_000_000))
    local_checksum = SystemFile("checksums.txt", 1_072, False, mtime_ns=epoch_ns)
    local_collision = SystemFile("collision.bin", 512, False, mtime_ns=epoch_ns + 5_000_000_000)
    local_collision.has_staging_collision = True
    local_root.add_child(local_checksum)
    local_root.add_child(local_collision)
    active_root = SystemFile("mixed-root", 2_981_865_472, True)
    active_root.add_child(SystemFile("payload.iso", 2_981_865_472, False,
                                     mtime_ns=epoch_ns + 24_000_000_000))
    active_collision = SystemFile("collision.bin", 512, False, is_staging=True,
                                  mtime_ns=epoch_ns + 6_000_000_000)
    active_root.add_child(active_collision)
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
        model = builder.build_model(
            diagnostic_target_root_ids=[ModelFile.build_file_id("mixed-root", None)],
        )
        assert model.get_file("mixed-root") is not None
        assert collector.flush_durable(2.0)
        assert reader.poll() == 6
    finally:
        reader.close()
        collector.close(timeout=2.0)

    durable = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    finalized = [row for row in durable if row.get("message") == "input_arbitration_witness"]
    assert len(finalized) == 10
    assert all(row.get("category") == "scan.authority"
               and row.get("stage") == "model_builder_input_arbitration"
               and row.get("event_type") == "diagnostic"
               and row.get("trace_scope") == "flow" and row.get("corr_id") == row.get("flow_id")
               and re.fullmatch(r"input-arbitration:[0-9a-f]{16}", row["corr_id"]) for row in finalized)
    projections = [item["capture"]["projection"] for item in _artifacts(artifact)
                   if item["capture"].get("state") == "record"]
    assert len(projections) == 6
    assert all(item["category"] == "scan.authority" and item["event_type"] == "diagnostic"
               and item["stage"] == "model_builder_input_arbitration" for item in projections)
    phases = [item["input_arbitration"]["phase"] for item in projections]
    assert phases.count("root_fill") == 1 and phases.count("remote_leaf_fill") == 3
    assert phases.count("summary") == 1 and phases.count("root_source") == 1
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
        if leaf["split_candidate_provenance"] == "split_candidate_local":
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
    assert root["selection"] == "split" and root["selection_reason"] == "split_merge"
    assert root["local_staging_descendant"] is True
    assert root["local_staging_collision_descendant"] is True
    assert root["split_has_verified_leaf"] is False
    assert root["local_candidate_eligible"] is None and root["local_size_ge_active"] is None
    assert root["remote_indicates_newer"] is None
    checksum = next(item["input_arbitration"] for item in projections
                    if item.get("input_arbitration", {}).get("split_candidate_remote_size") == "1072")
    assert checksum["split_candidate_local_present"] is True
    assert checksum["split_candidate_local_size"] == checksum["split_candidate_remote_size"] == "1072"
    assert checksum["split_candidate_local_mtime_ns"] == str(epoch_ns)
    assert checksum["split_candidate_remote_mtime_ns"] == str(epoch_ns + 500_000_000)
    assert int(checksum["split_candidate_local_mtime_ns"]) > 2**53
    assert checksum["split_candidate_local_mtime_known"] is True
    assert checksum["split_candidate_remote_mtime_known"] is True
    assert checksum["split_candidate_strict_verified"] is False
    assert checksum["split_candidate_portable_queue_exclusion_match"] is True
    assert checksum["split_candidate_provenance"] == "split_candidate_local"
    collision = next(item["input_arbitration"] for item in projections
                     if item.get("input_arbitration", {}).get("split_candidate_local_has_staging_collision") is True)
    assert collision["split_candidate_active_present"] is True
    assert collision["split_candidate_active_is_staging"] is True
    assert collision["split_candidate_active_has_staging_collision"] == "unknown"
    assert collision["split_candidate_active_matches_remote_identity"] is False
    assert collision["split_candidate_local_size"] == collision["split_candidate_active_size"] == "512"
    assert collision["split_candidate_active_mtime_ns"] == str(epoch_ns + 6_000_000_000)
    assert re.fullmatch(r"[0-9a-f]{16}", collision["split_candidate_local_object_ref"])
    assert re.fullmatch(r"[0-9a-f]{16}", collision["split_candidate_active_object_ref"])
    assert collision["split_candidate_local_object_ref"] != collision["split_candidate_active_object_ref"]
    assert collision["split_candidate_local_collision_scope"] == "leaf"
    assert [item["version"] for item in finalized] == list(range(1, 11))
    versions = [item["numbers"]["version"] for item in projections]
    assert versions == [5, 6, 7, 8, 9, 10]


@pytest.mark.skipif(BreadcrumbTraceCollector is None, reason="production breadcrumb collector is unavailable")
def test_finalized_cached_collision_input_survives_without_active_root(tmp_path: Path) -> None:
    spool = tmp_path / "cached-collision-spool"
    spool.mkdir()
    artifact = tmp_path / "cached-collision-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True, max_entries=64,
        policy={"default": "off", "rules": {"scan.authority": "info"}},
        durable_enabled=True, durable_path=str(spool),
    )
    reader = _reader(spool, artifact)
    baseline = ModelBuilder()
    baseline_remote = SystemFile("baseline-root", 8, True)
    baseline_remote.add_child(SystemFile("baseline-leaf", 8, False))
    baseline_local = SystemFile("baseline-root", 8, True)
    baseline_local.add_child(SystemFile("baseline-leaf", 8, False))
    baseline.set_remote_files([baseline_remote])
    baseline.set_local_files([baseline_local])
    baseline.set_stop_resume_trace_breadcrumb(collector.create_emitter())
    builder = ModelBuilder()
    source_mtime_ns = 1_790_873_610_000_000_000
    remote_root = SystemFile("cached-root", 512, True)
    remote_root.add_child(SystemFile("collision.bin", 512, False, mtime_ns=source_mtime_ns))
    local_root = SystemFile("cached-root", 512, True)
    local_leaf = SystemFile(
        "collision.bin", 512, False, is_staging=True, mtime_ns=source_mtime_ns,
    )
    local_leaf.has_staging_collision = True
    local_root.add_child(local_leaf)
    builder.set_remote_files([remote_root])
    builder.set_local_files([local_root])
    builder.set_stop_resume_trace_breadcrumb(collector.create_emitter())
    cached_root_id = ModelFile.build_file_id("cached-root", None)
    try:
        assert baseline.build_model().get_file("baseline-root") is not None
        assert collector.flush_durable(2.0)
        reader.start()
        model = builder.build_model(diagnostic_target_root_ids=[cached_root_id])
        assert model.get_file("cached-root") is not None
        assert collector.flush_durable(2.0)
        assert reader.poll() > 0
    finally:
        reader.close()
        collector.close(timeout=2.0)
    durable = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()]
    finalized = [row for row in durable if row.get("message") == "input_arbitration_witness"]
    candidate = next(row for row in finalized
                     if isinstance(row.get("details", {}).get("split_candidate"), dict)
                     and row["details"]["split_candidate"].get("provenance") == "cached_effective_input")
    assert candidate["details"]["split_candidate"]["local_has_staging_collision"] is True
    assert "collision.bin" not in json.dumps(candidate)
    projections = [item["capture"]["projection"] for item in _artifacts(artifact)
                   if item["capture"].get("state") == "record"]
    leaf = next(item["input_arbitration"] for item in projections
                if item.get("input_arbitration", {}).get("split_candidate_provenance") == "cached_effective_input")
    assert leaf["split_candidate_local_is_staging"] is True
    assert leaf["split_candidate_local_has_staging_collision"] is True
    assert leaf["split_candidate_local_collision_scope"] == "leaf"
    assert leaf["split_candidate_active_present"] == "unknown"
    assert leaf["split_candidate_local_size"] == leaf["split_candidate_remote_size"] == "512"
    assert re.fullmatch(r"[0-9a-f]{16}", leaf["split_candidate_local_object_ref"])


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


def test_model_publication_retry_reaches_finalized_reader_and_json_sink(tmp_path: Path) -> None:
    assert BreadcrumbTraceCollector is not None
    assert ModelUpdater is not None and _model_updater_fixture_module is not None
    _ModelUpdaterTestFixture = _model_updater_fixture_module.TestModelUpdater
    assert Path(capture.__file__).resolve() == (ROOT / "breadcrumb_spool_capture.py").resolve()
    assert Path(observer.__file__).resolve() == (ROOT / "incoming_recovery_observer.py").resolve()
    updater_module = sys.modules[ModelUpdater.__module__]
    fixture_module = sys.modules[_ModelUpdaterTestFixture.__module__]
    assert Path(updater_module.__file__).resolve() == Path("/src/controller/model_updater.py")
    assert Path(fixture_module.__file__).resolve() == Path("/src/tests/unittests/test_controller/test_model_updater.py")
    spool = tmp_path / "publication-retry-spool"
    spool.mkdir()
    artifact = tmp_path / "publication-retry-capture.jsonl"
    collector = BreadcrumbTraceCollector(
        lambda: True,
        max_entries=32,
        policy={"default": "off", "rules": {"model.candidate": "info"}},
        durable_enabled=True,
        durable_path=str(spool),
        durable_enabled_getter=lambda: True,
    )

    old = SystemFile("private-old-root", 10, False)
    old.path_pair_id = "private-pair"
    builder = ModelBuilder()
    builder.set_local_files([old])
    builder.set_remote_files([old])
    live_model = builder.build_model()
    next_local = SystemFile("private-new-root", 30, False)
    next_local.path_pair_id = "private-pair"
    next_remote = SystemFile("private-new-root", 30, False)
    next_remote.path_pair_id = "private-pair"
    final_local = ScannerResult(
        datetime.now(), [next_local], scanned_path_pair_ids={"private-pair"},
        is_progress=True, completed_path_pair_ids={"private-pair"}, is_scan_final=True,
        is_full_snapshot=True, full_snapshot_path_pair_ids={"private-pair"},
    )
    final_remote = ScannerResult(
        datetime.now(), [next_remote], scanned_path_pair_ids={"private-pair"},
        is_progress=True, completed_path_pair_ids={"private-pair"}, is_scan_final=True,
        is_full_snapshot=True, full_snapshot_path_pair_ids={"private-pair"},
    )
    controller, _ = _ModelUpdaterTestFixture()._make_progressive_update_controller(
        final_remote, local_scan=final_local, model_builder=builder, model=live_model,
    )
    controller._Controller__path_pairs_by_id = {"private-pair": MagicMock()}
    controller._Controller__context.breadcrumb_trace = collector
    controller._Controller__record_breadcrumb = Controller._Controller__record_breadcrumb.__get__(
        controller, Controller,
    )
    updater = ModelUpdater(controller)
    controller._Controller__updater = updater

    try:
        assert collector.record(
            "controller", "capture_boundary_probe", {}, category="model.candidate",
            event_type="diagnostic", stage="model", level="info",
        ) in {"retained", "enqueued"}
        assert collector.wait_for_ingress(2.0)
        assert collector.flush_durable(2.0)
        assert collector.durable_snapshot()["session_boundary_written"] is True
        reader = _reader(spool, artifact)
        try:
            reader.start()
            publication_error = RuntimeError("private publication exception")

            def fail_publication(*_args: object, **_kwargs: object) -> None:
                raise publication_error

            live_model.add_file = fail_publication
            with pytest.raises(RuntimeError) as first_failure:
                updater.update()
            assert first_failure.value is publication_error

            for side in ("local", "remote", "active"):
                process = getattr(controller, "_Controller__{}_scan_process".format(side))
                process.pop_latest_result.return_value = None
            with pytest.raises(RuntimeError) as retry_failure:
                updater.update()
            assert retry_failure.value is publication_error

            del live_model.add_file
            updater.update()
            assert not builder.has_changes()
            assert collector.flush_durable(2.0)
            assert reader.poll() >= 3
        finally:
            reader.close()
    finally:
        collector.close(timeout=2.0)

    finalized = [
        json.loads(line)
        for line in (spool / "breadcrumbs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    producer_rows = [row for row in finalized if row.get("message") == "model_publication_retry"]
    expected_outcomes = ["retry_admitted", "retry_rearmed", "retry_adopted"]
    assert [row["details"].get("outcome") for row in producer_rows] == expected_outcomes
    assert all(
        row.get("source") == "controller" and row.get("category") == "model.candidate"
        and row.get("event_type") == "state_transition" and row.get("stage") == "model"
        and row.get("level") == "info" and row["details"] == {
            "schema": "model_publication_retry.v1", "outcome": outcome,
        }
        for row, outcome in zip(producer_rows, expected_outcomes)
    )

    captured = [
        envelope for envelope in _artifacts(artifact)
        if envelope.get("capture", {}).get("state") == "record"
        and "model_publication_retry" in envelope["capture"].get("projection", {})
    ]
    assert len(captured) == len(producer_rows) == 3
    for raw, envelope, outcome in zip(producer_rows, captured, expected_outcomes):
        projection = envelope["capture"]["projection"]
        assert envelope["capture"]["created_ns"] == raw["created_ns"]
        assert projection["category"] == raw["category"]
        assert projection["corr_hash"] == capture._hash(raw["corr_id"])
        assert projection["model_publication_retry"] == {
            "schema": "model_publication_retry.v1", "outcome": outcome,
        }

    artifact_text = artifact.read_text(encoding="utf-8")
    for private in ("private-old-root", "private-new-root", "private-pair", "private publication exception"):
        assert private not in artifact_text


@pytest.mark.parametrize(
    "schema,outcome",
    [
        ("private.schema", "retry_admitted"),
        ("model_publication_retry.v1", "private-outcome"),
        ("model_publication_retry.v1", True),
    ],
)
def test_model_publication_retry_projection_guards_unknown_fields(schema: object, outcome: object) -> None:
    projected, unsupported = capture._projection({
        "source": "controller", "message": "model_publication_retry",
        "category": "model.candidate", "event_type": "state_transition",
        "stage": "model", "level": "info", "corr_id": "model_update:aggregate",
        "details": {
            "schema": schema, "outcome": outcome,
            "private_path": "/private/root", "private_label": "private-name",
        },
    })
    assert not unsupported and projected is not None
    known_schema = schema == "model_publication_retry.v1"
    known_outcome = (
        isinstance(outcome, str)
        and outcome in {"retry_admitted", "retry_rearmed", "retry_adopted"}
    )
    assert projected["model_publication_retry"] == {
        "schema": "model_publication_retry.v1" if known_schema else "unknown",
        "outcome": outcome if known_schema and known_outcome else "unknown",
    }
    sanitized = observer._sanitize_capture_projection(projected)
    assert sanitized["model_publication_retry"] == projected["model_publication_retry"]
    assert "private_path" not in json.dumps(sanitized) and "private-name" not in json.dumps(sanitized)
    wrong_context, rejected = capture._projection({
        "source": "model_updater", "message": "model_publication_retry",
        "category": "model.candidate", "event_type": "state_transition",
        "stage": "model", "level": "info",
        "details": {"schema": "model_publication_retry.v1", "outcome": "retry_admitted"},
    })
    assert wrong_context is None and rejected


def test_status_input_snapshot_survives_finalized_reader_and_sink(tmp_path: Path) -> None:
    from tests.unittests.test_lftp.test_lftp import TestLftp
    from lftp import LftpJobStatusParser
    import lftp.lftp as lftp_module
    assert _ControllerFixture is not None and BreadcrumbTraceCollector is not None
    spool, artifact = _prepare(tmp_path)
    collector = BreadcrumbTraceCollector(lambda: True, max_entries=256,
        policy={"default": "off", "rules": {"transfer.lftp.status": "info", "controller": "info"}},
        durable_enabled=True, durable_path=str(spool))
    fixture = _ControllerFixture()
    fixture.setUp()
    reader = _reader(spool, artifact, max_records=256)
    correlation = "lftp-poll:0123456789abcdef"
    try:
        collector.record("controller", "boundary", {}, category="controller", level="info")
        assert collector.wait_for_ingress(2.0) and collector.flush_durable(2.0)
        reader.start()
        backend = TestLftp()._build_status_poll_test_lftp()
        backend._Lftp__job_status_parser = LftpJobStatusParser()
        backend.set_breadcrumb_trace(collector)
        producer = fixture.controller
        producer._Controller__context.breadcrumb_trace = collector
        producer._Controller__lftp = backend
        def begin():
            producer._Controller__lftp_status_poll_correlation = correlation
            return correlation
        producer._Controller__begin_lftp_status_poll_lineage = begin
        partial = "jobs -v\n[0] queue (sftp://user@example.invalid)\nsftp://user@example.invalid/remote\n"
        healthy = partial + "Queue is running.\nCommands queued:\n1. pget -c /remote/sample.bin -o /local/sample.bin\n"
        for output, health in ((healthy, True), (partial, False), ("jobs -v\n", True)):
            backend._Lftp__run_command = MagicMock(return_value=output)
            assert producer._get_lftp_status_snapshot() is None
            producer._Controller__lftp_status_future.result(timeout=2)
            snapshot = producer._get_lftp_status_snapshot()
            assert snapshot[1] is health
        assert collector.wait_for_ingress(2.0) and collector.flush_durable(2.0)
        finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text().splitlines()]
        produced = [row for row in finalized if row.get("details", {}).get("schema") == "lftp.status_poll.v1"]
        assert len([row for row in produced if row["details"]["phase"] == "snapshot"]) == 3
        assert all(row["corr_id"] == correlation for row in produced)
        reader.poll()
        retained = [x["capture"] for x in _artifacts(artifact) if x["capture"].get("state") == "record"]
        projected = [x for x in retained if x["projection"].get("lftp_status_authority", {}).get("schema") == "lftp.status_poll.v1"]
        assert [x["projection"]["numbers"]["version"] for x in projected] == [x["version"] for x in produced]
        assert [x["created_ns"] for x in projected] == [x["created_ns"] for x in produced]
        snapshots = [x["projection"]["lftp_status_authority"] for x in projected if x["projection"]["lftp_status_authority"]["phase"] == "snapshot"]
        produced_snapshots = [x for x in produced if x["details"]["phase"] == "snapshot"]
        assert [x["healthy"] for x in snapshots] == [x["details"]["healthy"] for x in produced_snapshots]
        causal_snapshots = sorted(produced_snapshots, key=lambda x: x["created_ns"])
        assert [x["details"]["healthy"] for x in causal_snapshots] == [True, False, True]
        assert [x["parser_outcome"] for x in snapshots] == [x["details"]["parser_outcome"] for x in produced_snapshots]
        assert all(x["input_phase"] == "primary" and x["snapshot_disposition"] == "accepted" for x in snapshots)
        assert "example.invalid" not in artifact.read_text() and "/remote/sample" not in artifact.read_text()
        assert all(len(json.dumps(row)) < 4096 for row in produced)
        malicious = capture._lftp_status_authority_facts({"schema": "lftp.status_poll.v1", "input_phase": "/private", "healthy": 1, "status_count_bucket": "999999", "raw": "secret"}, "lftp_status_poll")
        assert malicious["input_phase"] == "unknown" and malicious["healthy"] is None
        assert malicious["status_count_bucket"] == "unknown" and "raw" not in malicious
        assert observer._sanitize_capture_projection({"lftp_status_authority": malicious})["lftp_status_authority"] == malicious
    finally:
        reader.close()
        collector.close()
        fixture.doCleanups()


def test_selected_scan_witness_retains_beginning_late_and_resets_provenance(tmp_path: Path) -> None:
    from common.root_progress_trace import root_progress_tracer
    from common.breadcrumb_trace import opaque_trace_correlation, is_critical_breadcrumb
    spool, artifact = _prepare(tmp_path)
    trace = BreadcrumbTraceCollector(lambda: True, max_entries=256, policy={"default": "info"},
        durable_enabled=True, durable_path=str(spool))
    reader = _reader(spool, artifact, max_records=1024)
    tracer = root_progress_tracer(trace)
    digest = opaque_trace_correlation("synthetic-selected-root")
    poll_a = "lftp-poll:" + "a" * 16
    try:
        tracer.select_numeric_subject(digest)
        trace.record("controller", "boundary", {}, category="controller", level="info")
        assert trace.wait_for_ingress(2) and trace.flush_durable(2)
        reader.start()
        with patch("common.root_progress_trace.time.monotonic", return_value=10):
            assert tracer.record_numeric(digest, "status", {"input_correlation": poll_a,
                "status_age_ms": 2000, "input_epoch": 2, "status_bytes": 2**53 + 7})
            assert tracer.record_numeric(digest, "active_scan", {"active_bytes": 2**53 + 9,
                "active_generation": 7, "active_adopted": True})
        for index in range(1500):
            trace.record("background", "synthetic_normal_load", {"counter": index},
                category="model.progress", level="info", _coalesce_key=str(index))
        with patch("common.root_progress_trace.time.monotonic", return_value=45):
            assert tracer.record_numeric(digest, "active_scan", {"active_bytes": 2**53 + 9,
                "active_generation": 7, "active_adopted": True})
            tracer.record_numeric(digest, "publication", {"model_version": 77, "selected_bytes": 2**53 + 7,
                "poll_correlation": "lftp-poll:" + "b" * 16, "status_source": "cached_unhealthy"})
            tracer.record_numeric_payload({"records": [{"file_id": "synthetic-selected-root", "transferred_size": 2**53 + 7}]}, "json", 77)
        assert trace.wait_for_ingress(2)
        clean_flush = trace.flush_durable(2)
        health = trace.durable_snapshot()
        if not clean_flush:
            # Normal saturation is a measured diagnostic coverage loss, not a clean flush.
            assert health.get("lost", 0) > 0 or trace.snapshot().get("dropped", 0) > 0
        assert health.get("critical_pending", 0) == 0 and health.get("normal_pending", 0) == 0
        assert not health.get("writing", False)
        rows = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text().splitlines()]
        scans = [r for r in rows if r.get("details", {}).get("phase") == "active_scan"]
        assert len(scans) == 2
        assert all(r["level"] == "info" and is_critical_breadcrumb(r["source"], r["message"], r["details"], r) for r in scans)
        assert all(r["details"]["values"]["active_bytes"] == str(2**53 + 9) for r in scans)
        assert all(r["details"]["input_correlation"] == poll_a for r in scans)
        # A saturated normal lane may block reader continuation; this must be explicit, never success with silent loss.
        try:
            reader.poll()
        except observer.ObserverCaptureError:
            assert any(x["capture"].get("state") == "loss" for x in _artifacts(artifact))
        for row in scans:
            projection, irrelevant = capture._projection(row)
            assert not irrelevant and projection is not None
            sanitized = observer._sanitize_capture_projection(projection)
            assert sanitized["numeric_progress"]["values"]["active_bytes"] == str(2**53 + 9)
            assert sanitized["numeric_progress"]["input_correlation"] == poll_a
        trace.reset()
        with patch("common.root_progress_trace.time.monotonic", return_value=50):
            tracer.record_numeric_payload({"records": [{"file_id": "synthetic-selected-root", "transferred_size": 9}]}, "sse", 77)
        assert trace.wait_for_ingress(2)
        reset_rows = [r["details"] for r in trace.snapshot()["entries"] if r.get("details", {}).get("phase") == "sse"]
        assert reset_rows and reset_rows[-1]["input_correlation"] is None
        assert reset_rows[-1]["values"]["active_generation"] is None
        assert reset_rows[-1]["values"]["status_age_ms"] is None
    finally:
        reader.close()
        trace.close()


def test_poll_failure_metrics_survive_finalized_reader_and_sink(tmp_path: Path) -> None:
    import lftp.lftp as producer
    spool, artifact = _prepare(tmp_path)
    trace = BreadcrumbTraceCollector(lambda: True, max_entries=256,
        policy={"default": "off", "rules": {"transfer.lftp.status": "debug", "controller": "info"}},
        durable_enabled=True, durable_path=str(spool))
    reader = _reader(spool, artifact, max_records=256)
    correlation = "lftp-poll:0123456789abcdef"
    try:
        trace.record("controller", "boundary", {}, category="controller", level="info")
        assert trace.wait_for_ingress(2) and trace.flush_durable(2)
        reader.start()
        for phase, reason in (("jobs_read", "none"), ("prompt_timeout", "timeout"), ("parse_error", "parser_error")):
            with patch("lftp.lftp.time.monotonic_ns", return_value=2**53 + 7):
                producer._record_lftp_status_poll_breadcrumb(trace, correlation, phase,
                    process_alive=True, output="synthetic-private-content", raw_before="raw\nlines", retained="retained",
                    normalized="normalized", failure_reason=reason,
                    post_send_zero_timeout_expect_iterations=4, post_send_productive_count=2,
                    post_send_no_progress_count=1, post_send_unknown_count=0,
                    post_send_sleep_requested_ns=2**40, post_send_sleep_actual_ns=7,
                    post_send_loop_elapsed_ns=11, post_send_buffer_progress="no_progress")
        assert trace.wait_for_ingress(2) and trace.flush_durable(2)
        finalized = [json.loads(line) for line in (spool / "breadcrumbs.jsonl").read_text().splitlines()]
        expected = [row for row in finalized if row.get("details", {}).get("schema") == "lftp.status_poll.v1"]
        assert len(expected) == 3
        reader.poll()
        records = [item["capture"] for item in _artifacts(artifact) if item["capture"].get("state") == "record"]
        records = [item for item in records if item["projection"].get("lftp_status_authority", {}).get("schema") == "lftp.status_poll.v1"]
        assert [item["projection"]["numbers"]["version"] for item in records] == [row["version"] for row in expected]
        assert [item["created_ns"] for item in records] == [row["created_ns"] for row in expected]
        for item, row in zip(records, expected):
            facts = item["projection"]["lftp_status_authority"]
            details = row["details"]
            assert facts["poll_correlation"] == correlation
            assert facts["monotonic_time_ns"] == str(2**53 + 7)
            for field in ("failure_reason", "prompt_outcome", "error_class", "raw_before_byte_length_bucket",
                          "raw_before_line_count_bucket", "retained_byte_length_bucket", "normalized_byte_length_bucket"):
                assert facts[field] == details[field]
            assert facts["post_send_metrics"]["post_send_sleep_requested_ns"] == "2147483647"
            assert facts["post_send_metrics"]["post_send_zero_timeout_expect_iterations"] == "4"
            assert facts["post_send_metrics"]["post_send_buffer_progress"] == "no_progress"
        malicious = capture._lftp_status_authority_facts({"schema": "lftp.status_poll.v1",
            "failure_reason": "/private", "prompt_outcome": "secret", "error_class": "raw error",
            "raw_before_byte_length_bucket": "100000", "monotonic_time_ns": True,
            "post_send_metrics": {"post_send_productive_count": "secret", "arbitrary": "private"}},
            "lftp_status_poll", "/private")
        assert malicious.get("poll_correlation") is None and malicious["monotonic_time_ns"] is None
        assert malicious["failure_reason"] == malicious["prompt_outcome"] == malicious["error_class"] == "unknown"
        assert malicious["raw_before_byte_length_bucket"] == "unknown"
        assert malicious["post_send_metrics"]["post_send_productive_count"] is None
        assert "arbitrary" not in malicious["post_send_metrics"]
        assert observer._sanitize_capture_projection({"lftp_status_authority": malicious})["lftp_status_authority"] == malicious
        assert "synthetic-private-content" not in artifact.read_text()
    finally:
        reader.close()
        trace.close()
