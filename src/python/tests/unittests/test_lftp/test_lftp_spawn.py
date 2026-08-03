import re
import unittest
from unittest.mock import MagicMock, patch

import pexpect

import lftp.lftp as lftp_mod
from lftp import Lftp, LftpError


class FakeSpawn:
    def __init__(self, command, args, env=None, dimensions=None, get_responses=None, password_prompt=False,
                 password_response_exception=None, open_exception=None, open_before=b"",
                 repeated_password_prompt=False, restore_echo_exception=None):
        self.command = command
        self.args = list(args)
        self.env = env
        self.dimensions = dimensions
        self.sendlines = []
        self._get_responses = get_responses or {}
        self._last_command = None
        self._password_prompt = password_prompt
        self._password_response_exception = password_response_exception
        self._open_exception = open_exception
        self._open_before = open_before
        self._repeated_password_prompt = repeated_password_prompt
        self._restore_echo_exception = restore_echo_exception
        self.before = b""
        self.after = b""
        self.closed = False
        self.close_force_calls = []
        self.echo_calls = []

    def isalive(self):
        return not self.closed

    def sendline(self, command):
        self.sendlines.append(command)
        self._last_command = command

    def setecho(self, enabled):
        self.echo_calls.append(enabled)
        if enabled and self._restore_echo_exception is not None:
            raise self._restore_echo_exception

    def expect(self, pattern, timeout=None):
        if isinstance(pattern, list):
            if self._last_command and self._last_command.startswith("open ") and self._open_exception is not None:
                self.before = self._open_before
                raise self._open_exception
            if self._last_command and self._last_command.startswith("open ") and self._password_prompt:
                self.before = b"Password:"
                return 0
            if self._last_command and self._password_prompt and not self._last_command.startswith(("set ", "open ")):
                if self._password_response_exception is not None:
                    raise self._password_response_exception
                if self._repeated_password_prompt:
                    self.before = b"Password:"
                    return 0
            return 1
        if self._last_command and self._last_command.startswith("set -a | grep "):
            setting = self._last_command[len("set -a | grep "):].strip()
            value = self._get_responses.get(setting, "true")
            self.before = "set {} {}".format(setting, value).encode("utf-8")
        else:
            self.before = b""
        return 0

    def close(self, force=False):
        self.close_force_calls.append(force)
        self.closed = True


def make_lftp(get_responses=None, password_prompt=False, password_response_exception=None, open_exception=None,
              open_before=b"", repeated_password_prompt=False, restore_echo_exception=None, **kwargs):
    created = []

    def fake_spawn(command, args, env=None, dimensions=None):
        fake = FakeSpawn(
            command, args, env=env, dimensions=dimensions, get_responses=get_responses,
            password_prompt=password_prompt, password_response_exception=password_response_exception,
            open_exception=open_exception, open_before=open_before,
            repeated_password_prompt=repeated_password_prompt, restore_echo_exception=restore_echo_exception,
        )
        created.append(fake)
        return fake

    with patch.object(lftp_mod.pexpect, "spawn", side_effect=fake_spawn, create=True):
        try:
            lftp = Lftp(**kwargs)
        finally:
            if created:
                make_lftp.last_fake = created[-1]
    return lftp, created[-1]


def sent_settings(fake):
    settings = {}
    for line in fake.sendlines:
        match = re.match(r"^set (?P<name>\S+) (?P<value>.+)$", line)
        if match and not line.startswith("set -a | grep "):
            settings[match.group("name")] = match.group("value")
    return settings


