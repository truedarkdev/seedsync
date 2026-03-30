# Copyright 2017, Inderpreet Singh, All rights reserved.

from typing import Type, Callable, Optional
from abc import ABC, abstractmethod
import time

import bottle
from bottle import static_file

from common import Context
from controller import Controller


class IHandler(ABC):
    """
    Abstract class that defines a web handler
    """
    @abstractmethod
    def add_routes(self, web_app: "WebApp"):
        """
        Add all the handled routes to the given web app
        :param web_app:
        :return:
        """
        pass


class IStreamHandler(ABC):
    """
    Abstract class that defines a streaming data provider
    """
    @abstractmethod
    def setup(self):
        pass

    @abstractmethod
    def get_value(self) -> Optional[str]:
        pass

    @abstractmethod
    def cleanup(self):
        pass

    @classmethod
    def register(cls, web_app: "WebApp", **kwargs):
        """
        Register this streaming handler with the web app
        :param web_app: web_app instance
        :param kwargs: args for stream handler ctor
        :return:
        """
        web_app.add_streaming_handler(cls, **kwargs)


class WebApp(bottle.Bottle):
    """
    Web app implementation
    """
    _STREAM_POLL_INTERVAL_IN_MS = 100
    _STREAM_EVENT_YIELD_INTERVAL_IN_MS = 10
    _CONTENT_SECURITY_POLICY = "connect-src 'self' https://api.github.com"

    def __init__(self, context: Context, controller: Controller):
        super().__init__()
        self.logger = context.logger.getChild("WebApp")
        self.__controller = controller
        self.__html_path = context.args.html_path
        self.__status = context.status
        self.__config = getattr(context, "config", None)
        self.logger.info("Html path set to: {}".format(self.__html_path))
        self._stop = False
        self.__streaming_handlers = []  # list of (handler, kwargs) pairs

        @self.hook("before_request")
        def __validate_host_header():
            if not WebApp.__is_server_path(bottle.request.path):
                return

            allowed_hostname = WebApp.__normalize_hostname(
                WebApp.__get_allowed_hostname(self.__config)
            )
            if not allowed_hostname:
                return

            host = WebApp.__normalize_hostname(
                WebApp.__strip_host_port(bottle.request.get_header("Host", ""))
            )

            # Best-effort local protection only: preserve loopback/container/dev access
            # while letting an explicit configured hostname narrow exposure.
            if host not in {"localhost", "127.0.0.1", "[::1]", allowed_hostname}:
                bottle.abort(400)

    def add_default_routes(self):
        """
        Add the default routes. This must be called after all the handlers have
        been added.
        :return:
        """
        # Streaming route
        self.get("/server/stream")(self.__web_stream)

        # Front-end routes
        self.route("/")(self.__index)
        self.route("/dashboard")(self.__index)
        self.route("/dashboard/<pathPairId>")(self.__dashboard_index)
        self.route("/settings")(self.__index)
        self.route("/autoqueue")(self.__index)
        self.route("/logs")(self.__index)
        self.route("/about")(self.__index)
        # For static files
        self.route("/<file_path:path>")(self.__static)

    def add_handler(self, path: str, handler: Callable):
        self.get(path)(handler)

    def add_post_handler(self, path: str, handler: Callable):
        self.post(path)(handler)

    def add_put_handler(self, path: str, handler: Callable):
        self.put(path)(handler)

    def add_delete_handler(self, path: str, handler: Callable):
        self.delete(path)(handler)

    def add_streaming_handler(self, handler: Type[IStreamHandler], **kwargs):
        self.__streaming_handlers.append((handler, kwargs))

    def process(self):
        """
        Advance the web app state
        :return:
        """
        pass

    def stop(self):
        """
        Exit gracefully, kill any connections and clean up any state
        :return:
        """
        object.__setattr__(self, "_stop", True)

    @staticmethod
    def __get_allowed_hostname(config) -> str:
        general_config = getattr(config, "general", None)
        if general_config is None:
            return ""
        allowed_hostname = getattr(general_config, "allowed_hostname", "")
        return allowed_hostname if isinstance(allowed_hostname, str) else ""

    @staticmethod
    def __is_server_path(path: str) -> bool:
        return path == "/server" or path.startswith("/server/")

    @staticmethod
    def __strip_host_port(host: str) -> str:
        if ":" in host and not host.startswith("["):
            return host.rsplit(":", 1)[0]
        if host.startswith("[") and "]:" in host:
            return host.rsplit(":", 1)[0]
        return host

    @staticmethod
    def __normalize_hostname(host: str) -> str:
        normalized = host.strip().lower()
        if normalized.endswith("."):
            normalized = normalized[:-1]
        if normalized.startswith("[") and normalized.endswith("]"):
            return normalized
        if ":" in normalized:
            return "[{}]".format(normalized)
        return normalized

    def __index(self):
        """
        Serves the index.html static file
        :return:
        """
        return self.__static("index.html")

    def __dashboard_index(self, pathPairId: str):
        """
        Serves the index.html static file for dashboard deep links.
        :param pathPairId:
        :return:
        """
        return self.__index()

    # noinspection PyMethodMayBeStatic
    def __static(self, file_path: str):
        """
        Serves all the static files
        :param file_path:
        :return:
        """
        response = static_file(file_path, root=self.__html_path)
        response.set_header("Content-Security-Policy", self._CONTENT_SECURITY_POLICY)
        return response

    def __web_stream(self):
        # Initialize all the handlers
        handlers = [cls(**kwargs) for (cls, kwargs) in self.__streaming_handlers]

        try:
            # Setup the response header
            bottle.response.content_type = "text/event-stream"
            bottle.response.cache_control = "no-cache"

            # Call setup on all handlers
            for handler in handlers:
                handler.setup()

            # Get streaming values until the connection closes
            while not self._stop:
                emitted_value = False
                for handler in handlers:
                    value = handler.get_value()
                    if value is not None:
                        emitted_value = True
                        yield value
                        time.sleep(WebApp._STREAM_EVENT_YIELD_INTERVAL_IN_MS / 1000)

                if not emitted_value:
                    time.sleep(WebApp._STREAM_POLL_INTERVAL_IN_MS / 1000)

        finally:
            self.logger.debug("Stream connection stopped by {}".format(
                "server" if self._stop else "client"
            ))

            # Cleanup all handlers
            for handler in handlers:
                handler.cleanup()
