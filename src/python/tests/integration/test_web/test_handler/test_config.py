# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import os
from unittest.mock import MagicMock, patch

from common import Config
from tests.integration.test_web.test_web_app import BaseTestWebApp


class TestConfigHandler(BaseTestWebApp):
    def setUp(self):
        super().setUp()
        self.config_path = os.path.join(self.temp_dir, "settings.cfg")
        self.context.config.file_path = self.config_path
        self.context.config.to_file()

    def _read_config_contents(self):
        with open(self.config_path, "r", encoding="utf-8") as config_file:
            return config_file.read()

    def test_get(self):
        self.context.config.general.log_level = "DEBUG"
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
        self.assertEqual("DEBUG", json_dict["general"]["log_level"])
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
        self.assertEqual("INFO", self.context.config.general.log_level)
        resp = self.test_app.post_json("/server/config/set/general/log_level", {"value": "DEBUG"})
        self.assertEqual(200, resp.status_int)
        self.assertEqual("DEBUG", self.context.config.general.log_level)

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

        persisted_contents = self._read_config_contents()
        self.assertIn("log_level = DEBUG", persisted_contents)
        self.assertIn("breadcrumb_trace_enabled = True", persisted_contents)
        self.assertIn("remote_path = /path/to/somewhere", persisted_contents)
        self.assertIn("port = 8080", persisted_contents)

    def test_set_persistence_failure_rolls_back(self):
        self.context.config.general.log_level = "INFO"
        before_contents = self._read_config_contents()

        with patch.object(Config, "to_file", side_effect=OSError("disk full")) as mock_to_file:
            resp = self.test_app.post_json(
                "/server/config/set/general/log_level",
                {"value": "DEBUG"},
                expect_errors=True
            )

        self.assertEqual(500, resp.status_int)
        self.assertIn("Failed to persist config general.log_level", str(resp.html))
        self.assertEqual("INFO", self.context.config.general.log_level)
        self.assertEqual(before_contents, self._read_config_contents())
        mock_to_file.assert_called_once()

    def test_set_persistence_failure_skips_callback_and_rolls_back(self):
        self.context.config.general.breadcrumb_trace_enabled = False
        sync_hook = MagicMock()
        self.web_app_builder.config_handler._ConfigHandler__breadcrumb_trace_sync = sync_hook
        before_contents = self._read_config_contents()

        with patch.object(Config, "to_file", side_effect=OSError("disk full")) as mock_to_file:
            resp = self.test_app.post_json(
                "/server/config/set/general/breadcrumb_trace_enabled",
                {"value": True},
                expect_errors=True
            )

        self.assertEqual(500, resp.status_int)
        self.assertIn("Failed to persist config general.breadcrumb_trace_enabled", str(resp.html))
        self.assertEqual(False, self.context.config.general.breadcrumb_trace_enabled)
        self.assertEqual(before_contents, self._read_config_contents())
        mock_to_file.assert_called_once()
        sync_hook.assert_not_called()

    def test_set_serializes_concurrent_writers(self):
        import threading

        self.context.config.general.log_level = "INFO"
        handler = self.web_app_builder.config_handler
        before_contents = self._read_config_contents()

        enter_to_file = threading.Event()
        release_to_file = threading.Event()
        results = {"release_timeout": False}

        class ProbingLock:
            def __init__(self):
                self._lock = threading.Lock()
                self.second_acquire_attempted = threading.Event()

            def __enter__(self):
                if self._lock.locked():
                    self.second_acquire_attempted.set()
                self._lock.acquire()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                self._lock.release()
                return False

        handler._ConfigHandler__write_lock = ProbingLock()

        def slow_failing_write(*_args, **_kwargs):
            enter_to_file.set()
            if not release_to_file.wait(timeout=5):
                results["release_timeout"] = True
            raise OSError("disk full")

        def writer_a():
            with patch.object(Config, "to_file", side_effect=slow_failing_write):
                response = handler._ConfigHandler__handle_set_config("general", "log_level", "DEBUG")
                results["a"] = response.status_code

        def writer_b():
            response = handler._ConfigHandler__handle_set_config("general", "log_level", "WARNING")
            results["b"] = response.status_code

        t_a = threading.Thread(target=writer_a)
        t_a.start()
        self.assertTrue(enter_to_file.wait(timeout=5))

        t_b = threading.Thread(target=writer_b)
        t_b.start()
        self.assertTrue(handler._ConfigHandler__write_lock.second_acquire_attempted.wait(timeout=5))

        self.assertEqual("DEBUG", self.context.config.general.log_level)
        self.assertEqual(before_contents, self._read_config_contents())
        self.assertNotIn("b", results)

        release_to_file.set()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        self.assertFalse(t_a.is_alive())
        self.assertFalse(t_b.is_alive())
        self.assertFalse(results["release_timeout"])
        self.assertEqual(500, results["a"])
        self.assertEqual(200, results["b"])
        self.assertEqual("WARNING", self.context.config.general.log_level)
        self.assertIn("log_level = WARNING", self._read_config_contents())

    def test_set_redacted_sentinel_is_rejected_for_sensitive_fields(self):
        self.context.config.general.api_token = "existing-api-token"
        self.context.config.lftp.remote_password = "existing-remote-password"

        for sentinel in (Config.REDACTED_SENTINEL, Config.LEGACY_REDACTED_SENTINEL):
            for section, key, expected_value in (
                ("general", "api_token", "existing-api-token"),
                ("lftp", "remote_password", "existing-remote-password"),
            ):
                with self.subTest(sentinel=sentinel, section=section, key=key):
                    resp = self.test_app.post_json(
                        "/server/config/set/{}/{}".format(section, key),
                        {"value": sentinel},
                        expect_errors=True
                    )

                    self.assertEqual(400, resp.status_int)
                    self.assertEqual(expected_value, getattr(getattr(self.context.config, section), key))
                    self.assertEqual(
                        "Section '{}' option '{}' cannot be set to redacted value".format(section, key),
                        str(resp.html)
                    )

    def test_set_get_requests_no_longer_mutate(self):
        self.assertEqual("INFO", self.context.config.general.log_level)
        resp = self.test_app.get("/server/config/set/general/log_level", expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual("INFO", self.context.config.general.log_level)

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
        # log level
        self.assertEqual("INFO", self.context.config.general.log_level)
        resp = self.test_app.post_json("/server/config/set/general/log_level", {"value": "cat"}, expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.assertEqual(
            "Bad config: General.log_level (cat) must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL",
            str(resp.html)
        )
        self.assertEqual("INFO", self.context.config.general.log_level)

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
