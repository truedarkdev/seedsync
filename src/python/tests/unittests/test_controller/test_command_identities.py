import threading
import unittest
from unittest.mock import patch

from controller import Controller
from model import Model, ModelFile


class TestControllerCommandIdentities(unittest.TestCase):
    @staticmethod
    def _controller_for_model(model: Model) -> Controller:
        controller = Controller.__new__(Controller)
        controller._Controller__model = model
        controller._Controller__model_lock = threading.RLock()
        return controller

    def test_returns_immutable_rendered_metadata_without_copying_children(self):
        root = ModelFile("release", True)
        root.path_pair_id = "movies"
        for index in range(2000):
            child = ModelFile("episode-{}.mkv".format(index), False)
            child.path_pair_id = "movies"
            root.add_child(child)

        model = Model()
        model.add_file(root)
        controller = self._controller_for_model(model)

        with patch("controller.controller.copy.deepcopy", side_effect=AssertionError):
            controller._refresh_model_file_command_identities_locked()
            identities = controller.get_model_file_command_identities()

        self.assertEqual(2001, len(identities))
        self.assertIn((root.file_id, root.name, root.path_pair_id), identities)
        self.assertIn((
            ModelFile.build_file_id("release/episode-0.mkv", "movies"),
            "episode-0.mkv", "movies",
        ), identities)
        self.assertIsInstance(identities, tuple)
        self.assertIsInstance(identities[0], tuple)

    def test_accessor_returns_published_snapshot_without_acquiring_model_lock(self):
        root = ModelFile("release", True)
        model = Model()
        model.add_file(root)
        controller = self._controller_for_model(model)
        controller._refresh_model_file_command_identities_locked()

        class FailingLock:
            def __enter__(self):
                raise AssertionError("published identity reads must not acquire model lock")

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        controller._Controller__model_lock = FailingLock()

        self.assertEqual(
            ((root.file_id, root.name, root.path_pair_id),),
            controller.get_model_file_command_identities(),
        )

    def test_refresh_replaces_snapshot_after_model_publication(self):
        first = ModelFile("first", False)
        model = Model()
        model.add_file(first)
        controller = self._controller_for_model(model)
        controller._refresh_model_file_command_identities_locked()

        second = ModelFile("second", False)
        model.add_file(second)
        controller._refresh_model_file_command_identities_locked()

        self.assertEqual(
            tuple((file.file_id, file.name, file.path_pair_id) for file in model.iter_files_by_id()),
            controller.get_model_file_command_identities(),
        )
