# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import sys
import copy
import tempfile
import os
from unittest.mock import MagicMock, patch

from common import overrides, Config
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
        self.assertFalse(Seedsync._detect_incomplete_config(config))

        # Test incomplete configs
        config.lftp.remote_address = incomplete_value
        self.assertTrue(Seedsync._detect_incomplete_config(config))
        config.lftp.remote_address = "value"

        config.lftp.remote_username = incomplete_value
        self.assertTrue(Seedsync._detect_incomplete_config(config))
        config.lftp.remote_username = "value"

        config.lftp.remote_path = incomplete_value
        self.assertTrue(Seedsync._detect_incomplete_config(config))
        config.lftp.remote_path = "value"

        config.lftp.local_path = incomplete_value
        self.assertTrue(Seedsync._detect_incomplete_config(config))
        config.lftp.local_path = "value"

        config.lftp.remote_path_to_scan_script = incomplete_value
        self.assertTrue(Seedsync._detect_incomplete_config(config))
        config.lftp.remote_path_to_scan_script = "value"

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
