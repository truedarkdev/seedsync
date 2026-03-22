import unittest
from queue import Queue
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from controller import Controller
from controller.scan import MultiPathActiveScanner
from common import AppError
from lftp import LftpError, LftpJobStatus, LftpJobStatusParserError
from model import ModelDiff, ModelError, ModelFile


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
        self.controller._Controller__context.config.lftp.local_path = "/local"
        self.controller._Controller__persist = MagicMock()
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__persist.extracted_file_names = set()
        self.controller._Controller__persist.stopped_file_names = set()
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
        self.controller._Controller__validate_process = MagicMock()
        self.controller._Controller__mp_logger = MagicMock()
        self.controller._Controller__staging_path = "/local/incomplete"
        self.controller._Controller__path_pairs_by_id = {}
        self.controller._Controller__path_pair_staging_paths = {}
        self.controller._Controller__startup_recovery_done = False
        self.controller._Controller__memory_monitor = MagicMock()

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

    def test_update_model_sets_remote_scan_failure_status_from_partial_result(self):
        partial_file = ModelFile("partial", False)
        latest_remote_scan = SimpleNamespace(
            timestamp=object(),
            files=[partial_file],
            failed=True,
            error_message="Failed to scan remote path for pair 'TV': temporary remote failure"
        )
        self.controller._Controller__remote_scan_process.pop_latest_result.return_value = latest_remote_scan
        self.controller._Controller__context.status.controller = SimpleNamespace(
            latest_remote_scan_time=None,
            latest_remote_scan_failed=None,
            latest_remote_scan_error=None
        )

        self.controller._Controller__update_model()

        self.controller._Controller__model_builder.set_remote_files.assert_called_once_with([partial_file])
        self.assertIs(latest_remote_scan.timestamp, self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual(
            "Failed to scan remote path for pair 'TV': temporary remote failure",
            self.controller._Controller__context.status.controller.latest_remote_scan_error
        )

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

        callback.on_failure.assert_called_once_with("Lftp error: bad status", 500)
        callback.on_success.assert_not_called()

    def test_process_commands_reports_not_found_as_404(self):
        self.controller._Controller__model.get_file.side_effect = ModelError("missing")

        command = Controller.Command(Controller.Command.Action.QUEUE, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("File 'example' not found", 404)
        callback.on_success.assert_not_called()

    def test_process_commands_reports_wrong_state_as_409(self):
        file = ModelFile("example", False)
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.STOP, "example")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("File 'example' is not Queued or Downloading", 409)
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
        self.controller._Controller__validate_process.propagate_exception.assert_called_once_with()

    def test_propagate_exceptions_records_first_remote_scan_failure(self):
        self.controller._Controller__context.status.controller = SimpleNamespace(
            latest_remote_scan_time=None,
            latest_remote_scan_failed=None,
            latest_remote_scan_error=None
        )
        self.controller._Controller__remote_scan_process.propagate_exception.side_effect = AppError("remote failed")

        with self.assertRaises(AppError) as ctx:
            self.controller._Controller__propagate_exceptions()

        self.assertEqual("remote failed", str(ctx.exception))
        self.assertIsNotNone(self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual("remote failed", self.controller._Controller__context.status.controller.latest_remote_scan_error)
        self.controller.logger.warning.assert_called_once_with(
            "Fatal remote scan failure recorded: remote failed"
        )
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_not_called()
        self.controller._Controller__extract_process.propagate_exception.assert_not_called()
        self.controller._Controller__validate_process.propagate_exception.assert_not_called()

    def test_propagate_exceptions_records_first_remote_scan_runtime_failure(self):
        self.controller._Controller__context.status.controller = SimpleNamespace(
            latest_remote_scan_time=None,
            latest_remote_scan_failed=None,
            latest_remote_scan_error=None
        )
        self.controller._Controller__remote_scan_process.propagate_exception.side_effect = FileNotFoundError("missing scanfs")

        with self.assertRaises(FileNotFoundError) as ctx:
            self.controller._Controller__propagate_exceptions()

        self.assertEqual("missing scanfs", str(ctx.exception))
        self.assertIsNotNone(self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual("missing scanfs", self.controller._Controller__context.status.controller.latest_remote_scan_error)
        self.controller.logger.warning.assert_called_once_with(
            "Fatal remote scan failure recorded: missing scanfs"
        )
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_not_called()
        self.controller._Controller__extract_process.propagate_exception.assert_not_called()
        self.controller._Controller__validate_process.propagate_exception.assert_not_called()

    def test_propagate_exceptions_records_fatal_remote_failure_after_prior_recoverable_status(self):
        existing_time = object()
        self.controller._Controller__context.status.controller = SimpleNamespace(
            latest_remote_scan_time=existing_time,
            latest_remote_scan_failed=True,
            latest_remote_scan_error="fatal remote error"
        )
        self.controller._Controller__remote_scan_process.propagate_exception.side_effect = FileNotFoundError("fatal remote error")

        with self.assertRaises(FileNotFoundError) as ctx:
            self.controller._Controller__propagate_exceptions()

        self.assertEqual("fatal remote error", str(ctx.exception))
        self.assertIsNot(existing_time, self.controller._Controller__context.status.controller.latest_remote_scan_time)
        self.assertTrue(self.controller._Controller__context.status.controller.latest_remote_scan_failed)
        self.assertEqual("fatal remote error", self.controller._Controller__context.status.controller.latest_remote_scan_error)
        self.controller.logger.warning.assert_called_once_with(
            "Fatal remote scan failure recorded: fatal remote error"
        )
        self.controller._Controller__active_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__local_scan_process.propagate_exception.assert_called_once_with()
        self.controller._Controller__mp_logger.propagate_exception.assert_not_called()
        self.controller._Controller__extract_process.propagate_exception.assert_not_called()
        self.controller._Controller__validate_process.propagate_exception.assert_not_called()

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

    @patch("controller.controller.ModelDiffUtil.diff_models", return_value=[])
    def test_update_model_prunes_stale_downloaded_file_names(self, _):
        self.controller._Controller__persist.downloaded_file_names = {"keep-id", "stale-id"}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__model.get_file_ids.return_value = {"keep-id"}
        self.controller._Controller__model.get_file_names.return_value = {"keep"}

        self.controller._Controller__update_model()

        self.assertEqual({"keep-id"}, self.controller._Controller__persist.downloaded_file_names)
        self.controller._Controller__model_builder.set_downloaded_files.assert_called_once_with({"keep-id"})

    @patch("controller.controller.ModelDiffUtil.diff_models")
    def test_update_model_keeps_downloaded_file_ids_when_new_download_completes(self, diff_models):
        added_file = ModelFile("keep", False)
        added_file.path_pair_id = "movies"
        added_file.state = ModelFile.State.DOWNLOADED
        added_file.file_id = "[\"movies\",\"keep\"]"

        stale_a = "[\"movies\",\"a\"]"
        stale_b = "[\"movies\",\"b\"]"
        self.controller._Controller__persist.downloaded_file_names = {stale_a, stale_b}
        self.controller._Controller__model_builder.has_changes.return_value = True
        self.controller._Controller__model_builder.build_model.return_value = MagicMock()
        self.controller._Controller__model.get_file_ids.return_value = {
            stale_a,
            stale_b,
            added_file.file_id
        }
        self.controller._Controller__model.get_file_names.return_value = {"a", "b", "keep"}
        diff_models.return_value = [MagicMock(change=ModelDiff.Change.ADDED, new_file=added_file)]

        self.controller._Controller__update_model()

        self.assertEqual(
            {stale_a, stale_b, added_file.file_id},
            self.controller._Controller__persist.downloaded_file_names
        )
        self.assertEqual(
            {stale_a, stale_b, added_file.file_id},
            self.controller._Controller__model_builder.set_downloaded_files.call_args_list[-1][0][0]
        )

    def test_process_commands_queue_uses_path_pair_paths(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "dup",
            False,
            remote_base_dir_path="/remote/movies",
            local_base_dir_path="/local/movies/incomplete"
        )

    def test_process_commands_stop_uses_path_pair_identity(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.state = ModelFile.State.DOWNLOADING
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        command = Controller.Command(Controller.Command.Action.STOP, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__lftp.kill.assert_called_once_with(
            "dup",
            path_pair_id="movies",
            remote_path="/remote/movies/dup",
            local_path="/local/movies/incomplete"
        )
        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_queue_clears_stopped_file_identity(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.remote_size = 10
        self.controller._Controller__persist.stopped_file_names = {file.file_id}
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }

        command = Controller.Command(Controller.Command.Action.QUEUE, file.file_id)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.assertEqual(set(), self.controller._Controller__persist.stopped_file_names)

    def test_process_commands_delete_local_tracks_stopped_file_identity(self):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        self.assertEqual({file.file_id}, self.controller._Controller__persist.stopped_file_names)

    @patch("controller.controller.os.path.exists")
    def test_process_commands_delete_local_prefers_staging_path_until_move(self, exists):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        exists.side_effect = lambda path: path == "/local/movies/incomplete/dup"

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_local_process.assert_called_once_with(
            local_path="/local/movies/incomplete",
            file_name="dup"
        )

    @patch("controller.controller.os.path.exists")
    def test_process_commands_delete_local_keeps_final_path_once_moved(self, exists):
        file = ModelFile("dup", False)
        file.path_pair_id = "movies"
        file.local_size = 10
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }
        exists.side_effect = lambda path: path == "/local/movies/dup"

        with patch("controller.controller.DeleteLocalProcess") as delete_local_process:
            process = MagicMock()
            delete_local_process.return_value = process
            command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, file.file_id)
            self.controller.queue_command(command)

            self.controller._Controller__process_commands()

        delete_local_process.assert_called_once_with(
            local_path="/local/movies",
            file_name="dup"
        )

    def test_process_commands_validate_queues_validation(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 10
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        self.controller._Controller__validate_process.validate.assert_called_once_with(file)

    def test_process_commands_validate_rejects_missing_remote_file(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = None
        file.state = ModelFile.State.DOWNLOADED
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with("File 'dup' does not exist remotely", 404)
        self.controller._Controller__validate_process.validate.assert_not_called()

    def test_process_commands_validate_rejects_stopped_partial_file(self):
        file = ModelFile("dup", False)
        file.local_size = 10
        file.remote_size = 20
        file.state = ModelFile.State.DEFAULT
        self.controller._Controller__model.get_file.return_value = file

        command = Controller.Command(Controller.Command.Action.VALIDATE, "dup")
        callback = MagicMock()
        command.add_callback(callback)
        self.controller.queue_command(command)

        self.controller._Controller__process_commands()

        callback.on_failure.assert_called_once_with(
            "File 'dup' in state State.DEFAULT cannot be validated",
            409
        )
        self.controller._Controller__validate_process.validate.assert_not_called()

    def test_log_memory_usage_reports_current_controller_collection_sizes(self):
        self.controller._Controller__model.get_file_ids.return_value = {"a", "b", "c"}
        self.controller._Controller__persist.downloaded_file_names = {"d1", "d2"}
        self.controller._Controller__persist.extracted_file_names = {"e1"}
        self.controller._Controller__persist.stopped_file_names = {"s1", "s2", "s3"}
        self.controller._Controller__active_downloading_file_names = [("down", None, None)]
        self.controller._Controller__active_extracting_file_names = [("extract", None, None), ("extract2", None, None)]
        self.controller._Controller__active_command_processes = [MagicMock(), MagicMock()]

        self.controller._Controller__log_memory_usage()

        self.controller._Controller__memory_monitor.log_if_due.assert_called_once_with(
            model_file_count=3,
            downloaded_file_count=2,
            extracted_file_count=1,
            stopped_file_count=3,
            active_download_count=1,
            active_extract_count=2,
            active_command_count=2
        )

    def test_build_staging_path_prefers_explicit_single_path_override(self):
        self.assertEqual(
            "/custom/staging",
            self.controller._Controller__build_staging_path("/local", "/custom/staging")
        )

    @patch("controller.controller.shutil.move")
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_uses_single_path_roots(self, _, move):
        self.controller._Controller__move_from_staging("movie.mkv")

        move.assert_called_once_with("/local/incomplete/movie.mkv", "/local/movie.mkv")

    @patch("controller.controller.shutil.move")
    @patch("controller.controller.os.path.exists", return_value=True)
    def test_move_from_staging_uses_path_pair_roots(self, _, move):
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        self.controller._Controller__move_from_staging("movie.mkv", "movies")

        move.assert_called_once_with(
            "/local/movies/incomplete/movie.mkv",
            "/local/movies/movie.mkv"
        )

    def test_recover_interrupted_downloads_requeues_single_path_temp_file(self):
        self.controller._Controller__persist.downloaded_file_names = set()

        remote_file = SimpleNamespace(name="movie.mkv", path_pair_id=None)
        with patch("controller.controller.os.listdir", return_value=["movie.mkv.lftp"]), \
                patch("controller.controller.os.path.isdir", return_value=False):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.assertTrue(self.controller._Controller__startup_recovery_done)
        self.controller._Controller__lftp.queue.assert_called_once_with(
            "movie.mkv",
            False,
            remote_base_dir_path=None,
            local_base_dir_path="/local/incomplete"
        )

    def test_recover_interrupted_downloads_skips_previously_downloaded_path_pair_file(self):
        file_id = ModelFile.build_file_id("dup.mkv", "movies")
        self.controller._Controller__persist.downloaded_file_names = {file_id}
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        remote_file = SimpleNamespace(name="dup.mkv", path_pair_id="movies")
        with patch("controller.controller.os.listdir", return_value=["dup.mkv.lftp"]), \
                patch("controller.controller.os.path.isdir", return_value=False):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_not_called()

    def test_recover_interrupted_downloads_skips_stopped_file_for_matching_path_pair_only(self):
        stopped_file_id = ModelFile.build_file_id("dup.mkv", "movies")
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__persist.stopped_file_names = {stopped_file_id}
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies"),
            "tv": SimpleNamespace(remote_path="/remote/tv", local_path="/local/tv")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete",
            "tv": "/local/tv/incomplete"
        }

        remote_files = [
            SimpleNamespace(name="dup.mkv", path_pair_id="movies"),
            SimpleNamespace(name="dup.mkv", path_pair_id="tv")
        ]

        def listdir_side_effect(path):
            if path == "/local/movies/incomplete":
                return ["dup.mkv.lftp"]
            if path == "/local/tv/incomplete":
                return ["dup.mkv.lftp"]
            raise AssertionError(path)

        with patch("controller.controller.os.listdir", side_effect=listdir_side_effect), \
                patch("controller.controller.os.path.isdir", return_value=False):
            self.controller._Controller__recover_interrupted_downloads(remote_files)

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "dup.mkv",
            False,
            remote_base_dir_path="/remote/tv",
            local_base_dir_path="/local/tv/incomplete"
        )

    def test_recover_interrupted_downloads_queues_path_pair_directory(self):
        self.controller._Controller__persist.downloaded_file_names = set()
        self.controller._Controller__path_pairs_by_id = {
            "movies": SimpleNamespace(remote_path="/remote/movies", local_path="/local/movies")
        }
        self.controller._Controller__path_pair_staging_paths = {
            "movies": "/local/movies/incomplete"
        }

        remote_file = SimpleNamespace(name="season1", path_pair_id="movies")

        def listdir_side_effect(path):
            if path == "/local/movies/incomplete":
                return ["season1"]
            if path == os.path.join("/local/movies/incomplete", "season1"):
                return ["episode1.mkv.lftp"]
            raise AssertionError(path)

        with patch("controller.controller.os.listdir", side_effect=listdir_side_effect), \
                patch("controller.controller.os.path.isdir", side_effect=lambda path: path.endswith("season1")):
            self.controller._Controller__recover_interrupted_downloads([remote_file])

        self.controller._Controller__lftp.queue.assert_called_once_with(
            "season1",
            True,
            remote_base_dir_path="/remote/movies",
            local_base_dir_path="/local/movies/incomplete"
        )
