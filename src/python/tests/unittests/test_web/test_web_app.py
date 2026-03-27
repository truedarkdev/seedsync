import unittest
import os
from unittest.mock import MagicMock, patch

from web.web_app import IStreamHandler, WebApp
from web.web_app_builder import WebAppBuilder


class QueueStreamHandler(IStreamHandler):
    def __init__(self, values, cleanup_log):
        self.values = list(values)
        self.cleanup_log = cleanup_log

    def setup(self):
        pass

    def get_value(self):
        if self.values:
            return self.values.pop(0)
        return None

    def cleanup(self):
        self.cleanup_log.append(True)


class TestWebAppStream(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.logger.getChild.return_value = MagicMock()
        self.context.args.html_path = "/tmp"
        self.context.config = MagicMock()
        self.context.status = MagicMock()
        self.context.path_pair_manager = None
        self.web_app = WebApp(self.context, MagicMock())

    def test_stream_interleaves_one_event_per_handler_per_cycle(self):
        first_cleanup = []
        second_cleanup = []
        self.web_app.add_streaming_handler(QueueStreamHandler, values=["a1", "a2"], cleanup_log=first_cleanup)
        self.web_app.add_streaming_handler(QueueStreamHandler, values=["b1", "b2"], cleanup_log=second_cleanup)

        with patch("web.web_app.time.sleep"):
            stream = self.web_app._WebApp__web_stream()
            self.assertEqual("a1", next(stream))
            self.assertEqual("b1", next(stream))
            self.assertEqual("a2", next(stream))
            self.assertEqual("b2", next(stream))
            stream.close()

        self.assertEqual([True], first_cleanup)
        self.assertEqual([True], second_cleanup)

    def test_stream_paces_between_emitted_events(self):
        self.web_app.add_streaming_handler(QueueStreamHandler, values=["a1", "a2"], cleanup_log=[])

        with patch("web.web_app.time.sleep") as sleep:
            stream = self.web_app._WebApp__web_stream()
            self.assertEqual("a1", next(stream))
            self.assertEqual("a2", next(stream))
            stream.close()

        sleep.assert_called_once_with(WebApp._STREAM_EVENT_YIELD_INTERVAL_IN_MS / 1000)

    def test_stop_does_not_raise_and_stops_active_stream(self):
        cleanup_log = []
        self.web_app.add_streaming_handler(
            QueueStreamHandler,
            values=["a1"],
            cleanup_log=cleanup_log,
        )

        with patch("web.web_app.time.sleep"):
            stream = self.web_app._WebApp__web_stream()
            self.assertEqual("a1", next(stream))

            self.web_app.stop()

            with self.assertRaises(StopIteration):
                next(stream)

        self.assertEqual([True], cleanup_log)

    def test_builder_registers_heartbeat_stream_handler(self):
        auto_queue_persist = MagicMock()

        web_app = WebAppBuilder(self.context, MagicMock(), auto_queue_persist).build()

        streaming_handler_classes = [
            stream_handler[0]
            for stream_handler in web_app._WebApp__streaming_handlers
        ]
        self.assertEqual("HeartbeatStreamHandler", streaming_handler_classes[-1].__name__)

    def test_builder_wires_lftp_local_path_into_controller_handler(self):
        auto_queue_persist = MagicMock()
        self.context.config.lftp = MagicMock()
        self.context.config.lftp.local_path = "/tmp/downloads"

        builder = WebAppBuilder(self.context, MagicMock(), auto_queue_persist)

        self.assertEqual(
            os.path.realpath("/tmp/downloads"),
            builder.controller_handler._ControllerHandler__local_path_root
        )

    def test_builder_leaves_controller_guard_root_unset_without_local_path(self):
        auto_queue_persist = MagicMock()
        self.context.config.lftp = MagicMock()
        self.context.config.lftp.local_path = None

        builder = WebAppBuilder(self.context, MagicMock(), auto_queue_persist)

        self.assertIsNone(builder.controller_handler._ControllerHandler__local_path_root)
