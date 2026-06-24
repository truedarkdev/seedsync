# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from controller.persist_keys import KEY_SEP
from controller.model_updater import ModelUpdater
from model import ModelFile
from system import SystemFile


class TestModelUpdater(unittest.TestCase):
    def _make_controller(self, downloaded_file_names, extracted_file_names, stopped_file_names, path_pairs_by_id=None):
        persist = SimpleNamespace(
            downloaded_file_names=downloaded_file_names,
            extracted_file_names=extracted_file_names,
            stopped_file_names=stopped_file_names,
        )
        model_builder = MagicMock()
        controller = SimpleNamespace(
            _Controller__persist=persist,
            _Controller__model_builder=model_builder,
        )
        if path_pairs_by_id is not None:
            controller._Controller__path_pairs_by_id = path_pairs_by_id
        return controller, model_builder

    def _make_lftp_completion_controller(self, prev_downloading_file_names=None):
        controller = SimpleNamespace(
            _Controller__prev_downloading_file_names=set(prev_downloading_file_names or []),
            _Controller__pending_completion_file_names=set(),
            _Controller__is_explicitly_stopped=MagicMock(return_value=False),
            _Controller__local_scan_process=MagicMock(),
            logger=MagicMock(),
        )
        return controller

    def test_sync_persist_to_all_builders_forwards_persisted_categories(self):
        downloaded_file_names = {"downloaded-a", "downloaded-b"}
        extracted_file_names = {"extracted-a"}
        stopped_file_names = {"stopped-a", "stopped-b"}
        controller, model_builder = self._make_controller(
            downloaded_file_names,
            extracted_file_names,
            stopped_file_names,
        )

        updater = ModelUpdater(controller)
        updater.sync_persist_to_all_builders()

        self.assertEqual(
            [
                call.set_downloaded_files(downloaded_file_names),
                call.set_extracted_files(extracted_file_names),
                call.set_stopped_files(stopped_file_names),
            ],
            model_builder.mock_calls,
        )

    def test_sync_persist_to_all_builders_normalizes_pair_separator_keys(self):
        pair_id = "movies"
        normalized_file_id = ModelFile.build_file_id("legacy.mkv", pair_id)
        controller, model_builder = self._make_controller(
            downloaded_file_names={
                "plain.txt",
                f"{pair_id}{KEY_SEP}legacy.mkv",
            },
            extracted_file_names={
                f"{pair_id}{KEY_SEP}legacy.mkv",
            },
            stopped_file_names={
                f"{pair_id}{KEY_SEP}legacy.mkv",
            },
            path_pairs_by_id={pair_id: SimpleNamespace()},
        )

        updater = ModelUpdater(controller)
        updater.sync_persist_to_all_builders()

        self.assertEqual(
            [
                call.set_downloaded_files({"plain.txt", normalized_file_id}),
                call.set_extracted_files({normalized_file_id}),
                call.set_stopped_files({normalized_file_id}),
            ],
            model_builder.mock_calls,
        )

    def test_sync_persist_to_all_builders_preserves_default_name_with_colon(self):
        pair_id = "movies"
        default_file_name = f"{pair_id}:legacy.mkv"
        controller, model_builder = self._make_controller(
            downloaded_file_names={default_file_name},
            extracted_file_names={default_file_name},
            stopped_file_names={default_file_name},
            path_pairs_by_id={pair_id: SimpleNamespace()},
        )

        updater = ModelUpdater(controller)
        updater.sync_persist_to_all_builders()

        self.assertEqual(
            [
                call.set_downloaded_files({default_file_name}),
                call.set_extracted_files({default_file_name}),
                call.set_stopped_files({default_file_name}),
            ],
            model_builder.mock_calls,
        )

    def test_sync_persist_to_all_builders_normalizes_uuid_legacy_colon_keys(self):
        pair_id = "12345678-1234-1234-1234-123456789abc"
        legacy_key = f"{pair_id}:legacy.mkv"
        normalized_file_id = ModelFile.build_file_id("legacy.mkv", pair_id)
        controller, model_builder = self._make_controller(
            downloaded_file_names={legacy_key},
            extracted_file_names={legacy_key},
            stopped_file_names={legacy_key},
            path_pairs_by_id={pair_id: SimpleNamespace()},
        )

        updater = ModelUpdater(controller)
        updater.sync_persist_to_all_builders()

        self.assertEqual(
            [
                call.set_downloaded_files({normalized_file_id}),
                call.set_extracted_files({normalized_file_id}),
                call.set_stopped_files({normalized_file_id}),
            ],
            model_builder.mock_calls,
        )

    def test_update_filters_remote_scan_files_before_publishing(self):
        remote_root = SystemFile("Series", 350, True)
        season = SystemFile("Season 1", 300, True)
        season.add_child(SystemFile("episode1.mkv", 100, False))
        season.add_child(SystemFile("episode1.nfo", 5, False))
        season.add_child(SystemFile("notes.txt", 3, False))
        remote_root.add_child(season)
        remote_root.add_child(SystemFile("keep.mkv", 50, False))
        remote_root.add_child(SystemFile("skip.nfo", 5, False))
        latest_remote_scan = SimpleNamespace(
            timestamp=0,
            failed=False,
            error_message=None,
            files=[remote_root],
        )

        controller = SimpleNamespace(
            _Controller__persist=SimpleNamespace(
                downloaded_file_names=set(),
                extracted_file_names=set(),
                stopped_file_names=set(),
            ),
            _Controller__model_builder=MagicMock(),
            _Controller__model=MagicMock(),
            _Controller__remote_scan_process=MagicMock(),
            _Controller__local_scan_process=MagicMock(),
            _Controller__active_scan_process=MagicMock(),
            _Controller__extract_process=MagicMock(),
            _Controller__validate_process=MagicMock(),
            _Controller__lftp=MagicMock(),
            _Controller__context=SimpleNamespace(
                config=SimpleNamespace(
                    general=SimpleNamespace(exclude_patterns="*.nfo, Sample/"),
                ),
                status=SimpleNamespace(
                    controller=SimpleNamespace(),
                    server=SimpleNamespace(),
                ),
            ),
            logger=MagicMock(),
            _Controller__temp_diag=MagicMock(),
            _Controller__set_active_scanner_files=MagicMock(),
            _Controller__record_breadcrumb=MagicMock(),
            _Controller__trace_corr_id_from_files=MagicMock(return_value="remote-scan-corr"),
            _Controller__startup_recovery_done=True,
            _Controller__pending_completion_file_names=set(),
            _Controller__prev_downloading_file_names=set(),
            _Controller__malformed_status_only_file_ids=set(),
            _Controller__pending_auto_purge_file_ids=set(),
            _Controller__last_lftp_statuses=[],
            _Controller__active_downloading_file_names=[],
            _Controller__active_extracting_file_names=[],
            _Controller__next_lftp_status_poll_at=None,
            _Controller__lftp_status_poll_retry_seconds=1,
            _Controller__lftp_status_cache_expires_at=None,
            _Controller__lftp_status_cache_max_age_seconds=3,
            _Controller__lftp_status_poll_retry_active=False,
            _Controller__exclude_patterns="*.nfo, Sample/",
        )
        controller._Controller__context.config.general.exclude_patterns = ""
        controller._Controller__remote_scan_process.pop_latest_result.return_value = latest_remote_scan
        controller._Controller__local_scan_process.pop_latest_result.return_value = None
        controller._Controller__active_scan_process.pop_latest_result.return_value = None
        controller._Controller__extract_process.pop_latest_statuses.return_value = None
        controller._Controller__extract_process.pop_completed.return_value = []
        controller._Controller__extract_process.pop_failed.return_value = []
        controller._Controller__validate_process.pop_latest_statuses.return_value = None
        controller._Controller__lftp.status.return_value = []
        controller._Controller__lftp.last_status_poll_healthy = True
        controller._Controller__model_builder.has_changes.return_value = False

        updater = ModelUpdater(controller)
        updater.update()

        controller._Controller__model_builder.set_remote_files.assert_called_once()
        filtered_files = controller._Controller__model_builder.set_remote_files.call_args[0][0]
        self.assertEqual(["Series"], [file.name for file in filtered_files])
        self.assertEqual(["Season 1", "keep.mkv"], [file.name for file in filtered_files[0].children])
        self.assertEqual(103, filtered_files[0].children[0].size)
        self.assertEqual(["episode1.mkv", "notes.txt"], [file.name for file in filtered_files[0].children[0].children])
        controller._Controller__record_breadcrumb.assert_any_call(
            stage="scan",
            message="remote_scan_result",
            details={
                "file_count": 1,
                "failed": False,
                "error_message": None,
            },
            event_type="state_transition",
            corr_id="remote-scan-corr",
        )

    def test_handle_lftp_completion_detection_records_completed_downloads_and_forces_rescan(self):
        completion_entry = ("movie.mkv", "movies", "Movies")
        still_downloading_entry = ("episode.mkv", "tv", "TV")
        explicitly_stopped_entry = ("stopped.mkv", "movies", "Movies")

        controller = self._make_lftp_completion_controller(
            prev_downloading_file_names={
                completion_entry,
                still_downloading_entry,
                explicitly_stopped_entry,
            }
        )
        controller._Controller__is_explicitly_stopped.side_effect = (
            lambda name, path_pair_id: (name, path_pair_id) == explicitly_stopped_entry[:2]
        )

        updater = ModelUpdater(controller)
        updater._handle_lftp_completion_detection(
            [still_downloading_entry],
            True,
        )

        self.assertEqual(
            {still_downloading_entry},
            controller._Controller__prev_downloading_file_names,
        )
        self.assertEqual(
            {completion_entry},
            controller._Controller__pending_completion_file_names,
        )
        controller._Controller__local_scan_process.force_scan.assert_called_once_with()
        controller.logger.info.assert_called_once_with(
            "Download completion pending (LFTP job finished): {}".format(
                ModelFile.build_file_id(*completion_entry[:2])
            )
        )

    def test_handle_lftp_completion_detection_skips_when_detection_is_not_ready(self):
        previous_entry = ("movie.mkv", "movies", "Movies")
        controller = self._make_lftp_completion_controller({previous_entry})

        updater = ModelUpdater(controller)
        updater._handle_lftp_completion_detection([], False)

        self.assertEqual(
            {previous_entry},
            controller._Controller__prev_downloading_file_names,
        )
        self.assertEqual(set(), controller._Controller__pending_completion_file_names)
        controller._Controller__local_scan_process.force_scan.assert_not_called()
        controller._Controller__is_explicitly_stopped.assert_not_called()
        controller.logger.info.assert_not_called()
