# Copyright 2017, Inderpreet Singh, All rights reserved.

import signal
import multiprocessing
import sys
import time
import argparse
import os
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional, Type, TypeVar, cast
import shutil
import platform
import tempfile
from types import FrameType
from typing import NoReturn, Sequence

# my libs
from common import ServiceExit, Context, Constants, Config, Args
from common import ServiceRestart
from common import Localization, Status, ConfigError, Persist, PersistError
from common import PathPairManager
from common.json_formatter import JsonFormatter
from controller import Controller, ControllerJob, ControllerPersist, AutoQueue, AutoQueuePersist
from web import WebAppJob, WebAppBuilder
from controller.notifier import NotificationService
from web.auth_store import ApiKeyStore, append_api_key_store_history
from web.handler.historical_log import create_historical_log_handler
from migration import MigrationCoordinator, MigrationDecision


T_Persist = TypeVar('T_Persist', bound=Persist)


def _configure_multiprocessing_start_method() -> None:
    """Select spawn once, accepting an already-compatible configuration."""
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError as exc:
        current_method = multiprocessing.get_start_method(allow_none=True)
        if current_method != "spawn":
            raise RuntimeError(
                "SeedSync requires multiprocessing start method 'spawn'; "
                f"current method is {current_method!r}"
            ) from exc


