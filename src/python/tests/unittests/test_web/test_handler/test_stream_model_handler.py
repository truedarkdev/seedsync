import json
import unittest
from unittest.mock import MagicMock

from controller import Controller
from model import ModelFile
from tests.unittests.test_web.test_serialize.test_serialize import parse_stream
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

    def test_target_trace_metadata_is_attached_only_when_provider_matches(self):
        listener = WebResponseModelListener(
            lambda file: {"cycle": 7, "corr_id": "stop-resume:a:7", "file_id": file.file_id}
        )
        file = ModelFile("a", False)
        listener.file_updated(file, file)

        event = listener.get_next_event()

        self.assertEqual(7, event.trace_metadata["cycle"])
        self.assertIsNotNone(event.enqueue_timestamp_ms)
        self.assertIsNotNone(event.queue_size_before_enqueue)

    def test_trace_metadata_provider_failure_does_not_interrupt_delivery(self):
        def fail_provider(file):
            raise RuntimeError("diagnostic provider unavailable")

        listener = WebResponseModelListener(fail_provider)
        file = ModelFile("a", False)
        listener.file_updated(file, file)

        event = listener.get_next_event()

        self.assertIsNotNone(event)
        self.assertIsNone(event.trace_metadata)

    def test_emit_breadcrumb_keeps_original_cycle_when_provider_advances(self):
        controller = MagicMock(spec=Controller)
        current_metadata = {
            "cycle": 1,
            "corr_id": "stop-resume:target:1",
            "file_id": "target",
        }
        controller.get_stop_resume_trace_metadata.side_effect = lambda file: dict(current_metadata)
        emitted = []
        controller.record_stop_resume_trace_breadcrumb.side_effect = (
            lambda stage, file, details: emitted.append((stage, details))
        )
        handler = ModelStreamHandler(controller)
        handler.first_run = False
        file = ModelFile("target", False)
        handler.model_listener.file_updated(file, file)
        current_metadata.update({
            "cycle": 2,
            "corr_id": "stop-resume:target:2",
            "file_id": "target",
        })

        handler.get_value()

        emit_details = [details for stage, details in emitted if stage == "emit"][-1]
        self.assertEqual(1, emit_details["cycle"])
        self.assertEqual("stop-resume:target:1", emit_details["corr_id"])
        self.assertEqual("target", emit_details["file_id"])


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

    def test_updated_event_serializes_current_active_progress_overlay(self):
        self.controller._model_file_progress_presentation.return_value = {
            "download_progress": 53,
            "transferred_size": 53,
            "downloading_speed": 12,
            "eta": 3,
        }
        self.handler.first_run = False
        old_file = ModelFile("active.bin", False)
        new_file = ModelFile("active.bin", False)
        old_file.download_progress = 48
        old_file.transferred_size = 48
        new_file.download_progress = 48
        new_file.transferred_size = 48

        self.handler.model_listener.file_updated(old_file, new_file)

        result = json.loads(parse_stream(self.handler.get_value())["data"])

        self.assertEqual(48, result["old_file"]["download_progress"])
        self.assertEqual(48, result["old_file"]["transferred_size"])
        self.assertEqual(53, result["new_file"]["download_progress"])
        self.assertEqual(53, result["new_file"]["transferred_size"])
        self.controller._model_file_progress_presentation.assert_called_once_with(new_file)

    def test_updated_event_keeps_base_when_progress_overlay_is_unavailable(self):
        self.controller._model_file_progress_presentation.return_value = None
        self.handler.first_run = False
        new_file = ModelFile("stopped.bin", False)
        new_file.download_progress = 53
        new_file.transferred_size = 53

        self.handler.model_listener.file_updated(new_file, new_file)

        result = json.loads(parse_stream(self.handler.get_value())["data"])

        self.assertEqual(53, result["new_file"]["download_progress"])
        self.assertEqual(53, result["new_file"]["transferred_size"])

    def test_enabled_enqueue_then_disable_before_emit_strips_trace_metadata(self):
        self.controller.is_stop_resume_trace_enabled.return_value = True
        self.controller.get_stop_resume_trace_metadata.return_value = {
            "cycle": 4,
            "corr_id": "stop-resume:active:4",
            "file_id": "active",
        }
        self.handler.first_run = False
        file = ModelFile("active", False)
        self.handler.model_listener.file_updated(file, file)

        self.controller.is_stop_resume_trace_enabled.return_value = False
        result = self.handler.get_value()

        self.assertIn("event: model-updated", result)
        self.assertNotIn('"trace"', result)
        self.assertFalse(any(
            call.args[0] == "emit"
            for call in self.controller.record_stop_resume_trace_breadcrumb.call_args_list
        ))

    def test_cleanup_removes_listener(self):
        self.handler.cleanup()

        self.controller.remove_model_listener.assert_called_once_with(
            self.handler.model_listener
        )
