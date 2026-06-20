# Copyright 2017, Inderpreet Singh, All rights reserved.

import ipaddress
from typing import Any, Type, Callable, Optional, Tuple
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
    _BOOTSTRAP_EXCHANGE_COOKIE_NAME = "seedsync_bootstrap_exchange"
    _BOOTSTRAP_SAFE_STATIC_ASSET_PATHS = {
        "/assets/favicon.png",
        "/assets/logo.png",
    }

    def __init__(self, context: Context, controller: Controller, auth_store: Optional[object] = None):
        super().__init__()
        self.logger = context.logger.getChild("WebApp")
        self.__controller = controller
        self.__html_path = context.args.html_path
        self.__status = context.status
        self.__config = getattr(context, "config", None)
        self.__auth_store = auth_store
        self.__trusted_browser_bootstrap_networks = self.__load_trusted_browser_bootstrap_networks()
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

            route: Any = None
            try:
                route, _ = self.match(bottle.request.environ)
            except bottle.HTTPError:
                bottle.abort(404)

            assert route is not None
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
                bool(route.config.get("allow_sessionless_ui", False)),
                bool(route.config.get("allow_first_admin_bootstrap", False)),
                bool(route.config.get("allow_bootstrap_proof_exchange", False)),
                bool(route.config.get("allow_browser_api_key_entry", False)),
            )

    def add_default_routes(self):
        """
        Add the default routes. This must be called after all the handlers have
        been added.
        :return:
        """
        # Bootstrap landing page. It is intentionally tiny and same-origin so the
        # browser can claim first-run admin access or remember an API key without
        # exposing credentials to the helper process.
        self.route("/bootstrap")(self.__bootstrap)  # type: ignore[operator]

        # Streaming route
        self.get(
            "/server/stream",
            required_scope="stream",
            allow_sessionless_ui=True,
        )(self.__web_stream)  # type: ignore[operator]

        # Front-end routes
        self.route("/")(self.__index)  # type: ignore[operator]
        self.route("/dashboard")(self.__index)  # type: ignore[operator]
        self.route("/dashboard/<pathPairId>")(self.__dashboard_index)  # type: ignore[operator]
        self.route("/settings")(self.__index)  # type: ignore[operator]
        self.route("/autoqueue")(self.__index)  # type: ignore[operator]
        self.route("/logs")(self.__index)  # type: ignore[operator]
        self.route("/about")(self.__index)  # type: ignore[operator]
        # For static files
        self.route("/<file_path:path>")(self.__static)  # type: ignore[operator]

    def add_handler(self, path: str, handler: Callable, required_scope: Optional[str] = None, **config):
        self.get(path, required_scope=required_scope, **config)(handler)  # type: ignore[operator]

    def add_post_handler(self, path: str, handler: Callable, required_scope: Optional[str] = None, **config):
        self.post(path, required_scope=required_scope, **config)(handler)  # type: ignore[operator]

    def add_put_handler(self, path: str, handler: Callable, required_scope: Optional[str] = None, **config):
        self.put(path, required_scope=required_scope, **config)(handler)  # type: ignore[operator]

    def add_delete_handler(self, path: str, handler: Callable, required_scope: Optional[str] = None, **config):
        self.delete(path, required_scope=required_scope, **config)(handler)  # type: ignore[operator]

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
              allow_sessionless_ui: bool = False,
              allow_first_admin_bootstrap: bool = False,
              allow_bootstrap_proof_exchange: bool = False,
              allow_browser_api_key_entry: bool = False,
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
                "allow_sessionless_ui": allow_sessionless_ui,
                "allow_first_admin_bootstrap": allow_first_admin_bootstrap,
                "allow_bootstrap_proof_exchange": allow_bootstrap_proof_exchange,
                "allow_browser_api_key_entry": allow_browser_api_key_entry,
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
    def __get_trusted_browser_bootstrap_remote_addrs(config) -> str:
        general_config = getattr(config, "general", None)
        if general_config is None:
            return ""
        remote_addrs = getattr(general_config, "trusted_browser_bootstrap_remote_addrs", "")
        return remote_addrs if isinstance(remote_addrs, str) else ""

    def __get_browser_handover_version(self) -> str:
        general_config = getattr(self.__config, "general", None)
        if general_config is None:
            return ""
        browser_handover_version = getattr(general_config, "browser_handover_recovery_version", "")
        return browser_handover_version if isinstance(browser_handover_version, str) else ""

    def __is_browser_handover_open(self) -> bool:
        if self.__auth_store is None:
            return False

        auth_store: Any = self.__auth_store
        browser_handover_state = auth_store.get_browser_handover_state(self.__config)
        return bool(browser_handover_state.get("open", False))

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
    def __request_forwarded_proto() -> Optional[str]:
        raw_value = bottle.request.get_header("X-Forwarded-Proto", "")
        if not isinstance(raw_value, str):
            return None

        raw_value = raw_value.strip().lower()
        if not raw_value or "," in raw_value:
            return None

        if raw_value not in {"http", "https"}:
            return None

        return raw_value

    @staticmethod
    def __request_forwarded_host() -> Optional[str]:
        raw_value = bottle.request.get_header("X-Forwarded-Host", "")
        if not isinstance(raw_value, str):
            return None

        raw_value = raw_value.strip()
        if not raw_value or "," in raw_value or "://" in raw_value:
            return None

        parsed = urlparse("//{}".format(raw_value))
        if (
            not parsed.hostname or
            parsed.username is not None or
            parsed.password is not None or
            parsed.path or
            parsed.params or
            parsed.query or
            parsed.fragment
        ):
            return None

        return raw_value

    @staticmethod
    def __has_proxy_forwarding_headers() -> bool:
        for header_name in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto"):
            raw_value = bottle.request.get_header(header_name, "")
            if isinstance(raw_value, str) and raw_value.strip():
                return True
        return False

    @staticmethod
    def __request_forwarded_origin() -> Optional[Tuple[str, str, int]]:
        forwarded_proto = WebApp.__request_forwarded_proto()
        forwarded_host = WebApp.__request_forwarded_host()
        if forwarded_proto is None or forwarded_host is None:
            return None

        parsed = urlparse("{}://{}".format(forwarded_proto, forwarded_host))
        if not parsed.scheme or not parsed.hostname:
            return None

        if parsed.username is not None or parsed.password is not None or parsed.path or parsed.params or parsed.query or parsed.fragment:
            return None

        scheme = parsed.scheme.strip().lower()
        if scheme not in {"http", "https"}:
            return None

        try:
            port = parsed.port
        except ValueError:
            return None

        if port is None:
            port = 80 if scheme == "http" else 443

        return scheme, WebApp.__normalize_hostname(parsed.hostname), port

    def __is_trusted_forwarded_origin_source(self) -> bool:
        return self.__is_loopback_remote_addr() or self.__is_trusted_browser_bootstrap_remote_addr()

    def __effective_request_origin(self) -> Optional[Tuple[str, str, int]]:
        request_origin = WebApp.__request_origin()
        if request_origin is None:
            return None

        if not self.__is_trusted_browser_bootstrap_remote_addr():
            return request_origin

        forwarded_origin = WebApp.__request_forwarded_origin()
        if forwarded_origin is not None:
            return forwarded_origin

        return request_origin

    @staticmethod
    def __request_local_origin() -> Optional[Tuple[str, str, int]]:
        scheme = bottle.request.environ.get("wsgi.url_scheme", "")
        if not isinstance(scheme, str):
            return None
        scheme = scheme.strip().lower()
        if scheme not in {"http", "https"}:
            return None

        host = bottle.request.get_header("Host", "")
        if not isinstance(host, str) or host.strip() == "":
            return None

        return WebApp.__parse_origin("{}://{}".format(scheme, host.strip()))

    def __is_direct_same_origin_browser_request(self) -> bool:
        request_origin = WebApp.__request_local_origin()
        if request_origin is None:
            return False

        origin = WebApp.__parse_origin(bottle.request.get_header("Origin", ""))
        if origin is not None:
            return origin == request_origin

        referer = WebApp.__parse_origin(bottle.request.get_header("Referer", ""))
        if referer is not None:
            return referer == request_origin

        return False

    def __is_same_origin_browser_request(self) -> bool:
        if (
            self.__is_loopback_remote_addr() and
            WebApp.__has_proxy_forwarding_headers() and
            not self.__is_trusted_browser_bootstrap_remote_addr()
        ):
            return False

        request_origin = self.__effective_request_origin()
        if request_origin is None:
            return False

        sec_fetch_site = bottle.request.get_header("Sec-Fetch-Site", "")
        if isinstance(sec_fetch_site, str) and sec_fetch_site.strip():
            sec_fetch_site = sec_fetch_site.strip().lower()
            if sec_fetch_site not in {"same-origin", "none"}:
                return False

        origin = WebApp.__parse_origin(bottle.request.get_header("Origin", ""))
        if origin is not None:
            return origin == request_origin

        referer = WebApp.__parse_origin(bottle.request.get_header("Referer", ""))
        if referer is not None:
            return referer == request_origin

        return False

    @staticmethod
    def __request_origin() -> Optional[Tuple[str, str, int]]:
        raw_url = bottle.request.url
        if not isinstance(raw_url, str) or raw_url.strip() == "":
            return None
        return WebApp.__parse_origin(raw_url)

    @staticmethod
    def __parse_origin(raw_value: str) -> Optional[Tuple[str, str, int]]:
        if not isinstance(raw_value, str) or raw_value.strip() == "":
            return None

        parsed = urlparse(raw_value.strip())
        if not parsed.scheme or not parsed.hostname:
            return None

        scheme = parsed.scheme.strip().lower()
        if not scheme:
            return None

        try:
            port = parsed.port
        except ValueError:
            return None

        if port is None:
            if scheme == "http":
                port = 80
            elif scheme == "https":
                port = 443
            else:
                return None

        return scheme, WebApp.__normalize_hostname(parsed.hostname), port

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
    def __parse_trusted_browser_bootstrap_networks(raw_value: str):
        networks = []
        invalid_entries = []

        for candidate in raw_value.split(","):
            candidate = candidate.strip()
            if not candidate:
                continue

            try:
                networks.append(ipaddress.ip_network(candidate, strict=False))
            except ValueError:
                invalid_entries.append(candidate)

        return tuple(networks), tuple(invalid_entries)

    def __allow_first_admin_bootstrap(self) -> bool:
        if self.__auth_store is None:
            return False

        can_claim = getattr(self.__auth_store, "can_claim_initial_admin", None)
        if can_claim is None:
            return False
        if not can_claim(self.__get_browser_handover_version()):
            return False

        return (
            self.__is_trusted_browser_bootstrap_request() and
            self.__is_same_origin_browser_request()
        )

    def __allow_browser_api_key_entry(self) -> bool:
        return (
            self.__is_trusted_browser_bootstrap_request() and
            self.__is_same_origin_browser_request()
        )

    def __allow_sessionless_ui_route(self) -> bool:
        return (
            self.__auth_store is not None and
            getattr(self.__auth_store, "active_admin_key_count", 0) == 0 and
            self.__is_loopback_remote_addr() and
            not WebApp.__has_proxy_forwarding_headers() and
            WebApp.__is_loopback_host(WebApp.__request_host()) and
            self.__is_direct_same_origin_browser_request()
        )

    def __is_bootstrap_safe_static_asset_request(self) -> bool:
        request_path = bottle.request.environ.get("seedsync.raw_path") or bottle.request.path
        return request_path in self._BOOTSTRAP_SAFE_STATIC_ASSET_PATHS

    def __allow_bootstrap_proof_exchange(self) -> bool:
        if (
            self.__auth_store is None or
            getattr(self.__auth_store, "active_admin_key_count", 0) != 0 or
            not WebApp.__is_loopback_host(WebApp.__request_host()) or
            not self.__is_same_origin_browser_request() or
            not self.__is_trusted_browser_bootstrap_request()
        ):
            return False

        exchange_secret = bottle.request.get_cookie(self._BOOTSTRAP_EXCHANGE_COOKIE_NAME)
        if not isinstance(exchange_secret, str) or not exchange_secret.strip():
            return False

        peek_exchange = getattr(self.__auth_store, "peek_bootstrap_exchange", None)
        if peek_exchange is None:
            return False

        return bool(peek_exchange(exchange_secret.strip()))

    def __load_trusted_browser_bootstrap_networks(self):
        configured_value = WebApp.__get_trusted_browser_bootstrap_remote_addrs(self.__config)
        networks, invalid_entries = WebApp.__parse_trusted_browser_bootstrap_networks(configured_value)
        for invalid_entry in invalid_entries:
            self.logger.warning(
                "Ignoring invalid General.trusted_browser_bootstrap_remote_addrs entry '%s'",
                invalid_entry
            )
        return networks

    def __is_trusted_browser_bootstrap_remote_addr(self) -> bool:
        remote_addr = WebApp.__request_remote_addr()
        if not remote_addr:
            return False

        try:
            remote_ip = ipaddress.ip_address(remote_addr)
        except ValueError:
            return False

        for trusted_network in self.__trusted_browser_bootstrap_networks:
            if remote_ip in trusted_network:
                return True
        return False

    def __is_trusted_browser_bootstrap_request(self) -> bool:
        if WebApp.__is_loopback_remote_addr():
            return WebApp.__is_loopback_host(WebApp.__request_host())

        if not self.__is_trusted_browser_bootstrap_remote_addr():
            return False

        effective_origin = self.__effective_request_origin()
        if effective_origin is None:
            return False

        _, host, _ = effective_origin
        return WebApp.__is_loopback_host(host)

    def __authorize_server_route(
        self,
        required_scope: str,
        allow_sessionless_ui: bool,
        allow_first_admin_bootstrap: bool,
        allow_bootstrap_proof_exchange: bool,
        allow_browser_api_key_entry: bool
    ) -> None:
        token = WebApp.__extract_bearer_token()
        if token is None:
            if not WebApp.__has_bearer_authorization_header():
                if allow_bootstrap_proof_exchange and self.__allow_bootstrap_proof_exchange():
                    return
                if allow_first_admin_bootstrap and self.__allow_first_admin_bootstrap():
                    return
                current_session = self.__get_ui_session()
                if self.__is_browser_handover_open() and current_session is not None:
                    if not getattr(current_session, "bootstrap", False):
                        bottle.redirect("/bootstrap")
                if self.__is_trusted_forwarded_origin_source():
                    ui_session_scopes = self.__get_ui_session_scopes()
                    if ui_session_scopes is not None:
                        if required_scope in {"write", "admin"} and not self.__is_same_origin_browser_request():
                            bottle.abort(403, "Browser-origin signal required for cookie-authenticated write requests")
                        self.__authorize_scopes(required_scope, ui_session_scopes)
                        return
                if allow_sessionless_ui and self.__allow_sessionless_ui_route():
                    return
                if allow_browser_api_key_entry and self.__allow_browser_api_key_entry():
                    return
            bottle.abort(401, "Missing API token")

        auth_record = None
        if self.__auth_store is not None:
            auth_store: Any = self.__auth_store
            auth_record = auth_store.find_api_key_by_secret(token)

        if auth_record is not None:
            if getattr(auth_record, "revoked_at", None) is not None:
                bottle.abort(403, "API key has been revoked")

            self.__authorize_scopes(
                required_scope,
                auth_record.scopes,
                forbidden_message="API key '{}' lacks scope '{}'".format(auth_record.id, required_scope)
            )
            return

        bottle.abort(401, "Invalid API token")

    def __get_ui_session_scopes(self):
        session = self.__get_ui_session()
        if session is None:
            return None

        if getattr(session, "api_key_id", None):
            resolved_api_key = getattr(self.__auth_store, "resolve_ui_session_api_key", None)
            if resolved_api_key is None:
                return None

            auth_record = resolved_api_key(session)
            if auth_record is None:
                return None

            return list(getattr(auth_record, "scopes", []) or [])

        if getattr(session, "bootstrap", False):
            return getattr(session, "scopes", None)

        return None

    def __get_ui_session(self):
        if self.__auth_store is None:
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

        if getattr(session, "api_key_id", None):
            resolve_api_key = getattr(self.__auth_store, "resolve_ui_session_api_key", None)
            if resolve_api_key is None:
                return None
            if resolve_api_key(session) is None:
                invalidate_session = getattr(self.__auth_store, "invalidate_ui_session", None)
                if invalidate_session is not None:
                    invalidate_session(ui_session_secret)
                return None
            return session

        if getattr(session, "bootstrap", False):
            return session

        invalidate_session = getattr(self.__auth_store, "invalidate_ui_session", None)
        if invalidate_session is not None:
            invalidate_session(ui_session_secret)

        return None

    def __authorize_scopes(self, required_scope: str, scopes, forbidden_message: Optional[str] = None) -> None:
        allowed_scopes = set(scopes or [])
        if "admin" in allowed_scopes:
            allowed_scopes.update(WebApp._AUTH_SCOPES)
        if required_scope not in allowed_scopes:
            bottle.abort(403, forbidden_message or "Session lacks scope '{}'".format(required_scope))

    def __bootstrap(self):
        if self.__auth_store is not None:
            if not self.__is_trusted_browser_bootstrap_request():
                bottle.abort(
                    403,
                    "Bootstrap access is limited to loopback or explicit trusted local runtime sources"
                )

        current_session = self.__get_ui_session()
        if getattr(current_session, "api_key_id", None) and not self.__is_browser_handover_open():
            bottle.redirect("/")

        browser_handover_state = {
            "configured_version": "",
            "claimed_version": "",
            "open": False,
        }
        if self.__auth_store is not None:
            auth_store: Any = self.__auth_store
            browser_handover_state = auth_store.get_browser_handover_state(self.__config)

        page_title = "SeedSync browser access"
        can_claim_initial_admin = bool(browser_handover_state.get("open", False))
        form_fields_html = """
    <label for="browser-secret">API key secret</label>
    <input id="browser-secret" type="password" autocomplete="off" placeholder="Paste an API key secret">
"""

        if can_claim_initial_admin:
            eyebrow_label = "First-run browser access"
            page_heading = "Claim the first local session"
            page_description = (
                "This trusted browser can take SeedSync's initial admin handoff and keep the setup inside the local runtime. "
                "After that, any other browser will need an API key once to become remembered."
            )
            brand_copy = (
                "SeedSync uses this page to hand the browser session back to the app. "
                "When the handoff is open, this first local browser can step in and continue straight into the UI."
            )
            primary_action_label = "Claim session"
            primary_action_url = "/server/admin/bootstrap/v1/first-api-key"
            primary_action_payload = ""
            form_fields_html = ""
            form_variant_class = "form-panel form-panel-initial"
        else:
            eyebrow_label = "Remembered browser"
            page_heading = "Save this browser for next time"
            page_description = (
                "Enter one API key once so SeedSync can recognize this browser and return to the same scoped access on the next visit."
            )
            brand_copy = (
                "SeedSync keeps browser access tied to the local runtime. "
                "One API-key entry is enough to remember this browser, so the app opens faster next time without another prompt."
            )
            primary_action_label = "Remember browser"
            primary_action_url = "/server/browser/v1/remember"
            primary_action_payload = 'secret: document.getElementById("browser-secret").value'
            form_variant_class = "form-panel"

        bottle.response.content_type = "text/html; charset=utf-8"
        bottle.response.cache_control = "no-store"  # type: ignore[assignment]
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <style>
    :root {{
      color-scheme: light;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      min-height: 100vh;
      background: #f5f5f5;
      color: #1f2937;
    }}
    .bootstrap-page {{
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 1rem;
    }}
    main {{
      width: min(68rem, calc(100vw - 2rem));
      display: grid;
      grid-template-columns: minmax(16rem, 0.95fr) minmax(20rem, 1.05fr);
      background: #ffffff;
      border: 1px solid rgba(17, 130, 71, 0.14);
      border-top: 4px solid #118247;
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 16px 40px rgba(15, 23, 42, 0.08);
    }}
    .brand-panel {{
      padding: 1.5rem;
      background: linear-gradient(180deg, #fbfdfb 0%, #f4f9f5 100%);
      border-right: 1px solid rgba(31, 41, 55, 0.08);
    }}
    .brand-lockup {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
    }}
    .brand-lockup img {{
      width: auto;
      height: auto;
      max-width: 80px;
      max-height: 80px;
      flex: 0 0 auto;
    }}
    .brand-wordmark {{
      color: #118247;
      font-family: "Arial Black", Gadget, sans-serif;
      font-size: clamp(2rem, 3vw, 2.6rem);
      line-height: 1;
      letter-spacing: -0.04em;
      user-select: none;
      white-space: nowrap;
    }}
    .brand-kicker {{
      display: inline-flex;
      margin-top: 1rem;
      padding: 0.32rem 0.65rem;
      border-radius: 999px;
      background: rgba(17, 130, 71, 0.08);
      color: #0d6b39;
      font-size: 0.76rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .brand-message {{
      margin-top: 1rem;
      padding: 1rem 1rem 0.95rem;
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid rgba(17, 130, 71, 0.12);
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.05);
      text-align: left;
    }}
    .brand-story-copy {{
      display: grid;
      gap: 0.85rem;
    }}
    .brand-copy {{
      margin: 0;
      line-height: 1.55;
      color: rgba(31, 41, 55, 0.78);
      max-width: 34rem;
    }}
    .brand-flow {{
      display: flex;
      align-items: center;
      flex-wrap: nowrap;
      gap: 0.32rem;
      color: #0d6b39;
      font-size: 0.76rem;
      font-weight: 700;
      letter-spacing: 0.01em;
    }}
    .brand-flow-item {{
      display: inline-flex;
      align-items: center;
      gap: 0.32rem;
      padding: 0.3rem 0.48rem;
      border-radius: 999px;
      background: rgba(17, 130, 71, 0.08);
      border: 1px solid rgba(17, 130, 71, 0.11);
      white-space: nowrap;
    }}
    .brand-flow-mark {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 1rem;
      height: 1rem;
      border-radius: 999px;
      border: 1px solid rgba(17, 130, 71, 0.18);
      background: rgba(255, 255, 255, 0.82);
      color: #118247;
      font-size: 0.68rem;
      line-height: 1;
      flex: 0 0 auto;
    }}
    .form-panel {{
      padding: 1.5rem;
    }}
    .form-panel-initial form {{
      margin-top: 1.25rem;
      max-width: 34rem;
    }}
    .form-panel-initial button {{
      width: min(100%, 15rem);
      min-height: 46px;
      margin-top: 0;
      padding: 0.45rem 0.55rem;
      font-size: 1.14rem;
      line-height: 1.05;
      font-weight: 700;
    }}
    .form-panel-initial .hint {{
      margin-top: 1.5rem;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      padding: 0.32rem 0.65rem;
      border-radius: 999px;
      background: rgba(51, 123, 183, 0.09);
      color: #2e6da4;
      font-size: 0.76rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    h1 {{
      margin: 0.85rem 0 0.45rem;
      font-size: clamp(1.8rem, 3vw, 2.35rem);
      line-height: 1.08;
      letter-spacing: -0.03em;
    }}
    .lede {{
      margin: 0;
      max-width: 34rem;
      line-height: 1.6;
      color: rgba(31, 41, 55, 0.78);
    }}
    form {{
      margin-top: 1.25rem;
      max-width: 30rem;
    }}
    label {{
      display: block;
      font-weight: 700;
      margin: 1rem 0 0.35rem;
      color: #1f2937;
    }}
    input {{
      width: 100%;
      padding: 0.8rem 0.9rem;
      border-radius: 8px;
      border: 1px solid rgba(31, 41, 55, 0.18);
      background: #ffffff;
      font: inherit;
      box-sizing: border-box;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }}
    input:focus {{
      outline: none;
      border-color: #337BB7;
      box-shadow: 0 0 0 3px rgba(51, 123, 183, 0.14);
    }}
    button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: fit-content;
      margin: 1rem auto 0;
      min-height: 32px;
      min-width: 8.5rem;
      padding: 0.44rem 1rem;
      border-radius: 4px;
      border: 1px solid #2e6da4;
      background: #337BB7;
      color: white;
      font: inherit;
      font-size: 0.95rem;
      font-weight: 600;
      line-height: 1.2;
      text-align: center;
      appearance: none;
      cursor: pointer;
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.45),
        0 1px 2px rgba(15, 23, 42, 0.08);
      transition: background-color 0.15s ease, border-color 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease;
    }}
    button:hover {{
      background: #2e6da4;
      border-color: #245580;
    }}
    button:active {{
      background: #286090;
      border-color: #204d74;
      box-shadow:
        inset 0 2px 5px rgba(0, 0, 0, 0.18),
        0 1px 1px rgba(15, 23, 42, 0.06);
      transform: translateY(1px) scale(0.99);
    }}
    button:focus-visible {{
      outline: 3px solid rgba(51, 123, 183, 0.28);
      outline-offset: 2px;
    }}
    .hint {{
      margin-top: 0.75rem;
      font-size: 0.9rem;
      color: rgba(31, 41, 55, 0.62);
      line-height: 1.5;
      max-width: 34rem;
    }}
    .error {{
      margin-top: 0.75rem;
      color: #b91c1c;
      white-space: pre-wrap;
    }}
    @media (max-width: 760px) {{
      .bootstrap-page {{
        padding: 0.75rem;
      }}
      main {{
        width: min(100%, 52rem);
        grid-template-columns: 1fr;
      }}
      .brand-panel {{
        border-right: 0;
        border-bottom: 1px solid rgba(31, 41, 55, 0.08);
      }}
      form {{
        max-width: none;
      }}
      button {{
        width: 100%;
      }}
    }}
    @media (max-width: 420px) {{
      .brand-panel {{
        padding: 1.25rem;
      }}
      .brand-lockup {{
        flex-direction: column;
        gap: 0.35rem;
        text-align: center;
      }}
      .brand-lockup img {{
        max-width: 64px;
        max-height: 64px;
      }}
      .brand-wordmark {{
        font-size: clamp(1.65rem, 12vw, 2rem);
        line-height: 0.98;
        white-space: normal;
      }}
      .brand-flow {{
        flex-wrap: wrap;
      }}
    }}
  </style>
