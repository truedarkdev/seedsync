# Copyright 2017, Inderpreet Singh, All rights reserved.

import multiprocessing
import time
import queue
import threading
from datetime import datetime
from typing import Any, Optional, List, cast
import logging
import os
import json
from collections import defaultdict, deque

from .dispatch import ExtractDispatch, ExtractStatus, ExtractListener, ExtractDispatchError
from .extract_request import ExtractRequest
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
                 file_id: Optional[str] = None,
                 path_pair_id: Optional[str] = None):
        self.timestamp = timestamp
        self.name = name
        self.is_dir = is_dir
        self.file_id = file_id
        self.path_pair_id = path_pair_id


class ExtractFailedResult:
    def __init__(self,
                 timestamp: datetime,
                 name: str,
                 is_dir: bool,
                 file_id: Optional[str] = None,
                 path_pair_id: Optional[str] = None):
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
                     trace_owner: "ExtractProcess",
                     failed_queue: Optional[multiprocessing.Queue] = None):
            self.logger = logger
            self.completed_queue = completed_queue
            self.trace_owner = trace_owner
            self.failed_queue = failed_queue

        def extract_completed(self,
                              name: str,
                              is_dir: bool,
                              file_id: Optional[str] = None,
                              path_pair_id: Optional[str] = None):
            self.logger.info("Extraction completed for {}".format(name))
            trace_owner: Any = self.trace_owner
            flow_id = trace_owner._ExtractProcess__pop_inflight_flow_id(file_id=file_id, file_name=name)
            trace_owner._ExtractProcess__record_breadcrumb(
                "extract_completed",
                {
                    "file_name": name,
                    "is_dir": is_dir,
                    "file_id": file_id,
                    "path_pair_id": path_pair_id,
                },
                corr_id=trace_owner._ExtractProcess__trace_corr_id(file_id, path_pair_id, name),
                flow_id=flow_id,
                file_id=file_id,
                path_pair_id=path_pair_id,
            )
            trace_owner._ExtractProcess__trace_target_archive_event("extract_completed", {
                "file_name": name,
                "is_dir": is_dir,
                "file_id": file_id,
                "path_pair_id": path_pair_id,
            })
            completed_result = ExtractCompletedResult(timestamp=datetime.now(),
                                                      name=name,
                                                      is_dir=is_dir,
                                                      file_id=file_id,
                                                      path_pair_id=path_pair_id)
            self.completed_queue.put(completed_result)

        def extract_failed(self,
                           name: str,
                           is_dir: bool,
                           file_id: Optional[str] = None,
                           path_pair_id: Optional[str] = None):
            self.logger.error("Extraction failed for {}".format(name))
            trace_owner: Any = self.trace_owner
            flow_id = trace_owner._ExtractProcess__pop_inflight_flow_id(file_id=file_id, file_name=name)
            trace_owner._ExtractProcess__record_breadcrumb(
                "extract_failed",
                {
                    "file_name": name,
                    "is_dir": is_dir,
                    "file_id": file_id,
                    "path_pair_id": path_pair_id,
                },
                event_type="failure",
                corr_id=trace_owner._ExtractProcess__trace_corr_id(file_id, path_pair_id, name),
                flow_id=flow_id,
                file_id=file_id,
                path_pair_id=path_pair_id,
            )
            trace_owner._ExtractProcess__trace_target_archive_event("extract_failed", {
                "file_name": name,
                "is_dir": is_dir,
                "file_id": file_id,
                "path_pair_id": path_pair_id,
            })
            failed_result = ExtractFailedResult(timestamp=datetime.now(),
                                                name=name,
                                                is_dir=is_dir,
                                                file_id=file_id,
                                                path_pair_id=path_pair_id)
            if self.failed_queue is not None:
                self.failed_queue.put(failed_result)

    def __init__(self,
                 out_dir_path: str,
                 local_path: str,
                 local_path_fallback: Optional[str] = None,
                 managed_extract_folders_enabled: bool = True,
                 breadcrumb_trace=None):
        super().__init__(name=self.__class__.__name__)
        self.__out_dir_path = out_dir_path
        self.__local_path = local_path
        self.__local_path_fallback = local_path_fallback
        self.__managed_extract_folders_enabled = managed_extract_folders_enabled
        self.__command_queue = multiprocessing.Queue()
        self.__status_result_queue = multiprocessing.Queue()
        self.__completed_result_queue = multiprocessing.Queue()
        self.__failed_result_queue = multiprocessing.Queue()
        self.__dispatch: Optional[ExtractDispatch] = None
        self.__breadcrumb_trace = breadcrumb_trace
        self.__target_archive_trace_file_id = os.environ.get("SEEDSYNC_TARGET_ARCHIVE_TRACE_FILE_ID")
        if self.__target_archive_trace_file_id is not None and not self.__target_archive_trace_file_id.strip():
            self.__target_archive_trace_file_id = None
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        self.__target_archive_trace_last_signature = None
        self.__inflight_flow_ids_by_file_id = defaultdict(deque)
        self.__inflight_flow_ids_by_name = defaultdict(deque)
        # Child-only synchronization must not cross the spawn pickle boundary.
        self.__inflight_flow_ids_lock = None

    @staticmethod
    def __extract_trace_selector_name(identifier: Optional[str]) -> Optional[str]:
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
        self.__inflight_flow_ids_lock = threading.Lock()
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        # Create dispatch inside the process
        self.__dispatch = ExtractDispatch(out_dir_path=self.__out_dir_path,
                                          local_path=self.__local_path,
                                          local_path_fallback=cast(Any, self.__local_path_fallback),
                                          managed_extract_folders_enabled=self.__managed_extract_folders_enabled)

        # Add extract listener
        listener = ExtractProcess.__ExtractListener(
            logger=self.logger,
            completed_queue=cast(Any, self.__completed_result_queue),
            trace_owner=self,
            failed_queue=cast(Any, self.__failed_result_queue)
        )
        self.__dispatch.add_listener(listener)

        # Start dispatch
        self.__dispatch.start()

    @overrides(AppProcess)
    def run_cleanup(self):
        assert self.__dispatch is not None
        self.__dispatch.stop()

    @overrides(AppProcess)
    def run_loop(self):
        assert self.__dispatch is not None
        assert self.__command_queue is not None
        assert self.__status_result_queue is not None
        assert self.__completed_result_queue is not None
        assert self.__failed_result_queue is not None
        # Forward all the extract commands
        try:
            first_queue_read = True
            while True:
                if first_queue_read:
                    # multiprocessing.Queue can briefly report empty right after a producer put.
                    # A short first read timeout avoids dropping immediate follow-up dispatch work.
                    queue_item = self.__command_queue.get(block=True, timeout=0.01)
                    first_queue_read = False
                else:
                    queue_item = self.__command_queue.get(block=False)
                if isinstance(queue_item, tuple) and len(queue_item) == 2:
                    file, flow_id = queue_item
                else:
                    file = queue_item
                    flow_id = None
                model_file = getattr(file, "model_file", file)
                assert isinstance(model_file, ModelFile)
                self.__record_breadcrumb(
                    "extract_command_dequeued",
                    {
                        "file_name": model_file.name,
                        "is_dir": model_file.is_dir,
                    },
                    corr_id=self.__trace_corr_id(model_file.file_id, model_file.path_pair_id, model_file.name),
                    flow_id=flow_id,
                    file_id=model_file.file_id,
                    path_pair_id=model_file.path_pair_id,
                )
                try:
                    self.__track_inflight_flow_id(model_file, flow_id)
                    self.__dispatch.extract(file)
                    self.__record_breadcrumb(
                        "extract_command_dispatched",
                        {
                            "file_name": model_file.name,
                            "is_dir": model_file.is_dir,
                        },
                        corr_id=self.__trace_corr_id(model_file.file_id, model_file.path_pair_id, model_file.name),
                        flow_id=flow_id,
                        file_id=model_file.file_id,
                        path_pair_id=model_file.path_pair_id,
                    )
                except ExtractDispatchError as e:
                    self.__untrack_inflight_flow_id(model_file, flow_id)
                    self.logger.warning(str(e))
                    self.__record_breadcrumb(
                        "extract_dispatch_blocked",
                        {
                            "file_name": model_file.name,
                            "is_dir": model_file.is_dir,
                            "reason": str(e),
                        },
                        event_type="failure",
                        corr_id=self.__trace_corr_id(model_file.file_id, model_file.path_pair_id, model_file.name),
                        flow_id=flow_id,
                        file_id=model_file.file_id,
                        path_pair_id=model_file.path_pair_id,
                    )
                    if self.__target_archive_trace_selector_matches_name(model_file.name):
                        self.__trace_target_archive_event("extract_dispatch_blocked", {
                            "file_name": model_file.name,
                            "is_dir": model_file.is_dir,
                            "reason": str(e),
                        })
                    failed_result = ExtractFailedResult(
                        timestamp=datetime.now(),
                        name=model_file.name,
                        is_dir=model_file.is_dir,
                        file_id=model_file.file_id,
                        path_pair_id=model_file.path_pair_id,
                    )
                    self.__failed_result_queue.put(failed_result)
                except Exception:
                    self.__untrack_inflight_flow_id(model_file, flow_id)
                    raise
        except queue.Empty:
            pass

        # Queue the latest status
        statuses = self.__dispatch.status()
        status_result = ExtractStatusResult(timestamp=datetime.now(),
                                            statuses=statuses)
        self.__status_result_queue.put(status_result)

        time.sleep(ExtractProcess.__DEFAULT_SLEEP_INTERVAL_IN_SECS)

    @overrides(AppProcess)
    def close_queues(self):
        self.__command_queue = self._close_multiprocessing_queue(self.__command_queue)
        self.__status_result_queue = self._close_multiprocessing_queue(self.__status_result_queue)
        self.__completed_result_queue = self._close_multiprocessing_queue(self.__completed_result_queue)
        self.__failed_result_queue = self._close_multiprocessing_queue(self.__failed_result_queue)
        super().close_queues()

    def __trace_corr_id(self,
                        file_id: Optional[str] = None,
                        path_pair_id: Optional[str] = None,
                        file_name: Optional[str] = None):
        return path_pair_id or file_id or file_name

    def __record_breadcrumb(self,
                            message: str,
                            details: dict,
                            event_type: str = "state_transition",
                            corr_id: Optional[str] = None,
                            flow_id: Optional[str] = None,
                            file_id: Optional[str] = None,
                            path_pair_id: Optional[str] = None):
        if self.__breadcrumb_trace is None:
            return
        self.__breadcrumb_trace.record(
            "extract_process",
            message,
            details,
            stage="extract",
            event_type=event_type,
            corr_id=corr_id if corr_id is not None else self.__trace_corr_id(file_id, path_pair_id),
            flow_id=flow_id,
            file_id=file_id,
            path_pair_id=path_pair_id,
        )

    def __track_inflight_flow_id(self, file: ModelFile, flow_id: Optional[str] = None):
        if flow_id is None:
            return
        with self.__inflight_flow_ids_lock_or_create():
            if file.file_id is not None:
                self.__inflight_flow_ids_by_file_id[file.file_id].append(flow_id)
            self.__inflight_flow_ids_by_name[file.name].append(flow_id)

    def __inflight_flow_ids_lock_or_create(self):
        # Direct run_loop unit tests do not call run_init; keep those local
        # execution paths synchronized without serializing a parent thread lock.
        if self.__inflight_flow_ids_lock is None:
            self.__inflight_flow_ids_lock = threading.Lock()
        return self.__inflight_flow_ids_lock

    def __untrack_inflight_flow_id(self, file: ModelFile, flow_id: Optional[str] = None):
        if flow_id is None:
            return
        with self.__inflight_flow_ids_lock_or_create():
            self.__remove_flow_id(
                self.__inflight_flow_ids_by_file_id,
                file.file_id,
                flow_id
            )
            self.__remove_flow_id(
                self.__inflight_flow_ids_by_name,
                file.name,
                flow_id
            )

    @staticmethod
    def __remove_flow_id(flow_ids_by_key, key, flow_id: str):
        if key is None:
            return
        flow_ids = flow_ids_by_key.get(key)
        if not flow_ids:
            return
        try:
            flow_ids.remove(flow_id)
        except ValueError:
            return
        if not flow_ids:
            flow_ids_by_key.pop(key, None)

    def __pop_inflight_flow_id(self,
                               file_id: Optional[str] = None,
                               file_name: Optional[str] = None):
        with self.__inflight_flow_ids_lock_or_create():
            if file_id is not None:
                by_id = self.__inflight_flow_ids_by_file_id.get(file_id)
                if by_id:
                    flow_id = by_id.popleft()
                    if not by_id:
                        self.__inflight_flow_ids_by_file_id.pop(file_id, None)
                    if file_name is not None:
                        self.__remove_flow_id(self.__inflight_flow_ids_by_name, file_name, flow_id)
                    return flow_id
            if file_name is not None:
                by_name = self.__inflight_flow_ids_by_name.get(file_name)
                if by_name:
                    flow_id = by_name.popleft()
                    if not by_name:
                        self.__inflight_flow_ids_by_name.pop(file_name, None)
                    return flow_id
        return None

    def extract(self, file: ExtractRequest | ModelFile, flow_id: Optional[str] = None):
        """
        Process-safe method to queue an extraction
        :param file:
        :return:
        """
        model_file = getattr(file, "model_file", file)
        assert isinstance(model_file, ModelFile)
        self.__record_breadcrumb(
            "extract_command_queued",
            {
                "file_name": model_file.name,
                "is_dir": model_file.is_dir,
            },
            corr_id=self.__trace_corr_id(model_file.file_id, model_file.path_pair_id, model_file.name),
            flow_id=flow_id,
            file_id=model_file.file_id,
            path_pair_id=model_file.path_pair_id,
        )
        assert self.__command_queue is not None
        self.__command_queue.put((file, flow_id))

    def pop_latest_statuses(self) -> Optional[ExtractStatusResult]:
        """
        Process-safe method to retrieve latest extract status
        Returns none if no new status is available since the last time
        this method was called
        :return:
        """
        latest_result = None
        try:
            assert self.__status_result_queue is not None
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
            assert self.__completed_result_queue is not None
            while True:
                result = self.__completed_result_queue.get(block=False)
                completed.append(result)
        except queue.Empty:
            pass
        return completed

    def pop_failed(self) -> List[ExtractFailedResult]:
        """
        Process-safe method to retrieve list of newly failed extractions
        Returns an empty list if no new extractions failed since the last time
        this method was called.
        :return:
        """
        failed = []
        try:
            assert self.__failed_result_queue is not None
            while True:
                result = self.__failed_result_queue.get(block=False)
                failed.append(result)
        except queue.Empty:
            pass
        return failed
