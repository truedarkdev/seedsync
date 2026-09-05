# Copyright 2017, Inderpreet Singh, All rights reserved.

import copy
import logging
import time
from abc import ABC, abstractmethod
import multiprocessing
import threading
from collections import deque
from datetime import datetime
from typing import Callable, List, Optional, Protocol
import queue
import uuid
from multiprocessing.queues import Queue as MPQueue
from multiprocessing.synchronize import Event as EventType

from common import AppError
from common.app_process import ExceptionWrapper
from common.breadcrumb_trace import opaque_trace_correlation, trace_session_digest
from common.performance_diagnostics import FixedDurationRecorder, PerformanceDiagnosticsCollector
from system import SystemFile


class _BreadcrumbEmitter(Protocol):
    def is_effectively_enabled(self, category: object, level: object = "info") -> bool: ...

    def record(self, source: str, message: str, details: object = None, **metadata: object) -> str: ...


def _breadcrumb_effectively_enabled(trace: object, category: str, level: str = "info") -> bool:
    """Check the cheap breadcrumb gate before constructing scan evidence."""
    if trace is None:
        return False
    gate = getattr(trace, "is_effectively_enabled", None)
    if callable(gate):
        try:
            return bool(gate(category, level))
        except Exception:
            return False
    enabled = getattr(trace, "is_enabled", None)
    if callable(enabled):
        try:
            return bool(enabled())
        except Exception:
            return False
    return True


class ScannerError(AppError):
    """
    Indicates a scanner error

    Args:
        recoverable: indicates scans can be retried
    """
    def __init__(self,
                 message: str,
                 recoverable: bool = False,
                 files: Optional[List[SystemFile]] = None):
        super().__init__(message)
        self.recoverable = recoverable
        self.files = files


class IScanner(ABC):
    """
    Interface to scan the system.
    This hides the scanning implementation from the scanner process.
    """
    @abstractmethod
    def scan(self) -> List[SystemFile]:
        """Scan system"""
        pass

    @abstractmethod
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        pass

    def pop_malformed_status_only_file_ids(self) -> List[str]:
        return []

    def pop_managed_extract_file_ids(self) -> List[str]:
        return []

    def scanned_path_pair_ids(self) -> set[str | None]:
        """Roots covered by a successful scan; empty roots still count."""
        return {None}

    def failed_path_pair_ids(self) -> set[str | None]:
        """Path pairs whose current generation ended in a recoverable error."""
        return set()

    def set_progress_callback(self, callback: Optional["ScanProgressCallback"]) -> None:
        """Receive bounded manifest/root/completion events during a scan."""
        # Optional for legacy scanners.  Concrete scanners override this hook.
        return None

    def set_accepted_root_fingerprints(self, fingerprints: dict[str | None, dict[str, str]]) -> None:
        """Optional remote-only hint; absence must retain full-stream behavior."""
        return None


ScanProgressCallback = Callable[
    [List[SystemFile], Optional[str], Optional[str], Optional[set[str]], bool, Optional[dict[str, str]]],
    None,
]


_ROOT_SHAPE_TRACE_CATEGORY = "scan.root_shape"
_ROOT_SHAPE_TRACE_SCHEMA = "scan.root_shape.v1"
_ROOT_SHAPE_TRACE_STAGE = "remote_scanner_stream"
_ROOT_SHAPE_TRACE_SIGNATURE_LIMIT = 4096
_ROOT_SHAPE_TRACE_FINGERPRINT_LIMIT = 64


