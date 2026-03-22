# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock
import time


from common import Job


class DummyError(Exception):
    pass


class DummyFailingJob(Job):
    def setup(self):
        # noinspection PyAttributeOutsideInit
        self.cleanup_run = False

    def execute(self):
        raise DummyError()

    def cleanup(self):
        # noinspection PyAttributeOutsideInit
        self.cleanup_run = True


class DummySetupJob(Job):
    def setup(self):
        # noinspection PyAttributeOutsideInit
        self.setup_run = True

    def execute(self):
        raise DummyError()

    def cleanup(self):
        # noinspection PyAttributeOutsideInit
        self.cleanup_run = True


class TestJob(unittest.TestCase):
    def test_exception_propagates(self):
        context = MagicMock()
        # noinspection PyTypeChecker
        job = DummyFailingJob("DummyFailingJob", context)
        job.start()
        time.sleep(0.2)
        with self.assertRaises(DummyError):
            job.propagate_exception()
        job.terminate()
        job.join()

    def test_wait_until_setup_complete_returns_after_setup(self):
        context = MagicMock()
        # noinspection PyTypeChecker
        job = DummySetupJob("DummySetupJob", context)
        job.start()
        self.assertTrue(job.wait_until_setup_complete(1))
        job.terminate()
        job.join()
        self.assertTrue(job.setup_run)

    def test_is_setup_complete_tracks_setup_completion(self):
        context = MagicMock()
        # noinspection PyTypeChecker
        job = DummySetupJob("DummySetupJob", context)
        self.assertFalse(job.is_setup_complete())
        job.start()
        self.assertTrue(job.wait_until_setup_complete(1))
        self.assertTrue(job.is_setup_complete())
        job.terminate()
        job.join()

    def test_cleanup_executes_on_execute_error(self):
        context = MagicMock()
        # noinspection PyTypeChecker
        job = DummyFailingJob("DummyFailingJob", context)
        job.start()
        time.sleep(0.2)
        job.terminate()
        job.join()
        self.assertTrue(job.cleanup_run)
