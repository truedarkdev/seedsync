import json
import logging
import unittest
from wsgiref.util import setup_testing_defaults
from threading import RLock, Timer
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import MagicMock, patch

from webtest import TestApp

from common import BreadcrumbTraceCollector, Config, Status
from common.performance_diagnostics import PerformanceDiagnosticsCollector
from controller import Controller
from controller.controller import MODEL_LEGACY_SCOPE_ID
from controller.model_builder import ModelBuilder
from model import ActiveProgressOverlay, Model, ModelFile
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

    def test_scoped_stream_breadcrumb_is_opt_in_and_identity_free(self):
        page = {"records": [{"private": "record"}] * 9, "model_version": 4, "next_cursor": "private"}
        disabled = BreadcrumbTraceCollector(lambda: True, policy={"default": "off"})
        self.controller._Controller__context.breadcrumb_trace = disabled
        with patch("controller.controller.opaque_trace_correlation") as correlation:
            self.controller.record_scoped_model_stream_breadcrumb("atomic_registered", "private-pair", page)
        correlation.assert_not_called()
        self.assertEqual([], disabled.snapshot()["entries"])

        enabled = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model_stream": "debug"}},
        )
        self.controller._Controller__context.breadcrumb_trace = enabled
        self.controller.record_scoped_model_stream_breadcrumb("atomic_registered", "private-pair", page)
        self.controller.record_scoped_model_stream_breadcrumb("initial_page_emitted", "private-pair", page)
        entries = enabled.snapshot()["entries"]
        self.assertEqual(["scoped_stream_atomic_registered", "scoped_stream_initial_page_emitted"],
                         [entry["message"] for entry in entries])
        self.assertEqual({"phase", "scope_kind", "record_count_bucket", "model_version", "next_page"},
                         set(entries[0]["details"]))
        self.assertEqual("5+", entries[0]["details"]["record_count_bucket"])
        self.assertNotIn("private-pair", str(entries))
        self.assertNotIn("private", str(entries))

    def test_scoped_stream_records_registration_before_initial_page_emission(self):
        self.model.add_file(self._file("root", "pair-a"))
        events = []
        self.controller.record_scoped_model_stream_breadcrumb = lambda phase, scope, page: events.append(phase)
        handler = ModelApiHandler(self.controller)
        stream = handler._ModelApiHandler__handle_stream("pair-a")
        self.assertEqual(["atomic_registered"], events)
        # Advancing to the first page executes the pre-yield handoff trace;
        # no second generator resume is needed to observe it.
        self.assertIn("event: model-page", next(stream))
        self.assertEqual(["atomic_registered", "initial_page_emitted"], events)
        stream.close()

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

    def test_disabled_model_api_category_skips_breadcrumb_payload_construction(self):
        class FormattingInt(int):
            def __new__(cls, value):
                instance = super().__new__(cls, value)
                instance.format_calls = 0
                return instance

            def __format__(self, format_spec):
                self.format_calls += 1
                return super().__format__(format_spec)

        breadcrumbs = BreadcrumbTraceCollector(
            lambda: True, max_entries=8, policy={"rules": {"model_api": "off"}},
        )
        global_version = FormattingInt(2)
        with patch.object(breadcrumbs, "record", wraps=breadcrumbs.record) as record:
            rendered = ModelApiHandler(self.controller, breadcrumb_trace=breadcrumbs)._ModelApiHandler__sse(
                "scoped", "model-page", {}, 1, global_version,
            )

        self.assertIn("event: model-page", rendered)
        self.assertEqual(0, global_version.format_calls)
        self.assertEqual(0, record.call_count)
        self.assertEqual([], breadcrumbs.snapshot()["entries"])

    def test_disabled_model_api_info_level_skips_breadcrumb_payload_construction(self):
        class FormattingInt(int):
            def __new__(cls, value):
                instance = super().__new__(cls, value)
                instance.format_calls = 0
                return instance

            def __format__(self, format_spec):
                self.format_calls += 1
                return super().__format__(format_spec)

        breadcrumbs = BreadcrumbTraceCollector(
            lambda: True, max_entries=8, policy={"rules": {"model_api": "error"}},
        )
        global_version = FormattingInt(2)
        with patch.object(breadcrumbs, "record", wraps=breadcrumbs.record) as record:
            rendered = ModelApiHandler(self.controller, breadcrumb_trace=breadcrumbs)._ModelApiHandler__sse(
                "scoped", "model-page", {}, 1, global_version,
            )

        self.assertIn("event: model-page", rendered)
        self.assertEqual(0, global_version.format_calls)
        self.assertEqual(0, record.call_count)
        self.assertEqual([], breadcrumbs.snapshot()["entries"])

    def test_model_progress_is_independent_and_uses_explicit_scope_and_event(self):
        breadcrumbs = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=16,
            policy={"default": "off", "rules": {"model.progress": "debug", "model_api": "off"}},
        )
        handler = ModelApiHandler(self.controller, breadcrumb_trace=breadcrumbs)
        handler._ModelApiHandler__sse(
            "scoped", "model-invalidate", {"path_pair_id": "must-not-be-read"},
            3, 8, "known-pair",
        )
        handler._ModelApiHandler__sse(
            "scoped", "model-reset", {}, 4, 9, "known-pair",
        )
        handler._ModelApiHandler__sse(
            "summary", "model-summary", {"path_pair_id": "must-not-be-read"}, 10, None,
        )

        progress_entries = breadcrumbs.snapshot(category="model.progress")["entries"]
        self.assertTrue(progress_entries)
        self.assertTrue(all(entry["category"] == "model.progress" for entry in progress_entries))
        events = {entry["details"].get("event") for entry in progress_entries}
        self.assertIn("model-invalidate", events)
        self.assertIn("model-reset", events)
        self.assertIn("model-summary", events)
        invalidate_sse = next(
            entry for entry in progress_entries
            if entry["stage"] == "root_progress_sse"
            and entry["details"].get("event") == "model-invalidate"
        )
        reset_serialization = next(
            entry for entry in progress_entries
            if entry["stage"] == "root_progress_serialization_end"
            and entry["details"].get("event") == "model-reset"
        )
        self.assertEqual(8, invalidate_sse["details"]["model_version"])
        self.assertEqual(3, invalidate_sse["details"]["scope_version"])
        self.assertEqual("scoped_sse", invalidate_sse["details"]["representation"])
        self.assertEqual(9, reset_serialization["details"]["model_version"])
        self.assertEqual(4, reset_serialization["details"]["scope_version"])
        self.assertNotIn("must-not-be-read", str(progress_entries))
        self.assertEqual([], breadcrumbs.snapshot(category="model_api")["entries"])

    def test_sse_serialization_timer_starts_after_start_admission(self):
        breadcrumbs = BreadcrumbTraceCollector(lambda: True, max_entries=8)
        calls = []

        class FakeProgressTrace:
            def enabled(self, level):
                return True

            def record(self, identity, stage, details):
                calls.append((stage, monotonic.call_count, dict(details)))
                return True

        handler = ModelApiHandler(self.controller, breadcrumb_trace=breadcrumbs)
        with patch("web.handler.model_api.root_progress_tracer", return_value=FakeProgressTrace()), \
                patch("web.handler.model_api.time.monotonic_ns", side_effect=[100, 101, 102, 103]) as monotonic:
            rendered = handler._ModelApiHandler__sse(
                "scoped", "model-page", {}, 3, 8, "known-pair",
            )

        self.assertIn("event: model-page", rendered)
        self.assertEqual("serialization_start", calls[0][0])
        self.assertEqual(0, calls[0][1])
        serialization_end = next(call for call in calls if call[0] == "serialization_end")
        self.assertEqual(0, serialization_end[2]["duration_ms"])
        self.assertEqual(8, serialization_end[2]["model_version"])
        self.assertEqual(3, serialization_end[2]["scope_version"])

    def test_json_scoped_serialization_uses_global_and_scope_versions(self):
        breadcrumbs = BreadcrumbTraceCollector(
            lambda: True,
            max_entries=8,
            policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        handler = ModelApiHandler(self.controller, breadcrumb_trace=breadcrumbs)
        response = handler._ModelApiHandler__json_response(
            {
                "model_version": 3,
                "_global_model_version": 8,
                "total": 0,
                "records": [],
            },
            scope_id="known-pair",
        )

        body = response.body.decode("utf-8") if isinstance(response.body, bytes) else response.body
        self.assertNotIn("_global_model_version", body)
        entries = breadcrumbs.snapshot(category="model.progress")["entries"]
        self.assertEqual(2, len(entries))
        self.assertEqual(8, entries[0]["details"]["model_version"])
        self.assertEqual(3, entries[0]["details"]["scope_version"])
        self.assertEqual(8, entries[1]["details"]["model_version"])
        self.assertEqual(3, entries[1]["details"]["scope_version"])

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

    def test_scoped_listener_does_not_publish_before_atomic_origin_attachment(self):
        listener = ScopedModelListener("pair-a")
        # This models an interleaving immediately after Model's preserved
        # legacy callback. It cannot expose an event without its origin.
        listener.model_version_changed(4, "pair-a", "file-a")
        self.assertIsNone(listener.take_next_event())
        listener.model_version_published(4, 12, "pair-a", "file-a")
        event = listener.take_next_event()
        self.assertEqual(4, event["model_version"])
        self.assertEqual(12, event["_origin_global_model_version"])

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

    def test_delayed_scoped_event_keeps_origin_lineage_not_later_cross_scope_version(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        self.controller._Controller__context.breadcrumb_trace = trace
        a_root = self._file("a-root", "pair-a")
        b_root = self._file("b-root", "pair-b")
        self.model.add_file(a_root)
        self.model.add_file(b_root)
        handler = ModelApiHandler(self.controller, breadcrumb_trace=trace)
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["QUERY_STRING"] = "limit=1"
        bottle.request.bind(environ)
        bottle.response.bind()
        stream = handler._ModelApiHandler__handle_stream("pair-a")
        next(stream)

        a_changed = self._file("a-root", "pair-a")
        a_changed.local_size = 1
        self.model.update_file(a_changed)
        a_version = self.model.version
        trace.record_progress_lineage(
            "lftp-poll:0123456789abcdef", "model_mutation",
            {"outcome": "mutated", "model_version": a_version},
        )
        b_changed = self._file("b-root", "pair-b")
        b_changed.local_size = 2
        self.model.update_file(b_changed)
        b_version = self.model.version
        trace.record_progress_lineage(
            "lftp-poll:fedcba9876543210", "model_mutation",
            {"outcome": "mutated", "model_version": b_version},
        )

        next(stream)
        spans = trace.snapshot()["progress_lineage"]["spans"]
        a_span = next(span for span in spans if span["correlation"] == "lftp-poll:0123456789abcdef")
        b_span = next(span for span in spans if span["correlation"] == "lftp-poll:fedcba9876543210")
        self.assertEqual("scoped_stream_emit", a_span["steps"][-1]["phase"])
        self.assertEqual("model_mutation", b_span["steps"][-1]["phase"])
        stream.close()

    def test_large_multi_scope_delayed_events_link_early_and_late_range_versions(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        correlation = "lftp-poll:0123456789abcdef"
        for phase in ("status_submit", "status_start", "status_finish", "status_consume", "updater_decision"):
            trace.record_progress_lineage(correlation, phase, {"outcome": "ok", "source": "fresh_healthy"})
        listeners = {"pair-a": ScopedModelListener("pair-a"), "pair-b": ScopedModelListener("pair-b")}
        for global_version in range(1, 41):
            scope = "pair-a" if global_version % 2 else "pair-b"
            scope_version = (global_version + 1) // 2 if scope == "pair-a" else global_version // 2
            listeners[scope].model_version_published(
                scope_version, global_version, scope, "file-{}".format(global_version),
            )
            trace.record_progress_lineage(
                correlation, "model_mutation",
                {"outcome": "mutated", "model_version": global_version, "scope_version": scope_version},
            )
        handler = ModelApiHandler(self.controller, breadcrumb_trace=trace)
        for scope, listener in listeners.items():
            event = listener.take_next_event()
            handler._ModelApiHandler__sse(
                "scoped", "model-invalidate", event, event["model_version"], 40, scope,
                event["_origin_global_model_version"],
            )
        span = trace.snapshot()["progress_lineage"]["spans"][0]
        self.assertEqual(1, next(step for step in span["steps"] if step["phase"] == "model_mutation")["details"]["model_version_first"])
        self.assertEqual(40, next(step for step in span["steps"] if step["phase"] == "model_mutation")["details"]["model_version_last"])
        emitted = next(step for step in span["steps"] if step["phase"] == "scoped_stream_emit")
        self.assertEqual("2-4", emitted["details"]["scoped_stream_count_bucket"])
        self.assertEqual(
            ["status_submit", "status_start", "status_finish", "status_consume", "updater_decision"],
            [step["phase"] for step in span["steps"][:5]],
        )

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
            listener.model_version_published(number + 1, number + 1, "pair-a", "file-{}".format(number))
        reset = listener.take_next_event()
        self.assertEqual("model-reset", reset["event"])
        self.assertEqual("coalesced", reset["reason"])
        listener.close()
        listener.model_version_published(999, 999, "pair-a", "ignored")
        self.assertIsNone(listener.take_next_event())

    def test_coalesced_reset_retains_origin_for_lineage_but_synthetic_reset_does_not(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        listener = ScopedModelListener("pair-a")
        for number in range(listener._MAX_IDENTITIES + 1):
            listener.model_version_published(number + 1, number + 1, "pair-a", "file-{}".format(number))
        event = listener.take_next_event()
        self.assertEqual(listener._MAX_IDENTITIES + 1, event["_origin_global_model_version"])
        trace.record_progress_lineage(
            "lftp-poll:0123456789abcdef", "model_mutation",
            {"outcome": "mutated", "model_version": event["_origin_global_model_version"]},
        )
        handler = ModelApiHandler(self.controller, breadcrumb_trace=trace)
        handler._ModelApiHandler__sse(
            "scoped", "model-reset", event, event["model_version"], 999, "pair-a",
            event["_origin_global_model_version"],
        )
        span = trace.snapshot()["progress_lineage"]["spans"][0]
        self.assertEqual("scoped_stream_emit", span["steps"][-1]["phase"])
        # Reconnect-generated resets have no listener origin and are refused.
        handler._ModelApiHandler__sse(
            "scoped", "model-reset", {"model_version": 1}, 1, 999, "pair-a", None,
        )
        self.assertEqual(2, len(span["steps"]))

    def test_scoped_listener_waits_until_a_matching_event(self):
        listener = ScopedModelListener("pair-a")
        timer = Timer(0.01, lambda: listener.model_version_published(1, 1, "pair-a", "file-a"))
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
            listener.model_version_published(number + 1, number + 1, "pair-a", root.file_id)
        event = listener.take_next_event()
        self.assertEqual("model-invalidate", event["event"])
        updates = self.controller.get_model_root_updates("pair-a", event["file_ids"])
        self.assertEqual(200, len(updates["records"]))
        self.assertEqual(0, len(updates["removed_file_ids"]))

        listener = ScopedModelListener("pair-a")
        for number, root in enumerate(roots):
            listener.model_version_published(number + 1, number + 1, "pair-a", root.file_id)
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

        builder.observe_local_scan_result({"pair-a"}, set(), {"pair-a"}, True,
                                          recoverable_failure_path_pair_ids={"pair-a"})
        retrying = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual(2, retrying["local_library_file_count"])
        self.assertEqual(18, retrying["local_library_size"])
        self.assertEqual("scanning", retrying["local_library_state"])

        # Failed results without recoverable retry ownership remain stale.
        builder.observe_local_scan_result(
            {"pair-a"}, set(), {"pair-a"}, True,
            terminal_failure_path_pair_ids={"pair-a"},
        )
        stale = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual("stale", stale["local_library_state"])

        # A retry/progress observation cannot turn failed evidence into a
        # healthy-looking scan; only an authoritative completion can.
        builder.observe_local_scan_result({"pair-a"}, set(), set(), False)
        stale_after_progress = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual("stale", stale_after_progress["local_library_state"])
        builder.record_local_inventory_completion({"pair-a"})
        recovered = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual("up_to_date", recovered["local_library_state"])

    def test_concrete_multi_pair_local_scan_failure_marks_each_scope_stale(self):
        builder = ModelBuilder()
        for pair_id, size in (("pair-a", 7), ("pair-b", 11)):
            file = SystemFile("file-{}".format(pair_id), size)
            file.path_pair_id = pair_id
            builder.set_local_files(list(builder.local_source_roots_snapshot()) + [file])
        builder.record_local_inventory_completion({"pair-a", "pair-b"})

        builder.observe_local_scan_result(
            {"pair-a", "pair-b"}, set(), {"pair-a", "pair-b"}, True,
            {"pair-a", "pair-b"},
        )
        _, inventory = builder.local_library_inventory_snapshot()
        self.assertEqual("stale", inventory["pair-a"].state)
        self.assertEqual("stale", inventory["pair-b"].state)
        self.assertEqual(7, inventory["pair-a"].size)
        self.assertEqual(11, inventory["pair-b"].size)

    def test_concrete_mixed_recoverable_failure_marks_only_uncompleted_scopes_scanning(self):
        builder = ModelBuilder()
        for pair_id, size in (("pair-a", 7), ("pair-b", 11)):
            file = SystemFile("file-{}".format(pair_id), size)
            file.path_pair_id = pair_id
            builder.set_local_files(list(builder.local_source_roots_snapshot()) + [file])
        builder.record_local_inventory_completion({"pair-a", "pair-b"})

        # Accumulator-normalized evidence completed pair-a and retained a
        # recoverable retry classification for pair-b.
        builder.observe_local_scan_result(
            {"pair-a"}, {"pair-a"}, {"pair-b"}, False, {"pair-a", "pair-b"}, {"pair-b"},
        )
        builder.record_local_inventory_completion({"pair-a"})
        _, inventory = builder.local_library_inventory_snapshot()

        self.assertEqual((1, 7, "up_to_date"), (
            inventory["pair-a"].file_count, inventory["pair-a"].size, inventory["pair-a"].state,
        ))
        self.assertEqual((1, 11, "scanning"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))
        self.assertNotIn(None, inventory)

    def test_concrete_mixed_terminal_failure_marks_only_uncompleted_scopes_stale(self):
        builder = ModelBuilder()
        for pair_id, size in (("pair-a", 7), ("pair-b", 11)):
            file = SystemFile("file-{}".format(pair_id), size)
            file.path_pair_id = pair_id
            builder.set_local_files(list(builder.local_source_roots_snapshot()) + [file])
        builder.record_local_inventory_completion({"pair-a", "pair-b"})

        builder.observe_local_scan_result(
            {"pair-a"}, {"pair-a"}, {"pair-b"}, False, {"pair-a", "pair-b"}, set(),
            {"pair-b"},
        )
        builder.record_local_inventory_completion({"pair-a"})
        _, inventory = builder.local_library_inventory_snapshot()

        self.assertEqual("up_to_date", inventory["pair-a"].state)
        self.assertEqual((1, 11, "stale"), (
            inventory["pair-b"].file_count, inventory["pair-b"].size, inventory["pair-b"].state,
        ))
        self.assertNotIn(None, inventory)

    def test_summary_bytes_and_speed_match_legacy_path_pair_card(self):
        downloading = self._file("down", "pair-a")
        downloading.state = ModelFile.State.DOWNLOADING
        downloading.remote_size = 100
        downloading.transferred_size = None
        downloading.local_size = 35
        downloading.display_size_total = 160
        downloading.display_transferred_size = 70
        downloading.downloading_speed = 7
        complete = self._file("complete", "pair-a")
        complete.state = ModelFile.State.DOWNLOADED
        complete.remote_size = 10
        complete.transferred_size = 99  # clamped to remote size
        complete.complete_local_coverage = True
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
        self.assertEqual(170, summary["remote_size"])
        self.assertEqual(80, summary["transferred_size"])
        self.assertEqual(80, summary["local_size"])
        self.assertEqual(7, summary["downloading_speed"])
        self.assertEqual(1, summary["active_count"])
        self.assertEqual(1, summary["queued_count"])
        self.assertEqual(2, summary["completed_count"])

    def test_page_record_keeps_raw_and_display_progress_separate(self):
        file = self._file("sample", "pair-a")
        file.remote_size = 40
        file.transferred_size = 10
        file.display_size_total = 100
        file.display_transferred_size = 70

        record = Controller._model_file_page_record(file)

        self.assertEqual((40, 10), (record["remote_size"], record["transferred_size"]))
        self.assertEqual((100, 70), (
            record["display_size_total"], record["display_transferred_size"],
        ))
        self.assertFalse(record["explicitly_stopped"])
        self.assertFalse(record["complete_local_coverage"])

    def test_scoped_page_resolves_immutable_active_progress_overlay(self):
        file = self._file("sample", "pair-a")
        file.remote_size = 100
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.model.add_file(file)
        self.model.replace_active_progress_overlays(
            {file.file_id: ActiveProgressOverlay(25, 25, 10, 8)}, {file.file_id},
        )

        page = self.client.get("/server/model/v1/pairs/pair-a/roots").json

        self.assertEqual("downloading", page["records"][0]["state"])
        self.assertEqual(25, page["records"][0]["download_progress"])
        self.assertEqual(25, page["records"][0]["transferred_size"])
        self.assertTrue(page["records"][0]["is_stoppable"])
        self.assertIsNone(self.model.get_file(file.file_id).transferred_size)
        summary = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual(25, summary["transferred_size"])
        self.assertEqual(10, summary["downloading_speed"])
        self.assertEqual(1, summary["active_count"])
        listener = MagicMock()
        self.model.add_listener(listener)
        before_clear_version = self.model.version
        self.model.replace_active_progress_overlays({}, {file.file_id})
        self.assertIsNone(self.model.active_progress_overlay(file.file_id))
        self.assertEqual(before_clear_version + 1, self.model.version)
        listener.model_version_published.assert_called_once_with(
            self.model.scope_version("pair-a"), self.model.version, "pair-a", file.file_id,
        )
        # A normal lifecycle publication owns the root again and cannot retain
        # a former running projection.
        self.model.update_file(file)
        self.assertIsNone(self.model.active_progress_overlay(file.file_id))

    def test_clearing_multi_root_projection_publishes_each_base_root(self):
        first = self._file("first", "pair-a")
        second = self._file("second", "pair-a")
        for file in (first, second):
            file.remote_size = 100
            file.state = ModelFile.State.DOWNLOADING
            self.model.add_file(file)
        self.model.replace_active_progress_overlays(
            {
                first.file_id: ActiveProgressOverlay(25, 25, 10, 8),
                second.file_id: ActiveProgressOverlay(30, 30, 11, 7),
            },
            {first.file_id, second.file_id},
        )
        listener = MagicMock()
        self.model.add_listener(listener)
        before_version = self.model.version

        self.model.clear_active_progress_overlays()

        self.assertEqual({}, self.model.active_progress_overlays_snapshot())
        self.assertEqual(before_version + 2, self.model.version)
        self.assertEqual(2, listener.model_version_published.call_count)
        published_ids = {call.args[3] for call in listener.model_version_published.call_args_list}
        self.assertEqual({first.file_id, second.file_id}, published_ids)

    def test_visible_state_uses_display_union_then_raw_progress_fallback(self):
        mixed = self._file("mixed", "pair-a")
        mixed.remote_size = 40
        mixed.transferred_size = 0
        mixed.display_size_total = 100
        mixed.display_transferred_size = 60
        mixed.remote_has_transferable_content = True
        self.assertEqual("stopped", Controller._model_record_visible_state(mixed))

        raw = self._file("raw", "pair-a")
        raw.remote_size = 40
        raw.transferred_size = 10
        raw.remote_has_transferable_content = True
        self.assertEqual("stopped", Controller._model_record_visible_state(raw))

    def test_visible_state_requires_physical_completion_and_stop_authority_is_preserved(self):
        vectors = [
            (100, 100, 100, 100, True, False, "downloaded"),
            (40, 40, 100, 100, False, False, "stopped"),  # full bytes without physical proof
            (40, 40, 100, 100, True, False, "downloaded"),  # mixed root physical proof
            (40, 10, 100, 100, False, False, "stopped"),  # display complete cannot replace incomplete proof
            (100, 100, 100, 100, True, True, "stopped"),  # persisted stop authority wins
        ]
        for raw_total, raw_transferred, display_total, display_transferred, complete_local_coverage, explicitly_stopped, expected in vectors:
            with self.subTest(raw_total=raw_total, raw_transferred=raw_transferred,
                              explicitly_stopped=explicitly_stopped):
                complete = self._file("complete", "pair-a")
                complete.remote_size = raw_total
                complete.transferred_size = raw_transferred
                complete.display_size_total = display_total
                complete.display_transferred_size = display_transferred
                complete.remote_has_transferable_content = True
                complete.complete_local_coverage = complete_local_coverage
                complete.explicitly_stopped = explicitly_stopped
                self.assertEqual(expected, Controller._model_record_visible_state(complete))

        zero_display_only = self._file("zero-display-only", "pair-a", directory=True)
        zero_display_only.remote_size = 0
        zero_display_only.transferred_size = 0
        zero_display_only.display_size_total = 60
        zero_display_only.display_transferred_size = 60
        zero_display_only.remote_has_transferable_content = True
        zero_display_only.complete_local_coverage = False
        self.assertNotEqual("downloaded", Controller._model_record_visible_state(zero_display_only))

        complete_directory = self._file("complete-directory", "pair-a", directory=True)
        complete_directory.remote_size = 40
        complete_directory.transferred_size = 0
        complete_directory.display_size_total = 100
        complete_directory.display_transferred_size = 100
        complete_directory.remote_has_transferable_content = True
        complete_directory.complete_local_coverage = True
        self.assertEqual("downloaded", Controller._model_record_visible_state(complete_directory))

    def test_visible_state_preserves_terminal_downloaded_unless_explicitly_stopped(self):
        downloaded = self._file("downloaded", "pair-a")
        downloaded.state = ModelFile.State.DOWNLOADED
        downloaded.remote_has_transferable_content = True
        self.assertEqual("downloaded", Controller._model_record_visible_state(downloaded))

        stopped = self._file("stopped-downloaded", "pair-a")
        stopped.state = ModelFile.State.DOWNLOADED
        stopped.remote_has_transferable_content = True
        stopped.explicitly_stopped = True
        self.assertEqual("stopped", Controller._model_record_visible_state(stopped))

        moved = self._file("moved", "pair-a")
        moved.state = ModelFile.State.DOWNLOADED
        moved.remote_has_transferable_content = True
        moved.explicitly_stopped = True
        moved.final_move_succeeded = True
        self.assertEqual("move_succeeded", Controller._model_record_visible_state(moved))

        extracted = self._file("extracted", "pair-a")
        extracted.state = ModelFile.State.EXTRACTED
        extracted.explicitly_stopped = True
        self.assertEqual("extracted", Controller._model_record_visible_state(extracted))

    def test_visible_state_leaves_untouched_default_without_progress(self):
        untouched = self._file("untouched", "pair-a")
        untouched.remote_size = 100
        untouched.transferred_size = 0
        untouched.remote_has_transferable_content = True
        self.assertEqual("default", Controller._model_record_visible_state(untouched))

    def test_visible_state_keeps_default_partial_progress_stopped(self):
        partial = self._file("partial", "pair-a")
        partial.remote_size = 100
        partial.transferred_size = 40
        partial.display_size_total = 100
        partial.display_transferred_size = 60
        partial.remote_has_transferable_content = True
        partial.complete_local_coverage = False
        self.assertEqual("stopped", Controller._model_record_visible_state(partial))

    def test_summary_card_counts_and_visible_states_match_view_contract(self):
        downloading = self._file("downloading", "pair-a")
        downloading.state = ModelFile.State.DOWNLOADING
        extracting = self._file("extracting", "pair-a")
        extracting.state = ModelFile.State.EXTRACTING
        downloaded = self._file("downloaded", "pair-a")
        downloaded.state = ModelFile.State.DOWNLOADED
        downloaded.complete_local_coverage = True
        complete_default = self._file("complete-default", "pair-a")
        complete_default.remote_size = 100
        complete_default.transferred_size = 100
        complete_default.display_size_total = 100
        complete_default.display_transferred_size = 100
        complete_default.remote_has_transferable_content = True
        complete_default.complete_local_coverage = True
        stopped_complete = self._file("stopped-complete", "pair-a")
        stopped_complete.remote_size = 100
        stopped_complete.transferred_size = 100
        stopped_complete.display_size_total = 100
        stopped_complete.display_transferred_size = 100
        stopped_complete.remote_has_transferable_content = True
        stopped_complete.explicitly_stopped = True
        unproven_complete = self._file("unproven-complete", "pair-a")
        unproven_complete.remote_size = 100
        unproven_complete.transferred_size = 100
        unproven_complete.display_size_total = 100
        unproven_complete.display_transferred_size = 100
        unproven_complete.remote_has_transferable_content = True
        unproven_complete.complete_local_coverage = False
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
        for file in (downloading, extracting, downloaded, complete_default, stopped_complete,
                     unproven_complete,
                     extracted, validated, stopped, local_only):
            self.model.add_file(file)
        summary = self.client.get("/server/model/v1/summary").json["path_pairs"][0]
        self.assertEqual(1, summary["active_count"])
        self.assertEqual(3, summary["completed_count"])
        self.assertEqual(2, summary["visible_state_counts"]["downloaded"])
        self.assertEqual(3, summary["visible_state_counts"]["stopped"])
        self.assertEqual(1, summary["visible_state_counts"]["local_only"])

    def test_summary_excludes_explicit_stop_from_ordinary_downloaded_completion(self):
        downloaded = self._file("downloaded-terminal", "pair-a")
        downloaded.state = ModelFile.State.DOWNLOADED
        downloaded.complete_local_coverage = True
        downloaded.explicitly_stopped = True
        extracted = self._file("extracted-terminal", "pair-a")
        extracted.state = ModelFile.State.EXTRACTED
        extracted.explicitly_stopped = True
        self.model.add_file(downloaded)
        self.model.add_file(extracted)

        summary = self.client.get("/server/model/v1/summary").json["path_pairs"][0]

        self.assertEqual(1, summary["completed_count"])
        self.assertEqual(0, summary["visible_state_counts"].get("downloaded", 0))
        self.assertEqual(1, summary["visible_state_counts"]["stopped"])
        self.assertEqual(1, summary["visible_state_counts"]["extracted"])
