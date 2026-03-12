import unittest
from unittest.mock import MagicMock

from controller.memory_monitor import ControllerMemoryMonitor


class TestControllerMemoryMonitor(unittest.TestCase):
    def test_log_if_due_waits_until_interval_elapses(self):
        now_values = iter([10, 14, 15])
        logger = MagicMock()
        monitor = ControllerMemoryMonitor(
            logger,
            log_interval_in_secs=5,
            time_fn=lambda: next(now_values)
        )

        self.assertFalse(monitor.log_if_due(1, 2, 3, 4, 5, 6, 7))
        self.assertFalse(monitor.log_if_due(1, 2, 3, 4, 5, 6, 7))
        self.assertTrue(monitor.log_if_due(1, 2, 3, 4, 5, 6, 7))

        logger.info.assert_called_once_with(
            "Memory monitor: model_files=%s downloaded_files=%s extracted_files=%s "
            "stopped_files=%s active_downloads=%s active_extracts=%s active_commands=%s",
            1,
            2,
            3,
            4,
            5,
            6,
            7
        )

    def test_log_if_due_resets_after_logging(self):
        now_values = iter([10, 15, 19, 20])
        logger = MagicMock()
        monitor = ControllerMemoryMonitor(
            logger,
            log_interval_in_secs=5,
            time_fn=lambda: next(now_values)
        )

        monitor.log_if_due(1, 1, 1, 1, 1, 1, 1)
        self.assertTrue(monitor.log_if_due(1, 1, 1, 1, 1, 1, 1))
        self.assertFalse(monitor.log_if_due(1, 1, 1, 1, 1, 1, 1))
        self.assertTrue(monitor.log_if_due(1, 1, 1, 1, 1, 1, 1))

        self.assertEqual(2, logger.info.call_count)
