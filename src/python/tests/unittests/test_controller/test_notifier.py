import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import Mock, patch

from common import Config
from controller.notifier import (
    AppriseProvider, NotificationError, NotificationEvent, NotificationProvider, NotificationService,
    NotificationSettings,
    ProviderRegistry, WebhookProvider, WebhookSettings, validate_apprise_url, validate_webhook_url,
)
from controller.notifier import _PinnedHTTPSConnection
from model import ModelFile


class RecordingProvider(NotificationProvider):
    def __init__(self):
        self.events = []

    def deliver(self, event, settings):
        self.events.append((event, settings))


class TestNotificationService(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.config.notifications.enabled = True
        self.config.notifications.webhook_url = "https://hooks.example.test/seed"
        self.provider = RecordingProvider()
        self.service = NotificationService(
            self.config, ProviderRegistry({"webhook": self.provider}), max_queue_size=2
        )

    def test_only_terminal_state_transitions_are_enqueued_once(self):
        old_file = ModelFile("release/file.mkv", False)
        old_file.path_pair_id = "pair-id"
        old_file.path_pair_name = "Movies"
        new_file = ModelFile("release/file.mkv", False)
        new_file.path_pair_id = "pair-id"
        new_file.path_pair_name = "Movies"
        new_file.state = ModelFile.State.DOWNLOADED

        self.service.file_added(new_file)
        self.service.file_updated(old_file, new_file)
        self.service.file_updated(new_file, new_file)
        queued = self.service._queue.get_nowait()
        self.assertEqual("download_complete", queued.event_type)
        self.assertEqual({
            "name": "release/file.mkv", "path_pair_id": "pair-id", "path_pair_name": "Movies"
        }, queued.payload()["file"])
        self.assertTrue(self.service._queue.empty())

    def test_extraction_rollback_does_not_emit_duplicate_download_complete(self):
        old_file = ModelFile("release/file.mkv", False)
        old_file.state = ModelFile.State.EXTRACTING
        new_file = ModelFile("release/file.mkv", False)
        new_file.state = ModelFile.State.DOWNLOADED

        self.service.file_updated(old_file, new_file)

        self.assertTrue(self.service._queue.empty())

    def test_local_deleted_state_does_not_emit_remote_delete_complete(self):
        old_file = ModelFile("release/file.mkv", False)
        new_file = ModelFile("release/file.mkv", False)
        new_file.state = ModelFile.State.DELETED

        self.service.file_updated(old_file, new_file)

        self.assertTrue(self.service._queue.empty())

    def test_proven_remote_delete_success_enqueues_once(self):
        file = ModelFile("release/file.mkv", False)

        self.service.remote_delete_completed(file)

        event = self.service._queue.get_nowait()
        self.assertEqual("delete_complete", event.event_type)
        self.assertEqual("release/file.mkv", event.file_name)
        self.assertTrue(self.service._queue.empty())

    @patch("controller.notifier.validate_webhook_url")
    def test_download_started_uses_dedicated_provider_neutral_event(self, _validate):
        file = ModelFile("release/file.mkv", False)
        self.config.notifications.download_start = True
        self.service.reconfigure(self.config)

        self.service.download_started(file)

        event = self.service._queue.get_nowait()
        self.assertEqual("download_start", event.event_type)
        self.assertEqual("release/file.mkv", event.payload()["file"]["name"])

    def test_download_started_is_disabled_by_default(self):
        self.service.download_started(ModelFile("release/file.mkv", False))

        self.assertTrue(self.service._queue.empty())

    def test_queue_drops_newest_when_full(self):
        event = NotificationEvent.create("download_complete", ModelFile("a", False))
        self.assertTrue(self.service.enqueue(event))
        self.assertTrue(self.service.enqueue(event))
        self.assertFalse(self.service.enqueue(event))

    def test_notification_settings_reject_malformed_dynamic_value(self):
        self.config.notifications.__dict__["__enabled"] = "yes"

        with self.assertRaises(NotificationError):
            NotificationSettings.from_config(self.config)

    def test_owned_worker_delivers_and_stops(self):
        self.service.start()
        self.service.enqueue(NotificationEvent.create("delete_complete", ModelFile("a", False)))
        deadline = time.monotonic() + 1
        while not self.provider.events and time.monotonic() < deadline:
            time.sleep(0.01)
        self.service.stop()
        self.assertEqual(1, len(self.provider.events))
        self.assertFalse(self.service._thread.is_alive())

    def test_test_delivery_uses_selected_apprise_provider(self):
        self.config.notifications.provider = "apprise"
        self.config.notifications.apprise_url = "https://apprise.example.test/notify/key"
        apprise = RecordingProvider()
        service = NotificationService(
            self.config,
            ProviderRegistry({"webhook": self.provider, "apprise": apprise}),
        )

        service.test_delivery()

        self.assertEqual(0, len(self.provider.events))
        self.assertEqual("test", apprise.events[0][0].event_type)


class TestWebhookProvider(unittest.TestCase):
    def test_webhook_validator_rejects_non_string_urls(self):
        for url in (None, 123):
            with self.subTest(url=url):
                with self.assertRaises(NotificationError):
                    validate_webhook_url(url, False)

    @patch("controller.notifier.ssl.create_default_context")
    @patch("controller.notifier.socket.create_connection")
    def test_tls_connection_pins_address_but_verifies_logical_hostname(self, create_connection, create_context):
        raw_socket = Mock()
        create_connection.return_value = raw_socket
        connection = _PinnedHTTPSConnection("hooks.example.test", "93.184.216.34", 443, 5.0)

        connection.connect()

        create_connection.assert_called_once_with(("93.184.216.34", 443), 5.0, None)
        create_context.return_value.wrap_socket.assert_called_once_with(
            raw_socket, server_hostname="hooks.example.test"
        )

    @patch("controller.notifier.socket.getaddrinfo")
    def test_rejects_mixed_public_and_private_dns_answers(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with self.assertRaises(NotificationError):
            validate_webhook_url("https://example.test/hook", False)

    @patch("controller.notifier.socket.getaddrinfo")
    def test_rejects_non_string_dns_address(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", (123, 443))]

        with self.assertRaises(NotificationError):
            validate_webhook_url("https://example.test/hook", False)

    @patch("controller.notifier.socket.getaddrinfo")
    def test_private_http_requires_explicit_opt_in(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("10.0.0.8", 80))]
        with self.assertRaises(NotificationError):
            validate_webhook_url("http://internal.test/hook", False)
        result = validate_webhook_url("http://internal.test/hook", True)
        self.assertEqual("10.0.0.8", result[-1])

    @patch("controller.notifier.socket.getaddrinfo")
    def test_http_remains_blocked_for_public_targets_even_with_private_opt_in(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 80))]
        with self.assertRaises(NotificationError):
            validate_webhook_url("http://example.test/hook", True)

    def test_canonical_json_and_signature_are_stable(self):
        payload = {"z": 1, "a": {"b": "x"}}
        body = WebhookProvider.canonical_json(payload)
        self.assertEqual(b'{"a":{"b":"x"},"z":1}', body)
        self.assertEqual(
            hmac.new(b"secret", body, hashlib.sha256).hexdigest(),
            "803153b58f2df74ff96599f92f49c1c3a298f6cae42ae6a481a090dc521b1d3c",
        )

    @patch("controller.notifier._PinnedHTTPSConnection")
    @patch("controller.notifier.validate_webhook_url")
    def test_delivery_uses_pinned_connection_host_header_and_hmac(self, validate, connection_cls):
        validate.return_value = ("https", "hooks.example.test", 443, "/hook?q=opaque", "93.184.216.34")
        response = Mock(status=204)
        connection_cls.return_value.getresponse.return_value = response
        event = NotificationEvent("download_complete", "movie.mkv", None, None, "event-id", "2026-01-01T00:00:00.000Z")
        settings = WebhookSettings(True, "https://hooks.example.test/hook?q=opaque", "secret", False, False, True, True, True)

        WebhookProvider().deliver(event, settings)

        connection_cls.assert_called_once_with("hooks.example.test", "93.184.216.34", 443, 5.0)
        _, kwargs = connection_cls.return_value.request.call_args
        self.assertEqual("hooks.example.test", kwargs["headers"]["Host"])
        expected = hmac.new(b"secret", kwargs["body"], hashlib.sha256).hexdigest()
        self.assertEqual("sha256=" + expected, kwargs["headers"]["X-SeedSync-Signature"])
        response.read.assert_called_once_with(4096)

    @patch("controller.notifier._PinnedHTTPSConnection")
    @patch("controller.notifier.validate_webhook_url")
    def test_redirect_is_rejected_without_following_or_retrying(self, validate, connection_cls):
        validate.return_value = ("https", "hooks.example.test", 443, "/hook", "93.184.216.34")
        connection_cls.return_value.getresponse.return_value = Mock(status=302)
        event = NotificationEvent("download_complete", "movie.mkv", None, None, "event-id", "2026-01-01T00:00:00.000Z")
        settings = WebhookSettings(True, "https://hooks.example.test/hook", "", False, False, True, True, True)

        with self.assertRaises(NotificationError):
            WebhookProvider().deliver(event, settings)

        self.assertEqual(1, connection_cls.return_value.request.call_count)

    @patch("controller.notifier.time.sleep")
    @patch("controller.notifier._PinnedHTTPSConnection")
    @patch("controller.notifier.validate_webhook_url")
    def test_retries_one_5xx_then_succeeds(self, validate, connection_cls, sleep):
        validate.return_value = ("https", "hooks.example.test", 443, "/hook", "93.184.216.34")
        connection_cls.return_value.getresponse.side_effect = [Mock(status=503), Mock(status=204)]
        event = NotificationEvent("download_complete", "movie.mkv", None, None, "event-id", "2026-01-01T00:00:00.000Z")
        settings = WebhookSettings(True, "https://hooks.example.test/hook", "", False, False, True, True, True)

        WebhookProvider().deliver(event, settings)

        self.assertEqual(2, connection_cls.call_count)
        self.assertEqual(2, connection_cls.return_value.request.call_count)
        sleep.assert_called_once()


