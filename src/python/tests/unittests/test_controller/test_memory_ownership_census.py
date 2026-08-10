# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from controller import Controller, ModelBuilder
from controller.ownership_census import _CompactIdSet, OwnershipRoot, build_ownership_census
from model import Model, ModelFile
from system import SystemFile


class TestMemoryOwnershipCensus(unittest.TestCase):
    def setUp(self):
        self.controller = Controller.__new__(Controller)
        self.controller._Controller__context = SimpleNamespace(
            config=SimpleNamespace(general=SimpleNamespace(performance_diagnostics_enabled=True))
        )
        self.controller._Controller__work_state_lock = threading.RLock()
        self.controller._Controller__model_lock = threading.RLock()
        self.controller._Controller__model = Model()
        self.controller._Controller__model_builder = ModelBuilder()
        self.controller._Controller__active_downloading_file_names = []
        self.controller._Controller__active_extracting_file_names = []
        self.controller._Controller__pending_completion_file_names = set()
        self.controller._Controller__pending_extract_file_ids = set()
        self.controller._Controller__pending_validation_file_ids = set()
        self.controller._Controller__move_retry_due = {}
        self.controller._Controller__deferred_move_file_ids = set()
        self.controller._Controller__malformed_status_only_file_ids = set()
        self.controller._Controller__pending_auto_purge_file_ids = set()

    def test_enabled_census_is_numeric_safe_and_dedupes_cached_live_model(self):
        root = ModelFile("sensitive-model-name", True)
        root.add_child(ModelFile("nested-secret", False))
        self.controller._Controller__model.add_file(root)
        builder = self.controller._Controller__model_builder
        builder._ModelBuilder__cached_model = self.controller._Controller__model
        system_root = SystemFile("/private/path", 1, True)
        system_child = SystemFile("credential-value", 1, True)
        system_root.add_child(system_child)
        system_child.add_child(system_root)
        builder._ModelBuilder__local_files = {"private-key": system_root}

        census = self.controller.get_memory_ownership_census()

        self.assertTrue(census["enabled"])
        self.assertFalse(census["truncated"])
        self.assertGreater(census["visited_object_count"], 0)
        self.assertGreater(census["total_shallow_bytes"], 0)
        cached = census["owners"]["builder_cached_model_graph"]
        self.assertTrue(cached["aliases_live_model"])
        self.assertEqual(0, cached["object_count"])
        response_text = json.dumps(census)
        for sensitive in ("sensitive-model-name", "nested-secret", "/private/path", "credential-value", "private-key"):
            self.assertNotIn(sensitive, response_text)
        for owner in census["owners"].values():
            self.assertTrue(all(isinstance(value, (int, bool)) for value in owner.values()))

    def test_disabled_mode_returns_before_any_model_walk(self):
        self.controller._Controller__context.config.general.performance_diagnostics_enabled = False
        self.controller._Controller__model = MagicMock()

        census = self.controller.get_memory_ownership_census()

        self.assertFalse(census["enabled"])
        self.controller._Controller__model.iter_files.assert_not_called()

    def test_snapshot_survives_builder_container_replacement(self):
        builder = self.controller._Controller__model_builder
        retained = SystemFile("retained", 1)
        builder._ModelBuilder__local_files = {"one": retained}
        roots = self.controller._Controller__capture_memory_ownership_roots()
        builder._ModelBuilder__local_files = {"two": SystemFile("replacement", 1)}

        census = build_ownership_census(roots)

        self.assertGreater(census["owners"]["builder_local_system_file_graph"]["object_count"], 0)

    def test_truncation_is_bounded_and_cycle_safe(self):
        root = SystemFile("root", 1, True)
        child = SystemFile("child", 1, True)
        root.add_child(child)
        child.add_child(root)

        census = build_ownership_census((OwnershipRoot("cycle", (root,)),), max_objects=2)

        self.assertTrue(census["truncated"])
        self.assertLessEqual(census["visited_object_count"], 2)

    def test_compact_identity_tracking_is_exact_and_bounded(self):
        seen = _CompactIdSet()
        values = [object() for _ in range(100_000)]

        for value in values:
            self.assertTrue(seen.add_if_absent(id(value)))
        for value in values:
            self.assertFalse(seen.add_if_absent(id(value)))

        self.assertEqual(len(values), len(seen))
        self.assertLessEqual(seen.storage_bytes, 32 * len(values))
        seen.release()
        self.assertEqual(0, len(seen))
        self.assertLessEqual(seen.storage_bytes, 8192)
