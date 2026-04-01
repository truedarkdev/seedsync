# Copyright 2017, Inderpreet Singh, All rights reserved.

import ipaddress
from typing import Type, Callable, Optional
from abc import ABC, abstractmethod
import time
from urllib.parse import urlparse

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
    _AUTH_SCOPES = {"read", "write", "stream", "admin"}
    _STREAM_POLL_INTERVAL_IN_MS = 100
    _STREAM_EVENT_YIELD_INTERVAL_IN_MS = 10
    _CONTENT_SECURITY_POLICY = "connect-src 'self' https://api.github.com"
    _UI_SESSION_COOKIE_NAME = "seedsync_ui_session"

    def __init__(self, context: Context, controller: Controller, auth_store: Optional[object] = None):
        super().__init__()
        self.logger = context.logger.getChild("WebApp")
        self.__controller = controller
        self.__html_path = context.args.html_path
        self.__status = context.status
        self.__config = getattr(context, "config", None)
        self.__auth_store = auth_store
        self.logger.info("Html path set to: {}".format(self.__html_path))
        self._stop = False
        self.__streaming_handlers = []  # list of (handler, kwargs) pairs

        @self.hook("before_request")
        def __gate_server_request():
            if not WebApp.__is_server_path(bottle.request.path):
                return

            allowed_hostname = WebApp.__normalize_hostname(
                WebApp.__get_allowed_hostname(self.__config)
            )
            if allowed_hostname:
                host = WebApp.__normalize_hostname(
                    WebApp.__strip_host_port(bottle.request.get_header("Host", ""))
                )

                # Best-effort local protection only: preserve loopback/container/dev access
                # while letting an explicit configured hostname narrow exposure.
                if host not in {"localhost", "127.0.0.1", "[::1]", allowed_hostname}:
                    bottle.abort(400)

            try:
                route, _ = self.match(bottle.request.environ)
            except bottle.HTTPError:
                bottle.abort(404)

            if not WebApp.__is_server_path(route.rule):
                bottle.abort(404)

            required_scope = route.config.get("required_scope")
            if not isinstance(required_scope, str):
                bottle.abort(404)

            required_scope = required_scope.strip().lower()
            if required_scope not in WebApp._AUTH_SCOPES:
                bottle.abort(404)

            self.__authorize_server_route(
                required_scope,
                bool(route.config.get("allow_legacy_api_token", False)),
                bool(route.config.get("allow_sessionless_ui", False)),
                bool(route.config.get("allow_first_admin_bootstrap", False)),
            )

    def add_default_routes(self):
        """
        Add the default routes. This must be called after all the handlers have
        been added.
        :return:
        """
        # Streaming route
        self.get(
            "/server/stream",
            required_scope="stream",
            allow_legacy_api_token=True,
        )(self.__web_stream)

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

    def add_handler(self, path: str, handler: Callable, required_scope: Optional[str] = None, **config):
        self.get(path, required_scope=required_scope, **config)(handler)

    def add_post_handler(self, path: str, handler: Callable, required_scope: Optional[str] = None, **config):
        self.post(path, required_scope=required_scope, **config)(handler)

    def add_put_handler(self, path: str, handler: Callable, required_scope: Optional[str] = None, **config):
        self.put(path, required_scope=required_scope, **config)(handler)

    def add_delete_handler(self, path: str, handler: Callable, required_scope: Optional[str] = None, **config):
        self.delete(path, required_scope=required_scope, **config)(handler)

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

    def route(self, path=None, method="GET", callback=None, name=None, apply=None, skip=None,
              required_scope: Optional[str] = None,
              allow_legacy_api_token: bool = False,
              allow_sessionless_ui: bool = False,
              allow_first_admin_bootstrap: bool = False,
              **config):
        if path is not None and WebApp.__is_server_path(path):
            if required_scope is None:
                raise ValueError("required_scope is required for /server routes")
            normalized_scope = required_scope.strip().lower()
            if normalized_scope not in WebApp._AUTH_SCOPES:
                raise ValueError("Unknown server route scope '{}'".format(required_scope))
            if allow_sessionless_ui and normalized_scope not in {"read", "stream"}:
                raise ValueError("allow_sessionless_ui is only supported for read/stream /server routes")
            config["required_scope"] = normalized_scope
            for flag_name, flag_value in {
                "allow_legacy_api_token": allow_legacy_api_token,
                "allow_sessionless_ui": allow_sessionless_ui,
                "allow_first_admin_bootstrap": allow_first_admin_bootstrap,
            }.items():
                if type(flag_value) is not bool:
                    raise ValueError("{} must be a boolean for /server routes".format(flag_name))
                config[flag_name] = flag_value
        return super().route(path=path, method=method, callback=callback, name=name, apply=apply, skip=skip, **config)

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

    @staticmethod
    def __is_blank_value(value) -> bool:
        return not isinstance(value, str) or value.strip() == ""

    @staticmethod
    def __extract_bearer_token() -> Optional[str]:
        auth_header = bottle.request.get_header("Authorization", "")
        if not isinstance(auth_header, str):
            return None
        auth_header = auth_header.strip()
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer "):].strip()
        return token if token else None

    def __get_legacy_api_token(self) -> Optional[str]:
        general_config = getattr(self.__config, "general", None)
        if general_config is None:
            return None
        api_token = getattr(general_config, "api_token", None)
        return api_token if isinstance(api_token, str) and api_token.strip() else None

    @staticmethod
    def __has_bearer_authorization_header() -> bool:
        auth_header = bottle.request.get_header("Authorization", "")
        if not isinstance(auth_header, str):
            return False
        auth_header = auth_header.strip()
        return auth_header == "Bearer" or auth_header.startswith("Bearer ")

    @staticmethod
    def __request_host() -> str:
        return WebApp.__normalize_hostname(
            WebApp.__strip_host_port(bottle.request.get_header("Host", ""))
        )

    @staticmethod
    def __request_remote_addr() -> str:
        remote_addr = bottle.request.environ.get("REMOTE_ADDR", "")
        return remote_addr.strip() if isinstance(remote_addr, str) else ""

    @staticmethod
    def __request_header_hostname(header_name: str) -> Optional[str]:
        raw_value = bottle.request.get_header(header_name, "")
        if not isinstance(raw_value, str) or raw_value.strip() == "":
            return None

        parsed = urlparse(raw_value.strip())
        if parsed.hostname:
            return WebApp.__normalize_hostname(parsed.hostname)

        return WebApp.__normalize_hostname(
            WebApp.__strip_host_port(raw_value.strip())
        )

    @staticmethod
    def __is_loopback_host(host: str) -> bool:
        return host in {"localhost", "127.0.0.1", "[::1]"}

    @staticmethod
    def __is_loopback_remote_addr() -> bool:
        remote_addr = WebApp.__request_remote_addr()
        if not remote_addr:
            return False

        try:
            return ipaddress.ip_address(remote_addr).is_loopback
        except ValueError:
            return False

    @staticmethod
    def __is_same_origin_browser_request() -> bool:
        host = WebApp.__request_host()
        if not host:
            return False

        sec_fetch_site = bottle.request.get_header("Sec-Fetch-Site", "")
        if isinstance(sec_fetch_site, str) and sec_fetch_site.strip():
            sec_fetch_site = sec_fetch_site.strip().lower()
            if sec_fetch_site not in {"same-origin", "none"}:
                return False
            if sec_fetch_site == "same-origin":
                return True

        origin_host = WebApp.__request_header_hostname("Origin")
        if origin_host is not None:
            return origin_host == host

        referer_host = WebApp.__request_header_hostname("Referer")
        if referer_host is not None:
            return referer_host == host

        return False

    def __allow_first_admin_bootstrap(self) -> bool:
        return (
            self.__auth_store is not None and
            getattr(self.__auth_store, "active_admin_key_count", 0) == 0 and
            WebApp.__is_loopback_host(WebApp.__request_host())
        )

    def __authorize_server_route(
        self,
        required_scope: str,
        allow_legacy_api_token: bool,
        allow_sessionless_ui: bool,
        allow_first_admin_bootstrap: bool
    ) -> None:
        token = WebApp.__extract_bearer_token()
        if token is None:
            if not WebApp.__has_bearer_authorization_header():
                ui_session_scopes = self.__get_ui_session_scopes()
                if ui_session_scopes is not None:
                    self.__authorize_scopes(required_scope, ui_session_scopes)
                    return
                if allow_first_admin_bootstrap and self.__allow_first_admin_bootstrap():
                    return
                if allow_sessionless_ui and WebApp.__is_same_origin_browser_request():
                    return
            bottle.abort(401, "Missing API token")

        auth_record = None
        if self.__auth_store is not None:
            auth_record = self.__auth_store.find_api_key_by_secret(token)

        if auth_record is not None:
            if getattr(auth_record, "revoked_at", None) is not None:
                bottle.abort(403, "API key has been revoked")

            self.__authorize_scopes(
                required_scope,
                auth_record.scopes,
                forbidden_message="API key '{}' lacks scope '{}'".format(auth_record.id, required_scope)
            )
            return

        legacy_token = self.__get_legacy_api_token()
        if legacy_token is not None and token == legacy_token:
            if not allow_legacy_api_token:
                if required_scope == "admin":
                    bottle.abort(403, "Legacy general.api_token cannot access admin endpoints")
                bottle.abort(403, "Legacy general.api_token cannot access this route")
            if self.__auth_store is not None and not self.__auth_store.legacy_api_token_compatibility_enabled:
                bottle.abort(403, "Legacy general.api_token compatibility has been disabled")
            return

        bottle.abort(401, "Invalid API token")

    def __get_ui_session_scopes(self):
        if self.__auth_store is None or not WebApp.__is_loopback_remote_addr():
            return None

        ui_session_secret = bottle.request.get_cookie(self._UI_SESSION_COOKIE_NAME)
        if not isinstance(ui_session_secret, str) or ui_session_secret.strip() == "":
            return None

        find_session = getattr(self.__auth_store, "find_ui_session_by_secret", None)
        if find_session is None:
            return None

        session = find_session(ui_session_secret)
        if session is None:
            return None

        return getattr(session, "scopes", None)

    def __authorize_scopes(self, required_scope: str, scopes, forbidden_message: Optional[str] = None) -> None:
        allowed_scopes = set(scopes or [])
        if "admin" in allowed_scopes:
            allowed_scopes.update(WebApp._AUTH_SCOPES)
        if required_scope not in allowed_scopes:
            bottle.abort(403, forbidden_message or "Session lacks scope '{}'".format(required_scope))

    def __create_ui_session_secret(self) -> Optional[str]:
        if self.__auth_store is None or not WebApp.__is_loopback_remote_addr():
            return None

        current_scopes = self.__get_ui_session_scopes()
        if current_scopes is not None:
            return None

        create_session = getattr(self.__auth_store, "create_ui_session", None)
        if create_session is None:
            return None

        ui_session = create_session(["admin"])
        return ui_session.secret

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
        if file_path == "index.html":
            ui_session_secret = self.__create_ui_session_secret()
            if ui_session_secret is not None:
                response.set_cookie(
                    self._UI_SESSION_COOKIE_NAME,
                    ui_session_secret,
                    path="/",
                    httponly=True,
                    samesite="strict",
                )
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
