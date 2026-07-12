# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import multiprocessing
import queue
import sys
import threading
import time
from types import TracebackType


class MultiprocessingLogger:
    """
    A helper class to enable logging across processes
    It starts a listener thread on the main process. The listener thread
    receives records on a queue from other processes and sends them to the
    main logger (effectively serializing the logging).
    Other processes use a QueueHandler to send logging records to the
    listener thread on the main process.
    Source: https://gist.github.com/vsajip/820132
    """

    __LISTENER_SLEEP_INTERVAL_IN_SECS = 0.1
    __SHUTDOWN_DRAIN_TIMEOUT_IN_SECS = 1.0
    __SHUTDOWN_DRAIN_MAX_RECORDS = 10000

    def __init__(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("MPLogger")
        self.__queue = multiprocessing.Queue(-1)
        self.__queue_closed = False
        self.__logger_level = base_logger.getEffectiveLevel()
        self.__listener_thread: threading.Thread | None = threading.Thread(
            name="MPLoggerListener", target=self.__listener
        )
        self.__listener_shutdown: threading.Event | None = threading.Event()
        self.__listener_exc_info: tuple[
            type[BaseException] | None,
            BaseException | None,
            TracebackType | None,
        ] | None = None

    @property
    def queue(self) -> multiprocessing.Queue:
        return self.__queue

    @property
    def log_level(self) -> int:
        return self.__logger_level

    def start(self):
        assert self.__listener_thread is not None
        self.__listener_thread.start()

    def stop(self):
        if self.__listener_shutdown is not None:
            self.__listener_shutdown.set()
        if self.__listener_thread is not None and self.__listener_thread.ident is not None:
            self.__listener_thread.join()
        if not self.__queue_closed:
            self.__queue.close()
            self.__queue.join_thread()
            self.__queue_closed = True

    def propagate_exception(self):
        """
        Raises any exception captured by the listener thread
        Source: https://stackoverflow.com/a/1854263/8571324
        :return:
        """
        if self.__listener_exc_info:
            exc_info = self.__listener_exc_info
            self.__listener_exc_info = None
            if exc_info[1] is not None:
                raise exc_info[1].with_traceback(exc_info[2])

    @staticmethod
    def __remove_closed_stream_handlers(logger: logging.Logger):
        current_logger = logger

        while current_logger:
            for handler in current_logger.handlers[:]:
                stream = getattr(handler, "stream", None)
                if stream is not None and getattr(stream, "closed", False):
                    current_logger.removeHandler(handler)

            current_logger = current_logger.parent

    def __listener(self):
        assert self.__listener_shutdown is not None
        self.__remove_closed_stream_handlers(self.logger)
        self.logger.debug("Started listener thread")

        shutdown_started_at = None
        shutdown_record_count = 0
        while True:
            if self.__listener_shutdown.is_set() and shutdown_started_at is None:
                shutdown_started_at = time.monotonic()
            # noinspection PyBroadException
            try:
                while True:
                    try:
                        record = self.__queue.get(block=False)
                        if shutdown_started_at is not None:
                            shutdown_record_count += 1
                        record_logger = self.logger.getChild(record.name)
                        self.__remove_closed_stream_handlers(record_logger)
                        record_logger.handle(record)
                        if shutdown_started_at is not None and (
                            shutdown_record_count >= self.__SHUTDOWN_DRAIN_MAX_RECORDS
                            or time.monotonic() - shutdown_started_at >= self.__SHUTDOWN_DRAIN_TIMEOUT_IN_SECS
                        ):
                            break
                    except queue.Empty:
                        break
            except Exception:
                self.__listener_exc_info = sys.exc_info()
                self.logger.exception("Caught exception in listener thread")
                # break out of run loop
                self.__listener_shutdown.set()
                break

            if self.__listener_shutdown.is_set():
                if shutdown_started_at is None:
                    shutdown_started_at = time.monotonic()
                if (
                    shutdown_record_count >= self.__SHUTDOWN_DRAIN_MAX_RECORDS
                    or time.monotonic() - shutdown_started_at >= self.__SHUTDOWN_DRAIN_TIMEOUT_IN_SECS
                ):
                    break
                # Reaching here means the non-blocking drain observed the queue
                # empty, so all records currently available have been handled.
                break
            time.sleep(MultiprocessingLogger.__LISTENER_SLEEP_INTERVAL_IN_SECS)

        self.__remove_closed_stream_handlers(self.logger)
        self.logger.debug("Stopped listener thread")
