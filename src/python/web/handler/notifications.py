import json
import logging
from typing import TypeGuard

import bottle
from bottle import HTTPResponse

from common import Config, ConfigError, overrides
from controller.notifier import (
    NotificationError, NotificationService, validate_apprise_url, validate_webhook_url,
)
from ..web_app import IHandler, WebApp


logger = logging.getLogger(__name__)


def _is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


class NotificationsAdminHandler(IHandler):
    _BOOLEAN_FIELDS = (
        "enabled", "allow_private_networks", "download_start", "download_complete",
        "extraction_complete", "delete_complete",
    )
    _TEXT_FIELDS = ("provider", "apprise_tag")
    _WRITE_ONLY_FIELDS = ("webhook_url", "hmac_secret", "apprise_url")
    _ALLOWED_FIELDS = frozenset(_BOOLEAN_FIELDS + _TEXT_FIELDS + _WRITE_ONLY_FIELDS)

    def __init__(self, config: Config, notifier: NotificationService) -> None:
        self._config = config
        self._notifier = notifier

    @staticmethod
    def _json_response(payload: object, status: int = 200) -> HTTPResponse:
        return HTTPResponse(body=json.dumps(payload), status=status,
                            headers={"Content-Type": "application/json"})

    @staticmethod
    def _load_json() -> dict[str, object]:
        raw = bottle.request.body.read().decode("utf-8")
        value: object = json.loads(raw) if raw.strip() else {}
        if not _is_object_dict(value):
            raise ValueError("Request body must be a JSON object")
        return value

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp) -> None:
        web_app.add_post_handler("/server/admin/notifications/v1/config", self._handle_update,
                                 required_scope="admin")
        web_app.add_post_handler("/server/admin/notifications/v1/test", self._handle_test,
                                 required_scope="admin")

    def _handle_update(self) -> HTTPResponse:
        try:
            data = self._load_json()
            if set(data).difference(self._ALLOWED_FIELDS):
                raise ValueError("Unknown notification setting")
            section = self._config.notifications
            with self._config.write_lock:
                previous = section.as_dict()
                try:
                    for field in self._BOOLEAN_FIELDS:
                        if field in data:
                            section.set_property(field, data[field])
                    for field in self._TEXT_FIELDS:
                        if field in data:
                            value = data[field]
                            if not isinstance(value, str):
                                raise ValueError("Notification text settings must be strings")
                            section.set_property(field, value.strip())
                    for field in self._WRITE_ONLY_FIELDS:
                        if field in data:
                            value = data[field]
                            if Config.is_redacted_value(value):
                                raise ValueError("Redacted values cannot be saved")
                            if not isinstance(value, str):
                                raise ValueError("Sensitive notification settings must be strings")
                            section.set_property(field, value.strip())
                    selected_url = section.webhook_url if section.provider == "webhook" else section.apprise_url
                    if section.enabled and not selected_url:
                        raise ValueError("Selected provider URL is required when notifications are enabled")
                    if "webhook_url" in data and section.webhook_url:
                        validate_webhook_url(section.webhook_url, section.allow_private_networks)
                    if "apprise_url" in data and section.apprise_url:
                        validate_apprise_url(section.apprise_url, section.allow_private_networks)
                    self._notifier.reconfigure(self._config)
                    self._config.to_file()
                except Exception:
                    for field, value in previous.items():
                        section.set_property(field, value)
                    try:
                        self._notifier.reconfigure(self._config)
                    except Exception:
                        logger.error("Failed to restore prior notification runtime configuration")
                    raise
            return self._json_response({"notifications": self._notifier.public_config()})
        except (ConfigError, NotificationError, TypeError, ValueError) as exc:
            return self._json_response({"error": str(exc)}, status=400)
        except Exception:
            logger.exception("Failed to update notification configuration")
            return self._json_response({"error": "Failed to update notification configuration"}, status=500)

    def _handle_test(self) -> HTTPResponse:
        try:
            self._notifier.test_delivery()
            return self._json_response({"delivered": True})
        except Exception:
            logger.warning("Notification test delivery failed", exc_info=False)
            return self._json_response({"error": "Notification test delivery failed"}, status=502)
