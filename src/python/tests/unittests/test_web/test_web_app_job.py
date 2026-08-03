import importlib
import logging
import socket
import time
import unittest
from email.message import Message
from queue import Queue
from socketserver import ThreadingMixIn
from threading import BoundedSemaphore, Event, Lock, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import web.web_app_job as web_app_job
from web.web_app_job import WebAppJob, MyWSGIRefServer, _BoundedWSGIServer, _RequestHandler, _RequestLoggingMiddleware


class _FakeSocket:
    def __init__(self, request_line=b"", recv_error=None, send_error=None):
        self._request_line = request_line
        self._recv_error = recv_error
        self._send_error = send_error
        self._timeout = 30
        self.timeouts = []
        self.recv_flags = None
        self.sent_responses = []

    def gettimeout(self):
        return self._timeout

    def settimeout(self, timeout):
        self._timeout = timeout
        self.timeouts.append(timeout)

    def recv(self, size, flags=0):
        self.recv_flags = flags
        if self._recv_error is not None:
            raise self._recv_error
        return self._request_line

    def sendall(self, data):
        if self._send_error is not None:
            raise self._send_error
        self.sent_responses.append(data)


class TestRequestHandler(unittest.TestCase):
    def test_setup_applies_socket_timeout_backstop(self):
        handler = object.__new__(_RequestHandler)
        handler.connection = _FakeSocket()

        with patch("web.web_app_job.WSGIRequestHandler.setup"):
            _RequestHandler.setup(handler)

        self.assertEqual(_RequestHandler.socket_timeout, handler.connection.gettimeout())

    def test_get_environ_preserves_raw_path(self):
        handler = object.__new__(_RequestHandler)
        handler.server = SimpleNamespace(base_environ={})
        handler.request_version = "HTTP/1.1"
        handler.server_version = "WSGIServer/0.2"
        handler.command = "GET"
        handler.path = "/assets/%2e%2e/logo.png?download=1"
        handler.client_address = ("127.0.0.1", 12345)
        headers = Message()
        headers["Content-Type"] = "text/plain"
        handler.headers = headers

        environ = _RequestHandler.get_environ(handler)

        self.assertEqual("/assets/../logo.png", environ["PATH_INFO"])
        self.assertEqual("/assets/%2e%2e/logo.png", environ["seedsync.raw_path"])


class TestRequestLoggingMiddleware(unittest.TestCase):
    def test_logs_request_path_status_and_duration(self):
        logger = MagicMock()

        def app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        middleware = _RequestLoggingMiddleware(app, logger, level=logging.INFO)
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/assets/logo.png",
            "QUERY_STRING": "download=1",
            "seedsync.raw_path": "/assets/%2e%2e/logo.png",
        }

        with patch("web.web_app_job.time.monotonic", side_effect=[1.0, 1.125]):
            response = list(middleware(environ, MagicMock()))

        self.assertEqual([b"ok"], response)
        logger.log.assert_called_once()
        self.assertEqual(
            (
                logging.INFO,
                "%s %s %s %.1fms",
                "GET",
                "/assets/%2e%2e/logo.png?download=1",
                "200",
                125.0,
            ),
            logger.log.call_args.args
        )

    def test_logs_app_exception_before_iterable_is_returned(self):
        logger = MagicMock()

        def app(environ, start_response):
            raise RuntimeError("boom")

        middleware = _RequestLoggingMiddleware(app, logger, level=logging.WARNING)
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/server/action",
            "QUERY_STRING": "",
        }

        with patch("web.web_app_job.time.monotonic", side_effect=[2.0, 2.25]):
            with self.assertRaises(RuntimeError):
                middleware(environ, MagicMock())

        logger.log.assert_called_once()
        self.assertEqual(
            (
                logging.WARNING,
                "%s %s %s %.1fms",
                "POST",
                "/server/action",
                "500",
                250.0,
            ),
            logger.log.call_args.args
        )


