"""Provider-generic, non-blocking lifecycle notifications.

Payload schema v1 deliberately contains only a logical filename and optional
path-pair identifiers. Local paths, remote connection details, and config
secrets are never included.
"""

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import queue
import socket
import ssl
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

from common import Config, overrides
from model import IModelListener, ModelFile


logger = logging.getLogger(__name__)
_MAX_RESPONSE_BYTES = 4096
_DELIVERY_TIMEOUT_SECONDS = 5.0
_RETRY_BACKOFF_SECONDS = 0.2


class NotificationError(ValueError):
    """A safe, user-displayable notification configuration/delivery error."""


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool
    webhook_url: str
    hmac_secret: str
    allow_private_networks: bool
    download_complete: bool
    extraction_complete: bool
    delete_complete: bool
    provider: str = "webhook"
    apprise_url: str = ""
    apprise_tag: str = ""

    @classmethod
    def from_config(cls, config: Config) -> "NotificationSettings":
        section = getattr(config, "notifications", None) or Config.Notifications()
        return cls(**{name: getattr(section, name) for name in cls.__dataclass_fields__})


# Compatibility name retained for the initial webhook-only test/import surface.
WebhookSettings = NotificationSettings


@dataclass(frozen=True)
class NotificationEvent:
    event_type: str
    file_name: str
    path_pair_id: Optional[str]
    path_pair_name: Optional[str]
    event_id: str
    occurred_at: str

    @classmethod
    def create(cls, event_type: str, file: ModelFile) -> "NotificationEvent":
        return cls(
            event_type=event_type,
            file_name=file.name,
            path_pair_id=getattr(file, "path_pair_id", None),
            path_pair_name=getattr(file, "path_pair_name", None),
            event_id=str(uuid.uuid4()),
            occurred_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )

    def payload(self) -> dict:
        payload = {
            "schema_version": 1,
            "event_id": self.event_id,
            "timestamp": self.occurred_at,
            "event_type": self.event_type,
            "file": {"name": self.file_name},
        }
        if self.path_pair_id:
            payload["file"]["path_pair_id"] = self.path_pair_id
        if self.path_pair_name:
            payload["file"]["path_pair_name"] = self.path_pair_name
        return payload


class NotificationProvider(ABC):
    @abstractmethod
    def deliver(self, event: NotificationEvent, settings: NotificationSettings) -> None:
        pass


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, pinned_address: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_address = pinned_address

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, pinned_address: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = pinned_address

    def connect(self):
        raw_socket = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self.source_address
        )
        # The logical hostname (not the pinned IP) preserves SNI and hostname verification.
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))


def _is_explicit_private_address(address) -> bool:
    return any(address.version == network.version and address in network for network in _PRIVATE_NETWORKS)


def _address_is_allowed(address, allow_private_networks: bool) -> bool:
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        return False
    if address.is_global:
        return True
    # Only RFC1918/ULA space is opt-in. Other reserved/special ranges stay blocked.
    return allow_private_networks and _is_explicit_private_address(address)


