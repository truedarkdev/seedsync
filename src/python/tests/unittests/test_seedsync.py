# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import sys
import copy
import tempfile
import os
import io
import json
import shutil
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from common import overrides, Config, PathPairManager, PathPair, Constants, ServiceExit, AppError
from controller import AutoQueuePattern, AutoQueuePersist
from seedsync import Seedsync
from web.auth_store import ApiKeyStore


def _read_history_entries(file_path):
    history_path = os.path.splitext(file_path)[0] + ".history.jsonl"
    with open(history_path, "r", encoding="utf-8") as handle:
        return [
            json.loads(line)
            for line in handle
            if line.strip()
        ]


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

    def test_args_web_bind_host(self):
        args = Seedsync._parse_args([
            "-c", "/path/to/config",
            "--html", "/path/to/html",
            "--scanfs", "/path/to/scanfs",
            "--web-bind-host", "127.0.0.1",
        ])
        self.assertEqual("127.0.0.1", args.web_bind_host)

        args = Seedsync._parse_args([
            "-c", "/path/to/config",
            "--html", "/path/to/html",
            "--scanfs", "/path/to/scanfs",
        ])
        self.assertEqual("0.0.0.0", args.web_bind_host)

    def test_apply_umask_from_env_skips_empty_or_unset_values(self):
        with patch("seedsync.os.umask") as umask, patch.dict(os.environ, {}, clear=True):
            Seedsync._apply_umask_from_env()
        umask.assert_not_called()

        with patch("seedsync.os.umask") as umask, patch.dict(os.environ, {"UMASK": ""}, clear=True):
            Seedsync._apply_umask_from_env()
        umask.assert_not_called()

    def test_apply_umask_from_env_applies_valid_octal_value(self):
        with patch("seedsync.os.umask") as umask, patch.dict(os.environ, {"UMASK": "002"}, clear=True):
            Seedsync._apply_umask_from_env()

        umask.assert_called_once_with(0o002)

    def test_apply_umask_from_env_exits_on_invalid_value(self):
        for umask_value in ("not-octal", "+022", "-022", "0o22", " 022", "022 ", "022\n", " "):
            with self.subTest(umask_value=umask_value):
                stderr = io.StringIO()
                with patch("seedsync.os.umask") as umask, \
                     patch("seedsync.logging.warning") as warning, \
                     patch("seedsync.sys.stderr", stderr), \
                     patch.dict(os.environ, {"UMASK": umask_value}, clear=True):
                    with self.assertRaises(SystemExit) as ctx:
                        Seedsync._apply_umask_from_env()

                self.assertEqual(1, ctx.exception.code)
                umask.assert_not_called()
                warning.assert_not_called()
                self.assertEqual(
                    "ERROR: invalid UMASK value {!r}; expected octal digits 0-7\n".format(umask_value),
                    stderr.getvalue(),
                )

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

    def test_resolve_log_level_prefers_debug_override(self):
        self.assertEqual("DEBUG", Seedsync._resolve_log_level("INFO", True))
        self.assertEqual("WARNING", Seedsync._resolve_log_level("warning", False))
        self.assertEqual("INFO", Seedsync._resolve_log_level(None, False))

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
        self.assertEqual("", config.general.allowed_hostname)

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

        config.lftp.remote_password = incomplete_value
        self.assertEqual(["Lftp.remote_password"], Seedsync._detect_incomplete_config(config))
        config.lftp.remote_password = "value"

        config.lftp.use_ssh_key = True
        config.lftp.remote_password = incomplete_value
        self.assertEqual([], Seedsync._detect_incomplete_config(config))
        config.lftp.remote_password = "value"
        config.lftp.use_ssh_key = False

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

    def test_detect_incomplete_config_reports_missing_startup_fields_and_args(self):
        config = SimpleNamespace(
            lftp=SimpleNamespace(
                remote_address="remote.server.com",
                remote_username="remote-user",
                remote_password="password",
                remote_port=22,
                remote_path="/remote/path",
                local_path="/local/path",
                remote_path_to_scan_script="/scanfs",
                use_ssh_key=False,
                num_max_parallel_downloads=1,
                num_max_parallel_files_per_download=1,
                num_max_connections_per_root_file=1,
                num_max_connections_per_dir_file=1,
                num_max_total_connections=1,
                use_temp_file=False,
            ),
            controller=SimpleNamespace(
                interval_ms_remote_scan=1000,
                interval_ms_local_scan=1000,
                interval_ms_downloading_scan=1000,
            ),
            general=SimpleNamespace(verbose=False),
            autoqueue=SimpleNamespace(auto_delete_remote=False),
        )
        args = SimpleNamespace(local_path_to_scanfs="/scanfs")

        self.assertEqual([], Seedsync._detect_incomplete_config(config, args=args))

        config.lftp.local_path = None
        self.assertEqual(["Lftp.local_path"], Seedsync._detect_incomplete_config(config, args=args))
        config.lftp.local_path = "/local/path"

        config.lftp.remote_address = None
        config.controller.interval_ms_remote_scan = None
        config.general.verbose = None
        config.autoqueue.auto_delete_remote = None
        args.local_path_to_scanfs = None
        self.assertEqual(
            [
                "Lftp.remote_address",
                "Controller.interval_ms_remote_scan",
                "General.verbose",
                "AutoQueue.auto_delete_remote",
                "Args.local_path_to_scanfs",
            ],
            Seedsync._detect_incomplete_config(config, args=args)
        )

    def test_detect_incomplete_config_requires_remote_username_even_with_ssh_key(self):
        config = SimpleNamespace(
            lftp=SimpleNamespace(
                remote_address="remote.server.com",
                remote_username=None,
                remote_password=None,
                remote_port=22,
                remote_path="/remote/path",
                local_path="/local/path",
                remote_path_to_scan_script="/scanfs",
                use_ssh_key=True,
                num_max_parallel_downloads=1,
                num_max_parallel_files_per_download=1,
                num_max_connections_per_root_file=1,
                num_max_connections_per_dir_file=1,
                num_max_total_connections=1,
                use_temp_file=False,
            ),
            controller=SimpleNamespace(
                interval_ms_remote_scan=1000,
                interval_ms_local_scan=1000,
                interval_ms_downloading_scan=1000,
            ),
            general=SimpleNamespace(verbose=False),
            autoqueue=SimpleNamespace(auto_delete_remote=False),
        )
        args = SimpleNamespace(local_path_to_scanfs="/scanfs")

        self.assertEqual(["Lftp.remote_username"], Seedsync._detect_incomplete_config(config, args=args))

    def test_detect_incomplete_config_skips_legacy_paths_when_path_pairs_exist(self):
        config = Seedsync._create_default_config()
        config.lftp.remote_address = "value"
        config.lftp.remote_password = "value"
        config.lftp.remote_username = "value"
        config.lftp.remote_path_to_scan_script = "value"

        manager = PathPairManager(tempfile.mkdtemp(prefix="test_path_pairs"))
        try:
            manager.load()
            manager.add_pair(PathPair(name="Movies", remote_path="/remote/movies", local_path="/downloads/movies", enabled=True))

            self.assertEqual([], Seedsync._detect_incomplete_config(config, manager))
        finally:
            shutil.rmtree(manager._config_dir)

    def test_detect_incomplete_config_does_not_skip_legacy_paths_for_disabled_path_pairs(self):
        config = Seedsync._create_default_config()
        config.lftp.remote_address = "value"
        config.lftp.remote_password = "value"
        config.lftp.remote_username = "value"
        config.lftp.remote_path_to_scan_script = "value"

        manager = PathPairManager(tempfile.mkdtemp(prefix="test_path_pairs"))
        try:
            manager.load()
            manager.add_pair(PathPair(name="Movies", remote_path="/remote/movies", local_path="/downloads/movies", enabled=False))

            self.assertEqual(
                ["Lftp.remote_path", "Lftp.local_path"],
                Seedsync._detect_incomplete_config(config, manager)
            )
        finally:
            shutil.rmtree(manager._config_dir)

    def test_start_jobs_waits_for_controller_setup_before_webapp_start(self):
        context = SimpleNamespace(
            logger=MagicMock(),
            status=SimpleNamespace(server=SimpleNamespace(up=True, error_msg=None))
        )
        controller_job = MagicMock()
        webapp_job = MagicMock()
        call_order = []

        controller_job.start.side_effect = lambda: call_order.append("controller.start")
        def wait_until_setup_complete(timeout=None):
            call_order.append("controller.wait_until_setup_complete")
            return True

        controller_job.wait_until_setup_complete.side_effect = wait_until_setup_complete
        webapp_job.start.side_effect = lambda: call_order.append("webapp.start")

        controller_start_failed, controller_start_isolated = Seedsync._Seedsync__start_jobs(
            context, True, controller_job, webapp_job
        )

        self.assertFalse(controller_start_failed)
        self.assertFalse(controller_start_isolated)
        self.assertTrue(controller_job.daemon)
        controller_job.wait_until_setup_complete.assert_called_once_with(
            timeout=Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
        )
        self.assertEqual(
            [
                "controller.start",
                "controller.wait_until_setup_complete",
                "webapp.start"
            ],
            call_order
        )

    def test_start_jobs_continues_with_webapp_when_controller_setup_times_out(self):
        context = SimpleNamespace(
            logger=MagicMock(),
            status=SimpleNamespace(server=SimpleNamespace(up=True, error_msg=None))
        )
        controller_job = MagicMock()
        webapp_job = MagicMock()

        controller_job.start.return_value = None
        controller_job.wait_until_setup_complete.return_value = False

        controller_start_failed, controller_start_isolated = Seedsync._Seedsync__start_jobs(
            context, True, controller_job, webapp_job
        )

        self.assertFalse(controller_start_failed)
        self.assertTrue(controller_start_isolated)
        self.assertTrue(controller_job.daemon)
        controller_job.wait_until_setup_complete.assert_called_once_with(
            timeout=Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
        )
        webapp_job.start.assert_called_once_with()
        controller_job.propagate_exception.assert_not_called()
        self.assertFalse(context.status.server.up)
        self.assertIn("timed out", context.status.server.error_msg)
        context.logger.error.assert_called_once()

    def test_timeout_status_clears_only_for_matching_timeout_recovery(self):
        context = SimpleNamespace(
            logger=MagicMock(),
            status=SimpleNamespace(
                server=SimpleNamespace(
                    up=False,
                    error_msg="Controller startup timed out after {} seconds; continuing with web UI".format(
                        Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
                    )
                )
            )
        )
        controller_job = MagicMock()
        controller_job.exc_info = None
        controller_job.wait_until_setup_complete.return_value = True

        controller_start_isolated = Seedsync._Seedsync__handle_controller_startup_timeout(
            context,
            controller_job,
            True
        )

        self.assertFalse(controller_start_isolated)
        controller_job.wait_until_setup_complete.assert_called_once_with(timeout=0)
        self.assertTrue(context.status.server.up)
        self.assertIsNone(context.status.server.error_msg)
        context.logger.info.assert_called_once_with("Controller startup recovered after timeout")

    def test_load_persist_records_auth_store_recovery_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "api-keys.json")
            sensitive_marker = "raw-secret-should-not-appear"
            with open(store_path, "w", encoding="utf-8") as handle:
                handle.write('{"api_keys":[{"secret_hash":"%s"}' % sensitive_marker)

            store = Seedsync._load_persist(ApiKeyStore, store_path)

            self.assertIsInstance(store, ApiKeyStore)
            self.assertEqual(0, len(store.list_api_keys()))

            backup_path = os.path.join(temp_dir, "api-keys.json.1.bak")
            self.assertTrue(os.path.isfile(backup_path))

            history_entries = _read_history_entries(store_path)
            recovery_entries = [
                entry for entry in history_entries
                if entry["event"] == "store_load_failed"
            ]
            self.assertEqual(1, len(recovery_entries))
            self.assertEqual("persist_error_fallback", recovery_entries[0]["reason"])
            self.assertEqual("PersistError", recovery_entries[0]["details"]["error_type"])
            self.assertEqual(backup_path, recovery_entries[0]["details"]["backup_path"])
            self.assertEqual("fresh_store", recovery_entries[0]["details"]["fallback"])
            history_path = os.path.splitext(store_path)[0] + ".history.jsonl"
            with open(history_path, "r", encoding="utf-8") as handle:
                history_text = handle.read()
            self.assertNotIn(sensitive_marker, history_text)

    def test_persist_uses_api_key_store_save_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "api-keys.json")
            config_path = os.path.join(temp_dir, "settings.cfg")
            store = ApiKeyStore(file_path=store_path)
            store.create_api_key("admin", ["admin"])

            initial_history = _read_history_entries(store_path)
            initial_save_count = len([
                entry for entry in initial_history
                if entry["event"] == "store_saved"
            ])

            seedsync = Seedsync.__new__(Seedsync)
            seedsync.context = SimpleNamespace(
                logger=MagicMock(),
                config=SimpleNamespace(to_str=MagicMock(return_value="{}")),
            )
            seedsync.controller_persist = MagicMock()
            seedsync.auto_queue_persist = MagicMock()
            seedsync.controller_persist_path = os.path.join(temp_dir, "controller.persist")
            seedsync.auto_queue_persist_path = os.path.join(temp_dir, "autoqueue.persist")
            seedsync.api_key_store = store
            seedsync.api_key_store_path = store_path
            seedsync.config_path = config_path

            seedsync.persist()

            updated_history = _read_history_entries(store_path)
            updated_save_count = len([
                entry for entry in updated_history
                if entry["event"] == "store_saved"
            ])
            self.assertEqual(initial_save_count + 1, updated_save_count)
            self.assertTrue(seedsync.controller_persist.to_file.called)
            self.assertTrue(seedsync.auto_queue_persist.to_file.called)

    def test_persist_logs_auto_queue_write_failures_and_continues(self):
        old_config = Seedsync._create_default_config()
        new_config = copy.deepcopy(old_config)
        new_config.general.log_level = "DEBUG" if old_config.general.log_level != "DEBUG" else "INFO"

        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = SimpleNamespace(
            logger=MagicMock(),
            config=new_config,
        )
        seedsync.controller_persist = MagicMock()
        seedsync.auto_queue_persist = AutoQueuePersist()
        queued_pattern = AutoQueuePattern("queued")
        seedsync.auto_queue_persist.add_pattern(queued_pattern)
        seedsync.controller_persist_path = "controller.persist"
        seedsync.auto_queue_persist_path = "autoqueue.persist"
        seedsync.api_key_store = MagicMock()
        seedsync.api_key_store_path = "api-keys.json"

        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(old_config.to_str())
            seedsync.config_path = f.name
        try:
            with patch.object(seedsync.auto_queue_persist, "to_file", side_effect=OSError("disk full")) as auto_queue_to_file:
                seedsync.persist()

            seedsync.controller_persist.to_file.assert_called_once_with("controller.persist")
            auto_queue_to_file.assert_called_once_with("autoqueue.persist")
            seedsync.api_key_store.save.assert_called_once_with()
            seedsync.context.logger.exception.assert_called_once_with("Failed to persist auto-queue state")
            self.assertIn(queued_pattern, seedsync.auto_queue_persist.patterns)
            with open(seedsync.config_path, "r") as f:
                self.assertEqual(new_config.to_str(), f.read())
        finally:
            os.remove(seedsync.config_path)

    def test_timeout_status_keeps_later_controller_failure_degraded(self):
        context = SimpleNamespace(
            logger=MagicMock(),
            status=SimpleNamespace(
                server=SimpleNamespace(
                    up=False,
                    error_msg="Controller startup timed out after {} seconds; continuing with web UI".format(
                        Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
                    )
                )
            )
        )
        controller_job = MagicMock()
        controller_job.exc_info = (PermissionError, PermissionError("permission denied"), None)

        controller_start_isolated = Seedsync._Seedsync__handle_controller_startup_timeout(
            context,
            controller_job,
            True
        )

        self.assertTrue(controller_start_isolated)
        self.assertFalse(context.status.server.up)
        self.assertEqual("permission denied", context.status.server.error_msg)
        context.logger.error.assert_called_once()

    def test_start_jobs_marks_immediate_controller_setup_failure(self):
        context = SimpleNamespace(
            logger=MagicMock(),
            status=SimpleNamespace(server=SimpleNamespace(up=True, error_msg=None))
        )
        controller_job = MagicMock()
        webapp_job = MagicMock()

        controller_job.start.return_value = None
        controller_job.wait_until_setup_complete.return_value = True
        controller_job.exc_info = (PermissionError, PermissionError("permission denied"), None)

        controller_start_failed, controller_start_isolated = Seedsync._Seedsync__start_jobs(
            context, True, controller_job, webapp_job
        )

        self.assertTrue(controller_start_failed)
        self.assertFalse(controller_start_isolated)
        self.assertTrue(controller_job.daemon)
        controller_job.wait_until_setup_complete.assert_called_once_with(
            timeout=Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
        )
        webapp_job.start.assert_called_once_with()
        self.assertFalse(context.status.server.up)
        self.assertEqual("permission denied", context.status.server.error_msg)
        context.logger.error.assert_called_once()

    def test_start_jobs_skips_controller_when_disabled(self):
        context = SimpleNamespace(
            logger=MagicMock(),
            status=SimpleNamespace(server=SimpleNamespace(up=True, error_msg=None))
        )
        controller_job = MagicMock()
        webapp_job = MagicMock()

        controller_start_failed, controller_start_isolated = Seedsync._Seedsync__start_jobs(
            context, False, controller_job, webapp_job
        )

        self.assertFalse(controller_start_failed)
        self.assertFalse(controller_start_isolated)
        controller_job.start.assert_not_called()
        controller_job.wait_until_setup_complete.assert_not_called()
        webapp_job.start.assert_called_once_with()

    def test_emit_startup_warnings_warns_when_api_token_is_blank(self):
        config = Seedsync._create_default_config()
        config.general.api_token = ""
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config)

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("no scoped API keys are configured" in message for message in warning_messages))
        self.assertTrue(any("external /server/* access is not enabled" in message for message in warning_messages))
        self.assertTrue(any("0.0.0.0" in message for message in warning_messages))
        self.assertEqual(2, logger.warning.call_count)

    def test_emit_startup_warnings_omits_public_bind_warning_for_localhost_bind(self):
        config = Seedsync._create_default_config()
        config.general.api_token = ""
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config, web_bind_host="127.0.0.1")

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("no scoped API keys are configured" in message for message in warning_messages))
        self.assertFalse(any("0.0.0.0" in message for message in warning_messages))
        self.assertEqual(1, logger.warning.call_count)

    def test_emit_startup_warnings_no_warnings_when_api_token_is_set(self):
        config = Seedsync._create_default_config()
        config.general.api_token = "configured-token"
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config)

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("general.api_token is configured" in message for message in warning_messages))
        self.assertTrue(any("admin endpoints require scoped API keys" in message for message in warning_messages))
        self.assertTrue(any("0.0.0.0" in message for message in warning_messages))
        self.assertEqual(2, logger.warning.call_count)

    def test_run_exits_early_in_bootstrap_mode(self):
        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = SimpleNamespace(
            logger=MagicMock(),
            config=SimpleNamespace(),
            args=SimpleNamespace(exit=True),
        )
        seedsync.persist = MagicMock()

        with patch("seedsync.Seedsync._emit_startup_warnings"), \
             patch("seedsync.Controller") as mock_controller, \
             patch("seedsync.AutoQueue") as mock_auto_queue, \
             patch("seedsync.WebAppBuilder") as mock_web_app_builder, \
             patch("seedsync.ControllerJob") as mock_controller_job, \
             patch("seedsync.WebAppJob") as mock_webapp_job, \
             self.assertRaises(ServiceExit):
            seedsync.run()

        mock_controller.assert_not_called()
        mock_auto_queue.assert_not_called()
        mock_web_app_builder.assert_not_called()
        mock_controller_job.assert_not_called()
        mock_webapp_job.assert_not_called()
        seedsync.persist.assert_called_once_with()
        seedsync.context.logger.info.assert_any_call(
            "Bootstrap mode requested; persisting defaults and exiting before startup"
        )

    def test_run_bootstrap_mode_skips_incomplete_config_validation(self):
        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = SimpleNamespace(
            logger=MagicMock(),
            config=SimpleNamespace(),
            args=SimpleNamespace(exit=True),
        )
        seedsync.persist = MagicMock()

        with patch("seedsync.Seedsync._emit_startup_warnings"), \
             patch("seedsync.Seedsync._detect_incomplete_config") as mock_detect_incomplete_config, \
             patch("seedsync.Controller") as mock_controller, \
             patch("seedsync.AutoQueue") as mock_auto_queue, \
             patch("seedsync.WebAppBuilder") as mock_web_app_builder, \
             patch("seedsync.ControllerJob") as mock_controller_job, \
             patch("seedsync.WebAppJob") as mock_webapp_job, \
             self.assertRaises(ServiceExit):
            seedsync.run()

        mock_detect_incomplete_config.assert_not_called()
        mock_controller.assert_not_called()
        mock_auto_queue.assert_not_called()
        mock_web_app_builder.assert_not_called()
        mock_controller_job.assert_not_called()
        mock_webapp_job.assert_not_called()
        seedsync.persist.assert_called_once_with()
        seedsync.context.logger.info.assert_any_call(
            "Bootstrap mode requested; persisting defaults and exiting before startup"
        )

    def test_run_skips_controller_join_when_setup_times_out(self):
        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = SimpleNamespace(
            logger=MagicMock(),
            web_access_logger=MagicMock(),
            config=SimpleNamespace(
                lftp=SimpleNamespace(
                    remote_password="pw",
                    use_ssh_key=False,
                    remote_address="addr",
                    remote_port=21,
                    remote_username="user",
                    remote_path="/remote",
                    local_path="/local",
                    remote_path_to_scan_script="/scan",
                    use_temp_file=False,
                    rate_limit=None,
                ),
                controller=SimpleNamespace(
                    interval_ms_downloading_scan=1,
                    interval_ms_local_scan=1,
                    interval_ms_remote_scan=1,
                    use_local_path_as_extract_path=False,
                    extract_path="/extract",
                ),
                general=SimpleNamespace(log_level="INFO", verbose=False),
                web=SimpleNamespace(port=8800),
            ),
            args=SimpleNamespace(exit=False, debug=False, local_path_to_scanfs="/scan"),
            status=SimpleNamespace(server=SimpleNamespace(up=True, error_msg=None)),
            path_pair_manager=None,
            create_child_context=MagicMock(side_effect=lambda name: SimpleNamespace(logger=MagicMock())),
        )
        seedsync.controller_persist = MagicMock()
        seedsync.auto_queue_persist = MagicMock()
        seedsync.persist = MagicMock()

        controller = MagicMock()
        auto_queue = MagicMock()
        web_app = MagicMock()
        web_app_builder = MagicMock()
        web_app_builder.build.return_value = web_app
        web_app_builder.server_handler.is_restart_requested.return_value = False

        controller_job = MagicMock()
        controller_job.wait_until_setup_complete.return_value = False
        controller_job.is_setup_complete.return_value = False
        webapp_job = MagicMock()

        with patch("seedsync.Seedsync._emit_startup_warnings"), \
             patch("seedsync.Seedsync._detect_incomplete_config", return_value=[]), \
             patch("seedsync.Controller", return_value=controller), \
             patch("seedsync.AutoQueue", return_value=auto_queue), \
             patch("seedsync.WebAppBuilder", return_value=web_app_builder), \
             patch("seedsync.ControllerJob", return_value=controller_job) as mock_controller_job, \
             patch("seedsync.WebAppJob", return_value=webapp_job) as mock_webapp_job, \
             patch("seedsync.time.sleep", side_effect=[ServiceExit(), None]):
            mock_controller_job.__name__ = "ControllerJob"
            mock_webapp_job.__name__ = "WebAppJob"
            with self.assertRaises(ServiceExit):
                seedsync.run()

        controller_job.terminate.assert_called_once_with()
        controller_job.join.assert_not_called()
        webapp_job.terminate.assert_called_once_with()
        webapp_job.join.assert_called_once_with()

    def test_run_propagates_controller_app_error_after_startup(self):
        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = SimpleNamespace(
            logger=MagicMock(),
            web_access_logger=MagicMock(),
            config=SimpleNamespace(
                lftp=SimpleNamespace(
                    remote_password="pw",
                    use_ssh_key=False,
                    remote_address="addr",
                    remote_port=21,
                    remote_username="user",
                    remote_path="/remote",
                    local_path="/local",
                    remote_path_to_scan_script="/scan",
                    use_temp_file=False,
                    rate_limit=None,
                ),
                controller=SimpleNamespace(
                    interval_ms_downloading_scan=1,
                    interval_ms_local_scan=1,
                    interval_ms_remote_scan=1,
                    use_local_path_as_extract_path=False,
                    extract_path="/extract",
                ),
                general=SimpleNamespace(log_level="INFO", verbose=False),
                web=SimpleNamespace(port=8800),
            ),
            args=SimpleNamespace(exit=False, debug=False, local_path_to_scanfs="/scan"),
            status=SimpleNamespace(server=SimpleNamespace(up=True, error_msg=None)),
            path_pair_manager=None,
            create_child_context=MagicMock(side_effect=lambda name: SimpleNamespace(logger=MagicMock())),
        )
        seedsync.controller_persist = MagicMock()
        seedsync.auto_queue_persist = MagicMock()
        seedsync.persist = MagicMock()

        controller = MagicMock()
        auto_queue = MagicMock()
        web_app = MagicMock()
        web_app_builder = MagicMock()
        web_app_builder.build.return_value = web_app
        web_app_builder.server_handler.is_restart_requested.return_value = False

        controller_job = MagicMock()
        controller_job.wait_until_setup_complete.return_value = True
        controller_job.is_setup_complete.return_value = True
        controller_job.propagate_exception.side_effect = AppError("controller died")
        webapp_job = MagicMock()

        with patch("seedsync.Seedsync._emit_startup_warnings"), \
             patch("seedsync.Seedsync._detect_incomplete_config", return_value=[]), \
             patch("seedsync.Controller", return_value=controller), \
             patch("seedsync.AutoQueue", return_value=auto_queue), \
             patch("seedsync.WebAppBuilder", return_value=web_app_builder), \
             patch("seedsync.ControllerJob", return_value=controller_job) as mock_controller_job, \
             patch("seedsync.WebAppJob", return_value=webapp_job) as mock_webapp_job, \
             patch("seedsync.time.sleep", return_value=None), \
             self.assertRaises(AppError):
            mock_controller_job.__name__ = "ControllerJob"
            mock_webapp_job.__name__ = "WebAppJob"
            seedsync.run()

        controller_job.wait_until_setup_complete.assert_called_once_with(
            timeout=Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
        )
        controller_job.propagate_exception.assert_called_once_with()
        webapp_job.propagate_exception.assert_called_once_with()
        controller_job.terminate.assert_called_once_with()
        controller_job.join.assert_called_once_with()
        webapp_job.terminate.assert_called_once_with()
        webapp_job.join.assert_called_once_with()
        seedsync.persist.assert_called_once_with()

    def test_run_redirects_restart_request_while_controller_setup_is_degraded(self):
        seedsync = Seedsync.__new__(Seedsync)
        seedsync.context = SimpleNamespace(
            logger=MagicMock(),
            web_access_logger=MagicMock(),
            config=SimpleNamespace(
                lftp=SimpleNamespace(
                    remote_password="pw",
                    use_ssh_key=False,
                    remote_address="addr",
                    remote_port=21,
                    remote_username="user",
                    remote_path="/remote",
                    local_path="/local",
                    remote_path_to_scan_script="/scan",
                    use_temp_file=False,
                    rate_limit=None,
                ),
                controller=SimpleNamespace(
                    interval_ms_downloading_scan=1,
                    interval_ms_local_scan=1,
                    interval_ms_remote_scan=1,
                    use_local_path_as_extract_path=False,
                    extract_path="/extract",
                ),
                general=SimpleNamespace(log_level="INFO", verbose=False),
                web=SimpleNamespace(port=8800),
            ),
            args=SimpleNamespace(exit=False, debug=False, local_path_to_scanfs="/scan"),
            status=SimpleNamespace(server=SimpleNamespace(up=True, error_msg=None)),
            path_pair_manager=None,
            create_child_context=MagicMock(side_effect=lambda name: SimpleNamespace(logger=MagicMock())),
        )
        seedsync.controller_persist = MagicMock()
        seedsync.auto_queue_persist = MagicMock()
        seedsync.persist = MagicMock()

        controller = MagicMock()
        auto_queue = MagicMock()
        web_app = MagicMock()
        web_app_builder = MagicMock()
        web_app_builder.build.return_value = web_app
        web_app_builder.server_handler.is_restart_requested.return_value = True

        controller_job = MagicMock()
        controller_job.wait_until_setup_complete.return_value = False
        controller_job.is_setup_complete.return_value = False
        webapp_job = MagicMock()

        with patch("seedsync.Seedsync._emit_startup_warnings"), \
             patch("seedsync.Seedsync._detect_incomplete_config", return_value=[]), \
             patch("seedsync.Controller", return_value=controller), \
             patch("seedsync.AutoQueue", return_value=auto_queue), \
             patch("seedsync.WebAppBuilder", return_value=web_app_builder), \
             patch("seedsync.ControllerJob", return_value=controller_job) as mock_controller_job, \
             patch("seedsync.WebAppJob", return_value=webapp_job) as mock_webapp_job, \
             self.assertRaises(ServiceExit):
            mock_controller_job.__name__ = "ControllerJob"
            mock_webapp_job.__name__ = "WebAppJob"
            seedsync.run()

        controller_job.terminate.assert_called_once_with()
        controller_job.join.assert_not_called()
        webapp_job.terminate.assert_called_once_with()
        webapp_job.join.assert_called_once_with()
        seedsync.context.logger.warning.assert_any_call(
            "Restart requested while controller startup is degraded; exiting instead of restarting"
        )

    def test_emit_startup_warnings_skips_webhook_secret_warning_when_field_absent(self):
        config = SimpleNamespace(general=SimpleNamespace(api_token="configured-token"))
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config, web_bind_host="127.0.0.1")

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("general.api_token is configured" in message for message in warning_messages))
        self.assertFalse(any("webhook_secret" in message for message in warning_messages))
        self.assertEqual(1, logger.warning.call_count)

    def test_emit_startup_warnings_warns_when_webhook_secret_field_exists_and_is_blank(self):
        config = SimpleNamespace(general=SimpleNamespace(api_token="configured-token", webhook_secret=""))
        logger = MagicMock()

        Seedsync._emit_startup_warnings(logger, config, web_bind_host="127.0.0.1")

        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        self.assertTrue(any("webhook_secret is not configured" in message for message in warning_messages))
        self.assertTrue(any("general.api_token is configured" in message for message in warning_messages))
        self.assertEqual(2, logger.warning.call_count)

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
        new_config.general.log_level = "DEBUG" if old_config.general.log_level != "DEBUG" else "INFO"

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
