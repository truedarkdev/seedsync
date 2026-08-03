# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import logging
import threading
from collections.abc import Callable
from typing import Protocol, TypeGuard, runtime_checkable

import bottle
from bottle import HTTPResponse

from common import overrides, Config, ConfigError, Localization
from ..web_app import IHandler, WebApp
from ..serialize import SerializeConfig
from ..config_restart import (
    GENERAL_RUNTIME_RECONFIGURE_FIELDS,
    LFTP_RUNTIME_RECONFIGURE_FIELDS,
    VALIDATE_RUNTIME_RECONFIGURE_FIELDS,
)

logger = logging.getLogger(__name__)


def _is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


@runtime_checkable
class _ConfigSection(Protocol):
    def has_property(self, name: str) -> bool: ...
    def set_property(self, name: str, value: object) -> None: ...
    def as_dict(self) -> dict[str, object]: ...


def _is_config_section(value: object) -> TypeGuard[_ConfigSection]:
    return all(
        callable(getattr(value, method_name, None))
        for method_name in ("has_property", "set_property", "as_dict")
    )


class ConfigHandler(IHandler):
    __BODY_SET_BLOCKED_FIELDS = {
        "general": {
            "api_token",
            "config_api_redact_remote_details",
            "disable_browser_auth",
        },
        "notifications": {
            "enabled", "provider", "webhook_url", "hmac_secret", "apprise_url", "apprise_tag",
            "allow_private_networks",
            "download_start", "download_complete", "extraction_complete", "delete_complete",
        },
        "lftp": {
            "use_legacy_lftp_password_argv",
        },
    }
    def __init__(
        self,
        config: Config,
        breadcrumb_trace_sync: Callable[[], object] | None = None,
        lftp_reconfigure_request: Callable[[], object] | None = None,
    ) -> None:
        self.__config = config
        self.__breadcrumb_trace_sync = breadcrumb_trace_sync
        self.__lftp_reconfigure_request = lftp_reconfigure_request
        self.__write_lock = getattr(config, "write_lock", threading.Lock())

    @staticmethod
    def __is_blank_text(value: object) -> bool:
        return value is None or (isinstance(value, str) and value.strip() == "")

    @staticmethod
    def __normalize_transfer_protocol_for_guard(value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @staticmethod
    def __normalize_transfer_backend_for_guard(value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @staticmethod
    def __normalize_lftp_backend_constraints(inner_config: _ConfigSection) -> None:
        if getattr(inner_config, "transfer_backend", "lftp") == "rclone":
            inner_config.set_property("protocol", "sftp")

    @staticmethod
    def __would_create_blank_ftps_password(
        inner_config: _ConfigSection, section: str, key: str, value: object
    ) -> bool:
        if section != "lftp":
            return False

        transfer_backend = getattr(inner_config, "transfer_backend", "lftp")
        protocol = getattr(inner_config, "protocol", None)
        remote_password = getattr(inner_config, "remote_password", None)
        if key == "transfer_backend":
            transfer_backend = value
        if key == "protocol":
            protocol = value
        elif key == "remote_password":
            remote_password = value

        transfer_backend = ConfigHandler.__normalize_transfer_backend_for_guard(transfer_backend)
        protocol = ConfigHandler.__normalize_transfer_protocol_for_guard(protocol)
        return transfer_backend == "lftp" and protocol == "ftps" and ConfigHandler.__is_blank_text(remote_password)

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp) -> None:
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

    def __handle_get_config(self) -> HTTPResponse:
        out_json = SerializeConfig.config(self.__config)
        return HTTPResponse(body=out_json)

    @staticmethod
    def __load_request_json() -> object:
        raw_body = bottle.request.body.read().decode("utf-8")
        if not raw_body.strip():
            raise ValueError("Missing config value")
        return json.loads(raw_body)

    @staticmethod
    def __read_current_value(inner_config: _ConfigSection, section: str, key: str) -> object:
        if section == "general" and key == "debug":
            return getattr(inner_config, "log_level", None) == "DEBUG"
        return getattr(inner_config, key)

    @staticmethod
    def __restore_previous_value(
        inner_config: _ConfigSection, section: str, key: str, value: object
    ) -> None:
        if section == "general" and key == "debug":
            inner_config.set_property("debug", value)
            return
        inner_config.set_property(key, value)

    @staticmethod
    def __restore_previous_snapshot(
        inner_config: _ConfigSection, snapshot: dict[str, object]
    ) -> None:
        for snapshot_key, snapshot_value in snapshot.items():
            inner_config.set_property(snapshot_key, snapshot_value)

    def __handle_set_config(
        self, section: str, key: str, value: object = None
    ) -> HTTPResponse:
        if value is None:
            try:
                data = ConfigHandler.__load_request_json()
            except (TypeError, ValueError) as exc:
                return HTTPResponse(body=str(exc), status=400)
            if not _is_object_dict(data) or "value" not in data:
                return HTTPResponse(body="Missing config value", status=400)
            value = data["value"]

        if not self.__config.has_section(section):
            return HTTPResponse(body="There is no section '{}' in config".format(section), status=404)
        inner_config_value: object = getattr(self.__config, section)
        if not _is_config_section(inner_config_value):
            return HTTPResponse(body="Invalid config section", status=500)
        inner_config = inner_config_value
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
        if ConfigHandler.__would_create_blank_ftps_password(inner_config, section, key, value):
            return HTTPResponse(
                body=Localization.Error.FTPS_TRANSFER_PASSWORD_REQUIRED,
                status=400
            )
        with self.__write_lock:
            old_value = ConfigHandler.__read_current_value(inner_config, section, key)
            snapshot_value: object = inner_config.as_dict()
            old_snapshot = snapshot_value if _is_object_dict(snapshot_value) else None
            try:
                inner_config.set_property(key, value)
                if section == "lftp":
                    ConfigHandler.__normalize_lftp_backend_constraints(inner_config)
                self.__config.to_file()
            except ConfigError as e:
                if old_snapshot is not None:
                    ConfigHandler.__restore_previous_snapshot(inner_config, old_snapshot)
                return HTTPResponse(body=str(e), status=400)
            except Exception:
                if old_snapshot is not None:
                    ConfigHandler.__restore_previous_snapshot(inner_config, old_snapshot)
                else:
                    ConfigHandler.__restore_previous_value(inner_config, section, key, old_value)
                logger.exception("Failed to persist config %s.%s", section, key)
                return HTTPResponse(body="Failed to persist config {}.{}".format(section, key), status=500)
        if self.__breadcrumb_trace_sync is not None and section == "general" and key == "breadcrumb_trace_enabled":
            self.__breadcrumb_trace_sync()
        if (
            self.__lftp_reconfigure_request is not None
            and (
                (section == "lftp" and key in LFTP_RUNTIME_RECONFIGURE_FIELDS)
                or (section == "general" and key in GENERAL_RUNTIME_RECONFIGURE_FIELDS)
                or (section == "validate" and key in VALIDATE_RUNTIME_RECONFIGURE_FIELDS)
            )
        ):
            self.__lftp_reconfigure_request()
        if Config.is_sensitive_field(section, key):
            response_value = Config.REDACTED_SENTINEL
        elif isinstance(getattr(type(inner_config), key, None), property):
            response_value: object = getattr(inner_config, key)
        else:
            response_value = value
        return HTTPResponse(body="{}.{} set to {}".format(section, key, response_value))
