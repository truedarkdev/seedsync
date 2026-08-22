# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
import logging
from datetime import datetime
from collections import OrderedDict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple, cast
import math
import json
import time

# my libs
from system import SystemFile
from lftp import LftpJobStatus
from model import ModelFile, Model, ModelError
from common.breadcrumb_trace import BreadcrumbTraceEmitter, opaque_trace_correlation
from common.performance_diagnostics import (
    COUNTER_PAIR_SAFETY_REJECT_CROSS_PAIR_TOUCH,
    COUNTER_PAIR_SAFETY_REJECT_DIRTY_INPUT,
    COUNTER_PAIR_SAFETY_REJECT_EXTRACTED_BARE_MARKER,
    COUNTER_PAIR_SAFETY_REJECT_LOCAL_ROOT_ARBITRATION,
    COUNTER_PAIR_SAFETY_REJECT_STATUS_ONLY_NAME,
    COUNTER_PAIR_SAFETY_REJECT_ACTIVE_ONLY_NAME,
    COUNTER_PAIR_SAFETY_REJECT_ORPHAN_ACTIVE,
    COUNTER_PAIR_SAFETY_REJECT_ORPHAN_STATUS,
    COUNTER_PAIR_SAFETY_REJECT_UNKNOWN_AUTHORITY,
    DURATION_MODEL_BUILDER_SET_ACTIVE_FILES,
    DURATION_MODEL_BUILDER_SET_LFTP_STATUSES,
    DURATION_MODEL_BUILDER_SET_LOCAL_FILES,
    DURATION_MODEL_BUILDER_SET_REMOTE_FILES,
    DURATION_MODEL_BUILDER_SET_STOPPED_FILES,
    MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
    MODEL_BUILDER_INVALIDATION_CONFIRMED_LOCAL_DELETIONS,
    MODEL_BUILDER_INVALIDATION_CLEAR,
    MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
    MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
    MODEL_BUILDER_INVALIDATION_EXPLICIT,
    MODEL_BUILDER_INVALIDATION_EXTRACTED_FILES,
    MODEL_BUILDER_INVALIDATION_EXTRACT_STATUSES,
    MODEL_BUILDER_INVALIDATION_FINAL_MOVE_SUCCEEDED_FILES,
    MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
    MODEL_BUILDER_INVALIDATION_LOCAL_FILES,
    MODEL_BUILDER_INVALIDATION_LOCAL_ROOT_PATHS,
    MODEL_BUILDER_INVALIDATION_MOVE_FAILED_FILES,
    MODEL_BUILDER_INVALIDATION_REMOTE_FILES,
    MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
    MODEL_BUILDER_INVALIDATION_UNKNOWN_LOCAL_PAIRS,
    MODEL_BUILDER_INVALIDATION_VALIDATION_STATUSES,
)
from .extract import ExtractStatus, Extract
from .validate import ValidateStatus


def _breadcrumb_effectively_enabled(
        breadcrumb: object, category: str, level: str = "info",
) -> bool:
    """Check the complete breadcrumb gate before diagnostic-only work.

    Keep legacy test doubles that only implement ``is_enabled`` working while
    treating record-only fakes as enabled and unconfigured mocks as disabled.
    """
    if breadcrumb is None:
        return False
    effective = getattr(breadcrumb, "is_effectively_enabled", None)
    if callable(effective):
        try:
            result = effective(category, level)
            if isinstance(result, bool):
                return result
        except Exception:
            pass
    enabled = getattr(breadcrumb, "is_enabled", None)
    if not callable(enabled):
        return True
    try:
        result = enabled()
        return result if isinstance(result, bool) else False
    except Exception:
        return False


@dataclass
class _RecentLiveTransferSnapshot:
    root_file_id: str
    size_local: Optional[int]
    percent_local: Optional[int]
    speed: Optional[int]
    eta: Optional[int]
    lftp_job_id: Optional[int] = None
    subset_size_local: Optional[int] = None
    subset_size_remote: Optional[int] = None


class _TransferState(NamedTuple):
    size_local: Optional[int]
    size_remote: Optional[int]
    percent_local: Optional[int | float]
    speed: Optional[int]
    eta: Optional[int]


@dataclass
class _BuiltRootFile:
    model_file: ModelFile
    normalized_local_root_path: Optional[str]
    is_local_only: bool
    seen_file_ids: set[str]


@dataclass
class _ActiveTransferRootBuild:
    """Isolated render and runtime state for an active-transfer root delta."""
    model: Optional[Model]
    recent_live_transfer_snapshots: dict[str, _RecentLiveTransferSnapshot]
    retained_stopped_transfer_snapshots: dict[str, _RecentLiveTransferSnapshot]


@dataclass
class _AuthoritativePairBuild:
    """Staged replacement for one fully-covered scan pair."""
    path_pair_id: Optional[str]
    previous_local_files: dict[str, SystemFile]
    previous_remote_files: dict[str, SystemFile]
    local_files: dict[str, SystemFile]
    remote_files: dict[str, SystemFile]
    model: Optional[Model]
    unknown_local_path_pair_ids: set[Optional[str]]
    unresolved_staging_collision_file_ids: set[str]
    terminalizable_staging_collision_file_ids: set[str]
    recent_live_transfer_snapshots: dict[str, _RecentLiveTransferSnapshot]
    retained_stopped_transfer_snapshots: dict[str, _RecentLiveTransferSnapshot]
    complete_local_coverage_file_ids: set[str]
    invalidation_tokens: frozenset[int]
    downloaded_timestamp_overlay_generation: int


@dataclass(frozen=True)
class _LocalLibraryInventory:
    """One bounded, scan-owned local-library aggregate for a path pair."""
    file_count: Optional[int]
    size: Optional[int]
    state: str


