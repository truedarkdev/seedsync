# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, List, Optional

import pexpect
import pexpect.popen_spawn

# my libs
from common import AppError
from common.performance_diagnostics import (
    DURATION_REMOTE_SCAN_TRANSPORT_READ,
    PerformanceDiagnosticsCollector,
)


class SshcpError(AppError):
    """
    Custom exception that describes the failure of the ssh command
    """
    pass


TRANSIENT_ERROR_PATTERNS = ("Timed out", "Connection refused by server")


def _linux_master_parent_death_preexec(parent_pid: int):
    """Make a foreground POSIX master die if its scanner worker dies."""
    def preexec() -> None:
        import ctypes
        import signal
        if os.getppid() != parent_pid:
            os.kill(os.getpid(), signal.SIGTERM)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
        if os.getppid() != parent_pid:
            os.kill(os.getpid(), signal.SIGTERM)
    return preexec


class _SshcpControlMaster:
    """One generation-local foreground OpenSSH multiplexing session.

    The object deliberately owns only runtime-temporary state.  It is shared
    by the compatible scanners in one MultiPathRemoteScanner generation and is
    discarded before scanners can be pickled for their next worker process.
    """

    def __init__(self):
        self.directory = tempfile.mkdtemp(prefix=".seedsync-ssh-", dir=tempfile.gettempdir())
        self.path = os.path.join(self.directory, "control")
        self.process: Optional[Any] = None
        self.started = False
        self.closed = False
        self.lock = threading.Lock()

    def close(self, logger: logging.Logger, remote_address: str, port: int) -> None:
        with self.lock:
            process = self.process
            self.process = None
            self.started = False
            self.closed = True

        # Ask OpenSSH to close the master before force-closing its foreground
        # pty.  Either step is intentionally best-effort: cleanup must never
        # replace the scan failure that led here.
        try:
            subprocess.run(
                ["ssh", "-S", self.path, "-O", "exit", "-p", str(port), remote_address],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:
            logger.warning("Failed to request SSH control master exit", exc_info=True)

        if process is not None:
            try:
                close = getattr(process, "close", None)
                if callable(close):
                    try:
                        close(force=True)
                    except TypeError:
                        close()
                else:
                    terminate = getattr(process, "terminate", None)
                    if callable(terminate):
                        terminate()
                wait = getattr(process, "wait", None)
                if callable(wait):
                    wait()
            except Exception:
                logger.warning("Failed to clean up SSH control master", exc_info=True)

        try:
            shutil.rmtree(self.directory, ignore_errors=True)
        except Exception:
            logger.warning("Failed to remove SSH control master directory", exc_info=True)


class Sshcp:
    """
    Scp command utility
    """
    __TIMEOUT_SECS = 180
    __CONTROL_MASTER_TIMEOUT_SECS = 30
    # Filesystem scans can return many megabytes of JSON.  Pexpect's 2 KiB
    # default turns that stream into thousands of small reads and repeatedly
    # searches the accumulated buffer for terminal SSH errors.  Keep enough
    # history for every fixed prompt/error pattern while reading bulk output
    # in efficient chunks.
    __PEXPECT_MAX_READ_BYTES = 64 * 1024
    __PEXPECT_SEARCH_WINDOW_BYTES = 1024
    # Keep bounded remote diagnostics when a streaming command fails without
    # retaining its successful aggregate output.
    __STREAM_ERROR_CAPTURE_BYTES = 64 * 1024
    SHELL_CANDIDATES = ["/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh"]
    __SCP_DESTINATION_PERMISSION_DENIED = re.compile(
        r"^scp:\s+(?:dest open\s+)?(?P<path>.+):\s+(?:-\s+)?permission denied$",
        re.IGNORECASE
    )

    def __init__(self,
                 host: Optional[str],
                 port: int,
                 user: Optional[str] = None,
                 password: Optional[str] = None,
                 performance_diagnostics: Optional[PerformanceDiagnosticsCollector] = None):
        if host is None:
            raise ValueError("Hostname not specified.")
        self.__host = host
        self.__port = port
        self.__user = user
        self.__password = password
        self.__performance_diagnostics = performance_diagnostics
        self.__detected_shell: Optional[str] = None
        self.__shell_detection_in_progress = False
        self.__control_master: Optional[_SshcpControlMaster] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    def set_performance_diagnostics(
            self, diagnostics: object) -> None:
        self.__performance_diagnostics = diagnostics

    def __getstate__(self) -> dict[str, object]:
        state = self.__dict__.copy()
        state["_Sshcp__performance_diagnostics"] = None
        # A foreground pexpect child and its runtime socket must never cross a
        # scanner worker serialization boundary.
        state["_Sshcp__control_master"] = None
        return state

    def generation_connection_key(self) -> tuple[Optional[str], int, Optional[str], Optional[str]]:
        """Return credentials used only to group compatible scan transports."""
        return self.__host, self.__port, self.__user, self.__password

    def create_generation_control_master(self) -> Optional[_SshcpControlMaster]:
        """Allocate a private runtime control socket for one scan generation."""
        if not sys.platform.startswith("linux") or not callable(getattr(pexpect, "spawn", None)):
            return None
        try:
            return _SshcpControlMaster()
        except (OSError, ValueError):
            # A temporary directory failure must not make a compatible scanner
            # unusable; callers retain the normal one-connection behavior.
            self.logger.warning("Unable to allocate SSH control master runtime state", exc_info=True)
            return None

    def set_generation_control_master(self, control_master: object) -> None:
        if control_master is not None and not isinstance(control_master, _SshcpControlMaster):
            raise TypeError("Invalid SSH control master")
        self.__control_master = control_master

    def clear_generation_control_master(self) -> None:
        self.__control_master = None

    def start_generation_control_master(self) -> None:
        control_master = self.__control_master
        if control_master is None:
            return
        with control_master.lock:
            if control_master.closed:
                return
            process = control_master.process
            if control_master.started and process is not None:
                isalive = getattr(process, "isalive", None)
                if not callable(isalive) or isalive():
                    return

            command_args = [
                "-M",
                "-S", control_master.path,
                "-N",
                "-T",
                "-o", "ControlMaster=yes",
                "-o", "ControlPersist=no",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "LogLevel=error",
            ]
            if self.__password is None:
                command_args += ["-o", "PasswordAuthentication=no"]
            else:
                command_args += ["-o", "PubkeyAuthentication=no"]
            command_args += ["-p", str(self.__port), self.__remote_address()]

            self.logger.debug("Starting generation-local SSH control master")
            start_time = time.time()
            sp, _using_spawn_fallback = self.__spawn_process(
                "ssh", command_args, preexec_fn=_linux_master_parent_death_preexec(os.getpid()))
            control_master.process = sp
            try:
                timeout_phase = "control master startup"
                if self.__password is not None:
                    timeout_phase = "password prompt"
                    i = sp.expect([
                        r'(?i)password:\s*',
                        pexpect.EOF,
                        'lost connection',
                        'Could not resolve hostname',
                        'Connection refused',
                        'Name or service not known',
                        'No route to host',
                        'Connection timed out',
                        'REMOTE HOST IDENTIFICATION HAS CHANGED',
                        'Permission denied',
                    ], timeout=self.__TIMEOUT_SECS)
                    self.__classify_expect_result(
                        "ssh",
                        sp,
                        i,
                        eof_error="Unknown error",
                        password_error=None,
                        scp_permission_denied_is_destination_error=False,
                    )
                    sp.sendline(self.__password)

                # The master has no success output after authentication. Poll
                # its socket while nonblockingly consuming only terminal
                # prompts/errors, so a rejected password retains the current
                # prompt error behavior instead of waiting for socket timeout.
                deadline = time.monotonic() + self.__CONTROL_MASTER_TIMEOUT_SECS
                while time.monotonic() < deadline:
                    if os.path.exists(control_master.path):
                        control_master.started = True
                        return
                    try:
                        i = sp.expect([
                            r'(?i)password:\s*',
                            pexpect.EOF,
                            'lost connection',
                            'Could not resolve hostname',
                            'Connection refused',
                            'Name or service not known',
                            'No route to host',
                            'Connection timed out',
                            'REMOTE HOST IDENTIFICATION HAS CHANGED',
                            'Permission denied',
                        ], timeout=0)
                    except pexpect.exceptions.TIMEOUT:
                        pass
                    else:
                        if i == 0:
                            raise SshcpError("Incorrect password")
                        self.__classify_expect_result(
                            "ssh",
                            sp,
                            i,
                            eof_error="Incorrect password" if self.__password is not None else "Unknown error",
                            password_error="Incorrect password",
                            scp_permission_denied_is_destination_error=False,
                        )
                    isalive = getattr(sp, "isalive", None)
                    if callable(isalive) and not isalive():
                        before = self.__decode_spawn_output(getattr(sp, "before", b"")).strip()
                        self.__check_shell_not_found(before)
                        raise SshcpError(before or "Unknown error")
                    time.sleep(0.05)

                self.__log_timeout(timeout_phase, "ssh", sp, start_time)
                raise SshcpError("Timed out")
            except pexpect.exceptions.TIMEOUT:
                self.__log_timeout(timeout_phase, "ssh", sp, start_time)
                raise SshcpError("Timed out")
            except Exception:
                try:
                    close = getattr(sp, "close", None)
                    if callable(close):
                        try:
                            close(force=True)
                        except TypeError:
                            close()
                except Exception:
                    self.logger.warning("Failed to clean up failed SSH control master", exc_info=True)
                control_master.process = None
                control_master.started = False
                raise

    def close_generation_control_master(self) -> None:
        control_master = self.__control_master
        if control_master is not None:
            control_master.close(self.logger, self.__remote_address(), self.__port)

    def __begin_duration(self) -> object:
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            return None
        try:
            return diagnostics.begin_duration(DURATION_REMOTE_SCAN_TRANSPORT_READ)
        except Exception:
            return None

    def __finish_duration(self, started_at: object) -> None:
        if started_at is None:
            return
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            return
        try:
            diagnostics.finish_duration(DURATION_REMOTE_SCAN_TRANSPORT_READ, started_at)
        except Exception:
            pass

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

    def __generation_control_options(self) -> List[str]:
        control_master = self.__control_master
        if control_master is None or not control_master.started or control_master.closed:
            return []
        # BatchMode is required after the foreground master has consumed the
        # only password prompt for this generation.
        return [
            "-o", "ControlPath={}".format(control_master.path),
            "-o", "ControlMaster=no",
            "-o", "BatchMode=yes",
        ]

    def __using_generation_control_master(self) -> bool:
        control_master = self.__control_master
        return control_master is not None and control_master.started and not control_master.closed

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
        available_shells: List[str] = []
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

        command_args += self.__generation_control_options()

        command_args += [
            "-P", str(self.__port),
            self.__remote_address(),
        ]

        self.logger.debug("Command: {}".format(command_args))

        sp, _using_spawn_fallback = self.__spawn_process(command_args[0], command_args[1:])
        try:
            timeout = 30
            if self.__password is not None and not self.__using_generation_control_master():
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

    def __spawn_process(self, command: str, command_args: list[str], preexec_fn=None,
                        force_popen_spawn: bool = False) -> tuple[Any, bool]:
        spawn_factory = getattr(pexpect, "spawn", None)
        child_env = None
        if self.__host not in {"127.0.0.1", "localhost"} and "." not in self.__host and ":" not in self.__host:
            child_env = os.environ.copy()
            child_env["RES_OPTIONS"] = "attempts:1 timeout:1"
        if callable(spawn_factory) and not force_popen_spawn:
            spawn_kwargs = {
                "maxread": self.__PEXPECT_MAX_READ_BYTES,
                "searchwindowsize": self.__PEXPECT_SEARCH_WINDOW_BYTES,
            }
            if child_env is not None:
                spawn_kwargs["env"] = child_env
            if preexec_fn is not None:
                spawn_kwargs["preexec_fn"] = preexec_fn
            return spawn_factory(command, command_args, **spawn_kwargs), False
        else:
            resolved_command = shutil.which(command) or command
            popen_kwargs = {} if child_env is None else {"env": child_env}
            return pexpect.popen_spawn.PopenSpawn([resolved_command] + command_args, **popen_kwargs), True

    @staticmethod
    def __bounded_reap_popen_stream(sp: Any) -> Optional[int]:
        process = getattr(sp, "proc", None)
        if process is None:
            return getattr(sp, "exitstatus", None)
        try:
            return process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            process.terminate()
        try:
            return process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            process.kill()
        try:
            return process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            return process.poll()

    def __run_command(self,
                      command: str,
                      flags: List[str],
                      args: List[str]) -> bytes:

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

        command_args += self.__generation_control_options()

        command_args += args

        self.logger.debug("Command: {}".format(command_args))

        start_time = time.time()
        sp, _using_spawn_fallback = self.__spawn_process(command_args[0], command_args[1:])
        transport_started = self.__begin_duration()
        timeout_phase: str = "command execution"
        cleanup_exitstatus = None
        try:
            if self.__password is not None and not self.__using_generation_control_master():
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
        finally:
            # Always reap the child and release its pty, including timeout and
            # expect/classification failures. Cleanup is best-effort so it
            # cannot replace the primary command failure.
            try:
                close = getattr(sp, "close", None)
                if callable(close):
                    close()
                else:
                    wait = getattr(sp, "wait", None)
                    if callable(wait):
                        cleanup_exitstatus = wait()
            except Exception:
                self.logger.warning("Failed to clean up SSH child process", exc_info=True)
            self.__finish_duration(transport_started)
        end_time = time.time()

        exitstatus = getattr(sp, "exitstatus", None)
        if exitstatus is None:
            exitstatus = cleanup_exitstatus
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

    def shell_stream(self, command: str, on_chunk, retain_output: bool = True) -> bytes:
        """Run a shell command and forward stdout chunks as they arrive.

        The command is still executed by one SSH process (like :meth:`shell`),
        but callers can consume a long-running response before the remote
        command reaches EOF.  The complete response is returned as bytes for
        callers that also need a final integrity check.
        """
        if not command:
            raise ValueError("Command cannot be empty")
        if not callable(on_chunk):
            raise TypeError("on_chunk must be callable")

        if self.__detected_shell is None and "'" in command and '"' in command:
            raise ValueError("Command cannot contain both single and double quotes")

        if self.__detected_shell is not None:
            command = "{} -c {}".format(
                shlex.quote(self.__detected_shell),
                shlex.quote(command)
            )

        if self.__using_generation_control_master():
            # A multiplexed SSH channel can disappear without the remote
            # non-interactive command receiving a hangup.  Keep a stdin-EOF
            # watchdog beside a dedicated session: channel loss kills that
            # session and its descendants, while normal command completion
            # stops the watchdog and preserves the command exit status.
            command = (
                "exec 3<&0; setsid sh -c {} & child=$!; "
                "( while IFS= read -r _ <&3; do :; done; kill -TERM -$child 2>/dev/null ) & watcher=$!; "
                "wait $child; status=$?; kill $watcher 2>/dev/null; wait $watcher 2>/dev/null; exit $status"
            ).format(shlex.quote(command))

        flags = [
            "-p", str(self.__port),  # port
        ]
        args = [
            self.__remote_address(),
            command
        ]
        return self.__run_command_stream(
            command="ssh",
            flags=flags,
            args=args,
            on_chunk=on_chunk,
            retain_output=retain_output,
        )

    def __run_command_stream(self,
                             command: str,
                             flags: List[str],
                             args: List[str],
                             on_chunk,
                             retain_output: bool = True) -> bytes:
        """Execute one command while forwarding output incrementally."""
        command_args = [command] + flags
        command_args += [
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=error",
        ]
        if self.__password is None:
            command_args += ["-o", "PasswordAuthentication=no"]
        else:
            command_args += ["-o", "PubkeyAuthentication=no"]
        command_args += self.__generation_control_options()
        command_args += args

        self.logger.debug("Command: {}".format(command_args))
        start_time = time.time()
        multiplexed_stream = self.__using_generation_control_master()
        sp, _using_spawn_fallback = self.__spawn_process(
            command_args[0], command_args[1:],
            force_popen_spawn=multiplexed_stream,
        )
        transport_started = self.__begin_duration()
        timeout_phase = "command execution"
        output = bytearray()
        error_output = bytearray()
        cleanup_exitstatus = None
        try:
            if self.__password is not None and not self.__using_generation_control_master():
                timeout_phase = "password prompt"
                i = sp.expect([
                    r'(?i)password:\s*',
                    pexpect.EOF,
                    'lost connection',
                    'Could not resolve hostname',
                    'Connection refused',
                    'Name or service not known',
                    'No route to host',
                    'Connection timed out',
                    'REMOTE HOST IDENTIFICATION HAS CHANGED',
                    'Permission denied',
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

            while True:
                try:
                    chunk = sp.read_nonblocking(
                        size=self.__PEXPECT_MAX_READ_BYTES,
                        timeout=1
                    )
                except pexpect.exceptions.TIMEOUT:
                    if time.time() - start_time >= self.__TIMEOUT_SECS:
                        self.__log_timeout(timeout_phase, command, sp, start_time)
                        raise SshcpError("Timed out")
                    continue
                except (pexpect.exceptions.EOF, pexpect.EOF):
                    break

                if chunk is None:
                    continue
                if not isinstance(chunk, bytes):
                    chunk = self.__decode_spawn_output(chunk).encode()
                if not chunk:
                    if _using_spawn_fallback:
                        remaining_timeout = self.__TIMEOUT_SECS - (time.time() - start_time)
                        if remaining_timeout <= 0:
                            self.__log_timeout(timeout_phase, command, sp, start_time)
                            raise SshcpError("Timed out")
                        time.sleep(min(0.01, remaining_timeout))
                    continue
                if retain_output:
                    output.extend(chunk)
                else:
                    remaining = self.__STREAM_ERROR_CAPTURE_BYTES - len(error_output)
                    if remaining > 0:
                        error_output.extend(chunk[:remaining])
                on_chunk(chunk)
        except pexpect.exceptions.TIMEOUT:
            self.__log_timeout(timeout_phase, command, sp, start_time)
            raise SshcpError("Timed out")
        finally:
            try:
                if _using_spawn_fallback:
                    cleanup_exitstatus = self.__bounded_reap_popen_stream(sp)
                else:
                    close = getattr(sp, "close", None)
                    if callable(close):
                        close()
                    else:
                        wait = getattr(sp, "wait", None)
                        if callable(wait):
                            cleanup_exitstatus = wait()
            except Exception:
                self.logger.warning("Failed to clean up SSH child process", exc_info=True)
            self.__finish_duration(transport_started)

        exitstatus = getattr(sp, "exitstatus", None)
        if exitstatus is None:
            exitstatus = cleanup_exitstatus
        if exitstatus is None and not _using_spawn_fallback:
            wait = getattr(sp, "wait", None)
            if callable(wait):
                exitstatus = wait()

        self.logger.debug("Return code: {}".format(exitstatus))
        self.logger.debug("Command took {:.3f}s".format(time.time() - start_time))
        if exitstatus != 0:
            diagnostic = output if retain_output else error_output
            output_text = bytes(diagnostic).decode(errors="replace").strip()
            self.logger.warning("Command failed: '{}'".format(output_text))
            self.__check_shell_not_found(output_text)
            raise SshcpError(output_text)
        if not retain_output:
            return b""
        return bytes(output).replace(b'\r\n', b'\n').strip()

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
