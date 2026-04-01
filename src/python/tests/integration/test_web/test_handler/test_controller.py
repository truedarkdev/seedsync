# Copyright 2017, Inderpreet Singh, All rights reserved.

from threading import Event, Thread
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from tests.integration.test_web.test_web_app import BaseTestWebApp
from controller import Controller
from web.handler.controller import ControllerHandler


class TestControllerHandler(BaseTestWebApp):
    def setUp(self):
        super().setUp()
        self.context.config.lftp.local_path = self.temp_dir
        self.web_app_builder.controller_handler = ControllerHandler(self.controller, local_path=self.temp_dir)
        self.web_app = self.web_app_builder.build()
        self.test_app = self.build_browser_test_app()
        self.controller.get_model_files = MagicMock(return_value=[])

    @staticmethod
    def __model_file(name: str, file_id: str, path_pair_id: str = None):
        file = MagicMock()
        file.name = name
        file.file_id = file_id
        file.path_pair_id = path_pair_id
        return file

    def test_queue(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        print(self.test_app.post("/server/command/queue/test1"))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("test1", command.filename)

        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        print(self.test_app.post("/server/command/queue/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("/value/with/slashes", command.filename)

        uri = quote(quote(" value with spaces", safe=""), safe="")
        print(self.test_app.post("/server/command/queue/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual(" value with spaces", command.filename)

        uri = quote(quote("value'with'singlequote", safe=""), safe="")
        print(self.test_app.post("/server/command/queue/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("value'with'singlequote", command.filename)

        uri = quote(quote("value\"with\"doublequote", safe=""), safe="")
        print(self.test_app.post("/server/command/queue/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("value\"with\"doublequote", command.filename)

    def test_stop(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        print(self.test_app.post("/server/command/stop/test1"))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.STOP, command.action)
        self.assertEqual("test1", command.filename)

        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        print(self.test_app.post("/server/command/stop/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.STOP, command.action)
        self.assertEqual("/value/with/slashes", command.filename)

        uri = quote(quote(" value with spaces", safe=""), safe="")
        print(self.test_app.post("/server/command/stop/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.STOP, command.action)
        self.assertEqual(" value with spaces", command.filename)

        uri = quote(quote("value'with'singlequote", safe=""), safe="")
        print(self.test_app.post("/server/command/stop/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.STOP, command.action)
        self.assertEqual("value'with'singlequote", command.filename)

        uri = quote(quote("value\"with\"doublequote", safe=""), safe="")
        print(self.test_app.post("/server/command/stop/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.STOP, command.action)
        self.assertEqual("value\"with\"doublequote", command.filename)

    def test_extract(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        print(self.test_app.post("/server/command/extract/test1"))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("test1", command.filename)

        uri = quote(quote("value/with/slashes", safe=""), safe="")
        print(self.test_app.post("/server/command/extract/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("value/with/slashes", command.filename)

        uri = quote(quote(" value with spaces", safe=""), safe="")
        print(self.test_app.post("/server/command/extract/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual(" value with spaces", command.filename)

        uri = quote(quote("value'with'singlequote", safe=""), safe="")
        print(self.test_app.post("/server/command/extract/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("value'with'singlequote", command.filename)

        uri = quote(quote("value\"with\"doublequote", safe=""), safe="")
        print(self.test_app.post("/server/command/extract/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("value\"with\"doublequote", command.filename)

    def test_delete_local(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        print(self.test_app.delete("/server/command/delete_local/test1"))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, command.action)
        self.assertEqual("test1", command.filename)

        uri = quote(quote("value/with/slashes", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_local/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, command.action)
        self.assertEqual("value/with/slashes", command.filename)

        uri = quote(quote(" value with spaces", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_local/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, command.action)
        self.assertEqual(" value with spaces", command.filename)

        uri = quote(quote("value'with'singlequote", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_local/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, command.action)
        self.assertEqual("value'with'singlequote", command.filename)

        uri = quote(quote("value\"with\"doublequote", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_local/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, command.action)
        self.assertEqual("value\"with\"doublequote", command.filename)

    def test_delete_local_returns_failure_response(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_failure("File 'test1' does not exist locally", 404)

        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        response = self.test_app.delete("/server/command/delete_local/test1", expect_errors=True)

        self.assertEqual(404, response.status_code)
        self.assertEqual("File 'test1' does not exist locally", response.text)

    def test_delete_remote(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        print(self.test_app.delete("/server/command/delete_remote/test1"))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("test1", command.filename)

        uri = quote(quote("value/with/slashes", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_remote/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("value/with/slashes", command.filename)

        uri = quote(quote(" value with spaces", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_remote/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual(" value with spaces", command.filename)

        uri = quote(quote("value'with'singlequote", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_remote/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("value'with'singlequote", command.filename)

        uri = quote(quote("value\"with\"doublequote", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_remote/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("value\"with\"doublequote", command.filename)

    def test_validate(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        print(self.test_app.post("/server/command/validate/test1"))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.VALIDATE, command.action)
        self.assertEqual("test1", command.filename)

    def test_extract_rejects_path_traversal(self):
        self.controller.queue_command = MagicMock()
        uri = quote(quote("../../etc/passwd", safe=""), safe="")

        response = self.test_app.post("/server/command/extract/"+uri, expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_delete_local_rejects_path_traversal_without_path_leak(self):
        self.controller.queue_command = MagicMock()
        uri = quote(quote("../../etc/passwd", safe=""), safe="")

        response = self.test_app.delete("/server/command/delete_local/"+uri, expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.assertNotIn("/etc", response.text)
        self.assertNotIn("passwd", response.text)
        self.assertNotIn(self.temp_dir, response.text)
        self.controller.queue_command.assert_not_called()

    def test_delete_remote_rejects_path_traversal(self):
        self.controller.queue_command = MagicMock()
        uri = quote(quote("../../etc/passwd", safe=""), safe="")

        response = self.test_app.delete("/server/command/delete_remote/"+uri, expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_delete_local_rejects_mismatched_file_id_authoritative_traversal_target(self):
        self.controller.queue_command = MagicMock()
        self.controller.get_model_files.return_value = [
            self.__model_file("safe.mkv", "[\"tv\",\"safe.mkv\"]", "tv"),
            self.__model_file("../../etc/passwd", "[\"movies\",\"../../etc/passwd\"]", "movies")
        ]

        safe_uri = quote(quote("safe.mkv", safe=""), safe="")
        traversal_file_id = quote("[\"movies\",\"../../etc/passwd\"]", safe="")
        response = self.test_app.delete(
            "/server/command/delete_local/{}?file_id={}&path_pair_id=tv".format(safe_uri, traversal_file_id),
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_delete_local_rejects_when_local_path_root_is_unavailable(self):
        self.web_app_builder.controller_handler = ControllerHandler(self.controller, local_path=None)
        self.web_app = self.web_app_builder.build()
        self.test_app = self.build_browser_test_app()
        self.controller.queue_command = MagicMock()

        response = self.test_app.delete("/server/command/delete_local/test1", expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_queue_remains_allowed_when_local_path_root_is_unavailable(self):
        self.web_app_builder.controller_handler = ControllerHandler(self.controller, local_path=None)
        self.web_app = self.web_app_builder.build()
        self.test_app = self.build_browser_test_app()

        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post("/server/command/queue/test1")

        self.assertEqual(200, response.status_code)
        self.assertEqual("Queued file 'test1'", response.text)
        self.assertEqual(1, self.controller.queue_command.call_count)

    def test_bulk_queue_preserves_order_and_deduplicates(self):
        seen_commands = []

        def side_effect(cmd: Controller.Command):
            seen_commands.append(cmd)
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        response = self.test_app.post_json("/server/command/bulk/queue", {
            "filenames": ["test1", "test2", "test1"]
        })

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(seen_commands))
        self.assertEqual(Controller.Command.Action.QUEUE, seen_commands[0].action)
        self.assertEqual(Controller.Command.Action.QUEUE, seen_commands[1].action)
        self.assertEqual("test1", seen_commands[0].filename)
        self.assertEqual("test2", seen_commands[1].filename)

    def test_queue_accepts_additive_file_identity(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect
        self.controller.get_model_files.return_value = [
            self.__model_file("dup", "[\"movies\",\"dup\"]", "movies"),
            self.__model_file("dup", "[\"tv\",\"dup\"]", "tv")
        ]

        response = self.test_app.post("/server/command/queue/dup?file_id=%5B%22tv%22%2C%22dup%22%5D")

        self.assertEqual(200, response.status_code)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("[\"tv\",\"dup\"]", command.filename)

    def test_queue_rejects_ambiguous_filename_without_identity(self):
        self.controller.get_model_files.return_value = [
            self.__model_file("dup", "[\"movies\",\"dup\"]", "movies"),
            self.__model_file("dup", "[\"tv\",\"dup\"]", "tv")
        ]

        response = self.test_app.post("/server/command/queue/dup", expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertIn("ambiguous", response.text)
        self.controller.queue_command.assert_not_called()

    def test_bulk_queue_accepts_file_identity_objects(self):
        seen_commands = []

        def side_effect(cmd: Controller.Command):
            seen_commands.append(cmd)
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect
        self.controller.get_model_files.return_value = [
            self.__model_file("dup", "[\"movies\",\"dup\"]", "movies"),
            self.__model_file("dup", "[\"tv\",\"dup\"]", "tv")
        ]

        response = self.test_app.post_json("/server/command/bulk/queue", {
            "files": [
                {"name": "dup", "file_id": "[\"movies\",\"dup\"]"},
                {"name": "dup", "file_id": "[\"tv\",\"dup\"]"},
                {"name": "dup", "file_id": "[\"movies\",\"dup\"]"}
            ]
        })

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(seen_commands))
        self.assertEqual("[\"movies\",\"dup\"]", seen_commands[0].filename)
        self.assertEqual("[\"tv\",\"dup\"]", seen_commands[1].filename)

    def test_bulk_queue_partial_failure_returns_summary(self):
        call_count = 0

        def side_effect(cmd: Controller.Command):
            nonlocal call_count
            call_count += 1
            if cmd.filename == "test2":
                cmd.callbacks[0].on_failure("bad file")
            else:
                cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        response = self.test_app.post_json(
            "/server/command/bulk/queue",
            {"filenames": ["test1", "test2"]},
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(2, call_count)
        self.assertIn("1 succeeded, 1 failed", response.text)
        self.assertIn("'test2': bad file", response.text)

    def test_bulk_delete_local_rejects_path_traversal_and_continues(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post_json(
            "/server/command/bulk/delete_local",
            {"filenames": ["../../etc/passwd", "test1"]},
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("1 succeeded, 1 failed", response.text)
        self.assertIn("'../../etc/passwd': Invalid file path", response.text)
        self.assertEqual(1, self.controller.queue_command.call_count)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, command.action)
        self.assertEqual("test1", command.filename)

    def test_bulk_delete_remote_rejects_mismatched_file_id_authoritative_traversal_target(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)
        self.controller.get_model_files.return_value = [
            self.__model_file("good.mkv", "good-file-id"),
            self.__model_file("../../etc/passwd", "traversal-file-id")
        ]

        response = self.test_app.post_json(
            "/server/command/bulk/delete_remote",
            {
                "files": [
                    {"name": "good.mkv", "file_id": "traversal-file-id"},
                    {"name": "good.mkv", "file_id": "good-file-id"}
                ]
            },
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("1 succeeded, 1 failed", response.text)
        self.assertIn("'good.mkv': Invalid file path", response.text)
        self.assertEqual(1, self.controller.queue_command.call_count)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("good-file-id", command.filename)

    def test_bulk_extract_rejects_path_traversal_and_continues(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post_json(
            "/server/command/bulk/extract",
            {"filenames": ["../../etc/passwd", "test1"]},
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("1 succeeded, 1 failed", response.text)
        self.assertIn("'../../etc/passwd': Invalid file path", response.text)
        self.assertEqual(1, self.controller.queue_command.call_count)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("test1", command.filename)

    def test_bulk_queue_does_not_guard_path_traversal_filenames(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post_json(
            "/server/command/bulk/queue",
            {"filenames": ["../../etc/passwd"]}
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("Bulk queue completed: 1 succeeded, 0 failed", response.text)
        self.assertEqual(1, self.controller.queue_command.call_count)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("../../etc/passwd", command.filename)

    def test_bulk_rejects_oversized_filenames_payload(self):
        with patch.object(ControllerHandler, "_MAX_BULK_ITEMS", 2):
            response = self.test_app.post_json(
                "/server/command/bulk/queue",
                {"filenames": ["test1", "test2", "test3"]},
                expect_errors=True
            )

        self.assertEqual(413, response.status_code)
        self.assertIn("maximum of 2 items", response.text)
        self.controller.queue_command.assert_not_called()

    def test_bulk_rejects_oversized_files_payload_and_allows_exact_boundary(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        with patch.object(ControllerHandler, "_MAX_BULK_ITEMS", 2):
            oversized_response = self.test_app.post_json(
                "/server/command/bulk/queue",
                {"files": [{"name": "test1"}, {"name": "test2"}, {"name": "test3"}]},
                expect_errors=True
            )

            boundary_response = self.test_app.post_json(
                "/server/command/bulk/queue",
                {"files": [{"name": "test1"}, {"name": "test2"}]}
            )

        self.assertEqual(413, oversized_response.status_code)
        self.assertIn("maximum of 2 items", oversized_response.text)
        self.assertEqual(200, boundary_response.status_code)
        self.assertEqual("Bulk queue completed: 2 succeeded, 0 failed", boundary_response.text)
        self.assertEqual(2, self.controller.queue_command.call_count)

    def test_bulk_rejects_concurrent_bulk_request_when_limit_is_reached(self):
        command_started = Event()
        release_first_request = Event()
        request_finished = Event()
        responses = {}

        def side_effect(cmd: Controller.Command):
            command_started.set()
            release_first_request.wait(timeout=1.0)
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        def issue_first_request():
            try:
                responses["first"] = self.test_app.post_json(
                    "/server/command/bulk/queue",
                    {"filenames": ["test1"]}
                )
            finally:
                request_finished.set()

        first_request_thread = Thread(target=issue_first_request)
        first_request_thread.start()
        self.assertTrue(command_started.wait(timeout=1.0))

        second_response = self.test_app.post_json(
            "/server/command/bulk/queue",
            {"filenames": ["test2"]},
            expect_errors=True
        )

        release_first_request.set()
        first_request_thread.join(timeout=1.0)
        self.assertTrue(request_finished.is_set())

        self.assertEqual(429, second_response.status_code)
        self.assertEqual("Bulk request already in progress", second_response.text)
        self.assertEqual(1, self.controller.queue_command.call_count)
        self.assertEqual(200, responses["first"].status_code)

    def test_bulk_queue_timeout_summary_is_preserved_after_limiter_release(self):
        call_count = 0

        def side_effect(cmd: Controller.Command):
            nonlocal call_count
            call_count += 1
            if cmd.filename == "test2":
                cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        with patch.object(ControllerHandler, "_ACTION_TIMEOUT", 0.01):
            first_response = self.test_app.post_json(
                "/server/command/bulk/queue",
                {"filenames": ["test1", "test2"]},
                expect_errors=True
            )
            second_response = self.test_app.post_json(
                "/server/command/bulk/queue",
                {"filenames": ["test3"]},
                expect_errors=True
            )

        self.assertEqual(400, first_response.status_code)
        self.assertIn("1 succeeded, 1 failed", first_response.text)
        self.assertIn("'test1': Operation timed out", first_response.text)
        self.assertEqual(400, second_response.status_code)
        self.assertIn("0 succeeded, 1 failed", second_response.text)
        self.assertIn("'test3': Operation timed out", second_response.text)
        self.assertEqual(3, call_count)

    def test_bulk_queue_times_out_when_callback_never_completes(self):
        self.controller.queue_command = MagicMock()

        with patch.object(ControllerHandler, "_ACTION_TIMEOUT", 0.01):
            response = self.test_app.post_json(
                "/server/command/bulk/queue",
                {"filenames": ["test1"]},
                expect_errors=True
            )

        self.assertEqual(400, response.status_code)
        self.assertIn("0 succeeded, 1 failed", response.text)
        self.assertIn("'test1': Operation timed out", response.text)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("test1", command.filename)

    def test_bulk_queue_continues_after_timeout_and_summarizes_failures(self):
        seen_commands = []

        def side_effect(cmd: Controller.Command):
            seen_commands.append(cmd.filename)
            if cmd.filename == "test2":
                cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        with patch.object(ControllerHandler, "_ACTION_TIMEOUT", 0.01):
            response = self.test_app.post_json(
                "/server/command/bulk/queue",
                {"filenames": ["test1", "test2"]},
                expect_errors=True
            )

        self.assertEqual(400, response.status_code)
        self.assertEqual(["test1", "test2"], seen_commands)
        self.assertIn("1 succeeded, 1 failed", response.text)
        self.assertIn("'test1': Operation timed out", response.text)

    def test_queue_propagates_not_found_status_code(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_failure("missing", 404)

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post("/server/command/queue/test1", expect_errors=True)

        self.assertEqual(404, response.status_code)
        self.assertEqual("missing", response.text)

    def test_queue_times_out_when_callback_never_completes(self):
        self.controller.queue_command = MagicMock()

        with patch.object(ControllerHandler, "_ACTION_TIMEOUT", 0.01):
            response = self.test_app.post("/server/command/queue/test1", expect_errors=True)

        self.assertEqual(504, response.status_code)
        self.assertEqual("Operation timed out", response.text)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("test1", command.filename)

    def test_validate_times_out_when_callback_never_completes(self):
        self.controller.queue_command = MagicMock()

        with patch.object(ControllerHandler, "_ACTION_TIMEOUT", 0.01):
            response = self.test_app.post("/server/command/validate/test1", expect_errors=True)

        self.assertEqual(504, response.status_code)
        self.assertEqual("Operation timed out", response.text)

    def test_stop_propagates_conflict_status_code(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_failure("wrong state", 409)

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post("/server/command/stop/test1", expect_errors=True)

        self.assertEqual(409, response.status_code)
        self.assertEqual("wrong state", response.text)

    def test_extract_propagates_internal_error_status_code(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_failure("backend failure", 500)

        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post("/server/command/extract/test1", expect_errors=True)

        self.assertEqual(500, response.status_code)
        self.assertEqual("backend failure", response.text)

    def test_bulk_rejects_unknown_action(self):
        response = self.test_app.post_json(
            "/server/command/bulk/not_real",
            {"filenames": ["test1"]},
            expect_errors=True
        )

        self.assertEqual(404, response.status_code)
        self.assertIn("Unsupported bulk action", response.text)

    def test_bulk_requires_non_empty_filename_list(self):
        response = self.test_app.post_json(
            "/server/command/bulk/queue",
            {"filenames": []},
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("non-empty 'files' or 'filenames' list", response.text)
