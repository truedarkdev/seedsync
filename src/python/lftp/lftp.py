# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import re
import os
import time
from functools import wraps
from typing import Callable, Union, List, Optional, Dict

# 3rd party libs
import pexpect

# my libs
from common import AppError
from common.config import Checkers
from .job_status_parser import LftpJobStatus, LftpJobStatusParser, LftpJobStatusParserError


# How many status errors are allowed before error propagates out
MAX_CONSECUTIVE_STATUS_ERRORS = 10
MAX_KILL_MATCH_ATTEMPTS = 20
STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS = 1.0


class LftpError(AppError):
    """
    Custom exception that describes the failure of the lftp command
    """
    pass


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
    __SET_SFTP_AUTO_CONFIRM = "sftp:auto-confirm"
    __SET_SFTP_CONNECT_PROGRAM = "sftp:connect-program"
    __SET_SFTP_SET_PERMISSIONS = "sftp:set-permissions"

    @staticmethod
    def __has_valid_umask() -> bool:
        umask_value = os.environ.get("UMASK", "")
        if not umask_value:
            return False

        return all(character in "01234567" for character in umask_value)

    def __init__(self,
                 address: str,
                 port: int,
                 user: str,
                 password: Optional[str]):
        self.__user = user
        self.__password = password
        self.__address = address
        self.__base_remote_dir_path = ""
        self.__base_local_dir_path = ""
        self.logger = logging.getLogger("Lftp")
        self.__expect_pattern = "lftp {}@{}:.*>".format(self.__user, self.__address)
        self.__job_status_parser = LftpJobStatusParser()
        self.__timeout = 30  # in seconds
        self.__consecutive_status_errors = 0
        self.__path_pairs_by_id: Dict[str, Dict[str, str]] = {}
        self.__last_command_timed_out = False
        self.__last_status_poll_healthy = True
        self.__status_poll_needs_connection_grace = False

        self.__log_command_output = False
        self.__pending_error = None

        args = [
            "-p", str(port),
            "-u", "{},{}".format(self.__user, self.__password if self.__password else ""),
            "sftp://{}".format(self.__address)
        ]
        spawn_env = os.environ.copy()
        spawn_env["COLUMNS"] = "10000"
        self.__process = pexpect.spawn("/usr/bin/lftp", args, env=spawn_env, dimensions=(24, 10000))
        try:
            self.__process.expect(self.__expect_pattern)
        except pexpect.exceptions.TIMEOUT:
            out = self.__process.before.decode("utf8", "replace").strip()
            if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "startup"):
                raise
        self.__setup()

    def set_verbose_logging(self, verbose: bool):
        self.__log_command_output = verbose

    def __setup(self):
        """
        Setup the lftp instance with default settings
        :return:
        """
        # Set to kill on exit to prevent a zombie process
        self.__set(Lftp.__SET_COMMAND_AT_EXIT, "\"kill all\"")
        # Auto-add server to known host file
        self.sftp_auto_confirm = True
        # Let a valid explicit UMASK control downloaded file permissions.
        if Lftp.__has_valid_umask():
            self.__set(Lftp.__SET_SFTP_SET_PERMISSIONS, "false")
        # Keep pget status snapshots fresher to reduce valid stop/resume rollback.
        self.__set(Lftp.__SET_PGET_SAVE_STATUS, "2")

    def with_check_process(method: Callable):
        """
        Decorator that checks for a valid process before executing
        the decorated method
        :param method:
        :return:
        """
        @wraps(method)
        def wrapper(inst: "Lftp", *args, **kwargs):
            if inst.__process is None or not inst.__process.isalive():
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

    def set_path_pairs(self, path_pairs):
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
            out = self.__normalize_output(self.__process.before.decode("utf8", "replace"))
            if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, context):
                self.__process.sendline()
                try:
                    self.__process.expect(self.__expect_pattern, timeout=3)
                except pexpect.exceptions.TIMEOUT:
                    retry_out = self.__normalize_output(self.__process.before.decode("utf8", "replace"))
                    if not self.__raise_lftp_error_for_ssh_host_key_prompt(retry_out, context):
                        self.logger.warning("Lftp timeout exception")
                        raise LftpError("Lftp process is not ready for {}: {}".format(context, retry_out))
                except pexpect.exceptions.EOF:
                    retry_out = self.__normalize_output(self.__process.before.decode("utf8", "replace"))
                    self.logger.error("Lftp process died unexpectedly (EOF) before {}".format(context))
                    raise LftpError("Lftp process terminated before {}: {}".format(context, retry_out))
        except pexpect.exceptions.EOF:
            out = self.__normalize_output(self.__process.before.decode("utf8", "replace"))
            self.logger.error("Lftp process died unexpectedly (EOF) before {}".format(context))
            raise LftpError("Lftp process terminated before {}: {}".format(context, out))

    @with_check_process
    def __run_command(self, command: str, timeout_seconds: Optional[int] = None, require_prompt_ready: bool = True, status_poll: bool = False):
        self.__last_command_timed_out = False
        restore_delaybeforesend = None
        restore_delayafterread = None
        out = ""
        status_poll_timeout_seconds = None
        log_command_output = self.__log_command_output and not status_poll
        if status_poll:
            status_poll_timeout_seconds = STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS if timeout_seconds == 0 else timeout_seconds
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
                                    self.__normalize_output(self.__process.before.decode("utf8", "replace"))
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
                        out = self.__normalize_output(self.__process.before.decode("utf8", "replace"))
                        if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "running command"):
                            self.logger.warning("Lftp timeout exception")
                        pass
                    except pexpect.exceptions.EOF:
                        self.logger.error("Lftp process died unexpectedly (EOF)")
                        raise LftpError("Lftp process terminated: {}".format(
                            self.__normalize_output(self.__process.before.decode("utf8", "replace"))
                        ))
            finally:
                out = self.__normalize_output(self.__process.before.decode('utf8', 'replace'))

                if log_command_output:
                    if status_poll:
                        self.logger.debug("status out ({} bytes, bounded)".format(len(out)))
                    else:
                        self.logger.debug("out ({} bytes):\n {}".format(len(out), out))
                    after = self.__process.after.decode('utf8', 'replace').strip() \
                        if self.__process.after not in (pexpect.TIMEOUT, None) else ""
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
                    out = self.__normalize_output(self.__process.before.decode("utf8", "replace"))

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
                    out = self.__normalize_output(self.__process.before.decode("utf8", "replace"))
                    if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "recovering from error"):
                        self.logger.warning("Lftp timeout exception")
                    pass
                except pexpect.exceptions.EOF:
                    self.logger.error("Lftp process died unexpectedly (EOF) during error recovery")
                    raise LftpError("Lftp process terminated during error recovery")
                finally:
                    out = self.__normalize_output(self.__process.before.decode('utf8', 'replace'))
                    if log_command_output:
                        self.logger.debug("retry out ({} bytes):\n {}".format(len(out), out))
                        after = self.__process.after.decode('utf8', 'replace').strip() \
                            if self.__process.after not in (pexpect.TIMEOUT, None) else ""
                        self.logger.debug("retry after: {}".format(after))
                    self.logger.error("Lftp detected error: {}".format(error_out))
                    # save pending error
                    self.__pending_error = error_out
            return out
        finally:
            if restore_delaybeforesend is not None:
                self.__process.delaybeforesend = restore_delaybeforesend
            if restore_delayafterread is not None:
                self.__process.delayafterread = restore_delayafterread

    @staticmethod
    def __detect_errors_from_output(out: str) -> bool:
        errors = [
            "pget: Access failed",
            "pget-chunk: Access failed",
            "mirror: Access failed",
            "Login failed: Login incorrect"
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
        self.__run_command("set {} {}".format(setting, value), require_prompt_ready=False)

    def __get(self, setting: str) -> str:
        """
        Get a setting from the lftp runtime
        :param setting:
        :return:
        """
        out = self.__run_command("set -a | grep {}".format(setting))
        m = re.search("set {} (.*)".format(setting), out)
        if not m or not m.group or not m.group(1):
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
            out = self.__run_command("jobs -v", timeout_seconds=0, require_prompt_ready=False, status_poll=True)
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
            out = self.__run_command(
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
        sorted_pairs = sorted(
            self.__path_pairs_by_id.items(),
            key=lambda item: max(len(item[1]["remote_path"]), len(item[1]["local_path"])),
            reverse=True
        )
        for status in statuses:
            match = next((
                (pair_id, pair) for pair_id, pair in sorted_pairs
                if self.__status_matches_paths(
                    status,
                    pair["remote_path"],
                    pair["local_path"]
                )
            ), None)
            if match is None:
                continue
            pair_id, pair = match
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
        return remote_matches or local_matches

    def queue(self,
              name: str,
              is_dir: bool,
              remote_base_dir_path: Optional[str] = None,
              local_base_dir_path: Optional[str] = None):
        """
        Queues a job for download
        This method may cause an exception to be generated in a later method call:
          * Wrong type (is_dir) is specified
          * File/folder does not exist
        :param name: name of file or folder to download
        :param is_dir: true if folder, false if file
        :return:
        """
        # Escape single and double quotes in any string used in queue command
        def escape(s: str) -> str:
            return s.replace("'", "\\'").replace("\"", "\\\"")

        remote_dir = remote_base_dir_path if remote_base_dir_path is not None else self.__base_remote_dir_path
        local_dir = local_base_dir_path if local_base_dir_path is not None else self.__base_local_dir_path

        parts = [
            "queue",
            "'",
            "pget" if not is_dir else "mirror",
            "-c",
            "\"{remote_dir}/{filename}\"".format(remote_dir=escape(remote_dir),
                                                 filename=escape(name)),
            "-o" if not is_dir else None,
            "\"{local_dir}/\"".format(local_dir=escape(local_dir)),
            "'"
        ]
        command = " ".join(part for part in parts if part is not None)
        self.logger.info("queue command: %s", command)
        self.__run_command(command, require_prompt_ready=False)

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
        def find_matching_jobs():
            statuses = self.status()
            if statuses is None:
                # Parser failures come back as None; treat them as an empty
                # snapshot so the retry loop can keep probing safely.
                statuses = []
            status_poll_healthy = self.last_status_poll_healthy
            matching_jobs = []
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
        previous_match_signature = None
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
            match_signature = tuple((job.id, job.state) for job in matching_jobs)
            if match_signature == previous_match_signature:
                self.logger.warning("Kill did not converge for job '{}' after repeated matching polls".format(name))
                break
            previous_match_signature = match_signature
            attempts += 1
            job_to_kill = matching_jobs[0]
            killed_any = True
            # Note: there's a chance that job ids change between when we called status
            #       and when we execute the kill command
            #       in this case the wrong job may be killed, there's nothing we can do about it
            if job_to_kill.state == LftpJobStatus.State.RUNNING:
                self.logger.debug("Killing running job '{}'...".format(name))
                self.__run_command("kill {}".format(job_to_kill.id), require_prompt_ready=False)
            elif job_to_kill.state == LftpJobStatus.State.QUEUED:
                self.logger.debug("Killing queued job '{}'...".format(name))
                self.__run_command("queue --delete {}".format(job_to_kill.id), require_prompt_ready=False)
            else:
                raise NotImplementedError("Unsupported state {}".format(str(job_to_kill.state)))
        else:
            self.logger.warning("Kill reached max attempts for job '{}'".format(name))

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
        self.__run_command("queue -d *", require_prompt_ready=False)
        self.__run_command("kill all", require_prompt_ready=False, timeout_seconds=0)

    def exit(self):
        """
        Exit the lftp instance. It cannot be used after being killed
        :return:
        """
        self.kill_all()
        self.__process.sendline("exit")
        self.__process.close(force=True)

    # Mark decorators as static (must be at end of class)
    # Source: https://stackoverflow.com/a/3422823
    with_check_process = staticmethod(with_check_process)
