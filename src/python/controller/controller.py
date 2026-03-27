# Copyright 2017, Inderpreet Singh, All rights reserved.

from abc import ABC, abstractmethod
from typing import List, Callable
from threading import Lock
from queue import Queue
from enum import Enum
from datetime import datetime
import copy
import json
import os
import shutil

# my libs
from .scan import (
    ScannerProcess,
    ActiveScanner,
    LocalScanner,
    RemoteScanner,
    MultiPathActiveScanner,
    MultiPathLocalScanner,
    MultiPathRemoteScanner,
)
from .extract import ExtractProcess, ExtractStatus
from .validate import ValidateProcess
from .model_builder import ModelBuilder
from .memory_monitor import ControllerMemoryMonitor
from common import Context, AppError, MultiprocessingLogger, AppOneShotProcess, Constants, PathPair
from model import ModelError, ModelFile, Model, ModelDiff, ModelDiffUtil, IModelListener
from lftp import Lftp, LftpError, LftpJobStatus, LftpJobStatusParserError
from .controller_persist import ControllerPersist
from .delete import DeleteLocalProcess, DeleteRemoteProcess


class ControllerError(AppError):
    """
    Exception indicating a controller error
    """
    pass


class Controller:
    """
    Top-level class that controls the behaviour of the app
    """
    class Command:
        """
        Class by which clients of Controller can request Actions to be executed
        Supports callbacks by which clients can be notified of action success/failure
        Note: callbacks will be executed in Controller thread, so any heavy computation
              should be moved out of the callback
        """
        class Action(Enum):
            QUEUE = 0
            STOP = 1
            EXTRACT = 2
            DELETE_LOCAL = 3
            DELETE_REMOTE = 4
            VALIDATE = 5

        class ICallback(ABC):
            """Command callback interface"""
            @abstractmethod
            def on_success(self):
                """Called on successful completion of action"""
                pass

            @abstractmethod
            def on_failure(self, error: str, error_code: int = 400):
                """Called on action failure"""
                pass

        def __init__(self, action: Action, filename: str):
            self.action = action
            self.filename = filename
            self.callbacks = []

        def add_callback(self, callback: ICallback):
            self.callbacks.append(callback)

    class CommandProcessWrapper:
        """
        Wraps any one-shot command processes launched by the controller
        """
        def __init__(
            self,
            command: "Controller.Command",
            file_id: str,
            file_name: str,
            process: AppOneShotProcess,
            post_callback: Callable,
            await_completion: bool
        ):
            self.command = command
            self.file_id = file_id
            self.file_name = file_name
            self.process = process
            self.post_callback = post_callback
            self.await_completion = await_completion

    def __init__(self,
                 context: Context,
                 persist: ControllerPersist):
        self.__context = context
        self.__persist = persist
        self.logger = context.logger.getChild("Controller")
        self.__stop_resume_trace_logger = self.logger.getChild("StopResumeTrace")
        self.__stop_resume_trace_file_id = os.environ.get("SEEDSYNC_STOP_RESUME_TRACE_FILE_ID")
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        self.__target_archive_trace_file_id = os.environ.get("SEEDSYNC_TARGET_ARCHIVE_TRACE_FILE_ID")
        if self.__target_archive_trace_file_id is not None and not self.__target_archive_trace_file_id.strip():
            self.__target_archive_trace_file_id = None
        self.__target_archive_trace_last_signature = None

        # Decide the password here
        self.__password = context.config.lftp.remote_password if not context.config.lftp.use_ssh_key else None

        # The command queue
        self.__command_queue = Queue()

        # The model
        self.__model = Model()
        self.__model.set_base_logger(self.logger)
        # Lock for the model
        # Note: While the scanners are in a separate process, the rest of the application
        #       is threaded in a single process. (The webserver is bottle+paste which is
        #       multi-threaded). Therefore it is safe to use a threading Lock for the model
        #       (the scanner processes never try to access the model)
        self.__model_lock = Lock()

        # Model builder
        self.__model_builder = ModelBuilder()
        self.__model_builder.set_base_logger(self.logger)
        self.__model_builder.set_downloaded_files(self.__persist.downloaded_file_names)
        self.__model_builder.set_extracted_files(self.__persist.extracted_file_names)
        self.__model_builder.set_stopped_files(self.__persist.stopped_file_names)

        self.__staging_path = self.__build_staging_path(
            self.__context.config.lftp.local_path,
            self.__context.config.lftp.staging_path
        )
        self.__path_pair_staging_paths = {}

        # Lftp
        self.__lftp = Lftp(address=self.__context.config.lftp.remote_address,
                           port=self.__context.config.lftp.remote_port,
                           user=self.__context.config.lftp.remote_username,
                           password=self.__password)
        self.__lftp.set_base_logger(self.logger)
        self.__lftp.set_base_remote_dir_path(self.__context.config.lftp.remote_path)
        self.__lftp.set_base_local_dir_path(self.__staging_path)
        # Configure Lftp
        self.__lftp.num_parallel_jobs = self.__context.config.lftp.num_max_parallel_downloads
        self.__lftp.num_parallel_files = self.__context.config.lftp.num_max_parallel_files_per_download
        self.__lftp.num_connections_per_root_file = self.__context.config.lftp.num_max_connections_per_root_file
        self.__lftp.num_connections_per_dir_file = self.__context.config.lftp.num_max_connections_per_dir_file
        self.__lftp.num_max_total_connections = self.__context.config.lftp.num_max_total_connections
        self.__lftp.use_temp_file = self.__context.config.lftp.use_temp_file
        if self.__context.config.lftp.rate_limit:
            self.__lftp.rate_limit = self.__context.config.lftp.rate_limit
        self.__lftp.temp_file_name = "*" + Constants.LFTP_TEMP_FILE_SUFFIX
        self.__lftp.set_verbose_logging(self.__context.config.general.verbose)

        enabled_path_pairs = []
        if self.__context.path_pair_manager is not None:
            enabled_path_pairs = self.__context.path_pair_manager.get_enabled_pairs()
        self.__path_pairs_by_id = {pair.id: pair for pair in enabled_path_pairs}
        if enabled_path_pairs:
            self.__lftp.set_path_pairs([
                PathPair(
                    remote_path=pair.remote_path,
                    local_path=self.__build_staging_path(pair.local_path),
                    name=pair.name,
                    id=pair.id,
                    enabled=pair.enabled,
                    auto_queue=pair.auto_queue
                )
                for pair in enabled_path_pairs
            ])

        # Setup the scanners and scanner processes
        if enabled_path_pairs:
            for pair in enabled_path_pairs:
                pair_staging_path = self.__build_staging_path(pair.local_path)
                self.__path_pair_staging_paths[pair.id] = pair_staging_path
            self.__active_scanner = MultiPathActiveScanner({
                pair.id: self.__path_pair_staging_paths[pair.id] for pair in enabled_path_pairs
            }, use_temp_file=self.__context.config.lftp.use_temp_file)
            self.__local_scanner = MultiPathLocalScanner([
                LocalScanner(
                    local_path=pair.local_path,
                    use_temp_file=self.__context.config.lftp.use_temp_file,
                    staging_path=self.__path_pair_staging_paths[pair.id],
                    managed_extract_folders_enabled=self.__context.config.controller.managed_extract_folders_enabled,
                    path_pair_id=pair.id,
                    path_pair_name=pair.name
                ) for pair in enabled_path_pairs
            ])
            self.__remote_scanner = MultiPathRemoteScanner([
                RemoteScanner(
                    remote_address=self.__context.config.lftp.remote_address,
                    remote_username=self.__context.config.lftp.remote_username,
                    remote_password=self.__password,
                    remote_port=self.__context.config.lftp.remote_port,
                    remote_path_to_scan=pair.remote_path,
                    local_path_to_scan_script=self.__context.args.local_path_to_scanfs,
                    remote_path_to_scan_script=self.__context.config.lftp.remote_path_to_scan_script,
                    path_pair_id=pair.id,
                    path_pair_name=pair.name
                ) for pair in enabled_path_pairs
            ])
        else:
            self.__active_scanner = ActiveScanner(
                self.__staging_path,
                use_temp_file=self.__context.config.lftp.use_temp_file
            )
            self.__local_scanner = LocalScanner(
                local_path=self.__context.config.lftp.local_path,
                use_temp_file=self.__context.config.lftp.use_temp_file,
                staging_path=self.__staging_path,
                managed_extract_folders_enabled=self.__context.config.controller.managed_extract_folders_enabled
            )
            self.__remote_scanner = RemoteScanner(
                remote_address=self.__context.config.lftp.remote_address,
                remote_username=self.__context.config.lftp.remote_username,
                remote_password=self.__password,
                remote_port=self.__context.config.lftp.remote_port,
                remote_path_to_scan=self.__context.config.lftp.remote_path,
                local_path_to_scan_script=self.__context.args.local_path_to_scanfs,
                remote_path_to_scan_script=self.__context.config.lftp.remote_path_to_scan_script
            )

        self.__active_scan_process = ScannerProcess(
            scanner=self.__active_scanner,
            interval_in_ms=self.__context.config.controller.interval_ms_downloading_scan,
            verbose=False
        )
        self.__local_scan_process = ScannerProcess(
            scanner=self.__local_scanner,
            interval_in_ms=self.__context.config.controller.interval_ms_local_scan,
        )
        self.__remote_scan_process = ScannerProcess(
            scanner=self.__remote_scanner,
            interval_in_ms=self.__context.config.controller.interval_ms_remote_scan,
        )

        # Setup extract process
        if self.__context.config.controller.use_local_path_as_extract_path:
            out_dir_path = self.__context.config.lftp.local_path
        else:
            out_dir_path = self.__context.config.controller.extract_path
        self.__extract_process = ExtractProcess(
            out_dir_path=out_dir_path,
            local_path=self.__context.config.lftp.local_path,
            managed_extract_folders_enabled=self.__context.config.controller.managed_extract_folders_enabled
        )
        self.__validate_process = ValidateProcess(
            remote_address=self.__context.config.lftp.remote_address,
            remote_username=self.__context.config.lftp.remote_username,
            remote_password=self.__password,
            remote_port=self.__context.config.lftp.remote_port,
            local_path=self.__context.config.lftp.local_path,
            remote_path=self.__context.config.lftp.remote_path,
            path_pairs_by_id=self.__path_pairs_by_id
        )

        # Setup multiprocess logging
        self.__mp_logger = MultiprocessingLogger(self.logger)
        self.__active_scan_process.set_multiprocessing_logger(self.__mp_logger)
        self.__local_scan_process.set_multiprocessing_logger(self.__mp_logger)
        self.__remote_scan_process.set_multiprocessing_logger(self.__mp_logger)
        self.__extract_process.set_multiprocessing_logger(self.__mp_logger)
        self.__validate_process.set_multiprocessing_logger(self.__mp_logger)

        # Keep track of active files
        self.__active_downloading_file_names = []
        self.__active_extracting_file_names = []
        self.__malformed_status_only_file_ids = set()
        self.__pending_auto_purge_file_ids = set()

        # Keep track of active command processes
        self.__active_command_processes = []
        self.__startup_recovery_done = False
        self.__memory_monitor = ControllerMemoryMonitor(self.logger.getChild("MemoryMonitor"))

        self.__started = False

    def start(self):
        """
        Start the controller
        Must be called after ctor and before process()
        :return:
        """
        self.logger.debug("Starting controller")
        os.makedirs(self.__staging_path, exist_ok=True)
        for staging_path in self.__path_pair_staging_paths.values():
            os.makedirs(staging_path, exist_ok=True)
        self.__active_scan_process.start()
        self.__local_scan_process.start()
        self.__remote_scan_process.start()
        self.__extract_process.start()
        self.__validate_process.start()
        self.__mp_logger.start()
        self.__started = True

    def process(self):
        """
        Advance the controller state
        This method should return relatively quickly as the heavy lifting is done by concurrent tasks
        :return:
        """
        if not self.__started:
            raise ControllerError("Cannot process, controller is not started")
        self.__propagate_exceptions()
        self.__cleanup_commands()
        self.__process_commands()
        self.__update_model()
        self.__log_memory_usage()

    def exit(self):
        self.logger.debug("Exiting controller")
        if self.__started:
            self.__lftp.exit()
            self.__active_scan_process.terminate()
            self.__local_scan_process.terminate()
            self.__remote_scan_process.terminate()
            self.__extract_process.terminate()
            self.__validate_process.terminate()
            self.__active_scan_process.join()
            self.__local_scan_process.join()
            self.__remote_scan_process.join()
            self.__extract_process.join()
            self.__validate_process.join()
            self.__mp_logger.stop()
            self.__started = False
            self.logger.info("Exited controller")

    def get_model_files(self) -> List[ModelFile]:
        """
        Returns a copy of all the model files
        :return:
        """
        with self.__model_lock:
            model_files = self.__get_model_files()
        return model_files

    def is_file_stopped(self, filename: str) -> bool:
        return filename in self.__persist.stopped_file_names

    def add_model_listener(self, listener: IModelListener):
        """
        Adds a listener to the controller's model
        :param listener:
        :return:
        """
        with self.__model_lock:
            self.__model.add_listener(listener)

    def remove_model_listener(self, listener: IModelListener):
        """
        Removes a listener from the controller's model
        :param listener:
        :return:
        """
        with self.__model_lock:
            self.__model.remove_listener(listener)

    def get_model_files_and_add_listener(self, listener: IModelListener):
        """
        Adds a listener and returns the current state of model files in one atomic operation
        This guarantees that model update events are not missed or duplicated for the clients
        Without an atomic operation, the following scenarios can happen:
            1. get_model() -> model updated -> add_listener()
               The model update never propagates to client
            2. add_listener() -> model updated -> get_model()
               The model update is duplicated on client side (once through listener, and once
               through the model).
        :param listener:
        :return:
        """
        with self.__model_lock:
            self.__model.add_listener(listener)
            model_files = self.__get_model_files()
        return model_files

    def queue_command(self, command: Command):
        self.__command_queue.put(command)

    def __get_model_files(self) -> List[ModelFile]:
        model_files = []
        get_ids = getattr(self.__model, "get_file_ids", None)
        identifiers = get_ids() if callable(get_ids) else self.__model.get_file_names()
        for identifier in identifiers:
            model_files.append(copy.deepcopy(self.__model.get_file(identifier)))
        return model_files

    def __get_path_pair(self, path_pair_id: str):
        if not path_pair_id:
            return None
        return getattr(self, "_Controller__path_pairs_by_id", {}).get(path_pair_id)

    @staticmethod
    def __build_staging_path(local_path: str, staging_path: str = None) -> str:
        return staging_path or os.path.join(local_path, "incomplete")

    def __is_previously_downloaded(self, name: str, path_pair_id: str = None) -> bool:
        file_id = ModelFile.build_file_id(name, path_pair_id)
        return file_id in self.__persist.downloaded_file_names or name in self.__persist.downloaded_file_names

    def __is_explicitly_stopped(self, name: str, path_pair_id: str = None) -> bool:
        file_id = ModelFile.build_file_id(name, path_pair_id)
        return file_id in self.__persist.stopped_file_names or name in self.__persist.stopped_file_names

    def __get_staging_path(self, path_pair_id: str = None) -> str:
        if path_pair_id:
            path_pair = self.__get_path_pair(path_pair_id)
            if path_pair is not None:
                return self.__path_pair_staging_paths.get(path_pair_id, self.__build_staging_path(path_pair.local_path))
            return self.__path_pair_staging_paths.get(path_pair_id)
        return self.__staging_path

    def __get_stop_resume_trace_file_details(self, path: str, include_allocated_size: bool = False) -> dict:
        if path is None:
            return {
                "exists": False,
                "size": None,
                "mtime": None,
                "allocated_size": None
            }

        try:
            stat_result = os.stat(path)
        except OSError:
            return {
                "exists": False,
                "size": None,
                "mtime": None,
                "allocated_size": None
            }

        details = {
            "exists": True,
            "size": stat_result.st_size,
            "mtime": stat_result.st_mtime,
            "allocated_size": None
        }
        if include_allocated_size:
            blocks = getattr(stat_result, "st_blocks", None)
            if blocks is not None:
                try:
                    details["allocated_size"] = int(blocks) * 512
                except (TypeError, ValueError, OverflowError):
                    details["allocated_size"] = None
        return details

    def __log_stop_resume_trace(self,
                                reason: str,
                                file_id: str,
                                file_name: str,
                                path_pair_id: str = None,
                                is_dir: bool = False,
                                current_state: str = None,
                                remote_base_dir_path: str = None,
                                local_base_dir_path: str = None,
        stopped_marked: bool = False):
        trace_file_id = getattr(self, "_Controller__stop_resume_trace_file_id", None)
        if trace_file_id is None or (trace_file_id != file_id and trace_file_id != file_name):
            return

        temp_path = None
        sidecar_path = None
        if local_base_dir_path is not None and not is_dir:
            temp_path = os.path.join(local_base_dir_path, file_name + Constants.LFTP_TEMP_FILE_SUFFIX)
            sidecar_path = temp_path + ".lftp-pget-status"

        temp_details = self.__get_stop_resume_trace_file_details(temp_path, include_allocated_size=True)
        sidecar_details = self.__get_stop_resume_trace_file_details(sidecar_path)
        logger = getattr(self, "_Controller__stop_resume_trace_logger", self.logger.getChild("StopResumeTrace"))
        logger.info(
            "stop_resume_trace %s",
            json.dumps({
                "reason": reason,
                "file_id": file_id,
                "filename": file_name,
                "path_pair_id": path_pair_id,
                "current_state": current_state,
                "remote_base_dir_path": remote_base_dir_path,
                "local_base_dir_path": local_base_dir_path,
                "stopped_marked": stopped_marked,
                "temp_path": temp_path,
                "temp_exists": temp_details["exists"],
                "temp_apparent_size": temp_details["size"],
                "temp_allocated_size": temp_details["allocated_size"],
                "temp_mtime": temp_details["mtime"],
                "sidecar_path": sidecar_path,
                "sidecar_exists": sidecar_details["exists"],
                "sidecar_size": sidecar_details["size"],
                "sidecar_mtime": sidecar_details["mtime"]
            }, sort_keys=True)
        )

    @staticmethod
    def __extract_target_archive_trace_selector_name(identifier: str):
        if identifier is None:
            return None
        try:
            parsed_identifier = json.loads(identifier)
        except (TypeError, ValueError, json.JSONDecodeError):
            return identifier
        if isinstance(parsed_identifier, list) and len(parsed_identifier) == 2 and isinstance(parsed_identifier[1], str):
            return parsed_identifier[1]
        return identifier

    def __is_target_archive_trace_enabled(self) -> bool:
        return self.__target_archive_trace_file_id is not None

    def __target_archive_trace_selector_matches_file(self, file_id: str, file_name: str) -> bool:
        if not self.__is_target_archive_trace_enabled():
            return False
        if self.__target_archive_trace_file_id == file_id or self.__target_archive_trace_file_id == file_name:
            return True
        selector_name = self.__extract_target_archive_trace_selector_name(self.__target_archive_trace_file_id)
        return selector_name == file_name

    @staticmethod
    def __summarize_target_archive_file(file: ModelFile) -> dict:
        return {
            "file_id": file.file_id,
            "name": file.name,
            "path_pair_id": file.path_pair_id,
            "path_pair_name": file.path_pair_name,
            "state": getattr(file.state, "name", file.state),
            "is_dir": file.is_dir,
            "local_size": file.local_size,
            "remote_size": file.remote_size,
            "is_extractable": file.is_extractable,
        }

    def __find_target_archive_model_file(self, file_name: str, file_id: str = None):
        try:
            file_ids = self.__model.get_file_ids()
        except AttributeError:
            return None
        for candidate_file_id in file_ids:
            try:
                file = self.__model.get_file(candidate_file_id)
            except ModelError:
                continue
            if file_id is not None and file.file_id == file_id:
                return file
            if file.name == file_name and self.__target_archive_trace_selector_matches_file(file.file_id, file.name):
                return file
        return None

    def __trace_target_archive_event(self, event: str, payload: dict):
        if not self.__is_target_archive_trace_enabled():
            return
        trace_payload = {
            "event": event,
            "target_selector": self.__target_archive_trace_file_id,
        }
        trace_payload.update(payload)
        signature = json.dumps(trace_payload, sort_keys=True)
        if signature == self.__target_archive_trace_last_signature:
            return
        self.__target_archive_trace_last_signature = signature
        self.__target_archive_trace_logger.info("target_archive_trace %s", signature)

    def __is_model_file_name_unambiguous(self, file_name: str) -> bool:
        try:
            file_ids = self.__model.get_file_ids()
        except AttributeError:
            return True

        matching_file_ids = 0
        try:
            for file_id in file_ids:
                try:
                    file = self.__model.get_file(file_id)
                except ModelError:
                    continue
                if file.name == file_name:
                    matching_file_ids += 1
                    if matching_file_ids > 1:
                        return False
        except TypeError:
            return True
        return matching_file_ids <= 1

    def clear_extracted_marker(self, file: ModelFile):
        stale_extracted_file_names = set()
        if file.file_id in self.__persist.extracted_file_names:
            stale_extracted_file_names.add(file.file_id)
        if file.name in self.__persist.extracted_file_names and self.__is_model_file_name_unambiguous(file.name):
            stale_extracted_file_names.add(file.name)
        if not stale_extracted_file_names:
            return

        self.logger.info(
            "Removing stale extracted list entries for blocked auto-extract: {}".format(
                stale_extracted_file_names
            )
        )
        self.__persist.extracted_file_names.difference_update(stale_extracted_file_names)
        self.__model_builder.set_extracted_files(self.__persist.extracted_file_names)

    def __move_from_staging(self, name: str, path_pair_id: str = None):
        if path_pair_id:
            staging_path = self.__get_staging_path(path_pair_id)
            path_pair = self.__get_path_pair(path_pair_id)
            final_path = path_pair.local_path if path_pair is not None else None
        else:
            staging_path = self.__staging_path
            final_path = self.__context.config.lftp.local_path

        if not staging_path or not final_path:
            return

        src = os.path.join(staging_path, name)
        dst = os.path.join(final_path, name)
        trace_file_id = ModelFile.build_file_id(name, path_pair_id)
        should_trace = self.__target_archive_trace_selector_matches_file(trace_file_id, name)
        if should_trace:
            self.__trace_target_archive_event("move_from_staging_attempt", {
                "file_id": trace_file_id,
                "file_name": name,
                "path_pair_id": path_pair_id,
                "staging_path": staging_path,
                "final_path": final_path,
                "source_path": src,
                "destination_path": dst,
                "source_exists": os.path.exists(src),
                "same_path": os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)),
            })
        if not os.path.exists(src):
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "missing_source",
                })
            return
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "same_path",
                })
            return

        try:
            shutil.move(src, dst)
            self.logger.info("Moved '%s' from staging '%s' to '%s'", name, staging_path, final_path)
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "moved",
                })
            self.__local_scan_process.force_scan()
        except OSError as error:
            self.logger.warning(
                "Failed to move '%s' from staging '%s' to '%s': %s",
                name,
                staging_path,
                final_path,
                error
            )
            if should_trace:
                self.__trace_target_archive_event("move_from_staging_result", {
                    "file_id": trace_file_id,
                    "file_name": name,
                    "result": "failed",
                    "error": str(error),
                })

    def __get_delete_local_target(self, file: ModelFile) -> tuple[str, str]:
        path_pair = self.__get_path_pair(file.path_pair_id)
        final_path = path_pair.local_path if path_pair is not None else self.__context.config.lftp.local_path
        staging_path = self.__get_staging_path(file.path_pair_id if path_pair is not None else None)
        final_target = os.path.join(final_path, file.name)

        if os.path.exists(final_target) or not staging_path:
            return final_path, file.name

        staging_target = os.path.join(staging_path, file.name)
        if os.path.exists(staging_target):
            return staging_path, file.name

        staging_target = os.path.join(staging_path, file.name + Constants.LFTP_TEMP_FILE_SUFFIX)
        if os.path.exists(staging_target):
            return staging_path, file.name + Constants.LFTP_TEMP_FILE_SUFFIX

        return final_path, file.name

    def __has_active_command_for_file(self, file_id: str) -> bool:
        return any(command_process.file_id == file_id for command_process in self.__active_command_processes)

    def __has_pending_delete_local_command(self, file_id: str) -> bool:
        if self.__has_active_command_for_file(file_id):
            return True
        with self.__command_queue.mutex:
            return any(
                command.action == Controller.Command.Action.DELETE_LOCAL and
                command.filename == file_id
                for command in self.__command_queue.queue
            )

    def __should_auto_purge_local_file(self, file: ModelFile) -> bool:
        if file.is_dir or file.remote_size is not None or file.local_size != 0:
            return False
        if file.state != ModelFile.State.DEFAULT:
            return False
        if self.__is_previously_downloaded(file.name, file.path_pair_id) or \
                self.__is_explicitly_stopped(file.name, file.path_pair_id):
            return False
        if file.file_id in self.__persist.extracted_file_names or file.name in self.__persist.extracted_file_names:
            return False
        return not self.__has_pending_delete_local_command(file.file_id)

    def __queue_delete_local_process(
        self,
        file: ModelFile,
        post_callback: Callable,
        command: "Controller.Command" = None
    ):
        delete_local_path, delete_local_name = self.__get_delete_local_target(file)
        process = DeleteLocalProcess(
            local_path=delete_local_path,
            file_name=delete_local_name
        )
        process.set_multiprocessing_logger(self.__mp_logger)
        command_wrapper = Controller.CommandProcessWrapper(
            command=command or Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id),
            file_id=file.file_id,
            file_name=file.name,
            process=process,
            post_callback=post_callback,
            await_completion=True
        )
        self.__active_command_processes.append(command_wrapper)
        command_wrapper.process.start()

    def __recover_interrupted_downloads(self, remote_files):
        self.__startup_recovery_done = True
        suffix = Constants.LFTP_TEMP_FILE_SUFFIX
        remote_names_by_pair = {}
        for remote_file in remote_files:
            remote_names_by_pair.setdefault(getattr(remote_file, "path_pair_id", None), set()).add(remote_file.name)

        staging_roots = self.__path_pair_staging_paths or {None: self.__staging_path}
        for path_pair_id, staging_path in staging_roots.items():
            try:
                staging_entries = os.listdir(staging_path)
            except OSError as error:
                self.logger.warning("Failed to inspect staging path '%s': %s", staging_path, error)
                continue

            remote_names = remote_names_by_pair.get(path_pair_id, set())
            for entry in staging_entries:
                entry_path = os.path.join(staging_path, entry)
                if entry.endswith(suffix):
                    file_name = entry[:-len(suffix)]
                    is_dir = False
                elif os.path.isdir(entry_path):
                    try:
                        has_temp_children = any(
                            child.endswith(suffix)
                            for child in os.listdir(entry_path)
                        )
                    except OSError:
                        continue
                    if not has_temp_children:
                        continue
                    file_name = entry
                    is_dir = True
                else:
                    continue

                if file_name not in remote_names or \
                        self.__is_previously_downloaded(file_name, path_pair_id) or \
                        self.__is_explicitly_stopped(file_name, path_pair_id):
                    continue

                path_pair = self.__get_path_pair(path_pair_id)
                try:
                    file_id = ModelFile.build_file_id(file_name, path_pair_id)
                    self.__log_stop_resume_trace(
                        "recover_interrupted_download",
                        file_id,
                        file_name,
                        path_pair_id,
                        is_dir,
                        None,
                        path_pair.remote_path if path_pair is not None else None,
                        staging_path,
                        False
                    )
                    self.__lftp.queue(
                        file_name,
                        is_dir,
                        remote_base_dir_path=path_pair.remote_path if path_pair is not None else None,
                        local_base_dir_path=staging_path
                    )
                    self.logger.info("Recovered interrupted download '%s' from '%s'", file_name, staging_path)
                except LftpError as error:
                    self.logger.warning(
                        "Failed to recover interrupted download '%s' from '%s': %s",
                        file_name,
                        staging_path,
                        error
                    )

    def __set_active_scanner_files(self, active_files):
        if isinstance(self.__active_scanner, MultiPathActiveScanner):
            self.__active_scanner.set_active_files(active_files)
        else:
            self.__active_scanner.set_active_files([name for name, _, _ in active_files])

    def __update_model(self):
        if not hasattr(self, "_Controller__malformed_status_only_file_ids"):
            self.__malformed_status_only_file_ids = set()
        if not hasattr(self, "_Controller__pending_auto_purge_file_ids"):
            self.__pending_auto_purge_file_ids = set()

        # Grab the latest scan results
        latest_remote_scan = self.__remote_scan_process.pop_latest_result()
        latest_local_scan = self.__local_scan_process.pop_latest_result()
        latest_active_scan = self.__active_scan_process.pop_latest_result()

        # Grab the Lftp status
        lftp_statuses = None
        lftp_status_poll_healthy = True
        try:
            lftp_statuses = self.__lftp.status()
            lftp_status_poll_healthy = getattr(self.__lftp, "last_status_poll_healthy", True)
        except (LftpError, LftpJobStatusParserError) as e:
            self.logger.warning("Caught lftp error: {}".format(str(e)))
            lftp_statuses = []
            lftp_status_poll_healthy = False

        # Grab the latest extract results
        latest_extract_statuses = self.__extract_process.pop_latest_statuses()
        latest_validation_statuses = self.__validate_process.pop_latest_statuses()

        # Grab the latest extracted file names
        latest_extracted_results = self.__extract_process.pop_completed()
        if latest_active_scan is not None:
            self.__malformed_status_only_file_ids.update(latest_active_scan.malformed_status_only_file_ids)

        # Update list of active file names
        if lftp_statuses is not None:
            active_status_file_ids = {status.file_id for status in lftp_statuses}
            self.__malformed_status_only_file_ids.intersection_update(active_status_file_ids)
            lftp_statuses = [
                status for status in lftp_statuses
                if status.file_id not in self.__malformed_status_only_file_ids
            ]
            self.__active_downloading_file_names = [
                (s.name, s.path_pair_id, s.path_pair_name)
                for s in lftp_statuses if s.state == LftpJobStatus.State.RUNNING
            ]
        if latest_extract_statuses is not None:
            self.__active_extracting_file_names = [
                (s.name, None, None)
                for s in latest_extract_statuses.statuses if s.state == ExtractStatus.State.EXTRACTING
            ]

        # Update the active scanner's state
        self.__set_active_scanner_files(
            self.__active_downloading_file_names + self.__active_extracting_file_names
        )

        # Update model builder state
        if latest_remote_scan is not None:
            self.__model_builder.set_remote_files(latest_remote_scan.files)
        if latest_local_scan is not None:
            self.__model_builder.set_local_files(latest_local_scan.files)
            recovered_extracted_file_ids = getattr(latest_local_scan, "managed_extract_file_ids", [])
            if isinstance(recovered_extracted_file_ids, (list, tuple, set)):
                self.__persist.extracted_file_names.update(recovered_extracted_file_ids)
        if latest_active_scan is not None:
            self.__model_builder.set_active_files(latest_active_scan.files)
        if lftp_statuses is not None:
            self.__model_builder.set_lftp_statuses(lftp_statuses)
            if not lftp_status_poll_healthy:
                self.__model_builder.evict_recent_live_transfer_snapshots_missing_roots(
                    {status.file_id for status in lftp_statuses}
                )
        if latest_extract_statuses is not None:
            self.__model_builder.set_extract_statuses(latest_extract_statuses.statuses)
            if self.__is_target_archive_trace_enabled():
                for status in latest_extract_statuses.statuses:
                    trace_target_file = self.__find_target_archive_model_file(status.name)
                    if trace_target_file is not None:
                        self.__trace_target_archive_event("extract_status", {
                            "file": self.__summarize_target_archive_file(trace_target_file),
                            "is_dir": status.is_dir,
                            "state": getattr(status.state, "name", status.state),
                        })
        if latest_validation_statuses is not None:
            self.__model_builder.set_validation_statuses(latest_validation_statuses.statuses)
        if latest_extracted_results:
            for result in latest_extracted_results:
                extracted_file_ids = {result.name}
                if result.file_id is not None:
                    extracted_file_ids.add(result.file_id)
                self.__persist.extracted_file_names.update(extracted_file_ids)
                trace_target_file = self.__find_target_archive_model_file(result.name, result.file_id)
                if trace_target_file is not None:
                    self.__trace_target_archive_event("extracted_marker_added", {
                        "file": self.__summarize_target_archive_file(trace_target_file),
                        "is_dir": result.is_dir,
                    })
            self.__model_builder.set_extracted_files(self.__persist.extracted_file_names)
        self.__model_builder.set_stopped_files(self.__persist.stopped_file_names)

        # Build the new model, if needed
        auto_purge_candidate_ids = set()
        remote_reconciliation_established = latest_remote_scan is not None and not latest_remote_scan.failed
        if self.__model_builder.has_changes():
            new_model = self.__model_builder.build_model()

            with self.__model_lock:
                # Diff the new model with old model
                model_diff = ModelDiffUtil.diff_models(self.__model, new_model)

                # Apply changes to the new model
                for diff in model_diff:
                    if diff.change == ModelDiff.Change.ADDED:
                        self.__model.add_file(diff.new_file)
                    elif diff.change == ModelDiff.Change.REMOVED:
                        self.__model.remove_file(diff.old_file.file_id)
                    elif diff.change == ModelDiff.Change.UPDATED:
                        self.__model.update_file(diff.new_file)

                    # Detect if a file was just Downloaded
                    #   an Added file in Downloaded state
                    #   an Updated file transitioning to Downloaded state
                    # If so, update the persist state
                    # Note: This step is done after the new model is build because
                    #       model_builder is the one that discovers when a file is Downloaded
                    downloaded = False
                    if diff.change == ModelDiff.Change.ADDED and \
                            diff.new_file.state == ModelFile.State.DOWNLOADED:
                        downloaded = True
                    elif diff.change == ModelDiff.Change.UPDATED and \
                            diff.new_file.state == ModelFile.State.DOWNLOADED and \
                            diff.old_file.state != ModelFile.State.DOWNLOADED:
                        downloaded = True
                    if downloaded:
                        self.__persist.downloaded_file_names.add(diff.new_file.file_id)
                        self.__model_builder.set_downloaded_files(self.__persist.downloaded_file_names)
                        self.clear_extracted_marker(diff.new_file)
                        if self.__target_archive_trace_selector_matches_file(diff.new_file.file_id, diff.new_file.name):
                            self.__trace_target_archive_event("downloaded_marker_added", {
                                "file": self.__summarize_target_archive_file(diff.new_file),
                            })
                        self.__move_from_staging(diff.new_file.name, diff.new_file.path_pair_id)

                current_auto_purge_candidate_ids = {
                    diff.new_file.file_id
                    for diff in model_diff
                    if diff.change in (ModelDiff.Change.ADDED, ModelDiff.Change.UPDATED) and
                    self.__should_auto_purge_local_file(diff.new_file)
                }
                if remote_reconciliation_established:
                    auto_purge_candidate_ids.update(current_auto_purge_candidate_ids)
                else:
                    self.__pending_auto_purge_file_ids.update(
                        current_auto_purge_candidate_ids
                    )

                # Prune the extracted files list of any files that were deleted locally
                # This prevents these files from going to EXTRACTED state if they are re-downloaded
                remove_extracted_file_names = set()
                existing_file_ids = self.__model.get_file_ids()
                for extracted_file_name in self.__persist.extracted_file_names:
                    if extracted_file_name in existing_file_ids:
                        file = self.__model.get_file(extracted_file_name)
                        if file.state == ModelFile.State.DELETED:
                            # Deleted locally, remove
                            remove_extracted_file_names.add(extracted_file_name)
                    elif extracted_file_name in self.__model.get_file_names():
                        try:
                            file = self.__model.get_file(extracted_file_name)
                        except ModelError:
                            continue
                        if file.state == ModelFile.State.DELETED:
                            remove_extracted_file_names.add(extracted_file_name)
                    else:
                        # Not in the model at all
                        # This could be because local and remote scans are not yet available
                        pass
                if remove_extracted_file_names:
                    self.logger.info("Removing from extracted list: {}".format(remove_extracted_file_names))
                    self.__persist.extracted_file_names.difference_update(remove_extracted_file_names)
                    if self.__is_target_archive_trace_enabled():
                        for extracted_file_name in remove_extracted_file_names:
                            if self.__target_archive_trace_selector_matches_file(extracted_file_name, extracted_file_name):
                                self.__trace_target_archive_event("extracted_marker_removed", {
                                    "file_name": extracted_file_name,
                                    "file_id": ModelFile.build_file_id(extracted_file_name, None),
                                })
                    self.__model_builder.set_extracted_files(self.__persist.extracted_file_names)

                active_model_names = set(self.__model.get_file_names())
                active_model_ids = set(self.__model.get_file_ids())
                remove_downloaded_file_names = {
                    downloaded_file_name
                    for downloaded_file_name in self.__persist.downloaded_file_names
                    if downloaded_file_name not in active_model_names and downloaded_file_name not in active_model_ids
                }
                if remove_downloaded_file_names:
                    self.logger.info("Removing from downloaded list: {}".format(remove_downloaded_file_names))
                    self.__persist.downloaded_file_names.difference_update(remove_downloaded_file_names)
                    if self.__is_target_archive_trace_enabled():
                        for downloaded_file_name in remove_downloaded_file_names:
                            if self.__target_archive_trace_selector_matches_file(downloaded_file_name, downloaded_file_name):
                                self.__trace_target_archive_event("downloaded_marker_removed", {
                                    "file_name": downloaded_file_name,
                                    "file_id": ModelFile.build_file_id(downloaded_file_name, None),
                                })
                    self.__model_builder.set_downloaded_files(self.__persist.downloaded_file_names)

        if remote_reconciliation_established and self.__pending_auto_purge_file_ids:
            pending_auto_purge_candidates = set()
            for file_id in list(self.__pending_auto_purge_file_ids):
                try:
                    file = self.__model.get_file(file_id)
                except ModelError:
                    self.__pending_auto_purge_file_ids.discard(file_id)
                    continue
                if self.__should_auto_purge_local_file(file):
                    pending_auto_purge_candidates.add(file_id)
                else:
                    self.__pending_auto_purge_file_ids.discard(file_id)
            auto_purge_candidate_ids.update(pending_auto_purge_candidates)
            self.__pending_auto_purge_file_ids.difference_update(auto_purge_candidate_ids)

        for file_id in auto_purge_candidate_ids:
            file = self.__model.get_file(file_id)
            self.__queue_delete_local_process(file, self.__local_scan_process.force_scan)

        # Update the controller status
        if latest_remote_scan is not None:
            self.__context.status.controller.latest_remote_scan_time = latest_remote_scan.timestamp
            self.__context.status.controller.latest_remote_scan_failed = latest_remote_scan.failed
            self.__context.status.controller.latest_remote_scan_error = latest_remote_scan.error_message
            if not latest_remote_scan.failed and not self.__startup_recovery_done:
                self.__recover_interrupted_downloads(latest_remote_scan.files)
        if latest_local_scan is not None:
            self.__context.status.controller.latest_local_scan_time = latest_local_scan.timestamp

    def __process_commands(self):
        def _notify_failure(_command: Controller.Command, _msg: str, _error_code: int = 400):
            self.logger.warning("Command failed. {}".format(_msg))
            for _callback in _command.callbacks:
                _callback.on_failure(_msg, _error_code)

        while not self.__command_queue.empty():
            command = self.__command_queue.get()
            self.logger.info("Received command {} for file {}".format(str(command.action), command.filename))
            try:
                file = self.__model.get_file(command.filename)
            except ModelError:
                _notify_failure(command, "File '{}' not found".format(command.filename), 404)
                continue

            if command.action == Controller.Command.Action.QUEUE:
                if file.remote_size is None:
                    _notify_failure(command, "File '{}' does not exist remotely".format(command.filename), 404)
                    continue
                try:
                    path_pair = self.__get_path_pair(file.path_pair_id)
                    local_base_dir_path = self.__get_staging_path(file.path_pair_id if path_pair else None)
                    stopped_marked = file.file_id in self.__persist.stopped_file_names or \
                        file.name in self.__persist.stopped_file_names
                    self.__log_stop_resume_trace(
                        "queue_after_stop" if stopped_marked else "queue_fresh",
                        file.file_id,
                        file.name,
                        file.path_pair_id,
                        file.is_dir,
                        getattr(file.state, "name", file.state),
                        path_pair.remote_path if path_pair else None,
                        local_base_dir_path,
                        stopped_marked
                    )
                    self.__lftp.queue(
                        file.name,
                        file.is_dir,
                        remote_base_dir_path=path_pair.remote_path if path_pair else None,
                        local_base_dir_path=local_base_dir_path
                    )
                    self.__persist.stopped_file_names.discard(file.file_id)
                    self.__persist.stopped_file_names.discard(file.name)
                except LftpError as e:
                    _notify_failure(command, "Lftp error: {}".format(str(e)), 500)
                    continue

            elif command.action == Controller.Command.Action.STOP:
                if file.state not in (ModelFile.State.DOWNLOADING, ModelFile.State.QUEUED):
                    _notify_failure(
                        command,
                        "File '{}' is not Queued or Downloading".format(command.filename),
                        409
                    )
                    continue
                if not file.is_stoppable:
                    _notify_failure(
                        command,
                        "File '{}' could not be stopped".format(command.filename),
                        409
                    )
                    continue
                try:
                    path_pair = self.__get_path_pair(file.path_pair_id)
                    remote_path = None
                    local_path = None
                    local_base_dir_path = self.__get_staging_path(file.path_pair_id if path_pair else None)
                    if path_pair is not None:
                        remote_path = "/".join([path_pair.remote_path.rstrip("/"), file.name])
                        local_path = self.__path_pair_staging_paths.get(file.path_pair_id, path_pair.local_path)
                    stopped_marked = file.file_id in self.__persist.stopped_file_names or \
                        file.name in self.__persist.stopped_file_names
                    self.__log_stop_resume_trace(
                        "stop",
                        file.file_id,
                        file.name,
                        file.path_pair_id,
                        file.is_dir,
                        getattr(file.state, "name", file.state),
                        path_pair.remote_path if path_pair else None,
                        local_base_dir_path,
                        stopped_marked
                    )
                    killed = self.__lftp.kill(
                        file.name,
                        path_pair_id=file.path_pair_id,
                        remote_path=remote_path,
                        local_path=local_path
                    )
                    if not killed:
                        _notify_failure(
                            command,
                            "File '{}' could not be stopped".format(command.filename),
                            409
                        )
                        continue
                    self.__persist.stopped_file_names.add(file.file_id)
                except (LftpError, LftpJobStatusParserError) as e:
                    _notify_failure(command, "Lftp error: {}".format(str(e)), 500)
                    continue

            elif command.action == Controller.Command.Action.EXTRACT:
                # Note: We don't check the is_extractable flag because it's just a guess
                should_trace_target = self.__target_archive_trace_selector_matches_file(file.file_id, file.name)
                if file.state not in (
                        ModelFile.State.DEFAULT,
                        ModelFile.State.DOWNLOADED,
                        ModelFile.State.EXTRACTED
                ):
                    if should_trace_target:
                        self.__trace_target_archive_event("extract_command_blocked", {
                            "file": self.__summarize_target_archive_file(file),
                            "reason": "state_not_allowed",
                        })
                    _notify_failure(
                        command,
                        "File '{}' in state {} cannot be extracted".format(
                            command.filename, str(file.state)
                        ),
                        409
                    )
                    continue
                elif file.local_size is None:
                    if should_trace_target:
                        self.__trace_target_archive_event("extract_command_blocked", {
                            "file": self.__summarize_target_archive_file(file),
                            "reason": "missing_local_file",
                        })
                    _notify_failure(command, "File '{}' does not exist locally".format(command.filename), 404)
                    continue
                else:
                    if should_trace_target:
                        self.__trace_target_archive_event("extract_command_queued", {
                            "file": self.__summarize_target_archive_file(file),
                        })
                    self.__extract_process.extract(file)

            elif command.action == Controller.Command.Action.VALIDATE:
                if file.state not in (
                    ModelFile.State.DOWNLOADED,
                    ModelFile.State.EXTRACTED,
                    ModelFile.State.VALIDATED,
                    ModelFile.State.CORRUPT
                ):
                    _notify_failure(
                        command,
                        "File '{}' in state {} cannot be validated".format(
                            command.filename, str(file.state)
                        ),
                        409
                    )
                    continue
                elif file.local_size is None:
                    _notify_failure(command, "File '{}' does not exist locally".format(command.filename), 404)
                    continue
                elif file.remote_size is None:
                    _notify_failure(command, "File '{}' does not exist remotely".format(command.filename), 404)
                    continue
                else:
                    self.__validate_process.validate(file)

            elif command.action == Controller.Command.Action.DELETE_LOCAL:
                if file.state not in (
                    ModelFile.State.DEFAULT,
                    ModelFile.State.DOWNLOADED,
                    ModelFile.State.EXTRACTED
                ):
                    _notify_failure(
                        command,
                        "Local file '{}' cannot be deleted in state {}".format(
                            command.filename, str(file.state)
                        ),
                        409
                    )
                    continue
                elif file.local_size is None:
                    _notify_failure(command, "File '{}' does not exist locally".format(command.filename), 404)
                    continue
                else:
                    self.__queue_delete_local_process(
                        file,
                        self.__local_scan_process.force_scan,
                        command=command
                    )
                    self.__persist.stopped_file_names.add(file.file_id)
                    self.__validate_process.clear(file.file_id)

            elif command.action == Controller.Command.Action.DELETE_REMOTE:
                if file.state not in (
                    ModelFile.State.DEFAULT,
                    ModelFile.State.DOWNLOADED,
                    ModelFile.State.EXTRACTED,
                    ModelFile.State.DELETED
                ):
                    _notify_failure(
                        command,
                        "Remote file '{}' cannot be deleted in state {}".format(
                            command.filename, str(file.state)
                        ),
                        409
                    )
                    continue
                elif file.remote_size is None:
                    _notify_failure(command, "File '{}' does not exist remotely".format(command.filename), 404)
                    continue
                else:
                    process = DeleteRemoteProcess(
                        remote_address=self.__context.config.lftp.remote_address,
                        remote_username=self.__context.config.lftp.remote_username,
                        remote_password=self.__password,
                        remote_port=self.__context.config.lftp.remote_port,
                        remote_path=path_pair.remote_path if (path_pair := self.__get_path_pair(file.path_pair_id))
                        else self.__context.config.lftp.remote_path,
                        file_name=file.name
                    )
                    process.set_multiprocessing_logger(self.__mp_logger)
                    post_callback = self.__remote_scan_process.force_scan
                    command_wrapper = Controller.CommandProcessWrapper(
                        command=command,
                        file_id=file.file_id,
                        file_name=file.name,
                        process=process,
                        post_callback=post_callback,
                        await_completion=False
                    )
                    self.__active_command_processes.append(command_wrapper)
                    command_wrapper.process.start()
                    self.__validate_process.clear(file.file_id)

            # If we get here, it was a success
            if command.action in (
                Controller.Command.Action.QUEUE,
                Controller.Command.Action.STOP
            ):
                self.__validate_process.clear(file.file_id)
            if command.action != Controller.Command.Action.DELETE_LOCAL:
                for callback in command.callbacks:
                    callback.on_success()

    def __log_memory_usage(self):
        with self.__model_lock:
            get_ids = getattr(self.__model, "get_file_ids", None)
            if callable(get_ids):
                model_file_count = len(get_ids())
            else:
                model_file_count = len(self.__model.get_file_names())

        self.__memory_monitor.log_if_due(
            model_file_count=model_file_count,
            downloaded_file_count=len(self.__persist.downloaded_file_names),
            extracted_file_count=len(self.__persist.extracted_file_names),
            stopped_file_count=len(self.__persist.stopped_file_names),
            active_download_count=len(self.__active_downloading_file_names),
            active_extract_count=len(self.__active_extracting_file_names),
            active_command_count=len(self.__active_command_processes)
        )

    def __propagate_exceptions(self):
        """
        Propagate any exceptions from child processes/threads to this thread
        :return:
        """
        try:
            self.__lftp.raise_pending_error()
        except LftpError as e:
            self.logger.warning("Caught lftp error: {}".format(str(e)))
        self.__active_scan_process.propagate_exception()
        self.__local_scan_process.propagate_exception()
        try:
            self.__remote_scan_process.propagate_exception()
        except Exception as error:
            self.__record_first_remote_scan_failure(str(error))
            raise
        self.__mp_logger.propagate_exception()
        self.__extract_process.propagate_exception()
        self.__validate_process.propagate_exception()

    def __record_first_remote_scan_failure(self, error_message: str):
        self.logger.warning("Fatal remote scan failure recorded: {}".format(error_message))
        self.__context.status.controller.latest_remote_scan_time = datetime.now()
        self.__context.status.controller.latest_remote_scan_failed = True
        self.__context.status.controller.latest_remote_scan_error = error_message

    def __cleanup_commands(self):
        """
        Cleanup the list of active commands and do any callbacks
        :return:
        """
        still_active_processes = []
        for command_process in self.__active_command_processes:
            if command_process.process.is_alive():
                still_active_processes.append(command_process)
            else:
                if command_process.await_completion:
                    try:
                        command_process.process.propagate_exception()
                    except FileNotFoundError as error:
                        self.logger.warning(
                            "Command {} for file {} failed: {}".format(
                                command_process.command.action,
                                command_process.file_name,
                                error
                            )
                        )
                        self.__persist.stopped_file_names.discard(command_process.file_id)
                        for callback in command_process.command.callbacks:
                            callback.on_failure(
                                "File '{}' does not exist locally".format(command_process.file_name),
                                404
                            )
                    except Exception as error:
                        self.logger.warning(
                            "Command {} for file {} failed: {}".format(
                                command_process.command.action,
                                command_process.file_name,
                                error
                            )
                        )
                        self.__persist.stopped_file_names.discard(command_process.file_id)
                        for callback in command_process.command.callbacks:
                            callback.on_failure(
                                "Failed to delete local file '{}'".format(command_process.file_name),
                                500
                            )
                    else:
                        command_process.post_callback()
                        for callback in command_process.command.callbacks:
                            callback.on_success()
                else:
                    # Do the post callback
                    command_process.post_callback()
                    # Propagate the exception
                    command_process.process.propagate_exception()
        self.__active_command_processes = still_active_processes
