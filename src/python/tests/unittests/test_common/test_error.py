# Copyright 2026, SeedSync Contributors, All rights reserved.

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[3] / "common" / "error.py"
SPEC = importlib.util.spec_from_file_location("test_common_error_module", MODULE_PATH)
error_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(error_module)
AppError = error_module.AppError
ServiceExit = error_module.ServiceExit
ServiceRestart = error_module.ServiceRestart


class TestErrors(unittest.TestCase):
    def test_service_exit_inherits_from_app_error_and_exception(self):
        error = ServiceExit("stop")
        self.assertIsInstance(error, ServiceExit)
        self.assertIsInstance(error, AppError)
        self.assertIsInstance(error, Exception)
        self.assertEqual("stop", str(error))

    def test_service_restart_inherits_from_app_error_and_exception(self):
        error = ServiceRestart("restart")
        self.assertIsInstance(error, ServiceRestart)
        self.assertIsInstance(error, AppError)
        self.assertIsInstance(error, Exception)
        self.assertEqual("restart", str(error))
