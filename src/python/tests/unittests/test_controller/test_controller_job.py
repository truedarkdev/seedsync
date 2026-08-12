# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock

from controller import ControllerJob


class TestControllerJob(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.logger = MagicMock()
        self.controller = MagicMock()
        self.controller.process_wake_generation.return_value = 0
        self.controller.has_active_runtime_work.return_value = False
        self.controller.next_process_delay_seconds.return_value = 5.0
        self.controller.wait_for_process_wake.return_value = False
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

    def test_execute_ignores_diagnostic_collector_failure(self):
        self.context.performance_diagnostics.begin_duration.side_effect = RuntimeError("diagnostic")

        self.job.execute()

        self.controller.process.assert_called_once_with()
        self.auto_queue.process.assert_called_once_with()

    def test_idle_run_waits_for_controller_deadline(self):
        self.controller.process.side_effect = self.job.terminate

        self.job.start()
        self.assertTrue(self.job.wait_until_setup_complete(1))
        self.job.join(1)

        self.assertFalse(self.job.is_alive())
        self.controller.next_process_delay_seconds.assert_called_once_with()
        self.controller.wait_for_process_wake.assert_called_once_with(0, 5.0)

    def test_active_run_preserves_100ms_deadline(self):
        self.controller.has_active_runtime_work.return_value = True
        self.controller.next_process_delay_seconds.return_value = 0.1
        self.controller.process.side_effect = self.job.terminate

        self.job.start()
        self.assertTrue(self.job.wait_until_setup_complete(1))
        self.job.join(1)

        self.controller.wait_for_process_wake.assert_called_once_with(0, 0.1)

    def test_terminate_wakes_controller_wait(self):
        self.job.terminate()

        self.controller.wake_process.assert_called_once_with()

    def test_cleanup_exits_controller(self):
        self.job.cleanup()

        self.controller.exit.assert_called_once_with()
