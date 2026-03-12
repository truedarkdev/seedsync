# Copyright 2026, SeedSync Contributors, All rights reserved.

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[3] / "common" / "constants.py"
SPEC = importlib.util.spec_from_file_location("test_common_constants_module", MODULE_PATH)
constants_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(constants_module)
Constants = constants_module.Constants


class TestConstants(unittest.TestCase):
    def test_shared_constant_values(self):
        self.assertEqual("seedsync", Constants.SERVICE_NAME)
        self.assertEqual(0.5, Constants.MAIN_THREAD_SLEEP_INTERVAL_IN_SECS)
        self.assertEqual(10 * 1024 * 1024, Constants.MAX_LOG_SIZE_IN_BYTES)
        self.assertEqual(10, Constants.LOG_BACKUP_COUNT)
        self.assertEqual("web_access", Constants.WEB_ACCESS_LOG_NAME)
        self.assertEqual(30, Constants.MIN_PERSIST_TO_FILE_INTERVAL_IN_SECS)
        self.assertEqual(4, Constants.JSON_PRETTY_PRINT_INDENT)
        self.assertEqual(".lftp", Constants.LFTP_TEMP_FILE_SUFFIX)
