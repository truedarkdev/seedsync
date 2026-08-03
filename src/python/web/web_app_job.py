# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import socket
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler
from queue import Empty, Full, Queue
from threading import BoundedSemaphore, Event, Lock, Thread
from types import TracebackType
from typing import Protocol, TypeAlias, runtime_checkable
from urllib.parse import unquote, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from wsgiref.types import StartResponse, WSGIApplication, WSGIEnvironment

import bottle

from .web_app import WebApp
from common import overrides, Job, Context


ClientAddress: TypeAlias = tuple[str, int] | tuple[str, int, int, int]
QueuedRequest: TypeAlias = tuple[object, ClientAddress]
WSGIHeaders: TypeAlias = list[tuple[str, str]]
WSGIExcInfo: TypeAlias = (
    tuple[type[BaseException], BaseException, TracebackType]
    | tuple[None, None, None]
    | None
)
WSGIWrite: TypeAlias = Callable[[bytes], object]


@runtime_checkable
class _PeekableRequest(Protocol):
    def gettimeout(self) -> float | None: ...
    def settimeout(self, value: float | None) -> None: ...
    def recv(self, bufsize: int, flags: int = 0) -> bytes: ...


def _call_request_lifecycle(callback: object, *args: object) -> object:
    if not callable(callback):
        raise RuntimeError("WSGI server request lifecycle is unavailable")
    return callback(*args)


class WebAppJob(Job):
    """
    Web interface service
    :return:
    """
    def __init__(self, context: Context, web_app: WebApp) -> None:
        super().__init__(name=self.__class__.__name__, context=context)
        self.web_access_logger = context.web_access_logger
        self.__context = context
        self.__app = web_app
        self.__server: MyWSGIRefServer | None = None
        self.__server_thread: Thread | None = None

    @overrides(Job)
    def setup(self) -> None:
        # Note: do not use requestlogger.WSGILogger as it breaks SSE
        port_value: object = self.__context.config.web.port
        if type(port_value) is not int:
            raise ValueError("Web port must be an integer")
        self.__server = MyWSGIRefServer(
            self.web_access_logger,
            host=getattr(self.__context.args, "web_bind_host", "0.0.0.0"),
            port=port_value
        )
        self.__server_thread = Thread(
            target=bottle.run,
            kwargs={
                "app": self.__app,
                "server": self.__server,
                "debug": self.__context.args.debug,
            }
        )
        self.__server_thread.start()

    @overrides(Job)
    def execute(self) -> None:
        self.__app.process()

    @overrides(Job)
    def cleanup(self) -> None:
        self.__app.stop()
        assert self.__server is not None
        self.__server.stop()
        assert self.__server_thread is not None
        self.__server_thread.join()


