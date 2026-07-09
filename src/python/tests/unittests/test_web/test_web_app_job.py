import importlib
import logging
import socket
import unittest
from email.message import Message
from queue import Queue
from socketserver import ThreadingMixIn
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import web.web_app_job as web_app_job
from web.web_app_job import MyWSGIRefServer, _BoundedWSGIServer, _RequestHandler, _RequestLoggingMiddleware


class _FakeSocket:
    def __init__(self, request_line=b"", recv_error=None):
        self._request_line = request_line
        self._recv_error = recv_error
        self._timeout = 30
        self.recv_flags = None

    def gettimeout(self):
        return self._timeout

    def settimeout(self, timeout):
        self._timeout = timeout

    def recv(self, size, flags=0):
        self.recv_flags = flags
        if self._recv_error is not None:
            raise self._recv_error
        return self._request_line


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
    def test_uses_fixed_worker_pool_and_bounded_queue(self):
        self.assertFalse(issubclass(_BoundedWSGIServer, ThreadingMixIn))
        self.assertEqual(10, _BoundedWSGIServer.worker_count)
        self.assertEqual(8, _BoundedWSGIServer.normal_worker_count)
        self.assertEqual(2, _BoundedWSGIServer.stream_worker_count)
        self.assertEqual(5, _BoundedWSGIServer.normal_queue_size)
        self.assertEqual(5, _BoundedWSGIServer.stream_queue_size)
        self.assertEqual(5, _BoundedWSGIServer.request_queue_size)

    def test_stream_queue_saturation_does_not_block_normal_request_queue(self):
        server = object.__new__(_BoundedWSGIServer)
        server._worker_shutdown = Event()
        server._normal_request_queue = Queue(maxsize=1)
        server._stream_request_queue = Queue(maxsize=1)
        server.shutdown_request = MagicMock()

        existing_stream = _FakeSocket(b"GET /server/stream HTTP/1.1\r\n\r\n")
        extra_stream = _FakeSocket(b"GET /server/stream?since=1 HTTP/1.1\r\n\r\n")
        normal_request = _FakeSocket(b"GET /server/config HTTP/1.1\r\n\r\n")
        server._stream_request_queue.put_nowait((existing_stream, ("127.0.0.1", 1001)))

        server.process_request(extra_stream, ("127.0.0.1", 1002))
        server.process_request(normal_request, ("127.0.0.1", 1003))

        server.shutdown_request.assert_called_once_with(extra_stream)
        queued_request, queued_client = server._normal_request_queue.get_nowait()
        self.assertIs(normal_request, queued_request)
        self.assertEqual(("127.0.0.1", 1003), queued_client)
        self.assertEqual(socket.MSG_PEEK, extra_stream.recv_flags)
        self.assertEqual(socket.MSG_PEEK, normal_request.recv_flags)

    def test_unreadable_or_partial_request_line_uses_normal_queue(self):
        server = object.__new__(_BoundedWSGIServer)
        server._worker_shutdown = Event()
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
        server._normal_request_queue = Queue(maxsize=1)
        server._stream_request_queue = Queue(maxsize=1)
        server.shutdown_request = MagicMock()

        encoded_stream_request = _FakeSocket(b"GET /server%2fstream?since=1 HTTP/1.1\r\n\r\n")

        server.process_request(encoded_stream_request, ("127.0.0.1", 1001))

        self.assertTrue(server._normal_request_queue.empty())
        queued_request, queued_client = server._stream_request_queue.get_nowait()
        self.assertIs(encoded_stream_request, queued_request)
        self.assertEqual(("127.0.0.1", 1001), queued_client)
        server.shutdown_request.assert_not_called()


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
