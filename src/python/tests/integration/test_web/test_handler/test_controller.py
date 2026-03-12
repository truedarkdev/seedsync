# Copyright 2017, Inderpreet Singh, All rights reserved.

from unittest.mock import MagicMock, patch
from urllib.parse import quote

from tests.integration.test_web.test_web_app import BaseTestWebApp
from controller import Controller
from web.handler.controller import ControllerHandler


class TestControllerHandler(BaseTestWebApp):
    def setUp(self):
        super().setUp()
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

        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        print(self.test_app.post("/server/command/extract/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("/value/with/slashes", command.filename)

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

        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_local/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_LOCAL, command.action)
        self.assertEqual("/value/with/slashes", command.filename)

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

    def test_delete_remote(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()
        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect

        print(self.test_app.delete("/server/command/delete_remote/test1"))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("test1", command.filename)

        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        print(self.test_app.delete("/server/command/delete_remote/"+uri))
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("/value/with/slashes", command.filename)

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
