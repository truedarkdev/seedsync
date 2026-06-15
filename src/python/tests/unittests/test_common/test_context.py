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
        args.web_bind_host = "127.0.0.1"

        self.assertEqual(
            {
                "local_path_to_scanfs": "/usr/bin/scanfs",
                "html_path": "/tmp/ui",
                "debug": "True",
                "exit": "False",
                "web_bind_host": "127.0.0.1",
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
        breadcrumb_trace = MagicMock()

        context = Context(
            logger,
            web_access_logger,
            config,
            args,
            status,
            path_pair_manager,
            breadcrumb_trace=breadcrumb_trace,
        )
        child_context = context.create_child_context("worker")

        self.assertIsNot(context, child_context)
        logger.getChild.assert_called_once_with("worker")
        self.assertIs(child_logger, child_context.logger)
        self.assertIs(web_access_logger, child_context.web_access_logger)
        self.assertIs(config, child_context.config)
        self.assertIs(args, child_context.args)
        self.assertIs(status, child_context.status)
        self.assertIs(path_pair_manager, child_context.path_pair_manager)
        self.assertIs(breadcrumb_trace, child_context.breadcrumb_trace)

    def test_context_provides_breadcrumb_trace_collector(self):
        logger = MagicMock()
        web_access_logger = MagicMock()
        config = MagicMock()
        config.general.breadcrumb_trace_enabled = True
        args = Args()
        status = Status()

        context = Context(logger, web_access_logger, config, args, status)
        context.breadcrumb_trace.record("controller", "start", {"phase": "init"})

        snapshot = context.breadcrumb_trace.snapshot()
        self.assertEqual(1, snapshot["entry_count"])
        self.assertEqual(1, len(snapshot["entries"]))
        self.assertEqual("controller", snapshot["entries"][0]["source"])
        self.assertEqual("start", snapshot["entries"][0]["message"])

    def test_context_uses_configured_breadcrumb_retention_depth(self):
        logger = MagicMock()
        web_access_logger = MagicMock()
        config = MagicMock()
        config.general.breadcrumb_trace_enabled = True
        config.general.breadcrumb_trace_retention_depth = 64
        args = Args()
        status = Status()

        context = Context(logger, web_access_logger, config, args, status)
        self.assertEqual(64, context.breadcrumb_trace.max_entries)

    def test_print_to_log_emits_config_and_args(self):
        logger = MagicMock()
        config = MagicMock()
        config.as_dict.return_value = {
            "general": {
                "debug": True,
                "verbose": False,
                "api_token": "super-secret-token",
                "webhook_secret": "super-secret-webhook-secret",
                "breadcrumb_trace_enabled": False,
            }
        }
        args = Args()
        args.local_path_to_scanfs = "/usr/bin/scanfs"
        args.html_path = "/tmp/ui"
        args.debug = True
        args.exit = None
        args.web_bind_host = "127.0.0.1"

        context = Context(logger, MagicMock(), config, args, Status())
        context.print_to_log()

        self.assertEqual(
            [
                call("Config:"),
                call("  general.debug: True"),
                call("  general.verbose: False"),
                call("  general.api_token: **REDACTED**"),
                call("  general.webhook_secret: **REDACTED**"),
                call("  general.breadcrumb_trace_enabled: False"),
                call("Args:"),
                call("  local_path_to_scanfs: /usr/bin/scanfs"),
                call("  html_path: /tmp/ui"),
                call("  debug: True"),
                call("  exit: None"),
                call("  web_bind_host: 127.0.0.1"),
            ],
            logger.debug.call_args_list
        )

    def test_print_to_log_redacts_lftp_remote_password(self):
        logger = MagicMock()
        config = MagicMock()
        config.as_dict.return_value = {
            "General": {
                "debug": True,
            },
            "Lftp": {
                "remote_address": "seedbox.example.com",
                "remote_username": "seeduser",
                "remote_password": "super-secret-ssh-password",
            },
        }
        args = Args()
        args.local_path_to_scanfs = "/usr/bin/scanfs"
        args.html_path = "/tmp/ui"
        args.debug = True
        args.exit = None
        args.web_bind_host = "127.0.0.1"

        context = Context(logger, MagicMock(), config, args, Status())
        context.print_to_log()

        self.assertEqual(
            [
                call("Config:"),
                call("  General.debug: True"),
                call("  Lftp.remote_address: seedbox.example.com"),
                call("  Lftp.remote_username: seeduser"),
                call("  Lftp.remote_password: ********"),
                call("Args:"),
                call("  local_path_to_scanfs: /usr/bin/scanfs"),
                call("  html_path: /tmp/ui"),
                call("  debug: True"),
                call("  exit: None"),
                call("  web_bind_host: 127.0.0.1"),
            ],
            logger.debug.call_args_list
        )

    def test_print_to_log_leaves_empty_or_missing_remote_password_blank(self):
        for remote_password in ("", None):
            with self.subTest(remote_password=remote_password):
                logger = MagicMock()
                config = MagicMock()
                config.as_dict.return_value = {
                    "General": {
                        "debug": True,
                    },
                    "Lftp": {
                        "remote_address": "seedbox.example.com",
                        "remote_password": remote_password,
                    },
                }
                args = Args()

                context = Context(logger, MagicMock(), config, args, Status())
                context.print_to_log()

                self.assertEqual(
                    [
                        call("Config:"),
                        call("  General.debug: True"),
                        call("  Lftp.remote_address: seedbox.example.com"),
                        call("  Lftp.remote_password: "),
                        call("Args:"),
                        call("  local_path_to_scanfs: None"),
                        call("  html_path: None"),
                        call("  debug: None"),
                        call("  exit: None"),
                        call("  web_bind_host: None"),
                    ],
                    logger.debug.call_args_list
                )
