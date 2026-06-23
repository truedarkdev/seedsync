import re
import unittest
from unittest.mock import MagicMock, patch

import lftp.lftp as lftp_mod
from lftp import Lftp, LftpError


class FakeSpawn:
    def __init__(self, command, args, env=None, dimensions=None, get_responses=None):
        self.command = command
        self.args = list(args)
        self.env = env
        self.dimensions = dimensions
        self.sendlines = []
        self._get_responses = get_responses or {}
        self._last_command = None
        self.before = b""
        self.after = b""
        self.closed = False

    def isalive(self):
        return not self.closed

    def sendline(self, command):
        self.sendlines.append(command)
        self._last_command = command

    def expect(self, pattern, timeout=None):
        if self._last_command and self._last_command.startswith("set -a | grep "):
            setting = self._last_command[len("set -a | grep "):].strip()
            value = self._get_responses.get(setting, "true")
            self.before = "set {} {}".format(setting, value).encode("utf-8")
        else:
            self.before = b""
        return 0

    def close(self, force=False):
        self.closed = True


def make_lftp(get_responses=None, **kwargs):
    created = []

    def fake_spawn(command, args, env=None, dimensions=None):
        fake = FakeSpawn(command, args, env=env, dimensions=dimensions, get_responses=get_responses)
        created.append(fake)
        return fake

    with patch.object(lftp_mod.pexpect, "spawn", side_effect=fake_spawn, create=True):
        lftp = Lftp(**kwargs)
    return lftp, created[-1]


def sent_settings(fake):
    settings = {}
    for line in fake.sendlines:
        match = re.match(r"^set (?P<name>\S+) (?P<value>.+)$", line)
        if match and not line.startswith("set -a | grep "):
            settings[match.group("name")] = match.group("value")
    return settings


class TestLftpSpawn(unittest.TestCase):
    def test_lftp_spawn_sftp_default_preserves_existing_endpoint_and_settings(self):
        _lftp, fake = make_lftp(
            address="host.example.com",
            port=22,
            user="bob",
            password=None,
        )

        self.assertEqual("22", fake.args[fake.args.index("-p") + 1])
        self.assertIn("sftp://host.example.com", fake.args)
        self.assertNotIn("ftp://host.example.com", fake.args)
        self.assertEqual("bob,", fake.args[fake.args.index("-u") + 1])

        settings = sent_settings(fake)
        self.assertEqual("1", settings.get("sftp:auto-confirm"))
        self.assertEqual("2", settings.get("pget:save-status"))
        self.assertNotIn("ftp:ssl-force", settings)
        self.assertNotIn("ssl:verify-certificate", settings)

    def test_lftp_spawn_ftps_uses_ftp_scheme_port_and_tls_settings(self):
        _lftp, fake = make_lftp(
            address="host.example.com",
            port=22,
            user="bob",
            password="secret",
            protocol="ftps",
            remote_ftp_port=2121,
            ssl_verify_certificate=False,
        )

        self.assertEqual("2121", fake.args[fake.args.index("-p") + 1])
        self.assertIn("ftp://host.example.com", fake.args)
        self.assertNotIn("sftp://host.example.com", fake.args)
        self.assertEqual("bob,secret", fake.args[fake.args.index("-u") + 1])

        settings = sent_settings(fake)
        self.assertEqual("true", settings.get("ftp:ssl-force"))
        self.assertEqual("true", settings.get("ftp:ssl-protect-data"))
        self.assertEqual("false", settings.get("ssl:verify-certificate"))
        self.assertEqual("TLS", settings.get("ftp:ssl-auth"))
        self.assertEqual("true", settings.get("ftp:passive-mode"))
        self.assertEqual("2", settings.get("pget:save-status"))
        self.assertNotIn("sftp:auto-confirm", settings)

    def test_lftp_spawn_ftps_can_verify_certificate(self):
        with patch.object(lftp_mod.logging.getLogger("Lftp"), "warning") as warn:
            _lftp, fake = make_lftp(
                address="host",
                port=22,
                user="u",
                password="secret",
                protocol="ftps",
                remote_ftp_port=21,
                ssl_verify_certificate=True,
            )

        settings = sent_settings(fake)
        self.assertEqual("true", settings.get("ssl:verify-certificate"))
        warned_mitm = any(
            "man-in-the-middle" in str(call.args[0]).lower()
            for call in warn.call_args_list
            if call.args
        )
        self.assertFalse(warned_mitm)

    def test_lftp_spawn_rejects_invalid_protocol(self):
        with self.assertRaises(ValueError):
            make_lftp(address="host", port=22, user="u", password=None, protocol="ftp")

    def test_lftp_ftps_fails_closed_when_tls_is_not_forced(self):
        with self.assertRaises(LftpError) as error:
            make_lftp(
                get_responses={"ftp:ssl-force": "false"},
                address="host",
                port=22,
                user="u",
                password="secret",
                protocol="ftps",
                remote_ftp_port=21,
            )

        self.assertIn("ssl-force", str(error.exception))

    def test_lftp_error_detection_includes_ftps_failures(self):
        self.assertTrue(Lftp._Lftp__detect_errors_from_output("Fatal error: gnutls_handshake failed"))
        self.assertTrue(Lftp._Lftp__detect_errors_from_output("Certificate verification failed"))
        self.assertTrue(Lftp._Lftp__detect_errors_from_output("Login failed: 530 Login incorrect"))

    def test_pending_errors_redact_credentialed_urls(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__expect_pattern = "prompt>"
        lftp._Lftp__timeout = 30
        lftp._Lftp__log_command_output = False
        lftp._Lftp__pending_error = None
        lftp._Lftp__last_command_timed_out = False
        process = MagicMock()
        process.isalive.return_value = True
        process.before = b"mirror: Access failed ftps://bob:pa:ss@host/path"
        process.after = b"prompt>"
        process.expect.return_value = None
        lftp._Lftp__process = process

        lftp._Lftp__run_command("mirror")

        self.assertEqual("mirror: Access failed ftps://**REDACTED**@**REDACTED**/path", lftp._Lftp__pending_error)

    def test_lftp_error_redacts_credentialed_urls_with_reserved_characters(self):
        error = LftpError("mirror: Access failed ftp://bob:pa/ss@host/path")

        self.assertEqual("mirror: Access failed ftp://**REDACTED**@**REDACTED**/path", str(error))
