# Copyright 2017, Inderpreet Singh, All rights reserved.

"""Model update orchestration extracted from controller.py.

The controller still owns the underlying state, but the per-tick refresh loop
now lives here so `Controller.process()` only coordinates the pipeline and
delegates the model refresh boundary.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from lftp import LftpError, LftpJobStatus, LftpJobStatusParserError
from model import Model, ModelDiff, ModelDiffUtil, ModelError, ModelFile

from common.exclude_patterns import filter_excluded_files

from .extract import ExtractStatus
from .persist_keys import KEY_SEP


_LEGACY_PAIR_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ModelUpdater:
    """Runs the per-tick model update loop for a controller instance."""

    def __init__(self, controller: Any):
        self._controller = controller

    @staticmethod
    def _get_exclude_patterns(controller: Any) -> str:
        exclude_patterns = getattr(controller, "_Controller__exclude_patterns", None)
        if isinstance(exclude_patterns, str):
            return exclude_patterns
        config = getattr(getattr(controller, "_Controller__context", None), "config", None)
        general = getattr(config, "general", None)
        exclude_patterns = getattr(general, "exclude_patterns", "")
        return exclude_patterns if isinstance(exclude_patterns, str) else ""

    def sync_persist_to_all_builders(self):
        controller = self._controller
        path_pair_ids = set(getattr(controller, "_Controller__path_pairs_by_id", {}).keys())
        controller._Controller__model_builder.set_downloaded_files(  # type: ignore[attr-defined]
            self._filter_keys_for_model_builder(
                controller._Controller__persist.downloaded_file_names,
                path_pair_ids,
            )
        )
        controller._Controller__model_builder.set_extracted_files(  # type: ignore[attr-defined]
            self._filter_keys_for_model_builder(
                controller._Controller__persist.extracted_file_names,
                path_pair_ids,
            )
        )
        controller._Controller__model_builder.set_stopped_files(  # type: ignore[attr-defined]
            self._filter_keys_for_model_builder(
                controller._Controller__persist.stopped_file_names,
                path_pair_ids,
            )
        )

    @staticmethod
    def _filter_keys_for_model_builder(keys: set[str], path_pair_ids: set[str]) -> set[str]:
        if not path_pair_ids:
            return set(keys)

        filtered = set()
        for key in keys:
            normalized_key = key
            for path_pair_id in path_pair_ids:
                prefix = f"{path_pair_id}{KEY_SEP}"
                if key.startswith(prefix):
                    normalized_key = ModelFile.build_file_id(key[len(prefix) :], path_pair_id)
                    break
                if ModelUpdater._is_legacy_pair_scoped_colon_key(key, path_pair_id):
                    normalized_key = ModelFile.build_file_id(key[len(path_pair_id) + 1 :], path_pair_id)
                    break
            filtered.add(normalized_key)
        return filtered

    @staticmethod
    def _is_legacy_pair_scoped_colon_key(key: str, path_pair_id: str) -> bool:
        if _LEGACY_PAIR_ID_RE.match(path_pair_id) is None:
            return False
        return key.startswith(f"{path_pair_id}:")

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
            if not controller._Controller__is_explicitly_stopped(file_name[0], file_name[1])  # type: ignore[attr-defined]
        }
        if just_completed_file_names:
            for name, path_pair_id, _ in just_completed_file_names:
                controller.logger.info(
                    "Download completion pending (LFTP job finished): {}".format(
                        ModelFile.build_file_id(name, path_pair_id)
                    )
                )
            controller._Controller__pending_completion_file_names.update(just_completed_file_names)
            controller._Controller__local_scan_process.force_scan()  # type: ignore[attr-defined]
        controller._Controller__prev_downloading_file_names = current_downloading_file_names_set

    def update(self):  # noqa: C901 - extracted controller refresh loop
        controller = self._controller
        model_builder = controller._Controller__model_builder  # type: ignore[attr-defined]
        persist = controller._Controller__persist  # type: ignore[attr-defined]
        model = controller._Controller__model  # type: ignore[attr-defined]

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

        # Grab the latest scan results.
        latest_remote_scan = controller._Controller__remote_scan_process.pop_latest_result()  # type: ignore[attr-defined]
        latest_local_scan = controller._Controller__local_scan_process.pop_latest_result()  # type: ignore[attr-defined]
        latest_active_scan = controller._Controller__active_scan_process.pop_latest_result()  # type: ignore[attr-defined]

        # Grab the Lftp status.
        lftp_statuses = []
        lftp_status_poll_healthy = True
        lftp_status_snapshot_fresh = True
        lftp_status_source = "fresh_healthy"
        now = datetime.now()
        current_lftp_status_poll_healthy = getattr(controller._Controller__lftp, "last_status_poll_healthy", True)  # type: ignore[attr-defined]
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
                lftp_statuses = controller._Controller__lftp.status()  # type: ignore[attr-defined]
                lftp_status_poll_healthy = getattr(controller._Controller__lftp, "last_status_poll_healthy", True)  # type: ignore[attr-defined]
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
        latest_extract_statuses = controller._Controller__extract_process.pop_latest_statuses()  # type: ignore[attr-defined]
        latest_validation_statuses = controller._Controller__validate_process.pop_latest_statuses()  # type: ignore[attr-defined]

        # Grab the latest extracted file names.
        latest_extracted_results = controller._Controller__extract_process.pop_completed()  # type: ignore[attr-defined]
        latest_failed_results = controller._Controller__extract_process.pop_failed()  # type: ignore[attr-defined]
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
                controller._Controller__active_extracting_file_tuple(s)  # type: ignore[attr-defined]
                for s in latest_extract_statuses.statuses
                if s.state == ExtractStatus.State.EXTRACTING
                and not controller._Controller__extract_status_matches_failed_result(s, latest_failed_results)  # type: ignore[attr-defined]
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
        controller._Controller__set_active_scanner_files(  # type: ignore[attr-defined]
            controller._Controller__active_downloading_file_names
            + controller._Controller__active_extracting_file_names
            + list(controller._Controller__pending_completion_file_names)
        )

        # Update model builder state.
        if latest_remote_scan is not None:
            remote_files = filter_excluded_files(
                latest_remote_scan.files,
                self._get_exclude_patterns(controller),
            )
            model_builder.set_remote_files(remote_files)
            controller._Controller__record_breadcrumb(
                stage="scan",
                message="remote_scan_result",
                details={
                    "file_count": len(remote_files),
                    "failed": latest_remote_scan.failed,
                    "error_message": latest_remote_scan.error_message,
                },
                event_type="failure" if latest_remote_scan.failed else "state_transition",
                corr_id=controller._Controller__trace_corr_id_from_files(remote_files, "remote_scan"),  # type: ignore[attr-defined]
            )
        if latest_local_scan is not None:
            model_builder.set_local_files(latest_local_scan.files)
            recovered_extracted_file_ids = getattr(latest_local_scan, "managed_extract_file_ids", [])
            if isinstance(recovered_extracted_file_ids, (list, tuple, set)):
                persist.extracted_file_names.update(recovered_extracted_file_ids)
            controller._Controller__record_breadcrumb(
                stage="scan",
                message="local_scan_result",
                details={
                    "file_count": len(latest_local_scan.files),
                    "managed_extract_file_count": len(recovered_extracted_file_ids)
                    if isinstance(recovered_extracted_file_ids, (list, tuple, set))
                    else None,
                },
                event_type="state_transition",
                corr_id=controller._Controller__trace_corr_id_from_files(latest_local_scan.files, "local_scan"),  # type: ignore[attr-defined]
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
                corr_id=controller._Controller__trace_corr_id_from_files(latest_active_scan.files, "active_scan"),  # type: ignore[attr-defined]
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
            if controller._Controller__is_target_archive_trace_enabled():  # type: ignore[attr-defined]
                for status in latest_extract_statuses.statuses:
                    trace_target_file = controller._Controller__find_target_archive_model_file(status.name)  # type: ignore[attr-defined]
                    if trace_target_file is not None:
                        controller._Controller__trace_target_archive_event("extract_status", {  # type: ignore[attr-defined]
                            "file": controller._Controller__summarize_target_archive_file(trace_target_file),  # type: ignore[attr-defined]
                            "is_dir": status.is_dir,
                            "state": getattr(status.state, "name", status.state),
                        })
        if latest_validation_statuses is not None:
            model_builder.set_validation_statuses(latest_validation_statuses.statuses)
        def _is_known_extract_result_pair(result, result_kind: str) -> bool:
            path_pair_id = getattr(result, "path_pair_id", None)
            if path_pair_id is None:
                return True
            if controller._Controller__get_path_pair(path_pair_id) is not None:  # type: ignore[attr-defined]
                return True
            controller.logger.warning(
                "Ignoring extract %s for '%s': pair '%s' no longer exists",
                result_kind,
                result.name,
                path_pair_id,
            )
            return False

        if latest_extracted_results:
            known_extracted_results = []
            extracted_result_summaries = []
            for result in latest_extracted_results:
                if not _is_known_extract_result_pair(result, "completion"):
                    continue
                known_extracted_results.append(result)
                extracted_file_ids = {result.name}
                if result.file_id is not None:
                    extracted_file_ids.add(result.file_id)
                persist.extracted_file_names.update(extracted_file_ids)
                extracted_result_summaries.append({
                    "name": result.name,
                    "file_id": result.file_id,
                    "is_dir": result.is_dir,
                    "path_pair_id": result.path_pair_id,
                })
                trace_target_file = controller._Controller__find_target_archive_model_file(result.name, result.file_id)  # type: ignore[attr-defined]
                if trace_target_file is not None:
                    controller._Controller__trace_target_archive_event("extracted_marker_added", {  # type: ignore[attr-defined]
                        "file": controller._Controller__summarize_target_archive_file(trace_target_file),  # type: ignore[attr-defined]
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
                    corr_id=controller._Controller__trace_corr_id_from_files(known_extracted_results, "extract"),  # type: ignore[attr-defined]
                )
        if latest_failed_results:
            known_failed_results = []
            failed_result_summaries = []
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
                    corr_id=controller._Controller__trace_corr_id_from_files(known_failed_results, "extract"),  # type: ignore[attr-defined]
                )
        model_builder.set_stopped_files(persist.stopped_file_names)

        # Build the new model, if needed.
        auto_purge_candidate_ids = set()
        remote_reconciliation_established = latest_remote_scan is not None and not latest_remote_scan.failed
        if model_builder.has_changes():
            new_model = model_builder.build_model()

            with controller._Controller__model_lock:  # type: ignore[attr-defined]
                def pending_completion_file_ids():
                    return {
                        ModelFile.build_file_id(file_name, path_pair_id)
                        for file_name, path_pair_id, _ in controller._Controller__pending_completion_file_names
                    }

                def keep_completion_pending_after_failed_staging_move(file: ModelFile):
                    path_pair_name = file.path_pair_name
                    if path_pair_name is None:
                        path_pair = controller._Controller__get_path_pair(file.path_pair_id)  # type: ignore[attr-defined]
                        path_pair_name = getattr(path_pair, "name", None)
                    controller._Controller__pending_completion_file_names.add((
                        file.name,
                        file.path_pair_id,
                        path_pair_name,
                    ))
                    controller.logger.warning(
                        "Keeping download completion pending after failed staging move: %s",
                        file.file_id,
                    )
                    if file.path_pair_id is None:
                        controller._Controller__local_scan_process.force_scan()  # type: ignore[attr-defined]
                    else:
                        controller._Controller__local_scan_process.force_scan(file.path_pair_id)  # type: ignore[attr-defined]

                def publish_completed_download(file: ModelFile):
                    if file.file_id not in persist.downloaded_file_names:
                        persist.downloaded_file_names.add(file.file_id)
                        model_builder.set_downloaded_files(persist.downloaded_file_names)
                    controller.clear_extracted_marker(file)
                    if controller._Controller__target_archive_trace_selector_matches_file(  # type: ignore[attr-defined]
                        file.file_id,
                        file.name,
                    ):
                        controller._Controller__trace_target_archive_event("downloaded_marker_added", {  # type: ignore[attr-defined]
                            "file": controller._Controller__summarize_target_archive_file(file),  # type: ignore[attr-defined]
                        })
                    controller._Controller__pending_completion_file_names = {
                        file_name
                        for file_name in controller._Controller__pending_completion_file_names
                        if ModelFile.build_file_id(file_name[0], file_name[1]) != file.file_id
                    }

                # Diff the new model with old model.
                model_diff = ModelDiffUtil.diff_models(model, new_model)

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
                        and not controller._Controller__is_explicitly_stopped(  # type: ignore[attr-defined]
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
                        staging_move_succeeded = controller._Controller__move_from_staging(  # type: ignore[attr-defined]
                            new_file.name,
                            new_file.path_pair_id,
                        )
                        if staging_move_succeeded is False:
                            keep_completion_pending_after_failed_staging_move(new_file)
                        else:
                            publish_completed_download(new_file)

                    # Detect if a file was just downloaded through a direct state transition.
                    # Pending-completion files are handled above so disappearance does not
                    # immediately count as a completed download.
                    downloaded = False
                    if (
                        new_file is not None
                        and not completion_proved
                        and new_file.file_id not in pending_completion_file_ids()
                    ):
                        if diff.change == ModelDiff.Change.ADDED and new_file.state == ModelFile.State.DOWNLOADED:
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
                        staging_move_succeeded = controller._Controller__move_from_staging(  # type: ignore[attr-defined]
                            new_file.name,
                            new_file.path_pair_id,
                        )
                        if staging_move_succeeded is False:
                            keep_completion_pending_after_failed_staging_move(new_file)
                        else:
                            publish_completed_download(new_file)

                current_auto_purge_candidate_ids = set()
                for diff in model_diff:
                    new_file = getattr(diff, "new_file", None)
                    if (
                        diff.change in (ModelDiff.Change.ADDED, ModelDiff.Change.UPDATED)
                        and new_file is not None
                        and controller._Controller__should_auto_purge_local_file(new_file)  # type: ignore[attr-defined]
                    ):
                        current_auto_purge_candidate_ids.add(new_file.file_id)
                if remote_reconciliation_established:
                    auto_purge_candidate_ids.update(current_auto_purge_candidate_ids)
                else:
                    controller._Controller__pending_auto_purge_file_ids.update(current_auto_purge_candidate_ids)

                # Prune the extracted files list of any files that were deleted locally.
                # This prevents these files from going to EXTRACTED state if they are re-downloaded.
                remove_extracted_file_names = set()
                existing_file_ids = model.get_file_ids()
                for extracted_file_name in persist.extracted_file_names:
                    if extracted_file_name in existing_file_ids:
                        file = model.get_file(extracted_file_name)
                        if file.state == ModelFile.State.DELETED:
                            remove_extracted_file_names.add(extracted_file_name)
                    elif extracted_file_name in model.get_file_names():
                        try:
                            file = model.get_file(extracted_file_name)
                        except ModelError:
                            continue
                        if file.state == ModelFile.State.DELETED:
                            remove_extracted_file_names.add(extracted_file_name)
                    else:
                        # Not in the model at all. This could be because local and remote scans are not yet available.
                        pass
                if remove_extracted_file_names:
                    controller.logger.info("Removing from extracted list: {}".format(remove_extracted_file_names))
                    persist.extracted_file_names.difference_update(remove_extracted_file_names)
                    if controller._Controller__is_target_archive_trace_enabled():  # type: ignore[attr-defined]
                        for extracted_file_name in remove_extracted_file_names:
                            if controller._Controller__target_archive_trace_selector_matches_file(  # type: ignore[attr-defined]
                                extracted_file_name,
                                extracted_file_name,
                            ):
                                controller._Controller__trace_target_archive_event("extracted_marker_removed", {  # type: ignore[attr-defined]
                                    "file_name": extracted_file_name,
                                    "file_id": ModelFile.build_file_id(extracted_file_name, None),
                                })
                    model_builder.set_extracted_files(persist.extracted_file_names)

                active_model_names = set(model.get_file_names())
                active_model_ids = set(model.get_file_ids())
                if remote_reconciliation_established:
                    remove_downloaded_file_names = {
                        downloaded_file_name
                        for downloaded_file_name in persist.downloaded_file_names
                        if downloaded_file_name not in active_model_names
                        and downloaded_file_name not in active_model_ids
                        and downloaded_file_name not in pending_completion_file_ids()
                    }
                    if remove_downloaded_file_names:
                        controller.logger.info("Removing from downloaded list: {}".format(remove_downloaded_file_names))
                        persist.downloaded_file_names.difference_update(remove_downloaded_file_names)
                        if controller._Controller__is_target_archive_trace_enabled():  # type: ignore[attr-defined]
                            for downloaded_file_name in remove_downloaded_file_names:
                                if controller._Controller__target_archive_trace_selector_matches_file(  # type: ignore[attr-defined]
                                    downloaded_file_name,
                                    downloaded_file_name,
                                ):
                                    controller._Controller__trace_target_archive_event("downloaded_marker_removed", {  # type: ignore[attr-defined]
                                        "file_name": downloaded_file_name,
                                        "file_id": ModelFile.build_file_id(downloaded_file_name, None),
                                    })
                        model_builder.set_downloaded_files(persist.downloaded_file_names)

        if remote_reconciliation_established and controller._Controller__pending_auto_purge_file_ids:  # type: ignore[attr-defined]
            pending_auto_purge_candidates = set()
            for file_id in list(controller._Controller__pending_auto_purge_file_ids):
                try:
                    file = model.get_file(file_id)
                except ModelError:
                    controller._Controller__pending_auto_purge_file_ids.discard(file_id)
                    continue
                if controller._Controller__should_auto_purge_local_file(file):  # type: ignore[attr-defined]
                    pending_auto_purge_candidates.add(file_id)
                else:
                    controller._Controller__pending_auto_purge_file_ids.discard(file_id)
            auto_purge_candidate_ids.update(pending_auto_purge_candidates)
            controller._Controller__pending_auto_purge_file_ids.difference_update(auto_purge_candidate_ids)

        for file_id in auto_purge_candidate_ids:
            file = model.get_file(file_id)
            controller._Controller__queue_delete_local_process(file, controller._Controller__local_scan_process.force_scan)  # type: ignore[attr-defined]

        # Update the controller status.
        if latest_remote_scan is not None:
            controller._Controller__context.status.controller.latest_remote_scan_time = latest_remote_scan.timestamp  # type: ignore[attr-defined]
            controller._Controller__context.status.controller.latest_remote_scan_failed = latest_remote_scan.failed  # type: ignore[attr-defined]
            controller._Controller__context.status.controller.latest_remote_scan_error = latest_remote_scan.error_message  # type: ignore[attr-defined]
            if not latest_remote_scan.failed and not controller._Controller__startup_recovery_done:  # type: ignore[attr-defined]
                controller._Controller__recover_interrupted_downloads(remote_files)  # type: ignore[attr-defined]
        if latest_local_scan is not None:
            controller._Controller__context.status.controller.latest_local_scan_time = latest_local_scan.timestamp  # type: ignore[attr-defined]
