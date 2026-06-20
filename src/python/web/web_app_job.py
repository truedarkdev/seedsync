# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import socket
import time
from queue import Empty, Full, Queue
from threading import Event, Thread
from urllib.parse import unquote, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import bottle

from .web_app import WebApp
from common import overrides, Job, Context


class WebAppJob(Job):
    """
    Web interface service
    :return:
    """
    def __init__(self, context: Context, web_app: WebApp):
        super().__init__(name=self.__class__.__name__, context=context)
        self.web_access_logger = context.web_access_logger
        self.__context = context
        self.__app = web_app
        self.__server: MyWSGIRefServer | None = None
        self.__server_thread: Thread | None = None

    @overrides(Job)
    def setup(self):
        # Note: do not use requestlogger.WSGILogger as it breaks SSE
        self.__server = MyWSGIRefServer(
            self.web_access_logger,
            host=getattr(self.__context.args, "web_bind_host", "0.0.0.0"),
            port=self.__context.config.web.port
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
    def execute(self):
        self.__app.process()

    @overrides(Job)
    def cleanup(self):
        self.__app.stop()
        assert self.__server is not None
        self.__server.stop()
        assert self.__server_thread is not None
        self.__server_thread.join()


class _BoundedWSGIServer(WSGIServer):
    normal_worker_count = 8
    stream_worker_count = 2
    worker_count = normal_worker_count + stream_worker_count
    normal_queue_size = 5
    stream_queue_size = 5
    request_queue_size = 5
    stream_request_path = "/server/stream"
    request_line_peek_size = 4096
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self._worker_shutdown = Event()
        self._normal_request_queue = Queue(maxsize=self.normal_queue_size)
        self._stream_request_queue = Queue(maxsize=self.stream_queue_size)
        self._workers = []
        super().__init__(*args, **kwargs)
        self._start_workers("SeedSyncWebWorker", self._normal_request_queue, self.normal_worker_count)
        self._start_workers("SeedSyncStreamWorker", self._stream_request_queue, self.stream_worker_count)

    def _start_workers(self, name_prefix, request_queue, worker_count):
        for index in range(worker_count):
            worker = Thread(
                target=self._worker_loop,
                args=(request_queue,),
                name="{}-{}".format(name_prefix, index + 1),
                daemon=True
            )
            worker.start()
            self._workers.append(worker)

    def _worker_loop(self, request_queue):
        while not self._worker_shutdown.is_set() or not request_queue.empty():
            try:
                request, client_address = request_queue.get(timeout=0.1)
            except Empty:
                continue

            try:
                self._process_request_from_worker(request, client_address)
            finally:
                request_queue.task_done()

    def _process_request_from_worker(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
            self.shutdown_request(request)
        else:
            self.shutdown_request(request)

    def process_request(self, request, client_address):
        if self._worker_shutdown.is_set():
            self.shutdown_request(request)
            return

        request_queue = self._queue_for_request(request)
        try:
            request_queue.put_nowait((request, client_address))
        except Full:
            self.shutdown_request(request)

    def _queue_for_request(self, request):
        if self._is_stream_request(request):
            return self._stream_request_queue
        return self._normal_request_queue

    def _is_stream_request(self, request):
        request_path = self._peek_request_path(request)
        if request_path is None:
            return True
        return request_path == self.stream_request_path

    def _peek_request_path(self, request):
        try:
            previous_timeout = request.gettimeout()
            request.settimeout(0)
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

    def stop_accepting(self):
        self._worker_shutdown.set()

    def server_close(self):
        self._worker_shutdown.set()
        super().server_close()
        for worker in self._workers:
            worker.join(timeout=1)


class _RequestHandler(WSGIRequestHandler):
    def address_string(self):
        return self.client_address[0]

    def log_request(self, *args, **kwargs):
        pass

    def get_environ(self):
        environ = super().get_environ()
        environ["seedsync.raw_path"] = urlsplit(self.path).path
        return environ


class _RequestLoggingMiddleware:
    """WSGI middleware that preserves access logging without Paste."""

    def __init__(self, app, logger, level=logging.DEBUG):
        self.app = app
        self.logger = logger
        self.level = level

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "")
        request_path = environ.get("seedsync.raw_path") or environ.get("PATH_INFO", "")
        query_string = environ.get("QUERY_STRING", "")
        if query_string:
            request_path = "{}?{}".format(request_path, query_string)

        start = time.monotonic()
        status_code = "-"

        def _start_response(status, headers, exc_info=None):
            nonlocal status_code
            status_code = status.split(" ", 1)[0]
            return start_response(status, headers, exc_info)

        try:
            response = self.app(environ, _start_response)
        except Exception:
            status_code = "500"
            self._log_request(method, request_path, status_code, start)
            raise

        def _response_iterator():
            try:
                for chunk in response:
                    yield chunk
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
                self._log_request(method, request_path, status_code, start)

        return _response_iterator()

    def _log_request(self, method, request_path, status_code, start):
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

    def __init__(self, logger: logging.Logger, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logger
        self.server = None
        self._stop_requested = Event()

    @overrides(bottle.ServerAdapter)
    def run(self, handler):
        self.logger.debug("Starting web server")
        handler = _RequestLoggingMiddleware(handler, logger=self.logger, level=logging.DEBUG)
        server_class = _BoundedWSGIServer
        if ":" in self.host and getattr(server_class, "address_family", socket.AF_INET) == socket.AF_INET:
            class _IPv6BoundedWSGIServer(server_class):
                address_family = socket.AF_INET6
            server_class = _IPv6BoundedWSGIServer

        self.server = make_server(
            self.host,
            self.port,
            handler,
            server_class=server_class,
            handler_class=_RequestHandler
        )
        self.port = self.server.server_port
        self.server.timeout = self._SERVER_POLL_INTERVAL_IN_SECS

        try:
            while not self._stop_requested.is_set():
                self.server.handle_request()
        finally:
            self.server.server_close()
            self.server = None

    def stop(self):
        self._stop_requested.set()
        server = self.server
        if server is not None:
            stop_accepting = getattr(server, "stop_accepting", None)
            if stop_accepting is not None:
                stop_accepting()
