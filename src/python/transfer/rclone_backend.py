# Copyright 2026, SeedSync Contributors, All rights reserved.

from __future__ import annotations

import logging
import os
import posixpath
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional

from common import AppError, Constants
from common.exclude_patterns import parse_exclude_patterns
from common.redaction import redact_sensitive_text
from lftp import LftpJobStatus


class RcloneTransferError(AppError):
    def __init__(self, message: str = ""):
        super().__init__(redact_sensitive_text("" if message is None else message))


@dataclass
class _TransferSample:
    size_local: int = 0
    observed_at: float = field(default_factory=time.monotonic)


@dataclass
class _RcloneJob:
    job_id: int
    name: str
    is_dir: bool
    remote_base_dir_path: str
    local_base_dir_path: str
    destination_path: str
    final_destination_path: str
    path_pair_id: Optional[str]
    path_pair_name: Optional[str]
    exclude_patterns: Iterable[str]
    process: Optional[subprocess.Popen[str]] = None
    cancel_requested: bool = False
    sample: _TransferSample = field(default_factory=_TransferSample)


class RcloneTransferBackend:
    backend_name = "rclone"
    _MISSING_EXECUTABLE_MESSAGE = "rclone backend requires the 'rclone' executable to be installed and available on PATH."

    def __init__(
        self,
        address: str,
        port: int,
        user: str,
        password: Optional[str],
        use_ssh_key: bool = False,
    ):
        self.__rclone_executable = self.__resolve_rclone_executable()
        self.__address = self.__validate_config_field("host", address)
        self.__port = port
        self.__user = self.__validate_config_field("user", user)
        self.__password = None if use_ssh_key else password
        self.__use_ssh_key = use_ssh_key
        self.__base_remote_dir_path = ""
        self.__base_local_dir_path = ""
        self.__path_pairs_by_id: Dict[str, Dict[str, str]] = {}
        self.__pending_jobs: Deque[_RcloneJob] = deque()
        self.__running_jobs: Dict[int, _RcloneJob] = {}
        self.__next_job_id = 1
        self.__lock = threading.RLock()
        self.__pending_error: Optional[str] = None
        self.__temp_dir_obj = tempfile.TemporaryDirectory(prefix="seedsync-rclone-")
        self.__config_path = os.path.join(self.__temp_dir_obj.name, "rclone.conf")
        self.logger = logging.getLogger("RcloneTransferBackend")

        self.__num_parallel_jobs = 1
        self.__num_parallel_files = 1
        self.__num_connections_per_root_file = 1
        self.__num_connections_per_dir_file = 1
        self.__num_max_total_connections = 0
        self.__use_temp_file = False
        self.__rate_limit = "0"
        self.__net_socket_buffer = ""
        self.__verbose_logging = False

        self.__write_config()

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("RcloneTransferBackend")

    def set_base_remote_dir_path(self, base_remote_dir_path: str):
        self.__base_remote_dir_path = base_remote_dir_path

    def set_base_local_dir_path(self, base_local_dir_path: str):
        self.__base_local_dir_path = base_local_dir_path

    def set_path_pairs(self, path_pairs):
        self.__path_pairs_by_id = {
            pair.id: {
                "name": pair.name,
                "remote_path": pair.remote_path,
                "local_path": pair.local_path,
            } for pair in path_pairs
        }

    def raise_pending_error(self):
        if self.__pending_error:
            error = self.__pending_error
            self.__pending_error = None
            raise RcloneTransferError(error)

    @property
    def last_status_poll_healthy(self) -> bool:
        return True

    @property
    def num_parallel_jobs(self) -> int:
        return self.__num_parallel_jobs

    @num_parallel_jobs.setter
    def num_parallel_jobs(self, value: int):
        self.__num_parallel_jobs = max(1, int(value))
        self.__start_jobs_if_capacity()

    @property
    def num_parallel_files(self) -> int:
        return self.__num_parallel_files

    @num_parallel_files.setter
    def num_parallel_files(self, value: int):
        self.__num_parallel_files = max(1, int(value))

    @property
    def num_connections_per_root_file(self) -> int:
        return self.__num_connections_per_root_file

    @num_connections_per_root_file.setter
    def num_connections_per_root_file(self, value: int):
        self.__num_connections_per_root_file = max(1, int(value))

    @property
    def num_connections_per_dir_file(self) -> int:
        return self.__num_connections_per_dir_file

    @num_connections_per_dir_file.setter
    def num_connections_per_dir_file(self, value: int):
        self.__num_connections_per_dir_file = max(1, int(value))

    @property
    def num_max_total_connections(self) -> int:
        return self.__num_max_total_connections

    @num_max_total_connections.setter
    def num_max_total_connections(self, value: int):
        self.__num_max_total_connections = max(0, int(value))

    @property
    def use_temp_file(self) -> bool:
        return self.__use_temp_file

    @use_temp_file.setter
    def use_temp_file(self, value: bool):
        self.__use_temp_file = bool(value)

    @property
    def rate_limit(self) -> str:
        return self.__rate_limit

    @rate_limit.setter
    def rate_limit(self, value):
        self.__rate_limit = "0" if value in (None, "") else str(value)

    @property
    def net_socket_buffer(self) -> str:
        return self.__net_socket_buffer

    @net_socket_buffer.setter
    def net_socket_buffer(self, value):
        self.__net_socket_buffer = "" if value is None else str(value)

    @property
    def temp_file_name(self) -> str:
        return "*" + Constants.LFTP_TEMP_FILE_SUFFIX

    @temp_file_name.setter
    def temp_file_name(self, _value: str):
        return

    @property
    def xfer_verify(self) -> bool:
        return False

    @xfer_verify.setter
    def xfer_verify(self, _value: bool):
        return

    @property
    def xfer_verify_command(self) -> str:
        return ""

    @xfer_verify_command.setter
    def xfer_verify_command(self, _value: str):
        return

    def set_verbose_logging(self, verbose: bool):
        self.__verbose_logging = bool(verbose)

    def queue(
        self,
        name: str,
        is_dir: bool,
        remote_base_dir_path: Optional[str] = None,
        local_base_dir_path: Optional[str] = None,
        exclude_patterns: str | Iterable[str] | None = None,
    ):
        with self.__lock:
            remote_base = remote_base_dir_path if remote_base_dir_path is not None else self.__base_remote_dir_path
            local_base = local_base_dir_path if local_base_dir_path is not None else self.__base_local_dir_path
            destination_name = name if is_dir or not self.__use_temp_file else name + Constants.LFTP_TEMP_FILE_SUFFIX
            destination_path = os.path.join(local_base, destination_name)
            final_destination_path = os.path.join(local_base, name)
            path_pair_id, path_pair_name = self.__resolve_path_pair(remote_base, local_base)
            job = _RcloneJob(
                job_id=self.__next_job_id,
                name=name,
                is_dir=is_dir,
                remote_base_dir_path=remote_base,
                local_base_dir_path=local_base,
                destination_path=destination_path,
                final_destination_path=final_destination_path,
                path_pair_id=path_pair_id,
                path_pair_name=path_pair_name,
                exclude_patterns=parse_exclude_patterns(exclude_patterns),
            )
            self.__next_job_id += 1
            self.__pending_jobs.append(job)
            self.__start_jobs_if_capacity()

    def kill(
        self,
        name: str,
        path_pair_id: Optional[str] = None,
        remote_path: Optional[str] = None,
        local_path: Optional[str] = None,
    ) -> bool:
        with self.__lock:
            for job in list(self.__pending_jobs):
                if self.__job_matches(job, name, path_pair_id, remote_path, local_path):
                    self.__pending_jobs.remove(job)
                    return True
            for job in list(self.__running_jobs.values()):
                if self.__job_matches(job, name, path_pair_id, remote_path, local_path):
                    job.cancel_requested = True
                    if job.process is not None and job.process.poll() is None:
                        job.process.terminate()
                    return True
        return False

    def status(self) -> List[LftpJobStatus]:
        with self.__lock:
            self.__reap_finished_jobs()
            statuses: List[LftpJobStatus] = []
            for job in self.__pending_jobs:
                statuses.append(self.__build_status(job, LftpJobStatus.State.QUEUED))
            for job in self.__running_jobs.values():
                statuses.append(self.__build_status(job, LftpJobStatus.State.RUNNING))
            return statuses

    def kill_all(self):
        with self.__lock:
            self.__pending_jobs.clear()
            for job in list(self.__running_jobs.values()):
                job.cancel_requested = True
                if job.process is not None and job.process.poll() is None:
                    job.process.terminate()
            self.__reap_finished_jobs(force=True)

    def exit(self):
        self.kill_all()
        self.__temp_dir_obj.cleanup()

    def __resolve_path_pair(self, remote_base: str, local_base: str) -> tuple[Optional[str], Optional[str]]:
        for pair_id, pair in self.__path_pairs_by_id.items():
            if pair["remote_path"] == remote_base and pair["local_path"] == local_base:
                return pair_id, pair["name"]
        return None, None

    def __build_status(self, job: _RcloneJob, state: LftpJobStatus.State) -> LftpJobStatus:
        status = LftpJobStatus(
            job.job_id,
            LftpJobStatus.Type.MIRROR if job.is_dir else LftpJobStatus.Type.PGET,
            state,
            job.name,
            "",
            remote_path=self.__job_remote_path(job),
            local_path=job.destination_path,
        )
        status.path_pair_id = job.path_pair_id
        status.path_pair_name = job.path_pair_name
        if state == LftpJobStatus.State.RUNNING:
            size_local = self.__measure_local_size(job.destination_path)
            now = time.monotonic()
            speed = None
            elapsed = now - job.sample.observed_at
            if elapsed > 0 and size_local >= job.sample.size_local:
                delta = size_local - job.sample.size_local
                speed = int(delta / elapsed) if delta > 0 else 0
            job.sample = _TransferSample(size_local=size_local, observed_at=now)
            status.total_transfer_state = LftpJobStatus.TransferState(
                size_local,
                None,
                None,
                speed,
                None,
            )
        return status

    def __job_matches(
        self,
        job: _RcloneJob,
        name: str,
        path_pair_id: Optional[str],
        remote_path: Optional[str],
        local_path: Optional[str],
    ) -> bool:
        if job.name != name:
            return False
        if path_pair_id is not None and job.path_pair_id != path_pair_id:
            return False
        if remote_path is not None and not self.__path_is_within(self.__job_remote_path(job), remote_path):
            return False
        if local_path is not None and not self.__path_is_within(job.destination_path, local_path):
            return False
        return True

    @staticmethod
    def __path_is_within(path: Optional[str], root: str) -> bool:
        if path is None:
            return False
        normalized_path = os.path.normpath(path)
        normalized_root = os.path.normpath(root)
        try:
            return os.path.commonpath([normalized_path, normalized_root]) == normalized_root
        except ValueError:
            return False

    def __start_jobs_if_capacity(self):
        with self.__lock:
            while len(self.__running_jobs) < self.__num_parallel_jobs and self.__pending_jobs:
                job = self.__pending_jobs.popleft()
                try:
                    job.process = subprocess.Popen(
                        self.__build_command(job),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                except FileNotFoundError as exc:
                    raise RcloneTransferError(self._MISSING_EXECUTABLE_MESSAGE) from exc
                job.sample = _TransferSample(
                    size_local=self.__measure_local_size(job.destination_path),
                    observed_at=time.monotonic(),
                )
                self.__running_jobs[job.job_id] = job

    def __reap_finished_jobs(self, force: bool = False):
        finished_job_ids = []
        for job_id, job in list(self.__running_jobs.items()):
            process = job.process
            if process is None:
                finished_job_ids.append(job_id)
                continue
            if force and process.poll() is None:
                process.kill()
            if process.poll() is None:
                continue
            stdout, stderr = process.communicate()
            if process.returncode == 0 and not job.cancel_requested:
                self.__finalize_success(job)
            elif not job.cancel_requested:
                error_output = stderr.strip() or stdout.strip() or "rclone transfer failed"
                self.__pending_error = self.__sanitize_error_output(error_output)
            finished_job_ids.append(job_id)
        for job_id in finished_job_ids:
            self.__running_jobs.pop(job_id, None)
        if finished_job_ids:
            self.__start_jobs_if_capacity()

    def __finalize_success(self, job: _RcloneJob):
        if job.is_dir or not self.__use_temp_file:
            return
        if os.path.exists(job.destination_path):
            os.replace(job.destination_path, job.final_destination_path)

    def __job_remote_path(self, job: _RcloneJob) -> str:
        return posixpath.join(job.remote_base_dir_path.rstrip("/"), job.name)

    def __build_command(self, job: _RcloneJob) -> List[str]:
        remote_path = "seedsync:{}".format(self.__job_remote_path(job))
        command = [
            self.__rclone_executable,
            "copy" if job.is_dir else "copyto",
            "--config", self.__config_path,
        ]
        if self.__rate_limit not in ("", "0", None):
            command.extend(["--bwlimit", str(self.__rate_limit)])
        if self.__verbose_logging:
            command.append("-vv")
        if job.is_dir:
            command.extend(["--transfers", str(self.__num_parallel_files)])
        for pattern in job.exclude_patterns:
            command.extend(["--exclude", pattern])
        command.append(remote_path)
        command.append(job.destination_path if not job.is_dir else job.final_destination_path)
        return command

    def __write_config(self):
        lines = [
            "[seedsync]",
            "type = sftp",
            "host = {}".format(self.__address),
            "user = {}".format(self.__user),
            "port = {}".format(self.__port),
            "shell_type = unix",
        ]
        if self.__password:
            lines.append("pass = {}".format(self.__obscure_password(self.__password)))
        elif self.__use_ssh_key:
            lines.append("key_use_agent = true")
        with open(self.__config_path, "w", encoding="utf-8") as config_file:
            config_file.write("\n".join(lines) + "\n")

    def __obscure_password(self, password: str) -> str:
        try:
            result = subprocess.run(
                [self.__rclone_executable, "obscure"],
                input=password,
                text=True,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RcloneTransferError(self._MISSING_EXECUTABLE_MESSAGE) from exc
        if result.returncode != 0:
            raise RcloneTransferError(
                "Failed to prepare rclone password: {}".format(
                    self.__sanitize_error_output(result.stderr.strip() or result.stdout.strip())
                )
            )
        return result.stdout.strip()

    @classmethod
    def __resolve_rclone_executable(cls) -> str:
        rclone_executable = shutil.which("rclone")
        if not rclone_executable:
            raise RcloneTransferError(cls._MISSING_EXECUTABLE_MESSAGE)
        return rclone_executable

    @staticmethod
    def __validate_config_field(field_name: str, value: str) -> str:
        if not isinstance(value, str):
            raise RcloneTransferError("Invalid rclone {} value.".format(field_name))
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise RcloneTransferError(
                "Invalid rclone {} value: control characters are not allowed.".format(field_name)
            )
        return value

    def __sanitize_error_output(self, message: str) -> str:
        sanitized_message = redact_sensitive_text(message) or ""
        if self.__config_path:
            sanitized_message = sanitized_message.replace(self.__config_path, "<rclone-config>")
        temp_dir_path = getattr(self.__temp_dir_obj, "name", None)
        if isinstance(temp_dir_path, str) and temp_dir_path:
            sanitized_message = sanitized_message.replace(temp_dir_path, "<rclone-tempdir>")
        return sanitized_message

    @staticmethod
    def __measure_local_size(path: str) -> int:
        if os.path.isfile(path):
            try:
                return os.path.getsize(path)
            except OSError:
                return 0
        if os.path.isdir(path):
            total_size = 0
            for root, _, files in os.walk(path):
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    try:
                        total_size += os.path.getsize(file_path)
                    except OSError:
                        continue
            return total_size
        return 0
