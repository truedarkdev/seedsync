# Copyright 2017, Inderpreet Singh, All rights reserved.

from enum import Enum
from typing import List, Optional
import copy

# my libs
from .file import ModelFile
from .model import Model


class ModelDiff:
    """
    Represents a single change in the model
    """
    class Change(Enum):
        ADDED = 0
        REMOVED = 1
        UPDATED = 2

    def __init__(self, change: Change, old_file: Optional[ModelFile], new_file: Optional[ModelFile]):
        self.__change = change
        self.__old_file = old_file
        self.__new_file = new_file

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        return str(self.__dict__)

    @property
    def change(self) -> Change:
        return self.__change

    @property
    def old_file(self) -> Optional[ModelFile]:
        return self.__old_file

    @property
    def new_file(self) -> Optional[ModelFile]:
        return self.__new_file


class ModelDiffUtil:
    @staticmethod
    def diff_models(model_before: Model, model_after: Model) -> List[ModelDiff]:
        """
        Compare two models and generate their diff
        :param model_before:
        :param model_after:
        :return:
        """
        diffs = []
        file_ids_before = model_before.get_file_ids()
        file_ids_after = model_after.get_file_ids()

        # 'after minus before' gives added files
        file_ids_added = file_ids_after.difference(file_ids_before)
        if file_ids_added:
            diffs += [
                ModelDiff(
                    ModelDiff.Change.ADDED,
                    None,
                    model_after.get_file(file_id)
                ) for file_id in file_ids_added
            ]

        # 'before minus after' gives removed files
        file_ids_removed = file_ids_before.difference(file_ids_after)
        if file_ids_removed:
            diffs += [
                ModelDiff(
                    ModelDiff.Change.REMOVED,
                    model_before.get_file(file_id),
                    None
                ) for file_id in file_ids_removed
            ]

        # 'before intersect after' gives potentially updated files
        file_ids_updated = file_ids_before.intersection(file_ids_after)
        for file_id in file_ids_updated:
            file_before = model_before.get_file(file_id)
            file_after = model_after.get_file(file_id)
            if file_before != file_after:
                diffs.append(ModelDiff(ModelDiff.Change.UPDATED, file_before, file_after))

        return diffs
