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

    def test_get_clears_previously_retained_entries_after_disable(self):
        self.context.config.general.breadcrumb_trace_enabled = True
        self.context.breadcrumb_trace.record("controller", "start", {"path_pair_count": 0})
        self.context.config.general.breadcrumb_trace_enabled = False

        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(False, json_dict["enabled"])
        self.assertEqual([], json_dict["entries"])
        self.assertEqual(True, json_dict["window_reset"])
        self.assertEqual("disabled", json_dict["window_reset_reason"])

        self.context.config.general.breadcrumb_trace_enabled = True
        resp = self.test_app.get("/server/breadcrumbs/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(resp.body.decode("utf-8"))
        self.assertEqual([], json_dict["entries"])

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
