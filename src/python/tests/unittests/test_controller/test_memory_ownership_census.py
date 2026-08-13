# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from controller import Controller, ModelBuilder
from controller.ownership_census import (
    _CompactIdSet, OwnershipRoot, build_ownership_census,
    disabled_ownership_census, unavailable_ownership_census,
)
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
        builder._ModelBuilder__local_files_by_pair = {None: {"private-key": system_root}}

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
            self.assertEqual(
                {"object_count", "shallow_bytes", "graph_node_count", "graph_shallow_bytes", "graph_truncated"},
                set(owner) - {"aliases_live_model"},
            )
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
        builder._ModelBuilder__local_files_by_pair = {None: {"one": retained}}
        roots = self.controller._Controller__capture_memory_ownership_roots()
        builder._ModelBuilder__local_files_by_pair = {None: {"two": SystemFile("replacement", 1)}}

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
        self.assertFalse(census["graph_truncated"])
        self.assertEqual(2, census["owners"]["cycle"]["graph_node_count"])

    def test_structural_graph_totals_complete_when_detailed_walk_truncates_and_roots_overlap(self):
        first = SystemFile("first", 1, True)
        second = SystemFile("second", 1, True)
        shared = SystemFile("shared", 1)
        first.add_child(shared)
        second.add_child(shared)

        census = build_ownership_census(
            (OwnershipRoot("local", (first, second)), OwnershipRoot("remote", (second,))), max_objects=1
        )

        self.assertTrue(census["truncated"])
        self.assertFalse(census["graph_truncated"])
        self.assertEqual(3, census["owners"]["local"]["graph_node_count"])
        self.assertEqual(2, census["owners"]["remote"]["graph_node_count"])
        self.assertEqual(5, census["graph_visited_node_count"])
        self.assertEqual(
            sum(sys.getsizeof(value) for value in (first, second, shared)),
            census["owners"]["local"]["graph_shallow_bytes"],
        )

    def test_structural_graph_totals_cover_later_roots_when_container_hits_detailed_cap(self):
        first = SystemFile("first", 1)
        second = SystemFile("second", 1)
        first_container = object()
        second_container = object()

        census = build_ownership_census((
            OwnershipRoot("first", (first,), shallow_container_id=id(first_container), shallow_container_bytes=1),
            OwnershipRoot("second", (second,), shallow_container_id=id(second_container), shallow_container_bytes=1),
        ), max_objects=1)

        self.assertTrue(census["truncated"])
        self.assertEqual(1, census["owners"]["second"]["graph_node_count"])
        self.assertEqual(sys.getsizeof(second), census["owners"]["second"]["graph_shallow_bytes"])

    def test_structural_graph_bound_marks_only_structural_results_truncated(self):
        root = SystemFile("root", 1, True)
        root.add_child(SystemFile("child", 1))

        census = build_ownership_census(
            (OwnershipRoot("local", (root,)),), max_graph_nodes_per_root=1, max_graph_nodes=10
        )

        self.assertFalse(census["truncated"])
        self.assertTrue(census["graph_truncated"])
        owner = census["owners"]["local"]
        self.assertTrue(owner["graph_truncated"])
        self.assertEqual(1, owner["graph_node_count"])

    def test_controller_reports_overlapping_live_local_and_remote_structural_graphs(self):
        model_root = ModelFile("model", True)
        model_root.add_child(ModelFile("model-child", False))
        self.controller._Controller__model.add_file(model_root)
        shared_scan_root = SystemFile("scan", 1, True)
        shared_scan_root.add_child(SystemFile("scan-child", 1))
        builder = self.controller._Controller__model_builder
        builder._ModelBuilder__local_files_by_pair = {None: {"local": shared_scan_root}}
        builder._ModelBuilder__remote_files_by_pair = {None: {"remote": shared_scan_root}}

        census = self.controller.get_memory_ownership_census()

        self.assertFalse(census["graph_truncated"])
        self.assertEqual(2, census["owners"]["live_model_graph"]["graph_node_count"])
        self.assertEqual(2, census["owners"]["builder_local_system_file_graph"]["graph_node_count"])
        self.assertEqual(2, census["owners"]["builder_remote_system_file_graph"]["graph_node_count"])
        self.assertEqual(6, census["graph_visited_node_count"])

    def test_disabled_and_unavailable_structural_schema_is_fixed_and_numeric(self):
        for census in (disabled_ownership_census(), unavailable_ownership_census()):
            self.assertFalse(census["graph_truncated"])
            self.assertEqual(0, census["graph_visited_node_count"])
            self.assertEqual({}, census["owners"])

    def test_unavailable_census_reports_only_fixed_failure_categories(self):
        with patch("controller.controller.build_ownership_census", side_effect=MemoryError("sensitive detail")):
            census = self.controller.get_memory_ownership_census()

        self.assertFalse(census["available"])
        self.assertEqual("build_census", census["failure_stage"])
        self.assertEqual("memory_error", census["failure_kind"])
        self.assertNotIn("sensitive detail", json.dumps(census))

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
