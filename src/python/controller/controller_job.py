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

    @overrides(Job)
    def setup(self) -> None:
        self.__controller.start()

    @overrides(Job)
    def execute(self) -> None:
        diagnostics = self.__performance_diagnostics
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

    @overrides(Job)
    def _get_sleep_interval_in_secs(self) -> float:
        return ControllerJob._SLEEP_INTERVAL_IN_SECS

    @overrides(Job)
    def cleanup(self) -> None:
        self.__controller.exit()