</head>
<body class="bootstrap-page">
<main>
  <section class="brand-panel" aria-label="SeedSync branding">
    <div class="brand-lockup">
      <img src="/assets/logo.png" alt="SeedSync" />
      <div class="brand-wordmark"><b>Seed</b>Sync</div>
    </div>
    <div class="brand-kicker">Browser access</div>
    <div class="brand-message">
      <div class="brand-story">
        <div class="brand-story-copy">
          <p class="brand-copy">{brand_copy}</p>
          <div class="brand-flow" aria-hidden="true">
            <span class="brand-flow-item"><span class="brand-flow-mark">&#8962;</span>local handoff</span>
            <span class="brand-flow-item"><span class="brand-flow-mark">&#9675;</span>one-time key</span>
            <span class="brand-flow-item"><span class="brand-flow-mark">&#10132;</span>open SeedSync</span>
          </div>
        </div>
      </div>
    </div>
  </section>
  <section class="{form_variant_class}" aria-label="Bootstrap form">
  <div class="eyebrow">{eyebrow_label}</div>
  <h1>{page_heading}</h1>
  <p class="lede">{page_description}</p>
  <form id="bootstrap-form">
{form_fields_html}
    <button type="submit">{primary_action_label}</button>
  </form>
  <p class="hint">This page stays local and same-origin. It only sends the entered credential to this SeedSync instance.</p>
  <p id="error" class="error" hidden></p>
  </section>
