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
                 delete_primary: bool = True, allow_recursive: bool = False,
                 additional_targets: Sequence[tuple[str, str, bool]] = (),
                 result_queue=None):
        super().__init__(name=self.__class__.__name__)
        self.__local_path = local_path
        self.__file_name: object = file_name
        self.__artifact_paths = tuple(artifact_paths)
        self.__artifact_root = artifact_root if artifact_root is not None else local_path
        self.__delete_primary = delete_primary
        self.__allow_recursive = allow_recursive
        self.__additional_targets = tuple(additional_targets)
        self.result_queue = result_queue

    def __report_result(self, planned: int, removed: int, partial: Optional[bool]) -> None:
        if self.result_queue is None:
            return
        try:
            self.result_queue.put({
                "planned": planned,
                "removed": removed,
                "known": True,
                "partial": partial,
            })
        except Exception:
            # Result reporting is diagnostic-only and must not change deletion.
            pass

    def run_once(self):
        file_name = self.__file_name
        if not isinstance(file_name, str) or file_name == "" or "\x00" in file_name:
            self.logger.error("Invalid local delete filename: {}".format(self.__file_name))
            raise ValueError("Invalid local delete filename")
        if type(self.__allow_recursive) is not bool:
            self.logger.error("Invalid local delete target kind: {}".format(self.__allow_recursive))
            raise ValueError("Invalid local delete target kind")

        # Preserve the established one-location path exactly. The stricter
        # all-target preflight below is needed only when one request spans
        # both configured local roots.
        if not self.__additional_targets:
            file_path = os.path.join(self.__local_path, file_name)
            real_base = os.path.realpath(self.__local_path)
            real_target = os.path.realpath(file_path)
            try:
                common_path = os.path.commonpath([real_base, real_target])
            except ValueError:
                common_path = ""
            if os.path.normcase(common_path) != os.path.normcase(real_base) or \
                    os.path.normcase(real_target) == os.path.normcase(real_base):
                self.logger.error("Path traversal blocked: {} escapes {}".format(real_target, real_base))
                raise ValueError("Path traversal blocked")
            self.logger.debug("Deleting local file {}".format(file_name))
            if self.__delete_primary:
                if not os.path.exists(file_path):
                    self.logger.error("Failed to delete non-existing file: {}".format(file_path))
                    raise FileNotFoundError(file_path)
                if os.path.islink(file_path):
                    self.logger.error("Delete Local target changed to a symbolic link: {}".format(file_path))
                    raise OSError("Delete Local target is a symbolic link: {}".format(file_path))
                if self.__allow_recursive:
                    if not os.path.isdir(file_path):
                        self.logger.error("Delete Local target changed from directory: {}".format(file_path))
                        raise OSError("Delete Local target is not a directory: {}".format(file_path))
                    try:
                        shutil.rmtree(file_path)
                    except OSError:
                        self.logger.exception("Failed to delete local file {}".format(file_path))
                        raise
                else:
                    if not os.path.isfile(file_path):
                        self.logger.error("Delete Local target changed from regular file: {}".format(file_path))
                        raise OSError("Delete Local target is not a regular file: {}".format(file_path))
                    try:
                        os.unlink(file_path)
                    except OSError:
                        self.logger.exception("Failed to delete local file {}".format(file_path))
                        raise
            for artifact_path in self.__artifact_paths:
                try:
                    artifact_info = os.lstat(artifact_path)
                except FileNotFoundError:
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
            return

        targets: list[tuple[str, str, bool]] = []
        if self.__delete_primary:
            targets.append((self.__local_path, file_name, self.__allow_recursive))
        targets.extend(self.__additional_targets)
        planned = len(targets)
        removed = 0
        removed_artifacts = 0
        mutation_attempted = False

        def validate_target(root: str, name: str, recursive: bool) -> str:
            if not isinstance(root, str) or not root or not isinstance(name, str) or \
                    name == "" or "\x00" in name or type(recursive) is not bool:
                raise ValueError("Invalid local delete target")
            path = os.path.join(root, name)
            real_base = os.path.realpath(root)
            real_target = os.path.realpath(path)
            try:
                common_path = os.path.commonpath([real_base, real_target])
            except ValueError:
                common_path = ""
            if os.path.normcase(common_path) != os.path.normcase(real_base) or \
                    os.path.normcase(real_target) == os.path.normcase(real_base):
                raise ValueError("Path traversal blocked")
            root_info = os.lstat(root)
            reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(root_info.st_mode) or (reparse_point and
                    getattr(root_info, "st_file_attributes", 0) & reparse_point) or \
                    not stat.S_ISDIR(root_info.st_mode):
                raise OSError("Delete Local root is not a safe directory")
            # realpath containment alone is insufficient: a parent component
            # can be swapped to a symlink that still points inside this root.
            parent = os.path.dirname(name)
            current = root
            if parent:
                for component in parent.split(os.sep):
                    if not component or component == ".":
                        continue
                    if component == "..":
                        raise ValueError("Path traversal blocked")
                    current = os.path.join(current, component)
                    parent_info = os.lstat(current)
                    if stat.S_ISLNK(parent_info.st_mode) or (reparse_point and
                            getattr(parent_info, "st_file_attributes", 0) & reparse_point) or \
                            not stat.S_ISDIR(parent_info.st_mode):
                        raise OSError("Delete Local parent is not a safe directory")
            target_info = os.lstat(path)
            if stat.S_ISLNK(target_info.st_mode) or (reparse_point and
                    getattr(target_info, "st_file_attributes", 0) & reparse_point) or \
                    recursive != stat.S_ISDIR(target_info.st_mode):
                raise OSError("Delete Local target kind changed")
            if not recursive and not stat.S_ISREG(target_info.st_mode):
                raise OSError("Delete Local target is not a regular file")
            return path

        try:
            # Validate every selected target and artifact before the first unlink.
            validated_targets = [validate_target(*target) for target in targets]
            validated_artifacts = []
            artifact_base = os.path.realpath(self.__artifact_root)
            for artifact_path in self.__artifact_paths:
                try:
                    artifact_info = os.lstat(artifact_path)
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(artifact_info.st_mode) or not stat.S_ISREG(artifact_info.st_mode):
                    raise OSError("Unsafe Delete Local staging artifact")
                artifact_real = os.path.realpath(artifact_path)
                try:
                    common_path = os.path.commonpath([artifact_base, artifact_real])
                except ValueError:
                    common_path = ""
                if os.path.normcase(common_path) != os.path.normcase(artifact_base):
                    raise OSError("Delete Local staging artifact escapes configured root")
                validated_artifacts.append(artifact_path)

            for (_root, _name, recursive) in targets:
                # Recheck the next exact location immediately before mutation;
                # the first deletion must not make a changed second target safe.
                path = validate_target(_root, _name, recursive)
                mutation_attempted = True
                if recursive:
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
                removed += 1
            for artifact_path in validated_artifacts:
                if artifact_path not in validated_targets:
                    # Physical target removal can take time. Reuse the
                    # artifact preflight checks immediately before unlink so
                    # a changed artifact is not followed after that interval.
                    try:
                        artifact_info = os.lstat(artifact_path)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISLNK(artifact_info.st_mode) or not stat.S_ISREG(artifact_info.st_mode):
                        raise OSError("Unsafe Delete Local staging artifact")
                    artifact_real = os.path.realpath(artifact_path)
                    artifact_base = os.path.realpath(self.__artifact_root)
                    try:
                        common_path = os.path.commonpath([artifact_base, artifact_real])
                    except ValueError:
                        common_path = ""
                    if os.path.normcase(common_path) != os.path.normcase(artifact_base):
                        raise OSError("Delete Local staging artifact escapes configured root")
                    mutation_attempted = True
                    os.unlink(artifact_path)
                    removed_artifacts += 1
        except BaseException:
            partial = True if removed or removed_artifacts else (None if mutation_attempted else False)
            self.__report_result(planned, removed, partial)
            raise
        else:
            self.__report_result(planned, removed, False)


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
