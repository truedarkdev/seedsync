# Copyright 2017, Inderpreet Singh, All rights reserved.

import datetime
import hashlib
import multiprocessing
import os
import queue
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from common import AppProcess
from model import ModelFile
from ssh import Sshcp


@dataclass(eq=True)
class ValidateStatus:
    file_id: str
    state: ModelFile.State
    progress: Optional[int] = None
    error: Optional[str] = None
    corrupt_chunks: Optional[List[int]] = None


@dataclass(eq=True)
class ValidateStatusResult:
    timestamp: datetime.datetime
    statuses: List[ValidateStatus]


class ValidateProcess(AppProcess):
    __DEFAULT_SLEEP_INTERVAL_IN_SECS = 0.1
    __HASH_CHUNK_SIZE = 1024 * 1024

    def __init__(self,
                 remote_address: str,
                 remote_username: str,
                 remote_password: Optional[str],
                 remote_port: int,
                 local_path: str,
                 remote_path: str,
                 path_pairs_by_id: Dict[str, object]):
        super().__init__(name=self.__class__.__name__)
        self.__command_queue = multiprocessing.Queue()
        self.__status_result_queue = multiprocessing.Queue()
        self.__ssh = Sshcp(
            host=remote_address,
            port=remote_port,
            user=remote_username,
            password=remote_password
        )
        self.__local_path = local_path
        self.__remote_path = remote_path
        self.__path_pairs_by_id = {
            pair_id: {
                "local_path": getattr(pair, "local_path", None),
                "remote_path": getattr(pair, "remote_path", None)
            } for pair_id, pair in path_pairs_by_id.items()
        }
        self.__statuses = {}

    def validate(self, file: ModelFile):
        self.__command_queue.put(("validate", file))

    def clear(self, file_id: str):
        self.__command_queue.put(("clear", file_id))

    def set_path_pairs_by_id(self, path_pairs_by_id: Dict[str, object]):
        self.__path_pairs_by_id = {
            pair_id: {
                "local_path": getattr(pair, "local_path", None),
                "remote_path": getattr(pair, "remote_path", None)
            } for pair_id, pair in path_pairs_by_id.items()
        }
        self.__command_queue.put(("set_path_pairs_by_id", self.__path_pairs_by_id))

    def pop_latest_statuses(self) -> Optional[ValidateStatusResult]:
        latest_result = None
        try:
            while True:
                latest_result = self.__status_result_queue.get(block=False)
        except queue.Empty:
            pass
        return latest_result

    def run_init(self):
        self.__ssh.set_base_logger(self.logger)

    def run_cleanup(self):
        pass

    def run_loop(self):
        try:
            command, payload = self.__command_queue.get(timeout=self.__DEFAULT_SLEEP_INTERVAL_IN_SECS)
        except queue.Empty:
            return

        if command == "clear":
            self.__statuses.pop(payload, None)
            self.__publish_statuses()
            return

        if command == "set_path_pairs_by_id":
            self.__path_pairs_by_id = payload
            return

        if command != "validate":
            return

        file = payload
        self.__set_status(file.file_id, ModelFile.State.VALIDATING, 0)
        try:
            is_valid, error = self.__validate(file)
            self.__set_status(
                file.file_id,
                ModelFile.State.VALIDATED if is_valid else ModelFile.State.CORRUPT,
                100,
                None if is_valid else error
            )
        except Exception as error:  # pragma: no cover - process-level safety
            self.logger.exception("Validation failed for %s", file.file_id)
            self.__set_status(file.file_id, ModelFile.State.CORRUPT, 100, str(error))

    def __publish_statuses(self):
        self.__status_result_queue.put(
            ValidateStatusResult(
                timestamp=datetime.datetime.now(),
                statuses=list(self.__statuses.values())
            )
        )

    def __set_status(self,
                     file_id: str,
                     state: ModelFile.State,
                     progress: Optional[int],
                     error: Optional[str] = None,
                     corrupt_chunks: Optional[List[int]] = None):
        self.__statuses[file_id] = ValidateStatus(
            file_id=file_id,
            state=state,
            progress=progress,
            error=error,
            corrupt_chunks=corrupt_chunks
        )
        self.__publish_statuses()

    def __validate(self, file: ModelFile) -> Tuple[bool, Optional[str]]:
        local_path = os.path.join(self.__get_local_base_path(file.path_pair_id), file.name)
        if not os.path.exists(local_path):
            return False, "Local file '{}' disappeared before validation".format(file.name)

        if file.is_dir:
            local_dirs, local_hashes = self.__build_local_directory_manifest(local_path, file.file_id)
            self.__set_status(file.file_id, ModelFile.State.VALIDATING, 60)
            remote_dirs, remote_hashes = self.__build_remote_directory_manifest(file.path_pair_id, file.name)
            self.__set_status(file.file_id, ModelFile.State.VALIDATING, 90)
            return self.__compare_directory_manifests(local_dirs, local_hashes, remote_dirs, remote_hashes)

        local_hash = self.__hash_local_file(local_path, file.file_id)
        remote_hash = self.__hash_remote_file(file.path_pair_id, file.name)
        if local_hash != remote_hash:
            return False, "Checksum mismatch"
        return True, None

    def __hash_local_file(self, local_path: str, file_id: str) -> str:
        total_size = os.path.getsize(local_path)
        hashed_size = 0
        digest = hashlib.sha256()
        with open(local_path, "rb") as file_handle:
            while True:
                chunk = file_handle.read(self.__HASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                hashed_size += len(chunk)
                progress = 50 if total_size == 0 else int(min(50, (hashed_size * 50) / total_size))
                self.__set_status(file_id, ModelFile.State.VALIDATING, progress)
        self.__set_status(file_id, ModelFile.State.VALIDATING, 50)
        return digest.hexdigest()

    def __build_local_directory_manifest(self,
                                         local_path: str,
                                         file_id: str) -> Tuple[Set[str], Dict[str, str]]:
        dirs = set()
        hashes = {}
        file_paths = []
        local_parent = os.path.dirname(local_path)
        for current_root, dir_names, file_names in os.walk(local_path):
            dir_names.sort()
            file_names.sort()
            dirs.add(os.path.relpath(current_root, local_parent))
            for file_name in file_names:
                file_paths.append(os.path.join(current_root, file_name))

        if not file_paths:
            self.__set_status(file_id, ModelFile.State.VALIDATING, 50)
            return dirs, hashes

        total_files = len(file_paths)
        for index, file_path in enumerate(file_paths, start=1):
            relative_path = os.path.relpath(file_path, local_parent)
            hashes[relative_path] = self.__hash_file(file_path)
            self.__set_status(file_id, ModelFile.State.VALIDATING, int((index * 50) / total_files))
        self.__set_status(file_id, ModelFile.State.VALIDATING, 50)
        return dirs, hashes

    @classmethod
    def __hash_file(cls, local_path: str) -> str:
        digest = hashlib.sha256()
        with open(local_path, "rb") as file_handle:
            while True:
                chunk = file_handle.read(cls.__HASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def __shell_double_quote(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("\"", "\\\"").replace("$", "\\$").replace("`", "\\`")
        return "\"{}\"".format(escaped)

    def __run_remote_command(self, path_pair_id: Optional[str], command: str) -> str:
        remote_base = self.__get_remote_base_path(path_pair_id)
        output = self.__ssh.shell(
            "cd {} && {}".format(self.__shell_double_quote(remote_base), command)
        )
        return output.decode()

    def __hash_remote_file(self, path_pair_id: Optional[str], file_name: str) -> str:
        output = self.__run_remote_command(
            path_pair_id,
            "sha256sum {}".format(self.__shell_double_quote(file_name))
        ).strip()
        return output.split(None, 1)[0]

    def __build_remote_directory_manifest(self,
                                          path_pair_id: Optional[str],
                                          root_name: str) -> Tuple[Set[str], Dict[str, str]]:
        dirs_output = self.__run_remote_command(
            path_pair_id,
            "find {} -type d | sort".format(self.__shell_double_quote(root_name))
        )
        dirs = set(filter(None, dirs_output.splitlines()))

        files_output = self.__run_remote_command(
            path_pair_id,
            "find {} -type f -exec sha256sum {{}} \\; | sort".format(self.__shell_double_quote(root_name))
        )
        hashes = {}
        for line in filter(None, files_output.splitlines()):
            digest, relative_path = line.split(None, 1)
            hashes[relative_path] = digest
        return dirs, hashes

    @staticmethod
    def __compare_directory_manifests(local_dirs: Set[str],
                                      local_hashes: Dict[str, str],
                                      remote_dirs: Set[str],
                                      remote_hashes: Dict[str, str]) -> Tuple[bool, Optional[str]]:
        if local_dirs != remote_dirs:
            missing_local = sorted(remote_dirs.difference(local_dirs))
            missing_remote = sorted(local_dirs.difference(remote_dirs))
            if missing_local:
                return False, "Missing local directories: {}".format(", ".join(missing_local[:3]))
            return False, "Missing remote directories: {}".format(", ".join(missing_remote[:3]))

        if local_hashes != remote_hashes:
            for path in sorted(set(local_hashes.keys()).union(remote_hashes.keys())):
                local_hash = local_hashes.get(path)
                remote_hash = remote_hashes.get(path)
                if local_hash != remote_hash:
                    if local_hash is None:
                        return False, "Missing local file: {}".format(path)
                    if remote_hash is None:
                        return False, "Missing remote file: {}".format(path)
                    return False, "Checksum mismatch for {}".format(path)

        return True, None

    def __get_local_base_path(self, path_pair_id: Optional[str]) -> str:
        pair = self.__path_pairs_by_id.get(path_pair_id)
        return pair["local_path"] if pair and pair.get("local_path") else self.__local_path

    def __get_remote_base_path(self, path_pair_id: Optional[str]) -> str:
        pair = self.__path_pairs_by_id.get(path_pair_id)
        return pair["remote_path"] if pair and pair.get("remote_path") else self.__remote_path
