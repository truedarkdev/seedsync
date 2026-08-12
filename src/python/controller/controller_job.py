# Copyright 2017, Inderpreet Singh, All rights reserved.


# my libs
from common import overrides, Job, Context
from common.performance_diagnostics import DURATION_CONTROLLER_JOB
from .controller import Controller
from .auto_queue import AutoQueue


class ControllerJob(Job):
    """
    The controller service
    Handles querying and downloading of files
    """
    _SLEEP_INTERVAL_IN_SECS = 0.1

    def __init__(self,
                 context: Context,
                 controller: Controller,
                 auto_queue: AutoQueue):
        super().__init__(name=self.__class__.__name__, context=context)
        self.__controller = controller
        self.__auto_queue = auto_queue
        self.__performance_diagnostics = getattr(context, "performance_diagnostics", None)
        self.__observed_wake_generation = 0
        self.__wake_reason = "startup"

    @overrides(Job)
    def setup(self) -> None:
        self.__controller.start()

    @overrides(Job)
    def execute(self) -> None:
        diagnostics = self.__performance_diagnostics
        self.__observed_wake_generation = self.__controller.process_wake_generation()
        if diagnostics is not None:
            try:
                diagnostics.increment("controller_job_wakes")
                diagnostics.increment("controller_job_wake_{}".format(self.__wake_reason))
            except Exception:
                pass
        try:
            started_at = diagnostics.begin_duration(DURATION_CONTROLLER_JOB) if diagnostics is not None else None
        except Exception:
            started_at = None
        try:
            self.__controller.process()
            self.__auto_queue.process()
        finally:
            if diagnostics is not None:
                try:
                    diagnostics.finish_duration(DURATION_CONTROLLER_JOB, started_at)
                except Exception:
                    pass
        active = self.__controller.has_active_runtime_work()
        if diagnostics is not None:
            try:
                diagnostics.increment("controller_job_active_cycles" if active else "controller_job_idle_cycles")
            except Exception:
                pass

    @overrides(Job)
    def _wait_for_next_execution(self) -> None:
        timeout = self.__controller.next_process_delay_seconds()
        diagnostics = self.__performance_diagnostics
        if diagnostics is not None:
            try:
                diagnostics.set_gauges({
                    "controller_active_runtime_work": int(self.__controller.has_active_runtime_work()),
                    "controller_next_deadline_ms": max(0, int(timeout * 1000)),
                })
            except Exception:
                pass
        signaled = self.__controller.wait_for_process_wake(self.__observed_wake_generation, timeout)
        self.__wake_reason = "event" if signaled else "deadline"

    @overrides(Job)
    def terminate(self) -> None:
        super().terminate()
        self.__controller.wake_process()

    @overrides(Job)
    def cleanup(self) -> None:
        self.__controller.exit()
