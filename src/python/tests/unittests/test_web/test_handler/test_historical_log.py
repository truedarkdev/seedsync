import json
import logging
import os
import tempfile
import unittest
from unittest import mock

import bottle

from web.handler.historical_log import (
    CANONICAL_CONFIG_SENSITIVE_FIELDS, HistoricalJsonFormatter, HistoricalLogHandler, HistoricalLogStore,
    create_historical_log_handler
)


class TestHistoricalLogStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp_dir.name, "history.jsonl")
        self.store = HistoricalLogStore(self.path, 2)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, path, records, partial=False):
        with open(path, "w", encoding="utf-8") as target:
            for record in records:
                target.write(json.dumps(record) + "\n")
            if partial:
                target.write('{"partial":')

    @staticmethod
    def record(record_id, epoch, level="INFO", logger="seed", message="message", exception=None):
        return {"id": record_id, "timestamp": "2026-01-01T00:00:00+00:00", "epoch": epoch,
                "level": level, "level_number": logging._nameToLevel[level], "logger": logger,
                "message": message, "exception": exception}

    def test_rotation_order_filters_and_cursor_are_deterministic(self):
        self.write(self.path + ".1", [self.record("one", 1, logger="scan")])
        self.write(self.path, [self.record("two", 2, level="ERROR", message="needle"),
                               self.record("three", 3, level="ERROR", message="needle")])
        first = self.store.query(levels={"ERROR"}, text="NEEDLE", direction="asc", limit=1)
        self.assertEqual(["two"], [item["id"] for item in first["records"]])
        self.assertTrue(first["page"]["has_more"])
        second = self.store.query(levels={"ERROR"}, text="needle", direction="asc", limit=1,
                                  cursor=first["page"]["next_cursor"])
        self.assertEqual(["three"], [item["id"] for item in second["records"]])

    def test_malformed_and_partial_records_are_skipped_and_reported(self):
        self.write(self.path, [self.record("valid", 1)], partial=True)
        result = self.store.query()
        self.assertEqual(["valid"], [item["id"] for item in result["records"]])
        self.assertEqual(1, result["evidence"]["malformed_records_skipped"])

    def test_missing_files_return_empty_page(self):
        result = self.store.query()
        self.assertEqual([], result["records"])
        self.assertFalse(result["page"]["has_more"])

    def test_invalid_or_expired_cursor_is_rejected(self):
        self.write(self.path, [self.record("valid", 1)])
        with self.assertRaisesRegex(ValueError, "invalid cursor"):
            self.store.query(cursor="bm90LWpzb24")

    def test_formatter_assigns_identity_and_redacts_message_and_exception(self):
        formatter = HistoricalJsonFormatter()
        try:
            raise RuntimeError("password=hunter2")
        except RuntimeError:
            record = logging.LogRecord("seed", logging.ERROR, __file__, 1,
                                       "api_key=secret-value path=/home/user/private/file", (), None)
            record.exc_info = __import__("sys").exc_info()
        payload = json.loads(formatter.format(record))
        self.assertTrue(payload["id"])
        self.assertNotIn("hunter2", payload["exception"])
        self.assertNotIn("secret-value", payload["message"])
        self.assertNotIn("/home/user/private/file", payload["message"])

    def test_formatter_redacts_structured_credentials_authorization_urls_and_all_absolute_paths(self):
        secrets = ["json-secret", "dict-secret", "bearer-secret", "basic-secret", "url-secret",
                   "token-secret", "digest-secret", "custom-secret", "remote-json", "remote-dict"]
        message = "\n".join([
            '{"password": "json-secret"}', "{'api_key': 'dict-secret'}",
            "Authorization: Bearer bearer-secret", "Authorization=Basic basic-secret",
            "Authorization: Token token-secret", "Authorization: Digest digest-secret, nonce=still-secret",
            "Authorization=Custom custom-secret", '{"remote_password": "remote-json"}',
            "{'remote_password': 'remote-dict'}",
            "https://user:url-secret@example.test/private", "/etc/passwd /var/lib/app /opt/app /root/key /usr/bin/tool",
            r"C:\Users\seed\secret.txt \\server\share\private.txt file:///etc/shadow remote:/var/private",
            "timestamp=2026-07-12T17:00:00Z https://example.test/status",
        ])
        record = logging.LogRecord("/srv/private/logger", logging.ERROR, __file__, 1, message, (), None)
        payload = json.loads(HistoricalJsonFormatter().format(record))
        serialized = json.dumps(payload)
        for secret in secrets:
            self.assertNotIn(secret, serialized)
        for path in ("/etc/passwd", "/var/lib/app", "/opt/app", "/root/key", "/usr/bin/tool",
                     r"C:\Users\seed\secret.txt", r"\\server\share\private.txt", "/srv/private/logger",
                     "/etc/shadow", "/var/private"):
            self.assertNotIn(path, serialized)
        self.assertIn("**REDACTED**", serialized)
        self.assertIn("**REDACTED_PATH**", serialized)
        self.assertIn("2026-07-12T17:00:00Z", serialized)
        self.assertIn("https://example.test/status", serialized)

    def test_persisted_line_contains_placeholders_not_revision_two_sensitive_values(self):
        handler = create_historical_log_handler(self.path, max_bytes=100000, backup_count=1)
        logger = logging.getLogger("history-redaction-persistence-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.error("Authorization: Token token-secret\n"
                     "Authorization: Digest digest-secret; opaque=still-secret, unknown=also-secret\n"
                     "Authorization: Custom custom-secret; mystery=custom-tail\n"
                     + json.dumps({"authorization": 'Digest username="bob"; opaque=json-auth-tail'}) + "\n"
                     "{'authorization': 'Custom realm=\"x\"; unknown=python-auth-tail'}\n"
                     "benign-next-line remains visible\n"
                     '{"remote_password": "remote-json"}\n'
                     "{'remote_password': 'remote-dict'}\nfile:///etc/private remote:/var/private")
        handler.close()
        with open(self.path, "r", encoding="utf-8") as source:
            persisted = source.read()
        for sensitive in ("token-secret", "digest-secret", "still-secret", "also-secret", "custom-secret",
                          "custom-tail", "remote-json", "remote-dict",
                          "json-auth-tail", "python-auth-tail",
                          "/etc/private", "/var/private"):
            self.assertNotIn(sensitive, persisted)
        self.assertIn("**REDACTED**", persisted)
        self.assertIn("**REDACTED_PATH**", persisted)
        self.assertIn("benign-next-line remains visible", persisted)

    def test_structured_policy_tracks_and_redacts_every_config_sensitive_field(self):
        expected = {"api_token", "webhook_secret", "remote_password", "webhook_url",
                    "hmac_secret", "apprise_url"}
        self.assertEqual(expected, set(CANONICAL_CONFIG_SENSITIVE_FIELDS))
        lines = []
        raw_values = []
        for key in sorted(expected):
            json_value = "json-{}-raw".format(key)
            python_value = "python-{}-raw".format(key)
            raw_values.extend((json_value, python_value))
            lines.append(json.dumps({key: json_value}))
            lines.append("{{{!r}: {!r}}}".format(key, python_value))
        message = "\n".join(lines)

        handler = create_historical_log_handler(self.path, max_bytes=100000, backup_count=1)
        logger = logging.getLogger("history-canonical-fields-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.error(message)
        handler.close()
        with open(self.path, "r", encoding="utf-8") as source:
            persisted = source.read()
        for value in raw_values:
            self.assertNotIn(value, persisted)
        self.assertIn("**REDACTED**", persisted)

        legacy_path = self.path + ".1"
        self.write(legacy_path, [self.record("legacy-fields", 1, message=message)])
        read_back = json.dumps(self.store.query())
        for value in raw_values:
            self.assertNotIn(value, read_back)
        self.assertIn("**REDACTED**", read_back)

    def test_unquoted_config_policy_redacts_every_canonical_field_and_preserves_benign_content(self):
        expected = sorted(CANONICAL_CONFIG_SENSITIVE_FIELDS)
        lines = []
        raw_values = []
        for index, key in enumerate(expected):
            separator = "=" if index % 2 == 0 else ": "
            value = "raw-{}-value".format(key)
            raw_values.append(value)
            lines.append("{}{}{}".format(key, separator, value))
        webhook_value = "https://hooks.example/secret-path"
        apprise_value = "https://notify.example/private-token"
        raw_values.extend((webhook_value, apprise_value))
        lines.extend([
            "webhook_url={}; benign_field=visible-same-line".format(webhook_value),
            "apprise_url: {}".format(apprise_value),
            "benign-next-line=visible-next-line",
        ])
        message = "\n".join(lines)

        handler = create_historical_log_handler(self.path, max_bytes=100000, backup_count=1)
        logger = logging.getLogger("history-unquoted-config-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.error(message)
        handler.close()
        with open(self.path, "r", encoding="utf-8") as source:
            persisted = source.read()
        for value in raw_values:
            self.assertNotIn(value, persisted)
        self.assertIn("benign_field=visible-same-line", persisted)
        self.assertIn("benign-next-line=visible-next-line", persisted)

        self.write(self.path + ".1", [self.record("legacy-unquoted", 1, message=message)])
        read_back = json.dumps(self.store.query())
        for value in raw_values:
            self.assertNotIn(value, read_back)
        self.assertIn("benign_field=visible-same-line", read_back)
        self.assertIn("benign-next-line=visible-next-line", read_back)

    def test_read_back_reapplies_redaction_and_record_truncation(self):
        legacy = self.record("legacy", 1, logger="/etc/logger",
                             message='{"token": "stored-secret", "remote_password": "remote-json"} '
                                     "{'remote_password': 'remote-dict'}\n" +
                                     json.dumps({"authorization": 'Digest realm="x"; opaque=json-read-tail'}) + "\n"
                                     "{'authorization': 'Custom realm=\"x\"; unknown=python-read-tail'} "
                                     "/root/private file:///etc/legacy",
                             exception="Authorization: Token token-old\n"
                                       "Authorization: Digest digest-old; opaque=digest-tail, unknown=other-tail\n"
                                       "Authorization: Strange custom-old; mystery=custom-tail\n"
                                       "benign-next-line survives\n/var/log/private remote:/var/legacy")
        legacy["message"] += "\n" + "x" * 20000
        self.write(self.path, [legacy])
        result = self.store.query()
        serialized = json.dumps(result)
        self.assertNotIn("stored-secret", serialized)
        for secret in ("remote-json", "remote-dict", "token-old", "digest-old", "digest-tail",
                       "other-tail", "custom-old", "custom-tail"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("json-read-tail", serialized)
        self.assertNotIn("python-read-tail", serialized)
        self.assertNotIn("/root/private", serialized)
        self.assertNotIn("/var/log/private", serialized)
        self.assertNotIn("/etc/legacy", serialized)
        self.assertNotIn("/var/legacy", serialized)
        self.assertIn("benign-next-line survives", serialized)
        self.assertTrue(result["records"][0]["truncated"])

    def test_multibyte_truncation_is_valid_and_cursor_identity_is_stable(self):
        self.write(self.path, [self.record("emoji", 1, message="🙂" * 5000),
                               self.record("next", 2, message="ordinary")])
        first = self.store.query(direction="asc", limit=1)
        message = first["records"][0]["message"]
        self.assertTrue(first["records"][0]["truncated"])
        self.assertTrue(message.endswith("...[TRUNCATED]"))
        message.encode("utf-8", errors="strict")
        second = self.store.query(direction="asc", limit=1, cursor=first["page"]["next_cursor"])
        self.assertEqual("next", second["records"][0]["id"])

    def test_response_ceiling_stops_at_record_boundary_and_keeps_cursor(self):
        self.write(self.path, [self.record(str(index), index, message="x" * 200) for index in range(20)])
        with mock.patch("web.handler.historical_log.MAX_RESPONSE_BYTES", 4600):
            first = self.store.query(direction="asc", limit=20)
            self.assertTrue(first["evidence"]["output_truncated"])
            self.assertTrue(first["page"]["has_more"])
            second = self.store.query(direction="asc", limit=20, cursor=first["page"]["next_cursor"])
            self.assertNotEqual(first["records"][-1]["id"], second["records"][0]["id"])

    @unittest.skipIf(os.name == "nt", "POSIX mode assertions are not meaningful on Windows")
    def test_handler_enforces_private_modes_and_rollover_modes_with_permissive_umask(self):
        directory = os.path.dirname(self.path)
        os.chmod(directory, 0o777)
        open(self.path, "w").close()
        os.chmod(self.path, 0o666)
        old_umask = os.umask(0)
        try:
            handler = create_historical_log_handler(self.path, max_bytes=1, backup_count=2)
            logger = logging.getLogger("history-permissions-test")
            logger.handlers = [handler]
            logger.propagate = False
            logger.error("first")
            logger.error("second")
            handler.close()
        finally:
            os.umask(old_umask)
        self.assertEqual(0o700, os.stat(directory).st_mode & 0o777)
        self.assertEqual(0o600, os.stat(self.path).st_mode & 0o777)
        self.assertEqual(0o600, os.stat(self.path + ".1").st_mode & 0o777)

    @unittest.skipIf(not hasattr(os, "symlink"), "symlink support unavailable")
    def test_handler_rejects_symlinked_directory_and_file(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        directory_link = os.path.join(self.temp_dir.name, "linked")
        try:
            os.symlink(other.name, directory_link, target_is_directory=True)
        except OSError as exc:
            self.skipTest("symlink creation unavailable: {}".format(exc))
        with self.assertRaises(OSError):
            create_historical_log_handler(os.path.join(directory_link, "history.jsonl"), 100, 1)

        target = os.path.join(other.name, "target")
        open(target, "w").close()
        file_link = os.path.join(self.temp_dir.name, "file-link")
        os.symlink(target, file_link)
        with self.assertRaises(OSError):
            create_historical_log_handler(file_link, 100, 1)

    def test_handler_rejects_unexpected_active_file_object(self):
        os.mkdir(self.path)
        with self.assertRaises(OSError):
            create_historical_log_handler(self.path, 100, 1)

    def test_route_is_registered_with_admin_scope(self):
        captured = {}

        class FakeWebApp:
            def add_handler(self, path, callback, required_scope=None):
                captured.update(path=path, callback=callback, required_scope=required_scope)

        HistoricalLogHandler(self.store, logging.getLogger("test")).add_routes(FakeWebApp())
        self.assertEqual("/server/logs/history/v1", captured["path"])
        self.assertEqual("admin", captured["required_scope"])

    def test_handler_returns_generic_error_for_invalid_filters_and_cursor(self):
        handler = HistoricalLogHandler(self.store, logging.getLogger("test-query-errors"))
        for query in ("limit=501", "direction=sideways", "level=SECRET", "start=20&end=10", "cursor=not-json"):
            bottle.request.bind({"QUERY_STRING": query, "REQUEST_METHOD": "GET", "PATH_INFO": "/"})
            response = handler._get()
            self.assertEqual(400, response.status_code)
            self.assertEqual({"error": "invalid log history query"}, json.loads(response.body))


if __name__ == "__main__":
    unittest.main()
