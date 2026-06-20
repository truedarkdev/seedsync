# Copyright 2017, Inderpreet Singh, All rights reserved.

from enum import Enum
from typing import List, Optional
import queue
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
import re

from .extract import Extract, ExtractError
from common.managed_extract import (
    build_managed_extract_folder_name,
    build_managed_extract_folder_path,
    write_managed_extract_marker,
)
from model import ModelFile
from common import AppError


class ExtractDispatchError(AppError):
    pass


class ExtractListener(ABC):
    @abstractmethod
    def extract_completed(self,
                          name: str,
                          is_dir: bool,
                          file_id: str | None = None,
                          path_pair_id: str | None = None):
        pass

    @abstractmethod
    def extract_failed(self,
                       name: str,
                       is_dir: bool,
                       file_id: str | None = None,
                       path_pair_id: str | None = None):
        pass


class ExtractStatus:
    """
    Represents the status of a single extraction request
    """

    class State(Enum):
        EXTRACTING = 0

    def __init__(self,
                 name: str,
                 is_dir: bool,
                 state: State,
                 file_id: str | None = None,
                 path_pair_id: str | None = None):
        self.__name = name
        self.__is_dir = is_dir
        self.__state = state
        self.__file_id = file_id
        self.__path_pair_id = path_pair_id

    @property
    def name(self) -> str: return self.__name

    @property
    def is_dir(self) -> bool: return self.__is_dir

    @property
    def state(self) -> State: return self.__state

    @property
    def file_id(self) -> str | None: return self.__file_id

    @property
    def path_pair_id(self) -> str | None: return self.__path_pair_id

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class ExtractDispatch:

    __WORKER_SLEEP_INTERVAL_IN_SECS = 0.5

    class _Task:
        def __init__(self,
                     root_name: str,
                     root_is_dir: bool,
                     root_file_id: str | None = None,
                     path_pair_id: str | None = None):
            self.root_name = root_name
            self.root_is_dir = root_is_dir
            self.root_file_id = root_file_id
            self.path_pair_id = path_pair_id
            self.archive_paths = []  # list of (archive path, out path, archive name, archive file_id, path_pair_id) tuples

        def add_archive(self,
                        archive_path: str,
                        out_dir_path: str,
                        archive_name: str,
                        archive_file_id: str | None = None,
                        path_pair_id: str | None = None):
            self.archive_paths.append((archive_path, out_dir_path, archive_name, archive_file_id, path_pair_id))

    def __init__(self,
                 out_dir_path: str,
                 local_path: str,
                 local_path_fallback: str | None = None,
                 managed_extract_folders_enabled: bool = True):
        self.__out_dir_path = out_dir_path
        self.__local_path = local_path
        self.__local_path_fallback = local_path_fallback
        self.__managed_extract_folders_enabled = managed_extract_folders_enabled

        self.__task_queue = queue.Queue()
        self.__worker_thread: threading.Thread = threading.Thread(name="ExtractWorker", target=self.__worker)
        self.__worker_shutdown = threading.Event()

        self.__listeners = []
        self.__listeners_lock = threading.Lock()

        self.logger = logging.getLogger(self.__class__.__name__)

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild(self.__class__.__name__)

    def start(self):
        self.__worker_thread.start()

    def stop(self):
        self.__worker_shutdown.set()
        self.__worker_thread.join()

    def add_listener(self, listener: ExtractListener):
        with self.__listeners_lock:
            self.__listeners.append(listener)

    def status(self) -> List[ExtractStatus]:
        with self.__task_queue.mutex:
            tasks = list(self.__task_queue.queue)
        statuses = []
        for task in tasks:
            status = ExtractStatus(name=task.root_name,
                                   is_dir=task.root_is_dir,
                                   state=ExtractStatus.State.EXTRACTING,
                                   file_id=task.root_file_id,
                                   path_pair_id=task.path_pair_id)
            statuses.append(status)
        return statuses

    def __resolve_archive_path(self, relative_path: str) -> Optional[str]:
        primary = os.path.join(self.__local_path, relative_path)
        if Extract.is_archive(primary):
            return primary
        if self.__local_path_fallback and self.__local_path_fallback != self.__local_path:
            fallback = os.path.join(self.__local_path_fallback, relative_path)
            if Extract.is_archive(fallback):
                return fallback
        return None

    def extract(self, model_file: ModelFile):
        self.logger.debug("Received extract for {}".format(model_file.name))

        # Build the task before taking the queue mutex.
        # noinspection PyProtectedMember
        task = ExtractDispatch._Task(
            model_file.name,
            model_file.is_dir,
            model_file.file_id,
            model_file.path_pair_id
        )

        if model_file.is_dir:
            # For a directory, try and find all archives
            # Loop through all directories using BFS
            frontier = [model_file]
            while frontier:
                curr_file = frontier.pop(0)
                if curr_file.is_dir:
                    frontier += curr_file.get_children()
                else:
                    archive_full_path = self.__resolve_archive_path(curr_file.full_path)
                    out_dir_path = os.path.join(self.__out_dir_path, os.path.dirname(curr_file.full_path))
                    if curr_file.local_size is not None \
                            and curr_file.local_size > 0 \
                            and archive_full_path is not None:
                        task.add_archive(
                            archive_path=archive_full_path,
                            out_dir_path=out_dir_path,
                            archive_name=curr_file.name,
                            archive_file_id=curr_file.file_id,
                            path_pair_id=curr_file.path_pair_id
                        )

            # Coalesce extractions
            ExtractDispatch.__coalesce_extractions(task)

            if len(task.archive_paths) == 0:
                raise ExtractDispatchError(
                    "Directory does not contain any archives: {}".format(model_file.name)
                )
        else:
            # For a single file, it must exist locally and must be an archive
            if model_file.local_size in (None, 0):
                raise ExtractDispatchError("File does not exist locally: {}".format(model_file.name))
            archive_full_path = self.__resolve_archive_path(model_file.name)
            if not archive_full_path:
                raise ExtractDispatchError("File is not an archive: {}".format(model_file.name))
            task.add_archive(
                archive_path=archive_full_path,
                out_dir_path=self.__out_dir_path,
                archive_name=model_file.name,
                archive_file_id=model_file.file_id,
                path_pair_id=model_file.path_pair_id
            )

        with self.__task_queue.mutex:
            for queued_task in self.__task_queue.queue:
                if queued_task.root_name == model_file.name:
                    self.logger.info("Ignoring extract for {}, already exists".format(model_file.name))
                    return
            self.__task_queue.queue.append(task)
            self.__task_queue.not_empty.notify()

    def __worker(self):
        self.logger.debug("Started worker thread")

        while not self.__worker_shutdown.is_set():
            with self.__task_queue.mutex:
                has_tasks = len(self.__task_queue.queue) > 0

            while has_tasks and not self.__worker_shutdown.is_set():
                with self.__task_queue.mutex:
                    if len(self.__task_queue.queue) == 0:
                        break
                    task = self.__task_queue.queue[0]

                # We have a task, extract archives one by one
                completed = True

                try:
                    for archive_path, out_dir_path, archive_name, archive_file_id, path_pair_id in task.archive_paths:
                        if self.__worker_shutdown.is_set():
                            # exit early
                            self.logger.warning("Extraction failed, shutdown requested")
                            completed = False
                            break

                        self.logger.debug("Extracting {}".format(archive_path))
                        resolved_out_dir_path = out_dir_path
                        if self.__managed_extract_folders_enabled:
                            managed_extract_folder_name = build_managed_extract_folder_name(archive_name)
                            out_dir_name = os.path.basename(os.path.normpath(out_dir_path))
                            if out_dir_name.casefold() != managed_extract_folder_name.casefold():
                                resolved_out_dir_path = build_managed_extract_folder_path(
                                    out_dir_path,
                                    archive_name
                                )
                        Extract.extract_archive(
                            archive_path=archive_path,
                            out_dir_path=resolved_out_dir_path
                        )
                        if self.__managed_extract_folders_enabled:
                            try:
                                write_managed_extract_marker(
                                    resolved_out_dir_path,
                                    archive_name=archive_name,
                                    archive_file_id=archive_file_id,
                                    path_pair_id=path_pair_id
                                )
                            except OSError as error:
                                raise ExtractError(str(error))

                except ExtractError:
                    self.logger.exception("Caught an extraction error")
                    completed = False
                finally:
                    try:
                        self.__task_queue.get(block=False)
                    except queue.Empty:
                        pass

                with self.__listeners_lock:
                    listeners_snapshot = list(self.__listeners)
                for listener in listeners_snapshot:
                    if completed:
                        listener.extract_completed(
                            task.root_name,
                            task.root_is_dir,
                            task.root_file_id,
                            task.path_pair_id
                        )
                    else:
                        listener.extract_failed(
                            task.root_name,
                            task.root_is_dir,
                            task.root_file_id,
                            task.path_pair_id
                        )

                with self.__task_queue.mutex:
                    has_tasks = len(self.__task_queue.queue) > 0

            time.sleep(ExtractDispatch.__WORKER_SLEEP_INTERVAL_IN_SECS)

        self.logger.debug("Stopped worker thread")

    @staticmethod
    def __coalesce_extractions(task: _Task):
        """
        Remove duplicate extractions due to split files
        :param task:
        :return:
        """
        # Filter out any rxx files for a split rar
        filtered_paths = []
        for archive_path, out_path, archive_name, archive_file_id, path_pair_id in task.archive_paths:
            file_ext = os.path.splitext(os.path.basename(archive_path))[1]
            if not re.match(r"^\.r\d{2,}$", file_ext):
                filtered_paths.append((archive_path, out_path, archive_name, archive_file_id, path_pair_id))
        task.archive_paths = filtered_paths
