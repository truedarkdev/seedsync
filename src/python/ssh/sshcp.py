# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import shutil
import time

import pexpect

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

    def __init__(self,
                 host: str,
                 port: int,
                 user: str = None,
                 password: str = None):
        if host is None:
            raise ValueError("Hostname not specified.")
        self.__host = host
        self.__port = port
        self.__user = user
        self.__password = password
        self.logger = logging.getLogger(self.__class__.__name__)

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild(self.__class__.__name__)

    def __describe_target(self) -> str:
        return "host={}, user={}, port={}".format(self.__host, self.__user, self.__port)

    def __log_timeout(self, phase: str, command: str, sp, start_time: float):
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

    def __spawn_process(self, command: str, command_args: list):
        spawn_factory = getattr(pexpect, "spawn", None)
        if callable(spawn_factory):
            return spawn_factory(command, command_args), False

        from pexpect.popen_spawn import PopenSpawn

        resolved_command = shutil.which(command) or command
        return PopenSpawn([resolved_command] + command_args), True

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
        try:
            timeout_phase = "command execution"
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
                if i > 0:
                    before = sp.before.decode().strip() if sp.before != pexpect.EOF else ""
                    after = sp.after.decode().strip() if sp.after != pexpect.EOF else ""
                    self.logger.warning("Command failed: '{} - {}'".format(before, after))
                if i == 1:
                    error_msg = "Unknown error"
                    if sp.before.decode().strip():
                        error_msg += " - " + sp.before.decode().strip()
                    raise SshcpError(error_msg)
                elif i in {3, 5}:
                    raise SshcpError("Bad hostname: {}".format(self.__host))
                elif i in {2, 4, 6, 7}:
                    error_msg = "Connection refused by server"
                    if sp.before.decode().strip():
                        error_msg += " - " + sp.before.decode().strip()
                    raise SshcpError(error_msg)
                elif i == 8:
                    raise SshcpError(
                        "Remote host key has changed. Remove the old key from ~/.ssh/known_hosts to continue."
                    )
                elif i == 9:
                    raise SshcpError("Incorrect password")
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
            if i > 0:
                before = sp.before.decode().strip() if sp.before != pexpect.EOF else ""
                after = sp.after.decode().strip() if sp.after != pexpect.EOF else ""
                self.logger.warning("Command failed: '{} - {}'".format(before, after))
            if i == 1:
                raise SshcpError("Incorrect password")
            elif i in {3, 5}:
                raise SshcpError("Bad hostname: {}".format(self.__host))
            elif i in {2, 4, 6, 7}:
                error_msg = "Connection refused by server"
                if sp.before.decode().strip():
                    error_msg += " - " + sp.before.decode().strip()
                raise SshcpError(error_msg)
            elif i == 8:
                raise SshcpError(
                    "Remote host key has changed. Remove the old key from ~/.ssh/known_hosts to continue."
                )
            elif i == 9:
                raise SshcpError("Incorrect password")

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
            before = sp.before.decode().strip() if sp.before != pexpect.EOF else ""
            after = sp.after.decode().strip() if sp.after != pexpect.EOF else ""
            self.logger.warning("Command failed: '{} - {}'".format(before, after))
            raise SshcpError(sp.before.decode().strip())

        return sp.before.replace(b'\r\n', b'\n').strip()

    def shell(self, command: str) -> bytes:
        """
        Run a shell command on remote service and return output
        :param command:
        :return:
        """
        if not command:
            raise ValueError("Command cannot be empty")

        if "'" in command and '"' in command:
            raise ValueError("Command cannot contain both single and double quotes")

        flags = [
            "-p", str(self.__port),  # port
        ]
        args = [
            "{}@{}".format(self.__user, self.__host),
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
            "{}@{}:{}".format(self.__user, self.__host, remote_path)
        ]
        self.__run_command(
            command="scp",
            flags=flags,
            args=args
        )