class TestBoundedWSGIServer(unittest.TestCase):
    def _routing_server(self, stream_capacity=None):
        server = object.__new__(_BoundedWSGIServer)
        server._worker_shutdown = Event()
        server._request_admission_lock = Lock()
        stream_capacity = stream_capacity or _BoundedWSGIServer.stream_capacity
        server._normal_request_queue = Queue(maxsize=1)
        server._stream_request_queue = Queue(maxsize=stream_capacity)
        server._stream_admission = BoundedSemaphore(stream_capacity)
        server.shutdown_request = MagicMock()
        return server

    def test_uses_fixed_worker_pool_and_bounded_queue(self):
        self.assertFalse(issubclass(_BoundedWSGIServer, ThreadingMixIn))
        self.assertEqual(8, _BoundedWSGIServer.normal_worker_count)
        self.assertEqual(12, _BoundedWSGIServer.stream_capacity)
        self.assertEqual(
            _BoundedWSGIServer.stream_capacity,
            _BoundedWSGIServer.stream_worker_count,
        )
        self.assertEqual(20, _BoundedWSGIServer.worker_count)
        self.assertEqual(5, _BoundedWSGIServer.normal_queue_size)
        self.assertEqual(
            _BoundedWSGIServer.stream_capacity,
            _BoundedWSGIServer.stream_queue_size,
        )
        self.assertEqual(5, _BoundedWSGIServer.request_queue_size)

    def test_starts_a_worker_for_each_stream_slot_and_shuts_down_cleanly(self):
        server = _BoundedWSGIServer(("127.0.0.1", 0), _RequestHandler)
        try:
            stream_workers = [
                worker for worker in server._workers
                if worker.name.startswith("SeedSyncStreamWorker-")
            ]
            self.assertEqual(_BoundedWSGIServer.stream_capacity, len(stream_workers))
            self.assertTrue(all(worker.is_alive() for worker in stream_workers))
        finally:
            server.server_close()

        self.assertTrue(server._worker_shutdown.is_set())
        self.assertTrue(all(not worker.is_alive() for worker in server._workers))

    def test_supported_stream_capacity_is_admitted_without_waiting_for_disconnect(self):
        server = self._routing_server()
        streams = [
            _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")
            for _ in range(_BoundedWSGIServer.stream_capacity)
        ]

        for index, stream in enumerate(streams):
            server.process_request(stream, ("127.0.0.1", 1000 + index))

        self.assertGreater(_BoundedWSGIServer.stream_capacity, 2)
        self.assertEqual(_BoundedWSGIServer.stream_capacity, server._stream_request_queue.qsize())
        self.assertTrue(all(not stream.sent_responses for stream in streams))
        server.shutdown_request.assert_not_called()

    def test_stream_saturation_returns_retryable_response_without_blocking_normal_requests(self):
        server = self._routing_server()
        streams = [
            _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")
            for _ in range(_BoundedWSGIServer.stream_capacity)
        ]
        for index, stream in enumerate(streams):
            server.process_request(stream, ("127.0.0.1", 1000 + index))

        extra_stream = _FakeSocket(b"GET /server/stream?since=1 HTTP/1.1\r\n\r\n")
        normal_request = _FakeSocket(b"GET /server/config HTTP/1.1\r\n\r\n")

        server.process_request(extra_stream, ("127.0.0.1", 1002))
        self.assertTrue(server._normal_request_queue.empty())
        server.process_request(normal_request, ("127.0.0.1", 1003))

        server.shutdown_request.assert_called_once_with(extra_stream)
        self.assertEqual(
            [_BoundedWSGIServer.stream_over_capacity_response],
            extra_stream.sent_responses,
        )
        self.assertEqual(
            0,
            extra_stream.gettimeout(),
        )
        self.assertEqual(_BoundedWSGIServer.stream_capacity, server._stream_request_queue.qsize())
        queued_request, queued_client = server._normal_request_queue.get_nowait()
        self.assertIs(normal_request, queued_request)
        self.assertEqual(("127.0.0.1", 1003), queued_client)
        self.assertEqual(socket.MSG_PEEK, extra_stream.recv_flags)
        self.assertEqual(socket.MSG_PEEK, normal_request.recv_flags)

    def test_delayed_complete_stream_request_line_uses_stream_admission(self):
        server = self._routing_server()
        client_socket, request_socket = socket.socketpair()

        def send_request_line():
            time.sleep(0.01)
            client_socket.sendall(b"GET /server/stream HTTP/1.1\r\nHost: local\r\n\r\n")

        sender = Thread(target=send_request_line)
        sender.start()
        try:
            server.process_request(request_socket, ("127.0.0.1", 1001))
            sender.join()
            self.assertTrue(server._normal_request_queue.empty())
            queued_request, queued_client = server._stream_request_queue.get_nowait()
            self.assertIs(request_socket, queued_request)
            self.assertEqual(("127.0.0.1", 1001), queued_client)
            self.assertIsNone(request_socket.gettimeout())
        finally:
            request_socket.close()
            client_socket.close()

    def test_empty_request_line_falls_back_to_normal_after_bounded_peek(self):
        server = self._routing_server()
        client_socket, request_socket = socket.socketpair()
        try:
            start = time.monotonic()
            server.process_request(request_socket, ("127.0.0.1", 1001))
            duration = time.monotonic() - start
            self.assertLess(duration, 0.25)
            queued_request, queued_client = server._normal_request_queue.get_nowait()
            self.assertIs(request_socket, queued_request)
            self.assertEqual(("127.0.0.1", 1001), queued_client)
            self.assertIsNone(request_socket.gettimeout())
        finally:
            request_socket.close()
            client_socket.close()

    def test_request_line_peek_uses_fixed_timeout_and_restores_the_socket(self):
        server = self._routing_server()
        unreadable_request = _FakeSocket(recv_error=TimeoutError())

        self.assertIsNone(server._peek_request_path(unreadable_request))

        self.assertEqual(
            [_BoundedWSGIServer.request_line_peek_timeout_seconds, 30],
            unreadable_request.timeouts,
        )

    def test_stream_admission_is_released_after_completion_or_error(self):
        for raises_error in (False, True):
            with self.subTest(raises_error=raises_error):
                server = object.__new__(_BoundedWSGIServer)
                server._worker_shutdown = Event()
                server._stream_admission = BoundedSemaphore(1)
                stream_queue = Queue(maxsize=1)
                stream_request = _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")
                stream_queue.put_nowait((stream_request, ("127.0.0.1", 1001)))
                self.assertTrue(server._stream_admission.acquire(blocking=False))

                def finish_request(request, client_address):
                    del request, client_address
                    server._worker_shutdown.set()
                    if raises_error:
                        raise RuntimeError("stream disconnected")

                server.finish_request = MagicMock(side_effect=finish_request)
                server.handle_error = MagicMock()
                server.shutdown_request = MagicMock()

                server._worker_loop(stream_queue, releases_stream_slot=True)

                self.assertTrue(server._stream_admission.acquire(blocking=False))
                server.shutdown_request.assert_called_once_with(stream_request)
                if raises_error:
                    server.handle_error.assert_called_once_with(stream_request, ("127.0.0.1", 1001))
                else:
                    server.handle_error.assert_not_called()

    def test_stream_admission_is_released_when_queue_is_unexpectedly_full(self):
        server = self._routing_server(stream_capacity=2)
        server._stream_request_queue = Queue(maxsize=1)
        server._stream_request_queue.put_nowait((object(), ("127.0.0.1", 1001)))
        self.assertTrue(server._stream_admission.acquire(blocking=False))
        extra_stream = _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")

        server.process_request(extra_stream, ("127.0.0.1", 1002))

        self.assertEqual(
            [_BoundedWSGIServer.stream_over_capacity_response],
            extra_stream.sent_responses,
        )
        self.assertTrue(server._stream_admission.acquire(blocking=False))
        self.assertFalse(server._stream_admission.acquire(blocking=False))

    def test_stream_overflow_send_failure_still_closes_request(self):
        server = self._routing_server(stream_capacity=1)
        admitted_stream = _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")
        server.process_request(admitted_stream, ("127.0.0.1", 1001))
        disconnected_stream = _FakeSocket(
            b"GET /server/stream HTTP/1.1\r\n\r\n",
            send_error=OSError("client disconnected"),
        )

        server.process_request(disconnected_stream, ("127.0.0.1", 1002))

        server.shutdown_request.assert_called_once_with(disconnected_stream)

    def test_nonreading_stream_overflow_does_not_delay_normal_admission(self):
        class _NonreadingSocket(_FakeSocket):
            def sendall(self, data):
                del data
                if self.gettimeout() != 0:
                    raise AssertionError("overflow response must be nonblocking")
                raise BlockingIOError("client is not reading")

        server = self._routing_server(stream_capacity=1)
        admitted_stream = _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")
        server.process_request(admitted_stream, ("127.0.0.1", 1001))
        nonreading_stream = _NonreadingSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")
        normal_request = _FakeSocket(b"GET /server/config HTTP/1.1\r\n\r\n")

        server.process_request(nonreading_stream, ("127.0.0.1", 1002))
        server.process_request(normal_request, ("127.0.0.1", 1003))

        queued_request, queued_client = server._normal_request_queue.get_nowait()
        self.assertIs(normal_request, queued_request)
        self.assertEqual(("127.0.0.1", 1003), queued_client)
        server.shutdown_request.assert_called_once_with(nonreading_stream)

    def test_unreadable_or_partial_request_line_uses_normal_queue(self):
        server = object.__new__(_BoundedWSGIServer)
        server._worker_shutdown = Event()
        server._request_admission_lock = Lock()
        server._normal_request_queue = Queue(maxsize=2)
        server._stream_request_queue = Queue(maxsize=2)
        server.shutdown_request = MagicMock()

        unreadable_request = _FakeSocket(recv_error=BlockingIOError())
        partial_request = _FakeSocket(b"GET /server/stream")

        server.process_request(unreadable_request, ("127.0.0.1", 1001))
        server.process_request(partial_request, ("127.0.0.1", 1002))

        queued_unreadable, unreadable_client = server._normal_request_queue.get_nowait()
        queued_partial, partial_client = server._normal_request_queue.get_nowait()
        self.assertIs(unreadable_request, queued_unreadable)
        self.assertEqual(("127.0.0.1", 1001), unreadable_client)
        self.assertIs(partial_request, queued_partial)
        self.assertEqual(("127.0.0.1", 1002), partial_client)
        self.assertTrue(server._stream_request_queue.empty())
        server.shutdown_request.assert_not_called()

    def test_encoded_stream_target_uses_stream_queue(self):
        server = object.__new__(_BoundedWSGIServer)
        server._worker_shutdown = Event()
        server._request_admission_lock = Lock()
        server._normal_request_queue = Queue(maxsize=1)
        server._stream_request_queue = Queue(maxsize=1)
        server._stream_admission = BoundedSemaphore(1)
        server.shutdown_request = MagicMock()

        encoded_stream_request = _FakeSocket(b"GET /server%2fstream?since=1 HTTP/1.1\r\n\r\n")

        server.process_request(encoded_stream_request, ("127.0.0.1", 1001))

        self.assertTrue(server._normal_request_queue.empty())
        queued_request, queued_client = server._stream_request_queue.get_nowait()
        self.assertIs(encoded_stream_request, queued_request)
        self.assertEqual(("127.0.0.1", 1001), queued_client)
        server.shutdown_request.assert_not_called()

    def test_shutdown_rejects_new_stream_without_overload_response(self):
        server = self._routing_server()
        server.stop_accepting()
        stream_request = _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")

        server.process_request(stream_request, ("127.0.0.1", 1001))

        self.assertEqual([], stream_request.sent_responses)
        server.shutdown_request.assert_called_once_with(stream_request)

    def test_stream_admitted_before_shutdown_is_drained_and_releases_slot(self):
        server = self._routing_server(stream_capacity=1)
        stream_request = _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")
        server.finish_request = MagicMock()
        server.handle_error = MagicMock()

        server.process_request(stream_request, ("127.0.0.1", 1001))
        server.stop_accepting()
        server._worker_loop(server._stream_request_queue, releases_stream_slot=True)

        server.finish_request.assert_called_once_with(stream_request, ("127.0.0.1", 1001))
        server.shutdown_request.assert_called_once_with(stream_request)
        self.assertTrue(server._stream_admission.acquire(blocking=False))

    def test_shutdown_before_stream_admission_closes_without_reserving_slot(self):
        server = self._routing_server(stream_capacity=1)
        server.stop_accepting()
        stream_request = _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")

        server.process_request(stream_request, ("127.0.0.1", 1001))

        self.assertTrue(server._stream_request_queue.empty())
        self.assertEqual([], stream_request.sent_responses)
        server.shutdown_request.assert_called_once_with(stream_request)
        self.assertTrue(server._stream_admission.acquire(blocking=False))


