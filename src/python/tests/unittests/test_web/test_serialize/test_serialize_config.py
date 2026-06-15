# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import json

from common import Config
from web.serialize import SerializeConfig


class TestSerializeConfig(unittest.TestCase):
    def test_section_general(self):
        config = Config()
        config.general.debug = True
        config.general.config_api_redact_remote_details = True
        config.general.api_token = "super-secret-token"
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("general", out_dict)
        self.assertEqual(True, out_dict["general"]["debug"])
        self.assertEqual(True, out_dict["general"]["config_api_redact_remote_details"])
        self.assertEqual("**REDACTED**", out_dict["general"]["api_token"])
        self.assertNotIn("super-secret-token", out)

    def test_section_lftp(self):
        config = Config()
        config.lftp.remote_address = "server.remote.com"
        config.lftp.remote_username = "user-on-remote-server"
        config.lftp.remote_password = "secret123"
        config.lftp.remote_port = 3456
        config.lftp.remote_path = "/remote/server/path"
        config.lftp.local_path = "/local/server/path"
        config.lftp.remote_path_to_scan_script = "/remote/server/path/to/script"
        config.lftp.num_max_parallel_downloads = 6
        config.lftp.num_max_parallel_files_per_download = 7
        config.lftp.num_max_connections_per_root_file = 2
        config.lftp.num_max_connections_per_dir_file = 3
        config.lftp.num_max_total_connections = 4
        config.lftp.net_socket_buffer = "512K"
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("lftp", out_dict)
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_address"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_username"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_password"])
        self.assertEqual(3456, out_dict["lftp"]["remote_port"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_path"])
        self.assertEqual("/local/server/path", out_dict["lftp"]["local_path"])
        self.assertEqual("/remote/server/path/to/script", out_dict["lftp"]["remote_path_to_scan_script"])
        self.assertEqual(6, out_dict["lftp"]["num_max_parallel_downloads"])
        self.assertEqual(7, out_dict["lftp"]["num_max_parallel_files_per_download"])
        self.assertEqual(2, out_dict["lftp"]["num_max_connections_per_root_file"])
        self.assertEqual(3, out_dict["lftp"]["num_max_connections_per_dir_file"])
        self.assertEqual(4, out_dict["lftp"]["num_max_total_connections"])
        self.assertEqual("512K", out_dict["lftp"]["net_socket_buffer"])
        self.assertNotIn("server.remote.com", out)
        self.assertNotIn("user-on-remote-server", out)
        self.assertNotIn("secret123", out)

    def test_section_lftp_with_remote_detail_redaction_disabled(self):
        config = Config()
        config.general.config_api_redact_remote_details = False
        config.general.api_token = "super-secret-token"
        config.lftp.remote_address = "server.remote.com"
        config.lftp.remote_username = "user-on-remote-server"
        config.lftp.remote_password = "secret123"
        config.lftp.remote_port = 3456
        config.lftp.remote_path = "/remote/server/path"
        config.lftp.local_path = "/local/server/path"
        config.lftp.remote_path_to_scan_script = "/remote/server/path/to/script"
        config.lftp.net_socket_buffer = "8M"
        out = SerializeConfig.config(config)
        out_dict = json.loads(out)
        self.assertIn("lftp", out_dict)
        self.assertEqual(False, out_dict["general"]["config_api_redact_remote_details"])
        self.assertEqual("server.remote.com", out_dict["lftp"]["remote_address"])
        self.assertEqual("user-on-remote-server", out_dict["lftp"]["remote_username"])
        self.assertEqual("**REDACTED**", out_dict["lftp"]["remote_password"])
        self.assertEqual(3456, out_dict["lftp"]["remote_port"])
        self.assertEqual("/remote/server/path", out_dict["lftp"]["remote_path"])
        self.assertEqual("/local/server/path", out_dict["lftp"]["local_path"])
        self.assertEqual("/remote/server/path/to/script", out_dict["lftp"]["remote_path_to_scan_script"])
        self.assertEqual("8M", out_dict["lftp"]["net_socket_buffer"])
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
