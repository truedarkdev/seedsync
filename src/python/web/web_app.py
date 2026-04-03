# Copyright 2017, Inderpreet Singh, All rights reserved.

import ipaddress
from typing import Type, Callable, Optional, Tuple
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
        self.route("/bootstrap")(self.__bootstrap)

        # Streaming route
        self.get(
            "/server/stream",
            required_scope="stream",
            allow_legacy_api_token=True,
            allow_sessionless_ui=True,
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
                "allow_legacy_api_token": allow_legacy_api_token,
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

        if not self.__is_trusted_forwarded_origin_source():
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
        request_origin = self.__effective_request_origin()
        if request_origin is None:
            return False

        sec_fetch_site = bottle.request.get_header("Sec-Fetch-Site", "")
        if isinstance(sec_fetch_site, str) and sec_fetch_site.strip():
            sec_fetch_site = sec_fetch_site.strip().lower()
            if sec_fetch_site not in {"same-origin", "none"}:
                return False
            if sec_fetch_site == "same-origin":
                return True

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
            WebApp.__is_loopback_host(WebApp.__request_host()) and
            self.__is_direct_same_origin_browser_request()
        )

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
        allow_legacy_api_token: bool,
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
        session = self.__get_ui_session()
        if session is None:
            return None

        resolved_api_key = getattr(self.__auth_store, "resolve_ui_session_api_key", None)
        if resolved_api_key is not None:
            auth_record = resolved_api_key(session)
            if auth_record is not None:
                return list(getattr(auth_record, "scopes", []) or [])
            if getattr(session, "api_key_id", None):
                return None

        return getattr(session, "scopes", None)

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
        if getattr(current_session, "api_key_id", None):
            bottle.redirect("/")

        browser_handover_state = {
            "configured_version": "",
            "claimed_version": "",
            "open": False,
        }
        if self.__auth_store is not None:
            browser_handover_state = self.__auth_store.get_browser_handover_state(self.__config)

        page_title = "SeedSync browser access"
        can_claim_initial_admin = bool(browser_handover_state.get("open", False))
        form_fields_html = """
    <label for="browser-secret">API key secret</label>
    <input id="browser-secret" type="password" autocomplete="off" placeholder="Paste an API key secret">
"""

        if can_claim_initial_admin:
            page_heading = "Finish browser setup"
            page_description = (
                "This first local browser can claim SeedSync's initial admin access. "
                "Click continue when you want this browser to take over."
            )
            brand_copy = "SeedSync keeps the first browser handoff local. Continue below to claim the initial admin session."
            primary_action_label = "Continue"
            primary_action_url = "/server/admin/bootstrap/v1/first-api-key"
            primary_action_payload = ""
            form_fields_html = ""
            brand_facts = [
                ("Mode", "Initial admin handover"),
                ("Scope", "Trusted local runtime only"),
                ("Result", "Claim the first browser session"),
            ]
        else:
            page_heading = "Remember this browser"
            page_description = "Enter an API key once to remember this browser and tie its access to that key's current scopes."
            brand_copy = "SeedSync keeps browser access tied to the local runtime. Paste an API key below once to remember this browser."
            primary_action_label = "Remember browser"
            primary_action_url = "/server/browser/v1/remember"
            primary_action_payload = 'secret: document.getElementById("browser-secret").value'
            brand_facts = [
                ("Mode", "Remembered browser"),
                ("Scope", "API-key-derived access"),
                ("Result", "Reuse this browser next time"),
            ]

        brand_facts_html = "\n".join(
            """
    <div class="fact">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
""".format(label=label, value=value)
            for label, value in brand_facts
        )

        bottle.response.content_type = "text/html; charset=utf-8"
        bottle.response.cache_control = "no-store"
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
      background: #fafafa;
      border-right: 1px solid rgba(31, 41, 55, 0.08);
    }}
    .brand-lockup {{
      display: flex;
      align-items: center;
      gap: 0.85rem;
    }}
    .brand-lockup img {{
      width: 58px;
      height: 58px;
      flex: 0 0 auto;
    }}
    .brand-wordmark {{
      color: #118247;
      font-family: "Arial Black", Gadget, sans-serif;
      font-size: 2rem;
      line-height: 0.95;
      letter-spacing: -0.04em;
      user-select: none;
    }}
    .brand-wordmark span {{
      display: block;
    }}
    .brand-kicker {{
      display: inline-flex;
      margin-top: 0.9rem;
      padding: 0.32rem 0.65rem;
      border-radius: 999px;
      background: rgba(17, 130, 71, 0.08);
      color: #0d6b39;
      font-size: 0.76rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .brand-copy {{
      margin: 1rem 0 0;
      line-height: 1.5;
      color: rgba(31, 41, 55, 0.78);
    }}
    .facts {{
      display: grid;
      gap: 0.75rem;
      margin-top: 1rem;
    }}
    .fact {{
      padding-top: 0.75rem;
      border-top: 1px solid rgba(31, 41, 55, 0.08);
    }}
    .fact dt {{
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: rgba(31, 41, 55, 0.52);
    }}
    .fact dd {{
      margin: 0.3rem 0 0;
      font-weight: 700;
      color: #1f2937;
    }}
    .form-panel {{
      padding: 1.5rem;
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
      margin-top: 1rem;
      min-height: 44px;
      padding: 0.8rem 1.15rem;
      border-radius: 8px;
      border: 1px solid #2e6da4;
      background: #337BB7;
      color: white;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12);
      transition: background-color 0.15s ease, border-color 0.15s ease, transform 0.15s ease;
    }}
    button:hover {{
      background: #2f72a9;
      border-color: #275f8e;
    }}
    button:active {{
      transform: translateY(1px);
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
  </style>
</head>
<body class="bootstrap-page">
<main>
  <section class="brand-panel" aria-label="SeedSync branding">
    <div class="brand-lockup">
      <img src="/assets/logo.png" alt="SeedSync" />
      <div class="brand-wordmark"><span>Seed</span>Sync</div>
    </div>
    <div class="brand-kicker">Browser access</div>
    <p class="brand-copy">{brand_copy}</p>
    <dl class="facts">
{brand_facts_html}
    </dl>
  </section>
  <section class="form-panel" aria-label="Bootstrap form">
  <div class="eyebrow">Bootstrap</div>
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
            page_heading=page_heading,
            page_description=page_description,
            brand_copy=brand_copy,
            form_fields_html=form_fields_html,
            brand_facts_html=brand_facts_html,
            primary_action_label=primary_action_label,
            primary_action_url=primary_action_url,
            primary_action_payload=primary_action_payload,
        )

    def __authorize_browser_bootstrap(self) -> None:
        if self.__auth_store is None:
            return

        if self.__get_ui_session() is not None:
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