class TestMyWSGIRefServer(unittest.TestCase):
    def test_run_uses_bounded_server_and_request_handler(self):
        logger = MagicMock()
        server = MyWSGIRefServer(logger, host="0.0.0.0", port=8800)
        fake_server = MagicMock()
        fake_server.server_port = 8811
        fake_server.handle_request.side_effect = server.stop

        with patch("web.web_app_job.make_server", return_value=fake_server) as mock_make_server:
            with patch("web.web_app_job._RequestLoggingMiddleware", side_effect=lambda app, logger, level=logging.DEBUG: "logged-app") as mock_middleware:
                server.run("raw-app")

        mock_middleware.assert_called_once_with("raw-app", logger=logger, level=logging.DEBUG)
        mock_make_server.assert_called_once()
        self.assertEqual(("0.0.0.0", 8800, "logged-app"), mock_make_server.call_args.args[:3])
        self.assertIs(mock_make_server.call_args.kwargs["server_class"], web_app_job._BoundedWSGIServer)
        self.assertIs(mock_make_server.call_args.kwargs["handler_class"], _RequestHandler)
        self.assertEqual(8811, server.port)
        fake_server.handle_request.assert_called_once_with()
        fake_server.server_close.assert_called_once_with()
        self.assertIsNone(server.server)

    def test_stop_before_make_server_returns_is_not_lost(self):
        logger = MagicMock()
        server = MyWSGIRefServer(logger, host="127.0.0.1", port=8800)
        fake_server = MagicMock()
        fake_server.server_port = 8800

        def make_server_stops_during_bind(*args, **kwargs):
            server.stop()
            return fake_server

        with patch("web.web_app_job.make_server", side_effect=make_server_stops_during_bind):
            server.run("raw-app")

        fake_server.handle_request.assert_not_called()
        fake_server.server_close.assert_called_once_with()
        self.assertIsNone(server.server)


