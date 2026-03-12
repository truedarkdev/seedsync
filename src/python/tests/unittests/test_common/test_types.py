# Copyright 2026, SeedSync Contributors, All rights reserved.

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[3] / "common" / "types.py"
SPEC = importlib.util.spec_from_file_location("test_common_types_module", MODULE_PATH)
types_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(types_module)
overrides = types_module.overrides


class BaseInterface:
    def method(self):
        raise NotImplementedError()


class TestOverrides(unittest.TestCase):
    def test_overrides_accepts_existing_method_name(self):
        @overrides(BaseInterface)
        def method():
            return "ok"

        self.assertEqual("ok", method())

    def test_overrides_rejects_non_class_parameter(self):
        with self.assertRaises(AssertionError) as error:
            overrides("not-a-class")
        self.assertEqual("Overrides parameter must be a class type", str(error.exception))

    def test_overrides_rejects_missing_method(self):
        decorator = overrides(BaseInterface)

        with self.assertRaises(AssertionError) as error:
            @decorator
            def other_method():
                return "nope"

        self.assertEqual("Method does not override super class", str(error.exception))