</main>
<script>
(function () {{
  const form = document.getElementById("bootstrap-form");
  const error = document.getElementById("error");
  let submitting = false;

  function submitBootstrapRequest(event) {{
    if (event) {{
      event.preventDefault();
    }}
    if (submitting) {{
      return;
    }}
    submitting = true;
    error.hidden = true;
    error.textContent = "";
    const payload = {{ {primary_action_payload} }};
    fetch("{primary_action_url}", {{
      method: "POST",
      credentials: "same-origin",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(payload),
    }}).then(function (response) {{
      if (!response.ok) {{
        return response.text().then(function (text) {{
          throw new Error(text || "Request failed");
        }});
      }}
      window.location.replace("/");
    }}).catch(function (err) {{
      submitting = false;
      error.hidden = false;
      error.textContent = err.message || String(err);
    }});
  }}

  form.addEventListener("submit", submitBootstrapRequest);
}})();
</script>
</body>
</html>
""".format(
            page_title=page_title,
            eyebrow_label=eyebrow_label,
            form_variant_class=form_variant_class,
            page_heading=page_heading,
            page_description=page_description,
            brand_copy=brand_copy,
            form_fields_html=form_fields_html,
            primary_action_label=primary_action_label,
            primary_action_url=primary_action_url,
            primary_action_payload=primary_action_payload,
        )

    def __authorize_browser_bootstrap(self) -> None:
        if self.__auth_store is None:
            return

        if self.__is_browser_handover_open():
            if self.__is_bootstrap_safe_static_asset_request():
                return
            current_session = self.__get_ui_session()
            if (
                (current_session is None or not getattr(current_session, "bootstrap", False)) and
                self.__is_trusted_browser_bootstrap_request()
            ):
                bottle.redirect("/bootstrap")

        if self.__get_ui_session() is not None and self.__is_trusted_browser_bootstrap_request():
            return

        if self.__is_loopback_remote_addr() and WebApp.__is_loopback_host(WebApp.__request_host()):
            return

        if self.__is_trusted_browser_bootstrap_request():
            return

        if getattr(self.__auth_store, "active_admin_key_count", 0) == 0:
            bottle.abort(
                403,
                "First-admin browser bootstrap requires direct loopback access or an approved local browser request."
            )

        bottle.abort(403, "Browser shell and static asset access is limited to loopback or explicit trusted local runtime sources")

    def __index(self):
        """
        Serves the index.html static file
        :return:
        """
        if self.__auth_store is not None:
            self.__authorize_browser_bootstrap()
            current_session = self.__get_ui_session()
            if current_session is None:
                bottle.redirect("/bootstrap")
            if (
                getattr(current_session, "api_key_id", None) is None and
                not getattr(current_session, "bootstrap", False)
            ):
                bottle.redirect("/bootstrap")

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
        self.__authorize_browser_bootstrap()
        response = static_file(file_path, root=self.__html_path)
        response.set_header("Content-Security-Policy", self._CONTENT_SECURITY_POLICY)
        return response

    def __web_stream(self):
        # Initialize all the handlers
        handlers = [cls(**kwargs) for (cls, kwargs) in self.__streaming_handlers]

        try:
            # Setup the response header
            bottle.response.content_type = "text/event-stream"
            bottle.response.cache_control = "no-cache"  # type: ignore[assignment]

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