class TestWebAppJob(unittest.TestCase):
    def test_setup_constructs_server_with_normal_runtime_bind_host(self):
        for bind_host in ("0.0.0.0", "127.0.0.1"):
            with self.subTest(bind_host=bind_host):
                access_logger = MagicMock()
                context = SimpleNamespace(
                    logger=MagicMock(),
                    web_access_logger=access_logger,
                    config=SimpleNamespace(web=SimpleNamespace(port=8800)),
                    args=SimpleNamespace(web_bind_host=bind_host, debug=False),
                )
                app = MagicMock()

                with patch("web.web_app_job.MyWSGIRefServer") as server_type, \
                     patch("web.web_app_job.Thread") as thread_type:
                    WebAppJob(context, app).setup()

                server_type.assert_called_once_with(
                    access_logger,
                    host=bind_host,
                    port=8800,
                )
                thread_type.assert_called_once_with(
                    target=web_app_job.bottle.run,
                    kwargs={
                        "app": app,
                        "server": server_type.return_value,
                        "debug": False,
                    },
                )
                thread_type.return_value.start.assert_called_once_with()


class TestWebAppJobImport(unittest.TestCase):
    def test_import_does_not_require_paste(self):
        original_import = __import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "paste" or name.startswith("paste."):
                raise ModuleNotFoundError(name)
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=guarded_import):
            reloaded = importlib.reload(web_app_job)

        self.assertIs(reloaded, web_app_job)
