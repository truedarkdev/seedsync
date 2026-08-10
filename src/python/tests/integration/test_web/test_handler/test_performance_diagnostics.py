# Copyright 2026, SeedSync Contributors, All rights reserved.

import json

from webtest import TestApp

from tests.integration.test_web.test_web_app import BaseTestWebApp
from common.performance_diagnostics import PerformanceDiagnosticsCollector


class TestPerformanceDiagnosticsHandler(BaseTestWebApp):
    def setUp(self):
        super().setUp()
        self.context.config.general.performance_diagnostics_enabled = True
        self.context.performance_diagnostics = PerformanceDiagnosticsCollector(
            lambda: self.context.config.general.performance_diagnostics_enabled
        )
        # Rebuild after replacing the test fixture's MagicMock placeholder.
        self.web_app_builder = self.web_app_builder.__class__(
            self.context, self.controller, self.auto_queue_persist, self.auth_store
        )
        self.web_app = self.web_app_builder.build()
        self.test_app = TestApp(
            self.web_app,
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(self.integration_admin_secret)},
        )

    def test_admin_can_read_bounded_numeric_snapshot_and_reset(self):
        self.context.performance_diagnostics.record_sample({
            "process_cpu_percent_one_core": 10.0,
            "process_rss_bytes": 123,
            "path": "/must-not-appear",
        })
        response = self.test_app.get("/server/admin/performance-diagnostics/v1?limit=1")
        self.assertEqual("application/json", response.content_type)
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual("seedsync.performance-diagnostics.v1", payload["schema"])
        self.assertEqual(1, payload["sample_count"])
        self.assertNotIn("path", payload["samples"][0])
        self.assertNotIn("must-not-appear", response.text)
        reset = self.test_app.post("/server/admin/performance-diagnostics/v1/reset")
        self.assertEqual("application/json", reset.content_type)
        self.assertEqual("nosniff", reset.headers["X-Content-Type-Options"])
        self.assertEqual("reset", json.loads(reset.body.decode("utf-8"))["status"])
        self.assertEqual(0, json.loads(self.test_app.get("/server/admin/performance-diagnostics/v1").text)["sample_count"])

    def test_admin_scope_and_query_bounds_are_enforced(self):
        reader_secret = self.auth_store.create_api_key("performance-reader", ["read"])["secret"]
        reader = TestApp(self.web_app, extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(reader_secret)})
        for method, path in (
            (reader.get, "/server/admin/performance-diagnostics/v1"),
            (reader.get, "/server/admin/performance-diagnostics/v1/export"),
            (reader.post, "/server/admin/performance-diagnostics/v1/reset"),
        ):
            denied = method(path, expect_errors=True)
            self.assertEqual(403, denied.status_int)
        bad_limit = self.test_app.get("/server/admin/performance-diagnostics/v1?limit=0", expect_errors=True)
        self.assertEqual(400, bad_limit.status_int)
        bad_since = self.test_app.get("/server/admin/performance-diagnostics/v1?since_sequence=-1", expect_errors=True)
        self.assertEqual(400, bad_since.status_int)

    def test_export_has_fixed_bounds_without_request_paths(self):
        response = self.test_app.get("/server/admin/performance-diagnostics/v1/export?path=/etc/passwd")
        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual(64, payload["export_max_samples"])
        self.assertIn("export_max_bytes", payload)
        self.assertEqual("application/json", response.content_type)
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        self.assertLessEqual(len(response.body), payload["export_max_bytes"])
