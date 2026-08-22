# Copyright 2026, SeedSync Contributors, All rights reserved.

import json

from webtest import TestApp

from tests.integration.test_web.test_web_app import BaseTestWebApp


class TestBreadcrumbTraceHandler(BaseTestWebApp):
    def test_get_returns_recorded_breadcrumbs_when_enabled(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record(
            "controller",
            "start",
            {"path_pair_count": 0},
            stage="controller",
            event_type="state_transition",
            corr_id="controller"
        )

        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(True, json_dict["enabled"])
        self.assertEqual(128, json_dict["max_entries"])
        self.assertEqual(1, json_dict["entry_count"])
        self.assertEqual(1, len(json_dict["entries"]))
        self.assertEqual("controller", json_dict["entries"][0]["source"])
        self.assertEqual("start", json_dict["entries"][0]["message"])
        self.assertEqual("state_transition", json_dict["entries"][0]["event_type"])
        self.assertEqual(0, json_dict["entries"][0]["details"]["path_pair_count"])
        self.assertEqual("controller", json_dict["entries"][0]["corr_id"])

    def test_get_returns_empty_collection_when_disabled(self):
        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(False, json_dict["enabled"])
        self.assertEqual(128, json_dict["max_entries"])
        self.assertEqual(0, json_dict["entry_count"])
        self.assertEqual([], json_dict["entries"])

    def test_get_returns_since_version_slice(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record("controller", "start", {"path_pair_count": 0})
        first_version = self.context.breadcrumb_trace.snapshot()["version"]
        self.context.breadcrumb_trace.record("controller", "refresh", {"path_pair_count": 1})

        resp = self.test_app.get("/server/breadcrumbs/get?since_version={}".format(first_version))
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(first_version, json_dict["since_version"])
        self.assertEqual(1, json_dict["entry_count"])
        self.assertEqual("refresh", json_dict["entries"][0]["message"])

    def test_get_rejects_read_only_token(self):
        read_only_secret = self.auth_store.create_api_key("integration-reader", ["read"])["secret"]
        read_only_app = TestApp(
            self.web_app,
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(read_only_secret)}
        )

        resp = read_only_app.get("/server/breadcrumbs/get", expect_errors=True)
        self.assertEqual(403, resp.status_int)
        self.assertIn("admin", resp.text)

    def test_get_preserves_previously_retained_entries_after_disable(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record("controller", "start", {"path_pair_count": 0})
        self.context.config.general.breadcrumb_trace_enabled = False

        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(False, json_dict["enabled"])
        self.assertEqual(1, json_dict["entry_count"])
        self.assertEqual("start", json_dict["entries"][0]["message"])
        self.assertFalse(json_dict["window_reset"])
        self.assertIsNone(json_dict["window_reset_reason"])

        self.context.config.general.breadcrumb_trace_enabled = True
        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(1, json_dict["entry_count"])
        self.assertEqual("start", json_dict["entries"][0]["message"])

    def test_get_supports_filters_limit_and_order(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record(
            "controller",
            "start",
            {"path_pair_count": 0},
            stage="controller",
            event_type="state_transition",
            corr_id="flow-1",
            flow_id="flow-a",
            path_pair_id="pair-1",
            file_id="file-1"
        )
        self.context.breadcrumb_trace.record(
            "controller",
            "refresh",
            {"path_pair_count": 1},
            stage="refresh",
            event_type="state_transition",
            corr_id="flow-2",
            flow_id="flow-b",
            path_pair_id="pair-2",
            file_id="file-2"
        )
        self.context.breadcrumb_trace.record(
            "controller",
            "finish",
            {"path_pair_count": 2},
            stage="finish",
            event_type="state_transition",
            corr_id="flow-1",
            flow_id="flow-a",
            path_pair_id="pair-1",
            file_id="file-1"
        )

        resp = self.test_app.get(
            "/server/breadcrumbs/get?corr_id=flow-1&flow_id=flow-a&event_type=state_transition"
            "&path_pair_id=pair-1&file_id=file-1&limit=1&order=desc"
        )
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(1, json_dict["entry_count"])
        self.assertEqual("finish", json_dict["entries"][0]["message"])
        self.assertEqual(1, json_dict["query"]["limit"])
        self.assertEqual("desc", json_dict["query"]["order"])
        self.assertEqual("flow-1", json_dict["query"]["corr_id"])
        self.assertEqual("flow-a", json_dict["query"]["flow_id"])
        self.assertEqual("state_transition", json_dict["query"]["event_type"])
        self.assertEqual("pair-1", json_dict["query"]["path_pair_id"])
        self.assertEqual("file-1", json_dict["query"]["file_id"])

    def test_get_rejects_invalid_limit_and_order(self):
        resp = self.test_app.get("/server/breadcrumbs/get?limit=0", expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.assertIn("limit must be greater than 0", resp.text)

        resp = self.test_app.get("/server/breadcrumbs/get?limit=cat", expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.assertIn("limit must be an integer", resp.text)

        resp = self.test_app.get("/server/breadcrumbs/get?order=sideways", expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.assertIn("order must be 'asc' or 'desc'", resp.text)

    def test_get_redacts_sensitive_command_fields_and_returns_failure_summary(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record(
            "controller",
            "start",
            {"path_pair_count": 0},
            stage="controller",
            event_type="state_transition",
            corr_id="flow-1"
        )
        self.context.breadcrumb_trace.record(
            "controller",
            "queue_command",
            {
                "command": "lftp -e 'open secret.example; get file'",
                "api_token": "super-secret-token",
                "reason": "x" * 400,
            },
            stage="command",
            event_type="failure",
            corr_id="flow-1"
        )

        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual("<redacted>", json_dict["entries"][1]["details"]["command"])
        self.assertEqual("<redacted>", json_dict["entries"][1]["details"]["api_token"])
        self.assertTrue(json_dict["entries"][1]["details"]["reason"].endswith("...<truncated>"))
        self.assertIsNotNone(json_dict["failure_summary"])
        self.assertEqual("flow", json_dict["failure_summary"]["trace_scope"])
        self.assertEqual("flow-1", json_dict["failure_summary"]["corr_id"])
        self.assertEqual("command", json_dict["failure_summary"]["stage"])
        self.assertEqual("queue_command", json_dict["failure_summary"]["message"])
        self.assertLessEqual(len(json_dict["failure_summary"]["recent_stage_trail"]), 5)
        self.assertEqual(2, json_dict["failure_summary"]["version"])

    def test_get_redacts_risky_strings_inside_generic_details(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record(
            "controller",
            "command_failed",
            {
                "error_message": "Lftp error: password=hunter2 user myuser@seedbox.example.com:~>",
                "reason": "ssh command failed: token=secret-token",
            },
            stage="command",
            event_type="failure",
            corr_id="flow-1"
        )

        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        error_message = json_dict["entries"][0]["details"]["error_message"]
        reason = json_dict["entries"][0]["details"]["reason"]
        self.assertNotIn("hunter2", error_message)
        self.assertNotIn("myuser@seedbox.example.com", error_message)
        self.assertNotIn("secret-token", reason)
        self.assertIn("**REDACTED**", error_message)
        self.assertIn("**REDACTED**", reason)

    def test_reset_purges_retained_entries_and_requires_admin(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record("controller", "start", {"path_pair_count": 0})
        self.context.breadcrumb_trace.record("controller", "refresh", {"path_pair_count": 1})

        read_only_secret = self.auth_store.create_api_key("integration-reader", ["read"])["secret"]
        read_only_app = TestApp(
            self.web_app,
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(read_only_secret)}
        )

        resp = read_only_app.post("/server/breadcrumbs/reset", expect_errors=True)
        self.assertEqual(403, resp.status_int)
        self.assertIn("admin", resp.text)

        resp = self.test_app.post("/server/breadcrumbs/reset")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual("reset", json_dict["status"])

        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(0, json_dict["entry_count"])
        self.assertEqual("reset", json_dict["window_reset_reason"])

    def test_v1_routes_expose_capabilities_policy_events_and_export(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record(
            "transfer",
            "started",
            {"size": 1},
            category="transfer.remote",
            level="warning",
            corr_id="corr-1",
            flow_id="flow-1",
            stage="transfer",
            event_type="state_transition",
        )

        capabilities = self.test_app.get("/server/breadcrumbs/v1/capabilities")
        self.assertEqual(200, capabilities.status_int)
        capabilities_json = json.loads(capabilities.text)
        self.assertTrue(capabilities_json["query"])
        self.assertTrue(capabilities_json["export"])

        events = self.test_app.get(
            "/server/breadcrumbs/v1/events?category_prefix=transfer&correlation_id=corr-1&limit=1"
        )
        self.assertEqual(200, events.status_int)
        events_json = json.loads(events.text)
        self.assertEqual(1, len(events_json["events"]))
        self.assertEqual("transfer.remote", events_json["events"][0]["category"])
        self.assertIn("gaps", events_json)

        exported = self.test_app.get("/server/breadcrumbs/v1/export?source=transfer&limit=1")
        self.assertEqual(200, exported.status_int)
        self.assertEqual(1, len(json.loads(exported.text)["events"]))
        jsonl = self.test_app.get("/server/breadcrumbs/v1/export?source=transfer&format=jsonl&limit=1")
        self.assertEqual(200, jsonl.status_int)
        self.assertEqual("application/x-ndjson", jsonl.headers["Content-Type"].split(";")[0])
        jsonl_records = [json.loads(line) for line in jsonl.text.splitlines()]
        self.assertEqual("breadcrumb-metadata", jsonl_records[0]["record_type"])
        self.assertEqual(1, jsonl_records[0]["export_event_count"])
        self.assertEqual("started", jsonl_records[1]["message"])

        policy = self.test_app.get("/server/breadcrumbs/v1/policy")
        self.assertEqual(200, policy.status_int)
        self.assertIn("revision", json.loads(policy.text))

        # New diagnostics surface is explicitly versioned; only get/reset are legacy.
        alias = self.test_app.get("/server/breadcrumbs/events?limit=1", expect_errors=True)
        self.assertEqual(404, alias.status_int)

    def test_v1_policy_validate_apply_reset_and_compare_and_swap_conflict(self):
        candidate = {"policy": {"default": "warning", "rules": {"transfer": "debug"}}}
        validated = self.test_app.post_json("/server/breadcrumbs/v1/policy/validate", candidate)
        self.assertEqual(200, validated.status_int)
        self.assertTrue(json.loads(validated.text)["valid"])

        applied = self.test_app.post_json(
            "/server/breadcrumbs/v1/policy/apply", {**candidate, "expected_revision": 0}
        )
        self.assertEqual(200, applied.status_int)
        self.assertTrue(json.loads(applied.text)["applied"])

        conflict = self.test_app.post_json(
            "/server/breadcrumbs/v1/policy/apply", {**candidate, "expected_revision": 0},
            expect_errors=True,
        )
        self.assertEqual(409, conflict.status_int)
        self.assertTrue(json.loads(conflict.text)["conflict"])

        reset = self.test_app.post_json(
            "/server/breadcrumbs/v1/policy/reset", {"expected_revision": 1}
        )
        self.assertEqual(200, reset.status_int)
        self.assertTrue(json.loads(reset.text)["applied"])

    def test_v1_clear_and_stream_are_bounded(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record("controller", "start", {"n": 1})

        stream = self.test_app.get("/server/breadcrumbs/v1/stream?limit=1")
        self.assertEqual(200, stream.status_int)
        self.assertEqual("text/event-stream", stream.headers["Content-Type"].split(";")[0])
        self.assertIn("event: snapshot", stream.text)
        self.assertIn('"events"', stream.text)

        cleared = self.test_app.post_json("/server/breadcrumbs/v1/clear", {})
        self.assertEqual(200, cleared.status_int)
        self.assertTrue(json.loads(cleared.text)["cleared"])

        invalid = self.test_app.post_json("/server/breadcrumbs/v1/clear", {"unknown": "scope"}, expect_errors=True)
        self.assertEqual(400, invalid.status_int)

    def test_v1_rejects_unknown_filters_and_page_export_limits(self):
        invalid = self.test_app.get(
            "/server/breadcrumbs/v1/events?not_a_filter=1", expect_errors=True
        )
        self.assertEqual(400, invalid.status_int)

        invalid_page = self.test_app.get(
            "/server/breadcrumbs/v1/events?limit=257", expect_errors=True
        )
        self.assertEqual(400, invalid_page.status_int)

        invalid_interval = self.test_app.get(
            "/server/breadcrumbs/v1/stream?follow=true&interval_ms=0", expect_errors=True
        )
        self.assertEqual(400, invalid_interval.status_int)

        invalid_export = self.test_app.get(
            "/server/breadcrumbs/v1/export?limit=2049", expect_errors=True
        )
        self.assertEqual(400, invalid_export.status_int)

        invalid_body = self.test_app.post_json(
            "/server/breadcrumbs/v1/policy/apply", {"policy": []}, expect_errors=True
        )
        self.assertEqual(400, invalid_body.status_int)

    def test_all_breadcrumb_routes_require_admin_and_always_auth(self):
        routes = [
            route for route in self.web_app.routes
            if route.rule.startswith("/server/breadcrumbs")
        ]
        self.assertGreaterEqual(len(routes), 11)
        for route in routes:
            self.assertEqual("admin", route.config.get("required_scope"))
            self.assertTrue(route.config.get("always_auth"))
