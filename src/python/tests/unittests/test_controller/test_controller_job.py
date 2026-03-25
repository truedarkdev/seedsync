# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

from controller import ControllerJob


class TestControllerJob(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.logger = MagicMock()
        self.controller = MagicMock()
        self.auto_queue = MagicMock()
        self.job = ControllerJob(
            context=self.context,
            controller=self.controller,
            auto_queue=self.auto_queue
        )

    def test_setup_starts_controller(self):
        self.job.setup()

        self.controller.start.assert_called_once_with()

    def test_execute_processes_controller_before_auto_queue(self):
        call_order = []
        self.controller.process.side_effect = lambda: call_order.append("controller.process")
        self.auto_queue.process.side_effect = lambda: call_order.append("auto_queue.process")

        self.job.execute()

        self.controller.process.assert_called_once_with()
        self.auto_queue.process.assert_called_once_with()
        self.assertEqual(["controller.process", "auto_queue.process"], call_order)

    def test_run_uses_controller_specific_sleep_interval(self):
        self.controller.process.side_effect = self.job.terminate

        with patch("common.job.time.sleep") as mock_sleep:
            self.job.start()
            self.assertTrue(self.job.wait_until_setup_complete(1))
            self.job.join(1)

        self.assertFalse(self.job.is_alive())
        mock_sleep.assert_called_once_with(ControllerJob._SLEEP_INTERVAL_IN_SECS)

    def test_cleanup_exits_controller(self):
        self.job.cleanup()

        self.controller.exit.assert_called_once_with()