class _BoundedWSGIServer(WSGIServer):
    normal_worker_count = 8
    # SSE responses are long-lived. Twelve fixed slots cover a household or
    # small LAN's normal browser usage without allowing unbounded threads.
    stream_capacity = 12
    stream_worker_count = stream_capacity
    worker_count = normal_worker_count + stream_worker_count
    normal_queue_size = 5
    # This queue only absorbs worker scheduling races. Admission below bounds
    # its queued and active requests together, so it cannot become a waiting
    # room for indefinitely long SSE connections.
    stream_queue_size = stream_capacity
    request_queue_size = 5
    stream_request_path = "/server/stream"
    request_line_peek_size = 4096
    request_line_peek_timeout_seconds = 0.05
    stream_retry_after_seconds = 1
    stream_over_capacity_response = (
        b"HTTP/1.1 503 Service Unavailable\r\n"
        b"Content-Length: 0\r\n"
        b"Connection: close\r\n"
        + b"Retry-After: "
        + str(stream_retry_after_seconds).encode("ascii")
        + b"\r\n\r\n"
    )
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        bind_and_activate: bool = True,
    ) -> None:
        self._worker_shutdown = Event()
        self._request_admission_lock = Lock()
        self._normal_request_queue: Queue[QueuedRequest] = Queue(maxsize=self.normal_queue_size)
        self._stream_request_queue: Queue[QueuedRequest] = Queue(maxsize=self.stream_queue_size)
        self._stream_admission = BoundedSemaphore(self.stream_capacity)
        self._workers: list[Thread] = []
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)
        self._start_workers("SeedSyncWebWorker", self._normal_request_queue, self.normal_worker_count)
        self._start_workers(
            "SeedSyncStreamWorker",
            self._stream_request_queue,
            self.stream_worker_count,
            releases_stream_slot=True,
        )

    def _start_workers(
        self,
        name_prefix: str,
        request_queue: Queue[QueuedRequest],
        worker_count: int,
        releases_stream_slot: bool = False,
    ) -> None:
        for index in range(worker_count):
            worker = Thread(
                target=self._worker_loop,
                args=(request_queue, releases_stream_slot),
                name="{}-{}".format(name_prefix, index + 1),
                daemon=True
            )
            worker.start()
            self._workers.append(worker)

    def _worker_loop(
        self,
        request_queue: Queue[QueuedRequest],
        releases_stream_slot: bool = False,
    ) -> None:
        while not self._worker_shutdown.is_set() or not request_queue.empty():
            try:
                request, client_address = request_queue.get(timeout=0.1)
            except Empty:
                continue

            try:
                self._process_request_from_worker(request, client_address)
            finally:
                request_queue.task_done()
                if releases_stream_slot:
                    self._stream_admission.release()

    def _process_request_from_worker(
        self, request: object, client_address: ClientAddress
    ) -> None:
        try:
            _call_request_lifecycle(self.finish_request, request, client_address)
        except Exception:
            _call_request_lifecycle(self.handle_error, request, client_address)
            _call_request_lifecycle(self.shutdown_request, request)
        else:
            _call_request_lifecycle(self.shutdown_request, request)

    def process_request(
        self,
        request: object,
        client_address: ClientAddress,
    ) -> None:
        # Keep admission atomic with shutdown. A request admitted immediately
        # before shutdown is drained by workers; one arriving afterward closes
        # without consuming a stream slot.
        with self._request_admission_lock:
            if self._worker_shutdown.is_set():
                _call_request_lifecycle(self.shutdown_request, request)
                return

            is_stream_request = self._is_stream_request(request)
            if is_stream_request and not self._stream_admission.acquire(blocking=False):
                self._reject_stream_request(request)
                return

            request_queue = (
                self._stream_request_queue if is_stream_request else self._normal_request_queue
            )
            try:
                request_queue.put_nowait((request, client_address))
            except Full:
                if is_stream_request:
                    self._stream_admission.release()
                    self._reject_stream_request(request)
                else:
                    _call_request_lifecycle(self.shutdown_request, request)

    def _reject_stream_request(self, request: object) -> None:
        sendall = getattr(request, "sendall", None)
        try:
            # The socket closes immediately afterward, so bound this write
            # instead of restoring the normal request timeout.
            if isinstance(request, _PeekableRequest):
                try:
                    request.settimeout(0)
                except (OSError, TimeoutError):
                    sendall = None
            if callable(sendall):
                try:
                    sendall(self.stream_over_capacity_response)
                except (BlockingIOError, OSError, TimeoutError):
                    pass
        finally:
            _call_request_lifecycle(self.shutdown_request, request)

    def _is_stream_request(self, request: object) -> bool:
        request_path = self._peek_request_path(request)
        # Only the positively identified SSE endpoint should use the tiny
        # stream pool; ambiguous peeks stay on the normal queue so transient
        # socket timing cannot starve ordinary pages.
        return request_path == self.stream_request_path

    def _peek_request_path(self, request: object) -> str | None:
        if not isinstance(request, _PeekableRequest):
            return None
        try:
            previous_timeout = request.gettimeout()
            # A browser can send the request line just after accept(). Give
            # that first read a small, fixed window so a valid SSE request is
            # classified before it can occupy a normal worker. Anything still
            # incomplete or unreadable remains intentionally ambiguous.
            request.settimeout(self.request_line_peek_timeout_seconds)
            try:
                data = request.recv(self.request_line_peek_size, socket.MSG_PEEK)
            finally:
                request.settimeout(previous_timeout)
        except (AttributeError, BlockingIOError, OSError, TimeoutError):
            return None

        if b"\r\n" not in data:
            return None

        try:
            request_line = data.split(b"\r\n", 1)[0].decode("iso-8859-1")
        except UnicodeDecodeError:
            return None

        parts = request_line.split()
        if len(parts) < 2:
            return None

        return unquote(urlsplit(parts[1]).path, "iso-8859-1")

    def stop_accepting(self) -> None:
        with self._request_admission_lock:
            self._worker_shutdown.set()

    def server_close(self) -> None:
        self.stop_accepting()
        super().server_close()
        for worker in self._workers:
            worker.join(timeout=1)


