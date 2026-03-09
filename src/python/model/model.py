# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
from abc import ABC, abstractmethod
from typing import Dict, Set

# my libs
from common import AppError
from .file import ModelFile


class ModelError(AppError):
    """
    Exception indicating a model error
    """
    pass


class IModelListener(ABC):
    """
    Interface to listen to model events
    """
    @abstractmethod
    def file_added(self, file: ModelFile):
        """
        Event indicating a file was added to the model
        :param file:
        :return:
        """
        pass

    @abstractmethod
    def file_removed(self, file: ModelFile):
        """
        Event indicating that the given file was removed from the model
        :param file:
        :return:
        """
        pass

    @abstractmethod
    def file_updated(self, old_file: ModelFile, new_file: ModelFile):
        """
        Event indicating that the given file was updated
        :param old_file:
        :param new_file:
        :return:
        """
        pass


class Model:
    """
    Represents the entire state of lftp
    """
    def __init__(self):
        self.logger = logging.getLogger("Model")
        self.__files_by_id: Dict[str, ModelFile] = {}
        self.__file_ids_by_name: Dict[str, Set[str]] = {}
        self.__listeners = []

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("Model")

    def add_listener(self, listener: IModelListener):
        """
        Add a model listener
        :param listener:
        :return:
        """
        self.logger.debug("LftpModel: Adding a listener")
        if listener not in self.__listeners:
            self.__listeners.append(listener)

    def remove_listener(self, listener: IModelListener):
        """
        Add a model listener
        :param listener:
        :return:
        """
        self.logger.debug("LftpModel: Removing a listener")
        if listener not in self.__listeners:
            self.logger.error("LftpModel: listener does not exist!")
        else:
            self.__listeners.remove(listener)

    def add_file(self, file: ModelFile):
        """
        Add a file to the model
        :param file:
        :return:
        """
        self.logger.debug("LftpModel: Adding file '{}'".format(file.name))
        file_id = file.file_id
        if file_id in self.__files_by_id:
            raise ModelError("File already exists in the model")
        self.__files_by_id[file_id] = file
        if file.name not in self.__file_ids_by_name:
            self.__file_ids_by_name[file.name] = set()
        self.__file_ids_by_name[file.name].add(file_id)
        for listener in self.__listeners:
            listener.file_added(self.__files_by_id[file_id])

    def __resolve_file_id(self, identifier: str) -> str:
        if identifier in self.__files_by_id:
            return identifier
        matching_ids = self.__file_ids_by_name.get(identifier)
        if not matching_ids:
            raise ModelError("File does not exist in the model")
        if len(matching_ids) > 1:
            raise ModelError("File lookup is ambiguous in the model")
        return next(iter(matching_ids))

    def remove_file(self, filename: str):
        """
        Remove the file from the model
        :param filename:
        :return:
        """
        self.logger.debug("LftpModel: Removing file '{}'".format(filename))
        file_id = self.__resolve_file_id(filename)
        file = self.__files_by_id[file_id]
        del self.__files_by_id[file_id]
        self.__file_ids_by_name[file.name].remove(file_id)
        if not self.__file_ids_by_name[file.name]:
            del self.__file_ids_by_name[file.name]
        for listener in self.__listeners:
            listener.file_removed(file)

    def update_file(self, file: ModelFile):
        """
        Update an already existing file
        :param file:
        :return:
        """
        self.logger.debug("LftpModel: Updating file '{}'".format(file.name))
        file_id = file.file_id
        if file_id not in self.__files_by_id:
            raise ModelError("File does not exist in the model")
        old_file = self.__files_by_id[file_id]
        new_file = file
        self.__files_by_id[file_id] = new_file
        for listener in self.__listeners:
            listener.file_updated(old_file, new_file)

    def get_file(self, name: str) -> ModelFile:
        """
        Returns a copy of the file of the given name
        :param name:
        :return:
        """
        return self.__files_by_id[self.__resolve_file_id(name)]

    def get_file_names(self) -> Set[str]:
        return set(self.__file_ids_by_name.keys())

    def get_file_ids(self) -> Set[str]:
        return set(self.__files_by_id.keys())