class Seedsync:
    """
    Implements the service for seedsync
    It is run in the main thread (no daemonization)
    """
    __FILE_CONFIG = "settings.cfg"
    __FILE_AUTO_QUEUE_PERSIST = "autoqueue.persist"
    __FILE_CONTROLLER_PERSIST = "controller.persist"
    __FILE_API_KEY_STORE = "api-keys.json"
    __FILE_BOOTSTRAP_PROOF = os.path.join("seedsync-bootstrap", "browser-bootstrap.json")
    __CONFIG_DUMMY_VALUE = "<replace me>"

    # This logger is used to print any exceptions caught at top module
    logger: Optional[logging.Logger] = None

    def __init__(self):
        Seedsync._apply_umask_from_env()

        # Parse the args
        args = self._parse_args(sys.argv[1:])

        # Migration preflight must precede every loader that can normalize,
        # back up, or persist legacy state. A later migration-only web slice
        # can consume this decision without constructing the normal runtime.
        self.migration_coordinator = MigrationCoordinator(args.config_dir)
        self.migration_decision: MigrationDecision = self.migration_coordinator.require_normal_startup()

        # Create/load config
        config = None
        self.config_path = os.path.join(args.config_dir, Seedsync.__FILE_CONFIG)
        create_default_config = False
        if os.path.isfile(self.config_path):
            try:
                config = Config.from_file(self.config_path)
            except (ConfigError, PersistError):
                Seedsync.__backup_file(self.config_path)
                # set config to default
                create_default_config = True
        else:
            create_default_config = True

        if create_default_config:
            # Create default config
            config = Seedsync._create_default_config()
            config.to_file(self.config_path)

        assert config is not None

        effective_log_level = Seedsync._resolve_log_level(config.general.log_level, args.debug)
        is_debug = effective_log_level == "DEBUG"

        # Create context args
        ctx_args = Args()
        ctx_args.local_path_to_scanfs = args.scanfs
        ctx_args.html_path = args.html
        ctx_args.debug = is_debug
        ctx_args.exit = args.exit
        ctx_args.web_bind_host = args.web_bind_host

        # Logger setup
        # We separate the main log from the web-access log
        log_format = config.logging.log_format or "standard"
        logger = self._create_logger(name=Constants.SERVICE_NAME,
                                     log_level=effective_log_level,
                                     logdir=args.logdir,
                                     log_format=log_format)
        history_log_path = os.path.join(args.config_dir, "logs", "history.jsonl")
        logger.addHandler(create_historical_log_handler(
            history_log_path, Constants.MAX_LOG_SIZE_IN_BYTES, Constants.LOG_BACKUP_COUNT
        ))
        ctx_args.history_log_path = history_log_path
        Seedsync.logger = logger
        web_access_logger = self._create_logger(name=Constants.WEB_ACCESS_LOG_NAME,
                                                log_level=effective_log_level,
                                                logdir=args.logdir,
                                                log_format=log_format)
        logger.info("Logging level is {}.".format(effective_log_level))

        # Create status
        status = Status()

        # Initialize path pairs for later multi-path support.
        path_pair_manager = PathPairManager(args.config_dir)
        path_pair_manager.load()
        if path_pair_manager.migrate_from_config(
                remote_path=cast(str, config.lftp.remote_path),
                local_path=cast(str, config.lftp.local_path)):
            logger.info("Migrated legacy path config to path pairs")

        # Create context
        self.context: Context = Context(
            logger=logger,
            web_access_logger=web_access_logger,
            config=config,
            args=ctx_args,
            status=status,
            path_pair_manager=path_pair_manager,
        )

        # Register the signal handlers
        signal.signal(signal.SIGTERM, self.signal)
        signal.signal(signal.SIGINT, self.signal)

        # Print context to log
        self.context.print_to_log()

        # Load the persists
        self.controller_persist_path: str = os.path.join(args.config_dir, Seedsync.__FILE_CONTROLLER_PERSIST)
        self.controller_persist: ControllerPersist = self._load_persist(ControllerPersist, self.controller_persist_path)

        self.auto_queue_persist_path: str = os.path.join(args.config_dir, Seedsync.__FILE_AUTO_QUEUE_PERSIST)
        self.auto_queue_persist: AutoQueuePersist = self._load_persist(AutoQueuePersist, self.auto_queue_persist_path)

        self.api_key_store_path: str = os.path.join(args.config_dir, Seedsync.__FILE_API_KEY_STORE)
        self.api_key_store: ApiKeyStore = self._load_persist(ApiKeyStore, self.api_key_store_path)
        self.api_key_store.bind_file_path(self.api_key_store_path)
        self.api_key_store.bind_bootstrap_proof_path(
            os.path.join(tempfile.gettempdir(), Seedsync.__FILE_BOOTSTRAP_PROOF)
        )

    def run(self):
        self.context.logger.info("Starting SeedSync")
        self.context.logger.info("Platform: {}".format(platform.machine()))
        self.api_key_store.ensure_bootstrap_proof()
        Seedsync._emit_startup_warnings(
            self.context.logger,
            self.context.config,
            self.api_key_store,
            web_bind_host=self.context.args.web_bind_host or "0.0.0.0"
        )

        if self.context.args.exit:
            self.context.logger.info("Bootstrap mode requested; persisting defaults and exiting before startup")
            self.persist()
            raise ServiceExit()

        # Create controller
        controller = Controller(self.context, self.controller_persist)

        # Create auto queue
        auto_queue = AutoQueue(self.context, self.auto_queue_persist, controller)

        notifier = NotificationService(self.context.config)

        # Create web app
        web_app_builder = WebAppBuilder(
            self.context, controller, self.auto_queue_persist, self.api_key_store, notifier=notifier
        )
        web_app = web_app_builder.build()

        # Define child threads
        controller_job = ControllerJob(
            context=self.context.create_child_context(ControllerJob.__name__),
            controller=controller,
            auto_queue=auto_queue
        )
        webapp_job = WebAppJob(
            context=self.context.create_child_context(WebAppJob.__name__),
            web_app=web_app
        )

        controller.add_model_listener(notifier)
        controller.add_download_start_listener(notifier.download_started)
        controller.add_remote_delete_success_listener(notifier.remote_delete_completed)
        notifier.start()

        do_start_controller = True

        # Initial checks to see if we should bother starting the controller
        path_pair_manager = self.context.path_pair_manager
        assert path_pair_manager is not None
        incomplete_fields = Seedsync._detect_incomplete_config(self.context.config, path_pair_manager, self.context.args)
        if incomplete_fields:
            do_start_controller = False
            self.context.logger.error("Config is incomplete: %s", ", ".join(incomplete_fields))
            self.context.status.server.up = False
            self.context.status.server.error_msg = Localization.Error.SETTINGS_INCOMPLETE_FIELDS.format(
                ", ".join(incomplete_fields)
            )

        controller_start_failed, controller_start_isolated = self.__start_jobs(
            self.context,
            do_start_controller,
            controller_job,
            webapp_job
        )

        try:
            prev_persist_timestamp = datetime.now()

            # Thread loop
            while True:
                # Persist to file occasionally
                now = datetime.now()
                if (now - prev_persist_timestamp).total_seconds() > Constants.MIN_PERSIST_TO_FILE_INTERVAL_IN_SECS:
                    prev_persist_timestamp = now
                    self.persist()

                # Propagate exceptions
                webapp_job.propagate_exception()
                if controller_start_isolated:
                    controller_start_isolated = Seedsync.__handle_controller_startup_timeout(
                        self.context,
                        controller_job,
                        controller_start_isolated
                    )
                elif do_start_controller and not controller_start_failed:
                    controller_job.propagate_exception()

                # Check if a restart is requested
                if web_app_builder.server_handler.is_restart_requested():
                    if controller_start_isolated:
                        self.context.logger.warning(
                            "Restart requested while controller startup is degraded; exiting instead of restarting"
                        )
                        raise ServiceExit()
                    raise ServiceRestart()

                # Nothing else to do
                time.sleep(Constants.MAIN_THREAD_SLEEP_INTERVAL_IN_SECS)

        except Exception as e:
            self._log_shutdown_cause(e)

            controller.remove_model_listener(notifier)
            controller.remove_download_start_listener(notifier.download_started)
            controller.remove_remote_delete_success_listener(notifier.remote_delete_completed)
            notifier.stop()

            # This sleep is important to allow the jobs to finish setup before we terminate them
            # If we kill too early, the jobs may leave lingering threads around
            # Note: There might be a better way to ensure that job setup has completed, but this
            #       will do for now
            time.sleep(Constants.MAIN_THREAD_SLEEP_INTERVAL_IN_SECS)

            # Join all the threads here
            if do_start_controller:
                controller_job.terminate()
                if controller_job.is_setup_complete():
                    controller_job.join()
                else:
                    self.context.logger.warning(
                        "Skipping controller join because setup never completed"
                    )
            webapp_job.terminate()

            # Wait for the threads to close
            webapp_job.join()

            # Last persist; guarded so a write failure cannot mask the
            # original in-flight exception that is re-raised below.
            self._final_persist()

            # Raise any exceptions so they can be logged properly
            # Note: ServiceRestart and ServiceExit will be caught and handled
            #       by outer code
            raise

    def persist(self):
        # Save the persists
        self.context.logger.debug("Persisting states to file")
        self.controller_persist.to_file(self.controller_persist_path)
        try:
            self.auto_queue_persist.to_file(self.auto_queue_persist_path)
        except OSError:
            self.context.logger.exception("Failed to persist auto-queue state")
        if hasattr(self, "api_key_store") and hasattr(self, "api_key_store_path"):
            self.api_key_store.save()
        new_config_str = self.context.config.to_str()
        try:
            with open(self.config_path, "r") as f:
                existing_config_str = f.read()
        except OSError:
            existing_config_str = None
        if new_config_str != existing_config_str:
            if existing_config_str is not None:
                Seedsync.__backup_file(self.config_path)
            with open(self.config_path, "w") as f:
                f.write(new_config_str)

    def _log_shutdown_cause(self, e: BaseException) -> None:
        # Intentional exit/restart stay friendly at INFO; genuine crashes surface
        # at ERROR with a traceback so an abnormal shutdown is visible.
        if isinstance(e, (ServiceExit, ServiceRestart)):
            self.context.logger.info("Exiting Seedsync")
        else:
            self.context.logger.exception("Seedsync exiting due to unexpected error")

    def _final_persist(self) -> None:
        # Last persist during shutdown. Guard it so a write failure here cannot
        # mask the original in-flight exception that run() re-raises afterwards.
        try:
            self.persist()
        except Exception:
            self.context.logger.exception("Final persist during shutdown failed")

    def signal(self, signum: int, _: Optional[FrameType]) -> NoReturn:
        # noinspection PyUnresolvedReferences
        # Signals is a generated enum
        self.context.logger.info("Caught signal {}".format(signal.Signals(signum).name))
        raise ServiceExit()

    @staticmethod
    def __start_jobs(
        context: Context,
        do_start_controller: bool,
        controller_job: ControllerJob,
        webapp_job: WebAppJob,
    ) -> tuple[bool, bool]:
        """
        Start the controller before the web server thread so controller subprocess startup
        does not race against the threaded web app.
        """
        controller_start_failed = False
        controller_start_isolated = False
        if do_start_controller:
            controller_job.daemon = True
            controller_job.start()
            setup_finished = controller_job.wait_until_setup_complete(
                timeout=Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
            )
            if not setup_finished:
                controller_start_isolated = True
                context.status.server.up = False
                context.status.server.error_msg = (
                    "Controller startup timed out after {} seconds; continuing with web UI".format(
                        Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
                    )
                )
                context.logger.error(context.status.server.error_msg)
            elif isinstance(controller_job.exc_info, tuple):
                controller_exc_info = controller_job.exc_info
                controller_start_failed = True
                context.status.server.up = False
                context.status.server.error_msg = str(controller_exc_info[1])
                logging_exc_info = (
                    (controller_exc_info[0], controller_exc_info[1], controller_exc_info[2])
                    if controller_exc_info[0] is not None and controller_exc_info[1] is not None
                    else False
                )
                context.logger.error(
                    "Controller failed to start; keeping the web UI available",
                    exc_info=logging_exc_info
                )

        webapp_job.start()
        return controller_start_failed, controller_start_isolated

    @staticmethod
    def __handle_controller_startup_timeout(
        context: Context,
        controller_job: ControllerJob,
        controller_start_isolated: bool,
    ) -> bool:
        """
        Keep monitoring a controller that timed out during startup without crashing the web service.
        """
        if not controller_start_isolated:
            return False

        timeout_error_msg = "Controller startup timed out after {} seconds; continuing with web UI".format(
            Constants.CONTROLLER_SETUP_TIMEOUT_IN_SECS
        )

        if isinstance(controller_job.exc_info, tuple):
            controller_exc_info = controller_job.exc_info
            error_msg = str(controller_exc_info[1])
            if context.status.server.error_msg != error_msg:
                context.status.server.up = False
                context.status.server.error_msg = error_msg
                logging_exc_info = (
                    (controller_exc_info[0], controller_exc_info[1], controller_exc_info[2])
                    if controller_exc_info[0] is not None and controller_exc_info[1] is not None
                    else False
                )
                context.logger.error(
                    "Controller failed after startup timeout; keeping the web UI available",
                    exc_info=logging_exc_info
                )
            return True

        if not controller_job.wait_until_setup_complete(timeout=0):
            return True

        if context.status.server.error_msg == timeout_error_msg:
            context.status.server.up = True
            context.status.server.error_msg = None
            context.logger.info("Controller startup recovered after timeout")

        return False

    @staticmethod
    def _parse_args(args: Sequence[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser(description="Seedsync daemon")
        parser.add_argument("-c", "--config_dir", required=True, help="Path to config directory")
        parser.add_argument("--logdir", help="Directory for log files")
        parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logs")
        parser.add_argument("--exit", action="store_true", help="Exit on error")

        # Whether package is frozen
        is_frozen = getattr(sys, 'frozen', False)
        meipass = getattr(sys, "_MEIPASS", None)  # type: ignore[attr-defined]

        # Html path is only required if not running a frozen package
        # For a frozen package, set default to root/html
        # noinspection PyUnresolvedReferences
        # noinspection PyProtectedMember
        default_html_path = os.path.join(meipass, "html") if is_frozen and meipass is not None else None
        parser.add_argument("--html",
                            required=not is_frozen,
                            default=default_html_path,
                            help="Path to directory containing html resources")

        # Scanfs path is only required if not running a frozen package
        # For a frozen package, set default to root/scanfs
        # noinspection PyUnresolvedReferences
        # noinspection PyProtectedMember
        default_scanfs_path = os.path.join(meipass, "scanfs") if is_frozen and meipass is not None else None
        parser.add_argument("--scanfs",
                            required=not is_frozen,
                            default=default_scanfs_path,
                            help="Path to scanfs executable")
        parser.add_argument("--web-bind-host",
                            default="0.0.0.0",
                            help="Host/IP address for the web server to bind to")

        return parser.parse_args(args)

    @staticmethod
    def _apply_umask_from_env():
        umask_value = os.environ.get("UMASK", "")
        if not umask_value:
            return

        if any(character not in "01234567" for character in umask_value):
            sys.stderr.write(
                "ERROR: invalid UMASK value {!r}; expected octal digits 0-7\n".format(umask_value)
            )
            raise SystemExit(1)

        os.umask(int(umask_value, 8))

    @staticmethod
    def _create_logger(name: str, log_level: str, logdir: Optional[str], log_format: str = "standard") -> logging.Logger:
        logger = logging.getLogger(name)

        # Remove any existing handlers (needed when restarting)
        handlers = logger.handlers[:]
        for handler in handlers:
            handler.close()
            logger.removeHandler(handler)

        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        if logdir is not None:
            # Output logs to a file in the given directory
            handler = RotatingFileHandler(
                        "{}/{}.log".format(logdir, name),
                        maxBytes=Constants.MAX_LOG_SIZE_IN_BYTES,
                        backupCount=Constants.LOG_BACKUP_COUNT
                      )
        else:
            handler = logging.StreamHandler(sys.stdout)
        if log_format == "json":
            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s - %(levelname)s - %(name)s (%(processName)s/%(threadName)s) - %(message)s"
            )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    @staticmethod
    def _create_default_config() -> Config:
        """
        Create a config with default values
        :return:
        """
        config = Config()

        config.general.log_level = "INFO"
        config.general.verbose = False
        config.general.api_token = ""
        config.general.allowed_hostname = ""
        config.general.trusted_browser_bootstrap_remote_addrs = ""
        config.general.browser_handover_recovery_version = ""
        config.general.breadcrumb_trace_enabled = False

        config.lftp.remote_address = Seedsync.__CONFIG_DUMMY_VALUE
        config.lftp.remote_username = Seedsync.__CONFIG_DUMMY_VALUE
        config.lftp.remote_password = Seedsync.__CONFIG_DUMMY_VALUE
        config.lftp.remote_port = 22
        config.lftp.remote_path = Seedsync.__CONFIG_DUMMY_VALUE
        config.lftp.local_path = Seedsync.__CONFIG_DUMMY_VALUE
        config.lftp.remote_path_to_scan_script = "/tmp"
        config.lftp.use_ssh_key = False
        config.lftp.num_max_parallel_downloads = 2
        config.lftp.num_max_parallel_files_per_download = 4
        config.lftp.num_max_connections_per_root_file = 4
        config.lftp.num_max_connections_per_dir_file = 4
        config.lftp.num_max_total_connections = 16
        config.lftp.use_temp_file = False
        config.lftp.rate_limit = "0"
        config.lftp.net_socket_buffer = "8M"
        config.lftp.staging_path = ""

        config.controller.interval_ms_remote_scan = 30000
        config.controller.interval_ms_local_scan = 10000
        config.controller.interval_ms_downloading_scan = 1000
        config.controller.extract_path = "/tmp"
        config.controller.use_local_path_as_extract_path = True

        config.web.port = 8800

        config.autoqueue.enabled = True
        config.autoqueue.patterns_only = False
        config.autoqueue.auto_extract = True
        config.logging.log_format = "standard"

        return config

    @staticmethod
    def _resolve_log_level(configured_log_level: Optional[str], debug_override: bool) -> str:
        if debug_override:
            return "DEBUG"
        if isinstance(configured_log_level, str):
            normalized = configured_log_level.strip().upper()
            if normalized in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
                return normalized
        return "INFO"

    @staticmethod
    def _detect_incomplete_config(
        config: Config,
        path_pair_manager: PathPairManager | None = None,
        args: Args | None = None
    ) -> list[str]:
        return Controller.collect_missing_startup_fields(config, args=args, path_pair_manager=path_pair_manager)

    @staticmethod
    def _is_blank_config_value(value: object) -> bool:
        return value is None or (isinstance(value, str) and value.strip() == "")

    @staticmethod
    def _emit_startup_warnings(
        logger: logging.Logger,
        config: Config,
        auth_store: ApiKeyStore | None = None,
        web_bind_host: str = "0.0.0.0"
    ) -> None:
        general_config = getattr(config, "general", None)
        if general_config is None:
            return

        active_api_key_count = 0
        if auth_store is not None:
            active_api_key_count = len([
                key for key in auth_store.api_keys
                if not getattr(key, "is_revoked", False)
            ])

        # webhook_secret is not part of all local config models; only warn when present.
        webhook_secret = getattr(general_config, "webhook_secret", None)
        if hasattr(general_config, "webhook_secret") and Seedsync._is_blank_config_value(webhook_secret):
            logger.warning(
                "Security: webhook_secret is not configured. Webhook endpoints may accept unauthenticated requests."
            )

        api_token = getattr(general_config, "api_token", None)
        token_is_configured = not Seedsync._is_blank_config_value(api_token)
        if token_is_configured:
            logger.warning(
                "Security: general.api_token is configured, but SeedSync now authenticates external /server/* "
                "requests with scoped API keys only. Clear general.api_token if you no longer need the stored value."
            )
        else:
            if active_api_key_count > 0:
                logger.warning(
                    "Security: scoped API keys are configured and general.api_token is blank. "
                    "External /server/* access uses scoped API keys only."
                )
            else:
                logger.warning(
                    "Security: no scoped API keys are configured and general.api_token is blank. "
                    "External /server/* access is not enabled until you create a scoped API key."
                )
        if web_bind_host == "0.0.0.0":
            if not token_is_configured:
                if active_api_key_count > 0:
                    logger.warning(
                        "Security: Application is bound to 0.0.0.0. Scoped API keys are configured, so external "
                        "/server/* access depends on them."
                    )
                else:
                    logger.warning(
                        "Security: Application is bound to 0.0.0.0 and external /server/* access is not enabled yet. "
                        "Create a scoped API key before exposing the API to the network."
                    )
            else:
                logger.warning(
                    "Security: Application is bound to 0.0.0.0. general.api_token does not grant external /server/* "
                    "access; admin endpoints require scoped API keys."
                )

    @staticmethod
    def _load_persist(persist_cls: Type[T_Persist], file_path: str) -> T_Persist:
        """
        Loads a persist from file.
        Backs up existing persist if it's corrupted. Returns a new blank
        persist in its place.
        :param persist_cls:
        :param file_path:
        :return:
        """
        if os.path.isfile(file_path):
            try:
                return persist_cls.from_file(file_path)
            except PersistError as exc:
                if Seedsync.logger:
                    Seedsync.logger.exception("Caught exception")

                # backup file
                backup_path = Seedsync.__backup_file(file_path)
                if persist_cls is ApiKeyStore:
                    append_api_key_store_history(
                        file_path,
                        "store_load_failed",
                        "persist_error_fallback",
                        error_type=exc.__class__.__name__,
                        backup_path=backup_path,
                        fallback="fresh_store",
                    )

                # noinspection PyCallingNonCallable
                return persist_cls()
        else:
            # noinspection PyCallingNonCallable
            return persist_cls()

    @staticmethod
    def __backup_file(file_path: str):
        file_name = os.path.basename(file_path)
        file_dir = os.path.dirname(file_path)
        i = 1
        while True:
            backup_path = os.path.join(
                file_dir, "{}.{}.bak".format(file_name, i)
            )
            if not os.path.exists(backup_path):
                break
            i += 1
        if Seedsync.logger:
            Seedsync.logger.info("Backing up {} to {}".format(file_path, backup_path))
        shutil.copy(file_path, backup_path)
        return backup_path


if __name__ == "__main__":
    _configure_multiprocessing_start_method()

    if sys.hexversion < 0x030B0000 or sys.hexversion >= 0x030D0000:
        sys.exit("Python 3.11 or 3.12 is required to run this program.")

    while True:
        try:
            seedsync = Seedsync()
            seedsync.run()
        except ServiceExit:
            break
        except ServiceRestart:
            if Seedsync.logger is not None:
                Seedsync.logger.info("Restarting...")
            continue
        except Exception as e:
            if Seedsync.logger is not None:
                Seedsync.logger.exception("Caught exception")
            raise

        if Seedsync.logger is not None:
            Seedsync.logger.info("Exited successfully")
