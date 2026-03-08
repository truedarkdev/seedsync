import unittest
from unittest.mock import MagicMock

from controller import Controller
from model import ModelFile
from web.handler.stream_model import ModelStreamHandler, WebResponseModelListener
from web.serialize import SerializeModel


class TestWebResponseModelListener(unittest.TestCase):
    def test_file_added_queues_added_event(self):
        listener = WebResponseModelListener()
        file = ModelFile("test.txt", False)

        listener.file_added(file)
        event = listener.get_next_event()

        self.assertEqual(SerializeModel.UpdateEvent.Change.ADDED, event.change)
        self.assertIsNone(event.old_file)
        self.assertIs(file, event.new_file)

    def test_empty_queue_returns_none(self):
        listener = WebResponseModelListener()

        self.assertIsNone(listener.get_next_event())


class TestModelStreamHandler(unittest.TestCase):
    def setUp(self):
        self.controller = MagicMock(spec=Controller)
        self.handler = ModelStreamHandler(self.controller)

    def test_setup_registers_listener_and_stores_initial_files(self):
        files = [ModelFile("alpha.txt", False)]
        self.controller.get_model_files_and_add_listener.return_value = files

        self.handler.setup()

        self.controller.get_model_files_and_add_listener.assert_called_once_with(
            self.handler.model_listener
        )
        self.assertIs(files, self.handler.initial_model_files)

    def test_first_get_value_returns_full_initial_model_event(self):
        self.controller.get_model_files_and_add_listener.return_value = [
            ModelFile("alpha.txt", False),
            ModelFile("beta.txt", False),
        ]
        self.handler.setup()

        result = self.handler.get_value()

        self.assertIn("event: model-init", result)
        self.assertIn("alpha.txt", result)
        self.assertIn("beta.txt", result)
        self.assertFalse(self.handler.first_run)

    def test_subsequent_get_value_returns_update_event(self):
        self.controller.get_model_files_and_add_listener.return_value = []
        self.handler.setup()
        self.handler.get_value()
        self.handler.model_listener.file_removed(ModelFile("old.txt", False))

        result = self.handler.get_value()

        self.assertIn("event: model-removed", result)

    def test_cleanup_removes_listener(self):
        self.handler.cleanup()

        self.controller.remove_model_listener.assert_called_once_with(
            self.handler.model_listener
        )
