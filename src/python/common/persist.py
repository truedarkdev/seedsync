# Copyright 2017, Inderpreet Singh, All rights reserved.

import glob
import logging
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Type, TypeVar

from .error import AppError
from .localization import Localization

_logger = logging.getLogger(__name__)

_BACKUP_DIR_NAME = "backups"
_MAX_BACKUPS = 10


# Source: https://stackoverflow.com/a/39205612/8571324
T_Persist = TypeVar('T_Persist', bound='Persist')
T_Serializable = TypeVar('T_Serializable', bound='Serializable')


class Serializable(ABC):
    """
    Defines a class that is serializable to string.
    The string representation must be human readable (i.e. not pickle)
    """
    @classmethod
    @abstractmethod
    def from_str(cls: Type[T_Serializable], content: str) -> T_Serializable:
        pass

    @abstractmethod
    def to_str(self) -> str:
        pass


class PersistError(AppError):
    """
    Exception indicating persist loading/saving error
    """
    pass


class Persist(Serializable):
    """
    Defines state that should be persisted between runs
    Provides utility methods to persist/load content to/from file
    Concrete implementations need to implement the from_str() and
    to_str() functionality
    """
    @classmethod
    def from_file(cls: Type[T_Persist], file_path: str) -> T_Persist:
        if not os.path.isfile(file_path):
            raise AppError(Localization.Error.MISSING_FILE.format(file_path))
        cls.__chmod_best_effort(file_path)
        with open(file_path, "r") as f:
            return cls.from_str(f.read())

    def to_file(self, file_path: str):
        dir_name = os.path.dirname(file_path) or "."
        if os.path.isfile(file_path):
            try:
                self.__backup_file(file_path, dir_name)
            except OSError as e:
                _logger.error("Failed to back up %s in %s: %s", file_path, dir_name, e)

            if os.name == "posix":
                self.__chmod_best_effort(file_path)

        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_persist_")
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.to_str())
            os.replace(tmp_path, file_path)
            self.__chmod_best_effort(file_path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    @abstractmethod
    def from_str(cls: Type[T_Persist], content: str) -> T_Persist:
        pass

    @abstractmethod
    def to_str(self) -> str:
        pass

    @staticmethod
    def __chmod_best_effort(file_path: str, mode: int = 0o600) -> None:
        if os.name != "posix":
            return
        try:
            os.chmod(file_path, mode)
        except (PermissionError, OSError):
            # Some filesystems, such as Windows bind mounts in Docker, do not
            # support chmod. Preserve the best-effort hardening without
            # aborting persistence operations.
            return

    @staticmethod
    def __backup_file(file_path: str, dir_name: str) -> None:
        backup_dir = os.path.join(dir_name, _BACKUP_DIR_NAME)
        os.makedirs(backup_dir, exist_ok=True)
        Persist.__chmod_best_effort(backup_dir, 0o700)

        base_name = os.path.basename(file_path)
        name, ext = os.path.splitext(base_name)
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
        backup_path = os.path.join(backup_dir, "{}-{}{}".format(name, timestamp, ext))

        shutil.copy2(file_path, backup_path)
        Persist.__chmod_best_effort(backup_path)
        Persist.__prune_backups(backup_dir, name, ext)

    @staticmethod
    def __prune_backups(backup_dir: str, name: str, ext: str) -> None:
        pattern = os.path.join(
            backup_dir,
            "{}-????-??-??T??-??-??-??????{}".format(glob.escape(name), glob.escape(ext))
        )
        backups = sorted(glob.glob(pattern))
        for old_backup in backups[:-_MAX_BACKUPS]:
            try:
                os.remove(old_backup)
            except OSError:
                pass
