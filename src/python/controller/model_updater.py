# Copyright 2017, Inderpreet Singh, All rights reserved.

"""Model update orchestration extracted from controller.py.

The controller still owns the underlying state, but the per-tick refresh loop
now lives here so `Controller.process()` only coordinates the pipeline and
delegates the model refresh boundary.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from threading import Lock, RLock
from datetime import datetime, timedelta
from typing import Callable, Optional, Sequence, TYPE_CHECKING, cast

from common import Context, PathPair
from common.performance_diagnostics import DURATION_MODEL_BUILD
from lftp import Lftp, LftpError, LftpJobStatus, LftpJobStatusParserError
from model import Model, ModelDiff, ModelDiffUtil, ModelError, ModelFile
from system import SystemFile
from transfer import RcloneTransferBackend

from common.exclude_patterns import filter_excluded_files

from .controller_persist import ControllerPersist
from .extract import ExtractCompletedResult, ExtractFailedResult, ExtractProcess, ExtractStatus
from .model_builder import ModelBuilder
from .scan import ScannerProcess, ScannerResult
from .validate import ValidateProcess

if TYPE_CHECKING:
    from .controller import Controller


class _ProgressiveScanAccumulator:
    """Reconcile manifest/root events without exposing unknown absence."""

    def __init__(self) -> None:
        self.__session_token: Optional[str] = None
        self.__committed: dict[tuple[Optional[str], str], SystemFile] = {}
        self.__working: dict[int, dict[Optional[str], dict[str, SystemFile]]] = {}
        self.__manifests: dict[int, dict[Optional[str], Optional[set[str]]]] = {}
        self.__active_generation: dict[Optional[str], int] = {}
        self.__failed_pairs: set[tuple[int, Optional[str]]] = set()
        self.__authoritative: dict[tuple[Optional[str], str], Optional[SystemFile]] = {}
        self.__incomplete_pairs: set[Optional[str]] = set()
        self.__completed_pairs: set[Optional[str]] = set()

    def set_session_token(self, session_token: Optional[str]) -> None:
        """Bind active evidence to the current scanner process identity."""
        if not isinstance(session_token, str) or not session_token:
            return
        if self.__session_token == session_token:
            return
        self.__session_token = session_token
        self.__working.clear()
        self.__manifests.clear()
        self.__active_generation.clear()
        self.__failed_pairs.clear()
        self.__incomplete_pairs.clear()
        self.__authoritative.clear()
        self.__completed_pairs.clear()

    @staticmethod
    def __pair_for_file(file: SystemFile, result: ScannerResult) -> Optional[str]:
        if isinstance(file.path_pair_id, str) or file.path_pair_id is None:
            return file.path_pair_id
        ids = list(result.scanned_path_pair_ids)
        return ids[0] if len(ids) == 1 else None

    @staticmethod
    def __legacy_result_as_progress_snapshot(event: ScannerResult) -> Optional[ScannerResult]:
        """Make legacy evidence explicit when it shares a progressive drain."""
        selected_ids = set(event.scanned_path_pair_ids)
        if not selected_ids:
            if bool(getattr(event, "is_targeted_scan", False)):
                return None
            selected_ids = {None}
        failed = bool(event.failed)
        unknown_ids = set(event.unknown_path_pair_ids)
        if failed and not unknown_ids:
            unknown_ids = set(selected_ids)
        return ScannerResult(
            event.timestamp,
            event.files,
            event.malformed_status_only_file_ids,
            event.managed_extract_file_ids,
            selected_ids,
            failed=failed,
            error_message=event.error_message,
            generation=event.generation,
            is_progress=True,
            root_names=event.root_names,
            completed_path_pair_ids=set() if failed else set(selected_ids),
            is_scan_final=not failed,
            unknown_path_pair_ids=unknown_ids,
            session_token=event.session_token,
            is_full_snapshot=not failed,
            full_snapshot_path_pair_ids=set() if failed else set(selected_ids),
            is_targeted_scan=event.is_targeted_scan,
        )

    def apply(self, events: Sequence[ScannerResult]) -> Optional[ScannerResult]:
        if not events:
            return None
        if self.__session_token is None:
            session_token = next(
                (getattr(event, "session_token", None) for event in events
                 if isinstance(getattr(event, "session_token", None), str)),
                None,
            )
            self.set_session_token(session_token)
        if self.__session_token is not None:
            events = [
                event for event in events
                if getattr(event, "session_token", None) == self.__session_token
            ]
            if not events:
                return None
        # Generations are owned by path pair, not by an entire queue drain.
        # A targeted refresh of A can validly overtake a queued full snapshot
        # for untouched B; the per-pair comparison inside the event loop
        # rejects only evidence superseded for that same pair.
        accepted = list(events)
        newest_generation = max((int(getattr(event, "generation", 0)) for event in accepted), default=0)
        if not accepted:
            return None
        latest = accepted[-1]
        if not any(getattr(event, "is_progress", False) for event in accepted):
            selected_ids = set(latest.scanned_path_pair_ids)
            if not selected_ids:
                if bool(getattr(latest, "is_targeted_scan", False)):
                    return None
                selected_ids = {None}
            latest_generation = int(getattr(latest, "generation", 0))
            if any(
                latest_generation < self.__active_generation.get(pair_id, -1)
                for pair_id in selected_ids
            ):
                return None
            if latest.failed:
                return latest
            for pair_id in selected_ids:
                self.__active_generation[pair_id] = latest_generation
                self.__completed_pairs.add(pair_id)
                self.__incomplete_pairs.discard(pair_id)
                for key in [key for key in self.__committed if key[0] == pair_id]:
                    self.__committed.pop(key, None)
                    self.__authoritative.pop(key, None)
            for file in latest.files:
                pair_id = file.path_pair_id if file.path_pair_id in selected_ids else (
                    next(iter(selected_ids)) if len(selected_ids) == 1 else file.path_pair_id
                )
                self.__committed[(pair_id, file.name)] = file
                self.__authoritative[(pair_id, file.name)] = file
            return latest
        accepted = [
            event if event.is_progress else self.__legacy_result_as_progress_snapshot(event)
            for event in accepted
        ]
        accepted = [event for event in accepted if event is not None]
        if not accepted:
            return None
        latest = accepted[-1]
        malformed: list[str] = []
        managed: list[str] = []
        failed = False
        error_message: Optional[str] = None
        completed: set[Optional[str]] = set()
        touched: set[Optional[str]] = set()
        for event in accepted:
            malformed.extend(event.malformed_status_only_file_ids)
            managed.extend(event.managed_extract_file_ids)
            generation = int(getattr(event, "generation", 0))
            ids = set(event.scanned_path_pair_ids)
            if not ids:
                ids = {None}
            for pair_id in ids:
                previous_generation = self.__active_generation.get(pair_id, -1)
                if generation < previous_generation:
                    continue
                if generation > previous_generation:
                    self.__active_generation[pair_id] = generation
                    self.__incomplete_pairs.add(pair_id)
                    self.__completed_pairs.discard(pair_id)
                    for old_generation in list(self.__working):
                        if old_generation < generation:
                            self.__working[old_generation].pop(pair_id, None)
                            self.__manifests.get(old_generation, {}).pop(pair_id, None)
                            self.__failed_pairs.discard((old_generation, pair_id))
                            if not self.__working[old_generation]:
                                self.__working.pop(old_generation, None)
                            if not self.__manifests.get(old_generation):
                                self.__manifests.pop(old_generation, None)
                    previous = {
                        name: file for (stored_pair, name), file in self.__committed.items()
                        if stored_pair == pair_id
                    }
                    self.__working.setdefault(generation, {})[pair_id] = previous
                    self.__manifests.setdefault(generation, {})[pair_id] = None
                    self.__failed_pairs.discard((generation, pair_id))
                    for key in [key for key in self.__authoritative if key[0] == pair_id]:
                        self.__authoritative.pop(key, None)
                touched.add(pair_id)
                if event.failed:
                    self.__failed_pairs.add((generation, pair_id))
                    for key in [key for key in self.__authoritative if key[0] == pair_id]:
                        self.__authoritative.pop(key, None)
                    failed = True
                    error_message = event.error_message
                    self.__incomplete_pairs.add(pair_id)
                    continue
                working = self.__working.setdefault(generation, {}).setdefault(pair_id, {})
                full_snapshot_ids = set(getattr(event, "full_snapshot_path_pair_ids", set()))
                full_snapshot = bool(getattr(event, "is_full_snapshot", False)) \
                    and pair_id in full_snapshot_ids
                if full_snapshot:
                    # The final aggregate is lossless even when intermediate
                    # queue events were dropped. Rebuild this pair from it;
                    # failed generations never carry this flag.
                    working.clear()
                manifest = getattr(event, "root_names", None)
                if manifest is not None:
                    self.__manifests.setdefault(generation, {})[pair_id] = set(manifest)
                    # Progress manifests are bounded/lossy transport hints.
                    # They can establish that a root is present, but must not
                    # establish that a previously committed root is absent.
                    # Only the lossless aggregate below may replace a pair.
                    if full_snapshot:
                        for key in [key for key in self.__committed if key[0] == pair_id]:
                            if key[1] not in manifest:
                                self.__authoritative[key] = None
                            else:
                                self.__authoritative.pop(key, None)
                for file in event.files:
                    file_pair = self.__pair_for_file(file, event)
                    if file_pair != pair_id and len(ids) > 1:
                        continue
                    working[file.name] = file
                    self.__authoritative[(pair_id, file.name)] = file
                if full_snapshot:
                    self.__manifests.setdefault(generation, {})[pair_id] = set(working)
                # A progressive completion marker can arrive after earlier
                # root batches were evicted from the bounded queue.  Do not
                # let it complete a pair or prove absence; the same scan's
                # lossless full snapshot is the authority boundary.
                if full_snapshot and pair_id in event.completed_path_pair_ids:
                    manifest_names = self.__manifests.get(generation, {}).get(pair_id)
                    if manifest_names is not None:
                        for name in list(working):
                            if name not in manifest_names:
                                working.pop(name, None)
                        for name in manifest_names:
                            self.__authoritative.setdefault((pair_id, name), None)
                    for name, file in working.items():
                        self.__committed[(pair_id, name)] = file
                    for key in [key for key in self.__committed if key[0] == pair_id and key[1] not in working]:
                        self.__committed.pop(key, None)
                        self.__authoritative.pop(key, None)
                    completed.add(pair_id)
                    self.__incomplete_pairs.discard(pair_id)
                    self.__completed_pairs.add(pair_id)

        visible: dict[tuple[Optional[str], str], SystemFile] = dict(self.__committed)
        for generation, pair_maps in self.__working.items():
            for pair_id, files in pair_maps.items():
                if self.__active_generation.get(pair_id) != generation:
                    continue
                if (generation, pair_id) in self.__failed_pairs:
                    continue
                for name, file in files.items():
                    visible[(pair_id, name)] = file
        return ScannerResult(
            latest.timestamp,
            list(visible.values()),
            malformed_status_only_file_ids=sorted(set(malformed)),
            managed_extract_file_ids=sorted(set(managed)),
            scanned_path_pair_ids=completed,
            # A multi-pair scan may complete healthy pairs while another pair
            # fails.  Publish the healthy incremental view and reserve the
            # failed flag for an all-unknown update so callers do not discard
            # unrelated progress.
            failed=failed and not completed,
            error_message=error_message,
            generation=newest_generation,
            is_progress=any(getattr(event, "is_progress", False) for event in accepted),
            completed_path_pair_ids=completed,
            unknown_path_pair_ids=set(self.__incomplete_pairs),
            is_scan_final=bool(completed) and not self.__incomplete_pairs and not failed,
        )

    def snapshot(self) -> dict[tuple[Optional[str], str], SystemFile]:
        visible: dict[tuple[Optional[str], str], SystemFile] = dict(self.__committed)
        for generation, pair_maps in self.__working.items():
            for pair_id, files in pair_maps.items():
                if self.__active_generation.get(pair_id) != generation:
                    continue
                if (generation, pair_id) in self.__failed_pairs:
                    continue
                for name, file in files.items():
                    visible[(pair_id, name)] = file
        return visible

    def authority(self) -> dict[tuple[Optional[str], str], Optional[SystemFile]]:
        return dict(self.__authoritative)

    def incomplete_pairs(self) -> set[Optional[str]]:
        return set(self.__incomplete_pairs)

    def completed_pairs(self) -> set[Optional[str]]:
        return set(self.__completed_pairs)


class _JointProgressiveReconciler:
    """Gate new roots until local and remote evidence agree for that root."""

    def __init__(self) -> None:
        self.__published: dict[
            tuple[Optional[str], str], tuple[Optional[SystemFile], Optional[SystemFile]]
        ] = {}

    def reconcile(
        self,
        local_snapshot: dict[tuple[Optional[str], str], SystemFile],
        local_authority: dict[tuple[Optional[str], str], Optional[SystemFile]],
        local_incomplete: set[Optional[str]],
        local_completed: set[Optional[str]],
        remote_snapshot: dict[tuple[Optional[str], str], SystemFile],
        remote_authority: dict[tuple[Optional[str], str], Optional[SystemFile]],
        remote_incomplete: set[Optional[str]],
        remote_completed: set[Optional[str]],
        enabled_pair_ids: set[Optional[str]],
        remote_excluded_keys: Optional[set[tuple[Optional[str], str]]] = None,
    ) -> tuple[list[SystemFile], list[SystemFile], set[Optional[str]]]:
        remote_excluded_keys = remote_excluded_keys or set()
        keys = set(self.__published)
        keys.update(local_snapshot)
        keys.update(remote_snapshot)
        keys.update(local_authority)
        keys.update(remote_authority)
        for pair_id, name in keys:
            if pair_id not in enabled_pair_ids:
                continue
            if (pair_id, name) in remote_excluded_keys:
                local_file = local_authority.get((pair_id, name), local_snapshot.get((pair_id, name)))
                if local_file is None:
                    self.__published.pop((pair_id, name), None)
                else:
                    self.__published[(pair_id, name)] = (local_file, None)
                continue
            local_known = (pair_id, name) in local_authority or pair_id in local_completed
            remote_known = (pair_id, name) in remote_authority or pair_id in remote_completed
            if not local_known or not remote_known:
                continue
            local_file = local_authority.get((pair_id, name))
            remote_file = remote_authority.get((pair_id, name))
            if local_file is None and remote_file is None:
                self.__published.pop((pair_id, name), None)
            else:
                self.__published[(pair_id, name)] = (local_file, remote_file)

        local_files: list[SystemFile] = []
        remote_files: list[SystemFile] = []
        for (pair_id, _), (local_file, remote_file) in self.__published.items():
            if pair_id not in enabled_pair_ids:
                continue
            if local_file is not None:
                local_files.append(local_file)
            if remote_file is not None:
                remote_files.append(remote_file)
        unknown_local_pairs = set(local_incomplete) | set(remote_incomplete)
        unknown_local_pairs.update(enabled_pair_ids - set(local_completed))
        unknown_local_pairs.update(enabled_pair_ids - set(remote_completed))
        return local_files, remote_files, unknown_local_pairs


def _filter_progressive_remote_state(
    snapshot: dict[tuple[Optional[str], str], SystemFile],
    authority: dict[tuple[Optional[str], str], Optional[SystemFile]],
    exclude_patterns: str,
) -> tuple[
    dict[tuple[Optional[str], str], SystemFile],
    dict[tuple[Optional[str], str], Optional[SystemFile]],
    set[tuple[Optional[str], str]],
]:
    """Apply legacy remote exclusions to progressive root evidence."""
    if not exclude_patterns:
        return dict(snapshot), dict(authority), set()

    filtered_snapshot: dict[tuple[Optional[str], str], SystemFile] = {}
    filtered_authority: dict[tuple[Optional[str], str], Optional[SystemFile]] = {}
    excluded_keys: set[tuple[Optional[str], str]] = set()
    keys = set(snapshot) | set(authority)
    for key in keys:
        raw_file = snapshot.get(key)
        authority_present = key in authority
        authority_file = authority.get(key)
        candidate = raw_file if raw_file is not None else authority_file
        filtered_file = None
        if candidate is not None:
            filtered = filter_excluded_files([candidate], exclude_patterns)
            filtered_file = filtered[0] if filtered else None
            if filtered_file is None and raw_file is not None:
                excluded_keys.add(key)
        if raw_file is not None and filtered_file is not None:
            filtered_snapshot[key] = filtered_file
        if authority_present:
            if authority_file is None:
                filtered_authority[key] = None
            elif filtered_file is not None:
                filtered_authority[key] = filtered_file
            elif raw_file is None:
                # A non-null authority entry without a snapshot is still
                # authoritative; retain its exclusion semantics.
                excluded_keys.add(key)
    return filtered_snapshot, filtered_authority, excluded_keys


def _remote_reconciliation_established(
    latest_remote_scan: Optional[ScannerResult],
    scan_final_relevant: bool,
) -> bool:
    """Gate remote lifecycle operations on a result-bearing final tick."""
    return latest_remote_scan is not None and scan_final_relevant


def _lifecycle_scanned_path_pair_ids(
    remote_scan: ScannerResult, enabled_path_pair_ids: set[str],
) -> set[str | None]:
    """Limit lifecycle pruning to roots the arriving scan actually covered."""
    if bool(getattr(remote_scan, "is_progress", False)):
        return set(getattr(remote_scan, "completed_path_pair_ids", set()))
    if bool(getattr(remote_scan, "is_targeted_scan", False)):
        raw_ids = getattr(remote_scan, "scanned_path_pair_ids", set())
        return set(raw_ids) if isinstance(raw_ids, set) else set()
    return set(enabled_path_pair_ids) if enabled_path_pair_ids else {None}


def _pop_scan_updates(controller: "Controller", side: str, process: object) -> Optional[ScannerResult]:
    """Drain progressive events when available; preserve legacy mock behavior."""
    if isinstance(process, ScannerProcess):
        events = process.pop_results()
        state_name = "_Controller__progressive_{}_scan_state".format(side)
        accumulator = getattr(controller, state_name, None)
        if not isinstance(accumulator, _ProgressiveScanAccumulator):
            accumulator = _ProgressiveScanAccumulator()
            setattr(controller, state_name, accumulator)
        accumulator.set_session_token(getattr(process, "session_token", None))
        return accumulator.apply(events)
    pop_latest = getattr(process, "pop_latest_result", None)
    return pop_latest() if callable(pop_latest) else None


def _merge_targeted_legacy_scan_files(
    controller: "Controller", side: str, result: ScannerResult, files: Sequence[SystemFile],
) -> list[SystemFile]:
    """Retain unselected roots when a legacy scanner returns one target pair."""
    state_name = "_Controller__legacy_{}_scan_files".format(side)
    cached = getattr(controller, state_name, None)
    if not isinstance(cached, dict):
        cached = {}
    targeted = bool(getattr(result, "is_targeted_scan", False))
    raw_ids = getattr(result, "scanned_path_pair_ids", set())
    selected_ids = set(raw_ids) if isinstance(raw_ids, set) else set()
    if not targeted:
        cached = {
            (getattr(file, "path_pair_id", None), file.name): file
            for file in files
        }
    elif selected_ids:
        for key in [key for key in cached if key[0] in selected_ids]:
            cached.pop(key, None)
        for file in files:
            if getattr(file, "path_pair_id", None) in selected_ids:
                cached[(file.path_pair_id, file.name)] = file
    setattr(controller, state_name, cached)
    return list(cached.values())


class _ControllerCoreAccess:
    logger: logging.Logger
    MoveFromStagingResult: type["Controller.MoveFromStagingResult"]
    _Controller__context: Context
    _Controller__persist: ControllerPersist
    _Controller__model: Model
    _Controller__model_builder: ModelBuilder
    _Controller__stop_resume_trace_cycle_id: int
    _Controller__path_pairs_by_id: dict[str, PathPair]
    _Controller__active_scan_process: ScannerProcess
    _Controller__local_scan_process: ScannerProcess
    _Controller__remote_scan_process: ScannerProcess
    _Controller__extract_process: ExtractProcess
    _Controller__validate_process: ValidateProcess
    _Controller__lftp: Lftp | RcloneTransferBackend
    _Controller__active_downloading_file_names: list[tuple[str, Optional[str], Optional[str]]]
    _Controller__active_extracting_file_names: list[tuple[str, Optional[str], Optional[str]]]
    _Controller__active_scan_force_file_ids: set[str]
    _Controller__active_scan_ready_file_ids: set[str]
    _Controller__next_active_scan_force_at: Optional[datetime]
    _Controller__prev_downloading_file_names: set[tuple[str, Optional[str], Optional[str]]]
    _Controller__pending_completion_file_names: set[tuple[str, Optional[str], Optional[str]]]
    _Controller__pending_completion_progress_floors: dict[str, tuple[Optional[int], Optional[int]]]
    _Controller__successful_final_move_handoff_file_ids: set[str]
    _Controller__move_retry_due: dict[str, datetime]
    _Controller__move_attempt_lock: Lock
    _Controller__move_attempt_reservations: set[str]
    _Controller__deferred_move_file_ids: set[str]
    _Controller__malformed_status_only_file_ids: set[str]
    _Controller__pending_auto_purge_file_ids: set[str]
    _Controller__last_lftp_statuses: Optional[list[LftpJobStatus]]
    _Controller__next_lftp_status_poll_at: Optional[datetime]
    _Controller__lftp_status_poll_retry_seconds: int
    _Controller__lftp_status_cache_expires_at: Optional[datetime]
    _Controller__lftp_status_cache_max_age_seconds: int
    _Controller__lftp_status_poll_retry_active: bool
    _Controller__startup_recovery_done: bool
    _Controller__exclude_patterns: str
    _Controller__model_lock: RLock
    _Controller__MAX_MOVE_FAILURES: int
    _Controller__MOVE_RETRY_DELAYS: tuple[int, ...]

    def _reconcile_pending_queue_dispatches_from_fresh_status(self, active_file_ids: set[str]) -> None: ...
    def _confirm_fresh_healthy_download_starts(self, statuses: list[LftpJobStatus]) -> None: ...
    def _complete_download_start_lifecycle(self, file_id: str) -> None: ...
    def _record_download_completion(self, file: ModelFile) -> None: ...
    def _mark_successful_final_move_handoff(self, file_id: str) -> None: ...
    def _mark_current_process_final_publication(self, file_id: str) -> None: ...
    def _final_move_succeeded_files_for_model(self) -> set[str]: ...
    def _sync_final_move_succeeded_files_to_model(self) -> None: ...
    def clear_extracted_marker(self, file: ModelFile) -> None: ...
    def _reserve_move_attempt(self, file_id: str) -> bool: ...
    def _release_move_attempt(self, file_id: str) -> None: ...
    def _Controller__active_extracting_file_tuple(
        self, status: ExtractStatus
    ) -> tuple[str, Optional[str], Optional[str]]: ...
    def _Controller__extract_status_matches_failed_result(
        self, status: ExtractStatus, failed_results: list[ExtractFailedResult]
    ) -> bool: ...
    def _Controller__find_target_archive_model_file(
        self, file_name: str, file_id: Optional[str] = None
    ) -> Optional[ModelFile]: ...
    def _Controller__get_path_pair(self, path_pair_id: Optional[str]) -> Optional[PathPair]: ...
    def _Controller__is_explicitly_stopped(
        self, name: str, path_pair_id: Optional[str] = None
    ) -> bool: ...
    def _Controller__is_target_archive_trace_enabled(self) -> bool: ...
    def _Controller__move_from_staging(
        self, name: str, path_pair_id: Optional[str] = None
    ) -> "Controller.MoveFromStagingResult": ...
    def _Controller__queue_delete_local_process(
        self, file: ModelFile, post_callback: Callable[[], None], command: object = None
    ) -> None: ...
    def _Controller__record_breadcrumb(
        self, stage: str, message: str, details: Optional[dict[str, object]] = None,
        event_type: str = "diagnostic", file_id: Optional[str] = None,
        path_pair_id: Optional[str] = None, path_pair_name: Optional[str] = None,
        corr_id: Optional[str] = None, flow_id: Optional[str] = None,
        trace_scope: str = "flow"
    ) -> None: ...
    def _Controller__recover_interrupted_downloads(self, remote_files: list[SystemFile]) -> None: ...
    def _Controller__set_active_scanner_files(
        self, active_files: list[tuple[str, Optional[str], Optional[str]]]
    ) -> None: ...
    def _Controller__should_auto_purge_local_file(self, file: ModelFile) -> bool: ...
    def _Controller__summarize_target_archive_file(self, file: ModelFile) -> dict[str, object]: ...
    def _Controller__target_archive_trace_selector_matches_file(
        self, file_id: str, file_name: str
    ) -> bool: ...
    def _Controller__temp_diag(
        self, stage: str, file_id: Optional[str] = None, **payload: object
    ) -> None: ...
    def _Controller__trace_corr_id_from_files(
        self, files: Optional[Sequence[object]], fallback: str
    ) -> str: ...
    def _Controller__trace_target_archive_event(
        self, event: str, payload: dict[str, object]
    ) -> None: ...


class ModelUpdater(_ControllerCoreAccess):
    """Runs the per-tick model update loop for a controller instance."""

    # LFTP persists pget checkpoints every two seconds. Keep the active
    # scanner responsive to that cadence without changing the user's normal
    # scan interval or waking it on every controller tick.
    _ACTIVE_SCAN_FORCE_INTERVAL = timedelta(seconds=2)

    def __init__(self, controller: object) -> None:
        from .controller import Controller as ControllerType
        if not isinstance(controller, (ControllerType, SimpleNamespace)):
            raise TypeError("ModelUpdater requires the controller core runtime boundary")
        self._controller = cast(_ControllerCoreAccess, controller)

    @staticmethod
    def _preserve_pending_completion_progress_floor(
        old_file: ModelFile,
        new_file: ModelFile,
        pending_completion_file_ids: set[str],
    ) -> None:
        """Keep a pending completion row from publishing a lower checkpoint.

        The LFTP job can disappear before the local scan observes the final
        staging file.  During that handoff the rebuilt model may briefly carry
        zero/unknown progress, which would make listener/SSE consumers regress
        the row even though the transfer has not been reset.  Keep the prior
        model checkpoint for the canonical pending identity while leaving the
        newly-built state and all other flags untouched.
        """
        ModelUpdater._apply_pending_completion_progress_floor(
            new_file,
            pending_completion_file_ids,
            old_file.download_progress,
            old_file.transferred_size,
        )

    @staticmethod
    def _apply_pending_completion_progress_floor(
        new_file: ModelFile,
        pending_completion_file_ids: set[str],
        previous_download_progress: Optional[int],
        previous_transferred_size: Optional[int],
    ) -> None:
        """Apply a stored pending-completion checkpoint to a rebuilt file."""
        if new_file.file_id not in pending_completion_file_ids:
            return

        # A pending completion can be invalidated when a healthy local scan
        # proves that the file reset/disappeared.  Keep that genuine reset
        # visible instead of copying the prior transfer checkpoint into the
        # model that will be retained after the pending identity is cleared.
        if new_file.state == ModelFile.State.DEFAULT and new_file.local_size is None:
            return

        if previous_download_progress is not None and (
            new_file.download_progress is None
            or new_file.download_progress < previous_download_progress
        ):
            new_file.download_progress = previous_download_progress

        if previous_transferred_size is not None:
            transferred_floor = previous_transferred_size
            if new_file.remote_size is not None:
                transferred_floor = min(transferred_floor, new_file.remote_size)
                if (
                    new_file.transferred_size is not None
                    and new_file.transferred_size > new_file.remote_size
                ):
                    new_file.transferred_size = new_file.remote_size
            if (
                new_file.transferred_size is None
                or new_file.transferred_size < transferred_floor
            ):
                new_file.transferred_size = transferred_floor

    @staticmethod
    def _get_exclude_patterns(controller: _ControllerCoreAccess) -> str:
        exclude_patterns = getattr(controller, "_Controller__exclude_patterns", None)
        if isinstance(exclude_patterns, str):
            return exclude_patterns
        config = getattr(getattr(controller, "_Controller__context", None), "config", None)
        general = getattr(config, "general", None)
        exclude_patterns = getattr(general, "exclude_patterns", "")
        return exclude_patterns if isinstance(exclude_patterns, str) else ""

    def sync_persist_to_all_builders(self):
        controller = self._controller
        persist = controller._Controller__persist
        move_failure_counts = getattr(persist, "move_failure_counts", {})
        max_move_failures = getattr(controller, "_Controller__MAX_MOVE_FAILURES", 4)
        path_pair_ids = set(getattr(controller, "_Controller__path_pairs_by_id", {}).keys())
        controller._Controller__model_builder.set_downloaded_files(
            self._filter_keys_for_model_builder(controller._Controller__persist.downloaded_file_names, path_pair_ids)
        )
        downloaded_timestamps = getattr(persist, "downloaded_timestamps", {})
        controller._Controller__model_builder.set_downloaded_timestamps({
            file_id: timestamp
            for file_id, timestamp in downloaded_timestamps.items()
            if self._normalize_scoped_persist_key(file_id, path_pair_ids) == file_id
        })
        controller._Controller__model_builder.set_extracted_files(
            self._filter_keys_for_model_builder(
                controller._Controller__persist.extracted_file_names,
                path_pair_ids,
            )
        )
        controller._Controller__model_builder.set_stopped_files(
            self._filter_keys_for_model_builder(
                controller._Controller__persist.stopped_file_names,
                path_pair_ids,
            )
        )
        if hasattr(persist, "move_failure_counts"):
            canonical_move_failure_ids = self._filter_keys_for_model_builder(
                set(move_failure_counts), path_pair_ids
            )
            controller._Controller__model_builder.set_move_failed_files(
                {
                    file_id for file_id, count in move_failure_counts.items()
                    if file_id in canonical_move_failure_ids and count >= max_move_failures
                }
            )
        if hasattr(persist, "final_move_succeeded_file_names"):
            controller._Controller__model_builder.set_final_move_succeeded_files(
                self._filter_keys_for_model_builder(
                    controller._final_move_succeeded_files_for_model(),
                    path_pair_ids,
                )
            )

    @staticmethod
    def _filter_keys_for_model_builder(keys: set[str], path_pair_ids: set[str]) -> set[str]:
        return {
            key for key in keys
            if ModelUpdater._normalize_scoped_persist_key(key, path_pair_ids) == key
        }

    @staticmethod
    def _canonical_scoped_persist_key(key: str) -> str | None:
        if not isinstance(key, str):
            return None
        try:
            parsed = json.loads(key)
        except (TypeError, ValueError):
            parsed = None
        if (
            isinstance(parsed, list)
            and len(parsed) == 2
            and isinstance(parsed[0], str)
            and isinstance(parsed[1], str)
            and key == ModelFile.build_file_id(parsed[1], parsed[0])
        ):
            return key

        return None

    @classmethod
    def _normalize_scoped_persist_key(cls, key: str, path_pair_ids: set[str]) -> str | None:
        """Accept canonical active model identities at runtime, and nothing else."""
        canonical = cls._canonical_scoped_persist_key(key)
        if canonical is not None:
            parsed_pair_id = json.loads(canonical)[0]
            return canonical if parsed_pair_id in path_pair_ids else None
        if not path_pair_ids and key == ModelFile.build_file_id(key, None):
            return key
        return None

    @classmethod
    def _safe_stale_marker_ids(
        cls,
        markers: set[str],
        active_model_ids: set[str],
        active_model_names: set[str],
        pending_ids: set[str],
        path_pair_ids: set[str],
    ) -> set[str]:
        stale: set[str] = set()
        for marker in markers:
            normalized_marker = cls._normalize_scoped_persist_key(marker, path_pair_ids)
            if normalized_marker is not None:
                if normalized_marker not in active_model_ids and normalized_marker not in pending_ids:
                    stale.add(marker)
                continue
            canonical_marker = cls._canonical_scoped_persist_key(marker)
            if canonical_marker is not None:
                path_pair_id = json.loads(canonical_marker)[0]
                # Disabled/removed pairs are retained as canonical history so
                # re-enabling the same pair restores only its own state.
                if path_pair_id not in path_pair_ids:
                    continue
                if canonical_marker not in active_model_ids and canonical_marker not in pending_ids:
                    stale.add(marker)
                continue
            # Legacy KEY_SEP/UUID-colon/bare forms cannot be matched after the
            # persistence boundary, even when their basename appears live.
            stale.add(marker)
        return stale

    def _handle_lftp_completion_detection(
        self,
        current_downloading_file_names: list[tuple[str, str | None, str | None]],
        should_process_completion_detection: bool,
    ) -> None:
        if not should_process_completion_detection:
            return

        controller = self._controller
        current_downloading_file_names_set = set(current_downloading_file_names)
        just_completed_file_names = (
            controller._Controller__prev_downloading_file_names - current_downloading_file_names_set
        )
        just_completed_file_names = {
            file_name for file_name in just_completed_file_names
            if not controller._Controller__is_explicitly_stopped(file_name[0], file_name[1])
        }
        if just_completed_file_names:
            for name, path_pair_id, _ in just_completed_file_names:
                controller.logger.info(
                    "Download completion pending (LFTP job finished): {}".format(
                        ModelFile.build_file_id(name, path_pair_id)
                    )
                )
            controller._Controller__pending_completion_file_names.update(just_completed_file_names)
            controller._Controller__local_scan_process.force_scan()
        controller._Controller__prev_downloading_file_names = current_downloading_file_names_set

    def _force_active_scan_for_stoppability(
        self,
        active_file_names: list[tuple[str, Optional[str], Optional[str]]],
        latest_active_scan: object = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Wake the active scanner while a running transfer lacks its stop sidecar.

        The configured active-scan interval can be deliberately large. A
        newly running regular file still needs a bounded opportunity to expose
        its pget checkpoint, so retry at the same cadence as lftp's
        ``pget:save-status`` snapshots. Once the scan reports readiness (or the
        transfer is no longer active), the retry state is discarded.
        """
        controller = self._controller
        transfer_backend = getattr(controller, "_Controller__lftp", None)
        if getattr(transfer_backend, "backend_name", "lftp") == "rclone":
            # Rclone has no lftp pget status sidecar; checkpoint wakeups are
            # inapplicable and would otherwise become an endless retry loop.
            controller._Controller__active_scan_force_file_ids = set()
            controller._Controller__active_scan_ready_file_ids = set()
            controller._Controller__next_active_scan_force_at = None
            return
        active_ids = {
            ModelFile.build_file_id(name, path_pair_id)
            for name, path_pair_id, _ in active_file_names
        }
        pending_ids = getattr(controller, "_Controller__active_scan_force_file_ids", None)
        if not isinstance(pending_ids, set):
            pending_ids = set()
            controller._Controller__active_scan_force_file_ids = pending_ids

        ready_ids = getattr(controller, "_Controller__active_scan_ready_file_ids", None)
        if not isinstance(ready_ids, set):
            ready_ids = set()
            controller._Controller__active_scan_ready_file_ids = ready_ids
        ready_ids.intersection_update(active_ids)

        active_scan_failed = latest_active_scan is not None and bool(
            getattr(latest_active_scan, "failed", False)
        )
        healthy_observed_ids: set[str] = set()
        if active_scan_failed:
            # Recoverable/partial scans are not authoritative evidence for a
            # checkpoint. Invalidate current active readiness and retry after
            # the bounded cadence instead of trusting stale sidecar state.
            ready_ids.difference_update(active_ids)
        elif latest_active_scan is not None:
            observed_ids = {
                ModelFile.build_file_id(
                    scanned_file.name,
                    getattr(scanned_file, "path_pair_id", None),
                )
                for scanned_file in getattr(latest_active_scan, "files", []) or []
            } & active_ids
            healthy_observed_ids = observed_ids
            # A missing active scan result means the sidecar/path is not
            # currently observable; do not retain a stale stop-ready gate.
            ready_ids.intersection_update(observed_ids)
            for scanned_file in getattr(latest_active_scan, "files", []) or []:
                file_id = ModelFile.build_file_id(
                    scanned_file.name,
                    getattr(scanned_file, "path_pair_id", None),
                )
                if file_id not in active_ids:
                    continue
                if getattr(scanned_file, "is_dir", False) or getattr(
                    scanned_file, "status_sidecar_ready", False
                ):
                    ready_ids.add(file_id)
                else:
                    ready_ids.discard(file_id)

        # Directory transfers are stoppable without a pget sidecar. Apply
        # this invariant after scan-result invalidation so failed scans do not
        # turn directories into pointless checkpoint retries.
        current_model = getattr(controller, "_Controller__model", None)
        if current_model is not None:
            for file_id in active_ids:
                if file_id in healthy_observed_ids:
                    continue
                try:
                    model_file = current_model.get_file(file_id)
                except (AttributeError, ModelError):
                    continue
                if isinstance(model_file, ModelFile) and model_file.is_dir:
                    ready_ids.add(file_id)

        pending_ids.clear()
        pending_ids.update(active_ids - ready_ids)
        if not pending_ids:
            controller._Controller__next_active_scan_force_at = None
            return

        current_time = now if now is not None else datetime.now()
        next_force_at = getattr(controller, "_Controller__next_active_scan_force_at", None)
        if next_force_at is not None and current_time < next_force_at:
            return

        controller._Controller__active_scan_process.force_scan()
        controller._Controller__next_active_scan_force_at = (
            current_time + self._ACTIVE_SCAN_FORCE_INTERVAL
        )

    def update(self) -> None:
        """Run one model refresh bracketed by the optional trace cycle."""
        controller = self._controller
        cycle_id = getattr(controller, "_Controller__stop_resume_trace_cycle_id", 0) + 1
        controller._Controller__stop_resume_trace_cycle_id = cycle_id
        model_builder = controller._Controller__model_builder
        model_builder.begin_stop_resume_trace_cycle(cycle_id)
        build_triggered = False
        work_state_lock = getattr(controller, "_Controller__work_state_lock", None)
        try:
            # Relocation snapshots treat these runtime collections as one
            # coherent unit.  Hold the same outer lock across this update so
            # an alias switch cannot observe a halfway scan/status transition.
            if work_state_lock is None:
                build_triggered = self._update_once()
            else:
                with work_state_lock:
                    build_triggered = self._update_once()
        finally:
            # Keep the no-rebuild case observable and finish only after all
            # model listeners have seen the applied diff.
            try:
                model_builder.finish_stop_resume_trace_cycle(
                    controller._Controller__model,
                    build_triggered,
                )
            except Exception:
                controller.logger.debug("Ignoring stop/resume trace finalization failure", exc_info=True)

    def _update_once(self) -> bool:
        controller = self._controller
        model_builder = controller._Controller__model_builder
        persist = controller._Controller__persist
        model = controller._Controller__model
        if not isinstance(getattr(persist, "move_failure_counts", None), dict):
            persist.move_failure_counts = {}
        if not isinstance(getattr(persist, "final_move_succeeded_file_names", None), set):
            persist.final_move_succeeded_file_names = set()
        if not isinstance(getattr(persist, "downloaded_timestamps", None), dict):
            persist.downloaded_timestamps = {}

        if not hasattr(controller, "_Controller__malformed_status_only_file_ids"):
            controller._Controller__malformed_status_only_file_ids = set()
        if not hasattr(controller, "_Controller__pending_auto_purge_file_ids"):
            controller._Controller__pending_auto_purge_file_ids = set()
        if not hasattr(controller, "_Controller__last_lftp_statuses"):
            controller._Controller__last_lftp_statuses = []
        if not hasattr(controller, "_Controller__next_lftp_status_poll_at"):
            controller._Controller__next_lftp_status_poll_at = None
        if not hasattr(controller, "_Controller__lftp_status_poll_retry_seconds"):
            controller._Controller__lftp_status_poll_retry_seconds = 1
        if not hasattr(controller, "_Controller__lftp_status_cache_expires_at"):
            controller._Controller__lftp_status_cache_expires_at = None
        if not hasattr(controller, "_Controller__lftp_status_cache_max_age_seconds"):
            controller._Controller__lftp_status_cache_max_age_seconds = max(
                3,
                controller._Controller__lftp_status_poll_retry_seconds * 3,
            )
        if not hasattr(controller, "_Controller__lftp_status_poll_retry_active"):
            controller._Controller__lftp_status_poll_retry_active = False
        if not hasattr(controller, "_Controller__prev_downloading_file_names"):
            controller._Controller__prev_downloading_file_names = set()
        if not hasattr(controller, "_Controller__pending_completion_file_names"):
            controller._Controller__pending_completion_file_names = set()
        if not hasattr(controller, "_Controller__pending_completion_progress_floors"):
            controller._Controller__pending_completion_progress_floors = {}
        if not hasattr(controller, "_Controller__successful_final_move_handoff_file_ids"):
            controller._Controller__successful_final_move_handoff_file_ids = set()
        if not hasattr(controller, "_Controller__current_process_final_publication_file_ids"):
            controller._Controller__current_process_final_publication_file_ids = set()
        controller._Controller__successful_final_move_handoff_file_ids.intersection_update(
            persist.final_move_succeeded_file_names
        )
        pending_completion_ids = {
            ModelFile.build_file_id(file_name, path_pair_id)
            for file_name, path_pair_id, _ in controller._Controller__pending_completion_file_names
        }
        controller._Controller__pending_completion_progress_floors = {
            file_id: floor
            for file_id, floor in controller._Controller__pending_completion_progress_floors.items()
            if file_id in pending_completion_ids
        }
        if not hasattr(controller, "_Controller__active_scan_force_file_ids"):
            controller._Controller__active_scan_force_file_ids = set()
        if not hasattr(controller, "_Controller__active_scan_ready_file_ids"):
            controller._Controller__active_scan_ready_file_ids = set()
        if not hasattr(controller, "_Controller__next_active_scan_force_at"):
            controller._Controller__next_active_scan_force_at = None
        if not hasattr(controller, "_Controller__move_retry_due"):
            controller._Controller__move_retry_due = {}
        if not hasattr(controller, "_Controller__deferred_move_file_ids"):
            controller._Controller__deferred_move_file_ids = set()
        if not hasattr(controller, "_Controller__move_attempt_reservations"):
            controller._Controller__move_attempt_reservations = set()
        if not hasattr(controller, "_Controller__move_attempt_lock"):
            controller._Controller__move_attempt_lock = Lock()
        if not hasattr(controller, "_Controller__last_remote_reconciliation_healthy"):
            controller._Controller__last_remote_reconciliation_healthy = False
        if not hasattr(controller, "_Controller__last_local_reconciliation_healthy"):
            controller._Controller__last_local_reconciliation_healthy = False

        # Grab the latest scan results.
        latest_remote_scan = _pop_scan_updates(controller, "remote", controller._Controller__remote_scan_process)
        latest_local_scan = _pop_scan_updates(controller, "local", controller._Controller__local_scan_process)
        latest_active_scan = controller._Controller__active_scan_process.pop_latest_result()
        progressive_mode = bool(getattr(controller, "_Controller__progressive_joint_mode", False)) or \
            bool(getattr(latest_remote_scan, "is_progress", False)) or \
            bool(getattr(latest_local_scan, "is_progress", False))
        if progressive_mode:
            controller._Controller__progressive_joint_mode = True
        joint_reconciler = getattr(controller, "_Controller__progressive_joint_reconciler", None)
        if progressive_mode and not isinstance(joint_reconciler, _JointProgressiveReconciler):
            joint_reconciler = _JointProgressiveReconciler()
            controller._Controller__progressive_joint_reconciler = joint_reconciler

        def side_state(side: str, result: Optional[ScannerResult]):
            accumulator = getattr(controller, "_Controller__progressive_{}_scan_state".format(side), None)
            if isinstance(accumulator, _ProgressiveScanAccumulator):
                return accumulator.snapshot(), accumulator.authority(), accumulator.incomplete_pairs(), accumulator.completed_pairs()
            if result is None or bool(getattr(result, "failed", False)):
                return {}, {}, set(getattr(result, "scanned_path_pair_ids", {None})) if result is not None else set(), set()
            snapshot = {(file.path_pair_id, file.name): file for file in result.files}
            ids = set(getattr(result, "scanned_path_pair_ids", {None}))
            return snapshot, dict(snapshot), set(getattr(result, "unknown_path_pair_ids", set())), ids

        joint_local_files: list[SystemFile] = []
        joint_remote_files: list[SystemFile] = []
        joint_unknown_local_ids: set[Optional[str]] = set()
        joint_remote_excluded_keys: set[tuple[Optional[str], str]] = set()
        if progressive_mode and joint_reconciler is not None:
            local_snapshot, local_authority, local_incomplete, local_completed = side_state("local", latest_local_scan)
            remote_snapshot, remote_authority, remote_incomplete, remote_completed = side_state("remote", latest_remote_scan)
            remote_snapshot, remote_authority, joint_remote_excluded_keys = _filter_progressive_remote_state(
                remote_snapshot,
                remote_authority,
                self._get_exclude_patterns(controller),
            )
            enabled_pair_ids = set(getattr(controller, "_Controller__path_pairs_by_id", {}).keys())
            if not enabled_pair_ids:
                enabled_pair_ids = {None}
            joint_local_files, joint_remote_files, joint_unknown_local_ids = joint_reconciler.reconcile(
                local_snapshot, local_authority, local_incomplete,
                local_completed,
                remote_snapshot, remote_authority, remote_incomplete,
                remote_completed,
                enabled_pair_ids,
                joint_remote_excluded_keys,
            )

        def scan_final_relevant(side: str, result: Optional[ScannerResult]) -> bool:
            """Return whether this side has a complete, authoritative view."""
            if result is not None:
                return bool(getattr(result, "is_scan_final", True)) \
                    and not bool(getattr(result, "failed", False)) \
                    and not bool(getattr(result, "unknown_path_pair_ids", set()))
            if not progressive_mode:
                return False
            accumulator = getattr(controller, "_Controller__progressive_{}_scan_state".format(side), None)
            if not isinstance(accumulator, _ProgressiveScanAccumulator):
                return False
            return not accumulator.incomplete_pairs() and bool(accumulator.completed_pairs())

        remote_scan_final_relevant = scan_final_relevant("remote", latest_remote_scan)
        local_scan_final_relevant = scan_final_relevant("local", latest_local_scan)
        joint_reconciliation_final = remote_scan_final_relevant and local_scan_final_relevant \
            and not joint_unknown_local_ids

        # Grab the Lftp status.
        lftp_statuses: Optional[list[LftpJobStatus]] = []
        lftp_status_poll_healthy = True
        lftp_status_snapshot_fresh = True
        lftp_status_source = "fresh_healthy"
        now = datetime.now()
        current_lftp_status_poll_healthy = getattr(controller._Controller__lftp, "last_status_poll_healthy", True)
        lftp_status_poll_due = (
            controller._Controller__next_lftp_status_poll_at is None
            or now >= controller._Controller__next_lftp_status_poll_at
            or (
                controller._Controller__last_lftp_statuses
                and not current_lftp_status_poll_healthy
                and not controller._Controller__lftp_status_poll_retry_active
            )
        )
        if not lftp_status_poll_due:
            if controller._Controller__last_lftp_statuses:
                lftp_statuses = controller._Controller__last_lftp_statuses
                lftp_status_snapshot_fresh = False
                lftp_status_source = "cached_retry"
            else:
                lftp_status_poll_healthy = False
                lftp_status_source = "retry_empty"
        else:
            try:
                lftp_statuses = controller._Controller__lftp.status()
                lftp_status_poll_healthy = getattr(controller._Controller__lftp, "last_status_poll_healthy", True)
                poll_finished_at = datetime.now()
                if lftp_status_poll_healthy:
                    controller._Controller__lftp_status_poll_retry_active = False
                    controller._Controller__last_lftp_statuses = lftp_statuses
                    controller._Controller__lftp_status_cache_expires_at = poll_finished_at + timedelta(
                        seconds=controller._Controller__lftp_status_cache_max_age_seconds
                    )
                    # Keep healthy polls responsive without hammering lftp on every controller tick.
                    controller._Controller__next_lftp_status_poll_at = poll_finished_at + timedelta(milliseconds=200)
                    lftp_status_source = "fresh_healthy"
                else:
                    controller._Controller__lftp_status_poll_retry_active = True
                    controller._Controller__next_lftp_status_poll_at = poll_finished_at + timedelta(
                        seconds=controller._Controller__lftp_status_poll_retry_seconds
                    )
                    if controller._Controller__last_lftp_statuses:
                        lftp_statuses = controller._Controller__last_lftp_statuses
                        lftp_status_snapshot_fresh = False
                        lftp_status_source = "cached_unhealthy"
                    elif lftp_statuses:
                        controller._Controller__last_lftp_statuses = lftp_statuses
                        controller._Controller__lftp_status_cache_expires_at = poll_finished_at + timedelta(
                            seconds=controller._Controller__lftp_status_cache_max_age_seconds
                        )
                        lftp_status_source = "fresh_unhealthy"
                    else:
                        lftp_status_source = "unhealthy_empty"
            except (LftpError, LftpJobStatusParserError) as e:
                controller.logger.warning("Caught transfer backend error: {}".format(str(e)))
                lftp_statuses = []
                lftp_status_poll_healthy = False
                controller._Controller__lftp_status_poll_retry_active = True
                poll_finished_at = datetime.now()
                controller._Controller__next_lftp_status_poll_at = poll_finished_at + timedelta(
                    seconds=controller._Controller__lftp_status_poll_retry_seconds
                )
                if controller._Controller__last_lftp_statuses:
                    lftp_statuses = controller._Controller__last_lftp_statuses
                    lftp_status_snapshot_fresh = False
                    lftp_status_source = "cached_error"
                else:
                    lftp_status_source = "error_empty"

        # Grab the latest extract results.
        latest_extract_statuses = controller._Controller__extract_process.pop_latest_statuses()
        latest_validation_statuses = controller._Controller__validate_process.pop_latest_statuses()

        # Grab the latest extracted file names.
        latest_extracted_results = controller._Controller__extract_process.pop_completed()
        latest_failed_results = controller._Controller__extract_process.pop_failed()
        previous_malformed_status_only_file_ids = set(controller._Controller__malformed_status_only_file_ids)
        if latest_active_scan is not None:
            controller._Controller__malformed_status_only_file_ids.update(latest_active_scan.malformed_status_only_file_ids)

        # Update list of active file names.
        if lftp_statuses is not None:
            active_status_file_ids = {status.file_id for status in lftp_statuses}
            controller._Controller__malformed_status_only_file_ids.intersection_update(active_status_file_ids)
            lftp_statuses = [
                status for status in lftp_statuses
                if status.file_id not in controller._Controller__malformed_status_only_file_ids
            ]
            if lftp_status_snapshot_fresh and lftp_status_poll_healthy:
                reconcile_pending_queues = getattr(
                    controller, "_reconcile_pending_queue_dispatches_from_fresh_status", None
                )
                if callable(reconcile_pending_queues):
                    reconcile_pending_queues({status.file_id for status in lftp_statuses})
                confirm_download_starts = getattr(controller, "_confirm_fresh_healthy_download_starts", None)
                if callable(confirm_download_starts):
                    confirm_download_starts(lftp_statuses)
            current_downloading_file_names = [
                (s.name, s.path_pair_id, s.path_pair_name)
                for s in lftp_statuses if s.state == LftpJobStatus.State.RUNNING
            ]
            self._handle_lftp_completion_detection(
                current_downloading_file_names,
                lftp_status_poll_healthy or bool(lftp_statuses),
            )
            controller._Controller__active_downloading_file_names = current_downloading_file_names
        if controller._Controller__malformed_status_only_file_ids != previous_malformed_status_only_file_ids:
            controller._Controller__next_lftp_status_poll_at = None
        if latest_extract_statuses is not None:
            controller._Controller__active_extracting_file_names = [
                controller._Controller__active_extracting_file_tuple(s)
                for s in latest_extract_statuses.statuses
                if s.state == ExtractStatus.State.EXTRACTING
                and not controller._Controller__extract_status_matches_failed_result(s, latest_failed_results)
            ]
        controller._Controller__temp_diag(
            "update_model",
            lftp_status_source=lftp_status_source,
            lftp_status_poll_healthy=lftp_status_poll_healthy,
            lftp_status_snapshot_fresh=lftp_status_snapshot_fresh,
            lftp_status_count=len(lftp_statuses) if lftp_statuses is not None else None,
            active_downloading_count=len(controller._Controller__active_downloading_file_names),
            active_extracting_count=len(controller._Controller__active_extracting_file_names),
            last_lftp_status_count=(
                len(controller._Controller__last_lftp_statuses)
                if controller._Controller__last_lftp_statuses is not None
                else None
            ),
            next_lftp_status_poll_at=controller._Controller__next_lftp_status_poll_at,
            lftp_status_cache_expires_at=controller._Controller__lftp_status_cache_expires_at,
        )

        # Update the active scanner's state.
        controller._Controller__set_active_scanner_files(
            controller._Controller__active_downloading_file_names
            + controller._Controller__active_extracting_file_names
            + list(controller._Controller__pending_completion_file_names)
        )
        self._force_active_scan_for_stoppability(
            controller._Controller__active_downloading_file_names,
            latest_active_scan,
        )

        model_builder.set_stop_resume_trace_cycle_context({
            "lftp_status_source": lftp_status_source,
            "lftp_status_healthy": lftp_status_poll_healthy,
            "lftp_status_fresh": lftp_status_snapshot_fresh,
            "lftp_status_count": len(lftp_statuses) if lftp_statuses is not None else None,
            "active_scan_arrived": latest_active_scan is not None,
            "local_scan_arrived": latest_local_scan is not None,
            "remote_scan_arrived": latest_remote_scan is not None,
        })

        # Update model builder state.
        remote_files: list[SystemFile] = []
        if latest_remote_scan is not None:
            remote_scan_failed = bool(getattr(latest_remote_scan, "failed", False))
            if progressive_mode:
                remote_files = joint_remote_files
            else:
                remote_files = _merge_targeted_legacy_scan_files(
                    controller,
                    "remote",
                    latest_remote_scan,
                    filter_excluded_files(latest_remote_scan.files, self._get_exclude_patterns(controller)),
                )
            remote_final = bool(getattr(latest_remote_scan, "is_scan_final", True)) and \
                not bool(getattr(latest_remote_scan, "unknown_path_pair_ids", set()))
            if remote_final and not progressive_mode:
                controller._Controller__last_remote_reconciliation_healthy = not remote_scan_failed
            if not remote_scan_failed and not progressive_mode:
                model_builder.set_remote_files(remote_files)
            controller._Controller__record_breadcrumb(
                stage="scan",
                message="remote_scan_result",
                details={
                    "file_count": len(remote_files),
                    "failed": remote_scan_failed,
                    "error_message": latest_remote_scan.error_message,
                },
                event_type="failure" if remote_scan_failed else "state_transition",
                corr_id=controller._Controller__trace_corr_id_from_files(remote_files, "remote_scan"),
            )
        if latest_local_scan is not None:
            # A failed local scan may contain a partial/empty result. Keep the
            # last authoritative local snapshot and its history until a
            # healthy scan proves absence.
            local_scan_failed = bool(getattr(latest_local_scan, "failed", False))
            local_final = bool(getattr(latest_local_scan, "is_scan_final", True)) and \
                not bool(getattr(latest_local_scan, "unknown_path_pair_ids", set()))
            if local_final and not progressive_mode:
                controller._Controller__last_local_reconciliation_healthy = not local_scan_failed
            recovered_extracted_file_ids = []
            if not local_scan_failed and local_final:
                if not progressive_mode:
                    model_builder.set_local_files(_merge_targeted_legacy_scan_files(
                        controller, "local", latest_local_scan, latest_local_scan.files,
                    ))
                raw_recovered_ids = getattr(latest_local_scan, "managed_extract_file_ids", [])
                if isinstance(raw_recovered_ids, (list, tuple, set)):
                    recovered_items = cast(list[object] | tuple[object, ...] | set[object], raw_recovered_ids)
                    recovered_extracted_file_ids = [
                        file_id for file_id in recovered_items
                        if isinstance(file_id, str)
                        and self._normalize_scoped_persist_key(
                            file_id,
                            set(getattr(controller, "_Controller__path_pairs_by_id", {}).keys()),
                        ) == file_id
                    ]
                persist.extracted_file_names.update(recovered_extracted_file_ids)
            controller._Controller__record_breadcrumb(
                stage="scan",
                message="local_scan_result",
                details={
                    "file_count": len(latest_local_scan.files),
                    "managed_extract_file_count": len(recovered_extracted_file_ids),
                },
                event_type="state_transition",
                corr_id=controller._Controller__trace_corr_id_from_files(latest_local_scan.files, "local_scan"),
            )
            unknown_local_ids = joint_unknown_local_ids if progressive_mode else set(
                getattr(latest_local_scan, "unknown_path_pair_ids", set())
            )
            if local_scan_failed and not unknown_local_ids:
                unknown_local_ids = set(getattr(latest_local_scan, "scanned_path_pair_ids", {None}))
            setter_unknown_local = getattr(model_builder, "set_unknown_local_path_pair_ids", None)
            if callable(setter_unknown_local):
                setter_unknown_local(unknown_local_ids)
        if progressive_mode and joint_reconciler is not None:
            model_builder.set_local_files(joint_local_files)
            model_builder.set_remote_files(joint_remote_files)
            setter_unknown_local = getattr(model_builder, "set_unknown_local_path_pair_ids", None)
            if callable(setter_unknown_local):
                setter_unknown_local(joint_unknown_local_ids)
            if joint_reconciliation_final:
                controller._Controller__last_local_reconciliation_healthy = True
                controller._Controller__last_remote_reconciliation_healthy = True
        healthy_local_ids: set[str | None] = set()
        healthy_remote_ids: set[str | None] = set()
        if latest_local_scan is not None and not bool(getattr(latest_local_scan, "failed", False)) and \
                bool(getattr(latest_local_scan, "is_scan_final", True)):
            raw_ids = getattr(latest_local_scan, "scanned_path_pair_ids", {None})
            if isinstance(raw_ids, set):
                healthy_local_ids = {item for item in raw_ids if item is None or isinstance(item, str)}
        if latest_remote_scan is not None and not bool(getattr(latest_remote_scan, "failed", False)) and \
                bool(getattr(latest_remote_scan, "is_scan_final", True)):
            raw_ids = getattr(latest_remote_scan, "scanned_path_pair_ids", {None})
            if isinstance(raw_ids, set):
                healthy_remote_ids = {item for item in raw_ids if item is None or isinstance(item, str)}
        if healthy_local_ids or healthy_remote_ids:
            recorder = getattr(controller, "_record_path_pair_reconciliation", None)
            if callable(recorder):
                recorder(healthy_local_ids, healthy_remote_ids)
        if latest_active_scan is not None:
            active_scan_files = list(latest_active_scan.files)
            handoff_file_ids = controller._Controller__successful_final_move_handoff_file_ids
            if handoff_file_ids:
                active_scan_root_ids = {
                    ModelFile.build_file_id(
                        active_file.name,
                        getattr(active_file, "path_pair_id", None),
                    )
                    for active_file in active_scan_files
                }
                if not bool(getattr(latest_active_scan, "failed", False)):
                    handoff_file_ids.intersection_update(active_scan_root_ids)
                active_scan_files = [
                    active_file
                    for active_file in active_scan_files
                    if ModelFile.build_file_id(
                        active_file.name,
                        getattr(active_file, "path_pair_id", None),
                    ) not in handoff_file_ids
                ]
            model_builder.set_active_files(active_scan_files)
            controller._Controller__record_breadcrumb(
                stage="scan",
                message="active_scan_result",
                details={
                    "file_count": len(latest_active_scan.files),
                    "malformed_status_only_file_count": len(latest_active_scan.malformed_status_only_file_ids),
                },
                event_type="state_transition",
                corr_id=controller._Controller__trace_corr_id_from_files(latest_active_scan.files, "active_scan"),
            )
        if lftp_statuses is not None:
            model_builder.set_lftp_statuses(lftp_statuses)
            if lftp_status_snapshot_fresh and not lftp_status_poll_healthy and not lftp_statuses:
                model_builder.evict_recent_live_transfer_snapshots_missing_roots(
                    {status.file_id for status in lftp_statuses}
                )
        if latest_extract_statuses is not None:
            model_builder.set_extract_statuses(latest_extract_statuses.statuses)
            controller._Controller__record_breadcrumb(
                stage="extract",
                message="extract_status_result",
                details={
                    "status_count": len(latest_extract_statuses.statuses),
                    "extracting_count": len([
                        s for s in latest_extract_statuses.statuses if s.state == ExtractStatus.State.EXTRACTING
                    ]),
                },
                event_type="state_transition",
                corr_id="extract:aggregate",
                trace_scope="aggregate",
            )
            if controller._Controller__is_target_archive_trace_enabled():
                for status in latest_extract_statuses.statuses:
                    trace_target_file = controller._Controller__find_target_archive_model_file(status.name)
                    if trace_target_file is not None:
                        controller._Controller__trace_target_archive_event("extract_status", {
                            "file": controller._Controller__summarize_target_archive_file(trace_target_file),
                            "is_dir": status.is_dir,
                            "state": getattr(status.state, "name", status.state),
                        })
        if latest_validation_statuses is not None:
            model_builder.set_validation_statuses(latest_validation_statuses.statuses)
            terminal_validation_ids = {
                status.file_id for status in latest_validation_statuses.statuses
                if status.state in (ModelFile.State.VALIDATED, ModelFile.State.CORRUPT)
            }
            recorder = getattr(controller, "_record_worker_terminal_ids", None)
            if callable(recorder) and terminal_validation_ids:
                recorder(set(), terminal_validation_ids)
        def _is_known_extract_result_pair(
            result: ExtractCompletedResult | ExtractFailedResult, result_kind: str
        ) -> bool:
            path_pair_id = getattr(result, "path_pair_id", None)
            if path_pair_id is None:
                return True
            if controller._Controller__get_path_pair(path_pair_id) is not None:
                return True
            controller.logger.warning(
                "Ignoring extract %s for '%s': pair '%s' no longer exists",
                result_kind,
                result.name,
                path_pair_id,
            )
            return False

        if latest_extracted_results:
            known_extracted_results: list[ExtractCompletedResult] = []
            extracted_result_summaries: list[dict[str, object]] = []
            for result in latest_extracted_results:
                if not _is_known_extract_result_pair(result, "completion"):
                    continue
                known_extracted_results.append(result)
                extracted_file_id = result.file_id
                if (
                    extracted_file_id is None
                    or ControllerPersist._canonical_file_id(extracted_file_id, None) != extracted_file_id
                ):
                    extracted_file_id = ModelFile.build_file_id(result.name, result.path_pair_id)
                persist.extracted_file_names.add(extracted_file_id)
                extracted_result_summaries.append({
                    "name": result.name,
                    "file_id": result.file_id,
                    "is_dir": result.is_dir,
                    "path_pair_id": result.path_pair_id,
                })
                trace_target_file = controller._Controller__find_target_archive_model_file(result.name, result.file_id)
                if trace_target_file is not None:
                    controller._Controller__trace_target_archive_event("extracted_marker_added", {
                        "file": controller._Controller__summarize_target_archive_file(trace_target_file),
                        "is_dir": result.is_dir,
                    })
            model_builder.set_extracted_files(persist.extracted_file_names)
            if known_extracted_results:
                controller._Controller__record_breadcrumb(
                    stage="extract",
                    message="extract_completed",
                    details={
                        "result_count": len(known_extracted_results),
                        "results": extracted_result_summaries,
                    },
                    event_type="state_transition",
                    corr_id=controller._Controller__trace_corr_id_from_files(known_extracted_results, "extract"),
                )
        if latest_failed_results:
            known_failed_results: list[ExtractFailedResult] = []
            failed_result_summaries: list[dict[str, object]] = []
            for result in latest_failed_results:
                if not _is_known_extract_result_pair(result, "failure"):
                    continue
                known_failed_results.append(result)
                failed_result_summaries.append({
                    "name": result.name,
                    "file_id": result.file_id,
                    "is_dir": result.is_dir,
                    "path_pair_id": result.path_pair_id,
                })
            if known_failed_results:
                controller._Controller__record_breadcrumb(
                    stage="extract",
                    message="extract_failed",
                    details={
                        "result_count": len(known_failed_results),
                        "results": failed_result_summaries,
                    },
                    event_type="failure",
                    corr_id=controller._Controller__trace_corr_id_from_files(known_failed_results, "extract"),
                )
        terminal_extract_ids = {
            result.file_id if isinstance(getattr(result, "file_id", None), str)
            else ModelFile.build_file_id(result.name, result.path_pair_id)
            for result in latest_extracted_results + latest_failed_results
        }
        recorder = getattr(controller, "_record_worker_terminal_ids", None)
        if callable(recorder) and terminal_extract_ids:
            recorder(terminal_extract_ids, set())
        model_builder.set_stopped_files(
            self._filter_keys_for_model_builder(
                persist.stopped_file_names,
                set(getattr(controller, "_Controller__path_pairs_by_id", {}).keys()),
            )
        )

        retry_now = datetime.now()
        if any(
            0 < count < controller._Controller__MAX_MOVE_FAILURES
            and (
                file_id not in controller._Controller__move_retry_due
                or controller._Controller__move_retry_due[file_id] <= retry_now
            )
            for file_id, count in persist.move_failure_counts.items()
        ):
            model_builder.request_rebuild()

        # Build the new model, if needed.
        auto_purge_candidate_ids: set[str] = set()
        # Result-dependent remote lifecycle work must only run on a tick that
        # actually delivered a remote result.  Accumulator state can remain
        # final across no-event ticks, but there is no timestamp/files payload
        # to prune or reconcile on those ticks.
        remote_reconciliation_established = _remote_reconciliation_established(
            latest_remote_scan,
            remote_scan_final_relevant,
        )
        reconciliation_healthy = (
            controller._Controller__last_remote_reconciliation_healthy
            and controller._Controller__last_local_reconciliation_healthy
            and (joint_reconciliation_final if progressive_mode else True)
        )
        # A zero-byte local-only row is retained until this tick establishes
        # healthy remote authority for its absence and covers its path pair.
        # Local scan health is not part of that remote reconciliation decision.
        remote_auto_purge_reconciliation_healthy = (
            remote_reconciliation_established
            and controller._Controller__last_remote_reconciliation_healthy
        )
        enabled_path_pair_ids = set(
            getattr(controller, "_Controller__path_pairs_by_id", {}).keys()
        )
        remote_auto_purge_path_pair_ids = (
            _lifecycle_scanned_path_pair_ids(latest_remote_scan, enabled_path_pair_ids)
            if remote_auto_purge_reconciliation_healthy and latest_remote_scan is not None
            else set()
        )
        if remote_reconciliation_established:
            remote_scan = latest_remote_scan
            if remote_scan is None:
                raise RuntimeError("Remote reconciliation requires a scan result")
            scanned_path_pair_ids = _lifecycle_scanned_path_pair_ids(
                remote_scan, enabled_path_pair_ids,
            )
            remote_file_ids = {
                ModelFile.build_file_id(file.name, getattr(file, "path_pair_id", None))
                for file in remote_scan.files
            }
            protected_file_ids = {
                status.file_id for status in (lftp_statuses or [])
                if status.state in (LftpJobStatus.State.QUEUED, LftpJobStatus.State.RUNNING)
            }
            protected_file_ids.update(
                ModelFile.build_file_id(file_name, path_pair_id)
                for file_name, path_pair_id, _ in controller._Controller__pending_completion_file_names
            )
            protected_file_ids.update(
                self._filter_keys_for_model_builder(
                    persist.stopped_file_names,
                    enabled_path_pair_ids,
                )
            )
            snapshot_delete_ids = getattr(controller, "_snapshot_delete_command_file_ids", None)
            if callable(snapshot_delete_ids):
                delete_ids = snapshot_delete_ids()
                if isinstance(delete_ids, set):
                    delete_items = cast(set[object], delete_ids)
                    protected_file_ids.update(
                        file_id for file_id in delete_items if isinstance(file_id, str)
                    )
            prune_lifecycles = getattr(controller, "_prune_download_start_lifecycles", None)
            # Download-start lifecycle pruning establishes remote absence. It
            # requires this remote scan's authoritative coverage, but a
            # concurrent local scan outage must not retain stale remote
            # lifecycle entries indefinitely.
            if controller._Controller__last_remote_reconciliation_healthy \
                    and callable(prune_lifecycles) and scanned_path_pair_ids:
                prune_lifecycles(
                    remote_scan.timestamp,
                    scanned_path_pair_ids,
                    remote_file_ids,
                    protected_file_ids,
                )
        build_triggered = model_builder.has_changes()
        if build_triggered:
            diagnostics = getattr(getattr(controller, "_Controller__context", None), "performance_diagnostics", None)
            try:
                started_at = diagnostics.begin_duration(DURATION_MODEL_BUILD) if diagnostics is not None else None
            except Exception:
                started_at = None
            try:
                new_model = model_builder.build_model()
            finally:
                if diagnostics is not None:
                    try:
                        diagnostics.finish_duration(DURATION_MODEL_BUILD, started_at)
                    except Exception:
                        pass

            with controller._Controller__model_lock:
                def pending_completion_file_ids():
                    return {
                        ModelFile.build_file_id(file_name, path_pair_id)
                        for file_name, path_pair_id, _ in controller._Controller__pending_completion_file_names
                    }

                def discard_pending_completion_file(file_id: str) -> None:
                    controller._Controller__pending_completion_file_names = {
                        file_name
                        for file_name in controller._Controller__pending_completion_file_names
                        if ModelFile.build_file_id(file_name[0], file_name[1]) != file_id
                    }
                    controller._Controller__pending_completion_progress_floors.pop(file_id, None)

                def remember_pending_completion_floor(file: ModelFile) -> None:
                    if file.file_id not in pending_completion_file_ids():
                        return
                    previous = controller._Controller__pending_completion_progress_floors.get(file.file_id)
                    current = (file.download_progress, file.transferred_size)
                    if previous is None:
                        controller._Controller__pending_completion_progress_floors[file.file_id] = current
                        return
                    previous_progress, previous_transferred = previous
                    current_progress, current_transferred = current
                    controller._Controller__pending_completion_progress_floors[file.file_id] = (
                        max(value for value in (previous_progress, current_progress) if value is not None)
                        if previous_progress is not None or current_progress is not None else None,
                        max(value for value in (previous_transferred, current_transferred) if value is not None)
                        if previous_transferred is not None or current_transferred is not None else None,
                    )

                def keep_completion_pending_after_failed_staging_move(file: ModelFile, consume_budget: bool):
                    persist.final_move_succeeded_file_names.discard(file.file_id)
                    controller._Controller__successful_final_move_handoff_file_ids.discard(file.file_id)
                    controller._Controller__current_process_final_publication_file_ids.discard(file.file_id)
                    controller._sync_final_move_succeeded_files_to_model()
                    path_pair_name = file.path_pair_name
                    if path_pair_name is None:
                        path_pair = controller._Controller__get_path_pair(file.path_pair_id)
                        path_pair_name = getattr(path_pair, "name", None)
                    controller._Controller__pending_completion_file_names.add((
                        file.name,
                        file.path_pair_id,
                        path_pair_name,
                    ))
                    remember_pending_completion_floor(file)
                    if consume_budget:
                        controller._Controller__deferred_move_file_ids.discard(file.file_id)
                        count = min(
                            controller._Controller__MAX_MOVE_FAILURES,
                            persist.move_failure_counts.get(file.file_id, 0) + 1,
                        )
                        persist.move_failure_counts[file.file_id] = count
                        if count < controller._Controller__MAX_MOVE_FAILURES:
                            delay = controller._Controller__MOVE_RETRY_DELAYS[count - 1]
                            controller._Controller__move_retry_due[file.file_id] = datetime.now() + timedelta(seconds=delay)
                        else:
                            controller._Controller__move_retry_due.pop(file.file_id, None)
                        model_builder.set_move_failed_files({
                            file_id for file_id, failures in persist.move_failure_counts.items()
                            if failures >= controller._Controller__MAX_MOVE_FAILURES
                        })
                    else:
                        controller._Controller__deferred_move_file_ids.add(file.file_id)
                    controller.logger.warning(
                        "Keeping download completion pending after failed staging move: %s",
                        file.file_id,
                    )
                    if file.path_pair_id is None:
                        controller._Controller__local_scan_process.force_scan()
                    else:
                        controller._Controller__local_scan_process.force_scan(file.path_pair_id)

                def publish_completed_download(
                        file: ModelFile, final_move_succeeded: bool,
                        current_process_publication: bool = False):
                    controller._record_download_completion(file)
                    persist.move_failure_counts.pop(file.file_id, None)
                    controller._Controller__deferred_move_file_ids.discard(file.file_id)
                    controller._Controller__move_retry_due.pop(file.file_id, None)
                    model_builder.set_move_failed_files({
                        file_id for file_id, failures in persist.move_failure_counts.items()
                        if failures >= controller._Controller__MAX_MOVE_FAILURES
                    })
                    if final_move_succeeded:
                        persist.final_move_succeeded_file_names.add(file.file_id)
                    else:
                        persist.final_move_succeeded_file_names.discard(file.file_id)
                    controller._sync_final_move_succeeded_files_to_model()
                    if final_move_succeeded:
                        controller._mark_successful_final_move_handoff(file.file_id)
                    if current_process_publication:
                        controller._mark_current_process_final_publication(file.file_id)
                    if file.file_id not in persist.downloaded_file_names:
                        persist.downloaded_file_names.add(file.file_id)
                        model_builder.set_downloaded_files(persist.downloaded_file_names)
                    controller._complete_download_start_lifecycle(file.file_id)
                    controller.clear_extracted_marker(file)
                    if controller._Controller__target_archive_trace_selector_matches_file(
                        file.file_id,
                        file.name,
                    ):
                        controller._Controller__trace_target_archive_event("downloaded_marker_added", {
                            "file": controller._Controller__summarize_target_archive_file(file),
                        })
                    controller._Controller__pending_completion_file_names = {
                        file_name
                        for file_name in controller._Controller__pending_completion_file_names
                        if ModelFile.build_file_id(file_name[0], file_name[1]) != file.file_id
                    }
                    controller._Controller__pending_completion_progress_floors.pop(file.file_id, None)

                def run_reserved_automatic_move(file: ModelFile):
                    if not controller._reserve_move_attempt(file.file_id):
                        return None
                    try:
                        return controller._Controller__move_from_staging(
                            file.name,
                            file.path_pair_id,
                        )
                    finally:
                        controller._release_move_attempt(file.file_id)

                # Diff the new model with old model.
                model_diff = ModelDiffUtil.diff_models(model, new_model)
                attempted_move_file_ids: set[str] = set()

                for file_id, count in persist.move_failure_counts.items():
                    if count <= 0 or count >= controller._Controller__MAX_MOVE_FAILURES:
                        continue
                    try:
                        restart_file = new_model.get_file(file_id)
                    except ModelError:
                        continue
                    controller._Controller__pending_completion_file_names.add((
                        restart_file.name,
                        restart_file.path_pair_id,
                        restart_file.path_pair_name,
                    ))

                # Apply changes to the new model.
                for diff in model_diff:
                    old_file = getattr(diff, "old_file", None)
                    new_file = getattr(diff, "new_file", None)

                    if (
                        diff.change == ModelDiff.Change.UPDATED
                        and old_file is not None
                        and new_file is not None
                    ):
                        remember_pending_completion_floor(old_file)
                        self._preserve_pending_completion_progress_floor(
                            old_file,
                            new_file,
                            pending_completion_file_ids(),
                        )
                    elif diff.change == ModelDiff.Change.REMOVED and old_file is not None:
                        remember_pending_completion_floor(old_file)
                    elif diff.change == ModelDiff.Change.ADDED and new_file is not None:
                        floor = controller._Controller__pending_completion_progress_floors.get(new_file.file_id)
                        if floor is not None:
                            self._apply_pending_completion_progress_floor(
                                new_file,
                                pending_completion_file_ids(),
                                floor[0],
                                floor[1],
                            )

                    if diff.change == ModelDiff.Change.ADDED:
                        assert new_file is not None
                        model.add_file(new_file)
                    elif diff.change == ModelDiff.Change.REMOVED:
                        assert old_file is not None
                        model.remove_file(old_file.file_id)
                    elif diff.change == ModelDiff.Change.UPDATED:
                        assert new_file is not None
                        model.update_file(new_file)

                    if (
                        diff.change == ModelDiff.Change.REMOVED
                        and old_file is not None
                        and latest_local_scan is not None
                        and old_file.file_id in pending_completion_file_ids()
                    ):
                        discard_pending_completion_file(old_file.file_id)

                    completion_proved = False
                    if (
                        new_file is not None
                        and old_file is not None
                        and new_file.file_id in pending_completion_file_ids()
                        and not controller._Controller__is_explicitly_stopped(
                            new_file.name,
                            new_file.path_pair_id,
                        )
                    ):
                        if new_file.state == ModelFile.State.DEFAULT and new_file.local_size is None:
                            discard_pending_completion_file(new_file.file_id)
                        if new_file.state in (
                            ModelFile.State.DOWNLOADED,
                            ModelFile.State.EXTRACTED,
                            ModelFile.State.DELETED,
                        ):
                            completion_proved = True
                        elif (
                            old_file.remote_size is not None
                            and new_file.local_size is not None
                            and new_file.local_size >= old_file.remote_size
                            and model_builder.has_complete_local_coverage(new_file.file_id)
                        ):
                            completion_proved = True

                    if completion_proved and new_file is not None:
                        failure_count = persist.move_failure_counts.get(new_file.file_id, 0)
                        retry_due = controller._Controller__move_retry_due.get(new_file.file_id)
                        if failure_count >= controller._Controller__MAX_MOVE_FAILURES or (
                            retry_due is not None and datetime.now() < retry_due
                        ):
                            continue
                        move_result = run_reserved_automatic_move(new_file)
                        if move_result is None:
                            continue
                        attempted_move_file_ids.add(new_file.file_id)
                        if move_result in (
                            controller.MoveFromStagingResult.FAILED,
                            controller.MoveFromStagingResult.CONFLICT,
                            controller.MoveFromStagingResult.DEFERRED,
                        ):
                            keep_completion_pending_after_failed_staging_move(
                                new_file,
                                move_result in (
                                    controller.MoveFromStagingResult.FAILED,
                                    controller.MoveFromStagingResult.CONFLICT,
                                ),
                            )
                        else:
                            publish_completed_download(
                                new_file,
                                move_result == controller.MoveFromStagingResult.COMPLETED or
                                new_file.file_id in persist.final_move_succeeded_file_names,
                                move_result == controller.MoveFromStagingResult.COMPLETED,
                            )

                    # Detect if a file was just downloaded through a direct state transition.
                    # Pending-completion files are handled above so disappearance does not
                    # immediately count as a completed download.
                    downloaded = False
                    if (
                        new_file is not None
                        and not completion_proved
                        and new_file.file_id not in pending_completion_file_ids()
                    ):
                        if (
                            diff.change == ModelDiff.Change.ADDED
                            and new_file.state == ModelFile.State.DOWNLOADED
                            and new_file.file_id not in persist.downloaded_file_names
                        ):
                            downloaded = True
                        elif (
                            diff.change == ModelDiff.Change.UPDATED
                            and new_file.state == ModelFile.State.DOWNLOADED
                            and old_file is not None
                            and old_file.state != ModelFile.State.DOWNLOADED
                        ):
                            downloaded = True
                    if downloaded:
                        assert new_file is not None
                        move_result = run_reserved_automatic_move(new_file)
                        if move_result is None:
                            continue
                        attempted_move_file_ids.add(new_file.file_id)
                        if move_result in (
                            controller.MoveFromStagingResult.FAILED,
                            controller.MoveFromStagingResult.CONFLICT,
                            controller.MoveFromStagingResult.DEFERRED,
                        ):
                            keep_completion_pending_after_failed_staging_move(
                                new_file,
                                move_result in (
                                    controller.MoveFromStagingResult.FAILED,
                                    controller.MoveFromStagingResult.CONFLICT,
                                ),
                            )
                        else:
                            publish_completed_download(
                                new_file,
                                move_result == controller.MoveFromStagingResult.COMPLETED or
                                new_file.file_id in persist.final_move_succeeded_file_names,
                                move_result == controller.MoveFromStagingResult.COMPLETED,
                            )

                # A pending file often has no subsequent model diff. Drive its
                # retry budget from the durable pending identity instead of
                # relying on incidental scan changes.
                for file_name, path_pair_id, _ in list(controller._Controller__pending_completion_file_names):
                    file_id = ModelFile.build_file_id(file_name, path_pair_id)
                    if file_id in attempted_move_file_ids:
                        continue
                    failure_count = persist.move_failure_counts.get(file_id, 0)
                    if failure_count >= controller._Controller__MAX_MOVE_FAILURES or (
                        failure_count <= 0
                        and file_id not in controller._Controller__deferred_move_file_ids
                    ):
                        continue
                    retry_due = controller._Controller__move_retry_due.get(file_id)
                    if retry_due is not None and datetime.now() < retry_due:
                        continue
                    try:
                        pending_file = new_model.get_file(file_id)
                    except ModelError:
                        continue
                    # Durable retry state alone must not re-authorize a move:
                    # the current effective local tree can have gained an
                    # active-only branch or collision since the last attempt.
                    # Leave pending/retry state intact until coverage is
                    # proven again.
                    if not model_builder.has_complete_local_coverage(file_id):
                        continue
                    move_result = run_reserved_automatic_move(pending_file)
                    if move_result is None:
                        continue
                    if move_result in (
                        controller.MoveFromStagingResult.COMPLETED,
                        controller.MoveFromStagingResult.ALREADY_COMPLETED,
                    ):
                        publish_completed_download(
                            pending_file,
                            move_result == controller.MoveFromStagingResult.COMPLETED or
                            pending_file.file_id in persist.final_move_succeeded_file_names,
                            move_result == controller.MoveFromStagingResult.COMPLETED,
                        )
                    elif move_result == controller.MoveFromStagingResult.NO_MOVE_APPLICABLE:
                        publish_completed_download(pending_file, False)
                    elif move_result in (
                        controller.MoveFromStagingResult.FAILED,
                        controller.MoveFromStagingResult.CONFLICT,
                    ):
                        keep_completion_pending_after_failed_staging_move(pending_file, True)
                    else:
                        keep_completion_pending_after_failed_staging_move(pending_file, False)

                for diff in model_diff:
                    new_file = getattr(diff, "new_file", None)
                    if (
                        diff.change in (ModelDiff.Change.ADDED, ModelDiff.Change.UPDATED)
                        and new_file is not None
                        and controller._Controller__should_auto_purge_local_file(new_file)
                    ):
                        if new_file.path_pair_id in remote_auto_purge_path_pair_ids:
                            auto_purge_candidate_ids.add(new_file.file_id)
                        else:
                            controller._Controller__pending_auto_purge_file_ids.add(new_file.file_id)

                # Prune the extracted files list of any files that were deleted locally.
                # This prevents these files from going to EXTRACTED state if they are re-downloaded.
                remove_extracted_file_names: set[str] = set()
                existing_file_ids = model.get_file_ids()
                if reconciliation_healthy:
                    for extracted_file_name in persist.extracted_file_names:
                        if extracted_file_name in existing_file_ids:
                            file = model.get_file(extracted_file_name)
                            if file.state == ModelFile.State.DELETED:
                                remove_extracted_file_names.add(extracted_file_name)
                if remove_extracted_file_names:
                    controller.logger.info("Removing from extracted list: {}".format(remove_extracted_file_names))
                    persist.extracted_file_names.difference_update(remove_extracted_file_names)
                    if controller._Controller__is_target_archive_trace_enabled():
                        for extracted_file_name in remove_extracted_file_names:
                            if controller._Controller__target_archive_trace_selector_matches_file(
                                extracted_file_name,
                                extracted_file_name,
                            ):
                                controller._Controller__trace_target_archive_event("extracted_marker_removed", {
                                    "file_name": extracted_file_name,
                                    "file_id": ModelFile.build_file_id(extracted_file_name, None),
                                })
                    model_builder.set_extracted_files(persist.extracted_file_names)

                active_model_names = set(model.get_file_names())
                active_model_ids = set(model.get_file_ids())
                if reconciliation_healthy:
                    enabled_path_pair_ids = set(
                        getattr(controller, "_Controller__path_pairs_by_id", {}).keys()
                    )
                    if bool(getattr(latest_remote_scan, "is_progress", False)):
                        enabled_path_pair_ids &= set(
                            getattr(latest_remote_scan, "completed_path_pair_ids", set())
                        )
                    if bool(getattr(latest_local_scan, "is_progress", False)):
                        enabled_path_pair_ids &= set(
                            getattr(latest_local_scan, "completed_path_pair_ids", set())
                        )
                    pending_ids = pending_completion_file_ids()
                    stale_move_failure_ids = {
                        file_id for file_id in persist.move_failure_counts
                        if file_id in self._safe_stale_marker_ids(
                            set(persist.move_failure_counts),
                            active_model_ids,
                            active_model_names,
                            pending_ids,
                            enabled_path_pair_ids,
                        )
                    }
                    if stale_move_failure_ids:
                        for file_id in stale_move_failure_ids:
                            persist.move_failure_counts.pop(file_id, None)
                            controller._Controller__move_retry_due.pop(file_id, None)
                            controller._Controller__deferred_move_file_ids.discard(file_id)
                        with controller._Controller__move_attempt_lock:
                            controller._Controller__move_attempt_reservations.difference_update(
                                stale_move_failure_ids
                            )
                        model_builder.set_move_failed_files({
                            file_id for file_id, failures in persist.move_failure_counts.items()
                            if failures >= controller._Controller__MAX_MOVE_FAILURES
                        })
                    remove_downloaded_file_names = self._safe_stale_marker_ids(
                        set(persist.downloaded_file_names),
                        active_model_ids,
                        active_model_names,
                        pending_ids,
                        enabled_path_pair_ids,
                    )
                    if remove_downloaded_file_names:
                        controller.logger.info("Removing from downloaded list: {}".format(remove_downloaded_file_names))
                        persist.downloaded_file_names.difference_update(remove_downloaded_file_names)
                        downloaded_timestamps = getattr(persist, "downloaded_timestamps", {})
                        for file_id in remove_downloaded_file_names:
                            downloaded_timestamps.pop(file_id, None)
                        persist.final_move_succeeded_file_names.difference_update(remove_downloaded_file_names)
                        controller._sync_final_move_succeeded_files_to_model()
                        if controller._Controller__is_target_archive_trace_enabled():
                            for downloaded_file_name in remove_downloaded_file_names:
                                if controller._Controller__target_archive_trace_selector_matches_file(
                                    downloaded_file_name,
                                    downloaded_file_name,
                                ):
                                    controller._Controller__trace_target_archive_event("downloaded_marker_removed", {
                                        "file_name": downloaded_file_name,
                                        "file_id": ModelFile.build_file_id(downloaded_file_name, None),
                                    })
                        model_builder.set_downloaded_files(persist.downloaded_file_names)
                        model_builder.set_downloaded_timestamps({
                            file_id: timestamp
                            for file_id, timestamp in downloaded_timestamps.items()
                            if self._normalize_scoped_persist_key(file_id, enabled_path_pair_ids) == file_id
                        })

                    downloaded_timestamps = getattr(persist, "downloaded_timestamps", {})
                    stale_downloaded_timestamp_ids = self._safe_stale_marker_ids(
                        set(downloaded_timestamps),
                        active_model_ids,
                        active_model_names,
                        pending_ids,
                        enabled_path_pair_ids,
                    )
                    if stale_downloaded_timestamp_ids:
                        for file_id in stale_downloaded_timestamp_ids:
                            downloaded_timestamps.pop(file_id, None)
                        model_builder.set_downloaded_timestamps({
                            file_id: timestamp
                            for file_id, timestamp in downloaded_timestamps.items()
                            if self._normalize_scoped_persist_key(file_id, enabled_path_pair_ids) == file_id
                        })

                    stale_extracted_file_names = self._safe_stale_marker_ids(
                        set(persist.extracted_file_names),
                        active_model_ids,
                        active_model_names,
                        pending_ids,
                        enabled_path_pair_ids,
                    )
                    if stale_extracted_file_names:
                        controller.logger.info(
                            "Removing stale extracted markers: %s",
                            stale_extracted_file_names,
                        )
                        persist.extracted_file_names.difference_update(stale_extracted_file_names)
                        model_builder.set_extracted_files(persist.extracted_file_names)

                    stale_final_move_succeeded_file_names = self._safe_stale_marker_ids(
                        set(persist.final_move_succeeded_file_names),
                        active_model_ids,
                        active_model_names,
                        pending_ids,
                        enabled_path_pair_ids,
                    )
                    if stale_final_move_succeeded_file_names:
                        controller.logger.info(
                            "Removing stale final-move markers: %s",
                            stale_final_move_succeeded_file_names,
                        )
                        persist.final_move_succeeded_file_names.difference_update(
                            stale_final_move_succeeded_file_names
                        )
                        controller._sync_final_move_succeeded_files_to_model()

        if remote_auto_purge_reconciliation_healthy and controller._Controller__pending_auto_purge_file_ids:
            pending_auto_purge_candidates: set[str] = set()
            for file_id in list(controller._Controller__pending_auto_purge_file_ids):
                try:
                    file = model.get_file(file_id)
                except ModelError:
                    controller._Controller__pending_auto_purge_file_ids.discard(file_id)
                    continue
                if file.path_pair_id not in remote_auto_purge_path_pair_ids:
                    continue
                if controller._Controller__should_auto_purge_local_file(file):
                    pending_auto_purge_candidates.add(file_id)
                else:
                    controller._Controller__pending_auto_purge_file_ids.discard(file_id)
            auto_purge_candidate_ids.update(pending_auto_purge_candidates)
            controller._Controller__pending_auto_purge_file_ids.difference_update(auto_purge_candidate_ids)

        for file_id in auto_purge_candidate_ids:
            file = model.get_file(file_id)
            controller._Controller__queue_delete_local_process(file, controller._Controller__local_scan_process.force_scan)

        # Update the controller status.
        if latest_remote_scan is not None:
            remote_scan_failed = bool(getattr(latest_remote_scan, "failed", False))
            controller._Controller__context.status.controller.latest_remote_scan_time = latest_remote_scan.timestamp
            controller._Controller__context.status.controller.latest_remote_scan_failed = remote_scan_failed
            controller._Controller__context.status.controller.latest_remote_scan_error = latest_remote_scan.error_message
            if remote_reconciliation_established and reconciliation_healthy \
                    and not remote_scan_failed and not controller._Controller__startup_recovery_done:
                controller._Controller__recover_interrupted_downloads(remote_files)
        if latest_local_scan is not None:
            controller._Controller__context.status.controller.latest_local_scan_time = latest_local_scan.timestamp
        if build_triggered:
            with controller._Controller__model_lock:
                controller._Controller__model.set_tree_file_count(new_model.tree_file_count)
                model_builder.adopt_applied_model(new_model, controller._Controller__model)
        return build_triggered
