import json
import logging
import unittest
from wsgiref.util import setup_testing_defaults
from threading import RLock, Timer
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import patch

from webtest import TestApp

from common import BreadcrumbTraceCollector, Config, Status
from common.performance_diagnostics import PerformanceDiagnosticsCollector
from controller import Controller
from controller.controller import MODEL_LEGACY_SCOPE_ID
from controller.model_builder import ModelBuilder
from model import Model, ModelFile
from system import SystemFile
from web.handler.model_api import ModelApiHandler, ScopedModelListener, SummaryModelListener
from web.web_app import WebApp
import bottle


class TestModelApi(unittest.TestCase):
    def setUp(self):
        self.controller = Controller.__new__(Controller)
        self.controller.logger = logging.getLogger("test-model-api")
        self.model = Model()
        self.controller._Controller__model = self.model
        self.controller._Controller__model_lock = RLock()
        self.controller._Controller__reconciled_local_path_pair_ids = set()
        self.controller._Controller__reconciled_remote_path_pair_ids = set()

        config = Config()
        config.general.disable_browser_auth = True
        context = SimpleNamespace(
            logger=logging.getLogger("test-model-api-web"),
            args=SimpleNamespace(html_path=None), config=config, status=Status(),
        )
        self.controller._Controller__context = context
        self.app = WebApp(context, self.controller)
        ModelApiHandler(self.controller).add_routes(self.app)
        self.client = TestApp(self.app)

    def test_model_sse_keepalive_matches_global_stream_closure_cadence(self):
        self.assertEqual(5.0, ModelApiHandler._KEEPALIVE_INTERVAL_SECONDS)

    def test_sse_publication_uses_context_supplied_bounded_collectors_without_payload_metadata(self):
        diagnostics_enabled = [True]
        breadcrumbs_enabled = [True]
        diagnostics = PerformanceDiagnosticsCollector(lambda: diagnostics_enabled[0])
        breadcrumbs = BreadcrumbTraceCollector(lambda: breadcrumbs_enabled[0], max_entries=8)
        self.model.add_file(self._file("sample-root-name", "pair-a"))
        handler = ModelApiHandler(self.controller, diagnostics, breadcrumbs)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["QUERY_STRING"] = "limit=1"
        bottle.request.bind(environ)
        bottle.response.bind()

        stream = handler._ModelApiHandler__handle_stream("pair-a")
        self.assertIn("event: model-page", next(stream))
        stream.close()

        snapshot = diagnostics.snapshot()
        self.assertEqual(1, snapshot["counters"]["model_scoped_sse_emissions"])
        self.assertEqual(1, snapshot["durations"]["model_scoped_serialization"]["count"])
        self.assertEqual(1, snapshot["durations"]["model_scoped_sse_emission"]["count"])
        trace = breadcrumbs.snapshot()
        self.assertEqual("model_api", trace["entries"][-1]["source"])
        self.assertEqual("model_scoped_sse_published", trace["entries"][-1]["message"])
        self.assertEqual({"model_version": 1, "scope_version": 1}, trace["entries"][-1]["details"])
        self.assertNotIn("sample-root-name", str(trace))

        diagnostics_enabled[0] = False
        breadcrumbs_enabled[0] = False
        disabled_handler = ModelApiHandler(self.controller, diagnostics, breadcrumbs)
        stream = disabled_handler._ModelApiHandler__handle_summary_stream()
        self.assertIn("event: model-summary", next(stream))
        stream.close()
        self.assertEqual(0, diagnostics.snapshot()["counters"]["model_summary_sse_emissions"])
        self.assertEqual(1, len(breadcrumbs.snapshot()["entries"]))

    def test_scoped_sse_correlates_to_global_version_without_exposing_scope_identity(self):
        breadcrumbs = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        self.model.add_file(self._file("root-a", "pair-a"))
        self.model.add_file(self._file("root-b", "pair-b"))
        self.assertEqual(2, self.model.version)
        self.assertEqual(1, self.model.scope_version("pair-a"))

        ModelApiHandler(self.controller, breadcrumb_trace=breadcrumbs)._ModelApiHandler__sse(
            "scoped", "model-page", {}, self.model.scope_version("pair-a"), self.model.version,
        )

        entry = breadcrumbs.snapshot()["entries"][-1]
        self.assertEqual("model_version:2", entry["corr_id"])
        self.assertEqual("model_version:2", entry["flow_id"])
        self.assertEqual({"model_version": 2, "scope_version": 1}, entry["details"])
        self.assertNotIn("pair-a", str(entry))

    def test_scoped_sse_uses_snapshot_global_version_after_a_concurrent_advance(self):
        breadcrumbs = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        self.model.add_file(self._file("root-a", "pair-a"))
        handler = ModelApiHandler(self.controller, breadcrumb_trace=breadcrumbs)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["QUERY_STRING"] = "limit=1"
        bottle.request.bind(environ)
        bottle.response.bind()

        stream = handler._ModelApiHandler__handle_stream("pair-a")
        # The scoped page was captured at global version 1. A different scope
        # advances the live model before this generator serializes that page.
        self.model.add_file(self._file("root-b", "pair-b"))
        emitted = next(stream)
        stream.close()

        entry = breadcrumbs.snapshot()["entries"][-1]
        self.assertEqual("model_version:1", entry["corr_id"])
        self.assertEqual({"model_version": 1, "scope_version": 1}, entry["details"])
        self.assertNotIn("_global_model_version", emitted)

    def test_coalesced_scoped_reset_captures_global_version_without_transport_leak(self):
        breadcrumbs = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        root = self._file("root-a", "pair-a")
        self.model.add_file(root)
        handler = ModelApiHandler(self.controller, breadcrumb_trace=breadcrumbs)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["QUERY_STRING"] = "limit=1"
        bottle.request.bind(environ)
        bottle.response.bind()

        with patch.object(ScopedModelListener, "_MAX_IDENTITIES", 0):
            stream = handler._ModelApiHandler__handle_stream("pair-a")
            next(stream)
            changed = self._file("root-a", "pair-a")
            changed.local_size = 4
            self.model.update_file(changed)
            emitted = next(stream)
            stream.close()

        entry = breadcrumbs.snapshot()["entries"][-1]
        self.assertIn("event: model-reset", emitted)
        self.assertNotIn("_global_model_version", emitted)
        self.assertEqual("model_version:2", entry["corr_id"])
        self.assertEqual("model_version:2", entry["flow_id"])
        self.assertEqual({"model_version": 2, "scope_version": 2}, entry["details"])

    @staticmethod
    def _file(name: str, pair: str | None, directory: bool = False) -> ModelFile:
        file = ModelFile(name, directory)
        file.path_pair_id = pair
        return file

    def test_global_stream_has_no_model_listener_registration(self):
        # The model API installs explicit routes only; it must not re-add the
        # old global ModelStreamHandler to /server/stream.
        self.assertEqual([], self.app._WebApp__streaming_handlers)

    def test_pair_isolation_and_legacy_scope(self):
        self.model.add_file(self._file("one", "pair-a"))
        self.model.add_file(self._file("two", "pair-b"))
        self.model.add_file(self._file("legacy", None))

        response = self.client.get("/server/model/v1/pairs/pair-a/roots")
        data = response.json
        self.assertEqual("pair-a", data["path_pair_id"])
        self.assertEqual(["one"], [record["name"] for record in data["records"]])

        legacy = self.client.get("/server/model/v1/pairs/{}/roots".format(MODEL_LEGACY_SCOPE_ID)).json
        self.assertEqual(["legacy"], [record["name"] for record in legacy["records"]])
        self.assertEqual("legacy", legacy["records"][0]["file_id"])

    def test_child_identity_must_be_canonical_and_in_scope(self):
        other = self._file("other", "pair-b", directory=True)
        self.model.add_file(other)
        cross_scope = self.client.get(
            "/server/model/v1/pairs/pair-a/children?parent_file_id={}".format(
                quote(other.file_id, safe="")
            ),
            expect_errors=True,
        )
        self.assertEqual(400, cross_scope.status_int)
        malformed = self.client.get(
            "/server/model/v1/pairs/pair-a/children?parent_file_id=not-a-canonical-id",
            expect_errors=True,
        )
        self.assertEqual(400, malformed.status_int)

    def test_pagination_is_deterministic_and_unrelated_mutation_does_not_stale_cursor(self):
        for name in ("zeta", "Alpha", "beta"):
            self.model.add_file(self._file(name, "pair-a"))

        first = self.client.get("/server/model/v1/pairs/pair-a/roots?limit=2").json
        self.assertEqual(["Alpha", "beta"], [record["name"] for record in first["records"]])
        self.assertIsNotNone(first["next_cursor"])
        second = self.client.get(
            "/server/model/v1/pairs/pair-a/roots?limit=2&cursor={}".format(first["next_cursor"])
        ).json
        self.assertEqual(["zeta"], [record["name"] for record in second["records"]])

        self.model.add_file(self._file("later", "pair-b"))
        continued = self.client.get(
            "/server/model/v1/pairs/pair-a/roots?limit=2&cursor={}".format(first["next_cursor"])
        )
        self.assertEqual(200, continued.status_int)
        self.assertEqual(["zeta"], [record["name"] for record in continued.json["records"]])

    def test_identity_cursor_chain_survives_same_scope_sort_boundary_mutation(self):
        for name in ("a", "b", "c"):
            file = self._file(name, "pair-a")
            file.remote_size = 100
            self.model.add_file(file)
        first = self.client.get("/server/model/v1/pairs/pair-a/roots?limit=1&sort=4").json
        changed = self._file("c", "pair-a")
        changed.remote_size = 1  # would cross a visual size-sort boundary
        self.model.update_file(changed)
        continued = self.client.get(
            "/server/model/v1/pairs/pair-a/roots?limit=1&sort=4&cursor={}".format(first["next_cursor"]),
        )
        self.assertEqual(200, continued.status_int)
        self.assertEqual(["b"], [record["name"] for record in continued.json["records"]])

    def test_root_transport_order_is_identity_canonical_not_visual_sort_or_filter(self):
        for name in ("z", "a", "m"):
            file = self._file(name, "pair-a")
            file.remote_size = 100 if name == "a" else 1
            file.state = ModelFile.State.DOWNLOADING if name == "m" else ModelFile.State.DEFAULT
            self.model.add_file(file)
        name_page = self.client.get("/server/model/v1/pairs/pair-a/roots?limit=3&sort=1").json
        smart_filtered_page = self.client.get(
            "/server/model/v1/pairs/pair-a/roots?limit=3&sort=10&status=downloading&name=m"
        ).json
        self.assertEqual(
            [record["file_id"] for record in name_page["records"]],
            [record["file_id"] for record in smart_filtered_page["records"]],
        )

    def test_child_page_is_bounded_and_shallow(self):
        root = self._file("root", "pair-a", directory=True)
        for name in ("c", "a", "b"):
            child = self._file(name, "pair-a")
            root.add_child(child)
        self.model.add_file(root)

        page = self.client.get(
            "/server/model/v1/pairs/pair-a/children?parent_file_id={}&limit=2".format(
                quote(root.file_id, safe="")
            )
        ).json
        self.assertEqual(["a", "b"], [record["name"] for record in page["records"]])
        self.assertEqual([], page["records"][0]["children"])
        self.assertFalse(page["records"][0]["has_children"])

    def test_high_cardinality_child_page_does_not_copy_or_sort_full_child_list(self):
        root = self._file("root", "pair-a", directory=True)
        for number in range(500, -1, -1):
            root.add_child(self._file("child-{:03d}".format(number), "pair-a"))
        self.model.add_file(root)

        with patch.object(ModelFile, "get_children", side_effect=AssertionError("full child copy")):
            page = self.client.get(
                "/server/model/v1/pairs/pair-a/children?parent_file_id={}&limit=3".format(
                    quote(root.file_id, safe="")
                )
            ).json
        self.assertEqual(
            ["child-000", "child-001", "child-002"],
            [record["name"] for record in page["records"]],
        )
        self.assertEqual(501, page["total"])
        self.assertIsNotNone(page["next_cursor"])

    def test_high_cardinality_root_page_uses_live_iteration_not_id_copy(self):
        for number in range(350):
            self.model.add_file(self._file("root-{:03d}".format(number), "pair-a"))
        with patch.object(Model, "get_file_ids", side_effect=AssertionError("root id copy")):
            page = self.client.get("/server/model/v1/pairs/pair-a/roots?limit=3").json
        self.assertEqual(["root-000", "root-001", "root-002"], [record["name"] for record in page["records"]])
        self.assertEqual(350, page["total"])

    def test_identity_page_chain_remains_unique_through_continuous_progress_updates(self):
        for number in range(25):
            file = self._file("root-{:02d}".format(number), "pair-a")
            file.remote_size = 100
            self.model.add_file(file)
        cursor = None
        seen: list[str] = []
        while True:
            suffix = "&cursor={}".format(cursor) if cursor else ""
            page = self.client.get("/server/model/v1/pairs/pair-a/roots?limit=4" + suffix).json
            seen.extend(record["file_id"] for record in page["records"])
            # Mutating progress changes the frontend Smart/size presentation,
            # but never the immutable identity cursor boundary.
            if page["records"]:
                name = page["records"][-1]["name"]
                updated = self._file(name, "pair-a")
                updated.remote_size = 100
                updated.transferred_size = len(seen)
                updated.download_progress = min(100, len(seen))
                self.model.update_file(updated)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(25, len(seen))
        self.assertEqual(25, len(set(seen)))

    def test_atomic_initial_page_and_scoped_coalescing(self):
        self.model.add_file(self._file("one", "pair-a"))
        listener = ScopedModelListener("pair-a")
        initial = self.controller.get_model_page_and_add_listener(listener, "pair-a", 10)
        self.assertEqual(1, initial["model_version"])
        self.assertEqual(["one"], [record["name"] for record in initial["records"]])

        changed = self._file("one", "pair-a")
        changed.local_size = 4
        self.model.update_file(changed)
        self.model.update_file(changed)
        event = listener.take_next_event()
        self.assertEqual("model-invalidate", event["event"])
        self.assertEqual([changed.file_id], event["file_ids"])
        self.controller.remove_model_listener(listener)
        listener.close()

    def test_add_delete_changes_coalesce_to_scoped_invalidation(self):
        existing = self._file("existing", "pair-a")
        self.model.add_file(existing)
        listener = ScopedModelListener("pair-a")
        self.controller.get_model_page_and_add_listener(listener, "pair-a", 10)
        added = self._file("added", "pair-a")
        self.model.add_file(added)
        self.model.remove_file(existing.file_id)
        event = listener.take_next_event()
        self.assertEqual("model-invalidate", event["event"])
        self.assertEqual({added.file_id, existing.file_id}, set(event["file_ids"]))
        self.controller.remove_model_listener(listener)
        listener.close()

    def test_child_identity_maps_to_one_shallow_root_patch_and_root_removal(self):
        root = self._file("root", "pair-a", directory=True)
        child = self._file("child", "pair-a")
        root.add_child(child)
        self.model.add_file(root)
        updates = self.controller.get_model_root_updates("pair-a", [child.file_id])
        self.assertEqual([root.file_id], [record["file_id"] for record in updates["records"]])
        self.assertEqual([], updates["records"][0]["children"])
        self.assertEqual([], updates["removed_file_ids"])
        self.model.remove_file(root.file_id)
        removed = self.controller.get_model_root_updates("pair-a", [root.file_id])
        self.assertEqual([], removed["records"])
        self.assertEqual([root.file_id], removed["removed_file_ids"])

    def test_scoped_stream_update_uses_root_patch_without_a_page_rescan(self):
        root = self._file("root", "pair-a")
        self.model.add_file(root)
        handler = ModelApiHandler(self.controller)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["QUERY_STRING"] = "limit=1"
        bottle.request.bind(environ)
        bottle.response.bind()
        stream = handler._ModelApiHandler__handle_stream("pair-a")
        next(stream)  # atomic initial page
        changed = self._file("root", "pair-a")
        changed.local_size = 4
        with patch.object(self.controller, "get_model_page", side_effect=AssertionError("full page rescan")):
            self.model.update_file(changed)
            update = next(stream)
        self.assertIn("event: model-invalidate", update)
        self.assertIn('"records":[{', update)
        self.assertEqual(changed.file_id, json.loads(update.split("data: ", 1)[1])["records"][0]["file_id"])
        stream.close()

    def test_scoped_stream_prioritizes_selected_pair_before_initial_page(self):
        self.model.add_file(self._file("root", "pair-a"))
        handler = ModelApiHandler(self.controller)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["QUERY_STRING"] = "limit=1"
        bottle.request.bind(environ)
        bottle.response.bind()

        with patch.object(self.controller, "prioritize_path_pair_scan") as prioritize:
            stream = handler._ModelApiHandler__handle_stream("pair-a")
            prioritize.assert_called_once_with("pair-a")
            next(stream)
            stream.close()

    def test_listener_bounds_reset_and_cleanup(self):
        listener = ScopedModelListener("pair-a")
        for number in range(listener._MAX_IDENTITIES + 1):
            listener.model_version_changed(number + 1, "pair-a", "file-{}".format(number))
        reset = listener.take_next_event()
        self.assertEqual("model-reset", reset["event"])
        self.assertEqual("coalesced", reset["reason"])
        listener.close()
        listener.model_version_changed(999, "pair-a", "ignored")
        self.assertIsNone(listener.take_next_event())

    def test_scoped_listener_waits_until_a_matching_event(self):
        listener = ScopedModelListener("pair-a")
        timer = Timer(0.01, lambda: listener.model_version_changed(1, "pair-a", "file-a"))
        timer.start()
        try:
            self.assertTrue(listener.wait_for_event(1.0))
            self.assertEqual(["file-a"], listener.take_next_event()["file_ids"])
        finally:
            timer.join()
            listener.close()

    def test_summary_listener_waits_until_an_event(self):
        listener = SummaryModelListener()
        timer = Timer(0.01, lambda: listener.model_version_changed(1, "pair-a", "file-a"))
        timer.start()
        try:
            self.assertTrue(listener.wait_for_event(1.0))
            self.assertEqual(["pair-a"], listener.take_next_event()["path_pair_ids"])
        finally:
            timer.join()
            listener.close()

    def test_scoped_patch_boundary_caps_enriched_roots_at_transport_limit(self):
        roots = []
        for number in range(201):
            root = self._file("root-{:03d}".format(number), "pair-a")
            self.model.add_file(root)
            roots.append(root)
        listener = ScopedModelListener("pair-a")
        for number, root in enumerate(roots[:200]):
            listener.model_version_changed(number + 1, "pair-a", root.file_id)
        event = listener.take_next_event()
        self.assertEqual("model-invalidate", event["event"])
        updates = self.controller.get_model_root_updates("pair-a", event["file_ids"])
        self.assertEqual(200, len(updates["records"]))
        self.assertEqual(0, len(updates["removed_file_ids"]))

        listener = ScopedModelListener("pair-a")
        for number, root in enumerate(roots):
            listener.model_version_changed(number + 1, "pair-a", root.file_id)
        reset = listener.take_next_event()
        self.assertEqual("model-reset", reset["event"])

    def test_stream_reconnect_resets_and_cleanup_unregisters_listener(self):
        self.model.add_file(self._file("one", "pair-a"))
        handler = ModelApiHandler(self.controller)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["QUERY_STRING"] = "limit=1"
        environ["HTTP_LAST_EVENT_ID"] = "1"
        bottle.request.bind(environ)
        bottle.response.bind()

        stream = handler._ModelApiHandler__handle_stream("pair-a")
        first = next(stream)
        reset = next(stream)
        self.assertIn("event: model-page", first)
        self.assertIn("event: model-reset", reset)
        self.assertIn("replay_unavailable", reset)
        self.assertEqual(1, len(self.model._Model__listeners))
        stream.close()
        self.assertEqual([], self.model._Model__listeners)

    def test_stream_emits_injectable_idle_keepalive(self):
        self.model.add_file(self._file("one", "pair-a"))
        handler = ModelApiHandler(self.controller)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["QUERY_STRING"] = "limit=1"
        bottle.request.bind(environ)
        bottle.response.bind()

        with patch.object(ModelApiHandler, "_KEEPALIVE_INTERVAL_SECONDS", 0):
            stream = handler._ModelApiHandler__handle_stream("pair-a")
            self.assertIn("event: model-page", next(stream))
            self.assertEqual(": keepalive\n\n", next(stream))
            stream.close()

    def test_summary_stream_is_compact_coalesced_and_cleans_up(self):
        root = self._file("root", "pair-a", directory=True)
        root.add_child(self._file("not-in-summary", "pair-a"))
        self.model.add_file(root)
        handler = ModelApiHandler(self.controller)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        bottle.request.bind(environ)
        bottle.response.bind()

        with patch.object(ModelApiHandler, "_SUMMARY_MIN_INTERVAL_SECONDS", 0):
            stream = handler._ModelApiHandler__handle_summary_stream()
            initial = next(stream)
            self.assertIn("event: model-summary", initial)
            self.assertNotIn("not-in-summary", initial)
            updated = self._file("root", "pair-a", directory=True)
            self.model.update_file(updated)
            update = next(stream)
            self.assertIn("event: model-summary", update)
            self.assertNotIn("event: model-summary-update", update)
            self.assertNotIn('"records"', update)
            self.assertEqual(1, len(self.model._Model__listeners))
            stream.close()
            self.assertEqual([], self.model._Model__listeners)

    def test_summary_stream_emits_injectable_keepalive(self):
        handler = ModelApiHandler(self.controller)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        bottle.request.bind(environ)
        bottle.response.bind()
        with patch.object(ModelApiHandler, "_KEEPALIVE_INTERVAL_SECONDS", 0):
            stream = handler._ModelApiHandler__handle_summary_stream()
            self.assertIn("event: model-summary", next(stream))
            self.assertEqual(": keepalive\n\n", next(stream))
            stream.close()

    def test_summary_cache_bounds_burst_recomputation(self):
        self.model.add_file(self._file("one", "pair-a"))
        initial = self.controller.get_model_summary()
        with patch.object(Model, "iter_files", side_effect=AssertionError("summary rescan")):
            for _ in range(10):
                self.assertIs(initial, self.controller.get_model_summary(max_age_seconds=0.5))

    def test_summary_stream_update_rebuilds_version_keyed_cache_after_one_mutation(self):
        initial_file = self._file("one", "pair-a")
        self.model.add_file(initial_file)
        cached = self.controller.get_model_summary()
        handler = ModelApiHandler(self.controller)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        bottle.request.bind(environ)
        bottle.response.bind()
        with patch.object(ModelApiHandler, "_SUMMARY_MIN_INTERVAL_SECONDS", 0):
            stream = handler._ModelApiHandler__handle_summary_stream()
            initial = json.loads(next(stream).split("data: ", 1)[1])
            changed = self._file("one", "pair-a")
            changed.state = ModelFile.State.DOWNLOADING
            self.model.update_file(changed)
            immediate = self.controller.get_model_summary(max_age_seconds=0.5)
            self.assertGreater(immediate["model_version"], cached["model_version"])
            update = json.loads(next(stream).split("data: ", 1)[1])
            self.assertGreater(update["model_version"], initial["model_version"])
            self.assertEqual(1, update["path_pairs"][0]["active_count"])
            stream.close()

    def test_invalid_cursor_is_explicit_and_does_not_register_listener(self):
        response = self.client.get(
            "/server/model/v1/pairs/pair-a/roots?cursor=malformed", expect_errors=True
        )
        self.assertEqual(400, response.status_int)
        self.assertEqual([], self.model._Model__listeners)

    def test_cursor_from_another_scope_requires_recoverable_reset(self):
        self.model.add_file(self._file("a", "pair-a"))
        self.model.add_file(self._file("b", "pair-a"))
        self.model.add_file(self._file("c", "pair-b"))
        self.model.add_file(self._file("d", "pair-b"))
        cursor = self.client.get("/server/model/v1/pairs/pair-a/roots?limit=1").json["next_cursor"]
        response = self.client.get(
            "/server/model/v1/pairs/pair-b/roots?limit=1&cursor={}".format(cursor),
            expect_errors=True,
        )
        self.assertEqual(409, response.status_int)
        self.assertEqual("cursor_reset_required", response.json["error"])

    def test_summary_has_no_file_records_or_children(self):
        root = self._file("root", "pair-a", directory=True)
        root.add_child(self._file("hidden-child", "pair-a"))
        root.path_pair_name = "Pair A"
        root.remote_size = 100
        root.local_size = 20
        root.transferred_size = 40
        root.downloading_speed = 5
        root.eta = 12
        root.state = ModelFile.State.DOWNLOADING
        self.model.add_file(root)

        summary = self.client.get("/server/model/v1/summary").json
        encoded = json.dumps(summary)
        self.assertNotIn("children", encoded)
        self.assertNotIn("hidden-child", encoded)
        self.assertEqual(1, summary["path_pairs"][0]["root_count"])
        path_pair = summary["path_pairs"][0]
        self.assertIn("transferred_size", path_pair)
        self.assertIn("downloading_speed", path_pair)
        self.assertIn("remaining_bytes", path_pair)
        self.assertIn("active_eta_seconds_max", path_pair)
        self.assertEqual("Pair A", path_pair["path_pair_name"])
        self.assertEqual(100, path_pair["remote_size"])
        self.assertEqual(40, path_pair["local_size"])
        self.assertEqual(40, path_pair["transferred_size"])
        self.assertEqual(5, path_pair["downloading_speed"])
        self.assertEqual(60, path_pair["remaining_bytes"])
        self.assertEqual(12, path_pair["active_eta_seconds_max"])
        self.assertEqual(1, path_pair["active_count"])

    def test_summary_exposes_scan_owned_local_inventory_without_walking_the_model_tree(self):
        root = SystemFile("sample-directory", 0, is_dir=True)
        root.path_pair_id = "pair-a"
        nested = SystemFile("nested", 0, is_dir=True)
        nested.add_child(SystemFile("first-file", 7))
        root.add_child(nested)
        standalone = SystemFile("second-file", 11)
        standalone.path_pair_id = "pair-a"
        builder = ModelBuilder()
        builder.set_local_files([root, standalone])
        builder.record_local_inventory_completion({"pair-a"})
        self.controller._Controller__model_builder = builder

        summary = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual(2, summary["local_library_file_count"])
        self.assertEqual(18, summary["local_library_size"])
        self.assertEqual("up_to_date", summary["local_library_state"])

        builder.observe_local_scan_result({"pair-a"}, set(), set(), False)
        scanning = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual(2, scanning["local_library_file_count"])
        self.assertEqual(18, scanning["local_library_size"])
        self.assertEqual("scanning", scanning["local_library_state"])

        builder.observe_local_scan_result({"pair-a"}, set(), {"pair-a"}, True)
        stale = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual("stale", stale["local_library_state"])

    def test_identityless_multi_pair_local_scan_failure_marks_every_configured_scope_stale(self):
        builder = ModelBuilder()
        for pair_id, size in (("pair-a", 7), ("pair-b", 11)):
            file = SystemFile("file-{}".format(pair_id), size)
            file.path_pair_id = pair_id
            builder.set_local_files(list(builder.local_source_roots_snapshot()) + [file])
        builder.record_local_inventory_completion({"pair-a", "pair-b"})

        builder.observe_local_scan_result({None}, set(), set(), True, {"pair-a", "pair-b"})
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("stale", inventory["pair-a"].state)
        self.assertEqual("stale", inventory["pair-b"].state)
        self.assertEqual(7, inventory["pair-a"].size)
        self.assertEqual(11, inventory["pair-b"].size)

    def test_summary_bytes_and_speed_match_legacy_path_pair_card(self):
        downloading = self._file("down", "pair-a")
        downloading.state = ModelFile.State.DOWNLOADING
        downloading.remote_size = 100
        downloading.transferred_size = None
        downloading.local_size = 35
        downloading.downloading_speed = 7
        complete = self._file("complete", "pair-a")
        complete.state = ModelFile.State.DOWNLOADED
        complete.remote_size = 10
        complete.transferred_size = 99  # clamped to remote size
        zero_remote = self._file("zero", "pair-a")
        zero_remote.state = ModelFile.State.EXTRACTED
        zero_remote.remote_size = 0
        zero_remote.local_size = 999
        zero_remote.downloading_speed = 100
        queued = self._file("queued", "pair-a")
        queued.state = ModelFile.State.QUEUED
        queued.remote_size = 0
        for file in (downloading, complete, zero_remote, queued):
            self.model.add_file(file)
        summary = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual(110, summary["remote_size"])
        self.assertEqual(45, summary["transferred_size"])
        self.assertEqual(45, summary["local_size"])
        self.assertEqual(7, summary["downloading_speed"])
        self.assertEqual(1, summary["active_count"])
        self.assertEqual(1, summary["queued_count"])
        self.assertEqual(2, summary["completed_count"])

    def test_summary_card_counts_and_visible_states_match_view_contract(self):
        downloading = self._file("downloading", "pair-a")
        downloading.state = ModelFile.State.DOWNLOADING
        extracting = self._file("extracting", "pair-a")
        extracting.state = ModelFile.State.EXTRACTING
        downloaded = self._file("downloaded", "pair-a")
        downloaded.state = ModelFile.State.DOWNLOADED
        extracted = self._file("extracted", "pair-a")
        extracted.state = ModelFile.State.EXTRACTED
        validated = self._file("validated", "pair-a")
        validated.state = ModelFile.State.VALIDATED
        stopped = self._file("stopped", "pair-a")
        stopped.remote_size = 100
        stopped.transferred_size = 1
        stopped.remote_has_transferable_content = True
        local_only = self._file("local", "pair-a")
        local_only.local_present = True
        for file in (downloading, extracting, downloaded, extracted, validated, stopped, local_only):
            self.model.add_file(file)
        summary = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual(1, summary["active_count"])
        self.assertEqual(2, summary["completed_count"])
        self.assertEqual(1, summary["visible_state_counts"]["stopped"])
        self.assertEqual(1, summary["visible_state_counts"]["local_only"])
