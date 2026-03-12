# Copyright 2017, Inderpreet Singh, All rights reserved.

from abc import ABC, abstractmethod
from typing import List, Callable
from threading import Lock
from queue import Queue
from enum import Enum
import copy
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
        def __init__(self, process: AppOneShotProcess, post_callback: Callable):
            self.process = process
            self.post_callback = post_callback

    def __init__(self,
                 context: Context,
                 persist: ControllerPersist):
        self.__context = context
        self.__persist = persist
        self.logger = context.logger.getChild("Controller")

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
            })
            self.__local_scanner = MultiPathLocalScanner([
                LocalScanner(
                    local_path=pair.local_path,
                    use_temp_file=self.__context.config.lftp.use_temp_file,
                    staging_path=self.__path_pair_staging_paths[pair.id],
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
            self.__active_scanner = ActiveScanner(self.__staging_path)
            self.__local_scanner = LocalScanner(
                local_path=self.__context.config.lftp.local_path,
                use_temp_file=self.__context.config.lftp.use_temp_file,
                staging_path=self.__staging_path
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
            local_path=self.__context.config.lftp.local_path
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

        # Keep track of active command processes
        self.__active_command_processes = []
        self.__startup_recovery_done = False

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
        if not os.path.exists(src):
            return
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            return

        try:
            shutil.move(src, dst)
            self.logger.info("Moved '%s' from staging '%s' to '%s'", name, staging_path, final_path)
            self.__local_scan_process.force_scan()
        except OSError as error:
            self.logger.warning(
                "Failed to move '%s' from staging '%s' to '%s': %s",
                name,
                staging_path,
                final_path,
                error
            )

    def __get_delete_local_path(self, file: ModelFile) -> str:
        path_pair = self.__get_path_pair(file.path_pair_id)
        final_path = path_pair.local_path if path_pair is not None else self.__context.config.lftp.local_path
        staging_path = self.__get_staging_path(file.path_pair_id if path_pair is not None else None)
        final_target = os.path.join(final_path, file.name)

        if os.path.exists(final_target) or not staging_path:
            return final_path

        staging_target = os.path.join(staging_path, file.name)
        if os.path.exists(staging_target):
            return staging_path

        return final_path

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
        # Grab the latest scan results
        latest_remote_scan = self.__remote_scan_process.pop_latest_result()
        latest_local_scan = self.__local_scan_process.pop_latest_result()
        latest_active_scan = self.__active_scan_process.pop_latest_result()

        # Grab the Lftp status
        lftp_statuses = None
        try:
            lftp_statuses = self.__lftp.status()
        except (LftpError, LftpJobStatusParserError) as e:
            self.logger.warning("Caught lftp error: {}".format(str(e)))

        # Grab the latest extract results
        latest_extract_statuses = self.__extract_process.pop_latest_statuses()
        latest_validation_statuses = self.__validate_process.pop_latest_statuses()

        # Grab the latest extracted file names
        latest_extracted_results = self.__extract_process.pop_completed()

        # Update list of active file names
        if lftp_statuses is not None:
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
        if latest_active_scan is not None:
            self.__model_builder.set_active_files(latest_active_scan.files)
        if lftp_statuses is not None:
            self.__model_builder.set_lftp_statuses(lftp_statuses)
        if latest_extract_statuses is not None:
            self.__model_builder.set_extract_statuses(latest_extract_statuses.statuses)
        if latest_validation_statuses is not None:
            self.__model_builder.set_validation_statuses(latest_validation_statuses.statuses)
        if latest_extracted_results:
            for result in latest_extracted_results:
                self.__persist.extracted_file_names.add(result.name)
            self.__model_builder.set_extracted_files(self.__persist.extracted_file_names)

        # Build the new model, if needed
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
                        self.__move_from_staging(diff.new_file.name, diff.new_file.path_pair_id)

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
                    self.__model_builder.set_extracted_files(self.__persist.extracted_file_names)

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
                    self.__lftp.queue(
                        file.name,
                        file.is_dir,
                        remote_base_dir_path=path_pair.remote_path if path_pair else None,
                        local_base_dir_path=self.__get_staging_path(file.path_pair_id if path_pair else None)
                    )
                    self.__persist.stopped_file_names.discard(file.file_id)
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
                try:
                    path_pair = self.__get_path_pair(file.path_pair_id)
                    remote_path = None
                    local_path = None
                    if path_pair is not None:
                        remote_path = "/".join([path_pair.remote_path.rstrip("/"), file.name])
                        local_path = self.__path_pair_staging_paths.get(file.path_pair_id, path_pair.local_path)
                    self.__lftp.kill(
                        file.name,
                        path_pair_id=file.path_pair_id,
                        remote_path=remote_path,
                        local_path=local_path
                    )
                    self.__persist.stopped_file_names.add(file.file_id)
                except (LftpError, LftpJobStatusParserError) as e:
                    _notify_failure(command, "Lftp error: {}".format(str(e)), 500)
                    continue

            elif command.action == Controller.Command.Action.EXTRACT:
                # Note: We don't check the is_extractable flag because it's just a guess
                if file.state not in (
                        ModelFile.State.DEFAULT,
                        ModelFile.State.DOWNLOADED,
                        ModelFile.State.EXTRACTED
                ):
                    _notify_failure(
                        command,
                        "File '{}' in state {} cannot be extracted".format(
                            command.filename, str(file.state)
                        ),
                        409
                    )
                    continue
                elif file.local_size is None:
                    _notify_failure(command, "File '{}' does not exist locally".format(command.filename), 404)
                    continue
                else:
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
                    process = DeleteLocalProcess(
                        local_path=self.__get_delete_local_path(file),
                        file_name=file.name
                    )
                    process.set_multiprocessing_logger(self.__mp_logger)
                    post_callback = self.__local_scan_process.force_scan
                    command_wrapper = Controller.CommandProcessWrapper(
                        process=process,
                        post_callback=post_callback
                    )
                    self.__active_command_processes.append(command_wrapper)
                    command_wrapper.process.start()
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
                        process=process,
                        post_callback=post_callback
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
            for callback in command.callbacks:
                callback.on_success()

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
        self.__remote_scan_process.propagate_exception()
        self.__mp_logger.propagate_exception()
        self.__extract_process.propagate_exception()
        self.__validate_process.propagate_exception()

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
                # Do the post callback
                command_process.post_callback()
                # Propagate the exception
                command_process.process.propagate_exception()
        self.__active_command_processes = still_active_processes