def _build_root_shape_details(
        roots: List[SystemFile], is_final: bool, protocol_mode: str,
        *, scanner_side: str = "unknown", generation: int = 0,
        session_token: object = None, progressive: bool = False,
        phase: str = "unknown",
) -> dict[str, object]:
    """Build the bounded, topology-only root-shape breadcrumb payload."""
    directory_root_count = 0
    direct_child_count = 0
    total_child_count = 0
    max_depth = 0
    shape_tokens: list[str] = []
    pair_ids: set[object] = set()
    unassigned_root_count = 0
    explicit_pair_ids: set[object] = set()
    root_fingerprints: list[str] = []
    for root in roots:
        path_pair_id = getattr(root, "path_pair_id", None)
        pair_ids.add(path_pair_id)
        if path_pair_id is None:
            unassigned_root_count += 1
        else:
            explicit_pair_ids.add(path_pair_id)
        if root.is_dir:
            directory_root_count += 1
        root_tokens: Optional[list[str]] = [] if len(root_fingerprints) < _ROOT_SHAPE_TRACE_FINGERPRINT_LIMIT else None
        stack: list[tuple[SystemFile, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            max_depth = max(max_depth, depth)
            children = list(current.iter_children())
            child_count = len(children)
            total_child_count += child_count
            if depth == 0:
                direct_child_count += child_count
            if len(shape_tokens) < _ROOT_SHAPE_TRACE_SIGNATURE_LIMIT:
                shape_tokens.append("{}:{}".format(int(bool(current.is_dir)), child_count))
            elif len(shape_tokens) == _ROOT_SHAPE_TRACE_SIGNATURE_LIMIT:
                shape_tokens.append("truncated")
            if root_tokens is not None:
                if len(root_tokens) < _ROOT_SHAPE_TRACE_SIGNATURE_LIMIT:
                    root_tokens.append("{}:{}".format(int(bool(current.is_dir)), child_count))
                elif len(root_tokens) == _ROOT_SHAPE_TRACE_SIGNATURE_LIMIT:
                    root_tokens.append("truncated")
            stack.extend((child, depth + 1) for child in reversed(children))
        if root_tokens is not None:
            root_fingerprints.append(opaque_trace_correlation(
                "{}|{}|{}".format(
                    getattr(root, "path_pair_id", None),
                    getattr(root, "name", ""),
                    "|".join(root_tokens),
                ),
            ))
    return {
        "schema": _ROOT_SHAPE_TRACE_SCHEMA,
        "stage": _ROOT_SHAPE_TRACE_STAGE,
        "scanner_side": scanner_side,
        "generation": generation if type(generation) is int else 0,
        "session_digest": trace_session_digest(session_token),
        "progressive": bool(progressive),
        "phase": phase,
        "pair_count": len(pair_ids),
        "unassigned_root_count": unassigned_root_count,
        "explicit_path_pair_count": len(explicit_pair_ids),
        "root_count": len(roots),
        "directory_root_count": directory_root_count,
        "direct_child_count": direct_child_count,
        "total_child_count": total_child_count,
        "max_depth": max_depth,
        "root_shape_digest": opaque_trace_correlation("|".join(shape_tokens)),
        "root_shape_truncated": len(shape_tokens) > _ROOT_SHAPE_TRACE_SIGNATURE_LIMIT,
        "root_fingerprints": sorted(root_fingerprints),
        "root_fingerprint_truncated": len(roots) > _ROOT_SHAPE_TRACE_FINGERPRINT_LIMIT,
        "is_final": is_final,
        "protocol_mode": protocol_mode,
    }


def _record_root_shape_breadcrumb(
        breadcrumb_trace: object, roots: List[SystemFile], is_final: bool,
        protocol_mode: str, source: str, *, scanner_side: str = "unknown",
        generation: int = 0, session_token: object = None,
        progressive: bool = False, phase: str = "unknown", flow_id: object = None,
        extra_details: Optional[dict[str, object]] = None,
) -> None:
    """Record root shape only after the category gate admits the work."""
    if not _breadcrumb_effectively_enabled(breadcrumb_trace, _ROOT_SHAPE_TRACE_CATEGORY, "info"):
        return
    try:
        details = _build_root_shape_details(
            roots, is_final, protocol_mode, scanner_side=scanner_side,
            generation=generation, session_token=session_token,
            progressive=progressive, phase=phase,
        )
        if extra_details:
            details.update(extra_details)
        recorder = getattr(breadcrumb_trace, "record", None)
        if callable(recorder):
            root_shape_digest = details["root_shape_digest"]
            corr_id = opaque_trace_correlation(
                "scan.root_shape|{}|{}".format(source, root_shape_digest),
            )
            recorder(
                source,
                "root_shape",
                details,
                stage=_ROOT_SHAPE_TRACE_STAGE,
                event_type="diagnostic",
                trace_scope="aggregate",
                category=_ROOT_SHAPE_TRACE_CATEGORY,
                level="info",
                corr_id=corr_id,
                flow_id=(opaque_trace_correlation(flow_id) if isinstance(flow_id, str) else None),
            )
    except Exception:
        # Breadcrumb diagnostics must never alter scan behavior.
        return


class ScannerResult:
    """
    Results of a system scan
    """
    def __init__(self,
                 timestamp: datetime,
                 files: List[SystemFile],
                 malformed_status_only_file_ids: Optional[List[str]] = None,
                 managed_extract_file_ids: Optional[List[str]] = None,
                 scanned_path_pair_ids: Optional[set[str | None]] = None,
                 failed: bool = False,
                 error_message: str | None = None,
                 generation: int = 0,
                 is_progress: bool = False,
                 root_names: Optional[set[str]] = None,
                 completed_path_pair_ids: Optional[set[str | None]] = None,
                 is_scan_final: bool = True,
                 unknown_path_pair_ids: Optional[set[str | None]] = None,
                 session_token: Optional[str] = None,
                 is_full_snapshot: bool = False,
                 full_snapshot_path_pair_ids: Optional[set[str | None]] = None,
                 is_targeted_scan: bool = False,
                 unchanged_root_fingerprints: Optional[dict[str, str]] = None,
                 unchanged_root_fingerprints_by_pair: Optional[dict[str | None, dict[str, str]]] = None,
                 duration_aggregates: Optional[dict[str, dict[str, float | int]]] = None,
                 duration_aggregate_token: Optional[tuple[str, int, int]] = None,
                 recoverable_failure_path_pair_ids: Optional[set[str | None]] = None,
                 terminal_failure_path_pair_ids: Optional[set[str | None]] = None):
        self.timestamp = timestamp
        self.files = files
        self.malformed_status_only_file_ids = [] if malformed_status_only_file_ids is None else malformed_status_only_file_ids
        self.managed_extract_file_ids = [] if managed_extract_file_ids is None else managed_extract_file_ids
        self.scanned_path_pair_ids = {None} if scanned_path_pair_ids is None else scanned_path_pair_ids
        self.failed = failed
        self.error_message = error_message
        self.generation = generation
        self.is_progress = is_progress
        self.root_names = root_names
        self.completed_path_pair_ids = set() if completed_path_pair_ids is None else completed_path_pair_ids
        self.is_scan_final = is_scan_final
        self.unknown_path_pair_ids = set() if unknown_path_pair_ids is None else unknown_path_pair_ids
        self.session_token = session_token
        self.is_full_snapshot = is_full_snapshot
        self.full_snapshot_path_pair_ids = set() if full_snapshot_path_pair_ids is None else full_snapshot_path_pair_ids
        self.is_targeted_scan = is_targeted_scan
        self.unchanged_root_fingerprints = {} if unchanged_root_fingerprints is None else unchanged_root_fingerprints
        self.unchanged_root_fingerprints_by_pair = {} if unchanged_root_fingerprints_by_pair is None \
            else unchanged_root_fingerprints_by_pair
        self.duration_aggregates = duration_aggregates
        self.duration_aggregate_token = duration_aggregate_token
        # Appended for positional compatibility with downstream result readers.
        self.recoverable_failure_path_pair_ids = set() if recoverable_failure_path_pair_ids is None \
            else recoverable_failure_path_pair_ids
        # ``None`` preserves legacy direct-result terminal fallback. The
        # progressive accumulator supplies concrete transient evidence.
        self.terminal_failure_path_pair_ids = terminal_failure_path_pair_ids


class _ScannerQueueReleaseMarker:
    """Tiny queue item that lets the feeder release its prior large payload."""
    pass


_PUBLISH_LOCK = threading.Lock()

# A scanner can publish progress while the model updater is consuming it.  A
# consumer that drains until ``queue.Empty`` can therefore starve forever when
# publication replenishes each item it removes.  Keep one updater tick bounded
# while retaining the rest of the queue for the next tick.  This matches the
# coordinator queue capacity and counts every queue item, including release
# markers, toward the budget.
_MAX_SCAN_QUEUE_ITEMS_PER_POP = 128


def _is_authoritative_scan_result(item: object) -> bool:
    return isinstance(item, ScannerResult) and (item.is_scan_final or item.is_full_snapshot)


def _is_progress_completion_result(item: object) -> bool:
    return isinstance(item, ScannerResult) and item.is_progress and bool(item.completed_path_pair_ids) \
        and not _is_authoritative_scan_result(item)


def _is_protected_scan_result(item: object) -> bool:
    """Return whether an item must survive ordinary bounded progress loss."""
    return _is_authoritative_scan_result(item) or _is_progress_completion_result(item)


def _scan_result_pair_ids(item: object) -> set[str | None]:
    if not isinstance(item, ScannerResult):
        return set()
    return set(item.scanned_path_pair_ids) | set(item.completed_path_pair_ids) \
        | set(item.full_snapshot_path_pair_ids) | set(item.unknown_path_pair_ids)


def _is_failure_scan_result(item: object) -> bool:
    return isinstance(item, ScannerResult) and (item.failed or bool(item.unknown_path_pair_ids))


def _replacement_index(retained: list[object], result: ScannerResult) -> Optional[int]:
    """Choose a bounded eviction without discarding unrelated failure state first."""
    ordinary = next((
        index for index, item in enumerate(retained)
        if not _is_protected_scan_result(item)
    ), None)
    if ordinary is not None:
        return ordinary

    incoming_pairs = _scan_result_pair_ids(result)
    incoming_generation = result.generation
    same_pair = [
        index for index, item in enumerate(retained)
        if isinstance(item, ScannerResult)
        and bool(_scan_result_pair_ids(item) & incoming_pairs)
        and item.generation <= incoming_generation
    ]
    same_completion = next((
        index for index in same_pair if _is_progress_completion_result(retained[index])
    ), None)
    if same_completion is not None:
        return same_completion
    if _is_progress_completion_result(result):
        return None
    if not _is_authoritative_scan_result(result):
        return None
    same_healthy = next((
        index for index in same_pair if not _is_failure_scan_result(retained[index])
    ), None)
    if same_healthy is not None:
        return same_healthy
    same_failure = next((
        index for index in same_pair if _is_failure_scan_result(retained[index])
    ), None)
    if same_failure is not None:
        return same_failure
    unrelated_completion = next((
        index for index, item in enumerate(retained)
        if _is_progress_completion_result(item)
    ), None)
    if unrelated_completion is not None:
        return unrelated_completion
    unrelated_healthy = next((
        index for index, item in enumerate(retained)
        if not _is_failure_scan_result(item)
    ), None)
    if unrelated_healthy is not None:
        return unrelated_healthy
    # Every remaining item is an unrelated failure/unknown record. Capacity
    # is finite, so retain newest arrivals while callers still receive the
    # bounded queue's newest authoritative evidence.
    return 0


def _publish_bounded_result(output_queue: object,
                            result: ScannerResult) -> None:
    """Publish without blocking forever when a scan outruns its consumer.

    Ordinary root progress is deliberately lossy at the queue boundary. Pair
    completion markers and final/full snapshots are retained: completion is
    useful progress metadata for every scanned pair, while the accumulator
    separately requires the lossless full snapshot before treating it as
    authority for absence or completion.
    """
    with _PUBLISH_LOCK:
        while True:
            try:
                put_nowait = getattr(output_queue, "put_nowait")
                put_nowait(result)
                return
            except queue.Full:
                retained: list[object] = []
                try:
                    while True:
                        retained.append(output_queue.get_nowait())
                except queue.Empty:
                    pass
                except (OSError, EOFError, ValueError):
                    return
                if not retained:
                    # A consumer can drain the queue between the failed put
                    # and this producer's non-blocking drain. Retry the
                    # direct put instead of treating an empty protected set
                    # as evictable state.
                    continue
                drop_index = _replacement_index(retained, result)
                if drop_index is None:
                    # An ordinary root batch, or a completion for a new pair
                    # when every slot is protected, is safely lossy.
                    for item in retained:
                        try:
                            output_queue.put_nowait(item)
                        except (queue.Full, OSError, EOFError, ValueError):
                            return
                    return
                retained.pop(drop_index)
                for item in retained:
                    try:
                        output_queue.put_nowait(item)
                    except (queue.Full, OSError, EOFError, ValueError):
                        return

            except (OSError, EOFError, ValueError):
                return


def _scanner_side(scanner: IScanner) -> str:
    scanner_name = scanner.__class__.__name__.lower()
    return "active" if "active" in scanner_name else (
        "local" if "local" in scanner_name else ("remote" if "remote" in scanner_name else "unknown")
    )


def _failed_scan_path_pair_ids(scanner: IScanner,
                               scan_target_path_pair_ids: Optional[set[str]]) -> set[str | None]:
    """Return authoritative scope for a recoverable scan failure.

    A targeted generation's selected IDs are known at the process boundary,
    even when a legacy scanner leaves its failure scope at the default
    ``{None}``. Preserve explicit string IDs from scanners that can report a
    narrower subset; use the selected target set only when no such IDs exist.
    Full scans retain the unscoped ``None`` marker for existing reconciliation
    behavior.
    """
    failed_ids = getattr(scanner, "failed_path_pair_ids", scanner.scanned_path_pair_ids)()
    explicit_failed_ids = {
        path_pair_id for path_pair_id in failed_ids
        if isinstance(path_pair_id, str)
    }
    if explicit_failed_ids:
        return explicit_failed_ids
    if scan_target_path_pair_ids:
        return set(scan_target_path_pair_ids)
    return {None}


def _scanner_correlation(scanner: IScanner, session_token: str, generation: int) -> str:
    """Join parent and worker scan events without exposing scan identity."""
    return "{}:{}:{}".format(_scanner_side(scanner), trace_session_digest(session_token), generation)


def _record_scan_breadcrumb(scanner: IScanner, breadcrumb_trace: Optional[_BreadcrumbEmitter], flow_id: str,
                            message: str,
                            details: dict[str, object] | Callable[[], dict[str, object]],
                            event_type: str = "state_transition") -> None:
    if not _breadcrumb_effectively_enabled(breadcrumb_trace, "scanner_process", "info"):
        return
    if callable(details):
        details = details()
    scanner_side = _scanner_side(scanner)
    correlation = "{}:{}:{}".format(
        scanner_side, details.get("session_digest", ""), details.get("generation", 0),
    )
    breadcrumb_trace.record("scanner_process", message, details, stage="scan", event_type=event_type,
                            corr_id=correlation, flow_id=opaque_trace_correlation(flow_id),
                            trace_scope="flow", scanner_side=scanner_side,
                            category="scanner_process", level="info")


def _run_scanner_once(scanner: IScanner, output_queue: Optional[object],
                      scan_target_path_pair_ids: Optional[set[str]], control_connection: object,
                      breadcrumb_trace: Optional[_BreadcrumbEmitter], flow_id: str,
                      mp_log_queue: Optional[MPQueue[logging.LogRecord]], mp_log_level: Optional[int],
                      generation: int = 0, session_token: str = "",
                      result_via_control: bool = False,
                      performance_diagnostics_enabled: bool = False,
                      performance_diagnostics_generation: int = 0) -> None:
    """Run one scan in a spawn-context process, without a nested forkserver."""
    logger = logging.getLogger("{}ScanRun".format(scanner.__class__.__name__))
    if mp_log_queue is not None:
        from logging.handlers import QueueHandler

        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)
        root_logger.addHandler(QueueHandler(mp_log_queue))
        if mp_log_level is not None:
            root_logger.setLevel(mp_log_level)
        logger = root_logger.getChild("{}ScanRun".format(scanner.__class__.__name__))

    setter = getattr(scanner, "set_scan_target_path_pair_ids", None)
    breadcrumb_setter = getattr(scanner, "set_breadcrumb_trace", None)
    duration_recorder = FixedDurationRecorder(performance_diagnostics_enabled)
    diagnostics_setter = getattr(scanner, "set_performance_diagnostics", None)
    if callable(diagnostics_setter):
        diagnostics_setter(duration_recorder)
    progress_emitted = False
    progress_files_by_pair: dict[Optional[str], list[SystemFile]] = {}
    unchanged_root_fingerprints_by_pair: dict[Optional[str], dict[str, str]] = {}
    control_send_lock = threading.Lock()

    def send_control_message(message: object) -> None:
        # MultiPathRemoteScanner invokes progress callbacks from worker
        # threads.  multiprocessing.Connection does not guarantee framing
        # when concurrent writers share one endpoint, so serialize every
        # progress/final/status send in the child.
        with control_send_lock:
            control_connection.send(message)

    def publish_progress(files: List[SystemFile], path_pair_id: Optional[str], path_pair_name: Optional[str],
                         root_names: Optional[set[str]], complete: bool,
                         unchanged_root_fingerprints: Optional[dict[str, str]] = None) -> None:
        nonlocal progress_emitted
        progress_emitted = True
        for system_file in files:
            system_file.path_pair_id = path_pair_id
            system_file.path_pair_name = path_pair_name
        if _scanner_side(scanner) in ("local", "remote") and files:
            _record_root_shape_breadcrumb(
                breadcrumb_trace, files, complete, "unknown", "scanner_process",
                scanner_side=_scanner_side(scanner), generation=generation,
                session_token=session_token, progressive=True,
                phase="scanner_process_progress", flow_id=flow_id,
            )
        progress_files_by_pair.setdefault(path_pair_id, []).extend(files)
        if unchanged_root_fingerprints:
            unchanged_root_fingerprints_by_pair.setdefault(path_pair_id, {}).update(unchanged_root_fingerprints)
        progress_result = ScannerResult(
            datetime.now(),
            files,
            scanned_path_pair_ids={path_pair_id},
            generation=generation,
            is_progress=True,
            root_names=None if root_names is None else set(root_names),
            completed_path_pair_ids={path_pair_id} if complete else set(),
            is_scan_final=False,
            session_token=session_token,
            unchanged_root_fingerprints=unchanged_root_fingerprints,
            unchanged_root_fingerprints_by_pair=({path_pair_id: unchanged_root_fingerprints}
                                                 if unchanged_root_fingerprints else None),
        )
        if result_via_control:
            send_control_message(("result", progress_result))
        elif output_queue is not None:
            _publish_bounded_result(output_queue, progress_result)
        if complete:
            pair_snapshot = ScannerResult(
                datetime.now(),
                list(progress_files_by_pair.get(path_pair_id, [])),
                scanned_path_pair_ids={path_pair_id},
                generation=generation,
                is_progress=True,
                completed_path_pair_ids={path_pair_id},
                is_full_snapshot=True,
                full_snapshot_path_pair_ids={path_pair_id},
                session_token=session_token,
                unchanged_root_fingerprints=unchanged_root_fingerprints_by_pair.get(path_pair_id),
                unchanged_root_fingerprints_by_pair=unchanged_root_fingerprints_by_pair,
            )
            if result_via_control:
                send_control_message(("result", pair_snapshot))
            elif output_queue is not None:
                _publish_bounded_result(output_queue, pair_snapshot)

    outcome: tuple[str, object | None] = ("success", None)
    try:
        scanner.set_base_logger(logger)
        if callable(breadcrumb_setter):
            breadcrumb_setter(breadcrumb_trace)
        scanner.set_progress_callback(publish_progress)
        if callable(setter):
            setter(scan_target_path_pair_ids)
        timestamp = datetime.now()
        try:
            files = scanner.scan()
            malformed = scanner.pop_malformed_status_only_file_ids()
            managed = scanner.pop_managed_extract_file_ids()
            scanned_ids = scanner.scanned_path_pair_ids()
            result = ScannerResult(
                timestamp,
                files,
                malformed,
                managed,
                scanned_ids,
                generation=generation,
                is_progress=progress_emitted,
                completed_path_pair_ids=scanned_ids if progress_emitted else set(),
                is_full_snapshot=progress_emitted,
                full_snapshot_path_pair_ids=scanned_ids if progress_emitted else set(),
                is_targeted_scan=scan_target_path_pair_ids is not None,
                session_token=session_token,
                unchanged_root_fingerprints_by_pair=unchanged_root_fingerprints_by_pair,
                duration_aggregates=duration_recorder.snapshot(),
                duration_aggregate_token=(session_token, generation, performance_diagnostics_generation),
            )
            if _scanner_side(scanner) in ("local", "remote"):
                _record_root_shape_breadcrumb(
                    breadcrumb_trace, files, True, "unknown", "scanner_process",
                    scanner_side=_scanner_side(scanner), generation=generation,
                    session_token=session_token, progressive=progress_emitted,
                    phase="scanner_process_return", flow_id=flow_id,
                )
            _record_scan_breadcrumb(scanner, breadcrumb_trace, flow_id, "scan_completed",
                                    lambda: {"file_count": len(files),
                                             "targeted": scan_target_path_pair_ids is not None,
                                             "progress": progress_emitted, "failed": False,
                                             "generation": generation,
                                             "session_digest": trace_session_digest(session_token),
                                             "monotonic_ms": int(time.monotonic_ns() / 1_000_000)})
        except ScannerError as error:
            if not error.recoverable:
                _record_scan_breadcrumb(scanner, breadcrumb_trace, flow_id, "scan_failed",
                                        lambda: {"failed": True, "generation": generation,
                                                 "session_digest": trace_session_digest(session_token),
                                                 "monotonic_ms": int(time.monotonic_ns() / 1_000_000)}, "failure")
                raise
            files = error.files if error.files is not None else []
            malformed = scanner.pop_malformed_status_only_file_ids()
            managed = scanner.pop_managed_extract_file_ids()
            failed_ids = _failed_scan_path_pair_ids(scanner, scan_target_path_pair_ids)
            result = ScannerResult(
                timestamp,
                [] if progress_emitted else files,
                malformed,
                managed,
                failed_ids,
                failed=True,
                error_message=str(error),
                generation=generation,
                # A setup failure can happen before the first stream batch.
                # Treat its affected IDs as a progressive event so the
                # reconciler marks them unknown and preserves last-good data.
                is_progress=progress_emitted or bool(failed_ids),
                unknown_path_pair_ids=failed_ids,
                is_targeted_scan=scan_target_path_pair_ids is not None,
                session_token=session_token,
                duration_aggregates=duration_recorder.snapshot(),
                duration_aggregate_token=(session_token, generation, performance_diagnostics_generation),
                recoverable_failure_path_pair_ids=failed_ids or {None},
            )
            outcome = ("recoverable", str(error))
            _record_scan_breadcrumb(scanner, breadcrumb_trace, flow_id, "scan_failed",
                                    lambda: {"file_count": len(files), "failed": True,
                                             "targeted": scan_target_path_pair_ids is not None,
                                             "generation": generation,
                                             "session_digest": trace_session_digest(session_token),
                                             "monotonic_ms": int(time.monotonic_ns() / 1_000_000)}, "failure")
        if result_via_control:
            # A spawn child must not leave a multiprocessing.Queue feeder
            # thread behind.  Send the authoritative aggregate over the
            # control pipe; the coordinator drains it while the worker is
            # still alive and publishes it to its bounded local queue.
            send_control_message(("result", result))
        elif output_queue is not None:
            _publish_bounded_result(output_queue, result)
        _record_scan_breadcrumb(scanner, breadcrumb_trace, flow_id, "scan_result_published", lambda: {
            "targeted": scan_target_path_pair_ids is not None,
            "final": bool(result.is_scan_final), "full": bool(result.is_full_snapshot),
            "progress": bool(result.is_progress), "failed": bool(result.failed),
            "scanned_pair_count": len(result.scanned_path_pair_ids),
            "completed_pair_count": len(result.completed_path_pair_ids),
            "unknown_pair_count": len(result.unknown_path_pair_ids),
            "root_count": sum(len(files) for files in progress_files_by_pair.values()),
            "file_count": len(result.files), "generation": generation,
            "session_digest": trace_session_digest(session_token),
            "monotonic_ms": int(time.monotonic_ns() / 1_000_000),
        })
        # The parent-facing queue/pipe now owns the result; keep no completed
        # scan graph in the coordinator or in this child while it exits.
        del result
        del files
        del malformed
        del managed
    except Exception as error:
        logger.debug("Process caught an exception")
        outcome = ("fatal", ExceptionWrapper(error))
    finally:
        scanner.set_progress_callback(None)
        if callable(breadcrumb_setter):
            breadcrumb_setter(None)
        if callable(diagnostics_setter):
            diagnostics_setter(None)
        if callable(setter):
            setter(None)
        recycled_state: object | None = None
        try:
            exporter = getattr(scanner, "export_recycled_state", None)
            if callable(exporter):
                recycled_state = exporter()
        except Exception as error:
            if outcome[0] != "fatal":
                outcome = ("fatal", ExceptionWrapper(error))
        try:
            send_control_message((outcome[0], outcome[1], recycled_state))
        finally:
            control_connection.close()


def _create_scanner_worker(scanner: IScanner, output_queue: Optional[object],
                           scan_target_path_pair_ids: Optional[set[str]], control_connection: object,
                           breadcrumb_trace: Optional[_BreadcrumbEmitter], flow_id: str,
                           mp_log_queue: Optional[MPQueue[logging.LogRecord]], mp_log_level: Optional[int],
                           generation: int = 0, session_token: str = "",
                           performance_diagnostics_enabled: bool = False,
                           performance_diagnostics_generation: int = 0) -> multiprocessing.Process:
    """Use spawn explicitly: coordinators can be forkserver children and threaded."""
    return multiprocessing.get_context("spawn").Process(
        name="{}ScanRun".format(scanner.__class__.__name__),
        target=_run_scanner_once,
        args=(scanner, output_queue, scan_target_path_pair_ids, control_connection, breadcrumb_trace, flow_id,
              mp_log_queue, mp_log_level, generation, session_token, True,
              performance_diagnostics_enabled, performance_diagnostics_generation),
    )


class ScannerProcess:
    """
    Process to scan a file system and publish the result
    """
    def __init__(self,
                 scanner: IScanner, interval_in_ms: int,
                 verbose: bool = True,
                 breadcrumb_trace: Optional[_BreadcrumbEmitter] = None,
                 recycle_scan_worker: bool = False,
                 performance_diagnostics: Optional[PerformanceDiagnosticsCollector] = None,
                 result_available_callback: Optional[Callable[[], None]] = None):
        """
        Create a scanner process
        :param scanner: IScanner implementation
        :param interval_in_ms: Minimum interval (in ms) between results
        """
        self.name = scanner.__class__.__name__
        self.logger = logging.getLogger(self.name)
        # Spawned workers publish over their control pipe.  Keeping the
        # coordinator-facing queue local avoids a second multiprocessing
        # feeder thread and gives the same bounded/drop-oldest semantics to
        # both inline and recycled scans.
        self.__queue: Optional[queue.Queue[ScannerResult | _ScannerQueueReleaseMarker]] = queue.Queue(
            maxsize=128,
        )
        self.__queue_is_multiprocessing = False
        self.__scan_target_queue: Optional[queue.Queue[Optional[str]]] = queue.Queue()
        self.__wake_event: Optional[threading.Event] = threading.Event()
        self.__scanner = scanner
        self.__interval_in_ms = interval_in_ms
        self.__last_recoverable_error_message: Optional[str] = None
        self.verbose = verbose
        self.__breadcrumb_trace = breadcrumb_trace
        self.__recycle_scan_worker = recycle_scan_worker
        self.__performance_diagnostics = performance_diagnostics
        self.__result_available_callback = result_available_callback
        self.__ingested_duration_tokens: deque[tuple[str, int, int]] = deque(maxlen=64)
        self.__scan_worker: Optional[multiprocessing.Process] = None
        self.__scan_worker_started_at: Optional[datetime] = None
        self.__scan_worker_control_connection: object | None = None
        self.__scan_worker_pending_status: object | None = None
        self.__scan_worker_force_pending = False
        self.__scan_worker_target_path_pair_ids: Optional[set[str]] = None
        # The model updater may publish a new accepted-root snapshot while the
        # coordinator is recycling a worker.  Keep it process-owned until a
        # scan admission atomically transfers it to the scanner copied by that
        # worker.  In particular, never mutate a scanner while it is scanning.
        self.__scan_admission_lock = threading.RLock()
        self.__pending_accepted_root_fingerprints: object = {}
        self.__has_pending_accepted_root_fingerprints = False
        self.__priority_interrupt_event = threading.Event()
        self.__priority_target_lock = threading.Lock()
        self.__priority_target_path_pair_ids: set[str] = set()
        self.__priority_requires_full_followup = False
        self.__inline_scan_active = threading.Event()
        self.__inline_scan_target_path_pair_ids: Optional[set[str]] = None
        self.__scan_generation = 0
        self.__session_token = uuid.uuid4().hex
        self.__thread: Optional[threading.Thread] = None
        self.__terminate_event = threading.Event()
        self.__exception: Optional[BaseException] = None
        self.__exception_lock = threading.Lock()
        self._mp_log_queue: Optional[MPQueue[logging.LogRecord]] = None
        self._mp_log_level: Optional[int] = None

    def run_init(self) -> None:
        if not self.__recycle_scan_worker:
            self.__scanner.set_base_logger(self.logger)

    def run_cleanup(self) -> None:
        self.__teardown_scan_worker()

    def run_loop(self) -> None:
        if not self.__recycle_scan_worker:
            self.__run_inline_scan()
            return
        self.__run_recycled_scan()

    @property
    def pid(self) -> Optional[int]:
        thread = self.__thread
        return thread.ident if thread is not None else None

    @property
    def session_token(self) -> str:
        return self.__session_token

    @property
    def generation(self) -> int:
        """Return the generation assigned to the currently active scan work."""
        return self.__scan_generation

    def set_mp_log_queue(self, log_queue: MPQueue[logging.LogRecord], log_level: int) -> None:
        self._mp_log_queue = log_queue
        self._mp_log_level = log_level

    def start(self) -> None:
        if self.__thread is not None:
            raise RuntimeError("Scanner coordinator has already been started")
        self.__thread = threading.Thread(target=self.__thread_main, name=self.name, daemon=True)
        self.__thread.start()

    def terminate(self) -> None:
        self.__terminate_event.set()
        wake_event = self.__wake_event
        if wake_event is not None:
            wake_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        thread = self.__thread
        if thread is not None:
            thread.join(timeout)

    def is_alive(self) -> bool:
        thread = self.__thread
        return thread.is_alive() if thread is not None else False

    def propagate_exception(self) -> None:
        with self.__exception_lock:
            exception = self.__exception
            self.__exception = None
        if exception is not None:
            raise exception

    def has_pending_results(self) -> bool:
        """Return whether this scanner still has unread queue entries."""
        result_queue = self.__queue
        if result_queue is None:
            return False
        with result_queue.mutex:
            return bool(result_queue.queue)

    def __thread_main(self) -> None:
        try:
            self.run_init()
            while not self.__terminate_event.is_set():
                self.run_loop()
        except Exception as error:
            self.logger.debug("Scanner coordinator caught an exception", exc_info=True)
            with self.__exception_lock:
                self.__exception = error
            self.__notify_result_available()
        finally:
            self.run_cleanup()

    def __run_recycled_scan(self) -> None:
        if self.__scan_worker is not None:
            self.__poll_scan_worker()
            return
        timestamp_start = datetime.now()
        if self.verbose:
            self.logger.debug("Running a scan")
        flow_id = "{}:{}".format(self.__scanner.__class__.__name__, int(timestamp_start.timestamp() * 1000))
        priority_target_path_pair_ids = self.__drain_priority_target_path_pair_ids()
        scan_target_path_pair_ids = priority_target_path_pair_ids \
            if priority_target_path_pair_ids else self.__drain_scan_target_path_pair_ids()
        is_priority_targeted = bool(priority_target_path_pair_ids)
        self.__record_breadcrumb(
            "scan_started",
            lambda: {
                "targeted": scan_target_path_pair_ids is not None,
                "scanner_side": _scanner_side(self.__scanner),
                "generation": self.__scan_generation + 1,
                "session_digest": trace_session_digest(self.__session_token),
                "monotonic_ms": int(time.monotonic_ns() / 1_000_000),
            },
            flow_id=flow_id,
        )
        if is_priority_targeted:
            # A priority queued before worker creation is already being
            # honored by this targeted generation; do not interrupt it.
            self.__priority_interrupt_event.clear()
            self.__increment_diagnostic("scan_priority_targeted_runs")
        assert self.__queue is not None
        spawn_context = multiprocessing.get_context("spawn")
        receive_connection, send_connection = spawn_context.Pipe(duplex=False)
        self.__scan_generation += 1
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            diagnostics_enabled, diagnostics_generation = False, 0
        else:
            diagnostics_enabled, diagnostics_generation = diagnostics.duration_worker_state()
        # Keep setting the hint and spawning its consumer in one admission
        # critical section.  A concurrent model update is then deterministically
        # either part of this worker's scanner snapshot or staged for its
        # successor; it cannot replace the scanner state mid-handoff.
        with self.__scan_admission_lock:
            self.__apply_pending_accepted_root_fingerprints()
            worker = _create_scanner_worker(
                self.__scanner, None, scan_target_path_pair_ids, send_connection,
                self.__breadcrumb_trace, flow_id, self._mp_log_queue, self._mp_log_level,
                self.__scan_generation, self.__session_token,
                diagnostics_enabled, diagnostics_generation,
            )
            worker.daemon = True
            worker.start()
        send_connection.close()
        self.__scan_worker = worker
        self.__scan_worker_target_path_pair_ids = scan_target_path_pair_ids
        self.__scan_worker_started_at = timestamp_start
        self.__scan_worker_control_connection = receive_connection
        # A failed-to-start or very small one-shot worker may already have
        # exited.  Surface its outcome now; normal scans remain asynchronous.
        if not worker.is_alive():
            self.__poll_scan_worker()

    def __run_inline_scan(self) -> None:
        timestamp_start = datetime.now()
        if self.verbose:
            self.logger.debug("Running a scan")
        flow_id = "{}:{}".format(self.__scanner.__class__.__name__, int(timestamp_start.timestamp() * 1000))
        priority_target_path_pair_ids = self.__drain_priority_target_path_pair_ids()
        scan_target_path_pair_ids = priority_target_path_pair_ids \
            if priority_target_path_pair_ids else self.__drain_scan_target_path_pair_ids()
        self.__record_breadcrumb("scan_started", lambda: {
            "targeted": scan_target_path_pair_ids is not None,
            "scanner_side": _scanner_side(self.__scanner),
            "generation": self.__scan_generation + 1,
            "session_digest": trace_session_digest(self.__session_token),
            "monotonic_ms": int(time.monotonic_ns() / 1_000_000),
        }, flow_id=flow_id)
        with self.__scan_admission_lock:
            self.__apply_pending_accepted_root_fingerprints()
            setter = getattr(self.__scanner, "set_scan_target_path_pair_ids", None)
            breadcrumb_setter = getattr(self.__scanner, "set_breadcrumb_trace", None)
            if callable(breadcrumb_setter):
                breadcrumb_setter(self.__breadcrumb_trace)
            if callable(setter):
                setter(scan_target_path_pair_ids)
        self.__scan_generation += 1
        progress_emitted = False
        progress_files_by_pair: dict[Optional[str], list[SystemFile]] = {}
        unchanged_root_fingerprints_by_pair: dict[Optional[str], dict[str, str]] = {}

        def publish_progress(files: List[SystemFile], path_pair_id: Optional[str], path_pair_name: Optional[str],
                             root_names: Optional[set[str]], complete: bool,
                             unchanged_root_fingerprints: Optional[dict[str, str]] = None) -> None:
            nonlocal progress_emitted
            progress_emitted = True
            for system_file in files:
                system_file.path_pair_id = path_pair_id
                system_file.path_pair_name = path_pair_name
            progress_files_by_pair.setdefault(path_pair_id, []).extend(files)
            if unchanged_root_fingerprints:
                unchanged_root_fingerprints_by_pair.setdefault(path_pair_id, {}).update(unchanged_root_fingerprints)
            if _scanner_side(self.__scanner) in ("local", "remote") and files:
                _record_root_shape_breadcrumb(
                    self.__breadcrumb_trace, files, complete, "unknown", "scanner_process",
                    scanner_side=_scanner_side(self.__scanner), generation=self.__scan_generation,
                    session_token=self.__session_token, progressive=True,
                    phase="scanner_process_progress", flow_id=flow_id,
                )
            assert self.__queue is not None
            self.__publish_result(ScannerResult(
                datetime.now(), files,
                scanned_path_pair_ids={path_pair_id},
                generation=self.__scan_generation,
                is_progress=True,
                root_names=None if root_names is None else set(root_names),
                completed_path_pair_ids={path_pair_id} if complete else set(),
                is_scan_final=False,
                session_token=self.__session_token,
                unchanged_root_fingerprints=unchanged_root_fingerprints,
                unchanged_root_fingerprints_by_pair=({path_pair_id: unchanged_root_fingerprints}
                                                     if unchanged_root_fingerprints else None),
            ))
            if complete:
                self.__publish_result(ScannerResult(
                    datetime.now(), list(progress_files_by_pair.get(path_pair_id, [])),
                    scanned_path_pair_ids={path_pair_id},
                    generation=self.__scan_generation,
                    is_progress=True,
                    completed_path_pair_ids={path_pair_id},
                    is_full_snapshot=True,
                    full_snapshot_path_pair_ids={path_pair_id},
                    session_token=self.__session_token,
                    unchanged_root_fingerprints=unchanged_root_fingerprints_by_pair.get(path_pair_id),
                    unchanged_root_fingerprints_by_pair=unchanged_root_fingerprints_by_pair,
                ))
        self.__scanner.set_progress_callback(publish_progress)
        self.__inline_scan_target_path_pair_ids = scan_target_path_pair_ids
        self.__inline_scan_active.set()
        try:
            files = self.__scanner.scan()
            malformed = self.__scanner.pop_malformed_status_only_file_ids()
            managed = self.__scanner.pop_managed_extract_file_ids()
            self.__last_recoverable_error_message = None
            result = ScannerResult(timestamp_start, files, malformed, managed,
                                    self.__scanner.scanned_path_pair_ids(), generation=self.__scan_generation,
                                    is_progress=progress_emitted,
                completed_path_pair_ids=self.__scanner.scanned_path_pair_ids()
                                    if progress_emitted else set(),
                                    is_full_snapshot=progress_emitted,
                                    full_snapshot_path_pair_ids=self.__scanner.scanned_path_pair_ids()
                                    if progress_emitted else set(),
                                    is_targeted_scan=scan_target_path_pair_ids is not None,
                                    session_token=self.__session_token,
                                    unchanged_root_fingerprints_by_pair=unchanged_root_fingerprints_by_pair)
            if _scanner_side(self.__scanner) in ("local", "remote"):
                _record_root_shape_breadcrumb(
                    self.__breadcrumb_trace, files, True, "unknown", "scanner_process",
                    scanner_side=_scanner_side(self.__scanner), generation=self.__scan_generation,
                    session_token=self.__session_token, progressive=progress_emitted,
                    phase="scanner_process_return", flow_id=flow_id,
                )
            self.__record_breadcrumb("scan_completed", lambda: {
                    "file_count": len(files), "targeted": scan_target_path_pair_ids is not None,
                    "scanner_side": _scanner_side(self.__scanner),
                    "progress": progress_emitted, "failed": False,
                    "generation": self.__scan_generation,
                    "session_digest": trace_session_digest(self.__session_token),
                    "monotonic_ms": int(time.monotonic_ns() / 1_000_000),
                }, flow_id=flow_id)
        except ScannerError as error:
            if not error.recoverable:
                self.__record_breadcrumb("scan_failed", lambda: {
                    "failed": True, "generation": self.__scan_generation,
                    "scanner_side": _scanner_side(self.__scanner),
                    "session_digest": trace_session_digest(self.__session_token),
                    "monotonic_ms": int(time.monotonic_ns() / 1_000_000),
                }, event_type="failure", flow_id=flow_id)
                raise
            files = error.files if error.files is not None else []
            malformed = self.__scanner.pop_malformed_status_only_file_ids()
            managed = self.__scanner.pop_managed_extract_file_ids()
            error_message = str(error)
            if error_message != self.__last_recoverable_error_message:
                self.logger.warning("Recoverable scanner error; returning failed result: {}".format(error_message))
                self.__last_recoverable_error_message = error_message
            failed_ids = _failed_scan_path_pair_ids(self.__scanner, scan_target_path_pair_ids)
            result = ScannerResult(timestamp_start, [] if progress_emitted else files, malformed, managed,
                                   failed_ids,
                                   failed=True, error_message=error_message,
                                   generation=self.__scan_generation,
                                   is_progress=progress_emitted or bool(failed_ids),
                                   unknown_path_pair_ids=failed_ids,
                                   is_targeted_scan=scan_target_path_pair_ids is not None,
                                   session_token=self.__session_token,
                                   recoverable_failure_path_pair_ids=failed_ids or {None})
            self.__record_breadcrumb("scan_failed", lambda: {
                "file_count": len(files), "failed": True,
                "scanner_side": _scanner_side(self.__scanner),
                "targeted": scan_target_path_pair_ids is not None,
                "generation": self.__scan_generation,
                "session_digest": trace_session_digest(self.__session_token),
                "monotonic_ms": int(time.monotonic_ns() / 1_000_000),
            }, event_type="failure", flow_id=flow_id)
        finally:
            self.__inline_scan_active.clear()
            self.__inline_scan_target_path_pair_ids = None
            self.__scanner.set_progress_callback(None)
            if callable(breadcrumb_setter):
                breadcrumb_setter(None)
            if callable(setter):
                setter(None)
        assert self.__queue is not None
        assert self.__queue is not None
        self.__publish_result(result)
        self.__record_breadcrumb("scan_result_published", lambda: {
            "targeted": scan_target_path_pair_ids is not None,
            "scanner_side": _scanner_side(self.__scanner),
            "final": bool(result.is_scan_final), "full": bool(result.is_full_snapshot),
            "progress": bool(result.is_progress), "failed": bool(result.failed),
            "scanned_pair_count": len(result.scanned_path_pair_ids),
            "completed_pair_count": len(result.completed_path_pair_ids),
            "unknown_pair_count": len(result.unknown_path_pair_ids),
            "root_count": sum(len(files) for files in progress_files_by_pair.values()),
            "file_count": len(result.files), "generation": self.__scan_generation,
            "session_digest": trace_session_digest(self.__session_token),
            "monotonic_ms": int(time.monotonic_ns() / 1_000_000),
        }, flow_id=flow_id)
        # Do not retain the completed graph in this long-lived coordinator.
        del result
        del files
        del malformed
        del managed
        delta_in_ms = int((datetime.now() - timestamp_start).total_seconds() * 1000)
        if self.verbose:
            self.logger.debug("Scan took {:.3f}s".format(float(delta_in_ms) / 1000.0))
        priority_followup_pending = False
        if self.__priority_requires_full_followup and scan_target_path_pair_ids is not None:
            if self.__has_pending_priority_targets():
                priority_followup_pending = True
            else:
                self.__priority_requires_full_followup = False
                assert self.__scan_target_queue is not None
                self.__scan_target_queue.put(None)
                self.__increment_diagnostic("scan_priority_full_followups")
                self.__record_breadcrumb(
                    "scan_priority_full_followup_scheduled",
                    {"scanner": self.__scanner.__class__.__name__},
                )
                priority_followup_pending = True
        if not priority_followup_pending and delta_in_ms < self.__interval_in_ms:
            assert self.__wake_event is not None
            self.__wake_event.wait(timeout=float(self.__interval_in_ms - delta_in_ms) / 1000.0)
            self.__wake_event.clear()

    def __poll_scan_worker(self) -> None:
        worker = self.__scan_worker
        assert worker is not None
        started_at = self.__scan_worker_started_at
        worker.join(timeout=0.05)
        if worker.is_alive():
            status = self.__drain_scan_worker_messages()
            if status is not None:
                # A child can finish its control-pipe sends just before its
                # process object reports dead.  Retain the terminal status
                # until the normal teardown path applies it exactly once.
                self.__scan_worker_pending_status = status
            assert self.__wake_event is not None
            if self.__wake_event.wait(timeout=0.05):
                self.__wake_event.clear()
                self.__scan_worker_force_pending = True
            if self.__priority_interrupt_event.is_set():
                self.__priority_interrupt_event.clear()
                if self.__scan_worker_target_path_pair_ids is None:
                    self.__priority_requires_full_followup = True
                    self.__scan_worker_force_pending = True
                    self.__increment_diagnostic("scan_priority_interrupts")
                    self.__record_breadcrumb(
                        "scan_priority_interrupted_full",
                        {"scanner": self.__scanner.__class__.__name__},
                    )
                    self.__teardown_scan_worker()
            return

        completed_target_path_pair_ids = self.__scan_worker_target_path_pair_ids
        try:
            # The child has exited, so its terminal status must be retained;
            # allow a short pipe-delivery window before tearing the endpoint
            # down (especially important on Windows spawn).
            status = self.__drain_scan_worker_messages(wait_timeout=1.0)
            if status is None:
                status = self.__scan_worker_pending_status
            self.__scan_worker_pending_status = None
            if isinstance(status, tuple) and len(status) >= 3:
                applier = getattr(self.__scanner, "apply_recycled_state", None)
                if callable(applier):
                    applier(status[2])
            if isinstance(status, tuple) and status[0] == "fatal":
                status[1].re_raise()
            error_message = status[1] if isinstance(status, tuple) and status[0] == "recoverable" else None
            if isinstance(error_message, str) and error_message != self.__last_recoverable_error_message:
                self.logger.warning("Recoverable scanner error; returning failed result: {}".format(error_message))
                self.__last_recoverable_error_message = error_message
            elif error_message is None:
                self.__last_recoverable_error_message = None
        finally:
            self.__teardown_scan_worker(terminate=False)

        if self.__priority_requires_full_followup and completed_target_path_pair_ids is not None:
            if not self.__has_pending_priority_targets():
                self.__priority_requires_full_followup = False
                assert self.__scan_target_queue is not None
                self.__scan_target_queue.put(None)
                self.__increment_diagnostic("scan_priority_full_followups")
                self.__record_breadcrumb(
                    "scan_priority_full_followup_scheduled",
                    {"scanner": self.__scanner.__class__.__name__},
                )
            self.__scan_worker_force_pending = True

        delta_in_ms = int((datetime.now() - started_at).total_seconds() * 1000) if started_at is not None else 0
        if self.verbose:
            self.logger.debug("Scan took {:.3f}s".format(float(delta_in_ms) / 1000.0))
        if not self.__scan_worker_force_pending and delta_in_ms < self.__interval_in_ms:
            assert self.__wake_event is not None
            self.__wake_event.wait(timeout=float(self.__interval_in_ms - delta_in_ms) / 1000.0)
            self.__wake_event.clear()
        self.__scan_worker_force_pending = False

    def __drain_scan_worker_messages(self, wait_timeout: float = 0.0) -> object | None:
        """Forward bounded progress and the final aggregate from a child.

        The child sends result envelopes synchronously over the control pipe,
        so it cannot strand a multiprocessing.Queue feeder at interpreter
        shutdown.  The coordinator owns the local bounded queue and may drop
        intermediate progress when the model updater is busy; the final
        aggregate is marked as full/authoritative and replaces stale entries.
        """
        connection = self.__scan_worker_control_connection
        if connection is None:
            return None
        status = None
        first = True
        while True:
            try:
                ready = connection.poll(wait_timeout if first else 0.0)
                first = False
                if not ready:
                    break
                message = connection.recv()
            except (OSError, EOFError):
                break
            if isinstance(message, tuple) and len(message) == 2 and message[0] == "result":
                result = message[1]
                if isinstance(result, ScannerResult):
                    self.__ingest_duration_aggregates(result)
                    assert self.__queue is not None
                    self.__publish_result(result)
            else:
                status = message
        return status

    def __ingest_duration_aggregates(self, result: ScannerResult) -> None:
        diagnostics = self.__performance_diagnostics
        aggregates = result.duration_aggregates
        token = result.duration_aggregate_token
        if diagnostics is None or not isinstance(aggregates, dict) or not isinstance(token, tuple) or len(token) != 3:
            return
        if not isinstance(token[0], str) or type(token[1]) is not int or type(token[2]) is not int:
            return
        if token in self.__ingested_duration_tokens:
            return
        self.__ingested_duration_tokens.append(token)
        for metric, aggregate in aggregates.items():
            if isinstance(metric, str):
                diagnostics.observe_duration_aggregate(metric, aggregate, expected_generation=token[2])

    def __publish_result(self, result: ScannerResult) -> None:
        assert self.__queue is not None
        _publish_bounded_result(self.__queue, result)
        self.__notify_result_available()

    def __notify_result_available(self) -> None:
        callback = self.__result_available_callback
        if callback is None:
            return
        try:
            callback()
        except Exception:
            # A wake hook is advisory and must never break scanning.
            pass

    def __teardown_scan_worker(self, terminate: bool = True) -> None:
        worker = self.__scan_worker
        connection = self.__scan_worker_control_connection
        self.__scan_worker = None
        self.__scan_worker_control_connection = None
        self.__scan_worker_pending_status = None
        self.__scan_worker_started_at = None
        self.__scan_worker_target_path_pair_ids = None
        if connection is not None:
            connection.close()
        if worker is None:
            return
        if worker.pid is not None:
            if terminate and worker.is_alive():
                worker.terminate()
            worker.join(timeout=1)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
        if not worker.is_alive():
            worker.close()

    def close_queues(self) -> None:
        if self.__queue_is_multiprocessing and self.__queue is not None:
            self.__queue.close()
            self.__queue.join_thread()
        self.__queue = None
        self.__queue_is_multiprocessing = False
        self.__scan_target_queue = None
        self.__wake_event = None
        self._mp_log_queue = None
        self._mp_log_level = None

    def __trace_corr_id(self) -> str:
        return opaque_trace_correlation(
            self.__trace_path_pair_id() or self.__scanner.__class__.__name__
        )

    def __trace_path_pair_id(self) -> Optional[str]:
        path_pair_id = getattr(self.__scanner, "path_pair_id", None)
        return path_pair_id if isinstance(path_pair_id, str) else None

    def __trace_path_pair_name(self) -> Optional[str]:
        path_pair_name = getattr(self.__scanner, "path_pair_name", None)
        return path_pair_name if isinstance(path_pair_name, str) else None

    def __record_breadcrumb(self, message: str,
                            details: dict[str, object] | Callable[[], dict[str, object]],
                            event_type: str = "state_transition",
                            corr_id: str | None = None, flow_id: str | None = None,
                            path_pair_id: str | None = None, path_pair_name: str | None = None) -> None:
        if not _breadcrumb_effectively_enabled(self.__breadcrumb_trace, "scanner_process", "info"):
            return
        if callable(details):
            details = details()
        self.__breadcrumb_trace.record(
            "scanner_process",
            message,
            details,
            stage="scan",
            event_type=event_type,
            corr_id=corr_id if corr_id is not None else (
                "{}:{}:{}".format(
                    details.get("scanner_side", _scanner_side(self.__scanner)),
                    details.get("session_digest", trace_session_digest(self.__session_token)),
                    details["generation"],
                ) if "generation" in details else self.__trace_corr_id()
            ),
            flow_id=opaque_trace_correlation(flow_id) if flow_id is not None else None,
            category="scanner_process",
            level="info",
            )

    def pop_latest_result(self, max_items: int = _MAX_SCAN_QUEUE_ITEMS_PER_POP) -> Optional[ScannerResult]:
        """
        Process-safe method to retrieve latest scan result
        Returns None if no new scan result was generated since the last time
        this method was called.  At most ``max_items`` queue entries are
        consumed; entries left behind are observed by a later tick.
        :return:
        """
        if max_items <= 0:
            return None
        latest_scan = None
        for _ in range(max_items):
            try:
                assert self.__queue is not None
                item = self.__queue.get(block=False)
                if isinstance(item, _ScannerQueueReleaseMarker):
                    continue
                latest_scan = item
            except queue.Empty:
                break
            except (OSError, EOFError) as exc:
                self.logger.warning("Scanner queue read failed: {}".format(exc))
                return latest_scan
        return latest_scan

    def pop_results(self, max_items: int = _MAX_SCAN_QUEUE_ITEMS_PER_POP) -> List[ScannerResult]:
        """Drain queued scan events in publication order.

        ``pop_latest_result`` remains for legacy callers that intentionally
        coalesce snapshots.  Progressive reconciliation uses this bounded
        drain so a manifest and root batches cannot be dropped between ticks;
        entries remaining after ``max_items`` are consumed by a later tick.
        """
        if max_items <= 0:
            return []
        results: List[ScannerResult] = []
        for _ in range(max_items):
            try:
                assert self.__queue is not None
                item = self.__queue.get(block=False)
                if isinstance(item, _ScannerQueueReleaseMarker):
                    continue
                if isinstance(item, ScannerResult):
                    results.append(item)
            except queue.Empty:
                break
            except (OSError, EOFError) as exc:
                self.logger.warning("Scanner queue read failed: {}".format(exc))
                break
        return results

    def force_scan(self, path_pair_id: Optional[str] = None) -> None:
        """Force process to wake and do an immediate scan"""
        target_queue = self.__scan_target_queue
        assert target_queue is not None
        # Coalesce force requests while a scan is active.  The queue is only
        # used by this coordinator thread, but its mutex gives the producer
        # side an atomic inspect/merge boundary with the drain at scan start.
        with target_queue.mutex:
            pending = list(target_queue.queue)
            if path_pair_id is None:
                if None not in pending:
                    target_queue.queue.clear()
                    target_queue.unfinished_tasks = 0
                    target_queue._put(None)
                    target_queue.unfinished_tasks += 1
                    target_queue.not_empty.notify()
            elif None not in pending and path_pair_id not in pending:
                target_queue._put(path_pair_id)
                target_queue.unfinished_tasks += 1
                target_queue.not_empty.notify()
        assert self.__wake_event is not None
        self.__wake_event.set()

    def set_accepted_root_fingerprints(self, fingerprints: object) -> None:
        """Stage model-owned accepted root digests for the next scan admission."""
        with self.__scan_admission_lock:
            # The accepted map is model-owned.  Copy it at the process boundary
            # so a later accumulator update cannot alter the snapshot already
            # selected for a worker.  Preserve unusual legacy inputs if they
            # cannot be copied; concrete remote scanners normally receive dicts.
            try:
                self.__pending_accepted_root_fingerprints = copy.deepcopy(fingerprints)
            except Exception:
                self.__pending_accepted_root_fingerprints = fingerprints
            self.__has_pending_accepted_root_fingerprints = True

    def __apply_pending_accepted_root_fingerprints(self) -> None:
        """Transfer the staged hint while the next scan is being admitted."""
        if not self.__has_pending_accepted_root_fingerprints:
            return
        setter = getattr(self.__scanner, "set_accepted_root_fingerprints", None)
        if callable(setter):
            setter(self.__pending_accepted_root_fingerprints)

    def prioritize_scan(self, path_pair_id: str) -> None:
        """Move one selected pair ahead of ordinary full-scan work."""
        if not isinstance(path_pair_id, str) or not path_pair_id:
            return
        if not self.__recycle_scan_worker:
            self.__increment_diagnostic("scan_priority_requests")
            self.__record_breadcrumb(
                "scan_priority_requested",
                {"scanner": self.__scanner.__class__.__name__},
                path_pair_id=path_pair_id,
            )
            if self.__inline_scan_active.is_set():
                active_targets = self.__inline_scan_target_path_pair_ids
                if active_targets is not None and path_pair_id in active_targets:
                    return
                # An active full scan has no safe in-place target boundary.
                # Queue the pair below so the coordinator admits it as a
                # successor generation after the current scan publishes.
            with self.__priority_target_lock:
                self.__priority_target_path_pair_ids.add(path_pair_id)
            if self.__scan_generation == 0:
                self.__priority_requires_full_followup = True
            assert self.__wake_event is not None
            self.__wake_event.set()
            return
        with self.__priority_target_lock:
            active_targets = self.__scan_worker_target_path_pair_ids
            if active_targets is not None and path_pair_id in active_targets:
                return
            self.__priority_target_path_pair_ids.add(path_pair_id)
        if self.__scan_generation == 0:
            self.__priority_requires_full_followup = True
        self.__increment_diagnostic("scan_priority_requests")
        self.__record_breadcrumb(
            "scan_priority_requested",
            {"scanner": self.__scanner.__class__.__name__},
            path_pair_id=path_pair_id,
        )
        self.__priority_interrupt_event.set()
        assert self.__wake_event is not None
        self.__wake_event.set()

    def __drain_priority_target_path_pair_ids(self) -> set[str]:
        with self.__priority_target_lock:
            path_pair_ids = set(self.__priority_target_path_pair_ids)
            self.__priority_target_path_pair_ids.clear()
        return path_pair_ids

    def __has_pending_priority_targets(self) -> bool:
        with self.__priority_target_lock:
            return bool(self.__priority_target_path_pair_ids)

    def __increment_diagnostic(self, counter: str) -> None:
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            return
        try:
            diagnostics.increment(counter)
        except Exception:
            pass

    def __drain_scan_target_path_pair_ids(self) -> Optional[set[str]]:
        scan_target_path_pair_ids: set[str] = set()
        full_scan_requested = False
        while True:
            try:
                assert self.__scan_target_queue is not None
                scan_target_path_pair_id = self.__scan_target_queue.get(block=False)
            except queue.Empty:
                break
            except (OSError, EOFError) as exc:
                self.logger.warning("Scanner target queue read failed: {}".format(exc))
                break
            if scan_target_path_pair_id is None:
                full_scan_requested = True
            elif not full_scan_requested:
                scan_target_path_pair_ids.add(scan_target_path_pair_id)
        if full_scan_requested:
            return None
        if not scan_target_path_pair_ids:
            return None
        return scan_target_path_pair_ids