class _RequestHandler(WSGIRequestHandler):
    socket_timeout = 300

    def setup(self) -> None:
        super().setup()
        # Bound blocking reads/writes for half-open clients so a worker cannot
        # remain occupied forever when the peer stops responding.
        self.connection.settimeout(self.socket_timeout)

    def address_string(self) -> str:
        return self.client_address[0]

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        del code, size
        pass

    def get_environ(self) -> dict[str, object]:
        environ = super().get_environ()
        environ["seedsync.raw_path"] = urlsplit(self.path).path
        return environ


class _RequestLoggingMiddleware:
    """WSGI middleware that preserves access logging without Paste."""

    def __init__(
        self,
        app: WSGIApplication,
        logger: logging.Logger,
        level: int = logging.DEBUG,
    ) -> None:
        self.app = app
        self.logger = logger
        self.level = level

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterator[bytes]:
        method_value = environ.get("REQUEST_METHOD", "")
        method = method_value if isinstance(method_value, str) else ""
        raw_path = environ.get("seedsync.raw_path") or environ.get("PATH_INFO", "")
        request_path = raw_path if isinstance(raw_path, str) else ""
        query_value = environ.get("QUERY_STRING", "")
        query_string = query_value if isinstance(query_value, str) else ""
        if query_string:
            request_path = "{}?{}".format(request_path, query_string)

        start = time.monotonic()
        status_code = "-"

        def _start_response(
            status: str,
            headers: WSGIHeaders,
            exc_info: WSGIExcInfo = None,
            /,
        ) -> WSGIWrite:
            nonlocal status_code
            status_code = status.split(" ", 1)[0]
            return start_response(status, headers, exc_info)

        try:
            response = self.app(environ, _start_response)
        except Exception:
            status_code = "500"
            self._log_request(method, request_path, status_code, start)
            raise

        def _response_iterator() -> Iterator[bytes]:
            try:
                for chunk in response:
                    yield chunk
            finally:
                close_method: object = getattr(response, "close", None)
                if callable(close_method):
                    close_method()
                self._log_request(method, request_path, status_code, start)

        return _response_iterator()

    def _log_request(
        self, method: str, request_path: str, status_code: str, start: float
    ) -> None:
        duration_ms = (time.monotonic() - start) * 1000
        self.logger.log(
            self.level,
            "%s %s %s %.1fms",
            method,
            request_path,
            status_code,
            duration_ms
        )


class MyWSGIRefServer(bottle.ServerAdapter):
    """
    Bottle-compatible WSGI server adapter with bounded workers and stop support.
    """
    _SERVER_POLL_INTERVAL_IN_SECS = 0.1

    quiet = True  # disable logging to stdout

    def __init__(
        self,
        logger: logging.Logger,
        host: str = "127.0.0.1",
        port: int = 8080,
        **options: object,
    ) -> None:
        super().__init__(host=host, port=port, **options)
        self.logger = logger
        self.server: _BoundedWSGIServer | None = None
        self._stop_requested = Event()

    @overrides(bottle.ServerAdapter)
    def run(self, handler: WSGIApplication) -> None:
        self.logger.debug("Starting web server")
        handler = _RequestLoggingMiddleware(handler, logger=self.logger, level=logging.DEBUG)
        server_class = _BoundedWSGIServer
        if ":" in self.host and getattr(server_class, "address_family", socket.AF_INET) == socket.AF_INET:
            class _IPv6BoundedWSGIServer(server_class):
                address_family = socket.AF_INET6
            server_class = _IPv6BoundedWSGIServer

        created_server = make_server(
            self.host,
            self.port,
            handler,
            server_class=server_class,
            handler_class=_RequestHandler
        )
        self.server = created_server
        self.port = created_server.server_port
        created_server.timeout = self._SERVER_POLL_INTERVAL_IN_SECS

        try:
            while not self._stop_requested.is_set():
                created_server.handle_request()
        finally:
            created_server.server_close()
            self.server = None

    def stop(self) -> None:
        self._stop_requested.set()
        server = self.server
        if server is not None:
            stop_accepting = getattr(server, "stop_accepting", None)
            if stop_accepting is not None:
                stop_accepting()
