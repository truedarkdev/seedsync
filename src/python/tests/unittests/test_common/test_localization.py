# Copyright 2026, SeedSync Contributors, All rights reserved.

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[3] / "common" / "localization.py"
SPEC = importlib.util.spec_from_file_location("test_common_localization_module", MODULE_PATH)
localization_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(localization_module)
Localization = localization_module.Localization


class TestLocalization(unittest.TestCase):
    def test_error_keys_match_current_contract(self):
        self.assertEqual("The file '{}' doesn't exist.", Localization.Error.MISSING_FILE)
        self.assertEqual(
            "An error occurred while scanning the remote server: '{}'.",
            Localization.Error.REMOTE_SERVER_SCAN
        )
        self.assertEqual(
            "An error occurred while installing scanner script to remote server: '{}'.",
            Localization.Error.REMOTE_SERVER_INSTALL
        )
        self.assertEqual(
            "An error occurred while scanning the local system.",
            Localization.Error.LOCAL_SERVER_SCAN
        )
        self.assertEqual(
            "The settings are not fully configured.",
            Localization.Error.SETTINGS_INCOMPLETE
        )
        self.assertEqual(
            "Please configure the following settings: {}",
            Localization.Error.SETTINGS_INCOMPLETE_FIELDS
        )

    def test_error_keys_support_expected_formatting(self):
        self.assertEqual(
            "The file '/tmp/example' doesn't exist.",
            Localization.Error.MISSING_FILE.format("/tmp/example")
        )
        self.assertEqual(
            "An error occurred while scanning the remote server: 'boom'.",
            Localization.Error.REMOTE_SERVER_SCAN.format("boom")
        )
        self.assertEqual(
            "An error occurred while installing scanner script to remote server: 'permission denied'.",
            Localization.Error.REMOTE_SERVER_INSTALL.format("permission denied")
        )
        self.assertEqual(
            "Please configure the following settings: local_path, remote_path",
            Localization.Error.SETTINGS_INCOMPLETE_FIELDS.format("local_path, remote_path")
        )
