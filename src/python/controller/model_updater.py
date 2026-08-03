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
from lftp import Lftp, LftpError, LftpJobStatus, LftpJobStatusParserError
from model import Model, ModelDiff, ModelDiffUtil, ModelError, ModelFile
from system import SystemFile
from transfer import RcloneTransferBackend

from common.exclude_patterns import filter_excluded_files

from .controller_persist import ControllerPersist
from .extract import ExtractCompletedResult, ExtractFailedResult, ExtractProcess, ExtractStatus
from .model_builder import ModelBuilder
from .scan import ScannerProcess
from .validate import ValidateProcess

if TYPE_CHECKING:
    from .controller import Controller


class _ControllerCoreAccess:
    logger: logging.Logger
    MoveFromStagingResult: type["Controller.MoveFromStagingResult"]
    _Controller__context: Context
    _Controller__persist: ControllerPersist
    _Controller__model: Model
    _Controller__model_builder: ModelBuilder
    _Controller__path_pairs_by_id: dict[str, PathPair]
    _Controller__active_scan_process: ScannerProcess
    _Controller__local_scan_process: ScannerProcess
    _Controller__remote_scan_process: ScannerProcess
    _Controller__extract_process: ExtractProcess
    _Controller__validate_process: ValidateProcess
    _Controller__lftp: Lftp | RcloneTransferBackend
    _Controller__active_downloading_file_names: list[tuple[str, Optional[str], Optional[str]]]
    _Controller__active_extracting_file_names: list[tuple[str, Optional[str], Optional[str]]]
    _Controller__prev_downloading_file_names: set[tuple[str, Optional[str], Optional[str]]]
    _Controller__pending_completion_file_names: set[tuple[str, Optional[str], Optional[str]]]
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

    def __init__(self, controller: object) -> None:
        from .controller import Controller as ControllerType
        if not isinstance(controller, (ControllerType, SimpleNamespace)):
            raise TypeError("ModelUpdater requires the controller core runtime boundary")
        self._controller = cast(_ControllerCoreAccess, controller)

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
                self._filter_keys_for_model_builder(persist.final_move_succeeded_file_names, path_pair_ids)
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

    def update(self) -> None:
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
        latest_remote_scan = controller._Controller__remote_scan_process.pop_latest_result()
        latest_local_scan = controller._Controller__local_scan_process.pop_latest_result()
        latest_active_scan = controller._Controller__active_scan_process.pop_latest_result()

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

        # Update model builder state.
        remote_files: list[SystemFile] = []
        if latest_remote_scan is not None:
            remote_scan_failed = bool(getattr(latest_remote_scan, "failed", False))
            remote_files = filter_excluded_files(
                latest_remote_scan.files,
                self._get_exclude_patterns(controller),
            )
            controller._Controller__last_remote_reconciliation_healthy = not remote_scan_failed
            if not remote_scan_failed:
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
            controller._Controller__last_local_reconciliation_healthy = not local_scan_failed
            recovered_extracted_file_ids = []
            if not local_scan_failed:
                model_builder.set_local_files(latest_local_scan.files)
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
        if latest_active_scan is not None:
            model_builder.set_active_files(latest_active_scan.files)
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
        remote_reconciliation_established = (
            latest_remote_scan is not None
            and not bool(getattr(latest_remote_scan, "failed", False))
        )
        reconciliation_healthy = (
            controller._Controller__last_remote_reconciliation_healthy
            and controller._Controller__last_local_reconciliation_healthy
        )
        if remote_reconciliation_established:
            remote_scan = latest_remote_scan
            if remote_scan is None:
                raise RuntimeError("Remote reconciliation requires a scan result")
            enabled_path_pair_ids = set(
                getattr(controller, "_Controller__path_pairs_by_id", {}).keys()
            )
            scanned_path_pair_ids = set(enabled_path_pair_ids) if enabled_path_pair_ids else {None}
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
            if callable(prune_lifecycles):
                prune_lifecycles(
                    remote_scan.timestamp,
                    scanned_path_pair_ids,
                    remote_file_ids,
                    protected_file_ids,
                )
        if model_builder.has_changes():
            new_model = model_builder.build_model()

            with controller._Controller__model_lock:
                def pending_completion_file_ids():
                    return {
                        ModelFile.build_file_id(file_name, path_pair_id)
                        for file_name, path_pair_id, _ in controller._Controller__pending_completion_file_names
                    }

                def keep_completion_pending_after_failed_staging_move(file: ModelFile, consume_budget: bool):
                    persist.final_move_succeeded_file_names.discard(file.file_id)
                    model_builder.set_final_move_succeeded_files(persist.final_move_succeeded_file_names)
                    path_pair_name = file.path_pair_name
                    if path_pair_name is None:
                        path_pair = controller._Controller__get_path_pair(file.path_pair_id)
                        path_pair_name = getattr(path_pair, "name", None)
                    controller._Controller__pending_completion_file_names.add((
                        file.name,
                        file.path_pair_id,
                        path_pair_name,
                    ))
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

                def publish_completed_download(file: ModelFile, final_move_succeeded: bool):
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
                    model_builder.set_final_move_succeeded_files(persist.final_move_succeeded_file_names)
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
                        controller._Controller__pending_completion_file_names = {
                            file_name
                            for file_name in controller._Controller__pending_completion_file_names
                            if ModelFile.build_file_id(file_name[0], file_name[1]) != old_file.file_id
                        }

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
                            controller._Controller__pending_completion_file_names = {
                                file_name
                                for file_name in controller._Controller__pending_completion_file_names
                                if ModelFile.build_file_id(file_name[0], file_name[1]) != new_file.file_id
                            }
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
                            controller.MoveFromStagingResult.DEFERRED,
                        ):
                            keep_completion_pending_after_failed_staging_move(
                                new_file,
                                move_result == controller.MoveFromStagingResult.FAILED,
                            )
                        else:
                            publish_completed_download(
                                new_file,
                                move_result == controller.MoveFromStagingResult.COMPLETED or
                                new_file.file_id in persist.final_move_succeeded_file_names,
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
                            controller.MoveFromStagingResult.DEFERRED,
                        ):
                            keep_completion_pending_after_failed_staging_move(
                                new_file,
                                move_result == controller.MoveFromStagingResult.FAILED,
                            )
                        else:
                            publish_completed_download(
                                new_file,
                                move_result == controller.MoveFromStagingResult.COMPLETED or
                                new_file.file_id in persist.final_move_succeeded_file_names,
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
                        )
                    elif move_result == controller.MoveFromStagingResult.NO_MOVE_APPLICABLE:
                        publish_completed_download(pending_file, False)
                    elif move_result == controller.MoveFromStagingResult.FAILED:
                        keep_completion_pending_after_failed_staging_move(pending_file, True)
                    else:
                        keep_completion_pending_after_failed_staging_move(pending_file, False)

                current_auto_purge_candidate_ids: set[str] = set()
                for diff in model_diff:
                    new_file = getattr(diff, "new_file", None)
                    if (
                        diff.change in (ModelDiff.Change.ADDED, ModelDiff.Change.UPDATED)
                        and new_file is not None
                        and controller._Controller__should_auto_purge_local_file(new_file)
                    ):
                        current_auto_purge_candidate_ids.add(new_file.file_id)
                if remote_reconciliation_established:
                    auto_purge_candidate_ids.update(current_auto_purge_candidate_ids)
                else:
                    controller._Controller__pending_auto_purge_file_ids.update(current_auto_purge_candidate_ids)

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
                        model_builder.set_final_move_succeeded_files(
                            persist.final_move_succeeded_file_names
                        )
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
                        model_builder.set_final_move_succeeded_files(
                            persist.final_move_succeeded_file_names
                        )

        if remote_reconciliation_established and controller._Controller__pending_auto_purge_file_ids:
            pending_auto_purge_candidates: set[str] = set()
            for file_id in list(controller._Controller__pending_auto_purge_file_ids):
                try:
                    file = model.get_file(file_id)
                except ModelError:
                    controller._Controller__pending_auto_purge_file_ids.discard(file_id)
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
            if not remote_scan_failed and not controller._Controller__startup_recovery_done:
                controller._Controller__recover_interrupted_downloads(remote_files)
        if latest_local_scan is not None:
            controller._Controller__context.status.controller.latest_local_scan_time = latest_local_scan.timestamp
