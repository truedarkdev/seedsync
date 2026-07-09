# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
import posixpath
import shutil
from typing import Optional

from common import AppOneShotProcess, escape_remote_path_for_shell
from ssh import Sshcp


class DeleteLocalProcess(AppOneShotProcess):
    def __init__(self, local_path: str, file_name: str):
        super().__init__(name=self.__class__.__name__)
        self.__local_path = local_path
        self.__file_name = file_name

    def run_once(self):
        if not isinstance(self.__file_name, str) or self.__file_name == "" or "\x00" in self.__file_name:
            self.logger.error("Invalid local delete filename: {}".format(self.__file_name))
            return

        file_path = os.path.join(self.__local_path, self.__file_name)
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
            return

        self.logger.debug("Deleting local file {}".format(self.__file_name))
        if not os.path.exists(file_path):
            self.logger.error("Failed to delete non-existing file: {}".format(file_path))
            raise FileNotFoundError(file_path)
        else:
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                else:
                    shutil.rmtree(file_path)
            except FileNotFoundError:
                # Another actor may remove the target between the existence
                # check and the actual delete. Treat only a vanished top-level
                # target as success; a missing descendant while a directory
                # remains is a partial-delete failure.
                if os.path.lexists(file_path):
                    self.logger.exception("Failed to delete local file {}".format(file_path))
                    raise
                self.logger.warning("File already gone: {}".format(file_path))
            except OSError:
                self.logger.exception("Failed to delete local file {}".format(file_path))
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
        self.__file_name = file_name
        self.__ssh = Sshcp(host=remote_address,
                           port=remote_port,
                           user=remote_username,
                           password=remote_password)

    def run_once(self):
        self.__ssh.set_base_logger(self.logger)
        if not isinstance(self.__file_name, str) or "\x00" in self.__file_name:
            self.logger.error("Invalid remote delete filename: {}".format(self.__file_name))
            return

        normalized_name = posixpath.normpath(self.__file_name.replace("\\", "/"))
        if (
            normalized_name in {"", ".", ".."} or
            normalized_name.startswith("../") or
            posixpath.isabs(normalized_name)
        ):
            self.logger.error("Path traversal blocked in remote delete: {}".format(self.__file_name))
            return

        file_path = posixpath.join(self.__remote_path, self.__file_name)
        self.logger.debug("Deleting remote file: {}".format(self.__file_name))
        out = self.__ssh.shell(
            "rm -rf {}".format(escape_remote_path_for_shell(file_path, allow_tilde_expansion=True))
        )
        self.logger.debug("Remote delete output: {}".format(out.decode()))
        self.logger.debug("Successfully deleted remote file: {}".format(self.__file_name))
