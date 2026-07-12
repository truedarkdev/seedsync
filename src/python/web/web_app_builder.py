# Copyright 2017, Inderpreet Singh, All rights reserved.

from common import Context
from typing import Optional
from controller import Controller, AutoQueuePersist
from .auth_store import ApiKeyStore
from .web_app import WebApp
from .handler.stream_model import ModelStreamHandler
from .handler.stream_status import StatusStreamHandler
from .handler.controller import ControllerHandler
from .handler.server import ServerHandler
from .handler.config import ConfigHandler
from .handler.auto_queue import AutoQueueHandler
from .handler.admin import AdminHandler
from .handler.stream_log import LogStreamHandler
from .handler.stream_heartbeat import HeartbeatStreamHandler
from .handler.status import StatusHandler
from .handler.path_pairs import PathPairsHandler
from .handler.breadcrumb_trace import BreadcrumbTraceHandler
from .handler.notifications import NotificationsAdminHandler
from .handler.historical_log import HistoricalLogHandler, HistoricalLogStore


class WebAppBuilder:
    """
    Helper class to build WebApp with all the extensions
    """
    def __init__(self,
                 context: Context,
                 controller: Controller,
                 auto_queue_persist: AutoQueuePersist,
                 auth_store: Optional[ApiKeyStore] = None,
                 notifier=None):
        self.__context = context
        self.__controller = controller
        self.__auth_store = auth_store

        local_path = None
        if getattr(context, "config", None) is not None and getattr(context.config, "lftp", None) is not None:
            local_path = getattr(context.config.lftp, "local_path", None)

        self.controller_handler = ControllerHandler(controller, local_path=local_path)
        self.server_handler = ServerHandler(context)
        self.config_handler = ConfigHandler(
            context.config,
            breadcrumb_trace_sync=context.breadcrumb_trace.sync_enabled_state,
            lftp_reconfigure_request=self.__controller.request_lftp_reconfigure,
        )
        self.auto_queue_handler = AutoQueueHandler(auto_queue_persist)
        self.status_handler = StatusHandler(context.status)
        self.breadcrumb_trace_handler = BreadcrumbTraceHandler(context)
        history_path = getattr(context.args, "history_log_path", None)
        self.historical_log_handler = HistoricalLogHandler(
            HistoricalLogStore(history_path, 10), context.logger
        ) if history_path else None
        self.admin_handler = None
        self.notifications_admin_handler = None
        if self.__auth_store is not None:
            self.admin_handler = AdminHandler(context.config, self.__auth_store)
            if notifier is not None:
                self.notifications_admin_handler = NotificationsAdminHandler(context.config, notifier)
        self.path_pairs_handler = None
        path_pair_manager = getattr(context, "path_pair_manager", None)
        if path_pair_manager is not None:
            self.path_pairs_handler = PathPairsHandler(path_pair_manager, controller=self.__controller)

    def build(self) -> WebApp:
        web_app = WebApp(context=self.__context,
                         controller=self.__controller,
                         auth_store=self.__auth_store)

        StatusStreamHandler.register(web_app=web_app,
                                     status=self.__context.status)

        LogStreamHandler.register(web_app=web_app,
                                  logger=self.__context.logger)

        ModelStreamHandler.register(web_app=web_app,
                                    controller=self.__controller)

        HeartbeatStreamHandler.register(web_app=web_app)

        self.controller_handler.add_routes(web_app)
        self.server_handler.add_routes(web_app)
        self.config_handler.add_routes(web_app)
        self.auto_queue_handler.add_routes(web_app)
        self.status_handler.add_routes(web_app)
        self.breadcrumb_trace_handler.add_routes(web_app)
        if self.historical_log_handler is not None:
            self.historical_log_handler.add_routes(web_app)
        if self.admin_handler is not None:
            self.admin_handler.add_routes(web_app)
        if self.notifications_admin_handler is not None:
            self.notifications_admin_handler.add_routes(web_app)
        if self.path_pairs_handler is not None:
            self.path_pairs_handler.add_routes(web_app)

        web_app.add_default_routes()

        return web_app