class ModelBuilder:
    """
    ModelBuilder combines all the difference sources of file system info
    to build a model. These sources include:
      * downloading file system as a Dict[name, SystemFile]
      * local file system as a Dict[name, SystemFile]
      * remote file system as a Dict[name, SystemFile]
      * lftp status as Dict[name, LftpJobStatus]
    """
    # Keep diagnostic dedupe state bounded independently from the breadcrumb
    # window.  A transfer workload can churn through many canonical file ids;
    # the oldest signatures are evicted deterministically once this cap is hit.
    # A production-shaped model can legitimately emit several lifecycle
    # stages for each retained subject. Keep enough signatures for the whole
    # bounded breadcrumb window so stable subjects do not churn the cache and
    # evict the rare transition being diagnosed.
    __STOP_RESUME_TRACE_SIGNATURE_CACHE_SIZE = 1024

    __QUEUE_EXCLUSION_TRACE_SCHEMA = "fractional_mtime_redownload.queue_exclusion.v2"
    __MODEL_PRESENTATION_TRACE_SCHEMA = "model_builder.model_presentation.v1"
    __MODEL_PRESENTATION_TRACE_COUNT_MAX = 256

    def __init__(self):
        self.logger = logging.getLogger("ModelBuilder")
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        self.__target_archive_trace_file_id = os.environ.get("SEEDSYNC_TARGET_ARCHIVE_TRACE_FILE_ID")
        if self.__target_archive_trace_file_id is not None and not self.__target_archive_trace_file_id.strip():
            self.__target_archive_trace_file_id = None
        # Scan authority is partitioned by path pair.  The previous flat maps
        # made an authoritative final scan of one pair walk every unrelated
        # pair merely to discover removals.  These are the only local/remote
        # source maps; full reconciliation deliberately flattens them at its
        # existing global-build boundary.
        self.__local_files_by_pair: dict[Optional[str], dict[str, SystemFile]] = {}
        # This is derived while authoritative local source trees are replaced.
        # It deliberately never participates in model rendering or tree
        # traversal on the web-summary path.
        self.__local_library_inventory_by_pair: dict[Optional[str], _LocalLibraryInventory] = {}
        self.__local_library_inventory_revision = 0
        self.__active_files: dict[str, SystemFile] = {}
        self.__remote_files_by_pair: dict[Optional[str], dict[str, SystemFile]] = {}
        self.__active_file_ids: set[str] = set()
        self.__lftp_statuses: dict[str, LftpJobStatus] = {}
        self.__recent_live_transfer_snapshots: dict[str, _RecentLiveTransferSnapshot] = {}
        self.__retained_stopped_transfer_snapshots: dict[str, _RecentLiveTransferSnapshot] = {}
        self.__downloaded_files: Optional[set[str]] = None
        self.__downloaded_timestamps: dict[str, float] = {}
        # One builder-owned revision identifies all roots rendered from the
        # current persisted timestamp overlay.
        self.__downloaded_timestamp_overlay_generation = 0
        self.__extract_statuses: dict[str, ExtractStatus] = {}
        self.__extracted_files: set[str] = set()
        self.__stopped_files: set[str] = set()
        self.__validation_statuses: dict[str, ValidateStatus] = {}
        self.__move_failed_files: set[str] = set()
        self.__final_move_succeeded_files: set[str] = set()
        self.__unknown_local_path_pair_ids: set[Optional[str]] = set()
        self.__local_root_paths: dict[Optional[str], str] = {}
        self.__local_staging_paths: dict[Optional[str], str] = {}
        self.__suppressed_ambiguous_extracted_file_names: set[str] = set()
        self.__cached_model: Optional[Model] = None
        # A cache miss by itself does not say whether rebuilding one root is
        # sound.  Keep the input categories that caused it until an
        # authoritative full build (or a proven active-transfer delta) has
        # been applied.  This deliberately fails closed: a new setter must be
        # explicitly classified before it can participate in a partial build.
        self.__invalidation_reasons: set[str] = set()
        self.__next_invalidation_token = 0
        self.__pending_invalidation_tokens: dict[
            int, tuple[str, Optional[frozenset[str]]]
        ] = {}
        self.__lftp_touched_root_file_ids: set[str] = set()
        self.__active_touched_root_file_ids: set[str] = set()
        self.__lftp_regressed_root_file_ids: set[str] = set()
        self.__source_name_counts: dict[str, int] = {}
        # Global rendering hides local roots that collide by configured local
        # root path and basename.  Keep the small derived index alongside the
        # source buckets so a pair-final safety check never walks unrelated
        # roots just to prove that arbitration is independent.
        self.__local_root_name_counts: dict[tuple[str, str], int] = {}
        self.__status_only_file_names: dict[str, str] = {}
        self.__status_only_name_counts: dict[str, int] = {}
        self.__active_only_file_names: dict[str, str] = {}
        self.__active_only_name_counts: dict[str, int] = {}
        self.__cached_unresolved_staging_collision_file_ids: set[str] = set()
        self.__cached_terminalizable_staging_collision_file_ids: set[str] = set()
        self.__stop_resume_trace_cycle_id: Optional[int] = None
        self.__stop_resume_trace_cycle_context: dict[str, object] = {}
        self.__stop_resume_trace_breadcrumb: Optional[BreadcrumbTraceEmitter] = None
        self.__stop_resume_trace_last_signatures: OrderedDict[tuple[str, str], str] = OrderedDict()
        self.__stop_resume_trace_last_enabled = False
        self.__target_archive_trace_last_signature: Optional[str] = None
        self.__performance_diagnostics: object | None = None

    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("ModelBuilder")
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")

    def set_performance_diagnostics(self, diagnostics: object | None) -> None:
        """Attach the shared fixed-counter diagnostics collector."""
        self.__performance_diagnostics = diagnostics

    def __record_cache_invalidation(self, counter: str) -> None:
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            return
        try:
            diagnostics.increment(counter)
        except Exception:
            pass

    def __reject_pair_safety(self, counter: str) -> bool:
        """Record one fixed fail-closed pair-delta category and reject it."""
        self.__record_cache_invalidation(counter)
        return False

    @staticmethod
    def __file_id_path_pair_id(file_id: str) -> Optional[str]:
        """Recover the canonical root's pair without inspecting other roots."""
        try:
            value = json.loads(file_id)
        except (TypeError, ValueError):
            return None
        return value[0] if isinstance(value, list) and len(value) == 2 and \
            isinstance(value[0], str) and isinstance(value[1], str) else None

    @staticmethod
    def __bucket_files(files: List[SystemFile]) -> dict[Optional[str], dict[str, SystemFile]]:
        buckets: dict[Optional[str], dict[str, SystemFile]] = {}
        for file in files:
            pair_id = file.path_pair_id
            buckets.setdefault(pair_id, {})[ModelBuilder.__root_file_id(file.name, pair_id)] = file
        return buckets

    @staticmethod
    def __flatten_buckets(
            buckets: dict[Optional[str], dict[str, SystemFile]],
    ) -> dict[str, SystemFile]:
        return {
            file_id: file
            for files in buckets.values()
            for file_id, file in files.items()
        }

    def __local_file(self, file_id: str) -> Optional[SystemFile]:
        return self.__local_files_by_pair.get(self.__file_id_path_pair_id(file_id), {}).get(file_id)

    def __remote_file(self, file_id: str) -> Optional[SystemFile]:
        return self.__remote_files_by_pair.get(self.__file_id_path_pair_id(file_id), {}).get(file_id)

    def get_remote_resume_source_identity(self, file_id: str) -> tuple[int, int] | None:
        """Return the portable remote identity that can authorize file resume.

        The raw scanner epoch is intentionally used instead of ModelFile's
        display datetime.  LFTP preserves source mtimes at whole-second
        precision, so a finer value would reject legitimate continuations
        while a missing raw value must fail closed.
        """
        remote_file = self.__remote_file(file_id)
        if remote_file is None or remote_file.is_dir or type(remote_file.mtime_ns) is not int:
            return None
        return remote_file.size, remote_file.mtime_ns // 1_000_000_000

    def __local_files(self) -> dict[str, SystemFile]:
        """Flatten only callers that already need global authority."""
        return self.__flatten_buckets(self.__local_files_by_pair)

    def __remote_files(self) -> dict[str, SystemFile]:
        """Flatten only callers that already need global authority."""
        return self.__flatten_buckets(self.__remote_files_by_pair)

    def local_source_roots_snapshot(self) -> tuple[SystemFile, ...]:
        """Return an on-demand ownership-census snapshot of local roots."""
        return tuple(
            file for files in self.__local_files_by_pair.values() for file in files.values()
        )

    def local_library_inventory_snapshot(
            self,
    ) -> tuple[int, dict[Optional[str], _LocalLibraryInventory]]:
        """Return immutable-value inventory state without walking local trees."""
        return self.__local_library_inventory_revision, self.__local_library_inventory_by_pair

    def local_library_inventory_revision(self) -> int:
        return self.__local_library_inventory_revision

    @staticmethod
    def __local_regular_file_inventory(files: Iterable[SystemFile]) -> tuple[int, int]:
        """Count only regular scan leaves while source authority is published."""
        file_count = 0
        total_size = 0

        def visit(file: SystemFile) -> None:
            nonlocal file_count, total_size
            if file.is_dir:
                for child in file.iter_children():
                    visit(child)
                return
            file_count += 1
            total_size += file.size

        for root in files:
            visit(root)
        return file_count, total_size

    def __replace_local_library_inventory(
            self, updated: dict[Optional[str], _LocalLibraryInventory],
    ) -> None:
        if updated != self.__local_library_inventory_by_pair:
            # Copy-on-write lets compact summary reads take the existing
            # controller model lock without sharing a mutable aggregate map.
            self.__local_library_inventory_by_pair = updated
            self.__local_library_inventory_revision += 1

    def begin_local_inventory_runtime_generation(
            self, enabled_path_pair_ids: Set[Optional[str]], changed_path_pair_ids: Set[Optional[str]],
    ) -> None:
        """Invalidate scan freshness at the controller-owned runtime boundary.

        The caller supplies configuration scope and identity changes; this
        derived cache deliberately never reads configuration itself.  A prior
        aggregate may remain visible while a same-root generation rescans, but
        it cannot stay authoritative across a new runtime generation.
        """
        enabled = {
            path_pair_id for path_pair_id in enabled_path_pair_ids
            if path_pair_id is None or isinstance(path_pair_id, str)
        }
        updated = {
            path_pair_id: inventory
            for path_pair_id, inventory in self.__local_library_inventory_by_pair.items()
            if path_pair_id in enabled
        }
        for path_pair_id, existing in list(updated.items()):
            updated[path_pair_id] = _LocalLibraryInventory(
                existing.file_count,
                existing.size,
                # A configuration/root transition needs a replacement scan,
                # but is not itself a failed scan.  Reserve ``stale`` for
                # actual failure evidence so a normal runtime refresh cannot
                # briefly report a false failure.  A retry still cannot
                # rehabilitate an actual failed snapshot; only the completed
                # authoritative replacement below may do that.
                "stale" if existing.state == "stale" else "scanning",
            )
        self.__replace_local_library_inventory(updated)

    def observe_local_scan_result(
            self, scanned_path_pair_ids: Set[Optional[str]], completed_path_pair_ids: Set[Optional[str]],
            unknown_path_pair_ids: Set[Optional[str]], failed: bool,
            enabled_path_pair_ids: Optional[Set[Optional[str]]] = None,
            recoverable_failure_path_pair_ids: Optional[Set[Optional[str]]] = None,
            terminal_failure_path_pair_ids: Optional[Set[Optional[str]]] = None,
    ) -> None:
        """Publish scan freshness without treating partial trees as inventory."""
        affected = set(scanned_path_pair_ids).union(completed_path_pair_ids, unknown_path_pair_ids)
        recoverable_failure_ids = set(recoverable_failure_path_pair_ids or set())
        terminal_failure_ids = (
            None if terminal_failure_path_pair_ids is None
            else set(terminal_failure_path_pair_ids)
        )
        normalized_unknown_path_pair_ids = set(unknown_path_pair_ids)
        # Normal progressive intake resolves anonymous failures to concrete
        # scopes before publishing.  Keep this only as a legacy direct-call
        # fallback; ModelBuilder must not independently attribute config.
        if not affected:
            affected = {None}
        updated = dict(self.__local_library_inventory_by_pair)
        for path_pair_id in affected:
            if path_pair_id is not None and not isinstance(path_pair_id, str):
                continue
            existing = updated.get(path_pair_id)
            terminal_failure = path_pair_id in terminal_failure_ids if terminal_failure_ids is not None else (
                failed and path_pair_id not in recoverable_failure_ids
            )
            if terminal_failure:
                updated[path_pair_id] = _LocalLibraryInventory(
                    existing.file_count if existing is not None else None,
                    existing.size if existing is not None else None,
                    "stale" if existing is not None else "waiting_for_scan",
                )
            elif failed or path_pair_id in normalized_unknown_path_pair_ids:
                updated[path_pair_id] = _LocalLibraryInventory(
                    existing.file_count if existing is not None else None,
                    existing.size if existing is not None else None,
                    "scanning",
                )
            else:
                updated[path_pair_id] = _LocalLibraryInventory(
                    existing.file_count if existing is not None else None,
                    existing.size if existing is not None else None,
                    "stale" if existing is not None and existing.state == "stale" else "scanning",
                )
        self.__replace_local_library_inventory(updated)

    def record_local_inventory_completion(self, path_pair_ids: Set[Optional[str]]) -> None:
        """Replace inventory only after a completed authoritative local pair."""
        updated = dict(self.__local_library_inventory_by_pair)
        for path_pair_id in path_pair_ids:
            if path_pair_id is not None and not isinstance(path_pair_id, str):
                continue
            file_count, total_size = self.__local_regular_file_inventory(
                self.__local_files_by_pair.get(path_pair_id, {}).values()
            )
            updated[path_pair_id] = _LocalLibraryInventory(file_count, total_size, "up_to_date")
        self.__replace_local_library_inventory(updated)

    def remote_source_roots_snapshot(self) -> tuple[SystemFile, ...]:
        """Return an on-demand ownership-census snapshot of remote roots."""
        return tuple(
            file for files in self.__remote_files_by_pair.values() for file in files.values()
        )

    def __invalidate_cache(
            self,
            counter: str,
            touched_lftp_root_file_ids: Optional[Set[str]] = None,
            touched_active_root_file_ids: Optional[Set[str]] = None,
            affected_file_ids: Optional[Set[str]] = None,
    ) -> int:
        self.__cached_model = None
        self.__invalidation_reasons.add(counter)
        if counter == MODEL_BUILDER_INVALIDATION_LFTP_STATUSES and touched_lftp_root_file_ids:
            self.__lftp_touched_root_file_ids.update(touched_lftp_root_file_ids)
        if counter == MODEL_BUILDER_INVALIDATION_ACTIVE_FILES and touched_active_root_file_ids:
            self.__active_touched_root_file_ids.update(touched_active_root_file_ids)
        self.__next_invalidation_token += 1
        token = self.__next_invalidation_token
        if affected_file_ids is None:
            if counter == MODEL_BUILDER_INVALIDATION_LFTP_STATUSES:
                affected_file_ids = touched_lftp_root_file_ids
            elif counter == MODEL_BUILDER_INVALIDATION_ACTIVE_FILES:
                affected_file_ids = touched_active_root_file_ids
        self.__pending_invalidation_tokens[token] = (
            counter,
            None if affected_file_ids is None else frozenset(affected_file_ids),
        )
        self.__record_cache_invalidation(counter)
        return token

    def __refresh_source_name_counts(self) -> None:
        counts: dict[str, int] = {}
        local_files = self.__local_files()
        remote_files = self.__remote_files()
        for file_id in set(local_files).union(remote_files):
            source = local_files.get(file_id) or remote_files[file_id]
            counts[source.name] = counts.get(source.name, 0) + 1
        self.__source_name_counts = counts
        self.__status_only_file_names = {
            file_id: status.name for file_id, status in self.__lftp_statuses.items()
            if self.__local_file(file_id) is None and self.__remote_file(file_id) is None
        }
        status_counts: dict[str, int] = {}
        for name in self.__status_only_file_names.values():
            status_counts[name] = status_counts.get(name, 0) + 1
        self.__status_only_name_counts = status_counts
        self.__active_only_file_names = {
            file_id: file.name for file_id, file in self.__active_files.items()
            if self.__local_file(file_id) is None and self.__remote_file(file_id) is None
        }
        active_counts: dict[str, int] = {}
        for name in self.__active_only_file_names.values():
            active_counts[name] = active_counts.get(name, 0) + 1
        self.__active_only_name_counts = active_counts

    def __local_root_name_key(
            self, local_file: SystemFile, path_pair_id: Optional[str],
    ) -> Optional[tuple[str, str]]:
        normalized_root = self.__resolve_normalized_local_root_path(
            local_file, path_pair_id,
        )
        if normalized_root is None:
            return None
        return normalized_root, local_file.name

    def __refresh_local_root_name_counts(self) -> None:
        counts: dict[tuple[str, str], int] = {}
        for path_pair_id, files in self.__local_files_by_pair.items():
            for local_file in files.values():
                key = self.__local_root_name_key(local_file, path_pair_id)
                if key is not None:
                    counts[key] = counts.get(key, 0) + 1
        self.__local_root_name_counts = counts

    def __adjust_local_root_name_count(
            self, local_file: SystemFile, path_pair_id: Optional[str], delta: int,
    ) -> None:
        key = self.__local_root_name_key(local_file, path_pair_id)
        if key is None:
            return
        count = self.__local_root_name_counts.get(key, 0) + delta
        if count > 0:
            self.__local_root_name_counts[key] = count
        else:
            self.__local_root_name_counts.pop(key, None)

    def __update_status_only_name_count(self, file_id: str) -> None:
        previous_name = self.__status_only_file_names.pop(file_id, None)
        if previous_name is not None:
            remaining = self.__status_only_name_counts.get(previous_name, 0) - 1
            if remaining > 0:
                self.__status_only_name_counts[previous_name] = remaining
            else:
                self.__status_only_name_counts.pop(previous_name, None)
        status = self.__lftp_statuses.get(file_id)
        if status is None or self.__local_file(file_id) is not None or self.__remote_file(file_id) is not None:
            return
        self.__status_only_file_names[file_id] = status.name
        self.__status_only_name_counts[status.name] = self.__status_only_name_counts.get(status.name, 0) + 1

    def __update_active_only_name_count(self, file_id: str) -> None:
        previous_name = self.__active_only_file_names.pop(file_id, None)
        if previous_name is not None:
            remaining = self.__active_only_name_counts.get(previous_name, 0) - 1
            if remaining > 0:
                self.__active_only_name_counts[previous_name] = remaining
            else:
                self.__active_only_name_counts.pop(previous_name, None)
        active_file = self.__active_files.get(file_id)
        if active_file is None or self.__local_file(file_id) is not None or self.__remote_file(file_id) is not None:
            return
        self.__active_only_file_names[file_id] = active_file.name
        self.__active_only_name_counts[active_file.name] = self.__active_only_name_counts.get(active_file.name, 0) + 1

    def __begin_duration(self, metric: str) -> object:
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            return None
        try:
            return diagnostics.begin_duration(metric)
        except Exception:
            return None

    def __finish_duration(self, metric: str, started_at: object) -> None:
        if started_at is None:
            return
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            return
        try:
            diagnostics.finish_duration(metric, started_at)
        except Exception:
            pass

    @staticmethod
    def __build_dummy_model_logger() -> logging.Logger:
        logger = logging.Logger("dummy.Model")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    def set_stop_resume_trace_breadcrumb(self, emitter: Optional[BreadcrumbTraceEmitter]) -> None:
        """Attach the shared opt-in bounded breadcrumb emitter."""
        self.__stop_resume_trace_breadcrumb = emitter

    def record_lifecycle_candidate_publication(self, candidate: Model, build_kind: str) -> set[str]:
        """Trace global-marker subjects before a candidate reaches the live model."""
        if not self.__is_stop_resume_trace_enabled("lifecycle.persist", "info"):
            return set()
        subject_ids = set(self.__downloaded_files or set()) | set(self.__final_move_succeeded_files)
        for file_id in tuple(subject_ids):
            try:
                candidate_file = candidate.get_file(file_id)
            except ModelError:
                self.__record_lifecycle_persist_breadcrumb_for_file_id("model_candidate", file_id, {
                    "candidate_state": "absent",
                    "build_kind": build_kind,
                    "adoption_kind": "pending",
                })
                continue
            self.__record_lifecycle_persist_breadcrumb("model_candidate", candidate_file, {
                "candidate_state": self.__state_category(candidate_file),
                "build_kind": build_kind,
                "adoption_kind": "pending",
            })
        return subject_ids

    def record_lifecycle_live_publication(
            self, candidate: Model, live: Model, subject_ids: set[str],
            build_kind: str, adoption_kind: str,
    ) -> None:
        """Trace the resulting live state for the same ephemeral candidate subjects."""
        if not self.__is_stop_resume_trace_enabled("lifecycle.persist", "info"):
            return
        for file_id in subject_ids:
            candidate_state = "absent"
            live_state = "absent"
            try:
                candidate_file = candidate.get_file(file_id)
                candidate_state = self.__state_category(candidate_file)
            except ModelError:
                pass
            try:
                live_file = live.get_file(file_id)
                live_state = self.__state_category(live_file)
            except ModelError:
                pass
            self.__record_lifecycle_persist_breadcrumb_for_file_id("model_live_publication", file_id, {
                "candidate_state": candidate_state,
                "live_state": live_state,
                "build_kind": build_kind,
                "adoption_kind": adoption_kind,
            })

    def is_stop_resume_trace_enabled(self) -> bool:
        """Expose the current fail-closed diagnostic gate to stream emitters."""
        return self.__is_stop_resume_trace_enabled()

    def set_stop_resume_trace_cycle_context(self, context: Optional[dict[str, object]]) -> None:
        self.__stop_resume_trace_cycle_context = dict(context or {})

    def stop_resume_trace_metadata_for_file(self, model_file: Optional[ModelFile]) -> Optional[dict[str, object]]:
        """Return metadata for active/recent transfer SSE correlation."""
        if model_file is None or not self.__is_stop_resume_trace_enabled("model.lifecycle", "info") or \
                not self.__is_relevant_trace_file(model_file):
            return None
        cycle = self.__stop_resume_trace_cycle_id
        if cycle is None:
            return None
        return {
            "cycle": cycle,
            "corr_id": "stop-resume:{}:{}".format(model_file.file_id, cycle),
            "file_id": model_file.file_id,
            "context": dict(self.__stop_resume_trace_cycle_context),
            "backend_timestamp_ms": int(time.time_ns() / 1_000_000),
        }

    def __is_stop_resume_trace_enabled(
            self, category: str = "model.lifecycle", level: str = "info",
    ) -> bool:
        emitter = self.__stop_resume_trace_breadcrumb
        if emitter is None:
            self.__stop_resume_trace_last_enabled = False
            return False
        try:
            enabled = _breadcrumb_effectively_enabled(emitter, category, level)
        except Exception:
            enabled = False
        if enabled and not self.__stop_resume_trace_last_enabled:
            self.__stop_resume_trace_last_signatures.clear()
        self.__stop_resume_trace_last_enabled = enabled
        return enabled

    def __is_model_presentation_trace_enabled(self) -> bool:
        """Read the presentation category gate without changing trace state."""
        emitter = self.__stop_resume_trace_breadcrumb
        if emitter is None:
            return False
        effective = getattr(emitter, "is_effectively_enabled", None)
        if not callable(effective):
            return False
        try:
            return effective("model.presentation", "info") is True
        except Exception:
            return False

    @staticmethod
    def __queue_exclusion_mtime_category(
            remote_file: SystemFile, local_file: Optional[SystemFile],
    ) -> str:
        """Classify the mtime comparison used by Queue's exact exclusion gate."""
        if local_file is None or remote_file.is_dir != local_file.is_dir:
            return "missing"
        local_mtime_ns = local_file.mtime_ns
        remote_mtime_ns = remote_file.mtime_ns
        if type(local_mtime_ns) is not int or type(remote_mtime_ns) is not int:
            return "missing"
        if local_mtime_ns == remote_mtime_ns:
            return "exact"
        if local_mtime_ns % 1_000_000_000 == 0 and \
                local_mtime_ns // 1_000_000_000 == remote_mtime_ns // 1_000_000_000:
            return "portable_second_match"
        return "raw_mismatch"

    @staticmethod
    def __queue_exclusion_trace_empty_counts() -> dict[str, int]:
        return {
            "remote_leaf_count": 0,
            "local_leaf_count": 0,
            "remote_only_leaf_count": 0,
            "local_only_leaf_count": 0,
            "staging_sibling_leaf_count": 0,
            "staging_candidate_leaf_count": 0,
            "excluded_leaf_count": 0,
            "rejected_leaf_count": 0,
            "collision_rejected_leaf_count": 0,
            "size_mismatch_leaf_count": 0,
            "type_mismatch_leaf_count": 0,
            "status_sidecar_leaf_count": 0,
        }

    @staticmethod
    def __queue_trace_presence(counts: dict[str, int]) -> dict[str, bool]:
        return {
            "{}_present".format(key.removesuffix("_count")): value > 0
            for key, value in counts.items()
        }

    def __record_queue_exclusion_trace(
            self, file_id: str, details: dict[str, object],
    ) -> None:
        """Emit one bounded, identity-free Queue exclusion decision."""
        if not self.__is_stop_resume_trace_enabled("queue.exclusion", "info"):
            return
        breadcrumb = self.__stop_resume_trace_breadcrumb
        if breadcrumb is None:
            return
        correlation = "fractional-mtime:{}".format(opaque_trace_correlation(file_id))
        try:
            breadcrumb.record(
                "model_builder",
                "queue_exclusion_decision",
                details,
                stage="queue_exclusion",
                event_type="diagnostic",
                category="queue.exclusion",
                level="info",
                corr_id=correlation,
                trace_scope="flow",
            )
        except Exception:
            self.logger.debug("Ignoring Queue exclusion breadcrumb failure", exc_info=True)

    @staticmethod
    def __state_category(model_file: ModelFile) -> str:
        state = getattr(model_file, "state", None)
        name = getattr(state, "name", None)
        return name.lower() if isinstance(name, str) else "unknown"

    @staticmethod
    def __model_presentation_visible_state(model_file: ModelFile) -> str:
        """Mirror the web presentation arbitration for diagnostic comparison."""
        if model_file.local_present and not model_file.remote_has_transferable_content:
            return "local_only"
        has_display_union = model_file.display_size_total is not None and \
            model_file.display_transferred_size is not None
        has_complete_display_union = model_file.display_size_total is not None and \
            model_file.display_transferred_size is not None and \
            model_file.display_size_total > 0 and \
            model_file.display_transferred_size >= model_file.display_size_total
        progress_total = model_file.display_size_total if has_display_union else model_file.remote_size
        progress_transferred = model_file.display_transferred_size \
            if has_display_union else model_file.transferred_size
        has_complete_progress = model_file.complete_local_coverage and \
            (not has_display_union or has_complete_display_union) and \
            not model_file.explicitly_stopped
        has_retained_progress = (
            model_file.remote_has_transferable_content
            and (progress_total or 0) > 0
            and ((progress_transferred or 0) > 0 or (model_file.download_progress or 0) > 0)
        )
        if model_file.state in (ModelFile.State.DEFAULT, ModelFile.State.DOWNLOADED) and \
                model_file.explicitly_stopped and not model_file.final_move_succeeded:
            return "stopped"
        if model_file.state == ModelFile.State.DEFAULT:
            if has_complete_progress:
                return "downloaded"
            if has_retained_progress:
                return "stopped"
        if model_file.state == ModelFile.State.DOWNLOADED and model_file.final_move_succeeded:
            return "move_succeeded"
        return ModelBuilder.__state_category(model_file)

    def __record_model_presentation_anomaly(self, model_file: ModelFile) -> None:
        """Emit identity-free evidence for a raw/visible or root/child split."""
        if not self.__is_model_presentation_trace_enabled():
            return

        child_counts = {
            "total": 0,
            "remote_only": 0,
            "local_only": 0,
            "both": 0,
            "downloaded_presentation": 0,
            "local_only_presentation": 0,
        }
        root_remote_present = bool(model_file.remote_present)
        root_local_present = bool(model_file.local_present)
        child_presence_split = False
        max_children = self.__MODEL_PRESENTATION_TRACE_COUNT_MAX
        for index, child in enumerate(model_file.iter_children()):
            if index >= max_children:
                break
            child_counts["total"] += 1
            child_remote_present = bool(child.remote_present)
            child_local_present = bool(child.local_present)
            if child_remote_present and child_local_present:
                child_counts["both"] += 1
            elif child_remote_present:
                child_counts["remote_only"] += 1
            elif child_local_present:
                child_counts["local_only"] += 1
            child_presence_split |= (
                child_remote_present != root_remote_present or
                child_local_present != root_local_present
            )
            child_visible_state = self.__model_presentation_visible_state(child)
            if child_visible_state == "downloaded":
                child_counts["downloaded_presentation"] += 1
            elif child_visible_state == "local_only":
                child_counts["local_only_presentation"] += 1

        derived_visible_state = self.__model_presentation_visible_state(model_file)
        raw_state = self.__state_category(model_file)
        if raw_state == derived_visible_state and not child_presence_split:
            return

        details: dict[str, object] = {
            "schema": self.__MODEL_PRESENTATION_TRACE_SCHEMA,
            "version": 1,
            "raw_state": raw_state,
            "derived_visible_state": derived_visible_state,
            "presence": {
                "remote_present": root_remote_present,
                "local_present": root_local_present,
                "root_child_split": child_presence_split,
            },
            "transferable": bool(model_file.remote_has_transferable_content),
            "coverage": bool(model_file.complete_local_coverage),
            "stopped": bool(model_file.explicitly_stopped),
            "child_counts": child_counts,
        }
        try:
            breadcrumb = self.__stop_resume_trace_breadcrumb
            if breadcrumb is not None:
                breadcrumb.record(
                    "model_builder",
                    "model_presentation_anomaly",
                    details,
                    stage="model_presentation",
                    event_type="diagnostic",
                    category="model.presentation",
                    level="info",
                    corr_id=opaque_trace_correlation(model_file.file_id),
                    trace_scope="flow",
                )
        except Exception:
            self.logger.debug("Ignoring model presentation breadcrumb failure", exc_info=True)

    @staticmethod
    def __root_trace_coverage_categories(
            remote_file: Optional[SystemFile], local_file: Optional[SystemFile],
    ) -> dict[str, int]:
        """Summarize root coverage without retaining names or file identities."""
        counts = {
            "remote_leaf_count": 0,
            "covered_remote_leaf_count": 0,
            "collision_node_count": 0,
            "unmatched_staging_leaf_count": 0,
            "type_mismatch_count": 0,
        }

        def staging_leaf_count(candidate: Optional[SystemFile]) -> int:
            if candidate is None or not ModelBuilder.__has_staging_descendant(candidate):
                return 0
            if not candidate.is_dir:
                return 1 if candidate.is_staging else 0
            children = tuple(candidate.iter_children())
            if not children:
                return 1 if candidate.is_staging else 0
            return sum(staging_leaf_count(child) for child in children)

        def visit(
                remote: Optional[SystemFile], local: Optional[SystemFile],
                ancestor_has_collision: bool = False,
        ) -> None:
            if local is not None and local.has_staging_collision:
                counts["collision_node_count"] += 1
            has_collision = ancestor_has_collision or bool(
                getattr(local, "has_staging_collision", False)
            )
            if remote is None:
                counts["unmatched_staging_leaf_count"] += staging_leaf_count(local)
                return
            if not remote.is_dir:
                counts["remote_leaf_count"] += 1
                if local is not None and remote.is_dir == local.is_dir and \
                        not has_collision and local.size >= remote.size:
                    counts["covered_remote_leaf_count"] += 1
                if local is not None and remote.is_dir != local.is_dir:
                    counts["type_mismatch_count"] += 1
                return
            if local is None:
                for child in remote.iter_children():
                    visit(child, None, has_collision)
                return
            if remote.is_dir != local.is_dir:
                counts["type_mismatch_count"] += 1
                for child in remote.iter_children():
                    visit(child, None, has_collision)
                counts["unmatched_staging_leaf_count"] += staging_leaf_count(local)
                return

            remote_children = {child.name: child for child in remote.iter_children()}
            local_children = {child.name: child for child in local.iter_children()}
            for child in remote_children.values():
                visit(child, local_children.get(child.name), has_collision)
            for name, child in local_children.items():
                if name not in remote_children:
                    counts["unmatched_staging_leaf_count"] += staging_leaf_count(child)

        visit(remote_file, local_file)
        return counts

    @staticmethod
    def __root_trace_reason(
            remote_present: bool, local_present: bool, type_mismatch: bool,
            presentation_coverage: bool, strict_lifecycle_proof: bool,
            explicit_stop: bool, marker_present: bool, move_failed: bool,
            transfer_hint_present: bool,
            collision_count: int, unmatched_staging_count: int,
    ) -> str:
        """Return a bounded reason enum for a default-root diagnostic."""
        if type_mismatch:
            return "type_mismatch"
        if not remote_present:
            return "remote_absent"
        if not local_present:
            return "local_absent"
        if explicit_stop:
            return "explicit_stop"
        if move_failed:
            return "move_failed"
        if collision_count:
            return "staging_collision"
        if unmatched_staging_count:
            return "unmatched_staging"
        if not presentation_coverage:
            return "presentation_coverage_incomplete"
        if not strict_lifecycle_proof:
            return "strict_lifecycle_unproven"
        if marker_present:
            return "lifecycle_marker"
        if transfer_hint_present:
            return "transfer_hint"
        return "ordinary_default"

    def __record_root_default_decision(
            self,
            file_id: str,
            remote: Optional[SystemFile],
            local: Optional[SystemFile],
            status: Optional[LftpJobStatus],
            is_stopped: bool,
            presentation_coverage: bool,
            strict_lifecycle_proof: bool,
            transfer_hint_present: bool,
            root_state: str,
    ) -> None:
        """Emit bounded, private evidence for an ordinary root decision."""
        if not self.__is_stop_resume_trace_enabled("queue.exclusion", "info"):
            return
        coverage = self.__root_trace_coverage_categories(remote, local)
        marker_present = (
            file_id in (self.__downloaded_files or set()) or
            file_id in self.__final_move_succeeded_files or
            file_id in self.__extracted_files or
            file_id in self.__move_failed_files
        )
        move_failed = root_state == "move_failed" or file_id in self.__move_failed_files
        remote_present = remote is not None
        local_present = local is not None
        type_mismatch = coverage["type_mismatch_count"] > 0 or (
            remote is not None and local is not None and remote.is_dir != local.is_dir
        ) or (
            status is not None and remote is not None and
            (status.type == LftpJobStatus.Type.MIRROR) != remote.is_dir
        ) or (
            status is not None and local is not None and
            (status.type == LftpJobStatus.Type.MIRROR) != local.is_dir
        )
        collision_count = coverage["collision_node_count"]
        unmatched_staging_count = coverage["unmatched_staging_leaf_count"]
        reason = self.__root_trace_reason(
            remote_present,
            local_present,
            type_mismatch,
            presentation_coverage,
            strict_lifecycle_proof,
            is_stopped,
            marker_present,
            move_failed,
            transfer_hint_present,
            collision_count,
            unmatched_staging_count,
        )
        correlation = opaque_trace_correlation(file_id)
        details: dict[str, object] = {
            "schema": "model_builder.root_default_decision.v2",
            "reason": reason,
            "presence": {
                "remote": remote_present,
                "local": local_present,
                "status": status is not None,
            },
            "type": {
                "mismatch": type_mismatch,
            },
            "coverage": {
                "presentation": presentation_coverage,
                "effective_tree_proof": strict_lifecycle_proof,
                "remote_leaves_present": coverage["remote_leaf_count"] > 0,
                "covered_remote_leaves_present": coverage["covered_remote_leaf_count"] > 0,
            },
            "lifecycle": {
                "explicit_stop": is_stopped,
                "marker_present": marker_present,
                "move_failed": move_failed,
                "transfer_hint_present": transfer_hint_present,
            },
            "staging": {
                "collision": collision_count > 0,
                "unmatched": unmatched_staging_count > 0,
            },
            "root_state": root_state,
        }
        try:
            breadcrumb = self.__stop_resume_trace_breadcrumb
            if breadcrumb is not None:
                breadcrumb.record(
                    "model_builder",
                    "root_default_decision",
                    details,
                    stage="model_root_default_decision",
                    event_type="diagnostic",
                    category="queue.exclusion",
                    level="info",
                    corr_id=correlation,
                    trace_scope="flow",
                )
        except Exception:
            self.logger.debug("Ignoring root default decision breadcrumb failure", exc_info=True)

    def __record_lifecycle_persist_breadcrumb(
            self, event: str, model_file: ModelFile, details: dict[str, object],
    ) -> None:
        """Emit deduplicated generic persist-arbitration evidence under the shared gate."""
        self.__record_lifecycle_persist_breadcrumb_for_file_id(event, model_file.file_id, details)

    @staticmethod
    def __lifecycle_trace_coalesce_key(
            event: str, stage: str, file_id: str, details: dict[str, object],
    ) -> str:
        """Create a private semantic key without retaining any source identity."""
        payload = {
            "event": event,
            "stage": stage,
            "corr_id": opaque_trace_correlation(file_id),
            "details": details,
        }
        return opaque_trace_correlation(json.dumps(payload, sort_keys=True, default=str))

    def __record_lifecycle_persist_breadcrumb_for_file_id(
            self, event: str, file_id: str, details: dict[str, object],
    ) -> None:
        """Retain a subject's opaque correlation even when a candidate lost it."""
        if not self.__is_stop_resume_trace_enabled("lifecycle.persist", "info"):
            return
        try:
            signature = json.dumps(details, sort_keys=True, default=str)
            signature_key = (file_id, event)
            if self.__stop_resume_trace_last_signatures.get(signature_key) == "retained:" + signature:
                self.__stop_resume_trace_last_signatures.move_to_end(signature_key)
                return
            breadcrumb = self.__stop_resume_trace_breadcrumb
            if breadcrumb is not None:
                outcome = breadcrumb.record(
                    "model_builder", event, {**details, "monotonic_ms": int(time.monotonic_ns() / 1_000_000)},
                    stage="persist_authority", event_type="diagnostic",
                    category="lifecycle.persist", level="info",
                    corr_id=opaque_trace_correlation(file_id), trace_scope="flow",
                    _coalesce_key=self.__lifecycle_trace_coalesce_key(
                        event, "persist_authority", file_id, details,
                    ),
                )
                # Enqueued signatures are bounded bookkeeping only; they are
                # never used to suppress retry. The collector coalesces them
                # using the private opaque key above.
                self.__remember_trace_signature(signature_key, signature, outcome == "retained")
                return
            self.__remember_trace_signature(signature_key, signature, True)
        except Exception:
            self.logger.debug("Ignoring lifecycle persist breadcrumb failure", exc_info=True)

    def __is_target_archive_trace_enabled(self) -> bool:
        return self.__target_archive_trace_file_id is not None

    def __trace_target_archive_selector_matches_model_file(self, model_file: ModelFile, root_file_id: str) -> bool:
        if not self.__is_target_archive_trace_enabled():
            return False
        if self.__target_archive_trace_file_id == model_file.file_id:
            return True
        if model_file.file_id != root_file_id:
            return False
        selector_name = self.__extract_trace_selector_name(self.__target_archive_trace_file_id)
        return selector_name == model_file.name

    def __is_relevant_trace_file(self, model_file: ModelFile, is_stopped: Optional[bool] = None) -> bool:
        """Keep tracing focused on transfer state, not every scanned file."""
        if model_file.file_id in self.__recent_live_transfer_snapshots or \
                model_file.file_id in self.__retained_stopped_transfer_snapshots:
            return True
        # A live LFTP root can reconcile to a terminal model state before its
        # transfer snapshot is evicted.  Keep that canonical status membership
        # traceable without treating every active-scan child as a transfer.
        if model_file.file_id in self.__lftp_statuses:
            return True
        if is_stopped is None:
            is_stopped = self.__is_stopped_file(model_file.file_id)
        if is_stopped:
            return True
        return model_file.state in (ModelFile.State.QUEUED, ModelFile.State.DOWNLOADING)

    @staticmethod
    def __summarize_target_archive_source(arbitration_source: str,
                                          model_file: ModelFile,
                                          local: Optional[SystemFile],
                                          transfer_state: Optional[_TransferState]) -> str:
        if arbitration_source in (
            "recent_live_snapshot",
            "live_status",
        ):
            return "live_transfer"
        if arbitration_source in (
            "retained_recent_live_snapshot",
            "retained_stopped_snapshot",
            "retained_stopped_snapshot_from_live_status",
            "retained_stopped_snapshot_without_live_progress",
            "live_status_coalesced_with_retained_floor",
        ):
            return "retained_snapshot"
        if arbitration_source == "staging_completion_without_live_status":
            return "staging_only"
        if arbitration_source == "suppressed_by_authoritative_local_completion":
            return "authoritative_local"
        if model_file.state == ModelFile.State.EXTRACTED:
            return "persisted_extracted"
        if model_file.state == ModelFile.State.DOWNLOADED:
            return "persisted_downloaded"
        if ModelBuilder.__is_authoritative_local_file(local):
            return "authoritative_local"
        if transfer_state is not None:
            return "live_transfer"
        return "scan_only"

    def __trace_target_archive_event(self, event: str, payload: dict[str, object]) -> None:
        if not self.__is_target_archive_trace_enabled():
            return
        trace_payload: dict[str, object] = {
            "event": event,
            "target_selector": self.__target_archive_trace_file_id,
        }
        trace_payload.update(payload)
        signature = json.dumps(trace_payload, sort_keys=True)
        if signature == self.__target_archive_trace_last_signature:
            return
        self.__target_archive_trace_last_signature = signature
        self.__target_archive_trace_logger.info("target_archive_trace %s", signature)

    def __trace_target_archive_arbitration(self,
                                           model_file: ModelFile,
                                           root_file_id: str,
                                           is_stopped: bool,
                                           remote_present: bool,
                                           local_present: bool,
                                           local: Optional[SystemFile],
                                           active_present: bool,
                                           local_freshness: str,
                                           status: Optional[LftpJobStatus],
                                           transfer_state: Optional[_TransferState],
                                           arbitration_source: str) -> None:
        if not self.__trace_target_archive_selector_matches_model_file(model_file, root_file_id):
            return

        self.__trace_target_archive_event("arbitration", {
            "model_source": "rebuilt",
            "source_kind": ModelBuilder.__summarize_target_archive_source(
                arbitration_source,
                model_file,
                local,
                transfer_state,
            ),
            "local_freshness": local_freshness,
            "resolved_identity": {
                "file_id": model_file.file_id,
                "root_file_id": root_file_id,
                "path_pair_id": model_file.path_pair_id,
                "path_pair_name": model_file.path_pair_name,
            },
            "markers": {
                "downloaded": self.__model_file_matches_persisted_name(model_file, self.__downloaded_files),
                "extracted": self.__model_file_matches_persisted_name(model_file, self.__extracted_files),
                "stopped": is_stopped,
            },
            "raw_lftp_status": {
                "state": ModelBuilder.__enum_name(status.state) if status is not None else None,
                "job_id": status.id if status is not None else None,
                "file_id": status.file_id if status is not None else None,
                "transfer": ModelBuilder.__summarize_transfer_state(transfer_state),
            },
            "matched_local": {
                "name": local.name if local is not None else None,
                "path": self.__resolve_local_disk_path(model_file, local),
            },
            "local_data_role": ModelBuilder.__summarize_local_data_role(
                model_file,
                local,
                transfer_state,
                arbitration_source,
            ),
            "local_size_apparent": local.size if local is not None else None,
            "local_size_allocated": self.__get_allocated_local_size(model_file, local),
            "presence": {
                "remote": remote_present,
                "local": local_present,
                "active": active_present,
                "live_transfer": transfer_state is not None,
            },
            "arbitration_source": arbitration_source,
            "final_model": ModelBuilder.__summarize_rendered_model(model_file),
        })

    def begin_stop_resume_trace_cycle(self, cycle_id: int) -> None:
        self.__stop_resume_trace_cycle_id = cycle_id
        self.__stop_resume_trace_cycle_context = {}

    def finish_stop_resume_trace_cycle(self, model: Model, build_triggered: bool) -> None:
        if not self.__is_stop_resume_trace_enabled() or build_triggered:
            return
        # Every relevant trace subject is already owned by one of these
        # runtime sets. Walking the complete model here made a no-rebuild
        # breadcrumb cycle O(all scanned roots) even when no transfer existed.
        candidate_file_ids = set(self.__recent_live_transfer_snapshots)
        candidate_file_ids.update(self.__retained_stopped_transfer_snapshots)
        candidate_file_ids.update(self.__lftp_statuses)
        candidate_file_ids.update(self.__stopped_files)
        candidate_file_ids.update(self.__active_file_ids)
        for file_id in candidate_file_ids:
            try:
                model_file = model.get_file(file_id)
            except ModelError:
                continue
            if not self.__is_relevant_trace_file(model_file):
                continue
            self.__trace_cycle_event("no_rebuild", {
                "model_source": "cached",
                "file_id": model_file.file_id,
                "final_model": self.__summarize_rendered_model(model_file),
                "update_context": dict(self.__stop_resume_trace_cycle_context),
            })

    @staticmethod
    def __root_file_id(name: str, path_pair_id: Optional[str]) -> str:
        return ModelFile.build_file_id(name, path_pair_id)

    @staticmethod
    def __model_file_matches_persisted_name(model_file: ModelFile, persisted_names: Optional[set[str]]) -> bool:
        if persisted_names is None:
            return False
        if model_file.file_id in persisted_names:
            return True
        return False

    @staticmethod
    def __extract_status_key(status: ExtractStatus) -> str:
        if status.file_id is not None:
            return status.file_id
        return ModelFile.build_file_id(status.name, status.path_pair_id)

    @staticmethod
    def __candidate_stopped_file_ids(file_id: Optional[str],
                                     remote: Optional[SystemFile] = None,
                                     local: Optional[SystemFile] = None,
                                     status: Optional[LftpJobStatus] = None) -> set[str]:
        candidate_ids: set[str] = set()
        if file_id is not None:
            candidate_ids.add(file_id)

        path_pair_id = remote.path_pair_id if remote and remote.path_pair_id is not None else \
            local.path_pair_id if local and local.path_pair_id is not None else \
            status.path_pair_id if status and status.path_pair_id is not None else None
        if path_pair_id is None:
            return candidate_ids

        full_path = status.name if status is not None else \
            remote.name if remote is not None else \
            local.name if local is not None else None
        if full_path is None:
            return candidate_ids

        candidate_ids.add(ModelFile.build_file_id(full_path, path_pair_id))
        return candidate_ids

    def __is_stopped_file(self,
                          file_id: str,
                          remote: Optional[SystemFile] = None,
                          local: Optional[SystemFile] = None,
                          status: Optional[LftpJobStatus] = None) -> bool:
        if any(candidate_id in self.__stopped_files for candidate_id in
               self.__candidate_stopped_file_ids(file_id, remote, local, status)):
            return True
        if remote is None:
            remote = self.__remote_file(file_id)
        if local is None:
            local = self.__local_file(file_id)
        if remote is None and local is None and status is None:
            status = self.__lftp_statuses.get(file_id)
        if status is not None and status.name in self.__stopped_files:
            return True
        return (remote is not None and remote.name in self.__stopped_files) or \
               (local is not None and local.name in self.__stopped_files)

    @staticmethod
    def __apply_path_pair_metadata(
        model_file: ModelFile, path_pair_id: Optional[str], path_pair_name: Optional[str]
    ) -> None:
        model_file.path_pair_id = path_pair_id
        model_file.path_pair_name = path_pair_name

    @staticmethod
    def __enum_name(value: Optional[Enum]) -> Optional[str]:
        return value.name if value is not None else None

    @staticmethod
    def __collect_active_file_ids(system_file: SystemFile,
                                  file_ids: set[str],
                                  parent_path: Optional[str] = None,
                                  path_pair_id: Optional[str] = None) -> None:
        current_path = system_file.name if parent_path is None else os.path.join(parent_path, system_file.name)
        effective_path_pair_id = system_file.path_pair_id if system_file.path_pair_id is not None else path_pair_id
        file_ids.add(ModelFile.build_file_id(current_path, effective_path_pair_id))
        for child in system_file.iter_children():
            ModelBuilder.__collect_active_file_ids(child, file_ids, current_path, effective_path_pair_id)

    @staticmethod
    def __summarize_transfer_state(
        transfer_state: Optional[_TransferState]
    ) -> Optional[dict[str, object]]:
        if transfer_state is None:
            return None
        return {
            "size_local": transfer_state.size_local,
            "size_remote": transfer_state.size_remote,
            "percent_local": transfer_state.percent_local,
            "speed": transfer_state.speed,
            "eta": transfer_state.eta,
        }

    @staticmethod
    def __summarize_rendered_model(model_file: Optional[ModelFile]) -> Optional[dict[str, object]]:
        if model_file is None:
            return None
        return {
            "state": ModelBuilder.__enum_name(model_file.state),
            "transferred_size": model_file.transferred_size,
            "download_progress": model_file.download_progress,
            "downloading_speed": model_file.downloading_speed,
            "eta": model_file.eta,
        }

    @staticmethod
    def __summarize_local_freshness(local_file: Optional[SystemFile]) -> str:
        if local_file is None:
            return "missing"
        return "staging" if getattr(local_file, "is_staging", False) else "authoritative"

    @staticmethod
    def __summarize_local_data_role(model_file: Optional[ModelFile],
                                    local_file: Optional[SystemFile],
                                    transfer_state: Optional[_TransferState],
                                    arbitration_source: str) -> Optional[str]:
        if local_file is None or model_file is None:
            return None
        if arbitration_source in (
            "suppressed_by_authoritative_local_completion",
            "suppressed_by_staging_completion_after_live_status_lost",
            "staging_completion_without_live_status",
        ):
            return "completion"
        if ModelBuilder.__is_authoritative_local_file(local_file) and \
                transfer_state is None and \
                model_file.transferred_size is not None and \
                model_file.transferred_size == local_file.size and \
                model_file.state != ModelFile.State.DOWNLOADED:
            return "progress"
        return "presence"

    @staticmethod
    def __is_stoppable_model_file(model_file: ModelFile,
                                  local_file: Optional[SystemFile],
                                  current_transfer_state: Optional[_TransferState],
                                  status: Optional[LftpJobStatus] = None) -> bool:
        if model_file.state == ModelFile.State.QUEUED:
            return True
        if model_file.state != ModelFile.State.DOWNLOADING:
            return False
        if current_transfer_state is None:
            return False
        if model_file.is_dir:
            return True
        # Parallel pget continuation needs its segment sidecar to stop safely.
        # A single-stream GET continuation has no map by design, but a parsed
        # authoritative running GET status identifies that narrower safe case.
        return (local_file is not None and getattr(local_file, "status_sidecar_ready", False)) or \
            (status is not None and status.state == LftpJobStatus.State.RUNNING and
             status.type == getattr(LftpJobStatus.Type, "GET", None))

    def set_local_root_paths(self,
                             local_root_paths: Dict[Optional[str], str],
                             local_staging_paths: Optional[Dict[Optional[str], str]] = None) -> None:
        next_local_root_paths = {path_pair_id: path for path_pair_id, path in local_root_paths.items() if path}
        next_local_staging_paths = {
            path_pair_id: path for path_pair_id, path in (local_staging_paths or {}).items() if path
        }
        if next_local_root_paths != self.__local_root_paths or next_local_staging_paths != self.__local_staging_paths:
            self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_LOCAL_ROOT_PATHS)
        self.__local_root_paths = next_local_root_paths
        self.__local_staging_paths = next_local_staging_paths
        self.__refresh_local_root_name_counts()

    def __resolve_local_disk_path(self,
                                  model_file: ModelFile,
                                  local_file: Optional[SystemFile]) -> Optional[str]:
        if local_file is None:
            return None
        root_paths = self.__local_staging_paths if getattr(local_file, "is_staging", False) else self.__local_root_paths
        resolved_root = root_paths.get(model_file.path_pair_id)
        if resolved_root is None:
            return None
        return os.path.join(resolved_root, model_file.full_path)

    def __resolve_normalized_local_root_path(self,
                                             local_file: Optional[SystemFile],
                                             path_pair_id: Optional[str]) -> Optional[str]:
        if local_file is not None and getattr(local_file, "is_staging", False):
            return None
        resolved_root = self.__local_root_paths.get(path_pair_id)
        if resolved_root is None:
            return None
        return os.path.normcase(os.path.normpath(resolved_root.replace("\\", "/")))

    def __get_allocated_local_size(self, model_file: ModelFile, local_file: Optional[SystemFile]) -> Optional[int]:
        local_path = self.__resolve_local_disk_path(model_file, local_file)
        if local_path is None or not os.path.exists(local_path):
            return None
        try:
            stat_result = os.stat(local_path)
        except (OSError, TypeError, ValueError):
            return None
        blocks = getattr(stat_result, "st_blocks", None)
        if blocks is None:
            return None
        try:
            return int(blocks) * 512
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def __summarize_snapshot_source(arbitration_source: str) -> str:
        if arbitration_source == "recent_live_snapshot":
            return "recent_live_snapshot"
        if arbitration_source == "retained_recent_live_snapshot":
            return "retained_recent_live_snapshot"
        if arbitration_source in (
            "retained_stopped_snapshot",
            "retained_stopped_snapshot_from_live_status",
            "retained_stopped_snapshot_without_live_progress",
            "live_status_coalesced_with_retained_floor",
        ):
            return "retained_stopped_snapshot"
        return "none"

    def __trace_cycle_event(self, event: str, payload: dict[str, object]) -> None:
        if not self.__is_stop_resume_trace_enabled():
            return
        resolved_identity = payload.get("resolved_identity")
        resolved_identity_dict = cast(Dict[object, object], resolved_identity) \
            if isinstance(resolved_identity, dict) else None
        file_id_value = payload.get("file_id")
        if not isinstance(file_id_value, str) and resolved_identity_dict is not None:
            file_id_value = resolved_identity_dict.get("file_id")
        file_id = file_id_value if isinstance(file_id_value, str) else None
        if file_id is None:
            return
        trace_payload: dict[str, object] = {
            "cycle": self.__stop_resume_trace_cycle_id,
            "event": event,
            "context": dict(self.__stop_resume_trace_cycle_context),
        }
        trace_payload.update(payload)
        signature_payload = dict(trace_payload)
        signature_payload.pop("cycle", None)
        try:
            signature = json.dumps(signature_payload, sort_keys=True, default=str)
        except Exception:
            signature = repr(signature_payload)
        signature_key = (file_id, event)
        previous_signature = self.__stop_resume_trace_last_signatures.get(signature_key)
        if previous_signature == "retained:" + signature:
            # Keep recently used keys near the tail while preserving one
            # signature per canonical file/event pair.
            self.__stop_resume_trace_last_signatures.move_to_end(signature_key)
            return
        breadcrumb = self.__stop_resume_trace_breadcrumb
        if breadcrumb is not None:
            final_model = payload.get("final_model")
            details: dict[str, object] = {
                "event": event,
                "cycle": self.__stop_resume_trace_cycle_id,
                "file_id": file_id,
                "context": dict(self.__stop_resume_trace_cycle_context),
                "update_context": payload.get(
                    "update_context",
                    dict(self.__stop_resume_trace_cycle_context),
                ),
            }
            safe_detail_keys = (
                "model_source",
                "update_context",
                "snapshot_source",
                "recent_snapshot_present",
                "retained_snapshot_present",
                "resolved_identity",
                "raw_lftp_status",
                "matched_local",
                "local_freshness",
                "local_data_role",
                "local_size_apparent",
                "local_size_allocated",
                "presence",
                "arbitration_source",
                "stopped",
            )
            for key in safe_detail_keys:
                if key in payload:
                    details[key] = payload[key]
            if isinstance(final_model, dict):
                details["final_model"] = final_model
            try:
                breadcrumb_stage = {
                    "arbitration": "model_arbitration",
                    "no_rebuild": "model_no_rebuild",
                    "target_not_rendered": "model_target_not_rendered",
                }.get(event, "model_trace")
                outcome = breadcrumb.record(
                    "model_builder",
                    "stop_resume_trace",
                    details,
                    stage=breadcrumb_stage,
                    event_type="diagnostic",
                    category="model.lifecycle",
                    level="info",
                    corr_id="stop-resume:{}:{}".format(
                        file_id,
                        self.__stop_resume_trace_cycle_id,
                    ),
                    file_id=file_id,
                    trace_scope="flow",
                    _coalesce_key=opaque_trace_correlation(signature),
                )
                self.__remember_trace_signature(signature_key, signature, outcome == "retained")
            except Exception:
                self.logger.debug("Ignoring stop/resume breadcrumb emission failure", exc_info=True)

    def __remember_trace_signature(
            self, signature_key: tuple[str, str], signature: str, retained: bool,
    ) -> None:
        """Bound signature bookkeeping; only retained entries suppress retries."""
        self.__stop_resume_trace_last_signatures[signature_key] = (
            "retained:" if retained else "enqueued:"
        ) + signature
        self.__stop_resume_trace_last_signatures.move_to_end(signature_key)
        while len(self.__stop_resume_trace_last_signatures) > self.__STOP_RESUME_TRACE_SIGNATURE_CACHE_SIZE:
            self.__stop_resume_trace_last_signatures.popitem(last=False)

    def __trace_target_arbitration(self,
                                   model_file: ModelFile,
                                   root_file_id: str,
                                   is_stopped: bool,
                                   remote_present: bool,
                                   local_present: bool,
                                   local: Optional[SystemFile],
                                   active_present: bool,
                                   local_freshness: str,
                                   status: Optional[LftpJobStatus],
                                   transfer_state: Optional[_TransferState],
                                   arbitration_source: str) -> None:
        if not self.__is_stop_resume_trace_enabled():
            return
        if not self.__is_relevant_trace_file(model_file, is_stopped):
            return
        self.__trace_cycle_event("arbitration", {
            "model_source": "rebuilt",
            "snapshot_source": ModelBuilder.__summarize_snapshot_source(arbitration_source),
            "local_freshness": local_freshness,
            "recent_snapshot_present": arbitration_source == "recent_live_snapshot",
            "retained_snapshot_present": arbitration_source in (
                "retained_recent_live_snapshot",
                "retained_stopped_snapshot",
                "retained_stopped_snapshot_from_live_status",
                "retained_stopped_snapshot_without_live_progress",
                "live_status_coalesced_with_retained_floor",
            ),
            "resolved_identity": {
                "file_id": model_file.file_id,
                "root_file_id": root_file_id,
                "path_pair_id": model_file.path_pair_id,
                "path_pair_name": model_file.path_pair_name,
            },
            "stopped": is_stopped,
            "raw_lftp_status": {
                "state": ModelBuilder.__enum_name(status.state) if status is not None else None,
                "job_id": status.id if status is not None else None,
                "file_id": status.file_id if status is not None else None,
                "transfer": ModelBuilder.__summarize_transfer_state(transfer_state),
            },
            "matched_local": {
                "present": local is not None,
                "is_staging": bool(getattr(local, "is_staging", False)) if local is not None else None,
            },
            "local_data_role": ModelBuilder.__summarize_local_data_role(
                model_file,
                local,
                transfer_state,
                arbitration_source,
            ),
            "local_size_apparent": local.size if local is not None else None,
            "local_size_allocated": self.__get_allocated_local_size(model_file, local),
            "presence": {
                "remote": remote_present,
                "local": local_present,
                "active": active_present,
            },
            "arbitration_source": arbitration_source,
            "final_model": ModelBuilder.__summarize_rendered_model(model_file),
        })

    @staticmethod
    def __is_authoritative_local_file(local_file: Optional[SystemFile]) -> bool:
        return local_file is not None and \
            not getattr(local_file, "is_staging", False) and \
            not getattr(local_file, "has_staging_collision", False)

    @staticmethod
    def __local_size_is_authoritative_progress(local_file: Optional[SystemFile],
                                               remote_file: Optional[SystemFile],
                                               retained_size_local: Optional[int]) -> bool:
        if local_file is None or not ModelBuilder.__is_authoritative_local_file(local_file):
            return False
        if retained_size_local is None:
            return False
        return local_file.size >= retained_size_local or \
            (remote_file is not None and local_file.size >= remote_file.size)

    @staticmethod
    def __local_file_proves_download_completion(local_file: Optional[SystemFile],
                                                remote_file: Optional[SystemFile]) -> bool:
        if local_file is None or not ModelBuilder.__is_authoritative_local_file(local_file):
            return False
        if remote_file is None:
            return False
        return local_file.size >= remote_file.size

    @staticmethod
    def __authoritative_remote_leaf_progress_bytes(
            remote_file: Optional[SystemFile],
            local_file: Optional[SystemFile]) -> int:
        """Count local bytes that belong to matching remote leaves only."""
        if remote_file is None or local_file is None or \
                remote_file.is_dir != local_file.is_dir or \
                getattr(local_file, "has_staging_collision", False):
            return 0
        if not remote_file.is_dir:
            if not ModelBuilder.__is_authoritative_local_file(local_file):
                return 0
            return min(local_file.size, remote_file.size)
        local_children = {child.name: child for child in local_file.iter_children()}
        return sum(
            ModelBuilder.__authoritative_remote_leaf_progress_bytes(
                remote_child, local_children.get(remote_child.name)
            )
            for remote_child in remote_file.iter_children()
        )

    @staticmethod
    def __local_transfer_snapshot_is_caught_up(
            local_file: Optional[SystemFile],
            remote_file: Optional[SystemFile],
            retained_size_local: Optional[int] = None) -> bool:
        """Whether local scan evidence can retire a transfer snapshot.

        Directory aggregate sizes include local-only leaves, so they cannot
        establish that a remote tree caught up.  Use the existing recursive
        completion proof when the snapshot's own node is a directory; retain
        the established size-based behavior for child and single-file nodes.
        """
        if remote_file is not None and remote_file.is_dir:
            if ModelBuilder.__effective_local_tree_proves_completion(
                    remote_file, local_file):
                return True
            return retained_size_local is not None and \
                ModelBuilder.__authoritative_remote_leaf_progress_bytes(
                    remote_file, local_file
                ) >= retained_size_local
        if retained_size_local is None:
            return ModelBuilder.__local_file_proves_download_completion(
                local_file, remote_file
            )
        return ModelBuilder.__local_size_is_authoritative_progress(
            local_file, remote_file, retained_size_local
        )

    @staticmethod
    def __trusted_final_leaf_bytes(remote_file: Optional[SystemFile],
                                   local_file: Optional[SystemFile],
                                   ancestor_has_staging_collision: bool = False) -> int:
        """Return the unique remote-covered bytes held below the final root.

        ``LocalScanner`` represents a split-root directory as one tree: final
        leaves are non-staging and incomplete leaves are staging.  A running
        backend reports only the latter, so directory progress needs the two
        disjoint sources.  The location tag, not a size comparison, decides
        whether a leaf is final-authoritative.
        """
        if remote_file is None or local_file is None or remote_file.is_dir != local_file.is_dir:
            return 0
        has_staging_collision = ancestor_has_staging_collision or \
            getattr(local_file, "has_staging_collision", False)
        if not remote_file.is_dir:
            if ModelBuilder.__leaf_matches_remote_queue_exclusion_identity(remote_file, local_file) and \
                    not has_staging_collision:
                return min(local_file.size, remote_file.size)
            return 0
        remote_children = {child.name: child for child in remote_file.iter_children()}
        return sum(
            ModelBuilder.__trusted_final_leaf_bytes(
                remote_children.get(child.name), child, has_staging_collision
            )
            for child in local_file.iter_children()
        )

    @staticmethod
    def __mtime_epoch_second(system_file: SystemFile) -> Optional[int]:
        """Return the finest portable mtime identity preserved by LFTP.

        LFTP mirrors source mtimes at whole-second resolution, even when the
        source filesystem reports nanoseconds.  Compare the raw epoch value at
        that portable precision rather than falling back to timezone-dependent
        display datetimes.
        """
        mtime_ns = system_file.mtime_ns
        if type(mtime_ns) is not int:
            return None
        return mtime_ns // 1_000_000_000

    @staticmethod
    def __is_verified_final_leaf(remote_file: SystemFile, local_file: SystemFile) -> bool:
        """Require positive content identity before trusting final-only data.

        A final pathname alone is not enough after an interrupted transfer:
        an older same-name file must remain eligible for download.  The scan
        carries size and source modification metadata, so require both exact
        size and equal non-null raw nanosecond mtimes.  Display timestamps are
        naive local datetimes and cannot establish identity across hosts in
        different timezones.  This deliberately fails closed for legacy scans
        without raw epoch provenance.
        """
        return not remote_file.is_dir and not local_file.is_dir and \
            ModelBuilder.__is_authoritative_local_file(local_file) and \
            ModelBuilder.__leaf_matches_remote_identity(remote_file, local_file)

    @staticmethod
    def __leaf_matches_remote_identity(remote_file: SystemFile, candidate_file: SystemFile) -> bool:
        """Whether a leaf has exact scanner-proven remote source identity."""
        local_mtime_ns = candidate_file.mtime_ns
        remote_mtime_ns = remote_file.mtime_ns
        return not remote_file.is_dir and not candidate_file.is_dir and \
            candidate_file.size == remote_file.size and \
            type(local_mtime_ns) is int and \
            type(remote_mtime_ns) is int and \
            local_mtime_ns == remote_mtime_ns

    @staticmethod
    def __leaf_matches_remote_queue_exclusion_identity(remote_file: SystemFile,
                                                       candidate_file: SystemFile) -> bool:
        """Whether a final leaf is safe to omit from a Queue transfer.

        Queue exclusions may account for LFTP's loss of source mtime
        precision at the final destination.  The local final leaf must still
        have the same size and whole epoch second as the remote leaf, and the
        local mtime must be exactly whole-second aligned.  Thus two distinct
        fractional scanner mtimes in one second remain downloadable.
        """
        local_mtime_ns = candidate_file.mtime_ns
        remote_mtime_ns = remote_file.mtime_ns
        if not remote_file.is_dir and not candidate_file.is_dir and \
                ModelBuilder.__is_authoritative_local_file(candidate_file) and \
                candidate_file.size == remote_file.size and \
                type(local_mtime_ns) is int and type(remote_mtime_ns) is int:
            return local_mtime_ns == remote_mtime_ns or \
                local_mtime_ns % 1_000_000_000 == 0 and \
                local_mtime_ns // 1_000_000_000 == remote_mtime_ns // 1_000_000_000
        return False

    @staticmethod
    def __leaf_matches_remote_collision_identity(remote_file: SystemFile,
                                                  candidate_file: SystemFile) -> bool:
        """Whether a collision leaf matches size and portable raw mtime.

        Remote scanner mtimes may retain fractional nanoseconds while LFTP's
        published final/staging leaves are second-precision.  Keep the raw
        epoch value (rather than display datetimes), but compare the finest
        precision common to both sides.  The controller's byte comparator is
        still required before any collided staging leaf is removed.
        """
        local_mtime_ns = candidate_file.mtime_ns
        remote_mtime_ns = remote_file.mtime_ns
        return not remote_file.is_dir and not candidate_file.is_dir and \
            candidate_file.size == remote_file.size and \
            type(local_mtime_ns) is int and \
            type(remote_mtime_ns) is int and \
            local_mtime_ns // 1_000_000_000 == remote_mtime_ns // 1_000_000_000

    @staticmethod
    def __combine_split_root_transfer_state(transfer_state: _TransferState,
                                            remote_file: Optional[SystemFile],
                                            local_file: Optional[SystemFile],
                                            previous_snapshot: Optional[_RecentLiveTransferSnapshot] = None,
                                            lftp_job_id: Optional[int] = None,
                                            lftp_job_type: Optional[LftpJobStatus.Type] = None) -> _TransferState:
        def has_staging_descendant(candidate: Optional[SystemFile]) -> bool:
            if candidate is None:
                return False
            if getattr(candidate, "is_staging", False):
                return True
            return any(has_staging_descendant(child) for child in candidate.iter_children())

        if remote_file is not None and transfer_state.size_remote is not None and \
                transfer_state.size_local is not None:
            # LFTP reports the currently selected resume subset, not always
            # the whole root. Classify a genuine zero reset before converting
            # the subset to root bytes: a new/reset job may report a partial
            # subset total while having transferred no current-lifecycle
            # bytes. For a continuing job, preserve the existing live floor
            # across changing subset totals without inventing another owner.
            remaining_size = max(0, min(transfer_state.size_remote, remote_file.size))
            subset_transferred = max(0, min(transfer_state.size_local, remaining_size))
            is_reset = subset_transferred == 0
            if is_reset:
                transfer_state = _TransferState(
                    0,
                    remote_file.size,
                    0,
                    transfer_state.speed,
                    transfer_state.eta,
                )
            elif lftp_job_type == LftpJobStatus.Type.MIRROR and \
                    (remaining_size != remote_file.size or transfer_state.size_remote > remote_file.size):
                # A running MIRROR directory discovers its children while it
                # runs.  Its denominator can therefore describe only the
                # currently known subset; treating omitted remote bytes as
                # already transferred produces a false whole-root progress
                # value.  The raw transferred bytes and verified final leaves
                # below are the only authorities available for this state.
                size_local = subset_transferred
                if previous_snapshot is not None and \
                        previous_snapshot.lftp_job_id == lftp_job_id and \
                        previous_snapshot.subset_size_local is not None:
                    size_local = max(size_local, previous_snapshot.subset_size_local)
                size_local = max(0, min(size_local, remote_file.size))
                transfer_state = _TransferState(
                    size_local,
                    remote_file.size,
                    int(round((size_local * 100) / remote_file.size))
                    if remote_file.size > 0 else None,
                    transfer_state.speed,
                    transfer_state.eta,
                )
            elif remaining_size != remote_file.size:
                size_local = remote_file.size - (remaining_size - subset_transferred)
                if previous_snapshot is not None and \
                        previous_snapshot.lftp_job_id == lftp_job_id and \
                        previous_snapshot.size_local is not None:
                    size_local = max(size_local, previous_snapshot.size_local)
                size_local = max(0, min(size_local, remote_file.size))
                return _TransferState(
                    size_local,
                    remote_file.size,
                    int(round((size_local * 100) / remote_file.size))
                    if remote_file.size > 0 else None,
                    transfer_state.speed,
                    transfer_state.eta,
                )
            elif transfer_state.size_remote > remote_file.size:
                size_local = subset_transferred
                if previous_snapshot is not None and \
                        previous_snapshot.lftp_job_id == lftp_job_id and \
                        previous_snapshot.size_local is not None:
                    size_local = max(size_local, previous_snapshot.size_local)
                size_local = max(0, min(size_local, remote_file.size))
                transfer_state = _TransferState(
                    size_local,
                    remote_file.size,
                    int(round((size_local * 100) / remote_file.size))
                    if remote_file.size > 0 else None,
                    transfer_state.speed,
                    transfer_state.eta,
                )
            elif previous_snapshot is not None and \
                    previous_snapshot.lftp_job_id == lftp_job_id and \
                    previous_snapshot.size_local is not None:
                # A continuing LFTP job can switch from reporting its
                # remaining subset to the exact whole-root total.  This is
                # neither a reset (handled above) nor permission to lower the
                # rendered whole-root floor just because the denominator
                # changed back to the root size.
                previous_floor = previous_snapshot.size_local
                if lftp_job_type == LftpJobStatus.Type.MIRROR:
                    # The stored whole-root value already includes verified
                    # final leaves.  Preserve only its raw subset component;
                    # final leaves are added once below.
                    previous_floor = previous_snapshot.subset_size_local
                floor_applied = previous_floor is not None and previous_floor > subset_transferred
                size_local = max(subset_transferred, previous_floor or 0)
                size_local = max(0, min(size_local, remote_file.size))
                transfer_state = _TransferState(
                    size_local,
                    remote_file.size,
                    int(round((size_local * 100) / remote_file.size))
                    if floor_applied and remote_file.size > 0
                    else transfer_state.percent_local,
                    transfer_state.speed,
                    transfer_state.eta,
                )

        if not has_staging_descendant(local_file):
            return transfer_state
        final_bytes = ModelBuilder.__trusted_final_leaf_bytes(remote_file, local_file)
        if final_bytes == 0:
            return transfer_state
        staged_bytes = transfer_state.size_local or 0
        size_local = final_bytes + staged_bytes
        if remote_file is not None:
            size_local = min(size_local, remote_file.size)
        percent_local = transfer_state.percent_local
        if remote_file is not None and remote_file.size > 0:
            percent_local = int(round((size_local * 100) / remote_file.size))
        return _TransferState(
            size_local,
            remote_file.size if remote_file is not None else transfer_state.size_remote,
            percent_local,
            transfer_state.speed,
            transfer_state.eta,
        )

    def get_trusted_final_leaf_paths(self, file_id: str) -> tuple[str, ...]:
        """Return root-relative final leaves safe to omit from Queue.

        Only paths present in both the current remote and local scan are
        returned.  This uses a Queue-specific final identity rule because
        LFTP can publish a source mtime at whole-second precision.  It does
        not establish completion, progress, or collision identity.
        """
        trace_enabled = self.__is_stop_resume_trace_enabled("queue.exclusion", "info")
        if not trace_enabled:
            return self.__get_trusted_final_leaf_paths_without_trace(file_id)
        remote_root = self.__remote_file(file_id)
        local_root = self.__local_file(file_id)
        counts = self.__queue_exclusion_trace_empty_counts() if trace_enabled else None
        mtime_counts = {
            "exact": 0,
            "raw_mismatch": 0,
            "portable_second_match": 0,
            "missing": 0,
        } if trace_enabled else None
        readiness_counts = {"local": 0, "remote": 0, "unknown": 0} if trace_enabled else None
        rejection_reasons = {
            "remote_only": 0,
            "local_only": 0,
            "type_mismatch": 0,
            "staging": 0,
            "collision": 0,
            "status_sidecar": 0,
            "size_mismatch": 0,
            "mtime_raw_mismatch": 0,
            "mtime_missing": 0,
            "not_ready": 0,
        } if trace_enabled else None

        def trace_increment(counter: Optional[dict[str, int]], key: str) -> None:
            if counter is not None:
                counter[key] += 1

        pair_id = self.__file_id_path_pair_id(file_id)
        unknown_local = pair_id in self.__unknown_local_path_pair_ids
        if remote_root is None:
            readiness_reason = "remote"
        elif local_root is None:
            readiness_reason = "unknown" if unknown_local else "local"
        elif unknown_local:
            readiness_reason = "unknown"
        else:
            readiness_reason = "local"
        # The updater keeps retained source snapshots visible while a local
        # or joint (local+remote) scan is incomplete.  Its one consolidated
        # unknown-pair overlay is the authority boundary for Queue: never
        # turn a retained stale leaf into a typed exclusion until that pair's
        # scan has completed authoritatively.
        if remote_root is None or local_root is None or not remote_root.is_dir or not local_root.is_dir:
            if trace_enabled:
                self.__record_queue_exclusion_trace(file_id, {
                    "schema": self.__QUEUE_EXCLUSION_TRACE_SCHEMA,
                    "readiness_reason": readiness_reason,
                    "readiness": {
                        "local_present": local_root is not None,
                        "remote_present": remote_root is not None,
                        "local_readiness_unknown": unknown_local,
                    },
                    "leaf_observations_present": self.__queue_trace_presence(counts),
                    "mtime_categories_present": self.__queue_trace_presence(mtime_counts),
                    "readiness_reasons": self.__queue_trace_presence(readiness_counts),
                    "decisions": {
                        "excluded": False,
                        "rejected": False,
                    },
                    "rejection_reasons": self.__queue_trace_presence(rejection_reasons),
                    "exclusions_present": False,
                })
            return ()

        if unknown_local:
            # Preserve the fail-closed authority boundary while still making
            # the reason and the currently visible tree explicit.
            if trace_enabled:
                def count_remote_leaves(remote_file: SystemFile) -> None:
                    if remote_file.is_dir:
                        for child in remote_file.iter_children():
                            count_remote_leaves(child)
                        return
                    trace_increment(counts, "remote_leaf_count")
                    trace_increment(counts, "remote_only_leaf_count")
                    trace_increment(mtime_counts, "missing")
                    trace_increment(readiness_counts, "unknown")
                    trace_increment(rejection_reasons, "not_ready")

                count_remote_leaves(remote_root)
                self.__record_queue_exclusion_trace(file_id, {
                    "schema": self.__QUEUE_EXCLUSION_TRACE_SCHEMA,
                    "readiness_reason": "unknown",
                    "readiness": {
                        "local_present": True,
                        "remote_present": True,
                        "local_readiness_unknown": True,
                    },
                    "leaf_observations_present": self.__queue_trace_presence(counts),
                    "mtime_categories_present": self.__queue_trace_presence(mtime_counts),
                    "readiness_reasons": self.__queue_trace_presence(readiness_counts),
                    "decisions": {
                        "excluded": False,
                        "rejected": counts["remote_leaf_count"] > 0,
                    },
                    "rejection_reasons": self.__queue_trace_presence(rejection_reasons),
                    "exclusions_present": False,
                })
            return ()

        paths: list[str] = []

        def count_local_only(local_file: SystemFile, ancestor_staging: bool = False) -> None:
            has_staging = ancestor_staging or local_file.is_staging
            if local_file.is_dir:
                for child in local_file.iter_children():
                    count_local_only(child, has_staging)
                return
            trace_increment(counts, "local_leaf_count")
            trace_increment(counts, "local_only_leaf_count")
            if has_staging:
                trace_increment(counts, "staging_sibling_leaf_count")
            trace_increment(rejection_reasons, "local_only")

        def count_remote_type_mismatch(remote_file: SystemFile) -> None:
            if remote_file.is_dir:
                for child in remote_file.iter_children():
                    count_remote_type_mismatch(child)
                return
            trace_increment(counts, "remote_leaf_count")
            trace_increment(counts, "type_mismatch_leaf_count")
            trace_increment(counts, "rejected_leaf_count")
            trace_increment(mtime_counts, "missing")
            trace_increment(readiness_counts, "unknown")
            trace_increment(rejection_reasons, "type_mismatch")

        def visit(remote_file: SystemFile, local_file: SystemFile, relative: str,
                  ancestor_has_staging_collision: bool = False) -> None:
            if remote_file.is_dir != local_file.is_dir:
                if trace_enabled:
                    count_remote_type_mismatch(remote_file)
                return
            has_staging_collision = ancestor_has_staging_collision or \
                getattr(local_file, "has_staging_collision", False)
            if not remote_file.is_dir:
                trace_increment(counts, "remote_leaf_count")
                trace_increment(counts, "local_leaf_count")
                if local_file.is_staging:
                    trace_increment(counts, "staging_candidate_leaf_count")
                mtime_category = self.__queue_exclusion_mtime_category(remote_file, local_file)
                trace_increment(mtime_counts, mtime_category)
                if mtime_category == "missing":
                    trace_increment(readiness_counts, "unknown")
                else:
                    trace_increment(readiness_counts, "local")
                size_matches = local_file.size == remote_file.size
                if not size_matches:
                    trace_increment(counts, "size_mismatch_leaf_count")
                if local_file.status_sidecar_ready:
                    trace_increment(counts, "status_sidecar_leaf_count")
                if has_staging_collision:
                    trace_increment(counts, "collision_rejected_leaf_count")
                    trace_increment(rejection_reasons, "collision")
                elif local_file.is_staging:
                    trace_increment(rejection_reasons, "staging")
                elif local_file.status_sidecar_ready:
                    trace_increment(rejection_reasons, "status_sidecar")
                elif not size_matches:
                    trace_increment(rejection_reasons, "size_mismatch")
                elif mtime_category == "missing":
                    trace_increment(rejection_reasons, "mtime_missing")
                elif mtime_category == "raw_mismatch":
                    trace_increment(rejection_reasons, "mtime_raw_mismatch")
                if ModelBuilder.__leaf_matches_remote_queue_exclusion_identity(
                    remote_file, local_file) and not has_staging_collision:
                    paths.append(relative)
                    trace_increment(counts, "excluded_leaf_count")
                else:
                    trace_increment(counts, "rejected_leaf_count")
                return
            remote_children = {child.name: child for child in remote_file.iter_children()}
            local_children = {child.name: child for child in local_file.iter_children()}
            for remote_child in remote_file.iter_children():
                local_child = local_children.get(remote_child.name)
                if local_child is None:
                    if trace_enabled:
                        def count_remote_only(remote_only: SystemFile) -> None:
                            if remote_only.is_dir:
                                for child in remote_only.iter_children():
                                    count_remote_only(child)
                                return
                            trace_increment(counts, "remote_leaf_count")
                            trace_increment(counts, "remote_only_leaf_count")
                            trace_increment(mtime_counts, "missing")
                            trace_increment(readiness_counts, "remote")
                            trace_increment(rejection_reasons, "remote_only")
                            trace_increment(counts, "rejected_leaf_count")
                        count_remote_only(remote_child)
                    continue
                child_relative = local_child.name if not relative else relative + "/" + local_child.name
                visit(remote_child, local_child, child_relative, has_staging_collision)
            for local_child_name, local_child in local_children.items():
                if local_child_name not in remote_children:
                    if trace_enabled:
                        count_local_only(local_child, has_staging_collision)

        visit(remote_root, local_root, "")
        paths = sorted(set(paths))
        if trace_enabled:
            self.__record_queue_exclusion_trace(file_id, {
                "schema": self.__QUEUE_EXCLUSION_TRACE_SCHEMA,
                "readiness_reason": readiness_reason,
                "readiness": {
                    "local_present": True,
                    "remote_present": True,
                    "local_readiness_unknown": False,
                },
                "leaf_observations_present": self.__queue_trace_presence(counts),
                "mtime_categories_present": self.__queue_trace_presence(mtime_counts),
                "readiness_reasons": self.__queue_trace_presence(readiness_counts),
                "decisions": {
                    "excluded": counts["excluded_leaf_count"] > 0,
                    "rejected": counts["rejected_leaf_count"] > 0,
                },
                "rejection_reasons": self.__queue_trace_presence(rejection_reasons),
                "exclusions_present": bool(paths),
            })
        return tuple(paths)

    def __get_trusted_final_leaf_paths_without_trace(self, file_id: str) -> tuple[str, ...]:
        """Preserve Queue's pre-trace traversal when diagnostics are disabled."""
        remote_root = self.__remote_file(file_id)
        local_root = self.__local_file(file_id)
        if remote_root is None or local_root is None or not remote_root.is_dir or not local_root.is_dir:
            return ()
        if self.__file_id_path_pair_id(file_id) in self.__unknown_local_path_pair_ids:
            return ()

        paths: list[str] = []

        def visit(remote_file: SystemFile, local_file: SystemFile, relative: str,
                  ancestor_has_staging_collision: bool = False) -> None:
            if remote_file.is_dir != local_file.is_dir:
                return
            has_staging_collision = ancestor_has_staging_collision or \
                getattr(local_file, "has_staging_collision", False)
            if not remote_file.is_dir:
                if ModelBuilder.__leaf_matches_remote_queue_exclusion_identity(
                        remote_file, local_file) and not has_staging_collision:
                    paths.append(relative)
                return
            remote_children = {child.name: child for child in remote_file.iter_children()}
            for local_child in local_file.iter_children():
                remote_child = remote_children.get(local_child.name)
                if remote_child is None:
                    continue
                child_relative = local_child.name if not relative else relative + "/" + local_child.name
                visit(remote_child, local_child, child_relative, has_staging_collision)

        visit(remote_root, local_root, "")
        return tuple(sorted(set(paths)))

    def has_complete_local_coverage(self, file_id: str) -> bool:
        """Whether the current remote/effective-local tree proves completion.

        This is intentionally narrower than model state: pending completion
        needs to recognize a fully staged directory after LFTP status has
        disappeared, while still rejecting collisions, partial leaves, and
        active-only paths that only inflate aggregate directory size.
        """
        remote_file = self.__remote_file(file_id)
        local_file = self.__build_effective_local_files().get(file_id)
        return self.__effective_local_tree_proves_completion(remote_file, local_file)

    def get_finalizable_staging_leaf_candidates(self) -> tuple[tuple[str, str], ...]:
        """Return exact-identity staging leaves safe to publish independently."""
        candidates: list[tuple[str, str]] = []

        def visit(remote_file: SystemFile, local_file: Optional[SystemFile], relative: str,
                  collision_ancestor: bool = False) -> None:
            if local_file is None or remote_file.is_dir != local_file.is_dir:
                return
            collision = collision_ancestor or bool(getattr(local_file, "has_staging_collision", False))
            if not remote_file.is_dir:
                if relative and "\\" not in relative and not collision and bool(getattr(local_file, "is_staging", False)) and \
                        not bool(getattr(local_file, "status_sidecar_ready", False)) and \
                        ModelBuilder.__leaf_matches_remote_collision_identity(remote_file, local_file):
                    candidates.append((root_file_id, relative))
                return
            local_children = {child.name: child for child in local_file.iter_children()}
            for remote_child in remote_file.iter_children():
                child_relative = remote_child.name if not relative else relative + "/" + remote_child.name
                visit(remote_child, local_children.get(remote_child.name), child_relative, collision)

        for root_file_id, remote_root in self.__remote_files().items():
            if not remote_root.is_dir:
                continue
            # Use only the authoritative local scanner tree. Active-transfer
            # presentation overlays must not authorize a physical move.
            local_root = self.__local_file(root_file_id)
            if local_root is not None:
                visit(remote_root, local_root, "")
        return tuple(sorted(candidates))

    def has_verified_complete_staging_remote_identity(self, file_id: str) -> bool:
        """Prove a pending automatic move has exact staged source identity.

        ``has_complete_local_coverage`` intentionally remains a compatibility
        predicate for existing lifecycle decisions: it may accept complete
        staging sizes without scanner mtimes.  A quiet automatic move
        has a narrower trust boundary.  Walk the current effective local tree
        against the current remote tree, require every staged leaf to match
        exact size and the portable whole-second raw mtime LFTP preserves,
        and reject every extra or collision.  Legitimate split roots may
        retain already-final leaves, but those require the stricter final-leaf
        identity and the root still needs at least one verified staged leaf.
        This derives proof from existing scan inputs only; it stores no new
        state.
        """
        return self.__trees_have_verified_complete_staging_remote_identity(
            self.__remote_file(file_id),
            self.__build_effective_local_files().get(file_id),
        )

    def has_verified_complete_staging_remote_identity_for_authoritative_pair(
            self, file_id: str, pair_build: _AuthoritativePairBuild,
    ) -> bool:
        """Apply the quiet-move proof to a staged final Path Pair source.

        A selected final pair is deliberately rendered before its source bucket
        is adopted.  Its candidate must therefore be able to prove the same
        physical staging identity from those staged scanner inputs, rather than
        consulting the unrelated live source bucket and waiting for a later
        incidental update.
        """
        if pair_build.path_pair_id in pair_build.unknown_local_path_pair_ids:
            return False
        return self.__trees_have_verified_complete_staging_remote_identity(
            pair_build.remote_files.get(file_id), pair_build.local_files.get(file_id),
        )

    @staticmethod
    def __trees_have_verified_complete_staging_remote_identity(
            remote_root: Optional[SystemFile], effective_root: Optional[SystemFile],
    ) -> bool:
        if remote_root is None or effective_root is None or \
                remote_root.is_dir != effective_root.is_dir:
            return False

        def visit(remote_file: SystemFile, effective_file: Optional[SystemFile]) -> tuple[bool, bool]:
            if effective_file is None or remote_file.is_dir != effective_file.is_dir or \
                    effective_file.has_staging_collision:
                return False, False
            if not remote_file.is_dir:
                # A parsed pget map can describe a logical full size while
                # the target is sparse, truncated, or otherwise not yet a
                # physical completed file.  The sidecar is resume metadata,
                # not completion proof: wait for LFTP to remove it and for a
                # subsequent physical scanner result to carry the leaf.
                if effective_file.status_sidecar_ready:
                    return False, False
                if getattr(effective_file, "is_staging", False):
                    return (
                        ModelBuilder.__leaf_matches_remote_collision_identity(
                            remote_file, effective_file
                        ),
                        True,
                    )
                # A final leaf omitted from Queue must contribute the same
                # portable LFTP identity to the root completion proof. Exact
                # identity remains required by split-root merge/collision
                # paths, which do not use this publication proof.
                return ModelBuilder.__leaf_matches_remote_queue_exclusion_identity(
                    remote_file, effective_file
                ), False
            remote_children = {child.name: child for child in remote_file.iter_children()}
            effective_children = {child.name: child for child in effective_file.iter_children()}
            if set(remote_children) != set(effective_children):
                return False, False
            saw_leaf = False
            for name, remote_child in remote_children.items():
                verified, child_saw_leaf = visit(remote_child, effective_children.get(name))
                if not verified:
                    return False, False
                saw_leaf = saw_leaf or child_saw_leaf
            return True, saw_leaf

        verified, saw_leaf = visit(remote_root, effective_root)
        return verified and saw_leaf

    def has_unresolved_staging_collision(self, file_id: str) -> bool:
        """Whether the effective root still has an unproven staging collision."""
        local_file = self.__build_effective_local_files().get(file_id)
        if local_file is None:
            return False
        return self.__has_staging_collision_descendant(local_file)

    def get_unresolved_staging_collision_file_ids(self) -> set[str]:
        """Return a copy of collision roots computed for the cached model."""
        if self.__cached_model is None:
            return set()
        return set(self.__cached_unresolved_staging_collision_file_ids)

    def get_terminalizable_staging_collision_file_ids(self) -> set[str]:
        """Return cached collision roots with complete remote leaf coverage."""
        if self.__cached_model is None:
            return set()
        return set(self.__cached_terminalizable_staging_collision_file_ids)

    def has_verified_staging_collision_remote_identity(self, file_id: str) -> bool:
        """Whether a cached terminal collision has exact remote leaf identity.

        ``get_terminalizable_staging_collision_file_ids`` deliberately uses
        apparent size only to classify complete collision roots.  A manual
        retry may invoke the byte comparator only after every remote-covered
        leaf below the collision is also backed by scanner identity: exact
        size and raw epoch mtime at the portable whole-second precision LFTP
        preserves.  If an active scan supplies the same leaf, it is evidence
        for the current staging tree and must carry that identity too.
        Missing or malformed metadata fails closed; byte equality remains the
        final proof before staging cleanup.
        """
        if self.__cached_model is None or file_id not in self.__cached_terminalizable_staging_collision_file_ids:
            return False
        remote_root = self.__remote_file(file_id)
        effective_root = self.__build_effective_local_files().get(file_id)
        active_root = self.__active_files.get(file_id)
        if remote_root is None or effective_root is None or remote_root.is_dir != effective_root.is_dir:
            return False

        def visit(
                remote_file: SystemFile,
                effective_file: Optional[SystemFile],
                active_file: Optional[SystemFile],
                ancestor_has_collision: bool = False,
        ) -> bool:
            if effective_file is None or remote_file.is_dir != effective_file.is_dir:
                return False
            has_collision = ancestor_has_collision or effective_file.has_staging_collision
            if remote_file.is_dir:
                effective_children = {child.name: child for child in effective_file.iter_children()}
                active_children = {
                    child.name: child for child in active_file.iter_children()
                } if active_file is not None and active_file.is_dir else {}
                if active_file is not None and not active_file.is_dir:
                    return False
                return all(
                    visit(
                        remote_child,
                        effective_children.get(remote_child.name),
                        active_children.get(remote_child.name) if active_file is not None else None,
                        has_collision,
                    )
                    for remote_child in remote_file.iter_children()
                )
            if not has_collision:
                return True
            if not ModelBuilder.__leaf_matches_remote_collision_identity(remote_file, effective_file):
                return False
            return active_file is None or ModelBuilder.__leaf_matches_remote_collision_identity(
                remote_file, active_file
            )

        return visit(remote_root, effective_root, active_root)

    def get_staging_collision_relative_paths(self, file_id: str) -> tuple[str, ...]:
        """Return cached remote-relative leaves covered by collision metadata."""
        if self.__cached_model is None or file_id not in self.__cached_unresolved_staging_collision_file_ids:
            return ()
        effective_root = self.__build_effective_local_files().get(file_id)
        if effective_root is None:
            return ()
        paths: list[str] = []

        def visit(system_file: SystemFile, relative: str, ancestor_has_collision: bool = False) -> None:
            has_collision = ancestor_has_collision or system_file.has_staging_collision
            if system_file.is_dir:
                for child in system_file.iter_children():
                    child_relative = child.name if not relative else relative + "/" + child.name
                    visit(child, child_relative, has_collision)
            elif has_collision:
                paths.append(relative)

        visit(effective_root, "")
        return tuple(sorted(set(paths)))

    def is_remote_leaf_path(self, file_id: str, relative_path: str) -> bool:
        """Whether a source-root-relative leaf is currently remote-listed."""
        remote_file = self.__remote_file(file_id)
        if remote_file is None or not relative_path:
            return False
        current = remote_file
        for component in relative_path.split("/"):
            if not current.is_dir:
                return False
            current = next((child for child in current.iter_children() if child.name == component), None)
            if current is None:
                return False
        return not current.is_dir

    @staticmethod
    def __has_incomplete_remote_file_children(model_file: ModelFile) -> bool:
        frontier = deque(model_file.iter_children())
        while frontier:
            child_file = frontier.popleft()
            if child_file.is_dir:
                frontier.extend(child_file.iter_children())
            elif child_file.remote_size is not None and \
                    (child_file.local_size is None or child_file.local_size < child_file.remote_size):
                return True
        return False

    @staticmethod
    def __has_remote_transferable_content(remote_file: Optional[SystemFile]) -> bool:
        """Return whether a remote node contains a transferable file.

        Directory metadata alone is not transferable content. The remote scan
        has already been filtered for exclusions before it reaches the builder,
        so this recursive check also naturally ignores excluded descendants.
        A file is content even when its size is zero.
        """
        if remote_file is None:
            return False
        if not remote_file.is_dir:
            return True
        return any(
            ModelBuilder.__has_remote_transferable_content(child)
            for child in remote_file.iter_children()
        )

    @staticmethod
    def __normalize_download_progress(percent_local: Optional[int | float]) -> Optional[int]:
        if percent_local is None:
            return None
        if type(percent_local) == float:
            # Treat fractional values below 1.0 as 0-1 progress fractions.
            # Keep an exact 1.0 as a literal 1% reading rather than 100%.
            if percent_local < 1:
                return int(round(percent_local * 100))
            return int(round(percent_local))
        return int(percent_local)

    @staticmethod
    def __transfer_state(value: object) -> _TransferState:
        if not isinstance(value, tuple):
            raise ModelError("Invalid transfer state")
        transfer_tuple = cast(tuple[object, ...], value)
        if len(transfer_tuple) != 5:
            raise ModelError("Invalid transfer state")
        size_local, size_remote, percent_local, speed, eta = transfer_tuple
        if size_local is not None and not isinstance(size_local, int):
            raise ModelError("Invalid local transfer size")
        if size_remote is not None and not isinstance(size_remote, int):
            raise ModelError("Invalid remote transfer size")
        if percent_local is not None and not isinstance(percent_local, (int, float)):
            raise ModelError("Invalid transfer progress")
        if speed is not None and not isinstance(speed, int):
            raise ModelError("Invalid transfer speed")
        if eta is not None and not isinstance(eta, int):
            raise ModelError("Invalid transfer ETA")
        return _TransferState(size_local, size_remote, percent_local, speed, eta)

    @staticmethod
    def __remote_indicates_newer_content(local_file: Optional[SystemFile],
                                         remote_file: Optional[SystemFile]) -> bool:
        if local_file is None or remote_file is None:
            return False
        if not ModelBuilder.__local_file_proves_download_completion(local_file, remote_file):
            return True
        # Display timestamps are naive local datetimes.  They cannot establish
        # ordering between the seedbox and controller when their timezones
        # differ, so retain the authoritative final file unless raw epoch
        # provenance proves the remote content is newer.
        local_mtime_second = ModelBuilder.__mtime_epoch_second(local_file)
        remote_mtime_second = ModelBuilder.__mtime_epoch_second(remote_file)
        if local_mtime_second is None or remote_mtime_second is None:
            return False
        return remote_mtime_second > local_mtime_second

    def __store_recent_live_transfer_snapshot(self,
                                              file_id: str,
                                              root_file_id: str,
                                              transfer_state: _TransferState,
                                              raw_transfer_state: Optional[_TransferState] = None,
                                              lftp_job_id: Optional[int] = None) -> None:
        snapshot = _RecentLiveTransferSnapshot(
            root_file_id=root_file_id,
            size_local=transfer_state.size_local,
            percent_local=ModelBuilder.__normalize_download_progress(transfer_state.percent_local),
            speed=transfer_state.speed,
            eta=transfer_state.eta,
            lftp_job_id=lftp_job_id,
            subset_size_local=raw_transfer_state.size_local if raw_transfer_state is not None else None,
            subset_size_remote=raw_transfer_state.size_remote if raw_transfer_state is not None else None,
        )
        if snapshot.size_local is None:
            return
        self.__recent_live_transfer_snapshots[file_id] = snapshot

    def __store_retained_stopped_transfer_snapshot(self,
                                                   file_id: str,
                                                   root_file_id: str,
                                                   transfer_state: _TransferState,
                                                   raw_transfer_state: Optional[_TransferState] = None,
                                                   lftp_job_id: Optional[int] = None) -> None:
        snapshot = _RecentLiveTransferSnapshot(
            root_file_id=root_file_id,
            size_local=transfer_state.size_local,
            percent_local=ModelBuilder.__normalize_download_progress(transfer_state.percent_local),
            speed=transfer_state.speed,
            eta=transfer_state.eta,
            lftp_job_id=lftp_job_id,
            subset_size_local=raw_transfer_state.size_local if raw_transfer_state is not None else None,
            subset_size_remote=raw_transfer_state.size_remote if raw_transfer_state is not None else None,
        )
        if snapshot.size_local is None:
            return
        self.__retained_stopped_transfer_snapshots[file_id] = snapshot

    @staticmethod
    def __candidate_snapshot_root_aliases(root_file_id: Optional[str]) -> List[str]:
        if root_file_id is None:
            return []
        alias_candidates: list[str] = [root_file_id]
        try:
            parsed_root = json.loads(root_file_id)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed_root = None
        if isinstance(parsed_root, list):
            parsed_items = cast(list[object], parsed_root)
            if len(parsed_items) == 2 and isinstance(parsed_items[1], str):
                alias_candidates.append(parsed_items[1])
        return list(dict.fromkeys(alias_candidates))

    @staticmethod
    def __get_transfer_snapshot_alias_keys(snapshot_store: dict[str, _RecentLiveTransferSnapshot],
                                           root_file_id: Optional[str],
                                           excluded_keys: Optional[Set[str]] = None) -> List[str]:
        root_aliases = ModelBuilder.__candidate_snapshot_root_aliases(root_file_id)
        if not root_aliases:
            return []
        excluded_keys = excluded_keys if excluded_keys is not None else set()
        alias_keys: list[str] = []
        for root_alias in root_aliases:
            if root_alias not in excluded_keys and root_alias in snapshot_store:
                alias_keys.append(root_alias)
        for stored_file_id, snapshot in snapshot_store.items():
            if stored_file_id in excluded_keys or stored_file_id in root_aliases:
                continue
            if snapshot.root_file_id in root_aliases:
                alias_keys.append(stored_file_id)
        return list(dict.fromkeys(alias_keys))

    def __resolve_transfer_snapshot(self,
                                    snapshot_store: dict[str, _RecentLiveTransferSnapshot],
                                    file_id: str,
                                    root_file_id: Optional[str] = None
                                    ) -> Tuple[Optional[str], Optional[_RecentLiveTransferSnapshot]]:
        snapshot = snapshot_store.get(file_id)
        if snapshot is not None:
            return file_id, snapshot
        if root_file_id is None:
            return None, None
        alias_keys = self.__get_transfer_snapshot_alias_keys(snapshot_store, root_file_id, {file_id})
        if len(alias_keys) == 1:
            alias_key = alias_keys[0]
            return alias_key, snapshot_store.get(alias_key)
        return None, None

    @staticmethod
    def __promote_transfer_snapshot(snapshot_store: dict[str, _RecentLiveTransferSnapshot],
                                    resolved_file_id: Optional[str],
                                    canonical_file_id: str,
                                    canonical_root_file_id: Optional[str]
                                    ) -> Optional[_RecentLiveTransferSnapshot]:
        if resolved_file_id is None:
            return None
        snapshot = snapshot_store.get(resolved_file_id)
        if snapshot is None:
            return None
        if canonical_root_file_id is not None:
            snapshot.root_file_id = canonical_root_file_id
        if resolved_file_id != canonical_file_id:
            snapshot_store.pop(resolved_file_id, None)
            snapshot_store[canonical_file_id] = snapshot
        return snapshot

    def __resolve_retained_stopped_transfer_snapshot(
            self,
            file_id: str,
            root_file_id: Optional[str] = None) -> Tuple[Optional[str], Optional[_RecentLiveTransferSnapshot]]:
        resolved_file_id, snapshot = self.__resolve_transfer_snapshot(
            self.__retained_stopped_transfer_snapshots,
            file_id,
            root_file_id
        )
        promoted_snapshot = self.__promote_transfer_snapshot(
            self.__retained_stopped_transfer_snapshots,
            resolved_file_id,
            file_id,
            root_file_id
        )
        if promoted_snapshot is not None:
            return file_id, promoted_snapshot
        return resolved_file_id, snapshot

    def __resolve_recent_live_transfer_snapshot(
            self,
            file_id: str,
            root_file_id: Optional[str] = None) -> Tuple[Optional[str], Optional[_RecentLiveTransferSnapshot]]:
        resolved_file_id, snapshot = self.__resolve_transfer_snapshot(
            self.__recent_live_transfer_snapshots,
            file_id,
            root_file_id
        )
        promoted_snapshot = self.__promote_transfer_snapshot(
            self.__recent_live_transfer_snapshots,
            resolved_file_id,
            file_id,
            root_file_id
        )
        if promoted_snapshot is not None:
            return file_id, promoted_snapshot
        return resolved_file_id, snapshot

    def __evict_retained_stopped_transfer_snapshots(self,
                                                    resolved_file_id: str,
                                                    root_file_id: Optional[str] = None) -> None:
        self.__retained_stopped_transfer_snapshots.pop(resolved_file_id, None)

    def __evict_transfer_completion_snapshots(self,
                                              file_id: str,
                                              root_file_id: Optional[str] = None) -> None:
        recent_snapshot_key, _ = self.__resolve_recent_live_transfer_snapshot(file_id, root_file_id)
        if recent_snapshot_key is not None:
            self.__recent_live_transfer_snapshots.pop(recent_snapshot_key, None)

        retained_snapshot_key, _ = self.__resolve_retained_stopped_transfer_snapshot(file_id, root_file_id)
        if retained_snapshot_key is not None:
            self.__retained_stopped_transfer_snapshots.pop(retained_snapshot_key, None)

    @staticmethod
    def __resolve_root_file_id(file_id: str,
                               root_remote: Optional[SystemFile],
                               root_local: Optional[SystemFile]) -> str:
        if root_remote is not None:
            return ModelBuilder.__root_file_id(root_remote.name, root_remote.path_pair_id)
        if root_local is not None:
            return ModelBuilder.__root_file_id(root_local.name, root_local.path_pair_id)
        return file_id

    @staticmethod
    def __build_retained_transfer_state(size_local: Optional[int],
                                        size_remote: Optional[int],
                                        percent_local: Optional[int | float]) -> Optional[_TransferState]:
        if size_local is None:
            return None
        return _TransferState(
            size_local,
            size_remote,
            ModelBuilder.__normalize_download_progress(percent_local),
            None,
            None
        )

    def __sweep_recent_live_transfer_snapshots(self, seen_file_ids: Optional[Set[str]] = None) -> None:
        for file_id, snapshot in list(self.__recent_live_transfer_snapshots.items()):
            root_status = self.__lftp_statuses.get(snapshot.root_file_id)
            if self.__is_stopped_file(file_id) or self.__is_stopped_file(snapshot.root_file_id, status=root_status):
                if seen_file_ids is not None and file_id not in seen_file_ids:
                    self.__recent_live_transfer_snapshots.pop(file_id, None)
                continue
            if self.__lftp_statuses.get(snapshot.root_file_id) is not None:
                continue
            if seen_file_ids is not None and file_id not in seen_file_ids:
                self.__recent_live_transfer_snapshots.pop(file_id, None)

    def __evict_recent_live_transfer_snapshots(self, root_file_id: str) -> None:
        for file_id, snapshot in list(self.__recent_live_transfer_snapshots.items()):
            if snapshot.root_file_id == root_file_id:
                self.__recent_live_transfer_snapshots.pop(file_id, None)

    def __has_pending_recent_live_transfer_snapshots(self) -> bool:
        for file_id, snapshot in self.__recent_live_transfer_snapshots.items():
            root_status = self.__lftp_statuses.get(snapshot.root_file_id)
            if self.__is_stopped_file(file_id) or self.__is_stopped_file(snapshot.root_file_id, status=root_status):
                continue
            if self.__lftp_statuses.get(snapshot.root_file_id) is None:
                return True
        return False

    def __get_recent_live_transfer_state(self,
                                         file_id: str,
                                         remote: Optional[SystemFile],
                                         local: Optional[SystemFile],
                                         root_remote: Optional[SystemFile] = None,
                                         root_local: Optional[SystemFile] = None) -> Optional[_TransferState]:
        root_file_id = self.__resolve_root_file_id(file_id, root_remote, root_local)
        resolved_file_id, snapshot = self.__resolve_recent_live_transfer_snapshot(file_id, root_file_id)
        if snapshot is None:
            return None
        root_status = self.__lftp_statuses.get(snapshot.root_file_id)
        stop_remote = root_remote if root_remote is not None else remote
        stop_local = root_local if root_local is not None else local
        if self.__is_stopped_file(snapshot.root_file_id, stop_remote, stop_local, root_status):
            self.__recent_live_transfer_snapshots.pop(
                resolved_file_id if resolved_file_id is not None else file_id,
                None
            )
            return None
        if self.__lftp_statuses.get(snapshot.root_file_id) is not None:
            return None
        if remote is None or local is None or snapshot.size_local is None:
            self.__recent_live_transfer_snapshots.pop(
                resolved_file_id if resolved_file_id is not None else file_id,
                None
            )
            return None
        if self.__local_transfer_snapshot_is_caught_up(local, remote, snapshot.size_local):
            self.__recent_live_transfer_snapshots.pop(
                resolved_file_id if resolved_file_id is not None else file_id,
                None
            )
            return None

        return _TransferState(
            snapshot.size_local,
            remote.size,
            snapshot.percent_local,
            snapshot.speed,
            snapshot.eta
        )

    @staticmethod
    def __has_clear_transfer_reset_signal(local: Optional[SystemFile],
                                          current_transfer_state: _TransferState,
                                          retained_snapshot: _RecentLiveTransferSnapshot) -> bool:
        # LFTP formats positive sub-1% byte reports as 0%.  Only a confirmed
        # zero-byte report can reset a retained same-lifecycle progress floor.
        return current_transfer_state.size_local == 0

    def __coalesce_retained_stopped_transfer_state(self,
                                                   file_id: str,
                                                   root_file_id: Optional[str],
                                                   remote: Optional[SystemFile],
                                                   local: Optional[SystemFile],
                                                   current_transfer_state: _TransferState,
                                                   raw_transfer_state: Optional[_TransferState] = None,
                                                   lftp_job_id: Optional[int] = None,
                                                   lftp_job_type: Optional[LftpJobStatus.Type] = None,
                                                   ) -> _TransferState:
        retained_snapshot_key, retained_snapshot = self.__resolve_retained_stopped_transfer_snapshot(
            file_id,
            root_file_id
        )
        if retained_snapshot is None or retained_snapshot.size_local is None:
            return current_transfer_state
        if lftp_job_type == LftpJobStatus.Type.MIRROR and raw_transfer_state is not None:
            # The combined state includes verified final leaves, so a raw zero
            # report must reach this boundary before it is turned into the
            # final-only value. A replacement MIRROR job is also a new
            # lifecycle and cannot inherit a stopped floor from the old one.
            if raw_transfer_state.size_local == 0 or (
                    retained_snapshot.lftp_job_id is not None and
                    lftp_job_id is not None and
                    retained_snapshot.lftp_job_id != lftp_job_id
            ):
                self.__evict_retained_stopped_transfer_snapshots(
                    retained_snapshot_key if retained_snapshot_key is not None else file_id,
                    retained_snapshot.root_file_id
                )
                return current_transfer_state
        if self.__has_clear_transfer_reset_signal(local, current_transfer_state, retained_snapshot):
            self.__evict_retained_stopped_transfer_snapshots(
                retained_snapshot_key if retained_snapshot_key is not None else file_id,
                retained_snapshot.root_file_id
            )
            return current_transfer_state
        current_percent = ModelBuilder.__normalize_download_progress(current_transfer_state.percent_local)
        retained_percent = retained_snapshot.percent_local
        size_has_caught_up = current_transfer_state.size_local is not None and \
            current_transfer_state.size_local >= retained_snapshot.size_local
        percent_has_caught_up = retained_percent is None or \
            (current_percent is not None and current_percent >= retained_percent)
        if size_has_caught_up and percent_has_caught_up:
            self.__evict_retained_stopped_transfer_snapshots(
                retained_snapshot_key if retained_snapshot_key is not None else file_id,
                retained_snapshot.root_file_id
            )
            return current_transfer_state
        coalesced_size_local = retained_snapshot.size_local
        if size_has_caught_up:
            coalesced_size_local = current_transfer_state.size_local
        coalesced_percent_local = retained_percent
        if percent_has_caught_up:
            coalesced_percent_local = current_percent
        return _TransferState(
            coalesced_size_local,
            remote.size if remote is not None else current_transfer_state.size_remote,
            coalesced_percent_local,
            current_transfer_state.speed,
            current_transfer_state.eta
        )

    def __get_retained_stopped_transfer_state_without_live_progress(
            self,
            file_id: str,
            root_file_id: Optional[str],
            remote: Optional[SystemFile],
            local: Optional[SystemFile],
            preserve_when_local_growth_only: bool = False) -> Optional[_TransferState]:
        retained_snapshot_key, retained_snapshot = self.__resolve_retained_stopped_transfer_snapshot(
            file_id,
            root_file_id
        )
        if retained_snapshot is None or retained_snapshot.size_local is None:
            return None
        if self.__local_transfer_snapshot_is_caught_up(local, remote):
            self.__evict_transfer_completion_snapshots(
                file_id,
                root_file_id
            )
            return None
        if local is not None and ModelBuilder.__is_authoritative_local_file(local):
            if local.size == 0:
                self.__evict_retained_stopped_transfer_snapshots(
                    retained_snapshot_key if retained_snapshot_key is not None else file_id,
                    retained_snapshot.root_file_id
                )
                return None
            if local.size > retained_snapshot.size_local and \
                    not preserve_when_local_growth_only and \
                    not (remote is not None and remote.is_dir):
                return None
        return self.__build_retained_transfer_state(
            retained_snapshot.size_local,
            remote.size if remote is not None else None,
            retained_snapshot.percent_local
        )

    def __get_retained_recent_transfer_state(self,
                                             file_id: str,
                                             remote: Optional[SystemFile],
                                             local: Optional[SystemFile],
                                             root_remote: Optional[SystemFile] = None,
                                             root_local: Optional[SystemFile] = None) -> Optional[_TransferState]:
        root_file_id = self.__resolve_root_file_id(file_id, root_remote, root_local)
        resolved_file_id, snapshot = self.__resolve_recent_live_transfer_snapshot(file_id, root_file_id)
        if snapshot is None:
            return None
        if snapshot.size_local is None:
            self.__recent_live_transfer_snapshots.pop(
                resolved_file_id if resolved_file_id is not None else file_id,
                None
            )
            return None
        if self.__local_transfer_snapshot_is_caught_up(local, remote):
            self.__evict_transfer_completion_snapshots(file_id, root_file_id)
            return None
        if remote is None:
            return self.__build_retained_transfer_state(snapshot.size_local, None, snapshot.percent_local)
        return self.__build_retained_transfer_state(snapshot.size_local, remote.size, snapshot.percent_local)

    @staticmethod
    def __staged_remote_leaf_bytes(
            remote_file: Optional[SystemFile],
            local_file: Optional[SystemFile]) -> int:
        """Count staged bytes only on remote-matching leaves."""
        if remote_file is None or local_file is None or \
                remote_file.is_dir != local_file.is_dir or \
                getattr(local_file, "has_staging_collision", False):
            return 0
        if not remote_file.is_dir:
            if not getattr(local_file, "is_staging", False) or \
                    not getattr(local_file, "status_sidecar_ready", False):
                return 0
            return min(local_file.size, remote_file.size)
        local_children = {child.name: child for child in local_file.iter_children()}
        return sum(
            ModelBuilder.__staged_remote_leaf_bytes(
                remote_child,
                local_children.get(remote_child.name),
            )
            for remote_child in remote_file.iter_children()
        )

    def __get_stopped_staging_transfer_state_without_live_progress(
            self,
            remote: Optional[SystemFile],
            local: Optional[SystemFile]) -> Optional[_TransferState]:
        """Reconstruct stopped directory progress from the current staging scan."""
        if remote is None or local is None or not remote.is_dir or not local.is_dir:
            return None
        if ModelBuilder.__effective_local_tree_proves_completion(remote, local):
            return None
        staged_bytes = ModelBuilder.__staged_remote_leaf_bytes(remote, local)
        final_bytes = ModelBuilder.__trusted_final_leaf_bytes(remote, local)
        if staged_bytes == 0 and final_bytes == 0:
            return None

        staged_state = _TransferState(
            staged_bytes,
            remote.size,
            int(round((staged_bytes * 100) / remote.size)) if remote.size > 0 else None,
            None,
            None,
        )
        if ModelBuilder.__has_staging_descendant(local):
            return ModelBuilder.__combine_split_root_transfer_state(
                staged_state, remote, local,
            )
        return _TransferState(
            final_bytes,
            remote.size,
            int(round((final_bytes * 100) / remote.size)) if remote.size > 0 else None,
            None,
            None,
        )

    def __promote_recent_live_transfer_snapshot_to_stopped_floor(
            self,
            file_id: str,
            root_file_id: Optional[str]) -> None:
        _, snapshot = self.__resolve_recent_live_transfer_snapshot(
            file_id,
            root_file_id
        )
        if snapshot is None:
            return
        # Keep the recent-live copy available for the existing no-status
        # resume window; the retained copy provides the stopped floor once a
        # queued or running status returns.
        self.__retained_stopped_transfer_snapshots[file_id] = snapshot

    def set_active_files(self, active_files: List[SystemFile]) -> None:
        started_at = self.__begin_duration(DURATION_MODEL_BUILDER_SET_ACTIVE_FILES)
        try:
            previous_active_files = self.__active_files
            next_active_file_ids: set[str] = set()
            next_active_files: dict[str, SystemFile] = {}
            for file in active_files:
                if file.path_pair_id is None:
                    matching_pair_ids = {
                        status.path_pair_id
                        for status in self.__lftp_statuses.values()
                        if status.name == file.name and status.path_pair_id is not None
                    }
                    if len(matching_pair_ids) == 1:
                        file.path_pair_id = next(iter(matching_pair_ids))
                # Active scanners read the staging path.  Some scanner paths do
                # not carry that location tag into their SystemFile roots, so make
                # the semantic boundary explicit here without changing sidecar or
                # collision metadata.
                self.__mark_active_tree_staging(file)
                self.__collect_active_file_ids(file, next_active_file_ids)
                next_active_files[self.__root_file_id(file.name, file.path_pair_id)] = file
            if next_active_files != previous_active_files:
                self.__active_files = next_active_files
                self.__active_file_ids = next_active_file_ids
                touched_file_ids = {
                    file_id for file_id in set(previous_active_files).union(next_active_files)
                    if previous_active_files.get(file_id) != next_active_files.get(file_id)
                }
                for file_id in touched_file_ids:
                    self.__update_active_only_name_count(file_id)
                self.__invalidate_cache(
                    MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
                    touched_active_root_file_ids=touched_file_ids,
                )
        finally:
            self.__finish_duration(DURATION_MODEL_BUILDER_SET_ACTIVE_FILES, started_at)

    def evict_active_file_ids(self, file_ids: Set[str]) -> None:
        """Drop exact active-scan roots during a completed move handoff."""
        if not file_ids:
            return
        removed_active_file_ids = set(self.__active_files).intersection(file_ids)
        retained_active_files = {
            root_file_id: active_file
            for root_file_id, active_file in self.__active_files.items()
            if root_file_id not in file_ids
        }
        if len(retained_active_files) == len(self.__active_files):
            return
        self.__active_files = retained_active_files
        self.__active_file_ids = set()
        for active_file in retained_active_files.values():
            self.__collect_active_file_ids(active_file, self.__active_file_ids)
        self.__invalidate_cache(
            MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
            touched_active_root_file_ids=removed_active_file_ids,
        )
        for file_id in removed_active_file_ids:
            self.__update_active_only_name_count(file_id)

    def confirm_local_deletions(self, file_ids: Set[str]) -> None:
        """Apply exact local absence after a successful delete process.

        The delete worker is authoritative for the concrete target it just
        removed. Reflect that bounded mutation immediately instead of keeping
        stale local/active transfer snapshots until an unrelated active-scan
        cadence happens to evict them. The targeted local rescan still runs as
        the filesystem confirmation path.
        """
        changed_file_ids: set[str] = set()
        for file_id in file_ids:
            pair_id = self.__file_id_path_pair_id(file_id)
            local_bucket = self.__local_files_by_pair.get(pair_id)
            if local_bucket is not None and local_bucket.pop(file_id, None) is not None:
                changed_file_ids.add(file_id)
                if not local_bucket:
                    self.__local_files_by_pair.pop(pair_id, None)
            if self.__active_files.pop(file_id, None) is not None:
                changed_file_ids.add(file_id)
            for snapshots in (
                self.__recent_live_transfer_snapshots,
                self.__retained_stopped_transfer_snapshots,
            ):
                for snapshot_id, snapshot in list(snapshots.items()):
                    if snapshot_id == file_id or snapshot.root_file_id == file_id:
                        snapshots.pop(snapshot_id, None)
                        changed_file_ids.add(file_id)
        if not changed_file_ids:
            return
        self.__active_file_ids = set()
        for active_file in self.__active_files.values():
            self.__collect_active_file_ids(active_file, self.__active_file_ids)
        self.__refresh_source_name_counts()
        self.__refresh_local_root_name_counts()
        self.__invalidate_cache(
            MODEL_BUILDER_INVALIDATION_CONFIRMED_LOCAL_DELETIONS,
            affected_file_ids=changed_file_ids,
        )

    @staticmethod
    def __mark_active_tree_staging(system_file: SystemFile) -> None:
        system_file.is_staging = True
        for child in system_file.iter_children():
            ModelBuilder.__mark_active_tree_staging(child)

    @staticmethod
    def __has_staging_descendant(system_file: SystemFile) -> bool:
        return getattr(system_file, "is_staging", False) or any(
            ModelBuilder.__has_staging_descendant(child)
            for child in system_file.iter_children()
        )

    @staticmethod
    def __has_staging_collision_descendant(system_file: SystemFile) -> bool:
        return system_file.has_staging_collision or any(
            ModelBuilder.__has_staging_collision_descendant(child)
            for child in system_file.iter_children()
        )

    @staticmethod
    def __clone_system_file(system_file: SystemFile,
                            children: Optional[List[SystemFile]] = None,
                            is_staging: Optional[bool] = None) -> SystemFile:
        cloned = SystemFile(
            system_file.name,
            sum(child.size for child in children) if children is not None else system_file.size,
            system_file.is_dir,
            time_created=system_file.timestamp_created,
            time_modified=system_file.timestamp_modified,
            is_staging=system_file.is_staging if is_staging is None else is_staging,
            mtime_ns=system_file.mtime_ns,
        )
        cloned.path_pair_id = system_file.path_pair_id
        cloned.path_pair_name = system_file.path_pair_name
        cloned.status_sidecar_ready = system_file.status_sidecar_ready
        cloned.has_staging_collision = system_file.has_staging_collision
        for child in children if children is not None else system_file.iter_children():
            cloned.add_child(child)
        return cloned

    @staticmethod
    def __has_verified_split_root_final_leaf(existing_file: SystemFile,
                                             remote_file: Optional[SystemFile]) -> bool:
        if remote_file is None or existing_file.is_dir != remote_file.is_dir:
            return False
        if not existing_file.is_dir:
            return not existing_file.has_staging_collision and \
                ModelBuilder.__is_verified_final_leaf(remote_file, existing_file)
        remote_children = {child.name: child for child in remote_file.iter_children()}
        return any(
            ModelBuilder.__has_verified_split_root_final_leaf(
                child, remote_children.get(child.name)
            )
            for child in existing_file.iter_children()
        )

    @staticmethod
    def __final_split_root_structure(existing_file: SystemFile,
                                     remote_file: Optional[SystemFile]) -> Optional[SystemFile]:
        """Keep only final evidence absent from a fresh active staging scan."""
        if remote_file is None or existing_file.is_dir != remote_file.is_dir:
            return None
        if not existing_file.is_dir:
            if existing_file.is_staging or not ModelBuilder.__is_verified_final_leaf(
                    remote_file, existing_file):
                return None
            return existing_file
        remote_children = {child.name: child for child in remote_file.iter_children()}
        retained_children = [
            retained_child
            for child in existing_file.iter_children()
            if (retained_child := ModelBuilder.__final_split_root_structure(
                child, remote_children.get(child.name)
            )) is not None
        ]
        if not retained_children and existing_file.is_staging:
            return None
        return ModelBuilder.__clone_system_file(
            existing_file,
            retained_children,
            is_staging=False,
        )

    @staticmethod
    def __merge_active_split_root(existing_file: SystemFile,
                                  active_file: SystemFile,
                                  remote_file: Optional[SystemFile]) -> SystemFile:
        """Overlay fresh staging state without hiding verified final leaves."""
        if existing_file.is_dir != active_file.is_dir:
            if remote_file is not None and existing_file.is_dir == remote_file.is_dir:
                # Keep the shape that matches the remote tree.  The competing
                # active shape is retained as ambiguity metadata so neither
                # this node nor its descendants can be trusted as final.
                merged = ModelBuilder.__clone_system_file(existing_file)
                merged.has_staging_collision = True
                return merged
            merged = ModelBuilder.__clone_system_file(active_file)
            merged.has_staging_collision = True
            return merged
        if existing_file.is_dir and active_file.is_dir:
            remote_children = {
                child.name: child for child in remote_file.iter_children()
            } if remote_file is not None and remote_file.is_dir else {}
            active_children = {child.name: child for child in active_file.iter_children()}
            merged_children: List[SystemFile] = []
            for existing_child in existing_file.iter_children():
                active_child = active_children.pop(existing_child.name, None)
                if active_child is None:
                    retained_final_child = ModelBuilder.__final_split_root_structure(
                        existing_child, remote_children.get(existing_child.name)
                    )
                    if retained_final_child is not None:
                        merged_children.append(retained_final_child)
                    continue
                merged_children.append(ModelBuilder.__merge_active_split_root(
                    existing_child,
                    active_child,
                    remote_children.get(existing_child.name),
                ))
            merged_children.extend(active_children.values())
            merged_children.sort(key=lambda child: child.name)
            merged = ModelBuilder.__clone_system_file(
                existing_file,
                merged_children,
                is_staging=existing_file.is_staging and active_file.is_staging,
            )
            merged.has_staging_collision = \
                existing_file.has_staging_collision or active_file.has_staging_collision
            return merged

        if not existing_file.is_dir and not active_file.is_dir and \
                not existing_file.is_staging and \
                remote_file is not None and \
                ModelBuilder.__leaf_matches_remote_identity(remote_file, existing_file):
            merged = ModelBuilder.__clone_system_file(existing_file)
            # The final copy always wins.  It is safe to reconcile only when
            # the staging duplicate independently carries the same portable
            # source identity; otherwise retain a terminal collision marker.
            merged.has_staging_collision = not ModelBuilder.__leaf_matches_remote_identity(
                remote_file, active_file
            )
            return merged

        merged = ModelBuilder.__clone_system_file(active_file)
        merged.has_staging_collision = existing_file.has_staging_collision or \
            active_file.has_staging_collision
        return merged

    @staticmethod
    def __directory_leaves_cover_remote(remote_file: Optional[SystemFile],
                                        local_file: Optional[SystemFile]) -> bool:
        """Whether every remote leaf has a non-ambiguous complete local leaf."""
        if remote_file is None or local_file is None or remote_file.is_dir != local_file.is_dir:
            return False
        if local_file.has_staging_collision:
            return False
        if not remote_file.is_dir:
            if local_file.is_staging:
                return local_file.size >= remote_file.size
            # Directory completion historically accepts a complete final leaf
            # without scan provenance; reserve raw-mtime identity for split-root
            # trust/exclusion.  Collision metadata still fails closed above.
            return local_file.size >= remote_file.size
        local_children = {child.name: child for child in local_file.iter_children()}
        remote_children = {child.name: child for child in remote_file.iter_children()}
        # A fresh active staging tree can contain obsolete or unrelated
        # entries.  They must not inflate a complete-looking directory into a
        # move candidate, while ordinary final-only local structure remains
        # non-blocking for compatibility with existing local scans.
        if any(
                child.has_staging_collision or ModelBuilder.__has_staging_descendant(child)
                for name, child in local_children.items()
                if name not in remote_children
        ):
            return False
        return all(
            ModelBuilder.__directory_leaves_cover_remote(
                remote_child, local_children.get(remote_child.name)
            )
            for remote_child in remote_children.values()
        )

    @staticmethod
    def __directory_leaves_cover_remote_for_presentation(
            remote_file: Optional[SystemFile], local_file: Optional[SystemFile]) -> bool:
        """Whether every remote leaf is locally complete for presentation.

        Presentation coverage is intentionally independent of unmatched local
        staging branches.  Those branches remain lifecycle ambiguity and are
        rejected by ``__directory_leaves_cover_remote`` and the strict
        completion predicates below; the model view only needs to report
        whether each remote leaf has a locally present complete counterpart.
        """
        if remote_file is None or local_file is None or remote_file.is_dir != local_file.is_dir:
            return False
        if local_file.has_staging_collision:
            return False
        if not remote_file.is_dir:
            return local_file.size >= remote_file.size
        local_children = {child.name: child for child in local_file.iter_children()}
        return all(
            ModelBuilder.__directory_leaves_cover_remote_for_presentation(
                remote_child, local_children.get(remote_child.name)
            )
            for remote_child in remote_file.iter_children()
        )

    @staticmethod
    def __effective_local_tree_proves_completion(remote_file: Optional[SystemFile],
                                                 local_file: Optional[SystemFile]) -> bool:
        """Apply split-root collision and staging-extra completion rules."""
        if remote_file is None or local_file is None or remote_file.is_dir != local_file.is_dir:
            return False
        if local_file.has_staging_collision:
            return False
        if remote_file.is_dir:
            if ModelBuilder.__directory_leaves_cover_remote(remote_file, local_file):
                return True
            # Some backends only report a staged root total while a remote
            # child has no size.  With no scanned local children there is no
            # split-root branch (or staging-only extra) to arbitrate, so keep
            # the established root-total recovery behavior.
            return local_file.is_staging and not tuple(local_file.iter_children()) and \
                local_file.size >= remote_file.size
        return local_file.size >= remote_file.size

    @staticmethod
    def __effective_local_tree_covers_remote_leaves_allowing_collision(
            remote_file: Optional[SystemFile], local_file: Optional[SystemFile]) -> bool:
        """Prove every remote leaf is locally complete without trusting extras.

        Terminal collision handling intentionally allows collision metadata on
        matched remote paths, but must not treat a root aggregate as complete
        when a remote leaf is partial or an unmatched staging branch inflates
        the byte total.
        """
        if remote_file is None or local_file is None or remote_file.is_dir != local_file.is_dir:
            return False
        if not remote_file.is_dir:
            return local_file.size >= remote_file.size

        remote_children = {child.name: child for child in remote_file.iter_children()}
        local_children = {child.name: child for child in local_file.iter_children()}
        if any(
                child.is_staging or ModelBuilder.__has_staging_descendant(child)
                for name, child in local_children.items()
                if name not in remote_children
        ):
            return False
        return all(
            ModelBuilder.__effective_local_tree_covers_remote_leaves_allowing_collision(
                remote_child,
                local_children.get(remote_child.name),
            )
            for remote_child in remote_children.values()
        )

    def __build_effective_local_files(self) -> Dict[str, SystemFile]:
        if not self.__active_files:
            return self.__local_files()

        effective_local_files = self.__local_files()
        for file_id, active_file in self.__active_files.items():
            existing_file = effective_local_files.get(file_id)
            remote_file = self.__remote_file(file_id)
            if existing_file is not None and remote_file is not None and \
                    (self.__has_staging_descendant(existing_file) or
                     self.__has_staging_collision_descendant(existing_file)) and \
                    (self.__has_verified_split_root_final_leaf(existing_file, remote_file) or
                     self.__has_staging_collision_descendant(existing_file)):
                effective_local_files[file_id] = self.__merge_active_split_root(
                    existing_file, active_file, remote_file
                )
                continue
            if existing_file is not None and getattr(existing_file, "is_staging", False):
                continue
            if existing_file is not None and \
                    self.__is_authoritative_local_file(existing_file) and \
                    getattr(active_file, "is_staging", False) and \
                    existing_file.size >= active_file.size and \
                    not self.__remote_indicates_newer_content(existing_file, remote_file):
                continue
            effective_local_files[file_id] = active_file
        return effective_local_files

    def set_local_files(self, local_files: List[SystemFile]) -> None:
        started_at = self.__begin_duration(DURATION_MODEL_BUILDER_SET_LOCAL_FILES)
        try:
            next_local_files = self.__bucket_files(local_files)
            # Invalidate the cache
            if next_local_files != self.__local_files_by_pair:
                self.__local_files_by_pair = next_local_files
                self.__refresh_source_name_counts()
                self.__refresh_local_root_name_counts()
                self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_LOCAL_FILES)
        finally:
            self.__finish_duration(DURATION_MODEL_BUILDER_SET_LOCAL_FILES, started_at)

    def set_remote_files(self, remote_files: List[SystemFile]) -> None:
        started_at = self.__begin_duration(DURATION_MODEL_BUILDER_SET_REMOTE_FILES)
        try:
            next_remote_files = self.__bucket_files(remote_files)
            # Invalidate the cache
            if next_remote_files != self.__remote_files_by_pair:
                self.__remote_files_by_pair = next_remote_files
                self.__refresh_source_name_counts()
                self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_REMOTE_FILES)
        finally:
            self.__finish_duration(DURATION_MODEL_BUILDER_SET_REMOTE_FILES, started_at)

    def build_progressive_roots(
        self,
        local_files: List[SystemFile],
        remote_files: List[SystemFile],
        unknown_local_path_pair_ids: Set[Optional[str]],
    ) -> Model:
        """Render a progressive root delta without walking retained roots.

        Partial scan publications are presence-only hints; destructive
        authority remains with ``build_model`` at the final reconciliation
        boundary.  A short-lived builder reuses the standing non-scan inputs
        while its local/remote maps contain only roots touched by this wave.
        """
        partial = ModelBuilder()
        partial.logger = self.logger
        partial.__target_archive_trace_logger = self.__target_archive_trace_logger
        partial.__stop_resume_trace_cycle_id = self.__stop_resume_trace_cycle_id
        partial.__stop_resume_trace_cycle_context = dict(self.__stop_resume_trace_cycle_context)
        partial.__stop_resume_trace_breadcrumb = self.__stop_resume_trace_breadcrumb
        partial.__stop_resume_trace_last_signatures = OrderedDict(self.__stop_resume_trace_last_signatures)
        partial.__stop_resume_trace_last_enabled = self.__stop_resume_trace_last_enabled
        selected_pair_ids = {
            file.path_pair_id for file in local_files + remote_files
        }
        selected_file_ids = {
            self.__root_file_id(file.name, file.path_pair_id)
            for file in local_files + remote_files
        }

        partial.__local_files_by_pair = partial.__bucket_files(local_files)
        partial.__remote_files_by_pair = partial.__bucket_files(remote_files)
        partial.__active_files = {
            file_id: file for file_id, file in self.__active_files.items()
            if file_id in selected_file_ids or file.path_pair_id in selected_pair_ids
        }
        partial.__active_file_ids = set(self.__active_file_ids).intersection(selected_file_ids)
        partial.__lftp_statuses = {
            file_id: status for file_id, status in self.__lftp_statuses.items()
            if file_id in selected_file_ids or status.path_pair_id in selected_pair_ids
        }
        partial.__recent_live_transfer_snapshots = {
            file_id: snapshot for file_id, snapshot in self.__recent_live_transfer_snapshots.items()
            if file_id in selected_file_ids or snapshot.root_file_id in selected_file_ids
        }
        partial.__retained_stopped_transfer_snapshots = {
            file_id: snapshot for file_id, snapshot in self.__retained_stopped_transfer_snapshots.items()
            if file_id in selected_file_ids or snapshot.root_file_id in selected_file_ids
        }
        partial.__downloaded_files = None if self.__downloaded_files is None else set(self.__downloaded_files)
        partial.__downloaded_timestamps = dict(self.__downloaded_timestamps)
        partial.__downloaded_timestamp_overlay_generation = self.__downloaded_timestamp_overlay_generation
        partial.__extract_statuses = {
            file_id: status for file_id, status in self.__extract_statuses.items()
            if getattr(status, "path_pair_id", None) in selected_pair_ids
        }
        partial.__extracted_files = set(self.__extracted_files)
        partial.__stopped_files = set(self.__stopped_files)
        partial.__validation_statuses = {
            file_id: status for file_id, status in self.__validation_statuses.items()
            if getattr(status, "path_pair_id", None) in selected_pair_ids
        }
        partial.__move_failed_files = set(self.__move_failed_files)
        partial.__final_move_succeeded_files = set(self.__final_move_succeeded_files)
        partial.__unknown_local_path_pair_ids = set(unknown_local_path_pair_ids)
        partial.__local_root_paths = dict(self.__local_root_paths)
        partial.__local_staging_paths = dict(self.__local_staging_paths)
        return partial.build_model()

    def __pair_delta_is_globally_safe(
            self, path_pair_id: Optional[str], local_files: dict[str, SystemFile],
            remote_files: dict[str, SystemFile], unknown_local_path_pair_ids: set[Optional[str]],
    ) -> bool:
        """Fail closed when a pair render would rely on global root identity.

        The final-pair path may replace or remove roots, so it is deliberately
        stricter than the presence-only progressive path.  Legacy marker names,
        duplicate basenames across pairs, orphan status roots, incomplete local
        coverage, ambiguous bare-name markers, and unrelated dirty inputs
        retain the normal full build; canonical cross-pair names stay scoped.
        """
        if path_pair_id is None or path_pair_id in unknown_local_path_pair_ids or \
                self.__unknown_local_path_pair_ids.symmetric_difference(
                    unknown_local_path_pair_ids
                ).difference({path_pair_id}):
            return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_UNKNOWN_AUTHORITY)
        current_local = self.__local_files_by_pair.get(path_pair_id, {})
        current_remote = self.__remote_files_by_pair.get(path_pair_id, {})
        old_sources = {
            file_id: current_local.get(file_id) or current_remote.get(file_id)
            for file_id in set(current_local).union(current_remote)
        }
        next_sources = {
            file_id: local_files.get(file_id) or remote_files.get(file_id)
            for file_id in set(local_files).union(remote_files)
        }
        old_names: dict[str, int] = {}
        next_names: dict[str, int] = {}
        for source in old_sources.values():
            if source is not None:
                old_names[source.name] = old_names.get(source.name, 0) + 1
        for source in next_sources.values():
            if source is not None:
                next_names[source.name] = next_names.get(source.name, 0) + 1
        for name in set(old_names).union(next_names):
            # A bare marker cannot safely be assigned to this pair.  Pairless
            # root ids are also bare, so preserving the global fallback is the
            # only sound interpretation there.
            if name in self.__extracted_files:
                return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_EXTRACTED_BARE_MARKER)
            # Canonical source identities include the pair, so another
            # pair's source basename is safe here. Source-less status/active
            # names still lack that proven source ownership.
            if self.__status_only_name_counts.get(name, 0) > 0:
                return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_STATUS_ONLY_NAME)
            if self.__active_only_name_counts.get(name, 0) > 0:
                return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_ACTIVE_ONLY_NAME)
        # ``build_model`` applies a second, path-aware visibility arbitration:
        # a local-only root is hidden if a managed root shares its configured
        # local root path and basename, and local-only peers choose one winner.
        # Reusing untouched live root objects would bypass that global pass, so
        # reject whenever either the old or staged selected local bucket shares
        # an arbitration key with another pair.  The maintained count keeps
        # this proof bounded even with a large unrelated source set.
        selected_local_keys = {
            key for local_file in list(current_local.values()) + list(local_files.values())
            if (key := self.__local_root_name_key(local_file, path_pair_id)) is not None
        }
        for key in selected_local_keys:
            selected_old_count = sum(
                1 for local_file in current_local.values()
                if self.__local_root_name_key(local_file, path_pair_id) == key
            )
            if self.__local_root_name_counts.get(key, 0) > selected_old_count:
                return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_LOCAL_ROOT_ARBITRATION)
        source_ids = set(next_sources)
        if any(
                status.path_pair_id == path_pair_id and status.file_id not in source_ids
                for status in self.__lftp_statuses.values()
        ):
            return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_ORPHAN_STATUS)
        if any(
                file.path_pair_id == path_pair_id and file_id not in source_ids
                for file_id, file in self.__active_files.items()
        ):
            return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_ORPHAN_ACTIVE)
        allowed_reasons = {
            MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
            MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
            MODEL_BUILDER_INVALIDATION_UNKNOWN_LOCAL_PAIRS,
        }
        if not self.__invalidation_reasons.issubset(allowed_reasons):
            return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_DIRTY_INPUT)
        touched_ids = self.__lftp_touched_root_file_ids | self.__active_touched_root_file_ids
        if not all(self.__file_id_path_pair_id(file_id) == path_pair_id for file_id in touched_ids):
            return self.__reject_pair_safety(COUNTER_PAIR_SAFETY_REJECT_CROSS_PAIR_TOUCH)
        return True

    def build_authoritative_pair_roots(
            self, path_pair_id: Optional[str], local_files: List[SystemFile],
            remote_files: List[SystemFile], unknown_local_path_pair_ids: Set[Optional[str]],
    ) -> Optional[_AuthoritativePairBuild]:
        """Stage a complete pair replacement without mutating authoritative maps."""
        next_local = {
            self.__root_file_id(file.name, path_pair_id): file
            for file in local_files if file.path_pair_id == path_pair_id
        }
        next_remote = {
            self.__root_file_id(file.name, path_pair_id): file
            for file in remote_files if file.path_pair_id == path_pair_id
        }
        current_unknown_ids = set(unknown_local_path_pair_ids)
        if not self.__pair_delta_is_globally_safe(
                path_pair_id, next_local, next_remote, current_unknown_ids,
        ):
            return None
        partial = ModelBuilder()
        partial.logger = self.logger
        partial.__target_archive_trace_logger = self.__target_archive_trace_logger
        partial.__stop_resume_trace_cycle_id = self.__stop_resume_trace_cycle_id
        partial.__stop_resume_trace_cycle_context = dict(self.__stop_resume_trace_cycle_context)
        partial.__stop_resume_trace_breadcrumb = self.__stop_resume_trace_breadcrumb
        partial.__stop_resume_trace_last_signatures = OrderedDict(self.__stop_resume_trace_last_signatures)
        partial.__stop_resume_trace_last_enabled = self.__stop_resume_trace_last_enabled
        partial.__target_archive_trace_last_signature = self.__target_archive_trace_last_signature
        partial.__performance_diagnostics = self.__performance_diagnostics
        partial.__local_files_by_pair = {path_pair_id: dict(next_local)}
        partial.__remote_files_by_pair = {path_pair_id: dict(next_remote)}
        partial.__active_files = {
            file_id: file for file_id, file in self.__active_files.items()
            if file.path_pair_id == path_pair_id
        }
        partial.__active_file_ids = set(self.__active_file_ids).intersection(
            set(next_local).union(next_remote)
        )
        partial.__lftp_statuses = {
            file_id: status for file_id, status in self.__lftp_statuses.items()
            if status.path_pair_id == path_pair_id
        }
        selected_root_ids = set(next_local).union(next_remote)
        partial.__recent_live_transfer_snapshots = {
            file_id: snapshot for file_id, snapshot in self.__recent_live_transfer_snapshots.items()
            if file_id in selected_root_ids or snapshot.root_file_id in selected_root_ids
        }
        partial.__retained_stopped_transfer_snapshots = {
            file_id: snapshot for file_id, snapshot in self.__retained_stopped_transfer_snapshots.items()
            if file_id in selected_root_ids or snapshot.root_file_id in selected_root_ids
        }
        partial.__downloaded_files = None if self.__downloaded_files is None else set(self.__downloaded_files)
        partial.__downloaded_timestamps = dict(self.__downloaded_timestamps)
        partial.__downloaded_timestamp_overlay_generation = self.__downloaded_timestamp_overlay_generation
        partial.__extract_statuses = dict(self.__extract_statuses)
        partial.__extracted_files = set(self.__extracted_files)
        partial.__stopped_files = set(self.__stopped_files)
        partial.__validation_statuses = dict(self.__validation_statuses)
        partial.__move_failed_files = set(self.__move_failed_files)
        partial.__final_move_succeeded_files = set(self.__final_move_succeeded_files)
        partial.__unknown_local_path_pair_ids = set(current_unknown_ids)
        partial.__local_root_paths = dict(self.__local_root_paths)
        partial.__local_staging_paths = dict(self.__local_staging_paths)
        return _AuthoritativePairBuild(
            path_pair_id,
            dict(self.__local_files_by_pair.get(path_pair_id, {})),
            dict(self.__remote_files_by_pair.get(path_pair_id, {})),
            next_local,
            next_remote,
            partial.build_model(),
            current_unknown_ids,
            partial.get_unresolved_staging_collision_file_ids(),
            partial.get_terminalizable_staging_collision_file_ids(),
            dict(partial.__recent_live_transfer_snapshots),
            dict(partial.__retained_stopped_transfer_snapshots),
            {
                file_id for file_id in selected_root_ids
                if partial.has_complete_local_coverage(file_id)
            },
            frozenset(self.__pending_invalidation_tokens),
            self.__downloaded_timestamp_overlay_generation,
        )

    def authorize_authoritative_pair_delta(
            self, root_exists: Callable[[str], bool], pair_build: _AuthoritativePairBuild,
    ) -> bool:
        """Recheck staged source identity and live roots immediately before publication."""
        pair_id = pair_build.path_pair_id
        if self.__local_files_by_pair.get(pair_id, {}) != pair_build.previous_local_files or \
                self.__remote_files_by_pair.get(pair_id, {}) != pair_build.previous_remote_files:
            return False
        if frozenset(self.__pending_invalidation_tokens) != pair_build.invalidation_tokens:
            return False
        if not self.__pair_delta_is_globally_safe(
                pair_id, pair_build.local_files, pair_build.remote_files,
                pair_build.unknown_local_path_pair_ids,
        ):
            return False
        previous_root_ids = set(pair_build.previous_local_files).union(pair_build.previous_remote_files)
        try:
            return all(root_exists(file_id) for file_id in previous_root_ids)
        except Exception:
            return False

    def adopt_authoritative_pair_delta(
            self, applied_model: Model, pair_build: _AuthoritativePairBuild,
            applied_invalidation_tokens: Optional[Set[int]] = None,
    ) -> None:
        """Commit staged pair authority only after the live model accepted it."""
        self.__replace_authoritative_pair_sources(pair_build)
        self.record_local_inventory_completion({pair_build.path_pair_id})
        selected_pair_ids = {pair_build.path_pair_id}
        selected_root_ids = set(pair_build.previous_local_files).union(pair_build.previous_remote_files)
        selected_root_ids.update(pair_build.local_files)
        selected_root_ids.update(pair_build.remote_files)
        def replace_snapshots(
                target: dict[str, _RecentLiveTransferSnapshot],
                staged: dict[str, _RecentLiveTransferSnapshot],
        ) -> None:
            relevant = lambda key, snapshot: key in selected_root_ids or snapshot.root_file_id in selected_root_ids
            for key, snapshot in list(target.items()):
                if relevant(key, snapshot):
                    target.pop(key, None)
            target.update({key: snapshot for key, snapshot in staged.items() if relevant(key, snapshot)})
        replace_snapshots(self.__recent_live_transfer_snapshots, pair_build.recent_live_transfer_snapshots)
        replace_snapshots(self.__retained_stopped_transfer_snapshots, pair_build.retained_stopped_transfer_snapshots)
        self.__cached_unresolved_staging_collision_file_ids = {
            file_id for file_id in self.__cached_unresolved_staging_collision_file_ids
            if self.__file_id_path_pair_id(file_id) not in selected_pair_ids
        }.union(pair_build.unresolved_staging_collision_file_ids)
        self.__cached_terminalizable_staging_collision_file_ids = {
            file_id for file_id in self.__cached_terminalizable_staging_collision_file_ids
            if self.__file_id_path_pair_id(file_id) not in selected_pair_ids
        }.union(pair_build.terminalizable_staging_collision_file_ids)
        self.__unknown_local_path_pair_ids = set(pair_build.unknown_local_path_pair_ids)
        consumed_tokens = set(pair_build.invalidation_tokens)
        consumed_tokens.update(applied_invalidation_tokens or set())
        for token in consumed_tokens:
            self.__pending_invalidation_tokens.pop(token, None)
        self.__invalidation_reasons = {
            reason for reason, _ in self.__pending_invalidation_tokens.values()
        }
        self.__lftp_touched_root_file_ids = set().union(*(
            file_ids or set()
            for reason, file_ids in self.__pending_invalidation_tokens.values()
            if reason == MODEL_BUILDER_INVALIDATION_LFTP_STATUSES
        )) if self.__pending_invalidation_tokens else set()
        self.__active_touched_root_file_ids = set().union(*(
            file_ids or set()
            for reason, file_ids in self.__pending_invalidation_tokens.values()
            if reason == MODEL_BUILDER_INVALIDATION_ACTIVE_FILES
        )) if self.__pending_invalidation_tokens else set()
        self.__lftp_regressed_root_file_ids.intersection_update(self.__lftp_touched_root_file_ids)
        self.__cached_model = applied_model if not self.__pending_invalidation_tokens else None

    def commit_authoritative_pair_sources_for_full_rebuild(
            self, pair_build: _AuthoritativePairBuild,
    ) -> None:
        """Keep a final scan authoritative when its live delta is rejected."""
        self.__replace_authoritative_pair_sources(pair_build)
        self.record_local_inventory_completion({pair_build.path_pair_id})
        self.__unknown_local_path_pair_ids = set(pair_build.unknown_local_path_pair_ids)
        self.request_rebuild()

    def replace_completed_pair_sources_for_full_rebuild(
            self, path_pair_id: Optional[str], local_files: List[SystemFile],
            remote_files: List[SystemFile], unknown_local_path_pair_ids: Set[Optional[str]],
    ) -> None:
        """Commit final pair scan authority when bounded rendering is unsafe.

        This intentionally performs no delta-safety classification: the caller
        has already selected the established global-build fallback.  It still
        replaces only this pair's bucket, preserving every unrelated source
        bucket for that global reconciliation.
        """
        next_local = {
            self.__root_file_id(file.name, path_pair_id): file
            for file in local_files if file.path_pair_id == path_pair_id
        }
        next_remote = {
            self.__root_file_id(file.name, path_pair_id): file
            for file in remote_files if file.path_pair_id == path_pair_id
        }
        self.__replace_authoritative_pair_sources(_AuthoritativePairBuild(
            path_pair_id,
            dict(self.__local_files_by_pair.get(path_pair_id, {})),
            dict(self.__remote_files_by_pair.get(path_pair_id, {})),
            next_local,
            next_remote,
            None,
            set(unknown_local_path_pair_ids),
            set(),
            set(),
            {},
            {},
            set(),
            frozenset(),
            self.__downloaded_timestamp_overlay_generation,
        ))
        self.record_local_inventory_completion({path_pair_id})
        self.__unknown_local_path_pair_ids = set(unknown_local_path_pair_ids)
        self.request_rebuild()

    def __replace_authoritative_pair_sources(self, pair_build: _AuthoritativePairBuild) -> None:
        """Replace one source bucket and maintain bounded global name counts."""
        pair_id = pair_build.path_pair_id
        previous_local = self.__local_files_by_pair.get(pair_id, {})
        previous_remote = self.__remote_files_by_pair.get(pair_id, {})
        old_sources = {
            file_id: previous_local.get(file_id) or previous_remote.get(file_id)
            for file_id in set(previous_local).union(previous_remote)
        }
        next_sources = {
            file_id: pair_build.local_files.get(file_id) or pair_build.remote_files.get(file_id)
            for file_id in set(pair_build.local_files).union(pair_build.remote_files)
        }
        for source in old_sources.values():
            if source is not None:
                remaining = self.__source_name_counts.get(source.name, 0) - 1
                if remaining > 0:
                    self.__source_name_counts[source.name] = remaining
                else:
                    self.__source_name_counts.pop(source.name, None)
        for source in next_sources.values():
            if source is not None:
                self.__source_name_counts[source.name] = self.__source_name_counts.get(source.name, 0) + 1
        for local_file in previous_local.values():
            self.__adjust_local_root_name_count(local_file, pair_id, -1)
        for local_file in pair_build.local_files.values():
            self.__adjust_local_root_name_count(local_file, pair_id, 1)
        if pair_build.local_files:
            self.__local_files_by_pair[pair_id] = dict(pair_build.local_files)
        else:
            self.__local_files_by_pair.pop(pair_id, None)
        if pair_build.remote_files:
            self.__remote_files_by_pair[pair_id] = dict(pair_build.remote_files)
        else:
            self.__remote_files_by_pair.pop(pair_id, None)
        affected_ids = set(old_sources).union(next_sources)
        for file_id in affected_ids:
            self.__update_status_only_name_count(file_id)
            self.__update_active_only_name_count(file_id)

    def has_pending_active_transfer_delta(self) -> bool:
        """Cheap hot-path gate; does not inspect model roots or source maps."""
        return bool(self.__invalidation_reasons.intersection({
            MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
            MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
            MODEL_BUILDER_INVALIDATION_CONFIRMED_LOCAL_DELETIONS,
            MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
            MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
            MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
        }))

    def has_only_pending_active_transfer_delta(self) -> bool:
        """Whether live transfer inputs are the only outstanding invalidation.

        Progressive scan roots and a live-status repaint can then be published
        independently in the same controller tick without walking unrelated
        retained roots.
        """
        return (
            bool(self.__invalidation_reasons.intersection({
                MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
                MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
                MODEL_BUILDER_INVALIDATION_CONFIRMED_LOCAL_DELETIONS,
                MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
            }))
            and self.__invalidation_reasons.issubset({
                MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
                MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
                MODEL_BUILDER_INVALIDATION_CONFIRMED_LOCAL_DELETIONS,
                MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
            })
        )

    def unknown_local_path_pair_ids_snapshot(self) -> frozenset[Optional[str]]:
        """Return the derived unknown-pair overlay without transferring ownership."""
        return frozenset(self.__unknown_local_path_pair_ids)

    def has_only_pending_unknown_local_path_pairs(self) -> bool:
        """Whether the derived safety overlay is the only pending render input."""
        return self.__invalidation_reasons == {
            MODEL_BUILDER_INVALIDATION_UNKNOWN_LOCAL_PAIRS,
        }

    def active_transfer_delta_diagnostics(self) -> dict[str, object]:
        """Return identity-free bounded evidence for a rejected root delta."""
        return {
            "invalidation_reasons": sorted(self.__invalidation_reasons),
            "lftp_touched_count": len(self.__lftp_touched_root_file_ids),
            "active_touched_count": len(self.__active_touched_root_file_ids),
            "lftp_regressed_count": len(self.__lftp_regressed_root_file_ids),
            "pending_token_count": len(self.__pending_invalidation_tokens),
            "token_reason_counts": {
                reason: sum(1 for candidate, _ in self.__pending_invalidation_tokens.values()
                            if candidate == reason)
                for reason in sorted({reason for reason, _ in self.__pending_invalidation_tokens.values()})
            },
        }

    def active_transfer_delta_file_ids(
            self, known_root_file_ids: Set[str] | Callable[[str], bool],
    ) -> Optional[set[str]]:
        """Return roots eligible for a transfer-lifecycle partial publication.

        Status disappearance, a new status-only root, or any invalidation
        outside live status, active-scan, and stopped-state inputs requires the
        normal full model build.  In particular, this method never grants
        partial authority to remove a root.
        """
        if not self.__invalidation_reasons.issubset({
                MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
                MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
                MODEL_BUILDER_INVALIDATION_CONFIRMED_LOCAL_DELETIONS,
                MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
        }) or not self.__invalidation_reasons.intersection({
                MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
                MODEL_BUILDER_INVALIDATION_ACTIVE_FILES,
                MODEL_BUILDER_INVALIDATION_CONFIRMED_LOCAL_DELETIONS,
                MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
        }):
            return None
        file_ids = set(self.__lftp_touched_root_file_ids)
        file_ids.update(self.__active_touched_root_file_ids)
        file_ids.update(
            file_id
            for reason, affected in self.__pending_invalidation_tokens.values()
            if reason in {
                MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
                MODEL_BUILDER_INVALIDATION_CONFIRMED_LOCAL_DELETIONS,
            } and affected is not None
            for file_id in affected
        )
        if callable(known_root_file_ids):
            try:
                roots_known = all(known_root_file_ids(file_id) for file_id in file_ids)
            except Exception:
                roots_known = False
        else:
            roots_known = file_ids.issubset(known_root_file_ids)
        if not file_ids or not roots_known:
            return None
        stopped_file_ids = {
            file_id for file_id in file_ids if file_id in self.__stopped_files
        }
        if self.__lftp_regressed_root_file_ids.intersection(file_ids).difference(stopped_file_ids):
            return None
        if not self.__active_touched_root_file_ids.issubset(file_ids):
            return None
        if MODEL_BUILDER_INVALIDATION_LFTP_STATUSES in self.__invalidation_reasons:
            for file_id in self.__lftp_touched_root_file_ids:
                status = self.__lftp_statuses.get(file_id)
                if status is None and file_id in stopped_file_ids:
                    continue
                if status is None or status.file_id != file_id or status.state not in (
                        LftpJobStatus.State.QUEUED, LftpJobStatus.State.RUNNING):
                    return None
        if not self.__active_transfer_delta_global_state_is_safe(file_ids):
            return None
        return file_ids

    def __active_transfer_delta_global_state_is_safe(self, root_file_ids: Set[str]) -> bool:
        """Reject root deltas whose rendering depends on global root visibility.

        ``build_model`` suppresses legacy name-only extraction markers when a
        name is ambiguous across path pairs.  Rendering one root cannot prove
        that absence, so reject the fast path before creating a misleading
        EXTRACTED state.  Collision and local-only winner caches are only
        authoritative after full builds; this delta never updates them.
        """
        selected_names = {
            (self.__remote_file(file_id) or self.__local_file(file_id)).name
            for file_id in root_file_ids
            if self.__remote_file(file_id) is not None or self.__local_file(file_id) is not None
        }
        if not selected_names.intersection(self.__extracted_files):
            return True
        return all(
            self.__source_name_counts.get(name, 0) + self.__status_only_name_counts.get(name, 0) +
            self.__active_only_name_counts.get(name, 0) <= 1
            for name in selected_names
        )

    def build_active_transfer_roots(self, root_file_ids: Set[str]) -> _ActiveTransferRootBuild:
        """Build only existing roots whose live transfer inputs changed.

        The sibling API intentionally filters by canonical root id rather
        than path pair.  A pair may legitimately contain multiple roots (and
        path-pair names can repeat), so widening to a pair would defeat the
        bounded work guarantee.
        """
        partial = ModelBuilder()
        partial.logger = self.logger
        partial.__target_archive_trace_logger = self.__target_archive_trace_logger
        partial.__stop_resume_trace_cycle_id = self.__stop_resume_trace_cycle_id
        partial.__stop_resume_trace_cycle_context = dict(self.__stop_resume_trace_cycle_context)
        partial.__stop_resume_trace_breadcrumb = self.__stop_resume_trace_breadcrumb
        partial.__stop_resume_trace_last_signatures = OrderedDict(self.__stop_resume_trace_last_signatures)
        partial.__stop_resume_trace_last_enabled = self.__stop_resume_trace_last_enabled
        selected_pair_ids = {
            self.__file_id_path_pair_id(file_id) for file_id in root_file_ids
        }
        partial.__local_files_by_pair = {
            pair_id: {
                file_id: file for file_id, file in files.items()
                if file_id in root_file_ids
            }
            for pair_id in selected_pair_ids
            for files in (self.__local_files_by_pair.get(pair_id, {}),)
            if files
        }
        partial.__remote_files_by_pair = {
            pair_id: {
                file_id: file for file_id, file in files.items()
                if file_id in root_file_ids
            }
            for pair_id in selected_pair_ids
            for files in (self.__remote_files_by_pair.get(pair_id, {}),)
            if files
        }
        partial.__active_files = {
            file_id: file for file_id, file in self.__active_files.items()
            if file_id in root_file_ids
        }
        partial.__active_file_ids = set()
        for active_file in partial.__active_files.values():
            partial.__collect_active_file_ids(active_file, partial.__active_file_ids)
        partial.__lftp_statuses = {
            file_id: status for file_id, status in self.__lftp_statuses.items()
            if file_id in root_file_ids
        }
        partial.__recent_live_transfer_snapshots = {
            file_id: snapshot for file_id, snapshot in self.__recent_live_transfer_snapshots.items()
            if file_id in root_file_ids or snapshot.root_file_id in root_file_ids
        }
        partial.__retained_stopped_transfer_snapshots = {
            file_id: snapshot for file_id, snapshot in self.__retained_stopped_transfer_snapshots.items()
            if file_id in root_file_ids or snapshot.root_file_id in root_file_ids
        }
        partial.__downloaded_files = None if self.__downloaded_files is None else set(self.__downloaded_files)
        partial.__downloaded_timestamps = dict(self.__downloaded_timestamps)
        partial.__downloaded_timestamp_overlay_generation = self.__downloaded_timestamp_overlay_generation
        partial.__extract_statuses = dict(self.__extract_statuses)
        partial.__extracted_files = set(self.__extracted_files)
        partial.__stopped_files = set(self.__stopped_files)
        partial.__validation_statuses = dict(self.__validation_statuses)
        partial.__move_failed_files = set(self.__move_failed_files)
        partial.__final_move_succeeded_files = set(self.__final_move_succeeded_files)
        partial.__unknown_local_path_pair_ids = set(self.__unknown_local_path_pair_ids)
        partial.__local_root_paths = dict(self.__local_root_paths)
        partial.__local_staging_paths = dict(self.__local_staging_paths)
        return _ActiveTransferRootBuild(
            model=partial.build_model(),
            recent_live_transfer_snapshots=dict(partial.__recent_live_transfer_snapshots),
            retained_stopped_transfer_snapshots=dict(partial.__retained_stopped_transfer_snapshots),
        )

    def set_lftp_statuses(self, lftp_statuses: List[LftpJobStatus]) -> None:
        started_at = self.__begin_duration(DURATION_MODEL_BUILDER_SET_LFTP_STATUSES)
        try:
            prev_lftp_statuses = self.__lftp_statuses
            self.__lftp_statuses = {file.file_id: file for file in lftp_statuses}
            # Invalidate the cache
            if self.__lftp_statuses != prev_lftp_statuses:
                touched_file_ids = {
                    file_id for file_id in set(prev_lftp_statuses).union(self.__lftp_statuses)
                    if prev_lftp_statuses.get(file_id) != self.__lftp_statuses.get(file_id)
                }
                self.__lftp_regressed_root_file_ids.update({
                    file_id for file_id in touched_file_ids
                    if prev_lftp_statuses.get(file_id) is not None and
                    prev_lftp_statuses[file_id].state == LftpJobStatus.State.RUNNING and
                    (self.__lftp_statuses.get(file_id) is None or
                     self.__lftp_statuses[file_id].state != LftpJobStatus.State.RUNNING)
                })
                for file_id in touched_file_ids:
                    self.__update_status_only_name_count(file_id)
                self.__invalidate_cache(
                    MODEL_BUILDER_INVALIDATION_LFTP_STATUSES,
                    touched_file_ids,
                )
        finally:
            self.__finish_duration(DURATION_MODEL_BUILDER_SET_LFTP_STATUSES, started_at)

    def evict_recent_live_transfer_snapshots_missing_roots(self, active_root_file_ids: Set[str]) -> None:
        removed = False
        for file_id, snapshot in list(self.__recent_live_transfer_snapshots.items()):
            if snapshot.root_file_id in active_root_file_ids:
                continue
            root_status = self.__lftp_statuses.get(snapshot.root_file_id)
            if self.__is_stopped_file(file_id) or self.__is_stopped_file(snapshot.root_file_id, status=root_status):
                continue
            self.__recent_live_transfer_snapshots.pop(file_id, None)
            removed = True
        if removed:
            self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_LFTP_STATUSES)

    def evict_recent_live_transfer_snapshots_for_completed_file_ids(
            self, completed_file_ids: Set[str]) -> None:
        """Discard live progress only after an authoritative completion handoff.

        A fresh LFTP status can disappear before a local scan catches up with
        its final checkpoint. That disappearance is distinct from ordinary
        transient status loss, whose snapshots remain available until local
        scan evidence catches up. The completion handoff supplies canonical
        file ids, so it can safely evict just those live snapshots without
        affecting stopped-transfer floors or unrelated roots.
        """
        root_snapshots: dict[str, _RecentLiveTransferSnapshot] = {}
        for snapshot in self.__recent_live_transfer_snapshots.values():
            root_snapshots.setdefault(snapshot.root_file_id, snapshot)
        canonical_claims_by_legacy_root = self.__completion_snapshot_canonical_claims(
            completed_file_ids,
        )
        root_file_ids_to_evict: set[str] = set()
        for completed_file_id in completed_file_ids:
            _, snapshot = self.__resolve_transfer_snapshot(
                root_snapshots, completed_file_id, completed_file_id,
            )
            if snapshot is None:
                continue
            root_file_id = snapshot.root_file_id
            if (self.__file_id_path_pair_id(completed_file_id) is not None and
                    root_file_id == completed_file_id) or \
                    canonical_claims_by_legacy_root.get(root_file_id) == {completed_file_id}:
                root_file_ids_to_evict.add(root_file_id)

        removed = False
        for file_id, snapshot in list(self.__recent_live_transfer_snapshots.items()):
            if snapshot.root_file_id in root_file_ids_to_evict:
                self.__recent_live_transfer_snapshots.pop(file_id, None)
                removed = True
        if removed:
            self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_LFTP_STATUSES)

    def __completion_snapshot_canonical_claims(
            self,
            completed_file_ids: Set[str],
    ) -> dict[str, set[str]]:
        """Build one completion-local census of canonical legacy-alias claims."""
        authoritative_file_ids = set(completed_file_ids)
        authoritative_file_ids.update(self.__local_files())
        authoritative_file_ids.update(self.__remote_files())
        authoritative_file_ids.update(self.__active_files)
        authoritative_file_ids.update(self.__lftp_statuses)
        authoritative_file_ids.update(self.__stopped_files)
        candidate_file_ids = set(authoritative_file_ids)
        for file_id, snapshot in self.__recent_live_transfer_snapshots.items():
            candidate_file_ids.add(file_id)
            if self.__file_id_path_pair_id(snapshot.root_file_id) is not None:
                candidate_file_ids.add(snapshot.root_file_id)
        for file_id, snapshot in self.__retained_stopped_transfer_snapshots.items():
            candidate_file_ids.add(file_id)
            if self.__file_id_path_pair_id(snapshot.root_file_id) is not None:
                candidate_file_ids.add(snapshot.root_file_id)

        claims_by_legacy_root: dict[str, set[str]] = {}
        for file_id in candidate_file_ids:
            path_pair_id = self.__file_id_path_pair_id(file_id)
            if path_pair_id is None:
                if file_id in authoritative_file_ids:
                    claims_by_legacy_root.setdefault(file_id, set()).add(file_id)
                continue
            aliases = self.__candidate_snapshot_root_aliases(file_id)
            if len(aliases) < 2:
                continue
            claims_by_legacy_root.setdefault(aliases[-1], set()).add(file_id)
        return claims_by_legacy_root

    def set_downloaded_files(self, downloaded_files: Set[str]) -> Optional[int]:
        prev_downloaded_files = self.__downloaded_files
        self.__downloaded_files = set(downloaded_files)
        # Invalidate the cache
        if self.__downloaded_files != prev_downloaded_files:
            changed_file_ids = set(self.__downloaded_files or set())
            changed_file_ids.update(prev_downloaded_files or set())
            changed_file_ids = {
                file_id for file_id in changed_file_ids
                if (file_id in (self.__downloaded_files or set())) !=
                (file_id in (prev_downloaded_files or set()))
            }
            return self.__invalidate_cache(
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_FILES,
                affected_file_ids=changed_file_ids,
            )
        return None

    def set_unknown_local_path_pair_ids(self, path_pair_ids: Set[Optional[str]]) -> None:
        """Keep persisted markers from becoming Deleted while local evidence is incomplete."""
        normalized = set(path_pair_ids)
        previous = self.__unknown_local_path_pair_ids
        if normalized == previous:
            return
        added = normalized.difference(previous)
        removed = previous.difference(normalized)
        self.__unknown_local_path_pair_ids = normalized
        cached_model = self.__cached_model
        # Queue safety and model summaries read this overlay directly.  A new
        # unknown pair only makes the cached render stale when it must hide an
        # already rendered Deleted root; every other additive change can keep
        # the standing authoritative model without another full tree walk.
        added_hides_deleted = cached_model is not None and any(
            (model_file := cached_model.get_file(file_id)).path_pair_id in added and
            model_file.state == ModelFile.State.DELETED
            for file_id in cached_model.get_file_ids()
        )
        if cached_model is None or removed or added_hides_deleted:
            self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_UNKNOWN_LOCAL_PAIRS)

    def set_downloaded_timestamps(self, downloaded_timestamps: Dict[str, float]) -> None:
        previous = self.__downloaded_timestamps
        self.__downloaded_timestamps = dict(downloaded_timestamps)
        if self.__downloaded_timestamps != previous:
            self.__downloaded_timestamp_overlay_generation += 1
            changed_file_ids = {
                file_id for file_id in set(previous).union(self.__downloaded_timestamps)
                if previous.get(file_id) != self.__downloaded_timestamps.get(file_id)
            }
            self.__invalidate_cache(
                MODEL_BUILDER_INVALIDATION_DOWNLOADED_TIMESTAMPS,
                affected_file_ids=changed_file_ids,
            )

    def set_extract_statuses(self, extract_statuses: List[ExtractStatus]) -> None:
        prev_extract_statuses = self.__extract_statuses
        self.__extract_statuses = {
            self.__extract_status_key(status): status for status in extract_statuses
        }
        # Invalidate the cache
        if self.__extract_statuses != prev_extract_statuses:
            self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_EXTRACT_STATUSES)

    def set_extracted_files(self, extracted_files: Set[str]) -> None:
        prev_extracted_files = self.__extracted_files
        self.__extracted_files = extracted_files
        # Invalidate the cache
        if self.__extracted_files != prev_extracted_files:
            self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_EXTRACTED_FILES)

    def set_stopped_files(self, stopped_files: Set[str]) -> None:
        started_at = self.__begin_duration(DURATION_MODEL_BUILDER_SET_STOPPED_FILES)
        try:
            prev_stopped_files = self.__stopped_files
            self.__stopped_files = set(stopped_files)
            self.__sweep_recent_live_transfer_snapshots()
            # Invalidate the cache
            if self.__stopped_files != prev_stopped_files:
                self.__invalidate_cache(
                    MODEL_BUILDER_INVALIDATION_STOPPED_FILES,
                    affected_file_ids=self.__stopped_files.symmetric_difference(prev_stopped_files),
                )
        finally:
            self.__finish_duration(DURATION_MODEL_BUILDER_SET_STOPPED_FILES, started_at)

    def set_move_failed_files(self, move_failed_files: Set[str]) -> Optional[int]:
        previous = self.__move_failed_files
        self.__move_failed_files = set(move_failed_files)
        if self.__move_failed_files != previous:
            changed_file_ids = self.__move_failed_files.symmetric_difference(previous)
            return self.__invalidate_cache(
                MODEL_BUILDER_INVALIDATION_MOVE_FAILED_FILES,
                affected_file_ids=changed_file_ids,
            )
        return None

    def set_final_move_succeeded_files(self, file_ids: Set[str]) -> None:
        previous = self.__final_move_succeeded_files
        self.__final_move_succeeded_files = set(file_ids)
        if self.__final_move_succeeded_files != previous:
            self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_FINAL_MOVE_SUCCEEDED_FILES)

    def set_validation_statuses(self, validation_statuses: List[ValidateStatus]) -> None:
        prev_validation_statuses = self.__validation_statuses
        self.__validation_statuses = {status.file_id: status for status in validation_statuses}
        if self.__validation_statuses != prev_validation_statuses:
            self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_VALIDATION_STATUSES)

    def clear(self) -> None:
        self.__local_files_by_pair.clear()
        self.__replace_local_library_inventory({})
        self.__active_files.clear()
        self.__remote_files_by_pair.clear()
        self.__source_name_counts.clear()
        self.__local_root_name_counts.clear()
        self.__status_only_file_names.clear()
        self.__status_only_name_counts.clear()
        self.__active_only_file_names.clear()
        self.__active_only_name_counts.clear()
        self.__active_file_ids.clear()
        self.__lftp_statuses.clear()
        self.__recent_live_transfer_snapshots.clear()
        self.__retained_stopped_transfer_snapshots.clear()
        self.__downloaded_files = None
        if self.__downloaded_timestamps:
            self.__downloaded_timestamp_overlay_generation += 1
        self.__downloaded_timestamps.clear()
        self.__extract_statuses.clear()
        self.__extracted_files.clear()
        self.__stopped_files.clear()
        self.__validation_statuses.clear()
        self.__move_failed_files.clear()
        self.__final_move_succeeded_files.clear()
        self.__suppressed_ambiguous_extracted_file_names.clear()
        self.__stop_resume_trace_last_signatures.clear()
        self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_CLEAR)
        self.__cached_unresolved_staging_collision_file_ids.clear()
        self.__cached_terminalizable_staging_collision_file_ids.clear()

    def has_changes(self) -> bool:
        """
        Returns true is model has changes and requires rebuild
        :return:
        """
        # A retained live-progress floor is rendered into the cached model.
        # It is continuity evidence, not an input change: rebuilding every
        # controller tick would traverse every unrelated root until the next
        # local scan catches up.  Source setters and snapshot eviction already
        # invalidate this cache when the rendered value can actually change.
        return self.__cached_model is None

    def request_rebuild(self) -> None:
        self.__invalidate_cache(MODEL_BUILDER_INVALIDATION_EXPLICIT)

    def invalidation_token_matches_file(self, token: int, file_id: str) -> bool:
        """Return whether a token changed exactly one known file identity."""
        event = self.__pending_invalidation_tokens.get(token)
        return event is not None and event[1] == frozenset({file_id})

    def adopt_applied_model(
            self,
            built_model: Model,
            applied_model: Model,
            applied_invalidation_tokens: Optional[Set[int]] = None,
    ) -> None:
        """Alias the cache to the controller's live model after a successful diff.

        Source setters may invalidate the cache while the updater is applying
        side effects from a build.  Only the exact model returned by this
        build may be replaced.  The updater may additionally identify exact
        invalidation tokens whose side effect was applied directly to the live
        model; unrelated or later invalidations remain dirty for the next
        update cycle.
        """
        if self.__cached_model is built_model:
            self.__cached_model = applied_model
            self.__pending_invalidation_tokens.clear()
            return
        if not self.__pending_invalidation_tokens or not applied_invalidation_tokens:
            return
        if not set(self.__pending_invalidation_tokens).issubset(applied_invalidation_tokens):
            return
        self.__cached_model = applied_model
        self.__invalidation_reasons.clear()
        self.__pending_invalidation_tokens.clear()
        self.__lftp_touched_root_file_ids.clear()
        self.__active_touched_root_file_ids.clear()
        self.__lftp_regressed_root_file_ids.clear()

    def authorize_active_transfer_delta(
            self, known_root_file_ids: Set[str] | Callable[[str], bool], root_file_ids: Set[str],
            partial_build: _ActiveTransferRootBuild, live_model: Optional[Model] = None,
    ) -> bool:
        """Validate a staged delta before it can mutate the live Model."""
        return self.active_transfer_delta_file_ids(known_root_file_ids) == set(root_file_ids) and \
            partial_build.model.get_file_ids() == set(root_file_ids) and \
            (live_model is None or
             partial_build.model.downloaded_timestamp_overlay_generation ==
             live_model.downloaded_timestamp_overlay_generation)

    def adopt_active_transfer_delta(
            self, applied_model: Model, root_file_ids: Set[str],
            partial_build: _ActiveTransferRootBuild,
    ) -> None:
        """Commit staged transfer floors after an authorized live publication."""
        def replace_snapshot_roots(
                target: dict[str, _RecentLiveTransferSnapshot],
                staged: dict[str, _RecentLiveTransferSnapshot],
        ) -> None:
            relevant = lambda key, snapshot: key in root_file_ids or snapshot.root_file_id in root_file_ids
            for key, snapshot in list(target.items()):
                if relevant(key, snapshot):
                    target.pop(key, None)
            target.update({key: snapshot for key, snapshot in staged.items() if relevant(key, snapshot)})

        replace_snapshot_roots(
            self.__recent_live_transfer_snapshots,
            partial_build.recent_live_transfer_snapshots,
        )
        replace_snapshot_roots(
            self.__retained_stopped_transfer_snapshots,
            partial_build.retained_stopped_transfer_snapshots,
        )
        self.__cached_model = applied_model
        self.__invalidation_reasons.clear()
        self.__pending_invalidation_tokens.clear()
        self.__lftp_touched_root_file_ids.clear()
        self.__active_touched_root_file_ids.clear()
        self.__lftp_regressed_root_file_ids.clear()

    def build_model(self) -> Model:
        if self.__cached_model is not None:
            return self.__cached_model

        model = Model()
        model.logger = self.__build_dummy_model_logger()  # ignore the logs for this temp model
        model.set_downloaded_timestamp_overlay_generation(
            self.__downloaded_timestamp_overlay_generation
        )
        live_transferred_file_ids: set[str] = set()
        effective_local_files = self.__build_effective_local_files()
        self.__cached_unresolved_staging_collision_file_ids = {
            file_id
            for file_id, local_file in effective_local_files.items()
            if self.__has_staging_collision_descendant(local_file)
        }
        self.__cached_terminalizable_staging_collision_file_ids = {
            file_id
            for file_id in self.__cached_unresolved_staging_collision_file_ids
            if self.__effective_local_tree_covers_remote_leaves_allowing_collision(
                self.__remote_file(file_id),
                effective_local_files.get(file_id),
            )
        }
        remote_files = self.__remote_files()
        all_file_ids: set[str] = set(effective_local_files).union(remote_files)
        source_file_ids: set[str] = set(effective_local_files).union(remote_files)
        for status_file_id in self.__lftp_statuses.keys():
            if status_file_id not in source_file_ids:
                all_file_ids.add(status_file_id)

        # A legacy name-only extraction marker cannot identify one of two
        # path-pair-scoped roots. Establish that ambiguity before root states
        # are rendered; the later visibility pass retains the suppression only
        # while the ambiguity still exists.
        source_name_counts: Dict[str, int] = {}
        for file_id in all_file_ids:
            source = effective_local_files.get(file_id) or remote_files.get(file_id)
            if source is None:
                status = self.__lftp_statuses.get(file_id)
                source_name = status.name if status is not None else file_id
            else:
                source_name = source.name
            source_name_counts[source_name] = source_name_counts.get(source_name, 0) + 1
        self.__suppressed_ambiguous_extracted_file_names = {
            file_name
            for file_name, count in source_name_counts.items()
            if count > 1 and file_name in self.__extracted_files
        }

        built_root_files: List[_BuiltRootFile] = []
        for file_id in all_file_ids:
            remote = remote_files.get(file_id, None)
            local = effective_local_files.get(file_id, None)
            status = self.__lftp_statuses.get(file_id, None)
            is_stopped = self.__is_stopped_file(file_id, remote, local)
            name = remote.name if remote else local.name if local else file_id
            if remote is None and local is None and status is None:
                # this should never happen, but just in case
                raise ModelError("Zero sources have a file object")

            # sanity check between the sources
            if remote is not None:
                is_dir = remote.is_dir
            elif local is not None:
                is_dir = local.is_dir
            else:
                assert status is not None
                is_dir = status.type == LftpJobStatus.Type.MIRROR
            if (remote and is_dir != remote.is_dir) or \
               (local and is_dir != local.is_dir) or \
               (status and is_dir != (status.type == LftpJobStatus.Type.MIRROR)):
                raise ModelError("Mismatch in is_dir between sources")

            model_file = ModelFile(name, is_dir)
            model_file.explicitly_stopped = is_stopped
            # Presentation proof requires each remote leaf; lifecycle state
            # retains its separate staging-root size fallback.
            model_file.complete_local_coverage = self.__directory_leaves_cover_remote_for_presentation(
                remote, local
            )
            path_pair_id = remote.path_pair_id if remote and remote.path_pair_id is not None else \
                local.path_pair_id if local else status.path_pair_id if status else None
            path_pair_name = remote.path_pair_name if remote and remote.path_pair_name is not None else \
                local.path_pair_name if local else status.path_pair_name if status else None
            self.__apply_path_pair_metadata(model_file, path_pair_id, path_pair_name)
            root_seen_file_ids: set[str] = set()
            (
                current_transfer_state,
                recent_transfer_state,
                retained_transfer_state,
                raw_current_transfer_state,
                arbitration_source,
            ) = self.__resolve_root_transfer_state(
                file_id,
                model_file,
                remote,
                local,
                status,
                is_stopped,
            )
            fill_transfer_state = current_transfer_state
            if fill_transfer_state is None:
                fill_transfer_state = recent_transfer_state
            if fill_transfer_state is None:
                fill_transfer_state = retained_transfer_state
            self.__fill_model_file(
                model_file,
                remote,
                local,
                fill_transfer_state,
                current_transfer_state is not None,
                status.file_id if status is not None else None,
                live_transferred_file_ids,
                self.__transfer_state(status.total_transfer_state) if status is not None and
                status.state == LftpJobStatus.State.RUNNING else None,
                status.id if status is not None else None,
            )
            self.__build_children(
                model_file,
                remote,
                local,
                status,
                root_seen_file_ids,
                live_transferred_file_ids,
            )
            self.__estimate_eta(model_file)
            incomplete_children, arbitration_source = self.__check_root_downloaded(
                file_id,
                model_file,
                remote,
                local,
                status,
                is_stopped,
                retained_transfer_state,
                arbitration_source,
            )
            self.__apply_local_only_union_progress(
                model_file, remote, self.__local_file(file_id),
            )
            self.__determine_state(model_file, local, incomplete_children)
            # Trace failed presentation/lifecycle decisions and explicit Stop
            # boundaries; successful, unstopped roots without a marker remain
            # quiet.
            trace_enabled = self.__is_stop_resume_trace_enabled("queue.exclusion", "info")
            if trace_enabled:
                effective_tree_proof = self.__effective_local_tree_proves_completion(remote, local)
                has_root_lifecycle_marker = (
                    file_id in (self.__downloaded_files or set()) or
                    file_id in self.__final_move_succeeded_files or
                    file_id in self.__extracted_files or
                    file_id in self.__move_failed_files
                )
                if (
                    not model_file.complete_local_coverage or
                    not effective_tree_proof or
                    is_stopped or
                    model_file.state == ModelFile.State.MOVE_FAILED or
                    has_root_lifecycle_marker
                ):
                    self.__record_root_default_decision(
                        file_id,
                        remote,
                        local,
                        status,
                        is_stopped,
                        model_file.complete_local_coverage,
                        effective_tree_proof,
                        status is not None or current_transfer_state is not None or
                        recent_transfer_state is not None or retained_transfer_state is not None,
                        self.__state_category(model_file),
                    )
            model_file.is_stoppable = self.__is_stoppable_model_file(
                model_file,
                local,
                current_transfer_state,
                status,
            )

            # Empty remote directory trees are metadata only. Keep a local
            # counterpart visible as Local Only, but do not create a row for a
            # remote-only tree with no transferable descendants.
            if (
                status is None
                and local is None
                and not model_file.remote_has_transferable_content
            ):
                continue

            if self.__is_stop_resume_trace_enabled():
                self.__trace_target_arbitration(
                    model_file,
                    status.file_id if status is not None else model_file.file_id,
                    is_stopped,
                    remote is not None,
                    local is not None,
                    local,
                    model_file.file_id in self.__active_file_ids,
                    ModelBuilder.__summarize_local_freshness(local),
                    status,
                    raw_current_transfer_state,
                    arbitration_source
                )
            if self.__is_target_archive_trace_enabled():
                self.__trace_target_archive_arbitration(
                    model_file,
                    status.file_id if status is not None else model_file.file_id,
                    is_stopped,
                    remote is not None,
                    local is not None,
                    local,
                    model_file.file_id in self.__active_file_ids,
                    ModelBuilder.__summarize_local_freshness(local),
                    status,
                    raw_current_transfer_state,
                    arbitration_source
                )

            built_root_files.append(_BuiltRootFile(
                model_file=model_file,
                normalized_local_root_path=self.__resolve_normalized_local_root_path(
                    local,
                    path_pair_id,
                ),
                is_local_only=local is not None
                and not model_file.remote_has_transferable_content
                and status is None,
                seen_file_ids=root_seen_file_ids,
            ))

        seen_names_by_path: Dict[str, Set[str]] = {}
        for built_root_file in built_root_files:
            normalized_local_root_path = built_root_file.normalized_local_root_path
            if normalized_local_root_path is None or built_root_file.is_local_only:
                continue
            if normalized_local_root_path not in seen_names_by_path:
                seen_names_by_path[normalized_local_root_path] = set()
            seen_names_by_path[normalized_local_root_path].add(built_root_file.model_file.name)

        local_only_root_winners: Dict[Tuple[str, str], _BuiltRootFile] = {}
        for built_root_file in built_root_files:
            normalized_local_root_path = built_root_file.normalized_local_root_path
            if normalized_local_root_path is None or not built_root_file.is_local_only:
                continue
            dedupe_key = (normalized_local_root_path, built_root_file.model_file.name)
            candidate_priority = (
                built_root_file.model_file.state.value,
                built_root_file.model_file.file_id,
            )
            current_winner = local_only_root_winners.get(dedupe_key)
            current_priority = (
                current_winner.model_file.state.value,
                current_winner.model_file.file_id,
            ) if current_winner is not None else None
            if current_priority is None or candidate_priority > current_priority:
                local_only_root_winners[dedupe_key] = built_root_file

        visible_root_files: List[_BuiltRootFile] = []
        for built_root_file in built_root_files:
            normalized_local_root_path = built_root_file.normalized_local_root_path
            if normalized_local_root_path is not None and normalized_local_root_path not in seen_names_by_path:
                seen_names_by_path[normalized_local_root_path] = set()
            if built_root_file.is_local_only and normalized_local_root_path is not None:
                if built_root_file.model_file.name in seen_names_by_path[normalized_local_root_path]:
                    continue
                dedupe_key = (normalized_local_root_path, built_root_file.model_file.name)
                if local_only_root_winners.get(dedupe_key) is not built_root_file:
                    continue
            visible_root_files.append(built_root_file)
            if normalized_local_root_path is not None:
                seen_names_by_path[normalized_local_root_path].add(built_root_file.model_file.name)

        if self.__is_model_presentation_trace_enabled():
            for built_root_file in visible_root_files:
                self.__record_model_presentation_anomaly(built_root_file.model_file)

        seen_file_ids: set[str] = set()
        for built_root_file in visible_root_files:
            seen_file_ids.add(built_root_file.model_file.file_id)
            seen_file_ids.update(built_root_file.seen_file_ids)
            model.add_file(built_root_file.model_file)

        self.__sweep_recent_live_transfer_snapshots(seen_file_ids)
        model.set_tree_file_count(len(seen_file_ids))
        self.__cached_model = model
        self.__invalidation_reasons.clear()
        self.__pending_invalidation_tokens.clear()
        self.__lftp_touched_root_file_ids.clear()
        self.__active_touched_root_file_ids.clear()
        self.__lftp_regressed_root_file_ids.clear()
        return model

    def __resolve_root_transfer_state(
        self,
        file_id: str,
        model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
        status: Optional[LftpJobStatus],
        is_stopped: bool,
    ) -> Tuple[
        Optional[_TransferState],
        Optional[_TransferState],
        Optional[_TransferState],
        Optional[_TransferState],
        str
    ]:
        # set the file state
        # for now we only set to Queued or Downloading
        # later after all children are built, we can set to Downloaded after performing a check
        recent_transfer_state = None
        retained_transfer_state = None
        arbitration_source = "scan_only"
        source_current_transfer_state = self.__transfer_state(status.total_transfer_state) if status and \
            status.state == LftpJobStatus.State.RUNNING else None
        raw_current_transfer_state = source_current_transfer_state
        if source_current_transfer_state is not None:
            _, previous_snapshot = self.__resolve_recent_live_transfer_snapshot(
                file_id,
                status.file_id if status is not None else None,
            )
            raw_current_transfer_state = self.__combine_split_root_transfer_state(
                source_current_transfer_state,
                remote,
                local,
                previous_snapshot,
                status.id if status is not None else None,
                status.type if status is not None else None,
            )
        current_transfer_state = raw_current_transfer_state if not is_stopped else None
        if is_stopped and raw_current_transfer_state is not None:
            retained_transfer_state = self.__build_retained_transfer_state(
                raw_current_transfer_state.size_local,
                remote.size if remote else None,
                raw_current_transfer_state.percent_local
            )
            self.__store_recent_live_transfer_snapshot(
                model_file.file_id,
                status.file_id if status is not None else model_file.file_id,
                raw_current_transfer_state,
                source_current_transfer_state,
                status.id if status is not None else None,
            )
            self.__store_retained_stopped_transfer_snapshot(
                model_file.file_id,
                status.file_id if status is not None else model_file.file_id,
                raw_current_transfer_state,
                source_current_transfer_state,
                status.id if status is not None else None,
            )
            arbitration_source = "retained_stopped_snapshot_from_live_status"
        elif current_transfer_state is not None:
            current_transfer_state = self.__coalesce_retained_stopped_transfer_state(
                file_id,
                status.file_id if status is not None else model_file.file_id,
                remote,
                local,
                current_transfer_state,
                source_current_transfer_state,
                status.id if status is not None else None,
                status.type if status is not None else None,
            )
            arbitration_source = "live_status"
            if current_transfer_state != raw_current_transfer_state:
                arbitration_source = "live_status_coalesced_with_retained_floor"
        elif status is not None:
            retained_transfer_state = self.__get_retained_stopped_transfer_state_without_live_progress(
                file_id,
                status.file_id,
                remote,
                local,
                preserve_when_local_growth_only=is_stopped
            )
            arbitration_source = "retained_stopped_snapshot_without_live_progress" \
                if retained_transfer_state is not None else \
                "suppressed_stopped_live_status" if is_stopped else "live_status_without_transfer_state"
        if current_transfer_state is None and status is None and not is_stopped:
            recent_transfer_state = self.__get_recent_live_transfer_state(file_id, remote, local)
            if recent_transfer_state is not None:
                arbitration_source = "recent_live_snapshot"
        if retained_transfer_state is None and status is None and is_stopped:
            retained_transfer_state = self.__get_retained_stopped_transfer_state_without_live_progress(
                file_id,
                model_file.file_id,
                remote,
                local,
                preserve_when_local_growth_only=True
            )
            if retained_transfer_state is not None:
                arbitration_source = "retained_stopped_snapshot"
        if retained_transfer_state is None and status is None and is_stopped:
            retained_transfer_state = self.__get_retained_recent_transfer_state(file_id, remote, local, remote, local)
            if retained_transfer_state is not None:
                self.__promote_recent_live_transfer_snapshot_to_stopped_floor(
                    file_id,
                    model_file.file_id
                )
                arbitration_source = "retained_recent_live_snapshot"
        if retained_transfer_state is None and status is None and is_stopped:
            retained_transfer_state = self.__get_stopped_staging_transfer_state_without_live_progress(
                remote, local
            )
            if retained_transfer_state is not None:
                arbitration_source = "derived_stopped_staging_scan"
        if status and not is_stopped:
            model_file.state = ModelFile.State.QUEUED if status.state == LftpJobStatus.State.QUEUED \
                               else ModelFile.State.DOWNLOADING
            if status.state == LftpJobStatus.State.QUEUED:
                self.__evict_recent_live_transfer_snapshots(status.file_id)
                if arbitration_source == "scan_only":
                    arbitration_source = "live_status_queued"
        elif recent_transfer_state:
            model_file.state = ModelFile.State.DOWNLOADING
        return (
            current_transfer_state,
            recent_transfer_state,
            retained_transfer_state,
            raw_current_transfer_state,
            arbitration_source,
        )

    def __build_children(
        self,
        root_model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
        status: Optional[LftpJobStatus],
        seen_file_ids: Set[str],
        live_transferred_file_ids: Set[str],
    ) -> None:
        # Traverse SystemFile children tree in BFS order
        # Store (remote, local, status, model_file) tuple in traversal frontier where remote and local
        # correspond to the same node in both remote and local SystemFile trees, status corresponds
        # to the LFTP status for the entire tree, and model_file corresponds to the generated ModelFile
        # for the pair
        # Note: in this case the frontier contains nodes that have already been process, it is
        #       merely used for traversing children
        frontier: deque[tuple[
            Optional[SystemFile], Optional[SystemFile], Optional[LftpJobStatus],
            ModelFile, Optional[SystemFile], Optional[SystemFile], bool
        ]] = deque()
        if remote or local:
            frontier.append((
                remote, local, status, root_model_file, remote, local,
                bool(getattr(local, "has_staging_collision", False)),
            ))
        while frontier:
            (
                _remote, _local, _status, _model_file, _root_remote, _root_local,
                _ancestor_has_staging_collision,
            ) = frontier.popleft()
            _remote_children: dict[str, SystemFile] = {sf.name: sf for sf in _remote.iter_children()} if _remote else {}
            _local_children: dict[str, SystemFile] = {sf.name: sf for sf in _local.iter_children()} if _local else {}
            _all_children_names: set[str] = set(_remote_children).union(_local_children)
            for _child_name in _all_children_names:
                _remote_child = _remote_children.get(_child_name, None)
                _local_child = _local_children.get(_child_name, None)
                if _remote_child is not None:
                    _is_dir = _remote_child.is_dir
                else:
                    assert _local_child is not None
                    _is_dir = _local_child.is_dir
                # sanity check is_dir
                if (_remote_child and _is_dir != _remote_child.is_dir) or \
                   (_local_child and _is_dir != _local_child.is_dir):
                    raise ModelError("Mismatch in is_dir between child sources")
                _child_model_file = ModelFile(_child_name, _is_dir)
                self.__apply_path_pair_metadata(
                    _child_model_file,
                    _model_file.path_pair_id,
                    _model_file.path_pair_name
                )

                # add it to the parent right away so we can access the full path
                _model_file.add_child(_child_model_file)
                seen_file_ids.add(_child_model_file.file_id)
                _child_is_stopped = _child_model_file.file_id in self.__stopped_files
                _child_model_file.explicitly_stopped = _child_is_stopped
                _child_model_file.complete_local_coverage = not _ancestor_has_staging_collision and \
                    self.__directory_leaves_cover_remote_for_presentation(_remote_child, _local_child)

                # Set the state, first matching criteria below decides state
                #   child is a directory: Default
                #   child is active: Downloading
                #   child local_size >= remote_size: Downloaded
                #   remote child exists and root is Queued or Downloading: Queued
                #   Default
                # Result:
                #   subdirectories are always Default
                #   downloading files are Downloading
                #   finished files are Downloaded
                #   Queued and Downloading root's unfinished files are Queued
                #   Local-only files are Default
                _child_current_transfer_state: Optional[_TransferState] = None
                _child_recent_transfer_state: Optional[_TransferState] = None
                _child_arbitration_source = "scan_only"
                if _status and _status.state == LftpJobStatus.State.RUNNING and \
                        not self.__is_stopped_file(_status.file_id, _root_remote, _root_local, _status) and \
                        not _child_is_stopped:
                    # Transfer states are in root-relative paths.
                    _child_status_path = "/".join(_child_model_file.full_path.split(os.sep)[1:])
                    for active_name, active_state in _status.get_active_file_transfer_states():
                        if active_name == _child_status_path:
                            _child_current_transfer_state = self.__transfer_state(active_state)
                            break
                    if _child_current_transfer_state is not None:
                        _child_arbitration_source = "live_status"
                if _child_current_transfer_state is None and _status is None:
                    _child_recent_transfer_state = self.__get_recent_live_transfer_state(
                        _child_model_file.file_id,
                        _remote_child,
                        _local_child,
                        _root_remote,
                        _root_local
                    )
                    if _child_recent_transfer_state is not None:
                        _child_arbitration_source = "recent_live_snapshot"
                if _is_dir:
                    _child_model_file.state = ModelFile.State.DEFAULT
                elif _child_current_transfer_state:
                    _child_model_file.state = ModelFile.State.DOWNLOADING
                elif _child_recent_transfer_state:
                    _child_model_file.state = ModelFile.State.DOWNLOADING
                elif _remote_child and _local_child is not None and \
                        self.__is_authoritative_local_file(_local_child) and \
                        _local_child.size >= _remote_child.size:
                    _child_model_file.state = ModelFile.State.DOWNLOADED
                elif _remote_child and not _child_is_stopped and \
                        root_model_file.state in (ModelFile.State.QUEUED, ModelFile.State.DOWNLOADING):
                    _child_model_file.state = ModelFile.State.QUEUED
                    _child_arbitration_source = "queued_by_root_state"
                else:
                    _child_model_file.state = ModelFile.State.DEFAULT
                    if _child_is_stopped and _status is not None:
                        _child_arbitration_source = "suppressed_stopped_live_status"

                # fill the rest
                self.__fill_model_file(
                    _child_model_file,
                    _remote_child,
                    _local_child,
                    _child_current_transfer_state if _child_current_transfer_state is not None
                    else _child_recent_transfer_state,
                    _child_current_transfer_state is not None,
                    status.file_id if status is not None else None,
                    live_transferred_file_ids,
                )
                _child_model_file.is_stoppable = self.__is_stoppable_model_file(
                    _child_model_file,
                    _local_child,
                    _child_current_transfer_state,
                    _status,
                )
                if self.__is_stop_resume_trace_enabled():
                    self.__trace_target_arbitration(
                        _child_model_file,
                        root_model_file.file_id,
                        _child_is_stopped,
                        _remote_child is not None,
                        _local_child is not None,
                        _local_child,
                        _child_model_file.file_id in self.__active_file_ids,
                        ModelBuilder.__summarize_local_freshness(_local_child),
                        _status,
                        _child_current_transfer_state,
                        _child_arbitration_source
                    )
                # add child to frontier
                frontier.append((
                    _remote_child, _local_child, _status, _child_model_file, _root_remote, _root_local,
                    _ancestor_has_staging_collision or
                    bool(getattr(_local_child, "has_staging_collision", False)),
                ))

    def __fill_model_file(
        self,
        model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
        transfer_state: Optional[_TransferState],
        store_recent_snapshot: bool,
        recent_snapshot_root_file_id: Optional[str],
        live_transferred_file_ids: Set[str],
        raw_transfer_state: Optional[_TransferState] = None,
        lftp_job_id: Optional[int] = None,
    ) -> None:
        # set local and remote sizes
        model_file.remote_present = remote is not None
        model_file.local_present = local is not None
        model_file.remote_has_transferable_content = self.__has_remote_transferable_content(remote)
        if remote:
            model_file.remote_size = remote.size
        if local:
            model_file.local_size = local.size

        # LFTP counters are normalized to whole-root progress before this
        # point because resumed jobs can report only their remaining subset.

        # set the downloading speed and eta
        if transfer_state:
            if store_recent_snapshot:
                self.__store_recent_live_transfer_snapshot(
                    model_file.file_id,
                    recent_snapshot_root_file_id if recent_snapshot_root_file_id is not None else model_file.file_id,
                    transfer_state,
                    raw_transfer_state,
                    lftp_job_id,
                )
            download_progress = ModelBuilder.__normalize_download_progress(transfer_state.percent_local)
            if download_progress is not None:
                model_file.download_progress = download_progress
            if transfer_state.size_local is not None:
                model_file.transferred_size = transfer_state.size_local
                live_transferred_file_ids.add(model_file.file_id)
            model_file.downloading_speed = transfer_state.speed
            model_file.eta = transfer_state.eta

        # set the transferred size (only if file or dir exists on both ends)
        if local and remote:
            self.__update_transferred_size(model_file, remote, local, live_transferred_file_ids)

        # set the is_extractable flag
        self.__update_extractable_flag(model_file)

        # set the timestamps
        self.__update_timestamps(model_file, remote, local)
        downloaded_timestamp = self.__downloaded_timestamps.get(model_file.file_id)
        if downloaded_timestamp is not None:
            try:
                model_file.downloaded_timestamp = datetime.fromtimestamp(downloaded_timestamp)
            except (OverflowError, OSError, ValueError):
                # Persisted values are schema-validated as finite/nonnegative,
                # but platform datetime ranges are narrower than float epochs.
                # Treat an unrepresentable value as unknown rather than
                # breaking model refresh.
                model_file.downloaded_timestamp = None

    @staticmethod
    def __update_transferred_size(
        model_file: ModelFile,
        remote: SystemFile,
        local: SystemFile,
        live_transferred_file_ids: Set[str],
    ) -> None:
        if model_file.is_dir:
            if model_file.transferred_size is None:
                # dir transferred size is updated by child files
                model_file.transferred_size = 0
        else:
            if model_file.transferred_size is None:
                if ModelBuilder.__is_authoritative_local_file(local):
                    model_file.transferred_size = min(local.size, remote.size)

            if model_file.transferred_size is not None:
                # also update all parent directories
                _parent_file = model_file.parent
                while _parent_file is not None:
                    if _parent_file.file_id in live_transferred_file_ids:
                        break
                    if _parent_file.transferred_size is None:
                        _parent_file.transferred_size = 0
                    _parent_file.transferred_size += model_file.transferred_size
                    _parent_file = _parent_file.parent

    @staticmethod
    def __apply_local_only_union_progress(
        model_file: ModelFile,
        remote: Optional[SystemFile],
        scanned_local: Optional[SystemFile],
    ) -> None:
        """Project local-only leaves into the displayed transfer union.

        Scanner and LFTP values remain the authority for remote-covered bytes.
        A local-only leaf contributes only to a remote-present ancestor's
        display union; its own Local Only fields stay untouched.  This keeps
        startup/local-only authority separate from the derived display total.
        """
        local_only_size = ModelBuilder.__authoritative_final_local_only_size(remote, scanned_local)
        if remote is not None and model_file.is_dir and \
                model_file.remote_has_transferable_content and local_only_size:
            model_file.display_size_total = (model_file.remote_size or 0) + local_only_size
            model_file.display_transferred_size = (model_file.transferred_size or 0) + local_only_size
        remote_children = {child.name: child for child in remote.iter_children()} if remote else {}
        local_children = {child.name: child for child in scanned_local.iter_children()} if scanned_local else {}
        for child in model_file.iter_children():
            remote_child = remote_children.get(child.name)
            local_child = local_children.get(child.name)
            if child.is_dir and remote_child is not None and local_child is not None and \
                    remote_child.is_dir == local_child.is_dir:
                ModelBuilder.__apply_local_only_union_progress(child, remote_child, local_child)

    @staticmethod
    def __authoritative_final_local_only_size(
        remote: Optional[SystemFile], scanned_local: Optional[SystemFile],
    ) -> int:
        """Count non-staging final leaves absent from the remote tree.

        This intentionally walks scanner authority rather than rendered model
        children: active staging overlays can omit final-only siblings.
        """
        if scanned_local is None or scanned_local.is_staging or \
                getattr(scanned_local, "has_staging_collision", False):
            return 0
        if remote is None:
            if not scanned_local.is_dir:
                return scanned_local.size
            return sum(ModelBuilder.__authoritative_final_local_only_size(None, child)
                       for child in scanned_local.iter_children())
        if remote.is_dir != scanned_local.is_dir:
            return 0
        if not scanned_local.is_dir:
            return 0
        remote_children = {child.name: child for child in remote.iter_children()}
        return sum(
            ModelBuilder.__authoritative_final_local_only_size(
                remote_children.get(child.name), child,
            )
            for child in scanned_local.iter_children()
        )

    @staticmethod
    def __update_extractable_flag(model_file: ModelFile) -> None:
        if not model_file.is_dir and Extract.is_archive_fast(model_file.name):
            model_file.is_extractable = True
            # Also set the flag for all of its parents
            _parent_file = model_file.parent
            while _parent_file is not None:
                _parent_file.is_extractable = True
                _parent_file = _parent_file.parent

    @staticmethod
    def __update_timestamps(
        model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
    ) -> None:
        if local:
            if local.timestamp_created:
                model_file.local_created_timestamp = local.timestamp_created
            if local.timestamp_modified:
                model_file.local_modified_timestamp = local.timestamp_modified
        if remote:
            if remote.timestamp_created:
                model_file.remote_created_timestamp = remote.timestamp_created
            if remote.timestamp_modified:
                model_file.remote_modified_timestamp = remote.timestamp_modified

    @staticmethod
    def __estimate_eta(model_file: ModelFile) -> None:
        # estimate the ETA for the root if it's not available
        if model_file.state == ModelFile.State.DOWNLOADING and \
                model_file.eta is None and \
                model_file.downloading_speed is not None and \
                model_file.downloading_speed > 0 and \
                model_file.remote_size is not None and \
                model_file.transferred_size is not None:
            # First-order estimate
            remaining_size = max(model_file.remote_size - model_file.transferred_size, 0)
            model_file.eta = int(math.ceil(remaining_size / model_file.downloading_speed))

    def __check_root_downloaded(
        self,
        file_id: str,
        model_file: ModelFile,
        remote: Optional[SystemFile],
        local: Optional[SystemFile],
        status: Optional[LftpJobStatus],
        is_stopped: bool,
        retained_transfer_state: Optional[_TransferState],
        arbitration_source: str,
    ) -> Tuple[bool, str]:
        incomplete_children = False

        if model_file.state == ModelFile.State.DOWNLOADING and \
                self.__local_file_proves_download_completion(local, remote) and \
                self.__effective_local_tree_proves_completion(remote, local):
            self.__evict_transfer_completion_snapshots(
                file_id,
                status.file_id if status is not None else model_file.file_id
            )
            model_file.state = ModelFile.State.DOWNLOADED
            model_file.transferred_size = remote.size if remote is not None else model_file.local_size
            model_file.download_progress = None
            model_file.downloading_speed = None
            model_file.eta = None
            arbitration_source = "suppressed_by_authoritative_local_completion"
        if model_file.state == ModelFile.State.DOWNLOADING and \
                status is None and \
                not is_stopped and \
                remote is not None and \
                local is not None and \
                getattr(local, "is_staging", False) and \
                local.size >= remote.size and \
                self.__effective_local_tree_proves_completion(remote, local) and \
                not self.__has_incomplete_remote_file_children(model_file) and \
                self.__resolve_recent_live_transfer_snapshot(
                    file_id,
                    status.file_id if status is not None else model_file.file_id
                )[1] is not None:
            self.__evict_transfer_completion_snapshots(
                file_id,
                status.file_id if status is not None else model_file.file_id
            )
            model_file.state = ModelFile.State.DOWNLOADED
            model_file.transferred_size = remote.size
            model_file.download_progress = None
            model_file.downloading_speed = None
            model_file.eta = None
            arbitration_source = "suppressed_by_staging_completion_after_live_status_lost"

        # now we can determine if root is Downloaded
        # root is Downloaded if all child remote files are Downloaded
        # again we use BFS to traverse
        if model_file.state == ModelFile.State.DEFAULT:
            if not model_file.is_dir and \
                    not (is_stopped and retained_transfer_state is not None) and \
                    model_file.local_size is not None and \
                    model_file.remote_size is not None and \
                    self.__is_authoritative_local_file(local) and \
                    self.__effective_local_tree_proves_completion(remote, local) and \
                    model_file.local_size >= model_file.remote_size:
                # root is a finished single file
                model_file.state = ModelFile.State.DOWNLOADED
            elif not model_file.is_dir and \
                    status is None and \
                    not is_stopped and \
                    model_file.local_size is not None and \
                    model_file.remote_size is not None and \
                    getattr(local, "is_staging", False) and \
                    self.__effective_local_tree_proves_completion(remote, local) and \
                    model_file.local_size >= model_file.remote_size:
                # Keep scan-only recovery for full-size staging copies so
                # they can leave incomplete and continue through the
                # normal completion path.
                model_file.state = ModelFile.State.DOWNLOADED
                model_file.transferred_size = model_file.remote_size
                model_file.download_progress = None
                model_file.downloading_speed = None
                model_file.eta = None
                arbitration_source = "staging_completion_without_live_status"
            elif not model_file.is_dir and \
                    model_file.local_size is not None and \
                    model_file.remote_size is None and \
                    self.__is_authoritative_local_file(local) and \
                    self.__model_file_matches_persisted_name(model_file, self.__downloaded_files):
                # keep previously-downloaded local-only files recognizable
                model_file.state = ModelFile.State.DOWNLOADED
            elif model_file.is_dir and model_file.remote_size is not None:
                if status is None and \
                        not is_stopped and \
                        local is not None and \
                        getattr(local, "is_staging", False) and \
                        self.__effective_local_tree_proves_completion(remote, local) and \
                        local.size >= model_file.remote_size:
                    # A fully staged directory copy should be treated as complete even
                    # if live transfer state has already disappeared.
                    if not self.__has_incomplete_remote_file_children(model_file):
                        model_file.state = ModelFile.State.DOWNLOADED
                        model_file.transferred_size = model_file.remote_size
                        model_file.download_progress = None
                        model_file.downloading_speed = None
                        model_file.eta = None
                        arbitration_source = "staging_completion_without_live_status"
                else:
                    # root is a directory that also exists remotely
                    # check all the children
                    all_downloaded = True
                    has_downloadable_children = False
                    frontier = deque(model_file.iter_children())
                    while frontier:
                        _child_file = frontier.popleft()
                        if not _child_file.is_dir and \
                                _child_file.remote_size is not None:
                            has_downloadable_children = True
                            if _child_file.state != ModelFile.State.DOWNLOADED:
                                all_downloaded = False
                                break
                        frontier.extend(_child_file.iter_children())
                    # A split root can finish through a delta-only LFTP job:
                    # final leaves omitted from Queue use the portable source
                    # identity, while staged leaves retain their physical
                    # completion proof.  The same complete-tree proof is
                    # sufficient even before the child display states are
                    # individually promoted.
                    trusted_split_completion = self.__trees_have_verified_complete_staging_remote_identity(
                        remote, local
                    )
                    if has_downloadable_children and (all_downloaded or trusted_split_completion) and \
                            self.__effective_local_tree_proves_completion(remote, local):
                        model_file.state = ModelFile.State.DOWNLOADED
                    else:
                        incomplete_children = True

        return incomplete_children, arbitration_source

    def __determine_state(self, model_file: ModelFile, local: Optional[SystemFile], incomplete_children: bool):
        model_file.final_move_succeeded = model_file.file_id in self.__final_move_succeeded_files
        downloaded_marker_present = model_file.file_id in (self.__downloaded_files or set())
        final_move_marker_present = model_file.file_id in self.__final_move_succeeded_files
        lifecycle_subject = downloaded_marker_present or final_move_marker_present
        if lifecycle_subject:
            self.__record_lifecycle_persist_breadcrumb("persist_authority_before", model_file, {
                "local_present": local is not None,
                "remote_present": self.__remote_file(model_file.file_id) is not None,
                "local_size_present": model_file.local_size is not None,
                "downloaded_marker_present": downloaded_marker_present,
                "final_move_marker_present": final_move_marker_present,
                "unknown_local": model_file.path_pair_id in self.__unknown_local_path_pair_ids,
                "pre_state": self.__state_category(model_file),
                "fence": "none",
            })
        self.__check_persist_authority(model_file, incomplete_children)
        if lifecycle_subject:
            self.__record_lifecycle_persist_breadcrumb("persist_authority_after", model_file, {
                "state_category": self.__state_category(model_file),
                "fence": "none",
            })
        self.__check_extracting(model_file)

        # next we check if root is Extracted
        # root is Extracted if it is in Downloaded state and in extracted files list
        # Note: Default files aren't marked extracted because they can still be queued
        #       for download, and it doesn't make sense to queue after extracting
        #       If a Default file is extracted, it will return back to the Default state
        has_exact_extracted_marker = model_file.file_id in self.__extracted_files
        if self.__model_file_matches_persisted_name(model_file, self.__extracted_files) and \
                model_file.state == ModelFile.State.DOWNLOADED and \
                self.__is_authoritative_local_file(local) and \
                (has_exact_extracted_marker or
                 model_file.name not in self.__suppressed_ambiguous_extracted_file_names):
                model_file.state = ModelFile.State.EXTRACTED

        self.__check_validating(model_file)
        # Terminal move failures are authoritative until an explicit local
        # cleanup or a successful retry clears their canonical identity.
        if model_file.file_id in self.__move_failed_files:
            model_file.state = ModelFile.State.MOVE_FAILED

    def __check_persist_authority(self, model_file: ModelFile, _incomplete_children: bool):
        # next we check persisted markers for previously downloaded files
        if self.__downloaded_files is None:
            return
        if model_file.path_pair_id in self.__unknown_local_path_pair_ids:
            return
        if model_file.state == ModelFile.State.DEFAULT and \
                model_file.local_size is None and \
                self.__model_file_matches_persisted_name(model_file, self.__downloaded_files):
            model_file.state = ModelFile.State.DELETED

    def __check_extracting(self, model_file: ModelFile):
        # next we check if root is Extracting
        # root is Extracting if it's part of an extract status, in an expected state,
        # and exists locally
        # if root is NOT in an expected state, then ignore the extract status
        # and report a warning message, as this shouldn't be happening
        if model_file.file_id not in self.__extract_statuses:
            return
        extract_status = self.__extract_statuses[model_file.file_id]
        if model_file.is_dir != extract_status.is_dir:
            raise ModelError("Mismatch in is_dir between file and extract status")
        if model_file.state in (
                ModelFile.State.DEFAULT,
            ModelFile.State.DOWNLOADED
        ) and model_file.local_size is not None:
            model_file.state = ModelFile.State.EXTRACTING
        else:
            if model_file.local_size is None:
                self.logger.warning("File {} has extract status but doesn't exist locally!".format(
                    model_file.name
                ))
            else:
                self.logger.warning("File {} has extract status but is in state {}".format(
                    model_file.name,
                    str(model_file.state)
                ))

    def __check_validating(self, model_file: ModelFile):
        # next we check if root is Validating
        # root is Validating if it has a validate status, is in an expected state, and exists locally
        validation_status = self.__validation_statuses.get(model_file.file_id)
        if validation_status is not None and model_file.state in (
                ModelFile.State.DEFAULT,
                ModelFile.State.DOWNLOADED,
                ModelFile.State.EXTRACTED,
                ModelFile.State.VALIDATING,
                ModelFile.State.VALIDATED,
                ModelFile.State.CORRUPT
        ) and model_file.local_size is not None and model_file.remote_size is not None:
            model_file.state = validation_status.state
            model_file.validation_progress = validation_status.progress
            model_file.validation_error = validation_status.error
            model_file.corrupt_chunks = validation_status.corrupt_chunks
