# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from controller.persist_keys import KEY_SEP
from controller.model_updater import ModelUpdater
from model import ModelFile


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
