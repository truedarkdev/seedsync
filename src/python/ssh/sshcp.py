# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
import posixpath
import re
import shlex
import shutil
import time
from typing import Any, List, Optional

import pexpect
import pexpect.popen_spawn

# my libs
from common import AppError


class SshcpError(AppError):
    """
    Custom exception that describes the failure of the ssh command
    """
    pass


TRANSIENT_ERROR_PATTERNS = ("Timed out", "Connection refused by server")


class Sshcp:
    """
    Scp command utility
    """
    __TIMEOUT_SECS = 180
    SHELL_CANDIDATES = ["/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh"]
    __SCP_DESTINATION_PERMISSION_DENIED = re.compile(
        r"^scp:\s+(?:dest open\s+)?(?P<path>.+):\s+(?:-\s+)?permission denied$",
        re.IGNORECASE
    )

    def __init__(self,
                 host: str,
                 port: int,
                 user: Optional[str] = None,
                 password: Optional[str] = None):
        if host is None:
            raise ValueError("Hostname not specified.")
        self.__host = host
        self.__port = port
        self.__user = user
        self.__password = password
        self.__detected_shell: Optional[str] = None
        self.__shell_detection_in_progress = False
        self.logger = logging.getLogger(self.__class__.__name__)

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild(self.__class__.__name__)

    def __describe_target(self) -> str:
        return "host={}, user={}, port={}".format(self.__host, self.__user, self.__port)

    def __remote_address(self) -> str:
        if self.__user is None:
            if self.__host.startswith("-"):
                raise ValueError("Hostname cannot start with '-'")
            return self.__host
        return "{}@{}".format(self.__user, self.__host)

    def __is_missing_remote_shell_error(self, error_message: str) -> bool:
        if "No such file or directory" not in error_message:
            return False
        for line in error_message.splitlines():
            stripped = line.strip()
            for shell in self.SHELL_CANDIDATES:
                shell_name = posixpath.basename(shell)
                if re.match(
                    r"^(?:-?{}:\s*)?{}:\s*No such file or directory$".format(
                        re.escape(shell_name),
                        re.escape(shell)
                    ),
                    stripped
                ):
                    return True
        return False

    def __format_missing_remote_shell_error(self,
                                            error_message: str,
                                            available_shells: Optional[List[str]] = None) -> str:
        if available_shells:
            shells_str = ", ".join(available_shells)
            return (
                "Remote user's shell not found: {}. Available shells on the remote server: {}. "
                "Fix by running on the remote server: sudo chsh -s {} {}".format(
                    error_message,
                    shells_str,
                    available_shells[0],
                    self.__user
                )
            )
        return (
            "Remote user's shell not found (login shell not found and no common shells could be "
            "detected): {}. Fix by running on the remote server: sudo chsh -s /bin/sh {} OR "
            "sudo ln -s /usr/bin/bash /bin/bash".format(
                error_message,
                self.__user
            )
        )

    @staticmethod
    def __decode_spawn_output(output: Any) -> str:
        if output is None or output is pexpect.EOF or output is pexpect.TIMEOUT:
            return ""
        if isinstance(output, bytes):
            return output.decode(errors="replace")
        return str(output)

    @classmethod
    def __format_spawn_error(cls, before: Any, after: Any) -> str:
        return "{}{}".format(
            cls.__decode_spawn_output(before),
            cls.__decode_spawn_output(after)
        ).strip()

    @classmethod
    def __is_scp_destination_permission_denied(cls, error_message: str) -> bool:
        for line in error_message.splitlines():
            if cls.__SCP_DESTINATION_PERMISSION_DENIED.match(line.strip()):
                return True
        return False

    def __check_shell_not_found(self, output: str) -> None:
        if self.__shell_detection_in_progress:
            return
        if self.__is_missing_remote_shell_error(output):
            raise SshcpError(self.__format_missing_remote_shell_error(output))

    def __classify_expect_result(self,
                                 command: str,
                                 sp: Any,
                                 i: int,
                                 eof_error: Optional[str],
                                 password_error: Optional[str],
                                 scp_permission_denied_is_destination_error: bool) -> None:
        if i == 0:
            return

        before = self.__decode_spawn_output(sp.before).strip()
        after = self.__decode_spawn_output(sp.after).strip()
        self.logger.warning("Command failed: '{} - {}'".format(before, after))
        self.__check_shell_not_found(before)

        if i == 1:
            before_lower = before.lower()
            if command == "scp" and "no such file or directory" not in before_lower:
                scp_error = self.__format_spawn_error(sp.before, sp.after)
                if self.__is_scp_destination_permission_denied(scp_error):
                    raise SshcpError(scp_error)
                raise SshcpError("connection closed")
            if self.__password is not None and self.__host in {"127.0.0.1", "localhost"} and (
                "bad owner or permissions on" in before_lower
            ):
                raise SshcpError("Incorrect password")
            if eof_error is not None:
                error_msg = eof_error
                if before:
                    error_msg += " - " + before
                raise SshcpError(error_msg)
            if password_error is not None:
                raise SshcpError(password_error)
        elif i in {3, 5}:
            raise SshcpError("Bad hostname: {}".format(self.__host))
        elif i in {2, 4, 6, 7}:
            error_msg = "Connection refused by server"
            if before:
                error_msg += " - " + before
            raise SshcpError(error_msg)
        elif i == 8:
            raise SshcpError(
                "Remote host key has changed. Remove the old key from ~/.ssh/known_hosts to continue."
            )
        elif i == 9:
            if command == "scp" and scp_permission_denied_is_destination_error:
                scp_error = self.__format_spawn_error(sp.before, sp.after)
                if self.__is_scp_destination_permission_denied(scp_error):
                    raise SshcpError(scp_error)
                raise SshcpError("connection closed")
            raise SshcpError("Incorrect password")

    def detect_shell(self) -> str:
        if self.__detected_shell is not None:
            return self.__detected_shell

        self.logger.debug("Detecting remote shell...")
        self.__shell_detection_in_progress = True
        try:
            out = self.__run_command(
                command="ssh",
                flags=[
                    "-p", str(self.__port),  # port
                ],
                args=[
                    self.__remote_address(),
                    "echo __shell_path__$(which bash 2>/dev/null || "
                    "which sh 2>/dev/null || "
                    "echo unknown)__end__"
                ]
            )
        except SshcpError as e:
            error_message = str(e)
            if not self.__is_missing_remote_shell_error(error_message):
                raise

            self.logger.warning(
                "Remote shell not found on server. Checking candidate shells via SFTP..."
            )
            available_shells = self.__check_remote_shells_via_sftp()
            raise SshcpError(self.__format_missing_remote_shell_error(
                error_message,
                available_shells
            ))
        finally:
            self.__shell_detection_in_progress = False

        out_str = out.decode()
        shell_path = None
        if "__shell_path__" in out_str and "__end__" in out_str:
            shell_path = out_str.split("__shell_path__", 1)[1].split("__end__", 1)[0].strip()
            if not shell_path or shell_path == "unknown" or not posixpath.isabs(shell_path):
                shell_path = None

        if shell_path is None:
            self.logger.warning(
                "Remote shell probe returned ambiguous output. Checking candidate shells via SFTP..."
            )
            available_shells = self.__check_remote_shells_via_sftp()
            if not available_shells:
                raise SshcpError(
                    "Unable to detect remote shell from probe output and no common shells "
                    "could be detected via SFTP."
                )
            shell_path = available_shells[0]

        self.__detected_shell = shell_path
        self.logger.info("Detected remote shell: {}".format(self.__detected_shell))
        return self.__detected_shell

    def __check_remote_shells_via_sftp(self) -> List[str]:
        available_shells = []
        for shell_path in self.SHELL_CANDIDATES:
            try:
                self.__sftp_stat(shell_path)
            except SshcpError as e:
                if not str(e).startswith("File not found:"):
                    raise SshcpError(
                        "SFTP shell probe failed while checking {}: {}".format(
                            shell_path,
                            e
                        )
                    )
                continue
            available_shells.append(shell_path)
        return available_shells

    def __sftp_stat(self, remote_path: str):
        command_args = [
            "sftp",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=error",
        ]

        if self.__password is None:
            command_args += [
                "-o", "PasswordAuthentication=no",
            ]
        else:
            command_args += [
                "-o", "PubkeyAuthentication=no",
            ]

        command_args += [
            "-P", str(self.__port),
            self.__remote_address(),
        ]

        self.logger.debug("Command: {}".format(command_args))

        sp, _using_spawn_fallback = self.__spawn_process(command_args[0], command_args[1:])
        try:
            timeout = 30
            if self.__password is not None:
                i = sp.expect([
                    r'(?i)password:\s*',
                    pexpect.EOF,
                ], timeout=timeout)
                if i == 1:
                    raise SshcpError("SFTP connection failed")
                sp.sendline(self.__password)

            i = sp.expect([
                r'sftp> ',
                pexpect.EOF,
            ], timeout=timeout)
            if i != 0:
                raise SshcpError("SFTP connection failed")

            sp.sendline("ls {}".format(remote_path))
            i = sp.expect([
                r'sftp> ',
                pexpect.EOF,
            ], timeout=timeout)

            output = sp.before.decode().strip() if isinstance(sp.before, bytes) else str(sp.before).strip()
            if i != 0:
                if "No such file" in output or "not found" in output or "Can't ls" in output:
                    raise SshcpError("File not found: {}".format(remote_path))
                raise SshcpError("SFTP connection failed")
            if "No such file" in output or "not found" in output or "Can't ls" in output:
                raise SshcpError("File not found: {}".format(remote_path))

            sp.sendline("bye")
            sp.expect(pexpect.EOF, timeout=10)
        except pexpect.exceptions.TIMEOUT:
            raise SshcpError("SFTP timed out")
        finally:
            close = getattr(sp, "close", None)
            if callable(close):
                close()

    def __log_timeout(self, phase: str, command: str, sp: Any, start_time: float):
        elapsed = time.time() - start_time
        self.logger.exception(
            "Timed out during {} after {:.3f}s (command={}, {})".format(
                phase,
                elapsed,
                command,
                self.__describe_target()
            )
        )
        self.logger.error("Command output before:\n{}".format(sp.before))

    def __spawn_process(self, command: str, command_args: list[str]) -> tuple[Any, bool]:
        spawn_factory = getattr(pexpect, "spawn", None)
        resolver_options = None
        resolver_modified = False
        if self.__host not in {"127.0.0.1", "localhost"} and "." not in self.__host and ":" not in self.__host:
            resolver_options = os.environ.get("RES_OPTIONS")
            os.environ["RES_OPTIONS"] = "attempts:1 timeout:1"
            resolver_modified = True
        if callable(spawn_factory):
            try:
                return spawn_factory(command, command_args), False
            finally:
                if resolver_modified:
                    if resolver_options is None:
                        os.environ.pop("RES_OPTIONS", None)
                    else:
                        os.environ["RES_OPTIONS"] = resolver_options
        else:
            from pexpect.popen_spawn import PopenSpawn

            resolved_command = shutil.which(command) or command
            try:
                return PopenSpawn([resolved_command] + command_args), True
            finally:
                if resolver_modified:
                    if resolver_options is None:
                        os.environ.pop("RES_OPTIONS", None)
                    else:
                        os.environ["RES_OPTIONS"] = resolver_options

    def __run_command(self,
                      command: str,
                      flags: list,
                      args: list) -> bytes:

        command_args = [command]
        command_args += flags

        # Common flags
        command_args += [
            "-o", "StrictHostKeyChecking=accept-new",  # accept new keys, reject changed ones
            "-o", "LogLevel=error",  # suppress warnings
        ]

        if self.__password is None:
            command_args += [
                "-o", "PasswordAuthentication=no",  # don't ask for password
            ]
        else:
            command_args += [
                "-o", "PubkeyAuthentication=no"  # don't use key authentication
            ]

        command_args += args

        self.logger.debug("Command: {}".format(command_args))

        start_time = time.time()
        sp, _using_spawn_fallback = self.__spawn_process(command_args[0], command_args[1:])
        timeout_phase: str = "command execution"
        try:
            if self.__password is not None:
                timeout_phase = "password prompt"
                i = sp.expect([
                    r'(?i)password:\s*',  # i=0, all's good
                    pexpect.EOF,  # i=1, unknown error
                    'lost connection',  # i=2, connection refused
                    'Could not resolve hostname',  # i=3, bad hostname
                    'Connection refused',  # i=4, connection refused
                    'Name or service not known',  # i=5, bad hostname
                    'No route to host',  # i=6, bad host
                    'Connection timed out',  # i=7, connection timeout
                    'REMOTE HOST IDENTIFICATION HAS CHANGED',  # i=8, possible MITM
                    'Permission denied',  # i=9, auth rejected before/at prompt
                ], timeout=self.__TIMEOUT_SECS)
                self.__classify_expect_result(
                    command,
                    sp,
                    i,
                    eof_error="Unknown error",
                    password_error=None,
                    scp_permission_denied_is_destination_error=False
                )
                sp.sendline(self.__password)
                timeout_phase = "command execution"

            i = sp.expect(
                [
                    pexpect.EOF,  # i=0, all's good
                    r'(?i)password:\s*',  # i=1, wrong password
                    'lost connection',  # i=2, connection refused
                    'Could not resolve hostname',  # i=3, bad hostname
                    'Connection refused',  # i=4, connection refused
                    'Name or service not known',  # i=5, bad hostname
                    'No route to host',  # i=6, bad host
                    'Connection timed out',  # i=7, connection timeout
                    'REMOTE HOST IDENTIFICATION HAS CHANGED',  # i=8, possible MITM
                    'Permission denied',  # i=9, wrong password on newer SSH
                ],
                timeout=self.__TIMEOUT_SECS
            )
            self.__classify_expect_result(
                command,
                sp,
                i,
                eof_error=None,
                password_error="Incorrect password",
                scp_permission_denied_is_destination_error=True
            )

        except pexpect.exceptions.TIMEOUT:
            self.__log_timeout(timeout_phase, command, sp, start_time)
            raise SshcpError("Timed out")
        close = getattr(sp, "close", None)
        if callable(close):
            close()
        else:
            wait = getattr(sp, "wait", None)
            if callable(wait):
                wait()
        end_time = time.time()

        exitstatus = getattr(sp, "exitstatus", None)
        if exitstatus is None:
            wait = getattr(sp, "wait", None)
            if callable(wait):
                exitstatus = wait()

        self.logger.debug("Return code: {}".format(exitstatus))
        self.logger.debug("Command took {:.3f}s".format(end_time-start_time))
        if exitstatus != 0:
            before = self.__decode_spawn_output(sp.before).strip()
            after = self.__decode_spawn_output(sp.after).strip()
            self.logger.warning("Command failed: '{} - {}'".format(before, after))
            self.__check_shell_not_found(before)
            raise SshcpError(self.__decode_spawn_output(sp.before).strip())

        before_val = sp.before
        assert isinstance(before_val, bytes)
        return before_val.replace(b'\r\n', b'\n').strip()

    def shell(self, command: str) -> bytes:
        """
        Run a shell command on remote service and return output
        :param command:
        :return:
        """
        if not command:
            raise ValueError("Command cannot be empty")

        if self.__detected_shell is None and "'" in command and '"' in command:
            raise ValueError("Command cannot contain both single and double quotes")

        if self.__detected_shell is not None:
            command = "{} -c {}".format(
                shlex.quote(self.__detected_shell),
                shlex.quote(command)
            )

        flags = [
            "-p", str(self.__port),  # port
        ]
        args = [
            self.__remote_address(),
            command
        ]
        return self.__run_command(
            command="ssh",
            flags=flags,
            args=args
        )

    def copy(self, local_path: str, remote_path: str):
        """
        Copies local file at local_path to remote remote_path
        :param local_path:
        :param remote_path:
        :return:
        """
        if not local_path:
            raise ValueError("Local path cannot be empty")
        if not remote_path:
            raise ValueError("Remote path cannot be empty")

        flags = [
            "-q",  # quiet
            "-P", str(self.__port),  # port
        ]
        args = [
            local_path,
            "{}:{}".format(self.__remote_address(), remote_path)
        ]
        self.__run_command(
            command="scp",
            flags=flags,
            args=args
        )
