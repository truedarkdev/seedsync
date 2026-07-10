# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import json
from unittest.mock import MagicMock

from common import Config
from web.serialize import SerializeConfig


class TestSerializeConfig(unittest.TestCase):
    def test_notifications_are_redacted_with_explicit_configured_state(self):
        config = Config()
        config.notifications.webhook_url = "https://hooks.example.test/private?token=value"
        config.notifications.hmac_secret = "signing-secret"
        config.notifications.apprise_url = "https://apprise.example.test/notify/private-key"

        payload = json.loads(SerializeConfig.config(config))

        self.assertEqual(Config.REDACTED_SENTINEL, payload["notifications"]["webhook_url"])
        self.assertEqual(Config.REDACTED_SENTINEL, payload["notifications"]["hmac_secret"])
        self.assertTrue(payload["notifications"]["webhook_url_configured"])
        self.assertTrue(payload["notifications"]["hmac_secret_configured"])
        self.assertEqual(Config.REDACTED_SENTINEL, payload["notifications"]["apprise_url"])
        self.assertTrue(payload["notifications"]["apprise_url_configured"])

    def test_restart_required_metadata_matches_config_shape_and_hot_fields(self):
        config = Config()
        config.general.verbose = False
        config.general.exclude_patterns = "*.nfo"
        config.general.breadcrumb_trace_enabled = True
        config.lftp.net_socket_buffer = "8M"
        config.lftp.remote_path = "/remote/server/path"
        config.validate.xfer_verify = True
        config.controller.interval_ms_remote_scan = 30000
        config.web.port = 8800
        config.autoqueue.enabled = True
        config.logging.log_format = "json"

        out = SerializeConfig.config(config)
        out_dict = json.loads(out)

        self.assertIn("restart_required", out_dict)
        for section in ("general", "lftp", "validate", "controller", "web", "autoqueue", "logging"):
            self.assertEqual(set(out_dict[section].keys()), set(out_dict["restart_required"][section].keys()))

        self.assertEqual(True, out_dict["restart_required"]["general"]["log_level"])
        self.assertEqual(False, out_dict["restart_required"]["general"]["verbose"])
        self.assertEqual(False, out_dict["restart_required"]["general"]["exclude_patterns"])
        self.assertEqual(False, out_dict["restart_required"]["general"]["breadcrumb_trace_enabled"])
        self.assertEqual(True, out_dict["restart_required"]["lftp"]["remote_path"])
        self.assertEqual(False, out_dict["restart_required"]["lftp"]["net_socket_buffer"])
        self.assertEqual(True, out_dict["restart_required"]["lftp"]["transfer_backend"])
        self.assertEqual(False, out_dict["restart_required"]["validate"]["xfer_verify"])
        self.assertEqual(True, out_dict["restart_required"]["controller"]["interval_ms_remote_scan"])
        self.assertEqual(True, out_dict["restart_required"]["web"]["port"])
        self.assertEqual(True, out_dict["restart_required"]["autoqueue"]["enabled"])
        self.assertEqual(True, out_dict["restart_required"]["logging"]["log_format"])

    def test_section_general(self):
        config = Config()
        config.general.log_level = "DEBUG"
        config.general.exclude_patterns = "*.nfo,Sample/"
        config.general.config_api_redact_remote_details = True
        config.general.api_token = "super-secret-token"
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("general", out_dict)
        self.assertEqual("DEBUG", out_dict["general"]["log_level"])
        self.assertEqual("*.nfo,Sample/", out_dict["general"]["exclude_patterns"])
        self.assertEqual(True, out_dict["general"]["config_api_redact_remote_details"])
        self.assertEqual("**REDACTED**", out_dict["general"]["api_token"])
        self.assertNotIn("super-secret-token", out)

    def test_section_general_redacts_webhook_secret_when_present(self):
        config = MagicMock()
        config.general.config_api_redact_remote_details = True
        config.as_dict.return_value = {
            "General": {
                "debug": True,
                "api_token": "super-secret-token",
                "webhook_secret": "super-secret-webhook-secret",
            },
            "Lftp": {
                "remote_password": "super-secret-ssh-password",
            },
        }

        out = SerializeConfig.config(config)
        out_dict = json.loads(out)

        self.assertEqual("**REDACTED**", out_dict["general"]["api_token"])
        self.assertEqual("**REDACTED**", out_dict["general"]["webhook_secret"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_password"])
        self.assertNotIn("super-secret-token", out)
        self.assertNotIn("super-secret-webhook-secret", out)
        self.assertNotIn("super-secret-ssh-password", out)

    def test_section_lftp(self):
        config = Config()
        config.lftp.transfer_backend = "rclone"
        config.lftp.remote_address = "server.remote.com"
        config.lftp.remote_username = "user-on-remote-server"
        config.lftp.remote_password = "secret123"
        config.lftp.remote_port = 3456
        config.lftp.remote_path = "/remote/server/path"
        config.lftp.local_path = "/local/server/path"
        config.lftp.remote_path_to_scan_script = "/remote/server/path/to/script"
        config.lftp.remote_python_path = "/home/user/.pyenv/shims/python3"
        config.lftp.num_max_parallel_downloads = 6
        config.lftp.num_max_parallel_files_per_download = 7
        config.lftp.num_max_connections_per_root_file = 2
        config.lftp.num_max_connections_per_dir_file = 3
        config.lftp.num_max_total_connections = 4
        config.lftp.net_socket_buffer = "512K"
        config.lftp.protocol = "ftps"
        config.lftp.remote_ftp_port = 2121
        config.lftp.ftp_ssl_verify_certificate = True
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("lftp", out_dict)
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_address"])
        self.assertEqual("rclone", out_dict["lftp"]["transfer_backend"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_username"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_password"])
        self.assertEqual(3456, out_dict["lftp"]["remote_port"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_path"])
        self.assertEqual("/local/server/path", out_dict["lftp"]["local_path"])
        self.assertEqual("/remote/server/path/to/script", out_dict["lftp"]["remote_path_to_scan_script"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_python_path"])
        self.assertEqual(6, out_dict["lftp"]["num_max_parallel_downloads"])
        self.assertEqual(7, out_dict["lftp"]["num_max_parallel_files_per_download"])
        self.assertEqual(2, out_dict["lftp"]["num_max_connections_per_root_file"])
        self.assertEqual(3, out_dict["lftp"]["num_max_connections_per_dir_file"])
        self.assertEqual(4, out_dict["lftp"]["num_max_total_connections"])
        self.assertEqual("512K", out_dict["lftp"]["net_socket_buffer"])
        self.assertEqual("sftp", out_dict["lftp"]["protocol"])
        self.assertEqual(2121, out_dict["lftp"]["remote_ftp_port"])
        self.assertEqual(True, out_dict["lftp"]["ftp_ssl_verify_certificate"])
        self.assertNotIn("server.remote.com", out)
        self.assertNotIn("user-on-remote-server", out)
        self.assertNotIn("secret123", out)

    def test_section_validate(self):
        config = Config()
        config.validate.xfer_verify = False
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("validate", out_dict)
        self.assertEqual(False, out_dict["validate"]["xfer_verify"])

    def test_section_lftp_with_remote_detail_redaction_disabled(self):
        config = Config()
        config.general.config_api_redact_remote_details = False
        config.general.api_token = "super-secret-token"
        config.lftp.transfer_backend = "rclone"
        config.lftp.remote_address = "server.remote.com"
        config.lftp.remote_username = "user-on-remote-server"
        config.lftp.remote_password = "secret123"
        config.lftp.remote_port = 3456
        config.lftp.remote_path = "/remote/server/path"
        config.lftp.local_path = "/local/server/path"
        config.lftp.remote_path_to_scan_script = "/remote/server/path/to/script"
        config.lftp.remote_python_path = "/home/user/.pyenv/shims/python3"
        config.lftp.net_socket_buffer = "8M"
        config.lftp.protocol = "sftp"
        config.lftp.remote_ftp_port = 21
        config.lftp.ftp_ssl_verify_certificate = False
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("lftp", out_dict)
        self.assertEqual(False, out_dict["general"]["config_api_redact_remote_details"])
        self.assertEqual("rclone", out_dict["lftp"]["transfer_backend"])
        self.assertEqual("server.remote.com", out_dict["lftp"]["remote_address"])
        self.assertEqual("user-on-remote-server", out_dict["lftp"]["remote_username"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_password"])
        self.assertEqual(3456, out_dict["lftp"]["remote_port"])
        self.assertEqual("/remote/server/path", out_dict["lftp"]["remote_path"])
        self.assertEqual("/local/server/path", out_dict["lftp"]["local_path"])
        self.assertEqual("/remote/server/path/to/script", out_dict["lftp"]["remote_path_to_scan_script"])
        self.assertEqual("/home/user/.pyenv/shims/python3", out_dict["lftp"]["remote_python_path"])
        self.assertEqual("8M", out_dict["lftp"]["net_socket_buffer"])
        self.assertEqual("sftp", out_dict["lftp"]["protocol"])
        self.assertEqual(21, out_dict["lftp"]["remote_ftp_port"])
        self.assertEqual(False, out_dict["lftp"]["ftp_ssl_verify_certificate"])
        self.assertEqual("**REDACTED**", out_dict["general"]["api_token"])

    def test_section_controller(self):
        config = Config()
        config.controller.interval_ms_remote_scan = 1234
        config.controller.interval_ms_local_scan = 5678
        config.controller.interval_ms_downloading_scan = 9012
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("controller", out_dict)
        self.assertEqual(1234, out_dict["controller"]["interval_ms_remote_scan"])
        self.assertEqual(5678, out_dict["controller"]["interval_ms_local_scan"])
        self.assertEqual(9012, out_dict["controller"]["interval_ms_downloading_scan"])
        self.assertEqual(True, out_dict["controller"]["managed_extract_folders_enabled"])

    def test_section_web(self):
        config = Config()
        config.web.port = 8080
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("web", out_dict)
        self.assertEqual(8080, out_dict["web"]["port"])

    def test_section_autoqueue(self):
        config = Config()
        config.autoqueue.enabled = True
        config.autoqueue.patterns_only = False
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("autoqueue", out_dict)
        self.assertEqual(True, out_dict["autoqueue"]["enabled"])
        self.assertEqual(False, out_dict["autoqueue"]["patterns_only"])
        self.assertEqual(False, out_dict["autoqueue"]["auto_delete_remote"])
