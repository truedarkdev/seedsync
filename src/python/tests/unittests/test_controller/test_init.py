# Copyright 2026, SeedSync Contributors, All rights reserved.

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


PYTHON_ROOT = Path(__file__).resolve().parents[3]
PUBLIC_EXPORTS = [
    "Controller", "ControllerJob", "ControllerPersist", "ControllerMemoryMonitor",
    "ModelBuilder", "AutoQueue", "AutoQueuePersist", "IAutoQueuePersistListener",
    "AutoQueuePattern", "IScanner", "ScannerResult", "ScannerProcess", "ScannerError",
    "ValidateProcess", "ValidateStatus", "ValidateStatusResult",
]


class TestControllerPackage(unittest.TestCase):
    def test_public_exports_resolve(self):
        controller = importlib.import_module("controller")

        self.assertEqual(PUBLIC_EXPORTS, controller.__all__)
        self.assertEqual(sorted(PUBLIC_EXPORTS), [name for name in controller.__dir__() if name in PUBLIC_EXPORTS])
        for name in PUBLIC_EXPORTS:
            self.assertIsNotNone(getattr(controller, name))

    def test_subprocess_preserves_import_styles_and_unknown_attribute_behavior(self):
        script = """
import json

import controller
from controller import Controller, ScannerProcess

namespace = {}
exec("from controller import *", namespace)
try:
    getattr(controller, "NotAnExport")
except AttributeError:
    unknown_attribute_raises = True
else:
    unknown_attribute_raises = False

print(json.dumps({
    "explicit_imports": [Controller.__name__, ScannerProcess.__name__],
    "wildcard_exports": sorted(name for name in namespace if not name.startswith("__")),
    "unknown_attribute_raises": unknown_attribute_raises,
}))
"""
        environment = os.environ.copy()
        python_path = str(PYTHON_ROOT)
        if environment.get("PYTHONPATH"):
            python_path += os.pathsep + environment["PYTHONPATH"]
        environment["PYTHONPATH"] = python_path

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PYTHON_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        imports = json.loads(result.stdout)

        self.assertEqual(["Controller", "ScannerProcess"], imports["explicit_imports"])
        self.assertEqual(sorted(PUBLIC_EXPORTS), imports["wildcard_exports"])
        self.assertTrue(imports["unknown_attribute_raises"])

    def test_scanner_submodule_import_does_not_load_unrelated_controller_modules(self):
        script = """
import json
import sys

import controller.scan.scanner_process

unrelated = {
    "controller.controller",
    "controller.controller_job",
    "controller.controller_persist",
    "controller.memory_monitor",
    "controller.model_builder",
    "controller.auto_queue",
    "controller.validate",
    "controller.validate.validate_process",
}
print(json.dumps({
    "scanner_loaded": "controller.scan.scanner_process" in sys.modules,
    "unrelated_loaded": sorted(unrelated.intersection(sys.modules)),
}))
"""
        environment = os.environ.copy()
        python_path = str(PYTHON_ROOT)
        if environment.get("PYTHONPATH"):
            python_path += os.pathsep + environment["PYTHONPATH"]
        environment["PYTHONPATH"] = python_path

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PYTHON_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        imports = json.loads(result.stdout)

        self.assertTrue(imports["scanner_loaded"])
        self.assertEqual([], imports["unrelated_loaded"])
