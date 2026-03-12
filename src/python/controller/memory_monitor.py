# Copyright 2017, Inderpreet Singh, All rights reserved.

import time
from typing import Callable


class ControllerMemoryMonitor:
    DEFAULT_LOG_INTERVAL_IN_SECS = 300

    def __init__(self, logger, log_interval_in_secs: int = DEFAULT_LOG_INTERVAL_IN_SECS,
                 time_fn: Callable[[], float] = None):
        self.__logger = logger
        self.__log_interval_in_secs = log_interval_in_secs
        self.__time_fn = time_fn or time.monotonic
        self.__next_log_time = None

    def log_if_due(self,
                   model_file_count: int,
                   downloaded_file_count: int,
                   extracted_file_count: int,
                   stopped_file_count: int,
                   active_download_count: int,
                   active_extract_count: int,
                   active_command_count: int) -> bool:
        now = self.__time_fn()
        if self.__next_log_time is None:
            self.__next_log_time = now + self.__log_interval_in_secs
            return False
        if now < self.__next_log_time:
            return False

        self.__logger.info(
            "Memory monitor: model_files=%s downloaded_files=%s extracted_files=%s "
            "stopped_files=%s active_downloads=%s active_extracts=%s active_commands=%s",
            model_file_count,
            downloaded_file_count,
            extracted_file_count,
            stopped_file_count,
            active_download_count,
            active_extract_count,
            active_command_count
        )
        self.__next_log_time = now + self.__log_interval_in_secs
        return True
