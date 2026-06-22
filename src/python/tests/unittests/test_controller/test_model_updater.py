# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from controller.model_updater import ModelUpdater


class TestModelUpdater(unittest.TestCase):
    def test_sync_persist_to_all_builders_forwards_persisted_categories(self):
        downloaded_file_names = {"downloaded-a", "downloaded-b"}
        extracted_file_names = {"extracted-a"}
        stopped_file_names = {"stopped-a", "stopped-b"}
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