class TestAppriseProvider(unittest.TestCase):
    def test_apprise_validator_rejects_non_string_urls(self):
        for url in (None, 123):
            with self.subTest(url=url):
                with self.assertRaises(NotificationError):
                    validate_apprise_url(url, False)

    def settings(self, tag=""):
        return WebhookSettings(
            True, "", "", False, True, True, True, True,
            provider="apprise",
            apprise_url="https://apprise.example.test/notify/private-key",
            apprise_tag=tag,
        )

    def test_payload_maps_all_supported_events_without_seed_sync_paths(self):
        expected = {
            "test": ("info", "Test notification"),
            "download_start": ("info", "Download started"),
            "download_complete": ("success", "Download complete"),
            "extraction_complete": ("success", "Extraction complete"),
            "delete_complete": ("success", "Remote delete complete"),
        }
        for event_type, (notification_type, label) in expected.items():
            with self.subTest(event_type=event_type):
                event = NotificationEvent(
                    event_type, "release/movie.mkv", "pair-id", "Movies",
                    "event-id", "2026-01-01T00:00:00.000Z",
                )
                payload = AppriseProvider.payload(event, "seedbox")
                self.assertEqual(notification_type, payload["type"])
                self.assertEqual("text", payload["format"])
                self.assertEqual("seedbox", payload["tag"])
                self.assertIn(label, payload["title"])
                self.assertIn("release/movie.mkv", payload["body"])
                serialized = json.dumps(payload)
                self.assertNotIn("pair-id", serialized)
                self.assertNotIn("/downloads/", serialized)

    def test_optional_tag_is_omitted(self):
        event = NotificationEvent("test", "test-notification", None, None, "id", "now")
        self.assertNotIn("tag", AppriseProvider.payload(event))

    @patch("controller.notifier.socket.getaddrinfo")
    def test_apprise_url_requires_notify_path_and_reuses_safe_url_policy(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("10.0.0.8", 80))]
        with self.assertRaises(NotificationError):
            validate_apprise_url("http://apprise.test/status", True)
        with self.assertRaises(NotificationError):
            validate_apprise_url("http://apprise.test/notify/key", False)
        self.assertEqual(
            "10.0.0.8",
            validate_apprise_url("http://apprise.test/notify/key", True)[-1],
        )

    @patch("controller.notifier._PinnedHTTPSConnection")
    @patch("controller.notifier.validate_apprise_url")
    def test_only_200_is_success_and_apprise_never_gets_hmac(self, validate, connection_cls):
        validate.return_value = ("https", "apprise.example.test", 443, "/notify/key", "93.184.216.34")
        connection_cls.return_value.getresponse.return_value = Mock(status=200)
        event = NotificationEvent("download_complete", "movie.mkv", None, None, "id", "now")
        settings = self.settings("seedbox")

        AppriseProvider().deliver(event, settings)

        _, kwargs = connection_cls.return_value.request.call_args
        self.assertNotIn("X-SeedSync-Signature", kwargs["headers"])
        payload = json.loads(kwargs["body"])
        self.assertEqual("seedbox", payload["tag"])

    @patch("controller.notifier._PinnedHTTPSConnection")
    @patch("controller.notifier.validate_apprise_url")
    def test_204_400_and_424_fail_without_retry(self, validate, connection_cls):
        validate.return_value = ("https", "apprise.example.test", 443, "/notify/key", "93.184.216.34")
        event = NotificationEvent("download_complete", "movie.mkv", None, None, "id", "now")
        for status in (204, 400, 424):
            with self.subTest(status=status):
                connection_cls.reset_mock()
                connection_cls.return_value.getresponse.return_value = Mock(status=status)
                with self.assertRaises(NotificationError):
                    AppriseProvider().deliver(event, self.settings())
                self.assertEqual(1, connection_cls.call_count)

    @patch("controller.notifier.time.sleep")
    @patch("controller.notifier._PinnedHTTPSConnection")
    @patch("controller.notifier.validate_apprise_url")
    def test_5xx_retries_once_then_fails(self, validate, connection_cls, sleep):
        validate.return_value = ("https", "apprise.example.test", 443, "/notify/key", "93.184.216.34")
        connection_cls.return_value.getresponse.return_value = Mock(status=503)
        event = NotificationEvent("download_complete", "movie.mkv", None, None, "id", "now")

        with self.assertRaises(NotificationError):
            AppriseProvider().deliver(event, self.settings())

        self.assertEqual(2, connection_cls.call_count)
        sleep.assert_called_once()