class TestLftpSpawn(unittest.TestCase):
    def test_pre_secret_password_prompt_pattern_requires_exact_echoed_open_command_and_buffer_end(self):
        lftp = Lftp.__new__(Lftp)
        open_command = 'open -p 21210 --user "alice" "ftp://127.0.0.1"'
        pattern = lftp._Lftp__password_prompt_pattern(open_command)
        observed = open_command + "\r\n\x1b[?2004l\rPassword: "

        self.assertIsNotNone(re.search(pattern, observed))
        self.assertIsNotNone(re.search(pattern, " " + observed))
        self.assertIsNotNone(re.search(pattern, open_command + "\r\nPassword: "))
        sftp_open_command = 'open -p 22 --user "alice" "sftp://127.0.0.1"'
        self.assertIsNotNone(re.search(
            lftp._Lftp__password_prompt_pattern(sftp_open_command),
            " " + sftp_open_command + "\r\n\x1b[?2004l\rPassword: ",
        ))
        self.assertIsNotNone(re.search(
            pattern,
            open_command + "\r\n\x1b[?2004l\rbob@host's password:\t",
        ))
        self.assertIsNotNone(re.search(
            pattern,
            " " + open_command + "\r\nbob@host's password:\t",
        ))
        for banner in (
            "banner\r\nPassword: ",
            "banner\r\nPassword: hint",
            "banner\x1b[?2004l\rPassword: ",
            "remote\r\n" + observed,
            open_command + " --forged\r\n\x1b[?2004l\rPassword: ",
            observed + "hint",
        ):
            with self.subTest(banner=repr(banner)):
                self.assertIsNone(re.search(pattern, banner))

    def test_post_secret_password_response_pattern_requires_exact_prompt_at_buffer_end(self):
        pattern = Lftp._Lftp__PASSWORD_RESPONSE_PATTERN

        self.assertIsNotNone(re.search(pattern, "Password: "))
        self.assertIsNotNone(re.search(pattern, "bob@host's password:\t"))
        self.assertIsNone(re.search(pattern, "Password: hint"))

    def test_endpoint_prompt_pattern_escapes_metacharacters_and_ipv6(self):
        pattern = Lftp._Lftp__endpoint_prompt_pattern("user+name", "[2001:db8::1]")

        self.assertIsNotNone(re.search(pattern, "lftp user+name@[2001:db8::1]:~>"))
        self.assertIsNone(re.search(pattern, "lftp userrname@x2001:db8::1:~>"))

    def test_key_auth_initialization_uses_escaped_endpoint_prompt_pattern(self):
        lftp, _fake = make_lftp(
            address="[2001:db8::1]", port=22, user="user+name", password=None,
        )

        self.assertEqual(
            Lftp._Lftp__endpoint_prompt_pattern("user+name", "[2001:db8::1]"),
            lftp._Lftp__expect_pattern,
        )

    @patch("lftp.lftp.pexpect.spawn", create=True)
    def test_lftp_constructor_rejects_control_character_password_before_spawn(self, spawn):
        for password in ("line\nbreak", "carriage\rreturn", "tab\tvalue", "null\x00value", "delete\x7fvalue", "escape\x1bvalue"):
            with self.subTest(password=repr(password)):
                with self.assertRaises(LftpError) as error:
                    Lftp(address="host.example.com", port=22, user="bob", password=password)
                self.assertIn("control characters", str(error.exception))
        spawn.assert_not_called()

    def test_lftp_spawn_sftp_default_uses_prompt_flow_without_password_in_spawn(self):
        _lftp, fake = make_lftp(
            address="host.example.com",
            port=22,
            user="bob",
            password='special,password:with spaces and "quotes" ü',
            password_prompt=True,
        )

        self.assertEqual([], fake.args)
        self.assertNotIn('special,password:with spaces and "quotes" ü', repr(fake.args))
        self.assertNotIn('special,password:with spaces and "quotes" ü', repr(fake.env))
        self.assertEqual(
            'open -p 22 --user "bob" "sftp://host.example.com"', fake.sendlines[-2]
        )
        self.assertEqual('special,password:with spaces and "quotes" ü', fake.sendlines[-1])
        self.assertEqual([False, True], fake.echo_calls)
        self.assertTrue(all('special,password:with spaces and "quotes" ü' not in command for command in fake.sendlines[:-1]))
        self.assertIsNone(_lftp._Lftp__password)

        settings = sent_settings(fake)
        self.assertEqual("1", settings.get("sftp:auto-confirm"))
        self.assertEqual("2", settings.get("pget:save-status"))
        self.assertEqual("false", settings.get("cmd:save-rl-history"))
        self.assertEqual("false", settings.get("cmd:save-cwd-history"))
        self.assertNotIn("ftp:ssl-force", settings)
        self.assertNotIn("ssl:verify-certificate", settings)

    def test_lftp_spawn_ftps_uses_prompt_flow_after_tls_settings(self):
        _lftp, fake = make_lftp(
            address="host.example.com",
            port=22,
            user="bob",
            password="secret",
            protocol="ftps",
            remote_ftp_port=2121,
            ssl_verify_certificate=False,
            password_prompt=True,
        )

        self.assertEqual([], fake.args)
        self.assertIn('open -p 2121 --user "bob" "ftp://host.example.com"', fake.sendlines)
        self.assertEqual("secret", fake.sendlines[-1])
        self.assertEqual([False, True], fake.echo_calls)

        settings = sent_settings(fake)
        self.assertEqual("true", settings.get("ftp:ssl-force"))
        self.assertEqual("true", settings.get("ftp:ssl-protect-data"))
        self.assertEqual("false", settings.get("ssl:verify-certificate"))
        self.assertEqual("TLS", settings.get("ftp:ssl-auth"))
        self.assertEqual("true", settings.get("ftp:passive-mode"))
        self.assertEqual("2", settings.get("pget:save-status"))
        self.assertNotIn("sftp:auto-confirm", settings)
        self.assertLess(
            fake.sendlines.index("set ftp:ssl-force true"),
            fake.sendlines.index('open -p 2121 --user "bob" "ftp://host.example.com"'),
        )

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
                password_prompt=True,
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

    def test_lftp_spawn_key_auth_does_not_send_a_password(self):
        _lftp, fake = make_lftp(
            address="host.example.com", port=22, user="bob", password=None,
        )

        self.assertEqual(["-p", "22", "-u", "bob,", "sftp://host.example.com"], fake.args)
        self.assertNotIn('open -p 22 --user "bob" "sftp://host.example.com"', fake.sendlines)
        self.assertEqual([], fake.echo_calls)

    def test_lftp_secure_spawn_removes_ambient_password_variable(self):
        with patch.dict(lftp_mod.os.environ, {"LFTP_PASSWORD": "ambient-secret", "OTHER": "keep"}, clear=True):
            _lftp, fake = make_lftp(
                address="host.example.com", port=22, user="bob", password="prompt-secret", password_prompt=True,
            )

        self.assertNotIn("LFTP_PASSWORD", fake.env)
        self.assertEqual("keep", fake.env["OTHER"])
        self.assertNotIn("prompt-secret", repr(fake.args))
        self.assertNotIn("prompt-secret", repr(fake.env))

    def test_lftp_legacy_mode_restores_exact_password_argv(self):
        with self.assertLogs("Lftp", level="WARNING") as logs:
            _lftp, fake = make_lftp(
                address="host.example.com", port=22, user="bob", password="secret",
                use_legacy_lftp_password_argv=True,
            )

        self.assertEqual(["-p", "22", "-u", "bob,secret", "sftp://host.example.com"], fake.args)
        self.assertTrue(any("legacy lftp password argv" in entry.lower() for entry in logs.output))

    def test_lftp_password_failure_restores_echo_and_redacts_output(self):
        secret = "special,password:with spaces"
        with self.assertRaises(LftpError) as error:
            make_lftp(
                address="host.example.com", port=22, user="bob", password=secret,
                password_prompt=True,
                password_response_exception=pexpect.exceptions.TIMEOUT("timeout"),
            )

        self.assertNotIn(secret, str(error.exception))
        self.assertEqual([False, True], make_lftp.last_fake.echo_calls)
        self.assertEqual([True], make_lftp.last_fake.close_force_calls)

    def test_repeated_password_prompt_fails_without_waiting_for_timeout(self):
        secret = "prompt-secret"
        with self.assertRaises(LftpError) as error:
            make_lftp(
                address="host.example.com", port=22, user="bob", password=secret,
                password_prompt=True, repeated_password_prompt=True,
            )

        self.assertIn("rejected the password", str(error.exception))
        self.assertNotIn(secret, str(error.exception))
        self.assertEqual([False, True], make_lftp.last_fake.echo_calls)
        self.assertEqual([True], make_lftp.last_fake.close_force_calls)

    def test_echo_restore_failure_forces_close_and_sanitizes_error(self):
        secret = "prompt-secret"
        with self.assertRaises(LftpError) as error:
            make_lftp(
                address="host.example.com", port=22, user="bob", password=secret,
                password_prompt=True, restore_echo_exception=OSError("echo failure"),
            )

        self.assertIn("echo", str(error.exception).lower())
        self.assertNotIn(secret, str(error.exception))
        self.assertEqual([False, True], make_lftp.last_fake.echo_calls)
        self.assertEqual([True], make_lftp.last_fake.close_force_calls)

    def test_lftp_initialization_cleanup_clears_password_and_forces_close(self):
        lftp = Lftp.__new__(Lftp)
        lftp._Lftp__password = "prompt-secret"
        lftp._Lftp__process = MagicMock()
        lftp._Lftp__process.isalive.return_value = True
        lftp.logger = MagicMock()

        lftp._Lftp__cleanup_failed_initialization()

        self.assertIsNone(lftp._Lftp__password)
        lftp._Lftp__process.close.assert_called_once_with(force=True)

    def test_lftp_password_flow_preserves_ssh_host_key_prompt_failure(self):
        secret = "prompt-secret"
        timeout = pexpect.exceptions.TIMEOUT("timeout")
        with self.assertRaises(LftpError) as error:
            make_lftp(
                address="host.example.com", port=22, user="bob", password=secret,
                open_exception=timeout,
                open_before=(
                    b"The authenticity of host 'host.example.com' can't be established.\n"
                    b"Are you sure you want to continue connecting (yes/no/[fingerprint])?"
                ),
            )

        self.assertIn("SSH host-key prompt", str(error.exception))
        self.assertNotIn(secret, str(error.exception))
        self.assertEqual([True], make_lftp.last_fake.close_force_calls)

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
