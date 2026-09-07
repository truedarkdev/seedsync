# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
import posixpath
import shutil
import stat
from typing import Optional, Sequence

from common import AppOneShotProcess, escape_remote_path_for_shell
from ssh import Sshcp


class DeleteLocalProcess(AppOneShotProcess):
    def __init__(self, local_path: str, file_name: str,
                 artifact_paths: Sequence[str] = (), artifact_root: Optional[str] = None,
                 delete_primary: bool = True, allow_recursive: bool = False):
        super().__init__(name=self.__class__.__name__)
        self.__local_path = local_path
        self.__file_name: object = file_name
        self.__artifact_paths = tuple(artifact_paths)
        self.__artifact_root = artifact_root if artifact_root is not None else local_path
        self.__delete_primary = delete_primary
        self.__allow_recursive = allow_recursive

    def run_once(self):
        file_name = self.__file_name
        if not isinstance(file_name, str) or file_name == "" or "\x00" in file_name:
            self.logger.error("Invalid local delete filename: {}".format(self.__file_name))
            raise ValueError("Invalid local delete filename")
        if type(self.__allow_recursive) is not bool:
            self.logger.error("Invalid local delete target kind: {}".format(self.__allow_recursive))
            raise ValueError("Invalid local delete target kind")

        file_path = os.path.join(self.__local_path, file_name)
        real_base = os.path.realpath(self.__local_path)
        real_target = os.path.realpath(file_path)
        try:
            common_path = os.path.commonpath([real_base, real_target])
        except ValueError:
            common_path = ""
        if (
            os.path.normcase(common_path) != os.path.normcase(real_base) or
            os.path.normcase(real_target) == os.path.normcase(real_base)
        ):
            self.logger.error("Path traversal blocked: {} escapes {}".format(real_target, real_base))
            raise ValueError("Path traversal blocked")

        self.logger.debug("Deleting local file {}".format(file_name))
        if self.__delete_primary:
            if not os.path.exists(file_path):
                self.logger.error("Failed to delete non-existing file: {}".format(file_path))
                raise FileNotFoundError(file_path)
            # The selected target kind is authoritative. A live type change
            # must fail instead of selecting a different delete primitive.
            if os.path.islink(file_path):
                self.logger.error("Delete Local target changed to a symbolic link: {}".format(file_path))
                raise OSError("Delete Local target is a symbolic link: {}".format(file_path))
            if self.__allow_recursive:
                if not os.path.isdir(file_path):
                    self.logger.error("Delete Local target changed from directory: {}".format(file_path))
                    raise OSError("Delete Local target is not a directory: {}".format(file_path))
            elif not os.path.isfile(file_path):
                self.logger.error("Delete Local target changed from regular file: {}".format(file_path))
                raise OSError("Delete Local target is not a regular file: {}".format(file_path))
            try:
                if self.__allow_recursive:
                    shutil.rmtree(file_path)
                else:
                    os.unlink(file_path)
            except OSError:
                self.logger.exception("Failed to delete local file {}".format(file_path))
                raise

        for artifact_path in self.__artifact_paths:
            try:
                artifact_info = os.lstat(artifact_path)
            except FileNotFoundError:
                # The primary direct/temp target can be part of the plan.
                continue
            if stat.S_ISLNK(artifact_info.st_mode) or not stat.S_ISREG(artifact_info.st_mode):
                raise OSError("Unsafe Delete Local staging artifact: {}".format(artifact_path))
            try:
                artifact_base = os.path.realpath(self.__artifact_root)
                artifact_real = os.path.realpath(artifact_path)
                if os.path.normcase(os.path.commonpath([artifact_base, artifact_real])) != os.path.normcase(artifact_base):
                    raise OSError("Delete Local staging artifact escapes configured root")
                os.unlink(artifact_path)
            except OSError:
                self.logger.exception("Failed to delete local staging artifact %s", artifact_path)
                raise


class DeleteRemoteProcess(AppOneShotProcess):
    def __init__(self,
                 remote_address: str,
                 remote_username: str,
                 remote_password: Optional[str],
                 remote_port: int,
                 remote_path: str,
                 file_name: str):
        super().__init__(name=self.__class__.__name__)
        self.__remote_path = remote_path
        self.__file_name: object = file_name
        self.__ssh = Sshcp(host=remote_address,
                           port=remote_port,
                           user=remote_username,
                           password=remote_password)

    def run_once(self):
        self.__ssh.set_base_logger(self.logger)
        file_name = self.__file_name
        if not isinstance(file_name, str) or "\x00" in file_name:
            self.logger.error("Invalid remote delete filename: {}".format(self.__file_name))
            return

        normalized_name = posixpath.normpath(file_name.replace("\\", "/"))
        if (
            normalized_name in {"", ".", ".."} or
            normalized_name.startswith("../") or
            posixpath.isabs(normalized_name)
        ):
            self.logger.error("Path traversal blocked in remote delete: {}".format(self.__file_name))
            return

        file_path = posixpath.join(self.__remote_path, file_name)
        self.logger.debug("Deleting remote file: {}".format(file_name))
        out = self.__ssh.shell(
            "rm -rf {}".format(escape_remote_path_for_shell(file_path, allow_tilde_expansion=True))
        )
        self.logger.debug("Remote delete output: {}".format(out.decode()))
        self.logger.debug("Successfully deleted remote file: {}".format(self.__file_name))
