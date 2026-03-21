# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import sys
import copy
import tempfile
import os
import shutil
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from common import overrides, Config, PathPairManager, PathPair
from seedsync import Seedsync


class TestSeedsync(unittest.TestCase):
    def test_args_config(self):
        argv = []
        argv.append("-c")
        argv.append("/path/to/config")
        argv.append("--html")
        argv.append("/path/to/html")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertEqual("/path/to/config", args.config_dir)

        argv = []
        argv.append("--config_dir")
        argv.append("/path/to/config")
        argv.append("--html")
        argv.append("/path/to/html")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertEqual("/path/to/config", args.config_dir)

        argv = []
        with self.assertRaises(SystemExit):
            Seedsync._parse_args(argv)

    def test_args_html(self):
        argv = []
        argv.append("-c")
        argv.append("/path/to/config")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        argv.append("--html")
        argv.append("/path/to/html")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertEqual("/path/to/html", args.html)

    def test_args_scanfs(self):
        argv = []
        argv.append("-c")
        argv.append("/path/to/config")
        argv.append("--html")
        argv.append("/path/to/html")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertEqual("/path/to/scanfs", args.scanfs)

    def test_args_logdir(self):
        argv = []
        argv.append("-c")
        argv.append("/path/to/config")
        argv.append("--logdir")
        argv.append("/path/to/logdir")
        argv.append("--html")
        argv.append("/path/to/html")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertEqual("/path/to/logdir", args.logdir)

        argv = []
        argv.append("-c")
        argv.append("/path/to/config")
        argv.append("--html")
        argv.append("/path/to/html")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertIsNone(args.logdir)

    def test_args_debug(self):
        argv = []
        argv.append("-c")
        argv.append("/path/to/config")
        argv.append("--html")
        argv.append("/path/to/html")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        argv.append("-d")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertTrue(args.debug)

        argv = []
        argv.append("-c")
        argv.append("/path/to/config")
        argv.append("--debug")
        argv.append("--html")
        argv.append("/path/to/html")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertTrue(args.debug)

        argv = []
        argv.append("-c")
        argv.append("/path/to/config")
        argv.append("--html")
        argv.append("/path/to/html")
        argv.append("--scanfs")
        argv.append("/path/to/scanfs")
        args = Seedsync._parse_args(argv)
        self.assertIsNotNone(args)
        self.assertFalse(args.debug)

    def test_default_config(self):
        config = Seedsync._create_default_config()
        # Test that default config doesn't have any uninitialized values
        config_dict = config.as_dict()
        for section, inner_config in config_dict.items():
            for key in inner_config:
                self.assertIsNotNone(inner_config[key],
                                     msg="{}.{} is uninitialized".format(section, key))

        # Test that default config is a valid config
        config_dict = config.as_dict()
        config2 = Config.from_dict(config_dict)
        config2_dict = config2.as_dict()
        self.assertEqual(config_dict, config2_dict)

    def test_detect_incomplete_config(self):
        # Test a complete config
        config = Seedsync._create_default_config()
        incomplete_value = config.lftp.remote_address
        config.lftp.remote_address = "value"
        config.lftp.remote_password = "value"
        config.lftp.remote_username = "value"
        config.lftp.remote_path = "value"
        config.lftp.local_path = "value"
        config.lftp.remote_path_to_scan_script = "value"
        self.assertEqual([], Seedsync._detect_incomplete_config(config))

        # Test incomplete configs
        config.lftp.remote_address = incomplete_value
        self.assertEqual(["Lftp.remote_address"], Seedsync._detect_incomplete_config(config))
        config.lftp.remote_address = "value"

        config.lftp.remote_username = incomplete_value
        self.assertEqual(["Lftp.remote_username"], Seedsync._detect_incomplete_config(config))
        config.lftp.remote_username = "value"

        config.lftp.remote_path = incomplete_value
        self.assertEqual(["Lftp.remote_path"], Seedsync._detect_incomplete_config(config))
        config.lftp.remote_path = "value"

        config.lftp.local_path = incomplete_value
        self.assertEqual(["Lftp.local_path"], Seedsync._detect_incomplete_config(config))
        config.lftp.local_path = "value"

        config.lftp.remote_path_to_scan_script = incomplete_value
        self.assertEqual(["Lftp.remote_path_to_scan_script"], Seedsync._detect_incomplete_config(config))
        config.lftp.remote_path_to_scan_script = "value"

    def test_detect_incomplete_config_skips_legacy_paths_when_path_pairs_exist(self):
        config = Seedsync._create_default_config()
        config.lftp.remote_address = "value"
        config.lftp.remote_password = "value"
        config.lftp.remote_username = "value"
        config.lftp.remote_path_to_scan_script = "value"

        manager = PathPairManager(tempfile.mkdtemp(prefix="test_path_pairs"))
        try:
            manager.load()
            manager.add_pair(PathPair(name="Movies", remote_path="/remote/movies", local_path="/downloads/movies"))

            self.assertEqual([], Seedsync._detect_incomplete_config(config, manager))
        finally:
            shutil.rmtree(manager._config_dir)

    def test_emit_startup_warnings_warns_when_api_token_is_blank(self):
        config = Seedsync._create_default_config()
        config.general.api_token = ""
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config)

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("No API token configured" in message for message in warning_messages))
        self.assertTrue(any("0.0.0.0" in message for message in warning_messages))
        self.assertEqual(2, logger.warning.call_count)

    def test_emit_startup_warnings_omits_public_bind_warning_for_localhost_bind(self):
        config = Seedsync._create_default_config()
        config.general.api_token = ""
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config, web_bind_host="127.0.0.1")

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("No API token configured" in message for message in warning_messages))
        self.assertFalse(any("0.0.0.0" in message for message in warning_messages))
        self.assertEqual(1, logger.warning.call_count)

    def test_emit_startup_warnings_no_warnings_when_api_token_is_set(self):
        config = Seedsync._create_default_config()
        config.general.api_token = "configured-token"
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config)

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("general.api_token is currently only stored in config" in message
                            for message in warning_messages))
        self.assertTrue(any("0.0.0.0" in message for message in warning_messages))
        self.assertEqual(1, logger.warning.call_count)

    def test_emit_startup_warnings_skips_webhook_secret_warning_when_field_absent(self):
        config = SimpleNamespace(general=SimpleNamespace(api_token="configured-token"))
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config, web_bind_host="127.0.0.1")

        logger.warning.assert_not_called()

    def test_emit_startup_warnings_warns_when_webhook_secret_field_exists_and_is_blank(self):
        config = SimpleNamespace(general=SimpleNamespace(api_token="configured-token", webhook_secret=""))
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config, web_bind_host="127.0.0.1")

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("webhook_secret is not configured" in message for message in warning_messages))
        self.assertEqual(1, logger.warning.call_count)

    def test_persist_does_not_rewrite_unchanged_config(self):
        config = Seedsync._create_default_config()
        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = MagicMock()
        seedsync.context.logger = MagicMock()
        seedsync.context.config = config
        seedsync.controller_persist = MagicMock()
        seedsync.auto_queue_persist = MagicMock()
        seedsync.controller_persist_path = "controller.persist"
        seedsync.auto_queue_persist_path = "autoqueue.persist"

        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(config.to_str())
            seedsync.config_path = f.name
        try:
            with patch.object(Seedsync, "_Seedsync__backup_file") as backup_file:
                seedsync.persist()
            backup_file.assert_not_called()
            seedsync.controller_persist.to_file.assert_called_once_with("controller.persist")
            seedsync.auto_queue_persist.to_file.assert_called_once_with("autoqueue.persist")
            with open(seedsync.config_path, "r") as f:
                self.assertEqual(config.to_str(), f.read())
        finally:
            os.remove(seedsync.config_path)

    def test_persist_rewrites_changed_config(self):
        old_config = Seedsync._create_default_config()
        new_config = copy.deepcopy(old_config)
        new_config.general.debug = not old_config.general.debug

        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = MagicMock()
        seedsync.context.logger = MagicMock()
        seedsync.context.config = new_config
        seedsync.controller_persist = MagicMock()
        seedsync.auto_queue_persist = MagicMock()
        seedsync.controller_persist_path = "controller.persist"
        seedsync.auto_queue_persist_path = "autoqueue.persist"

        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(old_config.to_str())
            seedsync.config_path = f.name
        try:
            with patch.object(Seedsync, "_Seedsync__backup_file") as backup_file:
                seedsync.persist()
            backup_file.assert_called_once_with(seedsync.config_path)
            seedsync.controller_persist.to_file.assert_called_once_with("controller.persist")
            seedsync.auto_queue_persist.to_file.assert_called_once_with("autoqueue.persist")
            with open(seedsync.config_path, "r") as f:
                self.assertEqual(new_config.to_str(), f.read())
        finally:
            os.remove(seedsync.config_path)

    def test_persist_recreates_missing_config_without_backup(self):
        config = Seedsync._create_default_config()
        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = MagicMock()
        seedsync.context.logger = MagicMock()
        seedsync.context.config = config
        seedsync.controller_persist = MagicMock()
        seedsync.auto_queue_persist = MagicMock()
        seedsync.controller_persist_path = "controller.persist"
        seedsync.auto_queue_persist_path = "autoqueue.persist"
        seedsync.config_path = tempfile.mktemp(suffix="settings.cfg")

        try:
            with patch.object(Seedsync, "_Seedsync__backup_file") as backup_file:
                seedsync.persist()
            backup_file.assert_not_called()
            seedsync.controller_persist.to_file.assert_called_once_with("controller.persist")
            seedsync.auto_queue_persist.to_file.assert_called_once_with("autoqueue.persist")
            with open(seedsync.config_path, "r") as f:
                self.assertEqual(config.to_str(), f.read())
        finally:
            if os.path.exists(seedsync.config_path):
                os.remove(seedsync.config_path)
