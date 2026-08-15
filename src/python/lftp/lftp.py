# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import re
import os
import stat
import time
from functools import wraps
from typing import Any, Callable, Union, List, Optional, Dict, Iterable, Concatenate, ParamSpec, Protocol, TypeVar

# 3rd party libs
import pexpect

# my libs
from common import AppError
from common.config import Checkers
from common.exclude_patterns import ExactPathExclusion, partition_transfer_exclusions
from common.redaction import redact_sensitive_text
from .job_status_parser import LftpJobStatus, LftpJobStatusParser, LftpJobStatusParserError


# How many status errors are allowed before error propagates out
MAX_CONSECUTIVE_STATUS_ERRORS = 10
MAX_KILL_MATCH_ATTEMPTS = 20
STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS = 1.0
redact_credentials = redact_sensitive_text
_P = ParamSpec("_P")
_R = TypeVar("_R")


class _PathPair(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def remote_path(self) -> str: ...
    @property
    def local_path(self) -> str: ...


class LftpError(AppError):
    """
    Custom exception that describes the failure of the lftp command
    """
    def __init__(self, message: Any = ""):
        super().__init__(redact_sensitive_text("" if message is None else message))


class Lftp:
    """
    Lftp command utility
    """
    __SET_NUM_PARALLEL_FILES = "mirror:parallel-transfer-count"
    __SET_NUM_CONNECTIONS_PGET = "pget:default-n"
    __SET_NUM_CONNECTIONS_MIRROR = "mirror:use-pget-n"
    __SET_NUM_MAX_TOTAL_CONNECTIONS = "net:connection-limit"
    __SET_RATE_LIMIT = "net:limit-rate"
    __SET_NET_SOCKET_BUFFER = "net:socket-buffer"
    __SET_MIN_CHUNK_SIZE = "pget:min-chunk-size"
    __SET_PGET_SAVE_STATUS = "pget:save-status"
    __SET_NUM_PARALLEL_JOBS = "cmd:queue-parallel"
    __SET_MOVE_BACKGROUND_ON_EXIT = "cmd:move-background"
    __SET_COMMAND_AT_EXIT = "cmd:at-exit"
    __SET_USE_TEMP_FILE = "xfer:use-temp-file"
    __SET_TEMP_FILE_NAME = "xfer:temp-file-name"
    __SET_XFER_VERIFY = "xfer:verify"
    __SET_XFER_VERIFY_COMMAND = "xfer:verify-command"
    __SET_SFTP_AUTO_CONFIRM = "sftp:auto-confirm"
    __SET_SFTP_CONNECT_PROGRAM = "sftp:connect-program"
    __SET_SFTP_SET_PERMISSIONS = "sftp:set-permissions"
    __SET_FTP_SSL_FORCE = "ftp:ssl-force"
    __SET_FTP_SSL_PROTECT_DATA = "ftp:ssl-protect-data"
    __SET_SSL_VERIFY_CERTIFICATE = "ssl:verify-certificate"
    __SET_FTP_SSL_AUTH = "ftp:ssl-auth"
    __SET_FTP_PASSIVE_MODE = "ftp:passive-mode"
    __SET_CMD_SAVE_HISTORY = "cmd:save-rl-history"
    __SET_CMD_SAVE_CWD = "cmd:save-cwd-history"
    __INITIAL_PROMPT_PATTERN = r"lftp.*>[ \t]*"
    __PASSWORD_RESPONSE_PATTERN = r"(?im)^(?:password:|\S+@\S+'s password:)[ \t]*\Z"
    __PGET_STATUS_FILE_SUFFIX = ".lftp-pget-status"
    __LFTP_TEMP_FILE_SUFFIX = ".lftp"

    @staticmethod
    def __has_valid_umask() -> bool:
        umask_value = os.environ.get("UMASK", "")
        if not umask_value:
            return False

        return all(character in "01234567" for character in umask_value)

    @staticmethod
    def __decode_spawn_output(output: Any) -> str:
        if output is None or output is pexpect.EOF or output is pexpect.TIMEOUT:
            return ""
        if isinstance(output, bytes):
            return output.decode("utf8", "replace")
        return str(output)

    @staticmethod
    def __quote_command_argument(value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise LftpError("LFTP command arguments cannot contain control characters")
        escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
        return "\"{}\"".format(escaped)

    @staticmethod
    def __validate_password(password: Optional[str]) -> None:
        if password is not None and any(ord(character) < 32 or ord(character) == 127 for character in password):
            raise LftpError("LFTP password cannot contain control characters")

    @staticmethod
    def __endpoint_prompt_pattern(user: str, address: str) -> str:
        return r"lftp {}@{}:.*>[ \t]*".format(re.escape(user), re.escape(address))

    def __password_prompt_pattern(self, open_command: str) -> str:
        return (
            r"(?i)\A[ \t]*{}\r\n(?:(?:\x1b\[\?2004[hl])+\r)?"
            r"(?:password:|\S+@\S+'s password:)[ \t]*\Z"
        ).format(re.escape(open_command))

    def __init__(self,
                 address: str,
                 port: int,
                 user: str,
                 password: Optional[str],
                 protocol: str = "sftp",
                 remote_ftp_port: Optional[int] = None,
                 ssl_verify_certificate: bool = False,
                 use_legacy_lftp_password_argv: bool = False):
        Lftp.__validate_password(password)
        self.__user = user
        self.__password = password
        self.__address = address
        self.__protocol = protocol.strip().lower()
        if self.__protocol not in ("sftp", "ftps"):
            raise ValueError("Invalid lftp protocol '{}': must be 'sftp' or 'ftps'".format(protocol))
        self.__scheme = "ftp" if self.__protocol == "ftps" else "sftp"
        self.__transfer_port = remote_ftp_port if self.__protocol == "ftps" and remote_ftp_port is not None else port
        self.__ssl_verify_certificate = ssl_verify_certificate
        self.__use_legacy_lftp_password_argv = use_legacy_lftp_password_argv
        self.__base_remote_dir_path = ""
        self.__base_local_dir_path = ""
        self.logger = logging.getLogger("Lftp")
        self.__expect_pattern = Lftp.__endpoint_prompt_pattern(self.__user, self.__address)
        self.__job_status_parser = LftpJobStatusParser()
        self.__timeout = 30  # in seconds
        self.__consecutive_status_errors = 0
        self.__path_pairs_by_id: Dict[str, Dict[str, str]] = {}
        self.__last_command_timed_out = False
        self.__last_status_poll_healthy = True
        self.__status_poll_needs_connection_grace = False

        self.__log_command_output = False
        self.__pending_error = None

        spawn_env = os.environ.copy()
        spawn_env["COLUMNS"] = "10000"
        # Do not allow an ambient lftp variable to become a second credential channel.
        spawn_env.pop("LFTP_PASSWORD", None)
        args: list[str] = []
        password_in_argv = bool(self.__password) and self.__use_legacy_lftp_password_argv
        if password_in_argv:
            self.logger.warning(
                "Using legacy lftp password argv mode; the remote password is visible to same-host "
                "process inspection. Disable Lftp.use_legacy_lftp_password_argv after compatibility testing."
            )
            args = [
                "-p", str(self.__transfer_port),
                "-u", "{},{}".format(self.__user, self.__password if self.__password else ""),
                "{}://{}".format(self.__scheme, self.__address)
            ]
        if not self.__password:
            # Key/no-password auth must let lftp connect with its normal non-secret
            # user argv; `open --user` otherwise prompts for a password on lftp 4.9.
            args = [
                "-p", str(self.__transfer_port),
                "-u", "{},".format(self.__user),
                "{}://{}".format(self.__scheme, self.__address),
            ]
        self.__process = pexpect.spawn("/usr/bin/lftp", args, env=spawn_env, dimensions=(24, 10000))  # type: ignore[arg-type]
        try:
            if password_in_argv or not self.__password:
                try:
                    self.__process.expect(self.__expect_pattern)
                except pexpect.exceptions.TIMEOUT:
                    out = self.__decode_spawn_output(self.__process.before).strip()
                    if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "startup"):
                        raise
                self.__setup()
                self.__password = None
            else:
                self.__process.expect(Lftp.__INITIAL_PROMPT_PATTERN)
                self.__expect_pattern = Lftp.__INITIAL_PROMPT_PATTERN
                self.__setup()
                self.__connect_with_native_password_prompt()
            # LFTP writes background progress to the same PTY that accepts
            # control commands. Terminal echo can splice `jobs -v` or queue
            # commands into those progress lines, producing a corrupt but
            # superficially parseable status snapshot. Commands are already
            # logged explicitly when verbose logging is enabled, so their PTY
            # echo is neither required nor safe.
            self.__process.setecho(False)
        except Exception:
            self.__cleanup_failed_initialization()
            raise

    def __cleanup_failed_initialization(self) -> None:
        self.__password = None
        try:
            if self.__process.isalive():
                self.__process.close(force=True)
        except (OSError, pexpect.exceptions.ExceptionPexpect):
            self.logger.warning("Unable to close lftp process after initialization failure")

    def __connect_with_native_password_prompt(self):
        """Open after protocol safeguards are set, answering a native password prompt without echo."""
        open_command = "open -p {} --user {} {}".format(
            self.__transfer_port,
            Lftp.__quote_command_argument(self.__user),
            Lftp.__quote_command_argument("{}://{}".format(self.__scheme, self.__address)),
        )
        self.__process.sendline(open_command)
        self.__expect_pattern = Lftp.__endpoint_prompt_pattern(self.__user, self.__address)
        try:
            match = self.__process.expect(
                [self.__password_prompt_pattern(open_command), self.__expect_pattern], timeout=self.__timeout
            )
        except pexpect.exceptions.TIMEOUT:
            out = self.__redact_password(self.__decode_spawn_output(self.__process.before).strip())
            if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "opening connection"):
                raise LftpError("Lftp process did not connect: {}".format(out))
            raise AssertionError("unreachable")
        except pexpect.exceptions.EOF:
            out = self.__redact_password(self.__decode_spawn_output(self.__process.before).strip())
            raise LftpError("Lftp process terminated while opening connection: {}".format(out))

        if match != 0:
            self.__password = None
            return
        if not self.__password:
            raise LftpError("Lftp requested a password but none is configured")

        password = self.__password
        try:
            # The password is a prompt response, never an lftp command. Disable PTY echo
            # first so it cannot enter pexpect output, readline history, or verbose logs.
            self.__process.setecho(False)
            self.__process.sendline(password)
            match = self.__process.expect(
                [Lftp.__PASSWORD_RESPONSE_PATTERN, self.__expect_pattern], timeout=self.__timeout
            )
            if match == 0:
                raise LftpError("Lftp rejected the password")
        except pexpect.exceptions.TIMEOUT:
            out = self.__redact_password(self.__decode_spawn_output(self.__process.before).strip())
            if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "submitting password"):
                raise LftpError("Lftp process did not accept password: {}".format(out))
            raise AssertionError("unreachable")
        except pexpect.exceptions.EOF:
            out = self.__redact_password(self.__decode_spawn_output(self.__process.before).strip())
            raise LftpError("Lftp process terminated while submitting password: {}".format(out))
        finally:
            self.__password = None
            try:
                self.__process.setecho(True)
            except (OSError, pexpect.exceptions.ExceptionPexpect):
                try:
                    if self.__process.isalive():
                        self.__process.close(force=True)
                except (OSError, pexpect.exceptions.ExceptionPexpect):
                    pass
                raise LftpError("Unable to restore lftp PTY echo after password prompt")

    def __redact_password(self, value: str) -> str:
        redacted = redact_credentials(value)
        if self.__password:
            return redacted.replace(self.__password, "**REDACTED**")
        return redacted

    def set_verbose_logging(self, verbose: bool):
        self.__log_command_output = verbose

    def __setup(self):
        """
        Setup the lftp instance with default settings
        :return:
        """
        # Set to kill on exit to prevent a zombie process
        self.__set(Lftp.__SET_COMMAND_AT_EXIT, "\"kill all\"")
        # A prompt response is not a command, but disable lftp persistence before opening
        # the connection so no interactive state is written while credentials are in use.
        self.__set(Lftp.__SET_CMD_SAVE_HISTORY, "false")
        self.__set(Lftp.__SET_CMD_SAVE_CWD, "false")
        if self.__protocol == "ftps":
            self.__setup_ftps()
        else:
            self.__setup_sftp()
        # Keep pget status snapshots fresher to reduce valid stop/resume rollback.
        self.__set(Lftp.__SET_PGET_SAVE_STATUS, "2")

    def __setup_sftp(self):
        # Auto-add server to known host file
        self.sftp_auto_confirm = True
        # Let a valid explicit UMASK control downloaded file permissions.
        if Lftp.__has_valid_umask():
            self.__set(Lftp.__SET_SFTP_SET_PERMISSIONS, "false")

    def __setup_ftps(self):
        self.__set(Lftp.__SET_FTP_SSL_FORCE, "true")
        self.__set(Lftp.__SET_FTP_SSL_PROTECT_DATA, "true")
        self.__set(
            Lftp.__SET_SSL_VERIFY_CERTIFICATE,
            "true" if self.__ssl_verify_certificate else "false"
        )
        if not self.__ssl_verify_certificate:
            self.logger.warning(
                "FTPS TLS certificate verification is disabled; the connection is encrypted "
                "but not authenticated against man-in-the-middle attacks."
            )
        self.__set(Lftp.__SET_FTP_SSL_AUTH, "TLS")
        self.__set(Lftp.__SET_FTP_PASSIVE_MODE, "true")
        self.__verify_ftps_ssl_forced()

    def __verify_ftps_ssl_forced(self):
        try:
            ssl_force = Lftp.__to_bool(self.__get(Lftp.__SET_FTP_SSL_FORCE))
        except LftpError as e:
            raise LftpError("FTPS fail-closed check could not read ftp:ssl-force: {}".format(e)) from e
        if not ssl_force:
            raise LftpError(
                "FTPS fail-closed check failed: ftp:ssl-force is not enabled; "
                "refusing to transfer over cleartext FTP"
            )

    @staticmethod
    def with_check_process(
        method: Callable[Concatenate["Lftp", _P], _R]
    ) -> Callable[Concatenate["Lftp", _P], _R]:
        """
        Decorator that checks for a valid process before executing
        the decorated method
        :param method:
        :return:
        """
        @wraps(method)
        def wrapper(inst: "Lftp", *args: _P.args, **kwargs: _P.kwargs) -> _R:
            if not inst.__process.isalive():
                raise LftpError("lftp process is not running")
            return method(inst, *args, **kwargs)
        return wrapper

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("Lftp")
        self.__job_status_parser.set_base_logger(self.logger)

    def set_base_remote_dir_path(self, base_remote_dir_path: str):
        self.__base_remote_dir_path = base_remote_dir_path

    def set_base_local_dir_path(self, base_local_dir_path: str):
        self.__base_local_dir_path = base_local_dir_path

    def set_path_pairs(self, path_pairs: Iterable[_PathPair]):
        self.__path_pairs_by_id = {
            pair.id: {
                "name": pair.name,
                "remote_path": pair.remote_path,
                "local_path": pair.local_path
            } for pair in path_pairs
        }

    def raise_pending_error(self):
        """
        Raise any pending errors
        Errors show up late after a command is executed
        This method raises any errors that were detected while executing the next command
        :return:
        """
        if self.__pending_error:
            error = self.__pending_error
            self.__pending_error = None
            raise LftpError(error)

    @staticmethod
    def __detect_ssh_host_key_prompt(out: str) -> bool:
        prompt_fragments = [
            "The authenticity of host ",
            "Are you sure you want to continue connecting (yes/no",
        ]
        return any(fragment in out for fragment in prompt_fragments)

    def __raise_lftp_error_for_ssh_host_key_prompt(self, out: str, context: str) -> bool:
        if self.__detect_ssh_host_key_prompt(out):
            error = "Lftp stalled on SSH host-key prompt during {}: {}".format(context, out)
            self.logger.error(error)
            raise LftpError(error)
        return False

    @staticmethod
    def __normalize_output(out: str) -> str:
        # lftp in an interactive PTY can leak bracketed-paste toggle lines into command output.
        bracketed_paste_toggle_lines = {
            "\x1b[?2004h",
            "\x1b[?2004l",
        }
        lines = [
            line for line in out.splitlines()
            if line.strip() not in bracketed_paste_toggle_lines
        ]
        return "\n".join(lines).strip()

    def __ensure_prompt_ready(self, context: str):
        try:
            self.__process.expect(self.__expect_pattern, timeout=1)
        except pexpect.exceptions.TIMEOUT:
            out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
            if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, context):
                self.__process.sendline()
                try:
                    self.__process.expect(self.__expect_pattern, timeout=3)
                except pexpect.exceptions.TIMEOUT:
                    retry_out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                    if not self.__raise_lftp_error_for_ssh_host_key_prompt(retry_out, context):
                        self.logger.warning("Lftp timeout exception")
                        raise LftpError("Lftp process is not ready for {}: {}".format(context, retry_out))
                except pexpect.exceptions.EOF:
                    retry_out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                    self.logger.error("Lftp process died unexpectedly (EOF) before {}".format(context))
                    raise LftpError("Lftp process terminated before {}: {}".format(context, retry_out))
        except pexpect.exceptions.EOF:
            out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
            self.logger.error("Lftp process died unexpectedly (EOF) before {}".format(context))
            raise LftpError("Lftp process terminated before {}: {}".format(context, out))

    @with_check_process
    def __run_command(self,
                      command: str,
                      timeout_seconds: Optional[int] = None,
                      require_prompt_ready: bool = True,
                      status_poll: bool = False,
                      low_latency: bool = False) -> str:
        self.__last_command_timed_out = False
        restore_delaybeforesend = None
        restore_delayafterread = None
        out = ""
        status_poll_timeout_seconds = None
        log_command_output = self.__log_command_output and not status_poll
        if status_poll:
            status_poll_timeout_seconds = STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS if timeout_seconds == 0 else timeout_seconds
        if status_poll or low_latency:
            restore_delaybeforesend = getattr(self.__process, "delaybeforesend", None)
            restore_delayafterread = getattr(self.__process, "delayafterread", None)
            if restore_delaybeforesend is not None:
                self.__process.delaybeforesend = 0
            if restore_delayafterread is not None:
                self.__process.delayafterread = 0
        try:
            if require_prompt_ready:
                self.__ensure_prompt_ready("running command")
            if log_command_output:
                self.logger.debug("command: {}".format(command.encode('utf8', 'surrogateescape')))
            try:
                if status_poll:
                    self.__process.send(command + "\n")
                else:
                    self.__process.sendline(command)
            except pexpect.exceptions.TIMEOUT:
                if status_poll:
                    self.__last_command_timed_out = True
                    self.logger.warning("Lftp timeout exception")
                    return ""
                raise
            except pexpect.exceptions.EOF:
                if status_poll:
                    self.__last_command_timed_out = True
                    self.logger.error("Lftp process died unexpectedly (EOF) while sending status command")
                    return ""
                raise
            timeout_seconds = self.__timeout if timeout_seconds is None else timeout_seconds
            prompt_reached = False
            recovered_output_preserved = False
            try:
                if status_poll:
                    try:
                        status_poll_timeout_seconds = STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS if timeout_seconds == 0 else timeout_seconds
                        status_poll_deadline = time.monotonic() + status_poll_timeout_seconds
                        while True:
                            try:
                                self.__process.expect(self.__expect_pattern, timeout=0)
                                prompt_reached = True
                                break
                            except pexpect.exceptions.TIMEOUT:
                                if time.monotonic() >= status_poll_deadline:
                                    break
                                time.sleep(0.01)
                            except pexpect.exceptions.EOF:
                                self.__last_command_timed_out = True
                                self.logger.error("Lftp process died unexpectedly (EOF)")
                                raise LftpError("Lftp process terminated: {}".format(
                                    self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                                ))
                    except pexpect.exceptions.ExceptionPexpect as exc:
                        self.__last_command_timed_out = True
                        self.logger.warning("Ignoring status poll failure: {}".format(exc))
                        return ""
                    except OSError as exc:
                        self.__last_command_timed_out = True
                        self.logger.warning("Ignoring status poll failure: {}".format(exc))
                        return ""
                    if not prompt_reached:
                        self.__last_command_timed_out = True
                else:
                    try:
                        self.__process.expect(self.__expect_pattern, timeout=timeout_seconds)
                    except pexpect.exceptions.TIMEOUT:
                        self.__last_command_timed_out = True
                        out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                        if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "running command"):
                            self.logger.warning("Lftp timeout exception")
                        pass
                    except pexpect.exceptions.EOF:
                        self.logger.error("Lftp process died unexpectedly (EOF)")
                        raise LftpError("Lftp process terminated: {}".format(
                            self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                        ))
            finally:
                out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))

                if log_command_output:
                    if status_poll:
                        self.logger.debug("status out ({} bytes, bounded)".format(len(out)))
                    else:
                        self.logger.debug("out ({} bytes):\n {}".format(len(out), redact_credentials(out)))
                    after = self.__decode_spawn_output(self.__process.after).strip()
                    self.logger.debug("after: {}".format(after))

            if status_poll and "Connecting..." in out:
                self.__status_poll_needs_connection_grace = True
            if status_poll and not prompt_reached and "Connecting..." in out:
                recovered_output_preserved = True
                try:
                    connecting_grace_timeout = max(status_poll_timeout_seconds or 0, 5.0)
                    self.__process.expect(self.__expect_pattern, timeout=connecting_grace_timeout)
                except pexpect.exceptions.TIMEOUT:
                    pass
                except pexpect.exceptions.EOF:
                    self.__last_command_timed_out = True
                    self.logger.error("Lftp process died unexpectedly (EOF) during status poll recovery")
                    raise LftpError("Lftp process terminated during status poll recovery")
                finally:
                    out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))

            if status_poll and not prompt_reached and not recovered_output_preserved and not self.__detect_errors_from_output(out):
                out = ""

            # let's try and detect some errors
            if self.__detect_errors_from_output(out):
                # we need to consume the actual output so that
                # it doesn't get passed onto next command
                error_out = out
                try:
                    self.__process.expect(self.__expect_pattern, timeout=timeout_seconds)
                except pexpect.exceptions.TIMEOUT:
                    out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                    if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "recovering from error"):
                        self.logger.warning("Lftp timeout exception")
                    pass
                except pexpect.exceptions.EOF:
                    self.logger.error("Lftp process died unexpectedly (EOF) during error recovery")
                    raise LftpError("Lftp process terminated during error recovery")
                finally:
                    out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                    if log_command_output:
                        self.logger.debug("retry out ({} bytes):\n {}".format(len(out), redact_credentials(out)))
                        after = self.__decode_spawn_output(self.__process.after).strip()
                        self.logger.debug("retry after: {}".format(after))
                    redacted_error_out = redact_credentials(error_out)
                    self.logger.error("Lftp detected error: {}".format(redacted_error_out))
                    # save pending error
                    self.__pending_error = redacted_error_out
            return out
        finally:
            if restore_delaybeforesend is not None:
                self.__process.delaybeforesend = restore_delaybeforesend
            if restore_delayafterread is not None:
                self.__process.delayafterread = restore_delayafterread

    @staticmethod
    def __detect_errors_from_output(out: str) -> bool:
        errors = [
            "get: Access failed",
            "pget: Access failed",
            "pget-chunk: Access failed",
            "mirror: Access failed",
            "Login failed:",
            "Fatal error: gnutls_handshake",
            "Fatal error: SSL_connect",
            "TLS/SSL connection error",
            "Certificate verification",
            "certificate verification",
            "AUTH TLS failed",
        ]
        for error in errors:
            if error in out:
                return True
        return False

    def __set(self, setting: str, value: str):
        """
        Set a setting in the lftp runtime
        :param setting:
        :param value:
        :return:
        """
        self.__run_command("set {} {}".format(setting, value), require_prompt_ready=False)  # type: ignore[arg-type]

    def __get(self, setting: str) -> str:
        """
        Get a setting from the lftp runtime
        :param setting:
        :return:
        """
        out = self.__run_command("set -a | grep {}".format(setting))  # type: ignore[arg-type]
        m = re.search(r"^set {} (.*)$".format(re.escape(setting)), out, re.MULTILINE)
        if not m or not m.group(1):
            raise LftpError("Failed to get setting '{}'. Output: '{}'".format(setting, out))
        return m.group(1).strip()

    @staticmethod
    def __to_bool(value: str) -> bool:
        # sets are taken from LFTP manual
        if value.lower() in {"true", "on", "yes", "1", "+"}:
            return True
        elif value.lower() in {"false",  "off", "no", "0", "-"}:
            return False
        else:
            raise LftpError("Cannot convert value '{}' to boolean".format(value))

    @property
    def num_connections_per_dir_file(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_CONNECTIONS_MIRROR))

    @num_connections_per_dir_file.setter
    def num_connections_per_dir_file(self, num_connections: int):
        if num_connections < 1:
            raise ValueError("Number of connections must be positive")
        self.__set(Lftp.__SET_NUM_CONNECTIONS_MIRROR, str(num_connections))

    @property
    def num_connections_per_root_file(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_CONNECTIONS_PGET))

    @num_connections_per_root_file.setter
    def num_connections_per_root_file(self, num_connections: int):
        if num_connections < 1:
            raise ValueError("Number of connections must be positive")
        self.__set(Lftp.__SET_NUM_CONNECTIONS_PGET, str(num_connections))

    @property
    def num_max_total_connections(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_MAX_TOTAL_CONNECTIONS))

    @num_max_total_connections.setter
    def num_max_total_connections(self, num_connections: int):
        if num_connections < 0:
            raise ValueError("Number of connections must be zero or greater")
        self.__set(Lftp.__SET_NUM_MAX_TOTAL_CONNECTIONS, str(num_connections))

    @property
    def num_parallel_files(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_PARALLEL_FILES))

    @num_parallel_files.setter
    def num_parallel_files(self, num_parallel_files: int):
        if num_parallel_files < 1:
            raise ValueError("Number of parallel files must be positive")
        self.__set(Lftp.__SET_NUM_PARALLEL_FILES, str(num_parallel_files))

    @property
    def rate_limit(self) -> str:
        return self.__get(Lftp.__SET_RATE_LIMIT)

    @rate_limit.setter
    def rate_limit(self, rate_limit: Union[int, str]):
        self.__set(Lftp.__SET_RATE_LIMIT, str(rate_limit))

    @property
    def net_socket_buffer(self) -> str:
        return self.__get(Lftp.__SET_NET_SOCKET_BUFFER)

    @net_socket_buffer.setter
    def net_socket_buffer(self, net_socket_buffer: Union[int, str]):
        normalized = Checkers.byte_size_or_empty(Lftp, "net_socket_buffer", net_socket_buffer)
        if normalized == "":
            return
        self.__set(Lftp.__SET_NET_SOCKET_BUFFER, normalized)

    @property
    def min_chunk_size(self) -> str:
        return self.__get(Lftp.__SET_MIN_CHUNK_SIZE)

    @min_chunk_size.setter
    def min_chunk_size(self, min_chunk_size: Union[int, str]):
        self.__set(Lftp.__SET_MIN_CHUNK_SIZE, str(min_chunk_size))

    @property
    def num_parallel_jobs(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_PARALLEL_JOBS))

    @num_parallel_jobs.setter
    def num_parallel_jobs(self, num_parallel_jobs: int):
        if num_parallel_jobs < 1:
            raise ValueError("Number of parallel jobs must be positive")
        self.__set(Lftp.__SET_NUM_PARALLEL_JOBS, str(num_parallel_jobs))

    @property
    def move_background_on_exit(self) -> bool:
        return Lftp.__to_bool(self.__get(Lftp.__SET_MOVE_BACKGROUND_ON_EXIT))

    @move_background_on_exit.setter
    def move_background_on_exit(self, move_background_on_exit: bool):
        self.__set(Lftp.__SET_MOVE_BACKGROUND_ON_EXIT, str(int(move_background_on_exit)))

    @property
    def use_temp_file(self) -> bool:
        return Lftp.__to_bool(self.__get(Lftp.__SET_USE_TEMP_FILE))

    @use_temp_file.setter
    def use_temp_file(self, use_temp_file: bool):
        self.__set(Lftp.__SET_USE_TEMP_FILE, str(int(use_temp_file)))

    @property
    def xfer_verify(self) -> bool:
        return Lftp.__to_bool(self.__get(Lftp.__SET_XFER_VERIFY))

    @xfer_verify.setter
    def xfer_verify(self, value: bool):
        self.__set(Lftp.__SET_XFER_VERIFY, str(int(value)))

    @property
    def xfer_verify_command(self) -> str:
        return self.__get(Lftp.__SET_XFER_VERIFY_COMMAND)

    @xfer_verify_command.setter
    def xfer_verify_command(self, command: str):
        self.__set(Lftp.__SET_XFER_VERIFY_COMMAND, command)

    @property
    def temp_file_name(self) -> str:
        return self.__get(Lftp.__SET_TEMP_FILE_NAME)

    @temp_file_name.setter
    def temp_file_name(self, temp_file_name: str):
        self.__set(Lftp.__SET_TEMP_FILE_NAME, temp_file_name)

    @property
    def sftp_auto_confirm(self) -> bool:
        return Lftp.__to_bool(self.__get(Lftp.__SET_SFTP_AUTO_CONFIRM))

    @sftp_auto_confirm.setter
    def sftp_auto_confirm(self, auto_confirm: bool):
        self.__set(Lftp.__SET_SFTP_AUTO_CONFIRM, str(int(auto_confirm)))

    @property
    def last_status_poll_healthy(self) -> bool:
        return self.__last_status_poll_healthy

    @property
    def sftp_connect_program(self) -> str:
        return self.__get(Lftp.__SET_SFTP_CONNECT_PROGRAM)

    @sftp_connect_program.setter
    def sftp_connect_program(self, program: str):
        self.__set(Lftp.__SET_SFTP_CONNECT_PROGRAM, program)

    def status(self) -> Optional[List[LftpJobStatus]]:
        """
        Return a status list of queued and running jobs, or None when
        parsing failed but the error is still within the tolerated threshold.
        :return:
        """
        try:
            out = self.__run_command("jobs -v", timeout_seconds=0, require_prompt_ready=False, status_poll=True)  # type: ignore[arg-type]
        except pexpect.exceptions.TIMEOUT:
            self.__consecutive_status_errors = 0
            self.__last_command_timed_out = True
            self.__last_status_poll_healthy = False
            self.logger.warning("Lftp timeout exception")
            return []
        except pexpect.exceptions.EOF:
            self.__consecutive_status_errors = 0
            self.__last_command_timed_out = True
            self.__last_status_poll_healthy = False
            self.logger.error("Lftp process died unexpectedly (EOF) during status poll")
            return []
        except LftpError as exc:
            self.__consecutive_status_errors = 0
            self.__last_command_timed_out = True
            self.__last_status_poll_healthy = False
            self.logger.warning("Ignoring status poll failure: {}".format(exc))
            return []
        timed_out = self.__last_command_timed_out
        statuses: Optional[List[LftpJobStatus]] = None
        try:
            statuses = self.__job_status_parser.parse(out)
            self.__consecutive_status_errors = 0
            self.__last_status_poll_healthy = not timed_out
        except LftpJobStatusParserError:
            self.__consecutive_status_errors += 1
            self.__last_status_poll_healthy = False
            if self.__consecutive_status_errors < MAX_CONSECUTIVE_STATUS_ERRORS:
                self.logger.warning(f"Ignoring status error (count={self.__consecutive_status_errors})")
            else:
                raise
        if statuses is not None:
            self.__annotate_status_path_pairs(statuses)
        if not statuses and getattr(self, "_Lftp__status_poll_needs_connection_grace", False) and not self.__pending_error:
            self.__status_poll_needs_connection_grace = False
            connection_grace_timeout = max(STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS, 5.0)
            run_command: Any = self.__run_command
            out = run_command(
                "jobs -v",
                timeout_seconds=connection_grace_timeout,
                require_prompt_ready=False,
                status_poll=True
            )
            try:
                statuses = self.__job_status_parser.parse(out)
                self.__consecutive_status_errors = 0
                self.__last_status_poll_healthy = not self.__last_command_timed_out
            except LftpJobStatusParserError:
                self.__consecutive_status_errors += 1
                self.__last_status_poll_healthy = False
                if self.__consecutive_status_errors < MAX_CONSECUTIVE_STATUS_ERRORS:
                    self.logger.warning(f"Ignoring status error (count={self.__consecutive_status_errors})")
                else:
                    raise
            if statuses is not None:
                self.__annotate_status_path_pairs(statuses)
        return statuses

    def __annotate_status_path_pairs(self, statuses: List[LftpJobStatus]):
        if not self.__path_pairs_by_id:
            return
        for status in statuses:
            remote_matches = {
                pair_id for pair_id, pair in self.__path_pairs_by_id.items()
                if Lftp.__path_is_within(status.remote_path, pair["remote_path"])
            }
            local_matches = {
                pair_id for pair_id, pair in self.__path_pairs_by_id.items()
                if Lftp.__path_is_within(status.local_path, pair["local_path"])
            }
            if len(remote_matches) != 1 or remote_matches != local_matches:
                continue
            pair_id = next(iter(remote_matches))
            pair = self.__path_pairs_by_id[pair_id]
            status.path_pair_id = pair_id
            status.path_pair_name = pair["name"]

    @staticmethod
    def __path_is_within(path: Optional[str], root: str) -> bool:
        if path is None:
            return False
        normalized_path = Lftp.__normalize_path(path)
        normalized_root = Lftp.__normalize_path(root)
        try:
            common = os.path.commonpath([normalized_path, normalized_root])
        except ValueError:
            return False
        return common == normalized_root

    @staticmethod
    def __normalize_path(path: str) -> str:
        return os.path.normpath(path)

    @staticmethod
    def __status_matches_paths(status: LftpJobStatus, remote_root: str, local_root: str) -> bool:
        remote_matches = Lftp.__path_is_within(status.remote_path, remote_root)
        local_matches = Lftp.__path_is_within(status.local_path, local_root)
        return remote_matches and local_matches

    @classmethod
    def __file_resume_artifacts(
            cls, local_dir: str, name: str, expected_size: Optional[int] = None,
    ) -> tuple[bool, bool]:
        """Return (one target exists, it has a valid matching pget map).

        LFTP's multi-connection pget resume requires the segment map beside the
        target.  This snapshot is intentionally non-mutating: a map is either
        valid and authorizes pget, absent and authorizes contiguous get, or it
        is unsafe and fails before queueing.  Both the direct and historical
        ``.lftp`` forms are inspected so an ambiguous artifact cannot fall
        through to get -c.
        """
        local_root, target_paths = cls.__file_artifact_paths(local_dir, name)
        targets: list[tuple[str, Optional[str]]] = []
        status_paths: list[str] = []
        for target_path, status_path in target_paths:
            if not cls.__is_lexically_and_really_contained(target_path, local_root):
                raise LftpError("LFTP queue target is outside the local directory")
            status_paths.append(status_path)
            if not cls.__is_lexically_and_really_contained(status_path, local_root):
                # The only ordinary reason a derived sibling escapes the
                # resolved root is that it is a link/reparse point. Do not
                # follow it or downgrade it to a get resume.
                raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue")
            try:
                target_info = os.stat(target_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise LftpError("LFTP queue target is unsafe; use Delete Local before Queue") from error
            if cls.__is_link_or_reparse(target_path, target_info):
                raise LftpError("LFTP queue target must be a regular file")
            if not stat.S_ISREG(target_info.st_mode):
                raise LftpError("LFTP queue target must be a regular file")
            try:
                status_info = os.stat(status_path, follow_symlinks=False)
            except FileNotFoundError:
                status_path = None
            except OSError as error:
                raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue") from error
            targets.append((target_path, status_path))

        for status_path in status_paths:
            if any(target_status_path == status_path for _, target_status_path in targets):
                continue
            try:
                os.stat(status_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue") from error
            raise LftpError("LFTP queue status sidecar is an orphan; use Delete Local before Queue")

        if not targets:
            return False, False
        if len(targets) != 1:
            raise LftpError("LFTP queue has ambiguous local partial artifacts; use Delete Local before Queue")

        _, status_path = targets[0]
        if status_path is None:
            return True, False
        try:
            status_info = os.stat(status_path, follow_symlinks=False)
        except OSError as error:
            raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue") from error
        if cls.__is_link_or_reparse(status_path, status_info) or not stat.S_ISREG(status_info.st_mode):
            raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue")
        if not cls.__is_valid_pget_status_file(status_path):
            raise LftpError("LFTP queue status sidecar is invalid; use Delete Local before Queue")
        if type(expected_size) is int and expected_size >= 0 and \
                cls.__pget_status_file_size(status_path) != expected_size:
            raise LftpError("LFTP queue status sidecar is stale; use Delete Local before Queue")
        return True, True

    @classmethod
    def get_safe_file_artifact_delete_paths(cls, local_dir: str, name: str) -> tuple[str, ...]:
        """Return app-owned exact file/map paths that Delete Local may remove.

        This is deliberately distinct from queue resume validation: malformed
        maps are unsafe to resume, but a regular malformed map is safe for the
        user-authorized Delete Local repair to remove.  More than one direct or
        historical-temp form is ambiguous and remains fail-closed.
        """
        cls.__validate_queue_name(name)
        local_root, target_paths = cls.__file_artifact_paths(local_dir, name)
        try:
            root_info = os.lstat(local_root)
        except OSError as error:
            raise LftpError("Delete Local staging directory is unsafe") from error
        if cls.__is_link_or_reparse(local_root, root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise LftpError("Delete Local staging directory is unsafe")

        forms: list[tuple[str, ...]] = []
        for target_path, status_path in target_paths:
            existing: list[str] = []
            for path in (target_path, status_path):
                if not cls.__is_lexically_and_really_contained(path, local_root):
                    raise LftpError("Delete Local staging artifact is unsafe")
                try:
                    info = os.lstat(path)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise LftpError("Delete Local staging artifact is unsafe") from error
                if cls.__is_link_or_reparse(path, info) or not stat.S_ISREG(info.st_mode):
                    raise LftpError("Delete Local staging artifact is unsafe")
                existing.append(path)
            if existing:
                forms.append(tuple(existing))
        if len(forms) > 1:
            raise LftpError("Delete Local has ambiguous local partial artifacts")
        return forms[0] if forms else ()

    @classmethod
    def __file_artifact_paths(cls, local_dir: str, name: str) -> tuple[str, tuple[tuple[str, str], ...]]:
        local_root = os.path.abspath(os.path.normpath(local_dir))
        targets = (
            os.path.join(local_root, name),
            os.path.join(local_root, name + cls.__LFTP_TEMP_FILE_SUFFIX),
        )
        return local_root, tuple((target, target + cls.__PGET_STATUS_FILE_SUFFIX) for target in targets)

    @staticmethod
    def __pget_status_file_size(status_path: str) -> Optional[int]:
        try:
            with open(status_path, "r", encoding="utf-8") as handle:
                first_line = handle.readline().strip()
        except (OSError, UnicodeError):
            return None
        match = re.fullmatch(r"size=(\d+)", first_line)
        return int(match.group(1)) if match is not None else None

    @classmethod
    def __allow_legacy_get_resume(cls, local_dir: str, name: str, expected_size: int) -> bool:
        """Verify the physical constraints for a v0.9.2 contiguous upgrade.

        This is intentionally narrower than ordinary sidecarless get -c: a
        legacy timestamp cannot bind pget's non-contiguous segment map.  One
        contained regular target, no status map, and a bounded partial are the
        minimum physical evidence required before asking LFTP to continue it.
        """
        if type(expected_size) is not int or expected_size < 0:
            raise LftpError("Legacy resume requires a valid remote size")
        local_root = os.path.abspath(os.path.normpath(local_dir))
        targets: list[os.stat_result] = []
        for target_path in (
                os.path.join(local_root, name),
                os.path.join(local_root, name + cls.__LFTP_TEMP_FILE_SUFFIX),
        ):
            status_path = target_path + cls.__PGET_STATUS_FILE_SUFFIX
            if not cls.__is_lexically_and_really_contained(target_path, local_root) or \
                    not cls.__is_lexically_and_really_contained(status_path, local_root):
                raise LftpError("LFTP legacy resume target is outside the local directory")
            try:
                status_info = os.stat(status_path, follow_symlinks=False)
            except FileNotFoundError:
                status_info = None
            except OSError as error:
                raise LftpError("LFTP legacy resume status sidecar is unsafe") from error
            if status_info is not None:
                raise LftpError("Cannot migrate a legacy partial with an LFTP pget status map")
            try:
                target_info = os.stat(target_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise LftpError("LFTP legacy resume target is unsafe") from error
            if cls.__is_link_or_reparse(target_path, target_info) or not stat.S_ISREG(target_info.st_mode):
                raise LftpError("LFTP legacy resume target must be a regular file")
            targets.append(target_info)
        if len(targets) != 1:
            raise LftpError("Legacy resume requires exactly one local partial")
        if targets[0].st_size > expected_size:
            raise LftpError("Legacy resume partial exceeds the remote size")
        return True

    @staticmethod
    def __is_lexically_and_really_contained(path: str, root: str) -> bool:
        try:
            path_abs = os.path.abspath(os.path.normpath(path))
            root_abs = os.path.abspath(os.path.normpath(root))
            lexical_common = os.path.commonpath([path_abs, root_abs])
            real_common = os.path.commonpath([
                os.path.realpath(path_abs),
                os.path.realpath(root_abs),
            ])
        except (OSError, ValueError):
            return False
        return (
            os.path.normcase(lexical_common) == os.path.normcase(root_abs)
            and os.path.normcase(real_common) == os.path.normcase(os.path.realpath(root_abs))
        )

    @staticmethod
    def __is_link_or_reparse(path: str, file_info: Optional[os.stat_result] = None) -> bool:
        try:
            file_info = file_info or os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if stat.S_ISLNK(file_info.st_mode):
            return True
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(getattr(file_info, "st_file_attributes", 0) & reparse_attribute)

    @staticmethod
    def __validate_queue_name(name: str) -> None:
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or os.path.isabs(name)
            or os.path.splitdrive(name)[0]
            or "/" in name
            or "\\" in name
        ):
            raise LftpError("LFTP queue name must be a single file or directory name")

    @staticmethod
    def __is_valid_pget_status_file(status_path: str) -> bool:
        """Recognize the segment-map shape emitted by LFTP's pget status.

        The status suffix alone is not enough to preserve a multi-connection
        resume: stale or malformed files must take the contiguous ``get -c``
        path instead.  This mirrors the repository scanner's bounded status
        parser without making queue construction depend on the scanner owner.
        """
        try:
            with open(status_path, "r", encoding="utf-8") as handle:
                lines = [line.strip() for line in handle.read().splitlines() if line.strip()]
        except (OSError, UnicodeError):
            return False
        if not lines:
            return False
        size_match = re.fullmatch(r"size=(\d+)", lines.pop(0))
        if size_match is None or not lines or len(lines) % 2:
            return False
        total_size = int(size_match.group(1))
        empty_size = 0
        ranges: list[tuple[int, int]] = []
        for index in range(0, len(lines), 2):
            pos_match = re.fullmatch(r"(\d+)\.pos=(\d+)", lines[index])
            limit_match = re.fullmatch(r"(\d+)\.limit=(\d+)", lines[index + 1])
            if pos_match is None or limit_match is None:
                return False
            expected_segment = index // 2
            if (
                int(pos_match.group(1)) != expected_segment or
                int(limit_match.group(1)) != expected_segment
            ):
                return False
            pos = int(pos_match.group(2))
            limit = int(limit_match.group(2))
            if pos > total_size or limit > total_size or limit < pos:
                return False
            if any(pos < previous_limit and previous_pos < limit
                   for previous_pos, previous_limit in ranges):
                return False
            ranges.append((pos, limit))
            empty_size += limit - pos
        return empty_size <= total_size

    def queue(self,
              name: str,
              is_dir: bool,
              remote_base_dir_path: Optional[str] = None,
              local_base_dir_path: Optional[str] = None,
              exclude_patterns: str | Iterable[str | ExactPathExclusion] | None = None,
              allow_resume: bool = True,
              allow_legacy_get_resume: bool = False,
              expected_size: Optional[int] = None):
        """
        Queues a job for download
        This method may cause an exception to be generated in a later method call:
          * Wrong type (is_dir) is specified
          * File/folder does not exist
        :param name: name of file or folder to download
        :param is_dir: true if folder, false if file
        :return:
        """
        self.__validate_queue_name(name)
        remote_dir = remote_base_dir_path if remote_base_dir_path is not None else self.__base_remote_dir_path
        local_dir = local_base_dir_path if local_base_dir_path is not None else self.__base_local_dir_path

        has_existing_target = False
        has_valid_pget_map = False
        if not is_dir:
            has_existing_target, has_valid_pget_map = self.__file_resume_artifacts(
                local_dir, name, expected_size if allow_resume else None,
            )
        legacy_get_resume = False
        if allow_legacy_get_resume:
            if is_dir or allow_resume:
                raise LftpError("Legacy resume is only valid for a changed file without a source binding")
            # A timestamp alone is not a partial.  With an empty, already
            # validated artifact snapshot, start an ordinary fresh pget
            # instead of sending a legacy get-c that has nothing to continue.
            if has_existing_target:
                legacy_get_resume = self.__allow_legacy_get_resume(local_dir, name, expected_size)  # type: ignore[arg-type]
        if not is_dir and not allow_resume and has_existing_target:
            # LFTP pget without -c does not clobber a pre-existing target.
            # Do not queue a transfer that will predictably fail, and do not
            # delete or rename a user's staging artifact outside the existing
            # Delete Local lifecycle.  In particular, get -e could leave a
            # pget map beside a differently configured temporary target.
            if not legacy_get_resume:
                raise LftpError(
                    "Cannot safely restart a changed remote file while a local partial exists; "
                    "use Delete Local before Queue"
                )
        use_get = not is_dir and (legacy_get_resume or (has_existing_target and not has_valid_pget_map))
        parts = [
            "queue",
            "mirror" if is_dir else ("get" if use_get else "pget"),
        ]
        if is_dir or allow_resume or not has_existing_target or legacy_get_resume:
            parts.append("-c")
        if is_dir:
            user_exclude_patterns, exact_exclude_paths = partition_transfer_exclusions(exclude_patterns)
            if user_exclude_patterns:
                parts.extend(
                    "--exclude-glob {}".format(Lftp.__quote_command_argument(pattern))
                    for pattern in user_exclude_patterns
                )
            # LFTP's glob exclusions match basenames at every depth.  Its
            # regex exclusion is evaluated against the source-root-relative
            # path, so anchors make this an exact leaf exclusion.
            parts.extend(
                "--exclude {}".format(Lftp.__quote_command_argument(
                    "^{}$".format(re.escape(exact_path.relative_path))
                ))
                for exact_path in exact_exclude_paths
            )

        parts.append(Lftp.__quote_command_argument("{remote_dir}/{filename}".format(
            remote_dir=remote_dir,
            filename=name,
        )))
        if is_dir:
            parts.append(Lftp.__quote_command_argument("{local_dir}/".format(local_dir=local_dir)))
        else:
            parts.extend([
                "-o",
                Lftp.__quote_command_argument("{local_dir}/".format(local_dir=local_dir)),
            ])
        command = " ".join(parts)
        self.logger.debug("queue command: %s", command)
        self.__run_command(
            command,
            require_prompt_ready=False,
            low_latency=True,
        )  # type: ignore[arg-type]

    def kill(self,
             name: str,
             path_pair_id: Optional[str] = None,
             remote_path: Optional[str] = None,
             local_path: Optional[str] = None) -> bool:
        """
        Kill a queued or running job
        :param name:
        :return: True if job of given name was found, False otherwise
        """
        def find_matching_jobs() -> tuple[List[LftpJobStatus], List[LftpJobStatus], bool]:
            statuses = self.status()
            if statuses is None:
                # Parser failures come back as None; treat them as an empty
                # snapshot so the retry loop can keep probing safely.
                statuses = []
            status_poll_healthy = self.last_status_poll_healthy
            matching_jobs: List[LftpJobStatus] = []
            for status in statuses:
                if status.name != name:
                    continue
                if remote_path is not None and not self.__path_is_within(status.remote_path, remote_path):
                    continue
                if local_path is not None and not self.__path_is_within(status.local_path, local_path):
                    continue
                if remote_path is None and local_path is None and path_pair_id is not None and status.path_pair_id != path_pair_id:
                    continue
                matching_jobs.append(status)
            if not matching_jobs:
                self.logger.debug(
                    "Kill poll for '%s' saw statuses: %s",
                    name,
                    [
                        {
                            "id": status.id,
                            "name": status.name,
                            "state": getattr(status.state, "name", status.state),
                            "remote_path": status.remote_path,
                            "local_path": status.local_path,
                            "path_pair_id": status.path_pair_id,
                        }
                        for status in statuses
                    ]
                )
            return statuses, matching_jobs, status_poll_healthy

        killed_any = False
        attempts = 0
        while attempts < MAX_KILL_MATCH_ATTEMPTS:
            statuses, matching_jobs, status_poll_healthy = find_matching_jobs()
            if not matching_jobs:
                if statuses:
                    break
                if not status_poll_healthy:
                    attempts += 1
                    time.sleep(0.05)
                    continue
                break
            attempts += 1
            # The snapshot can contain duplicate jobs for the same file.  Send
            # every matching id before returning, while avoiding synchronous
            # convergence polls.  The next controller refresh remains
            # responsible for publishing the authoritative post-stop state.
            for job_to_kill in matching_jobs:
                killed_any = True
                # Note: there's a chance that job ids change between when we called status
                #       and when we execute the kill command.
                if job_to_kill.state == LftpJobStatus.State.RUNNING:
                    self.logger.debug("Killing running job '{}'...".format(name))
                    self.__run_command(
                        "kill {}".format(job_to_kill.id),
                        require_prompt_ready=False,
                        low_latency=True,
                    )  # type: ignore[arg-type]
                elif job_to_kill.state == LftpJobStatus.State.QUEUED:
                    self.logger.debug("Killing queued job '{}'...".format(name))
                    self.__run_command(
                        "queue --delete {}".format(job_to_kill.id),
                        require_prompt_ready=False,
                        low_latency=True,
                    )  # type: ignore[arg-type]
                else:
                    raise NotImplementedError("Unsupported state {}".format(str(job_to_kill.state)))
            break

        if not killed_any:
            self.logger.debug("Kill failed to find job '{}'".format(name))
            return False
        return True

    def kill_all(self):
        """
        Kills are jobs, queued or downloading
        :return:
        """
        # empty the queue and kill running jobs
        self.__run_command("queue -d *", require_prompt_ready=False)  # type: ignore[arg-type]
        self.__run_command("kill all", require_prompt_ready=False, timeout_seconds=0)  # type: ignore[arg-type]

    def exit(self):
        """
        Exit the lftp instance. It cannot be used after being killed
        :return:
        """
        self.kill_all()
        self.__process.sendline("exit")
        self.__process.close(force=True)

    def force_close(self) -> None:
        """Interrupt a blocked PTY operation during controller teardown only."""
        try:
            self.__process.close(force=True)
        except (OSError, pexpect.exceptions.ExceptionPexpect):
            self.logger.debug("Lftp process was already closed during forced teardown")

    # Mark decorators as static (must be at end of class)
    # Source: https://stackoverflow.com/a/3422823
    with_check_process = staticmethod(with_check_process)  # type: ignore[arg-type]
