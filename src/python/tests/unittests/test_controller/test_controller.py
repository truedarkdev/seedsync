import unittest
from queue import Queue
from unittest.mock import MagicMock
from types import SimpleNamespace

from controller import Controller
from controller.scan import MultiPathActiveScanner
from lftp import LftpError, LftpJobStatus, LftpJobStatusParserError
from model import ModelFile


class TestController(unittest.TestCase):
    def setUp(self):
        self.controller = Controller.__new__(Controller)
        self.controller.logger = MagicMock()
        self.controller._Controller__command_queue = Queue()
        self.controller._Controller__active_command_processes = []
        self.controller._Controller__active_downloading_file_names = []
        self.controller._Controller__active_extracting_file_names = []
        self.controller._Controller__context = MagicMock()
        self.controller._Controller__context.status.controller = MagicMock()
        self.controller._Controller__persist = MagicMock()
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__persist.extracted_file_names = set()
        self.controller._Controller__model = MagicMock()
        self.controller._Controller__model_builder = MagicMock()
        self.controller._Controller__model_builder.has_changes.return_value = False
        self.controller._Controller__model_lock = MagicMock()
        self.controller._Controller__lftp = MagicMock()
        self.controller._Controller__active_scan_process = MagicMock()
        self.controller._Controller__local_scan_process = MagicMock()
        self.controller._Controller__remote_scan_process = MagicMock()
        self.controller._Controller__active_scanner = MagicMock()
        self.controller._Controller__extract_process = MagicMock()
        self.controller._Controller__mp_logger = MagicMock()
        self.controller._Controller__path_pairs_by_id = {}

        self.controller._Controller__active_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__local_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = None
        self.controller._Controller__extract_process.pop_latest_statuses.return_value = None
        self.controller._Controller__extract_process.pop_completed.return_value = []

    def test_update_model_ignores_lftp_status_parser_errors(self):
        self.controller._Controller__lftp.status.side_effect = LftpJobStatusParserError("bad status")

        self.controller._Controller__update_model()

        self.controller.logger.warning.assert_called_once_with("Caught lftp error: bad status")
        self.controller._Controller__model_builder.set_lftp_statuses.assert_not_called()
        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([])

    def test_get_model_files_uses_file_ids_when_available(self):
        file_movies = ModelFile("dup", False)
        file_movies.path_pair_id = "movies"
        file_tv = ModelFile("dup", False)
        file_tv.path_pair_id = "tv"
        self.controller._Controller__model.get_file_ids = MagicMock(return_value={
            file_movies.file_id,
            file_tv.file_id
        })
        self.controller._Controller__model.get_file.side_effect = lambda identifier: {
            file_movies.file_id: file_movies,
            file_tv.file_id: file_tv
        }[identifier]

        model_files = self.controller.get_model_files()

        self.assertEqual({file_movies.file_id, file_tv.file_id}, {file.file_id for file in model_files})

    def test_process_commands_stop_reports_lftp_status_parser_errors(self):
        file = ModelFile("example", False)
        file.state = ModelFile.State.DOWNLOADING
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__lftp.kill.side_effect = LftpJobStatusParserError("bad status")

        command = Controller.Command(Controller.Command.Action.STOP, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("Lftp error: ")
        callback.on_success.assert_not_called()

    def test_propagate_exceptions_ignores_pending_lftp_errors(self):
        self.controller._Controller__lftp.raise_pending_error.side_effect = LftpError("pending failure")

        self.controller._Controller__propagate_exceptions()

        self.controller.logger.warning.assert_called_once_with("Caught lftp error: pending failure")
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__remote_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_called_once_with()
        self.controller._Controller__extract_process.propagate_exception.assert_called_once_with()

    def test_update_model_sets_multi_path_active_scan_entries(self):
        self.controller._Controller__active_scanner = MultiPathActiveScanner({})
        self.controller._Controller__active_scanner.set_active_files = MagicMock()
        status = MagicMock()
        status.state = LftpJobStatus.State.RUNNING
        status.name = "dup"
        status.path_pair_id = "movies"
        status.path_pair_name = "Movies"
        self.controller._Controller__lftp.status.return_value = [status]

        self.controller._Controller__update_model()

        self.controller._Controller__active_scanner.set_active_files.assert_called_once_with([
            ("dup", "movies", "Movies")
        ])

    def test_process_commands_queue_uses_path_pair_paths(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "dup",
            False,
            remote_base_dir_path="/remote/movies",
            local_base_dir_path="/local/movies"
        )

    def test_process_commands_stop_uses_path_pair_identity(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.state = ModelFile.State.DOWNLOADING
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.kill.assert_called_once_with(
            "dup",
            path_pair_id="movies",
            remote_path="/remote/movies/dup",
            local_path="/local/movies"
        )
