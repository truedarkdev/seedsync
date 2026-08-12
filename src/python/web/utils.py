# Copyright 2017, Inderpreet Singh, All rights reserved.

from queue import Queue, Empty, Full
from threading import Event, Lock
from typing import TypeVar, Generic, Optional


T = TypeVar('T')


class StreamQueue(Generic[T]):
    """
    A queue that transfers events from one thread to another.
    Useful for web streams that wait for listener events from other threads.
    The producer thread calls put() to insert events. The consumer stream
    calls get_next_event() to receive event in its own thread.
    """
    DEFAULT_MAXSIZE = 1000

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE):
        self.__maxsize = maxsize
        self.__queue: Queue[T] = Queue(maxsize=maxsize)
        self.__dropped_count = 0
        self.__put_lock = Lock()
        self.__wake_event: Optional[Event] = None

    def put(self, event: T) -> None:
        with self.__put_lock:
            if self.__maxsize == 0:
                self.__queue.put(event)
            else:
                try:
                    self.__queue.put(event, block=False)
                except Full:
                    self.__queue.get(block=False)
                    self.__dropped_count += 1
                    self.__queue.put(event, block=False)
            if self.__wake_event is not None:
                self.__wake_event.set()

    def set_wake_event(self, wake_event: Event) -> None:
        """Wake a shared stream loop whenever this queue receives data."""
        with self.__put_lock:
            self.__wake_event = wake_event
            if not self.__queue.empty():
                wake_event.set()

    def get_next_event(self) -> Optional[T]:
        """
        Returns the next event if there is one, otherwise returns None
        :return:
        """
        try:
            return self.__queue.get(block=False)
        except Empty:
            return None

    def get_queue_size(self) -> int:
        return self.__queue.qsize()

    def get_maxsize(self) -> int:
        return self.__maxsize

    def get_dropped_count(self) -> int:
        return self.__dropped_count