def validate_webhook_url(url: str, allow_private_networks: bool) -> Tuple[str, str, int, str, str]:
    """Return (scheme, hostname, port, request target, pinned address)."""
    if not isinstance(url, str) or not url.strip():
        raise NotificationError("Webhook URL is required")
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise NotificationError("Webhook URL contains invalid characters")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise NotificationError("Webhook URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NotificationError("Webhook URL must use HTTP or HTTPS and include a host")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise NotificationError("Webhook URL cannot contain user information or a fragment")
    if "%" in parsed.hostname or any(ord(char) < 33 for char in parsed.hostname):
        raise NotificationError("Webhook host is invalid")
    if parsed.scheme == "http" and not allow_private_networks:
        raise NotificationError("Public webhook targets require HTTPS")

    hostname = parsed.hostname.rstrip(".")
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise NotificationError("Webhook host is invalid") from exc
    port = port or (443 if parsed.scheme == "https" else 80)
    try:
        resolved = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise NotificationError("Webhook host could not be resolved") from exc
    addresses = []
    for info in resolved:
        address_text = info[4][0]
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise NotificationError("Webhook host resolved ambiguously") from exc
        if not _address_is_allowed(address, allow_private_networks):
            raise NotificationError("Webhook target address is not allowed")
        if parsed.scheme == "http" and not _is_explicit_private_address(address):
            raise NotificationError("HTTP webhook targets must use an allowed private-network address")
        if address_text not in addresses:
            addresses.append(address_text)
    if not addresses:
        raise NotificationError("Webhook host could not be resolved")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return parsed.scheme, hostname, port, target, addresses[0]


class WebhookProvider(NotificationProvider):
    USER_AGENT = "SeedSync-Notifications/1"

    @staticmethod
    def canonical_json(payload: dict) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @overrides(NotificationProvider)
    def deliver(self, event: NotificationEvent, settings: NotificationSettings) -> None:
        body = self.canonical_json(event.payload())
        headers = {
        }
        if settings.hmac_secret:
            digest = hmac.new(settings.hmac_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-SeedSync-Signature"] = "sha256=" + digest

        _deliver_json(
            settings.webhook_url,
            settings.allow_private_networks,
            body,
            headers,
            success_statuses=range(200, 300),
            service_name="Webhook",
        )


def _deliver_json(url: str,
                  allow_private_networks: bool,
                  body: bytes,
                  extra_headers: dict,
                  success_statuses,
                  service_name: str,
                  validated_target=None) -> None:
    scheme, hostname, port, target, pinned_address = validated_target or validate_webhook_url(
        url, allow_private_networks
    )
    default_port = 443 if scheme == "https" else 80
    host_value = "[{}]".format(hostname) if ":" in hostname else hostname
    if port != default_port:
        host_value += ":{}".format(port)
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "User-Agent": WebhookProvider.USER_AGENT,
        "Host": host_value,
    }
    headers.update(extra_headers)

    last_error = None
    for attempt in range(2):
        connection = None
        try:
            connection_cls = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
            connection = connection_cls(hostname, pinned_address, port, _DELIVERY_TIMEOUT_SECONDS)
            connection.request("POST", target, body=body, headers=headers)
            response = connection.getresponse()
            response.read(_MAX_RESPONSE_BYTES)
            if response.status in success_statuses:
                return
            if not (500 <= response.status < 600) or attempt == 1:
                raise NotificationError("{} delivery was rejected by the remote service".format(service_name))
            last_error = NotificationError("{} service was temporarily unavailable".format(service_name))
        except NotificationError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
            if attempt == 1:
                break
        finally:
            if connection is not None:
                connection.close()
        time.sleep(_RETRY_BACKOFF_SECONDS)
    raise NotificationError("{} delivery failed".format(service_name)) from last_error


def validate_apprise_url(url: str, allow_private_networks: bool) -> Tuple[str, str, int, str, str]:
    try:
        parsed = urlsplit(url.strip()) if isinstance(url, str) else None
    except ValueError as exc:
        raise NotificationError("Apprise API notification URL is invalid") from exc
    notify_key = parsed.path[len("/notify/"):] if parsed is not None and parsed.path.startswith("/notify/") else None
    if parsed is None or notify_key is None or "/" in notify_key:
        raise NotificationError("Apprise API notification URL must use /notify/ or /notify/{KEY}")
    return validate_webhook_url(url, allow_private_networks)


class AppriseProvider(NotificationProvider):
    _LABELS = {
        "test": "Test notification",
        "download_complete": "Download complete",
        "extraction_complete": "Extraction complete",
        "delete_complete": "Remote delete complete",
    }

    @staticmethod
    def payload(event: NotificationEvent, tag: str = "") -> dict:
        label = AppriseProvider._LABELS[event.event_type]
        payload = {
            "body": "{}: {}".format(label, event.file_name),
            "title": "SeedSync - {}".format(label),
            "type": "info" if event.event_type == "test" else "success",
            "format": "text",
        }
        if tag:
            payload["tag"] = tag
        return payload

    @overrides(NotificationProvider)
    def deliver(self, event: NotificationEvent, settings: NotificationSettings) -> None:
        validated = validate_apprise_url(settings.apprise_url, settings.allow_private_networks)
        body = WebhookProvider.canonical_json(self.payload(event, settings.apprise_tag))
        _deliver_json(
            settings.apprise_url,
            settings.allow_private_networks,
            body,
            {},
            success_statuses={200},
            service_name="Apprise API",
            validated_target=validated,
        )


class ProviderRegistry:
    def __init__(self, providers: Optional[Dict[str, NotificationProvider]] = None):
        self._providers = providers or {
            "webhook": WebhookProvider(),
            "apprise": AppriseProvider(),
        }

    def get(self, name: str) -> NotificationProvider:
        return self._providers[name]


class NotificationService(IModelListener):
    _EVENT_BY_STATE = {
        ModelFile.State.DOWNLOADED: "download_complete",
        ModelFile.State.EXTRACTED: "extraction_complete",
    }

    def __init__(self, config: Config, registry: Optional[ProviderRegistry] = None, max_queue_size: int = 100):
        self._registry = registry or ProviderRegistry()
        self._settings_lock = threading.Lock()
        self._settings = NotificationSettings.from_config(config)
        self._queue = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._sentinel = object()
        self._thread = None
        self._last_drop_warning = 0.0

    def reconfigure(self, config: Config) -> None:
        settings = NotificationSettings.from_config(config)
        if settings.enabled:
            if settings.provider == "webhook" and settings.webhook_url:
                validate_webhook_url(settings.webhook_url, settings.allow_private_networks)
            elif settings.provider == "apprise" and settings.apprise_url:
                validate_apprise_url(settings.apprise_url, settings.allow_private_networks)
        with self._settings_lock:
            self._settings = settings

    def public_config(self) -> dict:
        with self._settings_lock:
            settings = self._settings
        return {
            "enabled": settings.enabled,
            "provider": settings.provider,
            "webhook_url_configured": bool(settings.webhook_url),
            "hmac_secret_configured": bool(settings.hmac_secret),
            "apprise_url_configured": bool(settings.apprise_url),
            "apprise_tag": settings.apprise_tag,
            "allow_private_networks": settings.allow_private_networks,
            "download_complete": settings.download_complete,
            "extraction_complete": settings.extraction_complete,
            "delete_complete": settings.delete_complete,
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="NotificationDelivery", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        # Shutdown does not wait behind an arbitrary delivery backlog. The one
        # in-flight request remains bounded by transport timeout/retry limits.
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        try:
            self._queue.put_nowait(self._sentinel)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))

    def test_delivery(self) -> None:
        with self._settings_lock:
            settings = self._settings
        endpoint = settings.webhook_url if settings.provider == "webhook" else settings.apprise_url
        if not endpoint:
            raise NotificationError("Selected notification provider URL is not configured")
        event = NotificationEvent(
            event_type="test",
            file_name="test-notification",
            path_pair_id=None,
            path_pair_name=None,
            event_id=str(uuid.uuid4()),
            occurred_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )
        self._registry.get(settings.provider).deliver(event, settings)

    # Compatibility for callers from the initial webhook-only implementation.
    def test_webhook(self) -> None:
        self.test_delivery()

    @overrides(IModelListener)
    def file_added(self, file: ModelFile):
        # Completion is defined as a state transition, not discovery of an
        # already-terminal model entry.
        return

    @overrides(IModelListener)
    def file_removed(self, file: ModelFile):
        return

    @overrides(IModelListener)
    def file_updated(self, old_file: ModelFile, new_file: ModelFile):
        if old_file.state == new_file.state:
            return
        if old_file.state == ModelFile.State.EXTRACTING and new_file.state == ModelFile.State.DOWNLOADED:
            return
        event_type = self._EVENT_BY_STATE.get(new_file.state)
        if event_type is None:
            return
        with self._settings_lock:
            settings = self._settings
        if not settings.enabled or not getattr(settings, event_type):
            return
        self.enqueue(NotificationEvent.create(event_type, new_file))

    def remote_delete_completed(self, file: ModelFile):
        with self._settings_lock:
            settings = self._settings
        if settings.enabled and settings.delete_complete:
            self.enqueue(NotificationEvent.create("delete_complete", file))

    def enqueue(self, event: NotificationEvent) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            now = time.monotonic()
            if now - self._last_drop_warning >= 60.0:
                self._last_drop_warning = now
                logger.warning("Notification queue is full; dropping newest delivery")
            return False

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            try:
                if item is self._sentinel:
                    return
                with self._settings_lock:
                    settings = self._settings
                if not settings.enabled or not getattr(settings, item.event_type, False):
                    continue
                self._registry.get(settings.provider).deliver(item, settings)
            except Exception:
                # Do not include URL, query, secret, request body, or remote response.
                logger.warning("Notification delivery failed", exc_info=False)
            finally:
                self._queue.task_done()
