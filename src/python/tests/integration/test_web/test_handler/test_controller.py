# Copyright 2017, Inderpreet Singh, All rights reserved.

from unittest.mock import MagicMock
from urllib.parse import quote

from tests.integration.test_web.test_web_app import BaseTestWebApp
from controller import Controller


class TestControllerHandler(BaseTestWebApp):
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
        self.assertIn("non-empty 'filenames' list", response.text)
