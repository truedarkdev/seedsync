import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


UTILS_PATH = Path(__file__).resolve().parents[3] / "web" / "utils.py"
UTILS_SPEC = spec_from_file_location("test_stream_queue_utils", UTILS_PATH)
UTILS_MODULE = module_from_spec(UTILS_SPEC)
UTILS_SPEC.loader.exec_module(UTILS_MODULE)
StreamQueue = UTILS_MODULE.StreamQueue


class TestStreamQueue(unittest.TestCase):
    def test_default_queue_is_bounded(self):
        queue = StreamQueue()

        self.assertEqual(StreamQueue.DEFAULT_MAXSIZE, queue.get_maxsize())
        for index in range(StreamQueue.DEFAULT_MAXSIZE + 1):
            queue.put(index)

        self.assertEqual(StreamQueue.DEFAULT_MAXSIZE, queue.get_queue_size())
        self.assertEqual(1, queue.get_dropped_count())
        self.assertEqual(1, queue.get_next_event())

    def test_fifo_order_without_overflow(self):
        queue = StreamQueue(maxsize=3)

        queue.put("first")
        queue.put("second")

        self.assertEqual("first", queue.get_next_event())
        self.assertEqual("second", queue.get_next_event())
        self.assertIsNone(queue.get_next_event())
        self.assertEqual(0, queue.get_dropped_count())
        self.assertEqual(0, queue.get_queue_size())

    def test_overflow_drops_oldest_and_keeps_newest(self):
        queue = StreamQueue(maxsize=2)

        queue.put("first")
        queue.put("second")
        queue.put("third")

        self.assertEqual(1, queue.get_dropped_count())
        self.assertEqual(2, queue.get_queue_size())
        self.assertEqual("second", queue.get_next_event())
        self.assertEqual("third", queue.get_next_event())
        self.assertIsNone(queue.get_next_event())

    def test_maxsize_zero_keeps_unlimited_behavior(self):
        queue = StreamQueue(maxsize=0)

        for index in range(5):
            queue.put(index)

        self.assertEqual(0, queue.get_maxsize())
        self.assertEqual(0, queue.get_dropped_count())
        self.assertEqual(5, queue.get_queue_size())
        self.assertEqual(0, queue.get_next_event())
        self.assertEqual(1, queue.get_next_event())
        self.assertEqual(2, queue.get_next_event())
        self.assertEqual(3, queue.get_next_event())
        self.assertEqual(4, queue.get_next_event())
        self.assertIsNone(queue.get_next_event())
