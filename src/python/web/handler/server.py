# Copyright 2017, Inderpreet Singh, All rights reserved.

from bottle import HTTPResponse

from common import Context, overrides
from ..web_app import IHandler, WebApp


class ServerHandler(IHandler):
    def __init__(self, context: Context) -> None:
        self.logger = context.logger.getChild("ServerActionHandler")
        self.__request_restart = False
        self.__request_recovery_restore = False

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp) -> None:
        web_app.add_post_handler(
            "/server/command/restart",
            self.__handle_action_restart,
            required_scope="write"
        )

    def is_restart_requested(self) -> bool:
        """
        Returns true is a restart is requested
        :return:
        """
        return self.__request_restart

    def request_recovery_restore(self) -> None:
        """Request a graceful reconstruction for a receipt-bound restore."""
        self.logger.info("Received a migration recovery restore action")
        self.__request_recovery_restore = True

    def is_recovery_restore_requested(self) -> bool:
        return self.__request_recovery_restore

    def __handle_action_restart(self) -> HTTPResponse:
        """
        Request a server restart
        :return:
        """
        self.logger.info("Received a restart action")
        self.__request_restart = True
        return HTTPResponse(body="Requested restart")
