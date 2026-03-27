# Copyright 2017, Inderpreet Singh, All rights reserved.

import multiprocessing
import datetime
import time
import queue
from typing import Optional, List
import logging
import os
import json

from .dispatch import ExtractDispatch, ExtractStatus, ExtractListener, ExtractDispatchError
from common import overrides, AppProcess
from model import ModelFile


class ExtractStatusResult:
    def __init__(self, timestamp: datetime, statuses: List[ExtractStatus]):
        self.timestamp = timestamp
        self.statuses = statuses


class ExtractCompletedResult:
    def __init__(self,
                 timestamp: datetime,
                 name: str,
                 is_dir: bool,
                 file_id: str = None,
                 path_pair_id: str = None):
        self.timestamp = timestamp
        self.name = name
        self.is_dir = is_dir
        self.file_id = file_id
        self.path_pair_id = path_pair_id


class ExtractProcess(AppProcess):
    __DEFAULT_SLEEP_INTERVAL_IN_SECS = 0.5

    class __ExtractListener(ExtractListener):
        def __init__(self,
                     logger: logging.Logger,
                     completed_queue: multiprocessing.Queue,
                     trace_owner: "ExtractProcess"):
            self.logger = logger
            self.completed_queue = completed_queue
            self.trace_owner = trace_owner

        def extract_completed(self,
                              name: str,
                              is_dir: bool,
                              file_id: str = None,
                              path_pair_id: str = None):
            self.logger.info("Extraction completed for {}".format(name))
            self.trace_owner._ExtractProcess__trace_target_archive_event("extract_completed", {
                "file_name": name,
                "is_dir": is_dir,
                "file_id": file_id,
                "path_pair_id": path_pair_id,
            })
            completed_result = ExtractCompletedResult(timestamp=datetime.datetime.now(),
                                                      name=name,
                                                      is_dir=is_dir,
                                                      file_id=file_id,
                                                      path_pair_id=path_pair_id)
            self.completed_queue.put(completed_result)

        def extract_failed(self,
                           name: str,
                           is_dir: bool,
                           file_id: str = None,
                           path_pair_id: str = None):
            self.logger.error("Extraction failed for {}".format(name))
            self.trace_owner._ExtractProcess__trace_target_archive_event("extract_failed", {
                "file_name": name,
                "is_dir": is_dir,
                "file_id": file_id,
                "path_pair_id": path_pair_id,
            })

    def __init__(self, out_dir_path: str, local_path: str, managed_extract_folders_enabled: bool = True):
        super().__init__(name=self.__class__.__name__)
        self.__out_dir_path = out_dir_path
        self.__local_path = local_path
        self.__managed_extract_folders_enabled = managed_extract_folders_enabled
        self.__command_queue = multiprocessing.Queue()
        self.__status_result_queue = multiprocessing.Queue()
        self.__completed_result_queue = multiprocessing.Queue()
        self.__dispatch = None
        self.__target_archive_trace_file_id = os.environ.get("SEEDSYNC_TARGET_ARCHIVE_TRACE_FILE_ID")
        if self.__target_archive_trace_file_id is not None and not self.__target_archive_trace_file_id.strip():
            self.__target_archive_trace_file_id = None
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        self.__target_archive_trace_last_signature = None

    @staticmethod
    def __extract_trace_selector_name(identifier: str):
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

    def __target_archive_trace_selector_matches_name(self, file_name: str) -> bool:
        if not self.__is_target_archive_trace_enabled():
            return False
        if self.__target_archive_trace_file_id == file_name:
            return True
        selector_name = self.__extract_trace_selector_name(self.__target_archive_trace_file_id)
        return selector_name == file_name

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

    @overrides(AppProcess)
    def run_init(self):
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        # Create dispatch inside the process
        self.__dispatch = ExtractDispatch(out_dir_path=self.__out_dir_path,
                                          local_path=self.__local_path,
                                          managed_extract_folders_enabled=self.__managed_extract_folders_enabled)

        # Add extract listener
        listener = ExtractProcess.__ExtractListener(
            logger=self.logger,
            completed_queue=self.__completed_result_queue,
            trace_owner=self
        )
        self.__dispatch.add_listener(listener)

        # Start dispatch
        self.__dispatch.start()

    @overrides(AppProcess)
    def run_cleanup(self):
        self.__dispatch.stop()

    @overrides(AppProcess)
    def run_loop(self):
        # Forward all the extract commands
        try:
            while True:
                file = self.__command_queue.get(block=False)
                try:
                    self.__dispatch.extract(file)
                except ExtractDispatchError as e:
                    self.logger.warning(str(e))
                    if self.__target_archive_trace_selector_matches_name(file.name):
                        self.__trace_target_archive_event("extract_dispatch_blocked", {
                            "file_name": file.name,
                            "is_dir": file.is_dir,
                            "reason": str(e),
                        })
        except queue.Empty:
            pass

        # Queue the latest status
        statuses = self.__dispatch.status()
        status_result = ExtractStatusResult(timestamp=datetime.datetime.now(),
                                            statuses=statuses)
        self.__status_result_queue.put(status_result)

        time.sleep(ExtractProcess.__DEFAULT_SLEEP_INTERVAL_IN_SECS)

    def extract(self, file: ModelFile):
        """
        Process-safe method to queue an extraction
        :param file:
        :return:
        """
        self.__command_queue.put(file)

    def pop_latest_statuses(self) -> Optional[ExtractStatusResult]:
        """
        Process-safe method to retrieve latest extract status
        Returns none if no new status is available since the last time
        this method was called
        :return:
        """
        latest_result = None
        try:
            while True:
                latest_result = self.__status_result_queue.get(block=False)
        except queue.Empty:
            pass
        return latest_result

    def pop_completed(self) -> List[ExtractCompletedResult]:
        """
        Process-safe method to retrieve list of newly completed extractions
        Returns an empty list if no new extractions were completed since the
        last time this method was called.
        :return:
        """
        completed = []
        try:
            while True:
                result = self.__completed_result_queue.get(block=False)
                completed.append(result)
        except queue.Empty:
            pass
        return completed
