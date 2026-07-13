# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import json
import logging

from .test_serialize import parse_stream
from web.serialize import SerializeLogRecord


class TestSerializeLogRecord(unittest.TestCase):
    def test_event_names(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        self.assertEqual("log-record", out["event"])

    def test_record_time(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual(str(record.created), data["time"])

    def test_record_level_name(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()

        record = logger.makeRecord(
            name=None,
            level=logging.DEBUG,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("DEBUG", data["level_name"])

        record = logger.makeRecord(
            name=None,
            level=logging.INFO,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("INFO", data["level_name"])

        record = logger.makeRecord(
            name=None,
            level=logging.WARNING,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("WARNING", data["level_name"])

        record = logger.makeRecord(
            name=None,
            level=logging.ERROR,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("ERROR", data["level_name"])

        record = logger.makeRecord(
            name=None,
            level=logging.CRITICAL,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("CRITICAL", data["level_name"])

    def test_record_logger_name(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()
        record = logger.makeRecord(
            name="myloggername",
            level=None,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("myloggername", data["logger_name"])

    def test_record_message(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg="my logger msg",
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("my logger msg", data["message"])

    def test_record_message_redacts_password_like_values(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg="running lftp -u seedbox,secretpass and remote_password=hunter2",
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual(
            "running lftp -u seedbox,**REDACTED** and remote_password=**REDACTED**",
            data["message"]
        )

    def test_record_message_uses_get_message(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg="password=%s",
            args=("secretpass",),
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("password=**REDACTED**", data["message"])

    def test_record_exception_text(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()

        # When there's exc_text already there
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        record.exc_text = "My traceback"
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("My traceback", data["exc_tb"])

        # When there's exc_info but no exc_text
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=(None, ValueError(), None),
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual("ValueError", data["exc_tb"])

        # When there's neither
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual(None, data["exc_tb"])

    def test_record_exception_text_redacts_password_like_values(self):
        serialize = SerializeLogRecord()
        logger = logging.getLogger()
        record = logger.makeRecord(
            name=None,
            level=None,
            fn=None,
            lno=None,
            msg=None,
            args=None,
            exc_info=None,
            func=None,
            sinfo=None
        )
        record.exc_text = "command failed: -u seedbox,secretpass password: hunter2"
        out = parse_stream(serialize.record(record))
        data = json.loads(out["data"])
        self.assertEqual(
            "command failed: -u seedbox,**REDACTED** password: **REDACTED**",
            data["exc_tb"]
        )

    def test_live_record_uses_history_path_and_credential_sanitizer(self):
        record = logging.LogRecord(
            "/srv/private/logger",
            logging.ERROR,
            __file__,
            1,
            "failed sftp://alice:secret@example.test at /home/alice/private/file",
            (),
            None,
        )
        record.exc_text = "Authorization: Bearer secret-token path=/etc/seedsync/config"

        data = json.loads(parse_stream(SerializeLogRecord().record(record))["data"])

        serialized = json.dumps(data)
        for secret_value in (
            "/srv/private/logger", "alice", "secret", "example.test",
            "/home/alice/private/file", "secret-token", "/etc/seedsync/config",
        ):
            self.assertNotIn(secret_value, serialized)


class TestRedactSensitive(unittest.TestCase):
    def test_redact_sftp_url(self):
        result = SerializeLogRecord._redact_sensitive(
            "Connecting to sftp://myuser@seedbox.example.com/downloads"
        )
        self.assertNotIn("myuser", result)
        self.assertNotIn("seedbox.example.com", result)
        self.assertIn("sftp://", result)
        self.assertIn("**REDACTED**", result)

    def test_redact_ftp_and_ftps_urls_with_reserved_characters(self):
        result = SerializeLogRecord._redact_sensitive(
            "Connecting to ftp://alice:pa:ss@seedbox.example.com/downloads "
            "and ftps://bob:pa/ss@mirror.example.net:21/files"
        )

        self.assertNotIn("pa:ss", result)
        self.assertNotIn("pa/ss", result)
        self.assertNotIn("seedbox.example.com", result)
        self.assertNotIn("mirror.example.net", result)
        self.assertIn("ftp://**REDACTED**@**REDACTED****REDACTED_PATH**", result)
        self.assertIn("ftps://**REDACTED**@**REDACTED**:21/files", result)

    def test_redact_ssh_command_args_user_at_host(self):
        result = SerializeLogRecord._redact_sensitive(
            "Command: ['ssh', '-p', '22', 'myuser@seedbox.example.com', 'ls']"
        )
        self.assertNotIn("myuser@seedbox", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_redact_scp_user_at_host_colon_path(self):
        result = SerializeLogRecord._redact_sensitive(
            "Command: ['scp', 'myuser@seedbox.example.com:/remote/path', '/local/path']"
        )
        self.assertNotIn("myuser@seedbox", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_redact_lftp_prompt(self):
        result = SerializeLogRecord._redact_sensitive("lftp myuser@seedbox.example.com:~>")
        self.assertNotIn("myuser@seedbox", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_redact_ssh_command_args_user_at_ipv4(self):
        result = SerializeLogRecord._redact_sensitive(
            "Command: ['ssh', '-p', '22', 'myuser@127.0.0.1', 'ls']"
        )
        self.assertNotIn("myuser@127.0.0.1", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_redact_scp_user_at_ipv4_colon_path(self):
        result = SerializeLogRecord._redact_sensitive(
            "Command: ['scp', 'myuser@127.0.0.1:/remote/path', '/local/path']"
        )
        self.assertNotIn("myuser@127.0.0.1", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_redact_ssh_command_args_user_at_remote(self):
        result = SerializeLogRecord._redact_sensitive(
            "Command: ['ssh', '-p', '22', 'myuser@remote', 'ls']"
        )
        self.assertNotIn("myuser@remote", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_redact_ssh_command_args_user_at_localhost(self):
        result = SerializeLogRecord._redact_sensitive(
            "Command: ['ssh', '-p', '22', 'myuser@localhost', 'ls']"
        )
        self.assertNotIn("myuser@localhost", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_redact_start_of_string_user_at_remote_prompt(self):
        result = SerializeLogRecord._redact_sensitive("myuser@remote:~>")
        self.assertEqual("**REDACTED**@**REDACTED**:~>", result)

    def test_redact_ssh_command_args_user_at_leading_digit_host(self):
        result = SerializeLogRecord._redact_sensitive(
            "Command: ['ssh', '-p', '22', 'myuser@1seedbox', 'ls']"
        )
        self.assertNotIn("myuser@1seedbox", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_redact_ssh_command_args_user_at_underscore_host(self):
        result = SerializeLogRecord._redact_sensitive(
            "Command: ['ssh', '-p', '22', 'myuser@my_seedbox', 'ls']"
        )
        self.assertNotIn("myuser@my_seedbox", result)
        self.assertIn("**REDACTED**@**REDACTED**", result)

    def test_no_redact_filename_with_at(self):
        msg = "Downloading file@720p.mkv"
        result = SerializeLogRecord._redact_sensitive(msg)
        self.assertEqual(msg, result)

    def test_no_redact_filename_at_version(self):
        msg = "Processing release@1.0.tar.gz"
        result = SerializeLogRecord._redact_sensitive(msg)
        self.assertEqual(msg, result)

    def test_redact_absolute_path_even_when_filename_contains_at(self):
        msg = "/downloads/show@720p/file.mkv"
        result = SerializeLogRecord._redact_sensitive(msg)
        self.assertEqual("**REDACTED_PATH**", result)

    def test_no_redact_filename_like_token_with_letter_suffix(self):
        msg = "Now playing episode@final.mkv"
        result = SerializeLogRecord._redact_sensitive(msg)
        self.assertEqual(msg, result)

    def test_existing_password_redaction_preserved(self):
        result = SerializeLogRecord._redact_sensitive("-u myuser,secretpass123")
        self.assertNotIn("secretpass123", result)
        self.assertIn("**REDACTED**", result)
