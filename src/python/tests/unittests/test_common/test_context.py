# Copyright 2026, SeedSync Contributors, All rights reserved.

import unittest
from unittest.mock import MagicMock, call

from common.context import Args, Context
from common.status import Status


class TestArgs(unittest.TestCase):
    def test_as_dict_stringifies_values(self):
        args = Args()
        args.local_path_to_scanfs = "/usr/bin/scanfs"
        args.html_path = "/tmp/ui"
        args.debug = True
        args.exit = False

        self.assertEqual(
            {
                "local_path_to_scanfs": "/usr/bin/scanfs",
                "html_path": "/tmp/ui",
                "debug": "True",
                "exit": "False",
            },
            args.as_dict()
        )


class TestContext(unittest.TestCase):
    def test_create_child_context_updates_only_logger(self):
        logger = MagicMock()
        child_logger = MagicMock()
        logger.getChild.return_value = child_logger
        web_access_logger = MagicMock()
        config = MagicMock()
        args = Args()
        status = Status()
        path_pair_manager = MagicMock()

        context = Context(logger, web_access_logger, config, args, status, path_pair_manager)
        child_context = context.create_child_context("worker")

        self.assertIsNot(context, child_context)
        logger.getChild.assert_called_once_with("worker")
        self.assertIs(child_logger, child_context.logger)
        self.assertIs(web_access_logger, child_context.web_access_logger)
        self.assertIs(config, child_context.config)
        self.assertIs(args, child_context.args)
        self.assertIs(status, child_context.status)
        self.assertIs(path_pair_manager, child_context.path_pair_manager)

    def test_print_to_log_emits_config_and_args(self):
        logger = MagicMock()
        config = MagicMock()
        config.as_dict.return_value = {
            "general": {
                "debug": True,
                "verbose": False,
                "api_token": "super-secret-token",
                "webhook_secret": "super-secret-webhook-secret",
            }
        }
        args = Args()
        args.local_path_to_scanfs = "/usr/bin/scanfs"
        args.html_path = "/tmp/ui"
        args.debug = True
        args.exit = None

        context = Context(logger, MagicMock(), config, args, Status())
        context.print_to_log()

        self.assertEqual(
            [
                call("Config:"),
                call("  general.debug: True"),
                call("  general.verbose: False"),
                call("  general.api_token: **REDACTED**"),
                call("  general.webhook_secret: **REDACTED**"),
                call("Args:"),
                call("  local_path_to_scanfs: /usr/bin/scanfs"),
                call("  html_path: /tmp/ui"),
                call("  debug: True"),
                call("  exit: None"),
            ],
            logger.debug.call_args_list
        )
