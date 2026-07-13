# Copyright 2017, Inderpreet Singh, All rights reserved.

import copy
import logging
import time
from threading import Lock
from typing import TYPE_CHECKING, Protocol, TypeGuard

from ..web_app import IStreamHandler
from ..utils import StreamQueue
from ..serialize import SerializeLogRecord
from common import overrides

if TYPE_CHECKING:
    from ..web_app import WebApp


class _HandlerLogger(Protocol):
    def addHandler(self, handler: logging.Handler) -> None: ...
    def removeHandler(self, handler: logging.Handler) -> None: ...


def _is_handler_logger(value: object) -> TypeGuard[_HandlerLogger]:
    return callable(getattr(value, "addHandler", None)) and callable(
        getattr(value, "removeHandler", None)
    )


class CachedQueueLogHandler(logging.Handler):
    """
    A logging.Handler that caches the past X seconds of
    logs
    """
    def __init__(self, history_size_in_ms: int):
        """
        Constructs a CachedQueueLogHandler
        :param history_size_in_ms: history size, set to 0 to disable caching
        """
        super().__init__()
        self.__history_size_in_ms = history_size_in_ms
        self.__cached_records: list[logging.LogRecord] = []
        self.__cache_lock = Lock()

    def get_cached_records(self) -> list[logging.LogRecord]:
        with self.__cache_lock:
            self.__prune_history()
            cache = copy.copy(self.__cached_records)
        return cache

    @overrides(logging.Handler)
    def emit(self, record: logging.LogRecord) -> None:
        if self.__history_size_in_ms > 0:
            with self.__cache_lock:
                self.__cached_records.append(record)
                self.__prune_history()

    def __prune_history(self) -> None:
        current_time_in_ms = int(time.time()*1000)
        history_start_time_in_ms = current_time_in_ms - self.__history_size_in_ms
        # Find the largest index older than history start time
        prune_index = -1
        for i, record in enumerate(self.__cached_records):
            if 1000.0*record.created < history_start_time_in_ms:
                prune_index = i
            else:
                # assume records are order oldest to newest
                break
        if prune_index >= 0:
            self.__cached_records = self.__cached_records[prune_index+1:]


class QueueLogHandler(logging.Handler, StreamQueue[logging.LogRecord]):
    """
    A log handler that stored records in a thread-safe queue
    """
    def __init__(self) -> None:
        logging.Handler.__init__(self)
        super(logging.Filterer, self).__init__()

    @overrides(logging.Handler)
    def emit(self, record: logging.LogRecord) -> None:
        self.put(record)


class LogStreamHandler(IStreamHandler):
    """
    Streams logs captured after the stream starts.
    Also cache a small history of logs and sends them when the stream
    starts.
    """
    _CACHE_HISTORY_SIZE_IN_MS = 3000

    # Cache of logs
    _cache: CachedQueueLogHandler | None = None

    def __init__(self, logger: _HandlerLogger) -> None:
        self.logger = logger
        self.handler = QueueLogHandler()
        self.serialize = SerializeLogRecord()

    # noinspection PyUnresolvedReferences
    @classmethod
    @overrides(IStreamHandler)
    def register(cls, web_app: "WebApp", **kwargs: object) -> None:
        logger = kwargs.get("logger")
        if not _is_handler_logger(logger):
            raise TypeError("Log stream registration requires a logger")
        # Initialize our cache when we register
        LogStreamHandler._cache = CachedQueueLogHandler(
            history_size_in_ms=LogStreamHandler._CACHE_HISTORY_SIZE_IN_MS
        )
        logger.addHandler(LogStreamHandler._cache)

        web_app.add_streaming_handler(cls, required_scope="admin", logger=logger)

    @overrides(IStreamHandler)
    def setup(self) -> None:
        # Send out all the cached records first
        assert LogStreamHandler._cache is not None
        for record in LogStreamHandler._cache.get_cached_records():
            self.handler.emit(record)
        # Then subscribe the live stream
        self.logger.addHandler(self.handler)

    @overrides(IStreamHandler)
    def get_value(self) -> str | None:
        record = self.handler.get_next_event()
        if record is not None:
            return self.serialize.record(record)
        else:
            return None

    @overrides(IStreamHandler)
    def cleanup(self) -> None:
        self.logger.removeHandler(self.handler)
