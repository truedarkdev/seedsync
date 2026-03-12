# Copyright 2017, Inderpreet Singh, All rights reserved.

from typing import Optional
import time

from common import overrides
from ..web_app import IStreamHandler


class HeartbeatStreamHandler(IStreamHandler):
    _HEARTBEAT_INTERVAL_IN_MS = 15000

    def __init__(self):
        self.__next_heartbeat_at = None

    @overrides(IStreamHandler)
    def setup(self):
        self.__next_heartbeat_at = time.monotonic() + (self._HEARTBEAT_INTERVAL_IN_MS / 1000)

    @overrides(IStreamHandler)
    def get_value(self) -> Optional[str]:
        if self.__next_heartbeat_at is None:
            return None

        current_time = time.monotonic()
        if current_time < self.__next_heartbeat_at:
            return None

        self.__next_heartbeat_at = current_time + (self._HEARTBEAT_INTERVAL_IN_MS / 1000)
        return ": heartbeat\n\n"

    @overrides(IStreamHandler)
    def cleanup(self):
        pass
