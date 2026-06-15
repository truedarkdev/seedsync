# Copyright 2017, Inderpreet Singh, All rights reserved.

import json

from tests.integration.test_web.test_web_app import BaseTestWebApp


class TestConfigHandler(BaseTestWebApp):
    def test_get(self):
        self.context.config.general.debug = True
        self.context.config.general.api_token = "super-secret-token"
        self.context.config.general.breadcrumb_trace_enabled = False
        self.context.config.lftp.remote_address = "server.remote.com"
        self.context.config.lftp.remote_username = "seedsync-user"
        self.context.config.lftp.remote_port = 2222
        self.context.config.lftp.remote_path = "/remote/server/path"
        self.context.config.lftp.net_socket_buffer = "512K"
        self.context.config.controller.interval_ms_local_scan = 5678
        self.context.config.web.port = 8080
        resp = self.test_app.get("/server/config/get")
        self.assertEqual(200, resp.status_int)
        json_dict = json.loads(str(resp.html))
        self.assertEqual(True, json_dict["general"]["debug"])
        self.assertEqual("**REDACTED**", json_dict["general"]["api_token"])
        self.assertEqual(False, json_dict["general"]["breadcrumb_trace_enabled"])
        self.assertEqual("**REDACTED**", json_dict["lftp"]["remote_address"])
        self.assertEqual("**REDACTED**", json_dict["lftp"]["remote_username"])
        self.assertEqual("**REDACTED**", json_dict["lftp"]["remote_path"])
        self.assertEqual(2222, json_dict["lftp"]["remote_port"])
        self.assertEqual("512K", json_dict["lftp"]["net_socket_buffer"])
        self.assertEqual(5678, json_dict["controller"]["interval_ms_local_scan"])
        self.assertEqual(8080, json_dict["web"]["port"])

    def test_set_good(self):
        self.assertEqual(None, self.context.config.general.debug)
        resp = self.test_app.post_json("/server/config/set/general/debug", {"value": True})
        self.assertEqual(200, resp.status_int)
        self.assertEqual(True, self.context.config.general.debug)

        self.assertEqual(False, self.context.config.general.breadcrumb_trace_enabled)
        resp = self.test_app.post_json("/server/config/set/general/breadcrumb_trace_enabled", {"value": True})
        self.assertEqual(200, resp.status_int)
        self.assertEqual(True, self.context.config.general.breadcrumb_trace_enabled)

        self.assertEqual(None, self.context.config.lftp.remote_path)
        resp = self.test_app.post_json("/server/config/set/lftp/remote_path", {"value": "/path/to/somewhere"})
        self.assertEqual(200, resp.status_int)
        self.assertEqual("/path/to/somewhere", self.context.config.lftp.remote_path)

        self.assertEqual(None, self.context.config.lftp.net_socket_buffer)
        resp = self.test_app.post_json("/server/config/set/lftp/net_socket_buffer", {"value": "8M"})
        self.assertEqual(200, resp.status_int)
        self.assertEqual("8M", self.context.config.lftp.net_socket_buffer)

        resp = self.test_app.post_json("/server/config/set/lftp/net_socket_buffer", {"value": ""})
        self.assertEqual(200, resp.status_int)
        self.assertEqual("", self.context.config.lftp.net_socket_buffer)

        self.assertEqual(None, self.context.config.controller.interval_ms_local_scan)
        resp = self.test_app.post_json("/server/config/set/controller/interval_ms_local_scan", {"value": 5678})
        self.assertEqual(200, resp.status_int)
        self.assertEqual(5678, self.context.config.controller.interval_ms_local_scan)

        self.assertEqual(None, self.context.config.web.port)
        resp = self.test_app.post_json("/server/config/set/web/port", {"value": 8080})
        self.assertEqual(200, resp.status_int)
        self.assertEqual(8080, self.context.config.web.port)

    def test_set_get_requests_no_longer_mutate(self):
        self.assertEqual(None, self.context.config.general.debug)
        resp = self.test_app.get("/server/config/set/general/debug", expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual(None, self.context.config.general.debug)

    def test_set_api_token_via_body_is_forbidden(self):
        self.assertEqual(None, self.context.config.general.api_token)
        resp = self.test_app.post_json(
            "/server/config/set/general/api_token",
            {"value": "super-secret-token"},
            expect_errors=True
        )
        self.assertEqual(403, resp.status_int)
        self.assertEqual(None, self.context.config.general.api_token)
        self.assertEqual(
            "Section 'general' option 'api_token' cannot be set via request body",
            str(resp.html)
        )

    def test_set_config_api_redaction_via_body_is_forbidden(self):
        self.assertEqual(True, self.context.config.general.config_api_redact_remote_details)
        resp = self.test_app.post_json(
            "/server/config/set/general/config_api_redact_remote_details",
            {"value": False},
            expect_errors=True
        )
        self.assertEqual(403, resp.status_int)
        self.assertEqual(True, self.context.config.general.config_api_redact_remote_details)
        self.assertEqual(
            "Section 'general' option 'config_api_redact_remote_details' cannot be set via request body",
            str(resp.html)
        )

    def test_set_trusted_browser_bootstrap_remote_addrs_via_body_is_forbidden(self):
        self.assertEqual(None, self.context.config.general.trusted_browser_bootstrap_remote_addrs)
        resp = self.test_app.post_json(
            "/server/config/set/general/trusted_browser_bootstrap_remote_addrs",
            {"value": "172.25.0.1/32"},
            expect_errors=True
        )
        self.assertEqual(403, resp.status_int)
        self.assertEqual(None, self.context.config.general.trusted_browser_bootstrap_remote_addrs)
        self.assertEqual(
            "Section 'general' option 'trusted_browser_bootstrap_remote_addrs' cannot be set via request body",
            str(resp.html)
        )

    def test_set_missing_section(self):
        self.assertFalse(self.context.config.has_section("bad_section"))
        resp = self.test_app.post_json("/server/config/set/bad_section/option", {"value": "value"}, expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual("There is no section 'bad_section' in config", str(resp.html))
        self.assertFalse(self.context.config.has_section("bad_section"))

    def test_set_missing_option(self):
        self.assertFalse(self.context.config.general.has_property("bad_option"))
        resp = self.test_app.post_json("/server/config/set/general/bad_option", {"value": "value"}, expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual("Section 'general' in config has no option 'bad_option'", str(resp.html))
        self.assertFalse(self.context.config.general.has_property("bad_option"))

    def test_set_bad_value(self):
        # boolean
        self.assertEqual(None, self.context.config.general.debug)
        resp = self.test_app.post_json("/server/config/set/general/debug", {"value": "cat"}, expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.assertEqual("Bad config: General.debug (cat) must be a boolean value", str(resp.html))
        self.assertEqual(None, self.context.config.general.debug)

        # positive int
        self.assertEqual(None, self.context.config.controller.interval_ms_local_scan)
        resp = self.test_app.post_json("/server/config/set/controller/interval_ms_local_scan", {"value": -1}, expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.assertEqual("Bad config: Controller.interval_ms_local_scan (-1) must be greater than 0", str(resp.html))
        self.assertEqual(None, self.context.config.controller.interval_ms_local_scan)

        # byte size value
        self.assertEqual(None, self.context.config.lftp.net_socket_buffer)
        resp = self.test_app.post_json("/server/config/set/lftp/net_socket_buffer", {"value": "bad"}, expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.assertEqual(
            "Bad config: Lftp.net_socket_buffer (bad) must be a byte size value like 512K, 8M, 1G, or 8388608",
            str(resp.html)
        )
        self.assertEqual(None, self.context.config.lftp.net_socket_buffer)

    def test_set_empty_value(self):
        self.assertEqual(None, self.context.config.lftp.remote_path)
        resp = self.test_app.post_json("/server/config/set/lftp/remote_path", {"value": "  "}, expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.assertEqual("Bad config: Lftp.remote_path is empty", str(resp.html))
        self.assertEqual(None, self.context.config.lftp.remote_path)
