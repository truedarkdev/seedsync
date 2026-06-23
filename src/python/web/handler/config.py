# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import logging
import threading

import bottle
from bottle import HTTPResponse

from common import overrides, Config, ConfigError
from ..web_app import IHandler, WebApp
from ..serialize import SerializeConfig

logger = logging.getLogger(__name__)


class ConfigHandler(IHandler):
    __BODY_SET_BLOCKED_FIELDS = {
        "general": {
            "api_token",
            "config_api_redact_remote_details",
            "trusted_browser_bootstrap_remote_addrs",
        },
    }

    def __init__(self, config: Config, breadcrumb_trace_sync=None):
        self.__config = config
        self.__breadcrumb_trace_sync = breadcrumb_trace_sync
        self.__write_lock = threading.Lock()

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp):
        web_app.add_handler(
            "/server/config/get",
            self.__handle_get_config,
            required_scope="read"
        )
        web_app.add_post_handler(
            "/server/config/set/<section>/<key>",
            self.__handle_set_config,
            required_scope="write"
        )

    def __handle_get_config(self):
        out_json = SerializeConfig.config(self.__config)
        return HTTPResponse(body=out_json)

    @staticmethod
    def __load_request_json():
        raw_body = bottle.request.body.read().decode("utf-8")
        if not raw_body.strip():
            raise ValueError("Missing config value")
        return json.loads(raw_body)

    @staticmethod
    def __read_current_value(inner_config, section: str, key: str):
        if section == "general" and key == "debug":
            return getattr(inner_config, "log_level", None) == "DEBUG"
        return getattr(inner_config, key)

    @staticmethod
    def __restore_previous_value(inner_config, section: str, key: str, value):
        if section == "general" and key == "debug":
            inner_config.set_property("debug", value)
            return
        inner_config.set_property(key, value)

    def __handle_set_config(self, section: str, key: str, value=None):
        if value is None:
            try:
                data = ConfigHandler.__load_request_json()
            except (TypeError, ValueError) as exc:
                return HTTPResponse(body=str(exc), status=400)
            if not isinstance(data, dict) or "value" not in data:
                return HTTPResponse(body="Missing config value", status=400)
            value = data["value"]

        if not self.__config.has_section(section):
            return HTTPResponse(body="There is no section '{}' in config".format(section), status=404)
        inner_config = getattr(self.__config, section)
        if not inner_config.has_property(key):
            return HTTPResponse(body="Section '{}' in config has no option '{}'".format(section, key), status=404)
        if Config.is_sensitive_field(section, key) and Config.is_redacted_value(value):
            return HTTPResponse(
                body="Section '{}' option '{}' cannot be set to redacted value".format(section, key),
                status=400
            )
        if (
            section in ConfigHandler.__BODY_SET_BLOCKED_FIELDS and
            key in ConfigHandler.__BODY_SET_BLOCKED_FIELDS[section]
        ):
            return HTTPResponse(
                body="Section '{}' option '{}' cannot be set via request body".format(section, key),
                status=403
            )
        with self.__write_lock:
            old_value = ConfigHandler.__read_current_value(inner_config, section, key)
            try:
                inner_config.set_property(key, value)
                self.__config.to_file()
            except ConfigError as e:
                return HTTPResponse(body=str(e), status=400)
            except Exception:
                ConfigHandler.__restore_previous_value(inner_config, section, key, old_value)
                logger.exception("Failed to persist config %s.%s", section, key)
                return HTTPResponse(body="Failed to persist config {}.{}".format(section, key), status=500)
        if self.__breadcrumb_trace_sync is not None and section == "general" and key == "breadcrumb_trace_enabled":
            self.__breadcrumb_trace_sync()
        if Config.is_sensitive_field(section, key):
            response_value = Config.REDACTED_SENTINEL
        elif isinstance(getattr(type(inner_config), key, None), property):
            response_value = getattr(inner_config, key)
        else:
            response_value = value
        return HTTPResponse(body="{}.{} set to {}".format(section, key, response_value))
